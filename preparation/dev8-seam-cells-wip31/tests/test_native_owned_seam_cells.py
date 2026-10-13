"""Synthetic two-commit contracts only. Never acoustic/native qualification."""
from dataclasses import asdict, replace
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from audio_backend import native_owned_seam_cells as seam
from audio_backend import native_owned_cells as cells
from audio_backend.native_owned_cell_capture import Capture
from audio_backend.native_owned_cell_store import Budget
from audio_backend.settings import Settings
from test_native_owned_support import synthetic_provenance, synthetic_state
from test_native_owned_cells import synthetic_cell

EPOCH = 'a' * 32


def synthetic_pieces(*, frames=16, sid='session', cids=('left', 'right'),
        receipt_ids=('b' * 64, 'd' * 64), source_hashes=('c' * 64, 'e' * 64),
        origin=0., changed_evidence=False):
    """Two completed-shape uploads, bounded synthetic PCM; no live authority."""
    scope = {'session_id': sid, 'stream_id': 'stream', 'origin_seconds_hex': float(origin).hex(),
             'epoch_id': EPOCH, 'epoch_index': 0, 'reset_generation': 1}
    provenance = synthetic_provenance(); total = 2 * frames; full = b'\x01\0' * total
    observed = {'epoch_id': EPOCH, 'finished': True, 'fingerprint': provenance['diarization_fingerprint'],
                'local_capacity': 8}
    for name in ('input', 'processed', 'stable'):
        raw = total / 16000; observed[name] = {'raw': raw, 'hex': float(raw).hex(), 'frame': total}
    native = {'epoch_id': EPOCH, 'epoch_index': 0, 'owned_start': origin, 'owned_end': origin + 300.,
              'replay_start': origin, 'input_until': origin + total / 16000,
              'processed_until': origin + total / 16000, 'stable_until': origin + total / 16000,
              'finished': True, 'provenance': provenance}
    result = []
    for index in range(2):
        start, end = index * frames, (index + 1) * frames
        lo, hi = (max(0, frames - seam.SIDE_FRAMES), frames) if index == 0 else (frames, min(total, frames + seam.SIDE_FRAMES))
        pcm = full[lo * 2:hi * 2]
        part = cells.SourceSpan(sid, cids[index], receipt_ids[index], source_hashes[index], frames,
                                lo - start, hi - start, lo, hi)
        cut = {'nominal': frames / 16000, 'actual': frames / 16000, 'reason': 'fixed_or_final'}
        if changed_evidence and index == 0:cut = dict(cut, nominal=cut['nominal'] + 1., reason='neural_speech_gap')
        evidence = {'chunk_id': cids[index], 'start': 0, 'end': frames, 'native': deepcopy(native), 'core_cut': cut}
        commit = {'chunk_id': cids[index], 'source_origin_frame': start, 'local_start_frame': 0,
                  'local_end_frame': frames, 'logical_start_frame': start, 'logical_end_frame': end,
                  'evidence': evidence, 'evidence_sha256': cells.sha(evidence)}
        witness = {'commit_index': index, 'query_start_frame': 0, 'query_end_frame': total,
            'analyzer_start_frame': 0, 'analyzer_end_frame': total,
            'analyzer_pcm16le_sha256': hashlib.sha256(full).hexdigest(),
            'piece_pcm16le_sha256': hashlib.sha256(pcm).hexdigest(),
            'native': {'epoch_id': EPOCH, 'epoch_index': 0, 'replay_start': 0, 'state': deepcopy(observed),
                       'provenance': deepcopy(provenance), 'observed_action_count': 5 + index, 'true_eof': True},
            'turns': [{'label': EPOCH + ':speaker_' + str(i), 'original': [0, total]} for i in (1, 2)]}
        result.append(seam.Piece(deepcopy(scope), commit, witness, part, pcm))
    return tuple(result)


def synthetic_composite(**kwargs):
    return seam.make_composite(*synthetic_pieces(**kwargs))


