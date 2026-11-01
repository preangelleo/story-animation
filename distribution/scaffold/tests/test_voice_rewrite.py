"""Offline regression: actual FFmpeg splice, independent source PCM and provider boundary mock."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch
from tests import test_integrations as fixtures
import unittest
from storyflow import integrations as it, voice_rewrite as vr
from storyflow.core import WorkflowError, CHECKS, digest, atomic_json, read_json
from storyflow.production import reserve, reserve_check

class RewriteTests(unittest.TestCase):
    # Reuse fixture setup only; do not duplicate its integration test suite.
    for _name in ('setUp','tearDown','file','structure','initial_review','readops','prepare_patch_source','prepare_alignment_recovery','prepare_asr_audit','patch_review_file'):
        locals()[_name]=getattr(fixtures.IntegrationTests,_name)
    def prepare_rewrite(self):
        old=self.prepare_patch_source()
        audio=self.root/'real.wav'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=2','-ar','44100','-ac','1',str(audio)],check=True)
        art=self.store.register(self.ep,'audio',audio); arts=self.store.artifacts(self.ep)
        pair={'script_sha256':arts['script']['sha256'],'audio_sha256':art['sha256']}; work=it.folder(self.store,self.ep)
        atomic_json(work/'voice.binding.json',{'inputs':{'script_sha256':pair['script_sha256'],'voice':'user-voice','model':'eleven_multilingual_v2','language':'zh'},'audio_sha256':art['sha256']})
        alignment={'characters':[{'text':c,'start':i*.2,'end':(i+1)*.2} for i,c in enumerate('钥匙。门锁。')]}
        p=work/'align'/it.fingerprint(pair)/'alignment.json'; atomic_json(p,alignment)
        atomic_json(work/'alignment.binding.json',{'inputs':pair,'alignment_sha256':digest(p),'artifact':arts['subtitles']})
        script=self.file('new.txt','新钥匙。门锁。'); text=self.file('new-paragraph.txt','新钥匙。')
        review=self.file('new-review.json',{'episode_id':self.ep,'reviewer':'independent','verdict':'pass','script_sha256':digest(script),'outline_sha256':arts['outline']['sha256'],'structure_sha256':arts['structure']['sha256'],'checks':{k:{'status':'pass','reason':'checked exact changed paragraph'} for k in CHECKS}})
        return argparse.Namespace(root=str(self.root),episode=self.ep,patch_id='rewrite',start=0,end=3,replacement_script=str(script),replacement_text=str(text),script_review_file=str(review),cut_start=None,cut_end=None,apply=False,execute=True,confirm_cost=True,estimated_usd=.1,review_file=None)
    def provider(self,argv,**kw):
        if argv[0]=='el':
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=880:duration=1','-ar','44100','-ac','1',argv[-1]],check=True); return ''
        return self.real_run(argv,**kw)
    def test_rewrite_real_splice_new_script_two_asr_namespace_and_replay(self):
        args=self.prepare_rewrite(); old=self.store.artifacts(self.ep); self.real_run=it.run
        with patch.object(it,'run',side_effect=self.provider):
            generated=vr.execute(args); self.assertTrue(vr.execute(args)['idempotent'])
        args.review_file=str(self.patch_review_file(generated,'新钥匙。')); args.apply=True
        result=vr.execute(args); replay=vr.execute(args)
        self.assertTrue(replay['idempotent']); self.assertEqual(self.store.artifacts(self.ep)['script']['sha256'],digest(args.replacement_script))
        self.assertEqual(self.store.review_errors(self.ep),[])
        self.assertTrue((self.root/old['script']['path']).is_file()); self.assertTrue((self.root/old['audio']['path']).is_file())
        binding=read_json(it.folder(self.store,self.ep)/'voice.binding.json')
        namespace=it.voice_binding_kind(self.store,self.ep,binding,{'script_sha256':digest(args.replacement_script),'audio_sha256':result['audio_sha256']})
        self.assertTrue(namespace.startswith('align-rewrite-'))
        self.assertEqual(result['fulltrack_pcm_sha256'],it.pcm_hash(result['output']))
        source=self.root/old['audio']['path']; output=Path(result['output']); pd=generated['duration']
        # Unchanged tail is independently compared against the original PCM.
        self.assertEqual(it.pcm_hash(source,.6,1.4),it.pcm_hash(output,pd,1.4))
        self.assertAlmostEqual(result['duration'],2-.6+pd,places=2)
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],1.1)
    def test_rewrite_changed_outside_range_rejected_without_post(self):
        args=self.prepare_rewrite(); Path(args.replacement_script).write_text('新钥匙。换门锁。')
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): vr.execute(args)
        external.assert_not_called(); self.assertEqual(len(self.readops()),1)
    def test_rewrite_review_or_cut_drift_prevents_post(self):
        args=self.prepare_rewrite(); args.cut_end=.9
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): vr.execute(args)
        external.assert_not_called(); args.cut_end=None
        review=read_json(args.script_review_file);review['reviewer']='writer';atomic_json(args.script_review_file,review)
        with patch.object(it,'run') as external,self.assertRaises(WorkflowError): vr.execute(args)
        external.assert_not_called()
    def test_budget_override_shared_paths_and_monthly_unchanged(self):
        proof=self.file('owner.md','explicit owner approval +2')
        self.store.config['budget']['episode_overrides']={self.ep:{'cap_usd':17,'actor':'owner','approved_at':'2030-11-02T00:00:00+09:00','owner_evidence_path':'owner.md','owner_evidence_sha256':digest(proof)}}
        self.assertEqual(self.store.episode_budget_cap(self.ep),17)
        self.store.cost(self.ep,15,'old-reservation');it.begin(self.store,self.ep,'new',{'x':1},.5)
        reserve(self.store,self.ep,'new',1);reserve_check(self.store,self.ep,'new',1)
        self.store.cost(self.ep,.5,'tail')
        with self.assertRaises(WorkflowError):it.begin(self.store,self.ep,'over',{},.01)
        with self.assertRaises(WorkflowError):self.store.cost(self.ep,.01,'over')
        with self.assertRaises(WorkflowError):reserve(self.store,self.ep,'over',.01)
        second=self.store.create('2030-11-03','other','推理')['id'];self.assertEqual(self.store.episode_budget_cap(second),15)
        Path(proof).write_text('tampered')
        with self.assertRaises(WorkflowError):self.store.episode_budget_cap(self.ep)

    def test_unknown_generation_preserves_reservation_and_blocks_repost(self):
        args=self.prepare_rewrite()
        with patch.object(it,'run',side_effect=WorkflowError('provider interrupted')) as external:
            with self.assertRaises(WorkflowError):vr.execute(args)
            with self.assertRaises(WorkflowError):vr.execute(args)
            self.assertEqual(external.call_count,1)
        row=self.store.db.execute("SELECT status FROM external_operations WHERE kind='voice-rewrite-generate-rewrite'").fetchone()
        self.assertEqual(row['status'],'unknown')
        self.assertEqual(self.store.db.execute('SELECT sum(amount) FROM costs').fetchone()[0],1.1)
    def test_apply_asr_and_frozen_review_tamper_block_registration(self):
        args=self.prepare_rewrite(); old=self.store.artifacts(self.ep); self.real_run=it.run
        with patch.object(it,'run',side_effect=self.provider): generated=vr.execute(args)
        args.review_file=str(self.patch_review_file(generated,'新钥匙。'));args.apply=True
        review=read_json(args.review_file); review['checks']=review['checks'][:1];atomic_json(args.review_file,review)
        with self.assertRaises(WorkflowError):vr.execute(args)
        self.assertEqual(self.store.artifacts(self.ep),old)
        args.review_file=str(self.patch_review_file(generated,'新钥匙。'))
        Path(args.script_review_file).write_text('{}')
        with self.assertRaises(WorkflowError):vr.execute(args)
        self.assertEqual(self.store.artifacts(self.ep),old)
    def test_override_does_not_change_monthly_limit(self):
        proof=self.file('owner.md','approval')
        self.store.config['budget']['episode_overrides']={self.ep:{'cap_usd':17,'actor':'owner','approved_at':'2030-11-02T00:00:00Z','owner_evidence_path':'owner.md','owner_evidence_sha256':digest(proof)}}
        self.store.config['budget']['monthly_usd']=1
        with self.assertRaises(WorkflowError):self.store.cost(self.ep,2,'over')
        with self.assertRaises(WorkflowError):it.begin(self.store,self.ep,'over',{},2)
        with self.assertRaises(WorkflowError):reserve(self.store,self.ep,'over',2)

    def test_patch_whole_sentence_after_dialogue_quote_and_reject_mid_sentence(self):
        text='回家。”\n\n钥匙。门锁。'
        self.prepare_alignment_recovery(text)
        self.store.register(self.ep,'subtitles',self.file('quoted-source.srt','1\n00:00:00,000 --> 00:00:03,000\n回家钥匙门锁\n'))
        arts=self.store.artifacts(self.ep)
        pair={'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']}
        work=it.folder(self.store,self.ep)
        atomic_json(work/'voice.binding.json',{'inputs':{'script_sha256':pair['script_sha256'],'voice':'user-voice','model':'eleven_multilingual_v2','language':'zh'},'audio_sha256':pair['audio_sha256']})
        alignment={'characters':[{'text':c,'start':i*.2,'end':(i+1)*.2} for i,c in enumerate(text)]}
        source=work/'align'/it.fingerprint(pair)/'alignment.json';atomic_json(source,alignment)
        atomic_json(work/'alignment.binding.json',{'inputs':pair,'alignment_sha256':digest(source),'artifact':arts['subtitles']})
        start=text.index('钥匙');end=start+3
        with patch.object(type(self.store),'_probe',return_value={'duration':20}):
            self.assertEqual(it.patch_context(self.store,self.ep,'quoted',start,end)[1],'钥匙。')
            self.assertEqual(it.patch_context(self.store,self.ep,'quoted',0,4)[1],'回家。”')
            with self.assertRaises(WorkflowError):it.patch_context(self.store,self.ep,'quoted',start+1,end)

    def test_asr_year_zero_glyph_equivalence_keeps_raw_and_rejects_wrong_word(self):
        args=self.prepare_rewrite();self.real_run=it.run
        with patch.object(it,'run',side_effect=self.provider):generated=vr.execute(args)
        expected='二〇一三年。'
        generated['inputs']['text_sha256']=vr.text_sha(expected)
        review=self.patch_review_file(generated,'二零一三年。')
        raw=(self.root/'qwen.json').read_bytes()
        self.assertTrue(it.patch_review(self.store,review,generated,expected))
        self.assertEqual((self.root/'qwen.json').read_bytes(),raw)
        wrong=self.patch_review_file(generated,'二铃一三年。')
        with self.assertRaises(WorkflowError):it.patch_review(self.store,wrong,generated,expected)

    def test_independent_bounded_asr_adjudication_requires_exact_second_provider(self):
        args=self.prepare_rewrite();self.real_run=it.run
        with patch.object(it,'run',side_effect=self.provider):generated=vr.execute(args)
        expected='李泽钜分得三亿六千二百万。'
        actual='李则聚分得3亿6200万。'
        generated['inputs']['text_sha256']=vr.text_sha(expected)
        review_path=self.patch_review_file(generated,expected)
        review=read_json(review_path)
        review.update(episode_id=self.ep,reviewer='independent-editor',status='pass',owner_review={'status':'pending_owner_review'},manual_adjudication={'verdict':'pass','reason':'actual raw comparison and exact Qwen corroboration','allowed_differences':[{'actual':'李则聚','expected':'李泽钜','scope':'Whisper name','reason':'ASR spelling; owner pronunciation pending'},{'actual':'3亿6200万','expected':'三亿六千二百万','scope':'Whisper amount','reason':'same numeric value'}]})
        def change_response(check,text):
            raw=read_json(check['response_path']);raw['text']=text
            if 'segments' in raw:raw['segments'][0]['text']=text
            atomic_json(Path(check['response_path']),raw);check['response_sha256']=digest(check['response_path'])
            receipt=read_json(check['receipt_path']);receipt['raw_response_sha256']=check['response_sha256'];atomic_json(Path(check['receipt_path']),receipt);check['receipt_sha256']=digest(check['receipt_path'])
        change_response(review['checks'][1],actual);atomic_json(review_path,review)
        before=Path(review['checks'][1]['response_path']).read_bytes()
        self.assertTrue(it.patch_review(self.store,review_path,generated,expected))
        self.assertEqual(before,Path(review['checks'][1]['response_path']).read_bytes())
        change_response(review['checks'][1],'李则聚分得3亿6201万。');atomic_json(review_path,review)
        with self.assertRaises(WorkflowError):it.patch_review(self.store,review_path,generated,expected)
        change_response(review['checks'][1],actual);change_response(review['checks'][0],actual);atomic_json(review_path,review)
        with self.assertRaises(WorkflowError):it.patch_review(self.store,review_path,generated,expected)

    def test_asr_numeral_values_and_unbounded_changes_rejected(self):
        from storyflow.asr_variants import numeral,adjudicate
        self.assertEqual(numeral('10亿3800万'),numeral('十亿三千八百万'))
        self.assertEqual(numeral('2013'),numeral('二〇一三'))
        self.assertNotEqual(numeral('3亿6201万'),numeral('三亿六千二百万'))
        value={'status':'pass','reviewer':'editor','owner_review':{'status':'pending_owner_review'},'manual_adjudication':{'verdict':'pass','reason':'review','allowed_differences':[{'actual':'整段漏读若干字','expected':'整段正确若干字','scope':'paragraph','reason':'invalid broad override'}]}}
        with self.assertRaises(WorkflowError):adjudicate(value,'整段漏读若干字','整段正确若干字','writer',it.speech_normalize)

    def test_asr_unsupported_composite_numeric_units_fail_closed(self):
        from storyflow.asr_variants import numeral,adjudicate
        self.assertIsNone(numeral('一万亿'))
        self.assertEqual(numeral('一亿一万'),100010000)
        self.assertIsNone(numeral('一百百'))
        value={'status':'pass','reviewer':'editor','owner_review':{'status':'pending_owner_review'},'manual_adjudication':{'verdict':'pass','reason':'review','allowed_differences':[{'actual':'一万亿','expected':'二万亿','scope':'number','reason':'unsupported must reject'}]}}
        with self.assertRaises(WorkflowError):adjudicate(value,'一万亿','二万亿','writer',it.speech_normalize)
