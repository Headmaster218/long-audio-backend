"""Source-only candidate fixtures; synthetic bytes, no model/audio invocation."""
import hashlib
import math
import random
import struct

import pytest

from audio_backend import recording_speaker_history_compare as subject
from audio_backend import recording_speaker_links as legacy


def vector(*values):
    values = list(values) + [0.] * (512 - len(values))
    norm = math.sqrt(math.fsum(value * value for value in values))
    return struct.pack('<512f', *(value / norm for value in values))


def observation(index, values=(1.,), **changes):
    raw_vector = vector(*values)
    row = {'sequence': index + 1,
        'observation_id': hashlib.sha256(f'observation-{index}'.encode()).hexdigest(),
        'evidence_kind': 'vector', 'invocation_id': f'{index:032x}',
        'certificate_id': hashlib.sha256(f'certificate-{index}'.encode()).hexdigest(),
        'node_id': f'node-{index:04}', 'origin_kind': 'primary_commit',
        'source_key': f'source-{index}', 'pcm_hash': f'pcm-{index}',
        'float_hash': f'float-{index}', 'vector': raw_vector,
        'vector_sha256': hashlib.sha256(raw_vector).hexdigest(),
        'attributions': (('native', f'label-{index}'),),
        'spans': (('source-version', 10000, index * 100, index * 100 + 10),),
        'refs': (('primary', f'primary-ref-{index}'),)}
    row.update(changes)
    if row['vector'] is None and 'evidence_kind' not in changes:
        row.update(evidence_kind='candidate_missing', certificate_id=None, vector_sha256=None)
    elif row['vector'] is not None and 'vector' in changes and 'vector_sha256' not in changes:
        row['vector_sha256'] = hashlib.sha256(row['vector']).hexdigest()
    return row


def token(rows, witnesses=(), revision=1, authority=1):
    return subject.SnapshotToken('a' * 64, 'primary-clean-history-v1', 'b' * 64,
                                 revision, authority, len(rows), len(witnesses),
                                 sum(row['vector'] is not None for row in rows))


def loaded(rows, witnesses=(), *, revision=1, authority=1, **kwargs):
    snapshot = token(rows, witnesses, revision, authority)
    build = subject.ComparisonBuild(snapshot,
        admitted_bytes=kwargs.pop('admitted_bytes', subject.MAX_MEMORY_BYTES), **kwargs)
    build.feed_observations(snapshot, 0, rows, complete=True)
    build.feed_witnesses(snapshot, 0, list(witnesses), complete=True)
    return build


def finish(build):
    for _ in range(20000):
        status = build.step()
        if status['state'] not in ('loading', 'pending'):
            return status
    raise AssertionError('synthetic fixture step bound')


def annotations(build):
    rows, after = [], 0
    while True:
        page = build.output_page(after, limit=1)
        assert len(subject.canonical(page)) <= subject.PAGE_BYTES
        rows.extend(row for row in page['rows'] if row['kind'] == 'annotation')
        if page['complete']:
            return rows
        after = page['next_after']


def consume(generator):
    while True:
        try:
            next(generator)
        except StopIteration as result:
            return result.value


@pytest.mark.parametrize('n', [0, 1, 2, 3, 4, 8, 16, 32])
def test_small_exact_compact_legacy_oracle(n):
    rng = random.Random(1701 + n)
    scores = {(a, b): rng.choice([-.1, 0., .2, .44, .55, .65, .8, .9, 1.])
              for a in range(n) for b in range(a + 1, n)}
    cannot = {pair for pair in scores if rng.random() < .04}
    overlap = {pair for pair in scores if rng.random() < .05}
    expected_groups, expected_trace = legacy.cluster_scores(scores, n, cannot, source_overlap=overlap)
    nodes, trace = consume(subject.cluster(subject.Matrix.from_scores(scores, n, cannot, overlap), subject.Work()))
    assert [tuple(sorted(trace.members(node))) for node in nodes] == expected_groups
    assert trace.oracle_trace() == expected_trace


@pytest.mark.parametrize('top,runner', [(.55, .45), (math.nextafter(.55, -math.inf), .45),
    (.55, math.nextafter(.45, -math.inf)), (.9, .9), (.9, math.nextafter(.8, math.inf))])
