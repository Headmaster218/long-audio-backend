"""Fresh synthetic migration, accounting and complete-root contracts.

Authored for a separate execution gate. Small SQLite fixtures use production
schemas and pure, producer-shaped synthetic proofs. The final integration case
uses the existing generated-PCM/fake-native pipeline, not historical recordings
or actual native segment history. No model or acoustic qualification is implied.
"""
import hashlib
import json
import sqlite3
import time
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from audio_backend import moss_automatic as auto
from audio_backend import moss_result_store as history
from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import transcript_contract as contract
from audio_backend import transcript_revision as revision
from audio_backend import transcript_store as transcripts
from audio_backend.native_owned_cell_store import Budget, CellStore, Operation
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.settings import Settings
from audio_backend.storage import Store
from test_bounded_core_support import SID, synthetic_case, final_cell, primary_row, ordinary_origin
from test_bounded_support_intents import (
    automatic_app_factory, bounded_app_factory, advance_to_waiter, c_target,
    process_primary, primary_output, intent_rows, wav_bytes, fixture_pcm, C_SUPPORT)
from test_native_owned_receipt_gc import fresh_receipts


STORE_KEY = '5' * 64
BATCH = 'synthetic-bounded-batch'
OTHER = 'unrelated-synthetic-session'


def rows(db, table, columns='*'):
    return [tuple(row) for row in db.execute(
        f'SELECT {columns} FROM {table} ORDER BY rowid')]


def schema(db):
    return [tuple(row) for row in db.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]


def legacy_exact_init_schema(db):
    """Frozen pre-v2 initializer algorithm, using the unchanged v1 DDL map.

    This is the exact old-binary equality rule, not the new additive matcher.
    The test never imports or opens a historical binary/database.
    """
    present = [db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
               for name in auto.SCHEMA]
    if any(present) and not all(present):raise ValueError('Incomplete automatic MOSS schema')
    for (name, sql), row in zip(auto.SCHEMA.items(), present):
        if row is None:db.execute(sql)
        elif ' '.join(row[0].split()) != ' '.join(sql.split()):
            raise ValueError('Incompatible automatic MOSS schema')
    if not any(present):db.execute(f'INSERT INTO {auto.USAGE} VALUES(?,0,0,0,0)', ('',))


@pytest.fixture
def legacy_store(tmp_path):
    # Store construction creates schemas only; no worker, owner or model starts.
    settings = Settings(data_dir=tmp_path, start_worker=False)
    store = Store(tmp_path / 'fresh-bounded-roots.sqlite3', settings)
    with store.db() as db:
        core.init_schema(db)
        for sid in (SID, OTHER):
            db.execute('''INSERT INTO sessions(id,name,identity_group,created,timeline_end)
                VALUES(?,?,?,?,120.)''', (sid, 'synthetic roots', sid, 1.))
    return store


def test_legacy_owner_calls_zero_argument_store_policy_hook(legacy_store, tmp_path, monkeypatch):
    expected = core.store_policy()
    calls = []
    def legacy_policy():
        calls.append(())
        return expected
    monkeypatch.setattr(core, 'store_policy', legacy_policy)
    directory = tmp_path / 'unstarted-legacy-owner'
    owner = CellStore(directory, SimpleNamespace(store=legacy_store),
        enabled=True, core_seam_enabled=True)
    assert calls == [()]
    assert owner._store_policy == expected
    assert owner.bounded_core_enabled is False
    assert owner._thread is None and not directory.exists()


def test_legacy_claim_and_commit_use_zero_argument_database_hook(legacy_store, monkeypatch):
    store = legacy_store
    with store.db() as db:
        db.execute("""INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
            timeline_start,status,next_offset,created) VALUES('legacy-hook',?,'legacy-hook',?,
            '/synthetic-not-opened',1.,0.,'queued',0.,1.)""", (SID, 'a' * 64))
    database = store.db; calls = []
    @contextmanager
    def legacy_database():
        calls.append(())
        with database() as db:yield db
    monkeypatch.setattr(store, 'db', legacy_database)
    chunk = store.claim()
    assert chunk['id'] == 'legacy-hook' and calls == [()]
    result = {'segments': [{'start': 0., 'end': 1., 'text': 'synthetic legacy output',
                           'raw_text': 'synthetic legacy raw'}], 'events': []}
    assert store.commit_window(chunk, 0., 1., result) is True
    assert calls == [(), ()]
    with database() as db:
        assert tuple(db.execute("SELECT status,next_offset FROM chunks WHERE id='legacy-hook'").fetchone()) == ('completed', 1.)
        assert [tuple(row) for row in db.execute("SELECT text,raw_text FROM segments WHERE chunk_id='legacy-hook'")] == [
            ('synthetic legacy output', 'synthetic legacy raw')]
    assert store.claim() is None and calls == [(), (), ()]


def migrate(db):
    if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
    auto.init_bounded_schema(db, enable=True)
    revision.init_bounded_schema(db)


def insert_legacy_row(db, *, index=1, sid=SID):
    """Accounting-only v1 payload; deliberately not a native authority fixture."""
    proof = cells.canonical({'synthetic_v1_ledger_only': index})
    binding = cells.canonical({'synthetic_v1_binding_only': index})
    key = hashlib.sha256(binding).hexdigest()
    charge = auto.ACCOUNTING_BYTES + len(proof) + len(binding)
    db.execute(f'''INSERT INTO {auto.TABLE}(candidate_key,sid,kind,binding,proof,
        proof_sha256,cell_id,slot1,cid1,state,reason,created,updated,charge_bytes)
        VALUES(?,?,'ordinary',?,?,?,?,?,?,'queued','synthetic_ledger_only',1.,1.,?)''',
        (key, sid, binding, proof, hashlib.sha256(proof).hexdigest(),
         cells.sha({'cell': index}), cells.sha({'slot': index}), f'legacy-{index}', charge))
    auto._charge(db, sid, (1, charge, 1, len(proof)))
    return key


def read_intent(db, key):
    return dict(db.execute(f'SELECT * FROM {auto.TABLE} WHERE candidate_key=?', (key,)).fetchone())


def assert_accounting(db, sid=SID):
    retained = [dict(row) for row in db.execute(f'SELECT * FROM {auto.TABLE}')]
    for scope, observed in zip(('', sid), auto.usage(db, sid)):
        selected = retained if scope == '' else [row for row in retained if row['sid'] == sid]
        expected = (len(selected), sum(row['charge_bytes'] for row in selected),
            sum(row['state'] in auto.ACTIVE for row in selected),
            sum(len(row['proof'] or b'') + len(row.get('target_body') or b'') for row in selected))
        assert tuple(observed) == expected


