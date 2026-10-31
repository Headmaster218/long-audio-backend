"""Opt-in adjacent-core alternatives and a compact primary outbox.

One genuine current response, no native push/ASR, no historical witness journal.
Only exact canonical proof is durable; immutable source PCM is rechecked at drain.
A fresh Capture run fences reset counters/epoch IDs after process restart.
Reset and restart replay use separate policies; historical contracts stay strict.
"""
from __future__ import annotations
from contextlib import nullcontext
from dataclasses import asdict
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid

from .native_owned_cells import (Cell, SourceSpan, SourceSupport, Turn, Unavailable,
    canonical, sha, digest, integer, text, exact_frame, float32_digest,
    validate_mapping, intersect_mapping, selection, RATE, CORE_FRAMES, PROOF_BYTES,
    MAX_TURNS, MAX_PARTS, POLICY_SHA256 as V1_POLICY)

SCHEMA = 'native-owned-core-seam-v3'
RESET_SCHEMA = 'native-owned-reset-replay-core-seam-v1'
RESTART_SCHEMA = 'native-owned-restart-replay-core-seam-v1'
BOUNDED_SCHEMA = 'native-owned-bounded-core-support-v1'
TARGET_SCHEMA = 'native-owned-original-target-v2'
INVENTORY_SCHEMA = 'native-owned-bounded-inventory-v1'
COMPACT_INVENTORY_SCHEMA = 'native-owned-bounded-inventory-v2'
SUPPORT_LOCAL_INVENTORY_SCHEMA = 'native-owned-bounded-inventory-v3'
SUPPORT_LOCAL_SCOPE_POLICY = 'selected-support-whole-parent-v1'
MAX_BOUND_COMMITS = 4
MAX_ORIGINS = 8
MAX_PLAN_PARENTS = 128
MAX_PLAN_UNITS = 512
MAX_PLAN_ATOMS = 128
SUPPORT_FRAMES = 320000
COMPACT_BYTES = 2048
MAX_COMPACTS = 2056
PENDING_BYTES = 128 * 1024
MAX_PENDING = 4
COMPACT_GC_BATCH = 8
COMPACT_GC_BODY_BYTES = 2 * COMPACT_GC_BATCH * COMPACT_BYTES
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
RESTART_POLICY = {**POLICY, 'schema': RESTART_SCHEMA, 'reset_straddling': True,
    'domain': 'distinct-capture-runs-same-or-consecutive-epoch',
    'support': 'up-to-ten-seconds-per-same-epoch-side-exact-ten-across-epochs',
    'witness': 'one-new-current-exact-support-response-with-observed-replay-pcm',
    'labels': 'current-capture-run-and-epoch-local-only'}
RESTART_POLICY_SHA256 = sha(RESTART_POLICY)
BOUNDED_POLICY = {**POLICY, 'schema': BOUNDED_SCHEMA,
    'support_frames': SUPPORT_FRAMES, 'commitments': MAX_BOUND_COMMITS,
    'target': 'full-original-positive-overlap-union-v2',
    'support': 'unchanged-or-least-whole-lexical-atom-fixed-point',
    'lexical': 'current-inventory-existing-ordinal-groups',
    'labels': 'current-capture-run-and-epoch-local-only'}
BOUNDED_POLICY_SHA256 = sha(BOUNDED_POLICY)
# Fresh isolated policy identity; existing optional stores are never adopted.
def store_policy(*, bounded=False):
    from .native_owned_seam_cells import POLICY_SHA256 as V2_POLICY
    if bounded:
        return sha({'schema': 'native-owned-cell-store-policy-bounded-core-v1',
            'accepted_cell_policies': sorted([V1_POLICY, V2_POLICY, POLICY_SHA256,
                RESET_POLICY_SHA256, RESTART_POLICY_SHA256, BOUNDED_POLICY_SHA256])})
    return sha({'schema': 'native-owned-cell-store-policy-restart-replay-v1',
                'accepted_cell_policies': sorted([V1_POLICY, V2_POLICY, POLICY_SHA256, RESET_POLICY_SHA256, RESTART_POLICY_SHA256])})


def is_core(doc):return type(doc) is dict and doc.get('schema') in (SCHEMA, RESET_SCHEMA, RESTART_SCHEMA, BOUNDED_SCHEMA)


def policy(schema):
    if schema == SCHEMA:return POLICY_SHA256
    if schema == RESET_SCHEMA:return RESET_POLICY_SHA256
    if schema == RESTART_SCHEMA:return RESTART_POLICY_SHA256
    if schema == BOUNDED_SCHEMA:return BOUNDED_POLICY_SHA256
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
    if schema == RESTART_SCHEMA:
        if any(left_scope[k] != right_scope[k] for k in ('session_id', 'kind', 'key', 'origin')):
            raise Unavailable('core_seam_capture_domain_changed')
        if left_scope['run'] == right_scope['run']:
            raise Unavailable('core_restart_run_required')
        delta = right_scope['index'] - left_scope['index']
        if (delta not in (0, 1)
                or (left_scope['epoch'] == right_scope['epoch']) != (delta == 0)):
            raise Unavailable('core_restart_epoch_transition')
        if delta == 1:
            boundary = right_scope['index'] * 300 * RATE
            if left.logical_end != boundary or right.logical_start != boundary:
                raise Unavailable('core_restart_boundary')
            if left.logical_end - left.logical_start < SIDE_FRAMES or right.logical_end - right.logical_start < SIDE_FRAMES:
                raise Unavailable('core_reset_short_side')
    elif schema == RESET_SCHEMA:
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
    if doc['schema'] == BOUNDED_SCHEMA:return doc['target']['key']
    return sha({'schema': doc['schema'], 'commits': [slot(c) for c in doc['commits']],
                'geometry': doc['geometry']})


def coverage(doc):
    shape = doc['geometry']; commits = doc['commits']; scope = commits[0]['scope']
    if doc['schema'] == BOUNDED_SCHEMA:
        return {'kind': 'bounded_same_epoch_core_alternative', 'scope_sha256': sha(scope),
            'frame_range': [shape['start_frame'], shape['end_frame']],
            'source_support_core_semantics': 'bounded_committed_core_envelope',
            'coverage_envelope_frames': [commits[0]['part']['logical_start'], commits[-1]['part']['logical_end']],
            'primary_commit_refs': [anchor(c) for c in commits],
            'target_key': doc['target']['key'], 'adds_primary_audio': False,
            'automatic_dispatch': False}
    result = {'kind': 'same_epoch_core_seam_alternative', 'scope_sha256': sha(scope),
        'frame_range': [shape['start_frame'], shape['end_frame']],
        'source_support_core_semantics': 'two_committed_core_envelope',
        'coverage_envelope_frames': [commits[0]['part']['logical_start'], commits[1]['part']['logical_end']],
        'primary_commit_refs': [anchor(c) for c in commits],
        'adds_primary_audio': False, 'automatic_dispatch': False}
    if doc['schema'] in (RESET_SCHEMA, RESTART_SCHEMA):
        result.update(kind=('restart_replay_core_seam_alternative' if doc['schema'] == RESTART_SCHEMA
                            else 'reset_replay_core_seam_alternative'),
            scope_sha256=sha({'primary_scopes': [c['scope'] for c in commits], 'query_scope': commits[1]['scope']}),
            primary_scope_sha256=[sha(c['scope']) for c in commits],
            query_scope_sha256=sha(commits[1]['scope']))
    return result


def validate_query(query, right, shape, evidence, schema=SCHEMA):
    """Reuse strict v2 current-response frontier checks without relaxing v2."""
    from .native_owned_seam_cells import validate_witness
    names = 'query_start_frame query_end_frame query_pcm16le_sha256 support_pcm16le_sha256 native turns'
    exact(query, names + (' capture_run' if schema == RESTART_SCHEMA else ''))
    a, b = shape['start_frame'], shape['end_frame']
    qa, qb = query['query_start_frame'], query['query_end_frame']
    integer(qa); integer(qb)
    if not qa <= a < b <= qb or not 0 < qb - qa <= CORE_FRAMES:
        raise Unavailable('core_seam_query_coverage')
    digest(query['query_pcm16le_sha256']); digest(query['support_pcm16le_sha256'])
    p = right['part']; scope = right['scope']
    turns = query['turns']
    if schema == RESTART_SCHEMA:
        if query['capture_run'] != scope['run']:raise Unavailable('core_restart_query_run')
        if (qa, qb) != (a, b):raise Unavailable('core_restart_exact_query_required')
        if query['query_pcm16le_sha256'] != query['support_pcm16le_sha256']:
            raise Unavailable('core_restart_query_pcm_mismatch')
        if type(turns) is not list or len(turns) > MAX_TURNS:raise Unavailable('native_turn_capacity')
        prefix = scope['run'] + ':'
        normalized = []
        for turn in turns:
            exact(turn, 'label original')
            if type(turn['label']) is not str or not turn['label'].startswith(prefix):
                raise Unavailable('core_restart_query_label_run')
            normalized.append({**turn, 'label': turn['label'][len(prefix):]})
        turns = normalized
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
        'native': query['native'], 'turns': turns}
    validate_witness(legacy_witness, legacy_commit, legacy_scope, 0, a, b)
    if (sha(evidence['native']) != right['native_sha256'] or sha(evidence['core_cut']) != right['cut_sha256']
            or sha(query['native']['provenance']) != right['runtime_sha256']):
        raise Unavailable('core_seam_evidence_mismatch')


def validate_document(proof):
    if type(proof) is not bytes or not 1 <= len(proof) <= PROOF_BYTES:raise Unavailable('proof_capacity')
    doc = json.loads(proof)
    if type(doc) is dict and doc.get('schema') == BOUNDED_SCHEMA:
        return validate_bounded_document(proof, doc)
    exact(doc, 'schema policy commits geometry support dependencies query right_evidence selection publication_anchor selected coverage')
    if not is_core(doc) or doc['policy'] != policy(doc['schema']) or doc['selected'] is not True:
        raise Unavailable('core_seam_policy')
    commits = doc['commits']
    if type(commits) is not list or len(commits) != 2:raise Unavailable('core_seam_two_commits')
    shape = geometry(commits, doc['schema'])
    if doc['geometry'] != shape:raise Unavailable('core_seam_nondeterministic_geometry')
    parts = mapping(commits, shape)
    if doc['dependencies'] != [asdict(p) for p in parts]:raise Unavailable('core_seam_mapping')
    validate_query(doc['query'], commits[1], shape, doc['right_evidence'], doc['schema'])
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


def overlap_components(turns, start, end):
    """Full positive distinct-label union from one response, in bounded events.

    Per-label counts avoid inventing overlap from duplicate same-label turns.
    End/start events are applied together at half-open boundaries. Neither pair
    ordering nor a display name participates in the resulting target identity.
    """
    integer(start); integer(end)
    if start >= end or type(turns) not in (tuple, list) or len(turns) > MAX_TURNS:
        raise Unavailable('bounded_overlap_shape')
    events = {}; labels = set()
    for turn in turns:
        if type(turn) is Turn:
            label, a, b = turn.label, turn.start, turn.end
        else:
            if type(turn) is not dict or not {'label', 'original'} <= set(turn):
                raise Unavailable('bounded_overlap_shape')
            label = text(turn['label']); interval = turn['original']
            if type(interval) is not list or len(interval) != 2:
                raise Unavailable('bounded_overlap_shape')
            a, b = interval
        text(label); integer(a); integer(b)
        if a >= b:raise Unavailable('native_turn_geometry')
        labels.add(label)
        if len(labels) > 8:raise Unavailable('native_label_capacity')
        a, b = max(start, a), min(end, b)
        if a < b:
            events.setdefault(a, []).append((label, 1))
            events.setdefault(b, []).append((label, -1))
    active = {}; previous = None; result = []
    for position in sorted(events):
        if previous is not None and previous < position and len(active) > 1:
            if result and result[-1][1] == previous:result[-1][1] = position
            else:result.append([previous, position])
        for label, delta in events[position]:
            count = active.get(label, 0) + delta
            if count:active[label] = count
            else:active.pop(label, None)
        previous = position
    if not result:raise Unavailable('no_same_response_native_overlap')
    if len(result) > MAX_TURNS:raise Unavailable('bounded_overlap_capacity')
    return result


