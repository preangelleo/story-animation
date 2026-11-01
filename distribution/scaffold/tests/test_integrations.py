import argparse
import datetime as dt
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from storyflow.core import Store, WorkflowError, CHECKS, atomic_json, digest, read_json
from storyflow import integrations as it

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.store=Store(self.root)
        self.ep=self.store.create('2030-11-02','消失的钥匙','推理')['id']
        self.file('documentation/configuration.json',{'channel':{'id':'UC'+'a'*22,'slug':'story-animation','youtube_profile':'story-animation'},'render':{'width':64,'height':64}})
        self.store.config['channel'].update({'id':'UC'+'a'*22,'slug':'story-animation','youtube_profile':'story-animation'})
        self.store.register(self.ep,'outline',self.file('outline.json',{'premise':'失踪','promise':'密室','ending':'门内','beats':['找','见']}))
        self.structure()
        self.initial_review()
        self.store.register(self.ep,'script',self.file('script.txt','钥匙。'))
        arts=self.store.artifacts(self.ep)
        self.store.record_review(self.ep,self.file('review.json',{'episode_id':self.ep,'reviewer':'critic','verdict':'pass','outline_sha256':arts['outline']['sha256'],'script_sha256':arts['script']['sha256'],'structure_sha256':arts['structure']['sha256'],'checks':{k:{'status':'pass','reason':'独立证据'} for k in CHECKS}}))
    def tearDown(self): self.store.close(); self.tmp.cleanup()
    def file(self,name,value):
        p=self.root/name; p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)); return p
    def structure(self, scenes=None):
        return self.store.register(self.ep,'structure',self.file('structure.json',{'desire':'找到','conflict':'密室','stakes':'友谊','payoff':'钥匙','ending':'和解','characters':['甲'],'world':'现代','continuity':[],'scenes':scenes or []}))
    def initial_review(self):
        arts=self.store.artifacts(self.ep)
        return self.store.outline_review(self.ep,self.file('outline-review.json',{'episode_id':self.ep,'reviewer':'outline-critic','verdict':'pass','outline_sha256':arts['outline']['sha256'],'structure_sha256':arts['structure']['sha256'],'checks':{k:{'status':'pass','reason':'提纲独立核查'} for k in CHECKS}}))
    def args(self,**kw): return argparse.Namespace(root=str(self.root),episode=self.ep,execute=False,confirm_cost=False,estimated_usd=None,**kw)
    def readops(self):
        it.operations(self.store); return self.store.db.execute('SELECT * FROM external_operations').fetchall()
    def test_fish_voice_guard_prevents_elevenlabs_call(self):
        self.store.config['voice']={'provider':'fish_audio','voice':'other-provider-fixture','model':'drama-3'}
        with patch.object(it,'run') as command:
            with self.assertRaisesRegex(WorkflowError,'禁止把其他供应商voice id'):
                it._voice(self.store,self.args())
            command.assert_not_called()
        self.assertEqual(self.readops(),[])
    def test_voice_plan_never_executes(self):
        with patch.object(it,'run') as command:
            plan=it.voice(self.args()); self.assertEqual(plan['mode'],'plan'); command.assert_not_called()
        self.assertEqual(self.readops(),[])
    def test_voice_revision_uses_new_version_and_no_double_charge_on_replay(self):
        a=self.args(); a.execute=True; a.confirm_cost=True; a.estimated_usd=1
        outputs=[]
        def command(argv,**kw):
            path=Path(argv[-1]); path.write_bytes(b'audio-'+str(len(outputs)).encode()); outputs.append(path); return 'ok'
        with patch.object(it,'run',side_effect=command),patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):
            it.voice(a); replay=it.voice(a); self.assertTrue(replay['idempotent'])
            self.store.register(self.ep,'script',self.file('revision.txt','门钥匙。'))
            arts=self.store.artifacts(self.ep)
            self.store.record_review(self.ep,self.file('revision-review.json',{'episode_id':self.ep,'reviewer':'critic','verdict':'pass','outline_sha256':arts['outline']['sha256'],'script_sha256':arts['script']['sha256'],'structure_sha256':arts['structure']['sha256'],'checks':{k:{'status':'pass','reason':'独立重审'} for k in CHECKS}}))
            it.voice(a)
        self.assertEqual(len(outputs),2); self.assertNotEqual(outputs[0],outputs[1]); self.assertTrue(all(p.exists() for p in outputs))
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],2)
    def test_paid_needs_confirmation(self):
        a=self.args(); a.execute=True
        with patch.object(it,'run') as command, self.assertRaises(WorkflowError): it.voice(a)
        command.assert_not_called(); self.assertEqual(self.readops(),[])
    def test_review_blocks_voice(self):
        self.store.register(self.ep,'script',self.file('change.txt','换稿。'))
        with self.assertRaises(WorkflowError): it.voice(self.args())
    def test_voice_failure_reserves_and_never_retries(self):
        a=self.args(); a.execute=True; a.confirm_cost=True; a.estimated_usd=1
        with patch.object(it,'run',side_effect=WorkflowError('断网')) as cmd:
            with self.assertRaises(WorkflowError): it.voice(a)
            with self.assertRaises(WorkflowError): it.voice(a)
            self.assertEqual(cmd.call_count,1)
        rows=self.readops(); self.assertEqual(rows[0]['status'],'unknown')
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],1)
    def test_reservation_budget_and_race(self):
        key,_=it.begin(self.store,self.ep,'voice',{'s':'one'},1)
        other=Store(self.root)
        try:
            with self.assertRaises(WorkflowError): it.begin(other,self.ep,'voice',{'s':'two'},1)
            with self.assertRaises(WorkflowError): it.begin(other,self.ep,'align',{'s':'two'},1000)
        finally: other.close()
        self.assertEqual(len(self.readops()),1)
    def chunk_voice_args(self):
        self.prepare_alignment_recovery('钥匙。门锁。')
        # 上述helper只提供已审新稿和测试音频，清除其测试align账本及预留。
        with self.store.db:
            self.store.db.execute('DELETE FROM external_operations'); self.store.db.execute('DELETE FROM costs')
        args=self.args(); args.take=2; args.chunk_chars=3; args.resume=False
        args.execute=True; args.confirm_cost=True; args.estimated_usd=2
        return args
    def chunk_mock(self,argv,**kw):
        Path(argv[-1]).write_bytes(('audio:'+argv[2] if argv[0]=='el' else 'pcm audio').encode())
        return ''
    def chunk_probe(self,path,kind):
        return {'duration':2 if Path(path).name=='voice.wav' else 1,'streams':[{'codec_type':'audio'}]}
    def test_voice_chunk_split_preserves_exact_text_and_bounds(self):
        text='第一句。\n第二句！\n最后一句。\n'
        chunks=it.split_voice_text(text,8)
        self.assertEqual(''.join(chunks),text); self.assertTrue(all(len(c)<=8 for c in chunks))
        for limit in (0,1201,True):
            with self.assertRaises(WorkflowError): it.split_voice_text(text,limit)
        with self.assertRaises(WorkflowError): it.split_voice_text('没有句号的长长句子',3)
    def test_explicit_voice_take_chunks_once_and_preserves_old_takes(self):
        args=self.chunk_voice_args()
        with patch.object(it,'run',side_effect=self.chunk_mock) as command,patch.object(Store,'_probe',side_effect=self.chunk_probe):
            first=it.voice(args); replay=it.voice(args)
            self.assertTrue(replay['idempotent'])
            self.assertEqual(sum(c.args[0][0]=='el' for c in command.call_args_list),2)
            self.assertEqual(''.join(c.args[0][2] for c in command.call_args_list if c.args[0][0]=='el'),'钥匙。门锁。')
            args.take=3; second=it.voice(args)
        self.assertNotEqual(it.fingerprint(first['inputs']),it.fingerprint(second['inputs']))
        self.assertEqual(first['take'],2); self.assertEqual(second['take'],3)
        self.assertTrue((self.root/first['artifact']['path']).exists())
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],4)
        self.assertEqual(len(first['chunks']),2)
        self.assertEqual(read_json(it.folder(self.store,self.ep)/'voice.binding.json')['take'],3)
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'需要本机ffmpeg')
    def test_real_chunk_pcm_concat_keeps_duration(self):
        args=self.chunk_voice_args(); source=self.root/'source.mp3'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.4','-c:a','libmp3lame',str(source)],check=True)
        original_run=it.run
        def command(argv,**kw):
            if argv[0]=='el': shutil.copyfile(source,argv[-1]); return ''
            return original_run(argv,**kw)
        with patch.object(it,'run',side_effect=command): result=it.voice(args)
        audio=self.root/result['artifact']['path']; probe=self.store._probe(audio,'audio')
        self.assertLess(abs(probe['duration']-0.8),0.05)
        self.assertEqual(probe['streams'][0]['codec_name'],'pcm_s16le')
    def test_voice_take_resume_local_concat_reuses_done_chunks(self):
        args=self.chunk_voice_args(); failed=[False]
        def command(argv,**kw):
            if argv[0]=='ffmpeg' and 'concat' in argv and not failed[0]: failed[0]=True; raise WorkflowError('local disk')
            return self.chunk_mock(argv,**kw)
        with patch.object(it,'run',side_effect=command) as external,patch.object(Store,'_probe',side_effect=self.chunk_probe):
            with self.assertRaises(WorkflowError): it.voice(args)
            args.resume=True; result=it.voice(args)
        self.assertEqual(result['status'],'done')
        self.assertEqual(sum(c.args[0][0]=='el' for c in external.call_args_list),2)
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],2)
    def test_running_take_all_done_resumes_only_local_under_lock(self):
        args=self.chunk_voice_args()
        def failed_concat(argv,**kw):
            if argv[0]=='ffmpeg': raise WorkflowError('interrupted local concat')
            return self.chunk_mock(argv,**kw)
        with patch.object(it,'run',side_effect=failed_concat),patch.object(Store,'_probe',side_effect=self.chunk_probe):
            with self.assertRaises(WorkflowError): it.voice(args)
        with self.store.db: self.store.db.execute("UPDATE external_operations SET status='running' WHERE kind='voice-take-2'")
        args.resume=True
        with patch.object(it,'run',side_effect=self.chunk_mock) as external,patch.object(Store,'_probe',side_effect=self.chunk_probe): result=it.voice(args)
        self.assertEqual(result['status'],'done')
        self.assertTrue(all(c.args[0][0]=='ffmpeg' for c in external.call_args_list))
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],2)
    def test_running_take_missing_chunk_and_legacy_lock_contract_refuse(self):
        args=self.chunk_voice_args()
        def fail_second(argv,**kw):
            if argv[0]=='el' and argv[2]=='门锁。': raise WorkflowError('supplier unknown')
            return self.chunk_mock(argv,**kw)
        with patch.object(it,'run',side_effect=fail_second),patch.object(Store,'_probe',side_effect=self.chunk_probe):
            with self.assertRaises(WorkflowError): it.voice(args)
        args.resume=True
        with self.store.db: self.store.db.execute("UPDATE external_operations SET status='running' WHERE kind='voice-take-2'")
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): it.voice(args)
        external.assert_not_called()
        parent=self.store.db.execute("SELECT data FROM external_operations WHERE kind='voice-take-2'").fetchone()
        data=json.loads(parent['data']); data.pop('exclusive_lock',None)
        with self.store.db: self.store.db.execute("UPDATE external_operations SET data=? WHERE kind='voice-take-2'",(json.dumps(data),))
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): it.voice(args)
        external.assert_not_called()
    def test_voice_episode_lock_blocks_concurrent_executor(self):
        import fcntl
        args=self.chunk_voice_args()
        with it.voice_lock_path(self.store,self.ep).open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(it,'run') as external,self.assertRaises(WorkflowError): it.voice(args)
            external.assert_not_called()
    def test_voice_take_unknown_chunk_never_resends_or_charges_again(self):
        args=self.chunk_voice_args()
        def command(argv,**kw):
            if argv[0]=='el' and argv[2]=='门锁。': raise WorkflowError('supplier unknown')
            return self.chunk_mock(argv,**kw)
        with patch.object(it,'run',side_effect=command) as external,patch.object(Store,'_probe',side_effect=self.chunk_probe):
            with self.assertRaises(WorkflowError): it.voice(args)
            args.resume=True
            with self.assertRaises(WorkflowError): it.voice(args)
        self.assertEqual(sum(c.args[0][0]=='el' for c in external.call_args_list),2)
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],2)
    def test_new_take_align_isolated_from_old_unknown_only_with_done_voice(self):
        args=self.chunk_voice_args()
        with patch.object(it,'run',side_effect=self.chunk_mock),patch.object(Store,'_probe',side_effect=self.chunk_probe): voice=it.voice(args)
        oldkey,_=it.begin(self.store,self.ep,'align',{'script_sha256':'old','audio_sha256':'bad'},0.5)
        it.finish(self.store,self.ep,'align',oldkey,'unknown',{'inputs':{'script_sha256':'old','audio_sha256':'bad'}})
        self.prepare_asr_audit()
        alignargs=self.args(); alignargs.execute=True; alignargs.confirm_cost=True; alignargs.estimated_usd=0.5
        def command(argv,**kw):
            atomic_json(Path(argv[-1]),{'characters':[{'text':c,'start':i*0.2,'end':(i+1)*0.2} for i,c in enumerate('钥匙。门锁。')]})
            return ''
        with patch.object(it,'run',side_effect=command) as external,patch.object(Store,'_probe',return_value={'duration':2,'streams':[]}): result=it.align(alignargs)
        self.assertEqual(result['status'],'done'); self.assertEqual(external.call_count,1)
        rows={r['kind']:r['status'] for r in self.readops()}
        self.assertEqual(rows['align'],'unknown'); self.assertEqual(rows['align-take-2'],'done')
    def prepare_patch_source(self):
        self.prepare_alignment_recovery('钥匙。门锁。')
        arts=self.store.artifacts(self.ep); pair={'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']}; work=it.folder(self.store,self.ep)
        atomic_json(work/'voice.binding.json',{'inputs':{'script_sha256':pair['script_sha256'],'voice':'user-voice','model':'eleven_multilingual_v2','language':'zh'},'audio_sha256':pair['audio_sha256']})
        alignment={'characters':[{'text':c,'start':i*0.2,'end':(i+1)*0.2} for i,c in enumerate('钥匙。门锁。')]}
        source=work/'align'/it.fingerprint(pair)/'alignment.json'; atomic_json(source,alignment)
        sub=self.store.register(self.ep,'subtitles',self.file('patch-source.srt','1\n00:00:00,000 --> 00:00:01,200\n钥匙。门锁。\n'))
        atomic_json(work/'alignment.binding.json',{'inputs':pair,'alignment_sha256':digest(source),'artifact':sub})
        from storyflow.cli import parser
        return parser().parse_args(['--root',str(self.root),'voice-patch',self.ep,'--patch-id','pronunciation','--start','0','--end','3','--generate','--execute','--confirm-cost','--estimated-usd','0.5'])
    def patch_probe(self,path,kind):
        return {'duration':2.4 if Path(path).name=='patched.wav' else 1 if Path(path).name=='patch.mp3' else 2,'streams':[{'codec_type':'audio'}]}
    def patch_command(self,argv,**kw):
        Path(argv[-1]).write_bytes(b'patch' if argv[0]=='el' else b'patched output'); return ''
    def patch_review_file(self,result,text='钥匙。'):
        checks=[]
        for engine in ('qwen','whisper'):
            provider='qwen' if engine=='qwen' else 'faster-whisper'; model='Qwen/Qwen3-ASR-1.7B' if engine=='qwen' else 'faster-whisper-medium'
            raw={'text':text,'language':'Chinese'} if engine=='qwen' else {'text':text,'model':model,'device':'cpu-int8','audio_sha256':result['audio_sha256'],'segments':[{'text':text,'start':0,'end':0.9}]}
            response=self.file(engine+'.json',raw)
            receipt={'provider':provider,'model':model,'audio_sha256':result['audio_sha256'],'request':{'audio_path':result['output'],'audio_sha256':result['audio_sha256'],'model':model},'raw_response_path':str(response),'raw_response_sha256':digest(response)}
            if engine=='qwen':
                health=self.file('qwen-health.json',{'status':'ok','model':model})
                receipt.update(model_identity_path=str(health),model_identity_sha256=digest(health))
            receipt_file=self.file(engine+'-receipt.json',receipt)
            checks.append({'engine':engine,'response_path':str(response),'response_sha256':digest(response),'receipt_path':str(receipt_file),'receipt_sha256':digest(receipt_file),'verdict':'pass','reason':'两路独立识别原句'})
        return self.file('patch-review.json',{'audio_sha256':result['audio_sha256'],'expected_text_sha256':result['inputs']['text_sha256'],'checks':checks})
    def test_patch_generate_apply_requires_two_asr_and_keeps_original(self):
        args=self.prepare_patch_source(); original=self.store.artifacts(self.ep)['audio']
        with patch.object(it,'run',side_effect=self.patch_command) as external,patch.object(Store,'_probe',side_effect=self.patch_probe):
            generated=it.voice_patch(args); replay=it.voice_patch(args)
            self.assertTrue(replay['idempotent']); self.assertEqual(external.call_count,1)
            self.assertEqual(self.store.artifacts(self.ep)['audio']['sha256'],original['sha256'])
            args.apply=True; args.review_file=str(self.patch_review_file(generated)); args.resume=False
            applied=it.voice_patch(args); again=it.voice_patch(args)
            self.assertEqual(sum(c.args[0][0]=='el' for c in external.call_args_list),1)
        self.assertTrue(again['idempotent']); self.assertEqual(applied['source_audio_sha256'],original['sha256'])
        self.assertNotEqual(applied['audio_sha256'],original['sha256']); self.assertTrue((self.root/original['path']).exists())
        binding=read_json(it.folder(self.store,self.ep)/'voice.binding.json')
        pair={'script_sha256':generated['inputs']['script_sha256'],'audio_sha256':applied['audio_sha256']}
        self.assertTrue(it.voice_binding_kind(self.store,self.ep,binding,pair).startswith('align-patch-'))
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],1.5)  # helper1 + patch.5，apply无收费
    def test_patch_mismatched_range_audio_and_patch_rejected(self):
        args=self.prepare_patch_source()
        with patch.object(it,'run',side_effect=self.patch_command),patch.object(Store,'_probe',side_effect=self.patch_probe): generated=it.voice_patch(args)
        args.end=2
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): it.voice_patch(args)
        external.assert_not_called()
        args.end=3; args.apply=True; args.resume=False; args.review_file=str(self.patch_review_file(generated))
        args.start=1
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): it.voice_patch(args)
        external.assert_not_called(); args.start=0
        patchfile=Path(generated['output']); patchfile.write_bytes(b'tampered')
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): it.voice_patch(args)
        external.assert_not_called(); patchfile.write_bytes(b'patch')
        self.store.register(self.ep,'audio',self.file('changed-source.wav','changed source'))
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): it.voice_patch(args)
        external.assert_not_called()
    def test_patch_same_response_relabelled_as_second_engine_rejected(self):
        args=self.prepare_patch_source()
        with patch.object(it,'run',side_effect=self.patch_command),patch.object(Store,'_probe',side_effect=self.patch_probe): generated=it.voice_patch(args)
        review=self.patch_review_file(generated); value=read_json(review)
        value['checks'][1]={**value['checks'][0],'engine':'whisper'}; atomic_json(review,value)
        args.apply=True; args.resume=False; args.review_file=str(review)
        with patch.object(it,'run',side_effect=self.patch_command) as external,patch.object(Store,'_probe',side_effect=self.patch_probe),self.assertRaises(WorkflowError): it.voice_patch(args)
        external.assert_not_called()
    def test_patch_wrong_or_duplicate_asr_rejected_without_state_swap(self):
        args=self.prepare_patch_source(); source=self.store.artifacts(self.ep)['audio']['sha256']
        with patch.object(it,'run',side_effect=self.patch_command),patch.object(Store,'_probe',side_effect=self.patch_probe): generated=it.voice_patch(args)
        args.apply=True; args.resume=False; review=self.patch_review_file(generated,text='错读。'); args.review_file=str(review)
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): it.voice_patch(args)
        external.assert_not_called()
        review=self.patch_review_file(generated); value=read_json(review); value['checks'][1]['engine']='qwen'; atomic_json(review,value)
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): it.voice_patch(args)
        external.assert_not_called(); self.assertEqual(self.store.artifacts(self.ep)['audio']['sha256'],source)
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'需要本机ffmpeg')
    def test_real_patch_splice_uses_timestamps_without_retiming(self):
        args=self.prepare_patch_source(); work=it.folder(self.store,self.ep)
        source=self.root/'source.wav'; short=self.root/'short.mp3'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=2','-c:a','pcm_s16le',str(source)],check=True)
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=880:duration=0.4','-c:a','libmp3lame',str(short)],check=True)
        old_alignment=read_json(work/'alignment.binding.json')
        old_json=read_json(work/'align'/it.fingerprint(old_alignment['inputs'])/'alignment.json')
        self.store.register(self.ep,'audio',source); arts=self.store.artifacts(self.ep)
        pair={'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']}
        voice=read_json(work/'voice.binding.json'); voice['audio_sha256']=pair['audio_sha256']; atomic_json(work/'voice.binding.json',voice)
        new_json=work/'align'/it.fingerprint(pair)/'alignment.json'; atomic_json(new_json,old_json)
        sub=self.store.register(self.ep,'subtitles',self.root/arts['subtitles']['path'])
        atomic_json(work/'alignment.binding.json',{'inputs':pair,'alignment_sha256':digest(new_json),'artifact':sub})
        real_run=it.run
        def command(argv,**kw):
            if argv[0]=='el': shutil.copyfile(short,argv[-1]); return ''
            return real_run(argv,**kw)
        with patch.object(it,'run',side_effect=command):
            generated=it.voice_patch(args); args.apply=True; args.review_file=str(self.patch_review_file(generated)); args.resume=False
            result=it.voice_patch(args)
        probe=self.store._probe(self.root/result['artifact']['path'],'audio')
        self.assertLess(abs(probe['duration']-1.8),0.05)
        self.assertEqual(probe['streams'][0]['codec_name'],'pcm_s16le')
        self.assertEqual(result['cut'],[0,0.6000000000000001])
        self.assertLess(abs(self.store._probe(source,'audio')['duration']-2),0.01)
    def test_patch_speech_text_only_whitespace_changes_allowed(self):
        args=self.prepare_patch_source(); args.speech_text_file=str(self.file('speech.txt','钥 匙。'))
        with patch.object(it,'run',side_effect=self.patch_command) as external,patch.object(Store,'_probe',side_effect=self.patch_probe): result=it.voice_patch(args)
        self.assertEqual(external.call_args.args[0][2],'钥 匙。')
        self.assertEqual(result['inputs']['spoken_text_sha256'],it.hashlib.sha256('钥 匙。'.encode()).hexdigest())
        self.assertNotEqual(result['inputs']['spoken_text_sha256'],result['inputs']['text_sha256'])
        args.apply=True; args.resume=False; args.review_file=str(self.patch_review_file(result))
        with patch.object(it,'run',side_effect=self.patch_command),patch.object(Store,'_probe',side_effect=self.patch_probe): self.assertEqual(it.voice_patch(args)['status'],'done')
    def test_patch_speech_text_nonwhitespace_change_rejected_before_cost(self):
        args=self.prepare_patch_source()
        for spoken in ('钥匙！','钥匙。<break/>','钥匙。 [pause]','钥。','门锁。'):
            args.speech_text_file=str(self.file('speech.txt',spoken))
            with patch.object(it,'run') as external,patch.object(Store,'_probe',side_effect=self.patch_probe),self.assertRaises(WorkflowError): it.voice_patch(args)
            external.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],1)
    def test_patch_asr_receipts_require_audio_model_and_distinct_response_hash(self):
        args=self.prepare_patch_source()
        with patch.object(it,'run',side_effect=self.patch_command),patch.object(Store,'_probe',side_effect=self.patch_probe): generated=it.voice_patch(args)
        args.apply=True; args.resume=False
        for failure in ('audio','model','same_hash'):
            review=self.patch_review_file(generated); value=read_json(review); check=value['checks'][1]
            if failure=='same_hash':
                cloned=self.file('cloned-response.json',read_json(value['checks'][0]['response_path']))
                check.update(response_path=str(cloned),response_sha256=digest(cloned))
            else:
                receipt_path=check['receipt_path']; receipt=read_json(receipt_path)
                receipt['audio_sha256' if failure=='audio' else 'model']='incorrect'
                atomic_json(receipt_path,receipt); check['receipt_sha256']=digest(receipt_path)
            atomic_json(review,value); args.review_file=str(review)
            with patch.object(it,'run') as external,patch.object(Store,'_probe',side_effect=self.patch_probe),self.assertRaises(WorkflowError): it.voice_patch(args)
            external.assert_not_called()
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'需要本机ffmpeg')
    def test_revision_real_splice_immutable_source_silence_dual_seam(self):
        args=self.prepare_patch_source(); work=it.folder(self.store,self.ep)
        source=self.root/'source-gap.wav'; short=self.root/'short-gap.mp3'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.6','-f','lavfi','-i','anullsrc=r=44100:cl=mono:d=0.8','-f','lavfi','-i','sine=frequency=440:duration=1.6','-filter_complex','[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]','-map','[a]','-c:a','pcm_s16le',str(source)],check=True)
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=880:duration=0.4','-c:a','libmp3lame',str(short)],check=True)
        audio_art=self.store.register(self.ep,'audio',source); arts=self.store.artifacts(self.ep)
        pair={'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']}
        voice=read_json(work/'voice.binding.json'); voice.update(audio_sha256=pair['audio_sha256'],artifact=audio_art); atomic_json(work/'voice.binding.json',voice)
        chars=[{'text':c,'start':i*0.2 if i<3 else 1.4+(i-3)*0.2,'end':(i+1)*0.2 if i<3 else 1.6+(i-3)*0.2} for i,c in enumerate('钥匙。门锁。')]
        src_json=work/'align'/it.fingerprint(pair)/'alignment.json'; atomic_json(src_json,{'characters':chars})
        sub=self.store.register(self.ep,'subtitles',self.root/arts['subtitles']['path'])
        atomic_json(work/'alignment.binding.json',{'inputs':pair,'alignment_sha256':digest(src_json),'artifact':sub})
        real_run=it.run
        def command(argv,**kw):
            if argv[0]=='el': shutil.copyfile(short,argv[-1]); return ''
            return real_run(argv,**kw)
        with patch.object(it,'run',side_effect=command): generated=it.voice_patch(args)
        args.apply=True; args.resume=False; args.review_file=str(self.patch_review_file(generated))
        it.voice_patch(args); old_apply=self.store.artifacts(self.ep)['audio']['sha256']
        candidate=self.root/'candidate.wav'; it.splice_audio(source,Path(generated['output']),0,1.2,3,candidate)
        asr={'audio_sha256':digest(candidate),'output':str(candidate),'duration':2.2,'inputs':{'text_sha256':it.hashlib.sha256('钥匙。门锁。'.encode()).hexdigest()}}
        review=self.patch_review_file(asr,text='钥匙。门锁。')
        log=self.file('silence.log','silence_start: 0.6\nsilence_end: 1.4\n')
        proof={'cut_end':1.2,'candidate_path':str(candidate),'candidate_sha256':digest(candidate),'seam_path':str(candidate),'seam_sha256':digest(candidate),'seam_start':0,'silence':{'source_audio_sha256':digest(source),'window_start':0,'log_path':str(log),'log_sha256':digest(log),'noise_db':-35},'review_file':str(review),'review_sha256':digest(review)}
        args.revise=True; args.apply=False; args.revision_id='seam2'; args.revision_file=str(self.file('revision.json',proof))
        for bad_cut in (0.6,1.4,1.5):
            atomic_json(args.revision_file,{**proof,'cut_end':bad_cut})
            with self.assertRaises(WorkflowError): it.voice_patch(args)
        atomic_json(args.revision_file,proof)
        with patch.object(it,'run',wraps=real_run) as external:
            result=it.voice_patch(args); replay=it.voice_patch(args)
        self.assertTrue(replay['idempotent']); self.assertEqual(result['audio_sha256'],digest(candidate))
        self.assertNotEqual(result['audio_sha256'],old_apply); self.assertEqual(result['paid_calls'],0)
        self.assertTrue(all(call.args[0][0]=='ffmpeg' for call in external.call_args_list))
        binding=read_json(work/'voice.binding.json'); pair['audio_sha256']=result['audio_sha256']
        self.assertTrue(it.voice_binding_kind(self.store,self.ep,binding,pair).startswith('align-patch-revision-'))
        self.assertLess(abs(self.store._probe(self.root/result['artifact']['path'],'audio')['duration']-2.2),0.05)
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],1.5)
    def test_seam_asr_context_requires_contiguous_unique_next_sentence(self):
        args=self.prepare_patch_source()
        with patch.object(it,'run',side_effect=self.patch_command),patch.object(Store,'_probe',side_effect=self.patch_probe): generated=it.voice_patch(args)
        expected='钥匙。门锁。'; generated['inputs']['text_sha256']=it.hashlib.sha256(expected.encode()).hexdigest()
        for actual in ('钥匙。匙。门锁。','钥匙。','钥匙。门锁。门锁。'):
            review=self.patch_review_file(generated,text=actual)
            with self.assertRaises(WorkflowError): it.patch_review(self.store,review,generated,expected,context_parts=('钥匙。','门锁。'))
    def test_patch_unknown_never_resends_or_charges_again(self):
        args=self.prepare_patch_source()
        with patch.object(it,'run',side_effect=WorkflowError('unknown response')) as external,patch.object(Store,'_probe',side_effect=self.patch_probe):
            with self.assertRaises(WorkflowError): it.voice_patch(args)
            with self.assertRaises(WorkflowError): it.voice_patch(args)
        self.assertEqual(external.call_count,1)
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],1.5)
    def test_alignment_uses_real_character_times(self):
        data={'characters':[{'text':'钥','start':0.2,'end':0.4},{'text':'匙','start':0.6,'end':0.8},{'text':'。','start':0.8,'end':0.9}]}
        srt=it.alignment_srt(data,'钥匙。'); self.assertIn('00:00:00,200 --> 00:00:00,900',srt)
        for text in ('钥匙','换稿'):
            with self.assertRaises(WorkflowError): it.alignment_srt(data,text)
        data['characters'][1]['start']=-1
        with self.assertRaises(WorkflowError): it.alignment_srt(data,'钥匙。')
    def test_zero_time_alignment_cues_merge_without_losing_text(self):
        chars=[{'text':t,'start':a,'end':b} for t,a,b in [('。',0,0),('钥',0.2,0.4),('匙',0.5,0.8),('。',0.8,0.8),('！',0.8,0.8),('\n',0.8,0.8),('年',0.8,0.8)]]
        result=it.alignment_srt({'characters':chars},'。钥匙。！\n年')
        self.assertIn('00:00:00,200 --> 00:00:00,800',result)
        for char in '钥匙年': self.assertIn(char,result)
        for char in '。！': self.assertNotIn(char,result)
        self.assertEqual(result.count('-->'),1)
        with self.assertRaises(WorkflowError): it.alignment_srt({'characters':[{'text':'字','start':0,'end':0}]},'字')
    def prepare_asr_audit(self):
        # Synthetic independent evidence fixture; never presented as a real ASR run.
        arts=self.store.artifacts(self.ep);work=it.folder(self.store,self.ep)
        pair={'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']}
        transcript=work/'asr/whisper-medium.json';comparison=work/'asr/comparison.json'
        atomic_json(transcript,{'audio_sha256':pair['audio_sha256'],'text':'synthetic test transcript'})
        atomic_json(comparison,{'script_sha256':pair['script_sha256'],'asr_sha256':digest(transcript)})
        atomic_json(work/'asr/fulltext-audit.json',{'status':'pass','reviewer':'independent-synthetic-fixture',**pair,'asr_sha256':digest(transcript),'comparison_sha256':digest(comparison)})
    def prepare_alignment_recovery(self, text='钥匙。！'):
        self.store.register(self.ep,'script',self.file('script.txt',text))
        arts=self.store.artifacts(self.ep)
        self.store.record_review(self.ep,self.file('recovery-review.json',{'episode_id':self.ep,'reviewer':'critic','verdict':'pass','outline_sha256':arts['outline']['sha256'],'script_sha256':arts['script']['sha256'],'structure_sha256':arts['structure']['sha256'],'checks':{k:{'status':'pass','reason':'独立复核'} for k in CHECKS}}))
        audio=self.store.register(self.ep,'audio',self.file('audio.mp3','audio'))
        arts=self.store.artifacts(self.ep); inputs={'script_sha256':arts['script']['sha256'],'audio_sha256':audio['sha256']}
        work=it.folder(self.store,self.ep); atomic_json(work/'voice.binding.json',{'inputs':{'script_sha256':inputs['script_sha256']},'audio_sha256':audio['sha256']})
        self.prepare_asr_audit()
        key,_=it.begin(self.store,self.ep,'align',inputs,1)
        output=work/'align'/key/'alignment.json'; atomic_json(output,{'characters':[{'text':t,'start':a,'end':b} for t,a,b in [('钥',0.1,0.3),('匙',0.4,0.8),('。',0.8,0.8),('！',0.8,0.8)]]})
        it.finish(self.store,self.ep,'align',key,'unknown',{'inputs':inputs,'output':str(output),'estimated_usd_reserved':1,'actual_usd':None})
        return output,key
    def test_alignment_reconcile_is_local_preserves_cost_and_replays(self):
        from storyflow.cli import parser
        output,key=self.prepare_alignment_recovery()
        args=parser().parse_args(['--root',str(self.root),'align',self.ep,'--reconcile'])
        with patch.object(it,'run') as external,patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):
            result=it.align(args); replay=it.align(args)
        external.assert_not_called(); self.assertEqual(result['status'],'done'); self.assertTrue(replay['idempotent'])
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],1)
        self.assertEqual(self.readops()[0]['status'],'done')
        self.assertEqual(result['alignment_sha256'],digest(output))
    def test_alignment_reconcile_reads_actual_caption_units_list(self):
        self.prepare_alignment_recovery()
        units=it.folder(self.store,self.ep)/'caption_units.json'
        units.write_text(json.dumps([{'index':0,'text':'钥匙'}],ensure_ascii=False))
        args=self.args();args.reconcile=True
        with patch.object(it,'run') as external,patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):
            result=it.align(args)
        external.assert_not_called()
        self.assertEqual(result['caption_quality']['mode'],'caption_units')
        self.assertEqual(result['caption_quality']['caption_units_sha256'],digest(units))

    def test_done_alignment_rejects_changed_semantic_units_before_replay(self):
        self.prepare_alignment_recovery()
        units=it.folder(self.store,self.ep)/'caption_units.json'
        units.write_text(json.dumps([{'index':0,'text':'钥匙'}],ensure_ascii=False))
        args=self.args();args.reconcile=True
        with patch.object(it,'run') as external,patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):
            it.align(args)
            units.write_text(json.dumps([{'index':0,'text':'错字'}],ensure_ascii=False))
            with self.assertRaises(WorkflowError):it.align(args)
        external.assert_not_called()

    def semantic_alignment_done(self, independent=True):
        self.prepare_alignment_recovery()
        work=it.folder(self.store,self.ep);units=work/'caption_units.json'
        units.write_text(json.dumps([{'index':0,'text':'钥匙'}],ensure_ascii=False))
        if independent:
            arts=self.store.artifacts(self.ep)
            review=read_json(self.root/arts['review']['path'])
            review.update(caption_units_sha256=digest(units),caption_review={'status':'pass','reason':'synthetic test fixture semantic review only'})
            self.store.record_review(self.ep,self.file('semantic-review.json',review))
        cfg=read_json(self.root/'documentation/configuration.json');cfg['quality']={'require_semantic_caption_units':independent}
        self.file('documentation/configuration.json',cfg)
        self.store.config['quality']['require_semantic_caption_units']=independent
        args=self.args();args.reconcile=True
        with patch.object(it,'run') as external,patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):result=it.align(args)
        external.assert_not_called()
        return args,result,units

    def test_required_semantic_units_revalidated_by_render_and_validate(self):
        args,result,units=self.semantic_alignment_done()
        from storyflow.captions import verify_store_caption_binding
        self.assertEqual(verify_store_caption_binding(self.store,self.ep,require_semantic=True)['mode'],'caption_units')
        units.write_text(json.dumps([{'index':0,'text':'错字'}],ensure_ascii=False))
        with patch.object(it,'run') as external,patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):
            with self.assertRaisesRegex(WorkflowError,'语义单元哈希过期'):it.render(args)
            value=self.store.validate(self.ep,persist=False)
        external.assert_not_called()
        self.assertTrue(any('语义单元哈希过期' in e for e in value['errors']))

    def test_semantic_mode_alone_cannot_replace_independent_review(self):
        args,result,units=self.semantic_alignment_done(independent=False)
        from storyflow.captions import verify_store_caption_binding
        with self.assertRaisesRegex(ValueError,'独立审核'):
            verify_store_caption_binding(self.store,self.ep,require_semantic=True)

    def test_semantic_legacy_receipt_metadata_upgrade_is_local_and_immutable(self):
        args,result,units=self.semantic_alignment_done()
        work=it.folder(self.store,self.ep);original=digest(work/result['alignment_path'].split('/production/',1)[1])
        srt_before=result['artifact']['sha256'];cost_before=self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0]
        for key in ('caption_binding_version','alignment_path','display_path','quality_path'):result.pop(key)
        for key in ('binding_version','inputs','semantic_review_sha256'):result['caption_quality'].pop(key)
        quality=work/'align'/it.fingerprint(result['inputs'])/'subtitles.quality.json'
        atomic_json(quality,result['caption_quality']);atomic_json(work/'alignment.binding.json',result)
        it.finish(self.store,self.ep,'align',it.fingerprint(result['inputs']),'done',result)
        with patch.object(it,'run') as external,patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):
            upgraded=it.align(args)
        external.assert_not_called()
        self.assertEqual(upgraded['caption_binding_version'],1)
        self.assertEqual(upgraded['artifact']['sha256'],srt_before)
        self.assertEqual(digest(self.root/upgraded['alignment_path']),original)
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],cost_before)
        from storyflow.captions import verify_store_caption_binding
        verify_store_caption_binding(self.store,self.ep,require_semantic=True)

    def test_semantic_srt_shift_cannot_pass_even_if_hash_receipt_is_rewritten(self):
        args,result,units=self.semantic_alignment_done()
        # Changing display times and updating file hashes does not change the
        # immutable alignment; recomputation is authoritative.
        source=self.root/result['display_path'];source.write_text(source.read_text().replace('00:00:00,100','00:00:00,200'))
        registered=self.store.register(self.ep,'subtitles',source)
        result['artifact']=registered;result['caption_quality']['srt_sha256']=digest(source)
        atomic_json(self.root/result['quality_path'],result['caption_quality'])
        atomic_json(it.folder(self.store,self.ep)/'alignment.binding.json',result)
        from storyflow.captions import verify_store_caption_binding
        with self.assertRaisesRegex(ValueError,'不能重建'):
            verify_store_caption_binding(self.store,self.ep,require_semantic=True)

    def test_alignment_reconcile_invalid_json_or_missing_op_never_calls_api(self):
        output,key=self.prepare_alignment_recovery(); args=self.args(); args.reconcile=True
        for value in ('broken',{'characters':[{'text':'错','start':0,'end':0.5}]}):
            self.file(str(output.relative_to(self.root)),value)
            with patch.object(it,'run') as external,patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}),self.assertRaises(WorkflowError): it.align(args)
            external.assert_not_called(); self.assertEqual(self.readops()[0]['status'],'unknown')
        with self.store.db: self.store.db.execute('DELETE FROM external_operations')
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): it.align(args)
        external.assert_not_called()
    def test_alignment_long_untimed_real_text_cannot_be_reconciled(self):
        text='真正遗漏了八个以上汉字'
        with self.assertRaisesRegex(WorkflowError,'漏读'):
            it.alignment_srt({'characters':[{'text':c,'start':0,'end':0} for c in text]},text)
    def test_align_requires_script_audio_hash_binding(self):
        self.store.register(self.ep,'audio',self.file('audio.mp3','fake'))
        with self.assertRaises(WorkflowError): it.align(self.args())
    def test_exact_schedule(self):
        plan=it.shipping_plan(self.store,self.ep); self.assertIn('2030-11-04T20:00:00+09:00',plan['scheduled_at'])
        data=self.store.episode(self.ep); data['scheduled_at']='2030-11-04T21:00:00+09:00'
        with self.store.db: self.store.db.execute('UPDATE episodes SET data=? WHERE id=?',(json.dumps(data),self.ep))
        with self.assertRaises(WorkflowError): it.shipping_plan(self.store,self.ep)
    def mark_withdrawn_fixture(self):
        value=self.store.episode(self.ep);value['editorial_status']='withdrawn'
        with self.store.db:
            self.store.db.execute('UPDATE episodes SET data=? WHERE id=?',(json.dumps(value),self.ep))
    def test_ship_begin_rechecks_withdrawal_inside_transaction(self):
        self.mark_withdrawn_fixture()
        with self.assertRaisesRegex(WorkflowError,'退稿'):
            it.begin(self.store,self.ep,'ship',{'episode_id':self.ep})
        self.assertEqual(self.readops(),[])
    def test_shipping_plan_refuses_withdrawn_episode(self):
        self.mark_withdrawn_fixture()
        with self.assertRaisesRegex(WorkflowError,'退稿'):
            it.shipping_plan(self.store,self.ep)
    def test_withdrawal_during_whoami_blocks_upload(self):
        self.prepare_upload();a=self.args();a.execute=True
        def command(argv,**kw):
            if argv[1]=='whoami':
                self.mark_withdrawn_fixture()
                return json.dumps({'id':'UC'+'a'*22})
            self.fail('withdrawn episode reached external upload')
        with patch.object(Store,'validate',return_value={'passed':True}),patch.object(it,'run',side_effect=command) as command_mock:
            with self.assertRaisesRegex(WorkflowError,'退稿'):it.ship(a)
        self.assertEqual([c.args[0][1] for c in command_mock.call_args_list],['whoami'])
        self.assertEqual(self.readops(),[])
    def test_ship_plan_no_account_required(self):
        self.file('documentation/configuration.json',{'channel':{'id':None}})
        with patch.object(it,'run') as command,patch.object(it,'youtube_service') as api:
            plan=it.ship(self.args()); self.assertEqual(plan['privacy'],'private'); command.assert_not_called(); api.assert_not_called()
    def test_channel_config_explicit_and_independent(self):
        self.assertEqual(it.channel_config(self.store)[1],'story-animation')
        self.store.config['publication']={'forbidden_profiles':['other-channel']}
        self.store.config['channel']['youtube_profile']='other-channel'
        with self.assertRaises(WorkflowError): it.channel_config(self.store)
    def remote(self,plan,**changes):
        item={'id':'a'*11,'snippet':{'channelId':'UC'+'a'*22,'title':plan['title']},'status':{'privacyStatus':'private','publishAt':plan['scheduled_at']}}
        for key,value in changes.items(): item[key].update(value)
        api=Mock(); api.videos.return_value.list.return_value.execute.return_value={'items':[item]}; return api
    def test_remote_readback_rejects_wrong_identity_and_schedule(self):
        plan=it.shipping_plan(self.store,self.ep)
        for change in ({'snippet':{'channelId':'UC'+'b'*22}},{'snippet':{'title':'错集'}},{'status':{'publishAt':'2030-11-05T20:00:00+09:00'}},{'status':{'privacyStatus':'public'}}):
            with patch.object(it,'youtube_service',return_value=self.remote(plan,**change)),self.assertRaises(WorkflowError): it.check_remote(self.store,plan,'a'*11)
        with patch.object(it,'youtube_service',return_value=self.remote(plan)):
            self.assertEqual(it.check_remote(self.store,plan,'a'*11)['channel_id'],'UC'+'a'*22)
    def prepare_upload(self):
        for kind,name,body in [('video','v.mp4','binary'),('cover','c.jpg','image')]: self.store.register(self.ep,kind,self.file(name,body))
        data=self.store.episode(self.ep)
        self.store.register(self.ep,'metadata',self.file('meta.json',{'episode_id':self.ep,'channel':data['channel'],'title':data['title'],'description':'简介','scheduled_at':data['scheduled_at']}))
    def test_upload_failure_marks_unknown_no_resubmit(self):
        self.prepare_upload(); a=self.args(); a.execute=True
        def command(argv,**kw):
            if argv[1]=='whoami': return json.dumps({'id':'UC'+'a'*22})
            raise WorkflowError('thumbnail failure after upload')
        with patch.object(Store,'validate',return_value={'passed':True}),patch.object(it,'run',side_effect=command) as cmd:
            with self.assertRaises(WorkflowError): it.ship(a)
            with self.assertRaises(WorkflowError): it.ship(a)
            self.assertEqual(sum(call.args[0][1]=='upload' for call in cmd.call_args_list),1)
        self.assertEqual(self.readops()[0]['status'],'unknown')
    def test_ship_validates_before_remote_calls(self):
        a=self.args(); a.execute=True
        with patch.object(it,'run') as cmd,self.assertRaises(WorkflowError): it.ship(a)
        cmd.assert_not_called()
    def test_upload_success_then_replay_no_remote(self):
        self.prepare_upload(); a=self.args(); a.execute=True; plan=it.shipping_plan(self.store,self.ep)
        def command(argv,**kw):
            if argv[1]=='whoami': return json.dumps({'id':'UC'+'a'*22})
            self.assertEqual(argv[argv.index('--privacy')+1],'private')
            self.assertEqual(argv[argv.index('--profile')+1],'story-animation')
            return json.dumps({'video_id':'a'*11})
        with patch.object(Store,'validate',return_value={'passed':True}),patch.object(it,'run',side_effect=command) as cmd,patch.object(it,'youtube_service',return_value=self.remote(plan)),patch.object(it,'caption_sync',return_value={'status':'verified'}):
            first=it.ship(a); second=it.ship(a)
        self.assertEqual(first['video_id'],'a'*11); self.assertTrue(second['idempotent']); self.assertEqual(cmd.call_count,2)
    def test_verify_recovers_unknown_and_is_idempotent(self):
        self.prepare_upload(); key,_=it.begin(self.store,self.ep,'ship',{'anything':'original'})
        it.finish(self.store,self.ep,'ship',key,'unknown',{'video_id':None})
        a=self.args(video_id='a'*11); plan=it.shipping_plan(self.store,self.ep)
        with patch.object(Store,'validate',return_value={'passed':True}),patch.object(it,'youtube_service',return_value=self.remote(plan)):
            first=it.verify(a); second=it.verify(a)
        self.assertEqual(first,second); self.assertEqual(self.readops()[0]['status'],'done')
    def test_verify_rejects_prior_different_video(self):
        self.prepare_upload(); key,_=it.begin(self.store,self.ep,'ship',{'s':'one'})
        it.finish(self.store,self.ep,'ship',key,'unknown',{'video_id':'b'*11})
        a=self.args(video_id='a'*11); plan=it.shipping_plan(self.store,self.ep)
        with patch.object(Store,'validate',return_value={'passed':True}),patch.object(it,'youtube_service',return_value=self.remote(plan)),self.assertRaises(WorkflowError): it.verify(a)
    def past_episode(self):
        value=self.store.episode(self.ep); value.update(production_date='2020-11-02',release_date='2020-11-04',scheduled_at='2020-11-04T20:00:00+09:00')
        with self.store.db: self.store.db.execute('UPDATE episodes SET data=? WHERE id=?',(json.dumps(value),self.ep))
        return it.shipping_plan(self.store,self.ep)
    def test_public_readback_and_observe_preserve_unknown_metrics(self):
        self.prepare_upload(); plan=self.past_episode()
        scheduled={'episode_id':self.ep,'channel':plan['channel'],'channel_id':'UC'+'a'*22,'video_id':'a'*11,'scheduled_at':plan['scheduled_at'],'status':'scheduled'}
        with self.store.db: self.store.db.execute('INSERT INTO publications VALUES(?,?)',(self.ep,json.dumps(scheduled)))
        api=self.remote(plan,status={'privacyStatus':'public'},snippet={'publishedAt':plan['scheduled_at']})
        item=api.videos.return_value.list.return_value.execute.return_value['items'][0]; item['statistics']={'viewCount':'15','likeCount':'0'}
        with patch.object(it,'youtube_service',return_value=api): result=it.observe(self.args())
        self.assertEqual(result['metrics']['views'],15); self.assertEqual(result['metrics']['likes'],0)
        self.assertIsNone(result['metrics']['comments']); self.assertIsNone(result['metrics']['retention']); self.assertTrue(result['eligible_for_retro'])
        recorded=json.loads(self.store.db.execute('SELECT data FROM publications').fetchone()[0]); self.assertEqual(recorded['status'],'public')
    def test_captions_unknown_never_reposts_and_remote_recovers(self):
        self.prepare_upload(); art=self.store.register(self.ep,'subtitles',self.file('subs.srt','1\n00:00:00,100 --> 00:00:00,900\n钥匙。\n'))
        plan=it.shipping_plan(self.store,self.ep); api=Mock(); api.captions.return_value.list.return_value.execute.return_value={'items':[]}
        api.captions.return_value.insert.return_value.execute.side_effect=WorkflowError('断网')
        with patch.object(it,'youtube_service',return_value=api),patch.object(it,'caption_media',return_value=Mock()):
            with self.assertRaises(WorkflowError): it.caption_sync(self.store,plan,'a'*11,True)
            with self.assertRaises(WorkflowError): it.caption_sync(self.store,plan,'a'*11,True)
            self.assertEqual(api.captions.return_value.insert.call_count,1)
            name='qy-'+self.ep+'-'+art['sha256'][:12]
            api.captions.return_value.list.return_value.execute.return_value={'items':[{'id':'caption','snippet':{'name':name,'language':'zh-CN','status':'serving'}}]}
            self.assertEqual(it.caption_sync(self.store,plan,'a'*11,True)['status'],'verified')
            self.assertEqual(api.captions.return_value.insert.call_count,1)
        self.assertEqual(self.readops()[0]['status'],'done')
    def test_verify_fixture_cannot_ship(self):
        value=self.store.episode(self.ep); value['verification_only']=True
        with self.store.db: self.store.db.execute('UPDATE episodes SET data=? WHERE id=?',(json.dumps(value),self.ep))
        a=self.args(); a.execute=True
        with patch.object(it,'run') as cmd,self.assertRaises(WorkflowError): it.ship(a)
        cmd.assert_not_called()
    def test_no_shell_execution(self):
        with patch.object(it.subprocess,'run',return_value=Mock(returncode=0,stdout='ok')) as cmd:
            self.assertEqual(it.run(['echo','$(touch /tmp/nope)']), 'ok')
            self.assertNotIn('shell',cmd.call_args.kwargs)
    def test_render_placeholder_rejected_before_ffmpeg(self):
        self.structure([{'id':'s1','image':'any.png','duration':1,'placeholder':True}])
        for kind in ('audio','subtitles'): self.store.register(self.ep,kind,self.file(kind+'.txt','x'))
        with patch.object(it,'run') as cmd,self.assertRaises(WorkflowError): it.render(self.args())
        cmd.assert_not_called()
    def prepare_scene_manifest(self, scenes=None):
        self.store.register(self.ep,'audio',self.file('audio.mp3','audio'))
        sub=self.store.register(self.ep,'subtitles',self.file('sub.srt','1\n00:00:00,100 --> 00:00:00,900\n钥匙。\n'))
        arts=self.store.artifacts(self.ep)
        atomic_json(it.folder(self.store,self.ep)/'alignment.binding.json',{'inputs':{'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']},'artifact':sub})
        self.file('scene.png','image-one')
        manifest=it.folder(self.store,self.ep)/'scenes.json'
        atomic_json(manifest,{'scenes':scenes if scenes is not None else [{'id':'s1','image':'scene.png','duration':1,'provenance':'original artwork'}]})
        return manifest
    def test_render_technical_manifest_preserves_frozen_story(self):
        before=self.store.artifacts(self.ep)['structure']['sha256']
        manifest=self.prepare_scene_manifest()
        with patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):
            plan=it.render(self.args())
            self.assertEqual(plan['scene_manifest']['sha256'],digest(manifest))
            self.assertEqual(plan['scene_manifest']['path'],str(manifest))
            self.assertEqual(plan['scenes'][0]['sha256'],digest(self.root/'scene.png'))
            changed=json.loads(manifest.read_text()); changed['scenes'][0]['provenance']='revised rights receipt'; atomic_json(manifest,changed)
            newer=it.render(self.args()); self.assertNotEqual(it.fingerprint(plan),it.fingerprint(newer))
            self.file('scene.png','image-two'); newest=it.render(self.args())
            self.assertNotEqual(it.fingerprint(newer),it.fingerprint(newest))
        self.assertEqual(self.store.artifacts(self.ep)['structure']['sha256'],before)
        self.assertEqual(self.store.review_errors(self.ep),[])
    def test_render_binding_rejects_manifest_image_video_and_corruption(self):
        manifest=self.prepare_scene_manifest()
        with patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}): plan=it.render(self.args())
        art=self.store.register(self.ep,'video',self.file('video.mp4','video'))
        binding=it.folder(self.store,self.ep)/'render.binding.json'
        original={'episode_id':self.ep,'inputs':plan,'artifact':art}
        atomic_json(binding,original)
        self.assertEqual(self.store.render_binding_errors(self.ep),[])
        self.file('scene.png','changed-image')
        self.assertTrue(self.store.render_binding_errors(self.ep))
        self.assertTrue(any('渲染绑定无效' in e for e in self.store.validate(self.ep,persist=False)['errors']))
        self.file('scene.png','image-one')
        original_manifest=manifest.read_text(); atomic_json(manifest,{'scenes':[]})
        self.assertTrue(self.store.render_binding_errors(self.ep)); manifest.write_text(original_manifest)
        self.store.register(self.ep,'video',self.file('replacement.mp4','different-video'))
        self.assertTrue(self.store.render_binding_errors(self.ep))
        binding.write_text('{broken')
        self.assertTrue(self.store.render_binding_errors(self.ep))
        binding.unlink()
        self.assertEqual(self.store.render_binding_errors(self.ep),[])  # 手工导入沿用现有审片合同
    def test_manifest_mutation_during_render_never_registers_video(self):
        manifest=self.prepare_scene_manifest(); args=self.args(); args.execute=True
        def command(argv,**kw):
            Path(argv[-1]).write_bytes(b'mocked-video')
            if '-f' in argv and 'concat' in argv:
                value=json.loads(manifest.read_text()); value['note']='changed during render'; atomic_json(manifest,value)
            return ''
        with patch.object(Store,'_probe',return_value={'duration':1,'streams':[{'codec_type':'audio'}]}),patch.object(it,'run',side_effect=command),self.assertRaises(WorkflowError): it.render(args)
        self.assertNotIn('video',self.store.artifacts(self.ep))
    def test_scene_motion_contract_and_validation(self):
        scene={'id':'s1','image':'scene.png','duration':1,'provenance':'original','motion':'pan_left','framing':{'zoom':1.2,'x':0.8,'y':0.3}}
        self.prepare_scene_manifest([scene])
        with patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):
            plan=it.render(self.args())
            self.assertEqual(plan['scenes'][0]['motion'],'pan_left')
            self.assertEqual(plan['scenes'][0]['framing'],scene['framing'])
            for changes in ({'motion':'spin'},{'framing':{'zoom':2}},{'framing':{'x':-1}},{'framing':{'y':True}},{'framing':{'zoom':float('nan')}}):
                self.prepare_scene_manifest([{**scene,**changes}])
                with self.assertRaises(WorkflowError): it.render(self.args())
    def test_manifest_does_not_weaken_scene_checks(self):
        for scene in (
            {'id':'s1','image':'scene.png','duration':1,'placeholder':True,'provenance':'original'},
            {'id':'s1','image':'scene.png','duration':1},
            {'id':'s1','image':'scene.png','duration':-1,'provenance':'original'},
            {'id':'s1','image':'../outside.png','duration':1,'provenance':'original'},
            {'id':'s1','image':'scene.png','duration':2,'provenance':'original'},
        ):
            self.prepare_scene_manifest([scene])
            with patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}),patch.object(it,'run') as cmd,self.assertRaises(WorkflowError): it.render(self.args())
            cmd.assert_not_called()
    def test_manifest_symlink_outside_episode_rejected(self):
        manifest=self.prepare_scene_manifest(); data=manifest.read_text(); manifest.unlink()
        outside=self.file('other-scenes.json',data); manifest.symlink_to(outside)
        with patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}),self.assertRaises(WorkflowError): it.render(self.args())
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'需要本机ffmpeg')
    def test_real_ffmpeg_render_and_hash_binding(self):
        image=self.root/'scene.png'; audio=self.root/'sound.wav'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc=size=128x128:rate=1','-frames:v','1',str(image)],check=True)
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','pcm_s16le',str(audio)],check=True)
        self.structure([{'id':'s1','image':'scene.png','duration':1,'provenance':'generated-for-test-only'}])
        self.initial_review()
        arts=self.store.artifacts(self.ep)
        self.store.record_review(self.ep,self.file('new-review.json',{'episode_id':self.ep,'reviewer':'critic','verdict':'pass','outline_sha256':arts['outline']['sha256'],'script_sha256':arts['script']['sha256'],'structure_sha256':arts['structure']['sha256'],'checks':{k:{'status':'pass','reason':'测试镜头独立核查'} for k in CHECKS}}))
        self.store.register(self.ep,'audio',audio); sub=self.store.register(self.ep,'subtitles',self.file('sub.srt','1\n00:00:00,100 --> 00:00:00,900\n钥匙。\n'))
        arts=self.store.artifacts(self.ep); atomic_json(it.folder(self.store,self.ep)/'alignment.binding.json',{'inputs':{'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']},'artifact':sub})
        episode=self.store.episode(self.ep); episode['verification_only']=True
        with self.store.db: self.store.db.execute('UPDATE episodes SET data=? WHERE id=?',(json.dumps(episode),self.ep))
        a=self.args(); a.execute=True
        result=it.render(a); self.assertFalse(result['burn_subtitles'])
        probe=self.store._probe(self.root/result['artifact']['path'],'video'); self.assertLess(abs(probe['duration']-1),0.1)
        self.assertFalse(any(s['codec_type']=='subtitle' for s in probe['streams']))
        self.assertEqual((probe['streams'][0]['width'],probe['streams'][0]['height']),(64,64))
        video=self.root/result['artifact']['path']
        frames=subprocess.run(['ffmpeg','-v','error','-i',str(video),'-vf',"select='eq(n,0)+eq(n,24)'",'-fps_mode','passthrough','-f','rawvideo','-pix_fmt','rgb24','-'],check=True,capture_output=True).stdout
        size=64*64*3; self.assertEqual(len(frames),2*size)
        self.assertEqual(frames[:size],frames[size:])  # 默认静图逐帧一致，不做推拉
        self.assertEqual(result['inputs']['scenes'][0]['motion'],'static')
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'需要本机ffmpeg')
    def test_real_static_video_mix_uses_one_clock_and_invalidates_replaced_clip(self):
        image=self.root/'scene.png'; audio=self.root/'sound.wav'; action=self.root/'action.mp4'
        def ff(*args):
            return subprocess.run(['ffmpeg','-v','error',*args],check=True,capture_output=True).stdout
        ff('-f','lavfi','-i','testsrc=size=64x64:rate=25:duration=1','-c:v','libx264','-pix_fmt','yuv420p',str(action))
        ff('-f','lavfi','-i','testsrc=size=64x64:rate=1','-frames:v','1',str(image))
        ff('-f','lavfi','-i','sine=frequency=440:duration=1.12','-c:a','pcm_s16le',str(audio))
        # Fractional segments must use cumulative frame boundaries, not round each clip up.
        scenes=[{'id':str(i),'image':'scene.png','duration':.13,'provenance':'test'} for i in range(4)]
        scenes.insert(2,{'id':'action','image':'action.mp4','media_type':'video','trim_start':.1,'duration':.6,'provenance':'test'})
        self.prepare_scene_manifest(scenes)
        ff('-y','-f','lavfi','-i','testsrc=size=64x64:rate=1','-frames:v','1',str(image))
        self.store.register(self.ep,'audio',audio)
        sub=self.store.register(self.ep,'subtitles',self.file('sub.srt','1\n00:00:00,100 --> 00:00:00,900\n钥匙\n'))
        arts=self.store.artifacts(self.ep)
        atomic_json(it.folder(self.store,self.ep)/'alignment.binding.json',{'inputs':{'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']},'artifact':sub})
        a=self.args();a.execute=True
        result=it.render(a); video=self.root/result['artifact']['path']
        probe=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(video)],capture_output=True,check=True).stdout)
        self.assertEqual(int(probe['streams'][0]['nb_frames']),34)
        for stream in probe['streams']: self.assertEqual(float(stream['start_time']),0)
        self.assertLessEqual(abs(float(probe['streams'][0]['duration'])-float(probe['streams'][1]['duration'])),.04)
        raw=ff('-i',str(video),'-pix_fmt','rgb24','-f','rawvideo','-')
        size=64*64*3; frames=[raw[i:i+size] for i in range(0,len(raw),size)]
        self.assertEqual(frames[0],frames[1]); self.assertNotEqual(frames[8],frames[17])
        # Decode both audio streams: AAC priming must not shift narration at head/middle/tail.
        import array
        def pcm(path):
            data=array.array('f');data.frombytes(ff('-i',str(path),'-ar','16000','-ac','1','-f','f32le','-'));return data
        original,encoded=pcm(audio),pcm(video)
        for start in (320,6400,12800):
            errors={lag:sum((original[n]-encoded[n+lag])**2 for n in range(start,start+640)) for lag in range(-16,17)}
            self.assertEqual(min(errors,key=errors.get),0)
        self.assertEqual(self.store.render_binding_errors(self.ep),[])
        action.write_bytes(action.read_bytes()+b'changed')
        self.assertTrue(self.store.render_binding_errors(self.ep))

    def test_manual_import_uses_real_verification_entry(self):
        from storyflow import cli
        ep=self.store.episode(self.ep)
        path=self.file('manual.json',{'episode_id':self.ep,'channel':ep['channel'],'channel_id':'UC'+'a'*22,'scheduled_at':ep['scheduled_at'],'status':'public','video_id':'testvideo01','actual_public_at':'2000-01-01T00:00:00+09:00'})
        args=argparse.Namespace(root=str(self.root),episode=self.ep,file=str(path),command='publication',action='record')
        with patch.object(it,'verify',side_effect=WorkflowError('远端核实失败')) as remote:
            with self.assertRaises(WorkflowError): cli.run(args)
        remote.assert_called_once()
        self.assertIsNone(self.store.db.execute('SELECT * FROM publications').fetchone())

if __name__=='__main__': unittest.main()
