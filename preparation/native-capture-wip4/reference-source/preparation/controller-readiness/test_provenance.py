"""Prepared model-free provenance fixtures. Never loads a provider or actual assets."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'controller'))
import bootstrap as b
import os
import json
import tempfile
import types
import unittest
from unittest.mock import patch
from common import Refused, sha, canonical


class Deadline:
    def __init__(self):
        self.now=100.;self.work=700.;self.posthash=820.;self.guard_work=820.;self.absolute=880.
        self.stop=Path('/deliberately-absent-provenance-fixture-stop')
        self.clock=lambda:self.now
    def check(self,end=None):
        if self.now>=min(self.work,end if end is not None else self.work):raise Refused('fixture_work_deadline')
    def call_end(self,maximum):self.check();return min(self.work,self.now+maximum)


def line(path, inode=None):
    st=path.stat()
    return f'1000-2000 r--p 00000000 {os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x} {st.st_ino if inode is None else inode} {path}\n'.encode()


class MappingFixtures(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.path=self.root/'file';self.path.write_bytes(b'abc')
        self.d=Deadline();self.spec={'hash_inventory':[self.pin(self.path)]}
        self.maps=self.root/'maps';self.maps.write_bytes(line(self.path))
    def pin(self,path):return {'path':str(path),'bytes':path.stat().st_size,'sha256':sha(path.read_bytes())}
    def observe(self,identity=lambda:None,end=500.):return b.observed_mappings(self.spec,self.d,self.maps,identity,end)
    def test_duplicate_segments_one_file(self):
        raw=line(self.path)*3+b'3000-4000 rw-p 00000000 00:00 0 [heap]\n'
        self.assertEqual(len(b.mapped_files(raw,lambda:None)),1)
    def test_maps_text_cap(self):
        with self.assertRaisesRegex(Refused,'mapping_text_cap'):b.mapped_files(b'x'*(b.MAP_BYTES+1),lambda:None)
    def test_distinct_file_count_cap(self):
        raw=b''.join(f'1000-2000 r--p 0 01:01 {i+1} /fixture/{i}\n'.encode() for i in range(257))
        with self.assertRaisesRegex(Refused,'mapped_file_count'):b.mapped_files(raw,lambda:None)
    def test_path_byte_cap(self):
        with self.assertRaisesRegex(Refused,'mapping_path_cap'):
            b.mapped_files(b'1000-2000 r--p 0 01:01 1 /'+b'x'*4096+b'\n',lambda:None)
    def test_deleted_mapping_refused(self):
        with self.assertRaisesRegex(Refused,'deleted_mapped_file'):
            b.mapped_files(line(self.path).rstrip()+b' (deleted)\n',lambda:None)
    def test_nonpath_file_mapping_refused(self):
        with self.assertRaisesRegex(Refused,'unknown_nonpath_mapping'):
            b.mapped_files(b'1000-2000 r--p 0 01:01 1 memfd:name\n',lambda:None)
    def test_malformed_row_refused(self):
        with self.assertRaises(Refused):b.mapped_files(b'malformed\n',lambda:None)
    def test_unknown_existing_file_refused(self):
        other=self.root/'other';other.write_bytes(b'x');self.spec['hash_inventory']=[self.pin(other)]
        with self.assertRaisesRegex(Refused,'unknown_mapped_file'):self.observe()
    def test_kernel_inode_mismatch(self):
        self.maps.write_bytes(line(self.path,1))
        with self.assertRaisesRegex(Refused,'mapped_kernel_identity_changed'):self.observe()
    def test_streamed_paging_and_identity_boundaries(self):
        self.path.write_bytes(b'x'*131073);self.spec['hash_inventory']=[self.pin(self.path)]
        calls=[];reads=[];read=os.read
        def page(fd,size):reads.append(size);return read(fd,size)
        with patch.object(b.os,'read',side_effect=page):result=self.observe(lambda:calls.append('identity'))
        self.assertEqual(result['hashed_bytes'],131073);self.assertEqual(len(calls),2)
        self.assertGreaterEqual(len(reads),6);self.assertLessEqual(max(reads),65536)
        self.assertEqual(result['files'][0]['sha256'],sha(b'x'*131073))
    def test_wrong_hash_refused(self):
        self.spec['hash_inventory'][0]['sha256']='0'*64
        with self.assertRaisesRegex(Refused,'mapped_file_pin'):self.observe()
    def test_total_hash_bytes_finite(self):
        other=self.root/'other';other.write_bytes(b'xyz')
        self.spec['hash_inventory'].append(self.pin(other));self.maps.write_bytes(line(self.path)+line(other))
        with patch.object(b,'MAP_HASH_BYTES',5),self.assertRaisesRegex(Refused,'mapped_total_byte_cap'):self.observe()
    def test_mutation_between_blocks_refused(self):
        read=os.read
        def changed(fd,size):
            raw=read(fd,size)
            if os.fstat(fd).st_ino==self.path.stat().st_ino and raw:
                with self.path.open('ab') as out:out.write(b'!')
            return raw
        with patch.object(b.os,'read',side_effect=changed),self.assertRaisesRegex(Refused,'mapped_file_changed'):self.observe()
    def test_registered_alias_checked(self):
        alias=self.root/'alias';alias.symlink_to('file');self.maps.write_bytes(line(alias))
        self.spec['runtime_aliases']=[{'path':str(alias),'literal_target':'file','resolved_target':str(self.path)}]
        self.assertEqual(self.observe()['files'][0]['pinned_path'],str(self.path))
    def test_unlisted_alias_refused(self):
        alias=self.root/'alias';alias.symlink_to('file');self.maps.write_bytes(line(alias))
        with self.assertRaisesRegex(Refused,'unlisted_mapping_alias'):self.observe()
    def test_expired_call_never_reads(self):
        with patch.object(b,'bounded_read') as read,self.assertRaisesRegex(Refused,'deadline'):self.observe(end=100.)
        read.assert_not_called()
    def test_snapshot_clips_to_remaining_call(self):self.assertEqual(self.observe(end=108.)['deadline'],108.)
    def test_accounting_reserves_sixty_seconds(self):
        self.d.now=self.d.absolute-60
        with self.assertRaisesRegex(Refused,'mapping_deadline'):b.accounting_check(self.d)
    def test_each_read_rechecks_deadline(self):
        read=os.read
        def late(fd,size):
            raw=read(fd,size)
            if os.fstat(fd).st_ino==self.path.stat().st_ino:self.d.now=116.
            return raw
        with patch.object(b.os,'read',side_effect=late),self.assertRaisesRegex(Refused,'deadline'):self.observe()
    def test_result_byte_cap(self):
        with patch.object(b,'TERMINAL_BYTES',1),self.assertRaisesRegex(Refused,'mapping_result_cap'):self.observe()
    def test_nonregular_file_refused(self):
        self.maps.write_bytes(line(self.root));st=self.root.stat()
        self.spec['hash_inventory']=[{'path':str(self.root),'bytes':st.st_size,'sha256':'0'*64}]
        with self.assertRaisesRegex(Refused,'mapped_file_shape'):self.observe()
    def test_reader_no_follow_and_cap(self):
        alias=self.root/'alias';alias.symlink_to('maps')
        with self.assertRaises(OSError):b.bounded_read(alias,65536)
        with self.assertRaises(Refused):b.bounded_read(self.maps,1)


class FinalizationFixtures(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.calls=[];self.receipts={};self.d=Deadline()
        self.spec={'output_dir':str(self.root),'_sha256':'a'*64,'python':'/fixture/python','allowed_programs':[]}
        self.ledger=types.SimpleNamespace(originals=[],cleanup=lambda path:self.clean(path))
        self.journal=types.SimpleNamespace(emit=lambda *a,**kw:self.calls.append('journal'))
        self.instrument=types.SimpleNamespace(summary=lambda:{'original_counts':1})
        self.stack=[]
        for context in (patch.object(b,'terminal_observation',side_effect=lambda *a:self.observe()),
                        patch.object(b,'atomic_json',side_effect=lambda p,v,*a:self.write(p,v))):
            context.start();self.addCleanup(context.stop)
    def observe(self):self.calls.append('snapshot');return {'status':'PATHS_OBSERVED_NOT_REHASHED'}
    def clean(self,path):
        self.calls.append('cleanup')
        return {'cleanup':'CONFIRMED','originals':[{'pid':p.pid,'original_waited':True,'returncode':p.returncode}
                                                 for p in self.ledger.originals]}
    def write(self,path,value):self.calls.append(Path(path).name);self.receipts[Path(path).name]=value
    def run_final(self,error=None,mode='native',live=None):
        return b.finalize(self.spec,self.d,self.journal,self.ledger,mode,self.instrument,error,live)
    def result(self,mode='native'):return self.receipts[f'{mode}-{os.getpid()}-terminal.json']
    def test_counts_failure_still_snapshots_and_cleans(self):
        error=RuntimeError('counts write failed');write=self.write
        def fail(path,value):
            if str(path).endswith('-counts.json'):raise error
            return write(path,value)
        self.write=fail
        with self.assertRaises(RuntimeError) as got:self.run_final()
        self.assertIs(got.exception,error);self.assertEqual(self.calls.count('cleanup'),1)
        self.assertIn('snapshot',self.calls);self.assertEqual(self.result()['failures'][0]['stage'],'counts')
    def test_mapping_failure_keeps_counts_cleanup_and_fails(self):
        error=Refused('unknown_mapped_file');self.observe=lambda:(_ for _ in ()).throw(error)
        with self.assertRaises(Refused) as got:self.run_final()
        self.assertIs(got.exception,error);self.assertEqual(self.calls.count('cleanup'),1)
        self.assertIn(f'native-{os.getpid()}-counts.json',self.calls)
    def test_original_exception_identity_with_multiple_failures(self):
        original=ValueError('producer original')
        self.observe=lambda:(_ for _ in ()).throw(Refused('mapping failed'))
        self.clean=lambda path:(_ for _ in ()).throw(OSError('cleanup failed'))
        def run():
            try:raise original
            finally:self.run_final(original)
        with self.assertRaises(ValueError) as got:run()
        self.assertIs(got.exception,original)
        self.assertEqual(self.result()['producer_error']['detail'],'producer original')
        self.assertEqual([r['stage'] for r in self.result()['failures']],
                         ['terminal_observation','owned_cleanup','owned_cleanup_status'])
    def test_cleanup_unknown_fails_otherwise_good(self):
        self.clean=lambda path:{'cleanup':'UNKNOWN'}
        with self.assertRaisesRegex(RuntimeError,'UNKNOWN'):self.run_final()
    def test_journal_failure_cannot_skip_cleanup(self):
        error=OSError('journal failed');self.journal.emit=lambda *a,**kw:(_ for _ in ()).throw(error)
        with self.assertRaises(OSError) as got:self.run_final()
        self.assertIs(got.exception,error);self.assertEqual(self.calls.count('cleanup'),1)
    def child(self):
        original=types.SimpleNamespace(pid=123456789,returncode=-15,
            args=[self.spec['python'],'-I','-B','-S',str(b.HERE/'bootstrap.py'),'--mode','native'])
        self.ledger.originals=[original];return original
    def test_missing_signaled_terminal_is_explicit_gap(self):
        child=self.child();live=types.SimpleNamespace(observations={'native':{'status':'VERIFIED','pid':child.pid}})
        self.run_final(mode='server',live=live)
        self.assertEqual(self.result('server')['children'][0]['terminal'],'UNOBSERVED')
        self.assertEqual(self.result('server')['status'],'COMPLETE')
    def test_missing_terminal_wrong_live_pid_fails(self):
        child=self.child();live=types.SimpleNamespace(observations={'native':{'status':'VERIFIED','pid':child.pid+1}})
        with self.assertRaisesRegex(Refused,'missing_child_terminal'):self.run_final(mode='server',live=live)
    def test_missing_terminal_without_live_receipt_fails(self):
        self.child()
        with self.assertRaisesRegex(Refused,'missing_child_terminal'):self.run_final(mode='server')
    def test_missing_normal_exit_terminal_fails(self):
        child=self.child();child.returncode=0
        live=types.SimpleNamespace(observations={'native':{'status':'VERIFIED','pid':child.pid}})
        with self.assertRaisesRegex(Refused,'missing_child_terminal'):self.run_final(mode='server',live=live)
    def test_observed_child_failure_propagates(self):
        child=self.child();(self.root/f'native-{child.pid}-terminal.json').write_bytes(canonical(
            {'schema':'terminal-python-provenance-v1','status':'INCOMPLETE'}))
        with self.assertRaisesRegex(Refused,'child_provenance_failed'):self.run_final(mode='server')
        self.assertEqual(self.calls.count('cleanup'),1)
    def test_required_live_observation_never_becomes_gap(self):
        self.spec['_server_phase']='capture'
        with self.assertRaisesRegex(Refused,'required_live_observation'):self.run_final(mode='server')
    def test_optional_terminal_deadline_is_honest_gap(self):
        self.observe=lambda:(_ for _ in ()).throw(Refused('mapping_deadline'))
        self.run_final();self.assertEqual(self.result()['mappings']['status'],'UNOBSERVED')
    def test_duplicate_live_observation_refused_before_work(self):
        live=b.LiveProvenance(self.spec,self.d,self.ledger);live.observations['native']={}
        with self.assertRaisesRegex(Refused,'live_observation_count'):live.observe('native',object(),120.)
    def test_unowned_live_original_refused(self):
        with self.assertRaisesRegex(Refused,'live_unowned_original'):
            b.LiveProvenance(self.spec,self.d,self.ledger).observe('native',object(),120.)
    def test_live_role_bound_to_original_argv(self):
        child=self.child()
        with self.assertRaisesRegex(Refused,'live_original_role_mismatch'):
            b.LiveProvenance(self.spec,self.d,self.ledger).observe('analyzer',child,120.)


class ServerBoundaryFixtures(unittest.TestCase):
    def exercise(self,role,producer_error=None,mapping_error=None):
        import server
        from common import Admission
        calls=[];value={'original':'same object'};original=object()
        class Native:
            def request(obj,*a,**kw):
                calls.append('original')
                if producer_error is not None:raise producer_error
                return value
        class Analyzer:
            def analyze(obj,*a,**kw):
                calls.append('original')
                if producer_error is not None:raise producer_error
                return value
        class Store:
            def commit_window(self,*a):return None
        class Witness:
            def finalize(self,*a):return None
        class Worker:
            def start(self):return None
        modules={'audio_backend':types.ModuleType('audio_backend')}
        for name,symbol,cls in [('native_worker','NativeProcess',Native),('resources','AnalyzerProcess',Analyzer),
                                ('storage','Store',Store),('native_owned_support','NativeWitness',Witness),
                                ('worker','Worker',Worker)]:
            module=types.ModuleType('audio_backend.'+name);setattr(module,symbol,cls);modules[module.__name__]=module
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);deadline=Deadline();hooks=server.ServerHooks.__new__(server.ServerHooks)
            def observe(which,proc,end):
                calls.append('observe');self.assertEqual(which,role);self.assertIs(proc,original)
                self.assertEqual(end,220. if role=='native' else 400.)
                if mapping_error is not None:raise mapping_error
            hooks.spec={'installed_files':{str(Path(__file__).resolve()):{'sha256':'a'*64}},
                        '_observe_live_mappings':observe,'speaker_model_sha256':'b'*64}
            hooks.deadlines=deadline;hooks.out=root;hooks.capture_allowed=True;hooks.labels=[];hooks.turns=[]
            hooks.journal=types.SimpleNamespace(emit=lambda *a,**kw:None)
            hooks.rpc=Admission({'turns':1},deadline.check)
            hooks.jobs=Admission({'analyzer':1,'commit':1,'worker_start':1,'witness_finalize':1},deadline.check)
            hooks.admission=types.SimpleNamespace(admit_raw=lambda *a,**kw:{})
            hooks.native_result=lambda action,result:None
            hooks.active=lambda *a:None
            def raw(path,name):
                calls.append('raw');(root/(name+'.raw')).write_bytes(canonical(value))
            hooks.raw=raw;hooks.raw_finally=lambda *a:calls.append('raw-finally')
            with patch.dict(sys.modules,modules),patch.object(server,'fixed_wav',return_value=({},b'fake',b'')):
                hooks.install()
                if role=='native':
                    obj=Native();obj.root=root;obj.counter=3;obj.proc=original
                    invoke=lambda:obj.request('turns',lambda:False)
                    gate=hooks.rpc;operation='turns'
                else:
                    obj=Analyzer();obj.proc=original;hooks.rpc.counts['turns']['accepted']=1
                    diarization=root/'diarization';diarization.write_bytes(b'{"turns":[]}')
                    invoke=lambda:obj.analyze(root/'unused.wav',root/'output',lambda:False,diarization_path=diarization)
                    gate=hooks.jobs;operation='analyzer'
                error=producer_error or mapping_error
                if error is not None:
                    with self.assertRaises(type(error)) as got:invoke()
                    self.assertIs(got.exception,error);self.assertEqual(gate.counts[operation]['accepted'],0)
                else:
                    self.assertIs(invoke(),value);self.assertEqual(gate.counts[operation]['accepted'],1)
            self.assertEqual(calls.count('original'),1);self.assertEqual(calls.count('raw-finally'),1)
            if producer_error is not None:self.assertNotIn('observe',calls)
            else:
                self.assertEqual(calls.count('observe'),1)
                self.assertLess(calls.index('raw-finally'),calls.index('observe'))
    def test_native_raw_before_single_observation_and_acceptance(self):self.exercise('native')
    def test_analyzer_raw_before_single_observation_and_acceptance(self):self.exercise('analyzer')
    def test_native_original_error_preserved_and_observation_skipped(self):self.exercise('native',ValueError('original'))
    def test_analyzer_original_error_preserved_and_observation_skipped(self):self.exercise('analyzer',ValueError('original'))
    def test_native_mapping_failure_prevents_acceptance(self):self.exercise('native',mapping_error=Refused('mapping'))
    def test_analyzer_mapping_failure_prevents_acceptance(self):self.exercise('analyzer',mapping_error=Refused('mapping'))
