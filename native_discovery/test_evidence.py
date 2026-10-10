"""Synthetic interval tests and metadata parity; no models or corpus text."""
import json
from pathlib import Path
import unittest
from portable_metrics import union, distinct_overlap, discovery_counts, shift_recording_intervals, cached_availability_score
ROOT = Path(__file__).resolve().parent

class EvidenceTests(unittest.TestCase):
    def test_union_half_open(self):
        self.assertEqual(union([(4, 8), (0, 4), (2, 3)], 10), [[0, 8]])
        self.assertEqual(union([], 10), [])
        for bad in [[(-1, 2)], [(1, 1)], [(1, 11)], [(False, 2)], [(0.5, 2)]]:
            with self.assertRaises(ValueError):
                union(bad, 10)

    def test_distinct_labels_not_duplicate_tracks(self):
        self.assertEqual(distinct_overlap([(0, 8, 'A'), (2, 6, 'A')], 10), [])
        self.assertEqual(distinct_overlap([(0, 8, 'A'), (2, 6, 'A'), (4, 10, 'B')], 10), [[4, 8]])
        self.assertEqual(distinct_overlap([(0, 4, 'A'), (4, 8, 'B')], 10), [])
        self.assertEqual(distinct_overlap([(0, 10, 'A'), (2, 8, 'B'), (3, 7, 'C')], 10), [[2, 8]])

    def test_exhaustive_binary_unions(self):
        # Every pair of8-sample binary masks:65536 exact comparisons.
        n = 8
        for reference in range(1 << n):
            ref = [(i, i + 1) for i in range(n) if reference & (1 << i)]
            for prediction in range(1 << n):
                pred = [(i, i + 1) for i in range(n) if prediction & (1 << i)]
                r = discovery_counts(ref, pred, n)
                self.assertEqual(r['true_positive_frames'], (reference & prediction).bit_count())
                self.assertEqual(r['false_positive_frames'], (prediction & ~reference).bit_count())
                self.assertEqual(r['false_negative_frames'], (reference & ~prediction).bit_count())

    def test_reference_axis_shift_and_empty(self):
        self.assertEqual(shift_recording_intervals([(100, 104)], 100, 10), [[0, 4]])
        self.assertEqual(discovery_counts([(0, 4)], [], 10)['false_negative_frames'], 4)
        self.assertIsNone(discovery_counts([], [], 10)['precision'])
        self.assertIsNone(discovery_counts([], [], 10)['recall'])

    def test_observed_sample_count_parity(self):
        r = json.loads((ROOT / 'results.json').read_text())
        for key, value in r['aggregate_counts'].items():
            self.assertEqual(sum(row['counts'][key] for row in r['rows']), value)
        counts = r['aggregate_counts']
        self.assertEqual((counts['true_positive_frames'], counts['false_positive_frames'], counts['false_negative_frames']), (307248, 63696, 223792))
        self.assertEqual(counts['true_positive_frames'] + counts['false_negative_frames'], 531040)
        self.assertEqual(counts['input_frames'] - counts['reference_overlap_frames'], 1397200)
        self.assertEqual(r['nonoverlap_seconds'], 1397200 / 16000)
        self.assertEqual(r['precision'], 307248 / 370944)
        self.assertEqual(r['recall'], 307248 / 531040)
        self.assertEqual(sum(row['triggered'] for row in r['rows']), 7)
        self.assertEqual(sum(row['reference_characters'] for row in r['rows']), 686)
        self.assertTrue(all(row['full_input_EOF'] and row['core_tail_frames'] == 0 for row in r['rows']))
        self.assertTrue(r['all_inputs_overlap_positive'])
        self.assertEqual(r['negative_input_count'], 0)
        self.assertIsNone(r['input_level_false_positive_rate'])

    def test_all_views_cached_availability(self):
        r = json.loads((ROOT / 'results.json').read_text())
        pop = r['rows']
        errors = {x['input_id']: x['historical_oracle_errors'] for x in pop}
        gates = {x['input_id']: x['cache_available'] for x in pop}
        result = cached_availability_score(pop, errors, gates, 686)
        self.assertEqual((result['errors'], result['reference_units']), (188, 686))
        self.assertEqual(sum(errors.values()), 127)
        self.assertEqual(sum(errors[x['input_id']] for x in pop if x['cache_available']), 117)
        self.assertEqual(sum(x['reference_characters'] for x in pop if not x['cache_available']), 71)
        self.assertEqual(127 - 10 + 71, 188)
        for view in ['raw', 'strict', 'strict_plus_partial']:
            self.assertEqual(sum(x['cached_gated_errors'][view] for x in pop), 188)
            self.assertEqual(sum(x['accepted_cached_phrases'][view] for x in pop), 45)
            self.assertEqual(r['cached_composition']['views'][view]['errors'], 188)
        self.assertEqual(cached_availability_score(pop, {}, {}, 686)['errors'], 686)
        with self.assertRaises(ValueError):
            cached_availability_score(pop, errors, gates, 615)
        with self.assertRaises(ValueError):
            cached_availability_score(pop, errors, {'unknown': True}, 686)
        with self.assertRaises(ValueError):
            cached_availability_score(pop, errors, {pop[0]['input_id']: 1}, 686)

    def test_cache_container_truthfulness(self):
        r = json.loads((ROOT / 'results.json').read_text())
        for row in r['rows']:
            receipt = row['cache_sample_receipt']
            if row['cache_available']:
                self.assertEqual((receipt['input_start_frame'], receipt['input_end_frame'], receipt['frames']), (0, row['frames'], row['frames']))
                self.assertEqual(receipt['float32_sha256'], row['expected_decoded_float32_sha256'])
                self.assertEqual(receipt['original_full_input_encoded_PCM16_sha256'], row['original_PCM16_encoded_sha256'])
                self.assertIsNone(receipt['hypothetical_production_FLOAT_container_sha256'])
                self.assertFalse(receipt['FLOAT_container_materialized'])
                self.assertFalse(receipt['fresh_MOSS_call'])
            else:
                self.assertIsNone(receipt)
        self.assertEqual(r['execution']['MOSS_calls'], 0)

if __name__ == '__main__':
    unittest.main()
