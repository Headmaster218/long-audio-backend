"""Boundary-sensitive job-oracle edit diagnostic, NOT complete-excerpt cpWER.
No models, audio, runtime or source-app import. Explicit midpoint ownership is
required by caller; unknown remains a separately reported optimistic bucket.
"""
import unicodedata

def tokens(s):
 return ''.join(c for c in unicodedata.normalize('NFKC',s).lower() if not unicodedata.category(c).startswith(('P','S'))).split()

def edits(a,b):
 # Fixed tie order: total errors, substitutions, deletions, insertions.
 prev=[(j,0,0,j) for j in range(len(b)+1)]
 for i,x in enumerate(a,1):
  cur=[(i,0,i,0)]
  for j,y in enumerate(b,1):
   z=prev[j-1];sub=int(x!=y);diagonal=(z[0]+sub,z[1]+sub,z[2],z[3])
   z=prev[j];deletion=(z[0]+1,z[1],z[2]+1,z[3]);z=cur[-1];insertion=(z[0]+1,z[1],z[2],z[3]+1)
   cur.append(min(diagonal,deletion,insertion))
  prev=cur
 return prev[-1]

def score(refs,hyps):
 # refs/hyps map job-local labels to complete text strings. No cross-job linking.
 if len(refs)>8 or len(hyps)>64:raise ValueError('bounded metric capacity')
 rt={k:tokens(v) for k,v in refs.items()};ht={k:tokens(v) for k,v in hyps.items()};rids=sorted(rt);hids=sorted(ht)
 baseline=sum(map(len,rt.values()))+sum(map(len,ht.values()));states={0:(baseline,{})}
 for h in hids:
  nxt=dict(states)
  for mask,(cost,mapping) in states.items():
   for i,r in enumerate(rids):
    if mask&(1<<i):continue
    c=cost-len(rt[r])-len(ht[h])+edits(rt[r],ht[h])[0];new=mask|(1<<i)
    if new not in nxt or c<nxt[new][0]:nxt[new]=(c,mapping|{r:h})
  states=nxt
 total,mapping=min(states.values(),key=lambda x:x[0]);parts=[]
 for r in rids:parts.append(edits(rt[r],ht[mapping[r]]) if r in mapping else (len(rt[r]),0,len(rt[r]),0))
 for h in hids:
  if h not in mapping.values():parts.append((len(ht[h]),0,0,len(ht[h])))
 counts=[sum(x[i] for x in parts) for i in range(4)];assert counts[0]==total
 den=sum(map(len,rt.values()))
 return dict(metric='midpoint_owned_boundary_sensitive_job_oracle_edit_rate',errors=total,substitutions=counts[1],deletions=counts[2],insertions=counts[3],reference_words=den,rate=total/den if den else None,oracle_mapping=mapping,unknown_words=len(ht.get('__unknown__',[])),global_identity_claim=False,complete_excerpt_cpwer=False)

if __name__=='__main__':
 assert edits(['a','b'],['a'])==(1,0,1,0)
 assert score({'a':'one two','b':'hello'},{'y':'hello','z':'one two'})['errors']==0
 assert score({'a':'one'},{'x':'one','y':'extra'})['insertions']==1
 assert score({'a':'one'},{'__unknown__':'one'})['unknown_words']==1
 assert tokens('Right,So')==['rightso'] # Preserve frozen normalizer, including known weakness.
 assert score({'a':'one two'}, {})['deletions']==2
 print('MODEL_FREE_PAIRED_METRIC_TESTS_PASS')
