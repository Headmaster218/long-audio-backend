"""Explicit v2 generation source binding; no listeners or native ASR."""
import json
import os
from pathlib import Path
import shutil
import pytest
from audio_backend import recorder_logical_boundary as logical
from audio_backend import recorder_logical_advance as advance
from audio_backend import recorder_receiver_publication as publication
from audio_backend import recorder_ftps_receiver as receiver
from test_recorder_logical_boundary import files,invoke,runner,Peer
from test_recorder_inbox_runner import example,fake_export
from test_recorder_logical_advance import NextPeer


def cross(tmp_path):
    paths,c,root=example(tmp_path,3)
    templates=tmp_path/'templates';templates.mkdir(mode=0o700)
    sources=[]
    for path in paths:
        new=templates/path.name;path.rename(new);sources.append(new)
    old_raw=tmp_path/'raw0';old_raw.mkdir(mode=0o700)
    new_raw=tmp_path/'raw1';new_raw.mkdir(mode=0o700)
    new_inbox=tmp_path/'inbox1';new_inbox.mkdir(mode=0o700)
    old_state,new_state=tmp_path/'pub0',tmp_path/'pub1'
    publication.configure(state_dir=old_state,staging_dir=old_raw,inbox_dir=Path(c['inbox_dir']),exclusive_receiver=True)
    publication.configure(state_dir=new_state,staging_dir=new_raw,inbox_dir=new_inbox,exclusive_receiver=True)
    for index,source in enumerate(sources):
        raw=old_raw if index<2 else new_raw;state=old_state if index<2 else new_state
        dest=raw/source.name;shutil.copytree(source,dest);dest.chmod(0o700)
        for p in dest.iterdir():p.chmod(0o600)
        publication.publish_completed(state_dir=state,delivery=dest,completed_delivery=True)
    c['receiver_publication']=publication.bind(old_state)
    runner.configure(state_dir=root,config=c,first_delivery=paths[0]);peer=Peer();invoke(root,peer,max_actions=2)
    for name in ('cert','key','password'):
        p=tmp_path/name;p.write_text('synthetic-only');p.chmod(0o600)
    old=dict(binding=publication.bind(old_state),inbox_dir=c['inbox_dir'])
    new=dict(binding=publication.bind(new_state),inbox_dir=str(new_inbox))
    route=dict(schema=receiver.SCHEMA,**new,generations=[old,new],username='fixture',
        password_file=str(tmp_path/'password'),cert_file=str(tmp_path/'cert'),key_file=str(tmp_path/'key'))
    route_path=tmp_path/'route.json';route_path.write_text(json.dumps(route));route_path.chmod(0o600)
    args=dict(state_dir=root,successor_attempt=paths[2].name,next_state_dir=tmp_path/'next-runner',
        next_job_dir=tmp_path/'next-job',next_output_dir=tmp_path/'next-export',next_capture_id='next-span',
        next_session_name='next bounded span',acknowledge_logical_analysis_boundary=True,
        next_inbox_dir=new_inbox,next_receiver_state_dir=new_state,route_config=route_path)
    return c,root,peer,args,route,[old_state,new_state],[old_raw,new_raw]


def test_v2_cross_inbox_freezes_exact_route_and_successor(tmp_path):
    c,root,peer,args,route,states,raws=cross(tmp_path)
    before=files(Path(c['inbox_dir']))|files(args['next_inbox_dir'])|files(raws[0])|files(raws[1])
    logical.prepare(**args);receipt=json.loads((Path(c['job_dir'])/logical.NAME).read_bytes())
    assert receipt['schema']==logical.SCHEMA_V2
    assert receipt['successor_publication']==route['generations'][1]
    assert receipt['routing_provenance']['old_generation']==route['generations'][0]
    assert receipt['routing_provenance']['new_generation']==route['generations'][1]
    assert logical.prepare(**args)['receipt_sha256']==logical.digest(receipt)
    invoke(root,peer,finalize=True);assert peer.eos
    assert before==files(Path(c['inbox_dir']))|files(args['next_inbox_dir'])|files(raws[0])|files(raws[1])
    source=logical.source_config(receipt,c)
    assert source['inbox_dir']==str(args['next_inbox_dir']) and source['receiver_publication']==publication.bind(states[1])
    assert all(p in logical.protected(receipt) for p in [*raws,*states,Path(c['inbox_dir']),args['next_inbox_dir'],args['route_config']])


