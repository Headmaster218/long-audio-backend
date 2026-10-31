"""Fresh archive-v2 contracts: generated PCM, actual Store/SQLite, no models.

The native observations and embedding vector are explicitly synthetic numerical
witnesses. They do not qualify a native runtime, embedding model, acoustics,
Worker cold routing, supervisor restart, or 12/48-hour recording. No historical
recording, model, database or test artifact is an input. Execution is separately
reviewed and approved; importing this file starts no thread or child process.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from audio_backend import boundaries
from audio_backend import embedding_space
from audio_backend import lexical_evidence as lexical
from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_overlap as recording
from audio_backend import recording_primary_provenance as pp
from audio_backend import speaker_evidence
from audio_backend import speaker_support as support
from audio_backend import storage
from audio_backend import strict_recording as strict
from audio_backend import support_vectors
from audio_backend.native_owned_cell_store import Budget, Receipt
from audio_backend.settings import Settings, primary_only_enabled, coordinator_enabled
from audio_backend.storage import Store
from test_native_owned_cells import EPOCH, observed_window
from test_native_owned_support import wav_bytes


# Deliberately fixed retention slots. Unknown cleanup owners are never reclosed,
# replaced by a new Store, or represented as a successful supervisor restart.
PENDING = [None] * 16
POLICY = 'strict-primary-archive-v2'
RATE = 16000


def configuration(root, *, policy=POLICY):
    # Set strict flags after Settings' ordinary construction, as Store admission
    # is itself under test. No worker/coordinator/model is constructed.
    cfg = Settings(data_dir=root, start_worker=False, offline=True,
        disk_reserve_bytes=0, recording_disk_reserve_bytes=0,
        diarization_backend='nemotron', native_diarization_model='synthetic',
        native_diarization_library='synthetic', native_owned_cells_enabled=True,
        native_owned_core_seams_enabled=True, asr_backend='sensevoice')
    cfg.recording_speaker_links_enabled = True
    cfg.recording_speaker_model_path = str((root / 'synthetic-never-opened-model.onnx').absolute())
    cfg.recording_primary_observation_policy = 'primary-clean-history-v1'
    cfg.recording_primary_observation_interval_seconds = 60
    cfg.native_owned_support_enabled = True
    cfg.moss_jobs_enabled = cfg.moss_automatic_enabled = False
    # V1 still requires this metadata flag; Store alone never starts a worker.
    cfg.start_worker = policy == 'strict-primary-manual-v1'
    cfg.native_owned_bounded_core_support_enabled = True
    cfg.lexical_evidence_enabled = True
    cfg.speaker_support_enabled = True
    # Preserve local support/vector evidence without inventing a person root.
    cfg.speaker_min_query_seconds = cfg.speaker_min_clean_seconds = 20.
    cfg.strict_primary_enabled = True
    cfg.strict_primary_policy = policy
    return cfg


def signature(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def seed_source(store, root, *, seconds=2, name='complete'):
    """Real independent source/work files and exact complete range/receipt.

    This tiny fixture seeds the already-materialized recording boundary, not a
    fake RecordingOverlap/Capture authority. All immutable PCM, encoded bytes,
    source ranges and receipt fields are real and mutually checkable.
    """
    root.mkdir(parents=True, exist_ok=True)
    sid = store.create_session('generated archive ' + name)['id']
    frames = round(seconds * RATE)
    pcm = b'\x01\x00' * frames
    encoded = wav_bytes(pcm)
    original = root / (name + '-original.wav')
    work = root / (name + '-work.wav')
    original.write_bytes(encoded)
    work.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    bid, source_id = 'recording.' + sid, uuid.uuid4().hex
    stream = 'recording.' + bid
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,state,
            capture_origin_frame,capture_end_frame,created)
            VALUES(?,?,?,'queued',0,?,0.)''', (bid, sid, 'generated-' + name, frames))
        cid, created = store._add_chunk(db, sid, name, digest, work, seconds,
            stream_id=stream, stream_final=True, _status='staging')
        assert created
        receipt = Receipt(sid, cid, digest, frames, 44, len(encoded), signature(work), 0., stream)
        db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,
            bytes,capture_start_frame,native_receipt_status,native_receipt,receipt_id,
            receipt_state,file_signature) VALUES(?,?,0,?,?,0,'durable',?,?,'durable',?)''',
            (cid, bid, frames, len(encoded), receipt.encoded, receipt.receipt_id,
             json.dumps(list(signature(work)), separators=(',', ':'))))
        db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,
            path,frames,bytes,source_start_frame,format,created)
            VALUES(?,?,?,?,?,?,?,0,?,0.)''',
            (source_id, bid, name, digest, str(original), frames, len(encoded), recording.FORMAT))
        db.execute('''INSERT INTO recording_ranges(batch_id,source_id,chunk_id,
            source_start_frame,capture_start_frame,work_start_frame,frames,relation)
            VALUES(?,?,?,0,0,0,?,'owner')''', (bid, source_id, cid, frames))
        db.execute("UPDATE chunks SET status='queued' WHERE id=?", (cid,))
    return SimpleNamespace(store=store, sid=sid, cid=cid, bid=bid,
        source_id=source_id, original=original, work=work, encoded=encoded,
        pcm=pcm, receipt=receipt)


def synthetic_embedding_descriptor():
    """Source-known descriptor only: no installed runtime/model attestation."""
    binding_id = next(iter(embedding_space._AUDITED_BINDINGS))
    descriptor = {'schema_version': embedding_space.SCHEMA_VERSION,
        'status': 'proven', 'binding_id': binding_id,
        **deepcopy(embedding_space._AUDITED_BINDINGS[binding_id]),
        'extractor': {'provider': 'cpu', 'num_threads': 1}}
    return descriptor, embedding_space.validate_space(descriptor)


def prepared(value, chunk, offset=0., end=2.):
    """Use original lexical/support/provenance codecs with numerical witnesses."""
    duration = chunk['duration']
    witness, native, pcm, _ = observed_window(round(duration * RATE))
    assert pcm == value.pcm
    label = EPOCH + ':speaker_1'
    turns = [{'start': 0., 'end': duration, 'speaker_local_id': label}]
    witness.observe({'turns': turns})
    native['turns'] = deepcopy(turns)
    native['end'] = end
    descriptor, space_id = synthetic_embedding_descriptor()
    vector = [1.] + [0.] * (descriptor['model']['dimension'] - 1)
    start, stop = offset + .25, offset + .5
    result = {'segments': [{'start': start, 'end': stop, 'text': 'bound',
        'raw_text': 'bound', 'speaker_local_id': label, 'speaker_embedding': vector,
        'asr_backend': 'sensevoice', 'timestamp_kind': lexical.PRIMARY_TIMESTAMP,
        'confidence': 0., '_units': [{'start': start, 'end': stop,
            'ownership_anchor': start, 'text': 'bound', 'raw_text': 'bound',
            'anchor_kind': 'ctc_emission'}]}], 'events': [],
        'speaker_turns': [dict(turns[0], speaker_embedding=vector)],
        'metadata': {'diarization_turns': deepcopy(turns),
            'asr_backend': 'sensevoice', 'timestamp_semantics': lexical.PRIMARY_TIMESTAMP}}
    metadata = result['metadata']
    metadata.update(native['provenance'])
    metadata['native_diarization'] = native['state']
    metadata['core_cut'] = {'nominal': end, 'actual': end, 'reason': 'fixed_or_final'}
    metadata.update(speaker_embedding_sha256=descriptor['model']['sha256'],
        speaker_embedding_version=descriptor['model']['version'],
        speaker_embedding_dimension=len(vector), speaker_embedding_space=descriptor,
        speaker_embedding_space_id=space_id)
    samples = support.decoded_floats(value.work)
    _, evidence = speaker_evidence.clean_speaker_audio(samples, turns, label)
    metadata['speaker_embedding_evidence'] = {label: evidence}
    # Explicitly synthetic frontend numbers; no producer_provenance() runtime
    # inventory/model inspection. Full original schema validators still apply.
    frontend = {'schema_version': 1, 'input_contract': support.FLOAT_CONTRACT,
        'analyzer_sha256': cells.sha({'synthetic': 'analyzer'}),
        'runtimes': {'numpy': 'synthetic', 'soundfile': 'synthetic', 'sherpa-onnx': 'synthetic'},
        'provider': 'cpu', 'threads': 1,
        'normalization': 'extractor float32 vector / numpy.linalg.norm; cosine L2',
        'model_sha256': descriptor['model']['sha256']}
    selection = {'schema_version': 1,
        'implementation_sha256': cells.sha({'synthetic': 'selection'}),
        'turns_sha256': support.digest(support.canonical(turns)),
        'policy': 'other-speaker overlap subtraction; inward rounding; first 20 seconds'}
    metadata['speaker_support_producer'] = {'frontend': frontend, 'selection': selection,
        'frontend_sha256': support.digest(support.canonical(frontend)),
        'selection_sha256': support.digest(support.canonical(selection)),
        'analyzer_input_pcm_sha256': support.digest(samples.astype('<f4').tobytes())}
    lineage = {'status': 'complete', 'analyzer_frame_count': len(samples),
        'decoder_sha256': cells.sha({'synthetic': 'canonical-pcm-copy'}),
        'parts': [{'chunk_id': chunk['id'], 'source_sha256': chunk['digest'],
            'seek_seconds': 0., 'requested_seconds': duration,
            'analyzer_frames': [0, len(samples)]}]}
    generation = support.capture_generation(value.store, chunk)
    result['_speaker_support'] = support.prepare_window(result, value.work, lineage,
        generation, core_start=offset, analyzer_start=0., settings=value.store.settings)
    assert result['_speaker_support'][label]['status'] == 'available'
    captured = pp.observe(result, native)
    assert captured[1] == 'pending'
    pp.validate_observation(result, captured)
    result['segments'] = boundaries.lexical_core(result, offset, end, retain_lexical=True)
    result['speaker_turns'] = [dict(turns[0], start=0., end=end-offset,
        speaker_embedding=vector)]
    result['_lexical_sources'] = [{**{key: chunk[key] for key in lexical.SOURCE_FIELDS},
        'seek_seconds': 0., 'requested_seconds': duration,
        'analyzer_frames': [0, round(duration * RATE)]}]
    part = cells.SourceSpan(chunk['session_id'], chunk['id'], value.receipt.receipt_id,
        chunk['digest'], round(duration * RATE), round(offset * RATE), round(end * RATE),
        round(offset * RATE), round(end * RATE))
    capture = SimpleNamespace(witness=witness, core_seams=SimpleNamespace(run='1' * 32),
        scope=('upload', chunk['session_id'], chunk['id']), scope_start=0., origin_frame=0,
        observed_reset_generation=1, analyzer_start=0, analyzer=pcm)
    evidence = {'chunk_id': chunk['id'], 'start': round(offset * RATE),
        'end': round(end * RATE), 'native': native['state'], 'core_cut': metadata['core_cut']}
    pp.freeze(capture, chunk, native, offset, end, result, (part,), evidence,
        pcm[round(offset * RATE)*2:round(end * RATE)*2])
    commit = result['_primary_provenance']['packet']['commit']
    assert commit['part'] == asdict(part)
    result['_native_core_seam'] = {'commit': commit, 'proof': None,
        'reason': 'core_first_or_unavailable_left'}
    result['_native_bounded_support'] = {'budget': Budget.for_bounded(.75), 'batch': None}
    result['_strict_capture'] = strict.make_capture_outcome(chunk, offset, end, result,
        'retained', commit['evidence_sha256'])
    return result


def assert_complete_primary(value):
    store = value.store
    with store.db() as db:
        db.execute('BEGIN')
        row = db.execute('SELECT * FROM segments WHERE chunk_id=?', (value.cid,)).fetchone()
        assert row is not None and row['lexical_evidence']
        assert lexical.current_inventory(db, value.sid, row) is not None
        saved = pp.read_exact(db, cid=value.cid, start=0, end=value.receipt.frames)
        assert saved and all(ref[1] for ref in saved['packet']['refs'])
        assert saved['packet']['commit']['part']['receipt_sha256'] == value.receipt.receipt_id
        support_row = db.execute('SELECT * FROM speaker_support WHERE chunk_id=?', (value.cid,)).fetchone()
        assert support_row['status'] == 'available' and support_row['pcm_bytes'] > 0
        manifest = json.loads(support_row['manifest'])
        support.validate_manifest(manifest)
        assert support.digest(support_row['pcm']) == manifest['support']['pooled_pcm_sha256']
        vector, normalized = support_vectors.read(db, support_row['id'])
        assert vector['raw_vector'] and vector['space'] and normalized[0] == 1.
        receipt = db.execute('SELECT native_receipt FROM recording_work WHERE chunk_id=?', (value.cid,)).fetchone()[0]
        assert Receipt.decode(receipt) == value.receipt
        assert db.execute('SELECT count(*) FROM strict_recording_outcome WHERE cid=?', (value.cid,)).fetchone()[0] == 1
    assert value.original.read_bytes() == value.work.read_bytes() == value.encoded
    assert store.strict_cleanup_confirmed()


def complete_store(tmp_path, *, seconds=2):
    store = Store(tmp_path / 'primary.sqlite', configuration(tmp_path))
    store.moss_automatic = SimpleNamespace(primary_only=True)
    value = seed_source(store, tmp_path / 'uploads', seconds=seconds)
    chunk = store.claim()
    assert chunk['id'] == value.cid
    assert store.commit_window(chunk, 0., seconds, prepared(value, chunk, 0., seconds))
    assert store.chunk(value.cid)['status'] == 'completed'
    assert_complete_primary(value)
    return value


def archive_module():
    from audio_backend import strict_archive_v2
    return strict_archive_v2


def new_budget():
    return archive_module().ArchiveBudget()


def positive_close(store):
    """Only inspect original owners; never clear, retry-close or replace one."""
    assert store._strict_depth == 0
    assert store._strict_connections == [None, None]
    assert store._strict_worker_owner is None
    assert store._strict_publication_owner is None
    assert store._strict_archive_owner is None
    assert store.strict_cleanup_confirmed()


def database_image(path):
    # Fresh generated DB only. No historical/raw target is ever opened.
    import sqlite3
    db = sqlite3.connect(str(path))
    original_error = None
    try:
        db.execute('PRAGMA query_only=ON')
        return db.serialize()
    except BaseException as error:
        original_error = error
        raise
    finally:
        try: db.close()
        except BaseException as close_error:
            PENDING[15] = (db, original_error or close_error)
            raise original_error or close_error


def archive_count(store):
    with store.db() as db:
        # The exact name is taken from the held archive schema, not a registry.
        return db.execute('SELECT count(*) FROM strict_archive_checkpoint').fetchone()[0]


def retained_snapshot(value):
    """Full original generated rows/files, excluding only new archive tables."""
    with value.store.db() as db:
        tables = ('chunks', 'segments', 'speaker_turns', 'strict_recording_control',
            'strict_recording_outcome', 'native_core_commits', pp.TABLE,
            'recording_batches', 'recording_sources', 'recording_ranges',
            'recording_work', 'speaker_support', 'speaker_support_sources',
            'speaker_support_vectors')
        rows = {name: [tuple(row) for row in db.execute('SELECT * FROM ' + name + ' ORDER BY rowid')]
            for name in tables}
    return rows, value.original.read_bytes(), signature(value.original), value.work.read_bytes(), signature(value.work)


# 1. Version/no-migration boundaries.
def test_v1_ddl_literal_is_unchanged(tmp_path):
    import ast
    source = Path(strict.__file__).read_text()
    node = next(item for item in ast.parse(source).body
        if isinstance(item, ast.FunctionDef) and item.name == '_statements')
    assert hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest() == (
        '76b990445e0f3249339d97b037b5c2bf7f00e8114833aa6d9560dc123e38aa42')
    assert strict.POLICY == 'strict-primary-manual-v1'
    cfg = configuration(tmp_path, policy=strict.POLICY)
    assert cfg.start_worker is True and primary_only_enabled(cfg) is True
    cfg.start_worker = False
    assert primary_only_enabled(cfg) is False
    # Change only the version selector: every other original profile flag,
    # including offline=True and start_worker=False, remains exactly unchanged.
    cfg.strict_primary_policy = POLICY
    assert primary_only_enabled(cfg) is True
    assert coordinator_enabled(cfg) is False


def test_exact_v2_reopen_only_after_positive_cleanup(tmp_path):
    store = Store(tmp_path / 'empty.sqlite', configuration(tmp_path))
    sid = store.create_session('fresh v2')['id']
    with store.db() as db:
        assert db.execute('SELECT policy FROM strict_recording_policy WHERE sid=?', (sid,)).fetchone()[0] == POLICY
    positive_close(store)
    # Old Store has no persistent DB handle. This is data reopen, not a
    # supervisor/service restart. There is no operation on old Store afterward.
    reopened = Store(store.path, configuration(tmp_path))
    positive_close(reopened)
    with reopened.db() as db:
        assert db.execute('SELECT policy FROM strict_recording_policy WHERE sid=?', (sid,)).fetchone()[0] == POLICY


@pytest.mark.parametrize('source_policy,target_policy', [
    ('strict-primary-manual-v1', POLICY), (POLICY, 'strict-primary-manual-v1')])
def test_cross_version_refuses_without_migration(tmp_path, source_policy, target_policy):
    store = Store(tmp_path / 'version.sqlite', configuration(tmp_path, policy=source_policy))
    store.create_session('existing selected version')
    positive_close(store)
    before = database_image(store.path)
    with pytest.raises(ValueError):
        Store(store.path, configuration(tmp_path, policy=target_policy))
    assert database_image(store.path) == before
    assert storage.pending_strict_initializer() is None


@pytest.mark.parametrize('shape', ['legacy', 'partial', 'mixed'])
def test_legacy_partial_mixed_namespace_refuses_before_writes(tmp_path, shape):
    import sqlite3
    path = tmp_path / ('bad-' + shape + '.sqlite')
    if shape == 'legacy':
        legacy = Store(path, Settings(data_dir=tmp_path, start_worker=False, offline=True))
        legacy.create_session('not fresh strict v2')
        assert legacy.strict_cleanup_confirmed()
    else:
        db = sqlite3.connect(path)
        try:
            if shape == 'partial':
                db.execute('CREATE TABLE strict_archive_checkpoint(unrecognized TEXT)')
            else:
                for statement in strict._statements():
                    if statement.startswith('CREATE TABLE'):
                        db.execute(statement)
                db.execute('CREATE TABLE strict_archive_unknown(value TEXT)')
            db.commit()
        finally:
            db.close()
    before = database_image(path)
    with pytest.raises(ValueError):
        Store(path, configuration(tmp_path))
    assert database_image(path) == before
    assert storage.pending_strict_initializer() is None


@pytest.mark.parametrize('change', ['unknown_policy', 'worker', 'online', 'strict_disabled'])
def test_selector_and_offline_prerequisites_refuse(tmp_path, change):
    cfg = configuration(tmp_path)
    if change == 'unknown_policy': cfg.strict_primary_policy = 'archive-next'
    if change == 'worker': cfg.start_worker = True
    if change == 'online': cfg.offline = False
    if change == 'strict_disabled': cfg.strict_primary_enabled = False
    with pytest.raises(ValueError):
        Store(tmp_path / 'refused.sqlite', cfg)
    assert storage.pending_strict_initializer() is None


# 2. Complete, nonempty collection and closed dependencies.
def test_actual_completed_nonempty_closure_is_archived_exactly(tmp_path):
    value = complete_store(tmp_path)
    before = retained_snapshot(value)
    checkpoint = value.store.archive_completed_chunk(value.cid, new_budget())
    assert checkpoint['cid'] == value.cid
    assert checkpoint['source'] == value.receipt.digest
    assert checkpoint['tip'] == value.receipt.frames
    assert 0 < checkpoint['bytes'] <= 32 * 1024**2
    assert {item['kind'] for item in checkpoint['objects']} == {
        'canonical_source', 'recording_source', 'support_pcm', 'vector'}
    assert all(item['bytes'] > 0 and len(item['sha256']) == 64 for item in checkpoint['objects'])
    assert sum(item['kind'] == 'support_pcm' for item in checkpoint['objects']) == 1
    assert sum(item['kind'] == 'vector' for item in checkpoint['objects']) == 1
    assert archive_count(value.store) == 1
    positive_close(value.store)
    assert retained_snapshot(value) == before


@pytest.mark.parametrize('missing', ['lexical', 'receipt', 'range', 'support_pcm', 'vector'])
def test_incomplete_nonempty_dependency_refuses_without_checkpoint(tmp_path, missing):
    value = complete_store(tmp_path)
    with value.store.db() as db:
        if missing == 'lexical':
            db.execute('UPDATE segments SET lexical_evidence=NULL WHERE chunk_id=?', (value.cid,))
        elif missing == 'receipt':
            db.execute('UPDATE recording_work SET native_receipt=NULL WHERE chunk_id=?', (value.cid,))
        elif missing == 'range':
            db.execute('DELETE FROM recording_ranges WHERE chunk_id=?', (value.cid,))
        elif missing == 'support_pcm':
            # Delete the exact generated support, not an immutable-row rewrite.
            # The collector must reject the surviving primary support pointer.
            db.execute('DELETE FROM speaker_support WHERE chunk_id=?', (value.cid,))
        elif missing == 'vector':
            db.execute('DELETE FROM speaker_support_vectors')
    with pytest.raises((ValueError, RuntimeError, OSError, core.Unavailable)):
        value.store.archive_completed_chunk(value.cid, new_budget())
    assert archive_count(value.store) == 0
    positive_close(value.store)


def test_external_compact_graph_is_refused_not_omitted(tmp_path):
    value = complete_store(tmp_path)
    with value.store.db() as db:
        # This mutable compact availability state is an unsupported external
        # dependency. Its original immutable compact body is not rewritten.
        db.execute("UPDATE native_core_commits SET state='pending',pending=? WHERE cid=?",
            (b'{"external_dependency":true}', value.cid))
    with pytest.raises((ValueError, RuntimeError, core.Unavailable)):
        value.store.archive_completed_chunk(value.cid, new_budget())
    assert archive_count(value.store) == 0
    positive_close(value.store)


@pytest.mark.parametrize('kind,maximum', [
    ('read', 128 * 1024**2), ('metadata', 4 * 1024**2), ('payload', 32 * 1024**2)])
def test_original_fixed_budget_never_renews_or_accepts_overflow(kind, maximum):
    archive = archive_module()
    budget = archive.ArchiveBudget()
    deadline = budget.deadline
    getattr(budget, kind)(maximum)
    assert budget.deadline == deadline
    with pytest.raises(archive.ArchiveLimit) as caught:
        getattr(budget, kind)(1)
    with pytest.raises(archive.ArchiveLimit) as repeated:
        budget.check()
    assert repeated.value is caught.value
    assert budget.deadline == deadline
    with pytest.raises(TypeError):
        archive.ArchiveBudget(seconds=60)


def test_original_sql_step_budget_and_deadline_are_fixed():
    archive = archive_module()
    budget = archive.ArchiveBudget()
    for _ in range(1499):
        assert budget.progress() == 0
    assert budget.progress() == 1
    assert budget.sql_steps == 150000
    with pytest.raises(archive.ArchiveLimit, match='sql_steps'):
        budget.check()
    expired = archive.ArchiveBudget()
    expired.deadline = 0.
    with pytest.raises(archive.ArchiveLimit, match='deadline'):
        expired.check()


def test_expired_caller_budget_fails_before_database_or_file_open(tmp_path, monkeypatch):
    value = complete_store(tmp_path)
    archive = archive_module()
    budget = archive.ArchiveBudget()
    budget.deadline = 0.
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('expired budget attempted I/O')
    with monkeypatch.context() as patch:
        patch.setattr(value.store, 'db', forbidden)
        patch.setattr(archive, 'os', SimpleNamespace(**{name: getattr(archive.os, name)
            for name in dir(archive.os) if not name.startswith('__')}))
        patch.setattr(archive.os, 'open', forbidden)
        with pytest.raises(archive.ArchiveLimit, match='deadline'):
            value.store.archive_completed_chunk(value.cid, budget)
    assert not calls
    positive_close(value.store)


def clone_row(db, table, original, overrides):
    """Negative-only malformed/count fixture, never offered as complete proof."""
    fields = dict(original)
    fields.update(overrides)
    names = list(fields)
    db.execute('INSERT INTO ' + table + '(' + ','.join(names) + ') VALUES(' +
        ','.join('?' for _ in names) + ')', [fields[name] for name in names])


@pytest.mark.parametrize('table,cap', [
    ('segments', 512), ('speaker_turns', 256), ('speaker_support', 8),
    ('recording_sources', 16), ('recording_ranges', 32)])
def test_actual_collector_row_limits_refuse_before_unbounded_projection(tmp_path, table, cap):
    value = complete_store(tmp_path)
    with value.store.db() as db:
        row = dict(db.execute('SELECT * FROM ' + table + ' LIMIT 1').fetchone())
        count = db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
        for index in range(count, cap + 1):
            if table in ('segments', 'speaker_turns'):
                overrides = {'id': index + 10000}
            elif table == 'speaker_support':
                overrides = {'id': uuid.uuid4().hex, 'local_speaker_id': 'negative-' + str(index)}
            elif table == 'recording_sources':
                overrides = {'seq': index + 10000, 'id': uuid.uuid4().hex,
                    'idempotency_key': 'negative-' + str(index), 'path': str(value.original) + '-negative-' + str(index)}
            else:
                overrides = {'source_start_frame': index + 1}
            clone_row(db, table, row, overrides)
            if table == 'recording_sources':
                db.execute('''INSERT INTO recording_ranges(batch_id,source_id,chunk_id,
                    source_start_frame,capture_start_frame,work_start_frame,frames,relation)
                    VALUES(?,?,?,0,0,0,1,'exact_alias')''',
                    (value.bid, overrides['id'], value.cid))
    reason = 'archive_source_count' if table == 'recording_sources' else table + '_count'
    with pytest.raises(archive_module().ArchiveUnavailable, match=reason):
        value.store.archive_completed_chunk(value.cid, new_budget())
    assert archive_count(value.store) == 0
    positive_close(value.store)


class OsFault:
    """Proxy only this module's syscall reference; never patch global os."""
    def __init__(self, original, operation, *, second_close_error=None):
        self.original = original
        self.operation = operation
        self.error = OSError('injected archive ' + operation)
        self.second_close_error = second_close_error
        self.fired = False
        self.close_fired = False
        self.calls = []

    def __getattr__(self, name):
        return getattr(self.original, name)

    def _kind(self, fd):
        import stat
        return 'dir' if stat.S_ISDIR(self.original.fstat(fd).st_mode) else 'file'

    def _fail(self, operation):
        self.calls.append(operation)
        if operation == self.operation and not self.fired:
            self.fired = True
            raise self.error

    def open(self, *args, **kwargs):
        self._fail('open')
        return self.original.open(*args, **kwargs)

    def read(self, *args, **kwargs):
        self._fail('read')
        return self.original.read(*args, **kwargs)

    def write(self, *args, **kwargs):
        self._fail('write')
        return self.original.write(*args, **kwargs)

    def fsync(self, fd):
        self._fail('fsync_' + self._kind(fd))
        return self.original.fsync(fd)

    def close(self, fd):
        operation = 'close_' + self._kind(fd)
        self._fail(operation)
        if self.fired and self.second_close_error is not None and not self.close_fired:
            self.close_fired = True
            raise self.second_close_error
        return self.original.close(fd)

    def link(self, *args, **kwargs):
        self._fail('link')
        return self.original.link(*args, **kwargs)

    def unlink(self, *args, **kwargs):
        self._fail('unlink')
        return self.original.unlink(*args, **kwargs)


