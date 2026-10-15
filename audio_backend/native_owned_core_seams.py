"""Opt-in adjacent-core alternatives and a compact primary outbox.

One genuine current response, no native push/ASR, no historical witness journal.
Only exact canonical proof is durable; immutable source PCM is rechecked at drain.
A fresh Capture run fences reset counters/epoch IDs after process restart.
The reset-replay policy is separate; historical v3 semantics remain same-epoch.
"""
from __future__ import annotations
from dataclasses import asdict
import hashlib
import json
import re
import sqlite3
import time
import uuid

from .native_owned_cells import (Cell, SourceSpan, SourceSupport, Turn, Unavailable,
    canonical, sha, digest, integer, text, exact_frame, float32_digest,
    validate_mapping, intersect_mapping, selection, RATE, CORE_FRAMES, PROOF_BYTES,
    MAX_TURNS, POLICY_SHA256 as V1_POLICY)

SCHEMA = 'native-owned-core-seam-v3'
RESET_SCHEMA = 'native-owned-reset-replay-core-seam-v1'
COMPACT_BYTES = 2048
MAX_COMPACTS = 2056
PENDING_BYTES = 128 * 1024
MAX_PENDING = 4
SIDE_FRAMES = 10 * RATE
POLICY = {'schema': SCHEMA, 'rate': RATE, 'support_frames': 20 * RATE,
          'witness': 'one-current-response', 'domain': 'capture-run-reset-epoch',
          'primary': 'unchanged', 'reset_straddling': False,
          'selection': 'positive-half-open-distinct-epoch-local-label-overlap'}
POLICY_SHA256 = sha(POLICY)
RESET_POLICY = {**POLICY, 'schema': RESET_SCHEMA, 'reset_straddling': True,
    'domain': 'same-capture-run-consecutive-observed-reset-epochs',
    'support': 'exact-ten-seconds-per-adjacent-commit',
    'witness': 'one-current-new-epoch-response-with-observed-replay-pcm'}
RESET_POLICY_SHA256 = sha(RESET_POLICY)
# Fresh isolated policy identity; existing optional stores are never adopted.
def store_policy():
    from .native_owned_seam_cells import POLICY_SHA256 as V2_POLICY
    return sha({'schema': 'native-owned-cell-store-policy-reset-replay-v1',
                'accepted_cell_policies': sorted([V1_POLICY, V2_POLICY, POLICY_SHA256, RESET_POLICY_SHA256])})


def is_core(doc):return type(doc) is dict and doc.get('schema') in (SCHEMA, RESET_SCHEMA)


def policy(schema):
    if schema == SCHEMA:return POLICY_SHA256
    if schema == RESET_SCHEMA:return RESET_POLICY_SHA256
    raise Unavailable('core_seam_policy')


def exact(value, names):
    if type(value) is not dict or set(value) != set(names.split()):
        raise Unavailable('core_seam_shape')
    return value


def scope_key(scope):
    return sha({k: scope[k] for k in ('session_id', 'kind', 'key', 'origin')})


def slot(commit):
    p = commit['part']
    return sha({'policy': POLICY_SHA256, 'sid': p['session_id'], 'cid': p['chunk_id'],
                'start': p['source_start']})


def anchor(commit):
    p = commit['part']
    return {'chunk_id': p['chunk_id'], 'start': p['source_start'],
            'end': p['source_end'], 'sha256': commit['evidence_sha256']}


