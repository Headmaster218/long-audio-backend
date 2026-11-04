"""Independent saved-output audit. No encoder/scorer imports, model calls, or T fitting."""
import hashlib,json,math
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parent; I=R/'inputs-resource-reduced-v1'
def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
verified={}
def verify(pin):
 p=Path(pin['path']); b=p.read_bytes(); assert len(b)==pin['bytes'] and hashlib.sha256(b).hexdigest()==pin['sha256'],str(p);verified[str(p)]=pin['sha256'];return p
def walk(v):
 if isinstance(v,dict):
  if set(v)=={'bytes','path','sha256'}:verify(v)
  else:
   for x in v.values():walk(x)
 elif isinstance(v,list):
  for x in v:walk(x)
def normal(v):
 v=[float(x) for x in v];m=max(map(abs,v));assert m>1e-30 and all(map(math.isfinite,v));a=[x/m for x in v];n=math.sqrt(math.fsum(x*x for x in a));return [x/n for x in a]
def cosine(ref,q):return max(-1.,min(1.,math.fsum(a*b for a,b in zip(normal(normal(ref)),normal(q)))))
def summarize(scores,truth,threshold):
 counts=dict.fromkeys(['planned','known','unknown','unresolved_truth','known_correct','known_wrong','known_refused','known_unavailable','unknown_accepted','unknown_refused','unknown_unavailable','unresolved_emitted','unresolved_unavailable','unresolved_no_output'],0);out=[]
 for q,ss in scores.items():
  rank=sorted(ss,key=lambda x:(-x['score'],x['person_id']));best,second=rank[:2];margin=best['score']-second['score'];reason='unknown_below_threshold' if best['score']<threshold else 'unknown_ambiguous' if margin<.1 else 'experimental_identity_match';emitted=best['person_id'] if reason=='experimental_identity_match' else None;t=truth[q];kind=t['kind'];counts['planned']+=1;counts[kind]+=1
  label=('known_correct' if emitted==t['person_id'] else 'known_wrong' if emitted is not None else 'known_refused') if kind=='known' else ('unknown_accepted' if emitted is not None else 'unknown_refused');counts[label]+=1
  out.append(dict(query_id=q,emitted=emitted,winner=best['person_id'],score=best['score'],margin=margin,available=True,reason=reason,outcome=label))
 return dict(counts=counts,outcomes=out)
def cdf(x,n,p):return math.fsum(math.comb(n,k)*p**k*(1-p)**(n-k) for k in range(x+1))
def cp(x,n,sides):
 alpha=.05/(2 if sides=='two-sided' else 1)
 if x==n:upper=1.
 else:
  lo,hi=0.,1.
  for _ in range(100):
   mid=(lo+hi)/2
   if cdf(x,n,mid)>alpha:lo=mid
   else:hi=mid
  upper=(lo+hi)/2
 if sides!='two-sided' or x==0:lower=0.
 else:
  lo,hi=0.,1.
  for _ in range(100):
   mid=(lo+hi)/2
   tail=math.fsum(math.comb(n,k)*mid**k*(1-mid)**(n-k) for k in range(x,n+1))
   if tail<alpha:lo=mid
   else:hi=mid
  lower=(lo+hi)/2
 return dict(event_count=x,trials=n,confidence=.95,sides=sides,lower=lower,upper=upper)
