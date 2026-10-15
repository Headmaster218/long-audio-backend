"""Opt-in two-commit alternative evidence. Pure, bounded, no I/O or inference.

The support core is a TWO-COMMITTED-CORE ENVELOPE; publication uses B's
original primary commitment. A proof is evidence, never live authorization.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import math
import re

from .native_lexical_alternatives import SourceSupport
from .native_owned_cells import (Unavailable, SourceSpan, Cell, RATE, CORE_FRAMES,
    PROOF_BYTES, MAX_TURNS, canonical, sha, integer, text, digest, exact_frame,
    float32_digest, validate_mapping, POLICY_SHA256 as V1_POLICY_SHA256)

SCHEMA = 'native-owned-seam-composite-v2'
SIDE_FRAMES = 160000
TAIL_BYTES = 768 * 1024
WORKING_BYTES = 4 * 1024 * 1024
POLICY = {'schema': 'native-owned-seam-composite-policy-v2', 'rate': RATE,
    'max_side_frames': SIDE_FRAMES, 'max_core_envelope_frames': CORE_FRAMES,
    'required_sources': 2, 'required_commits': 2, 'required_native_witnesses': 2,
    'max_turn_records': MAX_TURNS, 'max_proof_bytes': PROOF_BYTES,
    'required_complete_uploads': True, 'geometry': 'adjacent-terminal-first-core-seam-context',
    'selection': 'positive-half-open-distinct-epoch-local-label-overlap',
    'coverage': 'overlapping-diagnostic-only', 'eos': 'observed-native-finish-only',
    'retention': 'first-selected-no-automatic-eviction', 'regeneration_execution': False}
POLICY_SHA256 = sha(POLICY)
STORE_POLICY = {'schema': 'native-owned-cell-store-policy-v2',
                'accepted_cell_policies': sorted([V1_POLICY_SHA256, POLICY_SHA256])}
STORE_POLICY_SHA256 = sha(STORE_POLICY)


def exact(value, keys, reason='seam_proof_shape'):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise Unavailable(reason)
    return value


def pair(value):
    if type(value) is not list or len(value) != 2:
        raise Unavailable('seam_pair_shape')
    a, b = value
    integer(a); integer(b)
    if not a < b:raise Unavailable('seam_interval_geometry')
    return a, b


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise Unavailable('seam_finite_number_required')
    return value


def is_composite(doc):
    from .native_owned_core_seams import is_core
    return type(doc) is dict and (doc.get('schema') == SCHEMA or is_core(doc))


def anchor(doc):
    return doc['publication_anchor'] if is_composite(doc) else doc['commit']


def coverage(doc):
    return doc['coverage'] if is_composite(doc) else None


def ownership(doc):
    """Stable coordinate ownership deliberately excludes all evidence hashes."""
    from .native_owned_core_seams import is_core, ownership as core_ownership
    if is_core(doc):return core_ownership(doc)
    scope = doc['scope']
    return sha({'schema': 'two-commit-seam-ownership-v2',
        'session_id': scope['session_id'], 'stream_id': scope['stream_id'],
        'epoch_id': scope['epoch_id'],
        'commits': [[c['chunk_id'], c['local_start_frame'], c['local_end_frame']]
                    for c in doc['commits']],
        'geometry': doc['geometry'], 'policy': POLICY_SHA256})


def geometry(a, seam, b):
    integer(a); integer(seam); integer(b)
    if not a < seam < b or b - a > CORE_FRAMES:
        raise Unavailable('seam_two_core_geometry')
    left, right = min(SIDE_FRAMES, seam - a), min(SIDE_FRAMES, b - seam)
    return {'seam_frame': seam, 'left_frames': left, 'right_frames': right,
            'start_frame': seam - left, 'end_frame': seam + right}


def validate_scope(scope):
    exact(scope, 'session_id stream_id origin_seconds_hex epoch_id epoch_index reset_generation')
    text(scope['session_id']); text(scope['stream_id'])
    value = scope['origin_seconds_hex']
    if type(value) is not str or len(value) > 32 or not value.isascii():
        raise Unavailable('seam_origin_shape')
    try:origin = float.fromhex(value)
    except ValueError as exc:raise Unavailable('seam_origin_shape') from exc
    number(origin)
    if origin.hex() != value:raise Unavailable('seam_origin_noncanonical')
    if type(scope['epoch_id']) is not str or re.fullmatch('[a-f0-9]{32}', scope['epoch_id']) is None:
        raise Unavailable('native_epoch_id')
    integer(scope['epoch_index']); integer(scope['reset_generation'], (1 << 31) - 1, 1)
    return origin


def validate_commit(commit, scope):
    exact(commit, 'chunk_id source_origin_frame local_start_frame local_end_frame logical_start_frame logical_end_frame evidence evidence_sha256')
    text(commit['chunk_id']); digest(commit['evidence_sha256'])
    for key in ('source_origin_frame', 'local_start_frame', 'local_end_frame', 'logical_start_frame', 'logical_end_frame'):
        integer(commit[key])
    start, end = commit['local_start_frame'], commit['local_end_frame']
    origin = commit['source_origin_frame']
    if (not 0 < end - start <= CORE_FRAMES
            or commit['logical_start_frame'] != origin + start
            or commit['logical_end_frame'] != origin + end):
        raise Unavailable('seam_commit_geometry')
    evidence = exact(commit['evidence'], 'chunk_id start end native core_cut')
    text(evidence['chunk_id']); integer(evidence['start']); integer(evidence['end'])
    if (evidence['chunk_id'] != commit['chunk_id'] or evidence['start'] != start
            or evidence['end'] != end or sha(evidence) != commit['evidence_sha256']):
        raise Unavailable('seam_commit_evidence_mismatch')
    cut = exact(evidence['core_cut'], 'nominal actual reason')
    number(cut['nominal']); number(cut['actual'])
    if (cut['actual'] != end / RATE or cut['nominal'] < cut['actual']
            or cut['reason'] not in ('neural_speech_gap', 'fixed_or_final')):
        raise Unavailable('seam_core_cut_mismatch')
    native = exact(evidence['native'], 'epoch_id epoch_index owned_start owned_end replay_start input_until processed_until stable_until finished provenance')
    integer(native['epoch_index'])
    if native['epoch_id'] != scope['epoch_id'] or native['epoch_index'] != scope['epoch_index'] or type(native['finished']) is not bool:
        raise Unavailable('seam_native_scope_mismatch')
    for key in ('owned_start', 'owned_end', 'replay_start', 'input_until', 'processed_until', 'stable_until'):
        number(native[key])
    return evidence


def validate_witness(witness, commit, scope, index, start, end):
    exact(witness, 'commit_index query_start_frame query_end_frame analyzer_start_frame analyzer_end_frame analyzer_pcm16le_sha256 piece_pcm16le_sha256 native turns')
    integer(witness['commit_index'], 1)
    if witness['commit_index'] != index:raise Unavailable('seam_witness_order')
    for key in ('query_start_frame', 'query_end_frame', 'analyzer_start_frame', 'analyzer_end_frame'):
        integer(witness[key])
    qa, qb = witness['query_start_frame'], witness['query_end_frame']
    aa, ab = witness['analyzer_start_frame'], witness['analyzer_end_frame']
    if not (0 < qb - qa <= CORE_FRAMES and 0 < ab - aa <= CORE_FRAMES
            and qa <= start < end <= qb and aa <= start < end <= ab):
        raise Unavailable('seam_witness_geometry')
    digest(witness['analyzer_pcm16le_sha256']); digest(witness['piece_pcm16le_sha256'])
    native = exact(witness['native'], 'epoch_id epoch_index replay_start state provenance observed_action_count true_eof')
    integer(native['epoch_index']); integer(native['replay_start'])
    integer(native['observed_action_count'], (1 << 31) - 1, 1)
    if (native['epoch_id'] != scope['epoch_id'] or native['epoch_index'] != scope['epoch_index']
            or type(native['true_eof']) is not bool):raise Unavailable('seam_native_scope_mismatch')
    from .native_owned_support import validate_observed_provenance
    validate_observed_provenance(native['provenance'])
    state = exact(native['state'], 'epoch_id finished fingerprint local_capacity input processed stable')
    integer(state['local_capacity'], 8)
    if (state['epoch_id'] != scope['epoch_id'] or type(state['finished']) is not bool
            or state['finished'] != native['true_eof'] or state['local_capacity'] != 8
            or state['fingerprint'] != native['provenance']['diarization_fingerprint']):
        raise Unavailable('seam_native_state_identity')
    for key in ('input', 'processed', 'stable'):
        item = exact(state[key], 'raw hex frame')
        number(item['raw']); integer(item['frame'], 360 * RATE)
        if item['raw'] > 360 or item['hex'] != float(item['raw']).hex() or item['frame'] != round(item['raw'] * RATE):
            raise Unavailable('seam_native_frontier_transform')
    if not state['stable']['frame'] <= state['processed']['frame'] <= state['input']['frame']:
        raise Unavailable('seam_native_frontier_order')
    if state['finished'] and not state['stable']['frame'] == state['processed']['frame'] == state['input']['frame']:
        raise Unavailable('seam_native_incomplete_eof')
    epoch = scope['epoch_index']; replay = max(0, epoch * 300 * RATE - 339200)
    origin = float.fromhex(scope['origin_seconds_hex']); committed = commit['evidence']['native']
    if (native['replay_start'] != replay or qa != max(aa, replay) or qb != ab
            or committed['provenance'] != native['provenance']
            or committed['finished'] != state['finished']
            or exact_frame(committed['owned_start'], origin=origin) != epoch * 300 * RATE
            or exact_frame(committed['owned_end'], origin=origin) != (epoch + 1) * 300 * RATE
            or exact_frame(committed['replay_start'], origin=origin) != replay):
        raise Unavailable('seam_native_epoch_transform')
    for key in ('input', 'processed', 'stable'):
        expected = origin + replay / RATE + state[key]['raw'] if key == 'input' else origin + (replay + state[key]['frame']) / RATE
        if committed[key + '_until'] != expected:raise Unavailable('seam_native_frontier_transform')
    if (not epoch * 300 * RATE <= commit['logical_start_frame'] < commit['logical_end_frame'] <= (epoch + 1) * 300 * RATE
            or commit['logical_end_frame'] > replay + state['stable']['frame']
            or qb > replay + state['processed']['frame']):raise Unavailable('seam_unstable_core_or_query')
    turns = witness['turns']
    if type(turns) is not list or len(turns) > MAX_TURNS:raise Unavailable('native_turn_capacity')
    for turn in turns:
        exact(turn, 'label original')
        lo, hi = pair(turn['original']); text(turn['label'])
        if (not qa <= lo < hi <= qb
                or turn['label'] not in {scope['epoch_id'] + ':speaker_' + str(i) for i in range(1, 9)}):
            raise Unavailable('seam_turn_query_or_label')


def selected_pair(witnesses, start, end):
    """Lexicographically first overlap within ONE actual response, never a union."""
    for wi, witness in enumerate(witnesses):
        turns = witness['turns']
        for i, one in enumerate(turns):
            for j in range(i + 1, len(turns)):
                two = turns[j]
                lo = max(start, one['original'][0], two['original'][0])
                hi = min(end, one['original'][1], two['original'][1])
                if one['label'] != two['label'] and lo < hi:
                    return {'predicate': POLICY['selection'], 'witness_index': wi,
                            'turn_indexes': [i, j], 'overlap_frames': [lo, hi]}
    raise Unavailable('no_same_response_native_overlap')


def commit_anchor(commit):
    return {'chunk_id': commit['chunk_id'], 'start': commit['local_start_frame'],
            'end': commit['local_end_frame'], 'sha256': commit['evidence_sha256']}


def coverage_projection(scope, shape, commits, runtime):
    return {'kind': 'seam_overlap_alternative',
        'scope_sha256': sha({'schema': 'native-audio-coverage-scope-v1',
            **{k: scope[k] for k in ('session_id', 'stream_id', 'origin_seconds_hex', 'epoch_id', 'epoch_index')},
            'native_runtime_sha256': runtime}),
        'frame_range': [shape['start_frame'], shape['end_frame']],
        'source_support_core_semantics': 'two_committed_core_envelope',
        'coverage_envelope_frames': [commits[0]['logical_start_frame'], commits[1]['logical_end_frame']],
        'primary_commit_refs': [commit_anchor(c) for c in commits],
        'adds_primary_audio': False, 'automatic_dispatch': False}


def validate_document(proof):
    if type(proof) is not bytes or not 1 <= len(proof) <= PROOF_BYTES:raise Unavailable('proof_capacity')
    doc = json.loads(proof)
    from .native_owned_core_seams import is_core, validate_document as validate_core
    if is_core(doc):return validate_core(proof)
    exact(doc, 'schema policy scope geometry support dependencies commits native_witnesses selection publication_anchor selected coverage')
    if doc['schema'] != SCHEMA or doc['policy'] != POLICY_SHA256 or doc['selected'] is not True:
        raise Unavailable('proof_policy_or_selection')
    scope = doc['scope']; validate_scope(scope)
    commits, witnesses, dependencies = doc['commits'], doc['native_witnesses'], doc['dependencies']
    if any(type(x) is not list or len(x) != 2 for x in (commits, witnesses, dependencies)):
        raise Unavailable('seam_exactly_two_pieces_required')
    for c in commits:validate_commit(c, scope)
    left, right = commits
    if (left['chunk_id'] == right['chunk_id'] or left['logical_end_frame'] != right['logical_start_frame']
            or right['local_start_frame'] != 0):raise Unavailable('seam_nonadjacent_commits')
    a, seam, b = left['logical_start_frame'], left['logical_end_frame'], right['logical_end_frame']
    expected = geometry(a, seam, b)
    shape = exact(doc['geometry'], 'seam_frame left_frames right_frames start_frame end_frame')
    for value in shape.values():integer(value)
    if shape != expected:raise Unavailable('seam_nondeterministic_geometry')
    support = SourceSupport.from_dict(doc['support'])
    parts = []
    for p in dependencies:
        exact(p, 'session_id chunk_id receipt_sha256 source_sha256 source_frames source_start source_end logical_start logical_end')
        parts.append(SourceSpan(**p))
    parts = tuple(parts); validate_mapping(parts, shape['start_frame'], shape['end_frame'])
    for i, (part, commit) in enumerate(zip(parts, commits)):
        lo, hi = (shape['start_frame'], seam) if i == 0 else (seam, shape['end_frame'])
        if (part.session_id != scope['session_id'] or part.chunk_id != commit['chunk_id']
                or part.source_frames != commit['local_end_frame']
                or part.logical_start != lo or part.logical_end != hi
                or part.source_start != lo - commit['source_origin_frame']
                or part.source_end != hi - commit['source_origin_frame']):raise Unavailable('seam_dependency_commit_mapping')
        validate_witness(witnesses[i], commit, scope, i, lo, hi)
    if (witnesses[0]['native']['provenance'] != witnesses[1]['native']['provenance']
            or witnesses[0]['native']['observed_action_count'] >= witnesses[1]['native']['observed_action_count']):
        raise Unavailable('seam_native_continuity')
    if sum(len(w['turns']) for w in witnesses) > MAX_TURNS:raise Unavailable('native_turn_capacity')
    chosen = exact(doc['selection'], 'predicate witness_index turn_indexes overlap_frames')
    integer(chosen['witness_index'], 1); pair(chosen['turn_indexes']); pair(chosen['overlap_frames'])
    if chosen != selected_pair(witnesses, shape['start_frame'], shape['end_frame']):raise Unavailable('seam_selection_mismatch')
    publication = exact(doc['publication_anchor'], 'chunk_id start end sha256')
    text(publication['chunk_id']); integer(publication['start']); integer(publication['end']); digest(publication['sha256'])
    if publication != commit_anchor(right):raise Unavailable('seam_publication_anchor_mismatch')
    contents = [p.content() for p in parts]
    runtime = sha(witnesses[0]['native']['provenance'])
    core = sha({'schema': 'native-owned-seam-composition-v2', 'scope': scope, 'geometry': shape,
                'commits': commits, 'native_witnesses': witnesses})
    if (support.source_generation != sha({'schema': 'immutable-source-cell-generation-v1', 'sources': contents})
            or support.plan_sha256 != sha({'schema': 'bounded-cell-mapping-v1', 'parts': contents})
            or support.native_runtime_sha256 != runtime or support.core_sha256 != core
            or (support.source_frames, support.core_start_frame, support.core_end_frame,
                support.start_frame, support.end_frame) != (b, a, b, shape['start_frame'], shape['end_frame'])):
        raise Unavailable('immutable_content_proof_mismatch')
    projection = exact(doc['coverage'], 'kind scope_sha256 frame_range source_support_core_semantics coverage_envelope_frames primary_commit_refs adds_primary_audio automatic_dispatch')
    pair(projection['frame_range']); pair(projection['coverage_envelope_frames']); digest(projection['scope_sha256'])
    if (projection['adds_primary_audio'] is not False or projection['automatic_dispatch'] is not False
            or canonical(projection) != canonical(coverage_projection(scope, shape, commits, runtime))):
        raise Unavailable('seam_coverage_mismatch')
    if canonical(doc) != proof:raise Unavailable('noncanonical_proof')
    return doc, support, parts


def validate_cell_pcm(doc, pcm):
    from .native_owned_core_seams import is_core
    if is_core(doc):
        if hashlib.sha256(pcm).hexdigest() != doc['query']['support_pcm16le_sha256']:
            raise Unavailable('core_seam_pcm_mismatch')
        return
    split = 2 * doc['geometry']['left_frames']
    for witness, piece in zip(doc['native_witnesses'], (memoryview(pcm)[:split], memoryview(pcm)[split:])):
        if hashlib.sha256(piece).hexdigest() != witness['piece_pcm16le_sha256']:
            raise Unavailable('seam_piece_pcm_mismatch')


@dataclass(frozen=True, slots=True)
class Piece:
    """One exact contribution; immutable canonical metadata avoids producer aliases."""
    scope: dict
    commit: dict
    witness: dict
    part: SourceSpan
    pcm: bytes

    @property
    def material_bytes(self):
        return len(self.pcm) + len(canonical({'scope': self.scope, 'commit': self.commit,
                                            'witness': self.witness, 'part': asdict(self.part)}))

    def __post_init__(self):
        validate_scope(self.scope); validate_commit(self.commit, self.scope)
        if (type(self.part) is not SourceSpan or type(self.pcm) is not bytes
                or len(self.pcm) != 2 * (self.part.logical_end - self.part.logical_start)
                or not 0 < len(self.pcm) <= 2 * SIDE_FRAMES or self.material_bytes > TAIL_BYTES
                or hashlib.sha256(self.pcm).hexdigest() != self.witness['piece_pcm16le_sha256']):
            raise Unavailable('seam_piece_capacity_or_pcm')


def make_composite(left, right):
    if type(left) is not Piece or type(right) is not Piece or left.scope != right.scope:
        raise Unavailable('seam_piece_scope_mismatch')
    # Sum before concatenation: two pieces, new joined PCM, proof/canonical
    # copies, and original-read scratch remain inside the explicit 4 MiB
    # assembler working reservation. The existing handoff is charged separately.
    total = left.material_bytes + right.material_bytes
    if total + len(left.pcm) + len(right.pcm) + 8 * PROOF_BYTES > WORKING_BYTES:
        raise Unavailable('seam_assembly_working_capacity')
    scope = left.scope; commits = [left.commit, right.commit]
    witnesses = [dict(left.witness, commit_index=0), dict(right.witness, commit_index=1)]
    if sum(len(w['turns']) for w in witnesses) > MAX_TURNS:raise Unavailable('native_turn_capacity')
    shape = geometry(commits[0]['logical_start_frame'], commits[0]['logical_end_frame'], commits[1]['logical_end_frame'])
    selected = selected_pair(witnesses, shape['start_frame'], shape['end_frame'])
    parts = (left.part, right.part); contents = [p.content() for p in parts]
    pcm = left.pcm + right.pcm; runtime = sha(witnesses[0]['native']['provenance'])
    core = sha({'schema': 'native-owned-seam-composition-v2', 'scope': scope, 'geometry': shape,
                'commits': commits, 'native_witnesses': witnesses})
    support = SourceSupport(sha({'schema': 'immutable-source-cell-generation-v1', 'sources': contents}),
        sha({'schema': 'bounded-cell-mapping-v1', 'parts': contents}), runtime, core,
        commits[1]['logical_end_frame'], commits[0]['logical_start_frame'], commits[1]['logical_end_frame'],
        shape['start_frame'], shape['end_frame'], hashlib.sha256(pcm).hexdigest(), float32_digest(pcm))
    publication = commit_anchor(commits[1])
    proof = canonical({'schema': SCHEMA, 'policy': POLICY_SHA256, 'scope': scope, 'geometry': shape,
        'support': support.to_dict(), 'dependencies': [asdict(p) for p in parts], 'commits': commits,
        'native_witnesses': witnesses, 'selection': selected, 'publication_anchor': publication,
        'selected': True, 'coverage': coverage_projection(scope, shape, commits, runtime)})
    return Cell(support, parts, proof, pcm, scope['session_id'], publication['chunk_id'],
                publication['start'], publication['end'], publication['sha256'])
