"""Read-only receiver observations never select retries or authorize ingestion."""
import hashlib
import json
import os
import shutil
from pathlib import Path
import pytest
from audio_backend import recorder_inbox as inbox
from test_recorder_sequence import fixture


def delivery(root, seq=0, **kwargs):
    temporary=root.parent/('source-'+str(seq)+'-'+str(len(list(root.parent.iterdir()))))
    fixture(temporary,seq,**kwargs)
    name=(temporary/'delivery.v1.txt').read_text().splitlines()[1]
    destination=root/name
    shutil.copytree(temporary,destination)
    return destination


def test_ordering_holes_and_no_terminal_inference(tmp_path):
    root=tmp_path/'inbox';root.mkdir()
    b=delivery(root,2,close='STOP');a=delivery(root,0)
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result=inbox.inventory(root)
    assert all(x['status']=='observed_valid' for x in result['deliveries'])
    group=result['captures'][0]
    assert [x['sequence'] for x in group['ordinals']]==[0,2]
    assert group['missing_ordinal_ranges']==[[1,1]]
    assert group['terminal_state']=='not_determined'
    assert result['observation_expired_on_return'] and not result['selection_performed']
    assert not result['intake_authorized'] and not result['physical_continuity_proven']
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_distinct_transport_attempts_never_choose_one(tmp_path):
    root=tmp_path/'inbox';root.mkdir();a=delivery(root,0,close='STOP')
    name=a.name[:-32]+'1'*32;b=root/name;shutil.copytree(a,b)
    marker=b/'delivery.v1.txt';marker.write_text(marker.read_text().replace(a.name,name))
    result=inbox.inventory(root)
    candidates=result['captures'][0]['ordinals'][0]
    assert candidates['selection_required'] and set(candidates['candidates'])=={a.name,b.name}
    assert result['captures'][0]['terminal_state']=='not_determined'


@pytest.mark.parametrize('kind',['missing','extra','symlink','wrongname','badbytes'])
def test_incomplete_or_changed_delivery_visible(tmp_path,kind):
    root=tmp_path/'inbox';root.mkdir();a=delivery(root)
    if kind=='missing':(a/'delivery.v1.txt').unlink()
    elif kind=='extra':(a/'pending').write_text('pending')
    elif kind=='symlink':
        moved=tmp_path/'elsewhere';a.rename(moved);a.symlink_to(moved,target_is_directory=True)
    elif kind=='wrongname':a.rename(root/(a.name[:-32]+'2'*32))
    else:(a/'audio.wav').write_bytes(b'broken')
    result=inbox.inventory(root)
    assert result['deliveries'][0]['status']=='unresolved' and not result['captures']


def test_root_symlink_and_entry_limit_before_hash(tmp_path,monkeypatch):
    root=tmp_path/'inbox';root.mkdir();delivery(root)
    link=tmp_path/'link';link.symlink_to(root,target_is_directory=True)
    with pytest.raises(OSError):inbox.inventory(link)
    (root/'other').touch()
    monkeypatch.setattr(inbox,'inspect_delivery',lambda *a,**k:pytest.fail('must refuse before hash'))
    with pytest.raises(inbox.Refused,match='entry_limit'):inbox.inventory(root,max_entries=1)


def test_byte_limit_no_partial_result_and_safe_cli(tmp_path,capsys):
    root=tmp_path/'inbox';root.mkdir();delivery(root)
    with pytest.raises(inbox.Refused,match='byte_limit'):inbox.inventory(root,max_bytes=1)
    assert inbox.main([str(root),'--max-bytes','1'])==2
    assert json.loads(capsys.readouterr().out)['status']=='unresolved'


def test_changed_parent_rejected(tmp_path,monkeypatch):
    root=tmp_path/'inbox';root.mkdir();delivery(root);original=inbox.inspect_delivery
    def changed(*a,**k):
        result=original(*a,**k);(root/'arrived-later').touch();return result
    monkeypatch.setattr(inbox,'inspect_delivery',changed)
    with pytest.raises(inbox.Refused,match='inbox_changed'):inbox.inventory(root)


@pytest.mark.parametrize('kwargs',[{'max_entries':True},{'max_entries':257},{'max_bytes':0},{'deadline_seconds':float('nan')}])
def test_bounds(kwargs,tmp_path):
    with pytest.raises(inbox.Refused,match='bounds'):inbox.inventory(tmp_path,**kwargs)


def test_failed_hash_consumes_reserved_budget(tmp_path,monkeypatch):
    root=tmp_path/'inbox';root.mkdir();delivery(root,0);delivery(root,1)
    calls=[]
    def corrupt(*a,**k):
        calls.append(k['max_wav_bytes'])
        raise inbox.Refused('wav_hash_or_trailing')
    monkeypatch.setattr(inbox,'inspect_delivery',corrupt)
    with pytest.raises(inbox.Refused,match='byte_limit'):
        inbox.inventory(root,max_bytes=1024)
    assert calls==[1024]


def test_inspector_rejects_marker_over_budget(tmp_path):
    root=tmp_path/'inbox';root.mkdir();path=delivery(root)
    with pytest.raises(inbox.Refused,match='wav_budget_exceeded'):
        inbox.inspect_delivery(path,max_wav_bytes=45)
    for value in (True,0,44,inbox.MAX_WAV+1):
        with pytest.raises(inbox.Refused,match='wav_budget_bounds'):
            inbox.inspect_delivery(path,max_wav_bytes=value)
