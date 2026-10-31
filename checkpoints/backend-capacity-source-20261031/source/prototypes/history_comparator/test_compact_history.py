"""Fixed synthetic oracle/negative controls; no audio, models or gold labels."""
import hashlib
import math
import random
import struct
from dataclasses import replace
import pytest
from audio_backend import recording_speaker_links as oracle
from compact_history import (History, Observation, Matrix, Work, finish, cluster,
                             Pending, Invalid, canonical, pair_index, PAGE_BYTES)


def f32(value):
    return struct.unpack('<f',struct.pack('<f',value))[0]


def unit(*values):
    result=list(values)+[0.]*(512-len(values))
    norm=math.sqrt(math.fsum(v*v for v in result))
    return [f32(v/norm) for v in result]


def observation(i,values=None,**kwargs):
    return Observation.from_vector(f'n{i:05}',unit(1.) if values is None else values,**kwargs)


def make_history(rows):
    h=History(oracle.POLICY_SHA256_V3,'synthetic')
    finish(h.append(rows,Work()))
    return h


def oracle_equivalence(scores,n,cannot=(),overlap=(),typed=True):
    expected=oracle.cluster_scores(scores,n,cannot,source_overlap=overlap if typed else None)
    result=finish(cluster(Matrix.from_scores(scores,n,cannot,overlap),Work(),typed=typed))
    assert result[0]==expected[0]
    assert result[1].oracle_trace()==expected[1]
    return result


@pytest.mark.parametrize('n',[0,1,2,3,4,8,16,31,32])
@pytest.mark.parametrize('typed',[False,True])
def test_exact_random_oracle(n,typed):
    rng=random.Random(1701+n)
    scores={(a,b):rng.choice([-.1,0.,.2,.44,.55,.65,.8,.9,1.]) for a in range(n) for b in range(a+1,n)}
    cannot={p for p in scores if rng.random()<.04}
    overlap={p for p in scores if rng.random()<.05}
    oracle_equivalence(scores,n,cannot,overlap,typed)


@pytest.mark.parametrize('top',[math.nextafter(.55,-math.inf),.55,math.nextafter(.55,math.inf),.9])
@pytest.mark.parametrize('runner',[.45,math.nextafter(.45,-math.inf),math.nextafter(.45,math.inf),.55,.8,.9])
def test_threshold_margin_tie_bits(top,runner):
    oracle_equivalence({(0,1):top,(0,2):runner,(1,2):runner},3)