def bounded_chain(commits):
    """Validate original compact slots without coalescing their authority."""
    if type(commits) not in (tuple, list) or not 1 <= len(commits) <= MAX_BOUND_COMMITS:
        raise Unavailable('commitment_capacity')
    parts = [validate_commit(c) for c in commits]
    if not math.isfinite(float.fromhex(commits[0]['scope']['origin'])):
        raise Unavailable('core_seam_origin')
    if len({slot(c) for c in commits}) != len(commits):
        raise Unavailable('bounded_duplicate_commitment')
    first = commits[0]
    for c in commits:
        if c['scope'] != first['scope']:
            raise Unavailable('unresolved_scope_transition')
        if c['runtime_sha256'] != first['runtime_sha256']:
            raise Unavailable('core_seam_runtime_changed')
    seen = set(); previous = None
    for p in parts:
        if previous is not None:
            if previous.logical_end != p.logical_start:
                raise Unavailable('source_gap')
            if p.chunk_id == previous.chunk_id:
                if (p.source_start != previous.source_end or
                        (p.session_id, p.receipt_sha256, p.source_sha256, p.source_frames) !=
                        (previous.session_id, previous.receipt_sha256, previous.source_sha256, previous.source_frames)):
                    raise Unavailable('core_seam_source_alias')
            elif p.chunk_id in seen:raise Unavailable('source_mapping_repeated_alias')
        seen.add(p.chunk_id); previous = p
    return parts[0].logical_start, parts[-1].logical_end


def bounded_mapping(commits, start, end):
    a, b = bounded_chain(commits); integer(start); integer(end)
    if not a <= start < end <= b:raise Unavailable('authority_unavailable')
    parts = []
    for c in commits:
        p = SourceSpan(**c['part'])
        lo, hi = max(start, p.logical_start), min(end, p.logical_end)
        if lo >= hi:continue
        p = SourceSpan(p.session_id, p.chunk_id, p.receipt_sha256, p.source_sha256,
            p.source_frames, p.source_start + lo - p.logical_start,
            p.source_start + hi - p.logical_start, lo, hi)
        if parts and parts[-1].chunk_id == p.chunk_id:
            old = parts.pop()
            if (old.source_end != p.source_start or old.logical_end != p.logical_start or
                    (old.session_id, old.receipt_sha256, old.source_sha256, old.source_frames) !=
                    (p.session_id, p.receipt_sha256, p.source_sha256, p.source_frames)):
                raise Unavailable('core_seam_source_alias')
            p = SourceSpan(old.session_id, old.chunk_id, old.receipt_sha256, old.source_sha256,
                old.source_frames, old.source_start, p.source_end, old.logical_start, p.logical_end)
        parts.append(p)
    return validate_mapping(tuple(parts), start, end)


def origin_certificate(proof, commits, binding):
    if type(proof) is not bytes or type(binding) is not bytes:
        raise Unavailable('bounded_origin_shape')
    if not 1 <= len(proof) <= PROOF_BYTES or not 1 <= len(binding) <= 4096:
        raise Unavailable('bounded_origin_capacity')
    result = {'proof': proof.decode('ascii'), 'binding': binding.decode('ascii'),
              'commits': list(commits)}
    _origin_identity(result)
    canonical(result)
    return result


def _origin_identity(origin):
    """Revalidate genuine v1 originals. A final plan can never become an origin."""
    exact(origin, 'proof binding commits')
    if (type(origin['proof']) is not str or not 1 <= len(origin['proof']) <= PROOF_BYTES
            or type(origin['binding']) is not str or not 1 <= len(origin['binding']) <= 4096):
        raise Unavailable('bounded_origin_capacity')
    raw = origin['proof'].encode('ascii'); binding = origin['binding'].encode('ascii')
    parsed = json.loads(raw)
    if type(parsed) is not dict or parsed.get('schema') not in (None, SCHEMA):
        raise Unavailable('unresolved_scope_transition')
    from .native_owned_cells import validate_document as validate_original
    from .moss_automatic import ordinary_binding, source_binding
    doc, support, parts = validate_original(raw)
    commits = origin['commits']; bounded_chain(commits)
    if 'schema' not in doc:
        if len(commits) != 1:raise Unavailable('bounded_origin_commitments')
        ordinary_binding(doc, commits[0]); kind = 'ordinary'; turns = doc['turns']
        scope = commits[0]['scope']
        if (doc['native']['epoch_id'] != scope['epoch'] or doc['native']['epoch_index'] != scope['index']
                or doc['commit_evidence']['native']['epoch_id'] != scope['epoch']
                or doc['commit_evidence']['native']['epoch_index'] != scope['index']):
            raise Unavailable('unresolved_scope_transition')
    else:
        if commits != doc['commits']:raise Unavailable('bounded_origin_commitments')
        kind = 'core_seam'; turns = doc['query']['turns']
    if binding != source_binding(kind, doc, support, parts, commits):
        raise Unavailable('bounded_origin_binding')
    if bounded_mapping(commits, support.start_frame, support.end_frame) != parts:
        raise Unavailable('bounded_origin_mapping')
    components = overlap_components(turns, support.start_frame, support.end_frame)
    scope = commits[0]['scope']
    return {'schema': TARGET_SCHEMA, 'scope': {k: scope[k] for k in ('session_id', 'kind', 'key', 'origin')},
        'components': components,
        'mapping': [[asdict(p) for p in intersect_mapping(parts, a, b)] for a, b in components]}


def original_target(origin):
    identity = _origin_identity(origin)
    target = {'schema': TARGET_SCHEMA, 'key': sha(identity), 'identity': identity, 'origins': [origin]}
    validate_target(target)
    return target


def validate_target(target):
    exact(target, 'schema key identity origins')
    if target['schema'] != TARGET_SCHEMA:raise Unavailable('bounded_target_policy')
    digest(target['key']); origins = target['origins']
    if type(origins) is not list or not 1 <= len(origins) <= MAX_ORIGINS:
        raise Unavailable('bounded_origin_capacity')
    identity = None; roots = {}; fingerprints = set()
    for origin in origins:
        current_identity = _origin_identity(origin)
        if identity is None:identity = current_identity
        if current_identity != identity:raise Unavailable('bounded_target_alias_mismatch')
        fingerprint = sha(origin)
        if fingerprint in fingerprints:raise Unavailable('bounded_origin_duplicate')
        fingerprints.add(fingerprint)
        for c in origin['commits']:
            key = slot(c)
            if key in roots and roots[key] != c:raise Unavailable('core_slot_evidence_conflict')
            roots[key] = c
    if len(roots) > MAX_BOUND_COMMITS:raise Unavailable('commitment_capacity')
    ordered = sorted(roots.values(), key=lambda c: c['part']['logical_start'])
    bounded_chain(ordered)
    if target['identity'] != identity or target['key'] != sha(identity):
        raise Unavailable('bounded_target_identity')
    canonical(target)
    return identity, ordered


def append_target_origin(target, origin):
    identity, _ = validate_target(target)
    if _origin_identity(origin) != identity:raise Unavailable('bounded_target_alias_mismatch')
    if origin in target['origins']:return target
    result = {**target, 'origins': [*target['origins'], origin]}
    validate_target(result)
    return result


def lexical_snapshot(sid, rows, sources, scope_origin, *, complete=True, scan_frames=None,
                     schema=INVENTORY_SCHEMA):
    from .native_owned_support_cache import ReadError
    try:
        if schema not in (INVENTORY_SCHEMA, COMPACT_INVENTORY_SCHEMA, SUPPORT_LOCAL_INVENTORY_SCHEMA):
            raise Unavailable('bounded_inventory_policy')
        view = _lexical_snapshot(sid, rows, sources, scope_origin,
            complete=complete, scan_frames=scan_frames,
            allow_empty_sources=schema == SUPPORT_LOCAL_INVENTORY_SCHEMA)
        return _encode_inventory(view, schema)
    except ReadError as exc:
        # Shared projection validators use their HTTP/read-domain exception.
        # Numeric planning keeps this an optional unresolved placement result.
        raise Unavailable(exc.reason) from exc
    except Unavailable as exc:
        if str(exc) == 'serialization_capacity' and getattr(exc, 'capacity_phase', None) is None:exc.capacity_phase = 'retained_inventory'
        raise


def _lexical_snapshot(sid, rows, sources, scope_origin, *, complete=True, scan_frames=None,
                      allow_empty_sources=False):
    """Pure adapter over the *existing* inventory and ordinal-group validators.

    The caller supplies a complete bounded indexed scan and source snapshots.
    No database or source I/O is hidden here, including for a stale inventory.
    Retaining exact rows allows proof decoding to repeat identical semantics.
    """
    from .lexical_evidence import current_inventory, SOURCE_FIELDS, MAX_SEGMENT_BYTES
    from .transcript_contract import PRIMARY_FIELDS, primary_evidence, primary_frame
    from .transcript_projection import lexical_groups
    from .transcript_store import CONTEXT_SECONDS
    text(sid)
    if complete is not True:raise Unavailable('incomplete_primary')
    if type(rows) not in (list, tuple) or len(rows) > MAX_PLAN_PARENTS:
        raise Unavailable('primary_capacity')
    if type(sources) is not dict or not (0 if allow_empty_sources else 1) <= len(sources) <= MAX_PARTS:
        raise Unavailable('source_part_capacity')
    origin = exact_frame(scope_origin)
    if type(scan_frames) not in (list, tuple) or len(scan_frames) != 2:
        raise Unavailable('incomplete_primary')
    scan_start, scan_end = scan_frames; integer(scan_start); integer(scan_end)
    if scan_start >= scan_end:raise Unavailable('incomplete_primary')
    retained_sources = []
    for cid in sorted(sources):
        source = sources[cid]
        if any(k not in source for k in SOURCE_FIELDS):raise Unavailable('source_unavailable')
        item = {k: source[k] for k in SOURCE_FIELDS}
        text(cid); digest(item['digest'])
        if item['id'] != cid or item['session_id'] != sid:raise Unavailable('source_unavailable')
        exact_frame(item['timeline_start']); integer(exact_frame(item['duration']), minimum=1)
        retained_sources.append(item)
    parents = []; atoms = []; units = 0; seen = set()
    for supplied in rows:
        row = {k: supplied[k] for k in (*PRIMARY_FIELDS, 'lexical_evidence')}
        if row['id'] in seen:raise Unavailable('bounded_primary_duplicate')
        seen.add(row['id'])
        if row['chunk_id'] not in sources:raise Unavailable('source_unavailable')
        raw = row['lexical_evidence']
        if raw is not None and (type(raw) is not str or len(raw) > MAX_SEGMENT_BYTES):
            raise Unavailable('inventory_capacity')
        parent = primary_evidence(sid, row)
        own = sources[row['chunk_id']]
        if (parent['start_frame'] < max(0, primary_frame(own['timeline_start']) - CONTEXT_SECONDS * RATE) or
                parent['end_frame'] > primary_frame(own['timeline_start'] + own['duration'], end=True) + CONTEXT_SECONDS * RATE):
            raise Unavailable('primary_source_placement')
        if not (parent['start_frame'] < origin + scan_end and origin + scan_start < parent['end_frame']):
            raise Unavailable('bounded_primary_scan_mismatch')
        current = current_inventory(None, sid, row, source_lookup=lambda cid: sources.get(cid))
        groups = lexical_groups(parent, current) if current is not None else None
        if current is not None:
            units += len(current['inventory']['units'])
            if units > MAX_PLAN_UNITS:raise Unavailable('lexical_unit_capacity')
        parent_atoms = groups if groups is not None else [parent]
        descriptors = []
        for item in parent_atoms:
            atom = {'source': item['source'], 'segment_id': row['id'],
                'start_frame': item['start_frame'] - origin, 'end_frame': item['end_frame'] - origin}
            # ASR context may precede a nonzero recording scope origin. Keep
            # that known placement; only source/support frames must be >= 0.
            integer(atom['start_frame'], minimum=-((1 << 53) - 1))
            integer(atom['end_frame'], minimum=-((1 << 53) - 1))
            if groups is not None:
                atom.update(unit_range=item['unit_range'], display_span=item['display_span'], raw_span=item['raw_span'],
                    parent_start_frame=item['parent_start_frame'] - origin,
                    parent_end_frame=item['parent_end_frame'] - origin,
                    inventory_sha256=current['inventory_sha256'], policy_sha256=item['policy_sha256'])
            descriptors.append(atom); atoms.append(atom)
            if len(atoms) > MAX_PLAN_ATOMS:raise Unavailable('lexical_atom_capacity')
        parents.append({'row': row, 'primary_sha256': parent['content_sha256'],
            'inventory_sha256': current['inventory_sha256'] if current is not None else None,
            'atoms': descriptors})
    snapshot = {'schema': INVENTORY_SCHEMA, 'session_id': sid, 'origin': float(scope_origin).hex(),
        'scan_frames': [scan_start, scan_end], 'complete': True,
        'parents': parents, 'sources': retained_sources, 'atoms': atoms, 'unit_count': units}
    return snapshot


