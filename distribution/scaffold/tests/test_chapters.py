import tempfile
import unittest
from pathlib import Path
from storyflow.chapters import map_time, map_intervals, timeline_plan, pending_gate, require_owner_choice, sha256
from storyflow.core import WorkflowError

class ChapterTests(unittest.TestCase):
    def setUp(self):
        self.items=[{'source_time': 10, 'duration': 1}, {'source_time': 20, 'duration': 2}]

    def test_exact_before_at_after(self):
        for t, before, after in [('9.999', '9.999', '9.999'), (10,10,11), ('10.001','11.001','11.001'), ('19.999','20.999','20.999'), (20,21,23), ('20.001','23.001','23.001')]:
            self.assertEqual(str(map_time(t,self.items,'before')),str(before))
            self.assertEqual(str(map_time(t,self.items,'after')),str(after))

    def test_every_stream_is_split_no_bleed(self):
        streams={name:[{'start':9,'end':21,'id':'crossing'}] for name in ('narration','subtitles','shots','sfx')}
        with self.assertRaises(WorkflowError):timeline_plan({'source_duration':30,'insertions':self.items,'streams':streams})
        streams.pop('shots')
        p=timeline_plan({'source_duration':30,'insertions':self.items,'streams':streams})
        self.assertEqual(p['delivery_duration'],33)
        for name,values in p['streams'].items():
            if name=='shots':continue
            self.assertEqual([(v['start'],v['end']) for v in values],[(9,10),(11,21),(23,24)])
            self.assertEqual(sum(v['source_end']-v['source_start'] for v in values),12)
            for v in values:
                for gap in p['transitions']:
                    self.assertFalse(v['start'] < gap['end'] and v['end'] > gap['start'])

    def test_end_exact_boundary_and_start_exact_boundary(self):
        self.assertEqual(map_intervals([{'start':9,'end':10},{'start':10,'end':11}],self.items)[1]['start'],11)
        self.assertEqual(map_intervals([{'start':9,'end':10}],self.items)[0]['end'],10)

    def test_negative_controls(self):
        for items in ([{'source_time':10,'duration':0}], [{'source_time':10,'duration':3}], self.items[::-1], [self.items[0]]*2):
            with self.assertRaises(WorkflowError): map_time(4,items)
        for t in ('NaN',-1,'Infinity'):
            with self.assertRaises(WorkflowError): map_time(t,self.items)
        with self.assertRaises(WorkflowError): timeline_plan({'source_duration':10,'insertions':self.items})

    def test_gate_pending_and_hash_drift_fail(self):
        with tempfile.TemporaryDirectory() as td:
            a,b=Path(td)/'template',Path(td)/'sound'; a.write_text('a');b.write_text('b')
            with self.assertRaises(WorkflowError): require_owner_choice(pending_gate(),a,b)
            gate={'status':'approved','actor':'owner','owner_choice_evidence':'owner real message reference','template_sha256':sha256(a),'sound_sha256':sha256(b)}
            with self.assertRaisesRegex(WorkflowError, 'legacy or missing font'):
                require_owner_choice(gate,a,b)
            b.write_text('changed')
            with self.assertRaises(WorkflowError): require_owner_choice(gate,a,b)
            gate['actor']='agent'
            with self.assertRaises(WorkflowError): require_owner_choice(gate,a,b)
