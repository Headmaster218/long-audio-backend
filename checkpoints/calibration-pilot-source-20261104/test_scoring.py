"""Synthetic fixtures only; no corpus selection, audio, model, or inference."""
import ast
import copy
import hashlib
import json
import struct
import types
import wave
from unittest.mock import patch
import math
from pathlib import Path
import random
import tempfile
import unittest

import scoring as s


def vector(x=1.0, y=0.0):
    return [x, y] + [0.0] * 510


def predictor(split='C'):
    return {'split': split, 'gallery': [{'person_id': 'A', 'sample_id': split + 'ref-a'},
                                      {'person_id': 'B', 'sample_id': split + 'ref-b'}],
            'queries': [split + 'qa', split + 'qb', split + 'qu']}


def truth(split='C'):
    return {split + 'q' + suffix: {'kind': kind, 'person_id': person,
                                 'speaker_id': split + suffix, 'session_id': split + 'session-' + suffix}
            for suffix, kind, person in [('a', 'known', 'A'), ('b', 'known', 'B'), ('u', 'unknown', None)]}


def vectors(split='C'):
    return {split + 'ref-a': vector(), split + 'ref-b': vector(0, 1),
            split + 'qa': vector(.82, .2), split + 'qb': vector(.4, .78),
            split + 'qu': vector(-1, -1)}


def calibration_scores():
    p = predictor()
    rows = [{'query_id': q, 'unavailable_reason': None,
             'scores': [{'person_id': 'A', 'score': a}, {'person_id': 'B', 'score': b}]}
            for q, a, b in [('Cqa', .82, .2), ('Cqb', .4, .78), ('Cqu', .71, .1)]]
    return {'schema': s.SCHEMA, 'predictor': p, 'rows': rows, 'unavailable_reference_ids': [],
            'candidate_lock_sha256': None, 'allowed_thresholds': list(s.GRID)}


def synthetic_evidence():
    # Only helper unit tests use these placeholder pins. The real-data entry
    # point verifies actual files and complete run semantics in orchestrator.py.
    return {key: {'path': '/synthetic-not-real/' + key, 'bytes': 1, 'sha256': 'a' * 64}
            for key in s.EVIDENCE_KEYS}


