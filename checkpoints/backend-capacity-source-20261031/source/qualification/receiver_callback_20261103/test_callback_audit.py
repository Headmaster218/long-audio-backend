"""Small synthetic audit regressions; no receiver, service, or real evidence writes."""
import hashlib
import os

import pytest

import audit_receiver_callback as audit


def test_changed_pin_refuses(tmp_path):
    file = tmp_path/'file'; file.write_bytes(b'before')
    pins = {str(file): audit.digest(file)}
    assert audit.verify_hashes(pins) == 1
    file.write_bytes(b'after')
    with pytest.raises(AssertionError, match='pinned file hash changed'):
        audit.verify_hashes(pins)


def test_restore_checks_git_blob_and_complete_file_set(tmp_path):
    file = tmp_path/'file'; raw = b'source'; file.write_bytes(raw)
    row = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
        git_blob=hashlib.sha1(b'blob 6\0'+raw).hexdigest())
    manifest = dict(files={'file':row}, file_count=1, bytes=6)
    assert audit.verify_restore(tmp_path, manifest) == (1, 6)
    row['git_blob'] = '0'*40
    with pytest.raises(AssertionError, match='restored source blob changed'):
        audit.verify_restore(tmp_path, manifest)
    (tmp_path/'extra').touch()
    with pytest.raises(AssertionError, match='restored source file set changed'):
        audit.verify_restore(tmp_path, manifest)


@pytest.mark.parametrize('kind', ['bytes', 'hardlink', 'marker'])
def test_independent_snapshot_checks_reject_changes(tmp_path, kind):
    raw, published, selected = [tmp_path/name for name in ('attempt', 'published', 'selected')]
    for root in (raw, published, selected): root.mkdir(mode=0o700)
    audio, metadata = b'fake audio', b'fake metadata'
    marker = '\n'.join(['RECORDER-DELIVERY-V1', raw.name, 'revision', 'source', str(len(audio)),
        hashlib.sha256(audio).hexdigest(), str(len(metadata)), hashlib.sha256(metadata).hexdigest(), '']).encode()
    for root in (raw, published, selected):
        for name, body in zip(audit.FILES, (audio, metadata, marker)):
            file = root/name; file.write_bytes(body); file.chmod(0o600)
    assert set(audit.verify_copies(raw, published, selected)) == set(audit.FILES)
    if kind == 'bytes':
        (selected/'audio.wav').write_bytes(b'changed')
        reason = 'selected snapshot bytes differ'
    elif kind == 'hardlink':
        (selected/'audio.wav').unlink(); os.link(raw/'audio.wav', selected/'audio.wav')
        reason = 'copies share inode'
    else:
        for root in (raw, published, selected):
            (root/'delivery.v1.txt').write_bytes(marker.replace(
                hashlib.sha256(audio).hexdigest().encode(), b'0'*64))
        reason = 'marker transport binding'
    with pytest.raises(AssertionError, match=reason): audit.verify_copies(raw, published, selected)


def test_report_hash_gate_precedes_evidence_traversal(tmp_path):
    (tmp_path/'report.json').write_text('{}')
    with pytest.raises(AssertionError, match='unexpected run report hash'):
        audit.audit(tmp_path, tmp_path/'absent-pins', tmp_path/'absent-restore', '0'*64, '1'*64)


def test_restore_hash_gate_precedes_evidence_traversal(tmp_path):
    report = tmp_path/'report.json'; report.write_text('{}')
    restore = tmp_path/'restore.json'; restore.write_text('{}')
    with pytest.raises(AssertionError, match='unexpected restore manifest hash'):
        audit.audit(tmp_path, tmp_path/'absent-pins', restore, audit.digest(report), '1'*64)
