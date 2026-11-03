"""Small paired AMI human-reference experiment; hashes/counts only, no defaults.

References are human transcripts with forced-aligned timestamps. Low-share word
accuracy is NOT speaker identification. Requires existing pinned public assets.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import time
import unicodedata
import wave
import xml.etree.ElementTree as ET
import zipfile

ZIP_SHA='56e29112cd89cfba76fbf8f10215eb245cc89b0d3d27355ba14b406a7ea7776c'
AUDIO_SHA={'EN2002a':'a15017d0d3c871866971ee178052a0ce3a7e44d6fbf89fc23dcab326511b65f5',
           'EN2002b':'50e2c8e4737850c4bf3cc24e7087c755e1c733cd91822106afd0df485ba64893'}
# Label-only frozen selection, before inspecting model outputs. No overlap with
# previously exposed 60/180s crops. Exactly five 5.25s cores per clip.
CLIPS=[('EN2002a',1260),('EN2002a',1667),('EN2002b',1078),('EN2002b',1119)]
ROOT=Path(__file__).resolve().parents[1]

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def tokenize(text):
    text=unicodedata.normalize('NFKC',text).casefold().replace('’',"'")
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)*",text)

def parse_words(raw,speaker):
    result=[];excluded=Counter()
    for index,word in enumerate(ET.fromstring(raw)):
        if word.tag!='w' or word.get('punc')=='true':
            excluded[word.tag if word.tag!='w' else 'punctuation']+=1;continue
        tokens=tokenize(word.text or '')
        if not tokens:excluded['empty_normalized_word']+=1;continue
        start=float(word.attrib['starttime']);end=float(word.attrib['endtime'])
        if not 0<=start<=end:raise ValueError('invalid_reference_interval')
        for token in tokens:result.append({'text':token,'speaker':speaker,'start':start,'end':end,'index':index})
    return result,dict(excluded)

def align(reference,hypothesis):
    """Unit-cost Levenshtein: diagonal, deletion, insertion tie priority."""
    n=len(reference);m=len(hypothesis)
    if max(n,m)>4096:raise ValueError('word_count_bound')
    costs=[list(range(m+1))];back=[[None]+['I']*m]
    for i,x in enumerate(reference,1):
        row=[i];steps=['D']
        for j,y in enumerate(hypothesis,1):
            choices=[(costs[-1][j-1]+(x!=y),'C' if x==y else 'S'),(costs[-1][j]+1,'D'),(row[-1]+1,'I')]
            score,op=min(choices,key=lambda item:item[0]);row.append(score);steps.append(op)
        costs.append(row);back.append(steps)
    i=n;j=m;labels=['']*n;insertions=0
    while i or j:
        op=back[i][j]
        if op in {'C','S'}:labels[i-1]=op;i-=1;j-=1
        elif op=='D':labels[i-1]='D';i-=1
        else:insertions+=1;j-=1
    counts=Counter(labels);counts['I']=insertions
    return dict(counts),labels

def stratum(rows,labels,select):
    counts=Counter(op for row,op in zip(rows,labels) if select(row))
    n=sum(counts.values())
    return {'words':n,'correct':counts['C'],'substitutions':counts['S'],'deletions':counts['D'],
            'correct_fraction':counts['C']/n if n else None}

def references(archive):
    words={};inventory=[];exclusions={}
    with zipfile.ZipFile(archive) as z:
        for meeting in AUDIO_SHA:
            rows=[]
            for speaker in 'ABCD':
                name=f'words/{meeting}.{speaker}.words.xml';info=z.getinfo(name)
                if info.file_size>2*1024**2:raise ValueError('annotation_member_bound')
                raw=z.read(name);items,excluded=parse_words(raw,speaker);rows.extend(items)
                inventory.append({'member':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()});exclusions[name]=excluded
            rows.sort(key=lambda r:(r['start'],r['speaker'],r['index']))
            words[meeting]=rows
    return words,inventory,exclusions

def plan(words):
    clips=[];refs={}
    for meeting,start in CLIPS:
        end=start+26.25
        rows=[dict(r) for r in words[meeting] if start<=(r['start']+r['end'])/2<end]
        if any(r['start']<start<r['end'] or r['start']<end<r['end'] for r in words[meeting]):raise ValueError('outer_boundary_cuts_reference_word')
        counts=Counter(r['speaker'] for r in rows)
        low=sorted(s for s,n in counts.items() if n>=10 and .1<=n/len(rows)<=.3)
        if not low:raise ValueError('no_low_share_denominator')
        for row in rows:
            row['overlap']=any(r['speaker']!=row['speaker'] and min(r['end'],row['end'])>max(r['start'],row['start']) for r in rows)
            row['boundary_midpoint']=any(abs((row['start']+row['end'])/2-(start+k*5.25))<=.5 for k in range(1,5))
            row['boundary_start']=any(abs(row['start']-(start+k*5.25))<=.5 for k in range(1,5))
        key=f'{meeting}@{start}';refs[key]=rows
        clips.append({'key':key,'meeting':meeting,'start_frame':start*16000,'frames':420000,'reference_words':len(rows),
            'speaker_word_counts':dict(counts),'low_share_speakers':low,'overlap_words':sum(r['overlap'] for r in rows),
            'boundary_midpoint_words':sum(r['boundary_midpoint'] for r in rows),'boundary_start_words':sum(r['boundary_start'] for r in rows),
            'reference_intervals_over_one_second':sum(r['end']-r['start']>1 for r in rows),'outer_cut_words':0})
    return clips,refs

def main():
    import numpy as np
    import soundfile as sf
    import sherpa_onnx
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset-root',required=True,type=Path);parser.add_argument('--ami-root',required=True,type=Path)
    parser.add_argument('--annotations',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--plan-only',action='store_true');args=parser.parse_args()
    if sha(args.annotations)!=ZIP_SHA:raise ValueError('annotation_archive_hash')
    args.output.mkdir(mode=0o700,exist_ok=False)
    words,inventory,exclusions=references(args.annotations);clips,refs=plan(words)
    frozen={'clips':clips,'annotation_archive_sha256':ZIP_SHA,'members':inventory,'excluded_annotation_elements':exclusions,
        'normalization':'NFKC casefold; curly apostrophe to ASCII; regex ASCII letters/digits with internal apostrophe; fillers retained; no number expansion',
        'metrics':'time-start sorted reference WER, diagonal/deletion/insertion tie order; reference-word strata; no speaker insertions',
        'reference_membership':'midpoint half-open; all selected outer boundaries cut no reference words',
        'context_frames':[0,32000],'core_frames':84000,'language':'auto','source_sha256':sha(__file__)}
    (args.output/'PLAN.json').write_text(json.dumps(frozen,indent=2)+'\n')
    if args.plan_only:print(json.dumps({'clips':clips,'plan_sha256':sha(args.output/'PLAN.json')}));return
    for meeting,digest in AUDIO_SHA.items():
        if sha(args.ami_root/(meeting+'.Mix-Headset.wav'))!=digest:raise ValueError('audio_hash')
    path=ROOT/'qualification/sensevoice_context_diagnostic_20261103.py'
    spec=importlib.util.spec_from_file_location('context_diagnostic',path);ctx=importlib.util.module_from_spec(spec);spec.loader.exec_module(ctx)
    modelroot=args.asset_root/'expanded/sensevoice/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
    for name in ['model.int8.onnx','tokens.txt']:
        if sha(modelroot/name)!=ctx.PINS[name]:raise ValueError('model_hash')
    analyzer=ctx.AudioAnalyzer.__new__(ctx.AudioAnalyzer);analyzer.config={'language':'auto'};analyzer.threads=2;analyzer._sensevoice=None
    analyzer.paths={'asr':modelroot/'model.int8.onnx','tokens':modelroot/'tokens.txt'}
    results=[];calls=0;started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='ami-labeled-') as tmp:
        wav=Path(tmp)/'window.wav'
        def decode(audio):
            nonlocal calls
            if time.monotonic()-started>240:raise RuntimeError('wall_time_bound')
            sf.write(wav,audio,16000,subtype='PCM_16');calls+=1;result=analyzer._transcribe_sensevoice(wav)
            segments=ctx.align_tokens(result['transcription'],[{'start':0.,'end':len(audio)/16000,'speaker_local_id':'control'}],{},len(audio)/16000)
            return segments,result['result']['language']
        for clip in clips:
            with wave.open(str(args.ami_root/(clip['meeting']+'.Mix-Headset.wav')),'rb') as w:
                if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(1,2,16000):raise ValueError('audio_format')
                w.setpos(clip['start_frame']);audio=np.frombuffer(w.readframes(clip['frames']),dtype='<i2').astype('float32')/32768
            if len(audio)!=clip['frames']:raise ValueError('audio_length')
            for context in [0,32000]:
                previous=None;output=[];windows=[]
                for start in range(0,len(audio),84000):
                    end=start+84000;left=max(0,start-context);right=min(len(audio),end+context)
                    segments,language=decode(audio[left:right]);owned,metadata=ctx.own_window(segments,start,end,left,previous)
                    previous=metadata.get('boundary_tail');intervals=[]
                    for segment in owned:
                        a=segment['start']+start/16000;b=segment['end']+start/16000
                        if not left/16000-1e-8<=a<b<=right/16000+1e-8:raise ValueError('source_timing_bound')
                        intervals.append([a,b]);output.append(segment['text'])
                    windows.append({'core_frames':[start,end],'decode_frames':[left,right],'detected_language':language,'owned_intervals_seconds':intervals})
                hypothesis=tokenize(' '.join(output));reference=refs[clip['key']];counts,labels=align([r['text'] for r in reference],hypothesis)
                errors=sum(counts.get(k,0) for k in ['S','D','I']);n=len(reference)
                results.append({'key':clip['key'],'context_frames':context,'reference_words':n,'hypothesis_words':len(hypothesis),
                    'counts':counts,'errors':errors,'wer':errors/n,'hypothesis_sha256':hashlib.sha256(json.dumps(hypothesis).encode()).hexdigest(),
                    'per_reference_speaker':{s:stratum(reference,labels,lambda r,s=s:r['speaker']==s) for s in sorted(clip['speaker_word_counts'])},
                    'low_share_reference_words':stratum(reference,labels,lambda r:r['speaker'] in clip['low_share_speakers']),
                    'overlap_reference_words':stratum(reference,labels,lambda r:r['overlap']),
                    'nonoverlap_reference_words':stratum(reference,labels,lambda r:not r['overlap']),
                    'boundary_midpoint_reference_words':stratum(reference,labels,lambda r:r['boundary_midpoint']),
                    'boundary_start_reference_words':stratum(reference,labels,lambda r:r['boundary_start']),'windows':windows})
    report={'scope':'human-reference lexical diagnostic with synthetic all-active turns, not speaker identity or strict service qualification',
        'plan_sha256':sha(args.output/'PLAN.json'),'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),path,ROOT/'audio_backend/inference/analyzer.py',ROOT/'audio_backend/boundaries.py']},
        'model_sha256':{n:ctx.PINS[n] for n in ['model.int8.onnx','tokens.txt']},'audio_sha256':AUDIO_SHA,'runtime_version':sherpa_onnx.__version__,
        'calls':calls,'elapsed_seconds':time.monotonic()-started,'rows':results}
    (args.output/'SUMMARY.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'calls':calls,'rows':len(results),'summary_sha256':sha(args.output/'SUMMARY.json')}))

if __name__=='__main__':main()
