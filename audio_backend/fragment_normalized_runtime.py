"""Opt-in finalized normalization adapter; native owners never become model frames.

No codecs, ingestion, models, enrollment, or production entry points. The native
SourcePlan and exact canonical EOS receipt share the existing ledger transaction.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path

from .fragment_core import CoreWindow, SourceChanged, SourcePlan, UnsupportedAudio
from .fragment_normalizer import (
    CONTEXT, FINGERPRINT, MAX_NATIVE_READ, MAX_READ, MIN_ISLAND,
    FinalizedPCM16Normalizer,
)
from .fragment_runtime import LogicalRunner
from .fragment_runtime_contracts import (
    CORE, EPOCH, MAX_PARTS, RATE, PCMWindow, RuntimePolicy, bounded_json,
    canonical, digest, integer,
)
from .fragment_runtime_store import RuntimeStore

VERSION = 'finalized-normalized-runtime-v1'
MAP_KIND = 'native-filter-support-v1'
INPUT_FINGERPRINT = digest({'adapter': VERSION, 'mapping': MAP_KIND, 'normalizer': FINGERPRINT})
MAX_SUPPORT_PARTS = 512


def _native_plan(plan):
    if type(plan) is not SourcePlan:
        raise UnsupportedAudio('An unchanged native SourcePlan is required')
    plan.validate()
    if (not plan.final or plan.sample_rate not in (44100, 48000) or plan.channels not in (1, 2)
        or plan.end_frame < MIN_ISLAND or plan.observed_frames != plan.end_frame
        or any(span.island != 0 for span in plan.spans)):
        raise UnsupportedAudio('Normalization requires finalized continuous same-rate 44.1/48k mono/stereo PCM16')
    plan.to_json()
    return plan


@dataclass(frozen=True)
class NormalizedPlan:
    """Canonical scheduling extent plus original native ownership, never fake spans.

    Construct with prepare_normalized_plan. A restored receipt is checked against
    a freshly verified normalizer before any provider receives data.
    """
    source_plan: SourcePlan
    canonical_end: int
    normalizer_fingerprint: str = FINGERPRINT
    version: str = VERSION

    @property
    def end_frame(self):
        return self.canonical_end

    @property
    def final(self):
        return self.source_plan.final

    def validate(self):
        _native_plan(self.source_plan)
        integer(self.canonical_end, 'canonical EOS', 1)
        if self.version != VERSION or self.normalizer_fingerprint != FINGERPRINT:
            raise UnsupportedAudio('Unsupported normalized runtime/normalizer fingerprint')
        if abs(self.canonical_end*self.source_plan.sample_rate-self.source_plan.end_frame*RATE) > self.source_plan.sample_rate:
            raise ValueError('Canonical EOS differs from bounded native extent')
        return self

    @property
    def receipt(self):
        return {'kind': VERSION, 'native_plan_sha256': self.source_plan.sha256,
                'native_rate': self.source_plan.sample_rate, 'native_channels': self.source_plan.channels,
                'native_end': self.source_plan.end_frame, 'canonical_rate': RATE,
                'canonical_end': self.canonical_end, 'normalizer_fingerprint': self.normalizer_fingerprint,
                'input_fingerprint': INPUT_FINGERPRINT}

    def to_json(self):
        self.validate()
        # SourcePlan has its own (larger) metadata bound; the receipt is small.
        return canonical({'source_plan': json.loads(self.source_plan.to_json()), 'receipt': self.receipt})

    @property
    def sha256(self):
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @classmethod
    def from_json(cls, text):
        from .fragment_core import MAX_PLAN_BYTES, _parse_json
        if len(text.encode()) > MAX_PLAN_BYTES + 2048:
            raise ValueError('Normalized plan metadata exceeds bound')
        value = _parse_json(text)
        if set(value) != {'source_plan', 'receipt'}:
            raise ValueError('Unknown normalized plan fields')
        receipt = value['receipt']
        plan = cls(SourcePlan.from_json(canonical(value['source_plan'])), receipt['canonical_end'],
                   receipt['normalizer_fingerprint'], receipt['kind']).validate()
        if receipt != plan.receipt:
            raise ValueError('Normalization receipt differs from native source plan')
        return plan

    def next_window(self, cursor, policy):
        self.validate()
        integer(cursor, 'canonical cursor')
        if cursor > self.end_frame:
            raise ValueError('Canonical cursor exceeds EOS')
        if cursor == self.end_frame:
            return None
        epoch = cursor // EPOCH
        end = min((cursor//CORE+1)*CORE, (epoch+1)*EPOCH, self.end_frame)
        a, b = max(0, cursor-policy.context_frames), min(self.end_frame, end+policy.context_frames)
        return CoreWindow(cursor, end, a, b, 0, epoch, epoch*EPOCH, (epoch+1)*EPOCH,
                          'complete' if a == cursor-policy.context_frames else 'capture_start',
                          'complete' if b == end+policy.context_frames else 'eos', end == self.end_frame)

    def prefix_hash(self, end):
        integer(end, 'canonical prefix')
        if end > self.end_frame:
            raise ValueError('Canonical prefix exceeds EOS')
        # Filter support may extend on either side; never pretend this is a
        # one-to-one owner prefix in native frames. Final source plan is fixed.
        return digest({'kind': MAP_KIND, 'normalized_plan_sha256': self.sha256, 'canonical_prefix_end': end})

    def mapping(self, start, end):
        _range(start, end, self.end_frame)
        plan = self.source_plan
        common = math.gcd(plan.sample_rate, RATE)
        p, q = RATE//common, plan.sample_rate//common
        sources = {source.fragment_id: source for source in plan.sources}
        result, support_count = [], 0
        receipt = self.receipt
        for a in range(start, end, MAX_READ):
            b = min(end, a+MAX_READ)
            anchor = max(0, a*q//p-CONTEXT)//q*q
            limit = min(plan.end_frame, (b*q+p-1)//p+CONTEXT)
            owners = []
            for span in plan.spans:
                left, right = max(anchor, span.logical_start), min(limit, span.logical_end)
                if left < right:
                    part = asdict(span)
                    part.update(logical_start=left, logical_end=right,
                                source_start=span.source_start+left-span.logical_start,
                                source_end=span.source_start+right-span.logical_start)
                    owners.append({'span': part, 'source_sha256': sources[span.fragment_id].sha256})
                    if len(owners) > MAX_PARTS:
                        raise ValueError('Native support owner count exceeds bound')
            aliases = [{'alias': asdict(alias), 'source_sha256': sources[alias.fragment_id].sha256}
                       for alias in plan.aliases if alias.logical_start < limit and alias.logical_end > anchor]
            support_count += len(owners)+len(aliases)
            if len(aliases) > MAX_PARTS or support_count > MAX_SUPPORT_PARTS:
                raise ValueError('Native filter support count exceeds bound')
            result.append({'kind': MAP_KIND, 'canonical_start': a, 'canonical_end': b,
                           'native_context_start': anchor, 'native_context_end': limit,
                           'owners': owners, 'aliases': aliases, 'receipt': receipt})
        validate_support(result, start, end)
        bounded_json(result)
        return tuple(result)


def _range(start, end, extent):
    integer(start, 'canonical start')
    integer(end, 'canonical end')
    if not 0 <= start < end <= extent or end-start > 60*RATE:
        raise ValueError('Normalized runtime read must be nonempty, inside EOS, and <=60 seconds')


def validate_support(mapping, start, end):
    """Check declared support shape, never claim samplewise source ownership."""
    if not isinstance(mapping, (tuple, list)) or not 0 < len(mapping) <= 120:
        raise ValueError('Bounded normalized support chunks required')
    cursor, receipt, count = start, None, 0
    for item in mapping:
        if not isinstance(item, dict) or set(item) != {'kind', 'canonical_start', 'canonical_end',
            'native_context_start', 'native_context_end', 'owners', 'aliases', 'receipt'} or item['kind'] != MAP_KIND:
            raise ValueError('Unknown normalized support schema')
        r = item['receipt']
        if not isinstance(r, dict) or set(r) != {'kind', 'native_plan_sha256', 'native_rate', 'native_channels',
            'native_end', 'canonical_rate', 'canonical_end', 'normalizer_fingerprint', 'input_fingerprint'}:
            raise ValueError('Invalid normalization receipt')
        for field in ('native_rate', 'native_channels', 'native_end', 'canonical_rate', 'canonical_end'):
            integer(r[field], field, 1)
        if (r['kind'] != VERSION or r['native_rate'] not in (44100, 48000) or r['native_channels'] not in (1, 2)
            or r['native_end'] < MIN_ISLAND or r['canonical_rate'] != RATE or r['normalizer_fingerprint'] != FINGERPRINT
            or r['input_fingerprint'] != INPUT_FINGERPRINT or (receipt is not None and r != receipt)
            or abs(r['canonical_end']*r['native_rate']-r['native_end']*RATE) > r['native_rate']):
            raise ValueError('Normalization receipt contract differs')
        _sha(r['native_plan_sha256'])
        receipt = r
        a, b = item['canonical_start'], item['canonical_end']
        _range(a, b, r['canonical_end'])
        if a != cursor or b-a > MAX_READ:
            raise ValueError('Canonical support chunks are not contiguous/bounded')
        common = math.gcd(r['native_rate'], RATE)
        p, q = RATE//common, r['native_rate']//common
        integer(item['native_context_start'], 'native context start')
        integer(item['native_context_end'], 'native context end')
        anchor = max(0, a*q//p-CONTEXT)//q*q
        limit = min(r['native_end'], (b*q+p-1)//p+CONTEXT)
        if (item['native_context_start'], item['native_context_end']) != (anchor, limit) or limit-anchor > MAX_NATIVE_READ:
            raise ValueError('Native filter support differs from pinned context')
        owners, aliases = item['owners'], item['aliases']
        if not isinstance(owners, (list, tuple)) or not 0 < len(owners) <= MAX_PARTS or not isinstance(aliases, (list, tuple)) or len(aliases) > MAX_PARTS:
            raise ValueError('Native support record count exceeds bound')
        native_cursor = anchor
        for part in owners:
            span = _part(part, 'span', r['native_end'])
            if span['logical_start'] != native_cursor:
                raise ValueError('Native support ownership has a hole')
            native_cursor = span['logical_end']
        if native_cursor != limit:
            raise ValueError('Native owners do not cover filter support')
        for part in aliases:
            alias = _part(part, 'alias', r['native_end'])
            if not alias['logical_start'] < limit or not alias['logical_end'] > anchor:
                raise ValueError('Alias does not intersect native filter support')
        count += len(owners)+len(aliases)
        cursor = b
    if cursor != end or count > MAX_SUPPORT_PARTS:
        raise ValueError('Incomplete or oversized normalized support mapping')


def _sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Invalid source hash')


def _part(part, field, extent):
    if not isinstance(part, dict) or set(part) != {field, 'source_sha256'}:
        raise ValueError('Invalid native support record')
    _sha(part['source_sha256'])
    span = part[field]
    if not isinstance(span, dict) or set(span) != {'fragment_id', 'source_start', 'source_end', 'logical_start', 'logical_end', 'island'}:
        raise ValueError('Invalid native support span')
    for name in ('source_start', 'source_end', 'logical_start', 'logical_end', 'island'):
        integer(span[name], name)
    if (not isinstance(span['fragment_id'], str) or not span['fragment_id'] or len(span['fragment_id']) > 256
        or span['island'] != 0 or not 0 <= span['logical_start'] < span['logical_end'] <= extent
        or not span['source_start'] < span['source_end']
        or span['source_end']-span['source_start'] != span['logical_end']-span['logical_start']):
        raise ValueError('Invalid native support bounds')
    return span


def prepare_normalized_plan(source_plan, paths, *, temp_root, enabled=False):
    normalizer = FinalizedPCM16Normalizer(source_plan, paths, temp_root=temp_root, enabled=enabled)
    return NormalizedPlan(source_plan, normalizer.canonical_end).validate()


class NormalizedPCMReader:
    def __init__(self, plan, paths, *, temp_root, enabled=False):
        if enabled is not True or type(plan) is not NormalizedPlan:
            raise UnsupportedAudio('Normalized runtime reader requires explicit opt-in and receipt')
        self.plan = plan.validate()
        self.normalizer = FinalizedPCM16Normalizer(plan.source_plan, paths, temp_root=temp_root, enabled=True)
        if self.normalizer.canonical_end != plan.canonical_end:
            raise SourceChanged('Fresh exact canonical EOS differs from durable receipt')
        self.paths = {key: Path(value) for key, value in self.normalizer.reader.paths.items()}
        self.verified_sources = len(plan.source_plan.sources)

    def extend(self, plan, paths):
        if type(plan) is not NormalizedPlan or plan.sha256 != self.plan.sha256:
            raise SourceChanged('Finalized normalized plan cannot be extended/replaced')
        if any(Path(paths[key]) != path for key, path in self.paths.items()):
            raise SourceChanged('Move normalized source bindings only by verified reopen')
        self.verify()

    def verify(self):
        self.normalizer.verify()

    def read(self, start, end):
        mapping = self.plan.mapping(start, end)  # Reject metadata overflow before processing.
        self.verify()
        data = bytearray()
        native_plan_hash = self.plan.source_plan.sha256
        for expected in mapping:
            window = self.normalizer.read(expected['canonical_start'], expected['canonical_end'])
            if (window.plan_sha256 != native_plan_hash or window.normalizer_fingerprint != FINGERPRINT
                or window.native_end != self.plan.source_plan.end_frame or window.canonical_end != self.plan.canonical_end
                or window.native_context_start != expected['native_context_start']
                or window.native_context_end != expected['native_context_end']
                or canonical(window.owners) != canonical(expected['owners'])
                or canonical(window.aliases) != canonical(expected['aliases'])):
                raise SourceChanged('Normalizer read provenance differs from accepted native support')
            data.extend(window.pcm)
        self.verify()
        pcm = bytes(data)
        if len(pcm) != 2*(end-start):
            raise SourceChanged('Normalized runtime read returned a short canonical interval')
        return PCMWindow(start, end, pcm, mapping, digest(mapping), hashlib.sha256(pcm).hexdigest())


class NormalizedRuntimeStore(RuntimeStore):
    """Separate explicit store mode; ordinary RuntimeStore rejects these receipts."""
    def __init__(self, path, *, enabled=False, **kwargs):
        if enabled is not True:
            raise UnsupportedAudio('Normalized runtime store is default OFF')
        super().__init__(path, **kwargs)

    @staticmethod
    def _admit(plan):
        if type(plan) is not NormalizedPlan:
            raise UnsupportedAudio('Normalized store requires a verified normalization receipt')
        return plan.validate()

    @staticmethod
    def _decode_plan(text):
        return NormalizedPlan.from_json(text)

    @staticmethod
    def _validate_policy(plan, policy):
        if policy.input_fingerprint != INPUT_FINGERPRINT:
            raise UnsupportedAudio('Normalized policy must bind the exact input fingerprint')

    @staticmethod
    def _extend_plan(snapshot, plan):
        raise UnsupportedAudio('Finalized normalized runs cannot be appended')

    def create(self, run_id, plan, policy=None, operation_id='initial'):
        return super().create(run_id, plan, policy or RuntimePolicy(input_fingerprint=INPUT_FINGERPRINT), operation_id)


class NormalizedLogicalRunner(LogicalRunner):
    def __init__(self, store, run_id, paths, analyzer, native_factory, *, temp_root, enabled=False, fault=None):
        if enabled is not True or type(store) is not NormalizedRuntimeStore:
            raise UnsupportedAudio('Normalized runner requires explicit opt-in and normalized store')
        self.temp_root = temp_root
        super().__init__(store, run_id, paths, analyzer, native_factory, fault)

    def _reader(self, snapshot):
        if self.reader is None:
            self.reader = NormalizedPCMReader(snapshot.plan, self.paths, temp_root=self.temp_root, enabled=True)
        else:
            self.reader.extend(snapshot.plan, self.paths)
        if self.analyzer.fingerprint != snapshot.policy.analyzer_fingerprint:
            raise ValueError('Analyzer fingerprint differs from durable policy')
        return self.reader
