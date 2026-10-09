#!/usr/bin/env python3
"""Dependency-free transparent benchmark metrics. Does not infer gold labels.
Input predictions: {segments:[{start,end,speaker,text}],words:[{start,end,text}],events:[{start,end,event}]}.
DER uses exact interval sweep, global permutation, zero collar, overlap scored.
Lexical scores serialize segments by start (not overlap-permutation ASR scores).
"""
import argparse,itertools,json,unicodedata,pathlib
try:
 from rapidfuzz.distance import Levenshtein as _levenshtein
except ImportError:
 _levenshtein = None

def normalize(s):return ''.join(c for c in unicodedata.normalize('NFKC',s).lower() if not unicodedata.category(c).startswith(('P','S')))
def edit(a,b):
 if a==b:return 0
 if _levenshtein is not None:return _levenshtein.distance(a,b)
 # Strip shared edges without changing edit distance.
 lo=0
 while lo<min(len(a),len(b)) and a[lo]==b[lo]:lo+=1
 a=a[lo:];b=b[lo:]
 hi=0
 while hi<min(len(a),len(b)) and a[-hi-1]==b[-hi-1]:hi+=1
 if hi:a=a[:-hi];b=b[:-hi]
 prev=list(range(len(b)+1))
 for i,x in enumerate(a,1):
  cur=[i]
  for j,y in enumerate(b,1):cur.append(min(cur[-1]+1,prev[j]+1,prev[j-1]+(x!=y)))
  prev=cur
 return prev[-1]
def text(seq):return ' '.join(s.get('text','') for s in sorted(seq,key=lambda x:x['start']))
def lexical(ref,hyp):
 r=normalize(ref);h=normalize(hyp); rw=r.split();hw=h.split();rc=''.join(r.split());hc=''.join(h.split())
 word_errors=edit(rw,hw);character_errors=edit(rc,hc)
 return {'wer':word_errors/len(rw) if rw else None,'word_errors':word_errors,'reference_words':len(rw),'cer':character_errors/len(rc) if rc else None,'character_errors':character_errors,'reference_characters':len(rc)}
