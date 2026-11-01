import contextlib
import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from storyflow.core import Store, WorkflowError, CHECKS, schedule, parse_srt, digest

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.store=Store(self.root)
        self.ep=self.store.create('2026-11-02','消失的钥匙','推理')['id']
    def tearDown(self): self.store.close(); self.tmp.cleanup()
    def file(self,name,value):
        p=self.root/name; p.write_text(json.dumps(value,ensure_ascii=False) if not isinstance(value,str) else value); return p
    def scaffold(self):
        self.store.register(self.ep,'outline',self.file('outline.json',{'premise':'失踪','promise':'解释密室','ending':'钥匙就在门内','beats':['发现','调查','兑现']}))
        self.store.register(self.ep,'structure',self.file('structure.json',{'desire':'找到钥匙','conflict':'密室','stakes':'友情','payoff':'钥匙','ending':'和解','characters':['甲'],'world':'现代','continuity':[]}))
        a=self.store.artifacts(self.ep)
        self.store.outline_review(self.ep,self.file('initial-review.json',{'episode_id':self.ep,'reviewer':'outline-critic','verdict':'pass','outline_sha256':a['outline']['sha256'],'structure_sha256':a['structure']['sha256'],'checks':{k:{'status':'pass','reason':'已核查提纲'} for k in CHECKS}}))
        self.store.register(self.ep,'script',self.file('script.txt','他把钥匙放在门内。'))
        self.review()
    def review(self,reviewer='critic',verdict='pass'):
        a=self.store.artifacts(self.ep)
        v={'episode_id':self.ep,'reviewer':reviewer,'verdict':verdict,'outline_sha256':a['outline']['sha256'],'script_sha256':a['script']['sha256'],'structure_sha256':a['structure']['sha256'],'checks':{k:{'status':'pass','reason':'独立核查通过，见稿中明确依据'} for k in CHECKS}}
        return self.store.record_review(self.ep,self.file('review.json',v))
    def test_schedule_monday_wednesday(self):
        v=schedule('2026-11-02'); self.assertEqual(v['scheduled_at'],'2026-11-04T20:00:00+09:00'); self.assertIsNone(v['actual_public_at'])
    def test_month_year_rollover(self):
        self.assertEqual(schedule('2026-12-31')['scheduled_at'],'2027-01-02T20:00:00+09:00')
        self.assertEqual(schedule('2028-02-28')['release_date'],'2028-03-01')
    def test_duplicate_release(self):
        with self.assertRaises(WorkflowError): self.store.create('2026-11-02','另一个故事','温情',ep='another')
    def test_channel_slug_separate_from_id(self):
        self.store.config['channel']['id']=None; self.assertEqual(self.store.channel(),'story-animation')
    def test_changed_script_invalidates_review_and_assets(self):
        self.scaffold(); p=self.file('sound.mp3','pretend-audio'); self.store.register(self.ep,'audio',p)
        self.assertEqual(self.store.review_errors(self.ep),[])
        self.store.register(self.ep,'script',self.file('script2.txt','换了结局。'))
        self.assertTrue(self.store.review_errors(self.ep)); self.review()
        result=self.store.validate(self.ep)
        self.assertIn('audio 对 script 的输入绑定缺失或过期',result['errors'])
    def test_outline_initial_review_is_separate_and_invalidates(self):
        self.scaffold()
        self.assertEqual(self.store.outline_review_errors(self.ep),[])
        self.assertEqual(self.store.review_errors(self.ep),[])
        self.store.register(self.ep,'outline',self.file('new-outline.json',{'premise':'变了','promise':'新承诺','ending':'新结局','beats':['改']}))
        self.assertTrue(self.store.outline_review_errors(self.ep))
        with self.assertRaises(WorkflowError): self.review()
    def test_structure_change_invalidates_review(self):
        self.scaffold()
        self.store.register(self.ep,'structure',self.file('structure2.json',{'desire':'另一目标'}))
        self.assertTrue(self.store.review_errors(self.ep))
    def test_audio_change_invalidates_downstream(self):
        self.scaffold()
        self.store.register(self.ep,'audio',self.file('a.wav','old'))
        self.store.register(self.ep,'video',self.file('v.mp4','old'))
        self.store.register(self.ep,'subtitles',self.file('s.srt','old'))
        self.store.register(self.ep,'audio',self.file('a2.wav','new'))
        errors=self.store.validate(self.ep)['errors']
        self.assertIn('video 对 audio 的输入绑定缺失或过期',errors)
        self.assertIn('subtitles 对 audio 的输入绑定缺失或过期',errors)
    def test_production_short_script_and_fixture_publication(self):
        self.scaffold()
        self.assertIn('正式故事脚本文字数低于最低门槛',self.store.validate(self.ep)['errors'])
        other=self.store.create('2026-11-03','测试','fixture',verification_only=True)['id']
        ep=self.store.episode(other)
        with self.assertRaises(WorkflowError) as caught:
            self.store.publication(other,self.file('fixture-receipt.json',{'episode_id':other,'channel':ep['channel'],'scheduled_at':ep['scheduled_at'],'status':'accepted','video_id':'testvideo01'}))
        self.assertIn('verification_only',str(caught.exception))
    def test_production_requires_media_review(self):
        self.scaffold()
        self.assertIn('缺少独立最终审片记录',self.store.validate(self.ep)['errors'])
    def test_metrics_requires_public_receipt(self):
        with self.assertRaises(WorkflowError):
            self.store.metric(self.ep,self.file('metrics.json',{'episode_id':self.ep,'actual_public_at':'2026-11-04T20:00:00+09:00','captured_at':'2026-11-08T20:00:00+09:00','metrics':{'views':None}}))
    def test_review_author_must_differ(self):
        self.scaffold()
        with self.assertRaises(WorkflowError): self.review(' WRITER ')
    def test_mutated_copied_artifact_rejected(self):
        self.scaffold(); a=self.store.artifacts(self.ep)['script']; (self.root/a['path']).write_text('changed')
        self.assertTrue(self.store.verify_hashes(self.ep)); self.assertFalse(self.store.validate(self.ep)['passed'])
    def test_structure_search(self):
        self.scaffold(); result=self.store.search('密室')
        self.assertEqual(result['mode'],'lexical'); self.assertTrue(any(r['kind']=='structure' for r in result['results']))
    def test_vectors_binding_and_version_invalidation(self):
        f=self.store.memory_add('plot',self.file('fact.json',{'id':'locked-room','text':'密室钥匙诡计'}))
        self.store.vector_import(self.file('v.json',{'id':f['id'],'model':'external-v1','version':str(f['version']),'source_hash':f['sha256'],'vector':[1,0,0]}))
        q=self.file('query.json',{'model':'external-v1','vector':[0.9,0.1,0]})
        self.assertEqual(self.store.vector_search(q)['results'][0]['id'],'locked-room')
        self.store.memory_add('plot',self.file('fact2.json',{'id':'locked-room','text':'更正：窗户诡计'}))
        self.assertEqual(self.store.vector_search(q)['results'],[])
        with self.assertRaises(WorkflowError): self.store.vector_import(self.root/'v.json')
    def test_vector_zero_or_nan_rejected(self):
        for v in ([0,0],[float('nan'),1]):
            with self.assertRaises(WorkflowError): self.store.vector_search(self.file('q.json',{'model':'x','vector':v}))
    def test_claim_conflict_and_expiry(self):
        self.store.acquire(self.ep,'one')
        with self.assertRaises(WorkflowError): self.store.acquire(self.ep,'two')
        with self.store.db: self.store.db.execute('UPDATE claims SET expires_at=?',('2000-01-01T00:00:00+00:00',))
        self.assertEqual(self.store.acquire(self.ep,'two')['owner'],'two')
        with self.assertRaises(WorkflowError): self.store.release(self.ep,'one')
    def test_budget_and_unknown_metrics(self):
        self.store.cost(self.ep,14,'voice')
        with self.assertRaises(WorkflowError): self.store.cost(self.ep,2,'render')
        with self.store.db: self.store.db.execute('INSERT INTO publications VALUES(?,?)',(self.ep,json.dumps({'status':'public','actual_public_at':'2026-11-04T20:00:00+09:00'})))
        result=self.store.metric(self.ep,self.file('m.json',{'episode_id':self.ep,'actual_public_at':'2026-11-04T20:00:00+09:00','captured_at':'2026-11-05T20:00:00+09:00','metrics':{'views':None}}))
        self.assertIsNone(result['metrics']['views']); self.assertEqual(result['hours_since_public'],24)
    def test_srt_overlap_rejected(self):
        self.assertEqual(len(parse_srt('1\n00:00:00,000 --> 00:00:01,000\n你好\n')),1)
        with self.assertRaises(WorkflowError): parse_srt('1\n00:00:00,000 --> 00:00:02,000\n甲\n\n2\n00:00:01,000 --> 00:00:03,000\n乙')
    def test_invalid_receipt_identity(self):
        with self.assertRaises(WorkflowError) as caught:
            self.store.publication(self.ep,self.file('receipt.json',{'episode_id':self.ep,'channel':'other','scheduled_at':'wrong','video_id':'x','status':'accepted'}))
        self.assertEqual(caught.exception.code,409)
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'ffmpeg required')
    def test_real_media_validate_receipt_idempotency(self):
        self.scaffold()
        self.store.config['channel']['id']='UCtestFixtureChannelId0'
        self.store.config['quality']={'min_script_chars':1,'min_duration_seconds':0.1}
        def ff(args): subprocess.run(['ffmpeg','-v','error','-y',*args],check=True,capture_output=True)
        audio=self.root/'audio.wav'; video=self.root/'video.mp4'; cover=self.root/'cover.png'
        ff(['-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','pcm_s16le',str(audio)])
        ff(['-f','lavfi','-i','color=c=black:s=640x360:d=1','-i',str(audio),'-c:v','libx264','-threads','1','-c:a','aac','-shortest',str(video)])
        ff(['-f','lavfi','-i','color=c=blue:s=640x360','-frames:v','1','-threads','1',str(cover)])
        for kind,path in [('audio',audio),('video',video),('cover',cover),('subtitles',self.file('subs.srt','1\n00:00:00,000 --> 00:00:00,900\n故事\n'))]: self.store.register(self.ep,kind,path)
        ep=self.store.episode(self.ep)
        self.store.register(self.ep,'metadata',self.file('meta.json',{'episode_id':self.ep,'channel':ep['channel'],'scheduled_at':ep['scheduled_at'],'title':'消失的钥匙','description':'原创故事'}))
        arts=self.store.artifacts(self.ep)
        self.store.media_review(self.ep,self.file('media-review.json',{'episode_id':self.ep,'reviewer':'fixture-media-critic','verdict':'pass','hashes':{k:arts[k]['sha256'] for k in ('script','audio','video','subtitles','cover','metadata')},'checks':{k:{'status':'pass','reason':'测试断言，非真实内容质量评估','evidence':'本测试生成1秒蓝底和440Hz音频，字幕0至0.9秒；仅验证审核合同'} for k in ('narration','subtitles','visuals','ending','promise')}}))
        result=self.store.validate(self.ep); self.assertTrue(result['passed'],result['errors'])
        receipt={'episode_id':self.ep,'channel':ep['channel'],'scheduled_at':ep['scheduled_at'],'status':'accepted','channel_id':'UCtestFixtureChannelId0','video_id':'testvideo01'}
        path=self.file('receipt.json',receipt); self.store.publication(self.ep,path,verified=True)
        self.assertTrue(self.store.publication(self.ep,path,verified=True)['idempotent'])
        receipt['video_id']='testvideo02'
        with self.assertRaises(WorkflowError): self.store.publication(self.ep,self.file('conflict.json',receipt),verified=True)
        self.assertEqual(self.store.next(self.ep)['next'],'observe_publication')
    def test_explicit_dependency_order(self):
        self.assertEqual(self.store.next(self.ep)['next'],'produce_outline')
        self.store.register(self.ep,'outline',self.file('outline.json',{'premise':'失踪','promise':'解释密室','ending':'门内','beats':['发现','调查']}))
        self.assertEqual(self.store.next(self.ep)['next'],'produce_structure')
        self.store.register(self.ep,'structure',self.file('structure.json',{'desire':'找到','conflict':'密室','stakes':'友情','payoff':'钥匙','ending':'和解','characters':[],'world':'现代','continuity':[]}))
        self.assertEqual(self.store.next(self.ep)['next'],'independent_outline_review')
        a=self.store.artifacts(self.ep)
        self.store.outline_review(self.ep,self.file('initial.json',{'episode_id':self.ep,'reviewer':'editor','verdict':'pass','outline_sha256':a['outline']['sha256'],'structure_sha256':a['structure']['sha256'],'checks':{k:{'status':'pass','reason':'逐项审查'} for k in CHECKS}}))
        self.assertEqual(self.store.next(self.ep)['next'],'produce_script')
        self.store.register(self.ep,'script',self.file('script.txt','钥匙在门内。'))
        self.assertEqual(self.store.next(self.ep)['next'],'independent_review')
        self.review(); self.assertEqual(self.store.next(self.ep)['next'],'produce_audio')
    def test_unverified_manual_publication_rejected(self):
        episode=self.store.episode(self.ep)
        receipt={'episode_id':self.ep,'channel':episode['channel'],'scheduled_at':episode['scheduled_at'],'status':'public','video_id':'testvideo01','actual_public_at':'2000-01-01T00:00:00+09:00'}
        with self.assertRaises(WorkflowError) as e: self.store.publication(self.ep,self.file('unverified.json',receipt))
        self.assertIn('平台回读',str(e.exception))
        self.assertIsNone(self.store.db.execute('SELECT * FROM publications').fetchone())

if __name__=='__main__': unittest.main()
