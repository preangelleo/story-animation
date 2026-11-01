import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from storyflow.revision_gate import check_revision
from storyflow.core import WorkflowError
from storyflow.integrations import voice, render, align

class Gate(unittest.TestCase):
 def test_pending_blocks_real_execute_entry_before_provider(self):
  from storyflow.production import handle as production_handle
  # All evidence belongs to this fixture; never consult the real 006 approval.
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'documentation').mkdir()
   (root/'documentation/configuration.json').write_text(json.dumps({
    'revision_approval_gates':{'fixture-pending-episode':'pending-gate.json'}}))
   (root/'pending-gate.json').write_text(json.dumps({
    'schema':3,'status':'pending_font_choice','actor':None,
    'owner_choice_evidence':None}))
   before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
   entries=[('voice',voice),('render',render),('align',align),
            ('chapter-revision',production_handle)]
   with patch('subprocess.run',side_effect=AssertionError('provider/process reached before approval')) as provider, \
        patch('storyflow.integrations.Store',side_effect=AssertionError('database opened before approval')) as integrations_store, \
        patch('storyflow.production.Store',side_effect=AssertionError('production database opened before approval')) as production_store:
    for name,entry in entries:
     with self.subTest(entry=name),self.assertRaisesRegex(WorkflowError,'waiting_owner'):
      entry(SimpleNamespace(root=root,episode='fixture-pending-episode',execute=True,
                            production_action='chapter-revision'))
    provider.assert_not_called()
    integrations_store.assert_not_called()
    production_store.assert_not_called()
   self.assertEqual(before,{str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()})
   self.assertFalse((root/'state').exists())
   self.assertFalse((root/'episodes').exists())
 def test_approved_requires_owner_and_current_both_hashes(self):
  from storyflow.chapters import sha256
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'template').write_text('layout');(p/'sound').write_text('sound')
   gate={'status':'approved','actor':'owner','owner_choice_evidence':'test fixture only','template_path':'template','sound_path':'sound','template_sha256':sha256(p/'template'),'sound_sha256':sha256(p/'sound')}
   (p/'gate.json').write_text(json.dumps(gate))
   with patch('storyflow.revision_gate.config',return_value={'revision_approval_gates':{'ep':'gate.json'}}):
    with self.assertRaisesRegex(WorkflowError,'legacy or missing font'):check_revision(p,'ep')
    (p/'sound').write_text('changed')
    with self.assertRaisesRegex(WorkflowError,'legacy or missing font'):check_revision(p,'ep')
    gate['actor']='ceo';(p/'gate.json').write_text(json.dumps(gate))
    with self.assertRaisesRegex(WorkflowError,'waiting_owner'):check_revision(p,'ep')
