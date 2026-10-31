"""Bounded prospective primary observations for the recording-local probe.

This consumer never reconstructs old provenance or opens an audio source. The
only audio is the already owned CellLease; retained numeric packets are audit
evidence, while each live boundary independently requires the current owner.
"""
from dataclasses import asdict
from contextlib import contextmanager
import hashlib
import json
import struct
import sys

from . import recording_primary_provenance as provenance
from .native_owned_cell_store import Budget
from .native_owned_cells import (Unavailable, canonical, sha, validate_document,
    intersect_mapping, float32_digest)
from .native_owned_core_seams import BOUNDED_SCHEMA, slot, validate_target

MAX_PACKETS = 4
MAX_PACKET_BYTES = 65536
MAX_PACKET_TURNS = 256
# These tighter standalone limits bound the temporary decoded graph inside the
# existing512KiB per-probe envelope. Refusal never truncates a retained packet.
MAX_STANDALONE_UNITS, MAX_STANDALONE_TURNS, MAX_STANDALONE_ROWS = 128, 64, 16
MAX_STANDALONE_LABELS = 8


def standalone_staging_bound(packet,node=None,*,wire_bytes=0,receipt_bytes=0):
    """Conservative owned-graph admission, separate from sampled service RSS.

    Three graph/wire copies cover the live basis, detached current read and
    serialization. 128 KiB covers one validated vector/result, <=28 witnesses,
    <=16 row dependencies, fixed owner metadata and the 4 KiB hash buffer.
    Shared values are intentionally counted repeatedly; no dedup set is built.
    """
    count = 0
    def charge(value,depth=0):
        nonlocal count
        count += 1
        if count > 8192 or depth > 16:raise Unavailable('primary_probe_staging_capacity')
        total = sys.getsizeof(value)
        if type(value) is dict:
            for key,item in value.items():total += charge(key,depth+1)+charge(item,depth+1)
        elif type(value) in (list,tuple):
            for item in value:total += charge(item,depth+1)
        elif value is not None and type(value) not in (str,int,float,bool,bytes):
            raise Unavailable('primary_probe_staging_capacity')
        return total
    forecast = 3 * (charge(packet)+charge(node)) + 3 * (wire_bytes+receipt_bytes) + 64000 + 131072
    if forecast > 512 * 1024:raise Unavailable('primary_probe_staging_capacity')
    return forecast


def standalone_float_digest(pcm):
    """Exact conversion hash with a 4 KiB buffer and no float/object array."""
    if type(pcm) is not bytes or len(pcm) != 64000:
        raise Unavailable('primary_probe_exact_crop_required')
    result = hashlib.sha256(); block = bytearray(4096)
    for start in range(0,len(pcm),2048):
        count = min(2048,len(pcm)-start)//2
        for index in range(count):
            value = struct.unpack_from('<h',pcm,start+index*2)[0]
            struct.pack_into('<f',block,index*4,value/32768.)
        result.update(memoryview(block)[:count*4])
    return result.hexdigest()


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
    source.primary_authority_current()
    owner = source.owner
    return owner, owner.authority, owner._epoch, owner.authority.identity


def _check_fence(source, fence):
    owner, authority, generation, identity = fence
    from .native_owned_support_cache import ReadError
    try:source.primary_authority_current()
    except ReadError as exc:
        raise Unavailable('primary_probe_authority_changed') from exc
    if (source.owner is not owner or owner.authority is not authority
            or authority.owner is not owner or owner._epoch is not generation
            or authority.identity != identity):
        raise Unavailable('primary_probe_authority_changed')


def _source_current(source, fence, scope, part, budget, *, receipt=None):
    _check_fence(source, fence)
    owner, authority, _, _ = fence
    receipt = owner.receipt(part['chunk_id']) if receipt is None else receipt
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


def _metadata(support, proof, packets, space_id, *, with_components=False):
    from .recording_speaker_links import (_native_responses, _subtract,
        CROP_FRAMES, EROSION_FRAMES)
    doc, observed, parts = validate_document(proof)
    if observed != support:
        raise Unavailable('primary_probe_support_binding')
    validate_packets(proof, packets)
    responses = _native_responses(doc, support, parts[0].session_id, typed=True)
    result, outcomes, components = [], [], []
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
                components.append((a, b))
                outcome['status'] = 'candidate'
            else:
                outcome['status'] = 'crop_capacity'
    return (result, outcomes, components) if with_components else (result, outcomes)


def primary_allocation_metadata(support, proof, packets, space_id):
    """The unchanged admitted primary metadata plus its selected clean component."""
    return _metadata(support, proof, packets, space_id, with_components=True)


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


