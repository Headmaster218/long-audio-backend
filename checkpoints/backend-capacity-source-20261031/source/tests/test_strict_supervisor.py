"""Model-free launcher controls. No service listener, models, recording or device.

Synthetic preflight matches are only test seams, never runtime qualification.
Real process tests exercise the original lock and original OS child handles.
"""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from audio_backend import strict_supervisor as supervisor
from audio_backend import strict_runtime_preflight as runtime

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux procfs/flock/subreaper contract')
ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'tests/helpers/synthetic_supervisor_process.py'


def directory(tmp_path):
    data = tmp_path / 'data'
    data.mkdir(mode=0o700)
    lock = data / 'instance.lock'
    lock.write_bytes(b'unchanged-lock-bytes')
    lock.chmod(0o600)
    return data


def arguments(data, action='check'):
    return [action, '--data-dir', str(data), '--model-dir', str(data.parent / 'models'),
        '--native-diarization-model', str(data.parent / 'native.gguf'),
        '--native-diarization-library', str(data.parent / 'native.so'),
        '--recording-speaker-model', str(data.parent / 'speaker.onnx'), '--language', 'en',
        '--profile', 'strict-primary-payload-v2', '--strict-payload-source-storage', 'shared-source-v1']


def provision_assets(data):
    # Metadata fixtures only. These bytes are never loaded as models/libraries.
    for name in ('native.gguf', 'native.so', 'speaker.onnx', 'models/speaker.onnx',
                 'models/sensevoice/model.int8.onnx', 'models/sensevoice/tokens.txt',
                 'models/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx',
                 'models/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv'):
        path = data.parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic metadata fixture, not a model')


def reviewed_policy(tmp_path, schema=1):
    value = json.loads((ROOT / 'audio_backend/profiles/verified-review-experimental-v1.json').read_text())
    if schema == 2:
        from audio_backend.primary_crop_support import POLICY
        value.update(schema_version=2, primary_query_overlap_policy=POLICY)
    path = tmp_path / 'reviewed-policy.json'
    path.write_text(json.dumps(value))
    return path


def reviewed_arguments(policy):
    return ['--reviewed-primary-persons', '--verified-identity-policy', str(policy)]


def available(data):
    with (data / 'instance.lock').open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def conflict(data):
    with pytest.raises(BlockingIOError): available(data)


def until(predicate, process, timeout=15):
    end = time.monotonic() + timeout
    while not predicate():
        if process.poll() is not None:
            raise AssertionError('Synthetic supervisor exited before expected event')
        if time.monotonic() > end:
            raise AssertionError('Synthetic supervisor event deadline')
        time.sleep(.01)


