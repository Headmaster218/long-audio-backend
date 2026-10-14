# Unexecuted portable template; generate every configuration placeholder before use.
import os,sys,time,signal,resource,hashlib,json,stat,threading
from pathlib import Path
START=time.monotonic();WORK_END=START+50;END=START+60
HERE=Path(__file__).resolve().parent
PACKET=Path('@GENERATED_WORKSPACE_ROOT@')/'@GENERATED_PACKET_ROOT_REL@'
OUTPUT=Path('@GENERATED_WORKSPACE_ROOT@')/'@GENERATED_FOCUSED_OUTPUT_ROOT_REL@'
def need(x,reason):
    if not x:raise RuntimeError(reason)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def check():
    need(time.monotonic()<WORK_END,'focused_deadline')
    need(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024<=256*2**20,'focused_peak_RSS')
need(len(sys.argv)==3 and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,'focused_isolated_arguments')
need(Path(sys.executable)==Path('@GENERATED_HOST_PYTHON@'),'focused_host_python')
os.sched_setaffinity(0,{3});os.setpriority(os.PRIO_PROCESS,0,19);os.umask(0o077)
resource.setrlimit(resource.RLIMIT_AS,(512*2**20,512*2**20));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
for root,wanted in ((PACKET,sys.argv[1]),(HERE,sys.argv[2])):
    manifest=root/'SOURCE_MANIFEST.json';need(digest(manifest)==wanted,'focused_source_manifest_pin')
    rows=json.loads(manifest.read_bytes())['files']
    rows=[{'path':name,**pin} for name,pin in rows.items()] if isinstance(rows,dict) else rows
    for row in rows:
        f=root/row['path'];need(f.is_file() and not f.is_symlink() and f.stat().st_size==row['bytes'] and digest(f)==row['sha256'],'focused_source_pin')
owner_ref={'path':str(HERE/'SOURCE_MANIFEST.json'),'bytes':(HERE/'SOURCE_MANIFEST.json').stat().st_size,'sha256':sys.argv[2]}
harness_ref={'path':str(PACKET/'SOURCE_MANIFEST.json'),'bytes':(PACKET/'SOURCE_MANIFEST.json').stat().st_size,'sha256':sys.argv[1]}
sys.path.insert(0,str(PACKET/'controller'))
import socket

def audit(event,args):
    if event=='sqlite3.connect':need(args[0]==':memory:','focused_memory_SQL_only')
    if event.startswith(('socket.','subprocess.','ctypes.')) or event in {'os.system','os.posix_spawn','os.fork','os.forkpty'}:
        raise RuntimeError('external_execution_blocked')
sys.addaudithook(audit)
def timeout(sig,frame):raise TimeoutError('focused_work_deadline')
signal.signal(signal.SIGALRM,timeout);signal.alarm(50)
from contextlib import contextmanager
import sqlite3
import unittest
import common as d


