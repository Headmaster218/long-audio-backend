"""Reviewed manifest is the only source of actual launch Settings and assets."""
import os
from pathlib import Path
import sys

from bindings import canonical, verify_manifest, verify_import_paths


FIXED_SETTINGS = dict(cpu_threads=2, native_diarization_threads=2,
    process_memory_limit_bytes=6*1024**3, window_seconds=10.5,
    context_seconds=2.0, max_pending_chunks=80,
    reviewed_primary_persons_enabled=False, api_key=None, offline=True,
    recording_primary_sampling_policy='fresh-window-bounded-v2',
    native_owned_bounded_core_support_seconds=.75,
    framemn_enabled=False, speaker_support_enabled=True,
    recording_speaker_history_read_enabled=False, language='en',
    native_owned_compact_gc_enabled=False, native_owned_receipt_gc_enabled=False,
    max_upload_bytes=4000000, max_sessions=1, max_audio_seconds=720,
    disk_reserve_bytes=2*1024**3, recording_disk_reserve_bytes=11*1024**3//2)


def validate_settings_policy(settings, output):
    if settings.data_dir != Path(output)/'data':
        raise ValueError('Settings data output differs')
    if any(getattr(settings, key) != wanted for key, wanted in FIXED_SETTINGS.items()):
        raise ValueError('Settings violates fixed operational policy')


def prepare(path, sha256, *, deadline, output):
    value = verify_manifest(path, sha256, deadline=deadline)
    runner = canonical(value['runner_root'])
    if runner != Path(__file__).resolve().parent:
        raise ValueError('wrong runner root')
    pinned = {row['path'] for row in value['files']}
    # Every local helper, not merely this entry point, must be content-pinned.
    for file in runner.glob('*.py'):
        if str(file) not in pinned:
            raise ValueError('runner helper lacks content pin')
    output = Path(output)
    if not output.is_absolute() or Path(os.path.abspath(output)) != output:
        raise ValueError('output must be absolute and canonical')
    if output.parent.resolve(strict=True) != output.parent:
        raise ValueError('output parent is not canonical')
    if value['output'] != str(output):
        raise ValueError('manifest output differs')
    if value.get('trial_observer_mode') != 'NOT_ENABLED':
        raise ValueError('explicit observer-off manifest required')
    if value.get('qualification') not in ('lifecycle-only', 'short-operational'):
        raise ValueError('this revision only wires lifecycle and fixed short input')
    if value['qualification'] == 'short-operational':
        from short_input import load_input, SHA256, BYTES
        row = value['input']
        if row.get('sha256') != SHA256 or row.get('bytes') != BYTES or row['path'] not in pinned:
            raise ValueError('short input lacks exact reviewed pin')
        load_input(canonical(row['path']))
    if type(value.get('port')) is not int or not 1024 <= value['port'] <= 65535:
        raise ValueError('fixed nonprivileged port required')
    verify_import_paths(sys.path, allowed=value['allowed_import_paths'])
    source = value['product_source']
    if source not in sys.path:
        sys.path.insert(0, source)
    # No app, store, model or SQL import in preflight.
    from audio_backend.settings import Settings, primary_only_enabled
    from audio_backend import settings as settings_module
    if Path(settings_module.__file__).resolve() != Path(source)/'audio_backend/settings.py':
        raise ValueError('wrong product import origin')
    settings = Settings(**value['settings'])
    if settings.data_dir != output/'data' or not primary_only_enabled(settings):
        raise ValueError('wrong actual primary-only Settings')
    validate_settings_policy(settings, output)
    for key in ('native_diarization_model', 'native_diarization_library',
                'recording_speaker_model_path'):
        if str(getattr(settings, key)) not in pinned:
            raise ValueError('required asset lacks content pin')
    model_root = canonical(settings.model_dir)
    from bindings import runtime_inventory
    files, links = runtime_inventory(model_root, deadline)
    if links or not files or any(str(model_root/name) not in pinned for name in files):
        raise ValueError('model layout must be fully pinned regular leaves')
    return value, settings
