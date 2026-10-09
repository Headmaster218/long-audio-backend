"""Offline SourcePlan prototype. NOT integrated into the API or worker.

All coordinates are integer *native* PCM frames, including channels together.
Known gaps reserve unobserved frame ranges. Unknown boundaries separate islands;
coordinates after them are ordered-observation coordinates, not asserted elapsed
capture time. No resampling, downmixing, padding removal or source rewriting.
"""
from bisect import bisect_right
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import wave

from .fragment_matcher import BoundaryEvidence, Continuity, MAX_MATCH_SECONDS, POLICY, match_boundary

MAX_READ_SECONDS = 60
MAX_READ_BYTES = 60 * 48000 * 2 * 2
MAX_PLAN_BYTES = 8 * 1024 * 1024
MAX_SOURCES = 4096
V2_POLICY = 'exact-pcm16-v2q1'
P1_POLICY = 'exact-pcm16-v2q1p1'
SUPPORTED_POLICIES = (POLICY, V2_POLICY, P1_POLICY)


class UnsupportedAudio(ValueError):
    pass


class MissingAudio(ValueError):
    """A range crosses missing/unknown media; no invented silence is returned."""


class SourceChanged(ValueError):
    pass


def _integer(value, name, lower=0):
    if type(value) is not int or value < lower:
        raise ValueError(f'{name} must be an integer >= {lower}')


def _parse_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('Non-finite JSON value')

    return json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _signature(path):
    stat = Path(path).stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _descriptor_signature(stream):
    stat = os.fstat(stream.fileno())
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _digest_stream(stream):
    digest = hashlib.sha256()
    stream.seek(0)
    for block in iter(lambda: stream.read(65536), b''):
        digest.update(block)
    stream.seek(0)
    return digest.hexdigest()


@dataclass(frozen=True)
class Source:
    fragment_id: str
    sha256: str
    sample_rate: int
    channels: int
    frames: int
    sample_width: int = 2
    encoding: str = 'wav-pcm16le'

    def __post_init__(self):
        if not isinstance(self.fragment_id, str) or not self.fragment_id or len(self.fragment_id) > 256:
            raise ValueError('Source ID must be nonempty and bounded')
        if not isinstance(self.sha256, str) or len(self.sha256) != 64 or any(c not in '0123456789abcdef' for c in self.sha256):
            raise ValueError('Source requires a lowercase SHA256')
        _integer(self.sample_rate, 'sample rate', 8000)
        _integer(self.channels, 'channels', 1)
        _integer(self.frames, 'source frames', 1)
        if self.sample_rate > 48000 or self.channels > 2 or self.sample_width != 2 or self.encoding != 'wav-pcm16le':
            raise UnsupportedAudio('Prototype supports native PCM16 WAV, 8..48kHz, mono/stereo only')

    @property
    def format(self):
        return self.sample_rate, self.channels, self.sample_width, self.encoding


def inspect_pcm16_source(fragment_id, path):
    """Hash and decode the SAME open descriptor with bounded-memory reads."""
    try:
        with Path(path).open('rb') as original:
            before = _descriptor_signature(original)
            digest = _digest_stream(original)
            with wave.open(original, 'rb') as stream:
                if stream.getcomptype() != 'NONE' or stream.getsampwidth() != 2:
                    raise UnsupportedAudio('Compressed/float/non-PCM16 source is unsupported; no padding inference')
                source = Source(fragment_id, digest, stream.getframerate(),
                                stream.getnchannels(), stream.getnframes())
                actual = 0
                while block := stream.readframes(source.sample_rate):
                    if len(block) % (source.channels * 2):
                        raise UnsupportedAudio('Partial source PCM frame')
                    actual += len(block) // (source.channels * 2)
                if actual != source.frames:
                    raise UnsupportedAudio('Truncated or inconsistent PCM payload')
            if _descriptor_signature(original) != before or _signature(path) != before:
                raise SourceChanged('Source descriptor or path changed during inspection')
    except (wave.Error, EOFError) as exc:
        raise UnsupportedAudio('Unsupported WAV/codec; exact native PCM16 required') from exc
    return source


