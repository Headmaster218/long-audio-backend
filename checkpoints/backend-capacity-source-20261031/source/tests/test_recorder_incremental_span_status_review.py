"""Independent GET-only v3 observer review; all remote work rows are synthetic."""
import copy
import hashlib
import json
import os

import pytest

from audio_backend import recorder_incremental_span_status as status
from audio_backend import recorder_span_status as v2
from audio_backend import recording_uploader as u
from audio_backend import recording_uploader_history as history
from test_recorder_incremental_span_status import fixture
from test_recorder_span_status import local_files


def rewrite_archive(root, kind, ordinal, mutate):
    """Rebind a changed canonical chain to test semantics, not stale hashes."""
    state_path = root / 'uploader/uploader.json'
    state = json.loads(state_path.read_bytes())
    prefix = 'inputs' if kind == 'input' else 'pages'
    previous = history.ZERO
    for n in range(1, state[prefix + '_done'] + 1):
        path = root / f'uploader/uploader.history/{kind}-{n:04d}.json'
        envelope = json.loads(path.read_bytes())
        if n == ordinal:
            mutate(envelope['payload'])
        envelope['previous'] = previous
        raw = u._json(envelope)
        path.write_bytes(raw)
        previous = hashlib.sha256(raw).hexdigest()
    state[prefix + '_head'] = previous
    state_path.write_bytes(u._json(state))


@pytest.mark.parametrize('change', [
    'input_eof_integer', 'input_start_bool', 'input_frame_float', 'input_decoder_bool',
    'page_number_bool', 'create_page_bool', 'create_origin_bool', 'seal_eos_integer',
    'seal_revision_bool', 'piece_start_bool', 'piece_frame_float', 'piece_digest',
    'piece_input_id', 'unknown_page_id',
])
def test_rebound_local_chain_semantic_changes_fail_before_get(tmp_path, change):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        if change.startswith('input_'):
            def change_input(entry):
                if change == 'input_eof_integer': entry['clean_eof'] = 1
                elif change == 'input_start_bool': entry['normalized_start'] = False
                elif change == 'input_frame_float': entry['frames'] = float(entry['frames'])
                elif change == 'input_decoder_bool': entry['decoder']['format'] = True
            rewrite_archive(args['job_dir'], 'input', 1, change_input)
        else:
            def change_page(row):
                if change == 'page_number_bool': row['number'] = True
                elif change == 'create_page_bool': row['create']['page_number'] = True
                elif change == 'create_origin_bool': row['create']['capture_origin_frame'] = False
                elif change == 'seal_eos_integer': row['seal']['eos'] = 1
                elif change == 'seal_revision_bool': row['seal']['expected_revision'] = True
                elif change == 'piece_start_bool': row['pieces'][0]['input_start'] = False
                elif change == 'piece_frame_float': row['pieces'][0]['frames'] = float(row['pieces'][0]['frames'])
                elif change == 'piece_digest': row['pieces'][0]['digest'] = 'a' * 64
                elif change == 'piece_input_id': row['pieces'][0]['input_id'] = 'unknown'
                elif change == 'unknown_page_id': row['server_id'] = '\n'
            rewrite_archive(args['job_dir'], 'page', 1, change_page)
        before = local_files(args)
        with pytest.raises((status.Refused, u.Incomplete)):
            status.run(**run)
        assert not routes.calls and before == local_files(args)


@pytest.mark.parametrize('field', ['max_hours', 'max_inputs', 'input_bytes'])
def test_header_caps_cannot_alias_integer_types(tmp_path, field):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        path = args['job_dir'] / 'capture.json'
        value = json.loads(path.read_bytes())
        value[field] = float(value[field])
        path.write_bytes(u._json(value) + b'\n')
        with pytest.raises(status.Refused): status.run(**run)
        assert not routes.calls


@pytest.mark.parametrize('code', [401, 404, 429, 503])
def test_network_errors_make_one_get_and_leave_everything_unchanged(tmp_path, code):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        calls = []
        class Failed:
            def request(self, method, path):
                calls.append((method, path))
                raise u.HTTPFailure(code, 'private server text must not leak')
        run['transport'] = Failed()
        before = local_files(args)
        with pytest.raises(u.HTTPFailure) as caught: status.run(**run)
        report = status.failure_report(caught.value)
        assert report['http_status'] == code and report['read_only']
        assert report['possible_remote_effects'] is False
        assert not report['analysis_completion_verified']
        assert 'private server' not in json.dumps(report)
        assert len(calls) == 1 and calls[0][0] == 'GET'
        assert local_files(args) == before


