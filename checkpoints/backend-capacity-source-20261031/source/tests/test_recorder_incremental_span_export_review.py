"""Independent adversarial selected-span export checks.

Real ASGI transcript/status readers and synthetic finalized job/analysis rows;
no native worker, speech inference, deployment, or external network is exercised.
"""
from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path

import pytest

from audio_backend import recorder_incremental_span_export as export
from audio_backend import recorder_incremental_span_status as status
from audio_backend import recording_uploader as u
from audio_backend import transcript_contract as c
from test_recorder_incremental_span_status import fixture
from test_recorder_span_status import local_files


@contextmanager
def example(tmp_path, *, seconds=10, worker='completed'):
    with fixture(tmp_path, count=1, frames=seconds * u.RATE, worker=worker) as items:
        args, run, routes, app, state = items
        with app.state.store.db() as db:
            db.execute('''INSERT INTO segments(id,chunk_id,start,end,text,raw_text,
                speaker_id,speaker_local_id,speaker_status,asr_backend,timestamp_kind,
                confidence,speaker_window_start) VALUES
                (1,'work-0',0.25,1.25,'independent-marker','independent-marker',
                'PRIVATE-PERSON','S1','unknown','synthetic-asr','native-segment',0.8,0.0)''')
        run['output_dir'] = tmp_path / 'export'
        yield args, run, routes, app, state


def transcript(path):
    return '/transcript' in path and '/evidence/' not in path


def canonical_replace(row, **changes):
    source = dict(row['canonical_asr'], **changes)
    replacement = c.primary_evidence(source['session_id'], source)
    replacement['reference_only'] = False
    row.clear(); row.update(replacement)


BAD_ROW_CHANGES = [
    'text_mismatch', 'raw_text_mismatch', 'start_float_alias', 'start_bool_alias',
    'end_float_alias', 'home_bool_alias', 'reference_integer_alias', 'source_kind',
    'source_ref_hash', 'content_hash', 'speaker_scope', 'foreign_chunk_valid_hash',
    'foreign_session_valid_hash', 'future_geometry_valid_hash', 'foreign_chunk_time_valid_hash',
    'nonfinite_start', 'nonfinite_speaker_window', 'canonical_bool_id',
    'canonical_bool_start', 'canonical_bool_confidence', 'canonical_bool_window',
    'duplicate_row', 'word_timing_invention',
]


@pytest.mark.parametrize('change', BAD_ROW_CHANGES)
def test_primary_text_source_and_timing_cannot_be_reassociated(tmp_path, change):
    with example(tmp_path) as (args, run, routes, app, state):
        before = local_files(args); edited = False
        def edit(path, page):
            nonlocal edited
            if not transcript(path): return
            edited = True
            row = page['baseline_reading'][0]
            if change == 'text_mismatch': row['text'] = 'forged-display'
            elif change == 'raw_text_mismatch': row['raw_text'] = 'forged-raw'
            elif change == 'start_float_alias': row['start_frame'] = float(row['start_frame'])
            elif change == 'start_bool_alias': row['start_frame'] = False
            elif change == 'end_float_alias': row['end_frame'] = float(row['end_frame'])
            elif change == 'home_bool_alias': row['home_block'] = False
            elif change == 'reference_integer_alias': row['reference_only'] = 0
            elif change == 'source_kind': row['source'] = 'moss'
            elif change == 'source_ref_hash': row['source_ref'] = 'p.1.' + '0' * 64
            elif change == 'content_hash': row['content_sha256'] = '0' * 64
            elif change == 'speaker_scope': row['speaker_scope'] = ['work-0', 0.0, 'Private Person']
            elif change == 'foreign_chunk_valid_hash': canonical_replace(row, chunk_id='foreign-work')
            elif change == 'foreign_session_valid_hash': canonical_replace(row, session_id='foreign-session')
            elif change == 'future_geometry_valid_hash': canonical_replace(row, start=9.5, end=10.5)
            elif change == 'foreign_chunk_time_valid_hash': canonical_replace(row, start=11.0, end=12.0)
            elif change == 'nonfinite_start': row['canonical_asr']['start'] = float('nan')
            elif change == 'nonfinite_speaker_window': row['canonical_asr']['speaker_window_start'] = float('inf')
            elif change == 'canonical_bool_id': row['canonical_asr']['id'] = True
            elif change == 'canonical_bool_start': row['canonical_asr']['start'] = False
            elif change == 'canonical_bool_confidence': row['canonical_asr']['confidence'] = True
            elif change == 'canonical_bool_window': row['canonical_asr']['speaker_window_start'] = False
            elif change == 'duplicate_row': page['baseline_reading'].append(copy.deepcopy(row))
            elif change == 'word_timing_invention': row['word_times'] = [{'text': row['text'], 'start': 0.25}]
        routes.edit = edit
        with pytest.raises((status.Refused, u.Incomplete, ValueError)):
            export.run(**run)
        assert edited
        assert local_files(args) == before
        assert all(method == 'GET' for method, _ in routes.calls)