@dataclass(frozen=True)
class Span:
    fragment_id: str
    source_start: int
    source_end: int
    logical_start: int
    logical_end: int
    island: int


@dataclass(frozen=True)
class Alias:
    """Redundant source frames map to retained logical owners, never new time."""
    fragment_id: str
    source_start: int
    source_end: int
    logical_start: int
    logical_end: int
    island: int


@dataclass(frozen=True)
class Seam:
    left_id: str
    right_id: str
    declaration: Continuity
    evidence: BoundaryEvidence
    logical_boundary: int
    status: str


@dataclass(frozen=True)
class SourcePlan:
    sources: tuple[Source, ...]
    spans: tuple[Span, ...]
    aliases: tuple[Alias, ...]
    seams: tuple[Seam, ...]
    final: bool = False
    version: str = POLICY

    @property
    def sample_rate(self):
        return self.sources[0].sample_rate

    @property
    def channels(self):
        return self.sources[0].channels

    @property
    def end_frame(self):
        return self.spans[-1].logical_end

    @property
    def observed_frames(self):
        return sum(s.logical_end - s.logical_start for s in self.spans)

    def validate(self):
        if self.version not in SUPPORTED_POLICIES or type(self.final) is not bool:
            raise ValueError('Unknown plan policy or invalid EOS flag')
        if not self.sources or len(self.sources) > MAX_SOURCES or len(self.seams) != len(self.sources) - 1:
            raise ValueError('Invalid bounded source/seam count')
        by_id = {source.fragment_id: source for source in self.sources}
        if len(by_id) != len(self.sources):
            raise ValueError('Fragment IDs must be unique')
        if any(s.format != self.sources[0].format for s in self.sources):
            raise UnsupportedAudio('Mixed formats require a future stateful normalization path')
        if not self.spans or len(self.spans) > len(self.sources) or len(self.aliases) > len(self.seams):
            raise ValueError('Invalid owner/alias count')
        # Validate each fragment's complete physical coverage by disjoint owner
        # plus optional redundant prefix, without any per-frame record arrays.
        coverage = {key: [] for key in by_id}
        for item in (*self.spans, *self.aliases):
            if item.fragment_id not in by_id:
                raise ValueError('Unknown source reference')
            for field in ('source_start', 'source_end', 'logical_start', 'logical_end', 'island'):
                _integer(getattr(item, field), field)
            if not 0 <= item.source_start < item.source_end <= by_id[item.fragment_id].frames:
                raise ValueError('Invalid source interval')
            if item.logical_end - item.logical_start != item.source_end - item.source_start:
                raise ValueError('Native-frame mapping changed length')
            coverage[item.fragment_id].append((item.source_start, item.source_end))
        for key, intervals in coverage.items():
            cursor = 0
            for a, b in sorted(intervals):
                if a != cursor:
                    raise ValueError('Source frames lost or counted twice')
                cursor = b
            if cursor != by_id[key].frames:
                raise ValueError('Source coverage incomplete')
        end, island = 0, 0
        for span in self.spans:
            if span.logical_start < end or span.island < island:
                raise ValueError('Nonmonotonic logical ownership')
            if span.island == island and span.logical_start != end:
                raise ValueError('An island may not contain a hole')
            end, island = span.logical_end, span.island
        for alias in self.aliases:
            cursor = alias.logical_start
            for span in self.spans:
                if span.logical_end <= cursor:
                    continue
                if span.logical_start >= alias.logical_end:
                    break
                if span.logical_start > cursor or span.island != alias.island:
                    raise ValueError('Redundant alias lacks continuous retained owners')
                cursor = min(alias.logical_end, span.logical_end)
            if cursor != alias.logical_end:
                raise ValueError('Redundant alias lacks retained owner coverage')
        # Reconstruct all expected mappings from already-bound seam decisions.
        expected_spans, expected_aliases = _layout(self.sources, self.seams, policy=self.version)
        if tuple(expected_spans) != self.spans or tuple(expected_aliases) != self.aliases:
            raise ValueError('Plan mapping differs from recorded seam decisions')
        return self

    def to_json(self):
        self.validate()
        data = _canonical(asdict(self))
        if len(data.encode()) > MAX_PLAN_BYTES:
            raise ValueError('Plan serialization exceeds bounded metadata budget')
        return data

    @property
    def sha256(self):
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @classmethod
    def from_json(cls, text):
        if len(text.encode()) > MAX_PLAN_BYTES:
            raise ValueError('Plan too large')
        data = _parse_json(text)
        if set(data) != {'sources', 'spans', 'aliases', 'seams', 'final', 'version'}:
            raise ValueError('Unexpected plan fields')
        seams = []
        for item in data['seams']:
            item['declaration'] = Continuity(**item['declaration'])
            item['evidence']['candidate_frames'] = tuple(item['evidence']['candidate_frames'])
            item['evidence'] = BoundaryEvidence(**item['evidence'])
            seams.append(Seam(**item))
        plan = cls(tuple(Source(**s) for s in data['sources']),
                   tuple(Span(**s) for s in data['spans']), tuple(Alias(**s) for s in data['aliases']),
                   tuple(seams), data['final'], data['version'])
        return plan.validate()


