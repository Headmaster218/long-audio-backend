"""Independent exact-boundary compatibility review; original sources read-only.
Strict ASGI admission boundary only, no native execution or physical continuity claim.
"""
import os
import hashlib
import json
import shutil
import struct
from pathlib import Path
import pytest
from audio_backend import recorder_delivery as d, recorder_snapshot as s, recorder_sequence as seq
from audio_backend import recorder_batch_intake as batch, recorder_capture_intake as capture
from audio_backend import recording_uploader as u, recorder_metadata as m
from test_recording_session_contract import handler_app,session
from test_strict_uploader_admission import StrictRoutes,AdmissionBoundary

_fixture = os.environ.get('RECORDER_EXACT_STOP_FIXTURE')
pytestmark = pytest.mark.skipif(not _fixture, reason='Explicit local Java producer output not supplied')
ROOT = Path(_fixture) if _fixture else None

def inventory():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob('*') if p.is_file()}

@pytest.fixture(autouse=True)
def unchanged_originals():
    before=inventory()
    yield
    assert inventory()==before

def inputs():
    paths=list(ROOT.glob('r-*'))
    assert len(paths)==3
    return sorted(paths,key=lambda p:d.inspect_delivery(p,validate_metadata=True)['sequence'])

def opts(tmp_path):
    return dict(job_dir=tmp_path/'job',api='http://127.0.0.1:8765',capture_id='exact-boundary-review',
        exclusive_writer=True,limits=u.Limits(disk_floor=0,deadline_seconds=30,poll_seconds=.001))

def rotation_only(tmp_path):
    """Make a rechecksummed historical-shape negative fixture, never mutate originals."""
    paths=[]
    for index,path in enumerate(inputs()):
        target=tmp_path/f'rotation{index}'
        shutil.copytree(path,target)
        paths.append(target)
    last=paths[-1]
    marker=d.parse_marker((last/'delivery.v1.txt').read_bytes())
    data=(last/'segment.v1.bin').read_bytes()
    local=d.parse_envelope(data,marker,return_local=True)
    body=local[:-32]
    assert body.count(b'\x00\x04STOP')==1
    body=body.replace(b'\x00\x04STOP',b'\x00\x08ROTATION')
    local=body+hashlib.sha256(body).digest()
    local_start=len(data)-len(d.parse_envelope(data,marker,return_local=True))
    data=data[:local_start-4]+struct.pack('>i',len(local))+local
    (last/'segment.v1.bin').write_bytes(data)
    lines=(last/'delivery.v1.txt').read_text().split('\n')
    lines[6]=str(len(data));lines[7]=hashlib.sha256(data).hexdigest()
    (last/'delivery.v1.txt').write_text('\n'.join(lines))
    assert [d.inspect_delivery(p,validate_metadata=True)['close_reason'] for p in paths]==['ROTATION']*3
    return paths

def test_actual_producer_metadata_and_sequence(tmp_path):
    paths=inputs();reports=[d.inspect_delivery(p,validate_metadata=True) for p in paths]
    assert [r['sequence'] for r in reports]==[0,1,2]
    assert [r['frames'] for r in reports]==[64000]*3
    assert [r['frame_start'] for r in reports]==[0,64000,128000]
    assert [m.decode(d.parse_envelope((p/'segment.v1.bin').read_bytes(),r,return_local=True))['target_frames'] for p,r in zip(paths,reports)]==[64000]*3
    assert [r['close_reason'] for r in reports]==['ROTATION','ROTATION','STOP']
    snapshots=[tmp_path/str(i) for i in range(3)]
    for p,t in zip(paths,snapshots):s.create_snapshot(p,t)
    result=seq.reconcile_snapshots(snapshots)
    assert result['observations_only'] and not result['physical_continuity_proven']
    assert all(e['relation']=='declared_adjacent' and not e['physical_continuity_proven'] and e['hardware_frame_mapping']=='UNESTABLISHED' for e in result['captures'][0]['edges'])
    batch.check_sequence(snapshots,reports)

@pytest.mark.parametrize('lose_put',[False,True])
def test_actual_full_stop_batch_admission(tmp_path,lose_put):
    args=opts(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client,lose_put);args.update(session_id=session(client),transport=routes)
        with pytest.raises(AdmissionBoundary):batch.run(deliveries=inputs(),**args)
        saved=json.loads((args['job_dir']/'uploader/uploader.json').read_text())
        assert [e['frames'] for e in saved['inputs']]==[64000]*3
        assert [e['normalized_start'] for e in saved['inputs']]==[0,64000,128000]
        assert saved['finalize_requested'] and saved['pages'][0]['seal']['eos'] is True
        assert saved['pages'][0]['seal']['end_frame']==192000
        assert len(routes.puts)==3
        before=list(routes.puts)
        with pytest.raises(AdmissionBoundary):batch.run(deliveries=inputs(),resume=True,**args)
        assert routes.puts==before
        assert not app.state.worker.thread.is_alive()

@pytest.mark.parametrize('lose_put',[False,True])
def test_actual_full_stop_incremental_requires_finalize(tmp_path,lose_put):
    args=opts(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client,lose_put);args.update(session_id=session(client),transport=routes)
        for i,p in enumerate(inputs()):
            result=capture.run(delivery=p,start=i==0,**args)
            assert result['status']=='delivery_staged_without_eos'
            assert result['frames']==result['staged_frames']==64000*(i+1)
            assert result['published_frames']==0 and not result['capture_finalized']
            assert not result['physical_continuity_proven'] and result['hardware_frame_mapping']=='UNESTABLISHED'
        assert all(m=='GET' for m,_,_ in routes.calls)
        saved=json.loads((args['job_dir']/'uploader/uploader.json').read_text())
        assert not saved['finalize_requested']
        with pytest.raises(AdmissionBoundary):capture.run(finalize=True,**args)
        saved=json.loads((args['job_dir']/'uploader/uploader.json').read_text())
        assert saved['finalize_requested'] and saved['active_page']['seal']['eos'] is True
        assert saved['active_page']['seal']['end_frame']==192000
        assert len(routes.puts)==3
        before=list(routes.puts)
        with pytest.raises(AdmissionBoundary):capture.run(finalize=True,**args)
        assert routes.puts==before
        assert not app.state.worker.thread.is_alive()

def test_rotation_only_batch_refused_before_http(tmp_path):
    paths=rotation_only(tmp_path)
    class Forbidden:
        def request(self,*args,**kwargs):raise AssertionError('No HTTP allowed for rotation-only batch')
    with pytest.raises(batch.Refused,match='batch_requires_final_stop'):
        batch.run(deliveries=paths,session_id='s',transport=Forbidden(),**opts(tmp_path))
    assert not (tmp_path/'job/manifest.json').exists()

def test_rotation_only_incremental_cannot_finalize(tmp_path):
    args=opts(tmp_path);paths=rotation_only(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=StrictRoutes(client);args.update(session_id=session(client),transport=routes)
        for i,p in enumerate(paths):
            result=capture.run(delivery=p,start=i==0,**args)
            assert not result['capture_finalized'] and not result['physical_continuity_proven']
        with pytest.raises(capture.Refused,match='capture_stop_required_before_finalize'):
            capture.run(finalize=True,**args)
        saved=json.loads((args['job_dir']/'uploader/uploader.json').read_text())
        assert not saved['finalize_requested'] and routes.puts==[]
        assert all(m=='GET' for m,_,_ in routes.calls)
        assert not app.state.worker.thread.is_alive()
