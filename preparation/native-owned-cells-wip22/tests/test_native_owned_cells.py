"""Finite synthetic cell contracts. No model/native import, decode or service.

Producer-shaped evidence comes from the existing pure AST fixture helper, not
from loading native code. A passing fixture is never acoustic qualification.
"""
from dataclasses import asdict, replace
import hashlib
import json
import unittest

from audio_backend import native_owned_cells as mod
from audio_backend.native_lexical_alternatives import SourceSupport
from test_native_owned_support import synthetic_provenance, synthetic_state, synthetic_turns

EPOCH = 'a' * 32


def synthetic_cell(index=0, frames=16, *, sid='session', cid='chunk', receipt_id='b' * 64):
    """Selected immutable manifest fixture, explicitly synthetic producer data."""
    start = index * frames; end = start + frames; pcm = b'\x01\0' * frames
    part = mod.SourceSpan(sid, cid, receipt_id, 'c' * 64, end, start, end, start, end)
    contents = [part.content()]
    native = {'epoch_id': EPOCH, 'epoch_index': 0, 'replay_start': 0,
              'provenance': synthetic_provenance(), 'state': {'synthetic': True},
              'observed_action_count': 4, 'true_eof': True}
    evidence = {'chunk_id': cid, 'start': start, 'end': end,
                'native': {'synthetic': True}, 'core_cut': {'nominal': end / 16000, 'actual': end / 16000, 'reason': 'fixed_or_final'}}
    commit_hash = mod.sha(evidence)
    support = SourceSupport(mod.sha({'schema': 'immutable-source-cell-generation-v1', 'sources': contents}),
        mod.sha({'schema': 'bounded-cell-mapping-v1', 'parts': contents}), mod.sha(native['provenance']),
        mod.sha({'start': start, 'end': end, 'commit': commit_hash, 'native': native}),
        end, start, end, start, end, hashlib.sha256(pcm).hexdigest(), mod.float32_digest(pcm))
    _, turns = mod.selection((mod.Turn(start, end, EPOCH + ':speaker_1'),
                             mod.Turn(start, end, EPOCH + ':speaker_2')), start, end)
    proof = mod.canonical({'policy': mod.POLICY_SHA256, 'support': support.to_dict(),
        'dependencies': [asdict(part)], 'native': native, 'turns': turns, 'selected': True,
        'commit_evidence': evidence, 'commit': {'chunk_id': cid, 'start': start, 'end': end, 'sha256': commit_hash}})
    return mod.Cell(support, (part,), proof, pcm, sid, cid, start, end, commit_hash)


def observed_window(frames=1600, *, core_end=None, scope_start=0., epoch_index=0, finished=True):
    """Observe actual reset/push[/finish]/turns order using small PCM blocks."""
    replay = max(0, epoch_index * 300 * 16000 - 339200)
    witness = mod.RollingWitness(); provenance = synthetic_provenance()
    witness.observe({'state': synthetic_state(EPOCH, 0), 'provenance': provenance})
    pcm = b'\x01\0' * frames
    cursor = 0
    while cursor < frames:
        end = min(frames, cursor + 339200)
        witness.observe_pcm(replay + cursor, replay + end, pcm[cursor * 2:end * 2])
        witness.observe(synthetic_state(EPOCH, end, stable_frames=end))
        cursor = end
    if finished:witness.observe(synthetic_state(EPOCH, frames, finished=True))
    witness.observe({'turns': synthetic_turns(frames)})
    b = frames if core_end is None else core_end
    native = {'end': b / 16000, 'extract_start': 0., 'extract_end': frames / 16000,
              'turns': [dict(t, start=max(replay / 16000, replay / 16000 + t['start']) - replay / 16000,
                             end=min((replay + frames) / 16000, replay / 16000 + t['end']) - replay / 16000)
                        for t in synthetic_turns(frames)], 'provenance': provenance,
              'state': {'epoch_id': EPOCH, 'epoch_index': epoch_index,
                  'owned_start': scope_start + epoch_index * 300,
                  'owned_end': scope_start + (epoch_index + 1) * 300,
                  'replay_start': scope_start + replay / 16000,
                  'input_until': scope_start + replay / 16000 + frames / 16000,
                  'processed_until': scope_start + (replay + frames) / 16000,
                  'stable_until': scope_start + (replay + frames) / 16000,
                  'finished': finished, 'provenance': provenance}}
    return witness, native, pcm, replay


