"""Finite plan controls: real publication/receipts with synthetic HTTP and export."""
import json
import os
from pathlib import Path
import pytest

from audio_backend import recorder_analysis_plan as plan
from audio_backend import recorder_logical_boundary as boundary
from audio_backend import recording_uploader as u
from test_recorder_ftps_receiver import configured
from test_recorder_receiver_completion_review import example,adapter,write_one
from test_recorder_inbox import delivery
from test_recorder_inbox_runner import runner,fake_export
from test_recorder_runner_initialize import CreationPeer
from test_recorder_generation_transition import NoHTTP,files


class Sessions:
    def __init__(self):
        self.peers={'known':CreationPeer()};self.peers['known'].name='initial'
        self.captures={'known':'extended'};self.calls=[];self.created=0;self.uncertain_at=None
    def request(self,method,path,**kw):
        self.calls.append((method,path))
        if path=='/health':return {'status':'ok'}
        if path=='/sessions':
            self.created+=1;sid='session-'+str(self.created);capture='capture-'+str(self.created)
            self.peers[sid]=CreationPeer();self.captures[sid]=capture
            self.peers[sid].uncertain=self.created==self.uncertain_at
        else:
            sid=path.split('/')[2];capture=self.captures[sid]
        answer=self.peers[sid].request(method,path.replace(sid,'known').replace(capture,'extended'),**kw)
        def convert(v):
            if isinstance(v,dict):return {k:convert(x) for k,x in v.items()}
            if isinstance(v,list):return [convert(x) for x in v]
            return sid if v=='known' else capture if v=='extended' else v
        return convert(answer)


def setup(case,tmp_path,monkeypatch,*,terminal=2,last_close='STOP',seam_error=False):
    generations=[dict(binding=case['config'].binding,inbox_dir=str(case['ready']))]
    roots=[(case['raw'],case['ready'],case['state'])]
    for n in (1,2):
        parent=tmp_path/('generation-'+str(n));parent.mkdir(mode=0o700)
        raw,ready,state,_=example(parent);roots.append((raw,ready,state))
        generations.append(dict(binding=plan.publication.bind(state),inbox_dir=str(ready)))
    source=tmp_path/'source';source.mkdir(mode=0o700)
    deliveries=[]
    for n in range(3):
        close=last_close if n==terminal else 'ROTATION'
        selected=delivery(source,23+n,epoch=7,start=1000+4*n,boundary='RESTART',run='runner-process',seam=n>0 and not(seam_error and n==1),close=close)
        deliveries.append(selected)
        if n<=terminal:
            with adapter(roots[n][2],roots[n][1]) as owner:
                attempt=owner.begin_attempt(selected.name)
                for name in ('audio.wav','segment.v1.bin','delivery.v1.txt'):write_one(owner,attempt,selected,name)
    state=tmp_path/'runner-0'
    config=runner.config_from_delivery(first_delivery=roots[0][1]/deliveries[0].name,
        inbox_dir=roots[0][1],job_dir=tmp_path/'job-0',output_dir=tmp_path/'output-0',
        api='http://127.0.0.1:8765',session_id='known',capture_id='extended',receiver_state_dir=roots[0][2])
    runner.configure(state_dir=state,config=config,first_delivery=roots[0][1]/deliveries[0].name)
    edges=[]
    for n in (1,2):
        route=tmp_path/('route-'+str(n)+'.json')
        route.write_text(json.dumps(dict(case['data'],generations=generations,
            binding=generations[n]['binding'],inbox_dir=generations[n]['inbox_dir'])));route.chmod(0o600)
        edges.append(dict(route_config=str(route),next_state_dir=str(tmp_path/('runner-'+str(n))),
            next_job_dir=str(tmp_path/('job-'+str(n))),next_output_dir=str(tmp_path/('output-'+str(n))),
            next_capture_id='capture-'+str(n),next_session_name='span-'+str(n)))
    data=dict(schema=plan.SCHEMA,state_dir=str(state),edges=edges)
    path=tmp_path/'plan.json';path.write_text(json.dumps(data));path.chmod(0o600)
    control=tmp_path/'control';control.mkdir(mode=0o700)
    monkeypatch.setattr(runner.status,'run',lambda **kw:dict(status='completed',analysis_completion_verified=True))
    def export(output_dir,**kw):
        manifest=fake_export(output_dir=output_dir,**kw)
        n=int(Path(output_dir).name.split('-')[-1]);cutfile=tmp_path/('job-'+str(n))/boundary.NAME
        if cutfile.exists():
            cut=json.loads(cutfile.read_bytes())
            manifest.update(closure_kind='logical_analysis_boundary',logical_boundary_sha256=boundary.digest(cut),
                frames=cut['frames'],recording_complete=False,recognition_continuity_verified=False)
            (Path(output_dir)/'complete.json').write_bytes(runner.single._json(manifest))
        return manifest
    monkeypatch.setattr(runner.exporter,'run',export)
    args=dict(plan_file=path,control_dir=control,acknowledge_finite_plan=True,exclusive_writer=True,
        acknowledge_span_local_closure=True,phase_max_actions=8,phase_max_polls=8,poll_seconds=.1,
        limits=u.Limits(disk_floor=0,poll_seconds=.001))
    return args,Sessions(),data,roots


