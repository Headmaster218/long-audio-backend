"""Control/evidence tests with explicit fake producers; no acoustic qualification."""
import ast
import asyncio
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

from fastapi.testclient import TestClient
from audio_backend.app import create_app, _NativeSupportReadLifetime, _assign_native_read_lease
from audio_backend.settings import Settings
from audio_backend.worker import Worker
from audio_backend import native_worker
from audio_backend.native_owned_support_cache import lookup
from test_native_owned_support import synthetic_provenance, synthetic_state, synthetic_turns


def wav_bytes(frames=32000):
    out=io.BytesIO()
    with wave.open(out,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000)
        stream.writeframes(b'\x01\0'*frames)
    return out.getvalue()


class FakeNative:
    """Synthetic observed producer schema with the real Coordinator request flow."""
    instances=[]
    def __init__(self,settings):
        self.calls=[];self.frames=0;self.epoch=None;self.closed=False
        self.__class__.instances.append(self)
    def request(self,action,stop,**kwargs):
        self.calls.append(action)
        if action=='reset':
            self.epoch=kwargs['epoch_id'];self.frames=0
            result={'provenance':synthetic_provenance(), 'state':synthetic_state(self.epoch,0)}
        elif action=='push':
            with wave.open(kwargs['wav'],'rb') as stream:self.frames+=stream.getnframes()
            result=synthetic_state(self.epoch,self.frames,stable_frames=0)
        elif action=='finish':result=synthetic_state(self.epoch,self.frames,finished=True)
        elif action=='turns':result={'turns':synthetic_turns(self.frames,self.epoch)}
        else:raise AssertionError('unexpected synthetic RPC')
        observer=getattr(self,'response_observer',None)
        if observer is not None:observer(result)
        return result
    def close(self):self.closed=True


class FakeAnalyzer:
    def __init__(self,config):self.closed=False
    def analyze(self,path,**kwargs):
        with wave.open(path,'rb') as stream:duration=stream.getnframes()/16000
        return {'segments':[{'start':0.,'end':duration,'text':'explicit fake primary'}],
                'speaker_turns':[],'events':[],'metadata':{}}
    def close(self):self.closed=True


