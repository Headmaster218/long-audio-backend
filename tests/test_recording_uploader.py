"""Bounded functional ingestion fixtures. Fake analysis does not qualify ASR."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import socket
import struct
import subprocess
import threading
import time
import wave

import pytest
from fastapi.testclient import TestClient

from audio_backend import recording_uploader as u
from audio_backend import recording_uploader_history as history
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
        self.calls = []; self.put_sizes = []; self.seals = []; self.intents = []

    def request(self, method, path, body=None, file=None, key=None):
        headers = {'Authorization': 'Bearer fixture-secret'}
        if method != 'GET': self.intents.append((method, path, u._json(body) if body is not None else None, key))
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
    with pytest.raises(ValueError): u.Limits(output_frames=48*3600*RATE+1)
    with pytest.raises(ValueError): u.Uploader(**{**args, 'limits': u.Limits(output_frames=2*3600*RATE+1)})
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


def append_records(tmp_path, kind):
    """Inspect one authenticated v3 record at a time, never merge the history."""
    saved = journal(tmp_path)
    if saved['policy'] == u.APPEND_POLICY:
        yield from saved['inputs' if kind == 'input' else 'pages']; return
    caps = saved['caps']
    guard = u.Guard(u.Limits(disk_floor=0, output_frames=caps['output_frames'], input_bytes=caps['input_bytes']))
    saved = None
    current = history.PagedJournal(tmp_path / 'state', guard, u)
    current.lock.acquire()
    try:
        current.load()
        yield from (current.iter_inputs() if kind == 'input' else current.iter_pages())
    finally: current.lock.release()


def append_record(tmp_path, kind, number):
    rows = append_records(tmp_path, kind)
    try:
        for index, row in enumerate(rows, 1):
            if index == number: return row
        raise AssertionError(f'Missing {kind} record {number}')
    finally: rows.close()


def append_count(tmp_path, kind):
    saved = journal(tmp_path)
    return (len(saved['inputs' if kind == 'input' else 'pages']) if saved['policy'] == u.APPEND_POLICY
            else saved['input_count' if kind == 'input' else 'page_count'])


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
        saved = journal(tmp_path); assert saved['policy'] == history.POLICY
        tail = saved['active_page']
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
        seam = append_record(tmp_path, 'page', 13)['pieces']
        assert [p['input_id'] for p in seam] == ['hour-a', 'hour-b', 'hour-b']
        assert [p['frames'] for p in seam] == [72 * RATE, 120 * RATE, 102 * RATE]
        assert [p['input_start'] for p in seam] == [3528 * RATE, 0, 120 * RATE]
        assert append_record(tmp_path, 'input', 2)['normalized_start'] == frames
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


@pytest.mark.parametrize('point', ['eof', 'tail', 'create', 'put', 'seal', 'cleanup', 'intent', 'finalseal', 'finalrelease', 'finalarchive', 'finish'])
def test_append_crash_recovery_preserves_lineage_and_keys(tmp_path, app, monkeypatch, point):
    first, second = tmp_path / 'a.wav', tmp_path / 'b.wav'
    a_frames = Q + 7; total = u.PAGE_FRAMES if point == 'finish' else u.PAGE_FRAMES + Q + 3
    write_wav(first, a_frames); write_wav(second, total - a_frames, offset=a_frames)
    with TestClient(app) as client:
        sid = app.state.store.create_session('append crash ' + point)['id']; routes = Routes(client, app)
        u.AppendUploader(**append_arguments(tmp_path, sid, first, 'a', transport=routes)).run()
        retained = append_record(tmp_path, 'page', 1)['pieces'][0]
        retained_bytes = (tmp_path / 'state' / retained['name']).read_bytes(); hit = []
        if point in ('intent', 'finalseal', 'finalrelease', 'finalarchive', 'finish'):
            u.AppendUploader(**append_arguments(tmp_path, sid, second, 'b', transport=routes)).run()
            args = finalize_arguments(tmp_path, sid, transport=routes)
        else: args = append_arguments(tmp_path, sid, second, 'b', transport=routes)
        job = u.AppendUploader(**args); save = job.journal.save; cleanup = job.journal.cleanup
        def hooked_save():
            save(); d = job.journal.data
            if not hit and ((point == 'eof' and d['active_input'] and d['active_input']['id'] == 'b' and d['active_input']['clean_eof'])
                    or (point == 'tail' and d['active_input'] and d['active_input']['id'] == 'b' and d['active_input']['frames'] > 0)
                    or (point == 'intent' and d['finalize_requested'])
                    or (point == 'finalrelease' and d['active_page'] and d['active_page']['released']
                        and d['active_page'].get('seal', {}).get('eos'))
                    or (point == 'finalarchive' and d['finalize_requested'] and d['active_page'] is None
                        and d['pages_done'] == 2 and not d['complete'])):
                hit.append(point); raise Crash(point)
        def hooked_cleanup():
            if point == 'cleanup' and job.journal.data['published_frames'] and not hit:
                hit.append(point); raise Crash(point)
            cleanup()
        def hook(method, route, data):
            wanted = ((point == 'create' and method == 'POST' and route.endswith('/pages')) or
                      (point == 'put' and method == 'PUT') or
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
        frozen = {r['number']: r['seal'] for r in append_records(tmp_path, 'page') if r.get('seal')}
        resumed = u.AppendUploader(**args).run()
        assert resumed['capture_finalized'] if point in ('intent', 'finalseal', 'finalrelease', 'finalarchive', 'finish') else resumed['input_complete']
        done = u.AppendUploader(**finalize_arguments(tmp_path, sid, transport=routes)).run()
        assert done['capture_finalized'] and done['frames'] == total
        saved = journal(tmp_path)
        assert all(append_record(tmp_path, 'page', n)['seal'] == seal for n, seal in frozen.items())
        if point in ('finalrelease', 'finalarchive'):
            assert 'finish' not in saved and not any(route.endswith('/finish') for _, route, _ in routes.calls)
        before = counts(app)
        assert u.AppendUploader(**append_arguments(tmp_path, sid, first, 'a', transport=routes)).run()['input_complete']
        assert u.AppendUploader(**append_arguments(tmp_path, sid, second, 'b', transport=routes)).run()['input_complete']
        assert u.AppendUploader(**finalize_arguments(tmp_path, sid, transport=routes)).run()['capture_finalized']
        assert counts(app) == before and before[2] == (total + Q - 1) // Q
        assert first.exists() and second.exists() and not list((tmp_path / 'state').glob('*.wav*'))
        exact = {}
        for method, route, body, key in routes.intents:
            request = (body, key)
            page = u._parse(body)['page_number'] if route.endswith('/pages') else None
            assert exact.setdefault((method, route, page), request) == request


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
        tail = tmp_path / 'state' / append_record(tmp_path, 'page', 1)['pieces'][0]['name']
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
        saved = journal(tmp_path); assert saved['caps']['max_inputs'] == 64
        assert saved['input_count'] == 14 and len(saved['active_page']['pieces']) == 14
        assert saved['active_page']['pieces'][13]['name'].endswith('-s13.wav')
        before = len(routes.calls); path = tmp_path / 'refused.wav'; write_wav(path, 1)
        with pytest.raises(u.Incomplete, match='source slots'):
            u.AppendUploader(**append_arguments(tmp_path, sid, path, 'refused', transport=routes)).run()
        assert len(routes.calls) == before and append_count(tmp_path, 'input') == 14
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
        assert not saved['active_input']['clean_eof'] and not saved['complete'] and saved['emitted_frames'] <= Q * 2
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
            save(); active = job.journal.data['active_input']
            if not hit and active and active['id'] == 'b' and active['frames']:
                hit.append(True); raise Crash('piece checkpoint before EOF')
        monkeypatch.setattr(job.journal, 'save', interrupted)
        with pytest.raises(Crash): job.run()
        data = journal(tmp_path); old_tail = data['active_page']['pieces'][0]
        old_bytes = (tmp_path / 'state' / old_tail['name']).read_bytes()
        (tmp_path / 'state' / data['active_page']['pieces'][1]['name']).unlink()
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
        assert counts(app) == before_counts and append_count(tmp_path, 'input') == 1


def test_pending_piece_missing_from_ledger_is_not_adopted_or_deleted(tmp_path, app):
    path = tmp_path / 'a.wav'; write_wav(path, Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('missing ownership ledger')['id']; routes = Routes(client, app)
        args = append_arguments(tmp_path, sid, path, 'a', transport=routes)
        u.AppendUploader(**args).run(); before = len(routes.calls); data = journal(tmp_path)
        piece = tmp_path / 'state' / data['active_page']['pieces'][0]['name']; old = piece.read_bytes()
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
        assert len(routes.calls) == before and append_count(tmp_path, 'input') == 1


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


@pytest.mark.parametrize('total', [4115, 8229, 16458, 1536])
def test_worker_scan_streams_whole_configured_capture_and_sparse_global_cursors(total):
    """Synthetic rows only; global rowids are not capture-local ordinal limits."""
    frames = min(total * Q, 48 * 3600 * RATE)
    job = object.__new__(u.AppendUploader)
    job.sid = 'bounded-worker-scan'
    job.limits = u.Limits(output_frames=frames, disk_floor=0); job.guard = u.Guard(job.limits)
    calls = []; seen = 0
    def get(path):
        nonlocal seen
        query = parse_qs(urlsplit(path).query)
        after, limit = int(query['after'][0]), int(query['limit'][0])
        assert limit == 16
        assert after == (2**40 + seen * 37 if seen else 0)
        calls.append(after)
        count = min(limit, total - seen)
        rows = [{'cursor': 2**40 + (seen + n + 1) * 37,
                 'id': f'chunk-{seen + n}', 'status': 'completed'} for n in range(count)]
        seen += count
        return {'chunks': rows, 'next_cursor': rows[-1]['cursor'] if rows else after}
    job.get = get
    job._blocked_worker()
    assert seen == total and len(calls) == total // 16 + 1
    # Each scan restarts at zero so a formerly healthy row cannot escape a later check.
    seen = 0; job._blocked_worker()
    assert calls[len(calls) // 2] == 0


@pytest.mark.parametrize('status', ['failed', 'paused', 'cancelled'])
def test_worker_scan_finds_failure_beyond_old_1500_row_limit(status):
    job = object.__new__(u.AppendUploader); job.sid = 'late-worker-failure'
    job.limits = u.Limits(output_frames=48 * 3600 * RATE, disk_floor=0); job.guard = u.Guard(job.limits)
    count = 0
    def get(path):
        nonlocal count
        query = parse_qs(urlsplit(path).query); after = int(query['after'][0])
        assert after == count * 101
        size = min(int(query['limit'][0]), 16458 - count)
        rows = [{'cursor': (count + n + 1) * 101, 'id': f'chunk-{count + n}',
                 'status': status if count + n == 16457 else 'completed'} for n in range(size)]
        count += size
        return {'chunks': rows, 'next_cursor': rows[-1]['cursor'] if rows else after}
    job.get = get
    with pytest.raises(u.Incomplete, match='worker ' + status + ': chunk-16457'):
        job._blocked_worker()
    assert count == 16458


@pytest.mark.parametrize('kind', ['boolean_cursor', 'string_cursor', 'duplicate_cursor',
                                  'regressing_cursor', 'wrong_next', 'stalled_next',
                                  'oversized_batch', 'excess_total', 'nonlist', 'bad_row',
                                  'wrong_session', 'boolean_next', 'string_next'])
def test_worker_scan_rejects_malformed_cursor_and_membership_bounds(kind):
    job = object.__new__(u.AppendUploader); job.sid = 'bad-worker-cursor'
    job.limits = u.Limits(output_frames=16 * Q, disk_floor=0); job.guard = u.Guard(job.limits)
    calls = []
    def get(path):
        query = parse_qs(urlsplit(path).query); after = int(query['after'][0]); calls.append(after)
        if after:
            rows = [{'cursor': after + 1, 'id': 'extra', 'status': 'completed'}]
        else:
            rows = [{'cursor': n + 1, 'id': f'chunk-{n}', 'status': 'completed'} for n in range(16)]
        result = {'chunks': rows, 'next_cursor': rows[-1]['cursor']}
        if kind == 'boolean_cursor': rows[0]['cursor'] = True
        elif kind == 'string_cursor': rows[0]['cursor'] = '1'
        elif kind == 'duplicate_cursor': rows[1]['cursor'] = rows[0]['cursor']
        elif kind == 'regressing_cursor': rows[1]['cursor'] = 0
        elif kind == 'wrong_next': result['next_cursor'] += 1
        elif kind == 'stalled_next': result['next_cursor'] = after
        elif kind == 'oversized_batch': rows.append({'cursor': 17, 'id': 'extra', 'status': 'completed'})
        elif kind == 'nonlist': result['chunks'] = {'cursor': 1}
        elif kind == 'bad_row': rows[0] = None
        elif kind == 'wrong_session': rows[0]['session_id'] = 'another-session'
        elif kind == 'boolean_next': result['next_cursor'] = True
        elif kind == 'string_next': result['next_cursor'] = str(result['next_cursor'])
        return result
    job.get = get
    with pytest.raises(u.Incomplete): job._blocked_worker()
    assert len(calls) <= 2


@pytest.mark.parametrize('over', [False, True])
@pytest.mark.parametrize('declared', [False, True])
def test_http_unicode_response_limit_is_bytes_not_characters(monkeypatch, over, declared):
    # Max-width UTF-8 and ASCII padding make the wire response exactly 512 KiB.
    prefix, suffix = b'{"value":"', b'"}'
    available = u.JSON_BYTES - len(prefix) - len(suffix)
    glyph = '\U0001f9ed'.encode('utf-8')
    raw = prefix + glyph * (available // len(glyph)) + b'x' * (available % len(glyph)) + suffix
    assert len(raw) == u.JSON_BYTES
    if over: raw += b' '
    reads = []; connections = []
    class Response:
        status = 200
        def __init__(self): self.stream = io.BytesIO(raw)
        def getheader(self, name, default=None):
            return str(len(raw)) if name == 'Content-Length' and declared else default
        def read1(self, size): reads.append(size); return self.stream.read(size)
    class Connection:
        sock = None
        def __init__(self, *a, **kw): self.closed = False; connections.append(self)
        def putrequest(self, *a): pass
        def putheader(self, *a): pass
        def endheaders(self): pass
        def getresponse(self): return Response()
        def close(self): self.closed = True
    monkeypatch.setattr(u.http.client, 'HTTPConnection', Connection)
    transport = u.HTTPTransport('http://127.0.0.1:8765', u.Guard(u.Limits()))
    if over:
        with pytest.raises(u.Incomplete, match='HTTP response exceeds bound'): transport.request('GET', '/fixture')
    else:
        assert transport.request('GET', '/fixture')['value'].startswith('\U0001f9ed')
    assert connections[0].closed and (not reads or max(reads) <= u.READ_BYTES)


@pytest.mark.parametrize('status', [401, 409, 429, 503, 507])
def test_append_retry_keeps_capacity_failures_permanent_and_busy_transient(status):
    job = object.__new__(u.AppendUploader); job.limits = u.Limits(poll_seconds=.001)
    pauses = []
    class Guard:
        def pause(self, seconds): pauses.append(seconds)
    job.guard = Guard(); error = u.HTTPFailure(status, 'bounded test', .125)
    if status in (429, 503):
        job._retry(error); assert pauses == [.125]
    else:
        with pytest.raises(u.HTTPFailure) as raised: job._retry(error)
        assert raised.value is error and not pauses


@pytest.mark.parametrize('stop', ['cancel', 'deadline'])
def test_append_retry_respects_cancel_and_deadline_without_decode(stop):
    job = object.__new__(u.AppendUploader); job.limits = u.Limits(poll_seconds=.001)
    job.guard = u.Guard(job.limits, cancel=lambda: stop == 'cancel')
    if stop == 'deadline': job.guard.end = 0
    with pytest.raises(InterruptedError if stop == 'cancel' else u.Incomplete):
        job._retry(u.Uncertain('accepted response lost'))


def metadata_journal(tmp_path, frames, max_inputs=64):
    """Synthetic protocol storage only: no original PCM and no live receipts."""
    guard = u.Guard(u.Limits(disk_floor=0, output_frames=frames, deadline_seconds=180, poll_seconds=.001))
    current = history.PagedJournal(tmp_path / 'state', guard, u)
    current.lock.acquire()
    target = {'api': 'http://127.0.0.1:8765', 'session_id': 'metadata-only',
              'capture_id': 'synthetic.metadata', 'origin': 100003, 'format': u.FORMAT}
    current.initialize(target, max_inputs)
    return current


def metadata_input(current, number, frames, *, padding=False):
    d = current.data
    entry = {'id': f'input-{number}',
             'identity': {'path': f'/synthetic-not-a-real-original/{number}.wav',
                          'signature': [1, number, frames * 2 + 44, 0, 0],
                          'sha256': hashlib.sha256(str(number).encode()).hexdigest()},
             'decoder': {'kind': 'synthetic-metadata-only'},
             'normalized_start': d['emitted_frames'], 'frames': 0, 'clean_eof': False}
    if padding:
        entry['id'] = f'{number:03d}' + 'i' * 125
        entry['identity']['path'] = '/' + 'p' * 4080 + f'{number:03d}.wav'
        entry['decoder']['arguments'] = ['x' * 2900]
    d['active_input'] = entry; d['input_count'] += 1; d['input_bytes'] += entry['identity']['signature'][2]
    current.save()
    return entry


def metadata_piece(current, frames):
    d = current.data; row = d['active_page']; entry = d['active_input']
    if row is None:
        row = {'number': d['pages_done'] + 1, 'pieces': [], 'released': False}
        d['active_page'] = row; d['page_count'] += 1
    index = len(row['pieces']); number = row['number']; start = d['emitted_frames']
    name = f"{d['job']}-p{number}-s{index}.wav"
    piece = {'name': name, 'key': f"{d['job']}.p{number}.s{index}",
             'input_id': entry['id'], 'input_start': entry['frames'],
             'normalized_start': start, 'capture_start': d['identity']['origin'] + start,
             'frames': frames, 'bytes': frames * 2 + 44,
             'digest': 'a' * 64, 'pcm_sha256': 'b' * 64,
             'server_id': f'synthetic-source-{number}-{index}'}
    row['pieces'].append(piece); d['spool'].append(name)
    entry['frames'] += frames; d['emitted_frames'] += frames; d['source_count'] += 1
    current.save()
    return row


def metadata_release(current, eos=False):
    d = current.data; row = d['active_page']; number = row['number']
    row['create'] = {'page_number': number}
    if number == 1: row['create']['capture_origin_frame'] = d['identity']['origin']
    row['server_id'] = f'synthetic-page-{number}'
    row['seal'] = {'idempotency_key': d['job'] + '.seal.' + str(number),
                   'expected_revision': number * 5, 'end_frame': d['identity']['origin'] + d['emitted_frames'],
                   'eos': eos}
    row['released'] = True; d['published_frames'] = d['emitted_frames']
    current.save(); current.archive_page(); current.cleanup()


def test_metadata_only_48_hours_serializes_588_pages_without_history_lists(tmp_path):
    """48 one-hour identities and actual v3 files; deliberately no 48-hour audio."""
    import tracemalloc
    frames = 48 * 3600 * RATE; hour = 3600 * RATE
    current = metadata_journal(tmp_path, frames)
    tracemalloc.start(); baseline = tracemalloc.get_traced_memory()[0]
    checkpoint_high = 0; chunks = 0; observed = {}
    try:
        for number in range(1, 49):
            entry = metadata_input(current, number, hour)
            while entry['frames'] < hour:
                row = current.data['active_page']
                used = sum(p['frames'] for p in row['pieces']) if row else 0
                count = min(u.SOURCE_FRAMES, u.PAGE_FRAMES - used, hour - entry['frames'])
                row = metadata_piece(current, count)
                checkpoint_high = max(checkpoint_high, (current.path / 'uploader.json').stat().st_size)
                if row['number'] in (99, 100, 587, 588):
                    observed[row['number']] = row['pieces'][-1]['name']
                if entry['frames'] == hour:
                    entry['clean_eof'] = True; current.save(); current.archive_input()
                if used + count == u.PAGE_FRAMES:
                    metadata_release(current); chunks += 28
        assert current.data['active_page']['number'] == 588
        assert sum(p['frames'] for p in current.data['active_page']['pieces']) == 222 * RATE
        current.data['finalize_requested'] = True; current.save()
        synthetic_publish_with_lost_replies(current)
        chunks += (222 * RATE + Q - 1) // Q
        current.data['complete'] = True; current.save()
        assert frames == 2_764_800_000 and frames > 2**31 and chunks == 16458
        assert frames % Q == 24000
        assert current.data['page_count'] == current.data['pages_done'] == 588
        assert current.data['input_count'] == current.data['inputs_done'] == 48
        assert current.data['active_input'] is None and current.data['active_page'] is None
        assert 'inputs' not in current.data and 'pages' not in current.data
        assert len(current.digest_bindings['page']) == 588 * 32
        assert len(current.digest_bindings['input']) == 48 * 32
        assert all(isinstance(value, bytearray) for value in current.digest_bindings.values())
        assert checkpoint_high <= u.JSON_BYTES
        assert current.metadata_allocated <= history.METADATA_BYTES
        assert tracemalloc.get_traced_memory()[0] - baseline < 4 * 1024**2
        for number in (99, 100, 587, 588): assert f'-p{number}-s' in observed[number]
        assert not list(current.path.glob('*.wav*'))
        with pytest.raises(u.Incomplete, match='Unowned'):
            current.owned(current.data['job'] + '-p589-s0.wav')
    finally:
        tracemalloc.stop(); current.lock.release()
    reopened = history.PagedJournal(tmp_path / 'state', u.Guard(u.Limits(disk_floor=0, output_frames=frames)), u)
    reopened.lock.acquire()
    try:
        reopened.load(); reopened.recover()
        assert sum(1 for _ in reopened.iter_inputs()) == 48
        total = pages = sources = 0
        for row in reopened.iter_pages():
            pages += 1; sources += len(row['pieces']); total += sum(p['frames'] for p in row['pieces'])
            assert row['number'] == pages and row['released']
            assert row['seal']['eos'] == (pages == 588)
            assert reopened.archive_path('page', pages).stat().st_size <= history.PAGE_ARCHIVE_BYTES
        assert (pages, total, sources) == (588, frames, reopened.data['source_count'])
        assert len(u._json(reopened.data)) <= u.JSON_BYTES and not reopened.data['spool']
        print(json.dumps({'synthetic_metadata_only': True, 'frames': total, 'pages': pages,
                          'work_chunk_geometry': chunks, 'inputs': 48, 'sources': sources,
                          'checkpoint_max_bytes': checkpoint_high,
                          'metadata_allocated_bytes': reopened.metadata_allocated,
                          'authentication_bytes': sum(map(len, reopened.digest_bindings.values()))}))
    finally: reopened.lock.release()


@pytest.mark.parametrize('capacity', [64, 128])
def test_paged_registry_maximum_shapes_remain_separate_bounded_records(tmp_path, capacity):
    # Eight independently identified originals per full synthetic page.
    per_input = u.PAGE_FRAMES // 8
    current = metadata_journal(tmp_path, capacity * per_input + 1, capacity)
    try:
        for number in range(1, capacity + 1):
            entry = metadata_input(current, number, per_input, padding=True)
            metadata_piece(current, per_input)
            entry['clean_eof'] = True; current.save(); current.archive_input()
            assert current.archive_path('input', number).stat().st_size <= history.INPUT_ARCHIVE_BYTES
            if number % 8 == 0: metadata_release(current)
        assert current.data['inputs_done'] == capacity
        assert len(current.digest_bindings['input']) == capacity * 32
        assert (current.path / 'uploader.json').stat().st_size < 16384
        before = (current.path / 'uploader.json').read_bytes()
        with pytest.raises(u.Incomplete, match='input_count bound|registry'):
            metadata_input(current, capacity + 1, 1)
        assert (current.path / 'uploader.json').read_bytes() == before
        assert not current.archive_path('input', capacity + 1).exists()
    finally: current.lock.release()
    reopened = history.PagedJournal(tmp_path / 'state', u.Guard(u.Limits(disk_floor=0)), u)
    reopened.lock.acquire()
    try:
        reopened.load()
        assert sum(1 for _ in reopened.iter_inputs()) == capacity
        assert sum(1 for _ in reopened.iter_pages()) == capacity // 8
    finally: reopened.lock.release()


@pytest.mark.parametrize('kind', ['input', 'page'])
@pytest.mark.parametrize('point', ['intent_checkpoint', 'archive_fsync', 'archive_promote',
                                  'archive_dir_fsync', 'advance_checkpoint', 'checkpoint_fsync',
                                  'checkpoint_promote', 'checkpoint_dir_fsync'])
def test_bound_archive_transition_recovers_each_durable_crash_window(tmp_path, monkeypatch, kind, point):
    current = metadata_journal(tmp_path, u.PAGE_FRAMES)
    entry = metadata_input(current, 1, u.PAGE_FRAMES)
    for count in (u.SOURCE_FRAMES, u.SOURCE_FRAMES, u.PAGE_FRAMES - 2 * u.SOURCE_FRAMES):
        metadata_piece(current, count)
    entry['clean_eof'] = True; current.save()
    if kind == 'page':
        current.archive_input()
        row = current.data['active_page']; row['create'] = {'page_number': 1, 'capture_origin_frame': 100003}
        row['server_id'] = 'synthetic-page-1'
        row['seal'] = {'idempotency_key': current.data['job'] + '.seal.1', 'expected_revision': 4,
                       'end_frame': 100003 + u.PAGE_FRAMES, 'eos': False}
        row['released'] = True; current.data['published_frames'] = u.PAGE_FRAMES; current.save()
    expected = u._json(current.data['active_input' if kind == 'input' else 'active_page'])
    hit = []; events = []; archive = current.archive_path(kind, 1)
    real_fsync, real_promote = history.os.fsync, current._promote_archive
    real_dir, real_checkpoint, real_replace = u._fsync_dir, current._atomic_checkpoint, history.os.replace
    def crash(label):
        if label == point and not hit: hit.append(label); raise Crash(label)
    def fsync(fd):
        name = os.readlink('/proc/self/fd/' + str(fd)); result = real_fsync(fd)
        if name == str(current.history / 'archive.next'):
            events.append('archive_fsync'); crash('archive_fsync')
        if name == str(current.path / 'uploader.json.next'): crash('checkpoint_fsync')
        return result
    def promote(source, target):
        result = real_promote(source, target)
        if Path(target) == archive:
            assert 'archive_fsync' in events
            events.append('archive_promote'); crash('archive_promote')
        return result
    def replace(source, target, *args, **kwargs):
        result = real_replace(source, target, *args, **kwargs)
        if Path(target) == current.path / 'uploader.json': crash('checkpoint_promote')
        return result
    def fsync_dir(path):
        result = real_dir(path)
        if Path(path) == current.path: crash('checkpoint_dir_fsync')
        if Path(path) == current.history and archive.exists():
            assert 'archive_promote' in events
            events.append('archive_dir_fsync'); crash('archive_dir_fsync')
        return result
    def checkpoint(raw):
        state = u._parse(raw); result = real_checkpoint(raw)
        intent = state.get('archive_intent')
        if intent and intent['kind'] == kind:
            events.append('intent_checkpoint'); crash('intent_checkpoint')
        elif state['inputs_done' if kind == 'input' else 'pages_done'] == 1:
            assert 'archive_dir_fsync' in events
            events.append('advance_checkpoint'); crash('advance_checkpoint')
        return result
    monkeypatch.setattr(history.os, 'fsync', fsync); monkeypatch.setattr(current, '_promote_archive', promote)
    monkeypatch.setattr(history.os, 'replace', replace)
    monkeypatch.setattr(u, '_fsync_dir', fsync_dir); monkeypatch.setattr(current, '_atomic_checkpoint', checkpoint)
    try:
        with pytest.raises(Crash): (current.archive_input if kind == 'input' else current.archive_page)()
        assert hit == [point]
    finally:
        monkeypatch.undo(); current.lock.release()
    reopened = history.PagedJournal(tmp_path / 'state', u.Guard(u.Limits(disk_floor=0, output_frames=u.PAGE_FRAMES)), u)
    reopened.lock.acquire()
    try:
        reopened.load(); reopened.recover()
        assert u._json(reopened.read_archive(kind, 1)) == expected
        assert reopened.data['inputs_done' if kind == 'input' else 'pages_done'] == 1
        assert not reopened.data['archive_intent'] and not reopened.data['retirement']
        assert not (reopened.history / 'archive.next').exists()
        reopened.recover()
        assert u._json(reopened.read_archive(kind, 1)) == expected
    finally: reopened.lock.release()


@pytest.mark.parametrize('point', ['unlink', 'spool_dir_fsync', 'retirement_clear'])
def test_retirement_deletes_only_bound_owned_names_after_archive_commit(tmp_path, monkeypatch, point):
    current = metadata_journal(tmp_path, u.PAGE_FRAMES)
    entry = metadata_input(current, 1, u.PAGE_FRAMES)
    for count in (u.SOURCE_FRAMES, u.SOURCE_FRAMES, u.PAGE_FRAMES - 2 * u.SOURCE_FRAMES): metadata_piece(current, count)
    entry['clean_eof'] = True; current.save(); current.archive_input()
    row = current.data['active_page']; row['create'] = {'page_number': 1, 'capture_origin_frame': 100003}
    row['server_id'] = 'synthetic-page-1'
    row['seal'] = {'idempotency_key': current.data['job'] + '.seal.1', 'expected_revision': 4,
                   'end_frame': 100003 + u.PAGE_FRAMES, 'eos': False}
    row['released'] = True; current.data['published_frames'] = u.PAGE_FRAMES; current.save()
    names = tuple(current.data['spool'])
    for name in names: current.owned(name).write_bytes(b'synthetic-owned-spool')
    unrelated = current.path / 'unrelated.wav'; unrelated.write_bytes(b'keep exactly')
    current.archive_page()
    assert current.data['pages_done'] == 1 and current.data['retirement']['names'] == list(names)
    assert all(current.owned(name).exists() for name in names)
    hit = []; real_unlink, real_dir, real_checkpoint = Path.unlink, u._fsync_dir, current._atomic_checkpoint
    def crash(label):
        if point == label and not hit: hit.append(label); raise Crash(label)
    def unlink(path, *args, **kwargs):
        if path.name in names:
            saved = journal(tmp_path)
            assert saved['pages_done'] == 1 and saved['retirement']['digest'] == saved['pages_head']
        result = real_unlink(path, *args, **kwargs)
        if path.name in names: crash('unlink')
        return result
    def fsync_dir(path):
        result = real_dir(path)
        if Path(path) == current.path: crash('spool_dir_fsync')
        return result
    def checkpoint(raw):
        state = u._parse(raw); result = real_checkpoint(raw)
        if state['retirement'] is None: crash('retirement_clear')
        return result
    monkeypatch.setattr(Path, 'unlink', unlink); monkeypatch.setattr(u, '_fsync_dir', fsync_dir)
    monkeypatch.setattr(current, '_atomic_checkpoint', checkpoint)
    try:
        with pytest.raises(Crash): current.cleanup()
        assert hit == [point]
    finally: monkeypatch.undo(); current.lock.release()
    reopened = history.PagedJournal(tmp_path / 'state', u.Guard(u.Limits(disk_floor=0, output_frames=u.PAGE_FRAMES)), u)
    reopened.lock.acquire()
    try:
        reopened.load(); reopened.recover()
        assert not reopened.data['retirement'] and not reopened.data['spool']
        assert all(not (reopened.path / name).exists() for name in names)
        assert unrelated.read_bytes() == b'keep exactly'
        assert reopened.read_archive('page', 1)['seal']['idempotency_key'].endswith('.seal.1')
    finally: reopened.lock.release()


@pytest.mark.parametrize('kind', ['input', 'page'])
@pytest.mark.parametrize('change', ['bytes', 'missing', 'symlink', 'hardlink', 'unbound', 'temporary'])
def test_history_corruption_alias_and_unbound_files_refuse_without_mutation(tmp_path, kind, change):
    current = metadata_journal(tmp_path, u.PAGE_FRAMES)
    entry = metadata_input(current, 1, u.PAGE_FRAMES)
    for count in (u.SOURCE_FRAMES, u.SOURCE_FRAMES, u.PAGE_FRAMES - 2 * u.SOURCE_FRAMES): metadata_piece(current, count)
    entry['clean_eof'] = True; current.save(); current.archive_input(); metadata_release(current)
    archive = current.archive_path(kind, 1); original = archive.read_bytes()
    unrelated = tmp_path / 'unrelated-original'; unrelated.write_bytes(original)
    current.lock.release()
    if change == 'bytes':
        value = json.loads(original); value['payload']['unexpected'] = 'changed'; archive.write_bytes(u._json(value))
    elif change == 'missing': archive.unlink()
    elif change == 'symlink': archive.unlink(); archive.symlink_to(unrelated)
    elif change == 'hardlink': archive.unlink(); os.link(unrelated, archive)
    elif change == 'unbound': current.archive_path(kind, 2).write_bytes(original)
    else: (current.history / 'archive.next').write_bytes(original)
    checkpoint = (current.path / 'uploader.json').read_bytes()
    reopened = history.PagedJournal(current.path, u.Guard(u.Limits(disk_floor=0, output_frames=u.PAGE_FRAMES)), u)
    reopened.lock.acquire()
    try:
        with pytest.raises((u.Incomplete, OSError)): reopened.load()
        assert (current.path / 'uploader.json').read_bytes() == checkpoint
        assert unrelated.read_bytes() == original
        if change == 'unbound': assert current.archive_path(kind, 2).read_bytes() == original
        if change == 'temporary': assert (current.history / 'archive.next').read_bytes() == original
    finally: reopened.lock.release()


@pytest.mark.parametrize('kind', ['input', 'page'])
def test_archive_is_reauthenticated_after_initial_full_validation(tmp_path, kind):
    current = metadata_journal(tmp_path, u.PAGE_FRAMES)
    entry = metadata_input(current, 1, u.PAGE_FRAMES)
    for count in (u.SOURCE_FRAMES, u.SOURCE_FRAMES, u.PAGE_FRAMES - 2 * u.SOURCE_FRAMES): metadata_piece(current, count)
    entry['clean_eof'] = True; current.save(); current.archive_input(); metadata_release(current)
    current.lock.release()
    reopened = history.PagedJournal(current.path, u.Guard(u.Limits(disk_floor=0, output_frames=u.PAGE_FRAMES)), u)
    reopened.lock.acquire()
    try:
        reopened.load(); path = reopened.archive_path(kind, 1)
        value = u._parse(path.read_bytes())
        value['payload']['id' if kind == 'input' else 'server_id'] = 'changed-after-validation'
        path.write_bytes(u._json(value))
        checkpoint = (reopened.path / 'uploader.json').read_bytes()
        with pytest.raises(u.Incomplete, match='changed after validation'): reopened.read_archive(kind, 1)
        with pytest.raises(u.Incomplete, match='changed after validation'):
            next(reopened.iter_inputs() if kind == 'input' else reopened.iter_pages())
        assert (reopened.path / 'uploader.json').read_bytes() == checkpoint
    finally: reopened.lock.release()


def test_v2_journal_resumes_original_policy_and_caps_without_migration(tmp_path, app):
    first, second = tmp_path / 'z-2026.wav', tmp_path / 'a-2020.wav'
    write_wav(first, Q); write_wav(second, Q, offset=Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('saved v2 compatibility')['id']; routes = Routes(client, app)
        args = append_arguments(tmp_path, sid, first, 'first-called', transport=routes)
        assert u.LegacyAppendUploader(**args).run()['input_complete']
        assert journal(tmp_path)['policy'] == u.APPEND_POLICY
        assert not (tmp_path / 'state/uploader.history').exists()
        resumed = u.AppendUploader(**args)
        assert type(resumed) is u.LegacyAppendUploader and resumed.run()['input_complete']
        assert resumed.limits.output_frames == 2 * 3600 * RATE
        assert u.AppendUploader(**append_arguments(tmp_path, sid, second, 'second-called', transport=routes)).run()['input_complete']
        assert [entry['id'] for entry in journal(tmp_path)['inputs']] == ['first-called', 'second-called']
        before = (tmp_path / 'state/uploader.json').read_bytes(); calls = len(routes.calls)
        for changed in ({'max_inputs': 33}, {'max_hours': 12}, {'max_original_bytes': 9 * 1024**3}):
            with pytest.raises((ValueError, u.Incomplete)): u.AppendUploader(**{**args, **changed}).run()
            assert (tmp_path / 'state/uploader.json').read_bytes() == before and len(routes.calls) == calls
        assert u.AppendUploader(**finalize_arguments(tmp_path, sid, transport=routes)).run()['capture_finalized']
        assert journal(tmp_path)['policy'] == u.APPEND_POLICY
        assert not (tmp_path / 'state/uploader.history').exists()
        assert counts(app)[:3] == (1, 2, 2)


def test_v3_frozen_caps_default_omission_and_conflict_precede_requests(tmp_path, app):
    path = tmp_path / 'original.wav'; write_wav(path, Q)
    with TestClient(app) as client:
        sid = app.state.store.create_session('frozen v3 caps')['id']; routes = Routes(client, app)
        args = append_arguments(tmp_path, sid, path, 'a', transport=routes)
        job = u.AppendUploader(**args)
        assert job.limits.output_frames == 12 * 3600 * RATE
        configured = u.AppendUploader(**args, max_hours=24, max_inputs=128, max_original_bytes=9 * 1024**3)
        assert configured.run()['input_complete']
        assert journal(tmp_path)['caps'] == {'output_frames': 24 * 3600 * RATE,
                                            'max_inputs': 128, 'input_bytes': 9 * 1024**3}
        omitted = u.AppendUploader(**args)
        assert omitted.run()['input_complete'] and omitted.limits.output_frames == 24 * 3600 * RATE
        assert omitted.limits.input_bytes == 9 * 1024**3
        before = (tmp_path / 'state/uploader.json').read_bytes(); calls = len(routes.calls)
        for changed in ({'max_hours': 12}, {'max_inputs': 64}, {'max_original_bytes': 8 * 1024**3}):
            with pytest.raises(u.Incomplete, match='differs from frozen'):
                u.AppendUploader(**{**args, **changed}).run()
            assert (tmp_path / 'state/uploader.json').read_bytes() == before and len(routes.calls) == calls
        legacy = u.AppendJournal(tmp_path / 'state', u.Guard(u.Limits(disk_floor=0)))
        with pytest.raises(u.Incomplete, match='policy'): legacy.load()
        assert (tmp_path / 'state/uploader.json').read_bytes() == before


@pytest.mark.parametrize('frames', [Q, u.PAGE_FRAMES])
@pytest.mark.parametrize('extra', [0, 1])
def test_exact_append_frame_cap_probes_eof_without_an_extra_page(tmp_path, app, frames, extra):
    path = tmp_path / 'original.wav'; write_wav(path, frames + extra)
    limits = u.Limits(disk_floor=0, output_frames=frames, poll_seconds=.001, deadline_seconds=60)
    with TestClient(app) as client:
        sid = app.state.store.create_session('exact append cap')['id']; routes = Routes(client, app)
        args = {**append_arguments(tmp_path, sid, path, 'a', transport=routes), 'limits': limits}
        if extra:
            with pytest.raises(u.Incomplete, match='frame limit'): u.AppendUploader(**args).run()
        else:
            assert u.AppendUploader(**args).run()['input_complete']
        saved = journal(tmp_path)
        assert saved['emitted_frames'] == frames and saved['page_count'] == 1
        assert not saved['complete'] and not saved['finalize_requested']
        assert not any('/pages/2' in route for _, route, _ in routes.calls)
        assert not any('-p2-' in path.name for path in (tmp_path / 'state').iterdir())
        if extra:
            assert saved['active_input'] and not saved['active_input']['clean_eof'] and saved['inputs_done'] == 0
            with pytest.raises(u.Incomplete, match='clean EOF'):
                u.AppendUploader(**{**finalize_arguments(tmp_path, sid, transport=routes), 'limits': limits}).run()
        else:
            assert saved['active_input'] is None and saved['inputs_done'] == 1
            assert u.AppendUploader(**{**finalize_arguments(tmp_path, sid, transport=routes), 'limits': limits}).run()['capture_finalized']
            assert counts(app)[2] == (frames + Q - 1) // Q


@pytest.mark.parametrize('option,value', [('max_hours', 0), ('max_hours', 49), ('max_hours', True),
                                         ('max_inputs', 129), ('max_inputs', True),
                                         ('max_original_bytes', 64 * 1024**3 + 1),
                                         ('input_start_frame', 2**53)])
def test_v3_capacity_options_reject_invalid_values_before_state_creation(tmp_path, option, value):
    path = tmp_path / 'original.wav'; write_wav(path, 1)
    args = append_arguments(tmp_path, 'unused', path, 'a')
    with pytest.raises(ValueError): u.AppendUploader(**{**args, option: value})
    assert not (tmp_path / 'state/uploader.json').exists()


def test_v3_origin_reserves_entire_frozen_duration_exactly(tmp_path, app):
    path = tmp_path / 'original.wav'; write_wav(path, 1)
    with TestClient(app) as client:
        sid = app.state.store.create_session('exact coordinate capacity')['id']; routes = Routes(client, app)
        args = append_arguments(tmp_path, sid, path, 'a', transport=routes)
        maximum = (1 << 53) - 1 - 48 * 3600 * RATE
        with pytest.raises(ValueError, match='exact integers'):
            u.AppendUploader(**{**args, 'capture_origin_frame': maximum + 1, 'max_hours': 48}).run()
        assert not routes.calls and not (tmp_path / 'state/uploader.json').exists()
        assert u.AppendUploader(**{**args, 'capture_origin_frame': maximum, 'max_hours': 48}).run()['input_complete']
        assert append_record(tmp_path, 'page', 1)['pieces'][0]['capture_start'] == maximum


@pytest.mark.parametrize('option,value', [('--max-hours', '49'), ('--max-inputs', '129'),
                                         ('--max-original-bytes', str(64 * 1024**3 + 1)),
                                         ('--deadline-seconds', 'inf')])
def test_invalid_v3_cli_options_have_normal_errors_not_tracebacks(tmp_path, capsys, option, value):
    path = tmp_path / 'original.wav'; write_wav(path, 1)
    args = ['--session-id', 's', '--capture-id', 'c', '--capture-origin-frame', '0',
            '--state-dir', str(tmp_path / 'state'), '--append', '--input', str(path), '--input-id', 'a', option, value]
    assert u.main(args) == 2
    error = capsys.readouterr().err
    assert 'Incomplete ingestion:' in error and 'Traceback' not in error
    assert not (tmp_path / 'state/uploader.json').exists()


@pytest.mark.parametrize('change', ['page_id', 'source_id', 'missing_source', 'missing_page'])
def test_v3_archived_remote_authority_conflicts_precede_new_decode(tmp_path, app, monkeypatch, change):
    first, second = tmp_path / 'first.wav', tmp_path / 'second.wav'
    write_wav(first, u.PAGE_FRAMES); write_wav(second, 1)
    with TestClient(app) as client:
        sid = app.state.store.create_session('archived remote identity')['id']; routes = Routes(client, app)
        u.AppendUploader(**append_arguments(tmp_path, sid, first, 'first', transport=routes)).run()
        before = counts(app); calls = len(routes.calls); real = routes.request
        def request(method, route, **kwargs):
            if method == 'GET' and route.endswith('/pages/1') and change == 'missing_page':
                raise u.HTTPFailure(404, 'synthetic absent accepted page')
            result = real(method, route, **kwargs)
            if method == 'GET' and route.endswith('/pages/1'):
                if change == 'page_id': result['id'] = 'changed-server-page'
                elif change == 'source_id': result['sources'][0]['id'] = 'changed-server-source'
                elif change == 'missing_source': result['sources'].pop()
            return result
        routes.request = request
        def forbidden(*args): raise AssertionError('Conflicting archived authority must not decode')
        monkeypatch.setattr(u, 'PCMReader', forbidden)
        with pytest.raises(u.Incomplete):
            u.AppendUploader(**append_arguments(tmp_path, sid, second, 'second', transport=routes)).run()
        assert counts(app) == before and append_count(tmp_path, 'input') == 1
        assert all(method == 'GET' for method, _, _ in routes.calls[calls:])


def test_archive_tamper_between_reconciliation_and_replay_is_not_used(tmp_path, app, monkeypatch):
    first, second = tmp_path / 'first.wav', tmp_path / 'second.wav'
    write_wav(first, u.PAGE_FRAMES); write_wav(second, 1)
    with TestClient(app) as client:
        sid = app.state.store.create_session('late metadata tamper')['id']; routes = Routes(client, app)
        u.AppendUploader(**append_arguments(tmp_path, sid, first, 'first', transport=routes)).run()
        job = u.AppendUploader(**append_arguments(tmp_path, sid, second, 'second', transport=routes))
        real = job._reconcile_capture; calls = []
        def reconciled_then_changed():
            real(); calls.append(len(routes.calls))
            path = job.journal.archive_path('page', 1); record = u._parse(path.read_bytes())
            record['payload']['seal']['idempotency_key'] += '.tampered'; path.write_bytes(u._json(record))
        monkeypatch.setattr(job, '_reconcile_capture', reconciled_then_changed)
        monkeypatch.setattr(u, 'PCMReader', lambda *a: pytest.fail('Tampered archive must not permit new decode'))
        before = (tmp_path / 'state/uploader.json').read_bytes()
        with pytest.raises(u.Incomplete, match='changed after validation'): job.run()
        assert calls and len(routes.calls) == calls[0]
        assert (tmp_path / 'state/uploader.json').read_bytes() == before


def synthetic_publish_with_lost_replies(current):
    """One page of metadata responses, never HTTP/PCM or a history response map."""
    job = object.__new__(u.AppendUploader); job.journal = current
    job.limits, job.guard = current.guard.limits, current.guard
    target = current.data['identity']; job.sid = target['session_id']; job.capture = target['capture_id']
    job.origin = target['origin']; job.base = f'/sessions/{job.sid}/recordings/{job.capture}'
    row = current.data['active_page']; assert row['number'] == 588
    for piece in row['pieces']: piece.pop('server_id')
    current.save(); lost = set(); seal_bodies = []; accepted_keys = set(); creates = []
    class Responses:
        remote = None
        def request(self, method, path, body=None, file=None, key=None):
            if method == 'GET':
                if self.remote is None: raise u.HTTPFailure(404, 'synthetic uncreated page')
                return u._parse(u._json(self.remote))
            checkpoint = u._parse((current.path / 'uploader.json').read_bytes())
            if method == 'POST' and path.endswith('/pages'):
                assert checkpoint['active_page']['create'] == body == {'page_number': 588}
                creates.append(u._json(body))
                self.remote = {'page_number': 588, 'capture_id': job.capture,
                    'capture_origin_frame': job.origin, 'policy': 'sealed-recording-exact-overlap-v1',
                    'id': 'synthetic-page-588', 'sources': [], 'state': 'open',
                    'revision': 588, 'released': False, 'eos': False, 'end_frame': None}
                tag = 'create'
            elif method == 'PUT':
                piece = next(piece for piece in checkpoint['active_page']['pieces'] if piece['key'] == key)
                assert file == current.owned(piece['name']) and not file.exists()
                assert path.endswith('/sources?source_start_frame=' + str(piece['capture_start']))
                assert key not in accepted_keys; accepted_keys.add(key)
                self.remote['sources'].append({'id': 'synthetic-accepted-' + str(len(accepted_keys)),
                    'idempotency_key': key, 'digest': piece['digest'], 'frames': piece['frames'],
                    'bytes': piece['bytes'], 'source_start_frame': piece['capture_start'],
                    'format': 'wav-pcm16le-16000-mono'})
                self.remote['revision'] += 1; tag = 'put'
            else:
                assert method == 'POST' and path.endswith('/pages/588/seal')
                assert checkpoint['active_page']['seal'] == body
                seal_bodies.append(u._json(body)); tag = 'seal'
                assert len(self.remote['sources']) == len(row['pieces'])
                self.remote.update(state='published', released=True, eos=body['eos'], end_frame=body['end_frame'])
                self.remote['revision'] += 1
            if tag not in lost:
                lost.add(tag); raise u.Uncertain('Synthetic accepted ' + tag + ' reply lost at page588')
            return u._parse(u._json(self.remote))
    job.http = Responses(); job._publish(row, True)
    assert lost == {'create', 'put', 'seal'} and len(creates) == 1
    assert len(accepted_keys) == len(row['pieces']) and len(seal_bodies) == 2
    assert seal_bodies[0] == seal_bodies[1]
    archived = current.read_archive('page', 588)
    assert archived['server_id'] == 'synthetic-page-588' and archived['seal']['eos']
    assert all(piece['server_id'].startswith('synthetic-accepted-') for piece in archived['pieces'])


@pytest.mark.parametrize('collision', ['file', 'symlink', 'hardlink', 'directory'])
def test_atomic_archive_promotion_never_replaces_unbound_collision(tmp_path, monkeypatch, collision):
    current = metadata_journal(tmp_path, Q)
    entry = metadata_input(current, 1, Q); metadata_piece(current, Q)
    entry['clean_eof'] = True; current.save()
    unrelated = tmp_path / 'unrelated-original'; unrelated.write_bytes(b'preserve collision bytes')
    archive = current.archive_path('input', 1); real = current._promote_archive
    def collision_before_promote(temporary, destination):
        assert destination == archive
        if collision == 'file': destination.write_bytes(b'preserve collision bytes')
        elif collision == 'symlink': destination.symlink_to(unrelated)
        elif collision == 'hardlink': os.link(unrelated, destination)
        else: destination.mkdir()
        return real(temporary, destination)
    monkeypatch.setattr(current, '_promote_archive', collision_before_promote)
    try:
        with pytest.raises(OSError): current.archive_input()
        assert unrelated.read_bytes() == b'preserve collision bytes'
        assert archive.is_dir() if collision == 'directory' else archive.read_bytes() == b'preserve collision bytes'
        assert current.data['inputs_done'] == 0 and current.data['archive_intent']
    finally: monkeypatch.undo(); current.lock.release()
    reopened = history.PagedJournal(current.path, u.Guard(u.Limits(disk_floor=0, output_frames=Q)), u)
    reopened.lock.acquire()
    try:
        with pytest.raises(u.Incomplete): reopened.load(); reopened.recover()
        assert unrelated.read_bytes() == b'preserve collision bytes'
        assert archive.is_dir() if collision == 'directory' else archive.read_bytes() == b'preserve collision bytes'
    finally: reopened.lock.release()


@pytest.mark.parametrize('error_number', [28, 122])
def test_archive_fsync_capacity_error_retains_exact_bound_transition(tmp_path, monkeypatch, error_number):
    current = metadata_journal(tmp_path, Q)
    entry = metadata_input(current, 1, Q); metadata_piece(current, Q)
    entry['clean_eof'] = True; current.save(); expected = u._json(entry)
    real = history.os.fsync; hit = []
    def fsync(fd):
        if os.readlink('/proc/self/fd/' + str(fd)) == str(current.history / 'archive.next'):
            hit.append(True); raise OSError(error_number, 'synthetic archive capacity failure')
        return real(fd)
    monkeypatch.setattr(history.os, 'fsync', fsync)
    try:
        with pytest.raises(OSError) as raised: current.archive_input()
        assert raised.value.errno == error_number and hit
        saved = journal(tmp_path)
        assert saved['inputs_done'] == 0 and saved['active_input']['clean_eof'] and saved['archive_intent']
        assert not saved['complete'] and not current.archive_path('input', 1).exists()
    finally: monkeypatch.undo(); current.lock.release()
    reopened = history.PagedJournal(current.path, u.Guard(u.Limits(disk_floor=0, output_frames=Q)), u)
    reopened.lock.acquire()
    try:
        reopened.load(); reopened.recover()
        assert u._json(reopened.read_archive('input', 1)) == expected
        assert reopened.data['inputs_done'] == 1 and not reopened.data['archive_intent']
    finally: reopened.lock.release()


def test_metadata_accounting_refreshes_controls_and_spool_without_history_census(tmp_path, monkeypatch):
    current = metadata_journal(tmp_path, u.PAGE_FRAMES)
    entry = metadata_input(current, 1, u.PAGE_FRAMES)
    for count in (u.SOURCE_FRAMES, u.SOURCE_FRAMES, u.PAGE_FRAMES - 2 * u.SOURCE_FRAMES): metadata_piece(current, count)
    entry['clean_eof'] = True; current.save(); current.archive_input()
    for piece in current.data['active_page']['pieces']:
        with current.owned(piece['name']).open('wb') as stream: stream.truncate(piece['bytes'])
    current.budget(); rolling = current.metadata_allocated; current._census()
    assert current.metadata_allocated == rolling
    # Startup may census history, but repeated PCM-budget checks may not.
    monkeypatch.setattr(history.os, 'scandir', lambda *a: pytest.fail('No history census per PCM block'))
    try:
        for _ in range(4): current.budget(u.READ_BYTES)
        extra = current.path / 'uploader.json.next'
        extra.write_bytes(b' ' * u.JSON_BYTES)
        before = current.metadata_allocated; current.budget()
        assert current.metadata_allocated - before >= u.JSON_BYTES
        extra.unlink(); current.budget(); assert current.metadata_allocated == before
        allocated_pcm = sum(max(current.owned(n).stat().st_size, current.owned(n).stat().st_blocks * 512)
                            for n in current.data['spool'])
        with pytest.raises(u.Incomplete, match='spool budget'):
            current.budget(current.guard.limits.spool_bytes - allocated_pcm + 1)
        monkeypatch.setattr(history, 'METADATA_BYTES', current.metadata_allocated)
        with pytest.raises(u.Incomplete, match='metadata allocated-byte'): current.budget()
    finally:
        monkeypatch.undo()
        metadata_release(current); rolling = current.metadata_allocated; current._census()
        assert current.metadata_allocated == rolling and not list(current.path.glob('*.wav*'))
        current.lock.release()


def test_maximum_unicode_worker_rows_fit_fixed_batch_within_response_cap():
    rows = [{'cursor': (1 << 60) + n, 'id': 'i' * 128, 'status': 'failed',
             'duration': 10.5, 'timeline_start': 172800.0, 'next_offset': 172810.5,
             'recorded_at': '\U0010ffff' * 128, 'created': 1.7976931348623157e308,
             'error': '\U0010ffff' * 2000} for n in range(16)]
    raw = u._json({'chunks': rows, 'next_cursor': rows[-1]['cursor']})
    assert len(raw) < u.JSON_BYTES and u._parse(raw)['next_cursor'] == rows[-1]['cursor']
    assert len(u._json({'chunks': rows * 2, 'next_cursor': rows[-1]['cursor']})) > u.JSON_BYTES


@pytest.mark.parametrize('promoted', [False, True])
def test_corrupt_pending_archive_fails_full_startup_before_recovery(tmp_path, monkeypatch, promoted):
    current = metadata_journal(tmp_path, Q)
    entry = metadata_input(current, 1, Q); metadata_piece(current, Q)
    entry['clean_eof'] = True; current.save(); real = current._promote_archive
    def crash(temporary, destination):
        if promoted: real(temporary, destination)
        raise Crash('pending publication')
    monkeypatch.setattr(current, '_promote_archive', crash)
    try:
        with pytest.raises(Crash): current.archive_input()
        path = current.archive_path('input', 1) if promoted else current.history / 'archive.next'
        with path.open('r+b') as stream: stream.seek(0); stream.write(b'!')
        checkpoint = (current.path / 'uploader.json').read_bytes()
    finally: monkeypatch.undo(); current.lock.release()
    reopened = history.PagedJournal(current.path, u.Guard(u.Limits(disk_floor=0, output_frames=Q)), u)
    reopened.lock.acquire()
    try:
        with pytest.raises(u.Incomplete): reopened.load()
        assert (current.path / 'uploader.json').read_bytes() == checkpoint
        assert path.read_bytes().startswith(b'!')
        assert (current.history / 'archive.next').exists()
    finally: reopened.lock.release()
