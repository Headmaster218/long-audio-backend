"""Span-local closure: real strict admission, separate simulated completion.

Strict handler tests stop before sealing/native work. Peer tests exercise full
client returns and exact replay, NOT actual strict/native publication.
"""
import fcntl
import json

import pytest

from audio_backend import recorder_span_intake as span
from audio_backend import recording_uploader as u
from test_extended_recorder_intake import Peer
from test_recorder_sequence import fixture as segment
from test_recording_session_contract import handler_app, session, HEADERS
from test_strict_uploader_admission import StrictRoutes, AdmissionBoundary


def options(tmp_path, *, count=3, sequence=11, epoch=7, start=100003,
            close='INTERRUPTION', boundary='RESTART', run='restarted'):
    paths = []
    for index in range(count):
        path = tmp_path / ('delivery' + str(index))
        segment(path, sequence + index, epoch=epoch, start=start + 4 * index,
                run=run, boundary=boundary, seam=index > 0,
                close=close if index == count - 1 else 'ROTATION')
        paths.append(path)
    return dict(deliveries=paths, job_dir=tmp_path/'job', api='http://127.0.0.1:8765',
        session_id='known', capture_id='extended', exclusive_writer=True,
        acknowledge_span_local_closure=True,
        limits=u.Limits(disk_floor=0, deadline_seconds=30, poll_seconds=.001))


def originals(args):
    return [{p.name: p.read_bytes() for p in path.iterdir()} for path in args['deliveries']]


def saved(args):
    return json.loads((args['job_dir']/'uploader/uploader.json').read_text())


@pytest.mark.parametrize('loss', ['none', 'response', 'process'])
def test_real_strict_mid_put_reconcile_nonzero_original_coordinates(tmp_path, loss):
    # Large epoch coordinates are evidence only, never backend source offsets.
    args = options(tmp_path, start=2**63 - 1 - 12)
    before = originals(args)
    class Interrupted(BaseException): pass
    class Routes(StrictRoutes):
        interrupted = False
        def request(self, method, path, **kwargs):
            if method != 'GET':
                manifest = span.single.manifest_read(args['job_dir']/'manifest.json')
                assert manifest['acknowledgments']['span_local_closure'] is True
                assert manifest['span']['eos_meaning'] == 'selected_span_only'
            result = super().request(method, path, **kwargs)
            if method == 'PUT' and len(self.puts) == 2 and not self.interrupted and loss != 'none':
                self.interrupted = True
                if loss == 'process': raise Interrupted()
                raise u.Uncertain('Lost real second PUT response')
            return result
    with handler_app(tmp_path/'server') as (client, app):
        routes = Routes(client); args.update(session_id=session(client), transport=routes)
        if loss == 'process':
            with pytest.raises(Interrupted): span.run(**args)
            assert len(routes.puts) == 2
            with pytest.raises(AdmissionBoundary): span.run(**args, resume=True)
        else:
            with pytest.raises(AdmissionBoundary): span.run(**args)
        state = saved(args)
        assert state['policy'] == u.APPEND_POLICY
        assert [e['normalized_start'] for e in state['inputs']] == [0, 4, 8]
        assert [e['id'].split('-')[-1] for e in state['inputs']] == ['11.ready', '12.ready', '13.ready']
        assert state['finalize_requested'] and state['pages'][0]['seal']['eos'] is True
        assert state['pages'][0]['seal']['end_frame'] == 12
        manifest = span.single.manifest_read(args['job_dir']/'manifest.json')
        assert manifest['span']['original_epoch'] == 7
        assert manifest['span']['original_epoch_frame_start'] == 2**63 - 1 - 12
        assert manifest['span']['original_close_reason'] == 'INTERRUPTION'
        assert not manifest['span']['recorder_stop_observed']
        page = client.get(f"/sessions/{args['session_id']}/recordings/extended/pages/1", headers=HEADERS).json()
        assert [s['source_start_frame'] for s in page['sources']] == [0, 4, 8]
        assert len(page['sources']) == len(routes.puts) == 3
        puts = list(routes.puts)
        with pytest.raises(AdmissionBoundary): span.run(**args, resume=True)
        assert routes.puts == puts and originals(args) == before
        assert not app.state.worker.thread.is_alive()
        with app.state.store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 1
            assert db.execute('SELECT COUNT(*) FROM recording_work').fetchone()[0] == 0


