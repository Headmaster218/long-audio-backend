"""Explicit bank inheritance preserves originals; no speaker-quality claim."""
import copy
import json
from pathlib import Path

import pytest

from audio_backend import recorder_runner_initialize as init
from audio_backend import recorder_logical_boundary as boundary
from audio_backend import recorder_logical_advance as advance
from audio_backend import recorder_generation_transition as transition
from audio_backend import recording_uploader as u
from test_recorder_runner_initialize import options, CreationPeer
from test_recorder_logical_advance import ready, NextPeer
from test_recorder_logical_boundary import setup, files, invoke, runner
from test_recorder_ftps_receiver import configured
from test_recorder_generation_transition import setup as transition_setup

GROUP='reviewed.family'
GENERATION='a'*32
POLICY='b'*64
FLAGS=dict(preserve_reviewed_identity_group=GROUP,identity_generation=GENERATION,identity_policy_sha256=POLICY)


class BankPeer:
    def __init__(self, inner=None, source='old-session', target='known'):
        self.inner=inner or CreationPeer();self.source=source;self.target=target
        self.calls=[];self.bodies=[];self.source_group=GROUP;self.target_group=GROUP
        self.after_post=None;self.after_bank=None
        self.bank=dict(identity_group=GROUP,identity_mode='verified_review_v1',generation=GENERATION,
            policy_sha256=POLICY,primary_associations_enabled=True,explicitly_retained=True,
            automatic_enrollment=False,profile_revision=3,availability_sequence=4,space_id=None)
    def request(self,method,path,**kwargs):
        self.calls.append((method,path))
        if path=='/sessions/'+self.source:
            return dict(id=self.source,name='source',identity_group=self.source_group,processing_mode='legacy')
        if path=='/identity-groups/'+GROUP+'/reviewed-person-bank':
            result=copy.deepcopy(self.bank)
            if self.after_bank:self.after_bank()
            return result
        if method=='POST' and path=='/sessions':self.bodies.append(copy.deepcopy(kwargs['body']))
        result=self.inner.request(method,path,**kwargs)
        if path=='/sessions/'+self.target:result=dict(result,identity_group=self.target_group)
        if method=='POST' and path=='/sessions' and self.after_post:self.after_post()
        return result


def identity_ready(tmp_path,monkeypatch):
    original=boundary.prepare
    with monkeypatch.context() as patch:
        patch.setattr(boundary,'prepare',lambda **kw:original(**kw,**FLAGS))
        return ready(tmp_path,monkeypatch)


def test_initializer_explicit_existing_bank_exact_receipts(tmp_path):
    args,paths,config,state=options(tmp_path);peer=BankPeer()
    before=files(Path(config['inbox_dir']))
    result=init.initialize(**args,**FLAGS,identity_source_session_id='old-session',transport=peer)
    assert result['schema']==init.SCHEMA_V2 and result['network_requests']==11 and result['network_writes']==1
    assert peer.bodies==[dict(name=args['session_name'],processing_mode='legacy',identity_group=GROUP,identity_mode='verified_review_v1')]
    intent=json.loads((state/'initialization/intent.json').read_bytes())
    assert intent['reviewed_identity_handoff']==init.reviewed_handoff('old-session',GROUP,GENERATION,POLICY)
    assert intent['request']['body']==peer.bodies[0]
    assert before==files(Path(config['inbox_dir'])) and not Path(config['job_dir']).exists()
    before_posts=len(peer.bodies)
    assert init.initialize(**args,**FLAGS,identity_source_session_id='old-session',resume=True,transport=peer)['network_writes']==0
    assert len(peer.bodies)==before_posts


@pytest.mark.parametrize('field,value',[
    ('generation','c'*32),('policy_sha256','c'*64),('identity_mode','legacy'),
    ('primary_associations_enabled',False),('automatic_enrollment',True),('explicitly_retained',False)])