class ExactScoringTests(unittest.TestCase):
    def test_exact_frozen_frontend_math(self):
        source = Path(__file__).resolve().parent.parent / 'backend-frontend-comparison-20261104/frontend_experiment.py'
        if not source.is_file():
            self.skipTest('frozen comparison source absent; standalone synthetic tests still run')
        parsed = ast.parse(source.read_text())
        funcs = [node for node in parsed.body if isinstance(node, ast.FunctionDef)
                 and node.name in ('normal64', 'match_score')]
        self.assertEqual(len(funcs), 2)
        namespace = {'math': math}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), str(source), 'exec'), namespace)
        rng = random.Random(20261104)
        for scale in (1e-20, 1., 1e300):
            a = [rng.uniform(-1, 1) * scale for _ in range(512)]
            b = [rng.uniform(-1, 1) * scale for _ in range(512)]
            self.assertEqual(s.normal64(a), namespace['normal64'](a))
            self.assertEqual(s.match_score(a, b), namespace['match_score'](a, b))

    def test_known_geometry(self):
        self.assertEqual(s.match_score(vector(3, 4), vector(3, 4)), 1.)
        self.assertEqual(s.match_score(vector(), vector(0, 1)), 0.)
        self.assertEqual(s.match_score(vector(), vector(-1, 0)), -1.)

    def test_invalid_vectors(self):
        for bad in (None, [1.] * 511, [0.] * 512, vector(float('nan')), vector(float('inf')),
                    vector(True), [1e-31] * 512):
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(ValueError):
                s.normal64(bad)

    def test_predictor_excludes_truth(self):
        p = predictor()
        p['truth'] = truth()
        with self.assertRaises(ValueError):
            s.score_population(p, vectors())

    def test_k_one_and_duplicates_rejected(self):
        for field, value in [('gallery', predictor()['gallery'][:1]),
                             ('gallery', predictor()['gallery'] * 2),
                             ('queries', ['Cqa', 'Cqa'])]:
            p = predictor()
            p[field] = value
            with self.assertRaises(ValueError):
                s.score_population(p, vectors())

    def test_extra_samples_rejected(self):
        v = vectors()
        v['Tqa'] = vector()
        with self.assertRaises(ValueError):
            s.score_population(predictor(), v)

    def test_full_population_query_failure(self):
        v = vectors()
        del v['Cqa']
        result = s.summarize(s.score_population(predictor(), v), truth(), .75)
        self.assertEqual(result['counts']['planned'], 3)
        self.assertEqual(result['counts']['known_unavailable'], 1)
        self.assertEqual(result['counts']['known_refused'], 0)
        self.assertEqual(result['rates']['known_correct'], .5)
        self.assertEqual(result['failure_sensitivity_ranges_not_ci']['known_wrong'], [0, .5])

    def test_failed_reference_does_not_shrink_gallery(self):
        v = vectors()
        v['Cref-b'] = None
        scored = s.score_population(predictor(), v)
        self.assertEqual(scored['unavailable_reference_ids'], ['Cref-b'])
        result = s.summarize(scored, truth(), .75)
        self.assertEqual(result['counts']['known_unavailable'], 2)
        self.assertEqual(result['counts']['unknown_unavailable'], 1)
        self.assertEqual(result['counts']['unknown_refused'], 0)

    def test_missing_rival_never_accepted(self):
        row = {'query_id': 'q', 'scores': [{'person_id': 'A', 'score': 1.}], 'unavailable_reason': None}
        result = s.decide(row, ['A', 'B'], 0.)
        self.assertFalse(result['available'])
        self.assertIsNone(result['emitted'])
        self.assertIn('rival', result['reason'])

    def test_exact_boundaries_and_no_epsilon(self):
        row = {'query_id': 'q', 'unavailable_reason': None,
               'scores': [{'person_id': 'A', 'score': .1}, {'person_id': 'B', 'score': 0.}]}
        self.assertEqual(s.decide(row, ['A', 'B'], .1)['emitted'], 'A')
        self.assertIsNone(s.decide(row, ['A', 'B'], math.nextafter(.1, 1.))['emitted'])
        row['scores'][0]['score'], row['scores'][1]['score'] = .75, .65
        self.assertLess(.75 - .65, .10)
        self.assertEqual(s.decide(row, ['A', 'B'], .75)['reason'], 'unknown_ambiguous')

    def test_ties_lexical_winner_but_refused(self):
        row = {'query_id': 'q', 'unavailable_reason': None,
               'scores': [{'person_id': 'B', 'score': .9}, {'person_id': 'A', 'score': .9}]}
        result = s.decide(row, ['B', 'A'], 0.)
        self.assertEqual(result['winner'], 'A')
        self.assertIsNone(result['emitted'])


