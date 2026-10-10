"""Entirely synthetic partial-candidate tests; no real speech or model execution."""
import copy
import hashlib
import json
import sqlite3

import pytest

from audio_backend.inference.moss_td_cpp_asr import strict_result
from audio_backend.moss_td_recovery import partial_phrases, POLICY, TIMESTAMP_ERROR
from audio_backend.moss_td_candidates import partial_support, plan_supports
from audio_backend.overlap_candidates import validate_candidates
from audio_backend.overlap_ownership import own_candidates, validate_candidates as validate_unowned
from audio_backend import overlap_diagnostics, overlap_store

RAW = '[9][S8] PRIVATE REJECTED TAIL [10.01][3][S2] synthetic 中间 [4][1][S2] repeat [2][1][S2] repeat [2]'


def group(raw=RAW, frames=160000):
    result = strict_result(raw, frames / 16000)
    result['metadata'].update(runtime_provider='transcribe_cpp', inference_run=True,
                              duration_seconds=frames / 16000)
    result['support_id'] = 'support-0'
    return result


def recover(value=None, **kwargs):
    return partial_phrases(group() if value is None else value,
        **dict(frame_count=160000, sample_rate=16000, start_frame=0, capacity=8, **kwargs))


def bundle(raw=RAW, *, start=0, end=10, frames=160000):
    turns = [{'start':start,'end':end,'speaker_local_id':label} for label in ('a','b')]
    regions, plans = plan_supports(turns,end,{'candidate_core_start':start,'candidate_core_end':end})
    plan = plans[0]
    plan.update(start_frame=round(start*16000), frame_count=frames, sample_rate=16000)
    value = group(raw,frames)
    value['support_id'] = plan['shared_support_id']
    for region in regions:
        region.update(status='unresolved', reason_code=value['reason_code'],
            schema_error_count=len(value['schema_errors']), raw_output_sha256=hashlib.sha256(raw.encode()).hexdigest())
    tracks = partial_support(plan,value,8)
    return dict(regions=regions,tracks=tracks,metadata={},diagnostics=[overlap_diagnostics.support(plan,value)])


def test_exact_original_order_indexes_slots_and_strict_result_unchanged():
    value = group(); before = copy.deepcopy(value)
    rows, slots, provenance = recover(value)
    assert value == before and value['phrases'] == [] and value['status'] == 'unresolved'
    assert [r['native_phrase_index'] for r in rows] == [1,2,3]
    assert [(r['start'],r['end']) for r in rows] == [(3,4),(1,2),(1,2)]
    assert slots == {'S8':0,'S2':1}
    assert provenance['native_phrase_count'] == 4
    assert provenance['recovered_phrase_count'] == 3 and provenance['rejected_phrase_count'] == 1
    assert rows[0]['native_phrase_sha256'] == hashlib.sha256('[3][S2] synthetic 中间 [4]'.encode()).hexdigest()
    result = bundle(); validate_candidates(result,10); validate_unowned(result,10)
    assert [t['source_track_id'] for t in result['tracks']] == ['support-0-phrase-1','support-0-phrase-2','support-0-phrase-3']
    assert [t['source_slot'] for t in result['tracks']] == [1,1,1]
    assert all(t['provenance']['emitted_label_count'] == 2 for t in result['tracks'])
    assert all(t['quality_status'] == 'unverified' and t['reason_code'] == 'partial_support_schema_valid_phrase' for t in result['tracks'])
    assert result['regions'][0]['status'] == 'unresolved'
    assert result['regions'][0]['reason_code'] == TIMESTAMP_ERROR


def test_lightweight_grammar_matches_unchanged_strict_parser():
    from audio_backend.inference import moss_td_asr as strict
    from audio_backend import moss_td_recovery as recovery
    assert recovery._CANONICAL.pattern == strict._CANONICAL.pattern
    assert recovery._CANONICAL.flags == strict._CANONICAL.flags
    assert recovery._LABEL.pattern == strict._LABEL.pattern


@pytest.mark.parametrize('bad_end',['10.01','10.0101','10.000000000001','10.00000000000000000000000001','0','1'])
def test_bad_timestamp_never_clamped_or_promoted(bad_end):
    raw=f'[0][S0] valid [1][1][S1] rejected [{bad_end}][9][S2] known bad [11]'
    rows,slots,meta=recover(group(raw))
    assert [r['native_phrase_index'] for r in rows] == [0]
    assert rows[0]['end'] == 1 and meta['rejected_phrase_count'] == 2
    assert list(slots) == ['S0','S1','S2']


