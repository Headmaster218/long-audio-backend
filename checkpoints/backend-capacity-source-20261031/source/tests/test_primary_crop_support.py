"""Finite synthetic tests; no models, servers, external files or subprocesses.

The unit geometry is not authority. Integration cases obtain the optional
result only through the real immutable packet/certificate reader and Store.
"""
from copy import deepcopy
import json
import sqlite3
import struct

import pytest

from audio_backend import primary_crop_support as crops
from audio_backend import primary_voice_references as voices
from audio_backend import recording_primary_provenance as provenance
from audio_backend import reviewed_primary_persons as bridge
from audio_backend import speaker_support, verified_identity as bank
from reviewed_person_bridge_fixtures import GROUP, POLICY, env, count, rows, activation_request
from test_primary_voice_references import enroll, snapshot, overwrite_certificate, original_vector


def geometry(*, logical_origin=100003, physical_origin=123):
    lo=logical_origin
    part={'logical_start':lo,'logical_end':lo+64000,
          'source_start':physical_origin,'source_end':physical_origin+64000,
          'source_frames':physical_origin+64000,'source_sha256':'a'*64}
    start,end=lo+16000,lo+48000
    span=dict(part,logical_start=start,logical_end=end,
              source_start=physical_origin+16000,source_end=physical_origin+48000)
    packet={'commit':{'scope':{'origin':(7.25).hex()},'part':part},
            'labels':['speaker-a','speaker-b'],
            'turns':[[0,0,lo,lo+64000,0.,4.],
                     [1,1,lo+50000,lo+60000,3.125,3.75]]}
    node={'native_label':'speaker-a','start_frame':start,'end_frame':end,'source_spans':[span]}
    return packet,node


def test_nonzero_geometry_and_packet_external_overlap_are_supported():
    packet,node=geometry()
    value=crops.derive(packet,node)
    assert value=={'policy':crops.POLICY,'extent':'native_metadata_only',
        'acoustic_purity_certified':False,'start_frame':116003,'end_frame':148003,
        'erosion_frames':1600}
    # The display origin must not change frame geometry.
    packet['commit']['scope']['origin']=(1234.5).hex()
    assert crops.derive(packet,node)==value


@pytest.mark.parametrize('delta,allowed',[(0,True),(-1,False)])
def test_erosion_margin_boundary_and_one_frame_intrusion(delta,allowed):
    packet,node=geometry()
    packet['turns'][1][2]=node['end_frame']+crops.EROSION_FRAMES+delta
    assert (crops.derive(packet,node) is not None)==allowed


@pytest.mark.parametrize('damage',[
    'intersection','gap','unknown-label','unknown-target','duplicate-label',
    'reversed-turn','bool-frame','short-crop','mapping','source-bound',
    'missing-turns','missing-span','missing-margin',
])
def test_unproved_or_malformed_crop_is_refused(damage):
    packet,node=geometry();lo=packet['commit']['part']['logical_start']
    if damage=='intersection':packet['turns'][1][2]=node['start_frame']
    elif damage=='gap':packet['turns'][0][3]=node['end_frame']-1
    elif damage=='unknown-label':packet['turns'][1][1]=None
    elif damage=='unknown-target':node['native_label']='absent'
    elif damage=='duplicate-label':packet['labels'][1]=packet['labels'][0]
    elif damage=='reversed-turn':packet['turns'][0][3]=lo
    elif damage=='bool-frame':packet['turns'][0][2]=True
    elif damage=='short-crop':node['end_frame']-=1
    elif damage=='mapping':node['source_spans'][0]['source_start']+=1
    elif damage=='source-bound':packet['commit']['part']['source_frames']=1
    elif damage=='missing-turns':packet['turns']=[]
    elif damage=='missing-span':node['source_spans']=[]
    elif damage=='missing-margin':node['start_frame']=lo
    assert crops.derive(packet,node) is None


