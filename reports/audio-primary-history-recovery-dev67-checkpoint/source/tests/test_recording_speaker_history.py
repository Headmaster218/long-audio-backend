"""Finite, model-free HistoryStore contracts. Execution is separately gated.

Fixtures own fresh SQLite files and at most two 4-second synthetic PCM WAVs
(128,044 bytes each). No user audio, model, child process, server, benchmark,
maximum-sized proof, or ambient database is an input. Per test: <=2 scopes,
<=2 attempts/observations/vector certificates/source versions, <=2 primary packets, <=6 dependency
rows, <=2 copied receipts, <=2 small MOSS proof objects, and one decision slot
per scope (at most two total). Retirement and comparison loops each take at
most 32 steps; direct immutability checks attempt at most seven fixed updates.
Each diagnostic is at most 512 bytes, except one explicit 513-byte rejection.
Restart/fault cases reuse these same files and durable evidence.
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
import stat
import struct
import threading
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


def observation_rows(store, token):
    with connection(store.path) as db:
        return [dict(row) for row in db.execute(
            f'SELECT * FROM {history.CERTIFICATES} WHERE attempt=? ORDER BY sequence', (token,))]


def assert_missing_observation(row, kind):
    assert row['evidence_kind'] == kind
    assert row['certificate_id'] is row['vector'] is row['vector_sha256'] is None
    assert len(row['observation_id']) == 64 and len(row['invocation_id']) == 32
    assert row['charge'] == len(row['header']) + 1024


def failure_bytes(reason='child_failed'):
    raw = history.encoded({'schema': legacy.FAILURE_SCHEMA, 'stage': 'child_wait',
        'reason': reason, 'child_dispatch': 'possible', 'error_type': 'RuntimeError'})
    assert legacy.validate_failure(raw)['reason'] == reason and len(raw) <= 512
    return raw


@pytest.fixture
def history_env(tmp_path, monkeypatch, request):
    # Each synthetic database owns isolated process-local notification metadata.
    # Restore every field at teardown, including a deliberately latched overflow.
    monkeypatch.setattr(history, '_AUTHORITY_UNKNOWN_DATABASES', {})
    monkeypatch.setattr(history, '_AUTHORITY_UNKNOWN_BYTES', 0)
    monkeypatch.setattr(history, '_AUTHORITY_UNKNOWN_OVERFLOW', False)
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
    from audio_backend.resources import InstanceLock
    instance_lock = InstanceLock(tmp_path / 'instance.lock')
    instance_lock.acquire()
    request.addfinalizer(instance_lock.release)
    value.bind_recovery(instance_lock)
    assert value.reconcile_primary()
    owner = SimpleNamespace(allow=True, invocations=[], pending=None,
                            primary_admissions={}, moss_admissions={})
    made = []
    def receipt_valid(receipt, budget):
        budget.check()
        candidates = [item for item in made if item.cid == receipt.chunk_id]
        if len(candidates) != 1 or receipt != candidates[0].receipt:
            return False
        try:
            info = candidates[0].path.stat(follow_symlinks=False)
        except OSError:
            return False
        return stat.S_ISREG(info.st_mode) and receipt.signature == (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    owner.cell_source = SimpleNamespace(owner=SimpleNamespace(
        authority=SimpleNamespace(receipt_valid=receipt_valid),
        _publication_guard=threading.RLock()))
    owner._history_source_quiescent = lambda version: owner.pending is None
    def primary_admission(attempt_id, saved, node, invocation):
        owned = owner.primary_admissions.get(attempt_id)
        return owned is not None and owned == (history.encoded([saved, node]), invocation)
    def moss_admission(attempt_id, batch, invocation):
        owned = owner.moss_admissions.get(attempt_id)
        return owned is not None and owned == (history.encoded(batch), invocation)
    def primary_success(reservation, node, values, invocation):
        owner.invocations.append(invocation)
        owned = owner.primary_admissions.get(reservation['token'])
        return (owner.allow and owned is not None and invocation == owned[1]
                and history.encoded(node) == history.encoded(json.loads(owned[0])[1]))
    def moss_success(reservation, batch, invocation):
        owner.invocations.append(invocation)
        owned = owner.moss_admissions.get(reservation['token'])
        bare = deepcopy(batch)
        for node in bare['nodes']:node.pop('vector', None)
        return (owner.allow and owned is not None and invocation == owned[1]
                and history.encoded(bare) == owned[0])
    owner._primary_admission_matches = primary_admission
    owner._moss_admission_matches = moss_admission
    owner._primary_success_matches = primary_success
    owner._moss_success_matches = moss_success
    capability = value.bind_success_owner(owner)
    return SimpleNamespace(store=store, value=value, owner=owner, capability=capability,
                           directory=tmp_path, made=made, instance_lock=instance_lock)


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
    item.owned_primary = history.encoded([saved, node])
    return item


def current(item):
    def check(db):
        row = db.execute('SELECT digest,path,duration,next_offset,status FROM chunks WHERE id=?', (item.cid,)).fetchone()
        mapping = db.execute('SELECT batch_id,receipt_id FROM recording_work WHERE chunk_id=?', (item.cid,)).fetchone()
        return (row is not None and tuple(row) == (item.receipt.digest, str(item.path), 4., 4., 'completed')
                and mapping is not None and tuple(mapping) == (item.rid, item.receipt.receipt_id))
    return check


def invocation_for(token):
    return hashlib.sha256(('fixture-invocation:' + token).encode()).hexdigest()[:32]


def claim(env, item, *, token=None):
    token = token or f'{item.number:032x}'
    # The fake manager owns the original authenticated fixture bytes. A caller
    # cannot turn a changed or unrelated packet into admission with a boolean.
    assert item in env.made and item.owned_primary == history.encoded([item.saved, item.node])
    owned = (item.owned_primary, invocation_for(token))
    assert env.owner.primary_admissions.setdefault(token, owned) == owned
    return env.value.claim_primary(item.sid, item.rid, token,
        item.saved, item.node, item.receipt.encoded, current_check=current(item), space=links.expected_space(),
        admission_capability=env.capability, invocation_id=owned[1])


def append(env, item, reservation):
    return env.value.append_primary(reservation, item.node, vector(1., 0.), invocation_for(reservation['token']),
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
            decisions = db.execute(f'SELECT * FROM {history.DECISIONS} WHERE scope_seq=?', (seq,)).fetchall()
            decision_charge = sum(decision['charge'] for decision in decisions)
            decision_reserved = sum(66560 + 1920 * decision['observation_count'] - 384 - decision['charge']
                                    for decision in decisions if not decision['complete'])
            evidence_charge = sum(db.execute(f'SELECT COALESCE(SUM(charge),0) FROM {table} WHERE scope_seq=?', (seq,)).fetchone()[0]
                for table in (history.CERTIFICATES, history.OBJECTS, history.REFS, history.DEPENDENCIES, history.SOURCES))
            assert row['retained_bytes'] == history.SCOPE_CHARGE + attempts[0] + decision_charge + evidence_charge
            assert row['reserved_bytes'] == attempts[1] + decision_reserved
            assert row['staging_bytes'] == attempts[2]
            observations = db.execute(f'SELECT COUNT(*),COALESCE(SUM(evidence_kind=\'vector\'),0) FROM {history.CERTIFICATES} WHERE scope_seq=?', (seq,)).fetchone()
            assert row['observation_count'] == observations[0]
            assert row['vector_count'] == observations[1]
            assert row['witness_count'] == db.execute(f'SELECT COALESCE(SUM(witness_count),0) FROM {history.ATTEMPTS} WHERE scope_seq=?', (seq,)).fetchone()[0]
            for index, field in enumerate(('retained_bytes', 'reserved_bytes', 'staging_bytes')):
                totals[index] += row[field]
        assert history.aggregate_usage(db) == tuple(totals)
        for obj in db.execute(f'SELECT * FROM {history.OBJECTS}'):
            assert obj['refcount'] == db.execute(f'SELECT COUNT(*) FROM {history.REFS} WHERE scope_seq=? AND kind=? AND digest=?',
                (obj['scope_seq'], obj['kind'], obj['digest'])).fetchone()[0]
            assert hashlib.sha256(obj['body']).hexdigest() == obj['digest']
        for source in db.execute(f'SELECT * FROM {history.SOURCES}'):
            admissions = db.execute(f'SELECT attempt FROM {history.DEPENDENCIES} WHERE scope_seq=? AND kind=\'source\' AND dependency_key=?',
                (source['scope_seq'], source['chunk_id'])).fetchall()
            assert source['admission_revision'] == len(admissions)
            counts = [db.execute(f'SELECT COUNT(*),COALESCE(SUM(evidence_kind=\'vector\'),0) FROM {history.CERTIFICATES} WHERE attempt=?',
                (entry['attempt'],)).fetchone() for entry in admissions]
            assert source['observation_count'] == sum(count[0] for count in counts)
            assert source['vector_count'] == sum(count[1] for count in counts)


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
    assert history.CONTROLLER_METADATA_BYTES == 512 * 1024


def test_primary_exact_reservation_success_charge_and_immutable_bytes(history_env):
    env = history_env; item = primary_fixture(env); reservation = claim(env, item)
    reserved = attempt_row(env.store, reservation['token'])
    maximum = 10240 + 512 * len(item.saved['packet']['refs']) + 512
    pending = observation_rows(env.store, reservation['token'])[0]
    assert_missing_observation(pending, 'pending')
    assert pending['invocation_id'] == invocation_for(reservation['token'])
    assert reserved['basis'] is not None and reserved['terminal'] is None
    assert reserved['charge'] == history.ATTEMPT_CHARGE + history.BASIS_CHARGE
    assert reserved['reserved_bytes'] == 2048
    assert reserved['staging_bytes'] == history.PROBE_STAGING_BYTES
    admitted = scope_row(env.store, item.rid)
    assert admitted['retained_bytes'] + admitted['reserved_bytes'] <= history.SCOPE_CHARGE + maximum
    assert (admitted['observation_count'], admitted['vector_count'], admitted['history_revision']) == (1, 0, 1)
    assert source_row(env, item)['admission_revision'] == 1
    assert_accounting(env)
    append(env, item, reservation)
    with connection(env.store.path) as db:
        cert = db.execute(f'SELECT * FROM {history.CERTIFICATES}').fetchone()
        assert cert['evidence_kind'] == 'vector'
        for field in ('observation_id', 'header', 'invocation_id'):
            assert cert[field] == pending[field]
        assert cert['vector'] == struct.pack('<512f', *vector(1., 0.))
        assert cert['vector_sha256'] == hashlib.sha256(cert['vector']).hexdigest()
        assert cert['certificate_id'] == hashlib.sha256(history.encoded([
            cert['observation_id'], cert['invocation_id'], cert['vector_sha256']])).hexdigest()
        expected = (1024 + 1024 + len(cert['header']) + 3072 + len(item.receipt.encoded)
                    + 256 + 256 + 512 + 3 * 512 + 512)
        assert attempt_row(env.store, reservation['token'])['charge'] == reserved['charge'] == 2048
        assert scope_row(env.store, item.rid)['retained_bytes'] == history.SCOPE_CHARGE + expected
        assert admitted['retained_bytes'] + 2048 == history.SCOPE_CHARGE + expected
        assert db.execute(f'SELECT COUNT(*) FROM {history.DEPENDENCIES}').fetchone()[0] == 3
        with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
            db.execute(f'UPDATE {history.CERTIFICATES} SET vector=?', (b'\0' * 2048,))
    before = snapshot(env.store)
    assert append(env, item, reservation)['existing'] is True
    assert snapshot(env.store) == before
    finalized = scope_row(env.store, item.rid)
    assert (finalized['observation_count'], finalized['vector_count'], finalized['history_revision']) == (1, 1, 2)
    assert finalized['witness_count'] == admitted['witness_count']
    source = source_row(env, item)
    assert (source['admission_revision'], source['observation_count'], source['vector_count']) == (1, 1, 1)
    assert_accounting(env)


@pytest.mark.parametrize('limit', ['RECORDING_BYTES', 'GLOBAL_BYTES', 'STAGING_BYTES'])
def test_exact_quota_boundary_and_refusal_rollback(history_env, monkeypatch, limit):
    env = history_env; item = primary_fixture(env)
    maximum = 10240 + 512 + 512
    boundary = (history.PROBE_STAGING_BYTES + history.CONTROLLER_METADATA_BYTES
                if limit == 'STAGING_BYTES' else history.SCOPE_CHARGE + maximum)
    before = snapshot(env.store)
    monkeypatch.setattr(history, limit, boundary - 1)
    with pytest.raises(ReadError, match='capacity_exhausted'):
        claim(env, item)
    assert snapshot(env.store) == before
    monkeypatch.setattr(history, limit, boundary)
    reservation = claim(env, item)
    assert reservation['should_probe'] is True
    admitted = attempt_row(env.store, reservation['token'])
    env.value.finish_primary(reservation, 'cancelled')
    row = attempt_row(env.store, reservation['token'])
    assert (row['charge'], row['reserved_bytes'], row['staging_bytes']) == (admitted['charge'], 0, 0)
    assert row['basis'] == admitted['basis']
    assert_missing_observation(observation_rows(env.store, reservation['token'])[0], 'candidate_missing')
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


def test_failed_retained_basis_remains_charged_when_later_scope_is_refused(history_env, monkeypatch):
    env = history_env; one = primary_fixture(env); two = primary_fixture(env, number=2)
    reservation = claim(env, one)
    env.value.finish_primary(reservation, 'probe_failed', failure_json=failure_bytes())
    retained = scope_row(env.store, one.rid)['retained_bytes']
    maximum = 10240 + 512 * len(two.saved['packet']['refs']) + 512
    before = snapshot(env.store)
    monkeypatch.setattr(history, 'GLOBAL_BYTES', retained + history.SCOPE_CHARGE + maximum - 1)
    with pytest.raises(ReadError, match='capacity_exhausted'):
        claim(env, two)
    assert snapshot(env.store) == before
    assert_missing_observation(observation_rows(env.store, reservation['token'])[0], 'candidate_missing')
    monkeypatch.setattr(history, 'GLOBAL_BYTES', retained + history.SCOPE_CHARGE + maximum)
    second = claim(env, two)
    assert second['should_probe']
    assert scope_row(env.store, one.rid)['retained_bytes'] == retained
    assert_accounting(env)


def test_unknown_claim_ack_closes_only_and_never_grants_replay(history_env):
    env = history_env; item = primary_fixture(env); reservation = claim(env, item)
    admitted = attempt_row(env.store, reservation['token'])
    pending = observation_rows(env.store, reservation['token'])[0]
    recovered = history.HistoryStore(env.store, clock=lambda: 0.)
    with pytest.raises(ReadError, match='history_attempt_conflict'):
        recovered.resolve_primary(reservation['token'], 'b' * 64)
    result = recovered.resolve_primary(reservation['token'], reservation['identity'])
    assert result['found'] and result['resolved'] and not result['should_probe']
    row = attempt_row(env.store, reservation['token'])
    assert json.loads(row['terminal'])['reason'] == 'storage_unknown'
    assert row['basis'] == admitted['basis'] and row['charge'] == admitted['charge']
    assert not claim(env, item)['should_probe']
    assert not env.owner.invocations
    missing = observation_rows(env.store, reservation['token'])[0]
    assert_missing_observation(missing, 'candidate_missing')
    assert {key: value for key, value in missing.items() if key != 'evidence_kind'} == {
        key: value for key, value in pending.items() if key != 'evidence_kind'}
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
    with pytest.raises(ReadError, match='history_terminal_conflict'):
        env.value.finish_primary(reservation, 'cancelled')
    assert env.value.finish_primary(reservation, 'cleanup_unknown')['state'] == 'failure'
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
        env.value.append_primary(reservation, item.node, vector(1., 0.), invocation_for(reservation['token']),
            success_capability=capability, current_check=check)
    assert snapshot(env.store) == before
    env.value.finish_primary(reservation, 'proof_missing' if failure == 'missing_packet' else 'probe_failed')
    terminal = attempt_row(env.store, reservation['token'])
    assert terminal['state'] == json.loads(terminal['terminal'])['outcome'] == 'failure'
    assert terminal['basis'] is not None
    assert_missing_observation(observation_rows(env.store, reservation['token'])[0], 'candidate_missing')
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


@pytest.mark.parametrize('key', ['chunks.id', 'stream_chunks.chunk_id', 'recording_work.chunk_id',
                               'segments.id', 'recording_batches.id'])
def test_bound_lookup_key_rename_fences_old_scope_and_preserves_unrelated_primary(history_env, key):
    env = history_env; one = primary_fixture(env); two = primary_fixture(env, number=2)
    success(env, one); success(env, two); publish_one(env, one); publish_one(env, two)
    bound = scope_row(env.store, one.rid); other = scope_row(env.store, two.rid)
    other_read = saved_read(env, two); evidence = snapshot(env.store)
    assert saved_read(env, one)['history_status'] == other_read['history_status'] == 'current'
    with connection(env.store.path) as db:
        packets = [tuple(row) for row in db.execute(f'SELECT * FROM {provenance.TABLE} ORDER BY sequence')]
        other_primary = tuple(db.execute('SELECT * FROM segments WHERE id=?', (two.number,)).fetchone())
    statements = {
        'chunks.id': ('UPDATE chunks SET id=? WHERE id=?', ('renamed-source', one.cid)),
        'stream_chunks.chunk_id': ('UPDATE stream_chunks SET chunk_id=? WHERE chunk_id=?', ('renamed-source', one.cid)),
        'recording_work.chunk_id': ('UPDATE recording_work SET chunk_id=? WHERE chunk_id=?', ('renamed-source', one.cid)),
        'segments.id': ('UPDATE segments SET id=? WHERE id=?', (101, one.number)),
        'recording_batches.id': ('UPDATE recording_batches SET id=? WHERE id=?', ('e' * 32, one.rid)),
    }
    if key == 'chunks.id':
        # The retained provenance cid foreign key normally refuses this rename
        # before any trigger effects can commit. Preserve that real boundary.
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            with connection(env.store.path) as db:db.execute(*statements[key])
        assert snapshot(env.store) == evidence
        # This isolated fixture connection disables only FK enforcement so the
        # history trigger's OLD-key defense can also be exercised independently.
        # Production connections and the other four parameters keep FKs enabled.
        fixture_db = sqlite3.connect(env.store.path)
        try:
            fixture_db.execute('PRAGMA foreign_keys=OFF')
            assert fixture_db.execute('PRAGMA foreign_keys').fetchone()[0] == 0
            fixture_db.execute(*statements[key])
            fixture_db.commit()
        finally:
            fixture_db.close()
    else:
        with connection(env.store.path) as db:db.execute(*statements[key])
    changed = scope_row(env.store, one.rid)
    assert changed['authority_revision'] == bound['authority_revision'] + 1
    assert changed['history_revision'] == bound['history_revision']
    assert changed['authority_blocked'] == 'source_changed' and changed['current_slot'] is None
    invalid = saved_read(env, one)
    assert invalid['history_status'] == 'invalid' and invalid['reason'] == 'source_changed'
    assert invalid['annotations'] == []
    assert scope_row(env.store, two.rid) == other and saved_read(env, two) == other_read
    for table in (history.CERTIFICATES, history.ATTEMPTS, history.OBJECTS, history.REFS,
                  history.DEPENDENCIES, history.DECISIONS, history.ROWS):
        assert snapshot(env.store)[table] == evidence[table]
    with connection(env.store.path) as db:
        assert [tuple(row) for row in db.execute(f'SELECT * FROM {provenance.TABLE} ORDER BY sequence')] == packets
        assert tuple(db.execute('SELECT * FROM segments WHERE id=?', (two.number,)).fetchone()) == other_primary
    assert_accounting(env)


@pytest.mark.parametrize('state', ['reserved', 'failure'])
def test_claim_time_dependencies_fence_source_mutation_before_any_vector(history_env, state):
    env = history_env; item = primary_fixture(env); reservation = claim(env, item)
    if state == 'failure':env.value.finish_primary(reservation, 'probe_failed', failure_json=failure_bytes())
    before = snapshot(env.store); scope = scope_row(env.store, item.rid)
    with connection(env.store.path) as db:
        db.execute('UPDATE chunks SET digest=? WHERE id=?', ('e' * 64, item.cid))
    changed = scope_row(env.store, item.rid)
    assert changed['authority_revision'] == scope['authority_revision'] + 1
    assert changed['authority_blocked'] == 'source_changed' and changed['current_slot'] is None
    for table in (history.CERTIFICATES, history.ATTEMPTS, history.OBJECTS, history.REFS, history.DEPENDENCIES):
        assert snapshot(env.store)[table] == before[table]
    saved = saved_read(env, item)
    assert saved['history_status'] == 'invalid' and saved['annotations'] == []
    assert env.owner.invocations == []
    assert_accounting(env)


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


def moss_fixture(env, *, empty=False):
    support, pcm, raw = ordinary(seed=1, frames=FRAMES)
    item = source_fixture(env, pcm=pcm, cid='chunk-1')
    proof = json.loads(raw)
    proof['dependencies'][0]['receipt_sha256'] = item.receipt.receipt_id
    result = projection(support, '[0.125] [S01] Short overlap. [0.25]' if empty
                        else '[0.75] [S01] Synthetic phrase. [3.5]')
    batch, _ = links.capture_candidates_v3(support, pcm, cells.canonical(proof), result,
        '1' * 32, '2' * 64, links.expected_space())
    assert len(batch['nodes']) == (0 if empty else 1)
    assert 1 <= len(batch['witnesses']) <= 1
    assert len(history.encoded(batch)) <= 16384
    item.moss_inputs = (support, pcm, cells.canonical(proof), result)
    item.owned_moss = {batch['job_id']: history.encoded(batch)}
    return item, batch


def claim_moss(env, item, batch, *, token='1' * 32):
    assert item in env.made and item.owned_moss[batch['job_id']] == history.encoded(batch)
    owned = (item.owned_moss[batch['job_id']], invocation_for(token))
    assert env.owner.moss_admissions.setdefault(token, owned) == owned
    return env.value.claim_moss(item.sid, item.rid, token, batch,
        [item.receipt.encoded], links.expected_space(), current_check=current(item),
        admission_capability=env.capability, invocation_id=owned[1])


def test_unadmitted_moss_staging_forecast_refusal_leaves_no_missing_candidate(history_env, monkeypatch):
    env = history_env; primary_item = primary_fixture(env); item, batch = moss_fixture(env)
    forecast = history.moss_staging_bound(batch, (item.receipt.encoded,))
    assert 0 < forecast <= history.PROBE_STAGING_BYTES == 512 * 1024
    before = snapshot(env.store)
    with connection(env.store.path) as db:
        packets = [tuple(row) for row in db.execute(f'SELECT * FROM {provenance.TABLE}')]
        segments = [tuple(row) for row in db.execute('SELECT * FROM segments')]
    assert len(packets) == len(segments) == 1
    monkeypatch.setattr(history, 'PROBE_STAGING_BYTES', forecast - 1)
    with pytest.raises(ReadError, match='history_staging_capacity'):
        claim_moss(env, item, batch)
    assert snapshot(env.store) == before and env.value._frozen == {}
    assert env.owner.invocations == []
    for table in (history.ATTEMPTS, history.OBJECTS, history.CERTIFICATES):
        assert snapshot(env.store)[table] == []
    with connection(env.store.path) as db:
        assert [tuple(row) for row in db.execute(f'SELECT * FROM {provenance.TABLE}')] == packets
        assert [tuple(row) for row in db.execute('SELECT * FROM segments')] == segments
    assert primary_item.path.exists() and item.path.exists()
    assert_accounting(env)


def test_moss_copied_proof_receipt_dedup_edges_and_real_erasure(history_env):
    env = history_env; item, batch = moss_fixture(env)
    for index in (1, 2):
        bare = deepcopy(batch)
        if index == 2:
            # A different admitted result retains its own exact typed proof;
            # only the exact same Receipt object is deduplicated.
            bare, _ = links.capture_candidates_v3(*item.moss_inputs,
                "3" * 32, "2" * 64, links.expected_space())
            item.owned_moss[bare['job_id']] = history.encoded(bare)
        token = claim_moss(env, item, bare, token=f'{index:032x}')
        embedded = deepcopy(bare)
        embedded['nodes'][0]['vector'] = vector(1., 0.)
        env.value.append_moss(token, embedded, invocation_for(token['token']),
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
        assert source['admission_revision'] == source['observation_count'] == source['vector_count'] == 2
    assert_accounting(env)
    with connection(env.store.path) as db:
        db.execute('UPDATE sessions SET deleted=1 WHERE id=?', (item.sid,))
        history.erase_session(db, item.sid)
        for table in TABLES[1:]:assert db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
        assert history.aggregate_usage(db) == (0, 0, 0)


@pytest.mark.parametrize('origin', ['primary', 'moss'])
@pytest.mark.parametrize('refusal', ['capability', 'unowned', 'invocation', 'truthy', 'current'])
def test_claim_requires_exact_registered_owner_admission_and_current_source(history_env, monkeypatch, origin, refusal):
    env = history_env; token = '1' * 32; invocation = invocation_for(token)
    if origin == 'primary':
        item = primary_fixture(env)
        owned, callback = env.owner.primary_admissions, '_primary_admission_matches'
        raw = item.owned_primary
    else:
        item, batch = moss_fixture(env)
        owned, callback = env.owner.moss_admissions, '_moss_admission_matches'
        raw = item.owned_moss[batch['job_id']]
    if refusal != 'unowned':owned[token] = (raw, invocation)
    capability = object() if refusal == 'capability' else env.capability
    if refusal == 'invocation':invocation = 'e' * 32
    if refusal == 'truthy':monkeypatch.setattr(env.owner, callback, lambda *args: 1)
    check = (lambda db: False) if refusal == 'current' else current(item)
    reason = {'capability': 'history_admission_required', 'current': 'history_source_changed'}.get(
        refusal, 'history_admission_unverified')
    before = snapshot(env.store)
    with pytest.raises(ReadError, match=reason):
        if origin == 'primary':
            env.value.claim_primary(item.sid, item.rid, token, item.saved, item.node,
                item.receipt.encoded, current_check=check, space=links.expected_space(),
                admission_capability=capability, invocation_id=invocation)
        else:
            env.value.claim_moss(item.sid, item.rid, token, batch, [item.receipt.encoded],
                links.expected_space(), current_check=check,
                admission_capability=capability, invocation_id=invocation)
    assert snapshot(env.store) == before and env.owner.invocations == []
    assert_accounting(env)


@pytest.mark.parametrize('invocation', ['g' * 32, '1' * 31])
def test_admission_rejects_non_hex_or_wrong_length_invocation_before_any_claim(history_env, invocation):
    env = history_env; item = primary_fixture(env); token = '1' * 32
    env.owner.primary_admissions[token] = (item.owned_primary, invocation)
    before = snapshot(env.store)
    with pytest.raises(ReadError, match='history_invocation_token'):
        env.value.claim_primary(item.sid, item.rid, token, item.saved, item.node,
            item.receipt.encoded, current_check=current(item), space=links.expected_space(),
            admission_capability=env.capability, invocation_id=invocation)
    assert snapshot(env.store) == before and env.owner.invocations == []


@pytest.mark.parametrize('origin', ['primary', 'moss'])
def test_reserved_basis_blocks_comparison_passive_publication_and_retirement(history_env, origin):
    env = history_env
    if origin == 'primary':
        item = primary_fixture(env); reservation = claim(env, item)
    else:
        item, batch = moss_fixture(env); reservation = claim_moss(env, item, batch)
    before = snapshot(env.store)
    assert env.owner.pending is None
    assert env.value.begin_comparison() is None
    with connection(env.store.path) as db:
        saved = history.read_saved_for_refs(db, item.sid, item.rid, [])
    assert saved['history_status'] == 'pending' and saved['annotations'] == []
    with pytest.raises(ReadError, match='history_source_busy'):
        env.value.prepare_retirement(item.sid, item.rid, source_row(env, item)['version'])
    assert snapshot(env.store) == before
    assert_missing_observation(observation_rows(env.store, reservation['token'])[0], 'pending')
    assert_accounting(env)


@pytest.mark.parametrize('origin', ['primary', 'moss'])
@pytest.mark.parametrize('after_commit', [False, True], ids=['write_failure', 'lost_ack'])
def test_exact_failure_diagnostic_survives_unknown_write_and_storage_only_restart(history_env, monkeypatch, origin, after_commit):
    env = history_env
    if origin == 'primary':
        item = primary_fixture(env); reservation = claim(env, item)
    else:
        item, batch = moss_fixture(env); reservation = claim_moss(env, item, batch)
    admitted = attempt_row(env.store, reservation['token'])
    pending = observation_rows(env.store, reservation['token'])[0]
    evidence = snapshot(env.store); diagnostic = failure_bytes()
    finish = getattr(env.value, 'finish_' + origin)
    with database_write_fault(monkeypatch, env.value, after_commit=after_commit,
                              reason='synthetic_exact_diagnostic_unknown'):
        with pytest.raises(ReadError, match='synthetic_exact_diagnostic_unknown'):
            finish(reservation, 'probe_failed', failure_json=diagnostic)
    uncertain = snapshot(env.store)
    with pytest.raises(ReadError, match='history_terminal_conflict'):
        finish(reservation, 'probe_failed', failure_json=failure_bytes('cleanup_unverified'))
    assert snapshot(env.store) == uncertain
    if not after_commit:assert uncertain == evidence
    restart_history(env)
    resolve = getattr(env.value, 'resolve_' + origin)
    result = resolve(reservation['token'], reservation['identity'], failure_json=diagnostic)
    assert result['found'] and result['resolved'] and not result['should_probe']
    terminal = attempt_row(env.store, reservation['token'])
    assert terminal['state'] == 'failure'
    assert terminal['terminal'] == diagnostic
    assert terminal['terminal_sha256'] == hashlib.sha256(diagnostic).hexdigest()
    assert terminal['basis'] == admitted['basis'] and terminal['charge'] == admitted['charge']
    assert terminal['reserved_bytes'] == terminal['staging_bytes'] == 0
    missing = observation_rows(env.store, reservation['token'])[0]
    assert_missing_observation(missing, 'candidate_missing')
    for field in ('observation_id', 'header', 'invocation_id'):
        assert missing[field] == pending[field]
    closed = snapshot(env.store)
    assert resolve(reservation['token'], reservation['identity'])['state'] == 'failure'
    assert resolve(reservation['token'], reservation['identity'], failure_json=diagnostic)['state'] == 'failure'
    with pytest.raises(ReadError, match='history_terminal_conflict'):
        resolve(reservation['token'], reservation['identity'], failure_json=failure_bytes('cleanup_unverified'))
    assert snapshot(env.store) == closed and env.owner.invocations == []
    for table in (history.OBJECTS, history.REFS, history.DEPENDENCIES, history.SOURCES):
        assert closed[table] == evidence[table]
    scope = scope_row(env.store, item.rid)
    assert (scope['history_revision'], scope['observation_count'], scope['vector_count']) == (2, 1, 0)
    assert_accounting(env)


@pytest.mark.parametrize('origin', ['primary', 'moss'])
def test_claim_lost_ack_has_complete_retained_basis_before_restart_closure(history_env, monkeypatch, origin):
    env = history_env; token = '1' * 32
    if origin == 'primary':item = primary_fixture(env)
    else:item, batch = moss_fixture(env)
    with database_write_fault(monkeypatch, env.value, after_commit=True, reason='synthetic_claim_lost_ack'):
        with pytest.raises(ReadError, match='synthetic_claim_lost_ack'):
            if origin == 'primary':claim(env, item, token=token)
            else:claim_moss(env, item, batch, token=token)
    admitted = attempt_row(env.store, token)
    assert admitted['state'] == 'reserved' and admitted['basis'] is not None
    assert admitted['invocation_id'] == invocation_for(token)
    assert_missing_observation(observation_rows(env.store, token)[0], 'pending')
    basis_evidence = snapshot(env.store)
    assert_accounting(env)
    restart_history(env)
    result = getattr(env.value, 'resolve_' + origin)(token, admitted['identity_sha256'])
    assert result['resolved'] and result['state'] == 'failure' and not result['should_probe']
    final = attempt_row(env.store, token)
    assert final['basis'] == admitted['basis']
    assert json.loads(final['terminal'])['reason'] == 'storage_unknown'
    assert_missing_observation(observation_rows(env.store, token)[0], 'candidate_missing')
    for table in (history.OBJECTS, history.REFS, history.DEPENDENCIES, history.SOURCES):
        assert snapshot(env.store)[table] == basis_evidence[table]
    before = snapshot(env.store)
    duplicate = claim(env, item, token=token) if origin == 'primary' else claim_moss(env, item, batch, token=token)
    assert not duplicate['should_probe'] and duplicate['state'] == 'failure'
    assert snapshot(env.store) == before and env.owner.invocations == []
    assert_accounting(env)


@pytest.mark.parametrize('origin', ['primary', 'moss'])
@pytest.mark.parametrize('operation', ['finish', 'resolve'])
@pytest.mark.parametrize('invalid', ['noncanonical', 'enum', 'oversized'])
def test_failure_diagnostic_validation_precedes_terminal_mutation(history_env, origin, operation, invalid):
    env = history_env
    if origin == 'primary':item = primary_fixture(env); reservation = claim(env, item)
    else:item, batch = moss_fixture(env); reservation = claim_moss(env, item, batch)
    raw = failure_bytes()
    if invalid == 'noncanonical':raw += b' '
    elif invalid == 'oversized':raw = b' ' * 513
    else:
        document = json.loads(raw); document['reason'] = 'invented_failure'
        raw = history.encoded(document)
    before = snapshot(env.store)
    with pytest.raises(ReadError):
        if operation == 'finish':
            getattr(env.value, 'finish_' + origin)(reservation, 'probe_failed', failure_json=raw)
        else:
            getattr(env.value, 'resolve_' + origin)(reservation['token'], reservation['identity'], failure_json=raw)
    assert snapshot(env.store) == before and env.owner.invocations == []
    assert_missing_observation(observation_rows(env.store, reservation['token'])[0], 'pending')
    assert_accounting(env)


@pytest.mark.parametrize('empty', [False, True], ids=['candidate_missing', 'witness_only'])
def test_failed_moss_retains_authenticated_basis_witnesses_and_can_retire_without_vector(history_env, empty):
    env = history_env; item, batch = moss_fixture(env, empty=empty)
    reservation = claim_moss(env, item, batch)
    admitted = attempt_row(env.store, reservation['token'])
    basis = json.loads(admitted['basis'])
    assert basis['kind'] == 'moss_result' and basis['witness_count'] == len(batch['witnesses']) == 1
    assert (admitted['charge'], admitted['reserved_bytes']) == (2048, 0 if empty else 2048)
    rows = observation_rows(env.store, reservation['token'])
    assert len(rows) == (0 if empty else 1)
    for row in rows:assert_missing_observation(row, 'pending')
    with connection(env.store.path) as db:
        proof = db.execute(f'SELECT body,digest FROM {history.OBJECTS} WHERE kind=\'proof\'').fetchone()
        assert proof['body'] == history.encoded(batch) and proof['digest'] == basis['proof_digest']
        assert db.execute(f'SELECT COUNT(*) FROM {history.REFS}').fetchone()[0] == 2
        assert db.execute(f'SELECT COUNT(*) FROM {history.DEPENDENCIES}').fetchone()[0] == 2
    assert_accounting(env)
    env.value.finish_moss(reservation, 'probe_failed', failure_json=failure_bytes())
    terminal = attempt_row(env.store, reservation['token'])
    assert terminal['basis'] == admitted['basis'] and terminal['witness_count'] == 1
    assert terminal['terminal'] == failure_bytes()
    for row in observation_rows(env.store, reservation['token']):
        assert_missing_observation(row, 'candidate_missing')
    scope = scope_row(env.store, item.rid); source = source_row(env, item)
    assert (scope['history_revision'], scope['witness_count'], scope['vector_count']) == (2, 1, 0)
    assert source['admission_revision'] == 1 and source['vector_count'] == 0
    assert scope['observation_count'] == source['observation_count'] == (0 if empty else 1)
    if empty:assert env.value.begin_comparison() is None
    restart_history(env)
    prepared = prepared_retirement(env, item, observations=0 if empty else 1, vectors=0)
    marker = env.value.commit_retirement(prepared)
    assert marker['acknowledged'] and item.path.exists()
    retained_marker = json.loads(source_row(env, item)['marker'])
    assert retained_marker['observation_count'] == (0 if empty else 1)
    assert retained_marker['vector_count'] == 0 and retained_marker['admission_revision'] == 1
    assert retained_marker['basis_count'] == prepared['basis_count'] == 1
    assert env.owner.invocations == []
    assert_accounting(env)


def test_retirement_census_separates_vector_and_missing_observations(history_env):
    env = history_env; item, batch = moss_fixture(env)
    first = claim_moss(env, item, batch, token='1' * 32)
    embedded = deepcopy(batch); embedded['nodes'][0]['vector'] = vector(1., 0.)
    env.value.append_moss(first, embedded, invocation_for(first['token']),
        success_capability=env.capability, current_check=current(item))
    second_batch, _ = links.capture_candidates_v3(*item.moss_inputs,
        '3' * 32, '2' * 64, links.expected_space())
    item.owned_moss[second_batch['job_id']] = history.encoded(second_batch)
    second = claim_moss(env, item, second_batch, token='2' * 32)
    env.value.finish_moss(second, 'cleanup_unknown', failure_json=failure_bytes('cleanup_unverified'))
    source = source_row(env, item); scope = scope_row(env.store, item.rid)
    assert (source['admission_revision'], source['observation_count'], source['vector_count']) == (2, 2, 1)
    assert (scope['history_revision'], scope['observation_count'], scope['vector_count'], scope['witness_count']) == (4, 2, 1, 2)
    before = snapshot(env.store)
    prepared = prepared_retirement(env, item, observations=2, vectors=1, proofs=4)
    assert prepared['complete']
    marker = env.value.commit_retirement(prepared)
    assert marker['acknowledged'] and item.path.exists()
    document = json.loads(source_row(env, item)['marker'])
    assert (document['observation_count'], document['vector_count']) == (2, 1)
    assert document['basis_count'] == prepared['basis_count'] == 2
    for table in (history.ATTEMPTS, history.CERTIFICATES, history.OBJECTS, history.REFS, history.DEPENDENCIES):
        assert snapshot(env.store)[table] == before[table]
    assert_accounting(env)


def publish_one(env, item):
    from audio_backend.recording_speaker_history_compare import ComparisonBuild
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
    with connection(env.store.path) as db:
        decision = db.execute(f'SELECT observation_count,vector_count FROM {history.DECISIONS} WHERE scope_seq=? AND slot=?',
                              (pending['scope_seq'], pending['slot'])).fetchone()
        assert (decision['observation_count'], decision['vector_count']) == (1, 1)
    return pending


@pytest.mark.parametrize('empty', [False, True], ids=['missing_candidate', 'witness_only'])
def test_late_admission_and_failure_each_advance_revision_and_clear_publication(history_env, empty):
    env = history_env; one = primary_fixture(env); success(env, one); publish_one(env, one)
    published = scope_row(env.store, one.rid)
    assert published['current_slot'] is not None and saved_read(env, one)['history_status'] == 'current'
    two, batch = moss_fixture(env, empty=empty)
    assert two.rid == one.rid and two.cid != one.cid
    reservation = claim_moss(env, two, batch)
    admitted = scope_row(env.store, one.rid)
    assert admitted['history_revision'] == published['history_revision'] + 1
    assert admitted['current_slot'] is None and admitted['vector_count'] == 1
    assert admitted['observation_count'] == (1 if empty else 2)
    assert admitted['witness_count'] == published['witness_count'] + 1
    assert env.value.begin_comparison() is None
    saved = saved_read(env, one)
    assert saved['history_status'] == 'pending' and saved['annotations'] == []
    assert_accounting(env)
    env.value.finish_moss(reservation, 'probe_failed', failure_json=failure_bytes())
    terminal = scope_row(env.store, one.rid)
    assert terminal['history_revision'] == admitted['history_revision'] + 1
    assert terminal['current_slot'] is None
    for field in ('observation_count', 'vector_count', 'witness_count'):
        assert terminal[field] == admitted[field]
    assert_accounting(env)


def test_clean_published_cadence_skip_does_not_rebuild_or_starve_next_opportunity(history_env):
    env = history_env; one = primary_fixture(env); success(env, one); publish_one(env, one)
    published = scope_row(env.store, one.rid); before = snapshot(env.store)
    saved = saved_read(env, one)
    assert published['state'] == 'none' and published['building_slot'] is None
    assert saved['history_status'] == 'current'
    assert env.value.primary_due(one.sid, one.rid, 5 * RATE) == {'due': False, 'reason': 'missed'}
    assert env.value.note_primary_skip(one.sid, one.rid, 5 * RATE, 'missed', links.expected_space())
    skipped = scope_row(env.store, one.rid)
    assert skipped['high_water'] == 5 * RATE and skipped['skipped_count'] == published['skipped_count'] + 1
    assert {k: v for k, v in skipped.items() if k not in ('high_water', 'skipped_count')} == {
        k: v for k, v in published.items() if k not in ('high_water', 'skipped_count')}
    assert env.value.begin_comparison() is None
    assert saved_read(env, one) == saved
    assert env.value.primary_due(one.sid, one.rid, 10 * RATE) == {'due': True, 'reason': 'none'}
    for table in TABLES[1:]:assert snapshot(env.store)[table] == before[table]
    # A real later admission still clears this publication; cadence metadata
    # does not suppress new authenticated evidence or hide its pending rival.
    two, batch = moss_fixture(env)
    reservation = claim_moss(env, two, batch)
    admitted = scope_row(env.store, one.rid)
    assert admitted['history_revision'] == published['history_revision'] + 1
    assert admitted['state'] == 'pending' and admitted['current_slot'] is None
    assert admitted['observation_count'] == 2 and admitted['vector_count'] == 1
    assert saved_read(env, one)['history_status'] == 'pending'
    assert env.value.begin_comparison() is None
    env.value.finish_moss(reservation, 'probe_failed', failure_json=failure_bytes())
    fresh = env.value.begin_comparison()
    assert fresh is not None and fresh['token'].history_revision == published['history_revision'] + 2
    env.value.finish_comparison(fresh, 'cancelled')
    assert_accounting(env)


@pytest.mark.parametrize('state', ['pending', 'invalid'])
def test_cadence_skip_preserves_pending_or_invalid_authority_state(history_env, state):
    env = history_env; one = primary_fixture(env)
    if state == 'pending':claim(env, one)
    else:
        success(env, one); publish_one(env, one)
        with connection(env.store.path) as db:
            db.execute('UPDATE segments SET text=? WHERE id=?', ('changed', one.number))
    original = scope_row(env.store, one.rid); before = snapshot(env.store)
    saved = saved_read(env, one)
    assert saved['history_status'] == state
    assert env.value.note_primary_skip(one.sid, one.rid, 5 * RATE, 'capacity', links.expected_space())
    skipped = scope_row(env.store, one.rid)
    assert skipped['high_water'] == 5 * RATE and skipped['skipped_count'] == original['skipped_count'] + 1
    assert {k: v for k, v in skipped.items() if k not in ('high_water', 'skipped_count')} == {
        k: v for k, v in original.items() if k not in ('high_water', 'skipped_count')}
    assert saved_read(env, one) == saved and env.value.begin_comparison() is None
    for table in TABLES[1:]:assert snapshot(env.store)[table] == before[table]
    assert_accounting(env)


@pytest.mark.parametrize('late', ['resolving_alias', 'pending_rival'])
def test_old_unknown_rival_finish_cannot_latch_newer_admission(history_env, late):
    from audio_backend.recording_speaker_history_compare import ComparisonBuild
    env = history_env
    if late == 'resolving_alias':
        one, batch = moss_fixture(env); first = claim_moss(env, one, batch)
        env.value.finish_moss(first, 'probe_failed', failure_json=failure_bytes())
    else:
        one = primary_fixture(env); first = claim(env, one)
        env.value.finish_primary(first, 'probe_failed', failure_json=failure_bytes())

    def compute(pending):
        sources = env.value.active_source_page(pending)
        assert sources['complete'] and len(sources['rows']) <= 2
        env.value.revalidate_active_sources(pending, sources)
        build = ComparisonBuild(pending['token'], admitted_bytes=history.COMPARE_BYTES, clock=lambda: 0.)
        observations = env.value.observation_page(pending)
        expected = pending['token'].observation_count
        assert observations['complete'] and len(observations['rows']) == expected
        assert observations['next_after'] == expected and expected in (1, 2)
        build.feed_observations(pending['token'], 0, observations['rows'], complete=True)
        # Each of these one-node admissions retains exactly one witness basis.
        # The reader intentionally stops a page at that basis boundary.
        assert pending['token'].witness_count == expected
        cursor = 0
        for _ in range(2):
            witnesses = env.value.witness_page(pending, after=cursor)
            assert len(witnesses['rows']) == 1 and witnesses['next_after'] == cursor + 1 <= expected
            assert witnesses['complete'] == (witnesses['next_after'] == expected)
            build.feed_witnesses(pending['token'], cursor, witnesses['rows'], complete=witnesses['complete'])
            cursor = witnesses['next_after']
            if witnesses['complete']:break
        assert cursor == expected
        for _ in range(32):
            result = build.step()
            if result['state'] not in ('loading', 'pending'):break
        return build, result

    old = env.value.begin_comparison(); old_build, result = compute(old)
    assert result['state'] == 'refused' and result['reason'] == 'unknown_rival'
    retained_first = observation_rows(env.store, first['token'])
    # The old terminal result exists, but its durable finish has not occurred.
    # Admit through the real registered owner rather than mutating evidence SQL.
    if late == 'resolving_alias':
        second_batch, _ = links.capture_candidates_v3(*one.moss_inputs,
            '3' * 32, '2' * 64, links.expected_space())
        one.owned_moss[second_batch['job_id']] = history.encoded(second_batch)
        second = claim_moss(env, one, second_batch, token='2' * 32)
        embedded = deepcopy(second_batch); embedded['nodes'][0]['vector'] = vector(1., 0.)
        env.value.append_moss(second, embedded, invocation_for(second['token']),
            success_capability=env.capability, current_check=current(one))
    else:
        two, second_batch = moss_fixture(env)
        second = claim_moss(env, two, second_batch, token='2' * 32)
    newer = scope_row(env.store, one.rid)
    assert newer['history_revision'] > old['token'].history_revision and newer['state'] == 'pending'
    assert newer['current_slot'] is None and newer['observation_count'] == 2
    assert env.value.finish_comparison(old, 'unknown_rival') is True
    cleared = scope_row(env.store, one.rid)
    assert cleared['state'] == 'pending' and cleared['building_slot'] is None
    assert cleared['history_revision'] == newer['history_revision']
    assert observation_rows(env.store, first['token']) == retained_first
    assert len(observation_rows(env.store, second['token'])) == 1
    old_build.discard()
    assert_accounting(env)
    if late == 'pending_rival':
        assert env.value.begin_comparison() is None
        assert saved_read(env, one)['history_status'] == 'pending'
        env.value.finish_moss(second, 'probe_failed', failure_json=failure_bytes())
    fresh = env.value.begin_comparison(); assert fresh is not None
    build, result = compute(fresh)
    assert fresh['token'].observation_count == 2
    if late == 'resolving_alias':
        assert result['state'] == 'ready'
        output = build.output_page(); assert output['complete']
        env.value.write_decision_page(fresh, output)
        assert env.value.publish_comparison(fresh, build.decision_counts) == {'saved': True}
        assert scope_row(env.store, one.rid)['state'] == 'none'
    else:
        assert result['state'] == 'refused' and result['reason'] == 'unknown_rival'
        assert env.value.finish_comparison(fresh, 'unknown_rival') is True
        assert scope_row(env.store, one.rid)['state'] == 'unknown_rival'
    assert observation_rows(env.store, first['token']) == retained_first
    assert_accounting(env)


def test_unknown_rival_latch_survives_primary_skip_until_actual_new_admission(history_env):
    from audio_backend.recording_speaker_history_compare import ComparisonBuild, Pending
    env = history_env; one = primary_fixture(env); reservation = claim(env, one)
    env.value.finish_primary(reservation, 'probe_failed', failure_json=failure_bytes())
    missing = observation_rows(env.store, reservation['token'])
    assert len(missing) == 1
    assert_missing_observation(missing[0], 'candidate_missing')
    pending = env.value.begin_comparison()
    assert pending is not None
    sources = env.value.active_source_page(pending)
    assert sources['complete'] and len(sources['rows']) == 1
    env.value.revalidate_active_sources(pending, sources)
    build = ComparisonBuild(pending['token'], admitted_bytes=history.COMPARE_BYTES, clock=lambda: 0.)
    observations = env.value.observation_page(pending); witnesses = env.value.witness_page(pending)
    assert observations['complete'] and len(observations['rows']) == 1
    assert witnesses['complete'] and len(witnesses['rows']) <= 1
    build.feed_observations(pending['token'], 0, observations['rows'], complete=True)
    build.feed_witnesses(pending['token'], 0, witnesses['rows'], complete=True)
    for _ in range(32):
        result = build.step()
        if result['state'] not in ('loading', 'pending'):break
    assert result['state'] == 'refused' and result['reason'] == 'unknown_rival'
    with pytest.raises(Pending, match='unknown_rival'):build.output_page()
    assert env.value.finish_comparison(pending, result['reason']) is True
    latched = scope_row(env.store, one.rid)
    assert latched['state'] == 'unknown_rival' and latched['current_slot'] is None
    assert env.value.begin_comparison() is None
    next_frame = latched['high_water'] + FRAMES
    assert env.value.note_primary_skip(one.sid, one.rid, next_frame, 'capacity', links.expected_space()) is True
    skipped = scope_row(env.store, one.rid)
    assert skipped['high_water'] == next_frame and skipped['skipped_count'] == latched['skipped_count'] + 1
    for field in ('state', 'history_revision', 'authority_revision', 'authority_blocked',
                  'observation_count', 'vector_count', 'witness_count', 'current_slot'):
        assert skipped[field] == latched[field]
    assert env.value.begin_comparison() is None
    saved = saved_read(env, one)
    assert saved['history_status'] == 'abstained' and saved['reason'] == 'unknown_rival'
    assert saved['annotations'] == []
    two, batch = moss_fixture(env)
    assert two.rid == one.rid
    fresh = claim_moss(env, two, batch)
    assert fresh['should_probe'] is True
    admitted = scope_row(env.store, one.rid)
    assert admitted['state'] == 'pending' and admitted['current_slot'] is None
    assert admitted['history_revision'] == latched['history_revision'] + 1
    assert admitted['authority_revision'] == latched['authority_revision']
    assert admitted['observation_count'] == 2 and admitted['vector_count'] == 0
    assert observation_rows(env.store, reservation['token']) == missing
    assert env.owner.invocations == []
    assert_accounting(env)


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


def source_row(env, item):
    with connection(env.store.path) as db:
        rows = db.execute(f'SELECT * FROM {history.SOURCES} WHERE chunk_id=?', (item.cid,)).fetchall()
        assert len(rows) == 1
        return dict(rows[0])


def saved_read(env, item):
    with connection(env.store.path) as db:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        return history.read_saved_for_refs(db, item.sid, item.rid,
                                          [('primary', item.node['primary_source_ref'])])


def prepared_retirement(env, item, *, observations=1, vectors=1, proofs=2):
    row = source_row(env, item)
    prepared = env.value.prepare_retirement(item.sid, item.rid, row['version'])
    for _ in range(32):
        page = env.value.retirement_page(prepared)
        if page['complete']:
            break
    assert page['complete'] and prepared['complete']
    assert prepared['observation_count'] == observations and prepared['vector_count'] == vectors
    assert prepared['proof_count'] == proofs
    return prepared


def cleanup_retirement(env, marker, delete_file):
    from audio_backend.lifecycle import cleanup_verified_retirement
    return cleanup_verified_retirement(env.store, env.value, marker['scope_seq'],
                                      marker['version'], marker['token'], delete_file=delete_file)


def restart_history(env):
    previous = env.value.owner
    env.value = history.HistoryStore(env.store, clock=lambda: 0.)
    env.capability = env.value.bind_success_owner(env.owner)
    assert env.value.owner != previous


@contextmanager
def database_write_fault(monkeypatch, target, *, after_commit, reason):
    """Only the selected finite fixture's writes fail or lose their response."""
    original = target._database
    @contextmanager
    def fault(*args, write=False, **kwargs):
        if write and not after_commit:
            raise ReadError(503, reason)
        with original(*args, write=write, **kwargs) as db:
            yield db
        if write and after_commit:
            raise ReadError(503, reason)
    with monkeypatch.context() as patch:
        patch.setattr(target, '_database', fault)
        yield


