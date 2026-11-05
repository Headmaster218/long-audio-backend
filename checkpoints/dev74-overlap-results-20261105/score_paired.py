#!/usr/bin/env python3
"""Bounded excerpt diagnostics. No models, fabricated identity, or public raw text."""
import argparse, hashlib, importlib.util, itertools, json, re, sys, unicodedata
from pathlib import Path
BASE=Path(__file__).resolve().parent
ROOT=BASE.parent
OLD=ROOT/'backend-resume-20261104/scripts/benchmarks'
ARMS=('mixed_sensevoice','moss_td_q8','primary_mixture_content_unattributed')

def normalize(text):
 return ''.join(c for c in unicodedata.normalize('NFKC',text).lower() if not unicodedata.category(c).startswith(('P','S')))
def tokens(text,language):
 s=normalize(text)
 if language=='en':return s.split()
 if language=='zh':return list(''.join(s.split()))
 raise ValueError('Explicit en/zh language required')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def alignment(ref,hyp):
 """Minimum edit distance, deterministic ties: match, substitution, deletion, insertion."""
 directions=[bytearray([3]*(len(hyp)+1))];previous=list(range(len(hyp)+1))
 for i,x in enumerate(ref,1):
  current=[i];row=bytearray(len(hyp)+1);row[0]=2
  for j,y in enumerate(hyp,1):
   same=x==y
   cost,kind=min((previous[j-1]+(not same),0 if same else 1),(previous[j]+1,2),(current[j-1]+1,3))
   current.append(cost);row[j]=kind
  directions.append(row);previous=current
 i,j=len(ref),len(hyp);counts={'substitutions':0,'deletions':0,'insertions':0,'matches':0};matched=[]
 while i or j:
  kind=directions[i][j]
  if kind==0:counts['matches']+=1;matched.append(i-1);i-=1;j-=1
  elif kind==1:counts['substitutions']+=1;i-=1;j-=1
  elif kind==2:counts['deletions']+=1;i-=1
  else:counts['insertions']+=1;j-=1
 counts.update(errors=previous[-1],matched_reference_indices=list(reversed(matched)))
 assert counts['matches']+counts['substitutions']+counts['deletions']==len(ref)
 assert counts['matches']+counts['substitutions']+counts['insertions']==len(hyp)
 assert sum(counts[k]for k in ('substitutions','deletions','insertions'))==counts['errors']
 return counts