def activate_crop(fixture):
    policy=dict(POLICY,schema_version=2,primary_query_overlap_policy=crops.POLICY)
    fixture.settings.verified_identity_policy=policy
    request=activation_request()
    request['policy_sha256']=speaker_support.digest(speaker_support.canonical(policy))
    return bridge.activate_bank(fixture.store,GROUP,request)


def test_opt_in_scores_clean_crop_with_external_overlap_without_enrollment(env):
    activate_crop(env)
    _,claim,_,_=enroll(env)
    item=env.observe(group=GROUP,vector=True,packet_overlap=True)
    before=snapshot(env)
    with env.store.db() as db:
        db.execute('BEGIN')
        plain=bridge._binding(db,item.sid,item.rid,item.observation_id)
        derived=bridge._binding(db,item.sid,item.rid,item.observation_id,include_native_crop_support=True)
    assert plain['binding']==derived['binding'] and plain['binding_sha256']==derived['binding_sha256']
    assert '_native_crop_support' not in plain
    assert derived['_native_crop_support']['policy']==crops.POLICY
    assert voices.process_one(env.store)['status']=='experimental_identity_match'
    value=voices.suggestion(env.store,item.sid,item.rid,item.observation_id)
    assert value['person_id']==claim['person_id']
    assert value['primary_query_overlap_policy']==crops.POLICY
    assert value['packet_overlap_witness_count']==1
    assert value['acoustic_purity_certified'] is False and value['automatic_enrollment'] is False
    after=snapshot(env)
    assert {k:v for k,v in before.items() if k!='suggestions'}=={k:v for k,v in after.items() if k!='suggestions'}


def test_opt_in_eligibility_does_not_manufacture_reference_or_match(env):
    activate_crop(env)
    item=env.observe(group=GROUP,vector=True,packet_overlap=True)
    assert voices.process_one(env.store)['status']=='unknown_no_reviewed_references'
    assert voices.suggestion(env.store,item.sid,item.rid,item.observation_id)['person_id'] is None
    assert count(env.store,voices.TABLE)==0


@pytest.mark.parametrize('fault',['missing-packet','certificate-bytes','source-revoked','source-deleted'])
def test_opt_in_still_refuses_missing_revoked_deleted_or_tampered_proof(env,monkeypatch,fault):
    activate_crop(env);enroll(env)
    item=env.observe(group=GROUP,vector=True,packet_overlap=True)
    if fault=='missing-packet':
        monkeypatch.setattr(provenance,'read_metadata_binding',lambda *a,**k:None)
    elif fault=='certificate-bytes':
        overwrite_certificate(env,item,vector=struct.pack('<512f',*original_vector(0,1)))
    elif fault=='source-revoked':
        from audio_backend import recording_speaker_history as history
        with env.store.db() as db:
            db.execute('BEGIN')
            db.execute(f"UPDATE {history.SOURCES} SET state='revoked' WHERE version=?",(item.source['version'],))
    else:
        with env.store.db() as db:
            db.execute('BEGIN');db.execute('UPDATE sessions SET deleted=1 WHERE id=?',(item.sid,))
    before=count(env.store,voices.TABLE)
    result=voices.process_one(env.store)
    assert result.get('status')!='experimental_identity_match'
    try:value=voices.suggestion(env.store,item.sid,item.rid,item.observation_id)
    except bridge.BridgeError:pass
    else:assert value['person_id'] is None
    assert count(env.store,voices.TABLE)==before


