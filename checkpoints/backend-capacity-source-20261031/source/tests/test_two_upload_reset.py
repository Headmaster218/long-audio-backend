"""One streamed hour, two uploads, then exact persisted-proof replay.

Uses dev12 fixtures; no product mutation, model, decoder or API service.
See docs/DEV12_TWO_UPLOAD_BOUNDARY_ZH.md for the recorded validation and scope.
"""
from copy import deepcopy
import hashlib
import json
import sqlite3
import wave

from audio_backend import native_owned_core_seams as core
from audio_backend.native_owned_cell_capture import Capture
from audio_backend.native_owned_cell_store import Budget, CellStore
from audio_backend.native_owned_cells import RATE, RING_PCM_BYTES, canonical, sha
from test_multicore_seam_coverage import assert_disjoint_cover, pcm
from test_native_owned_cell_store import simulated_profile
from test_reset_replay_core_seams import primary_is_unchanged, reset_factory
from test_same_epoch_core_seams import wait


BOUNDARY = 3600
TAIL = 44.5
TOTAL = BOUNDARY + TAIL
SEAM = BOUNDARY * RATE


def primary_snapshot(h):
    """Actual primary rows must stay byte-for-byte unchanged during replay."""
    with h.store.db() as db:
        return {table: [tuple(row) for row in db.execute(
            f'SELECT * FROM {table} ORDER BY id')]
            for table in ('chunks', 'segments', 'speaker_turns', 'events')}


def owned_snapshot(owner):
    """Read only this test's fresh optional database, never an earlier store."""
    with sqlite3.connect(owner.path.as_uri() + '?mode=ro', uri=True) as db:
        return {
            'cells': db.execute('SELECT * FROM cells ORDER BY seq').fetchall(),
            'dependencies': db.execute(
                'SELECT * FROM dependencies ORDER BY cell_id,position').fetchall(),
            'sources': db.execute('SELECT * FROM sources ORDER BY cid').fetchall(),
        }


def boundary_row(h):
    rows = [row for row in h.rows()
        if json.loads(row['body'])['part']['logical_start'] == SEAM]
    assert len(rows) == 1, h.rows()
    return rows[0]


def source_path(h, cid):
    # Store.chunk intentionally omits paths from its public projection.
    with h.store.db() as db:
        return db.execute('SELECT path FROM chunks WHERE id=?', (cid,)).fetchone()[0]


