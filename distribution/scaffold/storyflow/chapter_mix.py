"""Deterministic chapter media derivation, without artifact or approval mutation.

Inputs are the uncaptioned source render and separately frozen dry narration.
Callers must retain their ASR/FA evidence and register the derivation normally.
"""
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import json
import hashlib
import re
import math
import subprocess
import wave
from .core import WorkflowError, atomic_json, digest
from .chapters import timeline_plan, map_intervals
from .vendor.smart_caption.convert import read_srt, write_srt
from .vendor.smart_caption.segment import parse_time, format_time


def ticks(value, rate):
    n = Decimal(str(value))
    if not n.is_finite() or n < 0:
        raise WorkflowError('invalid nonnegative clock value')
    return int((n * rate).to_integral_value(rounding=ROUND_HALF_UP))


def plan(payload, fps=30, sample_rate=48000):
    """Audio stays at semantic sample boundary; picture uses nearest frame.

    No source PCM sample is removed, repeated or time stretched. The picture
    rounding error is reported independently and never accumulates per segment.
    """
    if fps != 30 or sample_rate != 48000:
        raise WorkflowError('delivery contract requires CFR30 and PCM48000')
    # The shared timeline now also accepts BGM through this explicit fifth stream.
    original = dict(payload)
    streams = dict(payload.get('streams', {}))
    bgm = streams.pop('bgm', [])
    original['streams'] = streams
    result = timeline_plan(original)
    result['streams']['bgm'] = map_intervals(bgm, payload['insertions'])
    if any(float(x['end']) > result['source_duration'] for x in bgm):
        raise WorkflowError('bgm outside source duration')
    from .scene_assembly import playlist
    assembly=playlist(payload.get('scene_clips'),payload['insertions'],payload['source_duration'])
    if payload.get('assembly') is not None and payload['assembly']!=assembly:raise WorkflowError('provided typed scene assembly mismatch')
    result['assembly']=assembly
    previous_frame = -1
    for item in result['transitions']:
        frame = ticks(item['source_time'], fps)
        frame_count = ticks(item['duration'], fps)
        sample_count = ticks(item['duration'], sample_rate)
        # Durations must be exactly representable on both delivery clocks.
        if abs(frame_count / fps - float(item['duration'])) > 1e-9:
            raise WorkflowError('transition duration must be frame aligned')
        if frame <= previous_frame:
            raise WorkflowError('quantized chapter boundaries collide')
        previous_frame = frame
        item.update(source_frame=frame, source_sample=ticks(item['source_time'], sample_rate),
                    picture_rounding_seconds=frame/fps-float(item['source_time']),
                    frame_count=frame_count, sample_count=sample_count)
    result.update(schema='qy.chapter.mix.v1', fps=fps, sample_rate=sample_rate,
                  source_frames=ticks(result['source_duration'], fps),
                  source_samples=ticks(result['source_duration'], sample_rate),
                  policy='nearest_absolute_picture_frame; exact_semantic_PCM_sample; no_source_samples_lost')
    return result


def mapped_srt(source, destination, insertions, overrides=(), audio_sha256=None):
    cues = read_srt(str(source))
    values = [{'start': parse_time(c['start']), 'end': parse_time(c['end']),
               'text': c['text'], 'source_index': c['index']} for c in cues]
    apply_subtitle_overrides(values,source,insertions,overrides,audio_sha256)
    mapped = map_intervals(values, insertions)
    # Splitting a spoken cue duplicates its text and is not a valid semantic
    # caption. Require the editor to place the boundary at a cue gap instead.
    ids = [c['source_index'] for c in mapped]
    if len(ids) != len(set(ids)):
        raise WorkflowError('chapter boundary crosses a subtitle cue; choose a semantic caption gap')
    write_srt([{**c, 'start': format_time(c['start']), 'end': format_time(c['end'])}
               for c in mapped], str(destination))
    return mapped


def _run(argv, log):
    result = subprocess.run([str(x) for x in argv], capture_output=True)
    with Path(log).open('ab') as f:
        f.write(json.dumps([str(x) for x in argv]).encode()+b'\n'+result.stderr+b'\n')
    if result.returncode:
        raise WorkflowError('chapter media command failed; see '+str(log))
    return result.stdout


def _pcm(source, destination, log):
    _run(['ffmpeg','-nostdin','-v','error','-i',source,'-vn','-ar','48000','-ac','2',
          '-c:a','pcm_s32le',destination], log)


