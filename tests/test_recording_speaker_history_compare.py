"""Source-only candidate fixtures; synthetic bytes, no model/audio invocation."""
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
    row = {'sequence': index + 1, 'certificate_id': f'certificate-{index}',
        'node_id': f'node-{index:04}', 'origin_kind': 'primary_commit',
        'source_key': f'source-{index}', 'pcm_hash': f'pcm-{index}',
        'float_hash': f'float-{index}', 'vector': vector(*values),
        'attributions': (('native', f'label-{index}'),),
        'spans': (('source-version', 10000, index * 100, index * 100 + 10),),
        'refs': (('primary', f'primary-ref-{index}'),)}
    row.update(changes)
    return row


def token(rows, witnesses=(), revision=1, authority=1):
    return subject.SnapshotToken('a' * 64, 'primary-clean-history-v1', 'b' * 64,
                                 revision, authority, len(rows), len(witnesses))


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
    changed = dict(rows[0], vector=vector(0., 1.))
    with pytest.raises(subject.Invalid, match='score_prefix_row_mismatch'):
        loaded([changed] + rows[1:], revision=2, prefix=build.score_prefix)


def test_late_metadata_alias_preserves_overlap_and_no_extra_vote():
    rows = [observation(0), observation(1, (4., 3.)), observation(2, (0., 0., 1.))]
    alias = dict(rows[0], sequence=4, certificate_id='alias', node_id='z-alias', vector=None,
        source_key='different-source-alias', spans=rows[2]['spans'], origin_kind='moss_result', refs=(('moss', 'alias-ref'),))
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
    two = dict(one, sequence=2, certificate_id='two', node_id='two', attributions=(('native', 'other'),))
    witness = {'native_scope_id': 'native', 'labels': ('label-0', 'other'), 'proof_sha256': 'c' * 64}
    build = loaded([one, two], [witness])
    status = finish(build)
    assert status['state'] == 'refused' and status['reason'] == 'duplicate_native_conflict'
    with pytest.raises(subject.Pending):
        build.output_page()


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
