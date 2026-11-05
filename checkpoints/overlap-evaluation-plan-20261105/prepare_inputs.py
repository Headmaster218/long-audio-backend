#!/usr/bin/env python3
"""Frozen metadata-only selection and exact PCM crops. Does not invoke any model."""
import argparse, collections, decimal, hashlib, json, re, unicodedata, wave, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parent.parent
BASE=Path(__file__).resolve().parent
RATE=16000

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def normalize(s):
 return ''.join(c for c in unicodedata.normalize('NFKC',s).lower() if not unicodedata.category(c).startswith(('P','S')))
def units(s,lang):
 s=normalize(s);return s.split() if lang=='en' else list(''.join(s.split()))
def frame(s):
 x=decimal.Decimal(str(s))*RATE
 if x!=x.to_integral_value():raise ValueError('Nonintegral source frame boundary')
 return int(x)
def quoted(s):
 if not(s.startswith('"')and s.endswith('"')):raise ValueError('Invalid quoted TextGrid field')
 return s[1:-1].replace('""','"')
def parse_textgrid(path):
 text=Path(path).read_text(encoding='utf-8-sig')
 if not text.startswith('File type = "ooTextFile"'):raise ValueError('Unsupported TextGrid encoding/format')
 tiers=list(re.finditer(r'^\s*item \[(\d+)\]:\s*$',text,re.M))
 header=text[:tiers[0].start()]
 domain=[frame(re.search(r'^\s*'+key+r' = ([0-9.]+)\s*$',header,re.M)[1])for key in ('xmin','xmax')]
 if int(re.search(r'^size = (\d+)\s*$',header,re.M)[1])!=len(tiers):raise ValueError('Tier count mismatch')
 intervals=[];tier_info=[]
 for k,match in enumerate(tiers):
  block=text[match.end():tiers[k+1].start()if k+1<len(tiers)else len(text)]
  if re.search(r'^\s*class = "([^"]+)"',block,re.M)[1]!='IntervalTier':raise ValueError('Unsupported tier class')
  name=quoted(re.search(r'^\s*name = (".*")\s*$',block,re.M)[1])
  head=block.split('intervals:')[0];tdomain=[frame(re.search(r'^\s*'+key+r' = ([0-9.]+)\s*$',head,re.M)[1])for key in ('xmin','xmax')]
  if tdomain[0]<domain[0] or tdomain[1]>domain[1]:raise ValueError('Tier outside global domain')
  items=list(re.finditer(r'^\s*intervals \[(\d+)\]:\s*\n\s*xmin = ([0-9.]+)\s*\n\s*xmax = ([0-9.]+)\s*\n\s*text = (".*")\s*$',block,re.M))
  expected=int(re.search(r'intervals: size = (\d+)',block)[1])
  if len(items)!=expected:raise ValueError('Unsupported or missing interval fields')
  if [int(i[1])for i in items]!=list(range(1,expected+1)):raise ValueError('Interval ordering mismatch')
  previous=tdomain[0]
  for it in items:
   l,r=frame(it[2]),frame(it[3]);mark=quoted(it[4])
   if not(tdomain[0]<=l<r<=tdomain[1])or l<previous:raise ValueError('Invalid/overlapping same-tier interval')
   previous=r
   intervals.append({'id':name+':'+it[1],'speaker':name,'index':int(it[1]),'start_frame':l,'end_frame':r,'raw_text':mark,'tier_domain':tdomain})
  tier_info.append({'id':name,'domain_frames':tdomain,'intervals':expected})
 if len({x['id']for x in tier_info})!=len(tier_info):raise ValueError('Duplicate speaker tier')
 return domain,tier_info,intervals

def union_stats(intervals,start,end):
 changes=collections.defaultdict(list)
 for x in intervals:
  l=max(start,x['start_frame']);r=min(end,x['end_frame'])
  if l<r:changes[l].append((x['speaker'],1));changes[r].append((x['speaker'],-1))
 active=collections.Counter();last=start;union=overlap=0;per=collections.Counter()
 for pos in sorted(set(changes)|{start,end}):
  count=sum(v>0 for v in active.values())
  if count:union+=pos-last
  if count>=2:overlap+=pos-last
  for sp,n in active.items():
   if n>0:per[sp]+=pos-last
  for sp,n in changes[pos]:active[sp]+=n
  last=pos
 return {'labeled_union_frames':union,'overlap_frames':overlap,'unlabeled_gap_frames':end-start-union,'per_speaker_frames':dict(per)}
