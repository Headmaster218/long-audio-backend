import numpy as np
from audio_backend.events.multiresolution import change_windows

def test_change_windows_bounded_and_nonempty_transient():
    a=np.zeros(160000,dtype=np.float32);a[59000:59200]=.3
    windows=change_windows(a)
    assert 0<len(windows)<=6
    assert all(0<=start<end<=len(a) for start,end in windows)
    assert all(8000<=end-start<=32000 for start,end in windows)

def test_change_windows_silence_empty():
    assert change_windows(np.zeros(10000))==[]

def test_change_windows_short_input_safe():
    assert change_windows(np.array([.1,.2],dtype=np.float32))==[]
