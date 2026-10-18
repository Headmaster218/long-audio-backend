"""Bounded prospective primary observations for the recording-local probe.

This consumer never reconstructs old provenance or opens an audio source. The
only audio is the already owned CellLease; retained numeric packets are audit
evidence, while each live boundary independently requires the current owner.
"""
from dataclasses import asdict
from contextlib import contextmanager
import hashlib

from . import recording_primary_provenance as provenance
from .native_owned_cell_store import Budget
from .native_owned_cells import (Unavailable, canonical, sha, validate_document,
    intersect_mapping, float32_digest)
from .native_owned_core_seams import BOUNDED_SCHEMA, slot, validate_target

MAX_PACKETS = 4
MAX_PACKET_BYTES = 65536
MAX_PACKET_TURNS = 256


def named_commits(proof):
    """Only exact named bounded-core commitments, deduplicated by slot."""
    doc, _, _ = validate_document(proof)
    if doc.get('schema') != BOUNDED_SCHEMA:
        return ()
    _, originals = validate_target(doc['target'])
    named = {}
    for commit in (*originals, *doc['commits']):
        key = slot(commit)
        if key in named and named[key] != commit:
            raise Unavailable('primary_probe_conflicting_slot')
        named[key] = commit
    if len(named) > MAX_PACKETS:
        raise Unavailable('primary_probe_packet_capacity')
    return tuple(sorted(named.values(), key=lambda c: (c['part']['logical_start'], slot(c))))


def _fence(source):
    from .native_owned_cell_jobs import CellJobsSource
    from .native_owned_cell_capture import PrimaryAuthority
    if (type(source) is not CellJobsSource or type(source.owner.authority) is not PrimaryAuthority
            or source.owner.authority.store is not source.store):
        raise Unavailable('primary_probe_authority_unavailable')
    source._current()
    owner = source.owner
    return owner, owner.authority, owner._epoch, owner.authority.identity


def _check_fence(source, fence):
    owner, authority, generation, identity = fence
    from .native_owned_support_cache import ReadError
    try:source._current()
    except ReadError as exc:
        raise Unavailable('primary_probe_authority_changed') from exc
    if (source.owner is not owner or owner.authority is not authority
            or authority.owner is not owner or owner._epoch is not generation
            or authority.identity != identity):
        raise Unavailable('primary_probe_authority_changed')


def _source_current(source, fence, scope, part, budget):
    _check_fence(source, fence)
    owner, authority, _, _ = fence
    receipt = owner.receipt(part['chunk_id'])
    if (receipt is None or receipt.receipt_id != part['receipt_sha256']
            or receipt.session_id != part['session_id']
            or receipt.digest != part['source_sha256']
            or receipt.frames != part['source_frames']
            or not authority.receipt_valid(receipt, budget)):
        return False
    row = authority.row(part['chunk_id'], budget)
    if row is None or row['status'] in ('cancelled', 'failed', 'staging'):
        return False
    mapping = authority.mapping(row, float.fromhex(scope['origin']),
        part['logical_start'], part['logical_end'], budget)
    _check_fence(source, fence)
    return [asdict(item) for item in mapping] == [part]


def _packet_current(db, saved, source, fence, budget):
    packet = saved['packet']
    state = db.execute('SELECT state FROM native_core_commits WHERE slot=?',
                       (saved['header']['core_slot'],)).fetchone()
    budget.projection(128)
    if state is None or state[0] == 'cancelled':return False
    # Account all text projections before current() materializes them. Packet
    # bodies and owner source reads use this same boundary-local Budget.
    ids = [int(ref[0].split('.')[1]) for ref in packet['refs']]
    marks = ','.join('?' for _ in ids)
    size = db.execute('SELECT COALESCE(SUM(' + '+'.join(
        'COALESCE(length(CAST(' + k + ' AS BLOB)),0)' for k in
        ('text', 'raw_text', 'asr_backend', 'timestamp_kind', 'speaker_local_id'))
        + '+512),0) FROM segments WHERE id IN (' + marks + ')', ids).fetchone()[0]
    budget.projection(512)
    budget.projection(size)
    budget.projection(len(provenance.bounded(packet)))
    return provenance.current(db, saved, source_current=lambda part:
        _source_current(source, fence, packet['commit']['scope'], part, budget))


