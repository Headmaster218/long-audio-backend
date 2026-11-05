"""Materialize exactly the 36 preselected coordinates, with no quality filtering."""
import hashlib,json,struct,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PLAN=ROOT.parent/'fresh-ami-feasibility-20261104/prospective_annotation_candidates.json'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 assert sha(PLAN)=='fc297e67fbee68481809c600068ffc159df0742c395f00a1b51f767a13a16e52'
 receipt_path=ROOT/'acquired-recovery-02/RECOVERY_RECEIPT.json'
 receipt=json.loads(receipt_path.read_text())
 assert receipt['status']=='complete_recovered_not_original_single_attempt'
 audit=ROOT/'metadata-audit/metadata_audit.json'
 assert json.loads(audit.read_text())['status']=='PASS_WITH_EXPLICIT_LIMITATIONS'
 sources={Path(x['path']).name:x for x in receipt['files']}
 assert len(sources)==4
 for item in sources.values():assert sha(Path(item['path']))==item['sha256']
 plan=json.loads(PLAN.read_text());out=ROOT/'crops-01';out.mkdir(exist_ok=False)
 rows=[]
 for row in plan['rows']:
  identity=f"{row['split']}-{row['person_id']}-{row['crop_role']}-{row['slot']}"
  source=sources[row['meeting']+'.Mix-Headset.wav'];p=Path(source['path'])
  start,end=row['start_frame'],row['end_frame'];assert end-start==32000
  with wave.open(str(p)) as w:
   w.setpos(start);pcm=w.readframes(end-start)
  assert len(pcm)==64000
  dest=out/row['split']/(identity+'.wav');dest.parent.mkdir(exist_ok=True)
  with wave.open(str(dest),'wb') as w:
   w.setparams((1,2,16000,32000,'NONE','not compressed'));w.writeframes(pcm)
  with wave.open(str(dest)) as w:assert w.readframes(32001)==pcm
  floats=b''.join(struct.pack('<f',n[0]/32768) for n in struct.iter_unpack('<h',pcm))
  rows.append(dict(row,id=identity,path=str(dest),bytes=dest.stat().st_size,sha256=sha(dest),pcm_sha256=hashlib.sha256(pcm).hexdigest(),float32_sha256=hashlib.sha256(floats).hexdigest(),source_sha256=source['sha256']))
 assert len(rows)==36 and len({r['id'] for r in rows})==36
 result={'status':'complete_fixed_coordinates','candidate_sha256':sha(PLAN),'source_receipt_sha256':sha(receipt_path),'metadata_audit_sha256':sha(audit),'materializer_sha256':sha(Path(__file__)),'attribution':'AMI Meeting Corpus, CC BY 4.0 https://groups.inf.ed.ac.uk/ami/corpus/license.shtml ; original recordings cropped to preselected two-second excerpts, no other waveform changes.','rows':rows}
 (out/'CROPS_MANIFEST.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'path':str(out/'CROPS_MANIFEST.json'),'sha256':sha(out/'CROPS_MANIFEST.json'),'clips':len(rows)}))
if __name__=='__main__':main()