@pytest.mark.parametrize('change', [
    'wrong_session', 'wrong_policy_hash', 'wrong_policy_sha256', 'float_rate',
    'bool_block', 'float_page_start', 'float_page_end', 'false_complete',
    'invented_eos', 'premature_terminal', 'cursor_foreign_session',
    'cursor_wrong_revision', 'cursor_skips_block', 'cursor_loops_block',
])
def test_session_page_and_cursor_contract_is_exact(tmp_path, change):
    with example(tmp_path, seconds=65) as (args, run, routes, app, state):
        edited = False
        def edit(path, page):
            nonlocal edited
            if not transcript(path) or page['block'] != 0: return
            edited = True
            if change == 'wrong_session': page['session_id'] = 'unrelated'
            elif change == 'wrong_policy_hash': page['policy_hash'] = '0' * 64
            elif change == 'wrong_policy_sha256': page['policy_sha256'] = '0' * 64
            elif change == 'float_rate': page['sample_rate'] = float(page['sample_rate'])
            elif change == 'bool_block': page['block'] = False
            elif change == 'float_page_start': page['start_frame'] = 0.0
            elif change == 'float_page_end': page['end_frame'] = float(page['end_frame'])
            elif change == 'false_complete': page['complete_content'] = True
            elif change == 'invented_eos': page['eos'] = True
            elif change == 'premature_terminal': page['next_cursor'] = None
            elif change == 'cursor_foreign_session': page['next_cursor'] = c.cursor('unrelated', 1, page['revision'])
            elif change == 'cursor_wrong_revision': page['next_cursor'] = c.cursor(page['session_id'], 1, '0' * 64)
            elif change == 'cursor_skips_block': page['next_cursor'] = c.cursor(page['session_id'], 2, page['revision'])
            elif change == 'cursor_loops_block': page['next_cursor'] = c.cursor(page['session_id'], 0, page['revision'])
        routes.edit = edit
        with pytest.raises((status.Refused, u.Incomplete, ValueError)):
            export.run(**run)
        assert edited and all(method == 'GET' for method, _ in routes.calls)


@pytest.mark.parametrize('worker', ['queued', 'running', 'waiting_input', 'failed', 'paused', 'cancelled'])
def test_noncompleted_span_never_reads_transcript(tmp_path, worker):
    with example(tmp_path, worker=worker) as (args, run, routes, app, state):
        before = local_files(args)
        with pytest.raises((status.Refused, u.Incomplete)):
            export.run(**run)
        assert not any(transcript(path) for _, path in routes.calls)
        assert before == local_files(args)


@pytest.mark.parametrize('target', ['existing', 'symlink', 'symlink_parent', 'public_parent'])
def test_unsafe_output_never_overwrites_or_discloses(tmp_path, target):
    with example(tmp_path) as (args, run, routes, app, state):
        output = run['output_dir']
        if target == 'existing':
            output.mkdir(mode=0o700); (output / 'sentinel').write_bytes(b'keep')
        elif target == 'symlink':
            other = tmp_path / 'other'; other.mkdir(mode=0o700)
            (other / 'sentinel').write_bytes(b'keep'); output.symlink_to(other, target_is_directory=True)
        elif target == 'symlink_parent':
            other = tmp_path / 'other'; other.mkdir(mode=0o700)
            link = tmp_path / 'link'; link.symlink_to(other, target_is_directory=True)
            run['output_dir'] = link / 'new'
        elif target == 'public_parent':
            parent = tmp_path / 'public'; parent.mkdir(mode=0o755)
            run['output_dir'] = parent / 'new'
        before = local_files(args)
        with pytest.raises((status.Refused, OSError, ValueError)):
            export.run(**run)
        assert before == local_files(args)
        for p in tmp_path.rglob('sentinel'): assert p.read_bytes() == b'keep'
        if target in ('symlink_parent', 'public_parent'): assert not Path(run['output_dir']).exists()


