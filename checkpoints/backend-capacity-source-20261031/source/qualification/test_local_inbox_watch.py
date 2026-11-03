"""Focused model-free controls for the finite native-watch harness, not a launch."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

FILE = Path(__file__).with_name('qualify_local_inbox_watch.py')
SPEC = importlib.util.spec_from_file_location('native_watch_harness', FILE)
h = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(h)
from test_local_inbox_loopback import FakeProcess, source_and_inbox, hashes


def test_staged_marker_last_preserves_original_bytes(tmp_path):
    source, inbox = source_and_inbox(tmp_path)
    staging = tmp_path / 'staging'; staging.mkdir()
    before = hashes(source)
    target = h.arrive(source, inbox, staging, partial=True)
    assert not list(staging.iterdir())
    assert sorted(p.name for p in target.iterdir()) == ['audio.wav', 'segment.v1.bin']
    report = h.complete_arrival(source, target, staging)
    assert report == h.inspect_delivery(source, validate_metadata=True)
    assert hashes(target) == before == hashes(source) and not list(staging.iterdir())
    with pytest.raises(FileExistsError):
        h.arrive(source, inbox, staging)
    assert hashes(target) == before


def test_atomic_marker_publication_cannot_replace_existing(tmp_path):
    source = tmp_path / 'source'; source.write_bytes(b'new')
    target = tmp_path / 'target'; target.write_bytes(b'old')
    with pytest.raises(FileExistsError): h.publish_noreplace(source, target)
    assert source.read_bytes() == b'new' and target.read_bytes() == b'old'


@pytest.mark.parametrize('force,raced,expected', [(False, False, -15), (True, False, -9), (False, True, 0)])
def test_original_process_reaped(tmp_path, force, raced, expected):
    process = FakeProcess(force=force, raced=raced)
    if raced: process.poll()
    output = tmp_path / 'wait.json'
    assert h.reap(process, output) == expected
    assert json.loads(output.read_text()) == dict(pid=4711, original_popen_wait_completed=True, exit_code=expected)


def test_concurrent_event_tail_waits_for_newline(tmp_path):
    path = tmp_path / 'events.jsonl'
    path.write_bytes(b'{"event":"first"}\n{"event":')
    assert h.read_events(path) == [{'event': 'first'}]


def test_event_sequence_and_bounds(tmp_path):
    path = tmp_path / 'events.jsonl'; events = h.Events(path)
    events.emit('first', inputs=1); events.emit('second', inputs=2)
    events.count = 4096
    with pytest.raises(RuntimeError, match='event_bound'): events.emit('excess')
    events.close(); rows = h.read_events(path)
    assert [row['index'] for row in rows] == [1, 2]
    assert rows[0]['monotonic'] <= rows[1]['monotonic']


def test_fixture_pin_precedes_delivery_open(tmp_path, monkeypatch):
    path = tmp_path / 'FIXTURE.json'; path.write_text('{}')
    monkeypatch.setattr(h, 'inspect_delivery', lambda *a, **k: pytest.fail('delivery must not open'))
    with pytest.raises(RuntimeError, match='fixture_pin'): h.verify_fixture(path, '0' * 64)


@pytest.mark.parametrize('reason,api', [('INTERRUPTION', 'PcmSegmentWriter.interrupt()'), ('STOP', 'fabricated')])
def test_fixture_requires_genuine_stop_declaration(tmp_path, reason, api):
    path = tmp_path / 'FIXTURE.json'
    path.write_text(json.dumps(dict(passed=True, terminal_reason=reason, terminal_api=api)))
    with pytest.raises(RuntimeError, match='fixture_geometry_or_provenance'):
        h.verify_fixture(path, h.digest_file(path))


def test_observation_rejects_dead_child(tmp_path):
    gate = SimpleNamespace(deadline=float('inf'), check=lambda: None)
    process = SimpleNamespace(poll=lambda: 0)
    with pytest.raises(RuntimeError, match='watch_exited_before:pending'):
        h.wait_for(gate, process, lambda: False, 'pending')


@pytest.mark.parametrize('fail', [False, True])
def test_child_observers_preserve_original_calls_and_restore(tmp_path, monkeypatch, fail):
    from audio_backend import recorder_inbox_runner as runner
    seen = []
    def validator(path, remaining):
        seen.append(('validator', str(path))); return 32
    def exporter(**kwargs): seen.append(('export', kwargs)); return {'status': 'exported'}
    def intake(**kwargs): seen.append(('intake', kwargs)); return {'capture_finalized': True}
    monkeypatch.setattr(runner, '_unpublished', validator)
    monkeypatch.setattr(runner.exporter, 'run', exporter)
    monkeypatch.setattr(runner.intake, 'run', intake)
    (tmp_path / 'input-000.done.json').write_text('{}')
    (tmp_path / 'input-000.intent.json').write_text(json.dumps({'delivery': {'close_reason': 'STOP'}}))
    def watch(**kwargs):
        assert kwargs['finalize'] and kwargs['export'] and kwargs['wait_for_marker_last']
        assert kwargs['max_polls'] == 512 and kwargs['deadline_seconds'] == 120
        assert runner._unpublished(tmp_path, lambda: 1) == 32
        if fail: raise ValueError('test refusal')
        assert runner.intake.run(finalize=True) == {'capture_finalized': True}
        assert runner.exporter.run(test=True) == {'status': 'exported'}
        return {'status': 'exported'}
    monkeypatch.setattr(runner, 'watch', watch)
    spec = dict(api='http://127.0.0.1:1', watch_events=str(tmp_path / 'events.jsonl'), state_dir=str(tmp_path))
    output = tmp_path / 'result.json'
    assert h.run_watch_child(spec, output) == 0
    result = json.loads(output.read_text())
    assert result['returned'] is not fail
    assert runner._unpublished is validator and runner.exporter.run is exporter and runner.intake.run is intake
    rows = h.read_events(spec['watch_events'])
    assert rows[0]['event'] == 'watch_enter' and rows[1]['event'] == 'marker_pending_validated'
    assert rows[1]['request_count'] == rows[1]['writes'] == 0
    assert seen == [('validator', str(tmp_path))] + ([] if fail else [('intake', {'finalize': True}), ('export', {'test': True})])
    if not fail:
        final = next(row for row in rows if row['event'] == 'finalize_enter')
        assert final['inputs'] == 1 and final['last_admitted_delivery'] == {'close_reason': 'STOP'}


@pytest.mark.parametrize('error', [False, True])
def test_request_terminal_event_preserves_http_outcome(tmp_path, monkeypatch, error):
    from audio_backend import recorder_inbox_runner as runner
    def request(self, method, path, **kwargs):
        self.calls.append(dict(method=method, path=path))
        if error: raise h.u.HTTPFailure(404, 'private detail not recorded')
        return {'ok': True}
    monkeypatch.setattr(h.Trace, 'request', request)
    def watch(**kwargs):
        if error:
            with pytest.raises(h.u.HTTPFailure) as caught:
                kwargs['transport'].request('GET', '/test')
            assert caught.value.status == 404
        else: assert kwargs['transport'].request('GET', '/test') == {'ok': True}
        return {'status': 'test'}
    monkeypatch.setattr(runner, 'watch', watch)
    spec = dict(api='http://127.0.0.1:1', watch_events=str(tmp_path / 'events.jsonl'), state_dir=str(tmp_path))
    h.run_watch_child(spec, tmp_path / 'result.json')
    rows = h.read_events(spec['watch_events'])
    assert rows[1]['event'] == 'request_start' and rows[1]['request_count'] == 1
    assert rows[2]['event'] == ('request_error' if error else 'request_return')
    assert rows[2]['request_count'] == 1
    if error:
        assert rows[2]['status'] == 404 and rows[2]['error_type'] == 'HTTPFailure'
        assert 'private detail' not in Path(spec['watch_events']).read_text()
