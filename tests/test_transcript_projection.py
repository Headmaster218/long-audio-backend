"""Synthetic projection contracts for fixed coordinate-only selection.

Only coordinates and synthetic labels are used. No audio, gold transcript,
model, native runtime, decoder, download, worker, or external service is used.
"""
from copy import deepcopy
import hashlib
import time
from itertools import permutations

import pytest

from audio_backend import transcript_projection as projection, transcript_contract as contract
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.transcript_contract import BLOCK_FRAMES, MAX_PRIMARY, MAX_RESULTS, RATE


def primary(key, start, end, text=None, speaker='local'):
    return {
        'source_ref': f'primary:{key}', 'source': 'primary',
        'home_block': start // BLOCK_FRAMES,
        'start_frame': start, 'end_frame': end,
        'text': key if text is None else text, 'raw_text': key if text is None else text,
        'speaker_scope': [f'chunk:{key}', speaker] if speaker is not None else None,
        'timestamp_kind': 'native_unit', 'word_times': None,
    }


def candidate(key, start, end, *, kind='ordinary', archive=1, text=None,
              phrase_specs=None, result_hash=None):
    digest = result_hash or hashlib.sha256(key.encode()).hexdigest()
    job_id = f'job:{key}'
    if phrase_specs is None:
        phrase_specs = [(key if text is None else text, start, end, 'local')]
    phrases = []
    for index, (value, phrase_start, phrase_end, label) in enumerate(phrase_specs):
        phrases.append({
            'source_ref': f'moss:{key}:{index}', 'source': 'moss',
            'home_block': start // BLOCK_FRAMES, 'text': value, 'raw_text': value,
            'start': (phrase_start - start) / RATE, 'end': (phrase_end - start) / RATE,
            'session_start': phrase_start / RATE, 'session_end': phrase_end / RATE,
            'speaker_scope': [job_id, digest, label], 'word_times': None,
            'timestamp_quality': 'model_generated_unverified',
            'complete_content': False, 'eos': None,
        })
    return {
        'candidate_key': key, 'job_id': job_id, 'result_sha256': digest,
        'archive_seq': archive, 'kind': kind, 'start_frame': start, 'end_frame': end,
        'home_block': start // BLOCK_FRAMES, 'phrases': phrases,
        'complete_content': False, 'eos': None,
    }


def selected(result):
    return [key for run in result['selected_runs'] for key in run['candidate_keys']]


def texts(items):
    return [item['text'] for item in items if not item['reference_only']]


def alternatives_for(result, key):
    return [group for group in result['alternatives'] if key in group['candidate_keys']]


def test_adjacent_ordinary_union_covers_straddling_whole_primary_unit():
    original = [primary('left', 0, 160000), primary('straddles', 160000, 320000),
                primary('right', 320000, 336000), primary('untouched', 336000, 400000)]
    jobs = [candidate('first', 0, 168000), candidate('second', 168000, 336000)]
    result = projection.project(0, original, jobs)
    assert selected(result) == ['first', 'second']
    assert len(result['selected_runs']) == 1
    assert result['selected_runs'][0]['start_frame'] == 0
    assert result['selected_runs'][0]['end_frame'] == 336000
    assert texts(result['reading']) == ['first', 'second', 'untouched']
    assert texts(result['baseline_reading']) == ['left', 'straddles', 'right', 'untouched']
    group = result['alternatives'][0]
    assert group['reason'] == 'replaced_primary'
    assert texts(group['primary_reading']) == ['left', 'straddles', 'right']
    assert texts(group['candidate_reading']) == ['first', 'second']


def test_seam_partial_outer_units_remain_primary_with_full_candidate_lane():
    original = [primary('left', 0, 200), primary('right', 200, 400)]
    job = candidate('seam', 100, 300, kind='core_seam')
    result = projection.project(0, original, [job])
    assert selected(result) == []
    assert texts(result['reading']) == ['left', 'right']
    group = result['alternatives'][0]
    assert group['reason'] == 'partial_primary_unresolved'
    assert texts(group['candidate_reading']) == ['seam']
    assert texts(group['primary_reading']) == ['left', 'right']
    assert group['supports'][0]['start_frame'] == 100
    assert group['supports'][0]['end_frame'] == 300