# 3. Actual publication I/O boundaries and original-error precedence.
@pytest.mark.parametrize('operation', ['open', 'read', 'write', 'fsync_file', 'fsync_dir', 'link'])
def test_publication_fault_cannot_create_checkpoint(tmp_path, monkeypatch, operation):
    value = complete_store(tmp_path)
    archive = archive_module()
    before = retained_snapshot(value)
    fault = OsFault(archive.os, operation)
    with monkeypatch.context() as patch:
        patch.setattr(archive, 'os', fault)
        with pytest.raises(OSError) as caught:
            value.store.archive_completed_chunk(value.cid, new_budget())
    assert fault.fired and caught.value is fault.error
    assert archive_count(value.store) == 0
    positive_close(value.store)
    assert retained_snapshot(value) == before


@pytest.mark.parametrize('operation,slot', [('close_file', 0), ('close_dir', 1)])
def test_unknown_publication_close_retains_exact_original_owner(tmp_path, monkeypatch, operation, slot):
    value = complete_store(tmp_path)
    archive = archive_module()
    fault = OsFault(archive.os, operation)
    PENDING[slot] = (value.store, fault)
    with monkeypatch.context() as patch:
        patch.setattr(archive, '_PENDING_ARCHIVE_OWNER', None)
        patch.setattr(archive, 'os', fault)
        with pytest.raises(OSError) as caught:
            value.store.archive_completed_chunk(value.cid, new_budget())
    owner = value.store._strict_archive_owner
    assert fault.fired and caught.value is fault.error
    assert owner is not None and not value.store.strict_cleanup_confirmed()
    PENDING[slot] = (value.store, owner, fault, caught.value)
    calls = list(fault.calls)
    with pytest.raises(BaseException) as repeated:
        value.store.archive_completed_chunk(value.cid, new_budget())
    assert repeated.value is caught.value
    assert value.store._strict_archive_owner is owner and fault.calls == calls
    # No new Store, no raw DB probe, no retry-close of an unknown descriptor.


