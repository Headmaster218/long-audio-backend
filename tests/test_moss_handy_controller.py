"""Retained model-free owner fixtures. No child process or native model is run."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

if __name__ == '__main__' and len(sys.argv) == 5 and sys.argv[1] == '--fixture':
    # Fixed stdlib fake child; no audio/model reads or model/runtime imports.
    if sys.stdin.buffer.read(1) != b'1':
        raise SystemExit(0)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from audio_backend.moss_handy_runner import read_owned, write_owned
    from audio_backend.moss_handy_profile import canonical
    from audio_backend.native_lexical_alternatives import SourceSupport
    directory = Path(sys.argv[2])
    request_raw = read_owned(directory, 'request.json')
    if hashlib.sha256(request_raw).hexdigest() != sys.argv[3] or sys.argv[4] != 'fixed-v1':
        raise SystemExit(2)
    request = json.loads(request_raw)
    raw = b'[0][S1]synthetic[1]'
    write_owned(directory, 'raw.bin', raw)
    write_owned(directory, 'result.json', canonical(dict(
        construct_attempts=1, session_attempts=1, run_attempts=1, raw_retained=True,
        raw_unavailable_reason=None, raw_kind='exact-native-raw-text-utf8', raw_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest(),
        truncated=False, aborted=False, eos=None, generated_token_count=None,
        model_closed=True, session_closed=True, error_type=None, close_errors=[],
        job_id=request['job_id'], support_id=SourceSupport.from_dict(request['support']).support_id,
        profile_sha256=request['profile_sha256'])))
    raise SystemExit(0)

from audio_backend import moss_handy_controller as c
from audio_backend.moss_handy_profile import NAME
from audio_backend.moss_handy_runner import CAPS, check_directory, float_bytes, read_owned, samples_from_pcm, write_owned
from audio_backend.native_lexical_alternatives import SourceSupport
from test_moss_handy_profile import retained_directory


def support(pcm=b'\x01\x00'*16000):
    frames = len(pcm)//2
    return SourceSupport('1'*64, '2'*64, '3'*64, '4'*64, frames, 0, frames, 0, frames,
        hashlib.sha256(pcm).hexdigest(), hashlib.sha256(float_bytes(samples_from_pcm(pcm))).hexdigest()), pcm


def clean():
    return {'cleanup_verified': True, 'original_handle_waited_and_reaped': True,
        'remaining_live_owned': [], 'discovery_complete': False,
        'cleanup_scope': 'same-owner observed PID/create-time descendants'}


class FakeDriver:
    def __init__(self, directory, sha):
        self.directory = directory; self.sha = sha; self.starts = self.waits = self.cleanups = 0
        self.raw = b'[0][S1]synthetic[1]'; self.start_error = self.wait_error = None
        self.cleanup_value = clean(); self.callback = None; self.modify = lambda row: None
    def start(self, cancelled):
        self.starts += 1
        if self.start_error: raise self.start_error
    def wait(self, cancelled):
        self.waits += 1
        if self.callback: self.callback()
        if self.wait_error: raise self.wait_error
        request = json.loads(read_owned(self.directory, 'request.json'))
        row = dict(construct_attempts=1, session_attempts=1, run_attempts=1,
            raw_retained=self.raw is not None, raw_unavailable_reason=None if self.raw is not None else 'raw_text_oversize',
            raw_kind='exact-native-raw-text-utf8',
            truncated=False, aborted=False, eos=None, generated_token_count=None,
            model_closed=True, session_closed=True, error_type=None, close_errors=[],
            job_id=request['job_id'], support_id=SourceSupport.from_dict(request['support']).support_id,
            profile_sha256=request['profile_sha256'])
        if self.raw is not None:
            row.update(raw_bytes=len(self.raw), raw_sha256=hashlib.sha256(self.raw).hexdigest())
        self.modify(row)
        return row, self.raw
    def cleanup(self, stop):
        self.cleanups += 1
        if isinstance(self.cleanup_value, Exception): raise self.cleanup_value
        return dict(self.cleanup_value)


class ControllerTests(unittest.TestCase):
    def make(self, configure=lambda driver: None):
        directory = retained_directory('controller')
        drivers = []
        def factory(directory, sha):
            driver = FakeDriver(directory, sha); configure(driver); drivers.append(driver); return driver
        controller = c.MossController(directory, profile_name=NAME, model_path='/synthetic-model.fixture', _driver_factory=factory)
        source, pcm = support()
        return controller, controller.submit(source, pcm), drivers

    def test_one_job_one_invocation_one_alternative(self):
        owner, jid, drivers = self.make()
        result = owner.run(jid)
        self.assertEqual(result['state'], 'returned_diagnostic')
        self.assertEqual(drivers[0].starts, 1); self.assertEqual(drivers[0].waits, 1)
        self.assertFalse(result['result']['alternative']['complete_content'])
        self.assertEqual(owner.exact_raw(jid), b'[0][S1]synthetic[1]')
        self.assertFalse(result['cleanup']['discovery_complete'])
        self.assertFalse((owner.directory/'owner.json').exists())
        with self.assertRaises(c.Busy): owner.run(jid)
        with self.assertRaises(c.Busy): owner.submit(*support())

    def test_source_bytes_and_geometry_must_match(self):
        owner, jid, _ = self.make(); owner.cancel(jid); owner.discard(jid)
        source, pcm = support()
        for candidate in (pcm+b'\0\0', b'\0'*len(pcm), bytearray(pcm)):
            with self.subTest(kind=type(candidate).__name__), self.assertRaises(ValueError): owner.submit(source, candidate)

    def test_oversize_rejected_before_float_conversion(self):
        owner, jid, _ = self.make(); owner.cancel(jid); owner.discard(jid)
        source, _ = support()
        with patch.object(c,'samples_from_pcm',side_effect=AssertionError('must not convert')), self.assertRaises(ValueError):
            owner.submit(source,b'\0'*640002)

    def test_directory_identity_drift_blocks_operations(self):
        owner, jid, _ = self.make()
        owner._directory_identity=(0,0,os.getuid())
        with self.assertRaises(ValueError): owner.submit(*support())
        self.assertEqual(owner.run(jid)['state'],'cleanup_blocked')
        with self.assertRaises(c.Busy): owner.discard(jid)
        self.assertTrue((owner.directory/'owner.json').exists())

    def test_parent_identity_is_bound_before_and_after_admission(self):
        from audio_backend import moss_handy_runner as runner
        owner, jid, _ = self.make()
        expected=tuple(json.loads((owner.directory/'owner.json').read_bytes())['parent_identity'])
        for identities, byte in (([expected,expected],b''), ([(0,0)],b'1'), ([expected,(0,0)],b'1')):
            with self.subTest(identities=identities), patch.object(runner,'process_identity',side_effect=identities), self.assertRaises(ValueError):
                runner.admit_parent(owner.directory,io.BytesIO(byte))
        with patch.object(runner,'process_identity',side_effect=[expected,expected]):
            self.assertEqual(runner.admit_parent(owner.directory,io.BytesIO(b'1'))[0],expected)

    def test_maximum_support_and_one_frame(self):
        owner, jid, _ = self.make(); owner.cancel(jid); owner.discard(jid)
        for frames in (1, 320000):
            jid = owner.submit(*support(b'\0\0'*frames))
            owner.cancel(jid); owner.discard(jid)
        with self.assertRaises(ValueError): support(b'\0\0'*320001)

    def test_busy_claim_and_restart_are_fail_closed(self):
        owner, jid, _ = self.make()
        with self.assertRaises(c.Busy): c.MossController(owner.directory, profile_name=NAME, model_path='/synthetic-model.fixture')
        with self.assertRaises(c.Busy): owner.discard(jid)
        self.assertTrue((owner.directory/'owner.json').exists())

    def test_cancel_prepared_never_calls_driver(self):
        owner, jid, drivers = self.make()
        self.assertTrue(owner.cancel(jid)); self.assertEqual(drivers, [])
        self.assertEqual(owner.status(jid)['state'], 'cancelled')
        self.assertFalse(owner.status(jid)['invocation_possible'])
        with self.assertRaises(c.Busy): owner.run(jid)

    def test_late_return_after_cancel_never_publishes(self):
        owner, jid, drivers = self.make(lambda driver: setattr(driver, 'callback', lambda: owner.cancel(jid)))
        result = owner.run(jid)
        self.assertEqual(result['state'], 'cancelled'); self.assertIsNone(result['result'])
        self.assertFalse(result['publication_valid']); self.assertIsNotNone(owner.exact_raw(jid))
        self.assertEqual(drivers[0].starts, 1)

    def test_no_publication_before_cleanup(self):
        owner, jid, drivers = self.make()
        original = FakeDriver.cleanup
        seen = []
        def inspect(driver, stop):
            seen.append(owner.status(jid)['result']); return original(driver, stop)
        with patch.object(FakeDriver, 'cleanup', inspect): owner.run(jid)
        self.assertEqual(seen, [None])

    def test_raw_precedes_projection(self):
        owner, jid, _ = self.make()
        original = c.project
        def inspect(raw, **kwargs):
            self.assertEqual(owner.exact_raw(jid), raw); return original(raw, **kwargs)
        with patch.object(c, 'project', inspect): owner.run(jid)

    def test_raw_unavailable_is_explicit(self):
        owner, jid, _ = self.make(lambda driver: setattr(driver, 'raw', None))
        result = owner.run(jid)
        self.assertIsNone(result['result']); self.assertIsNone(owner.exact_raw(jid))
        self.assertEqual(result['native_receipt']['raw_unavailable_reason'], 'raw_text_oversize')

    def test_start_and_wait_failures_never_replay(self):
        for attribute in ('start_error', 'wait_error'):
            with self.subTest(attribute=attribute):
                owner, jid, drivers = self.make(lambda d: setattr(d, attribute, RuntimeError('synthetic')))
                result = owner.run(jid)
                self.assertEqual(result['state'], 'failed'); self.assertTrue(result['invocation_possible'])
                with self.assertRaises(c.Busy): owner.run(jid)
                self.assertEqual(drivers[0].starts, 1)

    def test_uncertain_cleanup_retains_slot_and_claim(self):
        value = dict(clean(), cleanup_verified=False)
        owner, jid, drivers = self.make(lambda driver: setattr(driver, 'cleanup_value', value))
        result = owner.run(jid)
        self.assertEqual(result['state'], 'cleanup_blocked'); self.assertIsNone(result['result'])
        self.assertTrue((owner.directory/'owner.json').exists())
        with self.assertRaises(c.Busy): owner.discard(jid)
        with self.assertRaises(c.Busy): owner.submit(*support())
        drivers[0].cleanup_value = clean()
        retried = owner.retry_cleanup(jid)
        self.assertEqual(retried['state'], 'failed'); self.assertEqual(drivers[0].starts, 1)
        self.assertIsNone(retried['result'])

    def test_cleanup_exception_and_unwaited_handle_block(self):
        for value in (RuntimeError('synthetic'), dict(clean(), original_handle_waited_and_reaped=False),
                      dict(clean(), remaining_live_owned=[{'pid':123}]), dict(clean(), cleanup_failure={'type':'unknown'})):
            with self.subTest(value_type=type(value).__name__):
                owner, jid, _ = self.make(lambda driver: setattr(driver, 'cleanup_value', value))
                self.assertEqual(owner.run(jid)['state'], 'cleanup_blocked')

    def test_claim_tampering_blocks_release(self):
        owner, jid, _ = self.make(lambda driver: setattr(driver, 'callback', lambda: (owner.directory/'owner.json').write_bytes(b'{}')))
        self.assertEqual(owner.run(jid)['state'], 'cleanup_blocked')
        self.assertTrue((owner.directory/'owner.json').exists())

    def test_return_binding_flags_and_counts(self):
        for key, value in (('job_id','other'), ('support_id','0'*64), ('profile_sha256','0'*64),
                           ('eos',True), ('run_attempts',2), ('run_attempts',True), ('raw_sha256','0'*64),
                           ('raw_bytes',False), ('model_closed',False), ('raw_unavailable_reason','contradiction'),
                           ('raw_kind','full-sdk-envelope')):
            with self.subTest(key=key):
                owner, jid, _ = self.make(lambda driver: setattr(driver, 'modify', lambda row: row.update({key:value})))
                self.assertEqual(owner.run(jid)['state'], 'failed')

    def test_native_close_failure_never_publishes(self):
        owner, jid, _ = self.make(lambda driver: setattr(driver, 'modify', lambda row: row.update(close_errors=[{'object':'model','type':'RuntimeError'}])))
        result = owner.run(jid)
        self.assertEqual(result['state'],'failed'); self.assertIsNone(result['result']); self.assertIsNotNone(owner.exact_raw(jid))

    def test_discard_releases_only_clean_terminal_record(self):
        owner, jid, _ = self.make(); owner.run(jid)
        owner.discard(jid)
        self.assertEqual(list(owner.directory.iterdir()), [])
        with self.assertRaises(KeyError): owner.exact_raw(jid)
        self.assertIsInstance(owner.submit(*support()), str)

    def test_directory_permission_and_alias_faults(self):
        directory = retained_directory('permissions'); directory.chmod(0o755)
        with self.assertRaises(ValueError): c.MossController(directory, profile_name=NAME, model_path='/fixture')
        directory.chmod(0o700)
        with patch.object(Path,'is_symlink',return_value=True), self.assertRaises(ValueError):
            c.MossController(directory, profile_name=NAME, model_path='/fixture')

    def test_fixed_file_and_byte_caps(self):
        directory = retained_directory('scratch')
        with self.assertRaises(ValueError): write_owned(directory,'extra',b'x')
        with self.assertRaises(ValueError): write_owned(directory,'raw.bin',b'x'*131073)
        write_owned(directory,'raw.bin',b'x'*131072)
        self.assertEqual(read_owned(directory,'raw.bin'),b'x'*131072)
        with self.assertRaises(FileExistsError): write_owned(directory,'raw.bin',b'other')
        (directory/'extra.fixture').write_bytes(b'x')
        with self.assertRaises(ValueError): check_directory(directory)


class CleanupTests(unittest.TestCase):
    """Exercise the unchanged reused cleanup with injected process observations."""
    def fixture(self, *, bound=True):
        from audio_backend.fragment_model_guard import Identity
        identity = Identity(123456, 1.0)
        guard = SimpleNamespace(deadline=time.monotonic()+60,
            own_roots=(identity,) if bound else (), known_own={},
            terminate_owned=lambda **kw: None, check=lambda: None)
        proc = SimpleNamespace(pid=identity.pid, wait=lambda timeout: 0, kill=lambda: None)
        return identity, guard, proc

    def run_cleanup(self, table, records, *, bound=True):
        from audio_backend import fragment_model_guard as g
        from audio_backend.qwen_candidate_runner import process_cleanup
        identity, guard, proc = self.fixture(bound=bound)
        receipt = {}
        with patch.object(g,'process_table',side_effect=table if isinstance(table,Exception) else None,
                          return_value=table if not isinstance(table,Exception) else None), patch.object(g,'rss_records',return_value=records):
            process_cleanup(proc,guard,receipt,False)
        return receipt

    def test_no_such_process_after_original_wait(self):
        receipt = self.run_cleanup({},[])
        self.assertTrue(receipt['original_handle_waited_and_reaped']); self.assertTrue(receipt['cleanup_verified'])

    def test_unbound_root_never_verified(self):
        receipt = self.run_cleanup({},[],bound=False)
        self.assertFalse(receipt['cleanup_verified']); self.assertIn('cleanup_failure',receipt)

    def test_pid_reuse_blocks_verification(self):
        receipt = self.run_cleanup({123456:(1,2.0)},[])
        self.assertFalse(receipt['cleanup_verified']); self.assertIn('cleanup_failure',receipt)

    def test_unknown_discovery_blocks_verification(self):
        from audio_backend.fragment_model_guard import ResourceLimit
        receipt = self.run_cleanup(ResourceLimit('synthetic access denial'),[])
        self.assertFalse(receipt['cleanup_verified']); self.assertIn('cleanup_failure',receipt)

    def test_zombie_is_terminated_but_not_claimed_reaped(self):
        import psutil
        records=[{'pid':123457,'created':1.0,'status':psutil.STATUS_ZOMBIE,'rss':0}]
        receipt=self.run_cleanup({123456:(1,1.0),123457:(123456,1.0)},records)
        self.assertTrue(receipt['cleanup_verified'])
        self.assertEqual(receipt['terminated_but_not_reaped_nonchild_zombies'],records)

    def test_known_live_descendant_blocks_release(self):
        from audio_backend import fragment_model_guard as g
        from audio_backend.qwen_candidate_runner import process_cleanup
        identity, guard, proc = self.fixture(); guard.deadline=time.monotonic()+1
        receipt={}
        with patch.object(g,'process_table',return_value={123456:(1,1.0),123457:(123456,1.0)}), \
                patch.object(g,'rss_records',return_value=[{'pid':123457,'created':1.0,'status':'running','rss':1}]):
            process_cleanup(proc,guard,receipt,False)
        self.assertFalse(receipt['cleanup_verified']); self.assertTrue(receipt['remaining_live_owned'])

    def test_direct_child_timeout_does_not_claim_reap(self):
        from audio_backend.qwen_candidate_runner import process_cleanup
        identity, guard, proc = self.fixture(); guard.deadline=time.monotonic()+1
        def timeout(timeout): raise subprocess.TimeoutExpired('synthetic',timeout)
        proc.wait=timeout; receipt={}
        process_cleanup(proc,guard,receipt,False)
        self.assertFalse(receipt['original_handle_waited_and_reaped']); self.assertFalse(receipt['cleanup_verified'])


class ProcessWiringTests(unittest.TestCase):
    """Tiny admitted fake process evidence, separate from mocked unit evidence."""
    def test_real_owned_handshake_raw_handoff_and_reap(self):
        directory = retained_directory('process-wiring')
        owner = c.MossController(directory, profile_name=NAME, model_path='/synthetic-model.fixture')
        jid = owner.submit(*support())
        original_popen = subprocess.Popen
        observed = []
        def launch(command, **kwargs):
            self.assertEqual(command[:4], [sys.executable, '-I', '-B', '-S'])
            self.assertEqual(command[4], str(c.ROOT / 'audio_backend/moss_handy_runner.py'))
            self.assertEqual(len(command), 7)
            actual = command[:4] + [str(Path(__file__).resolve()), '--fixture', command[5], command[6], 'fixed-v1']
            observed.append(actual)
            return original_popen(actual, **kwargs)
        with patch.object(c.subprocess, 'Popen', side_effect=launch):
            result = owner.run(jid)
        self.assertEqual(len(observed), 1)
        self.assertEqual(result['state'], 'returned_diagnostic')
        self.assertTrue(result['cleanup']['original_handle_waited_and_reaped'])
        self.assertTrue(result['cleanup']['cleanup_verified'])
        self.assertFalse(result['cleanup']['discovery_complete'])
        self.assertEqual(owner.exact_raw(jid), b'[0][S1]synthetic[1]')
        self.assertFalse((directory/'owner.json').exists())
        # Preserve the fixture files; slot remains terminal until explicit discard.
        with self.assertRaises(c.Busy): owner.submit(*support())

    def test_eof_before_admission_never_constructs(self):
        directory = retained_directory('process-eof')
        command = [sys.executable, '-I', '-B', '-S', str(Path(__file__).resolve()), '--fixture',
                   str(directory), '0'*64, 'fixed-v1']
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=5), 0)
        finally:
            if proc.poll() is None:
                proc.kill(); proc.wait(timeout=5)
        self.assertFalse((directory/'result.json').exists())
        self.assertFalse((directory/'raw.bin').exists())


if __name__ == '__main__':
    unittest.main()