def diarization(ref,hyp,collar=0.):
 """Exact overlap-aware interval DER; unassigned speech can NEVER map to a person.

 Local neural labels may be permuted within a window. Across windows callers
 must supply resolved global IDs, or score each window independently.
 """
 unknown=lambda x:x.get('speaker') in (None,'','unknown','UNKNOWN') or str(x.get('speaker_status','')).startswith('unknown')
 ref=[dict(x,speaker=str(x['speaker'])) for x in ref]
 hyp=[dict(x,speaker=('__unassigned_'+str(i)) if unknown(x) else str(x['speaker']),_unassigned=unknown(x)) for i,x in enumerate(hyp)]
 rids=sorted({x['speaker'] for x in ref});hids=sorted({x['speaker'] for x in hyp if not x['_unassigned']})
 if len(rids)>8:raise ValueError('Subset-DP scorer supports at most8 reference speakers per recording/window.')
 from collections import defaultdict
 # Exact event sweep, rather than rescanning every turn at every boundary.
 # Counters preserve union semantics for overlapping intervals of the same ID.
 changes=defaultdict(lambda:[defaultdict(int),defaultdict(int),0])
 for index,records in enumerate((ref,hyp)):
  for x in records:
   changes[x['start']][index][x['speaker']]+=1
   changes[x['end']][index][x['speaker']]-=1
 if collar:
  for x in ref:
   for boundary in (x['start'],x['end']):
    changes[boundary-collar][2]+=1;changes[boundary+collar][2]-=1
 points=sorted(changes);active=[{},{}];masked=0
 spans=[];overlap={};unknown_seconds=0.;excluded=0.
 for a,b in zip(points,points[1:]):
  delta=changes[a];masked+=delta[2]
  for index in (0,1):
   for person,value in delta[index].items():
    count=active[index].get(person,0)+value
    if count:active[index][person]=count
    else:active[index].pop(person,None)
  if b<=a:continue
  if masked:excluded+=b-a;continue
  r=set(active[0]);h=set(active[1])
  spans.append((b-a,r,h));unknown_seconds+=(b-a)*sum(x.startswith('__unassigned_') for x in h)
  for x in r:
   for y in h:
    if not y.startswith('__unassigned_'):overlap[x,y]=overlap.get((x,y),0)+(b-a)
 # Assign each true person to at most one hypothesis identity. Extra splits stay
 # unmapped; unlike factorial permutations this handles many erroneous clusters.
 states={0:(0.,{})}
 for h in hids:
  following=dict(states)
  for mask,(value,mapping) in states.items():
   for i,r in enumerate(rids):
    if mask&(1<<i):continue
    new=mask|(1<<i);score=value+overlap.get((r,h),0)
    if new not in following or score>following[new][0]:following[new]=(score,mapping|{h:r})
  states=following
 mapping=max(states.values(),key=lambda x:x[0])[1]
 miss=fa=conf=den=0.
 for dur,r,h in spans:
  correct=len(r & {mapping.get(x,'__unmapped__'+x) for x in h});den+=dur*len(r)
  miss+=dur*max(0,len(r)-len(h));fa+=dur*max(0,len(h)-len(r));conf+=dur*(min(len(r),len(h))-correct)
 return {'der':(miss+fa+conf)/den if den else None,'miss_seconds':miss,'false_alarm_seconds':fa,'confusion_seconds':conf,'reference_speaker_seconds':den,'unassigned_speaker_seconds':unknown_seconds,'mapping':mapping,'collar_seconds':collar,'collar_excluded_wall_seconds':excluded,'overlap_scored':True,'unknown_ids_permuted':False,'reference_definition':'Caller-supplied reference speaker turns; never inferred from ASR text'}

def boundaries(ref,hyp,tolerance=.2):
 # one-to-one same-word candidates within tolerance; counts prevent duplicate rewards
 used=set();errors=[]
 for r in ref:
  if r['end']<=r['start']:continue
  match=[(abs(r['start']-h['start'])+abs(r['end']-h['end']),i,h) for i,h in enumerate(hyp) if i not in used and normalize(r.get('text',''))==normalize(h.get('text','')) and abs(r['start']-h['start'])<=tolerance and abs(r['end']-h['end'])<=tolerance]
  if match:
   _,i,h=min(match);used.add(i);errors.extend([abs(r['start']-h['start']),abs(r['end']-h['end'])])
 n=sum(r['end']>r['start'] for r in ref);return {'matched_words':len(used),'reference_timed_words':n,'hypothesis_words':len(hyp),'recall':len(used)/n if n else None,'precision':len(used)/len(hyp) if hyp else None,'matched_boundary_mae_seconds':sum(errors)/len(errors) if errors else None,'tolerance_seconds':tolerance}
def event_score(ref,hyp,iou=.3):
 candidates=[]
 for i,r in enumerate(ref):
  for j,h in enumerate(hyp):
   if r.get('event')!=h.get('event'):continue
   inter=max(0,min(r['end'],h['end'])-max(r['start'],h['start']));union=max(r['end'],h['end'])-min(r['start'],h['start'])
   if union>0 and inter/union>=iou:candidates.append((inter/union,i,j))
 ri=set();hi=set()
 for _,i,j in sorted(candidates,reverse=True):
  if i not in ri and j not in hi:ri.add(i);hi.add(j)
 p=len(hi)/len(hyp) if hyp else 0;r=len(ri)/len(ref) if ref else 0
 return {'precision':p,'recall':r,'f1':2*p*r/(p+r) if p+r else 0,'matched':len(ri),'reference_events':len(ref),'hypothesis_events':len(hyp),'iou_threshold':iou,'matching':'greedy descending IoU; same source event type'}