def _encode_inventory(view, schema):
    """Persist one lossless row/inventory copy, never the derived atom view."""
    if schema == INVENTORY_SCHEMA:
        canonical(view)
        return view
    if schema not in (COMPACT_INVENTORY_SCHEMA, SUPPORT_LOCAL_INVENTORY_SCHEMA):
        raise Unavailable('bounded_inventory_policy')
    from .lexical_evidence import encoded
    parents = []
    for parent in view['parents']:
        row = dict(parent['row'])
        if parent['inventory_sha256'] is not None:
            raw = row['lexical_evidence']; value = json.loads(raw)
            if encoded(value).decode('ascii') != raw:
                raise Unavailable('bounded_inventory_roundtrip')
            row['lexical_evidence'] = {'encoding': 'canonical-json-v1', 'value': value}
        parents.append({'row': row})
    result = {key: view[key] for key in
        ('session_id', 'origin', 'scan_frames', 'complete', 'sources', 'unit_count')}
    result.update(schema=schema, parents=parents)
    if schema == SUPPORT_LOCAL_INVENTORY_SCHEMA:
        required = _local_source_ids(view)
        result['sources'] = [source for source in view['sources'] if source['id'] in required]
        result['scope_policy'] = SUPPORT_LOCAL_SCOPE_POLICY
    canonical(result)
    return result


def _local_source_ids(view):
    """Keep whole parents' own sources and independently valid lexical inputs.

    Embedded descriptors establish lexical validity, never current authority.
    Every independently valid input must also match the already validated
    complete supplied source snapshot before context-only sources are pruned.
    """
    from .lexical_evidence import current_inventory, SOURCE_FIELDS
    available = {source['id']: source for source in view['sources']}
    required = set()
    for parent in view['parents']:
        row = parent['row']; required.add(row['chunk_id'])
        raw = row['lexical_evidence']
        if raw is None:continue
        try:
            value = json.loads(raw)
            parts = value.get('source_parts') if type(value) is dict else None
            embedded = {part['id']: part for part in parts} if type(parts) is list else {}
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError, OverflowError):
            continue
        independent = current_inventory(None, view['session_id'], row,
            source_lookup=lambda cid: embedded.get(cid))
        if independent is None:continue
        for part in independent['inventory']['source_parts']:
            actual = available.get(part['id'])
            if actual is None or any(actual[key] != part[key] for key in SOURCE_FIELDS):
                raise Unavailable('bounded_inventory_source_changed')
            required.add(part['id'])
    return required


def localize_inventory(target, commits, snapshot, query_start, query_end, support, *, preparation=False):
    """Freeze a full-band result, retain whole parents, and require exact replay."""
    from .transcript_contract import primary_evidence
    if type(snapshot) is not dict or snapshot.get('schema') not in (
            INVENTORY_SCHEMA, COMPACT_INVENTORY_SCHEMA):
        raise Unavailable('bounded_inventory_policy')
    exact(support, 'start_frame end_frame')
    integer(support['start_frame']); integer(support['end_frame'])
    if type(preparation) is not bool:raise Unavailable('bounded_plan_scope')
    options = {'preparation': True} if preparation else {}
    selected = select_support(target, commits, snapshot, query_start, query_end, **options)
    if selected != support:
        exc=Unavailable('bounded_local_selection_changed');exc.local_selection_phase='full_band_reselect'
        raise exc
    view = inventory_view(snapshot)
    start, end = selected['start_frame'], selected['end_frame']
    origin = exact_frame(float.fromhex(view['origin']))
    rows = []
    for parent in view['parents']:
        evidence = primary_evidence(view['session_id'], parent['row'])
        if evidence['start_frame'] < origin + end and origin + start < evidence['end_frame']:
            rows.append(parent['row'])
    local = lexical_snapshot(view['session_id'], rows,
        {source['id']: source for source in view['sources']}, float.fromhex(view['origin']),
        complete=True, scan_frames=[start, end], schema=SUPPORT_LOCAL_INVENTORY_SCHEMA)
    replay = select_support(target, commits, local, query_start, query_end)
    if replay != selected or local['scan_frames'] != [start, end]:
        exc=Unavailable('bounded_local_selection_changed');exc.local_selection_phase='local_replay'
        raise exc
    return local


def _legacy_inventory_view(snapshot):
    """Exact v1 wire validation; historical bytes and hashes remain unchanged."""
    exact(snapshot, 'schema session_id origin scan_frames complete parents sources atoms unit_count')
    if snapshot['schema'] != INVENTORY_SCHEMA:raise Unavailable('bounded_inventory_policy')
    if type(snapshot['parents']) is not list or len(snapshot['parents']) > MAX_PLAN_PARENTS:
        raise Unavailable('primary_capacity')
    if type(snapshot['sources']) is not list or not 1 <= len(snapshot['sources']) <= MAX_PARTS:
        raise Unavailable('source_part_capacity')
    sources = {}
    for source in snapshot['sources']:
        if type(source) is not dict or source.get('id') in sources:
            raise Unavailable('bounded_source_duplicate')
        sources[source.get('id')] = source
    try:origin = float.fromhex(snapshot['origin'])
    except (TypeError, ValueError) as exc:raise Unavailable('core_seam_origin') from exc
    if not math.isfinite(origin) or origin.hex() != snapshot['origin']:
        raise Unavailable('core_seam_origin')
    expected = lexical_snapshot(snapshot['session_id'], [p['row'] for p in snapshot['parents']],
        sources, origin, complete=snapshot['complete'], scan_frames=snapshot['scan_frames'])
    if canonical(expected) != canonical(snapshot):raise Unavailable('bounded_inventory_mismatch')
    return snapshot


def inventory_view(snapshot):
    """Validate versioned wire bytes and regenerate a bounded, pure semantic view.

    Only the original wire payload may enter proof/core hashes or byte charges.
    The derived v1-shaped view is temporary and is never persisted or serialized.
    """
    if type(snapshot) is not dict:raise Unavailable('core_seam_shape')
    if snapshot.get('schema') == INVENTORY_SCHEMA:return _legacy_inventory_view(snapshot)
    schema = snapshot.get('schema')
    if schema not in (COMPACT_INVENTORY_SCHEMA, SUPPORT_LOCAL_INVENTORY_SCHEMA):
        raise Unavailable('bounded_inventory_policy')
    local_scope = schema == SUPPORT_LOCAL_INVENTORY_SCHEMA
    try:canonical(snapshot)  # Screen the complete wire input before expansion.
    except Unavailable as exc:
        if str(exc) == 'serialization_capacity' and getattr(exc, 'capacity_phase', None) is None:exc.capacity_phase = 'retained_inventory'
        raise
    exact(snapshot, 'schema session_id origin scan_frames complete parents sources unit_count' +
        (' scope_policy' if local_scope else ''))
    if local_scope and snapshot['scope_policy'] != SUPPORT_LOCAL_SCOPE_POLICY:
        raise Unavailable('bounded_inventory_scope_policy')
    if type(snapshot['parents']) is not list or len(snapshot['parents']) > MAX_PLAN_PARENTS:
        raise Unavailable('primary_capacity')
    if type(snapshot['sources']) is not list or not (0 if local_scope else 1) <= len(snapshot['sources']) <= MAX_PARTS:
        raise Unavailable('source_part_capacity')
    integer(snapshot['unit_count'], maximum=MAX_PLAN_UNITS)
    sources = {}
    for source in snapshot['sources']:
        if type(source) is not dict or source.get('id') in sources:
            raise Unavailable('bounded_source_duplicate')
        sources[source.get('id')] = source
    try:origin = float.fromhex(snapshot['origin'])
    except (TypeError, ValueError) as exc:raise Unavailable('core_seam_origin') from exc
    if not math.isfinite(origin) or origin.hex() != snapshot['origin']:
        raise Unavailable('core_seam_origin')
    from .lexical_evidence import encoded, MAX_SEGMENT_BYTES
    from .transcript_contract import PRIMARY_FIELDS
    rows = []; tagged = []
    for index, parent in enumerate(snapshot['parents']):
        exact(parent, 'row')
        row = dict(exact(parent['row'], ' '.join((*PRIMARY_FIELDS, 'lexical_evidence'))))
        raw = row['lexical_evidence']
        if type(raw) is dict:
            exact(raw, 'encoding value')
            if raw['encoding'] != 'canonical-json-v1' or type(raw['value']) is not dict:
                raise Unavailable('bounded_inventory_encoding')
            units = raw['value'].get('units')
            if type(units) is not list or len(units) > MAX_PLAN_UNITS:
                raise Unavailable('lexical_unit_capacity')
            raw = encoded(raw['value']).decode('ascii'); tagged.append(index)
            row['lexical_evidence'] = raw
        if raw is not None and (type(raw) is not str or len(raw) > MAX_SEGMENT_BYTES):
            raise Unavailable('inventory_capacity')
        rows.append(row)
    from .native_owned_support_cache import ReadError
    try:
        view = _lexical_snapshot(snapshot['session_id'], rows, sources, origin,
            complete=snapshot['complete'], scan_frames=snapshot['scan_frames'],
            allow_empty_sources=local_scope)
    except ReadError as exc:raise Unavailable(exc.reason) from exc
    if any(view['parents'][index]['inventory_sha256'] is None for index in tagged):
        raise Unavailable('bounded_inventory_encoding')
    if canonical(_encode_inventory(view, schema)) != canonical(snapshot):
        raise Unavailable('bounded_inventory_mismatch')
    return view


def validate_inventory(snapshot):
    inventory_view(snapshot)
    return snapshot


