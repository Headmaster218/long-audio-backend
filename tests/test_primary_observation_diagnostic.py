"""Model-free first-failure diagnostics. No process ownership policy changes."""
import json
import os
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from audio_backend import native_execution_permit as mod


class Handle:
    def __init__(self):
        self.pid, self.returncode, self.waits = 901, None, 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waits += 1
        if self.returncode is None:
            raise subprocess.TimeoutExpired('private command', timeout)
        return self.returncode


def settings():
    return SimpleNamespace(data_dir='.', process_memory_limit_bytes=6 * 1024**3,
                           disk_reserve_bytes=0)


def sample(extra=None):
    rows = {os.getpid(): (1., 0, 10)}
    rows.update(extra or {})
    return mod.ProcessSample(rows, sum(v[2] for v in rows.values()), 10,
                             10 * 1024**3, 10 * 1024**3)


class DiagnosticTests(unittest.TestCase):
    def permit(self, sampler=None):
        return mod.NativeExecutionPermit(settings(), sampler=sampler or (lambda _: sample()))

    def failure(self, permit, operation, phase, exception, reason=None):
        self.assertEqual(permit._unavailable, 'primary_ownership_uncertain')
        item = permit.status()['primary_observation_failure']
        self.assertEqual(item['operation'], operation)
        self.assertEqual(item['phase'], phase)
        self.assertEqual(item['exception_class'], exception)
        self.assertEqual(item['reason'], reason)
        self.assertEqual(set(item), {'operation', 'phase', 'exception_class',
                                    'cause_class', 'reason', 'errno',
                                    'handle_count', 'descendant_count'})
        self.assertLess(len(json.dumps(item)), 1024)
        return item

    def bind(self, permit, proc, resident=None):
        with patch.object(mod.psutil, 'Process', return_value=SimpleNamespace(create_time=lambda: 2.)):
            permit.bind_primary(proc, resident=resident)

    def test_initial_status_has_no_failure(self):
        permit = self.permit()
        self.assertIsNone(permit.status()['primary_observation_failure'])

    def test_exit_before_bind_is_safe_fixed_identity_phase(self):
        permit, proc = self.permit(), Handle()
        with patch.object(mod.psutil, 'Process', side_effect=mod.psutil.NoSuchProcess(proc.pid)) as lookup:
            permit.bind_primary(proc)
        self.assertEqual(lookup.call_count, 1)
        self.failure(permit, 'bind_primary', 'bind_identity', 'NoSuchProcess')
        self.assertFalse(permit._handles)

    def test_unknown_exception_has_no_message_or_custom_name(self):
        class PrivateFailure(Exception):
            pass
        permit = self.permit(lambda _: (_ for _ in ()).throw(PrivateFailure('/private/audio secret sql')))
        permit.observe_primary()
        item = self.failure(permit, 'observe_primary', 'sample', 'other')
        encoded = json.dumps(item)
        self.assertNotIn('private', encoded)
        self.assertNotIn('PrivateFailure', encoded)
        self.assertNotIn('secret', encoded)

    def test_unknown_admission_reason_is_redacted(self):
        permit = self.permit(lambda _: (_ for _ in ()).throw(mod.AdmissionError('/private unknown reason')))
        permit.observe_primary()
        self.failure(permit, 'observe_primary', 'sample', 'AdmissionError', 'other')

    def test_bounded_errno_without_oserror_message(self):
        permit = self.permit(lambda _: (_ for _ in ()).throw(OSError(13, 'private path /secret')))
        permit.observe_primary()
        item = self.failure(permit, 'observe_primary', 'sample', 'PermissionError')
        self.assertEqual(item['errno'], 13)
        self.assertNotIn('/secret', json.dumps(item))

    def test_errno_is_strict_bounded_integer(self):
        for value in (True, -1, 65536, '13'):
            with self.subTest(value=value):
                exc = OSError('private value')
                exc.errno = value
                permit = self.permit(lambda _: (_ for _ in ()).throw(exc))
                permit.observe_primary()
                self.assertIsNone(permit.status()['primary_observation_failure']['errno'])

    def test_first_failure_survives_later_failure_success_and_status_mutation(self):
        permit, proc = self.permit(), Handle()
        with patch.object(mod.psutil, 'Process', side_effect=mod.psutil.NoSuchProcess(proc.pid)):
            permit.bind_primary(proc)
        initial = dict(permit.status()['primary_observation_failure'])
        permit.status()['primary_observation_failure']['operation'] = 'corrupted returned copy'
        permit._sampler = lambda _: (_ for _ in ()).throw(ValueError('later'))
        permit.observe_primary()
        permit._sampler = lambda _: sample()
        permit.observe_primary()
        permit.mark_unavailable()
        self.assertEqual(permit.status()['primary_observation_failure'], initial)
        with self.assertRaises(mod.AdmissionError) as refused:
            permit.reserve_optional(object(), threading.Event(), lambda: None)
        self.assertEqual(refused.exception.args, ('primary_ownership_uncertain',))
        self.assertEqual(refused.exception.reason, 'primary_ownership_uncertain')

    def test_broken_logging_sink_does_not_escape_or_clear_latch(self):
        permit, proc = self.permit(), Handle()
        with patch.object(mod._LOG, 'warning', side_effect=RuntimeError('broken sink')), \
             patch.object(mod.psutil, 'Process', side_effect=mod.psutil.NoSuchProcess(proc.pid)):
            permit.bind_primary(proc)
        self.failure(permit, 'bind_primary', 'bind_identity', 'NoSuchProcess')

    def test_single_fixed_log_for_first_failure(self):
        permit = self.permit(lambda _: (_ for _ in ()).throw(ValueError('private value')))
        with patch.object(mod._LOG, 'warning') as sink:
            permit.observe_primary()
            permit.observe_primary()
        self.assertEqual(sink.call_count, 1)
        self.assertNotIn('private value', repr(sink.call_args))

    def test_log_runs_outside_ownership_lock(self):
        permit = self.permit(lambda _: (_ for _ in ()).throw(ValueError('unavailable')))
        owned = []
        with patch.object(mod._LOG, 'warning', side_effect=lambda *args, **kwargs:
                          owned.append(permit._condition._is_owned())):
            permit.observe_primary()
        self.assertEqual(owned, [False])

    def test_failure_does_not_add_process_or_clock_observations(self):
        calls = []
        def sampler(_):
            calls.append('sample')
            raise mod.AdmissionError('process_sample_budget')
        permit = self.permit(sampler)
        with patch.object(mod.psutil, 'Process', side_effect=AssertionError('extra process lookup')), \
             patch.object(mod.psutil, 'process_iter', side_effect=AssertionError('extra process census')), \
             patch.object(mod.time, 'monotonic', side_effect=AssertionError('extra clock call')):
            permit.observe_primary()
        self.assertEqual(calls, ['sample'])
        self.failure(permit, 'observe_primary', 'sample', 'AdmissionError', 'process_sample_budget')

    def test_unconfirmed_wait_retains_handle(self):
        permit, proc = self.permit(), Handle()
        self.bind(permit, proc)
        permit.finish_primary(proc)
        self.failure(permit, 'finish_primary', 'original_wait', 'AdmissionError', 'primary_wait_unconfirmed')
        self.assertIn(id(proc), permit._handles)

    def test_wait_exception_is_recorded_without_command(self):
        permit, proc = self.permit(), Handle()
        self.bind(permit, proc)
        proc.returncode = 0
        with patch.object(proc, 'wait', side_effect=subprocess.TimeoutExpired('private-command', 0)):
            permit.finish_primary(proc)
        item = self.failure(permit, 'finish_primary', 'original_wait', 'TimeoutExpired')
        self.assertNotIn('private-command', json.dumps(item))
        self.assertIn(id(proc), permit._handles)

    def test_resident_identity_mismatch_is_not_relaxed(self):
        proc = Handle()
        permit = self.permit(lambda _: sample({901: (2., os.getpid(), 1)}))
        self.bind(permit, proc, resident=lambda: True)
        with patch.object(mod.psutil, 'Process', return_value=SimpleNamespace(create_time=lambda: 3.)):
            permit.finish_primary(proc, normal=True)
        self.failure(permit, 'finish_primary', 'resident_check', 'AdmissionError', 'resident_identity_uncertain')
        self.assertFalse(permit._handles[id(proc)]['idle'])

    def test_descendant_alive_or_reused_after_wait_stays_owned(self):
        for created in (2., 99.):
            with self.subTest(created=created):
                permit, proc = self.permit(), Handle()
                self.bind(permit, proc)
                proc.returncode = 0
                with patch.object(mod.psutil, 'Process', return_value=SimpleNamespace(
                        create_time=lambda: created, is_running=lambda: True)):
                    permit.finish_primary(proc)
                self.failure(permit, 'finish_primary', 'descendant_check', 'AdmissionError',
                             'primary_descendant_unresolved')
                self.assertIn(id(proc), permit._handles)
                self.assertEqual(proc.waits, 1)

    def test_positive_wait_and_absence_do_not_create_failure(self):
        permit, proc = self.permit(), Handle()
        self.bind(permit, proc)
        proc.returncode = 0
        with patch.object(mod.psutil, 'Process', side_effect=mod.psutil.NoSuchProcess(proc.pid)):
            permit.finish_primary(proc)
        self.assertIsNone(permit._unavailable)
        self.assertIsNone(permit.status()['primary_observation_failure'])
        self.assertFalse(permit._handles)
        self.assertEqual(proc.waits, 1)

    def test_valid_resident_idle_does_not_create_failure(self):
        proc = Handle()
        permit = self.permit(lambda _: sample({901: (2., os.getpid(), 1)}))
        self.bind(permit, proc, resident=lambda: True)
        with patch.object(mod.psutil, 'Process', return_value=SimpleNamespace(create_time=lambda: 2.)):
            permit.finish_primary(proc, normal=True)
        self.assertIsNone(permit._unavailable)
        self.assertIsNone(permit.status()['primary_observation_failure'])
        self.assertTrue(permit._handles[id(proc)]['idle'])


