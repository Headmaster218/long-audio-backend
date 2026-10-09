import importlib.util
from pathlib import Path
import copy
import pytest
spec=importlib.util.spec_from_file_location('scheduled_review',Path(__file__).resolve().parents[1]/'scripts/benchmarks/replay_scheduled_identity_review.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def row(oid,day,start,person='A',vector=(1.,0.)):
    return {'id':oid,'recording_id':day,'recording_date':day,'start':start,'end':start+3,'speaker':person,'embedding':vector}


def case():
    return {'name':'fixture','warmup':[row(f'seed{i}','2005-01-01',i*5) for i in range(3)],
      'additional_warmup':[row(f'target{i}','2005-01-02',i*5) for i in range(9)],
      'probes':[row('later','2005-01-02',600)],'known':{'A'},'cutoff':['2005-01-02',600]}


def test_prefix_reviews_correct_queries_without_score_selection():
    r=m.run_prefix(case(),3,'v',2)
    assert r['target_already_correct_reviews']==3
    assert r['target_reviewed_clips']==3 and r['target_retained_anchors']==3
    assert [x['id'] for x in r['target_reviews']]==['target0','target1','target2']


def test_no_later_gold_enters_memory():
    a=case();b=copy.deepcopy(a);b['probes'][0]['speaker']='someone_else'
    ra=m.run_prefix(a,3,'v',2);rb=m.run_prefix(b,3,'v',2)
    assert ra['rows'][0]['assigned']==rb['rows'][0]['assigned']=='A'
    assert ra['frozen_anchor_state_sha256']==rb['frozen_anchor_state_sha256']


def test_capacity_rejections_count_all_reviews_seconds_no_replacement():
    r=m.run_prefix(case(),9,'v',2)
    assert r['target_reviewed_clips']==9 and r['target_reviewed_audio_seconds']==27
    assert r['target_retained_anchors']==5 and r['target_capacity_rejections']==4
    assert r['references_by_person']=={'A':8}
    assert [x['ordinal'] for x in r['target_reviews']]==list(range(1,10))


def test_zero_prefix_is_frozen_and_has_no_auto_memory():
    r=m.run_prefix(case(),0,'v',2)
    assert r['target_reviewed_clips']==0 and r['references_by_person']=={'A':3}
    assert r['frozen_state']['automatic_observations']==0
    assert r['later_bank_unchanged']


def test_missing_prefix_coverage_fails():
    c=case();c['additional_warmup']=c['additional_warmup'][:8]
    with pytest.raises(ValueError,match='coverage'):m.run_prefix(c,9,'v',2)


def test_duplicate_source_is_not_silently_substituted():
    c=case();c['additional_warmup'][1]=dict(c['additional_warmup'][0],id='duplicate')
    with pytest.raises(ValueError,match='Repeated source'):m.run_prefix(c,3,'v',2)


def test_unapproved_budget_is_rejected():
    with pytest.raises(ValueError,match='predeclared'):m.run_prefix(case(),2,'v',2)


def test_paired_wrong_regression_is_not_net_averaged_away():
    b=[{'id':'1','known':True,'gold_person':'A','assigned':'A'}, {'id':'2','known':True,'gold_person':'B','assigned':'A'}]
    c=[dict(b[0],assigned='B'),dict(b[1],assigned='B')]
    r=m.paired(c,b)
    assert r['newly_wrong_ids']==['1'] and r['rescued_correct_ids']==['2']
    assert r['safety_regression_observed']


def test_report_has_no_vectors():
    r=m.run_prefix(case(),3,'v',2)
    def check(v):
        if isinstance(v,dict):
            assert not {'embedding','vector','evidence'}.intersection(v)
            for x in v.values():check(x)
        elif isinstance(v,list):
            for x in v:check(x)
    check(r)
