"""Candidate planning must precede inference with the eventual primary cut."""
import pytest
from audio_backend.boundaries import candidate_core_bounds,safe_cut
from audio_backend.moss_td_candidates import plan_supports

TURNS=[{'start':0.,'end':27.5,'speaker_local_id':'a'},
       {'start':1.,'end':27.,'speaker_local_id':'b'},
       {'start':28.5,'end':34.,'speaker_local_id':'a'}]


def test_candidate_supports_stop_at_exact_later_worker_cut():
    options={'candidate_core_start':0.,'candidate_core_end':30.,
             'candidate_core_apply_safe_cut':True,'candidate_core_cut_lower':27.}
    start,end=candidate_core_bounds(options,TURNS,34.)
    worker_actual=safe_cut(TURNS,30.,27.,34.)
    assert end==worker_actual==28.
    regions,plans=plan_supports(TURNS,34.,dict(candidate_core_start=start,candidate_core_end=end))
    assert plans and all(p['end']<=worker_actual for p in plans)
    assert plans[-1]['end']==worker_actual
    # Same existing primary cut whether optional candidate inference is on/off.
    assert safe_cut(TURNS,30.,27.,34.)==worker_actual


def test_final_core_does_not_gain_an_extra_safe_cut():
    assert candidate_core_bounds({'candidate_core_start':0.,'candidate_core_end':30.,
        'candidate_core_apply_safe_cut':False,'candidate_core_cut_lower':27.},TURNS,34.)==(0.,30.)


def test_short_core_preserves_original_safe_cut_fallback():
    assert candidate_core_bounds({'candidate_core_start':0.,'candidate_core_end':.1,
        'candidate_core_apply_safe_cut':True,'candidate_core_cut_lower':.5},[],.1)==(0.,.1)

@pytest.mark.parametrize('options',[{'candidate_core_end':float('nan')},
    {'candidate_core_start':4,'candidate_core_end':2},
    {'candidate_core_apply_safe_cut':True,'candidate_core_cut_lower':float('inf')}])
def test_invalid_candidate_cut_rejected(options):
    with pytest.raises(ValueError):candidate_core_bounds(options,TURNS,34.)
