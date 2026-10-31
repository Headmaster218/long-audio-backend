import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import wave

import pytest

from audio_backend.fragment_core import SourcePlan, Span, inspect_pcm16_source
from audio_backend.fragment_runtime_contracts import digest
from audio_backend.qwen_candidates import prepare_candidate_requests, normalize_native_result
from audio_backend.qwen_candidate_store import CandidateStore, Conflict, StaleOwner
from audio_backend.qwen_candidate_runner import (
    atomic_json, decode_window, file_receipt, freeze, read_json, run, runner_lease, verify_frozen, wire,
)


def media(tmp_path, frames=16):
    path = tmp_path / 'source.wav'
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(b'\x01\x00' * frames)
    source = inspect_pcm16_source('source', path)
    plan = SourcePlan((source,), (Span('source', 0, frames, 0, frames, 0),), (), (), True).validate()
    return plan, {'source': str(path)}


def requests(tmp_path):
    plan, paths = media(tmp_path)
    return prepare_candidate_requests(plan, paths, source_scope='public-synthetic', source_generation=0,
                                      runtime_manifest_sha256='1' * 64, model_sha256='2' * 64)


def store_ready(tmp_path):
    items = requests(tmp_path)
    store = CandidateStore(tmp_path / 'ledger')
    store.initialize('3' * 64, items, {'max_calls': 3, 'max_frames': 48})
    store.acquire({'pid': os.getpid(), 'created': 0})
    return store, items[0]['request_id'], items


def native_raw(**changes):
    result = dict(native_status=0, native_status_name='OK', result_available=True,
                  text='hello', raw_text='hello', language='en', was_truncated=False,
                  truncation_available=True, exception_type=None, exception_status=None,
                  partial_result_available=False, native_token_count=0, native_timestamp_kind='none')
    result.update(changes)
    return result


def test_durable_duplicate_no_refund_and_explicit_retry(tmp_path):
    store, rid, items = store_ready(tmp_path)
    aid = store.reserve(rid); store.started(aid)
    with pytest.raises(Conflict):
        store.reserve(rid)
    # Reopening/acquiring preserves the original uncertain call, never runs it.
    reopened = CandidateStore(tmp_path / 'ledger')
    reopened.initialize('3' * 64, items, {'max_calls': 3, 'max_frames': 48})
    reopened.acquire({'pid': os.getpid(), 'created': 1})
    assert reopened.request(rid)['status'] == 'interrupted_outcome_unknown'
    assert reopened.check_owner()['calls'] == 1
    with pytest.raises(Conflict):
        reopened.reserve(rid)
    retry = reopened.reserve(rid, retry_of=aid, retry_key='explicit-review-1')
    reopened.started(retry)
    native = normalize_native_result(native_raw())
    reopened.finish(retry, native['status'], {'native': native}, returned=True)
    assert reopened.request(rid)['status'] == 'returned_text'
    assert reopened.check_owner()['calls'] == 2
    with pytest.raises(Conflict):
        reopened.reserve(rid, retry_of=retry, retry_key='explicit-review-2')


def test_idempotency_payload_conflict_and_budget(tmp_path):
    store, rid, items = store_ready(tmp_path)
    # Existing IDs are also immutable keys, not aliases with changed payload.
    with pytest.raises(Conflict):
        store.register('different-key', items[0])
    with pytest.raises(Conflict):
        store.initialize('4' * 64, items, {'max_calls': 3, 'max_frames': 48})
    store.initialize('3' * 64, items, {'max_calls': 3, 'max_frames': 48})
    assert store.check_owner()['calls'] == 0


def test_cancel_fences_late_success_retains_native_result(tmp_path):
    store, rid, _ = store_ready(tmp_path)
    aid = store.reserve(rid); store.started(aid)
    other = CandidateStore(tmp_path / 'ledger'); other.cancel()
    with pytest.raises(StaleOwner):
        store.check_owner()
    native = normalize_native_result(native_raw())
    store.finish(aid, native['status'], {'native': native}, returned=True)
    row = other.request(rid)
    assert row['status'] == 'interrupted_outcome_unknown'
    assert row['attempts'][0]['evidence']['native']['text'] == 'hello'
    with pytest.raises(StaleOwner):
        other.acquire({'pid': 0})