def splice_pcm(source, destination, transitions, clip_wavs):
    """Splice decoded S1 at unity gain; a separate narration-only projection too."""
    if len(transitions)!=len(clip_wavs):raise WorkflowError('chapter clip count mismatch')
    with wave.open(str(source),'rb') as src:
        if (src.getnchannels(), src.getsampwidth(), src.getframerate()) != (2,4,48000):
            raise WorkflowError('splice source must be stereo 32bit 48k PCM')
        with wave.open(str(destination),'wb') as dst:
            dst.setparams(src.getparams())
            previous = 0
            for item, clip in zip(transitions, clip_wavs):
                sample = item['source_sample']
                if sample < previous or sample > src.getnframes():
                    raise WorkflowError('PCM boundary outside source')
                remaining = sample-previous
                while remaining:
                    count = min(remaining, 48000)
                    dst.writeframesraw(src.readframes(count)); remaining -= count
                if clip is None:
                    dst.writeframesraw(b'\0' * (item['sample_count']*8))
                else:
                    with wave.open(str(clip),'rb') as card:
                        if card.getparams()[:3] != src.getparams()[:3]:
                            raise WorkflowError('chapter PCM format mismatch')
                        if card.getnframes() != item['sample_count']:
                            raise WorkflowError('chapter sound duration differs from insertion')
                        dst.writeframesraw(card.readframes(card.getnframes()))
                previous = sample
            while data := src.readframes(48000):
                dst.writeframesraw(data)


def canonical_card(source,destination,frame_count,log):
    """Lossless canonical carrier, compatible with still-scene SPS context.

    Approved source card and sound remain immutable. Encoding a short title card
    at CRF18 can change decoded pixels only when played after a CRF0 scene in a
    concatenated stream, even though isolated seek and all timestamps look valid.
    CRF0 preserves the approved decoded picture and its whole-clip frame proof.
    """
    canonical_filter='scale=iw:ih:out_range=tv:out_color_matrix=bt709,setsar=1,format=yuv420p,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709'
    flags=['-color_range','tv','-colorspace','bt709','-color_trc','bt709','-color_primaries','bt709']
    _run(['ffmpeg','-nostdin','-v','error','-i',source,'-vf','fps=30,setpts=PTS-STARTPTS,'+canonical_filter,
          '-frames:v',str(frame_count),'-an','-c:v','libx264','-preset','fast','-crf','0','-bf','0','-threads','4','-pix_fmt','yuv420p','-video_track_timescale','15360',*flags,destination],log)