def test_both_existing_job_locks_are_held_during_transcript_get(tmp_path):
    with example(tmp_path) as (args, run, routes, app, state):
        saw_read = False
        def edit(path, page):
            nonlocal saw_read
            if not transcript(path): return
            saw_read = True
            for relative in ('intake.lock', 'uploader/uploader.lock'):
                with (args['job_dir'] / relative).open('rb') as stream:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        routes.edit = edit
        export.run(**run)
        assert saw_read


@pytest.mark.parametrize('changed', ['header', 'checkpoint', 'archive'])
def test_local_history_change_during_transcript_get_prevents_success(tmp_path, changed):
    with example(tmp_path) as (args, run, routes, app, state):
        did_change = False
        def edit(path, page):
            nonlocal did_change
            if not transcript(path) or did_change: return
            did_change = True
            relative = {'header': 'capture.json', 'checkpoint': 'uploader/uploader.json',
                        'archive': 'uploader/uploader.history/page-0001.json'}[changed]
            (args['job_dir'] / relative).touch()
        routes.edit = edit
        with pytest.raises((status.Refused, u.Incomplete)):
            export.run(**run)
        assert did_change


@pytest.mark.parametrize('code', [401, 404, 409, 429, 503])
def test_transcript_http_errors_are_get_only_and_private(tmp_path, code):
    with example(tmp_path) as (args, run, routes, app, state):
        class Failed:
            def request(self, method, path):
                if transcript(path):
                    routes.calls.append((method, path))
                    raise u.HTTPFailure(code, 'PRIVATE-TEXT fixture-secret /private/original')
                return routes.request(method, path)
        run['transport'] = Failed()
        before = local_files(args)
        with pytest.raises(u.HTTPFailure) as caught:
            export.run(**run)
        report = export.failure_report(caught.value)
        assert all(secret not in json.dumps(report) for secret in ('PRIVATE-TEXT', 'fixture-secret', '/private/original'))
        assert before == local_files(args)
        assert all(method == 'GET' for method, _ in routes.calls)
        assert sum(transcript(path) for _, path in routes.calls) <= 3


def test_default_status_request_sequence_is_unchanged(tmp_path):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        result = status.run(**run)
        sid = run['session_id']; base = f'/sessions/{sid}'
        assert result['status'] == 'completed'
        assert routes.calls == [('GET', path) for path in [
            base + '/recording-contract', base + '/recordings/extended',
            base + '/recordings/extended/pages/1', base + '/chunks?after=0&limit=16',
            base + '/chunks?after=10000&limit=16', base + '/recordings/extended',
            base + '/recording-contract',
        ]]


def test_cross_page_primary_is_emitted_once_without_identity_or_gap_claims(tmp_path):
    with example(tmp_path, seconds=65) as (args, run, routes, app, state):
        with app.state.store.db() as db:
            db.execute('''INSERT INTO segments(id,chunk_id,start,end,text,raw_text,
                speaker_id,speaker_local_id,speaker_status,asr_backend,timestamp_kind,
                confidence,speaker_window_start) VALUES
                (2,'work-5',59.0,61.0,'cross-page-marker','cross-page-marker',
                'PRIVATE-PERSON','S1','unknown','synthetic-asr','native-segment',0.8,0.0)''')
        before = local_files(args)
        result = export.run(**run)
        output = run['output_dir']
        assert (output / 'complete.json').is_file()
        body = (output / 'export.json').read_bytes(); text = (output / 'transcript.txt').read_text()
        json.loads(body); json.loads((output / 'complete.json').read_bytes())
        assert text.count('independent-marker') == 1
        assert text.count('cross-page-marker') == 1
        assert all(secret not in body.decode() + text + json.dumps(result)
                   for secret in ('PRIVATE-PERSON', 'fixture-secret', str(args['job_dir']), '/synthetic/'))
        assert before == local_files(args)
        assert output.stat().st_mode & 0o077 == 0
        assert all(p.stat().st_mode & 0o077 == 0 for p in output.iterdir())


