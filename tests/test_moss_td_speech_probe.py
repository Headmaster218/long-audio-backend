"""Speech probes are analysis supports, never fabricated overlap detections."""
import copy,sqlite3
import pytest
from audio_backend.moss_td_candidates import plan_supports,parse_support
from audio_backend.overlap_ownership import own_candidates,validate_candidates
from audio_backend.overlap_candidates import validate_candidates as validate_transport
from audio_backend import overlap_store as store


def test_default_solo_unchanged_optional_probe_typed():
    turns=[dict(start=1,end=29,speaker_local_id='solo')]
    assert plan_supports(turns,30,{})==([],[])
    regions,plans=plan_supports(turns,30,{'moss_td_trigger':'speech_probe'})
    assert len(regions)==len(plans)==2
    assert all(r['region_kind']=='speech_support_probe' and r['detected_source_count']==1 for r in regions)
    assert all(r['support_activity']['distinct_local_speakers']==1 for r in regions)
    validate_candidates(dict(regions=regions,tracks=[]),30)
    validate_transport(dict(regions=regions,tracks=[]),30)


def test_zero_native_activity_never_probed():
    assert plan_supports([],30,{'moss_td_trigger':'speech_probe'})==([],[])


def test_sequential_labels_not_fabricated_concurrency():
    turns=[dict(start=0,end=10,speaker_local_id='a'),dict(start=10,end=20,speaker_local_id='b')]
    regions,plans=plan_supports(turns,20,{'moss_td_trigger':'speech_probe'})
    assert regions[0]['detected_source_count']==1
    assert regions[0]['support_activity']['distinct_local_speakers']==2
    assert plans[0]['native_peak_speakers']==1


def test_existing_overlap_support_decoded_once_without_extra_probe():
    turns=[dict(start=0,end=30,speaker_local_id='a'),dict(start=4,end=5,speaker_local_id='b')]
    regions,plans=plan_supports(turns,30,{'moss_td_trigger':'speech_probe'})
    assert len(plans)==2 and len(regions)==2
    assert [r['region_kind'] for r in regions]==['detected_overlap','speech_support_probe']
    assert len({p['shared_support_id']for p in plans})==2


def test_budget_probe_coverage_retained_and_boundary_owned_once():
    turns=[dict(start=0,end=60,speaker_local_id='a')]
    regions,plans=plan_supports(turns,60,{'moss_td_trigger':'speech_probe','moss_td_max_seconds':20})
    assert len(regions)==3 and len(plans)==1
    assert [r['reason_code']for r in regions]==['not_yet_attempted','analysis_budget_exhausted','analysis_budget_exhausted']
    assert sum(r['end']-r['start']for r in regions)==60
    tracks=parse_support(plans[0],[dict(speaker_local_id='S01',start=5,end=6,text='word')],8)
    assert len(own_candidates(dict(regions=regions,tracks=tracks),0,20)['tracks'])==1
    assert not own_candidates(dict(regions=regions,tracks=tracks),20,40)['tracks']


def test_probe_model_count_is_not_native_count_and_restart_idempotent():
    turns=[dict(start=0,end=20,speaker_local_id='solo')]
    regions,plans=plan_supports(turns,20,{'moss_td_trigger':'speech_probe'})
    tracks=parse_support(plans[0],[dict(speaker_local_id=f'S{i}',start=i,end=i+.5,text='x')for i in range(2)],8)
    regions[0]['model_label_count']=2
    payload=own_candidates(dict(regions=regions,tracks=tracks),0,20)
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT);INSERT INTO chunks VALUES("c","s");')
    store.init_schema(db)
    for _ in range(2):store.commit_candidates(db,dict(id='c',duration=20,timeline_start=0),0,copy.deepcopy(payload),{})
    assert db.execute('select count(*) from overlap_tracks').fetchone()[0]==2
    row=store.public_region(db.execute('select * from overlap_regions').fetchone())
    assert row['region_kind']=='speech_support_probe' and row['detected_source_count']==1 and row['model_label_count']==2
    assert row['support_activity']['distinct_local_speakers']==1
    for r in db.execute('select * from overlap_tracks'):
        public=store.public_track(r)
        assert public['speaker_id'] is None and public['conditioned_on_speaker_id'] is None
        assert public['provenance']['count_mismatch_uncertain']


@pytest.mark.parametrize('kind,count',[('detected_overlap',1),('speech_support_probe',0),('made_up',2),('speech_support_probe',True)])
def test_kind_count_validation(kind,count):
    regions,_=plan_supports([dict(start=0,end=20,speaker_local_id='a')],20,{'moss_td_trigger':'speech_probe'})
    regions[0].update(region_kind=kind,detected_source_count=count)
    for validate in [validate_candidates,validate_transport]:
        with pytest.raises(ValueError):validate(dict(regions=regions,tracks=[]),20)


def test_old_rows_migrate_to_detected_overlap():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT);INSERT INTO chunks VALUES("c","s");')
    store.init_schema(db)
    regions,_=plan_supports([dict(start=0,end=20,speaker_local_id=s)for s in ['a','b']],20,{})
    store.commit_candidates(db,dict(id='c',duration=20,timeline_start=0),0,dict(regions=regions,tracks=[]),{})
    db.execute('alter table overlap_regions drop column region_kind');db.execute('alter table overlap_regions drop column model_label_count')
    store.init_schema(db)
    row=store.public_region(db.execute('select * from overlap_regions').fetchone())
    assert row['region_kind']=='detected_overlap' and row['model_label_count'] is None