@pytest.mark.parametrize('bad',['[-1][S1] bad [2]','[NaN][S1] bad [2]',
    '[Inf][S1] bad [2]','[true][S1] bad [2]','[1][S1] bad [false]',
    '[1][X1] bad [11]','[1][S1234567890123456] bad [11]',
    '[1][S1]   [11]','[1][S1] \ud800 [11]','extra malformed text'])
def test_non_time_errors_and_hidden_empty_text_deny_all_recovery(bad):
    raw='[0][S0] good [1]'+bad
    value=group(raw)
    # Even maliciously narrowing the declared error list cannot bypass a full
    # raw-envelope/text/label rewalk, including the invalid timestamp row.
    value.update(status='unresolved',reason_code=TIMESTAMP_ERROR,schema_errors=[TIMESTAMP_ERROR],phrases=[])
    assert recover(value) is None


@pytest.mark.parametrize('key,value',[('inference_run',False),('inference_run',1),
    ('finish_reason','native_output_truncated'),('native_output_truncated',True),
    ('native_output_truncated',0),('token_limit_reached',True),('token_limit_reached',0),
    ('runtime_provider','transformers')])
def test_ineligible_receipts(key,value):
    source=group();source['metadata'][key]=value
    assert recover(source) is None
    source=group();del source['metadata'][key]
    assert recover(source) is None


@pytest.mark.parametrize('key,value',[('status','ok'),('phrases',[{'text':'not usable'}]),
    ('schema_errors',[TIMESTAMP_ERROR,'parser_envelope_mismatch']),('schema_errors',[]),
    ('schema_errors',[TIMESTAMP_ERROR,'generation_token_limit_reached']),('reason_code','different')])
def test_strict_unresolved_only(key,value):
    source=group();source[key]=value
    assert recover(source) is None


def test_label_receipts_count_capacity_and_all_invalid_are_fail_closed():
    value=group();value['metadata']['speaker_label_slots']={'S2':0}
    assert recover(value) is None
    value=group();value['metadata']['speaker_label_slots']={'S8':False,'S2':True}
    assert recover(value) is None
    value=group();value['metadata']['model_local_speaker_count']=1
    assert recover(value) is None
    value=group('[0][S0] good [1][1][S1] bad [11][1][S2] bad [11]')
    assert partial_phrases(value,frame_count=160000,sample_rate=16000,start_frame=0,capacity=2) is None
    assert recover(group('[1][S0] bad [11]')) is None
    assert recover(group('[0][S0] good [1]')) is None


@pytest.mark.parametrize('key,value',[('frame_count',True),('frame_count',0),('frame_count',320001),
    ('sample_rate',8000),('sample_rate',True),('start_frame',-1),('start_frame',960001),
    ('capacity',True),('capacity',1)])
def test_trusted_frame_arguments_are_bounded(key,value):
    args=dict(frame_count=160000,sample_rate=16000,start_frame=0,capacity=8);args[key]=value
    assert partial_phrases(group(),**args) is None


def test_exact_noninteger_frame_duration_and_full_support_slots():
    raw='[0][S0] exact [0.1000625][0][S1] tiny overshoot [0.100062500000000000001][0][S2] bad [1]'
    value=group(raw,1601)
    rows,slots,meta=partial_phrases(value,frame_count=1601,sample_rate=16000,start_frame=500,capacity=8)
    assert len(rows)==1 and rows[0]['end']==1601/16000
    assert meta['support_start_frame']==500 and meta['support_end_frame']==2101
    assert slots=={'S0':0,'S1':1,'S2':2}


def test_phrase_and_raw_byte_caps():
    assert recover(group('[0][S0] good [1]'*128+'[1][S1] bad [11]')) is None
    value=group('[0][S0] good [1]'*127+'[1][S1] bad [11]')
    assert len(recover(value)[0])==127
    assert recover(group('[0][S0] good [1][1][S1]'+'x'*10001+'[11]')) is None
    assert recover(group('[0][S0] good [1][1][S1]'+'🙂'*30000+'[11]')) is None
    assert recover(group('[0][S0] good [1][1][S1] bad ['+'1'*129+']')) is None


@pytest.mark.parametrize('key,value',[('native_phrase_index',True),('native_phrase_index',4),
    ('native_phrase_count',129),('rejected_phrase_count',0),('recovered_phrase_count',4),
    ('support_frame_count',160001),('support_start_frame',True),('support_end_frame',160001),
    ('support_sample_rate',True),('support_frame_axis','session_absolute'),
    ('original_timestamp_axis','session_absolute'),('timestamp_adjustment','clamped'),
    ('original_end_timestamp','10.000000000001'),('original_start_timestamp','NaN'),
    ('raw_output_sha256','not a hash'),('native_phrase_sha256','A'*64),
    ('recovery_policy','other'),('support_evidence_status','complete'),('support_parse_status','ok')])
