"""Bounded, model-free optional-retention preflight contracts.

Every database is a fresh tiny fixture. Production schemas and immutable models
are reused, with a fake primary read authority; no owner thread, native/model,
provider, network, original recording, or historical database is used.

Conditional synthetic graph eligibility is not a live orphan proof. The live
preflight has no proved producer frontier and must never authorize deletion.
"""
from copy import deepcopy
import builtins
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from audio_backend import native_owned_cell_store as store
from audio_backend import native_owned_core_seams as core
from audio_backend import native_owned_retention as retention
from audio_backend import recording_overlap
from audio_backend.native_owned_cell_capture import Capture, PrimaryAuthority
from audio_backend.native_owned_cells import Unavailable, canonical, sha
from test_native_owned_cells import synthetic_cell
from test_native_owned_seam_cells import synthetic_pieces


class FreshAuthority:
    """Only the existing read-only authority interface, without source access."""

    identity = 'fresh-retention-fixture-only'

    def __init__(self, db):
        self.db = db
        self.owner = None
        self.reads = []
        self.after_read = None

    def _read(self, budget, phase, reader):
        budget.check()
        self.reads.append(phase)
        answer = reader(self.db)
        if self.after_read is not None:
            self.after_read()
        budget.check()
        return answer

    def retention_counts(self, budget):
        # Exercise the real primary entry point and SQL, substituting only its
        # already-tested filesystem/connection authority boundary.
        return PrimaryAuthority.retention_counts(self, budget)

    def receipt_valid(self, receipt, budget):
        budget.check()
        return True

    def cell_valid(self, doc, budget, *, publishing):
        budget.check()
        return True

    def session_valid(self, sid, budget):
        budget.check()
        return True