def derive(source_video, dry_audio, source_srt, payload, output, cues=()):
    """Render real clean video + mapped subtitles + mix, with immutable evidence.

    transition.clip must be a genuine independently rendered HyperFrames MP4.
    Cues specify path/sha256, source_start/end, asset_in, gain_db, duck_db and
    fade_in/out seconds. duck_db attenuates only the background. Conservative
    constant attenuation under narration is intentional; dry speech never ducks.
    """
    output = Path(output)
    if output.exists():
        raise WorkflowError('chapter output exists; preserve and use new revision',409)
    mapping = plan(payload)
    paths = [Path(source_video), Path(dry_audio), Path(source_srt)]
    paths += [Path(t['clip']) for t in mapping['transitions']]
    paths += [Path(payload['scene_clips']['path'])]
    paths += [Path(n['clip_path']) for n in mapping['assembly']['nodes'] if n['type']=='scene']
    paths += [Path(c['path']) for c in cues]
    paths += [Path(c['waveform']['path']) for c in payload.get('subtitle_boundary_overrides',[])]
    before = {str(p.resolve()): digest(p) for p in paths}
    for c in cues:
        if digest(c['path']) != c['sha256']:
            raise WorkflowError('cue asset SHA mismatch')
    output.mkdir(parents=True)
    log = output/'commands.log'
    mapped_srt(source_srt,output/'delivery.srt',payload['insertions'],payload.get('subtitle_boundary_overrides',[]),digest(dry_audio))
    _pcm(dry_audio,output/'dry.pcm.wav',log)
    with wave.open(str(output/'dry.pcm.wav'),'rb') as dry:
        if dry.getnframes()!=mapping['source_samples']:raise WorkflowError('dry PCM duration differs from timeline')
    clips=[]
    for i,t in enumerate(mapping['transitions']):
        wav=output/f'card-{i}.wav'; _pcm(t['clip'],wav,log); clips.append(wav)
    splice_pcm(output/'dry.pcm.wav',output/'narration-projection.wav',mapping['transitions'],[None]*len(clips))
    splice_pcm(output/'dry.pcm.wav',output/'narration-and-cards.wav',mapping['transitions'],clips)
    # Cue fades apply on the original asset interval before chapter splitting.
    mix_inputs=[output/'narration-and-cards.wav']; filters=[]; labels=['[0:a]']
    for i,c in enumerate(cues):
        start,end=float(c['source_start']),float(c['source_end'])
        gain,duck=float(c['gain_db']),float(c.get('duck_db',-6))
        if not 0<=start<end<=mapping['source_duration'] or gain>0 or duck>0 or not all(math.isfinite(x) for x in (gain,duck)):
            raise WorkflowError('invalid cue clock or background gain')
        asset_in=float(c.get('asset_in',0));duration=end-start
        fi,fo=float(c.get('fade_in',.02)),float(c.get('fade_out',.2))
        if min(asset_in,fi,fo)<0 or fi+fo>duration: raise WorkflowError('invalid cue fades')
        prepared=output/f'cue-{i}.wav'
        _run(['ffmpeg','-nostdin','-v','error','-ss',str(asset_in),'-i',c['path'],'-t',str(duration),
              '-af',f'volume={gain+duck}dB,afade=t=in:d={fi},afade=t=out:st={duration-fo}:d={fo}',
              '-ar','48000','-ac','2','-c:a','pcm_s32le',prepared],log)
        with wave.open(str(prepared),'rb') as cw:
            if cw.getnframes()!=ticks(duration,48000):raise WorkflowError('cue asset ends before selected interval')
        for part in map_intervals([{'start':start,'end':end}],payload['insertions']):
            mix_inputs.append(prepared); index=len(mix_inputs)-1;label=f'cue{index}'
            a=ticks(part['source_offset'],48000); b=a+ticks(part['end']-part['start'],48000)
            delay=ticks(part['start'],48000)
            filters.append(f'[{index}:a]atrim=start_sample={a}:end_sample={b},asetpts=PTS-STARTPTS,adelay={delay}S:all=1[{label}]')
            labels.append(f'[{label}]')
    filters.append(''.join(labels)+f'amix=inputs={len(labels)}:duration=first:normalize=0[mix]')
    argv=['ffmpeg','-nostdin','-v','error']
    for p in mix_inputs: argv+=['-i',p]
    _run(argv+['-filter_complex',';'.join(filters),'-map','[mix]','-c:a','pcm_f32le',output/'mix.wav'],log)
    # Prove the narration projection is exactly reversible, preserving every sample.
    pcm_audit=projection_audit(output/'dry.pcm.wav',output/'narration-projection.wav',mapping)
    stats=subprocess.run(['ffmpeg','-nostdin','-v','info','-i',str(output/'mix.wav'),'-af','astats=metadata=0:reset=0','-f','null','-'],capture_output=True,check=True)
    (output/'mix-astats.log').write_bytes(stats.stderr)
    peaks=[float(x) for x in re.findall(rb'Peak level dB: ([-+0-9.einf]+)',stats.stderr)]
    if not peaks or any(x>=0 or math.isnan(x) for x in peaks):raise WorkflowError('mix clipping/invalid peak; reduce only background gain')
    # A scene is an immutable whole clip. No trim of the concatenated source
    # exists here: transitions are independent typed nodes between scene IDs.
    segments=[]; segment_frames=[]
    for i,node in enumerate(mapping['assembly']['nodes']):
        if node['type']=='scene':
            segment=Path(node['clip_path'])
        else:
            segment=output/f'picture-card-{i}.mp4'
            canonical_card(node['clip_path'],segment,node['frame_count'],log)
        segments.append(segment);segment_frames.append(node['frame_count'])
    segment_contract=validate_picture_segments(segments,segment_frames)
    atomic_json(output/'picture-stream-contract.json',segment_contract)
    listing=output/'pictures.txt';listing.write_text(''.join(f"file '{p.resolve()}'\n" for p in segments))
    _run(['ffmpeg','-nostdin','-v','error','-f','concat','-safe','0','-i',listing,'-i',output/'mix.wav',
          '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-use_editlist','1','-movflags','+faststart',output/'clean.mp4'],log)
    clean_stream=picture_stream(output/'clean.mp4')
    validate_picture_segments([output/'clean.mp4'],[sum(segment_frames)])
    atomic_json(output/'clean-stream-contract.json',clean_stream)
    from .scene_assembly import prove_frames
    frameproof=prove_frames(segments,output/'clean.mp4',mapping['assembly'])
    atomic_json(output/'whole-scene-frameproof.json',frameproof)
    atomic_json(output/'typed-assembly.json',mapping['assembly'])
    after={str(p.resolve()):digest(p) for p in paths}
    if before!=after:raise WorkflowError('chapter inputs drifted during derivation')
    result={'schema':'qy.chapter.derivation.v1','inputs':before,'mapping':mapping,'cues':list(cues),
            'dry_audio':str(Path(dry_audio).resolve()),'asr_fa_source':'dry_audio_only',
            'pcm_audit':pcm_audit,'peak_dbfs':max(peaks),
            'subtitle_boundary_overrides':payload.get('subtitle_boundary_overrides',[]),
            'narration_gain_db':0,'transition_gain_db':0,'normalization':False,
            'outputs':{p.name:digest(p) for p in output.iterdir() if p.is_file()},
            'tool_sha256':digest(__file__),'registration':'pending normal production derivation registration',
            'listening':'pending_owner_review'}
    atomic_json(output/'derivation.json',result)
    return result


