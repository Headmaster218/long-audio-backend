import importlib.util
from pathlib import Path
import copy
import pytest
spec=importlib.util.spec_from_file_location('correction_replay',Path(__file__).resolve().parents[1]/'scripts/benchmarks/replay_correction_identity.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def row(oid,day,start,person='A',vector=(1.,0.)):
    return {'id':oid,'recording_id':day,'recording_date':day,'start':start,'end':start+3,
            'speaker':person,'embedding':vector}


def case():
    return {'name':'fixture','warmup':[row('seed','2005-01-01',0)],
            'probes':[row('probe','2005-01-02',0)],'known':{'A'},'cutoff':['2005-01-01']}


def test_date_parsing_and_no_invented_date():
    assert m.date('01-03-2005')=='2005-03-01'
    assert m.date('19-01-05')=='2005-01-19'
    assert m.date(None) is None
    with pytest.raises(ValueError):m.date('today')


def test_probe_gold_cannot_affect_predictions():
    a=case();b=copy.deepcopy(a);b['probes'][0]['speaker']='unseen'
    ra=m.run_case(a,1,'anchored_auto','v',2);rb=m.run_case(b,1,'anchored_auto','v',2)
    assert ra['rows'][0]['assigned']==rb['rows'][0]['assigned']=='A'
    assert ra['rows'][0]['status']==rb['rows'][0]['status']
    assert rb['summary']['wrong_person_assignments']==1


def test_query_is_scored_before_its_label_is_added():
    c=case();c['probes'][0]['embedding']=(0.,1.)
    r=m.run_case(c,1,'anchored_auto','v',2)
    assert r['rows'][0]['assigned'] is None
    assert r['final_state']['verified_anchors']==1


def test_future_warmup_is_rejected():
    c=case();c['warmup'][0]['recording_date']='2005-01-03'
    with pytest.raises(ValueError,match='Future'):m.validate_case(c)


def test_duplicate_source_or_id_rejected():
    c=case();c['probes'][0]['id']='seed'
    with pytest.raises(ValueError,match='Duplicate'):m.validate_case(c)
    c=case();c['probes'][0]=dict(c['warmup'][0],id='different_view')
    with pytest.raises(ValueError,match='Repeated source'):m.validate_case(c)


def test_nonchronological_replay_rejected():
    c=case();c['probes']=[row('p1','2005-01-03',0),row('p2','2005-01-02',0)]
    with pytest.raises(ValueError,match='Nonchronological'):m.validate_case(c)


def test_review_counts_include_not_retained_labels():
    c=case();c['warmup'].append(row('extra','2005-01-01',5))
    r=m.run_case(c,1,'centroid','v',2)
    assert r['initial_reviewed_clips']==2 and r['initial_retained_anchors']==1


def test_report_rows_are_vector_free():
    r=m.run_case(case(),1,'centroid','v',2)
    banned={'embedding','vector','evidence'}
    def check(v):
        if isinstance(v,dict):
            assert not banned.intersection(v)
            for x in v.values():check(x)
        elif isinstance(v,list):
            for x in v:check(x)
    check(r)


def test_overlapping_source_evidence_is_rejected():
    c=case();c['probes'].append(row('overlap','2005-01-02',2))
    with pytest.raises(ValueError,match='Overlapping'):m.validate_case(c)