@pytest.mark.parametrize('missing',['next_inbox_dir','next_receiver_state_dir','route_config'])
def test_paired_explicit_arguments_required_without_writes(tmp_path,missing):
    c,root,peer,args,*_=cross(tmp_path);args[missing]=None;before=files(tmp_path)
    with pytest.raises(runner.Refused,match='explicit_arguments'):logical.prepare(**args)
    assert before==files(tmp_path)


@pytest.mark.parametrize('change',['route_bytes','active_old','omitted_old','wrong_pair','publication_identity','raw_copy','collision'])
def test_cross_scope_changes_fail_before_cut(tmp_path,change):
    c,root,peer,args,route,states,raws=cross(tmp_path)
    if change=='route_bytes':
        logical.prepare(**args);args['route_config'].write_text(json.dumps(route)+'\n')
    if change=='active_old':route.update(route['generations'][0]);args['route_config'].write_text(json.dumps(route))
    if change=='omitted_old':route['generations']=route['generations'][1:];args['route_config'].write_text(json.dumps(route))
    if change=='wrong_pair':args['next_receiver_state_dir']=states[0]
    if change=='publication_identity':
        old=args['next_inbox_dir'];old.rename(tmp_path/'saved-inbox');old.mkdir(mode=0o700)
    if change=='raw_copy':(raws[1]/args['successor_attempt']/'audio.wav').write_bytes(b'changed')
    if change=='collision':(raws[0]/args['successor_attempt']).mkdir(mode=0o700)
    before=files(tmp_path)
    with pytest.raises(Exception):logical.prepare(**args)
    assert before==files(tmp_path) and not peer.eos


def test_v1_never_silently_selects_other_inbox(tmp_path):
    c,root,peer,args,*_=cross(tmp_path)
    for k in ('next_inbox_dir','next_receiver_state_dir','route_config'):args.pop(k)
    before=files(tmp_path)
    with pytest.raises(runner.Refused,match='exact_successor'):logical.prepare(**args)
    assert before==files(tmp_path)


def test_advance_uses_v2_next_inbox_and_publication(tmp_path,monkeypatch):
    c,root,peer,args,route,states,raws=cross(tmp_path);logical.prepare(**args);invoke(root,peer,finalize=True)
    monkeypatch.setattr(runner.status,'run',lambda **kw:dict(status='completed',analysis_completion_verified=True))
    cut=json.loads((Path(c['job_dir'])/logical.NAME).read_bytes())
    def stub(output_dir,**kw):
        manifest=fake_export(output_dir=output_dir,**kw)
        manifest.update(closure_kind='logical_analysis_boundary',logical_boundary_sha256=logical.digest(cut),
                        frames=cut['frames'],recording_complete=False,recognition_continuity_verified=False)
        (Path(output_dir)/'complete.json').write_bytes(runner.single._json(manifest));return manifest
    monkeypatch.setattr(runner.exporter,'run',stub);invoke(root,peer,export=True)
    next_peer=NextPeer();options=dict(state_dir=root,acknowledge_create_next_session=True,
        exclusive_writer=True,acknowledge_span_local_closure=True)
    result=advance.advance(**options,transport=next_peer)
    assert result['network_writes']==1
    new=json.loads((args['next_state_dir']/'config.json').read_bytes())
    assert new['inbox_dir']==str(args['next_inbox_dir']) and new['receiver_publication']==publication.bind(states[1])
    invoke(args['next_state_dir'],next_peer)
    assert advance.advance(**options,transport=next_peer)['network_requests']==0


def test_live_raw_lease_does_not_block_readonly_selected_publication(tmp_path):
    import fcntl
    c,root,peer,args,route,states,raws=cross(tmp_path)
    fds=[os.open(p,os.O_RDONLY|os.O_DIRECTORY) for p in raws]
    try:
        for fd in fds:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert logical.prepare(**args)['status']=='logical_boundary_prepared'
    finally:
        for fd in fds:os.close(fd)


@pytest.mark.parametrize('part',[logical.ANCHOR,logical.RUNNER_ANCHOR,logical.NAME])
def test_v2_marker_first_interruption_exact_resume(tmp_path,monkeypatch,part):
    c,root,peer,args,*_=cross(tmp_path);real=logical.single.manifest_write
    def crash(path,value):
        real(path,value)
        if path.name==part:raise KeyboardInterrupt
    monkeypatch.setattr(logical.single,'manifest_write',crash)
    with pytest.raises(KeyboardInterrupt):logical.prepare(**args)
    monkeypatch.setattr(logical.single,'manifest_write',real)
    assert logical.prepare(**args)['status']=='logical_boundary_prepared'
    invoke(root,peer,finalize=True);assert peer.eos


