import numpy as np
import pytest
from audio_backend.overlap import (official_input_normalization, asr_safe_source,
    overlapping_regions, source_permutation, match_clean_identities, leakage_diagnostics)


def test_silent_normalization_and_finite_check():
    a,g=official_input_normalization(np.zeros(160)); assert g==1 and not a.any()
    a,g=official_input_normalization(np.ones(160)*.1); assert np.isfinite(a).all()
    with pytest.raises(ValueError): official_input_normalization([float('nan')])


def test_asr_scaling_retains_raw_and_bounds_peak():
    raw=np.array([0.,30.,-2.],dtype=np.float32); before=raw.copy()
    y,meta=asr_safe_source(raw,.05)
    assert np.array_equal(raw,before) and abs(y).max()<=.950001
    assert meta['gain_is_activity_evidence'] is False


def test_capacity_and_short_interruptions_are_retained():
    turns=[dict(start=0,end=5,speaker_local_id='a'),dict(start=1,end=4,speaker_local_id='b'),dict(start=2,end=2.1,speaker_local_id='c')]
    regions=overlapping_regions(turns,5)
    assert [r['status'] for r in regions]==['pending_separation','capacity_exceeded','pending_separation']
    assert regions[1]['speaker_local_ids']==['a','b','c']
    assert len(turns)==3


def test_permutation_matches_swapped_sources_but_is_not_identity():
    rng=np.random.default_rng(32); x=rng.normal(size=(2,1600)); y=x[::-1]*np.array([[.2],[-2.]])
    match=source_permutation(x,y)
    assert match['permutation']==[1,0] and match['identity_claim'] is False
    assert source_permutation(x,np.stack([x[0],x[0]]))['permutation'] is None
    assert source_permutation(np.zeros((2,100)),np.zeros((2,100)))['permutation'] is None


def test_identity_matching_never_promotes_or_forces_collisions():
    clean={'a':[1.,0.],'b':[0.,1.]}
    r=match_clean_identities([[0.,1.],[1.,0.]],clean)
    assert [s['speaker_local_id'] for s in r]==['b','a']
    assert not any(s['profile_update_eligible'] for s in r)
    r=match_clean_identities([[1.,0.],[.99,.01]],clean)
    assert all(s['speaker_local_id'] is None for s in r)
    assert match_clean_identities([[1.,0.]],{'a':[1.,0.]})[0]['speaker_local_id'] is None
    assert match_clean_identities([[1.,0.]],clean,evidence_seconds={'a':.3,'b':5})[0]['speaker_local_id'] is None


def test_duplicate_words_are_preserved_and_only_flagged():
    x=np.tile(np.arange(50),(2,1))
    result=leakage_diagnostics(x,['yes','yes'])
    assert len(result['flags'])==2 and result['tracks_deleted'] is False


def test_signal_score_is_gain_and_permutation_invariant():
    from scripts.benchmarks.score_overlap_signal import score
    rng=np.random.default_rng(12); refs=rng.normal(size=(2,1600)); mixture=refs.sum(axis=0)
    result=score(mixture,refs,refs[::-1]*3)
    assert result['mean_improvement_db']>100
    assert [r['estimate_index'] for r in result['per_source']]==[1,0]
