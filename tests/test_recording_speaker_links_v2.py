"""Pure synthetic typed-link contracts. Authored source-only; no acoustic claim.

Execution requires the separately approved gate. No models or historical input.
"""
from copy import deepcopy

import pytest

from audio_backend import recording_speaker_links as links
from audio_backend import recording_primary_probe as primary_probe
from audio_backend.native_owned_cells import float32_digest, sha
from test_recording_speaker_links import ordinary, projection, vector, matrix, capture
from test_recording_primary_probe import synthetic_primary_case
from test_bounded_core_support import final_cell, primary_row


def capture_v2(source=None, phrases=None, job='typed-job', primary_packets=()):
    support, pcm, proof = source or ordinary()
    return links.capture_candidates_v2(support, pcm, proof, projection(support, phrases),
        job, '1' * 64, links.expected_space(), primary_packets=primary_packets)


def rehash(batch):
    """Exercise semantic validation after internally consistent hash changes."""
    for node in batch['nodes']:
        original = {k: v for k, v in node.items() if k not in ('node_id', 'vector')}
        node['node_id'] = sha({'schema': links.SCHEMA_V2,
            'policy_sha256': links.POLICY_SHA256_V2, **original})
    for outcome in batch['outcomes']:
        if outcome['status'] != 'candidate':
            continue
        key = 'primary_source_ref' if 'primary_source_ref' in outcome else 'phrase_index'
        node = next(n for n in batch['nodes'] if n.get(key) == outcome[key])
        outcome['node_id'] = node['node_id']
    batch['batch_sha256'] = links._batch_digest(batch)
    return batch


def test_new_ordinary_first_job_uses_v2_and_v1_remains_unchanged():
    old, old_crops = capture()
    new, new_crops = capture_v2()
    assert old['schema'] == links.SCHEMA
    assert old['policy_sha256'] == links.POLICY_SHA256
    assert 'source_kind' not in old['nodes'][0]
    assert new['schema'] == links.SCHEMA_V2
    assert new['policy_sha256'] == links.POLICY_SHA256_V2
    assert new['primary_packets'] == []
    assert new_crops == old_crops
    assert new['nodes'][0]['source_kind'] == 'moss'
    assert new['nodes'][0]['float32le_sha256'] == float32_digest(new_crops[0])
    assert new['phrase_evidence'][0]['inward'] == [16000, 144000]
    assert links.validate_batch(new, require_vectors=False) is new
    assert links.validate_batch(old, require_vectors=False) is old
    assert links.SUPPORTED_POLICIES == (links.POLICY_SHA256, links.POLICY_SHA256_V2)


def test_four_typed_alias_admissions_share_one_physical_probe():
    phrases = ' '.join('[1] [S01] Synthetic duplicate. [9]' for _ in range(6))
    batch, crops = capture_v2(phrases=phrases)
    assert len(batch['nodes']) == 4
    assert len(crops) == 1
    assert [n['probe_index'] for n in batch['nodes']] == [0, 0, 0, 0]
    assert [o['status'] for o in batch['outcomes']] == ['candidate'] * 4 + ['crop_capacity'] * 2
    for node in batch['nodes']:
        node['vector'] = vector(1.)
    answer = links.reconcile_batches([batch])
    assert answer['diagnostics']['schema'] == links.SCHEMA_V2
    assert answer['diagnostics']['independent_node_count'] == 1
    assert answer['diagnostics']['duplicate_count'] == 3
    assert all(a['duplicate_evidence'] and not a['speaker_identity_valid'] for a in answer['annotations'])


