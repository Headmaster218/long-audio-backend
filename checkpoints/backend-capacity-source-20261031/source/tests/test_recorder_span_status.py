"""Real ASGI GET projections, with explicitly synthetic publication/analysis rows.

No native lifespan, seal, inference or production completion is qualified here.
The fixture uploader peer manufactures publication only to make finalized jobs.
"""
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json

import pytest
from audio_backend import recorder_span_intake as intake
from audio_backend import recorder_span_status as status
from audio_backend import recording_uploader as u
from test_extended_recorder_intake import Peer
from test_recorder_span_intake import options
from test_recorder_metadata import metadata, utf, i
from test_recording_session_contract import handler_app, session, HEADERS


def write_json(path, data):
    path.write_bytes(u._json(data))


def local_files(args):
    roots = [args['job_dir'], *args.get('deliveries', [])]
    return {str(p): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ctime_ns)
            for root in roots for p in root.rglob('*') if p.is_file()}


def large_delivery(root, frames):
    root.mkdir()
    pcm = b'\0\1' * frames
    wav = u.LegacyAppendUploader._header(frames) + pcm
    source = 'segment-' + hashlib.sha256(b'capture').hexdigest() + '-11.ready'
    body = metadata(sequence=11, epoch=7, start=100003, frames=frames, target=frames,
        close='INTERRUPTION', seam=False, boundary='RESTART', run='restarted', capture='capture',
        pcm=hashlib.sha256(pcm).hexdigest(), wav=hashlib.sha256(wav).hexdigest())
    envelope = b'RFT1' + utf('rev') + utf(source) + i(len(body)) + body
    wh = hashlib.sha256(wav).hexdigest()
    binding = hashlib.sha256((source + '\n' + wh + '\nrev').encode()).hexdigest()
    marker = '\n'.join(('RECORDER-DELIVERY-V1', 'r-' + binding + '-' + '0'*32,
        'rev', source, str(len(wav)), wh, str(len(envelope)), hashlib.sha256(envelope).hexdigest(), ''))
    (root/'audio.wav').write_bytes(wav); (root/'segment.v1.bin').write_bytes(envelope)
    (root/'delivery.v1.txt').write_text(marker)


class Routes:
    def __init__(self, client, edit=None):
        self.client = client; self.calls = []; self.edit = edit
    def request(self, method, path, **kwargs):
        assert method == 'GET' and not kwargs, 'Observer must use only body-free GET'
        response = self.client.get(path, headers=HEADERS)
        self.calls.append((method, path))
        if response.status_code >= 400:
            raise u.HTTPFailure(response.status_code, response.text)
        value = response.json()
        if self.edit: self.edit(path, value)
        return value


