"""Independent runner boundary tests, with real local journals and strict ASGI.

The ASGI case stops before sealing/native work. Synthetic peers in other cases
qualify only bounded orchestration and exact resume, not deployment or inference.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from audio_backend import recorder_capture_intake as engine
from audio_backend import recorder_incremental_span_intake as intake
from audio_backend import recorder_inbox_runner as runner
from audio_backend import recorder_intake as single
from audio_backend import recording_uploader as u
from audio_backend.recorder_delivery import inspect_delivery
from test_extended_recorder_intake import Peer
from test_recorder_inbox import delivery
from test_recording_session_contract import HEADERS, handler_app, session
from test_strict_uploader_admission import AdmissionBoundary, StrictRoutes
from test_recorder_span_status import Routes


class Crash(BaseException):
    """A process-stop boundary, deliberately not caught as an ordinary error."""


class LoggedPeer(Peer):
    def __init__(self):
        super().__init__()
        self.calls = []

    def request(self, method, path, **kwargs):
        assert not (method == 'POST' and path == '/sessions')
        self.calls.append((method, path))
        return super().request(method, path, **kwargs)


def example(tmp_path, *, count=1, terminal='ROTATION', sid='known'):
    inbox = tmp_path / 'inbox'
    inbox.mkdir(mode=0o700)
    paths = [delivery(inbox, 23 + index, epoch=7, start=1000 + 4 * index,
        boundary='RESTART', run='review-process', seam=index > 0,
        close=terminal if index == count - 1 else 'ROTATION') for index in range(count)]
    first = inspect_delivery(paths[0], validate_metadata=True)
    record = engine.candidate_record(paths[0], first, 0, lambda: 30)
    config = dict(schema='recorder-inbox-runner-config-v1', inbox_dir=str(inbox),
        job_dir=str(tmp_path / 'job'), output_dir=None,
        api='http://127.0.0.1:8765', session_id=sid, capture_id='extended',
        span_origin=intake.POLICY.header_fields(record)['span_origin'],
        max_hours=12, max_inputs=192)
    state = tmp_path / 'runner'
    return paths, config, state


def invoke(state, peer, **kwargs):
    options = dict(state_dir=state, exclusive_writer=True,
        acknowledge_span_local_closure=True, transport=peer,
        limits=u.Limits(disk_floor=0, deadline_seconds=120, poll_seconds=.001),
        deadline_seconds=120, max_actions=1)
    options.update(kwargs)
    return runner.run(**options)


def file_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def alternative(path, suffix='1'):
    target = path.with_name(path.name[:-32] + suffix * 32)
    shutil.copytree(path, target)
    marker = target / 'delivery.v1.txt'
    marker.write_text(marker.read_text().replace(path.name, target.name))
    return target


@pytest.mark.parametrize('lose_put', [False, True])
def test_runner_actual_asgi_admission_and_exact_seal_boundary_resume(tmp_path, lose_put):
    with handler_app(tmp_path / 'server') as (client, app):
        sid = session(client)
        paths, config, state = example(tmp_path, count=9, terminal='INTERRUPTION', sid=sid)
        before = file_bytes(Path(config['inbox_dir']))
        runner.configure(state_dir=state, config=config)
        routes = StrictRoutes(client, lose_put)
        invoke(state, routes, max_actions=9)
        assert all(method == 'GET' for method, _, _ in routes.calls)
        assert len(list((Path(config['job_dir']) / 'admissions').iterdir())) == 9
        with pytest.raises(AdmissionBoundary):
            invoke(state, routes, finalize=True)
        assert len(routes.puts) == 9 and len({key for key, _ in routes.puts}) == 9
        saved = json.loads((Path(config['job_dir']) / 'uploader/uploader.json').read_bytes())
        assert saved['finalize_requested']
        assert saved['active_page']['seal']['eos'] is True
        assert saved['active_page']['seal']['end_frame'] == 36
        exact_puts = list(routes.puts)
        with pytest.raises(AdmissionBoundary):
            invoke(state, routes, finalize=True)
        assert routes.puts == exact_puts
        page = client.get(f'/sessions/{sid}/recordings/extended/pages/1', headers=HEADERS).json()
        assert len(page['sources']) == 9 and not page['released']
        assert before == file_bytes(Path(config['inbox_dir']))
        with app.state.store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 1
            assert db.execute('SELECT COUNT(*) FROM recording_work').fetchone()[0] == 0
        assert not app.state.worker.thread.is_alive()


def test_runner_crash_after_intake_return_replays_exact_selected_path(tmp_path, monkeypatch):
    paths, config, state = example(tmp_path, count=2)
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    real = intake.run
    calls = []
    def stop_after_intake(**kwargs):
        calls.append(str(kwargs['delivery']))
        real(**kwargs)
        raise Crash()
    with monkeypatch.context() as patch:
        patch.setattr(intake, 'run', stop_after_intake)
        with pytest.raises(Crash): invoke(state, peer)
    assert calls == [str(paths[0])]
    alternative(paths[0])
    with monkeypatch.context() as patch:
        def observe(**kwargs):
            calls.append(str(kwargs['delivery']))
            return real(**kwargs)
        patch.setattr(intake, 'run', observe)
        invoke(state, peer, max_actions=2)
    assert calls == [str(paths[0]), str(paths[0]), str(paths[1])]
    admissions = sorted((Path(config['job_dir']) / 'admissions').iterdir())
    assert [json.loads((p / 'admission.json').read_bytes())['path'] for p in admissions] == list(map(str, paths))


def test_missing_selected_original_is_not_replaced_by_identical_attempt(tmp_path, monkeypatch):
    paths, config, state = example(tmp_path)
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    with monkeypatch.context() as patch:
        patch.setattr(intake, 'run', lambda **kwargs: (_ for _ in ()).throw(Crash()))
        with pytest.raises(Crash): invoke(state, peer)
    alternate = alternative(paths[0])
    retained = tmp_path / 'retained-original'
    paths[0].rename(retained)
    before = file_bytes(state)
    with pytest.raises((single.Refused, OSError, u.Incomplete)):
        invoke(state, peer)
    assert not peer.calls and file_bytes(state) == before
    assert alternate.exists() and retained.exists()


def test_duplicate_new_attempt_requires_explicit_selection(tmp_path):
    paths, config, state = example(tmp_path)
    other = alternative(paths[0])
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    with pytest.raises(single.Refused): invoke(state, peer)
    assert not peer.calls and not Path(config['job_dir']).exists()
    invoke(state, peer, select_attempt=other.name)
    receipt = json.loads((Path(config['job_dir']) / 'admissions/0/admission.json').read_bytes())
    assert receipt['path'] == str(other)


def test_conflicting_attempt_cannot_be_overridden_by_selection(tmp_path):
    paths, config, state = example(tmp_path)
    staging = tmp_path / 'alternate-inbox'
    staging.mkdir(mode=0o700)
    changed = delivery(staging, 23, epoch=7, start=1000, boundary='RESTART',
        run='review-process', seam=False, close='STOP')
    other = alternative(changed)
    other.rename(Path(config['inbox_dir']) / other.name)
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    with pytest.raises(single.Refused): invoke(state, peer, select_attempt=paths[0].name)
    assert not peer.calls and not Path(config['job_dir']).exists()


def test_unresolved_attempt_blocks_all_intake_without_guessing_its_identity(tmp_path):
    paths, config, state = example(tmp_path)
    pending = Path(config['inbox_dir']) / ('r-' + 'f' * 64 + '-' + '2' * 32)
    pending.mkdir()
    (pending / 'audio.wav').write_bytes(b'partial')
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    with pytest.raises(single.Refused): invoke(state, peer)
    assert not peer.calls and not Path(config['job_dir']).exists()
    assert (pending / 'audio.wav').read_bytes() == b'partial'


def test_gap_is_waited_for_without_renumbering_or_premature_eos(tmp_path):
    paths, config, state = example(tmp_path, count=3, terminal='STOP')
    middle = tmp_path / 'later-arrival'
    paths[1].rename(middle)
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    invoke(state, peer, max_actions=3, finalize=True)
    assert len(list((Path(config['job_dir']) / 'admissions').iterdir())) == 1
    assert not peer.eos and not any(method != 'GET' for method, _ in peer.calls)
    middle.rename(paths[1])
    invoke(state, peer, max_actions=2)
    assert len(list((Path(config['job_dir']) / 'admissions').iterdir())) == 3
    assert not peer.eos
    invoke(state, peer, finalize=True)
    assert peer.eos
    receipts = [json.loads((Path(config['job_dir']) / f'admissions/{i}/admission.json').read_bytes()) for i in range(3)]
    assert [r['delivery']['sequence'] for r in receipts] == [23, 24, 25]
    assert [r['delivery']['frame_start'] for r in receipts] == [1000, 1004, 1008]


@pytest.mark.parametrize('value', [False, True, 1.0, float('nan')])
def test_config_origin_exact_integer_types_fail_before_state_creation(tmp_path, value):
    _, config, state = example(tmp_path)
    config['span_origin']['first_sequence'] = value
    with pytest.raises((single.Refused, ValueError)):
        runner.configure(state_dir=state, config=config)
    assert not state.exists()


@pytest.mark.parametrize('field,value', [('max_actions', True), ('max_actions', 196),
    ('max_requests', False), ('max_network_writes', -1), ('deadline_seconds', float('nan'))])
def test_run_bounds_fail_before_any_remote_request(tmp_path, field, value):
    _, config, state = example(tmp_path)
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    before = file_bytes(state)
    with pytest.raises((single.Refused, ValueError)):
        invoke(state, peer, **{field: value})
    assert not peer.calls and before == file_bytes(state)


def test_symlinked_original_attempt_is_never_followed(tmp_path):
    paths, config, state = example(tmp_path)
    moved = tmp_path / 'elsewhere'
    paths[0].rename(moved)
    paths[0].symlink_to(moved, target_is_directory=True)
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    with pytest.raises((single.Refused, OSError)): invoke(state, peer)
    assert not peer.calls and not Path(config['job_dir']).exists()


def test_existing_output_is_not_overwritten_by_configuration(tmp_path):
    _, config, state = example(tmp_path)
    output = tmp_path / 'output'
    output.mkdir(mode=0o700)
    (output / 'sentinel').write_bytes(b'preserve')
    config['output_dir'] = str(output)
    with pytest.raises((single.Refused, OSError)):
        runner.configure(state_dir=state, config=config)
    assert not state.exists() and (output / 'sentinel').read_bytes() == b'preserve'


def test_unjournaled_external_intake_progress_is_not_adopted(tmp_path):
    paths, config, state = example(tmp_path, count=2)
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    invoke(state, peer)
    intake.run(job_dir=config['job_dir'], api=config['api'], session_id=config['session_id'],
        capture_id=config['capture_id'], delivery=paths[1], exclusive_writer=True,
        acknowledge_span_local_closure=True, transport=peer,
        limits=u.Limits(disk_floor=0, deadline_seconds=120))
    peer.calls.clear()
    before = file_bytes(state)
    with pytest.raises((single.Refused, u.Incomplete)):
        invoke(state, peer)
    assert not peer.calls and before == file_bytes(state)


def test_unknown_runner_evidence_is_preserved_and_refused(tmp_path):
    _, config, state = example(tmp_path)
    runner.configure(state_dir=state, config=config)
    unknown = state / 'unrecognized.partial'
    unknown.write_bytes(b'preserve uncertain prior evidence')
    unknown.chmod(0o600)
    peer = LoggedPeer()
    before = file_bytes(state)
    with pytest.raises((single.Refused, u.Incomplete)):
        invoke(state, peer)
    assert not peer.calls and before == file_bytes(state)


@pytest.mark.parametrize('changes', [dict(exclusive_writer=False),
    dict(exclusive_writer=1), dict(acknowledge_span_local_closure=False),
    dict(acknowledge_span_local_closure=1)])
def test_every_run_requires_exact_explicit_acknowledgments(tmp_path, changes):
    _, config, state = example(tmp_path)
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    before = file_bytes(state)
    with pytest.raises(single.Refused): invoke(state, peer, **changes)
    assert not peer.calls and before == file_bytes(state)


def test_runner_lock_excludes_second_invocation_without_new_evidence(tmp_path):
    _, config, state = example(tmp_path)
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    before = file_bytes(state)
    with (state / 'runner.lock').open('rb') as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError): invoke(state, peer)
    assert not peer.calls and before == file_bytes(state)


def test_failed_intent_fsync_never_reaches_http_and_resume_fsyncs_again(tmp_path, monkeypatch):
    _, config, state = example(tmp_path)
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    real_sync = os.fsync
    intent_path = state / 'input-000.intent.json'
    def failed(fd):
        if os.readlink(f'/proc/self/fd/{fd}') == str(intent_path):
            raise OSError('synthetic fsync failure')
        return real_sync(fd)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', failed)
        with pytest.raises(OSError): invoke(state, peer)
    assert not peer.calls and intent_path.exists()
    observed = []
    def success(fd):
        observed.append(os.readlink(f'/proc/self/fd/{fd}'))
        return real_sync(fd)
    original_request = peer.request
    def network(method, path, **kwargs):
        assert str(intent_path) in observed
        return original_request(method, path, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', success)
        patch.setattr(peer, 'request', network)
        invoke(state, peer)
    assert (state / 'input-000.done.json').exists()


def exported_files(tmp_path):
    output = tmp_path / 'export'
    output.mkdir(mode=0o700)
    artifacts = {}
    for name, raw in [('export.json', b'{}\n'), ('transcript.txt', b'Synthetic evidence only\n')]:
        target = output / name
        target.write_bytes(raw)
        target.chmod(0o600)
        artifacts[name] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    manifest = dict(schema=runner.exporter.SCHEMA, status='exported', artifacts=artifacts)
    marker = output / 'complete.json'
    marker.write_bytes(single._json(manifest))
    marker.chmod(0o600)
    intent = dict(schema=runner.SCHEMA, config_sha256='a' * 64,
        kind='export', input_intents_sha256='b' * 64)
    return output, dict(output_dir=str(output)), intent


def test_export_receipt_checks_shared_cancellation_during_hashing(tmp_path):
    output, config, intent = exported_files(tmp_path)
    before = file_bytes(output)
    class Guard:
        calls = 0
        def check(self):
            self.calls += 1
            if self.calls == 5: raise InterruptedError('synthetic cancellation during export read')
    guard = Guard()
    with pytest.raises(InterruptedError): runner._export_receipt(tmp_path, config, intent, guard)
    assert guard.calls == 5 and before == file_bytes(output)


def test_export_receipt_growth_is_bounded_and_refused(tmp_path, monkeypatch):
    output, config, intent = exported_files(tmp_path)
    real_read = os.read
    reads = []
    def growing(fd, size):
        raw = real_read(fd, size)
        if os.readlink(f'/proc/self/fd/{fd}') == str(output / 'transcript.txt'):
            reads.append(size)
            if len(reads) == 1:
                with (output / 'transcript.txt').open('ab') as stream: stream.write(b'unexpected growth')
        return raw
    with monkeypatch.context() as patch:
        patch.setattr(os, 'read', growing)
        with pytest.raises(single.Refused, match='artifact_changed'):
            runner._export_receipt(tmp_path, config, intent, u.Guard(u.Limits(disk_floor=0)))
    assert reads == [len(b'Synthetic evidence only\n'), 1]


@pytest.mark.parametrize('change', ['symlink', 'hardlink', 'public', 'oversized_manifest', 'extra_file'])
def test_export_receipt_rejects_unsafe_or_ambiguous_artifacts(tmp_path, change):
    output, config, intent = exported_files(tmp_path)
    artifact = output / 'transcript.txt'
    if change == 'symlink':
        moved = tmp_path / 'moved'
        artifact.rename(moved)
        artifact.symlink_to(moved)
    elif change == 'hardlink': os.link(artifact, tmp_path / 'linked')
    elif change == 'public': artifact.chmod(0o644)
    elif change == 'oversized_manifest': (output / 'complete.json').write_bytes(b' ' * (single.MAX_MANIFEST + 1))
    else: (output / 'extra.partial').write_bytes(b'preserve')
    with pytest.raises((single.Refused, OSError)):
        runner._export_receipt(tmp_path, config, intent, u.Guard(u.Limits(disk_floor=0)))


def test_export_receipt_aggregate_byte_cap_is_not_per_artifact(tmp_path, monkeypatch):
    output, config, intent = exported_files(tmp_path)
    monkeypatch.setattr(runner.exporter, 'MAX_OUTPUT_BYTES', len((output / 'transcript.txt').read_bytes()))
    with pytest.raises(single.Refused, match='artifact_bound'):
        runner._export_receipt(tmp_path, config, intent, u.Guard(u.Limits(disk_floor=0)))


def test_runner_actual_asgi_status_and_empty_primary_export_are_get_only(tmp_path):
    """Real readers, explicitly synthetic publication/analysis, no native worker."""
    with handler_app(tmp_path / 'server') as (client, app):
        sid = session(client)
        paths, config, state = example(tmp_path, terminal='STOP', sid=sid)
        config['output_dir'] = str(tmp_path / 'export')
        runner.configure(state_dir=state, config=config)
        class TargetPeer(LoggedPeer):
            def request(self, method, path, **kwargs):
                value = super().request(method, path.replace('/sessions/' + sid, '/sessions/known'), **kwargs)
                if path.endswith('/recording-contract'): value['session_id'] = sid
                return value
        peer = TargetPeer()
        invoke(state, peer, max_actions=2, finalize=True)
        job = Path(config['job_dir'])
        page = json.loads((job / 'uploader/uploader.history/page-0001.json').read_bytes())['payload']
        with app.state.store.db() as db:
            bid, pid, cid = 'review-synthetic-batch', page['server_id'], 'review-synthetic-work'
            db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,mode,state,revision,
                capture_origin_frame,capture_end_frame,eos,created)
                VALUES(?,?,?,'sealed_pages','queued',1,0,4,1,0)''', (bid, sid, 'extended'))
            db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,revision,state,released,
                start_frame,end_frame,eos,created) VALUES(?,?,1,?,'published',1,0,4,1,0)''',
                (pid, bid, page['seal']['expected_revision']))
            for piece in page['pieces']:
                db.execute('''INSERT INTO recording_sources(id,batch_id,page_id,idempotency_key,digest,
                    path,frames,bytes,source_start_frame,format,created) VALUES(?,?,?,?,?,?,?,?,?,?,0)''',
                    (piece['server_id'], bid, pid, piece['key'], piece['digest'], '/synthetic/source',
                     piece['frames'], piece['bytes'], piece['capture_start'], 'wav-pcm16le-16000-mono'))
            db.execute('''INSERT INTO chunks(rowid,id,session_id,idempotency_key,digest,path,duration,
                timeline_start,status,next_offset,created,error) VALUES(100,?,?,?,?,?,0.00025,0,'completed',0.00025,0,NULL)''',
                (cid, sid, cid, 'c' * 64, '/synthetic/work'))
            db.execute('''INSERT INTO recording_work(chunk_id,batch_id,page_id,position,frames,
                bytes,capture_start_frame,native_receipt_status,receipt_state)
                VALUES(?,?,?,0,4,52,0,'disabled','gap')''', (cid, bid, pid))
        routes = Routes(client)
        source_before = file_bytes(Path(config['inbox_dir']))
        job_before = file_bytes(job)
        result = invoke(state, routes, export=True, max_actions=3)
        assert result['status'] == 'exported' and result['export_completed']
        document = json.loads((Path(config['output_dir']) / 'export.json').read_bytes())
        assert document['completion']['status'] == 'completed'
        assert document['completion']['span']['original_close_reason'] == 'STOP'
        assert document['summary']['text_status'] == 'no_primary_text_observed'
        assert not document['complete_content'] and document['eos'] is None
        assert all(method == 'GET' for method, _ in routes.calls)
        before_requests = list(routes.calls)
        assert invoke(state, routes, export=True)['status'] == 'exported'
        assert routes.calls == before_requests
        assert source_before == file_bytes(Path(config['inbox_dir'])) and job_before == file_bytes(job)
        assert not app.state.worker.thread.is_alive()
