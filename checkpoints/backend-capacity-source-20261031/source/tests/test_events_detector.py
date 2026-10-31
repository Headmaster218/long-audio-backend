import numpy as np
import pytest
from audio_backend.events import category, postprocess, window_starts, detect_events

def test_category_does_not_conflate_doorbell():
    assert category('Doorbell') is None
    assert category('Door')=='door'
    assert category('Slam')=='door'

def test_windows_cover_short_and_tail():
    assert window_starts(3000)==[0]
    assert window_starts(80000)==[0]
    assert window_starts(81000)==[0,40000]

def test_multiple_classes_and_short_door():
    d=[dict(start=0,end=.2,speech_score=.99,category_scores={k:dict(score=.8,source_label=k) for k in ['door','music']})]
    e=postprocess(d)
    assert {x['label'] for x in e}=={'door','music'}
    assert all(x['end']==.2 and x['time_precision']=='window_support' for x in e)

def test_silence_does_not_swallow_transient():
    class R:
        name='Door';prob=.9
    class Stream:
        def accept_waveform(self,**kw): assert len(kw['waveform'])==80000
    class Tagger:
        def create_stream(self):return Stream()
        def compute(self,stream):return [R()]
    a=np.zeros(16000,dtype=np.float32);a[30]=.01
    events,_=detect_events(a,None,None,tagger=Tagger())
    assert events[0]['label']=='door'
    silence,_=detect_events(np.zeros(100),None,None,tagger=Tagger())
    assert silence[0]['label']=='silence'

def test_threshold_validation():
    with pytest.raises(ValueError):postprocess([],{"rain":float('nan')})

def test_unknown_background_is_not_false_positive():
    from audio_backend.events.metrics import presence_metrics
    m=presence_metrics([dict(id='speech',labels=[])],{'speech':{'rain'}},['rain'])['rain']
    assert m['fp']==0 and m['unknown']==1 and m['precision'] is None
    proxy=presence_metrics([dict(id='speech',labels=[])],{'speech':{'rain'}},['rain'],True)['rain']
    assert proxy['fp']==1

def test_matching_is_one_to_one():
    from audio_backend.events.metrics import interval_metrics
    g=[dict(label='door',start=1,end=1.2)]
    p=[dict(label='door',start=0,end=5),dict(label='door',start=1,end=1.2)]
    m=interval_metrics(g,p)
    assert m['tp']==1 and m['fp']==1 and m['fn']==0

def test_merge_source_label_tracks_max_score():
    d=[dict(start=0,end=5,category_scores={'door':dict(score=.5,source_label='Door')}),dict(start=2.5,end=7.5,category_scores={'door':dict(score=.8,source_label='Slam')})]
    e=postprocess(d,max_seconds=1e6)
    assert len(e)==1 and e[0]['source_label']=='Slam' and e[0]['end']==7.5

def test_positive_only_does_not_imply_perfect_precision():
    from audio_backend.events.metrics import presence_metrics
    m=presence_metrics([dict(id='rain',labels=['rain'])],{'rain':{'rain'}},['rain'])['rain']
    assert m['recall']==1 and m['precision'] is None and m['f1'] is None

def test_generic_creak_is_not_claimed_to_be_a_door_close():
    assert category('Creak') is None
    assert category('Squeak') is None
    d=[dict(start=0,end=5,category_scores={'door':dict(score=.6,source_label='Door')})]
    event=postprocess(d)[0]
    assert event['label']=='door' and 'close' not in event['label']