class SummarySelectionTests(unittest.TestCase):
    def test_selection_uses_c_only_and_higher_tie(self):
        selected = s.select_candidate(calibration_scores(), truth())
        self.assertEqual(selected['threshold'], .78)
        self.assertEqual(len(selected['grid_results']), 101)
        self.assertEqual(selected['chosen_calibration_report']['counts']['known_correct'], 2)
        scored = calibration_scores()
        scored['predictor']['split'] = 'T'
        with self.assertRaisesRegex(ValueError, 'C only'):
            s.select_candidate(scored, truth())

    def test_no_useful_candidate_is_stop_not_all_refused_success(self):
        scored = calibration_scores()
        scored['rows'][0]['scores'][0]['score'] = .2
        scored['rows'][0]['scores'][1]['score'] = .99
        result = s.select_candidate(scored, truth())
        self.assertIsNone(result['threshold'])
        self.assertEqual(result['status'], 'no_useful_candidate_stop_before_T')

    def test_failures_block_selection(self):
        scored = calibration_scores()
        scored['rows'][2]['unavailable_reason'] = 'run_failed'
        result = s.select_candidate(scored, truth())
        self.assertTrue(result['selection_blocked_by_incomplete_data'])
        self.assertEqual(result['status'], 'BLOCKED_INCONCLUSIVE')
        self.assertEqual(result['grid_results'], [])
        self.assertIsNone(result['threshold'])

    def test_repeats_not_fitting_trials(self):
        annotations = truth()
        annotations['Cqu']['speaker_id'] = annotations['Cqa']['speaker_id']
        # Internally inconsistent truth is rejected before any candidate search.
        with self.assertRaises(ValueError):
            s.select_candidate(calibration_scores(), annotations)

    def test_missing_gallery_primary_is_blocked(self):
        scored, annotations = calibration_scores(), truth()
        scored['predictor']['queries'].remove('Cqb')
        scored['rows'] = [r for r in scored['rows'] if r['query_id'] != 'Cqb']
        del annotations['Cqb']
        result = s.select_candidate(scored, annotations)
        self.assertEqual(result['status'], 'BLOCKED_INCONCLUSIVE')
        self.assertIn('known_primary_person_ids_must_cover_exact_gallery_once', result['block_reasons'])

    def test_duplicate_person_with_different_speaker_ids_is_blocked(self):
        scored, annotations = calibration_scores(), truth()
        annotations['Cqb']['person_id'] = 'A'
        scored['rows'][1]['scores'] = [{'person_id': 'A', 'score': .78}, {'person_id': 'B', 'score': .4}]
        result = s.select_candidate(scored, annotations)
        self.assertEqual(result['status'], 'BLOCKED_INCONCLUSIVE')
        self.assertIsNone(result['threshold'])

    def test_unresolved_truth_is_blocked_not_negative(self):
        annotations = truth()
        annotations['Cqu']['kind'] = 'unresolved'
        result = s.select_candidate(calibration_scores(), annotations)
        self.assertEqual(result['status'], 'BLOCKED_INCONCLUSIVE')
        self.assertIn('unresolved_required_truth', result['block_reasons'])

    def test_complete_population_and_truth_required(self):
        scored = calibration_scores()
        scored['rows'].pop()
        with self.assertRaises(ValueError):
            s.summarize(scored, truth(), .75)
        annotations = truth()
        annotations.pop('Cqu')
        with self.assertRaises(ValueError):
            s.summarize(calibration_scores(), annotations, .75)

    def test_zero_output_precision_undefined(self):
        result = s.summarize(calibration_scores(), truth(), 1.)
        self.assertIsNone(result['rates']['verified_output_precision'])
        self.assertEqual(result['counts']['known_refused'], 2)
        self.assertEqual(result['counts']['unknown_refused'], 1)
        self.assertIsNone(result['units']['independent_trials'])

    def test_wrong_known_unknown_accept_and_unresolved_distinct(self):
        scored = calibration_scores()
        scored['rows'][0]['scores'] = [{'person_id': 'A', 'score': .1}, {'person_id': 'B', 'score': .9}]
        report = s.summarize(scored, truth(), .5)
        self.assertEqual(report['counts']['known_wrong'], 1)
        self.assertEqual(report['counts']['unknown_accepted'], 1)
        self.assertEqual(report['rates']['verified_output_precision'], 1 / 3)
        annotations = truth()
        annotations['Cqu']['kind'] = 'unresolved'
        report = s.summarize(scored, annotations, .5)
        self.assertEqual(report['counts']['unresolved_emitted'], 1)
        self.assertEqual(report['counts']['unknown'], 0)
        self.assertIsNone(s.select_candidate(scored, annotations)['threshold'])

    def test_all_known_and_unknown_denominator_identities(self):
        result = s.summarize(calibration_scores(), truth(), .75)
        c = result['counts']
        self.assertEqual(c['known'], sum(c[x] for x in ('known_correct', 'known_wrong', 'known_refused', 'known_unavailable')))
        self.assertEqual(c['unknown'], sum(c[x] for x in ('unknown_accepted', 'unknown_refused', 'unknown_unavailable')))