def test_wrong_bank_refused_before_post(tmp_path,field,value):
    args,_,config,state=options(tmp_path);peer=BankPeer();peer.bank[field]=value
    with pytest.raises(init.single.Refused,match='bank_changed'):
        init.initialize(**args,**FLAGS,identity_source_session_id='old-session',transport=peer)
    assert not peer.bodies and not (state/'initialization/post.intent.json').exists()
    assert not Path(config['job_dir']).exists()


@pytest.mark.parametrize('stage',['source_before','target_after','reset_after','policy_after'])
def test_changed_membership_or_bank_retains_confirmed_empty_creation(tmp_path,stage):
    args,_,config,state=options(tmp_path);peer=BankPeer()
    if stage=='source_before':peer.source_group='different'
    elif stage=='target_after':peer.target_group='different'
    else:peer.after_post=lambda:peer.bank.update(**{'generation' if stage=='reset_after' else 'policy_sha256':'c'*(32 if stage=='reset_after' else 64)})
    with pytest.raises(init.single.Refused):
        init.initialize(**args,**FLAGS,identity_source_session_id='old-session',transport=peer)
    assert len(peer.bodies)==(0 if stage=='source_before' else 1)
    assert (state/'initialization/created.json').exists()==(stage!='source_before')
    assert not (state/'config.json').exists() and not Path(config['job_dir']).exists()
    if stage!='source_before':
        with pytest.raises(init.single.Refused):
            init.initialize(**args,**FLAGS,identity_source_session_id='old-session',resume=True,transport=peer)
        assert len(peer.bodies)==1


def test_uncertain_bank_creation_never_retries(tmp_path):
    args,_,_,state=options(tmp_path);peer=BankPeer();peer.inner.uncertain=True
    with pytest.raises(u.Uncertain):init.initialize(**args,**FLAGS,identity_source_session_id='old-session',transport=peer)
    calls=list(peer.calls)
    with pytest.raises(init.single.Refused,match='uncertain_no_retry'):
        init.initialize(**args,**FLAGS,identity_source_session_id='old-session',resume=True,transport=peer)
    assert peer.calls==calls and len(peer.bodies)==1


@pytest.mark.parametrize('missing',list(FLAGS)+['identity_source_session_id'])
def test_partial_opt_in_refuses_without_state_or_http(tmp_path,missing):
    args,_,_,state=options(tmp_path);peer=BankPeer()
    flags=dict(FLAGS,identity_source_session_id='old-session');flags.pop(missing)
    with pytest.raises(init.single.Refused,match='explicit_arguments'):
        init.initialize(**args,**flags,transport=peer)
    assert not peer.calls and not state.exists()


def test_boundary_v3_same_inbox_binds_exact_next_body_and_zero_http(tmp_path):
    paths,config,state,peer,args=setup(tmp_path)
    before=files(Path(config['inbox_dir']));result=boundary.prepare(**args,**FLAGS)
    assert result['network_requests']==0
    cut=json.loads((Path(config['job_dir'])/boundary.NAME).read_bytes())
    assert cut['schema']==boundary.SCHEMA_V3 and not boundary.cross_inbox(cut)
    nxt=cut['next_session_intent'];handoff=nxt['reviewed_identity_handoff']
    assert handoff==init.reviewed_handoff(config['session_id'],GROUP,GENERATION,POLICY)
    assert nxt['session_creation_request']==init.creation_request(config['api'],args['next_session_name'],handoff)
    assert cut['recognition_continuity_verified'] is False
    assert before==files(Path(config['inbox_dir']))
    assert boundary.prepare(**args,**FLAGS)['receipt_sha256']==result['receipt_sha256']
    with pytest.raises(init.single.Refused):boundary.prepare(**args,**dict(FLAGS,identity_generation='d'*32))


