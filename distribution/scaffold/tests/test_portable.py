import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from storyflow.portable import doctor
from storyflow.providers import eleven
class Portable(unittest.TestCase):
 def test_no_credentials_doctor_does_not_claim_production(self):
  with patch.dict(os.environ,{},clear=True):
   result=doctor(Path(__file__).resolve().parents[1]);self.assertFalse(result['production_ready']);self.assertEqual(result['paid_calls'],0)
 def test_missing_voice_refuses_before_network(self):
  with patch.dict(os.environ,{},clear=True),patch('urllib.request.urlopen',side_effect=AssertionError('no network')):
   with self.assertRaises(RuntimeError):eleven('speak',text='测试',voice='user-voice')
