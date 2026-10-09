import copy,json,sqlite3
from contextlib import contextmanager
import pytest
from audio_backend import overlap_store as store


def payload():
    region={'overlap_group_id':'group-0','start':.2,'end':1.5,'support_start':0.,'support_end':2.,
            'detected_source_count':2,'detected_speaker_local_ids':['a','b'],'capacity':2,
            'status':'candidate_tracks_unverified','original_mixture_retained':True}
    tracks=[]
    for slot in range(2):
        tracks.append({'source_track_id':f'group-0:scope-0:source-{slot}','overlap_group_id':'group-0',
            'scope_id':'scope-0','source_slot':slot,'start':.3,'end':1.4,'support_start':0.,'support_end':2.,
            'text':'yes','raw_text':'yes','identity_status':'unresolved','quality_status':'unverified',
            'speaker_id':None,'profile_update_eligible':False,'provenance':{'model_id':'test',
                'audio_path':'/private/wav','embedding':[1.,0.],'model_revision':'abc'}})
    return {'regions':[region],'tracks':tracks,'metadata':{}}


@pytest.fixture
def db():
    connection=sqlite3.connect(':memory:');connection.row_factory=sqlite3.Row
    connection.executescript('PRAGMA foreign_keys=ON;CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT);INSERT INTO chunks VALUES("chunk","session");')
    store.init_schema(connection);yield connection;connection.close()


def test_durable_candidate_tracks_distinct_idempotent_private_safe(db):
    chunk={'id':'chunk','timeline_start':10.,'duration':4.}
    store.commit_candidates(db,chunk,0.,payload(),{})
    store.commit_candidates(db,chunk,0.,payload(),{})
    rows=db.execute('SELECT * FROM overlap_tracks ORDER BY id').fetchall()
    assert len(rows)==2 and rows[0]['source_track_id']!=rows[1]['source_track_id']
    assert [r['text'] for r in rows]==['yes','yes'] and rows[0]['start']==10.3
    output=store.public_track(rows[0]);encoded=json.dumps(output)
    assert '/private' not in encoded and 'embedding' not in encoded
    assert output['speaker_id'] is None and not output['enrollment_available']
    store.commit_candidates(db,chunk,2.,payload(),{})
    assert db.execute('SELECT COUNT(DISTINCT source_track_id) FROM overlap_tracks').fetchone()[0]==4
    db.execute('DELETE FROM chunks WHERE id="chunk"')
    assert db.execute('SELECT COUNT(*) FROM overlap_tracks').fetchone()[0]==0
    assert db.execute('SELECT COUNT(*) FROM overlap_regions').fetchone()[0]==0


def test_public_pagination_keeps_source_pairs_together(db):
    chunk={'id':'chunk','timeline_start':0.,'duration':4.}
    store.commit_candidates(db,chunk,0.,payload(),{});store.commit_candidates(db,chunk,2.,payload(),{})
    class S:
        def session(self,sid):assert sid=='session'
        @contextmanager
        def db(self):yield db
    first=store.candidates(S(),'session',0,1)
    assert len(first['regions'])==1 and len(first['tracks'])==2
    second=store.candidates(S(),'session',first['next_cursor'],1)
    assert len(second['regions'])==1 and second['regions'][0]['id']!=first['regions'][0]['id']


def test_owned_native_word_crossing_window_keeps_unclipped_times(db):
    p=payload();p['regions'][0].update(start=0.,end=1.,support_start=-2.,support_end=2.)
    for t in p['tracks']:t.update(start=-.2,end=.3,support_start=-2.,support_end=2.)
    chunk={'id':'chunk','timeline_start':10.,'duration':4.}
    store.commit_candidates(db,chunk,2.,p,{})
    row=db.execute('SELECT * FROM overlap_tracks LIMIT 1').fetchone()
    assert row['start']==11.8 and row['end']==12.3 and row['support_start']==10.


def test_missing_enrollment_evidence_is_persisted_without_vectors(db):
    p=payload();p['tracks']=[];p['regions'][0].update(status='needs_more_evidence',reason_code='insufficient_nonoverlap_enrollment',
        conditioning_evidence=[{'conditioning_local_id':'a','reason_code':'insufficient_nonoverlap_enrollment','enrollment_seconds':.4,'embedding':[1.,0.]}])
    store.commit_candidates(db,{'id':'chunk','timeline_start':0.,'duration':2.},0.,p,{})
    row=store.public_region(db.execute('SELECT * FROM overlap_regions').fetchone())
    assert {k:row['conditioning_evidence'][0][k] for k in ('conditioning_local_id','reason_code','enrollment_seconds')}=={'conditioning_local_id':'a','reason_code':'insufficient_nonoverlap_enrollment','enrollment_seconds':.4}
    assert 'embedding' not in row['conditioning_evidence'][0]
    assert row['reason_code']=='insufficient_nonoverlap_enrollment'