def seed_bound(db):
    """Four distinct source roots; original ownership is only the first root."""
    case = synthetic_case([0, 16, 32, 48, 64], [[2, 8]], separate_sources=True)
    parent = primary_row(1, 0, 64, cid='source-0')
    cell = final_cell(case, [parent]); doc = json.loads(cell.proof)
    db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,created,
        capture_origin_frame,native_core_ordinal,retention_store_key)
        VALUES(?,?,?,1.,0,4,?)''', (BATCH, SID, 'synthetic-capture', STORE_KEY))
    for index, commit in enumerate(case['commits']):
        part = commit['part']; source = case['sources'][part['chunk_id']]
        db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
            timeline_start,status,next_offset,created,metadata) VALUES(?,?,?,?,?,?,?,'completed',?,1.,?)''',
            (source['id'], SID, source['id'], source['digest'], '/synthetic-not-opened/' + source['id'],
             source['duration'], source['timeline_start'], source['duration'], '{"synthetic":true}'))
        db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,
            capture_start_frame,native_receipt_status,receipt_id,receipt_state)
            VALUES(?,?,?,?,?,?,'synthetic',?,'durable')''',
            (source['id'], BATCH, index, part['source_frames'], part['source_frames'] * 2,
             part['logical_start'], part['receipt_sha256']))
        db.execute('''INSERT INTO native_core_commits(slot,sid,cid,scope_key,logical_end,body,
            pending,state,reason,recording_batch_id,commit_ordinal,retention_store_key)
            VALUES(?,?,?,?,?,?,NULL,'none','synthetic',?,?,?)''',
            (core.slot(commit), SID, source['id'], core.scope_key(commit['scope']),
             part['logical_end'], cells.canonical(commit, core.COMPACT_BYTES), BATCH, index + 1, STORE_KEY))
    names = list(parent)
    db.execute('INSERT INTO segments(' + ','.join(names) + ',speaker_status) VALUES(' +
        ','.join('?' for _ in names) + ",'unknown')", tuple(parent[name] for name in names))
    origin = case['target']['origins'][0]
    proof, binding = origin['proof'].encode('ascii'), origin['binding'].encode('ascii')
    original_doc, support, _ = cells.validate_document(proof)
    original = case['commits'][0]
    proposal = {'key': hashlib.sha256(binding).hexdigest(), 'sid': SID, 'kind': 'ordinary',
        'binding': binding, 'proof': proof, 'proof_sha256': hashlib.sha256(proof).hexdigest(),
        'cell_id': cells.sha({'policy': original_doc['policy'], 'support': support.to_dict()}),
        'slot1': core.slot(original), 'slot2': None, 'cid1': 'source-0', 'cid2': None,
        'target': case['target']}
    context = {'batch_id': BATCH, 'ordinal': 1, 'retention_store_key': STORE_KEY}
    now = time.time()
    with Budget().phase() as budget:
        waiting, status = auto._register_target(db, proposal, context,
            cells.sha(original['scope']), now, budget)
        assert status == 'registered'
        context = {**context, 'ordinal': 4}
        final = {'target': case['target'], 'commits': doc['commits'], 'query': doc['query'],
            'evidence': doc['right_evidence'], 'support': doc['geometry'], 'pcm': cell.pcm}
        assert auto._bind_target(db, waiting, final, context, doc['inventories'], budget, now) == cell.cell_id
        row = auto._read_v2(db, key=proposal['key'], budget=budget)
        assert auto.qualify_bounded_document(db, doc, budget, STORE_KEY)
    assert len(doc['commits']) == 4
    assert row['cid2'] is None and {root['cid'] for root in doc['dependency_roots']} == set(case['sources'])
    return SimpleNamespace(case=case, cell=cell, doc=doc, row=row, key=proposal['key'])


@pytest.fixture
def bound_ledger(legacy_store):
    with legacy_store.db() as db:
        migrate(db)
        value = seed_bound(db)
        assert_accounting(db)
    value.store = legacy_store
    return value


def test_additive_migration_preserves_all_legacy_payloads_and_old_binary_refuses(legacy_store):
    with legacy_store.db() as db:
        insert_legacy_row(db)
        historical = synthetic_case([0, 16], [[2, 8]])['commits'][0]
        db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
            timeline_start,status,next_offset,created,metadata) VALUES('legacy-source',?,
            'legacy-source',?,'/synthetic-not-opened',.001,0.,'completed',.001,1.,?)''',
            (SID, 'a' * 64, '{"retained_metadata":"unchanged"}'))
        db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,created)
            VALUES('legacy-batch',?,'legacy-capture',1.)''', (SID,))
        # This row's body is retained history only, never offered as authority.
        db.execute('''INSERT INTO native_core_commits(slot,sid,cid,scope_key,logical_end,
            body,pending,state,reason) VALUES(?,?,'legacy-source',?,16,?,NULL,'none','historical_fixture')''',
            (core.slot(historical), SID, core.scope_key(historical['scope']),
             cells.canonical(historical, core.COMPACT_BYTES)))
        legacy_exact_init_schema(db)
        old_columns = ','.join(row['name'] for row in db.execute(f'PRAGMA table_info({auto.TABLE})'))
        compact_columns = ','.join(row['name'] for row in db.execute('PRAGMA table_info(native_core_commits)'))
        batch_columns = ','.join(row['name'] for row in db.execute('PRAGMA table_info(recording_batches)'))
        before = rows(db, auto.TABLE, old_columns), rows(db, auto.USAGE), rows(db, history.TABLE)
        old_sources = rows(db, 'chunks'), rows(db, 'native_core_commits', compact_columns), rows(db, 'recording_batches', batch_columns)
        old_triggers = {row[0]: row[1] for row in db.execute(
            "SELECT name,sql FROM sqlite_master WHERE name LIKE 'transcript_revision_v1_%'")}
        statements = []; db.set_trace_callback(statements.append)
        migrate(db)
        db.set_trace_callback(None)
        assert (rows(db, auto.TABLE, old_columns), rows(db, auto.USAGE), rows(db, history.TABLE)) == before
        assert rows(db, auto.TABLE, ','.join(auto.V2_COLUMNS)) == [(None,) * len(auto.V2_COLUMNS)]
        assert (rows(db, 'chunks'), rows(db, 'native_core_commits', compact_columns),
                rows(db, 'recording_batches', batch_columns)) == old_sources
        assert rows(db, 'native_core_commits', ','.join(auto.COMPACT_V2_COLUMNS)) == [(None, None, None)]
        assert rows(db, 'recording_batches', 'native_core_ordinal,retention_store_key') == [(None, None)]
        assert not rows(db, auto.DEPENDENCIES)
        assert not any(sql.lstrip().upper().startswith(('UPDATE ', 'DELETE ', 'INSERT ')) for sql in statements)
        assert {row[0]: row[1] for row in db.execute(
            "SELECT name,sql FROM sqlite_master WHERE name LIKE 'transcript_revision_v1_%'")} == old_triggers
        frozen = schema(db)
        auto.init_schema(db); auto.init_bounded_schema(db, enable=False); revision.init_bounded_schema(db)
        assert schema(db) == frozen
        with pytest.raises(ValueError, match='Incompatible automatic MOSS schema'):
            legacy_exact_init_schema(db)
        assert rows(db, auto.TABLE, old_columns) == before[0]


def test_failed_additive_migration_rolls_back_every_column_index_and_trigger(legacy_store):
    with legacy_store.db() as db:
        before = schema(db)
        db.execute('BEGIN IMMEDIATE')
        def deny_late_index(action, name, *_):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_INDEX and name == 'auto_v2_target' else sqlite3.SQLITE_OK
        db.set_authorizer(deny_late_index)
        try:
            with pytest.raises(sqlite3.DatabaseError):auto.init_bounded_schema(db, enable=True)
        finally:
            db.set_authorizer(None); db.rollback()
        assert schema(db) == before and not auto.has_bounded_schema(db)
        db.execute('BEGIN IMMEDIATE'); migrate(db)
        assert auto.has_bounded_schema(db)


@pytest.mark.parametrize('damage', ['one_column', 'missing_index', 'changed_index', 'missing_revision_trigger'])
def test_partial_or_redefined_migration_is_never_repaired_implicitly(legacy_store, damage):
    with legacy_store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if damage == 'one_column':
            name, definition = next(iter(auto.V2_COLUMNS.items()))
            db.execute(f'ALTER TABLE {auto.TABLE} ADD COLUMN {name} {definition}')
        else:
            migrate(db)
            if damage == 'missing_revision_trigger':
                db.execute('DROP TRIGGER transcript_revision_v2_native_core_commits_update')
            else:
                db.execute('DROP INDEX auto_v2_dependency_slot')
                if damage == 'changed_index':
                    db.execute(f'CREATE INDEX auto_v2_dependency_slot ON {auto.DEPENDENCIES}(slot)')
        before = schema(db)
        with pytest.raises(ValueError, match='(Incomplete|Incompatible) bounded'):
            auto.init_bounded_schema(db, enable=False)
            revision.init_bounded_schema(db)
        assert schema(db) == before


@pytest.mark.parametrize('column,value', [
    ('target_key', None), ('plan_phase', 'waiting'), ('plan_phase', 'closed'),
    ('plan_scope_key', '0' * 64), ('plan_batch_id', 'wrong-batch'),
    ('wait_start_ordinal', 1.5), ('wait_last_ordinal', 5),
    ('wait_deadline', 'not-a-finite-deadline'), ('plan_revision', .5),
    ('planning_bytes', 1), ('proof_sha256', '0' * 64), ('cell_id', '0' * 64),
    ('candidate_key', '0' * 64), ('cid1', 'source-3'), ('kind', 'core_seam'),
])
def test_v2_metadata_corruption_fails_strict_dispatch(bound_ledger, column, value):
    f = bound_ledger
    with f.store.db() as db:
        db.execute(f'UPDATE {auto.TABLE} SET {column}=? WHERE seq=?', (value, f.row['seq']))
        row = dict(db.execute(f'SELECT * FROM {auto.TABLE} WHERE seq=?', (f.row['seq'],)).fetchone())
        with pytest.raises((cells.Unavailable, ReadError)):
            auto.validate_v2_row(db, row, row['proof'])


@pytest.mark.parametrize('damage', ['missing', 'role', 'ordinal', 'cid', 'slot', 'binding_store', 'binding_roots'])
def test_dependency_certificate_and_index_must_match_exactly(bound_ledger, damage):
    f = bound_ledger
    with f.store.db() as db:
        seq = f.row['seq']
        if damage.startswith('binding_'):
            value = json.loads(f.row['binding'])
            if damage == 'binding_store':value['retention_store_key'] = 'z' * 64
            else:value['dependency_roots'].pop()
            db.execute(f'UPDATE {auto.TABLE} SET binding=? WHERE seq=?', (cells.canonical(value), seq))
        elif damage == 'missing':
            db.execute(f'DELETE FROM {auto.DEPENDENCIES} WHERE intent_seq=? AND ordinal=0', (seq,))
        else:
            column, value = {'role': ('dependency_kind', 15), 'ordinal': ('ordinal', 131),
                'cid': ('cid', 'wrong-source'), 'slot': ('slot', '0' * 64)}[damage]
            db.execute(f'UPDATE {auto.DEPENDENCIES} SET {column}=? WHERE intent_seq=? AND ordinal=0', (value, seq))
        with pytest.raises((cells.Unavailable, ReadError)):
            auto._read_v2(db, key=f.key)


@pytest.mark.parametrize('terminal', ['explicit', 'cancel_session', 'cancel_trigger'])
def test_mixed_v1_v2_terminal_cancel_and_erase_account_exact_retained_bytes(bound_ledger, terminal):
    f = bound_ledger
    with f.store.db() as db:
        legacy = insert_legacy_row(db)
        other = insert_legacy_row(db, index=2, sid=OTHER)
        assert_accounting(db)
        retained = tuple(f.row[name] for name in ('binding', 'proof', 'target_body', 'charge_bytes'))
        if terminal == 'explicit':
            for key in (f.key, legacy):auto._terminal(db, read_intent(db, key), 'no_result', 'synthetic_terminal')
        elif terminal == 'cancel_session':auto.cancel_session(db, SID)
        else:db.execute("UPDATE chunks SET status='cancelled' WHERE id='source-3'")
        after = read_intent(db, f.key)
        assert after['plan_phase'] == 'closed' and after['state'] not in auto.ACTIVE
        assert tuple(after[name] for name in ('binding', 'proof', 'target_body', 'charge_bytes')) == retained
        assert read_intent(db, legacy)['proof'] is None
        assert auto.retention_root(db, cid='source-3') and auto.retention_root(db, slot=core.slot(f.case['commits'][3]))
        assert_accounting(db)
        stable = rows(db, auto.TABLE), rows(db, auto.USAGE)
        auto.cancel_session(db, SID)
        assert (rows(db, auto.TABLE), rows(db, auto.USAGE)) == stable
        other_before = read_intent(db, other)
        auto.erase_session(db, SID)
        assert not db.execute(f'SELECT 1 FROM {auto.DEPENDENCIES} WHERE intent_seq=?', (f.row['seq'],)).fetchone()
        assert not auto.retention_root(db, cid='source-3')
        assert read_intent(db, other) == other_before
        assert_accounting(db)
        assert tuple(auto.usage(db, SID)[1]) == (0, 0, 0, 0)


def test_explicit_retention_entry_points_preserve_nonlegacy_root_fences(bound_ledger):
    f = bound_ledger; cid = 'source-3'; slot = core.slot(f.case['commits'][3])
    assert cid not in (f.row['cid1'], f.row['cid2'])
    owner = SimpleNamespace(bounded_core_enabled=True, retention_store_key=STORE_KEY)
    with f.store.db() as db:
        # Legacy API has only its original two scalar anchors. Every production
        # retention entry point must instead protect this fourth dependency.
        assert not auto.root(db, cid=cid) and not auto.root(db, slot=slot)
        assert auto.retention_root(db, cid=cid) and auto.retention_root(db, slot=slot)
        with Budget().phase() as budget:
            assert core._gc_outbox_root(db, cid, budget)
            assert not core._gc_store_eligible(owner, db, slot, budget)
        assert not auto.retention_eligible(db, STORE_KEY, cid=cid, slot=slot)
        # A damaged relation cannot turn the later store fence into clearance.
        db.execute(f'DELETE FROM {auto.DEPENDENCIES} WHERE intent_seq=? AND cid=?',
                   (f.row['seq'], cid))
        with Budget().phase() as budget:
            assert not core._gc_store_eligible(owner, db, slot, budget)
        assert not auto.retention_eligible(db, STORE_KEY, cid=cid, slot=slot)


def test_retention_root_rechecks_changed_schema_without_connection_cache(bound_ledger):
    f = bound_ledger; cid = 'source-3'
    with f.store.db() as db:
        assert not db.in_transaction
        with pytest.raises(ValueError, match='Bounded automatic migration requires transaction'):
            auto.init_bounded_schema(db, enable=False)
        db.execute('BEGIN IMMEDIATE')
        assert auto.retention_root(db, cid=cid)
        db.execute('DROP INDEX auto_v2_dependency_cid')
        # The same connection must not reuse a prior root or schema decision.
        with pytest.raises(sqlite3.OperationalError, match='no such index: auto_v2_dependency_cid'):
            auto.retention_root(db, cid=cid)
        with Budget().phase() as budget:
            with pytest.raises(sqlite3.OperationalError, match='no such index: auto_v2_dependency_cid'):
                core._gc_outbox_root(db, cid, budget)
        assert not auto.retention_eligible(db, STORE_KEY, cid=cid)
        with pytest.raises(ValueError, match='Incomplete bounded automatic schema'):
            auto.init_bounded_schema(db, enable=False)


@pytest.mark.parametrize('index', [2, 3], ids=['third-root', 'fourth-root'])
def test_transcript_revision_follows_nonlegacy_roots_even_after_source_moves(bound_ledger, index):
    f = bound_ledger; cid = f'source-{index}'; slot = core.slot(f.case['commits'][index])
    assert cid not in (f.row['cid1'], f.row['cid2'])
    from audio_backend import change_feed
    change_feed.initialize(f.store)
    reader = transcripts.TranscriptStore(f.store)
    with f.store.db() as db:
        db.execute('UPDATE chunks SET session_id=? WHERE id=?', (OTHER, cid))
        db.execute('''INSERT INTO segments(chunk_id,start,end,text,speaker_status)
            VALUES(?,0.,.001,'root mutation','unknown')''', (cid,))
    mutations = [
        ('UPDATE chunks SET digest=? WHERE id=?', ('f' * 64, cid)),
        ('UPDATE recording_work SET receipt_id=? WHERE chunk_id=?', ('f' * 64, cid)),
        ("UPDATE native_core_commits SET state='gap' WHERE slot=?", (slot,)),
        ('UPDATE segments SET text=? WHERE chunk_id=?', ('changed root text', cid)),
    ]
    for statement, args in mutations:
        with reader.snapshot(SID) as (db, session, check):
            before = session['transcript_revision']
            token = contract.cursor(SID, 0, reader._revision(db, SID, session))
            check()
        with f.store.db() as db:
            db.execute(statement, args)
            assert db.execute('SELECT transcript_revision FROM sessions WHERE id=?', (SID,)).fetchone()[0] > before
        with pytest.raises(ReadError, match='transcript_revision_changed') as error:
            reader.page(SID, cursor=token)
        assert error.value.status_code == 409


@pytest.mark.parametrize('operation', ['insert', 'update', 'delete'])
def test_dependency_mutation_invalidates_transcript_revision(bound_ledger, operation):
    f = bound_ledger
    with f.store.db() as db:
        before = db.execute('SELECT transcript_revision FROM sessions WHERE id=?', (SID,)).fetchone()[0]
        if operation == 'insert':
            db.execute(f'INSERT INTO {auto.DEPENDENCIES} VALUES(?,4,131,?,NULL)', (f.row['seq'], 'additional-inventory-source'))
        elif operation == 'update':
            db.execute(f'UPDATE {auto.DEPENDENCIES} SET dependency_kind=15 WHERE intent_seq=? AND ordinal=0', (f.row['seq'],))
        else:db.execute(f'DELETE FROM {auto.DEPENDENCIES} WHERE intent_seq=? AND ordinal=0', (f.row['seq'],))
        assert db.execute('SELECT transcript_revision FROM sessions WHERE id=?', (SID,)).fetchone()[0] > before


def test_migrated_legacy_owner_and_other_store_keys_remain_conservatively_fenced(bound_ledger):
    f = bound_ledger
    with f.store.db() as db:
        auto.erase_session(db, SID)  # Remove target roots so the store fence is tested independently.
        slot = core.slot(f.case['commits'][2])
        assert auto.retention_eligible(db, STORE_KEY, slot=slot)
        for key in (None, '6' * 64):
            assert not auto.retention_eligible(db, key, slot=slot)
            owner = SimpleNamespace(bounded_core_enabled=False, retention_store_key=key)
            with Budget().phase() as budget:assert not core._gc_store_eligible(owner, db, slot, budget)
        db.execute('UPDATE native_core_commits SET retention_store_key=NULL WHERE slot=?', (slot,))
        assert not auto.retention_eligible(db, STORE_KEY, slot=slot)
        assert not auto.retention_eligible(db, STORE_KEY, cid='source-2')
        db.execute('UPDATE native_core_commits SET retention_store_key=? WHERE slot=?', (STORE_KEY, slot))
        db.execute('UPDATE recording_batches SET retention_store_key=NULL WHERE id=?', (BATCH,))
        assert not auto.retention_eligible(db, STORE_KEY, slot=slot)
    assert core.store_policy(bounded=True) != core.store_policy()


def test_complete_band_detects_new_primary_parent_not_named_in_saved_inventory(bound_ledger):
    f = bound_ledger
    with f.store.db() as db:
        before = transcripts.TranscriptStore._binding_v2(db, SID, dict(f.row))
        assert before[1:3] == (0, 64)
        inserted = primary_row(2, 33, 47, cid='source-2', text='newly inserted whole parent')
        names = list(inserted)
        db.execute('INSERT INTO segments(' + ','.join(names) + ',speaker_status) VALUES(' +
            ','.join('?' for _ in names) + ",'unknown')", tuple(inserted[name] for name in names))
        assert all(parent['row']['id'] != 2 for parent in f.doc['inventories']['parents'])
        with pytest.raises(ReadError, match='bounded_inventory_changed'):
            transcripts.TranscriptStore._binding_v2(db, SID, dict(f.row))


def test_legacy_transcript_page_and_audit_preserve_binding_hook_shape(tmp_path, monkeypatch):
    from test_transcript_http import Fixture
    fixture = Fixture(tmp_path / 'legacy-binding-hook.sqlite3')
    job = fixture.job(1, 0, 5)
    binding = fixture.reader._binding; calls = []
    def legacy_binding(db, sid, row, source_cache=None):
        calls.append((row['candidate_key'], source_cache is not None))
        return binding(db, sid, row, source_cache)
    monkeypatch.setattr(fixture.reader, '_binding', legacy_binding)
    page = fixture.page()
    assert page['selected_runs'][0]['candidate_keys'] == [job['key']]
    audit = fixture.audit()
    assert audit['examined'] == 1 and audit['unplaced_bindings'] == []
    assert calls == [(job['key'], True), (job['key'], False)]


@pytest.mark.parametrize('mode', ['page', 'audit'])
def test_bounded_transcript_callers_share_budget_and_refuse_unqualified_descriptors(bound_ledger, monkeypatch, mode):
    from audio_backend import change_feed
    f = bound_ledger
    change_feed.initialize(f.store)
    with f.store.db() as db:saved_intent = read_intent(db, f.key)
    reader = transcripts.TranscriptStore(f.store)
    invoke = (lambda: json.loads(reader.page(SID))) if mode == 'page' else (
        lambda: json.loads(reader.binding_audit(SID)))
    binding, qualified = reader._binding, transcripts.TranscriptStore._binding_v2
    passed, validated = [], []
    def measured_binding(db, sid, row, source_cache=None, read_budget=None):
        assert row['target_key'] == f.row['target_key']
        assert isinstance(read_budget, transcripts._CertificateReadBudget)
        passed.append(read_budget)
        return binding(db, sid, row, source_cache, read_budget)
    def measured_v2(db, sid, row, source_cache=None, read_budget=None):
        assert read_budget is passed[-1]
        before = read_budget.sql_bytes
        result = qualified(db, sid, row, source_cache, read_budget)
        assert read_budget.sql_bytes > before
        validated.append(read_budget)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(reader, '_binding', measured_binding)
        patch.setattr(transcripts.TranscriptStore, '_binding_v2', staticmethod(measured_v2))
        invoke()
    assert len(passed) == len(validated) == 1 and passed[0] is validated[0]
    exhausted = []
    def near_limit(db, sid, row, source_cache=None, read_budget=None):
        assert isinstance(read_budget, transcripts._CertificateReadBudget)
        exhausted.append(read_budget)
        read_budget.sql_bytes = contract.MAX_BINDING_BYTES - 1
        return binding(db, sid, row, source_cache, read_budget)
    with monkeypatch.context() as patch:
        patch.setattr(reader, '_binding', near_limit)
        with pytest.raises(ReadError, match='^unresolved_capacity$'):
            invoke()
    assert len(exhausted) == 1 and exhausted[0].sql_bytes > contract.MAX_BINDING_BYTES
    descriptor_lengths = reader._check_descriptor_lengths
    for corruption in ('missing', 'null', 'empty', 'malformed'):
        entered_v2 = []
        def corrupt_descriptors(rows, **kwargs):
            result = descriptor_lengths(rows, **kwargs)
            for row in result:
                assert row['target_key'] == f.row['target_key']
                if corruption == 'missing':row.pop('target_key')
                else:row['target_key'] = {'null': None, 'empty': '', 'malformed': 'bad'}[corruption]
            return result
        def forbidden_v2(*args, **kwargs):
            entered_v2.append(True)
            raise AssertionError('unqualified descriptor reached bounded binding')
        with monkeypatch.context() as patch:
            patch.setattr(transcripts.TranscriptStore, '_check_descriptor_lengths', staticmethod(corrupt_descriptors))
            patch.setattr(transcripts.TranscriptStore, '_binding_v2', staticmethod(forbidden_v2))
            response = invoke()
        rejected = response['notices'] if mode == 'page' else response['unplaced_bindings']
        assert len(rejected) == 1 and rejected[0]['candidate_key'] == f.key
        assert rejected[0]['reason'] == 'automatic_binding_invalid'
        assert entered_v2 == []
    with f.store.db() as db:
        assert read_intent(db, f.key) == saved_intent


@pytest.mark.parametrize('history_kind', ['processed_chunk', 'primary_segment'])
def test_fresh_bounded_policy_refuses_existing_primary_only_session(legacy_store, history_kind):
    with legacy_store.db() as db:
        migrate(db)
        db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,created)
            VALUES(?,?,?,1.)''', (BATCH, SID, 'primary-only-capture'))
        db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
            timeline_start,status,next_offset,created) VALUES('primary-only',?,'primary-only',?,
            '/synthetic-not-opened',1.,0.,'queued',?,1.)''',
            (SID, 'a' * 64, .5 if history_kind == 'processed_chunk' else 0.))
        db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,
            capture_start_frame,native_receipt_status) VALUES('primary-only',?,0,16000,32000,0,'synthetic')''', (BATCH,))
        if history_kind == 'primary_segment':
            db.execute('''INSERT INTO segments(chunk_id,start,end,text,speaker_status)
                VALUES('primary-only',0.,.5,'existing primary','unknown')''')
        assert not rows(db, auto.TABLE) and not rows(db, 'native_core_commits')
        before = rows(db, 'chunks'), rows(db, 'segments'), rows(db, 'recording_batches')
        with Budget().phase() as budget:
            with pytest.raises(cells.Unavailable, match='bounded_fresh_session_required'):
                auto.batch_context(db, {'id': 'primary-only', 'session_id': SID}, STORE_KEY, budget)
        assert (rows(db, 'chunks'), rows(db, 'segments'), rows(db, 'recording_batches')) == before


