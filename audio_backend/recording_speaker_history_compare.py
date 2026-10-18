"""Cooperative exact all-history comparison; no database, audio or model access.

The owner loads revision-bound, bounded pages, closes its read transaction, and
calls ``step`` under its separately accounted CPU/RAM grant. A ready build is
still only a candidate: the store must write all output pages and perform its
own complete-coverage/current-token CAS. Neither append nor capture may drive a
build synchronously. Retired observations remain ordinary competitors.

There is deliberately no observation-count/lifetime limit. A whole snapshot is
admitted by its owned-memory requirement and bounded work deadline; insufficient
resources refuse the optional comparison, never trim its competitor set.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import hashlib
import json
import math
import struct
import sys
import time

DIMENSION = 512
TAU, MARGIN = .55, .10
PAGE_ROWS, PAGE_BYTES = 32, 128 * 1024
# This is the flattened transport envelope, including immutable row identity
# and terminal vector provenance. The store's retained source/ref header keeps
# its separate unchanged 2048-byte ceiling. Three 64-character identifiers,
# a 32-character invocation, bounded kind/sequence, field names and framing
# consume less than the extra 1024 bytes; actual page/heap bytes are charged.
OBSERVATION_METADATA_BYTES = 3072
MAX_MEMORY_BYTES = 128 * 1024 * 1024
SCOPE_SECONDS = 180.
QUANTUM_SECONDS, QUANTUM_DOTS, QUANTUM_UPDATES = .050, 4096, 32768
CANCEL_DOTS, CANCEL_UPDATES = 16, 64
_SCORE_CHUNK = 4096
_BIT_CHUNK = 4096
_BASE_MEMORY = 1024 * 1024
_REASONS = ('missing_runner_up', 'exact_tie', 'below_tau', 'low_margin',
            'cannot_link_winner', 'not_reciprocal', 'source_overlap_dependency')


class Invalid(ValueError):
    """Malformed or inconsistent evidence; it cannot produce a decision."""


class Pending(RuntimeError):
    """The complete current decision is not available."""


class _Refused(RuntimeError):
    pass


class _Cancelled(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def _text(value, maximum=64):
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise Invalid('bounded_text')
    return value


def _integer(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise Invalid('bounded_integer')
    return value


def _sha(value):
    if (type(value) is not str or len(value) != 64
            or any(c not in '0123456789abcdef' for c in value)):
        raise Invalid('digest')
    return value


def pair_index(a, b):
    if a == b or min(a, b) < 0:
        raise Invalid('pair')
    if a > b:
        a, b = b, a
    return b * (b - 1) // 2 + a


@dataclass(frozen=True, slots=True)
class SnapshotToken:
    scope_id: str
    policy: str
    space_id: str
    history_revision: int
    authority_revision: int
    observation_count: int
    witness_count: int
    vector_count: int = 0

    def __post_init__(self):
        _sha(self.scope_id)
        _text(self.policy)
        _text(self.space_id, 256)
        for value in (self.history_revision, self.authority_revision,
                      self.observation_count, self.witness_count, self.vector_count):
            _integer(value)
        if self.vector_count > self.observation_count:
            raise Invalid('snapshot_vector_count')

    def document(self):
        return {name: getattr(self, name) for name in self.__slots__}

    @classmethod
    def from_record(cls, record):
        """Construct the immutable token from the store's exact header fields."""
        if isinstance(record, cls):
            return record
        if type(record) is not dict or set(record) != set(cls.__slots__):
            raise Invalid('snapshot_fields')
        return cls(**record)


