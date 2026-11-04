"""Exact next-session bootstrap; synthetic HTTP/export, no native-quality claim."""
import json
from pathlib import Path
import pytest
from audio_backend import recorder_logical_advance as advance
from audio_backend import recorder_logical_boundary as logical
from audio_backend import recording_uploader as u
from test_recorder_logical_boundary import setup,files,invoke,runner
from test_recorder_inbox_runner import fake_export
from test_recorder_runner_initialize import CreationPeer


class NextPeer(CreationPeer):
    def request(self,method,path,**kwargs):
        value=super().request(method,path.replace('next-session','known').replace('next-span','extended'),**kwargs)
        def convert(value):
            if isinstance(value,dict):return {k:convert(v) for k,v in value.items()}
            if isinstance(value,list):return [convert(v) for v in value]
            return 'next-session' if value=='known' else 'next-span' if value=='extended' else value
        return convert(value)


def ready(tmp_path,monkeypatch):
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args)
    invoke(root,peer,finalize=True)
    monkeypatch.setattr(runner.status,'run',lambda **kw:dict(status='completed',analysis_completion_verified=True))
    def stub(output_dir,**kwargs):
        manifest=fake_export(output_dir=output_dir,**kwargs)
        cut=json.loads((Path(c['job_dir'])/logical.NAME).read_bytes())
        manifest.update(closure_kind='logical_analysis_boundary',logical_boundary_sha256=logical.digest(cut),
                        frames=cut['frames'],recording_complete=False,recognition_continuity_verified=False)
        (Path(output_dir)/'complete.json').write_bytes(runner.single._json(manifest))
        return manifest
    monkeypatch.setattr(runner.exporter,'run',stub)
    invoke(root,peer,export=True)
    options=dict(state_dir=root,acknowledge_create_next_session=True,exclusive_writer=True,
                 acknowledge_span_local_closure=True)
    return paths,c,root,args,options


def test_one_post_exact_link_and_no_network_repeat_after_intake(tmp_path,monkeypatch):
    paths,c,root,args,options=ready(tmp_path,monkeypatch);peer=NextPeer()
    old=files(root)|files(Path(c['job_dir']))|files(Path(c['output_dir']))
    result=advance.advance(**options,transport=peer)
    assert result['status']=='next_session_linked' and result['network_writes']==1
    assert result['next_original_sequence']==25 and result['next_original_frame_start']==1008
    assert result['closed_span_frames']==8 and result['atomic_rollover'] is False
    assert [x for x in peer.calls if x[0]!='GET']==[('POST','/sessions')]
    invoke(args['next_state_dir'],peer)
    assert Path(args['next_job_dir']).exists()
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('repeat must not use HTTP')
    again=advance.advance(**options,transport=NoHTTP())
    assert again['network_requests']==0 and again['next_intake_started'] is True
    assert again['boundary_sha256']==result['boundary_sha256']
    assert old==files(root)|files(Path(c['job_dir']))|files(Path(c['output_dir']))


def test_missing_ack_no_new_state_or_http(tmp_path,monkeypatch):
    paths,c,root,args,options=ready(tmp_path,monkeypatch);peer=NextPeer()
    before=files(tmp_path)
    with pytest.raises(runner.Refused,match='ack_required'):
        advance.advance(**dict(options,acknowledge_create_next_session=False),transport=peer)
    assert not peer.calls and files(tmp_path)==before


def test_uncertain_post_never_retried(tmp_path,monkeypatch):
    paths,c,root,args,options=ready(tmp_path,monkeypatch);peer=NextPeer();peer.uncertain=True
    with pytest.raises(u.Uncertain):advance.advance(**options,transport=peer)
    assert len([x for x in peer.calls if x[0]=='POST'])==1
    count=len(peer.calls)
    with pytest.raises(runner.Refused,match='uncertain_no_retry'):advance.advance(**options,transport=peer)
    assert len(peer.calls)==count


