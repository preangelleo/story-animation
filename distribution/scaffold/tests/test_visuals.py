import hashlib
from pathlib import Path
import tempfile
import unittest

from storyflow.visuals import validate_manifest


class VisualContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Lossless PGM inputs exercise real decoding and perceptual hash, no SDK.
        self.image('sheet.pgm', 0)
        self.image('one.pgm', 1)
        self.image('two.pgm', 2)
        self.ref = self.asset('sheet.pgm')
        self.manifest = {
            'schema_version': 1, 'style_id': 'scene-classic-rubber-hose',
            'depiction': 'illustrated_reconstruction',
            'characters': [{'id': 'a', 'lock': {k: 'locked' for k in ('age', 'face', 'hair', 'skin', 'build')},
                            'outfits': [{'id': 'o', 'description': 'cream shirt'}],
                            'references': [{**self.ref, 'outfit_id': 'o', 'views': ['front', 'side', 'back', 'expression_neutral', 'expression_tense']}]}],
            'scenes': [self.scene('s1', 'one.pgm'), self.scene('s2', 'two.pgm')],
            'similarity_reviews': []}

    def tearDown(self):
        self.tmp.cleanup()

    def image(self, name, mode):
        data = bytes((x * 13 + y * 19) % 256 if mode == 0 else (x * 7 if mode == 1 else 255 - x * 7) for y in range(16) for x in range(16))
        (self.root / name).write_bytes(b'P5\n16 16\n255\n' + data)

    def asset(self, name):
        return {'image': name, 'sha256': hashlib.sha256((self.root / name).read_bytes()).hexdigest()}

    def scene(self, sid, name):
        asset = self.asset(name)
        return {'id': sid, **asset, 'style_id': 'scene-classic-rubber-hose',
                'characters': [{'character_id': 'a', 'outfit_id': 'o', 'reference_images': [self.ref]}],
                'generation': {'tool': 'builtin.image_gen', 'call_id': sid, 'prompt': 'locked character wearing cream shirt at a distinct location',
                               'reference_images': [self.ref], 'output_sha256': asset['sha256']},
                'visual_review': {'image_sha256': asset['sha256'], 'reference_sha256s': [self.ref['sha256']],
                                  'composition': {'full_frame': True, 'subtitle_reserve': False, 'bottom_edge_notes': 'fixture edge content checked'}, 'reviewer': 'independent-visual', 'result': 'pass', 'notes': 'checked age clothing and style'}}

    def check_error(self, manifest, code):
        report = validate_manifest(manifest, self.root)
        self.assertFalse(report['passed'], report)
        self.assertTrue(any(code in e for e in report['errors']), report)

    def test_distinct_images_pass(self):
        self.assertTrue(validate_manifest(self.manifest, self.root)['passed'])

    def test_renaming_exact_image_does_not_hide_reuse(self):
        (self.root / 'copy.pgm').write_bytes((self.root / 'one.pgm').read_bytes())
        self.manifest['scenes'][1] = self.scene('s2', 'copy.pgm')
        self.check_error(self.manifest, 'duplicate_without_reuse')

    def test_reuse_must_call_original_scene_and_file_with_reason(self):
        self.manifest['scenes'][1] = {'id': 's2', **self.asset('one.pgm'), 'style_id': 'scene-classic-rubber-hose',
                                      'visual_review': self.scene('s2','one.pgm')['visual_review'], 'reuse_of': 's1', 'intent_reason': '回到开场同一事件揭示新线索'}
        self.assertTrue(validate_manifest(self.manifest, self.root)['passed'])
        self.manifest['scenes'][1]['intent_reason'] = ''
        self.check_error(self.manifest, 'reuse_reason_missing')

    def test_reference_input_and_reference_file_are_both_bound(self):
        self.manifest['scenes'][0]['generation']['reference_images'] = []
        self.check_error(self.manifest, 'reference_not_passed')
        self.manifest['scenes'][0]['generation']['reference_images'] = [self.ref]
        self.image('sheet.pgm', 2)
        self.check_error(self.manifest, 'sha256_mismatch')

    def test_missing_character_sheet_views_and_unknown_outfit_fail(self):
        self.manifest['characters'][0]['references'][0]['views'] = ['front']
        self.check_error(self.manifest, 'missing_sheet_views')
        self.manifest['scenes'][0]['characters'][0]['outfit_id'] = 'different'
        self.check_error(self.manifest, 'unknown_character_outfit')

    def test_differently_encoded_pixels_require_bound_similarity_review(self):
        # Slight brightness change, same dHash, different decoded pixels.
        (self.root / 'near.pgm').write_bytes(b'P5\n16 16\n255\n' + bytes(x * 7 + 1 for y in range(16) for x in range(16)))
        self.manifest['scenes'][1] = self.scene('s2', 'near.pgm')
        self.check_error(self.manifest, 'near_duplicate_unreviewed')
        self.manifest['similarity_reviews'] = [{'scenes': ['s1', 's2'],
            'sha256s': [self.asset('one.pgm')['sha256'], self.asset('near.pgm')['sha256']],
            'reviewer': 'editor', 'decision': 'distinct_composition', 'notes': 'test only: explicit review bound to these files'}]
        self.assertTrue(validate_manifest(self.manifest, self.root)['passed'])
        self.manifest['similarity_reviews'][0]['sha256s'][1] = 'stale'
        self.check_error(self.manifest, 'near_duplicate_unreviewed')

    def test_lossless_reencoding_cannot_be_approved_as_distinct(self):
        source = (self.root / 'one.pgm').read_bytes()
        (self.root / 'copy.pgm').write_bytes(source.replace(b'P5\n', b'P5\n# reencoded copy\n'))
        self.manifest['scenes'][1] = self.scene('s2', 'copy.pgm')
        self.check_error(self.manifest, 'duplicate_pixels_without_reuse')

    def test_escape_wrong_style_and_archival_claim_rejected(self):
        self.manifest['scenes'][0]['image'] = '../outside.png'
        self.check_error(self.manifest, 'unsafe_path')
        self.manifest['style_id'] = 'photoreal'
        self.check_error(self.manifest, 'style_mismatch')
        self.manifest['depiction'] = 'archival_photo'
        self.check_error(self.manifest, 'depiction_mismatch')

    def test_generation_must_preserve_frozen_character_lock(self):
        self.manifest['scenes'][0]['generation']['prompt'] = 'different character'
        self.check_error(self.manifest, 'frozen_character_lock_missing')

    def test_missing_generation_and_visual_review_fail_closed(self):
        del self.manifest['scenes'][0]['generation']
        self.check_error(self.manifest, 'generation_missing')
        del self.manifest['scenes'][0]['visual_review']
        self.check_error(self.manifest, 'visual_review_missing')

    def test_bad_shapes_report_failure(self):
        for value in ({}, {'characters': None}, [], {'scenes': [None]}):
            self.assertFalse(validate_manifest(value, self.root)['passed'])