def read_named(db, proof, source):
    """At most four exact reads; admission precedes packet materialization."""
    commits = named_commits(proof)
    if not commits:
        return []
    fence = _fence(source)
    result = []
    with Budget().phase() as budget:
        admitted, total = [], 0
        for commit in commits:
            p = commit['part']
            size = db.execute(f'SELECT length(body) FROM {provenance.TABLE} '
                'WHERE cid=? AND core_start=? AND core_end=?',
                (p['chunk_id'], p['source_start'], p['source_end'])).fetchone()
            budget.projection(512)
            if size is None:
                continue
            if type(size[0]) is not int or not 1 <= size[0] <= provenance.MAX_BODY:
                raise Unavailable('primary_probe_packet_size')
            if total + size[0] > MAX_PACKET_BYTES:
                continue
            total += size[0]
            admitted.append((commit, size[0]))
        for commit, size in admitted:
            budget.projection(size)
            p = commit['part']
            saved = provenance.read_exact(db, cid=p['chunk_id'],
                start=p['source_start'], end=p['source_end'])
            if saved is None or saved['packet']['commit'] != commit:
                raise Unavailable('primary_probe_packet_changed')
            if len(saved['packet']['turns']) > MAX_PACKET_TURNS:
                continue
            if not _packet_current(db, saved, source, fence, budget):
                raise Unavailable('primary_probe_source_stale')
            result.append(saved)
        _check_fence(source, fence)
    return result


def validate_packets(proof, packets):
    """Retained numeric bytes must still match exact named proof slots."""
    if type(packets) not in (list, tuple) or len(packets) > MAX_PACKETS:
        raise Unavailable('primary_probe_packet_capacity')
    if not packets:
        return []
    named = {slot(c): c for c in named_commits(proof)}
    found, total = set(), 0
    for saved in packets:
        if type(saved) is not dict or set(saved) != {'header', 'packet'}:
            raise Unavailable('primary_probe_packet_shape')
        packet = provenance.validate_packet(saved['packet'], saved['header'])
        key = slot(packet['commit'])
        total += len(provenance.bounded(packet))
        if (key in found or named.get(key) != packet['commit']
                or len(packet['turns']) > MAX_PACKET_TURNS or total > MAX_PACKET_BYTES):
            raise Unavailable('primary_probe_packet_binding')
        found.add(key)
    return packets


class _PacketBoundary:
    """One fresh operation budget/fence, including every retained batch."""
    def __init__(self, source, budget):
        self.source, self.budget = source, budget
        self.fence, self.seen = None, {}

    def check(self, db, proof, packets):
        validate_packets(proof, packets)
        if not packets:return True
        if self.fence is None:self.fence = _fence(self.source)
        _check_fence(self.source, self.fence)
        for saved in packets:
            key = saved['header']['digest']
            original = self.seen.get(key)
            if original is not None:
                if original != saved:raise Unavailable('primary_probe_packet_changed')
                continue
            if not _packet_current(db, saved, self.source, self.fence, self.budget):
                return False
            self.seen[key] = saved
        _check_fence(self.source, self.fence)
        return True


@contextmanager
def packet_boundary(source):
    """Only boundary-local exact packet deduplication; never durable authority."""
    with Budget().phase() as budget:
        boundary = _PacketBoundary(source, budget)
        yield boundary
        if boundary.fence is not None:_check_fence(source, boundary.fence)


def current_packets(db, proof, packets, source, *, boundary=None):
    """No cache survives a reserve, commit, recomputation or publication edge."""
    if boundary is not None:
        return boundary.check(db, proof, packets)
    with packet_boundary(source) as fresh:
        return fresh.check(db, proof, packets)


def _namespace(packet):
    commit = packet['commit']
    return {'schema': 'recording-primary-native-capture-v2',
        'scope': commit['scope'], 'runtime_sha256': commit['runtime_sha256'],
        'query': packet['query']}


def primary_witnesses(proof, packets):
    from .recording_speaker_links import _witnesses
    validate_packets(proof, packets)
    result = []
    for saved in packets:
        packet = saved['packet']
        turns = [{'label': packet['labels'][t[1]], 'original': t[2:4]}
                 for t in packet['turns']]
        result.extend(_witnesses([(sha(_namespace(packet)), turns, 0, 0)],
                                 saved['header']['digest']))
    return sorted(result, key=lambda w: (w['native_scope_id'], w['labels'],
                                        w['overlap_frames'], w['proof_sha256']))