@pytest.mark.parametrize('qualification', ['legacy_control', 'null_tags', 'other_store'])
def test_otherwise_eligible_legacy_receipts_are_fenced_after_migration(fresh_receipts, qualification):
    # Existing fresh fixture: 31 generated 10.5s sources put 26 receipts behind
    # the unchanged epoch replay frontier. No compact/cell roots hide the fence.
    f = fresh_receipts; ids = f.recording()
    assert not f.owner.bounded_core_enabled
    assert f.settings.native_owned_compact_gc_enabled and f.settings.native_owned_receipt_gc_enabled
    assert f.owner.compact_gc_enabled and f.owner.receipt_gc_enabled
    with f.store.db() as db:
        assert not rows(db, 'native_core_commits') and not rows(db, auto.TABLE)
        if qualification != 'legacy_control':
            migrate(db)
            if qualification == 'other_store':
                assert f.owner.retention_store_key != '6' * 64
                db.execute('UPDATE recording_batches SET native_core_ordinal=31,retention_store_key=?', ('6' * 64,))
    before, preserved = f.source_rows(), f.preservation()
    results = f.cycle()
    if qualification == 'legacy_control':
        assert set(before) - set(f.source_rows()) == set(ids[:26])
        assert sum(item['retired'] for item in results) == 26
    else:
        assert f.source_rows() == before
        assert all(item['status'] == 'no_eligible_receipts' and item['retired'] == 0 for item in results)
        assert any(item['protected'] > 0 for item in results)
    assert f.preservation() == preserved


