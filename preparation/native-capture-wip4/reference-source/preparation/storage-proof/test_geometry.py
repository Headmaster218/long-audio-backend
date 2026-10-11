"""Retry accounting and installed Settings only; no Store, SQLite or models."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('finite_proof_geometry',HERE/'geometry.py')
g=importlib.util.module_from_spec(s);s.loader.exec_module(g)
c=g.c

def entry(path,kind='regular',size=1,blocks=8,ino=None,nlink=1):
    return dict(path=path,kind=kind,size=size,blocks=blocks,dev=27,
                ino=100+sum(ord(x) for x in path) if ino is None else ino,nlink=nlink)
def base():return [entry('',kind='directory',ino=1),entry('maximum_ascii',kind='directory',ino=2)]
def forbidden_actions(event,args):
    if event.startswith(('socket.','subprocess.','os.exec','os.spawn')) or event in ('os.system','os.posix_spawn','os.fork','os.forkpty','sqlite3.connect'):
        raise c.Refused('geometry fixture external action')
    if event in ('open','ctypes.dlopen') and args and isinstance(args[0],(str,bytes)):
        path=Path(os.fsdecode(args[0])).absolute()
        c.require(not any(path.is_relative_to(c.PREP/x) for x in ('models','vendor','corpus','input')),'geometry fixture asset access')

class Geometry(unittest.TestCase):
    def rejected(self,rows):
        with self.assertRaises(c.Refused):g.classify(rows)
    def test_all_allowed_partial_files_and_directories_charged(self):
        rows=base()+[entry('maximum_ascii/'+name,size=0,ino=10+i) for i,name in enumerate(g.FILES)]
        geo=g.classify(rows)
        self.assertEqual(geo['charged_bytes'],7*4096)
        self.assertEqual(geo['cases']['maximum_ascii']['charged_bytes'],6*4096)
    def test_sparse_logical_size_charged(self):
        geo=g.classify(base()+[entry('maximum_ascii/jobs.sqlite3',size=8193,blocks=0)])
        self.assertEqual(geo['charged_bytes'],8192+8193)
    def test_case_cap_exact_and_one_over(self):
        rows=base()+[entry('maximum_ascii/jobs.sqlite3',size=g.CASE_CAP-4096,blocks=0)]
        self.assertEqual(g.classify(rows)['cases']['maximum_ascii']['charged_bytes'],g.CASE_CAP)
        rows[-1]['size']+=1;self.rejected(rows)
    def test_combined_cap_exact_and_one_over(self):
        rows=base()+[entry('maximum_ascii/jobs.sqlite3',size=g.CASE_CAP-4096,blocks=0,ino=3),
             entry('maximum_utf8','directory',ino=4),entry('maximum_utf8/jobs.sqlite3',size=g.PROOF_CAP-g.CASE_CAP-8192,blocks=0,ino=5)]
        self.assertEqual(g.classify(rows)['charged_bytes'],g.PROOF_CAP)
        rows[-1]['size']+=1;self.rejected(rows)
    def test_alias_and_special_rejected(self):
        for kind in ('symlink','fifo','socket','block','character'):
            self.rejected(base()+[entry('maximum_ascii/jobs.sqlite3',kind=kind)])
    def test_hard_link_and_duplicate_inode_rejected(self):
        self.rejected(base()+[entry('maximum_ascii/jobs.sqlite3',nlink=2)])
        self.rejected(base()+[entry('maximum_ascii/jobs.sqlite3',ino=2)])
    def test_unexpected_name_and_descendants_rejected(self):
        for name in ('maximum_ascii/extra','maximum_ascii/jobs.sqlite3/extra','other/jobs.sqlite3'):
            self.rejected(base()+[entry(name)])
    def test_mount_device_rejected(self):
        rows=base();rows[-1]['dev']=28;self.rejected(rows)
    def test_missing_parent_duplicate_path_rejected(self):
        self.rejected([entry('maximum_ascii/jobs.sqlite3')])
        self.rejected(base()+[entry('maximum_ascii','directory',ino=3)])
    def test_generic_group_caps_unchanged_and_no_proof_leak(self):
        for group,cap in g.GROUP_CAPS.items():
            name='runs/run-fixture/tmp/value' if group=='temp' else group+'/value'
            self.assertEqual(g.classify_groups({name:cap})[group],cap)
            with self.assertRaises(c.Refused):g.classify_groups({name:cap+1})
        with self.assertRaises(c.Refused):g.classify_groups({'storage-proof/retained/maximum_ascii/jobs.sqlite3':1})
    def test_category_partition_and_remaining_reserve(self):
        scans=[({'build/fixture':1},4096,2),({'geometry.py':1},4096,2)]
        proof={'charged_bytes':8192,'entries':2,'root_charge':4096,'cases':{}}
        with patch.object(c,'scan',side_effect=scans),patch.object(c,'verified_category_exclusions',return_value={}),patch.object(g,'retained',return_value=proof):
            got=g.category(checker=lambda:None)
        self.assertEqual(got['charged_bytes'],16384)
        self.assertEqual(got['remaining_proof_reservation'],g.PROOF_CAP-8192)
        self.assertEqual(got['logical_groups']['bookkeeping'],1)
        self.assertEqual(got['logical_groups']['build'],1)
    def test_category_full_remaining_capacity_refused(self):
        scans=[({},c.CAP-c.RESERVE-g.PROOF_CAP+1,1),({},0,1)]
        with patch.object(c,'scan',side_effect=scans),patch.object(c,'verified_category_exclusions',return_value={}),patch.object(g,'retained',return_value=g.classify([])):
            with self.assertRaises(c.Refused):g.category(checker=lambda:None)
    def test_retry_total_includes_old_raw_and_roots(self):
        old=g.classify(base()+[entry('maximum_ascii/producer-raw.json',size=8387276,blocks=16384,ino=3)])
        left=g.PROOF_CAP-old['charged_bytes']-4096-g.CASE_CAP
        new=[entry('',kind='directory',ino=1001),entry('maximum_utf8','directory',ino=1002),
             entry('maximum_utf8/jobs.sqlite3',size=g.CASE_CAP-4096,blocks=0,ino=1003),
             entry('transaction_rollback','directory',ino=1004),
             entry('transaction_rollback/jobs.sqlite3',size=left-4096,blocks=0,ino=1005)]
        total=g.combine({'retained':old,'retained-v2':g.classify(new)})
        self.assertEqual(total['charged_bytes'],g.PROOF_CAP)
        self.assertEqual(total['attempt_charges']['retained'],8396800)
        new[-1]['size']+=1
        with self.assertRaises(c.Refused):g.combine({'retained':old,'retained-v2':g.classify(new)})
    def test_retry_percase_cap_spans_attempts(self):
        old=g.classify(base()+[entry('maximum_ascii/producer-raw.json',size=8387276,blocks=16384,ino=3)])
        available=g.CASE_CAP-old['cases']['maximum_ascii']['charged_bytes']
        new=[entry('',kind='directory',ino=1001),entry('maximum_ascii','directory',ino=1002),
             entry('maximum_ascii/jobs.sqlite3',size=available-4096,blocks=0,ino=1003)]
        total=g.combine({'retained':old,'retained-v2':g.classify(new)})
        self.assertEqual(total['cases']['maximum_ascii']['charged_bytes'],g.CASE_CAP)
        new[-1]['size']+=1
        with self.assertRaises(c.Refused):g.combine({'retained':old,'retained-v2':g.classify(new)})
    def test_settings_missing_native_paths_rejected(self):
        root=Path(os.environ['GUARD_RUN_DIRECTORY'])/'tmp'/'settings-shape-never-created'
        kwargs=p.fixture_settings_kwargs(root)
        kwargs.pop('native_diarization_model');kwargs.pop('native_diarization_library')
        with self.assertRaisesRegex(ValueError,'^Native diarization requires explicit local model and library paths$'):
            Settings(**kwargs)
        self.assertFalse(root.exists())
    def test_settings_nonexistent_native_paths_accepted_without_reads(self):
        root=Path(os.environ['GUARD_RUN_DIRECTORY'])/'tmp'/'settings-shape-never-created'
        kwargs=p.fixture_settings_kwargs(root);settings=Settings(**kwargs)
        self.assertEqual(settings.diarization_backend,'nemotron');self.assertEqual(settings.asr_backend,'sensevoice')
        self.assertFalse(settings.start_worker)
        for key in ('native_diarization_model','native_diarization_library'):
            self.assertEqual(getattr(settings,key),kwargs[key]);self.assertFalse(Path(kwargs[key]).exists())
        self.assertFalse(root.exists())
    def test_scandir_stops_at_sixth_entry(self):
        seen=[]
        class Children:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def __iter__(self):
                for number in range(100):
                    seen.append(number);yield SimpleNamespace(name=str(number))
        root_stat=SimpleNamespace(st_mode=g.stat.S_IFDIR,st_size=4096,st_blocks=8,st_dev=27,st_ino=1,st_nlink=2)
        with patch.object(c,'safe_path'),patch.object(Path,'lstat',return_value=root_stat),patch.object(g.os,'scandir',return_value=Children()):
            with self.assertRaises(c.Refused):g.scan_tree(Path('/synthetic'))
        self.assertEqual(seen,list(range(6)))
    def test_tiny_real_filesystem_classification(self):
        root=Path(os.environ['GUARD_RUN_DIRECTORY'])/'tmp'/'finite-geometry'
        root.mkdir();case=root/'maximum_ascii';case.mkdir()
        for name in g.FILES:(case/name).write_bytes(b'x')
        got=g.scan_tree(root,checker=c.check)
        paths=[root,case]+[case/name for name in g.FILES]
        expected=sum(max(x.lstat().st_size,x.lstat().st_blocks*512) for x in paths)
        self.assertEqual(got['charged_bytes'],expected)
        self.assertEqual(set(got['cases']['maximum_ascii']['files']),set(g.FILES))
        self.assertEqual(got['entries'],7)

def main():
    global p,Settings
    c.check()
    spec=importlib.util.spec_from_file_location('retry_store_settings_shape',HERE/'prove_store.py')
    p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
    install=p.bootstrap()
    sys.addaudithook(forbidden_actions)
    from audio_backend.settings import Settings
    c.require('audio_backend.storage' not in sys.modules,'Store import forbidden in readiness suite')
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Geometry)
    ids=[test.id() for test in suite]
    c.require(len(ids)==18,'fixed geometry fixture count')
    result=unittest.TextTestRunner(verbosity=2,failfast=True).run(suite)
    ok=result.wasSuccessful() and result.testsRun==18 and not result.skipped
    p.verify_environment(install);p.c.source_verify(p.c.SOURCE)
    c.require('audio_backend.storage' not in sys.modules,'Store import forbidden in readiness suite')
    sys.setprofile(None)
    c.write_json(Path(os.environ['GUARD_RUN_DIRECTORY'])/'geometry-result.json',
        {'status':'PASS' if ok else 'FAIL','ids':ids,'tests':result.testsRun,
         'installed_settings_imports':1,'store_imports':0,'model_constructors':0,'native_artifact_reads':0,'models':0,'network':0,'sqlite_connections':0,
         'fixture_files_retained':True})
    return 0 if ok else 1
if __name__=='__main__':raise SystemExit(c.cli_boundary(main))
