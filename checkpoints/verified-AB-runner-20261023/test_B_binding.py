"""Finite B dispatch/binding negatives. No product, SQL, model or service import."""
from pathlib import Path
import time

import pytest
import a_input
import b_input
import launch_binding
from prepare_manifest import validate_input_flags


def test_A_and_safety_helpers_remain_byte_identical_to_frozen_rev06():
    root = Path(__file__).parent
    old = root.parent/'audio-recovery-runner-rev06-20261023'
    for name in ('a_input.py', 'a_uploader_cli.py', 'settings.A.json',
                 'bindings.py', 'service_cli.py', 'service_wrapper.py',
                 'child_environment.py', 'health.py', 'health_transport.py',
                 'http_input.py', 'locks.py', 'monitor.py', 'ownership.py',
                 'runner.py', 'short_input.py', 'uploader_cli.py'):
        assert (root/name).read_bytes() == (old/name).read_bytes(), name


def test_B_receipt_constants_and_geometry_are_separate_from_A():
    assert b_input.SHA256 == '50e2c8e4737850c4bf3cc24e7087c755e1c733cd91822106afd0df485ba64893'
    assert b_input.BYTES == 57179180
    assert b_input.TOTAL_FRAMES == 28589568
    assert (b_input.SHA256, b_input.BYTES) != (a_input.SHA256, a_input.BYTES)
    assert b_input.plan() == a_input.plan()
    assert b_input.START == 13440000
    assert b_input.START+b_input.FRAMES == 24960000


@pytest.mark.parametrize('kind', ['short', 'A', 'B', 'lifecycle'])
def test_input_flags_must_be_explicit_and_exclusive(kind):
    values = {mode: None for mode in ('short', 'A', 'B')}
    if kind != 'lifecycle': values[kind] = Path('/fixed')
    validate_input_flags(kind, values['short'], values['A'], values['B'])
    for other in values:
        bad = dict(values)
        bad[other] = None if other == kind else Path('/wrong')
        with pytest.raises(ValueError):
            validate_input_flags(kind, bad['short'], bad['A'], bad['B'])


@pytest.mark.parametrize('kind,module', [('A', a_input), ('B', b_input)])
@pytest.mark.parametrize('defect', ['other_pin', 'window', 'unpinned', 'valid'])
def test_prepare_selects_only_exact_kind_input_before_product_import(tmp_path, monkeypatch, kind, module, defect):
    root = Path(launch_binding.__file__).resolve().parent
    output = tmp_path/'fresh'
    path = tmp_path/'original.wav'
    path.write_bytes(b'not used by fake reader')
    row = dict(path=str(path), sha256=module.SHA256, bytes=module.BYTES,
               source_frame_start=13440000, source_frame_end=24960000,
               capture_origin_frame=100003, local_timeline_start=0)
    files = [{'path': str(p)} for p in root.glob('*.py')]+[{'path': str(path)}]
    if defect == 'other_pin':
        other = b_input if kind == 'A' else a_input
        row.update(sha256=other.SHA256, bytes=other.BYTES)
    if defect == 'window': row['source_frame_start'] = 0
    if defect == 'unpinned': files.pop()
    value = dict(runner_root=str(root), files=files, output=str(output),
                 trial_observer_mode='NOT_ENABLED', qualification=kind+'-operational', input=row)
    monkeypatch.setattr(launch_binding, 'verify_manifest', lambda *a, **k: value)
    sentinel = RuntimeError('correct reader selected')
    def reader(actual_path, deadline):
        assert actual_path == path
        raise sentinel
    monkeypatch.setattr(module, kind+'Input', reader)
    if defect == 'valid':
        with pytest.raises(RuntimeError) as caught:
            launch_binding.prepare('unused', 'unused', deadline=time.monotonic()+1, output=output)
        assert caught.value is sentinel
    else:
        with pytest.raises(ValueError):
            launch_binding.prepare('unused', 'unused', deadline=time.monotonic()+1, output=output)