def _standalone_metadata(packet, space_id, *, saved=None):
    """One naturally observed clean crop, without inventing a MOSS query.

    The prospective form has no primary reference. Only the positively
    acknowledged primary transaction can supply that reference later.
    """
    from .recording_speaker_links import CROP_FRAMES, EROSION_FRAMES, _subtract
    if len(packet['turns']) > MAX_PACKET_TURNS:
        raise Unavailable('primary_probe_packet_capacity')
    part = packet['commit']['part']
    namespace = _namespace(packet); scope_id = sha(namespace)
    lo, hi = part['logical_start'], part['logical_end']
    for row_index, (parent, unit_indices) in enumerate(packet['rows']):
        units = [packet['units'][i] for i in unit_indices]
        assigned = {unit[5] for unit in units}
        if len(assigned) != 1 or None in assigned:continue
        label_index = next(iter(assigned)); label = packet['labels'][label_index]
        turns = [turn for turn in packet['turns'] if turn[1] == label_index]
        blocked = [turn[2:4] for turn in packet['turns'] if turn[1] != label_index]
        clean = [piece for a, b in _union((max(lo, t[2]), min(hi, t[3])) for t in turns)
                 for piece in _subtract((a, b), blocked)]
        eligible = [(a + EROSION_FRAMES, b - EROSION_FRAMES) for a, b in clean
                    if b - a >= CROP_FRAMES + 2 * EROSION_FRAMES]
        if not eligible:continue
        a, b = min(eligible, key=lambda value: (-(value[1] - value[0]), *value))
        start = a + (b - a - CROP_FRAMES) // 2; end = start + CROP_FRAMES
        span = dict(part, source_start=part['source_start'] + start - lo,
                    source_end=part['source_start'] + end - lo,
                    logical_start=start, logical_end=end)
        content = [{key: span[key] for key in
                    ('source_sha256', 'source_frames', 'source_start', 'source_end')}]
        node = {'source_kind':'native_primary', 'primary_row_index':row_index,
            'original_parent_ordinal':parent,
            'original_unit_ordinals':[[unit[0], unit[1]] for unit in units],
            'original_turn_ordinals':[turn[0] for turn in turns
                if max(start, turn[2]) < min(end, turn[3])],
            'capture_namespace':namespace, 'native_scope_id':scope_id,
            'native_label':label,
            'native_attributions':[{'native_scope_id':scope_id, 'native_label':label}],
            'start_frame':start, 'end_frame':end, 'source_spans':[span],
            'source_key':sha({'schema':'exact-source-crop-v1', 'parts':content}),
            'space_id':space_id}
        if saved is not None:
            node.update(primary_source_ref=packet['refs'][row_index][0],
                        primary_packet_sha256=saved['header']['digest'])
        return node
    return None


def standalone_primary_metadata(saved, space_id):
    """Deterministic standalone metadata from one exact immutable packet."""
    provenance.validate_packet(saved['packet'], saved['header'])
    return _standalone_metadata(saved['packet'], space_id, saved=saved)


def validate_standalone_primary_node(saved, node):
    expected = standalone_primary_metadata(saved, node['space_id'])
    if expected is None or any(node.get(key) != value for key, value in expected.items()):
        raise Unavailable('primary_probe_node_binding')
    if any(key in node for key in ('phrase_index', 'moss_tag', 'job_id', 'result_sha256')):
        raise Unavailable('primary_probe_fabricated_phrase')
    from .native_owned_cells import digest
    digest(node['pcm16le_sha256']); digest(node['float32le_sha256'])
    return node


def copy_standalone_primary(draft, owned_pcm, space_id):
    """The sole 64,000-byte owned copy, made while Capture owns exact PCM."""
    original = draft['packet']
    if (len(original['units']) > MAX_STANDALONE_UNITS
            or len(original['turns']) > MAX_STANDALONE_TURNS
            or len(original['rows']) > MAX_STANDALONE_ROWS
            or len(original['labels']) > MAX_STANDALONE_LABELS):
        raise Unavailable('primary_probe_staging_capacity')
    raw = provenance.bounded(original)
    if len(raw) > MAX_PACKET_BYTES:raise Unavailable('primary_probe_packet_capacity')
    standalone_staging_bound(original,wire_bytes=len(raw))
    packet = json.loads(raw)
    node = _standalone_metadata(packet, space_id)
    if node is None:return None
    part = packet['commit']['part']
    if (type(owned_pcm) is not bytes or len(owned_pcm) != 2 *
            (part['logical_end'] - part['logical_start']) or
            hashlib.sha256(owned_pcm).hexdigest() != packet['owned_pcm16le_sha256']):
        raise Unavailable('primary_probe_source_binding')
    a = 2 * (node['start_frame'] - part['logical_start'])
    crop = owned_pcm[a:a + 64000]
    if len(crop) != 64000:raise Unavailable('primary_probe_exact_crop_required')
    node.update(pcm16le_sha256=hashlib.sha256(crop).hexdigest(),
                float32le_sha256=standalone_float_digest(crop))
    standalone_staging_bound(packet,node,wire_bytes=len(raw))
    return raw, node, crop


