import importlib.util
import pathlib
import sys
p=pathlib.Path(__file__).resolve().parents[1]/'scripts/benchmarks'
sys.path.insert(0,str(p))
from replay_epoch_identity import replay

def turn(window,local='epoch:speaker_1',pid=None,score=None,status='unknown',chunk='a'):
 return dict(start=window,end=window+1,speaker_window_start=window,chunk_id=chunk,speaker_local_id=local,speaker_id=pid,speaker_match_score=score,speaker_status=status)

def test_causal_binding_is_scope_limited_and_requires_prior_verification():
 rows=[turn(0),turn(1,pid='person',status='provisional'),turn(2),turn(3,pid='person',score=.6,status='provisional'),turn(4),turn(5,local='other:speaker_1'),turn(6,chunk='b')]
 result,_=replay(rows)
 assert [r.get('continuity_added',False) for r in result]==[False,False,False,False,True,False,False]
 assert rows[4]['speaker_id'] is None

def test_fresh_ambiguity_clears_and_contradiction_suspends():
 rows=[turn(0,pid='one',score=.6,status='provisional'),turn(1,status='unknown_ambiguous'),turn(2),turn(3,pid='one',score=.6,status='provisional'),turn(4,pid='two',score=.65,status='provisional'),turn(5),turn(6,pid='two',score=.7,status='provisional'),turn(7)]
 result,counts=replay(rows)
 assert not any(r.get('continuity_added') for r in result)
 assert counts['contradictory_accepted_slot']==1
 assert counts['cleared_by_fresh_rejection']==1
