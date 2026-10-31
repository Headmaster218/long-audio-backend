"""Finite model-free configuration and denial contracts for primary-only mode.

Settings are valid when constructed. Live cases use the real app lifecycle,
registered capture/authority/permit and SQLite owner from the pipeline fixture.
These tests neither load models nor launch the Worker, child processes or a
listener. Synthetic runtime qualification is not deployment qualification.
"""
import builtins
from pathlib import Path
import subprocess

import pytest

from audio_backend.settings import (
    Settings, coordinator_enabled, primary_only_configuration, primary_only_enabled,
)
from audio_backend.native_owned_support_cache import ReadError
from test_primary_only_pipeline import primary_app_factory, primary_options


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    """Tripwires supplement real product gates; none replaces a gate."""
    from audio_backend import moss_handy_controller, moss_handy_profile, moss_handy_runner

    forbidden_imports = frozenset((
        'torch', 'onnxruntime', 'transformers', 'funasr', 'pyannote',
        'llama_cpp', 'sherpa_onnx', 'whisper', 'faster_whisper',
    ))
    original_import = builtins.__import__
    original_open = builtins.open
    original_path_open = Path.open
    calls = []

    def forbidden(*args, **kwargs):
        calls.append('external_execution')
        pytest.fail('Configuration/denial case attempted a model or child operation')

    def guarded_import(name, *args, **kwargs):
        if name.partition('.')[0] in forbidden_imports:
            calls.append('runtime_import:' + name)
            pytest.fail('External inference runtime import is forbidden: ' + name)
        return original_import(name, *args, **kwargs)

    def model_path(value):
        if not isinstance(value, (str, bytes, Path)):
            return False
        raw = str(value).lower()
        return (any(marker in raw for marker in ('absent-synthetic', 'absent-primary',
            'absent-moss', 'synthetic-native-model', 'synthetic-native-library')) or raw.endswith((
            '.onnx', '.gguf', '.safetensors', '.pt', '.pth', '.bin')))

    def guarded_open(file, *args, **kwargs):
        if model_path(file):
            forbidden()
        return original_open(file, *args, **kwargs)

    def guarded_path_open(path, *args, **kwargs):
        if model_path(path):
            forbidden()
        return original_path_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded_import)
    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr(Path, 'open', guarded_path_open)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(moss_handy_controller.OwnedDriver, '__init__', forbidden)
    monkeypatch.setattr(moss_handy_profile, 'verify_model', forbidden)
    monkeypatch.setattr(moss_handy_profile, 'check_loader_context', forbidden)
    monkeypatch.setattr(moss_handy_controller, 'check_loader_context', forbidden)
    monkeypatch.setattr(moss_handy_runner, 'verify_model', forbidden)
    monkeypatch.setattr(moss_handy_runner, 'one_call', forbidden)
    yield calls
    assert calls == []


def _mode_options(root, mode):
    root = Path(root)
    base = {'data_dir': root, 'model_dir': root / 'models'}
    if mode == 'default':
        return base
    if mode == 'passive':
        return dict(base, start_worker=False, recording_speaker_history_read_enabled=True)
    if mode == 'ordinary':
        return dict(base, asr_backend='sensevoice', diarization_backend='nemotron',
            native_diarization_model='synthetic', native_diarization_library='synthetic',
            native_owned_support_enabled=True)
    options = dict(primary_options(root))
    if mode == 'primary':
        return options
    options.update(moss_jobs_enabled=True,
        moss_jobs_model_path=str(root / 'ABSENT-SYNTHETIC-MOSS-MODEL'))
    if mode == 'combined':
        options['moss_automatic_enabled'] = True
    elif mode == 'manual':
        options.update(moss_automatic_enabled=False,
            recording_primary_observation_policy='off', recording_speaker_links_enabled=False,
            recording_speaker_model_path=None, native_owned_bounded_core_support_enabled=False)
    else:
        raise AssertionError(mode)
    return options


def _base_cli(root):
    options = primary_options(root)
    return ['--data-dir', str(options['data_dir']), '--model-dir', str(options.get('model_dir', Path(root) / 'models'))]


