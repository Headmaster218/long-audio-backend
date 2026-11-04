"""Actual tiny WAV admissions, real uploader journals, no inference claim."""
import pytest
from pathlib import Path
from test_recorder_inbox_runner import example, invoke, Peer, runner
from audio_backend import recorder_runner_capacity as capacity
from audio_backend import recorder_capture_intake as engine
from audio_backend import recording_uploader as u


def snapshot(*roots):
    return {str(p):p.read_bytes() for root in roots for p in root.rglob('*') if p.is_file()}


def test_source_slots_stop_before_new_selection_and_preserve_tail(tmp_path):
    paths,config,root=example(tmp_path,20,terminal='ROTATION')
    runner.configure(state_dir=root,config=config,first_delivery=paths[0]); peer=Peer()
    result=invoke(root,peer,max_actions=20)
    assert result['status']=='capacity_reached'
    assert result['capacity']['reason']=='pending_page_source_slots'
    assert result['inputs']==14
    assert result['capacity']['staged_frames']==56 and not peer.eos
    assert not (root/'input-014.intent.json').exists()
    before=snapshot(root,Path(config['job_dir']),Path(config['inbox_dir']))
    assert runner.preflight(state_dir=root)['capacity']==result['capacity']
    assert invoke(root,peer)['capacity']==result['capacity']
    assert before==snapshot(root,Path(config['job_dir']),Path(config['inbox_dir']))


def test_count_exhaustion_is_local_report(tmp_path):
    paths,config,root=example(tmp_path,2,terminal='ROTATION'); config['max_inputs']=1
    runner.configure(state_dir=root,config=config,first_delivery=paths[0]); invoke(root,Peer())
    before=snapshot(root,Path(config['job_dir']))
    result=runner.preflight(state_dir=root)
    assert result['capacity']['reason']=='input_count' and result['network_requests']==0
    assert before==snapshot(root,Path(config['job_dir']))


def test_pending_selection_never_cleared_by_preflight(tmp_path):
    paths,config,root=example(tmp_path)
    runner.configure(state_dir=root,config=config,first_delivery=paths[0]);before=snapshot(root)
    assert runner.preflight(state_dir=root)['status']=='pending_selection_preserved'
    assert before==snapshot(root)


@pytest.mark.parametrize('field,value,reason', [('frames',3600*16000+1,'duration'),('wav_bytes',engine.BYTES+1,'input_bytes')])
def test_deterministic_first_input_caps(field,value,reason):
    report=dict(frames=4,wav_bytes=52);report[field]=value
    result=capacity.inspect(dict(max_inputs=192,max_hours=1),[],0,report,u.Guard(u.Limits()))
    assert result['reason']==reason and not result['allowed']


def test_available_preflight_does_not_select(tmp_path):
    paths,config,root=example(tmp_path,2)
    runner.configure(state_dir=root,config=config,first_delivery=paths[0]);invoke(root,Peer())
    before=snapshot(root,Path(config['job_dir']))
    assert runner.preflight(state_dir=root)['status']=='capacity_available'
    assert before==snapshot(root,Path(config['job_dir']))


def test_changed_checkpoint_and_busy_intake_fail_closed(tmp_path):
    import json
    import os
    paths,config,root=example(tmp_path,2)
    runner.configure(state_dir=root,config=config,first_delivery=paths[0]);invoke(root,Peer())
    job=Path(config['job_dir']);fd=engine.held_lock(job/'intake.lock')
    try:
        with pytest.raises(BlockingIOError):runner.preflight(state_dir=root)
    finally:os.close(fd)
    path=job/'uploader/uploader.json';data=json.loads(path.read_bytes());data['input_bytes']+=1
    path.write_bytes(u._json(data))
    with pytest.raises(u.Incomplete):runner.preflight(state_dir=root)
    assert not (root/'input-001.intent.json').exists()


@pytest.mark.parametrize('stage,expected', [
    ('stop','awaiting_finalize'),('intent','finalize_pending'),
    ('finalized','finalized_locally_recorded'),('exported','exported_locally_verified')])
