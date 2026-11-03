"""Model-free primary export: real private v3 jobs and ASGI GET projections."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from audio_backend import recorder_incremental_span_export as export
from audio_backend import recorder_incremental_span_status as status
from audio_backend import recording_uploader as u
from audio_backend import transcript_contract as c
from test_recorder_incremental_span_status import fixture
from test_recorder_span_status import local_files


def seed(app, text='Hello 世界', *, ident=1, start=.125, end=1.25):
    with app.state.store.db() as db:
        db.execute('''INSERT INTO segments(id,chunk_id,start,end,text,raw_text,speaker_local_id,
            speaker_status,asr_backend,timestamp_kind,confidence,speaker_window_start)
            VALUES(?,'work-0',?,?,?,?,'S1','unknown','synthetic-asr','native-segment',.8,0.0)''',
            (ident,start,end,text,text))


def verify_files(path, result):
    assert set(p.name for p in path.iterdir()) == {'export.json', 'transcript.txt', 'complete.json'}
    assert json.loads((path/'complete.json').read_bytes()) == result
    assert path.stat().st_mode & 0o777 == 0o700
    for name, expected in result['artifacts'].items():
        data = (path/name).read_bytes()
        assert len(data) == expected['bytes']
        assert hashlib.sha256(data).hexdigest() == expected['sha256']
        assert (path/name).stat().st_mode & 0o777 == 0o600
    return json.loads((path/'export.json').read_bytes())


@pytest.mark.parametrize('close,count', [('STOP',9), ('INTERRUPTION',9), ('RECOVERED_TAIL',1)])
def test_empty_completed_export_preserves_uncertainty_and_exact_repeat(tmp_path, close, count):
    with fixture(tmp_path, count=count, close=close) as (args, run, routes, app, state):
        before = local_files(args)
        result = export.run(**run, output_dir=tmp_path/'one')
        repeated = export.run(**run, output_dir=tmp_path/'two')
        assert result == repeated
        for name in ('export.json', 'transcript.txt', 'complete.json'):
            assert (tmp_path/'one'/name).read_bytes() == (tmp_path/'two'/name).read_bytes()
        data = verify_files(tmp_path/'one', result)
        assert data['units'] == [] and result['text_status'] == 'no_primary_text_observed'
        assert result['nonempty_text_units'] == result['units'] == 0
        assert data['complete_content'] is False and data['eos'] is None
        assert data['word_times'] is None and data['speaker_identity_valid'] is False
        assert data['completion']['span']['before_span_gap_frames'] is None
        assert data['completion']['span']['original_close_reason'] == close
        assert all(row['text_status'] == 'no_primary_text_observed' for row in data['work_text_coverage'])
        assert 'NO PRIMARY TEXT OBSERVED' in (tmp_path/'one/transcript.txt').read_text()
        assert before == local_files(args)
        assert all(method == 'GET' for method, _ in routes.calls)


def test_non_grid_original_timestamps_and_unicode_are_preserved(tmp_path):
    with fixture(tmp_path, count=1, frames=10*u.RATE) as (args, run, routes, app, state):
        seed(app, start=.123456789, end=1.23456789)
        result = export.run(**run, output_dir=tmp_path/'export')
        data = verify_files(tmp_path/'export', result); row = data['units'][0]
        assert row['start_seconds'] == row['canonical_asr']['start'] == .123456789
        assert row['end_seconds'] == row['canonical_asr']['end'] == 1.23456789
        assert row['start_frame'] == c.primary_frame(.123456789)
        assert row['end_frame'] == c.primary_frame(1.23456789, end=True)
        assert row['word_times'] is None and row['speaker_scope'] == ['work-0',0.0,'S1']
        assert row['text'] == 'Hello 世界' and 'Hello 世界' in (tmp_path/'export/transcript.txt').read_text()
        assert data['completion']['input_mapping'][0]['original_epoch_frame_start'] == 100003
        assert result['nonempty_text_units'] == 1


@pytest.mark.parametrize('kind', ['pages', 'units', 'output_bytes', 'network_bytes', 'requests'])
def test_lower_export_budgets_fail_without_complete_marker(tmp_path, kind):
    with fixture(tmp_path, count=1, frames=65*u.RATE) as (args, run, routes, app, state):
        seed(app); seed(app, ident=2, start=2., end=3.)
        bounds = replace(export.ExportLimits(), **{kind:1})
        with pytest.raises(status.Refused):
            export.run(**run, output_dir=tmp_path/'export', export_limits=bounds)
        assert not (tmp_path/'export/complete.json').exists()


@pytest.mark.parametrize('event', ['cancel', 'deadline'])
def test_initial_resource_refusal_has_no_network_or_output(tmp_path, event):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        if event == 'cancel': run['cancel'] = lambda: True
        else: run['limits'] = replace(run['limits'], deadline_seconds=1e-9)
        with pytest.raises((InterruptedError,u.Incomplete)):
            export.run(**run, output_dir=tmp_path/'export')
        assert routes.calls == [] and not (tmp_path/'export').exists()


def test_work_status_regression_after_transcript_cannot_publish(tmp_path):
    with fixture(tmp_path, count=1, frames=10*u.RATE) as (args, run, routes, app, state):
        seed(app); changed = False
        def edit(path, value):
            nonlocal changed
            if '/transcript?' in path and not changed:
                changed = True
                with app.state.store.db() as db:
                    db.execute("UPDATE chunks SET status='paused' WHERE id='work-0'")
        routes.edit = edit
        with pytest.raises(status.Refused): export.run(**run, output_dir=tmp_path/'export')
        assert changed and not (tmp_path/'export/complete.json').exists()


def test_second_transcript_pass_catches_changed_content_under_same_revision(tmp_path):
    with fixture(tmp_path, count=1, frames=10*u.RATE) as (args, run, routes, app, state):
        seed(app); seen = 0
        def edit(path, page):
            nonlocal seen
            if '/transcript?' not in path: return
            seen += 1
            if seen == 2:
                old = page['baseline_reading'][0]
                row = dict(old['canonical_asr'],text='changed',raw_text='changed')
                page['baseline_reading'] = [dict(c.primary_evidence(run['session_id'],row),reference_only=False)]
        routes.edit = edit
        with pytest.raises(status.Refused, match='transcript_changed'):
            export.run(**run, output_dir=tmp_path/'export')
        assert seen == 2 and not (tmp_path/'export/complete.json').exists()


@pytest.mark.parametrize('git_kind', ['directory', 'file'])
def test_actual_repository_output_is_refused_before_get(tmp_path, git_kind):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        root = tmp_path/'private-repo'; root.mkdir(mode=0o700)
        if git_kind == 'directory':
            (root/'.git').mkdir(); (root/'.git/HEAD').write_text('ref: refs/heads/main\n')
        else: (root/'.git').write_text('gitdir: elsewhere\n')
        with pytest.raises(status.Refused, match='output_in_repository'):
            export.run(**run, output_dir=root/'export')
        assert not routes.calls and not (root/'export').exists()


def test_incomplete_manifest_write_never_creates_completion_marker(tmp_path, monkeypatch):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        original = export._Output.write
        def write(self, name, raw):
            if name == 'complete.json.partial': raise OSError('PRIVATE secret path')
            return original(self,name,raw)
        monkeypatch.setattr(export._Output,'write',write)
        with pytest.raises(OSError): export.run(**run, output_dir=tmp_path/'export')
        assert not (tmp_path/'export/complete.json').exists()


def test_cli_failure_and_success_never_print_transcript(tmp_path, monkeypatch, capsys):
    argv = ['--job-dir','private-job','--api','https://example.test','--session-id','sid',
            '--capture-id','cid','--output-dir',str(tmp_path/'out'),'--exclusive-writer']
    def refused(**kw): raise OSError('PRIVATE transcript password path')
    monkeypatch.setattr(export,'run',refused)
    assert export.main(argv) == 2
    captured = capsys.readouterr()
    assert not captured.out and 'PRIVATE' not in captured.err and json.loads(captured.err)['status'] == 'refused'
    monkeypatch.setattr(export,'run',lambda **kw: dict(schema=export.SCHEMA,status='exported',units=1))
    assert export.main(argv) == 0
    captured = capsys.readouterr()
    assert not captured.err and json.loads(captured.out)['units'] == 1