@pytest.mark.parametrize('kind', ['core_seam', 'reset_seam'])
def test_seam_replaces_when_all_intersecting_whole_units_are_contained(kind):
    original = [primary('left', 100, 200), primary('right', 200, 300)]
    result = projection.project(0, original, [candidate('seam', 0, 400, kind=kind)])
    assert selected(result) == ['seam']
    assert texts(result['reading']) == ['seam']
    assert texts(result['alternatives'][0]['primary_reading']) == ['left', 'right']


def test_maximum_support_beats_smaller_overlapping_seam():
    jobs = [candidate('left', 0, 100), candidate('right', 100, 200),
            candidate('seam', 50, 150, kind='core_seam')]
    result = projection.project(0, [], jobs)
    assert selected(result) == ['left', 'right']
    rejected = alternatives_for(result, 'seam')[0]
    assert rejected['reason'] == 'unselected_overlap'
    assert rejected['selected_candidate_keys'] == ['left', 'right']
    assert texts(rejected['candidate_reading']) == ['seam']


def test_fewer_jobs_precedes_seam_tie_preference():
    jobs = [candidate('one', 0, 200), candidate('left', 0, 100, kind='core_seam'),
            candidate('right', 100, 200, kind='reset_seam')]
    assert selected(projection.project(0, [], jobs)) == ['one']


@pytest.mark.parametrize('kind', ['core_seam', 'reset_seam'])
def test_same_support_prefers_seam_before_archive_order(kind):
    jobs = [candidate('ordinary', 0, 200, archive=1),
            candidate('seam', 0, 200, kind=kind, archive=100)]
    assert selected(projection.project(0, [], jobs)) == ['seam']


def test_tie_order_prefers_start_then_archive_then_hash():
    assert selected(projection.project(0, [], [candidate('late', 50, 150, archive=1),
                                              candidate('early', 0, 100, archive=9)])) == ['early']
    assert selected(projection.project(0, [], [candidate('new', 0, 100, archive=9),
                                              candidate('old', 0, 100, archive=1)])) == ['old']
    assert selected(projection.project(0, [], [candidate('hash-z', 0, 100, result_hash='f' * 64),
                                              candidate('hash-a', 0, 100, result_hash='0' * 64)])) == ['hash-a']


def test_core_and_reset_are_same_seam_tier_with_stable_archive_tie():
    jobs = [candidate('reset', 0, 100, kind='reset_seam', archive=2),
            candidate('core', 0, 100, kind='core_seam', archive=1)]
    assert selected(projection.project(0, [], jobs)) == ['core']


def test_equal_text_simultaneous_labels_and_intrinsic_repeats_are_preserved():
    specs = [('same', 0, 20, 'A'), ('same', 0, 20, 'B'),
             ('same', 0, 20, 'A'), ('same', 30, 40, 'A')]
    job = candidate('repeat', 0, 100, phrase_specs=specs)
    result = projection.project(0, [primary('original', 0, 100)], [job])
    assert texts(result['reading']) == ['same'] * 4
    assert [item['source_ref'] for item in result['reading']] == [p['source_ref'] for p in job['phrases']]
    assert [item['speaker_scope'][-1] for item in result['reading']] == ['A', 'B', 'A', 'A']
    assert [item['session_start'] for item in result['reading']] == [0, 0, 0, 30 / RATE]
    assert all(item['word_times'] is None and item['complete_content'] is False
               and item['eos'] is None and item['timestamp_quality'] == 'model_generated_unverified'
               for item in result['reading'])


def test_anonymous_local_labels_stay_scoped_to_each_job():
    jobs = [candidate('left', 0, 100, phrase_specs=[('same', 0, 100, 'anonymous')]),
            candidate('right', 100, 200, phrase_specs=[('same', 100, 200, 'anonymous')])]
    result = projection.project(0, [], jobs)
    assert texts(result['reading']) == ['same', 'same']
    scopes = [item['speaker_scope'] for item in result['reading']]
    assert scopes[0][-1] == scopes[1][-1] == 'anonymous'
    assert scopes[0][:2] != scopes[1][:2]


def test_primary_simultaneous_and_equal_text_units_are_not_deduplicated():
    original = [primary('A', 0, 100, text='same'), primary('B', 0, 100, text='same'),
                primary('C', 100, 200, text='same', speaker=None)]
    result = projection.project(0, original, [])
    assert texts(result['reading']) == ['same'] * 3
    assert len({item['source_ref'] for item in result['reading']}) == 3
    assert result['reading'][-1]['speaker_scope'] is None