def test_original_read_error_survives_cleanup_close_error(tmp_path, monkeypatch):
    value = complete_store(tmp_path)
    archive = archive_module()
    close_error = OSError('injected secondary original close unknown')
    fault = OsFault(archive.os, 'read', second_close_error=close_error)
    PENDING[2] = (value.store, fault)
    with monkeypatch.context() as patch:
        patch.setattr(archive, '_PENDING_ARCHIVE_OWNER', None)
        patch.setattr(archive, 'os', fault)
        with pytest.raises(OSError) as caught:
            value.store.archive_completed_chunk(value.cid, new_budget())
    assert fault.fired and fault.close_fired
    assert caught.value is fault.error and caught.value is not close_error
    owner = value.store._strict_archive_owner
    assert owner is not None and not value.store.strict_cleanup_confirmed()
    assert value.store._strict_original_error is fault.error
    PENDING[2] = (value.store, owner, fault, caught.value)


# 4. Guarded checkpoint recheck, lost acknowledgements, unknown DB close.
@pytest.mark.parametrize('mutation', ['source', 'tip', 'proof'])
def test_source_tip_or_proof_changed_after_publication_cannot_checkpoint(tmp_path, monkeypatch, mutation):
    import sqlite3
    value = complete_store(tmp_path)
    archive = archive_module()
    original = archive.os
    changed = []
    class MutateAfterLink:
        def __getattr__(self, name): return getattr(original, name)
        def link(self, *args, **kwargs):
            result = original.link(*args, **kwargs)
            if not changed:
                changed.append(mutation)
                if mutation == 'source':
                    data = bytearray(value.original.read_bytes())
                    data[-1] ^= 1
                    value.original.write_bytes(data)
                else:
                    db = sqlite3.connect(value.store.path)
                    try:
                        if mutation == 'tip':
                            db.execute("UPDATE strict_recording_control SET state='manual' WHERE cid=?", (value.cid,))
                        else:
                            db.execute('UPDATE segments SET lexical_evidence=NULL WHERE chunk_id=?', (value.cid,))
                        db.commit()
                    finally: db.close()
            return result
    with monkeypatch.context() as patch:
        patch.setattr(archive, 'os', MutateAfterLink())
        with pytest.raises(archive.ArchiveUnavailable):
            value.store.archive_completed_chunk(value.cid, new_budget())
    assert changed == [mutation] and archive_count(value.store) == 0
    positive_close(value.store)


