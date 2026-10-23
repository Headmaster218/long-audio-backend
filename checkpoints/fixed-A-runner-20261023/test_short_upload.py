"""Finite synthetic API projections, no service/model/audio inference."""
import copy
import json
from pathlib import Path

import pytest

from short_input import FRAMES, RATE, ORIGIN, END, BYTES, SHA256, validate_completed, load_input
from uploader_cli import collect, run_owned
from types import SimpleNamespace


@pytest.mark.parametrize('body_fails', [True, False])
def test_uploader_preserves_first_error_and_fails_on_cleanup(body_fails):
    original = RuntimeError('body')
    def action():
        if body_fails: raise original
        return 'result'
    def release(): raise OSError('release')
    with pytest.raises(Exception) as caught:
        run_owned(SimpleNamespace(verify_inherited=lambda: None, release=release), action)
    if body_fails:
        assert caught.value is original
        assert 'OSError' in original.__notes__[0]
    else:
        assert isinstance(caught.value, OSError)


def projection(status='completed'):
    source = dict(id='source', frames=FRAMES, bytes=BYTES, digest=SHA256, source_start_frame=ORIGIN)
    chunk = dict(chunk_id='chunk', frames=FRAMES, capture_start_frame=ORIGIN,
                 timeline_start=0, duration=FRAMES/RATE, position=0, status=status)
    page = dict(page_number=1, state='published', released=True, eos=True,
        start_frame=ORIGIN, end_frame=END, capture_origin_frame=ORIGIN, capture_end_frame=END,
        sources=[source], work_chunks=[chunk], ranges=[dict(source_id='source', chunk_id='chunk',
        source_start_frame=0, capture_start_frame=ORIGIN, work_start_frame=0, frames=FRAMES, relation='owner')])
    chunks = {'chunks': [dict(id='chunk', status=status, duration=FRAMES/RATE,
                             timeline_start=0, next_offset=FRAMES/RATE if status == 'completed' else 0)]}
    capture = dict(eos=True, page_count=1, released_pages=1, pending_page=None,
                   capture_origin_frame=ORIGIN, capture_end_frame=END)
    return page, chunks, capture


def test_queued_eos_is_not_processed_completion():
    assert validate_completed(*projection('queued')) is False
    assert validate_completed(*projection('completed')) is True
    with pytest.raises(RuntimeError): validate_completed(*projection('failed'))


@pytest.mark.parametrize('fault', ['range', 'offset', 'extra_chunk', 'source_hash', 'origin', 'capture'])
def test_exact_frame_and_count_gates(fault):
    page, chunks, capture = projection()
    if fault == 'range': page['ranges'][0]['frames'] -= 1
    if fault == 'offset': chunks['chunks'][0]['next_offset'] -= .001
    if fault == 'extra_chunk': chunks['chunks'].append(copy.deepcopy(chunks['chunks'][0]))
    if fault == 'source_hash': page['sources'][0]['digest'] = '0'*64
    if fault == 'origin': page['capture_origin_frame'] += 1
    if fault == 'capture': capture['page_count'] = 2
    with pytest.raises(ValueError): validate_completed(page, chunks, capture)


def test_mutations_once_then_read_until_processed():
    calls, now = [], [0.0]
    page_reads = [0]
    def call(method, route, path, **kwargs):
        calls.append((method, route, path, kwargs))
        if method == 'POST' and path == '/sessions': return {'id': 'a'*32}
        if method == 'POST' and path.endswith('/pages'): return {'revision': 0}
        if method == 'PUT': return {'revision': 1, 'duplicate': False}
        if method == 'POST' and path.endswith('/seal'): return {'state': 'pending_receipts'}
        if path.endswith('/pages/1'):
            page_reads[0] += 1
            return projection('queued' if page_reads[0] == 1 else 'completed')[0]
        if '/chunks?' in path: return projection('queued' if page_reads[0] == 1 else 'completed')[1]
        return projection()[2]
    result = collect(call, b'bounded fake WAV', deadline=5, clock=lambda: now[0],
                     sleep=lambda seconds: now.__setitem__(0, now[0]+seconds))
    assert result['completed_chunks'] == 1 and result['vector_success'] == 'UNKNOWN'
    assert [item[0] for item in calls].count('POST') == 3
    assert [item[0] for item in calls].count('PUT') == 1
    assert page_reads == [2]
    seal = next(item for item in calls if item[2].endswith('/seal'))
    assert json.loads(seal[3]['body']) == dict(idempotency_key='short-seal-1', expected_revision=1,
                                            end_frame=END, eos=True)


@pytest.mark.parametrize('failed_route', ['create_recording', 'create_source', 'source_bytes', 'seal_page'])
def test_failed_mutation_is_never_reissued(failed_route):
    calls = []
    def call(method, route, path, **kwargs):
        calls.append(route)
        if route == failed_route: raise TimeoutError('uncertain mutation')
        if route == 'create_recording': return {'id': 'a'*32}
        if route == 'source_bytes': return {'revision': 1, 'duplicate': False}
        return {}
    with pytest.raises(TimeoutError):
        collect(call, b'fake', deadline=1, clock=lambda: 0)
    assert calls.count(failed_route) == 1


def test_changed_input_rejected_without_upload(tmp_path):
    path = tmp_path/'en.wav'; path.write_bytes(b'not official')
    with pytest.raises(ValueError): load_input(path)


def test_short_settings_only_changes_fresh_data_dir():
    root = Path(__file__).parent
    short = json.loads((root/'settings.short.json').read_bytes())
    old = json.loads((root/'settings.lifecycle.json').read_bytes())
    assert short.pop('data_dir') != old.pop('data_dir')
    assert short == old
