"""Five exact duplicate intervals observed in frozen 12.25h candidate run.

Deterministic ownership regression, not a repeat of model inference or a change
of the historical run. Original simultaneous voice branches remain separate.
"""
import copy
import pytest
from audio_backend.boundaries import lexical_core, committed_punctuation

CASES=[(5305.81,5305.889,'？',5306.19),
       (12447.951,12448.009,'。',12448.32),
       (12688.99,12689.35,'，',12689.01),
       (14999.165,14999.229,'。',14999.245),
       (42228.56,42228.595,'。',42228.595)]

def unit(a,b,text,track=None):
    return dict(start=a,end=b,ownership_anchor=(a+b)/2,text=text,source_track_id=track)

def bundle(units):
    return {'segments':[{'start':u['start'],'end':u['end'],'text':u['text'],
            'speaker_local_id':None,'source_track_id':u.get('source_track_id'),'_units':[u]} for u in units],
            'metadata':{}}

@pytest.mark.parametrize('a,b,text,cut',CASES)
def test_committed_punctuation_not_readmitted_after_lexical_seam(a,b,text,cut):
    # Same preceding lexical anchor establishes a reliable seam. The old seam
    # ignored punctuation and therefore selected its second copy by list order.
    word=unit(a-.6,a-.3,'hello');punct=unit(a,b,text)
    prior=[dict(word,emitted=True),dict(punct,emitted=True)]
    current=bundle([word,punct,unit(cut+.5,cut+1,'next')])
    rows=lexical_core(current,cut,cut+30,prior)
    assert [r['text'] for r in rows]==['next']
    assert current['metadata']['boundary_alignment_used']

@pytest.mark.parametrize('track',['voice-b','voice-c'])
def test_same_punctuation_from_simultaneous_other_source_retained(track):
    p=unit(30.1,30.2,'。','voice-a')
    c=unit(30.1,30.2,'。',track)
    rows=lexical_core(bundle([c]),30,60,[dict(p,emitted=True)])
    assert [r['text'] for r in rows]==['。']
    assert rows[0]['source_track_id']==track

@pytest.mark.parametrize('text',['hello','嗯','♪','🙂'])
def test_nonpunctuation_never_removed_by_exact_surface_check(text):
    u=unit(1,2,text)
    assert not committed_punctuation(u,[dict(u,emitted=True)])

def test_uncommitted_or_later_punctuation_is_not_removed():
    u=unit(1,2,'。')
    assert not committed_punctuation(u,[dict(u,emitted=False)])
    assert not committed_punctuation(unit(1.01,2.01,'。'),[dict(u,emitted=True)])

def test_committed_punctuation_status_survives_another_small_core():
    u=unit(29.9,30.1,'。');prior=[dict(u,emitted=True)]
    first=bundle([copy.deepcopy(u)])
    assert lexical_core(first,30,30.5,prior)==[]
    tail=first['metadata']['boundary_tail']
    assert tail[0]['emitted'] is True
    second=bundle([copy.deepcopy(u)])
    assert lexical_core(second,30.5,31,tail)==[]
