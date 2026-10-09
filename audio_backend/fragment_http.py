"""Explicit finalized PCM16 HTTP experiment. No implicit model/provider defaults.

The ordinary Worker cannot claim these receipts. Provider factories implement the
existing LogicalRunner/AnalyzerBridge contracts; all public output is validated
against durable raw evidence before its atomic core transaction can commit.
"""
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import threading
import time
from pathlib import Path

from .fragment_core import Source, SourcePlan, Span, Seam, inspect_pcm16_source
from .fragment_matcher import BoundaryEvidence, Continuity
from .fragment_evidence import EvidenceStore, MAX_EVIDENCE_BYTES, SCHEMA, validate_reference
from .fragment_runtime import LogicalRunner
from .fragment_runtime_store import RuntimeStore
from .fragment_runtime_contracts import (
    RATE, RuntimePolicy, StaleWork, CorruptLedger, bounded_json, canonical, digest, own_intervals,
)

MODE = 'fragment_pcm16_v1'
PROJECTION = 'fragment-http-public-v1'
OWNERSHIP_PENDING = 'Provider ownership release is pending; independent cleanup verification required'
PUBLIC_FIELDS = ('projection_version', 'source_track_id', 'evidence_sha256', 'evidence_item')


def init_schema(db):
    if 'processing_mode' not in {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}:
        db.execute("ALTER TABLE sessions ADD COLUMN processing_mode TEXT NOT NULL DEFAULT 'legacy'")
    db.execute('''CREATE TABLE IF NOT EXISTS fragment_http_sessions (
        session_id TEXT PRIMARY KEY REFERENCES sessions(id), stream_id TEXT,
        state TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0)''')
    if 'cleanup_error' not in {row['name'] for row in db.execute('PRAGMA table_info(fragment_http_sessions)')}:
        db.execute('ALTER TABLE fragment_http_sessions ADD COLUMN cleanup_error TEXT')
    db.execute('CREATE INDEX IF NOT EXISTS fragment_http_queue ON fragment_http_sessions(state,session_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS fragment_http_sources (
        chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
        session_id TEXT NOT NULL REFERENCES sessions(id), source TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS fragment_http_source_session ON fragment_http_sources(session_id,chunk_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS fragment_http_evidence (
        session_id TEXT NOT NULL REFERENCES sessions(id), sha256 TEXT NOT NULL,
        bytes INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(session_id,sha256))''')
    for table in ('segments', 'events', 'speaker_turns'):
        columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
        for field in PUBLIC_FIELDS:
            if field not in columns:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {field} TEXT')


def is_experimental(store, sid):
    return store.session(sid)['processing_mode'] == MODE


def no_identity_mutation(store, sid):
    if is_experimental(store, sid):
        raise ValueError('Global identity learning and corrections are disabled for fragment_pcm16_v1')


def erase(db, sid):
    """Called after the session tombstone, in the same erasure transaction."""
    if not db.execute('SELECT 1 FROM fragment_http_sessions WHERE session_id=?', (sid,)).fetchone():
        return
    for table in ('fragment_core_commits', 'fragment_controls', 'fragment_plans', 'fragment_runs'):
        db.execute(f'DELETE FROM {table} WHERE run_id=?', (sid,))
    db.execute('DELETE FROM fragment_http_evidence WHERE session_id=?', (sid,))
    db.execute('DELETE FROM fragment_http_sources WHERE session_id=?', (sid,))
    db.execute('DELETE FROM fragment_http_sessions WHERE session_id=?', (sid,))


class SQLiteEvidenceStore:
    """AnalyzerBridge-compatible evidence, scoped to one live HTTP session.

    Durable database bytes survive tmp cleanup. Failed-call evidence may precede
    a core, but erasure removes both committed and orphan evidence atomically.
    No filesystem lease or cleanup inventory is needed for this isolated store.
    """
    def __init__(self, store, sid, generation=None):
        self.store, self.sid, self.generation = store, sid, generation

    def put(self, value):
        from .resources import check_resources
        text = bounded_json(value, MAX_EVIDENCE_BYTES)
        encoded = text.encode()
        ref = {'schema': SCHEMA, 'sha256': hashlib.sha256(encoded).hexdigest(), 'bytes': len(encoded)}
        check_resources(self.store.settings, len(encoded))
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            owner = db.execute('''SELECT s.deleted,h.state,h.generation FROM sessions s
                JOIN fragment_http_sessions h ON h.session_id=s.id WHERE s.id=?''', (self.sid,)).fetchone()
            if owner is None or owner['deleted'] or owner['state'] == 'cancelled' or self.generation is not None and (
                owner['state'] != 'running' or owner['generation'] != self.generation
            ):
                raise StaleWork('Session was erased or controlled; stale evidence write refused')
            db.execute('INSERT OR IGNORE INTO fragment_http_evidence VALUES(?,?,?,?)',
                       (self.sid, ref['sha256'], ref['bytes'], text))
        self.read(ref)
        return ref

    def read(self, ref):
        validate_reference(ref)
        with self.store.db() as db:
            row = db.execute('''SELECT e.* FROM fragment_http_evidence e JOIN sessions s ON s.id=e.session_id
                WHERE e.session_id=? AND e.sha256=? AND s.deleted=0''', (self.sid, ref['sha256'])).fetchone()
        if row is None or row['bytes'] != ref['bytes']:
            raise CorruptLedger('Missing session evidence')
        encoded = row['payload'].encode()
        if len(encoded) != ref['bytes'] or hashlib.sha256(encoded).hexdigest() != ref['sha256']:
            raise CorruptLedger('Session evidence hash mismatch')
        value = json.loads(row['payload'])
        if canonical(value) != row['payload']:
            raise CorruptLedger('Noncanonical session evidence')
        return value

    verify_core = EvidenceStore.verify_core


def _number(value, name, *, optional=False):
    if value is None and optional:
        return None
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Invalid genuine evidence ' + name)
    return value


def _text(value, name, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('Invalid bounded evidence ' + name)
    return value


def public_projection(payload, evidence):
    """Versioned projection, preserving uncertainty and every overlapping turn.

    Scores/local labels come only from raw analyzer items. Matching duplicate raw
    units with disagreeing attribution remain unassigned. Evidence references
    identify the original item set; they are not asserted as global identities.
    """
    if evidence.get('projection_version') != 'real-provider-projection-v1':
        raise ValueError('Unsupported raw provider projection version')
    expected = {key: payload[key] for key in ('segments', 'turns', 'events', 'tail')}
    if canonical(evidence.get('publication_projection')) != canonical(expected):
        raise ValueError('Raw evidence publication projection differs from committed core')
    raw = evidence.get('raw_analyzer_result')
    if not isinstance(raw, dict) or not isinstance(raw.get('segments'), list) or not isinstance(raw.get('events'), list):
        raise ValueError('Public projection requires actual raw analyzer evidence')
    if raw.get('overlap_candidates'):
        raise ValueError('Separate overlap candidate publication is not supported by this narrow mode')
    base = payload['window']['read_start'] / RATE
    candidates = {}
    for si, segment in enumerate(raw['segments']):
        for ui, unit in enumerate(segment['_units']):
            key = (unit['start'] + base, unit['end'] + base, unit['ownership_anchor'] + base, unit['text'].strip())
            candidates.setdefault(key, []).append((si, ui, segment, unit))
    rows = {'segments': [], 'events': [], 'speaker_turns': []}
    metadata = raw.get('metadata', {})
    for item in payload['segments']:
        key = (item['start'], item['end'], item['ownership_anchor'], item['text'])
        matches = candidates.get(key)
        if not matches:
            raise ValueError('Published text has no matching raw native support')
        locals_ = {entry[2].get('speaker_local_id') for entry in matches}
        scores = {_number(entry[3].get('confidence'), 'confidence', optional=True) for entry in matches}
        raw_texts = {entry[3].get('raw_text',entry[3]['text']) for entry in matches}
        timing_kinds = {_text(entry[2].get('timestamp_kind') or metadata.get('timestamp_semantics'),
                             'timestamp semantics', optional=True) for entry in matches}
        local = next(iter(locals_)) if len(locals_) == 1 else None
        scope = item['source_track_id'].split(':', 1)[0]
        if local is not None:
            local = scope + ':' + _text(local, 'epoch-local speaker')
        rows['segments'].append(dict(start=item['start'], end=item['end'], text=item['text'],
            raw_text=next(iter(raw_texts)) if len(raw_texts)==1 else None,
            speaker_local_id=local, speaker_status='epoch_local_only' if local else 'unknown',
            confidence=next(iter(scores)) if len(scores) == 1 else None,
            asr_backend=_text(metadata.get('asr_backend'), 'ASR backend', optional=True),
            timestamp_kind=next(iter(timing_kinds)) if len(timing_kinds)==1 else None,
            source_track_id=item['source_track_id'], evidence_item=canonical({'raw_units': [[m[0], m[1]] for m in matches]})))
    events = {digest([payload['window_id'], 'event', i, event]): (i, event) for i, event in enumerate(raw['events'])}
    for item in payload['events']:
        if item['instance_id'] not in events:
            raise ValueError('Published event has no raw model item')
        i, source = events[item['instance_id']]
        from .fragment_model_bridges import frame
        if (item['event'] != source['label'] or item['native_start_frame'] != payload['window']['read_start'] + frame(source['start'])
            or item['native_end_frame'] != payload['window']['read_start'] + frame(source['end'])):
            raise ValueError('Event support differs from raw model evidence')
        precision = source.get('onset_precision_seconds')
        if precision is not None and (type(precision) not in (int,float) or not math.isfinite(precision) or precision<0):
            raise ValueError('Invalid genuine evidence onset precision')
        rows['events'].append(dict(start=item['start_frame']/RATE, end=item['end_frame']/RATE,
            label=_text(source['label'], 'event label'), score=_number(source.get('score'), 'event score'),
            time_precision=_text(source.get('time_precision'), 'time precision', optional=True),
            source_label=_text(source.get('source_label'), 'source label', optional=True),
            method=_text(source.get('method'), 'event method', optional=True), onset_precision_seconds=precision,
            verification_status='unverified_inference', inference_source='model_or_rule_raw_evidence',
            source_track_id=item['instance_id'], evidence_item=canonical({'raw_event': i})))
    native = evidence.get('native', {})
    status = native.get('status', {})
    scope = digest(native.get('key'))
    if canonical(own_intervals(status.get('turns', ()), payload['window']['core_start'],
                               payload['window']['core_end'], scope)) != canonical(payload['turns']):
        raise ValueError('Published speaker turns differ from native evidence')
    for i, item in enumerate(payload['turns']):
        rows['speaker_turns'].append(dict(start=item['start_frame']/RATE, end=item['end_frame']/RATE,
            speaker_local_id=scope + ':' + item['speaker'], speaker_status='epoch_local_only',
            overlap=int(any(other['speaker'] != item['speaker'] and min(other['end_frame'], item['end_frame']) >
                            max(other['start_frame'], item['start_frame']) for other in payload['turns'])),
            source_track_id=scope + ':' + item['speaker'], evidence_item=canonical({'owned_native_turn': i})))
    bounded_json(rows)
    return rows


class HTTPRuntimeStore(RuntimeStore):
    def __init__(self, owner, sid, generation=None, fault=None):
        self.owner, self.sid, self.generation = owner, sid, generation
        super().__init__(owner.path, fault=fault, evidence_store=SQLiteEvidenceStore(owner, sid, generation))

    def _snapshot(self, db, run_id):
        if run_id != self.sid:
            raise StaleWork('HTTP runtime session scope mismatch')
        row = db.execute('''SELECT h.state,h.generation,s.deleted FROM fragment_http_sessions h
            JOIN sessions s ON s.id=h.session_id WHERE h.session_id=?''', (run_id,)).fetchone()
        if row is None or row['deleted'] or self.generation is not None and (
            row['generation'] != self.generation or row['state'] != 'running'
        ):
            raise StaleWork('HTTP session state/generation changed')
        return super()._snapshot(db, run_id)

    def _publish_core(self, db, snapshot, payload):
        if 'evidence_ref' not in payload:
            raise ValueError('HTTP publication requires durable raw evidence')
        evidence = self.evidence_store.verify_core(payload['evidence_ref'], payload, self.sid)
        rows = public_projection(payload, evidence)
        # One plan covers a maximum of 128 receipts. Anchor only the storage FK;
        # timestamps remain on the logical session axis, never rebase to chunks.
        cid = db.execute('SELECT id FROM chunks WHERE session_id=? ORDER BY timeline_start LIMIT 1', (self.sid,)).fetchone()[0]
        for table, values in rows.items():
            for value in values:
                value.update(chunk_id=cid, projection_version=PROJECTION, evidence_sha256=payload['evidence_ref']['sha256'])
                db.execute(f"INSERT INTO {table}({','.join(value)}) VALUES({','.join('?' for _ in value)})", list(value.values()))
        final = payload['window']['core_end'] == snapshot.plan.end_frame
        cursor = payload['window']['core_end']/RATE
        db.execute('''UPDATE chunks SET next_offset=MIN(duration,MAX(0,?-timeline_start)),
            status=?,error=NULL WHERE session_id=?''', (cursor, 'completed' if final else 'running', self.sid))
        if final:
            db.execute("UPDATE fragment_http_sessions SET state='completed' WHERE session_id=?", (self.sid,))
        self.fault('public.after_rows')


@dataclass(frozen=True)
class ProviderContext:
    settings: object
    snapshot: object
    paths: dict
    evidence_store: SQLiteEvidenceStore
    temp_root: Path


class FragmentHTTP:
    """Factory.policy is immutable RuntimePolicy; factory(context) returns pair.

    The pair is (evidence-capable analyzer, closeable native factory). Providers
    own their resource supervisors and must close all children when close() is
    called. No neural work occurs during session creation, upload or finish.
    """
    def __init__(self, store, settings, factory=None):
        self.store, self.settings, self.factory = store, settings, factory
        self.active_sid = None
        self.cleanup_error = None
        self.processing = threading.Lock()
        self.fault = lambda point: None
        if factory is not None and not isinstance(getattr(factory, 'policy', None), RuntimePolicy):
            raise ValueError('Explicit fragment provider factory requires a RuntimePolicy')
        self.ledger = RuntimeStore(store.path)
        with store.db() as db:
            pending = db.execute('SELECT session_id,cleanup_error FROM fragment_http_sessions WHERE cleanup_error IS NOT NULL ORDER BY session_id LIMIT 1').fetchone()
            if pending:
                self.active_sid, self.cleanup_error = pending['session_id'], pending['cleanup_error']
            # Preserve immutability except inside the authorized session erasure
            # transaction, which first sets the session's durable tombstone.
            for short, table in (('plan', 'fragment_plans'), ('core', 'fragment_core_commits')):
                db.execute(f'DROP TRIGGER IF EXISTS immutable_{short}_delete')
                db.execute(f'''CREATE TRIGGER immutable_{short}_delete BEFORE DELETE ON {table}
                    WHEN NOT EXISTS(SELECT 1 FROM sessions s JOIN fragment_http_sessions h ON h.session_id=s.id
                        WHERE s.id=OLD.run_id AND s.deleted=1)
                    BEGIN SELECT RAISE(ABORT,'immutable fragment ledger'); END''')
        store.fragment_http = self

    def create_session(self, db, sid):
        if self.factory is None:
            raise ValueError('Experimental fragment provider is not explicitly configured')
        db.execute("INSERT INTO fragment_http_sessions(session_id,state) VALUES(?,'collecting')", (sid,))

    def register_upload(self, db, sid, cid, stream_id, source, stream_final):
        row = db.execute('SELECT * FROM fragment_http_sessions WHERE session_id=?', (sid,)).fetchone()
        if not row or row['state'] != 'collecting' or not stream_id or row['stream_id'] not in (None, stream_id):
            raise ValueError('Experimental mode accepts one explicitly continuous unsealed stream per session')
        source = replace(source, fragment_id=cid)
        if source.format != (RATE, 1, 2, 'wav-pcm16le'):
            raise ValueError('Experimental mode requires original 16 kHz mono PCM16 WAV')
        db.execute('INSERT INTO fragment_http_sources VALUES(?,?,?)', (cid, sid, canonical(asdict(source))))
        db.execute('UPDATE fragment_http_sessions SET stream_id=? WHERE session_id=?', (stream_id, sid))
        db.execute("UPDATE chunks SET status='waiting_input' WHERE session_id=?", (sid,))
        if stream_final:
            self._seal(db, sid, stream_id)

    def _seal(self, db, sid, stream_id):
        row = db.execute('SELECT * FROM fragment_http_sessions WHERE session_id=?', (sid,)).fetchone()
        if not row or row['stream_id'] != stream_id:
            raise KeyError('Stream not found in session')
        if row['state'] == 'cancelled':
            raise ValueError('Cancelled stream cannot declare true EOS')
        last = db.execute('SELECT chunk_id FROM stream_chunks WHERE session_id=? ORDER BY position DESC LIMIT 1', (sid,)).fetchone()
        if row['state'] != 'collecting':
            if not db.execute('SELECT 1 FROM fragment_runs WHERE run_id=?',(sid,)).fetchone():
                raise ValueError('Resume the collecting stream before finishing')
            return {'stream_id': stream_id, 'stream_final': True, 'last_chunk_id': last[0]}
        if self.factory is None:
            raise ValueError('Experimental fragment provider is not configured')
        sources, spans, seams = [], [], []
        cursor = 0
        for item in db.execute('''SELECT c.*,h.source FROM chunks c JOIN fragment_http_sources h ON h.chunk_id=c.id
            WHERE c.session_id=? ORDER BY c.timeline_start LIMIT 129''', (sid,)):
            if len(sources) >= 128:
                raise ValueError('Finalized experiment supports at most 128 fragments')
            source = Source(**json.loads(item['source']))
            if source != inspect_pcm16_source(item['id'], item['path']) or source.sha256 != item['digest']:
                raise ValueError('Uploaded source changed before sealing')
            if item['timeline_start'] != cursor / RATE:
                raise ValueError('Experimental continuity requires exact adjacent frame offsets from zero')
            if sources:
                seams.append(Seam(sources[-1].fragment_id, source.fragment_id,
                    Continuity('continuous', same_capture=True, replay_possible=True),
                    BoundaryEvidence('abstain', 'http_declared_continuity_zero_trim'), cursor, 'declared_continuity_only'))
            spans.append(Span(source.fragment_id, 0, source.frames, cursor, cursor + source.frames, 0))
            sources.append(source)
            cursor += source.frames
        plan = SourcePlan(tuple(sources), tuple(spans), (), tuple(seams), True).validate()
        if plan.end_frame / RATE > self.settings.max_audio_seconds:
            raise ValueError('Finalized stream exceeds configured audio duration limit')
        self.ledger.fault = self.fault
        self.ledger._create_in_transaction(db, sid, plan, self.factory.policy, 'http-finish-v1')
        db.execute('UPDATE stream_chunks SET stream_final=1 WHERE chunk_id=?', (last[0],))
        db.execute("UPDATE fragment_http_sessions SET state='queued' WHERE session_id=?", (sid,))
        db.execute("UPDATE chunks SET status='queued',error=NULL WHERE session_id=?", (sid,))
        self.fault('seal.after_plan')
        return {'stream_id': stream_id, 'stream_final': True, 'last_chunk_id': last[0]}

    def finish(self, sid, stream_id):
        self.store.session(sid)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._seal(db, sid, stream_id)

    def control(self, cid, action):
        states = {'pause': ('collecting', 'queued', 'running'), 'resume': ('paused',), 'retry': ('failed',),
                  'cancel': ('collecting', 'queued', 'running', 'paused', 'failed')}
        target = {'pause': 'paused', 'resume': 'queued', 'retry': 'queued', 'cancel': 'cancelled'}
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT h.* FROM fragment_http_sessions h JOIN chunks c ON c.session_id=h.session_id
                WHERE c.id=?''', (cid,)).fetchone()
            if row is None:
                raise KeyError('Chunk not found')
            if row['state'] not in states[action]:
                raise ValueError(f"Cannot {action} a {row['state']} fragment stream")
            destination = target[action]
            if action == 'resume' and not db.execute('SELECT 1 FROM fragment_runs WHERE run_id=?',(row['session_id'],)).fetchone():
                destination = 'collecting'
            db.execute('UPDATE fragment_http_sessions SET state=?,generation=generation+1 WHERE session_id=?',
                       (destination, row['session_id']))
            db.execute('UPDATE chunks SET status=?,error=NULL WHERE session_id=?',
                       ('waiting_input' if destination == 'collecting' else destination, row['session_id']))
            # Cancellation is not finish. No stream_final mutation or native EOS.
            if action == 'cancel':
                run = db.execute('SELECT * FROM fragment_runs WHERE run_id=?', (row['session_id'],)).fetchone()
                if run:
                    self.ledger._save_state(db, run, status='cancelled', wait_revision=None)

    def recover(self):
        with self.store.db() as db:
            db.execute("UPDATE fragment_http_sessions SET state='queued',generation=generation+1 WHERE state='running'")

    def process_next(self, worker=None):
        if self.factory is None or self.cleanup_error is not None or not self.processing.acquire(blocking=False):
            return False
        sid = None
        analyzer = native = None
        factory_started = factory_returned = False
        try:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM fragment_http_sessions WHERE state='queued' ORDER BY session_id LIMIT 1").fetchone()
                if row is None:
                    return False
                sid = row['session_id']
                db.execute("UPDATE fragment_http_sessions SET state='running',generation=generation+1,cleanup_error=? WHERE session_id=?", (OWNERSHIP_PENDING,sid))
                generation = row['generation'] + 1
                db.execute("UPDATE chunks SET status='running' WHERE session_id=?", (sid,))
                paths = {r['id']: Path(r['path']) for r in db.execute('SELECT id,path FROM chunks WHERE session_id=?', (sid,))}
            self.active_sid = sid
            if worker:
                worker.current_chunk = next(iter(paths))
            runtime = HTTPRuntimeStore(self.store, sid, generation, self.fault)
            snapshot = runtime.snapshot(sid)
            if self.factory.policy != snapshot.policy:
                raise ValueError('Configured provider policy differs from the sealed run')
            context = ProviderContext(self.settings, snapshot, paths, runtime.evidence_store, self.settings.data_dir/'tmp')
            factory_started = True
            analyzer, native = self.factory(context)
            factory_returned = True
            runner = LogicalRunner(runtime, sid, paths, analyzer, native, self.fault)
            while True:
                if worker and worker.stop_event.is_set():
                    with self.store.db() as db:
                        db.execute("UPDATE fragment_http_sessions SET state='queued',generation=generation+1 WHERE session_id=? AND state='running'", (sid,))
                        db.execute("UPDATE chunks SET status='queued' WHERE session_id=? AND status='running'", (sid,))
                    break
                state = runner.step()
                if state == 'completed':
                    break
                if state != 'committed':
                    raise ValueError('Finalized stream cannot wait for more input')
            return True
        except StaleWork:
            return True  # Pause/cancel/delete won its transaction fence.
        except Exception as exc:
            if sid:
                with self.store.db() as db:
                    changed = db.execute("UPDATE fragment_http_sessions SET state='failed' WHERE session_id=? AND state='running' AND generation=?", (sid, generation)).rowcount
                    if changed:
                        db.execute("UPDATE chunks SET status='failed',error=? WHERE session_id=?", (f'{type(exc).__name__}: {exc}'[:2000], sid))
            return True
        finally:
            # One broken close must never prevent the other ownership cleanup,
            # replace the original error, or escape and kill the worker loop.
            errors = []
            try:
                for label, provider in (('analyzer', analyzer), ('native', native)):
                    if provider is not None and hasattr(provider, 'close'):
                        try:
                            provider.close()
                        except BaseException as exc:
                            errors.append(f'{label}: {type(exc).__name__}: {exc}'[:512])
                if factory_started and not factory_returned:
                    errors.append('Factory construction did not return provider ownership; independent cleanup verification required')
                if errors:
                    self.cleanup_error = '; '.join(errors)
                    # Fail closed on future experimental work until ownership
                    # is explicitly repaired. Restart preserves the blocker.
                    # Already published rows stay final.
                    if sid:
                        try:
                            self.fault('cleanup.before_record')
                            with self.store.db() as db:
                                db.execute('UPDATE fragment_http_sessions SET cleanup_error=? WHERE session_id=?',(self.cleanup_error,sid))
                                db.execute("UPDATE chunks SET error=SUBSTR(COALESCE(error||'; ','')||?,1,2000) WHERE session_id=?",
                                           ('Provider cleanup failed: ' + self.cleanup_error, sid))
                        except BaseException:
                            pass  # Pre-armed marker survives even this failure.
                elif sid:
                    try:
                        self.fault('cleanup.before_release')
                        with self.store.db() as db:
                            db.execute('UPDATE fragment_http_sessions SET cleanup_error=NULL WHERE session_id=?',(sid,))
                    except BaseException as exc:
                        self.cleanup_error = f'Cannot persist provider ownership release: {type(exc).__name__}: {exc}'[:1000]
            finally:
                if self.cleanup_error is None:
                    self.active_sid = None
                if worker:
                    worker.current_chunk = None
                self.processing.release()
