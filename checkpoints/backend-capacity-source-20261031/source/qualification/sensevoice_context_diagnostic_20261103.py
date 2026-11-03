"""Standalone context/ownership experiment; no strict service or accuracy claim.

Writes hash/count evidence and optional local transcripts. Never promote settings
from whole-file agreement alone. Run with external CPU/memory/wall-time limits.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unicodedata
import wave

import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from audio_backend.inference.analyzer import AudioAnalyzer,align_tokens
from audio_backend.boundaries import lexical_core

PINS={
 'tokens.txt':'f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc',
 'model.int8.onnx':'c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51',
 'en.wav':'eb1eb008904465b74c304aad8342e8c7d3c6e61ffe9f66adcaca9cf0f76a93f4',
 'ja.wav':'460bd8dccb0d2a5f4e29c628f837be4082d13defc64c3fc21dd1b6bb0e119095',
 'yue.wav':'0960b2db54ae202071d250e6462fbf74a3c863f0e3e7f01273e4939c996875a0',
 'ko.wav':'0dc797a5c81ed30fc339d91f3da718ab02854e17ffa37cb93c4c039ac5c6bb9c',
 'zh.wav':'b77f1794fe374a0ba1ee1dc458bfaf9349496cbbfc32780c50ba3c5a7ad8e373'}

def digest(raw):return hashlib.sha256(raw).hexdigest()
def normalized(text):return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())
def distance(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current=[i]
        for j,y in enumerate(b,1):current.append(min(current[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=current
    return row[-1]


def own_window(segments,core_start,core_end,left,previous=None):
    """Use real ownership; reject nonempty un-tokenized output, never shift it."""
    result={'segments':segments,'metadata':{}}
    owned=lexical_core(result,(core_start-left)/16000,(core_end-left)/16000,previous,left/16000)
    if owned is None:
        if segments:
            raise ValueError('missing_lexical_ownership_evidence')
        owned=[]
    return owned,result['metadata']


def main():
    import sherpa_onnx
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset-root',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--ami-root',type=Path,help='Optional existing pinned public AMI recordings; adds four fixed longer crops')
    parser.add_argument('--local-transcripts',action='store_true',help='Local only; never upload this output')
    args=parser.parse_args();root=args.asset_root.resolve();out=args.output.resolve();out.mkdir(mode=0o700,exist_ok=False)
    entries=next(v['files'] for v in json.loads((root/'expanded-receipt.json').read_text()) if v['archive']=='models/sensevoice/model.tar.bz2')
    selected=[e for e in entries if Path(e['path']).name in PINS]
    assert len(selected)==len(PINS) and {Path(e['path']).name for e in selected}==set(PINS)
    paths={}
    for e in selected:
        name=Path(e['path']).name;p=(root/e['path']).resolve()
        assert p.is_relative_to(root) and p.stat().st_size==e['bytes'] and digest(p.read_bytes())==e['sha256']==PINS[name]
        paths[name]=p
    # Construct only the real adapter; synthetic all-active turns isolate ASR and
    # ownership from diarization. No speaker recognition or service qualification.
    analyzer=AudioAnalyzer.__new__(AudioAnalyzer)
    analyzer.config={'language':'auto'};analyzer.threads=2;analyzer._sensevoice=None
    analyzer.paths={'asr':paths['model.int8.onnx'],'tokens':paths['tokens.txt']}
    clips=[]
    for name in ['zh','en','ja','ko','yue']:
        with wave.open(str(paths[name+'.wav']),'rb') as w:
            assert (w.getnchannels(),w.getsampwidth(),w.getframerate())==(1,2,16000)
            assert 0<w.getnframes()<=480000
            audio=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype('float32')/32768
        clips.append((name,audio,PINS[name+'.wav'],[0,len(audio)],[0,8000,32000]))
    ami_pins={'EN2002a.Mix-Headset.wav':'a15017d0d3c871866971ee178052a0ce3a7e44d6fbf89fc23dcab326511b65f5','EN2002b.Mix-Headset.wav':'50e2c8e4737850c4bf3cc24e7087c755e1c733cd91822106afd0df485ba64893'}
    if args.ami_root is not None:
        for name,sha in ami_pins.items():
            path=args.ami_root/name
            assert digest(path.read_bytes())==sha
            for offset in [60*16000,180*16000]:
                with wave.open(str(path),'rb') as w:
                    assert (w.getnchannels(),w.getsampwidth(),w.getframerate())==(1,2,16000)
                    w.setpos(offset);audio=np.frombuffer(w.readframes(468000),dtype='<i2').astype('float32')/32768
                    assert len(audio)==468000
                clips.append((name+'@'+str(offset//16000),audio,sha,[offset,offset+len(audio)],[0]))
    start=time.monotonic();rows=[];raw=[];calls=0
    with tempfile.TemporaryDirectory(prefix='speech-context-') as tmp:
        wav=Path(tmp)/'window.wav'
        def decode(audio):
            nonlocal calls
            assert time.monotonic()-start<300
            sf.write(wav,audio,16000,subtype='PCM_16');calls+=1
            result=analyzer._transcribe_sensevoice(wav)
            return align_tokens(result['transcription'],[{'start':0.,'end':len(audio)/16000,'speaker_local_id':'control'}],{},len(audio)/16000),result['result']['language']
        # Frozen design: five original recordings, three prefix placements,
        # existing zero or two-second symmetric context, core fixed at 5.25s.
        for name,original,input_sha,source_crop,prefixes in clips:
            for prefix in prefixes:
                audio=np.concatenate([np.zeros(prefix,dtype='float32'),original]);frames=len(audio)
                whole_segments,whole_language=decode(audio)
                reference=''.join(s['text'] for s in whole_segments);ref=normalized(reference)
                for context in [0,32000]:
                    previous=None;outputs=[];windows=[]
                    for core_start in range(0,frames,84000):
                        core_end=min(core_start+84000,frames);left=max(0,core_start-context);right=min(frames,core_end+context)
                        samples=audio[left:right];segments,detected_language=decode(samples)
                        owned,metadata=own_window(segments,core_start,core_end,left,previous)
                        previous=metadata.get('boundary_tail')
                        timings=[]
                        for segment in owned:
                            a=segment['start']+core_start/16000;b=segment['end']+core_start/16000
                            assert left/16000-1e-8<=a<b<=right/16000+1e-8
                            timings.append([a,b]);outputs.append(segment['text'])
                        windows.append({'core_frames':[core_start,core_end],'decode_source_frames':[left,right],
                            'float32_pcm_sha256':digest(samples.astype('<f4').tobytes()),'detected_language':detected_language,'owned_intervals_seconds':timings,
                            'alignment_used':metadata.get('boundary_alignment_used',False)})
                    text=''.join(outputs);norm=normalized(text)
                    row={'sample':name,'input_sha256':input_sha,'source_crop_frames':source_crop,'prefix_silence_frames':prefix,'frames':frames,'whole_detected_language':whole_language,
                        'context_frames':context,'core_frames':84000,'reference_characters':len(ref),
                        'output_characters':len(norm),'reference_sha256':digest(ref.encode()),'output_sha256':digest(norm.encode()),
                        'distance_to_whole_decode':distance(ref,norm),'windows':windows}
                    rows.append(row);raw.append({'sample':name,'prefix':prefix,'context':context,'whole':reference,'chunked':text})
    summary={'schema':'sensevoice-context-diagnostic-v1','scope':'standalone real adapter, synthetic all-active turns; whole-decode agreement is not accuracy',
        'original_recordings':7 if args.ami_root else 5,'public_release_recordings':5,'placements_per_release_recording':3,'ami_crops':4 if args.ami_root else 0,'settings':{'language':'auto','core_frames':84000,'contexts_frames':[0,32000]},
        'pins':PINS| (ami_pins if args.ami_root else {}),'source_sha256':{str(p.relative_to(ROOT)):digest(p.read_bytes()) for p in [Path(__file__).resolve(),ROOT/'audio_backend/inference/analyzer.py',ROOT/'audio_backend/boundaries.py']},
        'runtime_version':sherpa_onnx.__version__,'decode_calls':calls,'elapsed_seconds':time.monotonic()-start,'rows':rows}
    (out/'SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
    if args.local_transcripts:(out/'LOCAL_TRANSCRIPTS_DO_NOT_UPLOAD.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'rows':len(rows),'decode_calls':calls,'summary_sha256':digest((out/'SUMMARY.json').read_bytes())}))

if __name__=='__main__':main()