@pytest.mark.parametrize('close,count', [('STOP', 3), ('INTERRUPTION', 3), ('RECOVERED_TAIL', 1)])
def test_simulated_full_span_return_and_exact_repeated_resume(tmp_path, close, count):
    args = options(tmp_path, close=close, count=count)
    peer = Peer(); args['transport'] = peer
    before = originals(args)
    result = span.run(**args)
    assert result['status'] == 'selected_span_accepted_with_eos'
    assert result['span']['original_close_reason'] == close
    assert result['span']['recorder_stop_observed'] is (close == 'STOP')
    assert result['span']['recovered_tail_uncertain'] is (close == 'RECOVERED_TAIL')
    assert result['span']['eos_meaning'] == 'selected_span_only'
    assert result['span']['before_span_gap_frames'] is None
    assert result['span']['after_span_gap_frames'] is None
    assert not result['analysis_completion_verified'] and not result['capture_completeness_proven']
    assert not result['physical_continuity_proven'] and peer.eos
    assert result['warning_codes'] == ['anchor_observations_absent']
    puts = list(peer.puts)
    assert span.run(**args, resume=True) == result
    assert span.run(**args, resume=True) == result
    assert peer.puts == puts and len(puts) == count
    assert saved(args)['complete'] and not saved(args)['spool']
    assert originals(args) == before


@pytest.mark.parametrize('kind,reason', [
    ('missing', 'missing_ordinals'), ('epoch', 'epoch_boundary_unmapped'),
    ('gap', 'declared_frame_gap'), ('overlap', 'declared_frame_overlap'),
    ('seam', 'previous_seam_not_trusted'), ('recovered', 'recovered_tail'),
    ('run', 'same_epoch_declaration_conflict'), ('previous_stop', 'previous_segment_not_rotation'),
    ('duplicate', 'span_conflicting_or_duplicate_ordinal'),
    ('conflict', 'span_conflicting_or_duplicate_ordinal'),
    ('other_capture', 'span_multiple_capture_identities'),
    ('order', 'span_explicit_sequence_order')])
def test_cross_boundary_refused_before_http(tmp_path, kind, reason):
    first = tmp_path/'first'; second = tmp_path/'second'
    segment(first, 10, start=40, close='STOP' if kind == 'previous_stop' else 'ROTATION')
    changes = dict(start=44, close='STOP')
    seq = 11
    if kind == 'missing': seq = 12
    if kind == 'epoch': changes.update(epoch=1, start=0, boundary='RESTART', run='new', seam=False)
    if kind == 'gap': changes['start'] = 45
    if kind == 'overlap': changes['start'] = 43
    if kind == 'seam': changes['seam'] = False
    if kind == 'recovered': changes.update(close='RECOVERED_TAIL', seam=False)
    if kind == 'run': changes['run'] = 'new'
    if kind == 'other_capture': changes['capture'] = 'another'
    if kind in ('duplicate', 'conflict'):
        seq = 10; changes['start'] = 40
        if kind == 'duplicate': changes['close'] = 'ROTATION'
    segment(second, seq, **changes)
    class Forbidden:
        def request(self, *a, **kw): raise AssertionError('No HTTP for rejected span')
    paths = [second, first] if kind == 'order' else [first, second]
    with pytest.raises(span.Refused) as error:
        span.run(deliveries=paths, job_dir=tmp_path/'job', api='https://example.org',
            session_id='s', capture_id='c', exclusive_writer=True,
            acknowledge_span_local_closure=True, transport=Forbidden())
    assert reason in str(error.value) or reason in getattr(error.value, 'reasons', [])
    assert not (tmp_path/'job/manifest.json').exists()


@pytest.mark.parametrize('close', ['STOP', 'INTERRUPTION', 'RECOVERED_TAIL'])
def test_actual_strict_single_epoch_restart_admission(tmp_path, close):
    args = options(tmp_path, count=1, sequence=44, epoch=23, start=0, close=close)
    with handler_app(tmp_path/'server') as (client, app):
        routes = StrictRoutes(client); args.update(session_id=session(client), transport=routes)
        with pytest.raises(AdmissionBoundary): span.run(**args)
        assert len(routes.puts) == 1 and saved(args)['pages'][0]['seal']['eos'] is True
        assert not app.state.worker.thread.is_alive()


@pytest.mark.parametrize('change', ['target', 'session', 'origin', 'order', 'selection', 'closure',
                                   'lost_journal', 'partial_journal', 'registry', 'ack', 'snapshot'])
