"""Fresh SQLite closure, page invariance and bounded primary-only fallback.

Only synthetic text/identities and numeric geometry are used. No model, audio,
network, archived database, persistent index or worker is involved.
"""
import base64
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

import pytest

from test_transcript_http import Fixture, SID
from audio_backend import transcript_contract as c, transcript_store as ts
from audio_backend import moss_automatic as auto, moss_result_store as history
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.native_lexical_alternatives import SourceSupport


@pytest.fixture
def data(tmp_path):
    value = Fixture(tmp_path / 'component-fixture.sqlite3')
    with value.db() as db:
        db.execute('DELETE FROM segments')
        db.execute('DELETE FROM recording_work')
        db.execute('DELETE FROM chunks')
    return value


def source(data, end=200):
    data.chunk('source', 0, end)
    with data.db() as db: db.execute('UPDATE sessions SET timeline_end=? WHERE id=?', (end, SID))


def job(data, ident, start, end, **kwargs):
    return data.job(ident, start, end, cid='source', logical_origin=0, **kwargs)


def invalidate(data, value, *, cancelled=False):
    with data.db() as db:
        result = json.loads(value['result'])
        result['schema_errors'] = ['synthetic_parse_unresolved']
        raw = c.encoded(result)
        db.execute(f'UPDATE {history.TABLE} SET result_json=?,result_sha256=?,charge_bytes=? WHERE job_id=?',
            (raw, hashlib.sha256(raw).hexdigest(), len(raw)+len(value['metadata'])+16384, value['jid']))


def selected(page):
    return {key for run in page['selected_runs'] for key in run['candidate_keys']}


def recorded_loads(data, monkeypatch):
    calls, original = [], data.reader._result
    def measured(db, sid, row, *args, **kwargs):
        calls.append(row['job_id'])
        return original(db, sid, row, *args, **kwargs)
    monkeypatch.setattr(data.reader, '_result', measured)
    return calls


def baseline_only(page, limit=None):
    assert page['reading'] == page['baseline_reading']
    assert page['selected_runs'] == page['alternatives'] == []
    closure = page['optional_closure']
    assert closure['complete'] is closure['selection_performed'] is closure['alternatives_complete'] is False
    assert closure['status'] == 'capacity_unresolved'
    if limit is not None: assert closure['limit'] == limit
    assert page['reading_status'] == 'primary_only_optional_closure_unresolved'
    assert len(page['notices']) == 1 and page['notices'][0]['evidence_complete'] is False


def test_exact_adjacency_closes_both_directions_with_stable_whole_run(data):
    source(data)
    data.segment(1, 49, 71, 'whole straddling unit', cid='source')
    left, right = job(data, 1, 40, 60), job(data, 2, 60, 80)
    forward = [data.page(block=b) for b in (0, 1)]
    reverse = [data.page(block=b) for b in (1, 0)]
    assert forward == list(reversed(reverse))
    assert forward[0]['selected_runs'] == forward[1]['selected_runs']
    run = forward[0]['selected_runs'][0]
    assert run['candidate_keys'] == [left['key'], right['key']]
    assert (run['start_frame'], run['end_frame']) == (40*c.RATE, 80*c.RATE)
    assert len(run['primary_source_refs']) == 1
    for block, page in enumerate(forward):
        assert page['optional_closure']['complete'] is True
        assert all(item['source'] == 'moss' for item in page['reading'])
        assert sum(not item['reference_only'] for item in page['reading']) == 2
        assert all(item['home_block'] == block for item in page['reading'] if not item['reference_only'])
        assert len(page['reading']) == 4
        assert len(c.encoded(page)) <= c.MAX_RESPONSE_BYTES


def test_disjoint_page_seeds_have_separate_global_runs(data):
    source(data)
    data.segment(1, 49, 71, 'first component', cid='source')
    data.segment(2, 105, 109, 'second component', cid='source')
    jobs = [job(data, 1, 40, 60), job(data, 2, 60, 80), job(data, 3, 100, 110)]
    page = data.page(block=1)
    assert [run['candidate_keys'] for run in page['selected_runs']] == [
        [jobs[0]['key'], jobs[1]['key']], [jobs[2]['key']]]
    assert [len(run['primary_source_refs']) for run in page['selected_runs']] == [1, 1]
    assert page['selected_runs'][0] == data.page()['selected_runs'][0]