def test_v2_requires_old_owned_publication(tmp_path):
    c,root,peer,args,*_=cross(tmp_path)
    with pytest.raises(runner.Refused,match='old_publication_required'):
        logical.routing_selection(args['route_config'],{k:v for k,v in c.items() if k!='receiver_publication'})


@pytest.mark.parametrize('consumer',['ordinary','recovery'])
@pytest.mark.parametrize('where',['old_raw','old_state','old_inbox','new_raw','new_state','new_inbox','route'])
def test_both_generation_trees_protected_before_http_or_creation(tmp_path,consumer,where):
    from audio_backend import recorder_incremental_span_export as export
    from audio_backend import recorder_export_recovery as recovery
    c,root,peer,args,route,states,raws=cross(tmp_path);logical.prepare(**args);invoke(root,peer,finalize=True)
    protected={'old_raw':raws[0],'old_state':states[0],'old_inbox':Path(c['inbox_dir']),
        'new_raw':raws[1],'new_state':states[1],'new_inbox':args['next_inbox_dir'],'route':args['route_config']}[where]
    target=protected/'bad-output';before=files(tmp_path);names={str(p) for p in tmp_path.rglob('*')}
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('HTTP before rejecting protected generation output')
    with pytest.raises(Exception):
        (export if consumer=='ordinary' else recovery).run(job_dir=c['job_dir'],api=c['api'],
            session_id=c['session_id'],capture_id=c['capture_id'],exclusive_writer=True,
            limits=runner.u.Limits(disk_floor=0),transport=NoHTTP(),output_dir=target)
    assert names=={str(p) for p in tmp_path.rglob('*')} and before==files(tmp_path)


@pytest.mark.parametrize('changed',['route','raw_identity','published_identity'])
def test_changed_v2_provenance_refuses_before_effects(tmp_path,changed):
    c,root,peer,args,route,states,raws=cross(tmp_path);logical.prepare(**args)
    if changed=='route':args['route_config'].write_text(json.dumps(route)+'\n')
    else:
        path=(raws[1] if changed=='raw_identity' else args['next_inbox_dir'])/args['successor_attempt']
        saved=tmp_path/'saved-attempt';path.rename(saved);shutil.copytree(saved,path)
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('HTTP with changed generation provenance')
    before=files(tmp_path)
    with pytest.raises(Exception):invoke(root,NoHTTP(),finalize=True)
    assert before==files(tmp_path)


def test_v1_schema_cannot_acquire_v2_fields(tmp_path):
    c,root,peer,args,*_=cross(tmp_path);logical.prepare(**args)
    job=Path(c['job_dir']);value=runner.single.manifest_read(job/logical.NAME)
    value['schema']=logical.SCHEMA
    header=runner.single.manifest_read(job/'capture.json')
    receipts,records=runner.engine.registry(job,header,lambda:120,policy=runner.intake.POLICY)
    with pytest.raises(runner.Refused,match='fields'):
        logical.validate_proposal(job,header,receipts,records,lambda:120,value)


def test_cancelled_preparation_no_writes_and_between_markers_preserves_fence(tmp_path,monkeypatch):
    c,root,peer,args,*_=cross(tmp_path);before=files(tmp_path)
    with pytest.raises(InterruptedError):logical.prepare(**args,cancel=lambda:True)
    assert before==files(tmp_path)
    flag=[False];real=logical.single.manifest_write
    def stop(path,value):
        real(path,value)
        if path.name==logical.ANCHOR:flag[0]=True
    monkeypatch.setattr(logical.single,'manifest_write',stop)
    with pytest.raises(InterruptedError):logical.prepare(**args,cancel=lambda:flag[0])
    assert (Path(c['job_dir'])/logical.ANCHOR).exists()
    assert not (root/logical.RUNNER_ANCHOR).exists()
    assert not (Path(c['job_dir'])/logical.NAME).exists()
    monkeypatch.setattr(logical.single,'manifest_write',real)
    assert logical.prepare(**args)['status']=='logical_boundary_prepared'