class DiagnosticTests(unittest.TestCase):
    def test_success_returns_identical_object(self):
        diag = d.Diagnostics()
        value = object()
        self.assertIs(diag.wrap('test.call', lambda: value)(), value)
        self.assertEqual(diag.records, [])

    def test_original_exception_identity_and_traceback_tail(self):
        diag = d.Diagnostics()
        original = ValueError('must never be logged')
        def fail():
            raise original
        try:
            diag.wrap('test.call', fail)()
        except ValueError as caught:
            self.assertIs(caught, original)
            tb = caught.__traceback__
            while tb.tb_next is not None:
                tb = tb.tb_next
            self.assertIs(tb.tb_frame.f_code, fail.__code__)
        else:
            self.fail('exception suppressed')
        self.assertEqual(diag.records, [{'stage': 'test.call', 'exception_type': 'ValueError',
            'sqlite_errorcode': None, 'sqlite_errorname': None}])

    def test_hostile_exception_getters_and_string_are_not_called(self):
        class HostileError(Exception):
            def __getattribute__(self, name):
                raise AssertionError('getter called')
            def __str__(self):
                raise AssertionError('string called')
            def __repr__(self):
                raise AssertionError('repr called')
        diag = d.Diagnostics()
        diag.record('test.hostile', HostileError('secret'))
        self.assertEqual(diag.records[0]['exception_type'], 'HostileError')
        self.assertEqual(set(diag.records[0]),
            {'stage', 'exception_type', 'sqlite_errorcode', 'sqlite_errorname'})

    def test_real_sqlite_error_code_and_identity(self):
        db = sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE t (value INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO t VALUES (1)')
            try:
                db.execute('INSERT INTO t VALUES (1)')
            except sqlite3.IntegrityError as exc:
                original = exc
            diag = d.Diagnostics()
            def fail():
                raise original
            try:
                diag.wrap('test.sqlite', fail)()
            except sqlite3.IntegrityError as caught:
                self.assertIs(caught, original)
            else:
                self.fail('sqlite exception suppressed')
            self.assertEqual(diag.records[0]['sqlite_errorcode'], sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY)
            self.assertEqual(diag.records[0]['sqlite_errorname'], 'SQLITE_CONSTRAINT_PRIMARYKEY')
        finally:
            db.close()

    def test_forged_sqlite_attributes_are_omitted(self):
        exc = sqlite3.OperationalError('secret')
        exc.sqlite_errorcode = object()
        exc.sqlite_errorname = object()
        record = d.safe_record('test.sqlite', exc)
        self.assertIsNone(record['sqlite_errorcode'])
        self.assertIsNone(record['sqlite_errorname'])

    def test_context_body_exception_is_same_and_cleanup_runs(self):
        diag = d.Diagnostics()
        original = RuntimeError('secret')
        value = object()
        closed = []
        @contextmanager
        def source():
            try:
                yield value
            finally:
                closed.append(True)
        try:
            with diag.context('test.context', source)() as observed:
                self.assertIs(observed, value)
                raise original
        except RuntimeError as caught:
            self.assertIs(caught, original)
        else:
            self.fail('exception suppressed')
        self.assertEqual(closed, [True])
        self.assertEqual(diag.records[0]['stage'], 'test.context')

    def test_context_entry_and_exit_exception_identity(self):
        for phase in ('entry', 'exit'):
            with self.subTest(phase=phase):
                diag = d.Diagnostics()
                original = OSError('secret')
                @contextmanager
                def source():
                    if phase == 'entry':
                        raise original
                    yield object()
                    raise original
                try:
                    with diag.context('test.context', source)():
                        pass
                except OSError as caught:
                    self.assertIs(caught, original)
                else:
                    self.fail('exception suppressed')
                self.assertEqual(len(diag.records), 1)

    def test_diagnostics_are_bounded_and_do_not_wait_for_lock(self):
        diag = d.Diagnostics()
        for _ in range(70):
            diag.record('test.cap', RuntimeError())
        self.assertEqual(len(diag.records), 64)
        self.assertEqual(diag.dropped, 6)
        diag.lock.acquire()
        try:
            diag.record('test.cap', RuntimeError())
        finally:
            diag.lock.release()
        self.assertEqual(diag.dropped, 7)

    def test_diagnostic_failure_does_not_replace_original(self):
        diag = d.Diagnostics()
        diag.records = None
        original = LookupError('secret')
        def fail():
            raise original
        try:
            diag.wrap('test.call', fail)()
        except LookupError as caught:
            self.assertIs(caught, original)
        else:
            self.fail('exception suppressed')

        import socket
        import sys
        self.assertIsNotNone(socket.socket)
        with self.assertRaisesRegex(RuntimeError, '^external_execution_blocked$'):
            socket.socket()  # Audit denial occurs before the OS socket is created.
        for event in ('socket.__new__', 'socket.connect', 'socket.bind',
                      'socket.sendto', 'socket.getaddrinfo', 'socket.listen'):
            with self.subTest(event=event):
                # Exercise the installed audit matcher without a network syscall.
                with self.assertRaisesRegex(RuntimeError, '^external_execution_blocked$'):
                    sys.audit(event)


class DerivationTests(unittest.TestCase):
    def rows(self):
        import copy
        root='/synthetic';old={root+'/old/'+str(i)+'.py':{'bytes':i,'sha256':'a'*64} for i in range(11)}
        new={root+'/new/'+str(i)+'.py':dict(v) for i,v in enumerate(old.values())}
        rest={root+'/stable/'+str(i):{'bytes':i,'sha256':'b'*64} for i in range(3162)}
        def cfg(h,path):return {'paths':{'harness_root':path},'inventory':{'harness_files':h,
            'hash_inventory':[{'path':p,**v} for p,v in {**rest,**h}.items()]}}
        a=cfg(old,root+'/old');b=cfg(new,root+'/new')
        rows=[{'old':{'path':p,**old[p]},'new':{'path':root+'/new/'+Path(p).name,**new[root+'/new/'+Path(p).name]}} for p in sorted(old)]
        return a,b,rows
    def test_exact_eleven_and_3162_preserved(self):
        from launch import derivative_rows
        a,b,rows=self.rows();self.assertEqual(len(derivative_rows(a,b,rows)),3162)
        self.assertEqual(len(a['inventory']['hash_inventory']),3173)
    def test_changed_non_source_row_refused(self):
        from launch import derivative_rows
        a,b,rows=self.rows();next(r for r in b['inventory']['hash_inventory'] if '/stable/' in r['path'])['sha256']='c'*64
        with self.assertRaises(d.Refused):derivative_rows(a,b,rows)
    def test_missing_duplicate_or_stale_source_pin_refused(self):
        import copy
        from launch import derivative_rows
        a,b,rows=self.rows()
        for changed in (rows[:-1],rows+[rows[0]],copy.deepcopy(rows)):
            if len(changed)==11:changed[0]['new']['sha256']='d'*64
            with self.assertRaises(d.Refused):derivative_rows(a,b,changed)
        b['inventory']['harness_files']=a['inventory']['harness_files']
        with self.assertRaises((d.Refused,KeyError)):derivative_rows(a,b,rows)
    def test_diagnostic_emission_and_record_failure_preserve_exception(self):
        from types import SimpleNamespace
        diag=d.Diagnostics();original=ValueError('secret')
        def fail():raise original
        def sink(*a,**k):raise RuntimeError('sink')
        try:diag.wrap('test.sink',fail)()
        except ValueError as error:self.assertIs(error,original)
        diag.flush(SimpleNamespace(emit=sink))
        self.assertEqual(diag.flushed,1)  # Lossy best-effort emission never retries.
        quiet=d.Diagnostics()
        @contextmanager
        def suppress():
            try:yield object()
            except ValueError:pass
        with quiet.context('test.suppression',suppress)():raise original
        self.assertEqual(quiet.records,[])
        diag.record=sink
        try:diag.wrap('test.broken_record',fail)()
        except ValueError as error:self.assertIs(error,original)
        else:self.fail('primary replaced')
        @contextmanager
        def entered():yield object()
        try:
            with diag.context('test.broken_context',entered)():raise original
        except ValueError as error:self.assertIs(error,original)
        else:self.fail('context primary replaced')
    def test_separate_actual_authority_and_current_descriptor_refusal(self):
        import binding,launch
        config={'paths':{'output_dir':'/synthetic/new/run-fixture0'}};raw=b'fresh'
        authority={'schema':binding.AUTHORITY_SCHEMA,'authorized':True,'config_sha256':binding.sha(raw),
            'scope':binding.SCOPE,'run_directory':config['paths']['output_dir']}
        binding.validate_authority(authority,raw,config)
        for key,value in [('authorized',False),('config_sha256','a'*64),('run_directory','/synthetic/old/run')]:
            changed=dict(authority);changed[key]=value
            with self.assertRaises(d.Refused):binding.validate_authority(changed,raw,config)
        from unittest.mock import patch
        with patch.object(launch,'ordinary_path',side_effect=d.Refused('changed_identity')):
            with self.assertRaises(d.Refused):launch.descriptor_digest([{'path':'/synthetic/file','bytes':1}])

class BoundedResult(unittest.TextTestResult):
    def startTest(self,test):check();super().startTest(test)
    def stopTest(self,test):super().stopTest(test);check()

suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(DiagnosticTests),
    unittest.defaultTestLoader.loadTestsFromTestCase(DerivationTests)])