def test_advance_and_intake_preserve_bank_and_frame_edge(tmp_path,monkeypatch):
    paths,config,state,args,opts=identity_ready(tmp_path,monkeypatch)
    peer=BankPeer(NextPeer(),source='known',target='next-session')
    before=files(Path(config['inbox_dir']))
    linked=advance.advance(**opts,transport=peer)
    assert linked['network_requests']==11 and linked['next_original_frame_start']==1008
    assert linked['closed_span_frames']==8 and linked['recognition_continuity_verified'] is False
    assert peer.bodies[0]['identity_group']==GROUP
    result=invoke(args['next_state_dir'],peer)
    assert result['inputs']==1
    assert before==files(Path(config['inbox_dir']))
    assert [call for call in peer.calls if call[0]!='GET' and not call[1].startswith('/sessions/next-session/recordings/')]==[('POST','/sessions')]
    assert advance.advance(**opts,transport=peer)['network_requests']==0


def test_reset_after_completed_link_refuses_before_first_intake(tmp_path,monkeypatch):
    _,config,state,args,opts=identity_ready(tmp_path,monkeypatch)
    peer=BankPeer(NextPeer(),source='known',target='next-session')
    advance.advance(**opts,transport=peer);peer.bank['generation']='c'*32
    before=list(peer.calls)
    with pytest.raises(init.single.Refused,match='bank_changed'):invoke(args['next_state_dir'],peer)
    assert not Path(args['next_job_dir']).exists()
    assert (args['next_state_dir']/'initialization/created.json').exists()
    assert all(method=='GET' for method,path in peer.calls[len(before):])
    assert len(peer.bodies)==1


def test_review_changes_and_first_space_binding_are_not_reset(tmp_path):
    args,_,_,state=options(tmp_path);peer=BankPeer()
    peer.after_post=lambda:peer.bank.update(profile_revision=99,availability_sequence=100,space_id='new-reviewed-space')
    init.initialize(**args,**FLAGS,identity_source_session_id='old-session',transport=peer)
    assert invoke(state,peer)['inputs']==1


def test_runner_identity_checks_share_request_budget(tmp_path):
    args,_,config,state=options(tmp_path);peer=BankPeer()
    init.initialize(**args,**FLAGS,identity_source_session_id='old-session',transport=peer)
    before=len(peer.calls)
    with pytest.raises(init.single.Refused,match='network_request_bound'):invoke(state,peer,max_requests=2)
    assert len(peer.calls)-before==2 and not Path(config['job_dir']).exists()


def test_coordinator_cross_inbox_v3_and_resume(configured,tmp_path,monkeypatch):
    args,pair,config,old_raw,new_raw=transition_setup(configured,tmp_path,monkeypatch)
    peer=BankPeer(pair,source='known',target='next-session')
    before=files(old_raw)|files(new_raw)
    result=transition.transition(**args,**FLAGS,transport=peer)
    assert result['status']=='transition_complete'
    cut=json.loads((Path(config['job_dir'])/boundary.NAME).read_bytes())
    assert cut['schema']==boundary.SCHEMA_V3 and boundary.cross_inbox(cut)
    assert cut['next_session_intent']['session_creation_request']['body']['identity_group']==GROUP
    assert before==files(old_raw)|files(new_raw)
    assert transition.transition(**args,**FLAGS,transport=peer)['network_requests']==0
    count=len(peer.calls)
    with pytest.raises(init.single.Refused,match='saved_intent_changed'):
        transition.transition(**args,**dict(FLAGS,identity_policy_sha256='d'*64),transport=peer)
    assert len(peer.calls)==count


@pytest.mark.parametrize('kind',['generation','policy','source'])
def test_coordinator_known_invalid_bank_does_not_finalize_old(configured,tmp_path,monkeypatch,kind):
    args,pair,config,*_=transition_setup(configured,tmp_path,monkeypatch)
    peer=BankPeer(pair,source='known',target='next-session')
    if kind=='source':peer.source_group='different'
    else:peer.bank['generation' if kind=='generation' else 'policy_sha256']='c'*(32 if kind=='generation' else 64)
    with pytest.raises(init.single.Refused):transition.transition(**args,**FLAGS,transport=peer)
    assert not (args['state_dir']/'finalize.intent.json').exists()
    assert not [call for call in peer.calls if call[0]!='GET']
    assert not args['next_state_dir'].exists()


