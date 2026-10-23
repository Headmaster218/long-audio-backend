"""Finite AB same-Store projections. No product/model/SQL/service execution."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import ab_uploader_cli as uploader
from ab_profile import KIND, GROUP, PROFILE, validate_inputs, validate_result
import a_input
import b_input
from launch_binding import FIXED_SETTINGS, validate_settings_policy
from prepare_manifest import validate_input_flags
from test_A_upload import page_projection, final_projection


def arm_result(arm):
    return dict(completed_chunks=69, accepted_sources=8, pages=3, eos_pages=1,
        frames=11520000, seconds=720, input_sha256={'A': a_input.SHA256, 'B': b_input.SHA256}[arm],
        source_window_seconds=[840, 1560], capture_origin=100003, capture_end=11620003,
        exact_offsets=True, exact_frame_conservation=True)


def complete_result():
    return dict(completed_chunks=138, accepted_sources=16, pages=6, eos_pages=2,
        frames=23040000, seconds=1440, exact_offsets=True, exact_frame_conservation=True,
        sessions={'A': 'a'*32, 'B': 'b'*32}, integration_profile=PROFILE,
        arms={arm: arm_result(arm) for arm in ('A', 'B')})


def test_serial_normal_API_shared_group_distinct_sessions_and_capture_ids():
    records = {'A': {}, 'B': {}}
    created = {'A': [], 'B': []}
    calls, sessions, completed = [], [], []
    def piece(arm, source):
        data = b'0'*(44+source['frames']*2)
        records[arm][source['index']] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        return data
    def call(method, route, path, **kwargs):
        calls.append((method, route, path))
        if path == '/sessions':
            arm = 'A' if not sessions else 'B'
            if arm == 'B': assert completed == ['A']
            assert json.loads(kwargs['body']) == dict(name='', identity_group=GROUP,
                identity_mode='verified_review_v1', processing_mode='legacy')
            sessions.append(arm)
            return dict(id=arm.lower()*32, identity_group=GROUP)
        arm = 'A' if '/'+('a'*32)+'/' in path else 'B'
        if '/chunks?' not in path: assert '/recordings/'+arm+'-fixed' in path
        if method == 'POST' and path.endswith('/pages'):
            number = json.loads(kwargs['body'])['page_number']
            created[arm].append(number); return {}
        if method == 'PUT':
            number = int(path.split('/pages/')[1].split('/')[0])
            key = kwargs['headers']['Idempotency-Key']
            assert key.startswith(arm+'-source-')
            index = int(key.rsplit('-', 1)[1])
            revision = [s['index'] for s in a_input.plan()[number-1]['sources']].index(index)+1
            return dict(revision=revision, duplicate=False)
        if method == 'POST': return {}
        if '/pages/' in path:
            number = int(path.rsplit('/', 1)[1])
            return page_projection(a_input.plan()[number-1], records[arm],
                'waiting_input' if len(created[arm]) < 3 else 'completed')
        pages, chunks, capture = final_projection(records[arm])
        if '/chunks?' in path: return chunks
        completed.append(arm)
        return capture
    result = uploader.collect(call, {arm: lambda source, arm=arm: piece(arm, source) for arm in ('A', 'B')},
        deadline=1700, clock=lambda: 0, sleep=lambda _: pytest.fail('unexpected sleep'))
    validate_result(result)
    assert sessions == completed == ['A', 'B']
    assert len({p for m, r, p in calls if r == 'create_recording'}) == 1  # normal POST /sessions twice
    assert sum(m == 'POST' for m, _, _ in calls) == 14
    assert sum(m == 'PUT' for m, _, _ in calls) == 16
    assert not any(m not in ('POST', 'PUT', 'GET') for m, _, _ in calls)
    assert result['identity_learning'] == 'NOT_QUALIFIED'


@pytest.mark.parametrize('fault', ['A_failure', 'A_incomplete', 'deadline_after_A', 'B_failure', 'duplicate_sid', 'wrong_group'])
def test_no_B_after_A_failure_no_deadline_reset_or_write_retry(monkeypatch, fault):
    events, deadlines, requests, now = [], [], [], [0.]
    def call(method, route, path, **kwargs):
        requests.append((method, path))
        sid = 'a'*32 if len(requests) == 1 or fault == 'duplicate_sid' else 'b'*32
        return dict(id=sid, identity_group='wrong' if fault == 'wrong_group' else GROUP)
    def fake(arm):
        def collect(call, piece, *, deadline, clock, sleep):
            events.append(arm); deadlines.append(deadline)
            call('POST', 'create_recording', '/sessions', body=json.dumps(dict(name='',
                identity_group=arm+'-anonymous', processing_mode='legacy')).encode())
            if fault == arm+'_failure': raise TimeoutError('uncertain request')
            result = arm_result(arm)
            if fault == 'A_incomplete' and arm == 'A': result['completed_chunks'] = 68
            if fault == 'deadline_after_A' and arm == 'A': now[0] = 1700
            return result
        return collect
    monkeypatch.setattr(uploader, 'collect_a', fake('A'))
    monkeypatch.setattr(uploader, 'collect_b', fake('B'))
    with pytest.raises((ValueError, TimeoutError)):
        uploader.collect(call, {'A': None, 'B': None}, deadline=1700, clock=lambda: now[0])
    assert deadlines == [1700]*len(events)
    assert len(requests) == len(events)
    if fault in ('A_failure', 'A_incomplete', 'deadline_after_A', 'wrong_group'): assert events == ['A']


def test_settings_only_two_session_capacity_and_fresh_data_change():
    root = Path(__file__).parent
    ab = json.loads((root/'settings.AB.json').read_bytes())
    a = json.loads((root/'settings.A.json').read_bytes())
    assert ab.pop('data_dir') != a.pop('data_dir')
    assert ab.pop('max_sessions') == 2 and a.pop('max_sessions') == 1
    assert ab.pop('verified_identity_policy_file') == '/workspace/scratch/12ec3121e93f/audio-recovered-commit-diagnostics-rev01-20261023/audio_backend/profiles/verified-review-experimental-v1.json'
    assert ab == a and ab['reviewed_primary_persons_enabled'] is False
    values = dict(FIXED_SETTINGS, data_dir=Path('/fresh/data'), max_sessions=2)
    validate_settings_policy(SimpleNamespace(**values), Path('/fresh'), KIND)
    for mode in ('lifecycle-only', 'short-operational', 'A-operational', 'B-operational'):
        with pytest.raises(ValueError): validate_settings_policy(SimpleNamespace(**values), Path('/fresh'), mode)
    for capacity in (1, 3):
        with pytest.raises(ValueError):
            validate_settings_policy(SimpleNamespace(**dict(values, max_sessions=capacity)), Path('/fresh'), KIND)


@pytest.mark.parametrize('short,a,b', [(None,None,None), (None,'a',None), (None,None,'b'), ('short','a','b')])
def test_AB_flags_require_both_only(short, a, b):
    with pytest.raises(ValueError): validate_input_flags(KIND, short, a, b)
    validate_input_flags(KIND, None, 'a', 'b')


@pytest.mark.parametrize('fault', ['missing_B', 'swapped', 'window', 'unpinned', 'profile', 'valid'])
def test_AB_binding_checks_both_inputs_before_opening_either(tmp_path, monkeypatch, fault):
    rows, opens = {}, []
    for arm, module in [('A', a_input), ('B', b_input)]:
        path = tmp_path/arm; path.write_bytes(b'fake')
        rows[arm] = dict(path=str(path), bytes=module.BYTES, sha256=module.SHA256,
            source_frame_start=13440000, source_frame_end=24960000,
            capture_origin_frame=100003, local_timeline_start=0)
    pinned = {row['path'] for row in rows.values()}
    value = dict(input=rows, integration_profile=dict(PROFILE))
    if fault == 'missing_B': rows.pop('B')
    if fault == 'swapped': rows['A'], rows['B'] = rows['B'], rows['A']
    if fault == 'window': rows['B']['source_frame_start'] = 0
    if fault == 'unpinned': pinned.remove(rows['B']['path'])
    if fault == 'profile': value['integration_profile']['identity_group'] = 'other'
    class Reader:
        def __init__(self, path, deadline): opens.append((path.name, deadline))
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(a_input, 'AInput', Reader)
    monkeypatch.setattr(b_input, 'BInput', Reader)
    if fault == 'valid':
        validate_inputs(value, pinned, 1700)
        assert opens == [('A',1700), ('B',1700)]
    else:
        with pytest.raises(ValueError): validate_inputs(value, pinned, 1700)
        assert opens == []


def test_original_A_B_and_resource_modules_remain_byte_identical():
    root = Path(__file__).parent
    old = root.parent/'audio-recovery-runner-rev07-20261023'
    for name in ('a_input.py','b_input.py','a_uploader_cli.py','b_uploader_cli.py',
                 'settings.A.json','settings.B.json','monitor.py','ownership.py',
                 'locks.py','http_input.py','health.py','health_transport.py',
                 'bindings.py','service_cli.py','service_wrapper.py','child_environment.py',
                 'uploader_cli.py','short_input.py','test_A_upload.py','test_B_upload.py',
                 'test_B_binding.py','test_actual_A_input.py','test_actual_B_input.py'):
        assert (root/name).read_bytes() == (old/name).read_bytes(), name


@pytest.mark.parametrize('fault', ['one_session', 'same_session', 'one_arm', 'swapped_arm', 'incomplete_B', 'aggregate', 'profile'])
def test_parent_receipt_cannot_promote_one_arm_to_AB(fault):
    result = complete_result()
    if fault == 'one_session': result['sessions'].pop('B')
    if fault == 'same_session': result['sessions']['B'] = result['sessions']['A']
    if fault == 'one_arm': result['arms'].pop('B')
    if fault == 'swapped_arm': result['arms']['B'] = result['arms']['A']
    if fault == 'incomplete_B': result['arms']['B']['completed_chunks'] = 68
    if fault == 'aggregate': result['frames'] = 11520000
    if fault == 'profile': result['integration_profile'] = {}
    with pytest.raises(ValueError): validate_result(result)


def test_one_ledger_and_same_absolute_API_deadline_in_actual_uploader_wiring():
    import ast
    tree = ast.parse((Path(__file__).parent/'ab_uploader_cli.py').read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    ledgers = [node for node in calls if isinstance(node.func, ast.Name) and node.func.id == 'RequestLedger']
    assert len(ledgers) == 1
    api_call = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == 'api')
    keywords = {item.arg: ast.unparse(item.value) for item in api_call.keywords}
    assert keywords['ledger'] == 'ledger' and keywords['deadline'] == 'args.deadline'
    main_collect = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == 'collect')
    assert next(ast.unparse(item.value) for item in main_collect.keywords if item.arg == 'deadline') == 'args.deadline'
