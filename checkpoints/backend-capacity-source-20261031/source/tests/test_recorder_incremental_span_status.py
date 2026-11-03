"""V3 status with real ASGI GET projections; publication/analysis are synthetic.

Nine physical valid Python-produced delivery fixtures are admitted individually.
Actual recorder-producer/backend-worker completion is a separate integration.
"""
from contextlib import contextmanager
import copy
import fcntl
import json
from pathlib import Path

import pytest

from audio_backend import recorder_incremental_span_intake as intake
from audio_backend import recorder_incremental_span_status as status
from audio_backend import recorder_span_status as v2status
from audio_backend import recording_uploader as u
from audio_backend import recording_uploader_history as history
from test_extended_recorder_intake import Peer
from test_recorder_span_intake import options
from test_recorder_span_status import Routes, large_delivery, local_files
from test_recording_session_contract import handler_app, session


@contextmanager
def fixture(tmp_path, *, count=9, close='INTERRUPTION', frames=None, worker='completed', finalize=True):
    with handler_app(tmp_path / 'server') as (client, app):
        sid = session(client); args = options(tmp_path, count=count, close=close)
        if frames is not None:
            path = tmp_path / 'large'; large_delivery(path, frames); args['deliveries'] = [path]
        paths = args.pop('deliveries'); args['session_id'] = sid
        class SyntheticPublication(Peer):
            def request(self, method, path, **kwargs):
                value = super().request(method, path.replace('/sessions/' + sid, '/sessions/known'), **kwargs)
                if path.endswith('/recording-contract'): value['session_id'] = sid
                return value
        args['transport'] = SyntheticPublication()
        for index, path in enumerate(paths): intake.run(**args, delivery=path, start=index == 0)
        if finalize: intake.run(**args, finalize=True)
        state = json.loads((args['job_dir'] / 'uploader/uploader.json').read_bytes())
        pages = [json.loads(p.read_bytes())['payload'] for p in sorted((args['job_dir'] / 'uploader/uploader.history').glob('page-*.json'))]
        # Seed only explicit synthetic work/analysis projections, never native workers.
        if finalize:
            with app.state.store.db() as db:
                bid = 'synthetic-v3-batch'
                db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,mode,state,revision,
                    capture_origin_frame,capture_end_frame,eos,created)
                    VALUES(?,?,?,'sealed_pages','queued',1,0,?,1,0)''', (bid, sid, 'extended', state['emitted_frames']))
                position = 0
                for page in pages:
                    start = (page['number'] - 1) * u.PAGE_FRAMES; end = page['seal']['end_frame']; pid = page['server_id']
                    db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,revision,state,released,
                        start_frame,end_frame,eos,created) VALUES(?,?,?,?,'published',1,?,?,?,0)''',
                        (pid, bid, page['number'], page['seal']['expected_revision'], start, end, int(page['seal']['eos'])))
                    for piece in page['pieces']:
                        db.execute('''INSERT INTO recording_sources(id,batch_id,page_id,idempotency_key,digest,
                            path,frames,bytes,source_start_frame,format,created) VALUES(?,?,?,?,?,?,?,?,?,?,0)''',
                            (piece['server_id'], bid, pid, piece['key'], piece['digest'], '/synthetic/' + piece['server_id'],
                             piece['frames'], piece['bytes'], piece['capture_start'], 'wav-pcm16le-16000-mono'))
                    for start in range(start, end, v2status.CHUNK_FRAMES):
                        length = min(v2status.CHUNK_FRAMES, end - start); cid = 'work-' + str(position)
                        db.execute('''INSERT INTO chunks(rowid,id,session_id,idempotency_key,digest,path,duration,
                            timeline_start,status,next_offset,created,error) VALUES(?,?,?,?,?,?,?,?,?,?,0,?)''',
                            (10000 + position * 7, cid, sid, cid, 'c'*64, '/synthetic/' + cid, length/u.RATE,
                             start/u.RATE, worker, length/u.RATE if worker == 'completed' else 0, 'PRIVATE DO NOT LEAK'))
                        db.execute('''INSERT INTO recording_work(chunk_id,batch_id,page_id,position,frames,
                            bytes,capture_start_frame,native_receipt_status,receipt_state)
                            VALUES(?,?,?,?,?,?,?,'disabled','gap')''', (cid, bid, pid, position, length, length*2+44, start))
                        position += 1
        routes = Routes(client)
        run = dict(job_dir=args['job_dir'], api=args['api'], session_id=sid, capture_id='extended',
                   exclusive_writer=True, transport=routes, limits=args['limits'])
        args['deliveries'] = paths
        yield args, run, routes, app, state
        assert not app.state.worker.thread.is_alive()