protocol=read(I/'protocol.json'); lock=read(R/'CANDIDATE_LOCK.json');selection=read(R/'C_SELECTION.json');report=read(R/'T_REPORT.json');seal=read(R/'T_PREDICTION_SEAL.json')
expected_shas={'FINAL_INPUT_FREEZE.json':'b6b4902712c3e1bfa349c6c93de613c8f7c50cc0251135777ed533eb278c4b3d','CANDIDATE_LOCK.json':'b85c7290b413f02d4dcd0428581b5620d7177d92b0a163b191d2ce09194bee7d','C_SELECTION.json':'8cb529857f198bcc9307bb3e82f7163e6245fcad414e7a7be11e288c3cfb9698','T_PREDICTION_SEAL.json':'856d7afd2475a0a7d39b73d6eeff5d50a65cb01156bede85cad56522f8c4ca8f','T_REPORT.json':'701d626341f26076efeff4b6d17e2033d8a46a2876e8fcdc45cdf52be660f65b','C-run-01/TERMINAL.json':'4523f42179307063e9b9d0f4c6a3860de0f710dbf0e9bd0ef47dd77430d3b1dd','T-run-01/TERMINAL.json':'edafc7313f2cbe4d65ca52aae3061509c5f532c5403c152277f1fdc89649a530'}
for name,digest in expected_shas.items():assert sha(R/name)==digest
for doc in [protocol,lock,report,seal,read(R/'FINAL_INPUT_FREEZE.json')]:walk(doc)
assert lock['threshold']==report['threshold']==selection['threshold']==.46
assert report['formal_population_or_safety_ci'] is None
vectors={};terminals={};raw_repeats=0;norm_repeats=0;stage_results={}
for split in ('C','T'):
 manifest=read(I/(split+'_MANIFEST.json'));walk(manifest);terminal=read(R/(split+'-run-01/TERMINAL.json'));walk(terminal);result=read(R/(split+'-run-01/native/result.json'));walk(result);terminals[split]=terminal
 ids=[s['id'] for s in manifest['samples']];assert len(ids)==31 and terminal['planned_ids']==terminal['completed_ids']==ids and terminal['unrun_ids']==[] and terminal['planned_calls']==35
 assert terminal['status']=='completed_bounded_native_stage' and terminal['manifest_sha256']==sha(I/(split+'_MANIFEST.json')) and terminal['protocol_sha256']==sha(I/'protocol.json')
 st=terminal['stage'];assert st['returncode']==0 and st['positive_original_wait'] is True and st['owned_process_group_absent'] is True and st['termination_needed'] is False and st['cleanup_error'] is None and st['stop_reason'] is None
 assert result['calls_attempted']==result['calls_completed']==35 and result['sessions_attempted']==1 and result['status']=='passed' and result['terminal_integrity_passed'] is True
 assert result['loaded_ort_paths']==[manifest['runtime_contract']['library']['path']]
 for field,cap in [('elapsed_seconds',900),('aggregate_cpu_seconds',600),('allocated_output_bytes',33554432)]:assert 0<=terminal[field]<=cap
 assert 0<st['peak_sampled_owned_rss']<=3221225472
 journal=[json.loads(line) for line in (R/(split+'-run-01/native/journal.jsonl')).read_text().splitlines()]
 call_ids=[(q,0) for q in ids]+[(q,j) for q in manifest['repeat_ids'] for j in (1,2)]
 assert [r['event'] for r in journal]==['model_construction_attempt','model_construction_complete']+[x for _ in call_ids for x in ('encode_attempt','encode_complete')]+['terminal']
 assert journal[-1]['status']=='passed'
 with np.load(R/(split+'-run-01/native/vectors.npz'),allow_pickle=False) as z:
  assert set(z.files)=={'ids','raw','normalized','valid','repeat_raw','repeat_normalized','repeat_ids'} and z['ids'].tolist()==ids and z['valid'].dtype==np.bool_ and z['valid'].shape==(31,) and z['valid'].all()
  assert z['repeat_ids'].tolist()==[q for q in manifest['repeat_ids'] for _ in (1,2)]
  for name,count in [('raw',31),('normalized',31),('repeat_raw',4),('repeat_normalized',4)]:assert z[name].dtype==np.float32 and z[name].shape==(count,512) and np.isfinite(z[name]).all()
  for index,(q,rep) in enumerate(call_ids):
   attempt,done=journal[2*index+2:2*index+4];assert (attempt['id'],attempt['repeat'],attempt['call'])==(q,rep,index+1)==(done['id'],done['repeat'],done['call'])
   base=ids.index(q);sample=manifest['samples'][base];assert attempt['float32_sha256']==sample['float32_sha256']
   raw=z['raw'][index] if index<31 else z['repeat_raw'][index-31];norm=z['normalized'][index] if index<31 else z['repeat_normalized'][index-31]
   assert (raw/np.linalg.norm(raw)).astype(np.float32).tobytes()==norm.tobytes()
   assert hashlib.sha256(raw.tobytes()).hexdigest()==done['raw_sha256'] and hashlib.sha256(norm.tobytes()).hexdigest()==done['normalized_sha256']
   if index<31:
    clip=result['clips'][index];assert clip['id']==q and clip['status']=='encoded' and clip['wav_sha256']==sample['wav']['sha256'] and clip['pcm_sha256']==sample['pcm_sha256'] and clip['float32_sha256']==sample['float32_sha256'] and clip['raw_sha256']==done['raw_sha256'] and clip['normalized_sha256']==done['normalized_sha256']
   else:
    assert raw.tobytes()==z['raw'][base].tobytes();assert norm.tobytes()==z['normalized'][base].tobytes();raw_repeats+=1;norm_repeats+=1
  vectors[split]={q:v.tolist() for q,v in zip(ids,z['normalized'])}
 stage_results[split]=dict(calls=35,sessions=1,wall_seconds=terminal['elapsed_seconds'],aggregate_cpu_seconds=terminal['aggregate_cpu_seconds'],allocated_output_bytes=terminal['allocated_output_bytes'],peak_sampled_owned_rss=st['peak_sampled_owned_rss'],positive_original_wait=True,exit_code=0,owned_group_absent=True)