def test_block_crossing_candidate_is_unsliced_at_home_and_ref_only_elsewhere():
    edge = BLOCK_FRAMES
    original = [primary('crossing-primary', edge - 150, edge + 150)]
    job = candidate('crossing', edge - 100, edge + 100, kind='core_seam',
                    phrase_specs=[('before', edge - 75, edge - 25, 'A'),
                                  ('after', edge + 25, edge + 75, 'B')])
    home = projection.project(0, original, [job])
    following = projection.project(1, original, [job])
    assert selected(home) == selected(following) == []
    assert texts(home['reading']) == ['crossing-primary']
    assert texts(following['reading']) == []
    assert following['reading'][0]['reference_only'] is True
    assert following['baseline_reading'][0]['reference_only'] is True
    for result in (home, following):
        group = result['alternatives'][0]
        assert group['reason'] == 'boundary_unresolved'
        assert group['supports'][0]['start_frame'] == edge - 100
        assert group['supports'][0]['end_frame'] == edge + 100
        assert len(group['candidate_reading']) == 2
    lane = home['alternatives'][0]['candidate_reading']
    assert texts(lane) == ['before', 'after']
    assert lane[1]['session_start'] == (edge + 25) / RATE
    assert lane[1]['home_block'] == 0
    for item in following['alternatives'][0]['candidate_reading']:
        assert item['reference_only'] is True
        assert 'text' not in item and 'raw_text' not in item and 'speaker_scope' not in item
        assert item['home_block'] == 0


def test_exact_block_edges_have_one_owner_and_no_endpoint_overlap():
    edge = BLOCK_FRAMES
    original = [primary('before', edge - 100, edge), primary('after', edge, edge + 100)]
    jobs = [candidate('left', edge - 100, edge), candidate('right', edge, edge + 100)]
    left = projection.project(0, original, jobs)
    right = projection.project(1, original, jobs)
    assert selected(left) == ['left']
    assert selected(right) == ['right']
    assert texts(left['reading']) == ['left']
    assert texts(right['reading']) == ['right']
    assert all(item['home_block'] == 0 for item in left['reading'])
    assert all(item['home_block'] == 1 for item in right['reading'])


def test_boundary_unresolved_does_not_expand_overlap_component():
    original = [primary('ordinary-unit', 0, 300)]
    jobs = [candidate('ordinary', 0, 300),
            candidate('boundary-seam', 250, BLOCK_FRAMES + 50, kind='core_seam')]
    result = projection.project(0, original, jobs)
    assert selected(result) == ['ordinary']
    assert texts(result['reading']) == ['ordinary']
    boundary = alternatives_for(result, 'boundary-seam')[0]
    assert boundary['reason'] == 'boundary_unresolved'
    assert boundary['selected_candidate_keys'] == ['ordinary']
    assert texts(boundary['candidate_reading']) == ['boundary-seam']
    assert texts(boundary['primary_reading']) == ['ordinary-unit']


def test_nonadjacent_jobs_cannot_bridge_an_uncovered_primary_gap():
    original = [primary('whole', 0, 300)]
    jobs = [candidate('left', 0, 100), candidate('right', 200, 300)]
    result = projection.project(0, original, jobs)
    assert selected(result) == []
    assert texts(result['reading']) == ['whole']
    assert len(result['alternatives']) == 2


def test_separate_legal_runs_keep_intervening_primary_text():
    original = [primary('left-unit', 0, 100), primary('keep', 100, 200), primary('right-unit', 200, 300)]
    jobs = [candidate('left', 0, 100), candidate('right', 200, 300)]
    result = projection.project(0, original, jobs)
    assert len(result['selected_runs']) == 2
    assert texts(result['reading']) == ['left', 'keep', 'right']
    assert {item['text'] for group in result['alternatives'] for item in group['primary_reading']} == {'left-unit', 'right-unit'}


def test_overlapping_primary_units_all_must_fit():
    original = [primary('A', 0, 100), primary('B', 50, 150)]
    assert selected(projection.project(0, original, [candidate('partial', 0, 100)])) == []
    result = projection.project(0, original, [candidate('whole', 0, 150)])
    assert selected(result) == ['whole']
    assert texts(result['alternatives'][0]['primary_reading']) == ['A', 'B']