def _primary_cli(root):
    options = primary_options(root)
    return _base_cli(root) + [
        '--asr-backend', 'sensevoice', '--diarization-backend', 'nemotron',
        '--native-diarization-model', str(options['native_diarization_model']),
        '--native-diarization-library', str(options['native_diarization_library']),
        '--native-owned-support', '--native-owned-cells', '--native-owned-core-seams',
        '--native-owned-bounded-core-support', '--lexical-evidence',
        '--recording-speaker-links', '--recording-speaker-model', options['recording_speaker_model_path'],
        '--recording-primary-observation-policy', 'primary-clean-history-v1',
    ]


def _intercept_cli(monkeypatch):
    import audio_backend.app as app_module
    import uvicorn
    calls = {'settings': [], 'servers': []}
    token = object()

    def create(settings):
        assert type(settings) is Settings
        calls['settings'].append(settings)
        return token

    def run(app, **kwargs):
        assert app is token
        calls['servers'].append(kwargs)

    monkeypatch.setattr(app_module, 'create_app', create)
    monkeypatch.setattr(uvicorn, 'run', run)
    return calls


def _disabled(call):
    with pytest.raises(ReadError) as error:
        call()
    assert (error.value.status_code, error.value.reason) == (409, 'feature_disabled')


def _no_dispatch(h):
    assert not h.jobs._records and not h.jobs._keys
    assert h.jobs._live is None and h.jobs._primary_pending is None
    assert h.jobs.permit._optional is None and not h.owner._pins
    assert not h.probes and not h.natives and not h.drivers


@pytest.mark.parametrize('mode', ['default', 'ordinary', 'passive', 'manual', 'combined', 'primary'])
def test_configuration_modes_are_valid_at_construction(tmp_path, mode):
    options = _mode_options(tmp_path, mode)
    settings = Settings(**options)
    assert primary_only_enabled(settings) is (mode == 'primary')
    assert coordinator_enabled(settings) is (mode in ('manual', 'combined', 'primary'))
    assert settings.native_owned_bounded_core_support_seconds == .75
    assert settings.recording_primary_observation_interval_seconds == 60
    assert settings.moss_jobs_enabled is (mode in ('manual', 'combined'))
    assert settings.moss_automatic_enabled is (mode == 'combined')
    if mode == 'primary':
        assert settings.moss_jobs_model_path is None
        assert settings.recording_primary_observation_policy == 'primary-clean-history-v1'
    elif mode in ('default', 'ordinary', 'passive', 'manual'):
        assert settings.recording_primary_observation_policy == 'off'
    assert not Path(settings.data_dir).joinpath('jobs.sqlite3').exists()
    if settings.recording_speaker_model_path is not None:
        assert not Path(settings.recording_speaker_model_path).exists()


def test_missing_primary_prerequisites_fail_at_construction(tmp_path):
    options = primary_options(tmp_path)
    assert primary_only_enabled(Settings(**options))
    for field in (
        'recording_speaker_links_enabled', 'native_owned_support_enabled',
        'native_owned_cells_enabled', 'native_owned_core_seams_enabled',
        'native_owned_bounded_core_support_enabled', 'lexical_evidence_enabled', 'start_worker',
    ):
        with pytest.raises(ValueError):
            Settings(**dict(options, **{field: False}))
    for field in ('recording_speaker_model_path', 'native_diarization_model', 'native_diarization_library'):
        with pytest.raises(ValueError):
            Settings(**dict(options, **{field: None}))
    for path in ('', 'relative-speaker.onnx'):
        with pytest.raises(ValueError):
            Settings(**dict(options, recording_speaker_model_path=path))


def test_unsupported_primary_configurations_fail_at_construction(tmp_path):
    options = primary_options(tmp_path)
    variants = (
        {'asr_backend': 'whisper'}, {'diarization_backend': 'pyannote'},
        {'inference_device': 'vulkan'}, {'overlap_backend': 'mossformer2_experimental'},
        {'native_owned_seam_cells_enabled': True},
        {'recording_primary_observation_policy': 'off'},
        {'recording_primary_observation_policy': 'unreviewed'},
        {'recording_primary_observation_interval_seconds': 59},
        {'recording_primary_observation_interval_seconds': 60.0},
        {'moss_automatic_enabled': True},
        {'moss_jobs_enabled': True, 'moss_jobs_model_path': str(tmp_path / 'ABSENT-SYNTHETIC-MOSS-MODEL')},
    )
    for change in variants:
        with pytest.raises(ValueError):
            Settings(**dict(options, **change))


