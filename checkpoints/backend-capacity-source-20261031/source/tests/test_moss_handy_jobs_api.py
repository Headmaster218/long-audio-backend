"""Real ASGI/Worker/cache seams with explicit synthetic producers; no native call."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request
from audio_backend import app as app_module
from audio_backend.app import create_app, _NativeSupportReadLifetime, _assign_native_read_lease
from audio_backend.settings import Settings
from audio_backend.worker import Worker
from audio_backend import native_worker
from audio_backend import native_execution_permit as permit_module
from audio_backend import moss_handy_http as http
from audio_backend import moss_handy_jobs as jobs
from audio_backend.fragment_cpu_provider import CPUFactory
from audio_backend.fragment_http import FragmentHTTP
from audio_backend.fragment_runtime_contracts import RuntimePolicy
from audio_backend.native_owned_support_cache import ReadError, lookup
from test_native_owned_support_api import FakeAnalyzer, FakeNative, fake_extract, wav_bytes
from test_native_execution_permit import Handle, sample
from test_moss_handy_controller import FakeDriver
from test_moss_handy_jobs import ManualThread, RetainedTemporary, retain_outcome


class NativeHandleFixture(FakeNative):
    def __init__(self,settings):
        super().__init__(settings)
        self.proc=Handle();self.busy=False
    def close(self):
        super().close();self.proc.returncode=0


class JobsAPI(unittest.TestCase):
    def setUp(self):
        self.temp=RetainedTemporary();self.root=Path(self.temp.name)
        self.settings=Settings(data_dir=self.root,disk_reserve_bytes=0,start_worker=True,
            native_owned_support_enabled=True,diarization_backend='nemotron',
            native_diarization_model='synthetic-model',native_diarization_library='synthetic-library',
            moss_jobs_enabled=True,moss_jobs_model_path='/synthetic-model.fixture')
        self.start_patch=patch.object(Worker,'start',lambda _:None);self.start_patch.start()
        self.app=create_app(self.settings,analyzer_factory=FakeAnalyzer)
        self.client=TestClient(self.app);self.client.__enter__()
        self.owner=self.app.state.moss_jobs
        self.owner.permit._sampler=lambda _:sample()
        self.owner._thread_factory=ManualThread
        self.drivers=[]
        def factory(directory,sha):
            driver=FakeDriver(directory,sha);self.drivers.append(driver);return driver
        self.owner._driver_factory=factory
        self.sid=self.client.post('/sessions',json={'name':'synthetic'}).json()['id']
    def tearDown(self):
        retain_outcome(self.root,self.owner)
        record=self.owner._live
        self.owner.close_admission()
        if record and record['thread'] and record['thread'].is_alive():record['thread'].run()
        self.client.__exit__(None,None,None);self.start_patch.stop();self.temp.cleanup()
    def support(self):
        worker=self.app.state.worker;store=self.app.state.store
        with patch('audio_backend.app.probe',return_value=2.):
            response=self.client.put(f'/sessions/{self.sid}/chunks',content=wav_bytes(),headers={'Idempotency-Key':'one'})
        self.assertEqual(response.status_code,202,response.text);cid=response.json()['chunk']['id']
        original=store.claim;calls=[]
        def claim():
            if calls:worker.stop_event.set();return None
            calls.append(True);return original()
        self.owner.permit._sampler=lambda _:sample(extra={901:(2.,os.getpid(),1024)})
        with patch.object(store,'claim',side_effect=claim), \
             patch.object(native_worker,'NativeProcess',NativeHandleFixture), \
             patch.object(Worker,'extract_pcm',fake_extract), \
             patch('audio_backend.worker.check_resources'), \
             patch.object(permit_module.psutil,'Process',return_value=SimpleNamespace(create_time=lambda:2.)):
            worker.loop()
        self.assertEqual(store.chunk(cid)['status'],'completed')
        page=self.client.get(f'/sessions/{self.sid}/native-owned-supports').json()
        row=page['supports'][0]
        self.assertTrue(row['lexical_dispatch_available']);self.assertTrue(page['lexical_dispatch_available'])
        body={'jobs_epoch':self.owner.jobs_epoch,'expected_capture_epoch':page['capture_epoch'],
            'expected_revision':row['revision'],'support_id':row['support_id'],'profile':jobs.NAME}
        return cid,body
    def submit(self,cid,body,key='one'):
        return self.client.post(f'/sessions/{self.sid}/native-owned-supports/{cid}/moss-jobs',json=body,
                                headers={'Idempotency-Key':key})
    def finish(self):
        record=self.owner._live
        record['thread'].run()
        return record['job_id']
    def private(self,response):
        self.assertIn('private',response.headers['cache-control']);self.assertIn('no-store',response.headers['cache-control'])

    def test_real_worker_support_submit_all_reads_and_cancel_preserve_primary(self):
        cid,body=self.support();primary=self.client.get(f'/sessions/{self.sid}/segments').json()
        response=self.submit(cid,body);self.assertEqual(response.status_code,202,response.text);self.private(response)
        jid=self.finish();prefix=f'/sessions/{self.sid}/moss-jobs'
        for suffix in ('','/'+jid,'/'+jid+'/result','/'+jid+'/raw'):
            response=self.client.get(prefix+suffix);self.assertEqual(response.status_code,200,response.text);self.private(response)
        raw=self.client.get(prefix+'/'+jid+'/raw')
        self.assertEqual(raw.content,b'[0][S1]synthetic[1]')
        self.assertEqual(int(raw.headers['content-length']),len(raw.content))
        self.assertEqual(raw.headers['x-content-sha256'],hashlib.sha256(raw.content).hexdigest())
        self.assertEqual(raw.headers['content-type'],'text/plain; charset=utf-8')
        self.assertEqual(primary,self.client.get(f'/sessions/{self.sid}/segments').json())
        cancel=self.client.post(prefix+'/'+jid+'/cancel',json={'jobs_epoch':self.owner.jobs_epoch})
        self.assertEqual(cancel.status_code,200,cancel.text)
        self.assertEqual(self.client.get(prefix+'/'+jid+'/raw').status_code,409)
        self.assertEqual(self.owner.cache._lease_count,0)

    def test_raw_empty_and_unicode_whitespace_are_exact_bytes(self):
        cid,body=self.support()
        for index,raw in enumerate((b'', ' \n[0][S1]合成[1]\t'.encode())):
            response=self.submit(cid,body,str(index));self.assertEqual(response.status_code,202,response.text)
            original=self.owner._live['controller']._factory
            def factory(directory,sha,value=raw):
                driver=original(directory,sha);driver.raw=value;return driver
            self.owner._live['controller']._factory=factory
            jid=self.finish();response=self.client.get(f'/sessions/{self.sid}/moss-jobs/{jid}/raw')
            self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.content,raw)
            self.assertEqual(response.headers['content-length'],str(len(raw)))

    def test_idempotency_epoch_and_unknown_session_are_private(self):
        cid,body=self.support();first=self.submit(cid,body)
        again=self.submit(cid,body);self.assertEqual(again.status_code,200);self.assertEqual(first.json()['job_id'],again.json()['job_id'])
        conflict=self.submit(cid,{**body,'expected_revision':999});self.assertEqual(conflict.status_code,409)
        wrong=self.submit(cid,{**body,'jobs_epoch':'0'*32},'other');self.assertEqual(wrong.status_code,409)
        jid=first.json()['job_id']
        missing=self.client.get(f'/sessions/unknown/moss-jobs/{jid}')
        self.assertEqual(missing.status_code,404);self.private(missing)

    def test_auth_unknown_route_method_and_malformed_inputs_always_private(self):
        self.settings.api_key='synthetic-token'
        response=self.client.get(f'/sessions/{self.sid}/moss-jobs');self.assertEqual(response.status_code,401);self.private(response)
        self.settings.api_key=None
        for method,path in (('get',f'/sessions/{self.sid}/moss-jobs/invalid/raw'),
                            ('put',f'/sessions/{self.sid}/moss-jobs'),
                            ('get',f'/sessions/{self.sid}/moss-jobs/unknown/extra')):
            response=getattr(self.client,method)(path);self.private(response)
            self.assertIn(response.status_code,(404,405,409))
        for query in ('after=01','limit=21','after=1','limit=1&limit=2','unknown=1','limit=%31','limit=-1'):
            response=self.client.get(f'/sessions/{self.sid}/moss-jobs?{query}')
            self.assertEqual(response.status_code,409);self.private(response)

    def test_strict_body_and_headers_never_reach_manager(self):
        cid,body=self.support();path=f'/sessions/{self.sid}/native-owned-supports/{cid}/moss-jobs'
        variants=[{**body,'model':'/private'}, {**body,'expected_revision':True}, {**body,'profile':'other'},
                  {**body,'support_id':[]}, {**body,'jobs_epoch':'x'}, {**body,'prompt':'x'}]
        with patch.object(self.owner,'submit',side_effect=AssertionError('must not admit')):
            for candidate in variants:
                response=self.client.post(path,json=candidate,headers={'Idempotency-Key':'key'})
                self.assertEqual(response.status_code,409,response.text);self.private(response)
            for raw,headers in ((b'{}',{'Content-Type':'text/plain'}),
                                (b'{}',{'Content-Type':'application/json','Content-Encoding':'gzip'}),
                                (b'{"jobs_epoch":"x","jobs_epoch":"x"}',{'Content-Type':'application/json'}),
                                (b'x'*1025,{'Content-Type':'application/json'})):
                response=self.client.post(path,content=raw,headers=headers)
                self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.owner._records,[])

    def test_shared_read_pool_rejects_mutation_before_acceptance(self):
        cid,body=self.support();cache=self.owner.cache
        audio=cache.read_audio(self.sid,cid);result=cache.reserve_jobs_response('result')
        response=self.submit(cid,body)
        self.assertEqual(response.status_code,503);self.assertEqual(self.owner._records,[])
        audio.lease.release();result.release();self.assertEqual(cache._lease_count,0)

    def test_direct_alias_store_deletion_cancels_before_publication(self):
        from audio_backend.storage import Store
        cid,body=self.support();reply=self.submit(cid,body);self.assertEqual(reply.status_code,202,reply.text)
        alias=Store(self.app.state.store.path,self.settings)
        result=alias.delete_session(self.sid)
        self.assertTrue(result['moss_jobs_cleanup_pending'])
        record=self.owner._live;self.assertTrue(record['cancelled'].is_set())
        self.finish();self.assertFalse(self.drivers)
        self.assertEqual(self.client.get(f'/sessions/{self.sid}/moss-jobs/{record["job_id"]}/raw').status_code,404)

    def test_factory_mutation_after_start_disables_optional_admission(self):
        cid,body=self.support();self.app.state.fragments.factory=object()
        response=self.submit(cid,body);self.assertEqual(response.status_code,409,response.text)
        self.assertFalse(self.drivers);self.assertFalse(self.owner._records)
        self.app.state.fragments.factory=None

    def test_new_primary_intent_cancels_and_waits_for_manager_cleanup(self):
        cid,body=self.support();self.submit(cid,body)
        intent=self.owner.permit.primary_intent('probe')
        self.assertTrue(self.owner._live['cancelled'].is_set())
        self.finish();intent.wait();intent.release()
        self.assertFalse(self.drivers);self.assertIsNone(self.owner.permit._optional)

    def test_off_response_and_no_optional_namespace_creation(self):
        with RetainedTemporary() as directory:
            config=Settings(data_dir=directory,start_worker=False,disk_reserve_bytes=0)
            app=create_app(config,analyzer_factory=FakeAnalyzer)
            with TestClient(app) as client:
                response=client.get('/sessions/fixture/moss-jobs')
                self.assertEqual(response.status_code,409);self.private(response)
                self.assertIsNone(app.state.moss_jobs)
            self.assertFalse((Path(directory)/jobs.NAMESPACE).exists())


    def test_history_and_source_reads_use_the_exact_same_pool(self):
        cid, body = self.support(); self.submit(cid, body); jid = self.finish()
        pool = self.app.state.moss_response_budget
        self.assertIs(pool, self.owner.cache.response_budget)
        from audio_backend.storage import Store
        from audio_backend.native_owned_support_cache import lookup
        alias = Store(self.app.state.store.path, self.settings)
        registered = lookup(alias)
        self.assertIs(registered, self.owner.cache)
        self.assertIs(registered.response_budget, pool)
        source = self.owner.cache.read_audio(self.sid, cid)
        history = pool.reserve_jobs_response('result')
        try:
            response = self.client.get(f'/sessions/{self.sid}/moss-jobs/history/{jid}/result')
            self.assertEqual(response.status_code, 503); self.private(response)
            self.assertEqual(pool._lease_count, 2)
        finally:source.lease.release(); history.release()
        self.assertEqual(pool._lease_count, 0)

    def test_retry_storage_parser_epoch_one_attempt_and_no_native_replay(self):
        cid, body = self.support(); self.submit(cid, body)
        with patch.object(self.owner.results, 'commit', side_effect=ReadError(503, 'result_storage_unavailable')):
            jid = self.finish()
        path = f'/sessions/{self.sid}/moss-jobs/{jid}/retry-storage'
        with patch.object(self.owner, 'retry_storage', side_effect=AssertionError('must not reach retry')):
            for invalid in ({}, {'jobs_epoch': False}, {'jobs_epoch': self.owner.jobs_epoch, 'extra': 1}):
                response = self.client.post(path, json=invalid)
                self.assertEqual(response.status_code, 409); self.private(response)
        wrong = self.client.post(path, json={'jobs_epoch': 'f' * 32})
        self.assertEqual(wrong.status_code, 409); self.private(wrong)
        with patch.object(self.owner.results, 'commit', side_effect=ReadError(503, 'result_storage_unavailable')) as commit:
            response = self.client.post(path, json={'jobs_epoch': self.owner.jobs_epoch})
        self.assertEqual(response.status_code, 503); self.assertEqual(commit.call_count, 1); self.private(response)
        self.settings.moss_jobs_enabled = False
        with patch.object(self.owner, '_validate', side_effect=AssertionError('no source lookup')):
            response = self.client.post(path, json={'jobs_epoch': self.owner.jobs_epoch})
        self.assertEqual(response.status_code, 200, response.text); self.private(response)
        self.assertTrue(response.json()['durable_result_available'])
        self.assertEqual(sum(driver.starts for driver in self.drivers), 1)
        self.assertEqual(self.owner.cache._lease_count, 0)

    def test_delete_after_pending_save_prevents_retry_resurrection(self):
        cid, body = self.support(); self.submit(cid, body)
        with patch.object(self.owner.results, 'commit', side_effect=ReadError(503, 'result_storage_unavailable')):
            jid = self.finish()
        response = self.client.delete('/sessions/' + self.sid)
        self.assertEqual(response.status_code, 200, response.text)
        retry = self.client.post(f'/sessions/{self.sid}/moss-jobs/{jid}/retry-storage',
                                 json={'jobs_epoch': self.owner.jobs_epoch})
        self.assertEqual(retry.status_code, 404); self.private(retry)
        self.assertEqual(self.client.get(f'/sessions/{self.sid}/moss-jobs/history/{jid}/result').status_code, 404)
        self.assertIsNone(self.owner._records[0]['history_snapshot'])


class StartupFenceTests(unittest.TestCase):
    def settings(self,root):return Settings(data_dir=root,start_worker=False,disk_reserve_bytes=0)
    def factory(self):
        calls=[]
        factory=SimpleNamespace(policy=RuntimePolicy(),initialize=lambda store:calls.append('initialize'))
        return factory,calls
    def test_factory_hook_deferred_until_lock_and_once_with_direct_default_preserved(self):
        with RetainedTemporary() as directory:
            factory,calls=self.factory();app=create_app(self.settings(directory),fragment_provider_factory=factory)
            self.assertEqual(calls,[]);fragments=app.state.fragments
            original=app.state.instance_lock.acquire
            def acquire():calls.append('lock');original()
            with patch.object(app.state.instance_lock,'acquire',side_effect=acquire):
                with TestClient(app):self.assertEqual(calls,['lock','initialize'])
            fragments.initialize_factory_once();self.assertEqual(calls,['lock','initialize'])
            other,immediate=self.factory();FragmentHTTP(app.state.store,app.state.settings,other)
            self.assertEqual(immediate,['initialize'])
    def test_lock_failure_and_late_marker_block_initializer_and_worker(self):
        for failure in ('lock','marker'):
            with self.subTest(failure=failure),RetainedTemporary() as directory:
                factory,calls=self.factory();app=create_app(self.settings(directory),fragment_provider_factory=factory)
                if failure=='marker':
                    slot=jobs.create_namespace(Path(directory));(slot/'owner.json').write_bytes(b'{}')
                    context=patch.object(Worker,'start',side_effect=AssertionError('must not start'))
                else:context=patch.object(app.state.instance_lock,'acquire',side_effect=RuntimeError('lock fixture'))
                with context,self.assertRaises(RuntimeError):
                    with TestClient(app):pass
                self.assertEqual(calls,[])
    def test_unresolved_marker_at_construction_and_enabled_factory_conflict(self):
        with RetainedTemporary() as directory:
            root=Path(directory);slot=jobs.create_namespace(root);(slot/'owner.json').write_bytes(b'{}')
            factory,calls=self.factory()
            with self.assertRaises(RuntimeError):create_app(self.settings(root),fragment_provider_factory=factory)
            self.assertEqual(calls,[])
        with RetainedTemporary() as directory:
            config=Settings(data_dir=directory,moss_jobs_enabled=True,moss_jobs_model_path='/synthetic-model.fixture',
                native_owned_support_enabled=True,diarization_backend='nemotron',native_diarization_model='fixture',native_diarization_library='fixture')
            factory,calls=self.factory()
            with self.assertRaises(ValueError):create_app(config,fragment_provider_factory=factory)
            self.assertEqual(calls,[])
    def test_original_cpufactory_no_process_failure_preserves_exception_and_releases(self):
        with RetainedTemporary() as directory:
            factory=CPUFactory(SimpleNamespace(hash='a'*64))
            app=create_app(self.settings(directory),fragment_provider_factory=factory)
            failure=ValueError('explicit metadata fixture failure')
            with patch.object(app.state.store,'db',side_effect=failure),self.assertRaises(ValueError) as caught:
                with TestClient(app):pass
            self.assertIs(caught.exception,failure);self.assertIsNone(app.state.instance_lock.file)
            self.assertEqual(app.state.fragments.factory_initialization_state,'failed')
            with patch.object(app.state.store,'db',side_effect=AssertionError('must not retry')):
                with self.assertRaises(ValueError) as again:app.state.fragments.initialize_factory_once()
                self.assertIs(again.exception,failure)
    def test_arbitrary_failure_keeps_lock_diagnostic_and_original_exception(self):
        with RetainedTemporary() as directory:
            factory,calls=self.factory();failure=LookupError('private diagnostic fixture')
            def fail(store):calls.append('attempt');raise failure
            factory.initialize=fail
            app=create_app(self.settings(directory),fragment_provider_factory=factory)
            with self.assertRaises(LookupError) as caught:
                with TestClient(app):pass
            self.assertIs(caught.exception,failure);self.assertTrue(app.state.shutdown_blocked)
            self.assertIsNotNone(app.state.instance_lock.file);self.assertEqual(calls,['attempt'])
            app.state.instance_lock.release()  # Explicit no-process fixture teardown only.
    def test_pre_lifespan_process_next_cannot_start_factory(self):
        with RetainedTemporary() as directory:
            factory,calls=self.factory();app=create_app(self.settings(directory),fragment_provider_factory=factory)
            self.assertFalse(app.state.fragments.process_next());self.assertEqual(calls,[])


class ParserAndLifetimeTests(unittest.TestCase):
    def test_receive_stream_bounds_deception_disconnect_and_timeout(self):
        async def scenario(messages, headers):
            async def receive():return messages.pop(0)
            request=Request({'type':'http','headers':headers},receive)
            return await http.receive_body(request)
        for messages,headers in (([{'type':'http.request','body':b'a'*1025}],[(b'content-type',b'application/json')]),
             ([{'type':'http.request','body':b'{}'}],[(b'content-type',b'application/json'),(b'content-length',b'1')]),
             ([{'type':'http.disconnect'}],[(b'content-type',b'application/json')])):
            with self.assertRaises(ReadError):asyncio.run(scenario(messages,headers))
        async def slow():
            async def receive():await asyncio.sleep(.03);return {'type':'http.request','body':b'{}'}
            request=Request({'type':'http','headers':[(b'content-type',b'application/json')]},receive)
            with patch.object(http,'RECEIVE_SECONDS',.001):await http.receive_body(request)
        with self.assertRaises(ReadError):asyncio.run(slow())
    def test_outer_asgi_releases_exactly_once_after_send_failure_or_cancel(self):
        class Lease:
            def __init__(self):self.calls=0
            def release(self):self.calls+=1
        async def run(fail):
            lease=Lease();scope={'type':'http','path':'/sessions/x/moss-jobs/x/raw'}
            async def endpoint(scope,receive,send):
                _assign_native_read_lease(scope,lease)
                await send({'type':'http.response.start','status':200,'headers':[]})
                await send({'type':'http.response.body','body':b'synthetic'})
            async def send(message):
                if fail:raise asyncio.CancelledError()
            async def receive():return {'type':'http.disconnect'}
            try:await _NativeSupportReadLifetime(endpoint)(scope,receive,send)
            except asyncio.CancelledError:pass
            self.assertEqual(lease.calls,1)
        asyncio.run(run(False));asyncio.run(run(True))
    def test_late_sync_response_lease_after_scope_closed_releases_immediately(self):
        done=threading.Event();done.set();calls=[]
        lease=SimpleNamespace(release=lambda:calls.append(True))
        scope={'_native_support_read_done':done}
        self.assertFalse(_assign_native_read_lease(scope,lease));self.assertEqual(calls,[True])


class MutationHandoffTests(unittest.TestCase):
    def test_cancelled_request_after_acceptance_does_not_cancel_manager_job(self):
        from fastapi import FastAPI
        entered,release=threading.Event(),threading.Event()
        accepted=[];leases=[]
        class Lease:
            released=False
            def release(self):self.released=True
        class Owner:
            def __init__(self):self.cache=self
            def reserve_jobs_response(self,kind):
                lease=Lease();leases.append(lease);return lease
            def submit(self,sid,cid,body,key,**kwargs):
                accepted.append((sid,cid,key));entered.set();release.wait(1)
                return 202,{'job_id':'a'*32}
        owner=Owner();app=FastAPI();http.register_routes(app,lambda:owner,_assign_native_read_lease)
        endpoint=next(route.endpoint for route in app.routes if getattr(route,'path',None)=='/sessions/{sid}/native-owned-supports/{cid}/moss-jobs')
        body=json.dumps({'jobs_epoch':'a'*32,'expected_capture_epoch':'b'*32,'expected_revision':1,
                         'support_id':'c'*64,'profile':jobs.NAME}).encode()
        async def scenario():
            done=threading.Event()
            scope={'type':'http','query_string':b'','headers':[(b'content-type',b'application/json'),(b'idempotency-key',b'key')],
                   '_native_support_read_done':done}
            async def receive():return {'type':'http.request','body':body}
            task=asyncio.create_task(endpoint(Request(scope,receive),'session','chunk'))
            for _ in range(100):
                if entered.is_set():break
                await asyncio.sleep(.001)
            self.assertTrue(entered.is_set());task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            done.set();scope.pop('_native_support_read_lease').release();release.set()
            await asyncio.sleep(.03)
            self.assertEqual(accepted,[('session','chunk','key')]);self.assertTrue(leases[0].released)
        asyncio.run(scenario())


class UnsupportedHostFenceTests(unittest.TestCase):
    """Mocked missing Windows ownership primitives; no Windows qualification."""
    def test_mocked_windows_off_absent_namespace_starts_without_creation(self):
        with RetainedTemporary() as directory,patch.object(jobs,'_descriptor_support',return_value=False):
            config=Settings(data_dir=directory,start_worker=False,disk_reserve_bytes=0)
            app=create_app(config,analyzer_factory=FakeAnalyzer)
            with TestClient(app) as client:self.assertEqual(client.get('/health').status_code,200)
            self.assertFalse((Path(directory)/jobs.NAMESPACE).exists())
    def test_mocked_windows_existing_namespace_refuses_without_deleting(self):
        with RetainedTemporary() as directory:
            namespace=Path(directory)/jobs.NAMESPACE;namespace.mkdir(mode=0o700)
            marker=namespace/'owner.json';marker.write_bytes(b'preserve synthetic ownership')
            with patch.object(jobs,'_descriptor_support',return_value=False),self.assertRaisesRegex(RuntimeError,'supported Linux host'):
                create_app(Settings(data_dir=directory,start_worker=False,disk_reserve_bytes=0))
            self.assertEqual(marker.read_bytes(),b'preserve synthetic ownership')
    def test_mocked_windows_unknown_identity_and_enabled_jobs_fail_closed(self):
        with RetainedTemporary() as directory,patch.object(jobs,'_descriptor_support',return_value=False):
            with patch.object(Path,'lstat',side_effect=PermissionError('synthetic denial')),self.assertRaises(RuntimeError):
                jobs.ownership_fence(Path(directory))
            with self.assertRaisesRegex(RuntimeError,'supported Linux ownership'):
                jobs.create_namespace(Path(directory))
            self.assertFalse((Path(directory)/jobs.NAMESPACE).exists())
