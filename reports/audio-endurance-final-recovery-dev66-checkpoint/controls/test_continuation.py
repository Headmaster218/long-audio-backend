# PUBLIC SOURCE INSPECTION ONLY. This is not a runnable distribution.

raise RuntimeError("Inspection-only transformed excerpt: do not execute or import; private dependencies and pins are intentionally omitted.")

import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from adapter import Operations
from resume_state import require_checkpoint,preserve,validate,FAILED_CHUNK,require_lost_baseline,require_accounting,require_terminal_accounting,require_positive_baseline,verify_sources,require_loss20_baseline,require_loss20_accounting,require_loss23_baseline,require_loss23_accounting,require_baseline16_provenance,require_disk_capacity_amendment,require_inherited_disk_capacity,require_loss25_baseline,require_loss25_accounting,require_baseline17_provenance
from product_transition import ALLOWED,SOURCE_ALLOWED,package_delta,require_server_argv,require_source_delta,require_continued_artifact,validate_previous_continuation as validate_product,validate_loss23_continuation as validate_current_product,validate as validate_latest_product


def checkpoint():
    return dict(inputs=14,clean_inputs=14,frames=REDACTED_COORDINATE_9665B0FD66,published_frames=REDACTED_COORDINATE_D1C744E94F,finalized=False,active_input=None,pages=23)

def allowance():
    return dict(historical_accounting='UNKNOWN',segment_get_cap=8192,segment_decoded_cap=268435456,
                individual_body_cap=524288,individual_seconds=5)


def legacy_accounting():
    history=dict(observer_budget_reset=False,observer_charge_kind='conservative_bound_not_actual_usage',
        latest_owner_cleanup='UNKNOWN',latest_original_wait_available=False,
        observed_recovered_usage=dict(requests=REDACTED_COORDINATE_F8D64A31EB,decoded_bytes=REDACTED_COORDINATE_5D3B3DB7A7),
        observer_requests=REDACTED_COORDINATE_1C918023D6,decoded_bytes=REDACTED_COORDINATE_129FEF76C1)
    charge=dict(kind='conservative_bound_not_actual_usage',requests=REDACTED_COORDINATE_1C918023D6,decoded_bytes=REDACTED_COORDINATE_129FEF76C1,
        additional_unlogged_tail_request_reserve=1,body_reserve_each=REDACTED_COORDINATE_1DCFAA9A1F,
        body_reserve_slots=16,body_reserve_total=REDACTED_COORDINATE_52CF9856FB)
    evidence=dict(schema='lost-executor-durable-accounting-v1',status='derived_evidence_proposal_not_terminal',
        http=dict(observer_budget_reset=False,allowance_unchanged=allowance(),
            conservative_continuation_charge=charge,recovered_segment_request_counter_lower_bound=REDACTED_COORDINATE_F8D64A31EB,
            recovered_segment_success_return_decoded_lower_bound=REDACTED_COORDINATE_5D3B3DB7A7,missing_slot_count=15,
            missing_sequence_slots=list(range(15)),prior_carried_requests=61,
            prior_carried_success_return_decoded_bytes=REDACTED_COORDINATE_E801FDC582,durable_body_bytes=REDACTED_COORDINATE_BD80A69C59))
    return history,evidence


def positive_fixture():
    old=dict(resume=dict(previous_status_rounds=REDACTED_COORDINATE_F15223DCC0,observation_allowance=allowance()))
    wait=dict(returncode=1,positive_original_wait=True)
    stop=dict(segment_output='prior-segment-output-C',positive=True,survivors=[],errors=[],lifespan=True,
        forced=False,generation=0,cleanup=[dict(name='server',positive_original_wait=True,returncode=-15),
                                         dict(name='uploader',positive_original_wait=True,returncode=2)])
    terminal=dict(observation_allowance=allowance(),original_global_observer_quota_proven=False,
        historical_positive_stop=False,interrupted_owner_cleanup='UNKNOWN',observer_requests=REDACTED_COORDINATE_E41D64DB57,
        decoded_bytes=REDACTED_COORDINATE_FAD31DA6EC,observer_carry_charge=dict(requests=REDACTED_COORDINATE_1C918023D6,decoded_bytes=REDACTED_COORDINATE_129FEF76C1),
        clock_reconstruction=copy.deepcopy(old['resume']),positive_stop=True,
        operations_pass=False,recovered_operations_pass=False,uninterrupted_endurance_pass=False,
        recovery_baseline_completed_preserved=REDACTED_COORDINATE_D8EC3903C8,supervisor_ownership_retained_through_terminal_fsync=True,
        supervisor_ownership_release_after_terminal_authorized=True,
        stops=[dict(source=x,positive='UNKNOWN',original_wait_available=False)
               for x in ('prior-segment-output-A','prior-segment-output-B')]+[stop],
        readback=dict(completed=REDACTED_COORDINATE_D8EC3903C8,result_rows=REDACTED_COORDINATE_75AA8C109B,origin=REDACTED_COORDINATE_24EB33C5F8,end=REDACTED_COORDINATE_C7529010D9,eos=0,manifest_sha256='a'*64),
        disk_peak=10,log_bytes=2,peak_charged=3,peak_rss=4,max_sample_gap=5,sampled_roles=['server'],
        namespace_exit_gaps=6,disk_census_history=[],original_recheck_counts={},original_recheck_history={})
    history={key:copy.deepcopy(terminal[key]) for key in ('stops','disk_peak','log_bytes','peak_charged','peak_rss',
        'max_sample_gap','sampled_roles','namespace_exit_gaps','disk_census_history','original_recheck_counts','original_recheck_history')}
    history.update(observer_budget_reset=False,historical_request_accounting='UNKNOWN',
        observer_charge_kind='cumulative_terminal_charge_with_historical_conservative_reserves',
        interrupted_owner_cleanup='UNKNOWN',original_wait_available=False,latest_owner_cleanup='positive',
        latest_original_wait_available=True,observer_requests=REDACTED_COORDINATE_E41D64DB57,decoded_bytes=REDACTED_COORDINATE_FAD31DA6EC)
    accounting=dict(schema='positive-terminal-observer-accounting-v1',status='positive_terminal_cumulative_charge',
        observer_budget_reset=False,historical_request_accounting='UNKNOWN',requests=REDACTED_COORDINATE_E41D64DB57,decoded_bytes=REDACTED_COORDINATE_FAD31DA6EC,
        prior_status_rounds=REDACTED_COORDINATE_F15223DCC0,durable_completed_rounds=60,uncompleted_round_reserve=1,status_rounds_charge=REDACTED_COORDINATE_BC57590A33,
        last_sample_at=REDACTED_COORDINATE_9E967B95C5)
    return history,accounting,allowance(),terminal,wait,old


def positive_baseline(terminal,wait):
    return dict(status='complete',before={'same':1},after={'same':1},source_evidence={'same':1},changed=[],
        exclusive_locks_acquired=2,ownership_status='both_existing_flocks_held',historical_positive_stop=False,
        interrupted_owner_cleanup='UNKNOWN',original_wait_available=False,historical_lost_executor='prior-segment-output-A',
        historical_other_lost_executor='prior-segment-output-B',latest_run='prior-segment-output-C',latest_original_wait_available=True,
        latest_owner_cleanup='positive',latest_original_wait=copy.deepcopy(wait),latest_stop=copy.deepcopy(terminal['stops'][-1]),
        completed=copy.deepcopy(terminal['readback']),completed_manifest_sha256='a'*64,prior2042_preserved=REDACTED_COORDINATE_D8EC3903C8,
        prior587_preserved=REDACTED_COORDINATE_82A93B152B,restart2013_before_preserved=REDACTED_COORDINATE_7931AA2A1B,restart2013_after_preserved=REDACTED_COORDINATE_7931AA2A1B,
        native_ancestry_before={'same':1},native_ancestry_after={'same':1},
        journal=dict(inputs=46,clean_inputs=45,frames=REDACTED_COORDINATE_AAC3F5EB03,published_frames=REDACTED_COORDINATE_61682EB8F8,pages=74,finalized=False,
            active_input=dict(id='repeated-prefix-046',frames=REDACTED_COORDINATE_B01236484B,normalized_start=REDACTED_COORDINATE_3E05375E3C,clean_eof=False)))


