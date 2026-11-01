"""Real offline chapter derivation; no supplier, no production episodes."""
import json
import copy
import hashlib
def digest_bytes(data): return hashlib.sha256(data).hexdigest()
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import patch
from storyflow import chapter_mix as m, integrations as it, production as prod
from storyflow.burn_ops import burn
from storyflow.clock_qa import audit
from storyflow.core import WorkflowError, atomic_json, digest
from tests import test_integrations as fixtures
ROOT=Path(__file__).resolve().parents[1]

class ChapterClock(unittest.TestCase):
 def test_all_five_streams_and_absolute_quantization(self):
  item={'start':318,'end':730}
  p={'source_duration':1078.4,'insertions':[{'source_time':319.32,'duration':1},{'source_time':727.62,'duration':1}],
     'streams':{k:[item] for k in ('narration','subtitles','shots','sfx','bgm')}}
  from storyflow.chapters import timeline_plan
  # Continuous clocks split, while scene nodes must already end at chapters.
  self.assertRaises(WorkflowError,m.plan,p)
  p['streams'].pop('shots');p['streams'].pop('bgm')
  v=timeline_plan(p)
  self.assertEqual(v['delivery_duration'],1080.4)
  for name,stream in v['streams'].items():
   if name=='shots':continue
   self.assertEqual(len(stream),3)
   self.assertAlmostEqual(sum(x['end']-x['start'] for x in stream),412)
  self.assertEqual(m.ticks(319.32,30),9580)
  self.assertEqual(m.ticks(727.62,30),21829)
 def test_subtitle_boundary_cannot_duplicate_spoken_text(self):
  with tempfile.TemporaryDirectory() as d:
   source=Path(d)/'source.srt';source.write_text('1\n00:00:01,000 --> 00:00:03,000\n不要切断\n')
   self.assertRaises(WorkflowError,m.mapped_srt,source,Path(d)/'out.srt',[{'source_time':2,'duration':1}])

 def test_reviewed_quiet_boundary_moves_display_only_and_is_hash_bound(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);source=r/'source.srt';source.write_text('1\n00:00:01,000 --> 00:00:02,000\n前一句\n\n2\n00:00:02,000 --> 00:00:03,000\n后一句\n')
   original=source.read_bytes();waveform=r/'waveform.json'
   atomic_json(waveform,[{'source_time':2,'windows':[{'relative_start_ms':-100,'rms_dbfs':-80}]}])
   item={'source_srt_sha256':digest(source),'source_audio_sha256':'a'*64,'previous_index':1,'next_index':2,'previous_text':'前一句','next_text':'后一句','old_boundary':2,'new_boundary':1.9,'waveform':{'path':str(waveform),'sha256':digest(waveform)},'review':{'status':'pass','reviewer':'synthetic editor','reason':'synthetic quiet interval; no human listening','evidence':['synthetic RMS']}}
   insertions=[{'source_time':1.9,'duration':1}]
   values=m.mapped_srt(source,r/'out.srt',insertions,[item],'a'*64)
   self.assertEqual(values[0]['end'],1.9);self.assertEqual(values[1]['start'],2.9)
   self.assertEqual([x['text'] for x in values],['前一句','后一句']);self.assertEqual(source.read_bytes(),original)
   for key,value in [('source_audio_sha256','b'*64),('source_srt_sha256','b'*64),('previous_text','伪字'),('old_boundary',2.3),('new_boundary',1.7),('review',{'status':'pending'})]:
    bad=copy.deepcopy(item);bad[key]=value
    self.assertRaises(WorkflowError,m.mapped_srt,source,r/'bad.srt',insertions,[bad],'a'*64)
   atomic_json(waveform,[{'source_time':2,'windows':[{'relative_start_ms':-100,'rms_dbfs':-20}]}]);item['waveform']['sha256']=digest(waveform)
   self.assertRaises(WorkflowError,m.mapped_srt,source,r/'loud.srt',insertions,[item],'a'*64)

