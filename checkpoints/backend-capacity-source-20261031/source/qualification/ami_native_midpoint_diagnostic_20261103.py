"""Native anonymous-activity coverage on fixed labeled clips, not DER or identity.

Oracle mapping uses the same clip's labels and is optimistically fitted. Reference
word times are forced-aligned. No thresholds, backend settings or models change.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import sys
import tempfile
import time
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SPEC=importlib.util.spec_from_file_location('ami_labels',ROOT/'qualification/ami_labeled_context_diagnostic_20261103.py')
labels=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(labels)


def midpoint_coverage(reference,turns,low_share):
    speakers=sorted({r['speaker'] for r in reference});clusters=sorted({t['speaker_local_id'] for t in turns})
    if len(speakers)>4 or len(clusters)>8:raise ValueError('mapping_capacity')
    active=[{t['speaker_local_id'] for t in turns if t['start']<=(r['start']+r['end'])/2<t['end']} for r in reference]
    counts=Counter(r['speaker'] for r in reference)
    matrix={(s,c):sum(r['speaker']==s and c in a for r,a in zip(reference,active)) for s in speakers for c in clusters}
    # Dummy entries allow an unmatched reference speaker, never many-to-one fit.
    choices=clusters+[f'unmatched_{i}' for i in range(max(0,len(speakers)-len(clusters)))]
    best=None;best_score=-1
    for selected in itertools.permutations(choices,len(speakers)):
        score=sum(matrix.get((s,c),0) for s,c in zip(speakers,selected))
        if score>best_score:best_score=score;best=dict(zip(speakers,selected))
    result={}
    for speaker in speakers:
        subset=[a for r,a in zip(reference,active) if r['speaker']==speaker]
        mapped=best[speaker] if best else None
        result[speaker]={'words':counts[speaker],'any_activity':sum(bool(a) for a in subset),
            'oracle_one_to_one_covered':sum(mapped in a for a in subset),
            'best_single_cluster_covered':max((matrix[(speaker,c)] for c in clusters),default=0),
            'multiple_active_clusters':sum(len(a)>1 for a in subset),'mapped_cluster':mapped}
    pooled={key:sum(result[s][key] for s in low_share) for key in ['words','any_activity','oracle_one_to_one_covered','best_single_cluster_covered','multiple_active_clusters']}
    return {'speaker_count':len(speakers),'native_cluster_count':len(clusters),'native_turn_count':len(turns),
        'reference_speakers':result,'low_share_reference_words':pooled,'oracle_fit_scope':'same_clip_labels; optimistic one-to-one mapping, not recognized identity'}


def main():
    import psutil
    from audio_backend.inference.native_diarization import NativeDiarizer,MODEL_SHA256
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['asset-root','ami-root','annotations','output','frozen-plan']:parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();args.output.mkdir(mode=0o700,exist_ok=False)
    if labels.sha(args.annotations)!=labels.ZIP_SHA:raise ValueError('annotations_hash')
    words,inventory,exclusions=labels.references(args.annotations);clips,refs=labels.plan(words)
    frozen=json.loads(args.frozen_plan.read_text())
    if clips!=frozen['clips'] or inventory!=frozen['members']:raise ValueError('frozen_plan_mismatch')
    for meeting,digest in labels.AUDIO_SHA.items():
        if labels.sha(args.ami_root/(meeting+'.Mix-Headset.wav'))!=digest:raise ValueError('audio_hash')
    model=args.asset_root/'models/native-diarization/Nemotron-3-Diarization.q8_0.gguf'
    if labels.sha(model)!=MODEL_SHA256:raise ValueError('native_model_hash')
    library=args.asset_root/'expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_asr_c.so'
    inventory=next(r['files'] for r in json.loads((args.asset_root/'expanded-receipt.json').read_text()) if r['archive'].endswith('nemo-speech-0.2.0-linux-x86_64-cpu.tar.gz'))
    for item in inventory:
        p=args.asset_root/item['path']
        if p.stat().st_size!=item['bytes'] or labels.sha(p)!=item['sha256']:raise ValueError('native_runtime_hash')
    if psutil.virtual_memory().available<1536*1024**2:raise RuntimeError('host_memory_reserve')
    config={'native_diarization_model':str(model),'native_diarization_library':str(library),'native_diarization_epoch_max_seconds':30}
    started=time.monotonic();provider=NativeDiarizer(config);results=[]
    try:
        provenance=provider.provenance()
        with tempfile.TemporaryDirectory(prefix='ami-native-') as tmp:
            for index,clip in enumerate(clips):
                if time.monotonic()-started>180 or psutil.virtual_memory().available<1536*1024**2:raise RuntimeError('resource_guard')
                path=Path(tmp)/'clip.wav'
                with wave.open(str(args.ami_root/(clip['meeting']+'.Mix-Headset.wav')),'rb') as source:
                    source.setpos(clip['start_frame']);pcm=source.readframes(clip['frames'])
                if len(pcm)!=clip['frames']*2:raise ValueError('clip_length')
                with wave.open(str(path),'wb') as out:
                    out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(pcm)
                provider.reset('public-label-'+str(index));provider.push(path);state=provider.finish()
                turns=provider.turns(0.,26.25)
                local=[dict(r,start=r['start']-clip['start_frame']/16000,end=r['end']-clip['start_frame']/16000) for r in refs[clip['key']]]
                results.append({'key':clip['key'],'state':state,'coverage':midpoint_coverage(local,turns,clip['low_share_speakers'])})
    finally:provider.close()
    report={'scope':'isolated native finalized clips; reference-word midpoint activity, not full DER or persistent identity',
        'frozen_plan_sha256':labels.sha(args.frozen_plan),'annotation_archive_sha256':labels.ZIP_SHA,'audio_sha256':labels.AUDIO_SHA,
        'source_sha256':{str(p.relative_to(ROOT)):labels.sha(p) for p in [Path(__file__),ROOT/'qualification/ami_labeled_context_diagnostic_20261103.py',ROOT/'audio_backend/inference/native_diarization.py']},
        'native_provenance':provenance,'closed':provider._closed,'elapsed_seconds':time.monotonic()-started,'rows':results}
    (args.output/'SUMMARY.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'clips':len(results),'closed':provider._closed,'summary_sha256':labels.sha(args.output/'SUMMARY.json')}))

if __name__=='__main__':main()