def test_disabled_moss_model_cannot_be_supplied_or_inferred(tmp_path):
    path = str(tmp_path / 'ABSENT-SYNTHETIC-MOSS-MODEL')
    for options in ({'data_dir': tmp_path}, primary_options(tmp_path)):
        with pytest.raises(ValueError):
            Settings(**dict(options, moss_jobs_model_path=path))
    settings = Settings(**primary_options(tmp_path))
    assert settings.moss_jobs_model_path is None
    assert not settings.moss_jobs_enabled and not settings.moss_automatic_enabled
    assert not Path(path).exists()


def test_cli_explicit_primary_flags_agree_with_settings(monkeypatch, tmp_path):
    from audio_backend.__main__ import main
    calls = _intercept_cli(monkeypatch)
    main(_primary_cli(tmp_path))
    assert len(calls['settings']) == len(calls['servers']) == 1
    actual = calls['settings'][0]
    expected_options = dict(primary_options(tmp_path))
    expected_options.setdefault('model_dir', tmp_path / 'models')
    expected = Settings(**expected_options)
    assert primary_only_configuration(actual) == primary_only_configuration(expected)
    assert primary_only_enabled(actual)
    assert actual.native_owned_bounded_core_support_seconds == .75
    assert actual.moss_jobs_model_path is None
    assert calls['servers'] == [{'host': '127.0.0.1', 'port': 8765, 'workers': 1}]


def test_cli_legacy_modes_keep_explicit_semantics(monkeypatch, tmp_path):
    from audio_backend.__main__ import main
    calls = _intercept_cli(monkeypatch)
    base = _base_cli(tmp_path)
    manual = base + ['--diarization-backend', 'nemotron',
        '--native-diarization-model', 'synthetic', '--native-diarization-library', 'synthetic',
        '--native-owned-support', '--moss-jobs', '--moss-jobs-model',
        str(tmp_path / 'ABSENT-SYNTHETIC-MOSS-MODEL')]
    combined = _primary_cli(tmp_path) + ['--moss-jobs', '--moss-automatic',
        '--moss-jobs-model', str(tmp_path / 'ABSENT-SYNTHETIC-MOSS-MODEL')]
    for args in (base, base + ['--recording-speaker-history-read'], manual, combined):
        main(args)
    assert len(calls['settings']) == len(calls['servers']) == 4
    default, passive, manual_settings, combined_settings = calls['settings']
    assert not coordinator_enabled(default) and not coordinator_enabled(passive)
    assert passive.recording_speaker_history_read_enabled
    assert manual_settings.moss_jobs_enabled and not manual_settings.moss_automatic_enabled
    assert combined_settings.moss_jobs_enabled and combined_settings.moss_automatic_enabled
    assert not any(primary_only_enabled(settings) for settings in calls['settings'])


def test_cli_missing_prerequisites_and_fragment_flags_are_rejected(monkeypatch, tmp_path):
    from audio_backend.__main__ import main
    calls = _intercept_cli(monkeypatch)
    args = _primary_cli(tmp_path)
    for flag, field in (
        ('--native-owned-bounded-core-support', 'native_owned_bounded_core_support_enabled'),
        ('--lexical-evidence', 'lexical_evidence_enabled'),
        ('--native-owned-support', 'native_owned_support_enabled'),
        ('--native-owned-cells', 'native_owned_cells_enabled'),
        ('--native-owned-core-seams', 'native_owned_core_seams_enabled'),
    ):
        with pytest.raises(ValueError):
            Settings(**dict(primary_options(tmp_path), **{field: False}))
        with pytest.raises((ValueError, SystemExit)) as error:
            main([value for value in args if value != flag])
        if isinstance(error.value, SystemExit):
            assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        main(args + ['--fragment-cpu-check'])
    assert error.value.code == 2
    assert calls == {'settings': [], 'servers': []}


