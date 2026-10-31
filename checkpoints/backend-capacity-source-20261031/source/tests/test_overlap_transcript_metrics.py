import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/benchmarks'))
from score_overlap_transcripts import score

def test_both_voices_and_swapped_channels():
    gold={'language':'zh','speakers':[{'id':'A','text':'你好世界'},{'id':'B','text':'今天下雨'}]}
    p={'transcripts':[{'channel':0,'text':'今天下雨'},{'channel':1,'text':'你好世界'}]}
    result=score(gold,p)
    assert result['error_rate']==0
    assert result['identity_claim'] is False
    assert score(gold,{'transcripts':[{'channel':0,'text':'你好世界'}]})['error_rate']==.5

def test_missing_third_voice_penalized():
    g={'language':'en','speakers':[{'id':'A','text':'one two'},{'id':'B','text':'three four'},{'id':'C','text':'five six'}]}
    p={'transcripts':[{'channel':0,'text':'one two'},{'channel':1,'text':'three four'}]}
    assert score(g,p)['errors']==2


def test_many_extra_tracks_are_insertions():
    g={'language':'en','speakers':[{'id':'A','text':'one two'}]}
    p={'transcripts':[{'channel':0,'text':'one two'}]+[{'channel':i,'text':'extra'} for i in range(1,12)]}
    assert score(g,p)['errors']==11

def test_uncertain_scopes_not_pretended_to_be_people():
    import pytest
    with pytest.raises(ValueError,match='Uncertain'):
        score({'language':'en','speakers':[]},{'transcripts':[],'whole_clip_per_speaker_scoring_eligible':False})

def test_subset_dp_matches_padded_bruteforce():
    import random,itertools
    from score_overlap_transcripts import edit,tokens
    rng=random.Random(84)
    for _ in range(150):
        refs=[{'id':str(i),'text':' '.join(rng.choices(['a','b','c'],k=rng.randrange(5)))} for i in range(rng.randrange(4))]
        hyps=[{'channel':i,'text':' '.join(rng.choices(['a','b','c'],k=rng.randrange(5)))} for i in range(rng.randrange(5))]
        n=max(len(refs),len(hyps));r=[tokens(x['text'],'en') for x in refs]+[[]]*(n-len(refs));h=[tokens(x['text'],'en') for x in hyps]+[[]]*(n-len(hyps))
        expected=min((sum(edit(r[i],h[j]) for i,j in enumerate(p)) for p in itertools.permutations(range(n))),default=0)
        assert score({'language':'en','speakers':refs},{'transcripts':hyps})['errors']==expected