def test_ineligible_exact_touch_bridge_consumes_body_without_expanding(data, monkeypatch):
    source(data)
    data.segment(1, 41, 59, 'left whole unit', cid='source')
    left, invalid, remote = job(data, 1, 40, 60), job(data, 2, 60, 80), job(data, 3, 80, 100)
    invalidate(data, invalid)
    calls = recorded_loads(data, monkeypatch)
    page = data.page()
    assert selected(page) == {left['key']}
    assert set(calls) == {left['jid'], invalid['jid']}
    assert remote['jid'] not in calls
    assert page['optional_closure']['complete'] is True


def test_primary_primary_overlap_never_bridges_components(data, monkeypatch):
    source(data)
    data.segment(1, 50, 65, 'page seed', cid='source')
    data.segment(2, 64, 90, 'not a graph neighbor', cid='source')
    left, remote = job(data, 1, 40, 60), job(data, 2, 80, 100)
    calls = recorded_loads(data, monkeypatch)
    page = data.page()
    assert calls == [left['jid']] and remote['jid'] not in calls
    assert selected(page) == set()
    assert len(page['baseline_reading']) == 1


def test_full_primary_tail_seeds_candidates_without_initial_page_candidate(data, monkeypatch):
    source(data)
    data.segment(1, 50, 130, 'unsliced long primary', cid='source')
    remote = job(data, 1, 100, 120)
    calls = recorded_loads(data, monkeypatch)
    page = data.page()
    assert calls == [remote['jid']]
    assert page['reading'] == page['baseline_reading'] and len(page['reading']) == 1
    assert page['reading'][0]['end_frame'] == 130*c.RATE
    assert not page['selected_runs']


def test_primary_sql_guard_filters_exact_outward_frame_geometry(data):
    source(data)
    # Both raw floats sit on the other side of the page cut, but the existing
    # near-integer frame policy snaps them exactly to that cut.
    data.segment(1, math.nextafter(60., -math.inf), 61., 'home next page', cid='source')
    data.segment(2, 59., math.nextafter(60., math.inf), 'ends at cut', cid='source')
    data.segment(3, 59.5, (60*c.RATE+.25)/c.RATE, 'outward one-frame tail', cid='source')
    first, second = data.page(), data.page(block=1)
    assert [x['canonical_asr']['id'] for x in first['baseline_reading']] == [2, 3]
    assert [x.get('canonical_asr', {}).get('id') for x in second['baseline_reading'] if not x['reference_only']] == [1]
    assert len(second['baseline_reading']) == 2
    assert first['baseline_reading'][1]['end_frame'] == 60*c.RATE+1


def test_seventeen_body_chain_discards_all_optional_prefixes(data, monkeypatch):
    source(data, 340)
    data.segment(1, 1, 2, 'complete page baseline', cid='source')
    for n in range(17): job(data, n+1, n*20, (n+1)*20)
    calls = recorded_loads(data, monkeypatch)
    for block in (*range(6), *reversed(range(6))):
        calls.clear()
        baseline_only(data.page(block=block), 'result_bodies')
        assert len(calls) <= 16 and len(calls) == len(set(calls))


def test_independent_components_share_the_body_budget(data):
    source(data)
    data.segment(1, .1, .9, 'complete primary', cid='source')
    for n in range(17): job(data, n+1, n*3, n*3+1)
    baseline_only(data.page(), 'result_bodies')


@pytest.mark.parametrize('name,limit,expected', [
    ('MAX_RESULTS_BYTES', 1, 'results_bytes'), ('MAX_RESULT_BYTES', 1, 'result_bytes'),
    ('MAX_METADATA_BYTES', 1, 'result_or_metadata_bytes'),
    ('MAX_BINDINGS', 1, 'bindings'), ('MAX_BINDING_BYTES', 1, 'binding_bytes'),
])
def test_optional_admission_caps_keep_complete_initial_baseline(data, monkeypatch, name, limit, expected):
    source(data)
    data.segment(1, 1, 2, 'baseline', cid='source')
    job(data, 1, 0, 10)
    job(data, 2, 10, 20)
    monkeypatch.setattr(c, name, limit)
    baseline_only(data.page(), expected)