def fake_extract(worker,chunk,start,end,wav,tmp,stop,**kwargs):
    with wave.open(chunk['path'],'rb') as source:
        source.setpos(round(start*16000));pcm=source.readframes(round((end-start)*16000))
    with wave.open(str(wav),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(pcm)
    return None


class BridgeAPI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        FakeNative.instances=[]
        self.settings=Settings(data_dir=self.root,disk_reserve_bytes=0,start_worker=False,
            native_owned_support_enabled=True,diarization_backend='nemotron',
            native_diarization_model='synthetic-model',native_diarization_library='synthetic-library')
        self.app=create_app(self.settings,analyzer_factory=FakeAnalyzer)
        self.client=TestClient(self.app);self.client.__enter__()
        self.sid=self.client.post('/sessions',json={'name':'synthetic'}).json()['id']
    def tearDown(self):
        self.client.__exit__(None,None,None);self.temp.cleanup()
    def upload(self):
        with patch('audio_backend.app.probe',return_value=2.):
            response=self.client.put(f'/sessions/{self.sid}/chunks',content=wav_bytes(),headers={'Idempotency-Key':'one'})
        self.assertEqual(response.status_code,202,response.text)
        return response.json()['chunk']['id']
    def process_one(self):
        worker=self.app.state.worker;original=self.app.state.store.claim;calls=[]
        def one_claim():
            if calls:
                worker.stop_event.set();worker.wake.set();return None
            calls.append(True);return original()
        with patch.object(self.app.state.store,'claim',side_effect=one_claim), \
             patch.object(native_worker,'NativeProcess',FakeNative), \
             patch.object(Worker,'extract_pcm',fake_extract), \
             patch('audio_backend.worker.check_resources'):
            worker.loop()
        self.assertEqual(len(FakeNative.instances),1)
        self.assertEqual(FakeNative.instances[0].calls,['reset','push','finish','turns'])
    def assert_private(self,response):
        self.assertIn('no-store',response.headers['cache-control'])
        self.assertIn('private',response.headers['cache-control'])
    def test_actual_worker_public_selected_support_and_audio(self):
        cid=self.upload();self.process_one()
        self.assertEqual(self.app.state.store.chunk(cid)['status'],'completed')
        primary=self.client.get(f'/sessions/{self.sid}/segments').json()
        page=self.client.get(f'/sessions/{self.sid}/native-owned-supports')
        self.assertEqual(page.status_code,200,page.text);self.assert_private(page)
        payload=page.json();self.assertFalse(payload['lexical_dispatch_available'])
        self.assertEqual(len(payload['supports']),1)
        row=payload['supports'][0];self.assertTrue(row['selected']);self.assertEqual(row['chunk_id'],cid)
        query={'expected_capture_epoch':payload['capture_epoch'],'expected_revision':str(row['revision'])}
        detail=self.client.get(f'/sessions/{self.sid}/native-owned-supports/{cid}',params=query)
        self.assertEqual(detail.status_code,200,detail.text);self.assert_private(detail)
        audio=self.client.get(f'/sessions/{self.sid}/native-owned-supports/{cid}/audio',params=query)
        self.assertEqual(audio.status_code,200,audio.text);self.assert_private(audio)
        self.assertEqual(audio.content,wav_bytes())
        self.assertEqual(primary,self.client.get(f'/sessions/{self.sid}/segments').json())
        self.assertNotIn(str(self.root),json.dumps(payload)+detail.text)
    def test_public_delete_purges_and_blocks_reads(self):
        cid=self.upload();self.process_one()
        response=self.client.delete(f'/sessions/{self.sid}')
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json().get('native_owned_support_cleanup_pending',False))
        missing=self.client.get(f'/sessions/{self.sid}/native-owned-supports/{cid}')
        self.assertEqual(missing.status_code,404);self.assert_private(missing)
    def test_direct_store_delete_is_same_cache_fence(self):
        cid=self.upload();self.process_one()
        result=self.app.state.store.delete_session(self.sid)
        self.assertFalse(result.get('native_owned_support_cleanup_pending',False))
        self.assertEqual(self.client.get(f'/sessions/{self.sid}/native-owned-supports/{cid}').status_code,404)
    def test_off_after_enabled_still_purges(self):
        cid=self.upload();self.process_one();self.settings.native_owned_support_enabled=False
        self.assertEqual(self.client.get(f'/sessions/{self.sid}/native-owned-supports/{cid}').status_code,409)
        deleted=self.app.state.store.delete_session(self.sid)
        self.assertFalse(deleted.get('native_owned_support_cleanup_pending',False))
    def test_restart_epoch_drops_supports(self):
        self.upload();self.process_one()
        old=self.client.get(f'/sessions/{self.sid}/native-owned-supports').json()['capture_epoch']
        self.client.__exit__(None,None,None)
        self.app=create_app(self.settings,analyzer_factory=FakeAnalyzer)
        self.client=TestClient(self.app);self.client.__enter__()
        page=self.client.get(f'/sessions/{self.sid}/native-owned-supports')
        self.assertEqual(page.status_code,200,page.text)
        self.assertNotEqual(page.json()['capture_epoch'],old);self.assertEqual(page.json()['supports'],[])
    def test_stale_epoch_is_private_conflict(self):
        self.upload();self.process_one()
        r=self.client.get(f'/sessions/{self.sid}/native-owned-supports',params={'expected_capture_epoch':'0'*32})
        self.assertEqual(r.status_code,409);self.assert_private(r)
    def test_stale_revision_is_private_conflict(self):
        cid=self.upload();self.process_one()
        r=self.client.get(f'/sessions/{self.sid}/native-owned-supports/{cid}?expected_revision=999')
        self.assertEqual(r.status_code,409);self.assert_private(r)
    def test_unknown_and_duplicate_query_private_rejection(self):
        for query in ('unknown=1','limit=1&limit=2','after=01','limit=true','after=1','x='+'a'*1024):
            with self.subTest(query=query[:40]):
                r=self.client.get(f'/sessions/{self.sid}/native-owned-supports?{query}')
                self.assertIn(r.status_code,(409,422));self.assert_private(r)
    def test_unauthorized_response_is_private(self):
        self.settings.api_key='synthetic-token'
        r=self.client.get(f'/sessions/{self.sid}/native-owned-supports')
        self.assertEqual(r.status_code,401);self.assert_private(r)
    def test_missing_session_is_private(self):
        r=self.client.get('/sessions/'+'f'*32+'/native-owned-supports')
        self.assertEqual(r.status_code,404);self.assert_private(r)
    def test_changed_source_fails_public_gate(self):
        cid=self.upload();self.process_one()
        with self.app.state.store.db() as db:
            path=Path(db.execute('SELECT path FROM chunks WHERE id=?',(cid,)).fetchone()[0])
        path.write_bytes(wav_bytes().replace(b'\x01\0',b'\x02\0'))
        r=self.client.get(f'/sessions/{self.sid}/native-owned-supports/{cid}')
        self.assertEqual(r.status_code,409);self.assert_private(r)
    def test_factory_setup_failure_preserves_original_process(self):
        class Unattachable:
            __slots__=()
        worker=self.app.state.worker
        with patch.object(native_worker,'NativeProcess',return_value=Unattachable()) as factory:
            result=worker._native_process_factory(self.settings)
        self.assertIsInstance(result,Unattachable);factory.assert_called_once_with(self.settings)
    def test_original_factory_exception_is_not_retried(self):
        error=RuntimeError('original constructor error')
        with patch.object(native_worker,'NativeProcess',side_effect=error) as factory:
            with self.assertRaises(RuntimeError) as raised:self.app.state.worker._native_process_factory(self.settings)
        self.assertIs(raised.exception,error);self.assertEqual(factory.call_count,1)
    def test_observer_fault_does_not_change_original_native_return(self):
        class Witness:
            def observe(self,response):raise ValueError('optional injected failure')
            def fail(self,reason):self.reason=reason
        worker=self.app.state.worker;witness=Witness();worker._native_capture=witness
        worker._native_capture_observation=(worker._native_capture_token,witness)
        response={'synthetic':True};worker._native_response(response)
        self.assertEqual(response,{'synthetic':True});self.assertEqual(witness.reason,'response_observation_unavailable')
        worker._native_capture=None
        worker._native_capture_observation=None
    def test_original_extract_error_preserved(self):
        worker=self.app.state.worker;error=OSError('original extraction error')
        with patch.object(worker,'extract_pcm',side_effect=error):
            with self.assertRaises(OSError) as raised:worker._native_extract({},0,1,Path('unused'),Path('.'),lambda:False)
        self.assertIs(raised.exception,error)
    def test_old_prepare_observer_cannot_observe_new_token(self):
        class Witness:
            def __init__(self):self.calls=0
            def observe(self,response):self.calls+=1
        worker=self.app.state.worker;old,new=Witness(),Witness()
        worker._native_capture_observation=(object(),old)
        worker._native_capture_token=object();worker._native_capture=new
        worker._native_response({'synthetic':True})
        self.assertEqual((old.calls,new.calls),(0,0))
        worker._finish_native_support()
    def test_commit_false_never_publishes(self):
        self.upload();cache=self.app.state.worker.native_owned_support_cache
        with patch.object(self.app.state.store,'commit_window',return_value=False),patch.object(cache,'publish') as publish:
            self.process_one();publish.assert_not_called()
    def test_commit_exception_never_publishes(self):
        self.upload();cache=self.app.state.worker.native_owned_support_cache
        with patch.object(self.app.state.store,'commit_window',side_effect=RuntimeError('lost commit return')),patch.object(cache,'publish') as publish:
            self.process_one();publish.assert_not_called()
    def test_default_flag_excluded_from_inference_config(self):
        original=Settings(data_dir=self.root,disk_reserve_bytes=0,start_worker=False)
        enabled=Settings(data_dir=self.root,disk_reserve_bytes=0,start_worker=False,native_owned_support_enabled=True)
        self.assertEqual(original.inference_config(),enabled.inference_config())
    def test_default_off_delete_has_no_optional_key(self):
        with tempfile.TemporaryDirectory() as root:
            settings=Settings(data_dir=Path(root),disk_reserve_bytes=0,start_worker=False)
            with TestClient(create_app(settings)) as client:
                sid=client.post('/sessions',json={}).json()['id']
                self.assertIsNone(lookup(client.app.state.store))
                self.assertNotIn('native_owned_support_cleanup_pending',client.delete(f'/sessions/{sid}').json())
                self.assertIsNone(lookup(client.app.state.store))
    def test_disabled_read_private_without_cache_lookup(self):
        self.settings.native_owned_support_enabled=False
        with patch('audio_backend.native_owned_support_cache.parse_query',side_effect=AssertionError('disabled parsed')):
            r=self.client.get(f'/sessions/{self.sid}/native-owned-supports?bad=1')
        self.assertEqual(r.status_code,409);self.assert_private(r)


