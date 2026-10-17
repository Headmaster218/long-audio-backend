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
                path = Path(h.store.chunk(cid)['path'])
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
