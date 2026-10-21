# PUBLIC SOURCE INSPECTION ONLY. This is not a runnable distribution.
"""Finite synthetic controls; no actual run root, model, API or database."""

raise RuntimeError("Inspection-only transformed excerpt: do not execute or import; private dependencies and pins are intentionally omitted.")

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

class FinalSnapshotReservationControls(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'run';self.root.mkdir()
        self.output=self.root/'output-test';self.output.mkdir();self.path=self.output/'final.sqlite3'
        self.plan=SimpleNamespace(disk_bytes=2*1024**3,disk_floor=0)
        self.budget=Budget(self.root,self.plan,self.output/'disk-census.jsonl',stop_type=Stop,final_snapshot_reserve=512*MIB)
        self.budget.census();self.base=self.budget.last_bytes
    def convert(self,path):
        if not self.budget.in_terminal:self.budget.begin_terminal()
        return self.budget.admit_final_snapshot(path)
    def test_conversion_requires_terminal_mode(self):
        with self.assertRaisesRegex(Stop,'terminal mode'):self.budget.admit_final_snapshot(self.path)
        self.assertEqual(self.budget.summary()['final_snapshot_state'],'held')
    def tearDown(self):self.budget.close();self.temp.cleanup()
    def test_reserve_exists_before_first_census_and_survives_clean(self):
        self.assertEqual(self.budget.summary()['final_snapshot_pending'],512*MIB)
        self.budget.census();self.assertEqual(self.budget.summary()['final_snapshot_pending'],512*MIB)
    def test_gap_does_not_clear_snapshot_reserve(self):
        records=dict(self.budget._records)
        with patch.object(self.budget,'_walk',return_value=(records,[dict(path='gone',stage='entry-absent')])):self.budget.census()
        self.assertEqual(self.budget.summary()['final_snapshot_pending'],512*MIB)
    def test_admit_does_not_spend_snapshot_reserve(self):
        self.budget.admit(100);self.assertEqual(self.budget.summary()['final_snapshot_pending'],512*MIB)
    def test_near_ceiling_live_refuses_before_snapshot_space_consumed(self):
        self.plan.disk_bytes=self.base+512*MIB+64*MIB+99
        with self.assertRaisesRegex(Stop,'admission ceiling'):self.budget.admit(100)
    def test_live_free_floor_protects_snapshot_and_terminal_margin(self):
        available=512*MIB+64*MIB-1
        with patch('disk_census.os.fstatvfs',return_value=SimpleNamespace(f_bavail=available,f_frsize=1)),self.assertRaisesRegex(Stop,'floor'):self.budget.admit(0)
    def test_conversion_does_not_double_charge(self):
        self.plan.disk_bytes=self.base+512*MIB+64*MIB
        self.convert(self.path)
        self.assertEqual(self.budget.summary()['final_snapshot_pending'],512*MIB)
        self.assertEqual(self.budget.reserved,0)
    def test_conversion_is_one_shot(self):
        self.convert(self.path)
        with self.assertRaisesRegex(Stop,'one-shot'):self.convert(self.path)
    def test_conversion_existing_target_refuses_and_holds(self):
        self.path.write_bytes(b'old')
        with self.assertRaisesRegex(Stop,'absent'):self.convert(self.path)
        self.assertEqual(self.budget.summary()['final_snapshot_state'],'held')
    def test_conversion_wrong_target_refuses(self):
        with self.assertRaisesRegex(Stop,'confinement'):self.convert(self.output/'other.sqlite3')
    def test_conversion_failure_preserves_full_reserve(self):
        self.plan.disk_bytes=self.base+512*MIB-1
        with self.assertRaises(Stop):self.convert(self.path)
        self.assertEqual(self.budget.summary()['final_snapshot_pending'],512*MIB)
    def test_partial_target_allocation_not_double_charged(self):
        self.convert(self.path);self.path.write_bytes(b'x'*8192);self.budget.census()
        summary=self.budget.summary();self.assertEqual(summary['final_snapshot_accounted'],8192)
        self.assertEqual(summary['final_snapshot_pending'],512*MIB-8192)
    def test_gap_never_credits_cached_snapshot_allocation(self):
        self.convert(self.path);self.path.write_bytes(b'x'*8192);self.budget.census()
        with patch.object(self.budget,'_walk',return_value=(dict(self.budget._records),[dict(path='gone',stage='entry-absent')])):self.budget.census()
        self.assertEqual(self.budget.summary()['final_snapshot_pending'],512*MIB)
    def test_stale_snapshot_credit_expires(self):
        self.convert(self.path);self.path.write_bytes(b'x'*8192);self.budget.census()
        self.budget._snapshot_credit_at=time.monotonic()-6
        self.assertEqual(self.budget.summary()['final_snapshot_pending'],512*MIB)
    def test_failed_partial_backup_preserves_unused_reservation(self):
        self.convert(self.path);self.path.write_bytes(b'x'*8192);self.budget.census()
        self.budget.fail_final_snapshot('synthetic backup failure')
        self.assertEqual(self.budget.summary()['final_snapshot_pending'],512*MIB-8192)
        with self.assertRaises(Stop):self.budget.complete_final_snapshot(positive_wait=True)
    def test_completion_requires_positive_wait(self):
        self.convert(self.path);self.path.write_bytes(b'x'*8192)
        with self.assertRaises(Stop):self.budget.complete_final_snapshot()
        self.assertEqual(self.budget.summary()['final_snapshot_state'],'admitted')
    def test_completion_releases_only_after_fresh_complete_census(self):
        self.convert(self.path);self.path.write_bytes(b'x'*8192)
        self.budget.complete_final_snapshot(positive_wait=True)
        self.assertEqual(self.budget.summary()['final_snapshot_pending'],0)
        self.assertEqual(self.budget.summary()['final_snapshot_state'],'completed')
    def test_snapshot_inode_replacement_refused(self):
        self.convert(self.path);self.path.write_bytes(b'x'*8192);self.budget.census()
        replacement=self.output/'replacement';replacement.write_bytes(b'y');os.replace(replacement,self.path)
        with self.assertRaisesRegex(Stop,'identity replaced'):self.budget.census()
    def test_snapshot_hardlink_refused(self):
        self.convert(self.path);self.path.write_bytes(b'x');os.link(self.path,self.output/'alias')
        with self.assertRaisesRegex(Stop,'link/device'):self.budget.census()
    def test_terminal_mode_spends_existing_margin_after_live_refusal(self):
        self.plan.disk_bytes=self.base+512*MIB+32*MIB
        with self.assertRaisesRegex(Stop,'ceiling'):self.budget.census()
        self.budget.begin_terminal();self.budget._terminal_capacity(0);before=self.budget.terminal_used
        self.budget.admit_terminal_evidence(4096)
        self.assertEqual(self.budget.terminal_used,before+4096)
        self.assertEqual(self.budget.summary()['final_snapshot_pending'],512*MIB)
    def test_terminal_is_one_way_and_does_not_reset_spend(self):
        self.budget.begin_terminal();self.budget.admit_terminal_evidence(4096)
        with self.assertRaisesRegex(Stop,'one-shot'):self.budget.begin_terminal()
        self.assertEqual(self.budget.terminal_used,4096)
        with self.assertRaisesRegex(Stop,'Ordinary admission'):self.budget.admit(0)
    def test_terminal_overspend_refused_without_replenishment(self):
        self.budget.begin_terminal();self.budget.admit_terminal_evidence(64*MIB);self.budget.census()
        with self.assertRaisesRegex(Stop,'spend cap'):self.budget.admit_terminal_evidence(1)
        self.assertEqual(self.budget.terminal_used,64*MIB)
    def test_terminal_cannot_waive_snapshot_or_hard_ceiling(self):
        self.budget.begin_terminal();self.plan.disk_bytes=self.base+512*MIB+99
        with self.assertRaisesRegex(Stop,'Terminal disk ceiling'):self.budget.admit_terminal_evidence(100)
    def test_terminal_conversion_after_live_threshold_failure(self):
        self.plan.disk_bytes=self.base+512*MIB+32*MIB
        with self.assertRaises(Stop):self.budget.census()
        self.budget.begin_terminal();self.budget._terminal_capacity(0);before=self.budget.terminal_used
        self.budget.admit_terminal_evidence(4096)
        self.convert(self.path);self.path.write_bytes(b'x'*8192)
        self.budget.complete_final_snapshot(positive_wait=True)
        self.assertEqual(self.budget.terminal_used,before+4096)
        self.assertEqual(self.budget.summary()['final_snapshot_pending'],0)