def test_coordinator_identity_checks_share_total_budget(configured,tmp_path,monkeypatch):
    args,pair,config,*_=transition_setup(configured,tmp_path,monkeypatch)
    peer=BankPeer(pair,source='known',target='next-session')
    with pytest.raises(init.single.Refused,match='identity_network_request_bound'):
        transition.transition(**dict(args,max_requests=1),**FLAGS,transport=peer)
    assert len(peer.calls)==1 and not (args['state_dir']/'finalize.intent.json').exists()


def test_preflight_does_not_claim_atomic_write_generation_fencing():
    """Document the API limitation: a reset can race the final GET and write."""
    handoff=init.reviewed_handoff('old',GROUP,GENERATION,POLICY)
    class RacingPeer:
        reset=False
        writes=0
        def request(self,method,path,**kwargs):
            if method!='GET':
                assert self.reset
                self.writes+=1
                return {}
            if path.endswith('/reviewed-person-bank'):
                response=dict(identity_group=GROUP,identity_mode='verified_review_v1',
                    generation='c'*32 if self.reset else GENERATION,policy_sha256=POLICY,
                    primary_associations_enabled=True,explicitly_retained=True,automatic_enrollment=False)
                self.reset=True
                return response
            return dict(id=path.rsplit('/',1)[1],identity_group=GROUP,processing_mode='legacy')
    peer=RacingPeer()
    http=runner._Transport(peer,u.Guard(u.Limits()),dict(session_id='next',capture_id='capture'),
        20,3,lambda writes:None,reviewed_identity_handoff=handoff)
    http.request('PUT','/sessions/next/recordings/capture/example')
    assert peer.writes==1 and http.requests==4
    with pytest.raises(init.single.Refused,match='bank_changed'):
        http.request('PUT','/sessions/next/recordings/capture/example')
    assert peer.writes==1