@contextmanager
def fixture(tmp_path, *, frames=None, close='INTERRUPTION', count=3, worker='completed'):
    with handler_app(tmp_path/'server') as (client, app):
        sid = session(client)
        args = options(tmp_path, count=count, close=close)
        if frames is not None:
            path = tmp_path/'large'; large_delivery(path, frames); args['deliveries'] = [path]
        args['session_id'] = sid
        class SyntheticPublication(Peer):
            def request(self, method, path, **kwargs):
                value = super().request(method, path.replace('/sessions/' + sid, '/sessions/known'), **kwargs)
                if path.endswith('/recording-contract'): value['session_id'] = sid
                return value
        args['transport'] = SyntheticPublication()
        intake.run(**args)
        state = json.loads((args['job_dir']/'uploader/uploader.json').read_bytes())
        # Seed projections only. This does not run seal, native analysis or receipt publication.
        with app.state.store.db() as db:
            bid = 'synthetic-batch'
            db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,mode,state,revision,
                capture_origin_frame,capture_end_frame,eos,created)
                VALUES(?,?,?,'sealed_pages','queued',1,0,?,1,0)''',
                (bid, sid, 'extended', state['emitted_frames']))
            position = 0
            for page in state['pages']:
                start = (page['number'] - 1) * u.PAGE_FRAMES
                end = page['seal']['end_frame']; pid = page['server_id']
                db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,revision,state,released,
                    start_frame,end_frame,eos,created) VALUES(?,?,?,?,'published',1,?,?,?,0)''',
                    (pid, bid, page['number'], page['seal']['expected_revision'], start, end, int(page['seal']['eos'])))
                for piece in page['pieces']:
                    db.execute('''INSERT INTO recording_sources(id,batch_id,page_id,idempotency_key,digest,
                        path,frames,bytes,source_start_frame,format,created) VALUES(?,?,?,?,?,?,?,?,?,?,0)''',
                        (piece['server_id'], bid, pid, piece['key'], piece['digest'],
                        '/synthetic/' + piece['server_id'], piece['frames'], piece['bytes'],
                        piece['capture_start'], 'wav-pcm16le-16000-mono'))
                for start in range(start, end, status.CHUNK_FRAMES):
                    length = min(status.CHUNK_FRAMES, end - start); cid = 'work-' + str(position)
                    # Explicit sparse global cursors, unrelated to chunk count.
                    db.execute('''INSERT INTO chunks(rowid,id,session_id,idempotency_key,digest,path,duration,
                        timeline_start,status,next_offset,created,error) VALUES(?,?,?,?,?,?,?,?,?,?,0,?)''',
                        (10_000 + position*7, cid, sid, cid, 'c'*64, '/synthetic/' + cid,
                        length/u.RATE, start/u.RATE, worker, length/u.RATE if worker == 'completed' else 0, 'PRIVATE ERROR MUST NOT LEAK'))
                    db.execute('''INSERT INTO recording_work(chunk_id,batch_id,page_id,position,frames,
                        bytes,capture_start_frame,native_receipt_status,receipt_state)
                        VALUES(?,?,?,?,?,?,?,'disabled','gap')''',
                        (cid, bid, pid, position, length, length*2+44, start))
                    position += 1
        routes = Routes(client)
        run = dict(job_dir=args['job_dir'], api=args['api'], session_id=sid, capture_id='extended',
                   exclusive_writer=True, transport=routes, limits=args['limits'])
        yield args, run, routes, app, state
        assert not app.state.worker.thread.is_alive()


@pytest.mark.parametrize('close,count', [('STOP',3), ('INTERRUPTION',3), ('RECOVERED_TAIL',1)])
def test_completed_real_get_routes_keep_span_truth_and_all_local_bytes(tmp_path, close, count):
    with fixture(tmp_path, close=close, count=count) as (args, run, routes, app, state):
        before = local_files(args)
        result = status.run(**run)
        assert result['status'] == 'completed' and result['analysis_completion_observed']
        assert result['span']['original_close_reason'] == close
        assert result['span']['recorder_stop_observed'] is (close == 'STOP')
        assert result['span']['before_span_gap_frames'] is None
        assert result['span']['after_span_gap_frames'] is None
        assert result['span']['eos_meaning'] == 'selected_span_only'
        assert not any(result[k] for k in ('accuracy_verified', 'physical_continuity_proven',
            'capture_completeness_proven', 'observation_atomic', 'remote_request_identity_verified',
            'concurrent_writer_safety_guaranteed'))
        assert result['page_status_counts'] == result['session_status_counts'] == {'completed':1}
        assert result['capture_state'] == 'queued'
        assert 'PRIVATE' not in json.dumps(result)
        assert all(method == 'GET' for method, _ in routes.calls)
        assert any('/chunks?after=10000&' in path for _, path in routes.calls)
        assert status.run(**run) == result and local_files(args) == before


@pytest.mark.parametrize('frames', [u.PAGE_FRAMES, u.PAGE_FRAMES + 17])
def test_full_page_finish_and_real_geometry_multiple_pages(tmp_path, frames):
    with fixture(tmp_path, frames=frames) as (args, run, routes, app, state):
        if frames == u.PAGE_FRAMES:
            assert state['finish'] and not state['pages'][-1]['seal']['eos']
        else:
            assert state['pages'][1]['create'] == {'page_number':2}
        result = status.run(**run)
        assert result['status'] == 'completed'
        assert result['work_chunks'] == (frames + 167999)//168000
        assert result['pages'] == len(state['pages'])


@pytest.mark.parametrize('worker,expected', [('queued','pending'), ('running','pending'),
    ('waiting_input','pending'), ('staging','pending'), ('paused','blocked'),
    ('failed','blocked'), ('cancelled','blocked')])
def test_worker_states_visible_without_private_errors(tmp_path, worker, expected):
    with fixture(tmp_path, worker=worker) as (args, run, routes, app, state):
        before = local_files(args); result = status.run(**run)
        assert result['status'] == expected and not result['analysis_completion_observed']
        assert result['session_status_counts'] == {worker:1}
        assert len(result['blocked_chunks']) == (expected == 'blocked')
        assert 'PRIVATE' not in json.dumps(result) and local_files(args) == before