need(suite.countTestCases()==14,'exact_fourteen_cases')
result=unittest.TextTestRunner(verbosity=1,resultclass=BoundedResult).run(suite)
check();signal.alarm(0)
need(threading.enumerate()==[threading.main_thread()],'focused_threads_cleanup')
report={'schema':'fresh-seam-diagnostic-focused-result-v1','tests_run':result.testsRun,'failures':len(result.failures),
    'errors':len(result.errors),'skips':len(result.skipped),'provider_calls':0,'harness_manifest':harness_ref,'owner_manifest':owner_ref,
    'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,'elapsed_seconds':time.monotonic()-START,
    'cleanup':'MAIN_THREAD_ONLY_NO_CHILDREN','old_acceptances_relabelled':False}
raw=(json.dumps(report,sort_keys=True,separators=(',',':'))+'\n').encode();need(len(raw)<=65536,'focused_report_cap')
need(not OUTPUT.exists(),'fresh_focused_output');OUTPUT.mkdir(mode=0o700)
fd=os.open(OUTPUT/'result.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:need(os.write(fd,raw)==len(raw),'focused_report_write');os.fsync(fd)
finally:os.close(fd)
need(time.monotonic()<END,'focused_absolute_end');print(raw.decode(),end='',flush=True)
raise SystemExit(0 if result.wasSuccessful() and result.testsRun==14 else 1)