def ready_projection(env):
    from audio_backend.recording_speaker_history_compare import ComparisonBuild
    pending = env.value.begin_comparison()
    assert pending is not None
    sources = env.value.active_source_page(pending)
    assert sources['complete'] and len(sources['rows']) == 1
    env.value.revalidate_active_sources(pending, sources)
    build = ComparisonBuild(pending['token'], admitted_bytes=history.COMPARE_BYTES, clock=lambda: 0.)
    observations = env.value.observation_page(pending)
    witnesses = env.value.witness_page(pending)
    assert observations['complete'] and len(observations['rows']) == 1
    assert witnesses['complete'] and len(witnesses['rows']) <= 1
    build.feed_observations(pending['token'], 0, observations['rows'], complete=True)
    build.feed_witnesses(pending['token'], 0, witnesses['rows'], complete=True)
    for _ in range(32):
        result = build.step()
        if result['state'] not in ('loading', 'pending'):
            break
    assert result['state'] == 'ready'
    output = build.output_page()
    assert output['complete'] and len(output['rows']) <= 2
    return pending, build, output


def test_retirement_marker_precedes_unlink_and_retired_restart_read_is_passive(history_env, monkeypatch):
    env = history_env; item = primary_fixture(env); success(env, item); publish_one(env, item)
    prepared = prepared_retirement(env, item)
    assert item.path.exists() and source_row(env, item)['marker'] is None
    marker = env.value.commit_retirement(prepared)
    assert marker['acknowledged'] and item.path.exists()
    committed = source_row(env, item)
    assert committed['state'] == 'retired' and committed['cleanup'] == 'pending'
    assert len(committed['marker']) <= 1024
    events = []
    def unlink_after_marker(raw):
        # Independent connection proves commit visibility before the first unlink.
        current_source = source_row(env, item)
        assert current_source['marker'] == committed['marker']
        assert current_source['marker_token'] == marker['token']
        assert current_source['state'] == 'retired' and current_source['cleanup'] == 'pending'
        assert Path(raw) == item.path
        events.append('marker_visible'); Path(raw).unlink(); events.append('unlinked')
    assert cleanup_retirement(env, marker, unlink_after_marker) == {'cleanup': 'done'}
    assert events == ['marker_visible', 'unlinked'] and not item.path.exists()
    cleaned = source_row(env, item)
    assert cleaned['cleanup'] == 'done' and cleaned['cleanup_attempts'] == 1
    assert cleaned['marker'] == committed['marker']
    assert_accounting(env)
    restart_history(env)
    monkeypatch.setattr(history, 'BOOT_ID', 'e' * 32)
    env.store.settings.recording_primary_observation_policy = 'off'
    env.store.settings.recording_speaker_links_enabled = False
    assert not Path(env.store.settings.recording_speaker_model_path).exists()
    def forbidden(*args, **kwargs):pytest.fail('Retired passive read attempted active work')
    monkeypatch.setattr(env.value, 'begin_comparison', forbidden)
    monkeypatch.setattr(env.value, 'revalidate_active_sources', forbidden)
    monkeypatch.setattr(env.owner.cell_source.owner.authority, 'receipt_valid', forbidden)
    monkeypatch.setattr(primary, 'standalone_current', forbidden)
    before = snapshot(env.store)
    saved = saved_read(env, item)
    assert saved['history_status'] == 'current' and saved['complete']
    assert len(saved['annotations']) == 1 and saved['annotations'][0]['speaker_identity_valid'] is False
    assert saved['validation_semantics'] == 'validated_at_admission_source_version_verified_retired'
    assert snapshot(env.store) == before