def test_threshold_margin_and_exact_tie_unchanged(top, runner):
    scores = {(0, 1): top, (0, 2): runner, (1, 2): runner}
    expected = legacy.cluster_scores(scores, 3, source_overlap=())
    nodes, trace = consume(subject.cluster(subject.Matrix.from_scores(scores, 3), subject.Work()))
    assert ([tuple(sorted(trace.members(node))) for node in nodes], trace.oracle_trace()) == expected


def test_three_observation_a_a_b_links_and_two_abstain():
    rows = [observation(0), observation(1, (4., 3.), origin_kind='moss_result'), observation(2, (0., 0., 1.),
        origin_kind='moss_result', refs=(('moss', '{"job_id":"j","result_sha256":"r","phrase_index":0}'),))]
    build = loaded(rows)
    assert finish(build)['state'] == 'ready'
    output = annotations(build)
    assert [row['body']['status'] for row in output] == ['provisional_linked', 'provisional_linked', 'source_scoped']
    assert output[0]['body']['provisional_speaker_id'] == output[1]['body']['provisional_speaker_id']
    assert output[2]['ref_kind'] == 'moss'
    assert output[1]['ref_kind'] == 'primary'
    assert [row['ordinal'] for row in build.output_page()['rows']] == list(range(1, build.decision_counts['total_rows'] + 1))
    assert all(row['body']['speaker_identity_valid'] is False for row in output)
    pair = loaded(rows[:2])
    assert finish(pair)['state'] == 'ready'
    assert [row['body']['status'] for row in annotations(pair)] == ['source_scoped', 'source_scoped']


def test_late_competitor_reuses_exact_prefix_without_old_decision():
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    first = loaded(rows)
    assert finish(first)['state'] == 'ready'
    prefix = first.score_prefix
    old = [struct.pack('<d', prefix.scores[index]) for index in range(3)]
    second = loaded(rows + [observation(3, (3., 1.))], revision=2, prefix=prefix)
    with pytest.raises(subject.Pending):
        second.output_page()
    assert finish(second)['state'] == 'ready'
    assert second.work.counts['dot_products'] == 3
    assert [struct.pack('<d', prefix.scores[index]) for index in range(3)] == old
    assert all(row['body']['status'] == 'source_scoped' for row in annotations(second))


