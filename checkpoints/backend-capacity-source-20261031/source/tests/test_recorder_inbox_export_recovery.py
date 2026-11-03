"""Runner policy/receipt controls; stubs are not real transcript qualification."""
import json
from pathlib import Path
import pytest
from audio_backend import recorder_inbox_runner as runner
from audio_backend import recording_uploader as u
from test_recorder_inbox_runner import example, invoke, fake_export, Peer
from test_recorder_inbox_watch import watching


def ready(tmp_path, monkeypatch):
    paths, config, root = example(tmp_path, 1)
    runner.configure(state_dir=root, config=config, first_delivery=paths[0])
    peer=Peer(); invoke(root,peer,max_actions=2,finalize=True)
    monkeypatch.setattr(runner.status,'run',lambda **kw:dict(status='completed',analysis_completion_verified=True))
    return config,root,peer


def fake_recovery(*,output_dir,recovery_limits,job_dir,session_id,capture_id,**kwargs):
    """Exact bounded recovery layout, no inference/transcript claim."""
    root=Path(output_dir);root.mkdir(mode=0o700)
    first=root/'attempt-01';first.mkdir(mode=0o700)
    for name in ('export.json.partial','transcript.txt.partial'):
        path=first/name;path.write_bytes(b'preserved partial');path.chmod(0o600)
    second=root/'attempt-02';manifest=fake_export(output_dir=second)
    manifest['transcript_revision']='a'*64
    (second/'complete.json').write_bytes(runner.single._json(manifest))
    limits=recovery_limits
    rows=[dict(schema=runner.export_recovery.SCHEMA,job_dir=job_dir,session_id=session_id,capture_id=capture_id,
        remote_read_only=True,inference_replay=False,attempts=limits.attempts,requests_bound=limits.requests,
        reserved_network_bound=limits.network_bytes,output_bound=limits.output_bytes,audit_output_bound=16384),
        dict(attempt=1,output='attempt-01',status='started'),
        dict(attempt=1,status='revision_changed',partial_preserved=True,requests=2,reserved_network_bytes=2*u.JSON_BYTES),
        dict(attempt=2,output='attempt-02',status='started'),
        dict(attempt=2,status='exported',manifest='attempt-02/complete.json',transcript_revision='a'*64,
             requests=4,reserved_network_bytes=4*u.JSON_BYTES)]
    path=root/'recovery.jsonl';path.write_text(''.join(json.dumps(row)+'\n' for row in rows));path.chmod(0o600)
    return dict(status='exported',attempts=2)


@pytest.mark.parametrize('watch',[False,True])
def test_optin_is_durable_receipted_and_replays_without_flag_or_http(tmp_path,monkeypatch,watch):
    config,root,peer=ready(tmp_path,monkeypatch)
    monkeypatch.setattr(runner.export_recovery,'run',fake_recovery)
    result=(watching if watch else invoke)(root,peer,export=True,export_recovery_attempts=2,max_actions=3)
    assert result['status']=='exported' and result['export_attempts']==2 and result['revision_conflicts']==1
    assert json.loads((root/'export.intent.json').read_bytes())['recovery_attempts']==2
    receipt=json.loads((root/'export.done.json').read_bytes())
    assert receipt['selected_attempt']=='attempt-02' and len(receipt['recovery_tree_sha256'])==64
    class NoHTTP:
        def request(self,*a,**kw): pytest.fail('receipt replay HTTP')
    repeated=(watching if watch else invoke)(root,NoHTTP())
    assert repeated['status']=='exported' and repeated['revision_conflicts']==1


@pytest.mark.parametrize('part',['partial','audit','success','extra'])
def test_replay_detects_any_preserved_tree_tamper(tmp_path,monkeypatch,part):
    config,root,peer=ready(tmp_path,monkeypatch);monkeypatch.setattr(runner.export_recovery,'run',fake_recovery)
    invoke(root,peer,export=True,export_recovery_attempts=2)
    output=Path(config['output_dir'])
    path={'partial':output/'attempt-01/export.json.partial','audit':output/'recovery.jsonl',
          'success':output/'attempt-02/transcript.txt','extra':output/'unexpected'}[part]
    with path.open('ab') as stream:stream.write(b'changed')
    with pytest.raises((runner.Refused,ValueError)):invoke(root,peer)