def test_expanded_primary_capacity_never_truncates_initial_baseline(data, monkeypatch):
    source(data)
    data.segment(1, 50, 65, 'initial page whole unit', cid='source')
    data.segment(2, 66, 69, 'frontier unit', cid='source')
    job(data, 1, 50, 70)
    monkeypatch.setattr(c, 'MAX_PRIMARY', 1)
    page = data.page()
    baseline_only(page, 'primary_units')
    assert len(page['reading']) == 1 and page['reading'][0]['text'] == 'initial page whole unit'


def test_expanded_chunk_capacity_is_not_reset_per_frontier(data, monkeypatch):
    for n in range(3): data.chunk(f'chunk-{n}', 40+n*20, 60+n*20)
    data.segment(1, 41, 59, 'baseline', cid='chunk-0')
    for n in range(3): data.job(n+1, 40+n*20, 60+n*20, cid=f'chunk-{n}', logical_origin=0)
    monkeypatch.setattr(ts, 'MAX_CHUNKS', 2)
    baseline_only(data.page(), 'chunks')


def test_each_payload_loaded_once_in_one_indexed_snapshot(data, monkeypatch):
    source(data)
    data.segment(1, 49, 71, 'whole primary', cid='source')
    jobs = [job(data, 1, 40, 60), job(data, 2, 60, 80)]
    original, statements, connections = sqlite3.connect, [], []
    def connect(*args, **kwargs):
        connections.append((args, kwargs))
        db = original(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(ts.sqlite3, 'connect', connect)
    page = data.page()
    assert selected(page) == {value['key'] for value in jobs}
    assert len(connections) == 1 and '?mode=ro' in connections[0][0][0]
    assert statements.count('BEGIN') == 1
    assert sum(sql.startswith('SELECT binding ') for sql in statements) == 2
    assert sum(sql.startswith('SELECT result_json,metadata_json') for sql in statements) == 2
    assert sum(sql.startswith('SELECT s.id,s.chunk_id,') for sql in statements) == 1
    assert any('INDEXED BY chunk_timeline' in sql for sql in statements)
    assert any('INDEXED BY segments_chunk' in sql for sql in statements)
    assert any('INDEXED BY auto_cid1' in sql for sql in statements)
    assert any('INDEXED BY auto_cid2' in sql for sql in statements)
    assert not any('INDEXED BY auto_session_seq' in sql for sql in statements)
    assert not any(sql.startswith(('UPDATE', 'INSERT', 'DELETE', 'CREATE')) for sql in statements)


@pytest.mark.parametrize('prefix', ['SELECT binding FROM', 'SELECT result_json,metadata_json', 'SELECT s.id,s.chunk_id,'])
def test_required_length_then_payload_row_missing_is_not_capacity_fallback(data, monkeypatch, prefix):
    source(data)
    data.segment(1, 49, 71, 'whole primary', cid='source')
    job(data, 1, 40, 60)
    original = sqlite3.connect
    class MissingRow:
        def fetchone(self): return None
    class Connection:
        def __init__(self, wrapped): self.wrapped = wrapped
        def __setattr__(self, name, value):
            if name == 'wrapped': object.__setattr__(self, name, value)
            else: setattr(self.wrapped, name, value)
        def __getattr__(self, name): return getattr(self.wrapped, name)
        def execute(self, sql, *args):
            if sql.startswith(prefix): return MissingRow()
            return self.wrapped.execute(sql, *args)
    monkeypatch.setattr(ts.sqlite3, 'connect', lambda *a, **k: Connection(original(*a, **k)))
    with pytest.raises(ReadError, match='transcript_dependency_missing'):
        data.page()


@pytest.mark.parametrize('mutation', ['cancelled', 'receipt', 'delete_work'])
def test_revoked_frontier_source_cannot_bridge_and_invalidates_cursor(data, mutation):
    data.chunk('left', 40, 60)
    data.chunk('right', 60, 80)
    data.segment(1, 55, 65, 'whole unit', cid='left')
    # Keep the five-second ownership bound valid on the right source.
    with data.db() as db: db.execute("UPDATE segments SET chunk_id='right'")
    left = data.job(1, 40, 60, cid='left', logical_origin=0)
    right = data.job(2, 60, 80, cid='right', logical_origin=0)
    first = data.page()
    assert selected(first) == {left['key'], right['key']}
    with data.db() as db:
        if mutation == 'cancelled': db.execute("UPDATE chunks SET status='cancelled' WHERE id='right'")
        elif mutation == 'receipt': db.execute("UPDATE recording_work SET receipt_id=? WHERE chunk_id='right'", ('0'*64,))
        else: db.execute("DELETE FROM recording_work WHERE chunk_id='right'")
    with pytest.raises(ReadError, match='transcript_revision_changed'):
        data.page(cursor=first['next_cursor'])
    assert not data.page()['selected_runs']


def test_old_policy_cursor_rejected_after_component_and_fallback_policy_change(data):
    source(data)
    prior = deepcopy(c.POLICY)
    prior.pop('decision_scope')
    prior.pop('optional_closure')
    prior['discovery'] = 'indexed-both-source-aliases-all-states'
    prior['block_crossing'] = 'unresolved-alternative'
    old_hash = c.digest(prior)
    assert old_hash != c.POLICY_HASH
    value = {'policy': old_hash, 'session': SID, 'block': 1, 'revision': 'a'*64}
    token = base64.urlsafe_b64encode(c.encoded(value)).decode().rstrip('=')
    with pytest.raises(ReadError, match='invalid_transcript_cursor') as exc:
        data.page(cursor=token)
    assert exc.value.status_code == 409


@pytest.mark.parametrize('when', ['optional_start', 'after_serialization'])
def test_deadline_failure_is_never_reclassified_as_optional_capacity(data, monkeypatch, when):
    source(data)
    data.segment(1, 1, 2, 'baseline', cid='source')
    job(data, 1, 0, 10)
    clock = [0.]
    data.reader.clock = lambda: clock[0]
    if when == 'optional_start':
        original = data.reader._closure
        def delayed(*args, **kwargs):
            clock[0] = 1.
            return original(*args, **kwargs)
        monkeypatch.setattr(data.reader, '_closure', delayed)
    else:
        original = c.transcript_response
        def delayed(value):
            raw = original(value)
            clock[0] = 1.
            return raw
        monkeypatch.setattr(c, 'transcript_response', delayed)
    with pytest.raises(ReadError, match='unresolved_capacity'):
        data.page()


def test_actual_serialized_baseline_overflow_remains_failure(data):
    source(data)
    data.segment(1, 1, 2, 'x'*100000, cid='source')
    for n in range(17): job(data, n+1, n*3, n*3+1)
    with pytest.raises(ReadError, match='unresolved_capacity'):
        data.page()


def test_vm_work_budget_remains_shared_and_fatal(data, monkeypatch):
    source(data)
    data.segment(1, 1, 2, 'baseline', cid='source')
    job(data, 1, 0, 10)
    monkeypatch.setattr(ts, 'MAX_VM_STEPS', 100)
    with pytest.raises(ReadError, match='unresolved_capacity'):
        data.page()


@pytest.mark.parametrize('count', [1, 18])
@pytest.mark.parametrize('missing', ['history', 'job_tuple'])
def test_committed_intent_with_missing_required_history_is_failure(data, count, missing):
    source(data)
    data.segment(1, 1, 2, 'complete primary', cid='source')
    value = job(data, 1, 0, 10)
    for index in range(1, count): job(data, index+1, index, index+10)
    with data.db() as db:
        if missing == 'history':
            db.execute(f'DELETE FROM {history.TABLE} WHERE job_id=?', (value['jid'],))
        else:
            db.execute(f'UPDATE {auto.TABLE} SET job_id=NULL,jobs_epoch=NULL,request_sha256=NULL WHERE job_id=?',
                (value['jid'],))
    with pytest.raises(ReadError, match='transcript_dependency_missing'):
        data.page()


@pytest.mark.parametrize('column', ['result_json', 'metadata_json'])
def test_empty_required_payload_is_not_an_optional_admission_cap(data, column):
    source(data)
    value = job(data, 1, 0, 10)
    with data.db() as db:
        db.execute(f'UPDATE {history.TABLE} SET {column}=?,charge_bytes=16384+length(' +
            ('metadata_json' if column == 'result_json' else 'result_json') + ') WHERE job_id=?',
            (b'', value['jid']))
    with pytest.raises(ReadError, match='unresolved_capacity'):
        data.page()


def test_optional_link_stripping_preserves_explicit_primary_only_status(monkeypatch):
    value = {'reading_status': 'primary_only_optional_closure_unresolved',
        'optional_closure': {'complete': False, 'status': 'capacity_unresolved'},
        'reading': [], 'baseline_reading': [], 'selected_runs': [], 'alternatives': [],
        'recording_speaker_links': {'padding': 'x'*1000}}
    minimal = deepcopy(value)
    minimal.pop('recording_speaker_links')
    monkeypatch.setattr(c, 'MAX_RESPONSE_BYTES', len(c.encoded(minimal)))
    actual = json.loads(c.transcript_response(value))
    assert actual == minimal
    assert actual['reading_status'] == 'primary_only_optional_closure_unresolved'


def _synthetic_text(length, letter='x'):
    return None if length is None else letter * length


def _saved_scope(data, fixture, scope):
    """Rebuild numeric geometry with fresh identities and real durable validators.

    ASCII placeholders retain saved JSON lexical lengths. This can enlarge raw
    UTF-8 field storage; measured synthetic SQLite/response costs are reported
    separately from the saved numeric selection parity.
    """
    with data.db() as db:
        db.execute('DELETE FROM segments')
        db.execute('DELETE FROM recording_work')
        db.execute('DELETE FROM chunks')
        db.execute('UPDATE recording_batches SET capture_origin_frame=0')
        db.execute('UPDATE sessions SET timeline_end=? WHERE id=?',
            (scope['timeline_end_frame']/c.RATE, SID))
        for row in fixture['chunks']:
            if row['scope'] != scope['ordinal']: continue
            cid = f"synthetic-chunk-{row['ordinal']}"
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,NULL)',
                (cid, SID, 'd'*64, row['timeline_start_seconds'], row['duration_seconds'], 'completed'))
            db.execute('INSERT INTO recording_work VALUES(?,?,?,?,?)',
                (cid, 'batch', row['frames'], 'e'*64, row['capture_start_frame']))
        for row in fixture['primary']:
            if row['scope'] != scope['ordinal']: continue
            db.execute('INSERT INTO segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (row['ordinal'], f"synthetic-chunk-{row['chunk']}",
                 row['canonical_start_seconds'], row['canonical_end_seconds'],
                 _synthetic_text(row['text_length']), _synthetic_text(row['raw_text_length']),
                 _synthetic_text(row['asr_backend_length'], 'a'),
                 _synthetic_text(row['timestamp_kind_length'], 't'),
                 None if row['confidence_is_null'] else .8,
                 _synthetic_text(row['speaker_local_id_length'], 'S'),
                 row['speaker_window_start'], None))
        keys = {}
        for row in fixture['candidates']:
            if row['scope'] != scope['ordinal']: continue
            ident, kind = row['ordinal'], row['kind']
            parts = [dict(source_sha256='d'*64,
                **{key: value for key, value in part.items() if key != 'chunk'}) for part in row['parts']]
            aliases = [dict(chunk_id=f"synthetic-chunk-{part['chunk']}", receipt_sha256='e'*64)
                       for part in row['parts']]
            binding = dict(automation_policy=auto.POLICY,
                selection_policy={'ordinary':ts.CELL_POLICY,'core_seam':ts.SEAM_POLICY,
                                  'reset_seam':ts.RESET_POLICY_SHA256}[kind],
                kind=kind,session_id=SID,parts=parts,source_aliases=aliases,
                support_frames=row['support_frames'],core_frames=row['core_frames'])
            raw_binding = c.encoded(binding)
            key = hashlib.sha256(raw_binding).hexdigest()
            keys[key] = ident
            jid, epoch, request_sha = f'{ident:032x}', 'a'*32, f'{ident:064x}'
            support = SourceSupport(source_generation=c.digest({'schema':'immutable-source-cell-generation-v1','sources':parts}),
                plan_sha256=c.digest({'schema':'bounded-cell-mapping-v1','parts':parts}),
                native_runtime_sha256='b'*64,core_sha256='c'*64,pcm16le_sha256='f'*64,
                float32le_sha256='9'*64,**row['support'])
            identity = history.HistoryIdentity(jid, SID, epoch, f'synthetic-job-{ident}', request_sha)
            cell = {'cell_id':f'{ident+1000:064x}','proof_sha256':f'{ident+2000:064x}','support':support.to_dict()}
            metadata = history.build_metadata(identity,
                context={'automatic_candidate_key':key,'origin':'durable_cell','support_id':support.support_id},
                support=support.to_dict(),cell_binding=cell)
            phrases = [dict(raw_text=_synthetic_text(p['raw_text_length']),
                speaker_local_id=_synthetic_text(p['speaker_local_id_length'], chr(64+p['speaker_ordinal'])),
                start=p['start_seconds'],end=p['end_seconds'],
                timing_scope='support-relative-model-unverified',speaker_identity_valid=False)
                for p in row['phrases']]
            result = c.encoded(dict(schema='moss-handy-diagnostic-v1',support_id=support.support_id,
                complete_content=False,eos=None,timestamp_quality='model_generated_unverified',
                speaker_identity_valid=False,profile_update_allowed=False,cell_binding=cell,
                diagnostic_phrases=phrases,schema_errors=row['schema_errors']))
            sha = hashlib.sha256(result).hexdigest()
            roots = [f'synthetic-chunk-{n}' for n in row['commit_roots']]
            db.execute(f'INSERT INTO {auto.TABLE}(' + auto.COLUMNS + ') VALUES(' + ','.join('?' for _ in auto.COLUMNS.split(',')) + ')',
                (row['automatic_sequence'],key,SID,kind,raw_binding,cell['proof_sha256'],cell['cell_id'],
                 f'{ident*2:064x}',f'{ident*2+1:064x}' if len(roots)==2 else None,
                 roots[0],roots[1] if len(roots)==2 else None,'committed','synthetic',0.,0.,
                 jid,epoch,request_sha,16384+len(raw_binding)))
            db.execute(f'INSERT INTO {history.TABLE}(archive_seq,job_id,session_id,original_jobs_epoch,'
                'idempotency_key,canonical_request_sha256,state,schema_version,metadata_json,result_json,'
                'result_sha256,committed_at,charge_bytes) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?)',
                (row['archive_seq'],*identity.values(),'committed',metadata,result,sha,1.,
                 16384+len(metadata)+len(result)))
    return keys


