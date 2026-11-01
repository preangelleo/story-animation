"""All narration providers require actual current ASR evidence before FA."""
import unittest
from unittest.mock import patch
from storyflow import integrations as it
from storyflow.core import Store,WorkflowError,atomic_json,digest
from tests import test_integrations as fixtures
class ASRGateTests(unittest.TestCase):
 def setUp(self):
  self.fx=fixtures.IntegrationTests('test_voice_plan_never_executes');self.fx.setUp();self.s=self.fx.store;self.r=self.fx.root;self.ep=self.fx.ep
  self.work=it.folder(self.s,self.ep);audio=self.fx.file('voice.mp3','synthetic audio');self.s.register(self.ep,'audio',audio)
  arts=self.s.artifacts(self.ep);self.inputs={k+'_sha256':arts[k]['sha256'] for k in ('script','audio')}
  atomic_json(self.work/'voice.binding.json',{'inputs':self.inputs,'audio_sha256':digest(audio)})
  cfg=self.s.config;cfg['voice']={'provider':'elevenlabs'};atomic_json(self.r/'documentation/configuration.json',cfg)
  self.args=self.fx.args();self.args.execute=True;self.args.confirm_cost=True;self.args.estimated_usd=.5
 def tearDown(self):self.fx.tearDown()
 def audit(self):
  asr=self.work/'asr/whisper-medium.json';comparison=self.work/'asr/comparison.json'
  atomic_json(asr,{'audio_sha256':self.inputs['audio_sha256'],'text':'钥匙。'})
  atomic_json(comparison,{'script_sha256':self.inputs['script_sha256'],'asr_sha256':digest(asr),'changes':[]})
  audit={'status':'pass','reviewer':'independent-fixture',**self.inputs,'asr_sha256':digest(asr),'comparison_sha256':digest(comparison)}
  atomic_json(self.work/'asr/fulltext-audit.json',audit);return audit
 def test_elevenlabs_missing_audit_rejected_before_supplier(self):
  with patch.object(it,'run') as external,self.assertRaises(WorkflowError):it.align(self.args)
  external.assert_not_called()

 def provider(self,argv,**kw):
  atomic_json(argv[-1],{'characters':[{'text':'钥','start':.1,'end':.3},{'text':'匙','start':.4,'end':.8},{'text':'。','start':.8,'end':.8}]});return ''
 def test_valid_elevenlabs_audit_aligns_and_replays_without_second_post(self):
  self.audit()
  with patch.object(it,'run',side_effect=self.provider) as external,patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):
   result=it.align(self.args);self.assertEqual(result['status'],'done')
   self.assertTrue(it.align(self.args)['idempotent']);self.assertEqual(external.call_count,1)
 def test_stale_or_unreviewed_evidence_never_calls_fa(self):
  from storyflow.core import read_json
  for mutation in ('audio','script','transcript','comparison','reviewer'):
   audit=self.audit()
   if mutation in ('audio','script'):audit[mutation+'_sha256']='0'*64
   elif mutation=='reviewer':audit['reviewer']=' '
   elif mutation=='transcript':atomic_json(self.work/'asr/whisper-medium.json',{'audio_sha256':'0'*64})
   else:atomic_json(self.work/'asr/comparison.json',{'script_sha256':'0'*64})
   atomic_json(self.work/'asr/fulltext-audit.json',audit)
   with self.subTest(mutation=mutation),patch.object(it,'run') as external,self.assertRaises(WorkflowError):it.align(self.args)
   external.assert_not_called()
 def test_unknown_supplier_operation_cannot_repost(self):
  self.audit()
  with patch.object(it,'run',side_effect=WorkflowError('supplier uncertain')) as external:
   with self.assertRaises(WorkflowError):it.align(self.args)
   with self.assertRaises(WorkflowError):it.align(self.args)
   self.assertEqual(external.call_count,1)
  self.assertEqual(self.fx.readops()[0]['status'],'unknown')
 def test_reconcile_requires_same_independent_audit_without_http(self):
  key,_=it.begin(self.s,self.ep,'align',self.inputs,.5)
  it.finish(self.s,self.ep,'align',key,'unknown',{'inputs':self.inputs})
  self.args.execute=False;self.args.reconcile=True
  with patch.object(it,'run') as external,self.assertRaises(WorkflowError):it.align(self.args)
  external.assert_not_called()
 def test_audit_mutation_during_supplier_call_becomes_unknown(self):
  self.audit()
  def supplier(argv,**kw):
   self.provider(argv,**kw)
   audit=self.audit();audit['reviewer']='different independent fixture';atomic_json(self.work/'asr/fulltext-audit.json',audit)
  with patch.object(it,'run',side_effect=supplier),self.assertRaises(WorkflowError):it.align(self.args)
  self.assertEqual(self.fx.readops()[0]['status'],'unknown')

 def test_matching_file_digests_do_not_hide_wrong_audio_or_script(self):
  for target in ('asr','comparison'):
   audit=self.audit()
   if target=='asr':
    f=self.work/'asr/whisper-medium.json';atomic_json(f,{'audio_sha256':'0'*64,'text':'wrong audio fixture'});audit['asr_sha256']=digest(f)
   else:
    f=self.work/'asr/comparison.json';atomic_json(f,{'script_sha256':'0'*64,'asr_sha256':audit['asr_sha256']});audit['comparison_sha256']=digest(f)
   atomic_json(self.work/'asr/fulltext-audit.json',audit)
   with self.subTest(target=target),patch.object(it,'run') as external,self.assertRaises(WorkflowError):it.align(self.args)
   external.assert_not_called()

class PreFASemanticReviewTests(ASRGateTests):
 def setUp(self):
  super().setUp()
  cfg=self.s.config;cfg['quality']['require_semantic_caption_units']=True;atomic_json(self.r/'documentation/configuration.json',cfg)
  atomic_json(self.work/'caption_units.json',[{'index':0,'text':'钥匙'}]);self.semantic_review()
 def semantic_review(self,**changes):
  from storyflow.core import read_json
  arts=self.s.artifacts(self.ep);review=read_json(self.r/arts['review']['path'])
  review.update(caption_units_sha256=digest(self.work/'caption_units.json'),caption_review={'status':'pass','reason':'independent synthetic unit review'})
  review.update(changes);self.s.record_review(self.ep,self.fx.file('pre-fa-semantic-review.json',review))
 def test_missing_semantic_review_rejected_before_paid_fa(self):
  self.audit();self.semantic_review(caption_units_sha256=None,caption_review=None)
  with patch.object(it,'run',side_effect=self.provider) as external,patch.object(Store,'_probe',return_value={'duration':1,'streams':[]}):
   with self.assertRaises(WorkflowError):it.align(self.args)
   self.assertEqual(external.call_count,0)

 def test_stale_units_and_invalid_caption_review_cost_zero(self):
  self.audit()
  for fields in ({'caption_units_sha256':'0'*64},{'caption_review':{'status':'revise','reason':'revise'}},{'caption_review':{'status':'pass','reason':' '}}):
   self.semantic_review(**fields)
   with self.subTest(fields=fields),patch.object(it,'run') as external,self.assertRaises(WorkflowError):it.align(self.args)
   external.assert_not_called();self.assertEqual(self.fx.readops(),[])
 def test_missing_units_cost_zero(self):
  self.audit()
  (self.work/'caption_units.json').unlink()
  with patch.object(it,'run') as external,self.assertRaises(WorkflowError):it.align(self.args)
  external.assert_not_called();self.assertEqual(self.fx.readops(),[])