def test_primary_and_moss_exact_crop_share_probe_and_keep_typed_refs():
    _, cell, packets = synthetic_primary_case()
    batch, crops = capture_v2((cell.support, cell.pcm, cell.proof),
        '[0] [S01] Synthetic alias. [3]', primary_packets=packets)
    assert [n['source_kind'] for n in batch['nodes']] == ['native_primary', 'moss']
    assert [n['probe_index'] for n in batch['nodes']] == [0, 0]
    assert len(crops) == 1
    primary, moss = batch['nodes']
    assert primary['source_key'] == moss['source_key']
    assert primary['pcm16le_sha256'] == moss['pcm16le_sha256']
    assert primary['float32le_sha256'] == moss['float32le_sha256']
    assert len(primary['native_attributions']) == 2
    assert moss['native_attributions'] == [primary['native_attributions'][1]]
    assert batch['primary_packets'] == packets
    for node in batch['nodes']:
        node['vector'] = vector(1.)
    result = links.reconcile_batches([batch])
    assert result['diagnostics']['independent_node_count'] == 1
    annotation = next(a for a in result['annotations'] if a['source_kind'] == 'native_primary')
    assert annotation['primary_source_ref'] == packets[0]['packet']['refs'][0][0]
    assert annotation['native_attributions'] == primary['native_attributions']
    assert 'phrase_index' not in annotation and 'moss_tag' not in annotation
    assert annotation['speaker_identity_valid'] is False


@pytest.mark.parametrize('embedded_kind', [None, 'moss', 'native_primary', 'neither'])
def test_cross_namespace_alias_cannot_link_contradiction_fails_closed(embedded_kind):
    case, _, packets = synthetic_primary_case()
    query = deepcopy(case['query'])
    for turn in query['turns']:
        if turn['label'].endswith(':speaker_2'):
            turn['original'] = [40000, 96000]
        elif turn['label'].endswith(':speaker_3'):
            turn['original'] = [96000, 128000]
    cell = final_cell(case, [primary_row(1, 0, 192000)], query=query)
    batch, crops = capture_v2((cell.support, cell.pcm, cell.proof),
        '[3.25] [S01] Other local speaker. [6]', primary_packets=packets)
    assert [n['source_kind'] for n in batch['nodes']] == ['native_primary', 'moss']
    primary, moss = batch['nodes']
    assert primary['native_scope_id'] != moss['native_scope_id']
    assert primary['native_attributions'][1]['native_scope_id'] == moss['native_scope_id']
    assert primary['source_key'] != moss['source_key']
    assert primary['pcm16le_sha256'] == moss['pcm16le_sha256']
    assert len(crops) == 1
    for node in batch['nodes']:
        if embedded_kind is None or embedded_kind == node['source_kind']:
            node['vector'] = vector(1.)
    with pytest.raises(ValueError, match='duplicate_native_conflict'):
        links.reconcile_batches([batch])


def test_metadata_admission_precedes_only_selected_crop_allocations(monkeypatch):
    _, cell, packets = synthetic_primary_case()
    original_crop = links._owned_crop
    original_metadata = primary_probe.primary_metadata
    events = []

    def metadata(*args, **kwargs):
        events.append('metadata')
        return original_metadata(*args, **kwargs)

    def crop(*args):
        assert events and events[0] == 'metadata'
        events.append('crop')
        return original_crop(*args)

    def no_speculative_pool(*args, **kwargs):
        raise AssertionError('Typed admission must not allocate a speculative candidate crop pool')

    monkeypatch.setattr(primary_probe, 'primary_metadata', metadata)
    monkeypatch.setattr(primary_probe, 'primary_candidates', no_speculative_pool)
    monkeypatch.setattr(links, 'capture_candidates', no_speculative_pool)
    monkeypatch.setattr(links, '_owned_crop', crop)
    phrases = ' '.join('[0] [S01] Synthetic duplicate. [3]' for _ in range(8))
    batch, crops = capture_v2((cell.support, cell.pcm, cell.proof), phrases,
                             primary_packets=packets)
    assert len(batch['nodes']) == 4
    assert len(crops) == events.count('crop') == 1
    assert [n['source_kind'] for n in batch['nodes']] == ['native_primary', 'moss', 'moss', 'moss']


def test_staging_reservation_rejects_before_any_pcm_slice(monkeypatch):
    original_bounded = links._bounded
    materialized = []

    def bounded(value, maximum=links.MAX_BATCH_BYTES):
        if (type(value) is dict and value.get('schema') == links.SCHEMA_V2
                and value.get('batch_sha256') == '0' * 64):
            return b'x' * 524288
        return original_bounded(value, maximum)

    def crop(*args):
        materialized.append(True)
        raise AssertionError('PCM slicing must follow staging admission')

    monkeypatch.setattr(links, '_bounded', bounded)
    monkeypatch.setattr(links, '_owned_crop', crop)
    with pytest.raises(ValueError, match='staging_capacity'):
        capture_v2()
    assert materialized == []


