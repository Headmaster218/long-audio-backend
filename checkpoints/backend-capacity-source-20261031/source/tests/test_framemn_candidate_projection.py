"""Model-free exact ownership, serialization, privacy and rollback checks."""
import copy
import json
import math
from unittest.mock import patch
import pytest
from audio_backend import event_candidate_store as ledger
from audio_backend.worker import own_core, validate_result
from audio_backend.settings import Settings
from audio_backend.storage import Store


def candidate(first=0,count=160000,total=160000,runs=None):
    return {'payload_kind':ledger.TEMPORAL_KIND,'start':first/16000,'end':(first+count)/16000,
        'time_precision':'model_score_grid','onset_precision_seconds':None,
        'method':'framemn10_knock_temporal_experimental','model_inference_executed':True,
        'top_labels':[{'label':'Door','score':.9},{'label':'Knock','score':.02},
                      {'label':'Female speech, woman speaking','score':1.}],
        'category_scores':{},'temporal':{'profile':ledger.TEMPORAL_PROFILE,'target_label':'Knock',
        'target_class_index':228,'threshold':.01,'median_frames':9,'grid_samples':640,
        'sample_rate':16000,'tile_start_sample':first,'tile_sample_count':count,
        'analyzer_sample_count':total,'frame_runs':[[0,250]] if runs is None else runs,
        'raw_target_peak':.9,'filtered_target_peak':.02},
        'candidate_provenance':{'model':'FrameMN10','checkpoint_sha256':ledger.TEMPORAL_CHECKPOINT,
        'checkpoint_integrity':'local_first_seen_sha256_not_publisher_digest',
        'labelmap_sha256':ledger.TEMPORAL_LABELMAP,'labelmap_identity':'audioset_strong_447_at_'+ledger.TEMPORAL_COMMIT,
        'upstream_commit':ledger.TEMPORAL_COMMIT,'context_semantics':ledger.TEMPORAL_CONTEXT,
        'profile':ledger.TEMPORAL_PROFILE}}


def owned(rows,start=0.,end=10.):
    return own_core({'segments':[],'events':[],'metadata':{'event_candidates':copy.deepcopy(rows)}},start,end)


@pytest.fixture
def setup(tmp_path):
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0)
    store=Store(tmp_path/'db',settings);sid=store.create_session('temporal synthetic')['id']
    cid,_=store.add_chunk(sid,'one','one',tmp_path/'unused.wav',60,offset=100)
    return store,sid,store.claim(),settings


def test_original_support_and_exact_nested_half_open_core():
    raw=candidate(runs=[[1,4],[249,250]])
    result=owned([raw],641/16000,2239/16000)
    row=result['event_candidates'][0]
    assert row['temporal']['frame_runs']==raw['temporal']['frame_runs']
    assert row['temporal']['owned_sample_runs']==[[641,2239]]
    assert row['temporal']['owned_tile_sample_range']==[641,2239]
    assert result['metadata']['event_candidates']==[raw]
    assert raw['temporal'].get('owned_sample_runs') is None


def test_adjacent_integer_and_half_sample_cores_never_duplicate_support():
    for boundary in [1.,1.+1/16000,(16000+.5)/16000,math.nextafter(1.,math.inf)]:
        left=owned([candidate()],0,boundary)['event_candidates'][0]['temporal']
        right=owned([candidate()],boundary,10)['event_candidates'][0]['temporal']
        assert left['owned_sample_runs'][0][1]==right['owned_sample_runs'][0][0]
        assert left['owned_sample_runs'][0][0]==0 and right['owned_sample_runs'][0][1]==160000


def test_seventh_tile_partial_grid_clips_to_actual_samples():
    raw=candidate(first=960000,count=160,total=960160,runs=[[0,1]])
    row=owned([raw],60.,60.01)['event_candidates'][0]
    assert row['temporal']['frame_runs']==[[0,1]]
    assert row['temporal']['owned_sample_runs']==[[0,160]]
    assert row['end']==pytest.approx(.01)


def test_empty_positive_runs_still_retains_tile_diagnostics():
    row=owned([candidate(runs=[])])['event_candidates'][0]
    assert row['temporal']['owned_sample_runs']==[]


def test_maximum_run_encoding_fits_without_truncation(setup):
    store,sid,chunk,_=setup
    raw=candidate(runs=[[i,i+1] for i in range(0,250,2)])
    raw['top_labels']=[{'label':'x'*40+str(i),'score':.1} for i in range(10)]
    result=owned([raw]);assert store.commit_window(chunk,0,10,result)
    row=store.event_candidates(sid)['event_candidates'][0]
    assert len(row['temporal']['frame_runs'])==len(row['temporal']['owned_sample_runs'])==125
    with store.db() as db:
        item=db.execute('SELECT payload,provenance FROM event_candidates').fetchone()
    assert len(item['payload'].encode())<=8192 and len(item['provenance'].encode())<=4096