def same_numeric_snapshot(planned, actual, row_ids=None):
    """Match retained transaction rows to drafts, allowing only actual row IDs.

    A generated inventory hash changes with its primary row ID. Its exact
    canonical body may therefore differ only in that validated primary hash;
    all units, ordinals, text partitions and source snapshots remain equal.
    Missing/refused inventory cannot silently convert a planned lexical atom
    into a whole opaque parent or choose a second support.
    """
    if type(planned) is not dict or type(actual) is not dict:
        raise Unavailable('core_seam_shape')
    if planned.get('schema') != actual.get('schema'):return False
    planned, actual = inventory_view(planned), inventory_view(actual)
    row_ids = {} if row_ids is None else row_ids
    if type(row_ids) is not dict or len(row_ids) > MAX_PLAN_PARENTS:
        raise Unavailable('bounded_row_binding')
    for before, after in row_ids.items():
        integer(before, maximum=(1 << 63) - 1, minimum=1)
        integer(after, maximum=(1 << 63) - 1, minimum=1)
    if any(planned[k] != actual[k] for k in ('schema', 'session_id', 'origin', 'scan_frames', 'complete', 'sources', 'unit_count')):
        return False
    old = planned['parents']; new = actual['parents']
    if len(old) != len(new):return False
    by_id = {p['row']['id']: p for p in new}
    if len(by_id) != len(new):return False
    observed = set()
    for before in old:
        old_row = before['row']; new_id = row_ids.get(old_row['id'], old_row['id'])
        after = by_id.get(new_id)
        if after is None or new_id in observed:return False
        observed.add(new_id); new_row = after['row']
        if any(old_row[k] != new_row[k] for k in old_row if k not in ('id', 'lexical_evidence')):
            return False
        left, right = old_row['lexical_evidence'], new_row['lexical_evidence']
        if old_row['id'] == new_id:
            if left != right:return False
        elif before['inventory_sha256'] is None:
            if left != right or after['inventory_sha256'] is not None:return False
        else:
            if after['inventory_sha256'] is None:return False
            lv, rv = json.loads(left), json.loads(right)
            if {k: v for k, v in lv.items() if k != 'primary_sha256'} != {
                    k: v for k, v in rv.items() if k != 'primary_sha256'}:return False
        if len(before['atoms']) != len(after['atoms']):return False
        for one, two in zip(before['atoms'], after['atoms']):
            expected = {**one, 'segment_id': new_id}
            if 'inventory_sha256' in expected:expected['inventory_sha256'] = after['inventory_sha256']
            if expected != two:return False
    return True


def target_context(target, commits):
    """Complete numeric envelope of every legal <=20s support containing O.

    This is preparation context, never a selected-support certificate. Every
    intersecting parent must remain whole, including all of its outside atoms.
    """
    identity, _ = validate_target(target)
    ca, cb = bounded_chain(commits)
    first, last = identity['components'][0][0], identity['components'][-1][1]
    lo, hi = max(ca, last - SUPPORT_FRAMES), min(cb, first + SUPPORT_FRAMES)
    if not lo <= first < last <= hi:raise Unavailable('authority_unavailable')
    return [lo, hi]


def context_snapshot(sid, rows, sources, origin, scan_frames, *, complete_frames):
    """Filter complete raw context before atom expansion, never clip a parent."""
    from .transcript_contract import primary_evidence
    if type(rows) not in (list, tuple) or len(rows) > MAX_PLAN_PARENTS:
        raise Unavailable('bounded_primary_capacity')
    start, end = scan_frames; base = exact_frame(origin)
    for frame in (*scan_frames, *complete_frames):integer(frame)
    if not complete_frames[0] <= start < end <= complete_frames[1]:
        raise Unavailable('incomplete_primary')
    selected = []
    for row in rows:
        parent = primary_evidence(sid, row)
        if parent['start_frame'] < base + end and base + start < parent['end_frame']:
            selected.append(row)
    return lexical_snapshot(sid, selected, sources, origin,
        scan_frames=scan_frames, schema=COMPACT_INVENTORY_SCHEMA)


def select_support(target, commits, snapshot, query_start, query_end, *, preparation=False):
    """One deterministic fixed point, never a search over acoustic attempts."""
    from .transcript_projection import _legal
    local_scope = type(snapshot) is dict and snapshot.get('schema') == SUPPORT_LOCAL_INVENTORY_SCHEMA
    identity, originals = validate_target(target); snapshot = inventory_view(snapshot)
    ca, cb = bounded_chain(commits); integer(query_start); integer(query_end)
    all_roots = {slot(c): c for c in originals}
    for c in commits:
        if slot(c) in all_roots and all_roots[slot(c)] != c:
            raise Unavailable('core_slot_evidence_conflict')
        all_roots[slot(c)] = c
    if len(all_roots) > MAX_BOUND_COMMITS:raise Unavailable('commitment_capacity')
    bounded_chain(sorted(all_roots.values(), key=lambda c: c['part']['logical_start']))
    if type(preparation) is not bool:raise Unavailable('bounded_plan_scope')
    required = target_context(target, commits) if preparation else [ca, cb]
    if preparation and (local_scope or snapshot['schema'] != INVENTORY_SCHEMA):
        raise Unavailable('bounded_plan_scope')
    if (identity['scope'] != {k: commits[0]['scope'][k] for k in identity['scope']}
            or snapshot['session_id'] != identity['scope']['session_id']
            or snapshot['origin'] != identity['scope']['origin']
            or (not local_scope and not snapshot['scan_frames'][0] <= required[0] < required[1] <= snapshot['scan_frames'][1])):
        raise Unavailable('bounded_plan_scope')
    if not 0 < query_end - query_start <= CORE_FRAMES:raise Unavailable('core_seam_query_coverage')
    atoms = snapshot['atoms']; components = identity['components']
    if local_scope:
        sa, sb = snapshot['scan_frames']
        if (not ca <= sa < sb <= cb or not query_start <= sa < sb <= query_end
                or not all(sa <= lo < hi <= sb for lo, hi in components)):
            raise Unavailable('bounded_plan_scope')
    def selected(a, b):
        if local_scope and [a, b] != snapshot['scan_frames']:
            exc=Unavailable('bounded_local_selection_changed');exc.local_selection_phase='local_scope_geometry'
            raise exc
        return {'start_frame': a, 'end_frame': b}
    # Exact source placement must remain equal, even for an alias-origin O.
    for (a, b), expected in zip(components, identity['mapping']):
        if [asdict(p) for p in bounded_mapping(commits, a, b)] != expected:
            raise Unavailable('bounded_target_mapping_changed')
    def legal(a, b):
        return (0 < b - a <= SUPPORT_FRAMES and ca <= a < b <= cb
            and (not local_scope or snapshot['scan_frames'][0] <= a < b <= snapshot['scan_frames'][1])
            and query_start <= a < b <= query_end
            and all(a <= lo < hi <= b for lo, hi in components)
            and any(a < atom['end_frame'] and atom['start_frame'] < b for atom in atoms)
            and _legal(a, b, atoms))
    original = json.loads(target['origins'][0]['proof'])['support']
    a, b = original['start_frame'], original['end_frame']
    if legal(a, b):return selected(a, b)
    a, b = components[0][0], components[-1][1]; included = set()
    ordered = sorted(enumerate(atoms), key=lambda pair: (pair[1]['start_frame'], pair[1]['end_frame'], pair[0]))
    changed = True
    while changed:
        changed = False
        for index, atom in ordered:
            if index not in included and a < atom['end_frame'] and atom['start_frame'] < b:
                included.add(index)
                lo, hi = min(a, atom['start_frame']), max(b, atom['end_frame'])
                if (lo, hi) != (a, b):changed = True
                a, b = lo, hi
        if b - a > SUPPORT_FRAMES:raise Unavailable('support_duration')
    if not included:raise Unavailable('unresolved_lexical_gap')
    if not ca <= a < b <= cb:raise Unavailable('authority_unavailable')
    if not query_start <= a < b <= query_end:raise Unavailable('witness_unavailable')
    if not legal(a, b):raise Unavailable('no_legal_fixed_point')
    return selected(a, b)


def dependency_roots(target, commits, inventories):
    """One canonical role mask per (source, compact); slots never coalesce."""
    _, originals = validate_target(target); bounded_chain(commits)
    inventories = inventory_view(inventories)
    roots = {}; unique_slots = set()
    def add(cid, compact, role):
        text(cid)
        if compact is not None:digest(compact); unique_slots.add(compact)
        key = (cid, compact)
        roots[key] = roots.get(key, 0) | role
    for c in originals:add(c['part']['chunk_id'], slot(c), 1)
    for c in commits:add(c['part']['chunk_id'], slot(c), 2)
    for source in inventories['sources']:add(source['id'], None, 4)
    for parent in inventories['parents']:add(parent['row']['chunk_id'], None, 8)
    if len(unique_slots) > MAX_BOUND_COMMITS:raise Unavailable('commitment_capacity')
    if len({cid for cid, _ in roots}) > MAX_PARTS:raise Unavailable('source_part_capacity')
    result = [{'kind': roots[key], 'cid': key[0], 'slot': key[1]}
              for key in sorted(roots, key=lambda key: (key[0], key[1] or ''))]
    try:canonical(result, 4096)
    except Unavailable as exc:
        if str(exc) == 'serialization_capacity' and getattr(exc, 'capacity_phase', None) is None:exc.capacity_phase = 'dependency_roots'
        raise
    return result


def _bounded_query(query, commit, shape, evidence):
    exact(query, 'query_start_frame query_end_frame query_pcm16le_sha256 support_pcm16le_sha256 native turns capture_run')
    if query['capture_run'] != commit['scope']['run']:raise Unavailable('bounded_query_run')
    turns = query['turns']
    if type(turns) is not list or len(turns) > MAX_TURNS:raise Unavailable('native_turn_capacity')
    prefix = query['capture_run'] + ':'; normalized = []
    for t in turns:
        exact(t, 'label original')
        if type(t['label']) is not str or not t['label'].startswith(prefix):
            raise Unavailable('bounded_query_label_run')
        normalized.append({**t, 'label': t['label'][len(prefix):]})
    legacy = {k: v for k, v in query.items() if k != 'capture_run'}
    legacy['turns'] = normalized
    validate_query(legacy, commit, shape, evidence, SCHEMA)