def delivery_inputs(store, ep):
    """Resolve delivery files without replacing dry ASR/FA artifacts."""
    arts=store.artifacts(ep)
    value={k:{'path':str(store.root/arts[k]['path']),'sha256':arts[k]['sha256']}
           for k in ('audio','subtitles')}
    binding=store.root/'episodes'/ep/'production/chapter-revision.binding.json'
    if binding.exists():
        revision=verify_revision(store,ep)
        value={k:revision['delivery'][k] for k in ('audio','subtitles')}
    return value


def verify_revision(store,ep,value=None):
    work=store.root/'episodes'/ep/'production'
    binding=work/'chapter-revision.binding.json'
    if binding.is_symlink():raise WorkflowError('chapter binding cannot be symlink')
    v=value if value is not None else json.loads(binding.read_text())
    from .chapter_clip_gate import require_episode_clips
    require_episode_clips(store.root,ep,v['plan']['insertions'])
    if v.get('episode_id')!=ep or v.get('schema')!='qy.chapter.revision.v1':
        raise WorkflowError('invalid chapter revision identity')
    for record in v['evidence'].values():
        p=Path(record['path'])
        if p.is_symlink() or digest(p)!=record['sha256']:raise WorkflowError('chapter evidence changed')
    source=json.loads(Path(v['evidence']['source_binding']['path']).read_text())
    current=json.loads((work/'render.binding.json').read_text())
    if v['plan'].get('scene_clips') is None or v['plan']['scene_clips']!=source.get('scene_clips'):raise WorkflowError('chapter revision requires current whole-scene source contract; archive legacy using previous code before migration')
    if source!=current:raise WorkflowError('chapter source binding changed')
    arts=store.artifacts(ep)
    for kind in ('script','structure','audio','subtitles'):
        if v['dry_artifacts'][kind]!=arts[kind]['sha256']:
            raise WorkflowError('chapter dry artifact changed: '+kind)
    derived=json.loads(Path(v['evidence']['derivation']['path']).read_text())
    if derived.get('subtitle_boundary_overrides',[])!=v['plan'].get('subtitle_boundary_overrides',[]):raise WorkflowError('chapter caption display override binding mismatch')
    if derived['mapping']!=plan(v['plan']) or derived['cues']!=v['plan'].get('cues',[]):
        raise WorkflowError('chapter derivation plan mismatch')
    for path,sha in derived['inputs'].items():
        if digest(path)!=sha:raise WorkflowError('chapter input changed')
    directory=Path(v['evidence']['derivation']['path']).parent
    assembly=json.loads((directory/'typed-assembly.json').read_text())
    from .scene_assembly import validate_playlist
    validate_playlist(assembly,v['plan']['scene_clips'],v['plan']['insertions'],v['plan']['source_duration'])
    proof=json.loads((directory/'whole-scene-frameproof.json').read_text())
    if proof.get('status')!='pass' or proof.get('frame_count')!=assembly['output_frames'] or proof.get('expected_frame_md5')!=proof.get('actual_frame_md5') or len(proof.get('actual_frame_md5',[]))!=assembly['output_frames'] or proof.get('output_sha256')!=digest(directory/'clean.mp4'):raise WorkflowError('whole-scene decoded frame proof invalid')
    for name,sha in derived['outputs'].items():
        p=directory/name
        if p.parent!=directory or digest(p)!=sha:raise WorkflowError('chapter derived output changed')
    for kind,name in (('audio','mix.wav'),('subtitles','delivery.srt'),('video','clean.mp4')):
        record=v['delivery'][kind]
        if Path(record['path'])!=directory/name or digest(record['path'])!=record['sha256'] or record['sha256']!=derived['outputs'][name]:
            raise WorkflowError('chapter delivery output mismatch')
    review=json.loads(Path(v['evidence']['clip_review']['path']).read_text())
    if review.get('status')!='pass' or not isinstance(review.get('reviewer'),str) or not review['reviewer'].strip():
        raise WorkflowError('independent chapter clip visual review required')
    rows=review.get('clips',[])
    for item in v['plan']['insertions']:
        matches=[r for r in rows if Path(r['path']).resolve()==Path(item['clip']).resolve() and r.get('sha256')==digest(item['clip'])]
        if len(matches)!=1 or matches[0].get('status')!='pass' or not matches[0].get('reason') or not matches[0].get('evidence'):
            raise WorkflowError('chapter clip needs current independent visual evidence')
    return v


