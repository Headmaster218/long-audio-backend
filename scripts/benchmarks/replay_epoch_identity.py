#!/usr/bin/env python3
"""Read-only causal identity-continuity diagnostic; never updates profiles."""
import json,pathlib,collections,hashlib
from score_predictions import diarization
ROOT=pathlib.Path(__file__).resolve().parents[2]
SOURCE=ROOT/'reports/endurance_v02/candidate_native_v02/checkpoint/early-evaluation-20261008T194550Z'
POLICY={'seed':'accepted global ID and raw cosine >=0.55; first novel enrollment alone does not seed','reuse':'same chunk and epoch-prefixed local ID, later window only, unknown or unknown_insufficient_speech only','clear':'any fresh uncertain/ambiguous/quality rejection clears; contradictory accepted ID suspends slot through epoch','causal':True,'profile_updates':False,'selection':'declared before replay outcomes; one policy, no threshold search','boundary':'also reset at source recording boundary for this diagnostic'}
def replay(rows):
 out=[];bindings={};suspended=set();counts=collections.Counter();groups=collections.defaultdict(list)
 for row in rows:groups[(row['speaker_window_start'],row['chunk_id'],row['speaker_local_id'])].append(row)
 for (window,chunk,local),turns in sorted(groups.items(),key=lambda x:(x[0][0],str(x[0][2]))):
  key=(chunk,local);first=turns[0];pid=first.get('speaker_id');status=first.get('speaker_status');score=first.get('speaker_match_score');reuse=None
  valid= isinstance(local,str) and ':speaker_' in local
  if valid and key not in suspended:
   if pid:
    if key in bindings and bindings[key]!=pid:
     suspended.add(key);bindings.pop(key,None);counts['contradictory_accepted_slot']+=1
    elif score is not None and score>=.55:bindings[key]=pid;counts['verified_seed_windows']+=1
   elif status in ('unknown','unknown_insufficient_speech'):
    reuse=bindings.get(key)
   else:
    if key in bindings:counts['cleared_by_fresh_rejection']+=1
    bindings.pop(key,None)
  for row in turns:
   result=dict(row,speaker=row.get('speaker_id'))
   if reuse:
    result.update(speaker_id=reuse,speaker=reuse,speaker_status='tracking_continuity_unverified',continuity_added=True)
    counts['added_turns']+=1
   out.append(result)
 return out,dict(counts)
def added_breakdown(ref,rows,mapping):
 per=collections.defaultdict(lambda:collections.Counter());tot=collections.Counter()
 for row in rows:
  if not row.get('continuity_added'):continue
  cuts=sorted({row['start'],row['end']}|{max(row['start'],r['start']) for r in ref if r['end']>row['start'] and r['start']<row['end']}|{min(row['end'],r['end']) for r in ref if r['end']>row['start'] and r['start']<row['end']})
  mapped=mapping.get(row['speaker_id'])
  for a,b in zip(cuts,cuts[1:]):
   active={r['speaker'] for r in ref if r['start']<b and r['end']>a};kind='correct' if mapped in active else ('wrong_person' if active else 'nonspeech')
   tot[kind+'_seconds']+=b-a
   per[mapped or '__unmapped_global_id__'][kind+'_seconds']+=b-a
 return dict(tot),{p:dict(v) for p,v in per.items()}
def main():
 results=[]
 for p in sorted((SOURCE/'predictions').glob('*.json')):
  pred=json.loads(p.read_text());rid=pred['recording_id'];goldpath=ROOT/'benchmarks/alimeeting/gold'/f'{rid}.json'
  if not goldpath.exists():continue
  gold=json.loads(goldpath.read_text());ref=gold['segments'];rows=pred['speaker_turns'];base=diarization(ref,[dict(r,speaker=r.get('speaker_id')) for r in rows]);after,counts=replay(rows);score=diarization(ref,after);added,people=added_breakdown(ref,after,base['mapping'])
  results.append({'recording_id':rid,'prediction_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'before':base,'after':score,'counts':counts,'added_assignment_fixed_baseline_mapping':added,'per_assigned_reference_person_added':people})
 output={'policy':POLICY,'source':str(SOURCE.relative_to(ROOT)),'reference_mapping_caveat':'Mapping estimated once from complete baseline recording for evaluation only; replay never sees gold. Added wrong seconds include native slot contamination and baseline global identity errors.','results':results,'sparse_case':'TS3003a native raw-stage output has no per-core global registry decisions; cannot evaluate this policy from that artifact.'}
 dest=ROOT/'reports/epoch-identity-continuity-replay.json';dest.write_text(json.dumps(output,indent=2));print(json.dumps({'report':str(dest),'results':[{'id':r['recording_id'],'before':r['before']['der'],'after':r['after']['der'],'counts':r['counts'],'added':r['added_assignment_fixed_baseline_mapping']} for r in results]},indent=2))
if __name__=='__main__':main()