def components(intervals,domain,audio_frames):
 marked=[x for x in intervals if x['raw_text'].strip()]
 out=[]
 for x in sorted(marked,key=lambda x:(x['start_frame'],x['end_frame'],x['speaker'],x['index'])):
  l=max(0,domain[0],x['tier_domain'][0],x['start_frame']-4000)
  r=min(audio_frames,domain[1],x['tier_domain'][1],x['end_frame']+4000)
  if out and l<out[-1]['end_frame']:
   out[-1]['end_frame']=max(out[-1]['end_frame'],r);out[-1]['intervals'].append(x)
  else:out.append({'start_frame':l,'end_frame':r,'intervals':[x]})
 for i,c in enumerate(out):
  c['component_index']=i;c['speaker_texts']={}
  for x in c['intervals']:c['speaker_texts'].setdefault(x['speaker'],[]).append(x['raw_text'])
  c['speaker_texts']={k:' '.join(v)for k,v in sorted(c['speaker_texts'].items())}
  c['speaker_characters']={k:len(units(v,'zh'))for k,v in c['speaker_texts'].items()}
  c['coverage']=union_stats(c['intervals'],c['start_frame'],c['end_frame'])
  for x in marked:
   if x['start_frame']<c['end_frame'] and x['end_frame']>c['start_frame']:
    if x not in c['intervals']or x['start_frame']<c['start_frame']or x['end_frame']>c['end_frame']:raise ValueError('Utterance closure violated')
 return out

def crop(source,start,end,destination):
 with wave.open(str(source),'rb')as w:
  if (w.getframerate(),w.getnchannels(),w.getsampwidth())!=(16000,1,2):raise ValueError('Unsupported source geometry')
  if not(0<=start<end<=w.getnframes()):raise ValueError('Invalid source range')
  w.setpos(start);pcm=w.readframes(end-start)
 if len(pcm)!=(end-start)*2:raise ValueError('Short crop')
 with wave.open(str(destination),'wb')as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(pcm)
 return {'path':str(destination),'bytes':destination.stat().st_size,'sha256':sha(destination),'pcm_sha256':hashlib.sha256(pcm).hexdigest(),'frames':end-start,'sample_rate_hz':16000,'channels':1,'sample_width_bytes':2}
