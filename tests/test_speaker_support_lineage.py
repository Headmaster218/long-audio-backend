"""Sample-support provenance is evidence, never a purity/human certificate."""
import hashlib
import copy
import numpy as np
import pytest
from audio_backend.speaker_evidence import clean_speaker_audio, project_review_support, MAX_REVIEW_SUPPORT_INTERVALS


def test_pool_hash_covers_all_actual_fragments_not_one_corrected_utterance():
    samples=np.arange(60,dtype=np.float32)/100
    turns=[{'start':0,'end':5,'speaker_local_id':'A'},
           {'start':1,'end':2,'speaker_local_id':'B'},
           {'start':3,'end':4,'speaker_local_id':'B'}]
    wave,evidence=clean_speaker_audio(samples,turns,'A',sample_rate=10)
    support=evidence['pooled_support']
    assert support['intervals_samples']==[[0,10],[20,30],[40,50]]
    expected=np.concatenate([samples[:10],samples[20:30],samples[40:50]])
    np.testing.assert_array_equal(wave,expected)
    assert support['pooled_pcm_sha256']==hashlib.sha256(expected.astype('<f4').tobytes()).hexdigest()
    assert support['pooled_sample_count']==30 and support['complete']
    assert support['human_verified'] is False
    assert support['purity_status']=='predicted_from_diarization_unverified'


def test_inward_rounding_and_budget_record_exact_samples():
    samples=np.ones(100,dtype=np.float32)
    wave,evidence=clean_speaker_audio(samples,[{'start':.11,'end':8.89,'speaker_local_id':'A'}],
                                    'A',sample_rate=10,max_seconds=2.5)
    assert len(wave)==25
    assert evidence['pooled_support']['intervals_samples']==[[2,27]]


def test_projection_keeps_pre_and_post_core_context_without_certifying_it():
    _,evidence=clean_speaker_audio(np.ones(100,dtype=np.float32),
                                  [{'start':0,'end':10,'speaker_local_id':'A'}], 'A',sample_rate=10)
    source=copy.deepcopy(evidence['pooled_support'])
    result=project_review_support(source,core_start_seconds=2,analyzer_chunk_start_seconds=-1)
    assert result['coordinate_projection']['core_relative_intervals_seconds']==[[-2,8]]
    assert result['coordinate_projection']['chunk_relative_intervals_seconds']==[[-1,9]]
    assert source==evidence['pooled_support'] and 'coordinate_projection' not in source
    assert result['human_verified'] is False and result['coordinate_projection']['context_preserved']


def test_empty_input_has_empty_exact_support_not_an_eligible_anchor():
    wave,evidence=clean_speaker_audio(np.empty(0,dtype=np.float32),[],'A')
    support=evidence['pooled_support']
    assert len(wave)==support['pooled_sample_count']==0
    assert support['intervals_samples']==[] and support['complete']
    assert support['human_verified'] is False


def test_large_fragment_inventory_is_bounded_and_explicitly_incomplete():
    n=MAX_REVIEW_SUPPORT_INTERVALS+1
    turns=[{'start':2*i,'end':2*i+1,'speaker_local_id':'A'} for i in range(n)]
    wave,evidence=clean_speaker_audio(np.ones(2*n,dtype=np.float32),turns,'A',sample_rate=1,max_seconds=2*n)
    support=evidence['pooled_support']
    assert len(wave)==n
    assert len(support['intervals_samples'])==MAX_REVIEW_SUPPORT_INTERVALS
    assert support['interval_count']==n and support['complete'] is False
    assert support['pooled_sample_count']==n


@pytest.mark.parametrize('origin',[float('nan'),float('inf'),True,'2'])
def test_projection_rejects_invalid_origin(origin):
    _,evidence=clean_speaker_audio(np.ones(10,dtype=np.float32),[],'A')
    with pytest.raises(ValueError):project_review_support(evidence['pooled_support'],core_start_seconds=origin,analyzer_chunk_start_seconds=0)


@pytest.mark.parametrize('intervals',[[[-1,2]],[[0,11]],[[0,5],[4,7]],[[0,True]],[[2,2]]])
def test_projection_rejects_fabricated_interval_coordinates(intervals):
    _,evidence=clean_speaker_audio(np.ones(10,dtype=np.float32),[],'A')
    evidence['pooled_support']['intervals_samples']=intervals
    with pytest.raises(ValueError):project_review_support(evidence['pooled_support'],core_start_seconds=0,analyzer_chunk_start_seconds=0)


@pytest.mark.parametrize('key,value',[('pooled_sample_count',3),('interval_count',2),('complete',True),
                                    ('pooled_pcm_sha256','fake'),('human_verified',True),
                                    ('purity_status','verified')])
def test_projection_rejects_inconsistent_or_human_certified_metadata(key,value):
    _,evidence=clean_speaker_audio(np.ones(10,dtype=np.float32),
                                  [{'start':0,'end':1,'speaker_local_id':'A'}],'A',sample_rate=10)
    source=evidence['pooled_support']
    if key=='complete':source['interval_count']=2
    source[key]=value
    with pytest.raises(ValueError):project_review_support(source,core_start_seconds=0,analyzer_chunk_start_seconds=0)


def test_incomplete_prefix_requires_room_for_every_omitted_nonempty_interval():
    n=MAX_REVIEW_SUPPORT_INTERVALS+1
    _,evidence=clean_speaker_audio(np.ones(2*n,dtype=np.float32),
        [{'start':2*i,'end':2*i+1,'speaker_local_id':'A'} for i in range(n)],
        'A',sample_rate=1,max_seconds=2*n)
    support=evidence['pooled_support']
    support['pooled_sample_count']=n-1
    with pytest.raises(ValueError):project_review_support(support,core_start_seconds=0,analyzer_chunk_start_seconds=0)
    support['pooled_sample_count']=n+3
    with pytest.raises(ValueError):project_review_support(support,core_start_seconds=0,analyzer_chunk_start_seconds=0)


def test_projection_rejects_wrong_hash_encoding():
    _,evidence=clean_speaker_audio(np.ones(10,dtype=np.float32),[],'A')
    evidence['pooled_support']['hash_encoding']='encoded WAV header and bytes'
    with pytest.raises(ValueError):project_review_support(evidence['pooled_support'],core_start_seconds=0,analyzer_chunk_start_seconds=0)
