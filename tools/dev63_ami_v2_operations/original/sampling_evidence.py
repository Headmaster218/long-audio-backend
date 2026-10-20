# SOURCE ARCHIVE ONLY: this sanitized file is not the executable tested bundle.
# ${WORKSPACE_ROOT} is an explicit deployment placeholder, not a literal path.
# Restore local configuration and regenerate all pins/releases before execution.
"""Bounded read-only sampling evidence. No product import, PCM or model access."""
import json,sqlite3,time
from pathlib import Path
RATE=16000
POLICY='fresh-window-bounded-v2'

def read_sampling(path,sid,capture):
    started=time.monotonic()
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.5)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.set_progress_handler(lambda:int(time.monotonic()-started>1),1000)
    try:
        db.execute('BEGIN')
        rows=db.execute('''SELECT s.*,h.session_id,h.recording_id,h.high_water,h.last_bucket,h.bootstrap_count
            FROM recording_history_sampling_v2 s JOIN recording_history_scopes_v1 h USING(scope_seq)
            JOIN recording_batches b ON b.id=h.recording_id
            WHERE h.session_id=? AND b.capture_id=? LIMIT 2''',(sid,capture)).fetchall()
        if len(rows)>1:raise ValueError('multiple production sampling scopes')
        if not rows:return {'at':started,'scope_present':False}
        s=dict(rows[0]);s['counters']=json.loads(s['counters']);s['policy']=s.pop('sampling_policy')
        if s['policy']!=POLICY:raise ValueError('configuration/admission failure: production scope is '+s['policy'])
        attempts=db.execute('''SELECT a.token,a.origin_kind,a.origin_id,a.invocation_id,a.state,a.basis,a.terminal,
            p.body FROM recording_history_attempts_v1 a LEFT JOIN recording_primary_packets_v1 p ON p.digest=a.origin_id
            WHERE a.scope_seq=? ORDER BY a.token LIMIT 65''',(s['scope_seq'],)).fetchall()
        if len(attempts)>64:raise ValueError('attempt capacity')
        out=[]
        for r in attempts:
            a=dict(r); body=a.pop('body')
            if body is None or len(body)>65536:raise ValueError('attempt packet absent or oversized')
            packet=json.loads(body);a['source_frame']=packet['commit']['part']['logical_start']
            for k in ('basis','terminal'):a[k]=json.loads(a[k]) if a[k] else None
            out.append(a)
        return {'at':started,'scope_present':True,**s,'durable_attempts':out}
    finally:db.close()

def assess_sampling(samples,final):
    errors=[]
    if not final.get('scope_present'):errors.append('production_scope_absent_configuration_or_admission_failure')
    if final.get('policy')!=POLICY:errors.append('production_scope_not_v2_configuration_or_admission_failure')
    attempts=final.get('durable_attempts',[])
    by_bucket={};frames=[];invocations=[]
    for a in attempts:
        frame=a['source_frame'];frames.append(frame);invocations.append(a['invocation_id'])
        if a['origin_kind']!='primary_commit':errors.append('nonprimary_attempt')
        if not 0<=frame<7680000:errors.append('attempt_outside_prefix')
        if a['state']=='reserved':errors.append('unresolved_attempt')
        if frame>=120*RATE:
            bucket=(frame-120*RATE)//(60*RATE);by_bucket[bucket]=by_bucket.get(bucket,0)+1
    if any(n>1 for n in by_bucket.values()):errors.append('more_than_one_durable_attempt_per_postbootstrap_bucket')
    if len(frames)!=len(set(frames)) or len(invocations)!=len(set(invocations)):errors.append('duplicate_source_or_invocation')
    bootstrap=sorted(f for f in frames if f<120*RATE)
    if len(bootstrap)>6 or any(b-a<10*RATE for a,b in zip(bootstrap,bootstrap[1:])):errors.append('bootstrap_cadence')
    observed=[s for s in [*samples,final] if s.get('scope_present')]
    if any(s['policy']!=POLICY or not 0<=s['examined_count']<=3 for s in observed):errors.append('sampled_policy_or_examination_bound')
    if not attempts:errors.append('no_real_primary_probe_admission')
    if final.get('counters',{}).get('admitted')!=len(attempts):errors.append('admission_counter_durable_attempt_mismatch')
    branch=any(a.get('examined_bucket',-1)>=0 and a.get('last_reason') in ('busy','insufficient_clean_support') and b.get('examined_bucket')==a['examined_bucket'] and b.get('examined_count',0)>a.get('examined_count',0) and b.get('last_frame',-1)>a.get('last_frame',-1) for i,a in enumerate(observed) for b in observed[i+1:])
    # The sidecar resets per bucket; periodic reads cannot prove unseen intermediate values.
    return {'operations_sampling_pass':not errors,'failures':sorted(set(errors)),
        'later_window_branch':'EXERCISED' if branch else 'NOT_EXERCISED',
        'branch_evidence':'retained same-bucket retryable skip followed by a later fresh examination' if branch else 'no retained sample proves multiple fresh examinations in a bucket',
        'durable_attempts_per_postbootstrap_bucket':by_bucket,'durable_attempt_count':len(attempts),
        'all_intermediate_checks_observed':False,'diagnostics_are_sampled':True,
        'attempt_interpretation':'durable admissions, including terminal failures; actual child invocation evidence is retained separately by the ownership collector',
        'independent_speaker_accuracy_claimed':False}
