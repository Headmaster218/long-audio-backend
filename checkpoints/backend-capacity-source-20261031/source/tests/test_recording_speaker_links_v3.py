"""Finite model-free v3 allocator contracts; no acoustic/model qualification."""
from copy import deepcopy
from dataclasses import asdict, replace
from itertools import permutations, product
import hashlib

import pytest

from audio_backend import recording_speaker_links as links
from audio_backend import native_owned_cells as cells
from test_recording_speaker_links import ordinary, seam, projection, vector, matrix
from test_recording_speaker_links_v2 import capture_v2
from test_recording_primary_probe import synthetic_primary_case


def capture_v3(source=None, phrases=None, packets=()):
    support, pcm, proof = source or ordinary()
    return links.capture_candidates_v3(support, pcm, proof, projection(support, phrases),
        'typed-job', '1' * 64, links.expected_space(), primary_packets=packets)


def rehash(batch):
    for node in batch['nodes']:
        original = {k: v for k, v in node.items() if k not in ('node_id', 'vector')}
        node['node_id'] = cells.sha({'schema': batch['schema'], 'policy_sha256': batch['policy_sha256'], **original})
    for outcome in batch['outcomes']:
        if outcome['status'] == 'candidate':
            key = 'primary_source_ref' if 'primary_source_ref' in outcome else 'phrase_index'
            outcome['node_id'] = next(n['node_id'] for n in batch['nodes'] if n.get(key) == outcome[key])
    batch['batch_sha256'] = links._batch_digest(batch)
    return batch


def numeric_nodes(starts, *, logical_base=0, source_frames=200000):
    """Pure metadata fixture, never a substitute for a validated proof/lease."""
    part = cells.SourceSpan('session', 'chunk', 'b' * 64, 'c' * 64,
        source_frames, 0, source_frames, logical_base, logical_base + source_frames)
    nodes = []
    for index, start in enumerate(starts):
        span = asdict(part)
        span.update(logical_start=start, logical_end=start + links.CROP_FRAMES,
            source_start=start - logical_base, source_end=start - logical_base + links.CROP_FRAMES)
        content = [{k: span[k] for k in ('source_sha256', 'source_frames', 'source_start', 'source_end')}]
        nodes.append({'source_kind': 'moss', 'phrase_index': index, 'start_frame': start,
            'end_frame': start + links.CROP_FRAMES, 'source_spans': [span],
            'source_key': cells.sha({'schema': 'exact-source-crop-v1', 'parts': content}),
            'native_attributions': [{'native_scope_id': 'd' * 64, 'native_label': 'speaker'}]})
    return nodes, [part]


def hashes(*values):
    return [hashlib.sha256(str(value).encode()).hexdigest() for value in values]


def test_saved_numeric_witness_keeps_primary_aliases_and_two_support_abstention():
    nodes, parts = numeric_nodes([1589360, 1575672, 1589360], logical_base=1512000, source_frames=168000)
    components = [(1557904, 1652816), (1557904, 1625440), (1557904, 1652816)]
    output, header = links._allocate_candidates(nodes, components, parts, [], hashes(1, 2, 1))
    assert header['status'] == 'joint_disjoint'
    assert [(n['start_frame'], n['end_frame']) for n in output] == [
        (1557904, 1589904), (1589904, 1621904), (1557904, 1589904)]
    assert nodes[0]['start_frame'] == 1589360 and nodes[1]['start_frame'] == 1575672
    assert output[0]['source_key'] == output[2]['source_key']
    assert output[0]['source_spans'][0]['source_start'] == 45904
    assert all(a['native_attributions'] == b['native_attributions'] for a, b in zip(nodes, output))
    clusters, traces = links.cluster_scores(matrix(2, {(0, 1): .99}), 2, source_overlap=set())
    assert clusters == [(0,), (1,)]
    assert traces[0]['reasons']['missing_runner_up'] == 2


