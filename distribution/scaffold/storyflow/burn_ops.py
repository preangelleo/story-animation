"""Project-owned source render -> immutable subtitle derivation -> artifact registration."""
import json
import os
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
    if 'frame_mask' in value.get('inputs',{}):
        from .caption_mask import validate_mask,caption_filter,notice_filter
        mask=validate_mask(value['inputs']['frame_mask'],store.root/value['source']['path'])
        if mask['episode_id']!=ep:raise WorkflowError('burn caption mask episode mismatch')
        mask_path=Path(value['evidence']['frame_mask']['path'])
        if read_json(mask_path)!=mask:raise WorkflowError('burn caption mask snapshot mismatch')
        expected_tools={'tool':store.root/'tools/burn_subtitles_masked.py','legacy_helper':store.root/'tools/burn_subtitles.py','caption_mask_helper':store.root/'storyflow/caption_mask.py'}
        for key,path in expected_tools.items():
            hash_key={'tool':'tool_sha256','legacy_helper':'legacy_helper_sha256','caption_mask_helper':'caption_mask_helper_sha256'}[key]
            if value['evidence'][key]!=proof_file(path) or value['inputs'][hash_key]!=digest(path):raise WorkflowError('burn caption mask tool binding changed')
        invocation=read_json(value['evidence']['invocation']['path'])['argv']
        expected_argv=[sys.executable,str(expected_tools['tool']),'--video',str(store.root/value['source']['path']),'--srt',delivery['subtitles']['path'],'--output',result['output'],'--work',str(Path(value['evidence']['result']['path']).parent),'--execute','--frame-mask',str(mask_path),'--font',value['inputs']['subtitle_font'],'--font-family',value['inputs']['subtitle_family'],'--font-index',value['inputs']['subtitle_font_index']]
        if value['inputs'].get('first_frame'):expected_argv+=['--first-frame-cover']
        if value['inputs']['illustrated_reconstruction']:expected_argv+=['--illustrated-reconstruction']
        if digest(value['inputs']['subtitle_font'])!=value['inputs']['subtitle_font_sha256'] or result['font']!=value['inputs']['subtitle_font'] or 'FontName='+value['inputs']['subtitle_family']+',' not in result['force_style']:raise WorkflowError('burn configured subtitle font binding mismatch')
        if invocation!=expected_argv:raise WorkflowError('burn caption mask invocation mismatch')
        for receipt in (result,command):
            if (receipt.get('frame_mask')!=mask or receipt.get('frame_mask_path')!=str(mask_path)
                    or receipt.get('frame_mask_sha256')!=digest(mask_path)
                    or receipt.get('legacy_helper_sha256')!=value['inputs']['legacy_helper_sha256']
                    or receipt.get('caption_mask_helper_sha256')!=value['inputs']['caption_mask_helper_sha256']):
                raise WorkflowError('burn caption mask command/result mismatch')
            expected_filter=caption_filter(Path(value['evidence']['srt_snapshot']['path']),Path(receipt['font']),receipt['force_style'],mask,receipt['first_frame_cover'])
            actual_filter=receipt['command'][receipt['command'].index('-vf')+1]
            if value['inputs']['illustrated_reconstruction']:expected_filter+=notice_filter(Path(receipt['font']),receipt['first_frame_cover'])
            if actual_filter!=expected_filter:
                raise WorkflowError('burn caption mask actual ffmpeg filter mismatch')
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
        tool=root/'tools/burn_subtitles.py';mask=None
        if chapter.exists():
            from .caption_mask import create_mask
            mask=create_mask(chapter,source_path)
            if mask['episode_id']!=ep:raise WorkflowError('burn caption mask episode mismatch')
            tool=root/'tools/burn_subtitles_masked.py'
            font=Path(os.environ.get('STORY_SUBTITLE_FONT','assets/fonts/subtitle.ttf')).expanduser().resolve(strict=True)
            inputs.update(subtitle_font=str(font),subtitle_font_sha256=digest(font),subtitle_family=os.environ.get('STORY_SUBTITLE_FAMILY',''),subtitle_font_index=os.environ.get('STORY_SUBTITLE_FONT_INDEX','0'))
            if not inputs['subtitle_family']:raise WorkflowError('STORY_SUBTITLE_FAMILY must name the user-supplied registered font')
            inputs.update(frame_mask=mask,tool_sha256=digest(tool),legacy_helper_sha256=digest(root/'tools/burn_subtitles.py'),caption_mask_helper_sha256=digest(root/'storyflow/caption_mask.py'))
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
        if mask:atomic_json(target/'frame-mask.json',mask)
        argv=[sys.executable,str(tool),'--video',str(source_path),'--srt',delivery['subtitles']['path'],'--output',str(out),'--work',str(evidence),'--execute']
        if mask:argv+=['--frame-mask',str(target/'frame-mask.json'),'--font',inputs['subtitle_font'],'--font-family',inputs['subtitle_family'],'--font-index',inputs['subtitle_font_index']]
        atomic_json(target/'invocation.json',{'argv':argv+(['--first-frame-cover'] if thumbnail else [])+(['--illustrated-reconstruction'] if illustrated else [])})
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
        if mask:
            from .caption_mask import validate_mask
            validate_mask(mask,source_path)
            for path,key in ((tool,'tool_sha256'),(root/'tools/burn_subtitles.py','legacy_helper_sha256'),(root/'storyflow/caption_mask.py','caption_mask_helper_sha256')):
                if digest(path)!=inputs[key]:raise WorkflowError('burn tool changed during invocation')
        if result['output_sha256']!=digest(out) or result['video_sha256']!=before['video_sha256'] or result['srt_sha256']!=before['srt_sha256']:raise WorkflowError('burn result mismatch')
        value={'episode_id':ep,'inputs':inputs,'source':source_artifact,'delivery_inputs':delivery,'source_binding_sha256':digest(binding),'input_artifacts':{k:arts[k]['sha256'] for k in ('audio','subtitles')},'input_hashes_before':before,'input_hashes_after':after,'output_sha256':digest(out),'clock_output':str(target/'clock'),'evidence':{k:proof_file(p) for k,p in {'source':target/'source-render.binding.json','result':evidence/'result.json','command':evidence/'command.json','log':evidence/'ffmpeg.log','srt_snapshot':evidence/'subtitles.srt','tool':tool,'video_stream_gate':target/'video-stream-gate.json','burn_ops_snapshot':target/'burn_ops.py'}.items()}}
        value['evidence']['invocation']=proof_file(target/'invocation.json')
        if mask:
            value['evidence'].update({key:proof_file(path) for key,path in {'frame_mask':target/'frame-mask.json','legacy_helper':root/'tools/burn_subtitles.py','caption_mask_helper':root/'storyflow/caption_mask.py'}.items()})
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
