"""Portable stdlib-only synthetic scorer and sanitized metadata tests."""
import itertools
import json
from pathlib import Path
import unittest
from portable_scorer import score, score_population, tokens, edit
ROOT = Path(__file__).resolve().parent

class EvidenceTests(unittest.TestCase):
    def test_all_three_views_exact_127_of_686(self):
        r = json.loads((ROOT / 'results.json').read_text())
        self.assertEqual(set(r['views']), {'raw', 'strict', 'strict_plus_partial'})
        for view in r['views'].values():
            self.assertEqual((view['errors'], view['reference_units']), (127, 686))
            self.assertEqual(sum(x['errors'] for x in view['inputs']), 127)
            self.assertEqual(sum(x['reference_units'] for x in view['inputs']), 686)
            self.assertEqual(len(view['inputs']), 8)
        self.assertEqual(sum(x['errors'] for x in r['per_voice']), 127)
        self.assertEqual(sum(x['reference_units'] for x in r['per_voice']), 686)
        self.assertEqual(sum(r['sdi'].values()), 127)
        self.assertEqual(len(r['per_voice']), 24)
        self.assertEqual(sum(x['reference_units'] for x in r['per_voice'] if x['hypothesis_channel'] is None), 5)
        self.assertEqual(sum(x['accepted_phrase_count'] for x in r['views']['strict']['inputs']), 50)

    def test_selection_and_exact_exclusions(self):
        s = json.loads((ROOT / 'selection.json').read_text())
        self.assertEqual(len(s['eligible_candidates']), 33)
        self.assertEqual(len(s['exclusions']), 72)
        self.assertEqual([sum(x['source_set'] == i for x in s['exclusions']) for i in range(3)], [56, 8, 8])
        self.assertEqual(sum(x['frames'] for x in s['supports']), 1928240)
        self.assertEqual(sum(x['overlap_frames'] for x in s['supports']), 531040)
        self.assertEqual(sum(x['reference_turn_count'] for x in s['supports']), 59)
        for candidate in s['eligible_candidates']:
            a, b = candidate['start_frame'], candidate['end_frame']
            self.assertTrue(160000 <= b - a <= 320000)
            self.assertGreaterEqual(candidate['overlap_frames'] * 5, b - a)
            self.assertFalse(any(x['meeting'] == candidate['meeting'] and x['start_frame'] < b and x['end_frame'] > a for x in s['exclusions']))
        for chosen in s['supports']:
            pairs = [(x['start_frame'], x['end_frame']) for x in s['eligible_candidates'] if x['meeting'] == chosen['meeting']]
            self.assertEqual((chosen['start_frame'], chosen['end_frame']), min(pairs))
            self.assertEqual(chosen['partial_outer_turns'], 0)
            self.assertEqual(sum(chosen['speaker_reference_units'].values()), chosen['reference_characters'])

    def test_normalization(self):
        self.assertEqual(tokens('Ａ，甲！\n 乙★', 'zh'), list('a甲乙'))

    def test_missing_failed_population_keeps_denominator(self):
        inputs = [{'id': 'a', 'language': 'zh', 'speakers': [{'id': 'r', 'text': '甲乙'}]},
                  {'id': 'b', 'language': 'zh', 'speakers': [{'id': 'r', 'text': '丙丁'}]}]
        r = score_population(inputs, {'a': {'failure': 'timeout'}}, 4)
        self.assertEqual((r['errors'], r['reference_units'], len(r['inputs'])), (4, 4, 2))
        with self.assertRaises(ValueError):
            score_population(inputs, {}, 3)
        with self.assertRaises(ValueError):
            score_population(inputs, {'unexpected': {'transcripts': []}}, 4)

    def test_swap_and_extra_track(self):
        gold = {'language': 'zh', 'speakers': [{'id': 'a', 'text': '甲乙'}, {'id': 'b', 'text': '丙丁'}]}
        pred = {'transcripts': [{'channel': 9, 'text': '丙丁'}, {'channel': 3, 'text': '甲乙'}, {'channel': 7, 'text': '额外'}]}
        r = score(gold, pred)
        self.assertEqual(r['errors'], 2)
        self.assertFalse(r['identity_claim'])
        self.assertEqual(sum(x['reference_id'] is None for x in r['oracle_excerpt_assignment']), 1)

    def test_1024_exact_matching_cases(self):
        words = ['', '甲', '乙', '甲乙']
        for refs in itertools.product(words, repeat=2):
            for hyps in itertools.product(words, repeat=3):
                costs = []
                for assignment in itertools.product([None, 0, 1, 2], repeat=2):
                    used = [j for j in assignment if j is not None]
                    if len(used) != len(set(used)):
                        continue
                    costs.append(sum(len(refs[i]) if j is None else edit(list(refs[i]), list(hyps[j])) for i, j in enumerate(assignment)) + sum(len(t) for j, t in enumerate(hyps) if j not in used))
                result = score({'language': 'zh', 'speakers': [{'id': i, 'text': t} for i, t in enumerate(refs)]}, {'transcripts': [{'channel': i, 'text': t} for i, t in enumerate(hyps)]})
                self.assertEqual(result['errors'], min(costs))

    def test_no_published_transcript_fields(self):
        for filename in ['results.json', 'selection.json', 'provenance.json']:
            def walk(value):
                if isinstance(value, dict):
                    self.assertFalse({'text', 'raw_text', 'reference_text', 'hypothesis_text', 'operations'} & set(value))
                    for child in value.values():
                        walk(child)
                elif isinstance(value, list):
                    for child in value:
                        walk(child)
            walk(json.loads((ROOT / filename).read_text()))

if __name__ == '__main__':
    unittest.main()