@dataclass(frozen=True, slots=True)
class Observation:
    sequence: int
    observation_id: str
    evidence_kind: str
    invocation_id: str
    certificate_id: str | None
    node_id: str
    origin_kind: str
    source_key: str
    pcm_hash: str
    float_hash: str
    vector: bytes | None
    vector_sha256: str | None
    attributions: tuple
    spans: tuple
    refs: tuple

    @classmethod
    def from_record(cls, record):
        """Validate one bounded immutable store row without inference or I/O."""
        if isinstance(record, cls):
            row = record
        else:
            if type(record) is not dict or set(record) != set(cls.__slots__):
                raise Invalid('observation_fields')
            values = dict(record)
            for name in ('attributions', 'spans', 'refs'):
                value = values[name]
                if type(value) not in (tuple, list):
                    raise Invalid('metadata_shape')
                maximum = 1 if name == 'refs' else 4
                if len(value) > maximum:
                    raise Invalid('metadata_capacity')
                if any(type(part) not in (tuple, list) for part in value):
                    raise Invalid('metadata_shape')
                values[name] = tuple(tuple(part) for part in value)
            row = cls(**values)
        row.check()
        return row

    def metadata(self):
        return {name: getattr(self, name) for name in self.__slots__ if name != 'vector'}

    def check(self):
        _integer(self.sequence)
        if self.sequence == 0:
            raise Invalid('observation_sequence')
        for value in (self.observation_id, self.node_id, self.source_key,
                      self.pcm_hash, self.float_hash):
            _text(value)
        if self.evidence_kind == 'pending':
            raise Invalid('pending_observation')
        _text(self.invocation_id, 32)
        if len(self.invocation_id) != 32:
            raise Invalid('observation_invocation')
        if self.evidence_kind == 'candidate_missing':
            if any(value is not None for value in
                   (self.vector, self.vector_sha256, self.certificate_id)):
                raise Invalid('missing_observation_payload')
        elif self.evidence_kind == 'vector':
            _sha(self.certificate_id); _sha(self.vector_sha256)
            if self.vector is None:
                raise Invalid('vector_observation_payload')
        else:
            raise Invalid('evidence_kind')
        if self.origin_kind not in ('primary_commit', 'moss_result', 'primary', 'native_primary', 'moss'):
            raise Invalid('origin_kind')
        if (type(self.attributions) is not tuple or len(self.attributions) > 4
                or type(self.spans) is not tuple or len(self.spans) > 4
                or type(self.refs) is not tuple or len(self.refs) != 1):
            raise Invalid('metadata_capacity')
        for attr in self.attributions:
            if type(attr) is not tuple or len(attr) != 2:
                raise Invalid('attribution')
            _text(attr[0]); _text(attr[1])
        for span in self.spans:
            if type(span) is not tuple or len(span) != 4:
                raise Invalid('span')
            source, frames, start, end = span
            _text(source)
            for value in (frames, start, end):
                _integer(value)
            if not 0 <= start < end <= frames:
                raise Invalid('span')
        for ref in self.refs:
            if type(ref) is not tuple or len(ref) != 2:
                raise Invalid('reference')
            kind, key = ref
            if kind not in ('primary', 'native_primary', 'moss'):
                raise Invalid('reference_kind')
            _text(key, 192)
            # A genuine MOSS result may carry a primary-attributed certificate.
            # Producer identity is not the reference/annotation stream.
        if len(canonical(self.metadata())) > OBSERVATION_METADATA_BYTES:
            raise Invalid('metadata_bytes')
        if self.vector is not None:
            if (sys.byteorder != 'little' or array('d').itemsize != 8
                    or type(self.vector) is not bytes or len(self.vector) != DIMENSION * 4):
                raise Invalid('vector_bytes_or_host')
            if hashlib.sha256(self.vector).hexdigest() != self.vector_sha256:
                raise Invalid('vector_digest')
            values = memoryview(self.vector).cast('f')
            if (any(not math.isfinite(value) for value in values)
                    or abs(math.fsum(value * value for value in values) - 1.) >= 1e-5):
                raise Invalid('vector_norm')

    def fingerprint(self):
        header = canonical(self.metadata())
        return hashlib.sha256(struct.pack('<I', len(header)) + header
                              + (b'\x00' if self.vector is None else b'\x01' + self.vector)).digest()

    def owned_bytes(self):
        # Count bounded row-owned objects, conservatively counting shared strings
        # more than once, including every transport field at its actual size.
        # Extra space covers row maps and per-row alias/DAG state; it is not a
        # fixed allowance substituting for serialized metadata ownership.
        def size(value):
            return sys.getsizeof(value) + (sum(size(v) for v in value)
                if type(value) is tuple else 0)
        return sys.getsizeof(self) + sum(size(getattr(self, name))
            for name in self.__slots__) + 2048