class CheckpointConnection:
    def __init__(self, db, *, error, close_unknown=False):
        object.__setattr__(self, '_db', db)
        object.__setattr__(self, '_error', error)
        object.__setattr__(self, '_checkpoint', False)
        object.__setattr__(self, '_close_unknown', close_unknown)
    def __getattr__(self, name): return getattr(self._db, name)
    def __setattr__(self, name, value):
        if name.startswith('_'): object.__setattr__(self, name, value)
        else: setattr(self._db, name, value)
    def execute(self, sql, args=()):
        if sql.startswith('INSERT INTO strict_archive_checkpoint'):
            self._checkpoint = True
        return self._db.execute(sql, args)
    def commit(self):
        self._db.commit()
        if self._checkpoint and not self._close_unknown:
            raise self._error
    def close(self):
        if self._checkpoint and self._close_unknown:
            raise self._error
        self._db.close()


def after_positive_database_ack(store, marker, error, observed):
    """Lose caller acknowledgement only AFTER original commit and close."""
    original = store.db
    @contextmanager
    def context(*args, **kwargs):
        matched = []
        class Marker:
            def __init__(self, db): self._db = db
            def __getattr__(self, name): return getattr(self._db, name)
            def execute(self, sql, values=()):
                if marker in sql: matched.append(sql)
                return self._db.execute(sql, values)
        with original(*args, **kwargs) as db:
            yield Marker(db)
        if matched:
            assert store._strict_connections == [None, None] and store._strict_depth == 0
            assert kwargs['budget'].sql_outcome == 'committed'
            observed.append(marker)
            raise error
    return context


