"""Public API boundary payload contracts, all HTTP mocked; zero paid calls."""
import io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from storyflow import providers,integrations
class Response(io.BytesIO):
 def __enter__(self):return self
 def __exit__(self,*args):self.close()
class Providers(unittest.TestCase):
 def test_tts_uses_explicit_voice_and_model_without_environment_leak(self):
  calls=[]
  def http(request,**kw):calls.append(request);return Response(b'fake-mp3')
  with patch.dict(os.environ,{'ELEVENLABS_API_KEY':'offline-placeholder','ELEVENLABS_VOICE_ID':'configured-voice','UNRELATED_PRIVATE_VALUE':'do-not-send'},clear=True),patch('urllib.request.urlopen',side_effect=http):
   result=providers.eleven('speak',text='测试',voice='explicit-voice',model='configured-model')
  self.assertEqual(result,b'fake-mp3');self.assertTrue(calls[0].full_url.endswith('/explicit-voice'));body=json.loads(calls[0].data);self.assertEqual(body,{'text':'测试','model_id':'configured-model'});self.assertNotIn(b'do-not-send',calls[0].data)
 def test_original_text_multipart_alignment(self):
  calls=[]
  with tempfile.TemporaryDirectory() as tmp:
   audio=Path(tmp)/'voice.wav';audio.write_bytes(b'synthetic-audio')
   def http(req,**kw):calls.append(req);return Response(b'{"characters":[{"text":"a","start":0,"end":1}]}')
   with patch.dict(os.environ,{'ELEVENLABS_API_KEY':'offline-placeholder'},clear=True),patch('urllib.request.urlopen',side_effect=http):v=providers.eleven('align',text='纯旁白原稿',audio=audio)
  self.assertEqual(v['characters'][0]['text'],'a');self.assertIn('纯旁白原稿'.encode(),calls[0].data);self.assertIn(b'synthetic-audio',calls[0].data);self.assertEqual(len(calls),1)
 def test_network_error_does_not_retry(self):
  with patch.dict(os.environ,{'ELEVENLABS_API_KEY':'offline-placeholder'},clear=True),patch('urllib.request.urlopen',side_effect=TimeoutError) as network:
   with self.assertRaises(TimeoutError):providers.eleven('sfx',text='quiet drum',seconds=1)
  self.assertEqual(network.call_count,1)
 def test_execution_boundary_uses_package_adapter_not_private_path(self):
  class Result:returncode=0;stdout='{}'
  with patch('subprocess.run',return_value=Result()) as runner:integrations.run(['el','speak','test'])
  argv=runner.call_args.args[0];self.assertTrue(argv[1].endswith('adapters/el.py'));self.assertEqual(argv[2:4],['speak','test'])