def test_unavailable_mutation_functions_never_needed(tmp_path, monkeypatch):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        before = local_files(args)
        def forbidden(*a, **kw):
            raise AssertionError('Mutation-capable function reached')
        for name in ('__init__', 'load', 'initialize', 'save', '_atomic_checkpoint', '_census',
                     'budget', 'recover', 'cleanup', '_archive', '_promote_archive'):
            monkeypatch.setattr(history.PagedJournal, name, forbidden)
        for module, name in ((status.engine, 'run'), (status.engine, '_run'),
                             (status.engine, 'durable_admission'), (status.initial, 'complete_file'),
                             (status.initial, 'complete_scaffold'), (os, 'fsync'),
                             (os, 'rename'), (os, 'replace'), (os, 'unlink'), (os, 'mkdir')):
            monkeypatch.setattr(module, name, forbidden)
        result = status.run(**run)
        assert result['status'] == 'completed'
        assert local_files(args) == before
        monkeypatch.undo()


@pytest.mark.parametrize('change', ['auth_head', 'archive_replace', 'finish_late', 'admission_late',
                                    'history_extra_late', 'capture_aborted', 'contract_late'])
def test_remote_observation_cannot_hide_local_or_remote_changes(tmp_path, change):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        root = args['job_dir']
        did_change = False
        def edit(path, value):
            nonlocal did_change
            if '/chunks?' in path and not did_change:
                did_change = True
                if change == 'auth_head':
                    p = root/'uploader/uploader.json'; d = json.loads(p.read_bytes())
                    d['pages_head'] = 'a'*64; p.write_bytes(u._json(d))
                elif change == 'archive_replace':
                    p = root/'uploader/uploader.history/page-0001.json'
                    raw = p.read_bytes(); p.unlink(); p.write_bytes(raw); p.chmod(0o600)
                elif change == 'finish_late':
                    p = root/'uploader/uploader.json'; d = json.loads(p.read_bytes())
                    d['complete'] = False; p.write_bytes(u._json(d))
                elif change == 'admission_late': (root/'admissions/0/admission.json').touch()
                elif change == 'history_extra_late': (root/'uploader/uploader.history/extra').write_bytes(b'x')
                elif change == 'capture_aborted':
                    with app.state.store.db() as db: db.execute("UPDATE recording_batches SET state='aborted'")
            if change == 'contract_late' and did_change and path.endswith('/recording-contract'):
                value['readiness'] = 'changed'
        routes.edit = edit
        with pytest.raises((status.Refused, u.Incomplete)): status.run(**run)
        assert all(method == 'GET' for method, _ in routes.calls)


def test_response_and_deadline_limits_preserve_read_only_failure(tmp_path, monkeypatch):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        before = local_files(args)
        run['limits'] = u.Limits(deadline_seconds=.000001)
        with pytest.raises(u.Incomplete): status.run(**run)
        assert not routes.calls
        run['limits'] = args['limits']
        routes.edit = lambda path, value: value.update(padding='x' * u.JSON_BYTES)
        with pytest.raises(status.Refused, match='response_byte_bound'): status.run(**run)
        assert len(routes.calls) == 1 and before == local_files(args)
        routes.edit = None; routes.calls.clear()
        monkeypatch.setattr(status, 'MAX_RESPONSE_BYTES', 1)
        with pytest.raises(status.Refused, match='total_response_byte_bound'): status.run(**run)
        assert len(routes.calls) == 1 and before == local_files(args)


@pytest.mark.parametrize('field', ['position', 'frames', 'capture_start_frame', 'bytes'])
def test_work_projection_integer_types_are_exact(tmp_path, field):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        def edit(path, value):
            if '/pages/1' in path:
                value['work_chunks'][0][field] = float(value['work_chunks'][0][field])
        routes.edit = edit
        with pytest.raises(status.Refused): status.run(**run)


def test_shared_v2_reader_budgets_remain_identical():
    class Guard:
        def check(self): pass
    class Peer:
        def request(self, method, path): assert method == 'GET'; return {}
    reader = v2._Reader(Peer(), Guard())
    assert reader.max_requests == 755
    assert reader.max_response_bytes == 32 * 1024**2
    for _ in range(755): reader.get('/synthetic')
    with pytest.raises(status.Refused, match='request_bound'): reader.get('/synthetic')


def test_oversized_owned_lock_is_not_a_valid_bounded_history(tmp_path):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        path = args['job_dir']/'uploader/uploader.lock'
        with path.open('r+b') as stream: stream.truncate(history.METADATA_BYTES + 1)
        with pytest.raises((status.Refused, u.Incomplete)):
            status.run(**run)
        assert not routes.calls


