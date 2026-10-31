"""Focused live payload contracts; generated inputs and numerical witnesses only.

This file is a source draft, not an execution authorization. The connected
positive path must run original Worker processing, positive cleanup and its
automatic handoff. No test may replace that path with a direct maintenance call
and claim Worker integration. The one connected case may start only original
Worker.loop and CellStore._run threads under the separately approved gate;
component cases remain threadless. No models, HTTP or child processes run.
The qualified offline tests are helper sources only, never a rerun selection.
"""
from contextlib import contextmanager
from copy import deepcopy
import ast
import hashlib
import json
from pathlib import Path
import threading
import time
from types import MappingProxyType, SimpleNamespace
import uuid
import wave

import pytest

from audio_backend import change_feed
from audio_backend import embedding_space
from audio_backend import lexical_evidence as lexical
from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_cell_capture as capture_module
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_overlap as recording
from audio_backend import recording_primary_provenance as pp
from audio_backend import storage
from audio_backend import speaker_evidence
from audio_backend import speaker_support as support
from audio_backend import strict_payload_v2 as payload
from audio_backend import support_vectors
from audio_backend import verified_identity as bank
from audio_backend.settings import payload_live_enabled
from audio_backend.storage import Store
from audio_backend.native_owned_cell_store import Receipt
from test_strict_archive_v2 import (
    configuration as archive_configuration, database_image, positive_close,
    prepared, seed_source, signature, synthetic_embedding_descriptor,
)
from test_native_owned_support import synthetic_provenance, synthetic_state, wav_bytes


POLICY = 'strict-primary-payload-v2'
RATE = 16000
# Bind exact real owners BEFORE injecting uncertainty, including when an
# assertion fails. Never reclose or substitute a Store for one of these owners.
PENDING = [None] * 8
THREAD_CASE_OWNERS = {'worker': None, 'actor': None}
REVIEW_POLICY = {'schema_version': 1, 'experimental': True,
    'single_threshold': .75, 'centroid_threshold': .75, 'margin': .1,
    'minimum_query_seconds': 2., 'minimum_enrollment_seconds': 2.}


def configuration(root):
    cfg = archive_configuration(root, policy=POLICY)
    cfg.start_worker = True  # Metadata admission only; no thread is started.
    cfg.window_seconds = 2.
    cfg.context_seconds = 0.
    cfg.speaker_min_query_seconds = cfg.speaker_min_clean_seconds = 2.
    cfg.verified_identity_policy = deepcopy(REVIEW_POLICY)
    cfg.reviewed_primary_persons_enabled = True
    return cfg


class NumericalNativeTransport:
    """Synthetic native numbers only; never represents a PID or native handle.

    The original NativeCoordinator still owns reset/push/epoch/preroll geometry.
    Its original request observer sees the real generated input byte intervals.
    This transport is positively closed separately before final Worker teardown.
    """
    def __init__(self, worker):
        self.worker = worker
        self.frames = 0
        self.epoch = None
        self.closed = False
        self.calls = []

    def request(self, action, stop, **kwargs):
        assert not self.closed and not stop()
        self.calls.append(action)
        if action == 'reset':
            self.frames = 0
            self.epoch = kwargs['epoch_id']
            value = {'state': synthetic_state(self.epoch, 0),
                     'provenance': synthetic_provenance()}
        elif action == 'push':
            with wave.open(kwargs['wav'], 'rb') as source:
                assert (source.getframerate(), source.getnchannels(),
                        source.getsampwidth()) == (RATE, 1, 2)
                self.frames += source.getnframes()
            # A bounded two-second synthetic stability lag reaches true EOF
            # through original Coordinator.finish rather than fictional padding.
            value = synthetic_state(self.epoch, self.frames,
                                    stable_frames=max(0, self.frames - 2 * RATE))
        elif action == 'finish':
            value = synthetic_state(self.epoch, self.frames, finished=True)
        elif action == 'turns':
            start, end = max(0., kwargs['start']), min(self.frames / RATE, kwargs['end'])
            value = {'turns': [] if start >= end else [{'start': start, 'end': end,
                'speaker_local_id': self.epoch + ':speaker_1'}]}
        else:
            raise AssertionError('Unexpected synthetic native command')
        self.worker._native_response(value)
        return value

    def close(self):
        assert not self.closed
        self.closed = True


def canonical_copy_decoder(command, settings, *, timeout, stop):
    """Replace only the external decoder process, not Worker.extract_pcm.

    The actual extraction path still selects original source parts, verifies
    source digests/signatures, measures exact decoded frames, checks source EOF
    and produces lexical/support lineage. The generated executable marker is
    fingerprinted but never executed.
    """
    assert command[0] == settings.ffmpeg and timeout == 180 and not stop()
    source_path = Path(command[command.index('-i') + 1])
    assert source_path.parent == settings.data_dir / 'uploads'
    start = round(float(command[command.index('-ss') + 1]) * RATE)
    count = round(float(command[command.index('-t') + 1]) * RATE)
    assert 0 <= start and 0 < count <= 22 * RATE
    with wave.open(str(source_path), 'rb') as source:
        assert (source.getframerate(), source.getnchannels(), source.getsampwidth()) == (RATE, 1, 2)
        source.setpos(start)
        pcm = source.readframes(count)
    with wave.open(str(command[-1]), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm)


