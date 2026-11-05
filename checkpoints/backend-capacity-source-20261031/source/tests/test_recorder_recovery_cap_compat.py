"""Exact historical recovery budget identities; no service or model execution."""
from dataclasses import replace
import json
from pathlib import Path
import pytest

from audio_backend import recorder_inbox_runner as runner
from test_recorder_inbox_export_recovery import ready, fake_recovery
from test_recorder_inbox_runner import invoke
from test_recorder_generation_transition import NoHTTP, files


def audit(output):
    path = Path(output)/'recovery.jsonl'
    return path, [json.loads(line) for line in path.read_text().splitlines()]


def write_audit(path, rows):
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))


def fixture(tmp_path, cap):
    config = dict(output_dir=str(tmp_path/'export'), job_dir=str(tmp_path/'job'),
                  session_id='known', capture_id='extended')
    intent = dict(schema=runner.SCHEMA, config_sha256='0'*64, kind='export',
                  input_intents_sha256='1'*64, recovery_attempts=2)
    fake_recovery(**config, recovery_limits=runner.export_recovery.RecoveryLimits(attempts=2, requests=cap))
    return config, intent


def validate(tmp_path, config, intent):
    return runner._recovery_export_receipt(tmp_path, config, intent, runner.u.Guard(runner.u.Limits()))


@pytest.mark.parametrize('cap', [41050, 73978])
@pytest.mark.parametrize('attempts', [2,3])
def test_both_published_receipt_policies_repeat_without_http(tmp_path, monkeypatch, cap, attempts):
    config, root, peer = ready(tmp_path, monkeypatch)
    calls = []
    def historical(**kw):
        calls.append(kw['recovery_limits'].requests)
        return fake_recovery(**(kw | dict(recovery_limits=replace(kw['recovery_limits'], requests=cap))))
    monkeypatch.setattr(runner.export_recovery, 'run', historical)
    result = invoke(root, peer, export=True, export_recovery_attempts=attempts)
    assert result['status'] == 'exported'
    assert calls == [73978]  # The writer's existing default has not changed.
    _, rows = audit(config['output_dir']); assert rows[0]['requests_bound'] == cap
    assert rows[0]['attempts'] == attempts
    before = files(tmp_path)
    repeated = invoke(root, NoHTTP())
    assert repeated['status'] == 'exported' and repeated['network_requests'] == 0
    assert repeated['export_attempts'] == 2 and repeated['revision_conflicts'] == 1
    assert files(tmp_path) == before


@pytest.mark.parametrize('cap', [41050, 73978])
def test_reader_constructs_limits_from_recorded_policy(tmp_path, monkeypatch, cap):
    config, intent = fixture(tmp_path, cap); original = runner.export_recovery.RecoveryLimits; seen = []
    def inspect(**kw):
        seen.append(dict(kw)); return original(**kw)
    monkeypatch.setattr(runner.export_recovery, 'RecoveryLimits', inspect)
    validate(tmp_path, config, intent)
    assert seen == [dict(attempts=2, requests=cap)]


@pytest.mark.parametrize('bad', [41049,41051,73977,73979,0,-1,False,True,41050.0,'41050',None])
def test_unknown_or_noninteger_policy_is_refused_without_mutation(tmp_path, bad):
    config, intent = fixture(tmp_path, 41050)
    path, rows = audit(config['output_dir']); rows[0]['requests_bound'] = bad; write_audit(path, rows)
    before = files(tmp_path)
    with pytest.raises(runner.Refused, match='recovery_audit_binding'):
        validate(tmp_path, config, intent)
    assert files(tmp_path) == before


@pytest.mark.parametrize('cap', [41050,73978])
@pytest.mark.parametrize('extra', [0,1])
def test_recorded_request_count_uses_own_cap_without_expansion(tmp_path, monkeypatch, cap, extra):
    # The real response-byte ceiling is tighter than either request cap. Lower
    # only the synthetic per-request reservation to isolate this count guard.
    monkeypatch.setattr(runner.u, 'JSON_BYTES', 1)
    config, intent = fixture(tmp_path, cap)
    path, rows = audit(config['output_dir'])
    rows[-1]['requests'] = cap+extra; rows[-1]['reserved_network_bytes'] = cap+extra
    write_audit(path, rows); before = files(tmp_path)
    if extra:
        with pytest.raises(runner.Refused, match='recovery_audit_budget'):
            validate(tmp_path, config, intent)
    else:assert validate(tmp_path, config, intent)['export_attempts'] == 2
    assert files(tmp_path) == before


@pytest.mark.parametrize('field,value', [
    ('schema','foreign-schema'), ('job_dir','/foreign/job'), ('session_id','foreign'),
    ('capture_id','foreign'), ('attempts',3), ('remote_read_only',False),
    ('inference_replay',True), ('reserved_network_bound',268435457),
    ('output_bound',67108865), ('audit_output_bound',16385), ('extra',True),
])
def test_other_header_and_source_binding_stay_exact(tmp_path, field, value):
    config, intent = fixture(tmp_path, 41050)
    path, rows = audit(config['output_dir']); rows[0][field] = value; write_audit(path, rows)
    with pytest.raises(runner.Refused, match='recovery_audit_binding'):
        validate(tmp_path, config, intent)


@pytest.mark.parametrize('cap', [41050,73978])
def test_switching_to_other_known_cap_cannot_rewrite_committed_receipt(tmp_path, monkeypatch, cap):
    config, root, peer = ready(tmp_path, monkeypatch)
    def historical(**kw):
        return fake_recovery(**(kw | dict(recovery_limits=replace(kw['recovery_limits'], requests=cap))))
    monkeypatch.setattr(runner.export_recovery, 'run', historical)
    assert invoke(root, peer, export=True, export_recovery_attempts=2)['status'] == 'exported'
    path, rows = audit(config['output_dir']); rows[0]['requests_bound'] = 73978 if cap == 41050 else 41050
    write_audit(path, rows); before = files(tmp_path)
    with pytest.raises(runner.Refused, match='export_receipt_changed'):
        invoke(root, NoHTTP())
    assert files(tmp_path) == before


@pytest.mark.parametrize('part', ['partial','artifact','manifest','unknown_file','begin','revision','reservation'])
def test_known_policy_does_not_relax_tree_or_record_validation(tmp_path, part):
    config, intent = fixture(tmp_path, 41050); output = Path(config['output_dir'])
    if part in ('partial','artifact','manifest','unknown_file'):
        name = {'partial':'attempt-01/unexpected.partial', 'artifact':'attempt-02/transcript.txt',
                'manifest':'attempt-02/complete.json','unknown_file':'unexpected.json'}[part]
        with (output/name).open('ab') as stream:stream.write(b'changed')
    else:
        path, rows = audit(output)
        if part == 'begin':rows[1]['output'] = 'attempt-02'
        if part == 'revision':rows[-1]['transcript_revision'] = 'b'*64
        if part == 'reservation':rows[-1]['reserved_network_bytes'] += 1
        write_audit(path, rows)
    with pytest.raises((runner.Refused, ValueError)):
        validate(tmp_path, config, intent)