def assert_sources_and_proof(h, proof, source_ids, receipts):
    """Check both upload identities and their source-local byte intervals."""
    doc, _, parts = core.validate_document(proof)
    assert canonical(doc) == proof
    left, right = doc['commits']
    assert doc['schema'] == core.RESET_SCHEMA
    assert doc['policy'] == core.RESET_POLICY_SHA256 != core.POLICY_SHA256
    assert doc['geometry'] == {'seam_frame': SEAM,
        'start_frame': SEAM - 10 * RATE, 'end_frame': SEAM + 10 * RATE}
    assert [left['part']['logical_start'], left['part']['logical_end']] == [3570 * RATE, SEAM]
    assert [right['part']['logical_start'], right['part']['logical_end']] == [SEAM, 3630 * RATE]
    assert [left['part']['source_start'], left['part']['source_end']] == [3570 * RATE, SEAM]
    assert [right['part']['source_start'], right['part']['source_end']] == [0, 30 * RATE]
    assert [commit['part']['chunk_id'] for commit in (left, right)] == list(source_ids)
    assert right['part']['logical_end'] < round(TOTAL * RATE)
    assert [left['scope']['index'], right['scope']['index']] == [11, 12]
    assert [left['scope']['reset'], right['scope']['reset']] == [12, 13]
    assert right['scope']['epoch'] != left['scope']['epoch']
    for key, expected in (('session_id', h.sid), ('kind', 'stream'),
            ('key', 'stream'), ('origin', float(0).hex())):
        assert left['scope'][key] == right['scope'][key] == expected
    assert left['scope']['run'] == right['scope']['run']
    query = doc['query']
    assert query['query_start_frame'] <= SEAM - 10 * RATE
    assert query['query_end_frame'] >= SEAM + 10 * RATE
    assert query['native']['epoch_id'] == right['scope']['epoch']
    assert query['native']['epoch_index'] == 12
    assert query['native']['replay_start'] == SEAM - 339200
    assert query['native']['true_eof'] is False
    assert query['native']['state']['finished'] is False
    assert doc['right_evidence']['native']['finished'] is False
    assert doc['right_evidence']['core_cut']['actual'] == 30  # Upload-local.
    labels = {turn['label'] for turn in query['turns']}
    assert labels == {right['scope']['epoch'] + ':speaker_' + str(n) for n in (1, 2)}
    assert not any(label.startswith(left['scope']['epoch'] + ':') for label in labels)
    for frame in (SEAM - RATE // 8, SEAM + RATE // 8):
        assert {turn['label'] for turn in query['turns']
            if turn['original'][0] <= frame < turn['original'][1]} == labels
    coverage = doc['coverage']
    assert coverage['kind'] == 'reset_replay_core_seam_alternative'
    assert coverage['frame_range'] == [3590 * RATE, 3610 * RATE]
    assert coverage['primary_commit_refs'] == [core.anchor(left), core.anchor(right)]
    assert coverage['primary_scope_sha256'] == [sha(c['scope']) for c in (left, right)]
    assert coverage['query_scope_sha256'] == sha(right['scope'])
    assert coverage['scope_sha256'] == sha({'primary_scopes': [left['scope'], right['scope']],
        'query_scope': right['scope']})
    assert coverage['adds_primary_audio'] is False
    assert coverage['automatic_dispatch'] is False
    expected = []
    for cid, receipt, local, logical in zip(source_ids, receipts,
            ((3590 * RATE, SEAM), (0, 10 * RATE)),
            ((3590 * RATE, SEAM), (SEAM, 3610 * RATE))):
        assert h.owner.receipt(cid).encoded == receipt.encoded
        expected.append({'session_id': h.sid, 'chunk_id': cid,
            'receipt_sha256': receipt.receipt_id, 'source_sha256': receipt.digest,
            'source_frames': receipt.frames, 'source_start': local[0], 'source_end': local[1],
            'logical_start': logical[0], 'logical_end': logical[1]})
        # Independently read the exact local interval from each physical upload.
        with wave.open(source_path(h, cid), 'rb') as source:
            assert (source.getnchannels(), source.getsampwidth(), source.getframerate()) == (1, 2, RATE)
            assert source.getnframes() == receipt.frames
            source.setpos(local[0])
            assert source.readframes(local[1] - local[0]) == pcm(*logical)
    assert doc['dependencies'] == expected
    assert len({p['chunk_id'] for p in expected}) == 2
    assert len({p['receipt_sha256'] for p in expected}) == 2
    assert len({p['source_sha256'] for p in expected}) == 2
    data = pcm(3590 * RATE, 3610 * RATE)
    assert doc['support']['pcm16le_sha256'] == hashlib.sha256(data).hexdigest()
    with Budget().phase() as budget:
        assert h.authority.original_pcm(parts, budget) == data
        assert h.authority.cell_valid(doc, budget, publishing=True)
    return doc, data


def test_hour_reset_across_two_uploads_then_owned_restart_replays_exact_proof(reset_factory, monkeypatch):
    h = reset_factory((BOUNDARY,))
    source_ids = (h.add(BOUNDARY, final=False), h.add(TAIL, final=True))
    chunks = [h.store.chunk(cid) for cid in source_ids]
    assert source_ids[0] != source_ids[1]
    assert [c['timeline_start'] for c in chunks] == [0, BOUNDARY]
    assert [c['duration'] for c in chunks] == [BOUNDARY, TAIL]
    assert [c['stream_final'] for c in chunks] == [False, True]
    assert all(c['session_id'] == h.sid and c['stream_id'] == 'stream' for c in chunks)
    assert len({source_path(h, cid) for cid in source_ids}) == 2
    receipts = tuple(h.owner.receipt(cid) for cid in source_ids)
    assert all(receipt is not None for receipt in receipts)
    assert [r.frames for r in receipts] == [SEAM, round(TAIL * RATE)]
    assert len({r.signature[:2] for r in receipts}) == 2

    # Same publication hold as existing hold_pending/hold_reset_pending helpers.
    # Their row selectors assume a last/300s row, so select the 3600s row below.
    original_submit = h.owner.submit
    with monkeypatch.context() as patch:
        patch.setattr(h.owner, 'submit', lambda kind, payload:
            {'status': 'busy'} if kind == 'publish' else original_submit(kind, payload))
        while (chunk := h.store.claim()) is not None:
            h.worker.process(chunk)
    assert all(h.store.chunk(cid)['status'] == 'completed' for cid in source_ids)
    expected = [{'cid': source_ids[0], 'frames': [a * RATE, (a + 30) * RATE],
        'local': [a * RATE, (a + 30) * RATE]} for a in range(0, BOUNDARY, 30)]
    expected.extend({'cid': source_ids[1], 'frames': [round((BOUNDARY + a) * RATE),
        round((BOUNDARY + b) * RATE)], 'local': [round(a * RATE), round(b * RATE)]}
        for a, b in ((0, 30), (30, TAIL)))
    assert h.commits == expected
    primary_is_unchanged(h, TOTAL)
    assert len(h.markers) == 4
    assert len(h.rows()) == len(expected) == 122
    assert all(len(row['body']) <= core.COMPACT_BYTES for row in h.rows())
    row = boundary_row(h)
    assert [r['slot'] for r in h.rows() if r['state'] == 'pending'] == [row['slot']]
    proof = row['pending']
    assert type(proof) is bytes
    assert h.capture.core_seams.flight is None and h.owner._slot is None
    doc, data = assert_sources_and_proof(h, proof, source_ids, receipts)
    proof_run = doc['commits'][1]['scope']['run']
    assert h.capture.core_seams.run == proof_run
    assert h.store.chunk(source_ids[0])['metadata']['core_cut']['actual'] == BOUNDARY
    assert h.store.chunk(source_ids[1])['metadata']['core_cut']['actual'] == TAIL
    assert [h.store.chunk(cid)['next_offset'] for cid in source_ids] == [BOUNDARY, TAIL]
    with h.store.db() as db:
        turns = [dict(r) for r in db.execute('SELECT * FROM speaker_turns')]
        segments = [dict(r) for r in db.execute('SELECT * FROM segments')]
    for side, probe in enumerate((BOUNDARY - .125, BOUNDARY + .125)):
        epoch = doc['commits'][side]['scope']['epoch']
        assert {t['speaker_local_id'] for t in turns if t['start'] <= probe < t['end']} == {
            epoch + ':speaker_1', epoch + ':speaker_2'}
    for frame, voice, marker in h.markers:
        found = [s for s in segments if s['text'] == marker]
        assert len(found) == 1
        side = int(frame >= SEAM)
        assert found[0]['chunk_id'] == source_ids[side]
        assert found[0]['speaker_local_id'] == doc['commits'][side]['scope']['epoch'] + f':speaker_{voice}'
    assert any(w['frames'][0] < SEAM < w['frames'][1]
        and w['source_ids'] == list(source_ids) for w in h.windows.values())
    native = h.worker.native.process
    assert len(native.resets) == len(set(native.resets)) == 13
    assert native.calls.count('finish') == 1
    assert native.calls.count('push') == len(h.native_reads)
    assert h.capture.witness.failure is None
    assert len(h.capture.witness.ring) <= RING_PCM_BYTES
    assert len(h.core_actions) == 122
    assert all(len(event['actions']) <= 1 and set(event['actions']) <= {'turns'}
        for event in h.core_actions)
    replay_reads = [r['frames'] for r in h.native_reads
        if r['epoch'] == doc['commits'][1]['scope']['epoch']]
    assert_disjoint_cover(replay_reads, SEAM - 339200, round(TOTAL * RATE))
    before_primary = primary_snapshot(h)
    before_activity = deepcopy((h.commits, h.native_reads, h.analyzer_reads,
        h.core_actions, h.source_blocks, native.calls))
    h.evidence.update(primary_assertions_complete=True, boundary_seconds=BOUNDARY,
        upload_seconds=[BOUNDARY, TAIL], support_seconds=[3590, 3610],
        primary_markers=4, observed_epochs=13, right_primary_seconds=[3600, 3630],
        right_native_true_eof=False, proof_capture_run=proof_run,
        proof_sha256=hashlib.sha256(proof).hexdigest(), distinct_source_dependencies=2,
        source_local_support_seconds=[[3590, 3600], [0, 10]])

    # Deterministic commit-before-ack seam: finish the real owner operation,
    # but never call Capture.drain again to acknowledge its durable outcome.
    assert owned_snapshot(h.owner)['cells'] == []
    h.capture.core_seams.next_attempt = 0
    h.capture.drain_core_seams()
    flight = h.capture.core_seams.flight
    assert flight is not None
    slot_id, operation, cell_id, proof_hash = flight
    assert slot_id == row['slot'] and proof_hash == hashlib.sha256(proof).hexdigest()
    outcome = wait(operation)
    assert outcome == {'status': 'durable', 'cells': [{'cell_id': cell_id, 'status': 'retained'}]}
    assert boundary_row(h)['state'] == 'pending'
    assert boundary_row(h)['pending'] == proof
    before_owned = owned_snapshot(h.owner)
    assert len(before_owned['cells']) == 1
    assert len(before_owned['dependencies']) == len(before_owned['sources']) == 2
    assert not h.owner._pins and h.owner._slot is None
    h.evidence['owner_committed_before_outbox_ack'] = True

    # No primary preparation follows this restart. Only the already persisted
    # historical proof is eligible; a fresh run must not manufacture a join.
    old_owner = h.owner
    h.worker.native.close()
    h.worker.native = None
    old_owner.close(.5)
    old_owner._thread.join(4)
    assert not old_owner._thread.is_alive()
    assert old_owner._db is None and old_owner._slot is None and not old_owner._pins
    h.owner = CellStore(old_owner.directory, h.authority, enabled=True,
        qualification=simulated_profile(h.root), core_seam_enabled=True)
    h.authority.owner = h.owner
    h.owner.start()
    assert h.owner._ready.wait(4), h.owner.health()
    h.capture = Capture(h.owner, h.authority)
    h.worker.native_owned_cells = h.capture
    assert h.capture.core_seams.run != proof_run
    assert h.capture.observed_reset_generation == 0
    assert boundary_row(h)['pending'] == proof
    publications = []
    original_publish = h.owner._publish

    def observe_existing(op, budget):
        raw = tuple(cell.proof for cell in op.payload)
        result = original_publish(op, budget)
        publications.append((raw, deepcopy(result)))
        return result

    monkeypatch.setattr(h.owner, '_publish', observe_existing)
    h.settle()
    assert publications == [((proof,), {'status': 'durable',
        'cells': [{'cell_id': cell_id, 'status': 'existing'}]})]
    assert owned_snapshot(h.owner) == before_owned
    assert boundary_row(h)['state'] == 'published'
    assert boundary_row(h)['pending'] is None
    assert not any(r['state'] == 'pending' or r['pending'] is not None for r in h.rows())
    cells = h.cells()
    assert len(cells) == 1
    assert cells[0][0]['cell_id'] == cell_id and canonical(cells[0][1]) == proof
    assert_sources_and_proof(h, proof, source_ids, receipts)
    read = wait(h.owner.submit('read', cell_id))
    assert read['status'] == 'available', read
    try:
        assert read['pin'].read() == data
    finally:
        read['pin'].release()
    assert read['pin'].release_done.wait(4)
    # A further drain must be a no-op, rather than another successful replay.
    h.capture.core_seams.next_attempt = 0
    h.capture.drain_core_seams()
    assert len(publications) == 1
    assert h.capture.health()['core_seam_pending'] is False
    assert h.capture.core_seams.flight is None and h.owner._slot is None
    assert not h.owner._pins and not old_owner._pins
    assert primary_snapshot(h) == before_primary
    assert (h.commits, h.native_reads, h.analyzer_reads, h.core_actions,
        h.source_blocks, native.calls) == before_activity
    assert h.worker.native is None and h.capture.observed_reset_generation == 0
    primary_is_unchanged(h, TOTAL)
    h.sample_bounds()
    h.evidence.update(long_timeline_assertions_complete=True,
        persisted_proof_replay_assertions_complete=True, replay_result='existing',
        replay_capture_run=h.capture.core_seams.run, replay_native_asr_calls=0,
        final_pending=0, final_pins=0, original_owner_thread_closed=True)
