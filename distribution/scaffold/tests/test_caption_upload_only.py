"""Portable upload-only tests; synthetic local inputs and fake network only."""
import json
import contextlib
import io
import copy
import unittest
from pathlib import Path
from unittest.mock import patch
from storyflow import pilot_release as pilot, cli
from storyflow.core import WorkflowError, atomic_json
from tests.test_multilingual_pilot import Pilot, Endpoint, Request

class UploadOnly(Pilot):
    def cli_stage(self,action,**kwargs):
        args=['--root',str(self.root),'pilot-release',action,'--plan',str(self.plan)]
        if action!='readback':args+=['--execute']
        if kwargs.get('privacy'):args+=['--privacy',kwargs['privacy']]
        if kwargs.get('diagnostic'):args+=['--diagnostic-caption-download']
        out=io.StringIO()
        with patch.object(pilot,'youtube_service',return_value=self.api), patch('googleapiclient.http.MediaFileUpload',side_effect=lambda p,**kw:p), contextlib.redirect_stdout(out):
            code=cli.main(args)
        return code,json.loads(out.getvalue())

    def test_normal_cli_and_final_privacy_never_download(self):
        with patch.object(Endpoint,'download',create=True,side_effect=AssertionError('normal download forbidden')) as download:
            for action in ('upload','thumbnail','captions','playlist'):
                code,value=self.cli_stage(action);self.assertEqual(code,0,value)
            code,value=self.cli_stage('privacy',privacy='unlisted');self.assertEqual(code,0,value)
            for _ in range(2):
                code,value=self.cli_stage('readback');self.assertEqual(code,0,value)
            download.assert_not_called()
        self.assertTrue(value['all_five_captions_metadata_serving_verified'])
        self.assertTrue(all(row['content_matches'] is None for row in value['captions'].values()))
        self.assertEqual(self.api.posts.count('captions'),5)
        self.assertEqual(self.api.posts.count('videos'),1)

    def test_cli_processing_wrong_metadata_and_duplicate_ids_block_final_privacy(self):
        for stage in ('upload','thumbnail','captions','playlist'):self.run_stage(stage)
        original_plan=self.plan.read_bytes();rows=copy.deepcopy(self.api.caption_rows);posts=list(self.api.posts)
        cases=[('status','processing'),('status','failed'),('videoId','wrong'),('language','wrong'),('name','wrong')]
        with patch.object(Endpoint,'download',create=True,side_effect=AssertionError('normal download forbidden')) as download:
            for key,bad in cases:
                self.plan.write_bytes(original_plan);self.api.caption_rows=copy.deepcopy(rows)
                self.api.caption_rows[0]['snippet'][key]=bad
                code,value=self.cli_stage('privacy',privacy='public');self.assertEqual(code,1,(key,value))
                self.assertEqual(self.api.privacy,'private');self.assertEqual(self.api.posts,posts)
            self.plan.write_bytes(original_plan);self.api.caption_rows=copy.deepcopy(rows)
            value=json.loads(original_plan);value['caption_ids']['en']=value['caption_ids']['zh-Hans'];atomic_json(self.plan,value)
            code,value=self.cli_stage('privacy',privacy='public');self.assertEqual(code,1,value)
            self.assertEqual(self.api.posts,posts);download.assert_not_called()

    def test_cli_legacy_success_id_receipts_and_bad_http_never_blind_repost(self):
        self.run_stage('upload');self.run_stage('thumbnail');before=self.plan.read_bytes()
        from googleapiclient.errors import HttpError
        from httplib2 import Response
        failures=[Request({'id':''}),Request({'id':'not-success','error':{'code':403}}),Request(HttpError(Response({'status':'403'}),b'{"error":{"code":403,"message":"synthetic forbidden"}}'))]
        original=Endpoint.insert
        with patch.object(Endpoint,'download',create=True,side_effect=AssertionError('normal download forbidden')) as download:
            for response in failures:
                self.plan.write_bytes(before);start=len(self.api.posts)
                def fail(endpoint,**kwargs):
                    if endpoint.k=='captions':self.api.posts.append('captions');return response
                    return original(endpoint,**kwargs)
                with patch.object(Endpoint,'insert',fail):
                    for _ in range(2):
                        code,value=self.cli_stage('captions');self.assertEqual(code,1,value)
                self.assertEqual(len(self.api.posts),start+1)
                value=json.loads(self.plan.read_text());self.assertEqual(value['operations']['caption:zh-Hans']['status'],'unknown')
            download.assert_not_called()
        self.plan.write_bytes(before)
        for stage in ('captions','playlist'):self.run_stage(stage)
        plan=json.loads(self.plan.read_text())
        for lang,cid in plan['caption_ids'].items():plan['operations']['caption:'+lang]['result']={'id':cid}
        atomic_json(self.plan,plan)
        with patch.object(Endpoint,'download',create=True,side_effect=AssertionError('legacy receipt normal download forbidden')) as download:
            code,result=self.cli_stage('privacy',privacy='unlisted');self.assertEqual(code,0,result);download.assert_not_called()
        self.assertTrue(result['all_five_captions_metadata_serving_verified'])

    def test_explicit_cli_diagnostic_downloads_five_and_default_remains_zero(self):
        for stage in ('upload','thumbnail','captions','playlist'):self.run_stage(stage)
        tracks=json.loads((self.root/'tracks/tracks.json').read_text())['tracks']
        by_id={row['id']:Path(tracks[row['snippet']['language']]['path']).read_bytes() for row in self.api.caption_rows}
        with patch.object(Endpoint,'download',create=True,side_effect=lambda **kw:Request(by_id[kw['id']])) as download:
            code,result=self.cli_stage('readback',diagnostic=True);self.assertEqual(code,0,result)
            self.assertEqual(download.call_count,5);self.assertTrue(all(row['content_matches'] for row in result['captions'].values()))
            code,result=self.cli_stage('readback');self.assertEqual(code,0,result)
            self.assertEqual(download.call_count,5);self.assertTrue(all(row['content_matches'] is None for row in result['captions'].values()))

    def test_diagnostic_flag_rejected_on_mutating_cli_stages_before_any_api(self):
        with patch.object(pilot,'youtube_service') as factory, patch.object(Endpoint,'download',create=True) as download:
            for action in ('upload','thumbnail','captions','playlist','privacy'):
                args=['pilot-release',action,'--plan',str(self.plan),'--execute','--diagnostic-caption-download']
                if action=='privacy':args+=['--privacy','public']
                with contextlib.redirect_stdout(io.StringIO()):code=cli.main(args)
                self.assertEqual(code,1)
            factory.assert_not_called();download.assert_not_called()

    def test_normal_public_flow_downloads_zero(self):
        with patch.object(Endpoint,'download',create=True,side_effect=AssertionError('normal download forbidden')) as download:
            for stage in ('upload','thumbnail','captions','playlist'):self.run_stage(stage)
            result=self.run_stage('privacy',privacy='unlisted')
            for _ in range(2):result=self.run_stage('readback')
            download.assert_not_called()
        self.assertTrue(result['all_five_captions_metadata_serving_verified'])
        for row in result['captions'].values():
            self.assertTrue(row['upload_receipt_verified']);self.assertTrue(row['metadata_serving_verified'])
            self.assertIsNone(row['content_matches'])

    def test_unknown_recovery_checks_video_without_download_or_repost(self):
        self.run_stage('upload');self.run_stage('thumbnail');self.api.fail_caption=True
        with self.assertRaises(WorkflowError):self.run_stage('captions')
        self.api.fail_caption=False
        track=json.loads((self.root/'tracks/tracks.json').read_text())['tracks']['zh-Hans']
        row={'id':'RECOVERED','snippet':{'videoId':'wrong','language':'zh-Hans','name':track['name'],'status':'serving'}}
        self.api.caption_rows.append(row)
        with patch.object(Endpoint,'download',create=True,side_effect=AssertionError('normal download forbidden')) as download:
            with self.assertRaises(WorkflowError):pilot.recover(self.plan,'caption:zh-Hans','RECOVERED','explicit reconcile',lambda _:self.api)
            row['snippet']['videoId']=self.api.vid
            pilot.recover(self.plan,'caption:zh-Hans','RECOVERED','explicit reconcile',lambda _:self.api)
            for stage in ('captions','playlist'):self.run_stage(stage)
            self.run_stage('privacy',privacy='unlisted');download.assert_not_called()
        self.assertEqual(self.api.posts.count('captions'),5)

    def test_corrupt_receipt_and_failed_metadata_block_privacy(self):
        for stage in ('upload','thumbnail','captions','playlist'):self.run_stage(stage)
        plan=json.loads(self.plan.read_text());plan['operations']['caption:en']['result']={};atomic_json(self.plan,plan)
        with self.assertRaises(WorkflowError):self.run_stage('privacy',privacy='public')
        self.api.caption_rows[0]['snippet']['status']='failed'
        with self.assertRaises(WorkflowError):self.run_stage('privacy',privacy='public')
        self.assertEqual(self.api.privacy,'private')

    def test_explicit_diagnostic_reads_real_cues_without_scheduled_module(self):
        for stage in ('upload','thumbnail','captions','playlist'):self.run_stage(stage)
        tracks=json.loads((self.root/'tracks/tracks.json').read_text())['tracks']
        by_id={row['id']:Path(tracks[row['snippet']['language']]['path']).read_bytes() for row in self.api.caption_rows}
        with patch.object(Endpoint,'download',create=True) as method:
            method.side_effect=lambda **kwargs:Request(by_id[kwargs['id']])
            result=pilot.run(self.plan,'readback',service_factory=lambda _:self.api,diagnostic_caption_download=True)
            self.assertEqual(method.call_count,5)
            self.assertTrue(all(row['content_matches'] for row in result['captions'].values()))
            cid=next(row['id'] for row in self.api.caption_rows if row['snippet']['language']=='en')
            by_id[cid]=b'1\n00:00:00,000 --> 00:00:01,000\nchanged\n'
            result=pilot.run(self.plan,'readback',service_factory=lambda _:self.api,diagnostic_caption_download=True)
            self.assertFalse(result['captions']['en']['content_matches'])

# Imported base remains owned by its original module; run only added cases here.
def load_tests(loader,tests,pattern):
    return unittest.TestSuite(UploadOnly(name) for name in UploadOnly.__dict__ if name.startswith('test_'))