def _seam_status(declaration, evidence):
    if declaration.relation == 'gap':
        return 'declared_gap'
    if evidence.trim_frames:
        return 'verified_overlap'
    if declaration.relation == 'unknown':
        return 'unknown_continuity'
    if evidence.candidate_frames or evidence.search_exhausted or evidence.reason == 'too_short_for_search':
        return 'ambiguous_overlap'
    return 'declared_continuity_only'


def _layout(sources, seams, *, policy=POLICY):
    spans = [Span(sources[0].fragment_id, 0, sources[0].frames, 0, sources[0].frames, 0)]
    aliases = []
    cursor, island = sources[0].frames, 0
    for index, seam in enumerate(seams):
        source, previous = sources[index + 1], sources[index]
        declaration, evidence = seam.declaration, seam.evidence
        trim = evidence.trim_frames
        if seam.left_id != previous.fragment_id or seam.right_id != source.fragment_id:
            raise ValueError('Seam does not follow explicit fragment order')
        if seam.logical_boundary != cursor or seam.status != _seam_status(declaration, evidence):
            raise ValueError('Seam boundary/status differs from mapping')
        _integer(trim, 'trim frames')
        if trim > min(source.frames, previous.frames, source.sample_rate * MAX_MATCH_SECONDS):
            raise ValueError('Trim exceeds bounded source evidence')
        if evidence.policy != policy or evidence.outcome not in ('trim', 'abstain'):
            raise ValueError('Unsupported seam evidence')
        if bool(trim) != (evidence.outcome == 'trim'):
            raise ValueError('Trim decision is inconsistent')
        if trim and (declaration.relation != 'continuous' or not declaration.same_capture or
                     declaration.replay_possible or evidence.search_exhausted or evidence.candidate_frames != (trim,) or
                     evidence.reason != 'unique_informative_exact_overlap' or
                     trim < (source.sample_rate + 1) // 2):
            raise ValueError('Trim lacks required provenance and unique exact evidence')
        if declaration.relation == 'gap':
            cursor += declaration.gap_frames
        if seam.status in ('declared_gap', 'unknown_continuity', 'ambiguous_overlap'):
            island += 1
        if trim:
            aliases.append(Alias(source.fragment_id, 0, trim, cursor - trim, cursor, island))
        if source.frames > trim:
            spans.append(Span(source.fragment_id, trim, source.frames, cursor,
                              cursor + source.frames - trim, island))
            cursor += source.frames - trim
    return spans, aliases


class BoundedPCM16Reader:
    """Seek/read original sources through a plan; source paths are not plan IDs.

    Verify source hashes once on construction/restart, then reject any changed
    file signature before and after each read. At most 60s / 11.52MB returned.
    Each read's mapping is retained as spans; gaps/unknown islands raise.
    """
    def __init__(self, sources, paths, plan=None):
        self.sources = {s.fragment_id: s for s in sources}
        if len(self.sources) != len(sources):
            raise ValueError('Duplicate source IDs')
        self.paths = {key: Path(paths[key]) for key in self.sources}
        self.signatures = {}
        for key, source in self.sources.items():
            before = _signature(self.paths[key])
            actual = inspect_pcm16_source(key, self.paths[key])
            if actual != source or before != _signature(self.paths[key]):
                raise SourceChanged('Source identity or format differs from the plan')
            self.signatures[key] = before
        self.plan = plan.validate() if plan else None
        if self.plan and tuple(self.sources.values()) != self.plan.sources:
            raise ValueError('Reader sources differ from source plan')
        self.starts = [s.logical_start for s in plan.spans] if plan else []
        self.max_read_frames = 0
        self.max_read_bytes = 0

    def read_source(self, fragment_id, start, end):
        source = self.sources[fragment_id]
        _integer(start, 'read start')
        _integer(end, 'read end')
        if not 0 <= start <= end <= source.frames or end - start > MAX_READ_SECONDS * source.sample_rate:
            raise ValueError('Source read must stay inside source and <=60 seconds')
        size = (end - start) * source.channels * 2
        if size > MAX_READ_BYTES:
            raise ValueError('Read byte budget exceeded')
        path = self.paths[fragment_id]
        if _signature(path) != self.signatures[fragment_id]:
            raise SourceChanged('Source changed since verification')
        with path.open('rb') as original:
            if _descriptor_signature(original) != self.signatures[fragment_id]:
                raise SourceChanged('Opened source descriptor differs from verified identity')
            with wave.open(original, 'rb') as stream:
                stream.setpos(start)
                pcm = stream.readframes(end - start)
            if _descriptor_signature(original) != self.signatures[fragment_id]:
                raise SourceChanged('Opened source descriptor changed during read')
        if len(pcm) != size or _signature(path) != self.signatures[fragment_id]:
            raise SourceChanged('Source changed or yielded a short PCM read')
        self.max_read_frames = max(self.max_read_frames, end - start)
        self.max_read_bytes = max(self.max_read_bytes, len(pcm))
        return pcm

    def mapping(self, start, end):
        if not self.plan:
            raise ValueError('Logical reads require a validated SourcePlan')
        _integer(start, 'logical read start')
        _integer(end, 'logical read end')
        if not 0 <= start < end <= self.plan.end_frame or end - start > MAX_READ_SECONDS * self.plan.sample_rate:
            raise ValueError('Logical read must be nonempty, inside plan, and <=60 seconds')
        result, cursor, island = [], start, None
        first = max(0, bisect_right(self.starts, start) - 1)
        for span in self.plan.spans[first:]:
            if span.logical_start >= end:
                break
            a, b = max(start, span.logical_start), min(end, span.logical_end)
            if b <= a:
                continue
            if a != cursor or island is not None and span.island != island:
                raise MissingAudio('Requested range crosses a true gap or unresolved boundary')
            source_start = span.source_start + a - span.logical_start
            result.append(Span(span.fragment_id, source_start, source_start + b - a, a, b, span.island))
            cursor, island = b, span.island
        if cursor != end:
            raise MissingAudio('Requested range contains unobserved source frames')
        return tuple(result)

    def read(self, start, end):
        parts = self.mapping(start, end)
        pcm = b''.join(self.read_source(s.fragment_id, s.source_start, s.source_end) for s in parts)
        self.max_read_frames = max(self.max_read_frames, end - start)
        self.max_read_bytes = max(self.max_read_bytes, len(pcm))
        return pcm


def build_pcm16_plan(sources, declarations, reader, *, final=False, policy=POLICY):
    """Only matcher inference enters decisions; no ground-truth ranges accepted."""
    if policy not in SUPPORTED_POLICIES:
        raise ValueError('Unsupported exact PCM16 information policy')
    matcher = match_boundary
    if policy == V2_POLICY:
        from .fragment_matcher_v2 import match_boundary_v2
        matcher = match_boundary_v2
    elif policy == P1_POLICY:
        from .fragment_matcher_p1 import match_boundary_p1
        matcher = match_boundary_p1
    sources = tuple(sources)
    if not sources or len(sources) > MAX_SOURCES or len(declarations) != len(sources) - 1:
        raise ValueError('Ordered nonempty sources need one declaration per boundary')
    if any(s.format != sources[0].format for s in sources):
        raise UnsupportedAudio('Mixed source formats/resampling are not implemented')
    if any(reader.sources.get(s.fragment_id) != s for s in sources):
        raise SourceChanged('Plan source descriptors differ from verified reader sources')
    seams = []
    cursor = sources[0].frames
    for left, right, declaration in zip(sources, sources[1:], declarations):
        limit = sources[0].sample_rate * MAX_MATCH_SECONDS
        tail = reader.read_source(left.fragment_id, max(0, left.frames - limit), left.frames)
        head = reader.read_source(right.fragment_id, 0, min(right.frames, limit))
        evidence = matcher(tail, head, sample_rate=left.sample_rate,
                                  channels=left.channels, declaration=declaration)
        seams.append(Seam(left.fragment_id, right.fragment_id, declaration, evidence,
                          cursor, _seam_status(declaration, evidence)))
        cursor += (declaration.gap_frames or 0) + right.frames - evidence.trim_frames
    spans, aliases = _layout(sources, seams, policy=policy)
    return SourcePlan(sources, tuple(spans), tuple(aliases), tuple(seams), final, policy).validate()


@dataclass(frozen=True)
class CoreWindow:
    core_start: int
    core_end: int
    read_start: int
    read_end: int
    island: int
    epoch_index: int
    epoch_start: int
    epoch_end: int
    left_context: str
    right_context: str
    finish_native: bool


def core_windows(plan, *, core_frames, context_frames, epoch_frames=None, start_frame=0):
    """Global grid ownership, split at epochs/islands but never file edges.

    A live tail waits for a complete core AND right context. A known island end
    is processed with an explicit discontinuity; only true plan EOS may finish.
    Epoch numbers are global, even if an island starts between epoch boundaries.
    """
    plan.validate()
    _integer(core_frames, 'core frames', 1)
    _integer(context_frames, 'context frames')
    _integer(start_frame, 'start frame')
    fixed_epoch_frames = 300 * plan.sample_rate
    if epoch_frames is not None:
        _integer(epoch_frames, 'epoch frames', 1)
        if epoch_frames != fixed_epoch_frames:
            raise ValueError('Prototype native epochs are fixed to300 seconds; override unsupported')
    epoch_frames = fixed_epoch_frames
    if core_frames + 2 * context_frames > MAX_READ_SECONDS * plan.sample_rate:
        raise ValueError('Core plus context must be <=60 seconds')
    islands = []
    for span in plan.spans:
        if islands and islands[-1][0] == span.island:
            islands[-1][2] = span.logical_end
        else:
            islands.append([span.island, span.logical_start, span.logical_end])
    for index, (island, first, last) in enumerate(islands):
        final_island = index == len(islands) - 1
        cursor = max(first, start_frame)
        if cursor >= last:
            continue
        while cursor < last:
            epoch = cursor // epoch_frames
            nominal = min((cursor // core_frames + 1) * core_frames, (epoch + 1) * epoch_frames)
            if final_island and not plan.final and nominal + context_frames > last:
                break
            end = min(nominal, last)
            read_start, read_end = max(first, cursor - context_frames), min(last, end + context_frames)
            left = 'complete' if read_start == cursor - context_frames else ('capture_start' if first == 0 else 'discontinuity')
            right = 'complete' if read_end == end + context_frames else ('eos' if final_island and plan.final else 'discontinuity')
            yield CoreWindow(cursor, end, read_start, read_end, island, epoch,
                             epoch * epoch_frames, (epoch + 1) * epoch_frames,
                             left, right, bool(final_island and plan.final and end == last))
            cursor = end


@dataclass(frozen=True)
class EOSControl:
    """Zero-sample control message, never an invented audio/core interval."""
    final_frame: int
    epoch_index: int
    reason: str = 'true_eos'


@dataclass(frozen=True)
class PlanCheckpoint:
    """Offline cursor only, not an atomic model/output-store integration."""
    plan: SourcePlan
    next_frame: int
    eos_finished: bool = False

    def validate(self):
        self.plan.validate()
        _integer(self.next_frame, 'next frame')
        if self.next_frame > self.plan.end_frame:
            raise ValueError('Cursor exceeds plan')
        if type(self.eos_finished) is not bool or self.eos_finished and (not self.plan.final or self.next_frame != self.plan.end_frame):
            raise ValueError('EOS acknowledgement requires a fully owned final plan')
        return self

    def to_json(self):
        self.validate()
        payload = {'plan': json.loads(self.plan.to_json()), 'plan_sha256': self.plan.sha256,
                   'next_frame': self.next_frame, 'eos_finished': self.eos_finished,
                   'checkpoint_version': 'fragment-checkpoint-v2'}
        digest = hashlib.sha256(_canonical(payload).encode()).hexdigest()
        return _canonical(dict(payload, checkpoint_sha256=digest))

    @classmethod
    def from_json(cls, text):
        if len(text.encode()) > MAX_PLAN_BYTES + 1024:
            raise ValueError('Checkpoint too large')
        data = _parse_json(text)
        if set(data) != {'plan', 'plan_sha256', 'next_frame', 'eos_finished', 'checkpoint_version', 'checkpoint_sha256'}:
            raise ValueError('Unexpected checkpoint fields')
        digest = data.pop('checkpoint_sha256')
        if hashlib.sha256(_canonical(data).encode()).hexdigest() != digest:
            raise ValueError('Checkpoint envelope hash mismatch')
        if data['checkpoint_version'] != 'fragment-checkpoint-v2':
            raise ValueError('Unsupported checkpoint version')
        plan = SourcePlan.from_json(_canonical(data['plan']))
        if plan.sha256 != data['plan_sha256']:
            raise ValueError('Checkpoint plan hash mismatch')
        return cls(plan, data['next_frame'], data['eos_finished']).validate()

    def eos_control(self):
        """Poll after ownership advances AND after a live plan becomes final.

        A finish hint on the last CoreWindow is insufficient when all samples
        were owned before EOS arrived (e.g. context=0 on an exact grid). This
        explicit control also handles that zero-new-sample case. Acknowledge
        only after the actual consumer finishes; persist that acknowledgement.
        """
        self.validate()
        if self.plan.final and self.next_frame == self.plan.end_frame and not self.eos_finished:
            return EOSControl(self.plan.end_frame, (self.plan.end_frame - 1) // (300 * self.plan.sample_rate))
        return None

    def acknowledge_eos(self):
        if self.eos_control() is None:
            raise ValueError('No pending EOS control to acknowledge')
        return PlanCheckpoint(self.plan, self.next_frame, True).validate()

    def extend(self, new_plan):
        """Allow a live immutable-prefix extension, without re-trimming history."""
        self.validate()
        new_plan.validate()
        old = self.plan
        if old.final:
            raise ValueError('Cannot extend a true EOS plan')
        if new_plan.version != old.version:
            raise ValueError('Cannot change information policy in an accepted plan prefix')
        if (new_plan.sources[:len(old.sources)] != old.sources or
            new_plan.spans[:len(old.spans)] != old.spans or
            new_plan.aliases[:len(old.aliases)] != old.aliases or
            new_plan.seams[:len(old.seams)] != old.seams):
            raise ValueError('Plan extension rewrites an accepted source/decision/owner prefix')
        return PlanCheckpoint(new_plan, self.next_frame).validate()

    def save(self, path):
        """Atomic local snapshot of plan, hash and cursor; no model state saved."""
        path = Path(path)
        fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(self.to_json())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)
