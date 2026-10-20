"""Single bounded recovery after committed input005; never creates a session."""
import json,os,time
from pathlib import Path
from passive import compare

def require(ok,reason):
    if not ok:raise RuntimeError(reason)

def validate(release):
    r=release['resume'];p=release['plan']
    for key in ('previous_release','previous_terminal','baseline_receipt','completed_manifest','original_launch','clock_evidence'):
        require(r[key] in release['source_pins'],'Missing conserved recovery pin: '+key)
    old=json.loads(Path(r['previous_release']).read_bytes())
    launch=json.loads(Path(r['original_launch']).read_bytes())
    require(r['term_deadline_unix']==launch['term_deadline_unix'] and r['kill_ceiling_unix']==launch['kill_ceiling_unix'],'Original absolute deadlines changed')
    require(r['completed_inputs']==5 and r['historical_positive_stop'] is False,'Exact recovery boundary/history')
    for key,value in old['plan'].items():
        if key!='session_id':require(p[key]==value,'Recovery plan changed: '+key)
    for key in ('run_root','database','inputs_manifest','monitor','runtime_manifest','qualified_smoke'):
        require(release[key]==old[key],'Recovery target/pin changed: '+key)
    for key,value in old['controls']['environment'].items():
        if key not in ('HOME','TMPDIR'):require(release['controls']['environment'].get(key)==value,'Recovery environment changed: '+key)
    require(set(release['controls']['environment'])==set(old['controls']['environment']),'Recovery environment keys changed')
    require(release['controls']['environment']['HOME']==release['output'] and release['controls']['environment']['TMPDIR']==str(Path(release['output'])/'tmp'),'Recovery environment paths')
    for key,value in old['controls'].items():
        if key!='environment':require(release['controls'][key]==value,'Recovery control changed: '+key)
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
    j=baseline['journal']
    require((j['inputs'],j['clean_inputs'],j['active_input'],j['frames'],j['published_frames'],j['finalized'])==(5,5,None,38400000,37632000,False),'Stopped committed005 boundary')
    require(baseline['completed']['completed']==195 and baseline['completed']['origin']==100003 and baseline['completed']['end']==37732003 and baseline['completed']['eos']==0,'Stopped completed baseline')
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
    require(actual==expected,'Stopped source file set changed')

def preserve(baseline,old_manifest,new_manifest,new_summary):
    count=compare(old_manifest,new_manifest)
    require(count==195 and new_summary['completed']>=195 and new_summary['origin']==100003 and new_summary['end']>=baseline['completed']['end'] and new_summary['eos']==0,'Recovery lost completed result/frontier')
    return count
