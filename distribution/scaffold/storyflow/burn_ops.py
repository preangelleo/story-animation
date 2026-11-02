"""Project-owned source render -> immutable subtitle derivation -> artifact registration."""
import json
from pathlib import Path
import subprocess
import sys
from .core import WorkflowError, atomic_json, digest, read_json


def proof_file(path):
    path=Path(path).resolve(strict=True)
    return {'path':str(path),'sha256':digest(path)}


def verify_derivation(store, ep, value, source_binding):
    """Revalidate actual files and the original source proof on every consumer call."""
    arts=store.artifacts(ep)
    from .chapter_mix import delivery_inputs
    delivery=delivery_inputs(store,ep)
    if value['episode_id']!=ep or value['source_binding_sha256']!=digest(source_binding):
        raise WorkflowError('burn source binding changed')
    source=read_json(source_binding)
    archive=Path(value['clock_output']).parent/'derivation.json'
    if read_json(archive)!=value:raise WorkflowError('burn derivation archive changed')
    chapter=source_binding.parent/'chapter-revision.binding.json'
    if chapter.exists():
        from .chapter_mix import verify_revision
        revision=verify_revision(store,ep)
        expected_source={'path':str(Path(revision['delivery']['video']['path']).relative_to(store.root)),'sha256':revision['delivery']['video']['sha256']}
    else: expected_source=source['artifact']
    if value['source']!=expected_source or source.get('burn_subtitles') is not False:
        raise WorkflowError('burn source render mismatch')
    if digest(store.root/value['source']['path'])!=value['source']['sha256']:
        raise WorkflowError('burn source file changed')
    for kind in ('audio','subtitles'):
        if value['input_artifacts'][kind]!=arts[kind]['sha256'] or digest(store.root/arts[kind]['path'])!=arts[kind]['sha256']:
            raise WorkflowError('burn input changed: '+kind)
    for record in value['evidence'].values():
        p=Path(record['path'])
        if p.is_symlink() or digest(p)!=record['sha256']:raise WorkflowError('burn evidence changed')
    if 'video_stream_gate' in value['evidence']:
        gate=read_json(value['evidence']['video_stream_gate']['path'])
        if gate.get('status')!='pass' or gate.get('source_sha256')!=value['source']['sha256'] or gate.get('output_sha256')!=value['output_sha256']:raise WorkflowError('burn video stream gate mismatch')
    result=read_json(value['evidence']['result']['path'])
    if 'first_frame' in value.get('inputs',{}):
        from .first_frame import consume as consume_thumbnail
        thumb=consume_thumbnail(store,ep)
        if value['inputs']['first_frame']!=thumb:raise WorkflowError('burn thumbnail dependency stale')
        if thumb:
            proof=read_json(value['evidence']['first_frame_final']['path'])
            if proof.get('video',{}).get('sha256')!=value['output_sha256'] or proof.get('thumbnail')!=thumb or proof.get('status')!='pass' or not result.get('first_frame_cover'):raise WorkflowError('burn first-frame final proof mismatch')
    command=read_json(value['evidence']['command']['path'])
    if value.get('delivery_inputs',delivery)!=delivery:raise WorkflowError('burn delivery binding changed')
    before={'video_sha256':value['source']['sha256'],'audio_sha256':delivery['audio']['sha256'],'srt_sha256':delivery['subtitles']['sha256']}
    if value['input_hashes_before']!=before or value['input_hashes_after']!=before:
        raise WorkflowError('burn inputs drifted')
    if any(result[k]!=before[k] for k in ('video_sha256','srt_sha256')) or result['force_style']!=command['force_style'] or result['command']!=command['command']:
        raise WorkflowError('burn result/command mismatch')
    if digest(result['video'])!=before['video_sha256'] or digest(result['srt'])!=before['srt_sha256'] or digest(result['font'])!=result['font_sha256']:
        raise WorkflowError('burn result inputs/font changed')
    if digest(result['output'])!=result['output_sha256'] or value['output_sha256']!=result['output_sha256']:
        raise WorkflowError('burn output changed')
    if arts['video']['sha256']!=value['output_sha256'] or digest(store.root/arts['video']['path'])!=value['output_sha256']:
        raise WorkflowError('burn current video mismatch')
    if not result['font_select'] or result.get('srt_offset_seconds')!=0 or any(result['caption_quality'].get(k)!=0 for k in ('punctuation','orphan_cues','short_cues')):
        raise WorkflowError('burn style/caption proof missing')
    return source