def test_joint_capture_roundtrip_and_baseline_capture_unchanged():
    source = ordinary(varying=True)
    phrases = '[1] [S01] First. [6] [2] [S01] Second. [7]'
    old, _ = capture_v2(source, phrases)
    new, crops = capture_v3(source, phrases)
    assert [n['start_frame'] for n in old['nodes']] == [40000, 56000]
    assert [n['start_frame'] for n in new['nodes']] == [17600, 49600]
    assert new['allocation']['status'] == 'joint_disjoint'
    assert new['allocation']['baseline_pcm16le_sha256'] == [n['pcm16le_sha256'] for n in old['nodes']]
    assert new['witnesses'] == old['witnesses']
    assert [n['native_attributions'] for n in new['nodes']] == [n['native_attributions'] for n in old['nodes']]
    assert links.validate_batch(new, require_vectors=False) is new
    assert len(crops) == 2 and sum(map(len, crops)) == 128000
    for node in new['nodes']:node['vector'] = vector(1.)
    answer = links.reconcile_batches([new])
    assert answer['diagnostics']['schema'] == links.SCHEMA_V3
    assert answer['diagnostics']['independent_node_count'] == 2
    assert answer['diagnostics']['source_overlap_pair_count'] == 0
    assert all(a['status'] == 'source_scoped' and not a['speaker_identity_valid'] for a in answer['annotations'])


@pytest.mark.parametrize('starts', [[16000, 24000, 16000], [0, 64000, 0]])
def test_pcm_only_transitive_alias_falls_back_before_disjoint_return(starts):
    nodes, parts = numeric_nodes(starts)
    output, header = links._allocate_candidates(nodes, [(0, 150000)] * 3, parts, [], hashes(1, 1, 1))
    assert output == nodes
    assert header['status'] == 'baseline_fallback'
    assert header['reason'] == 'cross_coordinate_pcm_alias'


def test_all_baseline_pairs_checked_even_after_pcm_only_pair():
    nodes, parts = numeric_nodes([0, 64000, 64000])
    with pytest.raises(ValueError, match='duplicate_pcm_disagreement'):
        links._allocate_candidates(nodes, [(0, 150000)] * 3, parts, [], hashes(1, 1, 2))


def test_capture_constant_pcm_keeps_all_four_admissions_and_no_fifth_after_aliasing():
    phrases = ' '.join('[1] [S01] A. [6] [2] [S01] B. [7]' for _ in range(3))
    new, crops = capture_v3(phrases=phrases)
    old, old_crops = capture_v2(phrases=phrases)
    assert len(new['nodes']) == 4 and len(crops) == 1 and crops == old_crops
    assert [n['start_frame'] for n in new['nodes']] == [n['start_frame'] for n in old['nodes']]
    assert [n['probe_index'] for n in new['nodes']] == [0] * 4
    assert [o['status'] for o in new['outcomes']] == ['candidate'] * 4 + ['crop_capacity'] * 2
    assert new['allocation']['reason'] == 'cross_coordinate_pcm_alias'


def test_selected_pcm_duplicates_collapse_without_reallocation(monkeypatch):
    # Both baselines straddle the source's one change point with different
    # proportions. Earliest disjoint selection places both before that point.
    source = ordinary(varying=True)
    phrases = '[0] [S01] A. [9.75] [0.75] [S01] B. [10]'
    calls = []
    original = links._joint_layout
    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(links, '_joint_layout', counted)
    batch, crops = capture_v3(source, phrases)
    assert batch['allocation']['status'] == 'joint_disjoint'
    assert batch['allocation']['baseline_pcm16le_sha256'][0] != batch['allocation']['baseline_pcm16le_sha256'][1]
    assert len(crops) == 1 and [n['probe_index'] for n in batch['nodes']] == [0, 0]
    # Capture and validation each replay the kernel once; duplicates never retry it.
    assert len(calls) == 2


