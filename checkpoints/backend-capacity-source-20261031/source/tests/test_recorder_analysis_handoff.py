"""Automatic orchestration with real publication files and synthetic HTTP."""
from pathlib import Path
import pytest
from audio_backend import recorder_analysis_handoff as automatic
from audio_backend import recorder_logical_boundary as boundary
from test_recorder_generation_transition import setup, files, configured, NoHTTP
from test_recorder_inbox_runner import runner, fake_export


def options(args):
    return {k:v for k,v in args.items() if k in ('state_dir','route_config','next_state_dir',
        'next_job_dir','next_output_dir','next_capture_id','next_session_name','poll_seconds','limits')} | dict(
        acknowledge_automatic_handoff=True,exclusive_writer=True,acknowledge_span_local_closure=True)


@pytest.mark.parametrize('admit',[True,False])
def test_discovers_links_and_intakes_without_manual_selection(configured,tmp_path,monkeypatch,admit):
    args,peer,config,old_raw,new_raw=setup(configured,tmp_path,monkeypatch,admit=admit)
    old_export=runner.exporter.run
    def export(output_dir,**kw):
        return old_export(output_dir=output_dir,**kw) if Path(output_dir)==Path(config['output_dir']) else fake_export(output_dir=output_dir,**kw)
    monkeypatch.setattr(runner.exporter,'run',export)
    before=files(old_raw)|files(new_raw)
    result=automatic.handoff(**options(args),transport=peer)
    assert result['status']=='handoff_complete',result
    assert result['next_session_linked'] and result['stop_details']['inputs']==1
    assert result['transition']['closed_span_frames']==8
    assert result['transition']['next_original_frame_start']==1008
    assert peer.calls.count(('POST','/sessions'))==1
    assert files(old_raw)|files(new_raw)==before
    again=automatic.handoff(**options(args),transport=NoHTTP())
    assert again['status']=='handoff_complete',again
    assert again['network_requests']==0


def test_uncertain_creation_stops_and_never_reposts(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch);peer.next.uncertain=True
    first=automatic.handoff(**options(args),transport=peer)
    assert first['status']=='handoff_refused'
    assert peer.calls.count(('POST','/sessions'))==1
    second=automatic.handoff(**options(args),transport=NoHTTP())
    assert second['status']=='handoff_refused'
    assert second['network_requests']==0


def test_no_automatic_resume_of_paused_transition(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch)
    result=automatic.handoff(**options(args),transport=peer,max_actions=1)
    assert result['status']=='handoff_paused' and result['phase']=='transition'
    assert not args['next_state_dir'].exists()


def test_no_automatic_resume_of_unresolved_drain(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch);calls=[]
    def unresolved(**kw):
        calls.append(kw);return dict(status='unresolved')
    monkeypatch.setattr(runner,'watch',unresolved)
    result=automatic.handoff(**options(args),transport=NoHTTP())
    assert result['status']=='handoff_paused' and len(calls)==1
    assert not args['next_state_dir'].exists()


@pytest.mark.parametrize('key,value',[('max_polls',0),('max_actions',True),('deadline_seconds',float('nan')),
    ('max_network_bytes',1),('acknowledge_automatic_handoff',False)])
def test_invalid_bound_has_no_effect(configured,tmp_path,monkeypatch,key,value):
    args,peer,*_=setup(configured,tmp_path,monkeypatch);before=files(tmp_path)
    with pytest.raises(runner.Refused):automatic.handoff(**(options(args)|{key:value}),transport=NoHTTP())
    assert files(tmp_path)==before


def test_route_change_after_discovery_is_refused_before_effect(configured,tmp_path,monkeypatch):
    args,peer,config,*_=setup(configured,tmp_path,monkeypatch)
    real=automatic.transition.transition
    def changed(**kw):
        args['route_config'].write_bytes(args['route_config'].read_bytes()+b'\n')
        return real(**kw)
    monkeypatch.setattr(automatic.transition,'transition',changed)
    result=automatic.handoff(**options(args),transport=NoHTTP())
    assert result['status']=='handoff_refused'
    assert not (Path(config['job_dir'])/boundary.NAME).exists()


def test_remaining_bootstrap_budget_does_not_strand_post_intent(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch)
    real=runner.watch;first=[True]
    def drain(**kw):
        if first[0]:
            first[0]=False
            for _ in range(3):kw['transport'].request('GET','/health')
        return real(**kw)
    monkeypatch.setattr(runner,'watch',drain)
    result=automatic.handoff(**options(args),transport=peer,max_requests=8)
    assert result['status']=='handoff_paused',result
    assert result['network_requests']<=8
    assert not (args['next_state_dir']/'initialization/post.intent.json').exists()
    assert ('POST','/sessions') not in peer.calls


def test_duplicate_discovery_refused(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch);real=runner._inspect
    def duplicate(config,*a,**kw):
        result=real(config,*a,**kw)
        if config['inbox_dir']!=str(configured['ready']):
            return {key:value+value for key,value in result.items()}
        return result
    monkeypatch.setattr(runner,'_inspect',duplicate)
    result=automatic.handoff(**options(args),transport=NoHTTP())
    assert result['status']=='handoff_refused'
    assert not args['next_state_dir'].exists()
