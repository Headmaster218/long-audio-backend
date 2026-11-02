"""One raw SenseVoice decode; no gold, diarization or output reassignment."""
import hashlib,json,sys
from pathlib import Path

def main(spec_path,output):
 s=json.loads(Path(spec_path).read_text());out=Path(output)
 import soundfile as sf
 info=sf.info(s['audio']);assert (info.channels,info.samplerate,info.frames,info.subtype)==(1,16000,s['frames'],'FLOAT')
 assert hashlib.sha256(Path(s['audio']).read_bytes()).hexdigest()==s['audio_sha256']
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
