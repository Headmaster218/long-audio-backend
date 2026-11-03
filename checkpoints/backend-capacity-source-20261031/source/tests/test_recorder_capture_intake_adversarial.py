"""Independent capture review. Synthetic metadata history is not physical long audio."""
import json
from pathlib import Path
import pytest
from audio_backend import recorder_capture_intake as c
from audio_backend import recording_uploader as u
from audio_backend import recording_uploader_history as h
from test_recorder_capture_intake import options
from test_recorder_sequence import fixture as segment
from test_recording_session_contract import handler_app,session,HEADERS
from test_strict_uploader_admission import StrictRoutes,AdmissionBoundary
from test_recording_uploader import metadata_input,metadata_piece,metadata_release


def test_retry_transport_nonce_changed_same_audio_refuses(tmp_path):
    paths,args=options(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        c.run(**args,delivery=paths[0],start=True)
        path=paths[0]/'delivery.v1.txt';lines=path.read_text().splitlines()
        lines[1]=lines[1][:-32]+'1'*32;path.write_text('\n'.join(lines)+'\n')
        before=len(routes.calls)
        with pytest.raises(c.Refused,match='original_binding_changed'):
            c.run(**args,delivery=paths[0])
        assert len(routes.calls)==before


def test_stop_fences_next_without_eos(tmp_path):
    first=tmp_path/'first';second=tmp_path/'second'
    segment(first,0,close='STOP');segment(second,1,close='STOP')
    args=dict(job_dir=tmp_path/'job',api='http://127.0.0.1:8765',capture_id='c',exclusive_writer=True,
              limits=u.Limits(disk_floor=0,deadline_seconds=30,poll_seconds=.001))
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        c.run(**args,delivery=first,start=True)
        with pytest.raises(c.BoundaryRefused) as error:c.run(**args,delivery=second)
        assert 'previous_segment_not_rotation' in error.value.reasons
        assert set(p.name for p in (args['job_dir']/'admissions').iterdir())=={'0'}
        assert all(m=='GET' for m,_,_ in routes.calls)


def test_outstanding_stop_cannot_finalize(tmp_path,monkeypatch):
    paths,args=options(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        c.run(**args,delivery=paths[0],start=True);c.run(**args,delivery=paths[1])
        def interrupted(self):raise KeyboardInterrupt()
        monkeypatch.setattr(c.CaptureUploader,'run',interrupted)
        with pytest.raises(KeyboardInterrupt):c.run(**args,delivery=paths[2])
        with pytest.raises(c.Refused,match='pending_delivery_before_finalize'):
            c.run(**args,finalize=True)
        assert all(m=='GET' for m,_,_ in routes.calls)


def test_tiny_source_slots_refuse_before_admission(tmp_path):
    paths=[]
    for index in range(15):
        path=tmp_path/f'input{index}';segment(path,index);paths.append(path)
    args=dict(job_dir=tmp_path/'job',api='http://127.0.0.1:8765',capture_id='c',exclusive_writer=True,
              limits=u.Limits(disk_floor=0,deadline_seconds=30,poll_seconds=.001))
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        for index,path in enumerate(paths[:14]):c.run(**args,delivery=path,start=index==0)
        before=(args['job_dir']/'uploader/uploader.json').read_bytes()
        with pytest.raises(c.Refused,match='source_slots_exhausted_no_automatic_eos'):
            c.run(**args,delivery=paths[14])
        assert not (args['job_dir']/'admissions/14').exists()
        assert (args['job_dir']/'uploader/uploader.json').read_bytes()==before
        assert all(m=='GET' for m,_,_ in routes.calls)


def test_foreign_empty_capture_before_admission(tmp_path):
    paths,args=options(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        sid=session(client)
        assert client.post(f'/sessions/{sid}/recordings/foreign/pages',json={'page_number':1,'capture_origin_frame':0},headers=HEADERS).status_code==202
        routes=StrictRoutes(client);args.update(session_id=sid,transport=routes)
        with pytest.raises(c.Refused,match='existing_session_not_empty'):
            c.run(**args,delivery=paths[0],start=True)
        assert not list((args['job_dir']/'admissions').iterdir())
        assert all(m=='GET' for m,_,_ in routes.calls)


def test_effective_default_guard_is_twelve_hours(tmp_path,monkeypatch):
    paths,args=options(tmp_path);seen=[];real=c.CaptureUploader.run
    def capture(self):
        seen.append((self.limits.output_frames,self.guard.limits.output_frames,self.journal.guard.limits.output_frames))
        return real(self)
    monkeypatch.setattr(c.CaptureUploader,'run',capture)
    with handler_app(tmp_path/'server') as (client,app):
        args.update(session_id=session(client),transport=StrictRoutes(client))
        c.run(**args,delivery=paths[0],start=True)
    assert seen==[(12*3600*u.RATE,)*3]


def test_metadata_only_sixty_admissions_over_two_hours_and_512mib(tmp_path):
    """Actual v3 receipt files, synthetic descriptors only: no audio or HTTP."""
    target=c.single.target('http://127.0.0.1:8765','operator-selected-unverified','metadata-only','synthetic.metadata',0)
    header=dict(schema=c.SCHEMA,target=target,max_hours=12,max_inputs=64,input_bytes=c.BYTES)
    guard=u.Guard(u.Limits(disk_floor=0,output_frames=12*3600*u.RATE,deadline_seconds=180))
    journal=h.PagedJournal(tmp_path/'state',guard,u);journal.lock.acquire()
    journal.initialize(dict(api=target['origin'],session_id=target['session_id'],capture_id=target['capture_id'],origin=0,format=u.FORMAT),64)
    receipts=[]
    try:
        for index in range(60):
            entry=metadata_input(journal,index+1,u.PAGE_FRAMES)
            snapshot=f'/synthetic-not-a-real-original/{index}/snapshot'
            entry['identity']['path']=snapshot+'/audio.wav'
            receipts.append(dict(snapshot_path=snapshot,delivery=dict(source_id=entry['id'],frames=u.PAGE_FRAMES,close_reason='ROTATION')))
            while entry['frames']<u.PAGE_FRAMES:
                metadata_piece(journal,min(u.SOURCE_FRAMES,u.PAGE_FRAMES-entry['frames']))
            entry['clean_eof']=True;journal.save();journal.archive_input();metadata_release(journal)
        probe=object.__new__(c.CaptureUploader);probe.journal=journal
        saved,entries=c.verify_prefix(probe,receipts,header)
        assert saved['emitted_frames']==60*u.PAGE_FRAMES>2*3600*u.RATE
        assert saved['input_bytes']>512*1024**2
        assert len(entries)==60>32 and not saved['finalize_requested'] and not saved['complete']
        c.check_capacity(saved,dict(frames=u.PAGE_FRAMES,wav_bytes=2*u.PAGE_FRAMES+44),header)
        assert not list((tmp_path/'state').glob('*.wav*'))
        assert saved['caps']['output_frames']==12*3600*u.RATE
    finally:journal.lock.release()


def test_fsync_failure_retry_requires_positive_presend_durability_sync(tmp_path,monkeypatch):
    """A prior uncertain receipt write must be positively synced before upload."""
    import errno,os
    paths,args=options(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        c.run(**args,delivery=paths[0],start=True)
        receipt=args['job_dir']/'admissions/1/admission.json'
        actual=os.fsync
        def fail_receipt(fd):
            if os.readlink(f'/proc/self/fd/{fd}')==str(receipt):
                raise OSError(errno.EIO,'injected fsync uncertainty')
            return actual(fd)
        monkeypatch.setattr(os,'fsync',fail_receipt)
        with pytest.raises(OSError):c.run(**args,delivery=paths[1])
        assert receipt.is_file()
        synced=[]
        def observe(fd):
            synced.append(os.readlink(f'/proc/self/fd/{fd}'))
            return actual(fd)
        monkeypatch.setattr(os,'fsync',observe)
        class BeforeUpload(BaseException):pass
        def before_upload(self):
            snapshot=receipt.parent/'snapshot'
            expected=[str(snapshot/name) for name in ('audio.wav','segment.v1.bin','delivery.v1.txt','snapshot.pending.json','snapshot.json')]
            expected += [str(receipt),str(snapshot),str(receipt.parent),str(receipt.parent.parent)]
            assert synced[-len(expected):]==expected
            raise BeforeUpload()
        monkeypatch.setattr(c.CaptureUploader,'run',before_upload)
        with pytest.raises(BeforeUpload):c.run(**args,delivery=paths[1])
        assert all(m=='GET' for m,_,_ in routes.calls)


def test_repeated_receipt_fsync_failure_blocks_upload_again(tmp_path,monkeypatch):
    import errno,os
    paths,args=options(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        c.run(**args,delivery=paths[0],start=True)
        receipt=args['job_dir']/'admissions/1/admission.json'
        actual=os.fsync;failures=[];real_run=c.CaptureUploader.run
        def fail_receipt(fd):
            if os.readlink(f'/proc/self/fd/{fd}')==str(receipt):
                failures.append(True)
                raise OSError(errno.EIO,'injected fsync uncertainty')
            return actual(fd)
        def forbidden(self):raise AssertionError('Upload must not start without positive admission sync')
        monkeypatch.setattr(os,'fsync',fail_receipt)
        monkeypatch.setattr(c.CaptureUploader,'run',forbidden)
        for attempt in range(2):
            with pytest.raises(OSError):c.run(**args,delivery=paths[1])
            assert len(failures)==attempt+1
            assert receipt.is_file()
        assert all(m=='GET' for m,_,_ in routes.calls)
        monkeypatch.setattr(os,'fsync',actual)
        monkeypatch.setattr(c.CaptureUploader,'run',real_run)
        result=c.run(**args,delivery=paths[1])
        assert result['frames']==8 and result['inputs']==2 and not result['capture_finalized']
