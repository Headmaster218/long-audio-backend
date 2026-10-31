"""Prepared, bounded synthetic automatic-ledger contracts; execution is separate.

No model, native runtime, decoder, download, worker or owner thread is started.
Ledger-only rows use canonical synthetic payloads with the plan boundary stubbed;
they are NOT native proof qualification. The plan tests use tiny producer-shaped
synthetic evidence and the real pure proof validators, also not qualification.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from audio_backend import moss_automatic as auto
from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_core_seams as core
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.settings import Settings
from test_native_owned_seam_cells import synthetic_pieces, synthetic_composite


SID = 'synthetic-session'
OTHER = 'other-session'


def automatic_settings(tmp_path, **overrides):
    options = dict(data_dir=tmp_path, start_worker=True, moss_automatic_enabled=True,
        moss_jobs_enabled=True, moss_jobs_model_path=str(tmp_path / 'absent-model.gguf'),
        native_owned_support_enabled=True, native_owned_cells_enabled=True,
        native_owned_core_seams_enabled=True, diarization_backend='nemotron',
        native_diarization_model='synthetic', native_diarization_library='synthetic')
    options.update(overrides)
    return Settings(**options)


def fresh_database():
    """Only the tiny primary tables consumed by these ledger functions."""
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY, deleted INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE chunks(id TEXT PRIMARY KEY, session_id TEXT, status TEXT, metadata TEXT)')
    db.execute('CREATE TABLE recording_work(chunk_id TEXT PRIMARY KEY)')
    db.execute('CREATE TABLE native_core_commits(slot TEXT PRIMARY KEY, state TEXT)')
    db.executemany('INSERT INTO sessions(id) VALUES(?)', [(SID,), (OTHER,)])
    return db


@pytest.fixture
def db():
    value = fresh_database()
    auto.init_schema(value)
    try:
        yield value
    finally:
        value.close()


def ledger_plan(index=1, *, sid=SID, kind='ordinary', proof_bytes=None, binding_bytes=None):
    """Canonical row payload only; never presented to a proof validator."""
    def blob(label, length):
        base = cells.canonical({'fixture': label, 'index': index, 'padding': ''})
        if length is None:
            return base
        assert length >= len(base)
        return cells.canonical({'fixture': label, 'index': index,
            'padding': 'x' * (length - len(base))}, max(length, cells.PROOF_BYTES))
    proof = blob('ledger-only-proof', proof_bytes)
    binding = blob('ledger-only-binding', binding_bytes)
    return dict(key=cells.sha({'sid': sid, 'index': index, 'kind': kind}), sid=sid,
        kind=kind, binding=binding, proof=proof,
        proof_sha256=hashlib.sha256(proof).hexdigest(),
        cell_id=cells.sha({'cell': index, 'sid': sid}),
        slot1=cells.sha({'slot': index, 'side': 1, 'sid': sid}),
        slot2=cells.sha({'slot': index, 'side': 2, 'sid': sid}) if kind != 'ordinary' else None,
        cid1=f'{sid}-chunk-{index}-left',
        cid2=f'{sid}-chunk-{index}-right' if kind != 'ordinary' else None)


def enqueue_plan(db, monkeypatch, plan):
    for slot in (plan['slot1'], plan['slot2']):
        if slot is not None:
            db.execute("INSERT OR IGNORE INTO native_core_commits VALUES(?,'published')", (slot,))
    with monkeypatch.context() as patch:
        patch.setattr(auto, 'plans', lambda *_: [plan])
        auto.enqueue(db, (), {})


class LedgerResults:
    def __init__(self, db):
        self.db = db

    @contextmanager
    def _database(self, *, write=False):
        yield self.db


def publish(db, plan):
    manager = auto.Automatic(SimpleNamespace(settings=SimpleNamespace()), LedgerResults(db))
    manager.note(plan['key'], 'awaiting_admission', published=True)


def identity(plan, index=1):
    return SimpleNamespace(session_id=plan['sid'], job_id=f'{index:032x}',
        original_jobs_epoch='e' * 32, canonical_request_sha256=f'{index:064x}')


def row(db, plan):
    return dict(db.execute(f'SELECT * FROM {auto.TABLE} WHERE candidate_key=?', (plan['key'],)).fetchone())


def counters(db, sid=SID):
    return [tuple(value) for value in auto.usage(db, sid)]


def seed_usage(db, total, own=(0, 0, 0, 0)):
    """Seed scalar accounting boundaries without allocating thousands of rows."""
    db.execute(f'UPDATE {auto.USAGE} SET rows=?,charge=?,pending=?,proofs=? WHERE scope=?', (*total, ''))
    db.execute(f'INSERT OR REPLACE INTO {auto.USAGE} VALUES(?,?,?,?,?)', (SID, *own))


def plan_fixture():
    """Three 16-frame ordinary cells and one 96-frame same-epoch seam."""
    left, right = synthetic_pieces(frames=48, sid=SID)
    commits = []
    for piece in (left, right):
        evidence = piece.commit['evidence']
        commits.append(dict(scope=dict(session_id=SID, kind='stream', key='stream',
            origin=float(0).hex(), run='f' * 32, reset=1, epoch=piece.scope['epoch_id'], index=0),
            part=asdict(piece.part), native_sha256=cells.sha(evidence['native']),
            cut_sha256=cells.sha(evidence['core_cut']), evidence_sha256=cells.sha(evidence),
            runtime_sha256=cells.sha(piece.witness['native']['provenance'])))
    full_pcm = left.pcm + right.pcm
    witness = right.witness
    query = dict(query_start_frame=witness['query_start_frame'],
        query_end_frame=witness['query_end_frame'],
        query_pcm16le_sha256=witness['analyzer_pcm16le_sha256'],
        support_pcm16le_sha256=hashlib.sha256(full_pcm).hexdigest(),
        native=deepcopy(witness['native']), turns=deepcopy(witness['turns']))
    seam = core.make_cell(commits, query, right.commit['evidence'], full_pcm)
    ordinary = []
    evidence = right.commit['evidence']
    native = deepcopy(witness['native'])
    for index in range(3):
        start, end = 48 + 16 * index, 64 + 16 * index
        part = replace(right.part, source_start=start - 48, source_end=end - 48,
                       logical_start=start, logical_end=end)
        contents = [part.content()]
        pcm = b'\1\0' * 16
        support = cells.SourceSupport(
            cells.sha({'schema': 'immutable-source-cell-generation-v1', 'sources': contents}),
            cells.sha({'schema': 'bounded-cell-mapping-v1', 'parts': contents}),
            cells.sha(native['provenance']),
            cells.sha({'start': 48, 'end': 96, 'commit': cells.sha(evidence), 'native': native}),
            96, 48, 96, start, end, hashlib.sha256(pcm).hexdigest(), cells.float32_digest(pcm))
        _, turns = cells.selection(tuple(cells.Turn(*t['original'], t['label'])
            for t in witness['turns']), start, end)
        proof = cells.canonical(dict(policy=cells.POLICY_SHA256, support=support.to_dict(),
            dependencies=[asdict(part)], native=native, turns=turns, selected=True,
            commit_evidence=evidence, commit=dict(chunk_id='right', start=0, end=48,
                sha256=cells.sha(evidence))))
        ordinary.append(cells.Cell(support, (part,), proof, pcm, SID, 'right', 0, 48, cells.sha(evidence)))
    return tuple(ordinary), {'commit': commits[1], 'proof': seam.proof}, commits


def test_settings_default_off_and_complete_prerequisites(tmp_path):
    assert Settings(data_dir=tmp_path).moss_automatic_enabled is False
    assert auto.enabled(SimpleNamespace()) is False
    assert automatic_settings(tmp_path).moss_automatic_enabled is True
    assert not (tmp_path / 'absent-model.gguf').exists()


@pytest.mark.parametrize('value', [0, 1, 'yes', None])
def test_settings_reject_nonboolean_enablement(tmp_path, value):
    with pytest.raises(ValueError, match='moss_automatic_enabled must be boolean'):
        automatic_settings(tmp_path, moss_automatic_enabled=value)


@pytest.mark.parametrize('flag', ['moss_jobs_enabled', 'native_owned_support_enabled',
    'native_owned_cells_enabled', 'native_owned_core_seams_enabled'])
def test_settings_require_every_automatic_dependency(tmp_path, flag):
    with pytest.raises(ValueError, match='moss_automatic_enabled requires'):
        automatic_settings(tmp_path, **{flag: False})


def test_settings_reject_legacy_seam_cells(tmp_path):
    with pytest.raises(ValueError, match='excludes legacy native-owned seam cells'):
        automatic_settings(tmp_path, native_owned_seam_cells_enabled=True)


def test_init_schema_is_empty_and_does_not_backfill_historical_metadata():
    value = fresh_database()
    try:
        metadata = json.dumps({'native_owned_cells': {'selected': True}, 'native_core_seam': {'selected': True}})
        value.execute('INSERT INTO chunks VALUES(?,?,?,?)', ('historical', SID, 'completed', metadata))
        value.execute('INSERT INTO recording_work VALUES(?)', ('historical',))
        statements = []
        value.set_trace_callback(statements.append)
        auto.init_schema(value)
        value.set_trace_callback(None)
        assert value.execute(f'SELECT COUNT(*) FROM {auto.TABLE}').fetchone()[0] == 0
        assert counters(value) == [(0, 0, 0, 0), (0, 0, 0, 0)]
        assert value.execute('SELECT metadata FROM chunks').fetchone()[0] == metadata
        assert not any('FROM chunks' in sql or 'FROM recording_work' in sql for sql in statements)
        before = [tuple(r) for r in value.execute('SELECT name,sql FROM sqlite_master ORDER BY name')]
        auto.init_schema(value)
        assert [tuple(r) for r in value.execute('SELECT name,sql FROM sqlite_master ORDER BY name')] == before
    finally:
        value.close()


@pytest.mark.parametrize('fault', ['incomplete', 'incompatible'])
def test_init_schema_rejects_partial_or_changed_contract(db, fault):
    if fault == 'incomplete':
        db.execute('DROP INDEX auto_slot2')
    else:
        db.execute('DROP INDEX auto_slot2')
        db.execute(f'CREATE INDEX auto_slot2 ON {auto.TABLE}(slot2)')
    with pytest.raises(ValueError, match='automatic MOSS schema'):
        auto.init_schema(db)


def test_fixed_ledger_caps_and_maximum_per_row_charge():
    assert (auto.MAX_ROWS, auto.SESSION_ROWS) == (4096, 1024)
    assert (auto.MAX_BYTES, auto.SESSION_BYTES) == (64 * 1024**2, 16 * 1024**2)
    assert (auto.MAX_PENDING, auto.SESSION_PENDING) == (256, 64)
    assert (auto.MAX_PROOFS, auto.SESSION_PROOFS) == (16 * 1024**2, 4 * 1024**2)
    assert (auto.PROOF_BYTES, auto.BINDING_BYTES, auto.ACCOUNTING_BYTES) == (65536, 4096, 16384)
    assert auto.MAX_CHARGE == 86016
    assert auto.CORE_INTENTS == 4


@pytest.mark.parametrize('scope', ['global', 'session'])
@pytest.mark.parametrize('dimension,reason', [(0, 'rows'), (1, 'bytes'), (2, 'pending'), (3, 'proof_bytes')])
def test_capacity_accepts_exact_cap_and_rejects_one_unit_over(db, scope, dimension, reason):
    limits = ((4096, 64 * 1024**2, 256, 16 * 1024**2) if scope == 'global'
              else (1024, 16 * 1024**2, 64, 4 * 1024**2))
    # Counters satisfy usage invariants; no source or giant payload is allocated.
    have = [0, 0, 0, 0]
    if dimension == 0:
        have[:2] = [limits[0], limits[0] * auto.ACCOUNTING_BYTES]
    elif dimension == 1:
        have[:2] = [1, limits[1]]
    else:
        have = [limits[2], limits[2] * auto.ACCOUNTING_BYTES, limits[2],
                limits[3] if dimension == 3 else 0]
    seed_usage(db, tuple(have), tuple(have) if scope == 'session' else (0, 0, 0, 0))
    zero = dict(rows=0, charge=0, pending=0, proofs=0)
    auto.capacity(db, SID, **zero)
    zero[('rows', 'charge', 'pending', 'proofs')[dimension]] = 1
    with pytest.raises(ReadError) as error:
        auto.capacity(db, SID, **zero)
    assert (error.value.status_code, error.value.reason) == (429, 'automatic_' + reason + '_capacity')


def test_before_core_capacity_reserves_four_worst_case_rows(db):
    seed_usage(db, (1, auto.MAX_BYTES - 4 * auto.MAX_CHARGE, 0, 0))
    auto.capacity(db, SID)
    db.execute(f'UPDATE {auto.USAGE} SET charge=charge+1 WHERE scope=?', ('',))
    with pytest.raises(ReadError, match='automatic_bytes_capacity'):
        auto.capacity(db, SID)


@pytest.mark.parametrize('fault', ['missing_global', 'session_exceeds_global', 'proofs_without_pending', 'undercharge'])
def test_usage_invalid_counters_fail_closed(db, fault):
    if fault == 'missing_global':
        db.execute(f'DELETE FROM {auto.USAGE} WHERE scope=?', ('',))
    elif fault == 'session_exceeds_global':
        seed_usage(db, (1, 16384, 0, 0), (2, 32768, 0, 0))
    elif fault == 'proofs_without_pending':
        seed_usage(db, (1, 16384, 0, 1))
    else:
        seed_usage(db, (1, 16383, 0, 0))
    with pytest.raises(ReadError, match='automatic_usage_invalid'):
        auto.usage(db, SID)


def test_actual_enqueue_and_completion_charge_exact_bytes(db, monkeypatch):
    plan = ledger_plan(proof_bytes=auto.PROOF_BYTES, binding_bytes=auto.BINDING_BYTES)
    enqueue_plan(db, monkeypatch, plan)
    assert counters(db) == [(1, 86016, 1, 65536)] * 2
    assert row(db, plan)['charge_bytes'] == 86016
    # Re-enqueue is exact idempotency, including while the proof is retained.
    enqueue_plan(db, monkeypatch, plan)
    assert counters(db) == [(1, 86016, 1, 65536)] * 2
    publish(db, plan)
    ident = identity(plan)
    auto.admit(db, plan['key'], ident)
    assert counters(db) == [(1, 86016, 1, 65536)] * 2
    auto.complete(db, plan['key'], ident)
    assert counters(db) == [(1, 20480, 0, 0)] * 2
    assert row(db, plan)['proof'] is None
    auto.complete(db, plan['key'], ident)
    enqueue_plan(db, monkeypatch, plan)
    assert row(db, plan)['state'] == 'committed'
    assert counters(db) == [(1, 20480, 0, 0)] * 2


@pytest.mark.parametrize('field', ['proof', 'binding', 'charge_bytes'])
def test_schema_rejects_per_row_payload_one_byte_over(db, monkeypatch, field):
    plan = ledger_plan()
    enqueue_plan(db, monkeypatch, plan)
    too_large = {'proof': b'x' * 65537, 'binding': b'x' * 4097, 'charge_bytes': 86017}[field]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(f'UPDATE {auto.TABLE} SET {field}=? WHERE candidate_key=?', (too_large, plan['key']))
    assert row(db, plan)[field] != too_large


@pytest.mark.parametrize('field', ['binding', 'proof_sha256', 'cell_id'])
def test_existing_intent_key_conflict_never_replaces_first_binding(db, monkeypatch, field):
    plan = ledger_plan()
    enqueue_plan(db, monkeypatch, plan)
    before, usage_before = row(db, plan), counters(db)
    conflicting = dict(plan)
    conflicting[field] = cells.canonical({'changed': True}) if field == 'binding' else 'f' * 64
    with pytest.raises(cells.Unavailable, match='automatic_candidate_conflict'):
        enqueue_plan(db, monkeypatch, conflicting)
    assert row(db, plan) == before
    assert counters(db) == usage_before


def test_seventy_terminal_intents_use_real_ledger_beyond_manual_64_limit(db, monkeypatch):
    retained_charge = 0
    for index in range(1, 71):
        plan = ledger_plan(index)
        assert len(plan['proof']) < 128 and len(plan['binding']) < 128
        enqueue_plan(db, monkeypatch, plan)
        publish(db, plan)
        ident = identity(plan, index)
        auto.admit(db, plan['key'], ident)
        auto.complete(db, plan['key'], ident,
            state='committed' if index % 2 else 'no_result', reason='synthetic_terminal')
        retained_charge += auto.ACCOUNTING_BYTES + len(plan['binding'])
        assert counters(db) == [(index, retained_charge, 0, 0)] * 2
    assert db.execute(f'SELECT COUNT(*) FROM {auto.TABLE}').fetchone()[0] == 70
    assert db.execute(f'SELECT COUNT(*) FROM {auto.TABLE} WHERE proof IS NOT NULL').fetchone()[0] == 0
    assert db.execute(f'SELECT COUNT(DISTINCT job_id) FROM {auto.TABLE}').fetchone()[0] == 70
    # No fake manual ledger or raised production cap is used to make this pass.
    assert auto.SESSION_PENDING == 64


def test_primary_cancellation_vetoes_admission_and_completion(db, monkeypatch):
    first, second = ledger_plan(1), ledger_plan(2)
    for plan in (first, second):
        enqueue_plan(db, monkeypatch, plan)
        publish(db, plan)
    auto.admit(db, first['key'], identity(first))
    db.execute('INSERT INTO chunks VALUES(?,?,?,NULL)', ('cancelled-source', SID, 'cancelled'))
    with pytest.raises(ReadError, match='automatic_cancelled'):
        auto.admit(db, second['key'], identity(second, 2))
    with pytest.raises(ReadError, match='automatic_publication_veto'):
        auto.complete(db, first['key'], identity(first))
    assert [row(db, p)['state'] for p in (first, second)] == ['accepted', 'published']


def test_result_identity_mismatch_cannot_finish_accepted_intent(db, monkeypatch):
    plan = ledger_plan()
    enqueue_plan(db, monkeypatch, plan)
    publish(db, plan)
    auto.admit(db, plan['key'], identity(plan))
    before = counters(db)
    for field in ('job_id', 'original_jobs_epoch', 'canonical_request_sha256'):
        altered = identity(plan)
        setattr(altered, field, 'f' * (64 if field == 'canonical_request_sha256' else 32))
        with pytest.raises(ReadError, match='automatic_result_binding'):
            auto.complete(db, plan['key'], altered)
    assert row(db, plan)['state'] == 'accepted'
    assert counters(db) == before


def test_prior_admission_reconciles_interrupted_and_never_requeues(db, monkeypatch):
    plan = ledger_plan()
    enqueue_plan(db, monkeypatch, plan)
    publish(db, plan)
    ident = identity(plan)
    auto.admit(db, plan['key'], ident)
    manager = auto.Automatic(SimpleNamespace(settings=SimpleNamespace()), LedgerResults(db))
    projection = manager._projection(row(db, plan), epoch='d' * 32)
    assert projection['state'] == 'interrupted_or_unknown'
    assert projection['recovery_required'] is True
    auto.reconcile_job(db, ident.job_id)
    auto.reconcile_job(db, ident.job_id)
    enqueue_plan(db, monkeypatch, plan)
    assert row(db, plan)['state'] == 'interrupted'
    assert row(db, plan)['proof'] is None
    assert counters(db) == [(1, auto.ACCOUNTING_BYTES + len(plan['binding']), 0, 0)] * 2
    with pytest.raises(ReadError, match='automatic_admission_closed'):
        auto.admit(db, plan['key'], ident)


@pytest.mark.parametrize('kind', ['ordinary', 'core_seam', 'reset_seam'])
def test_active_roots_include_both_seam_sides_using_indexed_scalar_reads(db, monkeypatch, kind):
    plan = ledger_plan(kind=kind)
    enqueue_plan(db, monkeypatch, plan)
    for state in ('queued', 'published', 'accepted'):
        if state == 'published':
            publish(db, plan)
        elif state == 'accepted':
            auto.admit(db, plan['key'], identity(plan))
        statements = []
        db.set_trace_callback(statements.append)
        for side in ('1', '2'):
            if plan['cid' + side] is not None:
                assert auto.root(db, cid=plan['cid' + side])
                assert auto.root(db, slot=plan['slot' + side])
        assert not auto.root(db, cid='absent')
        assert not auto.root(db, slot='0' * 64)
        assert not auto.root(db)
        db.set_trace_callback(None)
        selects = [sql for sql in statements if sql.startswith('SELECT')]
        assert selects and all('SELECT 1' in sql and 'INDEXED BY auto_' in sql for sql in selects)
        assert all('proof' not in sql and 'binding' not in sql for sql in selects)
    auto.complete(db, plan['key'], identity(plan))
    for side in ('1', '2'):
        assert not auto.root(db, cid=plan['cid' + side])
        assert not auto.root(db, slot=plan['slot' + side])


def test_cancel_session_releases_only_active_proofs_and_is_idempotent(db, monkeypatch):
    plans = [ledger_plan(i) for i in range(1, 5)]
    for plan in plans:
        enqueue_plan(db, monkeypatch, plan)
    for plan in plans[1:]:
        publish(db, plan)
    for index, plan in enumerate(plans[2:], 3):
        auto.admit(db, plan['key'], identity(plan, index))
    auto.complete(db, plans[3]['key'], identity(plans[3], 4))
    committed = row(db, plans[3])
    auto.cancel_session(db, SID)
    auto.cancel_session(db, SID)
    retained = sum(auto.ACCOUNTING_BYTES + len(p['binding']) for p in plans)
    assert counters(db) == [(4, retained, 0, 0)] * 2
    assert [row(db, p)['state'] for p in plans] == ['cancelled'] * 3 + ['committed']
    assert row(db, plans[3]) == committed
    assert all(row(db, p)['proof'] is None for p in plans)


def test_erase_session_subtracts_exact_usage_preserving_other_session(db, monkeypatch):
    plans = [ledger_plan(1), ledger_plan(2, kind='core_seam'), ledger_plan(3, sid=OTHER)]
    for plan in plans:
        enqueue_plan(db, monkeypatch, plan)
    publish(db, plans[0])
    auto.admit(db, plans[0]['key'], identity(plans[0]))
    auto.complete(db, plans[0]['key'], identity(plans[0]))
    other_before = row(db, plans[2])
    remaining = (1, other_before['charge_bytes'], 1, len(plans[2]['proof']))
    auto.erase_session(db, SID)
    auto.erase_session(db, SID)
    assert counters(db, SID) == [remaining, (0, 0, 0, 0)]
    assert counters(db, OTHER) == [remaining, remaining]
    assert row(db, plans[2]) == other_before
    assert db.execute(f'SELECT COUNT(*) FROM {auto.TABLE}').fetchone()[0] == 1
    assert db.execute(f'SELECT 1 FROM {auto.USAGE} WHERE scope=?', (SID,)).fetchone() is None
    auto.erase_session(db, OTHER)
    assert counters(db, OTHER) == [(0, 0, 0, 0)] * 2


def test_plans_allow_three_ordinary_plus_one_core_seam():
    ordinary, packet, commits = plan_fixture()
    planned = auto.plans(ordinary, packet)
    assert [p['kind'] for p in planned] == ['ordinary'] * 3 + ['core_seam']
    assert len({p['key'] for p in planned}) == 4
    assert all(p['cid1'] == 'right' and p['cid2'] is None for p in planned[:3])
    assert (planned[3]['cid1'], planned[3]['cid2']) == ('left', 'right')
    assert (planned[3]['slot1'], planned[3]['slot2']) == tuple(core.slot(c) for c in commits)
    assert all(len(p['proof']) <= 65536 and len(p['binding']) <= 4096 for p in planned)
    assert auto.plans((), dict(packet, proof=None)) == []


def test_plans_reject_four_ordinary_and_non_tuple_container():
    ordinary, packet, _ = plan_fixture()
    for selected in (ordinary + ordinary[:1], list(ordinary)):
        with pytest.raises(cells.Unavailable, match='automatic_core_candidate_capacity'):
            auto.plans(selected, packet)


def test_plans_require_exact_current_commit_and_core_seam_policy():
    ordinary, packet, commits = plan_fixture()
    with pytest.raises(cells.Unavailable, match='automatic_core_commit_required'):
        auto.plans(ordinary, None)
    with pytest.raises(cells.Unavailable, match='automatic_seam_binding'):
        auto.plans((), dict(packet, commit=commits[0]))
    with pytest.raises(cells.Unavailable, match='automatic_seam_binding'):
        auto.plans((), dict(packet, proof=synthetic_composite(sid=SID).proof))
    # A seam cannot be smuggled into the ordinary tuple, even when it is a Cell.
    with pytest.raises(cells.Unavailable, match='automatic_ordinary_required'):
        auto.plans((synthetic_composite(sid=SID),), packet)


def test_plans_reject_duplicate_ownership_and_wrong_source_binding():
    ordinary, packet, _ = plan_fixture()
    baseline = auto.plans(ordinary, dict(packet, proof=None))
    new_run = deepcopy(packet)
    new_run['commit']['scope']['run'] = 'c' * 32
    assert auto.plans(ordinary, dict(new_run, proof=None)) == baseline
    # Evidence changes the proof/cell hash, but cannot mint new ownership keys.
    original = ordinary[0]
    doc = json.loads(original.proof)
    doc['native']['observed_action_count'] += 1
    support = replace(original.support, core_sha256=cells.sha({
        'start': original.support.core_start_frame, 'end': original.support.core_end_frame,
        'commit': original.commit_sha256, 'native': doc['native']}))
    doc['support'] = support.to_dict()
    changed_cell = replace(original, support=support, proof=cells.canonical(doc))
    changed_plan = auto.plans((changed_cell,), dict(packet, proof=None))[0]
    assert changed_plan['key'] == baseline[0]['key']
    assert changed_plan['binding'] == baseline[0]['binding']
    assert changed_plan['proof_sha256'] != baseline[0]['proof_sha256']
    assert changed_plan['cell_id'] != baseline[0]['cell_id']
    with pytest.raises(cells.Unavailable, match='automatic_duplicate_ownership'):
        auto.plans((ordinary[0], ordinary[0]), dict(packet, proof=None))
    changed = deepcopy(packet)
    changed['commit']['part']['source_sha256'] = 'f' * 64
    with pytest.raises(cells.Unavailable, match='automatic_source_binding'):
        auto.plans(ordinary, dict(changed, proof=None))
