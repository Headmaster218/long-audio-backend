"""Independent NumPy reconstruction; does not import scorer or run inference."""
import hashlib,json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
def pin(x):
 p=Path(x['path']);assert p.stat().st_size==x['bytes'];assert hashlib.sha256(p.read_bytes()).hexdigest()==x['sha256']
def counts(out,truth,ids):
 c={'known_correct':0,'known_wrong':0,'known_refused':0,'unknown_accepted':0,'unknown_refused':0}
 for i in ids:
  t=truth[i];v=out[i]
  k=('unknown_accepted' if v else 'unknown_refused') if t['kind']=='unknown' else ('known_refused' if v is None else 'known_correct' if v==t['person_id'] else 'known_wrong')
  c[k]+=1
 return c
report={}
for split in ['C','T']:
 stage=read(R/(split+'_STAGE_RECEIPT.json'));assert stage['supervisor_exit_code']==0
 for k in ['gate','predictions']:pin(stage[k])
 pin(stage['result']['report'])
 gate=read(Path(stage['gate']['path']));assert gate['status']=='complete_native_run_gate_passed';pin(gate['manifest']);pin(gate['terminal'])
 term=read(Path(gate['terminal']['path']));assert term['stage']['positive_original_wait'] and term['stage']['owned_process_group_absent'];assert term['planned_calls']==22 and len(term['completed_ids'])==18 and not term['unrun_ids']
 for a in term['artifacts']:pin(a)
 pred=read(R/'inputs-01'/(split+'.predictor.json'));truth=read(R/'inputs-01'/(split+'.truth.json'));saved=read(R/(split+'.report.json'))
 with np.load(R/(split+'-run-01/native/vectors.npz'),allow_pickle=False) as v:
  assert v['valid'].all();vectors={i:x.astype(np.float64)/np.linalg.norm(x.astype(np.float64)) for i,x in zip(v['ids'],v['normalized'])}
 report[split]={}
 for arm,n in [('single',1),('multi3',3)]:
  centroids={g['person_id']:np.mean([vectors[i] for i in g['reference_ids'][:n]],axis=0) for g in pred['gallery']}
  centroids={i:v/np.linalg.norm(v) for i,v in centroids.items()}
  scores={q:sorted([(float(np.dot(vectors[q],v)),i) for i,v in centroids.items()],key=lambda x:(-x[0],x[1])) for q in pred['queries']}
  def decisions(t):return {q:s[0][1] if s[0][0]>=t and s[0][0]-s[1][0]>=.1 else None for q,s in scores.items()}
  threshold=saved['arms'][arm]['primary']['threshold']
  if split=='C':
   eligible=[]
   for t in [i/100 for i in range(101)]:
    c=counts(decisions(t),truth,pred['primary_queries'])
    if c['known_wrong']==0 and c['unknown_accepted']==0 and c['known_correct']>0:eligible.append((c['known_correct'],t))
   assert threshold==max(eligible)[1]
  else:assert threshold==report['C'][arm]['threshold']
  result={'threshold':threshold}
  for label,ids in [('primary',pred['primary_queries']),('all_queries_diagnostic',pred['queries'])]:
   c=counts(decisions(threshold),truth,ids)
   assert all(saved['arms'][arm][label]['counts'][k]==v for k,v in c.items())
   for row in saved['arms'][arm][label]['outcomes']:
    s=scores[row['query_id']];assert abs(row['score']-s[0][0])<1e-12 and abs(row['margin']-(s[0][0]-s[1][0]))<1e-12
   result[label]=c
  report[split][arm]=result
out=R/'INDEPENDENT_RESULT_AUDIT.json'
with out.open('x') as f:json.dump({'status':'PASS','method':'Independent NumPy normalization/centroids/dot products and C-grid selection, saved artifact hashes and positive process terminal checks; no scorer imports or inference.','results':report},f,indent=2)
print(json.dumps(report))
