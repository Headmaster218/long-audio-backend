"""Bounded no-retry continuation at the qualified partial input; never creates a session."""
import json,os,time
from pathlib import Path
from passive import compare
from product_transition import mapped

def require(ok,reason):
    if not ok:raise RuntimeError(reason)

def validate(release):
    r=release['resume'];p=release['plan']
    for key in ('previous_release','previous_terminal','baseline_receipt','completed_manifest','original_launch','clock_evidence','recovered_history'):
        require(r[key] in release['source_pins'],'Missing conserved recovery pin: '+key)
    require(release.get('measurement_protocol')=='visible-pid-namespace-rss-v1','Revised measurement protocol must be explicit')
    history=json.loads(Path(r['recovered_history']).read_bytes())
    require(history['status']=='historical_lower_bounds_not_terminal' and history['interrupted_owner_cleanup']=='UNKNOWN' and history['original_wait_available'] is False and history['historical_request_accounting']=='UNKNOWN','Lost executor history cannot imply completion')
    require(history['observer_requests_lower_bound']>=405 and history['decoded_bytes_lower_bound']>=3025468,'Historical lowerbounds regressed')
    require(r['historical_observation_lower_bounds']==dict(requests=history['observer_requests_lower_bound'],decoded_bytes=history['decoded_bytes_lower_bound']),'Historical lowerbounds must match pinned evidence')
    require(history['observer_budget_reset'] is False and history['observer_requests']==100 and history['decoded_bytes']==739409,'Known recovered observation allowance use must carry forward')
    for path,sha in history['source_pins'].items():
        require(release['source_pins'].get(path)==sha,'Historical evidence missing or changed')
    old=json.loads(Path(r['previous_release']).read_bytes())
    launch=json.loads(Path(r['original_launch']).read_bytes())
    require(r['term_deadline_unix']==launch['term_deadline_unix'] and r['kill_ceiling_unix']==launch['kill_ceiling_unix'],'Original absolute deadlines changed')
    require(r['completed_inputs']==9 and r['resume_input_id']=='es2011a-repeat-010' and r['historical_positive_stop'] is False,'Exact recovery boundary/history')
    require(release['runtime_manifest']==old['runtime_manifest'],'Product runtime unchanged')
    old_installed=new_installed=old['controls']['environment']['PYTHONPATH']
    for key,value in old['plan'].items():
        if key!='session_id':require(p[key]==value,'Recovery plan changed: '+key)
    for key in ('run_root','database','inputs_manifest','monitor','qualified_smoke'):
        require(release[key]==old[key],'Recovery target/pin changed: '+key)
    for key,value in old['controls']['environment'].items():
        if key not in ('HOME','TMPDIR'):require(release['controls']['environment'].get(key)==mapped(value,old_installed,new_installed),'Recovery environment changed: '+key)
    require(set(release['controls']['environment'])==set(old['controls']['environment']),'Recovery environment keys changed')
    require(release['controls']['environment']['HOME']==release['output'] and release['controls']['environment']['TMPDIR']==str(Path(release['output'])/'tmp'),'Recovery environment paths')
    for key,value in old['controls'].items():
        if key!='environment':require(release['controls'][key]==('0.2.0rc3.dev65' if key=='package_version' else mapped(value,old_installed,new_installed)),'Recovery control changed: '+key)
    require(p['session_id']==r['session_id'] and p['capture_id']==r['capture_id'],'Recovery identity mismatch')
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==release['controls']['boot_id'],'Recovery boot changed')
    require(0<=r['started_upper']-r['started_lower']<=2.047,'Clock reconstruction interval')
    clock=json.loads(Path(r['clock_evidence']).read_bytes())
    for key in ('started_lower','started_upper','schedule_offset','previous_status_rounds','last_sample_at'):
        require(r[key]==clock[key],'Clock/counter evidence mismatch: '+key)
    require(abs(r['schedule_offset']-(484.4948807070032-480))<=1e-12,'Original schedule offset')
    require(0<r['outer_timeout_seconds']<=86280,'Recovery outer duration')
    require(time.time()+r['outer_timeout_seconds']<=r['term_deadline_unix'],'Recovery would extend TERM')
    require(r['kill_ceiling_unix']==r['term_deadline_unix']+120,'Original kill reserve')
    baseline=json.loads(Path(r['baseline_receipt']).read_bytes())
    require(baseline['status']=='complete' and baseline['before']==baseline['after'] and baseline['changed']==[],'Stopped baseline conservation')
    require(baseline['exclusive_locks_acquired']==2 and baseline['historical_positive_stop'] is False,'Lost executor history must remain unknown')
    require(r['observation_allowance']==dict(historical_accounting='UNKNOWN',segment_get_cap=8192,segment_decoded_cap=268435456,individual_body_cap=524288,individual_seconds=5),'Explicit bounded recovered observation allowance')
    require(baseline['latest_original_wait_available'] is True and baseline['latest_original_wait']['positive_original_wait'] is True and baseline['latest_owner_cleanup']=='positive' and baseline['latest_stop']['positive'] is True and not baseline['latest_stop']['survivors'],'Latest positive cleanup proof')
    j=baseline['journal']
    require_partial(j)
    require(r['retry_post_allowed'] is False,'Retry POST must remain prohibited')
    require(baseline['recovered']['chunk']['id']==FAILED_CHUNK and baseline['recovered']['chunk']['status']=='completed' and baseline['recovered']['chunk']['next_offset']==10.5 and baseline['recovered']['failed_count']==0,'Completed former failure proof')
    require(baseline['completed']['completed']==400 and baseline['completed']['result_rows']==2412 and baseline['completed']['origin']==100003 and baseline['completed']['end']==70660003 and baseline['completed']['eos']==0,'Stopped completed baseline')
    return baseline

def verify_sources(root,baseline,digest,check):
    root=Path(root)
    for relative,wanted in baseline['source_evidence'].items():
        check();p=root/relative
        require(p.is_relative_to(root) and not p.is_symlink(),'Source confinement')
        before=p.stat();identity=[before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns]
        require(identity==wanted['identity'] and digest(p)==wanted['sha256'],'Stopped source changed before recovery')
        after=p.stat();require(identity==[after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns],'Source changed while verifying')
    expected={str(root/k) for k in baseline['source_evidence']}
    actual={str(p) for p in (root/'journal-retry02').rglob('*') if p.is_file()}
    actual.update(str(p) for p in (root/'data-retry02').glob('jobs.sqlite3*') if p.is_file())
    actual.add(str(root/'data-retry02/instance.lock'))
    actual.update(str(p) for p in (root/'output-resume11').rglob('*') if p.is_file())
    require(actual==expected,'Stopped source file set changed')

def preserve(baseline,old_manifest,new_manifest,new_summary):
    count=compare(old_manifest,new_manifest)
    require(count==400 and new_summary['completed']>=400 and new_summary['origin']==100003 and new_summary['end']>=baseline['completed']['end'] and new_summary['eos']==0,'Recovery lost completed result/frontier')
    return count


FAILED_CHUNK='historical-completed-chunk'

def require_partial(j):
    require((j['inputs'],j['clean_inputs'],j['frames'],j['published_frames'],j['finalized'])==(10,9,75264000,70560000,False),'Stopped partial010 boundary')
    a=j.get('active_input')
    require(isinstance(a,dict) and (a.get('id'),a.get('normalized_start'),a.get('frames'),a.get('clean_eof'))==('es2011a-repeat-010',69120000,6144000,False),'Active010 cursor changed')
    return j['published_frames']