def test_original_float32_dot_products_are_exact():
    rows = [observation(0, (1., 2., 3.)), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    build = loaded(rows)
    assert finish(build)['state'] == 'ready'
    for b in range(3):
        for a in range(b):
            one, two = memoryview(rows[a]['vector']).cast('f'), memoryview(rows[b]['vector']).cast('f')
            expected = math.fsum(x * y for x, y in zip(one, two))
            assert struct.pack('<d', build.score_prefix.scores[subject.pair_index(a, b)]) == struct.pack('<d', expected)
    changed = dict(rows[0], vector=vector(0., 1.),
                   vector_sha256=hashlib.sha256(vector(0., 1.)).hexdigest())
    with pytest.raises(subject.Invalid, match='score_prefix_row_mismatch'):
        loaded([changed] + rows[1:], revision=2, prefix=build.score_prefix)


def test_late_metadata_alias_preserves_overlap_and_no_extra_vote():
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    alias = observation(3, node_id='z-alias', vector=None,
        source_key='different-source-alias', pcm_hash=rows[0]['pcm_hash'], float_hash=rows[0]['float_hash'],
        attributions=rows[0]['attributions'], spans=rows[2]['spans'], origin_kind='moss_result', refs=(('moss', 'alias-ref'),))
    build = loaded(rows + [alias])
    assert finish(build)['state'] == 'ready'
    output = annotations(build)
    assert all(row['body']['status'] != 'provisional_linked' for row in output)
    assert output[0]['body']['duplicate_evidence'] is True
    assert output[-1]['body']['status'] == 'probe_unavailable'
    assert build.decision_counts['annotation'] == 4
    assert build.decision_counts['independent_observations'] == 3


def test_witness_invalidates_alias_even_without_vector():
    one = observation(0, vector=None)
    two = observation(1, vector=None, source_key=one['source_key'], pcm_hash=one['pcm_hash'],
        float_hash=one['float_hash'], attributions=(('native', 'other'),))
    witness = {'native_scope_id': 'native', 'labels': ('label-0', 'other'), 'proof_sha256': 'c' * 64}
    build = loaded([one, two], [witness])
    status = finish(build)
    assert status['state'] == 'refused' and status['reason'] == 'duplicate_native_conflict'
    with pytest.raises(subject.Pending):
        build.output_page()


def test_late_missing_vector_rival_blocks_prior_positive_and_keeps_score_prefix():
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    first = loaded(rows)
    assert finish(first)['state'] == 'ready'
    assert annotations(first)[0]['body']['status'] == 'provisional_linked'
    prefix = first.score_prefix
    prior_scores = [struct.pack('<d', prefix.scores[index]) for index in range(3)]
    missing = observation(3, vector=None)
    second = loaded(rows + [missing], revision=2, prefix=prefix)
    status = finish(second)
    assert status['state'] == 'refused' and status['reason'] == 'unknown_rival'
    assert status['loaded_rows'] == 4 and status['loaded_vectors'] == 3
    assert len(second._physical) == 4
    assert second._trace is None
    assert second.work.counts.get('dot_products', 0) == 0
    assert prefix.row_count == 4 and prefix.vector_count == 3
    assert prefix.vector_slots == [0, 1, 2, -1]
    assert [struct.pack('<d', prefix.scores[index]) for index in range(3)] == prior_scores
    with pytest.raises(subject.Pending, match='unknown_rival'):
        second.output_page()
    with pytest.raises(subject.Pending, match='unknown_rival'):
        _ = second.decision_counts
    # The bounded owner stores this reason for the unchanged snapshot; even a
    # direct repeated step cannot silently restart or publish this build.
    assert second.step() == status


def test_exact_missing_alias_resolves_without_extra_vote_or_score_change():
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    first = loaded(rows)
    assert finish(first)['state'] == 'ready'
    prefix = first.score_prefix
    prior_scores = [struct.pack('<d', prefix.scores[index]) for index in range(3)]
    alias = observation(3, vector=None, pcm_hash=rows[0]['pcm_hash'],
                        float_hash=rows[0]['float_hash'])
    second = loaded(rows + [alias], revision=2, prefix=prefix)
    assert finish(second)['state'] == 'ready'
    assert second.decision_counts['independent_observations'] == 3
    output = annotations(second)
    assert output[0]['body']['status'] == output[1]['body']['status'] == 'provisional_linked'
    assert output[0]['body']['duplicate_evidence'] is True
    assert output[3]['body']['status'] == 'probe_unavailable'
    assert second.work.counts.get('dot_products', 0) == 0
    assert [struct.pack('<d', prefix.scores[index]) for index in range(3)] == prior_scores


def test_new_measured_alias_resolves_retained_missing_row_on_later_revision():
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.)),
            observation(3, vector=None)]
    first = loaded(rows)
    assert finish(first)['reason'] == 'unknown_rival'
    prefix = first.score_prefix
    missing_fingerprint = prefix.fingerprints[3]
    prior_scores = [struct.pack('<d', prefix.scores[index]) for index in range(3)]
    alias = observation(4, (0., 0., 0., 1.), source_key=rows[3]['source_key'],
                        pcm_hash=rows[3]['pcm_hash'], float_hash=rows[3]['float_hash'])
    second = loaded(rows + [alias], revision=2, prefix=prefix)
    assert finish(second)['state'] == 'ready'
    assert second.decision_counts['independent_observations'] == 4
    assert second.work.counts['dot_products'] == 3
    assert prefix.fingerprints[3] == missing_fingerprint
    assert prefix.vector_slots == [0, 1, 2, -1, 3]
    assert [struct.pack('<d', prefix.scores[index]) for index in range(3)] == prior_scores


def test_late_missing_alias_bridge_conflict_precedes_unknown_rival_barrier():
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    # Sort the independent unknown first to prove it cannot stop validation of
    # the later source/PCM bridge between two inconsistent physical classes.
    unknown = observation(3, vector=None, node_id='000-independent-unknown')
    bridge = observation(4, vector=None, source_key=rows[0]['source_key'],
                         pcm_hash=rows[1]['pcm_hash'], float_hash=rows[1]['float_hash'])
    build = loaded(rows + [unknown, bridge])
    status = finish(build)
    assert status['state'] == 'refused' and status['reason'] == 'duplicate_pcm_disagreement'
    assert build._trace is None
    with pytest.raises(subject.Pending, match='duplicate_pcm_disagreement'):
        build.output_page()