class NumericalAnalyzer:
    """Original analyzer_factory seam; numerical witnesses, no model assets."""
    def __init__(self, config):
        self.closed = False

    def analyze(self, path, *, diarization, **kwargs):
        assert not self.closed
        turns = deepcopy(diarization['turns'])
        assert len(turns) == 1
        label = turns[0]['speaker_local_id']
        samples = support.decoded_floats(Path(path))
        _, evidence = speaker_evidence.clean_speaker_audio(samples, turns, label)
        descriptor, space_id = synthetic_embedding_descriptor()
        vector = [1.] + [0.] * (descriptor['model']['dimension'] - 1)
        start = getattr(self, 'owned_start', 0.) + .25
        end = start + .25
        frontend = {'schema_version': 1, 'input_contract': support.FLOAT_CONTRACT,
            'analyzer_sha256': cells.sha({'synthetic': 'payload-analyzer'}),
            'runtimes': {'numpy': 'synthetic', 'soundfile': 'synthetic', 'sherpa-onnx': 'synthetic'},
            'provider': 'cpu', 'threads': 1,
            'normalization': 'extractor float32 vector / numpy.linalg.norm; cosine L2',
            'model_sha256': descriptor['model']['sha256']}
        selection = {'schema_version': 1,
            'implementation_sha256': cells.sha({'synthetic': 'payload-selection'}),
            'turns_sha256': support.digest(support.canonical(turns)),
            'policy': 'other-speaker overlap subtraction; inward rounding; first 20 seconds'}
        metadata = {'diarization_turns': deepcopy(turns), 'asr_backend': 'sensevoice',
            'timestamp_semantics': lexical.PRIMARY_TIMESTAMP,
            'speaker_embedding_sha256': descriptor['model']['sha256'],
            'speaker_embedding_version': descriptor['model']['version'],
            'speaker_embedding_dimension': len(vector), 'speaker_embedding_space': descriptor,
            'speaker_embedding_space_id': space_id, 'speaker_embedding_evidence': {label: evidence},
            'speaker_support_producer': {'frontend': frontend, 'selection': selection,
                'frontend_sha256': support.digest(support.canonical(frontend)),
                'selection_sha256': support.digest(support.canonical(selection)),
                'analyzer_input_pcm_sha256': support.digest(samples.astype('<f4').tobytes())}}
        return {'segments': [{'start': start, 'end': end, 'text': 'generated primary',
            'raw_text': 'generated primary', 'speaker_local_id': label,
            'speaker_embedding': vector, 'asr_backend': 'sensevoice',
            'timestamp_kind': lexical.PRIMARY_TIMESTAMP, 'confidence': 0.,
            '_units': [{'start': start, 'end': end, 'ownership_anchor': start,
                'text': 'generated primary', 'raw_text': 'generated primary',
                'anchor_kind': 'ctc_emission'}]}],
            'speaker_turns': [dict(turns[0], speaker_embedding=vector)],
            'events': [], 'metadata': metadata}

    def close(self):
        assert not self.closed
        self.closed = True


@pytest.mark.parametrize('old_policy', [
    'strict-primary-manual-v1', 'strict-primary-archive-v2'])
def test_live_rejects_prior_schema_without_migration(tmp_path, old_policy):
    original = Store(tmp_path / 'primary.sqlite',
        archive_configuration(tmp_path, policy=old_policy))
    positive_close(original)
    before = database_image(original.path)
    with pytest.raises((ValueError, RuntimeError)):
        Store(original.path, configuration(tmp_path))
    assert database_image(original.path) == before


def test_live_profile_is_explicit_and_fresh(tmp_path):
    cfg = configuration(tmp_path)
    assert payload_live_enabled(cfg)
    store = Store(tmp_path / 'primary.sqlite', cfg)
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM strict_payload_policy').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM strict_payload_unit').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM strict_payload_object').fetchone()[0] == 0
    positive_close(store)
    for field, changed in (('start_worker', False), ('strict_primary_enabled', False),
                           ('native_owned_compact_gc_enabled', True),
                           ('native_owned_receipt_gc_enabled', True)):
        old = getattr(cfg, field)
        setattr(cfg, field, changed)
        assert not payload_live_enabled(cfg)
        setattr(cfg, field, old)


def component_store(root):
    store = Store(root / 'primary.sqlite', configuration(root))
    store.moss_automatic = SimpleNamespace(primary_only=True)
    change_feed.initialize(store)
    return store


def completed_component(store, *, name='component'):
    value = seed_source(store, store.settings.data_dir / 'uploads', name=name)
    chunk = store.claim()
    assert chunk['id'] == value.cid
    assert store.commit_window(chunk, 0., 2., prepared(value, chunk)) is True
    with store.db() as db:
        value.support = dict(db.execute('SELECT * FROM speaker_support WHERE chunk_id=?', (value.cid,)).fetchone())
        value.vector = dict(db.execute('SELECT * FROM speaker_support_vectors WHERE support_id=?',
                                      (value.support['id'],)).fetchone())
    value.audio = support.audio(store, value.sid, value.support['id'])
    return value


def component_frontier(value, monkeypatch, *, roots=()):
    """Component-only scheduling seam, never proof of Worker integration.

    Only original frontier authority is replaced for tiny fault/layout cases.
    Original Store commits, complete source/proof collector, bounded publication,
    protected placement transaction and readers remain unmodified.
    """
    state = {'sid': value.sid, 'stream_id': value.receipt.stream_id,
             'frontier_frame': value.receipt.frames, 'roots': frozenset(roots)}
    witness = object.__new__(capture_module.PayloadFrontier)
    witness.store = value.store
    @contextmanager
    def guard(self, db, budget):
        assert self is witness and db.in_transaction
        budget.check()
        yield MappingProxyType(dict(state))
        budget.check()
    monkeypatch.setattr(capture_module.PayloadFrontier, 'guard', guard)
    return witness, state


def make_cold(value, monkeypatch):
    witness, _ = component_frontier(value, monkeypatch)
    value.store.maintain_payloads(witness, payload.PayloadBudget())
    with value.store.db() as db:
        value.unit = dict(db.execute('SELECT * FROM strict_payload_unit WHERE cid=?', (value.cid,)).fetchone())
        value.locators = {row['kind']: dict(row) for row in db.execute(
            'SELECT * FROM strict_payload_object WHERE unit_id=?', (value.unit['unit_id'],))}
        assert value.unit['state'] == 'committed'
        assert db.execute('SELECT pcm FROM speaker_support WHERE id=?', (value.support['id'],)).fetchone()[0] is None
        assert db.execute('SELECT raw_vector FROM speaker_support_vectors WHERE id=?',
                          (value.vector['id'],)).fetchone()[0] is None
    value.unit_path = (value.store.settings.data_dir / 'strict-payload-v2' /
        f"{value.unit['bucket']:08x}" / (value.unit['unit_id'] + '.unit'))
    return value


def exact_reads(value):
    assert support.audio(value.store, value.sid, value.support['id']) == value.audio
    with value.store.db() as db:
        db.execute('BEGIN')
        with value.store.payload_resolver(db) as resolver:
            raw, normalized = support_vectors.read(db, value.support['id'], payload=resolver)
            assert raw['raw_vector'] == value.vector['raw_vector']
            assert raw['space'] == value.vector['space'] and normalized[0] == 1.


def logical_snapshot(store):
    """Tiny selected fixture tables; omit only the two physical BLOB columns."""
    names = ('sessions', 'chunks', 'segments', 'speaker_turns', 'strict_recording_outcome',
             'native_core_commits', pp.TABLE,
             'recording_work', 'recording_sources', 'recording_ranges',
             'speaker_support', 'speaker_support_vectors', 'speaker_support_sources',
             'result_changes')
    result = {}
    with store.db() as db:
        for table in names:
            # Exact original table names are checked before each bounded read.
            exists = db.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone()
            assert exists, table
            rows = [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
            for row in rows:
                if table == 'speaker_support':row.pop('pcm')
                if table == 'speaker_support_vectors':row.pop('raw_vector')
            result[table] = rows
    return result


def corrupt_generated_row(store, table, sql, args):
    """Damage a fresh fixture only, restoring the exact original trigger SQL."""
    assert table in ('speaker_support', 'strict_payload_object')
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        triggers = list(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)))
        for name, _ in triggers:db.execute('DROP TRIGGER ' + name)
        db.execute(sql, args)
        for _, source in triggers:db.execute(source)