# Evaluation targets only. This does not change the runtime detector taxonomy.
ENVIRONMENTAL_EVENT_TARGETS = ('rain', 'driving', 'subway', 'door', 'music', 'silence')


def _event_coverage_intervals(coverage):
 """Only explicit, label-specific exhaustive intervals authorize negative scoring."""
 import math
 if coverage is None:
  return {}
 if not isinstance(coverage, dict) or set(coverage) != {'schema_version', 'exhaustive_intervals'} or type(coverage['schema_version']) is not int or coverage['schema_version'] != 1:
  raise ValueError('Expected event annotation coverage schema_version 1 and exhaustive_intervals')
 raw = coverage['exhaustive_intervals']
 if not isinstance(raw, dict) or any(label not in ENVIRONMENTAL_EVENT_TARGETS for label in raw):
  raise ValueError('Coverage labels must exactly match environmental evaluation targets')
 result = {}
 for label, intervals in raw.items():
  if not isinstance(intervals, list):
   raise ValueError('Exhaustive intervals must be a list')
  parsed = []
  for interval in intervals:
   if not isinstance(interval, dict) or set(interval) != {'start', 'end'}:
    raise ValueError('Coverage interval requires start/end only')
   a, b = interval['start'], interval['end']
   if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (a, b)) or not 0 <= a < b:
    raise ValueError('Coverage intervals must be finite nonnegative positive-duration supports')
   parsed.append((a, b))
  merged = []
  for a, b in sorted(parsed):
   if merged and a <= merged[-1][1]:
    merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
   else:
    merged.append((a, b))
  if merged:
   result[label] = merged
 return result


def environmental_event_score(ref, hyp, coverage=None, iou=.3):
 """Exact-label positive recall; precision only in explicitly exhaustive support.

 Non-exhaustive labels never authorize false-positive counting. Predictions that
 cross a coverage boundary are excluded from precision without clipping them.
 The unchanged event_score remains the historical raw diagnostic.
 """
 import math
 from collections import Counter
 if isinstance(iou, bool) or not isinstance(iou, (int, float)) or not math.isfinite(iou) or not 0 < iou <= 1:
  raise ValueError('IoU must be finite in (0, 1]')
 intervals = _event_coverage_intervals(coverage)
 def positive_duration(rows):
  kept = []
  for row in rows:
   if not isinstance(row, dict) or not isinstance(row.get('event'), str):
    raise ValueError('Event annotations require an exact event string')
   a, b = row.get('start'), row.get('end')
   if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (a, b)) or not 0 <= a <= b:
    raise ValueError('Event support must be finite, nonnegative and ordered')
   if b > a:
    kept.append(row)
  return kept
 ref, hyp = positive_duration(ref), positive_duration(hyp)
 reference_labels = Counter(x['event'] for x in ref)
 hypothesis_labels = Counter(x['event'] for x in hyp)
 covered = [label for label in ENVIRONMENTAL_EVENT_TARGETS if reference_labels[label] or label in intervals]
 unsupported = [label for label in ENVIRONMENTAL_EVENT_TARGETS if label not in covered]
 per_target = {}
 for label in covered:
  gold = [x for x in ref if x['event'] == label]
  predictions = [x for x in hyp if x['event'] == label]
  def within(x):
   return any(a <= x['start'] and x['end'] <= b for a, b in intervals.get(label, []))
  scoped = [x for x in predictions if within(x)]
  recall_matches = event_score(gold, predictions, iou)['matched']
  precision_matches = event_score(gold, scoped, iou)['matched']
  recall = recall_matches / len(gold) if gold else None
  precision = precision_matches / len(scoped) if scoped else None
  same_support = label in intervals and all(within(x) for x in gold + predictions)
  false_positives = len(scoped) - precision_matches if label in intervals else None
  f1 = None
  if same_support and precision is not None and recall is not None:
   f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.
  per_target[label] = {
   'reference_events': len(gold), 'hypothesis_events': len(predictions),
   'matched_reference_events': recall_matches, 'recall': recall,
   'precision': precision, 'f1': f1,
   'exhaustive_intervals': [{'start': a, 'end': b} for a, b in intervals.get(label, [])],
   'precision_scored_hypotheses': len(scoped), 'precision_matched_hypotheses': precision_matches,
   'hypotheses_outside_exhaustive_coverage': len(predictions) - len(scoped),
   'false_positive_events': false_positives,
   'precision_identifiable': bool(intervals.get(label)),
   'f1_support_aligned': same_support,
  }
 return {
  'schema_version': 1, 'available': bool(covered),
  'reason': None if covered else 'no_target_reference_coverage',
  'metric_scope': 'covered_target_subset',
  'target_labels': list(ENVIRONMENTAL_EVENT_TARGETS),
  'covered_targets': covered, 'unsupported_targets': unsupported,
  'all_targets_covered': not unsupported,
  # Deliberately no cross-class overall accuracy: missing targets/negative support
  # cannot be turned into a six-target percentage by an averaging convention.
  'precision': None, 'recall': None, 'f1': None,
  'per_target': per_target,
  'reference_label_counts': dict(sorted(reference_labels.items())),
  'hypothesis_label_counts': dict(sorted(hypothesis_labels.items())),
  'unsupported_target_hypotheses': {label: hypothesis_labels[label] for label in unsupported if hypothesis_labels[label]},
  'out_of_taxonomy_reference_events': sum(n for label, n in reference_labels.items() if label not in ENVIRONMENTAL_EVENT_TARGETS),
  'out_of_taxonomy_hypothesis_events': sum(n for label, n in hypothesis_labels.items() if label not in ENVIRONMENTAL_EVENT_TARGETS),
  'label_mapping': 'exact strings only; no inferred aliases, ancestors or source-context labels',
  'iou_threshold': iou,
  'matching': 'unchanged greedy descending IoU; same exact event type',
  'timing_scope': 'Original intervals; no clipping, shifts, collars or onset-accuracy claim',
 }