def test_late_alias_hard_witness_conflict_precedes_unknown_rival_barrier():
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    unknown = observation(3, vector=None, node_id='000-independent-unknown')
    alias = observation(4, vector=None, pcm_hash=rows[0]['pcm_hash'],
                        float_hash=rows[0]['float_hash'], attributions=(('native', 'other'),))
    witness = {'native_scope_id': 'native', 'labels': ('label-0', 'other'), 'proof_sha256': 'c' * 64}
    build = loaded(rows + [unknown, alias], [witness])
    status = finish(build)
    assert status['state'] == 'refused' and status['reason'] == 'duplicate_native_conflict'
    assert build._trace is None


def test_unknown_rival_waits_for_complete_cross_group_witness_constraints():
    rows = [observation(0, vector=None), observation(1), observation(2, (0., 1.))]
    witness = {'native_scope_id': 'native', 'labels': ('label-0', 'label-2'), 'proof_sha256': 'c' * 64}
    build = loaded(rows, [witness])
    status = finish(build)
    assert status['state'] == 'refused' and status['reason'] == 'unknown_rival'
    assert status['counts']['cross_witness_checks'] == 3
    assert status['counts']['source_span_checks'] == 3
    assert status['counts']['matrix_pair_updates'] == 3
    assert build._trace is None


def test_witness_only_empty_history_constrains_without_creating_rival():
    witness = {'native_scope_id': 'native', 'labels': ('label-0', 'label-1'), 'proof_sha256': 'd' * 64}
    build = loaded([], [witness])
    assert finish(build)['state'] == 'ready'
    assert build.token.observation_count == build.token.vector_count == 0
    assert build.decision_counts == {'annotation': 0, 'merge': 0, 'round': 0,
                                    'total_rows': 0, 'independent_observations': 0}
    assert build.output_page()['rows'] == []


def test_late_witness_only_basis_vetoes_previous_positive_without_new_rival():
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    first = loaded(rows)
    assert finish(first)['state'] == 'ready'
    assert annotations(first)[0]['body']['status'] == 'provisional_linked'
    prefix = first.score_prefix
    prior_scores = [struct.pack('<d', prefix.scores[index]) for index in range(3)]
    witness = {'native_scope_id': 'native', 'labels': ('label-0', 'label-1'), 'proof_sha256': 'd' * 64}
    second = loaded(rows, [witness], revision=2, prefix=prefix)
    assert finish(second)['state'] == 'ready'
    assert second.decision_counts['independent_observations'] == 3
    assert all(row['body']['status'] == 'source_scoped' for row in annotations(second))
    assert second.work.counts.get('dot_products', 0) == 0
    assert [struct.pack('<d', prefix.scores[index]) for index in range(3)] == prior_scores


def test_pending_row_cannot_enter_loaded_rows_or_score_cache():
    row = observation(0, vector=None)
    row['evidence_kind'] = 'pending'
    snapshot = token([row])
    build = subject.ComparisonBuild(snapshot, admitted_bytes=subject.MAX_MEMORY_BYTES)
    with pytest.raises(subject.Invalid, match='pending_observation'):
        build.feed_observations(snapshot, 0, [row], complete=True)
    assert build.rows == [] and build.score_prefix.row_count == 0
    assert build.score_prefix.vector_count == len(build.score_prefix.scores) == 0
    with pytest.raises(subject.Pending):
        build.output_page()


def test_multiple_missing_candidates_keep_distinct_observation_ids_without_certificates():
    rows = [observation(0, vector=None), observation(1, vector=None)]
    build = loaded(rows)
    assert finish(build)['reason'] == 'unknown_rival'
    assert len(build._physical) == 2
    duplicate = dict(rows[1], observation_id=rows[0]['observation_id'])
    with pytest.raises(subject.Invalid, match='duplicate_observation'):
        loaded([rows[0], duplicate])


@pytest.mark.parametrize('changes,reason', [
    ({'certificate_id': 'd' * 64}, 'missing_observation_payload'),
    ({'vector_sha256': 'd' * 64}, 'missing_observation_payload'),
    ({'vector': vector(1.)}, 'missing_observation_payload'),
    ({'invocation_id': None}, 'bounded_text'),
    ({'invocation_id': 'short'}, 'observation_invocation'),
])
def test_missing_candidate_payload_consistency(changes, reason):
    row = observation(0, vector=None)
    row.update(changes)
    with pytest.raises(subject.Invalid, match=reason):
        subject.Observation.from_record(row)


