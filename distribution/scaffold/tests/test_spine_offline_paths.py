"""Executable local spine paths. Provider/transport/model boundaries only are faked."""
import argparse
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from storyflow.core import Store, WorkflowError, atomic_json, digest
from storyflow import production as p, video_ops as v, integrations as it
from storyflow.manifest_ops import assemble
from tests import test_integrations as f, test_visuals as visual_f

class Response(io.BytesIO):
 status=200
 def __init__(self,data,url):super().__init__(data);self.url=url
 def geturl(self):return self.url

class SpinePaths(unittest.TestCase):
 def setUp(self):
  self.fx=f.IntegrationTests('test_voice_plan_never_executes');self.fx.setUp();self.s=self.fx.store;self.root=self.fx.root;self.ep=self.fx.ep;self.work=it.folder(self.s,self.ep)
 def tearDown(self):self.fx.tearDown()
 def test_video_actual_json_post_ledger_get_download_and_manifest_branch(self):
  p.reserve(self.s,self.ep,'video',7.5)
  (self.work/'script.txt').write_bytes((self.root/self.s.artifacts(self.ep)['script']['path']).read_bytes())
  cli=self.fx.file('shared-tool.py','# offline supplier boundary')
  ref=self.fx.file('ref.png','synthetic image fixture');reference=str(ref.relative_to(self.root))
  scenes=[{'id':sid,'media_type':'video' if i<3 else 'image','prompt':'fixture character','video_prompt':'fixture motion','reference_images':[],'characters':[],'beat':'fixture'} for i,sid in enumerate(('s1','s2','s3','s4'))]
  atomic_json(self.work/'visual-plan.json',{'style_id':'scene-classic-rubber-hose','characters':[],'scenes':scenes})
  calls=[]
  def external(argv,stdout,stderr,**kw):
   calls.append(argv);action=argv[2]
   pred={'id':'offline-prediction','created_at':'2026-11-01T00:00:00Z','status':'starting','input':{}}
   if action=='status':pred.update(status='succeeded',output='https://replicate.delivery/offline.mp4')
   stdout.write(json.dumps(pred));return NS(returncode=0)
  with patch.object(v,'CLI',cli),patch.object(v.subprocess,'run',side_effect=external),patch('urllib.request.urlopen',side_effect=lambda url,**kw:Response(b'offline-video-bytes',url)),redirect_stdout(io.StringIO()):
   result=v.main(self.root,self.ep,'submit','s1',reference);self.assertTrue(result['ok'])
   folder=self.work/'video-inserts/s1';req=json.loads((folder/'input.json').read_text());self.assertEqual(req['duration'],10);self.assertFalse(req['generate_audio']);self.assertTrue(req['reference_images'][0].startswith('data:'))
   receipt=json.loads((folder/'submission-receipt.json').read_text());self.assertEqual(receipt['creation_file']['sha256'],digest(folder/'creation.json'));self.assertEqual(receipt['request_input_file']['sha256'],digest(folder/'input.json'))
   self.assertTrue(v.main(self.root,self.ep,'status','s1')['ok']);self.assertEqual((folder/'clip.mp4').read_bytes(),b'offline-video-bytes')
   saved={x.name:digest(x) for x in folder.iterdir() if x.name not in ('operation.json',)}
   v.main(self.root,self.ep,'status','s1');self.assertEqual(sum(a[2]=='generate' for a in calls),1)
   with self.assertRaises(WorkflowError):v.main(self.root,self.ep,'submit','s1',reference)
   for name,h in saved.items():self.assertEqual(digest(folder/name),h)
   # Assemble both JSON branches from actual preceding receipts; do not invent reviews in code.
   plan=json.loads((self.work/'visual-plan.json').read_text());plan['scenes']=[scenes[0],scenes[-1]];atomic_json(self.work/'visual-plan.json',plan)
   w=self.work.parent/'work/visuals';w.mkdir(parents=True)
   atomic_json(w/'image-reviews.json',[{'id':'s4','reference_sha256s':[],'result':'fixture-only'}]);atomic_json(w/'video-reviews.json',[{'id':'s1','reference_sha256s':[],'result':'fixture-only'}])
   atomic_json(self.work/'image-generation/s4.json',{'image':reference,'sha256':digest(ref),'generation':{'fixture':True}})
   self.assertEqual(assemble(self.root,self.ep)['scenes'],2)
   manifest=json.loads((self.work/'visual-manifest.json').read_text());self.assertEqual(manifest['scenes'][0]['generation']['prediction_id'],'offline-prediction');self.assertEqual(manifest['scenes'][0]['sha256'],digest(folder/'clip.mp4'))
   (folder/'clip.mp4').write_bytes(b'corrupt')
   with self.assertRaisesRegex(WorkflowError,'hash drift'):v.main(self.root,self.ep,'status','s1')
  self.assertEqual(self.s.db.execute("SELECT status FROM external_operations WHERE kind='replicate-s1'").fetchone()[0],'done')
 def test_manifest_images_assemble_then_real_validator_success_and_missing_review(self):
  fx=visual_f.VisualContractTests('test_distinct_images_pass');fx.setUp()
  try:
   root=fx.root
   legacy=Store(root)
   try:legacy.create('2030-01-01','legacy fixture','legacy','fixture',review_only=True)
   finally:legacy.close()
   prod=root/'episodes/fixture/production';w=root/'episodes/fixture/work/visuals';prod.mkdir(parents=True);w.mkdir(parents=True)
   plan={'style_id':fx.manifest['style_id'],'characters':fx.manifest['characters'],'scenes':[]};reviews=[]
   for scene in fx.manifest['scenes']:
    plan['scenes'].append({'id':scene['id'],'media_type':'image','beat':scene['generation']['prompt'],'characters':scene['characters']})
    atomic_json(prod/'image-generation'/(scene['id']+'.json'),scene)
    reviews.append({'id':scene['id'],**scene['visual_review']})
   atomic_json(prod/'visual-plan.json',plan);atomic_json(w/'image-reviews.json',reviews);atomic_json(w/'video-reviews.json',[])
   self.assertEqual(assemble(root,'fixture')['scenes'],2)
   manifest=json.loads((prod/'visual-manifest.json').read_text())
   from storyflow.visuals import validate_manifest
   self.assertTrue(validate_manifest(manifest,root)['passed'])
   atomic_json(w/'image-reviews.json',[])
   with self.assertRaises(KeyError):assemble(root,'fixture')
  finally:fx.tearDown()
 def test_asr_serialization_compare_and_real_fa_binding_success_then_drift(self):
  # Only model inference is synthetic; no model downloaded or loaded.
  audio=self.root/'tone.wav'
  subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','pcm_s16le',str(audio)],check=True)
  self.s.register(self.ep,'audio',audio)
  module=NS(WhisperModel=lambda *a,**kw:NS(transcribe=lambda *a,**kw:(iter([NS(start=0,end=.9,text='钥匙。',words=[NS(word='钥匙',start=.1,end=.9,probability=.99)])]),NS(duration=1))))
  transcript=self.work/'asr/whisper-medium.json'
  with patch.dict('sys.modules',{'faster_whisper':module}):
   result=p.asr(audio,transcript,'medium');self.assertEqual(result['asr_sha256'],digest(transcript))
   with self.assertRaises(WorkflowError):p.asr(audio,transcript,'medium')
  script=self.root/self.s.artifacts(self.ep)['script']['path'];comparison=self.work/'asr/comparison.json';atomic_json(comparison,p.compare(script,transcript))
  diff=json.loads(comparison.read_text());self.assertEqual(diff['changes'],[]);self.assertEqual(diff['status'],'requires_manual_fulltext_review')
  # Actual align path, including independent ASR receipt, category reservation and binding checks.
  cfg=json.loads((self.root/'documentation/configuration.json').read_text());cfg['voice']={'provider':'fish_audio'};atomic_json(self.root/'documentation/configuration.json',cfg)
  inputs={'script_sha256':digest(script),'audio_sha256':digest(audio)}
  atomic_json(self.work/'voice.binding.json',{'inputs':inputs,'audio_sha256':digest(audio)})
  audit={'status':'pass','reviewer':'offline-independent-fixture','script_sha256':digest(script),'audio_sha256':digest(audio),'asr_sha256':digest(transcript),'comparison_sha256':digest(comparison)};atomic_json(self.work/'asr/fulltext-audit.json',audit)
  p.reserve(self.s,self.ep,'alignment',.5)
  args=self.fx.args();args.execute=True;args.confirm_cost=True;args.estimated_usd=.5;args.use_reservation=True
  def align_provider(argv,**kw):
   atomic_json(Path(argv[-1]),{'characters':[{'text':'钥','start':.1,'end':.3},{'text':'匙','start':.4,'end':.8},{'text':'。','start':.8,'end':.8}]});return ''
  before=self.s.db.execute('SELECT SUM(amount) FROM costs').fetchone()[0]
  with patch.object(it,'run',side_effect=align_provider) as external:
   v1=it.align(args);self.assertEqual(v1['status'],'done');self.assertEqual(external.call_count,1)
   self.assertTrue(it.align(args)['idempotent']);self.assertEqual(external.call_count,1)
  self.assertEqual(before,self.s.db.execute('SELECT SUM(amount) FROM costs').fetchone()[0]);self.assertEqual(v1['inputs'],inputs)
  # New audio forces new binding and cannot reuse prior ASR proof.
  changed=self.fx.file('new-audio.wav','synthetic-new-audio');self.s.register(self.ep,'audio',changed)
  newinputs={**inputs,'audio_sha256':digest(changed)};atomic_json(self.work/'voice.binding.json',{'inputs':newinputs,'audio_sha256':digest(changed)})
  with patch.object(it,'run',side_effect=AssertionError('no FA on drift')):
   with self.assertRaisesRegex(WorkflowError,'ASR fulltext'):it.align(args)
 def test_ingest_actual_entry_and_hash_error(self):
  from storyflow.cli import parser,run
  source=self.fx.file('new.png','synthetic-image');record=self.fx.file('record.json',{'sha256':digest(source),'generation':{'tool':'offline-fixture'}})
  args=parser().parse_args(['--root',str(self.root),'production','ingest',self.ep,'--file',str(source),'--record',str(record),'--name','s01.png','--execute'])
  self.assertTrue(run(args)['ok']);target=self.work/'images/s01.png';self.assertEqual(digest(target),digest(source));self.assertTrue((self.work/'image-generation/s01.json').exists())
  source.write_text('different');self.assertRaises(WorkflowError,run,args)

