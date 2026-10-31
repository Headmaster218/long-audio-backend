"""Single isolated native invocation. No gold/scorer input; no production override."""
import array,hashlib,json,sys,wave
from pathlib import Path

def main(spec_path,out_path):
 s=json.loads(Path(spec_path).read_text());out=Path(out_path)
 # Parent validates pins; repeat input/model identity before any native import.
 model=Path(s['model']);assert model.stat().st_size==986899616
 with model.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==s['model_sha256']
 with wave.open(s['audio']) as f:
  assert (f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getnframes())==(1,2,16000,731648)
  pcm=f.readframes(731648)
 assert hashlib.sha256(pcm).hexdigest()==s['pcm_sha256']
 samples=array.array('h',pcm)
 if sys.byteorder!='little':samples.byteswap()
 samples=array.array('f',(x/32768 for x in samples))
 import transcribe_cpp as api
 model=session=None;receipt=dict(attempts=0,eos=None,generated_token_count=None,complete_content=False,truncated=False,aborted=False,exception=None,model_closed=False,session_closed=False)
 def save(): (out/'receipt.json').write_text(json.dumps(receipt,indent=2))
 try:
  model=api.Model(s['model'],backend='cpu')
  assert model.device.kind.lower()=='cpu' and model.backend.lower().startswith('cpu') and model.arch=='moss' and model.variant=='moss-transcribe-diarize'
  session=model.session(n_threads=2,n_ctx=2048,kv_type='f16');limits=session.limits
  receipt['limits']=dict(effective_n_ctx=limits.effective_n_ctx,effective_max_audio_ms=limits.effective_max_audio_ms)
  assert limits.effective_n_ctx==2048 and limits.effective_max_audio_ms>=45728
  receipt['attempts']=1;save()
  try: result=session.run(samples,diarize='on',timestamps='segment')
  except (api.OutputTruncated,api.Aborted) as e:
   receipt['truncated']=isinstance(e,api.OutputTruncated);receipt['aborted']=isinstance(e,api.Aborted);result=e.partial_result
  raw=result.raw_text.encode('utf-8');assert len(raw)<=1048576
  (out/'exact-native-raw.utf8').write_bytes(raw)
  receipt.update(raw_sha256=hashlib.sha256(raw).hexdigest(),raw_bytes=len(raw),public_token_rows=len(result.tokens),source_derived_eos_inference=not(receipt['truncated'] or receipt['aborted']))
 except BaseException as e:receipt['exception']=type(e).__name__;raise
 finally:
  for name,obj in [('session',session),('model',model)]:
   if obj is not None:
    try:obj.close();receipt[name+'_closed']=True
    except Exception as e:receipt[name+'_close_error']=type(e).__name__
  save()
 if receipt['truncated'] or receipt['aborted'] or not receipt['model_closed'] or not receipt['session_closed']:raise SystemExit(4)
if __name__=='__main__':main(*sys.argv[1:])
