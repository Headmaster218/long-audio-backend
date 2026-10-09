from __future__ import annotations
import math

LABELS=('rain','driving','subway','door','music','silence')
# Defaults remain compatibility values until a calibration profile is explicitly selected.
DEFAULT_THRESHOLDS={label:.25 for label in LABELS if label!='silence'}

def category(label):
    l=label.lower()
    if l in {'rain','raindrop','rain on surface','thunderstorm'}: return 'rain'
    if l=='subway, metro, underground': return 'subway'
    if l in {'vehicle','car','engine','idling','accelerating, revving, vroom','traffic noise, roadway noise','heavy engine (low frequency)','medium engine (mid frequency)','light engine (high frequency)'}: return 'driving'
    # Doorbell is not a door movement. A Knock/Slam only supports a broad door-like event.
    if l in {'door','sliding door','slam','knock','cupboard open or close'}: return 'door'
    if l=='music' or l.endswith(' music'): return 'music'
    return None

def window_starts(length, window=80000, hop=40000):
    """Cover every sample without redundant tiny trailing windows; pad short recordings."""
    if length<=0: return []
    starts=[0]
    while starts[-1]+window<length: starts.append(starts[-1]+hop)
    return starts

def postprocess(details, thresholds=None, *, max_seconds=5., release_ratio=1.):
    """Merge only touching evidence windows. Hysteresis never requires multiple hits.

    Independent classes can overlap (speech is deliberately not an input). Release
    ratio defaults to 1; lower values must be explicitly evaluated before use.
    """
    thresholds=dict(DEFAULT_THRESHOLDS if thresholds is None else thresholds)
    if max_seconds<=0 or not 0<release_ratio<=1: raise ValueError('invalid event policy')
    for label,t in thresholds.items():
        if label not in LABELS or not math.isfinite(t) or not 0<=t<=1: raise ValueError('invalid threshold')
    merged=[]
    for label in LABELS:
        active=None
        for w in sorted(details,key=lambda x:x['start']):
            item=w.get('category_scores',{}).get(label)
            score=float(item['score']) if item else 0.
            threshold=thresholds.get(label,.25)
            if label=='silence': threshold=1.
            touching=active is not None and w['start']<=active['end']
            accepted=item and score>=threshold*(release_ratio if touching else 1.)
            if not accepted:
                if active is not None and w['start']>=active['end']: merged.append(active);active=None
                continue
            if touching:
                active['end']=max(active['end'],w['end'])
                if score>active['score']: active.update(score=score,source_label=item['source_label'])
            else:
                if active is not None: merged.append(active)
                active=dict(start=w['start'],end=w['end'],label=label,score=score,source_label=item['source_label'],time_precision='window_support',onset_precision_seconds=None)
        if active is not None: merged.append(active)
    bounded=[]
    for e in merged:
        st=e['start']
        while st<e['end']:
            end=min(e['end'],st+max_seconds);bounded.append(dict(e,start=st,end=end));st=end
    return sorted(bounded,key=lambda x:(x['start'],x['label']))

def configured_thresholds(config):
    thresholds=dict(DEFAULT_THRESHOLDS)
    if 'event_threshold' in config: thresholds={k:float(config['event_threshold']) for k in thresholds}
    thresholds.update(config.get('event_thresholds',{}))
    return thresholds

def detect_events(samples, model_path, labels_path, threads=2, config=None, *, tagger=None):
    import numpy as np
    config=config or {};a=np.asarray(samples,dtype=np.float32)
    if a.ndim!=1 or not np.all(np.isfinite(a)): raise ValueError('expected finite mono waveform')
    own=tagger is None
    if own:
        import sherpa_onnx as s
        tagger=s.AudioTagging(s.AudioTaggingConfig(model=s.AudioTaggingModelConfig(zipformer=s.OfflineZipformerAudioTaggingModelConfig(model=str(model_path)),num_threads=max(1,min(2,int(threads))),provider='cpu'),labels=str(labels_path),top_k=527))
    details=[]
    for st in window_starts(len(a)):
        chunk=a[st:st+80000];start=st/16000;end=(st+len(chunk))/16000
        peak=float(np.max(np.abs(chunk))) if len(chunk) else 0.
        rms=float(np.sqrt(np.mean(chunk*chunk))) if len(chunk) else 0.
        w=dict(start=start,end=end,speech_score=0.,top_labels=[],category_scores={},rms=rms,peak=peak,model_inference_executed=False)
        # Peak-based silence keeps short transients out of an RMS silence shortcut.
        if peak<=float(config.get('event_silence_peak',1e-5)):
            w['category_scores']['silence']=dict(score=1.,source_label='digital_or_near_digital_silence');details.append(w);continue
        if len(chunk)<80000: chunk=np.pad(chunk,(0,80000-len(chunk)))
        stream=tagger.create_stream();stream.accept_waveform(sample_rate=16000,waveform=chunk);result=tagger.compute(stream)
        w['model_inference_executed']=True
        scores=sorted([dict(label=str(r.name),score=float(r.prob)) for r in result],key=lambda x:-x['score'])
        private_voice_labels={'Male speech, man speaking','Female speech, woman speaking','Child speech, kid speaking','Baby cry, infant cry','Children shouting','Children playing'}
        w['top_labels']=[x for x in scores if x['label'] not in private_voice_labels][:10];w['speech_score']=max((x['score'] for x in scores if x['label']=='Speech'),default=0.)
        for item in scores:
            cat=category(item['label'])
            if cat and item['score']>w['category_scores'].get(cat,{}).get('score',-1): w['category_scores'][cat]=dict(score=item['score'],source_label=item['label'])
        details.append(w)
    return postprocess(details,configured_thresholds(config),release_ratio=float(config.get('event_release_ratio',1.))),details
