"""Fresh model-free persistence fixtures; no audio, model, network or old data."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from audio_backend import lexical_evidence as le, transcript_contract as tc, transcript_revision as tr
from audio_backend.boundaries import lexical_core
from audio_backend.worker import own_core
from audio_backend.settings import Settings
from audio_backend.storage import Store


def unit(text=' hello', start=.1, end=.8, raw=None, **extra):
    return {'text':text,'raw_text':text if raw is None else raw,'start':start,'end':end,
        'ownership_anchor':start,'anchor_kind':'ctc_emission',**extra}


def recognized(units=None):
    units = [unit()] if units is None else units
    return {'segments':[{'start':units[0]['start'],'end':max(u['end'] for u in units),
        'text':''.join(u['text'] for u in units).strip(),
        'raw_text':''.join(u['raw_text'] for u in units).strip(),
        'asr_backend':'sensevoice','timestamp_kind':le.PRIMARY_TIMESTAMP,'_units':units}],
        'events':[],'metadata':{'asr_backend':'sensevoice'}}


def create(tmp_path, enabled=True, duration=300):
    settings = Settings(data_dir=tmp_path,model_dir=tmp_path/'unused-models',
        start_worker=False,lexical_evidence_enabled=enabled)
    store = Store(tmp_path/'fresh.sqlite3',settings)
    sid = store.create_session('synthetic')['id']
    cid,_ = store.add_chunk(sid,'synthetic','a'*64,tmp_path/'never-opened.wav',duration)
    with store.db() as db: db.execute("UPDATE chunks SET status='running' WHERE id=?",(cid,))
    return store,sid,store.chunk(cid)


def window(chunk,offset=0.,end=1.,units=None,segments=1):
    result = recognized(units)
    result['segments'] = [deepcopy(result['segments'][0]) for _ in range(segments)]
    result = own_core(result,0.,end-offset,base=offset,retain_lexical=True)
    result['_lexical_sources'] = [{**{k:chunk[k] for k in le.SOURCE_FIELDS},
        'seek_seconds':offset,'requested_seconds':end-offset,'analyzer_frames':[0,round((end-offset)*16000)]}]
    return result


def rows(store):
    with store.db() as db: return [dict(r) for r in db.execute('SELECT * FROM segments ORDER BY id')]


def charge(store,sid):
    with store.db() as db: return db.execute('SELECT lexical_evidence_bytes FROM sessions WHERE id=?',(sid,)).fetchone()[0]


def revision(store,sid):
    with store.db() as db: return db.execute('SELECT transcript_revision FROM sessions WHERE id=?',(sid,)).fetchone()[0]


def inventory(store,sid,row):
    with store.db() as db: return le.current_inventory(db,sid,row)


@pytest.mark.parametrize('display,raw',[
    (['  hello',' world  '],['  hello',' world  ']),
    ([' 臺','灣 '],[' 台','湾 ']),
    (['','  國'],[' raw','  国']),
    ([' a',' a','!'],[' a',' a','!']),
    (['\tfirst','\nsecond\t'],['  first','\tsecond ']),
])
def test_exact_independent_raw_display_contributions(display,raw):
    source=recognized([unit(d,start=.1+i*.1,raw=r) for i,(d,r) in enumerate(zip(display,raw))])
    plain=own_core(deepcopy(source),0.,1.)
    saved=own_core(deepcopy(source),0.,1.,retain_lexical=True)
    for left,right in zip(plain['segments'],saved['segments']):
        proof=right.pop('_lexical_evidence')
        assert left==right
        for key,span in (('text','display_span'),('raw_text','raw_span')):
            pieces=[right[key][u[span][0]:u[span][1]] for u in proof['units']]
            assert ''.join(pieces)==right[key]
    saved.pop('_lexical_rejection')
    assert saved==plain


@pytest.mark.parametrize('value,eligible',[(" can't",True),(' 國',True),('中文',False),
    (' café',False),('e\u0301',False),('👩\u200d💻',False),('two words',False),('\ufffd',False)])
def test_conservative_grouping_does_not_change_retention_or_primary(value,eligible):
    result=own_core(recognized([unit(value)]),0,1,retain_lexical=True)
    assert result['segments'][0]['text']==value.strip()
    assert result['segments'][0]['_lexical_evidence']['units'][0]['granular_eligible'] is eligible


def test_context_overhang_negative_core_coordinates_not_clamped():
    source=recognized([unit(' anchor',start=.5,end=1.4),unit(' retained',start=.8,end=1.5),unit(' next',start=1.2,end=2.1)])
    previous=[{'start':10.5,'end':11.4,'ownership_anchor':10.5,'text':' anchor','emitted':True}]
    result=own_core(source,1.,2.,previous,10.,retain_lexical=True)
    first=result['segments'][0]
    assert first['start']==pytest.approx(-.2) and first['end']==pytest.approx(1.1)
    evidence=first['_lexical_evidence']
    assert evidence['coordinates']=={'axis':'chunk_relative_seconds','core_start':11.,'core_end':12.,'analyzer_start':10.}
    assert evidence['units'][0]['ownership_anchor']==10.8
    assert evidence['units'][-1]['end']==12.1


def test_native_phrase_or_legacy_fallback_cannot_claim_complete_inventory():
    source=recognized([unit('first'),unit('second',start=.3,unit_kind='native_phrase')])
    result=own_core(source,0,1,retain_lexical=True)
    assert result['segments'][0]['text']=='first second'
    assert all('_lexical_evidence' not in s for s in result['segments'])
    source=recognized();source['segments'].append({'start':.2,'end':.8,'text':'legacy'})
    result=own_core(source,0,1,retain_lexical=True)
    assert [s['text'] for s in result['segments']]==['hello','legacy']
    assert all('_lexical_evidence' not in s for s in result['segments'])


def test_opt_in_defaults_false_and_strict_boolean(tmp_path):
    assert Settings(data_dir=tmp_path).lexical_evidence_enabled is False
    with pytest.raises(ValueError): Settings(data_dir=tmp_path,lexical_evidence_enabled=1)
    store,sid,chunk=create(tmp_path,False)
    assert store.commit_window(chunk,0,1,window(chunk))
    assert rows(store)[0]['lexical_evidence'] is None and charge(store,sid)==0
    assert 'lexical_evidence' not in store.chunk(chunk['id'])['metadata']


def assert_primary_bytes_unchanged(sid, chunk, before, row, offset=0):
    expected={'session_id':sid,'id':row['id'],'chunk_id':chunk['id'],
        'start':chunk['timeline_start']+offset+before['start'],
        'end':chunk['timeline_start']+offset+before['end'],'text':before['text'],
        'raw_text':before.get('raw_text'),'asr_backend':before.get('asr_backend','sensevoice'),
        'timestamp_kind':before.get('timestamp_kind',le.PRIMARY_TIMESTAMP),
        'confidence':before.get('confidence'),'speaker_local_id':before.get('speaker_local_id'),
        'speaker_window_start':float(offset)}
    actual={'session_id':sid,**{k:row[k] for k in tc.PRIMARY_FIELDS}}
    assert tc.encoded(actual)==tc.encoded(expected)
    assert tc.primary_evidence(sid,row)['content_sha256']==hashlib.sha256(tc.encoded(expected)).hexdigest()


def test_atomic_commit_private_fields_and_unchanged_primary_reference(tmp_path):
    store,sid,chunk=create(tmp_path)
    assert store.commit_window(chunk,0,1,window(chunk))
    row=rows(store)[0];saved=inventory(store,sid,row)
    assert saved and saved['source_ref'].startswith(f"l.{row['id']}.")
    primary=tc.primary_evidence(sid,row)
    assert saved['inventory']['primary_sha256']==primary['content_sha256']
    copy=dict(row,lexical_evidence=None)
    assert tc.primary_evidence(sid,copy)==primary
    assert charge(store,sid)==len(row['lexical_evidence'].encode('ascii'))
    assert 'lexical_evidence' not in store.results(sid)['segments'][0]
    assert 'lexical_evidence_bytes' not in store.session(sid)
    metadata=store.chunk(chunk['id'])['metadata']
    assert metadata['lexical_evidence']=={'schema':le.SCHEMA,'status':'retained'}
    assert 'hello' not in json.dumps(metadata['lexical_evidence'])


@pytest.mark.parametrize('corrupt',[
    lambda r:r.pop('_lexical_sources'),
    lambda r:r['_lexical_sources'][0].update(digest='bad'),
    lambda r:r['_lexical_sources'][0].update(analyzer_frames=[0,True]),
    lambda r:r['segments'][0].pop('_lexical_evidence'),
    lambda r:r['segments'][0]['_lexical_evidence']['units'][0].update(display_span=[0,999]),
    lambda r:r['segments'][0]['_lexical_evidence']['units'][0].update(start=float('nan')),
    lambda r:r['segments'][0]['_lexical_evidence']['units'][0].update(anchor_kind='certified'),
    lambda r:r['segments'][0]['_lexical_evidence'].update(extra='unsupported'),
])
def test_invalid_optional_inventory_preserves_all_primary(tmp_path,corrupt):
    store,sid,chunk=create(tmp_path)
    result=window(chunk);before=deepcopy(result['segments'][0]);expected=before['text'];corrupt(result)
    assert store.commit_window(chunk,0,1,result)
    assert rows(store)[0]['text']==expected and rows(store)[0]['lexical_evidence'] is None
    assert charge(store,sid)==0 and store.chunk(chunk['id'])['next_offset']==1
    assert_primary_bytes_unchanged(sid,chunk,before,rows(store)[0])


def test_one_missing_segment_rejects_entire_window(tmp_path):
    store,sid,chunk=create(tmp_path)
    result=window(chunk,segments=2);result['segments'][1].pop('_lexical_evidence')
    assert store.commit_window(chunk,0,1,result)
    assert len(rows(store))==2 and all(r['lexical_evidence'] is None for r in rows(store))
    assert charge(store,sid)==0


def test_rollback_retry_restart_and_lost_acknowledgment(tmp_path,monkeypatch):
    store,sid,chunk=create(tmp_path)
    result=window(chunk);result['events']=[{'start':0,'end':1,'label':'x','score':.1,'time_precision':123}]
    with pytest.raises(ValueError):store.commit_window(chunk,0,1,result)
    assert rows(store)==[] and charge(store,sid)==0 and store.chunk(chunk['id'])['next_offset']==0
    original=store.db
    @contextmanager
    def lost_ack():
        with original() as db: yield db
        raise OSError('synthetic lost acknowledgement')
    with monkeypatch.context() as patch:
        patch.setattr(store,'db',lost_ack)
        with pytest.raises(OSError):store.commit_window(chunk,0,1,window(chunk))
    admitted=charge(store,sid);snapshot=rows(store)
    restarted=Store(store.path,store.settings)
    assert not restarted.commit_window(chunk,0,1,window(chunk))
    assert charge(restarted,sid)==admitted and rows(restarted)==snapshot


def test_sqlite_failure_is_not_optional_fallback(tmp_path):
    store,sid,chunk=create(tmp_path)
    with store.db() as db:
        db.execute("CREATE TRIGGER synthetic_failure BEFORE UPDATE OF lexical_evidence ON segments BEGIN SELECT RAISE(ABORT,'synthetic storage failure'); END")
    with pytest.raises(sqlite3.DatabaseError,match='synthetic storage failure'):
        store.commit_window(chunk,0,1,window(chunk))
    assert rows(store)==[] and charge(store,sid)==0


def test_source_and_authoritative_current_chunk_mismatch_reject_inventory(tmp_path):
    store,sid,chunk=create(tmp_path)
    result=window(chunk)
    with store.db() as db:db.execute('UPDATE chunks SET digest=? WHERE id=?',('b'*64,chunk['id']))
    assert store.commit_window(chunk,0,1,result)
    assert rows(store)[0]['lexical_evidence'] is None and charge(store,sid)==0


def test_edit_revocation_source_staleness_person_assignment_and_no_refund(tmp_path):
    store,sid,chunk=create(tmp_path)
    assert store.commit_window(chunk,0,1,window(chunk))
    row=rows(store)[0];used=charge(store,sid);rev=revision(store,sid)
    with store.db() as db:db.execute('UPDATE segments SET speaker_id=? WHERE id=?',('person',row['id']))
    assert inventory(store,sid,rows(store)[0]) and revision(store,sid)==rev
    with store.db() as db:db.execute('UPDATE chunks SET digest=? WHERE id=?',('b'*64,chunk['id']))
    assert inventory(store,sid,rows(store)[0]) is None and charge(store,sid)==used
    with store.db() as db:db.execute('UPDATE segments SET text=? WHERE id=?',('edited',row['id']))
    assert rows(store)[0]['lexical_evidence'] is None and charge(store,sid)==used
    with store.db() as db:db.execute('DELETE FROM segments WHERE id=?',(row['id'],))
    assert charge(store,sid)==used


def test_direct_inventory_mutation_changes_revision_but_never_primary_hash(tmp_path):
    store,sid,chunk=create(tmp_path);store.commit_window(chunk,0,1,window(chunk))
    row=rows(store)[0];primary=tc.primary_evidence(sid,row);rev=revision(store,sid)
    with store.db() as db:db.execute('UPDATE segments SET lexical_evidence=? WHERE id=?',('{}',row['id']))
    assert revision(store,sid)==rev+1 and inventory(store,sid,rows(store)[0]) is None
    assert tc.primary_evidence(sid,rows(store)[0])==primary


def test_historical_null_repeat_migration_exact_revision_sql_and_partial_rollback(tmp_path,monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(le,'init_schema',lambda db,**kwargs:None)
        store,sid,chunk=create(tmp_path,False)
        store.commit_window(chunk,0,1,window(chunk))
    before=tc.primary_evidence(sid,rows(store)[0]);rev=revision(store,sid)
    store=Store(store.path,store.settings)
    assert rows(store)[0]['lexical_evidence'] is None and charge(store,sid)==0
    assert tc.primary_evidence(sid,rows(store)[0])==before and revision(store,sid)==rev
    Store(store.path,store.settings)
    with store.db() as db:
        for name,sql in tr.SCHEMA.items():
            actual=db.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone()[0]
            assert ' '.join(actual.split())==' '.join(sql.split())
        db.execute('DROP TRIGGER lexical_inventory_v1_revision')
    with pytest.raises(ValueError,match='Incomplete lexical'):Store(store.path,store.settings)
    assert rows(store)[0]['lexical_evidence'] is None


def test_bounded_snapshot_parts_without_fresh_pcm_claim(tmp_path):
    store,sid,chunk=create(tmp_path)
    part=dict(chunk,read_start=.25,read_duration=.5)
    with store.db() as db:
        source=le.snapshot_parts(db,[part],sid)
        assert source[0]['digest']==chunk['digest'] and 'path' not in source[0]
        assert le.snapshot_parts(db,[part]*17,sid) is None
        assert le.snapshot_parts(db,[dict(part,timeline_start=999)],sid) is None
    assert 'not_pcm_attestation' in le.SOURCE_NOTE


def test_segment_unit_and_window_caps_are_all_or_none():
    for count in (le.MAX_SEGMENT_UNITS,le.MAX_SEGMENT_UNITS+1):
        result=own_core(recognized([unit('x',start=.1) for _ in range(count)]),0,1,retain_lexical=True)
        # Full canonical byte cap is tighter for these verbose per-unit records.
        assert result['segments'][0]['text']=='x'*count
        assert '_lexical_evidence' not in result['segments'][0]
    result=own_core(recognized([unit('x'*(le.MAX_UNIT_CHARS+1))]),0,1,retain_lexical=True)
    assert result['segments'][0]['text']=='x'*(le.MAX_UNIT_CHARS+1)
    assert '_lexical_evidence' not in result['segments'][0]


def test_actual_32_mib_admission_budget_without_seeded_charge(tmp_path):
    """Explicit large-fixture envelope: monotonically admit real canonical bytes."""
    store,sid,chunk=create(tmp_path,duration=1000)
    units=[unit('x'*8000,start=.1+i*.1,end=.8) for i in range(3)]
    total=0;offset=0
    while True:
        result=window(chunk,offset,offset+1,units,segments=10)
        assert store.commit_window(chunk,offset,offset+1,result)
        now=charge(store,sid)
        if now==total:break
        assert total < now <= le.MAX_SESSION_BYTES
        total=now;offset+=1
        assert offset < 100
    assert le.MAX_SESSION_BYTES-le.MAX_WINDOW_BYTES < total <= le.MAX_SESSION_BYTES
    with store.db() as db:
        retained=db.execute('SELECT COALESCE(SUM(length(lexical_evidence)),0) FROM segments').fetchone()[0]
        assert retained==total
        last=db.execute('SELECT lexical_evidence FROM segments ORDER BY id DESC LIMIT 10').fetchall()
        assert all(r[0] is None for r in last)
    assert store.chunk(chunk['id'])['metadata']['lexical_evidence']['status']=='session_capacity'
    assert not store.commit_window(chunk,offset,offset+1,result)
    assert charge(store,sid)==total
    # Revoking all admitted data does not replenish the session's budget.
    with store.db() as db:db.execute('UPDATE segments SET lexical_evidence=NULL')
    assert charge(store,sid)==total
    offset+=1
    assert store.commit_window(chunk,offset,offset+1,window(chunk,offset,offset+1,units,segments=10))
    assert charge(store,sid)==total


@pytest.mark.parametrize('change',[
    lambda r:r['segments'][0]['_lexical_evidence']['units'][0].update(ownership_anchor=.2),
    lambda r:r['segments'][0]['_lexical_evidence']['units'][0].update(start=100,ownership_anchor=100,end=101),
    lambda r:r['segments'][0]['_lexical_evidence']['units'][0].update(end=.7),
    lambda r:r['segments'][0]['_lexical_evidence']['coordinates'].update(analyzer_start=10),
    lambda r:r['_lexical_sources'][0].update(seek_seconds=10),
    lambda r:r['_lexical_sources'][0].update(analyzer_frames=[0,100]),
    lambda r:r['segments'][0].update(asr_backend='whisper'),
    lambda r:r['segments'][0].update(timestamp_kind='forced alignment'),
])
def test_forged_timing_or_backend_rejects_inventory_only(tmp_path,change):
    store,sid,chunk=create(tmp_path);result=window(chunk);before=deepcopy(result['segments'][0]);change(result)
    assert store.commit_window(chunk,0,1,result)
    assert rows(store)[0]['text']=='hello' and rows(store)[0]['lexical_evidence'] is None
    assert charge(store,sid)==0
    if all(result['segments'][0][key]==before[key] for key in ('asr_backend','timestamp_kind')):
        assert_primary_bytes_unchanged(sid,chunk,before,rows(store)[0])


def test_current_inventory_rechecks_timing_and_canonical_json(tmp_path):
    store,sid,chunk=create(tmp_path);store.commit_window(chunk,0,1,window(chunk))
    row=rows(store)[0];value=json.loads(row['lexical_evidence'])
    value['units'][0]['end']=.7
    assert inventory(store,sid,dict(row,lexical_evidence=le.encoded(value).decode('ascii'))) is None
    assert inventory(store,sid,dict(row,lexical_evidence=' '+row['lexical_evidence'])) is None
    duplicate=row['lexical_evidence'][:-1]+',"schema":"'+le.SCHEMA+'"}'
    assert inventory(store,sid,dict(row,lexical_evidence=duplicate)) is None


def test_nonzero_origin_preserves_exact_primary_arithmetic(tmp_path):
    store,sid,chunk=create(tmp_path)
    with store.db() as db:db.execute('UPDATE chunks SET timeline_start=? WHERE id=?',(123.456789,chunk['id']))
    chunk=store.chunk(chunk['id']);result=window(chunk,0,1,[unit(start=.1234567,end=.8123456)])
    store.commit_window(chunk,0,1,result)
    row=rows(store)[0]
    assert row['start']==chunk['timeline_start']+0+result['segments'][0]['start']
    assert inventory(store,sid,row) is not None


def test_local_count_caps_before_finish_and_source_cap_boundaries():
    segment={};capture=le.Capture(0,1,0)
    for _ in range(512):capture.add(segment,unit('x'))
    assert capture.reason is None and len(capture.items[id(segment)])==512
    capture.add(segment,unit('x'))
    assert capture.reason=='capacity' and capture.items=={}
    capture=le.Capture(0,1,0);segments=[{} for _ in range(9)]
    for n in range(4096):capture.add(segments[n//512],unit('x'))
    assert capture.reason is None and capture.total==4096
    capture.add(segments[-1],unit('x'))
    assert capture.reason=='capacity'
    parts=[{'id':str(n),'session_id':'s','digest':'a'*64,'timeline_start':n/16,
        'duration':1/16,'seek_seconds':0,'requested_seconds':1/16,'analyzer_frames':[n*1000,(n+1)*1000]}
        for n in range(16)]
    assert le._sources(parts,'s')==parts
    with pytest.raises(le.Rejected):le._sources(parts+[parts[-1]],'s')
    broken=deepcopy(parts);broken[1]['timeline_start']+=.01
    with pytest.raises(le.Rejected):le._sources(broken,'s')


def test_source_database_failure_remains_primary_transaction_error(tmp_path,monkeypatch):
    store,sid,chunk=create(tmp_path)
    def error(*args):raise sqlite3.OperationalError('synthetic source read error')
    monkeypatch.setattr(le,'_sources_current',error)
    with pytest.raises(sqlite3.OperationalError,match='source read error'):
        store.commit_window(chunk,0,1,window(chunk))
    assert rows(store)==[] and charge(store,sid)==0


def test_valid_persisted_seam_overhang_uses_real_pipeline_coordinates(tmp_path):
    store,sid,chunk=create(tmp_path)
    with store.db() as db:db.execute('UPDATE chunks SET next_offset=11 WHERE id=?',(chunk['id'],))
    chunk=store.chunk(chunk['id'])
    source=recognized([unit(' anchor',start=.5,end=1.4),unit(' retained',start=.8,end=1.5),unit(' next',start=1.2,end=2.1)])
    previous=[{'start':10.5,'end':11.4,'ownership_anchor':10.5,'text':' anchor','emitted':True}]
    result=own_core(source,1.,2.,previous,10.,retain_lexical=True)
    result['_lexical_sources']=[{**{k:chunk[k] for k in le.SOURCE_FIELDS},
        'seek_seconds':10.,'requested_seconds':2.,'analyzer_frames':[0,32000]}]
    assert result['segments'][0]['start']<0
    assert store.commit_window(chunk,11.,12.,result)
    row=rows(store)[0]
    assert row['start']==11.+result['segments'][0]['start'] and row['end']>12.
    assert inventory(store,sid,row) is not None


def test_complete_two_source_inventory_and_duplicate_rejection(tmp_path):
    store,sid,chunk=create(tmp_path,duration=1)
    cid,_=store.add_chunk(sid,'second','b'*64,tmp_path/'never-opened-2.wav',1)
    second=store.chunk(cid)
    result=window(chunk)
    result['_lexical_sources'].append({**{k:second[k] for k in le.SOURCE_FIELDS},
        'seek_seconds':0.,'requested_seconds':1.,'analyzer_frames':[16000,32000]})
    assert store.commit_window(chunk,0,1,result)
    assert inventory(store,sid,rows(store)[0]) is not None
    broken=deepcopy(result['_lexical_sources']);broken[1]['id']=broken[0]['id']
    with pytest.raises(le.Rejected):le._sources(broken,sid)
    with store.db() as db:db.execute('UPDATE chunks SET duration=1.1 WHERE id=?',(cid,))
    assert inventory(store,sid,rows(store)[0]) is None


def test_optional_false_preserves_legacy_lexical_call_signature(monkeypatch):
    import audio_backend.worker as worker
    called=[]
    def legacy(result,start,end,previous,base):
        called.append((start,end,base));return None
    monkeypatch.setattr(worker,'lexical_core',legacy)
    result=worker.own_core({'segments':[{'start':.1,'end':.8,'text':'plain'}],'events':[]},0,1)
    assert called==[(0,1,0.)] and result['segments'][0]['text']=='plain'


def test_stale_private_draft_cannot_survive_rejected_capture(tmp_path):
    store,sid,chunk=create(tmp_path);old=window(chunk)['segments'][0]['_lexical_evidence']
    source=recognized();source['segments'][0]['_lexical_evidence']=old
    source['segments'].append({'start':.2,'end':.8,'text':'legacy','_lexical_evidence':old})
    result=own_core(source,0,1,retain_lexical=True)
    result['_lexical_sources']=window(chunk)['_lexical_sources']
    assert all('_lexical_evidence' not in s for s in result['segments'])
    assert store.commit_window(chunk,0,1,result)
    assert all(r['lexical_evidence'] is None for r in rows(store)) and charge(store,sid)==0