def test_cancel_before_start_and_event_cursor_updates(tmp_path):
    store, rid, _ = store_ready(tmp_path)
    before = store.events()['next_cursor']
    store.cancel()
    events = store.events(before, 1)
    assert events['events'][0]['kind'] == 'cancelled_before_start'
    assert events['has_more']
    assert store.request(rid)['status'] == 'cancelled_before_start'
    with pytest.raises(ValueError):
        store.events(-1)


def test_commit_fault_reservation_and_evidence_corruption(tmp_path):
    store, rid, _ = store_ready(tmp_path)
    def fail(point):
        if point == 'before_commit':
            raise RuntimeError('injected')
    store.fault = fail
    with pytest.raises(RuntimeError):
        store.reserve(rid)
    assert store.check_owner()['calls'] == 0
    store.fault = lambda point: None
    aid = store.reserve(rid); store.started(aid)
    store.finish(aid, 'native_error', {'native_status': 17}, returned=True)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE attempts SET evidence='{}'")
    with pytest.raises(ValueError, match='Corrupt'):
        store.request(rid)


def test_lost_commit_response_never_reinvokes(tmp_path):
    store, rid, _ = store_ready(tmp_path)
    def fail(point):
        if point == 'after_commit':
            raise RuntimeError('lost ACK')
    store.fault = fail
    with pytest.raises(RuntimeError):
        store.reserve(rid)
    store.fault = lambda point: None
    assert store.check_owner()['calls'] == 1
    with pytest.raises(Conflict):
        store.reserve(rid)


def test_exclusive_runner_lease(tmp_path):
    with runner_lease(tmp_path):
        with pytest.raises(BlockingIOError):
            with runner_lease(tmp_path):
                pass


class Session:
    limits = SimpleNamespace(effective_n_ctx=2048, effective_max_audio_ms=20000)
    _h = object()
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def run(self, *args, **kwargs):
        if self.error: raise self.error
        return self.result


@pytest.mark.parametrize('text,status,truncated', [('hello', 0, False), ('', 0, False), ('partial', 18, True), ('partial', 13, False), (None, 17, False)])
def test_fake_native_adapter_all_statuses(text, status, truncated):
    result = SimpleNamespace(text=text, raw_text=text, language='en', tokens=[], timestamp_kind='none') if text is not None else None
    error = None
    if status:
        error = RuntimeError('native failure'); error.status = status
        error.partial_result = result
    session = Session(result, error)
    model = SimpleNamespace(session=lambda **kw: session)
    api = SimpleNamespace(_lib=SimpleNamespace(transcribe_was_truncated=lambda h: truncated))
    native = decode_window(model, api, [0.0])
    assert native['native_status'] == status
    assert native['text'] == text
    assert native['word_timestamps'] is None and native['speaker_id'] is None
    assert native['complete'] is (status == 0)
    if not text and status == 0:
        assert native['status'] == 'returned_empty'


def frozen_fixture(tmp_path, frames=16):
    plan, paths = media(tmp_path, frames)
    asset = tmp_path / 'synthetic-model'; asset.write_bytes(b'not a model, never imported')
    receipt = file_receipt(asset)
    runtime = dict(schema='qwen-local-runtime-v1', model=receipt, python=file_receipt(sys.executable),
                   native_library=receipt, files=[receipt], package_version='test', native_commit='test',
                   model_id='synthetic', model_revision='test', license='test')
    limits = dict(max_calls=(frames + 319999) // 320000, max_frames=frames, tree_rss_bytes=256 * 1024**2,
                  host_reserve_bytes=2 * 1024**3, disk_reserve_bytes=5 * 1024**3,
                  artifact_bytes=1024**2, timeout_seconds=30, cleanup_seconds=10,
                  cleanup_write_bytes=256 * 1024, preflight_seconds=30)
    frozen = freeze(plan, paths, runtime, limits, source_scope='public-synthetic')
    path = tmp_path / 'frozen.json'; atomic_json(path, frozen)
    return frozen, path


