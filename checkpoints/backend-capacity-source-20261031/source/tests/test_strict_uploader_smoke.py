"""Synthetic preflight refusal tests; no native service/model qualification."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from audio_backend import native_owned_cell_store as cells


@pytest.mark.parametrize('profile', [None, SimpleNamespace(sqlite3_package_sha256='expected')])
def test_known_runtime_refusal_starts_no_service(tmp_path, monkeypatch, profile):
    script = Path(__file__).resolve().parents[1] / 'tools/strict_uploader_smoke.py'
    spec = importlib.util.spec_from_file_location('strict_smoke_fixture', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assets = tmp_path / 'assets.json'
    assets.write_text('{"files": []}')
    output = tmp_path / 'output'
    monkeypatch.setattr(sys, 'argv', [str(script), str(output), '--assets', str(assets)])
    monkeypatch.setattr(cells, 'qualified_runtime', lambda _: profile)
    monkeypatch.setattr(cells, 'runtime_fingerprints', lambda _: {'sqlite3_package_sha256': 'observed'})
    # Fixture has no actual native/resource work; do not require CI disk headroom.
    monkeypatch.setattr(module.Gate, 'check', lambda _: None)
    def forbidden(*args, **kwargs):
        raise AssertionError('A refused runtime must not start a service')
    monkeypatch.setattr(module.subprocess, 'Popen', forbidden)
    assert module.main() == 1
    report = json.loads((output / 'RESULT.json').read_text())
    assert report['passed'] is False
    expected = 'no_shipped_runtime_qualification' if profile is None else 'runtime_binary_qualification_mismatch'
    assert report['error']['message'] == expected
    assert not (output / 'synthetic.wav').exists()
    assert not (output / 'config.json').exists()
    assert not (output / 'data/instance.lock').exists()
