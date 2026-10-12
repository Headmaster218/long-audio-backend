"""Finite primary/optional races with injected original handles; no subprocesses."""
import os
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from audio_backend import native_execution_permit as mod
from audio_backend.worker import Worker


class Handle:
    def __init__(self, pid=901):
        self.pid, self.returncode, self.waits = pid, None, 0
    def poll(self):return self.returncode
    def wait(self, timeout=None):
        self.waits += 1
        if self.returncode is None:raise subprocess.TimeoutExpired('fixture', timeout)
        return self.returncode


def settings():
    return SimpleNamespace(data_dir='.', process_memory_limit_bytes=6 * 1024**3,
        disk_reserve_bytes=0, native_owned_support_enabled=False)


def sample(*, extra=None, rss=10 * 1024**2, available=10 * 1024**3, free=10 * 1024**3):
    rows = {os.getpid():(1., 0, rss)}
    rows.update(extra or {})
    return mod.ProcessSample(rows, sum(x[2] for x in rows.values()), rss, available, free)


class PermitTests(unittest.TestCase):
    def make(self, sampler=None):
        return mod.NativeExecutionPermit(settings(), sampler=sampler or (lambda _:sample()))
    def reserve(self, permit):
        token, cancelled, callbacks = object(), threading.Event(), []
        permit.reserve_optional(token, cancelled, lambda:callbacks.append(cancelled.is_set()))
        return token, cancelled, callbacks

    def test_claim_probe_blocks_reservation_but_empty_poll_never_cancels(self):
        permit = self.make()
        with permit.claim_probe():
            with self.assertRaises(mod.AdmissionError):self.reserve(permit)
        token, event, callbacks = self.reserve(permit)
        with permit.claim_probe():pass
        self.assertFalse(event.is_set());self.assertEqual(callbacks, [])
        permit.release_optional(token, cleaned=True)

    def test_claim_probe_exception_clears_exactly_once(self):
        permit = self.make()
        with self.assertRaisesRegex(ValueError, 'fixture'):
            with permit.claim_probe():raise ValueError('fixture')
        token, _, _ = self.reserve(permit)
        permit.release_optional(token, cleaned=True)

    def test_real_intent_invalidates_before_callback_and_waits_for_clean_receipt(self):
        permit = self.make();token, event, callbacks = self.reserve(permit)
        with permit.claim_probe():intent = permit.primary_intent('worker')
        self.assertTrue(event.is_set());self.assertEqual(callbacks, [True])
        entered = threading.Event()
        thread = threading.Thread(target=lambda:(intent.wait(), entered.set()))
        thread.start()
        self.assertFalse(entered.wait(.03))
        permit.release_optional(token, cleaned=False)
        self.assertFalse(entered.wait(.03));self.assertEqual(permit.status()['state'], 'OPTIONAL_BLOCKED')
        permit.release_optional(token, cleaned=True)
        self.assertTrue(entered.wait(1));thread.join(1);intent.release()

    def test_waiter_stop_withdraws_without_claim_failure_or_process(self):
        permit = self.make();token, _, _ = self.reserve(permit)
        intent = permit.primary_intent('worker')
        with self.assertRaises(InterruptedError):intent.wait(lambda:True)
        self.assertNotIn('worker', permit._primary)
        self.assertEqual(permit.status()['state'], 'OPTIONAL_RESERVED')
        permit.release_optional(token, cleaned=True)

    def test_nested_extract_keeps_outer_span_and_two_primary_participants(self):
        permit = self.make()
        with permit.primary_span('worker') as worker:
            with permit.primary_span('probe') as nested:
                self.assertIs(worker, nested)
                with self.assertRaises(mod.AdmissionError):self.reserve(permit)
            seen = []
            def probe():
                with permit.primary_span('probe'):seen.append(len(permit._primary))
            thread = threading.Thread(target=probe);thread.start();thread.join(1)
            self.assertEqual(seen, [2]);self.assertIn('worker', permit._primary)
        self.assertFalse(permit._primary)

    def test_primary_uncertainty_only_disables_optional(self):
        permit = self.make();permit.mark_unavailable()
        with permit.primary_span('worker'):pass
        with self.assertRaises(mod.AdmissionError):self.reserve(permit)
        self.assertEqual(permit.status()['state'], 'IDLE')

    def test_close_admission_cancels_and_does_not_release_uncertain_owner(self):
        permit = self.make();token, event, _ = self.reserve(permit)
        permit.close_admission();self.assertTrue(event.is_set())
        self.assertIs(permit._optional, token)
        permit.release_optional(token, cleaned=True)
        with self.assertRaises(mod.AdmissionError):self.reserve(permit)

    def test_resource_failures_at_reservation_and_running(self):
        for current in (sample(rss=6*1024**3), sample(available=1), sample(free=1),
                        sample(extra={901:(2.,os.getpid(),1)})):
            with self.subTest(current=current), self.assertRaises(mod.AdmissionError):
                self.reserve(self.make(lambda _, value=current:value))
        permit = self.make();token, event, _ = self.reserve(permit)
        permit._sampler = lambda _:sample(rss=6*1024**3)
        with self.assertRaises(mod.AdmissionError):permit.check_optional(token)
        self.assertTrue(event.is_set());self.assertIs(permit._optional, token)

    def test_original_handle_exit_plus_absence_is_required(self):
        permit = self.make();proc = Handle()
        identity = SimpleNamespace(create_time=lambda:2.)
        with patch.object(mod.psutil, 'Process', return_value=identity):permit.bind_primary(proc)
        proc.returncode = 0
        with patch.object(mod.psutil, 'Process', side_effect=mod.psutil.NoSuchProcess(proc.pid)):
            permit.finish_primary(proc)
        self.assertEqual(proc.waits, 1);self.assertFalse(permit._handles)
        self.assertIsNone(permit._unavailable)

    def test_close_return_is_not_exit_proof(self):
        permit = self.make();proc = Handle()
        with patch.object(mod.psutil, 'Process', return_value=SimpleNamespace(create_time=lambda:2.)):
            permit.bind_primary(proc);permit.finish_primary(proc)
        self.assertIsNotNone(permit._unavailable)

    def test_resident_native_requires_current_original_owner_and_counts_rss(self):
        proc = Handle();owned = [True]
        current = sample(extra={proc.pid:(2.,os.getpid(),1024**3)})
        permit = self.make(lambda _:current)
        with patch.object(mod.psutil, 'Process', return_value=SimpleNamespace(create_time=lambda:2.)):
            permit.bind_primary(proc, resident=lambda:owned[0]);permit.finish_primary(proc, normal=True)
        token, _, _ = self.reserve(permit);permit.release_optional(token, cleaned=True)
        owned[0] = False
        with self.assertRaises(mod.AdmissionError):self.reserve(permit)
        self.assertIsNone(proc.returncode)

    def test_worker_actual_claim_registers_intent_before_process_and_retains_current(self):
        permit = self.make();config = settings();config._native_execution_permit = permit
        calls = []
        store = SimpleNamespace(fragment_http=None, fail=lambda *args:calls.append('failed'))
        worker = Worker(store, config)
        def claim():
            calls.append('claim')
            self.assertTrue(permit._probe)
            if len(calls) > 2:worker.stop_event.set();return None
            return {'id':'fixture'}
        def process(chunk):
            self.assertIn('worker', permit._primary)
            self.assertEqual(worker.current_chunk, 'fixture')
            with self.assertRaises(mod.AdmissionError):self.reserve(permit)
            calls.append('process');worker.stop_event.set()
        store.claim = claim
        store.chunk = lambda cid:{'status':'running'}
        worker.process = process
        worker.loop()
        self.assertEqual(calls, ['claim','process']);self.assertIsNone(worker.current_chunk)
        self.assertFalse(permit._primary)

    def test_worker_off_does_not_instantiate_or_touch_permit(self):
        config = settings();calls=[]
        store = SimpleNamespace(fragment_http=None, fail=lambda *args:calls.append('failed'))
        worker = Worker(store, config)
        store.claim = lambda: {'id':'fixture'}
        worker.process = lambda chunk:(calls.append('process'),worker.stop_event.set())
        with patch.object(mod, 'NativeExecutionPermit', side_effect=AssertionError('OFF')):worker.loop()
        self.assertEqual(calls, ['process'])

    def test_actual_run_bounded_probe_holds_span_through_wait_and_cleanup(self):
        from audio_backend import resources
        permit = self.make();config = settings();config.cpu_threads = 2;config._native_execution_permit = permit
        proc = Handle();proc.returncode = 0
        checks=[]
        def popen(*args, **kwargs):
            checks.append(bool(permit._primary));return proc
        with patch.object(resources.subprocess, 'Popen', side_effect=popen), \
             patch.object(resources, 'check_resources'), \
             patch.object(resources.psutil, 'Process', return_value=SimpleNamespace(create_time=lambda:1.)), \
             patch.object(permit, 'bind_primary') as bind, patch.object(permit, 'finish_primary') as finish:
            resources.run_bounded(['fixture-ffprobe'], config)
        self.assertEqual(checks,[True]);bind.assert_called_once_with(proc);finish.assert_called_once_with(proc)
        self.assertFalse(permit._primary)

    def test_observed_live_descendant_and_pid_reuse_fail_closed(self):
        for created in (2., 99.):
            permit=self.make(lambda _:sample(extra={901:(2.,os.getpid(),1),902:(3.,901,1)}));proc=Handle()
            with patch.object(mod.psutil,'Process',return_value=SimpleNamespace(create_time=lambda:2.)):
                permit.bind_primary(proc)
            proc.returncode=0
            with patch.object(mod.psutil,'Process',return_value=SimpleNamespace(create_time=lambda:created,is_running=lambda:True)):
                permit.finish_primary(proc)
            self.assertIsNotNone(permit._unavailable)

    def test_full_service_row_and_identity_budget(self):
        class Process:
            def __init__(self,pid,parent=0):self.pid=pid;self.info={'pid':pid,'ppid':parent}
            def create_time(self):return 1.
        caller=Process(os.getpid())
        with patch.object(mod.psutil,'Process',return_value=caller), \
             patch.object(mod.psutil,'process_iter',return_value=[Process(i+100000) for i in range(4097)]), \
             self.assertRaises(mod.AdmissionError):mod.service_sample(settings())
        with patch.object(mod.psutil,'Process',return_value=caller), \
             patch.object(mod.psutil,'process_iter',return_value=[caller]+[Process(i+100000,caller.pid) for i in range(128)]), \
             self.assertRaises(mod.AdmissionError):mod.service_sample(settings())


