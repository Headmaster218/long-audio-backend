"""Versioned endpoint policy: synthetic text/proofs and fresh SQLite, no models."""
import base64
from copy import deepcopy
from decimal import localcontext, ROUND_UP
import hashlib
import json

import pytest

from audio_backend import moss_handy_adapter as adapter
from audio_backend import moss_result_store as history
from audio_backend import recording_speaker_links as links
from audio_backend import transcript_contract as contract, transcript_store
from audio_backend.moss_handy_profile import NAME
from audio_backend.native_lexical_alternatives import SourceSupport
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.storage import Store
import test_moss_result_store as history_fixture
from test_moss_result_store import archive_fixture
import test_recording_speaker_links as speaker_fixture
import test_transcript_http as transcript_fixture


def support(frames=320000, origin=0):
    return SourceSupport('1'*64, '2'*64, '3'*64, '4'*64, origin+frames,
        origin, origin+frames, origin, origin+frames, '5'*64, '6'*64)


def project(start='18', end='20.01', *, source=None, text='synthetic phrase'):
    raw = f'[{start}][S01]{text}[{end}]'.encode()
    return adapter.project(raw, support=source or support(), profile_name=NAME)


@pytest.mark.parametrize('end', ['20.000000000000004', '20.00000000000000000000000001',
    '20.001', '20.009999999999999999999999999', '20.010', '020.010000'])
def test_exact_decimal_boundary_and_original_pair_retained(end):
    value = project('018.000', end)
    phrase, = value['diagnostic_phrases']
    record = phrase['timestamp_adjustment']
    assert value['schema_errors'] == [] and value['timestamp_policy'] == adapter.TIMESTAMP_POLICY
    assert (phrase['start'], phrase['end']) == (18., 20.)
    assert record['original_start_token'] == '018.000' and record['original_end_token'] == end
    assert record['original_start'] == 18. and record['original_end'] == float(end)
    assert record['relative_end_frame'] == record['support_end_frame'] == 320000
    assert record['reason'] == 'support_end_intersection_within_10ms'
    assert record['timestamp_quality'] == value['timestamp_quality'] == 'model_generated_unverified'
    assert not value['complete_content'] and not value['speaker_identity_valid']
    assert adapter.validate_adjustment(value, phrase, support()) == (288000, 288000, 320000)
    assert value['alternative']['text'] == 'synthetic phrase'
    assert 'rejected_timestamps' not in value


@pytest.mark.parametrize('end', ['20.01000000000000000000000001', '20.010000000000004', '20.011', '21'])
def test_larger_overshoot_is_rejected_even_when_float_rounds_to_boundary(end):
    value = project(end=end)
    assert value['diagnostic_phrases'] == []
    assert 'invalid_model_timestamps' in value['schema_errors']
    assert value['rejected_timestamps']['pairs'][0]['reason'] == 'end_after_support'
    assert value['alternative']['text'] == 'synthetic phrase'


@pytest.mark.parametrize('start,end', [('20','20.01'), ('21','20.01'), ('19.99996875','20.01'),
    ('0','0'), ('2','1'), ('-1','20.01'), ('NaN','20.01'), ('0','Infinity'), ('0','9'*400)])
def test_invalid_or_frame_empty_pairs_stay_rejected(start, end):
    value = project(start, end)
    assert value['diagnostic_phrases'] == [] and value['schema_errors']


def test_one_exact_frame_remains_positive():
    value = project('19.9999375','20.01')
    phrase, = value['diagnostic_phrases']
    assert adapter.validate_adjustment(value, phrase, support()) == (319999,319999,320000)


@pytest.mark.parametrize('origin', [0, 123456, 2**53-1-159999])
def test_frame_fractional_support_end_and_nonzero_origin(origin):
    source = support(159999, origin)
    value = project('9','10.0099375',source=source)
    phrase, = value['diagnostic_phrases']
    assert phrase['end'] == 159999/16000 and phrase['start'] == 9
    assert phrase['timestamp_adjustment']['support_end_frame'] == origin+159999
    assert phrase['timestamp_adjustment']['relative_end_frame'] == 159999
    assert adapter.validate_adjustment(value, phrase, source)[2] == 159999
    assert project('9','10.00993750000000000001',source=source)['diagnostic_phrases'] == []


def test_decimal_context_cannot_round_the_tolerance():
    with localcontext() as context:
        context.prec = 2; context.rounding = ROUND_UP
        assert project(end='20.010')['diagnostic_phrases'][0]['end'] == 20
        assert project(end='20.01000000000000001')['diagnostic_phrases'] == []


