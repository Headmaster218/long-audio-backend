"""Source-only smoke controls. No native service or model is started here."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def module(name):
    path = Path(__file__).resolve().parents[1] / 'tools' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize('other', ['--input', '--input-sha256', '--recorder-delivery',
    '--recorder-span-delivery', '--recorder-batch-delivery', '--recorder-incremental-delivery'])
def test_incremental_span_conflicts_before_service(tmp_path, monkeypatch, other):
    smoke = module('strict_uploader_smoke')
    monkeypatch.setattr(sys, 'argv', ['smoke', str(tmp_path / 'out'), '--assets', 'unused',
        '--recorder-incremental-span-delivery', 'one', other, 'other'])
    def forbidden(*a, **kw): raise AssertionError('service must not start')
    monkeypatch.setattr(smoke.subprocess, 'Popen', forbidden)
    assert smoke.main() == 1
    value = json.loads((tmp_path / 'out/RESULT.json').read_bytes())
    assert value['error']['message'] == 'recorder_incremental_span_conflicts_with_other_input'
    assert not (tmp_path / 'out/config.json').exists()


@pytest.mark.parametrize('count', [1, 33])
def test_incremental_span_count_refused_before_service(tmp_path, monkeypatch, count):
    smoke = module('strict_uploader_smoke')
    monkeypatch.setattr(sys, 'argv', ['smoke', str(tmp_path / 'out'), '--assets', 'unused'] +
        ['--recorder-incremental-span-delivery', 'unused'] * count)
    def forbidden(*a, **kw): raise AssertionError('service must not start')
    monkeypatch.setattr(smoke.subprocess, 'Popen', forbidden)
    assert smoke.main() == 1
    assert json.loads((tmp_path / 'out/RESULT.json').read_bytes())['error']['message'] == 'recorder_incremental_span_input_count'


def test_fixed_span_count_stays_at_eight(tmp_path, monkeypatch):
    smoke = module('strict_uploader_smoke')
    monkeypatch.setattr(sys, 'argv', ['smoke', str(tmp_path / 'out'), '--assets', 'unused'] +
        ['--recorder-span-delivery', 'unused'] * 9)
    assert smoke.main() == 1
    assert json.loads((tmp_path / 'out/RESULT.json').read_bytes())['error']['message'] == 'recorder_batch_input_count'


def test_completed_repeat_only_exact_existing_seal():
    probe = module('strict_incremental_span_probe')
    seal = dict(method='POST', path='/pages/1/seal', body={'key': 'frozen'}, key=None, file_sha256=None)
    get = dict(seal, method='GET')
    assert probe.verify_completed_repeat([seal], [get]) == dict(no_new_sources=True, exact_frozen_seal_replays=0)
    assert probe.verify_completed_repeat([seal], [get, seal])['exact_frozen_seal_replays'] == 1
    for row in (dict(seal, method='PUT'), dict(seal, path='/pages/2/seal'),
                dict(seal, body={'key': 'new'}), dict(seal, key='new'), dict(seal, file_sha256='new')):
        with pytest.raises(RuntimeError, match='changed_mutation'):
            probe.verify_completed_repeat([seal], [row])
    with pytest.raises(RuntimeError, match='missing_initial_seal'):
        probe.verify_completed_repeat([], [])


def test_get_only_transport_refuses_before_network(monkeypatch):
    probe = module('strict_incremental_span_probe')
    def forbidden(*a, **kw): raise AssertionError('network must not start')
    monkeypatch.setattr(probe.u.HTTPTransport, 'request', forbidden)
    transport = probe.Trace('http://127.0.0.1:8765', probe.u.Guard(probe.u.Limits()), get_only=True)
    for method in ('PUT', 'POST', 'DELETE'):
        with pytest.raises(RuntimeError, match='status_attempted_mutation'):
            transport.request(method, '/unused')
    assert transport.calls == []


def test_status_mapping_and_truth_guards():
    probe = module('strict_incremental_span_probe')
    reports = [dict(sequence=2, epoch=7, frame_start=32000, frames=16000, close_reason='INTERRUPTION')]
    value = dict(status='completed', analysis_completion_verified=True, read_only=True,
        input_mapping=[dict(ordinal=0, original_sequence=2, original_epoch=7,
            original_epoch_frame_start=32000, original_epoch_frame_end=48000,
            span_start_frame=0, span_end_frame=16000)], frames=16000, inputs=1,
        span=dict(original_close_reason='INTERRUPTION', recorder_stop_observed=False,
            before_span_gap_frames=None, after_span_gap_frames=None, eos_meaning='selected_span_only'),
        capture_completeness_proven=False)
    probe.verify_status(value, reports)
    for field, changed in [('status', 'pending'), ('read_only', False),
                           ('analysis_completion_verified', False), ('frames', 48000),
                           ('capture_completeness_proven', True)]:
        with pytest.raises(RuntimeError): probe.verify_status(dict(value, **{field: changed}), reports)
    for field, changed in [('recorder_stop_observed', True), ('before_span_gap_frames', 0),
                           ('after_span_gap_frames', 0), ('eos_meaning', 'whole_capture')]:
        bad = copy.deepcopy(value); bad['span'][field] = changed
        with pytest.raises(RuntimeError): probe.verify_status(bad, reports)


def test_inventory_detects_changes(tmp_path):
    probe = module('strict_incremental_span_probe')
    path = tmp_path / 'file'; path.write_bytes(b'old')
    before = probe.inventory([tmp_path])
    path.write_bytes(b'new')
    assert probe.inventory([tmp_path]) != before
    (tmp_path / 'link').symlink_to(path)
    with pytest.raises(RuntimeError, match='symlink'): probe.inventory([tmp_path])


def test_child_reaped_when_gate_fails(tmp_path, monkeypatch):
    probe = module('strict_incremental_span_probe')
    class Process:
        pid = 123; code = None; waits = 0
        def poll(self): return self.code
        def terminate(self): self.code = -15
        def wait(self, timeout): self.waits += 1; return self.code
    process = Process()
    monkeypatch.setattr(probe.subprocess, 'Popen', lambda *a, **kw: process)
    def fail(): raise RuntimeError('gate cap')
    with pytest.raises(RuntimeError, match='gate cap'):
        probe.child_phase(SimpleNamespace(out=tmp_path, check=fail), tmp_path / 'spec', 'first')
    assert process.waits == 1
    assert json.loads((tmp_path / 'incremental-first-wait.json').read_bytes()) == dict(
        pid=123, original_popen_wait_completed=True, exit_code=-15)


def test_clean_exit_racing_gate_failure_is_still_waited(tmp_path, monkeypatch):
    probe = module('strict_incremental_span_probe')
    class Process:
        pid = 124; code = None; waits = 0
        def poll(self): return self.code
        def wait(self, timeout): self.waits += 1; return self.code
    process = Process()
    monkeypatch.setattr(probe.subprocess, 'Popen', lambda *a, **kw: process)
    def fail(): process.code = 0; raise RuntimeError('gate cap')
    with pytest.raises(RuntimeError, match='gate cap'):
        probe.child_phase(SimpleNamespace(out=tmp_path, check=fail), tmp_path / 'spec', 'first')
    assert process.waits == 1
    assert json.loads((tmp_path / 'incremental-first-wait.json').read_bytes())['exit_code'] == 0


def test_export_requires_incremental_span_before_service(tmp_path, monkeypatch):
    smoke = module('strict_uploader_smoke')
    monkeypatch.setattr(sys, 'argv', ['smoke', str(tmp_path / 'out'), '--assets', 'unused', '--export-transcript'])
    monkeypatch.setattr(smoke.subprocess, 'Popen', lambda *a, **kw: pytest.fail('no service'))
    assert smoke.main() == 1
    assert json.loads((tmp_path / 'out/RESULT.json').read_bytes())['error']['message'] == 'transcript_export_requires_incremental_span'


def test_export_phases_use_original_wait_and_explicit_names():
    probe = module('strict_incremental_span_probe')
    assert probe.PHASES[-2:] == ('export', 'export-repeat')


def test_export_verifier_requires_nonempty_primary_text(tmp_path):
    probe = module('strict_incremental_span_probe')
    result = dict(schema=probe.export.SCHEMA, status='exported', projection='primary-baseline-v1',
                  complete_content=False, nonempty_text_units=0, text_status='no_primary_text_observed')
    value = dict(result=result, requests=[], local_files_unchanged=True)
    with pytest.raises(RuntimeError, match='no_primary_speech_text'):
        probe.verify_export_repeat(value, value, tmp_path, [])