def test_three_generations_one_call_and_zero_network_repeat(configured,tmp_path,monkeypatch):
    args,peer,data,roots=setup(configured,tmp_path,monkeypatch)
    before={str(root):files(root) for group in roots for root in group[:2]}
    answer=plan.run(**args,transport=peer)
    assert answer['status']=='plan_complete',answer
    assert answer['completed_edges']==2 and answer['unused_edges']==0 and peer.created==2
    assert answer['reserved_action_allowance']<=answer['max_total_actions']
    assert answer['reserved_poll_allowance']<=answer['max_total_polls']
    assert {str(root):files(root) for group in roots for root in group[:2]}==before
    repeated=plan.run(**args,transport=NoHTTP())
    assert repeated['status']=='plan_complete',repeated
    assert repeated['network_requests']==repeated['network_writes']==0


def test_uncertain_second_creation_never_replays(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch);peer.uncertain_at=2
    answer=plan.run(**args,transport=peer)
    assert answer['status']=='plan_refused' and peer.created==2
    with pytest.raises(runner.Refused,match='uncertain_creation_no_retry'):
        plan.run(**args,transport=NoHTTP())


@pytest.mark.parametrize('change',['last_route','overlap','backward','capture'])
def test_whole_plan_invalid_before_effects(configured,tmp_path,monkeypatch,change):
    args,peer,data,*_=setup(configured,tmp_path,monkeypatch)
    if change=='last_route':data['edges'][1]['route_config']=str(tmp_path/'absent.json')
    if change=='overlap':data['edges'][1]['next_job_dir']=data['edges'][0]['next_state_dir']
    if change=='backward':data['edges'][1]['route_config']=data['edges'][0]['route_config']
    if change=='capture':data['edges'][1]['next_capture_id']='bad/capture'
    args['plan_file'].write_text(json.dumps(data));before=files(tmp_path)
    with pytest.raises((runner.Refused,FileNotFoundError)):plan.run(**args,transport=NoHTTP())
    assert files(tmp_path)==before


def test_saved_plan_or_route_change_refused(configured,tmp_path,monkeypatch):
    args,peer,data,*_=setup(configured,tmp_path,monkeypatch)
    paused=plan.run(**args,transport=NoHTTP(),max_total_actions=1)
    assert paused['status']=='plan_paused'
    path=Path(data['edges'][1]['route_config']);path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(runner.Refused,match='saved_intent_changed'):plan.run(**args,transport=NoHTTP())


def test_global_phase_budget_stops_before_next_dispatch(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch)
    answer=plan.run(**args,transport=peer,max_total_actions=16)
    assert answer['status']=='plan_paused',answer
    assert answer['reserved_action_allowance']==16 and answer['completed_edges']==1
    assert peer.created==1