def test_cancelled_capture_does_not_become_completion(tmp_path):
    with fixture(tmp_path) as (args, run, routes, app, state):
        with app.state.store.db() as db: db.execute("UPDATE recording_batches SET state='aborted'")
        result = status.run(**run)
        assert result['status'] == 'blocked' and not result['analysis_completion_observed']


@pytest.mark.parametrize('page_state,chunk_state', [('running','completed'), ('completed','queued'),
                                                    ('completed','failed')])
def test_cross_projection_disagreement_is_never_completion(tmp_path, page_state, chunk_state):
    with fixture(tmp_path) as (args, run, routes, app, state):
        def edit(path, value):
            if '/pages/1' in path: value['work_chunks'][0]['status'] = page_state
            if '/chunks?' in path and value['chunks']: value['chunks'][0]['status'] = chunk_state
        routes.edit = edit
        result = status.run(**run)
        assert result['status'] == ('blocked' if chunk_state == 'failed' else 'pending')
        assert not result['analysis_completion_observed']


@pytest.mark.parametrize('change', ['work_missing', 'work_extra', 'work_duplicate', 'work_id',
    'work_position', 'work_start', 'work_frames', 'work_duration', 'work_timeline', 'work_bytes',
    'source_missing', 'source_duplicate', 'source_key', 'source_digest', 'source_id', 'source_start',
    'page_id', 'page_eos', 'page_start', 'page_end', 'page_capture', 'capture_extra_page', 'capture_end',
    'chunk_extra', 'chunk_missing', 'chunk_duplicate', 'chunk_geometry', 'cursor_stuck', 'cursor_regress',
    'next_cursor_stuck', 'empty_cursor_changed'])
def test_changed_or_incomplete_remote_histories_refuse(tmp_path, change):
    with fixture(tmp_path) as (args, run, routes, app, state):
        def edit(path, value):
            if '/pages/1' in path:
                work = value['work_chunks']; sources = value['sources']
                if change == 'work_missing': work.clear()
                elif change == 'work_extra': work.append(dict(work[0], chunk_id='extra'))
                elif change == 'work_duplicate': work.append(copy.deepcopy(work[0]))
                elif change == 'work_id': work[0]['chunk_id'] = 'unknown'
                elif change.startswith('work_'):
                    key = {'position':'position','start':'capture_start_frame','frames':'frames',
                           'duration':'duration','timeline':'timeline_start','bytes':'bytes'}[change[5:]]
                    work[0][key] += 1
                elif change == 'source_missing': sources.pop()
                elif change == 'source_duplicate': sources[1] = copy.deepcopy(sources[0])
                elif change.startswith('source_'):
                    key = {'key':'idempotency_key','digest':'digest','id':'id','start':'source_start_frame'}[change[7:]]
                    sources[0][key] = 1 if key == 'source_start_frame' else 'changed'
                elif change.startswith('page_'):
                    key = {'id':'id','eos':'eos','start':'start_frame','end':'end_frame','capture':'capture_id'}[change[5:]]
                    value[key] = False if key == 'eos' else 1 if key.endswith('frame') else 'changed'
            elif path.endswith('/recordings/extended'):
                if change == 'capture_extra_page': value['page_count'] += 1
                if change == 'capture_end': value['capture_end_frame'] += 1
            elif '/chunks?' in path:
                rows = value['chunks']
                if change == 'chunk_extra' and rows: rows[0]['id'] = 'foreign'
                if change == 'chunk_missing': rows.clear(); value['next_cursor'] = 0
                if change == 'chunk_duplicate' and rows:
                    rows.append(dict(rows[0], cursor=10001)); value['next_cursor'] = 10001
                if change == 'chunk_geometry' and rows: rows[0]['timeline_start'] += 1
                if change == 'cursor_stuck' and rows: rows[0]['cursor'] = 0
                if change == 'cursor_regress' and rows: rows[0]['cursor'] = -1
                if change == 'next_cursor_stuck' and rows: value['next_cursor'] = 0
                if change == 'empty_cursor_changed' and not rows: value['next_cursor'] += 1
        routes.edit = edit; before = local_files(args)
        with pytest.raises((status.Refused, u.Incomplete)): status.run(**run)
        assert local_files(args) == before


