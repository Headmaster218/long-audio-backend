"""Pure, bounded selected-cell evidence. No model, file, SQL or provider work.

UNQUALIFIED source checkpoint: durable runtime admission is separately gated.
Coordinates are integer frames relative to a witnessed source scope. Native
frontier float-to-frame transforms remain evidence, never repaired source bounds.
"""
from __future__ import annotations
from array import array
import sys

from dataclasses import dataclass, asdict
import hashlib
import json
import math
import re
import struct

from .native_lexical_alternatives import SourceSupport

RATE = 16000
MAX_FRAME = (1 << 53) - 1
CELL_FRAMES = 320000
CELL_PCM_BYTES = 640000
CORE_FRAMES = 60 * RATE
RING_FRAMES = 96 * RATE
RING_PCM_BYTES = 3072000
IO_BYTES = 65536
PROOF_BYTES = 65536
MAX_PARTS = 128
MAX_TURNS = 256
MAX_LABELS = 8
BATCH_CELLS = 3
HANDOFF_BYTES = 2359296
POLICY = {'schema': 'native-owned-cells-v1', 'rate': RATE,
          'cell_frames': CELL_FRAMES, 'core_frames': CORE_FRAMES,
          'selection': 'positive-half-open-distinct-epoch-local-label-overlap',
          'retention': 'first-selected-no-automatic-eviction',
          'eos': 'observed-native-finish-only', 'regeneration_execution': False}


class Unavailable(ValueError):
    """A bounded optional failure; must never replace the primary result."""


