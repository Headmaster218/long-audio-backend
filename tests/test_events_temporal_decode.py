import numpy as np
import pytest
from audio_backend.events.temporal_decode import median_probabilities,frame_runs

def test_reflection_preserves_edge_evidence():
    assert median_probabilities([[1.,0.,0.,0.,0.]],3).tolist()==[[1.,0.,0.,0.,0.]]

def test_class_axes_do_not_mix():
    x=np.array([[1.,1.,1.],[0.,0.,0.]])
    np.testing.assert_array_equal(median_probabilities(x,3),x)

def test_invalid_filter_inputs():
    for a,width in [([[np.nan]],3),([[1.]],2),([],3)]:
        with pytest.raises(ValueError):median_probabilities(a,width)

def test_frame_runs_bounded_to_real_audio():
    assert frame_runs([False,True,True,False,True],.04,.19)==[(.04,.12),(.16,.19)]

def test_same_formula_as_audited_decoder():
    p=np.random.default_rng(0).uniform(size=(7,250))
    expected=np.median(np.lib.stride_tricks.sliding_window_view(np.pad(p,((0,0),(4,4)),mode='symmetric'),9,axis=1),axis=-1)
    np.testing.assert_array_equal(median_probabilities(p),expected)
