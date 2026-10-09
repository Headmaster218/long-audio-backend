"""Isolated SQLite logical-run ledger. No legacy tables or identity writes.

Exactly-once publication, not exactly-once provider invocation. A successful
commit owns one bounded envelope, lexical tail, checkpoint and cursor together.
Connections are short-lived; BEGIN IMMEDIATE and revision/cursor fences serialize
mutations. No process-side checkpoint can override this database.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import sqlite3

from .fragment_core import PlanCheckpoint, SourcePlan
from .fragment_runtime_contracts import (
    CONTRACT, EPOCH, CorruptLedger, RuntimePolicy, StaleWork, admit, bounded_json,
    canonical, control_id, digest, integer, next_window, prefix_hash, validate_commit, window_id,
)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS fragment_runs (
 run_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, plan_hash TEXT NOT NULL,
 policy TEXT NOT NULL, policy_hash TEXT NOT NULL, next_frame INTEGER NOT NULL,
 prefix_hash TEXT NOT NULL, tail TEXT NOT NULL, checkpoint TEXT NOT NULL,
 state_hash TEXT NOT NULL, status TEXT NOT NULL, wait_revision INTEGER
);
CREATE TABLE IF NOT EXISTS fragment_plans (
 run_id TEXT NOT NULL, revision INTEGER NOT NULL, operation_id TEXT NOT NULL,
 plan_hash TEXT NOT NULL, parent_hash TEXT, plan TEXT NOT NULL,
 PRIMARY KEY(run_id,revision), UNIQUE(run_id,operation_id),
 FOREIGN KEY(run_id) REFERENCES fragment_runs(run_id)
);
CREATE TABLE IF NOT EXISTS fragment_core_commits (
 run_id TEXT NOT NULL, island INTEGER NOT NULL, core_start INTEGER NOT NULL,
 core_end INTEGER NOT NULL, window_id TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL,
 payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
 PRIMARY KEY(run_id,island,core_start),
 FOREIGN KEY(run_id,revision) REFERENCES fragment_plans(run_id,revision)
);
CREATE TABLE IF NOT EXISTS fragment_controls (
 run_id TEXT PRIMARY KEY, control_id TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL,
 final_frame INTEGER NOT NULL, epoch INTEGER NOT NULL, reason TEXT NOT NULL,
 response TEXT, response_hash TEXT, acknowledged INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(run_id,revision) REFERENCES fragment_plans(run_id,revision)
);
CREATE TRIGGER IF NOT EXISTS immutable_plan_update BEFORE UPDATE ON fragment_plans
 BEGIN SELECT RAISE(ABORT,'immutable accepted plan'); END;
CREATE TRIGGER IF NOT EXISTS immutable_plan_delete BEFORE DELETE ON fragment_plans
 BEGIN SELECT RAISE(ABORT,'immutable accepted plan'); END;
CREATE TRIGGER IF NOT EXISTS immutable_core_update BEFORE UPDATE ON fragment_core_commits
 BEGIN SELECT RAISE(ABORT,'immutable committed core'); END;
CREATE TRIGGER IF NOT EXISTS immutable_core_delete BEFORE DELETE ON fragment_core_commits
 BEGIN SELECT RAISE(ABORT,'immutable committed core'); END;
'''


@dataclass(frozen=True)
class Snapshot:
    run_id: str
    revision: int
    plan: SourcePlan
    policy: RuntimePolicy
    next_frame: int
    tail: list
    checkpoint: dict
    prefix_hash: str
    status: str
    control: dict | None


class RuntimeStore:
    def __init__(self, path, fault=None, evidence_store=None):
        self.path = str(path)
        self.evidence_store = evidence_store
        self.fault = fault or (lambda point: None)
        with self._connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self, kind):
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                self.fault(kind + '.before_commit')
                db.commit()
            except BaseException:
                db.rollback()
                raise
        self.fault(kind + '.after_commit')

    @staticmethod
    def _state_hash(revision, plan_hash, policy_hash, cursor, prefix, tail, checkpoint, status):
        return digest([revision, plan_hash, policy_hash, cursor, prefix, tail, checkpoint, status])

    def _save_state(self, db, row, **changes):
        value = dict(row)
        value.update(changes)
        value['state_hash'] = self._state_hash(*[value[k] for k in (
            'revision', 'plan_hash', 'policy_hash', 'next_frame', 'prefix_hash', 'tail', 'checkpoint', 'status')])
        names = ('revision', 'plan_hash', 'next_frame', 'prefix_hash', 'tail', 'checkpoint',
                 'state_hash', 'status', 'wait_revision')
        db.execute('UPDATE fragment_runs SET ' + ','.join(k + '=?' for k in names) + ' WHERE run_id=?',
                   [value[k] for k in names] + [value['run_id']])

    @staticmethod
    def _admit(plan):
        return admit(plan)

    @staticmethod
    def _decode_plan(text):
        return SourcePlan.from_json(text)

    @staticmethod
    def _validate_policy(plan, policy):
        if policy.input_fingerprint:
            raise ValueError('Exact-PCM store does not admit transformed input policies')

    @staticmethod
    def _extend_plan(snapshot, plan):
        PlanCheckpoint(snapshot.plan, snapshot.next_frame).extend(plan)

    def _snapshot(self, db, run_id):
        row = db.execute('SELECT * FROM fragment_runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        expected = self._state_hash(*[row[k] for k in (
            'revision', 'plan_hash', 'policy_hash', 'next_frame', 'prefix_hash', 'tail', 'checkpoint', 'status')])
        if row['state_hash'] != expected:
            raise CorruptLedger('Run state hash mismatch')
        saved = db.execute('SELECT * FROM fragment_plans WHERE run_id=? AND revision=?',
                           (run_id, row['revision'])).fetchone()
        if saved is None:
            raise CorruptLedger('Missing accepted plan')
        plan = self._admit(self._decode_plan(saved['plan']))
        policy = RuntimePolicy(**json.loads(row['policy']))
        self._validate_policy(plan, policy)
        if saved['plan_hash'] != plan.sha256 or row['plan_hash'] != plan.sha256 or policy.sha256 != row['policy_hash']:
            raise CorruptLedger('Plan or policy hash mismatch')
        if not 0 <= row['next_frame'] <= plan.end_frame or prefix_hash(plan, row['next_frame']) != row['prefix_hash']:
            raise CorruptLedger('Cursor/accepted owner prefix mismatch')
        control = db.execute('SELECT * FROM fragment_controls WHERE run_id=?', (run_id,)).fetchone()
        if plan.final and control is None:
            raise CorruptLedger('Final plan lacks durable intent')
        if control is not None:
            control = dict(control)
            if (not plan.final or control['control_id'] != control_id(run_id, plan, policy)
                or control['revision'] != row['revision'] or control['final_frame'] != plan.end_frame
                or control['epoch'] != (plan.end_frame - 1) // EPOCH or control['reason'] != 'true_eos'):
                raise CorruptLedger('Terminal control identity mismatch')
            if control['acknowledged']:
                if not control['response'] or digest(json.loads(control['response'])) != control['response_hash']:
                    raise CorruptLedger('Terminal response hash mismatch')
                if row['next_frame'] != plan.end_frame:
                    raise CorruptLedger('Premature terminal acknowledgement')
            elif control['response'] is not None or control['response_hash'] is not None:
                raise CorruptLedger('Unacknowledged control has a response')
        if row['status'] == 'completed' and (not control or not control['acknowledged']):
            raise CorruptLedger('Completion without acknowledgement')
        return Snapshot(run_id, row['revision'], plan, policy, row['next_frame'],
                        json.loads(row['tail']), json.loads(row['checkpoint']), row['prefix_hash'],
                        row['status'], control), row

    def snapshot(self, run_id):
        with self._connection() as db:
            db.execute('BEGIN')
            result, _ = self._snapshot(db, run_id)
            self._verify_evidence(db, run_id)
            return result

    def _intent(self, db, run_id, revision, plan, policy):
        if plan.final:
            db.execute('INSERT INTO fragment_controls(run_id,control_id,revision,final_frame,epoch,reason) '
                       'VALUES(?,?,?,?,?,?)', (run_id, control_id(run_id, plan, policy), revision,
                                               plan.end_frame, (plan.end_frame - 1) // EPOCH, 'true_eos'))
            self.fault('plan.after_intent')

    def create(self, run_id, plan, policy=RuntimePolicy(), operation_id='initial'):
        self._id(run_id)
        self._id(operation_id)
        self._admit(plan)
        self._validate_policy(plan, policy)
        with self._transaction('create') as db:
            self._create_in_transaction(db, run_id, plan, policy, operation_id)
        return self.snapshot(run_id)

    def _create_in_transaction(self, db, run_id, plan, policy, operation_id='initial'):
        """Embed admission in a caller-owned transaction; never commits it."""
        self._id(run_id)
        self._id(operation_id)
        self._admit(plan)
        self._validate_policy(plan, policy)
        existing = db.execute('SELECT 1 FROM fragment_runs WHERE run_id=?', (run_id,)).fetchone()
        if existing:
            snapshot, _ = self._snapshot(db, run_id)
            saved = db.execute('SELECT * FROM fragment_plans WHERE run_id=? AND operation_id=?',
                               (run_id, operation_id)).fetchone()
            if saved is None or saved['plan_hash'] != plan.sha256 or snapshot.policy != policy:
                raise StaleWork('Run/import identity already has a different payload')
            return snapshot
        self.fault('create.before_plan')
        prefix = prefix_hash(plan, 0)
        state_hash = self._state_hash(1, plan.sha256, policy.sha256, 0, prefix, '[]', '{}', 'running')
        db.execute('INSERT INTO fragment_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (run_id, 1, plan.sha256, canonical(asdict(policy)), policy.sha256,
                    0, prefix, '[]', '{}', state_hash, 'running', None))
        db.execute('INSERT INTO fragment_plans VALUES(?,?,?,?,?,?)',
                   (run_id, 1, operation_id, plan.sha256, None, plan.to_json()))
        self.fault('create.after_plan')
        self._intent(db, run_id, 1, plan, policy)

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError('Bounded nonempty identifier required')

    @staticmethod
    def _fence(snapshot, revision, cursor=None):
        if snapshot.status in ('cancelled', 'completed') or snapshot.revision != revision or (
            cursor is not None and snapshot.next_frame != cursor
        ):
            raise StaleWork('Run status, revision or cursor changed')

    def append(self, run_id, plan, *, expected_revision, operation_id):
        self._id(operation_id)
        self._admit(plan)
        with self._transaction('append') as db:
            snapshot, row = self._snapshot(db, run_id)
            saved = db.execute('SELECT * FROM fragment_plans WHERE run_id=? AND operation_id=?',
                               (run_id, operation_id)).fetchone()
            if saved:
                if saved['plan_hash'] != plan.sha256:
                    raise StaleWork('Import operation ID reused with a different plan')
                return snapshot
            self._fence(snapshot, expected_revision)
            self._extend_plan(snapshot, plan)
            if plan.sha256 == snapshot.plan.sha256:
                raise ValueError('Append must change plan metadata, samples, aliases or terminal state')
            revision = expected_revision + 1
            db.execute('INSERT INTO fragment_plans VALUES(?,?,?,?,?,?)',
                       (run_id, revision, operation_id, plan.sha256, snapshot.plan.sha256, plan.to_json()))
            self.fault('append.after_plan')
            self._intent(db, run_id, revision, plan, snapshot.policy)
            self._save_state(db, row, revision=revision, plan_hash=plan.sha256,
                             status='running', wait_revision=None)
            self.fault('append.after_state')
        return self.snapshot(run_id)

    def register_wait(self, run_id, revision, cursor):
        with self._transaction('wait') as db:
            snapshot, row = self._snapshot(db, run_id)
            self._fence(snapshot, revision, cursor)
            self._save_state(db, row, status='waiting', wait_revision=revision)
            self.fault('wait.after_state')
        return (revision, cursor)

    def changed(self, run_id, token):
        snapshot = self.snapshot(run_id)
        return (snapshot.revision, snapshot.next_frame) != token or snapshot.status in ('cancelled', 'completed')

    def cancel(self, run_id):
        with self._transaction('cancel') as db:
            snapshot, row = self._snapshot(db, run_id)
            if snapshot.status == 'completed':
                raise StaleWork('Completed run cannot be cancelled')
            self._save_state(db, row, status='cancelled', wait_revision=None)
            self.fault('cancel.after_state')

    @staticmethod
    def _response(snapshot, response):
        control = snapshot.control
        if not control or control['acknowledged'] or not isinstance(response, dict):
            raise ValueError('No pending finalization intent/response')
        expected = {'control_id', 'native_key', 'consumed_until', 'stable_until', 'activity_until', 'finished'}
        if set(response) != expected or response['control_id'] != control['control_id'] or response['finished'] is not True:
            raise ValueError('Invalid finalization response')
        from .fragment_runtime_contracts import NativeStatus, native_key
        if response['native_key'] != list(native_key(snapshot.run_id, control['epoch'], snapshot.policy)):
            raise ValueError('Finalization native identity mismatch')
        NativeStatus(response['consumed_until'], response['stable_until'], response['activity_until'], True).validate(
            snapshot.plan.end_frame, max(0, control['epoch'] * EPOCH - 339200))
        return bounded_json(response)

    def _ack(self, db, snapshot, response):
        text = self._response(snapshot, response)
        db.execute('UPDATE fragment_controls SET response=?,response_hash=?,acknowledged=1 WHERE run_id=?',
                   (text, digest(response), snapshot.run_id))
        self.fault('ack.after_control')

    def commit(self, snapshot, window, payload, tail, checkpoint, response=None):
        """Call only after bounded output validation; defensively bind the envelope."""
        if window != next_window(snapshot.plan, snapshot.next_frame, snapshot.policy):
            raise ValueError('Core does not match the frozen logical scheduler')
        wid = window_id(snapshot.run_id, window, snapshot.policy)
        if payload.get('window_id') != wid or payload.get('window') != asdict(window):
            raise ValueError('Output envelope differs from committed core')
        if payload.get('revision') != snapshot.revision or payload.get('plan_hash') != snapshot.plan.sha256:
            raise ValueError('Output envelope plan mismatch')
        if payload.get('tail') != tail or payload.get('checkpoint') != checkpoint:
            raise ValueError('Tail/checkpoint are not the validated envelope state')
        validate_commit(snapshot, window, payload, tail, checkpoint)
        payload_text = bounded_json(payload)
        tail_text, checkpoint_text = bounded_json(tail, 131072), bounded_json(checkpoint, 65536)
        with self._transaction('core') as db:
            actual, row = self._snapshot(db, snapshot.run_id)
            self._fence(actual, snapshot.revision, snapshot.next_frame)
            if actual != snapshot:
                raise StaleWork('Offered snapshot differs from the durable accepted state')
            if 'evidence_ref' in payload:
                self._evidence().verify_core(payload['evidence_ref'], payload, snapshot.run_id)
            db.execute('INSERT INTO fragment_core_commits VALUES(?,?,?,?,?,?,?,?)',
                       (snapshot.run_id, window.island, window.core_start, window.core_end,
                        wid, snapshot.revision, payload_text, digest(payload)))
            self.fault('core.after_output')
            final = actual.plan.final and window.core_end == actual.plan.end_frame
            if final:
                self._ack(db, actual, response)
            elif response is not None:
                raise ValueError('Only the terminal output may acknowledge EOS')
            self._save_state(db, row, next_frame=window.core_end, prefix_hash=prefix_hash(actual.plan, window.core_end),
                             tail=tail_text, checkpoint=checkpoint_text, status='completed' if final else 'running',
                             wait_revision=None)
            self._publish_core(db, snapshot, payload)
            self.fault('core.after_cursor')
        return wid

    def _publish_core(self, db, snapshot, payload):
        """Optional atomic projection; default internal ledger has none."""

    def acknowledge_empty(self, snapshot, response):
        with self._transaction('empty_ack') as db:
            actual, row = self._snapshot(db, snapshot.run_id)
            self._fence(actual, snapshot.revision, snapshot.next_frame)
            if actual != snapshot:
                raise StaleWork('Offered snapshot differs from the durable accepted state')
            if actual.next_frame != actual.plan.end_frame:
                raise ValueError('Zero-output acknowledgement requires fully owned audio')
            self._ack(db, actual, response)
            self._save_state(db, row, status='completed', wait_revision=None)
            self.fault('empty_ack.after_state')

    def commits(self, run_id):
        with self._connection() as db:
            result = []
            for row in db.execute('SELECT * FROM fragment_core_commits WHERE run_id=? ORDER BY core_start', (run_id,)):
                payload = json.loads(row['payload'])
                if digest(payload) != row['payload_hash']:
                    raise CorruptLedger('Committed envelope hash mismatch')
                if 'evidence_ref' in payload:
                    self._evidence().verify_core(payload['evidence_ref'], payload, run_id)
                result.append(payload)
            return result

    def _evidence(self):
        if self.evidence_store is None:
            raise CorruptLedger('Evidence-backed ledger requires its fixed evidence store')
        return self.evidence_store

    def _verify_evidence(self, db, run_id):
        for row in db.execute('SELECT payload,payload_hash FROM fragment_core_commits WHERE run_id=?', (run_id,)):
            payload = json.loads(row['payload'])
            if digest(payload) != row['payload_hash']:
                raise CorruptLedger('Committed envelope hash mismatch')
            if 'evidence_ref' in payload:
                self._evidence().verify_core(payload['evidence_ref'], payload, run_id)

    def cleanup_evidence_orphans(self, *, external_reference_provider=None):
        with self._transaction('evidence_cleanup') as db:
            refs = []
            for row in db.execute('SELECT run_id,payload,payload_hash FROM fragment_core_commits'):
                payload = json.loads(row['payload'])
                if digest(payload) != row['payload_hash']:
                    raise CorruptLedger('Committed envelope hash mismatch')
                if 'evidence_ref' in payload:
                    self._evidence().verify_core(payload['evidence_ref'], payload, row['run_id'])
                    refs.append(payload['evidence_ref'])
            return self._evidence().cleanup(refs, external_reference_provider=external_reference_provider)