def test_checkpoint_lost_ack_reconciles_exact_existing_without_replay(tmp_path, monkeypatch):
    value = complete_store(tmp_path)
    before = retained_snapshot(value)
    error = RuntimeError('injected checkpoint caller acknowledgement lost after positive close')
    observed = []
    with monkeypatch.context() as patch:
        patch.setattr(value.store, 'db', after_positive_database_ack(value.store,
            'INSERT INTO strict_archive_checkpoint', error, observed))
        with pytest.raises(RuntimeError) as caught:
            value.store.archive_completed_chunk(value.cid, new_budget())
    assert caught.value is error and observed and archive_count(value.store) == 1
    positive_close(value.store)
    with value.store.db() as db:
        exact = dict(db.execute('SELECT * FROM strict_archive_checkpoint').fetchone())
    repeated = value.store.archive_completed_chunk(value.cid, new_budget())
    assert all(repeated[name] == exact[name] for name in repeated if name != 'objects')
    assert archive_count(value.store) == 1 and retained_snapshot(value) == before
    positive_close(value.store)
    reopened = Store(value.store.path, configuration(tmp_path))
    assert reopened.archive_completed_chunk(value.cid, new_budget()) == repeated
    positive_close(reopened)


def test_unknown_checkpoint_db_close_keeps_original_connection_and_owner(tmp_path, monkeypatch):
    value = complete_store(tmp_path)
    archive = archive_module()
    connect = storage.sqlite3.connect
    error = OSError('injected original checkpoint DB close unknown')
    PENDING[3] = (value.store, error)
    with monkeypatch.context() as patch:
        patch.setattr(archive, '_PENDING_ARCHIVE_OWNER', None)
        patch.setattr(storage.sqlite3, 'connect', lambda *a, **k:
            CheckpointConnection(connect(*a, **k), error=error, close_unknown=True))
        with pytest.raises(OSError) as caught:
            value.store.archive_completed_chunk(value.cid, new_budget())
        owner = value.store._strict_archive_owner
        assert caught.value is error and owner is not None
        assert any(connection is not None for connection in value.store._strict_connections)
        assert not value.store.strict_cleanup_confirmed()
        PENDING[3] = (value.store, owner, tuple(value.store._strict_connections), error)
        with pytest.raises(OSError) as refused:
            archive.refuse_pending_archive()
        assert refused.value is error
    # Test isolation restores only registration; exact original owner/connection
    # remains retained above. No replacement Store or second close is attempted.


