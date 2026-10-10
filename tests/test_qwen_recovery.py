"""Synthetic contracts only: no model execution, quality or native proof."""
import copy
from dataclasses import replace
import time

import pytest
from test_qwen_candidates import media, one_request, raw, plan_for, ARGS
from audio_backend.qwen_candidates import normalize_native, candidate_windows, make_candidate_request
from audio_backend.fragment_runtime import LogicalPCMReader
from audio_backend.qwen_recovery import (
    CallBudget, assemble, child_pcm, eligible, execute, integer_halves, prepare, validate_plan,
)


def fixture(media):
    _, _, pcm, request = one_request(media)
    native = normalize_native(raw(native_status=18, native_status_name='OUTPUT_TRUNCATED', was_truncated=True))
    plan = prepare(request, native, pcm, enabled=True, invocation_id='c'*64, parent_session_id='d'*64)
    return request, native, pcm, plan


def receipts(plan):
    return [dict(request=copy.deepcopy(child), native=normalize_native(raw(text=text)),
                 session_id=str(i+1)*64, cache_used=False, history_used=False,
                 source_verified=True, runtime_verified=True)
            for i, (child, text) in enumerate(zip(plan['children'], [' A ', ' B ']))]


def synthetic_verifier(request, pcm, receipt):
    # Synthetic executor only. A real worker must rehash independently pinned
    # runtime/model files and observe actual session creation and input dispatch.
    assert request['pcm16_sha256'] == pcm.pcm_sha256
    assert request == receipt['request']
    return True


def result(plan, pcm, rows):
    return assemble(plan, pcm, rows, verify_material=synthetic_verifier)


