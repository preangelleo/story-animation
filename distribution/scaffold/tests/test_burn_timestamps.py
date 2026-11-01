"""Exercise the real burn CLI across the AAC44100→48000 encoder boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'tools/burn_subtitles.py'
FONT=Path(os.environ.get('STORY_SUBTITLE_FONT','assets/fonts/subtitle.ttf')).expanduser()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe') and FONT.is_file(),'requires production encoder and configured subtitle font')
class BurnTimestampTests(unittest.TestCase):
    def test_real_burn_preserves_zero_clock_and_aac_priming(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);source=p/'source.mp4';out=p/'burned.mp4';srt=p/'caption.srt'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=25:duration=1.5','-f','lavfi','-i','sine=frequency=440:sample_rate=44100:duration=1.5','-c:v','libx264','-threads','2','-c:a','aac','-shortest',str(source)],check=True)
            srt.write_text('1\n00:00:00,100 --> 00:00:01,400\n时钟验证\n',encoding='utf8')
            env=os.environ.copy();env['PYTHONPATH']=str(ROOT)
            subprocess.run([sys.executable,str(SCRIPT),'--video',str(source),'--srt',str(srt),'--output',str(out),'--work',str(p/'work'),'--execute'],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
            probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(out)]))
            video=next(s for s in probe['streams'] if s['codec_type']=='video');audio=next(s for s in probe['streams'] if s['codec_type']=='audio')
            self.assertEqual(video['time_base'],'1/15360');self.assertEqual(video['r_frame_rate'],'30/1')
            self.assertEqual(video['start_pts'],0);self.assertEqual(audio['start_pts'],0)
            packets=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','a:0','-read_intervals','%+#1','-show_packets','-of','json',str(out)]))['packets']
            self.assertEqual(packets[0]['pts'],-1024)
            self.assertTrue(any(d.get('skip_samples')==1024 for d in packets[0].get('side_data_list',[])))
