"""Real strict route admission, never native lifespan or simulated publication."""
import copy
import json
from pathlib import Path

import pytest
from audio_backend import recorder_intake as intake
from audio_backend import recording_uploader as u
from test_recorder_metadata import semantic_delivery
from test_recording_session_contract import handler_app, session, HEADERS
from test_strict_uploader_admission import StrictRoutes, AdmissionBoundary


def fixture(tmp_path):
    source = tmp_path / 'delivery'
    source.mkdir()
    semantic_delivery(source)
    return dict(delivery=source, job_dir=tmp_path / 'job', api='http://127.0.0.1:8765',
                session_id='filled-by-test', capture_id='explicit-capture', exclusive_writer=True,
                limits=u.Limits(disk_floor=0, deadline_seconds=30, poll_seconds=.001))


@pytest.mark.parametrize('lose_put', [False, True])
def test_real_strict_admission_resume_exact_job(tmp_path, lose_put):
    args = fixture(tmp_path)
    original = {p.name:p.read_bytes() for p in args['delivery'].iterdir()}
    with handler_app(tmp_path / 'server') as (client, app):
        args.update(session_id=session(client), transport=StrictRoutes(client, lose_put))
        with pytest.raises(AdmissionBoundary): intake.run(**args)
        journal = args['job_dir'] / 'uploader/uploader.json'
        saved = json.loads(journal.read_text())
        snapshot = args['job_dir'] / 'snapshot/audio.wav'
        stamp = snapshot.stat()
        puts = list(args['transport'].puts)
        assert len(puts) == 1 and saved['pages'][0]['create']
        with pytest.raises(AdmissionBoundary): intake.run(**args, resume=True)
        assert args['transport'].puts == puts
        assert snapshot.stat() == stamp
        assert {p.name:p.read_bytes() for p in args['delivery'].iterdir()} == original
        assert not app.state.worker.thread.is_alive()
        assert len(saved['pages']) == 1 and saved['pages'][0]['pieces'][0]['frames'] == 3


@pytest.mark.parametrize('change', ['target','lost_journal','snapshot','original','foreign_capture'])
def test_resume_refuses_without_writes(tmp_path, change):
    args = fixture(tmp_path)
    with handler_app(tmp_path / 'server') as (client, app):
        args.update(session_id=session(client), transport=StrictRoutes(client))
        with pytest.raises(AdmissionBoundary): intake.run(**args)
        if change == 'target': args['capture_id'] = 'another'
        elif change == 'lost_journal': (args['job_dir'] / 'uploader/uploader.json').rename(args['job_dir'] / 'uploader/lost.json')
        elif change == 'snapshot': (args['job_dir'] / 'snapshot/audio.wav').write_bytes(b'changed')
        elif change == 'original': (args['delivery'] / 'audio.wav').write_bytes(b'changed')
        else:
            saved = args['job_dir'] / 'uploader/uploader.json'
            data=json.loads(saved.read_text()); data['pages']=[]; saved.write_text(json.dumps(data))
        before=len(args['transport'].calls)
        with pytest.raises((intake.Refused,u.Incomplete,FileNotFoundError)):
            intake.run(**args, resume=True)
        assert all(method=='GET' for method,_,_ in args['transport'].calls[before:])


def test_existing_capture_refused_and_no_session_creation(tmp_path):
    args = fixture(tmp_path)
    with handler_app(tmp_path / 'server') as (client, app):
        sid=session(client); args.update(session_id=sid,transport=StrictRoutes(client))
        assert client.post(f'/sessions/{sid}/recordings/explicit-capture/pages',
            json={'page_number':1,'capture_origin_frame':0},headers=HEADERS).status_code==202
        with pytest.raises(u.Incomplete,match='Capture exists'): intake.run(**args)
        assert all(m=='GET' for m,_,_ in args['transport'].calls)


def test_rejected_remote_identity_does_not_poison_row():
    obj=object.__new__(intake.IntakeUploader);obj.capture='capture';obj.origin=0
    row=dict(number=1,pieces=[],released=False)
    remote=dict(sources=[])
    before=copy.deepcopy(row)
    with pytest.raises(u.Incomplete,match='create request'):obj._match(row,remote)
    assert row==before
    row['create']={'page_number':1,'capture_origin_frame':0};before=copy.deepcopy(row)
    remote.update(policy='sealed-recording-exact-overlap-v1',page_number=1,capture_id='capture',
                  capture_origin_frame=0,id='foreign-id',state='sealed',released=False)
    with pytest.raises(u.Incomplete,match='Frozen server page'):obj._match(row,remote)
    assert row==before