def test_frozen_hash_source_model_mutation_and_no_model_import(tmp_path):
    frozen, path = frozen_fixture(tmp_path)
    assert 'transcribe_cpp' not in sys.modules
    verify_frozen(read_json(path), digest(frozen))
    model = Path(frozen['runtime']['model']['path']); model.write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash changed'):
        verify_frozen(frozen, digest(frozen))
    assert 'transcribe_cpp' not in sys.modules


def test_source_changed_before_child_launch(tmp_path):
    frozen, path = frozen_fixture(tmp_path)
    source = Path(frozen['source_bindings']['source'])
    data = bytearray(source.read_bytes()); data[-1] ^= 1; source.write_bytes(data)
    with pytest.raises(ValueError):
        run(path, digest(frozen), {'frozen_sha256': digest(frozen), 'authorization_reference': 'fake-test-only'}, tmp_path / 'out')


def test_real_synthetic_child_positive_rss_wait_and_duplicate_no_call(tmp_path):
    frozen, path = frozen_fixture(tmp_path)
    # No model imports. Protocol double uses a 4 MiB allocation and actual IPC.
    code = '''import os,socket
from audio_backend.qwen_candidate_runner import wire
from audio_backend.qwen_candidates import normalize_native_result
sock=socket.socket(fileno=int(os.environ['QWEN_CONTROL_FD']))
allocation=bytearray(4*1024**2)
wire(sock,{'kind':'ready','pid':os.getpid(),'runtime':{'test_double':True}})
message=wire(sock); rid=message['request_id']
wire(sock,{'kind':'prepared','request_id':rid})
assert wire(sock)=={'kind':'invoke','request_id':rid}
raw=dict(native_status=0,native_status_name='OK',result_available=True,text='',raw_text='',language=None,was_truncated=False,truncation_available=True,exception_type=None,exception_status=None,partial_result_available=False,native_token_count=0,native_timestamp_kind='none')
wire(sock,{'kind':'returned','request_id':rid,'native':normalize_native_result(raw)})
assert wire(sock)=={'kind':'close'}
sock.close()
'''
    approval = {'frozen_sha256': digest(frozen), 'authorization_reference': 'explicit-model-free-test'}
    out = tmp_path / 'out'
    run(path, digest(frozen), approval, out, child_command=[sys.executable, '-B', '-c', code])
    store = CandidateStore(out)
    row = store.request(frozen['requests'][0]['request_id'])
    assert row['status'] == 'returned_empty'
    receipts = [e['payload'] for e in store.events()['events'] if e['kind'] == 'owner_receipt']
    assert receipts[-1]['original_handle_waited_and_reaped']
    assert receipts[-1]['wait_returncode'] == 0
    assert receipts[-1]['positive_child_rss_sample']['rss'] > 0
    assert receipts[-1]['ready_child_rss_sample']['rss'] >= 4 * 1024**2
    # Existing terminal work returns without even starting this failing command.
    run(path, digest(frozen), approval, out, child_command=['/not/a/command'])
    assert len(store.request(frozen['requests'][0]['request_id'])['attempts']) == 1


def test_child_exit_retains_unknown_no_resubmit(tmp_path):
    frozen, path = frozen_fixture(tmp_path)
    code = '''import os,socket
from audio_backend.qwen_candidate_runner import wire
s=socket.socket(fileno=int(os.environ['QWEN_CONTROL_FD']))
wire(s,{'kind':'ready','pid':os.getpid(),'runtime':{'test_double':True}})
m=wire(s);wire(s,{'kind':'prepared','request_id':m['request_id']})
wire(s);os._exit(9)
'''
    approval = {'frozen_sha256': digest(frozen), 'authorization_reference': 'explicit-model-free-test'}
    out = tmp_path / 'out'
    with pytest.raises(RuntimeError, match='stopped'):
        run(path, digest(frozen), approval, out, child_command=[sys.executable, '-B', '-c', code])
    store = CandidateStore(out)
    row = store.request(frozen['requests'][0]['request_id'])
    assert row['status'] == 'interrupted_outcome_unknown'
    assert row['attempts'][0]['started'] == 1 and row['attempts'][0]['returned'] == 0
    run(path, digest(frozen), approval, out, child_command=['/not/a/command'])
    assert len(store.request(row['request']['request_id'])['attempts']) == 1


