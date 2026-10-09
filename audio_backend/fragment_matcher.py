"""Conservative, offline-only exact PCM boundary evidence.

This module has no text/model inputs. Equal samples cannot prove common capture
provenance or exclude a real digital replay. Those are explicit declarations.
No production worker imports this prototype.
"""
from array import array
from dataclasses import asdict, dataclass
import math
import sys

POLICY = 'exact-pcm16-v1'
MAX_MATCH_SECONDS = 30
MIN_OVERLAP_MS = 500
ANCHOR_MS = 80
MIN_ANCHOR_SPACING_MS = 100
ANCHOR_COUNT = 3
MIN_RMS_DBFS = -45
PERIODIC_HZ = 400
MIN_PERIOD_MS = 20
PERIODIC_REJECT = .95
MAX_CANDIDATES = 32
MAX_ANCHOR_HITS = 128


@dataclass(frozen=True)
class Continuity:
    """Independent provenance assertions, never inferred from audio equality.

    continuous means no missing interval is declared, but does not mean that
    there is no duplication. replay_possible defaults to True (fail closed).
    gap_frames is in the common native sample rate and is never audio-filled.
    """
    relation: str = 'unknown'
    same_capture: bool = False
    replay_possible: bool = True
    gap_frames: int | None = None

    def __post_init__(self):
        if self.relation not in {'continuous', 'gap', 'unknown'}:
            raise ValueError('Invalid continuity declaration')
        if type(self.same_capture) is not bool or type(self.replay_possible) is not bool:
            raise ValueError('Capture and replay declarations must be explicit booleans')
        if self.relation == 'gap':
            if type(self.gap_frames) is not int or self.gap_frames <= 0:
                raise ValueError('Known gaps need a positive native-frame length')
        elif self.gap_frames is not None:
            raise ValueError('Only known gaps have a numeric gap length')


@dataclass(frozen=True)
class BoundaryEvidence:
    outcome: str
    reason: str
    trim_frames: int = 0
    candidate_frames: tuple[int, ...] = ()
    searched_left_frames: int = 0
    searched_right_frames: int = 0
    periodic_peak: float | None = None
    policy: str = POLICY
    anchor_hits: int = 0
    search_exhausted: bool = False

    def to_dict(self):
        return asdict(self)


def _samples(pcm):
    values = array('h')
    values.frombytes(pcm)
    if sys.byteorder != 'little':
        values.byteswap()
    return values