@pytest.mark.parametrize('kind', ['mixed_source', 'source_complex', 'frame_identity'])
def test_disjoint_mixed_or_composite_geometry_retains_centered_baseline(kind):
    nodes, parts = numeric_nodes([0, 64000])
    if kind == 'source_complex':
        nodes[1]['source_spans'].append(deepcopy(nodes[1]['source_spans'][0]))
        reason = 'source_complex'
    else:
        second = replace(parts[0], chunk_id='other', source_sha256='e' * 64) if kind == 'mixed_source' else replace(
            parts[0], chunk_id='other', source_frames=200001)
        parts.append(second)
        nodes[1]['source_spans'][0].update(chunk_id='other', source_sha256=second.source_sha256,
                                           source_frames=second.source_frames)
        reason = 'mixed_source'
    output, header = links._allocate_candidates(nodes, [(0, 150000)] * 2, parts, [], hashes(1, 2))
    assert output == nodes and header['reason'] == reason


def test_alias_domain_intersection_unplaceable_full_batch_retains_competitor_and_veto():
    nodes, parts = numeric_nodes([16000, 16000, 24000])
    components = [(0, 96000), (16000, 64000), (16000, 64000)]
    output, header = links._allocate_candidates(nodes, components, parts, [], hashes(1, 1, 2))
    assert output == nodes and header['reason'] == 'no_full_disjoint_layout'
    scores = matrix(3, {(0, 1): .99, (0, 2): .1, (1, 2): .1})
    clusters, traces = links.cluster_scores(scores, 3, source_overlap={(0, 1)})
    assert clusters == [(0,), (1,), (2,)]
    assert traces[0]['reasons']['source_overlap_dependency']


def test_primary_movement_regenerates_turn_ordinals_without_authority_changes():
    nodes, parts = numeric_nodes([40000, 50000])
    nodes[0].update(source_kind='native_primary', primary_packet_sha256='a' * 64,
        primary_source_ref='p.1.' + 'b' * 64, native_label='speaker', original_turn_ordinals=[2],
        original_parent_ordinal=5, original_unit_ordinals=[[5, 7]])
    packets = [{'header': {'digest': 'a' * 64}, 'packet': {'labels': ['speaker'],
        'turns': [[1, 0, 0, 32000], [2, 0, 32000, 150000]]}}]
    output, header = links._allocate_candidates(nodes, [(0, 150000)] * 2, parts, packets, hashes(1, 2))
    assert header['status'] == 'joint_disjoint' and output[0]['start_frame'] == 0
    assert output[0]['original_turn_ordinals'] == [1]
    for field in ('primary_source_ref', 'primary_packet_sha256', 'original_parent_ordinal', 'original_unit_ordinals'):
        assert output[0][field] == nodes[0][field]
    assert output[0]['source_key'] != nodes[0]['source_key']


@pytest.mark.parametrize('mutation', ['start', 'hash_count', 'hash_type', 'reason', 'status', 'extra'])
def test_rehashed_allocation_forgery_rejected(mutation):
    batch, _ = capture_v3(ordinary(varying=True), '[1] [S01] A. [6] [2] [S01] B. [7]')
    if mutation == 'start':batch['nodes'][0]['start_frame'] += 1
    elif mutation == 'hash_count':batch['allocation']['baseline_pcm16le_sha256'].pop()
    elif mutation == 'hash_type':batch['allocation']['baseline_pcm16le_sha256'][0] = 5
    elif mutation == 'reason':batch['allocation']['reason'] = 'mixed_source'
    elif mutation == 'status':batch['allocation']['status'] = 'baseline_disjoint'
    else:batch['allocation']['extra'] = 'unbounded'
    with pytest.raises((ValueError, TypeError)):
        links.validate_batch(rehash(batch), require_vectors=False)


