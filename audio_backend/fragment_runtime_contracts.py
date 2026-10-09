"""Internal model-free logical-run contracts; no public ingestion integration.

SourcePlan decisions are supplied by the caller. This layer never matches,
resamples, infers continuity, or authorizes an overlap trim.
"""
from dataclasses import asdict, dataclass
import hashlib
import json

from .fragment_core import SourcePlan, UnsupportedAudio, core_windows

RATE = 16000
CORE = 30 * RATE
EPOCH = 300 * RATE
PREROLL = 339200  # 21.2 s
MAX_PARTS = 128
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_ITEMS = 4096
CONTRACT = 'logical-mono-pcm16-16000-v1'


class StaleWork(RuntimeError):
    pass


class CorruptLedger(RuntimeError):
    pass


class UnsupportedDiscontinuity(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')


def bounded_json(value, limit=MAX_OUTPUT_BYTES):
    text = canonical(value)
    if len(text.encode()) > limit:
        raise ValueError('Bounded envelope exceeded')
    return text


def admit(plan):
    plan.validate()
    if plan.sources[0].format != (RATE, 1, 2, 'wav-pcm16le'):
        raise UnsupportedAudio('Internal runtime requires original 16 kHz mono PCM16 WAV')
    if any(span.island != 0 for span in plan.spans) or any(
        seam.status in ('declared_gap', 'unknown_continuity', 'ambiguous_overlap') for seam in plan.seams
    ):
        raise UnsupportedDiscontinuity('Internal runtime does not support discontinuity drain')
    plan.to_json()  # Enforce the prototype metadata bound before admission.
    return plan


@dataclass(frozen=True)
class RuntimePolicy:
    context_frames: int = 2 * RATE
    native_fingerprint: str = 'injected-native-v1'
    analyzer_fingerprint: str = 'injected-analyzer-v1'
    lexical_policy: str = 'native-support-end-left-routing-v1'

    def __post_init__(self):
        if self.lexical_policy != 'native-support-end-left-routing-v1':
            raise ValueError('Unsupported lexical routing policy')
        integer(self.context_frames, 'context')
        if self.context_frames > 15 * RATE:
            raise ValueError('Core plus context exceeds the 60 second read bound')
        for value in (self.native_fingerprint, self.analyzer_fingerprint):
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError('A bounded immutable provider fingerprint is required')

    @property
    def sha256(self):
        return digest({'contract': CONTRACT, **asdict(self)})


def next_window(plan, cursor, policy):
    return next(core_windows(plan, core_frames=CORE, context_frames=policy.context_frames,
                             start_frame=cursor), None)


def window_id(run_id, window, policy):
    # Whole-plan hash/revision is intentionally absent. Appends do not rename
    # an already-defined logical ownership interval or native identity.
    return digest({'run': run_id, 'island': window.island, 'start': window.core_start,
                   'end': window.core_end, 'policy': policy.sha256})


def native_key(run_id, epoch, policy):
    return (run_id, 0, epoch, policy.native_fingerprint, CONTRACT)


def control_id(run_id, plan, policy):
    return digest({'run': run_id, 'reason': 'true_eos', 'end': plan.end_frame,
                   'epoch': (plan.end_frame - 1) // EPOCH, 'plan': plan.sha256,
                   'policy': policy.sha256})


def prefix_hash(plan, end):
    """Hash accepted owner identities/ranges only, independent of audit aliases."""
    integer(end, 'prefix end')
    if end > plan.end_frame:
        raise ValueError('Prefix exceeds plan')
    sources = {source.fragment_id: source for source in plan.sources}
    parts = []
    for span in plan.spans:
        if span.logical_start >= end:
            break
        stop = min(span.logical_end, end)
        part = asdict(span)
        part['logical_end'] = stop
        part['source_end'] = span.source_start + stop - span.logical_start
        parts.append({'span': part, 'source': asdict(sources[span.fragment_id])})
    return digest(parts)


@dataclass(frozen=True)
class PCMWindow:
    start: int
    end: int
    pcm: bytes
    mapping: tuple
    map_sha256: str
    pcm_sha256: str
    contract: str = CONTRACT


@dataclass(frozen=True)
class NativeStatus:
    """Absolute logical frame axes, not file-relative or model-frame counts.

    consumed_until acknowledges actual supplied PCM. activity_until bounds the
    model's supported observations (possibly quantized). A finished provider
    must acknowledge every input frame; it need not invent final-frame activity.
    This is a new injected contract, not a claim about the production backend.
    """
    consumed_until: int
    stable_until: int
    activity_until: int
    finished: bool = False
    turns: tuple = ()

    def validate(self, pushed_until, replay_start):
        for name in ('consumed_until', 'stable_until', 'activity_until'):
            integer(getattr(self, name), name)
        if type(self.finished) is not bool or not (
            replay_start <= self.consumed_until == pushed_until
            and replay_start <= self.stable_until <= self.consumed_until
            and replay_start <= self.activity_until <= self.consumed_until
        ):
            raise ValueError('Invalid native input/frontier acknowledgement')
        validate_intervals(self.turns, replay_start, self.activity_until, 'speaker')
        return self


def validate_intervals(items, start, end, label):
    if not isinstance(items, (list, tuple)) or len(items) > MAX_ITEMS:
        raise ValueError('Bounded native intervals required')
    for item in items:
        if not isinstance(item, dict) or set(item) != {'start_frame', 'end_frame', label, 'instance_id'}:
            raise ValueError('Explicit native interval identity and label required')
        integer(item['start_frame'], 'interval start')
        integer(item['end_frame'], 'interval end')
        if not start <= item['start_frame'] < item['end_frame'] <= end:
            raise ValueError('Native interval outside supplied supported input')
        if any(not isinstance(item[key], str) or not item[key] or len(item[key]) > 256
               for key in (label, 'instance_id')):
            raise ValueError('Invalid native interval label/identity')
    bounded_json(items)


def own_intervals(items, start, end, scope):
    output = []
    for item in items:
        a, b = max(start, item['start_frame']), min(end, item['end_frame'])
        if a < b:
            output.append({**item, 'native_start_frame': item['start_frame'],
                           'native_end_frame': item['end_frame'], 'start_frame': a,
                           'end_frame': b, 'scope': scope})
    return output


def validate_commit(snapshot, window, payload, tail, checkpoint):
    """Defensive publication boundary; caller-supplied state cannot widen scope."""
    expected_keys = {'window_id', 'window', 'revision', 'plan_hash', 'contract', 'policy_hash',
                     'mapping', 'map_hash', 'pcm_hash', 'segments', 'turns', 'events', 'tail', 'checkpoint'}
    if not isinstance(payload, dict) or set(payload) not in (expected_keys, expected_keys | {'evidence_ref'}):
        raise ValueError('Unknown or missing output envelope fields')
    if 'evidence_ref' in payload:
        from .fragment_evidence import validate_reference
        validate_reference(payload['evidence_ref'])
    if payload['contract'] != CONTRACT or payload['policy_hash'] != snapshot.policy.sha256:
        raise ValueError('Output canonical/provider policy differs from run')
    # Compute the immutable expected read map without opening any source file.
    source_map = {source.fragment_id: source for source in snapshot.plan.sources}
    mapped = []
    for span in snapshot.plan.spans:
        a, b = max(window.read_start, span.logical_start), min(window.read_end, span.logical_end)
        if a >= b:
            continue
        part = asdict(span)
        part.update(logical_start=a, logical_end=b, source_start=span.source_start+a-span.logical_start,
                    source_end=span.source_start+b-span.logical_start)
        mapped.append({'span': part, 'source_sha256': source_map[span.fragment_id].sha256})
    if len(mapped) > MAX_PARTS or canonical(payload['mapping']) != canonical(mapped) or payload['map_hash'] != digest(mapped):
        raise ValueError('Output read map differs from accepted owners')
    sha = payload['pcm_hash']
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Invalid PCM hash')
    required_checkpoint = {'native_key', 'replay_start', 'consumed_until', 'stable_until',
                           'activity_until', 'finished', 'input_prefix_hash'}
    if not isinstance(checkpoint, dict) or set(checkpoint) != required_checkpoint:
        raise ValueError('Invalid native checkpoint fields')
    expected_key = list(native_key(snapshot.run_id, window.epoch_index, snapshot.policy))
    replay_start = max(0, window.epoch_start - PREROLL)
    if checkpoint['native_key'] != expected_key or checkpoint['replay_start'] != replay_start:
        raise ValueError('Checkpoint native identity/replay scope differs from core')
    consumed = checkpoint['consumed_until']
    integer(consumed, 'consumed frontier')
    if not window.read_end <= consumed <= min(snapshot.plan.end_frame, replay_start+360*RATE):
        raise ValueError('Checkpoint input frontier differs from bounded source range')
    status = NativeStatus(consumed, checkpoint['stable_until'], checkpoint['activity_until'], checkpoint['finished'])
    status.validate(consumed, replay_start)
    if checkpoint['input_prefix_hash'] != prefix_hash(snapshot.plan, consumed):
        raise ValueError('Checkpoint input mapping differs from accepted owner prefix')
    if status.finished and (not snapshot.control or consumed != snapshot.plan.end_frame or
                            window.epoch_index != snapshot.control['epoch']):
        raise ValueError('Finished checkpoint lacks matching durable finalization intent')
    if status.stable_until < window.core_end and not status.finished:
        raise ValueError('Core exceeds native stable frontier')
    scope = digest(expected_key)
    for kind, label in (('turns', 'speaker'), ('events', 'event')):
        items = payload[kind]
        if not isinstance(items, list) or len(items) > MAX_ITEMS:
            raise ValueError('Output interval count exceeds bound')
        for item in items:
            if not isinstance(item, dict) or set(item) != {
                'start_frame', 'end_frame', label, 'instance_id', 'native_start_frame', 'native_end_frame', 'scope'
            }:
                raise ValueError('Invalid owned native interval')
            validate_intervals([{key: item[key] for key in ('start_frame', 'end_frame', label, 'instance_id')}],
                               window.core_start, window.core_end, label)
            lo, hi = (replay_start, status.activity_until) if kind == 'turns' else (window.read_start, window.read_end)
            integer(item['native_start_frame'], 'native interval start')
            integer(item['native_end_frame'], 'native interval end')
            if not (lo <= item['native_start_frame'] <= item['start_frame'] < item['end_frame']
                    <= item['native_end_frame'] <= hi):
                raise ValueError('Owned interval exceeds native support')
            if item['scope'] != (scope if kind == 'turns' else payload['window_id']):
                raise ValueError('Owned interval has wrong local identity scope')
    if not isinstance(tail, list) or len(tail) > 256:
        raise ValueError('Lexical tail exceeds bounded ownership context')
    for item in tail:
        if not isinstance(item, dict) or set(item) != {
            'start', 'end', 'text', 'ownership_anchor', 'routing_anchor', 'emitted', 'source_track_id'
        } or type(item['emitted']) is not bool:
            raise ValueError('Invalid persisted lexical tail')
        _lexical_support(item, window.read_start/RATE, window.read_end/RATE, scope)
    segments = payload['segments']
    if not isinstance(segments, list) or len(segments) > MAX_ITEMS:
        raise ValueError('Owned segment count exceeds bound')
    for item in segments:
        if not isinstance(item, dict) or set(item) != {'source_track_id', 'start', 'end', 'text', 'raw_text', 'native_unit_kind', 'ownership_anchor', 'routing_anchor'}:
            raise ValueError('Invalid owned lexical segment')
        _lexical_support(item, window.read_start/RATE, window.read_end/RATE, scope)
        if item['native_unit_kind'] not in ('native_word', 'native_phrase'):
            raise ValueError('Unknown native lexical unit kind')
        if not isinstance(item['raw_text'], str) or len(item['raw_text']) > 65536:
            raise ValueError('Invalid bounded raw text')


def _lexical_support(item, start, end, scope):
    import math
    a, b = item['start'], item['end']
    # Two float rebases can differ from an integer-frame endpoint by a few
    # ulps. Tolerate at most 1 ns (1/62500 PCM frame), never clip native times
    # or relax exact source-frame ownership. No relative/scale-growing margin.
    tolerance = 1e-9
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in (a, b)) or not (
        start-tolerance <= a < b <= end+tolerance
    ):
        raise ValueError('Lexical native support outside read window')
    if 'ownership_anchor' in item:
        anchor = item['ownership_anchor']
        if type(anchor) not in (int, float) or not math.isfinite(anchor) or not a <= anchor <= b:
            raise ValueError('Native lexical anchor outside support')
        if item.get('routing_anchor') != lexical_routing_anchor(a, b, anchor):
            raise ValueError('Persisted routing anchor differs from frozen native-support policy')
    if not isinstance(item['source_track_id'], str) or not item['source_track_id'].startswith(scope+':'):
        raise ValueError('Lexical unit has a mismatched epoch/source scope')
    if not isinstance(item['text'], str) or not item['text'] or len(item['text']) > 65536:
        raise ValueError('Invalid bounded lexical text')


def lexical_routing_anchor(start, end, native_anchor):
    """Separate ownership routing from the unchanged model-native timestamp.

    A positive-duration unit anchored at its support end belongs just inside
    that support. Other anchors use ordinary half-open core ownership. Snap
    only sub-nanosecond integer-PCM-frame representation drift, and keep the
    routing point inside the unchanged native support. No PCM is reassigned.
    """
    import math
    nearest = round(native_anchor*RATE)/RATE
    anchor = nearest if abs(nearest-native_anchor) <= 1e-9 else native_anchor
    if native_anchor == end:
        anchor = math.nextafter(min(anchor, end), -math.inf)
    return max(start, min(anchor, math.nextafter(end, -math.inf)))