def revise(store, ep, payload, output, execute=False):
    """Formal registered clean-source chapter revision; leaves dry artifacts intact."""
    from .core import read_json
    from .revision_gate import check_revision
    from .chapter_clip_gate import require_episode_clips
    require_episode_clips(store.root,ep,payload['insertions'])
    if execute:check_revision(store.root,ep)
    work=store.root/'episodes'/ep/'production'
    binding=work/'chapter-revision.binding.json'
    source_binding=work/'render.binding.json'
    arts=store.artifacts(ep)
    existing=None
    if binding.exists():
        existing=verify_revision(store,ep)
        if existing['plan']==payload and Path(existing['delivery']['video']['path']).parent==Path(output).resolve():
            return {'ok':True,'idempotent':True,'binding':str(binding),'delivery':existing['delivery']}
        if Path(existing['delivery']['video']['path']).parent==Path(output).resolve():
            raise WorkflowError('new chapter revision requires a fresh output directory',409)
    if store.verify_hashes(ep) or store.render_binding_errors(ep):
        raise WorkflowError('current original render and dry artifacts required')
    source=read_json(source_binding)
    if source.get('burn_subtitles') is not False:
        raise WorkflowError('chapter requires original uncaptioned source render')
    out=Path(output).resolve()
    if not out.is_relative_to(work.resolve()):raise WorkflowError('chapter output must be inside episode production')
    expected=store._probe(store.root/arts['audio']['path'],'audio')['duration']
    if abs(expected-float(payload['source_duration']))>1/48000:
        raise WorkflowError('source duration differs from actual dry narration')
    if payload.get('scene_clips')!=source.get('scene_clips'):raise WorkflowError('chapter plan must consume current render whole-scene clips')
    mapping=plan(payload)
    result={'ok':True,'execute':execute,'operation':'chapter-revision','mapping':mapping,'output':str(out)}
    if not execute:return result
    check_revision(store.root,ep)
    # Review input must already exist before starting expensive local rendering.
    review_path=Path(payload['clip_review_path']).resolve()
    review=read_json(review_path)
    if review.get('status')!='pass' or not isinstance(review.get('reviewer'),str) or not review['reviewer'].strip():raise WorkflowError('clip review required')
    for item in payload['insertions']:
        rows=[r for r in review.get('clips',[]) if Path(r['path']).resolve()==Path(item['clip']).resolve() and r.get('sha256')==digest(item['clip'])]
        if len(rows)!=1 or rows[0].get('status')!='pass' or not rows[0].get('reason') or not rows[0].get('evidence'):raise WorkflowError('current independent clip review required')
    derived=derive(store.root/source['artifact']['path'],store.root/arts['audio']['path'],
                   store.root/arts['subtitles']['path'],payload,out,payload.get('cues',[]))
    atomic_json(out/'source-render.binding.json',source)
    v={'schema':'qy.chapter.revision.v1','episode_id':ep,'plan':payload,
       'dry_artifacts':{k:arts[k]['sha256'] for k in ('script','structure','audio','subtitles')},
       'evidence':{k:{'path':str(p),'sha256':digest(p)} for k,p in
                   {'source_binding':out/'source-render.binding.json','derivation':out/'derivation.json','clip_review':review_path}.items()},
       'delivery':{k:{'path':str(out/name),'sha256':digest(out/name)} for k,name in
                   (('audio','mix.wav'),('subtitles','delivery.srt'),('video','clean.mp4'))}}
    # Write and verify the formal derivation before normal registration.
    verify_revision(store,ep,value=v)
    if existing:archive_snapshot(store,ep)
    atomic_json(binding,v)
    try:artifact=store.register(ep,'video',out/'clean.mp4')
    except BaseException:
        if existing:atomic_json(binding,existing)
        raise
    return {**result,'binding':str(binding),'artifact':artifact,'delivery':v['delivery'],
            'next':'production burn -> clock-qa against delivery audio/SRT -> independent media review -> qa/validate/share'}


