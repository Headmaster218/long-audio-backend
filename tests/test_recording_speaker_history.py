"""Finite, model-free HistoryStore contracts. Execution is separately gated.

Fixtures own fresh SQLite files and at most two 4-second synthetic PCM WAVs
(128,044 bytes each). No user audio, model, child process, server, benchmark,
maximum-sized proof, or ambient database is an input. Per test: <=2 scopes,
<=2 attempts/certificates/source versions, <=2 primary packets, <=6 dependency
rows, <=2 copied receipts, <=2 small MOSS proof objects, and one decision slot.
Schema backup tests own at most three database files and read the pinned 60,146
byte dev50 link-store source to define only its old init_schema function during
the gated test. SQL snapshots and loops
below are bounded by these fixtures; none changes a production resource limit.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
from types import SimpleNamespace
import wave

import pytest

from audio_backend import moss_result_store as sql_owner
from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_primary_probe as primary
from audio_backend import recording_primary_provenance as provenance
from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_link_store as legacy
from audio_backend import recording_speaker_links as links
from audio_backend.native_owned_cell_store import Receipt
from audio_backend.embedding_space import validate_space
from audio_backend.native_owned_support_cache import ReadError
from test_bounded_core_support import synthetic_case, EPOCH
from test_recording_speaker_links import ordinary, projection, vector

RATE = cells.RATE
FRAMES = 4 * RATE
TABLES = (history.SCOPES, history.ATTEMPTS, history.CERTIFICATES,
          history.OBJECTS, history.REFS, history.DEPENDENCIES,
          history.SOURCES, history.DECISIONS, history.ROWS)


@contextmanager
def connection(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def base_schema(db):
    # All columns used by the production additive mutation triggers are present.
    db.executescript('''
        CREATE TABLE sessions(id TEXT PRIMARY KEY,deleted INTEGER NOT NULL,
                              transcript_revision INTEGER NOT NULL);
        CREATE TABLE recording_batches(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,
                              capture_origin_frame INTEGER,state TEXT NOT NULL);
        CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,status TEXT NOT NULL,
            digest TEXT,path TEXT,duration REAL,timeline_start REAL,next_offset REAL);
        CREATE TABLE stream_chunks(chunk_id TEXT PRIMARY KEY,session_id TEXT,
                                   stream_id TEXT,position INTEGER);
        CREATE TABLE recording_work(chunk_id TEXT PRIMARY KEY,batch_id TEXT,position INTEGER,
            frames INTEGER,capture_start_frame INTEGER,native_receipt BLOB,
            receipt_id TEXT,file_signature BLOB);
        CREATE TABLE segments(id INTEGER PRIMARY KEY,chunk_id TEXT,start REAL,end REAL,
            text TEXT,raw_text TEXT,asr_backend TEXT,timestamp_kind TEXT,
            speaker_local_id TEXT,speaker_window_start REAL);
    ''')


def snapshot(store):
    with connection(store.path) as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY 1,2')]
                for table in TABLES}


def scope_row(store, rid):
    with connection(store.path) as db:
        return dict(db.execute(f'SELECT * FROM {history.SCOPES} WHERE recording_id=?', (rid,)).fetchone())


def attempt_row(store, token):
    with connection(store.path) as db:
        return dict(db.execute(f'SELECT * FROM {history.ATTEMPTS} WHERE token=?', (token,)).fetchone())


@pytest.fixture
def history_env(tmp_path, monkeypatch):
    path = tmp_path / 'history.sqlite3'
    with connection(path) as db:
        base_schema(db)
        db.execute('BEGIN IMMEDIATE')
        legacy.init_schema(db)
        provenance.init_schema(db)
        history.init_schema(db)
    settings = SimpleNamespace(recording_primary_observation_policy=history.POLICY,
        recording_speaker_links_enabled=True, disk_reserve_bytes=0,
        moss_result_max_rows=1024, moss_result_max_rows_per_session=32,
        moss_result_max_bytes=128 * 1024**2, moss_result_max_bytes_per_session=4 * 1024**2,
        recording_speaker_model_path=str(tmp_path / 'ABSENT-MODEL'))
    store = SimpleNamespace(path=path, settings=settings)
    monkeypatch.setattr(sql_owner.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * 1024**3))
    value = history.HistoryStore(store, clock=lambda: 0.)
    owner = SimpleNamespace(allow=True, invocations=[])
    def primary_success(reservation, node, values, invocation):
        owner.invocations.append(invocation)
        return owner.allow and invocation == 'f' * 32
    def moss_success(reservation, batch, invocation):
        owner.invocations.append(invocation)
        return owner.allow and invocation == 'f' * 32
    owner._primary_success_matches = primary_success
    owner._moss_success_matches = moss_success
    capability = value.bind_success_owner(owner)
    return SimpleNamespace(store=store, value=value, owner=owner, capability=capability,
                           directory=tmp_path, made=[])


def source_fixture(env, *, number=1, sid='session', rid=None, pcm=None, cid=None):
    assert len(env.made) < 2
    rid = rid or f'{number:032x}'
    cid = cid or 'source-' + str(number)
    pcm = b'\0\0' * FRAMES if pcm is None else pcm
    assert len(pcm) == 2 * FRAMES
    path = env.directory / (cid + '.wav')
    with wave.open(str(path), 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(RATE)
        target.writeframes(pcm)
    info = path.stat()
    assert info.st_size == 128044
    signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    receipt = Receipt(sid, cid, hashlib.sha256(pcm).hexdigest(), FRAMES, 44,
                      info.st_size, signature, 0., 'stream-' + str(number))
    with connection(env.store.path) as db:
        db.execute('INSERT OR IGNORE INTO sessions VALUES(?,0,0)', (sid,))
        db.execute('INSERT OR IGNORE INTO recording_batches VALUES(?,?,0,\'completed\')', (rid, sid))
        db.execute('INSERT INTO chunks VALUES(?,?,\'completed\',?,?,4.,0.,4.)',
                   (cid, sid, receipt.digest, str(path)))
        db.execute('INSERT INTO stream_chunks VALUES(?,?,?,0)', (cid, sid, receipt.stream_id))
        db.execute('INSERT INTO recording_work VALUES(?,?,0,?,0,?,?,?)',
                   (cid, rid, FRAMES, receipt.encoded, receipt.receipt_id, history.encoded(signature)))
    item = SimpleNamespace(sid=sid, rid=rid, cid=cid, receipt=receipt, path=path, number=number)
    env.made.append(item)
    return item


def primary_fixture(env, *, number=1, sid='session', rid=None):
    item = source_fixture(env, number=number, sid=sid, rid=rid)
    commit = deepcopy(synthetic_case([0, FRAMES], [(3 * RATE, 13 * RATE // 4)])['commits'][0])
    commit['scope'].update(session_id=sid, key=item.receipt.stream_id)
    commit['part'].update(session_id=sid, chunk_id=item.cid, receipt_sha256=item.receipt.receipt_id)
    packet = {'schema': provenance.SCHEMA, 'commit': commit,
        'query': [0, FRAMES, 0, FRAMES, FRAMES, 9, True],
        'owned_pcm16le_sha256': item.receipt.digest,
        'labels': [EPOCH + ':speaker_1', EPOCH + ':speaker_2'],
        'turns': [[2, 0, 0, 52000, 0., 3.25], [6, 0, 2000, 52000, .125, 3.25],
                  [9, 1, 48000, 52000, 3., 3.25]],
        'units': [[5, 7, .5, .625, .5, 0, [2, 6]]], 'rows': [[5, [0]]],
        'refs': [[f'p.{number}.' + 'a' * 64, None]]}
    raw = provenance.bounded(packet)
    digest = hashlib.sha256(raw).hexdigest()
    slot = core.slot(commit)
    header = dict(sequence=number, sid=sid, rid=item.rid, cid=item.cid,
                  core_start=0, core_end=FRAMES, core_slot=slot, digest=digest)
    header['charge'] = provenance._charge(raw, sid, item.rid, item.cid, slot, digest)
    provenance.validate_packet(packet, header)
    with connection(env.store.path) as db:
        db.execute(f'INSERT INTO {provenance.TABLE} VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (number, sid, item.rid, item.cid, 0, FRAMES, slot, digest, raw, header['charge']))
        db.execute('INSERT INTO segments VALUES(?,?,.5,.625,\'synthetic\',\'synthetic\',\'sensevoice\',\'ctc\',?,0.)',
                   (number, item.cid, packet['labels'][0]))
    saved = {'header': header, 'packet': packet}
    node = primary.standalone_primary_metadata(saved, validate_space(links.expected_space()))
    crop = b'\0\0' * (2 * RATE)
    node.update(pcm16le_sha256=hashlib.sha256(crop).hexdigest(),
                float32le_sha256=cells.float32_digest(crop))
    primary.validate_standalone_primary_node(saved, node)
    item.saved, item.node = saved, node
    return item


def current(item):
    def check(db):
        row = db.execute('SELECT digest,path,duration,next_offset,status FROM chunks WHERE id=?', (item.cid,)).fetchone()
        mapping = db.execute('SELECT batch_id,receipt_id FROM recording_work WHERE chunk_id=?', (item.cid,)).fetchone()
        return (row is not None and tuple(row) == (item.receipt.digest, str(item.path), 4., 4., 'completed')
                and mapping is not None and tuple(mapping) == (item.rid, item.receipt.receipt_id))
    return check


def claim(env, item, *, token=None):
    return env.value.claim_primary(item.sid, item.rid, token or f'{item.number:032x}',
        item.saved, item.node, item.receipt.encoded, current_check=current(item), space=links.expected_space())


def append(env, item, reservation):
    return env.value.append_primary(reservation, item.node, vector(1., 0.), 'f' * 32,
        success_capability=env.capability, current_check=current(item))


def success(env, item):
    reservation = claim(env, item)
    assert reservation['should_probe'] is True
    assert append(env, item, reservation)['state'] == 'success'
    return reservation


def assert_accounting(env):
    with connection(env.store.path) as db:
        scopes = db.execute(f'SELECT * FROM {history.SCOPES} WHERE scope_seq>0').fetchall()
        totals = [0, 0, 0]
        for row in scopes:
            seq = row['scope_seq']
            attempts = db.execute(f'SELECT COALESCE(SUM(charge),0),COALESCE(SUM(reserved_bytes),0),COALESCE(SUM(staging_bytes),0) FROM {history.ATTEMPTS} WHERE scope_seq=?', (seq,)).fetchone()
            decisions = db.execute(f'SELECT COALESCE(SUM(charge),0) FROM {history.DECISIONS} WHERE scope_seq=?', (seq,)).fetchone()[0]
            assert row['retained_bytes'] == history.SCOPE_CHARGE + attempts[0] + decisions
            assert row['reserved_bytes'] == attempts[1]
            assert row['staging_bytes'] == attempts[2]
            for index, field in enumerate(('retained_bytes', 'reserved_bytes', 'staging_bytes')):
                totals[index] += row[field]
        assert history.aggregate_usage(db) == tuple(totals)
        for obj in db.execute(f'SELECT * FROM {history.OBJECTS}'):
            assert obj['refcount'] == db.execute(f'SELECT COUNT(*) FROM {history.REFS} WHERE scope_seq=? AND kind=? AND digest=?',
                (obj['scope_seq'], obj['kind'], obj['digest'])).fetchone()[0]
            assert hashlib.sha256(obj['body']).hexdigest() == obj['digest']


def test_accounting_component_maxima_are_scalar_arithmetic_only():
    # No maximum-sized object, dependency graph or database is allocated.
    primary_maximum = (1024 + 5120 + 1024 + 1280 + 256 + 512
                       + 512 * (128 + 2) + 512)
    moss_maximum = (1024 + 4 * 5120 + 1024 + 524800 + 128 * 1280
                    + 128 * 256 + 4 * 512 + 768 * 512 + 128 * 512)
    assert primary_maximum == 10240 + 128 * 512 + 512 == 76288
    assert moss_maximum == 1204736
    assert history.RECORDING_BYTES == 64 * 1024**2
    assert history.GLOBAL_BYTES == 128 * 1024**2
    assert history.STAGING_BYTES == 8 * 1024**2
    assert history.PROBE_STAGING_BYTES == 512 * 1024


def test_primary_exact_reservation_success_charge_and_immutable_bytes(history_env):
    env = history_env; item = primary_fixture(env); reservation = claim(env, item)
    reserved = attempt_row(env.store, reservation['token'])
    maximum = 10240 + 512 * len(item.saved['packet']['refs']) + 512
    assert reserved['charge'] == history.ATTEMPT_CHARGE
    assert reserved['reserved_bytes'] == maximum - history.ATTEMPT_CHARGE
    assert reserved['staging_bytes'] == history.PROBE_STAGING_BYTES
    assert_accounting(env)
    append(env, item, reservation)
    with connection(env.store.path) as db:
        cert = db.execute(f'SELECT * FROM {history.CERTIFICATES}').fetchone()
        assert cert['vector'] == struct.pack('<512f', *vector(1., 0.))
        assert cert['vector_sha256'] == hashlib.sha256(cert['vector']).hexdigest()
        expected = (1024 + 1024 + len(cert['header']) + 3072 + len(item.receipt.encoded)
                    + 256 + 256 + 512 + 3 * 512 + 512)
        assert attempt_row(env.store, reservation['token'])['charge'] == expected
        assert db.execute(f'SELECT COUNT(*) FROM {history.DEPENDENCIES}').fetchone()[0] == 3
        with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
            db.execute(f'UPDATE {history.CERTIFICATES} SET vector=?', (b'\0' * 2048,))
    before = snapshot(env.store)
    assert append(env, item, reservation)['existing'] is True
    assert snapshot(env.store) == before
    assert_accounting(env)


@pytest.mark.parametrize('limit', ['RECORDING_BYTES', 'GLOBAL_BYTES', 'STAGING_BYTES'])
def test_exact_quota_boundary_and_refusal_rollback(history_env, monkeypatch, limit):
    env = history_env; item = primary_fixture(env)
    maximum = 10240 + 512 + 512
    boundary = history.PROBE_STAGING_BYTES if limit == 'STAGING_BYTES' else history.SCOPE_CHARGE + maximum
    before = snapshot(env.store)
    monkeypatch.setattr(history, limit, boundary - 1)
    with pytest.raises(ReadError, match='capacity_exhausted'):
        claim(env, item)
    assert snapshot(env.store) == before
    monkeypatch.setattr(history, limit, boundary)
    reservation = claim(env, item)
    assert reservation['should_probe'] is True
    env.value.finish_primary(reservation, 'cancelled')
    row = attempt_row(env.store, reservation['token'])
    assert (row['charge'], row['reserved_bytes'], row['staging_bytes']) == (1024, 0, 0)
    assert_accounting(env)


def test_shared_legacy_usage_is_included_before_new_admission(history_env, monkeypatch):
    env = history_env; item = primary_fixture(env)
    with connection(env.store.path) as db:
        db.execute(f'UPDATE {legacy.SCOPES} SET retained_bytes=? WHERE scope_seq=0', (legacy.SCOPE_BYTES,))
    monkeypatch.setattr(history, 'GLOBAL_BYTES', legacy.SCOPE_BYTES + history.SCOPE_CHARGE + 11264 - 1)
    before = snapshot(env.store)
    with pytest.raises(ReadError, match='capacity_exhausted'):
        claim(env, item)
    assert snapshot(env.store) == before


def test_unknown_claim_ack_closes_only_and_never_grants_replay(history_env):
    env = history_env; item = primary_fixture(env); reservation = claim(env, item)
    recovered = history.HistoryStore(env.store, clock=lambda: 0.)
    with pytest.raises(ReadError, match='history_attempt_conflict'):
        recovered.resolve_primary(reservation['token'], 'b' * 64)
    result = recovered.resolve_primary(reservation['token'], reservation['identity'])
    assert result['found'] and result['resolved'] and not result['should_probe']
    row = attempt_row(env.store, reservation['token'])
    assert json.loads(row['terminal'])['reason'] == 'storage_unknown'
    assert row['basis'] is None and row['charge'] == 1024
    assert not claim(env, item)['should_probe']
    assert not env.owner.invocations
    assert not snapshot(env.store)[history.CERTIFICATES]
    assert_accounting(env)


def test_terminal_failure_freezes_first_payload_even_after_lost_ack(history_env, monkeypatch):
    env = history_env; item = primary_fixture(env); reservation = claim(env, item)
    original = env.value._database
    @contextmanager
    def lose_ack(*, write=False):
        with original(write=write) as db:
            yield db
        if write:raise ReadError(503, 'synthetic_lost_terminal_ack')
    monkeypatch.setattr(env.value, '_database', lose_ack)
    with pytest.raises(ReadError, match='synthetic_lost_terminal_ack'):
        env.value.finish_primary(reservation, 'cleanup_unknown')
    before = attempt_row(env.store, reservation['token'])
    monkeypatch.setattr(env.value, '_database', original)
    assert env.value.finish_primary(reservation, 'cancelled')['state'] == 'failure'
    assert attempt_row(env.store, reservation['token']) == before
    assert json.loads(before['terminal'])['reason'] == 'cleanup_unknown'
    assert_accounting(env)


def test_success_unknown_ack_resolves_exact_certificate_without_append(history_env):
    env = history_env; item = primary_fixture(env); reservation = success(env, item)
    before = snapshot(env.store); invocations = list(env.owner.invocations)
    recovered = history.HistoryStore(env.store, clock=lambda: 0.)
    result = recovered.resolve_primary(reservation['token'], reservation['identity'])
    assert result['state'] == 'success' and not result['should_probe']
    assert snapshot(env.store) == before and env.owner.invocations == invocations
    assert result['terminal_sha256'] == attempt_row(env.store, reservation['token'])['terminal_sha256']


@pytest.mark.parametrize('failure', ['capability', 'cleanup', 'current', 'missing_packet'])
def test_append_refusal_is_atomic_and_retains_no_vector(history_env, failure):
    env = history_env; item = primary_fixture(env); reservation = claim(env, item)
    capability, check = env.capability, current(item)
    reason = {'capability': 'history_fresh_success_required', 'cleanup': 'history_cleanup_unverified',
              'current': 'history_source_changed', 'missing_packet': 'history_proof_missing'}[failure]
    if failure == 'capability':capability = object()
    elif failure == 'cleanup':env.owner.allow = False
    elif failure == 'current':check = lambda db: False
    else:
        # A fixture-only missing external proof, not an ordinary production erase.
        with connection(env.store.path) as db:
            db.execute('DROP TRIGGER recording_primary_erasure_v1')
            db.execute(f'DELETE FROM {provenance.TABLE}')
    before = snapshot(env.store)
    with pytest.raises(ReadError, match=reason):
        env.value.append_primary(reservation, item.node, vector(1., 0.), 'f' * 32,
            success_capability=capability, current_check=check)
    assert snapshot(env.store) == before
    env.value.finish_primary(reservation, 'proof_missing' if failure == 'missing_packet' else 'probe_failed')
    terminal = attempt_row(env.store, reservation['token'])
    assert terminal['state'] == json.loads(terminal['terminal'])['outcome'] == 'failure'
    assert terminal['basis'] is None
    assert not snapshot(env.store)[history.CERTIFICATES]
    assert_accounting(env)


@pytest.mark.parametrize('mutation', ['source', 'segment', 'mapping', 'stream', 'cancel', 'rewind', 'recording'])
def test_bound_mutation_fences_only_exact_scope_and_preserves_evidence(history_env, mutation):
    env = history_env; one = primary_fixture(env); two = primary_fixture(env, number=2)
    success(env, one); success(env, two)
    first = scope_row(env.store, one.rid); other = scope_row(env.store, two.rid)
    evidence = snapshot(env.store)
    statements = {
        'source': ('UPDATE chunks SET digest=? WHERE id=?', ('b' * 64, one.cid)),
        'segment': ('UPDATE segments SET text=? WHERE id=?', ('changed', one.number)),
        'mapping': ('UPDATE recording_work SET capture_start_frame=1 WHERE chunk_id=?', (one.cid,)),
        'stream': ('UPDATE stream_chunks SET position=1 WHERE chunk_id=?', (one.cid,)),
        'cancel': ('UPDATE chunks SET status=\'cancelled\' WHERE id=?', (one.cid,)),
        'rewind': ('UPDATE chunks SET next_offset=3. WHERE id=?', (one.cid,)),
        'recording': ('UPDATE recording_batches SET state=\'cancelled\' WHERE id=?', (one.rid,)),
    }
    with connection(env.store.path) as db:db.execute(*statements[mutation])
    after = scope_row(env.store, one.rid)
    assert after['authority_revision'] == first['authority_revision'] + 1
    assert after['authority_blocked'] == 'source_changed' and after['current_slot'] is None
    assert scope_row(env.store, two.rid) == other
    for table in (history.CERTIFICATES, history.ATTEMPTS, history.OBJECTS, history.REFS, history.DEPENDENCIES):
        assert snapshot(env.store)[table] == evidence[table]


def test_outside_append_forward_progress_and_unrelated_mutation_do_not_touch_blocked_scope(history_env):
    env = history_env; bound = primary_fixture(env); success(env, bound)
    first = scope_row(env.store, bound.rid)
    outside = source_fixture(env, number=2, rid=bound.rid)
    with connection(env.store.path) as db:
        db.execute('UPDATE chunks SET next_offset=5. WHERE id=?', (bound.cid,))
        db.execute('UPDATE chunks SET status=\'paused\' WHERE id=?', (bound.cid,))
        db.execute('UPDATE sessions SET transcript_revision=transcript_revision+1 WHERE id=?', (bound.sid,))
    assert scope_row(env.store, bound.rid) == first
    with connection(env.store.path) as db:
        db.execute('UPDATE chunks SET digest=? WHERE id=?', ('c' * 64, bound.cid))
    blocked = scope_row(env.store, bound.rid)
    with connection(env.store.path) as db:
        db.execute('UPDATE chunks SET digest=? WHERE id=?', ('d' * 64, outside.cid))
        db.execute('UPDATE recording_work SET capture_start_frame=1 WHERE chunk_id=?', (outside.cid,))
    assert scope_row(env.store, bound.rid) == blocked


def test_bound_invalidation_failure_rolls_back_source_mutation(history_env):
    env = history_env; item = primary_fixture(env); success(env, item)
    with connection(env.store.path) as db:
        db.execute(f'UPDATE {history.SCOPES} SET authority_revision=? WHERE scope_seq>0', (history.MAX_REVISION,))
    before = snapshot(env.store)
    with pytest.raises(sqlite3.IntegrityError, match='History authority exhausted'):
        with connection(env.store.path) as db:
            db.execute('UPDATE chunks SET digest=? WHERE id=?', ('c' * 64, item.cid))
    assert snapshot(env.store) == before
    with connection(env.store.path) as db:
        assert db.execute('SELECT digest FROM chunks WHERE id=?', (item.cid,)).fetchone()[0] == item.receipt.digest


def test_explicit_erasure_removes_actual_certificates_receipts_and_accounting(history_env):
    env = history_env; one = primary_fixture(env); two = primary_fixture(env, number=2, sid='other-session')
    success(env, one); success(env, two)
    other = scope_row(env.store, two.rid)
    with connection(env.store.path) as db:
        with pytest.raises(ReadError, match='history_erasure_requires_tombstone'):
            db.execute('BEGIN IMMEDIATE'); history.erase_session(db, one.sid)
    with connection(env.store.path) as db:
        db.execute('UPDATE sessions SET deleted=1 WHERE id=?', (one.sid,))
        history.erase_session(db, one.sid)
        db.execute(f'DELETE FROM {provenance.TABLE} WHERE sid=?', (one.sid,))
        assert db.execute(f'SELECT COUNT(*) FROM {history.CERTIFICATES}').fetchone()[0] == 1
        assert db.execute(f'SELECT COUNT(*) FROM {history.OBJECTS}').fetchone()[0] == 1
        assert db.execute(f'SELECT COUNT(*) FROM {history.REFS}').fetchone()[0] == 2
        for table in TABLES[1:]:
            assert db.execute(f'SELECT COUNT(*) FROM {table} WHERE scope_seq=1').fetchone()[0] == 0
        assert db.execute('SELECT deleted FROM sessions WHERE id=?', (one.sid,)).fetchone()[0] == 1
        assert db.execute(f'SELECT packets FROM {provenance.USAGE} WHERE scope=\'\'').fetchone()[0] == 1
    assert scope_row(env.store, two.rid) == other
    assert_accounting(env)


def test_schema_is_additive_exact_and_reinitialization_does_not_rewrite(history_env):
    env = history_env; item = primary_fixture(env); success(env, item)
    before = snapshot(env.store)
    with connection(env.store.path) as db:
        db.execute('BEGIN IMMEDIATE'); history.init_schema(db); legacy.init_schema(db)
        assert 'history_format' in {row['name'] for row in db.execute(f'PRAGMA table_info({legacy.BATCHES})')}
        with pytest.raises(ValueError, match='Incompatible recording history schema'):
            db.execute('DROP INDEX recording_history_exact_ref_v1')
            history.init_schema(db)
        db.rollback()
    assert snapshot(env.store) == before


def moss_fixture(env):
    support, pcm, raw = ordinary(seed=1, frames=FRAMES)
    item = source_fixture(env, pcm=pcm, cid='chunk-1')
    proof = json.loads(raw)
    proof['dependencies'][0]['receipt_sha256'] = item.receipt.receipt_id
    result = projection(support, '[0.75] [S01] Synthetic phrase. [3.5]')
    batch, _ = links.capture_candidates_v3(support, pcm, cells.canonical(proof), result,
        '1' * 32, '2' * 64, links.expected_space())
    assert len(batch['nodes']) == 1
    assert len(history.encoded(batch)) <= 16384
    item.moss_inputs = (support, pcm, cells.canonical(proof), result)
    return item, batch


def test_moss_copied_proof_receipt_dedup_edges_and_real_erasure(history_env):
    env = history_env; item, batch = moss_fixture(env)
    for index in (1, 2):
        bare = deepcopy(batch)
        if index == 2:
            # A different admitted result retains its own exact typed proof;
            # only the exact same Receipt object is deduplicated.
            bare, _ = links.capture_candidates_v3(*item.moss_inputs,
                "3" * 32, "2" * 64, links.expected_space())
        token = env.value.claim_moss(item.sid, item.rid, f'{index:032x}', bare,
            [item.receipt.encoded], links.expected_space(), current_check=current(item))
        embedded = deepcopy(bare)
        embedded['nodes'][0]['vector'] = vector(1., 0.)
        env.value.append_moss(token, embedded, 'f' * 32,
            success_capability=env.capability, current_check=current(item))
    with connection(env.store.path) as db:
        receipts = db.execute(f'SELECT * FROM {history.OBJECTS} WHERE kind=\'receipt\'').fetchall()
        assert len(receipts) == 1 and receipts[0]['refcount'] == 2
        assert receipts[0]['body'] == item.receipt.encoded
        proofs = db.execute(f'SELECT * FROM {history.OBJECTS} WHERE kind=\'proof\'').fetchall()
        assert len(proofs) == 2 and all(row['refcount'] == 1 for row in proofs)
        assert db.execute(f'SELECT COUNT(*) FROM {history.CERTIFICATES}').fetchone()[0] == 2
        for row in proofs:
            assert row['charge'] == len(row['body']) + 512
            assert 'vector' not in json.loads(row['body'])['nodes'][0]
        source = db.execute(f'SELECT * FROM {history.SOURCES}').fetchone()
        assert source['admission_revision'] == source['certificate_count'] == 2
    assert_accounting(env)
    with connection(env.store.path) as db:
        db.execute('UPDATE sessions SET deleted=1 WHERE id=?', (item.sid,))
        history.erase_session(db, item.sid)
        for table in TABLES[1:]:assert db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
        assert history.aggregate_usage(db) == (0, 0, 0)


def publish_one(env, item):
    from audio_backend.recording_speaker_history_compare import ComparisonBuild
    def receipt_valid(receipt, budget):
        info = item.path.stat()
        return receipt == item.receipt and receipt.signature == (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    env.owner.cell_source = SimpleNamespace(owner=SimpleNamespace(
        authority=SimpleNamespace(receipt_valid=receipt_valid)))
    pending = env.value.begin_comparison()
    assert pending is not None
    page = env.value.active_source_page(pending)
    assert page['complete'] and len(page['rows']) == 1
    env.value.revalidate_active_sources(pending, page)
    build = ComparisonBuild(pending['token'], admitted_bytes=history.COMPARE_BYTES, clock=lambda: 0.)
    observations = env.value.observation_page(pending)
    witnesses = env.value.witness_page(pending)
    assert observations['complete'] and witnesses['complete']
    assert len(observations['rows']) == 1 and len(witnesses['rows']) <= 1
    build.feed_observations(pending['token'], 0, observations['rows'], complete=True)
    build.feed_witnesses(pending['token'], 0, witnesses['rows'], complete=True)
    for _ in range(32):
        result = build.step()
        if result['state'] not in ('loading', 'pending'):break
    assert result['state'] == 'ready'
    output = build.output_page()
    assert output['complete'] and len(output['rows']) <= 2
    env.value.write_decision_page(pending, output)
    assert env.value.publish_comparison(pending, build.decision_counts) == {'saved': True}
    return pending


def test_passive_model_disabled_read_is_db_only_and_restart_is_conservative(history_env, monkeypatch):
    env = history_env; item = primary_fixture(env); success(env, item); publish_one(env, item)
    env.store.settings.recording_primary_observation_policy = 'off'
    env.store.settings.recording_speaker_links_enabled = False
    assert not Path(env.store.settings.recording_speaker_model_path).exists()
    assert not env.value.enabled
    before = snapshot(env.store)
    def forbidden(*args, **kwargs):pytest.fail('Passive history read attempted active work')
    monkeypatch.setattr(env.value, 'begin_comparison', forbidden)
    monkeypatch.setattr(env.value, 'revalidate_active_sources', forbidden)
    monkeypatch.setattr(env.owner.cell_source.owner.authority, 'receipt_valid', forbidden)
    monkeypatch.setattr(primary, 'standalone_current', forbidden)
    refs = [('primary', item.node['primary_source_ref'])]
    with connection(env.store.path) as db:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        saved = history.read_saved_for_refs(db, item.sid, item.rid, refs)
        assert saved['history_status'] == 'current' and saved['complete']
        assert len(saved['annotations']) == 1
        assert saved['annotations'][0]['speaker_identity_valid'] is False
        assert saved['validation_semantics'] == 'validated_at_decision_revision'
        limited = history.read_saved_for_refs(db, item.sid, item.rid, refs, byte_limit=1)
        assert limited['history_status'] == 'pending' and limited['annotations'] == []
    assert snapshot(env.store) == before
    monkeypatch.setattr(history, 'BOOT_ID', 'e' * 32)
    with connection(env.store.path) as db:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        old = history.read_saved_for_refs(db, item.sid, item.rid, refs)
    assert old['history_status'] == 'historical' and old['annotations'] == []
    assert old['reason'] == 'active_source_revalidation_pending'
    assert snapshot(env.store) == before


def test_saved_current_pointer_cannot_hide_bound_invalidation(history_env):
    env = history_env; item = primary_fixture(env); success(env, item); publish_one(env, item)
    before = snapshot(env.store)
    with connection(env.store.path) as db:
        db.execute('UPDATE segments SET text=\'changed\' WHERE id=?', (item.number,))
        saved = history.read_saved_for_refs(db, item.sid, item.rid,
                                            [('primary', item.node['primary_source_ref'])])
    assert saved['history_status'] == 'invalid' and saved['annotations'] == []
    assert saved['reason'] == 'source_changed'
    assert snapshot(env.store)[history.DECISIONS] == before[history.DECISIONS]
    assert snapshot(env.store)[history.ROWS] == before[history.ROWS]


def old_reader_init(db):
    """Call the pinned baseline's exact initializer without importing its module."""
    baseline = Path(os.environ['PRIMARY_HISTORY_LEGACY_LINK_STORE_SOURCE'])
    raw = baseline.read_bytes()
    assert len(raw) == 60146
    assert hashlib.sha256(raw).hexdigest() == '5929d7e0d387d6c9f6ebfd9a313ef5d881f6e305f26a53d2b67c34a7779b506a'
    source = raw.decode('utf-8')
    begin = source.index('\ndef init_schema(db):')
    end = source.index('\ndef _fail(', begin)
    schema = dict(legacy.SCHEMA)
    schema[legacy.BATCHES] = legacy.PRE_HISTORY_BATCH_SCHEMA
    namespace = dict(SCHEMA=schema, BATCHES=legacy.BATCHES, SCOPES=legacy.SCOPES,
                     LEGACY_BATCH_SCHEMA=legacy.LEGACY_BATCH_SCHEMA,
                     FAILURE_COLUMN=legacy.FAILURE_COLUMN)
    # Execution is inside this explicitly selected test, never at import time.
    # Only a function definition is loaded; no baseline imports or other code.
    exec(source[begin:end], namespace)
    return namespace['init_schema'](db)


