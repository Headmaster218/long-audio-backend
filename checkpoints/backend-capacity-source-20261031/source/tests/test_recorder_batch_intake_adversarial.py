"""Independent batch review. Uses local ASGI or isolated method seams only."""
import hashlib
import shutil
import struct
from pathlib import Path
import pytest
from audio_backend import recorder_batch_intake as batch
from audio_backend import recording_uploader as u
from audio_backend.recorder_snapshot import create_snapshot
from test_recorder_batch_intake import fixture
from test_recorder_metadata import metadata, utf, i
from test_recording_session_contract import handler_app, session, HEADERS
from test_strict_uploader_admission import StrictRoutes, AdmissionBoundary


def test_other_empty_capture_refused_before_post(tmp_path):
    args = fixture(tmp_path)
    with handler_app(tmp_path/'server') as (client, app):
        sid = session(client)
        reply = client.post(f'/sessions/{sid}/recordings/foreign-empty/pages', json={'page_number':1,'capture_origin_frame':0}, headers=HEADERS)
        assert reply.status_code == 202
        routes = StrictRoutes(client)
        args.update(session_id=sid, transport=routes)
        with pytest.raises((batch.Refused, u.Incomplete)):
            batch.run(**args)
        assert all(method == 'GET' for method, _, _ in routes.calls), routes.calls


def alternate_delivery(path):
    path.mkdir()
    pcm = b'\x01\x02'*4
    wav = struct.pack('<4sI8sIHHIIHH4sI',b'RIFF',36+len(pcm),b'WAVEfmt ',16,1,1,16000,32000,2,16,b'data',len(pcm))+pcm
    wh = hashlib.sha256(wav).hexdigest()
    local = metadata(sequence=0,start=0,frames=4,target=4,close='ROTATION',seam=False,pcm=hashlib.sha256(pcm).hexdigest(),wav=wh)
    source = 'segment-'+hashlib.sha256('synthetic-中文-🎵'.encode()).hexdigest()+'-0.ready'
    env = b'RFT1'+utf('rev')+utf(source)+i(len(local))+local
    binding = hashlib.sha256((source+'\n'+wh+'\nrev').encode()).hexdigest()
    marker = '\n'.join(('RECORDER-DELIVERY-V1','r-'+binding+'-'+'0'*32,'rev',source,str(len(wav)),wh,str(len(env)),hashlib.sha256(env).hexdigest(),''))
    (path/'audio.wav').write_bytes(wav)
    (path/'segment.v1.bin').write_bytes(env)
    (path/'delivery.v1.txt').write_text(marker)


def test_final_same_geometry_snapshot_replacement_refused(tmp_path, monkeypatch):
    args = fixture(tmp_path)
    with handler_app(tmp_path/'server') as (client, app):
        sid = session(client)
        args.update(session_id=sid,transport=StrictRoutes(client))
        # Isolate final integrity check from worker/native completion, not an upload qualification.
        def accepted(self):
            if self.finalize:
                return {'capture_finalized':True,'frames':12,'pages':1,'sources':3}
            return {'input_complete':True}
        def replaced(self):
            alt = tmp_path/'alternative'
            alternate_delivery(alt)
            dest = args['job_dir']/'snapshots'/'0'
            shutil.rmtree(dest)
            create_snapshot(alt,dest)
        monkeypatch.setattr(batch.BatchUploader,'run',accepted)
        monkeypatch.setattr(batch.BatchUploader,'_reconcile_capture',replaced)
        with pytest.raises(batch.Refused, match='snapshot_changed|snapshot_identity_changed'):
            batch.run(**args)


def test_eight_input_real_admission(tmp_path):
    args = fixture(tmp_path,8)
    with handler_app(tmp_path/'server') as (client, app):
        routes = StrictRoutes(client)
        args.update(session_id=session(client), transport=routes)
        with pytest.raises(AdmissionBoundary):
            batch.run(**args)
        assert len(routes.puts)==8
        assert (args['job_dir']/'manifest.json').stat().st_size < batch.single.MAX_MANIFEST


@pytest.mark.parametrize('change',['premature_finalize','target','max_inputs'])
def test_registered_prefix_freeze_refusals(tmp_path,monkeypatch,change):
    import json
    args=fixture(tmp_path)
    original=batch.BatchUploader.run
    class Paused(BaseException):pass
    armed=True
    def stop_after_first(self):
        nonlocal armed
        result=original(self)
        if armed and self.input_id and self.input_id.endswith('-0.ready'):
            armed=False
            raise Paused()
        return result
    monkeypatch.setattr(batch.BatchUploader,'run',stop_after_first)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        with pytest.raises(Paused):batch.run(**args)
        path=args['job_dir']/'uploader/uploader.json';d=json.loads(path.read_text())
        assert len(d['inputs'])==1 and d['inputs'][0]['clean_eof']
        if change=='premature_finalize':d['finalize_requested']=True
        if change=='target':d['identity']['api']='https://other.example.org'
        if change=='max_inputs':d['max_inputs']=4
        path.write_bytes(u._json(d));before=len(routes.calls)
        with pytest.raises((batch.Refused,u.Incomplete,u.SourceChanged)):
            batch.run(**args,resume=True)
        assert all(method=='GET' for method,_,_ in routes.calls[before:])
