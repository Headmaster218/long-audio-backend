"""Prospective primary provenance through the real model-free recording path.

The existing test-only runtime fixture supplies fresh synthetic PCM, a real
Worker, Capture, source receipts, owner lifecycle and SQLite. Only extraction
and native/ASR inference are synthetic. No model, archived input, external
server, optional job dispatch or acoustic qualification is involved.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
from pathlib import Path

from audio_backend import lexical_evidence as lexical
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_primary_provenance as pp
from audio_backend import transcript_contract as contract
from audio_backend import worker as worker_module
from audio_backend.boundary_store import context_parts
from audio_backend.native_owned_cell_store import Budget
from audio_backend.native_owned_cells import RATE, canonical, sha
from audio_backend.settings import Settings
from audio_backend.worker import Worker
from test_moss_automatic_integration import (
    automatic_app_factory, ingest_recording, process_primary,
)
from test_native_owned_support_api import FakeAnalyzer, fake_extract


def _turns(frames, epoch):
    """Repeated same-label intervals retain distinct producer ordinals."""
    assert frames == 2 * RATE
    return [
        {'start': 0., 'end': 2., 'speaker_local_id': epoch + ':speaker_1'},
        {'start': .125, 'end': 2., 'speaker_local_id': epoch + ':speaker_1'},
        {'start': .8125, 'end': 1.0625, 'speaker_local_id': epoch + ':speaker_2'},
        {'start': 1.8125, 'end': 2., 'speaker_local_id': epoch + ':speaker_2'},
    ]


def _analyze(analyzer, path, **kwargs):
    native = kwargs['diarization']
    assert (native['extract_start'], native['extract_end']) == (0., 2.)
    label = native['state']['epoch_id'] + ':speaker_1'
    definitions = (
        (label, ((.25, 'alpha'), (.375, 'beta'),
                 (1.25, 'gamma'), (1.375, 'delta'))),
        (None, ((.875, 'unknownone'), (1.875, 'unknowntwo'))),
    )
    segments = []
    for local_id, entries in definitions:
        units = [{'start': start, 'end': start + .0625,
                  'ownership_anchor': start, 'anchor_kind': 'ctc_emission',
                  'text': text, 'raw_text': text} for start, text in entries]
        text = ''.join(unit['text'] for unit in units)
        segments.append({
            'start': units[0]['start'], 'end': units[-1]['end'],
            'text': text, 'raw_text': text, 'speaker_local_id': local_id,
            'source_track_id': local_id, 'confidence': 0,
            'asr_backend': 'sensevoice', 'timestamp_kind': lexical.PRIMARY_TIMESTAMP,
            '_units': units,
        })
    return {'segments': segments, 'speaker_turns': deepcopy(native['turns']),
            'events': [], 'metadata': {'asr_backend': 'sensevoice',
                'timestamp_semantics': lexical.PRIMARY_TIMESTAMP,
                'diarization_turns': deepcopy(native['turns'])}}


def _extract(worker, chunk, start, end, wav, tmp, stop, *,
             capture_support=False, capture_lexical=False):
    """Reuse fixture PCM extraction and the production source snapshot helper."""
    assert not capture_support
    fake_extract(worker, chunk, start, end, wav, tmp, stop)
    if not capture_lexical:
        return None
    parts = context_parts(worker.store, chunk, start, end)
    with worker.store.db() as db:
        sources = lexical.snapshot_parts(db, parts, chunk['session_id'])
    cursor = 0
    for source, part in zip(sources, parts):
        frames = round(part['read_duration'] * RATE)
        source['analyzer_frames'] = [cursor, cursor + frames]
        cursor += frames
    assert cursor == round((end - start) * RATE)
    return {'_lexical_sources': sources}


def _source_current(capture, scope, part):
    """Adapt real current receipt/file/mapping authority; never return a stub."""
    with Budget().phase() as budget:
        receipt = capture.owner.receipt(part['chunk_id'])
        if (receipt is None or receipt.receipt_id != part['receipt_sha256']
                or receipt.session_id != part['session_id']
                or receipt.digest != part['source_sha256']
                or receipt.frames != part['source_frames']
                or not capture.authority.receipt_valid(receipt, budget)):
            return False
        row = capture.authority.row(part['chunk_id'], budget)
        if row is None:
            return False
        mapping = capture.authority.mapping(row, float.fromhex(scope['origin']),
            part['logical_start'], part['logical_end'], budget)
        return [asdict(item) for item in mapping] == [part]


def _read_current(h, cid):
    packets = []
    with h.store.db() as db:
        db.execute('BEGIN')
        for index in range(2):
            saved = pp.read_exact(db, cid=cid, start=index * RATE,
                                  end=(index + 1) * RATE)
            assert saved is not None
            packet = saved['packet']
            assert pp.current(db, saved, source_current=lambda part:
                _source_current(h.capture, packet['commit']['scope'], part))
            rows = db.execute('SELECT ' + ','.join(contract.PRIMARY_FIELDS)
                + ',lexical_evidence,speaker_id,speaker_status FROM segments '
                'WHERE chunk_id=? AND speaker_window_start=? ORDER BY id',
                (cid, float(index))).fetchall()
            assert len(rows) == 2
            assert packet['refs'] == [
                [contract.primary_evidence(h.sid, row)['source_ref'], None]
                for row in rows]
            # SQLite REAL affinity matters: refs bind the stored projection,
            # rather than the analyzer's integer-confidence INSERT values.
            assert all(type(row['confidence']) is float for row in rows)
            assert all(row['lexical_evidence'] is None for row in rows)
            assert rows[1]['speaker_local_id'] is None
            assert rows[1]['speaker_id'] is None and rows[1]['speaker_status'] == 'unknown'
            assert rows[0]['text'] == ('alphabeta', 'gammadelta')[index]
            assert rows[1]['text'] == ('unknownone', 'unknowntwo')[index]
            stored_core = db.execute('SELECT body FROM native_core_commits WHERE slot=?',
                                    (core.slot(packet['commit']),)).fetchone()
            assert stored_core is not None
            assert bytes(stored_core[0]) == canonical(packet['commit'], core.COMPACT_BYTES)
            packets.append(saved)
        # An exact packet is mandatory. An overlapping/future request cannot
        # reconstruct evidence from the already-present transcript or metadata.
        assert pp.read_exact(db, cid=cid, start=0, end=2 * RATE) is None
        assert pp.read_exact(db, cid=cid, start=2 * RATE, end=3 * RATE) is None
        usage = [tuple(row) for row in db.execute(f'SELECT * FROM {pp.USAGE} ORDER BY scope')]
        bodies = [tuple(row) for row in db.execute(
            f'SELECT * FROM {pp.TABLE} ORDER BY sequence')]
    return packets, usage, bodies


def test_worker_capture_primary_transaction_restart_keeps_original_ordinals(
        automatic_app_factory, monkeypatch):
    """No helper manufactures a provenance draft or a core commitment."""
    original_post_init = Settings.__post_init__

    def post_init(settings):
        if settings.moss_automatic_enabled:
            settings.asr_backend = 'sensevoice'
            settings.lexical_evidence_enabled = True
            settings.native_owned_bounded_core_support_enabled = True
            settings.recording_speaker_links_enabled = True
            settings.recording_speaker_model_path = str(
                Path(settings.data_dir) / 'ABSENT-SYNTHETIC-SPEAKER-MODEL')
        original_post_init(settings)

    monkeypatch.setattr(Settings, '__post_init__', post_init)
    monkeypatch.setattr(Worker, 'extract_pcm', _extract)
    monkeypatch.setattr(FakeAnalyzer, 'analyze', _analyze)
    monkeypatch.setattr('test_native_owned_support_api.synthetic_turns', _turns)
    trace, observed, prepared, persisted = [], [], [], []
    original_observe = pp.observe
    original_validate = worker_module.validate_result
    original_own = worker_module.own_core
    original_persist = pp.persist

    def observe(result, native):
        assert not any(key.startswith('_primary_') for key in result)
        before = deepcopy(result)
        answer = original_observe(result, native)
        assert result == before, 'Pre-validation observation must not alter input'
        assert answer[1] == 'pending' and answer[0] is not None
        assert [(unit[0], unit[1]) for unit in answer[0]['units']] == [
            (0, 0), (0, 1), (0, 2), (0, 3), (1, 0), (1, 1)]
        observed.append(deepcopy(answer[0]))
        trace.append('observe')
        return answer

    def validate(result, duration):
        assert trace[-1] == 'observe'
        assert '_primary_observation' not in result
        trace.append('validate')
        return original_validate(result, duration)

    def own(result, start, end, *args, **kwargs):
        assert trace[-1] == 'validate'
        assert result['_primary_observation'] == observed[-1]
        assert [segment['_primary_parent_ordinal'] for segment in result['segments']] == [0, 1]
        assert [[unit['_primary_unit_ordinal'] for unit in segment['_units']]
                for segment in result['segments']] == [[0, 1, 2, 3], [0, 1]]
        answer = original_own(result, start, end, *args, **kwargs)
        index = len(prepared)
        assert (start, end) == (float(index), float(index + 1))
        assert [segment['_primary_units'] for segment in answer['segments']] == [
            [[0, 2 * index], [0, 2 * index + 1]], [[1, index]]]
        if index:
            assert answer['metadata']['boundary_alignment_used']
        trace.append('ownership')
        return answer

    def persist(store, db, chunk, offset, end, result, row_ids):
        assert trace[-1] == 'prepare'
        assert db.in_transaction
        assert result['_primary_provenance']['packet']['commit'] == result['_native_core_seam']['commit']
        assert len(row_ids) == 2
        status = original_persist(store, db, chunk, offset, end, result, row_ids)
        assert status == 'retained', status
        persisted.append(tuple(row_ids))
        trace.append('persist')
        return status

    monkeypatch.setattr(pp, 'observe', observe)
    monkeypatch.setattr(worker_module, 'validate_result', validate)
    monkeypatch.setattr(worker_module, 'own_core', own)
    monkeypatch.setattr(pp, 'persist', persist)

    with automatic_app_factory() as h:
        assert pp.enabled(h.store.settings) and h.owner.bounded_core_enabled
        assert h.store._primary_provenance_identity == pp.database_identity(h.store.path)
        cid = ingest_recording(h, frames=2 * RATE)
        sid = h.sid
        source = h.store.chunk(cid)
        receipt = h.owner.receipt(cid)
        assert receipt is not None
        # Keep valid prospective lexical drafts, but make retention genuinely
        # unavailable. Numeric p.refs must survive the existing lexical quota.
        with h.store.db() as db:
            db.execute('UPDATE sessions SET lexical_evidence_bytes=? WHERE id=?',
                       (lexical.MAX_SESSION_BYTES, sid))
        original_prepare = h.capture.prepare
        original_commit = h.store.commit_window

        def prepare(chunk, native, offset, end, result, *, required=False):
            assert required and trace[-1] == 'ownership'
            assert h.capture.analyzer is not None
            assert h.capture.witness.failure is None
            _, witness = h.capture.witness.prepared(
                native, h.capture.scope_start, h.capture.origin_frame)
            raw_turns = deepcopy(h.capture.witness.raw_turns)
            owned_pcm = h.capture.witness.read(round(offset * RATE), round(end * RATE))
            answer = original_prepare(chunk, native, offset, end, result, required=required)
            assert h.capture.analyzer is None, 'The real Capture.prepare must release analyzer PCM'
            assert '_primary_observation' not in result
            assert '_primary_provenance' in result, result.get('_primary_status')
            prepared.append({'packet': deepcopy(result['_primary_provenance']['packet']),
                'native': deepcopy(native), 'witness': deepcopy(witness),
                'raw_turns': raw_turns, 'owned_sha256': hashlib.sha256(owned_pcm).hexdigest()})
            trace.append('prepare')
            return answer

        def commit(chunk, offset, end, result):
            assert trace[-1] == 'prepare'
            accepted = original_commit(chunk, offset, end, result)
            assert accepted and trace[-1] == 'persist'
            trace.append('commit')
            return accepted

        monkeypatch.setattr(h.capture, 'prepare', prepare)
        monkeypatch.setattr(h.store, 'commit_window', commit)
        assert process_primary(h) == cid
        assert trace == ['observe', 'validate', 'ownership', 'prepare', 'persist', 'commit'] * 2
        assert len(prepared) == len(persisted) == 2
        assert len(h.natives) == 1 and h.natives[0].calls.count('reset') == 1
        assert h.natives[0].calls.count('push') == h.natives[0].calls.count('finish') == 1
        assert not h.drivers and h.jobs._live is None
        assert not Path(h.store.settings.recording_speaker_model_path).exists()
        completed = h.store.chunk(cid)
        assert completed['status'] == 'completed' and completed['next_offset'] == 2.
        assert completed['metadata']['lexical_evidence']['status'] == 'session_capacity'
        summary = completed['provenance']['recording_primary_provenance']
        assert summary == {'schema': pp.SCHEMA, 'counts': {'retained': 2},
                           'windows': 2, 'last_window_offset': 1.}
        saved, usage, bodies = _read_current(h, cid)
        old_run = h.capture.core_seams.run
        old_epoch = h.natives[0].epoch
        for index, (retained, live) in enumerate(zip(saved, prepared)):
            packet = retained['packet']
            assert {key: value for key, value in packet.items() if key != 'refs'} == live['packet']
            scope, part = packet['commit']['scope'], packet['commit']['part']
            assert scope['run'] == old_run and scope['epoch'] == old_epoch
            assert scope['index'] == 0 and scope['reset'] == 1
            assert scope['kind'] == 'stream' and scope['key'] == source['stream_id']
            assert scope['origin'] == float(source['timeline_start']).hex()
            assert part == {'session_id': sid, 'chunk_id': cid,
                'receipt_sha256': receipt.receipt_id, 'source_sha256': source['digest'],
                'source_frames': 2 * RATE, 'source_start': index * RATE,
                'source_end': (index + 1) * RATE, 'logical_start': index * RATE,
                'logical_end': (index + 1) * RATE}
            assert packet['owned_pcm16le_sha256'] == live['owned_sha256']
            assert packet['owned_pcm16le_sha256'] == hashlib.sha256(b'\x01\0' * RATE).hexdigest()
            evidence = {'chunk_id': cid, 'start': index * RATE, 'end': (index + 1) * RATE,
                'native': live['native']['state'], 'core_cut': {'nominal': float(index + 1),
                    'actual': float(index + 1), 'reason': 'fixed_or_final'}}
            assert packet['commit']['native_sha256'] == sha(live['native']['state'])
            assert packet['commit']['cut_sha256'] == sha(evidence['core_cut'])
            assert packet['commit']['evidence_sha256'] == sha(evidence)
            assert packet['commit']['runtime_sha256'] == sha(live['witness']['provenance'])
            assert packet['query'] == [0, 2 * RATE, 0, 2 * RATE, 2 * RATE,
                                       live['witness']['observed_action_count'], True]
            assert packet['labels'] == [old_epoch + ':speaker_1', old_epoch + ':speaker_2']
            assert packet['turns'] == [[ordinal, 0 if ordinal < 2 else 1,
                round(turn['start'] * RATE), round(turn['end'] * RATE), turn['start'], turn['end']]
                for ordinal, turn in enumerate(live['raw_turns'])]
            assert len(packet['turns']) == 4
            assert packet['rows'] == [[0, [0, 1]], [1, [2]]]
            assert [(unit[0], unit[1]) for unit in packet['units']] == [
                (0, 2 * index), (0, 2 * index + 1), (1, index)]
            assert [unit[5:] for unit in packet['units']] == [
                [0, [0, 1]], [0, [0, 1]], [None, [0, 1, 2 + index]]]
            assert [int(ref[0].split('.')[1]) for ref in packet['refs']] == list(persisted[index])
            assert all(text.encode() not in pp.encoded(packet) for text in
                       ('alpha', 'beta', 'gamma', 'delta', 'unknownone', 'unknowntwo'))

    def no_reconstruction(*args, **kwargs):
        raise AssertionError('Restart/current reads must not reconstruct producer evidence')

    monkeypatch.setattr(pp, 'observe', no_reconstruction)
    monkeypatch.setattr(pp, 'freeze', no_reconstruction)
    monkeypatch.setattr(FakeAnalyzer, 'analyze', no_reconstruction)
    with automatic_app_factory(sid=sid) as restarted:
        assert restarted.capture.core_seams.run != old_run
        assert restarted.worker.native is None and not restarted.natives
        assert _read_current(restarted, cid) == (saved, usage, bodies)
        assert restarted.store.chunk(cid)['provenance']['recording_primary_provenance'] == summary
        assert restarted.store.chunk(cid)['next_offset'] == 2.
        assert not restarted.drivers and restarted.jobs._live is None
        assert not Path(restarted.store.settings.recording_speaker_model_path).exists()
        assert trace == ['observe', 'validate', 'ownership', 'prepare', 'persist', 'commit'] * 2
