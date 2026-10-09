"""Pure decoding helpers for the disabled temporal research adapter."""
import numpy as np

def median_probabilities(probabilities, width=9):
    """Median over time with scipy.ndimage's default half-sample reflection."""
    p=np.asarray(probabilities)
    if p.ndim!=2 or p.shape[1]==0 or not np.isfinite(p).all():raise ValueError('expected finite class-by-time scores')
    if width<1 or width%2!=1:raise ValueError('median width must be positive and odd')
    pad=width//2
    return np.median(np.lib.stride_tricks.sliding_window_view(np.pad(p,((0,0),(pad,pad)),mode='symmetric'),width,axis=1),axis=-1)

def frame_runs(mask,step_seconds,duration_seconds):
    """Turn a Boolean frame mask into bounded model-estimated support intervals."""
    a=np.asarray(mask,dtype=bool)
    if a.ndim!=1 or step_seconds<=0 or duration_seconds<0:raise ValueError('invalid frame mask geometry')
    changes=np.diff(np.r_[False,a,False].astype(np.int8))
    return [(float(st*step_seconds),min(float(en*step_seconds),duration_seconds)) for st,en in zip(np.flatnonzero(changes==1),np.flatnonzero(changes==-1)) if st*step_seconds<duration_seconds]
