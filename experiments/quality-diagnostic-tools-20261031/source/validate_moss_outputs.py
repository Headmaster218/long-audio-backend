"""Validate external separator artifacts before ASR; no model/reference input."""
import hashlib,json,sys
from pathlib import Path

def main(repo,request_path,regions_path,output_dir):
 sys.path.insert(0,repo)
 import numpy as np,soundfile as sf
 from audio_backend.overlap_candidates import _validate_separator_response
 out=Path(output_dir).resolve();request=json.loads(Path(request_path).read_text());regions=json.loads(Path(regions_path).read_text());jobs=[r for r in regions if r['overlap_group_id'] in {j['overlap_group_id'] for j in request['jobs']}]
 result=json.loads((out/'result.json').read_text());_validate_separator_response(result,jobs,out)
 if {g['overlap_group_id'] for g in result['groups']}!={j['overlap_group_id'] for j in jobs}:raise ValueError('missing group')
 receipt=[]
 for g in result['groups']:
  if not g['tracks']:raise ValueError('empty source group')
  for t in g['tracks']:
   path=Path(t['path']);samples,sr=sf.read(path,dtype='float32');info=sf.info(path)
   if sr!=16000 or samples.ndim!=1 or not len(samples) or info.subtype!='FLOAT' or not np.isfinite(samples).all():raise ValueError('invalid float waveform')
   if len(samples)!=round(t['end']*16000)-round(t['start']*16000):raise ValueError('source PCM length differs from scope')
   receipt.append(dict(group=g['overlap_group_id'],scope_id=t['scope_id'],source_slot=t['source_slot'],audio=str(path.resolve()),frames=len(samples),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),start=t['start'],end=t['end']))
 return receipt
if __name__=='__main__':print(json.dumps(main(*sys.argv[1:]),indent=2))
