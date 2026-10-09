"""Conservative sample-exact clean speech pooling; never synthesize evidence."""
import math

def union_intervals(intervals):
    result=[]
    for start,end in sorted(intervals):
        if end<=start:continue
        if result and start<=result[-1][1]:result[-1]=(result[-1][0],max(end,result[-1][1]))
        else:result.append((start,end))
    return result

def subtract_intervals(source,blocked):
    """Subtract only actual overlap; keep all remaining positive-length pieces."""
    result=union_intervals(source)
    for lo,hi in union_intervals(blocked):
        result=[piece for a,b in result for piece in ((a,min(b,lo)),(max(a,hi),b)) if piece[1]>piece[0]]
    return result

def clean_speaker_audio(samples,turns,local_id,sample_rate=16000,max_seconds=20):
    import numpy as np
    duration=len(samples)/sample_rate
    own=[(max(0.,t['start']),min(duration,t['end'])) for t in turns if t['speaker_local_id']==local_id]
    other=[(max(0.,t['start']),min(duration,t['end'])) for t in turns if t['speaker_local_id']!=local_id]
    clean=subtract_intervals(own,other);budget=int(sample_rate*max_seconds);pieces=[];selected=[]
    for start,end in clean:
        # Round inward: no sample touching an excluded voice is added.
        lo=max(0,math.ceil(start*sample_rate));hi=min(len(samples),math.floor(end*sample_rate),lo+budget)
        if hi<=lo:continue
        pieces.append(samples[lo:hi]);selected.append((lo/sample_rate,hi/sample_rate));budget-=hi-lo
        if budget<=0:break
    waveform=np.concatenate(pieces) if pieces else np.zeros(0,dtype=np.float32)
    own_seconds=sum(b-a for a,b in union_intervals(own));clean_seconds=sum(b-a for a,b in clean)
    evidence={'clean_speech_seconds':len(waveform)/sample_rate,'source':'overlap_subtracted_neural_turns',
              'source_speech_seconds':own_seconds,'overlap_excluded_seconds':max(0.,own_seconds-clean_seconds),
              'clean_fragment_count':len(selected),'maximum_seconds':max_seconds,'overlap_excluded':True}
    return waveform,evidence
