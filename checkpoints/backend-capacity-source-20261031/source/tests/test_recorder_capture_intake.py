"""Incremental v3 recorder admission; strict handlers, no native lifespan."""
import json
import fcntl
from pathlib import Path
import pytest
from audio_backend import recorder_capture_intake as capture
from audio_backend import recording_uploader as u
from test_recorder_batch_intake import fixture
from test_recorder_sequence import fixture as segment
from test_recording_session_contract import handler_app,session
from test_strict_uploader_admission import StrictRoutes,AdmissionBoundary


def options(tmp_path):
    args=fixture(tmp_path);paths=args.pop('deliveries')
    return paths,args


@pytest.mark.parametrize('lose_put',[False,True])
def test_incremental_eof_never_sends_eos_and_explicit_finalize_retries(tmp_path,lose_put):
    paths,args=options(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client,lose_put);args.update(session_id=session(client),transport=routes)
        for index,path in enumerate(paths):
            result=capture.run(**args,delivery=path,start=index==0)
            assert result['status']=='delivery_staged_without_eos'
            assert not result['capture_finalized'] and result['published_frames']==0
            assert result['staged_frames']==4*(index+1) and result['max_hours']==12
        assert all(m=='GET' for m,_,_ in routes.calls)
        state=args['job_dir']/'uploader/uploader.json';saved=json.loads(state.read_text())
        assert saved['policy']==u.APPEND_V3_POLICY and saved['caps']['output_frames']==12*3600*16000
        assert not saved['finalize_requested']
        with pytest.raises(AdmissionBoundary):capture.run(**args,finalize=True)
        assert len(routes.puts)==3
        before=list(routes.puts)
        with pytest.raises(AdmissionBoundary):capture.run(**args,finalize=True)
        assert routes.puts==before
        assert not app.state.worker.thread.is_alive()


def test_exact_outstanding_admission_resume(tmp_path,monkeypatch):
    paths,args=options(tmp_path);original=capture.CaptureUploader.run
    class Interrupted(BaseException):pass
    with handler_app(tmp_path/'server') as (client,app):
        args.update(session_id=session(client),transport=StrictRoutes(client))
        capture.run(**args,delivery=paths[0],start=True)
        def stop(self):raise Interrupted()
        monkeypatch.setattr(capture.CaptureUploader,'run',stop)
        with pytest.raises(Interrupted):capture.run(**args,delivery=paths[1])
        assert (args['job_dir']/'admissions/1/admission.json').is_file()
        with pytest.raises(capture.Refused,match='pending_delivery'):
            capture.run(**args,delivery=paths[2])
        with pytest.raises(capture.Refused,match='stop_required'):
            capture.run(**args,finalize=True)
        monkeypatch.setattr(capture.CaptureUploader,'run',original)
        result=capture.run(**args,delivery=paths[1])
        assert result['inputs']==2 and result['frames']==8 and not result['capture_finalized']


def test_active_piece_rebuilt_from_exact_snapshot(tmp_path,monkeypatch):
    paths,args=options(tmp_path);original=capture.CaptureUploader._new_piece
    class Interrupted(BaseException):pass
    armed=True
    def stop(self,row):
        nonlocal armed
        result=original(self,row)
        if armed and self.input_id.endswith('-1.ready') and result is not None:
            armed=False;raise Interrupted()
        return result
    with handler_app(tmp_path/'server') as (client,app):
        args.update(session_id=session(client),transport=StrictRoutes(client))
        capture.run(**args,delivery=paths[0],start=True)
        monkeypatch.setattr(capture.CaptureUploader,'_new_piece',stop)
        with pytest.raises(Interrupted):capture.run(**args,delivery=paths[1])
        state=args['job_dir']/'uploader';saved=json.loads((state/'uploader.json').read_text())
        piece=saved['active_page']['pieces'][-1];(state/piece['name']).unlink()
        with pytest.raises(capture.Refused,match='pending_delivery'):
            capture.run(**args,delivery=paths[2])
        result=capture.run(**args,delivery=paths[1])
        assert result['inputs']==2 and result['frames']==8


@pytest.mark.parametrize('kind,reason',[('missing','next_sequence'),('epoch','epoch_boundary_unmapped'),
    ('gap','declared_frame_gap'),('seam','previous_seam_not_trusted'),('other','producer_identity')])
def test_invalid_next_delivery_does_not_poison_existing_job(tmp_path,kind,reason):
    paths,args=options(tmp_path)
    other=tmp_path/'invalid'
    changes=dict(close='STOP');seq=1
    if kind=='missing':seq=3
    if kind=='epoch':changes.update(epoch=1,start=0,run='new',boundary='RESTART',seam=False)
    if kind=='gap':changes.update(start=5)
    if kind=='seam':changes.update(seam=False)
    if kind=='other':changes.update(capture='another-capture')
    segment(other,seq,**changes)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        capture.run(**args,delivery=paths[0],start=True)
        with pytest.raises(capture.Refused) as error:capture.run(**args,delivery=other)
        assert reason in str(error.value) or reason in getattr(error.value,'reasons',[])
        assert sorted(p.name for p in (args['job_dir']/'admissions').iterdir())==['0']
        assert all(m=='GET' for m,_,_ in routes.calls)
        assert capture.run(**args,delivery=paths[1])['frames']==8