def validate_bounded_document(proof, doc=None):
    if type(proof) is not bytes or not 1 <= len(proof) <= PROOF_BYTES:raise Unavailable('proof_capacity')
    if doc is None:doc = json.loads(proof)
    exact(doc, 'schema policy commits geometry support dependencies query right_evidence selection publication_anchor selected coverage target inventories dependency_roots target_sha256')
    if doc['schema'] != BOUNDED_SCHEMA or doc['policy'] != BOUNDED_POLICY_SHA256 or doc['selected'] is not True:
        raise Unavailable('core_seam_policy')
    identity, originals = validate_target(doc['target']); commits = doc['commits']
    ca, cb = bounded_chain(commits); validate_inventory(doc['inventories'])
    shape = exact(doc['geometry'], 'start_frame end_frame')
    _bounded_query(doc['query'], commits[-1], shape, doc['right_evidence'])
    if doc['target_sha256'] != sha(doc['target']):raise Unavailable('bounded_target_hash')
    expected_shape = select_support(doc['target'], commits, doc['inventories'],
        doc['query']['query_start_frame'], doc['query']['query_end_frame'])
    if shape != expected_shape:raise Unavailable('bounded_nondeterministic_support')
    current_overlap = overlap_components(doc['query']['turns'], shape['start_frame'], shape['end_frame'])
    if any(not any(a <= lo < hi <= b for a, b in current_overlap) for lo, hi in identity['components']):
        raise Unavailable('bounded_current_target_disagreement')
    # Current query scope is identical to every accepted origin's acoustic scope.
    if any(c['scope'] != commits[-1]['scope'] for c in originals):
        raise Unavailable('unresolved_scope_transition')
    parts = bounded_mapping(commits, shape['start_frame'], shape['end_frame'])
    if doc['dependencies'] != [asdict(p) for p in parts]:raise Unavailable('core_seam_mapping')
    if doc['dependency_roots'] != dependency_roots(doc['target'], commits, doc['inventories']):
        raise Unavailable('bounded_dependency_mismatch')
    from .native_owned_seam_cells import selected_pair
    if doc['selection'] != selected_pair([doc['query']], shape['start_frame'], shape['end_frame']):
        raise Unavailable('core_seam_selection')
    support = SourceSupport.from_dict(doc['support']); contents = [p.content() for p in parts]
    try:inventory_hash = sha(doc['inventories'])
    except Unavailable as exc:
        if str(exc) == 'serialization_capacity' and getattr(exc, 'capacity_phase', None) is None:exc.capacity_phase = 'retained_inventory'
        raise
    try:
        expected_core = sha({'commits': commits, 'geometry': shape, 'query': doc['query'],
            'target_sha256': doc['target_sha256'], 'inventories_sha256': inventory_hash,
            'dependency_roots': doc['dependency_roots']})
    except Unavailable as exc:
        if str(exc) == 'serialization_capacity' and getattr(exc, 'capacity_phase', None) is None:exc.capacity_phase = 'core_preimage'
        raise
    if (support.source_generation != sha({'schema': 'immutable-source-cell-generation-v1', 'sources': contents})
            or support.plan_sha256 != sha({'schema': 'bounded-cell-mapping-v1', 'parts': contents})
            or support.native_runtime_sha256 != commits[-1]['runtime_sha256']
            or support.core_sha256 != expected_core
            or (support.source_frames, support.core_start_frame, support.core_end_frame,
                support.start_frame, support.end_frame) != (cb, ca, cb, shape['start_frame'], shape['end_frame'])
            or support.pcm16le_sha256 != doc['query']['support_pcm16le_sha256']):
        raise Unavailable('core_seam_support')
    if doc['publication_anchor'] != anchor(commits[-1]) or doc['coverage'] != coverage(doc):
        raise Unavailable('core_seam_publication')
    try:encoded_proof = canonical(doc)
    except Unavailable as exc:
        if str(exc) == 'serialization_capacity' and getattr(exc, 'capacity_phase', None) is None:exc.capacity_phase = 'full_proof'
        raise
    if encoded_proof != proof:raise Unavailable('noncanonical_proof')
    return doc, support, parts