def test_cli_help_and_defaults_expose_only_explicit_new_flags(monkeypatch, tmp_path, capsys):
    from audio_backend.__main__ import main
    calls = _intercept_cli(monkeypatch)
    with pytest.raises(SystemExit) as error:
        main(['--help'])
    assert error.value.code == 0
    help_text = ' '.join(capsys.readouterr().out.split())
    assert '--native-owned-bounded-core-support' in help_text
    assert '--lexical-evidence' in help_text
    assert 'clean-primary' in help_text and 'automatic MOSS' in help_text
    assert '--native-owned-bounded-core-support-seconds' not in help_text
    assert calls == {'settings': [], 'servers': []}
    main(_base_cli(tmp_path))
    settings = calls['settings'][0]
    assert not settings.native_owned_bounded_core_support_enabled
    assert not settings.lexical_evidence_enabled
    assert not settings.recording_speaker_links_enabled
    assert settings.recording_primary_observation_policy == 'off'
    assert settings.native_owned_bounded_core_support_seconds == .75


def test_fragment_provider_is_refused_before_app_initialization(tmp_path):
    from audio_backend.app import create_app
    settings = Settings(**primary_options(tmp_path))
    invoked = []

    def fragment_factory(*args, **kwargs):
        invoked.append(True)
        pytest.fail('Rejected fragment factory must not be initialized')

    with pytest.raises(ValueError, match='fragment'):
        create_app(settings, fragment_provider_factory=fragment_factory)
    assert not invoked
    assert not (settings.data_dir / 'jobs.sqlite3').exists()
    assert not (settings.data_dir / 'uploads').exists()


@pytest.mark.parametrize('kind', ['flag', 'path', 'mode'])
def test_primary_startup_tuple_cannot_change_in_place(primary_app_factory, monkeypatch, tmp_path, kind):
    with primary_app_factory() as h:
        settings = h.store.settings
        callback = h.jobs.permit._configuration_current
        assert callback() and h.jobs._coordinator_enabled()
        assert not h.jobs._enabled() and not h.automatic.current()
        variants = {
            'flag': [
                {name: False} for name in (
                    'recording_speaker_links_enabled', 'native_owned_support_enabled',
                    'native_owned_cells_enabled', 'native_owned_core_seams_enabled',
                    'native_owned_bounded_core_support_enabled', 'lexical_evidence_enabled', 'start_worker',
                )
            ] + [
                {'recording_primary_observation_policy': 'off'},
                {'recording_primary_observation_interval_seconds': 61},
                {'native_owned_seam_cells_enabled': True},
                {'asr_backend': 'whisper'}, {'diarization_backend': 'pyannote'},
                {'inference_device': 'vulkan'}, {'overlap_backend': 'mossformer2_experimental'},
            ],
            'path': [{name: str(tmp_path / ('ABSENT-SYNTHETIC-CHANGED-' + name))}
                for name in ('recording_speaker_model_path', 'native_diarization_model', 'native_diarization_library')]
                + [{'model_dir': tmp_path / 'other-models'}],
            'mode': [
                {'moss_jobs_enabled': True, 'moss_automatic_enabled': True,
                    'moss_jobs_model_path': str(tmp_path / 'ABSENT-SYNTHETIC-MOSS-MODEL')},
                {'moss_jobs_enabled': True, 'moss_jobs_model_path': str(tmp_path / 'ABSENT-SYNTHETIC-MOSS-MODEL')},
                {'moss_automatic_enabled': True},
            ],
        }[kind]
        for change in variants:
            with monkeypatch.context() as patch:
                for name, value in change.items():
                    patch.setattr(settings, name, value)
                assert h.jobs.permit._configuration_current is callback
                assert not callback() and not h.jobs._coordinator_enabled()
                assert not h.automatic.coordinator_current()
                _disabled(h.jobs.cell_source.primary_authority_current)
            assert callback()
        _no_dispatch(h)


def test_primary_callback_keeps_exact_startup_owners(primary_app_factory, monkeypatch):
    with primary_app_factory() as h:
        callback = h.jobs.permit._configuration_current
        assert callback()
        bindings = (
            (h.worker, 'native_owned_support_cache'), (h.worker, 'native_owned_cells'),
            (h.capture, 'owner'), (h.capture, 'authority'), (h.capture.authority, 'owner'),
            (h.app.state.fragments, 'factory'),
        )
        for target, name in bindings:
            with monkeypatch.context() as patch:
                patch.setattr(target, name, object())
                assert not callback()
                assert not h.jobs._coordinator_enabled()
                _disabled(h.jobs.cell_source.primary_authority_current)
            assert callback()
        _no_dispatch(h)