def test_actual_uploader_lock_blocks_registry_inspection(tmp_path):
    paths,args=options(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        capture.run(**args,delivery=paths[0],start=True)
        with (args['job_dir']/'uploader/uploader.lock').open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);before=len(routes.calls)
            with pytest.raises(BlockingIOError):capture.run(**args,delivery=paths[1])
            assert len(routes.calls)==before


def test_stop_required_and_missing_journal_never_adopted(tmp_path):
    paths,args=options(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        args.update(session_id=session(client),transport=StrictRoutes(client))
        capture.run(**args,delivery=paths[0],start=True)
        with pytest.raises(capture.Refused,match='stop_required'):capture.run(**args,finalize=True)
        state=args['job_dir']/'uploader/uploader.json';state.rename(state.with_suffix('.lost'))
        with pytest.raises(FileNotFoundError):capture.run(**args,delivery=paths[0])


def test_metadata_only_capacity_beyond_two_hours():
    # Arithmetic/profile boundary only: no three-hour media or runtime claim.
    header=dict(max_hours=12)
    report=dict(frames=16000,wav_bytes=32044)
    state=dict(emitted_frames=3*3600*16000,input_bytes=3*3600*32000,active_page=None)
    capture.check_capacity(state,report,header)
    with pytest.raises(capture.Refused,match='remaining_capacity'):
        capture.check_capacity(state,report,dict(max_hours=2))


def test_many_tiny_segments_refuse_without_implicit_finalize(tmp_path):
    paths=[]
    for index in range(15):
        p=tmp_path/('tiny'+str(index));segment(p,index,close='STOP' if index==14 else 'ROTATION');paths.append(p)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client)
        args=dict(job_dir=tmp_path/'job',api='http://127.0.0.1:8765',session_id=session(client),
            capture_id='tiny',exclusive_writer=True,transport=routes,
            limits=u.Limits(disk_floor=0,deadline_seconds=30,poll_seconds=.001))
        for index in range(14):capture.run(**args,delivery=paths[index],start=index==0)
        with pytest.raises(capture.Refused,match='source_slots_exhausted_no_automatic_eos'):
            capture.run(**args,delivery=paths[14])
        assert len(list((args['job_dir']/'admissions').iterdir()))==14
        saved=json.loads((args['job_dir']/'uploader/uploader.json').read_text())
        assert not saved['finalize_requested'] and all(m=='GET' for m,_,_ in routes.calls)


def long_segment(root, seq, frames, start, close):
    import hashlib,struct
    from test_recorder_metadata import metadata,utf,i
    root.mkdir();pcm=b'\0\0'*frames
    wav=struct.pack('<4sI8sIHHIIHH4sI',b'RIFF',36+len(pcm),b'WAVEfmt ',16,1,1,16000,32000,2,16,b'data',len(pcm))+pcm
    wh=hashlib.sha256(wav).hexdigest()
    local=metadata(sequence=seq,start=start,frames=frames,target=u.PAGE_FRAMES,close=close,seam=seq>0,
                   pcm=hashlib.sha256(pcm).hexdigest(),wav=wh)
    source='segment-'+hashlib.sha256('synthetic-中文-🎵'.encode()).hexdigest()+'-'+str(seq)+'.ready'
    envelope=b'RFT1'+utf('revision')+utf(source)+i(len(local))+local
    binding=hashlib.sha256((source+'\n'+wh+'\nrevision').encode()).hexdigest()
    marker='\n'.join(('RECORDER-DELIVERY-V1','r-'+binding+'-'+'0'*32,'revision',source,str(len(wav)),wh,
                      str(len(envelope)),hashlib.sha256(envelope).hexdigest(),''))
    (root/'audio.wav').write_bytes(wav);(root/'segment.v1.bin').write_bytes(envelope);(root/'delivery.v1.txt').write_text(marker)


def test_full_real_geometry_page_requests_non_eos_seal(tmp_path):
    # 294 seconds of synthetic PCM at real geometry; NO native processing.
    first=tmp_path/'first';second=tmp_path/'second'
    long_segment(first,0,u.PAGE_FRAMES,0,'ROTATION')
    long_segment(second,1,16000,u.PAGE_FRAMES,'STOP')
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client)
        args=dict(job_dir=tmp_path/'job',api='http://127.0.0.1:8765',session_id=session(client),
            capture_id='non-eos-page',exclusive_writer=True,transport=routes,
            limits=u.Limits(disk_floor=0,deadline_seconds=60,poll_seconds=.001))
        with pytest.raises(AdmissionBoundary):capture.run(**args,delivery=first,start=True)
        saved=json.loads((args['job_dir']/'uploader/uploader.json').read_text())
        assert saved['active_page']['seal']['eos'] is False
        assert saved['active_page']['seal']['end_frame']==u.PAGE_FRAMES
        assert not saved['finalize_requested'] and saved['active_input']['frames']==u.PAGE_FRAMES
        assert len(routes.puts)==3 and not app.state.worker.thread.is_alive()