@pytest.mark.parametrize('close,count', [('STOP',9), ('INTERRUPTION',9), ('RECOVERED_TAIL',1)])
def test_completion_mapping_and_read_only_repeat(tmp_path, close, count, monkeypatch):
    with fixture(tmp_path, close=close, count=count) as (args, run, routes, app, state):
        before = local_files(args)
        def forbidden(*a, **kw): raise AssertionError('Mutation-capable lifecycle must not run')
        for method in ('__init__', 'load', 'save', 'recover', 'cleanup', 'budget', '_census'):
            monkeypatch.setattr(history.PagedJournal, method, forbidden)
        monkeypatch.setattr(intake.engine.CaptureUploader, '__init__', forbidden)
        result = status.run(**run)
        assert result['schema'] == status.SCHEMA and result['status'] == 'completed'
        assert result['inputs'] == count and result['frames'] == count * 4
        assert result['span']['original_close_reason'] == close
        assert result['span']['recorder_stop_observed'] is (close == 'STOP')
        assert result['input_mapping'] == [dict(ordinal=i, original_sequence=11+i, original_epoch=7,
            original_epoch_frame_start=100003+4*i, original_epoch_frame_end=100007+4*i,
            span_start_frame=4*i, span_end_frame=4*i+4) for i in range(count)]
        assert result['span']['before_span_gap_frames'] is result['span']['after_span_gap_frames'] is None
        assert result['span']['eos_meaning'] == 'selected_span_only'
        assert result['analysis_completion_verified'] and result['analysis_completion_observed']
        assert not any(result[k] for k in ('accuracy_verified', 'physical_continuity_proven',
            'capture_completeness_proven', 'observation_atomic', 'remote_request_identity_verified',
            'concurrent_writer_safety_guaranteed'))
        assert result['optional_receipts'] == dict(pending=0, durable=0, gaps=0, ready=False)
        assert status.run(**run) == result and local_files(args) == before
        assert all(method == 'GET' for method, _ in routes.calls)
        assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('frames', [u.PAGE_FRAMES, u.PAGE_FRAMES + 17])
def test_real_geometry_and_full_page_finish(tmp_path, frames):
    with fixture(tmp_path, frames=frames) as (args, run, routes, app, state):
        result = status.run(**run)
        assert result['status'] == 'completed' and result['work_chunks'] == (frames+167999)//168000
        assert (state.get('finish') is not None) is (frames == u.PAGE_FRAMES)


@pytest.mark.parametrize('worker,expected', [('queued','pending'), ('running','pending'), ('waiting_input','pending'),
    ('staging','pending'), ('failed','blocked'), ('paused','blocked'), ('cancelled','blocked')])
def test_pending_and_blocked_are_not_complete(tmp_path, worker, expected):
    with fixture(tmp_path, worker=worker) as (args, run, routes, app, state):
        before = local_files(args); result = status.run(**run)
        assert result['status'] == expected and not result['analysis_completion_verified']
        assert 'PRIVATE' not in json.dumps(result) and local_files(args) == before


def test_unfinalized_never_queries_or_replays(tmp_path):
    with fixture(tmp_path, finalize=False) as (args, run, routes, app, state):
        before = local_files(args)
        with pytest.raises(status.NotReady): status.run(**run)
        assert not routes.calls and local_files(args) == before


@pytest.mark.parametrize('change', ['header', 'origin', 'ack_type', 'initial_intent', 'admission', 'admission_type',
    'admission_target_type', 'checkpoint', 'caps', 'input_history', 'page_history', 'history_missing',
    'history_extra', 'source', 'snapshot', 'unknown_root', 'unknown_uploader', 'lost_checkpoint'])