def test_snapshot_vector_count_is_independent_and_checked_at_page_boundary():
    rows = [observation(0), observation(1, vector=None)]
    good = token(rows)
    assert good.observation_count == 2 and good.vector_count == 1
    assert 'certificate_count' not in good.document()
    bad = subject.SnapshotToken.from_record(dict(good.document(), vector_count=2))
    build = subject.ComparisonBuild(bad, admitted_bytes=subject.MAX_MEMORY_BYTES)
    with pytest.raises(subject.Invalid, match='vector_count_mismatch'):
        build.feed_observations(bad, 0, rows, complete=True)
    assert build.rows == [] and build.score_prefix.row_count == 0
    with pytest.raises(subject.Invalid, match='snapshot_vector_count'):
        subject.SnapshotToken.from_record(dict(good.document(), vector_count=3))


def test_transport_envelope_does_not_reduce_immutable_source_header_capacity():
    row = observation(0, origin_kind='moss_result', node_id='n' * 64,
        source_key='s' * 64, pcm_hash='p' * 64, float_hash='f' * 64,
        attributions=tuple((f'{index:064x}', ('"' if index < 2 else 'l') * 64) for index in range(4)),
        spans=tuple((f'{index:064x}', 2**63 - 1, 2**63 - 11, 2**63 - 1) for index in range(4)),
        refs=(('moss', 'r' * 192),))
    source_header = {key: row[key] for key in ('origin_kind', 'node_id', 'source_key',
                                             'pcm_hash', 'float_hash', 'attributions', 'spans')}
    source_header.update(schema=1, source_kind='moss', ref_kind='moss', ref_key='r' * 192,
                         space_id='b' * 64)
    padding = 2048 - len(subject.canonical(source_header))
    assert 0 <= padding <= 192
    source_header['space_id'] += 'b' * padding
    assert len(subject.canonical(source_header)) == 2048
    observation_row = subject.Observation.from_record(row)
    assert 2048 < len(subject.canonical(observation_row.metadata())) <= 3072
    snapshot = subject.SnapshotToken.from_record(dict(token([row]).document(), space_id=source_header['space_id']))
    build = subject.ComparisonBuild(snapshot, admitted_bytes=subject.MAX_MEMORY_BYTES)
    build.feed_observations(snapshot, 0, [row], complete=True)
    build.feed_witnesses(snapshot, 0, [], complete=True)
    assert finish(build)['state'] == 'ready'
    assert len(subject.canonical(build.output_page())) <= subject.PAGE_BYTES
    assert build.required_bytes <= build.admitted_bytes

    # A count-bounded SQL read is not automatically a byte-bounded transport
    # page. These valid envelopes require the store to stop before 32 rows.
    rows = [dict(row, sequence=index + 1, node_id=f'{index:064x}',
                 observation_id=hashlib.sha256(f'observation-{index}'.encode()).hexdigest(),
                 certificate_id=hashlib.sha256(f'certificate-{index}'.encode()).hexdigest(),
                 invocation_id=f'{index:032x}', refs=(('moss', f'{index:0192x}'),))
            for index in range(32)]
    snapshot = subject.SnapshotToken.from_record(dict(token(rows).document(), space_id=source_header['space_id']))
    paged = subject.ComparisonBuild(snapshot, admitted_bytes=subject.MAX_MEMORY_BYTES)
    with pytest.raises(subject.Invalid, match='observation_page_bytes'):
        paged.feed_observations(snapshot, 0, rows, complete=True)
    assert paged.rows == []
    paged.feed_observations(snapshot, 0, rows[:31], complete=False)
    assert len(paged.rows) == 31 and not paged._observations_complete
    paged.feed_observations(snapshot, 31, rows[31:], complete=True)
    assert len(paged.rows) == 32 and paged._observations_complete
    assert paged.rows[-1].refs == rows[-1]['refs']
    paged.feed_witnesses(snapshot, 0, [], complete=True)
    assert finish(paged)['state'] == 'ready'
    assert paged.decision_counts['annotation'] == 32


def test_cannot_and_overlap_veto_winner_without_promotion():
    scores = {(0, 1): .95, (0, 2): .7, (0, 3): .1, (1, 2): .1, (1, 3): .1, (2, 3): .1}
    for cannot, overlap in [({(0, 1)}, ()), ((), {(1, 2)})]:
        nodes, trace = consume(subject.cluster(subject.Matrix.from_scores(scores, 4, cannot, overlap), subject.Work()))
        assert (0, 2) not in [tuple(sorted(trace.members(node))) for node in nodes]
        assert trace.oracle_trace() == legacy.cluster_scores(scores, 4, cannot, source_overlap=overlap)[1]