def test_valid_different_payload_same_client_key_rejected(tmp_path):
    plan, paths = media(tmp_path)
    first = prepare_candidate_requests(plan, paths, source_scope='one', source_generation=0,
        runtime_manifest_sha256='1' * 64, model_sha256='2' * 64)[0]
    second = prepare_candidate_requests(plan, paths, source_scope='two', source_generation=0,
        runtime_manifest_sha256='1' * 64, model_sha256='2' * 64)[0]
    store = CandidateStore(tmp_path / 'ledger')
    store.register('client-operation', first)
    store.register('client-operation', first)
    with pytest.raises(Conflict):
        store.register('client-operation', second)


def test_native_failure_child_packet_preserves_numeric_status(tmp_path):
    frozen, path = frozen_fixture(tmp_path)
    code = '''import os,socket
from audio_backend.qwen_candidate_runner import wire
s=socket.socket(fileno=int(os.environ['QWEN_CONTROL_FD']))
wire(s,{'kind':'failure','failure':{'type':'InputTooLong','native_status':17,'message':'bounded native error'}})
s.close()
'''
    approval = {'frozen_sha256': digest(frozen), 'authorization_reference': 'model-free-failure-test'}
    out = tmp_path / 'out'
    with pytest.raises(RuntimeError, match='ChildFailure'):
        run(path, digest(frozen), approval, out, child_command=[sys.executable, '-B', '-c', code])
    store = CandidateStore(out)
    assert store.request(frozen['requests'][0]['request_id'])['status'] == 'not_attempted_after_stop'
    receipt = [e['payload'] for e in store.events()['events'] if e['kind'] == 'owner_receipt'][-1]
    assert receipt['failure']['child']['native_status'] == 17
    assert receipt['original_handle_waited_and_reaped']


def test_truncation_probe_failure_keeps_text_but_cannot_succeed():
    result = SimpleNamespace(text='retained', raw_text='retained', language='en', tokens=[], timestamp_kind='none')
    def failure(handle):
        raise RuntimeError('probe failed')
    native = decode_window(SimpleNamespace(session=lambda **kw: Session(result)),
                           SimpleNamespace(_lib=SimpleNamespace(transcribe_was_truncated=failure)), [0.0])
    assert native['text'] == 'retained'
    assert native['native_status'] == 0 and native['was_truncated'] is None
    assert native['status'] == 'schema_error' and not native['complete']


def test_false_success_without_native_evidence_rejected(tmp_path):
    store, rid, _ = store_ready(tmp_path)
    aid = store.reserve(rid); store.started(aid)
    with pytest.raises(ValueError, match='requires validated'):
        store.finish(aid, 'returned_text', {}, returned=True)
    native = normalize_native_result(native_raw(native_status=18, native_status_name='OUTPUT_TRUNCATED',
                                               was_truncated=True))
    with pytest.raises(ValueError, match='disagrees'):
        store.finish(aid, 'returned_text', {'native': native}, returned=True)