@pytest.mark.parametrize('offset', [True, -1, float('nan'), float('inf'), 11/u.RATE, 13/u.RATE])
def test_invalid_or_completed_short_progress_refuses(tmp_path, offset):
    with fixture(tmp_path) as (args, run, routes, app, state):
        routes.edit = lambda path, value: value['chunks'][0].update(next_offset=offset) if '/chunks?' in path and value['chunks'] else None
        with pytest.raises((status.Refused, ValueError)): status.run(**run)


@pytest.mark.parametrize('change', ['api', 'session_id', 'capture_id', 'manifest_span', 'manifest_offset',
    'journal_identity', 'journal_digest', 'journal_original', 'original', 'snapshot', 'lost_journal',
    'lost_snapshot', 'unknown_job_file'])
def test_local_or_explicit_target_changes_refuse_before_http(tmp_path, change):
    with fixture(tmp_path) as (args, run, routes, app, state):
        if change in ('api', 'session_id', 'capture_id'):
            run[change] = 'https://other.example' if change == 'api' else 'different'
        elif change.startswith('manifest_'):
            path = args['job_dir']/'manifest.json'; value = json.loads(path.read_bytes())
            if change == 'manifest_span': value['span']['original_close_reason'] = 'STOP'
            else: value['inputs'][1]['span_start_frame'] += 1
            path.write_bytes(intake.single._json(value))
        elif change.startswith('journal_'):
            if change == 'journal_identity': state['identity']['capture_id'] = 'other'
            elif change == 'journal_digest': state['pages'][0]['pieces'][0]['digest'] = '0'*64
            else: state['inputs'][0]['identity']['sha256'] = '0'*64
            write_json(args['job_dir']/'uploader/uploader.json', state)
        elif change == 'original': (args['deliveries'][0]/'audio.wav').write_bytes(b'changed')
        elif change == 'snapshot': (args['job_dir']/'snapshots/0/audio.wav').write_bytes(b'changed')
        elif change == 'lost_journal': (args['job_dir']/'uploader/uploader.json').unlink()
        elif change == 'lost_snapshot': (args['job_dir']/'snapshots/0/audio.wav').unlink()
        else: (args['job_dir']/'extra').write_text('unknown')
        with pytest.raises((status.Refused, u.Incomplete, FileNotFoundError)): status.run(**run)
        assert routes.calls == []


def test_unfinished_local_job_never_resumes(tmp_path):
    with fixture(tmp_path) as (args, run, routes, app, state):
        state['complete'] = False; write_json(args['job_dir']/'uploader/uploader.json', state)
        before = local_files(args)
        with pytest.raises(status.NotReady) as error: status.run(**run)
        assert status.failure_report(error.value)['status'] == 'not_ready'
        assert routes.calls == [] and local_files(args) == before


@pytest.mark.parametrize('name', ['intake.lock', 'uploader/uploader.lock'])
def test_existing_locks_exclude_status_without_creating_files(tmp_path, name):
    with fixture(tmp_path) as (args, run, routes, app, state):
        before = local_files(args)
        with (args['job_dir']/name).open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(BlockingIOError): status.run(**run)
        assert not routes.calls and before == local_files(args)
        assert status.run(**run)['status'] == 'completed'


def test_cancel_before_and_during_get_is_read_only(tmp_path):
    with fixture(tmp_path) as (args, run, routes, app, state):
        before = local_files(args)
        with pytest.raises(InterruptedError): status.run(**run, cancel=lambda: True)
        assert not routes.calls
        with pytest.raises(InterruptedError): status.run(**run, cancel=lambda: bool(routes.calls))
        assert len(routes.calls) == 1 and local_files(args) == before


def test_shared_deadline_and_response_bounds(tmp_path, monkeypatch):
    with fixture(tmp_path) as (args, run, routes, app, state):
        run['limits'] = u.Limits(deadline_seconds=.000001)
        with pytest.raises((status.Refused, u.Incomplete)): status.run(**run)
        assert not routes.calls
        run['limits'] = args['limits']
        routes.edit = lambda path, value: value.update(padding='x' * u.JSON_BYTES)
        with pytest.raises(status.Refused, match='response_byte_bound'): status.run(**run)
        assert len(routes.calls) == 1


def test_local_change_during_observation_never_returns_success(tmp_path):
    with fixture(tmp_path) as (args, run, routes, app, state):
        def edit(path, value):
            if '/chunks?' in path:
                (args['deliveries'][0]/'audio.wav').write_bytes(b'changed')
        routes.edit = edit
        with pytest.raises(status.Refused): status.run(**run)


