"""Isolated model-free prototype. Not a product store, authority or migration.

Original float32 rows and exact fsum float64 scores are never averaged. Every
physical competitor participates in synchronous complete-link rounds. Yielding
pauses work; it does not certify partial results. All public pages are revision
bound and capped by rows and encoded bytes.
"""
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
MAX_OBSERVATIONS = 4096  # Prototype reservation, NOT a product/lifetime policy.
PAGE_ROWS, PAGE_BYTES = 32, 128 * 1024
SCORE_QUANTUM, WORK_QUANTUM = 64, 4096


class Pending(RuntimeError):
    pass


class Invalid(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def pair_index(a, b):
    if a == b or min(a, b) < 0:
        raise Invalid('pair')
    if a > b:
        a, b = b, a
    return b * (b - 1) // 2 + a


class Work:
    """Counter-based cooperative checkpoints; no artificial service sleeps."""
    def __init__(self):
        self.counts = {}
        self.phase_seconds = {}
        self.since_yield = 0
        self.last_yield = time.perf_counter()
        self.max_quantum_seconds = 0.

    def tick(self, phase, amount=1):
        self.counts[phase] = self.counts.get(phase, 0) + amount
        self.since_yield += amount
        threshold = SCORE_QUANTUM if phase == 'dot_products' else WORK_QUANTUM
        if self.since_yield >= threshold:
            now = time.perf_counter()
            self.max_quantum_seconds = max(self.max_quantum_seconds, now-self.last_yield)
            self.last_yield = now
            self.since_yield = 0
            self.counts['checkpoints'] = self.counts.get('checkpoints', 0) + 1
            return True
        return False


def finish(iterator):
    while True:
        try:
            next(iterator)
        except StopIteration as result:
            return result.value


@dataclass(frozen=True)
class Observation:
    node_id: str
    source_key: str
    pcm_hash: str
    float_hash: str
    vector: bytes | None
    attributions: tuple = ()  # (native scope, native label)
    spans: tuple = ()  # (source hash, source frames, start, end)
    stream: str = 'primary'

    @classmethod
    def from_vector(cls, node_id, values, **metadata):
        if type(values) is not list or len(values) != DIMENSION:
            raise Invalid('vector_shape')
        for value in values:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise Invalid('vector_component')
            try:
                exact = struct.unpack('<f', struct.pack('<f', value))[0]
            except (OverflowError, struct.error) as exc:
                raise Invalid('vector_component') from exc
            if value != exact:
                raise Invalid('not_original_float32')
        if abs(math.fsum(v*v for v in values)-1.) >= 1e-5:
            raise Invalid('vector_norm')
        return cls(node_id, metadata.pop('source_key', node_id),
                   metadata.pop('pcm_hash', node_id), metadata.pop('float_hash', node_id),
                   struct.pack('<512f', *values), **metadata)

    def metadata(self):
        return dict(node_id=self.node_id,source_key=self.source_key,pcm_hash=self.pcm_hash,
                    float_hash=self.float_hash,attributions=self.attributions,spans=self.spans,stream=self.stream)

    def encode(self):
        metadata=canonical(self.metadata())
        vector=self.vector or b''
        return struct.pack('<II',len(metadata),len(vector))+metadata+vector

    def check(self):
        if sys.byteorder != 'little' or array('d').itemsize != 8:
            raise Invalid('unsupported_host')
        for key in (self.node_id, self.source_key, self.pcm_hash, self.float_hash, self.stream):
            if type(key) is not str or not 1 <= len(key) <= 64:
                raise Invalid('bounded_key')
        if (self.stream not in ('primary', 'moss') or type(self.attributions) is not tuple
                or type(self.spans) is not tuple or len(self.attributions)>4 or len(self.spans)>4
                or any(type(v) is not tuple for v in self.attributions+self.spans)):
            raise Invalid('metadata_capacity')
        metadata = self.metadata()
        if len(canonical(metadata))>2048:
            raise Invalid('metadata_encoded_capacity')
        for scope, label in self.attributions:
            if any(type(v) is not str or not 1 <= len(v) <= 64 for v in (scope, label)):
                raise Invalid('attribution')
        for source, frames, start, end in self.spans:
            if (type(source) is not str or not 1 <= len(source) <= 64
                    or any(type(v) is not int for v in (frames, start, end))
                    or not 0 <= start < end <= frames <= 2**63-1):
                raise Invalid('span')
        if self.vector is not None:
            if type(self.vector) is not bytes or len(self.vector) != 2048:
                raise Invalid('vector_bytes')
            values = memoryview(self.vector).cast('f')
            if any(not math.isfinite(v) for v in values) or abs(math.fsum(v*v for v in values)-1.) >= 1e-5:
                raise Invalid('vector_norm')


class Bits:
    def __init__(self, pairs):
        self.data = bytearray((pairs+7)//8)

    def get(self, index):
        return bool(self.data[index >> 3] & (1 << (index & 7)))

    def set(self, index):
        self.data[index >> 3] |= 1 << (index & 7)


class Matrix:
    def __init__(self, n, scores=None):
        self.n = n
        pairs = n*(n-1)//2
        self.scores = array('d') if scores is None else scores
        self.cannot, self.overlap = Bits(pairs), Bits(pairs)

    @classmethod
    def from_scores(cls, scores, n, cannot=(), overlap=()):
        if not 0 <= n <= MAX_OBSERVATIONS or len(scores) != n*(n-1)//2:
            raise Invalid('matrix_size')
        result = cls(n)
        for b in range(n):
            for a in range(b):
                value = scores[(a, b)]
                if not math.isfinite(value):
                    raise Invalid('matrix_nonfinite')
                result.scores.append(value)
        for pairs, bits in ((cannot, result.cannot), (overlap, result.overlap)):
            for a, b in pairs:
                if not 0 <= a < b < n:
                    raise Invalid('dependency_pair')
                bits.set(pair_index(a, b))
        return result


@dataclass(frozen=True)
class Merge:
    round: int
    left: int
    right: int


class Trace:
    """Bounded merge DAG. Original member lists are expanded only on demand."""
    def __init__(self, n):
        self.n = n
        self.merges = []
        self.rounds = []

    def members(self, node):
        pending = [node]
        while pending:
            item = pending.pop()
            if item < self.n:
                yield item
            else:
                event = self.merges[item-self.n]
                pending.extend((event.right, event.left))

    def oracle_trace(self):
        if self.n > 32:
            raise Invalid('whole_trace_reserved_for_small_oracle')
        return [dict(round=index, cluster_count=count, reasons=dict(reasons),
                     merges=[[sorted(self.members(event.left)), sorted(self.members(event.right))]
                             for event in self.merges[lo:hi]])
                for index, (count, reasons, lo, hi) in enumerate(self.rounds)]


def cluster(matrix, work, *, typed=True):
    """Exact synchronous rounds, rank every cluster then apply winner vetoes.

    A merged cross-score is min of all constituent scores, dependencies are OR.
    This is exactly complete linkage, avoiding boxed original-crosspair lists.
    Ranking costs O(k^2) per round; total reconciliation is not O(N).
    """
    n = matrix.n
    groups = [(i,) for i in range(n)]
    trace = Trace(n)
    dag_ids = list(range(n))
    for round_number in range(max(1, n)):
        work.counts['clustering_rounds'] = work.counts.get('clustering_rounds', 0)+1
        k = len(groups)
        best, second = [None]*k, [None]*k
        for b in range(k):
            for a in range(b):
                score = matrix.scores[pair_index(a, b)]
                for one, other in ((a, b), (b, a)):
                    value = (-score, groups[other], other)
                    if best[one] is None or value < best[one]:
                        second[one], best[one] = best[one], value
                    elif second[one] is None or value < second[one]:
                        second[one] = value
                if work.tick('ranking_pairs'):
                    yield None
        reasons = dict.fromkeys(('missing_runner_up', 'exact_tie', 'below_tau', 'low_margin',
                                 'cannot_link_winner', 'not_reciprocal'), 0)
        if typed:
            reasons['source_overlap_dependency'] = 0
        proposals = array('i', [-1])*k
        for i in range(k):
            if second[i] is None:
                reasons['missing_runner_up'] += 1
                continue
            first, runner = best[i], second[i]
            j, r = first[2], runner[2]
            score, other_score = -first[0], -runner[0]
            if score == other_score:
                reason = 'exact_tie'
            elif score < TAU:
                reason = 'below_tau'
            elif score-other_score < MARGIN:
                reason = 'low_margin'
            elif matrix.cannot.get(pair_index(i, j)):
                reason = 'cannot_link_winner'
            elif typed and any(matrix.overlap.get(pair_index(a, b)) for a, b in ((i,j),(i,r),(j,r))):
                reason = 'source_overlap_dependency'
            else:
                proposals[i] = j
                reason = None
            if reason:
                reasons[reason] += 1
            if work.tick('proposal_checks'):
                yield None
        merges = [(i, j) for i, j in enumerate(proposals) if i < j and proposals[j] == i]
        used = bytearray(k)
        lo = len(trace.merges)
        replacements = []
        for i, j in merges:
            used[i] = used[j] = 1
            pair = pair_index(i, j)
            if matrix.cannot.get(pair) or matrix.scores[pair] < TAU:
                raise Invalid('complete_link_violation')
            trace.merges.append(Merge(round_number, dag_ids[i], dag_ids[j]))
            replacements.append((tuple(sorted(groups[i]+groups[j])), (i,j), n+len(trace.merges)-1))
        reasons['not_reciprocal'] = sum(j >= 0 and not used[i] for i,j in enumerate(proposals))
        trace.rounds.append((k, tuple(reasons.items()), lo, len(trace.merges)))
        if not merges:
            return groups, trace
        replacements.extend((groups[i], (i,), dag_ids[i]) for i in range(k) if not used[i])
        replacements.sort(key=lambda row: row[0])
        updated = Matrix(len(replacements))
        for b in range(len(replacements)):
            for a in range(b):
                score = math.inf
                cannot = overlap = False
                for i in replacements[a][1]:
                    for j in replacements[b][1]:
                        old = pair_index(i,j)
                        score = min(score, matrix.scores[old])
                        cannot |= matrix.cannot.get(old)
                        overlap |= matrix.overlap.get(old)
                        if work.tick('linkage_crosspair_checks'):
                            yield None
                updated.scores.append(score)
                pair = pair_index(a,b)
                if cannot:
                    updated.cannot.set(pair)
                if overlap:
                    updated.overlap.set(pair)
        matrix = updated
        groups, dag_ids = [r[0] for r in replacements], [r[2] for r in replacements]
    raise Invalid('round_capacity')


class Decision:
    def __init__(self, token, rows, physical, clusters, trace, policy, session):
        self.token, self.rows, self.physical, self.clusters, self.trace = token, rows, physical, clusters, trace
        self.memberships = array('i', [-1])*len(rows)
        self.identities = []
        self.duplicate = bytearray(len(rows))
        for cluster_id, members in enumerate(clusters):
            node_ids = sorted(rows[row].node_id for p in members for row in physical[p])
            identity = 'recording-speaker-'+digest(dict(policy_sha256=policy, session_id=session,
                                                        members=node_ids))[:20] if len(members)>1 else None
            self.identities.append(identity)
            for p in members:
                for row in physical[p]:
                    if rows[row].vector is not None:
                        self.memberships[row] = cluster_id
                        self.duplicate[row] = len(physical[p])>1

    def annotation(self, row):
        observation = self.rows[row]
        cluster_id = self.memberships[row]
        identity = self.identities[cluster_id] if cluster_id >= 0 else None
        return dict(sequence=row+1, node_id=observation.node_id, stream=observation.stream,
                    status=('provisional_linked' if identity else 'source_scoped') if cluster_id>=0 else 'probe_unavailable',
                    provisional_speaker_id=identity, duplicate_evidence=bool(self.duplicate[row]),
                    speaker_identity_valid=False)


class History:
    """In-memory ledger stand-in. It is not durable authority or a DB adapter."""
    def __init__(self, policy='prototype-v3-semantics', session='synthetic', space='synthetic-512'):
        if any(type(v) is not str or not 1<=len(v)<=64 for v in (policy,session,space)):
            raise Invalid('snapshot_identifier')
        self.policy, self.session, self._space = policy, session, space
        self.rows, self.vector_rows, self.vector_slots = [], [], []
        self.scores = array('d')
        self.witnesses = set()
        self.revision = 0
        self.revoked = False
        self.busy = False
        self.decision = None

    @property
    def space(self):
        return self._space

    def token(self):
        return (self.revision, len(self.rows), len(self.vector_rows), self.policy, self.session, self.space)

    def append(self, observations, work):
        if self.busy or self.revoked:
            raise Pending('busy_or_revoked')
        observations = tuple(observations)
        if len(self.rows)+len(observations)>MAX_OBSERVATIONS:
            raise Invalid('explicit_prototype_saturation')
        ids = {row.node_id for row in self.rows}
        for row in observations:
            if type(row) is not Observation:
                raise Invalid('observation_type')
            row.check()
            if row.node_id in ids:
                raise Invalid('duplicate_node')
            ids.add(row.node_id)
        self.revision += 1
        self.decision = None
        self.busy = True
        for row in observations:
            slot = -1
            if row.vector is not None:
                vector = memoryview(row.vector).cast('f')
                for previous in self.vector_rows:
                    other = memoryview(previous).cast('f')
                    self.scores.append(math.fsum(x*y for x,y in zip(vector,other)))
                    if work.tick('dot_products'):
                        yield None
                slot = len(self.vector_rows)
                self.vector_rows.append(row.vector)
            self.rows.append(row)
            self.vector_slots.append(slot)
            if work.tick('appended_observations'):
                yield None
        self.busy = False
        return self.token()

    def add_witness(self, scope, one, two):
        if self.busy:
            raise Pending('busy')
        if len(self.witnesses)>=4*MAX_OBSERVATIONS:
            raise Invalid('witness_capacity')
        if any(type(v) is not str or not 1 <= len(v) <= 64 for v in (scope,one,two)) or one==two:
            raise Invalid('witness')
        self.witnesses.add((scope, tuple(sorted((one,two)))))
        self.revision += 1
        self.decision = None

    def revoke(self):
        self.revoked = True
        self.revision += 1
        self.decision = None

    def observation_page(self, token, after=0, limit=PAGE_ROWS):
        self._current(token)
        if type(after) is not int or not 0 <= after <= len(self.rows) or not 1<=limit<=PAGE_ROWS:
            raise Invalid('page_cursor')
        answer=[]
        for row in self.rows[after:min(after+limit,len(self.rows))]:
            candidate=answer+[row]
            if len(self.encode_observation_page(token,candidate,after+len(candidate)))>PAGE_BYTES:
                break
            answer.append(row)
        if not answer and after<len(self.rows):
            raise Invalid('observation_page_capacity')
        return tuple(answer),after+len(answer)

    @staticmethod
    def encode_observation_page(token, rows, next_after):
        header=canonical(dict(revision=token,next_after=next_after,complete=next_after==token[1],rows=len(rows)))
        return struct.pack('<I',len(header))+header+b''.join(row.encode() for row in rows)

    def _current(self, token):
        if self.busy or self.revoked or token != self.token():
            raise Pending('incomplete_or_stale_snapshot')

    def compare(self, work):
        token = self.token()
        self._current(token)
        self.decision = None
        phase_started=time.perf_counter()
        rows, after = [], 0
        while after < token[1]:
            page, following = self.observation_page(token,after)
            if not page or following != after+len(page):
                raise Pending('incomplete_scan')
            rows.extend(page)
            after = following
            if work.tick('observation_pages'):
                yield None
        if after != token[1]:
            raise Pending('incomplete_scan')
        # Exact connected alias classes by both source and PCM. No metadata row is dropped.
        parent = list(range(len(rows)))
        def root(i):
            while parent[i]!=i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        source, pcm = {}, {}
        for i,row in enumerate(rows):
            for mapping,key in ((source,row.source_key),(pcm,row.pcm_hash)):
                if key in mapping:
                    parent[root(i)] = root(mapping[key])
                else:
                    mapping[key] = i
                if work.tick('alias_key_checks'):
                    yield None
        classes = {}
        for i in range(len(rows)):
            classes.setdefault(root(i),[]).append(i)
        all_groups = sorted((sorted(g,key=lambda i:rows[i].node_id) for g in classes.values()),
                            key=lambda g:rows[g[0]].node_id)
        physical, vectors, attrs, spans = [],[],[],[]
        for group in all_groups:
            if len({rows[i].pcm_hash for i in group})!=1 or len({rows[i].float_hash for i in group})!=1:
                raise Invalid('duplicate_pcm_disagreement')
            attributions = tuple(a for i in group for a in rows[i].attributions)
            for scope,a in attributions:
                for other,b in attributions:
                    if scope==other and (scope,tuple(sorted((a,b)))) in self.witnesses:
                        raise Invalid('duplicate_native_conflict')
                    if work.tick('alias_witness_checks'):
                        yield None
            available = [i for i in group if rows[i].vector is not None]
            if not available:
                continue
            exemplar = memoryview(rows[available[0]].vector).cast('f')
            for i in available[1:]:
                for a,b in zip(exemplar,memoryview(rows[i].vector).cast('f')):
                    if a!=b:
                        raise Invalid('duplicate_vector_disagreement')
                    if work.tick('alias_vector_components'):
                        yield None
            physical.append(group)
            vectors.append(self.vector_slots[available[0]])
            attrs.append(attributions)
            spans.append(tuple(s for i in group for s in rows[i].spans))
        work.phase_seconds['scan_alias_validation']=time.perf_counter()-phase_started
        phase_started=time.perf_counter()
        matrix = Matrix(len(physical))
        for b in range(len(physical)):
            for a in range(b):
                matrix.scores.append(self.scores[pair_index(vectors[a],vectors[b])])
                cannot = False
                for scope,label in attrs[a]:
                    for scope2,label2 in attrs[b]:
                        cannot |= scope==scope2 and (scope,tuple(sorted((label,label2)))) in self.witnesses
                        if work.tick('cross_witness_checks'):
                            yield None
                overlap = False
                for source,frames,lo,hi in spans[a]:
                    for source2,frames2,lo2,hi2 in spans[b]:
                        overlap |= source==source2 and frames==frames2 and max(lo,lo2)<min(hi,hi2)
                        if work.tick('source_span_checks'):
                            yield None
                pair = pair_index(a,b)
                if cannot:
                    matrix.cannot.set(pair)
                if overlap:
                    matrix.overlap.set(pair)
                if work.tick('matrix_pairs'):
                    yield None
        work.phase_seconds['matrix_dependencies']=time.perf_counter()-phase_started
        phase_started=time.perf_counter()
        clusters, trace = yield from cluster(matrix,work)
        work.phase_seconds['clustering']=time.perf_counter()-phase_started
        phase_started=time.perf_counter()
        decision = Decision(token,tuple(rows),physical,clusters,trace,self.policy,self.session)
        self._current(token)  # Final revision CAS analogue, no stale publication.
        self.decision = decision
        work.phase_seconds['publication_projection']=time.perf_counter()-phase_started
        return decision

    def annotation_page(self, token, after=0, limit=PAGE_ROWS):
        self._current(token)
        if self.decision is None or self.decision.token!=token:
            raise Pending('comparison_pending')
        if type(after) is not int or not 0<=after<=len(self.rows) or not 1<=limit<=PAGE_ROWS:
            raise Invalid('page_cursor')
        answer=[]
        for row in range(after,min(len(self.rows),after+limit)):
            entry=self.decision.annotation(row)
            if len(canonical(dict(revision=token,rows=answer+[entry],next_after=after+len(answer)+1,
                                  complete=after+len(answer)+1==len(self.rows))))>PAGE_BYTES:
                break
            answer.append(entry)
        if not answer and after<len(self.rows):
            raise Invalid('annotation_row_capacity')
        return dict(revision=token,rows=answer,next_after=after+len(answer),
                    complete=after+len(answer)==len(self.rows))

    def decision_page(self, token, after=0, limit=PAGE_ROWS):
        """Paged round summaries, merge DAG events, then membership references."""
        self._current(token)
        if self.decision is None or self.decision.token!=token:
            raise Pending('comparison_pending')
        d=self.decision
        rounds,merges=len(d.trace.rounds),len(d.trace.merges)
        total=rounds+merges+len(d.rows)
        if type(after) is not int or not 0<=after<=total or not 1<=limit<=PAGE_ROWS:
            raise Invalid('page_cursor')
        answer=[]
        for index in range(after,min(total,after+limit)):
            if index<rounds:
                k,reasons,lo,hi=d.trace.rounds[index]
                row=dict(kind='round',round=index,cluster_count=k,reasons=dict(reasons),merge_range=[lo,hi])
            elif index<rounds+merges:
                event=d.trace.merges[index-rounds]
                row=dict(kind='merge',id=d.trace.n+index-rounds,round=event.round,left=event.left,right=event.right)
            else:
                sequence=index-rounds-merges
                row=dict(kind='membership',sequence=sequence+1,cluster=int(d.memberships[sequence]))
            if len(canonical(dict(revision=token,rows=answer+[row],next_after=after+len(answer)+1,
                                  complete=after+len(answer)+1==total)))>PAGE_BYTES:
                break
            answer.append(row)
        if not answer and after<total:
            raise Invalid('decision_row_capacity')
        return dict(revision=token,rows=answer,next_after=after+len(answer),complete=after+len(answer)==total)
