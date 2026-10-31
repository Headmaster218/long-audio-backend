"""Experimental, opt-in one-level Qwen recovery; no native or production imports.

Trusted executors must enforce process resource/deadline limits and independently
observe fresh session/cache receipts. JSON hashes bind evidence, not truth of a
native executor's claims. This module never authorizes or launches model work.
"""
import hashlib
import math
import secrets
import time

from .fragment_runtime_contracts import PCMWindow, bounded_json, canonical, digest, integer
from .qwen_candidates import (
    CORE_FRAMES, _copy, _hash, _object, _SAFETY, _validate_pcm,
    validate_candidate_input, validate_candidate_request, validate_native_result,
)

SCHEMA = 'qwen-bounded-split-v1'
CHILD_FRAMES = CORE_FRAMES // 2


def integer_halves(start, end):
    """Pure arithmetic helper; the admitted execution policy is narrower."""
    integer(start, 'start'); integer(end, 'end')
    if end - start < 2:
        raise ValueError('Two nonempty children required')
    middle = start + (end - start) // 2
    return ((start, middle), (middle, end))


def eligible(request, native):
    validate_candidate_request(request)
    validate_native_result(native)
    return (request['float32_count'] == CORE_FRAMES and
            native['native_status'] == 18 and
            native['native_status_canonical_name'] == 'OUTPUT_TRUNCATED' and
            native['numeric_status_contract'] == 'transcribe.cpp-0.2.3' and
            native['was_truncated'] is True and native['truncation_available'] is True and
            native['execution_returned'] is True and not native['issues'] and
            native['status'] == 'output_truncated')


def child_pcm(parent, start, end):
    """Slice verified parent samples and original-source spans without rereads."""
    _validate_pcm(parent)
    if not parent.start <= start < end <= parent.end:
        raise ValueError('Child outside parent support')
    mapping = []
    for part in parent.mapping:
        span = part['span']
        lo, hi = max(start, span['logical_start']), min(end, span['logical_end'])
        if lo < hi:
            value = _copy(part)
            value['span'].update(logical_start=lo, logical_end=hi,
                source_start=span['source_start'] + lo - span['logical_start'],
                source_end=span['source_start'] + hi - span['logical_start'])
            mapping.append(value)
    data = parent.pcm[2*(start-parent.start):2*(end-parent.start)]
    return PCMWindow(start, end, data, mapping, digest(mapping), hashlib.sha256(data).hexdigest())


def prepare(request, native, pcm, *, enabled=False, invocation_id=None, parent_session_id=None):
    """Plan exactly two children, or return None. Never perform inference.

    invocation_id is a fresh executor-issued 256-bit attempt nonce, not a cache
    key. Persist the resulting plan before executing. Do not reuse an attempt.
    """
    if type(enabled) is not bool:
        raise ValueError('Explicit boolean opt-in required')
    validate_candidate_input(request, pcm)
    validate_native_result(native)
    if not enabled or not eligible(request, native):
        return None
    invocation_id = secrets.token_hex(32) if invocation_id is None else invocation_id
    _hash(invocation_id, 'invocation')
    _hash(parent_session_id, 'parent session')
    parent = {'request': _copy(request), 'native': _copy(native)}
    children = []
    for index, (start, end) in enumerate(integer_halves(pcm.start, pcm.end)):
        piece = child_pcm(pcm, start, end)
        floats = _validate_pcm(piece)
        child = {'schema': 'qwen-recovery-child-v1', 'parent_sha256': digest(parent),
            'invocation_id': invocation_id, 'index': index,
            'read': {'start_frame': start, 'end_frame': end},
            'source_mapping': _copy(piece.mapping), 'source_map_sha256': piece.map_sha256,
            'pcm16_sha256': piece.pcm_sha256, 'float32_sha256': hashlib.sha256(floats).hexdigest(),
            'float32_count': end-start, 'model_sha256': request['model_sha256'],
            'runtime_manifest_sha256': request['runtime_manifest_sha256'],
            'profile_sha256': request['profile_sha256'], 'config': _copy(request['config']),
            'session_policy': 'fresh-session-no-cache-no-history'}
        child['request_id'] = digest(child)
        children.append(child)
    return dict(schema=SCHEMA, parent=parent, children=children,
                invocation_id=invocation_id, parent_session_id=parent_session_id,
                maximum_extra_calls=2, maximum_depth=1)


def validate_plan(plan, pcm):
    _object(plan, {'schema', 'parent', 'children', 'invocation_id',
                   'maximum_extra_calls', 'maximum_depth', 'parent_session_id'}, 'recovery plan')
    _object(plan['parent'], {'request', 'native'}, 'parent evidence')
    expected = prepare(plan['parent']['request'], plan['parent']['native'], pcm,
                       enabled=True, invocation_id=plan['invocation_id'],
                       parent_session_id=plan['parent_session_id'])
    if expected is None or canonical(plan) != canonical(expected):
        raise ValueError('Recovery plan differs from original input/evidence')
    return plan


