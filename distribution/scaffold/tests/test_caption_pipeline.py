import unittest
from storyflow.integrations import alignment_srt


def alignment(text):
    return {'characters':[{'text':ch,'start':i*.2,'end':(i+1)*.2} for i,ch in enumerate(text)]}

class CaptionPipelineTests(unittest.TestCase):
    def test_no_punctuation_or_orphan_after_hard_boundary(self):
        text='这是一个超过二十四个字符而且没有逗号的长句最后留下单字了。'
        value=alignment_srt(alignment(text),text)
        texts=[b.splitlines()[2] for b in value.strip().split('\n\n')]
        self.assertTrue(all('。' not in t for t in texts))
        self.assertTrue(all(len(t.replace(' ',''))>=2 for t in texts))

    def test_semantic_units_are_exactly_bound_to_original_times(self):
        text='钥匙在这里。但是门锁换了。'
        value=alignment_srt(alignment(text),text,caption_units=[{'index':0,'text':'钥匙在这里'},{'index':1,'text':'但是门锁换了'}])
        self.assertIn('00:00:00,000 --> 00:00:01,200',value)
        self.assertIn('00:00:01,200 --> 00:00:02,600',value)
        with self.assertRaises(Exception):
            alignment_srt(alignment(text),text,caption_units=[{'text':'错字'}])

    def test_short_cue_merges_without_shifting_later_cues(self):
        text='他。打开大门。最后走开。'
        value=alignment_srt(alignment(text),text)
        self.assertNotIn('\n他\n',value)
        self.assertIn('00:00:01,400 --> 00:00:02,400',value)

    def test_immutable_source_and_endpoints_on_long_sequence(self):
        import copy
        from storyflow.captions import display_captions, compact
        data=alignment('他说：“这是一封旧信。”然后关门。'*30)
        before=copy.deepcopy(data)
        cues,report=display_captions(data['characters'])
        self.assertEqual(data,before)
        starts={c['start'] for c in data['characters']};ends={c['end'] for c in data['characters']}
        self.assertTrue(all(c['start'] in starts and c['end'] in ends for c in cues))
        self.assertEqual(compact(''.join(c['text'] for c in cues)),compact(''.join(c['text'] for c in data['characters'])))
        self.assertEqual(report['srt_offset_seconds'],0)

    def test_burn_gate_rejects_old_punctuation_and_flash(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from storyflow.captions import validate_srt
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.srt'
            for text,end in [('字。','900'),('字','900'),('两个','100')]:
                path.write_text(f'1\n00:00:00,000 --> 00:00:00,{end}\n{text}\n')
                with self.assertRaises(ValueError):validate_srt(path)
            path.write_text('1\n00:00:00,000 --> 00:00:00,900\n钥匙\n')
            self.assertEqual(validate_srt(path)['short_cues'],0)

    def test_impossible_orphan_fails_instead_of_retiming(self):
        from storyflow.captions import display_captions
        with self.assertRaises(ValueError):display_captions(alignment('字。')['characters'])

    def test_latin_words_keep_spaces_and_unicode_punctuation_cleans(self):
        text='他说 Hello world——你好／再见。'
        value=alignment_srt(alignment(text),text)
        self.assertIn('Hello world',value)
        self.assertNotIn('／',value)
        self.assertNotIn('——',value)
