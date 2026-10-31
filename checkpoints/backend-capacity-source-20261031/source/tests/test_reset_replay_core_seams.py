"""Model-free reset replay seam candidate checks through real transactions.

SOURCE ONLY until the candidate review authorizes execution. Native replies and
ASR are synthetic; the Store, Worker, Capture, source receipts, outbox, CellStore,
and acknowledgement path are real. These tests do not qualify models, binaries,
VFS allocation, process-crash recovery, acoustic accuracy, or speaker identity.

Run the two long cases SERIAL, each in its own fresh bounded process: CPU 3,
nice 19, 300-second wall timeout, 512 MiB RSS guard, 2 GiB address-space cap.
The source is generated and hashed in <=64 KiB blocks. Live fixture output is
checked against 160 MiB. RESET_REPLAY_REPORT_DIR optionally names a report
directory OUTSIDE the disposable fixture roots. Only small JSON evidence is
preserved; fixture removal happens only after confirmed owner-thread shutdown.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import sqlite3
import time
import wave

import pytest

from audio_backend import native_owned_core_seams as core
from audio_backend.native_owned_cell_capture import Capture
from audio_backend.native_owned_cell_store import Budget, CellStore
from audio_backend.native_owned_cells import (
    POLICY_SHA256 as V1_POLICY, RATE, RING_PCM_BYTES, Unavailable, canonical, sha,
)
from audio_backend.native_owned_seam_cells import POLICY_SHA256 as V2_POLICY
from test_multicore_seam_coverage import assert_disjoint_cover, pcm
from test_native_owned_cell_store import simulated_profile
from test_same_epoch_core_seams import RealHarness, wait


BLOCK_BYTES = 64 * 1024
OUTPUT_LIMIT = 160 * 1024**2
RSS_LIMIT = 512 * 1024**2


class StreamedHarness(RealHarness):
    """Reuse the real harness, replacing its whole-upload convenience helper."""

    def __init__(self, root, seams, monkeypatch):
        self.evidence = {'synthetic_native_and_asr': True, 'long_timeline_assertions_complete': False}
        self.source_blocks = []
        self.core_actions = []
        self.peak_fixture_bytes = 0
        self.began = time.monotonic()
        super().__init__(root, seams, monkeypatch)
        original_prepare = self.capture.core_seams.prepare

        def prepare(chunk, native, offset, end, result, coordinator, stop):
            before = len(coordinator.process.calls)
            answer = original_prepare(chunk, native, offset, end, result, coordinator, stop)
            actions = coordinator.process.calls[before:]
            self.core_actions.append({'start_frame': round(offset * RATE), 'actions': actions})
            assert len(actions) <= 1 and all(action == 'turns' for action in actions), actions
            self.sample_bounds()
            return answer

        self.capture.core_seams.prepare = prepare

    def sample_bounds(self):
        size = 0
        for path in self.root.rglob('*'):
            try:
                if path.is_file():
                    size += path.stat().st_size
            except FileNotFoundError:
                # A journal or bounded extracted window may disappear between
                # enumeration and stat; the continuous runner also monitors.
                continue
        self.peak_fixture_bytes = max(self.peak_fixture_bytes, size)
        assert size <= OUTPUT_LIMIT, (size, OUTPUT_LIMIT)
        # Linux ru_maxrss is KiB. The external runner supplies a continuous
        # guard; this is a second, measured high-water assertion per core.
        assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 <= RSS_LIMIT

    def add(self, seconds, final=True, isolated=False):
        origin = sum(self.store.chunk(cid)['duration'] for cid in self.source_ids)
        start, end = round(origin * RATE), round((origin + seconds) * RATE)
        path = self.root / 'uploads' / f'source{len(self.source_ids)}.part'
        largest_write = largest_read = 0
        with wave.open(str(path), 'wb') as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(RATE)
            target.setnframes(end - start)
            for at in range(start, end, BLOCK_BYTES // 2):
                data = pcm(at, min(end, at + BLOCK_BYTES // 2))
                largest_write = max(largest_write, len(data))
                target.writeframesraw(data)
        size = path.stat().st_size
        assert size == 44 + (end - start) * 2
        digest = hashlib.sha256()
        with path.open('rb') as source:
            while block := source.read(BLOCK_BYTES):
                largest_read = max(largest_read, len(block))
                digest.update(block)
            checksum = digest.hexdigest()
            observed = self.capture.streamed_descriptor(source.fileno(), checksum, size)
        assert max(largest_write, largest_read) <= BLOCK_BYTES
        token = self.capture.prepare_upload(path, checksum, size, observed)
        assert token is not None, self.capture.last_reason
        final_path = path.with_suffix('.audio')
        path.rename(final_path)
        options = {} if isolated else {'stream_id': 'stream', 'stream_final': final}
        cid, created = self.store.add_chunk(
            self.sid, path.name, checksum, final_path, seconds, **options)
        assert wait(self.capture.finish_upload(
            token, self.sid, cid, final_path, created))['status'] == 'receipt_retained'
        self.source_ids.append(cid)
        self.source_blocks.append({'frames': end - start, 'file_bytes': size,
            'largest_write': largest_write, 'largest_hash_read': largest_read})
        self.sample_bounds()
        return cid


@pytest.fixture
def reset_factory(tmp_path, monkeypatch, request):
    created = []

    def make(seams=(300,)):
        root = tmp_path / f'reset-case-{len(created)}'
        root.mkdir()
        harness = object.__new__(StreamedHarness)
        try:
            harness.__init__(root, seams, monkeypatch)
        except BaseException:
            owner = getattr(harness, 'owner', None)
            if owner is not None:
                owner.close(.5)
                if owner._thread:
                    owner._thread.join(4)
                assert not owner._thread or not owner._thread.is_alive()
            raise
        created.append(harness)
        return harness

    yield make
    for index, harness in enumerate(reversed(created)):
        bounds_error = None
        try:
            harness.sample_bounds()
        except AssertionError as exc:
            bounds_error = exc
        finally:
            harness.close()
        assert not harness.owner._thread.is_alive()
        assert harness.worker.native is None or harness.worker.native.process is None
        report_dir = Path(os.environ.get(
            'RESET_REPLAY_REPORT_DIR', str(tmp_path.parent / 'reset-replay-evidence'))).resolve()
        assert not report_dir.is_relative_to(harness.root.resolve())
        report_dir.mkdir(parents=True, exist_ok=True)
        report = {**harness.evidence, 'test': request.node.nodeid,
            'source_blocks': harness.source_blocks,
            'peak_fixture_bytes': harness.peak_fixture_bytes,
            'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            'elapsed_seconds': round(time.monotonic() - harness.began, 3),
            'primary_commits': len(harness.commits),
            'core_prepare_calls': len(harness.core_actions),
            'core_extra_push_or_finish': sum(action in ('push', 'finish')
                for event in harness.core_actions for action in event['actions']),
            'owner_thread_closed': True, 'native_coordinator_closed': True,
            'bounds_ok': bounds_error is None, 'fixture_removed': False}
        report_name = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]
        destination = report_dir / f'{report_name}-{index}.json'
        raw = json.dumps(report, sort_keys=True, indent=2)
        assert len(raw.encode()) <= 32 * 1024
        destination.write_text(raw)
        shutil.rmtree(harness.root)
        report['fixture_removed'] = True
        destination.write_text(json.dumps(report, sort_keys=True, indent=2))
        if bounds_error is not None:
            raise bounds_error


def reset_rows(harness):
    return [row for row in harness.rows()
        if json.loads(row['body'])['part']['logical_start'] % (300 * RATE) == 0
        and json.loads(row['body'])['part']['logical_start'] > 0]


def reset_cells(harness):
    return [(item, doc) for item, doc in harness.cells() if doc['schema'] == core.RESET_SCHEMA]


def primary_is_unchanged(harness, total, *, abandoned_analyzers=0):
    assert_disjoint_cover([row['frames'] for row in harness.commits], 0, round(total * RATE))
    with harness.store.db() as db:
        texts = [row[0] for row in db.execute('SELECT text FROM segments')]
    assert Counter(texts) == Counter(marker[2] for marker in harness.markers)
    assert all(count == 1 for count in Counter(texts).values())
    assert len(harness.analyzer_reads) == len(harness.commits) + abandoned_analyzers


@pytest.mark.parametrize('boundary', [300, 3600], ids=['300', '3600'])
def test_actual_reset_replay_nonterminal_long_timeline(reset_factory, boundary):
    """No seek, synthetic prefix receipts, or rebased shortened hour fixture."""
    harness = reset_factory((boundary,))
    total = boundary + 44.5
    cid = harness.add(total)
    harness.run()
    cells = reset_cells(harness)
    assert len(cells) == 1, (harness.capture.health(), harness.rows())
    item, doc = cells[0]
    left, right = doc['commits']
    seam = boundary * RATE
    assert doc['schema'] == core.RESET_SCHEMA
    assert doc['policy'] == core.RESET_POLICY_SHA256 != core.POLICY_SHA256
    assert doc['geometry'] == {'seam_frame': seam,
        'start_frame': seam - 10 * RATE, 'end_frame': seam + 10 * RATE}
    assert [right['part']['logical_start'], right['part']['logical_end']] == [seam, seam + 30 * RATE]
    assert right['part']['logical_end'] < round(total * RATE)
    assert right['scope']['index'] == boundary // 300
    assert right['scope']['reset'] == left['scope']['reset'] + 1
    assert right['scope']['epoch'] != left['scope']['epoch']
    assert doc['query']['native']['true_eof'] is False
    assert doc['query']['native']['state']['finished'] is False
    assert doc['right_evidence']['native']['finished'] is False
    assert doc['query']['native']['epoch_id'] == right['scope']['epoch']
    assert all(turn['label'].startswith(right['scope']['epoch'] + ':')
        for turn in doc['query']['turns'])
    assert not any(turn['label'].startswith(left['scope']['epoch'] + ':')
        for turn in doc['query']['turns'])
    projection = doc['coverage']
    assert projection['kind'] == 'reset_replay_core_seam_alternative'
    assert projection['primary_scope_sha256'] == [sha(commit['scope']) for commit in (left, right)]
    assert projection['query_scope_sha256'] == sha(right['scope'])
    assert projection['scope_sha256'] == sha({'primary_scopes': [left['scope'], right['scope']],
        'query_scope': right['scope']})
    assert projection['adds_primary_audio'] is False
    assert projection['automatic_dispatch'] is False
    assert projection['primary_commit_refs'] == [core.anchor(left), core.anchor(right)]
    assert len(doc['dependencies']) == 1
    expected_pcm = pcm(seam - 10 * RATE, seam + 10 * RATE)
    assert doc['support']['pcm16le_sha256'] == hashlib.sha256(expected_pcm).hexdigest()
    read = wait(harness.owner.submit('read', item['cell_id']))
    assert read['status'] == 'available', read
    try:
        assert read['pin'].read() == expected_pcm
    finally:
        read['pin'].release()
    with Budget().phase() as budget:
        assert harness.authority.cell_valid(doc, budget, publishing=True)
    native = harness.worker.native.process
    assert len(native.resets) == boundary // 300 + 1
    assert len(set(native.resets)) == len(native.resets)
    assert len(harness.native_reads) == native.calls.count('push')
    assert native.calls.count('finish') == 1
    assert len(harness.capture.witness.ring) <= RING_PCM_BYTES
    assert all(len(event['actions']) <= 1 and set(event['actions']) <= {'turns'}
        for event in harness.core_actions)
    # The right receipt and alternative survive subsequent primary advancement.
    assert harness.store.chunk(cid)['next_offset'] == total
    assert harness.store.chunk(cid)['metadata']['core_cut']['actual'] == total
    assert doc['right_evidence']['core_cut']['actual'] == boundary + 30
    assert all(len(row['body']) <= core.COMPACT_BYTES for row in harness.rows())
    primary_is_unchanged(harness, total)
    harness.evidence.update(long_timeline_assertions_complete=True, boundary_seconds=boundary,
        support_seconds=[boundary - 10, boundary + 10], observed_epochs=len(native.resets),
        right_primary_seconds=[boundary, boundary + 30], right_native_true_eof=False,
        query_epoch=right['scope']['epoch'], primary_markers=len(harness.markers),
        proof_sha256=hashlib.sha256(canonical(doc)).hexdigest())


def test_reset_policy_and_fresh_negative_proof_copies(reset_factory):
    harness = reset_factory()
    harness.add(344.5)
    harness.run()
    _, doc = reset_cells(harness)[0]
    immutable = canonical(doc)
    with pytest.raises(Unavailable, match='core_seam_reset_unsupported'):
        core.geometry(doc['commits'])
    with pytest.raises(Unavailable, match='core_seam_reset_unsupported'):
        core.make_cell(doc['commits'], doc['query'], doc['right_evidence'], pcm(290 * RATE, 310 * RATE))
    rebuilt = core.make_cell(doc['commits'], doc['query'], doc['right_evidence'],
        pcm(290 * RATE, 310 * RATE), schema=core.RESET_SCHEMA)
    assert rebuilt.proof == immutable
    mutations = {
        'skipped_serial': lambda d: d['commits'][1]['scope'].__setitem__('reset', 4),
        'reversed_serial': lambda d: d['commits'][0]['scope'].__setitem__('reset', 3),
        'skipped_index': lambda d: d['commits'][1]['scope'].__setitem__('index', 2),
        'reversed_index': lambda d: d['commits'][0]['scope'].__setitem__('index', 2),
        'same_epoch_id': lambda d: d['commits'][1]['scope'].__setitem__('epoch', d['commits'][0]['scope']['epoch']),
        'changed_run': lambda d: d['commits'][1]['scope'].__setitem__('run', 'f' * 32),
        'changed_kind': lambda d: d['commits'][1]['scope'].__setitem__('kind', 'upload'),
        'changed_key': lambda d: d['commits'][1]['scope'].__setitem__('key', 'other-stream'),
        'changed_origin': lambda d: d['commits'][1]['scope'].__setitem__('origin', float(1).hex()),
        'runtime_mismatch': lambda d: d['commits'][0].__setitem__('runtime_sha256', '0' * 64),
        'source_mismatch': lambda d: d['commits'][1]['part'].__setitem__('source_sha256', '0' * 64),
        'old_epoch_query': lambda d: d['query']['native'].__setitem__('epoch_id', d['commits'][0]['scope']['epoch']),
        'old_epoch_label': lambda d: d['query']['turns'][0].__setitem__('label', d['commits'][0]['scope']['epoch'] + ':speaker_1'),
        'query_missing_left': lambda d: d['query'].__setitem__('query_start_frame', 300 * RATE),
        'replay_missing_left': lambda d: d['query']['native'].__setitem__('replay_start', 300 * RATE),
        'wrong_coverage_scope': lambda d: d['coverage'].__setitem__('query_scope_sha256', sha(d['commits'][0]['scope'])),
        'v3_schema_relabel': lambda d: d.update(schema=core.SCHEMA, policy=core.POLICY_SHA256),
    }
    for name, mutate in mutations.items():
        changed = deepcopy(doc)
        mutate(changed)
        with pytest.raises(Unavailable):
            core.validate_document(canonical(changed))
        assert canonical(doc) == immutable, name
    for side in (0, 1):
        changed = deepcopy(doc['commits'])
        part = changed[side]['part']
        if side == 0:
            part['source_start'] = part['source_end'] - 9 * RATE
            part['logical_start'] = part['logical_end'] - 9 * RATE
        else:
            part['source_end'] = part['source_start'] + 9 * RATE
            part['logical_end'] = part['logical_start'] + 9 * RATE
        with pytest.raises(Unavailable, match='core_reset_short_side'):
            core.geometry(changed, schema=core.RESET_SCHEMA)
    for side in (0, 1):
        changed = deepcopy(doc['commits'])
        part = changed[side]['part']
        if side == 0:
            part['source_end'] -= RATE
            part['logical_end'] -= RATE
        else:
            part['source_start'] += RATE
            part['logical_start'] += RATE
        with pytest.raises(Unavailable, match='core_reset_boundary'):
            core.geometry(changed, schema=core.RESET_SCHEMA)
    # A coherently rewritten processed/stable snapshot still cannot authorize
    # a right core whose future support has not actually been processed.
    changed = deepcopy(doc)
    observed = changed['query']['native']
    for key in ('processed', 'stable'):
        absolute = 309 * RATE
        frame = absolute - observed['replay_start']
        observed['state'][key] = {'raw': frame / RATE, 'hex': float(frame / RATE).hex(), 'frame': frame}
        changed['right_evidence']['native'][key + '_until'] = absolute / RATE
    right = changed['commits'][1]
    right['native_sha256'] = sha(changed['right_evidence']['native'])
    right['evidence_sha256'] = sha(changed['right_evidence'])
    with pytest.raises(Unavailable):
        core.validate_document(canonical(changed))
    assert canonical(reset_cells(harness)[0][1]) == immutable


@pytest.mark.parametrize('fault,reason', [
    ('missing_ring', 'native_ring_history_unavailable'),
    ('source_pcm', 'core_seam_source_pcm_mismatch'),
    ('missing_replay', 'core_reset_replay_missing'),
    ('cancel', 'core_seam_cancelled'),
])
def test_preparation_failures_preserve_real_primary(reset_factory, monkeypatch, fault, reason):
    harness = reset_factory()
    harness.add(344.5)
    original = harness.capture.core_seams.prepare

    def prepare(chunk, native, offset, end, result, coordinator, stop):
        if offset != 300:
            return original(chunk, native, offset, end, result, coordinator, stop)
        with monkeypatch.context() as patch:
            witness = harness.capture.witness
            if fault == 'missing_ring':
                patch.setattr(witness, 'ring_start', 300 * RATE)
            elif fault == 'source_pcm':
                original_pcm = harness.authority.original_pcm
                def mismatched(parts, budget):
                    answer = original_pcm(parts, budget)
                    if parts[0].logical_start == 290 * RATE and parts[-1].logical_end == 310 * RATE:
                        return bytes([answer[0] ^ 1]) + answer[1:]
                    return answer
                patch.setattr(harness.authority, 'original_pcm', mismatched)
            elif fault == 'missing_replay':
                prepared = witness.prepared
                def unavailable_replay(*args):
                    turns, observed = prepared(*args)
                    return turns, {**observed, 'replay_start': 300 * RATE}
                patch.setattr(witness, 'prepared', unavailable_replay)
            return original(chunk, native, offset, end, result, coordinator,
                (lambda: True) if fault == 'cancel' else stop)

    monkeypatch.setattr(harness.capture.core_seams, 'prepare', prepare)
    harness.run()
    row = reset_rows(harness)[0]
    assert row['reason'] == reason, row
    assert row['pending'] is None
    assert not reset_cells(harness)
    primary_is_unchanged(harness, 344.5)


def test_missing_left_receipt_is_not_invented(reset_factory, monkeypatch):
    harness = reset_factory()
    harness.add(344.5)
    original = harness.store.commit_window

    def commit(chunk, offset, end, result):
        if end == 300:
            # Omit this fresh optional packet before the real primary commit.
            result.pop('_native_core_seam', None)
        return original(chunk, offset, end, result)

    monkeypatch.setattr(harness.store, 'commit_window', commit)
    harness.run()
    assert reset_rows(harness)[0]['reason'] == 'core_first_or_unavailable_left'
    assert not reset_cells(harness)
    primary_is_unchanged(harness, 344.5)


def test_short_real_right_core_is_gap(reset_factory):
    harness = reset_factory()
    harness.add(302)
    harness.run()
    assert reset_rows(harness)[0]['reason'] == 'core_reset_short_side'
    assert reset_rows(harness)[0]['state'] == 'gap'
    assert not reset_cells(harness)
    primary_is_unchanged(harness, 302)


def test_restarted_capture_cannot_join_prior_run(reset_factory, monkeypatch):
    harness = reset_factory()
    cid = harness.add(344.5)
    original = harness.store.commit_window

    def crash_after_left(chunk, offset, end, result):
        answer = original(chunk, offset, end, result)
        if end == 300:
            raise InterruptedError('fixture crash after genuine left primary commit')
        return answer

    monkeypatch.setattr(harness.store, 'commit_window', crash_after_left)
    with pytest.raises(InterruptedError):
        harness.worker.process(harness.store.claim())
    monkeypatch.setattr(harness.store, 'commit_window', original)
    old_run = harness.capture.core_seams.run
    if harness.worker.native:
        harness.worker.native.close()
    harness.worker.native = None
    harness.capture = Capture(harness.owner, harness.authority)
    harness.worker.native_owned_cells = harness.capture
    assert harness.capture.core_seams.run != old_run
    harness.worker.process(harness.store.chunk(cid))
    harness.settle()
    cells = harness.cells()
    restarted = [doc for _, doc in cells if doc['schema'] == core.RESTART_SCHEMA]
    assert len(restarted) == 1
    with pytest.raises(Unavailable, match='core_seam_capture_domain_changed'):
        core.geometry(restarted[0]['commits'], schema=core.RESET_SCHEMA)
    assert not reset_cells(harness)
    primary_is_unchanged(harness, 344.5)


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_real_primary_crash_retry_preserves_exact_reset_outbox(reset_factory, monkeypatch, phase):
    harness = reset_factory()
    cid = harness.add(344.5)
    original = harness.store.commit_window

    def interrupted(chunk, offset, end, result):
        if offset == 300 and phase == 'before':
            raise InterruptedError('fixture crash before right primary transaction')
        answer = original(chunk, offset, end, result)
        if offset == 300:
            raise InterruptedError('fixture crash after right commit before acknowledgement')
        return answer

    monkeypatch.setattr(harness.store, 'commit_window', interrupted)
    with pytest.raises(InterruptedError):
        harness.worker.process(harness.store.claim())
    assert harness.store.chunk(cid)['next_offset'] == (300 if phase == 'before' else 330)
    proof = None
    if phase == 'before':
        assert not reset_rows(harness)
    else:
        proof = reset_rows(harness)[0]['pending']
        assert proof is not None
        harness.capture = Capture(harness.owner, harness.authority)
        harness.worker.native_owned_cells = harness.capture
        harness.settle()
        assert canonical(reset_cells(harness)[0][1]) == proof
    monkeypatch.setattr(harness.store, 'commit_window', original)
    harness.worker.process(harness.store.chunk(cid))
    harness.settle()
    if phase == 'before':
        # Failed preparation already consumed reset serial 2. Worker closes
        # native on the exception, so retry observes serial 3. The committed
        # left serial 1 must not be joined across that skipped observation.
        assert not reset_cells(harness)
        assert reset_rows(harness)[0]['reason'] == 'core_reset_transition'
    else:
        assert len(reset_cells(harness)) == 1
        assert reset_rows(harness)[0]['state'] == 'published'
        assert canonical(reset_cells(harness)[0][1]) == proof
    primary_is_unchanged(harness, 344.5, abandoned_analyzers=int(phase == 'before'))


def hold_reset_pending(harness, monkeypatch):
    original = harness.owner.submit
    with monkeypatch.context() as patch:
        patch.setattr(harness.owner, 'submit',
            lambda kind, payload: {'status': 'busy'} if kind == 'publish' else original(kind, payload))
        while (chunk := harness.store.claim()) is not None:
            harness.worker.process(chunk)
    row = reset_rows(harness)[0]
    assert row['state'] == 'pending' and row['pending'] is not None
    return row['pending']


def test_owner_commit_before_ack_retry_after_nonfinal_primary_advance(reset_factory, monkeypatch):
    harness = reset_factory()
    cid = harness.add(344.5)
    proof = hold_reset_pending(harness, monkeypatch)
    assert harness.store.chunk(cid)['next_offset'] == 344.5
    assert json.loads(proof)['commits'][1]['part']['source_end'] == 330 * RATE
    original = harness.owner._publish
    calls = []

    def interrupted(op, budget):
        result = original(op, budget)
        calls.append(result)
        if len(calls) == 1:
            raise RuntimeError('fixture owner commit completed before acknowledgement')
        return result

    monkeypatch.setattr(harness.owner, '_publish', interrupted)
    harness.settle()
    assert len(calls) >= 2
    assert canonical(reset_cells(harness)[0][1]) == proof
    assert reset_rows(harness)[0]['state'] == 'published'
    with sqlite3.connect(harness.owner.path) as db:
        assert db.execute('SELECT COUNT(*) FROM cells').fetchone()[0] == 1
    primary_is_unchanged(harness, 344.5)


@pytest.mark.parametrize('action', ['cancel', 'revoke', 'source_mutation'])
def test_pending_reset_cancelled_before_publication(reset_factory, monkeypatch, action):
    harness = reset_factory()
    cid = harness.add(344.5)
    hold_reset_pending(harness, monkeypatch)
    if action == 'cancel':
        with harness.store.db() as db:
            db.execute("UPDATE chunks SET status='cancelled' WHERE id=?", (cid,))
    elif action == 'revoke':
        harness.owner.revoked()
    else:
        with harness.store.db() as db:
            path = Path(db.execute('SELECT path FROM chunks WHERE id=?', (cid,)).fetchone()[0])
        with path.open('r+b') as source:
            source.seek(44 + 300 * RATE * 2)
            source.write(b'\x55\x66')
    harness.capture.core_seams.next_attempt = 0
    harness.capture.drain_core_seams()
    operation = harness.owner._slot
    if operation is not None:
        wait(operation)
    assert reset_rows(harness)[0]['state'] != 'published'
    if action != 'source_mutation':
        assert reset_rows(harness)[0]['state'] == 'cancelled'
    with sqlite3.connect(harness.owner.path) as db:
        assert db.execute('SELECT COUNT(*) FROM cells').fetchone()[0] == 0


def test_previous_v3_store_policy_not_adopted(reset_factory, monkeypatch):
    old_policy = sha({'schema': 'native-owned-cell-store-policy-v3',
        'accepted_cell_policies': sorted([V1_POLICY, V2_POLICY, core.POLICY_SHA256])})
    assert core.store_policy() != old_policy
    with monkeypatch.context() as patch:
        patch.setattr(core, 'store_policy', lambda: old_policy)
        harness = reset_factory((30,))
        harness.add(32)
        harness.run()
        original = harness.cells()[0][1]
        assert original['schema'] == core.SCHEMA
        relabeled = deepcopy(original)
        relabeled.update(schema=core.RESET_SCHEMA, policy=core.RESET_POLICY_SHA256)
        with pytest.raises(Unavailable, match='core_reset_transition'):
            core.validate_document(canonical(relabeled))
    previous = harness.owner
    previous.close(.5)
    previous._thread.join(4)
    assert not previous._thread.is_alive()
    harness.owner = CellStore(previous.directory, harness.authority, enabled=True,
        qualification=simulated_profile(harness.root), core_seam_enabled=True)
    harness.authority.owner = harness.owner
    harness.owner.start()
    harness.owner._thread.join(4)
    assert not harness.owner._ready.is_set()
    assert harness.owner.health()['status'] == 'optional_primary_binding_mismatch'
