"""Generated decoder fixtures and fake ML only; no model/recognition evidence."""
import math
import shutil
import subprocess
import sys
import wave

import pytest
from fastapi.testclient import TestClient

from audio_backend.app import create_app, probe
from audio_backend.boundary_store import stream_info
from audio_backend.native_worker import NativeCoordinator
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.worker import Worker


RATE = 16000
FRAMES = 32003


def write_wav(path, frames=FRAMES, rate=RATE):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes((b'\x01\x00\xff\xff' * ((frames + 1) // 2))[:frames * 2])


def ffmpeg(*args):
    return subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1',
         '-filter_threads', '1', '-y', *map(str, args)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


def decode(path):
    return ffmpeg('-i', path, '-map', '0:a:0', '-vn', '-ac', '1', '-ar', RATE,
                  '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1')


@pytest.fixture
def settings(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg and ffprobe are required for generated decoder fixtures')
    (tmp_path / 'tmp').mkdir()
    return Settings(data_dir=tmp_path, disk_reserve_bytes=0, start_worker=False,
                    cpu_threads=1, process_memory_limit_bytes=512 * 1024**2,
                    diarization_backend='nemotron', native_diarization_model='/fake/model',
                    native_diarization_library='/fake/library')


@pytest.fixture(params=['wav', 'flac'])
def exact_source(request, tmp_path):
    wav = tmp_path / 'source.wav'
    write_wav(wav)
    if request.param == 'wav':
        return wav
    path = wav.with_suffix('.flac')
    ffmpeg('-i', wav, path)
    return path


class FrameNative:
    """Count actual PCM frames; hold all output until explicit finish."""
    def __init__(self, settings):
        self.frames = 0
        self.pushes = []
        self.finishes = 0
        self.finished = False

    def request(self, action, stop, **args):
        assert not stop()
        if action == 'reset':
            self.frames = 0
            self.finished = False
            return {'state': self.state(), 'provenance': {'diarization_fingerprint': 'test'}}
        if action == 'push':
            with wave.open(args['wav'], 'rb') as source:
                frames = source.getnframes()
            assert frames > 0, 'Empty native pushes must never be submitted'
            self.frames += frames
            self.pushes.append(frames)
        elif action == 'finish':
            self.finished = True
            self.finishes += 1
        elif action == 'turns':
            return {'turns': []}
        return self.state()

    def state(self):
        frontier = self.frames / RATE if self.finished else 0.
        return {'input_seconds': self.frames / RATE, 'processed_seconds': frontier,
                'stable_seconds': frontier, 'finished': self.finished}

    def close(self):
        pass


class RecordingAnalyzer:
    inputs = []

    def __init__(self, config):
        pass

    def analyze(self, path, **kwargs):
        with wave.open(path, 'rb') as source:
            self.inputs.append(source.readframes(source.getnframes()))
        return {'segments': [], 'events': [], 'speaker_turns': [], 'metadata': {}}

    def close(self):
        pass


def worker_source(settings, source, duration, origin=0.):
    store = Store(settings.data_dir / 'test.sqlite', settings)
    sid = store.create_session('Generated fixture')['id']
    cid, _ = store.add_chunk(sid, 'source', 'test-digest', source, duration, origin)
    worker = Worker(store, settings, RecordingAnalyzer)
    worker.native = NativeCoordinator(settings, worker.extract_pcm, FrameNative)
    RecordingAnalyzer.inputs = []
    return store, worker, cid


def test_exact_same_rate_pcm_flac_probe(settings, exact_source):
    settings.ffmpeg = '/not-invoked-by-admission'
    assert probe(exact_source, settings) == FRAMES / RATE


@pytest.mark.parametrize('origin', [0., 0.000017, 86400.1234567])
def test_exact_source_completes_without_losing_last_sample(settings, exact_source, origin):
    store, worker, cid = worker_source(settings, exact_source, probe(exact_source, settings), origin)
    worker.process(store.claim())
    result = store.chunk(cid)
    assert result['status'] == 'completed'
    assert result['timeline_start'] == origin
    assert result['duration'] == FRAMES / RATE
    assert result['next_offset'] == FRAMES / RATE
    assert worker.native.process.frames == FRAMES
    assert worker.native.process.finishes == 1
    assert RecordingAnalyzer.inputs == [decode(exact_source)]


def test_old_rounded_down_receipt_does_not_stall_one_sample(settings, exact_source):
    # A pre-upgrade six-decimal receipt can round either direction. Do not rewrite it.
    old_duration = math.floor(FRAMES / RATE * 1e6) / 1e6
    store, worker, cid = worker_source(settings, exact_source, old_duration)
    worker.process(store.claim())
    result = store.chunk(cid)
    assert result['status'] == 'completed'
    assert result['duration'] == old_duration
    assert result['next_offset'] == FRAMES / RATE
    assert worker.native.process.frames == FRAMES
    assert RecordingAnalyzer.inputs == [decode(exact_source)]


def test_final_one_sample_interval_is_owned(settings, exact_source):
    store, worker, cid = worker_source(settings, exact_source, FRAMES / RATE, .000017)
    source = store.claim()
    start = (FRAMES - 1) / RATE
    result = worker.native.prepare(source, start, FRAMES / RATE, None, lambda: False)
    assert result is not None
    assert result['end'] == FRAMES / RATE


@pytest.mark.parametrize('variant', ['primed', 'shifted', 'long_container'])
def test_aac_metadata_mismatch_fails_before_native_push(settings, tmp_path, variant):
    source = tmp_path / 'tone.wav'
    write_wav(source, 96001, 48000)
    encoded = tmp_path / (variant + '.m4a')
    if variant == 'long_container':
        encoded = encoded.with_suffix('.mp4')
        ffmpeg('-f', 'lavfi', '-i', 'color=size=16x16:rate=2:duration=3',
               '-i', source, '-c:v', 'mpeg4', '-c:a', 'aac', encoded)
    else:
        options = ['-output_ts_offset', '0.25'] if variant == 'shifted' else []
        ffmpeg('-i', source, '-c:a', 'aac', *options, encoded)
    duration = probe(encoded, settings)
    actual_frames = len(decode(encoded)) // 2
    if variant == 'long_container':
        assert actual_frames < round(duration * RATE)
    else:
        assert actual_frames > round(duration * RATE)
    store, worker, cid = worker_source(settings, encoded, duration, .000017)
    with pytest.raises(RuntimeError, match='Source duration does not match decoded PCM'):
        worker.process(store.claim())
    assert store.chunk(cid)['next_offset'] == 0
    assert not RecordingAnalyzer.inputs


def test_partial_interval_does_not_probe_past_its_end(settings, exact_source):
    store, worker, cid = worker_source(settings, exact_source, FRAMES / RATE)
    out = settings.data_dir / 'partial.wav'
    worker.extract_pcm(store.claim(), 0., 1., out, settings.data_dir / 'tmp', lambda: False)
    with wave.open(str(out), 'rb') as source:
        assert source.getnframes() == RATE


@pytest.mark.parametrize('first_frame', [1, 16000, FRAMES - 1])
def test_exact_terminal_intervals_preserve_samples(settings, exact_source, first_frame):
    origin = 86400.1234567
    store, worker, cid = worker_source(settings, exact_source, FRAMES / RATE, origin)
    out = settings.data_dir / 'tail.wav'
    worker.extract_pcm(store.claim(), origin + first_frame / RATE, origin + FRAMES / RATE,
                       out, settings.data_dir / 'tmp', lambda: False)
    with wave.open(str(out), 'rb') as source:
        assert source.getnframes() == FRAMES - first_frame
        assert source.readframes(source.getnframes()) == decode(exact_source)[first_frame * 2:]


def test_decode_nonzero_exit_is_not_clean_eof(settings, exact_source):
    store, worker, cid = worker_source(settings, exact_source, FRAMES / RATE)
    # Execute a real failing decoder command under the same watchdog, without models.
    decoder = settings.data_dir / 'failed-decoder'
    decoder.write_text('#!' + sys.executable + '\nimport sys\nsys.exit(23)\n')
    decoder.chmod(0o700)
    settings.ffmpeg = str(decoder)
    with pytest.raises(RuntimeError, match='exited with code 23'):
        worker.process(store.claim())
    assert store.chunk(cid)['next_offset'] == 0
    assert not RecordingAnalyzer.inputs


def test_corrupt_aac_packet_cannot_be_silently_skipped(settings, tmp_path):
    wav = tmp_path / 'generated.wav'
    write_wav(wav, 96001, 48000)
    encoded = tmp_path / 'corrupt-generated.aac'
    ffmpeg('-i', wav, '-c:a', 'aac', '-f', 'adts', encoded)
    original_frames = len(decode(encoded)) // 2
    data = bytearray(encoded.read_bytes())
    packets = []
    position = 0
    while position < len(data):
        assert data[position] == 0xff
        size = ((data[position + 3] & 3) << 11) | (data[position + 4] << 3) | (data[position + 5] >> 5)
        assert size > 7
        packets.append((position, size))
        position += size
    position, size = packets[len(packets) // 2]
    data[position + 7:position + size] = b'\0' * (size - 7)
    encoded.write_bytes(data)
    # Control: FFmpeg normally skips this corrupt AAC packet and still exits zero.
    permissive = subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-ss', '0', '-i', str(encoded),
         '-map', '0:a:0', '-ac', '1', '-ar', str(RATE), '-f', 's16le', 'pipe:1'],
        capture_output=True, check=True,
    )
    assert permissive.stderr
    assert len(permissive.stdout) // 2 < original_frames
    store, worker, cid = worker_source(settings, encoded, original_frames / RATE)
    with pytest.raises(RuntimeError, match='ffmpeg exited with code'):
        worker.process(store.claim())
    assert store.chunk(cid)['next_offset'] == 0
    assert not RecordingAnalyzer.inputs


def test_duration_failure_and_retry_preserve_committed_checkpoint(settings, tmp_path):
    wav = tmp_path / 'generated.wav'
    write_wav(wav, 96001, 48000)
    encoded = tmp_path / 'primed.m4a'
    ffmpeg('-i', wav, '-c:a', 'aac', encoded)
    duration = probe(encoded, settings)
    settings.window_seconds = 1.
    settings.context_seconds = 0.
    store, worker, cid = worker_source(settings, encoded, duration, .000017)

    class ImmediateNative(FrameNative):
        def state(self):
            state = super().state()
            state['processed_seconds'] = state['stable_seconds'] = self.frames / RATE
            return state

    for attempt in range(2):
        worker.native = NativeCoordinator(settings, worker.extract_pcm, ImmediateNative)
        worker.native.PUSH_SECONDS = 1.  # A small fake batch keeps this fixture tiny.
        with pytest.raises(RuntimeError, match='Source duration does not match decoded PCM') as error:
            worker.process(store.claim())
        store.fail(cid, str(error.value))
        failed = store.chunk(cid)
        assert failed['status'] == 'failed'
        assert failed['next_offset'] == 1.
        assert failed['timeline_start'] == .000017
        assert failed['duration'] == duration
        assert 'Source duration does not match decoded PCM' in failed['error']
        assert len(RecordingAnalyzer.inputs) == 1
        if attempt == 0:
            store.set_state(cid, 'retry')


def test_native_nonprogress_fails_instead_of_repeating_decode(settings, exact_source):
    store, worker, cid = worker_source(settings, exact_source, FRAMES / RATE)
    class Stuck(FrameNative):
        def request(self, action, stop, **args):
            if action == 'push':
                self.pushes.append(1)
                assert len(self.pushes) == 1, 'Native nonprogress must fail after its first push'
                return self.state()
            return super().request(action, stop, **args)
    worker.native = NativeCoordinator(settings, worker.extract_pcm, Stuck)
    with pytest.raises(RuntimeError, match='Native input did not advance by decoded frame count'):
        worker.process(store.claim())


def test_old_rounded_stream_drift_fails_without_rewriting_receipts(settings, tmp_path):
    source = tmp_path / 'short.wav'
    write_wav(source, 8001)
    duration = math.floor(8001 / RATE * 1e6) / 1e6
    store = Store(tmp_path / 'drift.sqlite', settings)
    sid = store.create_session('Old rounded receipts')['id']
    ids = [store.add_chunk(sid, str(i), 'same-generated-file', source, duration, None,
                           stream_id='mic', stream_final=i == 64)[0] for i in range(65)]
    before = [(store.chunk(cid)['timeline_start'], store.chunk(cid)['duration'],
               store.chunk(cid)['next_offset']) for cid in ids]
    worker = Worker(store, settings, RecordingAnalyzer)
    native = NativeCoordinator(settings, worker.extract_pcm, FrameNative)
    chunk = store.claim()
    try:
        with pytest.raises(RuntimeError, match='Source duration does not match decoded PCM'):
            native.prepare(chunk, 0., duration, stream_info(store, chunk), lambda: False)
        assert native.process.frames == round(21.2 * RATE)
        assert not native.process.finished
    finally:
        native.close()
    after = [(store.chunk(cid)['timeline_start'], store.chunk(cid)['duration'],
              store.chunk(cid)['next_offset']) for cid in ids]
    assert after == before


def test_http_exact_duration_stream_seam_restart(settings, tmp_path):
    first_path = tmp_path / 'first.wav'
    second_path = tmp_path / 'second.wav'
    write_wav(first_path, 16001)
    write_wav(second_path, 16003)
    app = create_app(settings, RecordingAnalyzer)
    with TestClient(app) as client:
        sid = client.post('/sessions', json={}).json()['id']
        worker = app.state.worker
        store = app.state.store
        worker.native = NativeCoordinator(settings, worker.extract_pcm, FrameNative)
        RecordingAnalyzer.inputs = []
        first = client.put(f'/sessions/{sid}/chunks?stream_id=mic&timeline_start=.000017',
                           headers={'Idempotency-Key': 'first'}, content=first_path.read_bytes()).json()['chunk']
        assert first['duration'] == 16001 / RATE
        worker.process(store.claim())
        assert store.chunk(first['id'])['status'] == 'waiting_input'
        worker.native.close()
        worker.native = NativeCoordinator(settings, worker.extract_pcm, FrameNative)
        last = client.put(f'/sessions/{sid}/chunks?stream_id=mic&stream_final=true',
                          headers={'Idempotency-Key': 'last'}, content=second_path.read_bytes()).json()['chunk']
        assert last['timeline_start'] == first['timeline_start'] + first['duration']
        while chunk := store.claim():
            worker.process(chunk)
        assert store.chunk(first['id'])['status'] == store.chunk(last['id'])['status'] == 'completed'
        assert store.chunk(first['id'])['next_offset'] == 16001 / RATE
        assert store.chunk(last['id'])['next_offset'] == 16003 / RATE
        assert worker.native.process.frames == 32004
        assert worker.native.process.finishes == 1
