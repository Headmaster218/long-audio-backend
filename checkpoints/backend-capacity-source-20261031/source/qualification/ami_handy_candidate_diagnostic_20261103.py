"""Two fresh labeled clips: primary chunked/whole versus explicit Handy candidate.

No model promotion, service dispatch, native provenance attestation or identity
claim. Handy output completeness/EOS remain unknown. Raw job files stay local.
"""
import argparse
from collections import Counter
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import sys
import tempfile
import time
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

labels=module('ami_labels',ROOT/'qualification/ami_labeled_context_diagnostic_20261103.py')
CLIPS=[('EN2002a',1551),('EN2002b',565)]
FRAMES=252000

def make_plan(words):
    plan=[];refs={}
    for meeting,start in CLIPS:
        end=start+FRAMES/16000
        if any(r['start']<start<r['end'] or r['start']<end<r['end'] for r in words[meeting]):raise ValueError('outer_reference_cut')
        rows=[dict(r) for r in words[meeting] if start<=(r['start']+r['end'])/2<end]
        counts=Counter(r['speaker'] for r in rows);low=sorted(s for s,n in counts.items() if n>=10 and .1<=n/len(rows)<=.3)
        if not low:raise ValueError('low_share_denominator')
        key=f'{meeting}@{start}';refs[key]=rows
        plan.append({'key':key,'meeting':meeting,'start_frame':start*16000,'frames':FRAMES,
            'reference_words':len(rows),'speaker_word_counts':dict(counts),'low_share_speakers':low,
            'overlap_reference_words':sum(any(x['speaker']!=r['speaker'] and min(x['end'],r['end'])>max(x['start'],r['start']) for x in rows) for r in rows)})
    return plan,refs