@pytest.mark.parametrize('primary_count', [1, MAX_PRIMARY])
def test_sixteen_candidate_chain_has_bounded_dp_work_and_runtime(monkeypatch, primary_count):
    assert MAX_RESULTS == 16
    jobs = [candidate(f'job-{index}', index * 100, (index + 1) * 100) for index in range(MAX_RESULTS)]
    original = [primary(f'whole-{index}', 0, MAX_RESULTS * 100) for index in range(primary_count)]
    legal, calls = projection._legal, []
    def counted(start, end, units):
        calls.append((start, end))
        return legal(start, end, units)
    monkeypatch.setattr(projection, '_legal', counted)
    started = time.process_time()
    result = projection.project(0, original, jobs)
    assert time.process_time() - started < .75
    # Exactly one reachable path per ordered endpoint pair: no subset search.
    assert len(calls) == MAX_RESULTS * (MAX_RESULTS + 1) // 2 == 136
    assert len(set(calls)) == len(calls)
    assert selected(result) == [job['candidate_key'] for job in jobs]
    assert len(result['selected_runs']) == 1
    assert len(result['reading']) == MAX_RESULTS
    assert len(result['alternatives'][0]['primary_reading']) == primary_count
    assert result['selected_runs'][0]['end_frame'] == MAX_RESULTS * 100
    assert len(contract.response(result)) <= contract.MAX_RESPONSE_BYTES


def test_maximum_primary_capacity_retains_every_unit():
    original = [primary(str(index), index * 2, index * 2 + 1) for index in range(MAX_PRIMARY)]
    result = projection.project(0, original, [])
    assert len(result['reading']) == MAX_PRIMARY


def test_dense_same_support_results_keep_all_linked_alternatives_at_bounds():
    unit_count = contract.MAX_BINDINGS // MAX_RESULTS
    original = [primary(str(index), 0, 100) for index in range(unit_count)]
    jobs = [candidate(f'job-{index}', 0, 100, archive=index) for index in range(MAX_RESULTS)]
    result = projection.project(0, original, jobs)
    assert selected(result) == ['job-0']
    assert len(result['alternatives']) == MAX_RESULTS
    assert sum(len(group['primary_reading']) for group in result['alternatives']) == MAX_RESULTS * unit_count == contract.MAX_BINDINGS
    assert all(len(group['candidate_reading']) == 1 for group in result['alternatives'])
    assert all(group['selected_candidate_keys'] == ['job-0'] for group in result['alternatives'])


def test_sixteen_by_128_overlap_links_fail_explicitly_without_truncation():
    original = [primary(str(index), 0, 100) for index in range(MAX_PRIMARY)]
    jobs = [candidate(f'job-{index}', 0, 100, archive=index) for index in range(MAX_RESULTS)]
    assert MAX_RESULTS * MAX_PRIMARY > contract.MAX_BINDINGS == 1024
    with pytest.raises(ReadError, match='transcript_projection_binding_capacity'):
        projection.project(0, original, jobs)


def test_primary_capacity_fails_closed():
    original = [primary(str(index), index * 2, index * 2 + 1) for index in range(MAX_PRIMARY + 1)]
    with pytest.raises(ReadError):
        projection.project(0, original, [])


def test_result_capacity_fails_closed():
    jobs = [candidate(str(index), index * 2, index * 2 + 1) for index in range(MAX_RESULTS + 1)]
    with pytest.raises(ReadError):
        projection.project(0, [], jobs)


def test_binding_capacity_fails_closed(monkeypatch):
    monkeypatch.setattr(projection, 'MAX_BINDINGS', 0)
    with pytest.raises(ReadError):
        projection.project(0, [primary('unit', 0, 100)], [candidate('replacement', 0, 100)])


@pytest.mark.parametrize('reason', ['parse_error', 'source_cancelled'])
def test_ineligible_candidate_keeps_complete_lane_without_replacing_primary(reason):
    job = candidate('ineligible', 0, 100)
    job.update(eligible=False, ineligible_reason=reason)
    result = projection.project(0, [primary('unit', 0, 100)], [job])
    assert selected(result) == []
    assert texts(result['reading']) == texts(result['baseline_reading']) == ['unit']
    assert texts(result['alternatives'][0]['candidate_reading']) == ['ineligible']
    assert result['alternatives'][0]['reason'] == reason
    assert result['alternatives'][0]['supports'][0]['eligible'] is False