def projection_audit(source,projection,mapping):
    """Hash all PCM source samples against the inverse mapped narration stream."""
    with wave.open(str(source),'rb') as src, wave.open(str(projection),'rb') as dst:
        a=hashlib.sha256();b=hashlib.sha256();previous=0
        for t in mapping['transitions']+[{'source_sample':src.getnframes(),'sample_count':0}]:
            remaining=t['source_sample']-previous
            while remaining:
                count=min(remaining,48000);a.update(src.readframes(count));b.update(dst.readframes(count));remaining-=count
            silence=dst.readframes(t['sample_count'])
            if len(silence)!=t['sample_count']*8 or any(silence):raise WorkflowError('narration projection chapter gap is not exact silence')
            previous=t['source_sample']
        if a.digest()!=b.digest() or dst.readframes(1):raise WorkflowError('inverse narration projection does not reproduce source PCM')
        return {'status':'pass','all_source_pcm_sha256':a.hexdigest(),'inverse_projection_pcm_sha256':b.hexdigest(),
                'source_samples':src.getnframes(),'delivery_samples':dst.getnframes(),'sample_rate':48000,
                'source_samples_dropped':0,'source_samples_repeated':0,'method':'complete inverse PCM mapping byte comparison'}


def archive_snapshot(store,ep):
    """Preserve current source, mix and burn indexes in content addressed history."""
    work=store.root/'episodes'/ep/'production'
    binding=work/'chapter-revision.binding.json'
    verify_revision(store,ep)
    target=work/'chapter-revision-history'/digest(binding)
    files=[p for p in (binding,work/'render.binding.json',work/'burned.binding.json') if p.exists()]
    if target.exists():
        if any(not (target/p.name).exists() or digest(target/p.name)!=digest(p) for p in files):
            raise WorkflowError('chapter history conflict; preserve and inspect')
    else:
        target.mkdir(parents=True)
        for p in files:atomic_json(target/p.name,json.loads(p.read_text()))
    return target


def archive_current(store,ep,execute=False):
    """Explicitly return to the verified original source before new dry/FA work.

    The old mixed final and every source asset remain immutable. This is the
    supported transition before registering new dry narration, never hand edits.
    """
    work=store.root/'episodes'/ep/'production'
    binding=work/'chapter-revision.binding.json'
    revision=verify_revision(store,ep)
    source=json.loads((work/'render.binding.json').read_text())
    if store.verify_hashes(ep) or store.render_binding_errors(ep):raise WorkflowError('valid current chapter revision required before archiving')
    result={'ok':True,'executed':execute,'operation':'chapter-revision-archive','source':source['artifact']}
    if not execute:return result
    from .revision_gate import check_revision
    check_revision(store.root,ep)
    history=archive_snapshot(store,ep)
    # Normal registration invalidates final review/QA; restore index on failure.
    artifact=store.register(ep,'video',store.root/source['artifact']['path'])
    binding.unlink()
    return {**result,'history':str(history),'artifact':artifact,'next':'register/review new dry script/audio/FA, render original clean source, then chapter-revision into a fresh output directory'}