def test_fixed_manifest_and_exact_intact_journal_required(tmp_path, change):
    args = options(tmp_path)
    with handler_app(tmp_path/'server') as (client, app):
        routes = StrictRoutes(client); args.update(session_id=session(client), transport=routes)
        with pytest.raises(AdmissionBoundary): span.run(**args)
        state = args['job_dir']/'uploader/uploader.json'
        if change == 'target': args['capture_id'] = 'different'
        elif change == 'session': args['session_id'] = 'different'
        elif change == 'origin': args['api'] = 'https://other.example'
        elif change == 'order': args['deliveries'].reverse()
        elif change == 'selection': args['deliveries'] = args['deliveries'][1:]
        elif change == 'closure':
            replacement = tmp_path/'replacement'
            segment(replacement, 13, start=100011, epoch=7, run='restarted', boundary='RESTART', close='STOP')
            for name in ('delivery.v1.txt', 'segment.v1.bin'):
                (args['deliveries'][-1]/name).write_bytes((replacement/name).read_bytes())
        elif change == 'lost_journal': state.rename(state.with_suffix('.lost'))
        elif change == 'partial_journal': state.write_bytes(b'{')
        elif change == 'registry':
            data = saved(args); data['inputs'][0]['identity']['path'] += '.other'; state.write_text(json.dumps(data))
        elif change == 'ack': args['acknowledge_span_local_closure'] = False
        else: (args['job_dir']/'snapshots/0/audio.wav').write_bytes(b'changed')
        before = len(routes.calls)
        with pytest.raises((span.Refused, u.Incomplete, FileNotFoundError)):
            span.run(**args, resume=True)
        assert all(method == 'GET' for method, _, _ in routes.calls[before:])
        assert len(routes.puts) == 3


@pytest.mark.parametrize('occupied', ['same_capture', 'other_capture', 'chunks'])
def test_known_empty_session_required_before_media(tmp_path, occupied):
    args = options(tmp_path)
    with handler_app(tmp_path/'server') as (client, app):
        sid = session(client)
        if occupied != 'chunks':
            capture = 'extended' if occupied == 'same_capture' else 'foreign'
            assert client.post(f'/sessions/{sid}/recordings/{capture}/pages',
                json={'page_number':1, 'capture_origin_frame':0}, headers=HEADERS).status_code == 202
        class Routes(StrictRoutes):
            def request(self, method, path, **kwargs):
                result = super().request(method, path, **kwargs)
                if occupied == 'chunks' and path.endswith('/recording-contract'): result['has_chunks'] = True
                return result
        routes = Routes(client); args.update(session_id=sid, transport=routes)
        with pytest.raises(span.Refused, match='session_not_empty'): span.run(**args)
        assert all(method == 'GET' for method, _, _ in routes.calls) and not routes.puts
        # Even this pre-journal failure is preserved, never adopted as a new job.
        with pytest.raises(FileNotFoundError): span.run(**args, resume=True)