def test_typed_public_provenance_rejects_invalid_fields(key,value):
    provenance=bundle()['tracks'][0]['provenance'];provenance[key]=value
    with pytest.raises(ValueError):overlap_store._provenance(provenance)


def test_provenance_all_required_fields_and_historical_rows_unchanged():
    provenance=bundle()['tracks'][0]['provenance']
    public=overlap_store._provenance(provenance)
    for key in overlap_store.RECOVERY_PUBLIC:
        assert public[key]==provenance[key]
        missing=copy.deepcopy(provenance);del missing[key]
        with pytest.raises(ValueError):overlap_store._provenance(missing)
    old={'association_basis':'support_not_model_timing','model_local_speaker_label':'S0'}
    assert overlap_store._provenance(old)==old
    assert overlap_store._provenance(dict(old,token_limit_reached=False))==old


@pytest.mark.parametrize('kind',['region_status','region_reason','region_hash','track_quality','track_reason',
    'changed_time','missing_provenance','claimed_identity','token_limit','empty_links','duplicate_links',
    'shifted_frames','changed_index'])
def test_partial_invariants_enforced_before_ownership_and_store(kind):
    payload=bundle();track=payload['tracks'][0];region=payload['regions'][0]
    if kind=='region_status':region['status']='unverified_hypothesis'
    elif kind=='region_reason':region['reason_code']='other'
    elif kind=='region_hash':region['raw_output_sha256']='0'*64
    elif kind=='track_quality':track['quality_status']='verified'
    elif kind=='track_reason':track['reason_code']='ordinary'
    elif kind=='changed_time':track['end']=4.01
    elif kind=='missing_provenance':track['provenance']={}
    elif kind=='claimed_identity':track['speaker_id']='person'
    elif kind=='token_limit':track['provenance']['token_limit_reached']=True
    elif kind=='empty_links':track['provenance']['related_overlap_group_ids']=[]
    elif kind=='duplicate_links':track['provenance']['related_overlap_group_ids']*=2
    elif kind=='shifted_frames':
        track['provenance']['support_start_frame']+=100;track['provenance']['support_end_frame']+=100
    elif kind=='changed_index':track['provenance']['native_phrase_index']=0
    for validator in (validate_candidates,validate_unowned):
        with pytest.raises(ValueError):validator(payload,10)


def test_support_ownership_keeps_whole_phrases_without_word_alignment():
    raw='[9][S8] bad [10.01][1][S2] shared [2][1.5][S3] concurrent [2.5]'
    payload=bundle(raw,start=2,end=12)
    owned=own_candidates(payload,2,12);validate_candidates(owned,10,owned=True)
    assert [(t['start'],t['end']) for t in owned['tracks']]==[(1,2),(1.5,2.5)]
    assert [t['source_slot'] for t in owned['tracks']]==[1,2]
    assert all(t['provenance']['support_start_frame']==32000 for t in owned['tracks'])
    assert all(t['provenance']['support_frame_axis']=='analyzer_window' for t in owned['tracks'])
    assert not own_candidates(payload,12,20)['tracks']


def analyze_fake(tmp_path,monkeypatch,outputs=None,*,frames=160000,config_extra=None):
    import numpy as np
    import soundfile as sf
    from audio_backend import moss_td_candidates as module
    wav=tmp_path/'synthetic.wav';sf.write(wav,np.zeros(frames,dtype='int16'),16000,subtype='PCM_16')
    def run(args,limits,**kwargs):
        request=json.loads(open(args[-1]).read())
        assert sf.info(request['supports'][0]['path']).frames==frames
        groups=[group(frames=frames)] if outputs is None else copy.deepcopy(outputs)
        if outputs is None:groups[0]['support_id']=request['supports'][0]['support_id']
        with open(request['output'],'w') as stream:json.dump({'supports':groups},stream)
    monkeypatch.setattr(module,'run_bounded',run)
    # Loader policy is tested independently. This model-free fake never loads a
    # library and only exercises the parent extraction/association path.
    monkeypatch.setattr(module,'reject_cpp_loader_overrides',lambda config:None)
    config=dict(data_dir=str(tmp_path/'data'),moss_td_python='/unused',moss_td_runtime='cpp')
    config.update(config_extra or {})
    turns=[{'start':0,'end':frames/16000,'speaker_local_id':x} for x in ('a','b')]
    return module.analyze_candidates(wav,turns,config)