def test_optin_exact_pair_and_immutability(media):
    req, native, pcm, plan = fixture(media)
    before = copy.deepcopy((req, native, plan))
    assert prepare(req, native, pcm) is None
    children = [child_pcm(pcm, **dict(start=c['read']['start_frame'], end=c['read']['end_frame'])) for c in plan['children']]
    assert children[0].pcm + children[1].pcm == pcm.pcm
    assert [len(c.pcm)//2 for c in children] == [160000, 160000]
    rows = receipts(plan)
    evidence = result(plan, pcm, rows)
    assert evidence['replacement_text'] == ' A  B '
    assert evidence['original_status'] == 'output_truncated'
    assert evidence['word_timestamps'] is evidence['speaker_id'] is None
    assert evidence['profile_update_eligible'] is False
    rows[0]['native']['raw_text'] = 'changed'
    assert evidence['children'][0]['native']['raw_text'] != 'changed'
    assert (req, native, plan) == before


@pytest.mark.parametrize('updates', [
    {'native_status': 0, 'native_status_name': 'OK'},
    {'native_status_name': 'OK'},
    {'was_truncated': False}, {'was_truncated': None, 'truncation_available': False},
    {'execution_returned': False}, {'exception_status': 13},
])
def test_false_eligibility(media, updates):
    req, _, _, _ = fixture(media)
    value = raw(native_status=18, native_status_name='OUTPUT_TRUNCATED', was_truncated=True)
    value.update(updates)
    assert not eligible(req, normalize_native(value))


def test_short_tail_unsupported_and_odd_arithmetic(media):
    _, paths, sources = media
    plan = plan_for([sources['whole']])
    window = list(candidate_windows(plan))[-1]
    pcm = LogicalPCMReader(plan, paths).read(window.read_start, window.read_end)
    req = make_candidate_request(plan, window, pcm, **ARGS)
    native = fixture(media)[1]
    assert not eligible(req, native)
    assert prepare(req, native, pcm, enabled=True) is None
    assert integer_halves(100, 105) == ((100, 102), (102, 105))
    for start, end in [(0, 0), (0, 1), (True, 4), (-1, 4)]:
        with pytest.raises(ValueError): integer_halves(start, end)


@pytest.mark.parametrize('mutation', ['failed_second', 'truncated', 'reorder', 'missing',
    'cache', 'history', 'stale', 'session_reuse', 'parent_session', 'runtime', 'source', 'model',
    'repetition', 'raw_disagreement'])
def test_atomic_failure_preserves_raw(media, mutation):
    _, _, pcm, plan = fixture(media)
    rows = receipts(plan)
    if mutation == 'failed_second': rows[1]['native'] = normalize_native(raw(native_status=13, native_status_name='ABORTED'))
    elif mutation == 'truncated': rows[1]['native'] = fixture(media)[1]
    elif mutation == 'reorder': rows.reverse()
    elif mutation == 'missing': rows.pop()
    elif mutation == 'cache': rows[1]['cache_used'] = True
    elif mutation == 'history': rows[1]['history_used'] = True
    elif mutation == 'stale': rows[1]['request']['invocation_id'] = 'e'*64
    elif mutation == 'session_reuse': rows[1]['session_id'] = rows[0]['session_id']
    elif mutation == 'parent_session': rows[1]['session_id'] = plan['parent_session_id']
    elif mutation == 'runtime': rows[1]['runtime_verified'] = False
    elif mutation == 'source': rows[1]['source_verified'] = False
    elif mutation == 'model': rows[1]['request']['model_sha256'] = 'f'*64
    elif mutation == 'repetition': rows[1]['native']['repetition_flag'] = True
    else: rows[1]['native']['complete'] = False
    original = copy.deepcopy(rows)
    out = result(plan, pcm, rows)
    assert out['recovery_status'] == 'unresolved' and out['replacement_text'] is None
    assert out['children'] == original and out['plan']['parent'] == plan['parent']


def test_material_verifier_mandatory_and_failure_closed(media):
    _, _, pcm, plan = fixture(media)
    with pytest.raises(ValueError): assemble(plan, pcm, receipts(plan), verify_material=None)
    def reject(*args): raise ValueError('Actual material changed')
    assert assemble(plan, pcm, receipts(plan), verify_material=reject)['replacement_text'] is None


def test_mapping_across_original_sources_and_corruption(media):
    _, paths, sources = media
    source_plan = plan_for([sources['left'], sources['right']])
    window = next(candidate_windows(source_plan))
    pcm = LogicalPCMReader(source_plan, paths).read(window.read_start, window.read_end)
    req = make_candidate_request(source_plan, window, pcm, **ARGS)
    plan = prepare(req, fixture(media)[1], pcm, enabled=True, parent_session_id='d'*64)
    right = plan['children'][1]['source_mapping']
    assert [(p['span']['logical_start'], p['span']['logical_end']) for p in right] == [(160000,160001), (160001,320000)]
    assert right[1]['span']['source_start'] == 0
    with pytest.raises(ValueError): validate_plan(plan, replace(pcm, pcm=b'\0'*len(pcm.pcm)))
    plan['children'][1]['config']['generation_cap'] = 512
    with pytest.raises(ValueError): validate_plan(plan, pcm)


def test_budget_retry_deadline_and_failure_retention(media):
    _, _, pcm, plan = fixture(media)
    rows = receipts(plan)
    calls = []
    def invoke(req, child):
        calls.append(req)
        if len(calls) == 2: raise RuntimeError('Synthetic executor stopped')
        return rows[0]
    budget = CallBudget(3, 1, time.monotonic()+60)
    out = execute(plan, pcm, budget=budget, invoke=invoke, check_resources=lambda: None,
                  verify_material=synthetic_verifier)
    assert len(calls) == 2 and budget.used == 3
    assert out['children'] == [rows[0]] and out['replacement_text'] is None
    assert out['controller_failure_type'] == 'RuntimeError'
    with pytest.raises(ValueError):
        execute(plan, pcm, budget=budget, invoke=invoke, check_resources=lambda: None,
                verify_material=synthetic_verifier)
    for total in [1, 2]:
        with pytest.raises(ValueError): CallBudget(total, 1, time.monotonic()+60).reserve_pair('parent')
    with pytest.raises(TimeoutError): CallBudget(3, 1, time.monotonic()-1).reserve_pair('parent')
    with pytest.raises(ValueError): CallBudget(True, 1, time.monotonic()+60)


def test_canonical_exception_status_and_verifier_return(media):
    req, native, pcm, plan = fixture(media)
    value = raw(native_status=18, native_status_name=None, was_truncated=True,
                exception_type='TranscribeError', exception_status=18, partial_result_available=True)
    assert eligible(req, normalize_native(value))
    for value in [None, False, 1, 'true']:
        assert assemble(plan, pcm, receipts(plan), verify_material=lambda *args: value)['replacement_text'] is None


def test_postcall_failure_keeps_raw(media):
    _, _, pcm, plan = fixture(media)
    rows = receipts(plan)
    budget = CallBudget(3, 1, time.monotonic()+60)
    def invoke(*args):
        budget.deadline = time.monotonic()-1
        return rows[0]
    out = execute(plan, pcm, budget=budget, invoke=invoke, check_resources=lambda: None,
                  verify_material=synthetic_verifier)
    assert out['children'] == [rows[0]] and out['replacement_text'] is None


@pytest.mark.parametrize('mutation', ['lost_frame', 'extra_field', 'depth', 'runtime', 'hash'])
def test_plan_tampering(media, mutation):
    _, _, pcm, plan = fixture(media)
    child = plan['children'][0]
    if mutation == 'lost_frame': child['read']['end_frame'] -= 1
    elif mutation == 'extra_field': child['word_timestamps'] = []
    elif mutation == 'depth': plan['maximum_depth'] = 2
    elif mutation == 'runtime': child['runtime_manifest_sha256'] = 'e'*64
    else: child['pcm16_sha256'] = 'e'*64
    with pytest.raises(ValueError): validate_plan(plan, pcm)


def test_callback_mutations_cannot_change_evidence(media):
    _, _, pcm, plan = fixture(media)
    rows = receipts(plan)
    original = copy.deepcopy(plan)
    def verify(req, child, receipt):
        plan['parent']['native']['text'] = 'mutated'
        req['config']['generation_cap'] = 1024
        receipt['native']['text'] = 'mutated'
        return True
    evidence = assemble(plan, pcm, rows, verify_material=verify)
    assert evidence['plan'] == original and evidence['replacement_text'] == ' A  B '


def test_verification_cannot_extend_deadline(media):
    _, _, pcm, plan = fixture(media)
    rows = receipts(plan)
    budget = CallBudget(3, 1, time.monotonic()+60)
    def verify(*args):
        budget.deadline = time.monotonic()-1
        return True
    out = execute(plan, pcm, budget=budget, invoke=lambda req, child: rows[req['index']],
                  check_resources=lambda: None, verify_material=verify)
    assert out['children'] == rows and out['replacement_text'] is None
    assert out['controller_failure_type'] == 'TimeoutError'