def test_empty_candidate_lane_cannot_remove_primary_even_without_explicit_eligibility():
    job = candidate('empty', 0, 100, phrase_specs=[])
    result = projection.project(0, [primary('unit', 0, 100)], [job])
    assert selected(result) == []
    assert texts(result['reading']) == ['unit']
    assert result['alternatives'][0]['reason'] == 'empty_candidate_reading'
    assert result['alternatives'][0]['candidate_reading'] == []


def test_ineligible_boundary_keeps_result_detail_link_and_both_limitations():
    job = candidate('untimed', BLOCK_FRAMES - 50, BLOCK_FRAMES + 50, phrase_specs=[])
    job.update(eligible=False, ineligible_reason='no_timed_phrases',
               result_ref='m.synthetic-job.synthetic-hash.all',
               authority='historical_snapshot', source_current='not_checked')
    result = projection.project(0, [], [job])
    group = result['alternatives'][0]
    assert group['reason'] == 'no_timed_phrases'
    assert group['boundary_unresolved'] is True
    assert group['supports'][0]['result_ref'] == job['result_ref']
    assert group['supports'][0]['authority'] == 'historical_snapshot'
    assert group['supports'][0]['source_current'] == 'not_checked'


@pytest.mark.parametrize('block', [-1, True, 1.5])
def test_invalid_block_fails_closed(block):
    with pytest.raises(ReadError):
        projection.project(block, [], [])


@pytest.mark.parametrize('start,end', [(0, 0), (2, 1), (-1, 1), (True, 2), (0, 1.5)])
def test_nonpositive_or_noninteger_support_cannot_create_graph_cycles(start, end):
    job = candidate('valid-before-damage', 0, 100)
    job['start_frame'], job['end_frame'] = start, end
    with pytest.raises(ReadError):
        projection.project(0, [], [job])


def test_input_permutation_cannot_change_projection():
    original = [primary('A', 0, 100), primary('B', 100, 200)]
    jobs = [candidate('left', 0, 100), candidate('right', 100, 200), candidate('seam', 50, 150, kind='core_seam')]
    expected = projection.project(0, original, jobs)
    for order in permutations(jobs):
        assert projection.project(0, list(reversed(original)), list(order)) == expected


def test_text_and_model_timestamps_never_choose_the_winner():
    original = [primary('unit', 0, 200)]
    jobs = [candidate('ordinary', 0, 200), candidate('seam', 0, 200, kind='core_seam')]
    before = projection.project(0, original, jobs)
    changed = deepcopy(jobs)
    changed[0]['phrases'][0]['text'] = changed[1]['phrases'][0]['text'] = 'identical repeated repeated'
    changed[1]['phrases'][0]['session_start'] = 0.009
    changed[1]['phrases'][0]['session_end'] = 0.010
    after = projection.project(0, original, changed)
    assert before['selected_runs'] == after['selected_runs']
    assert selected(after) == ['seam']


def test_projection_does_not_mutate_or_alias_input_evidence_or_notices():
    original = [primary('unit', 0, 100)]
    jobs = [candidate('replacement', 0, 100)]
    notices = [{'code': 'synthetic', 'details': ['keep']}]
    snapshot = deepcopy((original, jobs, notices))
    result = projection.project(0, original, jobs, notices)
    assert (original, jobs, notices) == snapshot
    result['reading'][0]['speaker_scope'][0] = 'changed-output'
    result['alternatives'][0]['primary_reading'][0]['speaker_scope'][0] = 'changed-output'
    result['notices'][0]['details'].append('changed-output')
    assert (original, jobs, notices) == snapshot


def test_empty_block_and_notices_have_explicit_fixed_bounds():
    notices = [{'code': 'not-yet-available'}]
    result = projection.project(2, [], [], notices)
    assert result['block'] == 2
    assert result['start_frame'] == 2 * BLOCK_FRAMES
    assert result['end_frame'] == 3 * BLOCK_FRAMES
    assert result['sample_rate'] == RATE == 16000
    assert result['policy_hash'] == projection.POLICY_HASH
    assert result['reading'] == result['selected_runs'] == result['alternatives'] == []
    assert result['notices'] == notices