class PackedScores:
    """Float64 triangle in fixed-size allocations; no large realloc/copy step."""
    def __init__(self):
        self.chunks, self.length = [], 0

    def __len__(self):
        return self.length

    def __getitem__(self, index):
        if not 0 <= index < self.length:
            raise Invalid('score_index')
        return self.chunks[index // _SCORE_CHUNK][index % _SCORE_CHUNK]

    def append(self, value):
        offset = self.length % _SCORE_CHUNK
        if offset == 0:
            self.chunks.append(array('d', [0.]) * _SCORE_CHUNK)
        self.chunks[-1][offset] = value
        self.length += 1

    def truncate(self, length):
        if not 0 <= length <= self.length:
            raise Invalid('score_prefix_length')
        while len(self.chunks) > (length + _SCORE_CHUNK - 1) // _SCORE_CHUNK:
            self.chunks.pop()
        self.length = length

    @property
    def owned_bytes(self):
        return len(self.chunks) * (_SCORE_CHUNK * 8 + 128) + 128


class PackedBits:
    """Sparse fixed-sized bit chunks, with full dense size reserved up front."""
    def __init__(self):
        self.chunks = {}

    def get(self, index):
        byte = index >> 3
        chunk = self.chunks.get(byte // _BIT_CHUNK)
        return bool(chunk is not None and chunk[byte % _BIT_CHUNK] & (1 << (index & 7)))

    def set(self, index):
        byte = index >> 3
        key = byte // _BIT_CHUNK
        if key not in self.chunks:
            self.chunks[key] = bytearray(_BIT_CHUNK)
        self.chunks[key][byte % _BIT_CHUNK] |= 1 << (index & 7)


class Matrix:
    def __init__(self, n):
        self.n, self.scores = n, PackedScores()
        self.cannot, self.overlap = PackedBits(), PackedBits()

    @classmethod
    def from_scores(cls, scores, n, cannot=(), overlap=()):
        """Small synthetic oracle adapter; production uses incremental matrices."""
        if type(n) is not int or not 0 <= n <= 32:
            raise Invalid('small_oracle_only')
        if len(scores) != n * (n - 1) // 2:
            raise Invalid('matrix_size')
        result = cls(n)
        for b in range(n):
            for a in range(b):
                value = scores[(a, b)]
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise Invalid('matrix_nonfinite')
                result.scores.append(value)
        for pairs, bits in ((cannot, result.cannot), (overlap, result.overlap)):
            for a, b in pairs:
                if not 0 <= a < b < n:
                    raise Invalid('dependency_pair')
                bits.set(pair_index(a, b))
        return result


class ScorePrefix:
    """One owner's recomputable cache, never durable evidence or authority.

    Reuse requires matching scope/policy/space and every retained row's exact
    metadata/vector fingerprint. Witness and authority changes do not reuse
    alias/linkage decisions. Memory must remain charged even while idle.
    """
    def __init__(self, token):
        self.binding = (token.scope_id, token.policy, token.space_id)
        self.fingerprints, self.vector_slots = [], []
        self.vector_count = 0
        self.scores = PackedScores()

    @property
    def owned_bytes(self):
        return self.scores.owned_bytes + len(self.fingerprints) * 160 + 256

    @property
    def row_count(self):
        return len(self.fingerprints)

    def repair_incomplete_tail(self):
        self.scores.truncate(self.vector_count * (self.vector_count - 1) // 2)
        if len(self.vector_slots) != self.row_count:
            raise Invalid('score_prefix_shape')


class Work:
    """Cooperative checkpoints, not a real-time scheduler or a CPU duty claim."""
    def __init__(self, *, clock=time.monotonic, cancelled=lambda: False,
                 grant_valid=lambda: True, deadline=None):
        self.clock, self.cancelled, self.grant_valid = clock, cancelled, grant_valid
        self.deadline = clock() + SCOPE_SECONDS if deadline is None else deadline
        self.counts = {}
        self.paused = False
        self.begin()

    def begin(self):
        self.started = self.clock()
        self.dots = self.updates = self.check_dots = self.check_updates = 0
        self.paused = False

    def check(self):
        if self.cancelled():
            raise _Cancelled('cancelled')
        if self.clock() >= self.deadline:
            raise _Refused('scope_deadline')
        self.paused = not self.grant_valid()
        return self.paused

    def tick(self, phase, *, dot=False):
        self.counts[phase] = self.counts.get(phase, 0) + 1
        if dot:
            self.dots += 1; self.check_dots += 1
        else:
            self.updates += 1; self.check_updates += 1
        checkpoint = self.check_dots >= CANCEL_DOTS or self.check_updates >= CANCEL_UPDATES
        if checkpoint:
            self.check_dots = self.check_updates = 0
            self.counts['checkpoints'] = self.counts.get('checkpoints', 0) + 1
            if self.check():
                return True
        return (self.dots >= QUANTUM_DOTS or self.updates >= QUANTUM_UPDATES
                or self.clock() - self.started >= QUANTUM_SECONDS)


@dataclass(frozen=True, slots=True)
class Merge:
    round: int
    left: int
    right: int


class Trace:
    def __init__(self, n):
        self.n, self.merges, self.rounds = n, [], []

    def members(self, node):
        """Expand on demand; production expansion adds its own work checkpoint."""
        pending = [node]
        while pending:
            item = pending.pop()
            if item < self.n:
                yield item
            else:
                event = self.merges[item - self.n]
                pending.extend((event.right, event.left))

    def oracle_trace(self):
        if self.n > 32:
            raise Invalid('small_oracle_only')
        return [dict(round=index, cluster_count=count, reasons=dict(reasons),
                     merges=[[sorted(self.members(event.left)), sorted(self.members(event.right))]
                             for event in self.merges[lo:hi]])
                for index, (count, reasons, lo, hi) in enumerate(self.rounds)]


def cluster(matrix, work):
    """Exact synchronous complete-link rounds; every competitor ranks first.

    Disjoint clusters have distinct minimum original indices, so ordering by
    that minimum is exactly the original lexicographic member-tuple ordering.
    The merge DAG avoids repeatedly expanding/copying all member lists.
    """
    n = matrix.n
    trace = Trace(n)
    nodes, minimum = [], []
    for i in range(n):
        nodes.append(i); minimum.append(i)
        if work.tick('cluster_initialization'):
            yield None
    for round_number in range(max(1, n)):
        k = len(nodes)
        best, second, proposals = [], [], []
        for _ in range(k):
            best.append(None); second.append(None); proposals.append(-1)
            if work.tick('ranking_initialization'):
                yield None
        for b in range(k):
            for a in range(b):
                score = matrix.scores[pair_index(a, b)]
                for one, other in ((a, b), (b, a)):
                    value = (-score, minimum[other], other)
                    if best[one] is None or value < best[one]:
                        second[one], best[one] = best[one], value
                    elif second[one] is None or value < second[one]:
                        second[one] = value
                    if work.tick('ranking_updates'):
                        yield None
        reasons = dict.fromkeys(_REASONS, 0)
        for i in range(k):
            reason = None
            if second[i] is None:
                reason = 'missing_runner_up'
            else:
                first, runner = best[i], second[i]
                j, r = first[2], runner[2]
                score, runner_score = -first[0], -runner[0]
                if score == runner_score:
                    reason = 'exact_tie'
                elif score < TAU:
                    reason = 'below_tau'
                elif score - runner_score < MARGIN:
                    reason = 'low_margin'
                elif matrix.cannot.get(pair_index(i, j)):
                    reason = 'cannot_link_winner'
                elif (matrix.overlap.get(pair_index(i, j))
                      or matrix.overlap.get(pair_index(i, r))
                      or matrix.overlap.get(pair_index(j, r))):
                    reason = 'source_overlap_dependency'
                else:
                    proposals[i] = j
            if reason:
                reasons[reason] += 1
            if work.tick('proposal_checks'):
                yield None
        lo = len(trace.merges)
        replacements, next_nodes, next_minimum = [], [], []
        for i, j in enumerate(proposals):
            reciprocal = j >= 0 and proposals[j] == i
            if j >= 0 and not reciprocal:
                reasons['not_reciprocal'] += 1
            if reciprocal and i < j:
                pair = pair_index(i, j)
                if matrix.cannot.get(pair) or matrix.scores[pair] < TAU:
                    raise Invalid('complete_link_violation')
                trace.merges.append(Merge(round_number, nodes[i], nodes[j]))
                replacements.append((i, j)); next_nodes.append(n + len(trace.merges) - 1)
                next_minimum.append(minimum[i])
            elif not reciprocal:
                replacements.append((i,)); next_nodes.append(nodes[i]); next_minimum.append(minimum[i])
            if work.tick('merge_updates'):
                yield None
        trace.rounds.append((k, tuple(reasons.items()), lo, len(trace.merges)))
        work.counts['clustering_rounds'] = work.counts.get('clustering_rounds', 0) + 1
        if len(trace.merges) == lo:
            return nodes, trace
        updated = Matrix(len(replacements))
        for b in range(len(replacements)):
            for a in range(b):
                score, cannot, overlap = math.inf, False, False
                for i in replacements[a]:
                    for j in replacements[b]:
                        old = pair_index(i, j)
                        score = min(score, matrix.scores[old])
                        cannot |= matrix.cannot.get(old)
                        overlap |= matrix.overlap.get(old)
                        if work.tick('linkage_crosspair_updates'):
                            yield None
                updated.scores.append(score)
                pair = pair_index(a, b)
                if cannot:
                    updated.cannot.set(pair)
                if overlap:
                    updated.overlap.set(pair)
                if work.tick('linkage_pair_updates'):
                    yield None
        matrix, nodes, minimum = updated, next_nodes, next_minimum
    raise Invalid('round_invariant')


def _sorted_indices(rows, work):
    """Checkpointed stable merge sort; no hidden whole-history sort call."""
    source, target = [], []
    for i in range(len(rows)):
        source.append(i); target.append(0)
        if work.tick('sort_initialization'):
            yield None
    width, n = 1, len(source)
    while width < n:
        for start in range(0, n, 2 * width):
            mid, end = min(start + width, n), min(start + 2 * width, n)
            left, right = start, mid
            for out in range(start, end):
                if right == end or (left < mid and rows[source[left]].node_id <= rows[source[right]].node_id):
                    target[out] = source[left]; left += 1
                else:
                    target[out] = source[right]; right += 1
                if work.tick('sort_updates'):
                    yield None
        source, target = target, source
        width *= 2
    return source


class ComparisonBuild:
    """Single-owner, paged and resumable exact snapshot candidate.

    ``grant_valid`` false pauses without killing generator/cache state. It is
    distinct from ``cancelled`` (stop/source invalidation), which terminates the
    build. The deadline is wall time, including pauses; a deadline never turns a
    partial result current. The owner retains/relinquishes its memory token only
    after actual task completion/join and cache disposal, outside this class.
    """
    def __init__(self, token, *, admitted_bytes, cancelled=lambda: False,
                 grant_valid=lambda: True, deadline=None, prefix=None, clock=time.monotonic):
        self.token = SnapshotToken.from_record(token)
        if type(admitted_bytes) is not int or not 0 < admitted_bytes <= MAX_MEMORY_BYTES:
            raise Invalid('memory_grant')
        self.admitted_bytes = admitted_bytes
        self.work = Work(clock=clock, cancelled=cancelled, grant_valid=grant_valid, deadline=deadline)
        self._prefix = ScorePrefix(self.token) if prefix is None else prefix
        if (type(self._prefix) is not ScorePrefix
                or self._prefix.binding != (self.token.scope_id, self.token.policy, self.token.space_id)
                or self._prefix.row_count > self.token.observation_count):
            raise Invalid('score_prefix_binding')
        self._prefix.repair_incomplete_tail()
        self._old_rows = self._prefix.row_count
        self.rows, self.witnesses = [], set()
        self._node_ids, self._observation_ids, self._certificate_ids = set(), set(), set()
        self._loaded_vectors = 0
        self._witness_after = 0
        self._observations_complete = self._witnesses_complete = False
        self._loaded_bytes = _BASE_MEMORY
        self.required_bytes = self._loaded_bytes + self._prefix.owned_bytes
        self.state, self.reason = 'loading', None
        self._operation = None
        self._trace = None
        self._physical, self._row_physical = [], []
        self._physical_cluster, self._identities = [], []
        self._total_rows = 0
        self._check_memory(self.required_bytes)

    @property
    def score_prefix(self):
        """Return the sole cache; the caller must retain its RAM charge."""
        if self.state == 'discarded':
            raise Pending('discarded')
        return self._prefix

    def _check_memory(self, required):
        self.required_bytes = max(self.required_bytes, required)
        if required > self.admitted_bytes:
            self.state, self.reason = 'refused', 'comparison_memory'
            raise _Refused(self.reason)

    def _page_start(self, token, after, rows, complete, *, witness=False):
        if self.state not in ('loading', 'paused') or self._operation is not None:
            raise Pending('not_loading')
        if SnapshotToken.from_record(token) != self.token:
            raise Invalid('stale_page')
        if type(rows) not in (tuple, list) or len(rows) > PAGE_ROWS or type(complete) is not bool:
            raise Invalid('page_shape')
        current = self._witness_after if witness else len(self.rows)
        maximum = self.token.witness_count if witness else self.token.observation_count
        already = self._witnesses_complete if witness else self._observations_complete
        if type(after) is not int or after != current or already or after + len(rows) > maximum:
            raise Invalid('page_cursor')
        if complete != (after + len(rows) == maximum) or (not rows and not complete):
            raise Invalid('incomplete_scan')
        return len(canonical({'token': self.token.document(), 'after': after,
            'complete': complete, 'count': len(rows)})) + 4

    def feed_observations(self, token, after, rows, *, complete):
        """Feed one contiguous ≤32-row/128KiB page after its DB read closes.

        Sequence must be exactly after+1 onward. An empty page is legal only for
        the declared empty/complete scan. Prefix fingerprints are checked even
        on warm builds; incomplete coverage can never enter computation.
        """
        size = self._page_start(token, after, rows, complete)
        checked = []
        ids, observations, certificates = set(), set(), set()
        vector_count = self._loaded_vectors
        for offset, record in enumerate(rows):
            row = Observation.from_record(record)
            if row.sequence != after + offset + 1:
                raise Invalid('observation_sequence')
            if (row.node_id in self._node_ids or row.node_id in ids
                    or row.observation_id in self._observation_ids or row.observation_id in observations
                    or (row.certificate_id is not None and
                        (row.certificate_id in self._certificate_ids or row.certificate_id in certificates))):
                raise Invalid('duplicate_observation')
            ids.add(row.node_id); observations.add(row.observation_id)
            if row.certificate_id is not None:
                certificates.add(row.certificate_id)
            vector_count += row.vector is not None
            if after + offset < self._old_rows and row.fingerprint() != self._prefix.fingerprints[after + offset]:
                raise Invalid('score_prefix_row_mismatch')
            size += 8 + len(canonical(row.metadata())) + (len(row.vector) if row.vector is not None else 0)
            checked.append(row)
        if (vector_count > self.token.vector_count
                or (complete and vector_count != self.token.vector_count)):
            raise Invalid('vector_count_mismatch')
        if size > PAGE_BYTES:
            raise Invalid('observation_page_bytes')
        new_bytes = sum(row.owned_bytes() for row in checked)
        self._check_memory(self._loaded_bytes + new_bytes + self._prefix.owned_bytes)
        self.rows.extend(checked); self._node_ids.update(ids); self._observation_ids.update(observations)
        self._certificate_ids.update(certificates); self._loaded_vectors = vector_count
        self._loaded_bytes += new_bytes
        self._observations_complete = complete

    def feed_witnesses(self, token, after, rows, *, complete):
        """Feed exact native_scope_id/labels/proof_sha256 rows in token order.

        Declared coverage counts input rows, including duplicate hard witnesses;
        deduplication changes neither that count nor the retained semantic veto.
        """
        size = self._page_start(token, after, rows, complete, witness=True)
        checked, new_bytes = [], 0
        for row in rows:
            if type(row) is not dict or set(row) != {'native_scope_id', 'labels', 'proof_sha256'}:
                raise Invalid('witness_fields')
            _text(row['native_scope_id']); _sha(row['proof_sha256'])
            labels = row['labels']
            if type(labels) not in (tuple, list) or len(labels) != 2 or labels[0] == labels[1]:
                raise Invalid('witness_labels')
            _text(labels[0]); _text(labels[1])
            key = (row['native_scope_id'], tuple(sorted(labels)))
            checked.append(key)
            encoded = canonical(row)
            size += 4 + len(encoded)
            new_bytes += 512 + len(encoded) * 2
        if size > PAGE_BYTES:
            raise Invalid('witness_page_bytes')
        self._check_memory(self._loaded_bytes + new_bytes + self._prefix.owned_bytes)
        self.witnesses.update(checked)
        self._loaded_bytes += new_bytes
        self._witness_after += len(rows)
        self._witnesses_complete = complete

    def _status(self):
        return {'state': self.state, 'reason': self.reason, 'counts': dict(self.work.counts),
                'required_bytes': self.required_bytes, 'loaded_rows': len(self.rows),
                'loaded_vectors': self._loaded_vectors, 'loaded_witnesses': self._witness_after}

    def step(self):
        """Advance one cooperative quantum outside all DB/owner/admission locks.

        Grant loss returns paused and preserves the exact program counter.
        Cancellation/deadline/refusal cannot yield output pages. Counters bound
        each quantum, with 50ms as a cooperative wall target plus one primitive
        operation/scheduling tail; it is not a hard real-time guarantee.
        """
        if self.state in ('ready', 'refused', 'cancelled', 'discarded'):
            return self._status()
        try:
            self.work.begin()
            if self.work.check():
                self.state = 'paused'
                return self._status()
            if not (self._observations_complete and self._witnesses_complete):
                self.state = 'loading'
                return self._status()
            if self._operation is None:
                self._operation = self._compute()
            self.state = 'pending'
            try:
                next(self._operation)
            except StopIteration:
                self.work.check()
                self.state = 'paused' if self.work.paused else 'ready'
                if self.state == 'ready':
                    self._operation = None
                return self._status()
            self.state = 'paused' if self.work.paused else 'pending'
        except _Cancelled as exc:
            self.state, self.reason = 'cancelled', str(exc)
            self._close_operation()
        except (_Refused, Invalid) as exc:
            self.state, self.reason = 'refused', str(exc)
            self._close_operation()
        return self._status()

    def _close_operation(self):
        if self._operation is not None:
            self._operation.close()
            self._operation = None

    def _compute(self):
        work, rows, prefix = self.work, self.rows, self._prefix
        vectors = []
        # Memory forecast covers all vector-bearing rows, even exact aliases:
        # raw prefix plus two dense linkage triangles, every bit plane, chunk
        # slack, sorting/DAG/index buffers and bounded publication pages. The
        # separate candidate bit planes include missing-vector classes too.
        vector_count = 0
        for row in rows:
            vector_count += row.vector is not None
            if work.tick('admission_scan'):
                yield None
        pairs = vector_count * (vector_count - 1) // 2
        candidate_pairs = len(rows) * (len(rows) - 1) // 2
        self._check_memory(self._loaded_bytes + 25 * pairs + 2 * ((candidate_pairs + 7) // 8) + 1024 * len(rows)
                           + 12 * _SCORE_CHUNK * 8 + 4 * PAGE_BYTES)
        for index, row in enumerate(rows):
            if index < self._old_rows:
                slot = prefix.vector_slots[index]
                expected = len(vectors) if row.vector is not None else -1
                if slot != expected:
                    raise Invalid('score_prefix_slot')
                if row.vector is not None:
                    vectors.append(row.vector)
            else:
                slot = -1
                if row.vector is not None:
                    vector = memoryview(row.vector).cast('f')
                    for previous in vectors:
                        other = memoryview(previous).cast('f')
                        prefix.scores.append(math.fsum(x * y for x, y in zip(vector, other)))
                        if work.tick('dot_products', dot=True):
                            yield None
                    slot = len(vectors)
                    vectors.append(row.vector)
                    prefix.vector_count += 1
                prefix.vector_slots.append(slot)
                prefix.fingerprints.append(row.fingerprint())
            if work.tick('prefix_rows'):
                yield None
        if len(prefix.scores) != vector_count * (vector_count - 1) // 2:
            raise Invalid('score_prefix_incomplete')
        ordered = yield from _sorted_indices(rows, work)
        parent, sizes = [], []
        for index in range(len(rows)):
            parent.append(index); sizes.append(1); self._row_physical.append(-1)
            if work.tick('alias_initialization'):
                yield None

        def root(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
                if work.tick('alias_root_updates'):
                    yield None
            return index

        source, pcm = {}, {}
        for index, row in enumerate(rows):
            for mapping, key in ((source, row.source_key), (pcm, row.pcm_hash)):
                if key in mapping:
                    left = yield from root(index)
                    right = yield from root(mapping[key])
                    if left != right:
                        if sizes[left] < sizes[right]:
                            left, right = right, left
                        parent[right] = left; sizes[left] += sizes[right]
                else:
                    mapping[key] = index
                if work.tick('alias_key_updates'):
                    yield None
        classes = {}
        for index in ordered:
            representative = yield from root(index)
            classes.setdefault(representative, []).append(index)
            if work.tick('alias_class_updates'):
                yield None
        attrs, spans, vector_slots = [], [], []
        unknown_rival = False
        for group in classes.values():
            exemplar, attributes, intervals = None, [], []
            first = rows[group[0]]
            for index in group:
                row = rows[index]
                if row.pcm_hash != first.pcm_hash or row.float_hash != first.float_hash:
                    raise Invalid('duplicate_pcm_disagreement')
                for attr in row.attributions:
                    attributes.append(attr)
                    if work.tick('alias_attributions'):
                        yield None
                for span in row.spans:
                    intervals.append(span)
                    if work.tick('alias_spans'):
                        yield None
                if row.vector is not None:
                    if exemplar is None:
                        exemplar = index
                    else:
                        for one, two in zip(memoryview(rows[exemplar].vector).cast('f'), memoryview(row.vector).cast('f')):
                            if one != two:
                                raise Invalid('duplicate_vector_disagreement')
                            if work.tick('alias_vector_components'):
                                yield None
                if work.tick('alias_rows'):
                    yield None
            for scope, label in attributes:
                for other_scope, other_label in attributes:
                    if scope == other_scope and (scope, tuple(sorted((label, other_label)))) in self.witnesses:
                        raise Invalid('duplicate_native_conflict')
                    if work.tick('alias_witness_checks'):
                        yield None
            # Every genuine candidate survives authenticated alias closure.
            # Missing vectors are not absent observations and cannot silently
            # improve the measured groups' runner-up margin.
            physical = len(self._physical)
            self._physical.append(group); attrs.append(attributes); spans.append(intervals)
            vector_slots.append(-1 if exemplar is None else prefix.vector_slots[exemplar])
            unknown_rival |= exemplar is None
            for index in group:
                self._row_physical[index] = physical
                if work.tick('physical_alias_updates'):
                    yield None
        matrix = Matrix(len(self._physical))
        for b in range(matrix.n):
            for a in range(b):
                cannot, overlap = False, False
                for scope, label in attrs[a]:
                    for other_scope, other_label in attrs[b]:
                        cannot |= scope == other_scope and (scope, tuple(sorted((label, other_label)))) in self.witnesses
                        if work.tick('cross_witness_checks'):
                            yield None
                for source, frames, lo, hi in spans[a]:
                    for other_source, other_frames, other_lo, other_hi in spans[b]:
                        overlap |= (source == other_source and frames == other_frames
                                    and max(lo, other_lo) < min(hi, other_hi))
                        if work.tick('source_span_checks'):
                            yield None
                pair = pair_index(a, b)
                if cannot:
                    matrix.cannot.set(pair)
                if overlap:
                    matrix.overlap.set(pair)
                if work.tick('matrix_pair_updates'):
                    yield None
        # Alias disagreements and all hard-witness/dependency relationships
        # above are checked before considering a missing candidate resolved.
        # Only a fully authenticated measured physical alias can supply its
        # score; an independent unmeasured class blocks the entire projection.
        if unknown_rival:
            raise _Refused('unknown_rival')
        for b in range(matrix.n):
            for a in range(b):
                matrix.scores.append(prefix.scores[pair_index(vector_slots[a], vector_slots[b])])
                if work.tick('matrix_score_updates'):
                    yield None
        operation = cluster(matrix, work)
        matrix = None  # Do not pin the original matrix across later rounds.
        nodes, self._trace = yield from operation
        # Empty histories need no projection rows (small oracle still records
        # its conventional empty round). No source is promoted by that case.
        if not rows:
            self._trace.rounds.clear()
        sizes = []
        for _ in self._physical:
            self._physical_cluster.append(-1)
            if work.tick('projection_initialization'):
                yield None
        for cluster_id, node in enumerate(nodes):
            size, pending = 0, [node]
            while pending:
                item = pending.pop()
                if item < self._trace.n:
                    self._physical_cluster[item] = cluster_id
                    size += 1
                else:
                    event = self._trace.merges[item - self._trace.n]
                    pending.extend((event.right, event.left))
                if work.tick('projection_dag_updates'):
                    yield None
            sizes.append(size)
        hashes, separators = [], []
        for size in sizes:
            hashes.append(hashlib.sha256(b'{"members":[') if size > 1 else None)
            separators.append(False)
            if work.tick('identity_initialization'):
                yield None
        for index in ordered:
            physical = self._row_physical[index]
            if physical >= 0:
                cluster_id = self._physical_cluster[physical]
                hasher = hashes[cluster_id]
                if hasher is not None:
                    if separators[cluster_id]:
                        hasher.update(b',')
                    hasher.update(canonical(rows[index].node_id))
                    separators[cluster_id] = True
            if work.tick('identity_member_updates'):
                yield None
        tail = b'],"policy":' + canonical(self.token.policy) + b',"scope_id":' + canonical(self.token.scope_id) + b'}'
        for hasher in hashes:
            if hasher is not None:
                hasher.update(tail)
                identity = 'recording-speaker-' + hasher.hexdigest()[:20]
            else:
                identity = None
            self._identities.append(identity)
            if work.tick('identity_finalization'):
                yield None
        self._total_rows = len(rows) + len(self._trace.merges) + len(self._trace.rounds)
        # Validate every output row incrementally before declaring ready. There
        # is no late first-page success followed by hidden oversized evidence.
        for index in range(self._total_rows):
            self._output_row(index)
            if work.tick('publication_row_validation'):
                yield None

    @property
    def decision_counts(self):
        """Expected exact publication counts, available only after completion."""
        if self.state != 'ready':
            raise Pending(self.reason or 'comparison_pending')
        return {'annotation': len(self.rows), 'merge': len(self._trace.merges),
                'round': len(self._trace.rounds), 'total_rows': self._total_rows,
                'independent_observations': len(self._physical)}

    def _output_row(self, index):
        n, merges = len(self.rows), len(self._trace.merges)
        if index < n:
            row = self.rows[index]
            physical = self._row_physical[index]
            cluster_id = self._physical_cluster[physical] if physical >= 0 and row.vector is not None else -1
            identity = self._identities[cluster_id] if cluster_id >= 0 else None
            body = {'sequence': row.sequence, 'status': ('provisional_linked' if identity else 'source_scoped')
                if cluster_id >= 0 else 'probe_unavailable', 'provisional_speaker_id': identity,
                'duplicate_evidence': physical >= 0 and row.vector is not None and len(self._physical[physical]) > 1,
                'speaker_identity_valid': False, 'cluster': cluster_id}
            kind, key = row.refs[0]
            kind = 'primary' if kind == 'native_primary' else kind
            result = {'kind': 'annotation', 'ordinal': row.sequence, 'ref_kind': kind, 'ref_key': key, 'body': body}
            maximum = 512
        elif index < n + merges:
            ordinal = index - n
            event = self._trace.merges[ordinal]
            result = {'kind': 'merge', 'ordinal': index + 1, 'ref_kind': None, 'ref_key': None,
                      'body': {'id': self._trace.n + ordinal, 'round': event.round, 'left': event.left, 'right': event.right}}
            maximum = 128
        else:
            ordinal = index - n - merges
            count, reasons, lo, hi = self._trace.rounds[ordinal]
            result = {'kind': 'round', 'ordinal': index + 1, 'ref_kind': None, 'ref_key': None,
                      'body': {'round': ordinal, 'cluster_count': count, 'reasons': dict(reasons), 'merge_range': [lo, hi]}}
            maximum = 512
        # Store charge limits apply to the canonical body; page accounting also
        # includes typed keys/ordinals/token and never truncates an exact ref.
        if len(canonical(result['body'])) > maximum:
            raise Invalid('decision_row_bytes')
        return result

    def output_page(self, after=0, limit=PAGE_ROWS):
        """Return ≤32 complete rows and ≤128KiB including the actual envelope.

        The store owns final revision validation/CAS. A callback cancellation or
        deadline is nevertheless checked here to reject stale task publication.
        """
        if self.state != 'ready':
            raise Pending(self.reason or 'comparison_pending')
        try:
            if self.work.check():
                raise Pending('comparison_grant_paused')
        except (_Cancelled, _Refused) as exc:
            self.state = 'cancelled' if isinstance(exc, _Cancelled) else 'refused'
            self.reason = str(exc)
            raise Pending(str(exc)) from exc
        if (type(after) is not int or not 0 <= after <= self._total_rows
                or type(limit) is not int or not 1 <= limit <= PAGE_ROWS):
            raise Invalid('output_cursor')
        answer = {'token': self.token.document(), 'rows': [], 'next_after': after,
                  'complete': after == self._total_rows, 'total_rows': self._total_rows}
        for index in range(after, min(self._total_rows, after + limit)):
            row = self._output_row(index)
            answer['rows'].append(row)
            answer['next_after'], answer['complete'] = index + 1, index + 1 == self._total_rows
            if len(canonical(answer)) > PAGE_BYTES:
                answer['rows'].pop()
                answer['next_after'], answer['complete'] = index, index == self._total_rows
                break
        if not answer['rows'] and after < self._total_rows:
            raise Invalid('decision_page_bytes')
        return answer

    def discard(self):
        """Drop this owner's recomputable memory after positive thread join.

        This method does not release a permit or durable evidence. The manager
        must not call it concurrently with step/feed/output or an unknown task.
        """
        self._close_operation()
        self.rows.clear(); self.witnesses.clear()
        self._node_ids.clear(); self._observation_ids.clear(); self._certificate_ids.clear()
        self._physical.clear(); self._row_physical.clear(); self._physical_cluster.clear(); self._identities.clear()
        self._trace = self._prefix = None
        self.state, self.reason = 'discarded', None
