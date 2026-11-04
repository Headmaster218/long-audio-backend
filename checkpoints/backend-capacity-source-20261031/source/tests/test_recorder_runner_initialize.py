"""One explicit creation, durable uncertainty, real loopback HTTP; no native inference."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest
from audio_backend import recorder_runner_initialize as init
from audio_backend import recorder_inbox_runner as runner
from audio_backend import recording_uploader as u
from test_recorder_inbox_runner import example,invoke
from test_extended_recorder_intake import Peer


class CreationPeer(Peer):
    def __init__(self):
        super().__init__();self.calls=[];self.name=None;self.uncertain=False;self.compatible=True
    def request(self,method,path,body=None,**kwargs):
        self.calls.append((method,path))
        if path=='/health':return {'status':'ok'}
        if path=='/sessions':
            assert method=='POST' and self.name is None
            self.name=body['name']
            if self.uncertain:raise u.Uncertain('response lost')
            return {'id':'known','name':self.name,'processing_mode':'legacy'}
        if path=='/sessions/known':return {'id':'known','name':self.name,'processing_mode':'legacy'}
        value=super().request(method,path,body=body,**kwargs)
        if path.endswith('/recording-contract') and not self.compatible:value['strict_primary_enabled']=False
        return value


def options(tmp_path):
    paths,config,state=example(tmp_path,1)
    args=dict(state_dir=state,first_delivery=paths[0],inbox_dir=config['inbox_dir'],job_dir=config['job_dir'],
        output_dir=config['output_dir'],api=config['api'],capture_id=config['capture_id'],
        exclusive_writer=True,acknowledge_span_local_closure=True,session_name='private test label')
    return args,paths,config,state


def test_initialize_one_session_then_existing_runner(tmp_path):
    args,paths,config,state=options(tmp_path);peer=CreationPeer()
    result=init.initialize(**args,transport=peer)
    assert result['status']=='configured' and result['network_writes']==1 and result['network_requests']==6
    assert [x for x in peer.calls if x[0]!='GET']==[('POST','/sessions')]
    assert not Path(config['job_dir']).exists()
    assert json.loads((state/'config.json').read_text())['session_id']=='known'
    init.validate_completed(state,json.loads((state/'config.json').read_text()))
    before=len(peer.calls);assert init.initialize(**args,resume=True,transport=peer)['network_requests']==5
    assert all(method=='GET' for method,path in peer.calls[before:])
    assert invoke(state,peer)['status']=='action_limit'


def test_uncertain_post_never_retried(tmp_path):
    args,_,_,state=options(tmp_path);peer=CreationPeer();peer.uncertain=True
    with pytest.raises(u.Uncertain):init.initialize(**args,transport=peer)
    assert (state/'initialization/post.intent.json').exists()
    assert not (state/'initialization/created.json').exists()
    before=list(peer.calls)
    with pytest.raises(init.single.Refused,match='uncertain_no_retry'):
        init.initialize(**args,resume=True,transport=peer)
    assert peer.calls==before and not (state/'config.json').exists()


def test_confirmed_session_resume_get_only_after_contract_refusal(tmp_path):
    args,_,_,state=options(tmp_path);peer=CreationPeer();peer.compatible=False
    with pytest.raises(init.single.Refused,match='strict_contract'):init.initialize(**args,transport=peer)
    assert (state/'initialization/created.json').exists() and not (state/'config.json').exists()
    peer.compatible=True;before=len(peer.calls)
    assert init.initialize(**args,resume=True,transport=peer)['network_writes']==0
    assert all(method=='GET' for method,path in peer.calls[before:])


def test_local_failure_precedes_network(tmp_path):
    args,_,_,state=options(tmp_path);peer=CreationPeer();args['job_dir']=args['inbox_dir']
    with pytest.raises(init.single.Refused):init.initialize(**args,transport=peer)
    assert peer.calls==[] and not state.exists()


def test_changed_intent_never_resumes(tmp_path):
    args,_,_,state=options(tmp_path);peer=CreationPeer();peer.compatible=False
    with pytest.raises(init.single.Refused):init.initialize(**args,transport=peer)
    before=list(peer.calls);args['session_name']='changed'
    with pytest.raises(init.single.Refused,match='control_changed'):
        init.initialize(**args,resume=True,transport=peer)
    assert peer.calls==before


def test_active_initializer_excludes_concurrent_resume(tmp_path):
    args,_,_,state=options(tmp_path);peer=CreationPeer();entered=threading.Event();release=threading.Event()
    original=peer.request
    def block(method,path,**kwargs):
        if path=='/health':entered.set();assert release.wait(5)
        return original(method,path,**kwargs)
    peer.request=block
    with ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(init.initialize,**args,transport=peer)
        try:
            assert entered.wait(3)
            with pytest.raises(BlockingIOError):init.initialize(**args,resume=True,transport=peer)
            assert not any(method=='POST' for method,path in peer.calls)
        finally:release.set()
        assert future.result(timeout=5)['network_writes']==1


def test_corrupt_bootstrap_receipt_blocks_existing_runner(tmp_path):
    args,_,_,state=options(tmp_path);peer=CreationPeer()
    init.initialize(**args,transport=peer)
    (state/'initialization/complete.json').write_bytes(b'{}')
    before=list(peer.calls)
    with pytest.raises(init.single.Refused):invoke(state,peer)
    assert peer.calls==before


@pytest.mark.parametrize('drop_response',[False,True])
def test_real_loopback_http_and_credentials_not_saved(tmp_path,monkeypatch,drop_response):
    args,_,_,state=options(tmp_path);peer=CreationPeer();auth=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):self.route('GET')
        def do_POST(self):self.route('POST')
        def route(self,method):
            auth.append(self.headers.get('Authorization'))
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')))) if method=='POST' else None
            try:value=peer.request(method,self.path,body=body);status=200
            except u.HTTPFailure as error:value={'detail':'missing'};status=error.status
            if method=='POST' and drop_response:
                self.close_connection=True;return
            raw=json.dumps(value).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    secret='test-only-key-not-a-real-credential';monkeypatch.setenv('AUDIO_API_KEY',secret)
    args['api']='http://127.0.0.1:'+str(server.server_port)
    try:
        if drop_response:
            with pytest.raises(u.Uncertain):init.initialize(**args)
            before=list(auth)
            with pytest.raises(init.single.Refused,match='uncertain_no_retry'):init.initialize(**args,resume=True)
            assert auth==before==['Bearer '+secret]*2
            result={}
        else:
            result=init.initialize(**args)
            assert result['network_requests']==6 and result['network_writes']==1
            assert auth==['Bearer '+secret]*6
    finally:server.shutdown();server.server_close();thread.join(3)
    assert all(secret.encode() not in p.read_bytes() for p in state.rglob('*') if p.is_file())
    assert args['session_name'] not in json.dumps(result)


def test_resume_finishes_partial_local_binding_without_post(tmp_path,monkeypatch):
    args,_,_,state=options(tmp_path);peer=CreationPeer();original=init.single.manifest_write
    def fail(path,value):
        if path.name=='input-000.intent.json':raise OSError('simulated local write failure')
        return original(path,value)
    with monkeypatch.context() as patch:
        patch.setattr(init.single,'manifest_write',fail)
        with pytest.raises(OSError):init.initialize(**args,transport=peer)
    assert (state/'config.json').exists()
    before=len(peer.calls)
    assert init.initialize(**args,resume=True,transport=peer)['status']=='configured'
    assert all(method=='GET' for method,path in peer.calls[before:])


def test_not_ready_server_has_no_creation_attempt(tmp_path):
    args,_,_,state=options(tmp_path);peer=CreationPeer();original=peer.request
    peer.request=lambda method,path,**kw: {'status':'blocked'} if path=='/health' else original(method,path,**kw)
    with pytest.raises(init.single.Refused,match='server_not_ready'):init.initialize(**args,transport=peer)
    assert not (state/'initialization/post.intent.json').exists()
    peer.request=original
    assert init.initialize(**args,resume=True,transport=peer)['network_writes']==1


def test_cli_failure_never_echoes_secret_or_private_name(tmp_path,monkeypatch,capsys):
    args,_,_,_=options(tmp_path)
    def fail(**kwargs):raise u.HTTPFailure(500,'secret-key private-session-label')
    monkeypatch.setattr(init,'initialize',fail)
    command=['initialize']
    for key in ('state_dir','inbox_dir','job_dir','first_delivery','api','capture_id'):
        command+=['--'+key.replace('_','-'),str(args[key])]
    command+=['--exclusive-writer','--acknowledge-span-local-closure','--session-name','private-session-label']
    assert runner.main(command)==2
    captured=capsys.readouterr()
    assert 'secret-key' not in captured.err and 'private-session-label' not in captured.err


def test_completed_binding_resume_rechecks_empty_session(tmp_path):
    args,_,_,state=options(tmp_path);peer=CreationPeer()
    init.initialize(**args,transport=peer);before=len(peer.calls)
    original=peer.request
    def changed(method,path,**kwargs):
        value=original(method,path,**kwargs)
        if path.endswith('/chunks?limit=1'):value['chunks']=[{'id':'foreign'}]
        return value
    peer.request=changed
    with pytest.raises(init.single.Refused,match='not_empty'):init.initialize(**args,resume=True,transport=peer)
    assert all(method=='GET' for method,path in peer.calls[before:])


@pytest.mark.parametrize('damage',['missing_post','missing_created','missing_both','missing_config','changed_config','changed_first','changed_created'])
def test_contradictory_partial_binding_refused_before_any_http(tmp_path,damage):
    args,_,_,state=options(tmp_path);peer=CreationPeer()
    init.initialize(**args,transport=peer)
    (state/'initialization/complete.json').unlink()
    if damage=='missing_post':(state/'initialization/post.intent.json').unlink()
    elif damage=='missing_created':(state/'initialization/created.json').unlink()
    elif damage=='missing_both':
        (state/'initialization/post.intent.json').unlink();(state/'initialization/created.json').unlink()
    elif damage=='missing_config':(state/'config.json').unlink()
    else:
        name={'changed_config':'config.json','changed_first':'input-000.intent.json',
              'changed_created':'initialization/created.json'}[damage]
        path=state/name;value=json.loads(path.read_text())
        if damage=='changed_first':value['config_sha256']='0'*64
        else:value['session_id']='different'
        path.write_bytes(init.single._json(value))
    before=list(peer.calls)
    with pytest.raises(init.single.Refused):init.initialize(**args,resume=True,transport=peer)
    assert peer.calls==before
