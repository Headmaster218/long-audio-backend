import math
import pytest
from audio_backend.experimental_identity_memory import CorrectionMemory, Evidence


def ev(seq, angle=0, *, oid=None, unit=None, version='model@sha256:test', seconds=3, clean=True):
    r=math.radians(angle)
    return Evidence(oid or f'o{seq}', seq, (math.cos(r), math.sin(r)), version, unit or f'u{seq}', seconds, clean)


def memory(strategy='anchored_auto', **kwargs):
    m=CorrectionMemory('model@sha256:test', 2, strategy, **kwargs)
    m.query(ev(0)); m.verify_latest('A','a',whole_evidence_verified=True)
    m.query(ev(1,180)); m.verify_latest('B','b',whole_evidence_verified=True)
    return m


def test_no_unverified_anchor_or_auto_enrollment():
    m=CorrectionMemory('model@sha256:test',2)
    assert m.query(ev(0)).status=='no_verified_identity'
    assert not m.anchors
    with pytest.raises(ValueError,match='whole-embedding'):
        m.verify_latest('A','e')


def test_explicit_provenance_and_retraction():
    m=memory(); e=m.events[0]
    assert e.evidence.observation_id=='o0' and e.previous_prediction is None
    m.query(ev(2,45)); assert m.summary()['automatic_observations']==1
    m.retract('a'); assert 'A' not in m.anchors and not m.automatic
    with pytest.raises(ValueError):m.retract('a')


def test_no_automatic_chain_or_distant_anchor_expansion():
    m=memory(); assert m.query(ev(2,45)).status=='verified_anchor'
    assert m.query(ev(3,60)).status=='automatic_supported'
    assert [x.observation_id for x in m.automatic['A']]==['o2']
    assert m.query(ev(4,70)).person_id is None


def test_automatic_support_is_not_safety_guarantee():
    # A second person's embedding could inhabit this region: acoustic gates do
    # not see person labels. This negative fixture records the extension risk.
    baseline=memory('verified_multi_anchor'); auto=memory()
    baseline.query(ev(2,45));auto.query(ev(2,45))
    assert baseline.query(ev(3,60)).person_id is None
    assert auto.query(ev(3,60)).person_id=='A'


def test_same_source_view_cannot_supply_automatic_support():
    m=memory();m.query(ev(2,45,unit='utterance'))
    assert m.query(ev(3,60,unit='utterance')).person_id is None


def test_automatic_cap_and_deterministic_eviction():
    m=memory()
    for i in range(2,10):m.query(ev(i,40))
    assert [e.observation_id for e in m.automatic['A']]==['o6','o7','o8','o9']


def test_source_unit_deduplicates_insertion():
    m=memory();m.query(ev(2,40,unit='same'));m.query(ev(3,42,unit='same'))
    assert len(m.automatic['A'])==1


@pytest.mark.parametrize('version,seconds,clean',[('wrong',3,True),('model@sha256:test',1.9,True),('model@sha256:test',3,False),('model@sha256:test',float('nan'),True)])
def test_model_and_quality_fail_closed(version,seconds,clean):
    m=memory()
    if version=='wrong':
        with pytest.raises(ValueError):m.query(ev(2,version=version))
    else:
        assert m.query(ev(2,seconds=seconds,clean=clean)).person_id is None
        with pytest.raises(ValueError):m.verify_latest('A','c',whole_evidence_verified=True)
    assert not m.automatic


def test_finite_nonzero_dimensions():
    for vector in ((0.,0.),(float('nan'),0.),(1.,)):
        m=memory()
        with pytest.raises(ValueError):m.query(Evidence('x',2,vector,'model@sha256:test','x',3,True))


def test_strict_chronology_and_no_future_correction():
    m=memory()
    with pytest.raises(ValueError):m.query(ev(1))
    assert m.last_sequence==1
    assert m.latest.observation_id=='o1'


def test_correction_duplicate_and_conflict_rejected():
    m=memory()
    with pytest.raises(ValueError):m.verify_latest('A','b',whole_evidence_verified=True)
    with pytest.raises(ValueError):m.verify_latest('A','c',whole_evidence_verified=True)


