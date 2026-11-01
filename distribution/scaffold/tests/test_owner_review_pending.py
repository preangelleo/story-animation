"""Honest owner audition pending permits review-only delivery, never publication."""
import copy
import json
from pathlib import Path
from unittest.mock import patch
from tests import test_readiness_fix2 as continuous_fixture
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from storyflow import integrations
from storyflow.core import WorkflowError
from tests.test_core import CoreTests

class OwnerReviewPending(unittest.TestCase):
 def setUp(self):
  self.fx=CoreTests('test_schedule_monday_wednesday'); self.fx.setUp()
  self.s=self.fx.store;self.r=self.fx.root
  self.fx.ep=self.s.create('2026-11-03','Review fixture','fixture',ep='owner-review-fixture',review_only=True)['id'];self.ep=self.fx.ep
  self.fx.scaffold()
  for kind in ('audio','video','subtitles','cover','metadata'):self.s.register(self.ep,kind,self.fx.file(kind+'.txt',kind))
  self.value={'episode_id':self.ep,'reviewer':'independent-fixture-editor','verdict':'pending_owner_review','hashes':{k:v['sha256'] for k,v in self.s.artifacts(self.ep).items() if k in ('script','audio','video','subtitles','cover','metadata')},'owner_review':{'status':'pending_owner_review','human_listening':'not_performed','prosody':'not_assessed','rare_names':'unknown'},'checks':{k:{'status':'pass','reason':'synthetic schema regression','evidence':'local fixture only; no actual listening claim'} for k in ('narration','subtitles','visuals','ending','promise')}}
  self.value['checks']['narration'].update(status='pending_owner_review',machine_checks={k:{'status':'pass','reason':'synthetic technical review evidence','evidence':'offline schema fixture'} for k in ('asr','forced_alignment')})
 def tearDown(self):self.fx.tearDown()
 def record(self,value=None):return self.s.media_review(self.ep,self.fx.file('pending.json',self.value if value is None else value))
 def test_visual_revise_records_honest_unheard_narration_and_still_blocks(self):
  value=copy.deepcopy(self.value);value['verdict']='revise';value['checks']['visuals']['status']='revise'
  result=self.record(value);self.assertEqual(result['checks']['narration']['status'],'pending_owner_review')
  self.assertIn('最终审片要求修改',self.s.media_review_errors(self.ep))
  self.assertFalse(self.s.validate(self.ep)['passed'])
  with self.assertRaises(WorkflowError):integrations.ship(SimpleNamespace(root=self.r,episode=self.ep,execute=False))
  value['verdict']='pass'
  with self.assertRaises(WorkflowError):self.record(value)
 def test_pending_retains_unknown_and_hash_binding(self):
  recorded=self.record();self.assertEqual(recorded['owner_review']['rare_names'],'unknown');self.assertEqual(self.s.media_review_errors(self.ep),[])
  self.s.register(self.ep,'audio',self.fx.file('changed.wav','changed'));self.assertTrue(self.s.media_review_errors(self.ep))
 def test_pending_cannot_mask_revise_or_missing_machine_evidence(self):
  for key in ('subtitles','visuals','ending','promise'):
   v=copy.deepcopy(self.value);v['checks'][key]['status']='revise'
   with self.subTest(key=key),self.assertRaises(WorkflowError):self.record(v)
  for key in ('asr','forced_alignment'):
   for field in ('status','reason','evidence'):
    v=copy.deepcopy(self.value);v['checks']['narration']['machine_checks'][key].pop(field)
    with self.subTest(key=key,field=field),self.assertRaises(WorkflowError):self.record(v)
 def test_pending_rejects_fake_human_pass_and_ordinary_production(self):
  for key in ('human_listening','prosody'):
   v=copy.deepcopy(self.value);v['owner_review'][key]='pass'
   with self.subTest(key=key),self.assertRaises(WorkflowError):self.record(v)
  ep=self.s.episode(self.ep);ep['review_only']=False
  with self.s.db:self.s.db.execute('UPDATE episodes SET data=? WHERE id=?',(json.dumps(ep),self.ep))
  with self.assertRaises(WorkflowError):self.record()
 def test_consumption_rechecks_pending_schema_and_receipt(self):
  recorded=self.record();recorded['checks']['narration']['machine_checks']['asr']['status']='revise'
  with self.s.db:self.s.db.execute('UPDATE media_reviews SET data=? WHERE episode_id=?',(json.dumps(recorded),self.ep))
  self.assertTrue(self.s.media_review_errors(self.ep))
 @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'local ffmpeg required')
 def test_real_local_media_normal_validation_and_publication_denial(self):
  # Only fixture duration/text minima are reduced. No validator/review function mocked.
  self.s.config['quality']={'min_script_chars':1,'min_duration_seconds':.1}
  def ff(args):subprocess.run(['ffmpeg','-v','error','-y',*args],check=True,capture_output=True)
  audio=self.r/'audio.wav';video=self.r/'video.mp4';cover=self.r/'cover.png'
  ff(['-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','pcm_s16le',str(audio)])
  ff(['-f','lavfi','-i','color=c=blue:s=640x360:d=1','-i',str(audio),'-c:v','libx264','-threads','1','-c:a','aac','-shortest',str(video)])
  ff(['-f','lavfi','-i','color=c=blue:s=640x360','-frames:v','1','-threads','1',str(cover)])
  for kind,path in [('audio',audio),('video',video),('cover',cover),('subtitles',self.fx.file('valid.srt','1\n00:00:00,000 --> 00:00:00,900\n测试字幕\n'))]:self.s.register(self.ep,kind,path)
  ep=self.s.episode(self.ep)
  self.s.register(self.ep,'metadata',self.fx.file('meta.json',{'episode_id':self.ep,'channel':ep['channel'],'scheduled_at':ep['scheduled_at'],'title':'fixture','description':'synthetic only'}))
  self.value['hashes']={k:v['sha256'] for k,v in self.s.artifacts(self.ep).items() if k in self.value['hashes']}
  self.record()
  for execute in (False,True):
   with self.assertRaises(WorkflowError):integrations.ship(SimpleNamespace(root=self.r,episode=self.ep,execute=execute))
  self.assertIsNone(self.s.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='external_operations'").fetchone())
  result=self.s.validate(self.ep);self.assertTrue(result['passed'],result['errors']);self.assertEqual(result['owner_review']['human_listening'],'not_performed');self.assertIn('publication_forbidden',result['delivery_scope'])
  with self.assertRaises(WorkflowError):self.s.publication(self.ep,self.fx.file('receipt.json',{'episode_id':self.ep}),verified=True)
  # Pending listening cannot bypass real subtitle integrity errors.
  self.s.register(self.ep,'subtitles',self.fx.file('invalid.srt','1\n00:00:00,000 --> 00:00:05,000\n超出视频\n'))
  self.value['hashes']['subtitles']=self.s.artifacts(self.ep)['subtitles']['sha256'];self.record()
  self.assertFalse(self.s.validate(self.ep)['passed'])

class PendingContinuousChain(unittest.TestCase):
 def test_actual_render_burn_clock_validate_share_readback_with_owner_pending(self):
  # Reuse the existing full real synthetic chain with a legitimately created
  # review-only episode and an honestly scoped fixture review. Only caller
  # inputs change; actual create/review/render/burn/clock/validate/share run.
  from storyflow.core import Store
  real_create=Store.create;real_review=Store.media_review
  def create_review_only(store,*args,**kwargs):
   kwargs['review_only']=True
   return real_create(store,*args,**kwargs)
  def pending_review(store,ep,file):
   value=json.loads(Path(file).read_text())
   value['verdict']='pending_owner_review'
   value['owner_review']={'status':'pending_owner_review','human_listening':'not_performed','prosody':'not_assessed'}
   value['checks']['narration'].update(status='pending_owner_review',machine_checks={k:{'status':'pass','reason':'synthetic technical schema fixture only','evidence':'local blue/noise fixture; no real listening or story ASR claim'} for k in ('asr','forced_alignment')})
   Path(file).write_text(json.dumps(value))
   return real_review(store,ep,file)
  with patch.object(Store,'create',new=create_review_only),patch.object(Store,'media_review',new=pending_review):
   continuous_fixture.ContinuousChain('test_render_burn_register_clock_validate_share_readback_and_negative_controls').test_render_burn_register_clock_validate_share_readback_and_negative_controls()