@pytest.mark.parametrize('action', ['check', 'serve'])
@pytest.mark.parametrize('reviewed', [False, True])
def test_unqualified_runtime_refuses_without_assets_service_or_data_mutation(tmp_path, monkeypatch, capsys, action, reviewed):
    data = directory(tmp_path)
    def inspect(path):
        assert path == data
        conflict(data)
        return {'status': 'unqualified', 'reason': 'synthetic_unqualified'}
    monkeypatch.setattr(runtime, 'inspect_runtime', inspect)
    monkeypatch.setattr(supervisor, '_assets', lambda _: pytest.fail('Assets touched after runtime refusal'))
    monkeypatch.setattr(supervisor, '_voice_embedding_preflight', lambda _: pytest.fail('Embedding inspection after refusal'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Child started on refusal'))
    extra = reviewed_arguments(tmp_path / 'intentionally-missing-policy.json') if reviewed else []
    assert supervisor.main(arguments(data, action) + extra) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'unqualified_runtime'
    assert [p.name for p in data.iterdir()] == ['instance.lock']
    assert (data / 'instance.lock').read_bytes() == b'unchanged-lock-bytes'
    available(data)
    assert supervisor._PENDING_OWNER is None


def test_check_is_metadata_only_and_explicit_about_its_limits(tmp_path, monkeypatch, capsys):
    data = directory(tmp_path)
    monkeypatch.setattr(runtime, 'inspect_runtime', lambda _: {'status': 'matched'})
    monkeypatch.setattr(supervisor, '_assets', lambda _: conflict(data))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Check launched a child'))
    assert supervisor.main(arguments(data)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'preflight_passed'
    assert report['service'] == 'not_started' and report['models'] == 'not_loaded'
    assert report['session'] == 'not_created'
    assert report['reviewed_primary_persons_enabled'] is False
    assert report['verified_identity_policy'] == 'not_requested'
    assert report['person_bank'] == 'not_opened_or_activated'
    available(data)


@pytest.mark.parametrize('schema', [1, 2])
def test_reviewed_check_validates_product_policy_without_mutation_or_bank_activation(tmp_path, monkeypatch, capsys, schema):
    import sqlite3
    from audio_backend import verified_identity
    data = directory(tmp_path)
    provision_assets(data)
    policy = reviewed_policy(tmp_path, schema)
    original = verified_identity.policy_file
    validated = []
    def validate(path):
        conflict(data)
        validated.append(path)
        return original(path)
    monkeypatch.setattr(verified_identity, 'policy_file', validate)
    monkeypatch.setattr(runtime, 'inspect_runtime', lambda _: {'status': 'matched'})
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('Check opened a database'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Check launched a child'))
    def snapshot():
        return {str(p.relative_to(tmp_path)): (p.read_bytes(), p.stat().st_mode)
                for p in tmp_path.rglob('*') if p.is_file()}
    before = snapshot()
    assert supervisor.main(arguments(data) + reviewed_arguments(policy)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'preflight_passed'
    assert report['reviewed_primary_persons_enabled'] is True
    assert report['verified_identity_policy'] == 'validated_configuration_only'
    assert report['person_bank'] == 'not_opened_or_activated'
    assert report['models'] == 'not_loaded' and report['service'] == 'not_started'
    assert report['session'] == 'not_created'
    assert validated == [policy]
    assert snapshot() == before
    assert [p.name for p in data.iterdir()] == ['instance.lock']
    assert supervisor._PENDING_OWNER is None
    available(data)


@pytest.mark.parametrize('action', ['check', 'serve'])
@pytest.mark.parametrize('flag', ['--reviewed-primary-persons', '--verified-identity-policy'])
def test_reviewed_options_require_both_explicit_flags_before_ownership(tmp_path, monkeypatch, capsys, action, flag):
    data = directory(tmp_path)
    monkeypatch.setattr(supervisor, '_acquire', lambda _: pytest.fail('Partial opt-in acquired lock'))
    extra = [flag] if flag == '--reviewed-primary-persons' else [flag, str(reviewed_policy(tmp_path))]
    assert supervisor.main(arguments(data, action) + extra) == 1
    assert 'must be supplied together' in capsys.readouterr().err
    available(data)


@pytest.mark.parametrize('action', ['check', 'serve'])
@pytest.mark.parametrize('kind', ['missing', 'relative', 'symlink', 'parent_symlink', 'hardlink',
    'directory', 'fifo', 'empty', 'oversized', 'invalid_json', 'wrong_shape', 'extra_key',
    'missing_key', 'wrong_schema', 'not_experimental', 'threshold', 'boolean', 'nonfinite', 'duration'])
def test_reviewed_policy_path_and_exact_bounded_schema_refuse_before_launch(tmp_path, monkeypatch, kind, action):
    data = directory(tmp_path)
    provision_assets(data)
    path = reviewed_policy(tmp_path)
    value = json.loads(path.read_text())
    if kind == 'missing': path.unlink()
    elif kind == 'relative': path = Path('reviewed-policy.json')
    elif kind == 'symlink':
        alias = tmp_path / 'policy-alias'; alias.symlink_to(path); path = alias
    elif kind == 'parent_symlink':
        alias = tmp_path / 'parent-alias'; alias.symlink_to(tmp_path, target_is_directory=True)
        path = alias / path.name
    elif kind == 'hardlink': os.link(path, tmp_path / 'policy-link')
    elif kind == 'directory': path = tmp_path
    elif kind == 'fifo': path.unlink(); os.mkfifo(path)
    elif kind == 'empty': path.write_bytes(b'')
    elif kind == 'oversized': path.write_bytes(b' ' * 8193)
    elif kind == 'invalid_json': path.write_text('{')
    else:
        if kind == 'wrong_shape': value = []
        elif kind == 'extra_key': value['unrecognized'] = True
        elif kind == 'missing_key': del value['margin']
        elif kind == 'wrong_schema': value['schema_version'] = 3
        elif kind == 'not_experimental': value['experimental'] = False
        elif kind == 'threshold': value['single_threshold'] = 1.01
        elif kind == 'boolean': value['single_threshold'] = True
        elif kind == 'nonfinite': value['single_threshold'] = float('nan')
        elif kind == 'duration': value['minimum_enrollment_seconds'] = 21
        path.write_text(json.dumps(value))
    monkeypatch.setattr(runtime, 'inspect_runtime', lambda _: {'status': 'matched'})
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Invalid policy launched a child'))
    assert supervisor.main(arguments(data, action) + reviewed_arguments(path)) == 1
    assert [p.name for p in data.iterdir()] == ['instance.lock']
    assert (data / 'instance.lock').read_bytes() == b'unchanged-lock-bytes'
    assert supervisor._PENDING_OWNER is None
    available(data)


def test_busy_lock_refuses_before_runtime_inspection(tmp_path, monkeypatch):
    data = directory(tmp_path)
    monkeypatch.setattr(runtime, 'inspect_runtime', lambda _: pytest.fail('Read before ownership'))
    monkeypatch.setattr(supervisor, '_voice_embedding_preflight', lambda _: pytest.fail('Embedding inspection before ownership'))
    with (data / 'instance.lock').open('rb') as original:
        fcntl.flock(original, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert supervisor.main(arguments(data)) == 1
        conflict(data)
    available(data)


@pytest.mark.parametrize('kind', ['missing_lock', 'public_dir', 'public_lock', 'alias_dir', 'alias_lock', 'hardlink_lock'])
def test_unsafe_existing_control_paths_refuse(tmp_path, kind):
    data = directory(tmp_path)
    lock = data / 'instance.lock'
    if kind == 'missing_lock': lock.unlink()
    elif kind == 'public_dir': data.chmod(0o755)
    elif kind == 'public_lock': lock.chmod(0o644)
    elif kind == 'alias_dir':
        alias = tmp_path / 'alias'; alias.symlink_to(data, target_is_directory=True); data = alias
    elif kind == 'alias_lock':
        real = data / 'original'; lock.rename(real); lock.symlink_to(real)
    elif kind == 'hardlink_lock': os.link(lock, data / 'other')
    assert supervisor.main(arguments(data)) == 1
    assert supervisor._PENDING_OWNER is None


@pytest.mark.parametrize('extra', [['--host', '0.0.0.0'], ['--instance-lock-fd', '3'], ['--profile', 'legacy'], ['--skip-runtime-check']])
def test_no_network_override_lock_fd_or_pin_bypass(tmp_path, extra):
    data = directory(tmp_path)
    with pytest.raises(SystemExit) as caught: supervisor.main(arguments(data) + extra)
    assert caught.value.code == 2
    available(data)


@pytest.mark.parametrize('flag,value', [('--port', '0'), ('--memory-limit-gib', 'nan'),
    ('--shutdown-grace-seconds', 'inf'), ('--recording-total-mib', '0'), ('--lexical-evidence-session-mib', '1025')])
def test_invalid_numeric_options_do_not_acquire_or_inspect(tmp_path, monkeypatch, flag, value):
    data = directory(tmp_path)
    monkeypatch.setattr(supervisor, '_acquire', lambda _: pytest.fail('Invalid config acquired lock'))
    assert supervisor.main(arguments(data) + [flag, value]) == 1


def test_command_is_exact_product_profile_without_private_harness_paths(tmp_path):
    data = directory(tmp_path)
    args = supervisor._parser().parse_args(arguments(data, 'serve'))
    command = supervisor._child_command(args, 9)
    def value(flag): return command[command.index(flag) + 1]
    assert command[:4] == [sys.executable, '-I', '-B', '-c']
    assert 'audio_backend.__main__' in command[4]
    assert value('--instance-lock-fd') == '9'
    assert value('--strict-primary-policy') == 'strict-primary-payload-v2'
    assert value('--strict-payload-source-storage') == 'shared-source-v1'
    assert value('--window-seconds') == '5.25' and value('--context-seconds') == '0'
    assert value('--speaker-support-max-records') == '65536'
    assert value('--recording-primary-sampling-policy') == 'skip-bucket-v1'
    assert value('--recording-capture-mib') == '4096' and value('--recording-total-mib') == '8192'
    assert value('--strict-primary-lifetime-records') == '32768'
    assert value('--strict-primary-receipt-records') == '8192'
    assert value('--lexical-evidence-session-mib') == '256'
    assert '--moss-jobs' not in command and '--native-owned-compact-gc' not in command
    assert '--reviewed-primary-persons' not in command and '--verified-identity-policy' not in command
    assert not any('capacity_service' in item or 'audio-recovery' in item for item in command[1:])


def test_reviewed_opt_in_only_appends_the_existing_product_flags(tmp_path):
    data = directory(tmp_path)
    policy = reviewed_policy(tmp_path)
    baseline = supervisor._child_command(supervisor._parser().parse_args(arguments(data, 'serve')), 9)
    args = supervisor._parser().parse_args(arguments(data, 'serve') + reviewed_arguments(policy))
    assert supervisor._child_command(args, 9) == baseline + reviewed_arguments(policy)


def test_reviewed_serve_forwards_validated_policy_with_original_lock_held(tmp_path, monkeypatch):
    data = directory(tmp_path)
    provision_assets(data)
    policy = reviewed_policy(tmp_path)
    # The existing product validator admits exactly 8 KiB, not one byte more.
    policy.write_bytes(policy.read_bytes().ljust(8192, b' '))
    monkeypatch.setattr(runtime, 'inspect_runtime', lambda _: {'status': 'matched'})
    captured = []
    def run(owner, command, signals, grace):
        conflict(data)
        owner.lock.verify_inherited()
        baseline = supervisor._child_command(
            supervisor._parser().parse_args(arguments(data, 'serve')), owner.lock.file.fileno())
        assert command == baseline + reviewed_arguments(policy)
        captured.append(command)
        return 23  # No actual service, model or bank is started by this test.
    monkeypatch.setattr(supervisor, '_run_child', run)
    assert supervisor.main(arguments(data, 'serve') + reviewed_arguments(policy)) == 23
    assert len(captured) == 1
    assert [p.name for p in data.iterdir()] == ['instance.lock']
    assert supervisor._PENDING_OWNER is None
    available(data)


@pytest.mark.parametrize('mode,signum,expected,childcode', [
    ('exit', None, 23, 23), ('release_delay', signal.SIGINT, 130, 0),
    ('release_delay', signal.SIGTERM, 143, 0), ('ignore', signal.SIGTERM, 143, -9),
    ('orphan', None, 7, 7), ('orphan_zero', None, 1, 0),
])
def test_real_original_wait_signal_and_orphan_cleanup(tmp_path, mode, signum, expected, childcode):
    data = directory(tmp_path)
    process = subprocess.Popen([sys.executable, '-B', str(HELPER), str(data), mode],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        until(lambda: (data / 'ready').exists(), process)
        conflict(data)
        if signum is not None:
            process.send_signal(signum)
            if mode == 'release_delay':
                until(lambda: (data / 'stopping').exists(), process)
                # Child has closed its inherited duplicate, but is still alive.
                # The supervisor's original hold is the remaining authority.
                conflict(data)
                time.sleep(.1)
                conflict(data)
                (data / 'allow_exit').write_text('test has checked continuous parent ownership')
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
        result = json.loads((data / 'result').read_text())
        assert result == {'result': expected, 'child_returncode': childcode,
                          'positive_wait': True, 'no_descendants': True}
        available(data)
        if (data / 'grandchild').exists():
            assert not Path('/proc/' + (data / 'grandchild').read_text()).exists()
        assert (data / 'instance.lock').read_bytes() == b'unchanged-lock-bytes'
    finally:
        (data / 'allow_exit').touch()
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired: process.kill()
        process.wait(timeout=15)


def test_child_cli_preserves_existing_default_context_and_support_cap(tmp_path, monkeypatch):
    from audio_backend import __main__ as server
    captured = []
    monkeypatch.setattr(server, '_run_app', lambda settings, *a, **k: captured.append(settings))
    server.main(['--data-dir', str(tmp_path)])
    assert captured[-1].context_seconds == 2 and captured[-1].speaker_support_max_records == 4096
    server.main(['--data-dir', str(tmp_path), '--context-seconds', '0',
                 '--speaker-support-max-records', '65536', '--window-seconds', '5.25'])
    assert captured[-1].context_seconds == 0 and captured[-1].speaker_support_max_records == 65536
    assert captured[-1].window_seconds == 5.25


def test_descendant_import_uses_same_checkout_from_foreign_cwd_and_pythonpath(tmp_path):
    data = directory(tmp_path)
    foreign = tmp_path / 'foreign'
    (foreign / 'audio_backend').mkdir(parents=True)
    (foreign / 'audio_backend/__init__.py').write_text("raise RuntimeError('wrong source origin')\n")
    env = dict(os.environ, PYTHONPATH=str(foreign))
    process = subprocess.Popen([sys.executable, '-B', str(HELPER), str(data), 'origin'],
        cwd=foreign, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
        assert json.loads((data / 'result').read_text())['result'] == 0
        available(data)
    finally:
        (data / 'allow_exit').touch()
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired: process.kill()
        process.wait(timeout=15)


def test_launch_failure_with_unknown_child_inventory_retains_lock(tmp_path, monkeypatch):
    data = directory(tmp_path)
    owner = supervisor._Owner(supervisor._acquire(data))
    calls = []
    def children():
        calls.append(True)
        if len(calls) == 1: return []
        raise OSError('synthetic observation denied')
    monkeypatch.setattr(supervisor, '_children', children)
    monkeypatch.setattr(supervisor, '_subreaper', lambda: None)
    def launch(*a, **k): raise OSError('synthetic exec failure')
    monkeypatch.setattr(subprocess, 'Popen', launch)
    try:
        with pytest.raises(OSError, match='observation denied'):
            supervisor._run_child(owner, ['not-executed'], supervisor._Signals(), 1)
        assert owner.uncertain and not owner.complete
        conflict(data)
    finally:
        owner.lock.release()  # Fixture proves no actual child was launched.


def test_signal_request_before_launch_creates_no_child(tmp_path, monkeypatch):
    data = directory(tmp_path)
    def inspect(_):
        os.kill(os.getpid(), signal.SIGTERM)
        return {'status': 'matched'}
    monkeypatch.setattr(runtime, 'inspect_runtime', inspect)
    monkeypatch.setattr(supervisor, '_assets', lambda _: None)
    monkeypatch.setattr(supervisor, '_voice_embedding_preflight', lambda _: pytest.fail('Stop request inspected embedding'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Stop request launched child'))
    assert supervisor.main(arguments(data, 'serve')) == 143
    available(data)


@pytest.mark.parametrize('policy_schema', [None, 1, 2])
def test_complete_supervisor_command_constructs_the_expected_product_settings(tmp_path, monkeypatch, policy_schema):
    from audio_backend import __main__ as server
    data = directory(tmp_path)
    lock = supervisor._acquire(data)
    captured = []
    monkeypatch.setattr(server, '_run_app', lambda settings, *a, **k: captured.append(settings))
    policy = reviewed_policy(tmp_path, policy_schema) if policy_schema is not None else None
    extra = reviewed_arguments(policy) if policy is not None else []
    args = supervisor._parser().parse_args(arguments(data, 'serve') + extra)
    try:
        # Same-process invocation must duplicate; production Popen has separate fd tables.
        command = supervisor._child_command(args, os.dup(lock.file.fileno()))
        server.main(command[5:])
        cfg = captured[0]
        assert cfg.strict_primary_enabled and cfg.strict_primary_policy == 'strict-primary-payload-v2'
        assert cfg.strict_payload_source_storage == 'shared-source-v1'
        assert cfg.window_seconds == 5.25 and cfg.context_seconds == 0
        assert cfg.speaker_support_enabled and cfg.speaker_support_max_records == 65536
        assert cfg.recording_primary_sampling_policy == 'skip-bucket-v1'
        assert cfg.strict_primary_lifetime_records == 32768 and cfg.strict_primary_receipt_records == 8192
        assert cfg.lexical_evidence_session_bytes == 256 * 1024**2
        assert cfg.recording_capture_bytes == 4 * 1024**3 and cfg.recording_total_bytes == 8 * 1024**3
        assert cfg.recording_disk_reserve_bytes == 11 * 1024**3 // 2
        assert cfg.native_diarization_threads == cfg.cpu_threads == 2
        assert cfg.process_memory_limit_bytes == 6 * 1024**3
        assert not cfg.moss_jobs_enabled and not cfg.native_owned_compact_gc_enabled
        assert cfg.reviewed_primary_persons_enabled is (policy is not None)
        assert cfg.verified_identity_policy_file == (str(policy) if policy is not None else None)
        assert cfg.verified_identity_policy == (json.loads(policy.read_text()) if policy is not None else None)
        from audio_backend.settings import payload_live_enabled
        assert payload_live_enabled(cfg)
        conflict(data)
    finally:
        lock.release()


@pytest.mark.parametrize('mode', ['wait_failure', 'inventory_failure', 'logging_failure'])
def test_uncertain_positive_wait_keeps_cli_alive_and_original_lock_held(tmp_path, mode):
    data = directory(tmp_path)
    process = subprocess.Popen([sys.executable, '-B', str(HELPER), str(data), mode],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        until(lambda: (data / 'uncertain').exists(), process)
        conflict(data)
        for signum in (signal.SIGINT, signal.SIGTERM):
            process.send_signal(signum)
            time.sleep(.1)
            assert process.poll() is None
            conflict(data)
        assert not (data / 'result').exists()
    finally:
        if process.poll() is None: process.kill()
        stdout, stderr = process.communicate(timeout=15)
    if mode != 'logging_failure':
        assert 'Ownership is unresolved; retaining the original lock' in stderr
    available(data)  # Only the fixture's explicit SIGKILL ends this hold.


@pytest.fixture
def structural_voice_preflight(tmp_path, monkeypatch):
    """Synthetic byte binding only; never evidence for a real runtime or voice."""
    import copy
    import hashlib
    from types import SimpleNamespace
    from audio_backend import embedding_space as space
    from audio_backend import recording_speaker_probe_runner as probe
    from audio_backend import moss_handy_profile
    data = directory(tmp_path)
    provision_assets(data)
    args = supervisor._parser().parse_args(arguments(data))
    binding = copy.deepcopy(space._AUDITED_BINDINGS[probe.BINDING_ID])
    binding['model']['sha256'] = hashlib.sha256(args.recording_speaker_model.read_bytes()).hexdigest()
    binding_id = space._digest(binding)
    monkeypatch.setitem(space._AUDITED_BINDINGS, binding_id, binding)
    monkeypatch.setattr(probe, 'BINDING_ID', binding_id)
    monkeypatch.setattr(probe, 'MODEL_SHA256', binding['model']['sha256'])
    class Config:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class Extractor:
        def __init__(self, *a, **k): pytest.fail('Preflight constructed an extractor')
    roots = tmp_path / 'synthetic-site'; roots.mkdir()
    artifact = roots / 'runtime.so'; artifact.write_bytes(b'not a real runtime')
    sherpa = SimpleNamespace(__file__=str(roots / 'sherpa.py'),
        SpeakerEmbeddingExtractorConfig=Config, SpeakerEmbeddingExtractor=Extractor)
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', sherpa)
    monkeypatch.setitem(sys.modules, 'numpy', SimpleNamespace(__file__=str(roots / 'numpy.py')))
    monkeypatch.setattr(moss_handy_profile, 'runtime_site_paths', lambda: (roots,))
    observed_runtime = copy.deepcopy(binding['runtime'])
    numpy_runtime = copy.deepcopy(binding['output']['numpy'])
    defaults = {key: binding['frontend'][key] for key in (
        'sampling_rate', 'feature_dim', 'low_freq', 'high_freq', 'dither', 'normalize_samples', 'snip_edges')}
    def snapshot(attestor, actual):
        assert actual is sherpa
        attestor._runtime_paths = {'sherpa_extension': artifact}
        return observed_runtime, numpy_runtime, defaults, []
    monkeypatch.setattr(space.SherpaSpaceAttestor, '_runtime_snapshot', snapshot)
    return SimpleNamespace(data=data, args=args, space=space, probe=probe, sherpa=sherpa,
        observed_runtime=observed_runtime, numpy_runtime=numpy_runtime, defaults=defaults,
        roots=roots, artifact=artifact)


def test_voice_preflight_reuses_begin_without_extractor_database_or_child(structural_voice_preflight, monkeypatch):
    import sqlite3
    fixture = structural_voice_preflight
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('Voice check opened a database'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Voice check created a child'))
    original = fixture.space.SherpaSpaceAttestor.begin
    calls = []
    def begin(attestor, sherpa, config, model_hash):
        calls.append((config.provider, config.num_threads, config.model, model_hash))
        return original(attestor, sherpa, config, model_hash)
    monkeypatch.setattr(fixture.space.SherpaSpaceAttestor, 'begin', begin)
    report = supervisor._voice_embedding_preflight(fixture.args)
    assert report['status'] == 'preconstruction_supported'
    assert report['reason'] == 'existing_probe_binding_match'
    assert report['expected_binding_id'] == fixture.probe.BINDING_ID
    assert report['expected_space_id'] == fixture.probe.expected_space()[1]
    assert report['extractor'] == 'not_constructed' and report['inference'] == 'not_run'
    assert report['quality'] == 'not_evaluated'
    assert calls == [('cpu', 2, str(fixture.args.recording_speaker_model), fixture.probe.MODEL_SHA256)]


@pytest.mark.parametrize('damage,reason', [
    ('sherpa', 'embedding_runtime_model_unverified'), ('numpy', 'embedding_runtime_model_unverified'),
    ('model', 'embedding_model_bytes_unverified'), ('frontend', 'embedding_frontend_unverified'),
    ('platform', 'embedding_platform_unverified'), ('descriptor', 'probe_embedding_space_mismatch')])
def test_voice_preflight_preserves_exact_attestor_rejections(structural_voice_preflight, monkeypatch, damage, reason):
    fixture = structural_voice_preflight
    if damage == 'sherpa': fixture.observed_runtime['artifacts']['sherpa_extension'] = 'f' * 64
    elif damage == 'numpy': fixture.numpy_runtime['version'] = 'unverified'
    elif damage == 'model': fixture.args.recording_speaker_model.write_bytes(b'changed model')
    elif damage == 'frontend': fixture.defaults['snip_edges'] = True
    elif damage == 'platform': monkeypatch.setattr(fixture.space.platform, 'system', lambda: 'unverified')
    else:
        expected, space_id = fixture.probe.expected_space()
        expected['extractor']['num_threads'] = 1
        monkeypatch.setattr(fixture.probe, 'expected_space', lambda: (expected, space_id))
    report = supervisor._voice_embedding_preflight(fixture.args)
    assert report['status'] == 'unsupported' and report['reason'] == reason
    assert report['extractor'] == 'not_constructed' and report['inference'] == 'not_run'


@pytest.mark.parametrize('error,reason', [
    (ImportError, 'embedding_dependency_unavailable'), (OSError, 'embedding_artifact_read_failed'),
    (ValueError, 'embedding_inspection_failed'), (RuntimeError, 'embedding_inspection_failed')])
def test_voice_preflight_sanitizes_inspection_errors(structural_voice_preflight, monkeypatch, error, reason):
    fixture = structural_voice_preflight
    def fail(*a, **k): raise error('/private/model/path: private native exception')
    monkeypatch.setattr(fixture.space.SherpaSpaceAttestor, 'begin', fail)
    report = supervisor._voice_embedding_preflight(fixture.args)
    assert report['status'] == 'inspection_failed' and report['reason'] == reason
    assert 'private' not in json.dumps(report)


def test_voice_preflight_rejects_foreign_runtime_origin(structural_voice_preflight):
    fixture = structural_voice_preflight
    fixture.sherpa.__file__ = str(fixture.roots.parent / 'foreign/sherpa.py')
    report = supervisor._voice_embedding_preflight(fixture.args)
    assert report['status'] == 'inspection_failed' and report['reason'] == 'foreign_runtime_origin'


@pytest.mark.parametrize('reviewed', [False, True])
def test_voice_preflight_is_advisory_and_separate_from_readonly_bank_configuration(
        structural_voice_preflight, monkeypatch, capsys, reviewed):
    import sqlite3
    fixture = structural_voice_preflight
    fixture.observed_runtime['artifacts']['sherpa_extension'] = 'f' * 64
    extra = reviewed_arguments(reviewed_policy(fixture.data.parent)) if reviewed else []
    monkeypatch.setattr(runtime, 'inspect_runtime', lambda _: {'status': 'matched'})
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('Check opened a database'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Check launched a child'))
    before = {p: p.read_bytes() for p in fixture.data.parent.rglob('*') if p.is_file()}
    assert supervisor.main(arguments(fixture.data) + extra) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'preflight_passed'
    assert report['voice_embedding']['status'] == 'unsupported'
    assert report['reviewed_primary_persons_enabled'] is reviewed
    assert report['person_bank'] == 'not_opened_or_activated'
    assert report['models'] == 'not_loaded' and report['service'] == 'not_started'
    assert {p: p.read_bytes() for p in fixture.data.parent.rglob('*') if p.is_file()} == before
    available(fixture.data)


def test_serve_reports_advisory_voice_failure_before_dispatch(structural_voice_preflight, monkeypatch, capsys):
    fixture = structural_voice_preflight
    fixture.observed_runtime['artifacts']['sherpa_extension'] = 'f' * 64
    monkeypatch.setattr(runtime, 'inspect_runtime', lambda _: {'status': 'matched'})
    def dispatch(owner, command, signals, grace):
        conflict(fixture.data)
        notice = capsys.readouterr().err
        assert 'embedding_runtime_model_unverified' in notice
        assert 'continuing the strict service' in notice
        assert command == supervisor._child_command(
            supervisor._parser().parse_args(arguments(fixture.data, 'serve')), owner.lock.file.fileno())
        return 23
    monkeypatch.setattr(supervisor, '_run_child', dispatch)
    assert supervisor.main(arguments(fixture.data, 'serve')) == 23
    available(fixture.data)


def test_stop_during_voice_inspection_prevents_dispatch(structural_voice_preflight, monkeypatch):
    fixture = structural_voice_preflight
    monkeypatch.setattr(runtime, 'inspect_runtime', lambda _: {'status': 'matched'})
    def inspect(_):
        os.kill(os.getpid(), signal.SIGTERM)
        return {'status': 'inspection_failed'}
    monkeypatch.setattr(supervisor, '_voice_embedding_preflight', inspect)
    monkeypatch.setattr(supervisor, '_run_child', lambda *a: pytest.fail('Stop request launched child'))
    assert supervisor.main(arguments(fixture.data, 'serve')) == 143
    available(fixture.data)


@pytest.mark.parametrize('action', ['check', 'serve'])
def test_real_cli_refuses_unqualified_or_missing_assets_without_target_mutation(tmp_path, action):
    data = directory(tmp_path)
    result = subprocess.run([sys.executable, '-B', '-m', 'audio_backend.strict_supervisor',
        *arguments(data, action)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=30)
    # Actual current qualification is evaluated, never monkeypatched. Even an
    # independently matching runtime must refuse these intentionally absent assets.
    assert result.returncode in (1, 2), (result.stdout, result.stderr)
    assert [p.name for p in data.iterdir()] == ['instance.lock']
    assert (data / 'instance.lock').read_bytes() == b'unchanged-lock-bytes'
    available(data)