class SeamProofTests(unittest.TestCase):
    def test_pinned_policy_hashes_and_unchanged_v1_identity(self):
        self.assertEqual(seam.POLICY_SHA256, 'b8480ec44a75c456a6e036cfa8d28d2b03829e2769f438178f4b29b14f2425b1')
        self.assertEqual(seam.STORE_POLICY_SHA256, 'fece6df7a046b4ac0b94e608e002f580f5df8a42c2f90ccd17f6a950b1184e64')
        old = synthetic_cell(); doc, support, parts = cells.validate_document(old.proof)
        self.assertEqual(old.cell_id, cells.sha({'policy': cells.POLICY_SHA256, 'support': old.support.to_dict()}))
        self.assertEqual(old.ownership, cells.sha({'session': old.session_id, 'chunk': old.chunk_id,
            'core': [old.commit_start, old.commit_end], 'cell': [support.start_frame, support.end_frame]}))
        self.assertIsNone(seam.coverage(doc)); self.assertNotIn('schema', doc)

    def test_symmetric_ten_point_five_second_geometry_and_nonzero_source_offset(self):
        cell = synthetic_composite(frames=168000, origin=64.)
        doc, support, parts = cells.validate_document(cell.proof)
        self.assertEqual((support.start_frame, support.end_frame), (8000, 328000))
        self.assertEqual([(p.source_start, p.source_end) for p in parts], [(8000, 168000), (0, 160000)])
        self.assertEqual(len(cell.pcm), 640000)
        self.assertEqual(doc['scope']['origin_seconds_hex'], (64.).hex())
        self.assertEqual([c['logical_start_frame'] for c in doc['commits']], [0, 168000])

    def test_short_halves_and_one_frame_positive_bounds(self):
        self.assertEqual(seam.geometry(4, 5, 8), {'seam_frame': 5, 'left_frames': 1,
            'right_frames': 3, 'start_frame': 4, 'end_frame': 8})
        cell = synthetic_composite(frames=1)
        self.assertEqual(len(cell.pcm), 4)
        for args in ((0, 0, 1), (0, 1, 960001), (True, 2, 3), (0, 3, 2)):
            with self.subTest(args=args), self.assertRaises(cells.Unavailable):seam.geometry(*args)

    def test_same_response_overlap_only_touching_and_same_label_decline(self):
        left, right = synthetic_pieces()
        one = deepcopy(left.witness); two = deepcopy(right.witness)
        one['turns'] = one['turns'][:1]; two['turns'] = two['turns'][1:]
        with self.assertRaisesRegex(cells.Unavailable, 'no_same_response'):seam.make_composite(replace(left, witness=one), replace(right, witness=two))
        one['turns'] = [{'label': EPOCH + ':speaker_1', 'original': [0, 16]},
                        {'label': EPOCH + ':speaker_2', 'original': [16, 32]}]
        with self.assertRaisesRegex(cells.Unavailable, 'no_same_response'):seam.selected_pair([one], 0, 32)
        one['turns'][1] = dict(one['turns'][0])
        with self.assertRaisesRegex(cells.Unavailable, 'no_same_response'):seam.selected_pair([one], 0, 32)

    def test_selection_is_lexicographic_and_envelope_is_not_b_anchor(self):
        cell = synthetic_composite(); doc = json.loads(cell.proof)
        self.assertEqual(doc['selection']['witness_index'], 0)
        self.assertEqual(doc['selection']['turn_indexes'], [0, 1])
        self.assertEqual(doc['coverage']['coverage_envelope_frames'], [0, 32])
        self.assertEqual((cell.commit_start, cell.commit_end), (0, 16))
        self.assertEqual(doc['publication_anchor']['chunk_id'], 'right')
        self.assertFalse(doc['coverage']['adds_primary_audio']); self.assertFalse(doc['coverage']['automatic_dispatch'])
        self.assertEqual(len(doc['coverage']['primary_commit_refs']), 2)

    def test_replay_identity_and_changed_evidence_stable_ownership(self):
        first = synthetic_composite(); replay = synthetic_composite(); changed = synthetic_composite(changed_evidence=True)
        self.assertEqual(first.cell_id, replay.cell_id); self.assertEqual(first.proof, replay.proof)
        self.assertEqual(first.ownership, changed.ownership)
        self.assertNotEqual(first.cell_id, changed.cell_id); self.assertNotEqual(first.proof, changed.proof)

    def test_closed_nested_schema_missing_extra_keys_and_wrong_lengths(self):
        base = json.loads(synthetic_composite().proof)
        targets = [(), ('scope',), ('geometry',), ('support',), ('dependencies', 0), ('commits', 0),
            ('commits', 0, 'evidence'), ('commits', 0, 'evidence', 'native'), ('commits', 0, 'evidence', 'core_cut'),
            ('native_witnesses', 0), ('native_witnesses', 0, 'native'), ('native_witnesses', 0, 'native', 'state'),
            ('native_witnesses', 0, 'native', 'state', 'input'), ('native_witnesses', 0, 'turns', 0),
            ('selection',), ('publication_anchor',), ('coverage',)]
        for path in targets:
            for mutation in ('extra', 'missing'):
                doc = deepcopy(base); node = doc
                for key in path:node = node[key]
                if mutation == 'extra':node['extra'] = 1
                else:node.pop(next(iter(node)))
                with self.subTest(path=path, mutation=mutation), self.assertRaises((ValueError, TypeError, KeyError)):
                    cells.validate_document(cells.canonical(doc))
        for key in ('commits', 'native_witnesses', 'dependencies'):
            doc = deepcopy(base); doc[key].append(deepcopy(doc[key][0]))
            with self.assertRaises(cells.Unavailable):cells.validate_document(cells.canonical(doc))

    def test_bool_nan_noncanonical_and_query_transform_rejected(self):
        base = json.loads(synthetic_composite().proof)
        for path, value in [(('scope', 'reset_generation'), True), (('scope', 'epoch_index'), True),
                (('geometry', 'left_frames'), True), (('native_witnesses', 0, 'query_start_frame'), True),
                (('native_witnesses', 0, 'native', 'state', 'input', 'hex'), 'wrong'),
                (('native_witnesses', 0, 'turns', 0, 'original'), [0, 33])]:
            doc = deepcopy(base); node = doc
            for key in path[:-1]:node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path), self.assertRaises((ValueError, TypeError)):cells.validate_document(cells.canonical(doc))
        with self.assertRaises(ValueError):cells.canonical(dict(base, bad=float('nan')))
        with self.assertRaises(cells.Unavailable):cells.validate_document(json.dumps(base).encode())

    def test_scope_epoch_generation_gap_and_third_core_decline(self):
        left, right = synthetic_pieces()
        for key, value in [('stream_id', 'other'), ('epoch_id', 'f' * 32), ('reset_generation', 2)]:
            with self.subTest(key=key), self.assertRaises(cells.Unavailable):
                seam.make_composite(left, replace(right, scope=dict(right.scope, **{key: value})))
        commit = deepcopy(right.commit); commit['logical_start_frame'] += 1
        with self.assertRaises(cells.Unavailable):seam.make_composite(left, replace(right, commit=commit))

    def test_piece_hash_and_turn_capacity_cannot_bypass_cell_admission(self):
        left, right = synthetic_pieces()
        with self.assertRaises(cells.Unavailable):replace(left, pcm=b'\2\0' * 16)
        witness = deepcopy(left.witness); witness['turns'] *= 129
        with self.assertRaises(cells.Unavailable):seam.make_composite(replace(left, witness=witness), right)
        cell = synthetic_composite(); doc = json.loads(cell.proof)
        doc['native_witnesses'][0]['piece_pcm16le_sha256'] = 'f' * 64
        with self.assertRaises(cells.Unavailable):replace(cell, proof=cells.canonical(doc))

    def test_opt_in_default_off_type_and_dependency(self):
        self.assertFalse(Settings().native_owned_seam_cells_enabled)
        for value in (1, 'yes'):
            with self.assertRaises(ValueError):Settings(native_owned_seam_cells_enabled=value)
        with self.assertRaises(ValueError):Settings(native_owned_seam_cells_enabled=True)
        self.assertTrue(Settings(native_owned_cells_enabled=True, native_owned_seam_cells_enabled=True).native_owned_seam_cells_enabled)