def validate_commit(commit):
    exact(commit, 'scope part native_sha256 cut_sha256 evidence_sha256 runtime_sha256')
    scope = exact(commit['scope'], 'session_id kind key origin run reset epoch index')
    text(scope['session_id']); text(scope['key'])
    if scope['kind'] not in ('upload', 'stream'):raise Unavailable('core_seam_scope')
    for key in ('run', 'epoch'):
        if type(scope[key]) is not str or re.fullmatch('[a-f0-9]{32}', scope[key]) is None:
            raise Unavailable('core_seam_domain')
    integer(scope['reset'], (1 << 31) - 1, 1); integer(scope['index'])
    if type(scope['origin']) is not str or len(scope['origin']) > 32:raise Unavailable('core_seam_origin')
    try:origin = float.fromhex(scope['origin'])
    except ValueError as exc:raise Unavailable('core_seam_origin') from exc
    if origin < 0 or origin.hex() != scope['origin']:raise Unavailable('core_seam_origin')
    exact(commit['part'], 'session_id chunk_id receipt_sha256 source_sha256 source_frames source_start source_end logical_start logical_end')
    p = SourceSpan(**commit['part'])
    if p.session_id != scope['session_id'] or not 0 < p.logical_end - p.logical_start <= CORE_FRAMES:
        raise Unavailable('core_seam_commit_geometry')
    if not scope['index'] * 300 * RATE <= p.logical_start < p.logical_end <= (scope['index'] + 1) * 300 * RATE:
        raise Unavailable('core_seam_reset_unsupported')
    for key in ('native_sha256', 'cut_sha256', 'evidence_sha256', 'runtime_sha256'):digest(commit[key])
    canonical(commit, COMPACT_BYTES)
    return p


def geometry(commits, schema=SCHEMA):
    """v3 remains strict by default; reset proofs must name the new contract."""
    left, right = [validate_commit(c) for c in commits]
    left_scope, right_scope = commits[0]['scope'], commits[1]['scope']
    if schema == RESET_SCHEMA:
        if any(left_scope[k] != right_scope[k] for k in ('session_id', 'kind', 'key', 'origin', 'run')):
            raise Unavailable('core_seam_capture_domain_changed')
        if (right_scope['index'] != left_scope['index'] + 1
                or right_scope['reset'] != left_scope['reset'] + 1
                or right_scope['epoch'] == left_scope['epoch']):
            raise Unavailable('core_reset_transition')
        boundary = right_scope['index'] * 300 * RATE
        if left.logical_end != boundary or right.logical_start != boundary:
            raise Unavailable('core_reset_boundary')
        if left.logical_end - left.logical_start < SIDE_FRAMES or right.logical_end - right.logical_start < SIDE_FRAMES:
            raise Unavailable('core_reset_short_side')
    elif schema != SCHEMA:
        raise Unavailable('core_seam_policy')
    elif left_scope != right_scope:
        if left_scope['run'] == right_scope['run'] and any(left_scope[k] != right_scope[k] for k in ('reset','epoch','index')):
            raise Unavailable('core_seam_reset_unsupported')
        raise Unavailable('core_seam_capture_domain_changed')
    if commits[0]['runtime_sha256'] != commits[1]['runtime_sha256']:
        raise Unavailable('core_seam_runtime_changed')
    if left.logical_end != right.logical_start:raise Unavailable('core_seam_not_adjacent')
    return {'seam_frame': right.logical_start,
            'start_frame': max(left.logical_start, right.logical_start - SIDE_FRAMES),
            'end_frame': min(right.logical_end, right.logical_start + SIDE_FRAMES)}


def mapping(commits, shape):
    """Canonical coalescing prevents two spans aliasing the same upload."""
    parts = []
    for c in commits:
        p = SourceSpan(**c['part'])
        a, b = max(shape['start_frame'], p.logical_start), min(shape['end_frame'], p.logical_end)
        p = SourceSpan(p.session_id, p.chunk_id, p.receipt_sha256, p.source_sha256,
                       p.source_frames, p.source_start + a - p.logical_start,
                       p.source_start + b - p.logical_start, a, b)
        if parts and parts[-1].chunk_id == p.chunk_id:
            old = parts.pop()
            if (old.source_end != p.source_start or old.logical_end != p.logical_start
                    or (old.session_id, old.receipt_sha256, old.source_sha256, old.source_frames) !=
                       (p.session_id, p.receipt_sha256, p.source_sha256, p.source_frames)):
                raise Unavailable('core_seam_source_alias')
            p = SourceSpan(old.session_id, old.chunk_id, old.receipt_sha256, old.source_sha256,
                           old.source_frames, old.source_start, p.source_end, old.logical_start, p.logical_end)
        parts.append(p)
    return validate_mapping(tuple(parts), shape['start_frame'], shape['end_frame'])


def ownership(doc):
    return sha({'schema': doc['schema'], 'commits': [slot(c) for c in doc['commits']],
                'geometry': doc['geometry']})


