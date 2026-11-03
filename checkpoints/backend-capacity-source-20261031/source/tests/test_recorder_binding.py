"""Disabled proposals and local exact journals; never outgoing backend requests."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from audio_backend import recorder_binding as b
from test_recorder_sequence import fixture

OPTIONS=dict(origin='http://127.0.0.1:8765',backend_id='backend-fixture',session_id='session-fixture',capture_id='capture-fixture')


def build(paths,**options):return b.build_proposal(paths,**(OPTIONS|options))
def bind(paths,journal,**options):return b.bind_proposal(paths,journal,**(OPTIONS|options))
def contents(path):return {p.name:p.read_bytes() for p in path.iterdir()}


def test_readonly_proposal_deterministic_order_and_duplicate_repeat(tmp_path):
    a=fixture(tmp_path/'a');c=fixture(tmp_path/'b',1)
    before={str(p):p.read_bytes() for root in (a,c) for p in root.iterdir()}
    first=build([a,c]);second=build([c,a,a])
    assert first==second and first['activation']=='disabled' and not first['strict_import_ready']
    assert not first['target_identity_verified'] and not first['server_reservation_proven']
    assert [(r['start_frame'],r['end_frame']) for r in first['sources']]==[(0,4),(4,8)]
    assert before=={str(p):p.read_bytes() for root in (a,c) for p in root.iterdir()}


def test_exact_journal_retry_is_readonly_observation(tmp_path):
    a=fixture(tmp_path/'a');journal=tmp_path/'journal'
    first=bind([a],journal);before=contents(journal)
    second=bind([a],journal)
    assert first['status']=='created_and_fsynced'
    assert second['status']=='existing_exact_observation' and not second['publication_durability_proven']
    assert first['journal_sha256']==second['journal_sha256'] and contents(journal)==before


@pytest.mark.parametrize('change',[{'capture_id':'other'},{'session_id':'other'},{'backend_id':'other'},{'capture_origin_frame':10},{'origin':'https://example.invalid'}])
def test_changed_target_conflicts_with_existing_journal(tmp_path,change):
    a=fixture(tmp_path/'a');journal=tmp_path/'journal';bind([a],journal);before=contents(journal)
    with pytest.raises(b.Refused,match='binding_conflict'):bind([a],journal,**change)
    assert contents(journal)==before


def test_changed_body_keeps_same_source_key_and_conflicts(tmp_path):
    a=fixture(tmp_path/'a');changed=fixture(tmp_path/'changed',requested='other')
    assert build([a])['sources'][0]['source_key']==build([changed])['sources'][0]['source_key']
    journal=tmp_path/'journal';bind([a],journal);before=contents(journal)
    with pytest.raises(b.Refused,match='binding_conflict'):bind([changed],journal)
    assert contents(journal)==before


def test_origin_coordinate_changes_do_not_mint_new_source_key(tmp_path):
    a=fixture(tmp_path/'a')
    assert build([a])['sources'][0]['source_key']==build([a],capture_origin_frame=99)['sources'][0]['source_key']


@pytest.mark.parametrize('case',['prefix','gap','overlap','restart','multi_capture','stereo','rate','conflict'])
def test_unresolved_or_unsupported_inputs_refused_before_journal(tmp_path,case):
    a=fixture(tmp_path/'a');paths=[a]
    if case=='prefix':paths=[fixture(tmp_path/'b',1)]
    elif case=='gap':paths.append(fixture(tmp_path/'b',2))
    elif case=='overlap':paths.append(fixture(tmp_path/'b',1,start=3))
    elif case=='restart':paths.append(fixture(tmp_path/'b',1,epoch=1,run='new',boundary='RESTART',start=0,seam=False))
    elif case=='multi_capture':paths.append(fixture(tmp_path/'b',capture='other'))
    elif case=='stereo':paths=[fixture(tmp_path/'b',channels=2)]
    elif case=='rate':paths=[fixture(tmp_path/'b',rate=48000)]
    else:paths.append(fixture(tmp_path/'b',requested='other'))
    journal=tmp_path/'journal'
    expected='binding_requires_pcm16_mono_16000' if case in ('stereo','rate') else 'binding_'
    with pytest.raises(b.Refused,match=expected):bind(paths,journal)
    assert not journal.exists()


@pytest.mark.parametrize('origin',[
    'http://example.invalid','https://user:secret@example.invalid','https://@example.invalid',
    'https://example.invalid/path','https://example.invalid?token=x','https://example.invalid#secret',
    'https://exa\nmple.invalid','https://example.invalid:0','https://example.invalid:99999',
    'file:///tmp','https://[bad]','https://example.invalid/ '])
def test_credential_or_unsafe_target_origin_refused(origin):
    with pytest.raises(b.Refused):b.target(origin,'backend','session','capture')


def test_origin_canonicalization():
    a=b.target('https://EXAMPLE.invalid:443/','b','s','c')
    assert a==b.target('https://example.invalid','b','s','c')
    assert b.target('http://[0:0:0:0:0:0:0:1]:80/','b','s','c')['origin']=='http://[::1]'


@pytest.mark.parametrize('field,value',[('backend_id','bad/name'),('session_id',''),('capture_id','a'*129),('capture_origin_frame',True),('capture_origin_frame',-1),('capture_origin_frame',2**53)])
def test_target_bounds(field,value):
    with pytest.raises(b.Refused):b.target(**(OPTIONS|{field:value}))


@pytest.mark.parametrize('cut',[1,2,3])
def test_publication_fsync_failure_preserves_state(tmp_path,monkeypatch,cut):
    a=fixture(tmp_path/'a');journal=tmp_path/'journal';original=os.fsync;count=0
    def fail(fd):
        nonlocal count
        count+=1
        if count==cut:raise OSError('injected fsync')
        return original(fd)
    monkeypatch.setattr(b.os,'fsync',fail)
    with pytest.raises(OSError):bind([a],journal)
    before=contents(journal)
    if cut==1:
        with pytest.raises(b.Refused,match='incomplete'):bind([a],journal)
    else:assert bind([a],journal)['status']=='existing_exact_observation'
    assert contents(journal)==before


def test_incomplete_and_tampered_journal_never_repaired(tmp_path):
    a=fixture(tmp_path/'a');journal=tmp_path/'journal';journal.mkdir()
    with pytest.raises(b.Refused,match='incomplete'):bind([a],journal)
    other=tmp_path/'other';bind([a],other)
    (other/b.FINAL).write_bytes(b'bad')
    with pytest.raises(b.Refused):bind([a],other)
    assert (other/b.FINAL).read_bytes()==b'bad'


def test_single_private_prefix_cannot_silently_extend(tmp_path):
    a=fixture(tmp_path/'a');c=fixture(tmp_path/'b',1);journal=tmp_path/'journal';bind([a],journal)
    with pytest.raises(b.Refused,match='binding_conflict'):bind([a,c],journal)


def test_cli_readonly_by_default(tmp_path,capsys):
    a=fixture(tmp_path/'a');before=set(tmp_path.iterdir())
    argv=[str(a),'--origin',OPTIONS['origin'],'--backend-id','backend','--session-id','session','--capture-id','capture']
    assert b.main(argv)==0
    assert json.loads(capsys.readouterr().out)['activation']=='disabled'
    assert set(tmp_path.iterdir())==before
    assert b.main(argv+['--journal',str(tmp_path/'journal')])==0
    assert json.loads(capsys.readouterr().out)['status']=='created_and_fsynced'


@pytest.mark.parametrize('cut',[1,2,3])
def test_abrupt_exit_exact_reconciliation_never_dispatches(tmp_path,cut):
    a=fixture(tmp_path/'a');journal=tmp_path/'journal';root=Path(__file__).resolve().parents[1]
    code='''
import os,sys
from audio_backend import recorder_binding as b
original=os.fsync
count=0
def stop(fd):
    global count
    count+=1
    if count==int(sys.argv[3]):os._exit(79)
    return original(fd)
os.fsync=stop
b.bind_proposal([sys.argv[1]],sys.argv[2],origin='http://127.0.0.1:8765',backend_id='backend-fixture',session_id='session-fixture',capture_id='capture-fixture')
'''
    result=subprocess.run([sys.executable,'-c',code,str(a),str(journal),str(cut)],cwd=root,capture_output=True,text=True,timeout=15)
    assert result.returncode==79,result.stderr
    before=contents(journal)
    if cut==1:
        with pytest.raises(b.Refused,match='incomplete'):bind([a],journal)
    else:
        observed=bind([a],journal)
        assert observed['status']=='existing_exact_observation' and not observed['strict_import_ready']
    assert contents(journal)==before


def test_publication_link_failure_leaves_incomplete_journal(tmp_path,monkeypatch):
    a=fixture(tmp_path/'a');journal=tmp_path/'journal'
    def fail(*args,**kwargs):raise OSError('injected link failure')
    monkeypatch.setattr(b.os,'link',fail)
    with pytest.raises(OSError):bind([a],journal)
    before=contents(journal)
    assert set(before)=={b.PENDING}
    with pytest.raises(b.Refused,match='incomplete'):bind([a],journal)
    assert contents(journal)==before


def test_shared_parent_and_symlink_journal_refused(tmp_path):
    a=fixture(tmp_path/'a');shared=tmp_path/'shared';shared.mkdir();shared.chmod(0o777)
    with pytest.raises(b.Refused,match='private_parent'):bind([a],shared/'journal')
    assert not (shared/'journal').exists()
    real=tmp_path/'real';bind([a],real);link=tmp_path/'link';link.symlink_to(real,target_is_directory=True)
    with pytest.raises(OSError):bind([a],link)


def test_hash_rebound_activation_tampering_is_not_authorization(tmp_path):
    import hashlib
    a=fixture(tmp_path/'a');journal=tmp_path/'journal';bind([a],journal)
    value=json.loads((journal/b.FINAL).read_bytes());value['proposal']['activation']='enabled'
    value['sha256']=hashlib.sha256(b._json(value['proposal'])).hexdigest()
    (journal/b.FINAL).write_bytes(b._json(value))
    with pytest.raises(b.Refused,match='binding_conflict'):bind([a],journal)


def test_local_creation_close_uncertainty_never_claims_success(tmp_path,monkeypatch):
    a=fixture(tmp_path/'a');journal=tmp_path/'journal';original=os.close;failed=[]
    def close(fd):
        cut=(journal/b.FINAL).exists() and os.fstat(fd).st_ino==journal.stat().st_ino and not failed
        original(fd)
        if cut:failed.append(True);raise OSError('injected post-close uncertainty')
    monkeypatch.setattr(b.os,'close',close)
    with pytest.raises(OSError):bind([a],journal)
    assert bind([a],journal)['status']=='existing_exact_observation'


def test_no_network_client_is_needed(tmp_path,monkeypatch):
    import socket
    a=fixture(tmp_path/'a')
    def blocked(*args,**kwargs):raise AssertionError('network is not part of binding proposal')
    monkeypatch.setattr(socket,'socket',blocked)
    proposal=build([a]);assert proposal['activation']=='disabled'
    assert bind([a],tmp_path/'journal')['strict_import_ready'] is False


def test_single_snapshot_preserves_missing_anchor_warning(tmp_path):
    a=fixture(tmp_path/'a')
    assert build([a])['warning_codes']==['anchor_observations_absent']
