"""Read-only geometry diagnostic; external private case/scorer paths only.
No character-level reference timing is inferred from utterance boundaries.
"""
import collections, hashlib, json, sys
from pathlib import Path

def overlap(a,b,c,d): return max(0.,min(b,d)-max(a,c))
def union(spans):
 out=[]
 for a,b in sorted(spans):
  if out and a<=out[-1][1]:out[-1][1]=max(b,out[-1][1])
  else:out.append([a,b])
 return out

def main(case,scorer):
 p=Path(case);sys.path.insert(0,scorer)
 from score_overlap_transcripts import tokens
 load=lambda f:json.loads((p/f).read_text())
 for f,h in load('raw-output-freeze.json').items():assert hashlib.sha256(Path(f).read_bytes()).hexdigest()==h
 refs=load('reference.json')['segments'];turns=load('native/native.json')['turns'];structured=load('asr/structured.json')['segments']
 raw=load('asr/raw.json')['transcription'];cropped=load('asr/cropped.json')['transcription']
 endpoints=sorted({v for x in refs for v in (x['start'],x['end'])});over=union([(a,b) for a,b in zip(endpoints,endpoints[1:]) if len({r['speaker'] for r in refs if r['start']<(a+b)/2<r['end']})>=2])
 result={'raw_crop_transcription_exact':raw==cropped,'asr_turn_batches':load('asr/cropped.json').get('asr_turn_batches'),'annotation_overlap_seconds':sum(b-a for a,b in over),'reference_granularity':'utterance only; no valid per-character overlap CER split','utterances':[],'unknown':[],'activity_intersection_seconds':{}}
 for i,r in enumerate(refs):
  ov=sum(overlap(r['start'],r['end'],a,b) for a,b in over);dur=r['end']-r['start']
  result['utterances'].append(dict(index=i,speaker=r['speaker'],start=r['start'],end=r['end'],characters=len(tokens(r['text_raw'],'zh')),overlap_seconds=ov,overlap_class='none' if ov<1e-9 else 'full' if abs(ov-dur)<1e-9 else 'partial'))
 for seg in structured:
  if seg['speaker_local_id'] is not None:continue
  counts=collections.Counter();pred=collections.Counter()
  for u in seg['_units']:
   n=len(tokens(u['text'],'zh'));t=u['ownership_anchor']
   counts[len({r['speaker'] for r in refs if r['start']<=t<r['end']})]+=n
   pred[len({r['speaker_local_id'] for r in turns if r['start']-.03<=t<=r['end']+.03})]+=n
  result['unknown'].append(dict(start=seg['start'],end=seg['end'],characters=len(tokens(seg['text'],'zh')),chars_by_reference_active_count=dict(counts),chars_by_predicted_active_count=dict(pred)))
 for sid in sorted({r['speaker'] for r in refs}):
  a=union([(r['start'],r['end']) for r in refs if r['speaker']==sid]);result['activity_intersection_seconds'][sid]={}
  for tid in sorted({t['speaker_local_id'] for t in turns}):
   b=union([(r['start'],r['end']) for r in turns if r['speaker_local_id']==tid]);result['activity_intersection_seconds'][sid][tid]=sum(overlap(x,y,z,w) for x,y in a for z,w in b)
  result['activity_intersection_seconds'][sid]['reference_duration']=sum(y-x for x,y in a)
 return result
if __name__=='__main__':print(json.dumps(main(*sys.argv[1:]),indent=2))