def coverage(doc):
    shape = doc['geometry']; commits = doc['commits']; scope = commits[0]['scope']
    result = {'kind': 'same_epoch_core_seam_alternative', 'scope_sha256': sha(scope),
        'frame_range': [shape['start_frame'], shape['end_frame']],
        'source_support_core_semantics': 'two_committed_core_envelope',
        'coverage_envelope_frames': [commits[0]['part']['logical_start'], commits[1]['part']['logical_end']],
        'primary_commit_refs': [anchor(c) for c in commits],
        'adds_primary_audio': False, 'automatic_dispatch': False}
    if doc['schema'] == RESET_SCHEMA:
        result.update(kind='reset_replay_core_seam_alternative',
            scope_sha256=sha({'primary_scopes': [c['scope'] for c in commits], 'query_scope': commits[1]['scope']}),
            primary_scope_sha256=[sha(c['scope']) for c in commits],
            query_scope_sha256=sha(commits[1]['scope']))
    return result


def validate_query(query, right, shape, evidence):
    """Reuse strict v2 current-response frontier checks without relaxing v2."""
    from .native_owned_seam_cells import validate_witness
    exact(query, 'query_start_frame query_end_frame query_pcm16le_sha256 support_pcm16le_sha256 native turns')
    a, b = shape['start_frame'], shape['end_frame']
    qa, qb = query['query_start_frame'], query['query_end_frame']
    integer(qa); integer(qb)
    if not qa <= a < b <= qb or not 0 < qb - qa <= CORE_FRAMES:
        raise Unavailable('core_seam_query_coverage')
    digest(query['query_pcm16le_sha256']); digest(query['support_pcm16le_sha256'])
    p = right['part']; scope = right['scope']
    legacy_scope = {'session_id': scope['session_id'], 'stream_id': scope['key'],
        'origin_seconds_hex': scope['origin'], 'epoch_id': scope['epoch'],
        'epoch_index': scope['index'], 'reset_generation': scope['reset']}
    legacy_commit = {'chunk_id': p['chunk_id'], 'source_origin_frame': p['logical_start'] - p['source_start'],
        'local_start_frame': p['source_start'], 'local_end_frame': p['source_end'],
        'logical_start_frame': p['logical_start'], 'logical_end_frame': p['logical_end'],
        'evidence': evidence, 'evidence_sha256': right['evidence_sha256']}
    from .native_owned_seam_cells import validate_commit as validate_legacy_commit
    validate_legacy_commit(legacy_commit, legacy_scope)
    legacy_witness = {'commit_index': 0, 'query_start_frame': qa, 'query_end_frame': qb,
        'analyzer_start_frame': qa, 'analyzer_end_frame': qb,
        'analyzer_pcm16le_sha256': query['query_pcm16le_sha256'],
        'piece_pcm16le_sha256': query['support_pcm16le_sha256'],
        'native': query['native'], 'turns': query['turns']}
    validate_witness(legacy_witness, legacy_commit, legacy_scope, 0, a, b)
    if (sha(evidence['native']) != right['native_sha256'] or sha(evidence['core_cut']) != right['cut_sha256']
            or sha(query['native']['provenance']) != right['runtime_sha256']):
        raise Unavailable('core_seam_evidence_mismatch')