all_summaries={};C_primary_scores=None;C_primary_truth=None
for split in ('C','T'):
 full=read(I/(split+'_all_predictor.json'));primary=read(I/(split+'_primary_predictor.json'));truth=read(I/(split+'_all_truth.json'))
 score={q:[dict(person_id=g['person_id'],score=cosine(vectors[split][g['sample_id']],vectors[split][q])) for g in full['gallery']] for q in full['queries']}
 sets={'primary':primary['queries'],'all_three_queries':full['queries'],'two_extra_queries_only':[q for q in full['queries'] if q not in primary['queries']]}
 all_summaries[split]={}
 for kind,queries in sets.items():
  partial={q:score[q] for q in queries};all_summaries[split][kind]={}
  for name,t in [('candidate',.46),('baseline_0_75',.75)]:
   actual=summarize(partial,truth,t);expected=(selection if split=='C' else report)['descriptive_reports'][kind][name]
   assert actual['counts']==expected['counts'] and actual['outcomes']==expected['outcomes'],(split,kind,name)
   c=actual['counts'];assert expected['rates']['known_correct']==c['known_correct']/c['known'] and expected['rates']['unknown_accepted']==c['unknown_accepted']/c['unknown']
   emissions=c['known_correct']+c['known_wrong']+c['unknown_accepted'];assert expected['rates']['verified_output_precision']==(c['known_correct']/emissions if emissions else None)
   all_summaries[split][kind][name]=c
 if split=='C':C_primary_scores={q:score[q] for q in primary['queries']};C_primary_truth={q:truth[q] for q in primary['queries']}
 else:
  for kind,queries in [('primary',primary['queries']),('all_three_queries',full['queries'])]:
   assert [row['query_id'] for row in seal[kind]['rows']]==queries
   for row in seal[kind]['rows']:assert row['scores']==score[row['query_id']] and row['unavailable_reason'] is None
   for t in (.46,.75):
    actual=summarize({q:score[q] for q in queries},truth,t)['outcomes'];expected=seal['fixed_decisions'][kind][str(t)]
    assert [{k:v for k,v in r.items() if k!='outcome'} for r in actual]==expected
# Only validate the predeclared C grid. Never scan T thresholds.
grid=[dict(threshold=i/100,counts=summarize(C_primary_scores,C_primary_truth,i/100)['counts']) for i in range(101)]
assert grid==selection['grid_results'];feasible=[r for r in grid if r['counts']['known_wrong']==r['counts']['unknown_accepted']==0 and r['counts']['known_correct']>0];chosen=max(feasible,key=lambda r:(r['counts']['known_correct'],r['threshold']));assert chosen['threshold']==.46
interval_checks=0
for working,events in report['conditional_iid_model']['working_points'].items():
 c=all_summaries['T']['primary'][working]
 for event,intervals in events.items():
  n=c['known' if event.startswith('known_') else 'unknown']
  for sides,interval in intervals.items():
   expected=cp(c[event],n,sides);assert interval['event_count']==expected['event_count'] and interval['trials']==expected['trials']
   assert abs(interval['lower']-expected['lower'])<2e-14 and abs(interval['upper']-expected['upper'])<2e-14;interval_checks+=1
