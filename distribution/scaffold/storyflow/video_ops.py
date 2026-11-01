"""004 scoped orchestration around the existing shared Replicate CLI; never retries POST."""
from pathlib import Path
import sys,json,hashlib,base64,subprocess,shutil,urllib.request,argparse
ROOT=None
from storyflow.core import Store,WorkflowError
from storyflow.integrations import begin,finish
EP=None;P=None;CLI=Path(__file__).resolve().parents[1]/'adapters/replicate.py'
PYTHON=Path(sys.executable)
VERSION='0000635f6659d0b53dc4ab407a0abd1ab8f152fe7fa4dc50d291571be31a7d0a'
MODEL='bytedance/seedance-2.5'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def proof(p):return {'path':str(p.relative_to(ROOT)),'sha256':sha(p)}
def call(argv,out):
 if out.exists():raise RuntimeError('refusing overwrite of raw supplier response')
 with out.open('w') as f, out.with_suffix('.stderr.log').open('w') as e:
  cp=subprocess.run([str(PYTHON),str(CLI),*map(str,argv)],stdout=f,stderr=e,timeout=120)
 if cp.returncode:raise RuntimeError('shared CLI failed; inspect existing evidence, no automatic resubmit')
 return json.loads(out.read_text())
def submit(sid,reference):
 from storyflow.production import reserve_check
 st=Store(ROOT);reserve_check(st,EP,'video',7.5);st.close()
 f=P/'video-inserts'/sid;f.mkdir(parents=True,exist_ok=True)
 if (f/'operation.json').exists():raise WorkflowError('operation already exists; use status only')
 plan=json.loads((P/'visual-plan.json').read_text());scenes=plan['scenes']
 if sum(s['media_type']=='video' for s in scenes)>3 or scenes[-1]['media_type']!='image':raise WorkflowError('contract: at most three key video scenes and terminal still required')
 from .style_binding import snapshot_for_episode,check_plan
 st=Store(ROOT)
 try:snapshot=snapshot_for_episode(st,EP)
 finally:st.close()
 check_plan(plan,snapshot)
 scene=next(s for s in scenes if s['id']==sid)
 if scene['media_type']!='video':raise WorkflowError('scene is not an approved video scene')
 ref=ROOT/reference;assert ref.is_file()
 refs=[{'image':reference,'sha256':sha(ref)},*scene['reference_images']]
 prompt=scene['prompt']+' '+scene.get('video_prompt','')+' [Image1]是本轮新生成的场景构图/色彩参考，其余参考只固定角色身份。制作10秒连续动画，固定摄影机；人物/车辆按上述场面自然活动，不是对静态图片推拉缩放。绝不把定妆表或多视图排版带进成片。No speech, no voices, no music, no generated text. Preserve the exact episode visual style and character identity.'
 from .visual_prompt import compose_prompt
 prompt=compose_prompt(prompt,snapshot=snapshot)
 inp={'prompt':prompt,'duration':10,'resolution':'720p','aspect_ratio':'16:9','generate_audio':False,'watermark':False,'output_format':'mp4','reference_images':['data:image/'+('jpeg' if Path(r['image']).suffix.lower() in ('.jpg','.jpeg') else 'png')+';base64,'+base64.b64encode((ROOT/r['image']).read_bytes()).decode() for r in refs]}
 dump(f/'input.json',inp);dump(f/'reference-origin.json',{'references':refs});shutil.copyfile(CLI,f/'shared-cli-submission-snapshot.py')
 inputs={'style_snapshot_sha256':snapshot['snapshot_sha256'] if snapshot else None,'model':MODEL,'version':VERSION,'input_sha256':sha(f/'input.json'),'script_sha256':sha(P/'script.txt'),'duration':10,'resolution':'720p'}
 st=Store(ROOT);kind='replicate-'+sid;key,prior=begin(st,EP,kind,inputs,estimated=None)
 if prior:raise RuntimeError('operation done; no resubmission')
 op={'kind':kind,'key':key,'inputs':inputs,'reservation':'existing 7.5USD aggregate reservation; no additional reservation','status':'running'};dump(f/'operation.json',op)
 row=dict(st.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=? AND inputs_hash=?',(EP,kind,key)).fetchone());row['data']=json.loads(row['data']);dump(f/'pre-submission-ledger.json',row)
 try:pred=call(['generate','--model',MODEL,'--version',VERSION,'--input-json',f/'input.json','--no-wait','--json'],f/'creation.json')
 except Exception:
  finish(st,EP,kind,key,'unknown',{'inputs':inputs,'reason':'POST status requires inspection; forbidden automatic resubmit','actual_usd':None});st.close();raise
 dump(f/'submission-receipt.json',{'status':'submitted','method':'POST','endpoint':'https://api.replicate.com/v1/predictions','episode_id':EP,'model':MODEL,'version':VERSION,'request_input_file':proof(f/'input.json'),'pre_submission_operation_file':proof(f/'operation.json'),'pre_submission_ledger_file':proof(f/'pre-submission-ledger.json'),'pre_submission_created_at':row['created_at'],'shared_cli_file':proof(f/'shared-cli-submission-snapshot.py'),'shared_cli_snapshot_scope':'source snapshot made before this actual submission','creation_file':proof(f/'creation.json'),'prediction_id':pred['id'],'created_at':pred['created_at'],'reference_echo':('provider_redacted_reference_echo' if any(isinstance(x,str) and x.endswith('base64,...') for x in pred.get('input',{}).get('reference_images',[])) else 'complete_reference_echo'),'redacted_reference_fields':[{'input_key':'reference_images','input_index':i,'sha256':r['sha256']} for i,r in enumerate(refs) if i<len(pred.get('input',{}).get('reference_images',[])) and pred['input']['reference_images'][i].endswith('base64,...')]})
 finish(st,EP,kind,key,'running',{'inputs':inputs,'prediction_id':pred['id'],'actual_usd':None});st.close()
 print(json.dumps({'scene':sid,'prediction_id':pred['id'],'status':pred['status']}))
def status(sid):
 f=P/'video-inserts'/sid;op=json.loads((f/'operation.json').read_text())
 from .style_binding import snapshot_for_episode
 st=Store(ROOT)
 try:snapshot=snapshot_for_episode(st,EP)
 finally:st.close()
 if snapshot is not None:
  actual=json.loads((f/'input.json').read_text())
  if op['inputs'].get('style_snapshot_sha256')!=snapshot['snapshot_sha256'] or snapshot['prompt'] not in actual.get('prompt',''):raise WorkflowError('video operation frozen style mismatch')
 creation=json.loads((f/'creation.json').read_text());pid=creation['id']
 target=f/('status-'+str(len(list(f.glob('status-*.json')))+1)+'.json');pred=call(['status',pid,'--json'],target)
 if pred.get('id')!=pid:raise WorkflowError('prediction identity mismatch')
 if pred['status']=='succeeded':
  if not (f/'prediction.json').exists():shutil.copyfile(target,f/'prediction.json')
  if not (f/'clip.mp4').exists():
   output=pred['output']; url=output[0] if isinstance(output,list) and len(output)==1 else output; assert isinstance(url,str) and url.startswith('https://')
   import urllib.parse
   host=urllib.parse.urlparse(url).hostname;assert host=='replicate.delivery' or host.endswith('.replicate.delivery')
   with urllib.request.urlopen(url,timeout=120) as resp,(f/'clip.mp4').open('wb') as out:
    final=resp.geturl();http=resp.status;host2=urllib.parse.urlparse(final).hostname;assert host2=='replicate.delivery' or host2.endswith('.replicate.delivery')
    if http!=200:raise WorkflowError('download transport mismatch')
    while block:=resp.read(1024*1024):out.write(block)
   dump(f/'download-receipt.json',{'prediction_id':pid,'url':url,'final_url':final,'http_status':http,'sha256':sha(f/'clip.mp4'),'bytes':(f/'clip.mp4').stat().st_size})
  receipt=json.loads((f/'download-receipt.json').read_text())
  if receipt['prediction_id']!=pid or receipt['sha256']!=sha(f/'clip.mp4') or receipt['bytes']!=(f/'clip.mp4').stat().st_size:raise WorkflowError('download evidence hash drift')
  st=Store(ROOT)
  try:finish(st,EP,op['kind'],op['key'],'done',{'inputs':op['inputs'],'prediction_id':pid,'output_sha256':sha(f/'clip.mp4'),'actual_usd':None})
  finally:st.close()
 elif pred['status'] in ('failed','canceled'):
  st=Store(ROOT)
  try:finish(st,EP,op['kind'],op['key'],pred['status'],{'inputs':op['inputs'],'prediction_id':pid,'actual_usd':None})
  finally:st.close()
 print(json.dumps({'scene':sid,'prediction_id':pid,'status':pred['status']}))

def main(root,episode,action,scene,reference=None):
 global ROOT,EP,P,MODEL,VERSION
 ROOT=Path(root).resolve();EP=episode;P=ROOT/'episodes'/EP/'production'
 st=Store(ROOT)
 video_cfg=st.config.get('content',{}).get('video_inserts',{})
 MODEL=video_cfg.get('default_model',MODEL);VERSION=video_cfg.get('model_version',VERSION)
 try:
  from storyflow.integrations import checked
  _,arts=checked(st,EP,('script',))
  if not (P/'script.txt').is_file() or sha(P/'script.txt')!=arts['script']['sha256']:raise WorkflowError('production script must match reviewed frozen script')
 finally:st.close()
 if action=='submit':submit(scene,reference)
 else:status(scene)
 return {'ok':True,'scene':scene,'action':action,'note':'read raw receipts; no subjective PASS'}