def test_changed_local_histories_fail_before_http(tmp_path, change):
    with fixture(tmp_path) as (args, run, routes, app, state):
        root = args['job_dir']
        def edit(path, fn, newline=True):
            value = json.loads(path.read_bytes()); fn(value)
            path.write_bytes(u._json(value) + (b'\n' if newline else b''))
        if change == 'header': edit(root/'capture.json', lambda d: d.update(schema='explicit-incremental-recorder-intake-v1'))
        elif change == 'origin': edit(root/'capture.json', lambda d: d['span_origin'].update(first_sequence=0))
        elif change == 'ack_type': edit(root/'capture.json', lambda d: d['acknowledgments'].update(span_local_closure=1))
        elif change == 'initial_intent': edit(root/'initial-intent.json', lambda d: d['delivery'].update(sequence=0))
        elif change == 'admission': edit(root/'admissions/3/admission.json', lambda d: d['delivery'].update(frame_start=1))
        elif change == 'admission_type': edit(root/'admissions/0/admission.json', lambda d: d.update(ordinal=False))
        elif change == 'admission_target_type': edit(root/'admissions/0/admission.json', lambda d: d['target'].update(capture_origin_frame=False))
        elif change == 'checkpoint': edit(root/'uploader/uploader.json', lambda d: d.update(policy=u.APPEND_POLICY), False)
        elif change == 'caps': edit(root/'uploader/uploader.json', lambda d: d['caps'].update(max_inputs=128), False)
        elif change in ('input_history', 'page_history'):
            kind = change.split('_')[0]
            edit(root/f'uploader/uploader.history/{kind}-0001.json', lambda d: d.update(previous='f'*64), False)
        elif change == 'history_missing': (root/'uploader/uploader.history/input-0005.json').unlink()
        elif change == 'history_extra': (root/'uploader/uploader.history/extra').write_bytes(b'x')
        elif change == 'source': (args['deliveries'][0]/'audio.wav').write_bytes(b'x')
        elif change == 'snapshot': (root/'admissions/0/snapshot/audio.wav').write_bytes(b'x')
        elif change == 'unknown_root': (root/'extra').write_bytes(b'x')
        elif change == 'unknown_uploader': (root/'uploader/uploader.json.next').write_bytes(b'x')
        elif change == 'lost_checkpoint': (root/'uploader/uploader.json').unlink()
        before = local_files(args)
        with pytest.raises((status.Refused, u.Incomplete, OSError, ValueError)): status.run(**run)
        assert not routes.calls and local_files(args) == before


@pytest.mark.parametrize('change', ['capture_end', 'page_digest', 'page_work_missing', 'page_status', 'chunk_missing',
    'chunk_extra', 'chunk_progress', 'cursor', 'late_local', 'late_history'])
def test_remote_disagreement_or_late_local_change_never_completes(tmp_path, change):
    with fixture(tmp_path) as (args, run, routes, app, state):
        def edit(path, value):
            if change == 'capture_end' and path.endswith('/recordings/extended'): value['capture_end_frame'] += 1
            if '/pages/1' in path:
                if change == 'page_digest': value['sources'][0]['digest'] = 'a'*64
                elif change == 'page_work_missing': value['work_chunks'].clear()
                elif change == 'page_status': value['work_chunks'][0]['status'] = 'running'
            if '/chunks?' in path and value['chunks']:
                if change == 'chunk_missing': value['chunks'].clear(); value['next_cursor'] = 0
                elif change == 'chunk_extra': value['chunks'][0]['id'] = 'foreign'
                elif change == 'chunk_progress': value['chunks'][0]['next_offset'] -= 1/u.RATE
                elif change == 'cursor': value['chunks'][0]['cursor'] = 0
                elif change == 'late_local': (args['job_dir']/'capture.json').touch()
                elif change == 'late_history': (args['job_dir']/'uploader/uploader.history/input-0001.json').touch()
        routes.edit = edit
        if change == 'page_status':
            result = status.run(**run)
            assert result['status'] == 'pending' and not result['analysis_completion_verified']
        else:
            with pytest.raises((status.Refused, u.Incomplete)): status.run(**run)


@pytest.mark.parametrize('lock', ['intake.lock', 'uploader/uploader.lock'])
def test_existing_locks_and_no_implicit_create(tmp_path, lock):
    with fixture(tmp_path) as (args, run, routes, app, state):
        with (args['job_dir']/lock).open('rb') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(BlockingIOError): status.run(**run)
        assert not routes.calls


def test_limits_and_cancellation_fail_without_success(tmp_path):
    with fixture(tmp_path) as (args, run, routes, app, state):
        with pytest.raises(InterruptedError): status.run(**run, cancel=lambda: True)
        run['limits'] = u.Limits(input_bytes=100, output_frames=48*3600*u.RATE, disk_floor=0)
        with pytest.raises(status.Refused, match='input_bound'): status.run(**run)
        assert not routes.calls


def test_v2_defaults_not_relaxed_and_v3_bounds_are_explicit():
    assert v2status.MAX_WORK_CHUNKS == 700 and v2status.MAX_RESPONSE_BYTES == 32*1024**2
    assert status.MAX_PAGES == 588 and status.MAX_WORK_CHUNKS == 588*28