def make_bounded_cell(commits, query, evidence, pcm, target, snapshot):
    from .native_owned_seam_cells import selected_pair
    shape = select_support(target, commits, snapshot, query['query_start_frame'], query['query_end_frame'])
    ca, cb = bounded_chain(commits); parts = bounded_mapping(commits, shape['start_frame'], shape['end_frame'])
    contents = [p.content() for p in parts]; target_hash = sha(target)
    roots = dependency_roots(target, commits, snapshot)
    try:inventory_hash = sha(snapshot)
    except Unavailable as exc:
        if str(exc) == 'serialization_capacity' and getattr(exc, 'capacity_phase', None) is None:exc.capacity_phase = 'retained_inventory'
        raise
    try:
        core_hash = sha({'commits': commits, 'geometry': shape, 'query': query, 'target_sha256': target_hash,
            'inventories_sha256': inventory_hash, 'dependency_roots': roots})
    except Unavailable as exc:
        if str(exc) == 'serialization_capacity' and getattr(exc, 'capacity_phase', None) is None:exc.capacity_phase = 'core_preimage'
        raise
    support = SourceSupport(sha({'schema': 'immutable-source-cell-generation-v1', 'sources': contents}),
        sha({'schema': 'bounded-cell-mapping-v1', 'parts': contents}), commits[-1]['runtime_sha256'],
        core_hash,
        cb, ca, cb, shape['start_frame'], shape['end_frame'], hashlib.sha256(pcm).hexdigest(), float32_digest(pcm))
    doc = {'schema': BOUNDED_SCHEMA, 'policy': BOUNDED_POLICY_SHA256, 'commits': list(commits),
        'geometry': shape, 'support': support.to_dict(), 'dependencies': [asdict(p) for p in parts],
        'query': query, 'right_evidence': evidence,
        'selection': selected_pair([query], shape['start_frame'], shape['end_frame']),
        'publication_anchor': anchor(commits[-1]), 'selected': True, 'target': target,
        'target_sha256': target_hash, 'inventories': snapshot, 'dependency_roots': roots}
    doc['coverage'] = coverage(doc); p = doc['publication_anchor']
    try:proof = canonical(doc)
    except Unavailable as exc:
        if str(exc) == 'serialization_capacity' and getattr(exc, 'capacity_phase', None) is None:exc.capacity_phase = 'full_proof'
        raise
    return Cell(support, parts, proof, pcm, commits[-1]['scope']['session_id'],
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
    db.execute("CREATE INDEX IF NOT EXISTS native_core_pending_bodies ON native_core_commits(slot) WHERE pending IS NOT NULL")
    db.execute('''CREATE TRIGGER IF NOT EXISTS native_core_cancel AFTER UPDATE OF status ON chunks
        WHEN NEW.status='cancelled' BEGIN
          UPDATE native_core_commits SET state='cancelled',pending=NULL,reason='primary_cancelled'
          WHERE sid=NEW.session_id AND state='pending'; END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS native_core_delete AFTER UPDATE OF deleted ON sessions
        WHEN NEW.deleted=1 BEGIN DELETE FROM native_core_commits WHERE sid=NEW.id; END''')


def retention_counts(db, budget):
    """Bounded read-only occupancy, not a complete cross-store root graph."""
    budget.check()
    present = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_core_commits'").fetchone()
    if present is None:return {'occupied': 0, 'pending': 0, 'pending_bytes': 0}
    from .strict_lifetime import read_limit
    limit = read_limit(db)
    occupied = db.execute('SELECT COUNT(*) FROM native_core_commits').fetchone()[0]
    # Keep the original OR semantics, including inconsistent pending-state/body
    # rows, without scanning all retained historical commits on every sample.
    pending = {}
    for sql in (
        "SELECT slot,length(pending) FROM native_core_commits INDEXED BY native_core_pending WHERE state='pending' ORDER BY slot LIMIT ?",
        "SELECT slot,length(pending) FROM native_core_commits INDEXED BY native_core_pending_bodies WHERE pending IS NOT NULL ORDER BY slot LIMIT ?",
    ):
        for slot_id, size in db.execute(sql, (MAX_PENDING + 1,)):
            pending[slot_id] = 0 if size is None else size
    budget.projection(96 + len(pending)*96)
    if not 0 <= occupied <= limit or len(pending) > MAX_PENDING:
        raise Unavailable('retention_compact_capacity')
    pending_bytes = sum(pending.values())
    if not 0 <= pending_bytes <= MAX_PENDING * PENDING_BYTES:
        raise Unavailable('retention_pending_capacity')
    return {'occupied': occupied, 'pending': len(pending), 'pending_bytes': pending_bytes}


def read_commit(db, slot_id, budget, *, reserve=False):
    row = db.execute('SELECT length(body) AS n FROM native_core_commits WHERE slot=?', (slot_id,)).fetchone()
    if row is None:return None
    if not 1 <= row[0] <= COMPACT_BYTES:raise Unavailable('core_receipt_capacity')
    size = row[0]
    if reserve:budget.projection(size)
    # Length is checked again in the same statement which projects bytes.
    if reserve:
        row = db.execute('SELECT body FROM native_core_commits WHERE slot=? AND length(body)=? AND length(body) BETWEEN 1 AND ?',
                         (slot_id, size, COMPACT_BYTES)).fetchone()
    else:
        row = db.execute('SELECT body FROM native_core_commits WHERE slot=? AND length(body) BETWEEN 1 AND ?',
                         (slot_id, COMPACT_BYTES)).fetchone()
    if row is None:return None
    if not reserve:budget.projection(len(row[0]))
    commit = json.loads(row[0]); validate_commit(commit)
    if canonical(commit, COMPACT_BYTES) != row[0] or slot(commit) != slot_id:raise Unavailable('core_receipt_corrupt')
    return commit


def _gc_guard(owner, op, db, budget, *, pending=None):
    """Primary BEGIN IMMEDIATE is the supported Worker.loop claim fence.

    A claim that won first leaves a running row before current_chunk is set.
    current_chunk covers paused/completed-but-still-publishing work. If an API
    pauses that tiny claim gap, the unchanged Worker interruption check rejects
    it before preparation. Direct process/prepare calls are not this contract.
    """
    from .native_owned_cell_capture import _identity
    budget.check(); owner._generation(op)
    if owner._admission_closed or op.cancelled.is_set() or owner._slot is not op:
        raise Unavailable('compact_gc_admission_closed')
    capture = owner._retention_capture
    if capture is None:raise Unavailable('compact_gc_binding_unavailable')
    if _identity(owner.authority.path) != owner.authority.identity:
        raise Unavailable('compact_gc_primary_changed')
    roots = set(capture.retention_roots())
    if db.execute("SELECT 1 FROM chunks INDEXED BY chunk_queue WHERE status='running' LIMIT 1").fetchone():
        raise Unavailable('compact_gc_producer_active')
    if pending is None:pending = retention_counts(db, budget)['pending']
    else:integer(pending, MAX_PENDING)
    # Cancelled/unreleased copied pins are still roots until exact drainage.
    for pin in tuple(owner._pins.values()):
        dependencies = pin.document.get('dependencies')
        if type(dependencies) is not list or not 1 <= len(dependencies) <= MAX_PARTS:
            raise Unavailable('compact_gc_producer_roots_pending')
        for dependency in dependencies:roots.add(text(dependency['chunk_id']))
        if pin.document.get('schema') == BOUNDED_SCHEMA:
            for dependency in pin.document['dependency_roots']:roots.add(text(dependency['cid']))
    budget.projection(64); budget.check()
    # Global occupancy always counts every row. Pending-proof refusal belongs
    # to each candidate's authority branch; an exclusive-primary domain may
    # prove its own absence without draining unrelated legacy work.
    return frozenset(roots), pending


def payload_retention_roots(owner, db, budget, roots):
    """Read-only payload protection under PayloadFrontier's original locks.

    Unlike compact/receipt GC this never removes a proof, source or logical row.
    Copied pins and pending compact publishers nevertheless remain protected
    until their exact original owners release them.  No actor RPC is made.
    """
    from .native_owned_cell_capture import PayloadFrontierBusy
    from .native_owned_cell_store import MAX_PINS
    budget.check()
    budget.projection(64 + (MAX_PENDING + 1) * 128)
    if db.execute("SELECT 1 FROM chunks INDEXED BY chunk_queue WHERE status='running' LIMIT 1").fetchone():
        raise PayloadFrontierBusy('payload_producer_active')
    if len(owner._pins) > MAX_PINS:raise Unavailable('payload_pin_capacity')
    roots = set(roots)
    for pin in tuple(owner._pins.values()):
        dependencies = pin.document.get('dependencies')
        if type(dependencies) is not list or not 1 <= len(dependencies) <= MAX_PARTS:
            raise Unavailable('payload_roots_invalid')
        for dependency in dependencies:roots.add(text(dependency['chunk_id']))
        if pin.document.get('schema') == BOUNDED_SCHEMA:
            extra = pin.document.get('dependency_roots')
            if type(extra) is not list or len(extra) > MAX_PARTS:raise Unavailable('payload_roots_invalid')
            for dependency in extra:roots.add(text(dependency['cid']))
    pending = db.execute('''SELECT CASE WHEN typeof(cid)='text' AND length(CAST(cid AS BLOB)) BETWEEN 1 AND 128
        THEN cid ELSE NULL END FROM native_core_commits WHERE pending IS NOT NULL OR state='pending' LIMIT ?''',
        (MAX_PENDING + 1,)).fetchall()
    if len(pending) > MAX_PENDING:raise Unavailable('payload_pending_capacity')
    roots.update(text(row[0]) for row in pending)
    if len(roots) > MAX_PINS * MAX_PARTS * 2 + MAX_PENDING + 2:
        raise Unavailable('payload_root_capacity')
    budget.check()
    return frozenset(roots)


def _gc_outbox_root(db, cid, budget):
    from .moss_automatic import retention_root
    if retention_root(db,cid=cid):return True
    row = db.execute('''SELECT 1 FROM recording_work rw
        JOIN recording_batches rb ON rb.id=rw.batch_id
        LEFT JOIN recording_pages rp ON rp.id=rw.page_id
        WHERE rw.chunk_id=? AND ((rw.page_id IS NULL AND rb.state!='queued')
            OR (rw.page_id IS NOT NULL AND COALESCE(rp.released,0)!=1)) LIMIT 1''', (cid,)).fetchone()
    budget.projection(16)
    return row is not None


def _gc_stream_source(db, commit, budget):
    """Return a verified completed stream position, otherwise protect it."""
    p = validate_commit(commit); scope = commit['scope']
    if scope['kind'] != 'stream':return None
    row = db.execute('''SELECT
        c.status='completed' AND s.deleted=0 AND s.processing_mode='legacy'
          AND c.session_id=? AND c.digest=? AND sc.session_id=? AND sc.stream_id=?
          AND ti.session_id=? AND ti.stream_id=? AND ti.position=sc.position
          AND ti.timeline_start=c.timeline_start AND ti.timeline_end=c.timeline_start+c.duration AS same,
        c.next_offset,c.timeline_start,c.duration,sc.position,
        (SELECT first.timeline_start FROM stream_chunks first_sc
         JOIN chunks first ON first.id=first_sc.chunk_id
         WHERE first_sc.session_id=sc.session_id AND first_sc.stream_id=sc.stream_id
         ORDER BY first_sc.position LIMIT 1) AS origin
        FROM chunks c JOIN sessions s ON s.id=c.session_id
        JOIN stream_chunks sc ON sc.chunk_id=c.id JOIN stream_time_index ti ON ti.chunk_id=c.id
        WHERE c.id=?''', (p.session_id, p.source_sha256, p.session_id, scope['key'], p.session_id, scope['key'], p.chunk_id)).fetchone()
    budget.projection(192)
    if row is None or not row[0] or row[5] is None:return None
    origin = float.fromhex(scope['origin'])
    if (float(row[5]) != origin or exact_frame(row[1]) != exact_frame(row[3])
            or exact_frame(row[3]) != p.source_frames or p.source_end > exact_frame(row[1])
            or exact_frame(row[2], origin=origin) != p.logical_start - p.source_start):
        return None
    integer(row[4], (1 << 63) - 1)
    if _gc_outbox_root(db, p.chunk_id, budget):return None
    return row[4]


def _gc_exact_row(db, slot_id, commit, raw, budget):
    p = commit['part']
    row = db.execute('''SELECT sid=? AND cid=? AND scope_key=? AND logical_end=?
        AND pending IS NULL AND state!='cancelled' AND body=?
        FROM native_core_commits WHERE slot=?''',
        (p['session_id'], p['chunk_id'], scope_key(commit['scope']), p['logical_end'], raw, slot_id)).fetchone()
    budget.projection(16)
    return row is not None and bool(row[0])


def _gc_receipt(owner, commit):
    p = commit['part']; receipt = owner.receipt(p['chunk_id'])
    return (receipt is not None and receipt.session_id == p['session_id']
            and receipt.receipt_id == p['receipt_sha256'] and receipt.digest == p['source_sha256']
            and receipt.frames == p['source_frames'] and receipt.stream_id == commit['scope']['key']
            and exact_frame(receipt.timeline_start, origin=float.fromhex(commit['scope']['origin']))
                == p['logical_start'] - p['source_start'])


def _gc_primary_affected_domains(db, budget):
    """One capped dirty-domain census, independent of candidate count.

    A compact's declared SID is not canonical authority. Protect both it and
    its chunk's actual marked session, including a wrong-SID pending proof on
    another source in the candidate's recording. The compact cap bounds the
    outer scan; indexed chunk/batch lookups and the marked-batch cap bound the
    joins and distinct projection. Never project compact/proof bodies here.
    """
    from .recording_overlap import MAX_BATCHES
    budget.check()
    # The exact-validated batch DDL declares id PRIMARY KEY first and
    # session_id UNIQUE second. Force that session index and a two-key IN
    # probe: never multiply the compact scan by all retained batches. If the
    # required index is unavailable, SQLite refuses rather than scanning.
    rows = db.execute('''SELECT DISTINCT CASE
        WHEN typeof(b.session_id)='text' AND length(CAST(b.session_id AS BLOB)) BETWEEN 1 AND 128
          THEN b.session_id ELSE NULL END AS sid
        FROM native_core_commits n LEFT JOIN chunks c ON c.id=n.cid
        CROSS JOIN recording_batches b INDEXED BY sqlite_autoindex_recording_batches_2
        WHERE (n.pending IS NOT NULL OR n.state IN ('pending','published')
          OR n.recording_batch_id IS NOT NULL OR n.commit_ordinal IS NOT NULL
          OR n.retention_store_key IS NOT NULL)
          AND b.primary_cache_policy IS NOT NULL
          AND b.session_id IN (n.sid,c.session_id)
        LIMIT ?''', (MAX_BATCHES + 1,)).fetchall()
    budget.projection(len(rows) * 128)
    if len(rows) > MAX_BATCHES:raise Unavailable('compact_gc_roots_changed')
    return frozenset(text(row[0]) for row in rows)


def _gc_primary_domains_current(db, domains, budget):
    if not domains:return True
    affected = _gc_primary_affected_domains(db, budget)
    return affected == domains['affected'] and not affected.intersection(domains['qualified'])


def _gc_primary_authority(owner, db, sid, cid, budget, domains):
    """Positive marked authority plus exact outbox and live source identity."""
    from .recording_overlap import primary_cache_qualified, receipt_gc_source
    primary = primary_cache_qualified(owner.authority.store, db, sid, cid,
                                      owner.retention_store_key, budget)
    if primary is None:return None
    if not domains:
        domains.update(affected=_gc_primary_affected_domains(db, budget), qualified=set())
    if sid in domains['affected']:return None
    if sid not in domains['qualified']:
        if len(domains['qualified']) >= COMPACT_GC_BATCH:raise Unavailable('operation_work_capacity')
        domains['qualified'].add(sid)
    receipt = owner.receipt(cid)
    if receipt is None or receipt.session_id != sid or receipt.chunk_id != cid:return None
    source = receipt_gc_source(db, receipt, budget)
    if source is None or source['batch_id'] != primary['batch_id']:return None
    # A replaced/revised file is revocation, never ordinary cache absence.
    # receipt_valid checks descriptor/path continuity and rereads its mapping.
    if not owner.authority.receipt_valid(receipt, budget):return None
    return primary


def _gc_store_eligible(owner, db, slot_id, budget, *, sid, cid, primary=None,
                       pending=0, receipts=False):
    from .moss_automatic import retention_eligible, has_bounded_schema
    from .recording_overlap import primary_cache_marked
    if primary is not None:
        # primary_cache_qualified already checked the complete versioned guard
        # contract and indexed relevant-domain MOSS/pending absence. Callers
        # freeze its exact result and repeat that lookup before deletion.
        return True
    if primary_cache_marked(db, sid, cid, store=owner.authority.store, budget=budget):return False
    if pending:return False
    if receipts and owner._retention_capture.core_seams.work_pending:return False
    if not getattr(owner, 'bounded_core_enabled', False) and not has_bounded_schema(db):return True
    budget.projection(384)
    eligible = retention_eligible(db, owner.retention_store_key, slot=slot_id, cid=cid)
    return eligible


def collect_compacts(owner, op, budget, result=None):
    """One primary-only transaction; never writes through PrimaryAuthority.

    Only completed stream sources behind a verified later completed source are
    considered. All receipts/cells/results stay intact. The owner slot excludes
    new cell/pin admission while primary BEGIN IMMEDIATE excludes new claims.
    No write/COMMIT is retried, and no absent ID alone is attributed to this GC.
    """
    from .native_owned_cell_capture import _identity
    if result is None:result = {'scanned': 0, 'retired': 0, 'protected': 0, 'body_bytes': 0}
    result.update(status='no_eligible_compacts', reason='compact_gc_page_protected')
    if _identity(owner.authority.path) != owner.authority.identity:
        raise Unavailable('compact_gc_primary_changed')
    cursor = owner._compact_gc_cursor; plans = []; domains = {}
    # Store.db owns rollback/close. Override its ordinary 15-second connection
    # busy timeout BEFORE any transaction or SQL work; never retry this write.
    with owner.authority.store.db() as db:
        db.execute('PRAGMA busy_timeout=5'); db.set_progress_handler(budget.progress, 100)
        try:
            budget.check(); db.execute('BEGIN IMMEDIATE')
            guard = _gc_guard(owner, op, db, budget); roots, pending = guard
            # The primary write fence excludes all competing claims/writers.
            # Reuse this operation's one complete occupancy census: our only
            # primary mutation deletes exact nonpending rows. Later guards
            # still resample held roots, generation, binding and active work.
            if owner._compact_gc_unknown is not None:
                absent = 0
                for slot_id, raw in owner._compact_gc_unknown:
                    row = db.execute('SELECT body=? FROM native_core_commits WHERE slot=?', (raw, slot_id)).fetchone()
                    budget.projection(16)
                    if row is None:absent += 1
                    elif not row[0]:raise Unavailable('compact_gc_unknown_changed')
                _gc_guard(owner, op, db, budget, pending=pending); budget.check()
                db.execute('ROLLBACK')
                result.update(status='reconciled', reason='absent_after_unknown_commit' if absent
                              else 'present_after_unknown_commit')
                return result
            rows = db.execute('''SELECT slot FROM native_core_commits WHERE slot>?
                ORDER BY slot LIMIT ?''', (cursor, COMPACT_GC_BATCH)).fetchall()
            budget.projection(len(rows) * 64)
            if not rows:cursor = ''
            for item in rows:
                budget.check(); slot_id = digest(item[0])
                commit = read_commit(db, slot_id, budget)
                if commit is None:raise Unavailable('compact_gc_roots_changed')
                raw = canonical(commit, COMPACT_BYTES); result['body_bytes'] += len(raw)
                result['scanned'] += 1; cursor = slot_id
                p = commit['part']; cid = p['chunk_id']
                eligible = _gc_exact_row(db, slot_id, commit, raw, budget)
                if cid in roots:eligible = False
                if owner._db.execute('SELECT 1 FROM dependencies WHERE cid=? LIMIT 1', (cid,)).fetchone():
                    eligible = False
                if owner._db.execute('SELECT 1 FROM cells WHERE cid=? LIMIT 1', (cid,)).fetchone():
                    eligible = False
                budget.projection(32)
                if not _gc_receipt(owner, commit):eligible = False
                if not eligible:
                    result['protected'] += 1; continue
                primary = _gc_primary_authority(owner, db, p['session_id'], cid, budget, domains)
                if not _gc_store_eligible(owner, db, slot_id, budget, sid=p['session_id'],
                        cid=cid, primary=primary, pending=pending):eligible = False
                position = _gc_stream_source(db, commit, budget) if eligible else None
                if position is None:
                    result['protected'] += 1; continue
                key = scope_key(commit['scope'])
                latest = db.execute('''SELECT slot,logical_end FROM native_core_commits
                    WHERE sid=? AND scope_key=? ORDER BY logical_end DESC LIMIT 2''',
                    (p['session_id'], key)).fetchall()
                budget.projection(160)
                if (not latest or latest[0][0] == slot_id or latest[0][1] <= p['logical_end']
                        or len(latest) == 2 and latest[0][1] == latest[1][1]):
                    result['protected'] += 1; continue
                frontier_id = digest(latest[0][0]); frontier = read_commit(db, frontier_id, budget)
                if frontier is None:raise Unavailable('compact_gc_roots_changed')
                frontier_raw = canonical(frontier, COMPACT_BYTES)
                result['body_bytes'] += len(frontier_raw)
                if result['body_bytes'] > COMPACT_GC_BODY_BYTES:raise Unavailable('operation_work_capacity')
                frontier_position = _gc_stream_source(db, frontier, budget)
                fp = frontier['part']
                if (frontier_position is None or frontier_position <= position or fp['chunk_id'] == cid
                        or not _gc_receipt(owner, frontier)
                        or fp['session_id'] != p['session_id'] or scope_key(frontier['scope']) != key
                        or fp['logical_end'] <= p['logical_end']
                        or not _gc_exact_row(db, frontier_id, frontier, frontier_raw, budget)):
                    result['protected'] += 1; continue
                frontier_primary = (_gc_primary_authority(owner, db, fp['session_id'], fp['chunk_id'], budget, domains)
                                    if primary is not None else None)
                if primary is not None and (frontier_primary is None or any(
                        primary[k] != frontier_primary[k]
                        for k in ('batch_id', 'session_id', 'store_key', 'schema_version'))):
                    result['protected'] += 1; continue
                plans.append((slot_id, raw, commit, frontier_id, frontier_raw, frontier,
                              primary, frontier_primary))
            if _gc_guard(owner, op, db, budget, pending=pending) != guard:
                raise Unavailable('compact_gc_roots_changed')
            # No proof bytes cross the boundary again. Every exact body/frontier
            # and root predicate is rechecked under the same claim/owner fence.
            for slot_id, raw, commit, frontier_id, frontier_raw, frontier, primary, frontier_primary in plans:
                cid = commit['part']['chunk_id']
                if (not _gc_exact_row(db, slot_id, commit, raw, budget)
                        or not _gc_store_eligible(owner, db, slot_id, budget,
                            sid=commit['part']['session_id'], cid=cid, primary=primary, pending=pending)
                        or primary is not None and (
                            _gc_primary_authority(owner, db, commit['part']['session_id'], cid, budget, domains) != primary
                            or _gc_primary_authority(owner, db, frontier['part']['session_id'],
                                frontier['part']['chunk_id'], budget, domains) != frontier_primary)
                        or not _gc_exact_row(db, frontier_id, frontier, frontier_raw, budget)
                        or _gc_stream_source(db, commit, budget) is None
                        or _gc_stream_source(db, frontier, budget) is None
                        or not _gc_receipt(owner, commit) or not _gc_receipt(owner, frontier)
                        or cid in roots or _gc_outbox_root(db, cid, budget)
                        or owner._db.execute('SELECT 1 FROM dependencies WHERE cid=? LIMIT 1', (cid,)).fetchone()
                        or owner._db.execute('SELECT 1 FROM cells WHERE cid=? LIMIT 1', (cid,)).fetchone()):
                    raise Unavailable('compact_gc_roots_changed')
                budget.projection(32)
            if plans:
                if not _gc_primary_domains_current(db, domains, budget):
                    raise Unavailable('compact_gc_roots_changed')
                owner.retention_changed()
                for slot_id, raw, *_ in plans:
                    if db.execute('DELETE FROM native_core_commits WHERE slot=? AND body=? AND pending IS NULL',
                                  (slot_id, raw)).rowcount != 1:
                        raise Unavailable('compact_gc_roots_changed')
                if _gc_guard(owner, op, db, budget, pending=pending) != guard:
                    raise Unavailable('compact_gc_roots_changed')
                budget.check()
                owner._compact_gc_unknown = tuple((item[0], item[1]) for item in plans)
                db.execute('COMMIT')
                result.update(status='retired', reason='compact_gc_retired', retired=len(plans))
            else:db.execute('ROLLBACK')
            budget.check()
            if _identity(owner.authority.path) != owner.authority.identity:
                raise Unavailable('compact_gc_primary_changed')
        finally:
            # Rollback is owned here/Store.db, with progress cancellation removed.
            db.set_progress_handler(None, 0)
    owner._compact_gc_cursor = cursor
    return result


def transaction(db, chunk, offset, end, result, *, strict=False, automatic=False):
    """Called inside the successful primary transaction, before its UPDATE.

    Optional capacity/errors roll back only this savepoint; primary text survives.
    Conflicting slot evidence never replaces the first immutable commitment.
    """
    packet = result.get('_native_core_seam')
    if packet is None:
        if strict:raise Unavailable('automatic_core_commit_required')
        return None
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
        from .strict_lifetime import read_limit
        if db.execute('SELECT COUNT(*) FROM native_core_commits').fetchone()[0] >= read_limit(db):
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
                if automatic:
                    # Exact seam proof belongs to the automatic intent table;
                    # do not duplicate it in the four-row manual seam outbox.
                    proof=None; state='none'; reason='automatic_intent'
                elif db.execute("SELECT COUNT(*) FROM native_core_commits WHERE pending IS NOT NULL").fetchone()[0] >= MAX_PENDING:
                    reason = 'core_pending_row_capacity'; proof = None; state = 'gap'
                else:state = 'pending'; reason = 'core_pending'
        db.execute('''INSERT INTO native_core_commits
            (slot,sid,cid,scope_key,logical_end,body,pending,state,reason) VALUES(?,?,?,?,?,?,?,?,?)''',
            (slot(commit), chunk['session_id'], chunk['id'], scope_key(commit['scope']), p.logical_end,
             raw, proof, state, reason[:80]))
        if packet.get('_bounded_context') is not None:
            from .moss_automatic import commit_ordinal
            commit_ordinal(db, packet, chunk)
        return reason
    except (Unavailable, ValueError, TypeError, KeyError) as exc:
        db.execute('ROLLBACK TO native_core_optional')
        if strict:raise
        return str(exc) if isinstance(exc, Unavailable) else 'core_optional_transaction_unavailable'
    finally:db.execute('RELEASE native_core_optional')


def current(authority, doc, budget, *, automatic_key=None):
    """Independent of mutable chunk.metadata, and fail-closed after cancellation."""
    doc, _, parts = validate_document(canonical(doc)); owner = authority.owner
    generation = owner._epoch
    commits = doc['commits']
    if doc['schema'] == BOUNDED_SCHEMA:
        from .moss_automatic import qualify_bounded_document
        if not authority._read(budget, 'automatic_intent', lambda db:
                qualify_bounded_document(db, doc, budget, owner.retention_store_key,
                                         automatic_key=automatic_key)):
            return False
        _, originals = validate_target(doc['target'])
        roots = {slot(c): c for c in originals}
        for c in commits:
            if slot(c) in roots and roots[slot(c)] != c:return False
            roots[slot(c)] = c
        commits = sorted(roots.values(), key=lambda c: c['part']['logical_start'])
        bounded_chain(commits)
        if not authority.bounded_inventory_current(doc['inventories'], budget):return False
    for commit in commits:
        p = validate_commit(commit)
        def observed_commit(db):
            observed = read_commit(db, slot(commit), budget, reserve=doc['schema'] == BOUNDED_SCHEMA)
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
    publication = doc['commits'][-1] if doc['schema'] == BOUNDED_SCHEMA else doc['commits'][1]
    right = authority.row(publication['part']['chunk_id'], budget)
    if right is None:return False
    observed = authority.mapping(right, float.fromhex(publication['scope']['origin']),
                                 doc['geometry']['start_frame'], doc['geometry']['end_frame'], budget)
    return observed == parts and generation is owner._epoch


class CoreSeams:
    """One bounded maintenance cursor/flight, sharing the existing owner slot."""
    def __init__(self, capture):
        self.capture = capture; self.run = uuid.uuid4().hex
        self.generation = capture.owner._epoch; self.flight = None; self.work_pending = True
        self.next_attempt = 0.; self.revocation_done = False
        self.last_reason = 'not_observed'

    def prepare(self, chunk, native, offset, end, result, coordinator, stop, *, required=False):
        c = self.capture
        if native is None or c.scope is None or not c.owner.health()['ready']:
            if required:raise Unavailable('automatic_core_prepare_unavailable')
            return
        if c.owner._epoch is not self.generation:
            self.last_reason = 'core_owner_revoked'
            if required:raise Unavailable('core_owner_revoked')
            return
        from .native_owned_cell_store import Budget
        bounded = result.get('_native_bounded_support')
        budget = bounded['budget'] if bounded is not None else Budget()
        try:
            # Opt in only this optional bounded discovery segment. The scope
            # ends before bounded_prepare and any native TURNS opportunity.
            with (budget.planning_phase() if bounded is not None else budget.phase()), (
                    c.authority.list_read_scope(budget, bounded_planning=True)
                    if bounded is not None else nullcontext()):
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
                if getattr(c, 'primary_only', False):
                    packet['reason'] = 'primary_observation_compact'
                    self.last_reason = packet['reason']
                    return
                def previous(db):
                    rows = db.execute('SELECT slot FROM native_core_commits WHERE sid=? AND scope_key=? AND logical_end=? LIMIT 2',
                        (chunk['session_id'], scope_key(scope), a)).fetchall()
                    if len(rows) > 1:raise Unavailable('core_left_ambiguous')
                    return read_commit(db, rows[0][0], budget, reserve=bounded is not None) if rows else None
                left = c.authority._read(budget, 'core_previous', previous)
            if bounded is not None:
                # The existing bounded TURNS opportunity runs outside optional
                # CPU/SQL phases, just as it does on the legacy prepare path.
                c.bounded_prepare(chunk, native, offset, end, result, coordinator, stop,
                    commit, left, ordinary_turns, observed, budget)
                return
            with budget.phase():
                if left is None:
                    if required:
                        if a!=0:raise Unavailable('automatic_previous_compact_missing')
                        packet['reason']='core_first_commit'; self.last_reason='core_first_commit'
                    return
                schema = (RESTART_SCHEMA if left['scope']['run'] != scope['run'] else
                          RESET_SCHEMA if left['scope']['index'] != scope['index'] else SCHEMA)
                shape = geometry([left, commit], schema); lo, hi = shape['start_frame'], shape['end_frame']
                if schema == SCHEMA and not observed['epoch_index'] * 300 * RATE <= lo < hi <= (observed['epoch_index'] + 1) * 300 * RATE:
                    raise Unavailable('core_seam_reset_unsupported')
                if schema in (RESET_SCHEMA, RESTART_SCHEMA) and observed['replay_start'] > lo:
                    raise Unavailable('core_restart_replay_missing' if schema == RESTART_SCHEMA
                                      else 'core_reset_replay_missing')
                # Full future half was pushed/processed by normal scheduling already.
                if hi > observed['replay_start'] + observed['state']['processed']['frame']:
                    raise Unavailable('core_seam_future_unprocessed')
                pcm = c.witness.read(lo, hi)
                expected = mapping([left, commit], shape)
                if pcm != c.authority.original_pcm(expected, budget):raise Unavailable('core_seam_source_pcm_mismatch')
            qa = max(c.origin_frame + round(native['extract_start'] * RATE), observed['replay_start'])
            qb = c.origin_frame + exact_frame(native['extract_end'])
            turns, proof = ordinary_turns, observed
            if schema == RESTART_SCHEMA or not qa <= lo < hi <= qb:
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
                if schema == RESTART_SCHEMA:
                    query['capture_run'] = self.run
                    query['turns'] = [{**t, 'label': self.run + ':' + t['label']} for t in query['turns']]
                cell = make_cell([left, commit], query, evidence, pcm, schema)
                packet['proof'] = cell.proof; packet['reason'] = 'core_pending'; self.work_pending = True
                self.last_reason = 'core_prepared'
        except Exception as exc:
            from .native_owned_cell_store import _diagnostic_failure
            _diagnostic_failure('core_seam_prepare', exc)
            reason = str(exc) if isinstance(exc, Unavailable) else 'core_prepare_unavailable'
            if '_native_core_seam' in result:result['_native_core_seam']['reason'] = reason
            self.last_reason = reason
            if bounded is not None:
                if isinstance(exc, sqlite3.Error):raise
                if isinstance(exc, (Unavailable, ValueError, TypeError, KeyError)):
                    # A discovery refusal owns no new target. Already staged
                    # origin certificates stay available to the transaction;
                    # only provisional PCM/final plans are discarded here.
                    bounded['reason'] = reason; bounded['plans'] = []
                    result['_native_automatic_cells'] = ()
                    return
            if required and reason not in ('no_same_response_native_overlap','core_reset_short_side'):
                raise
            # Only a positively checked nonoverlap or the policy's explicitly
            # unsupported short reset side is benign in automatic mode.

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


def collect_receipts(owner, op, budget, result=None):
    """Delete only exact orphan receipts, under owner and primary claim fences.

    The primary connection is read-only apart from BEGIN IMMEDIATE/ROLLBACK;
    only sources in the optional store are mutated. Both connections share the
    original two-second/SQL-step/projection budget. The compact scan is one
    bounded current-table membership check, not one history scan per source.
    """
    from .native_owned_cell_store import Receipt, RECEIPT_BYTES
    from .native_owned_cell_capture import _identity
    from .recording_overlap import (receipt_gc_source, receipt_gc_prefix, receipt_gc_pending_roots,
        RECEIPT_GC_PREFIX_ROWS, RECEIPT_GC_PREFIX_STREAMS)
    if result is None:result={'scanned':0,'retired':0,'protected':0,'body_bytes':0,'prefix_rows':0}
    result.update(status='no_eligible_receipts',reason='receipt_gc_page_protected')
    if owner._compact_gc_unknown is not None:raise Unavailable('receipt_gc_compact_pending')
    if owner._receipt_gc_prefix_generation is not owner._epoch:
        owner._receipt_gc_prefixes.clear();owner._receipt_gc_prefix_generation=owner._epoch
    cursor=owner._receipt_gc_cursor;plans=[];domains={}
    with owner.authority.store.db() as db:
        db.execute('PRAGMA busy_timeout=5');db.set_progress_handler(budget.progress,100)
        try:
            budget.check();db.execute('BEGIN IMMEDIATE')
            guard=_gc_guard(owner,op,db,budget);roots,pending=guard
            # This primary BEGIN IMMEDIATE owns a stable occupancy census;
            # receipt retirement mutates only the separately fenced sidecar.
            capture=owner._retention_capture
            if (not owner.receipt_gc_enabled or not capture._retention_worker.settings.native_owned_receipt_gc_enabled):
                raise Unavailable('receipt_gc_binding_unavailable')
            if capture.witness.pending is not None:
                raise Unavailable('compact_gc_pending_proof')
            if owner._receipt_gc_unknown is not None:
                # No cache authority or successful-deletion attribution until
                # every exact planned ID has been reread on the owner thread.
                observed=[]
                for cid,raw in owner._receipt_gc_unknown:
                    receipt=Receipt.decode(raw)
                    row=owner._db.execute('''SELECT sid=? AND digest=? AND receipt=?
                        FROM sources WHERE cid=?''',(receipt.session_id,receipt.digest,raw,cid)).fetchone()
                    budget.projection(16)
                    if row is not None and not row[0]:raise Unavailable('receipt_gc_unknown_changed')
                    observed.append((cid,receipt if row is not None else None))
                if _gc_guard(owner,op,db,budget,pending=pending) != guard:raise Unavailable('receipt_gc_roots_changed')
                budget.check();db.execute('ROLLBACK')
                for cid,receipt in observed:
                    if receipt is None:owner._receipts.pop(cid,None)
                    else:owner._receipts[cid]=receipt
                result.update(status='reconciled',reason='receipt_gc_exact_reread')
                return result
            rows=owner._db.execute('SELECT cid,length(receipt) FROM sources WHERE cid>? ORDER BY cid LIMIT ?',
                (cursor,COMPACT_GC_BATCH)).fetchall();budget.projection(len(rows)*160)
            if not rows:cursor=''
            cids=[text(row[0]) for row in rows]
            # No cid index exists in the compact table. Its existing MAX_COMPACTS
            # cap plus the shared VM-step budget bounds this single scan. An
            # exhausted budget defers rather than weakening any root predicate.
            compact_roots=set()
            if cids:
                marks=','.join('?' for _ in cids)
                compact_roots={row[0] for row in db.execute(
                    'SELECT DISTINCT cid FROM native_core_commits WHERE cid IN ('+marks+')',cids)}
                budget.projection(len(compact_roots)*128)
            seen={};pending_roots={}
            for cid,n in rows:
                cursor=cid;result['scanned']+=1;budget.check()
                if not 1 <= n <= RECEIPT_BYTES:raise Unavailable('receipt_gc_roots_changed')
                raw=owner._db.execute('SELECT receipt FROM sources WHERE cid=? AND length(receipt) BETWEEN 1 AND ?',
                    (cid,RECEIPT_BYTES)).fetchone()
                if raw is None:raise Unavailable('receipt_gc_roots_changed')
                raw=raw[0];budget.projection(len(raw));result['body_bytes']+=len(raw)
                receipt=Receipt.decode(raw)
                exact=owner._db.execute('SELECT sid=? AND digest=? FROM sources WHERE cid=?',
                    (receipt.session_id,receipt.digest,cid)).fetchone();budget.projection(16)
                if receipt.chunk_id != cid or not exact[0] or owner.receipt(cid) != receipt:
                    raise Unavailable('receipt_gc_roots_changed')
                source=receipt_gc_source(db,receipt,budget)
                if source is None:
                    result['protected']+=1;continue
                primary=_gc_primary_authority(owner,db,receipt.session_id,cid,budget,domains)
                if not _gc_store_eligible(owner,db,None,budget,sid=receipt.session_id,
                        cid=cid,primary=primary,pending=pending,receipts=True):
                    result['protected']+=1;continue
                key=(receipt.session_id,source['batch_id'])
                if key not in seen:
                    pending_roots[key]=receipt_gc_pending_roots(db,source['batch_id'],cids,budget)
                    prior=owner._receipt_gc_prefixes.get(key)
                    fence=(None if primary is None else tuple(primary[k]
                        for k in ('batch_id','session_id','store_key','schema_version')))
                    if prior is not None and prior.get('_primary_cache_fence')!=fence:prior=None
                    prefix,count=receipt_gc_prefix(db,receipt.session_id,source,prior,budget,
                        RECEIPT_GC_PREFIX_ROWS-result['prefix_rows'],qualified_primary=primary is not None)
                    prefix={**prefix,'_primary_cache_fence':fence}
                    result['prefix_rows']+=count
                    if key not in owner._receipt_gc_prefixes and len(owner._receipt_gc_prefixes)>=RECEIPT_GC_PREFIX_STREAMS:
                        owner._receipt_gc_prefixes.pop(next(iter(owner._receipt_gc_prefixes)))
                    owner._receipt_gc_prefixes[key]=prefix;seen[key]=prefix
                prefix=seen[key]
                future=prefix['frame']-prefix['origin']
                protected_start=capture.receipt_retention_start(receipt.session_id,receipt.stream_id,future)
                end=source['capture_start_frame']-source['origin']+receipt.frames
                aliases,intervals=pending_roots[key]
                source_start=source['capture_start_frame']
                pending_alias=cid in aliases or any(max(source_start,a)<min(source_start+receipt.frames,b) for a,b in intervals)
                eligible=(source['position']<prefix['position'] and end<=protected_start
                    and cid not in roots and cid not in compact_roots and not pending_alias
                    and not _gc_outbox_root(db,cid,budget))
                if owner._db.execute('SELECT 1 FROM dependencies WHERE cid=? LIMIT 1',(cid,)).fetchone():eligible=False
                # Supported insertion always records normalized dependencies.
                # Anchor fallback also keeps malformed/metadata-only cells. It
                # may scan <=MAX_CELLS entries (cid is not indexed), charged by
                # the same VM budget; we never parse/copy arbitrary proof blobs.
                if owner._db.execute('SELECT 1 FROM cells WHERE cid=? LIMIT 1',(cid,)).fetchone():eligible=False
                budget.projection(32)
                if not eligible:
                    result['protected']+=1;continue
                plans.append((cid,raw,receipt,source,protected_start,future,primary))
            if _gc_guard(owner,op,db,budget,pending=pending)!=guard:raise Unavailable('receipt_gc_roots_changed')
            if plans:
                owner.retention_changed()
                with owner._transaction(budget):
                    if not _gc_primary_domains_current(db,domains,budget):
                        raise Unavailable('receipt_gc_roots_changed')
                    for cid,raw,receipt,source,start,future,primary in plans:
                        if (receipt_gc_source(db,receipt,budget)!=source
                                or not _gc_store_eligible(owner,db,None,budget,sid=receipt.session_id,
                                    cid=cid,primary=primary,pending=pending,receipts=True)
                                or primary is not None and _gc_primary_authority(
                                    owner,db,receipt.session_id,cid,budget,domains)!=primary
                                or capture.receipt_retention_start(receipt.session_id,receipt.stream_id,future)!=start
                                or cid in roots or _gc_outbox_root(db,cid,budget)
                                or owner._db.execute('SELECT 1 FROM dependencies WHERE cid=? LIMIT 1',(cid,)).fetchone()
                                or owner._db.execute('SELECT 1 FROM cells WHERE cid=? LIMIT 1',(cid,)).fetchone()):
                            raise Unavailable('receipt_gc_roots_changed')
                        budget.projection(32)
                        if owner._db.execute('DELETE FROM sources WHERE cid=? AND sid=? AND digest=? AND receipt=?',
                            (cid,receipt.session_id,receipt.digest,raw)).rowcount!=1:
                            raise Unavailable('receipt_gc_roots_changed')
                    if _gc_guard(owner,op,db,budget,pending=pending)!=guard:
                        raise Unavailable('receipt_gc_roots_changed')
                    budget.check()
                    # Install suppression BEFORE the possibly successful COMMIT.
                    # _transaction never retries writes and owns rollback.
                    owner._receipt_gc_unknown=tuple((cid,raw) for cid,raw,*_ in plans)
                result.update(status='retired',reason='receipt_gc_retired',retired=len(plans))
            db.execute('ROLLBACK');budget.check()
            if _identity(owner.authority.path)!=owner.authority.identity:raise Unavailable('compact_gc_primary_changed')
        finally:db.set_progress_handler(None,0)
    owner._receipt_gc_cursor=cursor
    return result
