"""Finalized recording batch fixtures: real storage/API/worker, no loaded models.

Run with ordinary pytest after source review. Synthetic analyzer/native fixtures
are explicit; they establish routing/lineage, not acoustic or MOSS quality.
"""
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
from audio_backend.boundary_store import context_parts
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


def test_real_process_death_after_stage_never_dispatches_on_restart(fixture):
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
class Token:
    def close(self):pass
class Cells:
    def streamed_descriptor(self,*args):return object()
    def prepare_upload(self,*args):return Token()
    def finish_upload(self,*args):os._exit(73)
RecordingOverlap(store).finish(sys.argv[2],'capture.one','finish',1,Cells())
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
        assert current['state'] == 'interrupted'
        assert {row['chunk_id'] for row in current['work_chunks']} == {row['id'] for row in work}
        assert all(Path(row['path']).exists() for row in originals + work)
        retry = client.post(f'/sessions/{sid}/recordings/capture.one/finish',
                            json={'expected_revision': 1, 'idempotency_key': 'finish'})
        assert retry.status_code == 202 and retry.json()['state'] == 'interrupted'
        assert client.app.state.store.claim() is None
        assert client.post(f"/chunks/{work[0]['id']}/resume").status_code == 409
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
    assert calls == ['descriptor', 'prepare', 'finish']
    assert result['state'] == 'queued'
    assert result['work_chunks'][0]['native_receipt_status'] == 'receipt_unavailable'
    assert store.claim() is not None


def test_actual_native_cell_ingestion_hook_preserves_descriptor_contract(tmp_path):
    # Existing original-style fixture qualification; no production qualification
    # override and no native model is loaded by this test.
    from audio_backend import native_owned_cell_capture as capture_module
    from audio_backend import native_owned_cell_store as cell_module
    from test_native_owned_cell_store import simulated_profile
    cfg = settings(tmp_path, diarization_backend='nemotron',
                   native_diarization_model='fixture-model', native_diarization_library='fixture-library')
    app = create_app(cfg)
    with TestClient(app) as client:
        sid = client.post('/sessions', json={}).json()['id']
        cfg.native_owned_cells_enabled = True
        authority = capture_module.PrimaryAuthority(app.state.store)
        owner = cell_module.CellStore(tmp_path / 'native-owned-cells', authority,
            enabled=True, qualification=simulated_profile(tmp_path), instance_lock=app.state.instance_lock)
        authority.owner = owner
        capture = capture_module.Capture(owner, authority)
        app.state.worker.native_owned_cells = capture
        capture_module._REGISTRY[str(authority.path)] = capture
        owner.start()
        assert owner._ready.wait(3), owner.health()
        try:
            response = client.put(f'/sessions/{sid}/recordings/native/sources?source_start_frame=19',
                content=wav_bytes(pcm(16000)), headers={'Idempotency-Key': 'original'})
            assert response.status_code == 202, response.text
            result = client.post(f'/sessions/{sid}/recordings/native/finish',
                json={'expected_revision': 1, 'idempotency_key': 'finish'})
            assert result.status_code == 202, result.text
            chunk = result.json()['work_chunks'][0]
            operation = owner._slot
            if operation is not None:
                assert operation.done.wait(3)
            receipt = owner.receipt(chunk['chunk_id'])
            assert receipt is not None, (capture.last_reason, owner.health())
            assert receipt.digest == chunk['digest']
            assert receipt.frames == 16000 and receipt.timeline_start == 0
            _, work = paths(app.state.store, sid)
            assert receipt.signature == capture_module.signature(Path(work[0]['path']).stat())
            assert capture.duplicate(chunk['chunk_id'], chunk['digest'])['status'] == 'existing_receipt'
        finally:
            # The existing app shutdown unregisters/closes the injected owner.
            pass
    capture_module._REGISTRY.pop(str(authority.path), None)
    assert owner._thread is None or not owner._thread.is_alive()
    assert owner._db is None
    assert not owner.health()['cleanup_pending']


@pytest.mark.parametrize('change', ['file', 'map'])
def test_publication_revalidates_files_and_complete_map(fixture, change):
    store, recording, sid = fixture
    put(recording, sid, 'source', pcm(16000), 0)
    class Token:
        def close(self):pass
    class MutatingFixture:
        def streamed_descriptor(self, *args):return object()
        def prepare_upload(self, *args):return Token()
        def finish_upload(self, token, session, cid, path, created):
            if change == 'file':
                with path.open('r+b') as out:
                    out.seek(100); out.write(b'xx')
            else:
                with store.db() as db:
                    db.execute('DELETE FROM recording_ranges WHERE chunk_id=?', (cid,))
            return {'status': 'receipt_unavailable'}
    with pytest.raises(ValueError, match='changed'):
        finish(recording, sid, cells=MutatingFixture())
    result = recording.status(sid, 'capture.one')
    assert result['state'] == 'interrupted' and store.claim() is None
    assert all(row['status'] == 'staging' for row in result['work_chunks'])
    assert len(paths(store, sid)[0]) == 1
