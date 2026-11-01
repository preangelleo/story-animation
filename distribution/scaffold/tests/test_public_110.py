"""Portable 1.1 role, font-free cover export and OAuth scope fixtures only."""
import json,os,tempfile,tomllib,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from PIL import Image
from tools.configure_roles import configure
from tools.export_cover import export
from storyflow.providers import youtube_service
ROOT=Path(__file__).resolve().parents[1]
class Public110(unittest.TestCase):
 def test_explicit_thumbnail_role_model_and_no_implicit_write(self):
  with tempfile.TemporaryDirectory() as tmp:
   r=Path(tmp);(r/'roles').mkdir();(r/'roles/thumbnail-designer.toml').write_bytes((ROOT/'roles/thumbnail-designer.toml').read_bytes())
   self.assertFalse(configure(r)['executed']);self.assertFalse((r/'.codex').exists())
   self.assertEqual(configure(r,True)['model'],'gpt-6.1-sol');self.assertEqual(tomllib.loads((r/'.codex/agents/thumbnail-designer.toml').read_text())['model'],'gpt-6.1-sol')
 def test_cover_real_decode_resize_no_crop_or_overwrite(self):
  with tempfile.TemporaryDirectory() as tmp:
   r=Path(tmp);src=r/'source.png';Image.new('RGB',(1600,900),'#d97757').save(src)
   result=export(src,r/'cover.jpg');self.assertEqual((result['width'],result['height']),(1280,720));self.assertLess(result['bytes'],2000000)
   with self.assertRaises(ValueError):export(src,r/'cover.jpg')
   with self.assertRaises(ValueError):export(src,r/'vertical.jpg',720,1280)
 def test_youtube_profile_cannot_traverse_or_fall_back(self):
  for profile in ('default','../other','/private'):
   with self.assertRaises(RuntimeError):youtube_service(profile)
 def test_caption_scope_required_and_never_automatically_added(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);token=root/'my-stories.json';token.write_text(json.dumps({'scopes':['https://www.googleapis.com/auth/youtube.upload']}))
   with patch.dict(os.environ,{'STORY_YOUTUBE_TOKEN_DIR':str(root)}),patch('google.oauth2.credentials.Credentials.from_authorized_user_file') as auth,patch('googleapiclient.discovery.build') as build:
    with self.assertRaisesRegex(RuntimeError,'force-ssl'):youtube_service('my-stories')
    auth.assert_not_called();build.assert_not_called()
    scopes=['https://www.googleapis.com/auth/youtube.force-ssl'];token.write_text(json.dumps({'scopes':scopes}))
    youtube_service('my-stories');self.assertEqual(auth.call_args.kwargs['scopes'],scopes);self.assertTrue(build.called)
