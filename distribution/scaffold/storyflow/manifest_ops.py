from pathlib import Path
import json,hashlib
from .core import atomic_json,Store,WorkflowError

def assemble(root,episode):
 ROOT=Path(root).resolve();P=ROOT/'episodes'/episode/'production';W=P.parent/'work/visuals'
 def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
 def proof(p):return {'path':str(p.relative_to(ROOT)),'sha256':sha(p)}
 def read(p):return json.loads(p.read_text())
 plan=read(P/'visual-plan.json')
 from .style_binding import snapshot_for_episode,check_plan,check_binding
 store=Store(ROOT)
 try:snapshot=snapshot_for_episode(store,episode)
 finally:store.close()
 check_plan(plan,snapshot)
 reviews={v['id']:v for v in read(W/'image-reviews.json')};videos={v['id']:v for v in read(W/'video-reviews.json')}
 rows=[]
 for s in plan['scenes']:
  sid=s['id'];base={'id':sid,'style_id':plan['style_id'],'characters':s['characters'],'narrative':s['beat']}
  if s['media_type']=='image':
   actual=read(P/'image-generation'/f'{sid}.json');check_binding(actual,snapshot,'image record');check_binding(actual.get('generation'),snapshot,'image generation',prompt=True);review=dict(reviews[sid]);review['reviewed_all_input_sha256s']=review['reference_sha256s'];review['reference_sha256s']=sorted({r['sha256'] for c in s['characters'] for r in c['reference_images']});base.update({k:actual[k] for k in ('image','sha256','generation')});base['visual_review']=review
   if s.get('historical_source_record'):
    base['historical_source_record']=s['historical_source_record'];base['current_prompt']=s['prompt']
  else:
   f=P/'video-inserts'/sid;inp=read(f/'input.json');pred=read(f/'prediction.json');refs=read(f/'reference-origin.json')['references'];submission=read(f/'submission-receipt.json');review=dict(videos[sid]);review['reviewed_all_input_sha256s']=review['reference_sha256s'];review['reference_sha256s']=sorted({r['sha256'] for c in s['characters'] for r in c['reference_images']})
   gen={'tool':'replicate','model':'bytedance/seedance-2.5','prediction_id':pred['id'],'prompt':inp['prompt'],'input_file':proof(f/'input.json'),'prediction_file':proof(f/'prediction.json'),'download_receipt_file':proof(f/'download-receipt.json'),'reference_images':refs,'output_sha256':sha(f/'clip.mp4')}
   if snapshot:gen.update(style_id=snapshot['style_id'],style_snapshot_sha256=snapshot['snapshot_sha256'])
   if submission['reference_echo']=='provider_redacted_reference_echo':gen.update(reference_echo=submission['reference_echo'],submission_receipt_file=proof(f/'submission-receipt.json'))
   base.update({'media_type':'video','image':str((f/'clip.mp4').relative_to(ROOT)),'sha256':sha(f/'clip.mp4'),'generation':gen,'visual_review':review})
  from .scene_cleanup import normalize_cleanup
  if 'caption_band_cleanup' in s:
   base['caption_band_cleanup']=normalize_cleanup(s,base['sha256'])
   base['delivery_visual_review']=review.get('delivery_visual_review')
  if snapshot:base['style_snapshot_sha256']=snapshot['snapshot_sha256']
  rows.append(base)
 manifest={'schema_version':1,'style_id':plan['style_id'],'depiction':'illustrated_reconstruction','characters':plan['characters'],'scenes':rows,'similarity_reviews':[]}
 if snapshot:manifest.update(style_snapshot=snapshot,style_snapshot_sha256=snapshot['snapshot_sha256'])
 if plan.get('production_scope'):manifest['production_scope']=plan['production_scope']
 # Preserve genuinely recorded near-pair reviews across deterministic reassembly.
 if (W/'similarity-reviews.json').is_file():manifest['similarity_reviews']=read(W/'similarity-reviews.json')
 atomic_json(P/'visual-manifest.json',manifest);return {'ok':True,'scenes':len(rows),'review':'requires actual validator and independent review'}