def test_immutable_bank_prevents_policy_switch_and_old_job_reuse(env):
    env.activate()
    item=env.observe(group=GROUP,vector=True,packet_overlap=True)
    assert voices.process_one(env.store)['status']=='unknown_packet_overlap'
    old=rows(env.store,voices.SUGGESTIONS)
    new=dict(POLICY,schema_version=2,primary_query_overlap_policy=crops.POLICY)
    env.settings.verified_identity_policy=new
    request={**env.fence(), 'mode':bank.MODE,'confirmed':True,
             'policy_sha256':speaker_support.digest(speaker_support.canonical(new))}
    with pytest.raises(bridge.BridgeError):bridge.activate_bank(env.store,GROUP,request)
    with env.store.db() as db,pytest.raises(sqlite3.IntegrityError):
        db.execute('BEGIN');db.execute('UPDATE identity_group_policy SET policy=? WHERE identity_group=?',(json.dumps(new),GROUP))
    assert rows(env.store,voices.SUGGESTIONS)==old
    assert voices.suggestion(env.store,item.sid,item.rid,item.observation_id)['status']=='unknown_packet_overlap'
    other='new-crop-policy-bank'
    request=activation_request();request['policy_sha256']=speaker_support.digest(speaker_support.canonical(new))
    bridge.activate_bank(env.store,other,request)
    fresh=env.observe(group=other,vector=True,packet_overlap=True)
    assert voices.process_one(env.store)['status']=='unknown_no_reviewed_references'
    assert rows(env.store,voices.SUGGESTIONS)[0]==old[0]
    assert voices.suggestion(env.store,fresh.sid,fresh.rid,fresh.observation_id)['primary_query_overlap_policy']==crops.POLICY


@pytest.mark.parametrize('damage',['missing-version','schema1-extra','unknown-policy','bad-threshold','bad-duration'])
def test_opt_in_policy_file_keeps_limits_and_exact_version(tmp_path,damage):
    value=dict(POLICY,schema_version=2,primary_query_overlap_policy=crops.POLICY)
    if damage=='missing-version':del value['primary_query_overlap_policy']
    elif damage=='schema1-extra':value['schema_version']=1
    elif damage=='unknown-policy':value['primary_query_overlap_policy']='allow-anything'
    elif damage=='bad-threshold':value['single_threshold']=-.1
    else:value['minimum_query_seconds']=21
    path=tmp_path/'policy.json';path.write_text(json.dumps(value))
    with pytest.raises(ValueError):bank.policy_file(path)


def test_both_policy_file_schemas_are_explicit_and_numeric_rules_unchanged(tmp_path):
    path=tmp_path/'policy.json'
    path.write_text(json.dumps(POLICY));assert bank.policy_file(path)==POLICY
    value=dict(POLICY,schema_version=2,primary_query_overlap_policy=crops.POLICY)
    path.write_text(json.dumps(value));assert bank.policy_file(path)==value
    for scores in ([(.5,'a',['x'])],[(.9,'a',['x']),(.85,'b',['y'])]):
        assert bank.select_reviewed_score(scores,value)==bank.select_reviewed_score(scores,POLICY)
# Append to tests/test_primary_crop_support.py in a new candidate revision.
# Source-only review draft; no execution performed here.


def test_opt_in_exact_negative_still_vetoes_high_positive_score(env):
    from test_primary_voice_references import legacy_support_from_crop, legacy_request
    activate_crop(env)
    _,claim,_,_=enroll(env)
    item=env.observe(pcm=True,vector=True,group=GROUP,packet_overlap=True)
    legacy=legacy_support_from_crop(env,item)
    negative=bank.mutate(env.store,legacy.sid,
        legacy_request(env,legacy,claim['person_id'],kind='negative'))
    with env.store.db() as db:
        db.execute('BEGIN');voices.enqueue(db,item.sid,item.rid,item.observation_id)
    before=snapshot(env)
    assert voices.process_one(env.store)['status']=='stale'
    assert voices.process_one(env.store)['status']=='unknown_exact_negative'
    result=voices.suggestion(env.store,item.sid,item.rid,item.observation_id)
    assert result['primary_query_overlap_policy']==crops.POLICY
    assert result['status']=='unknown_exact_negative' and result['person_id'] is None
    assert result['candidate_person_id']==claim['person_id'] and result['score']==pytest.approx(1.)
    assert result['negative_event_id']==negative['event_id']
    after=snapshot(env)
    assert {k:v for k,v in before.items() if k!='suggestions'}=={k:v for k,v in after.items() if k!='suggestions'}


