"""Nine immutable Java-producer deliveries through incremental-span integration.

Strict ASGI admission runs without lifespan/native workers in four separate
processes. Completion is tested separately using an explicitly synthetic peer.
No listening socket, remote service, model, Android runtime or device is used.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
BACKEND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BACKEND), str(BACKEND / 'tests')]


def need(value, message):
    if not value:
        raise AssertionError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def inventory(root):
    return {str(p.relative_to(root)): dict(bytes=p.stat().st_size, sha256=digest(p),
            mtime_ns=p.stat().st_mtime_ns, ctime_ns=p.stat().st_ctime_ns)
            for p in sorted(root.rglob('*')) if p.is_file()}


@contextmanager
def handlers(root):
    from fastapi.testclient import TestClient
    from audio_backend.app import create_app, strict_factory_pending
    from test_recording_session_contract import configuration
    root.mkdir(exist_ok=True)
    fd = os.open(root / 'instance.lock', os.O_RDONLY | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    app = client = None
    try:
        app = create_app(configuration(root), instance_lock_fd=os.dup(fd))
        client = TestClient(app)  # Never enter lifespan: no native thread starts.
        need(not app.state.worker.thread.is_alive(), 'native worker started')
        need(not app.state.instance_lock_lifespan_started, 'native lifespan started')
        yield client, app
    finally:
        if client is not None:
            client.close()
        if app is not None:
            need(not app.state.worker.thread.is_alive(), 'native worker started')
            need(app.state.store.strict_cleanup_confirmed(), 'strict cleanup failed')
            need(app.state.strict_factory_owner.release(), 'owner release failed')
        os.close(fd)
        need(not strict_factory_pending(), 'factory owner remains pending')


def args_for(job, sid, transport):
    from audio_backend import recording_uploader as u
    return dict(job_dir=job, api='http://127.0.0.1:8765', session_id=sid,
                capture_id='extended', exclusive_writer=True,
                acknowledge_span_local_closure=True, transport=transport,
                limits=u.Limits(disk_floor=0, deadline_seconds=60, poll_seconds=.001))


def strict_phase(fixture, output, phase):
    from audio_backend import recorder_incremental_span_intake as span
    from test_recording_session_contract import session, HEADERS
    from test_strict_uploader_admission import StrictRoutes, AdmissionBoundary
    paths = [Path(row['path']) for row in fixture['deliveries']]
    with handlers(output / 'server') as (client, app):
        target = output / 'target.json'
        if phase == 'first':
            sid = session(client, 'nine-real-java-producer-host')
            save(target, dict(session_id=sid))
        else:
            sid = json.loads(target.read_bytes())['session_id']
        routes = StrictRoutes(client, lose_put=(phase == 'finalize'))
        args = args_for(output / 'strict-job', sid, routes)
        results = []
        if phase in ('first', 'second'):
            indices = range(4) if phase == 'first' else range(4, 9)
            if phase == 'second':
                repeated = span.run(**args, delivery=paths[3])
                need(repeated['inputs'] == 4 and repeated['frames'] == 64000, 'midpoint replay failed')
            for index in indices:
                result = span.run(**args, delivery=paths[index], start=index == 0)
                need(result['inputs'] == index + 1, 'input count changed')
                need(result['frames'] == (index + 1) * 16000, 'original frame count changed')
                need(result['published_frames'] == 0, 'tiny inputs unexpectedly published')
                need(result['span']['eos_meaning'] == 'selected_span_only', 'EOS truth lost')
                need(not result['capture_finalized'], 'implicit finalize')
                repeated = span.run(**args, delivery=paths[index])
                need(repeated == result, 'exact delivery retry changed result')
                results.append(result)
            need(all(m == 'GET' for m, _, _ in routes.calls), 'staging performed remote write')
        else:
            try:
                span.run(**args, finalize=True)
                raise AssertionError('strict phase crossed native admission boundary')
            except AdmissionBoundary:
                pass
            need(len(routes.puts) == (9 if phase == 'finalize' else 0), 'unexpected strict PUT count')
            base = f'/sessions/{sid}/recordings/extended/pages/1'
            page = client.get(base, headers=HEADERS).json()
            need(page['state'] == 'open' and not page['released'], 'strict page falsely completed')
            need(len(page['sources']) == 9, 'not nine strict sources')
            need([s['source_start_frame'] for s in page['sources']] == [i * 16000 for i in range(9)],
                 'backend-local frame mapping incorrect')
            if phase == 'retry':
                for index, source in enumerate(page['sources']):
                    response = client.put(base + '/sources?source_start_frame=' + str(index * 16000),
                        headers=HEADERS | {'Content-Type': 'audio/wav', 'Idempotency-Key': source['idempotency_key']},
                        content=(paths[index] / 'audio.wav').read_bytes())
                    need(response.status_code == 202 and response.json()['duplicate'] is True,
                         'real backend exact retry was not duplicate')
                    need(response.json()['source_id'] == source['id'], 'backend retry changed source identity')
            results.append(dict(page=page, direct_backend_duplicate_retries=9 if phase == 'retry' else 0))
        with app.state.store.db() as db:
            counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                      for table in ('sessions', 'recording_pages', 'recording_sources', 'recording_work')}
        need(counts['sessions'] == 1 and counts['recording_work'] == 0, 'unexpected session or native work')
        need(counts['recording_sources'] == (9 if phase in ('finalize', 'retry') else 0), 'source count wrong')
        result = dict(phase=phase, pid=os.getpid(), passed=True, strict_asgi=True,
                      native_lifespan_started=False, native_worker_started=False,
                      counts=counts, requests=routes.calls, admitted_puts=routes.puts, results=results)
    save(output / ('strict-' + phase + '.json'), result)


def protocol(fixture, output, sid):
    from audio_backend import recorder_incremental_span_intake as span
    from audio_backend import recorder_capture_intake as engine
    from audio_backend import recording_uploader as u
    from test_extended_recorder_intake import Peer
    class SyntheticPeer(Peer):
        def __init__(self):
            super().__init__()
            self.calls = []; self.lost_success = False
        def request(self, method, path, **kwargs):
            self.calls.append((method, path))
            value = super().request(method, path.replace('/sessions/' + sid, '/sessions/known'), **kwargs)
            if path.endswith('/recording-contract'):
                value['session_id'] = sid
            if method == 'PUT' and not self.lost_success:
                self.lost_success = True
                raise u.Uncertain('Synthetic protocol success response lost after storing source')
            return value
    peer = SyntheticPeer()
    args = args_for(output / 'protocol-job', sid, peer)
    paths = [Path(row['path']) for row in fixture['deliveries']]
    class Interruption(BaseException):
        pass
    interrupted = False
    results = []
    for index, path in enumerate(paths):
        if index == 4:
            # Stop after admission and one durable piece, then reconstruct a fresh
            # uploader from the exact original delivery and existing journal.
            original = engine.CaptureUploader._new_piece
            def stop_after_piece(self, row):
                nonlocal interrupted
                value = original(self, row)
                if value is not None and not interrupted:
                    interrupted = True
                    raise Interruption()
                return value
            engine.CaptureUploader._new_piece = stop_after_piece
            try:
                try:
                    span.run(**args, delivery=path)
                    raise AssertionError('piece interruption was not reached')
                except Interruption:
                    pass
            finally:
                engine.CaptureUploader._new_piece = original
        result = span.run(**args, delivery=path, start=index == 0)
        need(result['inputs'] == index + 1 and result['frames'] == (index + 1) * 16000, 'protocol count mismatch')
        need(not result['capture_finalized'] and not peer.eos, 'premature EOS')
        need(span.run(**args, delivery=path) == result, 'protocol exact retry mismatch')
        results.append(result)
    need(not peer.pages and not peer.puts, 'tiny staging started publication')
    result = span.run(**args, finalize=True)
    need(result['capture_finalized'] and result['published_frames'] == 144000, 'protocol finalize incomplete')
    need(len(peer.puts) == len(set(peer.puts)) == 9, 'duplicate protocol PUT')
    puts = list(peer.puts)
    need(span.run(**args, finalize=True) == result and peer.puts == puts, 'finalization replay changed protocol')
    checkpoint = json.loads((args['job_dir'] / 'uploader/uploader.json').read_bytes())
    need(checkpoint['inputs_done'] == 9 and checkpoint['pages_done'] == 1, 'v3 archive counts wrong')
    need(checkpoint['active_input'] is None and checkpoint['active_page'] is None and not checkpoint['spool'],
         'completed v3 journal has pending work')
    report = dict(passed=True, synthetic_protocol_only=True, native_processing_completed=False,
                  midpoint_piece_interruption_recovered=interrupted, lost_put_response_reconciled=peer.lost_success,
                  original_inputs_unchanged=True, distinct_source_puts=len(puts), calls=peer.calls,
                  staged_results=results, completion=result, checkpoint=checkpoint)
    save(output / 'protocol.json', report)
    return report


def status_projection(fixture, output, sid, client, app):
    """Seed explicit synthetic completion rows, then exercise only real GET routes."""
    from audio_backend import recorder_incremental_span_status as status
    from audio_backend import recorder_span_status as v2status
    from audio_backend import recording_uploader as u
    from test_recorder_span_status import Routes
    root = output / 'protocol-job'
    state = json.loads((root / 'uploader/uploader.json').read_bytes())
    pages = [json.loads(p.read_bytes())['payload']
             for p in sorted((root / 'uploader/uploader.history').glob('page-*.json'))]
    with app.state.store.db() as db:
        bid = 'synthetic-nine-java-batch'
        db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,mode,state,revision,
            capture_origin_frame,capture_end_frame,eos,created)
            VALUES(?,?,?,'sealed_pages','queued',1,0,?,1,0)''',
            (bid, sid, 'extended', state['emitted_frames']))
        position = 0
        for page in pages:
            start = (page['number'] - 1) * u.PAGE_FRAMES
            end = page['seal']['end_frame']; pid = page['server_id']
            db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,revision,state,released,
                start_frame,end_frame,eos,created) VALUES(?,?,?,?,'published',1,?,?,?,0)''',
                (pid, bid, page['number'], page['seal']['expected_revision'], start, end, int(page['seal']['eos'])))
            for piece in page['pieces']:
                db.execute('''INSERT INTO recording_sources(id,batch_id,page_id,idempotency_key,digest,
                    path,frames,bytes,source_start_frame,format,created) VALUES(?,?,?,?,?,?,?,?,?,?,0)''',
                    (piece['server_id'], bid, pid, piece['key'], piece['digest'], '/synthetic/' + piece['server_id'],
                     piece['frames'], piece['bytes'], piece['capture_start'], 'wav-pcm16le-16000-mono'))
            for cursor in range(start, end, v2status.CHUNK_FRAMES):
                length = min(v2status.CHUNK_FRAMES, end - cursor); cid = 'synthetic-work-' + str(position)
                db.execute('''INSERT INTO chunks(rowid,id,session_id,idempotency_key,digest,path,duration,
                    timeline_start,status,next_offset,created,error) VALUES(?,?,?,?,?,?,?,?,?,?,0,?)''',
                    (10000 + position * 7, cid, sid, cid, 'c' * 64, '/synthetic/' + cid,
                     length / u.RATE, cursor / u.RATE, 'completed', length / u.RATE, 'SYNTHETIC PRIVATE DO NOT LEAK'))
                db.execute('''INSERT INTO recording_work(chunk_id,batch_id,page_id,position,frames,
                    bytes,capture_start_frame,native_receipt_status,receipt_state)
                    VALUES(?,?,?,?,?,?,?,'disabled','gap')''',
                    (cid, bid, pid, position, length, length * 2 + 44, cursor))
                position += 1
    roots = [root, *[Path(row['path']) for row in fixture['deliveries']]]
    before = {str(p): inventory(p) for p in roots}
    db_before = digest(app.state.store.path)
    routes = Routes(client)
    args = args_for(root, sid, routes)
    del args['acknowledge_span_local_closure']
    result = status.run(**args)
    need(result['status'] == 'completed' and result['analysis_completion_observed']
         and result['analysis_completion_verified'], 'synthetic completed projection not verified')
    need(result['inputs'] == 9 and result['frames'] == 144000, 'status count mismatch')
    need(result['span']['original_epoch'] == 7 and result['span']['original_first_sequence'] == 2
         and result['span']['original_last_sequence'] == 10, 'status original origin lost')
    need(result['span']['original_close_reason'] == 'INTERRUPTION'
         and result['span']['recorder_stop_observed'] is False, 'status invented STOP')
    need(result['span']['before_span_gap_frames'] is None and result['span']['after_span_gap_frames'] is None,
         'status invented gap duration')
    expected_mapping = [dict(ordinal=i, original_sequence=i + 2, original_epoch=7,
        original_epoch_frame_start=(i + 2) * 16000, original_epoch_frame_end=(i + 3) * 16000,
        span_start_frame=i * 16000, span_end_frame=(i + 1) * 16000) for i in range(9)]
    need(result['input_mapping'] == expected_mapping, 'status input mapping incorrect')
    need(not any(result[k] for k in ('accuracy_verified', 'physical_continuity_proven',
         'capture_completeness_proven', 'observation_atomic', 'remote_request_identity_verified',
         'concurrent_writer_safety_guaranteed')), 'status overstated evidence')
    need(status.run(**args) == result, 'GET-only status replay changed')
    need(before == {str(p): inventory(p) for p in roots}, 'GET-only status mutated input/journal')
    need(digest(app.state.store.path) == db_before, 'GET-only status mutated backend DB')
    need(all(method == 'GET' for method, _ in routes.calls), 'status sent a write request')
    need('PRIVATE' not in json.dumps(result), 'private diagnostic leaked')
    report = dict(passed=True, synthetic_analysis_rows=True, real_get_routes=True,
                  native_analysis_completed=False, local_bytes_and_times_unchanged=True,
                  backend_db_bytes_unchanged=True, requests=routes.calls, status=result)
    save(output / 'status.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--phase', choices=('first', 'second', 'finalize', 'retry'))
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_bytes())
    need(fixture['current_source_commit_verified'] is True and fixture['selected_deliveries'] == 9,
         'verified nine-producer fixture required')
    output = args.output.resolve()
    if args.phase:
        strict_phase(fixture, output, args.phase)
        return
    need(BACKEND not in output.parents and output != BACKEND, 'runtime output inside backend source')
    output.mkdir(mode=0o700, exist_ok=False)
    roots = [Path(row['path']) for row in fixture['deliveries']]
    originals_before = {str(root): inventory(root) for root in roots}
    source_pins = {str(p.relative_to(BACKEND)): digest(p)
                   for p in sorted((BACKEND / 'audio_backend').rglob('*.py'))}
    phases = []
    for phase in ('first', 'second', 'finalize', 'retry'):
        with (output / ('strict-' + phase + '.log')).open('wb') as log:
            completed = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                '--fixture', str(args.fixture.resolve()), '--output', str(output), '--phase', phase],
                stdout=log, stderr=subprocess.STDOUT, timeout=90)
        need(completed.returncode == 0, 'strict phase failed: ' + phase)
        phases.append(json.loads((output / ('strict-' + phase + '.json')).read_bytes()))
    need(len({p['pid'] for p in phases}) == 4, 'backend phases were not separate processes')
    from test_recording_session_contract import session
    with handlers(output / 'projection-server') as (client, app):
        sid = session(client, 'explicitly-synthetic-completion-projection')
        synthetic = protocol(fixture, output, sid)
        observed = status_projection(fixture, output, sid, client, app)
    need(originals_before == {str(root): inventory(root) for root in roots}, 'producer originals changed')
    pins_after = {str(p.relative_to(BACKEND)): digest(p)
                  for p in sorted((BACKEND / 'audio_backend').rglob('*.py'))}
    changed = sorted(k for k in source_pins.keys() | pins_after.keys() if source_pins.get(k) != pins_after.get(k))
    need(not changed, 'backend sources changed during qualification: ' + ', '.join(changed))
    report = dict(passed=True, fixture_sha256=digest(args.fixture),
                  producer_commit=fixture['producer_commit'], source_pins=source_pins,
                  qualification_script_sha256=digest(__file__), python_version=sys.version,
                  test_helper_pins={name: digest(BACKEND / 'tests' / name) for name in
                    ('test_recording_session_contract.py', 'test_strict_uploader_admission.py',
                     'test_extended_recorder_intake.py', 'test_recorder_span_status.py')},
                  source_changes_during_run=changed, originals_before=originals_before,
                  originals_unchanged=True, strict_phase_processes=[p['pid'] for p in phases],
                  strict_admitted_sources=9, strict_backend_exact_duplicate_retries=9,
                  native_worker_started=False, real_native_analysis_completed=False,
                  synthetic_completion_only=synthetic['completion'],
                  synthetic_status_projection=observed['status'],
                  scope='real Java host producer + actual strict ASGI admission; separate synthetic protocol completion')
    save(output / 'RESULT.json', report)
    print(json.dumps(dict(passed=True, result=str(output / 'RESULT.json'), sha256=digest(output / 'RESULT.json'))))


if __name__ == '__main__':
    main()
