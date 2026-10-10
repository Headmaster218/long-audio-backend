import importlib.util
import json
from pathlib import Path
import random
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('english_offline_score', ROOT / 'score.py')
metric = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metric)

def distance(a, b):
    table = [[0] * (len(b)+1) for _ in range(len(a)+1)]
    for i in range(len(a)+1): table[i][0] = i
    for j in range(len(b)+1): table[0][j] = j
    for i, x in enumerate(a, 1):
        for j, y in enumerate(b, 1):
            table[i][j] = min(table[i-1][j]+1, table[i][j-1]+1,
                              table[i-1][j-1]+(x != y))
    return table[-1][-1]

class SnapshotTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(metric.normalize('Ａ! B—C $'), 'a bc ')
        self.assertEqual(metric.score('Hello, world!', 'hello world')['words']['errors'], 0)

    def test_edits_and_ties(self):
        self.assertEqual(metric.edits(['a', 'b'], ['b', 'a']),
                         dict(errors=2, substitutions=2, deletions=0, insertions=0, reference_units=2))
        self.assertEqual(metric.score('a b', '')['words']['deletions'], 2)
        self.assertEqual(metric.score('', 'a b')['words']['insertions'], 2)

    def test_synthetic_parity(self):
        rng = random.Random(20261010)
        for _ in range(200):
            a = [rng.choice('abc') for _ in range(rng.randrange(12))]
            b = [rng.choice('abc') for _ in range(rng.randrange(12))]
            counts = metric.edits(a, b)
            self.assertEqual(counts['errors'], distance(a, b))
            self.assertEqual(counts['errors'], sum(counts[k] for k in ('substitutions','deletions','insertions')))

    def test_bounds(self):
        for bad in (None, 3, 'a' * 65537):
            with self.assertRaises(ValueError): metric.score(bad, '')
        with self.assertRaises(ValueError): metric.score('a' * 4097, '')

    def test_selection(self):
        rows = json.loads((ROOT/'selection.json').read_text())['examples']
        self.assertEqual(len(rows), 58)
        self.assertEqual(len({(x['meeting'], x['segment_id']) for x in rows}), 58)
        self.assertEqual(sum(x['reference_words'] for x in rows), 1079)
        self.assertEqual(sum(x['end_frame']-x['start_frame'] for x in rows), 5848000)
        self.assertEqual(len({x['meeting'] for x in rows}), 5)
        for x in rows:
            self.assertTrue(32000 <= x['end_frame']-x['start_frame'] <= 313600)
            self.assertEqual(x['sample_rate'], 16000)
            self.assertNotIn('reference', x)
        for i, a in enumerate(rows):
            for b in rows[i+1:]:
                if a['meeting'] == b['meeting']:
                    self.assertFalse(max(a['start_frame'],b['start_frame']) < min(a['end_frame'],b['end_frame']))

    def test_result_counts(self):
        results = json.loads((ROOT/'results.json').read_text())
        for arm, total in [('sensevoice',249), ('qwen',179)]:
            a = results['arms'][arm]
            self.assertEqual(a['overall']['word_errors'], total)
            self.assertEqual(sum(x['word_errors'] for x in a['rows']), total)
            self.assertEqual(sum(x['reference_words'] for x in a['rows']), 1079)
            for x in a['rows']:
                self.assertEqual(x['word_errors'], sum(x[k] for k in ('substitutions','deletions','insertions')))
                self.assertEqual(x['status'], 'success')
                self.assertFalse(x['truncated'])
        self.assertEqual(results['paired_utterances'],dict(qwen_wins=37,ties=12,qwen_losses=9))

if __name__ == '__main__': unittest.main()
