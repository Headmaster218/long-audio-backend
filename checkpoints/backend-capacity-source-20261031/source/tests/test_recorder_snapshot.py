"""Generated local copy/crash-boundary controls; no real power-loss guarantee."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from audio_backend import recorder_snapshot as s
from test_recorder_metadata import semantic_delivery


def setup(tmp_path):
    source=tmp_path/'input';source.mkdir();semantic_delivery(source)
    return source,tmp_path/'snapshot'


def contents(path):return {p.name:p.read_bytes() for p in path.iterdir() if p.is_file()}


def test_new_snapshot_and_readonly_inspection(tmp_path):
    source,dest=setup(tmp_path);before=contents(source)
    result=s.create_snapshot(source,dest)
    assert result['created_and_fsynced'] and not result['strict_import_ready']
    assert contents(source)==before
    assert set(contents(dest))==set(s.FILES)|{s.PENDING,s.RECEIPT}
    snap_before=contents(dest)
    checked=s.inspect_snapshot(dest)
    assert checked['observed_snapshot_verified'] and not checked['publication_durability_proven']
    assert not checked['strict_import_ready'] and contents(dest)==snap_before
    for name in s.FILES:assert (dest/name).read_bytes()==before[name]


@pytest.mark.parametrize('state',['empty','partial','complete'])
def test_never_overwrites_existing_destination(tmp_path,state):
    source,dest=setup(tmp_path)
    if state=='complete':s.create_snapshot(source,dest)
    else:
        dest.mkdir()
        if state=='partial':(dest/'audio.wav').write_bytes(b'partial')
    before=contents(dest)
    with pytest.raises(FileExistsError):s.create_snapshot(source,dest)
    assert contents(dest)==before


def test_bad_source_has_no_destination_side_effect(tmp_path):
    source,dest=setup(tmp_path);(source/'audio.wav').write_bytes(b'bad')
    with pytest.raises(s.Refused):s.create_snapshot(source,dest)
    assert not dest.exists()


@pytest.mark.parametrize('cut',range(1,7))
def test_fsync_fault_never_reports_success_or_retries_destination(tmp_path,monkeypatch,cut):
    source,dest=setup(tmp_path);original=s.os.fsync;calls=[]
    def fail(fd):
        calls.append(fd)
        if len(calls)==cut:raise OSError('synthetic fsync failure')
        return original(fd)
    monkeypatch.setattr(s.os,'fsync',fail)
    with pytest.raises(OSError):s.create_snapshot(source,dest)
    assert dest.exists();before=contents(dest)
    with pytest.raises(FileExistsError):s.create_snapshot(source,dest)
    assert contents(dest)==before
    # A publication visible before a late fsync error is only an observed-byte
    # reconciliation, never retrospectively claimed creation/durability success.
    if cut>=5:
        result=s.inspect_snapshot(dest)
        assert result['observed_snapshot_verified'] and not result['publication_durability_proven']
    else:
        with pytest.raises(s.Refused):s.inspect_snapshot(dest)


def test_link_failure_preserves_staged_receipt(tmp_path,monkeypatch):
    source,dest=setup(tmp_path)
    def fail(*args,**kwargs):raise OSError('synthetic link failure')
    monkeypatch.setattr(s.os,'link',fail)
    with pytest.raises(OSError):s.create_snapshot(source,dest)
    assert (dest/s.PENDING).is_file() and not (dest/s.RECEIPT).exists()
    with pytest.raises(s.Refused):s.inspect_snapshot(dest)


@pytest.mark.parametrize('name',list(s.FILES)+[s.RECEIPT])
def test_postpublication_tampering_refused(tmp_path,name):
    source,dest=setup(tmp_path);s.create_snapshot(source,dest)
    data=bytearray((dest/name).read_bytes());data[-1]^=1;(dest/name).write_bytes(data)
    with pytest.raises(s.Refused):s.inspect_snapshot(dest)


def test_additional_files_refused(tmp_path):
    source,dest=setup(tmp_path);s.create_snapshot(source,dest);(dest/'unexpected').write_text('x')
    with pytest.raises(s.Refused,match='unexpected'):s.inspect_snapshot(dest)


def test_symlink_receipt_refused(tmp_path):
    source,dest=setup(tmp_path);s.create_snapshot(source,dest)
    (dest/s.RECEIPT).unlink();(dest/s.RECEIPT).symlink_to(dest/s.PENDING)
    with pytest.raises(OSError):s.inspect_snapshot(dest)


def test_preflight_source_mutation_refused(tmp_path,monkeypatch):
    source,dest=setup(tmp_path);original=s._inventory
    def changed(fd,remaining):
        result=original(fd,remaining)
        if not dest.exists():
            path=source/'audio.wav';data=bytearray(path.read_bytes());data[-1]^=1;path.write_bytes(data)
        return result
    monkeypatch.setattr(s,'_inventory',changed)
    with pytest.raises(s.Refused,match='snapshot_source_hash'):s.create_snapshot(source,dest)
    assert not (dest/s.RECEIPT).exists()


def test_cli_refusal_json_and_success(tmp_path,capsys):
    source,dest=setup(tmp_path)
    assert s.main(['create',str(source),str(dest)])==0
    assert json.loads(capsys.readouterr().out)['created_and_fsynced']
    assert s.main(['inspect',str(dest)])==0
    assert not json.loads(capsys.readouterr().out)['publication_durability_proven']
    assert s.main(['create',str(source),str(dest)])==2
    assert json.loads(capsys.readouterr().out)['preserve_existing_files']


def test_unknown_directory_close_does_not_report_creation_success(tmp_path,monkeypatch):
    source,dest=setup(tmp_path);original=s.os.close;calls=[]
    def close_then_fail(fd):
        fail=(dest/s.RECEIPT).exists() and os.fstat(fd).st_ino==dest.stat().st_ino and not calls
        original(fd)
        if fail:
            calls.append(fd)
            raise OSError('synthetic acknowledgement lost after directory close')
    monkeypatch.setattr(s.os,'close',close_then_fail)
    with pytest.raises(OSError):s.create_snapshot(source,dest)
    before=contents(dest)
    assert (dest/s.RECEIPT).exists()
    with pytest.raises(FileExistsError):s.create_snapshot(source,dest)
    assert contents(dest)==before
    assert s.inspect_snapshot(dest)['publication_durability_proven'] is False


def test_noncanonical_receipt_json_refused(tmp_path):
    source,dest=setup(tmp_path);s.create_snapshot(source,dest)
    value=json.loads((dest/s.RECEIPT).read_bytes())
    (dest/s.RECEIPT).write_text(json.dumps(value,indent=2))
    with pytest.raises(s.Refused,match='noncanonical'):s.inspect_snapshot(dest)


def test_directory_replacement_refused(tmp_path,monkeypatch):
    source,dest=setup(tmp_path);s.create_snapshot(source,dest)
    original=s.inspect_delivery
    def replace(path,**kwargs):
        result=original(path,**kwargs)
        path.rename(tmp_path/'moved');path.mkdir()
        return result
    monkeypatch.setattr(s,'inspect_delivery',replace)
    with pytest.raises(s.Refused,match='directory_replaced'):s.inspect_snapshot(dest)


def test_shared_writable_parent_refused_before_destination(tmp_path):
    source,dest=setup(tmp_path)
    shared=tmp_path/'shared';shared.mkdir(mode=0o777);shared.chmod(0o777)
    with pytest.raises(s.Refused,match='private_parent'):
        s.create_snapshot(source,shared/'target')
    assert not (shared/'target').exists()


def test_disk_headroom_refusal_has_no_destination(tmp_path,monkeypatch):
    from types import SimpleNamespace
    source,dest=setup(tmp_path)
    monkeypatch.setattr(s.os,'fstatvfs',lambda fd:SimpleNamespace(f_bavail=0,f_frsize=4096))
    with pytest.raises(s.Refused,match='disk_headroom'):s.create_snapshot(source,dest)
    assert not dest.exists()


@pytest.mark.parametrize('cut',[1,4,5,6])
def test_abrupt_process_exit_and_fresh_process_readonly_reconciliation(tmp_path,cut):
    import subprocess
    import sys
    source,dest=setup(tmp_path);before=contents(source)
    root=Path(__file__).resolve().parents[1]
    code='''
import os,sys
from audio_backend import recorder_snapshot as s
original=os.fsync
count=0
def interrupt(fd):
    global count
    count+=1
    if count==int(sys.argv[3]):os._exit(79)
    return original(fd)
s.os.fsync=interrupt
s.create_snapshot(sys.argv[1],sys.argv[2])
'''
    result=subprocess.run([sys.executable,'-c',code,str(source),str(dest),str(cut)],cwd=root,capture_output=True,text=True,timeout=15)
    assert result.returncode==79,result.stderr
    assert contents(source)==before
    snapshot_before=contents(dest)
    result=subprocess.run([sys.executable,'-m','audio_backend.recorder_snapshot','inspect',str(dest)],cwd=root,capture_output=True,text=True,timeout=15)
    assert result.returncode==(0 if cut>=5 else 2),result.stderr
    output=json.loads(result.stdout)
    assert output['strict_import_ready'] is False
    if cut>=5:assert output['publication_durability_proven'] is False
    assert contents(dest)==snapshot_before
