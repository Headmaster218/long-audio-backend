"""Frozen English-language control on two fresh AMI overlap clips, not promotion.

Reference timings are forced aligned, lexical references human-reviewed. Outputs
contain counts and hashes only. Same related-session speakers are not held out.
"""
import argparse
from collections import Counter
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
labels=module('ami_labels',ROOT/'qualification/ami_labeled_context_diagnostic_20261103.py')
CLIPS=[('EN2002a',350),('EN2002b',400)]
FRAMES=336000
ARMS=('auto','en')

def make_plan(words):
    clips=[];refs={}
    for meeting,start in CLIPS:
        end=start+FRAMES/16000
        if any(r['start']<start<r['end'] or r['start']<end<r['end'] for r in words[meeting]):raise ValueError('outer_reference_cut')
        rows=[dict(r) for r in words[meeting] if start<=(r['start']+r['end'])/2<end]
        counts=Counter(r['speaker'] for r in rows)
        low=sorted(s for s,n in counts.items() if n>=10 and .1<=n/len(rows)<=.3)
        if not low:raise ValueError('low_share_denominator')
        for row in rows:
            row['overlap']=any(x['speaker']!=row['speaker'] and min(x['end'],row['end'])>max(x['start'],row['start']) for x in rows)
        if sum(r['overlap'] for r in rows)<10:raise ValueError('overlap_denominator')
        key=f'{meeting}@{start}';refs[key]=rows
        clips.append({'key':key,'meeting':meeting,'start_frame':start*16000,'frames':FRAMES,'reference_words':len(rows),
            'speaker_word_counts':dict(counts),'low_share_speakers':low,'overlap_reference_words':sum(r['overlap'] for r in rows)})
    return clips,refs

def score(rows,text,low):
    hypothesis=labels.tokenize(text);counts,ops=labels.align([r['text'] for r in rows],hypothesis)
    errors=sum(counts.get(k,0) for k in ['S','D','I'])
    return {'reference_words':len(rows),'hypothesis_words':len(hypothesis),'counts':counts,'errors':errors,
        'hypothesis_sha256':hashlib.sha256(json.dumps(hypothesis).encode()).hexdigest(),
        'low_share_reference_words':labels.stratum(rows,ops,lambda r:r['speaker'] in low),
        'overlap_reference_words':labels.stratum(rows,ops,lambda r:r['overlap']),
        'nonoverlap_reference_words':labels.stratum(rows,ops,lambda r:not r['overlap'])}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['asset-root','ami-root','annotations','output']:parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--plan-only',action='store_true');args=parser.parse_args()
    if labels.sha(args.annotations)!=labels.ZIP_SHA:raise ValueError('annotation_hash')
    args.output.mkdir(mode=0o700,exist_ok=False)
    words,inventory,_=labels.references(args.annotations);clips,refs=make_plan(words)
    frozen={'clips':clips,'annotation_sha256':labels.ZIP_SHA,'members':inventory,'arms':ARMS,'core_frames':84000,'context_frames':0,
        'selection':'first qualifying integer start in [300,500) per meeting; >=10 overlap words; low-share >=10 words and 10-30%; no outer word cuts. [800,1000) also searched, no qualifying clips.',
        'normalization':'fixed ami_labeled_context_diagnostic tokenizer/scorer','source_sha256':labels.sha(__file__),
        'default_change_allowed':False,'retry_or_additional_clip_selection_allowed':False}
    (args.output/'PLAN.json').write_text(json.dumps(frozen,indent=2)+'\n')
    if args.plan_only:print(json.dumps({'clips':clips,'sha256':labels.sha(args.output/'PLAN.json')}));return
    import numpy as np
    import soundfile as sf
    import sherpa_onnx
    ctx=module('context',ROOT/'qualification/sensevoice_context_diagnostic_20261103.py')
    modelroot=args.asset_root/'expanded/sensevoice/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
    for name in ['model.int8.onnx','tokens.txt']:
        if labels.sha(modelroot/name)!=ctx.PINS[name]:raise ValueError('model_hash')
    for meeting,digest in labels.AUDIO_SHA.items():
        if labels.sha(args.ami_root/(meeting+'.Mix-Headset.wav'))!=digest:raise ValueError('audio_hash')
    results=[];calls=0;started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='ami-language-') as temp:
        wav=Path(temp)/'window.wav'
        for language in ARMS:
            # The recognizer caches its language at construction. Never mutate a
            # live recognizer's config and pretend the language has changed.
            analyzer=ctx.AudioAnalyzer.__new__(ctx.AudioAnalyzer);analyzer.config={'language':language};analyzer.threads=2;analyzer._sensevoice=None
            analyzer.paths={'asr':modelroot/'model.int8.onnx','tokens':modelroot/'tokens.txt'}
            for clip in clips:
                with wave.open(str(args.ami_root/(clip['meeting']+'.Mix-Headset.wav')),'rb') as w:
                    if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(1,2,16000):raise ValueError('audio_format')
                    w.setpos(clip['start_frame']);pcm=w.readframes(FRAMES)
                if len(pcm)!=FRAMES*2:raise ValueError('clip_length')
                audio=np.frombuffer(pcm,dtype='<i2').astype('float32')/32768;output=[];windows=[];previous=None
                for start in range(0,FRAMES,84000):
                    if time.monotonic()-started>180:raise RuntimeError('wall_time_bound')
                    end=start+84000;sf.write(wav,audio[start:end],16000,subtype='PCM_16');calls+=1
                    result=analyzer._transcribe_sensevoice(wav)
                    segments=ctx.align_tokens(result['transcription'],[{'start':0.,'end':5.25,'speaker_local_id':'control'}],{},5.25)
                    owned,metadata=ctx.own_window(segments,start,end,start,previous);previous=metadata.get('boundary_tail');intervals=[]
                    for segment in owned:
                        a=segment['start']+start/16000;b=segment['end']+start/16000
                        if not start/16000-1e-8<=a<b<=end/16000+1e-8:raise ValueError('source_timing_bound')
                        intervals.append([a,b]);output.append(segment['text'])
                    windows.append({'source_frames':[clip['start_frame']+start,clip['start_frame']+end],'clip_frames':[start,end],
                        'pcm_sha256':hashlib.sha256(pcm[start*2:end*2]).hexdigest(),'detected_language':result['result']['language'],'owned_clip_intervals_seconds':intervals})
                results.append({'key':clip['key'],'arm':language,'windows':windows,**score(refs[clip['key']],' '.join(output),clip['low_share_speakers'])})
            del analyzer;gc.collect()
    paths=[Path(__file__),ROOT/'qualification/ami_labeled_context_diagnostic_20261103.py',ROOT/'qualification/sensevoice_context_diagnostic_20261103.py',ROOT/'audio_backend/inference/analyzer.py',ROOT/'audio_backend/boundaries.py']
    report={'scope':'synthetic all-active ASR isolation; English corpus control, not identity or strict service qualification','plan_sha256':labels.sha(args.output/'PLAN.json'),
        'source_sha256':{str(p.relative_to(ROOT)):labels.sha(p) for p in paths},'model_sha256':{n:ctx.PINS[n] for n in ['model.int8.onnx','tokens.txt']},
        'audio_sha256':labels.AUDIO_SHA,'runtime_version':sherpa_onnx.__version__,'calls':calls,'elapsed_seconds':time.monotonic()-started,'rows':results}
    (args.output/'SUMMARY.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'calls':calls,'rows':len(results),'summary_sha256':labels.sha(args.output/'SUMMARY.json')}))
if __name__=='__main__':main()
