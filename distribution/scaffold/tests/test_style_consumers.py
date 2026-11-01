"""No media purchases: custom catalog through real composition/assembly/validator."""
import copy,json,shutil,tempfile,unittest
from pathlib import Path
from storyflow.core import Store,WorkflowError,atomic_json
from storyflow.style_registry import resolve_style
from storyflow.visual_prompt import compose_prompt
from storyflow.manifest_ops import assemble
from storyflow.visuals import validate_manifest
from tests import test_visuals

PROJECT=Path(__file__).resolve().parents[1]

class StyleConsumerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_visuals.VisualContractTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.root=self.fixture.root
        shutil.copytree(PROJECT/'config',self.root/'config')
        atomic_json(self.root/'documentation/configuration.json',{'style_registry_file':'config/styles.json','series_mapping_file':'config/weekly_series.json'})
        catalog=json.loads((self.root/'config/styles.json').read_text())
        custom=copy.deepcopy(catalog['styles'][0]);custom.update(index='CUSTOM27',id='custom-etched-night',prompt='Original etched crosshatching and indigo pigments, soft amber light.')
        catalog['styles'].append(custom);atomic_json(self.root/'config/styles.json',catalog)
        self.store=Store(self.root);self.addCleanup(self.store.close)

    def test_custom27_compose_assemble_validate_and_frozen_edit(self):
        ep=self.store.create('2026-11-01','fixture',None,'custom27',review_only=True,style_index='CUSTOM27')
        snap=ep['style_snapshot'];self.assertEqual(ep['series'],snap['series_key'])
        self.assertEqual(snap['release_date_tokyo'],'2026-11-03')
        bindings={'style_id':snap['style_id'],'style_snapshot_sha256':snap['snapshot_sha256']}
        m=copy.deepcopy(self.fixture.manifest)
        for char in m['characters']:
            char.update(bindings)
            for ref in char['references']:ref.update(bindings,generation={**bindings,'prompt':compose_prompt('character model sheet',snapshot=snap)})
        p=self.root/'episodes/custom27/production';w=p.parent/'work/visuals';w.mkdir(parents=True)
        scenes=[];reviews=[]
        for row in m['scenes']:
            row.update(bindings);row['generation'].update(bindings,prompt=compose_prompt('locked character wearing cream shirt at a distinct location',snapshot=snap))
            scenes.append({**row,'beat':'fixture beat','media_type':'image','prompt':row['generation']['prompt']})
            atomic_json(p/'image-generation'/f"{row['id']}.json",row)
            reviews.append({'id':row['id'],**row['visual_review']})
        plan={**bindings,'style_snapshot':snap,'characters':m['characters'],'scenes':scenes}
        atomic_json(p/'visual-plan.json',plan);atomic_json(w/'image-reviews.json',reviews);atomic_json(w/'video-reviews.json',[])
        self.assertTrue(assemble(self.root,'custom27')['ok'])
        manifest=json.loads((p/'visual-manifest.json').read_text())
        report=validate_manifest(manifest,self.root);self.assertTrue(report['passed'],report)
        self.assertEqual(self.store.visual_contract('custom27')['style_snapshot'],snap)
        reuse=copy.deepcopy(manifest)
        first=reuse['scenes'][0]
        reuse['scenes'][1]={**first,'id':'s2','reuse_of':'s1','intent_reason':'same episode callback'}
        reuse['scenes'][1].pop('generation')
        self.assertTrue(validate_manifest(reuse,self.root)['passed'])
        reuse['scenes'][1]['style_snapshot_sha256']='0'*64
        self.assertFalse(validate_manifest(reuse,self.root)['passed'])
        # Changing only destination tags must not re-label another snapshot's source.
        origin=copy.deepcopy(manifest)
        old=copy.deepcopy(manifest)
        from storyflow.style_registry import _sha
        old['style_snapshot']['release_date_tokyo']='2026-11-04'
        old['style_snapshot']['snapshot_sha256']=_sha({k:v for k,v in old['style_snapshot'].items() if k!='snapshot_sha256'})
        oldhash=old['style_snapshot']['snapshot_sha256']
        old['style_snapshot_sha256']=oldhash
        for char in old['characters']:
            char['style_snapshot_sha256']=oldhash
            for ref in char['references']:
                ref['style_snapshot_sha256']=oldhash;ref['generation']['style_snapshot_sha256']=oldhash
        for scene in old['scenes']:
            scene['style_snapshot_sha256']=oldhash;scene['generation']['style_snapshot_sha256']=oldhash
        atomic_json(self.root/'source-manifest.json',old)
        from storyflow.core import digest
        row=origin['scenes'][0];row.pop('generation');row['visual_review']['reviewer']='fresh-independent-review'
        row['asset_origin']={'manifest':'source-manifest.json','manifest_sha256':digest(self.root/'source-manifest.json'),'scene_id':'s1','reason':'test style drift rejection'}
        origin['production_scope']={'kind':'owner_approved_narration_test','authorization':'offline test only'}
        result=validate_manifest(origin,self.root)
        self.assertFalse(result['passed']);self.assertIn('s1:asset_origin_invalid',result['errors'])
        catalog=json.loads((self.root/'config/styles.json').read_text());catalog['styles'][-1]['prompt']='New future stippling style.';atomic_json(self.root/'config/styles.json',catalog)
        self.assertEqual(self.store.visual_contract('custom27')['style_snapshot'],snap)
        self.assertNotEqual(resolve_style(self.root,series=ep['series'],style_index='CUSTOM27')['prompt'],snap['prompt'])
        manifest['scenes'][0]['generation']['style_snapshot_sha256']='0'*64
        self.assertFalse(validate_manifest(manifest,self.root)['passed'])
        manifest['style_snapshot']=resolve_style(self.root,series=ep['series'],style_index='CUSTOM27')
        atomic_json(p/'visual-manifest.json',manifest)
        with self.assertRaises(WorkflowError):self.store.visual_contract('custom27')

    def test_override_preserves_series_and_unknown_never_falls_back(self):
        ep=self.store.create('2026-11-01','fixture','other','override',review_only=True,style_index='CUSTOM27',classification_evidence='Independent topic outside the six primary categories')
        self.assertEqual(ep['series'],'other');self.assertEqual(ep['style_index'],'CUSTOM27')
        with self.assertRaises(ValueError):self.store.create('2026-11-01','bad','other','bad',review_only=True,style_index='typo',classification_evidence='fixture')
        with self.assertRaises(ValueError):self.store.create('2026-11-01','bad','typo','bad',review_only=True)

    def test_each_release_weekday_and_cross_month(self):
        mapping=json.loads((self.root/'config/weekly_series.json').read_text())
        import datetime as dt
        for i in range(7):
            production=dt.date(2026,10,30)+dt.timedelta(days=i)
            ep=self.store.create(str(production),'fixture',None,'week'+str(i),review_only=True)
            release=production+dt.timedelta(days=2)
            row=next(x for x in mapping['series'] if x['weekday']==release.isoweekday())
            self.assertEqual(ep['series'],row['key']);self.assertEqual(ep['style_index'],row['default_style_index'])
        snap=resolve_style(self.root,release_date='2026-11-01T23:30:00-04:00')
        self.assertEqual(snap['release_date_tokyo'],'2026-11-02')

if __name__=='__main__':unittest.main()
