"""144 physical tiny inputs and real journals; scaled protocol peer, NOT long runtime.

The fake peer releases every synthetic page immediately. It does not run strict
server/native processing. Existing strict admission tests remain separate.
"""
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import wave

import pytest
from audio_backend import recorder_capture_intake as c
from audio_backend import recording_uploader as u
from test_recorder_sequence import fixture as segment


class Peer:
    def __init__(self):
        self.pages={};self.eos=False;self.puts=[];self.lose=True;self.seals={};self.finish=None
    def status(self):
        released=[p for p in self.pages.values() if p['released']]
        pending=[p for p in self.pages.values() if not p['released']]
        return dict(capture_id='extended',capture_origin_frame=0,mode='sealed_pages',state='active',
            policy='sealed-recording-exact-overlap-v1',format="wav-pcm16le-16000-mono",page_count=len(self.pages),
            released_pages=len(released),capture_end_frame=max((p['end_frame'] for p in released),default=0),
            pending_page=pending[0] if pending else None,eos=self.eos,revision=len(self.pages))
    def request(self,method,path,body=None,file=None,key=None):
        parts=urlsplit(path);route=parts.path;base='/sessions/known/recordings/extended'
        if route.endswith('/recording-contract'):
            assert method=='GET'
            return dict(schema='recording-session-contract-v1',session_id='known',processing_mode='legacy',
                strict_primary_enabled=True,strict_primary_policy='strict-primary-payload-v2',source_format="wav-pcm16le-16000-mono",
                sample_rate_hz=16000,channels=1,sample_width_bytes=2,max_page_work_chunks=28,work_chunk_frames=168000,
                max_source_frames=u.SOURCE_FRAMES,max_source_bytes=44+2*u.SOURCE_FRAMES,
                source_idempotency=dict(scope='session_capture',bound_fields=['page','wav_sha256','source_start_frame','format'],changed_body='conflict'),
                capture=dict(capture_id='extended',capture_origin_frame=0) if self.pages else None,has_chunks=bool(self.pages))
        if route=='/sessions/known':return {'processing_mode':'legacy'}
        if route=='/sessions/known/chunks':return {'chunks':[],'next_cursor':0}
        if route==base:
            if not self.pages:raise u.HTTPFailure(404)
            return copy.deepcopy(self.status())
        if route==base+'/finish':
            assert method=='POST'
            if self.finish is not None:assert self.finish==body
            self.finish=dict(body);self.eos=True;return copy.deepcopy(self.status())
        if route==base+'/pages':
            assert method=='POST';n=body['page_number'];assert n not in self.pages
            self.pages[n]=dict(page_number=n,capture_id='extended',capture_origin_frame=0,id='p'+str(n),
                policy='sealed-recording-exact-overlap-v1',state='open',released=False,sources=[],revision=0,end_frame=None,eos=False)
            return copy.deepcopy(self.pages[n])
        n=int(route.split('/pages/')[1].split('/')[0]);page=self.pages.get(n)
        if page is None:raise u.HTTPFailure(404)
        if method=='PUT':
            assert route.endswith('/sources') and key not in self.puts
            self.puts.append(key)
            with wave.open(str(file),'rb') as stream:frames=stream.getnframes()
            page['sources'].append(dict(id=key,idempotency_key=key,format="wav-pcm16le-16000-mono",frames=frames,
                bytes=file.stat().st_size,digest=hashlib.sha256(file.read_bytes()).hexdigest(),
                source_start_frame=int(parse_qs(parts.query)['source_start_frame'][0])))
            page['revision']+=1
            if n==65 and self.lose:self.lose=False;raise u.Uncertain('Committed synthetic PUT response lost')
        elif method=='POST':
            assert route.endswith('/seal')
            if n in self.seals:assert self.seals[n]==body
            self.seals[n]=dict(body);page.update(state='published',released=True,end_frame=body['end_frame'],eos=body['eos'])
            self.eos |= body['eos']
        else:assert method=='GET'
        return copy.deepcopy(page)


def test_144_inputs_reopen_replay_and_explicit_stop(tmp_path,monkeypatch):
    # Four-frame original files; two inputs per eight-frame synthetic page.
    monkeypatch.setattr(u,'PAGE_FRAMES',8);monkeypatch.setattr(u,'SOURCE_FRAMES',4)
    peer=Peer();args=dict(job_dir=tmp_path/'job',api='http://127.0.0.1:8765',session_id='known',
        capture_id='extended',exclusive_writer=True,transport=peer,
        limits=u.Limits(disk_floor=0,deadline_seconds=180,poll_seconds=.001))
    class Crash(BaseException):pass
    for index in range(144):
        path=tmp_path/('input'+str(index));segment(path,index,close='STOP' if index==143 else 'ROTATION')
        if index in (128,143):
            with monkeypatch.context() as patch:
                patch.setattr(c.CaptureUploader,'run',lambda self:(_ for _ in ()).throw(Crash()))
                with pytest.raises(Crash):c.run(**args,delivery=path)
            # Exact outstanding-admission retry reconstructs fresh uploader objects.
        result=c.run(**args,delivery=path,start=index==0)
        assert result['inputs']==index+1 and result['frames']==(index+1)*4
        assert result['max_inputs']==192 and not result['capture_finalized']
        if index in (63,127,128,143):
            puts=list(peer.puts);repeat=c.run(**args,delivery=path)
            assert repeat['inputs']==index+1 and repeat['frames']==result['frames'] and peer.puts==puts
    assert not peer.eos
    result=c.run(**args,finalize=True)
    assert result['capture_finalized'] and result['published_frames']==576 and result['inputs']==144
    assert len(peer.pages)==72 and len(peer.puts)==144 and len(set(peer.puts))==144
    assert all(not p['eos'] for p in peer.pages.values()) and peer.eos and peer.finish is not None
    puts=list(peer.puts);assert c.run(**args,finalize=True)==result;assert peer.puts==puts
    saved=json.loads((args['job_dir']/'uploader/uploader.json').read_text())
    assert saved['caps']['max_inputs']==192 and saved['inputs_done']==144 and saved['active_input'] is None
    assert not saved['spool'] and len(list((args['job_dir']/'admissions').iterdir()))==144


@pytest.mark.parametrize('capacity',[64,128])
def test_existing_intake_caps_are_not_migrated(tmp_path,capacity):
    first=tmp_path/'first';second=tmp_path/'second';segment(first,0);segment(second,1,close='STOP')
    peer=Peer();args=dict(job_dir=tmp_path/'job',api='http://127.0.0.1:8765',session_id='known',
        capture_id='extended',exclusive_writer=True,transport=peer,limits=u.Limits(disk_floor=0,deadline_seconds=30))
    c.run(**args,delivery=first,start=True,max_inputs=capacity)
    header=(args['job_dir']/'capture.json').read_bytes();intent=(args['job_dir']/'initial-intent.json').read_bytes()
    assert c.run(**args,delivery=second)['max_inputs']==capacity
    before=(args['job_dir']/'uploader/uploader.json').read_bytes()
    with pytest.raises(c.Refused,match='frozen_capacity_changed'):
        c.run(**args,delivery=second,max_inputs=192)
    assert before==(args['job_dir']/'uploader/uploader.json').read_bytes()
    assert header==(args['job_dir']/'capture.json').read_bytes() and intent==(args['job_dir']/'initial-intent.json').read_bytes()
