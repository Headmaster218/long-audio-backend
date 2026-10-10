import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('continuous_offline_score', ROOT/'score.py')
metric = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metric)
def record(text, status='success', truncated=False):
    return dict(text=text, status=status, truncated=truncated)

class SnapshotTests(unittest.TestCase):
    def test_whole_excerpt(self):
        result = metric.score_excerpt('Ａbc!', [record('a'), record('bc')])
        self.assertEqual(result['errors'], 0)
        self.assertEqual(result['reference_characters'], 3)

    def test_truncated_and_failure(self):
        result = metric.score_excerpt('abc', [record('ab', 'truncated', True),
                                             record('c', 'model_failure')])
        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['deletions'], 1)
        self.assertEqual(result['truncations'], 1)
        self.assertEqual(result['failures'], 1)

    def test_ties_and_empty(self):
        result = metric.score_excerpt('ab', [record('ba')])
        self.assertEqual(result['substitutions'], 2)
        result = metric.score_excerpt('abc', [record('', 'not_attempted')])
        self.assertEqual(result['deletions'], 3)
        self.assertIsNone(metric.score_excerpt('', [record('')])['cer'])

    def test_rejects_invalid(self):
        for records in ([], [record('', 'other')], [record('', 'success', True)],
                        [record('', 'truncated', False)], [{'text':'a'}]):
            with self.assertRaises(ValueError): metric.score_excerpt('a', records)
        with self.assertRaises(ValueError):
            metric.score_excerpt('a'*5000, [record('b'*5000)])

    def test_continuous_coverage(self):
        data = json.loads((ROOT/'selection.json').read_text())
        self.assertEqual(len(data['excerpts']), 8)
        self.assertEqual(len(data['windows']), 41)
        self.assertEqual(sum(x['frames'] for x in data['windows']), 11734160)
        self.assertEqual(sum(x['reference_characters'] for x in data['excerpts']), 4182)
        for e in data['excerpts']:
            ws = [w for w in data['windows'] if w['meeting'] == e['meeting']]
            self.assertEqual(len(ws), e['calls_per_backend'])
            self.assertEqual(ws[0]['start_frame'], e['start_frame'])
            self.assertEqual(ws[-1]['end_frame'], e['end_frame'])
            self.assertEqual(e['partial_outer_turns'], 0)
            self.assertTrue(60 <= e['seconds'] <= 120)
            for i, w in enumerate(ws):
                self.assertEqual(w['index'], i)
                self.assertEqual(w['end_frame'] - w['start_frame'], w['frames'])
                self.assertTrue(0 < w['frames'] <= 320000)
                self.assertEqual(w['final_short'], w['frames'] < 320000)
                if i:
                    self.assertEqual(ws[i-1]['end_frame'], w['start_frame'])
                    self.assertEqual(ws[i-1]['frames'], 320000)

    def test_original_failure_preserved(self):
        result = json.loads((ROOT/'results.json').read_text())
        self.assertEqual(result['result'], 'FAIL')
        self.assertFalse(result['gate']['passes'])
        self.assertEqual(result['gate']['nonregressing_meetings'], 5)
        for arm, expected in [('sensevoice',(1360,220,1107,33)), ('qwen',(1637,683,711,243))]:
            a = result['arms'][arm]
            keys = ('errors','substitutions','deletions','insertions')
            self.assertEqual(tuple(a['overall'][k] for k in keys), expected)
            self.assertEqual(a['overall']['reference_characters'],4182)
            self.assertEqual(len(a['windows']),41)
            for k in keys:
                self.assertEqual(sum(m[k] for m in a['meetings'].values()),a['overall'][k])
            for m in a['meetings'].values():
                self.assertEqual(m['errors'],sum(m[k] for k in keys[1:]))
        q = result['arms']['qwen']; b = result['arms']['sensevoice']
        self.assertEqual(sum(q['meetings'][m]['errors'] <= b['meetings'][m]['errors'] for m in q['meetings']),5)
        bad = [w for w in q['windows'] if w['truncated']]
        self.assertEqual(len(bad),2)
        self.assertEqual(len({w['meeting'] for w in bad}),1)
        self.assertEqual([(w['repetition_suspicion']['period'],w['repetition_suspicion']['repetitions']) for w in bad],[(7,46),(2,150)])
        for w in bad:
            self.assertNotIn('block', w['repetition_suspicion'])
            self.assertNotIn('span', w['repetition_suspicion'])

if __name__ == '__main__': unittest.main()