def test_missing_index_before_publication_protects_all_real_receipts_and_compacts(bounded_app_factory, monkeypatch):
    configure = Settings.__post_init__
    def gc_enabled(settings):
        if settings.moss_automatic_enabled:
            settings.native_owned_compact_gc_enabled = True
            settings.native_owned_receipt_gc_enabled = True
        configure(settings)
    monkeypatch.setattr(Settings, '__post_init__', gc_enabled)
    monkeypatch.setattr(CellStore, '_schedule_compact_gc', lambda self: False)
    monkeypatch.setattr(CellStore, '_schedule_receipt_gc', lambda self: False)
    with bounded_app_factory() as h:
        chunks, _ = advance_to_waiter(h)
        assert process_primary(h) == chunks[-1]['id']
        bound = c_target(h); doc = json.loads(bound['proof'])
        assert bound['plan_phase'] == 'bound' and bound['state'] == 'queued' and bound['job_id'] is None
        # No candidate has entered an owner publication or optional native job.
        with sqlite3.connect(h.owner.path) as sidecar:
            assert sidecar.execute('SELECT 1 FROM cells WHERE cell_id=?', (bound['cell_id'],)).fetchone() is None
            receipt_before = rows(sidecar, 'sources')
        with h.store.db() as db:
            db.execute(f'DELETE FROM {auto.DEPENDENCIES} WHERE intent_seq=?', (bound['seq'],))
            with pytest.raises(cells.Unavailable, match='bounded_dependency_index_mismatch'):
                auto._read_v2(db, key=bound['candidate_key'])
            for root in doc['dependency_roots']:
                assert not auto.retention_eligible(db, h.owner.retention_store_key, cid=root['cid'])
                if root['slot'] is not None:
                    assert not auto.retention_eligible(db, h.owner.retention_store_key, slot=root['slot'])
            compact_before = rows(db, 'native_core_commits')
            ledger_before = rows(db, auto.TABLE), rows(db, auto.USAGE), rows(db, history.TABLE)
        primary = primary_output(h)
        assert h.owner.compact_gc_enabled and h.owner.receipt_gc_enabled
        assert h.worker.settings.native_owned_compact_gc_enabled and h.worker.settings.native_owned_receipt_gc_enabled
        assert h.owner._retention_capture is h.capture
        # Existing drain checks the empty manual outbox, without publishing any
        # automatic target. Do not manufacture an idle producer state.
        h.capture.core_seams.next_attempt = 0.
        h.capture.core_seams.drain()
        assert not h.capture.core_seams.work_pending
        for kind in ('compact_gc', 'receipt_gc'):
            with h.owner._lock:
                assert h.owner._slot is None
                operation = Operation(kind, (), h.owner._epoch, h.owner._wake)
                h.owner._slot = operation; h.owner._wake.set()
            assert operation.slot_released.wait(3.), operation.result
            assert operation.result['retired'] == 0
            assert operation.result['status'] == ('no_eligible_compacts' if kind == 'compact_gc' else 'no_eligible_receipts')
        with sqlite3.connect(h.owner.path) as sidecar:assert rows(sidecar, 'sources') == receipt_before
        with h.store.db() as db:
            assert rows(db, 'native_core_commits') == compact_before
            assert (rows(db, auto.TABLE), rows(db, auto.USAGE), rows(db, history.TABLE)) == ledger_before
        assert primary_output(h) == primary
        assert not h.drivers and not h.owner._pins and h.jobs._live is None


