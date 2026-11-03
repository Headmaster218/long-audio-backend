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


def test_explicit_sample_validation(tmp_path):
    import hashlib
    import wave
    script = Path(__file__).resolve().parents[1] / 'tools/strict_uploader_smoke.py'
    spec = importlib.util.spec_from_file_location('strict_smoke_sample', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    p = tmp_path / 'sample.wav'
    def make(channels=1, frames=160):
        with wave.open(str(p), 'wb') as w:
            w.setnchannels(channels); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(bytes(frames * 2 * channels))
        return hashlib.sha256(p.read_bytes()).hexdigest()
    digest = make()
    assert module.validate_input(p, digest) == 160
    for bad in (None, 'a', '0' * 64):
        with pytest.raises(ValueError): module.validate_input(p, bad)
    with pytest.raises(ValueError, match='input_format'): module.validate_input(p, make(channels=2))
    with pytest.raises(ValueError, match='input_frame_bound'): module.validate_input(p, make(frames=0))
    with pytest.raises(ValueError, match='input_file_bound'): module.validate_input(p, make(frames=480000+3000))


@pytest.mark.parametrize('language', [None, 'auto', 'zh', 'en', 'ja', 'ko', 'yue'])
def test_explicit_smoke_language_changes_only_language(tmp_path, language):
    script = Path(__file__).resolve().parents[1] / 'tools/strict_uploader_smoke.py'
    spec = importlib.util.spec_from_file_location('strict_smoke_language', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = module.configuration(tmp_path, tmp_path / 'data')
    expected = dict(before)
    if language is not None:
        expected['language'] = language
    assert module.smoke_configuration(tmp_path, tmp_path / 'data', language) == expected
    assert module.configuration(tmp_path, tmp_path / 'data') == before
    with pytest.raises(ValueError, match='unsupported_smoke_language'):
        module.smoke_configuration(tmp_path, tmp_path / 'data', 'invalid')


def test_invalid_smoke_language_refused_before_output(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / 'tools/strict_uploader_smoke.py'
    spec = importlib.util.spec_from_file_location('strict_smoke_invalid_language', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / 'output'
    monkeypatch.setattr(sys, 'argv', [str(script), str(output), '--assets', 'unused', '--language', 'invalid'])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert not output.exists()