@pytest.mark.parametrize('side', ['start', 'end'])
def test_exact_token_length_bound_before_decimal_work(side, monkeypatch):
    accepted = ('0'*127+'1') if side == 'start' else ('20.01'+'0'*123)
    args = {'start': '18', 'end': '20.01'}; args[side] = accepted
    assert len(accepted) == 128 and project(**args)['diagnostic_phrases']
    args[side] += '0'
    original = adapter._decimal_token
    def bounded(token):
        assert len(token) <= 128
        return original(token)
    monkeypatch.setattr(adapter, '_decimal_token', bounded)
    value = project(**args)
    assert value['diagnostic_phrases'] == []
    assert value['schema_errors'] == ['timestamp_token_capacity']
    assert value['alternative']['text'] == 'synthetic phrase'


def test_empty_phrase_still_rejected_and_original_order_unchanged():
    assert project(text='  ')['schema_errors'] == ['empty_phrase_text']
    raw = b'[18][S1]A[20.01][1][S2]B[2][18][S1]A[20.01]'
    value = adapter.project(raw,support=support(),profile_name=NAME)
    assert [p['raw_text'] for p in value['diagnostic_phrases']] == ['A','B','A']
    assert [p['speaker_local_id'] for p in value['diagnostic_phrases']] == ['S1','S2','S1']
    assert value['alternative']['text'] == 'ABA'
    assert value['raw_sha256'] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('field', ['policy', 'reason', 'original_start_token', 'original_end_token',
    'original_start', 'original_end', 'support_id', 'support_end_frame', 'relative_end_frame', 'timestamp_quality'])
def test_complete_mandatory_adjustment_record_required(field):
    value = project(); phrase = value['diagnostic_phrases'][0]
    del phrase['timestamp_adjustment'][field]
    with pytest.raises(ValueError): adapter.validate_adjustment(value, phrase, support())


@pytest.mark.parametrize('change', ['extra','support','relative','number','end','token','unknown','null','legacy'])
def test_unknown_or_forged_metadata_refuses(change):
    value = project(); phrase = value['diagnostic_phrases'][0]; record = phrase['timestamp_adjustment']
    if change == 'extra': record['extra'] = {'untrusted': []}
    elif change == 'support': record['support_end_frame'] += 1
    elif change == 'relative': record['relative_end_frame'] -= 1
    elif change == 'number': record['original_end'] = 20.02
    elif change == 'end': phrase['end'] = 19.99
    elif change == 'token': record['original_end_token'] = '20.01000000000000001'
    elif change == 'unknown': value['timestamp_policy'] = 'unknown'
    elif change == 'null': value['timestamp_policy'] = None
    else: del value['timestamp_policy']
    with pytest.raises(ValueError): adapter.validate_adjustment(value, phrase, support())


def test_capacity_cannot_leave_an_adjusted_phrase_without_its_evidence(monkeypatch):
    value = project(); phrase = value['diagnostic_phrases'][0]
    old_fields = {k:v for k,v in phrase.items() if k != 'timestamp_adjustment'}
    monkeypatch.setattr(adapter,'MAX_DIAGNOSTIC_BYTES',len(adapter.encoded([old_fields]))+1)
    value = project()
    assert value['diagnostic_phrases'] == []
    assert 'diagnostic_projection_oversize' in value['schema_errors']
    assert value['timestamp_adjusted_phrase_count'] == 0
    adapter.validate_timestamp_policy(value)
    assert value['alternative']['text'] == 'synthetic phrase'
    assert len(adapter.encoded(value)) <= adapter.MAX_RESULT_BYTES


def test_maximum_phrase_result_stays_bounded():
    raw = b'[18][S1]synthetic[20.01]'*256
    value = adapter.project(raw,support=support(),profile_name=NAME)
    assert len(adapter.encoded(value)) <= adapter.MAX_RESULT_BYTES
    assert len(adapter.encoded(value['diagnostic_phrases'])) <= adapter.MAX_DIAGNOSTIC_BYTES
    assert value['diagnostic_phrases'] == [] and 'diagnostic_projection_oversize' in value['schema_errors']


@pytest.mark.parametrize('legacy', [False, True])
def test_fresh_history_reopens_exact_bytes_without_reprojection(archive_fixture, legacy):
    store, archive, sid = archive_fixture
    result = project() if not legacy else project(end='20')
    if legacy:
        del result['timestamp_policy'];del result['timestamp_adjusted_phrase_count']
    value = history_fixture.snapshot(sid,result=adapter.encoded(result))
    history_fixture.reserve(archive,value);archive.commit(value)
    reopened = history.MossResultStore(Store(store.path,store.settings))
    assert reopened.result(sid,value.identity.job_id) == value.result_json