class SharePaths(unittest.TestCase):
 def test_real_validate_and_clock_preconditions_then_upload_boundary_readback(self):
  fx=f.IntegrationTests('test_voice_plan_never_executes');fx.setUp()
  try:
   root,s,ep=fx.root,fx.store,fx.ep;work=it.folder(s,ep)
   # Explicit tiny fixture thresholds in temporary Store only. Normal quality code still runs.
   cfg=json.loads((root/'documentation/configuration.json').read_text());cfg['quality']={'min_script_chars':1,'min_duration_seconds':.1};atomic_json(root/'documentation/configuration.json',cfg);s.config['quality']=cfg['quality']
   audio=root/'audio.wav';video=root/'video.mp4';cover=root/'cover.png'
   for argv in (['-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','pcm_s16le',str(audio)],['-f','lavfi','-i','color=c=blue:s=640x360:r=30:d=1','-i',str(audio),'-c:v','libx264','-threads','1','-c:a','aac','-shortest',str(video)],['-f','lavfi','-i','color=c=blue:s=640x360','-frames:v','1','-threads','1',str(cover)]):subprocess.run(['ffmpeg','-v','error',*argv],check=True)
   for kind,file in [('audio',audio),('video',video),('cover',cover),('subtitles',fx.file('subtitle.srt','1\n00:00:00,000 --> 00:00:00,900\n测试字幕\n'))]:s.register(ep,kind,file)
   episode=s.episode(ep);s.register(ep,'metadata',fx.file('meta.json',{'episode_id':ep,'channel':episode['channel'],'scheduled_at':episode['scheduled_at'],'title':'fixture','description':'offline'}))
   arts=s.artifacts(ep)
   s.media_review(ep,fx.file('final-review.json',{'episode_id':ep,'reviewer':'offline-fixture','verdict':'pass','hashes':{k:arts[k]['sha256'] for k in ('script','audio','video','subtitles','cover','metadata')},'checks':{k:{'status':'pass','reason':'synthetic fixture only','evidence':'one-second blue and sine fixture'} for k in ('narration','subtitles','visuals','ending','promise')}}))
   self.assertTrue(s.validate(ep)['passed'])
   hashes={'video_sha256':digest(video),'audio_sha256':digest(audio),'srt_sha256':arts['subtitles']['sha256']}
   clock={**hashes,'mechanical_passed':True,'source_unchanged':True,'input_hashes_before':hashes,'input_hashes_after':hashes}
   clockpath=work/'burned-ultrasafe/final-clock-review.json';atomic_json(clockpath,clock)
   # This test exercises share's contract, ClockTests separately proves full clock audit.
   real_run=subprocess.run
   def no_upload(argv,**kw):
    if Path(argv[0]).name!='ffprobe':raise AssertionError('plan must not upload')
    return real_run(argv,**kw)
   with patch.object(p.subprocess,'run',side_effect=no_upload):
    self.assertFalse(p.share(root,ep,video,False)['executed'])
    bad=copy.deepcopy(clock);bad['audio_sha256']='wrong';atomic_json(clockpath,bad)
    with self.assertRaisesRegex(WorkflowError,'bound final'):p.share(root,ep,video,True)
    atomic_json(clockpath,clock)
   uploads=[]
   def upload(argv,**kw):
    if Path(argv[0]).name=='ffprobe':return real_run(argv,**kw)
    uploads.append(argv);kw['stdout'].write('URL: https://example.org/share/offline/final.mp4\n');return NS(returncode=0)
   with patch.object(p.subprocess,'run',side_effect=upload) as external:
    result=p.share(root,ep,video,True);self.assertTrue(result['ok']);self.assertTrue(p.share(root,ep,video,True)['idempotent']);self.assertEqual(len(uploads),1)
   receipt=Path(result['receipt_path'])
   with patch('urllib.request.urlopen',side_effect=lambda req,**kw:Response(video.read_bytes(),result['url'])):
    self.assertTrue(p.share_readback(video,receipt,root/'readback.json')['ok'])
   with patch('urllib.request.urlopen',side_effect=lambda req,**kw:Response(b'wrong',result['url'])):
    self.assertFalse(p.share_readback(video,receipt,root/'bad-readback.json')['ok'])
   # A new same-episode final artifact preserves old and legacy delivery evidence.
   old_receipt=receipt.read_bytes();old_log=(receipt.parent/'r2-upload.log').read_bytes()
   legacy=work.parent/'delivery/r2-upload.log';legacy.write_text('historical short-version upload')
   atomic_json(legacy.parent/'r2-upload-receipt.json',result)
   video.write_bytes(video.read_bytes()+b'longform-version')
   s.register(ep,'video',video);arts=s.artifacts(ep)
   review=json.loads((root/'final-review.json').read_text())
   review['hashes']={k:arts[k]['sha256'] for k in review['hashes']}
   s.media_review(ep,fx.file('new-final-review.json',review))
   hashes['video_sha256']=digest(video)
   atomic_json(clockpath,{**hashes,'mechanical_passed':True,'source_unchanged':True,'input_hashes_before':hashes,'input_hashes_after':hashes})
   with patch.object(p.subprocess,'run',side_effect=upload):
    new_result=p.share(root,ep,video,True)
    self.assertTrue(p.share(root,ep,video,True)['idempotent'])
   self.assertEqual(len(uploads),2);self.assertNotEqual(new_result['receipt_path'],str(receipt))
   self.assertEqual(receipt.read_bytes(),old_receipt);self.assertEqual((receipt.parent/'r2-upload.log').read_bytes(),old_log)
   self.assertEqual(legacy.read_text(),'historical short-version upload')
   self.assertEqual(json.loads(Path(new_result['receipt_path']).read_text())['sha256'],digest(video))
   (legacy.parent/'r2-upload-receipt.json').unlink()
   with self.assertRaisesRegex(WorkflowError,'legacy upload outcome unknown'):p.share(root,ep,video,True)
   atomic_json(legacy.parent/'r2-upload-receipt.json',result)
   # An unresolved upload never retries with different input.
   it.finish(s,ep,'review-share',it.fingerprint({'sha256':digest(video),'retention_days':7,'bytes':video.stat().st_size}),'unknown',{})
   with patch.object(p.subprocess,'run',side_effect=no_upload):
    with self.assertRaises(WorkflowError):p.share(root,ep,video,True)
  finally:fx.tearDown()

if __name__=='__main__':unittest.main()