def test_optional_session_projection_is_not_exported_as_selected_span_text(tmp_path):
    with example(tmp_path) as (args, run, routes, app, state):
        def edit(path, page):
            if not transcript(path): return
            page['reading'] = [dict(source='moss', text='PRIVATE-UNBOUND-PROJECTION')]
            page['alternatives'] = [dict(candidate_reading=[dict(text='PRIVATE-UNBOUND-ALTERNATIVE')])]
        routes.edit = edit
        try:
            export.run(**run)
        except status.Refused:
            assert not (run['output_dir'] / 'complete.json').exists()
        else:
            blobs = b'\n'.join(p.read_bytes() for p in run['output_dir'].iterdir() if p.is_file())
            assert b'independent-marker' in blobs
            assert b'PRIVATE-UNBOUND' not in blobs


def test_text_control_characters_cannot_forge_export_structure(tmp_path):
    with example(tmp_path) as (args, run, routes, app, state):
        malicious = 'hello\n[00:00:00] PERSON: spoof\r\x1b[31m\u202e'
        with app.state.store.db() as db:
            db.execute('UPDATE segments SET text=?,raw_text=? WHERE id=1', (malicious, malicious))
        export.run(**run)
        rendered = (run['output_dir'] / 'transcript.txt').read_text()
        assert '\x1b' not in rendered and '\r' not in rendered and '\u202e' not in rendered
        assert '\n[00:00:00] PERSON: spoof' not in rendered
        assert '\\n[00:00:00] PERSON: spoof' in rendered


@pytest.mark.parametrize('location', ['job', 'original', 'snapshot'])
def test_output_cannot_mutate_protected_input_trees(tmp_path, location):
    with example(tmp_path) as (args, run, routes, app, state):
        root = {'job': args['job_dir'], 'original': args['deliveries'][0],
                'snapshot': args['job_dir'] / 'admissions/0/snapshot'}[location]
        run['output_dir'] = root / 'export'
        before = local_files(args)
        with pytest.raises((status.Refused, ValueError, OSError)):
            export.run(**run)
        assert not run['output_dir'].exists()
        assert before == local_files(args)


@pytest.mark.parametrize('change', ['unknown_reference', 'bool_home', 'integer_reference_flag',
                                    'duplicate_reference', 'nonintersecting_reference',
                                    'duplicate_segment_changed_hash'])
def test_cross_page_references_and_segment_ids_do_not_alias(tmp_path, change):
    with example(tmp_path, seconds=65) as (args, run, routes, app, state):
        with app.state.store.db() as db:
            db.execute('''INSERT INTO segments(id,chunk_id,start,end,text,raw_text,
                speaker_local_id,speaker_status,asr_backend,timestamp_kind,
                confidence,speaker_window_start) VALUES
                (2,'work-5',59.0,61.0,'cross-page-marker','cross-page-marker',
                'S1','unknown','synthetic-asr','native-segment',0.8,0.0)''')
        first_row = None; edited = False
        def edit(path, page):
            nonlocal first_row, edited
            if not transcript(path): return
            if page['block'] == 0:
                first_row = copy.deepcopy(page['baseline_reading'][0])
                if change == 'duplicate_segment_changed_hash':
                    row = copy.deepcopy(first_row); canonical_replace(row, text='changed-with-valid-hash')
                    page['baseline_reading'].append(row); edited = True
                return
            if change == 'duplicate_segment_changed_hash': return
            row = page['baseline_reading'][0]; edited = True
            if change == 'unknown_reference': row['source_ref'] = 'p.2.' + '0' * 64
            elif change == 'bool_home': row['home_block'] = False
            elif change == 'integer_reference_flag': row['reference_only'] = 1
            elif change == 'duplicate_reference': page['baseline_reading'].append(copy.deepcopy(row))
            elif change == 'nonintersecting_reference':
                page['baseline_reading'].append(dict(source_ref=first_row['source_ref'],
                    source='primary', home_block=0, reference_only=True))
        routes.edit = edit
        with pytest.raises((status.Refused, u.Incomplete, ValueError)):
            export.run(**run)
        assert edited and not (run['output_dir'] / 'complete.json').exists()


