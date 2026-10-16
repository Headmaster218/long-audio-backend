"""One logical hour of real streamed PCM, fake analyzer and real primary commits."""
import hashlib
import wave

from audio_backend.worker import Worker
from audio_backend.boundary_store import context_parts
from test_recording_pages import fixture, create, seal, CAPTURE, Q, RATE, pcm, mod


def test_bounded_one_hour_pages_real_pcm_fake_analyzer(fixture, monkeypatch):
    store, recording, sid = fixture
    origin = 100003
    expected = hashlib.sha256()
    analyzer_calls = []
    class Analyzer:
        def __init__(self, config): pass
        def close(self): pass
        def analyze(self, path, **kwargs):
            with wave.open(path, 'rb') as stream:
                frames = stream.getnframes()
            analyzer_calls.append(frames)
            # Explicitly synthetic output exercises the normal commit path.
            return {'segments': [{'start': 0., 'end': frames / RATE,
                                  'text': 'synthetic hour fixture'}],
                    'events': [], 'speaker_turns': [], 'metadata': {}}
    def extract(worker, chunk, start, end, wav, tmp, stop, **kwargs):
        with wave.open(str(wav), 'wb') as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(RATE)
            for part in context_parts(worker.store, chunk, start, end):
                with wave.open(part['path'], 'rb') as source:
                    source.setpos(round(part['read_start'] * RATE))
                    remaining = round(part['read_duration'] * RATE)
                    while remaining:
                        count = min(32768, remaining)
                        block = source.readframes(count)
                        assert len(block) == count * 2
                        output.writeframesraw(block); remaining -= count
    monkeypatch.setattr(Worker, 'extract_pcm', extract)
    worker = Worker(store, store.settings, Analyzer)
    position = 0; page_ids = []; revisions = []
    for page_number, seconds in enumerate([294] * 12 + [72], 1):
        page = create(recording, sid, page_number, origin if page_number == 1 else None)
        page_end = position + seconds * RATE
        source_number = 0
        while position < page_end:
            frames = min(120 * RATE, page_end - position)
            with store.db() as db:
                batch = recording._batch(db, sid, CAPTURE)
            path = recording.source_path(batch, page)
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(RATE)
                written = 0
                while written < frames:
                    count = min(32768, frames - written)
                    block = pcm(count, position + written)
                    expected.update(block); output.writeframesraw(block); written += count
            recording.accept_page(sid, CAPTURE, page_number,
                f'page.{page_number}.source.{source_number}', path, origin + position)
            position += frames; source_number += 1
        result = seal(recording, sid, page_number, origin + page_end, eos=page_number == 13)
        assert result['state'] == 'published'
        page_ids.append([row['chunk_id'] for row in result['work_chunks']])
        revisions.append(result['revision'])
        while (chunk := store.claim()) is not None:
            worker.process(chunk)
        with store.db() as db:
            assert not db.execute("SELECT 1 FROM chunks WHERE status IN ('failed','paused','staging','running')").fetchone()
    assert position == 57_600_000
    actual = hashlib.sha256()
    with store.db() as db:
        work = [dict(r) for r in db.execute('''SELECT c.*,w.frames,w.position FROM recording_work w
            JOIN chunks c ON c.id=w.chunk_id ORDER BY w.position''')]
        before = [tuple(r) for r in db.execute('SELECT * FROM segments ORDER BY id')]
    assert len(work) == 343 and sum(r['frames'] for r in work) == 57_600_000
    assert [r['position'] for r in work] == list(range(343))
    assert all(r['status'] == 'completed' and r['next_offset'] == r['duration'] for r in work)
    assert before
    for row in work:
        with wave.open(row['path'], 'rb') as source:
            while block := source.readframes(32768): actual.update(block)
    assert actual.digest() == expected.digest()
    committed_calls = len(analyzer_calls)
    store.recover(); replay = mod.RecordingOverlap(store)
    for number, seconds in enumerate([294] * 12 + [72], 1):
        end = origin + min(number * 294, 3600) * RATE
        result = replay.seal_page(sid, CAPTURE, number, f'seal.{number}', revisions[number-1], end, eos=number==13)
        assert [r['chunk_id'] for r in result['work_chunks']] == page_ids[number-1]
    assert store.claim() is None
    with store.db() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM segments ORDER BY id')] == before
        assert db.execute('SELECT COUNT(DISTINCT chunk_id) FROM recording_work').fetchone()[0] == 343
        assert db.execute('SELECT COUNT(*) FROM stream_chunks WHERE stream_final=1').fetchone()[0] == 1
    assert len(analyzer_calls) == committed_calls
    assert replay.status(sid, CAPTURE)['capture_end_frame'] == origin + 57_600_000
    assert replay.status(sid, CAPTURE)['eos']
    assert worker.stop()
