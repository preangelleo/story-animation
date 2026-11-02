"""Real decoder regression for mixed lossless/lossy H264 picture carriers."""
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from storyflow.chapter_mix import concat_pictures
from storyflow.scene_assembly import framemd5


@unittest.skipUnless(shutil.which('ffmpeg'), 'requires ffmpeg')
class MixedPictureContext(unittest.TestCase):
    def test_lossy_video_after_lossless_scene_keeps_every_decoded_pixel(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            paths=[]
            for label,source,crf in [
                ('still','color=c=gray:s=1920x1080:r=30:d=14.8','0'),
                ('video','testsrc2=s=1920x1080:r=30:d=10','18')]:
                path=root/(label+'.mp4');paths.append(path)
                subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',source,
                    '-an','-c:v','libx264','-preset','fast','-crf',crf,'-bf','0','-threads','1',
                    '-pix_fmt','yuv420p','-video_track_timescale','15360',str(path)],check=True)
            audio=root/'audio.wav'
            with wave.open(str(audio),'wb') as wav:
                wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(48000)
                wav.writeframes(b'\0'*(1190400*4))
            original=[framemd5(p) for p in paths]
            # A standalone remux is the positive control for the original video.
            alone=root/'alone';alone.mkdir()
            concat_pictures([paths[1]],audio,alone/'clean.mp4',alone/'commands.log')
            self.assertEqual(framemd5(alone/'clean.mp4'),original[1])
            joined=root/'joined';joined.mkdir()
            concat_pictures(paths,audio,joined/'clean.mp4',joined/'commands.log')
            self.assertEqual(framemd5(joined/'clean.mp4'),original[0]+original[1])
            self.assertEqual([framemd5(p) for p in paths],original)