@pytest.mark.parametrize('mutation', ['noncontiguous', 'split_alias', 'float_hash'])
def test_probe_index_and_exact_float_alias_binding_fail_closed(mutation):
    batch, _ = capture_v2(phrases='[1] [S01] A. [9] [1] [S01] B. [9]')
    if mutation == 'noncontiguous':
        for node in batch['nodes']:
            node['probe_index'] = 2
    elif mutation == 'split_alias':
        batch['nodes'][1]['probe_index'] = 1
    else:
        batch['nodes'][1]['float32le_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='probe_|duplicate_pcm_disagreement'):
        links.validate_batch(rehash(batch), require_vectors=False)


@pytest.mark.parametrize('overlap', [{(0, 1)}, {(0, 2)}, {(1, 2)}])
def test_source_overlap_vetoes_any_selected_trio_pair_without_substitution(overlap):
    scores = matrix(4, {(0, 1): .95, (0, 2): .8, (0, 3): .1,
                        (1, 2): .2, (1, 3): .1, (2, 3): .1})
    groups, traces = links.cluster_scores(scores, 4, source_overlap=overlap)
    assert groups == [(0,), (1,), (2,), (3,)]
    assert traces[0]['reasons']['source_overlap_dependency'] >= 1
    assert traces[0]['merges'] == []


def test_unrelated_fourth_overlap_does_not_suppress_disjoint_winner_trio():
    scores = matrix(4, {(0, 1): .95, (0, 2): .3, (0, 3): .1,
                        (1, 2): .2, (1, 3): .1, (2, 3): .1})
    groups, traces = links.cluster_scores(scores, 4, source_overlap={(2, 3)})
    assert (0, 1) in groups
    assert traces[0]['merges'] == [[[0], [1]]]
    assert traces[0]['reasons']['source_overlap_dependency'] == 0


def test_two_independent_typed_nodes_still_abstain():
    first, _ = capture_v2(ordinary(seed=1), job='one')
    second, _ = capture_v2(ordinary(seed=2), job='two')
    for batch in (first, second):
        batch['nodes'][0]['vector'] = vector(1.)
    answer = links.reconcile_batches([first, second])
    assert answer['diagnostics']['independent_node_count'] == 2
    assert answer['diagnostics']['round_reasons']['missing_runner_up'] == 2
    assert all(a['status'] == 'source_scoped' for a in answer['annotations'])


def test_mixed_policies_rejected_even_when_space_session_and_pcm_match():
    old, _ = capture(job='old')
    new, _ = capture_v2(job='new')
    with pytest.raises(ValueError, match='mixed_policy'):
        links.reconcile_batches([old, new])


@pytest.mark.parametrize('target', ['batch', 'node', 'outcome'])
def test_v1_rehashed_typed_metadata_cannot_inject_v2_attributions(target):
    batch, _ = capture()
    if target == 'batch':
        batch['primary_packets'] = []
    elif target == 'node':
        node = batch['nodes'][0]
        node.update(source_kind='moss', native_attributions=[{
            'native_scope_id': node['native_scope_id'], 'native_label': node['native_label']}])
        original = {k: v for k, v in node.items() if k != 'node_id'}
        node['node_id'] = sha({'schema': links.SCHEMA, 'policy_sha256': links.POLICY_SHA256, **original})
        batch['outcomes'][0]['node_id'] = node['node_id']
    else:
        batch['outcomes'][0]['primary_source_ref'] = 'p.1.' + 'a' * 64
    batch['batch_sha256'] = links._batch_digest(batch)
    with pytest.raises(ValueError, match='mixed_policy_metadata'):
        links.validate_batch(batch, require_vectors=False)


@pytest.mark.parametrize('field,value', [('primary_row_index', 0),
    ('capture_namespace', {}), ('original_turn_ordinals', [1])])
def test_v2_moss_rejects_rehashed_primary_metadata(field, value):
    batch, _ = capture_v2()
    batch['nodes'][0][field] = value
    with pytest.raises(ValueError, match='phrase_binding'):
        links.validate_batch(rehash(batch), require_vectors=False)


def test_source_overlap_uses_source_coordinates_and_frames_not_logical_axis():
    one = {'source_spans': [{'source_sha256': 'a' * 64, 'source_frames': 100,
        'source_start': 10, 'source_end': 30, 'logical_start': 1000, 'logical_end': 1020}]}
    two = {'source_spans': [{'source_sha256': 'a' * 64, 'source_frames': 100,
        'source_start': 20, 'source_end': 40, 'logical_start': 9000, 'logical_end': 9020}]}
    assert links._source_intersects(one, two)
    two['source_spans'][0]['source_frames'] = 101
    assert not links._source_intersects(one, two)
    two['source_spans'][0]['source_frames'] = 100
    two['source_spans'][0]['source_start'] = 30
    assert not links._source_intersects(one, two)


def test_rehashed_numeric_moss_crop_change_cannot_bypass_admissibility():
    batch, _ = capture_v2()
    batch['phrase_evidence'][0]['probe_source_eligible'] = False
    with pytest.raises(ValueError, match='phrase_evidence|phrase_binding'):
        links.validate_batch(rehash(batch), require_vectors=False)


def test_v2_order_invariant_reconciliation_and_provisional_annotations():
    batches = []
    for seed, values in ((1, (1., 0.)), (2, (1., .1)), (3, (0., 1.))):
        batch, _ = capture_v2(ordinary(seed=seed), job=str(seed))
        batch['nodes'][0]['vector'] = vector(*values)
        batches.append(batch)
    forward = links.reconcile_batches(batches)
    assert forward == links.reconcile_batches(list(reversed(batches)))
    assert forward['diagnostics']['linked_cluster_count'] == 1
    assert forward['diagnostics']['source_overlap_pair_count'] == 0
    assert all(a['speaker_identity_valid'] is False for a in forward['annotations'])


def test_unembedded_bridge_alias_preserves_partial_source_overlap_veto():
    shared = ordinary(seed=1, varying=True)
    first, _ = capture_v2(ordinary(seed=1), job='first')
    second, _ = capture_v2(shared, '[3.5] [S01] Mixed crop. [6.5]', job='second')
    third, _ = capture_v2(ordinary(seed=3), job='third')
    alias, _ = capture_v2(shared, '[2] [S01] Constant alias. [5]', job='alias')
    for batch, values in ((first, (1., 0.)), (second, (1., .1)), (third, (0., 1.))):
        batch['nodes'][0]['vector'] = vector(*values)
    assert links.reconcile_batches([first, second, third])['diagnostics']['linked_cluster_count'] == 1
    assert alias['nodes'][0]['pcm16le_sha256'] == first['nodes'][0]['pcm16le_sha256']
    assert alias['nodes'][0]['source_key'] != first['nodes'][0]['source_key']
    assert links._source_intersects(alias['nodes'][0], second['nodes'][0])
    answer = links.reconcile_batches([first, second, third, alias])
    assert answer['diagnostics']['independent_node_count'] == 3
    assert answer['diagnostics']['unembedded_alias_count'] == 1
    assert answer['diagnostics']['source_overlap_pair_count'] == 1
    assert answer['diagnostics']['linked_cluster_count'] == 0
    unembedded = next(a for a in answer['annotations'] if a['job_id'] == 'alias')
    assert unembedded['status'] == 'probe_unavailable'
    assert unembedded['provisional_speaker_id'] is None


def test_unembedded_independent_class_never_supplies_missing_runner_up():
    batches = []
    for seed in (1, 2, 3):
        batch, _ = capture_v2(ordinary(seed=seed), job=str(seed))
        if seed != 3:
            batch['nodes'][0]['vector'] = vector(1.)
        batches.append(batch)
    answer = links.reconcile_batches(batches)
    assert answer['diagnostics']['independent_node_count'] == 2
    assert answer['diagnostics']['pair_count'] == 1
    assert answer['diagnostics']['linked_cluster_count'] == 0
    assert answer['diagnostics']['round_reasons']['missing_runner_up'] == 2
