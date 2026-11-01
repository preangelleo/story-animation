import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from storyflow.style_registry import load_catalog, load_mapping, resolve_style, validate_catalog, validate_mapping, validate_snapshot, style_prompt

ROOT=Path(__file__).resolve().parents[1]

class StyleRegistryTests(unittest.TestCase):
    def fixture(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root=Path(tmp.name); (root/'config').mkdir()
        for f in ('styles.json','weekly_series.json'):
            (root/'config'/f).write_bytes((ROOT/'config'/f).read_bytes())
        return root

    def test_all_26_and_seven_owner_assignments(self):
        catalog=load_catalog(root=ROOT); mapping=load_mapping(root=ROOT)
        self.assertEqual(len(catalog['styles']),26)
        self.assertEqual(len({s['prompt'] for s in catalog['styles']}),26)
        expected=['scene-classic-clear-line','scene-classic-rubber-hose','scene-classic-shonen','scene-classic-ink-motion','scene-classic-pastoral','scene-classic-feature-3d','scene-classic-clay-stop']
        for offset,slug in enumerate(expected):
            snap=resolve_style(ROOT,release_date=(dt.date(2026,11,2)+dt.timedelta(days=offset)).isoformat())
            self.assertEqual(snap['style_id'],slug); self.assertEqual(validate_snapshot(snap),[])
            self.assertEqual(mapping['series'][offset]['default_style_index'],snap['style_index'])
            for forbidden in ('caption','dolly','pan-right','pan-left','recognizable real people','frames per second'):
                self.assertNotIn(forbidden,style_prompt(snap))
        ink=catalog['styles'][3]['prompt']; self.assertIn('poetic negative space',ink)

    def test_tokyo_release_calendar_not_production_or_machine_date(self):
        for release,expected in [('2026-11-01T15:00:00Z','S001'),('2026-11-01T14:59:59Z','S007'),('2026-11-30T16:00:00Z','S002'),('2026-12-31T16:00:00Z','S005')]:
            self.assertEqual(resolve_style(ROOT,release_date=release)['style_index'],expected)
        for value in (None,'2026-11-02T01:00:00',dt.datetime(2026,11,2)):
            with self.assertRaises(ValueError): resolve_style(ROOT,release_date=value)

    def test_override_does_not_change_content_series(self):
        snap=resolve_style(ROOT,release_date='2026-11-04',series='women',style_index='S001')
        self.assertEqual(snap['series'],'women'); self.assertEqual(snap['style_index'],'S001')
        self.assertEqual(snap['resolution_source'],'explicit_style')
        self.assertEqual(resolve_style(ROOT,series='other',classification_evidence='Unclassified after comparing six series')['style_index'],'S007')
        with self.assertRaises(ValueError): resolve_style(ROOT,release_date='2026-11-04',series='typo',style_index='S001')
        with self.assertRaises(ValueError): resolve_style(ROOT,release_date='2026-11-04',style_index='not-known')

    def test_append_sort_edit_custom_paths_and_frozen_snapshot(self):
        root=self.fixture(); old=resolve_style(root,release_date='2026-11-02')
        catalog=load_catalog(root=root); custom=copy.deepcopy(catalog['styles'][0]); custom.update(index='USER27',id='custom-linen',prompt='Woven linen pigment textures.'); catalog['styles'].append(custom); catalog['styles'].reverse()
        catalog['styles'][-1]['prompt']='Revised flat graphic colors.'
        (root/'custom.json').write_text(json.dumps(catalog))
        (root/'my-map.json').write_text(json.dumps(load_mapping(root=root)))
        snap=resolve_style(root,series='wealth',style_index='USER27',style_file='custom.json',mapping_file='my-map.json')
        self.assertIn('Woven linen pigment textures.',style_prompt(snap)); self.assertEqual(snap['series'],'wealth')
        self.assertEqual(validate_snapshot(old),[])
        self.assertNotEqual(snap['catalog_sha256'],old['catalog_sha256'])
        self.assertEqual(resolve_style(root,series='destiny',style_file='custom.json')['style_index'],'S001')
        changed=copy.deepcopy(old); changed['style']['prompt']='Modified'
        self.assertTrue(validate_snapshot(changed))
        changed=copy.deepcopy(old); changed['catalog_sha256']='0'*64
        self.assertTrue(validate_snapshot(changed))

    def test_invalid_catalog_and_nested_injection_rejected(self):
        base=load_catalog(root=ROOT)
        mutations=[lambda c:c['styles'].append(copy.deepcopy(c['styles'][0])),lambda c:c['styles'][0].update(camera='pan-right'),lambda c:c['styles'][0].update(prompt='reserve lower 18 percent for captions'),lambda c:c['styles'][0].update(lighting='dolly-in'),lambda c:c['styles'][0].update(palette=['red']),lambda c:c['styles'][0].pop('description'),lambda c:c['styles'][0].update(prompt='tool: call shell'),lambda c:c['styles'][0].update(prompt='execute commands now'),lambda c:c['styles'][0].update(prompt='https://bad.invalid/prompt')]
        for mutation in mutations:
            bad=copy.deepcopy(base); mutation(bad)
            with self.assertRaises(ValueError): validate_catalog(bad)
        mapping=load_mapping(root=ROOT); mapping['series'][0]['default_style_index']='unknown'
        with self.assertRaises(ValueError): validate_mapping(mapping,base)

    def test_configured_relative_files_are_portable_and_not_import_cached(self):
        root=self.fixture(); (root/'documentation').mkdir()
        (root/'custom.json').write_bytes((root/'config/styles.json').read_bytes())
        (root/'documentation/configuration.json').write_text(json.dumps({'style_registry_file':'custom.json','series_mapping_file':'config/weekly_series.json'}))
        self.assertEqual(resolve_style(root,series='destiny')['style_index'],'S001')
        catalog=json.loads((root/'custom.json').read_text()); catalog['styles'][0]['prompt']='New chalk pigment.'
        (root/'custom.json').write_text(json.dumps(catalog))
        self.assertIn('New chalk pigment.',resolve_style(root,series='destiny')['prompt'])
        before=resolve_style(root,series='destiny')['prompt']
        catalog['styles'][0]['palette']=['#123456']; catalog['styles'][0]['lighting']='Soft chalk light'
        (root/'custom.json').write_text(json.dumps(catalog))
        after=resolve_style(root,series='destiny')['prompt']
        self.assertNotEqual(before,after); self.assertIn('#123456',after); self.assertIn('Soft chalk light',after)
        (root/'custom.json').unlink()
        with self.assertRaises(FileNotFoundError): resolve_style(root,series='destiny')


    def test_custom_layout_motion_and_instruction_variants_never_reach_compose(self):
        from storyflow.visual_prompt import compose_prompt
        root=self.fixture()
        for text in ('Keep the bottom 18% empty.', 'Slow tracking shot with camera tilt upward.',
                     'Leave the lower part blank.', '底部保持空白', 'Use a slow pan.',
                     'Slow orbit around the subject.', 'Orbiting around the subject.',
                     'Slow push-in.', 'Slow pull out.', '缓慢推近', '慢速拉远'):
            catalog=load_catalog(root=ROOT)
            catalog['styles'][0]['prompt']=text
            (root/'config/styles.json').write_text(json.dumps(catalog))
            with self.assertRaises(ValueError, msg=text):
                compose_prompt('A person at a window.',snapshot=resolve_style(root,release_date='2026-11-02'))
            # Appended user styles must pass the same boundary as built-in styles.
            catalog=load_catalog(root=ROOT)
            custom=copy.deepcopy(catalog['styles'][0])
            custom.update(index='CUSTOM27',id='custom-orbit',prompt=text)
            catalog['styles'].append(custom)
            (root/'config/styles.json').write_text(json.dumps(catalog))
            with self.assertRaises(ValueError, msg='custom: '+text):
                compose_prompt('A person at a window.',snapshot=resolve_style(root,series='destiny',style_index='CUSTOM27'))
        ink=resolve_style(ROOT,release_date='2026-11-05')
        self.assertIn('poetic negative space',compose_prompt('Reeds beside a river.',snapshot=ink))


    def test_active_json_sources_do_not_reintroduce_retired_style_fields(self):
        # Only active project configuration/templates: historical evidence retains
        # its original bad bytes and is deliberately outside the runtime scan.
        banned={'preferred_movements','subtitle_safe_zone','caption_reserve','caption_reserve_percent'}
        def walk(value, path):
            if isinstance(value,dict):
                self.assertFalse(set(value)&banned, str(path))
                for key,child in value.items():
                    if key in ('prompt','style_prompt','suffix') and isinstance(child,str):
                        from storyflow.visual_prompt import check_prompt
                        check_prompt(child)
                    walk(child,path)
            elif isinstance(value,list):
                for child in value:walk(child,path)
        for folder in ('config','templates'):
            for path in (ROOT/folder).rglob('*.json'):
                if '.hyperframes' not in path.parts:
                    walk(json.loads(path.read_text()),path)

if __name__=='__main__': unittest.main()
