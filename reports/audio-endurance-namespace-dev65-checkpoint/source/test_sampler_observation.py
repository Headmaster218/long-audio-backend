import hashlib,importlib.util,os,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sampler_observation import observation_family
MONITOR=Path('/REVIEWED_DEPENDENCIES/fixed_trial.py')
assert hashlib.sha256(MONITOR.read_bytes()).hexdigest()=='bcad729f24170fca1574f55a20850a2f8d41d52e59fa44ad8699ae6f851f6c3b'
spec=importlib.util.spec_from_file_location('reviewed_monitor',MONITOR);monitor=importlib.util.module_from_spec(spec);spec.loader.exec_module(monitor)

class ObservationControls(unittest.TestCase):
    def fixture(self,known=True):
        events=[];cls=observation_family(monitor.Family,events.append);f=object.__new__(cls)
        kernel=dict(pid=123,ticks=100,parent=8,uid=os.geteuid(),pgid=7,sid=7,state='R')
        previous=dict(kind='analyzer',argv=['python','known'],exe='/python',parent=8,uid=os.geteuid(),kernel=kernel.copy())
        f.authenticated={(123,100):previous} if known else {};f.shutting_down=False;f.sample_recheck_used=False;f.incomplete=None
        f.last_complete=time.monotonic();f.last_charge=2000000000;f.pending_charge=0;f.live_rechecks=[];f.read_context={}
        f.wrapper=None;f.root_identities={};f.terminal_grace_deadline=None;f.child_refusals=[]
        f._chain=lambda pid:[dict(kernel,state='Z')];f.confirm_child_exit=lambda q,ticks:dict(numeric_pid_absent=True,original_parent_reap=False)
        pending=f.PendingMetadata(dict(kernel=kernel.copy(),native_reads=dict(argv='empty',exe='value')))
        q=SimpleNamespace(pid=123)
        return f,q,pending,events,previous
    def call(self,f,q,p):
        with patch.object(monitor.Family,'authenticate',side_effect=p):return f.authenticate(q,allow_pending=True)
    def test_known_terminal_zombie_only(self):
        f,q,p,events,previous=self.fixture();self.assertIsNone(self.call(f,q,p));self.assertTrue(previous['exit_pending']);self.assertEqual(f.pending_charge,f.last_charge);self.assertTrue(f.sample_recheck_used)
        self.assertEqual(events[-1]['outcome'],'terminal_grace_zombie_pending')
    def test_known_terminal_absence_only(self):
        f,q,p,events,_=self.fixture();f._chain=lambda pid:(_ for _ in ()).throw(f.Vanished(pid,100))
        self.assertIsNone(self.call(f,q,p));self.assertEqual(events[-1]['outcome'],'terminal_grace_positive_absence')
    def test_unknown_unchanged(self):
        f,q,p,events,_=self.fixture(False)
        with self.assertRaises(f.PendingMetadata):self.call(f,q,p)
        self.assertFalse(f.sample_recheck_used);self.assertEqual(len(events),1)
    def test_partial_old_identity_not_eligible(self):
        f,q,p,events,previous=self.fixture();previous.pop('exe')
        with self.assertRaises(f.PendingMetadata):self.call(f,q,p)
    def test_second_recovery_unchanged(self):
        f,q,p,events,_=self.fixture();f.sample_recheck_used=True
        with self.assertRaises(f.PendingMetadata):self.call(f,q,p)
    def test_prior_pending_unchanged(self):
        f,q,p,events,_=self.fixture();f.incomplete=dict(at=time.monotonic(),pid=124,ticks=200,deadline=f.last_complete+3)
        with self.assertRaises(f.PendingMetadata):self.call(f,q,p)
    def test_shutdown_unchanged(self):
        f,q,p,events,_=self.fixture();f.shutting_down=True
        with self.assertRaises(f.PendingMetadata):self.call(f,q,p)
    def test_persistent_live_missing_metadata_refuses(self):
        f,q,p,events,previous=self.fixture();f._chain=lambda pid:[previous['kernel'].copy()]
        with self.assertRaisesRegex(monitor.TrialStop,'remains live/unverifiable'):self.call(f,q,p)
        self.assertEqual(len(events[-1]['reads']),3);self.assertEqual(events[-1]['maximum_seconds'],.1)
    def test_stale_complete_refuses_no_extension(self):
        f,q,p,events,_=self.fixture();f.last_complete=time.monotonic()-4
        with self.assertRaisesRegex(monitor.TrialStop,'expired'):self.call(f,q,p)
        self.assertEqual(events[-1]['reads'],[])
    def test_reused_pid_refuses(self):
        f,q,p,events,previous=self.fixture();f._chain=lambda pid:[dict(previous['kernel'],ticks=101,state='Z')]
        with self.assertRaisesRegex(monitor.TrialStop,'identity changed'):self.call(f,q,p)
    def test_changed_uid_refuses(self):
        f,q,p,events,previous=self.fixture();f._chain=lambda pid:[dict(previous['kernel'],uid=os.geteuid()+1,state='Z')]
        with self.assertRaisesRegex(monitor.TrialStop,'UID changed'):self.call(f,q,p)
    def test_ancestor_disappearance_refuses(self):
        f,q,p,events,_=self.fixture();f._chain=lambda pid:(_ for _ in ()).throw(f.Vanished(8,1))
        with self.assertRaisesRegex(monitor.TrialStop,'ancestor'):self.call(f,q,p)
    def test_denied_read_refuses(self):
        f,q,p,events,_=self.fixture();f._chain=lambda pid:(_ for _ in ()).throw(PermissionError('denied'))
        with self.assertRaises(PermissionError):self.call(f,q,p)
    def test_durable_sample_failure_branch_and_pending(self):
        f,q,p,events,_=self.fixture();f.incomplete=dict(at=time.monotonic()-.2,pid=123,ticks=100,deadline=f.last_complete+3)
        with patch.object(monitor.Family,'sample',side_effect=monitor.TrialStop('second incomplete sample')):
            with self.assertRaises(monitor.TrialStop):f.sample()
        self.assertEqual(events[-1]['stage'],'sample-failed');self.assertGreaterEqual(events[-1]['pending_age'],.2);self.assertTrue(events[-1]['failure_sites'])
    def test_diagnostic_snapshot_not_mutable_alias(self):
        f,q,p,events,_=self.fixture();f.incomplete=dict(at=time.monotonic(),pid=123);f._diagnostic('test');f.incomplete['pid']=999
        self.assertEqual(events[-1]['pending']['pid'],123)
    def sample_fixture(self):
        f,q,p,events,previous=self.fixture()
        server=SimpleNamespace(pid=8,children=lambda recursive:[q],cpu_affinity=lambda:[0,1],nice=lambda:19,
            memory_info=lambda:SimpleNamespace(rss=1000000),cpu_times=lambda:SimpleNamespace(user=1.,system=0.),create_time=lambda:1.)
        f.originals={'server':(SimpleNamespace(pid=8,poll=lambda:None),server,['server'])}
        f._original=lambda name:dict(pid=8,ticks=80)
        f._expected_cpus=lambda:{0,1};f.lock=threading.RLock();f.max_gap=0;f.live_phase=False
        f.psutil=SimpleNamespace(Process=lambda:SimpleNamespace(memory_info=lambda:SimpleNamespace(rss=1000000)),NoSuchProcess=type('NoSuchProcess',(Exception,),{}))
        f.samples=[dict(at=f.last_complete,rss=1000000,cpu=0)];f.peak_charge=0;f.peak_rss=0;f.pre_stop_sampling=False
        f.observed={(8,80):(server,'server')};f.rows=[];f.incomplete_rows=[]
        return f,q,p,events,server
    def test_direct_sample_traversal_known_terminal_completes(self):
        f,q,p,events,server=self.sample_fixture()
        with patch.object(monitor.Family,'authenticate',side_effect=p):f.sample()
        self.assertTrue(f.samples[-1]['complete']);self.assertEqual(f.samples[-1]['charged'],2000000000)
        self.assertIsNone(f.incomplete);self.assertEqual(sum(x['stage']=='known-terminal-grace' for x in events),1)
    def test_direct_sample_memory_ceiling_still_refuses(self):
        f,q,p,events,server=self.sample_fixture();server.memory_info=lambda:SimpleNamespace(rss=monitor.LIMITS['charged_memory'])
        with patch.object(monitor.Family,'authenticate',side_effect=p):
            with self.assertRaisesRegex(monitor.TrialStop,'memory ceiling'):f.sample()
        self.assertEqual(events[-1]['stage'],'sample-failed')
    def test_direct_sample_second_known_pending_gets_no_second_grace(self):
        f,q,p,events,server=self.sample_fixture();other=SimpleNamespace(pid=124)
        kernel=dict(p.fields['kernel'],pid=124,ticks=200)
        second=f.PendingMetadata(dict(kernel=kernel,native_reads=dict(argv='empty',exe='value')))
        second.resources=dict(rss=None,cpu=None,affinity=None,nice=None,attempted=[],unavailable=[])
        f.authenticated[(124,200)]=dict(f.authenticated[(123,100)],kernel=kernel)
        other.cpu_affinity=lambda:[0,1];other.nice=lambda:19
        other.memory_info=lambda:SimpleNamespace(rss=1000000)
        other.cpu_times=lambda:SimpleNamespace(user=0.,system=0.)
        server.children=lambda recursive:[q,other]
        f._chain=lambda pid:[dict(p.fields['kernel'],state='Z')] if pid==123 else [kernel.copy()]
        f._kernel=lambda pid,expected=None:kernel.copy()
        f.psutil.Process=lambda pid=None:other if pid==124 else SimpleNamespace(memory_info=lambda:SimpleNamespace(rss=1000000))
        def pending_for(child,*args,**kwargs):raise p if child.pid==123 else second
        with patch.object(monitor.Family,'authenticate',side_effect=pending_for):
            f.sample()
            self.assertEqual(f.incomplete['pid'],124);self.assertEqual(len(f.samples),1)
            with self.assertRaisesRegex(monitor.TrialStop,'second incomplete sample'):f.sample()
        self.assertEqual(sum(x['stage']=='known-terminal-grace' for x in events),1)
    def test_terminal_retained_charge_survives_complete_census(self):
        f,q,p,events,_=self.fixture();self.call(f,q,p)
        f.samples=[];f.peak_charge=0;f.peak_rss=0;f.pre_stop_sampling=False
        f._finish_sample(time.monotonic(),1000000,0,1.)
        self.assertEqual(f.samples[-1]['charged'],2000000000)
    def test_readable_pending_memory_ceiling_remains(self):
        f,q,p,events,_=self.fixture(False);f.samples=[dict(rss=1000000)];f.incomplete_rows=[];f.peak_charge=0
        p.resources['rss']=monitor.LIMITS['charged_memory']
        with self.assertRaisesRegex(monitor.TrialStop,'memory ceiling'):
            f._mark_incomplete(p,time.monotonic(),0)
        self.assertGreaterEqual(f.pending_charge,monitor.LIMITS['charged_memory'])
    def test_complete_pending_resolution_durable(self):
        f,q,p,events,_=self.fixture();f.samples=[];f.peak_charge=0;f.peak_rss=0;f.pre_stop_sampling=False
        f.incomplete=dict(pid=123,ticks=100,at=time.monotonic(),deadline=f.last_complete+3);f._absent=lambda pid:True
        f._finish_sample(time.monotonic(),1000000,0,1.)
        self.assertIsNone(f.incomplete);self.assertEqual(events[-1]['stage'],'incomplete-resolved')
    def test_diagnostic_cap_fails_closed(self):
        f,q,p,events,_=self.fixture();f.diagnostic_count=32768
        with self.assertRaisesRegex(RuntimeError,'event cap'):f._diagnostic('test')
    def test_diagnostic_sink_failure_fails_closed(self):
        class Base:pass
        cls=observation_family(Base,lambda row:(_ for _ in ()).throw(OSError('disk')));f=cls()
        with self.assertRaises(OSError):f._diagnostic('test')

