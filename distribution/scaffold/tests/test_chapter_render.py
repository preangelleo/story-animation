"""Public parameter resolver regression; no provider requests or personal fonts required."""
import json
from pathlib import Path
import tempfile
import unittest
from storyflow.chapter_render import DEFAULTS, composition, effective
from storyflow.cli import parser
from storyflow.core import WorkflowError

FONT='/usr/share/fonts/TTF/DejaVuSans.ttf'
import subprocess

class Parameters(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.font=Path(subprocess.run(['fc-match','-f','%{file}','DejaVu Sans'],capture_output=True,text=True,check=True).stdout)
        if not self.font.is_file(): self.skipTest('DejaVu font unavailable')
        self.preset=self.root/'preset.json'
        self.values=dict(title_font=str(self.font),number_font=str(self.font),sound_path=None,title_text='Test',chapter_number='II')
        self.write()
    def write(self): self.preset.write_text(json.dumps(self.values))
    def resolve(self,**kw): return effective(self.root,self.preset,kw)
    def test_portable_assets_and_all_effective_values(self):
        (self.root/'title.ttf').write_bytes(self.font.read_bytes());self.values['title_font']='title.ttf';self.write()
        v,p=self.resolve(background_color='#123456',title_color='#abcdef',number_color='#fedcba',line_color='#654321',title_family='OwnTitle',number_family='OwnNumber',layout='b',line_x=17,line_y=19,line_height=220,line_gap=3,duration=1.5,fps=24)
        source=composition(v)
        for token in ('#123456','#abcdef','#fedcba','#654321','OwnTitle','OwnNumber','left:17px','top:19px','height:220px','margin-top:3px','data-duration="1.5"','data-fps="24"'):
            self.assertIn(token,source)
        self.assertEqual(p['fonts']['title']['sha256'],p['fonts']['number']['sha256'])
        self.assertNotIn('https://',source)
    def test_unicode_family_aliases_consumed_and_injection_rejected(self):
        v,_=self.resolve(title_family='思源宋体 自定义-2',number_family='カスタム書体_3')
        source=composition(v)
        for family in ('思源宋体 自定义-2','カスタム書体_3'):
            self.assertIn('font-family:"'+family+'"',source)
            self.assertIn('px "'+family+'"',source)
        for family in ('1思源宋体','思源;宋体','思源"宋体',"思源'宋体",'思源(宋体)','思源<宋体>','思源\n宋体','思源\t宋体','字'*81):
            with self.subTest(family=family),self.assertRaisesRegex(WorkflowError,'unsafe font family'):
                self.resolve(title_family=family)
    def test_cli_fractional_and_booleans(self):
        a=parser().parse_args(['chapters','render','--output','clip.mp4','--sound-volume','.2','--duration','1.5','--no-line-enabled','--muted'])
        self.assertEqual(a.sound_volume,.2);self.assertEqual(a.duration,1.5);self.assertFalse(a.line_enabled);self.assertTrue(a.muted)
    def test_reject_invalid(self):
        for kw in [dict(no_such_parameter=1),dict(background_color='red'),dict(title_family="bad';url(x)"),dict(title_font='/missing/font.ttf'),dict(duration=float('nan')),dict(duration=1.01),dict(width=101),dict(fps=0),dict(font_weight=700),dict(sound_volume=-1),dict(number_line_height=float('nan')),dict(line_enabled=1),dict(title_text='')]:
            with self.subTest(kw=kw),self.assertRaises((WorkflowError,OSError,ValueError)):self.resolve(**kw)
    def test_wrong_and_missing_glyph_font_rejected(self):
        bad=self.root/'fake.ttf';bad.write_text('not a font')
        with self.assertRaises(WorkflowError):self.resolve(title_font=str(bad))
        with self.assertRaises(WorkflowError):self.resolve(title_text='豪门惊夜')
    def test_schema_exact_keys_and_defaults(self):
        base=Path(__file__).resolve().parents[1]/'templates/chapter-transition'
        schema=json.loads((base/'parameters.schema.json').read_text())
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual({k:p['default'] for k,p in schema['properties'].items()},DEFAULTS)
        self.assertEqual(json.loads((base/'default-preset.json').read_text()),DEFAULTS)
    def test_muted_and_line_disable_are_explicit(self):
        v,_=self.resolve(muted=True,line_enabled=False)
        self.assertIn('display:none',composition(v))
        self.assertNotIn('<audio',composition(v)) # Audio handled explicitly during mux.
    def test_reject_both_explicit_sound_sources(self):
        with self.assertRaisesRegex(WorkflowError,'mutually exclusive'):
            self.resolve(sound_name='cached',sound_path=str(self.font))
        self.values.update(sound_name='cached',sound_path=str(self.font));self.write()
        with self.assertRaisesRegex(WorkflowError,'mutually exclusive'):self.resolve()
    def test_explicit_path_replaces_preset_name(self):
        self.values.update(sound_name='cached',sound_path=None);self.write()
        v,p=self.resolve(sound_path=None)
        self.assertIsNone(v['sound_name']);self.assertIsNone(p['sound'])
    def test_muted_does_not_require_default_sound(self):
        self.values['sound_path']='/missing/sound.mp3';self.write()
        v,p=self.resolve(muted=True)
        self.assertIsNone(v['sound_path']);self.assertIsNone(p['sound'])
    def test_layout_no_private_owner_gate(self):
        v,_=self.resolve(layout='b',title_family='UserFont')
        self.assertEqual(v['layout'],'b')
        self.assertEqual(v['group_x'],370)
        self.assertEqual(v['group_y'],393)
        self.assertEqual(self.resolve(layout='b',group_x=77)[0]['group_x'],77)
        self.assertIn('UserFont',composition(v))

if __name__=='__main__':unittest.main()
