"""Annotation-only, no models. External private paths; outputs contain private refs.
Protocol CN_CLEAN_PROTOCOL_V1.md is fixed before selection. No auto-relaxation.
"""
import argparse,collections,hashlib,json,re,unicodedata,wave
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING
from pathlib import Path
RATE=16000

def normalize(s):return ''.join(c for c in unicodedata.normalize('NFKC',s).lower() if not unicodedata.category(c).startswith(('P','S')) and not c.isspace())
def parse(text):
 rows=[]
 for tier in re.split(r'\bitem\s*\[\d+\]\s*:',text)[1:]:
  name=re.search(r'\bname\s*=\s*"((?:[^"\n]|"")*)"',tier)
  if not name:raise ValueError('Missing tier name')
  speaker=name[1].replace('""','"')
  for block in re.split(r'\bintervals\s*\[\d+\]\s*:',tier)[1:]:
   a=re.search(r'\bxmin\s*=\s*([\d.]+)',block);b=re.search(r'\bxmax\s*=\s*([\d.]+)',block);t=re.search(r'\btext\s*=\s*"((?:[^"]|"")*)"',block)
   if not(a and b and t):raise ValueError('Malformed interval')
   raw=t[1].replace('""','"')
   if not raw.strip():continue
   start,end=Decimal(a[1]),Decimal(b[1]);assert 0<=start<end
   rows.append(dict(speaker=speaker,start=start,end=end,text_raw=raw,units=len(normalize(raw)),tagged=bool(re.search(r'<[^>]*>',raw) or '&' in raw)))
 return sorted(rows,key=lambda r:(r['start'],r['speaker'],r['end']))
def candidates(rows,available_frames):
 floor=lambda x:int((x*RATE).to_integral_value(rounding=ROUND_FLOOR));ceil=lambda x:int((x*RATE).to_integral_value(rounding=ROUND_CEILING))
 starts=sorted({floor(r['start']) for r in rows});ends=sorted({ceil(r['end']) for r in rows});accepted=[];reject=collections.Counter()
 for a in starts:
  for b in ends:
   if not 0<b-a<=45*RATE or b>available_frames:continue
   if a<2616506 and b>1998020:reject['prior_case_overlap']+=1;continue
   aa,bb=Decimal(a)/RATE,Decimal(b)/RATE
   inc=[r for r in rows if r['start']<bb and r['end']>aa]
   if not inc or any(r['start']<aa or r['end']>bb for r in inc):reject['incomplete_annotation']+=1;continue
   if any(r['tagged'] for r in inc):reject['annotation_tag']+=1;continue
   counts=collections.Counter()
   for r in inc:counts[r['speaker']]+=r['units']
   counts={k:v for k,v in counts.items() if v};total=sum(counts.values())
   if not 3<=len(counts)<=4:reject['speaker_count']+=1;continue
   if total<60 or any(v<10 or v*10<total for v in counts.values()):reject['lexical_balance']+=1;continue
   accepted.append(dict(start_frame=a,end_frame=b,frames=b-a,reference_characters=total,characters_by_speaker=counts))
 return sorted(accepted,key=lambda r:(r['frames'],r['start_frame'],r['end_frame'])),dict(reject)
def main():
 p=argparse.ArgumentParser();p.add_argument('textgrid');p.add_argument('wav');p.add_argument('rttm');p.add_argument('out');a=p.parse_args();out=Path(a.out);out.mkdir()
 rows=parse(Path(a.textgrid).read_text(encoding='utf-8-sig'))
 with wave.open(a.wav) as w:
  assert(w.getframerate(),w.getnchannels(),w.getsampwidth())==(16000,1,2);frames=w.getnframes();w.setpos(1998020);anchor=w.readframes(618486)
 anchor_hash=hashlib.sha256(anchor).hexdigest();assert anchor_hash=='8ddea2760ce0c01d44d775f1811c104d7f6f9e0a6ea6b7199fda7340e3d3e6a9'
 eligible,rejections=candidates(rows,frames);selected=eligible[:1];rttm=[x.split() for x in Path(a.rttm).read_text().splitlines()]
 for c in selected:
  aa,bb=Decimal(c['start_frame'])/RATE,Decimal(c['end_frame'])/RATE;inc=[r for r in rows if aa<=r['start']<bb and r['end']<=bb]
  for r in inc:
   assert any(t[7]==r['speaker'] and abs(Decimal(t[3])-r['start'])<=Decimal('.00011') and abs(Decimal(t[3])+Decimal(t[4])-r['end'])<=Decimal('.00021') for t in rttm)
  private=dict(segments=[dict(speaker=r['speaker'],start=float(r['start']-aa),end=float(r['end']-aa),text_raw=r['text_raw']) for r in inc])
  (out/'reference.json').write_text(json.dumps(private,ensure_ascii=False,indent=2))
  with wave.open(a.wav) as w:w.setpos(c['start_frame']);pcm=w.readframes(c['frames'])
  with wave.open(str(out/'input.wav'),'wb') as w:w.setparams((1,2,RATE,0,'NONE','not compressed'));w.writeframes(pcm)
  c['pcm_sha256']=hashlib.sha256(pcm).hexdigest()
 receipt=dict(protocol='cn-clean-balanced-v1-20261102',decoded_frames=frames,decoded_seconds=frames/RATE,anchor_pcm_match=True,anchor_pcm_sha256=anchor_hash,nonempty_annotation_intervals=len(rows),eligible_count=len(eligible),selected=selected,rejection_counts=rejections,inputs={str(Path(f).resolve()):hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in [a.textgrid,a.wav,a.rttm]},no_inference=True)
 (out/'selection-receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