def burn(store, ep, execute=False, illustrated=False):
    """Only actual tool results are registered. Orphan/failed evidence is never overwritten."""
    from .integrations import fingerprint
    root=store.root;work=root/'episodes'/ep/'production';binding=work/'render.binding.json';derived=work/'burned.binding.json'
    try:
        source=read_json(binding);arts=store.artifacts(ep)
        if store.verify_hashes(ep) or store.render_binding_errors(ep):raise WorkflowError('valid current source render/derivation required')
        from .chapter_mix import delivery_inputs,verify_revision
        delivery=delivery_inputs(store,ep)
        chapter=work/'chapter-revision.binding.json'
        source_artifact=source['artifact']
        if chapter.exists():
            revision=verify_revision(store,ep)
            source_artifact={'path':str(Path(revision['delivery']['video']['path']).relative_to(root)),'sha256':revision['delivery']['video']['sha256']}
        source_path=root/source_artifact['path']
        from .first_frame import consume as consume_thumbnail
        thumbnail=consume_thumbnail(store,ep)
        if thumbnail and (source.get('inputs',{}).get('first_frame')!=thumbnail or not source.get('first_frame_receipt')):raise WorkflowError('new final requires current thumbnail source consumption receipt')
        if source.get('burn_subtitles') is not False or digest(source_path)!=source_artifact['sha256']:raise WorkflowError('bound unburned source required')
        inputs={'source_binding_sha256':digest(binding),'audio_sha256':delivery['audio']['sha256'],'srt_sha256':delivery['subtitles']['sha256'],'chapter_binding_sha256':digest(chapter) if chapter.exists() else None,'tool_sha256':digest(root/'tools/burn_subtitles.py'),'burn_ops_sha256':digest(__file__),'video_stream_gate_version':1,'illustrated_reconstruction':illustrated,'first_frame':thumbnail}
        target=work/'burns'/fingerprint(inputs);out=target/'final.mp4';evidence=target/'evidence'
        plan={'ok':True,'executed':False,'operation':'burn','inputs':inputs,'output':str(out),'clock_output':str(target/'clock')}
        if derived.exists() and read_json(derived).get('inputs')==inputs:
            verify_derivation(store,ep,read_json(derived),binding)
            return {**plan,'idempotent':True}
        if arts['video']['sha256']!=source_artifact['sha256']:raise WorkflowError('register source render before a new burn')
        if not execute:return plan
        if target.exists():raise WorkflowError('burn evidence exists without registered derivation; preserve and inspect',409)
        target.mkdir(parents=True)
        atomic_json(target/'source-render.binding.json',source)
        (target/'burn_ops.py').write_bytes(Path(__file__).read_bytes())
        if digest(target/'burn_ops.py')!=inputs['burn_ops_sha256']:raise WorkflowError('burn implementation changed during invocation')
        before={'video_sha256':digest(source_path),'audio_sha256':digest(delivery['audio']['path']),'srt_sha256':digest(delivery['subtitles']['path'])}
        argv=[sys.executable,str(root/'tools/burn_subtitles.py'),'--video',str(source_path),'--srt',delivery['subtitles']['path'],'--output',str(out),'--work',str(evidence),'--execute']
        if thumbnail:argv+=['--first-frame-cover']
        if illustrated:argv+=['--illustrated-reconstruction']
        with (target/'invocation.log').open('x') as log:
            subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,check=True)
        result=read_json(evidence/'result.json')
        stream_gate=video_stream_gate(source_path,out)
        if thumbnail:
            from .first_frame import final_pixels
            atomic_json(target/'first-frame-final.json',final_pixels(out,thumbnail))
            if not result.get('first_frame_cover'):raise WorkflowError('burn did not suppress frame-zero captions')
        atomic_json(target/'video-stream-gate.json',stream_gate)
        after={'video_sha256':digest(source_path),'audio_sha256':digest(delivery['audio']['path']),'srt_sha256':digest(delivery['subtitles']['path'])}
        if before!=after or digest(binding)!=inputs['source_binding_sha256'] or store.render_binding_errors(ep):raise WorkflowError('source/input drift during burn')
        if result['output_sha256']!=digest(out) or result['video_sha256']!=before['video_sha256'] or result['srt_sha256']!=before['srt_sha256']:raise WorkflowError('burn result mismatch')
        value={'episode_id':ep,'inputs':inputs,'source':source_artifact,'delivery_inputs':delivery,'source_binding_sha256':digest(binding),'input_artifacts':{k:arts[k]['sha256'] for k in ('audio','subtitles')},'input_hashes_before':before,'input_hashes_after':after,'output_sha256':digest(out),'clock_output':str(target/'clock'),'evidence':{k:proof_file(p) for k,p in {'source':target/'source-render.binding.json','result':evidence/'result.json','command':evidence/'command.json','log':evidence/'ffmpeg.log','srt_snapshot':evidence/'subtitles.srt','tool':root/'tools/burn_subtitles.py','video_stream_gate':target/'video-stream-gate.json','burn_ops_snapshot':target/'burn_ops.py'}.items()}}
        if thumbnail:value['evidence']['first_frame_final']=proof_file(target/'first-frame-final.json')
        # Normal registration invalidates persisted QA/media reviews. No PASS is created.
        value['artifact']=store.register(ep,'video',out)
        atomic_json(target/'derivation.json',value)
        atomic_json(derived,value)
        verify_derivation(store,ep,value,binding)
        return {**plan,'executed':True,'artifact':value['artifact'],'derivation':str(target/'derivation.json'),'review':'pending independent final review and clock QA'}
    except (OSError,ValueError,KeyError,TypeError,subprocess.CalledProcessError) as exc:
        raise WorkflowError('burn evidence/source required: '+type(exc).__name__) from exc


