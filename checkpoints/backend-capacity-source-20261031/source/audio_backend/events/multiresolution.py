"""Experimental bounded extra support windows; disabled in the production adapter."""
from __future__ import annotations
import numpy as np
from .detector import category

def change_windows(samples, sample_rate=16000, max_peaks=3, lengths=(1.,2.)):
    """Select waveform changes without knowing event labels or using VAD.

    Three peaks, two scales: at most six extra windows per bounded input, not per
    second. Flux detects spectral changes; energy deltas include onset/offset.
    These are classifier support windows, never estimated acoustic event bounds.
    """
    a=np.asarray(samples,dtype=np.float32)
    if len(a)==0 or np.max(np.abs(a))<=1e-5:return []
    frame=800;hop=400
    padded=np.pad(a,(0,max(0,frame-len(a))))
    offsets=list(range(0,max(1,len(padded)-frame+1),hop))
    frames=np.stack([padded[st:st+frame] for st in offsets])
    rms=np.sqrt(np.mean(frames*frames,axis=1))
    spectrum=np.abs(np.fft.rfft(frames*np.hanning(frame),n=1024,axis=1))
    spectrum/=np.maximum(spectrum.sum(axis=1,keepdims=True),1e-9)
    flux=np.r_[0.,np.maximum(np.diff(spectrum,axis=0),0.).sum(axis=1)]
    energy=np.r_[0.,np.abs(np.diff(np.log(np.maximum(rms,1e-5))))]
    score=flux+np.minimum(energy,3.)/3.
    score[rms<max(1e-5,float(rms.max())*.025)]=0.
    peaks=[]
    for i in np.argsort(-score,kind='stable'):
        if score[i]<=0:break
        center=(offsets[int(i)]+frame//2)/sample_rate
        if all(abs(center-other)>=.5 for other in peaks):peaks.append(center)
        if len(peaks)>=max_peaks:break
    duration=len(a)/sample_rate;windows=[]
    for center in peaks:
        for length in lengths:
            length=min(float(length),duration)
            start=max(0.,min(center-length/2,duration-length));end=start+length
            pair=(round(start*sample_rate),round(end*sample_rate))
            if pair not in windows and pair[1]-pair[0]>=8000:windows.append(pair)
    return windows

def additional_details(samples,tagger):
    a=np.asarray(samples,dtype=np.float32);details=[]
    for st,en in change_windows(a):
        stream=tagger.create_stream();stream.accept_waveform(sample_rate=16000,waveform=a[st:en]);scores=tagger.compute(stream)
        w=dict(start=st/16000,end=en/16000,category_scores={},speech_score=0.,method='experimental_change_peak_multiresolution',time_precision='window_support',onset_precision_seconds=None)
        for r in scores:
            name=str(r.name);prob=float(r.prob);cat=category(name)
            if name=='Speech':w['speech_score']=prob
            if cat and prob>w['category_scores'].get(cat,{}).get('score',-1.):w['category_scores'][cat]=dict(score=prob,source_label=name)
        details.append(w)
    return details