def test_cli_safe_failures_and_required_ack(monkeypatch, capsys):
    args = ['--job-dir','j','--api','https://example.org','--session-id','s','--capture-id','c']
    with pytest.raises(SystemExit): status.main(args)
    capsys.readouterr()
    def fail(**kwargs): raise u.HTTPFailure(503, 'PRIVATE BODY token secret')
    monkeypatch.setattr(status, 'run', fail)
    assert status.main(args + ['--exclusive-writer']) == 2
    output = capsys.readouterr().err; value = json.loads(output)
    assert value['status'] == 'refused' and value['http_status'] == 503
    assert not value['possible_remote_effects'] and 'PRIVATE' not in output and 'token' not in output


def test_single_row_sparse_pagination_and_unicode_errors(tmp_path):
    with fixture(tmp_path, frames=u.PAGE_FRAMES) as (args, run, routes, app, state):
        with app.state.store.db() as db:
            db.execute('UPDATE chunks SET error=?', ('\U0001f600'*2000,))
        # Both actual 16-row escaped responses and legal short pages fit the cap.
        assert status.run(**run)['status'] == 'completed'
        routes.calls.clear()
        def one_row(path, value):
            if '/chunks?' in path and value['chunks']:
                value['chunks'] = value['chunks'][:1]
                value['next_cursor'] = value['chunks'][-1]['cursor']
        routes.edit = one_row
        assert status.run(**run)['work_chunks'] == 28
        assert sum('/chunks?' in p for _, p in routes.calls) == 29


@pytest.mark.parametrize('code', [401, 404, 429, 503])
def test_http_failures_are_not_retried(tmp_path, code):
    with fixture(tmp_path) as (args, run, routes, app, state):
        calls = []
        class Failed:
            def request(self, method, path):
                calls.append((method,path)); raise u.HTTPFailure(code, 'PRIVATE RESPONSE')
        run['transport'] = Failed(); before = local_files(args)
        with pytest.raises(u.HTTPFailure): status.run(**run)
        assert len(calls) == 1 and calls[0][0] == 'GET' and local_files(args) == before


@pytest.mark.parametrize('change', ['revision', 'unknown_status', 'boolean_position', 'boolean_create',
                                    'boolean_origin', 'unfinished_missing_piece'])
def test_additional_malformed_histories(tmp_path, change):
    with fixture(tmp_path) as (args, run, routes, app, state):
        if change in ('boolean_create', 'boolean_origin', 'unfinished_missing_piece'):
            if change == 'boolean_create': state['pages'][0]['create']['page_number'] = True
            elif change == 'boolean_origin': state['pages'][0]['create']['capture_origin_frame'] = False
            else:
                state['complete'] = False
                state['spool'] = [state['pages'][0]['pieces'][0]['name']]
            write_json(args['job_dir']/'uploader/uploader.json', state)
        else:
            def edit(path, value):
                if '/pages/1' in path:
                    if change == 'revision': value['revision'] += 1
                    if change == 'unknown_status': value['work_chunks'][0]['status'] = 'new-unknown-state'
                    if change == 'boolean_position': value['work_chunks'][0]['position'] = False
            routes.edit = edit
        with pytest.raises((status.Refused, u.Incomplete)): status.run(**run)
        if change.startswith('boolean_') and change != 'boolean_position' or change.startswith('unfinished_'):
            assert not routes.calls


def test_optional_receipts_are_separate_from_primary_completion(tmp_path):
    with fixture(tmp_path) as (args, run, routes, app, state):
        result = status.run(**run)
        assert result['status'] == 'completed'
        assert result['optional_receipts'] == dict(pending=0, durable=0, gaps=0, ready=False)
        with app.state.store.db() as db:
            db.execute("UPDATE recording_work SET receipt_state='pending',native_receipt_status='receipt_pending'")
        result = status.run(**run)
        assert result['status'] == 'completed'
        assert result['optional_receipts'] == dict(pending=1, durable=0, gaps=0, ready=False)