def test_multiround_complete_link_and_nontransitive():
    scores={(a,b):(.95 if a//2==b//2 else .75 if b<4 else .1)
            for a in range(6) for b in range(a+1,6)}
    groups,trace=oracle_equivalence(scores,6)
    assert groups==[(0,1,2,3),(4,5)]
    assert len(trace.rounds)==3
    oracle_equivalence({(0,1):.95,(0,2):.8,(0,3):.1,(1,2):.2,(1,3):.1,(2,3):.1},4)


def test_cannot_and_overlap_are_veto_after_rank_not_filters():
    scores={(0,1):.95,(0,2):.7,(0,3):.1,(1,2):.1,(1,3):.1,(2,3):.1}
    groups,_=oracle_equivalence(scores,4,cannot={(0,1)})
    assert (0,2) not in groups
    groups,trace=oracle_equivalence(scores,4,overlap={(1,2)})
    assert groups==[(0,),(1,),(2,),(3,)]
    assert trace.oracle_trace()[0]['reasons']['source_overlap_dependency']>0


def test_exact_float32_score_bytes_and_incremental_prefix():
    rows=[observation(0,unit(1)),observation(1,unit(4,3)),observation(2,unit(0,0,1))]
    h=make_history(rows)
    prior=hashlib.sha256(memoryview(h.scores).cast('B')).hexdigest()
    work=Work()
    finish(h.append([observation(3,unit(1,2,3))],work))
    assert work.counts['dot_products']==3
    assert hashlib.sha256(memoryview(h.scores).cast('B')[:3*8]).hexdigest()==prior
    for b in range(4):
        for a in range(b):
            one=oracle.valid_vector(list(memoryview(h.rows[a].vector).cast('f')))
            two=oracle.valid_vector(list(memoryview(h.rows[b].vector).cast('f')))
            expected=math.fsum(x*y for x,y in zip(one,two))
            assert struct.pack('<d',h.scores[pair_index(a,b)])==struct.pack('<d',expected)


def test_original_float32_not_renormalized_and_bad_inputs():
    values=unit(1,2,3)
    assert Observation.from_vector('x',values).vector==struct.pack('<512f',*oracle.valid_vector(values))
    bad=values.copy();bad[0]=.1
    with pytest.raises(Invalid,match='not_original_float32'):
        Observation.from_vector('x',bad)
    with pytest.raises(Invalid):
        Observation.from_vector('x',[0.]*512)


def test_late_historical_competitor_changes_current_decision():
    h=make_history([observation(0,unit(1)),observation(1,unit(4,3)),observation(2,unit(0,0,1))])
    first=finish(h.compare(Work()))
    assert first.clusters==[(0,1),(2,)]
    old=first.token
    finish(h.append([observation(3,unit(3,1))],Work()))
    with pytest.raises(Pending): h.annotation_page(old)
    with pytest.raises(Pending): h.annotation_page(h.token())
    second=finish(h.compare(Work()))
    assert (0,1) not in second.clusters
    assert second.clusters==[(0,),(1,),(2,),(3,)]
    # Full-history result is independent of ingestion recency/order.
    reversed_history=make_history(list(reversed(h.rows)))
    assert finish(reversed_history.compare(Work())).clusters==second.clusters


def test_late_metadata_alias_overlap_and_minority_annotation_preserved():
    rows=[observation(0,unit(1),spans=(('s',100,0,10),)),
          observation(1,unit(4,3),spans=(('s',100,20,30),)),
          observation(2,unit(0,0,1),spans=(('s',100,40,50),))]
    h=make_history(rows)
    assert finish(h.compare(Work())).clusters==[(0,1),(2,)]
    alias=replace(rows[0],node_id='z_alias',source_key='alternate',vector=None,
                  spans=(('s',100,40,50),),stream='moss')
    finish(h.append([alias],Work()))
    decision=finish(h.compare(Work()))
    assert decision.clusters==[(0,),(1,),(2,)]
    pages=[];after=0
    while after<len(h.rows):
        page=h.annotation_page(h.token(),after,1);pages+=page['rows'];after=page['next_after']
    assert len(pages)==4 and pages[-1]['stream']=='moss'
    assert pages[-1]['status']=='probe_unavailable'
    assert pages[0]['duplicate_evidence']


def test_late_witness_and_alias_internal_contradiction():
    rows=[observation(0,unit(1),attributions=(('native','A'),)),
          observation(1,unit(4,3),attributions=(('native','B'),)),
          observation(2,unit(0,0,1))]
    h=make_history(rows)
    assert finish(h.compare(Work())).clusters==[(0,1),(2,)]
    h.add_witness('native','A','B')
    assert finish(h.compare(Work())).clusters==[(0,),(1,),(2,)]
    alias=replace(rows[0],node_id='alias',vector=None,attributions=(('native','B'),))
    finish(h.append([alias],Work()))
    with pytest.raises(Invalid,match='duplicate_native_conflict'): finish(h.compare(Work()))
    with pytest.raises(Pending): h.annotation_page(h.token())


def test_metadata_only_class_contradiction_not_dropped():
    a=replace(observation(0),vector=None,attributions=(('scope','A'),))
    b=replace(a,node_id='b',attributions=(('scope','B'),))
    h=make_history([a,b]);h.add_witness('scope','A','B')
    with pytest.raises(Invalid,match='duplicate_native_conflict'): finish(h.compare(Work()))


def test_duplicate_vector_and_pcm_disagreement_fail_closed():
    a=observation(0)
    for b in [replace(a,node_id='b',pcm_hash='wrong'),
              replace(a,node_id='b',vector=observation(1,unit(0,1)).vector)]:
        h=make_history([a,b])
        with pytest.raises(Invalid): finish(h.compare(Work()))
        with pytest.raises(Pending): h.annotation_page(h.token())


def test_paged_snapshot_pause_resume_and_stale_cas(monkeypatch):
    # Modest matrix creates multiple deterministic work quanta and a minority stream.
    rows=[observation(i,unit(1,i+1),stream='moss' if i==79 else 'primary') for i in range(80)]
    h=make_history(rows);work=Work();operation=h.compare(work)
    next(operation)
    with pytest.raises(Pending): h.annotation_page(h.token())
    result=finish(operation)
    assert work.counts['checkpoints']>=1
    for limit in (1,7,32):
        annotations=[];decisions=[]
        for method,target in ((h.annotation_page,annotations),(h.decision_page,decisions)):
            after=0
            while True:
                page=method(result.token,after,limit)
                assert len(page['rows'])<=limit and len(canonical(page))<=PAGE_BYTES
                target.extend(page['rows']);after=page['next_after']
                if page['complete']:break
        assert annotations==[result.annotation(i) for i in range(80)]
        assert annotations[-1]['stream']=='moss'
        assert sum(r['kind']=='membership' for r in decisions)==80
    operation=h.compare(Work());next(operation)
    h.add_witness('unrelated','A','B')
    with pytest.raises(Pending): finish(operation)
    with pytest.raises(Pending): h.annotation_page(h.token())
    # Missing a page is pending, not an incomplete current result.
    original=h.observation_page
    monkeypatch.setattr(h,'observation_page',lambda token,after=0,limit=32: ((),after) if after>=32 else original(token,after,limit))
    with pytest.raises(Pending,match='incomplete_scan'): finish(h.compare(Work()))


def test_append_pause_and_revocation_block_all_current_pages():
    h=make_history([observation(i,unit(1,i+1)) for i in range(12)])
    operation=h.append([observation(100+i,unit(2,i+1)) for i in range(8)],Work())
    next(operation)
    with pytest.raises(Pending): h.observation_page(h.token())
    with pytest.raises(Pending): finish(h.compare(Work()))
    finish(operation);finish(h.compare(Work()));token=h.token();h.revoke()
    with pytest.raises(Pending): h.annotation_page(token)
    with pytest.raises(Pending): finish(h.compare(Work()))


def test_budgeted_cluster_resume_identical():
    n=32;scores={(a,b):.9 if a//2==b//2 else .1 for a in range(n) for b in range(a+1,n)}
    work=Work();work.since_yield=4095
    operation=cluster(Matrix.from_scores(scores,n),work)
    next(operation)
    actual=finish(operation)
    expected=oracle.cluster_scores(scores,n,source_overlap=())
    assert (actual[0],actual[1].oracle_trace())==expected


def test_exact_observation_wire_cap_and_full_envelope_boundaries(monkeypatch):
    # Approach the metadata limit with escaped Unicode; measure actual bytes.
    base=observation(0,attributions=(('é'*64,'é'*64),('é'*64,'é'*64)),
                     spans=(('s'*64,2**63-1,0,10),),source_key='s'*64)
    assert len(canonical(base.metadata()))<=2048
    for length in range(65):
        candidate=replace(base,source_key='é'*length+'s'*(64-length))
        if len(canonical(candidate.metadata()))<=2048:base=candidate
    rows=[replace(base,node_id=f'n{i:05}') for i in range(32)]
    h=make_history(rows)
    page,after=h.observation_page(h.token())
    assert after<32 and after==len(page)
    assert len(h.encode_observation_page(h.token(),page,after))<=PAGE_BYTES
    assert len(h.encode_observation_page(h.token(),rows,32))>PAGE_BYTES
    # Small dynamically lowered projection cap exercises exact envelope logic.
    import compact_history as module
    simple=make_history([observation(i,unit(1,i+1)) for i in range(4)])
    finish(simple.compare(Work()))
    for method in (simple.annotation_page,simple.decision_page):
        one=method(simple.token(),limit=1)
        cap=len(canonical(one))
        monkeypatch.setattr(module,'PAGE_BYTES',cap)
        actual=method(simple.token())
        assert len(canonical(actual))<=cap and len(actual['rows'])==1
        monkeypatch.setattr(module,'PAGE_BYTES',PAGE_BYTES)


def test_embedding_space_is_bound_to_snapshot_and_current_pages():
    one=History(space='synthetic-space-A')
    two=History(space='synthetic-space-B')
    assert one.token()!=two.token()
    finish(one.compare(Work()))
    with pytest.raises(Pending):one.annotation_page(two.token())
    with pytest.raises(Pending):one.decision_page(two.token())
    with pytest.raises(AttributeError):one.space='synthetic-space-B'
