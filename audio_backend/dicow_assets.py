"""Pinned local SE-DiCoW assets; no model import or network in readiness checks."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def manifest():
    return json.loads((ROOT/'dicow-model-manifest.json').read_text())

def verify_model_directory(directory, *, hash_files=True):
    directory=Path(directory).expanduser().resolve()
    expected=manifest()
    for row in expected['runtime_files']:
        name=row['name']
        if Path(name).name!=name:raise ValueError('Invalid model manifest filename')
        path=directory/name
        if not path.is_file() or path.stat().st_size!=row['bytes']:
            raise ValueError(f'Missing/invalid pinned SE-DiCoW file: {name}')
        if hash_files:
            with path.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
            if actual!=row['sha256']:raise ValueError(f'SE-DiCoW SHA256 mismatch: {name}')
    return expected

def dicow_readiness(config):
    directory=config.get('dicow_model_dir')
    python=config.get('dicow_python')
    if not directory or not python:
        return {'ready':False,'reason':'dicow_runtime_not_configured','backend':'dicow_en_experimental'}
    if not Path(python).expanduser().is_file():
        return {'ready':False,'reason':'dicow_python_missing','backend':'dicow_en_experimental'}
    try:verify_model_directory(directory,hash_files=False)
    except (OSError,ValueError) as exc:
        return {'ready':False,'reason':'dicow_model_files_missing_or_wrong_size','detail':str(exc),
                'backend':'dicow_en_experimental'}
    return {'ready':True,'reason':'configured_files_present','backend':'dicow_en_experimental',
            'verification':'Existence/size only; all SHA256 and dependency versions checked before model execution',
            'experimental':True,'languages':['en'],'device':'cpu','vulkan_validated':False,
            'identity_status':'unresolved','profile_update_eligible':False}