def test_saved_numeric_geometry_uses_real_sqlite_validators_and_serialization(tmp_path, monkeypatch, record_property):
    fixture = json.loads((Path(__file__).parent/'fixtures/transcript_components.json').read_text())
    assert (len(fixture['candidates']),len(fixture['primary']),len(fixture['chunks'])) == (52,321,174)
    assert (c.MAX_RESULTS,ts.READ_SECONDS,ts.MAX_VM_STEPS,c.MAX_RESPONSE_BYTES) == (16,.75,250000,262144)
    original_connect, original_result = sqlite3.connect, ts.TranscriptStore._result
    observed, eligibility, snapshots = [], {}, []
    class Connection:
        def __init__(self, wrapped):
            object.__setattr__(self,'wrapped',wrapped)
            object.__setattr__(self,'steps',0)
        def __setattr__(self,name,value): setattr(self.wrapped,name,value)
        def __getattr__(self,name): return getattr(self.wrapped,name)
        def set_progress_handler(self, callback, interval):
            def measured():
                object.__setattr__(self,'steps',self.steps+interval)
                return callback()
            return self.wrapped.set_progress_handler(measured,interval)
    def connect(*args, **kwargs):
        raw = original_connect(*args, **kwargs)
        if '?mode=ro' not in str(args[0]): return raw
        wrapped = Connection(raw)
        snapshots.append(wrapped)
        return wrapped
    def result(db, sid, row, *args, **kwargs):
        value = original_result(db,sid,row,*args,**kwargs)
        observed.append(row['job_id'])
        eligibility[int(row['job_id'],16)] = value[0]['eligible']
        return value
    monkeypatch.setattr(ts.sqlite3,'connect',connect)
    monkeypatch.setattr(ts.TranscriptStore,'_result',staticmethod(result))
    selected_ids, replaced_ids, canonical_runs, selected_pages = set(),set(),{},set()
    maxima = {'result_bodies':0,'vm_steps':0,'serialized_response_bytes':0,'page_seconds':0.}
    for scope in fixture['scopes']:
        data = Fixture(tmp_path/f"scope-{scope['ordinal']}.sqlite3")
        keys = _saved_scope(data,fixture,scope)
        before = data.path.read_bytes()
        pages = {}
        for block in (*range(scope['page_count']),*reversed(range(scope['page_count']))):
            observed.clear()
            begun = time.monotonic()
            raw = data.reader.page(SID,block=block)
            elapsed = time.monotonic()-begun
            page = json.loads(raw)
            assert page['optional_closure']['complete'] is True
            assert page['notices'] == []
            assert len(observed) == len(set(observed)) <= 16
            assert snapshots[-1].steps < 250000 and elapsed < .75
            assert len(raw) <= 262144
            if block in pages: assert raw == pages[block]
            pages[block] = raw
            maxima['result_bodies'] = max(maxima['result_bodies'],len(observed))
            maxima['vm_steps'] = max(maxima['vm_steps'],snapshots[-1].steps)
            maxima['serialized_response_bytes'] = max(maxima['serialized_response_bytes'],len(raw))
            maxima['page_seconds'] = max(maxima['page_seconds'],elapsed)
            expected = [r for r in fixture['expected']['runs'] if r['scope']==scope['ordinal']
                and r['start_frame']<page['end_frame'] and page['start_frame']<r['end_frame']]
            actual = [dict(scope=scope['ordinal'],start_frame=r['start_frame'],end_frame=r['end_frame'],
                candidate_ordinals=[keys[k] for k in r['candidate_keys']],
                primary_ordinals=[int(ref.split('.')[1]) for ref in r['primary_source_refs']])
                for r in page['selected_runs']]
            assert actual == expected
            for run in actual:
                selected_ids.update(run['candidate_ordinals'])
                replaced_ids.update(run['primary_ordinals'])
                canonical_runs[(run['scope'],run['start_frame'],run['end_frame'])] = run
                selected_pages.add((scope['ordinal'],block))
            for item in page['reading']:
                if not item['reference_only']: assert item['home_block'] == block
                if item['source']=='primary': assert int(item['source_ref'].split('.')[1]) not in replaced_ids
        assert data.path.read_bytes() == before
    assert len(snapshots) == 60
    assert len(eligibility) == 52 and sum(eligibility.values()) == 43
    assert selected_ids == set(fixture['expected']['selected_candidate_ordinals'])
    assert replaced_ids == set(fixture['expected']['replaced_primary_ordinals'])
    assert (len(selected_ids),len(replaced_ids),len(selected_pages)) == (7,52,6)
    assert sum(r['end_frame']-r['start_frame'] for r in canonical_runs.values()) == 83*c.RATE
    assert maxima['result_bodies'] == 8
    record_property('saved_numeric_geometry_and_fresh_sqlite',json.dumps(dict(
        selected_jobs=7,selected_seconds=83,replaced_whole_primary=52,selected_pages=6,
        page_reads=60,fully_validated_jobs=52,eligible_jobs=43,**maxima),sort_keys=True))