def test_truncated_run_stops_remaining_preserves_partial_and_denominator(tmp_path):
    frozen, path = frozen_fixture(tmp_path, frames=320001)
    code = '''import os,socket
from audio_backend.qwen_candidate_runner import wire
from audio_backend.qwen_candidates import normalize_native_result
s=socket.socket(fileno=int(os.environ['QWEN_CONTROL_FD']))
wire(s,{'kind':'ready','pid':os.getpid(),'runtime':{'test_double':True}})
m=wire(s);rid=m['request_id'];wire(s,{'kind':'prepared','request_id':rid})
assert wire(s)=={'kind':'invoke','request_id':rid}
raw=dict(native_status=18,native_status_name='OUTPUT_TRUNCATED',result_available=True,text='partial',raw_text='partial',language=None,was_truncated=True,truncation_available=True,exception_type='OutputTruncated',exception_status=18,partial_result_available=True,native_token_count=0,native_timestamp_kind='none')
wire(s,{'kind':'returned','request_id':rid,'native':normalize_native_result(raw)})
assert wire(s)=={'kind':'close'}
'''
    approval = {'frozen_sha256': digest(frozen), 'authorization_reference': 'model-free-truncation-test'}
    out = tmp_path / 'out'
    run(path, digest(frozen), approval, out, child_command=[sys.executable, '-B', '-c', code])
    store = CandidateStore(out)
    first, last = [store.request(r['request_id']) for r in frozen['requests']]
    assert first['status'] == 'output_truncated'
    assert first['attempts'][0]['evidence']['native']['text'] == 'partial'
    assert last['status'] == 'not_attempted_after_stop' and last['attempts'] == []


def test_post_call_source_failure_retains_output_without_success(tmp_path):
    store, rid, _ = store_ready(tmp_path)
    aid = store.reserve(rid); store.started(aid)
    native = normalize_native_result(native_raw())
    store.finish(aid, 'source_changed', {'native': native,
                 'validation_failure': {'status': 'source_changed', 'type': 'SourceChanged'}}, returned=True)
    row = store.request(rid)
    assert row['status'] == 'source_changed'
    assert row['attempts'][0]['evidence']['native']['text'] == 'hello'


