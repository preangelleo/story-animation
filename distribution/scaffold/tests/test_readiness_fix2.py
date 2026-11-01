"""Fix2 continuous synthetic local chain; only external upload/readback mocked."""
import copy
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import time
import unittest
import wave
from types import SimpleNamespace as NS
from unittest.mock import patch
from storyflow import integrations as it, production as p
from storyflow.burn_ops import burn
from storyflow.clock_qa import audit
from storyflow.core import Store, WorkflowError, atomic_json, digest
from tests import test_integrations as fixtures
from tests.test_spine_offline_paths import Response
from storyflow.cli import parser, run

ROOT=Path(__file__).resolve().parents[1]

class ContinuousChain(unittest.TestCase):
 def test_render_burn_register_clock_validate_share_readback_and_negative_controls(self):
  fx=fixtures.IntegrationTests('test_voice_plan_never_executes');fx.setUp()
  try:
   r,s,ep=fx.root,fx.store,fx.ep;work=it.folder(s,ep)
   # Temporary thresholds accommodate a 20-second synthetic fixture; all normal
   # validator code and independent-review binding rules remain enabled.
   cfg=json.loads((r/'documentation/configuration.json').read_text());cfg['quality']={'min_script_chars':1,'min_duration_seconds':.1};atomic_json(r/'documentation/configuration.json',cfg);s.config['quality']=cfg['quality']
   (r/'tools').mkdir();shutil.copyfile(ROOT/'tools/burn_subtitles.py',r/'tools/burn_subtitles.py')
   import numpy as np
   audio=r/'audio.wav'
   data=(np.random.default_rng(719).normal(0,.12,20*16000)*32767).astype('<i2')
   with wave.open(str(audio),'w') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(data.tobytes())
   image=r/'scene.png';cover=r/'cover.png'
   for target,size in [(image,'64x64'),(cover,'640x360')]:subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=cyan:s='+size,'-frames:v','1','-threads','1',str(target)],check=True)
   s.register(ep,'audio',audio)
   sub=s.register(ep,'subtitles',fx.file('sub.srt',''.join(f'{i+1}\n00:00:{i*3+1:02d},000 --> 00:00:{i*3+2:02d},000\n测试字幕\n\n' for i in range(6))))
   arts=s.artifacts(ep);atomic_json(work/'alignment.binding.json',{'inputs':{'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']},'artifact':sub})
   atomic_json(work/'scenes.json',{'scenes':[{'id':'s01','image':'scene.png','duration':20,'provenance':'synthetic offline test'}]})
   # The real source renderer creates its own binding. No hand-assembled render proof.
   args=fx.args();args.execute=True;render=it.render(args)
   binding=work/'render.binding.json';source_hash=digest(binding);source=r/render['artifact']['path'];source_bytes=digest(source)
   self.assertEqual(s.render_binding_errors(ep),[])
   s.register(ep,'cover',cover);episode=s.episode(ep)
   s.register(ep,'metadata',fx.file('meta.json',{'episode_id':ep,'channel':episode['channel'],'scheduled_at':episode['scheduled_at'],'title':'synthetic','description':'offline only'}))
   def review():
    arts=s.artifacts(ep)
    s.media_review(ep,fx.file('media.json',{'episode_id':ep,'reviewer':'offline-fixture','verdict':'pass','hashes':{k:arts[k]['sha256'] for k in ('script','audio','video','subtitles','cover','metadata')},'checks':{k:{'status':'pass','reason':'synthetic fixture only','evidence':'20-second local blue/noise fixture, no real story'} for k in ('narration','subtitles','visuals','ending','promise')}}))
   review();self.assertTrue(s.validate(ep)['passed'])
   self.assertFalse((work/'burns').exists());plan=burn(s,ep);self.assertFalse((work/'burns').exists())
   # The production CLI invokes the actual burner and registers its final output.
   with patch.dict(os.environ,{'PYTHONPATH':str(ROOT)}):
    v=run(parser().parse_args(['--root',str(r),'production','burn',ep,'--execute']))
   self.assertTrue(v['executed']);self.assertEqual(s.render_binding_errors(ep),[])
   self.assertFalse(s.validate(ep)['passed']);self.assertTrue(s.media_review_errors(ep))
   proof=json.loads((work/'burned.binding.json').read_text());burned=r/s.artifacts(ep)['video']['path']
   self.assertEqual(source_hash,digest(binding));self.assertEqual(source_bytes,digest(source));self.assertNotEqual(digest(source),digest(burned))
   snapshots={x:digest(x) for x in Path(v['derivation']).parent.rglob('*') if x.is_file()}
   self.assertTrue(burn(s,ep,True)['idempotent']);self.assertEqual(snapshots,{x:digest(x) for x in snapshots})
   # Full real audit, then normal review/validate, then only HTTP/provider edges mocked.
   clock=audit(burned,r/s.artifacts(ep)['audio']['path'],r/s.artifacts(ep)['subtitles']['path'],plan['clock_output'])
   self.assertTrue(clock['mechanical_passed'],clock);review();self.assertTrue(s.validate(ep)['passed'])
   self.assertTrue(burn(s,ep,True)['idempotent']);self.assertFalse(s.media_review_errors(ep))
   self.assertFalse(p.share(r,ep,burned,False)['executed'])
   self.assertRaises(WorkflowError,p.share,r,ep,source,False)
   cp=Path(plan['clock_output'])/'final-clock-review.json';saved=cp.read_bytes();old=json.loads(saved);old['video_sha256']=source_bytes;atomic_json(cp,old)
   self.assertRaises(WorkflowError,p.share,r,ep,burned,False);cp.write_bytes(saved)
   real_run=subprocess.run;uploads=[]
   def boundary(argv,**kw):
    if Path(argv[0]).name=='ffprobe':return real_run(argv,**kw)
    self.assertEqual(Path(argv[1]).name,'review-upload.py');uploads.append(argv);kw['stdout'].write('URL: https://example.org/share/offline/final.mp4\n');return NS(returncode=0)
   with patch.object(p.subprocess,'run',side_effect=boundary):
    result=p.share(r,ep,burned,True);self.assertTrue(result['ok']);self.assertTrue(p.share(r,ep,burned,True)['idempotent']);self.assertEqual(len(uploads),1)
   print('FIX2_CONTINUOUS_EVIDENCE='+json.dumps({'scope':'20s synthetic fixture; all supplier/HTTP edges mocked; no real episode','source_render':render,'source_binding_sha256':source_hash,'burn':v,'derivation':proof,'clock':clock,'normal_validation':s.validate(ep),'simulated_share':result},ensure_ascii=False))
   receipt=Path(result['receipt_path'])
   with patch('urllib.request.urlopen',side_effect=lambda req,**kw:Response(burned.read_bytes(),result['url'])):self.assertTrue(p.share_readback(burned,receipt,r/'readback.json')['ok'])
   # Missing/corrupt source, result, audio, SRT, style/command, and final video:
   # all invalidate the exact same continuously constructed chain.
   for file in [source,Path(proof['evidence']['result']['path']),r/s.artifacts(ep)['audio']['path'],r/s.artifacts(ep)['subtitles']['path'],Path(proof['evidence']['command']['path']),burned]:
    original=file.read_bytes()
    for action in ('wrong','missing'):
     with self.subTest(file=file.name,action=action):
      if action=='wrong':file.write_bytes(original+b'changed')
      else:file.unlink()
      self.assertTrue(s.render_binding_errors(ep));self.assertRaises(WorkflowError,burn,s,ep,True)
      file.write_bytes(original)
   self.assertEqual(s.render_binding_errors(ep),[])
   # Re-registering an input uses the normal QA invalidation rules.
   s.register(ep,'subtitles',fx.file('new.srt','1\n00:00:01,000 --> 00:00:02,000\n变更字幕\n'))
   self.assertTrue(s.render_binding_errors(ep));self.assertFalse(s.validate(ep)['passed']);self.assertTrue(s.media_review_errors(ep))
   self.assertEqual(source_hash,digest(binding));self.assertEqual(source_bytes,digest(source))
  finally:fx.tearDown()

