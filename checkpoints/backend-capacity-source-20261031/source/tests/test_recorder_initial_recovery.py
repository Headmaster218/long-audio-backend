"""Hard-exit initial-admission recovery; exact identities, no blind replay."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from audio_backend import recorder_capture_intake as capture
from audio_backend import recording_uploader as u
from test_recorder_capture_intake import options
from test_recording_session_contract import handler_app, session, HEADERS
from test_strict_uploader_admission import StrictRoutes


CHILD = '''
import json, os, sys
from audio_backend import recorder_capture_intake as c
from audio_backend import recording_uploader as u
args=json.loads(sys.argv[1]); contract=json.loads(sys.argv[2])
class ReadOnly:
    def request(self, method, path, **kwargs):
        assert method == 'GET' and path.endswith('/recording-contract')
        return contract
c.CaptureUploader.run=lambda self: os._exit(73)
c.run(**args, transport=ReadOnly(), limits=u.Limits(disk_floor=0,deadline_seconds=30))
'''


def interrupted(tmp_path, client):
    paths,args=options(tmp_path)
    args.update(session_id=session(client),transport=StrictRoutes(client))
    wire={k:str(v) if isinstance(v,Path) else v for k,v in args.items() if k not in ('limits','transport')}
    wire.update(delivery=str(paths[0]),start=True)
    contract=client.get('/sessions/'+args['session_id']+'/recording-contract',headers=HEADERS).json()
    child=subprocess.run([sys.executable,'-c',CHILD,json.dumps(wire),json.dumps(contract)],capture_output=True,timeout=40)
    assert child.returncode==73,child.stderr.decode()
    assert (args['job_dir']/'admissions/0/admission.json').is_file()
    assert set(p.name for p in (args['job_dir']/'uploader').iterdir())=={'uploader.lock'}
    return paths,args


def test_hard_exit_recovery_and_exact_repeat(tmp_path):
    with handler_app(tmp_path/'server') as (client,app):
        paths,args=interrupted(tmp_path,client)
        original={str(p):p.read_bytes() for p in (args['job_dir']/'admissions').rglob('*') if p.is_file()}
        with pytest.raises(FileNotFoundError):capture.run(**args,delivery=paths[0])
        result=capture.run(**args,delivery=paths[0],recover_initial=True)
        assert result['inputs']==1 and result['frames']==result['staged_frames']==4
        assert result['published_frames']==0 and not result['capture_finalized']
        assert all(m=='GET' for m,_,_ in args['transport'].calls)
        saved=json.loads((args['job_dir']/'uploader/uploader.json').read_text())
        assert saved['active_page']['pieces'][0]['input_id']==json.loads((args['job_dir']/'admissions/0/admission.json').read_text())['delivery']['source_id']
        with pytest.raises(capture.Refused,match='pristine_uploader'):
            capture.run(**args,delivery=paths[0],recover_initial=True)
        assert capture.run(**args,delivery=paths[0])['frames']==4
        assert capture.run(**args,delivery=paths[1])['frames']==8
        assert original=={str(p):p.read_bytes() for p in (args['job_dir']/'admissions/0').rglob('*') if p.is_file()}
        assert not app.state.worker.thread.is_alive()


@pytest.mark.parametrize('kind',['checkpoint','temp','history','spool','symlink'])
def test_any_uploader_artifact_refuses_before_http(tmp_path,kind):
    with handler_app(tmp_path/'server') as (client,app):
        paths,args=interrupted(tmp_path,client)
        directory=args['job_dir']/'uploader'
        if kind=='history':(directory/'uploader.history').mkdir()
        elif kind=='symlink':(directory/'uploader.json').symlink_to(tmp_path/'absent')
        else:(directory/{'checkpoint':'uploader.json','temp':'uploader.json.tmp','spool':'piece.wav'}[kind]).write_bytes(b'preserved')
        with pytest.raises(capture.Refused,match='pristine_uploader'):
            capture.run(**args,delivery=paths[0],recover_initial=True)
        assert not args['transport'].calls


@pytest.mark.parametrize('kind',['path','next','original','snapshot','receipt','partial'])
def test_changed_or_partial_admission_preserved(tmp_path,kind):
    with handler_app(tmp_path/'server') as (client,app):
        paths,args=interrupted(tmp_path,client); selected=paths[0]
        if kind=='path':
            selected=tmp_path/'substitute';shutil.copytree(paths[0],selected)
        elif kind=='next':selected=paths[1]
        elif kind=='original':(paths[0]/'audio.wav').write_bytes(b'changed')
        elif kind=='snapshot':(args['job_dir']/'admissions/0/snapshot/audio.wav').write_bytes(b'changed')
        elif kind=='receipt':(args['job_dir']/'admissions/0/admission.json').write_bytes(b'{}\n')
        else:(args['job_dir']/'admissions/0/admission.json').unlink()
        before={str(p):p.read_bytes() for p in args['job_dir'].rglob('*') if p.is_file()}
        with pytest.raises((capture.Refused,KeyError)):
            capture.run(**args,delivery=selected,recover_initial=True)
        assert before=={str(p):p.read_bytes() for p in args['job_dir'].rglob('*') if p.is_file()}
        assert not args['transport'].calls


@pytest.mark.parametrize('kind',['capture','chunks','uncertain'])
def test_remote_not_positively_empty_never_starts_uploader(tmp_path,kind):
    with handler_app(tmp_path/'server') as (client,app):
        paths,args=interrupted(tmp_path,client);routes=args['transport']
        class Remote:
            def request(self,method,path,**kwargs):
                assert method=='GET'
                if kind=='uncertain':raise u.Uncertain('response unavailable')
                value=routes.request(method,path,**kwargs)
                if path.endswith('/recording-contract'):
                    if kind=='capture':value['capture']={'capture_id':args['capture_id'],'capture_origin_frame':0}
                    else:value['has_chunks']=True
                return value
        args['transport']=Remote()
        if kind=='uncertain':args['limits']=u.Limits(disk_floor=0,deadline_seconds=1,poll_seconds=.001)
        with pytest.raises((capture.Refused,u.Incomplete)):
            capture.run(**args,delivery=paths[0],recover_initial=True)
        assert not (args['job_dir']/'uploader/uploader.json').exists()


def test_recovery_mode_excludes_start_finalize(tmp_path):
    paths,args=options(tmp_path)
    for mode in ({'start':True,'delivery':paths[0]},{'finalize':True}):
        with pytest.raises(capture.Refused,match='recovery_requires'):
            capture.run(**args,**mode,recover_initial=True)
    assert not args['job_dir'].exists()


def test_recovery_fsync_failure_does_not_initialize(tmp_path,monkeypatch):
    with handler_app(tmp_path/'server') as (client,app):
        paths,args=interrupted(tmp_path,client)
        original=capture.durable_admission
        def fail(receipt,remaining):
            original(receipt,remaining)
            raise OSError('injected durability failure')
        monkeypatch.setattr(capture,'durable_admission',fail)
        with pytest.raises(OSError):capture.run(**args,delivery=paths[0],recover_initial=True)
        assert not (args['job_dir']/'uploader/uploader.json').exists()
        assert all(m=='GET' for m,_,_ in args['transport'].calls)
        monkeypatch.setattr(capture,'durable_admission',original)
        assert capture.run(**args,delivery=paths[0],recover_initial=True)['frames']==4


def test_interrupted_recovery_uses_normal_checkpoint_retry(tmp_path,monkeypatch):
    with handler_app(tmp_path/'server') as (client,app):
        paths,args=interrupted(tmp_path,client)
        original=u.PagedJournal.initialize
        class Interrupted(BaseException):pass
        def stop(self,*a,**kw):
            original(self,*a,**kw)
            raise Interrupted()
        monkeypatch.setattr(u.PagedJournal,'initialize',stop)
        with pytest.raises(Interrupted):capture.run(**args,delivery=paths[0],recover_initial=True)
        assert (args['job_dir']/'uploader/uploader.json').is_file()
        monkeypatch.setattr(u.PagedJournal,'initialize',original)
        with pytest.raises(capture.Refused,match='pristine_uploader'):
            capture.run(**args,delivery=paths[0],recover_initial=True)
        assert capture.run(**args,delivery=paths[0])['frames']==4
        assert all(m=='GET' for m,_,_ in args['transport'].calls)