def test_header_bounded_and_no_candidates_and_legacy_policy_separation():
    batch, crops = capture_v3(phrases='[1] [S01] Short. [1.5]')
    assert crops == () and batch['allocation'] == links._allocation_header([], 'no_candidates')
    header = links._allocation_header(hashes(1, 2, 3, 4), 'baseline_fallback', 'cross_coordinate_pcm_alias')
    assert len(cells.canonical(header)) <= 512
    old, _ = capture_v2()
    old['allocation'] = header
    with pytest.raises(ValueError, match='mixed_policy_metadata'):
        links.validate_batch(rehash(old), require_vectors=False)
    old, _ = capture_v2()
    with pytest.raises(ValueError, match='mixed_policy'):
        links.reconcile_batches([old, batch])


def test_baseline_hashes_are_captured_evidence_not_independent_pcm_truth():
    batch, _ = capture_v3(ordinary(varying=True), '[1] [S01] A. [6] [2] [S01] B. [7]')
    # A metadata-only replay can prove shape/alias consistency and placement,
    # not recover baseline PCM truth. Current live-lease gates remain required.
    batch['allocation']['baseline_pcm16le_sha256'] = hashes('captured-one', 'captured-two')
    assert links.validate_batch(rehash(batch), require_vectors=False) is batch


def test_staging_precedes_borrowed_baseline_hashes_and_owned_buffers(monkeypatch):
    original = links._bounded
    calls = []
    def bounded(value, maximum=links.MAX_BATCH_BYTES):
        if type(value) is dict and value.get('batch_sha256') == '0' * 64:
            assert value['allocation']['schema'] == links.ALLOCATION_SCHEMA
            return b'x' * 524288
        return original(value, maximum)
    def forbidden(*args):
        calls.append(True)
        raise AssertionError('No PCM access before staging admission')
    monkeypatch.setattr(links, '_bounded', bounded)
    monkeypatch.setattr(links, '_baseline_pcm_evidence', forbidden)
    monkeypatch.setattr(links, '_owned_crop', forbidden)
    with pytest.raises(ValueError, match='staging_capacity'):capture_v3()
    assert calls == []


def test_owned_buffers_only_for_final_crops_and_baseline_hashes_borrow(monkeypatch):
    original_hash, original_crop = links._hash_bytes, links._owned_crop
    baseline_reads, final_crops = [], []
    def observed_hash(value):
        if isinstance(value, memoryview):baseline_reads.append(len(value))
        return original_hash(value)
    def observed_crop(*args):
        crop = original_crop(*args);final_crops.append(len(crop));return crop
    monkeypatch.setattr(links, '_hash_bytes', observed_hash)
    monkeypatch.setattr(links, '_owned_crop', observed_crop)
    batch, _ = capture_v3(ordinary(varying=True), '[1] [S01] A. [6] [2] [S01] B. [7]')
    assert baseline_reads == [64000] * len(batch['nodes'])
    assert final_crops == [64000, 64000]


def test_finite_order_kernel_exhaustive_tiny_domain_oracle():
    intervals = [(a, b) for a in range(4) for b in range(a, 4)]
    cases = assignments = 0
    maxima = dict(orders=0, placements=0, collisions=0, domains=0, insertions=0, comparisons=0)
    for count in range(1, 5):
        for domains in product(intervals, repeat=count):
            cases += 1
            allowed = list(product(*(range(a, b + 1) for a, b in domains)))
            assignments += len(allowed)
            legal = [starts for starts in allowed if len(set(starts)) == count]
            counters = {}
            actual = links._joint_layout(domains, crop_frames=1, counters=counters)
            assert (actual is None) == (not legal)
            if actual is not None:assert actual in legal
            # Independent finite-order oracle uses a set of occupied integer
            # points, not the production interval scan/insertion implementation.
            enumerated = []
            for order in permutations(range(count)):
                used, placed = set(), [None] * count
                for index in order:
                    available = [p for p in range(domains[index][0], domains[index][1] + 1) if p not in used]
                    if not available:break
                    placed[index] = min(available);used.add(placed[index])
                else:enumerated.append(tuple(placed))
            assert actual == (min(enumerated) if enumerated else None)
            for key, value in counters.items():maxima[key] = max(maxima[key], value)
    assert cases == 11110 and assignments == 168420
    assert maxima['orders'] <= 24 and maxima['placements'] <= 96 and maxima['domains'] <= 96
    assert maxima['collisions'] <= 144 and maxima['insertions'] <= 144 and maxima['comparisons'] <= 96