def add_waiter(db, index, *, run='1' * 32, created=1000.):
    """One independent, tiny original certificate in the same fresh batch."""
    a = 64 + index * 16; cid = f'waiting-source-{index}'; ordinal = 5 + index
    case = synthetic_case([a, a + 16], [[a + 2, a + 8]], run=run, separate_sources=True)
    commit, evidence = case['commits'][0], case['evidences'][0]
    commit['part']['chunk_id'] = cid; evidence['chunk_id'] = cid
    commit['evidence_sha256'] = cells.sha(evidence)
    target = core.original_target(ordinary_origin(case))
    origin = target['origins'][0]
    proof, binding = origin['proof'].encode('ascii'), origin['binding'].encode('ascii')
    doc, support, _ = cells.validate_document(proof)
    p = commit['part']
    db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
        timeline_start,status,next_offset,created) VALUES(?,?,?,?,?,.001,?,'completed',.001,?)''',
        (cid, SID, cid, p['source_sha256'], '/synthetic-not-opened/' + cid, a / cells.RATE, created))
    db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,
        capture_start_frame,native_receipt_status,receipt_id,receipt_state)
        VALUES(?,?,?,16,32,?,'synthetic',?,'durable')''',
        (cid, BATCH, ordinal - 1, a, p['receipt_sha256']))
    db.execute('''INSERT INTO native_core_commits(slot,sid,cid,scope_key,logical_end,body,
        pending,state,reason,recording_batch_id,commit_ordinal,retention_store_key)
        VALUES(?,?,?,?,?,?,NULL,'none','synthetic',?,?,?)''',
        (core.slot(commit), SID, cid, core.scope_key(commit['scope']), a + 16,
         cells.canonical(commit, core.COMPACT_BYTES), BATCH, ordinal, STORE_KEY))
    db.execute('UPDATE recording_batches SET native_core_ordinal=? WHERE id=?', (ordinal, BATCH))
    proposal = {'key': hashlib.sha256(binding).hexdigest(), 'sid': SID, 'kind': 'ordinary',
        'binding': binding, 'proof': proof, 'proof_sha256': hashlib.sha256(proof).hexdigest(),
        'cell_id': cells.sha({'policy': doc['policy'], 'support': support.to_dict()}),
        'slot1': core.slot(commit), 'slot2': None, 'cid1': cid, 'cid2': None, 'target': target}
    with Budget().phase() as budget:
        row, reason = auto._register_target(db, proposal,
            {'batch_id': BATCH, 'ordinal': ordinal, 'retention_store_key': STORE_KEY},
            cells.sha(commit['scope']), created, budget)
    assert reason == 'registered'
    return row


def metadata_manager(f, monkeypatch, *, run='1' * 32):
    """Real metadata algorithms/Store transactions, no owner or optional lease."""
    f.store.settings.moss_automatic_enabled = True
    f.store.settings.native_owned_bounded_core_support_enabled = True
    manager = auto.Automatic(f.store, None,
        capture=SimpleNamespace(core_seams=SimpleNamespace(run=run)))
    @contextmanager
    def database(budget, *, write=False):
        with f.store.db() as db:
            if write:db.execute('BEGIN IMMEDIATE')
            yield db
    monkeypatch.setattr(manager, '_bounded_database', database)
    return manager


def test_wall_clock_jumps_never_extend_frozen_monotonic_wait(monkeypatch):
    clock = {'wall': 1000., 'mono': 10.}
    monkeypatch.setattr(auto, 'time', SimpleNamespace(
        time=lambda: clock['wall'], monotonic=lambda: clock['mono']))
    manager = auto.Automatic(SimpleNamespace(), None)
    key = '7' * 64
    assert not manager.bounded_expired(key, 1300.)
    assert manager._bounded_deadlines[key] == 310.
    for wall, mono in ((1200., 20.), (800., 30.), (1200., 40.), (700., 309.)):
        clock.update(wall=wall, mono=mono)
        assert not manager.bounded_expired(key, 1300.)
        assert manager._bounded_deadlines[key] <= 310.
    clock.update(wall=700., mono=310.)
    assert manager.bounded_expired(key, 1300.)
    # A wall-clock expiry observation is sticky before any durable close too.
    second = '8' * 64
    clock.update(wall=1000., mono=400.)
    assert not manager.bounded_expired(second, 1300.)
    clock.update(wall=1301., mono=410.)
    assert manager.bounded_expired(second, 1300.)
    assert manager._bounded_deadlines[second] <= 410.
    clock.update(wall=800., mono=411.)
    assert manager.bounded_expired(second, 1300.)


def test_forward_wall_expiry_closes_once_and_backward_jump_cannot_reopen(bound_ledger, monkeypatch):
    f = bound_ledger
    with f.store.db() as db:waiting = add_waiter(db, 0)
    clock = {'wall': 1001., 'mono': 100.}
    monkeypatch.setattr(auto, 'time', SimpleNamespace(
        time=lambda: clock['wall'], monotonic=lambda: clock['mono']))
    manager = metadata_manager(f, monkeypatch); manager._bounded_startup = False
    assert not manager.bounded_expired(waiting['target_key'], waiting['wait_deadline'])
    clock.update(wall=1301., mono=110.)
    assert manager.maintain_bounded_waiters()
    with f.store.db() as db:
        closed = read_intent(db, waiting['candidate_key'])
        assert closed['plan_phase'] == 'closed' and closed['reason'] == 'unresolved_wait_deadline'
        assert_accounting(db)
    clock.update(wall=800., mono=111.)
    assert not manager.maintain_bounded_waiters()
    with f.store.db() as db:assert read_intent(db, waiting['candidate_key']) == closed


