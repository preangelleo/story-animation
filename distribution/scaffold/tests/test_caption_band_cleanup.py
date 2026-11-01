"""Retired band declaration must fail even if it was formerly valid."""
import unittest
from storyflow.core import WorkflowError
from storyflow.scene_cleanup import normalize_cleanup
from storyflow.integrations import scene_filter
class CleanupRetirementTests(unittest.TestCase):
 def test_previously_valid_band_cannot_render(self):
  row={'media_type':'video','motion':'static','caption_band_cleanup':{'reason':'old corruption','source_sha256':'a'*64}}
  with self.assertRaises(WorkflowError):normalize_cleanup(row,'a'*64)
  with self.assertRaises(WorkflowError):scene_filter(row,320,180)
 def test_default_has_no_painted_band(self):
  self.assertIsNone(normalize_cleanup({},'a'*64))
  self.assertNotIn('drawbox',scene_filter({'motion':'static'},320,180))