class LockTests(unittest.TestCase):
    def make_lock(self, path):
        return s.write_candidate_lock(path, calibration_scores(), truth(), predictor('T'), protocol_sha256='a' * 64, evidence_pins=synthetic_evidence(), holdout_diagnostic_predictor=predictor('T'))

    def test_lock_required_before_t_vectors_are_touched(self):
        class Untouchable(dict):
            def __iter__(self):
                raise AssertionError('T vectors touched')
        with self.assertRaisesRegex(ValueError, 'before scoring T'):
            s.score_population(predictor('T'), Untouchable())

    def test_lock_exclusive_and_t_only_fixed_thresholds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            receipt = self.make_lock(path)
            self.assertEqual(receipt['lock']['threshold'], .78)
            with self.assertRaises(FileExistsError):
                self.make_lock(path)
            scored = s.score_population(predictor('T'), vectors('T'), candidate_lock=path, lock_sha256=receipt['sha256'])
            for t in (.78, .75):
                self.assertEqual(s.summarize(scored, truth('T'), t)['counts']['planned'], 3)
            with self.assertRaises(ValueError):
                s.summarize(scored, truth('T'), .76)
            with self.assertRaises(ValueError):
                s.select_candidate(scored, truth('T'))

    def test_lock_tampering_or_population_changes_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            receipt = self.make_lock(path)
            p = predictor('T')
            p['queries'].append('Textra')
            with self.assertRaisesRegex(ValueError, 'population'):
                s.score_population(p, {}, candidate_lock=path, lock_sha256=receipt['sha256'])
            path.write_bytes(path.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                s.score_population(predictor('T'), {}, candidate_lock=path, lock_sha256=receipt['sha256'])

    def test_no_candidate_means_no_lock_written(self):
        scored = calibration_scores()
        scored['rows'][0]['unavailable_reason'] = 'failed'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            with self.assertRaisesRegex(ValueError, 'stop before T'):
                s.write_candidate_lock(path, scored, truth(), predictor('T'), protocol_sha256='a' * 64, evidence_pins=synthetic_evidence(), holdout_diagnostic_predictor=predictor('T'))
            self.assertFalse(path.exists())


class BinomialTests(unittest.TestCase):
    def interval(self, k, n, **kwargs):
        return s.binomial_interval(k, n, independence_basis='Synthetic iid Bernoulli unit-test fixture only',
                                   denominator='One independent trial, not an audio pair', **kwargs)

    def test_zero_errors_not_zero_upper_bound(self):
        interval = self.interval(0, 30, sides='upper')
        self.assertAlmostEqual(interval['upper'], 1 - .05 ** (1 / 30), places=14)
        self.assertGreater(interval['upper'], .09)
        self.assertGreater(self.interval(0, 30)['upper'], interval['upper'])

    def test_nonzero_errors_match_known_exact_quantiles(self):
        interval = self.interval(1, 10)
        self.assertAlmostEqual(interval['lower'], .00252857854446178, places=13)
        self.assertAlmostEqual(interval['upper'], .4450161170281954, places=13)
        self.assertAlmostEqual(self.interval(1, 10, sides='upper')['upper'], .3941633024365047, places=12)
        mirror = self.interval(9, 10)
        self.assertAlmostEqual(interval['lower'], 1 - mirror['upper'], places=13)
        self.assertAlmostEqual(interval['upper'], 1 - mirror['lower'], places=13)

    def test_all_errors_and_zero_trials(self):
        interval = self.interval(10, 10)
        self.assertEqual(interval['upper'], 1.)
        self.assertAlmostEqual(interval['lower'], .025 ** .1, places=13)
        empty = self.interval(0, 0)
        self.assertIsNone(empty['lower'])
        self.assertIsNone(empty['upper'])

    def test_explicit_qualification_and_integer_denominator_required(self):
        for k, n in [(True, 10), (0, 1.5), (-1, 10), (11, 10)]:
            with self.assertRaises(ValueError):
                self.interval(k, n)
        with self.assertRaises(ValueError):
            s.binomial_interval(0, 20, independence_basis='', denominator='queries')


class OrchestratorTests(unittest.TestCase):
    """Whole bridge with synthetic PCM and a fake extractor only."""
    def fake_stage(self, o, root, manifest_pin, fail_diagnostic=False):
        import numpy as np
        from test_native_runner import FakeExtractor
        manifest = o.h.read_json(manifest_pin['path'])
        split = manifest['partition']
        output = root / (split + '_run')
        output.mkdir()
        base = [vector(), vector(0, 1), vector(.82, .2), vector(.4, .78), vector(-1, -1),
                vector(.82, .2), vector(.82, .2), vector(.4, .78), vector(.4, .78), vector(-1, -1), vector(-1, -1)]
        calls = base + [base[0], base[0], base[1], base[1]]
        class FixtureExtractor(FakeExtractor):
            def compute(self, stream):
                self.calls += 1
                if fail_diagnostic and self.calls == 6:
                    raise RuntimeError('synthetic required diagnostic failure')
                return np.asarray(calls[self.calls - 1], dtype=np.float32)
        args = types.SimpleNamespace(manifest=manifest_pin['path'], manifest_sha=manifest_pin['sha256'],
                                     output=str(output / 'native'))
        with patch.object(o.runner, 'verify_environment'), patch.object(o.runner, 'verify_worker_bounds'), \
             patch.object(o.runner, 'new_extractor', return_value=FixtureExtractor()), \
             patch.object(o.h, 'loaded_ort', return_value=['fake-ort']):
            code = o.runner.worker(args)
        worker = o.h.read_json(output / 'native/result.json')
        ids = [sample['id'] for sample in manifest['samples']]
        terminal = {'schema': 1, 'partition': split, 'manifest_sha256': manifest_pin['sha256'],
                    'protocol_sha256': manifest['protocol_sha256'], 'release_pin': manifest['release_pin'],
                    'status': 'completed_bounded_native_stage' if code == 0 else 'failed',
                    'planned_ids': ids, 'planned_calls': len(ids) + 4,
                    'completed_ids': worker['completed_ids'], 'unrun_ids': worker['unrun_ids'],
                    'elapsed_seconds': .01, 'aggregate_cpu_seconds': .01,
                    'allocated_output_bytes': o.h.output_bytes(output),
                    'stage': {'returncode': code, 'positive_original_wait': True,
                              'owned_process_group_absent': True, 'termination_needed': False,
                              'cleanup_error': None, 'stop_reason': None},
                    'artifacts': [o.h.pinned(output / 'native' / name)
                                  for name in ('vectors.npz', 'result.json', 'journal.jsonl')]}
        o.h.write_json(output / 'TERMINAL.json', terminal)
        return o.h.pinned(output / 'TERMINAL.json')

    def fixture(self, directory, *, missing_primary=False, fail_diagnostic=False):
        import numpy as np
        import orchestrator as o
        root = Path(directory)
        def save(name, value):
            path = root / name
            o.h.write_json(path, value)
            return o.h.pinned(path)
        plans, records, inputs, diagnostics = {}, [], {}, {}
        for split in ('C', 'T'):
            suffixes = ('ref-a', 'ref-b', 'qa', 'qb', 'qu', 'qa1', 'qa2', 'qb1', 'qb2', 'qu1', 'qu2')
            sample_ids = [split + suffix for suffix in suffixes]
            samples, local = [], []
            for index, identity in enumerate(sample_ids):
                path = root / (identity + '.wav')
                pcm = struct.pack('<32000h', *([index + 1] * 32000))
                with wave.open(str(path), 'wb') as audio:
                    audio.setparams((1, 2, 16000, 32000, 'NONE', 'synthetic'))
                    audio.writeframes(pcm)
                floats = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / np.float32(32768)
                samples.append({'id': identity, 'wav': o.h.pinned(path),
                                'pcm_sha256': hashlib.sha256(pcm).hexdigest(),
                                'float32_sha256': hashlib.sha256(floats.tobytes()).hexdigest()})
                suffix = suffixes[index]
                who = suffix[-1] if suffix.startswith('ref') else suffix[1]
                role = 'reference' if suffix.startswith('ref') else 'primary' if len(suffix) == 2 else 'diagnostic'
                day = 1 if role == 'reference' else 2 if role == 'primary' else int(suffix[-1]) + 2
                date_string = '2026-' + ('01' if split == 'C' else '02') + '-%02d' % day
                local.append({'id': identity, 'split': split, 'speaker_id': split + who,
                              'person_id': who.upper() if who != 'u' else None,
                              'roster_role': 'unknown' if who == 'u' else 'known', 'role': role,
                              'recording_date': date_string, 'session_id': split + who + '@' + date_string})
            records += local
            plans[split] = o.runner.make_plan_template(split, samples, sample_ids[:2])
            cp = predictor(split)
            all_predictor = copy.deepcopy(cp)
            all_predictor['queries'] = [r['id'] for r in local if r['role'] != 'reference']
            all_truth = {r['id']: {'kind': r['roster_role'], 'person_id': r['person_id'],
                                   'speaker_id': r['speaker_id'], 'session_id': r['session_id']}
                         for r in local if r['role'] != 'reference'}
            ct = {q: all_truth[q] for q in cp['queries']}
            if missing_primary and split == 'C':
                cp['queries'].remove('Cqb')
                del ct['Cqb']
            inputs[split] = {'predictor': save(split + '_predictor.json', cp), 'truth': save(split + '_truth.json', ct)}
            diagnostics[split] = {'predictor': save(split + '_all_predictor.json', all_predictor),
                                  'truth': save(split + '_all_truth.json', all_truth)}
        protocol = {'scoring_source': o.h.pinned(s.__file__), 'orchestrator': o.h.pinned(o.__file__),
                    'scoring_inputs': inputs, 'diagnostic_inputs': diagnostics,
                    'source_roles': save('roles.json', {'schema': 1, 'source_selection_slots_sha256': 'a' * 64, 'records': records}),
                    'calibration_run_plan': save('C_plan.json', plans['C']),
                    'holdout_run_plan': save('T_plan.json', plans['T']),
                    'resource_limits': {'model_wall_seconds_total': 1800, 'model_cpu_seconds_total': 1200,
                                        'model_output_bytes_total': 64 * 1024**2},
                    'counts': {'K_per_split': 2, 'unknown_people_per_split': 1, 'primary_queries_per_split': 3,
                               'all_queries_per_split': 9, 'distinct_clips_total': 22, 'repeat_calls_total': 8, 'max_encoder_calls_total': 30},
                    'selection_rule': {'grid_start': 0, 'grid_stop': 1, 'grid_step': .01, 'margin': .10, 'baseline': .75,
                                       'C_primaries_only': True, 'C_max_wrong_known': 0, 'C_max_unknown_accepted': 0,
                                       'maximize_known_correct': True, 'tie_break': 'higher_threshold', 'no_useful_candidate': 'stop_before_T'},
                    'interval_policy': {'formal_population_or_safety_CI': None, 'C_selected_point_CI': None,
                                        'reference_only': 'conditional_iid_binomial_model_not_validated_sampling_guarantee',
                                        'confidence': .95, 'primary_n_known': 2, 'primary_n_unknown': 1}}
        protocol_pin = save('protocol.json', protocol)
        manifest = copy.deepcopy(plans['C'])
        manifest['protocol_sha256'] = protocol_pin['sha256']
        manifest_pin = save('C_manifest.json', manifest)
        terminal_pin = self.fake_stage(o, root, manifest_pin, fail_diagnostic)
        return o, protocol_pin, manifest_pin, terminal_pin, plans

    def test_full_sealed_c_selects_and_exact_t_is_released(self):
        with tempfile.TemporaryDirectory() as directory:
            o, pp, mp, tp, plans = self.fixture(directory)
            root = Path(directory)
            result = o.choose(pp, mp, tp, root / 'lock.json', root / 'selection.json')
            self.assertEqual(result['status'], 'candidate_locked')
            released = o.release_holdout(pp, result['lock_pin'], root / 'T_manifest.json')
            actual = o.h.read_json(released['path'])
            expected = copy.deepcopy(plans['T'])
            expected['protocol_sha256'], expected['release_pin'] = pp['sha256'], result['lock_pin']
            self.assertEqual(actual, expected)
            lock = s.read_candidate_lock(result['lock_pin']['path'], result['lock_pin']['sha256'])
            self.assertEqual(lock['evidence_pins']['calibration_manifest'], mp)
            self.assertEqual(lock['evidence_pins']['calibration_terminal'], tp)

    def test_missing_c_primary_blocks_without_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            o, pp, mp, tp, _ = self.fixture(directory, missing_primary=True)
            root = Path(directory)
            result = o.choose(pp, mp, tp, root / 'lock.json', root / 'selection.json')
            self.assertEqual(result['status'], 'BLOCKED_INCONCLUSIVE')
            self.assertFalse((root / 'lock.json').exists())

    def test_failed_required_diagnostic_blocks_even_when_primaries_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            o, pp, mp, tp, _ = self.fixture(directory, fail_diagnostic=True)
            root = Path(directory)
            result = o.choose(pp, mp, tp, root / 'lock.json', root / 'selection.json')
            self.assertEqual(result['status'], 'BLOCKED_INCONCLUSIVE')
            self.assertFalse((root / 'lock.json').exists())

    def test_changed_t_waveform_blocks_release(self):
        with tempfile.TemporaryDirectory() as directory:
            o, pp, mp, tp, plans = self.fixture(directory)
            root = Path(directory)
            result = o.choose(pp, mp, tp, root / 'lock.json', root / 'selection.json')
            path = Path(plans['T']['samples'][0]['wav']['path'])
            path.write_bytes(path.read_bytes() + b'changed')
            with self.assertRaises(ValueError):
                o.release_holdout(pp, result['lock_pin'], root / 'T_manifest.json')
            self.assertFalse((root / 'T_manifest.json').exists())

    def test_changed_frozen_t_plan_and_c_primary_pin_block(self):
        for mutation in ('T_plan.json', 'C_predictor.json'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                o, pp, mp, tp, _ = self.fixture(directory)
                root = Path(directory)
                path = root / mutation
                path.write_bytes(path.read_bytes() + b' ')
                result = o.choose(pp, mp, tp, root / 'lock.json', root / 'selection.json')
                self.assertEqual(result['status'], 'BLOCKED_INCONCLUSIVE')
                self.assertFalse((root / 'lock.json').exists())

    def test_arbitrary_new_c_manifest_cannot_replace_frozen_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            o, pp, mp, tp, _ = self.fixture(directory)
            root = Path(directory)
            manifest = o.h.read_json(mp['path'])
            manifest['samples'] = manifest['samples'][:-1]
            altered = root / 'altered_C_manifest.json'
            o.h.write_json(altered, manifest)
            result = o.choose(pp, o.h.pinned(altered), tp, root / 'lock.json', root / 'selection.json')
            self.assertEqual(result['status'], 'BLOCKED_INCONCLUSIVE')
            self.assertFalse((root / 'lock.json').exists())

    def test_t_truth_is_opened_only_after_predictions_are_sealed(self):
        with tempfile.TemporaryDirectory() as directory:
            o, pp, mp, tp, _ = self.fixture(directory)
            root = Path(directory)
            protocol = o.h.read_json(pp['path'])
            truth_paths = {protocol[key]['T']['truth']['path'] for key in ('scoring_inputs', 'diagnostic_inputs')}
            original_read = o._read
            opened_truth = []
            def guarded_read(pin):
                if pin['path'] in truth_paths:
                    self.assertTrue((root / 'T_predictions.json').exists())
                    opened_truth.append(pin['path'])
                return original_read(pin)
            with patch.object(o, '_read', side_effect=guarded_read):
                chosen = o.choose(pp, mp, tp, root / 'lock.json', root / 'C_report.json')
                self.assertEqual(opened_truth, [])
                manifest_pin = o.release_holdout(pp, chosen['lock_pin'], root / 'T_manifest.json')
                self.assertEqual(opened_truth, [])
                terminal_pin = self.fake_stage(o, root, manifest_pin)
                final = o.evaluate_holdout(pp, chosen['lock_pin'], manifest_pin, terminal_pin,
                                           root / 'T_predictions.json', root / 'T_report.json')
            self.assertEqual(len(opened_truth), 2)
            self.assertEqual(final['status'], 'holdout_descriptive_complete')
            report = o.h.read_json(final['report_pin']['path'])
            self.assertIsNone(report['formal_population_or_safety_ci'])
            tables = report['descriptive_reports']
            self.assertEqual(tables['primary']['candidate']['counts']['known'], 2)
            self.assertEqual(tables['all_three_queries']['candidate']['counts']['known'], 6)
            self.assertEqual(tables['two_extra_queries_only']['candidate']['counts']['known'], 4)
            intervals = report['conditional_iid_model']['working_points']['candidate']
            self.assertEqual(intervals['unknown_accepted']['upper']['trials'], 1)
            self.assertGreater(intervals['unknown_accepted']['upper']['upper'], .9)
            with self.assertRaises(FileExistsError):
                o.evaluate_holdout(pp, chosen['lock_pin'], manifest_pin, terminal_pin,
                                   root / 'T_predictions.json', root / 'another_report.json')

    def test_failed_t_keeps_truth_sealed_and_emits_no_prediction_seal(self):
        with tempfile.TemporaryDirectory() as directory:
            o, pp, mp, tp, _ = self.fixture(directory)
            root = Path(directory)
            chosen = o.choose(pp, mp, tp, root / 'lock.json', root / 'C_report.json')
            manifest_pin = o.release_holdout(pp, chosen['lock_pin'], root / 'T_manifest.json')
            terminal_pin = self.fake_stage(o, root, manifest_pin, fail_diagnostic=True)
            with self.assertRaisesRegex(ValueError, 'BLOCKED_INCONCLUSIVE'):
                o.evaluate_holdout(pp, chosen['lock_pin'], manifest_pin, terminal_pin,
                                   root / 'T_predictions.json', root / 'T_report.json')
            self.assertFalse((root / 'T_predictions.json').exists())
            self.assertFalse((root / 'T_report.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
