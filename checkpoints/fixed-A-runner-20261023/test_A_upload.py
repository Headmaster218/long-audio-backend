"""Finite A geometry/API projections, no model or actual recording service."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import stat

import pytest

import a_input as geometry
from a_uploader_cli import collect


@pytest.mark.parametrize('body_fails', [False, True])
def test_input_cleanup_attempts_both_and_preserves_original(body_fails):
    source = geometry.AInput('/unused', 0)
    events = []
    def close_wav(): events.append('wav'); raise OSError('wav close')
    def close_stream(): events.append('stream'); raise RuntimeError('stream close')
    source.wav = SimpleNamespace(close=close_wav)
    source.stream = SimpleNamespace(close=close_stream)
    original = ValueError('original') if body_fails else None
    if body_fails:
        source.__exit__(ValueError, original, None)
        assert 'OSError' in original.__notes__[0]
    else:
        with pytest.raises(OSError): source.__exit__(None, None, None)
    assert events == ['wav', 'stream']


def test_input_enter_preserves_read_error_when_stream_close_also_fails(monkeypatch):
    original = RuntimeError('hash read')
    def read(*args): raise original
    def close(): raise OSError('close')
    monkeypatch.setattr(geometry.os, 'open', lambda *a: 42)
    monkeypatch.setattr(geometry.os, 'fdopen', lambda *a: SimpleNamespace(read=read, close=close))
    monkeypatch.setattr(geometry.os, 'fstat', lambda fd: SimpleNamespace(
        st_mode=stat.S_IFREG, st_size=geometry.BYTES, st_dev=1, st_ino=2, st_mtime_ns=3))
    with pytest.raises(RuntimeError) as caught:
        geometry.AInput('/fake', float('inf')).__enter__()
    assert caught.value is original and 'OSError' in original.__notes__[0]


def receipts():
    return {s['index']: dict(bytes=44+2*s['frames'], sha256='a'*64)
            for p in geometry.plan() for s in p['sources']}


def page_projection(p, records, status='completed'):
    sources = [dict(id='source'+str(s['index']), frames=s['frames'], bytes=records[s['index']]['bytes'],
        digest=records[s['index']]['sha256'], source_start_frame=geometry.ORIGIN+s['offset']) for s in p['sources']]
    chunks = [dict(chunk_id='chunk'+str(c['position']), position=c['position'], frames=c['frames'],
        capture_start_frame=geometry.ORIGIN+c['offset'], timeline_start=c['offset']/16000,
        duration=c['frames']/16000, status=status) for c in p['chunks']]
    ranges = []
    for source, s in zip(sources, p['sources']):
        for chunk, c in zip(chunks, p['chunks']):
            start, end = max(s['offset'], c['offset']), min(s['offset']+s['frames'], c['offset']+c['frames'])
            if end > start:
                ranges.append(dict(source_id=source['id'], chunk_id=chunk['chunk_id'],
                    source_start_frame=start-s['offset'], capture_start_frame=geometry.ORIGIN+start,
                    work_start_frame=start-c['offset'], frames=end-start, relation='owner'))
    return dict(page_number=p['number'], state='published', released=True,
        start_frame=geometry.ORIGIN+p['offset'], end_frame=geometry.ORIGIN+p['offset']+p['frames'],
        eos=p['number'] == 3, capture_origin_frame=geometry.ORIGIN, sources=sources,
        work_chunks=chunks, ranges=ranges)


def final_projection(records):
    pages = [page_projection(p, records) for p in geometry.plan()]
    chunks = {'chunks': [dict(id=c['chunk_id'], status=c['status'], duration=c['duration'],
        timeline_start=c['timeline_start'], next_offset=c['duration']) for p in pages for c in p['work_chunks']]}
    capture = dict(eos=True, page_count=3, released_pages=3, pending_page=None,
                   capture_origin_frame=100003, capture_end_frame=11620003)
    return pages, chunks, capture


def test_fixed_independent_geometry_constants():
    pages = geometry.plan()
    assert [p['frames'] for p in pages] == [294*16000, 294*16000, 132*16000]
    assert [len(p['sources']) for p in pages] == [3, 3, 2]
    assert [len(p['chunks']) for p in pages] == [28, 28, 13]
    chunks = [c for p in pages for c in p['chunks']]
    assert [c['position'] for c in chunks] == list(range(69))
    assert [c['offset'] for c in chunks] == [i*168000 for i in range(69)]
    assert [c['frames'] for c in chunks] == [168000]*68+[96000]
    assert sum(c['frames'] for c in chunks) == 11520000
    assert geometry.START == 13440000 and geometry.START+geometry.FRAMES == 24960000
    assert geometry.ORIGIN+geometry.FRAMES == 11620003
    assert max(44+s['frames']*2 for p in pages for s in p['sources']) == 3840044


def test_waiting_input_page_can_advance_but_not_finish():
    p = geometry.plan()[0]
    value = page_projection(p, receipts(), 'waiting_input')
    assert geometry.validate_page(value, p, receipts()) is True
    assert geometry.validate_page(value, p, receipts(), require_completed=True) is False


@pytest.mark.parametrize('fault', ['range_gap', 'range_duplicate', 'source', 'offset', 'extra_chunk', 'failed'])
def test_final_requires_exact_frames_and_all_69_offsets(fault):
    records = receipts(); pages, chunks, capture = final_projection(records)
    assert geometry.validate_final(pages, chunks, capture, records)
    if fault == 'range_gap': pages[0]['ranges'][0]['frames'] -= 1
    if fault == 'range_duplicate': pages[0]['ranges'].append(dict(pages[0]['ranges'][0]))
    if fault == 'source': pages[1]['sources'][0]['digest'] = 'b'*64
    if fault == 'offset': chunks['chunks'][68]['next_offset'] -= .001
    if fault == 'extra_chunk': chunks['chunks'].append(dict(chunks['chunks'][0]))
    if fault == 'failed': pages[0]['work_chunks'][0]['status'] = 'failed'
    with pytest.raises((ValueError, RuntimeError)):
        geometry.validate_final(pages, chunks, capture, records)


def test_three_page_api_progression_does_not_wait_for_non_eos_completion():
    records, calls, created = {}, [], []
    pieces = [s for p in geometry.plan() for s in p['sources']]
    def piece(s):
        data = b'0'*(44+s['frames']*2)
        records[s['index']] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        return data
    def call(method, route, path, **kwargs):
        calls.append((method, route, path))
        if path == '/sessions': return {'id': 'a'*32}
        if method == 'POST' and path.endswith('/pages'):
            number = json.loads(kwargs['body'])['page_number']; created.append(number); return {}
        if method == 'PUT':
            number = int(path.split('/pages/')[1].split('/')[0])
            index = int(kwargs['headers']['Idempotency-Key'].split('-')[-1])
            revision = [s['index'] for s in geometry.plan()[number-1]['sources']].index(index)+1
            return {'revision': revision, 'duplicate': False}
        if method == 'POST': return {'state': 'pending_receipts'}
        if '/pages/' in path:
            number = int(path.rsplit('/', 1)[1])
            return page_projection(geometry.plan()[number-1], records,
                                   'waiting_input' if len(created) < 3 else 'completed')
        pages, chunks, capture = final_projection(records)
        return chunks if '/chunks?' in path else capture
    result = collect(call, piece, deadline=10, clock=lambda: 0,
                     sleep=lambda seconds: (_ for _ in ()).throw(AssertionError('must advance immediately')))
    assert created == [1, 2, 3] and result['completed_chunks'] == 69
    assert sum(method == 'PUT' for method, _, _ in calls) == 8
    assert sum(method == 'POST' for method, _, _ in calls) == 7
    assert result['vector_success'] == 'UNKNOWN'


def test_A_settings_only_changes_fresh_output():
    root = Path(__file__).parent
    a = json.loads((root/'settings.A.json').read_bytes())
    short = json.loads((root/'settings.short.json').read_bytes())
    assert a.pop('data_dir') != short.pop('data_dir')
    assert a == short


@pytest.mark.parametrize('failed_route', ['create_source', 'source_bytes', 'seal_page'])
def test_A_does_not_retry_uncertain_mutations(failed_route):
    calls = []
    def call(method, route, path, **kwargs):
        calls.append(route)
        if route == failed_route: raise TimeoutError('uncertain mutation')
        if route == 'create_recording': return {'id': 'a'*32}
        if route == 'source_bytes':
            return {'revision': calls.count('source_bytes'), 'duplicate': False}
        return {}
    with pytest.raises(TimeoutError):
        collect(call, lambda s: b'0'*(44+s['frames']*2), deadline=10, clock=lambda: 0)
    assert calls.count(failed_route) == 1


def test_continuous_pending_page_stops_at_original_deadline():
    now, writes = [0.0], []
    def call(method, route, path, **kwargs):
        if method != 'GET': writes.append(route)
        if route == 'create_recording': return {'id': 'a'*32}
        if route == 'source_bytes': return {'revision': writes.count('source_bytes'), 'duplicate': False}
        return {'state': 'pending_receipts', 'released': False}
    with pytest.raises(TimeoutError):
        collect(call, lambda s: b'0'*(44+s['frames']*2), deadline=2, clock=lambda: now[0],
                sleep=lambda seconds: now.__setitem__(0, now[0]+seconds))
    assert now[0] == 2 and writes.count('create_source') == 1
