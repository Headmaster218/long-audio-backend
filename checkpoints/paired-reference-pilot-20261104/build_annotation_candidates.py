"""Metadata-only prospective AMI candidate selection; no audio/embedding/score reads."""
from pathlib import Path
import zipfile,xml.etree.ElementTree as E,hashlib,json,math
R=Path(__file__).resolve().parent
Z=R.parent/'backend-carryover-assets-20261104/ami_public_manual_1.6.2.zip'
META=json.loads((R/'official_meetings_metadata.json').read_text());MM={m['observation']:m for m in META}
NS='{http://nite.sourceforge.net/}'
def frames(x):return round(float(x)*16000)
def subtract(a,b,forbidden):
 out=[(a,b)]
 for c,d in forbidden:
  out=[z for l,r in out for z in ([(l,r)] if d<=l or c>=r else [(l,min(c,r)),(max(d,l),r)]) if z[1]>z[0]]
 return out
annotations={};bymeeting={};sha={}
with zipfile.ZipFile(Z)as z:
 for m in ['ES2014a','ES2014d','ES2015a','ES2015d']:
  agents={}
  for s in MM[m]['speakers']:
   agent=s['nxt_agent'];names=[f'{layer}/{m}.{agent}.{layer}.xml'for layer in ['segments','words']]
   bs=[z.read(n)for n in names]
   sha.update({n:hashlib.sha256(b).hexdigest()for n,b in zip(names,bs)})
   segs=[]
   for e in E.fromstring(bs[0]):
    segs.append({'id':e.attrib.get(NS+'id'),'start_frame':frames(e.attrib['transcriber_start']),'end_frame':frames(e.attrib['transcriber_end'])})
   words=[];bad=[]
   for e in E.fromstring(bs[1]):
    a=e.attrib
    if 'starttime' not in a or 'endtime' not in a:continue
    l,r=frames(a['starttime']),frames(a['endtime'])
    if r<=l:continue
    if e.tag=='w' and a.get('punc')!='true':words.append((l,r))
    else:bad.append((l-1600,r+1600))
   chains=[]
   for l,r in sorted(words):
    if chains and l<=chains[-1][1]:chains[-1]=(chains[-1][0],max(chains[-1][1],r))
    else:chains.append((l,r))
   agents[agent]={'speaker':s,'segments':segs,'words':words,'word_chains':chains,'bad_intervals':bad}
  bymeeting[m]=agents
  for a,d in agents.items():
   other=[(x['start_frame']-1600,x['end_frame']+1600)for k,v in agents.items()if k!=a for x in v['segments']]
   forbidden=other+d['bad_intervals'];candidates=[]
   for seg in d['segments']:
    sl,sr=seg['start_frame']+1600,seg['end_frame']-1600
    for l,r in d['word_chains']:
     l,r=max(sl,l+1600),min(sr,r-1600)
     if r-l<32000:continue
     for start,end in subtract(l,r,forbidden):
      if end-start>=32000:
       candidates.append({'meeting':m,'person_id':d['speaker']['global_name'],'agent':a,'channel':d['speaker']['channel'],'segment_id':seg['id'],'segment_start_frame':seg['start_frame'],'segment_end_frame':seg['end_frame'],'start_frame':start,'end_frame':start+32000,'earliest_qualifying_interval_end_frame':end,'start_seconds':start/16000,'end_seconds':(start+32000)/16000})
       break
   # One earliest candidate per original segment; disjoint + 30-second start separation.
   chosen=[];seen=set()
   for c in sorted(candidates,key=lambda x:(x['start_frame'],x['segment_id'])):
    if c['segment_id']in seen:continue
    if chosen and c['start_frame']-chosen[-1]['start_frame']<480000:continue
    chosen.append(c);seen.add(c['segment_id'])
   annotations[(m,d['speaker']['global_name'])]=chosen
