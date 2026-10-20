"""Finite synthetic conservation/scheduling controls for the single recovery."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from endurance import Input,Plan,run,Stop
from test_endurance_source import FakeOperations
from resume_state import preserve,verify_sources,validate
from copy import deepcopy
import time
from types import SimpleNamespace
from adapter import Operations

class RecoveryControls(unittest.TestCase):
    def test_schedule_skips_five_without_new_session(self):
        ops=FakeOperations();ops.count=5;ops.resume_start=lambda:'original-session'
        ops.start=lambda:(_ for _ in ()).throw(AssertionError('new session'))
        plan=Plan('/python','http://127.0.0.1:1','original-session','capture','/journal')
        inputs=tuple(Input(i,'/input/'+str(i),(1,i,1,1,1)) for i in range(90))
        state=dict(completed_inputs=5,started_upper=1002.,started_lower=1000.,schedule_offset=4.5)
        with patch('endurance.verify_inputs'):report=run(plan,inputs,ops,resume=state)
        self.assertTrue(report['successful_live']);self.assertEqual(len(ops.calls),86)
        self.assertEqual(ops.calls[0][ops.calls[0].index('--input-id')+1],'es2011a-repeat-006')
        self.assertEqual(ops.restarts,[(45,21606.5)]);self.assertEqual(ops.time,43206.5)
        self.assertTrue(all(argv[argv.index('--session-id')+1]=='original-session' for argv in ops.calls))
    def test_baseline_failure_still_cleans_up(self):
        ops=FakeOperations();ops.resume_start=lambda:(_ for _ in ()).throw(Stop('baseline mismatch'))
        p=Plan('/python','http://127.0.0.1:1','sid','cap','/journal')
        with patch('endurance.verify_inputs'),self.assertRaises(Stop):run(p,(),ops,resume={'completed_inputs':5})
        self.assertTrue(ops.stopped);self.assertEqual(ops.calls,[])
    def manifests(self,directory):
        old=Path(directory)/'old';new=Path(directory)/'new'
        rows=''.join(json.dumps({'id':f'{i:04}','sha256':'a'*64})+'\n' for i in range(195))
        old.write_text(rows);new.write_text(rows)
        return old,new
    def test_preserves_all_195(self):
        with tempfile.TemporaryDirectory() as d:
            a,b=self.manifests(d)
            self.assertEqual(preserve({'completed':{'end':37732003}},a,b,dict(completed=195,origin=100003,end=37732003,eos=0)),195)
    def test_changed_completed_result_refused(self):
        with tempfile.TemporaryDirectory() as d:
            a,b=self.manifests(d);b.write_text(b.read_text().replace('a'*64,'b'*64,1))
            with self.assertRaises(RuntimeError):preserve({'completed':{'end':37732003}},a,b,dict(completed=195,origin=100003,end=37732003,eos=0))
    def test_frontier_regression_refused(self):
        with tempfile.TemporaryDirectory() as d:
            a,b=self.manifests(d)
            with self.assertRaises(RuntimeError):preserve({'completed':{'end':37732003}},a,b,dict(completed=195,origin=100003,end=37732002,eos=0))
    def test_server_binding_failure_requires_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            calls=[]
            family=SimpleNamespace(launch=lambda *a:(_ for _ in ()).throw(RuntimeError('binding refused')),stop=lambda:calls.append('stop') or False,seal_tails=lambda:None,lifespan=False,forced=False,cleanup_survivors=[],errors=['binding incomplete'],cleanup=[])
            ops=Operations.__new__(Operations);ops.output=Path(d);ops.generation=0;ops.started=0;ops.budget=None;ops.family=None;ops.running=False;ops.stops=[]
            ops.base=SimpleNamespace(Deadlines=lambda *a:None);ops.r={'controls':{'server_argv':[],'environment':{}}};ops.family_type=lambda *a:family;ops.event=lambda *a:None
            with self.assertRaisesRegex(RuntimeError,'binding refused'):ops.launch_server()
            self.assertTrue(ops.running);self.assertFalse(ops.stop_server());self.assertEqual(calls,['stop']);self.assertFalse(ops.stops[-1]['positive'])
    def fixture(self,directory):
        root=Path(directory);now=time.time()
        baseline=dict(status='complete',before={},after={},changed=[],journal=dict(inputs=5,clean_inputs=5,active_input=None,frames=38400000,published_frames=37632000,finalized=False),completed=dict(completed=195,origin=100003,end=37732003,eos=0))
        old=dict(plan=dict(session_id='PENDING',capture_id='cap'),run_root='root',database='db',inputs_manifest='inputs',monitor={},runtime_manifest={},qualified_smoke={},controls=dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),environment=dict(HOME='old',TMPDIR='old/tmp',OMP_NUM_THREADS='2')))
        r=dict(completed_inputs=5,historical_positive_stop=False,session_id='sid',capture_id='cap',started_lower=1000.,started_upper=1002.,schedule_offset=4.4948807070032,previous_status_rounds=91,last_sample_at=2000.,outer_timeout_seconds=10,term_deadline_unix=now+100,kill_ceiling_unix=now+220)
        data=dict(previous_release=old,previous_terminal={},baseline_receipt=baseline,completed_manifest={},original_launch=dict(term_deadline_unix=now+100,kill_ceiling_unix=now+220),clock_evidence={k:r[k] for k in ('started_lower','started_upper','schedule_offset','previous_status_rounds','last_sample_at')})
        pins={}
        for key,value in data.items():
            path=root/key;path.write_text(json.dumps(value));r[key]=str(path);pins[str(path)]='fixture'
        release=deepcopy(old);release.update(output='new',resume=r,source_pins=pins);release['plan']['session_id']='sid';release['controls']['environment'].update(HOME='new',TMPDIR='new/tmp')
        return release
    def test_reviewed_recovery_validation(self):
        with tempfile.TemporaryDirectory() as d:self.assertEqual(validate(self.fixture(d))['completed']['completed'],195)
    def test_extended_original_deadline_refused(self):
        with tempfile.TemporaryDirectory() as d:
            release=self.fixture(d);release['resume']['term_deadline_unix']+=1
            with self.assertRaisesRegex(RuntimeError,'deadlines changed'):validate(release)
    def test_product_environment_change_refused(self):
        with tempfile.TemporaryDirectory() as d:
            release=self.fixture(d);release['controls']['environment']['OMP_NUM_THREADS']='9'
            with self.assertRaisesRegex(RuntimeError,'environment changed'):validate(release)
    def test_source_changed_before_restart_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'journal-retry02').mkdir();(root/'data-retry02').mkdir()
            p=root/'journal-retry02/uploader.json';p.write_text('original');st=p.stat()
            digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            baseline={'source_evidence':{'journal-retry02/uploader.json':dict(identity=[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns],sha256=digest(p))}}
            verify_sources(root,baseline,digest,lambda:None)
            p.write_text('changed')
            with self.assertRaises(RuntimeError):verify_sources(root,baseline,digest,lambda:None)

if __name__=='__main__':unittest.main()
