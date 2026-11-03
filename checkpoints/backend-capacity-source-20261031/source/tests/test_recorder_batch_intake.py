"""Fixed-batch strict ASGI admission and real journal recovery; no native work."""
import json
import pytest
from audio_backend import recorder_batch_intake as batch
from audio_backend import recording_uploader as u
from test_recorder_sequence import fixture as segment
from test_recording_session_contract import handler_app, session
from test_strict_uploader_admission import StrictRoutes, AdmissionBoundary


def fixture(tmp_path, count=3):
    deliveries=[]
    for index in range(count):
        root=tmp_path / ('delivery'+str(index))
        segment(root,index,close='STOP' if index==count-1 else 'ROTATION')
        deliveries.append(root)
    return dict(deliveries=deliveries,job_dir=tmp_path/'job',api='http://127.0.0.1:8765',
        session_id='replace',capture_id='batch-capture',exclusive_writer=True,
        limits=u.Limits(disk_floor=0,deadline_seconds=30,poll_seconds=.001))


@pytest.mark.parametrize('lose_put',[False,True])
def test_three_segment_real_admission_and_finalization_resume(tmp_path,lose_put):
    args=fixture(tmp_path)
    before=[{p.name:p.read_bytes() for p in root.iterdir()} for root in args['deliveries']]
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client,lose_put);args.update(session_id=session(client),transport=routes)
        with pytest.raises(AdmissionBoundary):batch.run(**args)
        saved=json.loads((args['job_dir']/'uploader/uploader.json').read_text())
        assert [e['frames'] for e in saved['inputs']]==[4,4,4]
        assert [e['normalized_start'] for e in saved['inputs']]==[0,4,8]
        assert saved['finalize_requested'] and len(routes.puts)==3
        assert saved['pages'][0]['seal']['eos'] is True
        prior=list(routes.puts)
        with pytest.raises(AdmissionBoundary):batch.run(**args,resume=True)
        assert routes.puts==prior
        assert [{p.name:p.read_bytes() for p in root.iterdir()} for root in args['deliveries']]==before
        assert not app.state.worker.thread.is_alive()


def test_resume_active_input_rebuilds_missing_owned_piece(tmp_path,monkeypatch):
    args=fixture(tmp_path);original=batch.BatchUploader._new_piece
    class Interrupted(BaseException):pass
    armed=True
    def interrupted(self,row):
        nonlocal armed
        result=original(self,row)
        if armed and self.input_id.endswith('-1.ready') and result is not None:
            armed=False;raise Interrupted()
        return result
    monkeypatch.setattr(batch.BatchUploader,'_new_piece',interrupted)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        with pytest.raises(Interrupted):batch.run(**args)
        state=args['job_dir']/'uploader';saved=json.loads((state/'uploader.json').read_text())
        assert [e['clean_eof'] for e in saved['inputs']]==[True,False]
        piece=saved['pages'][0]['pieces'][-1]
        assert piece['input_id'].endswith('-1.ready')
        (state/piece['name']).unlink()  # Owned generated spool only; original/snapshot retained.
        with pytest.raises(AdmissionBoundary):batch.run(**args,resume=True)
        after=json.loads((state/'uploader.json').read_text())
        assert [e['clean_eof'] for e in after['inputs']]==[True,True,True]
        assert len(routes.puts)==3


@pytest.mark.parametrize('kind,reason',[
    ('missing','missing_ordinals'),('epoch','epoch_boundary_unmapped'),
    ('gap','declared_frame_gap'),('seam','previous_seam_not_trusted')])
def test_unresolved_boundary_before_any_http(tmp_path,kind,reason):
    first=tmp_path/'first';second=tmp_path/'second'
    segment(first,0)
    options=dict(close='STOP')
    seq=1
    if kind=='missing':seq=2
    if kind=='epoch':options.update(epoch=1,start=0,boundary='RESTART',run='new',seam=False)
    if kind=='gap':options.update(start=5)
    if kind=='seam':options.update(seam=False)
    segment(second,seq,**options)
    class Forbidden:
        def request(self,*a,**kw):raise AssertionError('No HTTP for unresolved batch')
    with pytest.raises(batch.BoundaryRefused) as error:
        batch.run(deliveries=[first,second],job_dir=tmp_path/'job',api='https://example.org',
            session_id='s',capture_id='c',exclusive_writer=True,transport=Forbidden())
    assert reason in error.value.reasons
    assert not (tmp_path/'job/manifest.json').exists()


@pytest.mark.parametrize('change',['target','order','lost_journal','changed_registry'])
def test_fixed_manifest_resume_refusal(tmp_path,change):
    args=fixture(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        with pytest.raises(AdmissionBoundary):batch.run(**args)
        state=args['job_dir']/'uploader/uploader.json'
        if change=='target':args['capture_id']='different'
        elif change=='order':args['deliveries'].reverse()
        elif change=='lost_journal':state.rename(state.with_suffix('.lost'))
        else:
            d=json.loads(state.read_text());d['inputs'][0]['identity']['path']+='.other';state.write_text(json.dumps(d))
        before=len(routes.calls)
        with pytest.raises((batch.Refused,FileNotFoundError)):
            batch.run(**args,resume=True)
        assert all(m=='GET' for m,_,_ in routes.calls[before:])


def test_capacity_simulation_boundaries():
    batch.capacity_plan([1]*14)
    with pytest.raises(batch.Refused,match='source_capacity'):batch.capacity_plan([1]*15)
    batch.capacity_plan([u.PAGE_FRAMES,1])
    batch.capacity_plan([u.SOURCE_FRAMES+1]*8)


def test_cli_reports_boundary_codes(monkeypatch,capsys):
    def refused(**kwargs):raise batch.BoundaryRefused(['missing_ordinals','epoch_boundary_unmapped'])
    monkeypatch.setattr(batch,'run',refused)
    assert batch.main(['--delivery','a','--delivery','b','--job-dir','j','--api','https://example.org',
        '--session-id','s','--capture-id','c','--exclusive-writer'])==2
    result=json.loads(capsys.readouterr().err)
    assert result['status']=='unresolved' and result['boundary_reason_codes']==['epoch_boundary_unmapped','missing_ordinals']


@pytest.mark.parametrize('count',[0,1,9])
def test_fixed_input_count_refuses_before_inspection(count,tmp_path):
    with pytest.raises(batch.Refused,match='input_count'):
        batch.run(deliveries=[None]*count,job_dir=tmp_path/'job',api='https://example.org',
                  session_id='s',capture_id='c',exclusive_writer=True)
    assert not (tmp_path/'job').exists()


def test_two_hour_source_slot_plan():
    batch.capacity_plan([15*60*u.RATE]*8)
