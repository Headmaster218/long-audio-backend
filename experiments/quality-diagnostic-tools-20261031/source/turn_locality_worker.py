"""One raw SenseVoice decode; no gold, diarization or output reassignment."""
import hashlib,json,sys,wave
from pathlib import Path

def main(spec_path,output):
 s=json.loads(Path(spec_path).read_text());out=Path(output)
 with wave.open(s['audio']) as f:
  assert (f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getnframes())==(1,2,16000,s['frames'])
  pcm=f.readframes(s['frames'])
 assert hashlib.sha256(pcm).hexdigest()==s['pcm_sha256']
 cfg=json.loads(Path(s['config']).read_text());assert cfg['language']=='auto' and cfg['cpu_threads']==2
 sys.path.insert(0,s['repo'])
 from audio_backend.inference.analyzer import AudioAnalyzer
 a=AudioAnalyzer(cfg)
 try:
  raw=a._transcribe(s['audio'])
  (out/'raw.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
 finally:a.close()
 (out/'receipt.json').write_text(json.dumps(dict(attempts=1,analyzer_closed=True),indent=2))
if __name__=='__main__':main(*sys.argv[1:])
