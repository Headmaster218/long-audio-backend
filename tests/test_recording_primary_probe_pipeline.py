"""Prospective model-free Worker -> bounded proof -> exact live lease contracts.

The Worker, recording API, Capture, receipts, immutable commits, SQLite, owner
publication and CellJobsSource are real. Only PCM, native responses, ASR results
and 512-dimensional vector values are synthetic. No old audio is restored and
no embedding/MOSS model or model child is launched. Scoring assertions are
mechanical evidence contracts, not acoustic qualification.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import wave

import pytest

from audio_backend import lexical_evidence as lexical
from audio_backend import moss_automatic as automatic
from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_primary_probe as probe
from audio_backend import recording_primary_provenance as provenance
from audio_backend import recording_speaker_links as links
from audio_backend.native_owned_cell_jobs import CellJobsSource, CellLease
from audio_backend.native_owned_cell_store import Budget, Operation, SQL_PROJECTION_BYTES
from audio_backend.native_owned_cell_capture import PrimaryAuthority
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.settings import Settings
from audio_backend.worker import Worker
from test_moss_automatic_integration import automatic_app_factory, ingest_recording, process_primary
from test_native_owned_support_api import FakeAnalyzer
from test_recording_primary_pipeline import _extract
from test_recording_speaker_links import projection, vector

RATE = cells.RATE


def _source_path(h, cid):
    # The public chunk projection intentionally omits its private file path.
    with h.store.db() as db:
        row = db.execute('SELECT path FROM chunks WHERE id=?', (cid,)).fetchone()
    assert row is not None
    return Path(row['path'])


def _pcm():
    # Different exact PCM in each disjoint probe region; equal constant PCM
    # would correctly collapse all three observations as physical duplicates.
    return b'\x01\0' * (4 * RATE) + b'\x02\0' * (3 * RATE) + b'\x03\0' * (3 * RATE)


def _wav(frames=10 * RATE):
    assert frames == 10 * RATE
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(RATE)
        target.writeframes(_pcm())
    return output.getvalue()


def _turns(frames, epoch):
    assert frames == 10 * RATE
    return [{'start': a, 'end': b, 'speaker_local_id': epoch + ':speaker_' + str(label)}
        for a, b, label in ((0., 4., 1), (.125, 4., 1), (3., 3.25, 2),
                            (4., 7., 3), (7., 10., 4))]


def _analyze(analyzer, path, **kwargs):
    native = kwargs['diarization']
    assert (native['extract_start'], native['extract_end']) == (0., 10.)
    label = native['state']['epoch_id'] + ':speaker_1'
    # An opaque two-character unit remains whole. Its start has one native
    # assignment; its long endpoint forces a real prospective bounded closure.
    definitions = ((.5, 10., '甲乙', label), (3.125, 3.1875, '丙丁', None))
    segments = []
    for start, end, text, assigned in definitions:
        segments.append({'start': start, 'end': end, 'text': text, 'raw_text': text,
            'speaker_local_id': assigned, 'source_track_id': assigned, 'confidence': 0.,
            'asr_backend': 'sensevoice', 'timestamp_kind': lexical.PRIMARY_TIMESTAMP,
            '_units': [{'start': start, 'end': end, 'ownership_anchor': start,
                'anchor_kind': 'ctc_emission', 'text': text, 'raw_text': text}]})
    return {'segments': segments, 'speaker_turns': deepcopy(native['turns']), 'events': [],
        'metadata': {'asr_backend': 'sensevoice', 'timestamp_semantics': lexical.PRIMARY_TIMESTAMP,
                     'diarization_turns': deepcopy(native['turns'])}}


@pytest.fixture
def primary_probe_app_factory(automatic_app_factory, monkeypatch):
    post_init = Settings.__post_init__

    def configure(settings):
        if settings.moss_automatic_enabled:
            settings.asr_backend = 'sensevoice'
            settings.lexical_evidence_enabled = True
            settings.native_owned_bounded_core_support_enabled = True
            settings.recording_speaker_links_enabled = True
            settings.recording_speaker_model_path = str(Path(settings.data_dir) / 'ABSENT-SYNTHETIC-SPEAKER-MODEL')
            settings.window_seconds = 5.
            settings.context_seconds = 5.
        post_init(settings)

    monkeypatch.setattr(Settings, '__post_init__', configure)
    monkeypatch.setattr(Worker, 'extract_pcm', _extract)
    monkeypatch.setattr(FakeAnalyzer, 'analyze', _analyze)
    monkeypatch.setattr('test_native_owned_support_api.synthetic_turns', _turns)
    monkeypatch.setattr('test_native_owned_support_api.wav_bytes', _wav)
    return automatic_app_factory


def _bound_intent(h):
    with h.store.db() as db:
        rows = [dict(row) for row in db.execute(
            f'SELECT * FROM {automatic.TABLE} WHERE sid=? ORDER BY seq', (h.sid,))]
    selected = [row for row in rows if row['plan_phase'] == 'bound'
                and row['proof'] is not None
                and json.loads(row['proof']).get('schema') == core.BOUNDED_SCHEMA]
    assert selected, [(r['state'], r['plan_phase'], r['reason']) for r in rows]
    # Original and seam aliases may share the exact target; select the owner
    # whose persisted numeric support exposes the whole opaque primary row.
    selected = [row for row in selected
                if json.loads(row['proof'])['support']['start_frame'] == RATE // 2
                and json.loads(row['proof'])['support']['end_frame'] == 10 * RATE]
    assert len(selected) == 1
    return selected[0]


def _produce(h):
    cid = ingest_recording(h, frames=10 * RATE)
    assert process_primary(h) == cid
    assert h.store.chunk(cid)['status'] == 'completed'
    assert h.store.chunk(cid)['next_offset'] == 10.
    assert not h.drivers and h.jobs._live is None
    assert h.capture.analyzer is None
    assert not Path(h.store.settings.recording_speaker_model_path).exists()
    intent = _bound_intent(h)
    with h.store.db() as db:
        db.execute('BEGIN')
        saved = provenance.read_exact(db, cid=cid, start=0, end=5 * RATE)
        assert saved is not None
        assert provenance.read_exact(db, cid=cid, start=5 * RATE, end=10 * RATE) is None
    return cid, intent, saved


@contextmanager
def _live_lease(h, intent):
    """Use current original PCM and real optional owner publication, no stub."""
    doc, support, parts = cells.validate_document(intent['proof'])
    source = h.jobs.cell_source
    assert type(source) is CellJobsSource
    assert type(source.owner.authority) is PrimaryAuthority
    with Budget().phase() as budget:
        assert automatic.authority_valid(h.capture.authority, intent['candidate_key'], doc, budget)
        pcm = h.capture.authority.original_pcm(parts, budget)
    anchor = core.anchor(doc['commits'][-1])
    cell = cells.Cell(support, parts, intent['proof'], pcm, h.sid,
        anchor['chunk_id'], anchor['start'], anchor['end'], anchor['sha256'])
    operation = h.owner.submit('publish_auto', (intent['candidate_key'], cell))
    assert type(operation) is Operation
    assert operation.slot_released.wait(3.), operation.result
    assert operation.result['status'] == 'durable', operation.result
    detail = source.detail(h.sid, cell.cell_id)
    assert detail['pcm_retained']
    request = {'expected_cell_sequence': detail['sequence'],
        'expected_cell_revision': detail['revision'],
        'expected_proof_sha256': hashlib.sha256(intent['proof']).hexdigest(),
        'support_id': support.support_id, 'coverage_intent': 'overlapping_diagnostic'}
    lease = source.acquire_dispatch(h.sid, cell.cell_id, request)
    assert type(lease) is CellLease
    try:
        assert source.revalidate_dispatch(lease.token)
        assert lease.pcm == _pcm()[RATE:]
        yield source, lease
    finally:
        assert source.abandon(lease)
        assert not h.owner._pins


def _read(h, lease, source):
    with h.store.db() as db:
        db.execute('BEGIN')
        packets = probe.read_named(db, lease.token.proof, source)
        assert probe.current_packets(db, lease.token.proof, packets, source)
        return packets


def _batch(lease, packets, *, competitor=True):
    phrases = '[4.1] [S01] Matching synthetic observation. [6.9]'
    if competitor:
        phrases += ' [7.1] [S02] Independent synthetic competitor. [9.9]'
    # Adapter time is support-relative; support starts at .5 s.
    phrases = phrases.replace('[4.1]', '[3.6]').replace('[6.9]', '[6.4]')
    phrases = phrases.replace('[7.1]', '[6.6]').replace('[9.9]', '[9.4]')
    batch, crops = links.capture_candidates_v2(lease.support, lease.pcm, lease.token.proof,
        projection(lease.support, phrases), 'prospective-synthetic-job', '1' * 64,
        links.expected_space(), primary_packets=packets)
    for node in batch['nodes']:
        node['vector'] = (vector(1., 0.) if node['source_kind'] == 'native_primary'
            else vector(.98, .20) if node['phrase_index'] == 0 else vector(0., 1.))
    links.validate_batch(batch)
    return batch, crops


def test_real_prospective_packets_live_lease_and_three_observation_acceptance(primary_probe_app_factory):
    with primary_probe_app_factory() as h:
        cid, intent, saved = _produce(h)
        with _live_lease(h, intent) as (source, lease):
            packets = _read(h, lease, source)
            assert packets == [saved]
            assert len(probe.named_commits(lease.token.proof)) == 2
            batch, crops = _batch(lease, packets)
            assert len(batch['nodes']) == len(crops) == 3
            assert {n['source_kind'] for n in batch['nodes']} == {'native_primary', 'moss'}
            assert len({n['pcm16le_sha256'] for n in batch['nodes']}) == 3
            assert [(n['start_frame'], n['end_frame']) for n in batch['nodes']] == [
                (12000, 44000), (72000, 104000), (120000, 152000)]
            answer = links.reconcile_batches([batch])
            assert answer['state'] == 'provisional_linked'
            assert answer['diagnostics']['independent_node_count'] == 3
            assert answer['diagnostics']['source_overlap_pair_count'] == 0
            assert answer['diagnostics']['linked_cluster_count'] == 1
            native = next(a for a in answer['annotations'] if a['source_kind'] == 'native_primary')
            moss = next(a for a in answer['annotations'] if a.get('phrase_index') == 0)
            other = next(a for a in answer['annotations'] if a.get('phrase_index') == 1)
            assert native['status'] == moss['status'] == 'provisional_linked'
            assert native['provisional_speaker_id'] == moss['provisional_speaker_id']
            assert native['primary_source_ref'] == saved['packet']['refs'][0][0]
            assert native['original_parent_ordinal'] == 0
            assert native['original_unit_ordinals'] == [[0, 0]]
            assert native['original_turn_ordinals'] == [0, 1]
            assert 'phrase_index' not in native and 'moss_tag' not in native
            assert other['status'] == 'source_scoped' and other['provisional_speaker_id'] is None
            assert all(a['speaker_identity_valid'] is False for a in answer['annotations'])
            assert source.revalidate_dispatch(lease.token)
        assert not h.drivers and h.jobs._live is None


def test_same_real_evidence_two_observations_abstains(primary_probe_app_factory):
    with primary_probe_app_factory() as h:
        _, intent, _ = _produce(h)
        with _live_lease(h, intent) as (source, lease):
            batch, crops = _batch(lease, _read(h, lease, source), competitor=False)
            assert len(crops) == len(batch['nodes']) == 2
            answer = links.reconcile_batches([batch])
            assert answer['state'] == 'source_scoped'
            assert answer['diagnostics']['round_reasons']['missing_runner_up'] == 2
            assert all(a['provisional_speaker_id'] is None for a in answer['annotations'])


def test_retained_packets_restart_requires_fresh_live_authority(primary_probe_app_factory, monkeypatch):
    with primary_probe_app_factory() as h:
        cid, intent, saved = _produce(h)
        sid = h.sid
        with _live_lease(h, intent) as (source, lease):
            assert _read(h, lease, source) == [saved]
            prior_epoch = source.epoch

    def no_reconstruction(*args, **kwargs):
        raise AssertionError('Historical current reads cannot reconstruct producer evidence')

    monkeypatch.setattr(provenance, 'observe', no_reconstruction)
    monkeypatch.setattr(provenance, 'freeze', no_reconstruction)
    monkeypatch.setattr(FakeAnalyzer, 'analyze', no_reconstruction)
    with primary_probe_app_factory(sid=sid) as h:
        assert not h.natives and h.worker.native is None
        assert h.jobs.cell_source.epoch != prior_epoch
        current_intent = _bound_intent(h)
        assert current_intent['proof'] == intent['proof']
        with _live_lease(h, current_intent) as (source, lease):
            assert _read(h, lease, source) == [saved]
        assert not h.drivers and h.jobs._live is None
        assert h.store.chunk(cid)['next_offset'] == 10.


@pytest.mark.parametrize('mutation', ['row', 'mapping', 'core', 'file', 'receipt_generation', 'packet'])
def test_fresh_current_boundary_refuses_each_real_mutation(primary_probe_app_factory, mutation):
    with primary_probe_app_factory() as h:
        cid, intent, _ = _produce(h)
        with _live_lease(h, intent) as (source, lease):
            packets = _read(h, lease, source)
            if mutation == 'file':
                path = _source_path(h, cid)
                with path.open('r+b') as target:
                    target.seek(44); target.write(b'\x04\0')
            elif mutation == 'receipt_generation':
                # A generation change invalidates an already-open boundary and
                # the exact lease, even when its packet was deduplicated there.
                with h.store.db() as db:
                    db.execute('BEGIN')
                    with pytest.raises(cells.Unavailable, match='primary_probe_authority_changed'):
                        with probe.packet_boundary(source) as boundary:
                            assert probe.current_packets(db, lease.token.proof, packets, source, boundary=boundary)
                            h.owner.revoked()
                            probe.current_packets(db, lease.token.proof, packets, source, boundary=boundary)
                with pytest.raises(ReadError, match='cell_revoked'):
                    source.revalidate_dispatch(lease.token)
                return
            elif mutation == 'packet':
                packets = deepcopy(packets)
                packets[0]['packet']['owned_pcm16le_sha256'] = 'f' * 64
            else:
                with h.store.db() as db:
                    if mutation == 'row':
                        ident = int(packets[0]['packet']['refs'][0][0].split('.')[1])
                        assert db.execute('UPDATE segments SET text=? WHERE id=?', ('changed', ident)).rowcount == 1
                    elif mutation == 'mapping':
                        assert db.execute('UPDATE chunks SET timeline_start=timeline_start+? WHERE id=?',
                            (1 / RATE, cid)).rowcount == 1
                    else:
                        assert db.execute('DELETE FROM native_core_commits WHERE slot=?',
                            (packets[0]['header']['core_slot'],)).rowcount == 1
            with h.store.db() as db:
                db.execute('BEGIN')
                if mutation in ('packet', 'receipt_generation', 'file'):
                    try:
                        accepted = probe.current_packets(db, lease.token.proof, packets, source)
                    except (cells.Unavailable, ReadError, ValueError):
                        accepted = False
                    assert accepted is False
                else:
                    assert not probe.current_packets(db, lease.token.proof, packets, source)


def test_named_read_admits_sizes_before_body_and_never_scans_packet_inventory(
        primary_probe_app_factory, monkeypatch):
    with primary_probe_app_factory() as h:
        _, intent, saved = _produce(h)
        with _live_lease(h, intent) as (source, lease):
            statements = []
            with h.store.db() as db:
                db.execute('BEGIN'); db.set_trace_callback(statements.append)
                assert probe.read_named(db, lease.token.proof, source) == [saved]
            packet_reads = [s for s in statements if provenance.TABLE in s]
            assert packet_reads
            assert all(' WHERE ' in s and ('cid=' in s or 'sequence=' in s) for s in packet_reads)
            assert not any('ORDER BY' in s or 'OFFSET' in s for s in packet_reads)
            reads = []
            read_exact = provenance.read_exact
            def observed(*args, **kwargs):
                reads.append(kwargs); return read_exact(*args, **kwargs)
            monkeypatch.setattr(provenance, 'read_exact', observed)
            monkeypatch.setattr(probe, 'MAX_PACKET_BYTES', len(provenance.bounded(saved['packet'])) - 1)
            with h.store.db() as db:
                db.execute('BEGIN')
                assert probe.read_named(db, lease.token.proof, source) == []
            assert reads == []


def test_one_real_budget_per_boundary_dedups_only_within_boundary_and_refuses_exhaustion(
        primary_probe_app_factory, monkeypatch):
    with primary_probe_app_factory() as h:
        _, intent, _ = _produce(h)
        with _live_lease(h, intent) as (source, lease):
            packets = _read(h, lease, source)
            budgets, authority_calls = [], []
            real_budget, real_current = probe.Budget, probe._source_current
            def budget_factory():
                value = real_budget(); budgets.append(value); return value
            def current(*args, **kwargs):
                authority_calls.append(args[-1]); return real_current(*args, **kwargs)
            monkeypatch.setattr(probe, 'Budget', budget_factory)
            monkeypatch.setattr(probe, '_source_current', current)
            for _ in range(2):
                with h.store.db() as db:
                    db.execute('BEGIN')
                    with probe.packet_boundary(source) as boundary:
                        assert probe.current_packets(db, lease.token.proof, packets, source, boundary=boundary)
                        assert probe.current_packets(db, lease.token.proof, deepcopy(packets), source, boundary=boundary)
            assert len(budgets) == len(authority_calls) == 2
            assert authority_calls == budgets and budgets[0] is not budgets[1]
            assert all(budget.sql_bytes > len(provenance.bounded(packets[0]['packet'])) for budget in budgets)
            with h.store.db() as db:
                db.execute('BEGIN')
                with pytest.raises(cells.Unavailable, match='operation_work_capacity'):
                    with probe.packet_boundary(source) as boundary:
                        boundary.budget.sql_bytes = SQL_PROJECTION_BYTES
                        probe.current_packets(db, lease.token.proof, packets, source, boundary=boundary)
            assert len(budgets) == 3 and len(authority_calls) == 2


# These store cases retain the prospective fixture's real source authority.
# Only the already-committed synthetic MOSS text/history is inserted directly;
# native packets, primary rows, source receipts and bounded proof are produced
# above by Worker/Capture and are never fabricated for ledger acceptance.
from types import SimpleNamespace
from audio_backend import moss_result_store as history
from audio_backend import recording_speaker_link_store as ledger
from test_moss_result_store import observed_connections
from test_recording_speaker_link_store import _insert_committed_history


def _ledger_evidence(h):
    cid, intent, saved = _produce(h)
    jid = 'd' * 32
    with _live_lease(h, intent) as (source, lease):
        value = h.jobs.speaker_links
        assert value._primary_source is source
        packets = value.capture_primary_packets(lease.token.proof)
        assert packets == [saved]
        result = projection(lease.support,
            '[3.6] [S01] Matching synthetic observation. [6.4] '
            '[6.6] [S02] Independent synthetic competitor. [9.4]')
        result['cell_binding'] = lease.token.projection()
        digest = hashlib.sha256(history.encoded(result)).hexdigest()
        batch, crops = links.capture_candidates_v2(lease.support, lease.pcm,
            lease.token.proof, result, jid, digest, links.expected_space(), primary_packets=packets)
        assert len(crops) == len(batch['nodes']) == 3
        metadata = {'cell_binding': result['cell_binding'], 'source_support': lease.support.to_dict(),
            'identity': {'job_id': jid, 'session_id': h.sid, 'original_jobs_epoch': 'e' * 32,
                'canonical_request_sha256': 'f' * 64, 'idempotency_key': jid},
            'context': {'origin': 'durable_cell', 'automatic_candidate_key': intent['candidate_key'],
                'support_id': lease.support.support_id}}
    assert lease.released and not h.owner._pins
    # The exact current source object survives release; it must perform fresh
    # receipt/file/row/mapping checks at each ledger boundary without PCM pins.
    assert value._primary_source is h.jobs.cell_source
    h.automatic.note(intent['candidate_key'], 'synthetic_history_only', published=True)
    identity = SimpleNamespace(session_id=h.sid, job_id=jid, original_jobs_epoch='e' * 32,
                               canonical_request_sha256='f' * 64)
    with h.store.db() as db:
        assert _insert_committed_history(db, h.sid, jid, result, metadata) == digest
        automatic.admit(db, intent['candidate_key'], identity)
        automatic.complete(db, intent['candidate_key'], identity)
        rid = db.execute('SELECT batch_id FROM recording_work WHERE chunk_id=?', (cid,)).fetchone()[0]
    assert value.recording_for_job(h.sid, jid, digest) == rid
    embedded = deepcopy(batch)
    for node in embedded['nodes']:
        node['vector'] = (vector(1., 0.) if node['source_kind'] == 'native_primary'
            else vector(.98, .20) if node['phrase_index'] == 0 else vector(0., 1.))
    assert not h.drivers and h.jobs._live is None
    return SimpleNamespace(h=h, sid=h.sid, rid=rid, cid=cid, jid=jid, digest=digest,
        batch=batch, embedded=embedded, packets=packets, value=value, result=result, intent=intent)


def _reserve_evidence(f):
    return f.value.reserve(f.sid, f.rid, f.jid, f.digest, links.POLICY_SHA256_V2,
        links.expected_space(), max_nodes=len(f.batch['nodes']), batch=f.batch)


def _finish_evidence(f):
    admission = _reserve_evidence(f)
    assert admission['should_probe'] and admission['token'] is not None
    f.value.commit(admission, f.embedded)
    return admission


def _ledger_state(f):
    with f.h.store.db() as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table
            + (' ORDER BY scope_seq,batch_seq' if table == ledger.BATCHES else ' ORDER BY scope_seq'))]
            for table in (ledger.SCOPES, ledger.BATCHES)}


def _decision_row(f, scope):
    with f.h.store.db() as db:
        row = db.execute(f'SELECT decision_json,decision_sha256 FROM {ledger.SCOPES} WHERE scope_id=?',
                         (scope,)).fetchone()
        return tuple(row)


def _change_real_evidence(f, mutation, monkeypatch=None):
    if mutation == 'file':
        with _source_path(f.h, f.cid).open('r+b') as target:
            target.seek(44); target.write(b'\x04\0')
        return
    if mutation == 'receipt_unavailable':
        receipt = f.h.owner.receipt(f.cid)
        assert receipt is not None
        assert receipt.receipt_id == f.packets[0]['packet']['commit']['part']['receipt_sha256']
        with Budget().phase() as budget:
            assert f.h.capture.authority.receipt_valid(receipt, budget)
        # Remove the actual immutable receipt from the current owner's
        # registry; retain its durable audit and all packet/row/file bytes.
        assert f.h.owner._receipts.pop(f.cid) is receipt
        assert f.h.owner.receipt(f.cid) is None
        return
    if mutation == 'owner_generation':
        assert monkeypatch is not None
        original = probe._packet_current
        observed = []

        def revoke_after_real_check(db, saved, source, fence, budget):
            assert source is f.h.jobs.cell_source
            accepted = original(db, saved, source, fence, budget)
            assert accepted is True
            assert observed == []
            before = f.h.owner._epoch
            f.h.owner.revoked()
            assert f.h.owner._epoch is not before
            observed.append(saved['header']['digest'])
            return accepted

        # Change real authority during this ledger operation. Revocation
        # before a fresh operation is different: retained history may then be
        # revalidated using the new owner generation.
        monkeypatch.setattr(probe, '_packet_current', revoke_after_real_check)
        return observed
    with f.h.store.db() as db:
        if mutation == 'mapping':
            changed = db.execute('UPDATE chunks SET timeline_start=timeline_start+? WHERE id=?',
                                 (1 / RATE, f.cid)).rowcount
        elif mutation == 'primary_ref':
            ident = int(f.packets[0]['packet']['refs'][0][0].split('.')[1])
            changed = db.execute('UPDATE segments SET raw_text=? WHERE id=?', ('changed primary ref', ident)).rowcount
        elif mutation == 'core_deleted':
            changed = db.execute('DELETE FROM native_core_commits WHERE slot=?',
                                 (f.packets[0]['header']['core_slot'],)).rowcount
        else:
            assert mutation == 'core_cancelled'
            changed = db.execute("UPDATE native_core_commits SET state='cancelled' WHERE slot=?",
                                 (f.packets[0]['header']['core_slot'],)).rowcount
        assert changed == 1


def _current_session_revision(f):
    with f.h.store.db() as db:
        return db.execute('SELECT transcript_revision FROM sessions WHERE id=?', (f.sid,)).fetchone()[0]


def test_real_primary_ledger_reserve_commit_snapshot_recompute_and_publish(primary_probe_app_factory):
    with primary_probe_app_factory() as h:
        f = _ledger_evidence(h)
        admission = _finish_evidence(f)
        scope = admission['scope_id']
        snapshot = f.value.snapshot(scope)
        assert snapshot['batches'] == snapshot['evidence_batches'] == [f.embedded]
        assert snapshot['inflight'] is False and not h.owner._pins
        expected = links.reconcile_batches(snapshot['evidence_batches'])
        recomputed = f.value.recompute_scope(scope, links.reconcile_batches)
        assert recomputed['saved'] and recomputed['state'] == 'provisional_linked'
        saved = f.value.read_saved(f.sid, f.rid)
        assert saved['annotations'] == expected['annotations']
        assert saved['diagnostics']['independent_node_count'] == 3
        raw, digest = _decision_row(f, scope)
        assert hashlib.sha256(raw).hexdigest() == digest == saved['decision_sha256']
        assert f.value.save_decision(scope, snapshot['revision'], json.loads(raw),
            session_revision=snapshot['session_revision'], policy_sha256=links.POLICY_SHA256_V2)
        native = next(a for a in saved['annotations'] if a['source_kind'] == 'native_primary')
        node = next(n for n in f.embedded['nodes'] if n['source_kind'] == 'native_primary')
        assert {k: v for k, v in native.items() if k not in ('status', 'provisional_speaker_id',
            'reason', 'duplicate_evidence', 'speaker_identity_valid')} == {
            k: v for k, v in node.items() if k not in ('vector', 'source_spans', 'primary_commit')}
        assert native['status'] == 'provisional_linked' and native['speaker_identity_valid'] is False
        assert not h.owner._pins and not h.drivers and h.jobs._live is None


@pytest.mark.parametrize('boundary', ['reserve', 'commit', 'snapshot', 'decision'])
@pytest.mark.parametrize('mutation', ['file', 'mapping', 'primary_ref', 'core_cancelled',
                                     'receipt_unavailable', 'owner_generation', 'core_deleted'])
def test_real_primary_authority_mutation_at_every_ledger_boundary(
        primary_probe_app_factory, monkeypatch, boundary, mutation):
    with primary_probe_app_factory() as h:
        f = _ledger_evidence(h)
        admission = None
        if boundary != 'reserve':
            admission = _reserve_evidence(f)
            assert admission['should_probe']
        if boundary in ('snapshot', 'decision'):
            f.value.commit(admission, f.embedded)
        if boundary == 'decision':
            snapshot = f.value.snapshot(admission['scope_id'])
            decision = links.reconcile_batches(snapshot['evidence_batches'])
            assert decision['state'] == 'provisional_linked'
        before = _ledger_state(f)
        observed = _change_real_evidence(f, mutation, monkeypatch)
        if boundary == 'reserve':
            with pytest.raises(ReadError):
                _reserve_evidence(f)
        elif boundary == 'commit':
            with pytest.raises(ReadError):
                f.value.commit(admission, f.embedded)
        elif boundary == 'snapshot':
            with pytest.raises(ReadError):
                f.value.snapshot(admission['scope_id'])
        else:
            current = _current_session_revision(f)
            if current != snapshot['session_revision']:
                assert f.value.save_decision(admission['scope_id'], snapshot['revision'], decision,
                    session_revision=snapshot['session_revision'], policy_sha256=links.POLICY_SHA256_V2) is False
            # Updating the optimistic revision cannot bless changed source
            # evidence. The publication boundary must independently refuse.
            with pytest.raises(ReadError):
                f.value.save_decision(admission['scope_id'], snapshot['revision'], decision,
                    session_revision=current, policy_sha256=links.POLICY_SHA256_V2)
        assert _ledger_state(f) == before
        if mutation == 'owner_generation':
            assert observed == [f.packets[0]['header']['digest']]
        assert not h.owner._pins and not h.drivers and h.jobs._live is None


def test_reserve_refuses_valid_rehashed_capture_with_noncurrent_exact_packet(primary_probe_app_factory):
    with primary_probe_app_factory() as h:
        f = _ledger_evidence(h)
        original = deepcopy(f.packets[0])
        f.batch = deepcopy(f.batch)
        held = f.batch['primary_packets'][0]
        # Sequence is an exact persisted identity, although it does not alter
        # the canonical numeric packet/body digest. No immutable SQL trigger
        # is removed and the real persisted packet remains unchanged.
        held['header']['sequence'] += 1
        provenance.validate_packet(held['packet'], held['header'])
        f.batch['batch_sha256'] = links._batch_digest(f.batch)
        links.validate_batch(f.batch, require_vectors=False)
        before = _ledger_state(f)
        with pytest.raises(ReadError, match='primary_probe_source_stale'):
            _reserve_evidence(f)
        assert _ledger_state(f) == before
        with h.store.db() as db:
            db.execute('BEGIN')
            assert provenance.read_exact(db, cid=f.cid, start=0, end=5 * RATE) == original
        assert not h.owner._pins and not h.drivers and h.jobs._live is None


def test_native_annotation_immutable_fields_reject_even_after_reserialization(primary_probe_app_factory):
    with primary_probe_app_factory() as h:
        f = _ledger_evidence(h)
        admission = _finish_evidence(f)
        scope = admission['scope_id']
        snapshot = f.value.snapshot(scope)
        assert f.value.recompute_scope(scope, links.reconcile_batches)['saved']
        original_raw, original_digest = _decision_row(f, scope)
        original = json.loads(original_raw)
        variants = {
            'original_parent_ordinal': 999,
            'original_unit_ordinals': [[0, 1]],
            'original_turn_ordinals': [0],
            'primary_packet_sha256': 'f' * 64,
            'native_scope_id': 'f' * 64,
            'float32le_sha256': 'f' * 64,
            'probe_index': 99,
            'capture_namespace': {'schema': 'rewritten-synthetic-namespace'},
            'native_attributions': [{'native_scope_id': 'f' * 64, 'native_label': 'speaker_1'}],
        }
        for field, replacement in variants.items():
            decision = deepcopy(original)
            annotation = next(a for a in decision['annotations'] if a['source_kind'] == 'native_primary')
            annotation[field] = replacement
            changed_raw = history.encoded(decision)
            assert hashlib.sha256(changed_raw).hexdigest() != original_digest
            # This is valid freshly serialized JSON, so refusal must bind the
            # original typed node fields rather than a stale envelope checksum.
            with pytest.raises(ReadError, match='invalid_speaker_link_decision'):
                f.value.save_decision(scope, snapshot['revision'], json.loads(changed_raw),
                    session_revision=snapshot['session_revision'], policy_sha256=links.POLICY_SHA256_V2)
            assert _decision_row(f, scope) == (original_raw, original_digest)
        assert f.value.read_saved(f.sid, f.rid)['annotations'] == original['annotations']


@pytest.mark.parametrize('phase', ['reserve', 'commit'])
def test_real_native_lost_ack_resolution_is_storage_only_without_reprobe(
        primary_probe_app_factory, monkeypatch, phase):
    with primary_probe_app_factory() as h:
        f = _ledger_evidence(h)
        calls = [list(native.calls) for native in h.natives]
        def no_replay(*args, **kwargs):
            raise AssertionError('Lost-ack resolution cannot replay native, ASR, capture or probe work')
        monkeypatch.setattr(FakeAnalyzer, 'analyze', no_replay)
        monkeypatch.setattr(provenance, 'observe', no_replay)
        monkeypatch.setattr(provenance, 'freeze', no_replay)
        monkeypatch.setattr(links, 'capture_candidates_v2', no_replay)
        monkeypatch.setattr(h.jobs.cell_source, 'acquire_dispatch', no_replay)
        admission = None
        if phase == 'commit':
            admission = _reserve_evidence(f)
            assert admission['should_probe']
        match = (f'UPDATE {ledger.SCOPES} SET node_count' if phase == 'reserve'
                 else f'INSERT INTO {ledger.BATCHES}')
        with observed_connections(lose_commit_ack=True, commit_match=match):
            with pytest.raises(ReadError):
                if phase == 'reserve':
                    _reserve_evidence(f)
                else:
                    f.value.commit(admission, f.embedded)
        if phase == 'reserve':
            # A repeated reservation observes the consumed attempt; it never
            # grants a second physical probe after an uncertain return.
            repeated = _reserve_evidence(f)
            assert repeated['should_probe'] is False and repeated['token'] is not None
            admission = repeated
        before = _ledger_state(f)
        _change_real_evidence(f, 'file')
        answer = f.value.resolve_attempt(f.sid, f.rid, f.jid, f.digest, f.batch, token=admission)
        assert answer['resolved']
        after = _ledger_state(f)
        if phase == 'commit':
            assert answer['already_committed'] and after == before
        with h.store.db() as db:
            row = db.execute(f'SELECT batch_json,state FROM {ledger.BATCHES} WHERE job_id=?', (f.jid,)).fetchone()
            retained = json.loads(row['batch_json'])
            assert retained == (f.embedded if phase == 'commit' else f.batch)
            assert row['state'] == ('source_scoped' if phase == 'commit' else 'probe_unavailable')
        assert f.value.resolve_attempt(f.sid, f.rid, f.jid, f.digest, f.batch,
            token=admission)['already_committed']
        assert _ledger_state(f) == after
        with pytest.raises(ReadError):
            f.value.snapshot(admission['scope_id'])
        assert [list(native.calls) for native in h.natives] == calls
        assert not h.owner._pins and not h.drivers and h.jobs._live is None