def test_absent_unmarked_source_cannot_acquire_retirement_authority(history_env):
    env = history_env; item = primary_fixture(env); success(env, item)
    source = source_row(env, item); item.path.unlink(); before = snapshot(env.store)
    with pytest.raises(ReadError, match='history_source_unknown'):
        env.value.prepare_retirement(item.sid, item.rid, source['version'])
    calls = []
    with pytest.raises(ReadError, match='history_retirement_missing'):
        cleanup_retirement(env, dict(scope_seq=source['scope_seq'], version=source['version'], token='d' * 32), calls.append)
    assert calls == [] and snapshot(env.store) == before
    assert source_row(env, item)['state'] == 'active'
    assert source_row(env, item)['marker'] is None
    assert_accounting(env)


@pytest.mark.parametrize('after_commit', [False, True], ids=['write_failure', 'lost_ack'])
def test_retirement_write_uncertainty_never_unlinks_without_later_exact_marker(history_env, monkeypatch, after_commit):
    env = history_env; item = primary_fixture(env); success(env, item)
    prepared = prepared_retirement(env, item); before = snapshot(env.store); calls = []
    with database_write_fault(monkeypatch, env.value, after_commit=after_commit, reason='synthetic_marker_write_unknown'):
        with pytest.raises(ReadError, match='synthetic_marker_write_unknown'):
            acknowledged = env.value.commit_retirement(prepared)
            cleanup_retirement(env, acknowledged, calls.append)
    assert calls == [] and item.path.exists()
    marker = dict(scope_seq=prepared['scope_seq'], version=prepared['source']['version'], token=prepared['token'])
    restart_history(env)
    if after_commit:
        exact = env.value.read_retirement(marker['scope_seq'], marker['version'], marker['token'])
        assert exact['state'] == 'retired' and exact['cleanup'] == 'pending'
        assert json.loads(exact['marker'])['token'] == marker['token']
        with pytest.raises(ReadError, match='history_retirement_missing'):
            env.value.read_retirement(marker['scope_seq'], marker['version'], 'd' * 32)
        def unlink(raw):
            calls.append(raw); assert Path(raw) == item.path; Path(raw).unlink()
        assert cleanup_retirement(env, marker, unlink) == {'cleanup': 'done'}
        assert calls == [str(item.path)] and not item.path.exists()
    else:
        with pytest.raises(ReadError, match='history_retirement_missing'):
            cleanup_retirement(env, marker, calls.append)
        assert calls == [] and item.path.exists() and snapshot(env.store) == before
    assert_accounting(env)


