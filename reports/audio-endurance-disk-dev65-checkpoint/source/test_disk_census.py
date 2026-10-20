"""Finite synthetic controls; no actual run root, model, API or database."""
import copy
import errno
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from disk_census import Budget,MIB

class Stop(RuntimeError):pass

def rows(*values):
    return {(name,1,index,stat.S_IFREG):(size,logs,bucket) for index,(name,size,logs,bucket) in enumerate(values,1)}

class DiskCensusControls(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'run';self.root.mkdir()
        self.plan=SimpleNamespace(disk_bytes=2*1024**3,disk_floor=0)
        self.budget=Budget(self.root,self.plan,stop_type=Stop)
    def tearDown(self):self.budget.close();self.temp.cleanup()
    def sample(self,data,gaps=()):
        with patch.object(self.budget,'_walk',return_value=(data,list(gaps))):return self.budget.census()
    def baseline(self):self.sample(rows(('old',100,10,None)))
    def gap(self):return [{'path':'old','stage':'entry-absent'}]
    def test_clean_real_tree_counts_all_files_and_logs(self):
        d=self.root/'nested';d.mkdir();(d/'worker.stdout').write_bytes(b'x'*200);(d/'ordinary').write_bytes(b'y'*300)
        value=self.budget.census();self.assertGreaterEqual(value,500);self.assertEqual(self.budget.log_bytes,200)
    def test_new_empty_log_growth_second_read_charged(self):
        p=self.root/'worker.stderr';p.write_bytes(b'');original=os.stat;calls=0
        def observed(path,*args,**kwargs):
            nonlocal calls
            result=original(path,*args,**kwargs)
            if path=='worker.stderr' and kwargs.get('dir_fd')==self.budget.root_fd:
                calls+=1
                if calls==1:p.write_bytes(b'x'*123)
            return result
        with patch('disk_census.os.stat',side_effect=observed):self.budget.census()
        self.assertEqual(self.budget.log_bytes,123)
    def test_known_deleted_entry_retains_aggregate(self):
        self.baseline();when=self.budget.last_time
        self.assertEqual(self.sample({},self.gap()),100);self.assertEqual(self.budget.last_time,when)
    def test_new_file_and_deleted_old_are_additive(self):
        self.baseline();self.assertEqual(self.sample(rows(('new',300,0,None)),self.gap()),400)
    def test_new_partial_read_then_absent_keeps_bytes(self):
        self.baseline();data=rows(('old',100,10,None),('new',800,0,None))
        self.assertEqual(self.sample(data,[{'path':'new','stage':'file-postread-absent'}]),900)
    def test_reservations_survive_gaps(self):
        self.baseline();self.budget.admit(50);self.assertEqual(self.sample(rows(('new',300,0,None)),self.gap()),450)
        self.assertEqual(self.budget.reserved,50)
    def test_repeat_identity_increment_not_double_counted(self):
        self.baseline();data=rows(('new',300,20,None))
        first=self.sample(data,self.gap());self.assertEqual(self.sample(data,self.gap()),first)
    def test_replacement_identity_retains_both(self):
        self.baseline();a={('new',1,50,stat.S_IFREG):(300,20,None)};b={('new',1,51,stat.S_IFREG):(400,30,None)}
        self.sample(a,self.gap());self.assertEqual(self.sample(b,self.gap()),800)
    def test_existing_file_growth_positive_increment(self):
        self.baseline();self.assertEqual(self.sample(rows(('old',500,50,None)),self.gap()),500)
    def test_decrease_never_reduces_episode_charge(self):
        self.baseline();self.sample(rows(('old',500,50,None)),self.gap())
        self.assertEqual(self.sample(rows(('old',20,1,None)),self.gap()),500)
    def test_initial_gap_refused(self):
        with self.assertRaisesRegex(Stop,'initial'):self.sample({},self.gap())
    def test_stale_gap_never_refreshes_last_clean(self):
        self.baseline();self.budget.last_time=time.monotonic()-16;old=self.budget.last_time
        with self.assertRaisesRegex(Stop,'stale'):self.sample({},self.gap())
        self.assertEqual(self.budget.last_time,old)
    def test_clean_recovery_checks_retained_before_clear(self):
        self.baseline();self.sample(rows(('new',300,0,None)),self.gap());self.plan.disk_bytes=64*MIB+399
        with self.assertRaisesRegex(Stop,'ceiling'):self.sample({})
        self.assertIsNotNone(self.budget._episode)
    def test_clean_recovery_clears_only_after_success(self):
        self.baseline();self.budget.admit(50);self.sample(rows(('new',300,0,None)),self.gap())
        self.sample(rows(('remaining',20,2,None)));self.assertEqual(self.budget.last_bytes,20);self.assertEqual(self.budget.reserved,0)
        self.assertEqual(self.budget.recovery_count,1);self.assertIsNone(self.budget._episode)
    def test_unknown_large_log_refused(self):
        self.baseline()
        with self.assertRaisesRegex(Stop,'log cap'):self.sample(rows(('new',300*MIB,300*MIB,None)),self.gap())
    def test_disk_ceiling_includes_new_partial_allocation(self):
        self.baseline();self.plan.disk_bytes=64*MIB+399
        with self.assertRaisesRegex(Stop,'ceiling'):self.sample(rows(('new',300,0,None)),self.gap())
    def test_free_floor_refused(self):
        self.plan.disk_floor=1
        with patch('disk_census.os.fstatvfs',return_value=SimpleNamespace(f_bavail=0,f_frsize=4096)):
            with self.assertRaisesRegex(Stop,'floor'):self.budget.census()
    def test_symlink_refused(self):
        (self.root/'link').symlink_to('/tmp')
        with self.assertRaisesRegex(Stop,'symlink'):self.budget.census()
    def test_special_file_refused(self):
        os.mkfifo(self.root/'pipe')
        with self.assertRaisesRegex(Stop,'special'):self.budget.census()
    def test_scandir_permission_error_is_not_suppressed(self):
        with patch('disk_census.os.scandir',side_effect=PermissionError(errno.EACCES,'denied')):
            with self.assertRaises(PermissionError):self.budget.census()
        self.assertEqual(self.budget.refusal_count,1)
    def test_scandir_eio_is_not_suppressed(self):
        with patch('disk_census.os.scandir',side_effect=OSError(errno.EIO,'I/O')):
            with self.assertRaises(OSError):self.budget.census()
    def test_root_replacement_refused(self):
        self.root.rename(self.root.with_name('moved'));self.root.mkdir()
        with self.assertRaisesRegex(Stop,'binding changed'):self.budget.census()
    def test_entry_cap(self):
        (self.root/'one').write_bytes(b'x')
        with patch('disk_census.ENTRY_CAP',0):
            with self.assertRaisesRegex(Stop,'entry cap'):self.budget.census()
    def test_depth_cap(self):
        (self.root/'one').mkdir()
        with patch('disk_census.DEPTH_CAP',0):
            with self.assertRaisesRegex(Stop,'depth cap'):self.budget.census()
    def test_sparse_file_charges_logical_size(self):
        p=self.root/'sparse';p.touch();os.truncate(p,10*MIB)
        self.assertGreaterEqual(self.budget.census(),10*MIB)
    def test_ledger_buckets_share_census(self):
        p=self.root/'output-old';p.mkdir();(p/'events.jsonl').write_text('{}\n')
        self.budget.census();self.assertGreaterEqual(self.budget.ledger_bytes['events.jsonl'],3)
    def test_diagnostics_do_not_reenter_admission(self):
        self.budget.close();self.budget=Budget(self.root,self.plan,self.root/'disk.jsonl',stop_type=Stop);self.baseline()
        with patch.object(self.budget,'admit',side_effect=AssertionError('recursive')):self.sample({},self.gap())
        records=[json.loads(x) for x in (self.root/'disk.jsonl').read_text().splitlines()]
        self.assertEqual(records[0]['event'],'gap');self.assertEqual(records[0]['path'],'old')
    def test_diagnostic_identity_replacement_refused(self):
        self.budget.close();p=self.root/'disk.jsonl';self.budget=Budget(self.root,self.plan,p,stop_type=Stop);self.baseline()
        p.rename(self.root/'old.jsonl');p.write_text('')
        with self.assertRaisesRegex(Stop,'diagnostic identity'):self.sample({},self.gap())
    def test_invalid_growth_refused(self):
        for value in (-1,True,1.5):
            with self.subTest(value=value),self.assertRaises(Stop):self.budget.admit(value)
    def test_admission_preserves_reservation_when_stale(self):
        self.baseline();self.budget.reserved=50;self.budget.last_time=time.monotonic()-6
        with patch.object(self.budget,'_walk',return_value=({},self.gap())):self.budget.admit(20)
        self.assertEqual(self.budget.reserved,70)
    def test_atomic_regular_replacement_is_charged_gap(self):
        p=self.root/'checkpoint.json';p.write_bytes(b'x'*100);self.budget.census();prior=self.budget.last_bytes
        replacement=self.root/'next.tmp';replacement.write_bytes(b'y'*9000);original=os.stat;calls=0
        def observed(path,*args,**kwargs):
            nonlocal calls
            result=original(path,*args,**kwargs)
            if path=='checkpoint.json' and kwargs.get('dir_fd')==self.budget.root_fd:
                calls+=1
                if calls==1:os.replace(replacement,p)
            return result
        with patch('disk_census.os.stat',side_effect=observed):value=self.budget.census()
        self.assertGreaterEqual(value,prior+9000);self.assertGreater(self.budget.gap_count,0)
    def test_regular_reappearance_is_charged_gap(self):
        self.budget.census();p=self.root/'new.json';p.write_bytes(b'x'*500);original=os.stat;calls=0
        def observed(path,*args,**kwargs):
            nonlocal calls
            if path=='new.json' and kwargs.get('dir_fd')==self.budget.root_fd:
                calls+=1
                if calls==1:raise FileNotFoundError(errno.ENOENT,'synthetic leaf gap')
            return original(path,*args,**kwargs)
        with patch('disk_census.os.stat',side_effect=observed):self.budget.census()
        self.assertGreater(self.budget.gap_count,0);self.assertGreaterEqual(self.budget.last_bytes,500)
    def test_regular_replacement_by_symlink_refused(self):
        p=self.root/'checkpoint.json';p.write_bytes(b'x');self.budget.census();original=os.stat;calls=0
        def observed(path,*args,**kwargs):
            nonlocal calls
            result=original(path,*args,**kwargs)
            if path=='checkpoint.json' and kwargs.get('dir_fd')==self.budget.root_fd:
                calls+=1
                if calls==1:p.unlink();p.symlink_to('/tmp')
            return result
        with patch('disk_census.os.stat',side_effect=observed),self.assertRaisesRegex(Stop,'type/device'):self.budget.census()
    def test_directory_parent_measurement_is_not_overwritten(self):
        (self.root/'child').mkdir();original=os.stat
        def observed(path,*args,**kwargs):
            value=original(path,*args,**kwargs)
            if path=='child' and kwargs.get('dir_fd')==self.budget.root_fd:
                return SimpleNamespace(st_dev=value.st_dev,st_ino=value.st_ino,st_mode=value.st_mode,st_size=5*MIB,st_blocks=value.st_blocks)
            return value
        with patch('disk_census.os.stat',side_effect=observed):value=self.budget.census()
        self.assertGreaterEqual(value,5*MIB)
    def test_empty_final_scan_duration_bound(self):
        ticks=iter([0.,0.,6.])
        def clock():return next(ticks,6.)
        with patch('disk_census.time.monotonic',side_effect=clock),self.assertRaisesRegex(Stop,'duration'):self.budget.census()
        self.assertGreater(self.budget.summary()['observed_bytes'],0)
    def test_output_child_diagnostic_supported_and_bound(self):
        self.budget.close();directory=self.root/'output-new';directory.mkdir()
        self.budget=Budget(self.root,self.plan,directory/'disk-census.jsonl',stop_type=Stop);self.baseline();self.sample({},self.gap())
        self.assertTrue((directory/'disk-census.jsonl').read_bytes())
        directory.rename(self.root/'renamed');directory.mkdir()
        with self.assertRaisesRegex(Stop,'binding changed'):self.budget.census()
    def test_gap_diagnostics_identify_known_and_unknown_bytes(self):
        self.baseline();self.sample({},self.gap());row=self.budget.last_diagnostic
        self.assertEqual(row['prior_identities'][0]['allocated'],100);self.assertIsNone(row['readable_identities']);self.assertFalse(row['unmeasured'])
        self.sample({},[{'path':'never-measured','stage':'entry-absent'}]);self.assertTrue(self.budget.last_diagnostic['unmeasured'])
    def test_diagnostic_failure_is_reported_in_summary(self):
        with patch.object(self.budget,'_walk',side_effect=PermissionError('denied')),patch.object(self.budget,'_record',side_effect=OSError('diagnostic denied')):
            with self.assertRaises(PermissionError):self.budget.census()
        self.assertIn('diagnostic denied',self.budget.summary()['diagnostic_write_error'])
    def test_summary_separates_readable_and_retained_charge(self):
        self.baseline();self.sample(rows(('new',300,0,None)),self.gap());summary=self.budget.summary()
        self.assertEqual(summary['observed_bytes'],300);self.assertEqual(summary['retained_charge'],400)

    def test_terminal_reserve_zero_preserves_diagnostic_carveout(self):
        self.budget.close();self.budget=Budget(self.root,self.plan,self.root/'disk.jsonl',stop_type=Stop);self.baseline()
        self.plan.disk_bytes=8*MIB+100+19
        with self.assertRaisesRegex(Stop,'admission ceiling'):self.budget.admit(20,reserve=0)
    def test_diagnostic_carveout_does_not_stack_on_normal_reserve(self):
        self.budget.close();self.budget=Budget(self.root,self.plan,self.root/'disk.jsonl',stop_type=Stop);self.baseline()
        self.plan.disk_bytes=64*MIB+100+20
        self.budget.admit(20);self.assertEqual(self.budget.reserved,20)
