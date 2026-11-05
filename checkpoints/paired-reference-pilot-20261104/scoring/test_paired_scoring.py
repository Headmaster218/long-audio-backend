"""Synthetic only: no audio, real corpus vectors, model loads, or network."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import paired_scoring as p


def vec(axis, extra=None):
    out = [0.] * 512
    out[axis] = 1.
    if extra is not None:
        out[axis], out[extra] = .8, .6
    return out


def fixture(split):
    prefix = split.lower()
    gallery = [{'person_id': prefix + person, 'reference_ids': [prefix + person + 'r' + str(i) for i in range(3)]}
               for person in ('A', 'B')]
    queries = [prefix + person + 'q' + str(i) for person in ('A', 'B', 'U') for i in range(3)]
    predictor = {'split': split, 'gallery': gallery, 'queries': queries,
                 'primary_queries': [prefix + person + 'q0' for person in ('A', 'B', 'U')]}
    truth, vectors = {}, {}
    for person, axis in (('A', 0), ('B', 1), ('U', 2)):
        for i in range(3):
            q = prefix + person + 'q' + str(i)
            vectors[q] = vec(axis)
            truth[q] = {'kind': 'unknown' if person == 'U' else 'known',
                        'person_id': None if person == 'U' else prefix + person,
                        'speaker_id': prefix + 'speaker' + person, 'session_id': prefix + 'later'}
        if person != 'U':
            for i in range(3):
                vectors[prefix + person + 'r' + str(i)] = vec(axis)
    return predictor, truth, vectors


class PairedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='paired-synthetic-')
        self.root = Path(self.tmp.name)
        self.inputs = {}
        for split in ('C', 'T'):
            pred, truth, vectors = fixture(split)
            self.inputs[split] = {'predictor': self.write(split + 'p.json', pred),
                                  'truth': self.write(split + 't.json', truth),
                                  'vectors': self.write(split + 'v.json', vectors)}
        source = self.write('source.json', self.source_evidence())
        self.protocol = p.freeze_protocol(self.inputs['C']['predictor'], self.inputs['C']['truth'],
                                         self.inputs['T']['predictor'], self.inputs['T']['truth'], source,
                                         self.root / 'protocol.json')

    def source_evidence(self):
        rows = []
        for split in ('C', 'T'):
            pred, truth, _ = fixture(split)
            for person in pred['gallery']:
                source = next(t for t in truth.values() if t['person_id'] == person['person_id'])
                for ref in person['reference_ids']:
                    rows.append(dict(source, id=ref, split=split, role='reference',
                                     session_id=split.lower() + 'earlier', recording_day_label='02-03-05', meeting_phase='a', turn_id=ref))
            for q, source in truth.items():
                rows.append(dict(source, id=q, split=split, role='query', recording_day_label='02-03-05', meeting_phase='d', turn_id=q))
        return {'schema': 1, 'records': rows, 'synthetic_only': True}

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, value):
        path = self.root / name
        path.write_bytes(p.canonical(value) + b'\n')
        return path

    def score(self, split='C', lock=None, name=None):
        path = self.inputs[split]['vectors']
        return p.score_and_seal(self.protocol['path'], self.protocol['sha256'], split,
                                path, p.pin(path)['sha256'], self.root / (name or split + 'predictions.json'), lock)

    def choose(self):
        predictions = self.score()
        receipt = p.report_sealed(self.protocol['path'], self.protocol['sha256'], predictions,
                                  self.root / 'Creport.json', self.root / 'paired-lock.json')
        return predictions, receipt

    def test_end_to_end_counts_and_locked_thresholds(self):
        _, receipt = self.choose()
        self.assertEqual(receipt['status'], 'both_candidates')
        lock = p.read_pinned(receipt['lock'])
        self.assertEqual(lock['thresholds'], {'single': 1., 'multi3': 1.})
        prediction = self.score('T', receipt['lock'])
        result = p.report_sealed(self.protocol['path'], self.protocol['sha256'], prediction, self.root / 'Treport.json')
        report = p.read_pinned(result['report'])
        for arm in p.ARMS:
            self.assertEqual(report['arms'][arm]['primary']['counts']['planned'], 3)
            self.assertEqual(report['arms'][arm]['all_queries_diagnostic']['counts']['planned'], 9)
            self.assertEqual(report['arms'][arm]['primary']['counts']['known_correct'], 2)
            self.assertIsNone(report['formal_population_or_safety_ci'])
            self.assertTrue(report['arms'][arm]['conditional_iid_reference_only']['available'])
        self.assertEqual(report['paired_count_differences_multi3_minus_single']['primary']['known_correct'], 0)

    def test_calls_exact_product_functions(self):
        pred, _, vectors = fixture('C')
        with mock.patch.object(p.support_vectors, 'normalized_vector', wraps=p.support_vectors.normalized_vector) as normal:
            with mock.patch.object(p.verified_identity, 'centroid_score', wraps=p.verified_identity.centroid_score) as centroid:
                rows, bad = p._raw_scores(pred, vectors)
        self.assertFalse(bad)
        self.assertGreater(normal.call_count, len(vectors))
        self.assertEqual(centroid.call_count, 2 + 9 * 2 * 2)
        self.assertEqual(rows['single'][0]['scores'][0]['score'], 1.)

    def test_independent_thresholds_and_tie_higher(self):
        vectors = p.read_json(self.inputs['C']['vectors'])
        vectors['cAr1'] = vec(0, 3)
        vectors['cAr2'] = vec(0, 3)
        self.write('Cv.json', vectors)
        _, receipt = self.choose()
        lock = p.read_pinned(receipt['lock'])
        self.assertEqual(lock['thresholds']['single'], 1.)
        self.assertLess(lock['thresholds']['multi3'], 1.)
        self.assertEqual(lock['thresholds']['multi3'], .90)

    def test_one_arm_no_candidate_stops_both(self):
        vectors = p.read_json(self.inputs['C']['vectors'])
        vectors['cAr0'], vectors['cBr0'] = vec(1), vec(0)
        self.write('Cv.json', vectors)
        _, receipt = self.choose()
        self.assertEqual(receipt['status'], 'stop_before_T')
        self.assertIsNone(receipt['lock'])
        self.assertFalse((self.root / 'paired-lock.json').exists())
        report = p.read_pinned(receipt['report'])
        self.assertEqual(report['arms']['single']['selection']['status'], 'no_useful_candidate_stop_before_T')
        self.assertEqual(report['arms']['multi3']['selection']['status'], 'candidate')
        with self.assertRaisesRegex(ValueError, 'both arm'):
            self.score('T')

    def test_t_requires_lock_before_vector_access(self):
        self.inputs['T']['vectors'] = self.root / 'absent-vectors.json'
        # Call directly, since test convenience score() hashes vectors first.
        with self.assertRaisesRegex(ValueError, 'both arm'):
            p.score_and_seal(self.protocol['path'], self.protocol['sha256'], 'T',
                             self.inputs['T']['vectors'], '0' * 64, self.root / 'unused.json')

    def test_lock_tamper_rejected(self):
        _, receipt = self.choose()
        lock = p.read_pinned(receipt['lock'])
        lock['thresholds']['single'] = .46
        Path(receipt['lock']['path']).write_bytes(p.canonical(lock))
        with self.assertRaisesRegex(ValueError, 'pin mismatch'):
            self.score('T', receipt['lock'])

    def test_partial_lock_even_with_new_hash_rejected(self):
        _, receipt = self.choose()
        lock = p.read_pinned(receipt['lock'])
        del lock['thresholds']['multi3']
        path = self.write('partial.json', lock)
        with self.assertRaisesRegex(ValueError, 'partial'):
            self.score('T', p.pin(path))

    def test_t_threshold_tamper_with_new_prediction_hash_rejected(self):
        _, receipt = self.choose()
        sealed = self.score('T', receipt['lock'])
        prediction = p.read_pinned(sealed)
        prediction['thresholds']['single'] = .46
        path = self.write('tampered.json', prediction)
        with self.assertRaisesRegex(ValueError, 'thresholds differ'):
            p.report_sealed(self.protocol['path'], self.protocol['sha256'], p.pin(path), self.root / 'report.json')

    def test_t_no_lock_creation_or_retuning(self):
        _, receipt = self.choose()
        pred = self.score('T', receipt['lock'])
        with self.assertRaisesRegex(ValueError, 'cannot create or retune'):
            p.report_sealed(self.protocol['path'], self.protocol['sha256'], pred,
                            self.root / 'report.json', self.root / 'new-lock.json')

    def test_reordered_predictor_file_rejected(self):
        _, receipt = self.choose()
        predictor = p.read_json(self.inputs['T']['predictor'])
        predictor['gallery'].reverse()
        self.write('Tp.json', predictor)
        with self.assertRaisesRegex(ValueError, 'pin mismatch'):
            self.score('T', receipt['lock'])

    def test_reordered_reference_changes_binding(self):
        pred, _, _ = fixture('C')
        changed = copy.deepcopy(pred)
        changed['gallery'][0]['reference_ids'].reverse()
        self.assertNotEqual(p.digest(pred), p.digest(changed))

    def test_vector_key_reordering_semantically_same_but_pin_changed(self):
        _, receipt = self.choose()
        vectors = p.read_json(self.inputs['C']['vectors'])
        Path(self.inputs['C']['vectors']).write_text(json.dumps(dict(reversed(list(vectors.items())))))
        with self.assertRaisesRegex(ValueError, 'pin mismatch'):
            self.score('T', receipt['lock'])

    def test_changed_source_rejected(self):
        _, receipt = self.choose()
        original = p.source_pins()
        changed = copy.deepcopy(original)
        changed['product_verified_identity']['sha256'] = '0' * 64
        with mock.patch.object(p, 'source_pins', return_value=changed):
            with self.assertRaisesRegex(ValueError, 'changed source'):
                self.score('T', receipt['lock'])

    def test_changed_protocol_rejected(self):
        original = p.read_pinned(self.protocol)
        original['warning'] += 'changed'
        Path(self.protocol['path']).write_bytes(p.canonical(original))
        with self.assertRaisesRegex(ValueError, 'externally retained SHA'):
            self.score()

    def test_missing_unused_single_reference_disables_both_arms(self):
        pred, _, vectors = fixture('C')
        del vectors['cAr2']
        rows, bad = p._raw_scores(pred, vectors)
        self.assertEqual(bad, ['cAr2'])
        for arm in p.ARMS:
            self.assertEqual(len(rows[arm]), 9)
            self.assertTrue(all(not r['scores'] and r['unavailable_reason'] for r in rows[arm]))

    def test_invalid_reference_variants(self):
        for bad in ([0.] * 512, [1.] * 511, [True] * 512, [float('nan')] * 512,
                    [float('inf')] * 512, None, 'bad'):
            with self.subTest(kind=repr(bad)[:30]):
                pred, _, vectors = fixture('C')
                vectors['cAr2'] = bad
                rows, invalid = p._raw_scores(pred, vectors)
                self.assertIn('cAr2', invalid)
                self.assertTrue(all(r['unavailable_reason'] for arm in p.ARMS for r in rows[arm]))

    def test_zero_centroid_disables_both_arms(self):
        pred, _, vectors = fixture('C')
        vectors['cAr0'] = vec(0)
        vectors['cAr1'] = [-.5, math.sqrt(3) / 2] + [0.] * 510
        vectors['cAr2'] = [-.5, -math.sqrt(3) / 2] + [0.] * 510
        # Exercise product f64 cancellation itself, not substitute centroid math.
        rows, invalid = p._raw_scores(pred, vectors)
        self.assertEqual(len(invalid), 3)
        self.assertTrue(all(r['unavailable_reason'] for arm in p.ARMS for r in rows[arm]))

    def test_missing_query_keeps_denominator(self):
        pred, _, vectors = fixture('C')
        del vectors['cAq1']
        rows, invalid = p._raw_scores(pred, vectors)
        self.assertFalse(invalid)
        for arm in p.ARMS:
            self.assertEqual(len(rows[arm]), 9)
            self.assertEqual(sum(bool(r['unavailable_reason']) for r in rows[arm]), 1)

    def test_extra_vector_id_rejected(self):
        pred, _, vectors = fixture('C')
        vectors['unfrozen_reference'] = vec(0)
        with self.assertRaisesRegex(ValueError, 'outside'):
            p._raw_scores(pred, vectors)

    def test_duplicate_or_overlapping_predictor_ids(self):
        for alteration in ('person', 'ref', 'query', 'overlap', 'unknownref', 'primary'):
            pred, _, _ = fixture('C')
            if alteration == 'person': pred['gallery'][1]['person_id'] = pred['gallery'][0]['person_id']
            elif alteration == 'ref': pred['gallery'][1]['reference_ids'][0] = pred['gallery'][0]['reference_ids'][0]
            elif alteration == 'query': pred['queries'].append(pred['queries'][0])
            elif alteration == 'overlap': pred['queries'][0] = pred['gallery'][0]['reference_ids'][0]
            elif alteration == 'unknownref': pred['gallery'][0]['reference_ids'].append('outside')
            elif alteration == 'primary': pred['primary_queries'][0] = 'outside'
            with self.subTest(alteration=alteration), self.assertRaises(ValueError):
                p.validate_predictor(pred)

    def test_at_least_two_people(self):
        pred, _, _ = fixture('C')
        pred['gallery'] = pred['gallery'][:1]
        with self.assertRaisesRegex(ValueError, 'at least two'):
            p.validate_predictor(pred)

    def test_truth_primary_duplicates_and_unknown_reference(self):
        pred, truth, _ = fixture('C')
        truth['cBq0']['speaker_id'] = truth['cAq0']['speaker_id']
        with self.assertRaises(ValueError):
            p.validate_truth(pred, truth)
        pred, truth, _ = fixture('C')
        for q in pred['queries'][:3]:
            truth[q]['kind'], truth[q]['person_id'] = 'unknown', None
        with self.assertRaisesRegex(ValueError, 'unknown references prohibited'):
            p.validate_truth(pred, truth)

    def test_prediction_seal_precedes_truth_parse(self):
        original = p.read_pinned
        visited = []
        def tracked(pin):
            if pin == p.pin(self.inputs['C']['truth']):
                self.assertTrue((self.root / 'Cpredictions.json').exists())
                visited.append('C truth')
            return original(pin)
        with mock.patch.object(p, 'read_pinned', side_effect=tracked):
            self.choose()
        self.assertEqual(visited, ['C truth'])

    def test_t_prediction_seal_precedes_truth_parse(self):
        _, receipt = self.choose()
        original = p.read_pinned
        visited = []
        def tracked(pin):
            if pin == p.pin(self.inputs['T']['truth']):
                self.assertTrue((self.root / 'Tpredictions.json').exists())
                visited.append('T truth')
            return original(pin)
        with mock.patch.object(p, 'read_pinned', side_effect=tracked):
            pred = self.score('T', receipt['lock'])
            p.report_sealed(self.protocol['path'], self.protocol['sha256'], pred, self.root / 'Treport.json')
        self.assertEqual(visited, ['T truth'])

    def test_atomic_exclusive_seals(self):
        path = self.root / 'once.json'
        original = p.seal(path, {'first': 1})
        with self.assertRaises(FileExistsError):
            p.seal(path, {'second': 2})
        self.assertEqual(p.pin(path), original)
        self.assertEqual(list(self.root.glob('.once.json.*')), [])

    def test_failure_before_atomic_link_leaves_no_lock(self):
        path = self.root / 'no-lock.json'
        with mock.patch.object(p.os, 'link', side_effect=OSError('simulated publish failure')):
            with self.assertRaises(OSError):
                p.seal(path, {'thresholds': {'single': .8, 'multi3': .9}})
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.glob('.no-lock.json.*')), [])

    def test_source_evidence_rejects_dummy_unknown_reference_and_ct_speaker_overlap(self):
        preds = {split: fixture(split)[0] for split in ('C', 'T')}
        cases = []
        cases.append({'unverified': 'dummy'})
        unknown = self.source_evidence()
        unknown['records'][0].update(kind='unknown', person_id=None)
        cases.append(unknown)
        overlap = self.source_evidence()
        for row in overlap['records']:
            if row['split'] == 'T' and row['speaker_id'] == 'tspeakerU':
                row['speaker_id'] = 'cspeakerU'
        cases.append(overlap)
        for evidence in cases:
            with self.subTest(evidence=str(evidence)[:45]), self.assertRaises(ValueError):
                p.validate_source_roles(evidence, preds)

    def test_source_evidence_requires_three_distinct_earlier_meeting_turns(self):
        preds = {split: fixture(split)[0] for split in ('C', 'T')}
        for mutation in ('same_turn', 'same_session', 'wrong_phase', 'different_reference_session'):
            evidence = self.source_evidence()
            if mutation == 'same_turn':
                evidence['records'][1]['turn_id'] = evidence['records'][0]['turn_id']
            elif mutation == 'same_session':
                for row in evidence['records']:
                    if row['role'] == 'reference': row['session_id'] = row['split'].lower() + 'later'
            elif mutation == 'wrong_phase':
                for row in evidence['records']:
                    if row['role'] == 'reference': row['meeting_phase'] = 'd'
            elif mutation == 'different_reference_session':
                evidence['records'][0]['session_id'] = 'other-earlier'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                p.validate_source_roles(evidence, preds)

    def test_source_session_metadata_is_consistent(self):
        preds = {split: fixture(split)[0] for split in ('C', 'T')}
        for mutation in ('unknown_in_reference_session', 'different_days_same_session'):
            evidence = self.source_evidence()
            for row in evidence['records']:
                if row['split'] != 'C': continue
                if mutation == 'unknown_in_reference_session' and row['kind'] == 'unknown':
                    row['session_id'] = 'cearlier'
                elif mutation == 'different_days_same_session' and row['person_id'] == 'cB':
                    row['recording_day_label'] = '03-03-05'
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'conflicting'):
                p.validate_source_roles(evidence, preds)

    def test_known_reference_query_day_labels_must_match(self):
        evidence = self.source_evidence()
        for row in evidence['records']:
            if row['split'] == 'C' and row['role'] == 'query':
                row['recording_day_label'] = 'different-raw-day'
        with self.assertRaisesRegex(ValueError, 'same raw day label'):
            p.validate_source_roles(evidence, {split: fixture(split)[0] for split in ('C', 'T')})

    def test_c_truth_must_match_source_roles(self):
        protocol = p.read_pinned(self.protocol)
        truth = p.read_json(self.inputs['C']['truth'])
        for annotation in truth.values():
            annotation['session_id'] = 'different-meeting'
        self.write('Ct.json', truth)
        protocol['inputs']['C']['truth'] = p.pin(self.inputs['C']['truth'])
        self.protocol = p.pin(self.write('protocol.json', protocol))
        with self.assertRaisesRegex(ValueError, 'truth disagrees'):
            self.choose()
        self.assertFalse((self.root / 'paired-lock.json').exists())

    def test_resealed_selection_tamper_recomputed(self):
        _, receipt = self.choose()
        lock = p.read_pinned(receipt['lock'])
        report = p.read_pinned(lock['C_report'])
        report['arms']['single']['selection']['threshold'] = .99
        changed_report = self.write('tampered-report.json', report)
        lock['C_report'] = p.pin(changed_report)
        lock['thresholds']['single'] = .99
        changed_lock = self.write('tampered-lock.json', lock)
        with self.assertRaisesRegex(ValueError, 'recomputed C selection'):
            self.score('T', p.pin(changed_lock))

    def test_substituted_c_vectors_rejected_even_with_new_pin(self):
        _, receipt = self.choose()
        lock = p.read_pinned(receipt['lock'])
        vectors = p.read_json(self.inputs['C']['vectors'])
        vectors['cAr0'] = vec(3)
        changed_vectors = self.write('other-vectors.json', vectors)
        lock['C_vectors'] = p.pin(changed_vectors)
        changed_lock = self.write('tampered-lock.json', lock)
        with self.assertRaisesRegex(ValueError, 'prediction evidence'):
            self.score('T', p.pin(changed_lock))

    def test_duplicate_json_keys_rejected(self):
        path = self.root / 'duplicate.json'
        path.write_text('{"x": [1], "x": [2]}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            p.load_vectors(p.pin(path))

    def test_native_npz_and_invalid_mask(self):
        import numpy as np
        pred, _, vectors = fixture('C')
        path = self.root / 'synthetic.npz'
        ids = list(vectors)
        np.savez(path, ids=np.asarray(ids), normalized=np.asarray(list(vectors.values())), valid=np.asarray([True] * len(ids)))
        self.assertEqual(p.load_vectors(p.pin(path)), vectors)
        np.savez(path, ids=np.asarray(ids), normalized=np.asarray(list(vectors.values())), valid=np.asarray([1] * len(ids)))
        with self.assertRaisesRegex(ValueError, 'malformed'):
            p.load_vectors(p.pin(path))

    def test_native_npz_duplicate_and_length_mismatch(self):
        import numpy as np
        path = self.root / 'synthetic.npz'
        for ids, vectors, mask in ((['a', 'a'], [vec(0), vec(0)], [True, True]),
                                   (['a', 'b'], [vec(0)], [True, True])):
            np.savez(path, ids=np.asarray(ids), normalized=np.asarray(vectors), valid=np.asarray(mask))
            with self.assertRaisesRegex(ValueError, 'malformed'):
                p.load_vectors(p.pin(path))

    def test_c_missing_reference_blocks_lock(self):
        vectors = p.read_json(self.inputs['C']['vectors'])
        del vectors['cAr1']
        self.write('Cv.json', vectors)
        _, receipt = self.choose()
        self.assertEqual(receipt['status'], 'stop_before_T')
        report = p.read_pinned(receipt['report'])
        for arm in p.ARMS:
            self.assertEqual(report['arms'][arm]['selection']['status'], 'BLOCKED_INCONCLUSIVE')
        self.assertIsNone(receipt['lock'])


if __name__ == '__main__':
    unittest.main()