def test_retirement_lost_ack_exact_same_process_retry_releases_owner_for_second_source(history_env, monkeypatch):
    env = history_env; one = primary_fixture(env); two = primary_fixture(env, number=2)
    success(env, one); success(env, two)
    prepared = prepared_retirement(env, one); owner = env.value.owner
    with database_write_fault(monkeypatch, env.value, after_commit=True,
                              reason='synthetic_same_process_retirement_lost_ack'):
        with pytest.raises(ReadError, match='synthetic_same_process_retirement_lost_ack'):
            env.value.commit_retirement(prepared)
    assert env.value.owner == owner and env.value._retirement is prepared
    committed = source_row(env, one)
    assert committed['state'] == 'retired' and committed['marker_token'] == prepared['token']
    assert committed['cleanup'] == 'pending' and one.path.exists() and two.path.exists()
    durable = snapshot(env.store)
    retried = env.value.commit_retirement(prepared)
    assert retried['acknowledged'] is True and retried['token'] == prepared['token']
    assert env.value._retirement is None and env.value.owner == owner
    assert snapshot(env.store) == durable and source_row(env, one)['marker'] == committed['marker']
    second = prepared_retirement(env, two)
    assert second is env.value._retirement and second is not prepared
    assert second['complete'] and second['scope_seq'] != prepared['scope_seq']
    assert second['source']['version'] == source_row(env, two)['version']
    assert source_row(env, two)['state'] == 'active' and source_row(env, two)['marker'] is None
    assert snapshot(env.store) == durable
    assert_accounting(env)