class ResponseLifetime(unittest.TestCase):
    def test_late_thread_handoff_after_cancel_releases(self):
        events=[];ready=threading.Event();finish=threading.Event();owned=[]
        class Lease:
            def release(self):events.append('released')
        lease=Lease();scope={'type':'http','path':'/native-owned-supports'}
        def abandoned_endpoint():
            body=b'x'*640000
            owned.append(body);ready.set()
            if not finish.wait(2):raise AssertionError('test synchronization timeout')
            self.assertFalse(_assign_native_read_lease(scope,lease))
            owned.clear()
        thread=threading.Thread(target=abandoned_endpoint)
        async def app(scope,receive,send):
            thread.start()
            while not ready.is_set():await asyncio.sleep(0)
            raise asyncio.CancelledError()
        async def receive():return {'type':'http.disconnect'}
        async def send(message):raise AssertionError('cancelled before send')
        try:
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(_NativeSupportReadLifetime(app)(scope,receive,send))
            self.assertTrue(scope['_native_support_read_done'].is_set())
            self.assertEqual(events,[]);self.assertEqual(len(owned[0]),640000)
        finally:
            finish.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(owned,[])
        self.assertEqual(events,['released']);self.assertNotIn('_native_support_read_lease',scope)
    def test_normal_handoff_remains_charged(self):
        events=[]
        class Lease:
            def release(self):events.append('released')
        lease=Lease();scope={'_native_support_read_done':threading.Event()}
        self.assertTrue(_assign_native_read_lease(scope,lease))
        self.assertIs(scope['_native_support_read_lease'],lease);self.assertEqual(events,[])
    def run_case(self,fail=False,cancel=False):
        events=[]
        class Lease:
            def release(self):events.append('release')
        lease=Lease();scope={'type':'http','path':'/sessions/x/native-owned-supports'}
        async def app(scope,receive,send):
            scope['_native_support_read_lease']=lease
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'bounded'})
        async def send(message):
            events.append(message['type'])
            self.assertNotIn('release',events)
            if message['type']=='http.response.start':
                self.assertIn((b'cache-control',b'private, no-store'),message['headers'])
            if message['type']=='http.response.body':
                if cancel:raise asyncio.CancelledError()
                if fail:raise OSError('send failure')
        async def receive():return {'type':'http.disconnect'}
        async def run():await _NativeSupportReadLifetime(app)(scope,receive,send)
        if cancel:
            with self.assertRaises(asyncio.CancelledError):asyncio.run(run())
        elif fail:
            with self.assertRaises(OSError):asyncio.run(run())
        else:asyncio.run(run())
        self.assertEqual(events[-1],'release');self.assertEqual(events.count('release'),1)
        self.assertNotIn('_native_support_read_lease',scope)
    def test_completion_release(self):self.run_case()
    def test_send_exception_release(self):self.run_case(fail=True)
    def test_disconnect_cancellation_release(self):self.run_case(cancel=True)
    def test_live_send_holds_lease(self):
        events=[]
        class Lease:
            def release(self):events.append('released')
        async def app(scope,receive,send):
            scope['_native_support_read_lease']=Lease()
            await send({'type':'http.response.body','body':b'x'})
        async def receive():return {'type':'http.disconnect'}
        async def send(message):
            await asyncio.sleep(0)
            self.assertEqual(events,[])
            events.append('delivered')
        asyncio.run(_NativeSupportReadLifetime(app)({'type':'http','path':'/native-owned-supports'},receive,send))
        self.assertEqual(events,['delivered','released'])