def _informative(pcm, rate, channels):
    values = _samples(pcm)
    frames = len(values) // channels
    width = (rate * ANCHOR_MS + 999) // 1000
    starts = (0, (frames - width) // 2, frames - width)
    if starts[1] - starts[0] < (rate * MIN_ANCHOR_SPACING_MS + 999) // 1000:
        return False
    minimum_power = (32768 * 10 ** (MIN_RMS_DBFS / 20)) ** 2
    for start in starts:
        for ch in range(channels):
            anchor = values[start * channels + ch:(start + width) * channels:channels]
            power = sum(x * x for x in anchor) / len(anchor)
            mean = sum(anchor) / len(anchor)
            if power <= minimum_power or power - mean * mean <= 0:
                return False
    return True


def _periodic_peak(pcm, rate, channels):
    """Bounded 400 Hz decimation rejection, not a positive confidence score.

    Deliberately no approximate matching or periodicity-removal transformation.
    This simple screen is conservative and has unmeasured real-audio recall.
    """
    values = _samples(pcm)
    step = max(1, rate // PERIODIC_HZ)
    effective_rate = rate / step
    peak = -1.0
    for ch in range(channels):
        x = list(values[ch::channels * step])
        mean = sum(x) / len(x)
        x = [v - mean for v in x]
        energy = [0.0]
        for v in x:
            energy.append(energy[-1] + v * v)
        for lag in range(math.ceil(effective_rate * MIN_PERIOD_MS / 1000), len(x) // 2 + 1):
            n = len(x) - lag
            denominator = math.sqrt(energy[n] * (energy[-1] - energy[lag]))
            if denominator <= 0:
                return 1.0
            numerator = sum(x[i] * x[i + lag] for i in range(n))
            correlation = max(-1.0, min(1.0, numerator / denominator))
            peak = max(peak, correlation)
            if peak >= PERIODIC_REJECT:
                return peak
    return peak


def _match_boundary(left_pcm, right_pcm, *, sample_rate, channels, declaration, information_gate,
                    policy, rejection_reason='weak_or_uninformative_anchors', periodic_gate=None):
    """Infer suffix/prefix offsets from bounded bytes; no oracle/expected offset.

    Every channel and every frame in a candidate must compare equal. bytes.find
    is a bounded exact anchor search; a hash collision cannot authorize a trim.
    The maximal accepted overlap is 30s. Candidate offsets >=80ms are inspected;
    smaller overlaps remain unresolved. This is not proof that no overlap exists.
    """
    if not isinstance(declaration, Continuity):
        raise TypeError('Explicit Continuity object required')
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 48000 or type(channels) is not int or channels not in (1, 2):
        raise ValueError('Matcher only supports native PCM16 mono/stereo 8..48kHz')
    if not isinstance(left_pcm, (bytes, bytearray)) or not isinstance(right_pcm, (bytes, bytearray)):
        raise TypeError("PCM inputs must be bytes or bytearray")
    frame_bytes = 2 * channels
    if len(left_pcm) % frame_bytes or len(right_pcm) % frame_bytes:
        raise ValueError('PCM must contain complete interleaved frames')
    left_frames, right_frames = len(left_pcm) // frame_bytes, len(right_pcm) // frame_bytes
    if max(left_frames, right_frames) > sample_rate * MAX_MATCH_SECONDS:
        raise ValueError('Matcher reads must be bounded to 30 seconds per edge')
    common = dict(searched_left_frames=left_frames, searched_right_frames=right_frames, policy=policy)
    anchor_frames = (sample_rate * ANCHOR_MS + 999) // 1000
    if min(left_frames, right_frames) < anchor_frames:
        return BoundaryEvidence('abstain', 'too_short_for_search', **common)
    anchor = right_pcm[:anchor_frames * frame_bytes]
    candidates = []
    anchor_hits = 0
    # Only offsets whose suffix can fit the supplied right edge are relevant.
    position = max(0, left_frames - right_frames) * frame_bytes
    while True:
        position = left_pcm.find(anchor, position)
        if position < 0:
            break
        anchor_hits += 1
        common['anchor_hits'] = anchor_hits
        if anchor_hits > MAX_ANCHOR_HITS:
            return BoundaryEvidence('abstain', 'anchor_search_limit_ambiguous',
                                    candidate_frames=tuple(candidates), search_exhausted=True, **common)
        if position % frame_bytes == 0:
            count = left_frames - position // frame_bytes
            if left_pcm[position:] == right_pcm[:count * frame_bytes]:
                candidates.append(count)
                if len(candidates) > MAX_CANDIDATES:
                    return BoundaryEvidence('abstain', 'candidate_limit_ambiguous',
                                            candidate_frames=tuple(candidates), search_exhausted=True, **common)
        position += 1
    common['candidate_frames'] = tuple(candidates)
    if not candidates:
        return BoundaryEvidence('abstain', 'no_exact_overlap_evidence', **common)
    if len(candidates) != 1:
        return BoundaryEvidence('abstain', 'multiple_exact_offsets', **common)
    frames = candidates[0]
    if frames < (sample_rate * MIN_OVERLAP_MS + 999) // 1000:
        return BoundaryEvidence('abstain', 'overlap_below_minimum', **common)
    pcm = right_pcm[:frames * frame_bytes]
    if not information_gate(pcm, sample_rate, channels):
        return BoundaryEvidence('abstain', rejection_reason, **common)
    peak = (periodic_gate or _periodic_peak)(pcm, sample_rate, channels)
    common['periodic_peak'] = peak
    if peak >= PERIODIC_REJECT:
        return BoundaryEvidence('abstain', 'periodic_evidence', **common)
    if declaration.relation != 'continuous' or not declaration.same_capture:
        return BoundaryEvidence('abstain', 'capture_continuity_not_established', **common)
    if declaration.replay_possible:
        return BoundaryEvidence('abstain', 'real_replay_not_excluded', **common)
    return BoundaryEvidence('trim', 'unique_informative_exact_overlap', trim_frames=frames, **common)


def match_boundary(left_pcm, right_pcm, *, sample_rate, channels, declaration):
    """V1 policy entry point; original absolute-RMS information gate unchanged."""
    return _match_boundary(left_pcm, right_pcm, sample_rate=sample_rate, channels=channels,
                           declaration=declaration, information_gate=_informative, policy=POLICY)