@pytest.mark.parametrize('changed',['next_config','next_receipt','old_export','old_boundary'])
def test_changed_link_refuses_without_http(tmp_path,monkeypatch,changed):
    paths,c,root,args,options=ready(tmp_path,monkeypatch);peer=NextPeer()
    advance.advance(**options,transport=peer)
    if changed=='next_config':
        path=args['next_state_dir']/'config.json';d=json.loads(path.read_bytes());d['capture_id']='other';path.write_bytes(runner.single._json(d))
    if changed=='next_receipt':
        path=args['next_state_dir']/'initialization/intent.json';d=json.loads(path.read_bytes());d['request']['body']['name']='other';path.write_bytes(runner.single._json(d))
    if changed=='old_export':(Path(c['output_dir'])/'transcript.txt').write_bytes(b'changed')
    if changed=='old_boundary':
        path=Path(c['job_dir'])/logical.NAME;d=json.loads(path.read_bytes());d['next_session_intent']['session_name']='other';path.write_bytes(runner.single._json(d))
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('invalid link must not use HTTP')
    before=files(tmp_path)
    with pytest.raises(Exception):advance.advance(**options,transport=NoHTTP())
    assert files(tmp_path)==before


def test_requires_verified_old_export_before_create(tmp_path):
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args);invoke(root,peer,finalize=True)
    new=NextPeer()
    with pytest.raises(runner.Refused,match='completed_export_required'):
        advance.advance(state_dir=root,acknowledge_create_next_session=True,exclusive_writer=True,
            acknowledge_span_local_closure=True,transport=new)
    assert not new.calls and not args['next_state_dir'].exists()


def test_confirmed_creation_interruption_resumes_without_second_post(tmp_path,monkeypatch):
    paths,c,root,args,options=ready(tmp_path,monkeypatch);peer=NextPeer()
    real=advance.initialize._saved
    def crash(path,value):
        if path.name=='config.json':raise KeyboardInterrupt
        return real(path,value)
    monkeypatch.setattr(advance.initialize,'_saved',crash)
    with pytest.raises(KeyboardInterrupt):advance.advance(**options,transport=peer)
    assert (args['next_state_dir']/'initialization/created.json').exists()
    monkeypatch.setattr(advance.initialize,'_saved',real)
    result=advance.advance(**options,transport=peer)
    assert result['network_writes']==0 and len([x for x in peer.calls if x[0]=='POST'])==1


def test_completed_creation_lost_return_repeats_without_http(tmp_path,monkeypatch):
    paths,c,root,args,options=ready(tmp_path,monkeypatch);peer=NextPeer()
    real=advance.initialize.initialize
    def crash(**kw):real(**kw);raise KeyboardInterrupt
    monkeypatch.setattr(advance.initialize,'initialize',crash)
    with pytest.raises(KeyboardInterrupt):advance.advance(**options,transport=peer)
    monkeypatch.setattr(advance.initialize,'initialize',real)
    before=len(peer.calls)
    assert advance.advance(**options,transport=peer)['network_requests']==0
    assert len(peer.calls)==before


def test_export_change_after_health_blocks_post(tmp_path,monkeypatch):
    paths,c,root,args,options=ready(tmp_path,monkeypatch);peer=NextPeer();real=peer.request
    def change(method,path,**kw):
        result=real(method,path,**kw)
        if path=='/health':(Path(c['output_dir'])/'transcript.txt').write_bytes(b'changed')
        return result
    peer.request=change
    with pytest.raises(runner.Refused):advance.advance(**options,transport=peer)
    assert not [x for x in peer.calls if x[0]=='POST']


def test_old_session_response_never_confirms_new_link(tmp_path,monkeypatch):
    paths,c,root,args,options=ready(tmp_path,monkeypatch);peer=CreationPeer()
    with pytest.raises(runner.Refused,match='distinct_session'):advance.advance(**options,transport=peer)
    assert not (args['next_state_dir']/'config.json').exists()
    count=len(peer.calls)
    with pytest.raises(runner.Refused,match='uncertain_no_retry'):advance.advance(**options,transport=peer)
    assert len(peer.calls)==count