if __name__ == '__main__':
    unittest.main()

class OriginAndVideoTests(VisualContractTests):
    def test_origin_retains_generation_but_requires_new_review_and_source_hash(self):
        import copy,json
        source=self.root/'original.json';source.write_text(json.dumps(self.manifest))
        new=copy.deepcopy(self.manifest)
        new['production_scope']={'kind':'owner_approved_narration_test','authorization':'Owner requested reuse for narrative test'}
        new['scenes']=[]
        for row in self.manifest['scenes']:
            review={**row['visual_review'],'notes':'new scope: owner approved existing art narrative test'}
            new['scenes'].append({k:row[k] for k in ('id','image','sha256','style_id')})
            new['scenes'][-1].update(asset_origin={'manifest':'original.json','manifest_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'scene_id':row['id'],'reason':'owner-approved test reuses prior episode art'},visual_review=review)
        self.assertTrue(validate_manifest(new,self.root)['passed'])
        source.write_text(source.read_text()+' ')
        self.check_error(new,'asset_origin_invalid')

    def origin_test_manifest(self):
        import copy,json
        source=self.root/'original.json';source.write_text(json.dumps(self.manifest))
        new=copy.deepcopy(self.manifest)
        new['production_scope']={'kind':'owner_approved_narration_test','authorization':'fixture owner-authorized narrative test'}
        new['scenes']=[]
        for row in self.manifest['scenes']:
            new['scenes'].append({**{k:row[k] for k in ('id','image','sha256','style_id')},
                'asset_origin':{'manifest':'original.json','manifest_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'scene_id':row['id'],'reason':'fixture reuse'},
                'visual_review':{**row['visual_review'],'notes':'fresh independent review'}})
        return source,new

    def test_origin_source_validated_once_per_call_not_once_per_scene(self):
        from unittest.mock import patch
        import storyflow.visuals as visuals
        source,new=self.origin_test_manifest()
        with patch.object(visuals,'_validate_manifest',wraps=visuals._validate_manifest) as checked:
            self.assertTrue(visuals.validate_manifest(new,self.root)['passed'])
            self.assertEqual(checked.call_count,2)  # one root plus one source
            self.assertTrue(visuals.validate_manifest(new,self.root)['passed'])
            self.assertEqual(checked.call_count,4)  # no cross-call cache
        self.image('sheet.pgm',2)
        self.check_error(new,'asset_origin_invalid')  # unchanged manifest, modified original reference

    def test_origin_source_bytes_rechecked_even_after_same_call_cache_hit(self):
        from unittest.mock import patch
        import storyflow.visuals as visuals
        source,new=self.origin_test_manifest();original=visuals._validate_manifest
        def checked(value,root):
            result=original(value,root)
            if value is not new: source.write_bytes(source.read_bytes()+b' ')
            return result
        with patch.object(visuals,'_validate_manifest',side_effect=checked):
            report=visuals.validate_manifest(new,self.root)
        self.assertFalse(report['passed'])
        self.assertIn('s2:asset_origin_invalid',report['errors'])

    def test_video_rejects_static_mp4_and_changed_prediction_input(self):
        import json,subprocess
        path=self.root/'action.mp4'
        def build(source):
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',source,'-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)
        build('testsrc=size=64x64:rate=25:duration=1')
        self.manifest['characters']=[];self.manifest['scenes']=[]
        inputs={'prompt':'cartoon action','duration':10}
        (self.root/'input.json').write_text(json.dumps(inputs))
        (self.root/'response.json').write_text(json.dumps({'id':'local-fixture-only','model':'bytedance/seedance-2.5','status':'succeeded','input':inputs,'output':'https://replicate.delivery/fixture-only/synthetic.mp4'}))
        proof=lambda n:{'path':n,'sha256':hashlib.sha256((self.root/n).read_bytes()).hexdigest()}
        def scene():
            asset=self.asset('action.mp4')
            # Offline contract fixture only: neither URI nor receipt is claimed
            # to be a real provider run. Production requires a real download log.
            (self.root/'download.json').write_text(json.dumps({'prediction_id':'local-fixture-only','url':'https://replicate.delivery/fixture-only/synthetic.mp4','final_url':'https://replicate.delivery/fixture-only/synthetic.mp4','http_status':200,'sha256':asset['sha256'],'bytes':path.stat().st_size}))
            return {'id':'v1',**asset,'media_type':'video','characters':[],'style_id':'scene-classic-rubber-hose','generation':{'tool':'replicate','model':'bytedance/seedance-2.5','prediction_id':'local-fixture-only','prompt':'cartoon action','input_file':proof('input.json'),'prediction_file':proof('response.json'),'download_receipt_file':proof('download.json'),'output_sha256':asset['sha256'],'reference_images':[]},'visual_review':{'composition': {'full_frame': True, 'subtitle_reserve': False, 'bottom_edge_notes': 'synthetic fixture edge content'}, 'image_sha256':asset['sha256'],'reference_sha256s':[],'reviewer':'fixture-independent','result':'pass','notes':'test-only decoded action'}}
        self.manifest['scenes']=[scene()]
        self.assertTrue(validate_manifest(self.manifest,self.root)['passed'])
        build('color=c=red:size=64x64:rate=25:duration=1')
        self.manifest['scenes']=[scene()]
        self.check_error(self.manifest,'video_generation_evidence_invalid')
        build('testsrc=size=64x64:rate=25:duration=1')
        self.manifest['scenes']=[scene()]
        (self.root/'input.json').write_text('{}')
        self.check_error(self.manifest,'video_generation_evidence_invalid')


class VideoTransportBoundaryTests(unittest.TestCase):
    def test_only_actual_image_fields_and_uploaded_content_count(self):
        import base64
        from storyflow.visuals import verify_video_references
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);content=b'explicit unit-test-only bytes'
            sha=hashlib.sha256(content).hexdigest()
            uri='data:image/png;base64,'+base64.b64encode(content).decode()
            gen={'reference_images':[{'image':'local.png','sha256':sha}], 'input_reference_bindings':[{'sha256':sha,'input_key':'prompt','input_value':'text'}]}
            with self.assertRaises(ValueError): verify_video_references(gen,{'prompt':'text'},root)
            verify_video_references(gen,{'reference_images':[uri]},root)
            with self.assertRaises(ValueError): verify_video_references(gen,{'reference_images':[uri+'changed']},root)
            with self.assertRaises(ValueError): verify_video_references(gen,{'reference_images':['https://example.invalid/unverified.png']},root)

    def test_output_uri_and_download_identity_bytes_are_bound(self):
        import json
        from storyflow.visuals import verify_video_download
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'clip.mp4';path.write_bytes(b'unit-test-only bytes')
            sha=hashlib.sha256(path.read_bytes()).hexdigest();uri='https://replicate.delivery/fixture-only/clip.mp4'
            item={'resolved':str(path),'sha256':sha};generation={'prediction_id':'fixture-only'}
            record={'prediction_id':'fixture-only','url':uri,'final_url':uri,'http_status':200,'sha256':sha,'bytes':path.stat().st_size}
            def check(receipt,changes=None):
                proof=root/'download.json';proof.write_text(json.dumps({**record,**(changes or {})}))
                generation['download_receipt_file']={'path':'download.json','sha256':hashlib.sha256(proof.read_bytes()).hexdigest()}
                verify_video_download(generation,receipt,item,root)
            check({'output':uri})
            for output in ('https://example.invalid/clip.mp4','http://replicate.delivery/clip.mp4','https://replicate.delivery.attacker.test/clip.mp4'):
                with self.assertRaises(ValueError):check({'output':output})
            for changes in ({'prediction_id':'other'},{'url':uri+'other'},{'sha256':'0'*64},{'bytes':1000},{'http_status':404}):
                with self.assertRaises(ValueError):check({'output':uri},changes)


class ReplicateRedactedEchoTests(unittest.TestCase):
    """Offline protocol fixtures only, never supplier production evidence."""
    def setUp(self):
        import base64,json
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.content=b'fixture-only-image-bytes';sha=hashlib.sha256(self.content).hexdigest()
        self.sent={'prompt':'fixture-only cartoon action','duration':10,'reference_images':['data:image/png;base64,'+base64.b64encode(self.content).decode()],'generate_audio':False}
        self.echo={**self.sent,'reference_images':['data:image/png;base64,...']}
        self.prediction={'id':'fixture-only-prediction','created_at':'2026-10-31T13:31:59.507Z','model':'bytedance/seedance-2.5','version':'hidden','input':self.echo,'status':'succeeded'}
        self.gen={'model':self.prediction['model'],'prediction_id':self.prediction['id'],'reference_echo':'provider_redacted_reference_echo','reference_images':[{'image':'reference.png','sha256':sha}]}
        (self.root/'reference.png').write_bytes(self.content)
        self.gen['input_file']=self.proof('input.json',self.sent)
        self.operation={'kind':'replicate-video-03','status':'running','inputs':{'input_sha256':self.gen['input_file']['sha256'],'model':self.gen['model'],'version':'1'*64,'script_sha256':'2'*64}}
        self.operation['key']=hashlib.sha256(json.dumps(self.operation['inputs'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.trace={'status':'submitted','method':'POST','endpoint':'https://api.replicate.com/v1/predictions','model':self.gen['model'],'version':'1'*64,'prediction_id':self.prediction['id'],'created_at':self.prediction['created_at'],'pre_submission_created_at':'2026-10-31T13:31:58Z','reference_echo':'provider_redacted_reference_echo','redacted_reference_fields':[{'input_key':'reference_images','input_index':0,'sha256':sha}],'request_input_file':self.gen['input_file'],'pre_submission_operation_file':self.proof('operation.json',self.operation),'creation_file':self.proof('creation.json',{**self.prediction,'status':'starting'})}
        cli=self.root/'shared-cli-fixture.py';cli.write_text('# test-only snapshot, not real CLI\n')
        self.trace['shared_cli_file']={'path':cli.name,'sha256':hashlib.sha256(cli.read_bytes()).hexdigest()}
        self.trace['episode_id']='fixture-only-episode'
        self.ledger={'episode_id':'fixture-only-episode','kind':self.operation['kind'],'inputs_hash':self.operation['key'],'status':'running','created_at':self.trace['pre_submission_created_at'],'data':{'inputs':self.operation['inputs'],'prediction_id':self.prediction['id']}}
        self.trace['pre_submission_ledger_file']=self.proof('ledger.json',self.ledger)
        self.gen['submission_receipt_file']=self.proof('submission.json',self.trace)

    def proof(self,name,value):
        import json
        path=self.root/name;path.write_text(json.dumps(value,ensure_ascii=False))
        return {'path':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

    def run_check(self):
        from storyflow.visuals import verify_prediction_input
        return verify_prediction_input(self.gen,self.sent,self.prediction,self.root)

    def update_trace(self):
        self.gen['submission_receipt_file']=self.proof('submission.json',self.trace)

    def test_exact_observed_redaction_uses_original_bytes_and_explicit_trace(self):
        result=self.run_check()
        self.assertEqual(result['kind'],'provider_redacted_reference_echo')
        self.assertIn('Offline JSON',result['limitation'])
        self.assertIsNone(__import__('storyflow.visuals',fromlist=['verify_prediction_input']).verify_prediction_input(self.gen,self.sent,{**self.prediction,'input':self.sent},self.root))

    def test_missing_key_count_wrong_mime_other_scalar_and_nonliteral_echo_rejected(self):
        import copy
        variants=[]
        missing=copy.deepcopy(self.echo);del missing['reference_images'];variants.append(missing)
        for refs in ([],self.echo['reference_images']*2,['data:image/jpeg;base64,...'],['data:image/png;base64,'],['data:image/png;base64,....'],['data:image/png;base64,abc']):
            variants.append({**self.echo,'reference_images':refs})
        variants.append({**self.echo,'duration':9})
        variants.append({**self.echo,'generate_audio':True})
        variants.append({**self.echo,'generate_audio':0})
        for echo in variants:
            with self.subTest(echo=echo), self.assertRaises(ValueError):
                self.prediction['input']=echo;self.run_check()

    def test_wrong_index_sha_prediction_request_timestamp_and_cli_snapshot_rejected(self):
        import copy
        original=copy.deepcopy(self.trace)
        variants=[]
        for change in ({'input_index':1},{'input_key':'prompt'},{'sha256':'0'*64}):
            variants.append({**original,'redacted_reference_fields':[{**original['redacted_reference_fields'][0],**change}]})
        for change in ({'prediction_id':'another-id'},{'created_at':'2026-10-31T13:32:00Z'},{'pre_submission_created_at':'2026-10-31T13:32:00Z'},{'endpoint':'https://example.invalid/v1/predictions'},{'method':'GET'},{'request_input_file':{'path':'input.json','sha256':'0'*64}},{'shared_cli_file':{'path':'shared-cli-fixture.py','sha256':'0'*64}}):
            variants.append({**original,**change})
        for trace in variants:
            with self.subTest(trace=trace),self.assertRaises(ValueError):
                self.trace=trace;self.update_trace();self.run_check()

    def test_changed_submitted_image_is_not_allowed_even_with_rehashed_request(self):
        import base64
        self.sent['reference_images']=['data:image/png;base64,'+base64.b64encode(b'wrong-picture').decode()]
        self.gen['input_file']=self.proof('input.json',self.sent)
        self.trace['request_input_file']=self.gen['input_file'];self.update_trace()
        with self.assertRaises(ValueError):self.run_check()

    def test_missing_trace_and_arbitrary_prompt_reference_binding_stay_rejected(self):
        self.gen.pop('submission_receipt_file')
        with self.assertRaises(KeyError):self.run_check()
        self.gen.pop('reference_echo')
        self.gen['input_reference_bindings']=[{'input_key':'prompt','input_value':self.sent['prompt']}]
        with self.assertRaises(ValueError):self.run_check()


    def test_trace_cannot_claim_earlier_time_than_exported_ledger(self):
        self.trace['pre_submission_created_at']='1900-01-01T00:00:00Z'
        self.update_trace()
        with self.assertRaises(ValueError):self.run_check()

    def test_exported_ledger_identity_inputs_time_and_hash_bound(self):
        import copy
        original=copy.deepcopy(self.ledger)
        variants=[{**original,'episode_id':'wrong-episode'},
                  {**original,'kind':'wrong-kind'},
                  {**original,'inputs_hash':'0'*64},
                  {**original,'created_at':'2026-10-31T13:32:00Z'},
                  {**original,'data':{'inputs':{**self.operation['inputs'],'input_sha256':'0'*64}}},
                  {**original,'data':{**original['data'],'prediction_id':'wrong-id'}}]
        for ledger in variants:
            with self.subTest(ledger=ledger),self.assertRaises(ValueError):
                self.trace['pre_submission_ledger_file']=self.proof('ledger.json',ledger)
                self.update_trace();self.run_check()
        self.trace['pre_submission_ledger_file']=self.proof('ledger.json',original)
        (self.root/'ledger.json').write_text('{}')
        self.update_trace()
        with self.assertRaises(ValueError):self.run_check()
