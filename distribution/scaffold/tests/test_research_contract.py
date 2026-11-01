import json
from pathlib import Path
import tempfile
import unittest
from storyflow.research_contract import material_bundle, writer_permissions

class Handoff(unittest.TestCase):
 def test_evidence_and_theme_required_and_hashes_change(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)
   with self.assertRaises(ValueError):material_bundle(p)
   (p/'dossier.md').write_text('事实与因果')
   (p/'themes.json').write_text(json.dumps({'themes':['选择'],'takeaway':'本片总结'}))
   (p/'sources.json').write_text(json.dumps({'sources':[{'url':'https://example.org/source','classification':'reported_claim','accessed_at':'2026-11-01'}]}))
   before=material_bundle(p)
   (p/'dossier.md').write_text('增加核实证据')
   self.assertNotEqual(before['files'],material_bundle(p)['files'])
   (p/'themes.json').write_text(json.dumps({'themes':[],'takeaway':'本片总结'}))
   with self.assertRaises(ValueError):material_bundle(p)
 def test_writer_has_no_browsing_exception(self):
  self.assertEqual(writer_permissions(),{'*':'deny'})