def score(g,p):
 out={'recording_id':g.get('recording_id'),'lexical_serialized':lexical(text(g['segments']),text(p.get('segments',[]))),'limitations':['Serialized lexical WER/CER is sensitive to overlapping turn order. It is not tcpWER/cpWER.','Native word timestamps exist only for AMI; AliMeeting TextGrid has turn-level timing.','Gold timing quality and unannotated sounds limit event/boundary claims.']}
 if 'speaker_turns' not in p:
  out['diarization']={'available':False,'der':None,'reason':'Independent speaker_turns were not supplied; ASR text spans are not a diarization substitute.'}
 elif any('speaker_id' not in t and 'speaker' not in t for t in p['speaker_turns']):
  out['diarization']={'available':False,'der':None,'reason':'Unresolved window-local labels require per-window scoring or global identity resolution.'}
 else:
  turns=[dict(t,speaker=t.get('speaker_id') if 'speaker_id' in t else t.get('speaker')) for t in p['speaker_turns']]
  out['diarization']=diarization(g['segments'],turns)
  out['diarization']['available']=True
  out['diarization']['reference_definition']='first-to-last lexical word of each AMI source turn' if g.get('dataset')=='AMI' else 'released reference speaker-turn intervals'
 if 'words' in g:out['word_boundaries']=boundaries(g['words'],p.get('words',[]))
 if 'events' in g:out['events']=event_score([e for e in g['events'] if e['end']>e['start']],p.get('events',[]))
 out['environmental_events']=environmental_event_score(g.get('events',[]),p.get('events',[]),g.get('event_annotation_coverage'))
 return out
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('gold');ap.add_argument('prediction');ap.add_argument('--out');a=ap.parse_args();result=score(json.loads(pathlib.Path(a.gold).read_text()),json.loads(pathlib.Path(a.prediction).read_text()));s=json.dumps(result,indent=2,ensure_ascii=False);print(s)
 if a.out:pathlib.Path(a.out).write_text(s)