def test_preemption_resumes_exact_program_counter_cancel_does_not_publish(monkeypatch):
    monkeypatch.setattr(subject, 'QUANTUM_UPDATES', 4)
    permission = [True]
    cancelled = [False]
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    build = loaded(rows, grant_valid=lambda: permission[0], cancelled=lambda: cancelled[0])
    assert build.step()['state'] == 'pending'
    operation = build._operation
    permission[0] = False
    before = dict(build.work.counts)
    assert build.step()['state'] == 'paused'
    assert build.work.counts == before and build._operation is operation
    permission[0] = True
    assert finish(build)['state'] == 'ready'
    assert annotations(build)[0]['body']['status'] == 'provisional_linked'
    cancelled[0] = True
    with pytest.raises(subject.Pending, match='cancelled'):
        build.output_page()
    assert build.state == 'cancelled'


def test_deadline_and_memory_refuse_without_partial_current_output():
    now = [0.]
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    build = loaded(rows, clock=lambda: now[0], deadline=1.)
    now[0] = 2.
    assert build.step()['reason'] == 'scope_deadline'
    with pytest.raises(subject.Pending):
        build.output_page()
    limited = loaded(rows, admitted_bytes=2 * 1024 * 1024)
    # The forecast must fit the explicitly granted amount, without a node cap.
    limited.admitted_bytes = limited._loaded_bytes + 1
    status = finish(limited)
    assert status['state'] == 'refused' and status['reason'] == 'comparison_memory'


def test_exact_page_coverage_and_snapshot_binding():
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    snapshot = token(rows)
    build = subject.ComparisonBuild(snapshot, admitted_bytes=subject.MAX_MEMORY_BYTES)
    with pytest.raises(subject.Invalid, match='stale_page'):
        build.feed_observations(token(rows, revision=2), 0, rows, complete=True)
    with pytest.raises(subject.Invalid, match='incomplete_scan'):
        build.feed_observations(snapshot, 0, rows[:2], complete=True)
    build.feed_observations(snapshot, 0, rows[:2], complete=False)
    assert build.step()['state'] == 'loading'
    build.feed_observations(snapshot, 2, rows[2:], complete=True)
    assert build.step()['state'] == 'loading'
    build.feed_witnesses(snapshot, 0, [], complete=True)
    assert finish(build)['state'] == 'ready'
    assert build.decision_counts['annotation'] == 3


def test_duplicate_witnesses_count_for_coverage_but_not_extra_constraints():
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    witness = {'native_scope_id': 'native', 'labels': ('label-0', 'label-1'), 'proof_sha256': 'd' * 64}
    build = loaded(rows, [witness, witness])
    assert len(build.witnesses) == 1
    assert finish(build)['loaded_witnesses'] == 2
    assert all(row['body']['status'] == 'source_scoped' for row in annotations(build))


def test_cancelled_partial_score_tail_is_not_trusted_on_rebase(monkeypatch):
    monkeypatch.setattr(subject, 'QUANTUM_DOTS', 1)
    cancelled = [False]
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    first = loaded(rows, cancelled=lambda: cancelled[0], clock=lambda: 0.)
    assert first.step()['state'] == 'pending'
    prefix = first.score_prefix
    assert len(prefix.scores) == 1 and prefix.vector_count == 1
    cancelled[0] = True
    assert first.step()['state'] == 'cancelled'
    first.discard()
    rebased = loaded(rows, revision=2, prefix=prefix)
    assert len(prefix.scores) == 0
    assert finish(rebased)['state'] == 'ready'
    assert rebased.work.counts['dot_products'] == 3
    assert annotations(rebased)[0]['body']['status'] == 'provisional_linked'


@pytest.mark.parametrize('dot,interval', [(True, 16), (False, 64)])
def test_cancel_check_frequency(dot, interval):
    cancelled = [False]
    work = subject.Work(clock=lambda: 0., cancelled=lambda: cancelled[0], deadline=1.)
    cancelled[0] = True
    for _ in range(interval - 1):
        assert work.tick('synthetic_checkpoint', dot=dot) is False
    with pytest.raises(RuntimeError, match='cancelled'):
        work.tick('synthetic_checkpoint', dot=dot)