def test_startup_cursor_passes_current_run_waiter_and_closes_later_stale_run(bound_ledger, monkeypatch):
    f = bound_ledger
    with f.store.db() as db:
        current = add_waiter(db, 0, created=1000.)
        stale = add_waiter(db, 1, run='9' * 32, created=1001.)
    monkeypatch.setattr(auto, 'time', SimpleNamespace(time=lambda: 1050., monotonic=lambda: 50.))
    manager = metadata_manager(f, monkeypatch)
    assert not manager.maintain_bounded_waiters()  # Current-run row is preserved, cursor advances.
    assert manager._bounded_startup and manager._bounded_startup_after == (current['wait_deadline'], current['seq'])
    assert manager.maintain_bounded_waiters()
    with f.store.db() as db:
        assert read_intent(db, current['candidate_key']) == current
        closed = read_intent(db, stale['candidate_key'])
        assert closed['reason'] == 'unresolved_scope_transition' and closed['plan_phase'] == 'closed'
        assert_accounting(db)
    assert not manager.maintain_bounded_waiters()  # One additional empty indexed turn completes startup.
    assert not manager._bounded_startup


def test_wait_pressure_closes_one_certificate_per_turn_without_freeing_evidence(bound_ledger):
    f = bound_ledger
    f.store.settings.moss_automatic_enabled = True
    f.store.settings.native_owned_bounded_core_support_enabled = True
    with f.store.db() as db:
        waiting = [add_waiter(db, index, created=1000. + index) for index in range(2)]
        for index in range(1, 62):insert_legacy_row(db, index=index)
        assert auto.usage(db, SID)[1][2] == auto.SESSION_PENDING == 64
        preserved = {row['candidate_key']: tuple(row[name] for name in ('proof', 'binding', 'target_body', 'charge_bytes'))
                     for row in waiting}
        db.execute("UPDATE recording_batches SET state='queued' WHERE id=?", (BATCH,))
        db.execute("UPDATE chunks SET status='queued',next_offset=0. WHERE id='source-3'")
        primary_before = rows(db, 'chunks'), rows(db, 'segments')
    for index in range(2):
        # Capacity is still full after one closure. Store.claim must commit that
        # useful closure rather than roll it back or mark the primary running.
        if index == 0:assert f.store.claim() is None
        with f.store.db() as db:
            if index:
                with Budget().phase() as budget:assert auto.release_wait_pressure(db, SID, budget) == 1
            assert auto.usage(db, SID)[1][2] == 63 - index
            with pytest.raises(ReadError, match='automatic_pending_capacity'):auto.capacity(db, SID)
            assert_accounting(db)
            assert (rows(db, 'chunks'), rows(db, 'segments')) == primary_before
        with f.store.db() as db:
            closed = read_intent(db, waiting[index]['candidate_key'])
            assert closed['reason'] == 'unresolved_wait_capacity' and closed['plan_phase'] == 'closed'
            assert tuple(closed[name] for name in ('proof', 'binding', 'target_body', 'charge_bytes')) == preserved[closed['candidate_key']]
    with f.store.db() as db:
        with Budget().phase() as budget:assert auto.release_wait_pressure(db, SID, budget) == 0


@pytest.mark.parametrize('error', [ReadError(503, 'result_storage_unavailable'),
    cells.Unavailable('bounded_planning_capacity'), sqlite3.OperationalError('synthetic write refusal')])
def test_bounded_before_window_write_refusal_keeps_queued_primary_exact(bound_ledger, monkeypatch, error):
    f = bound_ledger; manager = metadata_manager(f, monkeypatch)
    with f.store.db() as db:
        db.execute("UPDATE chunks SET status='queued',next_offset=0. WHERE id='source-3'")
        chunk = dict(db.execute("SELECT * FROM chunks WHERE id='source-3'").fetchone())
        before = rows(db, 'chunks'), rows(db, 'segments'), rows(db, auto.TABLE), rows(db, auto.USAGE)
    @contextmanager
    def refused(budget, *, write=False):
        assert write
        raise error
        yield  # The failure occurs before the context can expose a connection.
    monkeypatch.setattr(manager, '_bounded_database', refused)
    assert manager.before_window(chunk) is False
    assert manager.last_reason == 'bounded_preflight_deferred'
    with f.store.db() as db:
        assert (rows(db, 'chunks'), rows(db, 'segments'), rows(db, auto.TABLE), rows(db, auto.USAGE)) == before


def queue_bounded_claim(f):
    f.store.settings.moss_automatic_enabled=True
    f.store.settings.native_owned_bounded_core_support_enabled=True
    with f.store.db() as db:
        db.execute("UPDATE recording_batches SET state='queued' WHERE id=?",(BATCH,))
        db.execute("UPDATE chunks SET status='queued',next_offset=0. WHERE id='source-3'")
        return rows(db,'chunks'),rows(db,'segments'),rows(db,auto.TABLE),rows(db,auto.USAGE)


@pytest.mark.parametrize('kind',['cooperative','vm_interrupt'])
def test_proven_preclaim_budget_rollback_defers_then_claims_exactly_once(bound_ledger,monkeypatch,kind):
    from audio_backend.storage import _ClaimDeferred
    f=bound_ledger;before=queue_bounded_claim(f);release=auto.release_wait_pressure
    def refused(db,sid,budget):
        if kind=='cooperative':raise cells.Unavailable('bounded_planning_capacity')
        db.execute('''WITH RECURSIVE work(n) AS (VALUES(1) UNION ALL
            SELECT n+1 FROM work WHERE n<100000) SELECT sum(n) FROM work''').fetchone()
        pytest.fail('real bounded claim VM limit did not interrupt')
    monkeypatch.setattr(auto,'release_wait_pressure',refused)
    with pytest.raises(_ClaimDeferred):f.store.claim()
    with f.store.db() as db:
        assert (rows(db,'chunks'),rows(db,'segments'),rows(db,auto.TABLE),rows(db,auto.USAGE))==before
    monkeypatch.setattr(auto,'release_wait_pressure',release)
    claimed=f.store.claim()
    assert claimed['id']=='source-3' and claimed['next_offset']==0.
    assert f.store.chunk('source-3')['status']=='running'
    assert f.store.claim() is None
    assert f.store.chunk('source-3')['next_offset']==0.  # No false completion.


def test_arbitrary_preclaim_corruption_is_not_a_cooperative_defer(bound_ledger,monkeypatch):
    f=bound_ledger;before=queue_bounded_claim(f)
    def invalid(*args):raise cells.Unavailable('bounded_dependency_index_mismatch')
    monkeypatch.setattr(auto,'release_wait_pressure',invalid)
    with pytest.raises(cells.Unavailable,match='bounded_dependency_index_mismatch'):f.store.claim()
    with f.store.db() as db:
        assert (rows(db,'chunks'),rows(db,'segments'),rows(db,auto.TABLE),rows(db,auto.USAGE))==before


def test_postcommit_claim_failure_is_not_mislabeled_idle_or_deferred(bound_ledger,monkeypatch):
    f=bound_ledger;queue_bounded_claim(f);database=f.store.db
    @contextmanager
    def late_failure(*,budget=None):
        with database(budget=budget) as db:yield db
        if budget is not None:raise cells.Unavailable('cooperative_deadline')
    monkeypatch.setattr(f.store,'db',late_failure)
    with pytest.raises(cells.Unavailable,match='cooperative_deadline'):f.store.claim()
    with database() as db:
        row=db.execute("SELECT status,next_offset FROM chunks WHERE id='source-3'").fetchone()
        assert tuple(row)==('running',0.)


