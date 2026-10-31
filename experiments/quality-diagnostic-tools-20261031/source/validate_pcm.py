"""Model-free comparison of a local WAV prefix with independently sourced WAV.
Does not download anything or write/print audio/transcripts.
"""
import argparse, hashlib, json, wave
from pathlib import Path

def compare(a, b, frames):
    if type(frames) is not int or not 0 < frames <= 16_000 * 120:
        raise ValueError('frames must describe at most 120 seconds at 16 kHz')
    rows=[]; pcm=[]
    for path in (a,b):
        with wave.open(str(path),'rb') as f:
            if (f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getcomptype()) != (1,2,16000,'NONE'):
                raise ValueError('Expected uncompressed mono PCM16 16 kHz')
            data=f.readframes(frames)
        if len(data)!=frames*2: raise ValueError('Insufficient prefix data')
        pcm.append(data); rows.append(hashlib.sha256(data).hexdigest())
    return dict(frames=frames,pcm_bytes=frames*2,sha256=rows,exact_pcm_match=pcm[0]==pcm[1],conversion='none')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('local');p.add_argument('official_prefix');p.add_argument('--frames',type=int,required=True);a=p.parse_args()
    result=compare(Path(a.local),Path(a.official_prefix),a.frames);print(json.dumps(result));raise SystemExit(0 if result['exact_pcm_match'] else 1)
