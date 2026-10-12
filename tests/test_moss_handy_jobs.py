"""One-slot source/TTL/cleanup state machine using real cache and fake drivers."""
import hashlib
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from audio_backend import moss_handy_jobs as jobs
from audio_backend.moss_handy_profile import NAME
from audio_backend.native_execution_permit import NativeExecutionPermit
from audio_backend.native_owned_support_cache import ReadError
from test_native_owned_support_cache import CacheFixture
from test_native_execution_permit import sample
from test_moss_handy_controller import FakeDriver, clean
from test_moss_handy_profile import retained_directory


class RetainedTemporary:
    def __init__(self,*args,**kwargs):self.name=str(retained_directory('jobs-fixture'))
    def cleanup(self):pass
    def __enter__(self):return self.name
    def __exit__(self,*args):pass


def retain_outcome(root,owner):
    record=owner._live
    files=[]
    if owner.directory.exists():
        for path in sorted(owner.directory.iterdir()):
            if path.is_file() and not path.is_symlink():
                raw=path.read_bytes()
                files.append({'name':path.name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    value={'status':'synthetic-fixture-before-teardown','job':owner._summary(record) if record else None,
           'permit':owner.permit.status(),'slot_files':files}
    (Path(root)/'fixture-outcome.json').write_bytes(jobs.encoded(value))


class ManualThread:
    """Handoff succeeds, explicit fixture drives it. Never spawns a child."""
    def __init__(self, *, target, args, **kwargs):
        self.target, self.args, self.alive = target, args, False
    def start(self):self.alive = True
    def is_alive(self):return self.alive
    def join(self, timeout=None):pass
    def run(self):
        try:self.target(*self.args)
        finally:self.alive = False


class JobsFixture(CacheFixture):
    def setUp(self):
        with patch('test_native_owned_support_cache.tempfile.TemporaryDirectory',RetainedTemporary):
            super().setUp()
        self.store.settings.moss_jobs_enabled = True
        self.store.settings.moss_jobs_model_path = '/synthetic-model.fixture'
        self.store.settings.data_dir = self.root
        self.store.settings.disk_reserve_bytes = 0
        self.store.settings.process_memory_limit_bytes = 6*1024**3
        self.permit = NativeExecutionPermit(self.store.settings, sampler=lambda _:sample())
        self.drivers = []
        def factory(directory, sha):
            driver = FakeDriver(directory, sha)
            self.drivers.append(driver)
            return driver
        self.owner = jobs.MossJobs(self.store, self.cache, self.permit, clock=self.clock,
                                   _driver_factory=factory, _thread_factory=ManualThread)
    def tearDown(self):
        retain_outcome(self.root,self.owner)
        # No real optional thread/process exists in this fixture. Preserve all
        # slot bytes, including intentional uncertain claims, for guard review.
        self.owner.close_admission()
        super().tearDown()
    def ready(self, *, sid='session'):
        chunk, pcm, _, artifact = self.capture(sid=sid)
        return chunk, {'jobs_epoch':self.owner.jobs_epoch, 'expected_capture_epoch':self.cache.epoch,
            'expected_revision':self.cache._entries[(sid,chunk['id'])].revision,
            'support_id':artifact.support.support_id, 'profile':NAME}
    def submit(self, chunk, body, key='key'):
        return self.owner.submit(chunk['session_id'], chunk['id'], body, key)
    def finish(self):
        record = self.owner._live
        record['thread'].run()
        return record


class JobsTests(JobsFixture):
    def test_explicit_one_job_preserves_primary_and_exact_raw(self):
        chunk, body = self.ready()
        before = tuple(self.db.execute('SELECT status,next_offset,metadata FROM chunks').fetchone())
        code, reply = self.submit(chunk, body)
        self.assertEqual(code,202);self.assertEqual(reply['state'],'reserved');self.assertFalse(self.drivers)
        record = self.finish()
        self.assertEqual(record['state'],'returned_diagnostic')
        self.assertEqual(self.owner.content('session',record['job_id'],'raw'),b'[0][S1]synthetic[1]')
        result = json.loads(self.owner.content('session',record['job_id'],'result'))
        self.assertIsNone(result['eos']);self.assertFalse(result['complete_content'])
        self.assertEqual(before,tuple(self.db.execute('SELECT status,next_offset,metadata FROM chunks').fetchone()))
        self.assertEqual(self.drivers[0].starts,1);self.assertIsNone(self.cache._dispatch)
        self.assertIsNone(self.permit._optional)

    def test_same_key_reuses_job_and_conflict_does_not_replay(self):
        chunk, body = self.ready();_, first = self.submit(chunk,body)
        code, second = self.submit(chunk,body)
        self.assertEqual(code,200);self.assertEqual(first['job_id'],second['job_id'])
        with self.assertRaises(ReadError) as caught:self.submit(chunk,{**body,'expected_revision':body['expected_revision']+1})
        self.assertEqual(caught.exception.reason,'idempotency_conflict')
        self.assertEqual(len(self.owner._records),1)

    def test_busy_and_rejected_source_do_not_consume_keys(self):
        chunk, body = self.ready()
        with self.permit.primary_span('worker'), self.assertRaises(ReadError):self.submit(chunk,body)
        self.assertEqual(self.owner._records,[]);self.assertIsNone(self.cache._dispatch)
        with self.assertRaises(ReadError):self.submit(chunk,{**body,'support_id':'0'*64})
        self.assertEqual(self.owner._records,[])
        self.submit(chunk,body)
        with self.assertRaises(ReadError):self.submit(chunk,body,'other')
        self.assertEqual(len(self.owner._records),1)

    def test_retire_cleaned_only_after_fully_valid_next_request(self):
        chunk, body = self.ready();_, original = self.submit(chunk,body);self.finish()
        with self.assertRaises(ReadError):self.submit(chunk,{**body,'expected_revision':999},'two')
        self.assertEqual(self.owner._live['job_id'],original['job_id'])
        _, second = self.submit(chunk,body,'two')
        self.assertNotEqual(original['job_id'],second['job_id'])
        self.assertEqual(self.owner._records[0]['state'],'retired')
        self.assertIsNone(self.owner._records[0]['token'])
        with self.assertRaises(ReadError) as caught:self.submit(chunk,body)
        self.assertEqual(caught.exception.status_code,410)

    def test_epoch_and_sixty_four_tombstones_never_evict(self):
        chunk, body = self.ready()
        for index in range(64):
            self.submit(chunk,body,str(index));self.finish()
        self.assertEqual(len(self.owner._keys),64)
        with self.assertRaises(ReadError) as caught:self.submit(chunk,body,'65')
        self.assertEqual((caught.exception.status_code,caught.exception.reason),(429,'epoch_capacity'))
        with self.assertRaises(ReadError):self.submit(chunk,{**body,'jobs_epoch':'0'*32},'65')
        page = json.loads(self.owner.list('session',after=20,limit=20,expected_jobs_epoch=self.owner.jobs_epoch))
        self.assertEqual([x['sequence'] for x in page['jobs']],list(range(21,41)))
        self.assertTrue(page['has_more']);self.assertLessEqual(len(jobs.encoded(page)),32768)

    def test_ttl_is_acceptance_based_and_cancels_before_late_dispatch(self):
        chunk, body = self.ready();self.submit(chunk,body)
        self.clock.now += 901
        record = self.finish()
        self.assertTrue(record['cancelled'].is_set());self.assertFalse(self.drivers)
        with self.assertRaises(ReadError):self.owner.content('session',record['job_id'],'raw')
        self.assertEqual(record['state'],'expired')
        self.assertIsNone(self.permit._optional)

    def test_source_deleted_between_acceptance_and_dispatch_never_starts(self):
        chunk, body = self.ready();self.submit(chunk,body)
        self.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'");self.db.commit()
        self.cache.invalidate('session')
        record = self.finish()
        self.assertFalse(self.drivers);self.assertIsNone(record['result'])
        self.assertTrue(record['cleaned']);self.assertIsNone(self.cache._dispatch)

    def test_source_changed_after_run_suppresses_raw_and_result(self):
        chunk, body = self.ready();self.submit(chunk,body)
        factory = self.owner._driver_factory
        def mutate(directory, sha):
            driver = factory(directory,sha)
            driver.callback = lambda:(self.db.execute("UPDATE chunks SET status='paused' WHERE id=?",(chunk['id'],)),self.db.commit())
            return driver
        self.owner._driver_factory = mutate
        # Controller factory was already bound during submit.
        self.owner._live['controller']._factory = mutate
        record = self.finish()
        self.assertIsNone(record['raw']);self.assertIsNone(record['result']);self.assertTrue(record['cleaned'])
        self.assertIsNone(self.permit._optional)

    def test_cleanup_failure_keeps_permit_pin_marker_and_retry_never_invokes(self):
        chunk, body = self.ready();self.submit(chunk,body)
        original = self.owner._live['controller']._factory
        def failing(directory,sha):
            driver = original(directory,sha);driver.cleanup_value = RuntimeError('fixture');return driver
        self.owner._live['controller']._factory = failing
        record = self.finish()
        self.assertEqual(record['state'],'cleanup_blocked');self.assertIs(self.permit._optional,record)
        self.assertIsNotNone(self.cache._dispatch);self.assertTrue((self.owner.directory/'owner.json').exists())
        self.clock.now += 901
        self.owner.detail('session',record['job_id'])
        self.assertEqual(record['state'],'cleanup_blocked');self.assertIs(self.permit._optional,record)
        self.drivers[0].cleanup_value = clean()
        code,_ = self.owner.cancel('session',record['job_id'],self.owner.jobs_epoch)
        self.assertEqual(code,202);record['thread'].run()
        self.assertEqual(self.drivers[0].starts,1);self.assertEqual(self.drivers[0].cleanups,2)
        self.assertTrue(record['cleaned']);self.assertIsNone(self.permit._optional)

    def test_primary_preemption_wins_over_late_fake_return(self):
        chunk,body=self.ready();self.submit(chunk,body)
        original=self.owner._live['controller']._factory
        intents=[]
        def factory(directory,sha):
            driver=original(directory,sha)
            driver.callback=lambda:intents.append(self.permit.primary_intent('probe'))
            return driver
        self.owner._live['controller']._factory=factory
        record=self.finish();self.assertIsNone(record['result']);self.assertIsNone(record['raw'])
        self.assertTrue(record['cancelled'].is_set());intents[0].wait();intents[0].release()

    def test_failed_thread_handoff_has_no_invocation_and_truthful_cleanup(self):
        class Broken(ManualThread):
            def start(self):raise RuntimeError('fixture start failed')
        self.owner._thread_factory=Broken
        chunk,body=self.ready();code,reply=self.submit(chunk,body)
        self.assertEqual(code,202);self.assertFalse(reply['invocation_possible']);self.assertFalse(reply['cleanup_pending'])
        self.assertFalse(self.drivers);self.assertIsNone(self.permit._optional)

    def test_result_read_reauthorizes_and_metadata_has_no_private_bytes(self):
        chunk,body=self.ready();self.submit(chunk,body);record=self.finish()
        text=self.owner.detail('session',record['job_id']).decode()
        self.assertNotIn(str(self.root),text);self.assertNotIn('synthetic',text);self.assertNotIn('parent_identity',text)
        self.db.execute("UPDATE chunks SET status='paused' WHERE id=?",(chunk['id'],));self.db.commit()
        with self.assertRaises(ReadError):self.owner.content('session',record['job_id'],'raw')

    def test_failed_diagnostic_can_keep_raw_after_verified_cleanup(self):
        chunk,body=self.ready();self.submit(chunk,body)
        original=self.owner._live['controller']._factory
        def factory(directory,sha):
            driver=original(directory,sha);driver.modify=lambda row:row.update(close_errors=['SyntheticClose'])
            return driver
        self.owner._live['controller']._factory=factory
        record=self.finish();self.assertEqual(record['state'],'failed')
        self.assertIsNone(record['result']);self.assertEqual(self.owner.content('session',record['job_id'],'raw'),b'[0][S1]synthetic[1]')

    def test_namespace_never_creates_when_off_and_refuses_leftovers_and_links(self):
        absent=self.root/'absent';jobs.ownership_fence(absent);self.assertFalse(absent.exists())
        (self.owner.directory/'owner.json').write_bytes(b'{}')
        with self.assertRaises(RuntimeError):jobs.ownership_fence(self.root)
        (self.owner.directory/'owner.json').unlink()
        import errno
        link = self.root / 'alias'
        observations = []
        parent_identity = (self.root.stat().st_dev, self.root.stat().st_ino)
        # Mocked nofollow-open coverage: no real symlink is retained. The
        # descriptor-relative traversal and ownership_fence exception branch
        # remain real; unrelated opens delegate to the original os module.
        def nofollow_open(path, flags, *args, **kwargs):
            if os.fspath(path) == link.name:
                self.assertEqual(path, 'alias')
                self.assertTrue(flags & os.O_NOFOLLOW)
                self.assertTrue(flags & os.O_DIRECTORY)
                self.assertIsInstance(kwargs.get('dir_fd'), int)
                info = os.fstat(kwargs['dir_fd'])
                self.assertEqual((info.st_dev, info.st_ino), parent_identity)
                observations.append({'basename': path, 'nofollow': True,
                                     'descriptor_relative': True, 'errno': errno.ELOOP})
                raise OSError(errno.ELOOP, 'synthetic symlink nofollow refusal')
            return os.open(path, flags, *args, **kwargs)

        scoped_os = SimpleNamespace(**dict(vars(os), open=nofollow_open,
            supports_dir_fd=os.supports_dir_fd | {nofollow_open}))
        try:
            with patch.object(jobs, 'os', scoped_os):
                with self.assertRaisesRegex(RuntimeError, 'MOSS ownership namespace unavailable'):
                    jobs.ownership_fence(link)
            self.assertEqual(len(observations), 1)
            self.assertFalse(link.exists())
        finally:
            (self.root / 'mocked-namespace-alias.json').write_bytes(jobs.encoded({
                'coverage': 'mocked_descriptor_relative_symlink_open_refusal',
                'observations': observations}))

    def test_namespace_wrong_mode_extra_file_and_hardlink_refused(self):
        namespace=self.root/jobs.NAMESPACE
        namespace.chmod(0o755)
        with self.assertRaises(RuntimeError):jobs.ownership_fence(self.root)
        namespace.chmod(0o700)
        extra=namespace/'extra';extra.write_bytes(b'x')
        with self.assertRaises(RuntimeError):jobs.ownership_fence(self.root)
        extra.unlink()
        slot_info = self.owner.directory.stat()
        observations = []
        # This historical hardlink-named ID exercises nonempty-slot refusal,
        # not nlink validation. Inject only the slot scan, with no real links.
        class SlotEntries:
            def __enter__(self):
                return iter((SimpleNamespace(name='raw.bin'),
                             SimpleNamespace(name='owner.json')))
            def __exit__(self, *args):
                return False

        def slot_scandir(descriptor):
            if isinstance(descriptor, int):
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) == (slot_info.st_dev, slot_info.st_ino):
                    observations.append({'names': ['raw.bin', 'owner.json']})
                    return SlotEntries()
            return os.scandir(descriptor)

        scoped_os = SimpleNamespace(**dict(vars(os), scandir=slot_scandir,
            supports_fd=os.supports_fd | {slot_scandir}))
        try:
            with patch.object(jobs, 'os', scoped_os):
                with self.assertRaisesRegex(RuntimeError, 'MOSS ownership unresolved'):
                    jobs.ownership_fence(self.root)
            self.assertEqual(len(observations), 1)
            self.assertEqual(list(self.owner.directory.iterdir()), [])
        finally:
            (self.root / 'mocked-nonempty-slot.json').write_bytes(jobs.encoded({
                'coverage': 'mocked_nonempty_slot_refusal_without_nlink_validation',
                'observations': observations}))

    def test_slot_binding_has_both_epochs_and_fixed_private_names(self):
        chunk,body=self.ready();self.submit(chunk,body)
        claim=json.loads((self.owner.directory/'owner.json').read_bytes())
        self.assertEqual(claim['service_context']['jobs_epoch'],self.owner.jobs_epoch)
        self.assertEqual(claim['service_context']['capture_epoch'],self.cache.epoch)
        self.assertLessEqual(len((self.owner.directory/'owner.json').read_bytes()),1024)
        self.assertNotIn('tmp',self.owner.directory.relative_to(self.root).parts)


class AdmissionBoundaryTests(JobsFixture):
    def test_deletion_after_input_before_admission_never_invokes(self):
        chunk,body=self.ready();self.submit(chunk,body)
        original=self.owner._live['controller']._factory
        def factory(directory,sha):
            driver=original(directory,sha)
            def start(cancelled):
                self.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'");self.db.commit()
                driver._service_gate.check('before_admission')
                driver.starts+=1
            driver.start=start;return driver
        self.owner._live['controller']._factory=factory
        record=self.finish();self.assertEqual(self.drivers[0].starts,0);self.assertIsNone(record['raw'])
        self.assertTrue(record['cleaned']);self.assertIsNone(self.permit._optional)
    def test_source_expiry_poll_needs_no_external_cache_activity(self):
        chunk,body=self.ready();self.clock.now+=899;self.submit(chunk,body)
        original=self.owner._live['controller']._factory
        def factory(directory,sha):
            driver=original(directory,sha)
            def callback():
                self.clock.now+=2
                driver._service_gate.check('running')
            driver.callback=callback;return driver
        self.owner._live['controller']._factory=factory
        record=self.finish();self.assertLess(self.clock.now,record['deadline'])
        self.assertTrue(record['cancelled'].is_set());self.assertIsNone(record['raw']);self.assertTrue(record['cleaned'])
    def test_pending_dispatch_release_drains_after_lock_recovery(self):
        chunk,body=self.ready();self.submit(chunk,body)
        lease=self.owner._live['lease']
        original=lease.release
        def blocked_release():
            self.cache._lock.acquire()
            try:return original()
            finally:self.cache._lock.release()
        with patch.object(lease,'release',side_effect=blocked_release):record=self.finish()
        self.assertTrue(record['cleaned']);self.assertIsNone(self.permit._optional)
        self.assertTrue(self.owner._summary(record)['memory_cleanup_pending'])
        self.assertIs(self.cache._dispatch,lease)
        self.cache.drain();self.assertTrue(lease.released);self.assertIsNone(self.cache._dispatch)
        self.assertFalse(self.owner._summary(record)['cleanup_pending'])


class WorkingEnvelopeTests(JobsFixture):
    def test_maximum_support_and_raw_wrapper_peak_is_below_sixteen_mib(self):
        import tracemalloc
        chunk,_,_,artifact=self.capture(frames=320000)
        body={'jobs_epoch':self.owner.jobs_epoch,'expected_capture_epoch':self.cache.epoch,
              'expected_revision':self.cache._entries[('session',chunk['id'])].revision,
              'support_id':artifact.support.support_id,'profile':NAME}
        tracemalloc.start()
        try:
            self.submit(chunk,body)
            original=self.owner._live['controller']._factory
            def factory(directory,sha):
                driver=original(directory,sha);driver.raw=b'x'*131072;return driver
            self.owner._live['controller']._factory=factory
            record=self.finish()
            raw=self.owner.content('session',record['job_id'],'raw')
            current,peak=tracemalloc.get_traced_memory()
            self.assertEqual(len(raw),131072);self.assertLess(peak,16*1024**2)
            self.assertLessEqual(sum(len(record[k] or b'') for k in ('raw','result'))+262144+65536,1024**2)
            (self.root/'wrapper-envelope.json').write_bytes(jobs.encoded({'current_bytes':current,'peak_bytes':peak,'limit':16*1024**2}))
        finally:tracemalloc.stop()


class ErasureLifetimeTests(JobsFixture):
    def test_deleting_cleaned_terminal_discards_private_controller_content(self):
        chunk,body=self.ready();self.submit(chunk,body);record=self.finish()
        controller=record['controller'];self.assertIsNotNone(controller.exact_raw(record['controller_id']))
        self.cache.invalidate('session')
        self.assertFalse(self.owner.invalidate_session('session'))
        self.assertIsNone(self.owner._live);self.assertIsNone(controller._job)
        self.assertIsNone(record['raw']);self.assertEqual(record['state'],'retired')
        self.assertEqual(list(self.owner.directory.iterdir()),[])
    def test_deletion_during_running_honors_purge_after_positive_cleanup(self):
        chunk,body=self.ready();self.submit(chunk,body);record=self.owner._live
        original=record['controller']._factory
        def factory(directory,sha):
            driver=original(directory,sha)
            driver.callback=lambda:self.owner.invalidate_session('session')
            return driver
        record['controller']._factory=factory
        self.finish()
        self.assertTrue(record['cleaned']);self.assertIsNone(self.owner._live)
        self.assertIsNone(record['controller']);self.assertIsNone(record['lease'])
        self.assertEqual(record['state'],'retired');self.assertIsNone(self.permit._optional)
    def test_purge_failure_reports_pending_and_never_discards_unknown_claim(self):
        chunk,body=self.ready();self.submit(chunk,body);record=self.finish()
        controller=record['controller']
        with patch.object(controller,'discard',side_effect=RuntimeError('synthetic identity uncertainty')):
            self.assertTrue(self.owner.invalidate_session('session'))
            self.assertTrue(self.owner.health()['cleanup_pending'])
            self.assertIs(record,self.owner._live);self.assertIs(record['controller'],controller)
        self.assertFalse(self.owner.invalidate_session('session'));self.assertIsNone(self.owner._live)