class SamplerTests(unittest.TestCase):
    class Process:
        def __init__(self,pid,parent,created,rss):
            self.pid,self.parent,self.created,self.rss=pid,parent,created,rss
            self.info={'pid':pid,'ppid':parent}
        def create_time(self):return self.created
        def ppid(self):return self.parent
        def memory_info(self):return SimpleNamespace(rss=self.rss)
    def test_aggregate_counts_caller_resident_candidate_and_descendant_once(self):
        caller=self.Process(os.getpid(),0,1.,10)
        resident=self.Process(901,caller.pid,2.,20)
        candidate=self.Process(902,caller.pid,3.,30)
        grandchild=self.Process(903,candidate.pid,4.,40)
        unrelated=self.Process(904,0,5.,999)
        with patch.object(mod.psutil,'Process',return_value=caller), \
             patch.object(mod.psutil,'process_iter',return_value=[caller,resident,candidate,grandchild,unrelated]), \
             patch.object(mod.psutil,'virtual_memory',return_value=SimpleNamespace(available=10*1024**3)), \
             patch.object(mod.shutil,'disk_usage',return_value=SimpleNamespace(free=10*1024**3)):
            result=mod.service_sample(settings())
        self.assertEqual(result.rss,100);self.assertEqual(result.caller_rss,10)
        self.assertEqual(set(result.identities),{caller.pid,901,902,903})
    def test_denied_scan_budget_timeout_and_changed_identity_reject(self):
        with patch.object(mod.psutil,'Process',side_effect=mod.psutil.AccessDenied()),self.assertRaises(mod.AdmissionError):
            mod.service_sample(settings())
        caller=self.Process(os.getpid(),0,1.,10)
        with patch.object(mod.psutil,'Process',return_value=caller), \
             patch.object(mod.psutil,'process_iter',return_value=[caller]), \
             self.assertRaises(mod.AdmissionError):
            mod.service_sample(settings(),clock=iter([0.,1.]).__next__)
        values=iter([1.,2.,3.]);caller.create_time=lambda:next(values)
        with patch.object(mod.psutil,'Process',return_value=caller), \
             patch.object(mod.psutil,'process_iter',return_value=[caller]), \
             self.assertRaises(mod.AdmissionError):mod.service_sample(settings())