def bind_standalone_primary(saved, draft_bytes, prospective_node):
    packet = saved['packet']
    if provenance.bounded({key:value for key,value in packet.items() if key != 'refs'}) != draft_bytes:
        raise Unavailable('primary_probe_packet_changed')
    node = standalone_primary_metadata(saved, prospective_node['space_id'])
    if node is None or any(node.get(key) != value for key,value in prospective_node.items()
                          if key not in ('pcm16le_sha256','float32le_sha256')):
        raise Unavailable('primary_probe_node_binding')
    node.update({key:prospective_node[key] for key in ('pcm16le_sha256','float32le_sha256')})
    return validate_standalone_primary_node(saved, node)


def standalone_current(db, saved, source, fence, receipt_bytes, *, admitted=False):
    """Live receipt/mapping/row authority without retaining a lease or compact.

    The caller's fresh, acknowledged in-memory transfer is required separately.
    Immutable audit packets alone never call this path or create permission.
    """
    from .native_owned_cell_store import Receipt
    from .native_owned_cells import exact_frame
    from .native_owned_core_seams import COMPACT_BYTES
    from . import transcript_contract as contract
    if not db.in_transaction:raise ValueError('Primary history requires a snapshot')
    receipt = Receipt.decode(receipt_bytes)
    packet = provenance.validate_packet(saved['packet'], saved['header'])
    part = packet['commit']['part']; header = saved['header']
    with Budget().phase() as budget:
        _check_fence(source, fence)
        if not admitted:
            # First authority still requires the actual committed compact.
            # Only this manager's positively acknowledged claim may later use
            # its detached reservation basis after ordinary compact cleanup.
            budget.projection(len(provenance.bounded(packet)) + provenance.MAX_BODY)
            current = provenance.current(db, saved, source_current=lambda part:
                _source_current(source, fence, packet['commit']['scope'], part, budget, receipt=receipt))
            _check_fence(source, fence)
            return current
        actual = provenance.read_exact(db, cid=part['chunk_id'],
            start=part['source_start'], end=part['source_end'])
        if actual != saved:return False
        budget.projection(len(provenance.bounded(packet)))
        live = db.execute('''SELECT 1 FROM sessions s JOIN recording_batches b ON b.session_id=s.id
            JOIN recording_work w ON w.batch_id=b.id WHERE s.id=? AND s.deleted=0
            AND b.id=? AND w.chunk_id=?''', (part['session_id'], header['rid'], part['chunk_id'])).fetchone()
        if live is None:return False
        # An absent ordinary-GC compact is harmless; a present conflicting or
        # explicitly cancelled one is never ignored.
        compact = db.execute('SELECT body,state FROM native_core_commits WHERE slot=?',
                             (header['core_slot'],)).fetchone()
        if compact is not None and (bytes(compact['body']) != canonical(packet['commit'], COMPACT_BYTES)
                                     or compact['state'] == 'cancelled'):return False
        row = db.execute('SELECT next_offset,status FROM chunks WHERE id=?', (part['chunk_id'],)).fetchone()
        if row is None or row['status'] in ('cancelled','failed','staging') or exact_frame(row['next_offset']) < part['source_end']:
            return False
        if not _source_current(source, fence, packet['commit']['scope'], part, budget, receipt=receipt):
            return False
        for index, (ref, _) in enumerate(packet['refs']):
            ident = int(ref.split('.')[1])
            lengths = db.execute('SELECT ' + '+'.join('COALESCE(length(CAST(' + key + ' AS BLOB)),0)'
                for key in ('text','raw_text','asr_backend','timestamp_kind','speaker_local_id'))
                + '+512 FROM segments WHERE id=?', (ident,)).fetchone()
            if lengths is None:return False
            budget.projection(lengths[0])
            row = db.execute('SELECT ' + ','.join(contract.PRIMARY_FIELDS) +
                             ' FROM segments WHERE id=?', (ident,)).fetchone()
            if (row is None or not provenance._row_binding(packet,index,row)
                    or contract.primary_evidence(part['session_id'],row)['source_ref'] != ref):return False
        _check_fence(source, fence)
    return True


def detached_sources_current(db, source, fence, proof, receipt_bytes):
    """Validate a freshly copied MOSS basis using its detached source receipts."""
    from .native_owned_cell_store import Receipt
    from .native_owned_cells import RATE
    _, _, parts = validate_document(proof)
    receipts = {item.chunk_id:item for item in (Receipt.decode(raw) for raw in receipt_bytes)}
    if len(receipts) != len(receipt_bytes) or len(receipts) > 128:
        raise Unavailable('primary_probe_packet_capacity')
    with Budget().phase() as budget:
        for part in parts:
            receipt = receipts.get(part.chunk_id)
            if receipt is None:return False
            origin = float(receipt.timeline_start - (part.logical_start - part.source_start) / RATE).hex()
            if not _source_current(source, fence, {'origin':origin}, asdict(part), budget, receipt=receipt):return False
        _check_fence(source, fence)
    return True
