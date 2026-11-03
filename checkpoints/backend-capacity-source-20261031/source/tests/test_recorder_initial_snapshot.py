"""Synthetic interrupted-prefix recovery; no power-loss/provenance claim."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from audio_backend import recorder_initial_snapshot as r
from audio_backend import recorder_snapshot as s
from test_recorder_metadata import semantic_delivery


def setup(tmp_path):
    source = tmp_path / 'source'; source.mkdir(mode=0o700)
    semantic_delivery(source)
    report = s.inspect_delivery(source, validate_metadata=True)
    complete = tmp_path / 'complete'; s.create_snapshot(source, complete)
    expected = {p.name: p.read_bytes() for p in complete.iterdir()}
    return source, tmp_path / 'partial', report, expected


def recover(source, dest, report, **kwargs):
    return r.recover_snapshot(source, dest, report, lambda: 120, **kwargs)


def prefix_file(path, data):
    path.write_bytes(data); path.chmod(0o600)


def contents(path):
    return {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}


@pytest.mark.parametrize('cut', [0, 1, 2, 43, 44, 45, 100, 65535, 65536])
def test_exact_prefixes_complete_and_repeat_without_replacement(tmp_path, cut):
    source, dest, report, expected = setup(tmp_path)
    dest.mkdir(mode=0o700)
    inodes = {}
    for name in (*s.FILES, s.PENDING):
        prefix_file(dest / name, expected[name][:cut])
        inodes[name] = (dest / name).stat().st_ino
    original = contents(source)
    assert recover(source, dest, report)['observed_snapshot_verified']
    assert contents(dest) == expected and contents(source) == original
    assert all((dest / name).stat().st_ino == inode for name, inode in inodes.items())
    assert (dest / s.PENDING).stat().st_ino == (dest / s.RECEIPT).stat().st_ino
    assert (dest / s.PENDING).stat().st_nlink == 2
    assert recover(source, dest, report)['observed_snapshot_verified']
    assert s.inspect_snapshot(dest)['delivery'] == report


def test_absent_destination_and_readonly_mode(tmp_path):
    source, dest, report, expected = setup(tmp_path)
    assert recover(source, dest, report, validate_only=True)['validated_prefix']
    assert not dest.exists()
    nested = tmp_path / 'absent' / 'snapshot'
    assert recover(source, nested, report, validate_only=True)['validated_prefix']
    assert not nested.parent.exists()
    recover(source, dest, report)
    assert contents(dest) == expected


@pytest.mark.parametrize('bad', ['prefix', 'extra', 'symlink', 'oversize', 'hardlink', 'receipt_prefix', 'separate_receipt', 'source_hardlink'])
def test_all_candidates_checked_before_any_writes(tmp_path, bad):
    source, dest, report, expected = setup(tmp_path)
    dest.mkdir(mode=0o700)
    prefix_file(dest / s.FILES[0], expected[s.FILES[0]][:1])
    target = dest / s.PENDING
    if bad == 'prefix': prefix_file(dest / 'audio.wav', b'wrong')
    if bad == 'extra': prefix_file(dest / 'unknown', b'x')
    if bad == 'symlink': (dest / 'audio.wav').symlink_to(source / 'audio.wav')
    if bad == 'oversize': prefix_file(target, expected[s.PENDING] + b'x')
    if bad == 'hardlink':
        prefix_file(target, expected[s.PENDING][:4]); os.link(target, tmp_path / 'outside')
    if bad == 'receipt_prefix': prefix_file(target, b'wrong')
    if bad == 'separate_receipt':
        prefix_file(target, expected[s.PENDING]); prefix_file(dest / s.RECEIPT, expected[s.RECEIPT])
    if bad == 'source_hardlink': os.link(source / 'audio.wav', tmp_path / 'outside')
    before = contents(dest)
    with pytest.raises((s.Refused, OSError)): recover(source, dest, report)
    assert contents(dest) == before


@pytest.mark.parametrize('cut', range(1, 7))
def test_fsync_interruption_is_repeatable(tmp_path, monkeypatch, cut):
    source, dest, report, expected = setup(tmp_path)
    original = os.fsync; count = 0
    def fail(fd):
        nonlocal count
        count += 1
        if count == cut: raise OSError('synthetic interruption')
        return original(fd)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', fail)
        with pytest.raises(OSError): recover(source, dest, report)
    partial = contents(dest)
    assert recover(source, dest, report)['observed_snapshot_verified']
    assert contents(dest) == expected
    assert all(expected[name].startswith(data) for name, data in partial.items())


@pytest.mark.parametrize('cut', [1, 2, 43, 44, 45, 97])
def test_short_write_then_interruption_preserves_prefix(tmp_path, monkeypatch, cut):
    source, dest, report, expected = setup(tmp_path)
    original = os.write; count = 0
    def fail(fd, block):
        nonlocal count
        count += 1
        if count == 2: raise OSError('synthetic interruption')
        return original(fd, block[:cut])
    with monkeypatch.context() as patch:
        patch.setattr(os, 'write', fail)
        with pytest.raises(OSError): recover(source, dest, report)
    before = contents(dest)
    assert recover(source, dest, report)['observed_snapshot_verified']
    assert all(expected[name].startswith(data) for name, data in before.items())
    assert contents(dest) == expected


def test_changed_original_disk_and_deadline_refuse_without_destination(tmp_path, monkeypatch):
    source, dest, report, expected = setup(tmp_path)
    changed = dict(report, revision='changed')
    with pytest.raises(s.Refused, match='original_report_changed'): recover(source, dest, changed)
    assert not dest.exists()
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fstatvfs', lambda fd: SimpleNamespace(f_bavail=0, f_frsize=4096))
        with pytest.raises(s.Refused, match='disk_headroom'): recover(source, dest, report)
    assert not dest.exists()
    def expired(): raise s.Refused('deadline')
    with pytest.raises(s.Refused, match='deadline'): r.recover_snapshot(source, dest, report, expired)
    assert not dest.exists()


@pytest.mark.parametrize('kind', ['directory', 'fifo', 'external_receipt_link', 'published_prefix'])
def test_nonregular_and_invalid_publication_preserved(tmp_path, kind):
    source, dest, report, expected = setup(tmp_path)
    dest.mkdir(mode=0o700)
    prefix_file(dest / s.FILES[0], expected[s.FILES[0]][:1])
    if kind == 'directory': (dest / 'audio.wav').mkdir(mode=0o700)
    elif kind == 'fifo': os.mkfifo(dest / 'audio.wav', 0o600)
    else:
        prefix_file(dest / s.PENDING, expected[s.PENDING][:1] if kind == 'published_prefix' else expected[s.PENDING])
        os.link(dest / s.PENDING, dest / s.RECEIPT)
        if kind == 'external_receipt_link': os.link(dest / s.PENDING, tmp_path / 'outside')
    before = {p.name: p.lstat() for p in dest.iterdir()}
    with pytest.raises((s.Refused, OSError)): recover(source, dest, report)
    assert {p.name: p.lstat() for p in dest.iterdir()} == before


def test_original_bytes_changed_refuses_all_mutation(tmp_path):
    source, dest, report, expected = setup(tmp_path)
    dest.mkdir(mode=0o700)
    prefix_file(dest / 'audio.wav', expected['audio.wav'][:44])
    data = bytearray((source / 'audio.wav').read_bytes()); data[-1] ^= 1
    (source / 'audio.wav').write_bytes(data)
    before = contents(dest)
    with pytest.raises(s.Refused): recover(source, dest, report)
    assert contents(dest) == before


def test_readonly_validation_checks_partial_receipt_too(tmp_path):
    source, dest, report, expected = setup(tmp_path)
    dest.mkdir(mode=0o700)
    prefix_file(dest / s.FILES[0], expected[s.FILES[0]][:2])
    prefix_file(dest / s.PENDING, b'wrong')
    before = contents(dest)
    with pytest.raises(s.Refused, match='prefix_mismatch'):
        recover(source, dest, report, validate_only=True)
    assert contents(dest) == before