def test_joint_same_source_aliases_use_intersected_legal_domain():
    nodes, parts = numeric_nodes([48000, 48000, 56000])
    output, header = links._allocate_candidates(nodes,
        [(0, 150000), (32000, 112000), (0, 150000)], parts, [], hashes(1, 1, 2))
    assert header['status'] == 'joint_disjoint'
    assert output[0]['start_frame'] == output[1]['start_frame'] == 32000
    assert output[2]['start_frame'] == 0
    assert output[0]['source_key'] == output[1]['source_key']


def test_capture_baseline_pair_truth_fails_closed_before_allocation():
    nodes, _ = numeric_nodes([0, 64000])
    nodes[1]['source_key'] = nodes[0]['source_key']
    support, _, _ = ordinary()
    pcm = b'\1\0' * 64000 + b'\2\0' * 96000
    with pytest.raises(ValueError, match='duplicate_pcm_disagreement'):
        links._baseline_pcm_evidence(nodes, pcm, support)


def test_primary_exact_aliases_roundtrip_and_keep_cannot_link_witnesses():
    _, cell, packets = synthetic_primary_case()
    batch, crops = capture_v3((cell.support, cell.pcm, cell.proof),
        '[0] [S01] Alias. [3]', packets)
    old, _ = capture_v2((cell.support, cell.pcm, cell.proof),
        '[0] [S01] Alias. [3]', primary_packets=packets)
    assert batch['allocation']['status'] == 'baseline_disjoint'
    assert [n['probe_index'] for n in batch['nodes']] == [0, 0] and len(crops) == 1
    assert batch['witnesses'] == old['witnesses'] and batch['primary_packets'] == packets
    assert links.validate_batch(batch, require_vectors=False) is batch
    batch['nodes'][0]['original_turn_ordinals'] = []
    with pytest.raises(ValueError, match='allocation_selection'):
        links.validate_batch(rehash(batch), require_vectors=False)