class ChapterChain(unittest.TestCase):
 def test_real_splice_registered_burn_clock_and_drift(self):
  fx=fixtures.IntegrationTests('test_voice_plan_never_executes');fx.setUp()
  try:
   import numpy as np
   r,s,ep=fx.root,fx.store,fx.ep;work=it.folder(s,ep)
   cfg=json.loads((r/'documentation/configuration.json').read_text());cfg['quality']={'min_script_chars':1,'min_duration_seconds':.1};atomic_json(r/'documentation/configuration.json',cfg);s.config['quality']=cfg['quality']
   (r/'tools').mkdir();shutil.copyfile(ROOT/'tools/burn_subtitles.py',r/'tools/burn_subtitles.py')
   audio=r/'audio.wav';data=(np.random.default_rng(719).normal(0,.08,20*16000)*32767).astype('<i2')
   with wave.open(str(audio),'w') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(data.tobytes())
   image=r/'scene.png'
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=cyan:s=64x64','-frames:v','1','-threads','1',str(image)],check=True)
   s.register(ep,'audio',audio)
   sub=s.register(ep,'subtitles',fx.file('sub.srt',''.join(f'{i+1}\n00:00:{i*3+1:02d},000 --> 00:00:{i*3+2:02d},000\n测试字幕\n\n' for i in range(6))))
   arts=s.artifacts(ep);atomic_json(work/'alignment.binding.json',{'inputs':{'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']},'artifact':sub})
   atomic_json(work/'scenes.json',{'scenes':[{'id':sid,'image':'scene.png','duration':duration,'provenance':'synthetic offline test'} for sid,duration in [('s01',6.02),('s02',6),('s03',7.98)]]})
   args=fx.args();args.execute=True;source=it.render(args);original_arts=s.artifacts(ep);source_bytes=digest(work/'render.binding.json')
   card=r/'card.mp4'
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=0x141414:s=64x64:r=30:d=1','-f','lavfi','-i','sine=frequency=550:sample_rate=48000:duration=1','-c:v','libx264','-bf','0','-threads','1','-color_range','tv','-colorspace','bt709','-color_trc','bt709','-color_primaries','bt709','-c:a','aac','-shortest',str(card)],check=True)
   review=r/'clip-review.json';atomic_json(review,{'status':'pass','reviewer':'synthetic fixture only','clips':[{'path':str(card),'sha256':digest(card),'status':'pass','reason':'synthetic fixture only','evidence':['dark chapter fixture']} ]})
   payload={'scene_clips':source['scene_clips'],'source_duration':20,'insertions':[{'source_time':6.02,'duration':1,'clip':str(card),'previous_scene_id':'s01','next_scene_id':'s02'},{'source_time':12.02,'duration':1,'clip':str(card),'previous_scene_id':'s02','next_scene_id':'s03'}],'clip_review_path':str(review),'cues':[{'path':str(audio),'sha256':digest(audio),'source_start':11.8,'source_end':12.2,'asset_in':1,'gain_db':-30,'duck_db':-8,'fade_in':.02,'fade_out':.02}]}
   target=work/'chapter-revisions/test'
   self.assertFalse(target.exists());m.revise(s,ep,payload,target,False);self.assertFalse(target.exists())
   v=m.revise(s,ep,payload,target,True)
   self.assertEqual(s.render_binding_errors(ep),[])
   self.assertEqual(source_bytes,digest(work/'render.binding.json'))
   for kind in ('script','structure','audio','subtitles'):self.assertEqual(original_arts[kind],s.artifacts(ep)[kind])
   self.assertTrue(m.revise(s,ep,payload,target,True)['idempotent'])
   derived=json.loads((target/'derivation.json').read_text());self.assertEqual(derived['pcm_audit']['source_samples_dropped'],0)
   self.assertEqual(derived['pcm_audit']['delivery_samples'],22*48000)
   self.assertEqual(derived['mapping']['transitions'][0]['source_frame'],181)
   with patch.dict(os.environ,{'PYTHONPATH':str(ROOT)}):result=burn(s,ep,True)
   self.assertEqual(s.render_binding_errors(ep),[])
   final_probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=duration,nb_frames','-of','json',str(r/s.artifacts(ep)['video']['path'])]))['streams'][0]
   self.assertEqual(int(final_probe['nb_frames']),660,final_probe)
   self.assertAlmostEqual(float(final_probe['duration']),22,places=5)
   stream_contract=json.loads((target/'picture-stream-contract.json').read_text())
   self.assertEqual(stream_contract['canonical']['sample_aspect_ratio'],'1:1')
   self.assertEqual(stream_contract['canonical']['color_space'],'bt709')
   self.assertEqual(stream_contract['canonical']['color_range'],'tv')
   for row in stream_contract['segments']:
    self.assertEqual(row['stream']['color_primaries'],'bt709')
    self.assertEqual(row['stream']['color_transfer'],'bt709')
   self.assertRaises(WorkflowError,m.validate_picture_segments,[target/'picture-0.mp4'],[999])
   from storyflow.burn_ops import video_stream_gate
   damaged=r/'long-audio-truncated-video.mp4'
   subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(r/s.artifacts(ep)['video']['path']),'-vf','trim=end_frame=300,setpts=PTS-STARTPTS','-map','0:v:0','-map','0:a:0','-c:v','libx264','-bf','0','-threads','1','-r','30','-video_track_timescale','15360','-c:a','copy',str(damaged)],check=True)
   damaged_probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(damaged)]))
   self.assertAlmostEqual(float(damaged_probe['format']['duration']),22,places=5)
   self.assertEqual(float(damaged_probe['streams'][0]['duration']),10)
   self.assertRaises(WorkflowError,video_stream_gate,target/'clean.mp4',damaged)
   delivery=m.delivery_inputs(s,ep)
   self.assertNotEqual(delivery['audio']['sha256'],s.artifacts(ep)['audio']['sha256'])
   clock=audit(r/s.artifacts(ep)['video']['path'],delivery['audio']['path'],delivery['subtitles']['path'],result['clock_output'],chapter={'mapping':m.plan(payload),'binding_sha256':digest(work/'chapter-revision.binding.json')})
   self.assertTrue(clock['mechanical_passed'],clock)
   self.assertTrue(clock['full_decode']['black_intervals'])
   self.assertFalse(clock['full_decode']['unexpected_black_intervals'])
   self.assertEqual(clock['audio_sha256'],digest(target/'mix.wav'))
   self.assertTrue(burn(s,ep,True)['idempotent'])
   cover=r/'cover.png'
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=640x360','-frames:v','1','-threads','1',str(cover)],check=True)
   s.register(ep,'cover',cover);episode=s.episode(ep)
   s.register(ep,'metadata',fx.file('meta.json',{'episode_id':ep,'channel':episode['channel'],'scheduled_at':episode['scheduled_at'],'title':'synthetic','description':'offline only'}))
   arts=s.artifacts(ep)
   s.media_review(ep,fx.file('media.json',{'episode_id':ep,'reviewer':'offline-fixture','verdict':'pass','hashes':{k:arts[k]['sha256'] for k in ('script','audio','video','subtitles','cover','metadata')},'checks':{k:{'status':'pass','reason':'synthetic fixture only','evidence':'22-second local blue/green/noise fixture, no real story'} for k in ('narration','subtitles','visuals','ending','promise')}}))
   self.assertTrue(s.validate(ep)['passed'],s.validate(ep))
   self.assertEqual(s.validate(ep)['media']['audio']['duration'],22)
   final=r/arts['video']['path']
   self.assertFalse(prod.share(r,ep,final,False)['executed'])
   # A fresh dry-audio audit can never substitute for actual mixed delivery.
   cp=Path(result['clock_output'])/'final-clock-review.json';old=cp.read_bytes();bad=json.loads(old);bad['audio_sha256']=arts['audio']['sha256'];atomic_json(cp,bad)
   self.assertRaises(WorkflowError,prod.share,r,ep,final,False);cp.write_bytes(old)
   for path in (target/'mix.wav',target/'delivery.srt',target/'narration-projection.wav',card,review):
    old=path.read_bytes();path.write_bytes(old+b'drift')
    self.assertTrue(s.render_binding_errors(ep));self.assertRaises(WorkflowError,m.delivery_inputs,s,ep)
    path.write_bytes(old)
   self.assertEqual(s.render_binding_errors(ep),[])
   binding=work/'chapter-revision.binding.json';prior=binding.read_bytes()
   invalid={**payload,'insertions':[{'source_time':6.02,'duration':1,'clip':str(r/'missing.mp4')}]}
   self.assertRaises((WorkflowError,FileNotFoundError),m.revise,s,ep,invalid,work/'chapter-revisions/failure',True)
   self.assertEqual(binding.read_bytes(),prior)
   newer=m.revise(s,ep,payload,work/'chapter-revisions/second',True)
   self.assertTrue((work/'chapter-revision-history'/digest_bytes(prior)/'chapter-revision.binding.json').is_file())
   self.assertEqual(s.render_binding_errors(ep),[])
   archived=m.archive_current(s,ep,True)
   self.assertFalse(binding.exists());self.assertTrue(Path(archived['history']).exists())
   self.assertEqual(s.artifacts(ep)['video']['sha256'],source['artifact']['sha256'])
   self.assertEqual(s.render_binding_errors(ep),[])
  finally:fx.tearDown()