def video_stream_gate(source,output):
    """A long audio stream must never hide a truncated video behind FORMAT duration."""
    from decimal import Decimal,ROUND_HALF_UP
    def probe(path):
        value=subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=duration,avg_frame_rate,nb_frames,nb_read_frames,time_base','-of','json',str(path)])
        rows=json.loads(value)['streams']
        if len(rows)!=1:raise WorkflowError('burn requires exactly one source and output video stream')
        row=rows[0]
        if int(row['nb_read_frames'])!=int(row['nb_frames']):raise WorkflowError('burn video container frame count differs from actual decode')
        return row
    src,dst=probe(source),probe(output)
    expected=int((Decimal(src['duration'])*30).to_integral_value(rounding=ROUND_HALF_UP))
    if dst.get('avg_frame_rate')!='30/1' or dst.get('time_base')!='1/15360' or int(dst['nb_read_frames'])!=expected or abs(float(dst['duration'])-expected/30)>1e-6 or abs(float(dst['duration'])-float(src['duration']))>1/30+1e-6:
        raise WorkflowError('burn independently measured video stream truncated/frame count changed; no artifact registration')
    return {'status':'pass','source_sha256':digest(source),'output_sha256':digest(output),'source_video_stream':src,'output_video_stream':dst,'expected_output_frames':expected,'comparison':'actual decoded video frames and video-stream duration; FORMAT/audio duration not used'}
