"""Annotation-only additional panel; external audio/reference paths only."""
import json,sys,wave,hashlib
from pathlib import Path
from decimal import Decimal
from select_clean_cn_coverage_v2 import parse,candidates,RATE
CURRENT=(15282880,15468012)
def overlaps(a,b):return a[0]<b[1] and b[0]<a[1]
def greedy(eligible,maximum=3):
 selected=[]
 for c in eligible:
  span=(c['start_frame'],c['end_frame'])
  if overlaps(span,CURRENT) or any(overlaps(span,(x['start_frame'],x['end_frame'])) for x in selected):continue
  selected.append(c)
  if len(selected)==maximum:break
 return selected

def main(textgrid,wav,rttm,outdir):
 out=Path(outdir);out.mkdir();rows=parse(Path(textgrid).read_text(encoding='utf-8-sig'))
 with wave.open(wav) as w:
  assert (w.getframerate(),w.getnchannels(),w.getsampwidth())==(RATE,1,2);frames=w.getnframes()
 eligible,rejections=candidates(rows,frames);selected=greedy(eligible);timings=[r.split() for r in Path(rttm).read_text().splitlines()]
 for i,c in enumerate(selected):
  d=out/f'case-{i}';d.mkdir();a,b=Decimal(c['start_frame'])/RATE,Decimal(c['end_frame'])/RATE;inc=[r for r in rows if a<=r['start']<b and r['end']<=b]
  for r in inc:assert any(t[7]==r['speaker'] and abs(Decimal(t[3])-r['start'])<=Decimal('.00011') and abs(Decimal(t[3])+Decimal(t[4])-r['end'])<=Decimal('.00021') for t in timings)
  ref=dict(segments=[dict(speaker=r['speaker'],start=float(r['start']-a),end=float(r['end']-a),text_raw=r['text_raw']) for r in inc]);(d/'reference.json').write_text(json.dumps(ref,ensure_ascii=False,indent=2))
  with wave.open(wav) as w:w.setpos(c['start_frame']);pcm=w.readframes(c['frames'])
  with wave.open(str(d/'input.wav'),'wb') as w:w.setparams((1,2,RATE,0,'NONE','not compressed'));w.writeframes(pcm)
  c.update(pcm_sha256=hashlib.sha256(pcm).hexdigest(),reference_sha256=hashlib.sha256((d/'reference.json').read_bytes()).hexdigest())
 receipt=dict(protocol='cn-panel-selection-v1-20261102',eligible_before_current_exclusion=len(eligible),eligible_after_current_exclusion=sum(not overlaps((c['start_frame'],c['end_frame']),CURRENT) for c in eligible),selected=selected,existing_rejections=rejections,no_inference=True,inputs={str(Path(f).resolve()):hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in [textgrid,wav,rttm]})
 (out/'selection-receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
if __name__=='__main__':main(*sys.argv[1:])