def read_all(lease):
    blocks = []
    while True:
        block = lease.read(65536)
        if not block: return b''.join(blocks)
        blocks.append(block)


# 5. Live lease/exclusive original owner and exact duplicate deletion.
def test_live_lease_blocks_retirement_and_all_same_store_db_operations(tmp_path):
    value = complete_store(tmp_path)
    checkpoint = value.store.archive_completed_chunk(value.cid, new_budget())
    lease = value.store.open_archived_source(checkpoint['archive_id'], 'work', new_budget())
    with lease:
        assert read_all(lease) == value.encoded
        for call in (
            lambda: value.store.retire_cached_source(lease.cache_id, new_budget()),
            lambda: value.store.archive_completed_chunk(value.cid, new_budget()),
            lambda: value.store.chunk(value.cid),
        ):
            with pytest.raises((storage.StrictMutationBusy, RuntimeError, ValueError)):
                call()
        with pytest.raises(archive_module().ArchiveUnavailable, match='read_bound'):
            lease.read(1024**2 + 1)
        assert Path(lease.path).read_bytes() == value.encoded
    positive_close(value.store)
    receipt = value.store.retire_cached_source(lease.cache_id, new_budget())
    assert receipt['state'] == 'absent' and not Path(lease.path).exists()
    assert value.original.read_bytes() == value.work.read_bytes() == value.encoded


@pytest.mark.parametrize('busy', ['depth', 'worker', 'publication'])
def test_original_owner_or_database_depth_blocks_archive(tmp_path, monkeypatch, busy):
    value = complete_store(tmp_path)
    if busy == 'depth':
        with value.store.db():
            with pytest.raises(storage.StrictMutationBusy):
                value.store.archive_completed_chunk(value.cid, new_budget())
    else:
        owner = object()  # Explicit synthetic registered-owner discriminator.
        with monkeypatch.context() as patch:
            patch.setattr(value.store, '_strict_' + busy + '_owner', owner)
            with pytest.raises(storage.StrictMutationBusy):
                value.store.archive_completed_chunk(value.cid, new_budget())
    assert archive_count(value.store) == 0
    positive_close(value.store)


def test_wrong_store_or_id_cannot_retire_cache(tmp_path):
    value = complete_store(tmp_path)
    checkpoint = value.store.archive_completed_chunk(value.cid, new_budget())
    lease = value.store.open_archived_source(checkpoint['archive_id'], 'work', new_budget())
    with lease:
        cache_id, path = lease.cache_id, Path(lease.path)
    other_root = tmp_path / 'unrelated'
    other_root.mkdir()
    other = Store(other_root / 'other.sqlite', configuration(other_root))
    for store, ident in ((other, cache_id), (value.store, '0' * 64), (value.store, '../original.wav')):
        with pytest.raises(archive_module().ArchiveUnavailable):
            store.retire_cached_source(ident, new_budget())
    assert path.read_bytes() == value.encoded
    positive_close(value.store)
    positive_close(other)


def test_replaced_cache_incarnation_is_not_deleted(tmp_path):
    value = complete_store(tmp_path)
    checkpoint = value.store.archive_completed_chunk(value.cid, new_budget())
    lease = value.store.open_archived_source(checkpoint['archive_id'], 'work', new_budget())
    with lease:
        path, cache_id, old_signature = Path(lease.path), lease.cache_id, lease.signature
    replacement = path.with_suffix('.replacement')
    replacement.write_bytes(value.encoded)
    replacement.replace(path)
    assert list(signature(path)) != old_signature
    with pytest.raises(archive_module().ArchiveUnavailable):
        value.store.retire_cached_source(cache_id, new_budget())
    assert path.read_bytes() == value.encoded
    assert value.original.read_bytes() == value.work.read_bytes() == value.encoded
    positive_close(value.store)


