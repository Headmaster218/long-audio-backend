"""Existing direct native/ASR alignment baseline; no gold access, no HTTP claim."""
from pathlib import Path
import hashlib,json,sys,wave

def main(spec_path,output):
 s=json.loads(Path(spec_path).read_text());out=Path(output);sys.path.insert(0,s['repo'])
 def save(name,data):(out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2))
 with wave.open(s['audio']) as f:
  assert (f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getnframes())==(1,2,16000,s['frames'])
  pcm=f.readframes(s['frames'])
 assert hashlib.sha256(pcm).hexdigest()==s['pcm_sha256']
 seconds=s['frames']/16000;cfg=json.loads(Path(s['config']).read_text())
 if s['phase']=='native':
  from audio_backend.inference.native_diarization import NativeDiarizer
  d=NativeDiarizer(cfg)
  try:
   reset=d.reset(s['case_id']);push=d.push(Path(s['audio']));finish=d.finish();turns=d.turns(0,seconds)
   save('native.json',dict(reset=reset,push=push,finish=finish,turns=turns,provenance=d.provenance(),mode='standalone completed epoch, not rolling Worker'))
  finally:d.close()
 elif s['phase']=='asr':
  import soundfile as sf
  from audio_backend.inference.analyzer import AudioAnalyzer,align_tokens
  prior=json.loads(Path(s['native_execution']).read_text());assert prior['returncode']==0 and prior['reason'] is None and prior['original_handle_reaped'] and not prior['unexpected_live_descendants']
  a=AudioAnalyzer(cfg);samples,sr=sf.read(s['audio'],dtype='float32');assert sr==16000
  raw=a._transcribe(s['audio']);save('raw.json',raw)
  turns=json.loads(Path(s['native_result']).read_text())['turns']
  crop=a._transcribe_turns(samples,turns) if len(turns)>1 else a._transcribe(s['audio']) if turns else {'transcription':[],'result':{'language':None}}
  save('cropped.json',crop);save('structured.json',dict(segments=align_tokens(crop['transcription'],turns,{},seconds,5.),native_turns=turns,embeddings_computed=False,stable_identity_evaluated=False))
 else:raise ValueError('baseline phase required')
if __name__=='__main__':main(*sys.argv[1:])
