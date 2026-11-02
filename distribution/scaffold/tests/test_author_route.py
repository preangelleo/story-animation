import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from storyflow.author_route import route
from storyflow.core import atomic_json
ROOT=Path(__file__).resolve().parents[1]
class AuthorRoute(unittest.TestCase):
 def test_custom_adapter_called_and_default_handoff_not_ark(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);brief=root/'brief.md';brief.write_text('Tell the frozen fictional story.');args=SimpleNamespace(root=root,brief_file=brief,material_dir=ROOT/'examples/materials',advice_file=None,run_dir=root/'handoff',execute=True)
   atomic_json(root/'documentation/configuration.json',{'author':{'provider':'claude','network_tools':False}})
   result=route(args);self.assertEqual(result['status'],'handoff_ready');packet=json.loads((root/'handoff/packet.json').read_text());self.assertEqual(packet['provider'],'claude');self.assertIsNone(packet['command']);self.assertEqual(packet['tools_allowed'],[])
   adapter=root/'custom.py';adapter.write_text('import sys,json\np=json.load(sys.stdin)\nassert p["provider"]=="synthetic-custom"\nassert p["tools_allowed"]==[]\nprint(json.dumps({"text":"A new fixture narration.","tools_used":[],"tool_policy_evidence":"synthetic adapter has no tool implementation","actual_cost_usd":0}))\n')
   atomic_json(root/'documentation/configuration.json',{'author':{'provider':'synthetic-custom','network_tools':False,'command':[sys.executable,str(adapter)]}});args.run_dir=root/'custom-run'
   result=route(args);self.assertEqual(result['status'],'candidate_requires_independent_review');self.assertEqual((root/'custom-run/script.txt').read_text(),'A new fixture narration.');self.assertEqual(result['actual_cost_usd'],0)