supplement={working:{event:{s:cp(c[event],c['known' if event.startswith('known_') else 'unknown'],s) for s in ('upper','two-sided')} for event in ('known_refused','unknown_refused')} for working,c in all_summaries['T']['primary'].items()}
# Immutable source path plus prior release audit and launch receipts provide sequencing evidence, not global blinding.
cl=read(R/'C_LAUNCH_RECEIPT.json');tl=read(R/'T_LAUNCH_RECEIPT.json');assert tl['lock_sha256']==sha(R/'CANDIDATE_LOCK.json') and tl['release_audit_sha256']==sha(R/'INDEPENDENT_T_RELEASE_AUDIT.json')
assert cl['utc']<tl['utc'] and (R/'CANDIDATE_LOCK.json').stat().st_mtime_ns<(R/'T-run-01/TERMINAL.json').stat().st_mtime_ns<(R/'T_PREDICTION_SEAL.json').stat().st_mtime_ns<(R/'T_REPORT.json').stat().st_mtime_ns
assert report['prediction_seal']['sha256']==sha(R/'T_PREDICTION_SEAL.json') and seal['candidate_lock']['sha256']==sha(R/'CANDIDATE_LOCK.json')
old=R.parent/'backend-frontend-comparison-20261104'
for name,digest in [('frontend_experiment.py','4c324be6150b02d513cbbd867acfd1910e3126586ab719e7ec8e5a277e4a7ed7'),('EXPERIMENT_PLAN.md','d434c4213fc50f6417ca6cabd379d862962656dd8376150a8f1f1c77f9367737'),('EXPERIMENT_FREEZE.json','fbe31b4d31c8bb43af745618f93bda75ccda9e6e11e7de6254471e3a23b5bc3f')]:assert sha(old/name)==digest
result=dict(status='PASSED_READ_ONLY_RESULT_AUDIT_NO_PRODUCTION_QUALIFICATION',input_and_output_pins_rehashed=len(verified),fixed_artifact_sha256=expected_shas,model_calls_by_auditor=0,total_actual_encoder_calls=70,total_model_sessions=2,raw_and_normalized_repeat_vectors_bitwise_equal=8,stages=stage_results,totals={f:sum(x[f] for x in stage_results.values()) for f in ['wall_seconds','aggregate_cpu_seconds','allocated_output_bytes']},C_grid_recomputed_101_exact_rows=True,C_unique_rule_winner=.46,T_only_fixed_thresholds=[.46,.75],full_outcome_and_denominator_tables_recomputed=all_summaries,conditional_iid_interval_objects_verified=interval_checks,formal_population_or_safety_CI=None,supplementary_refusal_reference_intervals=supplement,reference_interval_assumption='Hypothetical iid Bernoulli primary-query outcomes conditional on fixed gallery/source domain. Source does not establish iid or population sampling; no safety/population guarantee or multiplicity adjustment.',sequencing='Pinned code exclusively writes prediction seal before formal T truth read; lock and release audit predate T launch, report binds exact seal, filesystem order corroborates. Not a global access/blinding or tamper-proof attestation.',critical_limit='T primary3/4 correct vs baseline0/4; extra-only1/8, all-three4/12. Zero known-wrong and0/5 primaryunknown accepts do not establish rare-error safety. No threshold change or production authorization.',original32_acquisition='BLOCKED; separate prospective resource reduction to18 people, original roles preserved; archive-prefix availability bias disclosed.')
path=R/'INDEPENDENT_PILOT_RESULT_AUDIT.json';path.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'audit_sha256':sha(path),'totals':result['totals'],'T_counts':all_summaries['T'],'C_counts':all_summaries['C'],'reference_refusal_candidate':supplement['candidate']}))
