"""Synthetic, network-free optional module contracts and clean HOME CLI checks."""
import copy,itertools,json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from storyflow.core import WorkflowError
from storyflow.learning_options import MODULES,validate
ROOT=Path(__file__).resolve().parents[1]
class OptionalModules(unittest.TestCase):
 def test_every_combination_has_honest_dependency_contract(self):
  for bits in itertools.product((False,True),repeat=6):
   selected=dict(zip(MODULES,bits));v={'enabled':selected['sql'],'vector':{'enabled':selected['vector']},'comments':{'enabled':selected['comments'],'auto_reply':selected['reply']},'topics':{'enabled':selected['topics']},'dedup':{'enabled':selected['dedup']}}
   invalid=(not selected['sql'] and any(bits[1:])) or (selected['reply'] and not selected['comments'])
   if invalid:
    with self.assertRaises(WorkflowError):validate(v)
   else:self.assertEqual(validate(v),v)
 def test_clean_home_install_configure_and_disabled_commands(self):
  with tempfile.TemporaryDirectory() as d:
   home=Path(d)/'home';home.mkdir();target=Path(d)/'installed';shutil.copytree(ROOT,target,ignore=shutil.ignore_patterns('__pycache__','state','episodes','.codex'))
   env={'HOME':str(home),'PATH':os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1'}
   def run(*argv,ok=True):
    p=subprocess.run(['python3',str(target/'bin/story-animation'),*argv],env=env,cwd=target,capture_output=True,text=True)
    self.assertEqual(p.returncode==0,ok,p.stdout+p.stderr);return json.loads(p.stdout)
   initial=run('modules','status');self.assertFalse(initial['semantic_configured']);self.assertFalse(initial['learning']['enabled'])
   run('init');self.assertTrue((target/'state/story.sqlite3').exists())
   preview=run('modules','configure','--enable','sql,dedup,topics','--author-provider','claude');self.assertFalse(preview['execute']);self.assertFalse(run('modules','status')['learning']['enabled'])
   run('modules','configure','--enable','sql,dedup,topics','--execute');configured=run('modules','status');self.assertTrue(configured['learning']['enabled']);self.assertFalse(configured['semantic_configured'])
   run('modules','configure','--enable','vector','--execute',ok=False)
   run('modules','configure','--enable','','--execute');self.assertFalse(run('modules','status')['learning']['enabled'])
   run('learning','facts',ok=False)
   self.assertEqual(list(home.iterdir()),[])
 def test_all_combinations_cli_and_sql_state_smoke(self):
  with tempfile.TemporaryDirectory() as d:
   target=Path(d);(target/'documentation').mkdir();shutil.copy2(ROOT/'documentation/configuration.json',target/'documentation/configuration.json')
   env={'HOME':str(target/'home'),'PATH':os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1'}
   for bits in itertools.product((False,True),repeat=6):
    names=[name for name,enabled in zip(MODULES,bits) if enabled]
    p=subprocess.run(['python3',str(ROOT/'bin/story-animation'),'--root',str(target),'modules','configure','--enable',','.join(names),'--execute'],env=env,capture_output=True,text=True)
    invalid=(not bits[0] and any(bits[1:])) or (bits[3] and not bits[2])
    self.assertEqual(p.returncode!=0,invalid,p.stdout+p.stderr)
    if not invalid:
     state=subprocess.run(['python3',str(ROOT/'bin/story-animation'),'--root',str(target),'modules','status'],env=env,capture_output=True,text=True)
     self.assertEqual(state.returncode,0,state.stdout+state.stderr)
     self.assertFalse(json.loads(state.stdout)['semantic_configured'])
     if bits[0]:
      sql=subprocess.run(['python3',str(ROOT/'bin/story-animation'),'--root',str(target),'learning','status'],env=env,capture_output=True,text=True)
      self.assertEqual(sql.returncode,0,sql.stdout+sql.stderr)
      self.assertEqual(json.loads(sql.stdout)['facts'],[])
 def test_default_no_private_identity_or_optional_enable(self):
  cfg=json.loads((ROOT/'documentation/configuration.json').read_text());self.assertFalse(cfg['learning']['enabled']);self.assertFalse(cfg['learning']['comments']['auto_reply']);self.assertIsNone(cfg['learning']['vector']['endpoint']);self.assertIsNone(cfg['channel']['id'])