@pytest.mark.parametrize('failure', ['unlink_io', 'after_unlink'])
def test_retirement_cleanup_failure_and_post_unlink_restart_reconcile(history_env, monkeypatch, failure):
    env = history_env; item = primary_fixture(env); success(env, item)
    marker = env.value.commit_retirement(prepared_retirement(env, item))
    original_marker = source_row(env, item)['marker']; calls = []
    if failure == 'unlink_io':
        def fail_unlink(raw):
            calls.append(raw); raise OSError('synthetic owned unlink failure')
        assert cleanup_retirement(env, marker, fail_unlink) == {'cleanup': 'failed'}
        failed = source_row(env, item)
        assert failed['cleanup'] == 'failed' and failed['cleanup_reason'] == 'io_unavailable'
        assert failed['cleanup_attempts'] == 1 and item.path.exists()
    else:
        def fail_cleanup_record(*args, **kwargs):
            raise ReadError(503, 'synthetic_post_unlink_crash')
        def unlink(raw):
            calls.append(raw); Path(raw).unlink()
        with monkeypatch.context() as patch:
            patch.setattr(env.value, 'record_retirement_cleanup', fail_cleanup_record)
            with pytest.raises(ReadError, match='synthetic_post_unlink_crash'):
                cleanup_retirement(env, marker, unlink)
        failed = source_row(env, item)
        assert failed['cleanup'] == 'pending' and failed['cleanup_attempts'] == 0
        assert not item.path.exists()
    restart_history(env)
    def recovered_unlink(raw):
        calls.append(raw); assert Path(raw) == item.path; Path(raw).unlink()
    assert cleanup_retirement(env, marker, recovered_unlink) == {'cleanup': 'done'}
    assert len(calls) == (2 if failure == 'unlink_io' else 1)
    final = source_row(env, item)
    assert final['cleanup'] == 'done' and final['cleanup_reason'] is None
    assert final['cleanup_attempts'] == (2 if failure == 'unlink_io' else 1)
    assert final['marker'] == original_marker and not item.path.exists()
    assert_accounting(env)