def test_shared_support_phrase_stored_once_linked_from_each_region(db):
    p=payload();first=p['regions'][0];first.update(shared_support_id='support-0')
    second=copy.deepcopy(first);second.update(overlap_group_id='group-1',start=1.5,end=1.8,status='candidate_evidence_shared')
    p['regions'].append(second)
    for track in p['tracks']:
        track['provenance'].update(shared_support_id='support-0',related_overlap_group_ids=['group-0','group-1'])
    store.commit_candidates(db,{'id':'chunk','timeline_start':0.,'duration':2.},0.,p,{})
    assert db.execute('SELECT COUNT(*) FROM overlap_tracks').fetchone()[0]==2
    assert db.execute('SELECT COUNT(*) FROM overlap_track_regions').fetchone()[0]==4
    class S:
        def session(self,sid):assert sid=='session'
        @contextmanager
        def db(self):yield db
    first_page=store.candidates(S(),'session',0,1)
    second_page=store.candidates(S(),'session',first_page['next_cursor'],1)
    assert {x['id'] for x in first_page['tracks']}=={x['id'] for x in second_page['tracks']}
    all_rows=store.candidates(S(),'session',0,10)
    assert len(all_rows['regions'])==2 and len(all_rows['tracks'])==2
    assert all_rows['regions'][0]['shared_support_id']=='chunk@0.000000/support-0'
    assert all_rows['tracks'][0]['provenance']['related_overlap_group_ids']==['chunk@0.000000/group-0','chunk@0.000000/group-1']
    plan=' '.join(str(tuple(r)) for r in db.execute('EXPLAIN QUERY PLAN SELECT t.* FROM overlap_tracks t JOIN overlap_track_regions l ON l.track_id=t.id WHERE l.region_id=?',(1,)))
    assert 'overlap_track_regions_lookup' in plan
    db.execute('DELETE FROM chunks WHERE id="chunk"')
    assert db.execute('SELECT COUNT(*) FROM overlap_track_regions').fetchone()[0]==0


def test_conditioned_global_hint_never_becomes_verified_track_identity(db):
    p=payload()
    p['tracks'][0].update(conditioning_local_id='raw-target-a')
    p['tracks'][0]['provenance'].update(conditioning_local_id='raw-target-a',conditioning_source='provided_local_diarization')
    p['regions'][0]['conditioning_evidence']=[{'conditioning_local_id':'raw-target-a','reason_code':'example','enrollment_seconds':3.}]
    store.commit_candidates(db,{'id':'chunk','timeline_start':10.,'duration':4.},2.,p,{},
        resolved_local_identities={'raw-target-a':{'speaker_id':'global-person-a','speaker_status':'matched','speaker_turn_ids':[7,9]}})
    rows=[store.public_track(r) for r in db.execute('SELECT * FROM overlap_tracks ORDER BY id')]
    hint=rows[0]['conditioning_evidence']
    assert hint['conditioning_local_id']=='raw-target-a' and hint['chunk_id']=='chunk' and hint['speaker_window_start']==2.
    assert rows[0]['conditioned_on_speaker_id']=='global-person-a'
    assert hint['speaker_turn_ids']==[7,9]
    assert rows[0]['speaker_id'] is None and rows[0]['identity_status']=='unresolved'
    assert not hint['independently_verified_source_identity'] and not rows[0]['profile_update_eligible']
    assert rows[1]['conditioning_evidence']=={} and rows[1]['conditioned_on_speaker_id'] is None
    region=store.public_region(db.execute('SELECT * FROM overlap_regions').fetchone())
    assert region['conditioning_evidence'][0]['speaker_window_start']==2.


def test_native_phrase_provenance_keeps_exact_absolute_extent(db):
    p=payload();p['tracks'][0]['provenance'].update(native_unit_kind='native_phrase',native_time_axis='core_relative',
        native_phrase_intervals=[{'start':-.2,'end':1.7,'ownership_anchor':.1,'text':'a whole native phrase',
            'unit_kind':'native_phrase','ownership_anchor_kind':'native_start_with_uncertain_boundary','boundary_uncertain':True}])
    store.commit_candidates(db,{'id':'chunk','timeline_start':10.,'duration':4.},2.,p,{})
    row=store.public_track(db.execute('SELECT * FROM overlap_tracks ORDER BY id LIMIT 1').fetchone())
    evidence=row['provenance'];unit=evidence['native_phrase_intervals'][0]
    assert unit['start']==11.8 and unit['end']==13.7 and unit['ownership_anchor']==12.1
    assert unit['text']=='a whole native phrase' and unit['boundary_uncertain'] is True
    assert evidence['native_time_axis']=='session_absolute'


def test_support_capacity_details_remain_public_without_source_tracks(db):
    p=payload();p['tracks']=[];p['regions'][0].update(status='support_capacity_exceeded',
        reason_code='recognition_support_exceeds_two_concurrent_sources',
        support_activity={'max_simultaneous_speakers':3,'distinct_local_speakers':3,
            'speaker_local_ids':['a','b','c'],'identity_scope':'analysis_window_local'})
    store.commit_candidates(db,{'id':'chunk','timeline_start':0.,'duration':2.},0.,p,{})
    row=store.public_region(db.execute('SELECT * FROM overlap_regions').fetchone())
    assert row['status']=='support_capacity_exceeded' and row['support_activity']['max_simultaneous_speakers']==3
    assert row['support_activity']['speaker_local_ids']==['a','b','c']
    assert db.execute('SELECT COUNT(*) FROM overlap_tracks').fetchone()[0]==0