def apply_subtitle_overrides(values, source_srt, insertions, overrides, audio_sha256):
    """Move only a shared adjacent-cue display boundary into reviewed silence.

    Frozen FA and original SRT are never modified. Exact words, source hashes,
    measured quiet window and independent semantic approval bind every change.
    """
    used=set()
    for row in overrides:
        required={'source_srt_sha256','source_audio_sha256','previous_index','next_index','previous_text','next_text','old_boundary','new_boundary','waveform','review'}
        if set(row)!=required:raise WorkflowError('subtitle override fields must exactly match schema')
        if row['source_srt_sha256']!=digest(source_srt) or row['source_audio_sha256']!=audio_sha256:
            raise WorkflowError('subtitle override source hashes changed')
        old,new=float(row['old_boundary']),float(row['new_boundary'])
        if not all(math.isfinite(x) for x in (old,new)) or not 0<abs(old-new)<=.2+1e-9:
            raise WorkflowError('subtitle display boundary shift must be <=0.2 seconds')
        if not any(abs(float(x['source_time'])-new)<1e-9 for x in insertions):
            raise WorkflowError('subtitle override must equal chapter insertion clock')
        previous=[i for i,c in enumerate(values) if c['source_index']==row['previous_index']]
        following=[i for i,c in enumerate(values) if c['source_index']==row['next_index']]
        if len(previous)!=1 or len(following)!=1 or following[0]!=previous[0]+1 or previous[0] in used or following[0] in used:
            raise WorkflowError('subtitle override requires unique adjacent cues')
        a,b=values[previous[0]],values[following[0]]
        if a['text']!=row['previous_text'] or b['text']!=row['next_text'] or abs(a['end']-old)>1e-9 or abs(b['start']-old)>1e-9 or not a['start']<new<b['end']:
            raise WorkflowError('subtitle override text/shared boundary mismatch')
        proof=row['waveform'];path=Path(proof['path'])
        if digest(path)!=proof['sha256']:raise WorkflowError('subtitle quiet-window evidence changed')
        measurements=json.loads(path.read_text())
        windows=[w for boundary in measurements if abs(float(boundary['source_time'])-old)<1e-9 for w in boundary['windows']
                 if abs(old+float(w['relative_start_ms'])/1000-new)<1e-9]
        if len(windows)!=1 or not math.isfinite(float(windows[0]['rms_dbfs'])) or float(windows[0]['rms_dbfs'])> -45:
            raise WorkflowError('subtitle boundary needs measured quiet window <=-45dBFS at selected sample')
        review=row['review']
        if review.get('status')!='pass' or not isinstance(review.get('reviewer'),str) or not review['reviewer'].strip() or not review.get('reason') or not review.get('evidence'):
            raise WorkflowError('independent semantic silence-boundary review required')
        a['end']=new;b['start']=new
        used.update((previous[0],following[0]))


def picture_stream(path):
    fields='codec_name,width,height,pix_fmt,sample_aspect_ratio,r_frame_rate,avg_frame_rate,time_base,color_range,color_space,color_transfer,color_primaries,field_order,has_b_frames,nb_frames,duration'
    try:value=subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream='+fields,'-of','json',str(path)],stderr=subprocess.PIPE)
    except (OSError,subprocess.CalledProcessError) as exc:raise WorkflowError('picture stream probe failed: '+str(path)) from exc
    streams=json.loads(value)['streams']
    if len(streams)!=1:raise WorkflowError('exactly one video stream required')
    return streams[0]


def validate_picture_segments(paths,counts):
    """Reject metadata changes that would reset downstream ffmpeg filter clocks."""
    if not paths or len(paths)!=len(counts):raise WorkflowError('picture segment/count contract missing')
    expected={'codec_name':'h264','pix_fmt':'yuv420p','sample_aspect_ratio':'1:1',
              'r_frame_rate':'30/1','avg_frame_rate':'30/1','time_base':'1/15360',
              'color_range':'tv','color_space':'bt709','color_transfer':'bt709','color_primaries':'bt709',
              'field_order':'progressive','has_b_frames':0}
    first=picture_stream(paths[0]);expected.update(width=first['width'],height=first['height'])
    rows=[]
    for path,count in zip(paths,counts):
        stream=picture_stream(path)
        if any(stream.get(k)!=v for k,v in expected.items()):
            raise WorkflowError('picture segment parameters differ from canonical contract: '+str(path))
        if int(stream.get('nb_frames',-1))!=count or abs(float(stream.get('duration',0))-count/30)>1e-6:
            raise WorkflowError('picture segment independently measured video frame/duration mismatch')
        rows.append({'path':str(path),'sha256':digest(path),'stream':stream,'expected_frames':count})
    return {'status':'pass','canonical':expected,'segments':rows,'total_frames':sum(counts)}