def observe_authority_connections(monkeypatch, *, failure=None):
    """Real SQLite reads; intercept only their cleanup for explicit negatives."""
    original = sqlite3.connect
    opened = []
    class Tracked(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.events = []; self.closed = False
        def execute(self, sql, *args, **kwargs):
            verb = sql.strip().split()[0].upper()
            self.events.append(verb)
            if verb == 'ROLLBACK' and failure == 'rollback':
                raise sqlite3.OperationalError('synthetic rollback failure')
            return super().execute(sql, *args, **kwargs)
        def close(self):
            self.events.append('CLOSE')
            super().close(); self.closed = True
            if failure == 'close':raise sqlite3.OperationalError('synthetic close acknowledgement failure')
    def connect(path, *args, **kwargs):
        if type(path) is str and '?mode=ro' in path:
            db = original(path, *args, factory=Tracked, **kwargs)
            opened.append(db)
            return db
        return original(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    return opened


def exhaust_planning_read(db):
    # Genuine SQLite VM work under the real per-read Budget callback. No
    # monkeypatched interrupt result or preseeded step charge is authority.
    return db.execute('''WITH RECURSIVE work(n) AS (VALUES(1) UNION ALL
        SELECT n+1 FROM work WHERE n<100000) SELECT sum(n) FROM work''').fetchone()


def test_bounded_connection_reuse_keeps_fresh_source_snapshots(legacy_store, monkeypatch):
    from audio_backend import native_owned_cell_capture as capture
    from audio_backend.native_owned_cell_store import Receipt
    store = legacy_store; path = store.settings.data_dir / 'uploads' / 'fresh-source.audio'
    path.parent.mkdir(exist_ok=True); content = wav_bytes(0, 2); path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest(); duration = 2 / cells.RATE
    with store.db() as db:
        db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
            timeline_start,status,next_offset,created,metadata)
            VALUES('fresh-source',?,'fresh-source',?,?,?,0.,'completed',?,1.,'{}')''',
            (SID, digest, str(path), duration, duration))
    receipt = Receipt(SID, 'fresh-source', digest, 2, 44, len(content),
                      capture.signature(path.stat()), 0., None)
    authority = capture.PrimaryAuthority(store)
    opened = observe_authority_connections(monkeypatch)
    budget = Budget()
    with budget.planning_phase(), authority.list_read_scope(budget, bounded_planning=True):
        assert authority.receipt_valid(receipt, budget)
        with store.db() as db:db.execute("UPDATE chunks SET digest=? WHERE id='fresh-source'", ('2' * 64,))
        assert not authority.receipt_valid(receipt, budget)
    assert len(opened) == 1 and opened[0].closed
    # The successful receipt validates two independent row snapshots; the
    # subsequent changed digest refuses on its first fresh row snapshot.
    assert opened[0].events.count('BEGIN') == opened[0].events.count('ROLLBACK') == 3
    assert authority._list_reads.current is None


@pytest.mark.parametrize('error', [RuntimeError('synthetic read body failure'),
                                   InterruptedError('synthetic read cancellation')])
def test_bounded_connection_scope_closes_on_exception_or_cancellation(legacy_store, monkeypatch, error):
    from audio_backend.native_owned_cell_capture import PrimaryAuthority
    authority = PrimaryAuthority(legacy_store); opened = observe_authority_connections(monkeypatch)
    budget = Budget()
    def refused(db):
        assert db.execute('SELECT 1').fetchone()[0] == 1
        raise error
    with pytest.raises(type(error)) as observed:
        with budget.planning_phase(), authority.list_read_scope(budget, bounded_planning=True):
            authority._read(budget, 'source_row', refused)
    assert observed.value is error
    assert len(opened) == 1 and opened[0].closed and 'CLOSE' in opened[0].events
    assert authority._list_reads.current is None


def test_only_authenticated_closed_planning_read_interrupt_is_optional(legacy_store, monkeypatch):
    from audio_backend.native_owned_cell_capture import PrimaryAuthority
    authority = PrimaryAuthority(legacy_store); opened = observe_authority_connections(monkeypatch)
    budget = Budget()
    with pytest.raises(cells.Unavailable, match='^bounded_planning_capacity$'):
        with budget.planning_phase(), authority.list_read_scope(budget, bounded_planning=True):
            authority._read(budget, 'source_row', exhaust_planning_read)
    assert budget.steps > 120000
    assert len(opened) == 1 and opened[0].closed
    assert opened[0].events[-2:] == ['ROLLBACK', 'CLOSE']
    assert authority._list_reads.current is None


def test_unmarked_read_interrupt_with_stale_budget_marker_stays_hard(legacy_store, monkeypatch):
    from audio_backend.native_owned_cell_capture import PrimaryAuthority
    authority = PrimaryAuthority(legacy_store); opened = observe_authority_connections(monkeypatch)
    budget = Budget(); budget._progress_refusal = 'bounded_planning_capacity'
    def unmarked(db):
        db.set_progress_handler(lambda: 1, 100)
        return exhaust_planning_read(db)
    with pytest.raises(sqlite3.OperationalError) as observed:
        with budget.planning_phase(), authority.list_read_scope(budget, bounded_planning=True):
            authority._read(budget, 'source_row', unmarked)
    assert observed.value.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT
    assert budget.steps < 120000
    assert len(opened) == 1 and opened[0].closed
    assert authority._list_reads.current is None


@pytest.mark.parametrize('failure', ['rollback', 'close'])
def test_planning_read_cleanup_uncertainty_never_becomes_optional(legacy_store, monkeypatch, failure):
    from audio_backend.native_owned_cell_capture import PrimaryAuthority
    authority = PrimaryAuthority(legacy_store)
    opened = observe_authority_connections(monkeypatch, failure=failure); budget = Budget()
    with pytest.raises(sqlite3.OperationalError):
        with budget.planning_phase(), authority.list_read_scope(budget, bounded_planning=True):
            authority._read(budget, 'source_row', exhaust_planning_read)
    assert budget.steps > 120000
    assert len(opened) == 1 and opened[0].closed
    assert 'ROLLBACK' in opened[0].events and 'CLOSE' in opened[0].events
    assert authority._list_reads.current is None


def test_planning_read_refusal_preserves_primary_waiter_backoff_and_unique_owner(bounded_app_factory, monkeypatch):
    with bounded_app_factory() as h:
        chunks, waiting = advance_to_waiter(h)
        before = intent_rows(h); authority = h.capture.authority; original = authority.original_pcm
        injected = []
        def refuse_once(parts, budget):
            scope = getattr(authority._list_reads, 'current', None)
            if (not injected and scope is not None
                    and scope.get('bounded_planning') is True and scope['budget'] is budget):
                assert budget._planning_limits == (.4, 120000, 128 * 1024)
                injected.append(True)
                authority._read(budget, 'source_row', exhaust_planning_read)
                pytest.fail('real planning read did not interrupt')
            return original(parts, budget)
        monkeypatch.setattr(authority, 'original_pcm', refuse_once)
        assert process_primary(h) == chunks[-1]['id']
        assert injected == [True]
        assert h.store.chunk(chunks[-1]['id'])['status'] == 'completed'
        assert h.store.chunk(chunks[-1]['id'])['next_offset'] == 10.5
        notice = h.store.chunk(chunks[-1]['id'])['metadata']['bounded_core_support']
        assert notice['status'] == 'waiting' and notice['owned_targets_preserved'] is True
        assert notice['reason'] == 'bounded_planning_unavailable'
        assert intent_rows(h) == before and c_target(h) == waiting
        h.automatic._snapshot()
        assert waiting['candidate_key'] not in [key for group in h.automatic._round.values() for _, key in group]
        assert h.store.claim() is None  # No replay of the already committed core.
        assert authority._list_reads.current is None
        assert not h.owner._pins and h.jobs.permit._optional is None
        assert not h.drivers and h.jobs._live is None and h.automatic.flight is None
        # Exercise existing stop-aware maintenance backoff without admitting a
        # different ready target. The waiting target remains undispatchable.
        checked = []
        monkeypatch.setattr(h.jobs, 'automatic_idle', lambda: checked.append(True) or False)
        monkeypatch.setattr(auto, 'time', SimpleNamespace(time=time.time, monotonic=lambda: 1000.))
        h.automatic.next_attempt = 0.; h.automatic.step(); h.automatic.step()
        assert checked == [True] and h.automatic.next_attempt == 1001.
        assert intent_rows(h) == before and c_target(h)['job_id'] is None


def test_bind_uses_exact_fresh_cell_validation_once(legacy_store, monkeypatch):
    # Tiny four-root synthetic proof; the real binding SQL and final reader
    # qualification remain in seed_bound, with every existing assertion intact.
    original_bind = auto._bind_target; original_validate = core.validate_bounded_document
    active = []; counts = []
    def validate(*args, **kwargs):
        if active:counts[-1] += 1
        return original_validate(*args, **kwargs)
    def bind(*args, **kwargs):
        active.append(True); counts.append(0)
        try:return original_bind(*args, **kwargs)
        finally:active.pop()
    monkeypatch.setattr(core, 'validate_bounded_document', validate)
    monkeypatch.setattr(auto, '_bind_target', bind)
    with legacy_store.db() as db:
        migrate(db); value = seed_bound(db); assert_accounting(db)
        assert value.row['plan_phase'] == 'bound'
    assert counts == [1]


@pytest.mark.parametrize('seconds', [.5, .75, 1.])
def test_bounded_budget_config_accepts_finite_bounds(tmp_path, seconds):
    settings = Settings(data_dir=tmp_path, native_owned_bounded_core_support_seconds=seconds)
    budget = Budget.for_bounded(settings.native_owned_bounded_core_support_seconds)
    assert not settings.native_owned_bounded_core_support_enabled
    assert budget.seconds == seconds and Budget().seconds == .5


@pytest.mark.parametrize('seconds', [True, False, None, '0.75', .49, 1.01,
                                      float('nan'), float('inf'), -float('inf')])
def test_bounded_budget_config_refuses_invalid_values(tmp_path, seconds):
    with pytest.raises(ValueError, match='native_owned_bounded_core_support_seconds'):
        Settings(data_dir=tmp_path, native_owned_bounded_core_support_seconds=seconds)
    with pytest.raises(cells.Unavailable, match='invalid_bounded_budget_seconds'):
        Budget.for_bounded(seconds)


def test_bounded_budget_keeps_reserve_deadline_and_numeric_caps(monkeypatch):
    from audio_backend import native_owned_cell_store as cellstore
    monkeypatch.setattr(cellstore, 'time', SimpleNamespace(monotonic=lambda: 10.))
    assert Settings().native_owned_bounded_core_support_seconds == .75
    budget = Budget.for_bounded(.75)
    budget.elapsed = .4; assert budget.reserve_planning()
    budget.elapsed = .401; assert not budget.reserve_planning()
    budget.elapsed = .65
    with budget.finalization_phase():assert budget._planning_limits == (.73, 148000, 252*1024)
    budget.elapsed = .731
    with pytest.raises(cells.Unavailable, match='bounded_planning_capacity'):
        with budget.finalization_phase():pass
    budget.elapsed = .751
    with pytest.raises(cells.Unavailable, match='cooperative_deadline'):
        with budget.phase():pass
    budget = Budget.for_bounded(.75); budget.deadline = 10.
    with pytest.raises(cells.Unavailable, match='cooperative_deadline'):budget.check()
    for name, seconds, steps, projected in [('planning_phase', .4, 120000, 128*1024),
            ('finalization_phase', .73, 148000, 252*1024), ('notice_phase', .75, 150000, 256*1024)]:
        budget = Budget.for_bounded(.75)
        with getattr(budget, name)():assert budget._planning_limits == (seconds, steps, projected)
        for field, limit in [('steps', steps), ('sql_bytes', projected)]:
            budget = Budget.for_bounded(.75); setattr(budget, field, limit+1)
            with pytest.raises(cells.Unavailable, match='bounded_planning_capacity'):
                with getattr(budget, name)():pass
    budget = Budget.for_bounded(.75); budget.read_bytes = cellstore.SOURCE_READ_BYTES+1
    with pytest.raises(cells.Unavailable, match='operation_work_capacity'):budget.check()
    legacy = Budget()
    with legacy.finalization_phase():assert legacy._planning_limits == (.48, 148000, 252*1024)
    with legacy.notice_phase():assert legacy._planning_limits == (.5, 150000, 256*1024)


def test_bounded_configured_timeout_rolls_back_primary_and_waiter(bounded_app_factory, monkeypatch):
    with bounded_app_factory() as h:
        chunks, waiting = advance_to_waiter(h); before = intent_rows(h)
        with h.store.db() as db:
            ordinal = db.execute('SELECT native_core_ordinal FROM recording_batches WHERE session_id=?', (h.sid,)).fetchone()[0]
            compacts = db.execute('SELECT COUNT(*) FROM native_core_commits WHERE sid=?', (h.sid,)).fetchone()[0]
        observed = []
        def expired_bind(db, row, proposal, context, snapshot, budget, now):
            assert budget.seconds == .75 and budget.primary_sql and db.in_transaction
            observed.append(budget)
            # Deterministically expire the same cumulative clock inside the
            # real primary transaction, then let its actual progress handler
            # interrupt bounded SQL. No exception result or handler is faked.
            budget.elapsed = budget.seconds + .01
            exhaust_planning_read(db)
            pytest.fail('expired real primary SQL did not interrupt')
        monkeypatch.setattr(auto, '_bind_target', expired_bind)
        with pytest.raises(sqlite3.OperationalError) as caught:process_primary(h)
        assert caught.value.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT
        assert len(observed) == 1 and observed[0].sql_outcome == 'rolled_back'
        assert intent_rows(h) == before and c_target(h) == waiting
        assert h.store.chunk(chunks[-1]['id'])['next_offset'] == 0.
        with h.store.db() as db:
            assert db.execute('SELECT native_core_ordinal FROM recording_batches WHERE session_id=?', (h.sid,)).fetchone()[0] == ordinal
            assert db.execute('SELECT COUNT(*) FROM native_core_commits WHERE sid=?', (h.sid,)).fetchone()[0] == compacts
            assert db.execute('SELECT COUNT(*) FROM segments WHERE chunk_id=?', (chunks[-1]['id'],)).fetchone()[0] == 0
        assert not h.drivers and h.jobs._live is None and not h.owner._pins


@pytest.mark.parametrize('field,damage', [
    ('proof', 'mutated'), ('target_body', 'mutated'),
    ('proof', 'encoding'), ('target_body', 'encoding'),
    ('proof', 'null'), ('target_body', 'null'),
    ('proof', 'truncated'), ('target_body', 'truncated'), ('proof', 'missing')])
def test_exact_publication_record_refuses_changed_bytes(bound_ledger, field, damage):
    value = bound_ledger
    with value.store.db() as db:
        raw = value.row[field]
        if damage == 'missing':db.execute(f'DELETE FROM {auto.TABLE} WHERE candidate_key=?', (value.key,))
        else:
            changed = (raw[:-1] + b' ' if damage == 'mutated' else raw.decode('ascii')
                       if damage == 'encoding' else None if damage == 'null' else raw[:-1])
            # Deliberate corrupt storage. Production's length/type/equality
            # checks must reject even when a writer bypassed CHECK constraints.
            db.execute('PRAGMA ignore_check_constraints=ON')
            db.execute(f'UPDATE {auto.TABLE} SET {field}=? WHERE candidate_key=?', (changed, value.key))
        with Budget().phase() as budget:
            try:accepted = auto.qualify_bounded_document(db, value.doc, budget, STORE_KEY,
                                                        automatic_key=value.key)
            except cells.Unavailable:accepted = False
        assert accepted is False


@pytest.mark.parametrize('expected', [(b'x' * 65536, b'y'), (b'x', 'y'), [b'x', b'y']])
def test_exact_publication_parameters_are_bounded_before_sql(bound_ledger, expected):
    with bound_ledger.store.db() as db:
        statements = []; db.set_trace_callback(lambda _: statements.append(True))
        try:
            with pytest.raises(cells.Unavailable, match='bounded_certificate_capacity'):
                auto._read_v2(db, key=bound_ledger.key, expected=expected)
        finally:db.set_trace_callback(None)
        assert statements == []


def test_exact_publication_projection_counts_match_returned_bytes(bound_ledger, capsys):
    value = bound_ledger; full = Budget(); exact = Budget()
    expected = (value.row['proof'], value.row['target_body'])
    with value.store.db() as db:
        with full.phase():before = auto._read_v2(db, key=value.key, budget=full)
        with exact.phase():after = auto._read_v2(db, key=value.key, budget=exact, expected=expected)
    assert before == after == value.row
    assert full.sql_bytes - exact.sql_bytes == sum(map(len, expected)) - 32
    assert after['proof'] is expected[0] and after['target_body'] is expected[1]
    with capsys.disabled():print('bounded_exact_projection '+json.dumps({
        'full_projected_bytes': full.sql_bytes, 'exact_projected_bytes': exact.sql_bytes,
        'comparison_input_bytes': sum(map(len, expected)), 'comparison_parameter_count': 2}, sort_keys=True), flush=True)


@pytest.mark.parametrize('boundary', [2, 3])
def test_owner_fresh_boundary_mutation_never_acknowledges_publication(bounded_app_factory, monkeypatch, boundary):
    with bounded_app_factory() as h:
        chunks, _ = advance_to_waiter(h)
        assert process_primary(h) == chunks[-1]['id']
        row = c_target(h); doc, support, parts = cells.validate_document(row['proof'])
        anchor = core.anchor(doc['commits'][-1])
        cell = cells.Cell(support, parts, row['proof'], fixture_pcm(*C_SUPPORT), h.sid,
            anchor['chunk_id'], anchor['start'], anchor['end'], anchor['sha256'])
        original = h.owner._cell_current; calls = []
        def mutate(op, proposed, budget):
            calls.append(True)
            if len(calls) == boundary:
                with h.store.db() as db:
                    db.execute(f'UPDATE {auto.TABLE} SET proof=? WHERE candidate_key=?',
                               (row['proof'][:-1] + b' ', row['candidate_key']))
            return original(op, proposed, budget)
        monkeypatch.setattr(h.owner, '_cell_current', mutate)
        operation = h.owner.submit('publish_auto', (row['candidate_key'], cell))
        assert isinstance(operation, Operation) and operation.slot_released.wait(3.)
        assert operation.result['status'] == 'unknown'
        assert operation.result['reason'] == ('source_changed_before_publication_commit' if boundary == 2
                                               else 'source_changed_after_publication_commit')
        assert len(calls) == boundary
        # Read a separate owner connection only after its sole operation ended.
        # Precommit refusal rolls back; postcommit lost acknowledgement retains
        # the exact row without authorizing a job or claiming it rolled back.
        db = sqlite3.connect(h.owner.path.as_uri() + '?mode=ro', uri=True)
        try:retained = db.execute('SELECT proof FROM cells WHERE cell_id=?', (cell.cell_id,)).fetchone()
        finally:db.close()
        assert retained is None if boundary == 2 else retained[0] == row['proof']
        assert c_target(h)['state'] == 'queued' and c_target(h)['job_id'] is None
        assert not h.drivers and h.jobs._live is None and not h.owner._pins