def test_additive_marker_old_reader_refusal_and_backup_restore(tmp_path):
    original, backup, restored = [tmp_path / name for name in
                                  ('old.sqlite3', 'backup.sqlite3', 'restored.sqlite3')]
    with connection(original) as db:
        base_schema(db); db.execute('BEGIN IMMEDIATE'); old_reader_init(db)
        db.execute('INSERT INTO sessions VALUES(\'preserved\',0,7)')
        old_schema = [tuple(row) for row in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')]
        old_scopes = [tuple(row) for row in db.execute(f'SELECT * FROM {legacy.SCOPES}')]
    with connection(original) as src, connection(backup) as dst:src.backup(dst)
    backup_digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    with connection(original) as db:
        db.execute('BEGIN IMMEDIATE'); legacy.init_schema(db); history.init_schema(db)
        assert [tuple(row) for row in db.execute(f'SELECT * FROM {legacy.SCOPES}')] == old_scopes
        assert db.execute('SELECT transcript_revision FROM sessions').fetchone()[0] == 7
        assert history.aggregate_usage(db) == (0, 0, 0)
    with pytest.raises(ValueError, match='Incompatible recording speaker link schema'):
        with connection(original) as db:
            db.execute('BEGIN IMMEDIATE'); old_reader_init(db)
    with connection(backup) as src, connection(restored) as dst:src.backup(dst)
    with connection(restored) as db:
        db.execute('BEGIN IMMEDIATE'); old_reader_init(db)
        assert [tuple(row) for row in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')] == old_schema
        assert [tuple(row) for row in db.execute(f'SELECT * FROM {legacy.SCOPES}')] == old_scopes
        assert db.execute('SELECT transcript_revision FROM sessions').fetchone()[0] == 7
        assert not db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (history.SCOPES,)).fetchone()
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == backup_digest


def test_success_retry_requires_identical_frozen_vector_and_invocation(history_env):
    env = history_env; item = primary_fixture(env); reservation = success(env, item)
    before = snapshot(env.store)
    with pytest.raises(ReadError, match='history_terminal_conflict'):
        env.value.append_primary(reservation, item.node, vector(0., 1.), 'f' * 32,
            success_capability=env.capability, current_check=current(item))
    assert snapshot(env.store) == before
    # A capability-shaped object or a different owner cannot bless saved bytes.
    with pytest.raises(ReadError, match='history_owner_changed'):
        env.value.bind_success_owner(SimpleNamespace())
    assert snapshot(env.store) == before


def test_existing_legacy_scope_remains_frozen_and_cannot_mix_history(history_env):
    env = history_env; item = primary_fixture(env)
    space_raw, space_id = legacy._space(links.expected_space())
    with connection(env.store.path) as db:
        db.execute(f'''INSERT INTO {legacy.SCOPES}(scope_seq,scope_id,session_id,recording_id,
            policy_sha256,space_json,space_id,retained_bytes) VALUES(1,?,?,?,?,?,?,?)''',
            (legacy.scope_identity(item.sid, item.rid), item.sid, item.rid,
             links.POLICY_SHA256_V3, space_raw, space_id, legacy.SCOPE_BYTES))
        db.execute(f'UPDATE {legacy.SCOPES} SET retained_bytes=? WHERE scope_seq=0', (legacy.SCOPE_BYTES,))
        old = [tuple(row) for row in db.execute(f'SELECT * FROM {legacy.SCOPES} ORDER BY scope_seq')]
    before = snapshot(env.store)
    assert env.value.uses_history(item.sid, item.rid) is False
    with pytest.raises(ReadError, match='history_legacy_scope_frozen'):
        claim(env, item)
    assert snapshot(env.store) == before
    with connection(env.store.path) as db:
        assert [tuple(row) for row in db.execute(f'SELECT * FROM {legacy.SCOPES} ORDER BY scope_seq')] == old