class SeamCaptureLifetimeTests(unittest.TestCase):
    def capture(self):
        owner = SimpleNamespace(enabled=True, seam_enabled=True, _epoch=object(), health=lambda: {'ready': True},
                                close=lambda: True)
        authority = SimpleNamespace(seam_commit_current=lambda *a: True, cell_valid=lambda *a, **k: True)
        result = Capture(owner, authority); result.scope = ('stream', 'session', 'stream')
        result.observed_reset_generation = 1
        return result

    def staged(self, capture, *, left=None, ordinary=()):
        first, second = synthetic_pieces()
        tail = first if left is None else second
        capture._seam_provisional = (ordinary, capture._seam_generation, 1, left,
                                    None if left is None else second, tail)
        return ordinary, tail

    def test_unselected_a_promotes_only_true_commit_hook(self):
        capture = self.capture(); ordinary, tail = self.staged(capture)
        self.assertIsNone(capture._seam_tail)
        self.assertEqual(capture.commit_seam(ordinary), ())
        self.assertIs(capture._seam_tail, tail)
        self.assertEqual(capture.last_seam_reason, 'seam_tail_promoted')

    def test_stale_provisional_failed_commit_cleared_before_next_prepare(self):
        capture = self.capture(); self.staged(capture)
        capture.begin_prepare({'session_id': 'session', 'id': 'right', 'timeline_start': 1.},
                              {'stream_id': 'stream', 'start': 0.})
        self.assertIsNone(capture._seam_provisional); self.assertIsNone(capture._seam_tail)
        self.assertEqual(capture.last_seam_reason, 'unconfirmed_primary_commit')

    def test_same_epoch_reset_serial_survives_rolling_witness_reinit(self):
        capture = self.capture(); _, tail = self.staged(capture); capture.commit_seam(())
        capture.active = True; response = {'state': synthetic_state(EPOCH, 0), 'provenance': synthetic_provenance()}
        capture.observe_response(response); capture.observe_response(response)
        self.assertEqual(capture.observed_reset_generation, 3); self.assertEqual(capture.witness.actions, 1)
        self.assertIsNone(capture._seam_tail)

    def test_reset_overflow_and_invalid_reset_disable_composition(self):
        capture = self.capture(); capture.active = True; capture.observed_reset_generation = (1 << 31) - 1
        capture.observe_response({'state': synthetic_state(EPOCH, 0), 'provenance': synthetic_provenance()})
        self.assertTrue(capture._seam_disabled)
        capture = self.capture(); capture.active = True
        capture.observe_response({'state': {}, 'provenance': {}})
        self.assertEqual(capture.observed_reset_generation, 2); self.assertTrue(capture._seam_disabled)

    def test_revocation_and_shutdown_clear_tail_and_provisional(self):
        capture = self.capture(); self.staged(capture); capture.commit_seam(())
        token = capture._seam_generation; capture.invalidate_seam('capture_revoked')
        with self.assertRaises(cells.Unavailable):capture._seam_current(token, 1)
        self.assertIsNone(capture._seam_tail)
        self.staged(capture); self.assertTrue(capture.close())
        self.assertIsNone(capture._seam_provisional)

    def test_three_ordinary_cells_saturate_without_displacement(self):
        capture = self.capture(); left, _ = synthetic_pieces()
        ordinary = tuple(synthetic_cell(i) for i in range(3)); original, tail = self.staged(capture, left=left, ordinary=ordinary)
        with patch.object(seam, 'make_composite', side_effect=AssertionError('must not build fourth cell')):
            self.assertIs(capture.commit_seam(original), original)
        self.assertEqual(capture.last_seam_reason, 'seam_batch_saturated'); self.assertIs(capture._seam_tail, tail)

    def test_owner_generation_change_and_completed_row_failure_decline(self):
        capture = self.capture(); ordinary, _ = self.staged(capture); capture.owner._epoch = object()
        with self.assertRaises(cells.Unavailable):capture.commit_seam(ordinary)
        capture = self.capture(); ordinary, _ = self.staged(capture)
        capture.authority.seam_commit_current = lambda *a: False
        with self.assertRaises(cells.Unavailable):capture.commit_seam(ordinary)
        self.assertIsNone(capture._seam_tail)

    def test_tail_and_current_candidate_payloads_have_explicit_bounds(self):
        left, right = synthetic_pieces(frames=168000)
        self.assertLessEqual(left.material_bytes, seam.TAIL_BYTES); self.assertLessEqual(right.material_bytes, seam.TAIL_BYTES)
        self.assertLessEqual(left.material_bytes + right.material_bytes + len(left.pcm) + len(right.pcm) + 8 * cells.PROOF_BYTES, seam.WORKING_BYTES)
        self.assertEqual(seam.TAIL_BYTES, 768 * 1024); self.assertEqual(seam.WORKING_BYTES, 4 * 1024 * 1024)