class BridgeCLI(unittest.TestCase):
    def test_primary_sql_and_inference_arguments_unchanged(self):
        root=Path(__file__).resolve().parents[1]
        pins={'audio_backend/lifecycle.py':'2ee1a32026cc8eac5e18a0cefc5a0d4a95413294a86f616b52b3881b8d72653f',
              'audio_backend/worker.py':'ea4575591f8e1e8644aa759e5eaf4c59bf701a9e8bc3ccfe3925aabb65e5a7ca'}
        for path,pin in pins.items():
            tree=ast.parse((root/path).read_bytes())
            calls=[ast.dump(n,include_attributes=False) for n in ast.walk(tree)
                   if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                   and n.func.attr in ('execute','executescript')]
            self.assertEqual(hashlib.sha256(json.dumps(calls,separators=(',',':')).encode()).hexdigest(),pin)
        for path,name,pin in (
                ('audio_backend/settings.py','inference_config','ef5835122c8d210d270740ad7d5e26b672a7c9b17dbc04130a57903af9da20d1'),
                ('audio_backend/worker.py','extract_pcm','a15b1c69839cdfe43a4109cfb52684ef021e37e86b91aca1676ab2349fd7a579')):
            node=next(n for n in ast.walk(ast.parse((root/path).read_bytes()))
                      if isinstance(n,ast.FunctionDef) and n.name==name)
            self.assertEqual(hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest(),pin)
    def test_fragment_switch_rejected_before_app(self):
        from audio_backend.__main__ import main
        with self.assertRaises(SystemExit) as raised:
            main(['--native-owned-support','--fragment-cpu-check'])
        self.assertEqual(raised.exception.code,2)
    def test_strict_flag_types(self):
        for value in (1,0,'true',None):
            with self.subTest(value=value),self.assertRaises(ValueError):Settings(native_owned_support_enabled=value)
    def test_native_response_close_implementation_unchanged(self):
        source=Path(native_worker.__file__).read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(),'d11e31f44742966ea42ce1407995b22381fd85607f08a6cfc9a4b4f30afe1afb')


if __name__=='__main__':unittest.main()
