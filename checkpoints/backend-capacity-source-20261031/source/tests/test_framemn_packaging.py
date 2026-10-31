"""Preserve the original MIT notice in the ordinary source packaging path."""
import hashlib
import importlib.util
from pathlib import Path


def test_source_packager_includes_exact_vendor_license_and_adaptation():
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('framemn_source_packager',root/'scripts/package_source.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    names={path.relative_to(root).as_posix() for path in module.selected_files()}
    license='audio_backend/events/_vendor/framemn/LICENSE'
    assert license in names and 'audio_backend/events/_vendor/framemn/ADAPTATION.json' in names
    raw,sanitized=module.packaged_bytes(root/license)
    assert not sanitized and hashlib.sha256(raw).hexdigest()=='399acc6d903cfd65a6cd5baba6a99af3ee534d79161b07854c1a6ffeab3dc88b'
    assert not any(name.endswith(('.pt','.npy','.wav','.sqlite3')) for name in names)