def test_primary_authority_rejects_profile_owner_and_database_mismatch(primary_app_factory, monkeypatch, tmp_path):
    from audio_backend.native_owned_cell_store import Budget
    from audio_backend.native_owned_cells import Unavailable
    from audio_backend.recording_primary_probe import _fence, _check_fence
    with primary_app_factory() as h:
        source = h.jobs.cell_source
        authority = h.capture.authority
        fence = _fence(source)
        _check_fence(source, fence)
        with monkeypatch.context() as patch:
            patch.setattr(h.owner, 'qualification', None)
            with pytest.raises(ReadError, match='cell_owner_unavailable'):
                source.primary_authority_current()
            with pytest.raises(Unavailable, match='primary_probe_authority_changed'):
                _check_fence(source, fence)
        with monkeypatch.context() as patch:
            patch.setattr(authority, 'store', object())
            with pytest.raises(Unavailable, match='primary_probe_authority_unavailable'):
                _fence(source)
        with monkeypatch.context() as patch:
            patch.setattr(authority, 'identity', '0' * 64)
            with pytest.raises(ReadError, match='cell_revoked'):
                source.primary_authority_current()
            with pytest.raises(Unavailable, match='primary_probe_authority_changed'):
                _check_fence(source, fence)
        wrong_database = tmp_path / 'unrelated-empty.sqlite3'
        wrong_database.touch()
        with monkeypatch.context() as patch:
            patch.setattr(authority, 'path', wrong_database)
            with pytest.raises(Unavailable, match='primary_database_replaced'):
                with Budget().phase() as budget, authority.db(budget):
                    pytest.fail('A mismatched primary database reached a read connection')
        _check_fence(source, fence)
        _no_dispatch(h)


def test_primary_http_moss_routes_stay_disabled(primary_app_factory, monkeypatch):
    from audio_backend.moss_handy_profile import NAME
    with primary_app_factory() as h:
        prefix = '/sessions/' + h.sid
        cell = '1' * 64
        job = '2' * 32
        body = {'jobs_epoch': h.jobs.jobs_epoch, 'profile': NAME, 'support_id': '3' * 64,
            'expected_cell_sequence': 1, 'expected_cell_revision': 1, 'expected_proof_sha256': '4' * 64}
        calls = []

        def manager_reached(*args, **kwargs):
            calls.append(True)
            pytest.fail('Disabled HTTP route exposed the internal primary coordinator')

        with monkeypatch.context() as patch:
            for name in ('submit', 'submit_cell', 'cells_list', 'cell_detail', 'list', 'detail', 'content'):
                patch.setattr(h.jobs, name, manager_reached)
            for suffix in ('/moss-jobs', '/moss-jobs/' + job, '/moss-jobs/' + job + '/result',
                    '/moss-jobs/' + job + '/raw', '/native-owned-cells', '/native-owned-cells/' + cell):
                response = h.client.get(prefix + suffix)
                assert (response.status_code, response.json()) == (409, {'detail': 'feature_disabled'})
                assert response.headers['cache-control'] == 'private, no-store'
            response = h.client.post(prefix + '/native-owned-cells/' + cell + '/moss-jobs',
                json=body, headers={'Idempotency-Key': 'disabled-cell'})
            assert (response.status_code, response.json()) == (409, {'detail': 'feature_disabled'})
            rolling = {'jobs_epoch': h.jobs.jobs_epoch, 'profile': NAME, 'support_id': '3' * 64,
                'expected_capture_epoch': '5' * 32, 'expected_revision': 1}
            response = h.client.post(prefix + '/native-owned-supports/' + job + '/moss-jobs',
                json=rolling, headers={'Idempotency-Key': 'disabled-support'})
            assert (response.status_code, response.json()) == (409, {'detail': 'feature_disabled'})
        assert calls == []
        health = h.client.get('/health')
        assert health.status_code == 200 and health.json()['moss_jobs']['enabled'] is False
        assert h.client.get(prefix + '/moss-jobs/history').status_code == 200
        _no_dispatch(h)