class FactoryExclusionTests(unittest.TestCase):
    def test_post_start_fragment_factory_cannot_run_during_optional_job(self):
        config=settings();permit=mod.NativeExecutionPermit(config,sampler=lambda _:sample())
        config._native_execution_permit=permit
        calls=[];event=threading.Event();token=object()
        permit.reserve_optional(token,event,lambda:calls.append('cancel'))
        fragments=SimpleNamespace(factory=object(),cleanup_error=None,
                                  process_next=lambda _:calls.append('FRAGMENT'))
        store=SimpleNamespace(fragment_http=fragments)
        worker=Worker(store,config)
        def claim():worker.stop_event.set();return None
        store.claim=claim
        worker.loop()
        self.assertTrue(event.is_set());self.assertEqual(calls,['cancel'])
        self.assertIs(permit._optional,token)
        permit.release_optional(token,cleaned=True)


class ReservationEnvelopeTests(unittest.TestCase):
    def test_resident_and_host_future_envelopes_are_conservative(self):
        permit=mod.NativeExecutionPermit(settings(),sampler=lambda _:sample(available=3*1024**3))
        with self.assertRaises(mod.AdmissionError) as raised:
            permit.reserve_optional(object(),threading.Event(),lambda:None)
        self.assertEqual(raised.exception.reason,'service_host_envelope')
        proc=Handle();current=sample(extra={proc.pid:(2.,os.getpid(),int(2.6*1024**3))})
        permit=mod.NativeExecutionPermit(settings(),sampler=lambda _:current)
        with patch.object(mod.psutil,'Process',return_value=SimpleNamespace(create_time=lambda:2.)):
            permit.bind_primary(proc,resident=lambda:True);permit.finish_primary(proc,normal=True)
        with self.assertRaises(mod.AdmissionError) as raised:
            permit.reserve_optional(object(),threading.Event(),lambda:None)
        self.assertEqual(raised.exception.reason,'service_envelope_budget')