def _union(intervals):
    merged = []
    for a, b in sorted(intervals):
        if a >= b:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def _metadata(support, proof, packets, space_id):
    from .recording_speaker_links import (_native_responses, _subtract,
        CROP_FRAMES, EROSION_FRAMES)
    doc, observed, parts = validate_document(proof)
    if observed != support:
        raise Unavailable('primary_probe_support_binding')
    validate_packets(proof, packets)
    responses = _native_responses(doc, support, parts[0].session_id, typed=True)
    result, outcomes = [], []
    for saved in packets:
        packet = saved['packet']; commit = packet['commit']; part = commit['part']
        namespace = _namespace(packet); scope_id = sha(namespace)
        lo = max(part['logical_start'], support.start_frame)
        hi = min(part['logical_end'], support.end_frame)
        for row_index, (parent, unit_indices) in enumerate(packet['rows']):
            pref = packet['refs'][row_index][0]
            outcome = {'primary_source_ref': pref, 'status': 'insufficient_clean_support'}
            outcomes.append(outcome)
            units = [packet['units'][i] for i in unit_indices]
            assigned = {u[5] for u in units}
            if len(assigned) != 1 or None in assigned:
                outcome['status'] = 'ambiguous_native_label'
                continue
            label_index = next(iter(assigned)); label = packet['labels'][label_index]
            original_turns = [t for t in packet['turns'] if t[1] == label_index]
            blocked = [t[2:4] for t in packet['turns'] if t[1] != label_index]
            clean = [piece for a, b in _union((max(lo, t[2]), min(hi, t[3]))
                     for t in original_turns) for piece in _subtract((a, b), blocked)]
            eligible = {}
            for query_scope, turns, qa, qb in responses:
                for query_label in sorted({t['label'] for t in turns}):
                    query_clean = [piece for a, b in _union(
                        (max(qa, t['original'][0]), min(qb, t['original'][1]))
                        for t in turns if t['label'] == query_label)
                        for piece in _subtract((a, b), [t['original'] for t in turns
                                                     if t['label'] != query_label])]
                    for a, b in clean:
                        for c, d in query_clean:
                            left, right = max(a, c) + EROSION_FRAMES, min(b, d) - EROSION_FRAMES
                            if right - left >= CROP_FRAMES:
                                eligible.setdefault((query_scope, query_label), set()).add((left, right))
            if len(eligible) != 1:
                if eligible:outcome['status'] = 'ambiguous_native_label'
                continue
            (query_scope, query_label), intervals = next(iter(eligible.items()))
            a, b = min(intervals, key=lambda x: (-(x[1] - x[0]), x[0], x[1]))
            start = a + (b - a - CROP_FRAMES) // 2; end = start + CROP_FRAMES
            spans = [asdict(p) for p in intersect_mapping(parts, start, end)]
            # Independent live-lease mapping must reproduce the original owned
            # source coordinates, not merely overlap their logical timestamps.
            expected = dict(part, source_start=part['source_start'] + start - part['logical_start'],
                source_end=part['source_start'] + end - part['logical_start'],
                logical_start=start, logical_end=end)
            if spans != [expected]:
                continue
            content = [{k: p[k] for k in ('source_sha256', 'source_frames',
                        'source_start', 'source_end')} for p in spans]
            node = {'source_kind': 'native_primary', 'primary_source_ref': pref,
                'primary_packet_sha256': saved['header']['digest'], 'primary_commit': commit,
                'primary_row_index': row_index, 'original_parent_ordinal': parent,
                'original_unit_ordinals': [[u[0], u[1]] for u in units],
                'original_turn_ordinals': [t[0] for t in original_turns if max(start, t[2]) < min(end, t[3])],
                'capture_namespace': namespace, 'native_scope_id': scope_id, 'native_label': label,
                'native_attributions': [{'native_scope_id': scope_id, 'native_label': label},
                    {'native_scope_id': query_scope, 'native_label': query_label}],
                'start_frame': start, 'end_frame': end, 'source_spans': spans,
                'source_key': sha({'schema': 'exact-source-crop-v1', 'parts': content}),
                'space_id': space_id}
            if len(result) < MAX_PACKETS:
                result.append(node)
                outcome['status'] = 'candidate'
            else:
                outcome['status'] = 'crop_capacity'
    return result, outcomes


def primary_candidates(support, pcm, proof, packets, space_id):
    nodes, outcomes = _metadata(support, proof, packets, space_id)
    answer = []
    for node in nodes:
        a, b = node['start_frame'], node['end_frame']
        crop = pcm[2 * (a - support.start_frame):2 * (b - support.start_frame)]
        if type(crop) is not bytes or len(crop) != 64000:
            raise Unavailable('primary_probe_exact_crop_required')
        answer.append(({**node, 'pcm16le_sha256': hashlib.sha256(crop).hexdigest(),
                        'float32le_sha256': float32_digest(crop)}, crop))
    return answer, primary_witnesses(proof, packets), outcomes


def primary_metadata(support, proof, packets, space_id):
    """Deterministic numeric evidence, including every abstention outcome."""
    return _metadata(support, proof, packets, space_id)


def validate_primary_node(node, proof, packets):
    _, support, _ = validate_document(proof)
    candidates, _ = _metadata(support, proof, packets, node['space_id'])
    matched = [candidate for candidate in candidates
        if candidate['primary_source_ref'] == node.get('primary_source_ref')
        and candidate['primary_packet_sha256'] == node.get('primary_packet_sha256')]
    if len(matched) != 1 or any(node.get(key) != value for key, value in matched[0].items()):
        raise Unavailable('primary_probe_node_binding')
    if 'phrase_index' in node or 'moss_tag' in node:
        raise Unavailable('primary_probe_fabricated_phrase')
    return node