def validate_document(proof):
    if type(proof) is not bytes or not 1 <= len(proof) <= PROOF_BYTES:raise Unavailable('proof_capacity')
    doc = json.loads(proof)
    exact(doc, 'schema policy commits geometry support dependencies query right_evidence selection publication_anchor selected coverage')
    if not is_core(doc) or doc['policy'] != policy(doc['schema']) or doc['selected'] is not True:
        raise Unavailable('core_seam_policy')
    commits = doc['commits']
    if type(commits) is not list or len(commits) != 2:raise Unavailable('core_seam_two_commits')
    shape = geometry(commits, doc['schema'])
    if doc['geometry'] != shape:raise Unavailable('core_seam_nondeterministic_geometry')
    parts = mapping(commits, shape)
    if doc['dependencies'] != [asdict(p) for p in parts]:raise Unavailable('core_seam_mapping')
    validate_query(doc['query'], commits[1], shape, doc['right_evidence'])
    from .native_owned_seam_cells import selected_pair
    if doc['selection'] != selected_pair([doc['query']], shape['start_frame'], shape['end_frame']):
        raise Unavailable('core_seam_selection')
    support = SourceSupport.from_dict(doc['support']); contents = [p.content() for p in parts]
    expected_core = sha({'commits': commits, 'geometry': shape, 'query': doc['query']})
    if (support.source_generation != sha({'schema': 'immutable-source-cell-generation-v1', 'sources': contents})
            or support.plan_sha256 != sha({'schema': 'bounded-cell-mapping-v1', 'parts': contents})
            or support.native_runtime_sha256 != commits[1]['runtime_sha256']
            or support.core_sha256 != expected_core
            or (support.source_frames, support.core_start_frame, support.core_end_frame,
                support.start_frame, support.end_frame) !=
               (commits[1]['part']['logical_end'], commits[0]['part']['logical_start'],
                commits[1]['part']['logical_end'], shape['start_frame'], shape['end_frame'])
            or support.pcm16le_sha256 != doc['query']['support_pcm16le_sha256']):
        raise Unavailable('core_seam_support')
    if doc['publication_anchor'] != anchor(commits[1]) or doc['coverage'] != coverage(doc):
        raise Unavailable('core_seam_publication')
    if canonical(doc) != proof:raise Unavailable('noncanonical_proof')
    return doc, support, parts


def make_cell(commits, query, evidence, pcm, schema=SCHEMA):
    from .native_owned_seam_cells import selected_pair
    shape = geometry(commits, schema); parts = mapping(commits, shape)
    chosen = selected_pair([query], shape['start_frame'], shape['end_frame'])
    contents = [p.content() for p in parts]
    support = SourceSupport(sha({'schema': 'immutable-source-cell-generation-v1', 'sources': contents}),
        sha({'schema': 'bounded-cell-mapping-v1', 'parts': contents}), commits[1]['runtime_sha256'],
        sha({'commits': commits, 'geometry': shape, 'query': query}),
        commits[1]['part']['logical_end'], commits[0]['part']['logical_start'], commits[1]['part']['logical_end'],
        shape['start_frame'], shape['end_frame'], hashlib.sha256(pcm).hexdigest(), float32_digest(pcm))
    doc = {'schema': schema, 'policy': policy(schema), 'commits': commits, 'geometry': shape,
        'support': support.to_dict(), 'dependencies': [asdict(p) for p in parts], 'query': query,
        'right_evidence': evidence, 'selection': chosen, 'publication_anchor': anchor(commits[1]), 'selected': True}
    doc['coverage'] = coverage(doc)
    p = anchor(commits[1])
    return Cell(support, parts, canonical(doc), pcm, commits[1]['scope']['session_id'],
                p['chunk_id'], p['start'], p['end'], p['sha256'])


def init_schema(db):
    """Additive and initially empty: no metadata backfill, no old witness adoption."""
    db.execute('''CREATE TABLE IF NOT EXISTS native_core_commits (
        slot TEXT PRIMARY KEY, sid TEXT NOT NULL, cid TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
        scope_key TEXT NOT NULL, logical_end INTEGER NOT NULL,
        body BLOB NOT NULL CHECK(typeof(body)='blob' AND length(body) BETWEEN 1 AND 2048),
        pending BLOB CHECK(pending IS NULL OR (typeof(pending)='blob' AND length(pending) BETWEEN 1 AND 131072)),
        state TEXT NOT NULL CHECK(state IN ('none','pending','published','gap','cancelled')),
        reason TEXT NOT NULL CHECK(length(reason)<=80))''')
    db.execute('CREATE INDEX IF NOT EXISTS native_core_adjacent ON native_core_commits(sid,scope_key,logical_end)')
    db.execute("CREATE INDEX IF NOT EXISTS native_core_pending ON native_core_commits(state,slot)")
    db.execute('''CREATE TRIGGER IF NOT EXISTS native_core_cancel AFTER UPDATE OF status ON chunks
        WHEN NEW.status='cancelled' BEGIN
          UPDATE native_core_commits SET state='cancelled',pending=NULL,reason='primary_cancelled'
          WHERE sid=NEW.session_id AND state='pending'; END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS native_core_delete AFTER UPDATE OF deleted ON sessions
        WHEN NEW.deleted=1 BEGIN DELETE FROM native_core_commits WHERE sid=NEW.id; END''')