def test_negative_example_veto_never_redirects_to_next_person():
    m=memory();m.query(ev(2,45));m.reject_latest('A','neg',whole_evidence_verified=True)
    assert m.query(ev(3,45)).status=='negative_evidence_veto'
    m.retract('neg');assert m.query(ev(4,45)).person_id=='A'


def test_cannot_links_symmetric_reversible_and_not_transitive():
    m=memory();m.add_cannot_link('A','B','different')
    with pytest.raises(ValueError,match='different-person'):m.assert_merge_allowed('B','A')
    m.retract_constraint('different')
    with pytest.raises(ValueError,match='deliberately unavailable'):m.assert_merge_allowed('B','A')


def test_zero_auto_cap_and_anchor_caps():
    m=memory(max_automatic=0,max_anchors=1)
    m.query(ev(2,40));assert not m.automatic
    with pytest.raises(OverflowError):m.verify_latest('A','c',whole_evidence_verified=True)


def test_full_gallery_margin_prevents_ambiguous_admission():
    m=CorrectionMemory('model@sha256:test',2,'anchored_auto')
    m.query(ev(0));m.verify_latest('A','a',whole_evidence_verified=True)
    m.query(ev(1,30));m.verify_latest('B','b',whole_evidence_verified=True)
    assert m.query(ev(2,15)).status=='ambiguous'
    assert not m.automatic


def test_wrong_strategy_and_policy_caps():
    with pytest.raises(ValueError):memory('made_up')
    with pytest.raises(ValueError):memory(max_anchors=9)
    with pytest.raises(ValueError):memory(max_automatic=5)


def test_automatic_vectors_never_modify_verified_anchors():
    m=memory();before=dict(m.anchors)
    for i in range(2,20):m.query(ev(i,40))
    assert m.anchors==before


def test_contradictory_negative_requires_retraction():
    m=memory()
    with pytest.raises(ValueError,match='retract first'):
        m.reject_latest('B','negative',whole_evidence_verified=True)
    m.retract('b')
    m.reject_latest('B','negative',whole_evidence_verified=True)


def test_event_cap_and_negative_cap_are_explicit():
    m=memory(max_events=2)
    m.query(ev(2,40))
    with pytest.raises(OverflowError):m.verify_latest('A','c',whole_evidence_verified=True)
    m=memory(max_negative=1);m.query(ev(2,40));m.reject_latest('A','n',whole_evidence_verified=True)
    m.query(ev(3,50))
    with pytest.raises(OverflowError):m.reject_latest('A','n2',whole_evidence_verified=True)


def test_exact_poisoned_memory_amplification_fixture():
    outcomes=[]
    for strategy in ('verified_multi_anchor','anchored_auto'):
        m=CorrectionMemory('v',3,strategy)
        m.query(Evidence('a',0,(1,0,0),'v','a',3,True));m.verify_latest('A','seed',whole_evidence_verified=True)
        for seq,x in [(1,.66),(2,.46)]:
            d=m.query(Evidence(str(seq),seq,(x,0,math.sqrt(1-x*x)),'v',str(seq),3,True))
        outcomes.append(d.person_id)
    assert outcomes==[None,'A']  # Expected design risk, not a passing safety claim.


@pytest.mark.parametrize('sequence',[float('nan'),float('inf'),1.5,True,-1])
def test_noninteger_sequence_fails_closed(sequence):
    m=memory()
    with pytest.raises(ValueError):m.query(ev(sequence))
    assert m.last_sequence==1


def test_stable_normalization_of_huge_values():
    from audio_backend.experimental_identity_memory import normalized
    n=normalized((1e308,1e308))
    assert sum(x*x for x in n)==pytest.approx(1.)


def test_unknown_or_segment_only_scope_cannot_seed_anchor():
    m=memory();e=ev(2)
    m.query(Evidence(e.observation_id,e.sequence,e.vector,e.model_version,e.source_unit,3,True,'one_text_utterance_only'))
    with pytest.raises(ValueError):m.verify_latest('A','c',whole_evidence_verified=True)


def test_verification_requires_literal_true():
    m=memory();m.query(ev(2,45))
    with pytest.raises(ValueError):m.verify_latest('A','c',whole_evidence_verified='false')