def score(rows,text,low):
    hypothesis=labels.tokenize(text);counts,ops=labels.align([r['text'] for r in rows],hypothesis)
    errors=sum(counts.get(k,0) for k in ['S','D','I'])
    return {'reference_words':len(rows),'hypothesis_words':len(hypothesis),'counts':counts,'errors':errors,'wer':errors/len(rows),
        'hypothesis_sha256':hashlib.sha256(json.dumps(hypothesis).encode()).hexdigest(),
        'low_share_reference_words':labels.stratum(rows,ops,lambda r:r['speaker'] in low)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['asset-root','ami-root','annotations','handy-model','output']:parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--plan-only',action='store_true');args=parser.parse_args()
    args.output=args.output.resolve();args.output.mkdir(mode=0o700,exist_ok=False)
    if labels.sha(args.annotations)!=labels.ZIP_SHA:raise ValueError('annotation_hash')
    words,inventory,_=labels.references(args.annotations);clips,refs=make_plan(words)
    frozen={'clips':clips,'annotation_sha256':labels.ZIP_SHA,'members':inventory,'arms':['primary_chunked_zero_context','primary_whole_support','handy_manual_one_call'],
        'primary_language':'auto','primary_core_frames':84000,'normalization':'same fixed ami_labeled_context_diagnostic tokenizer/scorer',
        'source_sha256':labels.sha(__file__),'candidate_retry_allowed':False,'default_change_allowed':False}
    (args.output/'PLAN.json').write_text(json.dumps(frozen,indent=2)+'\n')
    if args.plan_only:print(json.dumps({'plan':frozen['clips'],'sha256':labels.sha(args.output/'PLAN.json')}));return
    import numpy as np
    import soundfile as sf
    from audio_backend.moss_handy_profile import NAME,verify_model,verify_runtime
    from audio_backend.moss_handy_controller import MossController
    from audio_backend.moss_handy_runner import samples_from_pcm,float_bytes
    from audio_backend.native_lexical_alternatives import SourceSupport
    verify_model(args.handy_model.resolve());chosen,runtime_paths,runtime_hashes=verify_runtime(NAME)
    for meeting,digest in labels.AUDIO_SHA.items():
        if labels.sha(args.ami_root/(meeting+'.Mix-Headset.wav'))!=digest:raise ValueError('audio_hash')
    ctx=module('context_diagnostic',ROOT/'qualification/sensevoice_context_diagnostic_20261103.py')
    modelroot=args.asset_root/'expanded/sensevoice/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
    for name in ['model.int8.onnx','tokens.txt']:
        if labels.sha(modelroot/name)!=ctx.PINS[name]:raise ValueError('primary_model_hash')
    analyzer=ctx.AudioAnalyzer.__new__(ctx.AudioAnalyzer);analyzer.config={'language':'auto'};analyzer.threads=2;analyzer._sensevoice=None
    analyzer.paths={'asr':modelroot/'model.int8.onnx','tokens':modelroot/'tokens.txt'}
    pcm_inputs=[];results=[];primary_calls=0;started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='handy-primary-') as tmp:
        wav=Path(tmp)/'window.wav'
        def decode(audio):
            nonlocal primary_calls
            sf.write(wav,audio,16000,subtype='PCM_16');primary_calls+=1
            value=analyzer._transcribe_sensevoice(wav)
            return ctx.align_tokens(value['transcription'],[{'start':0.,'end':len(audio)/16000,'speaker_local_id':'control'}],{},len(audio)/16000),value['result']['language']
        for clip in clips:
            with wave.open(str(args.ami_root/(clip['meeting']+'.Mix-Headset.wav')),'rb') as w:
                if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(1,2,16000):raise ValueError('audio_format')
                source_frames=w.getnframes();w.setpos(clip['start_frame']);pcm=w.readframes(FRAMES)
            if len(pcm)!=FRAMES*2:raise ValueError('clip_length')
            pcm_inputs.append((pcm,source_frames));audio=np.frombuffer(pcm,dtype='<i2').astype('float32')/32768
            for arm in ['primary_chunked_zero_context','primary_whole_support']:
                output=[];langs=[];previous=None;size=84000 if arm=='primary_chunked_zero_context' else FRAMES
                for start in range(0,FRAMES,size):
                    end=min(start+size,FRAMES);segments,language=decode(audio[start:end]);langs.append(language)
                    owned,metadata=ctx.own_window(segments,start,end,start,previous);previous=metadata.get('boundary_tail')
                    for segment in owned:
                        if not start/16000-1e-8<=segment['start']+start/16000<segment['end']+start/16000<=end/16000+1e-8:raise ValueError('primary_timing_bound')
                        output.append(segment['text'])
                results.append({'key':clip['key'],'arm':arm,'detected_languages':langs,**score(refs[clip['key']],' '.join(output),clip['low_share_speakers'])})
    del analyzer;gc.collect()
    stopped=False;current=None
    def cancel(*_):
        nonlocal stopped
        stopped=True
        if current is not None:current[0].cancel(current[1])
    for sig in [signal.SIGTERM,signal.SIGINT]:signal.signal(sig,cancel)
    attempts=[]
    for index,(clip,(pcm,source_frames)) in enumerate(zip(clips,pcm_inputs)):
        if stopped:break
        directory=args.output/f'owned-job-{index}';directory.mkdir(mode=0o700)
        # Diagnostic caller binding only. Runtime manifest hash is NOT proof of a
        # native source capture; no strict service provenance is fabricated.
        pcm_sha=hashlib.sha256(pcm).hexdigest()
        support=SourceSupport(labels.AUDIO_SHA[clip['meeting']],labels.sha(args.output/'PLAN.json'),labels.sha(ROOT/'native-runtime-manifest.json'),pcm_sha,
            source_frames,clip['start_frame'],clip['start_frame']+FRAMES,clip['start_frame'],clip['start_frame']+FRAMES,
            pcm_sha,hashlib.sha256(float_bytes(samples_from_pcm(pcm))).hexdigest())
        controller=MossController(directory,profile_name=NAME,model_path=str(args.handy_model.resolve()))
        job=controller.submit(support,pcm);current=(controller,job);status=controller.run(job);current=None
        # Sanitized scalar lifecycle receipt; raw/parsed transcript stays only in
        # controller-owned local scratch, never copied into this summary.
        observation={k:status.get(k) for k in ['state','invocation_possible','publication_valid','cleanup','error_type','native_receipt','receipt_validated','failure']}
        projection=status.get('result') or {};alternative=projection.get('alternative')
        observation['projection']={k:projection.get(k) for k in ['complete_content','eos','generated_token_count','schema_errors','truncated','aborted','raw_sha256','raw_bytes']}
        observation.update(key=clip['key'],support_id=support.support_id)
        attempts.append(observation)
        (args.output/f'ATTEMPT-{index}.json').write_text(json.dumps(observation,indent=2)+'\n')
        if status['state']!='returned_diagnostic' or not status['publication_valid'] or alternative is None:
            stopped=True;break
        results.append({'key':clip['key'],'arm':'handy_manual_one_call','complete_content':False,'eos':None,
            **score(refs[clip['key']],alternative['text'],clip['low_share_speakers'])})
    report={'scope':'two fresh related-session clips; manual candidate only; no promotion, identity or strict-service qualification',
        'plan_sha256':labels.sha(args.output/'PLAN.json'),'primary_calls':primary_calls,'handy_attempts':attempts,'stopped_early':stopped,
        'profile_sha256':chosen.sha256,'runtime_file_count':len(runtime_hashes),'runtime_hashes':runtime_hashes,'model_sha256':labels.sha(args.handy_model),
        'source_sha256':{str(p.relative_to(ROOT)):labels.sha(p) for p in [Path(__file__),ROOT/'audio_backend/moss_handy_controller.py',ROOT/'audio_backend/moss_handy_runner.py',ROOT/'qualification/ami_labeled_context_diagnostic_20261103.py',ROOT/'qualification/sensevoice_context_diagnostic_20261103.py',ROOT/'audio_backend/inference/analyzer.py',ROOT/'audio_backend/boundaries.py',ROOT/'audio_backend/moss_handy_adapter.py',ROOT/'audio_backend/moss_handy_profile.py']},
        'elapsed_seconds':time.monotonic()-started,'rows':results}
    (args.output/'SUMMARY.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'rows':len(results),'handy_attempts':len(attempts),'stopped_early':stopped,'summary_sha256':labels.sha(args.output/'SUMMARY.json')}))

if __name__=='__main__':main()
