"""Real libass/ffmpeg seam regression; source cues deliberately straddle card frames."""
import hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from storyflow.core import atomic_json,digest,WorkflowError
ROOT=Path(__file__).resolve().parents[1]

class BurnFrameMaskTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.video=self.root/'clean.mp4';self.srt=self.root/'source.srt'
  subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=black:s=320x180:r=30:d=3','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=3','-c:v','libx264','-pix_fmt','yuv420p','-bf','0','-video_track_timescale','15360','-c:a','aac','-y',str(self.video)],check=True)
  # Card starts at frame31 (1.0333), old text expires13ms later.
  # Next text starts13ms before final card frame60 (2.0000).
  self.srt.write_text('1\n00:00:00,000 --> 00:00:01,047\n正文前段正常显示\n\n2\n00:00:01,987 --> 00:00:03,000\n正文后段正常恢复\n')
  self.assembly=self.root/'typed-assembly.json';self.derived=self.root/'derivation.json';self.binding=self.root/'chapter-revision.binding.json'
  assembly={'schema':'qy.scene.assembly.v1','fps':30,'output_frames':90,'nodes':[{'type':kind,'output_start_frame':a,'output_end_frame':b,'frame_count':b-a} for kind,a,b in [('scene',0,31),('transition',31,61),('scene',61,90)]]}
  atomic_json(self.assembly,assembly);atomic_json(self.derived,{'mapping':{'assembly':assembly},'outputs':{'clean.mp4':digest(self.video)}})
  atomic_json(self.binding,{'schema':'qy.chapter.revision.v1','episode_id':'fixture','delivery':{'video':{'path':str(self.video),'sha256':digest(self.video)}},'evidence':{'derivation':{'path':str(self.derived),'sha256':digest(self.derived)}}})
 def run_tool(self,name,mask=None,notice=False):
  folder=self.root/name;args=[sys.executable,str(ROOT/'tools'/('burn_subtitles.py' if name=='legacy' else 'burn_subtitles_masked.py')),'--video',str(self.video),'--srt',str(self.srt),'--output',str(folder/'out.mp4'),'--work',str(folder/'evidence'),'--first-frame-cover','--execute']
  if mask:args+=['--frame-mask',str(mask),'--font',os.environ['STORY_SUBTITLE_FONT'],'--font-family',os.environ['STORY_SUBTITLE_FAMILY']]
  if notice:args+=['--illustrated-reconstruction']
  subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True)
  data=subprocess.check_output(['ffmpeg','-v','error','-i',str(folder/'out.mp4'),'-map','0:v:0','-pix_fmt','gray','-f','rawvideo','-'])
  size=320*180;frames=[data[i:i+size] for i in range(0,len(data),size)]
  return folder,frames
 def test_actual_legacy_red_and_normal_masked_green_with_frame_zero_and_resume(self):
  before=digest(self.srt);old,legacy=self.run_tool('legacy')
  bright=lambda f:sum(v>180 for v in f)
  self.assertGreater(bright(legacy[30]),20) # Positive control: preceding normal captions render.
  self.assertGreater(bright(legacy[31]),20) # Actual legacy red:13ms cue leaks onto typed card.
  self.assertGreater(bright(legacy[60]),20) # Early next cue also leaks onto the final card frame.
  if os.environ.get('QY_MASK_LEGACY_RED'):
   self.assertEqual(bright(legacy[31]),0,'legacy libass leaks old text onto first card frame')
  from storyflow.caption_mask import create_mask
  mask=self.root/'frame-mask.json';atomic_json(mask,create_mask(self.binding,self.video))
  folder,frames=self.run_tool('masked',mask)
  self.assertEqual(len(frames),90);self.assertEqual(bright(frames[0]),0)
  self.assertGreater(bright(frames[30]),20)
  for n in range(31,61):self.assertEqual(bright(frames[n]),0,'body caption leaked on card frame '+str(n))
  self.assertGreater(bright(frames[61]),20);self.assertGreater(bright(frames[62]),20)
  self.assertEqual(digest(self.srt),before)
  result=json.loads((folder/'evidence/result.json').read_text());self.assertEqual(digest(folder/'evidence/subtitles.srt'),before);self.assertEqual(result['frame_mask']['ranges'],[[31,61]])
  def pcm(p):return hashlib.sha256(subprocess.check_output(['ffmpeg','-v','error','-i',str(p),'-map','0:a:0','-f','s16le','-'])).hexdigest()
  self.assertEqual(pcm(old/'out.mp4'),pcm(folder/'out.mp4'))
 def test_notice_keeps_frame_zero_free_and_actual_clock_stream_gate_passes(self):
  from storyflow.caption_mask import create_mask
  from storyflow.burn_ops import video_stream_gate
  mask=self.root/'frame-mask.json';atomic_json(mask,create_mask(self.binding,self.video))
  folder,frames=self.run_tool('notice',mask,notice=True)
  self.assertEqual(sum(v>180 for v in frames[0]),0)
  self.assertGreater(sum(v>180 for v in frames[1]),20)
  gate=video_stream_gate(self.video,folder/'out.mp4');self.assertEqual(gate['expected_output_frames'],90)
  rows=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(folder/'out.mp4')]))['streams']
  self.assertTrue(all(float(row['start_time'])==0 for row in rows))
 def test_unconfigured_font_family_is_clear_fail_closed(self):
  from storyflow.caption_mask import create_mask
  mask=self.root/'frame-mask.json';atomic_json(mask,create_mask(self.binding,self.video))
  env=dict(os.environ);env.pop('STORY_SUBTITLE_FAMILY',None)
  base=['--video',str(self.video),'--srt',str(self.srt),'--output',str(self.root/'missing-font.mp4'),'--work',str(self.root/'missing-evidence')]
  legacy=subprocess.run([sys.executable,str(ROOT/'tools/burn_subtitles.py')]+base,env=env,capture_output=True,text=True)
  self.assertNotEqual(legacy.returncode,0);self.assertIn('STORY_SUBTITLE_FAMILY must name',legacy.stderr)
  masked=subprocess.run([sys.executable,str(ROOT/'tools/burn_subtitles_masked.py')]+base+['--frame-mask',str(mask),'--font',env['STORY_SUBTITLE_FONT']],env=env,capture_output=True,text=True)
  self.assertNotEqual(masked.returncode,0);self.assertIn('--font-family',masked.stderr)
  self.assertFalse((self.root/'missing-font.mp4').exists());self.assertFalse((self.root/'missing-evidence').exists())
 def test_actual_source_frame_count_must_match_bound_mask(self):
  from storyflow.caption_mask import create_mask,validate_mask
  assembly=json.loads(self.assembly.read_text());assembly['output_frames']=91;assembly['nodes'][-1].update(output_end_frame=91,frame_count=30);atomic_json(self.assembly,assembly)
  derived=json.loads(self.derived.read_text());derived['mapping']['assembly']=assembly;atomic_json(self.derived,derived)
  binding=json.loads(self.binding.read_text());binding['evidence']['derivation']['sha256']=digest(self.derived);atomic_json(self.binding,binding)
  mask=create_mask(self.binding,self.video)
  with self.assertRaisesRegex(WorkflowError,'decoded source frame count'):validate_mask(mask,self.video,probe=True)
 def test_mask_rejects_overbroad_ranges_malformed_edl_and_source_drift(self):
  from storyflow.caption_mask import create_mask,validate_mask
  mask=create_mask(self.binding,self.video);self.assertEqual(validate_mask(mask,self.video),mask)
  bad=json.loads(json.dumps(mask));bad['ranges']=[[1,89]]
  with self.assertRaises(WorkflowError):validate_mask(bad,self.video)
  assembly=json.loads(self.assembly.read_text());assembly['nodes'][1]['output_start_frame']=30;atomic_json(self.assembly,assembly)
  derived=json.loads(self.derived.read_text());derived['mapping']['assembly']=assembly;atomic_json(self.derived,derived)
  binding=json.loads(self.binding.read_text());binding['evidence']['derivation']['sha256']=digest(self.derived);atomic_json(self.binding,binding)
  with self.assertRaises(WorkflowError):create_mask(self.binding,self.video)
  self.video.write_bytes(b'drift')
  with self.assertRaises(WorkflowError):validate_mask(mask,self.video)

if __name__=='__main__':unittest.main()
