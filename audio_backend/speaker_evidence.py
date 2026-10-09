"""Conservative sample-exact clean speech pooling; never synthesize evidence."""
import math
import hashlib
from copy import deepcopy

MAX_REVIEW_SUPPORT_INTERVALS = 512
POOLED_HASH_ENCODING = 'concatenated selected mono IEEE754 float32 little-endian samples; no container header'


def project_review_support(support, *, core_start_seconds, analyzer_chunk_start_seconds):
    """Project decoder-input sample coordinates without clipping away context.

    This is not a purity certificate, source-file sample alignment, or a human
    confirmation. Negative core/chunk coordinates may refer to stream context.
    Persistence/API enrollment integration is deliberately separate.
    """
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
               for x in (core_start_seconds, analyzer_chunk_start_seconds)):
        raise ValueError('Support projection requires finite coordinate origins')
    if (support.get('schema_version') != 1 or support.get('time_axis') != 'analyzer_audio_samples'
            or not isinstance(support.get('sample_rate'), int) or isinstance(support['sample_rate'], bool)
            or support['sample_rate'] <= 0):
        raise ValueError('Unsupported embedding support coordinates')
    rate = support['sample_rate']
    intervals = support.get('intervals_samples')
    frames = support.get('analyzer_frame_count')
    if not isinstance(frames, int) or isinstance(frames, bool) or frames < 0:
        raise ValueError('Invalid analyzer frame count')
    if not isinstance(intervals, list) or len(intervals) > MAX_REVIEW_SUPPORT_INTERVALS:
        raise ValueError('Invalid or oversized support interval inventory')
    previous = 0
    for pair in intervals:
        if (not isinstance(pair, list) or len(pair) != 2
                or not all(isinstance(x, int) and not isinstance(x, bool) for x in pair)
                or not 0 <= previous <= pair[0] < pair[1] <= frames):
            raise ValueError('Invalid embedding support sample interval')
        previous = pair[1]
    count = support.get('interval_count')
    pooled = support.get('pooled_sample_count')
    complete = support.get('complete')
    digest = support.get('pooled_pcm_sha256')
    shown = sum(b-a for a,b in intervals)
    if (not isinstance(count, int) or isinstance(count, bool) or count < len(intervals)
            or not isinstance(pooled, int) or isinstance(pooled, bool) or not shown <= pooled <= frames
            or not isinstance(complete, bool)
            or (complete and (count != len(intervals) or pooled != shown))
            or (not complete and (len(intervals) != MAX_REVIEW_SUPPORT_INTERVALS or count <= len(intervals)))
            or pooled < shown + count - len(intervals)
            or pooled - shown > frames - previous
            or not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)
            or support.get('hash_encoding') != POOLED_HASH_ENCODING
            or support.get('human_verified') is not False
            or support.get('purity_status') != 'predicted_from_diarization_unverified'):
        raise ValueError('Inconsistent or improperly certified embedding support')
    projected = deepcopy(support)
    projected['coordinate_projection'] = {
        'core_start_in_analyzer_seconds': core_start_seconds,
        'analyzer_start_in_chunk_seconds': analyzer_chunk_start_seconds,
        'core_relative_intervals_seconds': [[a/rate-core_start_seconds, b/rate-core_start_seconds] for a,b in intervals],
        'chunk_relative_intervals_seconds': [[a/rate+analyzer_chunk_start_seconds, b/rate+analyzer_chunk_start_seconds] for a,b in intervals],
        'precision': 'decoder-input sample coordinates projected through extraction origins; not original encoded-file sample alignment',
        'context_preserved': True,
    }
    return projected

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
    clean=subtract_intervals(own,other);budget=int(sample_rate*max_seconds);pieces=[];selected=[];selected_samples=[]
    for start,end in clean:
        # Round inward: no sample touching an excluded voice is added.
        lo=max(0,math.ceil(start*sample_rate));hi=min(len(samples),math.floor(end*sample_rate),lo+budget)
        if hi<=lo:continue
        pieces.append(samples[lo:hi]);selected.append((lo/sample_rate,hi/sample_rate));selected_samples.append([lo,hi]);budget-=hi-lo
        if budget<=0:break
    waveform=np.concatenate(pieces) if pieces else np.zeros(0,dtype=np.float32)
    own_seconds=sum(b-a for a,b in union_intervals(own));clean_seconds=sum(b-a for a,b in clean)
    evidence={'clean_speech_seconds':len(waveform)/sample_rate,'source':'overlap_subtracted_neural_turns',
              'source_speech_seconds':own_seconds,'overlap_excluded_seconds':max(0.,own_seconds-clean_seconds),
              'clean_fragment_count':len(selected),'maximum_seconds':max_seconds,'overlap_excluded':True,
              'pooled_support': {
                  'schema_version': 1, 'time_axis': 'analyzer_audio_samples',
                  'sample_rate': sample_rate, 'analyzer_frame_count': len(samples),
                  'intervals_samples': selected_samples[:MAX_REVIEW_SUPPORT_INTERVALS],
                  'interval_count': len(selected_samples),
                  'complete': len(selected_samples) <= MAX_REVIEW_SUPPORT_INTERVALS,
                  'pooled_sample_count': len(waveform),
                  'pooled_pcm_sha256': hashlib.sha256(np.ascontiguousarray(waveform, dtype='<f4').tobytes()).hexdigest(),
                  'hash_encoding': POOLED_HASH_ENCODING,
                  'purity_status': 'predicted_from_diarization_unverified',
                  'human_verified': False,
              }}
    return waveform,evidence
