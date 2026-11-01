import concurrent.futures
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from storyflow.core import Store, WorkflowError, atomic_json, digest
from storyflow.sound_library import get_sound,lookup,normalize_name,PREVIEW_SCOPE,validate_cue_sheet

class SoundLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        (self.root/'config').mkdir(); (self.root/'config/sound-library-approval.json').write_text('authorized sound preview')
        self.calls=[]
        self.wav=self.root/'fixture.mp3'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=120:duration=1','-y',str(self.wav)],check=True)
    def tearDown(self): self.temp.cleanup()
    def runner(self,argv,**kwargs):
        self.calls.append(argv); shutil.copyfile(self.wav,argv[argv.index('-o')+1]); return subprocess.CompletedProcess(argv,0,'sfx successful','')
    def generate(self,name='Test Beat',**kwargs):
        return get_sound(self.root,name,prompt='original low drum',scope=PREVIEW_SCOPE,execute=True,confirm_cost=True,estimated_usd=.10,runner=self.runner,**kwargs)
    def test_names(self):
        self.assertEqual(normalize_name(' Car--HORN  v2 '),'car_horn_v2')
        for x in ('../x','/tmp/a','','---','中文','a.b','a\\b'):
            with self.assertRaises(WorkflowError): normalize_name(x)
    def test_plan_and_cache_integrity(self):
        self.assertEqual(get_sound(self.root,'Beat',prompt='x')['post_attempts'],0)
        first=self.generate(); hit=get_sound(self.root,'TEST-beat')
        self.assertEqual(first['audio_sha256'],hit['audio_sha256']); self.assertEqual(len(self.calls),1)
        (self.root/'sound-effects/test_beat/metadata.json').write_text('{}')
        with self.assertRaises(WorkflowError): self.generate()
        self.assertEqual(len(self.calls),1)
    def test_audio_corruption(self):
        self.generate(); (self.root/'sound-effects/test_beat/original.mp3').write_bytes(b'corrupt')
        with self.assertRaises(WorkflowError): self.generate()
        self.assertEqual(len(self.calls),1)
    def test_concurrent_name_one_post(self):
        # Initialize Store before racing to avoid unrelated database bootstrap concurrency.
        Store(self.root).close()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            rows=list(pool.map(lambda name:self.generate(name),['same beat','SAME-BEAT']))
        self.assertEqual(len(self.calls),1); self.assertEqual(rows[0]['audio_sha256'],rows[1]['audio_sha256'])
    def test_unknown_no_resend(self):
        def failed(*a,**kw): self.calls.append(a); raise subprocess.TimeoutExpired('el',1)
        with self.assertRaises(subprocess.TimeoutExpired):
            get_sound(self.root,'lost',prompt='x',scope=PREVIEW_SCOPE,execute=True,confirm_cost=True,estimated_usd=.1,runner=failed)
        with self.assertRaises(WorkflowError): self.generate('lost')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(json.loads((self.root/'sound-effects/.requests/lost.json').read_text())['status'],'unknown')
    def test_scope_budget_and_no_episode_costs(self):
        for n in ('a','b','c'): self.generate(n)
        with self.assertRaises(WorkflowError): self.generate('d')
        self.assertEqual(len(self.calls),3)
        store=Store(self.root)
        self.assertEqual(store.db.execute('SELECT COUNT(*) FROM costs WHERE episode_id IS NULL').fetchone()[0],3);store.close()
    def test_month_budget_rejects_before_post(self):
        store=Store(self.root)
        store.db.execute("INSERT INTO costs(episode_id,amount,category,created_at) VALUES(NULL,200,'existing',datetime('now'))");store.db.commit();store.close()
        with self.assertRaises(WorkflowError): self.generate('over_month')
        self.assertFalse(self.calls)
    def test_unapproved_scope_rejects_before_post(self):
        with self.assertRaises(WorkflowError): get_sound(self.root,'unapproved',prompt='x',scope='another',execute=True,confirm_cost=True,estimated_usd=.1,runner=self.runner)
        self.assertFalse(self.calls)
    def test_missing_metadata_never_regenerates(self):
        self.generate(); (self.root/'sound-effects/test_beat/metadata.sha256.json').unlink()
        with self.assertRaises(WorkflowError): self.generate()
        self.assertEqual(len(self.calls),1)
    def test_bgm_existing_entry(self):
        self.generate('music',category='bgm',seconds=3)
        self.assertEqual(self.calls[0][2],'music');self.assertIn('music_v1',self.calls[0])
    def test_symlink_rejected(self):
        (self.root/'sound-effects').symlink_to(self.root/'config',target_is_directory=True)
        with self.assertRaises(WorkflowError): self.generate()
    def test_bad_estimate_prepost(self):
        with self.assertRaises(WorkflowError): get_sound(self.root,'no',prompt='x',scope=PREVIEW_SCOPE,execute=True,confirm_cost=True,estimated_usd=float('nan'),runner=self.runner)
        self.assertFalse(self.calls)
    def test_cue_requires_hash_and_dry_clock(self):
        a=self.generate()
        cue={'scene_id':'chapter_2','name':'test_beat','source_audio_in':0,'source_audio_out':1,'source_timeline_in':360,'final_timeline_in':361,'gain_db':-12,'fade_in_seconds':.01,'fade_out_seconds':.15,'ducking':{'sidechain':'dry_narration','attenuation_db':-8,'attack_seconds':.02,'release_seconds':.2},'audio_sha256':a['audio_sha256']}
        p=self.root/'cues.json';atomic_json(p,{'schema':'qy.sound.cues.v1','alignment_source':'dry_narration','cues':[cue]})
        self.assertTrue(validate_cue_sheet(self.root,p)['ok'])
        cue['audio_sha256']='f'*64;atomic_json(p,{'schema':'qy.sound.cues.v1','alignment_source':'dry_narration','cues':[cue]})
        with self.assertRaises(WorkflowError): validate_cue_sheet(self.root,p)