def test_primary_direct_manager_cannot_submit_moss(primary_app_factory, monkeypatch):
    from audio_backend.moss_handy_profile import NAME
    with primary_app_factory() as h:
        body = {'jobs_epoch': h.jobs.jobs_epoch, 'profile': NAME, 'support_id': '3' * 64,
            'expected_cell_sequence': 1, 'expected_cell_revision': 1, 'expected_proof_sha256': '4' * 64}
        calls = []

        def forbidden(*args, **kwargs):
            calls.append(True)
            pytest.fail('MOSS denial happened after acquisition or dispatch')

        with monkeypatch.context() as patch:
            for target, name in ((h.jobs.cell_source, 'detail'), (h.jobs.cell_source, 'acquire_dispatch'),
                    (h.jobs.cache, 'acquire_dispatch'), (h.jobs.permit, 'reserve_optional'),
                    (h.jobs, '_driver_factory')):
                patch.setattr(target, name, forbidden)
            _disabled(lambda: h.jobs.submit_cell(h.sid, '1' * 64, body, 'direct-cell'))
            rolling = {'jobs_epoch': h.jobs.jobs_epoch, 'profile': NAME, 'support_id': '3' * 64,
                'expected_capture_epoch': '5' * 32, 'expected_revision': 1}
            _disabled(lambda: h.jobs.submit(h.sid, '2' * 32, rolling, 'direct-support'))
            # Empty intent proves the strict MOSS gate runs before key/cell lookup.
            _disabled(lambda: h.jobs.submit_automatic({}))
            _disabled(h.jobs._require_cells)
            _disabled(lambda: h.jobs.cells_list(h.sid))
            _disabled(lambda: h.jobs.cell_detail(h.sid, '1' * 64))
        assert calls == []
        assert h.jobs.health()['enabled'] is False
        _no_dispatch(h)


def test_primary_cell_adapter_keeps_dispatch_and_publication_closed(primary_app_factory, monkeypatch):
    with primary_app_factory() as h:
        source = h.jobs.cell_source
        source.primary_authority_current()
        request = {'expected_cell_sequence': 1, 'expected_cell_revision': 1,
            'expected_proof_sha256': '4' * 64, 'support_id': '3' * 64}
        calls = []

        def forbidden(*args, **kwargs):
            calls.append(True)
            pytest.fail('Disabled MOSS adapter reached its durable owner')

        with monkeypatch.context() as patch:
            patch.setattr(h.owner, 'submit', forbidden)
            _disabled(source._current)
            _disabled(lambda: source.list(h.sid))
            _disabled(lambda: source.detail(h.sid, '1' * 64))
            _disabled(lambda: source.acquire_dispatch(h.sid, '1' * 64, request))
            _disabled(lambda: source.revalidate_dispatch(object()))
            _disabled(lambda: source.publication_current(object()))
        assert calls == []
        assert source._pending is None
        _no_dispatch(h)


def test_passive_lifecycle_never_constructs_a_coordinator(primary_app_factory, monkeypatch):
    from audio_backend.moss_handy_jobs import MossJobs
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail('Passive history read constructed an active coordinator')

    monkeypatch.setattr(MossJobs, '__init__', forbidden)
    with primary_app_factory(mode='passive') as h:
        assert h.jobs is None and h.app.state.moss_jobs is None
        assert h.automatic.jobs is None and not h.automatic.current()
        assert not h.worker.thread.is_alive() and h.worker.native is None
        assert not h.probes and not h.natives
        assert h.client.get('/sessions/' + h.sid + '/transcript').status_code == 200
        assert h.client.get('/sessions/' + h.sid + '/moss-jobs/history').status_code == 200
    assert calls == []


def test_combined_to_primary_does_not_rebind_existing_callback(primary_app_factory, monkeypatch):
    with primary_app_factory(mode='combined') as h:
        callback = h.jobs.permit._configuration_current
        assert callback() and h.jobs._enabled() and h.automatic.current()
        with monkeypatch.context() as patch:
            patch.setattr(h.store.settings, 'moss_jobs_enabled', False)
            patch.setattr(h.store.settings, 'moss_automatic_enabled', False)
            patch.setattr(h.store.settings, 'moss_jobs_model_path', None)
            assert primary_only_enabled(h.store.settings)
            assert h.jobs.permit._configuration_current is callback
            assert not callback() and not h.jobs._enabled()
            assert not h.jobs._coordinator_enabled() and not h.automatic.coordinator_current()
            _disabled(h.jobs.cell_source.primary_authority_current)
        assert callback() and h.jobs._enabled() and h.automatic.current()
        _no_dispatch(h)