def test_actual_uploader_lock_held_before_resume_inspection(tmp_path):
    args = options(tmp_path); peer = Peer(); args['transport'] = peer
    span.run(**args)
    with (args['job_dir']/'uploader/uploader.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError): span.run(**args, resume=True)
    assert span.run(**args, resume=True)['frames'] == 12


@pytest.mark.parametrize('count', [0, 9])
def test_count_refuses_before_inspection(tmp_path, count):
    with pytest.raises(span.Refused, match='input_count'):
        span.run(deliveries=[None]*count, job_dir=tmp_path/'job', api='https://example.org',
            session_id='s', capture_id='c', exclusive_writer=True, acknowledge_span_local_closure=True)
    assert not (tmp_path/'job').exists()


@pytest.mark.parametrize('missing', ['exclusive_writer', 'acknowledge_span_local_closure'])
def test_both_operator_acknowledgments_required(tmp_path, missing):
    args = options(tmp_path); args[missing] = False
    with pytest.raises(span.Refused, match='ack_required'): span.run(**args)
    assert not args['job_dir'].exists()


def test_rotation_tail_refused_without_http(tmp_path):
    args = options(tmp_path, count=1, close='ROTATION')
    with pytest.raises(span.Refused, match='terminal_closure'): span.run(**args)
    assert not (args['job_dir']/'manifest.json').exists()


def test_maximum_eight_inputs_canonical_manifest(tmp_path):
    args = options(tmp_path, count=8); peer = Peer(); args['transport'] = peer
    result = span.run(**args)
    manifest = span.single.manifest_read(args['job_dir']/'manifest.json')
    assert len(manifest['inputs']) == result['inputs'] == 8
    assert (args['job_dir']/'manifest.json').stat().st_size <= span.single.MAX_MANIFEST
    assert [row['span_start_frame'] for row in manifest['inputs']] == list(range(0, 32, 4))
    assert span.run(**args, resume=True) == result


def test_configured_input_bound_before_snapshot_or_http(tmp_path):
    args = options(tmp_path); args['limits'] = u.Limits(output_frames=11, disk_floor=0)
    with pytest.raises(span.Refused, match='configured_input_limit'): span.run(**args)
    assert not args['job_dir'].exists()


def test_cli_required_ack_and_safe_boundary_report(monkeypatch, capsys):
    args = ['--delivery', 'a', '--job-dir', 'j', '--api', 'https://example.org',
            '--session-id', 's', '--capture-id', 'c', '--exclusive-writer']
    with pytest.raises(SystemExit) as error: span.main(args)
    assert error.value.code == 2
    capsys.readouterr()
    def refused(**kwargs): raise span.BoundaryRefused(['missing_ordinals', 'epoch_boundary_unmapped'])
    monkeypatch.setattr(span, 'run', refused)
    assert span.main(args + ['--acknowledge-span-local-closure']) == 2
    result = json.loads(capsys.readouterr().err)
    assert result['status'] == 'unresolved' and result['eos_meaning'] == 'selected_span_only'
    assert result['boundary_reason_codes'] == ['epoch_boundary_unmapped', 'missing_ordinals']


@pytest.mark.parametrize('stage', ['manifest', 'active_piece'])
def test_true_process_exit_and_exact_restart(tmp_path, stage):
    """Separate interpreter exits without cleanup; this is not a power-loss test."""
    import os
    from pathlib import Path
    import subprocess
    import sys
    args = options(tmp_path)
    before = originals(args)
    script = r'''
import json, os, sys
from audio_backend import recorder_span_intake as span
from audio_backend import recording_uploader as u
from test_extended_recorder_intake import Peer
args = json.loads(sys.argv[1]); stage = sys.argv[2]
args['limits'] = u.Limits(disk_floor=0, deadline_seconds=30, poll_seconds=.001)
class ExitBeforeJournal(Peer):
    def request(self, *a, **kw): os._exit(76)
args['transport'] = ExitBeforeJournal() if stage == 'manifest' else Peer()
if stage == 'active_piece':
    original = span.SpanUploader._new_piece
    def exit_after_durable_piece(self, row):
        result = original(self, row)
        if result is not None: os._exit(77)
        return result
    span.SpanUploader._new_piece = exit_after_durable_piece
span.run(**args)
'''
    child_args = {key: value for key, value in args.items() if key != 'limits'}
    child_args['deliveries'] = list(map(str, child_args['deliveries']))
    child_args['job_dir'] = str(child_args['job_dir'])
    source_root = Path(__file__).resolve().parents[1]
    pythonpath = os.pathsep.join((str(source_root), str(source_root/'tests'),
                                 os.environ.get('PYTHONPATH', '')))
    child = subprocess.run([sys.executable, '-c', script, json.dumps(child_args), stage],
        cwd=source_root, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=pythonpath),
        capture_output=True, timeout=30)
    assert child.returncode == (76 if stage == 'manifest' else 77), child.stderr.decode()
    manifest = span.single.manifest_read(args['job_dir']/'manifest.json')
    assert manifest['span']['original_close_reason'] == 'INTERRUPTION'
    if stage == 'manifest':
        retained = {str(p): p.read_bytes() for p in args['job_dir'].rglob('*') if p.is_file()}
        args['transport'] = Peer()
        with pytest.raises(FileNotFoundError): span.run(**args, resume=True)
        assert retained == {str(p): p.read_bytes() for p in args['job_dir'].rglob('*') if p.is_file()}
        assert not args['transport'].pages
    else:
        interrupted = saved(args)
        assert len(interrupted['inputs']) == 1 and not interrupted['inputs'][0]['clean_eof']
        assert interrupted['inputs'][0]['frames'] == 4 and not interrupted['pages'][0].get('create')
        peer = Peer(); args['transport'] = peer
        result = span.run(**args, resume=True)
        assert result['frames'] == 12 and len(peer.puts) == 3
        assert span.run(**args, resume=True) == result and len(peer.puts) == 3
    assert originals(args) == before


def test_aggregate_byte_precheck_without_reading_sparse_media(tmp_path):
    args = options(tmp_path, count=1)
    with (args['deliveries'][0]/'audio.wav').open('r+b') as stream:
        stream.truncate(span.MAX_INPUT_BYTES + 1)
    with pytest.raises(span.Refused, match='input_byte_bound'): span.run(**args)
    assert not args['job_dir'].exists()


def test_two_hour_span_bound_without_large_media(monkeypatch):
    # Arithmetic-only capacity check, not a two-hour runtime claim.
    monkeypatch.setattr(span, 'reconcile_snapshots', lambda *a, **kw: dict(captures=[
        dict(conflicting_ordinals=[], duplicates=[], edges=[])]))
    report = dict(sequence=9, close_reason='STOP', sample_rate_hz=16000,
                  channels=1, frames=2*3600*u.RATE + 1)
    with pytest.raises(span.Refused, match='two_hour_bound'): span.check_sequence([], [report])


def test_one_shared_upload_guard_across_inputs_and_finalization(tmp_path, monkeypatch):
    args = options(tmp_path); args['transport'] = Peer()
    original = span.SpanUploader.run; guards = []
    def observed(self):
        guards.append(self.guard)
        assert self.journal.guard is self.guard
        return original(self)
    monkeypatch.setattr(span.SpanUploader, 'run', observed)
    span.run(**args)
    assert len(guards) == 4 and all(guard is guards[0] for guard in guards)
