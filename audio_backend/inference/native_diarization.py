"""Opt-in, bounded NeMo-Speech.cpp 0.2.0 native diarization provider.

The caller owns stream isolation, durable epoch checkpoints, replay, and process
resource supervision. No native state or global speaker identity is serialized
here. Upload/ASR boundaries must never call finish().
"""
from __future__ import annotations

import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path

MODEL_SHA256 = '08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1'
RUNTIME_VERSION = '0.2.0'
GEOMETRY = {'spkcache': 264, 'fifo': 0, 'chunk': 264, 'update_period': 188,
            'left_context': 1, 'right_context': 1, 'frame_seconds': .08}
POSTPROCESSING = {'onset': .641, 'offset': .561, 'pad_onset': .229,
                  'pad_offset': .079, 'min_duration_on': .511, 'min_duration_off': .296}
MORPHOLOGY_GUARD_SECONDS = 2.
MAX_EPOCH_INPUT_SECONDS = 360.
MAX_PUSH_SECONDS = 60.
MAX_SEGMENTS = 32768


def build_expected_native_provenance(*, model_sha256, library_sha256,
                                     runtime_version, epoch_max_seconds,
                                     runtime_files_sha256):
    """Construct expected metadata from explicit identities, without model I/O.

    These are supplied expectations, not observations of a loaded runtime. The
    live provider verifies its runtime, capacity, and cadence before calling
    this function. Wrapper CPU observations are appended later and are not part
    of the historical base fingerprint.
    """
    provenance = {
        'diarization_backend': 'nemotron3-native', 'diarization_runtime': 'NeMo-Speech.cpp',
        'diarization_runtime_version': runtime_version,
        'diarization_library_sha256': library_sha256, 'diarization_model_sha256': model_sha256,
        'diarization_model_license': 'OpenMDW-1.1',
        'diarization_model_license_url': 'https://openmdw.ai/license/1-1/',
        'diarization_preset': 'v3-offline', 'diarization_geometry': dict(GEOMETRY),
        'diarization_postprocessing': dict(POSTPROCESSING), 'diarization_local_capacity': 8,
        'diarization_epoch_input_cap_seconds': float(epoch_max_seconds),
        'diarization_morphology_guard_seconds': MORPHOLOGY_GUARD_SECONDS,
        'diarization_global_identity': 'separate ERes2Net evidence and identity registry',
        'diarization_capacity_warning': 'At most 8 distinct speakers in one epoch, including replay/context; not unlimited speakers.',
        'diarization_native_requested_threads': 4,
        'diarization_runtime_files_sha256': dict(runtime_files_sha256),
    }
    provenance['diarization_fingerprint'] = hashlib.sha256(
        json.dumps(provenance, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return provenance


def _sha256(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


class _ModelConfig(C.Structure):
    _fields_ = [('size', C.c_size_t), ('model_path', C.c_char_p), ('gpu', C.c_int32),
                ('preset', C.c_char_p), ('chunk_frames', C.c_int32),
                ('right_context_frames', C.c_int32), ('left_context_frames', C.c_int32),
                ('fifo_frames', C.c_int32), ('spkcache_frames', C.c_int32),
                ('update_period_frames', C.c_int32)]


class _Segment(C.Structure):
    _fields_ = [('start_time', C.c_double), ('end_time', C.c_double), ('speaker', C.c_int32)]


class _CAPI:
    def __init__(self, library_path):
        # Keep Windows DLL-search handles alive while the runtime is loaded.
        self._dll_directory = None
        if os.name == 'nt':
            self._dll_directory = os.add_dll_directory(str(Path(library_path).parent))
        self.lib = C.CDLL(str(library_path))
        lib = self.lib
        lib.nemo_speech_asr_version.restype = C.c_char_p
        reported_version = lib.nemo_speech_asr_version().decode('utf8')
        if reported_version != f'nemo-speech-asr {RUNTIME_VERSION}':
            raise ValueError(f'Native diarization requires runtime {RUNTIME_VERSION}, got {reported_version}')
        self.version = RUNTIME_VERSION
        lib.nemo_speech_asr_last_error.restype = C.c_char_p
        lib.nemo_speech_diar_create.argtypes = [C.POINTER(_ModelConfig), C.POINTER(C.c_void_p)]
        lib.nemo_speech_diar_destroy.argtypes = [C.c_void_p]
        lib.nemo_speech_diar_stream_open.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
        lib.nemo_speech_diar_stream_close.argtypes = [C.c_void_p]
        lib.nemo_speech_diar_stream_push_f32.argtypes = [C.c_void_p, C.POINTER(C.c_float), C.c_size_t, C.c_int32]
        lib.nemo_speech_diar_stream_finish.argtypes = [C.c_void_p]
        lib.nemo_speech_diar_num_speakers.argtypes = [C.c_void_p]
        lib.nemo_speech_diar_seconds_per_frame.argtypes = [C.c_void_p]
        lib.nemo_speech_diar_seconds_per_frame.restype = C.c_double
        lib.nemo_speech_diar_frame_count.argtypes = [C.c_void_p]
        lib.nemo_speech_diar_frame_count.restype = C.c_int64
        lib.nemo_speech_diar_segments.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(_Segment),
                                                C.c_size_t, C.POINTER(C.c_size_t)]

    def check(self, code):
        if code:
            message = self.lib.nemo_speech_asr_last_error()
            raise RuntimeError(f'Native diarizer: {message.decode("utf8", "replace") if message else code}')

    def create(self, model_path):
        config = _ModelConfig(size=C.sizeof(_ModelConfig), model_path=os.fsencode(model_path),
                              gpu=-1, preset=b'v3-offline', left_context_frames=-1)
        model = C.c_void_p()
        self.check(self.lib.nemo_speech_diar_create(C.byref(config), C.byref(model)))
        return model

    def destroy(self, model):
        self.lib.nemo_speech_diar_destroy(model)

    def open(self, model):
        stream = C.c_void_p()
        self.check(self.lib.nemo_speech_diar_stream_open(model, C.byref(stream)))
        return stream

    def close(self, stream):
        self.lib.nemo_speech_diar_stream_close(stream)

    def push(self, stream, block):
        self.check(self.lib.nemo_speech_diar_stream_push_f32(
            stream, block.ctypes.data_as(C.POINTER(C.c_float)), len(block), 16000))

    def finish(self, stream):
        self.check(self.lib.nemo_speech_diar_stream_finish(stream))

    def frames(self, stream):
        return self.lib.nemo_speech_diar_frame_count(stream)

    def capacity(self, model):
        return self.lib.nemo_speech_diar_num_speakers(model)

    def seconds_per_frame(self, model):
        return self.lib.nemo_speech_diar_seconds_per_frame(model)

    def segments(self, stream):
        count = C.c_size_t()
        self.check(self.lib.nemo_speech_diar_segments(stream, None, None, 0, C.byref(count)))
        if count.value > MAX_SEGMENTS:
            raise RuntimeError('Native diarization segment limit exceeded')
        storage = (_Segment * count.value)()
        self.check(self.lib.nemo_speech_diar_segments(stream, None, storage, count.value, C.byref(count)))
        return [(s.start_time, s.end_time, s.speaker) for s in storage]


class NativeDiarizer:
    """One model and at most one epoch stream; epoch-relative, finalized turns.

    Required config: native_diarization_model, native_diarization_library.
    Optional native_diarization_epoch_max_seconds can lower the 360s input cap.
    The embedding model, global identity registry, and ASR remain separate.
    """
    def __init__(self, config, *, before_model_create=None):
        self._model = self._stream = None
        self._closed = False
        self._failed = False
        self._finished = False
        self._epoch_id = None
        self._samples = 0
        self._max_seconds = float(config.get('native_diarization_epoch_max_seconds', MAX_EPOCH_INPUT_SECONDS))
        if not math.isfinite(self._max_seconds) or not 1 <= self._max_seconds <= MAX_EPOCH_INPUT_SECONDS:
            raise ValueError('Native epoch input cap must be between 1 and 360 seconds')
        model = Path(config.get('native_diarization_model', '')).expanduser().resolve()
        library = Path(config.get('native_diarization_library', '')).expanduser().resolve()
        if not model.is_file() or not library.is_file():
            raise FileNotFoundError('Configure local native_diarization_model and native_diarization_library; no automatic download is performed')
        model_sha = _sha256(model)
        if model_sha != MODEL_SHA256:
            raise ValueError('Native diarization model hash differs from the evaluated Nemotron-3 Q8 checkpoint')
        self._api = _CAPI(library)
        if before_model_create is not None:
            before_model_create()
        self._model = self._api.create(model)
        try:
            self._capacity = self._api.capacity(self._model)
            self._frame_seconds = self._api.seconds_per_frame(self._model)
            if self._capacity != 8 or abs(self._frame_seconds - .01) > 1e-7:
                raise ValueError('Unexpected native model speaker capacity or frame cadence')
            # The ABI reports a float-derived 0.009999999776; use exact 160-sample
            # frame arithmetic so checkpoint frontiers do not drift by a sample.
            self._frame_seconds = .01
        except BaseException:
            self._api.destroy(self._model)
            self._model = None
            raise
        library_sha = _sha256(library)
        # The C ABI library delegates inference to sibling runtime libraries.
        # Include their bytes in the checkpoint identity, not just the thin ABI.
        companions = {}
        for path in sorted(library.parent.iterdir()):
            if path.is_file() and not path.is_symlink() and any(
                    name in path.name.lower() for name in ('nemo_speech_asr', 'ggml', 'llama')):
                companions[path.name] = _sha256(path)
        self._provenance = build_expected_native_provenance(
            model_sha256=model_sha, library_sha256=library_sha,
            runtime_version=self._api.version, epoch_max_seconds=self._max_seconds,
            runtime_files_sha256=companions)

    def provenance(self):
        return json.loads(json.dumps(self._provenance))

    def _check_open(self):
        if self._closed:
            raise RuntimeError('Native diarizer is closed')

    def _check_epoch(self):
        self._check_open()
        if self._stream is None:
            raise RuntimeError('Start a native epoch with reset(epoch_id) first')
        if self._failed:
            raise RuntimeError('Native epoch failed; reset and replay from its checkpoint')

    def reset(self, epoch_id):
        self._check_open()
        if not isinstance(epoch_id, str) or not epoch_id or len(epoch_id) > 96:
            raise ValueError('Native epoch_id must contain 1–96 characters')
        if self._stream is not None:
            self._api.close(self._stream)
            self._stream = None
        self._stream = self._api.open(self._model)
        self._epoch_id, self._samples = epoch_id, 0
        self._failed = self._finished = False
        return self._status()

    def _status(self):
        seconds = self._samples / 16000
        processed = min(seconds, max(0., self._api.frames(self._stream) * self._frame_seconds))
        stable = seconds if self._finished else max(0., processed - MORPHOLOGY_GUARD_SECONDS)
        return {'epoch_id': self._epoch_id, 'input_seconds': seconds,
                'processed_seconds': processed, 'stable_seconds': stable,
                'finished': self._finished, 'local_capacity': self._capacity,
                'fingerprint': self._provenance['diarization_fingerprint']}

    def push(self, wav_path):
        self._check_epoch()
        if self._finished:
            raise RuntimeError('Cannot push audio after native epoch finish')
        import numpy as np
        import soundfile as sf
        with sf.SoundFile(wav_path) as source:
            if source.samplerate != 16000 or source.channels != 1 or source.frames <= 0:
                raise ValueError('Native push expects nonempty 16 kHz mono audio')
            if source.frames > round(MAX_PUSH_SECONDS * 16000):
                raise ValueError('Native push is limited to 60 seconds')
            if self._samples + source.frames > round(self._max_seconds * 16000):
                raise ValueError('Native epoch input cap reached; reset/replay a new bounded epoch')
            expected_samples = self._samples + source.frames
            try:
                for block in source.blocks(blocksize=32000, dtype='float32'):
                    if not np.isfinite(block).all():
                        raise ValueError('Non-finite native audio samples')
                    self._api.push(self._stream, np.ascontiguousarray(block))
                    self._samples += len(block)
                if self._samples != expected_samples:
                    raise RuntimeError('Incomplete native PCM read; reset and replay the epoch')
            except BaseException:
                self._failed = True
                raise
        return self._status()

    def finish(self):
        self._check_epoch()
        if not self._finished:
            try:
                self._api.finish(self._stream)
                self._finished = True
            except BaseException:
                self._failed = True
                raise
        return self._status()

    def turns(self, start, end, allow_provisional=False):
        self._check_epoch()
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (start, end)) or start < 0 or end < start:
            raise ValueError('Invalid native turn query interval')
        frontier = self._status()['processed_seconds' if allow_provisional else 'stable_seconds']
        if end > frontier + 1e-5:
            raise ValueError('Native turn query exceeds finalized frontier')
        result = []
        for lo, hi, slot in self._api.segments(self._stream):
            if not math.isfinite(lo) or not math.isfinite(hi) or hi < lo or not 1 <= slot <= self._capacity:
                self._failed = True
                raise RuntimeError('Invalid native diarization segment')
            lo, hi = round(lo * 16000) / 16000, round(hi * 16000) / 16000
            a, b = max(start, lo), min(end, hi, self._samples / 16000)
            if b > a:
                result.append({'start': a, 'end': b,
                               'speaker_local_id': f'{self._epoch_id}:speaker_{slot}'})
        return result

    def close(self):
        if self._closed:
            return
        if self._stream is not None:
            self._api.close(self._stream)
            self._stream = None
        if self._model is not None:
            self._api.destroy(self._model)
            self._model = None
        self._closed = True
