"""Model-free controls for the loopback qualification harness, never a launch."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

PATH = Path(__file__).with_name('qualify_local_inbox_loopback.py')
spec = importlib.util.spec_from_file_location('local_inbox_qualification', PATH)
harness = importlib.util.module_from_spec(spec); spec.loader.exec_module(harness)
sys.path.insert(0, str(PATH.parents[1] / 'tests'))
from test_recorder_sequence import fixture


def source_and_inbox(tmp_path):
    source = tmp_path / 'source'; fixture(source, 0)
    inbox = tmp_path / 'inbox'; inbox.mkdir()
    return source, inbox


def hashes(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}


def test_partial_completion_and_originals(tmp_path):
    source, inbox = source_and_inbox(tmp_path); before = hashes(source)
    target = harness.arrive(source, inbox, partial=True)
    assert sorted(p.name for p in target.iterdir()) == ['audio.wav', 'segment.v1.bin']
    with pytest.raises(FileNotFoundError): harness.inspect_delivery(target, validate_metadata=True)
    expected = harness.inspect_delivery(source, validate_metadata=True)
    assert harness.complete_arrival(source, target) == expected
    assert hashes(target) == before == hashes(source)
    assert all(p.stat().st_mode & 0o077 == 0 for p in target.iterdir())
    assert target.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('conflict', [False, True])
def test_variants_are_valid_distinct_test_copies(tmp_path, conflict):
    source, inbox = source_and_inbox(tmp_path); before = hashes(source)
    original = harness.inspect_delivery(source, validate_metadata=True)
    target = harness.variant(source, inbox, conflict=conflict)
    changed = harness.inspect_delivery(target, validate_metadata=True)
    assert original['attempt'] != changed['attempt']
    for key in ('capture_identity_sha256', 'run_identity_sha256', 'epoch', 'sequence', 'frame_start', 'frames'):
        assert original[key] == changed[key]
    for key in ('wav_sha256', 'pcm_sha256', 'metadata_sha256'):
        assert (original[key] != changed[key]) is conflict
    assert hashes(source) == before


def test_variant_does_not_overwrite_attempt(tmp_path):
    source, inbox = source_and_inbox(tmp_path)
    target = harness.variant(source, inbox); before = hashes(target)
    with pytest.raises(FileExistsError): harness.variant(source, inbox)
    assert hashes(target) == before


def test_fixture_pin_refused_before_delivery_open(tmp_path, monkeypatch):
    fixture_path = tmp_path / 'FIXTURE.json'; fixture_path.write_text('{}')
    monkeypatch.setattr(harness, 'inspect_delivery', lambda *a, **k: pytest.fail('must fail before delivery'))
    with pytest.raises(RuntimeError, match='fixture_pin'): harness.verify_fixture(fixture_path)


def test_refusal_requires_no_remote_mutations():
    phase = dict(phase='conflict', returned=False, error={'message': 'conflict'}, requests=[])
    harness.assert_refused(phase)
    phase['requests'] = [{'method': 'POST'}]
    with pytest.raises(RuntimeError, match='unexpected_mutation'): harness.assert_refused(phase)
    phase.update(returned=True, requests=[])
    with pytest.raises(RuntimeError, match='expected_refusal'): harness.assert_refused(phase)


class FakeProcess:
    pid = 4711
    def __init__(self, *, force=False, raced=False):
        self.waited = []; self.terminated = False; self.killed = False
        self.force = force; self.raced = raced; self.polls = 0
    def poll(self):
        self.polls += 1
        if self.raced and self.polls > 1: return 0
        return None if not self.killed and (not self.terminated or self.force) else -15
    def terminate(self): self.terminated = True
    def kill(self): self.killed = True
    def wait(self, timeout):
        self.waited.append(timeout)
        if self.force and not self.killed: raise subprocess.TimeoutExpired('child', timeout)
        return 0 if self.raced else -9 if self.killed else -15


@pytest.mark.parametrize('force,raced,expected', [(False,False,-15),(True,False,-9),(False,True,0)])
def test_gate_failure_always_reaps_original_process(tmp_path, monkeypatch, force, raced, expected):
    process = FakeProcess(force=force, raced=raced)
    monkeypatch.setattr(harness.subprocess, 'Popen', lambda *a, **k: process)
    def check(): raise RuntimeError('gate_failed')
    gate = SimpleNamespace(out=tmp_path, check=check)
    with pytest.raises(RuntimeError, match='gate_failed'):
        harness.child_phase(gate, {}, 'probe')
    receipt = json.loads((tmp_path / 'probe-wait.json').read_bytes())
    assert receipt == {'pid':4711,'original_popen_wait_completed':True,'exit_code':expected}
    assert process.waited == ([5,5] if force else [1] if raced else [5])


def test_source_fixture_pin_is_fixed():
    assert harness.FIXTURE_SHA256 == '735895855f8de14f84992216ee7b28fb7359ce74fb30a86aed6ed5e93e80dd56'


def export_pair(tmp_path):
    doc = dict(completion={}, units=[{'text':'synthetic harness test'}])
    payloads = {'export.json':json.dumps(doc).encode(), 'transcript.txt':b'synthetic harness test\n'}
    manifest = dict(status='exported', schema='recorder-incremental-span-export-v1',
        artifacts={name:{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in payloads.items()},
        nonempty_text_units=1, units=1, frames=144000, complete_content=False)
    payloads['complete.json'] = json.dumps(manifest).encode()
    for directory in ('export','export-repeat'):
        root=tmp_path/directory; root.mkdir(mode=0o700)
        for name, raw in payloads.items(): harness.private_write(root/name, raw)
    return manifest


def test_export_requires_matching_manifest_hash_and_length(tmp_path, monkeypatch):
    manifest = export_pair(tmp_path)
    monkeypatch.setattr(harness, 'verify_status', lambda *args: None)
    assert harness.check_exports(tmp_path, [])['nonempty_text_units'] == 1
    manifest['artifacts']['transcript.txt']['sha256'] = '0'*64
    for directory in ('export','export-repeat'):
        (tmp_path/directory/'complete.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='export_manifest_hash_or_length'):
        harness.check_exports(tmp_path, [])


def test_export_requires_actual_nonempty_unit_count(tmp_path, monkeypatch):
    manifest = export_pair(tmp_path)
    monkeypatch.setattr(harness, 'verify_status', lambda *args: None)
    manifest['nonempty_text_units'] = 2
    for directory in ('export','export-repeat'):
        (tmp_path/directory/'complete.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='no_real_primary_speech_text'):
        harness.check_exports(tmp_path, [])


def test_empty_managed_git_marker_is_not_repository(tmp_path):
    (tmp_path / '.git').mkdir()
    harness.require_nonrepository_output(tmp_path / 'private-output' / 'run')


@pytest.mark.parametrize('kind', ['head', 'gitfile'])
def test_actual_repository_output_is_refused(tmp_path, kind):
    if kind == 'head':
        (tmp_path / '.git').mkdir()
        (tmp_path / '.git/HEAD').write_text('ref: refs/heads/main\n')
    else:
        (tmp_path / '.git').write_text('gitdir: /not-followed-by-this-check\n')
    with pytest.raises(RuntimeError, match='output_in_repository'):
        harness.require_nonrepository_output(tmp_path / 'private-output' / 'run')
