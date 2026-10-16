"""Bounded functional ingestion fixtures. Fake analysis does not qualify ASR."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time
import wave

import pytest
from fastapi.testclient import TestClient

from audio_backend import recording_uploader as u
from audio_backend.app import create_app
from audio_backend.boundary_store import context_parts
from audio_backend.settings import Settings
from audio_backend.worker import Worker
from test_recording_pages import pcm, RATE, Q


class Crash(BaseException): pass


def write_wav(path, frames, channels=1, rate=RATE, offset=0):
    digest = hashlib.sha256()
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(channels); output.setsampwidth(2); output.setframerate(rate)
        cursor = 0
        while cursor < frames:
            count = min(16384 if channels == 2 else 32768, frames-cursor)
            data = pcm(count * channels, (cursor + offset) * channels)
            digest.update(data); output.writeframesraw(data); cursor += count
    return digest.hexdigest()


class Analyzer:
    def __init__(self, config): pass
    def close(self): pass
    def analyze(self, path, **kwargs):
        with wave.open(path, 'rb') as source: frames = source.getnframes()
        return {'segments': [{'start': 0., 'end': frames / RATE, 'text': 'synthetic uploader fixture'}],
                'events': [], 'speaker_turns': [], 'metadata': {}}


def extract(worker, chunk, start, end, wav, tmp, stop, **kwargs):
    with wave.open(str(wav), 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(RATE)
        for part in context_parts(worker.store, chunk, start, end):
            with wave.open(part['path'], 'rb') as source:
                source.setpos(round(part['read_start'] * RATE)); left = round(part['read_duration'] * RATE)
                while left:
                    count = min(32768, left); block = source.readframes(count)
                    assert len(block) == count * 2; output.writeframesraw(block); left -= count


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(Worker, 'extract_pcm', extract)
    cfg = Settings(data_dir=tmp_path / 'server', recording_disk_reserve_bytes=0,
                   disk_reserve_bytes=0, start_worker=False, api_key='fixture-secret')
    return create_app(cfg, Analyzer)


def drain(app):
    while (chunk := app.state.store.claim()) is not None: app.state.worker.process(chunk)


class Routes:
    def __init__(self, client, app, hook=None):
        self.client, self.app, self.hook = client, app, hook
        self.calls = []; self.put_sizes = []; self.seals = []

    def request(self, method, path, body=None, file=None, key=None):
        headers = {'Authorization': 'Bearer fixture-secret'}
        if key: headers['Idempotency-Key'] = key
        kwargs = {}
        if body is not None: kwargs['json'] = body
        if file:
            headers['Content-Length'] = str(file.stat().st_size)
            def blocks():
                with file.open('rb') as source:
                    while block := source.read(u.READ_BYTES):
                        self.put_sizes.append(len(block)); yield block
            kwargs['content'] = blocks()
        if path.endswith('/seal'): self.seals.append(json.loads(json.dumps(body)))
        result = self.client.request(method, path, headers=headers, **kwargs)
        self.calls.append((method, path, result.status_code))
        if result.status_code >= 400:
            if result.status_code == 429: drain(self.app)
            raise u.HTTPFailure(result.status_code, result.text, .001)
        data = result.json()
        if self.hook: self.hook(method, path, data)
        return data


@contextmanager
def local_http(app):
    import uvicorn
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(128)
    import sitecustomize
    if hasattr(sitecustomize, 'allow_owned_loopback'): sitecustomize.allow_owned_loopback(sock)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False, lifespan='on'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True); thread.start()
    try:
        end = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > end: raise RuntimeError('Owned loopback server startup failed')
            time.sleep(.01)
        yield 'http://127.0.0.1:' + str(sock.getsockname()[1])
    finally:
        server.should_exit = True; thread.join(timeout=15); sock.close()
        assert not thread.is_alive(), 'Owned HTTP server did not stop'


def arguments(tmp_path, sid, path=None, **kw):
    return dict(input=path or tmp_path / 'original.wav', session_id=sid, capture_id='uploader.capture',
                capture_origin_frame=100003, state_dir=tmp_path / 'state', final=True,
                limits=u.Limits(disk_floor=0, poll_seconds=.001, deadline_seconds=180), **kw)


def journal(tmp_path): return json.loads((tmp_path / 'state/uploader.json').read_text())


def counts(app):
    with app.state.store.db() as db:
        return tuple(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                     for table in ('recording_pages', 'recording_sources', 'recording_work', 'segments'))


def test_logical_hour_real_http_streaming_conservation_backpressure(tmp_path, app, monkeypatch):
    frames = 3600 * RATE
    original = tmp_path / 'original.wav'; expected = write_wav(original, frames)
    with original.open('rb') as stream: initial = u._hash(stream)
    with local_http(app) as api:
        sid = app.state.store.create_session('one finalized hour')['id']
        job = u.Uploader(**arguments(tmp_path, sid, api=api))
        job.http.token = 'fixture-secret'
        original_request = job.http.request; lost = set(); seals = {}; backpressure = []; first_publish = []
        def request(method, path, **kwargs):
            if path.endswith('/seal'):
                body = kwargs['body']; key = body['idempotency_key']
                if key in seals: assert seals[key] == body
                seals[key] = dict(body)
            try: result = original_request(method, path, **kwargs)
            except u.HTTPFailure as exc:
                if exc.status == 429:
                    backpressure.append(job.reader.frames)
                    assert job.journal.max_spool <= u.PAGE_BYTES
                    drain(app)
                    exc.retry_after = .001
                raise
            if method == 'POST' and path.endswith('/seal') and result['released']:
                if not first_publish: first_publish.append(job.reader.frames)
            if method == 'PUT' and 'put' not in lost:
                lost.add('put'); raise u.Uncertain('Injected lost source acceptance response')
            if method == 'POST' and '/pages/2/seal' in path and 'seal' not in lost:
                lost.add('seal'); raise u.Uncertain('Injected lost published seal response')
            return result
        job.http.request = request
        result = job.run()
        assert result['frames'] == 57_600_000 and result['pages'] == 13 and result['sources'] == 37
        assert first_publish == [u.PAGE_FRAMES] and first_publish[0] < frames
        assert backpressure and len(seals) == 13 and lost == {'put', 'seal'}
        assert result['max_spool_bytes'] == u.PAGE_BYTES
        assert result['max_pcm_read'] <= 65536 and job.http.max_read <= 65536 and job.http.max_send <= 65536
        drain(app)
        store = app.state.store
        with store.db() as db:
            work = [dict(r) for r in db.execute('''SELECT c.*,w.frames,w.position,w.capture_start_frame
                FROM recording_work w JOIN chunks c ON c.id=w.chunk_id ORDER BY w.position''')]
            assert db.execute('SELECT COUNT(*) FROM stream_chunks WHERE stream_final=1').fetchone()[0] == 1
            assert db.execute('SELECT COUNT(*) FROM recording_ranges').fetchone()[0] >= 343
        assert len(work) == 343 and sum(w['frames'] for w in work) == frames
        assert all(w['status'] == 'completed' for w in work)
        assert [w['capture_start_frame'] for w in work] == [100003 + n * Q for n in range(343)]
        actual = hashlib.sha256()
        for item in work:
            with wave.open(item['path'], 'rb') as source:
                while block := source.readframes(32768): actual.update(block)
        assert actual.hexdigest() == expected
        before = counts(app)
        resume = u.Uploader(**arguments(tmp_path, sid, api=api)); resume.http.token = 'fixture-secret'
        assert resume.run()['complete'] and counts(app) == before
        with original.open('rb') as stream: assert u._hash(stream) == initial
        saved = journal(tmp_path)
        assert saved['complete'] and saved['clean_eof'] and saved['emitted_frames'] == frames
        assert 'fixture-secret' not in json.dumps(saved)
        assert len(saved['pages']) == 13 and sum(len(p['pieces']) for p in saved['pages']) == 37
        assert not list((tmp_path / 'state').glob('*.wav*'))
        print(json.dumps({'hour_result': result, 'backpressure_at_frames': backpressure,
                          'work_chunks': len(work), 'pcm_sha256': actual.hexdigest(), 'retained_source_sha256': initial}))


@pytest.mark.parametrize('frames', [1, u.PAGE_FRAMES, u.PAGE_FRAMES + 1])
def test_exact_grid_and_one_frame_tail(tmp_path, app, frames):
    write_wav(tmp_path / 'original.wav', frames)
    with TestClient(app) as client:
        sid = app.state.store.create_session('grid')['id']; routes = Routes(client, app)
        result = u.Uploader(**arguments(tmp_path, sid, transport=routes)).run()
        data = journal(tmp_path)
        assert result['frames'] == frames and data['complete']
        assert bool(data.get('finish')) == (frames == u.PAGE_FRAMES)
        with app.state.store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM stream_chunks WHERE stream_final=1').fetchone()[0] == 1
        assert max(routes.put_sizes) <= 65536


@pytest.mark.parametrize('point', ['put', 'seal', 'cleanup', 'finish'])
def test_restart_after_uncertain_acceptance_and_publication(tmp_path, app, monkeypatch, point):
    frames = u.PAGE_FRAMES if point == 'finish' else Q + 1
    write_wav(tmp_path / 'original.wav', frames)
    (tmp_path / 'state').mkdir(); unrelated = tmp_path / 'state/unrelated.txt'; unrelated.write_text('preserve')
    with TestClient(app) as client:
        sid = app.state.store.create_session('restart')['id']; hit = []
        def hook(method, path, data):
            if hit: return
            if ((point == 'put' and method == 'PUT') or (point == 'seal' and path.endswith('/seal'))
                    or (point == 'finish' and path.endswith('/finish'))):
                hit.append(True); raise Crash(point)
        routes = Routes(client, app, hook)
        job = u.Uploader(**arguments(tmp_path, sid, transport=routes))
        if point == 'cleanup':
            real_cleanup = job.journal.cleanup
            def cleanup():
                if job.journal.data['published_frames'] and not hit:
                    hit.append(True); raise Crash(point)
                return real_cleanup()
            monkeypatch.setattr(job.journal, 'cleanup', cleanup)
        with pytest.raises(Crash): job.run()
        assert hit and not journal(tmp_path)['complete']
        original_counts = counts(app)
        resumed = u.Uploader(**arguments(tmp_path, sid, transport=Routes(client, app)))
        assert resumed.run()['complete']
        assert counts(app)[:2] == original_counts[:2]
        assert counts(app)[2] == (frames + Q - 1) // Q
        assert unrelated.read_text() == 'preserve' and (tmp_path / 'original.wav').exists()
        assert not list((tmp_path / 'state').glob('*.wav*'))


def test_changed_input_and_recipe_refused_before_new_request(tmp_path, app, monkeypatch):
    write_wav(tmp_path / 'original.wav', Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('identity')['id']; routes = Routes(client, app)
        u.Uploader(**arguments(tmp_path, sid, transport=routes)).run()
        real_recipe = u.decoder_recipe
        monkeypatch.setattr(u, 'decoder_recipe', lambda *a: {**real_recipe(*a), 'altered': True})
        before = len(routes.calls)
        with pytest.raises(u.SourceChanged): u.Uploader(**arguments(tmp_path, sid, transport=routes)).run()
        assert len(routes.calls) == before
        monkeypatch.setattr(u, 'decoder_recipe', real_recipe)
        with (tmp_path / 'original.wav').open('r+b') as stream: stream.seek(44); stream.write(b'xx')
        with pytest.raises(u.SourceChanged): u.Uploader(**arguments(tmp_path, sid, transport=routes)).run()
        assert len(routes.calls) == before


def test_truncated_wav_no_eos_after_published_prefix(tmp_path, app):
    path = tmp_path / 'original.wav'; write_wav(path, u.PAGE_FRAMES + Q)
    with path.open('r+b') as stream: stream.truncate(path.stat().st_size - 100)
    with TestClient(app) as client:
        sid = app.state.store.create_session('late truncation')['id']
        with pytest.raises(u.Incomplete, match='Truncated'):
            u.Uploader(**arguments(tmp_path, sid, transport=Routes(client, app))).run()
        status = app.state.recordings.status(sid, 'uploader.capture')
        assert not status['eos'] and status['released_pages'] == 1
        assert status['capture_end_frame'] == 100003 + u.PAGE_FRAMES
        assert not journal(tmp_path)['clean_eof']


def test_empty_and_nonfinal_multiple_inputs_rejected(tmp_path):
    args = arguments(tmp_path, 'sid')
    with pytest.raises(ValueError): u.Uploader(**{**args, 'final': False})
    with pytest.raises(ValueError): u.Uploader(**{**args, 'input': ['a', 'b']})
    with pytest.raises(ValueError): u.Limits(deadline_seconds=float('inf'))
    with pytest.raises(ValueError): u.Limits(output_frames=2*3600*RATE+1)
    with pytest.raises(ValueError): u.HTTPTransport('http://example.com', u.Guard(u.Limits()))


def test_journal_limits_lock_and_owned_cleanup(tmp_path):
    guard = u.Guard(u.Limits(disk_floor=0)); j = u.Journal(tmp_path / 'state', guard)
    j.lock.acquire()
    try:
        other = u.Journal(j.path, guard)
        with pytest.raises(RuntimeError): other.lock.acquire()
        j.data = {'job': 'a'*32, 'spool': ['../original.wav']}
        with pytest.raises(u.Incomplete): j.cleanup()
        (j.path / 'uploader.json').write_bytes(b'x' * (u.JSON_BYTES+1))
        with pytest.raises(u.Incomplete): j.load()
        (j.path / 'uploader.json').write_text('{"policy":"x","policy":"y"}')
        with pytest.raises(u.Incomplete): j.load()
    finally: j.lock.release()


def test_disk_reservation_failure_does_not_create_server_capture(tmp_path, app, monkeypatch):
    write_wav(tmp_path / 'original.wav', Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('disk full')['id']
        monkeypatch.setattr(u.shutil, 'disk_usage', lambda p: type('Disk', (), {'free': 0})())
        with pytest.raises(u.Incomplete, match='Disk safety'):
            u.Uploader(**arguments(tmp_path, sid, transport=Routes(client, app))).run()
        assert counts(app)[:3] == (0, 0, 0)
        assert not list((tmp_path / 'state').glob('*.wav*'))


@pytest.mark.parametrize('status', [401, 409, 507])
def test_permanent_http_error_stops_without_eos(tmp_path, app, status):
    write_wav(tmp_path / 'original.wav', Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('failure')['id']; routes = Routes(client, app)
        real = routes.request
        def request(method, path, **kwargs):
            if method == 'PUT': raise u.HTTPFailure(status, 'injected')
            return real(method, path, **kwargs)
        routes.request = request
        with pytest.raises(u.HTTPFailure) as error:
            u.Uploader(**arguments(tmp_path, sid, transport=routes)).run()
        assert error.value.status == status
        assert not app.state.recordings.status(sid, 'uploader.capture')['eos']
        assert list((tmp_path / 'state').glob('*.wav'))


def fake_decoder_recipe(command):
    return {'kind': 'ffmpeg', 'tools': {'ffmpeg': {'path': command[0]}}, 'arguments': command[1:]}


@pytest.mark.parametrize('script,pattern', [
    ("import os;os.write(1,b'abc')", 'Odd trailing'),
    ("import os,sys;os.write(1,b'ab');sys.exit(7)", 'Decoder failed')])
def test_decoder_odd_pcm_and_nonzero_exit_reaped(tmp_path, script, pattern):
    path = tmp_path / 'x.bin'; path.write_bytes(b'placeholder')
    guard = u.Guard(u.Limits(disk_floor=0, deadline_seconds=10))
    original = u.Original(path, guard)
    reader = u.PCMReader(original, fake_decoder_recipe([sys_executable(), '-c', script]), guard)
    try:
        with pytest.raises(u.Incomplete, match=pattern):
            while reader.read(32768): pass
    finally: reader.close(); original.close()
    assert reader.child.returncode is not None
    assert not reader.stderr_thread.is_alive()


def sys_executable():
    import sys
    return sys.executable


def test_decoder_stderr_flood_and_cancel_are_bounded_and_reaped(tmp_path):
    path = tmp_path / 'x.bin'; path.write_bytes(b'placeholder')
    stop = threading.Event(); guard = u.Guard(u.Limits(disk_floor=0, deadline_seconds=10), stop.is_set)
    original = u.Original(path, guard)
    script = "import os,time;os.write(2,b'x'*200000);os.write(1,b'ab');time.sleep(30)"
    reader = u.PCMReader(original, fake_decoder_recipe([sys_executable(), '-c', script]), guard)
    try:
        assert reader.read(32768) == b'ab'
        assert len(reader.stderr) <= 8192
        stop.set()
        with pytest.raises(InterruptedError): reader.read(32768)
    finally: reader.close(); original.close()
    assert reader.child.returncode is not None and not reader.stderr_thread.is_alive()


def test_real_ffmpeg_stereo_44100_matches_continuous_decode(tmp_path, app):
    if not Path('/usr/bin/ffmpeg').is_file(): pytest.skip('Installed FFmpeg unavailable')
    path = tmp_path / 'stereo.wav'; write_wav(path, 44100, 2, 44100)
    guard = u.Guard(u.Limits(disk_floor=0, deadline_seconds=30))
    source = u.Original(path, guard)
    try:
        recipe = u.decoder_recipe(source, guard)
        assert recipe['kind'] == 'ffmpeg'
        command = [recipe['tools']['ffmpeg']['path']] + [str(path) if a == '{held_fd}' else a for a in recipe['arguments']]
        reference = u._collect(command, guard, cap=65536)
    finally: source.close()
    with TestClient(app) as client:
        sid = app.state.store.create_session('continuous FFmpeg')['id']
        job = u.Uploader(**arguments(tmp_path, sid, path=path, transport=Routes(client, app)))
        result = job.run(); assert result['frames'] == 16000
        saved = journal(tmp_path)
        assert saved['identity']['decoder']['tools']['ffmpeg']['sha256']
        with app.state.store.db() as db:
            stored = db.execute('SELECT path FROM recording_sources').fetchone()[0]
        with wave.open(stored, 'rb') as stream: assert stream.readframes(16000) == reference
        assert u.Uploader(**arguments(tmp_path, sid, path=path, transport=Routes(client, app))).run()['complete']


@pytest.mark.parametrize('name', ['uploader.json.next', 'uploader.lock', 'uploader.json'])
@pytest.mark.parametrize('alias', [False, True])
def test_original_control_path_collisions_never_modify_input(tmp_path, name, alias):
    state = tmp_path / 'state'; state.mkdir()
    path = tmp_path / 'original.wav' if alias else state / name
    write_wav(path, 100); before = path.read_bytes()
    if alias: os.link(path, state / name)
    with pytest.raises(ValueError): u.Uploader(**arguments(tmp_path, 'sid', path=path))
    assert path.read_bytes() == before


def test_preparing_and_pending_receipts_hold_page_and_verify_frozen_seal(tmp_path, app):
    write_wav(tmp_path / 'original.wav', Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('pending states')['id']; routes = Routes(client, app)
        real = routes.request; phase = []; counts_before = []
        def request(method, path, **kwargs):
            result = real(method, path, **kwargs)
            if method == 'POST' and path.endswith('/seal') and not phase:
                phase.append('lost'); raise u.Uncertain('Injected in-flight response')
            if method == 'GET' and '/pages/1' in path and phase == ['lost']:
                phase.append('preparing')
                return {**result, 'released': False, 'state': 'preparing'}
            if method == 'POST' and path.endswith('/seal') and phase == ['lost', 'preparing']:
                phase.append('pending')
                counts_before.append(counts(app))
                return {**result, 'released': False, 'state': 'pending_receipts'}
            return result
        routes.request = request
        result = u.Uploader(**arguments(tmp_path, sid, transport=routes)).run()
        assert result['complete'] and phase == ['lost', 'preparing', 'pending']
        assert len(routes.seals) == 3 and all(x == routes.seals[0] for x in routes.seals)
        assert counts(app) == counts_before[0]


def test_saved_seal_identity_mismatch_is_not_adopted(tmp_path, app):
    write_wav(tmp_path / 'original.wav', Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('seal identity')['id']
        u.Uploader(**arguments(tmp_path, sid, transport=Routes(client, app))).run()
        data = journal(tmp_path); data['pages'][0]['seal']['idempotency_key'] += '.wrong'
        (tmp_path / 'state/uploader.json').write_text(json.dumps(data))
        before = counts(app)
        with pytest.raises(u.HTTPFailure, match='409'):
            u.Uploader(**arguments(tmp_path, sid, transport=Routes(client, app))).run()
        assert counts(app) == before


@pytest.mark.parametrize('declared', [True, False])
def test_http_response_cap_and_read_size(monkeypatch, declared):
    reads = []; connection = []
    class Response:
        status = 200
        left = u.JSON_BYTES + 1
        def getheader(self, name, default=None):
            if name == 'Content-Length': return str(self.left) if declared else None
            return default
        def read1(self, size):
            reads.append(size); count = min(size, self.left); self.left -= count
            return b' ' * count
    class Connection:
        sock = None
        def __init__(self, *a, **kw): self.closed = False; connection.append(self)
        def putrequest(self, *a): pass
        def putheader(self, *a): pass
        def endheaders(self): pass
        def getresponse(self): return Response()
        def close(self): self.closed = True
    monkeypatch.setattr(u.http.client, 'HTTPConnection', Connection)
    http = u.HTTPTransport('http://127.0.0.1:8765', u.Guard(u.Limits()))
    with pytest.raises(u.Incomplete, match='HTTP response exceeds bound'): http.request('GET', '/fixture')
    assert connection[0].closed and (not reads or max(reads) <= 65536)


@pytest.mark.parametrize('error_number', [28, 122])
def test_owned_fsync_disk_full_keeps_prefix_and_resumes(tmp_path, app, monkeypatch, error_number):
    path = tmp_path / 'original.wav'; write_wav(path, u.PAGE_FRAMES + Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('late spool disk full')['id']; original_fsync = u.os.fsync; hit = []
        def fsync(fd):
            name = os.readlink('/proc/self/fd/' + str(fd))
            if name.startswith(str(tmp_path / 'state')) and '-p2-s0.wav.part' in name and not hit:
                hit.append(True); raise OSError(error_number, 'injected capacity failure')
            return original_fsync(fd)
        monkeypatch.setattr(u.os, 'fsync', fsync)
        with pytest.raises(OSError): u.Uploader(**arguments(tmp_path, sid, transport=Routes(client, app))).run()
        assert hit
        status = app.state.recordings.status(sid, 'uploader.capture')
        assert status['released_pages'] == 1 and not status['eos']
        assert journal(tmp_path)['published_frames'] == u.PAGE_FRAMES
        monkeypatch.setattr(u.os, 'fsync', original_fsync)
        assert u.Uploader(**arguments(tmp_path, sid, transport=Routes(client, app))).run()['complete']
        assert counts(app)[:3] == (2, 4, 29)
        assert path.exists()


def test_missing_decoder_refused_before_capture_or_journal(tmp_path, app):
    path = tmp_path / 'stereo.wav'; write_wav(path, 100, 2, 44100)
    with TestClient(app) as client:
        sid = app.state.store.create_session('missing decoder')['id']; routes = Routes(client, app)
        with pytest.raises(FileNotFoundError):
            u.Uploader(**arguments(tmp_path, sid, path=path, transport=routes), ffmpeg=str(tmp_path / 'absent-ffmpeg')).run()
        assert not routes.calls and counts(app)[:3] == (0, 0, 0)
        assert not (tmp_path / 'state/uploader.json').exists() and path.exists()


def append_arguments(tmp_path, sid, path, input_id, **kwargs):
    args = arguments(tmp_path, sid, path=path, **kwargs)
    args.pop('final'); args.update(append=True, input_id=input_id)
    return args


def finalize_arguments(tmp_path, sid, **kwargs):
    args = arguments(tmp_path, sid, **kwargs)
    args.pop('input'); args.pop('final'); args['finalize'] = True
    return args


def pcm_hash(paths):
    digest = hashlib.sha256()
    for path in paths:
        with wave.open(str(path), 'rb') as source:
            while block := source.readframes(32768): digest.update(block)
    return digest.hexdigest()


def test_two_logical_hours_append_real_http_exact_conservation(tmp_path, app, monkeypatch):
    frames = 3600 * RATE
    first, second = tmp_path / 'hour-a.wav', tmp_path / 'hour-b.wav'
    write_wav(first, frames); write_wav(second, frames, offset=frames)
    expected = pcm_hash([first, second]); originals = []
    for path in (first, second):
        with path.open('rb') as stream: originals.append(u._hash(stream))
    with local_http(app) as api:
        sid = app.state.store.create_session('two separate logical hours')['id']
        lost = set(); seals = {}; backpressure = []; decoded = []
        real_reader = u.PCMReader
        def reader(original, *args):
            decoded.append(str(original.path)); return real_reader(original, *args)
        monkeypatch.setattr(u, 'PCMReader', reader)
        def job_for(path=None, input_id=None):
            args = (append_arguments(tmp_path, sid, path, input_id, api=api) if path else
                    finalize_arguments(tmp_path, sid, api=api))
            job = u.AppendUploader(**args); job.http.token = 'fixture-secret'; request = job.http.request
            def wrapped(method, route, **kwargs):
                if route.endswith('/seal'):
                    body = kwargs['body']; key = body['idempotency_key']
                    if key in seals: assert seals[key] == body
                    seals[key] = dict(body)
                try: reply = request(method, route, **kwargs)
                except u.HTTPFailure as exc:
                    if exc.status == 429:
                        backpressure.append(job.journal.data['emitted_frames']); drain(app); exc.retry_after = .001
                    raise
                tag = ('put' if method == 'PUT' else 'seal13' if method == 'POST' and route.endswith('/pages/13/seal') else None)
                if tag and tag not in lost:
                    lost.add(tag); raise u.Uncertain('Injected accepted response loss')
                return reply
            job.http.request = wrapped; return job
        result_a = job_for(first, 'hour-a').run()
        assert result_a['input_complete'] and result_a['awaiting_input'] and not result_a['capture_finalized']
        assert (result_a['frames'], result_a['published_frames'], result_a['staged_frames']) == (57_600_000, 56_448_000, 1_152_000)
        assert app.state.recordings.status(sid, 'uploader.capture')['released_pages'] == 12
        assert not app.state.recordings.status(sid, 'uploader.capture')['eos']
        saved = journal(tmp_path); tail = saved['pages'][-1]
        assert len(tail['pieces']) == 1 and not tail['released'] and not tail.get('server_id')
        tail_path = tmp_path / 'state' / tail['pieces'][0]['name']
        assert tail_path.stat().st_size == 2_304_044
        tail_hash = hashlib.sha256(tail_path.read_bytes()).hexdigest()
        before = counts(app); assert job_for(first, 'hour-a').run()['input_complete']
        assert counts(app) == before and hashlib.sha256(tail_path.read_bytes()).hexdigest() == tail_hash
        assert decoded == [str(first)]
        result_b = job_for(second, 'hour-b').run()
        assert (result_b['frames'], result_b['published_frames'], result_b['staged_frames']) == (115_200_000, 112_896_000, 2_304_000)
        assert not result_b['capture_finalized'] and not app.state.recordings.status(sid, 'uploader.capture')['eos']
        assert decoded == [str(first), str(second)]
        saved = journal(tmp_path); seam = saved['pages'][12]['pieces']
        assert [p['input_id'] for p in seam] == ['hour-a', 'hour-b', 'hour-b']
        assert [p['frames'] for p in seam] == [72 * RATE, 120 * RATE, 102 * RATE]
        assert [p['input_start'] for p in seam] == [3528 * RATE, 0, 120 * RATE]
        assert saved['inputs'][1]['normalized_start'] == frames
        assert result_b['max_spool_bytes'] <= u.PAGE_FRAMES * 2 + u.PAGE_SOURCES * 44
        done = job_for().run(); drain(app)
        assert done['capture_finalized'] and done['frames'] == 115_200_000
        assert (done['pages'], done['sources']) == (25, 74)
        assert backpressure and lost == {'put', 'seal13'} and len(seals) == 25
        assert decoded == [str(first), str(second)]
        with app.state.store.db() as db:
            work = [dict(r) for r in db.execute('''SELECT c.path,c.status,w.* FROM recording_work w
                JOIN chunks c ON c.id=w.chunk_id ORDER BY w.position''')]
            ranges = [dict(r) for r in db.execute('''SELECT r.*,s.source_start_frame AS source_capture
                FROM recording_ranges r JOIN recording_sources s ON s.id=r.source_id''')]
            assert db.execute('SELECT COUNT(*) FROM stream_chunks WHERE stream_final=1').fetchone()[0] == 1
        assert len(work) == 686 and [w['frames'] for w in work] == [Q] * 685 + [120_000]
        assert all(w['status'] == 'completed' for w in work)
        assert [w['capture_start_frame'] for w in work] == [100003 + n * Q for n in range(686)]
        assert sum(r['frames'] for r in ranges if r['relation'] == 'owner') == 115_200_000
        assert all(r['capture_start_frame'] == r['source_capture'] + r['source_start_frame'] for r in ranges)
        seam_ranges = [r for r in ranges if r['chunk_id'] == work[342]['chunk_id']]
        assert sorted(r['frames'] for r in seam_ranges) == [24_000, 144_000]
        assert pcm_hash([w['path'] for w in work]) == expected
        before = counts(app)
        assert job_for(first, 'hour-a').run()['capture_finalized']
        assert job_for(second, 'hour-b').run()['capture_finalized']
        assert job_for().run()['capture_finalized'] and counts(app) == before
        assert decoded == [str(first), str(second)] and not list((tmp_path / 'state').glob('*.wav*'))
        for path, original in zip((first, second), originals):
            with path.open('rb') as stream: assert u._hash(stream) == original
        print(json.dumps({'two_hour_result': done, 'work_chunks': len(work), 'pcm_sha256': expected,
                          'backpressure_at_frames': backpressure, 'decoded_originals': len(decoded)}))


@pytest.mark.parametrize('point', ['eof', 'tail', 'put', 'seal', 'cleanup', 'intent', 'finalseal', 'finish'])
def test_append_crash_recovery_preserves_lineage_and_keys(tmp_path, app, monkeypatch, point):
    first, second = tmp_path / 'a.wav', tmp_path / 'b.wav'
    a_frames = Q + 7; total = u.PAGE_FRAMES if point == 'finish' else u.PAGE_FRAMES + Q + 3
    write_wav(first, a_frames); write_wav(second, total - a_frames, offset=a_frames)
    with TestClient(app) as client:
        sid = app.state.store.create_session('append crash ' + point)['id']; routes = Routes(client, app)
        u.AppendUploader(**append_arguments(tmp_path, sid, first, 'a', transport=routes)).run()
        retained = journal(tmp_path)['pages'][0]['pieces'][0]
        retained_bytes = (tmp_path / 'state' / retained['name']).read_bytes(); hit = []
        if point in ('intent', 'finalseal', 'finish'):
            u.AppendUploader(**append_arguments(tmp_path, sid, second, 'b', transport=routes)).run()
            args = finalize_arguments(tmp_path, sid, transport=routes)
        else: args = append_arguments(tmp_path, sid, second, 'b', transport=routes)
        job = u.AppendUploader(**args); save = job.journal.save; cleanup = job.journal.cleanup
        def hooked_save():
            save(); d = job.journal.data
            if not hit and ((point == 'eof' and len(d['inputs']) == 2 and d['inputs'][-1]['clean_eof'])
                    or (point == 'tail' and len(d['inputs']) == 2 and d['inputs'][-1]['frames'] > 0)
                    or (point == 'intent' and d['finalize_requested'])):
                hit.append(point); raise Crash(point)
        def hooked_cleanup():
            if point == 'cleanup' and job.journal.data['published_frames'] and not hit:
                hit.append(point); raise Crash(point)
            cleanup()
        def hook(method, route, data):
            wanted = ((point == 'put' and method == 'PUT') or
                      (point in ('seal', 'finalseal') and method == 'POST' and route.endswith('/seal')) or
                      (point == 'finish' and route.endswith('/finish')))
            if wanted and not hit: hit.append(point); raise Crash(point)
        monkeypatch.setattr(job.journal, 'save', hooked_save)
        monkeypatch.setattr(job.journal, 'cleanup', hooked_cleanup); routes.hook = hook
        with pytest.raises(Crash): job.run()
        assert hit; routes.hook = None
        saved = journal(tmp_path)
        if point == 'tail':
            assert (tmp_path / 'state' / retained['name']).read_bytes() == retained_bytes
        frozen = {r['number']: r['seal'] for r in saved['pages'] if r.get('seal')}
        resumed = u.AppendUploader(**args).run()
        assert resumed['capture_finalized'] if point in ('intent', 'finalseal', 'finish') else resumed['input_complete']
        done = u.AppendUploader(**finalize_arguments(tmp_path, sid, transport=routes)).run()
        assert done['capture_finalized'] and done['frames'] == total
        saved = journal(tmp_path)
        assert all(saved['pages'][n - 1]['seal'] == seal for n, seal in frozen.items())
        before = counts(app)
        assert u.AppendUploader(**append_arguments(tmp_path, sid, first, 'a', transport=routes)).run()['input_complete']
        assert u.AppendUploader(**append_arguments(tmp_path, sid, second, 'b', transport=routes)).run()['input_complete']
        assert u.AppendUploader(**finalize_arguments(tmp_path, sid, transport=routes)).run()['capture_finalized']
        assert counts(app) == before and before[2] == (total + Q - 1) // Q
        assert first.exists() and second.exists() and not list((tmp_path / 'state').glob('*.wav*'))


def test_append_conflicts_refuse_before_network_and_old_journals_stay_separate(tmp_path, app, monkeypatch):
    first, second = tmp_path / 'a.wav', tmp_path / 'b.wav'; write_wav(first, Q); write_wav(second, Q, offset=Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('append conflicts')['id']; routes = Routes(client, app)
        args = append_arguments(tmp_path, sid, first, 'a', transport=routes)
        u.AppendUploader(**args, max_inputs=2).run(); before = len(routes.calls)
        for changed in ({'input_start_frame': 100004}, {'input': second}, {'max_inputs': 3}):
            with pytest.raises((u.Incomplete, u.SourceChanged)): u.AppendUploader(**{**args, **changed}).run()
            assert len(routes.calls) == before
        for start in (100003, 100003 + Q - 1, 100003 + Q + 1):
            with pytest.raises(u.Incomplete, match='contiguous'):
                u.AppendUploader(**append_arguments(tmp_path, sid, second, 'b', transport=routes), input_start_frame=start).run()
            assert len(routes.calls) == before
        with pytest.raises(u.SourceChanged, match='stable input ID'):
            u.AppendUploader(**{**args, 'input_id': 'another'}).run()
        assert len(routes.calls) == before
        real_recipe = u.decoder_recipe
        monkeypatch.setattr(u, 'decoder_recipe', lambda *a: {**real_recipe(*a), 'changed': True})
        with pytest.raises(u.SourceChanged): u.AppendUploader(**args).run()
        assert len(routes.calls) == before; monkeypatch.setattr(u, 'decoder_recipe', real_recipe)
        with pytest.raises(u.Incomplete, match='policy'):
            u.Uploader(**arguments(tmp_path, sid, path=first, transport=routes)).run()
        assert len(routes.calls) == before
        u.AppendUploader(**finalize_arguments(tmp_path, sid, transport=routes)).run(); before = len(routes.calls)
        with pytest.raises(u.Incomplete, match='fenced'):
            u.AppendUploader(**append_arguments(tmp_path, sid, second, 'b', transport=routes)).run()
        assert len(routes.calls) == before


@pytest.mark.parametrize('kind', ['missing', 'corrupt', 'changed_original'])
def test_completed_original_tail_integrity_fails_closed(tmp_path, app, kind):
    path = tmp_path / 'a.wav'; write_wav(path, Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('tail integrity')['id']; routes = Routes(client, app)
        args = append_arguments(tmp_path, sid, path, 'a', transport=routes)
        u.AppendUploader(**args).run(); before = len(routes.calls)
        tail = tmp_path / 'state' / journal(tmp_path)['pages'][0]['pieces'][0]['name']
        if kind == 'missing': tail.unlink()
        else:
            target = tail if kind == 'corrupt' else path
            with target.open('r+b') as stream: stream.seek(44); stream.write(b'xx')
        with pytest.raises((u.Incomplete, u.SourceChanged)): u.AppendUploader(**args).run()
        assert len(routes.calls) == before and counts(app)[:3] == (0, 0, 0)


def test_many_originals_slot_reservation_and_frozen_registry(tmp_path, app):
    with TestClient(app) as client:
        sid = app.state.store.create_session('many immutable originals')['id']; routes = Routes(client, app)
        for number in range(14):
            path = tmp_path / f'{number}.wav'; write_wav(path, 1, offset=number)
            result = u.AppendUploader(**append_arguments(tmp_path, sid, path, f'input-{number}', transport=routes)).run()
            assert result['frames'] == number + 1
        saved = journal(tmp_path); assert len(saved['inputs']) == 14 and len(saved['pages'][0]['pieces']) == 14
        assert saved['pages'][0]['pieces'][13]['name'].endswith('-s13.wav')
        before = len(routes.calls); path = tmp_path / 'refused.wav'; write_wav(path, 1)
        with pytest.raises(u.Incomplete, match='source slots'):
            u.AppendUploader(**append_arguments(tmp_path, sid, path, 'refused', transport=routes)).run()
        assert len(routes.calls) == before and len(journal(tmp_path)['inputs']) == 14
        assert u.AppendUploader(**finalize_arguments(tmp_path, sid, transport=routes)).run()['capture_finalized']
        assert counts(app)[:3] == (1, 14, 1)


def test_registry_and_aggregate_limits_do_not_duplicate_or_finalize(tmp_path, app):
    first, second = tmp_path / 'a.wav', tmp_path / 'b.wav'; write_wav(first, Q); write_wav(second, Q + 1)
    with TestClient(app) as client:
        sid = app.state.store.create_session('aggregate bounds')['id']; routes = Routes(client, app)
        limits = u.Limits(disk_floor=0, poll_seconds=.001, output_frames=Q * 2, deadline_seconds=30)
        args = {**append_arguments(tmp_path, sid, first, 'a', transport=routes), 'limits': limits}
        u.AppendUploader(**args, max_inputs=2).run()
        with pytest.raises(u.Incomplete, match='Aggregate decoded'):
            u.AppendUploader(**{**args, 'input': second, 'input_id': 'b'}).run()
        saved = journal(tmp_path)
        assert not saved['inputs'][-1]['clean_eof'] and not saved['complete'] and saved['emitted_frames'] <= Q * 2
        assert counts(app)[:3] == (0, 0, 0)
        with pytest.raises(u.Incomplete, match='clean EOF'):
            u.AppendUploader(**{**finalize_arguments(tmp_path, sid, transport=routes), 'limits': limits}).run()


def test_active_missing_piece_rebuilt_but_completed_original_never_decoded(tmp_path, app, monkeypatch):
    first, second = tmp_path / 'a.wav', tmp_path / 'b.wav'; write_wav(first, Q); write_wav(second, Q + 7, offset=Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('bounded active replay')['id']; routes = Routes(client, app)
        u.AppendUploader(**append_arguments(tmp_path, sid, first, 'a', transport=routes)).run()
        args = append_arguments(tmp_path, sid, second, 'b', transport=routes)
        job = u.AppendUploader(**args); save = job.journal.save; hit = []
        def interrupted():
            save(); inputs = job.journal.data['inputs']
            if not hit and len(inputs) == 2 and inputs[1]['frames']:
                hit.append(True); raise Crash('piece checkpoint before EOF')
        monkeypatch.setattr(job.journal, 'save', interrupted)
        with pytest.raises(Crash): job.run()
        data = journal(tmp_path); old_tail = data['pages'][0]['pieces'][0]
        old_bytes = (tmp_path / 'state' / old_tail['name']).read_bytes()
        (tmp_path / 'state' / data['pages'][0]['pieces'][1]['name']).unlink()
        real_reader = u.PCMReader; decoded = []
        def checked(original, *rest):
            decoded.append(original.path); assert original.path == second
            return real_reader(original, *rest)
        monkeypatch.setattr(u, 'PCMReader', checked)
        assert u.AppendUploader(**args).run()['input_complete'] and decoded == [second]
        assert (tmp_path / 'state' / old_tail['name']).read_bytes() == old_bytes
        assert u.AppendUploader(**finalize_arguments(tmp_path, sid, transport=routes)).run()['capture_finalized']


@pytest.mark.parametrize('option', ['--append', '--finalize', '--final'])
def test_cli_modes_dispatch_without_cross_policy_adoption(monkeypatch, tmp_path, option):
    called = []
    class Job:
        def __init__(self, **kw): called.append(kw)
        def run(self): return {'fixture': True}
    monkeypatch.setattr(u, 'Uploader', Job); monkeypatch.setattr(u, 'AppendUploader', Job)
    args = ['--session-id', 's', '--capture-id', 'c', '--capture-origin-frame', '100003',
            '--state-dir', str(tmp_path / 'state'), option]
    if option != '--finalize': args += ['--input', str(tmp_path / 'a.wav')]
    if option == '--append': args += ['--input-id', 'a']
    assert u.main(args) == 0
    assert (called[0].get('final') is True) == (option == '--final')
    if option == '--final': assert 'append' not in called[0] and 'input_id' not in called[0]


@pytest.mark.parametrize('intrusion', ['eos', 'extra_page'])
def test_unexpected_capture_history_refused_before_new_input_decode(tmp_path, app, monkeypatch, intrusion):
    first, second = tmp_path / 'a.wav', tmp_path / 'b.wav'; write_wav(first, u.PAGE_FRAMES); write_wav(second, Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('unexpected capture history')['id']; routes = Routes(client, app)
        u.AppendUploader(**append_arguments(tmp_path, sid, first, 'a', transport=routes)).run()
        base = f'/sessions/{sid}/recordings/uploader.capture'
        if intrusion == 'eos':
            status = routes.request('GET', base)
            routes.request('POST', base + '/finish', body={'idempotency_key': 'external.finish', 'expected_revision': status['revision']})
        else: routes.request('POST', base + '/pages', body={'page_number': 2})
        before = len(routes.calls); before_counts = counts(app)
        def forbidden(*args): raise AssertionError('Conflicting capture must not decode the next original')
        monkeypatch.setattr(u, 'PCMReader', forbidden)
        for path, input_id in ((first, 'a'), (second, 'b')):
            with pytest.raises(u.Incomplete, match='Capture'):
                u.AppendUploader(**append_arguments(tmp_path, sid, path, input_id, transport=routes)).run()
        assert all(method == 'GET' for method, _, _ in routes.calls[before:])
        assert counts(app) == before_counts and len(journal(tmp_path)['inputs']) == 1


def test_pending_piece_missing_from_ledger_is_not_adopted_or_deleted(tmp_path, app):
    path = tmp_path / 'a.wav'; write_wav(path, Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('missing ownership ledger')['id']; routes = Routes(client, app)
        args = append_arguments(tmp_path, sid, path, 'a', transport=routes)
        u.AppendUploader(**args).run(); before = len(routes.calls); data = journal(tmp_path)
        piece = tmp_path / 'state' / data['pages'][0]['pieces'][0]['name']; old = piece.read_bytes()
        data['spool'] = []; (tmp_path / 'state/uploader.json').write_text(json.dumps(data))
        with pytest.raises(u.Incomplete, match='ownership ledger'): u.AppendUploader(**args).run()
        assert len(routes.calls) == before and piece.read_bytes() == old


@pytest.mark.parametrize('bound', ['registry', 'original_bytes'])
def test_append_admission_limits_precede_registration_and_mutation(tmp_path, app, bound):
    first, second = tmp_path / 'a.wav', tmp_path / 'b.wav'; write_wav(first, Q); write_wav(second, Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('append admission bounds')['id']; routes = Routes(client, app)
        limits = u.Limits(disk_floor=0, poll_seconds=.001, input_bytes=first.stat().st_size + second.stat().st_size - 1)
        args = {**append_arguments(tmp_path, sid, first, 'a', transport=routes), 'limits': limits}
        u.AppendUploader(**args, max_inputs=1 if bound == 'registry' else 32).run(); before = len(routes.calls)
        with pytest.raises(u.Incomplete, match='registry capacity|original byte'):
            u.AppendUploader(**{**args, 'input': second, 'input_id': 'b'}).run()
        assert len(routes.calls) == before and len(journal(tmp_path)['inputs']) == 1


def test_v1_journal_cannot_be_adopted_by_append_mode(tmp_path, app):
    path = tmp_path / 'a.wav'; write_wav(path, Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('legacy journal remains legacy')['id']; routes = Routes(client, app)
        u.Uploader(**arguments(tmp_path, sid, path=path, transport=routes)).run()
        before = len(routes.calls); before_counts = counts(app)
        with pytest.raises(u.Incomplete, match='policy'):
            u.AppendUploader(**append_arguments(tmp_path, sid, path, 'a', transport=routes)).run()
        assert len(routes.calls) == before and counts(app) == before_counts
        assert u.Uploader(**arguments(tmp_path, sid, path=path, transport=routes)).run()['complete']