def test_parent_extracts_actual_frames_preserves_diagnostics_and_original_errors(tmp_path,monkeypatch):
    payload=analyze_fake(tmp_path,monkeypatch)
    assert len(payload['tracks'])==3
    assert payload['tracks'][0]['provenance']['support_frame_count']==160000
    diagnostic=payload['diagnostics'][0]
    assert diagnostic['captures']['raw_text']['text']==RAW
    assert json.loads(diagnostic['captures']['schema_errors']['text'])==[TIMESTAMP_ERROR]
    assert json.loads(diagnostic['captures']['emitted_phrases']['text'])==[]
    assert payload['regions'][0]['reason_code']==TIMESTAMP_ERROR
    other=analyze_fake(tmp_path,monkeypatch,config_extra={'moss_td_runtime':'transformers'})
    assert not other['tracks']


@pytest.mark.parametrize('mode',['duplicate','unexpected','missing','malformed','truncated','mismatch'])
def test_batch_association_and_receipt_failure_never_expose_partial(tmp_path,monkeypatch,mode):
    value=group();outputs=[value]
    if mode=='duplicate':outputs.append(copy.deepcopy(value))
    elif mode=='unexpected':value['support_id']='not requested'
    elif mode=='missing':outputs=[]
    elif mode=='malformed':outputs=[None,value]
    elif mode=='truncated':value['metadata']['native_output_truncated']=True
    elif mode=='mismatch':value['schema_errors'].append('parser_envelope_mismatch')
    assert not analyze_fake(tmp_path,monkeypatch,outputs)['tracks']


def result_payload():
    return {'segments':[{'start':0,'end':2,'text':'original mixture','speaker_local_id':None}],
            'speaker_turns':[],'events':[],'metadata':{},'overlap_candidates':own_candidates(bundle(),0,10)}


def test_rest_feed_restart_privacy_identity_and_idempotence(tmp_path):
    from fastapi.testclient import TestClient
    from audio_backend.app import create_app
    from audio_backend.settings import Settings
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0)
    with TestClient(create_app(settings)) as client:
        store=client.app.state.store;sid=store.create_session('synthetic partial')['id']
        source=tmp_path/'uploads'/'source.audio';source.write_bytes(b'synthetic source')
        cid,_=store.add_chunk(sid,'key','digest',source,10,offset=100);chunk=store.claim()
        assert store.commit_window(chunk,0,10,result_payload())
        assert not store.commit_window(chunk,0,10,result_payload())
        rest=client.get(f'/sessions/{sid}/overlap-candidates').json()
        feed=client.get(f'/sessions/{sid}/changes').json()
        assert [r['data'] for r in feed['changes'] if r['entity']=='overlap_tracks']==rest['tracks']
        # Existing feed emits insert + bounded metadata updates in one commit.
        # Applying ordered upserts must produce exactly the REST projection.
        region_state={r['entity_id']:r['data'] for r in feed['changes'] if r['entity']=='overlap_regions'}
        assert list(region_state.values())==rest['regions']
        assert len(rest['tracks'])==3 and len(rest['regions'])==1
        assert rest['regions'][0]['status']=='unresolved'
        for track in rest['tracks']:
            assert track['candidate_only'] and track['identity_status']=='unresolved'
            assert track['speaker_id'] is None and not track['profile_update_eligible']
            assert track['provenance']['recovery_policy']==POLICY
            assert track['provenance']['support_frame_axis']=='analyzer_window'
            assert track['provenance']['support_start_frame']==0
            assert track['provenance']['native_time_axis']=='session_absolute'
        assert rest['tracks'][0]['start']==103 and rest['tracks'][0]['end']==104
        assert 'PRIVATE REJECTED TAIL' not in json.dumps(rest)+json.dumps(feed)
        assert client.get(f'/sessions/{sid}/overlap-diagnostics').status_code==404
        assert store.overlap_diagnostics(sid)['diagnostics'][0]['envelope']['captures']['raw_text']['text']==RAW
        assert [s['text'] for s in store.results(sid)['segments']]==['original mixture']
        with store.db() as db:
            for table in ('speakers','speaker_examples','speaker_observations'):
                assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
        cursor=feed['next_cursor']
    with TestClient(create_app(settings)) as client:
        assert client.get(f'/sessions/{sid}/overlap-candidates').json()==rest
        assert client.get(f'/sessions/{sid}/changes',params={'after':cursor}).json()['changes']==[]