def transcript_case(tmp_path, *, legacy=False):
    data = transcript_fixture.Fixture(tmp_path/'fresh.sqlite3')
    job = data.job(1,1,21,logical_origin=123456)
    metadata = json.loads(job['metadata'])
    source = SourceSupport.from_dict(metadata['source_support'])
    value = project(source=source,end='20' if legacy else '20.01')
    if legacy:
        del value['timestamp_policy'];del value['timestamp_adjusted_phrase_count']
    value['cell_binding'] = metadata['cell_binding']
    def save(value):
        raw=adapter.encoded(value); sha=hashlib.sha256(raw).hexdigest()
        with data.db() as db:
            db.execute('DELETE FROM segments')
            db.execute(f'UPDATE {history.TABLE} SET result_json=?,result_sha256=?,charge_bytes=? WHERE job_id=?',
                (raw,sha,len(raw)+len(job['metadata'])+16384,job['jid']))
        return raw,sha
    raw,sha=save(value)
    return data,job,value,raw,sha,save


@pytest.mark.parametrize('legacy', [False, True])
def test_transcript_eligibility_and_exact_frame_end_survive_reopen(tmp_path,legacy):
    data,job,value,raw,sha,_ = transcript_case(tmp_path,legacy=legacy)
    page=data.page(); assert len(page['selected_runs']) == 1
    phrase,=page['reading']
    assert phrase['session_end'] == 21 and phrase['end'] == 20
    assert phrase['timestamp_quality'] == 'model_generated_unverified'
    assert ('timestamp_adjustment' in phrase) is not legacy
    assert page['selected_runs'][0]['start_frame'] == 16000
    assert page['selected_runs'][0]['end_frame'] == 336000
    data.reader=transcript_store.TranscriptStore(data)
    assert data.page() == page
    with data.db() as db:
        row=db.execute(f'SELECT result_json,result_sha256 FROM {history.TABLE} WHERE job_id=?',(job['jid'],)).fetchone()
    assert row['result_json'] == raw and row['result_sha256'] == sha


@pytest.mark.parametrize('change', ['legacy_overshoot','unknown_policy','partial_adjustment'])
def test_transcript_rejects_legacy_overshoot_and_unvalidated_policy(tmp_path,change):
    data,job,value,raw,sha,save=transcript_case(tmp_path,legacy=change=='legacy_overshoot')
    if change=='legacy_overshoot':value['diagnostic_phrases'][0]['end']=20.001
    elif change=='unknown_policy':value['timestamp_policy']='unknown'
    else:del value['diagnostic_phrases'][0]['timestamp_adjustment']['reason']
    save(value)
    with pytest.raises(ReadError,match='transcript_result_invalid'):data.page()


@pytest.mark.parametrize('audit',[False,True])
def test_prospective_cursor_policy_rejects_prior_version(audit):
    old=dict(contract.POLICY,schema='current-unified-transcript-v2');del old['moss_timestamps']
    old_hash=hashlib.sha256(contract.encoded(old)).hexdigest()
    value={'policy':old_hash,'session':'synthetic','revision':'a'*64}
    value.update({'view':'binding-placement-audit-v1','after':0} if audit else {'block':0})
    token=base64.urlsafe_b64encode(contract.encoded(value)).decode().rstrip('=')
    parse=contract.parse_audit_cursor if audit else contract.parse_cursor
    with pytest.raises(ReadError,match='invalid_transcript_cursor'):parse(token,'synthetic')
    fresh=contract.audit_cursor('synthetic',0,'a'*64) if audit else contract.cursor('synthetic',0,'a'*64)
    assert parse(fresh,'synthetic')['policy'] == contract.POLICY_HASH


def test_adjusted_phrase_never_supplies_probe_pcm():
    source=speaker_fixture.ordinary()
    batch,crops=speaker_fixture.capture(source,'[1][S1]adjusted[10.01]')
    assert batch['nodes'] == [] and crops == ()
    assert batch['outcomes'] == [{'phrase_index':0,'status':'timestamp_adjusted_unverified'}]
    assert batch['timestamp_policy'] == adapter.TIMESTAMP_POLICY
    assert links.validate_batch(batch,require_vectors=False) == batch


@pytest.mark.parametrize('label',['S1','S2'])
def test_adjusted_occupancy_blocks_other_probe_sources_even_same_tag(label):
    source=speaker_fixture.ordinary()
    alone,crops=speaker_fixture.capture(source,'[1][S1]candidate[9]')
    assert len(alone['nodes']) == len(crops) == 1
    batch,crops=speaker_fixture.capture(source,f'[1][S1]candidate[9][0][{label}]occupied[10.01]')
    assert batch['nodes'] == [] and crops == ()
    assert batch['outcomes'][0]['status'] == 'insufficient_clean_support'
    assert batch['outcomes'][1]['status'] == 'timestamp_adjusted_unverified'
    assert links.validate_batch(batch,require_vectors=False) == batch


