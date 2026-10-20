# PUBLIC INSPECTION COPY. Do not execute or import.
"""Finite synthetic source controls. Root executes; preparation does not import."""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import copy
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import namespace_sampling as ns

GIB=1024**3
class Gone(Exception):
    def __init__(self,pid):self.pid=pid
class Denied(Exception):pass
class Base:
    STABLE=('pid','ticks','parent','uid','pgid','sid')

def row(pid,ticks=None,parent=7):
    return dict(pid=pid,ticks=pid*10 if ticks is None else ticks,parent=parent,uid=1000,pgid=6,sid=1,state='S')
def item(pid,rss=1024):
    return dict(kernel=row(pid),rss=rss,max_rss=rss,cpu=1.,bootstrap=False)

class NamespaceControls(unittest.TestCase):
    def family(self,measurements=None,previous=None):
        records=[];kind=ns.namespace_family(Base,records.append,{'charged_memory':6*GIB,'stop_output':8*GIB})
        f=kind.__new__(kind);f.lock=threading.RLock();f.namespace_pin={'pinned':True}
        f.namespace_records=f.namespace_gap_count=0;f.namespace_attribution_complete=True
        f.namespace_bootstrap={};f.namespace_page_size=4096;f.namespace_unreadable=None
        f.namespace_active=copy.deepcopy(previous or {});f.originals={};f.samples=[]
        f.last_complete=100.;f.last_charge=f._charge(16*ns.MIB+sum(x['rss'] for x in f.namespace_active.values()))
        f.peak_charge=f.peak_rss=f.pending_charge=0;f.max_gap=0.;f.incomplete=None
        f.shutting_down=f.pre_stop_sampling=f.final_sample_complete=f.live_phase=False
        f.psutil=SimpleNamespace(NoSuchProcess=Gone,AccessDenied=Denied)
        f._verify_namespace=lambda:None;f._envelope=lambda pid,kernel:False
        f._positive_absent=lambda pid:True
        values=dict(measurements or {})
        f._enumerate=lambda:list(values)
        def measure(pid):
            value=values[pid]
            if isinstance(value,BaseException):raise value
            return copy.deepcopy(value)
        f._measure=measure;f._raw_identity=lambda pid:row(pid)
        return f,records,values
    def sample(self,f,at=101.):
        with patch.object(ns.time,'monotonic',return_value=at):f.sample()
    def test_all_unknown_rss_counted_without_role_auth(self):
        f,records,_=self.family({10:item(10,1000),11:item(11,2000)})
        self.sample(f)
        self.assertEqual(f.samples[-1]['rss'],16*ns.MIB+3000)
        self.assertEqual(f.samples[-1]['comparator_rss'],0)
        self.assertTrue(f.samples[-1]['complete'])
    def test_unknown_large_rss_hits_same_six_gib_charge(self):
        f,_,_=self.family({10:item(10,3*GIB),11:item(11,3*GIB)})
        with self.assertRaisesRegex(RuntimeError,'charged memory ceiling'):self.sample(f)
    def test_observed_two_exit_orderings_are_gaps_not_admission_failures(self):
        for order in ((2264,2267),(2267,2264)):
            with self.subTest(order=order):
                f,records,_=self.family({order[0]:FileNotFoundError(),order[1]:Gone(order[1]),8:item(8)})
                self.sample(f)
                self.assertEqual(f.samples[-1]['exit_gaps'],2)
                self.assertTrue(f.samples[-1]['complete'])
                gaps=[x for x in records if x['stage']=='exit-gap']
                self.assertEqual([x['pid'] for x in gaps],list(order))
                self.assertTrue(all(not x['historical_peak_known'] and not x['cleanup_authority'] for x in gaps))
    def test_five_gib_newcomer_exits_after_read_still_exceeds_ceiling(self):
        f,records,_=self.family({10:item(10)})
        f._rss=lambda pid:5*GIB
        f.psutil.Process=lambda pid:SimpleNamespace(cpu_times=lambda:(_ for _ in ()).throw(Gone(pid)))
        f._measure=type(f)._measure.__get__(f)
        with self.assertRaisesRegex(RuntimeError,'charged memory ceiling'):self.sample(f)
        gap=next(x for x in records if x['stage']=='exit-gap')
        self.assertEqual(gap['readable_gap_rss'],5*GIB)
    def test_partial_cpu_and_rss_before_final_absence_are_retained(self):
        f,records,_=self.family({10:item(10)})
        def measure(pid):
            f.namespace_read_context.update(readable_rss=10000,readable_cpu=7.)
            raise Gone(pid)
        f._measure=measure;self.sample(f)
        self.assertEqual(f.samples[-1]['rss'],16*ns.MIB+10000)
        self.assertEqual(f.samples[-1]['cpu'],7.)
    def test_terminal_race_retains_prior_full_charge(self):
        f,_,_=self.family({10:FileNotFoundError(),11:item(11)},previous={10:item(10,200*ns.MIB)})
        before=f.last_charge;self.sample(f);self.assertGreaterEqual(f.last_charge,before)
    def test_unconfirmed_absence_refuses(self):
        f,_,_=self.family({10:FileNotFoundError()});f._positive_absent=lambda pid:False
        with self.assertRaisesRegex(RuntimeError,'absence unconfirmed'):self.sample(f)
    def test_foreign_disappearance_refuses(self):
        f,_,_=self.family({10:Gone(99)})
        with self.assertRaisesRegex(RuntimeError,'Foreign selected'):self.sample(f)
    def test_fresh_unreadable_live_refuses_without_zero(self):
        f,_,_=self.family({10:ns.RssUnavailable(10,PermissionError())});f._positive_absent=lambda pid:False
        with self.assertRaisesRegex(RuntimeError,'no conservative prior'):self.sample(f)
    def test_existing_unreadable_retains_prior_plus_identity_max(self):
        old=item(10,1000);old['max_rss']=2000
        f,_,_=self.family({10:ns.RssUnavailable(10,PermissionError())},previous={10:old});f._positive_absent=lambda pid:False
        before=f.last_charge;self.sample(f)
        self.assertFalse(f.samples[-1]['complete']);self.assertGreaterEqual(f.pending_charge,before+2000)
        self.assertEqual(f.incomplete['deadline'],103.)
    def test_repeated_unreadable_cannot_refresh_deadline(self):
        f,_,_=self.family({10:ns.RssUnavailable(10,PermissionError())},previous={10:item(10)})
        f._positive_absent=lambda pid:False;self.sample(f,101.);self.sample(f,102.)
        self.assertEqual(f.incomplete['deadline'],103.)
        with self.assertRaisesRegex(RuntimeError,'freshness expired'):self.sample(f,103.01)
    def test_first_complete_after_unreadable_keeps_retained_charge(self):
        f,_,values=self.family({10:ns.RssUnavailable(10,PermissionError())},previous={10:item(10,10000)})
        f._positive_absent=lambda pid:False;self.sample(f,101.);charge=f.pending_charge
        values[10]=item(10,1);self.sample(f,102.)
        self.assertGreaterEqual(f.last_charge,charge);self.assertIsNone(f.incomplete)
        self.sample(f,102.5);self.assertLess(f.last_charge,charge)
    def test_unreadable_then_positive_exit_resolves_with_gap(self):
        f,_,values=self.family({10:ns.RssUnavailable(10,PermissionError())},previous={10:item(10,10000)})
        f._positive_absent=lambda pid:False;self.sample(f,101.);values.clear();f._positive_absent=lambda pid:True
        self.sample(f,102.);self.assertTrue(f.samples[-1]['complete']);self.assertEqual(f.samples[-1]['exit_gaps'],1)
    def test_live_envelope_denial_is_not_rss_grace(self):
        f,_,_=self.family({10:Denied()},previous={10:item(10)})
        with self.assertRaises(Denied):self.sample(f)
        self.assertIsNone(f.namespace_unreadable)
    def test_unreadable_memory_does_not_exempt_cpu_nice(self):
        f,_,_=self.family({10:ns.RssUnavailable(10,PermissionError())},previous={10:item(10)})
        f._positive_absent=lambda pid:False
        f._envelope=lambda *a:(_ for _ in ()).throw(RuntimeError('Visible workload CPU/nice envelope'))
        with self.assertRaisesRegex(RuntimeError,'CPU/nice'):self.sample(f)
    def test_old_birth_replaced_before_resolution_refuses(self):
        value=item(10);value['kernel']['ticks']=999
        f,_,_=self.family({10:value},previous={10:item(10)})
        with self.assertRaisesRegex(RuntimeError,'identity changed'):self.sample(f)
    def test_new_birth_after_positive_exit_is_new_nonowned_identity(self):
        f,_,values=self.family({},previous={10:item(10)})
        self.sample(f,101.);self.assertNotIn(10,f.namespace_active)
        values[10]=item(10);values[10]['kernel']['ticks']=999;self.sample(f,102.)
        self.assertEqual(f.namespace_active[10]['kernel']['ticks'],999)
        self.assertFalse(hasattr(f,'authenticated'))
    def test_namespace_or_boot_drift_refuses_before_measurement(self):
        f,_,_=self.family({10:item(10)});f._namespace_pin=lambda:{'different':True}
        f._verify_namespace=type(f)._verify_namespace.__get__(f)
        with self.assertRaisesRegex(RuntimeError,'namespace or boot'):self.sample(f)
    def test_missing_bootstrap_refuses(self):
        f,_,_=self.family({10:item(10)});f.namespace_bootstrap={1:{'kernel':row(1)}}
        with self.assertRaisesRegex(RuntimeError,'Bootstrap ancestor'):self.sample(f)
    def test_refusal_is_durable_and_bounded(self):
        f,records,_=self.family({10:item(10,6*GIB)})
        with self.assertRaises(RuntimeError):self.sample(f)
        self.assertEqual(records[-1]['stage'],'census-refused')
        f.namespace_records=ns.MAX_RECORDS
        with self.assertRaisesRegex(RuntimeError,'record cap'):f._ns_record('extra')
    def test_record_size_cap(self):
        f,_,_=self.family()
        with self.assertRaisesRegex(RuntimeError,'byte cap'):f._ns_record('oversize',value='x'*ns.MAX_RECORD_BYTES)
    def test_rss_parse_and_overflow_refused(self):
        f,_,_=self.family()
        for raw in (b'0 x',b'0 -1',b'0 '+str(1<<63).encode()):
            f._read_bytes=lambda *a,raw=raw:raw
            with self.assertRaises(RuntimeError):f._rss(10)
        f._read_bytes=lambda *a:b'12 3 0';self.assertEqual(f._rss(10),12288)
    def test_cpu_nice_strict_for_unknown_role(self):
        f,_,_=self.family();f._verify_bootstrap=lambda r:False;f._expected_cpus=lambda:{0,1}
        f._envelope=type(f)._envelope.__get__(f)
        for affinity,nice in (([0,1],0),([2],19),([],19)):
            f.psutil.Process=lambda pid:SimpleNamespace(cpu_affinity=lambda:affinity,nice=lambda:nice)
            with self.assertRaisesRegex(RuntimeError,'CPU/nice'):f._envelope(10,row(10))
    def test_exact_bootstrap_exempt_from_cpu_but_rss_counted(self):
        f,_,_=self.family();f._raw_identity=lambda pid:row(pid)
        f.namespace_bootstrap={2:dict(kernel=row(2),exe='/timeout',argv_sha256='a')}
        f._fingerprint=lambda pid:dict(exe='/timeout',argv_sha256='a')
        f.psutil.Process=lambda pid:SimpleNamespace(cpu_times=lambda:SimpleNamespace(user=1,system=0))
        f._rss=lambda pid:12345
        f._envelope=type(f)._envelope.__get__(f)
        measured=type(f)._measure(f,2)
        self.assertEqual(measured['rss'],12345);self.assertTrue(measured['bootstrap'])
    def test_bootstrap_reuse_and_exec_drift_refused(self):
        f,_,_=self.family();f.namespace_bootstrap={2:dict(kernel=row(2),exe='/timeout',argv_sha256='a')}
        changed=row(2);changed['ticks']+=1
        with self.assertRaisesRegex(RuntimeError,'Bootstrap identity'):f._verify_bootstrap(changed)
        f._fingerprint=lambda pid:dict(exe='/other',argv_sha256='a')
        with self.assertRaisesRegex(RuntimeError,'Bootstrap executable'):f._verify_bootstrap(row(2))
    def test_rss_identity_bracket_birth_change_refuses(self):
        f,_,_=self.family();reads=iter([row(10),row(10,999)])
        f._raw_identity=lambda pid:next(reads);f._rss=lambda pid:100
        f._envelope=lambda pid,k:False;f.psutil.Process=lambda pid:SimpleNamespace(cpu_times=lambda:SimpleNamespace(user=1,system=0))
        with self.assertRaisesRegex(RuntimeError,'RSS bracket'):type(f)._measure(f,10)
    def test_enumeration_visible_and_scan_caps(self):
        class Entries(list):
            def __enter__(self):return iter(self)
            def __exit__(self,*args):pass
        f,_,_=self.family();method=type(f)._enumerate.__get__(f)
        with patch.object(ns.os,'scandir',return_value=Entries(SimpleNamespace(name=str(i)) for i in range(1,130))):
            with self.assertRaisesRegex(RuntimeError,'visible process cap'):method()
        with patch.object(ns.os,'scandir',return_value=Entries(SimpleNamespace(name='notnumeric') for _ in range(4097))):
            with self.assertRaisesRegex(RuntimeError,'enumeration cap'):method()
    def test_bootstrap_capture_includes_wrapper_not_helper(self):
        f,_,_=self.family();f.wrapper=row(2,parent=1)
        f._raw_identity=lambda pid:row(pid,parent=1 if pid==2 else 0)
        f._fingerprint=lambda pid:dict(exe='/'+str(pid),argv_sha256=str(pid))
        f._capture_bootstrap();self.assertEqual(set(f.namespace_bootstrap),{1,2})
        self.assertNotIn(7,f.namespace_bootstrap)
    def test_bootstrap_ancestry_cycle_refused(self):
        f,_,_=self.family();f.wrapper=row(2,parent=1)
        f._raw_identity=lambda pid:row(pid,parent=1 if pid==2 else 2)
        f._fingerprint=lambda pid:dict(exe='/'+str(pid),argv_sha256=str(pid))
        with self.assertRaisesRegex(RuntimeError,'ancestry bound'):f._capture_bootstrap()
    def test_raw_identity_birth_bracket(self):
        f,_,_=self.family();values=iter([row(10),row(10,999)])
        f._stat=lambda pid:next(values);f._read_bytes=lambda *a:b'Uid:\t1000\t1000\t1000\t1000\n'
        with self.assertRaisesRegex(RuntimeError,'birth changed'):type(f)._raw_identity(f,10)
    def test_final_inventory_rejects_unknown_without_signal_authority(self):
        f,_,_=self.family({99:item(99)});f.helper=row(7);f.wrapper=row(6)
        with self.assertRaisesRegex(RuntimeError,'Unowned visible process'):f._final_inventory()
    def test_final_inventory_exact_infrastructure_only(self):
        f,_,_=self.family({7:item(7),6:item(6),1:item(1)})
        f.helper=row(7);f.wrapper=row(6);f.namespace_bootstrap={1:dict(kernel=row(1))};f._verify_bootstrap=lambda r:True
        self.assertTrue(f._final_inventory())
    def test_final_retained_unreadable_refuses(self):
        f,_,_=self.family();f.incomplete={'reason':'unknown'}
        with self.assertRaisesRegex(RuntimeError,'final cleanup'):f._retained_resolved()
    def test_final_original_waits_are_mandatory(self):
        f,_,_=self.family();f.originals={'server':None};f._original_exit=lambda name:(_ for _ in ()).throw(RuntimeError('not waited'))
        with self.assertRaisesRegex(RuntimeError,'not waited'):f._retained_resolved()
    def test_shutdown_requires_fresh_complete_sample(self):
        f,_,_=self.family();f.shutting_down=True
        with self.assertRaisesRegex(RuntimeError,'Fresh namespace'):f._retained_resolved()
    def test_empty_first_census_with_terminal_gap_is_explicit(self):
        f,records,_=self.family({10:FileNotFoundError()});f.last_complete=None;f.last_charge=0
        self.sample(f);self.assertTrue(f.samples[-1]['complete']);self.assertEqual(f.samples[-1]['coverage'],'sampled_with_exit_gaps')
        self.assertFalse(f.samples[-1]['historical_peak_guaranteed'])