def test_unknown_original_lease_close_retains_lease_and_blocks_every_success(tmp_path, monkeypatch):
    value = complete_store(tmp_path)
    archive = archive_module()
    checkpoint = value.store.archive_completed_chunk(value.cid, new_budget())
    lease = value.store.open_archived_source(checkpoint['archive_id'], 'work', new_budget())
    fault = OsFault(archive.os, 'close_file')
    PENDING[4] = (value.store, lease, fault)
    with monkeypatch.context() as patch:
        patch.setattr(archive, '_PENDING_ARCHIVE_OWNER', None)
        patch.setattr(archive, 'os', fault)
        with pytest.raises(OSError) as caught: lease.close()
        assert caught.value is fault.error and fault.fired
        owner = value.store._strict_archive_owner
        assert owner is lease._owner and not value.store.strict_cleanup_confirmed()
        PENDING[4] = (value.store, owner, lease, fault)
        with pytest.raises(OSError) as refused: archive.refuse_pending_archive()
        assert refused.value is caught.value
    assert Path(lease.path).exists()
    # Never close again, read again, construct replacement Store, or retire.


# 6. Every object is retained as exact bytes; new cache incarnations are distinct.
def test_all_cold_objects_match_saved_bytes_and_new_generation_is_not_stale_id(tmp_path):
    value = complete_store(tmp_path)
    checkpoint = value.store.archive_completed_chunk(value.cid, new_budget())
    for obj in checkpoint['objects']:
        lease = value.store.open_archived_source(checkpoint['archive_id'], obj['object_id'], new_budget())
        with lease:
            data = read_all(lease)
            assert len(data) == obj['bytes']
            assert hashlib.sha256(data).hexdigest() == obj['sha256']
            if obj['kind'] in ('canonical_source', 'recording_source'):
                assert data == value.encoded
        old_id, old_generation, old_path, old_signature = lease.cache_id, lease.generation, lease.path, lease.signature
        assert value.store.retire_cached_source(old_id, new_budget())['state'] == 'absent'
        fresh = value.store.open_archived_source(checkpoint['archive_id'], obj['object_id'], new_budget())
        with fresh:
            assert fresh.generation > old_generation
            assert fresh.cache_id != old_id and fresh.path != old_path and fresh.signature != old_signature
            assert read_all(fresh) == data
        # A stale exact ID must be refused once a new generation exists.
        with pytest.raises(archive_module().ArchiveUnavailable):
            value.store.retire_cached_source(old_id, new_budget())
        assert Path(fresh.path).read_bytes() == data
    positive_close(value.store)


@pytest.mark.parametrize('tamper', ['byte', 'truncate', 'oversize', 'manifest_bound', 'missing_proof', 'missing_file'])
def test_cold_tamper_never_returns_lease_or_empty_success(tmp_path, tamper):
    import struct
    value = complete_store(tmp_path)
    archive = archive_module()
    checkpoint = value.store.archive_completed_chunk(value.cid, new_budget())
    path = tmp_path / 'strict-archive-v2' / (checkpoint['archive_id'] + '.archive')
    data = path.read_bytes()
    if tamper == 'byte': path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    elif tamper == 'truncate': path.write_bytes(data[:-1])
    elif tamper == 'oversize': path.write_bytes(data + b'!')
    elif tamper == 'manifest_bound':
        path.write_bytes(data[:len(archive.MAGIC)] + struct.pack('>I', 4 * 1024**2 + 1) + data[len(archive.MAGIC)+4:])
    elif tamper == 'missing_proof':
        assert b'"lexical_evidence"' in data
        path.write_bytes(data.replace(b'"lexical_evidence"', b'"lexical_evidencX"', 1))
    else: path.unlink()
    with pytest.raises((archive.ArchiveUnavailable, OSError)):
        value.store.open_archived_source(checkpoint['archive_id'], 'work', new_budget())
    with value.store.db() as db:
        assert db.execute('SELECT count(*) FROM strict_archive_cache').fetchone()[0] == 0
    positive_close(value.store)


def test_cold_close_failure_never_exposes_cache_lease(tmp_path, monkeypatch):
    value = complete_store(tmp_path)
    archive = archive_module()
    checkpoint = value.store.archive_completed_chunk(value.cid, new_budget())
    fault = OsFault(archive.os, 'close_file')
    PENDING[5] = (value.store, fault)
    with monkeypatch.context() as patch:
        patch.setattr(archive, '_PENDING_ARCHIVE_OWNER', None)
        patch.setattr(archive, 'os', fault)
        with pytest.raises(OSError) as caught:
            value.store.open_archived_source(checkpoint['archive_id'], 'work', new_budget())
        assert fault.fired and caught.value is fault.error
        owner = value.store._strict_archive_owner
        assert owner is not None and not owner.lease
        PENDING[5] = (value.store, owner, fault)
        assert not value.store.strict_cleanup_confirmed()


def test_completed_chunk_frame_bound_refuses_real_generated_overflow(tmp_path):
    value = complete_store(tmp_path, seconds=168001 / RATE)
    with pytest.raises(archive_module().ArchiveUnavailable, match='completed_prefix'):
        value.store.archive_completed_chunk(value.cid, new_budget())
    assert archive_count(value.store) == 0
    positive_close(value.store)


def test_fifth_original_outcome_refuses_without_truncating_prefix(tmp_path):
    store = Store(tmp_path / 'primary.sqlite', configuration(tmp_path))
    store.moss_automatic = SimpleNamespace(primary_only=True)
    value = seed_source(store, tmp_path / 'uploads', seconds=2.5)
    for ordinal in range(5):
        chunk = store.claim()
        start, end = ordinal / 2, (ordinal + 1) / 2
        assert chunk['id'] == value.cid and chunk['next_offset'] == start
        assert store.commit_window(chunk, start, end, prepared(value, chunk, start, end))
        if ordinal < 4: store.requeue_core(chunk)
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM strict_recording_outcome WHERE cid=?', (value.cid,)).fetchone()[0] == 5
    with pytest.raises(archive_module().ArchiveUnavailable, match='strict_recording_outcome_count'):
        store.archive_completed_chunk(value.cid, new_budget())
    assert archive_count(store) == 0
    positive_close(store)


