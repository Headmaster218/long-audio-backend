import json
import pytest
from audio_backend.event_candidate_store import init_schema,commit_candidates,candidates,MAX_PAYLOAD_BYTES,MAX_PROVENANCE_BYTES
from audio_backend.storage import Store
from audio_backend.settings import Settings

@pytest.fixture
def setup(tmp_path):
    store=Store(tmp_path/'db',Settings(data_dir=tmp_path,start_worker=False))
    with store.db() as db:init_schema(db)
    sid=store.create_session('candidates')['id'];cid,_=store.add_chunk(sid,'one','one',tmp_path/'one.wav',60)
    return store,sid,store.chunk(cid)

def sample(**changes):
    result=dict(start=0.,end=3.,support_start=-2.,support_end=3.,time_axis='core_relative',ownership='core_intersection',top_labels=[{'label':'Creak','score':.61},{'label':'Door','score':.02}],category_scores={'door':{'score':.02,'source_label':'Door'}},speech_score=.9,rms=.03,peak=.2)
    return dict(result,**changes)

def put(store,chunk,offset,rows,meta=None):
    with store.db() as db:return commit_candidates(db,chunk,offset,rows,meta or {'models':{'events':'Zipformer'},'event_window_seconds':5.,'event_hop_seconds':2.5,'effective_config':{'event_threshold':.25}})

def test_support_and_candidate_status(setup):
    store,sid,chunk=setup;assert put(store,chunk,30,[sample()])==1
    row=candidates(store,sid)['event_candidates'][0]
    assert (row['start'],row['end'],row['support_start'],row['support_end'])==(30,33,28,33)
    assert row['status']=='unverified_model_candidate' and row['onset_precision_seconds'] is None
    assert row['time_precision']=='window_support' and row['scores_calibrated'] is False
    assert row['top_labels'][0]['label']=='Creak' and row['category_scores']['door']['score']==.02
    assert store.events(sid)['events']==[]
    assert row['provenance']['event_model']=='Zipformer'

def test_retry_is_idempotent_and_does_not_rewrite_evidence(setup):
    store,sid,chunk=setup;put(store,chunk,30,[sample()]);before=candidates(store,sid)['event_candidates'][0]
    assert put(store,chunk,30,[sample(top_labels=[{'label':'Music','score':.99}])])==0
    after=candidates(store,sid)['event_candidates'][0];assert after==before
    assert put(store,chunk,35,[sample()])==1
    assert len(candidates(store,sid)['event_candidates'])==2

def test_pagination_and_session_isolation_and_cascade(setup):
    store,sid,chunk=setup
    for offset in [5,10,15]:put(store,chunk,offset,[sample()])
    first=candidates(store,sid,limit=2);assert len(first['event_candidates'])==2 and first['has_more']
    second=candidates(store,sid,after=first['next_cursor'],limit=2);assert len(second['event_candidates'])==1 and not second['has_more']
    empty=candidates(store,sid,after=second['next_cursor']);assert empty['event_candidates']==[] and empty['next_cursor']==second['next_cursor']
    other=store.create_session('other')['id'];assert candidates(store,other)['event_candidates']==[]
    with store.db() as db:db.execute('DELETE FROM chunks WHERE id=?',(chunk['id'],))
    assert candidates(store,sid)['event_candidates']==[]

def test_bounded_top_labels_and_provenance(setup):
    store,sid,chunk=setup;labels=[{'label':f'label{i}','score':i/20} for i in range(20)]+[{'label':'Female speech, woman speaking','score':1.}]
    put(store,chunk,5,[sample(top_labels=labels)],{'models':{'events':'x'*1000,'asr':'secret-unused'},'event_model_sha256':'a'*64,'irrelevant':{'bulk':'x'*100000}})
    row=candidates(store,sid)['event_candidates'][0]
    assert len(row['top_labels'])==10 and row['top_labels'][0]['label']=='label19'
    assert row['top_labels_truncated'] and row['provenance']['provenance_truncated']
    assert len(row['provenance']['event_model'])==256 and 'irrelevant' not in row['provenance']
    with store.db() as db:
        raw=db.execute('SELECT payload,provenance FROM event_candidates').fetchone()
        assert len(raw['payload'].encode())<=MAX_PAYLOAD_BYTES and len(raw['provenance'].encode())<=MAX_PROVENANCE_BYTES

@pytest.mark.parametrize('change',[{'start':-1},{'end':61},{'support_start':1},{'support_end':2},{'end':float('nan')},{'time_axis':'input_relative'},{'category_scores':{'extra':{'score':.3,'source_label':'x'}}},{'top_labels':[{'label':'Creak','score':1.1}]}])
def test_bad_payload_is_rejected_atomically(setup,change):
    store,sid,chunk=setup
    with pytest.raises(ValueError):put(store,chunk,5,[sample(),sample(**change)])
    assert candidates(store,sid)['event_candidates']==[]

def test_limits_and_rollback(setup):
    store,sid,chunk=setup
    with pytest.raises(ValueError):put(store,chunk,5,[sample()]*65)
    with pytest.raises(ValueError):candidates(store,sid,limit=501)
    with pytest.raises(ValueError):candidates(store,sid,after=-1)
    with pytest.raises(RuntimeError):
        with store.db() as db:
            commit_candidates(db,chunk,5,[sample()],{})
            raise RuntimeError('simulated interruption before commit')
    assert candidates(store,sid)['event_candidates']==[]
    assert put(store,chunk,5,[sample()])==1

def test_store_session_deletion_removes_candidate_rows(setup):
    store,sid,chunk=setup;put(store,chunk,5,[sample()])
    store.delete_session(sid)
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM event_candidates').fetchone()[0]==0

def test_distinct_commits_keep_own_provenance(setup):
    store,sid,chunk=setup
    put(store,chunk,5,[sample()],{'models':{'events':'first'},'effective_config':{'event_threshold':.25}})
    put(store,chunk,10,[sample()],{'models':{'events':'second'},'effective_config':{'event_threshold':.4}})
    rows=candidates(store,sid)['event_candidates']
    assert [r['provenance']['event_model'] for r in rows]==['first','second']
    assert rows[0]['provenance']['fingerprint']!=rows[1]['provenance']['fingerprint']

def test_silence_rule_is_not_reported_as_neural_execution(setup):
    store,sid,chunk=setup
    put(store,chunk,5,[sample(top_labels=[],category_scores={'silence':{'score':1.,'source_label':'digital_or_near_digital_silence'}},speech_score=0.,rms=0.,peak=0.)])
    assert candidates(store,sid)['event_candidates'][0]['method']=='signal_threshold_silence_rule'

def test_actual_boundary_adapter_and_ledger_agree_on_context_origin(setup):
    from audio_backend.worker import own_core
    store,sid,chunk=setup
    raw={'start':8.,'end':13.,'top_labels':[{'label':'Creak','score':.61}], 'category_scores':{'door':{'score':.02,'source_label':'Door'}}}
    result=own_core({'segments':[],'events':[],'metadata':{'event_candidates':[raw]}},10.,40.,base=20.)
    assert result['metadata']['event_candidates'][0]['start']==8.
    put(store,chunk,30,result['event_candidates'])
    saved=candidates(store,sid)['event_candidates'][0]
    assert (saved['start'],saved['end'],saved['support_start'],saved['support_end'])==(30.,33.,28.,33.)
    assert saved['top_labels'][0]['label']=='Creak'