def read_commit(db, slot_id, budget):
    row = db.execute('SELECT length(body) AS n FROM native_core_commits WHERE slot=?', (slot_id,)).fetchone()
    if row is None:return None
    if not 1 <= row[0] <= COMPACT_BYTES:raise Unavailable('core_receipt_capacity')
    # Length is checked again in the same statement which projects bytes.
    row = db.execute('SELECT body FROM native_core_commits WHERE slot=? AND length(body) BETWEEN 1 AND ?',
                     (slot_id, COMPACT_BYTES)).fetchone()
    if row is None:return None
    budget.projection(len(row[0])); commit = json.loads(row[0]); validate_commit(commit)
    if canonical(commit, COMPACT_BYTES) != row[0] or slot(commit) != slot_id:raise Unavailable('core_receipt_corrupt')
    return commit


def transaction(db, chunk, offset, end, result):
    """Called inside the successful primary transaction, before its UPDATE.

    Optional capacity/errors roll back only this savepoint; primary text survives.
    Conflicting slot evidence never replaces the first immutable commitment.
    """
    packet = result.get('_native_core_seam')
    if packet is None:return None
    db.execute('SAVEPOINT native_core_optional')
    try:
        commit = packet['commit']; p = validate_commit(commit); raw = canonical(commit, COMPACT_BYTES)
        evidence = {'chunk_id': chunk['id'], 'start': exact_frame(offset), 'end': exact_frame(end),
            'native': result['metadata']['native_diarization'], 'core_cut': result['metadata']['core_cut']}
        if (p.chunk_id != chunk['id'] or p.session_id != chunk['session_id']
                or (p.source_start, p.source_end) != (exact_frame(offset), exact_frame(end))
                or sha(evidence) != commit['evidence_sha256']
                or sha(evidence['native']) != commit['native_sha256'] or sha(evidence['core_cut']) != commit['cut_sha256']):
            raise Unavailable('core_commit_transaction_binding')
        old = db.execute('SELECT body=? AS same_body,pending IS NULL OR pending=? AS same_pending FROM native_core_commits WHERE slot=?',
                         (raw, packet['proof'], slot(commit))).fetchone()
        if old is not None:
            if not old[0] or packet['proof'] is not None and not old[1]:
                raise Unavailable('core_slot_evidence_conflict')
            return 'existing_core_commit'
        if db.execute('SELECT COUNT(*) FROM native_core_commits').fetchone()[0] >= MAX_COMPACTS:
            raise Unavailable('core_receipt_row_capacity')
        proof = packet['proof']; reason = packet['reason']
        state = 'gap' if reason == 'core_reset_short_side' else 'none'
        if proof is not None:
            if type(proof) is not bytes or not 1 <= len(proof) <= PENDING_BYTES:
                reason = 'core_pending_payload_capacity'; proof = None; state = 'gap'
            else:
                doc, _, _ = validate_document(proof)
                if doc['commits'][1] != commit:raise Unavailable('core_pending_commit_binding')
                left = doc['commits'][0]
                observed = db.execute('SELECT body FROM native_core_commits WHERE slot=? AND length(body)<=?',
                                      (slot(left), COMPACT_BYTES)).fetchone()
                if observed is None or observed[0] != canonical(left, COMPACT_BYTES):raise Unavailable('core_left_commit_missing')
                if db.execute("SELECT COUNT(*) FROM native_core_commits WHERE pending IS NOT NULL").fetchone()[0] >= MAX_PENDING:
                    reason = 'core_pending_row_capacity'; proof = None; state = 'gap'
                else:state = 'pending'; reason = 'core_pending'
        db.execute('INSERT INTO native_core_commits VALUES(?,?,?,?,?,?,?,?,?)',
            (slot(commit), chunk['session_id'], chunk['id'], scope_key(commit['scope']), p.logical_end,
             raw, proof, state, reason[:80]))
        return reason
    except (Unavailable, ValueError, TypeError, KeyError) as exc:
        db.execute('ROLLBACK TO native_core_optional')
        return str(exc) if isinstance(exc, Unavailable) else 'core_optional_transaction_unavailable'
    finally:db.execute('RELEASE native_core_optional')


