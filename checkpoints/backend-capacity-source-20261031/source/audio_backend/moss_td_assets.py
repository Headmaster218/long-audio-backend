"""Pinned, optional MOSS-TD assets. Readiness never imports model dependencies."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = 'moss_td_experimental'


def manifest():
    return json.loads((ROOT / 'moss-td-model-manifest.json').read_text(encoding='utf-8'))


def verify_file(path, row, *, hash_files=True):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != row['bytes']:
        raise ValueError(f'Missing/invalid pinned MOSS-TD file: {row["name"]}')
    if hash_files:
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != row['sha256']:
            raise ValueError(f'MOSS-TD SHA256 mismatch: {row["name"]}')


def verify_model_directory(directory, *, hash_files=True):
    directory = Path(directory).expanduser().resolve()
    expected = manifest()
    names = set()
    for row in expected['runtime_files']:
        name = row['name']
        if not name or Path(name).name != name or '/' in name or '\\' in name or name in names:
            raise ValueError('Invalid model manifest filename')
        names.add(name)
        verify_file(directory / name, row, hash_files=hash_files)
    return expected


def moss_td_readiness(config):
    if config.get('moss_td_runtime', 'transformers') == 'cpp':
        from .moss_td_cpp_assets import moss_td_cpp_readiness
        return moss_td_cpp_readiness(config)
    base = {'ready': False, 'backend': BACKEND}
    directory, python = config.get('moss_td_model_dir'), config.get('moss_td_python')
    if not directory or not python:
        return {**base, 'reason': 'moss_td_runtime_not_configured'}
    if not Path(python).expanduser().is_file():
        return {**base, 'reason': 'moss_td_python_missing'}
    try:
        verify_model_directory(directory, hash_files=False)
    except (OSError, ValueError, TypeError) as exc:
        return {**base, 'reason': 'moss_td_model_files_missing_or_wrong_size', 'detail': str(exc)}
    return {**base, 'ready': True, 'reason': 'configured_files_present',
            'verification': 'Existence/size only; all SHA256 and dependency versions checked before model execution',
            'experimental': True, 'languages': ['zh', 'en'], 'device': 'cpu', 'dtype': 'bfloat16',
            'vulkan_validated': False, 'identity_status': 'unresolved', 'profile_update_eligible': False,
            'timestamp_quality': 'model_generated_unverified'}