def test_cli_freeze_list_show_and_primary_sentinels_unchanged(tmp_path):
    frozen, _ = frozen_fixture(tmp_path)
    # Deliberately unrelated primary artifacts: candidate operations must never
    # discover/open/write them or infer a primary-store path from this folder.
    sentinels = {}
    for name in ('primary-transcript.json', 'identity-bank.sqlite3', 'coverage.json'):
        p = tmp_path / name; p.write_bytes(b'primary bytes unchanged'); sentinels[p] = p.read_bytes()
    source_plan = tmp_path / 'plan.json'; source_plan.write_text(frozen['source_plan'])
    args = {}
    for name, value in [('bindings', frozen['source_bindings']), ('runtime', frozen['runtime']), ('limits', frozen['limits'])]:
        p = tmp_path / (name + '.json'); p.write_text(json.dumps(value)); args[name] = p
    cli = Path(__file__).resolve().parents[1] / 'scripts/benchmarks/run_qwen_candidates.py'
    out = tmp_path / 'cli-out'
    command = [sys.executable, '-B', str(cli), 'freeze', '--output', str(out), '--source-plan', str(source_plan),
               '--source-bindings', str(args['bindings']), '--runtime-manifest', str(args['runtime']),
               '--limits', str(args['limits']), '--source-scope', 'public-synthetic']
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(result.stdout)['inference_executed'] is False
    value = read_json(out / 'frozen.json')
    store = CandidateStore(out)
    store.initialize(digest(value), value['requests'], {k: value['limits'][k] for k in ('max_calls', 'max_frames')})
    listed = subprocess.run([sys.executable, '-B', str(cli), 'list', '--output', str(out)],
                            capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(listed.stdout)['events'][0]['kind'] == 'planned'
    shown = subprocess.run([sys.executable, '-B', str(cli), 'show', '--output', str(out),
                            '--candidate-id', value['requests'][0]['request_id']],
                           capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(shown.stdout)['status'] == 'planned'
    for p, before in sentinels.items():
        assert p.read_bytes() == before
    assert 'transcribe_cpp' not in sys.modules


def test_incomplete_implementation_manifest_rejected(tmp_path):
    frozen, _ = frozen_fixture(tmp_path)
    frozen['implementation'] = []
    with pytest.raises(ValueError, match='implementation inventory'):
        verify_frozen(frozen, digest(frozen))


@pytest.mark.parametrize('prior', ['invocation_started', 'output_truncated'])
def test_noncomplete_recovery_fences_every_still_planned_window(tmp_path, prior):
    frozen, path = frozen_fixture(tmp_path, frames=320001)
    out = tmp_path / 'out'; store = CandidateStore(out)
    store.initialize(digest(frozen), frozen['requests'], {k: frozen['limits'][k] for k in ('max_calls', 'max_frames')})
    store.acquire({'pid': os.getpid()})
    aid = store.reserve(frozen['requests'][0]['request_id']); store.started(aid)
    if prior == 'output_truncated':
        native = normalize_native_result(native_raw(native_status=18, native_status_name='OUTPUT_TRUNCATED', was_truncated=True))
        store.finish(aid, prior, {'native': native}, returned=True)
    # Both crash positions precede stop_remaining. Neither permits a new child.
    approval = {'frozen_sha256': digest(frozen), 'authorization_reference': 'model-free-recovery-test'}
    run(path, digest(frozen), approval, out, child_command=['/must-not-launch'])
    remaining = store.request(frozen['requests'][1]['request_id'])
    assert remaining['status'] == 'not_attempted_after_stop' and remaining['attempts'] == []
    assert not [e for e in store.events()['events'] if e['kind'] == 'owner_receipt']


def test_expired_compute_deadline_keeps_bounded_terminal_reap_receipt(tmp_path):
    frozen, _ = frozen_fixture(tmp_path)
    frozen['limits']['timeout_seconds'] = 1
    path = tmp_path / 'deadline.json'; atomic_json(path, frozen)
    code = '''import os,socket,time
from audio_backend.qwen_candidate_runner import wire
s=socket.socket(fileno=int(os.environ['QWEN_CONTROL_FD']))
wire(s,{'kind':'ready','pid':os.getpid(),'runtime':{'test_double':True}})
m=wire(s);wire(s,{'kind':'prepared','request_id':m['request_id']});wire(s);time.sleep(10)
'''
    out = tmp_path / 'out'
    with pytest.raises(RuntimeError):
        run(path, digest(frozen), {'frozen_sha256': digest(frozen), 'authorization_reference': 'deadline-test'},
            out, child_command=[sys.executable, '-B', '-c', code])
    store = CandidateStore(out)
    assert store.request(frozen['requests'][0]['request_id'])['status'] == 'interrupted_outcome_unknown'
    receipt = [e['payload'] for e in store.events()['events'] if e['kind'] == 'owner_receipt'][-1]
    assert receipt['original_handle_waited_and_reaped'] and receipt['cleanup_verified']
    assert receipt['cleanup_seconds_limit'] == 10
    assert receipt['cleanup_write_bytes_limit'] == 256 * 1024


@pytest.mark.parametrize('when,target', [('construct', 'model'), ('before_invoke', 'model'), ('before_invoke', 'source')])
def test_child_mutation_at_load_or_actual_invocation_boundary_stops_call(tmp_path, monkeypatch, when, target):
    import threading
    import audio_backend.qwen_candidate_runner as runner
    frozen, path = frozen_fixture(tmp_path); calls = []
    result = SimpleNamespace(text='native', raw_text='native', language='en', tokens=[], timestamp_kind='none')
    class Counted(Session):
        def run(self, *args, **kwargs):
            calls.append(True); return result
    model = SimpleNamespace(session=lambda **kw: Counted(), close=lambda: None)
    api = SimpleNamespace(_lib=SimpleNamespace(transcribe_was_truncated=lambda _: False))
    changed = Path(frozen['runtime']['model']['path'] if target == 'model' else frozen['source_bindings']['source'])
    def mutate():
        data = bytearray(changed.read_bytes()); data[-1] ^= 1; changed.write_bytes(data)
    def construct(runtime):
        runner.verify_runtime(runtime)
        if when == 'construct': mutate()
        return model, api, {}
    monkeypatch.setattr(runner, '_construct', construct)
    parent, child = socket.socketpair(); errors = []
    def worker():
        try: runner.child_main(child.detach(), str(path), digest(frozen))
        except Exception as exc: errors.append(type(exc).__name__)
    thread = threading.Thread(target=worker); thread.start()
    message = wire(parent)
    if when == 'before_invoke':
        assert message['kind'] == 'ready'
        rid = frozen['requests'][0]['request_id']
        wire(parent, {'kind': 'prepare', 'request_id': rid})
        assert wire(parent)['kind'] == 'prepared'
        mutate()
        wire(parent, {'kind': 'invoke', 'request_id': rid})
        message = wire(parent)
    thread.join(3); parent.close()
    assert not thread.is_alive() and calls == []
    assert errors and message['kind'] == 'failure'


def test_reserved_cancel_is_positive_not_invoked_not_unknown(tmp_path):
    store, rid, _ = store_ready(tmp_path)
    aid = store.reserve(rid); store.cancel()
    assert store.request(rid)['status'] == 'cancelled_before_start'
    with pytest.raises(StaleOwner): store.started(aid)
    store.finish(aid, 'cancelled_before_start', {'cancelled': True})
    row = store.request(rid)
    assert row['status'] == 'cancelled_before_start'
    assert row['attempts'][0]['started'] == 0 and row['attempts'][0]['returned'] == 0
    event = next(e for e in store.events()['events'] if e['payload'].get('reason_code') == 'cancelled_not_invoked')
    assert event['payload']['execution_started'] is False


def test_normal_success_terminates_remembered_owned_descendant(tmp_path):
    import psutil
    frozen, path = frozen_fixture(tmp_path)
    code = '''import os,socket,subprocess,sys,time
from audio_backend.qwen_candidate_runner import wire
from audio_backend.qwen_candidates import normalize_native_result
s=socket.socket(fileno=int(os.environ['QWEN_CONTROL_FD']))
child=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(15)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(.1)
wire(s,{'kind':'ready','pid':os.getpid(),'runtime':{'test_double':True}})
m=wire(s);rid=m['request_id'];wire(s,{'kind':'prepared','request_id':rid});wire(s)
raw=dict(native_status=0,native_status_name='OK',result_available=True,text='',raw_text='',language=None,was_truncated=False,truncation_available=True,exception_type=None,exception_status=None,partial_result_available=False,native_token_count=0,native_timestamp_kind='none')
wire(s,{'kind':'returned','request_id':rid,'native':normalize_native_result(raw)})
assert wire(s)=={'kind':'close'}
'''
    out = tmp_path / 'out'
    run(path, digest(frozen), {'frozen_sha256': digest(frozen), 'authorization_reference': 'owned-descendant-test'},
        out, child_command=[sys.executable, '-B', '-c', code])
    receipt = [e['payload'] for e in CandidateStore(out).events()['events'] if e['kind'] == 'owner_receipt'][-1]
    assert receipt['wait_returncode'] == 0 and receipt['original_handle_waited_and_reaped']
    assert receipt['cleanup_verified'] and receipt['remaining_live_owned'] == []
    assert receipt['observed_live_descendants_after_direct_wait']
    # Non-child zombies are not falsely represented as reaped by this owner.
    assert all(p['status'] == psutil.STATUS_ZOMBIE for p in receipt['terminated_but_not_reaped_nonchild_zombies'])


def test_caller_unrelated_sibling_is_not_owned_or_signalled(tmp_path):
    from audio_backend.qwen_candidate_runner import CandidateGuard
    from audio_backend.fragment_model_guard import Identity
    sibling = subprocess.Popen([sys.executable, '-B', '-c', 'import time;time.sleep(15)'])
    worker = subprocess.Popen([sys.executable, '-B', '-c', 'import time;time.sleep(15)'])
    try:
        guard = CandidateGuard(tmp_path, own_cap=256 * 1024**2, aggregate_cap=256 * 1024**2,
                               disk_reserve=5 * 1024**3, deadline=10)
        guard.bind_worker(Identity.bind(worker.pid))
        sample = guard.check()
        assert worker.pid in guard.known_own and sibling.pid not in guard.known_own
        assert sibling.pid not in {p['pid'] for p in sample['processes']}
        guard.terminate_owned()
        worker.wait(timeout=3)
        assert sibling.poll() is None
    finally:
        for proc in (worker, sibling):
            if proc.poll() is None: proc.kill()
            proc.wait(timeout=3)


def test_recycled_worker_pid_is_not_signalled(tmp_path, monkeypatch):
    import audio_backend.qwen_candidate_runner as runner
    import audio_backend.fragment_model_guard as common
    from audio_backend.fragment_model_guard import Identity, ResourceLimit
    fake = Identity(987654321, 1.)
    guard = runner.CandidateGuard(tmp_path, disk_reserve=5 * 1024**3)
    guard.own_roots = (fake,); guard.known_own[fake.pid] = fake
    monkeypatch.setattr(runner, 'process_table', lambda: {fake.pid: (1, 2.)})
    with pytest.raises(ResourceLimit, match='reused'): guard.check()
    killed = []
    monkeypatch.setattr(common, 'process_table', lambda: {fake.pid: (1, 2.)})
    monkeypatch.setattr(common.psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 2., kill=lambda: killed.append(pid)))
    guard.terminate_owned()
    assert killed == []


@pytest.mark.parametrize('operation', ['freeze', 'run'])
@pytest.mark.parametrize('target', ['runtime_hash', 'source_scan'])
@pytest.mark.parametrize('failure', ['deadline', 'read_error'])
def test_preflight_is_bounded_and_fails_before_child_or_reservation(tmp_path, monkeypatch, operation, target, failure):
    import time
    import audio_backend.qwen_candidate_runner as runner
    frozen, _ = frozen_fixture(tmp_path)
    frozen['limits']['preflight_seconds'] = 1
    path = tmp_path / 'preflight.json'; atomic_json(path, frozen)
    launches = []
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: launches.append(a))
    def interrupt():
        if failure == 'deadline': time.sleep(1.05)
        else: raise OSError('synthetic bounded preflight read failure')
    if target == 'runtime_hash':
        original = runner._hash_read
        def read(stream):
            interrupt(); return original(stream)
        monkeypatch.setattr(runner, '_hash_read', read)
    else:
        original = wave.Wave_read.readframes
        def frames(self, count):
            interrupt(); return original(self, count)
        monkeypatch.setattr(wave.Wave_read, 'readframes', frames)
    expected = runner.ResourceLimit if failure == 'deadline' else OSError
    with pytest.raises(expected):
        if operation == 'freeze':
            runner.freeze(SourcePlan.from_json(frozen['source_plan']), frozen['source_bindings'],
                          frozen['runtime'], frozen['limits'], source_scope='public-synthetic')
        else:
            runner.run(path, digest(frozen), {'frozen_sha256': digest(frozen), 'authorization_reference': 'preflight-failure-test'}, tmp_path / 'out')
    assert launches == []
    assert not (tmp_path / 'out/qwen-candidates.sqlite3').exists()
    assert runner._PREFLIGHT.get() is None


def test_unsupported_cleanup_reserve_is_rejected_before_preflight_child(tmp_path, monkeypatch):
    import audio_backend.qwen_candidate_runner as runner
    frozen, _ = frozen_fixture(tmp_path); frozen['limits']['cleanup_write_bytes'] = 65536
    path = tmp_path / 'too-small-cleanup.json'; atomic_json(path, frozen)
    launches = []; monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: launches.append(a))
    with pytest.raises(ValueError, match='cleanup reserve'):
        runner.run(path, digest(frozen), {'frozen_sha256': digest(frozen), 'authorization_reference': 'admission-test'}, tmp_path / 'out')
    assert launches == []