def test_terminal_preflight_precedence_is_read_only(tmp_path,monkeypatch,stage,expected):
    from test_recorder_inbox_runner import fake_export
    paths,config,root=example(tmp_path,1)
    config['max_inputs']=1  # Terminal status takes precedence over full capacity.
    runner.configure(state_dir=root,config=config,first_delivery=paths[0]);peer=Peer()
    invoke(root,peer)
    if stage=='intent':
        class Crash(BaseException):pass
        monkeypatch.setattr(runner.intake,'run',lambda **kw:(_ for _ in ()).throw(Crash()))
        with pytest.raises(Crash):invoke(root,peer,finalize=True)
    if stage in ('finalized','exported'):
        invoke(root,peer,finalize=True)
    if stage=='exported':
        monkeypatch.setattr(runner.status,'run',lambda **kw:dict(status='completed',analysis_completion_verified=True))
        monkeypatch.setattr(runner.exporter,'run',fake_export)
        invoke(root,peer,export=True)
    monkeypatch.setattr(u.HTTPTransport,'request',lambda *a,**kw:pytest.fail('preflight HTTP'))
    monkeypatch.setattr(runner.status,'run',lambda **kw:pytest.fail('preflight live status'))
    before=snapshot(tmp_path)
    result=runner.preflight(state_dir=root)
    assert result['status']==expected and result['network_requests']==0
    assert result['remote_completion_checked'] is False and result['analysis_completion_verified'] is False
    assert before==snapshot(tmp_path)
    if stage=='exported':
        (Path(config['output_dir'])/'transcript.txt').write_bytes(b'changed')
        with pytest.raises(runner.Refused,match='artifact'):runner.preflight(state_dir=root)
    if stage=='finalized':
        (root/'finalize.done.json').write_bytes(b'{}\n')
        with pytest.raises(runner.Refused,match='receipt_binding'):runner.preflight(state_dir=root)


@pytest.mark.parametrize('field,value',[('frames',12*3600*16000+1),('wav_bytes',engine.BYTES+1)])
def test_configure_oversize_first_never_creates_state(tmp_path,monkeypatch,field,value):
    paths,config,root=example(tmp_path,1)
    real=runner.inspect_delivery(paths[0],validate_metadata=True)
    record=engine.candidate_record(paths[0],real,0,lambda:120)
    oversized=dict(real);oversized[field]=value
    monkeypatch.setattr(runner,'inspect_delivery',lambda *a,**kw:oversized)
    monkeypatch.setattr(engine,'candidate_record',lambda *a,**kw:record)
    monkeypatch.setattr(u.HTTPTransport,'request',lambda *a,**kw:pytest.fail('HTTP'))
    before=snapshot(tmp_path)
    with pytest.raises(runner.Refused,match='^first_delivery_capacity$'):
        runner.configure(state_dir=root,config=config,first_delivery=paths[0])
    assert not root.exists() and not Path(config['job_dir']).exists()
    assert before==snapshot(tmp_path)


@pytest.mark.parametrize('field,value',[('frames',12*3600*16000+1),('wav_bytes',engine.BYTES+1)])
def test_initialize_oversize_first_never_creates_session_or_state(tmp_path,monkeypatch,field,value):
    from test_recorder_runner_initialize import options,CreationPeer,init
    args,_,_,root=options(tmp_path);peer=CreationPeer()
    real=init.inspect_delivery(Path(args['first_delivery']),validate_metadata=True)
    oversized=dict(real);oversized[field]=value
    monkeypatch.setattr(init,'inspect_delivery',lambda *a,**kw:oversized)
    before=snapshot(tmp_path)
    with pytest.raises(runner.Refused,match='^first_delivery_capacity$'):
        init.initialize(**args,transport=peer)
    assert peer.calls==[] and not root.exists() and not Path(args['job_dir']).exists()
    assert before==snapshot(tmp_path)