def test_retirement_replacement_path_is_never_deleted_and_revokes_authority(history_env):
    env = history_env; item = primary_fixture(env); success(env, item); publish_one(env, item)
    marker = env.value.commit_retirement(prepared_retirement(env, item))
    replacement = source_fixture(env, number=2, pcm=b'\1\0' * FRAMES)
    replacement_bytes = replacement.path.read_bytes()
    replacement.path.replace(item.path)
    calls = []
    assert cleanup_retirement(env, marker, calls.append) == {'cleanup': 'failed', 'reason': 'path_changed'}
    assert calls == [] and item.path.read_bytes() == replacement_bytes
    assert source_row(env, item)['state'] == 'revoked'
    assert scope_row(env.store, item.rid)['authority_blocked'] == 'source_changed'
    saved = saved_read(env, item)
    assert saved['history_status'] == 'invalid' and saved['annotations'] == []
    assert saved['reason'] == 'source_changed'
    assert_accounting(env)


@pytest.mark.parametrize('stage', ['prepare', 'page', 'commit', 'cleanup'])
def test_pending_owner_blocks_retirement_and_unlink(history_env, stage):
    env = history_env; item = primary_fixture(env); success(env, item)
    source = source_row(env, item); prepared = None; marker = None
    if stage == 'page':
        prepared = env.value.prepare_retirement(item.sid, item.rid, source['version'])
    elif stage in ('commit', 'cleanup'):
        prepared = prepared_retirement(env, item)
        if stage == 'cleanup':marker = env.value.commit_retirement(prepared)
    before = snapshot(env.store); env.owner.pending = object(); calls = []
    if stage == 'cleanup':
        assert cleanup_retirement(env, marker, calls.append) == {'cleanup': 'pending'}
    else:
        with pytest.raises(ReadError, match='history_source_busy'):
            if stage == 'prepare':env.value.prepare_retirement(item.sid, item.rid, source['version'])
            elif stage == 'page':env.value.retirement_page(prepared)
            else:env.value.commit_retirement(prepared)
    assert calls == [] and item.path.exists() and snapshot(env.store) == before
    assert_accounting(env)


def test_retirement_final_commit_rechecks_new_path_alias(history_env):
    env = history_env; item = primary_fixture(env); success(env, item)
    prepared = prepared_retirement(env, item)
    alias = source_fixture(env, number=2)
    with connection(env.store.path) as db:
        db.execute('UPDATE chunks SET path=? WHERE id=?', (str(item.path), alias.cid))
    before = snapshot(env.store)
    with pytest.raises(ReadError, match='history_active_source_alias'):
        env.value.commit_retirement(prepared)
    assert item.path.exists() and alias.path.exists() and snapshot(env.store) == before
    assert source_row(env, item)['state'] == 'active' and source_row(env, item)['marker'] is None
    assert_accounting(env)


def test_acknowledged_source_invalidation_keeps_unrelated_scope_readable(history_env):
    env = history_env; one = primary_fixture(env); two = primary_fixture(env, number=2)
    success(env, one); success(env, two); publish_one(env, one); publish_one(env, two)
    other_scope = scope_row(env.store, two.rid); other_read = saved_read(env, two)
    assert other_read['history_status'] == 'current'
    assert history.notify_source_mismatch(env.store, [one.cid]) is True
    invalid = saved_read(env, one)
    assert invalid['history_status'] == 'invalid' and invalid['reason'] == 'source_changed'
    assert invalid['annotations'] == [] and source_row(env, one)['state'] == 'revoked'
    assert scope_row(env.store, two.rid) == other_scope and saved_read(env, two) == other_read
    assert_accounting(env)


