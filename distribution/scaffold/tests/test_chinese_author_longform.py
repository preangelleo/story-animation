"""Offline author length contract: unlimited is not a larger hidden ceiling."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from unittest.mock import patch
from types import SimpleNamespace
from tools import chinese_author
from tools.chinese_author import length_matches

def package(root):
 material=root/'materials';material.mkdir()
 (material/'dossier.md').write_text('人物因果与核实素材，非旧稿')
 (material/'themes.json').write_text(json.dumps({'themes':['约束欲望'],'takeaway':'本片总结'}))
 (material/'sources.json').write_text(json.dumps({'sources':[{'url':'https://example.org/source','accessed_at':'2026-11-01','classification':'fact'}]}))
 return material

class AuthorLengthTests(unittest.TestCase):
 def test_unlimited_has_no_twenty_thousand_ceiling(self):
  self.assertTrue(length_matches(20001,3500,0))
  self.assertTrue(length_matches(1000000,3500,0))
  self.assertFalse(length_matches(3499,3500,0))
  self.assertFalse(length_matches(5001,3500,5000))
  self.assertTrue(length_matches(5000,3500,5000))
 def test_execute_real_output_parsing_beyond_legacy_ceiling(self):
  # Only provider process and credential retrieval are replaced; run parsing is real.
  text='汉'*20001
  answer='===SCRIPT_BEGIN==='+text+'===SCRIPT_END===\n===SOURCES_BEGIN===来源===SOURCES_END===\n===SELF_REVIEW_BEGIN===待独审===SELF_REVIEW_END==='
  events=[]
  events.append({'type':'text','sessionID':'offline','part':{'text':answer}})
  proc=SimpleNamespace(stdout=[json.dumps(e,ensure_ascii=False)+'\n' for e in events],wait=lambda **kw:0,poll=lambda:0)
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);material=package(root);brief=root/'brief.md';brief.write_text('新的完整故事brief')
   argv=['author','--brief-file',str(brief),'--material-dir',str(material),'--run-dir',str(root/'run'),'--min-han-chars','3500','--max-han-chars','0','--execute']
   with patch.object(sys,'argv',argv),patch.dict(__import__('os').environ,{'ARK_AGENT_PLAN_API_KEY':'offline-test-placeholder'}),patch.object(chinese_author.subprocess,'Popen',return_value=proc),redirect_stdout(io.StringIO()):
    self.assertEqual(chinese_author.main(),0)
   result=json.loads((root/'run/run.json').read_text())
   self.assertEqual(result['status'],'candidate_requires_independent_review')
   self.assertEqual(result['chinese_characters'],20001)
   self.assertEqual((root/'run/script.txt').read_text().strip(),text)
 def test_plan_unlimited_defaults_and_invalid_range_without_external_action(self):
  tool=Path(__file__).resolve().parents[1]/'tools/chinese_author.py'
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);material=package(root);brief=root/'brief.md';brief.write_text('完整丰富故事，无篇幅上限')
   argv=[sys.executable,str(tool),'--brief-file',str(brief),'--material-dir',str(material),'--run-dir',str(root/'run')]
   cp=subprocess.run(argv,capture_output=True,text=True,check=True)
   self.assertEqual(json.loads(cp.stdout)['length_target_han_characters'],{'min':3500,'max':0})
   cp=subprocess.run(argv+['--min-han-chars','3500','--max-han-chars','0'],capture_output=True,text=True,check=True)
   self.assertEqual(json.loads(cp.stdout)['length_target_han_characters'],{'min':3500,'max':0})
   cp=subprocess.run(argv+['--max-han-chars','5000'],capture_output=True,text=True,check=True)
   self.assertEqual(json.loads(cp.stdout)['length_target_han_characters'],{'min':3500,'max':5000})
   self.assertFalse((root/'run').exists())
   for args in (['--max-han-chars','-1'],['--min-han-chars','0','--max-han-chars','0'],['--min-han-chars','3500','--max-han-chars','2900']):
    self.assertNotEqual(subprocess.run(argv+args,capture_output=True,text=True).returncode,0)

if __name__=='__main__':unittest.main()