@pytest.mark.parametrize('change', [dict(strict_primary_enabled=False),dict(strict_primary_enabled=1),
    dict(strict_primary_policy='strict-primary-manual-v1'),dict(max_source_frames=2),
    dict(max_source_bytes=49),dict(source_idempotency={}),dict(source_format='other')])
def test_incompatible_contract_no_writes(tmp_path,change):
    args=fixture(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        sid=session(client);contract=client.get(f'/sessions/{sid}/recording-contract',headers=HEADERS).json()
        with pytest.raises(intake.Refused):intake.check_contract(contract|change,sid,3)


def test_cli_unresolved_does_not_leak_exception(monkeypatch,capsys):
    def broken(**kwargs):raise u.Uncertain('SECRET and a server response')
    monkeypatch.setattr(intake,'run',broken)
    assert intake.main(['--delivery','a','--job-dir','b','--api','https://example.org',
                        '--session-id','s','--capture-id','c','--exclusive-writer'])==2
    out=capsys.readouterr();assert not out.out and 'SECRET' not in out.err
    result=json.loads(out.err)
    assert result['status']=='unresolved' and result['possible_remote_effects']
    assert result['preserve_job_and_original']


def test_requires_explicit_private_new_job_and_writer(tmp_path):
    args=fixture(tmp_path);args['exclusive_writer']=False
    with pytest.raises(intake.Refused,match='exclusive_writer'):intake.run(**args)
    args['exclusive_writer']=True;args['job_dir'].mkdir(mode=0o700)
    with pytest.raises(FileExistsError):intake.run(**args)


def test_foreign_empty_page_between_checks_refused(tmp_path):
    args=fixture(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        sid=session(client)
        class RacingRoutes(StrictRoutes):
            def request(self,method,path,**kwargs):
                if method=='GET' and path.endswith('/pages/1'):
                    client.post(f'/sessions/{sid}/recordings/explicit-capture/pages',
                        json={'page_number':1,'capture_origin_frame':0},headers=HEADERS)
                return super().request(method,path,**kwargs)
        routes=RacingRoutes(client);args.update(session_id=sid,transport=routes)
        with pytest.raises(u.Incomplete,match='create request'):intake.run(**args)
        saved=json.loads((args['job_dir']/'uploader/uploader.json').read_text())
        assert 'server_id' not in saved['pages'][0]
        assert not routes.puts and all(m=='GET' for m,_,_ in routes.calls)


def test_remote_extra_history_resume_refused(tmp_path):
    args=fixture(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        args.update(session_id=session(client),transport=StrictRoutes(client))
        with pytest.raises(AdmissionBoundary):intake.run(**args)
        class ExtraHistory(StrictRoutes):
            def request(self,method,path,**kwargs):
                reply=super().request(method,path,**kwargs)
                if path.endswith('/recordings/explicit-capture'):reply['page_count']+=1
                return reply
        routes=ExtraHistory(client);args['transport']=routes
        with pytest.raises(intake.Refused,match='capture_history'):intake.run(**args,resume=True)
        assert all(m=='GET' for m,_,_ in routes.calls)


def test_pre_journal_contract_failure_preserved_and_refused(tmp_path):
    args=fixture(tmp_path)
    class Unauthorized:
        def request(self,*a,**kw):raise u.HTTPFailure(401,'private response',0)
    args['transport']=Unauthorized()
    with pytest.raises(u.HTTPFailure):intake.run(**args)
    assert (args['job_dir']/'manifest.json').is_file()
    with pytest.raises(FileNotFoundError):intake.run(**args,resume=True)


@pytest.mark.parametrize('error,category', [
    (u.HTTPFailure(401,'secret',0),'authentication_required'),
    (u.HTTPFailure(409,'secret',0),'server_conflict'),
    (u.Uncertain('secret'),'transport_or_local_io_uncertain'),
    (OSError('secret'),'transport_or_local_io_uncertain'),
    (u.Incomplete('Uploader deadline reached; capture prefix retained'),'deadline_pending_or_uncertain'),
    (u.Incomplete('Admission blocked by worker failed: private-chunk'),'worker_failed'),
    (u.Incomplete('Admission blocked by worker paused: private-chunk'),'worker_paused'),
    (u.Incomplete('Admission blocked by worker cancelled: private-chunk'),'worker_cancelled')])
def test_safe_visible_failure_categories(error,category):
    result=intake.failure_report(error)
    assert result['category']==category and result['status']=='unresolved'
    assert result['possible_remote_effects']
    assert 'secret' not in json.dumps(result) and 'private-chunk' not in json.dumps(result)