@pytest.mark.parametrize('after_commit', [False, True], ids=['write_failure', 'lost_ack'])
def test_distinct_unknown_source_notifications_keep_fence_until_each_exact_ack(history_env, monkeypatch, after_commit):
    env = history_env; one = primary_fixture(env); two = primary_fixture(env, number=2)
    success(env, one); success(env, two); publish_one(env, one); publish_one(env, two)
    before = snapshot(env.store)
    with database_write_fault(monkeypatch, history.MossResultStore, after_commit=after_commit, reason='synthetic_notify_unknown'):
        assert history.notify_source_mismatch(env.store, [one.cid]) is False
        assert history.notify_source_mismatch(env.store, [two.cid]) is False
    if not after_commit:assert snapshot(env.store) == before
    for item in (one, two):
        saved = saved_read(env, item)
        assert saved['history_status'] == 'pending' and saved['reason'] == 'source_authority_unknown'
        assert saved['annotations'] == []
    assert history.notify_source_mismatch(env.store, [one.cid]) is True
    for item in (one, two):
        saved = saved_read(env, item)
        assert saved['history_status'] == 'pending' and saved['reason'] == 'source_authority_unknown'
    assert history.notify_source_mismatch(env.store, [two.cid]) is True
    for item in (one, two):
        saved = saved_read(env, item)
        assert saved['history_status'] == 'invalid' and saved['reason'] == 'source_changed'
        assert saved['annotations'] == [] and source_row(env, item)['state'] == 'revoked'
    assert_accounting(env)


def test_older_notification_ack_cannot_clear_newer_same_source_unknown(history_env, monkeypatch):
    env = history_env; one = primary_fixture(env); two = primary_fixture(env, number=2)
    success(env, one); success(env, two); publish_one(env, one); publish_one(env, two)
    original = history.MossResultStore._database
    path = str(env.store.path.resolve()); events = []; owners = []; writes = []
    unrelated = scope_row(env.store, two.rid); unrelated_read = saved_read(env, two)
    assert unrelated_read['history_status'] == 'current'
    @contextmanager
    def interleaved_database(instance, *, write=False):
        if not write:
            with original(instance, write=False) as db:
                yield db
            return
        writes.append(len(writes) + 1)
        assert len(writes) <= 2
        owners.append(history._AUTHORITY_UNKNOWN_DATABASES[path][one.cid])
        if len(writes) == 2:
            assert owners[1] is not owners[0]
            events.append('newer_registered')
            raise ReadError(503, 'synthetic_newer_notification_failed')
        with original(instance, write=True) as db:
            yield db
        # A is committed but has not reached its positive-ack cleanup. Run B
        # synchronously here, so this race has no threads, sleeps or timing luck.
        events.append('older_committed')
        assert history.notify_source_mismatch(env.store, [one.cid]) is False
        assert history._AUTHORITY_UNKNOWN_DATABASES[path][one.cid] is owners[1]
        events.append('newer_failed')
    with monkeypatch.context() as patch:
        patch.setattr(history.MossResultStore, '_database', interleaved_database)
        assert history.notify_source_mismatch(env.store, [one.cid]) is True
        events.append('older_returned')
    assert writes == [1, 2]
    assert events == ['older_committed', 'newer_registered', 'newer_failed', 'older_returned']
    assert history._AUTHORITY_UNKNOWN_DATABASES[path][one.cid] is owners[1]
    assert history._AUTHORITY_UNKNOWN_BYTES > 0 and not history._AUTHORITY_UNKNOWN_OVERFLOW
    assert source_row(env, one)['state'] == 'revoked'
    assert scope_row(env.store, two.rid) == unrelated
    for item in (one, two):
        saved = saved_read(env, item)
        assert saved['history_status'] == 'pending' and saved['reason'] == 'source_authority_unknown'
        assert saved['annotations'] == []
    # A new, positively acknowledged write covers B's unresolved observation.
    assert history.notify_source_mismatch(env.store, [one.cid]) is True
    assert history._AUTHORITY_UNKNOWN_DATABASES == {} and history._AUTHORITY_UNKNOWN_BYTES == 0
    assert saved_read(env, one)['history_status'] == 'invalid'
    assert saved_read(env, two) == unrelated_read
    assert_accounting(env)


def test_notification_metadata_overflow_preserves_existing_fence_without_growth(history_env, monkeypatch):
    env = history_env; one = primary_fixture(env); two = primary_fixture(env, number=2)
    success(env, one); success(env, two); publish_one(env, one); publish_one(env, two)
    path = str(env.store.path.resolve())
    exact = len(path.encode('utf-8')) + 512 + len(one.cid.encode('utf-8')) + 256
    assert exact < 2048
    # Clamp only this test's metadata envelope after all fixture admissions.
    # This allocates one tiny entry; it never constructs a maximum-sized map.
    with monkeypatch.context() as patch:
        patch.setattr(history, 'CONTROLLER_METADATA_BYTES', exact)
        with database_write_fault(monkeypatch, history.MossResultStore, after_commit=False,
                                  reason='synthetic_notification_capacity_unknown'):
            assert history.notify_source_mismatch(env.store, [one.cid]) is False
        owner = history._AUTHORITY_UNKNOWN_DATABASES[path][one.cid]
        assert history._AUTHORITY_UNKNOWN_BYTES == exact
        assert not history._AUTHORITY_UNKNOWN_OVERFLOW
        assert history.notify_source_mismatch(env.store, [two.cid]) is True
        assert history._AUTHORITY_UNKNOWN_OVERFLOW
        assert history._AUTHORITY_UNKNOWN_BYTES == exact
        assert len(history._AUTHORITY_UNKNOWN_DATABASES) == 1
        assert history._AUTHORITY_UNKNOWN_DATABASES[path] == {one.cid: owner}
        assert source_row(env, one)['state'] == 'active'
        assert source_row(env, two)['state'] == 'revoked'
    # Even with normal admission headroom restored, an ack cannot clear the
    # process-wide overflow latch or pretend that discarded identities are known.
    assert history.notify_source_mismatch(env.store, [one.cid]) is True
    assert history._AUTHORITY_UNKNOWN_DATABASES == {} and history._AUTHORITY_UNKNOWN_BYTES == 0
    assert history._AUTHORITY_UNKNOWN_OVERFLOW
    restart_history(env)
    for item in (one, two):
        saved = saved_read(env, item)
        assert saved['history_status'] == 'pending' and saved['reason'] == 'source_authority_unknown'
        assert saved['annotations'] == []
    assert history._AUTHORITY_UNKNOWN_OVERFLOW
    assert_accounting(env)


def test_partial_projection_restart_replaces_one_reservation_exactly(history_env):
    env = history_env; item = primary_fixture(env); success(env, item)
    baseline = scope_row(env.store, item.rid); evidence = snapshot(env.store)
    pending, build, output = ready_projection(env)
    env.value.write_decision_page(pending, output)
    assert_accounting(env)
    partial = scope_row(env.store, item.rid)
    assert partial['reserved_bytes'] > 0
    assert partial['retained_bytes'] + partial['reserved_bytes'] == baseline['retained_bytes'] + pending['maximum']
    restart_history(env)
    replacement = env.value.begin_comparison()
    assert replacement['slot'] == pending['slot'] and replacement['build_token'] != pending['build_token']
    after = scope_row(env.store, item.rid)
    assert after['retained_bytes'] + after['reserved_bytes'] == baseline['retained_bytes'] + replacement['maximum']
    assert_accounting(env)
    with pytest.raises(ReadError, match='history_snapshot_changed'):
        env.value.publish_comparison(pending, build.decision_counts)
    assert env.value.finish_comparison(replacement, 'storage_unknown') is True
    final = scope_row(env.store, item.rid)
    assert (final['retained_bytes'], final['reserved_bytes'], final['staging_bytes']) == (baseline['retained_bytes'], 0, 0)
    for table in (history.ATTEMPTS, history.CERTIFICATES, history.OBJECTS, history.REFS, history.DEPENDENCIES, history.SOURCES):
        assert snapshot(env.store)[table] == evidence[table]
    assert snapshot(env.store)[history.DECISIONS] == [] and snapshot(env.store)[history.ROWS] == []
    assert_accounting(env)


@pytest.mark.parametrize('stage', ['begin', 'page', 'publish', 'finish'])
def test_projection_lost_write_ack_retains_exact_durable_accounting(history_env, monkeypatch, stage):
    env = history_env; item = primary_fixture(env); success(env, item)
    baseline = scope_row(env.store, item.rid); evidence = snapshot(env.store)
    pending = build = output = None
    if stage != 'begin':
        pending, build, output = ready_projection(env)
        if stage in ('publish', 'finish'):env.value.write_decision_page(pending, output)
    with database_write_fault(monkeypatch, env.value, after_commit=True, reason='synthetic_projection_lost_ack'):
        with pytest.raises(ReadError, match='synthetic_projection_lost_ack'):
            if stage == 'begin':env.value.begin_comparison()
            elif stage == 'page':env.value.write_decision_page(pending, output)
            elif stage == 'publish':env.value.publish_comparison(pending, build.decision_counts)
            else:env.value.finish_comparison(pending, 'storage_unknown')
    assert_accounting(env)
    persisted = scope_row(env.store, item.rid)
    if stage in ('begin', 'page'):
        assert persisted['reserved_bytes'] > 0
        maximum = 66560 + 1920 * persisted['observation_count'] - 384
        assert persisted['retained_bytes'] + persisted['reserved_bytes'] == baseline['retained_bytes'] + maximum
    else:
        assert persisted['reserved_bytes'] == 0 and persisted['staging_bytes'] == 0
    restart_history(env)
    if stage == 'publish':
        before = snapshot(env.store)
        assert env.value.begin_comparison() is None
        assert saved_read(env, item)['history_status'] == 'current'
        assert snapshot(env.store) == before
        assert len(before[history.DECISIONS]) == 1
    else:
        recovered = env.value.begin_comparison()
        assert recovered is not None
        assert_accounting(env)
        assert env.value.finish_comparison(recovered, 'storage_unknown') is True
        final = scope_row(env.store, item.rid)
        assert (final['retained_bytes'], final['reserved_bytes'], final['staging_bytes']) == (baseline['retained_bytes'], 0, 0)
        assert snapshot(env.store)[history.DECISIONS] == [] and snapshot(env.store)[history.ROWS] == []
    for table in (history.ATTEMPTS, history.CERTIFICATES, history.OBJECTS, history.REFS, history.DEPENDENCIES, history.SOURCES):
        assert snapshot(env.store)[table] == evidence[table]
    assert_accounting(env)


