"""Aggregate arithmetic only; no model, audio or network access."""
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
P = json.loads((HERE / 'PROTOCOL.json').read_text())
R = json.loads((HERE / 'RESULTS.json').read_text())
ARMS = ('baseline_5s_2.5s', 'experimental_2s_1s')
COUNTS = ('tp', 'fp', 'fn', 'tn', 'segments')

def rates(counts):
    tp, fp, fn = (counts[k] for k in ('tp', 'fp', 'fn'))
    return dict(counts, precision=tp/(tp+fp) if tp+fp else None,
                recall=tp/(tp+fn) if tp+fn else None,
                f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None)


class Aggregates(unittest.TestCase):
    def test_counts_rates_and_pooling(self):
        for split in ('calibration', 'heldout'):
            for arm in ARMS:
                p = R['splits'][split][arm]
                rows = [r['metrics'] for r in R['per_file'] if r['split'] == split and r['arm'] == arm]
                self.assertEqual(len(rows), 2)
                for row in [p] + rows:
                    c = row['one_second_door_segments']
                    self.assertEqual(sum(c[k] for k in COUNTS[:4]), c['segments'])
                    self.assertEqual(rates({k: c[k] for k in COUNTS}), c)
                    self.assertEqual(row['covered_door_reference_count'] + row['missed_door_reference_count'], row['door_reference_count'])
                for k in COUNTS:
                    self.assertEqual(sum(r['one_second_door_segments'][k] for r in rows), p['one_second_door_segments'][k])
                for k in rows[0]:
                    if k != 'one_second_door_segments':
                        self.assertAlmostEqual(sum(r[k] for r in rows), p[k], places=12)

    def test_gate_calls_and_defaults(self):
        b, c = [R['splits']['calibration'][a] for a in ARMS]
        self.assertGreater(c['one_second_door_segments']['f1'], b['one_second_door_segments']['f1'])
        self.assertGreaterEqual(c['covered_door_reference_count'], b['covered_door_reference_count'])
        self.assertTrue(R['calibration_gate']['advanced'])
        self.assertEqual(R['calibration_gate']['total_compute_attempts_before_heldout'], 332)
        self.assertFalse(R['production_defaults_changed'])
        h = R['history']
        self.assertEqual(h['original']['valid_score_calls_retained'], 166)
        self.assertEqual(h['continuation']['new_score_calls_completed'], 498)
        self.assertEqual(h['total_score_calls'], 166 + 498)
        self.assertEqual(h['total_model_loads'], 2)
        self.assertEqual(sum(r['compute_attempts'] for r in R['per_file']), 664)
        for r in R['per_file']:
            self.assertEqual(r['compute_attempts'], P['execution_plan']['windows_per_120s_file'][r['arm']])

    def test_api_and_error_breakdown(self):
        api = R['api_persistence_check']
        self.assertEqual([r['source_label'] for r in api['observations']], ['Rain on surface', 'Knock'])
        for r in api['observations']:
            self.assertEqual((r['start'], r['end'], r['inference_windows']), (0, 5, 1))
        self.assertEqual(api['elapsed_seconds'], 6.665)
        for arm in ARMS:
            d = R['heldout_error_breakdown'][arm]
            self.assertEqual(d['knock_covered'] + d['doorslam_covered'], R['splits']['heldout'][arm]['covered_door_reference_count'])
            self.assertEqual(d['doorslam_covered'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