def current(authority, doc, budget):
    """Independent of mutable chunk.metadata, and fail-closed after cancellation."""
    doc, _, parts = validate_document(canonical(doc)); owner = authority.owner
    generation = owner._epoch
    for commit in doc['commits']:
        p = validate_commit(commit)
        def observed_commit(db):
            observed = read_commit(db, slot(commit), budget)
            state = db.execute('SELECT state FROM native_core_commits WHERE slot=?', (slot(commit),)).fetchone()
            return observed if state is not None and state[0] != 'cancelled' else None
        stored = authority._read(budget, 'core_commit', observed_commit)
        if stored != commit:return False
        receipt = owner.receipt(p.chunk_id)
        if (receipt is None or receipt.receipt_id != p.receipt_sha256 or receipt.digest != p.source_sha256
                or receipt.frames != p.source_frames or not authority.receipt_valid(receipt, budget)):
            return False
        row = authority.row(p.chunk_id, budget); scope = commit['scope']
        if (row is None or row['status'] in ('cancelled', 'failed', 'staging')
                or exact_frame(row['next_offset']) < p.source_end
                or exact_frame(row['timeline_start'], origin=float.fromhex(scope['origin'])) != p.logical_start - p.source_start
                or (row['stream_id'] != scope['key'] if scope['kind'] == 'stream' else row['stream_id'] is not None)):
            return False
    # Mapping is independently reconstructed, catching changed stream/source aliases.
    right = authority.row(doc['commits'][1]['part']['chunk_id'], budget)
    observed = authority.mapping(right, float.fromhex(doc['commits'][1]['scope']['origin']),
                                 doc['geometry']['start_frame'], doc['geometry']['end_frame'], budget)
    return observed == parts and generation is owner._epoch