class ContinuationControls(unittest.TestCase):
    def test_exact_clean014(self):self.assertEqual(require_checkpoint(checkpoint(),checkpoint()),REDACTED_COORDINATE_D1C744E94F)
    def test_old_offset_refused(self):
        p=checkpoint();p['frames']-=1
        with self.assertRaises(RuntimeError):require_checkpoint(p,checkpoint())
    def test_invented_active_input_refused(self):
        p=checkpoint();p['active_input']={'id':'repeated-prefix-015'}
        with self.assertRaises(RuntimeError):require_checkpoint(p,checkpoint())
    def test_preserve_all587(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'a';b=Path(d)/'b';rows=''.join(json.dumps(dict(id=f'{i:04}',sha256='a'*64))+'\n' for i in range(REDACTED_COORDINATE_82A93B152B));a.write_text(rows);b.write_text(rows)
            baseline=dict(completed={'completed':REDACTED_COORDINATE_82A93B152B,'origin':REDACTED_COORDINATE_24EB33C5F8,'end':REDACTED_COORDINATE_99A154A5BA});summary=dict(completed=REDACTED_COORDINATE_82A93B152B,origin=REDACTED_COORDINATE_24EB33C5F8,end=REDACTED_COORDINATE_99A154A5BA,eos=0)
            self.assertEqual(preserve(baseline,a,b,summary),REDACTED_COORDINATE_82A93B152B)
            b.write_text(rows.replace('a'*64,'b'*64,1))
            with self.assertRaises(RuntimeError):preserve(baseline,a,b,summary)
    def test_checkpoint_exact_extra_fields(self):
        p=checkpoint();p['checkpoint_sha256']='changed'
        with self.assertRaises(RuntimeError):require_checkpoint(p,checkpoint())
    def test_partial_checkpoint_structural_geometry(self):
        p=dict(inputs=46,clean_inputs=45,frames=REDACTED_COORDINATE_AAC3F5EB03,published_frames=REDACTED_COORDINATE_61682EB8F8,
               finalized=False,pages=74,active_input=dict(frames=REDACTED_COORDINATE_B01236484B,normalized_start=REDACTED_COORDINATE_3E05375E3C,clean_eof=False))
        self.assertEqual(require_checkpoint(p,copy.deepcopy(p)),REDACTED_COORDINATE_61682EB8F8)
        for key in ('frames','normalized_start'):
            q=copy.deepcopy(p);q['active_input'][key]+=1
            with self.assertRaises(RuntimeError):require_checkpoint(q,q)
    def test_checkpoint_boolean_integer_refused(self):
        for key in ('inputs','clean_inputs','frames','published_frames','pages'):
            p=checkpoint();p[key]=True
            with self.assertRaises(RuntimeError):require_checkpoint(p,p)
    def test_preserve_dynamic_count(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'a';b=Path(d)/'b'
            rows=''.join(json.dumps(dict(id=f'{i:04}',sha256='a'*64))+'\n' for i in range(3))
            a.write_text(rows);b.write_text(rows)
            baseline=dict(completed=dict(completed=3,origin=REDACTED_COORDINATE_24EB33C5F8,end=REDACTED_COORDINATE_A665A45920))
            self.assertEqual(preserve(baseline,a,b,dict(completed=3,origin=REDACTED_COORDINATE_24EB33C5F8,end=REDACTED_COORDINATE_A665A45920,eos=0)),3)
            baseline['completed']['completed']=4
            with self.assertRaises(RuntimeError):preserve(baseline,a,b,dict(completed=4,origin=REDACTED_COORDINATE_24EB33C5F8,end=REDACTED_COORDINATE_A665A45920,eos=0))
    def test_unknown_cleanup_requires_exact_held_lock_baseline(self):
        baseline=dict(status='complete',before={'a':1},after={'a':1},changed=[],exclusive_locks_acquired=2,
            latest_original_wait_available=False,latest_owner_cleanup='UNKNOWN',
            ownership_status='both_existing_flocks_held',
            latest_cleanup_proof='UNKNOWN; no original wait/terminal required or inferred')
        require_lost_baseline(baseline)
        for key,value in (('exclusive_locks_acquired',1),('exclusive_locks_acquired',True),
                ('latest_original_wait_available',True),('latest_owner_cleanup','positive'),
                ('latest_original_wait',{'positive_original_wait':True}),('latest_stop',{'positive':True}),
                ('ownership_status','busy'),('changed',['a']),('after',{'a':2}),('status','failed')):
            with self.subTest(key=key,value=value):
                with self.assertRaises(RuntimeError):require_lost_baseline(dict(baseline,**{key:value}))
    def test_accounting_actual_bound_and_drift(self):
        history,evidence=legacy_accounting()
        require_accounting(history,evidence,allowance())
        for key,value in (('observer_requests',REDACTED_COORDINATE_F8D64A31EB),('decoded_bytes',REDACTED_COORDINATE_3D0CC8F301),
                          ('observer_budget_reset',True),('latest_owner_cleanup','positive'),
                          ('latest_original_wait_available',True)):
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):require_accounting(dict(history,**{key:value}),evidence,allowance())
        for key,value in (('kind','measured'),('body_reserve_slots',0),('body_reserve_each',524288),
                          ('additional_unlogged_tail_request_reserve',0),('requests',REDACTED_COORDINATE_7A62EFD0AD),('decoded_bytes',REDACTED_COORDINATE_8F5D0E774C)):
            changed=copy.deepcopy(evidence);changed['http']['conservative_continuation_charge'][key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):require_accounting(history,changed,allowance())
    def operation(self):
        ops=Operations.__new__(Operations);calls=[]
        recovered=dict(chunk=dict(id=FAILED_CHUNK,status='completed',next_offset=REDACTED_COORDINATE_80B8062FEF),batch={'id':'same'},failed_count=0)
        ops.root=Path('/unused');ops.baseline=dict(journal=checkpoint(),recovered=recovered)
        ops.r={'resume':{'completed_manifest':'old'},'database':'same'};ops.plan=SimpleNamespace(session_id='same-session',capture_id='same-capture',origin=REDACTED_COORDINATE_24EB33C5F8)
        ops.check=lambda:None;ops.launch_server=lambda:calls.append('launch');ops.proof=lambda label:('new',{})
        ops.checkpoint=lambda:checkpoint();ops.helper=lambda args:copy.deepcopy(recovered)
        ops.family=SimpleNamespace(listener_owned=lambda:calls.append('owned'))
        def http(path,method='GET',*args):
            self.assertEqual(method,'GET');calls.append(path)
            if path=='/health':return REDACTED_COORDINATE_27BADC983D,{'worker_alive':True}
            self.assertEqual(path,ops.capture_path)
            return REDACTED_COORDINATE_27BADC983D,dict(mode='sealed_pages',capture_id='same-capture',capture_origin_frame=REDACTED_COORDINATE_24EB33C5F8,
                            page_count=0,released_pages=0,pending_page=None)
        ops.http=http;ops.event=lambda kind,value:calls.append(kind)
        return ops,calls
    def run_start(self,ops):
        with patch('adapter.resume_state.verify_sources'),patch('adapter.resume_state.preserve',return_value=REDACTED_COORDINATE_82A93B152B):return ops.resume_start()
    def test_no_retry_post_on_continuation(self):
        ops,calls=self.operation();self.assertEqual(self.run_start(ops),'same-session');self.assertEqual(calls,['launch','owned','/health',ops.capture_path,'receipt-liveness','continuation-conservation'])
    def test_new_failed_work_refused_no_retry(self):
        ops,calls=self.operation();old=ops.helper;ops.helper=lambda args:dict(old(args),failed_count=1)
        with self.assertRaises(RuntimeError):self.run_start(ops)
        self.assertEqual(calls,['launch'])
    def test_former_completed_identity_change_refused(self):
        ops,calls=self.operation();ops.helper=lambda args:dict(chunk={'id':'other'},batch={'id':'same'},failed_count=0)
        with self.assertRaises(RuntimeError):self.run_start(ops)
    def test_dead_worker_refused(self):
        ops,calls=self.operation();ops.http=lambda *args:(REDACTED_COORDINATE_27BADC983D,{'worker_alive':False})
        with self.assertRaises(RuntimeError):self.run_start(ops)
    def test_real_disabled_release_shape(self):
        # No fallback to an earlier candidate and no skip if qualification is
        # incomplete. Root must first supply this runner's pinned proposal.
        path=Path(__file__).parent/'resume-proposal/endurance-release.json'
        release=json.loads(path.read_bytes())
        self.assertIs(release['approved_for_actual'],False)
        self.assertEqual(release['product_transition']['from_version'],'0.2.0rc3.dev65')
        self.assertEqual(release['product_transition']['to_version'],'0.2.0rc3.dev66')
        self.assertEqual(release['resume']['previous_status_rounds'],REDACTED_COORDINATE_7DEEF068FE)
        with patch('resume_state.time.time',return_value=release['resume']['term_deadline_unix']-release['resume']['outer_timeout_seconds']-1):
            proof=validate(release)
            self.assertEqual(proof,json.loads(Path(release['resume']['baseline_receipt']).read_bytes()))
            self.assertEqual(proof['completed']['completed'],REDACTED_COORDINATE_C9DC1FB0C0)
            self.assertEqual(proof['completed']['result_rows'],REDACTED_COORDINATE_0B42D71285)
            self.assertEqual(proof['latest_owner_cleanup'],'UNKNOWN')
            self.assertEqual(proof['interrupted_owner_cleanup'],'UNKNOWN')
            self.assertEqual(proof['journal']['active_input']['id'],'repeated-prefix-075')
            release['resume']['retry_post_allowed']=True
            with self.assertRaises(RuntimeError):validate(release)
    def test_no_retry_implementation_in_continuation(self):
        import resume_state
        self.assertFalse(hasattr(resume_state,'retry_failed'))

class PositiveTerminalControls(unittest.TestCase):
    def test_terminal_carry_accepts_latest_positive_without_rewriting_unknown(self):
        args=positive_fixture();require_terminal_accounting(*args)
        self.assertEqual(args[0]['observer_requests'],REDACTED_COORDINATE_E41D64DB57)
        self.assertEqual(args[0]['decoded_bytes'],REDACTED_COORDINATE_FAD31DA6EC)
        self.assertEqual(args[1]['status_rounds_charge'],REDACTED_COORDINATE_BC57590A33)
        self.assertEqual([row['positive'] for row in args[0]['stops']],['UNKNOWN','UNKNOWN',True])
    def test_terminal_accounting_counter_reset_drift_and_boolean_refused(self):
        for key,value in (('requests',REDACTED_COORDINATE_1C918023D6),('decoded_bytes',REDACTED_COORDINATE_129FEF76C1),('requests',True),
                ('observer_budget_reset',True),('historical_request_accounting','known'),
                ('prior_status_rounds',0),('durable_completed_rounds',59),('uncompleted_round_reserve',0),
                ('status_rounds_charge',REDACTED_COORDINATE_2B4DA94214),('last_sample_at',REDACTED_COORDINATE_4FC4CF8E49)):
            args=list(positive_fixture());args[1][key]=value
            with self.subTest(key=key,value=value):
                with self.assertRaises(RuntimeError):require_terminal_accounting(*args)
    def test_terminal_scope_allowance_and_history_drift_refused(self):
        for key,value in (('latest_owner_cleanup','UNKNOWN'),('latest_original_wait_available',False),
                ('observer_requests',0),('decoded_bytes',0),('original_wait_available',True),
                ('interrupted_owner_cleanup','positive'),('observer_budget_reset',True),
                ('observer_charge_kind','actual_usage'),('disk_peak',0),('log_bytes',0)):
            args=list(positive_fixture());args[0][key]=value
            with self.subTest(key=key,value=value):
                with self.assertRaises(RuntimeError):require_terminal_accounting(*args)
        args=list(positive_fixture());args[2]['segment_get_cap']+=1
        with self.assertRaises(RuntimeError):require_terminal_accounting(*args)
    def test_terminal_root_wait_and_cleanup_fail_closed(self):
        for key,value in (('returncode',0),('returncode',True),('positive_original_wait',False)):
            args=list(positive_fixture());args[4][key]=value
            with self.subTest(key=key,value=value):
                with self.assertRaises(RuntimeError):require_terminal_accounting(*args)
        for key,value in (('positive',False),('survivors',[{'pid':1}]),('errors',['unknown']),
                          ('lifespan',False),('forced',True),('generation',True),
                          ('segment_output','prior-segment-output-B'),('cleanup',[])):
            args=list(positive_fixture());args[3]['stops'][-1][key]=value
            with self.subTest(key=key,value=value):
                with self.assertRaises(RuntimeError):require_terminal_accounting(*args)
        args=list(positive_fixture());args[3]['stops'][-1]['cleanup'][0]['positive_original_wait']=False
        with self.assertRaises(RuntimeError):require_terminal_accounting(*args)
    def test_terminal_cannot_rewrite_earlier_unknown_or_failure(self):
        for index in (0,1):
            args=list(positive_fixture());args[3]['stops'][index]['positive']=True
            args[0]['stops']=copy.deepcopy(args[3]['stops'])
            with self.assertRaises(RuntimeError):require_terminal_accounting(*args)
        for key,value in (('operations_pass',True),('recovered_operations_pass',True),
                ('uninterrupted_endurance_pass',True),('recovery_baseline_completed_preserved',REDACTED_COORDINATE_9E088CDDB9),
                ('supervisor_ownership_retained_through_terminal_fsync',False),
                ('supervisor_ownership_release_after_terminal_authorized',False),
                ('original_global_observer_quota_proven',True),('cleanup_or_readback_error','failed')):
            args=list(positive_fixture());args[3][key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):require_terminal_accounting(*args)
        args=list(positive_fixture());args[3]['observer_carry_charge']['requests']=0
        with self.assertRaises(RuntimeError):require_terminal_accounting(*args)
    def test_positive_stopped_baseline_matches_terminal(self):
        _,_,_,terminal,wait,_=positive_fixture();baseline=positive_baseline(terminal,wait)
        require_positive_baseline(baseline,terminal,wait)
        for key,value in (('latest_owner_cleanup','UNKNOWN'),('latest_original_wait_available',False),
                ('exclusive_locks_acquired',True),('source_evidence',{}),('prior2042_preserved',REDACTED_COORDINATE_9E088CDDB9),
                ('restart2013_after_preserved',0),('native_ancestry_after',{}),
                ('historical_positive_stop',True),('original_wait_available',True)):
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):require_positive_baseline(dict(baseline,**{key:value}),terminal,wait)
        for key,value in (('frames',REDACTED_COORDINATE_D64B22F96D),('id','repeated-prefix-047'),('clean_eof',True)):
            changed=copy.deepcopy(baseline);changed['journal']['active_input'][key]=value
            with self.assertRaises(RuntimeError):require_positive_baseline(changed,terminal,wait)
        changed=copy.deepcopy(baseline);changed['latest_original_wait']['returncode']=0
        with self.assertRaises(RuntimeError):require_positive_baseline(changed,terminal,wait)
        changed=copy.deepcopy(baseline);changed['completed']['manifest_sha256']='b'*64
        with self.assertRaises(RuntimeError):require_positive_baseline(changed,terminal,wait)