@pytest.mark.parametrize('change',[
    lambda c:c.update(payload_kind='unknown'),lambda c:c.pop('payload_kind'),
    lambda c:c['temporal'].update(target_class_index=True),
    lambda c:c['temporal'].update(target_class_index=228.),
    lambda c:c['temporal'].update(profile='other'),
    lambda c:c['temporal'].update(threshold=.02),
    lambda c:c['temporal'].update(grid_samples=641),
    lambda c:c['temporal'].update(frame_runs=[[1,2],[2,3]]),
    lambda c:c['temporal'].update(frame_runs=[[0,251]]),
    lambda c:c['temporal'].update(frame_runs=[[0,1]]*126),
    lambda c:c['temporal'].update(raw_target_peak=float('nan')),
    lambda c:c['temporal'].update(tile_start_sample=1),
    lambda c:c['temporal'].update(analyzer_sample_count=960161),
    lambda c:c['temporal'].update(extra='must refuse'),
    lambda c:c['candidate_provenance'].update(checkpoint_sha256='b'*64),
    lambda c:c.update(onset_precision_seconds=.04),
    lambda c:c.update(speech_score=.9),lambda c:c.update(model_inference_executed=False),
    lambda c:c.update(start=.01),
])
def test_invalid_optional_payloads_rejected_even_when_outside_core(change):
    row=candidate();change(row)
    with pytest.raises(ValueError):owned([row],30,40)


def test_combined_row_cap_and_existing_result_cap():
    with pytest.raises(ValueError,match='window limit'):owned([candidate()]*65)
    assert len(owned([candidate()]*64)['event_candidates'])==64
    with pytest.raises(ValueError,match='too large'):
        validate_result({'segments':[],'events':[],'metadata':{'padding':'x'*(8*1024**2)}},10)


def test_tampered_nested_owned_support_refused(setup):
    store,sid,chunk,_=setup
    result=owned([candidate()],2,4)
    result['event_candidates'][0]['temporal']['owned_sample_runs']=[[0,160000]]
    with pytest.raises(ValueError):store.commit_window(chunk,0,2,result)
    assert store.event_candidates(sid)['event_candidates']==[]


def test_rollback_retry_restart_own_provenance_and_no_primary_promotion(setup):
    store,sid,chunk,settings=setup
    raw=candidate();result=owned([raw],2,4)
    result['metadata'].update(models={'events':'Zipformer'},event_model_sha256='b'*64)
    result['segments']=[{'start':0,'end':1,'text':'primary remains'}]
    before=copy.deepcopy(result)
    with patch('audio_backend.storage.update_provenance',side_effect=RuntimeError('rollback')):
        with pytest.raises(RuntimeError):store.commit_window(chunk,0,2,result)
    assert result==before and store.event_candidates(sid)['event_candidates']==[]
    assert store.results(sid)['segments']==[] and store.chunk(chunk['id'])['next_offset']==0
    assert store.commit_window(chunk,0,2,result)
    snapshot=store.event_candidates(sid)
    assert not store.commit_window(chunk,0,2,result)
    restarted=Store(store.path,settings) if hasattr(store,'path') else Store(settings.data_dir/'db',settings)
    assert restarted.event_candidates(sid)==snapshot
    row=snapshot['event_candidates'][0]
    assert (row['start'],row['end'],row['support_start'],row['support_end'])==(100,102,98,108)
    assert row['time_precision']=='model_score_grid' and row['onset_precision_seconds'] is None
    assert row['provenance']['model']=='FrameMN10' and 'Zipformer' not in json.dumps(row['provenance'])
    assert row['temporal']['owned_sample_runs']==[[32000,64000]]
    assert 'Female speech' not in json.dumps(row)
    assert store.events(sid)['events']==[]


def test_temporal_protected_labels_filtered_at_persistence_without_legacy_change(setup):
    store,sid,chunk,_=setup
    protected=['Baby laughter','Child singing','Female singing','Male singing']
    raw=candidate();raw['top_labels']=[{'label':label,'score':1.} for label in protected]+[{'label':'Knock','score':.1}]
    assert store.commit_window(chunk,0,10,owned([raw]))
    row=store.event_candidates(sid)['event_candidates'][0]
    assert [item['label'] for item in row['top_labels']]==['Knock']
    legacy=json.loads(ledger._payload({'top_labels':[{'label':'Female singing','score':.1}]}))
    assert legacy['top_labels'][0]['label']=='Female singing'


def test_temporal_payload_byte_overflow_is_refused_without_truncating_runs():
    raw=candidate(runs=[[i,i+1] for i in range(0,250,2)])
    raw['top_labels']=[{'label':'雨'*255+str(i),'score':.1} for i in range(10)]
    with pytest.raises(ValueError,match='payload exceeds limit'):owned([raw])


def test_full_tolerated_input_seventh_tile_can_persist(tmp_path):
    store=Store(tmp_path/'db',Settings(data_dir=tmp_path,start_worker=False))
    sid=store.create_session('tolerated endpoint')['id']
    cid,_=store.add_chunk(sid,'one','one',tmp_path/'unused.audio',61)
    raw=candidate(first=960000,count=160,total=960160,runs=[[0,1]])
    result=owned([raw],0,60.01)
    with store.db() as db:
        assert ledger.commit_candidates(db,store.chunk(cid),0,result['event_candidates'],result['metadata'])==1
    row=store.event_candidates(sid)['event_candidates'][0]
    assert (row['start'],row['end'])==(60,60.01)
    assert row['temporal']['owned_sample_runs']==[[0,160]]