def test_seventeen_real_units_roll_into_two_fixed_buckets(tmp_path, monkeypatch):
    store = component_store(tmp_path)
    values = []
    for index in range(17):
        value = completed_component(store, name=f'bucket-{index:02d}')
        before = logical_snapshot(store)
        make_cold(value, monkeypatch)
        assert logical_snapshot(store) == before
        values.append(value)
    with store.db() as db:
        assert [tuple(row) for row in db.execute(
            'SELECT bucket,count(*) FROM strict_payload_unit GROUP BY bucket ORDER BY bucket')] == [(0, 16), (1, 1)]
        assert db.execute('SELECT count(*) FROM strict_payload_object').fetchone()[0] == 34
        assert db.execute('SELECT sum(length(pcm)) FROM speaker_support').fetchone()[0] is None
        assert db.execute('SELECT sum(length(raw_vector)) FROM speaker_support_vectors').fetchone()[0] is None
    assert payload.BUCKET_UNITS == 16
    assert {p.name for p in (tmp_path / 'strict-payload-v2').iterdir()} == {'00000000', '00000001'}
    exact_reads(values[0])
    exact_reads(values[-1])


def test_component_frontier_roots_and_one_unit_bound(tmp_path, monkeypatch):
    # Exact finite classification controls from held ready-predicate source;
    # these exercise no actor and grant no placement authority.
    cases = [
        ('ready', True, False, False, False, True),
        ('ready', False, True, False, False, True),
        ('ready', True, True, False, False, True),
        ('ready', False, False, False, False, False),
        ('ready', True, True, True, False, False),
        ('ready', True, True, False, True, False),
        ('optional_sql_or_io_failure', True, True, False, False, False),
        ('handoff_release_pending', True, True, False, False, False),
        ('cleanup_pending', True, True, False, False, False),
        ('shutdown_pending', True, True, False, False, False),
        ('initializing', True, True, False, False, False),
        ('cleanup_only', True, True, False, False, False),
        ('ready-but-not-exact', True, True, False, False, False),
        (None, True, True, False, False, False),
        ('ready', 1, True, False, False, False),
        ('ready', True, True, False, 0, False),
    ]
    assert len(cases) == 16
    for status, reconcile, cleanup, slot, pins, expected in cases:
        assert capture_module._payload_pending_ready(status, reconcile, cleanup, slot, pins) is expected
    source = Path(capture_module.__file__)
    assert source.name == 'native_owned_cell_capture.py'
    with source.open('rb') as file:raw = file.read(160 * 1024 + 1)
    assert len(raw) <= 160 * 1024
    assert hashlib.sha256(raw).hexdigest() == 'd9c2cdd81359c5ff5edc84b82a53498deba7a1649047c6f426450b774cd56539'
    tree = ast.parse(raw, filename=str(source))
    frontier, = [node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == 'PayloadFrontier']
    links, = [node for node in frontier.body if isinstance(node, ast.FunctionDef)
              and node.name == '_links']
    strict, = [node for node in ast.walk(links) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and node.func.attr == 'require_strict_wait']
    pending, = [node for node in ast.walk(links) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == '_payload_pending_ready']
    observation, = [node for node in links.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'state' for target in node.targets)]
    common, = [node for node in links.body if isinstance(node, ast.If)
               and "state['status'] != 'ready'" in ast.unparse(node.test)]
    assert observation.lineno < common.lineno < strict.lineno < pending.lineno
    assert isinstance(observation.value, ast.Dict) and len(observation.value.keys) == 12
    value = completed_component(component_store(tmp_path))
    before = logical_snapshot(value.store)
    # Original bounded pin-root extraction, with an explicitly synthetic pin
    # document (not a fabricated live actor or a complete native-cell proof).
    pin_owner = SimpleNamespace(_pins={1: SimpleNamespace(document={
        'dependencies': [{'chunk_id': value.cid}]})})
    with value.store.db() as db:
        db.execute('BEGIN')
        assert core.payload_retention_roots(pin_owner, db, payload.PayloadBudget(), ()) == frozenset((value.cid,))
    witness, state = component_frontier(value, monkeypatch, roots=(value.cid,))
    assert value.store.maintain_payloads(witness, payload.PayloadBudget())['retired_units'] == 0
    state['roots'] = frozenset()
    state['frontier_frame'] = value.receipt.frames - 1
    assert value.store.maintain_payloads(witness, payload.PayloadBudget())['retired_units'] == 0
    state['frontier_frame'] += 1
    value.store.maintain_payloads(witness, payload.PayloadBudget())
    with value.store.db() as db:
        assert db.execute("SELECT count(*) FROM strict_payload_unit WHERE state='committed'").fetchone()[0] == 1
    assert value.store.maintain_payloads(witness, payload.PayloadBudget())['retired_units'] == 0
    assert logical_snapshot(value.store) == before


@pytest.mark.parametrize('fault', ['hot_capacity', 'archive_disk', 'known_busy'])
def test_capacity_refuses_before_accepting_new_prefix(tmp_path, monkeypatch, fault):
    store = component_store(tmp_path)
    existing = completed_component(store)
    candidate = seed_source(store, tmp_path / 'uploads', name='unaccepted')
    chunk = store.claim()
    assert chunk['id'] == candidate.cid
    result = prepared(candidate, chunk)
    before = logical_snapshot(store)
    if fault == 'hot_capacity':
        with store.db() as db:used = support_vectors.retained_bytes(db, live=True)
        monkeypatch.setattr(store.settings, 'speaker_support_total_pcm_bytes', used + 1)
        with pytest.raises(payload.PayloadCapacityError):store.commit_window(chunk, 0., 2., result)
    elif fault == 'archive_disk':
        # A real archive reservation encounters a synthetic zero-free-space
        # observation. The still-full hot pool cannot become unavailable success.
        with store.db() as db:used = support_vectors.retained_bytes(db, live=True)
        monkeypatch.setattr(store.settings, 'speaker_support_total_pcm_bytes', used + 1)
        monkeypatch.setattr(payload.os, 'fstatvfs', lambda fd: SimpleNamespace(f_bavail=0, f_frsize=4096))
        witness, _ = component_frontier(existing, monkeypatch)
        with pytest.raises(payload.PayloadCapacityError):store.maintain_payloads(witness, payload.PayloadBudget())
        with pytest.raises(payload.PayloadCapacityError):store.commit_window(chunk, 0., 2., result)
    else:
        # Ordinary retained DB ownership, not fabricated cleanup uncertainty.
        with store.db() as db:used = support_vectors.retained_bytes(db, live=True)
        monkeypatch.setattr(store.settings, 'speaker_support_total_pcm_bytes', used + 1)
        witness, _ = component_frontier(existing, monkeypatch)
        with store.db():
            with pytest.raises(payload.PayloadAdmissionBusy):
                store.maintain_payloads(witness, payload.PayloadBudget())
        assert not store.strict_cleanup_uncertain()
        with pytest.raises(payload.PayloadCapacityError):store.commit_window(chunk, 0., 2., result)
    assert logical_snapshot(store) == before
    assert store.chunk(candidate.cid)['next_offset'] == 0.
    assert existing.original.read_bytes() == existing.encoded


