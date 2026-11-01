"""Real local subtitle pipeline and fake transport only: no uploads or paid calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from storyflow.core import WorkflowError, atomic_json, digest
from storyflow import captions_multilingual as cm, pilot_release as pr

class Captions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.src=self.root/'source.srt';self.src.write_text(cm.srt([{'start':0.,'end':2.,'text':'人间故事'},{'start':2.,'end':4.,'text':'命运转弯'},{'start':5.,'end':7.,'text':'重新出发'},{'start':7.,'end':9.,'text':'莫忘来路'}]))
        self.packet=self.root/'packet.json';self.value=cm.prepare(self.src,self.packet,[{'start':4.,'end':5.}])
        self.translations={}
        for lang in ('en','ja','ko'):
            p=self.root/(lang+'.json');atomic_json(p,{'schema':'qy.caption.translation.v1','language':lang,'source_sha256':digest(self.src),'groups':[{'source_ids':[1,2],'text':'A story of choices'},{'source_ids':[3,4],'text':'Remember the way home'}]});self.translations[lang]=str(p)
    def tearDown(self):self.tmp.cleanup()
    def test_full_pipeline_all_five_keep_clock(self):
        manifest=cm.render(self.packet,self.root/'tracks',self.translations)
        result=cm.validate(self.root/'tracks/tracks.json');self.assertEqual(result['languages'],list(cm.LANGUAGES))
        self.assertEqual([(c['start'],c['end']) for c in cm.cues(manifest['tracks']['en']['path'])],[(0,4),(5,9)])
        self.assertEqual([(c['start'],c['end']) for c in cm.cues(manifest['tracks']['zh-Hant']['path'])],[(0,2),(2,4),(5,7),(7,9)])
    def test_source_drift(self):
        self.src.write_text(self.src.read_text().replace('故事','真相'))
        with self.assertRaisesRegex(WorkflowError,'漂移'):cm.render(self.packet,self.root/'tracks',self.translations)
    def test_missing_duplicate_reverse_coverage(self):
        for ids in ([1],[1,2,2,3],[2,1,3,4]):
            value=json.loads(Path(self.translations['en']).read_text());value['groups']=[{'source_ids':[i],'text':'X'} for i in ids]
            with self.assertRaises(WorkflowError):cm.translation(self.value,'en',value)
    def test_forbid_group_crossing_chapter(self):
        v=json.loads(Path(self.translations['en']).read_text());v['groups']=[{'source_ids':[1,2,3,4],'text':'No'}]
        with self.assertRaisesRegex(WorkflowError,'跨章节'):cm.translation(self.value,'en',v)
    def test_one_frame_audio_edge_is_explicit(self):
        self.assertFalse(cm.crosses(4.99,7,[{'start':4,'end':5}]))
        self.assertTrue(cm.crosses(4.9,7,[{'start':4,'end':5}]))
        self.assertTrue(cm.crosses(2,7,[{'start':4,'end':5}]))
    def test_wrong_language_sha_blank_or_srt_injection(self):
        v=json.loads(Path(self.translations['en']).read_text())
        for key,value in [('language','ko'),('source_sha256','0'*64)]:
            bad=copy.deepcopy(v);bad[key]=value
            with self.assertRaises(WorkflowError):cm.translation(self.value,'en',bad)
        for text in ('','\n','x\n\ny','bad --> srt'):
            bad=copy.deepcopy(v);bad['groups'][0]['text']=text
            with self.assertRaises(WorkflowError):cm.translation(self.value,'en',bad)
    def test_speed_rejection(self):
        v=json.loads(Path(self.translations['en']).read_text());v['groups'][0]['text']='x'*150
        with self.assertRaisesRegex(WorkflowError,'阅读'):cm.translation(self.value,'en',v)
    def test_input_and_output_drift(self):
        m=cm.render(self.packet,self.root/'tracks',self.translations)
        p=Path(m['tracks']['en']['path']);p.write_text(p.read_text().replace('choices','money'))
        with self.assertRaisesRegex(WorkflowError,'SHA'):cm.validate(self.root/'tracks/tracks.json')
    def test_no_overwrite(self):
        with self.assertRaises(WorkflowError):cm.prepare(self.src,self.packet)
        cm.render(self.packet,self.root/'tracks',self.translations)
        with self.assertRaises(WorkflowError):cm.render(self.packet,self.root/'tracks',self.translations)

class Request:
    def __init__(self,value):self.value=value
    def execute(self):
        if isinstance(self.value,Exception):raise self.value
        return self.value
    def next_chunk(self,num_retries=0):return None,self.execute()
class API:
    def __init__(self):self.channel='UC'+'x'*22;self.posts=[];self.vid=None;self.caption_rows=[];self.playlist=[];self.privacy='private';self.fail_upload=False;self.fail_caption=False;self.remote_status={};self.last_status_update=None
    def channels(self):return Endpoint(self,'channels')
    def videos(self):return Endpoint(self,'videos')
    def captions(self):return Endpoint(self,'captions')
    def thumbnails(self):return Endpoint(self,'thumbnails')
    def playlists(self):return Endpoint(self,'playlists')
    def playlistItems(self):return Endpoint(self,'playlistItems')
class Endpoint:
    def __init__(self,api,kind):self.a=api;self.k=kind
    def list(self,**kw):
        a=self.a
        if self.k=='channels':return Request({'items':[{'id':a.channel}]})
        if self.k=='videos':return Request({'items':[] if not a.vid else [{'id':a.vid,'snippet':{'channelId':a.channel,'title':a.body['snippet']['title'],'description':a.body['snippet']['description'],'thumbnails':{'high':{'url':'https://example.test/poster.jpg'}}},'status':{**a.remote_status,'privacyStatus':a.privacy,'uploadStatus':'processed'}}]})
        if self.k=='captions':return Request({'items':a.caption_rows})
        if self.k=='playlists':return Request({'items':[{'snippet':{'channelId':a.channel}}]})
        if self.k=='playlistItems':return Request({'items':a.playlist})
    def insert(self,**kw):
        a=self.a;a.posts.append(self.k)
        if self.k=='videos':
            if a.fail_upload:return Request(RuntimeError('synthetic transport loss'))
            a.vid='VIDEO';a.body=kw['body'];return Request({'id':a.vid})
        if self.k=='captions':
            if a.fail_caption:return Request(RuntimeError('synthetic caption loss'))
            row={'id':'C'+str(len(a.caption_rows)),'snippet':{**kw['body']['snippet'],'status':'serving'}};a.caption_rows.append(row);return Request(row)
        if self.k=='playlistItems':
            row={'id':'P','contentDetails':{'videoId':a.vid}};a.playlist.append(row);return Request(row)
    def set(self,**kw):self.a.posts.append('thumbnail');return Request({'items':[{'high':{'url':'https://example.test/poster.jpg'}}]})
    def update(self,**kw):
        self.a.posts.append('privacy');self.a.last_status_update=kw['body']['status'];self.a.privacy=kw['body']['status']['privacyStatus']
        self.a.remote_status={**self.a.remote_status,'embeddable':False,'publicStatsViewable':False,**kw['body']['status']}
        return Request({'id':self.a.vid})

class Pilot(unittest.TestCase):
    def setUp(self):
        Captions.setUp(self);cm.render(self.packet,self.root/'tracks',self.translations)
        (self.root/'.jarvis').mkdir();(self.root/'review-a.json').write_text('{}');(self.root/'review-b.json').write_text('{}')
        for f in ('clean.mp4','thumb.jpg','accept.md','authority.md'):(self.root/f).write_text('synthetic local fixture '+f)
        self.report=self.root/'.jarvis/report.json';atomic_json(self.report,{'final_media':{'episode_id':'test','mechanical_clock_passed':True,'chapter_clean':{'sha256':digest(self.root/'clean.mp4')},'final_video':{'sha256':'b'*64},'delivery':{'video':pr.record_file(self.root/'clean.mp4'),'subtitles':pr.record_file(self.src)}},'independent_reviews':{'A':'review-a.json','B':'review-b.json','A_verdict':'A-PASS','B_verdict':'B-PASS'}})
        # Real decoded synthetic PNG; no optional system SVG converter required.
        from PIL import Image
        (self.root/'thumb.jpg').unlink()
        Image.new('RGB',(1280,720),'#141414').save(self.root/'thumb.png')
        atomic_json(self.root/'caption-review.json',{'status':'pass','reviewer':'independent-test-editor','translator':'synthetic-translation-fixture','source_sha256':digest(self.src),'tracks_manifest_sha256':digest(self.root/'tracks/tracks.json'),'checks':{k:'pass' for k in ('coverage','names_numbers','faithfulness','readability')}})
        self.metadata=self.root/'metadata.json';atomic_json(self.metadata,{'title':'Pilot','description':'Story','made_for_kids':False})
        self.contract=self.root/'contract.json';atomic_json(self.contract,{'kind':'accepted-review-pilot','source_review_only':True,'publication_id':'pilot-test','episode_id':'test','expected_channel_id':'UC'+'x'*22,'profile':'isolated-stories','source_report':str(self.report),'owner_acceptance':str(self.root/'accept.md'),'upload_authority':str(self.root/'authority.md'),'thumbnail':str(self.root/'thumb.png'),'captions_review':str(self.root/'caption-review.json'),'captions_manifest':str(self.root/'tracks/tracks.json'),'metadata':str(self.metadata),'playlist_id':'PL-test','accepted_burned_sha256':'b'*64})
        self.plan=self.root/'plan.json';pr.create(self.contract,self.plan);self.api=API()
    def test_public_accepted_media_receipt_is_independent_of_private_layout(self):
        value={'schema':'story-animation.accepted-media.v1','episode_id':'test','source_review_only':True,'accepted_burned_sha256':'b'*64,'mechanical_clock_passed':True,'clean_video':pr.record_file(self.root/'clean.mp4'),'subtitles':pr.record_file(self.src),'reviews':{role:{**pr.record_file(self.root/('review-'+role.lower()+'.json')),'verdict':'pass','reviewer':'independent-'+role} for role in ('A','B')}}
        atomic_json(self.report,value)
        result=pr.create(self.contract,self.root/'public-preflight.json',True)
        self.assertTrue(result['ok']);self.assertFalse((self.root/'public-preflight.json').exists());self.assertEqual(result['files']['review_A']['sha256'],digest(self.root/'review-a.json'))
        # Initial synthetic plan has no POSTs; recreate its same claim using public receipt, then exercise full fake-API chain.
        self.plan.unlink();pr.create(self.contract,self.plan)
        for stage in ('upload','thumbnail','captions','playlist'):self.run_stage(stage)
        self.assertEqual(self.run_stage('privacy',privacy='unlisted')['privacy'],'unlisted')
        value['reviews']['B']['reviewer']=value['reviews']['A']['reviewer'];atomic_json(self.report,value)
        with self.assertRaisesRegex(WorkflowError,'independent'):pr.create(self.contract,self.root/'public-preflight.json',True)
    def tearDown(self):self.tmp.cleanup()
    def run_stage(self,name,execute=True,privacy=None):return pr.run(self.plan,name,execute,privacy,service_factory=lambda p:self.api,media_factory=lambda *a,**k:'MEDIA')
    def test_new_id_cannot_escape_existing_source_publication(self):
        value=json.loads(self.contract.read_text());value['publication_id']='new-id';new=self.root/'another-contract.json';atomic_json(new,value)
        with self.assertRaisesRegex(WorkflowError,'已有独立发布记录'):pr.create(new,self.root/'another-plan.json')
    def test_caption_review_cannot_be_self_signed(self):
        value=json.loads((self.root/'caption-review.json').read_text());value['reviewer']=value['translator'];atomic_json(self.root/'caption-review.json',value)
        with self.assertRaisesRegex(WorkflowError,'自签'):pr.create(self.contract,self.root/'plan2.json')
    def test_fake_thumbnail_rejected(self):
        (self.root/'thumb.png').write_bytes(b'not an image')
        with self.assertRaisesRegex(WorkflowError,'封面'):pr.create(self.contract,self.root/'plan2.json')
    def test_metadata_external_description_and_alias(self):
        (self.root/'description.txt').write_text('Full description')
        meta=self.root/'external-meta.json';atomic_json(meta,{'title':'Pilot','description_file':'description.txt','self_declared_made_for_kids':False})
        result=pr.load_metadata(meta);self.assertEqual(result['description'],'Full description');self.assertIs(result['made_for_kids'],False)
    def test_metadata_rejects_outside_description_and_conflicting_text(self):
        for value in ({'description_file':'../outside.txt'},{'description_file':'description.txt','description':'different'}):
            (self.root/'description.txt').write_text('Real text');meta=self.root/'external-meta.json';atomic_json(meta,value)
            with self.assertRaises(WorkflowError):pr.load_metadata(meta)
    def test_preflight_reads_actual_inputs_without_writing_plan_or_index(self):
        old=(self.root/'state/pilot-publications.json').read_bytes();out=self.root/'missing-dir/new-plan.json'
        result=pr.create(self.contract,out,validate_only=True)
        self.assertEqual(result['mode'],'preflight');self.assertFalse(out.exists());self.assertEqual((self.root/'state/pilot-publications.json').read_bytes(),old)
    def test_dryrun_no_network(self):
        with patch.object(pr,'youtube_service',side_effect=AssertionError('network')):pr.run(self.plan,'upload')
        self.assertEqual(self.api.posts,[])
    def test_order_and_full_success_without_mode_flip(self):
        with self.assertRaisesRegex(WorkflowError,'video_id'):self.run_stage('thumbnail')
        self.run_stage('upload')
        with self.assertRaisesRegex(WorkflowError,'缩略图'):self.run_stage('captions')
        self.run_stage('thumbnail');self.run_stage('captions');self.run_stage('playlist');out=self.run_stage('privacy',privacy='unlisted')
        self.assertTrue(out['all_five_captions_serving']);self.assertEqual(out['privacy'],'unlisted');self.assertEqual(self.api.posts,['videos','thumbnail']+['captions']*5+['playlistItems','privacy'])
        self.assertTrue(json.loads(self.plan.read_text())['source_review_only_unchanged'])
        self.run_stage('upload');self.run_stage('captions');self.assertEqual(len(self.api.posts),9)
    def test_unknown_upload_no_repeat(self):
        self.api.fail_upload=True
        with self.assertRaisesRegex(WorkflowError,'未知'):self.run_stage('upload')
        self.api.fail_upload=False
        with self.assertRaisesRegex(WorkflowError,'未决'):self.run_stage('upload')
        self.assertEqual(self.api.posts,['videos']);self.assertEqual(json.loads(self.plan.read_text())['operations']['upload']['status'],'unknown')
    def test_unknown_caption_blocks_other_writes(self):
        self.run_stage('upload');self.run_stage('thumbnail');self.api.fail_caption=True
        with self.assertRaisesRegex(WorkflowError,'未知'):self.run_stage('captions')
        self.api.fail_caption=False
        with self.assertRaisesRegex(WorkflowError,'未决'):self.run_stage('captions')
        with self.assertRaisesRegex(WorkflowError,'未决'):self.run_stage('playlist')
        self.assertEqual(self.api.posts,['videos','thumbnail','captions'])
    def test_channel_mismatch_no_write(self):
        self.api.channel='UC'+'z'*22
        with self.assertRaisesRegex(WorkflowError,'频道'):self.run_stage('upload')
        self.assertEqual(self.api.posts,[])
    def test_input_mutation_blocks(self):
        (self.root/'clean.mp4').write_text('changed')
        with self.assertRaisesRegex(WorkflowError,'改变'):self.run_stage('upload')
        self.assertEqual(self.api.posts,[])
    def test_privacy_preserves_existing_writable_status_and_excludes_readonly(self):
        self.api.remote_status={'license':'youtube','embeddable':True,'publicStatsViewable':True,'selfDeclaredMadeForKids':False,'containsSyntheticMedia':True,'madeForKids':False,'failureReason':'readonly-example'}
        self.run_stage('upload');self.run_stage('thumbnail');self.run_stage('captions');self.run_stage('playlist');self.run_stage('privacy',privacy='unlisted')
        self.assertEqual(self.api.last_status_update,{'license':'youtube','embeddable':True,'publicStatsViewable':True,'selfDeclaredMadeForKids':False,'containsSyntheticMedia':True,'privacyStatus':'unlisted'})
        self.assertTrue(self.api.remote_status['embeddable']);self.assertTrue(self.api.remote_status['publicStatsViewable'])
    def test_privacy_preserves_false_flags_without_inventing_absent_fields(self):
        self.api.remote_status={'embeddable':False,'publicStatsViewable':False,'selfDeclaredMadeForKids':False}
        self.run_stage('upload');self.run_stage('thumbnail');self.run_stage('captions');self.run_stage('playlist');self.run_stage('privacy',privacy='unlisted')
        self.assertEqual(self.api.last_status_update,{'embeddable':False,'publicStatsViewable':False,'selfDeclaredMadeForKids':False,'privacyStatus':'unlisted'})
    def test_private_until_all_captions_serving(self):
        self.run_stage('upload');self.run_stage('thumbnail');self.run_stage('captions');self.run_stage('playlist');self.api.caption_rows[-1]['snippet']['status']='syncing'
        with self.assertRaisesRegex(WorkflowError,'serving'):self.run_stage('privacy',privacy='public')
        self.assertEqual(self.api.privacy,'private')
    def test_readonly_recovery_cannot_adopt_other_video(self):
        self.api.fail_upload=True
        with self.assertRaises(WorkflowError):self.run_stage('upload')
        self.api.fail_upload=False;self.api.vid='WRONG';self.api.body={'snippet':{'title':'Pilot','description':'no publication marker'}}
        with self.assertRaisesRegex(WorkflowError,'标识'):pr.recover(self.plan,'upload','WRONG','readback check',lambda p:self.api)
        self.assertEqual(len(self.api.posts),1)

if __name__=='__main__':unittest.main()