@pytest.mark.parametrize('terminal',[0,1])
def test_early_stop_exports_and_reports_unused_edges(configured,tmp_path,monkeypatch,terminal):
    args,peer,*_=setup(configured,tmp_path,monkeypatch,terminal=terminal)
    answer=plan.run(**args,transport=peer)
    assert answer['status']=='plan_complete' and answer['phase']=='early_physical_stop',answer
    assert answer['unused_edges']==2-terminal and peer.created==terminal
    assert (tmp_path/('output-'+str(terminal))/'complete.json').exists()
    assert answer['recording_complete'] is False
    again=plan.run(**args,transport=NoHTTP())
    assert again['status']=='plan_complete' and again['network_requests']==0


def test_final_rotation_exhausts_plan_without_fake_stop(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch,last_close='ROTATION')
    answer=plan.run(**args,transport=peer)
    assert answer['status']=='plan_exhausted',answer
    assert answer['terminal_stop_verified'] is False and answer['recording_complete'] is False
    assert not (tmp_path/'runner-2/finalize.intent.json').exists()


def test_plan_lock_held_and_cancel_before_effect(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch)
    lock=runner.engine.held_lock(args['control_dir']/'chain.lock',create=True)
    try:
        with pytest.raises(BlockingIOError):plan.run(**args,transport=NoHTTP())
    finally:os.close(lock)
    with pytest.raises(InterruptedError):plan.run(**args,transport=NoHTTP(),cancel=lambda:True)
    assert not (args['control_dir']/plan.INTENT).exists()


@pytest.mark.parametrize('terminal',[0,1])
def test_early_stop_resume_after_finalization_pause(configured,tmp_path,monkeypatch,terminal):
    args,peer,*_=setup(configured,tmp_path,monkeypatch,terminal=terminal)
    paused=plan.run(**(args|dict(phase_max_actions=2)),transport=peer)
    # With small action allowance the first bounded phase may stop at an earlier edge.
    for _ in range(4):
        if (tmp_path/('runner-'+str(terminal))/'finalize.done.json').exists():break
        assert paused['status']=='plan_paused',paused
        paused=plan.run(**(args|dict(phase_max_actions=2)),transport=peer)
    assert (tmp_path/('runner-'+str(terminal))/'finalize.done.json').exists()
    assert paused['status']=='plan_paused',paused
    assert not (tmp_path/('output-'+str(terminal))/'complete.json').exists()
    finished=plan.run(**args,transport=peer)
    assert finished['status']=='plan_complete' and finished['unused_edges']==2-terminal,finished
    assert peer.created==terminal


def test_route_change_after_zero_network_final_result_refuses(configured,tmp_path,monkeypatch):
    args,peer,data,*_=setup(configured,tmp_path,monkeypatch)
    assert plan.run(**args,transport=peer)['status']=='plan_complete'
    real=runner.watch
    def changed(**kw):
        result=real(**kw)
        if Path(kw['state_dir']).name=='runner-2':
            path=Path(data['edges'][1]['route_config']);path.write_bytes(path.read_bytes()+b'\n')
        return result
    monkeypatch.setattr(runner,'watch',changed)
    result=plan.run(**args,transport=NoHTTP())
    assert result['status']=='plan_refused' and result['network_requests']==0,result


def test_changed_plan_after_first_link_stops_before_second(configured,tmp_path,monkeypatch):
    args,peer,data,*_=setup(configured,tmp_path,monkeypatch);real=plan.handoff._coordinate
    def changed(**kw):
        result=real(**kw)
        args['plan_file'].write_bytes(args['plan_file'].read_bytes()+b'\n')
        return result
    monkeypatch.setattr(plan.handoff,'_coordinate',changed)
    result=plan.run(**args,transport=peer)
    assert result['status']=='plan_refused' and peer.created==1,result
    assert not (tmp_path/'runner-2').exists()


