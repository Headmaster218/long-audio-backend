"""Fresh synthetic SQLite/ASGI contracts. No audio, model, native call or worker."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from audio_backend import transcript_contract as c, transcript_store as ts, transcript_http as http
from audio_backend import moss_automatic as auto, moss_result_store as history, transcript_revision
from audio_backend.app import _NativeSupportReadLifetime, _assign_native_read_lease, create_app
from audio_backend.native_lexical_alternatives import SourceSupport
from audio_backend.native_owned_support_cache import ReadError, ReadResponseBudget
from audio_backend.settings import Settings

SID = 'synthetic-transcript'


class Fixture:
    def __init__(self, path):
        self.path = path
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE sessions(id TEXT PRIMARY KEY, deleted INTEGER DEFAULT 0,timeline_end REAL);
                CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT,digest TEXT,timeline_start REAL,
                    duration REAL,status TEXT,metadata TEXT);
                CREATE INDEX chunk_timeline ON chunks(session_id,timeline_start);
                CREATE TABLE segments(id INTEGER PRIMARY KEY,chunk_id TEXT,start REAL,end REAL,text TEXT,
                    raw_text TEXT,asr_backend TEXT,timestamp_kind TEXT,confidence REAL,speaker_local_id TEXT,
                    speaker_window_start REAL,speaker_id TEXT);
                CREATE INDEX segments_chunk ON segments(chunk_id,start);
                CREATE TABLE result_changes(cursor INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT);
                CREATE INDEX result_changes_session ON result_changes(session_id,cursor);
                CREATE TRIGGER change_segment AFTER UPDATE ON segments BEGIN
                    INSERT INTO result_changes(session_id) SELECT session_id FROM chunks WHERE id=NEW.chunk_id; END;
                CREATE TABLE recording_batches(id TEXT PRIMARY KEY,session_id TEXT,capture_origin_frame INTEGER);
                CREATE TABLE recording_work(chunk_id TEXT PRIMARY KEY,batch_id TEXT,frames INTEGER,
                    receipt_id TEXT,capture_start_frame INTEGER);
                CREATE INDEX fixture_recording_work_batch ON recording_work(batch_id);
            ''')
            auto.init_schema(db)
            history.init_schema(db)
            transcript_revision.init_schema(db)
            db.execute('INSERT INTO sessions(id,deleted,timeline_end) VALUES(?,0,120.0)', (SID,))
            db.execute('INSERT INTO sessions(id,deleted,timeline_end) VALUES(?,0,120.0)', ('other',))
            db.execute('INSERT INTO recording_batches VALUES(?,?,?)', ('batch', SID, 32000))
            db.execute('INSERT INTO result_changes(session_id) VALUES(?)', (SID,))
        self.chunk('chunk', 0, 21)
        self.segment(1, 0, 5, 'primary one')
        self.segment(2, 9.5, 11, 'whole straddling primary')
        self.segment(3, 15, 20.5, 'primary three')
        self.reader = ts.TranscriptStore(self)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally: db.close()

    def chunk(self, cid, start, end, *, sid=SID):
        with self.db() as db:
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,NULL)',
                (cid, sid, 'd' * 64, start, end - start, 'completed'))
            db.execute('INSERT INTO recording_work VALUES(?,?,?,?,?)',
                (cid, 'batch', round((end - start) * c.RATE), 'e' * 64, 32000 + round(start * c.RATE)))

    def segment(self, ident, start, end, text, *, cid='chunk'):
        with self.db() as db:
            db.execute('INSERT INTO segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident, cid, start, end, text, text, 'synthetic-asr', 'native-segment', .8, 'S1', 0., 'private-person'))

    def job(self, ident, start, end, *, kind='ordinary', state='committed', labels=('S1', 'S2'),
            text='same text', logical_origin=8000, cid='chunk', source_chunks=None):
        with self.db() as db:
            chunks = [db.execute('SELECT * FROM chunks WHERE id=?', (item,)).fetchone()
                      for item in (source_chunks or (cid,))]
        chunk = chunks[0]
        offset = round(chunk['timeline_start'] * c.RATE)
        a, b = round(start * c.RATE), round(end * c.RATE)
        size = round(chunk['duration'] * c.RATE)
        parts, aliases = [], []
        for item in chunks:
            origin = round(item['timeline_start']*c.RATE)
            frames = round(item['duration']*c.RATE)
            x,y = max(a,origin),min(b,origin+frames)
            parts.append(dict(source_sha256='d'*64,source_frames=frames,source_start=x-origin,
                source_end=y-origin,logical_start=x+logical_origin,logical_end=y+logical_origin))
            aliases.append(dict(chunk_id=item['id'],receipt_sha256='e'*64))
        core_start = offset + logical_origin
        core_end = round((chunks[-1]['timeline_start']+chunks[-1]['duration'])*c.RATE)+logical_origin
        join = round(chunks[1]['timeline_start']*c.RATE)+logical_origin if len(chunks)>1 else core_start+size//2
        cores = [[core_start, core_end]] if kind == 'ordinary' else [[core_start,join],[join,core_end]]
        binding = dict(automation_policy=auto.POLICY,
            selection_policy={'ordinary': ts.CELL_POLICY, 'core_seam': ts.SEAM_POLICY, 'reset_seam': ts.RESET_POLICY_SHA256}[kind],
            kind=kind, session_id=SID, parts=parts, source_aliases=aliases,
            support_frames=[a+logical_origin,b+logical_origin], core_frames=cores)
        raw_binding = c.encoded(binding)
        key = hashlib.sha256(raw_binding).hexdigest()
        jid, epoch, request_sha = f'{ident:032x}', 'a'*32, f'{ident:064x}'
        support = SourceSupport(c.digest({'schema':'immutable-source-cell-generation-v1','sources':parts}),
            c.digest({'schema':'bounded-cell-mapping-v1','parts':parts}), 'b'*64, 'c'*64,
            core_end, core_start, core_end, a+logical_origin, b+logical_origin, 'f'*64, '9'*64)
        identity = history.HistoryIdentity(jid, SID, epoch, f'job-{ident}', request_sha)
        metadata = history.build_metadata(identity,
            context={'automatic_candidate_key':key, 'origin':'durable_cell', 'support_id':support.support_id}, support=support.to_dict(),
            cell_binding={'cell_id':'1'*64,'proof_sha256':'2'*64,'support':support.to_dict()})
        result = c.encoded(dict(schema='moss-handy-diagnostic-v1', support_id=support.support_id,
            complete_content=False, eos=None, timestamp_quality='model_generated_unverified',
            speaker_identity_valid=False, profile_update_allowed=False,
            cell_binding={'cell_id':'1'*64,'proof_sha256':'2'*64,'support':support.to_dict()},
            diagnostic_phrases=[dict(raw_text=text, speaker_local_id=label, start=0., end=min(end-start,1.),
                timing_scope='support-relative-model-unverified',speaker_identity_valid=False) for label in labels],
            schema_errors=[]))
        sha = hashlib.sha256(result).hexdigest()
        with self.db() as db:
            db.execute(f'''INSERT INTO {auto.TABLE}(candidate_key,sid,kind,binding,proof,proof_sha256,cell_id,
                slot1,cid1,cid2,state,reason,created,updated,job_id,jobs_epoch,request_sha256,charge_bytes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (key,SID,kind,raw_binding,b'proof' if state in auto.ACTIVE else None,'2'*64,'1'*64,
                 f'{ident:064x}',chunks[0]['id'],chunks[1]['id'] if len(chunks)>1 else None,
                 state,'synthetic',0.,0.,jid,epoch,request_sha,16384+len(raw_binding)))
            if state not in ('queued', 'published'):
                committed = state == 'committed'
                db.execute(f'''INSERT INTO {history.TABLE}(job_id,session_id,original_jobs_epoch,idempotency_key,
                    canonical_request_sha256,state,schema_version,metadata_json,result_json,result_sha256,committed_at,charge_bytes)
                    VALUES(?,?,?,?,?,?,1,?,?,?,?,?)''',
                    (*identity.values(), state, metadata, result if committed else None, sha if committed else None,
                     1. if committed else None, len(metadata)+len(result)+16384 if committed else
                     294912 if state == 'accepted' else len(metadata)+16384))
        return dict(key=key,jid=jid,sha=sha,result=result,metadata=metadata,binding=binding)

    def commit(self, value):
        with self.db() as db:
            db.execute(f'''UPDATE {history.TABLE} SET state='committed',result_json=?,result_sha256=?,
                committed_at=1.0,charge_bytes=? WHERE job_id=?''',
                (value['result'],value['sha'],len(value['metadata'])+len(value['result'])+16384,value['jid']))
            db.execute(f"UPDATE {auto.TABLE} SET state='committed',proof=NULL WHERE job_id=?", (value['jid'],))

    def page(self, **kwargs): return json.loads(self.reader.page(SID, **kwargs))

    def audit(self, **kwargs): return json.loads(self.reader.binding_audit(SID, **kwargs))


@pytest.fixture
def data(tmp_path): return Fixture(tmp_path / 'synthetic.sqlite3')


@pytest.fixture
def api(data):
    app, budget = FastAPI(), ReadResponseBudget()
    http.register_routes(app, data, _assign_native_read_lease, lambda: budget)
    app.add_middleware(_NativeSupportReadLifetime)
    with TestClient(app) as client:
        yield client, budget, app
    assert budget._lease_count == 0
    assert budget._lease_bytes == 0


def test_models_disabled_real_app_registration_and_auth(tmp_path, monkeypatch):
    from audio_backend.worker import Worker
    monkeypatch.setattr(Worker, 'start', lambda *_: pytest.fail('worker must not start'))
    app = create_app(Settings(data_dir=tmp_path, start_worker=False, api_key='synthetic-token'))
    with TestClient(app) as client:
        for suffix in ('/transcript', '/transcript/binding-audit'):
            assert client.get('/sessions/missing' + suffix).status_code == 401
        headers = {'Authorization':'Bearer synthetic-token'}
        session = client.post('/sessions', json={}, headers=headers).json()
        assert 'transcript_revision' not in session
        sid = session['id']
        assert 'transcript_revision' not in client.get(f'/sessions/{sid}', headers=headers).json()
        result = client.get(f'/sessions/{sid}/transcript', headers=headers)
        assert result.status_code == 200
        assert result.json()['reading'] == []
        assert app.state.moss_jobs is None
        assert app.state.moss_response_budget._lease_count == 0


def test_unified_reading_uses_ordinary_union_and_keeps_all_speakers(data, api):
    left, right = data.job(1,0,10.5), data.job(2,10.5,21)
    data.job(3,.5,20.5,kind='core_seam')
    client, budget, _ = api
    response = client.get(f'/sessions/{SID}/transcript')
    assert response.status_code == 200, response.text
    page = response.json()
    assert len(page['reading']) == 4
    assert [item['speaker_scope'] for item in page['reading']] == [
        [job['jid'],job['sha'],label] for job in (left,right) for label in ('S1','S2')]
    assert len(page['baseline_reading']) == 3
    assert len(page['selected_runs']) == 1
    assert page['selected_runs'][0]['end_frame'] == 21*c.RATE
    assert all(item['eligible'] and item['ineligible_reason'] is None for item in page['selected_runs'][0]['supports'])
    assert all(item['word_times'] is None for item in page['reading'])
    assert page['complete_content'] is False and page['eos'] is None
    assert 'private-person' not in response.text
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['pragma'] == 'no-cache'
    assert budget._lease_count == 0


def test_exact_primary_evidence_hash_excludes_mutable_person_assignment(data):
    item = data.page()['reading'][0]
    ref = item['source_ref']
    before = data.reader.evidence(SID,ref)
    with data.db() as db: db.execute("UPDATE segments SET speaker_id='other-person' WHERE id=1")
    assert data.reader.evidence(SID,ref) == before
    with data.db() as db: db.execute("UPDATE segments SET text='changed' WHERE id=1")
    with pytest.raises(ReadError,match='primary_evidence_changed') as exc:
        data.reader.evidence(SID,ref)
    assert exc.value.status_code == 409
    with data.db() as db: db.execute('DELETE FROM segments WHERE id=1')
    with pytest.raises(ReadError) as exc: data.reader.evidence(SID,ref)
    assert exc.value.status_code == 404


def test_exact_moss_reference_lookup_historical_only(data):
    job = data.job(1,0,10.5)
    value = json.loads(data.reader.evidence(SID,f"m.{job['jid']}.{job['sha']}.1"))
    assert value['evidence']['raw_text'] == 'same text'
    assert value['authority'] == 'historical_snapshot' and value['source_current'] == 'not_checked'
    assert value['result_sha256'] == job['sha']
    with pytest.raises(ReadError) as exc: data.reader.evidence('other',f"m.{job['jid']}.{job['sha']}.1")
    assert exc.value.status_code == 404
    with pytest.raises(ReadError) as exc: data.reader.evidence(SID,f"m.{job['jid']}.{'0'*64}.1")
    assert exc.value.status_code == 409
    with pytest.raises(ReadError) as exc: data.reader.evidence(SID,f"m.{job['jid']}.{job['sha']}.5")
    assert exc.value.status_code == 404


def test_stable_retry_and_bound_cursor_then_old_archive_commit_invalidates(data):
    old = data.job(1,0,10.5,state='accepted')
    data.job(2,10.5,21)
    first = data.page()
    assert data.page() == first
    assert data.page(cursor=first['next_cursor'])['block'] == 1
    data.commit(old)
    with pytest.raises(ReadError,match='transcript_revision_changed') as exc:
        data.page(cursor=first['next_cursor'])
    assert exc.value.status_code == 409


def test_primary_change_invalidates_cursor_and_cross_session_cursor_rejected(data):
    page = data.page()
    with data.db() as db: db.execute("UPDATE segments SET text='new primary' WHERE id=1")
    with pytest.raises(ReadError,match='transcript_revision_changed'): data.page(cursor=page['next_cursor'])
    with pytest.raises(ReadError,match='invalid_transcript_cursor'): data.reader.page('other',cursor=page['next_cursor'])


@pytest.mark.parametrize('state', ['queued','published','accepted','no_result','interrupted'])
def test_pending_and_unknown_never_remove_primary(data,state):
    data.job(1,0,10.5,state=state)
    value = data.page()
    assert len(value['reading']) == 3 and value['selected_runs'] == []
    assert value['notices'][0]['status'] == state


@pytest.mark.parametrize('mutation', ['session','digest','receipt','geometry','source_missing','binding_hash'])
def test_invalid_source_binding_is_explicit_unplaced(data,mutation):
    job = data.job(1,0,10.5)
    with data.db() as db:
        if mutation == 'session': db.execute("UPDATE chunks SET session_id='other' WHERE id='chunk'")
        elif mutation == 'digest': db.execute("UPDATE chunks SET digest=? WHERE id='chunk'",('0'*64,))
        elif mutation == 'receipt': db.execute("UPDATE recording_work SET receipt_id=?",('0'*64,))
        elif mutation == 'geometry': db.execute('UPDATE recording_work SET capture_start_frame=capture_start_frame+1')
        elif mutation == 'source_missing': db.execute('DELETE FROM recording_work')
        else: db.execute(f'UPDATE {auto.TABLE} SET binding=? WHERE candidate_key=?',(b'{}',job['key']))
    value = data.page()
    assert value['selected_runs'] == []
    assert data.audit()['unplaced_bindings'][0]['status'] == 'unsupported_unplaced'
    assert value['unplaced_history_check'] == 'not_performed_by_time_page'


def test_actual_support_not_core_envelope_controls_selection(data):
    data.job(1,.5,20.5,kind='core_seam')
    value = data.page()
    assert value['selected_runs'] == []
    assert len(value['reading']) == 3
    assert value['alternatives'][0]['start_frame'] == 8000
    assert len(value['alternatives'][0]['candidate_reading']) == 2


def test_two_alias_nonzero_logical_origin_and_context_owned_primary(data):
    with data.db() as db:
        db.execute('DELETE FROM segments')
        db.execute('DELETE FROM chunks')
        db.execute('DELETE FROM recording_work')
    data.chunk('left',0,10.5)
    data.chunk('right',10.5,21)
    data.segment(1,9.941,10.781,'left-owned crossing',cid='left')
    data.segment(2,10.18,11.14,'right-owned crossing',cid='right')
    data.job(1,.5,20.5,kind='core_seam',source_chunks=('left','right'),logical_origin=123456)
    value = data.page()
    assert value['selected_runs'][0]['start_frame'] == 8000
    assert value['selected_runs'][0]['end_frame'] == 328000
    assert len(value['baseline_reading']) == 2
    with data.db() as db: db.execute("UPDATE chunks SET timeline_start=10.5000625 WHERE id='right'")
    assert data.page()['notices'][0]['status'] == 'unsupported_unplaced'


def test_context_owned_units_across_block_edge_and_binary_float(data):
    data.chunk('edge-left',49.5,60)
    data.chunk('edge-right',60,70.5)
    data.segment(4,59.9,60.5,'left context overhang',cid='edge-left')
    data.segment(5,59.75,60.25,'right context prefix',cid='edge-right')
    first, second = data.page(), data.page(block=1)
    refs = {item['source_ref'] for item in first['reading'] if item.get('text') in ('left context overhang','right context prefix')}
    assert len(refs) == 2
    assert refs.issubset({item['source_ref'] for item in second['reading'] if item['reference_only']})
    with data.db() as db: db.execute('UPDATE segments SET start=?,end=? WHERE id=1',(.1+.2, .400001))
    item = data.page()['reading'][0]
    assert item['start_frame'] == 4800 and item['end_frame'] == 6401
    assert item['canonical_asr']['start'] == .1+.2
    assert item['speaker_scope'] == ['chunk',0.0,'S1']


@pytest.mark.parametrize('field', ['cell_binding','core_support','identity','timing_scope','speaker_identity'])
def test_retained_schema_and_embedded_binding_fail_closed(data,field):
    job = data.job(1,0,10.5)
    result = json.loads(job['result'])
    metadata = json.loads(job['metadata'])
    if field == 'cell_binding': result['cell_binding']['cell_id'] = '0'*64
    elif field == 'core_support':
        metadata['source_support']['core_end_frame'] -= 1
    elif field == 'identity': metadata['identity']['session_id'] = 'other'
    elif field == 'timing_scope': result['diagnostic_phrases'][0]['timing_scope'] = 'verified'
    else: result['speaker_identity_valid'] = True
    result,metadata = c.encoded(result),c.encoded(metadata)
    with data.db() as db:
        db.execute(f'''UPDATE {history.TABLE} SET result_json=?,result_sha256=?,metadata_json=?,charge_bytes=?
            WHERE job_id=?''',(result,hashlib.sha256(result).hexdigest(),metadata,len(result)+len(metadata)+16384,job['jid']))
    with pytest.raises(ReadError,match='transcript_result_invalid'): data.page()


def test_cancelled_source_preserves_history_as_ineligible_lanes(data):
    job = data.job(1,0,10.5)
    with data.db() as db: db.execute("UPDATE chunks SET status='cancelled' WHERE id='chunk'")
    value = data.page()
    assert value['selected_runs'] == []
    assert value['alternatives'][0]['reason'] == 'source_cancelled'
    assert len(value['alternatives'][0]['candidate_reading']) == 2
    assert json.loads(data.reader.evidence(SID,f"m.{job['jid']}.{job['sha']}.all"))['authority'] == 'historical_snapshot'


def test_parse_error_preserves_timed_lanes_and_links_omitted_exact_result(data):
    job = data.job(1,0,10.5)
    raw = json.loads(job['result'])
    raw.update(schema_errors=['unparsed_text'], alternative={'text':'untimed trailing words'})
    raw = c.encoded(raw)
    sha = hashlib.sha256(raw).hexdigest()
    with data.db() as db:
        db.execute(f'UPDATE {history.TABLE} SET result_json=?,result_sha256=?,charge_bytes=? WHERE job_id=?',
            (raw,sha,len(raw)+len(job['metadata'])+16384,job['jid']))
    value = data.page()
    assert value['selected_runs'] == []
    group = value['alternatives'][0]
    assert len(group['candidate_reading']) == 2 and group['reason'] == 'native_parse_unresolved'
    support = group['supports'][0]
    assert support['inline_timing_projection'] == 'partial_or_unavailable'
    exact = json.loads(data.reader.evidence(SID,support['result_ref']))
    assert exact['evidence']['alternative']['text'] == 'untimed trailing words'


def test_tombstone_hides_page_and_both_kinds_of_evidence(data,api):
    job = data.job(1,0,10.5)
    primary = data.page()['baseline_reading'][0]['source_ref']
    with data.db() as db: db.execute('UPDATE sessions SET deleted=1 WHERE id=?',(SID,))
    client, _, _ = api
    for suffix in ('', '/binding-audit', '/evidence/'+primary, f"/evidence/m.{job['jid']}.{job['sha']}.0"):
        assert client.get(f'/sessions/{SID}/transcript'+suffix).status_code == 404


def test_seventeen_results_preserve_complete_primary_without_truncated_choice(data):
    assert c.MAX_RESULTS == 16
    for i in range(17): data.job(i+1,i/2,i/2+1)
    page = data.page()
    assert page['reading'] == page['baseline_reading'] and len(page['reading']) == 3
    assert page['selected_runs'] == page['alternatives'] == []
    assert page['optional_closure']['limit'] == 'result_bodies'
    assert page['optional_closure']['complete'] is False


def test_sixteen_result_cap_keeps_two_mib_aggregate_length_first(data, monkeypatch):
    jobs = [data.job(index + 1, index / 2, index / 2 + 1) for index in range(9)]
    payload = b'x' * (240 * 1024)
    assert len(payload) < c.MAX_RESULT_BYTES and len(payload) * len(jobs) > c.MAX_RESULTS_BYTES
    with data.db() as db:
        db.execute(f'UPDATE {history.TABLE} SET result_json=?,charge_bytes=length(metadata_json)+?+16384',
                   (payload, len(payload)))
    monkeypatch.setattr(data.reader, '_result', lambda *_, **__: pytest.fail('payload must not be loaded'))
    page = data.page()
    assert page['optional_closure']['limit'] == 'results_bytes'
    assert page['reading'] == page['baseline_reading']
    assert page['selected_runs'] == page['alternatives'] == []


def test_dense_23_job_126_second_chain_keeps_primary_only_on_all_pages(data, api):
    with data.db() as db:
        db.execute('DELETE FROM segments')
        db.execute('DELETE FROM recording_work')
        db.execute('DELETE FROM chunks')
        db.execute('UPDATE sessions SET timeline_end=126 WHERE id=?', (SID,))
    for index in range(12):
        start, end = index * 10.5, (index + 1) * 10.5
        cid = f'chunk-{index}'
        data.chunk(cid, start, end)
        data.segment(index + 1, start + .1, end - .1, f'whole-{index}', cid=cid)
        data.job(index + 1, start, end, cid=cid)
    for index in range(11):
        join = (index + 1) * 10.5
        data.job(index + 20, join - 10, join + 10, kind='core_seam',
                 source_chunks=(f'chunk-{index}', f'chunk-{index + 1}'))
    client, budget, _ = api
    # Whole-component closure reaches all 23 jobs. A smaller display page
    # cannot bypass the unchanged 16-result body budget.
    for block in (0, 1, 2, 2, 1, 0):
        response = client.get(f'/sessions/{SID}/transcript?block={block}')
        assert response.status_code == 200, response.text
        page = response.json()
        assert page['optional_closure']['status'] == 'capacity_unresolved'
        assert page['optional_closure']['limit'] == 'result_bodies'
        assert page['optional_closure']['alternatives_complete'] is False
        assert page['selected_runs'] == page['alternatives'] == []
        assert page['reading'] == page['baseline_reading']
        assert len(page['reading']) == (6, 7, 1)[block]
    assert budget._lease_count == budget._lease_bytes == 0


def test_primary_row_and_text_capacity_fail_before_blob_materialization(data,monkeypatch):
    monkeypatch.setattr(c,'MAX_PRIMARY',2)
    with pytest.raises(ReadError,match='unresolved_capacity'): data.page()
    monkeypatch.setattr(c,'MAX_PRIMARY',128)
    with data.db() as db: db.execute('UPDATE segments SET text=? WHERE id=1',('x'*(c.MAX_PRIMARY_BYTES+1),))
    with pytest.raises(ReadError,match='unresolved_capacity'): data.page()


def test_binding_row_quota_length_first(data,monkeypatch):
    data.job(1,0,1)
    data.job(2,1,2)
    monkeypatch.setattr(c,'MAX_BINDINGS',1)
    page = data.page()
    assert page['optional_closure']['limit'] == 'bindings'
    assert page['reading'] == page['baseline_reading']
    assert page['selected_runs'] == page['alternatives'] == []


def test_result_hash_and_response_oversize_are_explicit(data,monkeypatch):
    job = data.job(1,0,10.5)
    with data.db() as db: db.execute(f'UPDATE {history.TABLE} SET result_sha256=? WHERE job_id=?',('0'*64,job['jid']))
    with pytest.raises(ReadError,match='transcript_result_hash_invalid'): data.page()
    with data.db() as db: db.execute(f'UPDATE {history.TABLE} SET result_sha256=? WHERE job_id=?',(job['sha'],job['jid']))
    monkeypatch.setattr(c,'MAX_RESPONSE_BYTES',100)
    with pytest.raises(ReadError,match='unresolved_capacity'): data.page()


def test_one_read_only_snapshot_length_first_no_change_replay(data,monkeypatch):
    data.job(1,0,10.5)
    original, statements, connections = sqlite3.connect, [], []
    def connect(*args,**kwargs):
        connections.append((args,kwargs))
        db = original(*args,**kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(ts.sqlite3,'connect',connect)
    data.page()
    assert len(connections) == 1 and '?mode=ro' in connections[0][0][0]
    assert 'PRAGMA query_only=ON' in statements
    assert sum(sql == 'BEGIN' for sql in statements) == 1
    length = next(i for i,s in enumerate(statements) if 'length(a.binding)' in s)
    materialize = next(i for i,s in enumerate(statements) if s.startswith('SELECT binding'))
    assert length < materialize
    assert all('SELECT *' not in sql and 'result_changes' not in sql or 'SELECT cursor ' in sql for sql in statements)
    assert not any(sql.startswith(('INSERT','UPDATE','DELETE','CREATE')) for sql in statements)


def test_long_chunk_predecessor_and_long_session_seek(data):
    # General primary-only legacy uploads may exceed 10.5 seconds.
    with data.db() as db:
        db.execute('DELETE FROM segments')
        db.execute('DELETE FROM chunks')
        db.execute('DELETE FROM recording_work')
        db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,NULL)',('long',SID,'a'*64,0.,10000.,'completed'))
    data.segment(1,5999,6001,'whole long primary',cid='long')
    value = data.page(block=100)
    assert value['reading'][0]['reference_only'] is True
    assert value['reading'][0]['home_block'] == 99
    assert data.page(block=99)['reading'][0]['text'] == 'whole long primary'


@pytest.mark.parametrize('query', ['block=-1','block=00','block=1&block=2','cursor=x%3D','unknown=1','block=1&cursor=x&x=2'])
def test_strict_queries(api,query):
    client, _, _ = api
    assert client.get(f'/sessions/{SID}/transcript?'+query).status_code == 409


def test_exhausted_shared_response_pool_and_closed_handoff(api):
    client, budget, app = api
    leases = [budget.reserve_jobs_response('result') for _ in range(2)]
    try: assert client.get(f'/sessions/{SID}/transcript').status_code == 503
    finally:
        for lease in leases: lease.release()
    endpoint = next(route.endpoint for route in app.routes if getattr(route,'path',None) == '/sessions/{sid}/transcript')
    done = threading.Event(); done.set()
    request = Request({'type':'http','query_string':b'','_native_support_read_done':done})
    assert endpoint(request,SID).status_code == 503
    assert budget._lease_count == 0


def test_lifetime_holds_lease_through_send_failure_and_disconnect():
    async def scenario(disconnect, fail_send):
        budget = ReadResponseBudget()
        async def inner(scope,receive,send):
            lease = budget.reserve_jobs_response('result')
            assert _assign_native_read_lease(scope,lease)
            assert budget._lease_count == 1
            if disconnect:
                await receive()
                assert scope['_native_support_read_done'].is_set()
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'{}'})
        async def receive(): return {'type':'http.disconnect'}
        async def send(message):
            assert budget._lease_count == 1
            if fail_send: raise ConnectionError('synthetic disconnected transport')
        gate = _NativeSupportReadLifetime(inner)
        try: await gate({'type':'http','path':'/sessions/s/transcript'},receive,send)
        except ConnectionError:
            assert fail_send
        assert budget._lease_count == 0 and budget._lease_bytes == 0
    for disconnect,fail_send in ((False,False),(True,False),(False,True)):
        asyncio.run(scenario(disconnect,fail_send))


def test_snapshot_time_budget_and_database_replacement(data):
    times = iter((0.,1.))
    data.reader.clock = lambda: next(times,1.)
    with pytest.raises(ReadError,match='unresolved_capacity'): data.page()
    data.reader = ts.TranscriptStore(data)
    data.reader.database_identity = (-1,-1)
    with pytest.raises(ReadError,match='transcript_storage_unavailable'): data.page()


@pytest.mark.parametrize('field,value', [('start',1e308),('end',float('inf')),('confidence',float('inf')),
    ('speaker_window_start',float('inf'))])
def test_nonfinite_or_overflowing_primary_is_predictably_unresolved(data,field,value):
    with data.db() as db: db.execute(f'UPDATE segments SET {field}=? WHERE id=1',(value,))
    with pytest.raises(ReadError):
        data.reader.evidence(SID,'p.1.'+'0'*64)
