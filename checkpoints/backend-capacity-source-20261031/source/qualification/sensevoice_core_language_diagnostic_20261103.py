"""Bounded pinned public-release decoder diagnostic; writes hashes/counts, no text.

Requires an existing extracted release and its expanded-receipt.json inventory.
Use an external timeout/resource limit; no service, downloads or model promotion.
This is not strict-service or accuracy qualification.
"""
import argparse,gc,hashlib,json,time,wave
from pathlib import Path
import numpy as np
import sherpa_onnx as s
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--asset-root',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
args=parser.parse_args()
R=args.asset_root.resolve();D=args.output.resolve();D.mkdir(mode=0o700,exist_ok=False)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
files=next(r['files'] for r in json.loads((R/'expanded-receipt.json').read_text()) if r['archive']=='models/sensevoice/model.tar.bz2')
pins={'tokens.txt': 'f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc', 'model.int8.onnx': 'c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51', 'ja.wav': '460bd8dccb0d2a5f4e29c628f837be4082d13defc64c3fc21dd1b6bb0e119095', 'yue.wav': '0960b2db54ae202071d250e6462fbf74a3c863f0e3e7f01273e4939c996875a0', 'ko.wav': '0dc797a5c81ed30fc339d91f3da718ab02854e17ffa37cb93c4c039ac5c6bb9c', 'zh.wav': 'b77f1794fe374a0ba1ee1dc458bfaf9349496cbbfc32780c50ba3c5a7ad8e373'}
selected=[r for r in files if Path(r['path']).name in pins]
assert len(selected)==6 and {Path(r['path']).name for r in selected}==set(pins)
verified={}
for r in selected:
 name=Path(r['path']).name;path=(R/r['path']).resolve()
 assert path.is_relative_to(R) and path.stat().st_size==r['bytes'] and digest(path)==r['sha256']==pins[name]
 verified[name]=path
model=verified['model.int8.onnx']
# Frozen before execution: diagnostic zh plus three other public language samples;
# full clip and every independent 5.25s core, en/auto/matching language.
summary=[];start=time.monotonic()
for language in ['en','auto','zh','ja','ko','yue']:
 recognizer=s.OfflineRecognizer.from_sense_voice(model=str(model),tokens=str(verified['tokens.txt']),num_threads=2,language=language,use_itn=True,provider='cpu')
 for name in ['zh','ja','ko','yue']:
  if language not in ['en','auto',name]:continue
  p=verified[f'{name}.wav']
  with wave.open(str(p),'rb') as w:
   assert (w.getnchannels(),w.getsampwidth(),w.getframerate())==(1,2,16000)
   assert 0 < w.getnframes() <= 480000
   audio=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype('float32')/32768
  variants=[('full',0,len(audio))]+[(f'core_{i//84000}',i,min(i+84000,len(audio))) for i in range(0,len(audio),84000)]
  for variant,left,right in variants:
   assert time.monotonic()-start<240
   stream=recognizer.create_stream();stream.accept_waveform(16000,audio[left:right]);recognizer.decode_stream(stream);v=stream.result
   record=dict(sample=name,sha256=digest(p),language=language,variant=variant,frames=right-left,text=v.text,detected_language=v.lang,token_count=len(v.tokens))
   summary.append({k:v for k,v in record.items() if k!='text'}|{'text_sha256':hashlib.sha256(v.text.encode()).hexdigest(),'text_characters':len(v.text),'ascii_letters':sum(c.isascii() and c.isalpha() for c in v.text)})
 del recognizer;gc.collect()
(D/'SUMMARY.json').write_text(json.dumps({'scope':'standalone decoder only; not strict service; no reference accuracy score','assets':selected,'runtime_version':s.__version__,'rows':summary,'elapsed_seconds':time.monotonic()-start},indent=2))
print(json.dumps({'rows':len(summary),'elapsed_seconds':time.monotonic()-start,'summary_sha256':digest(D/'SUMMARY.json')}))