def test_lower_input_limits_and_total_http_limit(tmp_path, monkeypatch):
    with fixture(tmp_path) as (args, run, routes, app, state):
        for limits in (u.Limits(input_bytes=100), u.Limits(output_frames=11)):
            run['limits'] = limits
            with pytest.raises(status.Refused): status.run(**run)
            assert not routes.calls
        run['limits'] = args['limits']
        monkeypatch.setattr(status, 'MAX_RESPONSE_BYTES', 5)
        with pytest.raises(status.Refused, match='total_response_byte_bound'): status.run(**run)
        assert len(routes.calls) == 1


def test_maximum_input_count_and_missing_ack(tmp_path):
    with fixture(tmp_path, count=8) as (args, run, routes, app, state):
        assert status.run(**run)['inputs'] == 8
        routes.calls.clear(); run['exclusive_writer'] = False
        with pytest.raises(status.Refused, match='ack_required'): status.run(**run)
        assert not routes.calls


def test_sparse_oversized_input_refused_before_hashing(tmp_path):
    with fixture(tmp_path) as (args, run, routes, app, state):
        with (args['deliveries'][0]/'audio.wav').open('r+b') as stream:
            stream.truncate(intake.MAX_INPUT_BYTES + 1)
        with pytest.raises(status.Refused, match='input_byte_bound'): status.run(**run)
        assert not routes.calls


@pytest.mark.parametrize('change', ['ack', 'claim', 'origin', 'offset', 'report', 'decoder', 'format'])
def test_canonical_local_identity_rejects_bool_integer_aliases(tmp_path, change):
    with fixture(tmp_path) as (args, run, routes, app, state):
        if change in ('decoder', 'format'):
            if change == 'decoder': state['inputs'][0]['decoder']['format']['channels'] = True
            else: state['identity']['format']['channels'] = True
            write_json(args['job_dir']/'uploader/uploader.json', state)
        else:
            path = args['job_dir']/'manifest.json'; value = json.loads(path.read_bytes())
            if change == 'ack': value['acknowledgments']['exclusive_writer'] = 1
            elif change == 'claim': value['span']['capture_completeness_proven'] = 0
            elif change == 'origin': value['target']['capture_origin_frame'] = False
            elif change == 'offset': value['inputs'][0]['span_start_frame'] = False
            else: value['inputs'][0]['delivery']['channels'] = True
            path.write_bytes(intake.single._json(value))
        with pytest.raises(status.Refused): status.run(**run)
        assert not routes.calls


@pytest.mark.parametrize('change', ['ready', 'durable', 'pending', 'gaps', 'state'])
def test_inconsistent_optional_receipts_refused(tmp_path, change):
    with fixture(tmp_path) as (args, run, routes, app, state):
        def edit(path, value):
            if '/pages/1' in path:
                if change == 'state': value['work_chunks'][0]['receipt_state'] = 'unknown'
                else: value['optional_receipts'][change] = True if change == 'ready' else 1
        routes.edit = edit
        with pytest.raises(status.Refused, match='optional_receipt_projection'): status.run(**run)


def test_snapshot_receipt_bool_alias_is_not_a_valid_source_identity(tmp_path):
    with fixture(tmp_path) as (args, run, routes, app, state):
        receipt = args['job_dir']/'snapshots/0/snapshot.json'
        value = json.loads(receipt.read_bytes()); value['body']['delivery']['channels'] = True
        value['sha256'] = hashlib.sha256(intake.single._json(value['body'])).hexdigest()
        # Pending and final receipts are the existing hardlinked snapshot pair.
        receipt.write_bytes(intake.single._json(value))
        with pytest.raises(status.Refused, match='snapshot_identity_changed'): status.run(**run)
        assert not routes.calls


def test_boolean_contract_channel_is_refused(tmp_path):
    with fixture(tmp_path) as (args, run, routes, app, state):
        routes.edit = lambda path, value: value.update(channels=True) if path.endswith('/recording-contract') else None
        with pytest.raises(status.Refused, match='contract_numeric_type'): status.run(**run)


@pytest.mark.parametrize('target', ['capture', 'contract'])
def test_final_get_type_change_is_not_hidden_by_python_equality(tmp_path, target):
    with fixture(tmp_path) as (args, run, routes, app, state):
        calls = 0
        def edit(path, value):
            nonlocal calls
            if (target == 'capture' and path.endswith('/recordings/extended') or
                    target == 'contract' and path.endswith('/recording-contract')):
                calls += 1
                if calls == 2: value['eos' if target == 'capture' else 'channels'] = 1 if target == 'capture' else True
        routes.edit = edit
        with pytest.raises(status.Refused, match='history_changed_during_read'): status.run(**run)
