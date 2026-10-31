"""Synthetic, model-free event ownership and persistence regressions.

All times are generated from simple constants, PCM frames or nextafter. No
recording, captured model output, external fixture or model runtime is needed.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from audio_backend import event_candidate_store as ledger
from audio_backend.settings import Settings
from audio_backend.storage import Store, update_provenance
from audio_backend.worker import own_core


RATE = 16000
TOUCH = math.nextafter(1., math.inf)


def candidate(start=1., end=3.):
    return {'start': start, 'end': end,
            'top_labels': [{'label': 'Door', 'score': .25}],
            'category_scores': {'door': {'source_label': 'Door', 'score': .25}},
            'model_inference_executed': True}


def owned(candidates, start=.5, end=TOUCH):
    return own_core({'segments': [], 'events': [],
                     'metadata': {'event_candidates': copy.deepcopy(candidates)}}, start, end)


class NumericProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-event-')
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'test.sqlite3', Settings(data_dir=self.root, start_worker=False))
        self.sid = self.store.create_session('synthetic numeric ownership')['id']
        self.chunk_count = 0

    def tearDown(self):
        self.temp.cleanup()

    def chunk(self, origin=0., offset=0., duration=10.):
        self.chunk_count += 1
        key = str(self.chunk_count)
        # Prior cases replace timeline origins directly. Allocate the next
        # synthetic upload after their current extent before applying this case.
        with self.store.db() as db:
            upload_offset = db.execute('SELECT COALESCE(MAX(timeline_start+duration),0) '
                                       'FROM chunks WHERE session_id=?', (self.sid,)).fetchone()[0]
        cid, _ = self.store.add_chunk(self.sid, key, key, self.root / 'unused.wav', duration,
                                     offset=max(0., upload_offset))
        with self.store.db() as db:
            # Exercise projection directly, including origins rejected by upload validation.
            db.execute("UPDATE chunks SET status='running',timeline_start=?,next_offset=? WHERE id=?",
                       (origin, offset, cid))
        return self.store.chunk(cid)

    def rows(self):
        return self.store.event_candidates(self.sid)['event_candidates']

    def audit(self, chunk):
        return self.store.chunk(chunk['id'])['provenance']['event_candidate_projection_diagnostics']

    def test_one_ulp_touching_sliver_is_audited_and_main_text_commits(self):
        raw = candidate()
        self.assertGreater(TOUCH, raw['start'])
        self.assertEqual(2. ** 20 + TOUCH, 2. ** 20 + raw['start'])
        result = owned([raw])
        result['segments'] = [{'start': .125, 'end': .25,
                               'text': 'synthetic text', 'raw_text': 'synthetic raw text'}]
        self.assertEqual(result['event_candidates'], [])
        self.assertEqual(result['metadata']['event_candidates'], [raw])
        chunk = self.chunk(origin=2. ** 20)
        self.assertTrue(self.store.commit_window(chunk, 0., .5, result))
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.store.results(self.sid)['segments'][0]['raw_text'], 'synthetic raw text')
        self.assertEqual(self.store.chunk(chunk['id'])['next_offset'], .5)
        audit = self.audit(chunk)
        self.assertEqual(audit['count'], 1)
        self.assertFalse(audit['truncated'])
        self.assertEqual(audit['records'][0]['intersection_frames'], [RATE, RATE])
        self.assertEqual(audit['records'][0]['reason'], 'numeric_degenerate_sample_grid_intersection')
        self.assertEqual(audit['records'][0]['raw_support_seconds'], [1., 3.])

    def test_touching_or_outside_support_is_not_a_numeric_diagnostic(self):
        for raw in (candidate(1., 2.), candidate(2., 3.)):
            with self.subTest(raw=raw):
                result = owned([raw], 0., 1.)
                self.assertEqual(result['event_candidates'], [])
                self.assertNotIn('event_candidate_projection_diagnostics', result['metadata'])

    def test_adjacent_cores_retain_a_single_sample_without_a_duplicate(self):
        raw = candidate(1., 1. + 1 / RATE)
        left = owned([raw], .5, TOUCH)
        right = owned([raw], TOUCH, 1.5)
        self.assertEqual(left['event_candidates'], [])
        self.assertEqual(len(right['event_candidates']), 1)
        item = right['event_candidates'][0]
        self.assertEqual(item['start'], 0.)
        self.assertEqual(item['end'], raw['end'] - TOUCH)
        self.assertGreater(item['end'], 0.)
        self.assertEqual(right['metadata']['event_candidates'], [raw])

    def test_one_sample_half_grid_ties_survive_both_parities(self):
        for frame in range(16):
            with self.subTest(frame=frame):
                start, end = (frame + .5) / RATE, (frame + 1.5) / RATE
                result = owned([candidate(0., 4.)], start, end)
                self.assertEqual(len(result['event_candidates']), 1)
                self.assertEqual(result['event_candidates'][0]['end'], end - start)
                self.assertNotIn('event_candidate_projection_diagnostics', result['metadata'])

    def test_full_detector_range_round_trips_integer_and_half_frame_boundaries(self):
        # Exhaustive conversion sweep across the accepted 0..120-second input
        # domain. Full ownership/persistence cases below cover the integration.
        for frame in range(120 * RATE):
            self.assertEqual(ledger._sample_boundary(frame / RATE), frame)
            start = (frame + .5) / RATE
            self.assertEqual(ledger._sample_boundary(start), frame + 1)
            if frame < 120 * RATE - 1:
                self.assertEqual(ledger._sample_boundary(start + 1 / RATE), frame + 2)

    def test_half_grid_roundoff_cases_survive_full_ownership_and_varied_origins(self):
        frames = (9, 499, 2002, 2006, 3999, 16001, 127999, 511999, 1023999, 1919998)
        for frame in frames:
            start = (frame + .5) / RATE
            for end in ((frame + 1.5) / RATE, start + 1 / RATE):
                # Detector supports themselves stay aligned to integer PCM frames.
                support_start = math.floor(start)
                support_end = min(120., support_start + 2.)
                result = owned([candidate(support_start, support_end)], start, end)
                self.assertEqual(len(result['event_candidates']), 1, frame)
                self.assertNotIn('event_candidate_projection_diagnostics', result['metadata'])
                for origin in (0., 512., -512., 2. ** 30, -2. ** 30):
                    with self.subTest(frame=frame, origin=origin, end=end):
                        chunk = self.chunk(origin=origin, duration=end - start)
                        self.assertTrue(self.store.commit_window(chunk, 0., end - start, result))
                        row = next(row for row in self.rows() if row['chunk_id'] == chunk['id'])
                        self.assertEqual(row['start'], origin)
                        self.assertEqual(row['end'], origin + (end - start))
                        self.assertGreater(row['end'], row['start'])

    def test_uncertainty_convention_is_local_to_a_half_frame(self):
        for frame in (0, 499, 2002, 127999, 1023999):
            midpoint = (frame + .5) / RATE
            near = math.nextafter(midpoint, -math.inf)
            scaled = near * RATE
            uncertainty = (3 * math.ulp(near) * RATE + math.ulp(scaled)) / 2
            if frame + .5 - scaled <= uncertainty:
                self.assertEqual(ledger._sample_boundary(near), frame + 1)
            below, above = midpoint, midpoint
            for _ in range(8):
                below = math.nextafter(below, -math.inf)
                above = math.nextafter(above, math.inf)
            self.assertEqual(ledger._sample_boundary(below), frame)
            self.assertEqual(ledger._sample_boundary(above), frame + 1)
            # The recovery band is a fraction of an ULP-derived frame amount,
            # not a sample-sized or recording-duration-based epsilon.
            self.assertLess(uncertainty, 1e-8)

    def test_adjacent_half_grid_cores_share_one_quantized_boundary(self):
        for frame in (499, 2002, 2006, 127999, 1023999):
            a, b, c = [(frame + shift) / RATE for shift in (.5, 1.5, 2.5)]
            raw = candidate(math.floor(a), math.floor(a) + 2.)
            left, right = owned([raw], a, b), owned([raw], b, c)
            self.assertEqual(len(left['event_candidates']), 1)
            self.assertEqual(len(right['event_candidates']), 1)
            left_frames = (ledger._sample_boundary(a), ledger._sample_boundary(b))
            right_frames = (ledger._sample_boundary(b), ledger._sample_boundary(c))
            self.assertEqual(left_frames[1], right_frames[0])
            self.assertEqual(right_frames[1] - left_frames[0], 2)
            self.assertEqual(left['event_candidates'][0]['end'], b - a)
            self.assertEqual(right['event_candidates'][0]['end'], c - b)

    def test_adjacent_detector_supports_are_not_duplicated(self):
        for frame in (499, 2002, 127999):
            a, b, c = [value / RATE for value in (frame, frame + 1, frame + 2)]
            raw = [candidate(a, b), candidate(b, c)]
            left, right = owned(raw, a, b), owned(raw, b, c)
            self.assertEqual(len(left['event_candidates']), 1)
            self.assertEqual(len(right['event_candidates']), 1)
            self.assertEqual(left['event_candidates'][0]['_source_candidate_index'], 0)
            self.assertEqual(right['event_candidates'][0]['_source_candidate_index'], 1)
            self.assertNotIn('event_candidate_projection_diagnostics', left['metadata'])
            self.assertNotIn('event_candidate_projection_diagnostics', right['metadata'])

    def test_subsample_intersections_follow_boundary_ownership_without_time_inflation(self):
        raw = candidate(0., 2.)
        for start, end in ((1., 1.), (1., math.nextafter(1., math.inf)),
                           (.125 / RATE, .25 / RATE)):
            result = owned([raw], start, end)
            self.assertEqual(result['event_candidates'], [])
            if end > start:
                note = result['metadata']['event_candidate_projection_diagnostics'][0]
                self.assertEqual(note['intersection_frames'][0], note['intersection_frames'][1])
                self.assertEqual(note['intersection_input_seconds'], [start, end])
            else:
                self.assertNotIn('event_candidate_projection_diagnostics', result['metadata'])
        # A short interval crossing a rounding boundary owns a frame index but
        # keeps its exact fractional duration; it is not stretched to one sample.
        start, end = .49 / RATE, .51 / RATE
        result = owned([raw], start, end)
        self.assertEqual(len(result['event_candidates']), 1)
        self.assertEqual(result['event_candidates'][0]['end'], end - start)
        self.assertLess(end - start, 1 / RATE)

    def test_detector_hop_touching_sliver_is_still_durably_audited(self):
        raw = candidate(2.5, 5.)
        result = owned([raw], 2., math.nextafter(2.5, math.inf))
        self.assertEqual(result['event_candidates'], [])
        chunk = self.chunk()
        self.assertTrue(self.store.commit_window(chunk, 0., .5, result))
        note = self.audit(chunk)['records'][0]
        self.assertEqual(note['intersection_frames'], [40000, 40000])
        self.assertEqual(note['raw_support_seconds'], [2.5, 5.])
        self.assertEqual(note['reason'], 'numeric_degenerate_sample_grid_intersection')

    def test_prior_cancellation_outside_supported_bound_remains_explicit(self):
        # Known limitation: rounding before subtraction can exceed a ULP bound
        # based on the final local float. No generic rebase invariance is claimed.
        origin = 1.
        start = (origin + 2.5 / RATE) - origin
        end = (origin + 3.5 / RATE) - origin
        result = owned([candidate(0., 1.)], start, end)
        self.assertEqual(result['event_candidates'], [])
        note = result['metadata']['event_candidate_projection_diagnostics'][0]
        self.assertEqual(note['intersection_frames'], [3, 3])
        self.assertEqual(note['intersection_input_seconds'], [start, end])
        self.assertEqual(note['reason'], 'numeric_degenerate_sample_grid_intersection')

    def test_aligned_one_sample_survives_varied_origins_and_boundaries(self):
        for origin in (0., 512., -512., 2. ** 30, -2. ** 30, 2. ** 34, -2. ** 34):
            chunk = self.chunk(origin=origin)
            for frame in (0, 1, 2, 3, RATE, RATE + 1):
                start, end = frame / RATE, (frame + 1) / RATE
                result = owned([candidate(start, end)], start, end)
                with self.subTest(origin=origin, frame=frame):
                    self.assertEqual(len(result['event_candidates']), 1)
                    with self.store.db() as db:
                        self.assertEqual(ledger.commit_candidates(db, chunk, start,
                            result['event_candidates'], result['metadata']), 1)
                    row = next(row for row in self.rows()
                               if row['chunk_id'] == chunk['id'] and row['analysis_offset'] == start)
                    self.assertGreater(row['end'], row['start'])
                    self.assertEqual(row['start'], origin + start)
                    self.assertEqual(row['end'], (origin + start) + (end - start))

    def test_surviving_interval_keeps_original_float_support(self):
        raw = candidate(.25, 1.75)
        result = owned([raw], .5, 1.25)
        item = result['event_candidates'][0]
        self.assertEqual((item['start'], item['end']), (0., .75))
        self.assertEqual((item['support_start'], item['support_end']), (-.25, 1.25))
        self.assertEqual(item['top_labels'], raw['top_labels'])
        self.assertEqual(result['metadata']['event_candidates'], [raw])
        chunk = self.chunk(origin=512.)
        self.assertTrue(self.store.commit_window(chunk, 0., .75, result))
        row = self.rows()[0]
        self.assertEqual((row['start'], row['end']), (512., 512.75))
        self.assertEqual((row['support_start'], row['support_end']), (511.75, 513.25))
        self.assertEqual(row['status'], 'unverified_model_candidate')
        self.assertFalse(row['scores_calibrated'])
        self.assertEqual(self.store.events(self.sid)['events'], [])

    def test_unrepresentable_absolute_one_sample_is_explicitly_audited(self):
        result = owned([candidate(0., 1 / RATE)], 0., 1 / RATE)
        before = copy.deepcopy(result)
        chunk = self.chunk(origin=2. ** 60)
        self.assertTrue(self.store.commit_window(chunk, 0., 1., result))
        self.assertEqual(result, before)
        self.assertEqual(self.rows(), [])
        note = self.audit(chunk)['records'][0]
        self.assertEqual(note['reason'], 'numeric_degenerate_absolute_projection')
        self.assertEqual(note['owned_core_seconds'], [0., 1 / RATE])
        self.assertEqual(note['absolute_seconds'][:2], [2. ** 60] * 2)
        self.assertEqual(note['raw_time_axis'], 'analyzer_input')

    def test_legacy_absolute_sliver_uses_core_relative_fallback(self):
        start, end = .5, math.nextafter(.5, math.inf)
        item = dict(candidate(), start=start, end=end, support_start=0., support_end=2.,
                    time_axis='core_relative', ownership='core_intersection')
        result = {'segments': [], 'events': [], 'event_candidates': [item], 'metadata': {}}
        chunk = self.chunk(origin=2. ** 20)
        self.assertTrue(self.store.commit_window(chunk, 0., 1., result))
        note = self.audit(chunk)['records'][0]
        self.assertEqual(note['reason'], 'numeric_degenerate_absolute_projection')
        self.assertEqual(note['raw_support_seconds'], [start, end])
        self.assertEqual(note['raw_time_axis'], 'core_relative')
        self.assertEqual(self.rows(), [])

    def test_exclusion_still_validates_candidates_and_count(self):
        invalid = [None, candidate(1., 1.), candidate(1., float('nan')),
                   dict(candidate(), top_labels='bad'),
                   dict(candidate(), top_labels=[{'label': 'Door', 'score': 1.25}]),
                   dict(candidate(), onset_precision_seconds=-1)]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                owned([raw])
        with self.assertRaisesRegex(ValueError, 'window limit'):
            owned([candidate()] * (ledger.MAX_WINDOWS_PER_COMMIT + 1))

    def test_durable_evidence_filters_labels_and_unknown_fields(self):
        raw = candidate()
        private = 'Female speech, woman speaking'
        raw['top_labels'].append({'label': private, 'score': .75})
        raw['category_scores']['music'] = {'source_label': private, 'score': .5}
        raw['unknown_field'] = 'synthetic value that must not enter the durable audit'
        result = owned([raw])
        chunk = self.chunk()
        self.store.commit_window(chunk, 0., .5, result)
        audit = self.audit(chunk)
        self.assertNotIn(private, json.dumps(audit))
        self.assertNotIn('unknown_field', json.dumps(audit))
        self.assertEqual(self.store.chunk(chunk['id'])['metadata']['event_candidates'], [raw])
        note = audit['records'][0]
        encoded = json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        self.assertEqual(note['raw_candidate_sha256'], hashlib.sha256(encoded.encode()).hexdigest())
        self.assertTrue(note['candidate_evidence_sanitized'])

    def test_record_cap_is_explicit_and_preserves_first_evidence(self):
        metadata = owned([candidate()])['metadata']
        previous = None
        for offset in range(ledger.MAX_PROJECTION_RECORDS + 3):
            current = update_provenance(previous, metadata, offset)
            previous = json.dumps(current)
        audit = current['event_candidate_projection_diagnostics']
        self.assertEqual(audit['count'], ledger.MAX_PROJECTION_RECORDS + 3)
        self.assertEqual(len(audit['records']), ledger.MAX_PROJECTION_RECORDS)
        self.assertEqual([r['analysis_offset'] for r in audit['records']], list(range(64)))
        self.assertTrue(audit['truncated'])

    def test_byte_cap_includes_persisted_unicode_escaping_and_wrapper(self):
        raw = candidate()
        raw['top_labels'] = [{'label': '\u96e8' * 250 + str(i), 'score': .25} for i in range(8)]
        metadata = owned([raw])['metadata']
        previous = None
        for offset in range(150):
            current = update_provenance(previous, metadata, offset)
            previous = json.dumps(current)
            audit = current['event_candidate_projection_diagnostics']
            self.assertLessEqual(len(json.dumps(audit).encode()), ledger.MAX_PROJECTION_BYTES)
        self.assertEqual(audit['count'], 150)
        self.assertTrue(audit['truncated'])
        self.assertLess(len(audit['records']), ledger.MAX_PROJECTION_RECORDS)

    def test_oversized_record_is_counted_without_blocking_later_small_evidence(self):
        metadata = owned([candidate()])['metadata']
        oversized = copy.deepcopy(metadata)
        oversized['event_candidate_projection_diagnostics'][0]['candidate_evidence']['extra'] = 'x' * 65537
        current = update_provenance(None, oversized, 0)
        self.assertEqual(current['event_candidate_projection_diagnostics'],
                         {'count': 1, 'records': [], 'truncated': True})
        later = update_provenance(json.dumps(current), metadata, 1)
        audit = later['event_candidate_projection_diagnostics']
        self.assertEqual(audit['count'], 2)
        self.assertEqual(len(audit['records']), 1)
        self.assertTrue(audit['truncated'])

    def test_audit_survives_later_empty_and_silence_windows(self):
        metadata = owned([candidate()])['metadata']
        first = update_provenance(None, metadata, 0)
        for later in ({'event_candidates': []}, {'silence_fast_path': True, 'models': {}}):
            current = update_provenance(json.dumps(first), later, 1)
            self.assertEqual(current['event_candidate_projection_diagnostics'],
                             first['event_candidate_projection_diagnostics'])
        metadata['silence_fast_path'] = True
        current = update_provenance(None, metadata, 0)
        self.assertEqual(current['variants'], [])
        self.assertEqual(current['event_candidate_projection_diagnostics']['count'], 1)

    def test_core_rollback_then_retry_is_atomic_and_preserves_input(self):
        result = owned([candidate(.5, .75), candidate()])
        result['segments'] = [{'start': 0., 'end': .25, 'text': 'synthetic rollback'}]
        before = copy.deepcopy(result)
        chunk = self.chunk()
        with patch('audio_backend.storage.update_provenance', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                self.store.commit_window(chunk, 0., .5, result)
        self.assertEqual(result, before)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.store.results(self.sid)['segments'], [])
        self.assertEqual(self.store.chunk(chunk['id'])['next_offset'], 0.)
        self.assertTrue(self.store.commit_window(chunk, 0., .5, result))
        self.assertEqual(len(self.rows()), 1)
        snapshot = self.store.chunk(chunk['id'])
        self.assertFalse(self.store.commit_window(chunk, 0., .5, result))
        self.assertEqual(self.store.chunk(chunk['id']), snapshot)
        self.assertEqual(self.audit(chunk)['count'], 1)

    def test_mixed_projection_diagnostics_do_not_mutate_rollback_input(self):
        result = owned([candidate()])
        result['event_candidates'] = [dict(candidate(), start=.5, end=math.nextafter(.5, math.inf),
            support_start=0., support_end=2., time_axis='core_relative', ownership='core_intersection')]
        before = copy.deepcopy(result)
        chunk = self.chunk(origin=2. ** 20)
        with patch('audio_backend.storage.update_provenance', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.store.commit_window(chunk, 0., .5, result)
        self.assertEqual(result, before)
        self.assertTrue(self.store.commit_window(chunk, 0., .5, result))
        self.assertEqual(result, before)
        self.assertEqual(self.audit(chunk)['count'], 2)

    def test_direct_candidate_retry_is_idempotent(self):
        result = owned([candidate(0., 1.)], 0., 1.)
        chunk = self.chunk()
        for expected in (1, 0):
            with self.store.db() as db:
                self.assertEqual(ledger.commit_candidates(db, chunk, 0.,
                    result['event_candidates'], result['metadata']), expected)
        self.assertEqual(len(self.rows()), 1)

    def test_numeric_projection_does_not_change_schema(self):
        def schema():
            with self.store.db() as db:
                return list(db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name'))
        before = schema()
        chunk = self.chunk()
        self.store.commit_window(chunk, 0., .5, owned([candidate()]))
        self.assertEqual(schema(), before)

    def test_main_event_and_speaker_ownership_is_unchanged(self):
        source = {'segments': [{'start': .5, 'end': 1.5, 'text': 'synthetic', 'raw_text': 'raw synthetic'}],
                  'events': [{'start': .5, 'end': 2., 'label': 'door', 'score': .25}],
                  'speaker_turns': [{'start': .25, 'end': 2., 'speaker_local_id': 'synthetic-local'}],
                  'metadata': {'event_candidates': [candidate(0., 3.)]}}
        result = own_core(copy.deepcopy(source), 1., 2.)
        self.assertEqual(result['segments'], [dict(source['segments'][0], start=0., end=.5)])
        self.assertEqual(result['events'], [dict(source['events'][0], start=0., end=1.)])
        self.assertEqual(result['speaker_turns'], [dict(source['speaker_turns'][0], start=0., end=1.)])
        self.assertEqual(result['metadata']['event_candidates'], source['metadata']['event_candidates'])


if __name__ == '__main__':
    unittest.main()
