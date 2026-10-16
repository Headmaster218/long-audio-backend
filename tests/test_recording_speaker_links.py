"""Model-free synthetic contracts; no model/runtime import or acoustic claim.

Execution is separately approved. All waveform samples/vectors are synthetic.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math
import struct

import pytest

from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_speaker_links as links
from audio_backend.native_lexical_alternatives import SourceSupport
from audio_backend.moss_handy_adapter import project
from audio_backend.moss_handy_profile import NAME
from test_native_owned_support import synthetic_provenance

EPOCH = 'a' * 32
RATE = 16000


def ordinary(turns=None, *, seed=1, frames=160000, start=0, epoch=EPOCH, varying=False):
    pcm = struct.pack('<h', seed) * frames
    if varying:
        pcm = struct.pack('<h', seed) * (frames // 2) + struct.pack('<h', seed + 1) * (frames - frames // 2)
    end = start + frames
    part = cells.SourceSpan('session', 'chunk-' + str(seed), 'b' * 64,
        hashlib.sha256(pcm).hexdigest(), end, start, end, start, end)
    native = {'epoch_id': epoch, 'epoch_index': 0, 'replay_start': 0,
        'provenance': synthetic_provenance(), 'state': {'synthetic': True},
        'observed_action_count': 4, 'true_eof': True}
    evidence = {'chunk_id': part.chunk_id, 'start': start, 'end': end,
        'native': {'synthetic': True}, 'core_cut': {'nominal': end / RATE,
            'actual': end / RATE, 'reason': 'fixed_or_final'}}
    content = [part.content()]
    support = SourceSupport(cells.sha({'schema': 'immutable-source-cell-generation-v1', 'sources': content}),
        cells.sha({'schema': 'bounded-cell-mapping-v1', 'parts': content}), cells.sha(native['provenance']),
        cells.sha({'start': start, 'end': end, 'commit': cells.sha(evidence), 'native': native}),
        end, start, end, start, end, hashlib.sha256(pcm).hexdigest(), cells.float32_digest(pcm))
    turns = turns or [(start, end, 1), (start, start + 8000, 2)]
    selected, observed = cells.selection(tuple(cells.Turn(a, b, epoch + ':speaker_' + str(label))
        for a, b, label in turns), start, end)
    assert selected
    proof = cells.canonical({'policy': cells.POLICY_SHA256, 'support': support.to_dict(),
        'dependencies': [asdict(part)], 'native': native, 'turns': observed, 'selected': True,
        'commit_evidence': evidence, 'commit': {'chunk_id': part.chunk_id, 'start': start,
            'end': end, 'sha256': cells.sha(evidence)}})
    return support, pcm, proof


def seam(*, reset=False):
    epoch = 'd' * 32 if reset else EPOCH
    index = 1 if reset else 0
    a, middle, b = (4640000, 4800000, 4960000) if reset else (0, 160000, 320000)
    replay = max(0, index * 300 * RATE - 339200)
    provenance = synthetic_provenance()
    raw = (b - replay) / RATE
    state = {'epoch_id': epoch, 'finished': True,
        'fingerprint': provenance['diarization_fingerprint'], 'local_capacity': 8}
    for key in ('input', 'processed', 'stable'):
        state[key] = {'raw': raw, 'hex': raw.hex(), 'frame': b - replay}
    native = {'epoch_id': epoch, 'epoch_index': index, 'owned_start': index * 300.,
        'owned_end': (index + 1) * 300., 'replay_start': replay / RATE,
        'input_until': replay / RATE + raw, 'processed_until': b / RATE,
        'stable_until': b / RATE, 'finished': True, 'provenance': provenance}
    commits = []
    for i, (lo, hi) in enumerate(((a, middle), (middle, b))):
        p = cells.SourceSpan('session', 'piece-' + str(i), ('b' if i == 0 else 'c') * 64,
            ('e' if i == 0 else 'f') * 64, hi - lo, 0, hi - lo, lo, hi)
        scope = {'session_id': 'session', 'kind': 'stream', 'key': 'stream', 'origin': '0x0.0p+0',
            'run': '9' * 32, 'reset': i + 1 if reset else 1,
            'epoch': EPOCH if reset and i == 0 else epoch,
            'index': i if reset else 0}
        evidence = {'chunk_id': p.chunk_id, 'start': 0, 'end': hi - lo, 'native': native,
            'core_cut': {'nominal': 10., 'actual': 10., 'reason': 'fixed_or_final'}}
        commits.append({'scope': scope, 'part': asdict(p), 'native_sha256': cells.sha(native),
            'cut_sha256': cells.sha(evidence['core_cut']), 'evidence_sha256': cells.sha(evidence),
            'runtime_sha256': cells.sha(provenance)})
    pcm = b'\x03\0' * (b - a)
    query = {'query_start_frame': a, 'query_end_frame': b,
        'query_pcm16le_sha256': hashlib.sha256(pcm).hexdigest(),
        'support_pcm16le_sha256': hashlib.sha256(pcm).hexdigest(),
        'native': {'epoch_id': epoch, 'epoch_index': index, 'replay_start': replay,
            'state': state, 'provenance': provenance, 'observed_action_count': 7, 'true_eof': True},
        'turns': [{'label': epoch + ':speaker_1', 'original': [a, b]},
                  {'label': epoch + ':speaker_2', 'original': [a, a + 8000]}]}
    cell = core.make_cell(commits, query, evidence, pcm, core.RESET_SCHEMA if reset else core.SCHEMA)
    return cell.support, cell.pcm, cell.proof


def projection(support, phrases=None):
    return project((phrases or '[1] [S01] Synthetic phrase. [9]').encode(),
                   support=support, profile_name=NAME)


def capture(source=None, phrases=None, job='job', result='1' * 64):
    support, pcm, proof = source or ordinary()
    return links.capture_candidates(support, pcm, proof, projection(support, phrases),
                                    job, result, links.expected_space())


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def vector(*components):
    length = math.sqrt(math.fsum(v * v for v in components))
    return [f32(v / length) for v in components] + [0.] * (512 - len(components))


def embedded(seed, job, values=(1., 0.), source=None):
    batch, _ = capture(source or ordinary(seed=seed), job=job)
    for node in batch['nodes']:
        node['vector'] = vector(*values)
    return batch


def matrix(n, values=None, default=.0):
    out = {(a, b): default for a in range(n) for b in range(a + 1, n)}
    out.update(values or {})
    return out


def test_exact_contiguous_center_crop_and_provenance():
    support, pcm, proof = ordinary(start=320000)
    batch, crops = capture((support, pcm, proof))
    assert len(crops) == len(batch['nodes']) == 1
    node = batch['nodes'][0]
    assert (node['start_frame'], node['end_frame']) == (384000, 416000)
    assert crops[0] == pcm[(384000 - 320000) * 2:(416000 - 320000) * 2]
    assert len(crops[0]) == 64000
    assert node['proof_sha256'] == hashlib.sha256(proof).hexdigest()
    assert node['source_spans'][0]['source_start'] == 384000
    assert batch['witnesses'][0]['overlap_frames'] == [320000, 328000]
    assert links.validate_batch(batch, require_vectors=False) is batch


@pytest.mark.parametrize('reset', [False, True])
def test_seam_and_reset_use_query_coordinate_scope_once(reset):
    source = seam(reset=reset)
    batch, crops = capture(source, '[3] [S01] Synthetic phrase. [7]')
    base = source[0].start_frame
    assert batch['nodes'][0]['start_frame'] == base + 64000
    assert batch['nodes'][0]['end_frame'] == base + 96000
    expected = 'd' * 32 if reset else EPOCH
    assert batch['nodes'][0]['native_label'] == expected + ':speaker_1'
    assert len(crops[0]) == 64000
    assert batch['witnesses'][0]['overlap_frames'] == [base, base + 8000]


def test_ami_clean_component_center_is_220656_without_padding():
    turns = [(215856, 288784, 2), (257456, 266544, 1),
             (168000, 199664, 2), (168000, 175984, 1)]
    source = ordinary(turns, frames=168000, start=168000)
    batch, crops = capture(source, '[2.96] [S01] Synthetic phrase. [7.44]')
    assert [(n['start_frame'], n['end_frame']) for n in batch['nodes']] == [(220656, 252656)]
    assert len(crops) == 1
    # Overlap evidence remains even though it is outside the selected crop.
    assert batch['witnesses'][0]['labels'] == [EPOCH + ':speaker_1', EPOCH + ':speaker_2']


def test_ambiguous_labels_and_short_support_abstain():
    turns = [(0, 80000, 1), (70000, 160000, 2)]
    batch, crops = capture(ordinary(turns), '[0] [S01] Ambiguous. [10]')
    assert not crops
    assert batch['outcomes'][0]['status'] == 'ambiguous_native_label'
    batch, crops = capture(phrases='[1] [S01] Too short. [3.19999]')
    assert not crops
    assert batch['outcomes'][0]['status'] == 'insufficient_clean_support'


def test_round_inward_other_tags_outward_and_erode_exactly():
    batch, _ = capture(phrases='[1.00001] [S01] A. [4.00001] [3.5] [S02] B. [4.1]')
    node = batch['nodes'][0]
    # Inward phrase left 16001, competing tag begins floor(3.5*16000).
    left, right = 16001 + 1600, 56000 - 1600
    assert node['start_frame'] == left + (right - left - 32000) // 2
    assert node['end_frame'] <= right
    assert batch['outcomes'][1]['status'] == 'insufficient_clean_support'


def test_no_concat_or_floor_relaxation_and_crop_capacity():
    batch, crops = capture(phrases='[1] [S01] A. [3.19999] [3.2] [S01] B. [5.39999]')
    assert not crops
    phrases = ' '.join(f'[{1 + i * .05}] [S01] A. [{4 + i * .05}]' for i in range(6))
    batch, crops = capture(phrases=phrases)
    assert len(crops) == 4
    assert [x['status'] for x in batch['outcomes']] == ['candidate'] * 4 + ['crop_capacity'] * 2


def test_hash_support_projection_and_malformed_proof_fail_closed():
    support, pcm, proof = ordinary()
    kwargs = dict(support=support, pcm=pcm, proof=proof, projection=projection(support),
                  job_id='job', result_sha256='1' * 64, space=links.expected_space())
    for key, changed in [('pcm', b'\0' * len(pcm)), ('proof', proof + b' '),
                         ('result_sha256', 'not-a-hash')]:
        with pytest.raises(ValueError):
            links.capture_candidates(**dict(kwargs, **{key: changed}))
    for key, changed in [('support_id', 'f' * 64), ('truncated', True), ('schema_errors', ['unparsed_text'])]:
        with pytest.raises(ValueError):
            links.capture_candidates(**dict(kwargs, projection=dict(kwargs['projection'], **{key: changed})))
    descriptor = deepcopy(kwargs['space']); descriptor['frontend']['sampling_rate'] = 8000
    with pytest.raises(ValueError):
        links.capture_candidates(**dict(kwargs, space=descriptor))


def test_retained_batch_node_and_witness_mutations_detected():
    batch = embedded(1, 'a')
    for mutate in ('witness', 'node', 'outcome', 'proof'):
        changed = deepcopy(batch)
        if mutate == 'witness':
            changed['witnesses'] = []
        elif mutate == 'node':
            changed['nodes'][0]['start_frame'] += 1
        elif mutate == 'outcome':
            changed['outcomes'][0]['phrase_index'] = 2
        else:
            changed['proof']['support']['pcm16le_sha256'] = 'a' * 64
        with pytest.raises(ValueError):
            links.validate_batch(changed)


def test_exact_original_float32_only_and_norm_dimension():
    assert links.valid_vector(vector(1., 2.))
    for value in ([1.] * 512, vector(1.)[:-1], [float('nan')] + [0.] * 511,
                  [.1] + [0.] * 511, [True] + [0.] * 511, [1e300] + [0.] * 511):
        with pytest.raises(ValueError):
            links.valid_vector(value)


def test_two_node_missing_runner_up_cn17_and_ties_abstain():
    groups, traces = links.cluster_scores({(0, 1): .99}, 2)
    assert groups == [(0,), (1,)]
    assert traces[0]['reasons']['missing_runner_up'] == 2
    assert links.cluster_scores(matrix(3, {(0, 1): .9, (0, 2): .9, (1, 2): .9}), 3)[0] == [(0,), (1,), (2,)]


def test_below_threshold_competitor_counts_and_both_margins_required():
    assert links.cluster_scores(matrix(3, {(0, 1): .6, (0, 2): .54, (1, 2): .1}), 3)[0] == [(0,), (1,), (2,)]
    groups, _ = links.cluster_scores(matrix(3, {(0, 1): .7, (0, 2): .1, (1, 2): .64}), 3)
    assert groups == [(0,), (1,), (2,)]
    groups, _ = links.cluster_scores(matrix(3, {(0, 1): .7, (0, 2): .1, (1, 2): .2}), 3)
    assert (0, 1) in groups


def test_cannot_link_winner_veto_never_promotes_second():
    scores = matrix(4, {(0, 1): .95, (0, 2): .8, (0, 3): .1,
                        (1, 2): .2, (1, 3): .1, (2, 3): .1})
    groups, traces = links.cluster_scores(scores, 4, {(0, 1)})
    assert groups == [(0,), (1,), (2,), (3,)]
    assert traces[0]['reasons']['cannot_link_winner'] == 2


def test_complete_link_blocks_unsafe_chain_and_merges_synchronously():
    scores = matrix(5, {(0, 1): .9, (1, 2): .75, (0, 2): .4,
                        (3, 4): .9, (2, 3): .3, (2, 4): .3})
    groups, traces = links.cluster_scores(scores, 5)
    assert groups == [(0, 1), (2,), (3, 4)]
    assert traces[0]['merges'] == [[[0], [1]], [[3], [4]]]
    assert all(scores[(a, b)] >= links.TAU for group in groups for a in group for b in group if a < b)


def test_batch_order_invariance_and_no_moss_tag_global_identity():
    batches = [embedded(1, 'a', (1., 0.)), embedded(2, 'b', (1., .1)), embedded(3, 'c', (0., 1.))]
    result = links.reconcile_batches(batches)
    assert result == links.reconcile_batches(list(reversed(batches)))
    linked = [a for a in result['annotations'] if a['status'] == 'provisional_linked']
    assert len(linked) == 2
    assert len({a['provisional_speaker_id'] for a in linked}) == 1
    assert result['diagnostics']['pair_count'] == 3
    assert all(a['moss_tag'] == 'S01' for a in result['annotations'])
    assert result['annotations'][2]['status'] == 'source_scoped'


def test_exact_crop_duplicate_is_one_independent_observation():
    same = ordinary(seed=7)
    a = embedded(7, 'ordinary', source=same)
    b = embedded(7, 'seam-alias', source=same)
    c = embedded(8, 'other', (1., .1))
    result = links.reconcile_batches([a, b, c])
    assert result['diagnostics']['node_count'] == 3
    assert result['diagnostics']['independent_node_count'] == 2
    assert result['diagnostics']['pair_count'] == 1
    assert all(a['status'] == 'source_scoped' for a in result['annotations'])
    b['nodes'][0]['vector'] = vector(1., .01)
    with pytest.raises(ValueError, match='duplicate_vector_disagreement'):
        links.reconcile_batches([a, b, c])


def test_hard_native_overlap_survives_disjoint_selected_crops():
    source = ordinary([(0, 88000, 1), (72000, 160000, 2)], seed=8)
    batch, _ = capture(source, '[1] [S01] Left. [4] [6] [S02] Right. [9]')
    assert len(batch['nodes']) == 2
    for node in batch['nodes']:
        node['vector'] = vector(1., 0.)
    # Constant synthetic waveform makes these exact PCM duplicates, and their
    # native cannot-link is contradictory: fail closed rather than erase it.
    with pytest.raises(ValueError, match='duplicate_native_conflict'):
        links.reconcile_batches([batch])


def test_capacity_and_matrix_validation_fail_closed():
    with pytest.raises(ValueError):
        links.cluster_scores({}, 33)
    with pytest.raises(ValueError):
        links.cluster_scores({}, 2)
    with pytest.raises(ValueError):
        links.cluster_scores({(0, 1): float('nan')}, 2)
    with pytest.raises(ValueError):
        links.reconcile_batches([{}] * 33)


def test_nonduplicate_native_overlap_is_hard_cannot_link_with_runner_up():
    source = ordinary([(0, 88000, 1), (72000, 160000, 2)], seed=8, varying=True)
    batch, _ = capture(source, '[1] [S01] Left. [4] [6] [S02] Right. [9]')
    for node in batch['nodes']:
        node['vector'] = vector(1., 0.)
    competitor = embedded(10, 'other', (0., 1.), source=ordinary(seed=10, epoch='c' * 32))
    result = links.reconcile_batches([batch, competitor])
    assert result['diagnostics']['cannot_link_count'] == 1
    assert result['diagnostics']['independent_node_count'] == 3
    assert all(a['status'] == 'source_scoped' for a in result['annotations'])


def test_unavailable_and_interrupted_probe_nodes_have_bounded_annotations():
    batch, _ = capture()
    result = links.reconcile_batches([batch])
    assert result['annotations'][0]['status'] == 'probe_unavailable'
    assert result['state'] == 'probe_unavailable'
    assert result['diagnostics']['available_node_count'] == 0
    result = links.reconcile_batches([batch], terminal_states={batch['batch_sha256']: 'probe_interrupted'})
    assert result['annotations'][0]['status'] == 'probe_interrupted'
    assert result['state'] == 'probe_interrupted'
    assert 'source_spans' not in result['annotations'][0]
    assert len(result['jobs']) == 1


def test_non_candidate_phrase_outcomes_stay_in_batch_not_decision():
    phrases = ' '.join(f'[1] [S01] Short {i}. [1.1]' for i in range(256))
    batch, _ = capture(phrases=phrases)
    result = links.reconcile_batches([batch])
    assert len(batch['outcomes']) == 256
    assert not result['annotations']
    assert result['state'] == 'insufficient_clean_support'
    assert result['jobs'][0]['outcome_counts'] == {'insufficient_clean_support': 256}


def test_crop_capacity_has_explicit_fallback_status():
    phrases = ' '.join(f'[{1 + i * .05}] [S01] A. [{4 + i * .05}]' for i in range(6))
    batch, _ = capture(phrases=phrases)
    for node in batch['nodes']:
        node['vector'] = vector(1.)
    result = links.reconcile_batches([batch])
    assert len(result['annotations']) == 4
    assert result['jobs'][0]['default_status'] == 'capacity_exhausted'
    assert result['jobs'][0]['outcome_counts']['crop_capacity'] == 2


def test_ambiguous_phrase_is_explicitly_insufficient_without_claiming_identity():
    batch, _ = capture(ordinary([(0, 80000, 1), (70000, 160000, 2)]), '[0] [S01] A. [10]')
    result = links.reconcile_batches([batch])
    assert result['state'] == 'insufficient_clean_support'
    assert result['jobs'][0]['outcome_counts'] == {'ambiguous_native_label': 1}
    assert not result['annotations']


def test_mixed_blocker_container_order_and_equal_start_are_numeric():
    blockers = [[2, 4], (2, 5), [7, 8]]
    assert links._subtract((0, 10), blockers) == [(0, 2), (5, 7), (8, 10)]
    assert links._subtract((0, 10), list(reversed(blockers))) == [(0, 2), (5, 7), (8, 10)]
