"""Prepared thumbnail and one-frame replacement; no timeline or audio edits."""
import hashlib
import json
from pathlib import Path
import subprocess
from .core import Store, WorkflowError, atomic_json, digest, read_json

SCHEMA='qy.thumbnail.first-frame.v1'

def policy(store):
    value={'enabled':True,'frame_count':1,'fit':'strict',**store.config.get('render',{}).get('first_frame',{})}
    if type(value['enabled']) is not bool or type(value['frame_count']) is not int or value['frame_count']!=1 or value['fit']!='strict':
        raise WorkflowError('first_frame requires boolean enabled, frame_count=1, fit=strict')
    return value

def file_record(path):
    path=Path(path).resolve(strict=True)
    return {'path':str(path),'sha256':digest(path)}

def dimensions(path):
    v=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height,codec_name','-of','json',str(path)]))['streams'][0]
    return v

def review_context(store,ep):
    from .style_binding import snapshot_for_episode
    arts=store.artifacts(ep);p=store.root/'episodes'/ep/'production'/'visual-plan.json'
    return {'artifact_hashes':{k:arts[k]['sha256'] for k in ('script','structure') if k in arts},'style_snapshot':snapshot_for_episode(store,ep),'visual_plan':file_record(p) if p.exists() else None}

def prepare(store,ep,image,review,execute=False):
    image=Path(image).resolve(strict=True);review=Path(review).resolve(strict=True)
    if not image.is_relative_to(store.root) or not review.is_relative_to(store.root):raise WorkflowError('thumbnail and review must be project-local')
    if image.suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):raise WorkflowError('thumbnail must be PNG/JPEG/WebP')
    cfg=store.config.get('render',{});size=[cfg.get('width',1920),cfg.get('height',1080)]
    info=dimensions(image)
    if info['codec_name'] not in ('png','mjpeg','webp'):raise WorkflowError('thumbnail must decode as a static image codec')
    count=subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=nb_read_frames','-of','csv=p=0',str(image)],text=True).strip()
    if count!='1':raise WorkflowError('thumbnail must contain exactly one decoded frame')
    if [info['width'],info['height']]!=size:raise WorkflowError('thumbnail dimensions must exactly match final canvas; no crop/stretch')
    arts=store.artifacts(ep);r=read_json(review)
    dependencies={k:arts[k]['sha256'] for k in ('script','structure') if k in arts}
    from .style_binding import snapshot_for_episode
    style=snapshot_for_episode(store,ep)
    visual_plan=store.root/'episodes'/ep/'production'/'visual-plan.json'
    context={'artifact_hashes':dependencies,'style_snapshot':style,'visual_plan':file_record(visual_plan) if visual_plan.exists() else None}
    if r.get('context')!=context:raise WorkflowError('thumbnail review must freeze current script/structure/style/visual-plan context')
    references=r.get('references')
    if not isinstance(references,list):raise WorkflowError('thumbnail review requires explicit reference records (empty only when no references used)')
    for ref in references:
        p=Path(ref['path']).resolve(strict=True)
        if not p.is_relative_to(store.root) or digest(p)!=ref['sha256']:raise WorkflowError('thumbnail reference missing/changed/outside project')
    if r.get('reviewer')==store.episode(ep).get('author'):raise WorkflowError('thumbnail review must be independent of episode author')
    if r.get('status')!='pass' or not isinstance(r.get('reviewer'),str) or not r['reviewer'].strip() or r.get('episode_id')!=ep or r.get('image_sha256')!=digest(image) or not r.get('reason') or not r.get('references_reviewed') is True:
        raise WorkflowError('independent thumbnail/reference review with episode and exact SHA required')
    value={'schema':SCHEMA,'episode_id':ep,'policy':policy(store),'canvas':size,'image':file_record(image),'review':file_record(review),'context':context,'references':references,'tool_sha256':digest(__file__)}
    value['fingerprint']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    path=store.root/'episodes'/ep/'production'/'thumbnails'/value['fingerprint']/'prepared.json'
    if execute:
        if path.exists() and read_json(path)!=value:raise WorkflowError('prepared receipt conflict')
        atomic_json(path,value);atomic_json(path.parents[1].parent/'thumbnail.binding.json',{'path':str(path),'sha256':digest(path)})
    return {'prepared':value,'receipt_path':str(path),'executed':execute}

