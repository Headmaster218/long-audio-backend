"""Synthetic refusal checks for the read-only audit, never native execution."""
import importlib.util
import json
from pathlib import Path
import shutil
import wave

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('span_native_audit',
    ROOT / 'qualification/independent_recorder_span_audit_20261103.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def candidates(tmp_path):
    source, candidate, run = (tmp_path / name for name in ('source', 'candidate', 'run'))
    for root in (source, candidate):
        (root / 'audio_backend').mkdir(parents=True)
        (root / 'tools').mkdir()
        (root / 'qualification').mkdir()
        shutil.copyfile(ROOT / 'audio_backend/native_owned_cell_store.py',
                        root / 'audio_backend/native_owned_cell_store.py')
        shutil.copyfile(ROOT / 'qualification/ACCEPTED_RUNTIME_BASIS_20261103.json',
                        root / 'qualification/ACCEPTED_RUNTIME_BASIS_20261103.json')
        (root / 'tools/unchanged.py').write_text('unchanged = True\n')
    path = candidate / 'audio_backend/native_owned_cell_store.py'
    path.write_text(path.read_text().replace(
        'd1eafbcf6bde0071d301f488ba82e69fa3bbadc446c62775276e4998efbdbe67',
        audit.digest(source / 'qualification/ACCEPTED_RUNTIME_BASIS_20261103.json')).replace(
        '096bb1a3902a75a2c3723f2d7863a8d3e9ed627bd862e6538eca2bc517d6e29b',
        '6ac9a15ee2c393bd994e6b3784ed3f5fb97c9b4f986cbce03dccbad6226778df'))
    run.mkdir()
    pins = [dict(path=str(p.relative_to(candidate)), bytes=p.stat().st_size,
                 sha256=audit.digest(p)) for folder in ('audio_backend', 'tools')
            for p in (candidate / folder).glob('*.py')]
    (run / 'SOURCE_PINS.json').write_text(json.dumps(pins))
    return source, candidate, run


def test_exact_two_literal_candidate_is_accepted_readonly(tmp_path):
    source, candidate, run = candidates(tmp_path)
    before = [audit.inventory(root) for root in (source, candidate, run)]
    assert len(audit.verify_sources(source, candidate, run)) == 2
    assert [audit.inventory(root) for root in (source, candidate, run)] == before


@pytest.mark.parametrize('changed,relative,reason', [
    ('candidate', 'audio_backend/native_owned_cell_store.py', 'not_exact_accepted_two_literal_candidate'),
    ('source', 'audio_backend/native_owned_cell_store.py', 'production_profile_changed'),
    ('candidate', 'qualification/ACCEPTED_RUNTIME_BASIS_20261103.json', 'candidate_basis_changed'),
    ('candidate', 'tools/unchanged.py', 'candidate_pin_changed'),
    ('source', 'tools/unchanged.py', 'unexpected_candidate_difference'),
])
def test_changed_profile_basis_or_source_refused(tmp_path, changed, relative, reason):
    source, candidate, run = candidates(tmp_path)
    path = {'source': source, 'candidate': candidate}[changed] / relative
    path.write_bytes(path.read_bytes() + b'\n')
    before = [audit.inventory(root) for root in (source, candidate, run)]
    with pytest.raises(RuntimeError, match=reason):
        audit.verify_sources(source, candidate, run)
    assert [audit.inventory(root) for root in (source, candidate, run)] == before


def test_inventory_refuses_symlink(tmp_path):
    (tmp_path / 'target').write_bytes(b'value')
    (tmp_path / 'alias').symlink_to('target')
    with pytest.raises(RuntimeError, match='evidence_symlink'):
        audit.inventory(tmp_path)


@pytest.mark.parametrize('channels,frames,reason', [(2, 16, 'noncanonical_pcm'), (1, 480001, 'smoke_frame_bound')])
def test_pcm_refuses_noncanonical_or_overlong(tmp_path, channels, frames, reason):
    path = tmp_path / 'fixture.wav'
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\0' * frames * channels * 2)
    before = audit.digest(path)
    with pytest.raises(RuntimeError, match=reason):
        audit.pcm(path)
    assert audit.digest(path) == before
