"""Hash-bound episode clip consumption; no supplier calls or real episode writes."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from storyflow.chapter_render import effective,composition
from storyflow.chapter_clip_gate import validate_parameter_contract,require_episode_clips
from storyflow.core import WorkflowError,atomic_json,digest

class ClipApproval(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.font=Path(subprocess.check_output(['fc-match','-f','%{file}','DejaVu Sans'],text=True))
  self.sound=self.root/'accepted.m4a'
  subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=1:sample_rate=48000','-c:a','aac',str(self.sound)],check=True)
  self.preset=self.root/'preset.json';atomic_json(self.preset,{'title_font':str(self.font),'number_font':str(self.font),'sound_path':str(self.sound),'muted':False,'title_text':'Test','chapter_number':'II'})
  self.contract={'schema':'qy.chapter.parameters.approval.v1','preset_path':str(self.preset),'preset_sha256':digest(self.preset),'clips':[]}
  self.gate={'title_font_sha256':digest(self.font),'number_font_sha256':digest(self.font),'accepted_base_clip_sha256':digest(self.sound)}
  for title,number in [('Second','II'),('Third','III')]:
   v,source=effective(self.root,self.preset,{'title_text':title,'chapter_number':number})
   clip=self.root/(number+'.mp4');clip.write_bytes(('synthetic '+number).encode())
   bundle=clip.with_suffix('.composition');(bundle/'assets').mkdir(parents=True)
   (bundle/'index.html').write_text(composition(v));(bundle/'assets/gsap.min.js').write_text('synthetic runtime')
   for role in ('title','number'):shutil.copyfile(self.font,bundle/'assets'/f'{role}.font')
   manifest={'schema':'qy.chapter-render.v1','effective':v,'source':source,'output':str(clip),'output_sha256':digest(clip),'composition':str(bundle),'executed':True,'audio_mode':'stream_copy','html_sha256':digest(bundle/'index.html'),'renderer':{'hyperframes':'test-fixture','module_sha256':'1'*64,'gsap_sha256':digest(bundle/'assets/gsap.min.js')}}
   atomic_json(bundle/'manifest.json',manifest)
   self.contract['clips'].append({'clip_path':str(clip),'clip_sha256':digest(clip),'manifest_path':str(bundle/'manifest.json'),'manifest_sha256':digest(bundle/'manifest.json'),'title_text':title,'chapter_number':number})
  self.contract_path=self.root/'approval-contract.json';self.save()
 def save(self):
  atomic_json(self.contract_path,self.contract);self.gate.update(parameter_contract_path=str(self.contract_path),parameter_contract_sha256=digest(self.contract_path))
 def manifest(self,index=0):return Path(self.contract['clips'][index]['manifest_path'])
 def change(self,mutate,index=0):
  path=self.manifest(index);v=json.loads(path.read_text());mutate(v);atomic_json(path,v)
  self.contract['clips'][index]['manifest_sha256']=digest(path);self.save()
 def validate(self,clips=None):return validate_parameter_contract(self.root,self.gate,clips)
 def test_complete_positive_and_ordered_consumption(self):
  rows=[{'clip':x['clip_path']} for x in self.contract['clips']]
  proof=self.validate(rows);self.assertEqual(len(proof['asset_relocations']),6)
  self.assertRaises(WorkflowError,self.validate,list(reversed(rows)))
  self.assertRaises(WorkflowError,self.validate,rows[:1])
 def test_same_bytes_relocation_does_not_need_old_private_paths(self):
  for i in range(2):
   def relocate(v):
    for role in ('title','number'):
     v['effective'][role+'_font']='/retired/private/'+role+'.ttf';v['source']['fonts'][role]['source']=v['effective'][role+'_font']
    v['effective']['sound_path']='/retired/private/s1.mp4';v['source']['sound']['source']=v['effective']['sound_path']
   self.change(relocate,i)
  proof=self.validate();self.assertEqual(proof['asset_relocations'][0]['original_asset_path'],'/retired/private/title.ttf')
  self.assertEqual(proof['asset_relocations'][0]['canonical_asset_path'],str(self.font))
 def test_wrong_font_combination_color_sound_or_gain_rejected(self):
  mutations=[lambda v:v['effective'].update(line_enabled=1),lambda v:v['effective'].update(background_color='#abcdef'),lambda v:v['effective'].update(title_family='C2Font'),lambda v:v['source']['fonts']['title'].update(sha256='2'*64),lambda v:v['source']['sound'].update(sha256='2'*64),lambda v:v.update(audio_mode='explicit_gain_aac'),lambda v:v['effective'].update(sound_volume=.2),lambda v:v['effective'].update(muted=True)]
  original=self.manifest().read_bytes()
  for change in mutations:
   with self.subTest(change=change):
    self.manifest().write_bytes(original);self.change(change)
    self.assertRaises(WorkflowError,self.validate)
 def test_missing_drifted_manifest_html_font_and_runtime_rejected(self):
  bundle=self.manifest().parent
  for p in [self.manifest(),bundle/'index.html',bundle/'assets/title.font',bundle/'assets/gsap.min.js']:
   old=p.read_bytes();p.write_bytes(old+b'drift')
   self.assertRaises(WorkflowError,self.validate);p.write_bytes(old)
   p.unlink();self.assertRaises((WorkflowError,FileNotFoundError),self.validate);p.write_bytes(old)
  self.sound.write_bytes(b'different asset bytes')
  self.assertRaises(WorkflowError,self.validate)
 def test_missing_parameter_contract_gated_rejected_ungated_portable(self):
  self.assertIsNone(require_episode_clips(self.root,'ungated',[]))
  with patch('storyflow.chapter_clip_gate.config',return_value={'revision_approval_gates':{'gated':'gate.json'}}),patch('storyflow.revision_gate.check_revision'):
   atomic_json(self.root/'gate.json',{'status':'approved'})
   self.assertRaises(WorkflowError,require_episode_clips,self.root,'gated',[])
  self.assertIsNone(validate_parameter_contract(self.root,{},required=False))
  self.assertRaises(WorkflowError,validate_parameter_contract,self.root,{})

 def test_actual_episode_entrypoints_reject_unapproved_clip_before_derive(self):
  from storyflow import chapter_mix
  rows=[{'clip':x['clip_path']} for x in self.contract['clips']]
  wrong=list(reversed(rows));gate_path=self.root/'gate.json';atomic_json(gate_path,self.gate)
  work=self.root/'episodes/gated/production';work.mkdir(parents=True)
  atomic_json(work/'chapter-revision.binding.json',{'plan':{'insertions':wrong}})
  store=SimpleNamespace(root=self.root)
  with patch('storyflow.chapter_clip_gate.config',return_value={'revision_approval_gates':{'gated':'gate.json'}}),patch('storyflow.revision_gate.check_revision'),patch.object(chapter_mix,'derive') as derive:
   self.assertRaises(WorkflowError,chapter_mix.revise,store,'gated',{'insertions':wrong},work/'new',True)
   self.assertRaises(WorkflowError,chapter_mix.verify_revision,store,'gated')
   derive.assert_not_called()
