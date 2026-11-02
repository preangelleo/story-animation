import datetime as dt
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from storyflow.core import Store, WorkflowError, atomic_json, digest
from storyflow.learning import Learning

class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        atomic_json(self.root/'documentation/configuration.json',{'channel':{'id':'UC'+'A'*22,'youtube_profile':'independent'},'learning':{'enabled':True,'vector':{'enabled':True,'model':'fixture-explicit-test-only','dimensions':3,'collection':'stories','batch_size':2},'comments':{'enabled':True,'auto_reply':True,'max_pages':2}}})
        self.s=Store(self.root);self.s.create('2026-11-02','Test figure','series','ep')
        p=self.root/'script.txt';p.write_text('An accepted final story about courage.');self.s.register('ep','script',str(p))
        ev=self.root/'accepted.json';atomic_json(ev,{'accepted':True})
        self.value={'title':'Test figure','topic':'courage','series':'series','core_ideas':['cost of courage'],'summary':'A brave ending','final_script':p.read_text(),'script_sha256':digest(p),'sources':[{'url':'https://example.test/source'}],'publication':{'status':'reviewed','observed_at':'2026-11-02T10:00:00+09:00'},'acceptance_evidence':{'path':'accepted.json','sha256':digest(ev)}}
        self.l=Learning(self.s)
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def provider(self,texts,cfg):return [[1.,.3,.2] for _ in texts]
    def test_backup_version_outbox_failure_recovery_and_dedup(self):
        self.assertTrue((self.root/'state/backups/before-learning-1.sqlite3').exists())
        first=self.l.record('ep',self.value);self.assertEqual(first['version'],1)
        self.assertTrue(self.l.record('ep',self.value)['idempotent'])
        failed=self.l.index(lambda *_:(_ for _ in ()).throw(OSError('offline')));self.assertFalse(failed['ok'])
        self.assertEqual(self.s.db.execute('SELECT status FROM learning_outbox').fetchone()[0],'failed')
        self.assertEqual(self.l.index(self.provider)['indexed'],1)
        self.assertEqual(self.l.index(self.provider)['indexed'],0)
        result=self.l.dedup('courage',self.provider);self.assertEqual(result['semantic_matches'][0]['episode_id'],'ep')
        v={**self.value,'summary':'Another legitimate summary'};self.assertEqual(self.l.record('ep',v)['version'],2)
        # Old vector excluded when SQL advances; no stale false confidence.
        self.assertEqual(self.l.dedup('courage',self.provider)['semantic_matches'],[])
        self.assertEqual(self.l.index(self.provider)['indexed'],1)
    def test_reject_hash_drift_and_bad_dimensions(self):
        with self.assertRaises(WorkflowError):self.l.record('ep',{**self.value,'final_script':'different'})
        self.l.record('ep',self.value)
        self.assertFalse(self.l.index(lambda *_:[[1,2]])['ok'])
        (self.root/self.s.artifacts('ep')['script']['path']).write_text('mutated')
        with self.assertRaises(WorkflowError):self.l.record('ep',self.value)
    def service(self):
        svc=Mock();svc.channels.return_value.list.return_value.execute.return_value={'items':[{'id':'UC'+'A'*22}]};svc.videos.return_value.list.return_value.execute.return_value={'items':[{'id':'abcdefghijk','snippet':{'channelId':'UC'+'A'*22},'status':{'privacyStatus':'public'}}]};return svc
    def comment(self,id='c1'):
        return {'snippet':{'topLevelComment':{'id':id,'snippet':{'channelId':'UC'+'A'*22,'videoId':'abcdefghijk','publishedAt':'2026-11-02T08:00:00+00:00','textDisplay':'How did he learn courage?'}}}}
    def test_comments_zero_and_error_do_not_fake_watermark(self):
        svc=self.service();svc.commentThreads.return_value.list.return_value.execute.return_value={'items':[]}
        now=dt.datetime(2026,11,2,10,tzinfo=dt.timezone.utc);self.assertEqual(self.l.comments(svc,now)['observed'],0)
        old=self.s.db.execute('SELECT watermark FROM feedback_scans').fetchone()[0]
        svc.commentThreads.return_value.list.return_value.execute.side_effect=RuntimeError('quota')
        self.assertFalse(self.l.comments(svc,now+dt.timedelta(hours=2))['ok'])
        self.assertEqual(self.s.db.execute('SELECT watermark FROM feedback_scans').fetchone()[0],old)
    def test_page_cap_rolls_back_and_identity_checked(self):
        svc=self.service();svc.commentThreads.return_value.list.return_value.execute.return_value={'items':[self.comment()],'nextPageToken':'more'}
        result=self.l.comments(svc,dt.datetime(2026,11,2,10,tzinfo=dt.timezone.utc));self.assertFalse(result['ok'])
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM feedback_comments').fetchone()[0],0)
        svc.channels.return_value.list.return_value.execute.return_value={'items':[{'id':'other'}]}
        self.assertFalse(self.l.comments(svc)['ok'])
    def test_topic_provenance_and_reply_unknown_never_repost(self):
        svc=self.service();svc.commentThreads.return_value.list.return_value.execute.return_value={'items':[self.comment()]}
        self.l.comments(svc,dt.datetime(2026,11,2,10,tzinfo=dt.timezone.utc))
        self.l.assess('c1',{'reviewer':'independent-feedback-agent','author':'audience','reply_approved':True,'reply_text':'Thank you.','classification':'useful','reason':'a substantive historical question','topic_title':'Courage learned','series':'series'})
        self.l.assess('c1',{'reviewer':'independent-feedback-agent','author':'audience','reply_approved':True,'reply_text':'Thank you.','classification':'useful','reason':'a substantive historical question','topic_title':'Courage learned','series':'series'})
        self.assertEqual(len(self.l.topics()),1)
        self.assertEqual(self.l.topics()[0]['comment_id'],'c1')
        svc.comments.return_value.list.return_value.execute.return_value={'items':[{'id':'c1','snippet':{'videoId':'abcdefghijk','textOriginal':'How did he learn courage?','moderationStatus':'published'}}]}
        svc.comments.return_value.insert.return_value.execute.side_effect=RuntimeError('timeout')
        self.assertEqual(self.l.reply('c1','Thank you.',True,svc)['status'],'unknown')
        self.assertTrue(self.l.reply('c1','Thank you.',True,svc)['idempotent'])
        self.assertEqual(svc.comments.return_value.insert.call_count,1)
        svc.comments.return_value.list.return_value.execute.return_value={'items':[{'id':'receipt','snippet':{'authorChannelId':{'value':'UC'+'A'*22},'textOriginal':'Thank you.'}}]}
        self.assertEqual(self.l.reconcile_reply('c1',svc)['status'],'done')
    def test_identity403_and_reconciliation403_persist_quota(self):
        class QuotaError(Exception):pass
        error=QuotaError('quota');error.resp=Mock(status=403)
        svc=self.service();svc.channels.return_value.list.return_value.execute.side_effect=error
        result=self.l.comments(svc);self.assertFalse(result['ok']);self.assertIsNotNone(result['retry_after'])
        with self.assertRaises(WorkflowError):self.l.quota_gate()
    def test_public_filter_and_published_only(self):
        svc=self.service();svc.commentThreads.return_value.list.return_value.execute.return_value={'items':[self.comment()]}
        svc.videos.return_value.list.return_value.execute.return_value={'items':[{'id':'abcdefghijk','snippet':{'channelId':'UC'+'A'*22},'status':{'privacyStatus':'unlisted'}}]}
        self.assertEqual(self.l.comments(svc,dt.datetime(2026,11,2,10,tzinfo=dt.timezone.utc))['observed'],0)
        self.assertEqual(svc.commentThreads.return_value.list.call_args.kwargs['moderationStatus'],'published')
    def test_disabled_outbox_and_dimensions_rebuild(self):
        self.l.cfg['vector']['enabled']=False;self.l.record('ep',self.value)
        self.assertEqual(self.s.db.execute('SELECT status FROM learning_outbox').fetchone()[0],'disabled')
        self.l.cfg['vector']['enabled']=True;self.assertEqual(self.l.index(self.provider)['indexed'],1)
        self.l.cfg['vector']['dimensions']=4
        self.assertEqual(self.l.index(lambda texts,cfg:[[1.,.2,.3,.4] for _ in texts])['indexed'],1)
        self.assertEqual(self.s.db.execute('SELECT dimensions FROM semantic_vectors').fetchone()[0],4)
    def test_empty_vectors_report_semantic_gap(self):
        self.l.record('ep',self.value)
        self.assertIn('unindexed_or_stale',self.l.dedup('Test',self.provider)['semantic_gap'])
    def test_receipt_success_survives_learning_hook_error(self):
        from unittest.mock import patch
        from storyflow.integrations import persist_receipt
        receipt={'episode_id':'ep','video_id':'abcdefghijk','channel_id':'UC'+'A'*22,'scheduled_at':self.s.episode('ep')['scheduled_at'],'status':'scheduled','artifact_hashes':{}}
        def accepted(ep,path,verified=False):
            with self.s.db:self.s.db.execute('INSERT INTO publications VALUES(?,?)',(ep,json.dumps(receipt)))
        with patch.object(self.s,'publication',side_effect=accepted),patch('storyflow.learning.after_publication',side_effect=RuntimeError('learning down')):
            self.assertEqual(persist_receipt(self.s,receipt),receipt)
        self.assertTrue(self.s.db.execute('SELECT 1 FROM publications WHERE episode_id="ep"').fetchone())
        result=json.loads((self.root/'episodes/ep/learning/finalize-status.json').read_text());self.assertTrue(result['publication_untouched'])
    def test_finalize_rejects_false_public_status(self):
        from unittest.mock import patch
        with self.s.db:self.s.db.execute('INSERT INTO publications VALUES(?,?)',('ep',json.dumps({'status':'scheduled','video_id':'abcdefghijk','channel_id':'UC'+'A'*22,'verified_at':'2026-11-02T10:00:00+09:00','scheduled_at':self.s.episode('ep')['scheduled_at']})))
        value={**self.value,'publication':{'status':'public','video_id':'abcdefghijk','observed_at':'2026-11-02T10:00:00+09:00'}}
        with patch.object(self.s,'validate',return_value={'passed':True}):
            with self.assertRaisesRegex(WorkflowError,'status must match'):self.l.finalize('ep',value)
    def test_public_time_must_match_remote_and_not_follow_observation(self):
        evidence=self.root/'public.json'
        observed='2026-11-02T10:00:00+09:00';actual='2026-11-02T09:00:00+09:00'
        def value(remote,claimed):
            atomic_json(evidence,{'video_id':'abcdefghijk','privacyStatus':'public','checked_at':observed,'actual_public_at':remote})
            return {**self.value,'publication':{'status':'public','video_id':'abcdefghijk','observed_at':observed,'actual_public_at':claimed,'evidence':{'path':'public.json','sha256':digest(evidence)}}}
        with self.assertRaises(WorkflowError):self.l.record('ep',value(actual,'2099-01-01T00:00:00Z'))
        with self.assertRaises(WorkflowError):self.l.record('ep',value(None,actual))
        with self.assertRaises(WorkflowError):self.l.record('ep',value('2099-01-01T00:00:00Z','2099-01-01T00:00:00Z'))
        self.assertEqual(self.l.record('ep',value(actual,actual))['version'],1)
    def test_long_gap_retains_watermark_until_bounded_catchup_succeeds(self):
        old='2026-11-01T00:00:00+00:00'
        with self.s.db:self.s.db.execute('INSERT INTO feedback_scans VALUES(?,?,?)',(self.s.config['channel']['id'],old,'{}'))
        svc=self.service();now=dt.datetime(2026,11,20,tzinfo=dt.timezone.utc)
        result=self.l.comments(svc,now);self.assertEqual(result['status'],'catchup_window_exceeded')
        self.assertFalse(result['watermark_advanced']);svc.channels.assert_not_called()
        self.assertEqual(self.s.db.execute('SELECT watermark FROM feedback_scans').fetchone()[0],old)
        svc.commentThreads.return_value.list.return_value.execute.return_value={'items':[self.comment()]}
        result=self.l.comments(svc,now,catchup_days=30,max_pages=10)
        self.assertTrue(result['ok']);self.assertEqual(result['observed'],1)
        self.assertEqual(self.s.db.execute('SELECT watermark FROM feedback_scans').fetchone()[0],now.isoformat())
    def test_concurrent_index_executors_do_not_repeat_provider_batch(self):
        import threading,time
        self.l.record('ep',self.value);barrier=threading.Barrier(2);calls=[];results=[];errors=[]
        def provider(texts,cfg):
            calls.append(len(texts));time.sleep(.05);return self.provider(texts,cfg)
        def worker():
            store=Store(self.root)
            try:
                learn=Learning(store);barrier.wait(timeout=3);results.append(learn.index(provider))
            except Exception as exc:errors.append(type(exc).__name__)
            finally:store.close()
        threads=[threading.Thread(target=worker) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(timeout=5)
        self.assertFalse(any(t.is_alive() for t in threads));self.assertEqual(errors,[])
        self.assertEqual(calls,[1]);self.assertEqual(sorted(r['indexed'] for r in results),[0,1])
    def test_optional_disabled_and_no_store_startup_migration(self):
        root=self.root/'disabled';s=Store(root)
        with self.assertRaises(WorkflowError):Learning(s)
        self.assertFalse(s.db.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone());s.close()

    def reply_fixture(self):
        svc=self.service();svc.commentThreads.return_value.list.return_value.execute.return_value={'items':[self.comment()]}
        self.l.comments(svc,dt.datetime(2026,11,2,10,tzinfo=dt.timezone.utc))
        self.l.assess('c1',{'reviewer':'independent-feedback-agent','author':'audience','reply_approved':True,'reply_text':'谢谢你的鼓励！','classification':'normal','reason':'a short supportive audience comment'})
        parent={'textOriginal':'How did he learn courage?','moderationStatus':'published','authorChannelId':{'value':'audience-author'}}
        svc.comments.return_value.list.return_value.execute.return_value={'items':[{'id':'c1','snippet':parent}]}
        thread={'id':'c1','snippet':{'channelId':'UC'+'A'*22,'videoId':'abcdefghijk','isPublic':True,'canReply':True,'topLevelComment':{'id':'c1','snippet':{**parent,'videoId':'abcdefghijk','channelId':'UC'+'A'*22}}}}
        svc.commentThreads.return_value.list.reset_mock();svc.commentThreads.return_value.list.return_value.execute.return_value={'items':[thread]}
        svc.comments.return_value.insert.return_value.execute.return_value={'id':'new-reply'}
        return svc,thread,parent
    def test_actual_parent_shape_missing_video_uses_exact_live_thread_binding(self):
        svc,thread,parent=self.reply_fixture()
        out=self.l.reply('c1','谢谢你的鼓励！',True,svc)
        self.assertEqual(out['status'],'done',out)
        svc.commentThreads.return_value.list.assert_called_once_with(part='snippet',id='c1',textFormat='plainText')
        self.assertEqual(svc.comments.return_value.insert.call_count,1)

    def assert_safe_reply_rejected(self,svc):
        out=self.l.reply('c1','谢谢你的鼓励！',True,svc)
        self.assertEqual(out['status'],'preflight_failed',out);svc.comments.return_value.insert.assert_not_called()
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM reply_ledger').fetchone()[0],0)
        return out
    def test_fallback_wrong_missing_null_duplicate_and_text_identity_matrix(self):
        cases=[('thread_id',lambda x:x.update(id='other')),('top_id',lambda x:x['snippet']['topLevelComment'].update(id='other'))]
        for field in ('videoId','channelId','isPublic','canReply'):
            for value in (None,'wrong' if field in ('videoId','channelId') else False):cases.append((field+str(value),lambda x,k=field,v=value:x['snippet'].update({k:v})))
            cases.append(('missing_'+field,lambda x,k=field:x['snippet'].pop(k)))
        cases += [('changed_text',lambda x:x['snippet']['topLevelComment']['snippet'].update(textOriginal='changed')),('nested_wrong_channel',lambda x:x['snippet']['topLevelComment']['snippet'].update(channelId='other')),('nested_wrong_video',lambda x:x['snippet']['topLevelComment']['snippet'].update(videoId='other')),('not_top',lambda x:x['snippet']['topLevelComment']['snippet'].update(parentId='other')),('owner_author',lambda x:x['snippet']['topLevelComment']['snippet'].update(authorChannelId={'value':'UC'+'A'*22})),('unknown_author',lambda x:x['snippet']['topLevelComment']['snippet'].pop('authorChannelId'))]
        for label,mutate in cases:
            with self.subTest(label=label):
                svc,thread,parent=self.reply_fixture();mutate(thread);self.assert_safe_reply_rejected(svc)
        for items in ([],None):
            svc,thread,parent=self.reply_fixture();svc.commentThreads.return_value.list.return_value.execute.return_value={'items':items};self.assert_safe_reply_rejected(svc)
        svc,thread,parent=self.reply_fixture();svc.commentThreads.return_value.list.return_value.execute.return_value={'items':[thread,copy.deepcopy(thread)]};self.assert_safe_reply_rejected(svc)
        svc,thread,parent=self.reply_fixture();svc.commentThreads.return_value.list.return_value.execute.return_value={'items':[thread],'nextPageToken':'ambiguous'};self.assert_safe_reply_rejected(svc)
    def test_existing_video_binding_needs_no_thread_read_and_refuses_owner_or_cross_video(self):
        svc,thread,parent=self.reply_fixture();parent['videoId']='abcdefghijk'
        self.assertEqual(self.l.reply('c1','谢谢你的鼓励！',True,svc)['status'],'done');svc.commentThreads.return_value.list.assert_not_called()
        self.s.db.execute('DELETE FROM reply_ledger');self.s.db.commit()
        for change in ({'videoId':'other'},{'authorChannelId':{'value':'UC'+'A'*22}},{'channelId':'other'},{'textOriginal':'changed'}):
            svc,thread,parent=self.reply_fixture();parent['videoId']='abcdefghijk';parent.update(change);self.assert_safe_reply_rejected(svc);svc.commentThreads.return_value.list.assert_not_called()
    def test_realtime_video_private_wrong_channel_missing_duplicate_reject_before_intent(self):
        for items in ([],[{'id':'abcdefghijk','snippet':{'channelId':'UC'+'A'*22},'status':{'privacyStatus':'private'}}],[{'id':'abcdefghijk','snippet':{'channelId':'other'},'status':{'privacyStatus':'public'}}],[{'id':'other','snippet':{'channelId':'UC'+'A'*22},'status':{'privacyStatus':'public'}}]):
            svc,thread,parent=self.reply_fixture();svc.videos.return_value.list.return_value.execute.return_value={'items':items};self.assert_safe_reply_rejected(svc)
        svc,thread,parent=self.reply_fixture();video={'id':'abcdefghijk','snippet':{'channelId':'UC'+'A'*22},'status':{'privacyStatus':'public'}};svc.videos.return_value.list.return_value.execute.return_value={'items':[video,copy.deepcopy(video)]};self.assert_safe_reply_rejected(svc)
    def test_fallback403_persists_quota_without_unknown_post_intent(self):
        svc,thread,parent=self.reply_fixture()
        class QuotaError(Exception):pass
        exc=QuotaError('quota');exc.resp=Mock(status=403);svc.commentThreads.return_value.list.return_value.execute.side_effect=exc
        out=self.assert_safe_reply_rejected(svc);self.assertIsNotNone(out['retry_after'])
        with self.assertRaises(WorkflowError):self.l.quota_gate()
    def test_fallback_post_unknown_never_rechecks_parent_or_reposts(self):
        svc,thread,parent=self.reply_fixture();svc.comments.return_value.insert.return_value.execute.side_effect=TimeoutError('response lost')
        first=self.l.reply('c1','谢谢你的鼓励！',True,svc);self.assertEqual(first['status'],'unknown')
        svc.channels.return_value.list.reset_mock();svc.commentThreads.return_value.list.reset_mock();svc.comments.return_value.list.reset_mock()
        second=self.l.reply('c1','谢谢你的鼓励！',True,svc);self.assertEqual(second['status'],'unknown');self.assertTrue(second['idempotent'])
        svc.channels.return_value.list.assert_not_called();svc.commentThreads.return_value.list.assert_not_called();svc.comments.return_value.list.assert_not_called();self.assertEqual(svc.comments.return_value.insert.call_count,1)

if __name__=='__main__':unittest.main()