def legacy_score(gold,prediction):
 sys.path.insert(0,str(OLD))
 try:
  spec=importlib.util.spec_from_file_location('_legacy_overlap_score',OLD/'score_overlap_transcripts.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
  return mod.score({'language':gold['language'],'speakers':gold['speakers'],'whole_clip_lexical_scoring_eligible':True},{'transcripts':prediction,'whole_clip_per_speaker_scoring_eligible':True})
 finally:sys.path.pop(0)

def score_case(gold,prediction,crosscheck=True):
 if not isinstance(prediction,dict)or not isinstance(prediction.get('status'),str):raise ValueError('Invalid prediction status')
 if not isinstance(prediction.get('parse_errors',[]),list)or any(not isinstance(x,str)for x in prediction.get('parse_errors',[])):raise ValueError('Invalid parse errors')
 raw_hash=prediction.get('raw_output_sha256')
 if raw_hash is not None and(not isinstance(raw_hash,str)or re.fullmatch('[0-9a-f]{64}',raw_hash)is None):raise ValueError('Invalid raw output hash')
 if prediction.get('case_id')!=gold['case_id']:raise ValueError('Case identity mismatch')
 result={'case_id':gold['case_id'],'language':gold['language'],'class':gold['class'],'arm':prediction.get('arm'),'status':prediction.get('status')if prediction.get('status')in {'completed','timeout','failed','resource_limit','parse_failure','not_attempted','refused','cancelled','model_error','partial','scope_unscorable'}else'other_failure','completed':prediction.get('status')=='completed','identity_claim':False,'reference_status':gold.get('status'),'metric':'excerpt_permutation_invariant_WER'if gold['language']=='en'else'excerpt_permutation_invariant_CER'}
 if gold.get('status')!='ready':
  result.update(scoring_status='reference_unavailable',errors=None,reference_units=None,error_rate=None);return result
 if gold.get('whole_clip_lexical_scoring_eligible')is not True:raise ValueError('Incomplete boundary gold')
 hyps=prediction.get('transcripts',[])
 if not isinstance(hyps,list)or len(hyps)>1024:raise ValueError('Invalid/unbounded output tracks')
 if any(not isinstance(h,dict)or not isinstance(h.get('text'),str)or not isinstance(h.get('channel'),str)or(h.get('scope_id')is not None and not isinstance(h['scope_id'],str))for h in hyps):raise ValueError('Malformed track')
 if len({h['channel']for h in hyps})!=len(hyps):raise ValueError('Duplicate channel IDs; normalize scopes explicitly without dropping tracks')
 if prediction.get('arm')=='primary_mixture_content_unattributed'and prediction.get('status')=='completed':
  unit_texts=prediction.get('source_primary_unit_texts')
  if not isinstance(unit_texts,list)or any(not isinstance(t,str)for t in unit_texts)or len(hyps)!=1 or hyps[0]['text']!='\n'.join(unit_texts):raise ValueError('Unattributed mixture unit conservation failed')
  result['mixture_unit_conservation']='passed';result['source_primary_unit_count']=len(unit_texts)
 scopes={h.get('scope_id')for h in hyps if h.get('scope_id')is not None}
 if len(scopes)>1 or prediction.get('whole_clip_per_speaker_scoring_eligible')is False:
  result.update(scoring_status='independent_output_scope_unscorable',errors=None,reference_units=sum(len(tokens(r['text'],gold['language']))for r in gold['speakers']),error_rate=None,parse_error_count=len(prediction.get('parse_errors',[])))
  result['nonempty_hypothesis_tracks']=sum(bool(tokens(h['text'],gold['language']))for h in hyps)
  if gold['class']=='synthetic_zero':result['zero_control']={'generated_word_units':sum(len(tokens(h['text'],'en'))for h in hyps),'generated_character_units':sum(len(tokens(h['text'],'zh'))for h in hyps),'nonempty_tracks':result['nonempty_hypothesis_tracks'],'error_rate':None}
  return result
 refs=sorted(gold['speakers'],key=lambda s:s['id'])
 if len(refs)>2 or len({r['id']for r in refs})!=len(refs):raise ValueError('This frozen panel supports at most2 unique reference speakers')
 rt=[tokens(r['text'],gold['language'])for r in refs];ht=[tokens(h['text'],gold['language'])for h in hyps]
 if any(len(r)>500 for r in rt)or sum(map(len,ht))>8000:raise ValueError('Scorer unit budget exceeded; do not truncate to force a score')
 costs=[[alignment(r,h)for h in ht]for r in rt]
 # This panel has at most2 reference speakers: exact enumerated assignment is bounded.
 choices=range(-1,len(hyps));best=None;mapping=None;ties=0;total_hypothesis_units=sum(map(len,ht))
 for assignment in itertools.product(choices,repeat=len(refs)):
  used=[j for j in assignment if j>=0]
  if len(set(used))!=len(used):continue
  value=sum(costs[i][j]['errors']if j>=0 else len(rt[i])for i,j in enumerate(assignment))+total_hypothesis_units-sum(len(ht[j])for j in used)
  if best is None or value<best:best=value;mapping=assignment;ties=1
  elif value==best:ties+=1
 if best is None:raise AssertionError('No assignment')
 per=[];used=set(j for j in mapping if j>=0)
 for i,j in enumerate(mapping):
  a=costs[i][j]if j>=0 else alignment(rt[i],[])
  matched=[rt[i][k]for k in a['matched_reference_indices']];others={token for k,r in enumerate(rt)if k!=i for token in r};unique=set(rt[i])-others
  per.append({'reference_ordinal':i+1,'hypothesis_track_ordinal':j+1 if j>=0 else None,'reference_units':len(rt[i]),'hypothesis_units':len(ht[j])if j>=0 else 0,**{k:a[k]for k in ('substitutions','deletions','insertions','matches','errors')},'error_rate':a['errors']/len(rt[i])if rt[i]else None,'distinct_matched_unit_types':len(set(matched)),'matched_reference_exclusive_unit_occurrences':sum(t in unique for t in matched),'matched_reference_exclusive_unit_types':len(set(matched)&unique),'full_reference_sequence_exact_under_frozen_normalization':a['errors']==0,'missing_output_stream':j<0 or not ht[j]})
 extra=[{'hypothesis_track_ordinal':j+1,'insertion_units':len(ht[j])}for j in range(len(ht))if j not in used]
 den=sum(map(len,rt));minority=min(range(len(refs)),key=lambda i:(len(rt[i]),refs[i]['id']))if len(refs)==2 else None
 complete=result['completed'];loss={'errors':best,'reference_units':den,'error_rate':best/den if den else None}
 result.update(scoring_status='completed_output'if complete else'noncompleted_retained_output_diagnostic',**loss,per_reference_voice=per,extra_tracks=extra,minority_reference_ordinal=minority+1 if minority is not None else None,assignment_tied=ties>1,optimal_assignment_count=ties,assignment_tie_rule='Lexicographic reference ID, then lexicographic tuple of original track indices with missing=-1; first minimum.',edit_alignment_tie_rule='At each DP cell: exact match, substitution, deletion, insertion.',hypothesis_tracks=len(hyps),nonempty_hypothesis_tracks=sum(bool(h)for h in ht),empty_hypothesis_tracks=sum(not h for h in ht),missing_reference_streams=sum(v['missing_output_stream']for v in per),all_reference_streams_exact_under_frozen_normalization=bool(refs)and best==0,parse_error_count=len(prediction.get('parse_errors',[])),no_output_loss_sensitivity={'errors':den,'reference_units':den,'error_rate':1.0 if den else None}if not complete and not any(ht)else None)
 # Speaker assignment is an oracle. Exact strings are never a product identity/recognition boolean.
 result['limitations']=['Oracle whole-excerpt speaker assignment; no persistent identity or physical voice guarantee.','S/D/I and matches are conditioned on one deterministic optimum; tied assignments can change per-voice counts.','Distinct units are lexical types, not independent recovered facts; Chinese units are characters.','No waveform separation, reliable generated alignment, heldout or general accuracy claim.']
 if gold['class']=='synthetic_zero':
  result['zero_control']={'generated_word_units':sum(len(tokens(h['text'],'en'))for h in hyps),'generated_character_units':sum(len(tokens(h['text'],'zh'))for h in hyps),'nonempty_tracks':sum(bool(h)for h in ht),'error_rate':None}
 if gold['class']=='solo':result['solo_control']={'extra_tracks':len(extra),'extra_track_insertion_units':sum(x['insertion_units']for x in extra),'all_insertions':sum(x['insertions']for x in per)+sum(x['insertion_units']for x in extra)}
 if crosscheck:
  legacy=legacy_score(gold,hyps)
  if legacy['errors']!=best or legacy['reference_units']!=den:raise AssertionError('Independent legacy total scorer disagrees')
  result['legacy_aggregate_crosscheck']='passed'
 return result

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--predictions',required=True);ap.add_argument('--out',required=True);args=ap.parse_args()
 manifest=json.loads(Path(args.manifest).read_text());blob=json.loads(Path(args.predictions).read_text());predictions=blob['predictions']if isinstance(blob,dict)else blob
 index={}
 for p in predictions:
  key=(p['case_id'],p['arm'])
  if key in index:raise ValueError('Duplicate case/arm')
  if p.get('raw_output_path'):
   if not p.get('raw_output_sha256')or sha(p['raw_output_path'])!=p['raw_output_sha256']:raise ValueError('Raw output hash missing or changed')
  index[key]=p
 expected={(c['case_id'],arm)for c in manifest['cases']for arm in ARMS}
 if set(index)-expected:raise ValueError('Unexpected case/arm')
 rows=[]
 for c in manifest['cases']:
  goldpath=Path(c['gold_file']['path'])
  if sha(goldpath)!=c['gold_file']['sha256']:raise ValueError('Gold changed')
  gold=json.loads(goldpath.read_text())
  for arm in ARMS:
   pred=index.get((c['case_id'],arm),{'case_id':c['case_id'],'arm':arm,'status':'not_attempted','transcripts':[]})
   try:row=score_case(gold,pred)
   except ValueError:
    row={'case_id':gold['case_id'],'language':gold['language'],'class':gold['class'],'arm':arm,'status':'parse_failure','completed':False,'reference_status':gold.get('status'),'scoring_status':'prediction_format_unscorable','errors':None,'reference_units':sum(len(tokens(r['text'],gold['language']))for r in gold.get('speakers',[])),'error_rate':None,'parse_error_count':1,'identity_claim':False}
   row['gold_sha256']=c['gold_file']['sha256'];row['raw_output_sha256']=pred.get('raw_output_sha256')if isinstance(pred.get('raw_output_sha256'),str)and re.fullmatch('[0-9a-f]{64}',pred['raw_output_sha256'])else None;rows.append(row)
 pairs=[]
 comparisons=[('moss_vs_actual_primary','mixed_sensevoice'),('moss_vs_unattributed_mixture_sensitivity','primary_mixture_content_unattributed')]
 for c in manifest['cases']:
  byarm={r['arm']:r for r in rows if r['case_id']==c['case_id']}
  for comparison,baseline_arm in comparisons:
   a,b=byarm[baseline_arm],byarm['moss_td_q8']
   pairs.append({'comparison':comparison,'case_id':c['case_id'],'language':c['language'],'class':c['class'],'both_completed':a['completed']and b['completed'],'both_completed_and_scorable':a['completed']and b['completed']and a['errors']is not None and b['errors']is not None,'baseline_errors':a['errors'],'candidate_errors':b['errors'],'candidate_minus_baseline_errors':b['errors']-a['errors']if a['completed']and b['completed']and a['errors']is not None and b['errors']is not None else None,'retained_output_diagnostic_delta':b['errors']-a['errors']if a['errors']is not None and b['errors']is not None else None,'reference_units':a['reference_units']})
 totals=[]
 for language in ('en','zh'):
  for arm in ARMS:
   rr=[r for r in rows if r['language']==language and r['arm']==arm and r['class']=='overlap'];done=[r for r in rr if r['completed']and r['reference_status']=='ready'and r['errors']is not None]
   totals.append({'language':language,'arm':arm,'class':'overlap','planned_cases':len(rr),'reference_ready_cases':sum(r['reference_status']=='ready'for r in rr),'completed_cases':len(done),'completed_errors':sum(r['errors']for r in done),'completed_reference_units':sum(r['reference_units']for r in done),'completed_error_rate':sum(r['errors']for r in done)/sum(r['reference_units']for r in done)if sum(r['reference_units']for r in done)else None})
 paired_totals=[]
 for comparison,_ in comparisons:
  for language in ('en','zh'):
   pp=[p for p in pairs if p['comparison']==comparison and p['language']==language and p['class']=='overlap'and p['both_completed_and_scorable']]
   denominator=sum(p['reference_units']for p in pp)
   paired_totals.append({'comparison':comparison,'language':language,'class':'overlap','completed_pair_count':len(pp),'reference_units':denominator,'baseline_errors':sum(p['baseline_errors']for p in pp),'candidate_errors':sum(p['candidate_errors']for p in pp),'baseline_error_rate':sum(p['baseline_errors']for p in pp)/denominator if denominator else None,'candidate_error_rate':sum(p['candidate_errors']for p in pp)/denominator if denominator else None,'candidate_minus_baseline_errors':sum(p['candidate_minus_baseline_errors']for p in pp)})
 result={'schema':'overlap-paired-public-score-v1','private_input_manifest_sha256':sha(args.manifest),'private_predictions_sha256':sha(args.predictions),'scorer_sha256':sha(__file__),'protocol_amendment_sha256':sha(BASE/'PROTOCOL_AMENDMENT_01.json'),'rows':rows,'pairs':pairs,'per_arm_availability_and_overlap_totals':totals,'paired_language_overlap_totals':paired_totals,'public_text_policy':'No raw gold/hypothesis text, private paths, audio or physical speaker IDs. Hypothesis track ordinals preserve original array order; labels remain private.','planned_case_count':len(manifest['cases'])}
 Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'output':args.out,'sha256':sha(args.out),'rows':len(rows)},indent=2))
if __name__=='__main__':main()