def assemble(plan, pcm, receipts, *, verify_material):
    """Atomic replacement only for two valid successes in source order.

    Preserve every bounded receipt, including failed or inconsistent evidence.
    A malformed receipt produces unresolved output, never partial replacement.
    Session observations must come from the trusted executor, not model text.
    """
    plan = _copy(plan)
    validate_plan(plan, pcm)
    if not callable(verify_material):
        raise ValueError('Independent material verifier required')
    if type(receipts) is not list or len(receipts) > 2:
        raise ValueError('At most two child receipts')
    bounded_json(receipts, 2 * 262144)
    receipts = _copy(receipts)
    valid = len(receipts) == 2
    sessions = set()
    for index, receipt in enumerate(receipts):
        try:
            _object(receipt, {'request', 'native', 'session_id', 'cache_used',
                              'history_used', 'source_verified', 'runtime_verified'}, 'child receipt')
            if canonical(receipt['request']) != canonical(plan['children'][index]):
                raise ValueError('Stale, reordered or substituted child')
            _hash(receipt['session_id'], 'fresh child session')
            if receipt['session_id'] in sessions or receipt['session_id'] in (plan['invocation_id'], plan['parent_session_id']):
                raise ValueError('Reused session')
            sessions.add(receipt['session_id'])
            if (receipt['cache_used'] is not False or receipt['history_used'] is not False or
                    receipt['source_verified'] is not True or receipt['runtime_verified'] is not True):
                raise ValueError('Unverified executor provenance')
            span = plan['children'][index]['read']
            # This trusted boundary must verify real source/model/runtime bytes
            # and session observations, not simply echo the receipt flags.
            verified = verify_material(_copy(plan['children'][index]),
                child_pcm(pcm, span['start_frame'], span['end_frame']), _copy(receipt))
            if verified is not True:
                raise ValueError('Material verifier did not confirm exact execution')
            native = validate_native_result(receipt['native'])
            if not (native['complete'] is True and native['native_status'] == 0 and
                    native['was_truncated'] is False and not native['issues']):
                raise ValueError('Unresolved child')
        except Exception:
            valid = False
    return dict(schema='qwen-recovery-evidence-v1', plan=_copy(plan), children=receipts,
                recovery_status='replaced' if valid else 'unresolved',
                replacement_text=''.join(r['native']['text'] for r in receipts) if valid else None,
                original_status=plan['parent']['native']['status'], **_SAFETY)


class CallBudget:
    """One shared, nonrefundable call allowance and original absolute deadline.

    Include all used or reserved original parent calls in already_used. A caller must retain this
    object across recovery attempts and persist reservations across restarts.
    This synchronous seam cannot interrupt a blocking injected executor; that
    executor must run under the existing parent-controlled process deadline.
    """
    def __init__(self, total, already_used, deadline):
        integer(total, 'total', 1); integer(already_used, 'already used', 1)
        if already_used > total or type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ValueError('Invalid bounded allowance/deadline')
        self.total, self.used, self.deadline = total, already_used, deadline
        self.parents = set()

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Original deadline expired')

    def reserve_pair(self, parent_id):
        self.check()
        if parent_id in self.parents or self.used + 2 > self.total:
            raise ValueError('Pair already attempted or call budget exhausted')
        self.parents.add(parent_id)
        self.used += 2  # Reserve both even if preparation/execution fails.


def execute(plan, pcm, *, budget, invoke, check_resources, verify_material):
    """Injected executor seam; no retries, depth, cap increase or native imports.

    invoke(child_request, PCMWindow) returns the exact receipt accepted above.
    The external controller owns durable reservations, fresh sessions, resource
    limits and hard cancellation. Do not use an unbounded native callable here.
    """
    plan = _copy(plan)
    validate_plan(plan, pcm)
    if not callable(verify_material):
        raise ValueError('Independent material verifier required')
    if type(budget) is not CallBudget:
        raise ValueError('Shared recovery CallBudget required')
    budget.reserve_pair(plan['parent']['request']['request_id'])
    receipts = []
    for child in plan['children']:
        try:
            budget.check(); check_resources()
            span = child['read']
            receipt = invoke(_copy(child), child_pcm(pcm, span['start_frame'], span['end_frame']))
            bounded_json(receipt, 262144)
            receipts.append(_copy(receipt))
            budget.check(); check_resources()
        except Exception as exc:
            # No traceback/message or private paths; already returned raw stays.
            result = assemble(plan, pcm, receipts, verify_material=verify_material)
            result['recovery_status'] = 'unresolved'
            result['replacement_text'] = None
            result['controller_failure_type'] = type(exc).__name__
            return result
    result = assemble(plan, pcm, receipts, verify_material=verify_material)
    try:
        budget.check(); check_resources()
    except Exception as exc:
        result['recovery_status'] = 'unresolved'
        result['replacement_text'] = None
        result['controller_failure_type'] = type(exc).__name__
    return result