def test_opt_in_capacity_refuses_before_gallery_materialization(env,monkeypatch):
    activate_crop(env);enroll(env)
    item=env.observe(group=GROUP,vector=True,packet_overlap=True)
    monkeypatch.setattr(voices,'MAX_QUERY_REFERENCES',0)
    def forbidden(*a,**k):
        raise AssertionError('Gallery must not materialize above the query ceiling')
    monkeypatch.setattr(bank,'active_reference_rows',forbidden)
    before=snapshot(env)
    assert voices.process_one(env.store)['status']=='unknown_gallery_capacity'
    result=voices.suggestion(env.store,item.sid,item.rid,item.observation_id)
    assert result['primary_query_overlap_policy']==crops.POLICY and result['person_id'] is None
    after=snapshot(env)
    assert {k:v for k,v in before.items() if k!='suggestions'}=={k:v for k,v in after.items() if k!='suggestions'}


def test_opt_in_conflicting_manual_claim_vetoes_high_score_without_relabel(env):
    activate_crop(env)
    _,first,_,_=enroll(env)
    item=env.observe(group=GROUP,vector=True,packet_overlap=True)
    other=env.associate(item,name='Different synthetic person')
    assert other['person_id']!=first['person_id']
    with env.store.db() as db:
        db.execute('BEGIN');voices.enqueue(db,item.sid,item.rid,item.observation_id)
    before=snapshot(env)
    assert voices.process_one(env.store)['status']=='stale'
    assert voices.process_one(env.store)['status']=='unknown_conflicting_manual_claim'
    result=voices.suggestion(env.store,item.sid,item.rid,item.observation_id)
    assert result['primary_query_overlap_policy']==crops.POLICY and result['person_id'] is None
    assert result['candidate_person_id']==first['person_id'] and result['score']==pytest.approx(1.)
    after=snapshot(env)
    assert {k:v for k,v in before.items() if k!='suggestions'}=={k:v for k,v in after.items() if k!='suggestions'}


@pytest.mark.parametrize('fault',['missing-version','legacy-version','missing-hash','wrong-hash'])
def test_opt_in_cached_outcome_policy_corruption_is_stale_without_recompute(env,monkeypatch,fault):
    activate_crop(env);enroll(env)
    item=env.observe(group=GROUP,vector=True,packet_overlap=True)
    assert voices.process_one(env.store)['status']=='experimental_identity_match'
    expected=voices.suggestion(env.store,item.sid,item.rid,item.observation_id)
    assert expected['primary_query_overlap_policy']==crops.POLICY
    damaged=deepcopy(expected)
    if fault=='missing-version':del damaged['primary_query_overlap_policy']
    elif fault=='legacy-version':damaged['primary_query_overlap_policy']=crops.LEGACY_POLICY
    elif fault=='missing-hash':del damaged['query_policy_sha256']
    else:damaged['query_policy_sha256']='f'*64
    # Damage only this private synthetic completed row, then restore its exact
    # production immutability guard. No caller can normally make this update.
    with env.store.db() as db:
        db.execute('BEGIN')
        trigger='primary_voice_outcome_immutable'
        original=db.execute('SELECT sql FROM sqlite_master WHERE name=?',(trigger,)).fetchone()[0]
        db.execute('DROP TRIGGER '+trigger)
        db.execute(f'UPDATE {voices.SUGGESTIONS} SET outcome=? WHERE observation_id=?',
                   (json.dumps(damaged),item.observation_id))
        db.execute(original)
    before=snapshot(env)
    def no_recompute(*a,**k):raise AssertionError('Reading a cached result must not recompute')
    monkeypatch.setattr(voices,'_compute',no_recompute)
    result=voices.suggestion(env.store,item.sid,item.rid,item.observation_id)
    assert result['status']=='stale' and result['person_id'] is None
    assert snapshot(env)==before