def test_full_capacity_projection_helpers_are_bounded_without_v2_relaxation():
    """Synthetic geometry only: no media, worker, native service or completion claim."""
    frames = history.MAX_FRAMES
    page_count = (frames + u.PAGE_FRAMES - 1) // u.PAGE_FRAMES
    work_count = (frames + v2.CHUNK_FRAMES - 1) // v2.CHUNK_FRAMES
    capture = 'synthetic-capacity'

    def local_page(number):
        start = (number - 1) * u.PAGE_FRAMES
        end = min(number * u.PAGE_FRAMES, frames)
        pieces = []
        while start < end:
            size = min(u.SOURCE_FRAMES, end - start)
            key = f'piece-{number}-{len(pieces)}'
            pieces.append(dict(key=key, digest='a'*64, frames=size, bytes=44+2*size,
                               capture_start=start, server_id=key))
            start += size
        return dict(number=number, server_id=f'page-{number}', create=dict(page_number=number),
                    released=True, pieces=pieces,
                    seal=dict(expected_revision=1, end_frame=end, eos=number == page_count))

    class Pages:
        def __len__(self): return page_count
        def __iter__(self):
            for number in range(1, page_count + 1): yield local_page(number)

    class Guard:
        def check(self): pass

    class Synthetic:
        def request(self, method, path):
            assert method == 'GET'
            if '/pages/' in path:
                number = int(path.rsplit('/', 1)[1]); row = local_page(number)
                start = (number - 1) * u.PAGE_FRAMES; end = row['seal']['end_frame']
                work = []
                while start < end:
                    size = min(v2.CHUNK_FRAMES, end-start); position = start // v2.CHUNK_FRAMES
                    work.append(dict(chunk_id=f'chunk-{position}', position=position, frames=size,
                        capture_start_frame=start, bytes=44+2*size, timeline_start=start/u.RATE,
                        duration=size/u.RATE, status='completed', receipt_state='gap',
                        native_receipt_status='disabled'))
                    start += size
                return dict(id=row['server_id'], page_number=number, capture_id=capture,
                    capture_origin_frame=0, capture_end_frame=frames,
                    policy='sealed-recording-exact-overlap-v1', state='published', released=True,
                    revision=1, start_frame=(number-1)*u.PAGE_FRAMES, end_frame=end,
                    eos=row['seal']['eos'], sources=[dict(id=p['server_id'], idempotency_key=p['key'],
                        digest=p['digest'], frames=p['frames'], bytes=p['bytes'],
                        source_start_frame=p['capture_start'], format='wav-pcm16le-16000-mono') for p in row['pieces']],
                    work_chunks=work, optional_receipts=dict(pending=0, durable=0, gaps=0, ready=False))
            after = int(path.split('?after=')[1].split('&')[0])
            rows = []
            for i in range(after, min(after+v2.CHUNK_LIMIT, work_count)):
                start = i * v2.CHUNK_FRAMES; size = min(v2.CHUNK_FRAMES, frames-start)
                rows.append(dict(id=f'chunk-{i}', cursor=i+1, status='completed',
                    timeline_start=start/u.RATE, duration=size/u.RATE, next_offset=size/u.RATE))
            return dict(chunks=rows, next_cursor=rows[-1]['cursor'] if rows else after)

    reader = v2._Reader(Synthetic(), Guard(), max_pages=page_count,
        max_work_chunks=status.MAX_WORK_CHUNKS, max_response_bytes=status.MAX_RESPONSE_BYTES)
    data = dict(pages=Pages(), emitted_frames=frames)
    work, receipts = v2._pages(reader, '/synthetic', data, dict(capture_id=capture),
                             max_work_chunks=status.MAX_WORK_CHUNKS)
    chunks = v2._chunks(reader, '/synthetic/chunks', work, max_work_chunks=status.MAX_WORK_CHUNKS)
    assert page_count == 588 and len(work) == len(chunks) == work_count == 16458
    assert set(chunks.values()) == {'completed'} and receipts['ready'] is False
    assert reader.bytes < status.MAX_RESPONSE_BYTES
    assert reader.requests == page_count + (work_count+v2.CHUNK_LIMIT-1)//v2.CHUNK_LIMIT + 1
    default_reader = v2._Reader(Synthetic(), Guard())
    with pytest.raises(status.Refused, match='work_coverage_changed'):
        v2._pages(default_reader, '/synthetic', data, dict(capture_id=capture))
    assert v2.MAX_WORK_CHUNKS == 700 and v2.MAX_RESPONSE_BYTES == 32*1024**2


@pytest.mark.parametrize('field', ['api', 'session_id', 'capture_id'])
def test_explicit_target_changes_fail_before_get(tmp_path, field):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        run[field] = 'http://127.0.0.1:9876' if field == 'api' else 'different'
        before = local_files(args)
        with pytest.raises(status.Refused): status.run(**run)
        assert not routes.calls and local_files(args) == before


def test_cli_failure_is_safe_and_requires_existing_target_ack(monkeypatch, capsys):
    argv = ['--job-dir', 'none', '--api', 'https://example.org', '--session-id', 's', '--capture-id', 'c']
    with pytest.raises(SystemExit): status.main(argv)
    capsys.readouterr()
    def fail(**kw): raise u.HTTPFailure(503, 'PRIVATE BODY with secret')
    monkeypatch.setattr(status, 'run', fail)
    assert status.main(argv+['--exclusive-writer']) == 2
    output = capsys.readouterr().err; value = json.loads(output)
    assert value['schema'] == status.SCHEMA and value['status'] == 'refused'
    assert value['http_status'] == 503 and not value['possible_remote_effects']
    assert value['read_only'] and not value['analysis_completion_verified']
    assert 'PRIVATE' not in output and 'secret' not in output