@pytest.mark.parametrize('table',['overlap_support_diagnostics','overlap_regions','overlap_tracks',
                                 'overlap_track_regions','result_changes'])
def test_partial_atomic_rollback_all_storage_stages(tmp_path,table):
    from audio_backend.storage import Store
    from audio_backend.settings import Settings
    from audio_backend import change_feed
    store=Store(tmp_path/'test.sqlite',Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0))
    change_feed.initialize(store)
    sid=store.create_session('synthetic transaction')['id'];source=tmp_path/'source';source.write_bytes(b'synthetic')
    cid,_=store.add_chunk(sid,'key','digest',source,10);chunk=store.claim()
    tables=('overlap_support_diagnostics','overlap_regions','overlap_tracks','overlap_track_regions',
            'segments','speakers','speaker_examples','speaker_observations','result_changes')
    with store.db() as db:
        baseline={name:db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in tables}
        db.execute(f"CREATE TRIGGER synthetic_failure BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END")
    with pytest.raises(sqlite3.IntegrityError,match='synthetic rollback'):
        store.commit_window(chunk,0,10,result_payload())
    assert store.chunk(cid)['next_offset']==0
    with store.db() as db:
        for name in tables:assert db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]==baseline[name]
        db.execute('DROP TRIGGER synthetic_failure')
    assert store.commit_window(chunk,0,10,result_payload())
    assert len(store.overlap_candidates(sid)['tracks'])==3
    assert not store.commit_window(chunk,0,10,result_payload())


def test_partial_conflicting_diagnostic_retry_retains_first_evidence():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT);INSERT INTO chunks VALUES("c","s");')
    overlap_store.init_schema(db);chunk=dict(id='c',duration=10,timeline_start=0)
    original=own_candidates(bundle(),0,10)
    overlap_store.commit_candidates(db,chunk,0,original,{})
    before=list(db.execute('SELECT envelope_json FROM overlap_support_diagnostics'))[0][0]
    changed=own_candidates(bundle(RAW.replace('PRIVATE REJECTED TAIL','CHANGED PRIVATE TAIL')),0,10)
    with pytest.raises(ValueError,match='Conflicting diagnostic retry'):
        overlap_store.commit_candidates(db,chunk,0,changed,{})
    assert db.execute('SELECT envelope_json FROM overlap_support_diagnostics').fetchone()[0]==before
    assert db.execute('SELECT COUNT(*) FROM overlap_tracks').fetchone()[0]==3
    db.close()


def test_three_partial_supports_cannot_exceed_256_track_envelope(tmp_path,monkeypatch):
    import numpy as np
    import soundfile as sf
    from audio_backend import moss_td_candidates as module
    wav=tmp_path/'bounded.wav';sf.write(wav,np.zeros(60*16000,dtype='int16'),16000,subtype='PCM_16')
    def run(args,limits,**kwargs):
        request=json.loads(open(args[-1]).read());outputs=[]
        assert len(request['supports'])==3
        for support in request['supports']:
            value=group('[0][S0] synthetic [1]'*127+'[19][S1] private invalid [21]',320000)
            value['support_id']=support['support_id'];outputs.append(value)
        with open(request['output'],'w') as stream:json.dump({'supports':outputs},stream)
    monkeypatch.setattr(module,'run_bounded',run)
    monkeypatch.setattr(module,'reject_cpp_loader_overrides',lambda config:None)
    payload=module.analyze_candidates(wav,[{'start':0,'end':60,'speaker_local_id':x} for x in ('a','b')],
        dict(data_dir=str(tmp_path/'data'),moss_td_python='/unused',moss_td_runtime='cpp',moss_td_max_seconds=60))
    assert not payload['tracks'] and len(payload['diagnostics'])==4
    assert all(r['reason_code']=='candidate_schema_failure' for r in payload['regions'])


def test_partial_envelope_two_mib_cap_and_optional_dependency_isolation():
    import os
    import subprocess
    import sys
    payload=bundle();payload['metadata']['synthetic_padding']='x'*(2*1024**2)
    with pytest.raises(ValueError,match='2MiB'):validate_candidates(payload,10)
    with pytest.raises(ValueError,match='2MiB'):validate_unowned(payload,10)
    script='''import sys
class RejectOptional:
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'numpy','soundfile'}:
            raise ImportError('optional inference module must not be imported')
sys.meta_path.insert(0,RejectOptional())
import audio_backend.overlap_store
'''
    completed=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,
                             timeout=10,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert completed.returncode==0,completed.stderr
