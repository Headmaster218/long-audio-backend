"""Bounded, passive evidence for one original native-owned PCM core.

This module never constructs a provider, decodes audio, dispatches a model or
writes a database. A witness only sees responses and WAVs already produced by
the ordinary Worker. Historical observations are not current runtime readiness.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import stat
import struct

from .fragment_core import Source, SourcePlan, Span
from .native_lexical_alternatives import SourceSupport
from .qwen_candidates import pcm16_to_float32le

RATE = 16000
MAX_FRAMES = 320000
MAX_PCM_BYTES = 2 * MAX_FRAMES
MAX_FILE_BYTES = MAX_PCM_BYTES + 1048576
MAX_PROOF_BYTES = 65536
MAX_PROVENANCE_BYTES = 32768
MAX_TURNS = 32
MAX_LABELS = 8
SELECTION_POLICY = {
    'schema': 'native-owned-one-cell-selection-v1', 'sample_rate': RATE,
    'max_frames': MAX_FRAMES, 'max_turns': MAX_TURNS, 'max_local_labels': MAX_LABELS,
    'trigger': 'native_overlap', 'intervals': 'half-open-distinct-local-labels',
    'support': 'entire-original-full-eof-native-core',
    'lexical_dispatch_available': False,
}
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_EPOCH = re.compile(r'[0-9a-f]{32}\Z')


class CaptureUnavailable(ValueError):
    """Optional proof is unavailable; never a replacement primary exception."""


def canonical(value, maximum=MAX_PROOF_BYTES):
    """Only call with already type/count/length-validated internal values."""
    data = json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False, ensure_ascii=True).encode('utf-8')
    if len(data) > maximum:
        raise CaptureUnavailable('proof_byte_limit')
    return data


def _digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


SELECTION_POLICY_SHA256 = _digest(SELECTION_POLICY)


def _hash(value):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise CaptureUnavailable('invalid_digest')
    return value


def _text(value, maximum, *, ascii_only=False):
    if type(value) is not str or not value or len(value) > maximum:
        raise CaptureUnavailable('invalid_string')
    try:
        if len(value.encode('ascii' if ascii_only else 'utf-8')) > maximum:
            raise CaptureUnavailable('invalid_string')
    except UnicodeError as exc:
        raise CaptureUnavailable('invalid_string') from exc
    return value


def _number(value, maximum=360.):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise CaptureUnavailable('invalid_number')
    return value


def exact_frame(value, maximum=MAX_FRAMES):
    """A stored/prepared value must be the exact float representation of n/RATE.

    No tolerance, floor, inward rounding or duration repair is permitted here.
    Raw native frontiers have a separate, explicitly witnessed round transform.
    """
    _number(value, maximum / RATE)
    frame = round(value * RATE)
    if value != frame / RATE:
        raise CaptureUnavailable('off_grid_geometry')
    return frame


def _exact(value, keys):
    if type(value) is not dict or len(value) != len(keys) or set(value) != set(keys):
        raise CaptureUnavailable('invalid_observed_fields')
    return value


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _check(check):
    if check is not None:
        check()


def _read_original(path, *, expected_signature=None, check=None, account_read=None):
    """One bounded regular-file read with nonblocking/no-follow open and checks.

    Descriptor/path identity detects local changes, not adversarial filesystem
    or VFS behavior. Cooperative checks do not impose a hard syscall deadline.
    """
    _text(path, 4096)
    if '\x00' in path or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_NONBLOCK'):
        raise CaptureUnavailable('unsafe_source_open')
    _check(check)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0))
    try:
        before = os.fstat(fd)
        signature = _signature(before)
        if (not stat.S_ISREG(before.st_mode) or not 44 <= before.st_size <= MAX_FILE_BYTES
                or expected_signature is not None and signature != expected_signature):
            raise CaptureUnavailable('unsupported_or_changed_source')
        entry = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(entry.st_mode) or _signature(entry) != signature:
            raise CaptureUnavailable('source_path_changed')
        blocks = bytearray()
        remaining = before.st_size
        while remaining:
            _check(check)
            size = min(65536, remaining)
            if account_read is not None:
                account_read(size)
            block = os.read(fd, size)
            _check(check)
            if not block:
                raise CaptureUnavailable('truncated_source')
            blocks.extend(block)
            remaining -= len(block)
        _check(check)
        if account_read is not None:
            account_read(1)
        extra = os.read(fd, 1)
        after = os.fstat(fd)
        entry = os.stat(path, follow_symlinks=False)
        _check(check)
        if (extra or _signature(after) != signature or not stat.S_ISREG(entry.st_mode)
                or _signature(entry) != signature):
            raise CaptureUnavailable('source_changed_during_read')
        return bytes(blocks), signature
    finally:
        os.close(fd)


def _pcm_from_wav(data):
    if type(data) is not bytes or not 44 <= len(data) <= MAX_FILE_BYTES:
        raise CaptureUnavailable('unsupported_wav')
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE' or struct.unpack_from('<I', data, 4)[0] + 8 != len(data):
        raise CaptureUnavailable('physical_riff_eof_mismatch')
    position, count, fmt, pcm = 12, 0, False, None
    while position < len(data):
        count += 1
        if count > 128 or position + 8 > len(data):
            raise CaptureUnavailable('wav_chunk_limit')
        kind, size = struct.unpack_from('<4sI', data, position)
        start = position + 8
        end = start + size
        position = end + (size & 1)
        if position > len(data):
            raise CaptureUnavailable('truncated_wav_chunk')
        if kind == b'fmt ':
            if fmt or size not in (16, 18):
                raise CaptureUnavailable('unsupported_wav_format')
            if struct.unpack_from('<HHIIHH', data, start) != (1, 1, RATE, RATE * 2, 2, 16):
                raise CaptureUnavailable('original_pcm16_mono_16k_required')
            if size == 18 and data[start + 16:end] != b'\0\0':
                raise CaptureUnavailable('unsupported_wav_extension')
            fmt = True
        elif kind == b'data':
            if not fmt or pcm is not None or not 2 <= size <= MAX_PCM_BYTES or size % 2:
                raise CaptureUnavailable('invalid_pcm_extent')
            pcm = data[start:end]
    if pcm is None or len(data) > len(pcm) + 1048576:
        raise CaptureUnavailable('missing_or_oversized_pcm_container')
    return pcm


@dataclass(frozen=True, slots=True)
class SourceMaterial:
    path: str
    digest: str
    frames: int
    signature: tuple[int, int, int, int, int]
    source_generation: str
    plan_sha256: str

    def __post_init__(self):
        _text(self.path, 4096)
        for value in (self.digest, self.source_generation, self.plan_sha256):
            _hash(value)
        if type(self.frames) is not int or not 1 <= self.frames <= MAX_FRAMES:
            raise CaptureUnavailable('source_frame_limit')
        if (type(self.signature) is not tuple or len(self.signature) != 5
                or any(type(item) is not int or not -(1 << 64) < item < (1 << 64)
                       for item in self.signature)
                or not 44 <= self.signature[2] <= 2 * self.frames + 1048576):
            raise CaptureUnavailable('invalid_source_signature')

    @classmethod
    def inspect(cls, chunk, *, check=None, account_read=None):
        if type(chunk) is not dict:
            raise CaptureUnavailable('private_chunk_required')
        for field in ('stream_id', 'stream_membership', 'continuation', 'normalized', 'fragment_source'):
            if chunk.get(field) not in (None, False):
                raise CaptureUnavailable('unsupported_source_mode')
        if chunk.get('processing_mode', 'legacy') != 'legacy':
            raise CaptureUnavailable('unsupported_source_mode')
        if exact_frame(chunk.get('timeline_start')) != 0 or exact_frame(chunk.get('next_offset')) != 0:
            raise CaptureUnavailable('nonzero_source_origin_or_resume')
        frames = exact_frame(chunk.get('duration'))
        if not 1 <= frames <= MAX_FRAMES:
            raise CaptureUnavailable('source_frame_limit')
        digest = _hash(chunk.get('digest'))
        path = _text(chunk.get('path'), 4096)
        data, signature = _read_original(path, check=check, account_read=account_read)
        if hashlib.sha256(data).hexdigest() != digest:
            raise CaptureUnavailable('source_digest_mismatch')
        pcm = _pcm_from_wav(data)
        if len(pcm) != 2 * frames:
            raise CaptureUnavailable('declared_source_eof_mismatch')
        generation = _digest({'schema': 'original-pcm-source-generation-v1',
                              'sha256': digest, 'frames': frames,
                              'sample_rate': RATE, 'channels': 1, 'sample_width': 2,
                              'encoding': 'wav-pcm16le'})
        source = Source(generation, digest, RATE, 1, frames)
        plan = SourcePlan((source,), (Span(generation, 0, frames, 0, frames, 0),), (), (), final=True)
        plan_hash = hashlib.sha256(plan.to_json().encode('utf-8')).hexdigest()
        _check(check)
        return cls(path, digest, frames, signature, generation, plan_hash)

    def _read_pcm(self, *, check=None, account_read=None):
        data, _ = _read_original(self.path, expected_signature=self.signature,
                                 check=check, account_read=account_read)
        if hashlib.sha256(data).hexdigest() != self.digest:
            raise CaptureUnavailable('source_digest_changed')
        pcm = _pcm_from_wav(data)
        if len(pcm) != self.frames * 2:
            raise CaptureUnavailable('source_geometry_changed')
        _check(check)
        return pcm

    def verify(self, *, check=None, account_read=None):
        """Rehash known original bytes without allocating a response-owned PCM.

        Initial inspection already binds complete geometry to this digest. Two
        simultaneously live 32KiB blocks bound the hash loop's transfer buffers;
        API leases account for them alongside projections and response bodies.
        """
        _check(check)
        if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_NONBLOCK'):
            raise CaptureUnavailable('unsafe_source_open')
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | getattr(os, 'O_CLOEXEC', 0))
        try:
            before = os.fstat(fd)
            entry = os.stat(self.path, follow_symlinks=False)
            if (not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(entry.st_mode)
                    or not 44 <= before.st_size <= MAX_FILE_BYTES
                    or _signature(before) != self.signature or _signature(entry) != self.signature):
                raise CaptureUnavailable('unsupported_or_changed_source')
            remaining, digest = before.st_size, hashlib.sha256()
            while remaining:
                _check(check)
                size = min(32768, remaining)
                if account_read is not None:
                    account_read(size)
                block = os.read(fd, size)
                _check(check)
                if not block:
                    raise CaptureUnavailable('truncated_source')
                digest.update(block)
                remaining -= len(block)
            _check(check)
            if account_read is not None:
                account_read(1)
            extra = os.read(fd, 1)
            after = os.fstat(fd)
            entry = os.stat(self.path, follow_symlinks=False)
            _check(check)
            if (extra or not stat.S_ISREG(entry.st_mode)
                    or _signature(after) != self.signature or _signature(entry) != self.signature):
                raise CaptureUnavailable('source_changed_during_read')
            if digest.hexdigest() != self.digest:
                raise CaptureUnavailable('source_digest_changed')
            _check(check)
        finally:
            os.close(fd)


# A bounded copy of the unchanged producer's identity schema/constants. Keeping
# this validation here avoids importing the native provider or loading ctypes.
_NATIVE_FIXED = {
    'diarization_backend': 'nemotron3-native', 'diarization_runtime': 'NeMo-Speech.cpp',
    'diarization_runtime_version': '0.2.0',
    'diarization_model_sha256': '08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1',
    'diarization_model_license': 'OpenMDW-1.1',
    'diarization_model_license_url': 'https://openmdw.ai/license/1-1/',
    'diarization_preset': 'v3-offline',
    'diarization_geometry': {'spkcache': 264, 'fifo': 0, 'chunk': 264, 'update_period': 188,
                             'left_context': 1, 'right_context': 1, 'frame_seconds': .08},
    'diarization_postprocessing': {'onset': .641, 'offset': .561, 'pad_onset': .229,
                                   'pad_offset': .079, 'min_duration_on': .511, 'min_duration_off': .296},
    'diarization_local_capacity': 8, 'diarization_morphology_guard_seconds': 2.,
    'diarization_global_identity': 'separate ERes2Net evidence and identity registry',
    'diarization_capacity_warning': 'At most 8 distinct speakers in one epoch, including replay/context; not unlimited speakers.',
    'diarization_native_requested_threads': 4,
}
_CPU_WARNING = 'Affinity unavailable: only OpenMP limits set; native CPU cap unverified on this platform.'


def _match_fixed(value, expected):
    if type(value) is not type(expected):
        raise CaptureUnavailable('producer_schema_type_mismatch')
    if type(expected) is dict:
        _exact(value, expected)
        for key, child in expected.items():
            _match_fixed(value[key], child)
    elif value != expected:
        raise CaptureUnavailable('producer_identity_mismatch')


def validate_observed_provenance(value):
    """Validate an actual reset observation, not settings-derived expectations."""
    keys = set(_NATIVE_FIXED) | {'diarization_library_sha256', 'diarization_epoch_input_cap_seconds',
                                'diarization_runtime_files_sha256', 'diarization_fingerprint',
                                'diarization_wrapper_cpu_limit', 'diarization_cpu_affinity_enforced'}
    if type(value) is not dict or len(value) > len(keys) + 1:
        raise CaptureUnavailable('producer_schema_mismatch')
    affinity = value.get('diarization_cpu_affinity_enforced')
    if type(affinity) is not bool:
        raise CaptureUnavailable('producer_affinity_unobserved')
    if not affinity:
        keys.add('diarization_cpu_cap_warning')
    _exact(value, keys)
    for key, expected in _NATIVE_FIXED.items():
        _match_fixed(value[key], expected)
    library = _hash(value['diarization_library_sha256'])
    fingerprint = _hash(value['diarization_fingerprint'])
    cap = value['diarization_epoch_input_cap_seconds']
    if type(cap) is not float or not 1 <= _number(cap) <= 360:
        raise CaptureUnavailable('producer_epoch_cap')
    cpu = value['diarization_wrapper_cpu_limit']
    if type(cpu) is not int or cpu not in (1, 2):
        raise CaptureUnavailable('producer_cpu_limit')
    if not affinity:
        _match_fixed(value['diarization_cpu_cap_warning'], _CPU_WARNING)
    files = value['diarization_runtime_files_sha256']
    if type(files) is not dict or not 1 <= len(files) <= 32:
        raise CaptureUnavailable('producer_runtime_files_limit')
    for name, digest in files.items():
        _text(name, 192, ascii_only=True)
        if name in ('.', '..') or '/' in name or '\\' in name or '\0' in name:
            raise CaptureUnavailable('producer_runtime_filename')
        _hash(digest)
    if library not in files.values():
        raise CaptureUnavailable('producer_library_not_bound')
    base = {key: value[key] for key in value if key not in (
        'diarization_fingerprint', 'diarization_wrapper_cpu_limit',
        'diarization_cpu_affinity_enforced', 'diarization_cpu_cap_warning')}
    if hashlib.sha256(canonical(base, MAX_PROVENANCE_BYTES)).hexdigest() != fingerprint:
        raise CaptureUnavailable('producer_fingerprint_mismatch')
    # Canonical round-trip retains no mutable producer-owned dictionaries.
    return json.loads(canonical(value, MAX_PROVENANCE_BYTES))


def _turns(value, frames, epoch=None):
    if type(value) is not list or len(value) > MAX_TURNS:
        raise CaptureUnavailable('native_turn_limit')
    turns, labels = [], set()
    for item in value:
        _exact(item, {'start', 'end', 'speaker_local_id'})
        start, end = exact_frame(item['start'], frames), exact_frame(item['end'], frames)
        label = _text(item['speaker_local_id'], 128, ascii_only=True)
        if epoch is not None and label not in {f'{epoch}:speaker_{i}' for i in range(1, MAX_LABELS + 1)}:
            raise CaptureUnavailable('native_local_label_mismatch')
        if not start < end:
            raise CaptureUnavailable('native_turn_geometry')
        labels.add(label)
        if len(labels) > MAX_LABELS:
            raise CaptureUnavailable('native_label_limit')
        turns.append({'start_frame': start, 'end_frame': end, 'speaker_local_id': label})
    return turns


def select_overlap(turns, frames):
    """One-cell native_overlap gate; duplicate/touching labels are not overlap."""
    if type(frames) is not int or not 1 <= frames <= MAX_FRAMES:
        raise CaptureUnavailable('source_frame_limit')
    bounded = _turns(turns, frames)
    return any(a['speaker_local_id'] != b['speaker_local_id']
               and max(a['start_frame'], b['start_frame']) < min(a['end_frame'], b['end_frame'])
               for index, a in enumerate(bounded) for b in bounded[index + 1:])


@dataclass(frozen=True, slots=True)
class CapturedSupport:
    source: SourceMaterial
    proof: bytes
    pcm16le: bytes
    selected: bool
    support: SourceSupport | None
    selection_policy_sha256: str
    reason: str

    def __post_init__(self):
        if (type(self.source) is not SourceMaterial or type(self.selected) is not bool
                or type(self.proof) is not bytes or not 1 <= len(self.proof) <= MAX_PROOF_BYTES
                or type(self.pcm16le) is not bytes or len(self.pcm16le) > MAX_PCM_BYTES
                or self.selection_policy_sha256 != SELECTION_POLICY_SHA256
                or type(self.selection_policy_sha256) is not str):
            raise CaptureUnavailable('invalid_capture_envelope')
        if self.selected:
            if (type(self.support) is not SourceSupport or self.reason != 'available'
                    or len(self.pcm16le) != self.source.frames * 2
                    or self.support.source_generation != self.source.source_generation
                    or self.support.plan_sha256 != self.source.plan_sha256
                    or self.support.source_frames != self.source.frames
                    or self.support.start_frame != 0 or self.support.core_start_frame != 0
                    or self.support.end_frame != self.source.frames
                    or self.support.core_end_frame != self.source.frames
                    or hashlib.sha256(self.pcm16le).hexdigest() != self.support.pcm16le_sha256):
                raise CaptureUnavailable('invalid_selected_capture')
        elif self.support is not None or self.pcm16le or len(self.proof) > 512 or self.reason != 'no_native_overlap':
            raise CaptureUnavailable('invalid_unselected_capture')


class NativeWitness:
    """Passive fixed-sequence reset/push/finish/turns evidence, at most once.

    Observe methods swallow only their own ordinary failures. They do not issue
    requests or replace producer/extractor exceptions or return values.
    """
    def __init__(self, source):
        if type(source) is not SourceMaterial:
            raise CaptureUnavailable('validated_source_required')
        self.source = source
        self.failure = None
        self.provenance = None
        self.epoch = None
        self.states = []
        self.turns = None
        self.material = {}
        self.analyzer_pcm = None
        self.done = False

    def fail(self, reason='optional_observation_failed'):
        if self.failure is None:
            self.failure = reason if type(reason) is str and re.fullmatch('[a-z_]{1,80}', reason) else 'optional_observation_failed'
        self.analyzer_pcm = None

    def _state(self, value):
        _exact(value, {'epoch_id', 'input_seconds', 'processed_seconds', 'stable_seconds',
                       'finished', 'local_capacity', 'fingerprint'})
        if (value['epoch_id'] != self.epoch or type(value['epoch_id']) is not str
                or value['fingerprint'] != self.provenance['diarization_fingerprint']
                or type(value['fingerprint']) is not str
                or type(value['local_capacity']) is not int or value['local_capacity'] != 8
                or type(value['finished']) is not bool):
            raise CaptureUnavailable('native_state_identity')
        result = {'epoch_id': self.epoch, 'fingerprint': value['fingerprint'],
                  'local_capacity': 8, 'finished': value['finished']}
        for key in ('input_seconds', 'processed_seconds', 'stable_seconds'):
            # Native arithmetic may produce either adjacent binary float at
            # the exact EOF. Admit its unchanged prepared round transform;
            # never use an epsilon or round a committed/source boundary.
            raw = _number(value[key], 360.)
            result[key] = raw
            result[key + '_hex'] = float(raw).hex()
            result[key + '_prepared_frame'] = round(raw * RATE)
            if result[key + '_prepared_frame'] > self.source.frames:
                raise CaptureUnavailable('native_frontier_past_eof')
        if not (result['stable_seconds_prepared_frame'] <= result['processed_seconds_prepared_frame']
                <= result['input_seconds_prepared_frame']):
            # finish() marks all input stable even if the actual processed
            # frontier is short. Such a source is unavailable, never padded.
            raise CaptureUnavailable('native_frontier_short_or_reversed')
        return result

    def observe(self, response):
        if self.failure is not None:
            return
        try:
            if self.done or self.turns is not None:
                raise CaptureUnavailable('extra_native_response')
            count = len(self.states)
            if count == 0:
                _exact(response, {'state', 'provenance'})
                self.provenance = validate_observed_provenance(response['provenance'])
                if type(response['state']) is not dict:
                    raise CaptureUnavailable('native_reset_state')
                epoch = response['state'].get('epoch_id')
                if type(epoch) is not str or _EPOCH.fullmatch(epoch) is None:
                    raise CaptureUnavailable('native_epoch')
                self.epoch = epoch
                state = self._state(response['state'])
                if state['finished'] or any(state[name] != 0 for name in ('input_seconds', 'processed_seconds', 'stable_seconds')):
                    raise CaptureUnavailable('native_reset_not_empty')
                self.states.append(state)
            elif count in (1, 2):
                state = self._state(response)
                if 'native' not in self.material or exact_frame(state['input_seconds']) != self.source.frames:
                    raise CaptureUnavailable('native_push_material_missing')
                if state['finished'] is not (count == 2):
                    raise CaptureUnavailable('native_action_sequence')
                if count == 2 and any(state[name + '_prepared_frame'] != self.source.frames
                                      for name in ('input_seconds', 'processed_seconds', 'stable_seconds')):
                    raise CaptureUnavailable('native_incomplete_eof')
                if any(state[name + '_prepared_frame'] < self.states[-1][name + '_prepared_frame']
                       for name in ('input_seconds', 'processed_seconds', 'stable_seconds')):
                    raise CaptureUnavailable('native_frontier_reversed')
                self.states.append(state)
            else:
                _exact(response, {'turns'})
                bounded = _turns(response['turns'], self.source.frames, self.epoch)
                self.turns = bounded
        except Exception as exc:
            self.fail(str(exc) if isinstance(exc, CaptureUnavailable) else 'native_observer_failed')

    def observe_pcm(self, kind, path, start, end):
        if self.failure is not None:
            return
        try:
            if kind not in ('native', 'analyzer') or kind in self.material or self.done:
                raise CaptureUnavailable('extra_material_observation')
            if exact_frame(start) != 0 or exact_frame(end) != self.source.frames:
                raise CaptureUnavailable('material_not_full_original_core')
            if kind == 'native' and len(self.states) != 1 or kind == 'analyzer' and self.turns is None:
                raise CaptureUnavailable('material_sequence')
            data, _ = _read_original(os.fspath(path))
            produced = _pcm_from_wav(data)
            original = self.source._read_pcm()
            if produced != original:
                raise CaptureUnavailable('submitted_pcm_differs_from_original')
            item = {'frames': len(produced) // 2,
                    'pcm16le_sha256': hashlib.sha256(produced).hexdigest(),
                    'float32le_sha256': hashlib.sha256(pcm16_to_float32le(produced)).hexdigest()}
            self.material[kind] = item
            if kind == 'analyzer':
                self.analyzer_pcm = produced
        except Exception as exc:
            self.fail(str(exc) if isinstance(exc, CaptureUnavailable) else 'material_observer_failed')

    def finalize(self, native, offset, end):
        if self.failure is not None:
            raise CaptureUnavailable(self.failure)
        if self.done:
            raise CaptureUnavailable('second_core_unavailable')
        self.done = True
        if len(self.states) != 3 or self.turns is None or set(self.material) != {'native', 'analyzer'}:
            raise CaptureUnavailable('incomplete_native_observation')
        if self.material['native'] != self.material['analyzer'] or self.analyzer_pcm is None:
            raise CaptureUnavailable('native_analyzer_material_mismatch')
        _exact(native, {'end', 'extract_start', 'extract_end', 'turns', 'provenance', 'state'})
        if (exact_frame(offset) != 0 or exact_frame(end) != self.source.frames
                or exact_frame(native['end']) != self.source.frames
                or exact_frame(native['extract_start']) != 0
                or exact_frame(native['extract_end']) != self.source.frames):
            raise CaptureUnavailable('changed_or_partial_final_core')
        if validate_observed_provenance(native['provenance']) != self.provenance:
            raise CaptureUnavailable('prepared_provenance_changed')
        if _turns(native['turns'], self.source.frames, self.epoch) != self.turns:
            raise CaptureUnavailable('prepared_turns_changed')
        state = native['state']
        _exact(state, {'epoch_id', 'epoch_index', 'owned_start', 'owned_end', 'replay_start',
                       'input_until', 'processed_until', 'stable_until', 'finished', 'provenance'})
        if (type(state['epoch_id']) is not str or state['epoch_id'] != self.epoch
                or type(state['epoch_index']) is not int or state['epoch_index'] != 0
                or state['finished'] is not True
                or exact_frame(state['owned_start']) != 0
                or exact_frame(state['owned_end'], 300 * RATE) != 300 * RATE
                or exact_frame(state['replay_start']) != 0):
            raise CaptureUnavailable('prepared_epoch_geometry')
        if validate_observed_provenance(state['provenance']) != self.provenance:
            raise CaptureUnavailable('prepared_state_provenance_changed')
        for name in ('input', 'processed', 'stable'):
            if exact_frame(state[name + '_until']) != self.states[-1][name + '_seconds_prepared_frame']:
                raise CaptureUnavailable('native_prepared_transform_mismatch')
        self.source.verify()
        selected = select_overlap(native['turns'], self.source.frames)
        if not selected:
            self.analyzer_pcm = None
            marker = canonical({'schema': 'native-owned-unselected-v1', 'reason': 'no_native_overlap',
                                'source_generation': self.source.source_generation,
                                'selection_policy_sha256': SELECTION_POLICY_SHA256,
                                'lexical_dispatch_available': False}, 512)
            return CapturedSupport(self.source, marker, b'', False, None,
                                   SELECTION_POLICY_SHA256, 'no_native_overlap')
        core = {'schema': 'observed-native-owned-core-v1', 'source_generation': self.source.source_generation,
                'plan_sha256': self.source.plan_sha256, 'core_start_frame': 0,
                'core_end_frame': self.source.frames, 'source_frames': self.source.frames,
                'selection_policy_sha256': SELECTION_POLICY_SHA256,
                'native_action_sequence': ['reset', 'push', 'finish', 'turns'],
                'native_states': self.states, 'native_turns': self.turns,
                'native_provenance': self.provenance, 'material': self.material,
                'prepared_frames': {'input': self.source.frames, 'processed': self.source.frames,
                                    'stable': self.source.frames},
                'prepared_transform': 'round(raw_seconds*16000)',
                'lexical_dispatch_available': False}
        runtime = _digest(self.provenance)
        support = SourceSupport(self.source.source_generation, self.source.plan_sha256, runtime,
                                _digest(core), self.source.frames, 0, self.source.frames,
                                0, self.source.frames, self.material['analyzer']['pcm16le_sha256'],
                                self.material['analyzer']['float32le_sha256'])
        proof = canonical({'schema': 'native-owned-support-proof-v1', 'support': support.to_dict(),
                           'observed_core': core, 'observed_producer_only': True,
                           'current_runtime_ready': False})
        pcm = self.analyzer_pcm
        self.analyzer_pcm = None
        return CapturedSupport(self.source, proof, pcm, True, support, SELECTION_POLICY_SHA256, 'available')
