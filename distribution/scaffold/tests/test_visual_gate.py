"""Synthetic, local-only integration fixtures; never production visual approval."""
import argparse
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from storyflow.core import Store, WorkflowError, atomic_json, digest
from storyflow import integrations as it


class VisualGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        atomic_json(self.root/'documentation/configuration.json',{'quality':{'require_visual_manifest':True}})
        self.store=Store(self.root);self.ep='visual-gate-test'
        self.store.create('2030-01-01','Synthetic test only','test',ep=self.ep,verification_only=True)
        self.work=self.root/'episodes'/self.ep/'production';self.work.mkdir()
        for kind in ('structure','script','audio','subtitles'):
            p=self.root/(kind+'.txt');p.write_text('{}' if kind=='structure' else 'test')
            self.store.register(self.ep,kind,p)
        self.png('sheet.png',0);self.png('shot.png',1);self.png('different.png',2)
        self.sheet=self.asset('sheet.png');self.shot=self.asset('shot.png')
        self.visual={
            'schema_version':1,'style_id':'scene-classic-rubber-hose','depiction':'illustrated_reconstruction',
            'characters':[{'id':'actor','lock':{k:'synthetic locked' for k in ('age','face','hair','skin','build')},
                'outfits':[{'id':'shirt','description':'synthetic blue shirt'}],
                'references':[{**self.sheet,'outfit_id':'shirt','views':['front','side','back','expression_neutral','expression_tense']}]}],
            'scenes':[{'id':'s1',**self.shot,'style_id':'scene-classic-rubber-hose',
                'characters':[{'character_id':'actor','outfit_id':'shirt','reference_images':[self.sheet]}],
                'generation':{'tool':'builtin.image_gen','call_id':'synthetic-unit-test-only',
                    'prompt':'synthetic locked wearing synthetic blue shirt',
                    'reference_images':[self.sheet],'output_sha256':self.shot['sha256']},
                'visual_review':{'composition': {'full_frame': True, 'subtitle_reserve': False, 'bottom_edge_notes': 'synthetic fixture edge content'}, 'reviewer':'synthetic-test-fixture','result':'pass',
                    'notes':'Fixture schema only; not production visual inspection',
                    'image_sha256':self.shot['sha256'],'reference_sha256s':[self.sheet['sha256']]}}],
            'similarity_reviews':[]}
        atomic_json(self.work/'visual-manifest.json',self.visual)
        self.scenes={'scenes':[{'id':'s1','image':'shot.png','duration':1,'provenance':'synthetic unit test only'}]}
        atomic_json(self.work/'scenes.json',self.scenes)
        arts=self.store.artifacts(self.ep)
        atomic_json(self.work/'alignment.binding.json',{'inputs':{'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']},'artifact':arts['subtitles']})

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def png(self,name,mode):
        pixels=bytes(v for y in range(16) for x in range(16) for v in ((x*13+y*7+mode*31)%256,(y*11+mode*17)%256,(x*9+mode*23)%256))
        def chunk(tag,data):
            return struct.pack('!I',len(data))+tag+data+struct.pack('!I',zlib.crc32(tag+data)&0xffffffff)
        raw=b''.join(b'\0'+pixels[y*48:(y+1)*48] for y in range(16))
        (self.root/name).write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',16,16,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))

    def asset(self,name):return {'image':name,'sha256':digest(self.root/name)}

    def plan(self):
        # Isolate editorial prerequisite setup; real visual validator, files,
        # render planning and hash comparison all run without mocking a pass.
        args=argparse.Namespace(root=str(self.root),episode=self.ep,execute=False)
        with patch.object(it,'checked',return_value=(self.store.episode(self.ep),self.store.artifacts(self.ep))),patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):
            return it.render(args)

    def bound(self):
        plan=self.plan();p=self.root/'manual-video.mp4';p.write_bytes(b'synthetic video placeholder for binding-only unit test')
        artifact=self.store.register(self.ep,'video',p)
        binding={'episode_id':self.ep,'artifact':artifact,'inputs':plan}
        atomic_json(self.work/'render.binding.json',binding)
        self.assertEqual(self.store.render_binding_errors(self.ep),[])
        return binding

    def test_actual_project_enables_required_gate(self):
        actual=json.loads((Path(__file__).resolve().parents[1]/'documentation/configuration.json').read_text())
        self.assertIs(actual['quality']['require_visual_manifest'],True)

    def test_manual_video_and_cached_old_pass_cannot_bypass_required_binding(self):
        p=self.root/'manual-video.mp4';p.write_bytes(b'test-only')
        self.store.register(self.ep,'video',p)
        with self.store.db:self.store.db.execute('INSERT INTO validations VALUES(?,?)',(self.ep,json.dumps({'passed':True})))
        with patch.object(Store,'_probe',return_value={'duration':1,'streams':[{'codec_type':'audio'},{'codec_type':'video'}]}):
            result=self.store.validate(self.ep,persist=False)
        self.assertFalse(result['passed'])
        self.assertIn('缺少强制视觉与渲染绑定',result['errors'])

    def test_different_actual_image_is_rejected_by_render_and_final_binding(self):
        binding=self.bound()
        self.scenes['scenes'][0]['image']='different.png'
        atomic_json(self.work/'scenes.json',self.scenes)
        with self.assertRaisesRegex(WorkflowError,'逐项匹配'):self.plan()
        # Even updating technical-manifest hash and plan to the actual different
        # image cannot keep an old visual approval alive.
        binding['inputs']['scene_manifest']['sha256']=digest(self.work/'scenes.json')
        binding['inputs']['scenes'][0].update(image=str(self.root/'different.png'),sha256=digest(self.root/'different.png'))
        atomic_json(self.work/'render.binding.json',binding)
        self.assertTrue(any('视觉审查' in e for e in self.store.render_binding_errors(self.ep)))

    def test_real_reference_pixel_change_invalidates_final_review(self):
        self.bound();self.png('sheet.png',2)
        errors=self.store.render_binding_errors(self.ep)
        self.assertTrue(any('sha256_mismatch' in e for e in errors),errors)

    def test_missing_manifest_cannot_reuse_old_bound_pass(self):
        self.bound();(self.work/'visual-manifest.json').unlink()
        self.assertTrue(any('缺少本集实体视觉合同' in e for e in self.store.render_binding_errors(self.ep)))