class SamplerDiagnosticTests(unittest.TestCase):
    class Process:
        def __init__(self, pid, parent, created=1.):
            self.pid, self.parent, self.created = pid, parent, created
            self.info = {'pid': pid, 'ppid': parent}
        def create_time(self): return self.created
        def ppid(self): return self.parent
        def memory_info(self): return SimpleNamespace(rss=10)

    def run_failure(self, stage, method, failure):
        caller = self.Process(os.getpid(), 0)
        child = self.Process(901, caller.pid, 2.)
        permit = mod.NativeExecutionPermit(settings())
        with patch.object(mod.psutil, 'Process', return_value=caller), \
             patch.object(mod.psutil, 'process_iter', return_value=[caller, child]), \
             patch.object(child, method, side_effect=failure), \
             patch.object(mod.psutil, 'virtual_memory', return_value=SimpleNamespace(available=10 * 1024**3)), \
             patch.object(mod.shutil, 'disk_usage', return_value=SimpleNamespace(free=10 * 1024**3)):
            permit.observe_primary()
        item = permit.status()['primary_observation_failure']
        self.assertEqual(item['operation'], 'observe_primary')
        self.assertEqual(item['phase'], stage)
        self.assertEqual(item['exception_class'], 'AdmissionError')
        self.assertEqual(item['cause_class'], 'NoSuchProcess')
        self.assertEqual(item['reason'], 'process_sample_unavailable')
        self.assertEqual(permit._unavailable, 'primary_ownership_uncertain')

    def test_exit_between_enumeration_and_first_identity(self):
        self.run_failure('selected_identity', 'create_time', mod.psutil.NoSuchProcess(901))

    def test_exit_before_rss(self):
        self.run_failure('selected_rss', 'memory_info', mod.psutil.NoSuchProcess(901))

    def test_exit_between_identity_reads(self):
        self.run_failure('selected_recheck', 'create_time', [2., mod.psutil.NoSuchProcess(901)])

    def test_remembered_live_identity_missing_from_census_is_not_ignored(self):
        caller = self.Process(os.getpid(), 0)
        permit = mod.NativeExecutionPermit(settings())
        permit._observed = {901: 2.}
        def process(pid):
            return caller if pid == caller.pid else self.Process(pid, caller.pid, 2.)
        with patch.object(mod.psutil, 'Process', side_effect=process), \
             patch.object(mod.psutil, 'process_iter', return_value=[caller]):
            permit.observe_primary()
        item = permit.status()['primary_observation_failure']
        self.assertEqual(item['phase'], 'remembered_identity')
        self.assertEqual(item['reason'], 'remembered_identity_unobserved')
        self.assertEqual(permit._unavailable, 'primary_ownership_uncertain')

    def test_census_pid_reuse_is_not_ignored(self):
        caller = self.Process(os.getpid(), 0)
        child = self.Process(901, caller.pid, 99.)
        permit = mod.NativeExecutionPermit(settings())
        permit._observed = {901: 2.}
        with patch.object(mod.psutil, 'Process', return_value=caller), \
             patch.object(mod.psutil, 'process_iter', return_value=[caller, child]):
            permit.observe_primary()
        item = permit.status()['primary_observation_failure']
        self.assertEqual(item['phase'], 'selected_recheck')
        self.assertEqual(item['reason'], 'process_identity_uncertain')
        self.assertEqual(permit._unavailable, 'primary_ownership_uncertain')


if __name__ == '__main__':
    unittest.main()