class ExactProductTransitionControls(unittest.TestCase):
    def inventories(self):
        before={key:'a'*64 for key in ALLOWED if not key.endswith('/native_root_reattestation.py')}
        after={key:'b'*64 for key in ALLOWED}
        before['audio_backend/resources.py']=after['audio_backend/resources.py']='c'*64
        before['long_audio_backend-0.2.0rc3.dev65.dist-info/METADATA']='d'*64
        after['long_audio_backend-0.2.0rc3.dev66.dist-info/METADATA']='e'*64
        return before,after
    def test_exact_six_file_delta(self):
        before,after=self.inventories();changes=package_delta(before,after)
        self.assertEqual({k for k in changes if k.startswith('audio_backend/')},ALLOWED)
        self.assertIsNone(changes['audio_backend/native_root_reattestation.py']['before'])
    def test_code_runtime_metadata_and_removal_deltas_refused(self):
        for key,value in (('audio_backend/resources.py','changed'),('dependency.py','added'),
                          ('other.dist-info/METADATA','added')):
            before,after=self.inventories();after[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):package_delta(before,after)
        before,after=self.inventories();del after['audio_backend/app.py']
        with self.assertRaises(RuntimeError):package_delta(before,after)
        before,after=self.inventories();after['audio_backend/app.py']=before['audio_backend/app.py']
        with self.assertRaises(RuntimeError):package_delta(before,after)
    def test_exact_frozen_nine_file_source_allowlist(self):
        before={key:dict(bytes=1,sha256='a'*64) for key in SOURCE_ALLOWED if not key.endswith('/native_root_reattestation.py')}
        after={key:dict(bytes=2,sha256='b'*64) for key in SOURCE_ALLOWED}
        before['audio_backend/resources.py']=after['audio_backend/resources.py']=dict(bytes=3,sha256='c'*64)
        base=dict(version='0.2.0rc3.dev65',files=before)
        source=dict(version='0.2.0rc3.dev66',files=after,installed_audio_backend_delta=sorted(ALLOWED),
                    change_allowlist=sorted(SOURCE_ALLOWED))
        require_source_delta(source,base)
        for key,value in (('version','0.2.0rc3.dev67'),('installed_audio_backend_delta',[]),('change_allowlist',[])):
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):require_source_delta(dict(source,**{key:value}),base)
        changed=copy.deepcopy(source);changed['files']['audio_backend/resources.py']['sha256']='d'*64
        with self.assertRaises(RuntimeError):require_source_delta(changed,base)

    def argv_fixture(self):
        old=dict(controls=dict(server_argv=['/python','-B','-m','audio_backend','--verified-identity-policy','/old/profile.json']))
        proof=dict(path='/reviewed/attestation.json',sha256='a'*64)
        release=dict(product_transition=dict(native_root_reattestation=proof),source_pins={proof['path']:proof['sha256']},
            controls=dict(server_argv=['/python','-B','-m','audio_backend','--verified-identity-policy','/new/profile.json',
                '--native-root-reattestation',proof['path'],'--native-root-reattestation-sha256',proof['sha256']]))
        return release,old
    def test_exact_paired_flags_only(self):
        release,old=self.argv_fixture();require_server_argv(release,old,'/old','/new')
        for suffix in (['--threads','4'],['--native-root-reattestation','/other']):
            changed=copy.deepcopy(release);changed['controls']['server_argv']+=suffix
            with self.assertRaises(RuntimeError):require_server_argv(changed,old,'/old','/new')
        for index,value in ((0,'/different-python'),(-1,'b'*64),(-3,'/different-artifact')):
            changed=copy.deepcopy(release);changed['controls']['server_argv'][index]=value
            with self.assertRaises(RuntimeError):require_server_argv(changed,old,'/old','/new')
        changed=copy.deepcopy(release);changed['source_pins']={}
        with self.assertRaises(RuntimeError):require_server_argv(changed,old,'/old','/new')
        changed=copy.deepcopy(release);changed['controls']['server_argv']=changed['controls']['server_argv'][:-2]
        with self.assertRaises(RuntimeError):require_server_argv(changed,old,'/old','/new')