def test_legacy_speaker_batch_is_unchanged_and_new_outcome_needs_policy():
    support,pcm,proof=speaker_fixture.ordinary()
    projection=speaker_fixture.projection(support)
    del projection['timestamp_policy'];del projection['timestamp_adjusted_phrase_count']
    batch,_=links.capture_candidates(support,pcm,proof,projection,'job','1'*64,links.expected_space())
    before=links._bounded(batch)
    assert links.validate_batch(batch,require_vectors=False) == batch
    assert links._bounded(batch) == before and 'timestamp_policy' not in batch
    batch['nodes']=[];batch['outcomes']=[{'phrase_index':0,'status':'timestamp_adjusted_unverified'}]
    batch['batch_sha256']=links._batch_digest(batch)
    with pytest.raises(Exception,match='speaker_link_outcome_binding'):
        links.validate_batch(batch,require_vectors=False)


def test_result_ceiling_refuses_whole_adjusted_projection(monkeypatch):
    monkeypatch.setattr(adapter,'MAX_RESULT_BYTES',1000)
    value=project()
    assert value['diagnostic_phrases'] == [] and value['alternative'] is None
    assert value['schema_errors'] == ['result_projection_oversize']
    assert value['timestamp_adjusted_phrase_count'] == 0
    adapter.validate_timestamp_policy(value)
    assert len(adapter.encoded(value)) <= 1000


def test_mixed_adjusted_and_still_invalid_result_keeps_error():
    raw=b'[18][S1]adjusted[20.01][1][S2]rejected[20.010000000000004]'
    value=adapter.project(raw,support=support(),profile_name=NAME)
    assert len(value['diagnostic_phrases']) == 1
    assert value['diagnostic_phrases'][0]['timestamp_adjustment']['original_end_token'] == '20.01'
    assert value['schema_errors'] == ['invalid_model_timestamps']
    assert value['rejected_timestamps']['total'] == 1
    assert value['alternative']['text'] == 'adjustedrejected'


@pytest.mark.parametrize('field',['truncated','aborted'])
def test_adjustment_does_not_relax_probe_outcome_refusals(field):
    source,pcm,proof=speaker_fixture.ordinary()
    projection=speaker_fixture.projection(source,'[1][S1]adjusted[10.01]')
    projection[field]=True
    with pytest.raises(links.Unavailable,match='speaker_link_projection_unusable'):
        links.capture_candidates(source,pcm,proof,projection,'job','1'*64,links.expected_space())


@pytest.mark.parametrize('count',[None, True, False, -1, 257, 0, 2, 1.0])
def test_new_policy_requires_exact_complete_adjustment_count(count):
    value=project()
    if count is None:del value['timestamp_adjusted_phrase_count']
    else:value['timestamp_adjusted_phrase_count']=count
    with pytest.raises(ValueError,match='timestamp_adjustment_count'):
        adapter.validate_timestamp_policy(value)


def test_whole_record_removal_is_detected():
    value=project();del value['diagnostic_phrases'][0]['timestamp_adjustment']
    with pytest.raises(ValueError,match='timestamp_adjustment_count'):
        adapter.validate_timestamp_policy(value)


@pytest.mark.parametrize('consumer',['transcript','speaker'])
def test_consumers_reject_whole_adjustment_record_removal(tmp_path,consumer):
    if consumer=='transcript':
        data,job,value,raw,sha,save=transcript_case(tmp_path)
        del value['diagnostic_phrases'][0]['timestamp_adjustment'];save(value)
        with pytest.raises(ReadError,match='transcript_result_invalid'):data.page()
    else:
        source,pcm,proof=speaker_fixture.ordinary()
        value=speaker_fixture.projection(source,'[1][S1]adjusted[10.01]')
        del value['diagnostic_phrases'][0]['timestamp_adjustment']
        with pytest.raises(links.Unavailable,match='speaker_link_projection_unusable'):
            links.capture_candidates(source,pcm,proof,value,'job','1'*64,links.expected_space())


@pytest.mark.parametrize('remaining',['zero_count','count_and_record','record_only'])
def test_partial_policy_stripping_cannot_downgrade_to_legacy(remaining):
    value=project(end='20' if remaining=='zero_count' else '20.01')
    del value['timestamp_policy']
    if remaining=='record_only':del value['timestamp_adjusted_phrase_count']
    with pytest.raises(ValueError,match='timestamp_adjustment_policy'):
        adapter.validate_timestamp_policy(value)
