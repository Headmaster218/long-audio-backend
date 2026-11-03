import json
import os
import shutil
import sys

import pytest
from audio_backend import recorder_inbox as inbox
from audio_backend import recorder_delivery as transport
from test_recorder_inbox import delivery


def test_failed_hash_scan_consumes_budget(tmp_path):
    root = tmp_path/'inbox'; root.mkdir()
    delivery(root, 0); delivery(root, 1)
    first = sorted(root.iterdir())[0]
    audio=first/'audio.wav'; raw=bytearray(audio.read_bytes()); raw[-1] ^= 1; audio.write_bytes(raw)
    with pytest.raises(inbox.Refused, match='inbox_byte_limit_no_partial_inventory'):
        inbox.inventory(root, max_bytes=104)


def test_late_extra_child_is_unresolved(tmp_path, monkeypatch):
    root=tmp_path/'inbox'; root.mkdir(); candidate=delivery(root)
    original=inbox.inspect_delivery
    def replace(*args, **kwargs):
        result=original(*args, **kwargs)
        (candidate/'new-pending').write_bytes(b'')
        return result
    monkeypatch.setattr(inbox,'inspect_delivery',replace)
    report=inbox.inventory(root)
    assert report['deliveries'][0]['reason']=='inbox_delivery_directory_changed'
    assert not report['captures']


def test_candidate_swap_never_publishes_valid_lookup(tmp_path, monkeypatch):
    root=tmp_path/'inbox'; root.mkdir(); candidate=delivery(root)
    replacement=tmp_path/'replacement'; shutil.copytree(candidate,replacement)
    original=inbox.inspect_delivery
    def replace(*args, **kwargs):
        candidate.rename(tmp_path/'old-held-dir')
        replacement.rename(candidate)
        return original(*args, **kwargs)
    monkeypatch.setattr(inbox,'inspect_delivery',replace)
    with pytest.raises(inbox.Refused, match='inbox_changed_no_partial_inventory'):
        inbox.inventory(root)


@pytest.mark.parametrize('name',['delivery.v1.txt','segment.v1.bin','audio.wav'])
def test_member_fifo_never_blocks(tmp_path,name):
    root=tmp_path/'inbox'; root.mkdir(); candidate=delivery(root)
    target=candidate/name;target.unlink();os.mkfifo(target)
    report=inbox.inventory(root)
    assert report['deliveries'][0]['status']=='unresolved'
    assert not report['captures']


def test_budget_enforced_before_any_wav_read(tmp_path,monkeypatch):
    root=tmp_path/'inbox';root.mkdir();candidate=delivery(root)
    original=transport.os.fdopen
    def checked(fd,*a,**k):
        stream=original(fd,*a,**k)
        if os.readlink('/proc/self/fd/'+str(fd)).endswith('/audio.wav'):
            class NoWavRead:
                def __enter__(self):return self
                def __exit__(self,*a):stream.close()
                def fileno(self):return stream.fileno()
                def read(self,*a):pytest.fail('WAV read before budget rejection')
            return NoWavRead()
        return stream
    monkeypatch.setattr(transport.os,'fdopen',checked)
    with pytest.raises(inbox.Refused,match='wav_budget_exceeded'):
        transport.inspect_delivery(candidate,validate_metadata=True,max_wav_bytes=51)


def test_all_ignored_is_explicit(tmp_path):
    (tmp_path/'foo').write_bytes(b'')
    result=inbox.inventory(tmp_path)
    assert result['ignored_entries']==1 and result['deliveries']==result['captures']==[]