class CoreSeams:
    """One bounded maintenance cursor/flight, sharing the existing owner slot."""
    def __init__(self, capture):
        self.capture = capture; self.run = uuid.uuid4().hex
        self.generation = capture.owner._epoch; self.flight = None; self.work_pending = True
        self.next_attempt = 0.; self.revocation_done = False
        self.last_reason = 'not_observed'

    def prepare(self, chunk, native, offset, end, result, coordinator, stop):
        c = self.capture
        if native is None or c.scope is None or not c.owner.health()['ready']:return
        if c.owner._epoch is not self.generation:
            self.last_reason = 'core_owner_revoked'; return
        from .native_owned_cell_store import Budget
        budget = Budget()
        try:
            with budget.phase():
                a, b = c.origin_frame + exact_frame(offset), c.origin_frame + exact_frame(end)
                parts = c.authority.mapping(chunk, c.scope_start, a, b, budget)
                if len(parts) != 1 or parts[0].chunk_id != chunk['id']:raise Unavailable('core_single_source_required')
                ordinary_turns, observed = c.witness.prepared(native, c.scope_start, c.origin_frame)
                evidence = {'chunk_id': chunk['id'], 'start': exact_frame(offset), 'end': exact_frame(end),
                            'native': native['state'], 'core_cut': result['metadata']['core_cut']}
                scope = {'session_id': chunk['session_id'], 'kind': c.scope[0], 'key': c.scope[2],
                    'origin': float(c.scope_start).hex(), 'run': self.run,
                    'reset': c.observed_reset_generation, 'epoch': observed['epoch_id'], 'index': observed['epoch_index']}
                commit = {'scope': scope, 'part': asdict(parts[0]), 'native_sha256': sha(native['state']),
                    'cut_sha256': sha(evidence['core_cut']), 'evidence_sha256': sha(evidence),
                    'runtime_sha256': sha(observed['provenance'])}
                validate_commit(commit)
                if (b > observed['replay_start'] + observed['state']['stable']['frame']
                        or b > observed['replay_start'] + observed['state']['processed']['frame']):
                    raise Unavailable('core_unprocessed_commit')
                # This receipt is staged even if no overlap/seam is selected.
                packet = {'commit': commit, 'proof': None, 'reason': 'core_first_or_unavailable_left'}
                result['_native_core_seam'] = packet
                def previous(db):
                    rows = db.execute('SELECT slot FROM native_core_commits WHERE sid=? AND scope_key=? AND logical_end=? LIMIT 2',
                        (chunk['session_id'], scope_key(scope), a)).fetchall()
                    if len(rows) > 1:raise Unavailable('core_left_ambiguous')
                    return read_commit(db, rows[0][0], budget) if rows else None
                left = c.authority._read(budget, 'core_previous', previous)
                if left is None:return
                schema = RESET_SCHEMA if left['scope']['index'] != scope['index'] else SCHEMA
                shape = geometry([left, commit], schema); lo, hi = shape['start_frame'], shape['end_frame']
                if schema == SCHEMA and not observed['epoch_index'] * 300 * RATE <= lo < hi <= (observed['epoch_index'] + 1) * 300 * RATE:
                    raise Unavailable('core_seam_reset_unsupported')
                if schema == RESET_SCHEMA and observed['replay_start'] > lo:
                    raise Unavailable('core_reset_replay_missing')
                # Full future half was pushed/processed by normal scheduling already.
                if hi > observed['replay_start'] + observed['state']['processed']['frame']:
                    raise Unavailable('core_seam_future_unprocessed')
                pcm = c.witness.read(lo, hi)
                expected = mapping([left, commit], shape)
                if pcm != c.authority.original_pcm(expected, budget):raise Unavailable('core_seam_source_pcm_mismatch')
            qa = max(c.origin_frame + round(native['extract_start'] * RATE), observed['replay_start'])
            qb = c.origin_frame + exact_frame(native['extract_end'])
            turns, proof = ordinary_turns, observed
            if not qa <= lo < hi <= qb:
                # One bounded TURNS request, sequenced AFTER v1/v2 preparation.
                # Never push/finish/ASR, and never request fictional future audio.
                if stop():raise Unavailable('core_seam_cancelled')
                token = c.observed_reset_generation; actions = c.witness.actions
                replay = observed['replay_start']; c.active = True
                try:response = coordinator.process.request('turns', stop, start=(lo-replay)/RATE, end=(hi-replay)/RATE)
                finally:c.active = False
                if (token != c.observed_reset_generation or c.owner._epoch is not self.generation
                        or c.witness.actions != actions + 1 or c.witness.state != observed['state']):
                    raise Unavailable('core_seam_query_not_observed')
                prepared_turns = []
                for t in response['turns']:
                    x, y = max(lo/RATE, replay/RATE+t['start']), min(hi/RATE, replay/RATE+t['end'])
                    if x < y:prepared_turns.append(dict(t, start=x-lo/RATE, end=y-lo/RATE))
                query_native = dict(native, extract_start=(lo-c.origin_frame)/RATE,
                    extract_end=(hi-c.origin_frame)/RATE, turns=prepared_turns)
                turns, proof = c.witness.prepared(query_native, c.scope_start, c.origin_frame)
                qa, qb = lo, hi
            with budget.phase():
                query_pcm = c.witness.read(qa, qb)
                query = {'query_start_frame': qa, 'query_end_frame': qb,
                    'query_pcm16le_sha256': hashlib.sha256(query_pcm).hexdigest(),
                    'support_pcm16le_sha256': hashlib.sha256(pcm).hexdigest(), 'native': proof,
                    'turns': [{'label': t.label, 'original': [t.start, t.end]} for t in turns]}
                cell = make_cell([left, commit], query, evidence, pcm, schema)
                packet['proof'] = cell.proof; packet['reason'] = 'core_pending'; self.work_pending = True
                self.last_reason = 'core_prepared'
        except Exception as exc:
            from .native_owned_cell_store import _diagnostic_failure
            _diagnostic_failure('core_seam_prepare', exc)
            reason = str(exc) if isinstance(exc, Unavailable) else 'core_prepare_unavailable'
            if '_native_core_seam' in result:result['_native_core_seam']['reason'] = reason
            self.last_reason = reason

    def _status(self, slot_id, state, reason, expected_hash=None):
        from .native_owned_cell_store import Budget
        with Budget().phase() as budget:
            with self.capture.authority.store.db() as db:
                db.execute('PRAGMA busy_timeout=5')
                if expected_hash is None:
                    changed = db.execute("UPDATE native_core_commits SET state=?,reason=?,pending=NULL WHERE slot=? AND state='pending'",
                               (state, reason[:80], slot_id)).rowcount
                else:
                    row = db.execute("SELECT pending FROM native_core_commits WHERE slot=? AND state='pending' AND length(pending) BETWEEN 1 AND ?",
                                     (slot_id, PENDING_BYTES)).fetchone()
                    if row is None:return False
                    budget.projection(len(row[0]))
                    if hashlib.sha256(row[0]).hexdigest() != expected_hash:return False
                    changed = db.execute("UPDATE native_core_commits SET state=?,reason=?,pending=NULL WHERE slot=? AND state='pending' AND pending=?",
                               (state, reason[:80], slot_id, row[0])).rowcount
                budget.check()
            return changed == 1

    def drain(self):
        """One nonwaiting post-core/outer-loop opportunity; no new owner or queue."""
        c = self.capture
        from .native_owned_cell_store import Budget, Operation
        try:
            if c.owner._epoch is not self.generation:
                if self.revocation_done or time.monotonic() < self.next_attempt:return
                self.next_attempt = time.monotonic() + 1.0
                with c.authority.store.db() as db:
                    db.execute('PRAGMA busy_timeout=5')
                    db.execute("UPDATE native_core_commits SET state='cancelled',pending=NULL,reason='core_owner_revoked' WHERE state='pending'")
                self.revocation_done = True
                self.flight = None; self.work_pending = False; self.last_reason = 'core_owner_revoked'; return
            if self.flight is not None:
                slot_id, op, expected_id, expected_hash = self.flight
                if not op.slot_released.is_set():return
                self.flight = None
                outcome = op.result
                rows = outcome.get('cells', []) if type(outcome) is dict else []
                if (outcome.get('status') == 'durable' and len(rows) == 1
                        and rows[0].get('cell_id') == expected_id
                        and rows[0].get('status') in ('retained', 'existing', 'not_retained_pcm')):
                    if self._status(slot_id, 'published', 'core_published', expected_hash):
                        self.last_reason = 'core_published'
                    return
                if rows and rows[0].get('cell_id') == expected_id and rows[0].get('status') == 'not_retained_metadata_capacity':
                    self._status(slot_id, 'gap', 'core_cell_capacity'); self.last_reason = 'core_cell_capacity'; return
                # Unknown/postcommit acknowledgement: replay the EXACT proof through
                # the existing idempotent owner writer on this/later drain.
            if time.monotonic() < self.next_attempt:return
            self.next_attempt = time.monotonic() + 1.0
            if not c.owner.health()['ready'] or getattr(c.owner, '_slot', None) is not None:return
            budget = Budget()
            with budget.phase():
                def pending(db):
                    row = db.execute("SELECT slot,length(pending) FROM native_core_commits WHERE state='pending' ORDER BY slot LIMIT 1").fetchone()
                    if row is None:return None
                    if not 1 <= row[1] <= PENDING_BYTES:raise Unavailable('core_pending_payload_capacity')
                    raw = db.execute("SELECT pending FROM native_core_commits WHERE slot=? AND state='pending' AND length(pending)<=?",
                                     (row[0], PENDING_BYTES)).fetchone()
                    if raw is None:return None
                    budget.projection(len(raw[0])); return row[0], raw[0]
                found = c.authority._read(budget, 'core_pending', pending)
                if found is None:
                    self.work_pending = False; return
                slot_id, raw = found; doc, support, parts = validate_document(raw)
                if not current(c.authority, doc, budget):
                    self._status(slot_id, 'cancelled', 'core_authority_revoked'); return
                pcm = c.authority.original_pcm(parts, budget)
                p = doc['publication_anchor']
                cell = Cell(support, parts, raw, pcm, doc['commits'][1]['scope']['session_id'],
                            p['chunk_id'], p['start'], p['end'], p['sha256'])
                if c.owner._epoch is not self.generation:return
                op = c.owner.submit('publish', (cell,))
                if isinstance(op, Operation):
                    self.flight = (slot_id, op, cell.cell_id, hashlib.sha256(raw).hexdigest())
                    self.last_reason = 'core_in_flight'
        except Exception as exc:
            from .native_owned_cell_store import _diagnostic_failure
            _diagnostic_failure('core_seam_drain', exc)
            self.last_reason = str(exc) if isinstance(exc, Unavailable) else 'core_drain_pending'