class Loss20ContinuationControls(unittest.TestCase):
    def setUp(self):
        current=json.loads((Path(__file__).parent/'resume-proposal/endurance-release.json').read_bytes())
        run25=json.loads(Path(current['resume']['previous_release']).read_bytes())
        self.release=json.loads(Path(run25['resume']['previous_release']).read_bytes())
        r=self.release['resume']
        self.old=json.loads(Path(r['previous_release']).read_bytes())
        self.baseline=json.loads(Path(r['baseline_receipt']).read_bytes())
        self.history=json.loads(Path(r['recovered_history']).read_bytes())
        self.accounting=json.loads(Path(r['accounting_evidence']).read_bytes())
        self.prior_history=json.loads(Path(self.old['resume']['recovered_history']).read_bytes())
        prior_accounting=json.loads(Path(self.old['resume']['accounting_evidence']).read_bytes())
        self.terminal=json.loads(Path(prior_accounting['terminal']).read_bytes())
        self.wait=json.loads(Path(prior_accounting['root_wait']).read_bytes())
    def check_accounting(self):
        require_loss20_accounting(self.history,self.accounting,allowance(),self.prior_history,self.old)
    def test_exact_carried_accounting_and_partial067_baseline(self):
        self.check_accounting()
        require_loss20_baseline(self.baseline,self.terminal,self.wait)
        self.assertEqual(self.history['observer_requests'],REDACTED_COORDINATE_7DCCA41D83)
        self.assertEqual(self.history['decoded_bytes'],REDACTED_COORDINATE_0B760BF1C8)
        self.assertEqual(self.accounting['status_rounds_charge'],REDACTED_COORDINATE_434DBD07BC)
        self.assertEqual(self.baseline['journal']['clean_inputs'],66)
        self.assertEqual(self.baseline['journal']['active_input']['frames'],REDACTED_COORDINATE_A1B9726EA0)
    def test_loss20_cannot_promote_latest_cleanup(self):
        for key,value in (('latest_owner_cleanup','positive'),('latest_original_wait_available',True),
                ('observer_budget_reset',True),('historical_request_accounting','known'),
                ('observer_charge_kind','actual_usage')):
            original=copy.deepcopy(self.history);self.history[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.history=original
    def test_loss20_cannot_lose_prior_reserve_or_add_measured_usage_claim(self):
        for section,key,value in (('prior_carried','decoded_bytes',0),('prior_carried','requests',0),
                ('charged_prefix','is_actual_usage',True),('charged_prefix','requests',REDACTED_COORDINATE_10DC397496),
                ('charged_prefix','decoded_bytes',REDACTED_COORDINATE_B90499B95B),('conservative_charge','requests',REDACTED_COORDINATE_25F2044C9F),
                ('conservative_charge','decoded_bytes',REDACTED_COORDINATE_880F2E2CD7),('conservative_charge','unlogged_tail_requests',0),
                ('conservative_charge','body_reserve_slots',7),('conservative_charge','body_reserve_each',524288),
                ('conservative_charge','requests',True),('conservative_charge','unlogged_tail_requests',True)):
            original=copy.deepcopy(self.accounting);self.accounting[section][key]=value
            with self.subTest(section=section,key=key,value=value):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.accounting=original
    def test_loss20_durable_sequence_and_round_mutations_refused(self):
        for key,value in (('rows',REDACTED_COORDINATE_339D9D13ED),('body_bytes',0),('missing_sequences',[]),
                ('first_sequence',REDACTED_COORDINATE_4332CD7659),('last_sequence',REDACTED_COORDINATE_7DCCA41D83),('new_request_attempts_lower_bound',REDACTED_COORDINATE_0AA598C735)):
            original=copy.deepcopy(self.accounting);self.accounting['durable_http'][key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.accounting=original
        for key,value in (('prior_status_rounds',0),('durable_completed_rounds',REDACTED_COORDINATE_8882046218),
                ('uncompleted_round_reserve',0),('status_rounds_charge',REDACTED_COORDINATE_BC10B57514),
                ('status_rounds_charge',True),('last_sample_at',REDACTED_COORDINATE_329F7E642B)):
            original=copy.deepcopy(self.accounting);self.accounting[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.accounting=original
    def test_loss20_resource_and_stop_history_cannot_reset(self):
        for key,value in (('disk_peak',0),('log_bytes',0),('peak_charged',0),('peak_rss',0),
                ('namespace_exit_gaps',0),('max_sample_gap',0),('sampled_roles',[]),
                ('disk_census_history',[]),('original_recheck_counts',{}),('stops',[])):
            original=copy.deepcopy(self.history);self.history[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.history=original
        self.history['stops'][-1]['positive']=True
        with self.assertRaises(RuntimeError):self.check_accounting()
    def test_loss20_baseline_immutability_unknown_and_prior_stop_mutations(self):
        for key,value in (('exclusive_locks_acquired',True),('exclusive_locks_acquired',1),
                ('source_evidence',{}),('latest_run','prior-segment-output-C'),('latest_owner_cleanup','positive'),
                ('latest_original_wait_available',True),('latest_stop',{'positive':True}),
                ('prior_run17_owner_cleanup','UNKNOWN'),('prior_run17_original_wait_available',False),
                ('prior2042_preserved',0),('restart2013_after_preserved',0),('native_ancestry_after',{})):
            changed=copy.deepcopy(self.baseline);changed[key]=value
            with self.subTest(key=key,value=value):
                with self.assertRaises(RuntimeError):require_loss20_baseline(changed,self.terminal,self.wait)
    def test_loss20_baseline_exact_completed_and_cursor_mutations(self):
        for key,value in (('completed',REDACTED_COORDINATE_0D46EBF59E),('result_rows',REDACTED_COORDINATE_9901EFE13F),('end',REDACTED_COORDINATE_8D214F94BD),('eos',True),
                ('manifest_sha256','0'*64)):
            changed=copy.deepcopy(self.baseline);changed['completed'][key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):require_loss20_baseline(changed,self.terminal,self.wait)
        for key,value in (('id','repeated-prefix-046'),('frames',REDACTED_COORDINATE_FE0AC72A9E),('normalized_start',REDACTED_COORDINATE_A9A9C0333D),
                ('clean_eof',True)):
            changed=copy.deepcopy(self.baseline);changed['journal']['active_input'][key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):require_loss20_baseline(changed,self.terminal,self.wait)
    def test_same_product_cannot_change_runtime_or_argv(self):
        for key,value in (('runtime_manifest',{}),('product_transition',{})):
            changed=copy.deepcopy(self.release);changed[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):validate_product(changed,self.old)
        changed=copy.deepcopy(self.release);changed['controls']['server_argv']+=['--threads','4']
        with self.assertRaises(RuntimeError):validate_product(changed,self.old)
        changed=copy.deepcopy(self.release);changed['controls']['package_version']='0.2.0rc3.dev67'
        with self.assertRaises(RuntimeError):validate_product(changed,self.old)
    def artifact_fixture(self):
        proof=self.release['product_transition']['native_root_reattestation']
        artifact=json.loads(Path(proof['path']).read_bytes())
        anchor=json.loads(Path(artifact['baseline']['path']).read_bytes())
        objects={proof['path']:artifact,artifact['baseline']['path']:anchor,
                 self.release['resume']['baseline_receipt']:self.baseline}
        return artifact,anchor,objects
    def test_artifact_baseline14_is_not_replaced_by_progressed_baseline15(self):
        artifact,anchor,objects=self.artifact_fixture()
        with patch('product_transition.pinned_json',side_effect=lambda release,path:objects[path]):
            require_continued_artifact(self.release,self.old)
            artifact['baseline']=dict(path=self.release['resume']['baseline_receipt'],
                sha256=self.release['source_pins'][self.release['resume']['baseline_receipt']])
            with self.assertRaises(RuntimeError):require_continued_artifact(self.release,self.old)
    def test_artifact_exact_pin_cannot_be_refreshed(self):
        changed=copy.deepcopy(self.release)
        changed['product_transition']['native_root_reattestation']['sha256']='0'*64
        with self.assertRaises(RuntimeError):require_continued_artifact(changed,self.old)
    def test_artifact_current_binding_ancestry_and_lock_identity_must_match(self):
        for field in ('native_binding','native_ancestry_before','native_ancestry_after','ownership_locks','anchor_baseline','retained_root_artifact'):
            artifact,anchor,objects=self.artifact_fixture()
            current=copy.deepcopy(self.baseline);current[field]={}
            objects[self.release['resume']['baseline_receipt']]=current
            with patch('product_transition.pinned_json',side_effect=lambda release,path:objects[path]):
                with self.subTest(field=field):
                    with self.assertRaises(RuntimeError):require_continued_artifact(self.release,self.old)
    def test_artifact_cannot_accept_jointly_changed_root_or_binding(self):
        artifact,anchor,objects=self.artifact_fixture()
        current=copy.deepcopy(self.baseline);objects[self.release['resume']['baseline_receipt']]=current
        for baseline in (anchor,current):
            baseline['native_ancestry_before']['ancestors'][0]['device']=53
            baseline['native_ancestry_after']['ancestors'][0]['device']=53
        with patch('product_transition.pinned_json',side_effect=lambda release,path:objects[path]):
            with self.assertRaises(RuntimeError):require_continued_artifact(self.release,self.old)
        artifact,anchor,objects=self.artifact_fixture()
        current=copy.deepcopy(self.baseline);objects[self.release['resume']['baseline_receipt']]=current
        for baseline in (anchor,current):baseline['native_binding']['stored']['policy']='changed'
        with patch('product_transition.pinned_json',side_effect=lambda release,path:objects[path]):
            with self.assertRaises(RuntimeError):require_continued_artifact(self.release,self.old)


class Loss23ContinuationControls(unittest.TestCase):
    def setUp(self):
        current=json.loads((Path(__file__).parent/'resume-proposal/endurance-release.json').read_bytes())
        self.release_path=Path(current['resume']['previous_release'])
        self.release=json.loads(self.release_path.read_bytes())
        r=self.release['resume'];self.old=json.loads(Path(r['previous_release']).read_bytes())
        self.run20=json.loads(Path(self.old['resume']['previous_release']).read_bytes())
        self.history=json.loads(Path(r['recovered_history']).read_bytes())
        self.accounting=json.loads(Path(r['accounting_evidence']).read_bytes())
        self.prior_history=json.loads(Path(self.old['resume']['recovered_history']).read_bytes())
        self.baseline=json.loads(Path(r['baseline_receipt']).read_bytes())
        self.prior_baseline=json.loads(Path(self.old['resume']['baseline_receipt']).read_bytes())
        positive=json.loads(Path(self.run20['resume']['accounting_evidence']).read_bytes())
        self.terminal=json.loads(Path(positive['terminal']).read_bytes())
        self.wait=json.loads(Path(positive['root_wait']).read_bytes())
    def check_accounting(self):
        require_loss23_accounting(self.history,self.accounting,allowance(),self.prior_history,self.old)
    def check_baseline(self,baseline=None):
        require_loss23_baseline(self.baseline if baseline is None else baseline,
                               self.terminal,self.wait,self.prior_baseline)
    def test_exact_loss23_carry_and_unchanged_partial067(self):
        self.check_accounting();self.check_baseline()
        self.assertEqual(self.history['observer_requests'],REDACTED_COORDINATE_09C2A7BBA8)
        self.assertEqual(self.history['decoded_bytes'],REDACTED_COORDINATE_4F8B747129)
        self.assertEqual(self.accounting['status_rounds_charge'],REDACTED_COORDINATE_A183251A84)
        self.assertEqual(self.baseline['journal'],self.prior_baseline['journal'])
        self.assertEqual(self.baseline['prior2989_preserved'],REDACTED_COORDINATE_C311B90801)
    def test_latest_loss23_and_earlier_loss20_stay_unknown(self):
        for key,value in (('latest_owner_cleanup','positive'),('latest_original_wait_available',True),
                ('observer_budget_reset',True),('observer_charge_kind','actual_usage')):
            original=copy.deepcopy(self.history);self.history[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.history=original
        for index in (-1,-2):
            original=copy.deepcopy(self.history);self.history['stops'][index]['positive']=True
            with self.assertRaises(RuntimeError):self.check_accounting()
            self.history=original
        changed=copy.deepcopy(self.baseline);changed['historical_run20_cleanup']='positive'
        with self.assertRaises(RuntimeError):self.check_baseline(changed)
    def test_loss23_missing_startup_and_tail_reserves_cannot_reset(self):
        for section,key,value in (('prior_carried','requests',REDACTED_COORDINATE_E41D64DB57),('prior_carried','decoded_bytes',REDACTED_COORDINATE_FAD31DA6EC),
                ('charged_prefix','is_actual_usage',True),('charged_prefix','requests',REDACTED_COORDINATE_EAAA9A5B38),
                ('conservative_charge','requests',REDACTED_COORDINATE_670A7F467E),('conservative_charge','decoded_bytes',REDACTED_COORDINATE_DC4EC9A439),
                ('conservative_charge','body_reserve_slots',8),('conservative_charge','body_reserve_each',524288),
                ('conservative_charge','unlogged_tail_requests',0),('conservative_charge','unlogged_tail_requests',True),
                ('durable_http','missing_sequences',[]),('durable_http','body_bytes',REDACTED_COORDINATE_8EF6E5EA68)):
            original=copy.deepcopy(self.accounting);self.accounting[section][key]=value
            with self.subTest(section=section,key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.accounting=original
    def test_loss23_round_sample_and_resource_carry_cannot_regress(self):
        for key,value in (('prior_status_rounds',REDACTED_COORDINATE_BC57590A33),('durable_completed_rounds',0),
                ('uncompleted_round_reserve',0),('status_rounds_charge',REDACTED_COORDINATE_73401DFC93),('last_sample_at',REDACTED_COORDINATE_C38F9F247A)):
            original=copy.deepcopy(self.accounting);self.accounting[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.accounting=original
        for key,value in (('disk_peak',0),('namespace_exit_gaps',REDACTED_COORDINATE_3641D61CA8),('log_bytes',0),
                ('disk_census_history',[]),('original_recheck_counts',{})):
            original=copy.deepcopy(self.history);self.history[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.history=original
    def test_loss23_baseline_cannot_be_previous_baseline_or_promote_wait(self):
        with self.assertRaises(RuntimeError):self.check_baseline(self.prior_baseline)
        for key,value in (('latest_run','prior-segment-output-D'),('latest_original_wait_available',True),
                ('latest_stop',{'positive':True}),('prior2989_preserved',REDACTED_COORDINATE_0D46EBF59E),('exclusive_locks_acquired',True)):
            changed=copy.deepcopy(self.baseline);changed[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_baseline(changed)
    def test_loss23_product_uses_fixed_qualification_lineage(self):
        installed=self.release['controls']['environment']['PYTHONPATH']
        self.assertEqual(validate_current_product(self.release,self.old),(installed,installed))
        with self.assertRaises(RuntimeError):validate_current_product(self.release,self.run20)
        changed=copy.deepcopy(self.release);changed['runtime_manifest']={}
        with self.assertRaises(RuntimeError):validate_current_product(changed,self.old)
    def test_qualified_snapshot_and_terminal_reserve_sources_are_unchanged(self):
        previous=Path(self.release['resume']['previous_release']).parent.parent
        current=self.release_path.parent.parent
        for name in ('adapter.py','disk_census.py'):
            with self.subTest(name=name):
                self.assertEqual((current/name).read_bytes(),(previous/name).read_bytes())
                self.assertEqual(self.release['source_pins'][str(current/name)],
                                 self.old['source_pins'][str(previous/name)])
    def provenance_objects(self):
        paths=[self.baseline['phase1_receipt']['path'],self.old['resume']['baseline_receipt'],
            self.release['resume']['previous_launch']]
        paths += [self.release['baseline16_provenance'][key]['path']
                  for key in ('phase2_root_wait','provenance_audit')]
        return {path:json.loads(Path(path).read_bytes()) for path in paths}
    def check_provenance(self,release=None,baseline=None,objects=None):
        release=self.release if release is None else release
        baseline=self.baseline if baseline is None else baseline
        if objects is None:
            require_baseline16_provenance(release,baseline,self.accounting,self.old)
        else:
            with patch('resume_state.pinned_json',side_effect=lambda release,path:objects[path]):
                require_baseline16_provenance(release,baseline,self.accounting,self.old)
    def test_phase1_accounting_and_phase2_data_are_separate(self):
        self.check_provenance()
        self.assertNotEqual(self.accounting['baseline_phase1_receipt'],self.release['resume']['baseline_receipt'])
        self.assertFalse(self.release['baseline16_provenance']['exact_executed_helper_bytes_proven'])
        self.assertEqual(self.baseline['lost_run20_release']['path'],self.old['resume']['previous_release'])
    def test_phase1_and_phase2_source_equality_cannot_be_weakened(self):
        objects=self.provenance_objects();phase=objects[self.baseline['phase1_receipt']['path']]
        phase['source_evidence']={}
        with self.assertRaises(RuntimeError):self.check_provenance(objects=objects)
        changed=copy.deepcopy(self.baseline);changed['source_evidence']=self.prior_baseline['source_evidence']
        with self.assertRaises(RuntimeError):self.check_provenance(baseline=changed)
    def test_native_reuse_and_single_copy_pins_are_exact(self):
        for field in ('baseline_sha256','raw_copy_sha256'):
            changed=copy.deepcopy(self.baseline);changed['native_reuse'][field]='0'*64
            with self.subTest(field=field):
                with self.assertRaises(RuntimeError):self.check_provenance(baseline=changed)
        changed=copy.deepcopy(self.release)
        del changed['source_pins'][self.baseline['native_reuse']['raw_copy']]
        with self.assertRaises(RuntimeError):self.check_provenance(release=changed)
        changed=copy.deepcopy(self.baseline);changed['snapshot_sha256']='0'*64
        with self.assertRaises(RuntimeError):self.check_provenance(baseline=changed)
    def test_helper_provenance_gap_and_lost_run_wait_cannot_be_promoted(self):
        for key in ('prelaunch_helper_sha_available','exact_executed_helper_bytes_proven'):
            changed=copy.deepcopy(self.release);changed['baseline16_provenance'][key]=True
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_provenance(release=changed)
        objects=self.provenance_objects()
        objects[self.release['baseline16_provenance']['phase2_root_wait']['path']]['positive_original_wait']=False
        with self.assertRaises(RuntimeError):self.check_provenance(objects=objects)
    def test_lost_run23_launch_and_readonly_audit_must_match(self):
        objects=self.provenance_objects();objects[self.release['resume']['previous_launch']]['argv']=[]
        with self.assertRaises(RuntimeError):self.check_provenance(objects=objects)
        objects=self.provenance_objects()
        objects[self.release['baseline16_provenance']['provenance_audit']['path']]['current_audit'][0]['sha256']='0'*64
        with self.assertRaises(RuntimeError):self.check_provenance(objects=objects)


class DiskCapacityAmendmentControls(unittest.TestCase):
    def setUp(self):
        current=json.loads((Path(__file__).parent/'resume-proposal/endurance-release.json').read_bytes())
        self.release=json.loads(Path(current['resume']['previous_release']).read_bytes())
        self.old=json.loads(Path(self.release['resume']['previous_release']).read_bytes())
        self.document_path=self.release['disk_capacity_amendment']['protocol_document']['path']
        self.document=json.loads(Path(self.document_path).read_bytes())
        self.protocol_path=self.document['disk_protocol']['path']
        self.protocol=json.loads(Path(self.protocol_path).read_bytes())
        self.audit_path=self.document['capacity_audit']['path']
        self.audit=json.loads(Path(self.audit_path).read_bytes())
    def check(self,release=None,old=None,document=None,protocol=None,audit=None):
        current=self.release if release is None else release
        previous=self.old if old is None else old
        def pinned(release,path):
            if path==self.document_path and document is not None:return document
            if path==self.protocol_path and protocol is not None:return protocol
            if path==self.audit_path and audit is not None:return audit
            return json.loads(Path(path).read_bytes())
        if document is None and protocol is None and audit is None:
            require_disk_capacity_amendment(current,previous)
        else:
            with patch('resume_state.pinned_json',side_effect=pinned):
                require_disk_capacity_amendment(current,previous)
    def test_exact_ten_gib_is_separate_from_incomplete_eight_gib(self):
        self.check()
        self.assertEqual(self.old['plan']['disk_bytes'],8589934592)
        self.assertEqual(self.release['plan']['disk_bytes'],10737418240)
        self.assertEqual({key for key in self.old['plan'] if self.old['plan'][key]!=self.release['plan'][key]},
                         {'disk_bytes'})
        self.assertEqual(self.document['prior_completion_verdict'],'capacity_limited_incomplete')
        self.assertFalse(self.document['completion_capacity_proven'])
        self.assertTrue(self.document['all_originals_retained'])
    def test_no_arbitrary_disk_cap_or_unamended_fallback(self):
        for value in (8589934592,REDACTED_COORDINATE_759E24300D,REDACTED_COORDINATE_9E9F767641,REDACTED_COORDINATE_6A8017FA44,True):
            changed=copy.deepcopy(self.release);changed['plan']['disk_bytes']=value
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):self.check(release=changed)
        changed=copy.deepcopy(self.old);changed['plan']['disk_bytes']=10737418240
        with self.assertRaises(RuntimeError):self.check(old=changed)
    def test_disk_amendment_cannot_change_any_other_plan_field(self):
        for key,value in self.old['plan'].items():
            if key=='disk_bytes':continue
            changed=copy.deepcopy(self.release)
            changed['plan'][key]=value+1 if type(value) is int else value+'-changed'
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check(release=changed)
        changed=copy.deepcopy(self.release);changed['plan']['extra_limit']=1
        with self.assertRaises(RuntimeError):self.check(release=changed)
    def test_amendment_and_all_evidence_pins_are_mandatory(self):
        changed=copy.deepcopy(self.release);del changed['disk_capacity_amendment']
        with self.assertRaises(RuntimeError):self.check(release=changed)
        paths=[self.document_path]+[self.document[key]['path'] for key in
               ('prior_release','accounting_evidence','capacity_audit','disk_protocol','envelope_provenance')]
        for path in paths:
            changed=copy.deepcopy(self.release);del changed['source_pins'][path]
            with self.subTest(path=path):
                with self.assertRaises(RuntimeError):self.check(release=changed)
    def test_amendment_cannot_promote_old_verdict_or_completion_capacity(self):
        for key,value in (('prior_completion_verdict','passed'),('prior_completion_verdict','measured_breach'),
                ('completion_capacity_proven',True),('all_originals_retained',False),
                ('prior_disk_bytes',10737418240),('new_disk_bytes',REDACTED_COORDINATE_6A8017FA44)):
            changed=copy.deepcopy(self.release);changed['disk_capacity_amendment'][key]=value
            with self.subTest(key=key,value=value):
                with self.assertRaises(RuntimeError):self.check(release=changed)
            document=copy.deepcopy(self.document);document[key]=value
            with self.assertRaises(RuntimeError):self.check(document=document)
    def test_protocol_algorithm_reserves_floor_and_sampling_are_unchanged(self):
        for key in ('future_final_snapshot_reserve_bytes','terminal_reserve_bytes','free_floor_bytes',
                    'normal_sample_seconds','last_clean_freshness_seconds','diagnostic_cap_bytes','log_cap_bytes'):
            protocol=copy.deepcopy(self.protocol);protocol[key]-=1
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check(protocol=protocol)
        for key,value in (('disk_bytes',8589934592),('hard_peak_guarantee',True),
                ('scope','exclude retained recovery copies'),('conservative_gap_charge','reset after gap')):
            protocol=copy.deepcopy(self.protocol);protocol[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check(protocol=protocol)
    def test_document_cannot_reset_deadlines_or_reanchor_accounting(self):
        for key in self.document['limits_unchanged']:
            document=copy.deepcopy(self.document);document['limits_unchanged'][key]+=1
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check(document=document)
        document=copy.deepcopy(self.document);document['accounting_evidence']=document['prior_release']
        with self.assertRaises(RuntimeError):self.check(document=document)
        document=copy.deepcopy(self.document);document['unreviewed_override']=True
        with self.assertRaises(RuntimeError):self.check(document=document)
    def test_capacity_audit_cannot_change_carry_retention_or_completion_scope(self):
        for section,key,value in (('decision','old_8gib_verdict','passed'),
                ('decision','full_remaining_completion_capacity_proven',True),
                ('decision','possible_capacity_stop',False),('decision','explicit_increase_bytes',0),
                ('retained_allocation','sampled_runroot_charge_bytes',0),
                ('retained_allocation','recovery_database_family_allocation_bytes',0),
                ('unchanged_constraints','observer_budget_reset',True),
                ('unchanged_constraints','originals_and_all_recovery_copies_preserved',False),
                ('unchanged_constraints','no_deletion_or_silent_exclusion',False),
                ('unchanged_constraints','observer_requests_carried',0),
                ('unchanged_constraints','observer_decoded_bytes_carried',0),
                ('unchanged_constraints','status_rounds_carried',0),
                ('sampling_limitations','hard_peak_guarantee',True)):
            audit=copy.deepcopy(self.audit);audit[section][key]=value
            with self.subTest(section=section,key=key):
                with self.assertRaises(RuntimeError):self.check(audit=audit)
    def test_capacity_inventory_and_audit_source_pins_cannot_be_dropped(self):
        for row in self.audit['source_pins']+[self.audit['retained_allocation']['inventory']]:
            changed=copy.deepcopy(self.release);del changed['source_pins'][row['path']]
            with self.subTest(path=row['path']):
                with self.assertRaises(RuntimeError):self.check(release=changed)
    def test_capacity_scope_cannot_remove_reserves_or_stop_possibility(self):
        for key,value in (('existing_disk_limit_bytes',8589934592),
                ('full_remaining_completion_capacity_proven',True),('possible_capacity_stop',False),
                ('future_final_snapshot_reserve_bytes',0),('terminal_reserve_bytes',0)):
            changed=copy.deepcopy(self.release);changed['disk_completion_scope'][key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check(release=changed)


class Loss25ContinuationControls(unittest.TestCase):
    def setUp(self):
        self.release=json.loads((Path(__file__).parent/'resume-proposal/endurance-release.json').read_bytes())
        r=self.release['resume'];self.old=json.loads(Path(r['previous_release']).read_bytes())
        self.run23=json.loads(Path(self.old['resume']['previous_release']).read_bytes())
        self.run20=json.loads(Path(self.run23['resume']['previous_release']).read_bytes())
        self.history=json.loads(Path(r['recovered_history']).read_bytes())
        self.accounting=json.loads(Path(r['accounting_evidence']).read_bytes())
        self.prior_history=json.loads(Path(self.old['resume']['recovered_history']).read_bytes())
        self.baseline=json.loads(Path(r['baseline_receipt']).read_bytes())
        self.prior_baseline=json.loads(Path(self.old['resume']['baseline_receipt']).read_bytes())
        positive=json.loads(Path(self.run20['resume']['accounting_evidence']).read_bytes())
        self.terminal=json.loads(Path(positive['terminal']).read_bytes())
        self.wait=json.loads(Path(positive['root_wait']).read_bytes())
    def check_accounting(self):
        require_loss25_accounting(self.history,self.accounting,allowance(),self.prior_history,self.old)
    def check_baseline(self,baseline=None):
        require_loss25_baseline(self.baseline if baseline is None else baseline,
                               self.terminal,self.wait,self.prior_baseline)
    def check_capacity(self,release=None):
        require_inherited_disk_capacity(self.release if release is None else release,self.old,self.run23)
    def test_exact_loss25_carry_and_proved_partial075(self):
        self.check_accounting();self.check_baseline();self.check_capacity()
        self.assertEqual((self.history['observer_requests'],self.history['decoded_bytes']),(REDACTED_COORDINATE_7CA2552FFD,REDACTED_COORDINATE_BC330203F5))
        self.assertEqual(self.accounting['status_rounds_charge'],REDACTED_COORDINATE_7DEEF068FE)
        self.assertEqual(self.baseline['journal']['clean_inputs'],74)
        self.assertEqual(self.baseline['journal']['active_input']['frames'],REDACTED_COORDINATE_07C9188141)
        self.assertEqual(self.baseline['completed']['completed'],REDACTED_COORDINATE_C9DC1FB0C0)
        self.assertEqual(self.baseline['completed']['result_rows'],REDACTED_COORDINATE_0B42D71285)
    def test_loss25_latest_and_earlier_cleanup_cannot_be_promoted(self):
        for key,value in (('latest_owner_cleanup','positive'),('latest_original_wait_available',True),
                ('observer_budget_reset',True),('observer_charge_kind','actual_usage')):
            original=copy.deepcopy(self.history);self.history[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.history=original
        for index in (-1,-2,-3):
            original=copy.deepcopy(self.history);self.history['stops'][index]['positive']=True
            with self.assertRaises(RuntimeError):self.check_accounting()
            self.history=original
    def test_loss25_startup_tail_and_prior_reserves_cannot_reset(self):
        for section,key,value in (('prior_carried','requests',REDACTED_COORDINATE_7DCCA41D83),('prior_carried','decoded_bytes',REDACTED_COORDINATE_0B760BF1C8),
                ('charged_prefix','is_actual_usage',True),('charged_prefix','requests',REDACTED_COORDINATE_8A97451623),
                ('conservative_charge','requests',REDACTED_COORDINATE_5C7A722D8D),('conservative_charge','decoded_bytes',REDACTED_COORDINATE_146CB954D5),
                ('conservative_charge','body_reserve_slots',9),('conservative_charge','body_reserve_each',524288),
                ('conservative_charge','unlogged_tail_requests',0),('conservative_charge','unlogged_tail_requests',True),
                ('durable_http','missing_sequences',[]),('durable_http','body_bytes',REDACTED_COORDINATE_AFF26331CD)):
            original=copy.deepcopy(self.accounting);self.accounting[section][key]=value
            with self.subTest(section=section,key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.accounting=original
    def test_loss25_round_sample_and_resource_carry_cannot_regress(self):
        for key,value in (('prior_status_rounds',REDACTED_COORDINATE_434DBD07BC),('durable_completed_rounds',128),
                ('uncompleted_round_reserve',0),('status_rounds_charge',REDACTED_COORDINATE_C676B6E9A4),('last_sample_at',REDACTED_COORDINATE_95CF99B035)):
            original=copy.deepcopy(self.accounting);self.accounting[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.accounting=original
        for key,value in (('disk_peak',0),('namespace_exit_gaps',REDACTED_COORDINATE_F632DEDA61),('log_bytes',0),
                ('disk_census_history',[]),('original_recheck_counts',{})):
            original=copy.deepcopy(self.history);self.history[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.history=original
    def test_loss25_accounting_preserves_capacity_and_artifact(self):
        for key,value in (('disk_capacity_amendment',{}),('retained_root_artifact',{})):
            original=copy.deepcopy(self.accounting);self.accounting[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_accounting()
            self.accounting=original
        self.accounting['disk']['disk_ceiling_bytes']=8589934592
        with self.assertRaises(RuntimeError):self.check_accounting()
    def test_loss25_exact_baseline_and_completed_geometry_cannot_drift(self):
        with self.assertRaises(RuntimeError):self.check_baseline(self.prior_baseline)
        for key,value in (('latest_run','prior-segment-output-E'),('latest_original_wait_available',True),
                ('latest_stop',{'positive':True}),('prior2989_preserved',REDACTED_COORDINATE_0D46EBF59E),('exclusive_locks_acquired',True),
                ('historical_cleanup_unknown',[])):
            changed=copy.deepcopy(self.baseline);changed[key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_baseline(changed)
        for key,value in (('completed',REDACTED_COORDINATE_15FC36B3E8),('result_rows',REDACTED_COORDINATE_739926914A),('end',REDACTED_COORDINATE_FAB0F33AFB),('eos',True),
                ('manifest_sha256','0'*64)):
            changed=copy.deepcopy(self.baseline);changed['completed'][key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_baseline(changed)
    def test_loss25_partial075_and_work_states_are_exact(self):
        for key,value in (('id','repeated-prefix-067'),('frames',REDACTED_COORDINATE_A47E910896),('normalized_start',REDACTED_COORDINATE_0A09C651C2),
                ('clean_eof',True)):
            changed=copy.deepcopy(self.baseline);changed['journal']['active_input'][key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_baseline(changed)
        changed=copy.deepcopy(self.baseline);changed['recovered']['states']['running']=0
        with self.assertRaises(RuntimeError):self.check_baseline(changed)
    def test_loss25_product_has_fixed_finite_predecessor_chain(self):
        installed=self.release['controls']['environment']['PYTHONPATH']
        self.assertEqual(validate_latest_product(self.release,self.old),(installed,installed))
        for previous in (self.run23,self.run20):
            with self.assertRaises(RuntimeError):validate_latest_product(self.release,previous)
        for key in ('runtime_manifest','product_transition'):
            changed=copy.deepcopy(self.release);changed[key]={}
            with self.assertRaises(RuntimeError):validate_latest_product(changed,self.old)
    def test_same_ten_gib_cannot_reapply_increase_or_change_other_limits(self):
        self.assertEqual(self.release['plan'],self.old['plan'])
        for key,value in self.old['plan'].items():
            changed=copy.deepcopy(self.release)
            changed['plan'][key]=value+1 if type(value) is int else value+'-changed'
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_capacity(changed)
        for value in (8589934592,REDACTED_COORDINATE_6A8017FA44,True):
            changed=copy.deepcopy(self.release);changed['plan']['disk_bytes']=value
            with self.assertRaises(RuntimeError):self.check_capacity(changed)
    def test_historical_capacity_document_cannot_be_reanchored_or_dropped(self):
        for key in ('disk_capacity_amendment','disk_completion_scope'):
            changed=copy.deepcopy(self.release);changed[key]={}
            with self.assertRaises(RuntimeError):self.check_capacity(changed)
        path=self.old['disk_capacity_amendment']['protocol_document']['path']
        changed=copy.deepcopy(self.release);del changed['source_pins'][path]
        with self.assertRaises(RuntimeError):self.check_capacity(changed)
        changed=copy.deepcopy(self.release);changed['resume']['term_deadline_unix']+=1
        with self.assertRaises(RuntimeError):self.check_capacity(changed)
    def test_qualified_runtime_and_reserve_sources_stay_byte_identical(self):
        previous=Path(self.release['resume']['previous_release']).parent.parent;current=Path(__file__).parent
        for name in ('adapter.py','disk_census.py','endurance.py','integration_controls.py','disk-protocol.json'):
            with self.subTest(name=name):
                self.assertEqual((current/name).read_bytes(),(previous/name).read_bytes())
                self.assertEqual(self.release['source_pins'][str(current/name)],self.old['source_pins'][str(previous/name)])
    def provenance_objects(self):
        paths=[self.baseline['phase1_receipt']['path'],self.old['resume']['baseline_receipt'],
            self.release['resume']['previous_launch'],self.release['product_transition']['native_root_reattestation']['path']]
        for key,pin in self.release['baseline17_provenance'].items():
            if key!='latest_run_cleanup':paths.append(pin['path'])
            if key.endswith('_source_manifest'):paths.append(str(Path(pin['path']).parent/'launch-proposal.json'))
        return {path:json.loads(Path(path).read_bytes()) for path in paths}
    def check_provenance(self,release=None,baseline=None,objects=None):
        release=self.release if release is None else release;baseline=self.baseline if baseline is None else baseline
        if objects is None:require_baseline17_provenance(release,baseline,self.accounting,self.old)
        else:
            with patch('resume_state.pinned_json',side_effect=lambda release,path:objects[path]):
                require_baseline17_provenance(release,baseline,self.accounting,self.old)
    def test_baseline17_provenance_and_baseline16_caveat_are_separate(self):
        self.check_provenance()
        self.assertEqual(self.release['baseline16_provenance'],self.old['baseline16_provenance'])
        self.assertEqual(self.release['baseline17_provenance']['latest_run_cleanup'],'UNKNOWN')
        self.assertNotIn('positive_original_wait',self.provenance_objects()[self.release['baseline17_provenance']['phase2_root_wait']['path']])
    def test_phase1_phase2_source_and_cursor_must_match(self):
        for field in ('source_evidence','journal'):
            objects=self.provenance_objects();objects[self.baseline['phase1_receipt']['path']][field]={}
            with self.subTest(field=field):
                with self.assertRaises(RuntimeError):self.check_provenance(objects=objects)
        changed=copy.deepcopy(self.baseline);changed['current_lineage_pins']={}
        with self.assertRaises(RuntimeError):self.check_provenance(baseline=changed)
    def test_new_native_copy_schema_hash_and_main_copy_are_exact(self):
        for key,value in (('sha256','0'*64),('schema_sha256','0'*64),('bytes',REDACTED_COORDINATE_AD2D9E5220),
                ('copied_only_immutable_query',False),('path',self.prior_baseline['native_reuse']['raw_copy'])):
            changed=copy.deepcopy(self.baseline);changed['native_copy'][key]=value
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):self.check_provenance(baseline=changed)
        changed=copy.deepcopy(self.baseline);changed['snapshot_sha256']='0'*64
        with self.assertRaises(RuntimeError):self.check_provenance(baseline=changed)
        changed=copy.deepcopy(self.release);del changed['source_pins'][self.baseline['native_copy']['path']]
        with self.assertRaises(RuntimeError):self.check_provenance(release=changed)
    def test_baseline17_cannot_accept_unrecorded_source_sidecars(self):
        for name in ('qualified-data/jobs.sqlite3-wal','qualified-data/native-owned-cells-bounded-v1/cells.sqlite3-wal'):
            changed=copy.deepcopy(self.baseline);changed['source_evidence'][name]={}
            objects=self.provenance_objects();phase=objects[changed['phase1_receipt']['path']]
            for key in ('before','after','source_evidence'):phase[key]=changed['source_evidence']
            changed['before']=changed['after']=changed['source_evidence']
            with self.subTest(name=name):
                with self.assertRaises(RuntimeError):self.check_provenance(baseline=changed,objects=objects)
    def test_collector_manifest_and_helper_entry_hashes_are_required(self):
        for number in (1,2):
            key='phase'+str(number)
            objects=self.provenance_objects();objects[self.release['baseline17_provenance'][key+'_root_wait']['path']]['manifest_sha256']='0'*64
            with self.assertRaises(RuntimeError):self.check_provenance(objects=objects)
            objects=self.provenance_objects();objects[self.release['baseline17_provenance'][key+'_root_wait']['path']]['returncode']=True
            with self.assertRaises(RuntimeError):self.check_provenance(objects=objects)
        changed=copy.deepcopy(self.baseline);changed['helper_source_check']['observed_sha256_at_entry']='0'*64
        with self.assertRaises(RuntimeError):self.check_provenance(baseline=changed)
    def test_lost_run25_wait_and_historical_source_caveat_cannot_be_promoted(self):
        changed=copy.deepcopy(self.release);changed['baseline17_provenance']['latest_run_cleanup']='positive'
        with self.assertRaises(RuntimeError):self.check_provenance(release=changed)
        changed=copy.deepcopy(self.release);changed['baseline16_provenance']['prelaunch_helper_sha_available']=True
        with self.assertRaises(RuntimeError):self.check_provenance(release=changed)
        objects=self.provenance_objects();objects[self.release['resume']['previous_launch']]['argv']=[]
        with self.assertRaises(RuntimeError):self.check_provenance(objects=objects)


class StoppedSourceSetControls(unittest.TestCase):
    def fixture(self,root):
        names=('qualified-data/instance.lock','qualified-data/jobs.sqlite3',
            'qualified-data/native-owned-cells-bounded-v1/cells.sqlite3',
            'qualified-journal/uploader.lock','qualified-journal/uploader.json','prior-segment-output-F/events.jsonl')
        evidence={}
        for name in names:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'stopped')
            st=path.stat();evidence[name]=dict(identity=[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns],
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            if '/native-owned-cells-' in name:evidence[name].update(mode=st.st_mode,uid=st.st_uid,nlink=st.st_nlink)
        return dict(source_evidence=evidence)
    def verify(self,root,baseline):
        verify_sources(root,baseline,lambda p:hashlib.sha256(p.read_bytes()).hexdigest(),lambda:None)
    def test_native_and_current_output_are_mandatory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);baseline=self.fixture(root);self.verify(root,baseline)
            for name in ('qualified-data/native-owned-cells-bounded-v1/cells.sqlite3','prior-segment-output-F/events.jsonl'):
                changed=copy.deepcopy(baseline);del changed['source_evidence'][name]
                with self.assertRaises(RuntimeError):self.verify(root,changed)
    def test_new_native_sidecar_and_output_evidence_refused(self):
        for name in ('qualified-data/native-owned-cells-bounded-v1/cells.sqlite3-wal',
                     'qualified-data/native-owned-cells-bounded-v1/unknown','prior-segment-output-F/added'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);baseline=self.fixture(root);(root/name).write_bytes(b'new')
                with self.subTest(name=name):
                    with self.assertRaises(RuntimeError):self.verify(root,baseline)
    def test_native_metadata_drift_and_symlink_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);baseline=self.fixture(root)
            name='qualified-data/native-owned-cells-bounded-v1/cells.sqlite3'
            baseline['source_evidence'][name]['nlink']+=1
            with self.assertRaises(RuntimeError):self.verify(root,baseline)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);baseline=self.fixture(root)
            (root/'qualified-journal/unrecorded-link').symlink_to(root/'qualified-data')
            with self.assertRaises(RuntimeError):self.verify(root,baseline)