def test_moved_primary_full_capture_and_validator_regenerate_exact_geometry():
    import json
    from audio_backend import moss_automatic as automatic
    from audio_backend import native_owned_core_seams as core
    from audio_backend import recording_primary_provenance as provenance
    from test_bounded_core_support import synthetic_case, ordinary_origin, primary_row, snapshot_for
    from test_recording_primary_probe import packet_header
    rate = cells.RATE
    case = synthetic_case([0, 8 * rate, 12 * rate], [(7 * rate, 7 * rate + rate // 4)])
    label = case['commits'][0]['scope']['epoch'] + ':speaker_'
    labels = [label + '1', label + '2']
    geometry = [(2, 0, 0, rate), (6, 0, 0, 7 * rate + rate // 4),
                (9, 1, 7 * rate, 7 * rate + rate // 4)]
    turns = [{'label': labels[index], 'original': [a, b]} for _, index, a, b in geometry]
    case['turns'] = deepcopy(turns)
    case['query']['turns'] = deepcopy(turns)
    pcm = b'\1\0' * (4 * rate) + b'\2\0' * (8 * rate)
    checksum = hashlib.sha256(pcm).hexdigest()
    for commit in case['commits']:commit['part']['source_sha256'] = checksum
    for source in case['sources'].values():source['digest'] = checksum
    case['query']['query_pcm16le_sha256'] = checksum
    case['query']['support_pcm16le_sha256'] = checksum
    # Rebuild the synthetic origin with its own exact nonzero PCM hashes;
    # no historical source bytes or live authority are manufactured.
    origin_doc = json.loads(ordinary_origin(case)['proof'])
    origin_pcm = pcm[:2 * 8 * rate]
    support = replace(cells.SourceSupport.from_dict(origin_doc['support']),
        pcm16le_sha256=hashlib.sha256(origin_pcm).hexdigest(),
        float32le_sha256=cells.float32_digest(origin_pcm))
    origin_doc['support'] = support.to_dict()
    origin_proof = cells.canonical(origin_doc)
    _, _, original_parts = cells.validate_document(origin_proof)
    binding = automatic.source_binding('ordinary', origin_doc, support, original_parts, [case['commits'][0]])
    target = core.original_target(core.origin_certificate(origin_proof, [case['commits'][0]], binding))
    snapshot = snapshot_for(case, [primary_row(1, 0, 12 * rate)])
    query = deepcopy(case['query'])
    query['capture_run'] = case['commits'][-1]['scope']['run']
    query['turns'] = [{**turn, 'label': query['capture_run'] + ':' + turn['label']} for turn in turns]
    cell = core.make_bounded_cell(case['commits'], query, case['evidences'][-1], pcm, target, snapshot)
    packet = {'schema': provenance.SCHEMA, 'commit': deepcopy(case['commits'][0]),
        'query': [0, 12 * rate, 0, 12 * rate, 12 * rate, 9, True],
        'owned_pcm16le_sha256': hashlib.sha256(origin_pcm).hexdigest(),
        'labels': labels, 'turns': [[ordinal, index, a, b, a / rate, b / rate]
            for ordinal, index, a, b in geometry],
        'units': [[5, 7, .5, .625, .5, 0, [2, 6]]], 'rows': [[5, [0]]],
        'refs': [['p.1.' + 'a' * 64, None]]}
    packets = [packet_header(packet)]
    source = cell.support, cell.pcm, cell.proof
    phrases = '[2] [S01] Overlapping synthetic crop. [7]'
    old, _ = capture_v2(source, phrases, primary_packets=packets)
    batch, crops = capture_v3(source, phrases, packets)
    primary, moss = batch['nodes']
    assert old['nodes'][0]['original_turn_ordinals'] == [6]
    assert primary['original_turn_ordinals'] == [2, 6]
    assert batch['allocation']['status'] == 'joint_disjoint'
    assert [(n['start_frame'], n['end_frame']) for n in batch['nodes']] == [(1600, 33600), (33600, 65600)]
    assert len(crops) == 2 and primary['source_key'] != old['nodes'][0]['source_key']
    for field in ('primary_source_ref', 'primary_packet_sha256', 'primary_commit',
                  'original_parent_ordinal', 'original_unit_ordinals', 'native_attributions'):
        assert primary[field] == old['nodes'][0][field]
    assert batch['witnesses'] == old['witnesses'] and batch['primary_packets'] == packets
    assert links.validate_batch(batch, require_vectors=False) is batch
    primary['original_turn_ordinals'] = [6]
    with pytest.raises(ValueError, match='allocation_selection'):
        links.validate_batch(rehash(batch), require_vectors=False)


def test_source_aliases_with_distinct_logical_offsets_move_together():
    nodes, parts = numeric_nodes([48000, 48000, 56000])
    second = replace(parts[0], chunk_id='alias', logical_start=200000, logical_end=400000)
    parts.append(second)
    nodes[1]['start_frame'] += 200000
    nodes[1]['end_frame'] += 200000
    nodes[1]['source_spans'][0].update(chunk_id='alias', logical_start=248000, logical_end=280000)
    assert nodes[0]['source_key'] == nodes[1]['source_key']
    output, header = links._allocate_candidates(nodes,
        [(0, 150000), (232000, 312000), (0, 150000)], parts, [], hashes(1, 1, 2))
    assert header['status'] == 'joint_disjoint'
    assert [n['start_frame'] for n in output] == [32000, 232000, 0]
    assert [n['source_spans'][0]['source_start'] for n in output] == [32000, 32000, 0]
    assert output[0]['source_key'] == output[1]['source_key']
    assert not links._source_intersects(output[0], output[2])
    assert not links._source_intersects(output[1], output[2])