@pytest.mark.parametrize('outcome',['created_then_reset','reset_after_post','uncertain_post'])
def test_real_app_existing_bank_inheritance_and_reset(tmp_path,monkeypatch,outcome):
    """Actual supported routes/Store; no native lifetime, model or enrollment."""
    from fastapi.testclient import TestClient
    from audio_backend.app import create_app
    from audio_backend.settings import Settings
    from reviewed_person_bridge_fixtures import POLICY as REAL_POLICY, activation_request
    server=tmp_path/'server';server.mkdir()
    policy_file=server/'synthetic-reviewed-policy.json';policy_file.write_text(json.dumps(REAL_POLICY))
    settings=Settings(data_dir=server,start_worker=False,disk_reserve_bytes=0,
        recording_disk_reserve_bytes=0,reviewed_primary_persons_enabled=True,
        verified_identity_policy_file=policy_file)
    args,_,config,state=options(tmp_path)
    app=create_app(settings)
    with TestClient(app) as client:
        def call(method,path,body=None):
            return client.request(method,path,**({'json':body} if body is not None else {}))
        baseline=call('POST','/sessions',dict(name='default unchanged'))
        assert baseline.status_code==201 and baseline.json()['identity_group']=='default'
        activation=call('PUT','/identity-groups/'+GROUP+'/reviewed-person-bank',activation_request())
        assert activation.status_code==200,activation.text
        bank=call('GET','/identity-groups/'+GROUP+'/reviewed-person-bank').json()
        source=call('POST','/sessions',dict(name='existing source',identity_group=GROUP,identity_mode='verified_review_v1'))
        assert source.status_code==201,source.text
        source_id=source.json()['id']
        flags=dict(preserve_reviewed_identity_group=GROUP,identity_source_session_id=source_id,
            identity_generation=bank['generation'],identity_policy_sha256=bank['policy_sha256'])
        before=call('GET','/identity-groups/'+GROUP+'/reviewed-person-bank').json()
        class Routes:
            calls=[]
            def request(self,method,path,body=None,**kwargs):
                assert not kwargs
                self.calls.append((method,path))
                response=call(method,path,body)
                if response.status_code>=400:raise u.HTTPFailure(response.status_code,'synthetic API response')
                if method=='POST' and path=='/sessions':
                    if outcome=='reset_after_post':
                        assert call('DELETE','/identity-groups/'+GROUP+'/speakers').status_code==200
                    if outcome=='uncertain_post':raise u.Uncertain('synthetic lost response')
                return response.json()
        peer=Routes()
        if outcome=='created_then_reset':
            # A native-free legacy fixture cannot claim live payload-v2 readiness.
            # Real bank checks and creation succeed, then the existing strict
            # contract refuses honestly and retains the confirmed empty session.
            with pytest.raises(init.single.Refused,match='incompatible_strict_contract'):
                init.initialize(**args,**flags,transport=peer)
            target=json.loads((state/'initialization/created.json').read_bytes())['session_id']
            assert call('GET','/sessions/'+target).json()['identity_group']==GROUP
            assert before==call('GET','/identity-groups/'+GROUP+'/reviewed-person-bank').json()
            assert call('DELETE','/identity-groups/'+GROUP+'/speakers').status_code==200
            with pytest.raises(init.single.Refused,match='bank_changed'):
                init.initialize(**args,**flags,resume=True,transport=peer)
        elif outcome=='reset_after_post':
            with pytest.raises(init.single.Refused,match='bank_changed'):init.initialize(**args,**flags,transport=peer)
            assert (state/'initialization/created.json').exists()
            with pytest.raises(init.single.Refused,match='bank_changed'):init.initialize(**args,**flags,resume=True,transport=peer)
        else:
            with pytest.raises(u.Uncertain):init.initialize(**args,**flags,transport=peer)
            count=len(peer.calls)
            with pytest.raises(init.single.Refused,match='uncertain_no_retry'):init.initialize(**args,**flags,resume=True,transport=peer)
            assert len(peer.calls)==count
        assert sum(method=='POST' and path=='/sessions' for method,path in peer.calls)==1
        assert not Path(config['job_dir']).exists()
        with app.state.store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM sessions WHERE identity_group=?',(GROUP,)).fetchone()[0]==2
            assert db.execute('SELECT COUNT(*) FROM speakers').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM recording_sources').fetchone()[0]==0


def test_missing_opt_in_initialization_cannot_downgrade_to_legacy(tmp_path):
    args,_,config,state=options(tmp_path);peer=BankPeer()
    init.initialize(**args,**FLAGS,identity_source_session_id='old-session',transport=peer)
    saved=json.loads((state/'config.json').read_bytes())
    assert saved['schema']==runner.CONFIG_SCHEMA_V2 and saved['reviewed_identity_handoff']['identity_group']==GROUP
    (state/'initialization').rename(tmp_path/'preserved-initialization')
    calls=list(peer.calls)
    with pytest.raises((init.single.Refused,FileNotFoundError)):invoke(state,peer)
    assert peer.calls==calls and not Path(config['job_dir']).exists()


def test_coordinator_reserves_bootstrap_before_post_intent(configured,tmp_path,monkeypatch):
    args,pair,config,*_=transition_setup(configured,tmp_path,monkeypatch)
    peer=BankPeer(pair,source='known',target='next-session')
    paused=transition.transition(**dict(args,max_actions=1),**FLAGS,transport=peer)
    assert paused['status']=='transition_paused'
    paused=transition.transition(**dict(args,max_requests=5),**FLAGS,transport=peer)
    assert paused['status']=='transition_paused' and paused['stop_reason']=='identity_initialization_budget'
    assert not args['next_state_dir'].exists() and not peer.bodies
    assert transition.transition(**args,**FLAGS,transport=peer)['status']=='transition_complete'
    assert len(peer.bodies)==1