def consume(store,ep):
    if not policy(store)['enabled']:return None
    binding=store.root/'episodes'/ep/'production'/'thumbnail.binding.json'
    if not binding.is_file():raise WorkflowError('prepare and verify thumbnail before final synthesis')
    ref=read_json(binding);p=Path(ref['path'])
    if not p.resolve().is_relative_to(store.root) or digest(p)!=ref['sha256']:raise WorkflowError('thumbnail receipt changed')
    value=read_json(p)
    current=prepare(store,ep,value['image']['path'],value['review']['path'])['prepared']
    if value!=current:raise WorkflowError('thumbnail prepared inputs/policy/canvas/script stale')
    return {'path':str(p),'sha256':digest(p),'fingerprint':value['fingerprint'],'image':value['image'],'policy':value['policy'],'canvas':value['canvas']}

def frames(path):
    out=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:v:0','-f','framemd5','-'])
    return [line.rsplit(b',',1)[-1].strip().decode() for line in out.splitlines() if line and not line.startswith(b'#')]

def replace(source,output,contract):
    source,output=Path(source),Path(output)
    if output.exists():raise WorkflowError('first-frame output exists; preserve evidence')
    subprocess.run(['ffmpeg','-v','error','-nostdin','-i',str(source),'-i',contract['image']['path'],'-filter_complex',"[0:v][1:v]overlay=enable='eq(n,0)':format=yuv420,setsar=1[v]",'-map','[v]','-map','0:a?','-c:a','copy','-c:v','libx264','-crf','0','-preset','fast','-bf','0','-r','30','-video_track_timescale','15360','-pix_fmt','yuv420p',str(output)],check=True)
    before,after=frames(source),frames(output)
    if len(before)!=len(after) or before[1:]!=after[1:]:raise WorkflowError('first-frame replacement changed scene frames/count after frame zero')
    pixel_proof=final_pixels(output,contract)
    return {'schema':SCHEMA,'tool_sha256':digest(__file__),'frame_zero':pixel_proof,'thumbnail':contract,'source':file_record(source),'output':file_record(output),'frame_count':len(after),'unchanged_from_frame':1,'ordered_remaining_frames_sha256':hashlib.sha256(''.join(after[1:]).encode()).hexdigest(),'audio':'packet copy; no timestamp filter','caption_offset_seconds':0}

def verify_replacement(value,contract):
    if value.get('schema')!=SCHEMA or value.get('thumbnail')!=contract:raise WorkflowError('first-frame consumption stale/missing')
    for k in ('source','output'):
        if digest(value[k]['path'])!=value[k]['sha256']:raise WorkflowError('first-frame media changed')
    if value.get('tool_sha256')!=digest(__file__) or value.get('unchanged_from_frame')!=1 or not value.get('ordered_remaining_frames_sha256') or value.get('frame_zero',{}).get('status')!='pass':raise WorkflowError('first-frame sequence proof invalid/stale')

def final_pixels(video,contract):
    """Actual decoded frame-zero RGB mean error, allowing the final lossy encoder."""
    def rgb(path):return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-frames:v','1','-vf','format=rgb24','-f','rawvideo','-'])
    a,b=rgb(video),rgb(contract['image']['path'])
    if len(a)!=len(b) or not a:raise WorkflowError('final first-frame canvas mismatch')
    error=sum(abs(x-y) for x,y in zip(a,b))/len(a)
    if error>12:raise WorkflowError('final first-frame thumbnail pixel error exceeds 12/255')
    return {'status':'pass','video':file_record(video),'thumbnail':contract,'mean_absolute_rgb_error':error,'maximum_allowed':12,'caption_zero_frame_suppressed':True}

def register(subparsers):
    q=subparsers.add_parser('thumbnail');s=q.add_subparsers(dest='thumbnail_action',required=True)
    for action in ('context','prepare','verify','consume'):
        p=s.add_parser(action);p.add_argument('episode');p.set_defaults(handler=handle)
        if action=='prepare':p.add_argument('--image',required=True);p.add_argument('--review',required=True);p.add_argument('--execute',action='store_true')

def handle(args):
    store=Store(args.root)
    try:
        if args.thumbnail_action=='context':return {'episode_id':args.episode,'context':review_context(store,args.episode)}
        if args.thumbnail_action=='prepare':return prepare(store,args.episode,args.image,args.review,args.execute)
        return {'ok':True,'thumbnail':consume(store,args.episode)}
    finally:store.close()