def test_remote_row_order_is_verified_or_canonicalized(tmp_path):
    with example(tmp_path) as (args, run, routes, app, state):
        with app.state.store.db() as db:
            db.execute('''INSERT INTO segments(id,chunk_id,start,end,text,raw_text,
                speaker_local_id,speaker_status,asr_backend,timestamp_kind,
                confidence,speaker_window_start) VALUES
                (2,'work-0',2.0,3.0,'later-marker','later-marker',
                'S1','unknown','synthetic-asr','native-segment',0.8,0.0)''')
        def edit(path, page):
            if transcript(path): page['baseline_reading'].reverse()
        routes.edit = edit
        try:
            export.run(**run)
        except status.Refused:
            assert not (run['output_dir'] / 'complete.json').exists()
        else:
            rendered = (run['output_dir'] / 'transcript.txt').read_text()
            assert rendered.index('independent-marker') < rendered.index('later-marker')


def test_publication_rechecks_final_link_inode(tmp_path, monkeypatch):
    with example(tmp_path) as (args, run, routes, app, state):
        sentinel = tmp_path / 'unrelated-private'; sentinel.write_bytes(b'KEEP-UNRELATED'); sentinel.chmod(0o600)
        original = os.link; changed = False
        def replace_then_link(source, destination, **kwargs):
            nonlocal changed
            if source == 'export.json.partial' and not changed:
                changed = True
                os.unlink(source, dir_fd=kwargs['src_dir_fd'])
                os.symlink(sentinel, source, dir_fd=kwargs['src_dir_fd'])
            return original(source, destination, **kwargs)
        monkeypatch.setattr(os, 'link', replace_then_link)
        with pytest.raises((status.Refused, OSError)):
            export.run(**run)
        assert changed and sentinel.read_bytes() == b'KEEP-UNRELATED'
        assert not (run['output_dir'] / 'complete.json').exists()


@pytest.mark.parametrize('field', ['pages', 'units', 'output_bytes', 'network_bytes', 'requests'])
@pytest.mark.parametrize('value', [False, 1.0, 0, -1, float('inf'), float('nan')])
def test_export_limit_values_are_finite_exact_positive_integers(field, value):
    with pytest.raises((status.Refused, ValueError)):
        export.ExportLimits(**{field: value})


def test_post_manifest_sync_failure_does_not_leave_success_marker(tmp_path, monkeypatch):
    with example(tmp_path) as (args, run, routes, app, state):
        original = os.fsync; raised = False
        def failing_sync(fd):
            nonlocal raised
            if (run['output_dir'] / 'complete.json').exists() and not raised:
                raised = True
                raise OSError('synthetic directory sync fault')
            return original(fd)
        monkeypatch.setattr(os, 'fsync', failing_sync)
        with pytest.raises(OSError):
            export.run(**run)
        assert raised and not (run['output_dir'] / 'complete.json').exists()


def test_output_path_substitution_cannot_redirect_text(tmp_path):
    with example(tmp_path) as (args, run, routes, app, state):
        output = run['output_dir']; displaced = tmp_path / 'displaced'
        unrelated = tmp_path / 'unrelated'; unrelated.mkdir(mode=0o700)
        sentinel = unrelated / 'sentinel'; sentinel.write_bytes(b'KEEP-UNRELATED')
        changed = False
        def edit(path, page):
            nonlocal changed
            if transcript(path) and not changed:
                changed = True
                output.rename(displaced)
                output.symlink_to(unrelated, target_is_directory=True)
        routes.edit = edit
        with pytest.raises((status.Refused, OSError)):
            export.run(**run)
        assert changed and sentinel.read_bytes() == b'KEEP-UNRELATED'
        assert sorted(p.name for p in unrelated.iterdir()) == ['sentinel']
        assert not (displaced / 'complete.json').exists()