def test_success_retry_requires_identical_frozen_vector_and_invocation(history_env):
    env = history_env; item = primary_fixture(env); reservation = success(env, item)
    before = snapshot(env.store)
    with pytest.raises(ReadError, match='history_terminal_conflict'):
        env.value.append_primary(reservation, item.node, vector(0., 1.), invocation_for(reservation['token']),
            success_capability=env.capability, current_check=current(item))
    assert snapshot(env.store) == before
    # A capability-shaped object or a different owner cannot bless saved bytes.
    with pytest.raises(ReadError, match='history_owner_changed'):
        env.value.bind_success_owner(SimpleNamespace())
    assert snapshot(env.store) == before


@pytest.mark.parametrize('terminal_kind', ['candidate_missing', 'vector'])
def test_terminal_evidence_kind_and_identity_never_change_on_conflicting_retry(history_env, terminal_kind):
    env = history_env; item = primary_fixture(env); reservation = claim(env, item)
    if terminal_kind == 'vector':append(env, item, reservation)
    else:env.value.finish_primary(reservation, 'probe_failed', failure_json=failure_bytes())
    before = snapshot(env.store)
    if terminal_kind == 'vector':
        with pytest.raises(ReadError, match='history_terminal_conflict'):
            env.value.finish_primary(reservation, 'probe_failed', failure_json=failure_bytes())
        with pytest.raises(ReadError, match='history_terminal_conflict'):
            env.value.resolve_primary(reservation['token'], reservation['identity'], failure_json=failure_bytes())
        # A newly owned invocation cannot replace this already frozen result.
        raw, _ = env.owner.primary_admissions[reservation['token']]
        env.owner.primary_admissions[reservation['token']] = (raw, 'e' * 32)
        with pytest.raises(ReadError, match='history_terminal_conflict'):
            env.value.append_primary(reservation, item.node, vector(1., 0.), 'e' * 32,
                success_capability=env.capability, current_check=current(item))
    else:
        with pytest.raises(ReadError, match='history_terminal_conflict'):
            append(env, item, reservation)
    assert snapshot(env.store) == before
    row = observation_rows(env.store, reservation['token'])[0]
    replacements = {'observation_id': 'e' * 64, 'invocation_id': 'e' * 32,
        'header': history.encoded({'changed': True}), 'certificate_id': 'e' * 64,
        'vector': struct.pack('<512f', *vector(0., 1.)), 'vector_sha256': 'e' * 64,
        'evidence_kind': 'pending'}
    with connection(env.store.path) as db:
        for field, replacement in replacements.items():
            with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
                db.execute(f'UPDATE {history.CERTIFICATES} SET {field}=? WHERE observation_id=?',
                    (replacement, row['observation_id']))
    assert snapshot(env.store) == before
    assert_accounting(env)


def test_pending_observation_and_retained_basis_identity_are_already_immutable(history_env):
    env = history_env; item = primary_fixture(env); reservation = claim(env, item)
    before = snapshot(env.store)
    with connection(env.store.path) as db:
        for field, value in (('observation_id', 'e' * 64), ('invocation_id', 'e' * 32),
                             ('header', history.encoded({'changed': True}))):
            with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
                db.execute(f'UPDATE {history.CERTIFICATES} SET {field}=? WHERE attempt=?',
                    (value, reservation['token']))
        for field, value in (('basis', history.encoded({'changed': True})), ('invocation_id', 'e' * 32)):
            with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
                db.execute(f'UPDATE {history.ATTEMPTS} SET {field}=? WHERE token=?',
                    (value, reservation['token']))
    assert snapshot(env.store) == before
    assert_accounting(env)


@pytest.mark.parametrize('origin', ['primary', 'moss'])
@pytest.mark.parametrize('state', ['reserved', 'failure', 'success'])
def test_attempt_audit_after_restart_is_exact_read_only_and_never_authorizes_execution(history_env, monkeypatch, origin, state):
    env = history_env
    if origin == 'primary':item = primary_fixture(env); reservation = claim(env, item)
    else:item, batch = moss_fixture(env); reservation = claim_moss(env, item, batch)
    if state == 'failure':
        getattr(env.value, 'finish_' + origin)(reservation, 'probe_failed', failure_json=failure_bytes())
    elif state == 'success':
        if origin == 'primary':append(env, item, reservation)
        else:
            embedded = deepcopy(batch); embedded['nodes'][0]['vector'] = vector(1., 0.)
            env.value.append_moss(reservation, embedded, invocation_for(reservation['token']),
                success_capability=env.capability, current_check=current(item))
    stored = attempt_row(env.store, reservation['token'])
    invocations = list(env.owner.invocations)
    restart_history(env)
    env.store.settings.recording_primary_observation_policy = 'off'
    env.store.settings.recording_speaker_links_enabled = False
    assert not env.value.enabled and env.value._frozen == {}
    before = snapshot(env.store); original = env.value._database
    @contextmanager
    def read_only_database(*, write=False):
        assert write is False
        with original(write=False) as db:
            db.execute('PRAGMA query_only=ON')
            yield db
    def forbidden(*args, **kwargs):pytest.fail('Passive attempt audit attempted active work')
    monkeypatch.setattr(env.value, '_database', read_only_database)
    for name in ('begin_comparison', 'revalidate_active_sources', 'append_primary', 'append_moss',
                 'finish_primary', 'finish_moss', 'resolve_primary', 'resolve_moss'):
        monkeypatch.setattr(env.value, name, forbidden)
    monkeypatch.setattr(env.owner.cell_source.owner.authority, 'receipt_valid', forbidden)
    monkeypatch.setattr(primary, 'standalone_current', forbidden)
    audited = env.value.read_attempt(item.sid, item.rid, reservation['token'])
    assert audited['state'] == state and audited['audit_only'] is True
    assert audited['execution_authorized'] is False
    assert audited['invocation_id'] == stored['invocation_id'] == invocation_for(reservation['token'])
    assert audited['basis_bytes'] == stored['basis'] and audited['basis'] == json.loads(stored['basis'])
    assert audited['terminal_bytes'] == stored['terminal']
    assert audited['terminal_sha256'] == stored['terminal_sha256']
    assert audited['terminal'] == (None if state == 'reserved' else json.loads(stored['terminal']))
    assert audited['failure'] == (json.loads(failure_bytes()) if state == 'failure' else None)
    scope = scope_row(env.store, item.rid)
    for field in ('history_revision', 'authority_revision', 'authority_blocked'):
        assert audited[field] == scope[field]
    assert env.value.read_attempt(item.sid, item.rid, reservation['token']) == audited
    assert snapshot(env.store) == before and env.owner.invocations == invocations
    assert env.value._frozen == {}
    assert_accounting(env)


def test_attempt_audit_binds_live_session_recording_and_exact_token(history_env):
    env = history_env; one = primary_fixture(env)
    two = source_fixture(env, number=2, sid='other-session')
    reservation = claim(env, one)
    env.value.finish_primary(reservation, 'probe_failed', failure_json=failure_bytes())
    restart_history(env); before = snapshot(env.store)
    assert env.value.read_attempt(two.sid, one.rid, reservation['token']) is None
    assert env.value.read_attempt(one.sid, two.rid, reservation['token']) is None
    assert env.value.read_attempt(one.sid, one.rid, 'e' * 32) is None
    with pytest.raises(ReadError, match='session_not_found'):
        env.value.read_attempt('absent-session', one.rid, reservation['token'])
    assert snapshot(env.store) == before and env.owner.invocations == []
    with connection(env.store.path) as db:
        db.execute('UPDATE sessions SET deleted=1 WHERE id=?', (one.sid,))
    tombstoned = snapshot(env.store)
    with pytest.raises(ReadError, match='session_not_found'):
        env.value.read_attempt(one.sid, one.rid, reservation['token'])
    assert snapshot(env.store) == tombstoned


@pytest.mark.parametrize('corruption', ['oversized', 'digest', 'json', 'shape', 'enum'])
def test_attempt_audit_rejects_bounded_fixture_corruption_without_copying_oversized_terminal(history_env, monkeypatch, corruption):
    env = history_env; item = primary_fixture(env); reservation = claim(env, item)
    env.value.finish_primary(reservation, 'probe_failed', failure_json=failure_bytes())
    raw = failure_bytes()
    if corruption == 'oversized':raw = b' ' * 513
    elif corruption == 'json':raw = b'{'
    elif corruption == 'shape':raw = b'[]'
    elif corruption == 'enum':
        document = json.loads(raw); document['reason'] = 'invented_failure'
        raw = history.encoded(document)
    digest = 'e' * 64 if corruption == 'digest' else hashlib.sha256(raw).hexdigest()
    # Fault injection belongs only to this fresh fixture. Restore its exact
    # immutable trigger immediately; the reader must still distrust stored bytes.
    with connection(env.store.path) as db:
        db.execute('DROP TRIGGER recording_history_terminal_immutable_v1')
        db.execute('PRAGMA ignore_check_constraints=ON')
        db.execute(f'UPDATE {history.ATTEMPTS} SET terminal=?,terminal_sha256=? WHERE token=?',
                   (raw, digest, reservation['token']))
        db.execute('PRAGMA ignore_check_constraints=OFF')
        db.execute(history.DDL['recording_history_terminal_immutable_v1'])
    restart_history(env); before = snapshot(env.store)
    if corruption == 'oversized':
        def forbidden(*args, **kwargs):pytest.fail('Oversized terminal copied before scalar preflight')
        monkeypatch.setattr(history, '_decode', forbidden)
        monkeypatch.setattr(history, '_hash', forbidden)
    with pytest.raises(ReadError):
        env.value.read_attempt(item.sid, item.rid, reservation['token'])
    assert snapshot(env.store) == before and env.owner.invocations == []
    assert env.value._frozen == {}


def test_history_first_scope_stays_frozen_to_legacy_reserve_after_policy_off(history_env, monkeypatch):
    env = history_env; item = primary_fixture(env); success(env, item)
    env.store.settings.recording_primary_observation_policy = 'off'
    value = legacy.RecordingSpeakerLinkStore(env.store, clock=lambda: 0.)
    assert value.enabled and value.history is None
    def forbidden(*args, **kwargs):
        pytest.fail('History-first scope reached legacy bound-job lookup')
    monkeypatch.setattr(value, '_bound_job', forbidden)
    before = snapshot(env.store)
    with connection(env.store.path) as db:
        legacy_before = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                         for table in (legacy.SCOPES, legacy.BATCHES)}
        primary_before = [tuple(row) for row in db.execute('SELECT * FROM segments ORDER BY id')]
    # This sparse fixture intentionally has no job tables: the history guard
    # must run inside the real transaction before any bound-job lookup or write.
    with pytest.raises(ReadError, match='speaker_link_history_scope_frozen'):
        value.reserve(item.sid, item.rid, '3' * 32, '4' * 64,
            links.POLICY_SHA256_V3, links.expected_space(), max_nodes=1)
    assert snapshot(env.store) == before
    with connection(env.store.path) as db:
        assert {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                for table in (legacy.SCOPES, legacy.BATCHES)} == legacy_before
        assert [tuple(row) for row in db.execute('SELECT * FROM segments ORDER BY id')] == primary_before
    assert_accounting(env)


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