class GeometryTests(unittest.TestCase):
    def test_typed_integer_bounds_and_no_fraction_coercion(self):
        for value in (True, 1.2, -1, 1 << 53, '1'):
            with self.subTest(value=value), self.assertRaises(mod.Unavailable):mod.integer(value)
        for value in (True, .100001, float('inf'), float('nan')):
            with self.subTest(value=value), self.assertRaises(mod.Unavailable):mod.exact_frame(value)

    def test_partition_exact_safe_cut_and_three_cell_ceiling(self):
        self.assertEqual(mod.partition(10, 10 + 960000), ((10, 320010), (320010, 640010), (640010, 960010)))
        self.assertEqual(mod.partition(10, 320011), ((10, 320010), (320010, 320011)))
        for end in (0, 960001):
            with self.assertRaises(mod.Unavailable):mod.partition(0, end)

    def test_overlap_requires_positive_distinct_labels(self):
        a = mod.Turn(0, 20, 'a'); touching = mod.Turn(20, 30, 'b')
        self.assertFalse(mod.selection((a, touching), 0, 30)[0])
        self.assertFalse(mod.selection((a, a), 0, 30)[0])
        chosen, proof = mod.selection((a, mod.Turn(10, 30, 'b')), 15, 25)
        self.assertTrue(chosen)
        self.assertEqual(proof[0], {'label': 'a', 'original': [0, 20], 'intersection': [15, 20]})
        self.assertEqual(len(proof), 2)

    def test_source_mapping_gaps_overlap_and_part_ceiling(self):
        a = mod.SourceSpan('s', 'a', 'a' * 64, 'b' * 64, 20, 0, 10, 0, 10)
        b = mod.SourceSpan('s', 'b', 'c' * 64, 'd' * 64, 20, 0, 10, 10, 20)
        self.assertEqual(mod.validate_mapping((a, b), 0, 20), (a, b))
        for parts in ((b, a), (a, a), (a,) * 129):
            with self.assertRaises(mod.Unavailable):mod.validate_mapping(parts, 0, 20)
        self.assertEqual(mod.intersect_mapping((a, b), 5, 15)[1].source_end, 5)

    def test_source_generation_and_mapping_are_append_stable(self):
        cell = synthetic_cell(50)
        self.assertEqual(cell.cell_id, synthetic_cell(50).cell_id)
        self.assertNotEqual(cell.cell_id, synthetic_cell(51).cell_id)
        content = [p.content() for p in cell.parts]
        self.assertNotIn('session_id', content[0]); self.assertNotIn('chunk_id', content[0])
        changed = replace(cell.parts[0], session_id='alias', chunk_id='alias')
        self.assertEqual(changed.content(), cell.parts[0].content())

    def test_float_pcm_and_selection_tampering_fail_admission(self):
        cell = synthetic_cell(); support = replace(cell.support, float32le_sha256='f' * 64)
        doc = json.loads(cell.proof); doc['support'] = support.to_dict()
        with self.assertRaises(mod.Unavailable):replace(cell, support=support, proof=mod.canonical(doc))
        with self.assertRaises(mod.Unavailable):replace(cell, pcm=b'\0\0' * 16)
        doc = json.loads(cell.proof); doc['turns'][1]['label'] = doc['turns'][0]['label']
        with self.assertRaises(mod.Unavailable):replace(cell, proof=mod.canonical(doc))

    def test_combined_proof_dependencies_and_immutable_receipt_binding(self):
        cell = synthetic_cell(); self.assertLessEqual(len(cell.proof), mod.PROOF_BYTES)
        doc = json.loads(cell.proof); doc['dependencies'][0]['receipt_sha256'] = 'f' * 64
        with self.assertRaises(mod.Unavailable):replace(cell, proof=mod.canonical(doc))
        with self.assertRaises(mod.Unavailable):mod.canonical({'x': 'x' * 65536})

    def test_support_interior_and_large_48_hour_coordinates(self):
        # Stream finite tiny fixture blocks at sparse 48-hour coordinates; the
        # recording waveform is never constructed or retained.
        for coordinate in range(0, 48 * 3600 * 16000 + 1, 1800 * 16000):
            cell = synthetic_cell(coordinate // 16)
            self.assertEqual(cell.support.start_frame, coordinate)
            self.assertEqual(len(cell.pcm), 32)
            self.assertLessEqual(len(cell.proof), mod.PROOF_BYTES)


class RollingWitnessTests(unittest.TestCase):
    def test_observed_sequence_and_true_eof(self):
        witness, native, pcm, _ = observed_window()
        turns, proof = witness.prepared(native, 0., 0)
        self.assertTrue(proof['true_eof']); self.assertEqual(len(turns), 2)
        self.assertEqual(witness.read(0, 1600), pcm)

    def test_successive_21_2_second_pushes_keep_96_second_ring(self):
        witness = mod.RollingWitness()
        witness.observe({'state': synthetic_state(EPOCH, 0), 'provenance': synthetic_provenance()})
        block = b'\1\0' * 339200
        for i in range(12):
            witness.observe_pcm(i * 339200, (i + 1) * 339200, block)
            witness.observe(synthetic_state(EPOCH, (i + 1) * 339200, stable_frames=(i + 1) * 339200))
            self.assertLessEqual(len(witness.ring), mod.RING_PCM_BYTES)
        self.assertIsNone(witness.failure); self.assertFalse(witness.finished)
        with self.assertRaises(mod.Unavailable):witness.read(0, 16)

    def test_epoch_boundary_reset_does_not_fabricate_eof(self):
        witness, native, _, replay = observed_window(epoch_index=1, finished=False)
        _, proof = witness.prepared(native, 0., replay)
        self.assertFalse(proof['true_eof']); self.assertEqual(proof['epoch_index'], 1)
        witness.observe({'state': synthetic_state('b' * 32, 0), 'provenance': synthetic_provenance()})
        self.assertEqual(len(witness.ring), 0); self.assertFalse(witness.finished)

    def test_nonzero_scope_relative_origins(self):
        witness, native, _, replay = observed_window(scope_start=.00001)
        self.assertEqual(mod.exact_frame(.00001 + 1600 / 16000, origin=.00001), 1600)
        self.assertEqual(witness.prepared(native, .00001, replay)[1]['epoch_index'], 0)

    def test_native_eof_deficit_is_not_padded(self):
        witness, _, _, _ = observed_window(finished=False)
        witness.observe(synthetic_state(EPOCH, 1600, finished=True, processed_frames=1520, stable_frames=1600))
        self.assertEqual(witness.failure, 'native_eof_deficit_or_frontier_order')
        self.assertEqual(witness.ring, bytearray())

    def test_unobserved_reset_and_discontinuous_pcm_fail_closed(self):
        witness = mod.RollingWitness(); witness.observe(synthetic_state(EPOCH, 100))
        self.assertEqual(witness.failure, 'unobserved_native_reset')
        witness, _, _, _ = observed_window(finished=False)
        witness.observe_pcm(2000, 2016, b'\0\0' * 16)
        self.assertEqual(witness.failure, 'native_pcm_discontinuity')

    def test_native_prepared_turns_provenance_and_frontier_mutation(self):
        for field in ('turns', 'provenance', 'state'):
            witness, native, _, _ = observed_window()
            if field == 'turns':native['turns'][0]['end'] -= .01
            elif field == 'provenance':native['provenance'] = {}
            else:native['state']['processed_until'] += .01
            with self.subTest(field=field), self.assertRaises(mod.Unavailable):witness.prepared(native, 0., 0)

    def test_cell_builder_60_second_context_and_partial_safe_cut(self):
        witness, native, pcm, _ = observed_window(960000, core_end=880000, finished=False)
        part = mod.SourceSpan('session', 'chunk', 'b' * 64, 'c' * 64, 960000, 0, 880000, 0, 880000)
        evidence = {'chunk_id': 'chunk', 'start': 0, 'end': 880000,
                    'native': native['state'], 'core_cut': {'nominal': 60., 'actual': 55., 'reason': 'neural_speech_gap'}}
        cells = mod.make_cells(witness=witness, native=native, scope_start=0., origin_frame=0,
            core_start=0, core_end=880000, analyzer_start=0, analyzer_pcm=pcm, mapping=(part,),
            chunk_id='chunk', commit_start=0, commit_end=880000, commit_sha256=mod.sha(evidence), commit_evidence=evidence)
        self.assertEqual([len(c.pcm) // 2 for c in cells], [320000, 320000, 240000])
        self.assertTrue(all(not json.loads(c.proof)['native']['true_eof'] for c in cells))
        with self.assertRaises(mod.Unavailable):
            mod.make_cells(witness=witness, native=native, scope_start=0., origin_frame=0,
                core_start=0, core_end=880000, analyzer_start=0, analyzer_pcm=pcm + b'\0\0', mapping=(part,),
                chunk_id='chunk', commit_start=0, commit_end=880000, commit_sha256=mod.sha(evidence), commit_evidence=evidence)

    def test_turn_and_material_overflow_discards_optional_evidence(self):
        witness, _, _, _ = observed_window(finished=False)
        witness.observe({'turns': synthetic_turns(1600) * 129})
        self.assertEqual(witness.failure, 'native_turn_capacity_or_sequence')
        self.assertEqual(len(witness.ring), 0)