@pytest.mark.parametrize('damage', ['missing', 'byte', 'locator', 'dual_hot'])
def test_cold_integrity_never_becomes_empty_success(tmp_path, monkeypatch, damage):
    value = make_cold(completed_component(component_store(tmp_path)), monkeypatch)
    if damage == 'missing':
        value.unit_path.unlink()
    elif damage == 'byte':
        with value.unit_path.open('r+b') as file:
            file.seek(value.locators['pcm']['offset'])
            byte = file.read(1)
            file.seek(value.locators['pcm']['offset'])
            file.write(bytes((byte[0] ^ 1,)))
    elif damage == 'locator':
        corrupt_generated_row(value.store, 'strict_payload_object',
            "UPDATE strict_payload_object SET sha256=? WHERE kind='pcm' AND owner_id=?",
            ('f' * 64, value.support['id']))
    else:
        corrupt_generated_row(value.store, 'speaker_support',
            'UPDATE speaker_support SET pcm=? WHERE id=?', (value.support['pcm'], value.support['id']))
    with pytest.raises((payload.PayloadIntegrityError, OSError)):
        support.audio(value.store, value.sid, value.support['id'])


@pytest.mark.parametrize('revoke', ['source', 'generation'])
def test_original_revocation_cannot_resurrect_cold_evidence(tmp_path, monkeypatch, revoke):
    value = make_cold(completed_component(component_store(tmp_path)), monkeypatch)
    if revoke == 'source':
        value.store.delete_session(value.sid)
    else:
        from audio_backend.identity_reset import reset_group
        group = value.support['identity_group']
        assert value.store.session(value.sid)['identity_group'] == group
        result = reset_group(value.store, group)
        assert result == {'identity_group': group, 'reset': True}
        with value.store.db() as db:
            current = db.execute('SELECT generation FROM speaker_identity_generations WHERE identity_group=?',
                                 (group,)).fetchone()
            assert current is not None and current['generation'] != value.support['generation']
    with pytest.raises((KeyError, ValueError, payload.PayloadIntegrityError)):
        support.audio(value.store, value.sid, value.support['id'])
    with value.store.db() as db:
        assert db.execute('SELECT 1 FROM strict_payload_object WHERE owner_id IN (?,?)',
            (value.support['id'], value.vector['id'])).fetchone() is None
    # Logical revocation does not silently erase immutable archive evidence.
    assert value.unit_path.exists()


class PlacementConnection:
    def __init__(self, db, error, fault):
        object.__setattr__(self, '_db', db)
        object.__setattr__(self, '_error', error)
        object.__setattr__(self, '_fault', fault)
        object.__setattr__(self, '_placement', False)
    def __getattr__(self, name):return getattr(self._db, name)
    def __setattr__(self, name, value):
        if name.startswith('_'):object.__setattr__(self, name, value)
        else:setattr(self._db, name, value)
    def execute(self, sql, args=()):
        if sql.startswith("UPDATE strict_payload_unit SET state='committed'"):
            self._placement = True
        return self._db.execute(sql, args)
    def commit(self):
        self._db.commit()
        if self._placement and self._fault == 'commit':raise self._error
    def close(self):
        if self._placement and self._fault == 'close':raise self._error
        self._db.close()


@pytest.mark.parametrize('fault,slot', [('commit', 0), ('close', 1)])
def test_unknown_original_placement_owner_is_sticky(tmp_path, monkeypatch, fault, slot):
    value = completed_component(component_store(tmp_path))
    witness, _ = component_frontier(value, monkeypatch)
    error = OSError('synthetic original placement ' + fault + ' uncertainty')
    PENDING[slot] = {'store': value.store, 'connections': [], 'error': error}
    connect = storage.sqlite3.connect
    def connection(*args, **kwargs):
        result = PlacementConnection(connect(*args, **kwargs), error, fault)
        PENDING[slot]['connections'].append(result)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(payload, '_PENDING_PAYLOAD_OWNER', None)
        patch.setattr(storage.sqlite3, 'connect', connection)
        with pytest.raises(OSError) as raised:
            value.store.maintain_payloads(witness, payload.PayloadBudget())
        assert raised.value is error
        PENDING[slot]['owner'] = value.store._strict_archive_owner
        assert payload._PENDING_PAYLOAD_OWNER is PENDING[slot]['owner']
        assert value.store.strict_cleanup_uncertain()
        count = len(PENDING[slot]['connections'])
        with pytest.raises(OSError) as refused:
            value.store.maintain_payloads(witness, payload.PayloadBudget())
        assert refused.value is error and len(PENDING[slot]['connections']) == count
    # No replacement Store, second close, or same-process recovery is attempted.


def test_multibody_reader_shares_original_caller_allowance(tmp_path, monkeypatch):
    value = make_cold(completed_component(component_store(tmp_path)), monkeypatch)
    class Caller:
        def __init__(self):self.used = 0; self.maximum = payload.MAX_READ
        def check(self):
            if self.used > self.maximum:raise RuntimeError('synthetic caller exhausted')
        def projection(self, count):self.used += count; self.check()
        def progress(self):self.check(); return 0
    caller = Caller()
    budget = payload.PayloadBudget(caller=caller)
    with pytest.raises(RuntimeError, match='synthetic caller exhausted'):
        with value.store.db() as db:
            db.execute('BEGIN')
            with value.store.payload_resolver(db, budget=budget) as resolver:
                pcm = resolver.resolve('pcm', value.support['id'], value.support['pcm_bytes'],
                    json.loads(value.support['manifest'])['support']['pooled_pcm_sha256'])
                assert pcm == value.support['pcm'] and caller.used > 0
                used = caller.used
                caller.maximum = used
                support_vectors.read(db, value.support['id'], payload=resolver)
    assert caller.used > used and budget.read_bytes >= caller.used
    # Actual SQLite VM work, not a direct call to progress() or a long wait.
    # The exact remaining envelope is consumed by the composed original hook.
    vm_budget = payload.PayloadBudget()
    deadline = vm_budget.deadline
    with pytest.raises(payload.base.ArchiveLimit, match='archive_sql_steps'):
        with value.store.db() as db:
            db.execute('BEGIN')
            with value.store.payload_resolver(db, budget=vm_budget):
                vm_budget.sql_steps = payload.base.MAX_STEPS - 100
                db.execute('''WITH RECURSIVE bounded_numbers(n) AS (
                    VALUES(0) UNION ALL SELECT n+1 FROM bounded_numbers WHERE n<10000)
                    SELECT sum(n) FROM bounded_numbers''').fetchone()
    assert vm_budget.sql_steps >= payload.base.MAX_STEPS
    assert vm_budget.deadline == deadline and value.store.strict_cleanup_confirmed()