@pytest.fixture
def fresh(tmp_path):
    """Two small fresh SQLite families; no runtime-admission bypass or thread."""
    primary = sqlite3.connect(tmp_path / 'primary.sqlite3', isolation_level=None)
    optional = sqlite3.connect(tmp_path / 'cells.sqlite3', isolation_level=None)
    primary.row_factory = optional.row_factory = sqlite3.Row
    primary.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE sessions(id TEXT PRIMARY KEY, deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE chunks(
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
            status TEXT NOT NULL, next_offset REAL NOT NULL DEFAULT 0,
            duration REAL NOT NULL DEFAULT 0.001, timeline_start REAL NOT NULL DEFAULT 0,
            metadata TEXT);
        CREATE INDEX chunk_queue ON chunks(status);
    ''')
    core.init_schema(primary)
    recording_overlap.init_schema(primary)
    optional.execute('PRAGMA foreign_keys=ON')
    store.CellStore._schema(optional)
    authority = FreshAuthority(primary)
    owner = store.CellStore(tmp_path / 'not-opened', authority, enabled=True,
                            core_seam_enabled=True)
    owner._db = optional
    authority.owner = owner
    value = SimpleNamespace(primary=primary, optional=optional,
                            authority=authority, owner=owner, root=tmp_path)
    yield value
    assert owner._thread is None
    owner._db = None
    optional.close()
    primary.close()


def add_source(fresh, cid, *, sid='session', digest='c' * 64, frames=16,
               status='completed', timeline=0., stream='stream'):
    """Insert a real immutable receipt and matching tiny primary row."""
    fresh.primary.execute('INSERT OR IGNORE INTO sessions(id) VALUES(?)', (sid,))
    fresh.primary.execute(
        'INSERT INTO chunks(id,session_id,status,duration,timeline_start) VALUES(?,?,?,?,?)',
        (cid, sid, status, frames / 16000, timeline))
    size = 44 + frames * 2
    receipt = store.Receipt(sid, cid, digest, frames, 44, size,
                            (1, len(fresh.owner._receipts) + 1, size, 1, 1),
                            timeline, stream)
    fresh.optional.execute('INSERT INTO sources VALUES(?,?,?,?)',
                           (cid, sid, digest, receipt.encoded))
    fresh.owner._receipts[cid] = receipt
    return receipt


def add_cell(fresh, cell, *, with_pcm=True):
    """Reuse exact production row and dependency shapes, including metadata-only."""
    fresh.optional.execute('''
        INSERT INTO cells(cell_id,ownership,sid,cid,proof,pcm,state)
        VALUES(?,?,?,?,?,?,?)''',
        (cell.cell_id, cell.ownership, cell.session_id, cell.chunk_id,
         cell.proof, cell.pcm if with_pcm else None,
         'retained' if with_pcm else 'not_retained_pcm'))
    for index, part in enumerate(cell.parts):
        fresh.optional.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)',
            (cell.cell_id, index, part.chunk_id, part.source_sha256,
             part.receipt_sha256, part.source_start, part.source_end,
             part.logical_start, part.logical_end))
    return cell


def add_core_pair(fresh, *, suffix='', pending=False, retain=False,
                  with_pcm=True, left_state='none'):
    """A valid 32-frame v3 proof assembled from existing synthetic v2 fixtures."""
    left = add_source(fresh, 'left' + suffix)
    right = add_source(fresh, 'right' + suffix, digest='e' * 64, timeline=.001)
    pieces = synthetic_pieces(cids=(left.chunk_id, right.chunk_id),
        receipt_ids=(left.receipt_id, right.receipt_id))
    commits = []
    for piece in pieces:
        evidence = piece.commit['evidence']
        commits.append({
            'scope': {'session_id': piece.part.session_id, 'kind': 'stream',
                      'key': 'stream', 'origin': (0.).hex(), 'run': 'a' * 32,
                      'reset': 1, 'epoch': 'a' * 32, 'index': 0},
            'part': asdict(piece.part),
            'native_sha256': sha(evidence['native']),
            'cut_sha256': sha(evidence['core_cut']),
            'evidence_sha256': sha(evidence),
            'runtime_sha256': sha(piece.witness['native']['provenance']),
        })
    pcm = pieces[0].pcm + pieces[1].pcm
    query = {
        'query_start_frame': 0, 'query_end_frame': 32,
        'query_pcm16le_sha256': hashlib.sha256(pcm).hexdigest(),
        'support_pcm16le_sha256': hashlib.sha256(pcm).hexdigest(),
        'native': deepcopy(pieces[1].witness['native']),
        'turns': deepcopy(pieces[1].witness['turns']),
    }
    cell = core.make_cell(commits, query, pieces[1].commit['evidence'], pcm)
    core.validate_document(cell.proof)
    for index, commit in enumerate(commits):
        has_pending = pending and index == 1
        fresh.primary.execute('INSERT INTO native_core_commits VALUES(?,?,?,?,?,?,?,?,?)',
            (core.slot(commit), 'session', commit['part']['chunk_id'],
             core.scope_key(commit['scope']), commit['part']['logical_end'],
             canonical(commit, core.COMPACT_BYTES), cell.proof if has_pending else None,
             'pending' if has_pending else left_state if index == 0 else 'published',
             'synthetic_pending' if has_pending else 'synthetic_settled'))
    if retain:
        add_cell(fresh, cell, with_pcm=with_pcm)
    return cell, commits


def durable_snapshot(fresh):
    """Exact durable rows plus primary/optional files, independent of telemetry."""
    rows = []
    for db in (fresh.primary, fresh.optional):
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        rows.append([(name, [tuple(row) for row in db.execute('SELECT * FROM ' + name)])
                     for name in tables])
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in fresh.root.iterdir() if p.is_file()}
    return rows, files, tuple(fresh.owner._receipts.items())


def refreshed(fresh):
    with store.Budget.for_owner().phase() as budget:
        fresh.owner._refresh_retention(budget)
    return fresh.owner.health()['retention']


def assert_protected(row, occupied):
    assert row['occupied'] == occupied
    assert row['eligible_orphan'] == 0
    assert row['referenced'] + row['unknown_protected'] == occupied


@pytest.mark.parametrize('kind', ['cells', 'sources', 'compacts'])
def test_explicit_roots_can_never_be_orphans(kind):
    assert retention.classify_row(kind, referenced=True, roots_complete=True,
                                  behind_frontier=True) == 'referenced'
    assert retention.classify_row(kind, pending=True, roots_complete=True,
                                  behind_frontier=True) == 'referenced'
    assert retention.classify_row(kind, in_flight=True, roots_complete=True,
                                  behind_frontier=True) == 'referenced'


@pytest.mark.parametrize('roots_complete,behind_frontier',
                         [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize('kind', ['sources', 'compacts'])
def test_missing_complete_roots_or_frontier_is_protected(kind, roots_complete,
                                                        behind_frontier):
    assert retention.classify_row(kind, roots_complete=roots_complete,
        behind_frontier=behind_frontier) == 'unknown_protected'


def test_every_retained_cell_is_a_root_even_after_pcm_release():
    for complete in (False, True):
        assert retention.classify_row('cells', roots_complete=complete,
                                      behind_frontier=True) == 'referenced'


def test_synthetic_referenced_full_graph_and_true_orphan_graph_differ():
    # Exactly known fixture roots/frontier. This is deliberately NOT a claim
    # that the live owner can supply either complete proof today.
    dependencies = {'metadata_only_cell': {'left', 'right'},
                    'retained_cell': {'left', 'third'}}
    source_rows = {'left', 'right', 'third', 'settled_orphan'}
    roots = set().union(*dependencies.values())
    observed = {cid: retention.classify_row('sources', referenced=cid in roots,
        roots_complete=True, behind_frontier=True) for cid in source_rows}
    assert {cid for cid, state in observed.items() if state == 'referenced'} == roots
    assert {cid for cid, state in observed.items() if state == 'eligible_orphan'} == {
        'settled_orphan'}
    dependencies['retained_cell'].add('settled_orphan')
    roots = set().union(*dependencies.values())
    assert all(retention.classify_row('sources', referenced=cid in roots,
               roots_complete=True, behind_frontier=True) == 'referenced'
               for cid in source_rows)
    assert retention.classify_row('compacts', roots_complete=True,
                                  behind_frontier=True) == 'eligible_orphan'


@pytest.mark.parametrize('bad', [None, 1, 'yes', [], {}])
@pytest.mark.parametrize('flag', ['referenced', 'pending', 'in_flight',
                                 'roots_complete', 'behind_frontier'])
def test_classifier_rejects_unproved_truthy_flags(flag, bad):
    with pytest.raises(Unavailable, match='retention_classification_input'):
        retention.classify_row('sources', **{flag: bad})


def test_empty_preflight_reports_real_global_limits_and_zero_eligibility(fresh):
    report = refreshed(fresh)
    assert report['roots_complete'] is False
    assert report['producer_frontier_known'] is False
    for kind, limit in (('cells', store.MAX_CELLS),
                        ('sources', store.MAX_RECEIPTS),
                        ('compacts', core.MAX_COMPACTS)):
        assert_protected(report[kind], 0)
        assert report[kind]['limit'] == limit
    assert report['cells']['referenced_is_lower_bound'] is False
    assert report['sources']['referenced_is_lower_bound'] is True
    assert report['compacts']['referenced_is_lower_bound'] is True
    assert fresh.authority.reads == ['retention']


@pytest.mark.parametrize('with_pcm', [True, False])
def test_real_retained_cell_dependency_is_root_even_without_pcm(fresh, with_pcm):
    receipt = add_source(fresh, 'chunk')
    add_source(fresh, 'unproven_orphan')
    cell = synthetic_cell(cid='chunk', receipt_id=receipt.receipt_id)
    add_cell(fresh, cell, with_pcm=with_pcm)
    report = refreshed(fresh)
    assert_protected(report['cells'], 1)
    assert report['cells']['referenced'] == 1
    assert_protected(report['sources'], 2)
    assert report['sources']['referenced'] == 1
    assert report['sources']['unknown_protected'] == 1
    assert fresh.optional.execute('SELECT state FROM cells').fetchone()[0] == (
        'retained' if with_pcm else 'not_retained_pcm')


def test_distinct_dependency_roots_are_not_double_counted_across_cells(fresh):
    receipt = add_source(fresh, 'shared', frames=32)
    first = synthetic_cell(0, 32, cid='shared', receipt_id=receipt.receipt_id)
    second = synthetic_cell(1, 16, cid='shared', receipt_id=receipt.receipt_id)
    add_cell(fresh, first, with_pcm=False)
    add_cell(fresh, second)
    report = refreshed(fresh)
    assert_protected(report['cells'], 2)
    assert_protected(report['sources'], 1)
    assert report['sources']['referenced'] == 1


def test_counts_are_instance_global_across_sessions(fresh):
    for sid in ('session_one', 'session_two'):
        receipt = add_source(fresh, 'chunk_' + sid, sid=sid)
        cell = synthetic_cell(cid=receipt.chunk_id, sid=sid,
                              receipt_id=receipt.receipt_id)
        add_cell(fresh, cell, with_pcm=False)
    report = refreshed(fresh)
    assert_protected(report['cells'], 2)
    assert_protected(report['sources'], 2)
    assert report['sources']['referenced'] == 2


@pytest.mark.parametrize('with_pcm', [True, False])
def test_both_retained_core_commit_sides_and_receipts_remain_protected(fresh,
                                                                    with_pcm):
    cell, commits = add_core_pair(fresh, retain=True, with_pcm=with_pcm)
    before = durable_snapshot(fresh)
    report = refreshed(fresh)
    assert_protected(report['cells'], 1)
    assert_protected(report['sources'], 2)
    assert report['sources']['referenced'] == 2
    assert_protected(report['compacts'], 2)
    # This initial slice does not decode every retained proof for compact
    # closure. Unknown protection keeps BOTH exact stored sides, including a
    # published compact, without falsely calling the root scan complete.
    assert report['compacts']['unknown_protected'] == 2
    assert report['compacts']['referenced_is_lower_bound'] is True
    assert {row[0] for row in fresh.primary.execute('SELECT slot FROM native_core_commits')} == {
        core.slot(commit) for commit in commits}
    assert durable_snapshot(fresh) == before
    assert fresh.optional.execute('SELECT proof FROM cells').fetchone()[0] == cell.proof


@pytest.mark.parametrize('left_state', ['none', 'published', 'gap'])
def test_pending_proof_protects_holder_and_unknown_left_side(fresh, left_state):
    cell, commits = add_core_pair(fresh, pending=True, left_state=left_state)
    before = durable_snapshot(fresh)
    report = refreshed(fresh)
    assert_protected(report['sources'], 2)
    assert report['sources']['unknown_protected'] == 2
    assert_protected(report['compacts'], 2)
    assert report['compacts']['pending'] == 1
    assert report['compacts']['referenced'] == 1
    assert report['compacts']['unknown_protected'] == 1
    assert fresh.primary.execute(
        'SELECT pending FROM native_core_commits WHERE slot=?',
        (core.slot(commits[1]),)).fetchone()[0] == cell.proof
    assert durable_snapshot(fresh) == before


def test_unreleased_outbox_including_durable_receipt_is_protected(fresh):
    receipts = [add_source(fresh, name) for name in
                ('pending', 'durable_unreleased', 'durable_released')]
    fresh.primary.execute('''INSERT INTO recording_batches
        (id,session_id,capture_id,state,manifest,created,mode)
        VALUES('batch','session','capture','pending_receipts','frozen-batch',0,'sealed_pages')''')
    for page_id, number, released, state in (
            ('pending-page', 1, 0, 'pending_receipts'),
            ('released-page', 2, 1, 'ready')):
        fresh.primary.execute('''INSERT INTO recording_pages
            (id,batch_id,page_number,state,released,start_frame,end_frame,manifest,created)
            VALUES(?,'batch',?,?,?,?,?,?,0)''',
            (page_id, number, state, released, (number - 1) * 32,
             number * 32, 'frozen-page-' + page_id))
    for position, receipt in enumerate(receipts):
        fresh.primary.execute('''INSERT INTO recording_work
            (chunk_id,batch_id,position,frames,bytes,capture_start_frame,
             native_receipt_status,native_receipt,receipt_id,receipt_state,page_id)
            VALUES(?,'batch',?,?,?,?,?,?,?,?,?)''',
            (receipt.chunk_id, position, receipt.frames, receipt.size, position * 16,
             'durable' if position else 'publication_pending', receipt.encoded,
             receipt.receipt_id, 'durable' if position else 'pending',
             'released-page' if position == 2 else 'pending-page'))
    before = durable_snapshot(fresh)
    report = refreshed(fresh)
    assert_protected(report['sources'], 3)
    assert report['sources']['unknown_protected'] == 3
    assert report['roots_complete'] is False
    assert durable_snapshot(fresh) == before


@pytest.mark.parametrize('status', ['queued', 'running', 'paused', 'failed',
                                   'waiting_input', 'staging', 'completed'])
def test_unproved_producer_or_replay_horizon_never_becomes_orphan(fresh, status):
    add_source(fresh, 'producer_source', status=status)
    report = refreshed(fresh)
    assert_protected(report['sources'], 1)
    assert report['sources']['unknown_protected'] == 1
    assert report['producer_frontier_known'] is False


def test_released_pin_does_not_retire_still_readable_cell_evidence(fresh):
    receipt = add_source(fresh, 'chunk')
    cell = add_cell(fresh, synthetic_cell(cid='chunk', receipt_id=receipt.receipt_id))
    pin = store.Pin(fresh.owner, cell.cell_id, json.loads(cell.proof), cell.pcm)
    fresh.owner._pins[id(pin)] = pin
    before = durable_snapshot(fresh)
    assert fresh.owner.health()['pins'] == 1
    assert refreshed(fresh)['cells']['referenced'] == 1
    assert pin.release() is True
    assert pin.release_confirmed
    assert fresh.owner.health()['pins'] == 0
    report = refreshed(fresh)
    assert report['cells']['referenced'] == 1
    assert report['sources']['referenced'] == 1
    assert report['cells']['eligible_orphan'] == 0
    assert durable_snapshot(fresh) == before


@pytest.mark.parametrize('kind', ['cells', 'sources'])
def test_full_referenced_capacity_is_explicit_and_never_evicted(fresh,
                                                               monkeypatch, kind):
    receipt = add_source(fresh, 'chunk')
    add_cell(fresh, synthetic_cell(cid='chunk', receipt_id=receipt.receipt_id),
             with_pcm=False)
    monkeypatch.setattr(store, 'MAX_CELLS' if kind == 'cells' else 'MAX_RECEIPTS', 1)
    before = durable_snapshot(fresh)
    report = refreshed(fresh)
    assert report['status'] == 'blocked_referenced_capacity'
    assert report['reason'] == kind + '_referenced_capacity'
    assert report[kind]['occupied'] == report[kind]['limit'] == 1
    assert report[kind]['eligible_orphan'] == 0
    assert durable_snapshot(fresh) == before


@pytest.mark.parametrize('kind', ['sources', 'compacts'])
def test_full_unproven_capacity_is_blocked_without_false_reference_claim(fresh,
                                                                        monkeypatch, kind):
    if kind == 'sources':
        add_source(fresh, 'unproved')
        monkeypatch.setattr(store, 'MAX_RECEIPTS', 1)
    else:
        add_core_pair(fresh)
        monkeypatch.setattr(core, 'MAX_COMPACTS', 2)
    before = durable_snapshot(fresh)
    report = refreshed(fresh)
    assert report['status'] == 'optional_unavailable'
    assert report['reason'] == kind + '_unknown_protected_capacity'
    assert report[kind]['referenced'] == 0
    assert report[kind]['unknown_protected'] == report[kind]['occupied']
    assert report['reclamation_enabled'] is False
    assert durable_snapshot(fresh) == before


def test_pending_count_and_byte_limit_are_reported_without_projecting_proofs(fresh):
    cell, _ = add_core_pair(fresh, pending=True)
    report = refreshed(fresh)
    assert report['pending_proof_limit'] == core.MAX_PENDING
    assert report['pending_proof_bytes_limit'] == core.MAX_PENDING * core.PENDING_BYTES
    assert report['pending_proof_bytes'] == len(cell.proof)
    assert report['metadata_only_cells'] == 0


def test_preflight_is_sql_read_only_and_preserves_every_durable_byte(fresh):
    add_core_pair(fresh, pending=True, retain=True, with_pcm=False)
    before = durable_snapshot(fresh)
    changes = (fresh.primary.total_changes, fresh.optional.total_changes)
    statements = []
    for db in (fresh.primary, fresh.optional):
        db.execute('PRAGMA query_only=ON')
        db.set_trace_callback(statements.append)
    report = refreshed(fresh)
    for db in (fresh.primary, fresh.optional):
        db.set_trace_callback(None)
    assert report['snapshot_state'] == 'last_observed'
    assert report['metadata_only_cells'] == 1
    assert report['reclamation_enabled'] is False
    assert changes == (fresh.primary.total_changes, fresh.optional.total_changes)
    assert durable_snapshot(fresh) == before
    assert statements
    assert all(sql.lstrip().upper().startswith(('SELECT ', 'PRAGMA PAGE_COUNT',
                                                'PRAGMA FREELIST_COUNT'))
               for sql in statements), statements
    storage = report['storage']
    assert storage['page_count'] == fresh.optional.execute('PRAGMA page_count').fetchone()[0]
    assert storage['freelist_pages'] == fresh.optional.execute('PRAGMA freelist_count').fetchone()[0]
    assert storage['reusable_bytes'] == storage['freelist_pages'] * store.PAGE_SIZE
    assert storage['last_observed_allocated_bytes'] == fresh.owner._charge


def test_health_is_passive_fixed_size_and_does_not_expose_cached_mutability(fresh,
                                                                         monkeypatch):
    add_core_pair(fresh, retain=True)
    report = refreshed(fresh)
    capture = Capture(fresh.owner, fresh.authority)
    capture.active = True

    def forbidden(*args, **kwargs):
        raise AssertionError('health must not perform SQL, filesystem, thread or wait work')

    poison = SimpleNamespace(execute=forbidden, set_progress_handler=forbidden)
    with monkeypatch.context() as blocked:
        blocked.setattr(fresh.owner, '_db', poison)
        blocked.setattr(fresh.owner, '_scan', forbidden)
        blocked.setattr(fresh.authority, 'retention_counts', forbidden)
        blocked.setattr(sqlite3, 'connect', forbidden)
        blocked.setattr(builtins, 'open', forbidden)
        blocked.setattr(Path, 'open', forbidden)
        blocked.setattr(Path, 'stat', forbidden)
        blocked.setattr(Path, 'lstat', forbidden)
        blocked.setattr(os, 'open', forbidden)
        blocked.setattr(os, 'stat', forbidden)
        blocked.setattr(os, 'scandir', forbidden)
        blocked.setattr(threading, 'Thread', forbidden)
        blocked.setattr(fresh.owner._wake, 'wait', forbidden)
        blocked.setattr(fresh.owner._ready, 'wait', forbidden)
        first = fresh.owner.health()
        richer = capture.health()
        first['retention']['cells']['occupied'] = 999
        first['retention']['storage']['page_count'] = 999
        first['retention']['roots']['unreleased_outbox'] = 0
        second = fresh.owner.health()
    assert second['retention']['cells']['occupied'] == report['cells']['occupied']
    assert second['retention']['storage']['page_count'] == report['storage']['page_count']
    assert second['retention']['roots']['unreleased_outbox'] is None
    assert richer['retention']['roots']['producer_active'] == 1
    assert 'producer_active' not in second['retention']['roots']
    encoded = json.dumps(second)
    assert len(encoded) <= 8192
    assert str(fresh.root) not in encoded
    assert 'left' not in encoded and 'right' not in encoded


def test_health_before_observation_is_explicitly_unknown(fresh):
    report = fresh.owner.health()['retention']
    assert report['snapshot_state'] == 'unobserved'
    assert report['status'] == 'optional_unavailable'
    assert report['occupancy_complete'] is False
    assert report['observed_at_monotonic'] is None
    assert all(report[kind]['occupied'] is None for kind in ('cells', 'sources', 'compacts'))
    assert report['roots']['retained_moss_results'] is None
    assert report['roots']['moss_results_covered_by_retained_cells'] is True


def test_snapshot_expiry_is_visible_without_triggering_sql(fresh, monkeypatch):
    report = refreshed(fresh)
    observed = report['observed_at_monotonic']
    reads = tuple(fresh.authority.reads)
    with monkeypatch.context() as frozen:
        frozen.setattr(store.time, 'monotonic', lambda: observed + store.RETENTION_REFRESH_SECONDS)
        expired = fresh.owner.health()['retention']
    assert expired['snapshot_state'] == 'expired'
    assert expired['status'] == 'optional_unavailable'
    assert expired['occupancy_complete'] is False
    assert expired['counts_are_last_observed'] is True
    assert tuple(fresh.authority.reads) == reads


@pytest.mark.parametrize('operation', ['receipt', 'publish', 'expire', 'delete'])
def test_mutation_admission_marks_sample_dirty_before_owner_work(fresh, operation):
    receipt = add_source(fresh, 'chunk')
    cell = synthetic_cell(cid='chunk', receipt_id=receipt.receipt_id)
    report = refreshed(fresh)
    assert report['snapshot_state'] == 'last_observed'
    fresh.owner._ready.set()
    payload = receipt if operation == 'receipt' else (cell,) if operation == 'publish' else cell.cell_id
    op = fresh.owner.submit(operation, payload)
    try:
        assert isinstance(op, store.Operation)
        dirty = fresh.owner.health()['retention']
        assert dirty['snapshot_state'] == 'dirty'
        assert dirty['status'] == 'optional_unavailable'
        assert dirty['roots']['in_flight_operations'] == 1
        assert dirty['cells']['occupied'] == 0
    finally:
        fresh.owner._slot = None


@pytest.mark.parametrize('invalidate', ['revoked', 'close_admission'])
def test_generation_change_invalidates_cached_sample_immediately(fresh, invalidate):
    refreshed(fresh)
    getattr(fresh.owner, invalidate)()
    report = fresh.owner.health()['retention']
    assert report['snapshot_state'] == 'generation_changed'
    assert report['status'] == 'optional_unavailable'
    assert report['occupancy_complete'] is False


@pytest.mark.parametrize('change,reason', [
    ('generation', 'retention_generation_changed'),
    ('dirty', 'retention_refresh_deferred'),
    ('handoff', 'retention_refresh_deferred'),
])
def test_mid_observation_change_refuses_new_snapshot_without_mutation(fresh, change, reason):
    receipt = add_source(fresh, 'chunk')
    refreshed(fresh)
    old_snapshot = fresh.owner._retention_snapshot
    before = durable_snapshot(fresh)
    if change == 'generation':
        fresh.authority.after_read = fresh.owner.close_admission
    elif change == 'dirty':
        fresh.authority.after_read = fresh.owner.retention_changed
    else:
        def accept_handoff():
            fresh.owner._slot = store.Operation('receipt', receipt, fresh.owner._epoch)
        fresh.authority.after_read = accept_handoff
    try:
        assert fresh.owner._refresh_retention(store.Budget.for_owner()) is False
        report = fresh.owner.health()['retention']
        assert fresh.owner._retention_snapshot is old_snapshot
        assert report['status'] == 'optional_unavailable'
        assert report['reason'] == reason
        assert report['snapshot_state'] == 'unavailable'
        assert durable_snapshot(fresh) == before
    finally:
        fresh.owner._slot = None


def test_existing_in_flight_handoff_defers_before_any_sql(fresh):
    receipt = add_source(fresh, 'inflight')
    fresh.owner._slot = store.Operation('receipt', receipt, fresh.owner._epoch)
    statements = []
    fresh.optional.set_trace_callback(statements.append)
    try:
        assert fresh.owner._refresh_retention(store.Budget.for_owner()) is False
        report = fresh.owner.health()['retention']
        assert report['reason'] == 'retention_refresh_deferred'
        assert report['roots']['in_flight_operations'] == 1
        assert report['sources']['eligible_orphan'] == 0
        assert statements == []
        assert fresh.authority.reads == []
    finally:
        fresh.optional.set_trace_callback(None)
        fresh.owner._slot = None


def test_handoff_arriving_during_first_sql_yields_before_further_scan(fresh):
    receipt = add_source(fresh, 'arriving')
    refreshed(fresh)
    old_snapshot = fresh.owner._retention_snapshot
    fresh.authority.reads.clear()
    fresh.owner._status = 'ready'
    statements = []

    def arrive(sql):
        statements.append(sql)
        if len(statements) == 1:
            fresh.owner._slot = store.Operation('receipt', receipt, fresh.owner._epoch)

    fresh.optional.set_trace_callback(arrive)
    try:
        assert fresh.owner._refresh_retention(store.Budget.for_owner()) is False
        assert len(statements) == 1
        assert fresh.authority.reads == []
        assert fresh.owner._retention_snapshot is old_snapshot
        assert fresh.owner.health()['retention']['reason'] == 'retention_refresh_deferred'
        assert fresh.owner._status == 'ready'
        assert fresh.owner._cleanup_pending is False
    finally:
        fresh.optional.set_trace_callback(None)
        fresh.owner._slot = None


@pytest.mark.parametrize('limit', ['sql', 'steps', 'deadline'])
def test_bounded_refusal_keeps_last_sample_and_primary_status(fresh, limit):
    add_source(fresh, 'protected')
    refreshed(fresh)
    before = durable_snapshot(fresh)
    old_snapshot = fresh.owner._retention_snapshot
    fresh.owner._status = 'ready'
    budget = store.Budget.for_owner()
    if limit == 'sql':
        budget.sql_bytes = store.SQL_PROJECTION_BYTES - 32
    elif limit == 'steps':
        budget.steps = store.SQL_STEPS + 1
    else:
        budget.deadline = 0.
    assert fresh.owner._refresh_retention(budget) is False
    report = fresh.owner.health()['retention']
    assert report['snapshot_state'] == 'unavailable'
    assert report['reason'] == ('cooperative_deadline' if limit == 'deadline' else 'operation_work_capacity')
    assert fresh.owner._retention_snapshot is old_snapshot
    assert fresh.owner._status == 'ready'
    assert fresh.owner._cleanup_pending is False
    assert durable_snapshot(fresh) == before
    # A failed retention budget must not poison later primary/optional queries.
    assert fresh.optional.execute('''WITH RECURSIVE n(x) AS
        (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100)
        SELECT SUM(x) FROM n''').fetchone()[0] == 5050


@pytest.mark.parametrize('fault', ['busy', 'private_message', 'over_capacity'])
def test_primary_observation_failure_is_private_bounded_and_nonfatal(fresh,
                                                                  monkeypatch, fault):
    refreshed(fresh)
    old_snapshot = fresh.owner._retention_snapshot
    fresh.owner._status = 'ready'
    if fault == 'over_capacity':
        monkeypatch.setattr(fresh.authority, 'retention_counts',
                            lambda budget: {'occupied': core.MAX_COMPACTS + 1,
                                            'pending': 0, 'pending_bytes': 0})
    else:
        def refuse(budget):
            if fault == 'busy':
                raise sqlite3.OperationalError('database is locked')
            raise RuntimeError('private path /secret/project and receipt private-id')
        monkeypatch.setattr(fresh.authority, 'retention_counts', refuse)
    assert fresh.owner._refresh_retention(store.Budget.for_owner()) is False
    report = fresh.owner.health()['retention']
    assert report['status'] == 'optional_unavailable'
    assert report['snapshot_state'] == 'unavailable'
    assert fresh.owner._retention_snapshot is old_snapshot
    assert fresh.owner._status == 'ready'
    assert fresh.owner._cleanup_pending is False
    assert 'secret' not in json.dumps(report) and 'private-id' not in json.dumps(report)


def test_primary_counts_reject_too_many_pending_proofs_without_writes(fresh,
                                                                   monkeypatch):
    add_core_pair(fresh, suffix='one', pending=True)
    add_core_pair(fresh, suffix='two', pending=True)
    before = durable_snapshot(fresh)
    monkeypatch.setattr(core, 'MAX_PENDING', 1)
    assert fresh.owner._refresh_retention(store.Budget.for_owner()) is False
    report = fresh.owner.health()['retention']
    assert report['reason'] == 'retention_compact_capacity'
    assert report['status'] == 'optional_unavailable'
    assert durable_snapshot(fresh) == before


@pytest.mark.parametrize('priority', ['receipt', 'reconcile', 'idle', 'admission_closed'])
def test_real_owner_loop_only_refreshes_at_idle_and_preserves_work_priority(fresh,
                                                                         monkeypatch, priority):
    # Run the unchanged production loop synchronously for exactly one useful
    # iteration, then its real stop/close path. No worker/thread is launched.
    calls = []
    waits = []
    owner = fresh.owner

    def wait(timeout):
        waits.append(timeout)
        assert len(waits) <= 3, 'fixture exceeded its fixed owner-loop bound'
        if len(waits) == 2:
            owner._stop.set()
        return True

    monkeypatch.setattr(owner, '_wake', SimpleNamespace(wait=wait, clear=lambda: None,
                                                       set=lambda: None))
    monkeypatch.setattr(owner, '_open', lambda: None)
    monkeypatch.setattr(owner, '_scan', lambda budget=None: {})
    original_refresh = owner._refresh_retention

    def refresh(budget):
        calls.append('refresh')
        return original_refresh(budget)

    def sweep(budget):
        calls.append('reconcile')
        owner._reconcile.clear()

    def receipt(op, budget):
        calls.append('receipt')
        return {'status': 'receipt_retained'}

    monkeypatch.setattr(owner, '_refresh_retention', refresh)
    monkeypatch.setattr(owner, '_sweep', sweep)
    monkeypatch.setattr(owner, '_receipt', receipt)
    op = None
    if priority == 'receipt':
        item = add_source(fresh, 'admitted')
        op = store.Operation('receipt', item, owner._epoch)
        owner._slot = op
        owner._reconcile.set()
    elif priority == 'reconcile':
        owner._reconcile.set()
    elif priority == 'admission_closed':
        owner._admission_closed = True
    owner._run()
    assert calls == ({'receipt': ['receipt'], 'reconcile': ['reconcile'],
                      'idle': ['refresh'], 'admission_closed': []}[priority])
    assert len(waits) == 2
    assert owner._thread is None
    assert owner._db is None
    assert owner._status == 'ready'
    if op is not None:
        assert op.done.is_set() and op.slot_released.is_set()
        assert op.result == {'status': 'receipt_retained'}


def test_real_owner_idle_refresh_failure_does_not_create_cleanup_failure(fresh,
                                                                      monkeypatch):
    owner = fresh.owner
    waits = []
    observed = []
    primary_calls = []

    def wait(timeout):
        waits.append(timeout)
        assert len(waits) <= 4
        if len(waits) > 1:
            observed.append((owner._status, owner._cleanup_pending,
                             owner.health()['retention']['snapshot_state']))
        if len(waits) == 3:
            owner._stop.set()
        return True

    def refuse(budget):
        primary_calls.append(1)
        raise sqlite3.OperationalError('fixture SQLITE_BUSY')

    monkeypatch.setattr(owner, '_wake', SimpleNamespace(wait=wait, clear=lambda: None,
                                                       set=lambda: None))
    monkeypatch.setattr(owner, '_open', lambda: None)
    monkeypatch.setattr(owner, '_scan', lambda budget=None: {})
    monkeypatch.setattr(fresh.authority, 'retention_counts', refuse)
    # Fixed time proves failure cadence without sleeps or a wall-time race.
    with monkeypatch.context() as frozen:
        frozen.setattr(store.time, 'monotonic', lambda: 100.)
        owner._run()
        assert owner._retention_next_refresh == 100. + store.RETENTION_REFRESH_SECONDS
    assert primary_calls == [1]
    assert observed == [('ready', False, 'unavailable')] * 2
    assert len(waits) == 3
    assert owner._status == 'ready'
    assert owner._cleanup_pending is False
    assert owner._db is None


def test_missing_primary_counter_is_explicitly_incomplete_and_never_eligible(fresh,
                                                                          monkeypatch):
    add_source(fresh, 'protected')
    monkeypatch.setattr(fresh.authority, 'retention_counts', None)
    report = refreshed(fresh)
    assert report['occupancy_complete'] is False
    assert report['compacts']['occupied'] is None
    assert report['compacts']['unknown_protected'] is None
    assert report['sources']['unknown_protected'] == 1
    assert all(report[kind]['eligible_orphan'] == 0 for kind in ('cells', 'sources', 'compacts'))