def main():
 p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args();out=Path(a.out);out.mkdir(exist_ok=False);(out/'crops').mkdir();(out/'gold').mkdir()
 protocol_path=BASE/'PROTOCOL_RULES.json';protocol=json.loads(protocol_path.read_text());cases=[]
 z=zipfile.ZipFile(ROOT/protocol['provenance']['annotation_archive']['path'])
 for row in protocol['panel']['exact_english_cases']:
  source=ROOT/row['source_file']['path']
  if sha(source)!=row['source_file']['sha256']:raise ValueError('Source hash mismatch')
  words={}
  for name,h in row['annotation_members_sha256'].items():
   b=z.read(name)
   if hashlib.sha256(b).hexdigest()!=h:raise ValueError('Annotation member changed')
   if name.startswith('words/'):
    for e in ET.fromstring(b):words[e.attrib.get('{http://nite.sourceforge.net/}id')]=e
  refs=collections.defaultdict(list);ref_intervals=[]
  for w in row['reference_word_ids']:
   e=words[w['id']];x=dict(w,raw_text=e.text or '');refs[w['speaker']].append(x);ref_intervals.append(x)
  speakers=[{'id':sp,'text':' '.join(x['raw_text']for x in sorted(ws,key=lambda x:(x['start_frame'],x['end_frame'],x['id'])))}for sp,ws in sorted(refs.items())]
  c={'case_id':row['id'],'language':'en','class':row['class'],'corpus':'AMI','meeting':row['session'],'start_frame':row['start_frame'],'end_frame':row['end_frame'],'status':'ready','source_file':row['source_file'],'reference_intervals':ref_intervals,'reference_annotation':'AMI human transcript with forced-aligned word times','whole_clip_lexical_scoring_eligible':True,'speakers':speakers}
  c['crop']=crop(source,c['start_frame'],c['end_frame'],out/'crops'/(c['case_id']+'.wav'));cases.append(c)
 path=ROOT/'overlap-assets-preparation-20261105-01/alimeeting-R8001_M8004/R8001_M8004.TextGrid';source=path.parent/'channel1.wav'
 if sha(path)!=protocol['chinese_source']['reference_sha256']or sha(source)!=protocol['chinese_source']['mono_file_sha256']:raise ValueError('Chinese source changed')
 with wave.open(str(source),'rb')as w:audio_frames=w.getnframes()
 domain,tiers,intervals=parse_textgrid(path);comps=components(intervals,domain,audio_frames)
 eligible=[c for c in comps if 4*RATE<=c['end_frame']-c['start_frame']<=20*RATE and len(c['speaker_texts'])==2 and min(c['speaker_characters'].values())>=10 and c['coverage']['overlap_frames']>=RATE]
 selected=[]
 for c in eligible:
  if not selected or c['start_frame']-selected[-1]['start_frame']>=60*RATE:selected.append(c)
  if len(selected)==2:break
 solos=[c for c in comps if 4*RATE<=c['end_frame']-c['start_frame']<=20*RATE and len(c['speaker_texts'])==1 and min(c['speaker_characters'].values())>=10 and all(c['end_frame']<=o['start_frame']or c['start_frame']>=o['end_frame']for o in selected)]
 for id,c,kind in [('zh-overlap-'+str(i+1),selected[i]if i<len(selected)else None,'overlap')for i in range(2)]+[('zh-solo-1',solos[0]if solos else None,'solo')]:
  if c is None:cases.append({'case_id':id,'language':'zh','class':kind,'status':'unavailable_no_eligible_component'});continue
  row={'case_id':id,'language':'zh','class':kind,'corpus':'AliMeeting','meeting':'R8001_M8004','start_frame':c['start_frame'],'end_frame':c['end_frame'],'status':'ready','source_file':{'path':str(source),'sha256':sha(source)},'annotation':{'path':str(path),'sha256':sha(path),'domain_frames':domain,'tiers':tiers},'component_index':c['component_index'],'reference_intervals':c['intervals'],'reference_annotation':'Human per-speaker TextGrid utterances; no word or character timestamps','whole_clip_lexical_scoring_eligible':True,'speakers':[{'id':sp,'text':text}for sp,text in c['speaker_texts'].items()],'coverage':c['coverage']}
  row['crop']=crop(source,row['start_frame'],row['end_frame'],out/'crops'/(id+'.wav'));cases.append(row)
 zero=out/'crops'/'digital-zero-1.wav';pcm=bytes(96000*2)
 with wave.open(str(zero),'wb')as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(pcm)
 cases.append({'case_id':'digital-zero-1','language':'en','class':'synthetic_zero','corpus':None,'status':'ready','start_frame':0,'end_frame':96000,'whole_clip_lexical_scoring_eligible':True,'speakers':[],'crop':{'path':str(zero),'bytes':zero.stat().st_size,'sha256':sha(zero),'pcm_sha256':hashlib.sha256(pcm).hexdigest(),'frames':96000,'sample_rate_hz':16000,'channels':1,'sample_width_bytes':2}})
 order=['en-overlap-1','en-overlap-2','zh-overlap-1','zh-overlap-2','en-solo-1','zh-solo-1','digital-zero-1'];cases.sort(key=lambda c:order.index(c['case_id']))
 for c in cases:
  g=out/'gold'/(c['case_id']+'.json');g.write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n');c['gold_file']={'path':str(g),'sha256':sha(g)}
 manifest={'schema':'overlap-private-input-manifest-v1','status':'concrete_inputs_bound_pending_runtime_and_independent_review','protocol_sha256':sha(protocol_path),'selector_sha256':sha(Path(__file__)),'case_order':order,'cases':cases,'chinese_selection_audit':{'components':len(comps),'eligible_overlap_components':len(eligible),'eligible_solo_components':len(solos),'selected_component_indices':[c['component_index']for c in selected],'total_marked_intervals':sum(bool(x['raw_text'].strip())for x in intervals),'textgrid_sha256':sha(path)},'total_ready_audio_seconds':sum((c['end_frame']-c['start_frame'])/RATE for c in cases if c['status']=='ready'),'model_inference_performed':False,'no_final_model_launch_authority':True}
 dest=out/'PRIVATE_INPUT_MANIFEST.json';dest.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
 public={'schema':'overlap-input-summary-public-v1','protocol_sha256':sha(protocol_path),'private_manifest_sha256':sha(dest),'cases':[{'case_id':c['case_id'],'language':c['language'],'class':c['class'],'status':c['status'],'meeting':c.get('meeting'),'start_frame':c.get('start_frame'),'end_frame':c.get('end_frame'),'reference_counts':[len(units(s['text'],c['language']))for s in c.get('speakers',[])],'crop_sha256':c.get('crop',{}).get('sha256'),'gold_sha256':c['gold_file']['sha256']}for c in cases],'total_audio_seconds':manifest['total_ready_audio_seconds'],'limitations':['Previously exposed public meetings; not held out.','Direct multi-speaker ASR, not waveform separation.','Human annotation timing and channel audibility have limitations.','No inference yet.']}
 (out/'PUBLIC_INPUT_SUMMARY.json').write_text(json.dumps(public,indent=2)+'\n');print(json.dumps(public,indent=2))
if __name__=='__main__':main()