def test_global_network_budget_reserves_before_bootstrap(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch)
    answer=plan.run(**args,transport=peer,max_requests=8)
    assert answer['status'] in ('plan_paused','plan_refused'),answer
    assert answer['network_requests']<=8
    assert not (tmp_path/'runner-1/initialization/post.intent.json').exists()
    assert peer.created==0


def test_cancel_after_link_stops_before_second(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch);real=plan.handoff._coordinate;cancel=[False]
    def stopped(**kw):
        result=real(**kw);cancel[0]=True;return result
    monkeypatch.setattr(plan.handoff,'_coordinate',stopped)
    answer=plan.run(**args,transport=peer,cancel=lambda:cancel[0])
    assert answer['status']=='plan_refused' and peer.created==1
    assert not (tmp_path/'runner-2').exists()


def test_plan_lock_replacement_refused(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch);real=peer.request;changed=[False]
    def replaced(*a,**kw):
        result=real(*a,**kw)
        if not changed[0]:
            changed[0]=True;path=args['control_dir']/'chain.lock';path.rename(path.with_name('old.lock'))
            path.touch(mode=0o600)
        return result
    peer.request=replaced
    answer=plan.run(**args,transport=peer)
    assert answer['status']=='plan_refused' and peer.created==0


def test_invalid_seam_stops_before_next_session(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch,seam_error=True)
    answer=plan.run(**args,transport=peer)
    assert answer['status']=='plan_refused' and peer.created==0,answer
    assert not (tmp_path/'runner-1').exists()


def test_duplicate_next_candidate_requires_review(configured,tmp_path,monkeypatch):
    args,peer,data,*_=setup(configured,tmp_path,monkeypatch);real=runner._inspect
    successor=json.loads(Path(data['edges'][0]['route_config']).read_text())['inbox_dir']
    def duplicate(config,*a,**kw):
        rows=real(config,*a,**kw)
        return {n:items+items for n,items in rows.items()} if config['inbox_dir']==successor else rows
    monkeypatch.setattr(runner,'_inspect',duplicate)
    answer=plan.run(**args,transport=peer)
    assert answer['status']=='plan_refused' and peer.created==0,answer


def test_unknown_mutation_latches_further_writes(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch);real=peer.request;failed=[None]
    def lost(method,path,**kw):
        value=real(method,path,**kw)
        if method=='POST' and path.endswith('/pages') and failed[0] is None:
            failed[0]=len(peer.calls);raise u.Uncertain('Synthetic committed response lost')
        return value
    peer.request=lost
    answer=plan.run(**args,transport=peer)
    assert failed[0] is not None and answer['status']=='plan_refused'
    assert all(method=='GET' for method,path in peer.calls[failed[0]:])
    assert peer.created==0


def test_next_config_changed_after_link_has_no_next_effect(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch);real=plan.handoff._coordinate
    def changed(**kw):
        answer=real(**kw);path=tmp_path/'runner-1/config.json'
        value=json.loads(path.read_bytes());value['job_dir']=str(tmp_path/'foreign-job')
        path.write_bytes(runner.single._json(value));return answer
    monkeypatch.setattr(plan.handoff,'_coordinate',changed)
    answer=plan.run(**args,transport=peer)
    assert answer['status']=='plan_refused' and peer.created==1
    assert not (tmp_path/'foreign-job').exists() and not (tmp_path/'runner-2').exists()


def test_global_poll_allowance_is_not_reset_per_edge(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch)
    answer=plan.run(**args,transport=peer,max_total_polls=9)
    assert answer['status']=='plan_paused' and answer['completed_edges']==1,answer
    assert answer['reserved_poll_allowance']==9 and peer.created==1


def test_final_rotation_does_not_wait_entire_deadline(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch,last_close='ROTATION')
    real=runner.watch;final=[]
    def capture(**kw):
        if Path(kw['state_dir']).name=='runner-2':final.append(kw['max_polls'])
        return real(**kw)
    monkeypatch.setattr(runner,'watch',capture)
    answer=plan.run(**(args|dict(poll_seconds=3600)),transport=peer)
    assert answer['status']=='plan_exhausted' and final==[1],answer