def seed_contiguous_recording(store, group, *, count=31, seconds=10.5):
    """325.5 actual seconds, 31 bounded canonical original/work source pairs.

    There is no sparse timeline, oversized individual source, or real-time wait.
    Each source has distinct constant PCM, allowing honest review evidence keys.
    Total source+work WAV bytes: 20,834,728 (31 * 2 * 336,044).
    """
    root = store.settings.data_dir / 'uploads'
    root.mkdir(exist_ok=True)
    sid = store.create_session('generated native frontier', group, bank.MODE)['id']
    bid = uuid.uuid4().hex
    stream = 'recording.' + bid
    frames = round(seconds * RATE)
    with store.db() as db:
        db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,state,
            capture_origin_frame,capture_end_frame,created) VALUES(?,?,?,'queued',0,?,0.)''',
            (bid, sid, 'generated-payload-frontier', count * frames))
    values = []
    for index in range(count):
        pcm = (index + 1).to_bytes(2, 'little', signed=True) * frames
        encoded = wav_bytes(pcm)
        source_id = uuid.uuid4().hex
        original = root / f'frontier-{index:02d}-original.audio'
        work = root / f'frontier-{index:02d}-work.audio'
        original.write_bytes(encoded)
        work.write_bytes(encoded)
        digest = hashlib.sha256(encoded).hexdigest()
        position = index * frames
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            cid, created = store._add_chunk(db, sid, f'frontier-{index:02d}', digest,
                work, seconds, stream_id=stream, stream_final=index == count - 1, _status='staging')
            assert created
            chunk = db.execute('SELECT timeline_start FROM chunks WHERE id=?', (cid,)).fetchone()
            assert chunk[0] == position / RATE
            receipt = Receipt(sid, cid, digest, frames, 44, len(encoded), signature(work),
                              position / RATE, stream)
            db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,
                capture_start_frame,native_receipt_status,native_receipt,receipt_id,receipt_state,file_signature)
                VALUES(?,?,?,?,?,?,'durable',?,?,'durable',?)''',
                (cid, bid, index, frames, len(encoded), position, receipt.encoded, receipt.receipt_id,
                 json.dumps(list(receipt.signature), separators=(',', ':'))))
            db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,
                frames,bytes,source_start_frame,format,created) VALUES(?,?,?,?,?,?,?,?,?,0.)''',
                (source_id, bid, f'frontier-{index:02d}', digest, str(original), frames,
                 len(encoded), position, recording.FORMAT))
            db.execute('''INSERT INTO recording_ranges(batch_id,source_id,chunk_id,
                source_start_frame,capture_start_frame,work_start_frame,frames,relation)
                VALUES(?,?,?,0,?,0,?,'owner')''', (bid, source_id, cid, position, frames))
            db.execute("UPDATE chunks SET status='queued' WHERE id=?", (cid,))
        values.append(SimpleNamespace(store=store, sid=sid, cid=cid, bid=bid, source_id=source_id,
            original=original, work=work, encoded=encoded, pcm=pcm, receipt=receipt))
    return values


def support_value(value):
    with value.store.db() as db:
        db.execute('BEGIN')
        value.support = dict(db.execute('SELECT * FROM speaker_support WHERE chunk_id=? ORDER BY window_start LIMIT 1',
                                       (value.cid,)).fetchone())
        value.vector = dict(db.execute('SELECT * FROM speaker_support_vectors WHERE support_id=?',
                                      (value.support['id'],)).fetchone())
        if value.vector['raw_vector'] is None:
            with value.store.payload_resolver(db) as resolver:
                value.vector = dict(support_vectors.read(db, value.support['id'], payload=resolver)[0])
    value.audio = support.audio(value.store, value.sid, value.support['id'])
    return value


def review_request(value, *, person=None):
    state = bank.bank(value.store, value.sid)
    result = {'expected_generation': state['generation'], 'expected_revision': state['profile_revision'],
        'idempotency_key': uuid.uuid4().hex, 'kind': 'positive',
        'support_id': value.support['id'], 'vector_id': value.vector['id'],
        'manifest_sha256': value.support['manifest_sha256'],
        'vector_sha256': value.vector['vector_sha256'], 'space_id': value.vector['space_id'],
        'attestation': {'version': bank.ATTESTATION, 'reviewed_all_pooled_support': True,
            'single_identified_voice': True, 'no_other_overlapping_voice': True}}
    result['person_id' if person else 'new_person_name'] = person or 'Synthetic reviewed person'
    return result


def original_unit_rows(value):
    result = {}
    with value.store.db() as db:
        for table, key in (('chunks', 'id'), ('segments', 'chunk_id'), ('speaker_turns', 'chunk_id'),
                           ('strict_recording_outcome', 'cid'), ('native_core_commits', 'cid'), (pp.TABLE, 'cid')):
            result[table] = [dict(row) for row in db.execute('SELECT * FROM ' + table + ' WHERE ' + key + '=? ORDER BY rowid',
                                                          (value.cid,))]
    return result


def assert_primary_evidence_body(raw, sid, ref, original_rows):
    """Verify the original bounded JSON-bytes API and immutable ASR identity."""
    from audio_backend import transcript_contract as contract
    assert type(raw) is bytes and 0 < len(raw) <= contract.MAX_RESPONSE_BYTES
    decoded = contract.parse_json(raw, contract.MAX_RESPONSE_BYTES)
    assert type(decoded) is dict and set(decoded) == {
        'source_ref', 'source', 'content_sha256', 'home_block', 'start_frame', 'end_frame',
        'text', 'raw_text', 'timestamp_kind', 'speaker_scope', 'word_times',
        'current_evidence', 'person_identity', 'canonical_asr'}
    assert raw == contract.encoded(decoded)
    kind, ident, content_hash, index = contract.parse_reference(ref)
    assert kind == 'primary' and index is None
    original, = [row for row in original_rows if row['id'] == ident]
    assert decoded == contract.primary_evidence(sid, original)
    assert decoded['source_ref'] == ref and decoded['source'] == 'primary'
    assert decoded['content_sha256'] == content_hash == hashlib.sha256(
        contract.encoded(decoded['canonical_asr'])).hexdigest()
    assert set(decoded['canonical_asr']) == {'session_id', *contract.PRIMARY_FIELDS}
    assert decoded['canonical_asr']['session_id'] == sid and decoded['canonical_asr']['id'] == ident
    assert decoded['text'] == decoded['canonical_asr']['text'] == 'generated primary'
    assert decoded['raw_text'] == decoded['canonical_asr']['raw_text'] == 'generated primary'
    assert decoded['start_frame'] < decoded['end_frame']
    assert decoded['current_evidence'] == 'exact_current_asr_content'
    assert decoded['person_identity'] == 'not_projected' and decoded['word_times'] is None


def _cohort_diagnostic(cohort, error):
    """Fixed scalar observations only: no SQL, filesystem census or sweep."""
    worker, capture, actor = cohort.worker, cohort.capture, cohort.actor
    branch = getattr(error, 'payload_owner_state', None)
    actual = None
    if isinstance(branch, (dict, MappingProxyType)):
        actual = {}
        for index, (key, value) in enumerate(branch.items()):
            if index == 16:break
            if type(value) in (bool, int, str, type(None)):
                actual[str(key)[:48]] = value[:160] if type(value) is str else value
    return {'error_class': type(error).__name__[:80], 'error': str(error)[:240],
        'actual_classifier_snapshot': actual,
        'supplemental_observation_not_failure_census': {
            'actor_status': str(actor._status)[:120], 'actor_closed': actor._closed,
            'actor_admission_closed': actor._admission_closed,
            'actor_revoke_pending': actor._publication_revoke_pending,
            'actor_cleanup_pending': actor._cleanup_pending, 'actor_reconcile': actor._reconcile.is_set(),
            'actor_ready': actor._ready.is_set(), 'actor_alive': actor._thread is not None and actor._thread.is_alive(),
            'capture_uncertain': capture._strict_cleanup_uncertain,
            'store_uncertain': cohort.store.strict_cleanup_uncertain(),
            'worker_failed': worker._strict_cleanup_failed, 'worker_alive': worker.thread.is_alive(),
            'owner_links_current': (worker.native_owned_cells is capture and capture.owner is actor
                and capture.authority.owner is actor and actor.authority is capture.authority),
            'epoch_current': capture.core_seams is not None and capture.core_seams.generation is actor._epoch}}


def cohort_diagnostic(cohort, error):
    try:return _cohort_diagnostic(cohort, error)
    except BaseException as diagnostic_error:
        if 'diagnostic_error' not in PENDING[2]:PENDING[2]['diagnostic_error'] = diagnostic_error
        return {'diagnostic_failed': type(diagnostic_error).__name__[:80]}


def remember_cohort_refusal(cohort, error):
    slot = PENDING[2]
    if slot['first_refusal'] is None:
        slot['first_refusal'] = cohort_diagnostic(cohort, error)


def emit_cohort_diagnostic(record_property, name, value):
    # A diagnostic failure cannot replace the original body/cleanup exception.
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
        assert len(encoded.encode('utf-8')) <= 4096
        record_property(name, encoded)
    except BaseException as error:
        if 'diagnostic_error' not in PENDING[2]:PENDING[2]['diagnostic_error'] = error


def wait_original_event(event, cohort, description, *, require_alive=True):
    worker = cohort.worker
    deadline = time.monotonic() + 20.
    while True:
        error = worker._payload_maintenance_error or worker._strict_first_error
        if error is not None:
            remember_cohort_refusal(cohort, error)
            raise error.with_traceback(error.__traceback__)
        if event.is_set():break
        if not worker.thread.is_alive():
            raise AssertionError(description + ': original Worker exited before event')
        remaining = deadline - time.monotonic()
        assert remaining > 0., description
        event.wait(min(.05, remaining))
    if require_alive:
        assert worker.thread.is_alive() and worker._payload_maintenance_error is None
        assert not worker.store.strict_cleanup_uncertain()


def adapt_numerical_failure_retirement(cohort, monkeypatch):
    """Only the retained numerical producer lacks a subprocess reap contract.

    Close that exact producer through original Coordinator.close on the original
    Worker thread before delegating the unchanged original retirement. Never
    manufacture proc/poll/wait, recover uncertainty or attempt a second close.
    """
    from audio_backend.native_worker import NativeCoordinator
    worker = cohort.worker
    original_retire = worker._strict_retire
    def retire():
        error = worker._payload_maintenance_error or worker._strict_first_error
        if error is not None:remember_cohort_refusal(cohort, error)
        native = worker.native
        process = native.process if type(native) is NativeCoordinator else None
        if (threading.current_thread() is worker.thread
                and type(process) is NumericalNativeTransport
                and process.worker is worker and any(process is item for item in cohort.transports)
                and not process.closed and not worker._strict_cleanup_failed
                and not cohort.store.strict_cleanup_uncertain()
                and not cohort.capture._strict_cleanup_uncertain
                and not cohort.permit._handles and cohort.permit._unavailable is None
                and cohort.permit._primary_observation_failure is None):
            worker._strict_native_owner = process
            try:
                native.close()
                assert process.closed and native.process is None
            except BaseException as cleanup:
                worker._strict_unknown(cleanup)
                return False
        return original_retire()
    monkeypatch.setattr(worker, '_strict_retire', retire)


def build_original_cohort(store, lock, analyzer_factory):
    from audio_backend.worker import Worker
    from audio_backend.native_execution_permit import NativeExecutionPermit
    from audio_backend.native_owned_support_cache import NativeOwnedSupportCache
    from audio_backend.native_owned_cell_jobs import CellJobsSource
    from audio_backend.moss_result_store import MossResultStore
    from audio_backend.moss_handy_jobs import MossJobs
    from audio_backend.moss_automatic import Automatic
    from audio_backend.settings import primary_only_enabled, primary_only_configuration
    worker = Worker(store, store.settings, analyzer_factory=analyzer_factory)
    capture = capture_module.register(store, instance_lock=lock)
    worker.native_owned_cells = capture
    capture._strict_failure_owner = capture.authority._strict_failure_owner = worker
    cache = NativeOwnedSupportCache(store)
    worker.native_owned_support_cache = cache
    permit = NativeExecutionPermit(store.settings)
    store.settings._native_execution_permit = permit
    configuration_snapshot = primary_only_configuration(store.settings)
    permit._configuration_current = lambda: (
        primary_only_enabled(store.settings)
        and primary_only_configuration(store.settings) == configuration_snapshot
        and store.settings._native_execution_permit is permit
        and worker.native_owned_support_cache is cache and worker.native_owned_cells is capture
        and capture.authority.owner is capture.owner)
    source = CellJobsSource(capture.owner, store, config_allowed=permit._configuration_current)
    results = MossResultStore(store)
    jobs = MossJobs(store, cache, permit, cell_source=source, result_store=results)
    automatic = Automatic(store, results, capture=capture, jobs=jobs)
    jobs.automatic = automatic
    worker.moss_automatic = store.moss_automatic = automatic
    # Source-backed optional constructor: no speaker-probe/history producer is
    # attached in this model-free fixture. The real manager/permit/root links
    # stay intact; no thread factory or readiness predicate is faked.
    assert jobs.history is None and jobs.speaker_links is None
    assert store.settings.moss_jobs_enabled is False and store.settings.moss_automatic_enabled is False
    assert automatic.primary_only is True and automatic.current() is False
    # These exact links are checked again by the production frontier guard.
    assert capture.owner.qualification is not None
    return SimpleNamespace(store=store, worker=worker, capture=capture, actor=capture.owner,
        cache=cache, permit=permit, jobs=jobs, automatic=automatic, lock=lock,
        transports=[], worker_started=False, actor_started=False)


def close_numerical_transport_on_worker(cohort):
    """Positive test-producer close, not a claimed native process reap."""
    worker = cohort.worker
    assert threading.current_thread() is worker.thread and worker.current_chunk is None
    assert not worker._strict_active and worker._strict_intent_owner is None
    native = worker.native
    if native is not None:
        assert type(native.process) is NumericalNativeTransport
        assert not cohort.permit._handles
        original = native.process
        native.close()
        assert original.closed and native.process is None
        worker.native = None
    worker.stop_event.set()
    worker.wake.set()


def finish_original_cohort(cohort, control):
    """Single original shutdown attempt; never retry uncertain close/join."""
    control.abort = True
    control.release_first.set()
    control.release_cold.set()
    if cohort.worker_started:
        if cohort.worker.thread.is_alive():
            deadline = time.monotonic() + 20.
            while not control.idle_closed.is_set() and cohort.worker.thread.is_alive():
                remaining = deadline - time.monotonic()
                assert remaining > 0., 'original Worker did not reach positive fixture-producer closure'
                control.idle_closed.wait(min(.05, remaining))
        assert cohort.worker.stop() is True
        assert not cohort.worker.thread.is_alive()
        cohort.worker.thread.join(0)
        assert not cohort.worker.thread.is_alive()
    assert not cohort.worker.store.strict_cleanup_uncertain()
    assert cohort.jobs.stop() is True
    assert capture_module.unregister(cohort.capture) is True
    assert not cohort.actor._thread.is_alive() and cohort.actor._db is None
    cohort.actor._thread.join(0)
    assert not cohort.actor._thread.is_alive()
    cohort.lock.release()
    positive_close(cohort.store)


def test_worker_payload_handoff_cold_review_reopen_and_manual_tail(tmp_path, monkeypatch, record_property):
    """The sole original two-thread connected case; exactly two Thread starts.

    Original Worker.loop/_run_strict_chunk/process/_process_windows/extract_pcm,
    NativeCoordinator, Capture, Store commit/cleanup and payload handoff execute.
    Only external decoder execution, native numerical transport and analyzer
    inference are synthetic. Runtime qualification/factory and actor are original.
    """
    from audio_backend import worker as worker_module
    from audio_backend import reviewed_primary_persons as bridge
    from audio_backend import primary_voice_references as voices
    from audio_backend.resources import InstanceLock
    from audio_backend.transcript_store import TranscriptStore
    from audio_backend.native_owned_cell_store import Operation
    from audio_backend.native_owned_support_cache import ReadResponseBudget
    from reviewed_person_bridge_fixtures import GROUP, activation_request
    cfg = configuration(tmp_path)
    cfg.window_seconds = 10.5
    cfg.context_seconds = 2.  # Original inherited profile, not a zero-context shortcut.
    (tmp_path / 'tmp').mkdir()
    decoder = tmp_path / 'generated-canonical-decoder'
    decoder.write_bytes(b'SYNTHETIC CANONICAL COPY MARKER; NEVER EXECUTED\n')
    decoder.chmod(0o700)
    cfg.ffmpeg = str(decoder)
    store = Store(tmp_path / 'primary.sqlite', cfg)
    change_feed.initialize(store)
    lock = InstanceLock(tmp_path / 'instance.lock')
    lock.acquire()
    store._reviewed_person_response_budget = ReadResponseBudget()
    bridge.activate_bank(store, GROUP, activation_request())
    values = seed_contiguous_recording(store, GROUP)
    control = SimpleNamespace(first=threading.Event(), cold=threading.Event(),
        release_first=threading.Event(), release_cold=threading.Event(),
        idle_closed=threading.Event(), abort=False, first_seen=False, cold_seen=False)
    cohort = None
    class ControlledAnalyzer(NumericalAnalyzer):
        def analyze(self, path, *, diarization, **kwargs):
            worker = cohort.worker
            if worker.current_chunk == values[1].cid and not control.first_seen:
                control.first_seen = True
                control.first.set()
                if not control.release_first.wait(20.):raise RuntimeError('fixture first review timeout')
            if worker.current_chunk == values[30].cid and not control.cold_seen:
                control.cold_seen = True
                control.cold.set()
                if not control.release_cold.wait(20.):raise RuntimeError('fixture cold review timeout')
            current = store.chunk(worker.current_chunk)
            self.owned_start = current['next_offset'] - diarization['extract_start']
            return super().analyze(path, diarization=diarization, **kwargs)
    cohort = build_original_cohort(store, lock, ControlledAnalyzer)
    # Fixed owners/handles are retained before either Thread.start can execute.
    PENDING[2] = {'cohort': cohort, 'control': control, 'error': None, 'first_refusal': None}
    THREAD_CASE_OWNERS.update(worker=cohort.worker, actor=cohort.actor)
    adapt_numerical_failure_retirement(cohort, monkeypatch)
    monkeypatch.setattr(worker_module, 'run_bounded', canonical_copy_decoder)
    def transport_factory(settings):
        assert settings is cfg
        transport = NumericalNativeTransport(cohort.worker)
        cohort.transports.append(transport)
        return transport
    monkeypatch.setattr(cohort.worker, '_native_process_factory', transport_factory)
    original_claim = store.claim
    def observed_claim(*args, **kwargs):
        # Test-abort closure only at the original between-chunk idle boundary.
        if control.abort:
            close_numerical_transport_on_worker(cohort)
            control.idle_closed.set()
            return None
        result = original_claim(*args, **kwargs)
        if result is None:
            # Normal case: original claim proves queue absence, after previous
            # original cleanup and automatic handoff have both returned.
            close_numerical_transport_on_worker(cohort)
            control.idle_closed.set()
        return result
    monkeypatch.setattr(store, 'claim', observed_claim)
    original_error = None
    try:
        cohort.actor.start()
        cohort.actor_started = True
        assert cohort.actor._ready.wait(5.), cohort.actor.health()
        for value in values:
            operation = cohort.actor.submit('receipt', value.receipt)
            assert type(operation) is Operation
            assert operation.done.wait(2.)
            assert cohort.actor.receipt(value.cid) == value.receipt, operation.result
        cohort.worker.start()
        cohort.worker_started = True
        wait_original_event(control.first, cohort, 'first original primary commit')
        a = support_value(values[0])
        original_a = original_unit_rows(a)
        with store.db() as db:
            assert db.execute('SELECT count(*) FROM strict_payload_unit').fetchone()[0] == 0
            assert db.execute('SELECT length(pcm) FROM speaker_support WHERE id=?', (a.support['id'],)).fetchone()[0] > 0
        first_review = bank.mutate(store, a.sid, review_request(a))
        first_person = first_review['person_id']
        control.release_first.set()
        wait_original_event(control.cold, cohort, 'original epoch-frontier handoff')
        b = support_value(values[1])
        with store.db() as db:
            for value in (a, b):
                assert db.execute('SELECT pcm FROM speaker_support WHERE id=?', (value.support['id'],)).fetchone()[0] is None
                assert db.execute('SELECT raw_vector FROM speaker_support_vectors WHERE id=?',
                                  (value.vector['id'],)).fetchone()[0] is None
            assert db.execute("SELECT count(*) FROM strict_payload_unit WHERE state='committed'").fetchone()[0] == 2
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert original_unit_rows(a) == original_a
        exact_reads(a)
        exact_reads(b)
        assert any(row['chunk_id'] == a.cid for row in store.results(a.sid)['segments'])
        assert any(row['chunk_id'] == a.cid for row in store.speaker_turns(a.sid)['speaker_turns'])
        with store.db() as db:
            db.execute('BEGIN')
            packet = pp.read_exact(db, cid=a.cid, start=0, end=a.receipt.frames)
            primary_ref = packet['packet']['refs'][0][0]
        evidence = TranscriptStore(store).evidence(a.sid, primary_ref)
        assert_primary_evidence_body(evidence, a.sid, primary_ref, original_a['segments'])
        second_review = bank.mutate(store, b.sid, review_request(b, person=first_person))
        assert second_review['person_id'] == first_person
        # Actual original old-cold gallery and vector/projection validators;
        # no fake trust rows, packet sequence adapter, or omitted candidate.
        # process_one/_compute propagation is source-reviewed only in this slice.
        with bridge._db(store) as (db, check):
            policy = bank.group_policy(db, GROUP)
            generation = db.execute('SELECT generation FROM speaker_identity_generations WHERE identity_group=?',
                                    (GROUP,)).fetchone()[0]
            canonical = bridge._canonical(db, GROUP, generation, policy)
            with support._payload_context(store, db, check=check) as resolver:
                gallery = list(voices._gallery(db, GROUP, generation,
                    a.vector['space_id'], canonical, payload=resolver))
        assert len(gallery) == 1 and gallery[0][0] == first_person
        assert len(gallery[0][1]) == 2 and all(vector[0] == 1. for vector in gallery[0][1])
        assert set(gallery[0][2]) == {first_review['event_id'], second_review['event_id']}
        before_next = original_unit_rows(a)
        control.release_cold.set()
        wait_original_event(control.idle_closed, cohort, 'original next commit and queue drain', require_alive=False)
        assert store.chunk(values[-1].cid)['status'] == 'completed'
        assert original_unit_rows(a) == before_next
        assert all(transport.closed for transport in cohort.transports)
    except BaseException as error:
        original_error = error
        PENDING[2]['error'] = error
        remember_cohort_refusal(cohort, error)
        raise
    finally:
        try:finish_original_cohort(cohort, control)
        except BaseException as cleanup:
            PENDING[2]['cleanup_error'] = cleanup
            emit_cohort_diagnostic(record_property, 'payload_cohort_cleanup_error', cohort_diagnostic(cohort, cleanup))
            if original_error is None:raise
        finally:
            if PENDING[2]['first_refusal'] is not None:
                emit_cohort_diagnostic(record_property, 'payload_cohort_first_refusal', PENDING[2]['first_refusal'])
    # Both exact original threads have positively joined. This proves data
    # reopen, not a supervisor restart or a second inference cohort.
    reopened = Store(store.path, cfg)
    reopened.moss_automatic = SimpleNamespace(primary_only=True)
    for value in (a, b):
        value.store = reopened
        exact_reads(value)
    for event in (first_review, second_review):
        detail = bank.event_detail(reopened, a.sid, event['event_id'])
        assert detail['id'] == event['event_id'] and detail['source']['audio_available'] is True
    reopened_evidence = TranscriptStore(reopened).evidence(a.sid, primary_ref)
    assert_primary_evidence_body(reopened_evidence, a.sid, primary_ref, original_a['segments'])
    assert reopened_evidence == evidence
    tail = seed_source(reopened, tmp_path / 'uploads', seconds=4, name='manual-unarchived-tail')
    first = reopened.claim()
    assert first['id'] == tail.cid
    assert reopened.commit_window(first, 0., 2., prepared(tail, first))
    reopened.strict_pause(first, 'synthetic explicit manual boundary', cleanup_confirmed=True)
    with reopened.db() as db:
        accepted = [tuple(row) for row in db.execute('SELECT * FROM strict_recording_outcome WHERE cid=?', (tail.cid,))]
    reopened.set_state(tail.cid, 'retry')
    retried = reopened.claim()
    assert retried['id'] == tail.cid and retried['next_offset'] == 2.
    assert retried['_strict_attempt_nonce'] != first['_strict_attempt_nonce']
    with reopened.db() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM strict_recording_outcome WHERE cid=?', (tail.cid,))] == accepted
        assert db.execute('SELECT 1 FROM strict_payload_unit WHERE cid=?', (tail.cid,)).fetchone() is None
    # Durable manual admission only after reopen, deliberately no extra threads.
    reopened.strict_pause(retried, 'end of model-free manual admission case', cleanup_confirmed=True)
    positive_close(reopened)