def integer(value, maximum=MAX_FRAME, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Unavailable('invalid_integer_geometry')
    return value


def text(value, maximum=128):
    if type(value) is not str or not value or len(value) > maximum:
        raise Unavailable('invalid_text')
    if len(value.encode('utf-8')) > maximum or '\0' in value:
        raise Unavailable('invalid_text')
    return value


def digest(value):
    if type(value) is not str or re.fullmatch('[a-f0-9]{64}', value) is None:
        raise Unavailable('invalid_digest')
    return value


def canonical(value, maximum=PROOF_BYTES):
    """Only bounded, validated internal structures may reach this serializer."""
    data = json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')
    if len(data) > maximum:
        error = Unavailable('serialization_capacity')
        error.encoded_bytes = len(data)
        error.maximum_bytes = maximum
        raise error
    return data


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


POLICY_SHA256 = sha(POLICY)


def exact_frame(seconds, *, origin=0., maximum=MAX_FRAME):
    if (type(seconds) not in (int, float) or not math.isfinite(seconds)
            or type(origin) not in (int, float) or not math.isfinite(origin)):
        raise Unavailable('invalid_frame_coordinate')
    frame = round((seconds - origin) * RATE)
    integer(frame, maximum)
    if seconds != origin + frame / RATE:
        raise Unavailable('fractional_frame_coordinate')
    return frame


def partition(start, end):
    integer(start); integer(end)
    if not 0 < end - start <= CORE_FRAMES:
        raise Unavailable('committed_core_capacity')
    return tuple((a, min(a + CELL_FRAMES, end)) for a in range(start, end, CELL_FRAMES))


def float32_digest(pcm):
    """Bounded conversion hash, without a second full float waveform."""
    if type(pcm) is not bytes or len(pcm) > CELL_PCM_BYTES or len(pcm) % 2:
        raise Unavailable('invalid_cell_pcm')
    result = hashlib.sha256()
    fast = sys.byteorder == 'little' and array('h').itemsize == 2 and array('f').itemsize == 4
    for at in range(0, len(pcm), IO_BYTES // 2):
        block = pcm[at:at + IO_BYTES // 2]
        if fast:
            samples = array('h'); samples.frombytes(block)
            floats = array('f', (n / 32768. for n in samples))
            result.update(floats)
            del samples, floats
        else:
            result.update(b''.join(struct.pack('<f', n[0] / 32768.)
                                   for n in struct.iter_unpack('<h', block)))
    return result.hexdigest()


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Content mapping and separate immutable authorization dependency."""
    session_id: str
    chunk_id: str
    receipt_sha256: str
    source_sha256: str
    source_frames: int
    source_start: int
    source_end: int
    logical_start: int
    logical_end: int

    def __post_init__(self):
        text(self.session_id); text(self.chunk_id)
        digest(self.receipt_sha256); digest(self.source_sha256)
        for name in ('source_frames', 'source_start', 'source_end', 'logical_start', 'logical_end'):
            integer(getattr(self, name))
        if (not 0 <= self.source_start < self.source_end <= self.source_frames
                or self.source_end - self.source_start != self.logical_end - self.logical_start):
            raise Unavailable('source_span_geometry')

    def content(self):
        return {k: getattr(self, k) for k in ('source_sha256', 'source_frames',
                'source_start', 'source_end', 'logical_start', 'logical_end')}


def validate_mapping(parts, start, end):
    if type(parts) is not tuple or not 1 <= len(parts) <= MAX_PARTS:
        raise Unavailable('source_part_capacity')
    cursor = start
    seen_chunks = set()
    sid = parts[0].session_id if type(parts[0]) is SourceSpan else None
    for part in parts:
        if type(part) is not SourceSpan or part.session_id != sid or part.logical_start != cursor:
            raise Unavailable('source_mapping_gap_or_alias')
        if part.chunk_id in seen_chunks:raise Unavailable('source_mapping_repeated_alias')
        seen_chunks.add(part.chunk_id)
        cursor = part.logical_end
    if cursor != end:
        raise Unavailable('source_mapping_incomplete')
    canonical([asdict(p) for p in parts])
    return parts


def intersect_mapping(parts, start, end):
    result = []
    for p in parts:
        a, b = max(start, p.logical_start), min(end, p.logical_end)
        if a < b:
            local = p.source_start + a - p.logical_start
            result.append(SourceSpan(p.session_id, p.chunk_id, p.receipt_sha256,
                p.source_sha256, p.source_frames, local, local + b - a, a, b))
    return validate_mapping(tuple(result), start, end)


@dataclass(frozen=True, slots=True)
class Turn:
    start: int
    end: int
    label: str

    def __post_init__(self):
        integer(self.start); integer(self.end); text(self.label)
        if self.start >= self.end:
            raise Unavailable('native_turn_geometry')


def selection(turns, start, end):
    """Preserve original intervals and intersections; touching is not overlap."""
    if type(turns) is not tuple or len(turns) > MAX_TURNS:
        raise Unavailable('native_turn_capacity')
    clipped = []
    labels = set()
    for t in turns:
        if type(t) is not Turn:
            raise Unavailable('native_turn_type')
        labels.add(t.label)
        if len(labels) > MAX_LABELS:
            raise Unavailable('native_label_capacity')
        a, b = max(start, t.start), min(end, t.end)
        if a < b:
            clipped.append({'label': t.label, 'original': [t.start, t.end], 'intersection': [a, b]})
    selected = any(a['label'] != b['label'] and
        max(a['intersection'][0], b['intersection'][0]) < min(a['intersection'][1], b['intersection'][1])
        for i, a in enumerate(clipped) for b in clipped[i + 1:])
    return selected, clipped


def validate_document(proof):
    if type(proof) is not bytes or not 1 <= len(proof) <= PROOF_BYTES:
        raise Unavailable('proof_capacity')
    doc = json.loads(proof)
    if type(doc) is dict and 'schema' in doc:
        from .native_owned_seam_cells import validate_document as validate_seam
        return validate_seam(proof)
    if type(doc) is not dict or set(doc) != {'policy', 'support', 'dependencies', 'native',
            'turns', 'selected', 'commit_evidence', 'commit'}:
        raise Unavailable('proof_shape')
    if doc['policy'] != POLICY_SHA256 or doc['selected'] is not True:
        raise Unavailable('proof_policy_or_selection')
    support = SourceSupport.from_dict(doc['support'])
    if type(doc['dependencies']) is not list or not 1 <= len(doc['dependencies']) <= MAX_PARTS:
        raise Unavailable('source_part_capacity')
    parts = tuple(SourceSpan(**p) for p in doc['dependencies'])
    validate_mapping(parts, support.start_frame, support.end_frame)
    commit = doc['commit']; evidence = doc['commit_evidence']; native = doc['native']
    if type(commit) is not dict or set(commit) != {'chunk_id', 'start', 'end', 'sha256'}:
        raise Unavailable('commit_shape')
    text(commit['chunk_id']); integer(commit['start']); integer(commit['end']); digest(commit['sha256'])
    if (type(evidence) is not dict or sha(evidence) != commit['sha256']
            or evidence.get('chunk_id') != commit['chunk_id'] or evidence.get('start') != commit['start']
            or evidence.get('end') != commit['end']
            or not 0 < commit['end'] - commit['start'] <= CORE_FRAMES
            or support.core_end_frame - support.core_start_frame != commit['end'] - commit['start']):
        raise Unavailable('commit_evidence_mismatch')
    if type(native) is not dict or set(native) != {'epoch_id', 'epoch_index', 'replay_start',
            'state', 'provenance', 'observed_action_count', 'true_eof'}:
        raise Unavailable('native_proof_shape')
    if type(native['epoch_id']) is not str or re.fullmatch('[a-f0-9]{32}', native['epoch_id']) is None:
        raise Unavailable('native_epoch_id')
    integer(native['epoch_index']); integer(native['replay_start']); integer(native['observed_action_count'], (1 << 31) - 1, 1)
    if type(native['true_eof']) is not bool:raise Unavailable('native_eof_type')
    from .native_owned_support import validate_observed_provenance
    validate_observed_provenance(native['provenance'])
    contents = [p.content() for p in parts]
    if (support.source_generation != sha({'schema': 'immutable-source-cell-generation-v1', 'sources': contents})
            or support.plan_sha256 != sha({'schema': 'bounded-cell-mapping-v1', 'parts': contents})
            or support.native_runtime_sha256 != sha(native['provenance'])
            or support.core_sha256 != sha({'start': support.core_start_frame,
                'end': support.core_end_frame, 'commit': commit['sha256'], 'native': native})):
        raise Unavailable('immutable_content_proof_mismatch')
    observed = doc['turns']
    if type(observed) is not list or len(observed) > MAX_TURNS:raise Unavailable('native_turn_capacity')
    original = tuple(Turn(t['original'][0], t['original'][1], t['label']) for t in observed)
    if any(t.label not in {native['epoch_id'] + ':speaker_' + str(i) for i in range(1, 9)} for t in original):
        raise Unavailable('native_local_label_mismatch')
    selected, clipped = selection(original, support.start_frame, support.end_frame)
    if not selected or clipped != observed:raise Unavailable('not_selected')
    if canonical(doc) != proof:raise Unavailable('noncanonical_proof')
    return doc, support, parts


@dataclass(frozen=True, slots=True)
class Cell:
    """An immutable selected candidate, not evidence of durable publication."""
    support: SourceSupport
    parts: tuple[SourceSpan, ...]
    proof: bytes
    pcm: bytes
    session_id: str
    chunk_id: str
    commit_start: int
    commit_end: int
    commit_sha256: str

    def __post_init__(self):
        if type(self.support) is not SourceSupport:
            raise Unavailable('support_contract_required')
        text(self.session_id); text(self.chunk_id); digest(self.commit_sha256)
        integer(self.commit_start); integer(self.commit_end)
        if not 0 < self.commit_end - self.commit_start <= CORE_FRAMES:
            raise Unavailable('commit_geometry')
        validate_mapping(self.parts, self.support.start_frame, self.support.end_frame)
        if any(p.session_id != self.session_id for p in self.parts):
            raise Unavailable('cell_session_mismatch')
        if type(self.proof) is not bytes or not 1 <= len(self.proof) <= PROOF_BYTES:
            raise Unavailable('proof_capacity')
        if (type(self.pcm) is not bytes or len(self.pcm) != 2 * (self.support.end_frame - self.support.start_frame)
                or len(self.pcm) > CELL_PCM_BYTES or hashlib.sha256(self.pcm).hexdigest() != self.support.pcm16le_sha256
                or float32_digest(self.pcm) != self.support.float32le_sha256):
            raise Unavailable('cell_pcm_mismatch')
        doc, support, parts = validate_document(self.proof)
        from .native_owned_seam_cells import anchor, is_composite, validate_cell_pcm
        if is_composite(doc):validate_cell_pcm(doc, self.pcm)
        if (support != self.support or parts != self.parts or anchor(doc) != {
                'chunk_id': self.chunk_id, 'start': self.commit_start,
                'end': self.commit_end, 'sha256': self.commit_sha256}):
            raise Unavailable('cell_proof_mismatch')

    @property
    def cell_id(self):
        # Authorization aliases are deliberately excluded from content identity.
        return sha({'policy': json.loads(self.proof)['policy'], 'support': self.support.to_dict()})

    @property
    def ownership(self):
        from .native_owned_seam_cells import is_composite, ownership
        doc = json.loads(self.proof)
        if is_composite(doc):return ownership(doc)
        return sha({'session': self.session_id, 'chunk': self.chunk_id,
            'core': [self.commit_start, self.commit_end],
            'cell': [self.support.start_frame, self.support.end_frame]})


class RollingWitness:
    """One epoch summary, one turns response, and a 96-second PCM ring.

    Never requests a reset, a finish, more context or another model invocation.
    Overflow disables optional capture until the next observed reset.
    """
    def __init__(self):
        self.failure = None
        self.epoch = None
        self.provenance = None
        self.state = None
        self.ring = bytearray()
        self.ring_start = 0
        self.ring_end = 0
        self.replay_start = None
        self.pending = None
        self.raw_turns = None
        self.actions = 0
        self.finished = False

    def fail(self, reason):
        self.failure = reason if type(reason) is str and re.fullmatch('[a-z_]{1,80}', reason) else 'observation_failed'
        self.ring.clear(); self.pending = None; self.raw_turns = None

    def observe_pcm(self, start, end, pcm):
        if self.failure:
            return
        try:
            integer(start); integer(end)
            if (self.epoch is None or self.finished or self.pending is not None
                    or type(pcm) is not bytes or len(pcm) != 2 * (end - start)
                    or not 0 < end - start <= 339200):
                raise Unavailable('native_pcm_sequence')
            if self.replay_start is None:
                self.replay_start = start; self.ring_start = start; self.ring_end = start
            if start != self.ring_end:
                raise Unavailable('native_pcm_discontinuity')
            self.ring.extend(pcm); self.ring_end = end
            excess = max(0, len(self.ring) - RING_PCM_BYTES)
            if excess:
                del self.ring[:excess]; self.ring_start += excess // 2
            self.pending = end - start
        except Exception as exc:
            self.fail(str(exc) if isinstance(exc, Unavailable) else 'native_pcm_observation_failed')

    def _state(self, value):
        if type(value) is not dict or set(value) != {'epoch_id', 'fingerprint', 'local_capacity',
                'input_seconds', 'processed_seconds', 'stable_seconds', 'finished'}:
            raise Unavailable('native_state_shape')
        if (value['epoch_id'] != self.epoch or type(value['local_capacity']) is not int
                or value['local_capacity'] != MAX_LABELS or type(value['finished']) is not bool
                or value['fingerprint'] != self.provenance['diarization_fingerprint']):
            raise Unavailable('native_state_identity')
        result = {'epoch_id': self.epoch, 'finished': value['finished'],
                  'fingerprint': value['fingerprint'], 'local_capacity': MAX_LABELS}
        for name in ('input', 'processed', 'stable'):
            raw = value[name + '_seconds']
            if type(raw) not in (int, float) or not math.isfinite(raw) or not 0 <= raw <= 360:
                raise Unavailable('native_frontier_bounds')
            result[name] = {'raw': raw, 'hex': float(raw).hex(), 'frame': round(raw * RATE)}
        if not result['stable']['frame'] <= result['processed']['frame'] <= result['input']['frame']:
            raise Unavailable('native_eof_deficit_or_frontier_order')
        return result

    def observe(self, response):
        try:
            if type(response) is not dict:
                raise Unavailable('native_response_shape')
            if set(response) == {'state', 'provenance'}:
                # A real producer reset is the sole way to start another epoch.
                from .native_owned_support import validate_observed_provenance
                provenance = validate_observed_provenance(response['provenance'])
                epoch = response['state'].get('epoch_id')
                if type(epoch) is not str or re.fullmatch('[a-f0-9]{32}', epoch) is None:
                    raise Unavailable('native_epoch_id')
                self.__init__(); self.epoch = epoch; self.provenance = provenance
                state = self._state(response['state'])
                if state['finished'] or any(state[k]['frame'] for k in ('input', 'processed', 'stable')):
                    raise Unavailable('nonempty_native_reset')
                self.state = state; self.actions = 1
                return
            if self.failure:
                return
            if self.state is None:
                raise Unavailable('unobserved_native_reset')
            if set(response) == {'turns'}:
                if self.pending is not None or type(response['turns']) is not list or len(response['turns']) > MAX_TURNS:
                    raise Unavailable('native_turn_capacity_or_sequence')
                bounded = []
                for t in response['turns']:
                    if type(t) is not dict or set(t) != {'start', 'end', 'speaker_local_id'}:
                        raise Unavailable('native_turn_shape')
                    a = exact_frame(t['start'], maximum=360 * RATE)
                    b = exact_frame(t['end'], maximum=360 * RATE)
                    label = text(t['speaker_local_id'])
                    if not a < b or label not in {f'{self.epoch}:speaker_{i}' for i in range(1, 9)}:
                        raise Unavailable('native_turn_label_or_geometry')
                    bounded.append({'start': t['start'], 'end': t['end'], 'speaker_local_id': label})
                self.raw_turns = tuple(bounded)
            else:
                state = self._state(response)
                if self.finished or any(state[k]['frame'] < self.state[k]['frame'] for k in ('input', 'processed', 'stable')):
                    raise Unavailable('native_frontier_reversed')
                if state['finished']:
                    if self.pending is not None or any(state[k]['frame'] != state['input']['frame'] for k in ('processed', 'stable')):
                        raise Unavailable('native_finish_not_true_complete_eof')
                elif self.pending is None or state['input']['frame'] - self.state['input']['frame'] != self.pending:
                    raise Unavailable('native_push_frame_mismatch')
                self.state = state; self.pending = None; self.finished = state['finished']
                self.raw_turns = None
            self.actions = min(self.actions + 1, (1 << 31) - 1)
        except Exception as exc:
            self.fail(str(exc) if isinstance(exc, Unavailable) else 'native_response_observation_failed')

    def read(self, start, end):
        if self.failure or self.pending is not None or not self.ring_start <= start < end <= self.ring_end:
            raise Unavailable(self.failure or 'native_ring_history_unavailable')
        return bytes(self.ring[(start - self.ring_start) * 2:(end - self.ring_start) * 2])

    def prepared(self, native, scope_start, origin_frame):
        """Reproduce the unchanged coordinator transform; retain raw intervals."""
        if self.failure or self.state is None or self.raw_turns is None or self.replay_start is None:
            raise Unavailable(self.failure or 'incomplete_native_witness')
        if type(native) is not dict or set(native) != {'end', 'extract_start', 'extract_end', 'turns', 'provenance', 'state'}:
            raise Unavailable('native_prepared_shape')
        if native['provenance'] != self.provenance:
            raise Unavailable('native_prepared_provenance')
        state = native['state']
        if (state.get('epoch_id') != self.epoch or state.get('finished') is not self.finished
                or state.get('provenance') != self.provenance
                or exact_frame(state['replay_start'], origin=scope_start) != self.replay_start):
            raise Unavailable('native_prepared_state')
        epoch_index = integer(state['epoch_index'])
        if self.replay_start != max(0, epoch_index * 300 * RATE - 339200):
            raise Unavailable('native_epoch_replay_geometry')
        if (exact_frame(state['owned_start'], origin=scope_start) != epoch_index * 300 * RATE
                or exact_frame(state['owned_end'], origin=scope_start) != (epoch_index + 1) * 300 * RATE):
            raise Unavailable('native_epoch_geometry')
        for name in ('input', 'processed', 'stable'):
            expected = scope_start + self.replay_start / RATE + self.state[name]['raw'] if name == 'input' else scope_start + (self.replay_start + self.state[name]['frame']) / RATE
            if state[name + '_until'] != expected:
                raise Unavailable('native_prepared_frontier_transform')
        a = origin_frame + exact_frame(native['extract_start']) if native['extract_start'] >= 0 else round(native['extract_start'] * RATE) + origin_frame
        b = origin_frame + exact_frame(native['extract_end'])
        if native['extract_start'] != (a - origin_frame) / RATE:
            raise Unavailable('fractional_extract_start')
        prepared, originals = [], []
        for t in self.raw_turns:
            lo = max(a / RATE, self.replay_start / RATE + t['start'])
            hi = min(b / RATE, self.replay_start / RATE + t['end'])
            if hi > lo:
                prepared.append(dict(t, start=lo - a / RATE, end=hi - a / RATE))
                originals.append(Turn(self.replay_start + exact_frame(t['start']),
                                      self.replay_start + exact_frame(t['end']), t['speaker_local_id']))
        if prepared != native['turns']:
            raise Unavailable('native_prepared_turn_transform')
        return tuple(originals), {'epoch_id': self.epoch, 'epoch_index': epoch_index,
            'replay_start': self.replay_start, 'state': self.state,
            'provenance': self.provenance, 'observed_action_count': self.actions,
            'true_eof': self.finished}


def make_cells(*, witness, native, scope_start, origin_frame, core_start, core_end,
               analyzer_start, analyzer_pcm, mapping, chunk_id, commit_start, commit_end, commit_sha256, commit_evidence):
    """Validate all selected evidence before offering anything to retention."""
    intervals = partition(core_start, core_end)
    validate_mapping(mapping, core_start, core_end)
    if type(analyzer_pcm) is not bytes or len(analyzer_pcm) > CORE_FRAMES * 2:
        raise Unavailable('analyzer_pcm_capacity')
    turns, native_proof = witness.prepared(native, scope_start, origin_frame)
    owned_start = native_proof['epoch_index'] * 300 * RATE
    nominal_end = origin_frame + exact_frame(native['end'])
    if (not owned_start <= core_start < core_end <= nominal_end <= owned_start + 300 * RATE
            or core_end > witness.replay_start + witness.state['stable']['frame']
            or core_end > witness.replay_start + witness.state['processed']['frame']
            or not analyzer_start <= core_start < core_end <= analyzer_start + len(analyzer_pcm) // 2):
        raise Unavailable('core_outside_witnessed_native_ownership')
    native_runtime = sha(native_proof['provenance'])
    core_hash = sha({'start': core_start, 'end': core_end, 'commit': commit_sha256,
                     'native': native_proof})
    result = []
    for a, b in intervals:
        chosen, overlaps = selection(turns, a, b)
        if not chosen:
            continue
        parts = intersect_mapping(mapping, a, b)
        pcm = analyzer_pcm[2 * (a - analyzer_start):2 * (b - analyzer_start)]
        if len(pcm) != 2 * (b - a) or pcm != witness.read(a, b):
            raise Unavailable('native_analyzer_pcm_mismatch')
        contents = [p.content() for p in parts]
        generation = sha({'schema': 'immutable-source-cell-generation-v1', 'sources': contents})
        plan_hash = sha({'schema': 'bounded-cell-mapping-v1', 'parts': contents})
        support = SourceSupport(generation, plan_hash, native_runtime, core_hash,
            core_end, core_start, core_end, a, b, hashlib.sha256(pcm).hexdigest(), float32_digest(pcm))
        proof = canonical({'policy': POLICY_SHA256, 'support': support.to_dict(),
            'dependencies': [asdict(p) for p in parts], 'native': native_proof, 'turns': overlaps,
            'selected': True, 'commit_evidence': commit_evidence, 'commit': {'chunk_id': chunk_id, 'start': commit_start,
                                          'end': commit_end, 'sha256': commit_sha256}})
        result.append(Cell(support, parts, proof, pcm, parts[0].session_id, chunk_id,
                           commit_start, commit_end, commit_sha256))
    if len(result) > BATCH_CELLS or sum(len(c.pcm) + len(c.proof) for c in result) > HANDOFF_BYTES:
        raise Unavailable('handoff_material_capacity')
    return tuple(result)