rows=[];roster=[]
for split,series in [('C','ES2014'),('T','ES2015')]:
 people=sorted(s['global_name']for s in MM[series+'a']['speakers'])
 for i,p in enumerate(people):
  role='known'if i<2 else'unknown';roster.append({'split':split,'person_id':p,'role':role,'reference_meeting':series+'a'if role=='known'else None,'query_meeting':series+'d','date':MM[series+'a']['dateOnly']})
  selections=([('reference',series+'a',3)]if role=='known'else[])+[('query',series+'d',3)]
  for crop_role,m,n in selections:
   cc=annotations[(m,p)];assert len(cc)>=n,(m,p,len(cc),n)
   for slot,c in enumerate(cc[:n],1):rows.append(dict(c,split=split,person_role=role,crop_role=crop_role,slot=slot,official_wav_url=f'https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/{m}/audio/{m}.Mix-Headset.wav'))
out={'status':'prospective_metadata_only_not_frozen_no_audio_or_scores','design':'AMI same-day early-meeting references to later-meeting query; distinct from strict D planner','selection_rules':['Series ES2014 for C and ES2015 for T conditional on complete prior-use audit.','Each series four official global IDs sorted: first2 known,next2 unknown.','3 references from distinct original annotated segments in meeting a, ≥30 seconds apart; 1 primary plus2 diagnostic queries in meeting d, in distinct segments ≥30 seconds apart.','Earliest 2 seconds per segment wholly within contiguous lexical word intervals, eroded100ms. Segment boundaries eroded100ms. Other speakers full annotated segments and own positive-duration non-lexical events expanded100ms and excluded.','No waveform inspection, speech-quality proof, human listening or model scores. Annotation oracle is imperfect forcedalignment support, not physical purity.','No automatic substitutions: this is a feasibility list, parent must freeze exact manifest and failure rules before acquisition/quality review.'], 'annotation_zip':str(Z),'meetings_sha256':hashlib.sha256((R/'meetings.xml').read_bytes()).hexdigest(),'annotation_sha256':sha,'roster':roster,'counts':{'speakers':8,'known':4,'unknown':4,'reference_crops':12,'query_crops':24,'total_crops':36,'crop_pcm_bytes':36*64000,'crop_audio_seconds':72},'rows':rows}
# Bind verified byte geometry without claiming a full-source content hash.
headers=json.loads((R/'official_wav_range_headers.json').read_text())
byurl={x['url']:x for x in headers}
for row in rows:
 h=byurl[row['official_wav_url']]
 total=int(h['headers']['content-range'].rsplit('/',1)[1])
 row.update(source_file_size_bytes=total,source_etag=h['headers']['etag'],source_last_modified=h['headers']['last-modified'],verified_audio_format={'format_tag':1,'channels':1,'sample_rate':16000,'bits_per_sample':16,'block_align':2,'data_offset':44},prospective_http_byte_range=f'bytes={44+row["start_frame"]*2}-{44+row["end_frame"]*2-1}')
 assert 44+row['end_frame']*2<=total
out['verified_sources']=[{'url':h['url'],'bytes':int(h['headers']['content-range'].rsplit('/',1)[1]),'etag':h['headers']['etag'],'last_modified':h['headers']['last-modified'],'header_sha256':h['header_sha256']} for h in headers]
out['counts'].update(full_wav_download_bytes=sum(x['bytes']for x in out['verified_sources']),metadata_probe_bytes=176)
out['status']='prospective_metadata_only_prior_calibration_person_inventory_incomplete'
out['unresolved_prior_use']='The recovered ami20 profile names20 calibration people without IDs; unrecovered original artifact means absence of selected meetings in present workspace does not prove never-used global IDs.'
(R/'prospective_annotation_candidates.json').write_text(json.dumps(out,indent=2)+'\n')
for r in roster:print(r)
for x in rows:print(x['split'],x['person_id'],x['crop_role'],x['meeting'],x['start_seconds'],x['end_seconds'],x['segment_id'])
print('MANIFEST_SHA256',hashlib.sha256((R/'prospective_annotation_candidates.json').read_bytes()).hexdigest())
