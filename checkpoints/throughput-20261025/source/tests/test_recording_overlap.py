"""Finalized recording batch fixtures: real storage/API/worker, no loaded models.

Run with ordinary pytest after source review. Synthetic analyzer/native fixtures
are explicit; they establish routing/lineage, not acoustic or MOSS quality.
"""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import threading
import time

import hashlib
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import wave

import pytest
from fastapi.testclient import TestClient

from audio_backend import recording_overlap as mod
from audio_backend.app import create_app
from audio_backend.boundary_store import context_parts, stream_info
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.worker import Worker


def pcm(frames):
    return b''.join(struct.pack('<h', (index * 811 % 60001) - 30000) for index in range(frames))


def wav_bytes(data):
    out = io.BytesIO()
    with wave.open(out, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(data)
    return out.getvalue()


def settings(root, **kwargs):
    return Settings(data_dir=root, disk_reserve_bytes=0, start_worker=False, **kwargs)


@pytest.fixture
def fixture(tmp_path):
    cfg = settings(tmp_path)
    (tmp_path / 'uploads').mkdir(); (tmp_path / 'tmp').mkdir()
    store = Store(tmp_path / 'jobs.sqlite3', cfg)
    sid = store.create_session('recording fixture')['id']
    return store, mod.RecordingOverlap(store), sid


def put(recording, sid, key, data, offset, capture='capture.one'):
    batch = recording.begin(sid, capture)
    path = recording.source_path(batch)
    path.write_bytes(wav_bytes(data))
    try:
        return recording.accept(sid, capture, key, path, offset)
    finally:
        path.unlink(missing_ok=True)


def finish(recording, sid, capture='capture.one', key='finish', revision=None, cells=None):
    revision = recording.status(sid, capture)['revision'] if revision is None else revision
    return recording.finish(sid, capture, key, revision, cells)


def paths(store, sid):
    with store.db() as db:
        originals = [dict(row) for row in db.execute('''SELECT s.* FROM recording_sources s
            JOIN recording_batches b ON b.id=s.batch_id WHERE b.session_id=? ORDER BY s.seq''', (sid,))]
        work = [dict(row) for row in db.execute('''SELECT c.* FROM recording_work w JOIN chunks c ON c.id=w.chunk_id
            JOIN recording_batches b ON b.id=w.batch_id WHERE b.session_id=? ORDER BY w.position''', (sid,))]
    return originals, work


def joined(work):
    result = bytearray()
    for item in work:
        with wave.open(item['path'], 'rb') as stream:
            assert (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) == (16000, 1, 2)
            result.extend(stream.readframes(stream.getnframes()))
    return bytes(result)


def assert_complete_map(result):
    for source in result['sources']:
        ranges = sorted((row for row in result['ranges'] if row['source_id'] == source['id']),
                        key=lambda row: row['source_start_frame'])
        cursor = 0
        for row in ranges:
            assert row['source_start_frame'] == cursor
            assert row['capture_start_frame'] == source['source_start_frame'] + cursor
            assert row['relation'] in ('owner', 'exact_alias')
            cursor += row['frames']
        assert cursor == source['frames']


@pytest.mark.parametrize('order', [(0, 1, 2, 3), (1, 3, 2, 0)])
def test_exact_prefix_suffix_contained_and_out_of_order(fixture, order):
    store, recording, sid = fixture
    data = pcm(40000)
    intervals = [(0, 24000), (16000, 40000), (8000, 16000), (0, 24000)]
    expected = {}
    for index in order:
        a, b = intervals[index]
        receipt = put(recording, sid, str(index), data[a*2:b*2], a + 900000)
        expected[receipt['source_id']] = wav_bytes(data[a*2:b*2])
    assert store.claim() is None
    result = finish(recording, sid)
    assert result['state'] == 'queued'
    assert result['capture_origin_frame'] == 900000
    assert result['capture_end_frame'] == 940000
    assert sum(chunk['frames'] for chunk in result['work_chunks']) == 40000
    assert any(row['relation'] == 'exact_alias' for row in result['ranges'])
    assert_complete_map(result)
    originals, work = paths(store, sid)
    assert joined(work) == data
    assert len(originals) == 4
    for source in originals:
        assert Path(source['path']).read_bytes() == expected[source['id']]
        assert source['digest'] == hashlib.sha256(expected[source['id']]).hexdigest()
    assert work[0]['timeline_start'] == 0
    with store.db() as db:
        rows = [dict(row) for row in db.execute('SELECT * FROM stream_chunks WHERE session_id=? ORDER BY position', (sid,))]
    assert rows[-1]['stream_final'] == 1
    assert len({row['stream_id'] for row in rows}) == 1


def test_one_differing_sample_retained_as_conflict_with_zero_work(fixture):
    store, recording, sid = fixture
    data = pcm(32000); different = bytearray(data[16000*2:])
    different[100] ^= 1
    put(recording, sid, 'a', data, 0)
    put(recording, sid, 'b', bytes(different), 16000)
    result = finish(recording, sid)
    assert result['state'] == 'unresolved'
    assert any(row['kind'] == 'pcm_conflict' for row in result['blockers'])
    assert result['ranges'] == result['work_chunks'] == []
    originals, work = paths(store, sid)
    assert len(originals) == 2 and work == [] and store.claim() is None
    assert Path(originals[1]['path']).read_bytes() == wav_bytes(bytes(different))
    assert finish(recording, sid) == result
    with pytest.raises(ValueError, match='frozen'):
        put(recording, sid, 'late', data, 0)


@pytest.mark.parametrize('offset,kind', [(None, 'unknown_placement'), (64000, 'capture_gap')])
def test_unknown_microphone_placement_and_gaps_never_infer_alignment(fixture, offset, kind):
    store, recording, sid = fixture
    data = pcm(16000)
    put(recording, sid, 'known', data, 0)
    put(recording, sid, 'mic-two-unknown' if offset is None else 'gap', data, offset)
    result = finish(recording, sid)
    assert result['state'] == 'unresolved'
    assert kind in {row['kind'] for row in result['blockers']}
    assert len(result['sources']) == 2
    assert result['ranges'] == result['work_chunks'] == [] and store.claim() is None


def test_distinct_capture_domains_never_compare_or_alias(fixture):
    store, recording, sid = fixture
    second = store.create_session('other microphone')['id']
    data = pcm(16000)
    put(recording, sid, 'first', data, 1234, 'microphone.a')
    put(recording, second, 'second', data, 1234, 'microphone.b')
    with pytest.raises(ValueError, match='different recording coordinate domain'):
        recording.begin(sid, 'microphone.b')
    a = finish(recording, sid, 'microphone.a')
    b = finish(recording, second, 'microphone.b')
    assert a['stream_id'] != b['stream_id']
    assert all(row['relation'] == 'owner' for row in a['ranges'] + b['ranges'])
    assert len(paths(store, sid)[0]) == len(paths(store, second)[0]) == 1


def test_fixed_piece_geometry_and_source_to_work_mapping(fixture):
    store, recording, sid = fixture
    data = pcm(mod.CHUNK_FRAMES * 2 + 1)
    put(recording, sid, 'whole', data, 77)
    result = finish(recording, sid)
    assert [row['frames'] for row in result['work_chunks']] == [mod.CHUNK_FRAMES, mod.CHUNK_FRAMES, 1]
    assert [row['timeline_start'] for row in result['work_chunks']] == [0, 10.5, 21.0]
    assert_complete_map(result)
    assert joined(paths(store, sid)[1]) == data


def test_source_and_finish_idempotency_bind_metadata_and_revision(fixture):
    store, recording, sid = fixture
    data = pcm(16000)
    first = put(recording, sid, 'source', data, 0)
    assert put(recording, sid, 'source', data, 0) == {'source_id': first['source_id'], 'duplicate': True}
    assert recording.status(sid, 'capture.one')['revision'] == 1
    with pytest.raises(ValueError, match='different bytes or placement'):
        put(recording, sid, 'source', data, 1)
    with pytest.raises(ValueError, match='revision changed'):
        finish(recording, sid, revision=0)
    result = finish(recording, sid)
    assert finish(recording, sid) == result
    with pytest.raises(ValueError, match='different finish request'):
        finish(recording, sid, key='different')
    assert len(paths(store, sid)[1]) == 1
    assert put(recording, sid, 'source', data, 0)['duplicate']
    with pytest.raises(ValueError, match='frozen'):
        put(recording, sid, 'another', data, 0)


def test_original_tamper_prevents_work_and_retains_receipt(fixture):
    store, recording, sid = fixture
    put(recording, sid, 'original', pcm(16000), 0)
    original = paths(store, sid)[0][0]
    with Path(original['path']).open('r+b') as stream:
        stream.seek(100); stream.write(b'xx')
    result = finish(recording, sid)
    assert result['state'] == 'unresolved'
    assert result['blockers'][0]['kind'] == 'original_unavailable'
    assert result['sources'][0]['digest'] == original['digest']
    assert store.claim() is None


def test_limits_include_source_count_duration_and_copies(fixture, monkeypatch):
    store, recording, sid = fixture
    data = pcm(16000)
    put(recording, sid, 'one', data, 0)
    monkeypatch.setattr(mod, 'MAX_SOURCES', 1)
    with pytest.raises(OverflowError, match='count'):
        put(recording, sid, 'two', data, 16000)
    monkeypatch.setattr(mod, 'MAX_CAPTURE_FRAMES', 8000)
    result = finish(recording, sid)
    assert result['state'] == 'unresolved'
    assert result['blockers'] == [{'kind': 'capture_duration_capacity'}]
    assert len(paths(store, sid)[0]) == 1
    # Physical orphan and failed-cleanup copies count before any further write.
    orphan = store.settings.data_dir / 'uploads' / 'ro-orphan-copy.part'
    orphan.write_bytes(b'x' * 8192)
    monkeypatch.setattr(mod, 'MAX_TOTAL_BYTES', mod.METADATA_RESERVATION + 4096)
    with pytest.raises(OverflowError, match='quota'):
        recording.admit_bytes(recording.begin(sid, 'capture.one')['id'], 1)


def test_stricter_existing_source_limit_and_disk_floor(fixture, monkeypatch):
    store, recording, sid = fixture
    store.settings.max_audio_seconds = .5
    with pytest.raises(OverflowError, match='duration'):
        put(recording, sid, 'large', pcm(16000), 0)
    assert recording.status(sid, 'capture.one')['sources'] == []
    store.settings.max_audio_seconds = 300
    def no_disk(*args, **kwargs):
        raise RuntimeError('Disk reserve reached')
    monkeypatch.setattr(mod, 'check_resources', no_disk)
    with pytest.raises(RuntimeError, match='Disk reserve'):
        put(recording, sid, 'no-space', pcm(16000), 0)
    assert list((store.settings.data_dir / 'uploads').glob('*.audio')) == []


def test_stage_rollback_retains_immutable_fence_and_originals(fixture):
    store, recording, sid = fixture
    put(recording, sid, 'original', pcm(mod.CHUNK_FRAMES + 100), 0)
    originals = paths(store, sid)[0]
    with store.db() as db:
        db.execute("""CREATE TRIGGER reject_second_recording BEFORE INSERT ON chunks
            WHEN NEW.timeline_start>0 BEGIN SELECT RAISE(ABORT,'injected stage failure'); END""")
    with pytest.raises(Exception, match='injected stage failure'):
        finish(recording, sid)
    assert paths(store, sid)[1] == []
    assert store.claim() is None
    assert [Path(row['path']).exists() for row in originals] == [True]
    assert len(list((store.settings.data_dir / 'uploads').glob('*.audio'))) == 1
    assert recording.status(sid, 'capture.one')['state'] == 'interrupted'
    with store.db() as db:
        db.execute('DROP TRIGGER reject_second_recording')
    assert finish(recording, sid)['state'] == 'interrupted'
    assert store.claim() is None
    with pytest.raises(ValueError, match='frozen'):
        put(recording, sid, 'late', pcm(16000), 0)


def test_real_process_death_after_complete_stage_resumes_without_new_work(fixture):
    store, recording, sid = fixture
    put(recording, sid, 'original', pcm(mod.CHUNK_FRAMES + 100), 50)
    code = '''
import os,sys
from pathlib import Path
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.recording_overlap import RecordingOverlap
root=Path(sys.argv[1]);cfg=Settings(data_dir=root,disk_reserve_bytes=0,start_worker=False)
store=Store(root/'jobs.sqlite3',cfg)
RecordingOverlap.advance=lambda *a,**kw:os._exit(73)
RecordingOverlap(store).finish(sys.argv[2],'capture.one','finish',1)
'''
    result = subprocess.run([sys.executable, '-c', code, str(store.settings.data_dir), sid],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30,
                            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == 73, result.stderr.decode()
    originals, work = paths(store, sid)
    assert len(originals) == 1 and len(work) == 2
    assert all(row['status'] == 'staging' for row in work)
    with TestClient(create_app(store.settings)) as client:
        current = client.get(f'/sessions/{sid}/recordings/capture.one').json()
        assert current['state'] == 'pending_receipts'
        assert {row['chunk_id'] for row in current['work_chunks']} == {row['id'] for row in work}
        assert all(Path(row['path']).exists() for row in originals + work)
        retry = client.post(f'/sessions/{sid}/recordings/capture.one/finish',
                            json={'expected_revision': 1, 'idempotency_key': 'finish'})
        assert retry.status_code == 202 and retry.json()['state'] == 'pending_receipts'
        assert client.app.state.store.claim() is None
        assert client.post(f"/chunks/{work[0]['id']}/resume").status_code == 409
        assert client.app.state.worker.recordings.advance(None, force=True)
        resumed = client.get(f'/sessions/{sid}/recordings/capture.one').json()
        assert resumed['state'] == 'queued'
        assert {row['chunk_id'] for row in resumed['work_chunks']} == {row['id'] for row in work}
        deleted = client.delete(f'/sessions/{sid}')
        assert deleted.status_code == 200 and deleted.json()['cleanup_pending'] == 0
    assert all(not Path(row['path']).exists() for row in originals + work)


def test_session_erasure_mapping_and_retry_cleanup(fixture):
    store, recording, sid = fixture
    put(recording, sid, 'source', pcm(16000), 0)
    finish(recording, sid)
    originals, work = paths(store, sid)
    failed = originals[0]['path']
    def unlink(raw):
        if raw == failed:
            raise PermissionError('fixture locked original')
        Path(raw).unlink(missing_ok=True)
    result = store.delete_session(sid, unlink)
    assert result['cleanup_pending'] == 1
    assert Path(failed).exists() and not Path(work[0]['path']).exists()
    with store.db() as db:
        for table in ('recording_batches', 'recording_sources', 'recording_work', 'recording_ranges'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    assert store.delete_session(sid)['cleanup_pending'] == 0
    assert not Path(failed).exists()


def test_http_unknown_validation_and_old_nonoverlap_compatibility(tmp_path, monkeypatch):
    app = create_app(settings(tmp_path))
    monkeypatch.setattr('audio_backend.app.probe', lambda path, settings: 1.0)
    with TestClient(app) as client:
        sid = client.post('/sessions', json={'name': 'legacy'}).json()['id']
        payload = wav_bytes(pcm(16000))
        old = client.put(f'/sessions/{sid}/chunks', content=payload, headers={'Idempotency-Key': 'old'})
        assert old.status_code == 202
        again = client.put(f'/sessions/{sid}/chunks', content=payload, headers={'Idempotency-Key': 'old'})
        assert again.status_code == 202 and again.json()['duplicate']
        overlapping = client.put(f'/sessions/{sid}/chunks?timeline_start=0', content=payload,
                                 headers={'Idempotency-Key': 'overlap'})
        assert overlapping.status_code == 409
        assert client.put(f'/sessions/{sid}/recordings/new/sources?source_start_frame=0', content=payload,
                          headers={'Idempotency-Key': 'new'}).status_code == 409
        second = client.post('/sessions', json={}).json()['id']
        url = f'/sessions/{second}/recordings/microphone.unknown'
        for value in ('-1', '1.5', 'true'):
            assert client.put(url + '/sources?source_start_frame=' + value, content=payload,
                              headers={'Idempotency-Key': value}).status_code == 422
        accepted = client.put(url + '/sources', content=payload, headers={'Idempotency-Key': 'unknown'})
        assert accepted.status_code == 202 and accepted.json()['sources'][0]['source_start_frame'] is None
        assert client.put(f'/sessions/{second}/chunks', content=payload,
                          headers={'Idempotency-Key': 'mixed'}).status_code == 409
        finalized = client.post(url + '/finish', json={'expected_revision': 1, 'idempotency_key': 'finish'})
        assert finalized.status_code == 202 and finalized.json()['state'] == 'unresolved'
        status = client.get(url)
        assert 'no-store' in status.headers['cache-control']
        assert str(tmp_path) not in status.text


def test_normal_worker_and_restart_replay_do_not_duplicate_committed_asr(fixture, monkeypatch):
    store, recording, sid = fixture
    data = pcm(mod.CHUNK_FRAMES + 1000)
    put(recording, sid, 'a', data[:200000], 0)
    put(recording, sid, 'b', data[160000:], 80000)
    result = finish(recording, sid)
    calls = []
    class Analyzer:
        def __init__(self, config):pass
        def close(self):pass
        def analyze(self, path, **kwargs):
            with wave.open(path, 'rb') as source:
                frames = source.getnframes()
            calls.append(frames)
            return {'segments': [{'start': 0., 'end': frames / 16000, 'text': 'fixture transcript'}],
                    'events': [], 'speaker_turns': [], 'metadata': {}}
    def extract(worker, chunk, start, end, wav, tmp, stop, **kwargs):
        with wave.open(str(wav), 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
            for part in context_parts(worker.store, chunk, start, end):
                with wave.open(part['path'], 'rb') as source:
                    source.setpos(round(part['read_start'] * 16000))
                    out.writeframes(source.readframes(round(part['read_duration'] * 16000)))
        return None
    monkeypatch.setattr(Worker, 'extract_pcm', extract)
    worker = Worker(store, store.settings, Analyzer)
    while (chunk := store.claim()) is not None:
        worker.process(chunk)
    with store.db() as db:
        before = [dict(row) for row in db.execute('SELECT * FROM segments ORDER BY id')]
    assert before and len(calls) == len(result['work_chunks'])
    # Running inference can replay after a crash; this test claims only durable
    # work/committed result idempotence for already completed batches.
    store.recover()
    retry = finish(mod.RecordingOverlap(store), sid)
    assert [row['chunk_id'] for row in retry['work_chunks']] == [row['chunk_id'] for row in result['work_chunks']]
    assert store.claim() is None
    with store.db() as db:
        assert [dict(row) for row in db.execute('SELECT * FROM segments ORDER BY id')] == before
    assert len(calls) == len(result['work_chunks'])
    assert worker.stop() and not worker.thread.is_alive()


def test_optional_hook_refusal_never_blocks_primary(fixture):
    store, recording, sid = fixture
    put(recording, sid, 'source', pcm(16000), 0)
    calls = []
    class RefusingCells:
        def streamed_descriptor(self, fd, digest, size):
            calls.append('descriptor'); return None
        def prepare_upload(self, path, digest, size, observation):
            assert path.suffix == '.part' and path.exists()
            calls.append('prepare'); return None
        def finish_upload(self, token, session, cid, path, created):
            assert path.suffix == '.audio' and path.exists() and created
            assert store.chunk(cid)['status'] == 'staging'
            assert store.claim() is None
            calls.append('finish'); return {'status': 'receipt_unavailable'}
    result = finish(recording, sid, cells=RefusingCells())
    assert calls == ['descriptor', 'prepare']
    assert result['state'] == 'queued'
    assert result['work_chunks'][0]['native_receipt_status'] == 'observation_unavailable'
    assert result['optional_receipts']['gaps'] == 1 and not result['optional_receipts']['ready']
    assert store.claim() is not None


@pytest.fixture
def native(tmp_path):
    """Actual descriptor/authority/owner, explicitly simulated binary qualification."""
    from audio_backend import native_owned_cell_capture as capture_module
    from audio_backend import native_owned_cell_store as cell_module
    from test_native_owned_cell_store import simulated_profile
    cfg = settings(tmp_path, diarization_backend='nemotron',
                   native_diarization_model='fixture-model', native_diarization_library='fixture-library')
    app = create_app(cfg)
    owners = []
    with TestClient(app) as client:
        sid = client.post('/sessions', json={}).json()['id']
        cfg.native_owned_cells_enabled = True
        def new_owner():
            authority = capture_module.PrimaryAuthority(app.state.store)
            owner = cell_module.CellStore(tmp_path / 'native-owned-cells', authority,
                enabled=True, qualification=simulated_profile(tmp_path), instance_lock=app.state.instance_lock)
            authority.owner = owner
            capture = capture_module.Capture(owner, authority)
            app.state.worker.native_owned_cells = capture
            capture_module._REGISTRY[str(authority.path)] = capture
            owners.append(owner); owner.start()
            assert owner._ready.wait(3), owner.health()
            return capture
        capture = new_owner()
        item = SimpleNamespace(client=client, app=app, store=app.state.store, sid=sid,
            recording=app.state.worker.recordings, capture=capture, owner=capture.owner,
            new_owner=new_owner, root=tmp_path)
        try:
            yield item
        finally:
            for owner in owners:
                owner.close(.5)
                if owner._thread is not None:
                    owner._thread.join(3)
                assert owner._thread is None or not owner._thread.is_alive()
                assert owner._db is None and not owner.health()['cleanup_pending']
    capture_module._REGISTRY.pop(str(capture.authority.path), None)
    assert not app.state.worker.thread.is_alive()


def native_batch(native, frames=mod.CHUNK_FRAMES + 100):
    put(native.recording, native.sid, 'original', pcm(frames), 19)
    return finish(native.recording, native.sid, cells=native.capture)


def drain(native, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        native.recording.advance(native.capture, force=True)
        result = native.recording.status(native.sid, 'capture.one')
        if result['state'] != 'pending_receipts':
            return result
        flight = native.recording._flight
        if flight is not None:
            flight['operation'].slot_released.wait(.02)
        else:
            time.sleep(.01)
    pytest.fail('Bounded fixture receipt drain did not reach a terminal batch state')


def payloads(native):
    with native.store.db() as db:
        return [tuple(row) for row in db.execute('''SELECT chunk_id,native_receipt,receipt_id FROM recording_work
            WHERE batch_id=(SELECT id FROM recording_batches WHERE session_id=?) ORDER BY position''', (native.sid,))]


def test_actual_native_cell_ingestion_hook_preserves_descriptor_contract(native):
    from audio_backend import native_owned_cell_capture as capture_module
    result = native_batch(native, 16000)
    assert result['state'] == 'pending_receipts' and native.store.claim() is None
    result = drain(native)
    assert result['state'] == 'queued' and result['optional_receipts']['ready']
    chunk = result['work_chunks'][0]
    receipt = native.owner.receipt(chunk['chunk_id'])
    assert receipt is not None
    assert receipt.digest == chunk['digest'] and receipt.frames == 16000 and receipt.timeline_start == 0
    _, work = paths(native.store, native.sid)
    assert receipt.signature == capture_module.signature(Path(work[0]['path']).stat())
    assert native.capture.duplicate(chunk['chunk_id'], chunk['digest'])['status'] == 'existing_receipt'
    assert all(len(row[1]) <= 1024 for row in payloads(native))


def test_staged_receipt_attests_before_mapping_and_atomic_release(native, monkeypatch):
    """Real primary/owner receipt path remains valid while inference is fenced."""
    from audio_backend.boundary_store import stream_info
    from audio_backend.native_owned_cell_store import Budget, Operation, Receipt
    from audio_backend.native_owned_cells import Unavailable
    advance = native.recording.advance
    publish = native.recording._publish_pending
    monkeypatch.setattr(native.recording, 'advance', lambda *args, **kwargs: False)
    result = native_batch(native, 16000)
    cid, payload, _ = payloads(native)[0]
    receipt = Receipt.decode(payload)
    _, work = paths(native.store, native.sid)
    chunk = work[0]
    assert result['state'] == 'pending_receipts' and native.store.claim() is None
    with Budget().phase() as budget:
        assert native.capture.authority.row(cid, budget)['status'] == 'staging'
        assert native.capture.authority.receipt_valid(receipt, budget)
    with Budget().phase() as budget, pytest.raises(Unavailable, match='primary_source_unpublished'):
        native.capture.authority.mapping(chunk, 0., 0, 16000, budget)
    operation = native.owner.submit('receipt', receipt)
    assert type(operation) is Operation and operation.slot_released.wait(3)
    assert native.owner.receipt(cid) == receipt
    monkeypatch.setattr(native.recording, 'advance', advance)
    monkeypatch.setattr(native.recording, '_publish_pending', lambda *args, **kwargs: False)
    advance(native.capture, force=True)  # Persist the exact current-owner acknowledgement.
    assert native.recording.status(native.sid, 'capture.one')['optional_receipts']['durable'] == 1
    assert stream_info(native.store, chunk) is None and native.store.claim() is None
    with Budget().phase() as budget, pytest.raises(Unavailable, match='primary_source_unpublished'):
        native.capture.authority.mapping(chunk, 0., 0, 16000, budget)
    with native.store.db() as db:
        bid = db.execute('SELECT id FROM recording_batches WHERE session_id=?', (native.sid,)).fetchone()[0]
    assert publish(bid, native.capture)
    assert not publish(bid, native.capture)
    assert stream_info(native.store, chunk)['final']
    with Budget().phase() as budget:
        parts = native.capture.authority.mapping(chunk, 0., 0, 16000, budget)
        assert native.capture.authority.original_pcm(parts, budget) == pcm(16000)
    assert native.store.claim()['id'] == cid


@pytest.mark.parametrize('change', ['file', 'map'])
def test_publication_revalidates_files_and_complete_map(fixture, monkeypatch, change):
    store, recording, sid = fixture
    put(recording, sid, 'source', pcm(16000), 0)
    advance = recording.advance
    monkeypatch.setattr(recording, 'advance', lambda *args, **kwargs: False)
    result = finish(recording, sid)
    assert result['state'] == 'pending_receipts'
    if change == 'file':
        with Path(paths(store, sid)[1][0]['path']).open('r+b') as out:
            out.seek(100); out.write(b'xx')
    else:
        with store.db() as db:
            db.execute('DELETE FROM recording_ranges')
    advance(None, force=True)
    result = recording.status(sid, 'capture.one')
    assert result['state'] == 'interrupted' and store.claim() is None
    assert all(row['status'] == 'staging' for row in result['work_chunks'])
    assert len(paths(store, sid)[0]) == 1


def test_two_real_receipts_take_over_two_seconds_without_early_batch_claim(native, monkeypatch):
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    old = native.owner._receipt
    calls = []
    def held(op, budget):
        index = len(calls); calls.append(op.payload.chunk_id)
        entered[index].set()
        assert release[index].wait(1.8)
        return old(op, budget)
    monkeypatch.setattr(native.owner, '_receipt', held)
    started = time.monotonic()
    result = native_batch(native)
    assert entered[0].wait(1)
    stable = payloads(native)
    try:
        for index in range(2):
            if index:
                deadline = time.monotonic() + 2
                while not entered[index].is_set() and time.monotonic() < deadline:
                    native.recording.advance(native.capture, force=True)
                    entered[index].wait(.02)
                assert entered[index].is_set()
            time.sleep(1.05)
            assert native.store.claim() is None
            assert native.recording.status(native.sid, 'capture.one')['state'] == 'pending_receipts'
            assert payloads(native) == stable
            release[index].set()
            operation = native.recording._flight['operation']
            assert operation.slot_released.wait(2)
        result = drain(native)
        assert time.monotonic() - started > 2
        assert result['state'] == 'queued' and result['optional_receipts']['durable'] == 2
        assert calls == [row['chunk_id'] for row in result['work_chunks']]
        assert [row['receipt_attempts'] for row in result['work_chunks']] == [1, 1]
        assert finish(native.recording, native.sid, cells=native.capture) == result
    finally:
        for event in release:event.set()


def test_initial_busy_and_admission_race_keep_exact_observations(native, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    old = native.owner._list
    def held(op, budget):
        entered.set(); assert release.wait(1.8); return old(op, budget)
    monkeypatch.setattr(native.owner, '_list', held)
    busy = native.owner.submit('list', (native.sid, 0))
    assert entered.wait(1)
    try:
        result = native_batch(native)
        stable = payloads(native)
        assert len(stable) == 2 and all(row[1] for row in stable)
        assert result['work_chunks'][0]['native_receipt_status'] == 'owner_busy_pending'
        assert all(row['receipt_attempts'] == 0 for row in result['work_chunks'])
        assert native.store.claim() is None
        for chunk in paths(native.store, native.sid)[1]:
            assert stream_info(native.store, chunk) is None
            with pytest.raises(ValueError, match='not published'):
                context_parts(native.store, chunk, chunk['timeline_start'], chunk['timeline_start'] + chunk['duration'])
    finally:
        release.set(); assert busy.slot_released.wait(3)
    submit = native.owner.submit
    raced = []
    def refused_once(kind, payload):
        if not raced:
            raced.append(payload); return {'status': 'busy', 'reason': 'one_total_handoff_slot'}
        return submit(kind, payload)
    monkeypatch.setattr(native.owner, 'submit', refused_once)
    native.recording.advance(native.capture, force=True)
    assert payloads(native) == stable and native.store.claim() is None
    assert all(stream_info(native.store, chunk) is None for chunk in paths(native.store, native.sid)[1])
    result = drain(native)
    assert result['state'] == 'queued' and result['optional_receipts']['ready']
    assert native.owner.health()['accepted'] == 3  # One list and two receipts.
    assert payloads(native) == stable


@pytest.mark.parametrize('second_committed', [False, True])
def test_pending_shutdown_restart_revalidates_durable_acknowledgement(native, monkeypatch, second_committed):
    entered, release = threading.Event(), threading.Event()
    original = native.owner._receipt
    def held(op, budget):
        entered.set(); assert release.wait(1.8); return original(op, budget)
    monkeypatch.setattr(native.owner, '_receipt', held)
    result = native_batch(native)
    assert entered.wait(1)
    stable = payloads(native)
    operation = native.recording._flight['operation']
    try:
        assert result['state'] == 'pending_receipts'
        assert native.owner.close(0) is False
        assert native.owner.health()['cleanup_pending']
        assert not operation.slot_released.is_set()
        native.recording.advance(native.capture, force=True)
        assert payloads(native) == stable and native.store.claim() is None
    finally:
        release.set(); assert operation.slot_released.wait(3)
        native.owner._thread.join(3)
    assert native.owner._db is None and not native.owner._thread.is_alive()
    assert not native.owner.health()['cleanup_pending']
    native.recording.advance(native.capture, force=True)
    assert payloads(native) == stable and native.store.claim() is None
    native.store.recover()
    native.capture = native.new_owner(); native.owner = native.capture.owner
    native.recording = mod.RecordingOverlap(native.store)
    second_entered, second_release = threading.Event(), threading.Event()
    second_receipt = native.owner._receipt
    second_cid = stable[1][0]
    def hold_second(op, budget):
        if op.payload.chunk_id == second_cid:
            second_entered.set()
            assert second_release.wait(1.8)
        return second_receipt(op, budget)
    monkeypatch.setattr(native.owner, '_receipt', hold_second)
    native.recording.advance(native.capture, force=True)
    flight = native.recording._flight
    assert flight is not None and flight['operation'].slot_released.wait(3)
    native.recording.advance(native.capture, force=True)
    assert native.recording.status(native.sid, 'capture.one')['optional_receipts']['durable'] == 1
    try:
        # If the cooperative visit deadline prevented same-visit submission,
        # perform only bounded fixture pumping until receipt 2 is held.
        deadline = time.monotonic() + 1.
        while not second_entered.is_set() and time.monotonic() < deadline:
            native.recording.advance(native.capture, force=True)
            second_entered.wait(.01)
        assert second_entered.is_set()
        second_operation = native.recording._flight['operation']
        assert not second_operation.slot_released.is_set()
        assert native.owner.receipt(second_cid) is None
        if second_committed:
            second_release.set()
            assert second_operation.slot_released.wait(3)
            assert native.owner.receipt(second_cid) is not None
            assert native.owner.close(.5)
        else:
            assert native.owner.close(0) is False
            second_release.set()
            assert second_operation.slot_released.wait(3)
            native.owner._thread.join(3)
            assert native.owner.receipt(second_cid) is None
            assert native.owner.close(.5)
    finally:
        second_release.set()
    assert native.recording.status(native.sid, 'capture.one')['optional_receipts']['durable'] == 1
    native.store.recover()
    assert native.recording.status(native.sid, 'capture.one')['optional_receipts']['durable'] == 0
    native.capture = native.new_owner(); native.owner = native.capture.owner
    native.recording = mod.RecordingOverlap(native.store)
    result = drain(native)
    assert result['state'] == 'queued' and result['optional_receipts']['ready']
    assert payloads(native) == stable
    assert native.owner.health()['accepted'] == (0 if second_committed else 1)


def test_commit_before_ack_reconciles_exact_durable_row_without_restart(native, monkeypatch):
    from audio_backend.native_owned_cells import Unavailable
    original = native.owner._transaction
    failures = []
    @contextmanager
    def committed_then_unknown(budget):
        with original(budget):
            yield
        if not failures:
            failures.append(True)
            raise Unavailable('cooperative_deadline')
    monkeypatch.setattr(native.owner, '_transaction', committed_then_unknown)
    native_batch(native, 16000)
    operation = native.recording._flight['operation']
    assert operation.slot_released.wait(3)
    cid = payloads(native)[0][0]
    assert native.owner.receipt(cid) is None
    result = drain(native)
    assert result['state'] == 'queued' and result['optional_receipts']['ready']
    assert result['work_chunks'][0]['receipt_attempts'] == 2
    assert native.owner.receipt(cid) is not None and failures == [True]


@pytest.mark.parametrize('action', ['cancel', 'delete', 'revoke'])
def test_pending_cancel_delete_or_revoke_cannot_resurrect_batch(native, monkeypatch, action):
    entered, release = threading.Event(), threading.Event()
    original = native.owner._receipt
    def held(op, budget):
        entered.set(); assert release.wait(1.8); return original(op, budget)
    monkeypatch.setattr(native.owner, '_receipt', held)
    result = native_batch(native)
    assert entered.wait(1)
    stable = payloads(native); originals, work = paths(native.store, native.sid)
    operation = native.recording._flight['operation']
    try:
        if action == 'cancel':
            response = native.client.post(f'/sessions/{native.sid}/recordings/capture.one/cancel')
            assert response.status_code == 200, response.text
            assert response.json()['state'] == 'cancelled'
            assert payloads(native) == stable
        elif action == 'delete':
            response = native.client.delete(f'/sessions/{native.sid}')
            assert response.status_code == 200, response.text
        else:
            native.owner.revoked()
        assert native.store.claim() is None
    finally:
        release.set(); assert operation.slot_released.wait(3)
    native.recording.advance(native.capture, force=True)
    if action == 'cancel':
        result = native.recording.status(native.sid, 'capture.one')
        assert result['state'] == 'cancelled' and all(row['status'] == 'cancelled' for row in result['work_chunks'])
        assert all(Path(row['path']).exists() for row in originals + work)
        assert finish(native.recording, native.sid, cells=native.capture)['state'] == 'cancelled'
    elif action == 'delete':
        assert payloads(native) == [] and all(not Path(row['path']).exists() for row in originals + work)
    else:
        monkeypatch.setattr(native.owner, '_receipt', original)
        assert drain(native)['state'] == 'queued'
    if action != 'revoke':assert native.store.claim() is None


def test_pending_batch_does_not_starve_other_primary_worker_claim(native, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = native.owner._receipt
    def held(op, budget):
        entered.set(); assert release.wait(1.8); return original(op, budget)
    monkeypatch.setattr(native.owner, '_receipt', held)
    native_batch(native)
    assert entered.wait(1)
    try:
        other = native.store.create_session('unrelated ordinary session')['id']
        path = native.root / 'uploads' / 'ordinary.audio'; data = wav_bytes(pcm(16000)); path.write_bytes(data)
        cid, _ = native.store.add_chunk(other, 'ordinary', hashlib.sha256(data).hexdigest(), path, 1., 0.)
        seen = []
        worker = native.app.state.worker
        monkeypatch.setattr(worker, 'process', lambda row, **kw: (seen.append(row['id']), worker.stop_event.set()))
        worker.start(); worker.thread.join(1)
        assert not worker.thread.is_alive() and seen == [cid]
        assert native.recording.status(native.sid, 'capture.one')['state'] == 'pending_receipts'
        assert native.store.claim() is None
    finally:
        release.set()
        operation = native.recording._flight['operation']
        assert operation.slot_released.wait(3)
    monkeypatch.setattr(native.owner, '_receipt', original)
    assert drain(native)['state'] == 'queued'


def test_terminal_capacity_is_explicit_gap_and_retains_payload(native, monkeypatch):
    from audio_backend.native_owned_cells import Unavailable
    def full(op, budget):raise Unavailable('source_receipt_capacity')
    monkeypatch.setattr(native.owner, '_receipt', full)
    native_batch(native, 16000)
    stable = payloads(native)
    result = drain(native)
    assert result['state'] == 'queued' and result['optional_receipts']['gaps'] == 1
    assert not result['optional_receipts']['ready'] and payloads(native) == stable
    assert result['blockers'][0]['reason'] == 'source_receipt_capacity'


def test_conflicting_durable_receipt_is_rejected_inside_owner(native, monkeypatch):
    # Deliberately permissive TEST authority isolates durable same-ID conflict
    # handling, after a genuine original observation has already been retained.
    from audio_backend import native_owned_cell_store as cell_module
    native_batch(native, 16000); result = drain(native)
    cid = result['work_chunks'][0]['chunk_id']; receipt = native.owner.receipt(cid)
    original = native.owner._receipt
    def forget_cache(op, budget):
        native.owner._receipts.pop(cid, None)
        return original(op, budget)
    monkeypatch.setattr(native.owner, '_receipt', forget_cache)
    monkeypatch.setattr(native.capture.authority, 'receipt_valid', lambda receipt, budget: True)
    operation = native.owner.submit('receipt', replace(receipt, digest='a' * 64))
    assert type(operation) is cell_module.Operation and operation.slot_released.wait(3)
    assert operation.result['reason'] == 'conflicting_ingestion_receipt'
    monkeypatch.setattr(native.owner, '_receipt', original)
    operation = native.owner.submit('receipt', receipt)
    assert operation.slot_released.wait(3)
    assert operation.result['status'] == 'existing_receipt' and native.owner.receipt(cid) == receipt


def test_interruption_reporting_failure_cannot_escape_worker_maintenance(fixture, monkeypatch):
    store, recording, sid = fixture
    put(recording, sid, 'source', pcm(100), 0)
    advance = recording.advance
    monkeypatch.setattr(recording, 'advance', lambda *a, **kw: False)
    finish(recording, sid)
    with store.db() as db:db.execute("UPDATE recording_batches SET manifest='{}'")
    def unavailable(*args):raise RuntimeError('contended error reporting')
    monkeypatch.setattr(recording, '_interrupt_pending', unavailable)
    assert advance(None, force=True) is False
    assert store.claim() is None


def test_revocation_during_acknowledgement_never_marks_durable(native, monkeypatch):
    native_batch(native, 16000)
    operation = native.recording._flight['operation']
    assert operation.slot_released.wait(3)
    original = native.capture.authority.receipt_valid
    revoked = []
    def revoke_after_valid(receipt, budget):
        answer = original(receipt, budget)
        if threading.current_thread() is threading.main_thread() and not revoked:
            revoked.append(True); native.owner.revoked()
        return answer
    monkeypatch.setattr(native.capture.authority, 'receipt_valid', revoke_after_valid)
    native.recording.advance(native.capture, force=True)
    result = native.recording.status(native.sid, 'capture.one')
    assert revoked and result['optional_receipts']['durable'] == 0
    assert result['state'] == 'pending_receipts' and native.store.claim() is None
    monkeypatch.setattr(native.capture.authority, 'receipt_valid', original)
    assert drain(native)['state'] == 'queued'


def test_reconciled_receipt_rechecks_generation_before_cache_install(native, monkeypatch):
    from audio_backend.native_owned_cells import Unavailable
    native_batch(native, 16000); result = drain(native)
    cid = result['work_chunks'][0]['chunk_id']; receipt = native.owner.receipt(cid)
    original_receipt = native.owner._receipt
    original_valid = native.capture.authority.receipt_valid
    calls = []
    def forget(op, budget):
        native.owner._receipts.pop(cid, None)
        return original_receipt(op, budget)
    def revoke_second(observed, budget):
        answer = original_valid(observed, budget)
        if threading.current_thread() is native.owner._thread:
            calls.append(True)
            if len(calls) == 2:native.owner.revoked()
        return answer
    monkeypatch.setattr(native.owner, '_receipt', forget)
    monkeypatch.setattr(native.capture.authority, 'receipt_valid', revoke_second)
    operation = native.owner.submit('receipt', receipt)
    assert operation.slot_released.wait(3)
    assert operation.result['reason'] == 'publication_generation_revoked'
    assert native.owner.receipt(cid) is None
    monkeypatch.setattr(native.owner, '_receipt', original_receipt)
    monkeypatch.setattr(native.capture.authority, 'receipt_valid', original_valid)


def test_owner_deadline_keeps_payload_pending_until_exact_retry(native, monkeypatch):
    original = native.owner._receipt
    calls = []
    def slow_once(op, budget):
        calls.append(op.payload.receipt_id)
        if len(calls) == 1:
            time.sleep(2.05)
            budget.check()  # Real unchanged owner deadline, not a fake result.
        return original(op, budget)
    monkeypatch.setattr(native.owner, '_receipt', slow_once)
    native_batch(native, 16000)
    stable = payloads(native)
    operation = native.recording._flight['operation']
    assert operation.slot_released.wait(3)
    assert operation.result['reason'] == 'cooperative_deadline'
    assert native.store.claim() is None and payloads(native) == stable
    assert stream_info(native.store, paths(native.store, native.sid)[1][0]) is None
    native.recording.advance(native.capture, force=True)
    assert native.recording.status(native.sid, 'capture.one')['state'] == 'pending_receipts'
    result = drain(native)
    assert result['state'] == 'queued' and result['optional_receipts']['ready']
    assert calls == [stable[0][2], stable[0][2]] and payloads(native) == stable


def test_public_status_uses_one_snapshot_across_queue_transition(fixture, monkeypatch):
    store, recording, sid = fixture
    put(recording, sid, 'source', pcm(100), 0)
    advance = recording.advance
    monkeypatch.setattr(recording, 'advance', lambda *a, **kw: False)
    finish(recording, sid)
    original_batch = recording._batch
    published = []
    def batch_then_publish(db, session, capture):
        batch = original_batch(db, session, capture)
        thread = threading.Thread(target=lambda: published.append(advance(None, force=True)))
        thread.start(); thread.join(3)
        assert not thread.is_alive() and published == [True]
        return batch
    monkeypatch.setattr(recording, '_batch', batch_then_publish)
    snapshot = recording.status(sid, 'capture.one')
    assert snapshot['state'] == 'pending_receipts'
    assert {row['status'] for row in snapshot['work_chunks']} == {'staging'}
    monkeypatch.setattr(recording, '_batch', original_batch)
    current = recording.status(sid, 'capture.one')
    assert current['state'] == 'queued' and {row['status'] for row in current['work_chunks']} == {'queued'}


def probe_authority(tmp_path):
    """Tiny real primary SQLite file; no optional database or models."""
    import sqlite3
    from audio_backend import native_owned_cell_capture as cap
    path = tmp_path / 'authority.sqlite3'
    db = sqlite3.connect(path)
    try:
        db.execute('CREATE TABLE probe(value INTEGER NOT NULL)')
        db.execute('INSERT INTO probe VALUES(1)'); db.commit()
    finally:db.close()
    return cap.PrimaryAuthority(SimpleNamespace(path=path, settings=SimpleNamespace(data_dir=tmp_path)))


def test_exact_recovery_code_replays_complete_closed_read_snapshot(tmp_path, monkeypatch):
    # Exact-code injection, NOT a reproduction of a real WAL recovery race.
    import sqlite3
    from audio_backend import native_owned_cell_capture as cap
    from audio_backend.native_owned_cell_store import Budget
    authority = probe_authority(tmp_path); budget = Budget(); connections = []; waits = []
    failure = sqlite3.OperationalError('fixture recovery contention')
    failure.sqlite_errorcode = sqlite3.SQLITE_BUSY_RECOVERY
    failure.sqlite_errorname = 'SQLITE_BUSY_RECOVERY'
    def read(db):
        connections.append(db)
        assert db.in_transaction and db.execute('PRAGMA query_only').fetchone()[0] == 1
        assert db.execute('PRAGMA busy_timeout').fetchone()[0] == 0
        value = db.execute('SELECT value FROM probe').fetchone()[0]
        budget.projection(7)
        if len(connections) == 1:raise failure
        return value
    def backoff(seconds):
        waits.append(seconds)
        with pytest.raises(sqlite3.ProgrammingError):connections[-1].execute('SELECT 1')
        db = sqlite3.connect(authority.path)
        try:db.execute('UPDATE probe SET value=2'); db.commit()
        finally:db.close()
    monkeypatch.setattr(cap.time, 'sleep', backoff)
    assert authority._read(budget, 'source_row', read) == 2
    assert len(connections) == 2 and connections[0] is not connections[1]
    assert waits == [.005] and budget.sql_bytes == 14
    with pytest.raises(sqlite3.ProgrammingError):connections[-1].execute('SELECT 1')


def test_real_sqlite_busy_contention_closes_then_recovers(tmp_path, monkeypatch):
    # Real SQLITE_BUSY in rollback-journal mode, not a claimed RECOVERY reproduction.
    import sqlite3
    from audio_backend import native_owned_cell_capture as cap
    from audio_backend.native_owned_cell_store import Budget
    authority = probe_authority(tmp_path); connections = []; codes = []; waits = []
    writer = sqlite3.connect(authority.path, timeout=0)
    writer.execute('BEGIN EXCLUSIVE'); writer.execute('UPDATE probe SET value=3')
    def read(db):
        connections.append(db)
        try:return db.execute('SELECT value FROM probe').fetchone()[0]
        except sqlite3.OperationalError as exc:
            codes.append(exc.sqlite_errorcode); raise
    def backoff(seconds):
        waits.append(seconds)
        with pytest.raises(sqlite3.ProgrammingError):connections[-1].execute('SELECT 1')
        writer.commit()
    monkeypatch.setattr(cap.time, 'sleep', backoff)
    try:
        assert authority._read(Budget(), 'mapping', read) == 3
    finally:writer.close()
    assert codes == [sqlite3.SQLITE_BUSY] and waits == [.005] and len(connections) == 2
    with pytest.raises(sqlite3.ProgrammingError):connections[-1].execute('SELECT 1')


def test_persistent_primary_busy_exhausts_three_reads_and_diagnoses_phase(tmp_path, monkeypatch):
    import sqlite3
    from audio_backend import native_owned_cell_capture as cap
    from audio_backend.native_owned_cell_store import Budget
    authority = probe_authority(tmp_path); connections = []; errors = []; diagnostics = []
    writer = sqlite3.connect(authority.path, timeout=0); writer.execute('BEGIN EXCLUSIVE')
    def read(db):
        connections.append(db)
        try:return db.execute('SELECT value FROM probe').fetchone()[0]
        except sqlite3.OperationalError as exc:errors.append(exc); raise
    monkeypatch.setattr(cap, '_diagnostic', lambda fmt, *args: diagnostics.append(fmt % args))
    try:
        with pytest.raises(sqlite3.OperationalError) as caught:
            authority._read(Budget(), 'cell_commit', read)
        assert caught.value is errors[-1]
    finally:writer.rollback(); writer.close()
    assert len(connections) == len(errors) == 3
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError):db.execute('SELECT 1')
    assert len(diagnostics) == 1 and 'phase=cell_commit attempt=3' in diagnostics[0]
    assert 'sqlite_code=5 sqlite_name=SQLITE_BUSY' in diagnostics[0]
    assert str(tmp_path) not in diagnostics[0] and 'SELECT' not in diagnostics[0]


@pytest.mark.parametrize('code', [6, 517])
def test_locked_snapshot_and_malformed_codes_never_retry(tmp_path, monkeypatch, code):
    import sqlite3
    from audio_backend import native_owned_cell_capture as cap
    from audio_backend.native_owned_cell_store import Budget
    authority = probe_authority(tmp_path); waits = []
    monkeypatch.setattr(cap.time, 'sleep', lambda seconds: waits.append(seconds))
    for current in (code, None, '5', True):
        failure = sqlite3.OperationalError('fixture nonretryable'); calls = []
        if current is not None:failure.sqlite_errorcode = current
        def read(db):calls.append(db); raise failure
        with pytest.raises(sqlite3.OperationalError) as caught:
            authority._read(Budget(), 'seam_commit', read)
        assert caught.value is failure and len(calls) == 1 and waits == []
        with pytest.raises(sqlite3.ProgrammingError):calls[0].execute('SELECT 1')


def test_read_retry_backoff_consumes_original_deadline_and_counters(tmp_path):
    import sqlite3
    from audio_backend import native_owned_cell_capture as cap
    from audio_backend.native_owned_cell_store import Budget
    from audio_backend.native_owned_cells import Unavailable
    authority = probe_authority(tmp_path); budget = Budget(); calls = []; deadlines = []
    failure = sqlite3.OperationalError('fixture recovery contention')
    failure.sqlite_errorcode = sqlite3.SQLITE_BUSY_RECOVERY
    def read(db):
        calls.append(db); budget.projection(11)
        budget.deadline = time.monotonic() + .001
        deadlines.append(budget.deadline)
        raise failure
    with pytest.raises(Unavailable, match='cooperative_deadline'):
        authority._read(budget, 'seam_adjacent', read)
    assert len(calls) == 1 and budget.sql_bytes == 11 and budget.deadline == deadlines[0]
    assert budget.elapsed >= .001
    with pytest.raises(sqlite3.ProgrammingError):calls[0].execute('SELECT 1')
    # Authority failure is also never retried or converted into an empty result.
    calls.clear(); invalid = Unavailable('primary_database_replaced')
    def revoked(db):calls.append(db); raise invalid
    with pytest.raises(Unavailable) as caught:authority._read(Budget(), 'session', revoked)
    assert caught.value is invalid and len(calls) == 1