@pytest.mark.parametrize('stage',['partial','committed','before_output'])
def test_interrupted_optin_never_adopts_or_reinterprets_existing_output(tmp_path,monkeypatch,stage):
    config,root,peer=ready(tmp_path,monkeypatch)
    class Crash(BaseException):pass
    def crash(**kwargs):
        if stage=='committed':fake_recovery(**kwargs)
        elif stage=='partial':Path(kwargs['output_dir']).mkdir(mode=0o700)
        raise Crash()
    monkeypatch.setattr(runner.export_recovery,'run',crash)
    with pytest.raises(Crash):invoke(root,peer,export=True,export_recovery_attempts=2)
    assert not (root/'export.done.json').exists()
    monkeypatch.setattr(runner.export_recovery,'run',lambda **kw:pytest.fail('must not retry ambiguous export'))
    if stage=='before_output':
        with pytest.raises(runner.Refused,match='control_changed'):invoke(root,peer,export=True)
    else:
        with pytest.raises(runner.Refused,match='publication_uncertain'):
            invoke(root,peer,export=True,export_recovery_attempts=2)


@pytest.mark.parametrize('value',[-1,1,4,True,2.0,'2'])
def test_invalid_attempts_fail_without_effect(tmp_path,monkeypatch,value):
    config,root,peer=ready(tmp_path,monkeypatch)
    with pytest.raises(runner.Refused,match='recovery_bound'):
        invoke(root,peer,export=True,export_recovery_attempts=value)
    assert not (root/'export.intent.json').exists()


def test_optin_requires_export_authorization(tmp_path,monkeypatch):
    config,root,peer=ready(tmp_path,monkeypatch)
    with pytest.raises(runner.Refused,match='requires_export'):invoke(root,peer,export_recovery_attempts=2)


@pytest.mark.parametrize('phase,reason,path,allowed',[
    (True,'transcript_revision_changed','/sessions/s/transcript?cursor=x&projection=whole-primary-v1',True),
    (False,'transcript_revision_changed','/sessions/s/transcript?cursor=x&projection=whole-primary-v1',False),
    (True,'unknown','/sessions/s/transcript?cursor=x&projection=whole-primary-v1',False),
    (True,'transcript_revision_changed','/sessions/s/recordings/c',False),
    (True,'transcript_revision_changed','/sessions/s/transcript?block=0&projection=whole-primary-v1',False),
])
def test_scoped_watch_passthrough_and_failed_response_budget(phase,reason,path,allowed):
    class Peer:
        def request(self,*a,**kw):raise u.HTTPFailure(409,json.dumps({'detail':reason}))
    http=runner._Transport(Peer(),u.Guard(u.Limits(),None),dict(session_id='s',capture_id='c'),10,1,
        lambda writes:None,watch=True,max_bytes=2*u.JSON_BYTES)
    http.export_recovery_phase=phase
    with pytest.raises(u.HTTPFailure if allowed else runner._WatchTransportStopped):http.request('GET',path)
    assert http.requests==1 and http.bytes==(u.JSON_BYTES if phase else 0)
    if phase:
        with pytest.raises(u.HTTPFailure if allowed else runner._WatchTransportStopped):http.request('GET',path)
        with pytest.raises(runner.Refused,match='byte_bound'):http.request('GET',path)
        assert http.requests==2


@pytest.mark.parametrize('event',['cancel','deadline'])
def test_nonwatch_recovery_shares_outer_stop_guard(tmp_path,monkeypatch,event):
    config,root,peer=ready(tmp_path,monkeypatch);cancelled=False
    real=runner.time.monotonic
    def check(**kwargs):
        nonlocal cancelled
        if event=='cancel':cancelled=True
        else:monkeypatch.setattr(runner.time,'monotonic',lambda:real()+1000)
        kwargs['cancel']()
        pytest.fail('outer guard did not stop')
    monkeypatch.setattr(runner.export_recovery,'run',check)
    with pytest.raises((InterruptedError,u.Incomplete)):
        invoke(root,peer,export=True,export_recovery_attempts=2,cancel=lambda:cancelled)
    assert not (root/'export.done.json').exists()


def test_recovery_phase_independently_forbids_write_and_shares_request_cap():
    class Peer:
        def request(self,*a,**kw):raise u.HTTPFailure(409,'{"detail":"transcript_revision_changed"}')
    http=runner._Transport(Peer(),u.Guard(u.Limits(),None),dict(session_id='s',capture_id='c'),1,1,
        lambda writes:None,watch=True)
    http.export_recovery_phase=True
    with pytest.raises(runner.Refused,match='get_only'):http.request('POST','/sessions/s/recordings/c/finalize')
    path='/sessions/s/transcript?cursor=x&projection=whole-primary-v1'
    with pytest.raises(u.HTTPFailure):http.request('GET',path)
    with pytest.raises(runner.Refused,match='request_bound'):http.request('GET',path)
    assert http.requests==1 and http.writes==0