# 7. Original Store lifecycle, positive-close data reopen and exact intent crash.
def test_original_commit_archive_retire_reopen_fetch_and_unarchived_manual_tail(tmp_path):
    value = complete_store(tmp_path)
    store = value.store
    tail = seed_source(store, tmp_path / 'uploads', seconds=4, name='manual-tail')
    first = store.claim()
    assert first['id'] == tail.cid
    assert store.commit_window(first, 0., 2., prepared(tail, first))
    store.strict_pause(first, 'synthetic manual boundary', cleanup_confirmed=True)
    with store.db() as db:
        accepted = [tuple(row) for row in db.execute('SELECT * FROM strict_recording_outcome WHERE cid=?', (tail.cid,))]
        source_tip = tuple(db.execute('SELECT digest,path,next_offset FROM chunks WHERE id=?', (tail.cid,)).fetchone())
    before = retained_snapshot(value)
    checkpoint = store.archive_completed_chunk(value.cid, new_budget())
    lease = store.open_archived_source(checkpoint['archive_id'], 'work', new_budget())
    with lease:
        assert read_all(lease) == value.encoded
        old_cache, old_generation, old_path = lease.cache_id, lease.generation, lease.path
    assert store.retire_cached_source(old_cache, new_budget())['state'] == 'absent'
    assert not Path(old_path).exists() and retained_snapshot(value) == before
    with store.db() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM strict_recording_outcome WHERE cid=?', (tail.cid,))] == accepted
        assert tuple(db.execute('SELECT digest,path,next_offset FROM chunks WHERE id=?', (tail.cid,)).fetchone()) == source_tip
        assert not db.execute('SELECT 1 FROM strict_archive_checkpoint WHERE cid=?', (tail.cid,)).fetchone()
    store.set_state(tail.cid, 'retry')
    retried = store.claim()
    assert retried['id'] == tail.cid and retried['next_offset'] == 2.
    assert retried['_strict_attempt_nonce'] != first['_strict_attempt_nonce']
    with pytest.raises(ValueError, match='strict_attempt_revoked'):
        store.commit_window(first, 0., 2., prepared(tail, first))
    assert store.commit_window(retried, 2., 4., prepared(tail, retried, 2., 4.))
    with store.db() as db:
        outcomes = [tuple(row) for row in db.execute('SELECT * FROM strict_recording_outcome WHERE cid=? ORDER BY start_frame', (tail.cid,))]
        assert len(outcomes) == 2 and outcomes[:1] == accepted
        assert db.execute('SELECT count(*) FROM segments WHERE chunk_id=?', (tail.cid,)).fetchone()[0] == 2
        assert not db.execute('SELECT 1 FROM strict_archive_checkpoint WHERE cid=?', (tail.cid,)).fetchone()
    positive_close(store)
    path = store.path
    # This is the last access to the positively closed old Store lifecycle.
    reopened = Store(path, configuration(tmp_path))
    fetched = reopened.open_archived_source(checkpoint['archive_id'], 'work', new_budget())
    with fetched:
        assert fetched.generation > old_generation and fetched.cache_id != old_cache
        assert fetched.path != old_path and read_all(fetched) == value.encoded
    positive_close(reopened)
    assert value.original.read_bytes() == value.work.read_bytes() == value.encoded
    assert tail.original.read_bytes() == tail.work.read_bytes() == tail.encoded


@pytest.mark.parametrize('crash', ['intent_ack', 'unlink', 'after_unlink_fsync', 'absent_ack'])
def test_retirement_exact_intent_reconciles_after_positive_close_data_reopen(tmp_path, monkeypatch, crash):
    value = complete_store(tmp_path)
    archive = archive_module()
    checkpoint = value.store.archive_completed_chunk(value.cid, new_budget())
    lease = value.store.open_archived_source(checkpoint['archive_id'], 'work', new_budget())
    with lease:
        cache_id, cache_path, generation = lease.cache_id, Path(lease.path), lease.generation
    original_os, connect = archive.os, storage.sqlite3.connect
    error = OSError('injected retirement ' + crash)
    fired = []
    class CrashOS:
        def __getattr__(self, name): return getattr(original_os, name)
        def unlink(self, path, *args, **kwargs):
            if Path(path) == cache_path:
                if crash == 'unlink': fired.append(crash); raise error
                result = original_os.unlink(path, *args, **kwargs)
                if crash == 'after_unlink_fsync': fired.append('unlinked')
                return result
            return original_os.unlink(path, *args, **kwargs)
        def fsync(self, fd):
            if crash == 'after_unlink_fsync' and fired == ['unlinked']:
                fired.append(crash)
                raise error
            return original_os.fsync(fd)
    before = retained_snapshot(value)
    with monkeypatch.context() as patch:
        patch.setattr(archive, 'os', CrashOS())
        if crash in ('intent_ack', 'absent_ack'):
            marker = "SET state='retiring'" if crash == 'intent_ack' else "SET state='absent'"
            patch.setattr(value.store, 'db', after_positive_database_ack(value.store, marker, error, fired))
        with pytest.raises(OSError) as caught:
            value.store.retire_cached_source(cache_id, new_budget())
    assert caught.value is error and fired
    positive_close(value.store)
    with value.store.db() as db:
        state = db.execute('SELECT state FROM strict_archive_cache WHERE cache_id=?', (cache_id,)).fetchone()[0]
    assert state == ('absent' if crash == 'absent_ack' else 'retiring')
    assert cache_path.exists() == (crash in ('intent_ack', 'unlink'))
    assert retained_snapshot(value) == before
    reopened = Store(value.store.path, configuration(tmp_path))
    exact = reopened.retire_cached_source(cache_id, new_budget())
    assert exact == {'cache_id': cache_id, 'generation': generation, 'state': 'absent'}
    assert not cache_path.exists()
    positive_close(reopened)
    assert value.original.read_bytes() == value.work.read_bytes() == value.encoded


def test_absence_without_committed_exact_intent_is_not_retirement_success(tmp_path):
    value = complete_store(tmp_path)
    checkpoint = value.store.archive_completed_chunk(value.cid, new_budget())
    lease = value.store.open_archived_source(checkpoint['archive_id'], 'work', new_budget())
    with lease:
        cache_id, path = lease.cache_id, Path(lease.path)
    path.unlink()  # Negative fixture removes only the newly generated duplicate.
    with pytest.raises((archive_module().ArchiveUnavailable, FileNotFoundError)):
        value.store.retire_cached_source(cache_id, new_budget())
    with value.store.db() as db:
        assert db.execute('SELECT state FROM strict_archive_cache WHERE cache_id=?', (cache_id,)).fetchone()[0] == 'present'
    assert value.original.read_bytes() == value.work.read_bytes() == value.encoded
    positive_close(value.store)


def test_unknown_original_checkpoint_commit_latches_even_after_positive_db_close(tmp_path, monkeypatch):
    value = complete_store(tmp_path)
    archive = archive_module()
    connect = storage.sqlite3.connect
    error = OSError('injected original COMMIT outcome unknown')
    PENDING[6] = (value.store, error)
    with monkeypatch.context() as patch:
        patch.setattr(archive, '_PENDING_ARCHIVE_OWNER', None)
        patch.setattr(storage.sqlite3, 'connect', lambda *a, **k:
            CheckpointConnection(connect(*a, **k), error=error))
        with pytest.raises(OSError) as caught:
            value.store.archive_completed_chunk(value.cid, new_budget())
        assert caught.value is error
        owner = value.store._strict_archive_owner
        assert owner is not None and owner.unknown
        assert value.store._strict_connections == [None, None]
        assert not value.store.strict_cleanup_confirmed()
        PENDING[6] = (value.store, owner, error)
        with pytest.raises(OSError) as refused: archive.refuse_pending_archive()
        assert refused.value is error
    # Close was positive but COMMIT was not: no reconciliation/reopen here.
