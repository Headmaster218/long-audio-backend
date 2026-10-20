# PUBLIC INSPECTION COPY. Do not execute or import.

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from adapter import Operations
from resume_state import require_checkpoint,preserve,validate,FAILED_CHUNK


def checkpoint():
    return dict(inputs=14,clean_inputs=14,frames=REDACTED_EMITTED_FRAMES,published_frames=REDACTED_PUBLISHED_FRAMES,finalized=False,active_input=None,pages=REDACTED_STAGED_PAGE_COUNT)

class ContinuationControls(unittest.TestCase):
    def test_exact_clean014(self):self.assertEqual(require_checkpoint(checkpoint()),REDACTED_PUBLISHED_FRAMES)
    def test_old_offset_refused(self):
        p=checkpoint();p['frames']-=1
        with self.assertRaises(RuntimeError):require_checkpoint(p)
    def test_invented_active_input_refused(self):
        p=checkpoint();p['active_input']={'id':'repeated-prefix-015'}
        with self.assertRaises(RuntimeError):require_checkpoint(p)
    def test_preserve_all_prior_results(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'a';b=Path(d)/'b';rows=''.join(json.dumps(dict(id=f'{i:04}',sha256='a'*64))+'\n' for i in range(REDACTED_COMPLETED_BASELINE));a.write_text(rows);b.write_text(rows)
            baseline=dict(completed={'end':REDACTED_COMPLETED_END});summary=dict(completed=REDACTED_COMPLETED_BASELINE,origin=REDACTED_FRAME_ORIGIN,end=REDACTED_COMPLETED_END,eos=0)
            self.assertEqual(preserve(baseline,a,b,summary),REDACTED_COMPLETED_BASELINE)
            b.write_text(rows.replace('a'*64,'b'*64,1))
            with self.assertRaises(RuntimeError):preserve(baseline,a,b,summary)
    def operation(self):
        ops=Operations.__new__(Operations);calls=[]
        recovered=dict(chunk=dict(id=FAILED_CHUNK,status='completed',next_offset=REDACTED_PRIOR_CHUNK_DURATION),batch={'id':'same'},failed_count=0)
        ops.root=Path('/unused');ops.baseline=dict(journal=checkpoint(),recovered=recovered)
        ops.r={'resume':{'completed_manifest':'old'},'database':'same'};ops.plan=SimpleNamespace(session_id='same-session',capture_id='same-capture')
        ops.check=lambda:None;ops.launch_server=lambda:calls.append('launch');ops.proof=lambda label:('new',{})
        ops.checkpoint=lambda:checkpoint();ops.helper=lambda args:copy.deepcopy(recovered)
        ops.family=SimpleNamespace(listener_owned=lambda:calls.append('owned'))
        def http(path,method='GET',*args):
            self.assertEqual((path,method),('/health','GET'));calls.append('GET');return 200,{'worker_alive':True}
        ops.http=http;ops.event=lambda *args:calls.append('event')
        return ops,calls
    def run_start(self,ops):
        with patch('adapter.resume_state.verify_sources'),patch('adapter.resume_state.preserve',return_value=REDACTED_COMPLETED_BASELINE):return ops.resume_start()
    def test_no_retry_post_on_continuation(self):
        ops,calls=self.operation();self.assertEqual(self.run_start(ops),'same-session');self.assertEqual(calls,['launch','owned','GET','event'])
    def test_new_failed_work_refused_no_retry(self):
        ops,calls=self.operation();old=ops.helper;ops.helper=lambda args:dict(old(args),failed_count=1)
        with self.assertRaises(RuntimeError):self.run_start(ops)
        self.assertEqual(calls,['launch'])
    def test_former_completed_identity_change_refused(self):
        ops,calls=self.operation();ops.helper=lambda args:dict(chunk={'id':'other'},batch={'id':'same'},failed_count=0)
        with self.assertRaises(RuntimeError):self.run_start(ops)
    def test_dead_worker_refused(self):
        ops,calls=self.operation();ops.http=lambda *args:(200,{'worker_alive':False})
        with self.assertRaises(RuntimeError):self.run_start(ops)
    def test_real_disabled_release_shape(self):
        path=Path(__file__).parent/'resume-proposal/endurance-release.json';release=json.loads(path.read_bytes())
        self.assertIs(release['approved_for_actual'],False)
        self.assertEqual(release['measurement_protocol'],'visible-pid-namespace-rss-v1')
        with patch('resume_state.time.time',return_value=release['resume']['term_deadline_unix']-release['resume']['outer_timeout_seconds']-1):
            proof=validate(release)
            self.assertEqual(proof['completed']['completed'],REDACTED_COMPLETED_BASELINE);self.assertEqual(proof['completed']['result_rows'],REDACTED_RESULT_ROWS)
            release['resume']['retry_post_allowed']=True
            with self.assertRaises(RuntimeError):validate(release)
    def test_no_retry_implementation_in_continuation(self):
        import resume_state
        self.assertFalse(hasattr(resume_state,'retry_failed'))

