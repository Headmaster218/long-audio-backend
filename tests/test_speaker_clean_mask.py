import numpy as np
from audio_backend.speaker_evidence import subtract_intervals,clean_speaker_audio

def test_only_overlap_removed_not_entire_long_turn():
    turns=[{'start':0.,'end':10.,'speaker_local_id':'a'},{'start':4.,'end':6.,'speaker_local_id':'b'}]
    clean,evidence=clean_speaker_audio(np.ones(160000,dtype=np.float32),turns,'a')
    assert len(clean)==8*16000 and evidence['overlap_excluded_seconds']==2

def test_short_pieces_pool_without_duplication_or_padding():
    turns=[{'start':a,'end':a+.6,'speaker_local_id':'a'} for a in [0.,1.,2.,3.]]
    turns.append(dict(turns[0]))
    clean,evidence=clean_speaker_audio(np.ones(4*16000,dtype=np.float32),turns,'a')
    assert 2.399<=len(clean)/16000<=2.4
    assert evidence['clean_fragment_count']==4

def test_union_and_cap():
    assert subtract_intervals([(0,5),(4,10)],[(2,3),(2.5,7)])==[(0,2),(7,10)]
    clean,evidence=clean_speaker_audio(np.ones(30*16000,dtype=np.float32),[{'start':0,'end':30,'speaker_local_id':'a'}],'a')
    assert len(clean)==20*16000 and evidence['source_speech_seconds']==30
