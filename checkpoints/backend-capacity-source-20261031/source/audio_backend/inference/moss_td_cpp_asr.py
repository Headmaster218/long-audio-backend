"""Optional pinned native MOSS runtime, using untouched raw text and strict parsing."""
from array import array
from dataclasses import dataclass
import math
from pathlib import Path
import struct
import sys
import time
from types import SimpleNamespace

from ..moss_td_cpp_assets import (MANIFEST_PATH, load_verified_runtime, native_library_name, sha256,
                                   verify_configuration)
from .moss_td_asr import _CANONICAL, parse_generation, unresolved as _unresolved

MAX_SUPPORT_SECONDS = 20.0
MAX_GENERATION_TOKENS = 512


def unresolved(reason_code, **kwargs):
    result = _unresolved(reason_code, **kwargs)
    result['raw_generated_ids'] = None
    result['metadata'].update(generated_tokens=None, generation_token_count_known=False)
    return result


@dataclass(frozen=True)
class WaveInfo:
    channels: int
    samplerate: int
    frames: int
    sample_format: int
    sample_width: int
    data_offset: int
    data_bytes: int


def wav_info(path):
    """Inspect little-endian RIFF PCM16/FLOAT32 without third-party imports."""
    with Path(path).open('rb') as stream:
        size = stream.seek(0, 2)
        stream.seek(0)
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b'RIFF' or header[8:] != b'WAVE':
            raise ValueError('Expected a little-endian RIFF WAVE support')
        end = struct.unpack_from('<I', header, 4)[0] + 8
        if end > size or end < 12:
            raise ValueError('Truncated/invalid RIFF support')
        fmt = data = None
        chunks = 0
        while stream.tell() < end:
            chunks += 1
            if chunks > 4096 or end - stream.tell() < 8:
                raise ValueError('Invalid WAV chunk envelope')
            kind, count = struct.unpack('<4sI', stream.read(8))
            offset = stream.tell()
            next_offset = offset + count + (count & 1)
            if next_offset > end:
                raise ValueError('Truncated WAV chunk')
            if kind == b'fmt ':
                if fmt is not None or count < 16:
                    raise ValueError('Invalid/duplicate WAV format chunk')
                fmt = struct.unpack('<HHIIHH', stream.read(16))
            elif kind == b'data':
                if data is not None:
                    raise ValueError('Multiple WAV data chunks are unsupported')
                data = (offset, count)
            stream.seek(next_offset)
    if fmt is None or data is None:
        raise ValueError('WAV format/data chunk is missing')
    kind, channels, rate, byte_rate, align, bits = fmt
    if (kind, bits) not in {(1, 16), (3, 32)} or channels != 1 or rate != 16000:
        raise ValueError('Expected mono 16 kHz PCM16 or FLOAT32 WAV')
    width = bits // 8
    if align != channels * width or byte_rate != rate * align or data[1] % align:
        raise ValueError('Invalid WAV frame alignment')
    return WaveInfo(channels, rate, data[1] // align, kind, width, *data)


def read_support(path, max_seconds=MAX_SUPPORT_SECONDS):
    info = wav_info(path)
    if not 0 < info.frames <= max_seconds * info.samplerate:
        raise ValueError('Expected a bounded nonempty 16 kHz mono support')
    with Path(path).open('rb') as stream:
        stream.seek(info.data_offset)
        encoded = stream.read(info.data_bytes)
    if len(encoded) != info.data_bytes:
        raise ValueError('WAV changed/truncated during read')
    samples = array('f' if info.sample_format == 3 else 'h')
    samples.frombytes(encoded)
    if sys.byteorder != 'little':
        samples.byteswap()
    if info.sample_format == 1:
        samples = array('f', (sample / 32768.0 for sample in samples))
    if not all(math.isfinite(sample) for sample in samples):
        raise ValueError('Nonfinite MOSS-TD input')
    return samples


def canonical_segments(raw_text, *, strip_text=False, skip_empty=False):
    """Raw canonical callback only. No timestamp repair or text fallback."""
    for match in _CANONICAL.finditer(raw_text):
        text = match[3].strip() if strip_text else match[3]
        if skip_empty and not text:
            continue
        yield SimpleNamespace(start=float(match[1]), end=float(match[4]), speaker=match[2], text=text)


def generation_budget(n_samples):
    if isinstance(n_samples, bool) or not isinstance(n_samples, int) or not 0 < n_samples <= 320000:
        raise ValueError('Generation budget requires 1 to 320000 input samples')
    audio_tokens = (n_samples - 1) // 1280 + 1
    marker_tokens = sum(len(str(second)) for second in range(5, (audio_tokens * 2) // 25 + 1, 5))
    prompt_tokens = 86 + audio_tokens + marker_tokens
    n_ctx = prompt_tokens + MAX_GENERATION_TOKENS
    return {'audio_token_count': audio_tokens, 'prompt_token_count': prompt_tokens,
            'generation_budget': min(n_ctx - prompt_tokens, max(256, 2 * audio_tokens + 128)),
            'generation_budget_policy': 'native_adaptive', 'session_n_ctx': n_ctx,
            'fixed_512_token_parity': False}


def strict_result(raw_text, duration, *, truncated=False):
    # The legacy parser's [] argument bypasses its fixed-512 length check.
    # Replace unavailable IDs/count immediately; native status supplies the
    # actual adaptive-budget exhaustion signal, never a retokenized estimate.
    result = parse_generation(raw_text, [], duration, canonical_segments)
    result['raw_generated_ids'] = None
    result['metadata'].update(generated_tokens=None, generation_token_count_known=False,
                              token_limit_reached=truncated, native_output_truncated=truncated,
                              finish_reason='native_output_truncated' if truncated else 'eos')
    if truncated:
        result['diagnostic_phrases'] = result['phrases'] or result['diagnostic_phrases']
        result['phrases'] = []
        result['status'] = 'unresolved'
        result['reason_code'] = 'native_generation_budget_exhausted'
        result['schema_errors'] = list(dict.fromkeys(['native_generation_budget_exhausted', *result['schema_errors']]))
    return result


class MossTDCppRecognizer:
    def __init__(self, config):
        self.max_support_seconds = float(config.get('moss_td_max_support_seconds', 20))
        if not math.isfinite(self.max_support_seconds) or not 0 < self.max_support_seconds <= 20:
            raise ValueError('Invalid MOSS-TD support limit')
        spec, row, paths, hashes = verify_configuration(config)
        self.runtime = load_verified_runtime(paths, hashes)
        runtime = self.runtime
        library_name = native_library_name()
        if (runtime.__version__ != spec['runtime_version'] or runtime.native_version() != spec['runtime_version'] or
                runtime.native_provider() != 'transcribe-cpp-native' or
                Path(runtime.library_path()).resolve() != paths[library_name]):
            raise RuntimeError('Loaded C++ runtime does not match the verified pinned provider')
        self.device = config.get('moss_td_cpp_device', 'cpu')
        if not runtime.backend_available(self.device):
            raise RuntimeError(f'Requested MOSS-TD {self.device} backend is unavailable; no fallback is permitted')
        began = time.monotonic()
        self.model = runtime.Model(Path(config['moss_td_gguf_path']).expanduser().resolve(), backend=self.device)
        actual = self.model.device
        if actual.kind.lower() != self.device or not self.model.backend.lower().startswith(self.device):
            self.model.close()
            raise RuntimeError('Requested MOSS-TD backend was not honored; no fallback is permitted')
        self.metadata = {
            'runtime_provider': 'transcribe_cpp', 'runtime_version': spec['runtime_version'],
            'runtime_source_revision': spec['runtime_source_revision'],
            'runtime_manifest_sha256': sha256(MANIFEST_PATH), 'runtime_library_sha256': hashes[library_name],
            'model_revision': spec['model_revision'], 'source_revision': spec['source_revision'],
            'model_sha256': row['sha256'], 'source_weight_sha256': spec['source_weight_sha256'],
            'precision': row['precision'], 'requested_device': self.device, 'device': self.device,
            'actual_backend': self.model.backend, 'actual_device_kind': actual.kind,
            'actual_device_name': actual.name, 'cpu_threads': 2, 'kv_type': 'f16',
            'prompt_sha256': spec['decoding']['prompt_sha256'], 'load_seconds': time.monotonic() - began,
            'profile_update_eligible': False, 'identity_status': 'unresolved',
            'vulkan_validated': False, 'windows_validated': False,
            'generated_tokens': None, 'generation_token_count_known': False}

    def close(self):
        self.model.close()

    def transcribe(self, support):
        began = time.monotonic()
        samples = read_support(support['path'], self.max_support_seconds)
        duration = len(samples) / 16000
        provenance = {**self.metadata, 'audio_sha256': sha256(support['path']),
                      'audio_hash_scope': 'encoded_input_file', 'duration_seconds': duration,
                      **generation_budget(len(samples))}
        if not any(sample != 0 for sample in samples):
            return unresolved('silent_input', metadata={**provenance, 'inference_run': False,
                                                       'native_output_truncated': None})
        n_ctx = provenance['session_n_ctx']
        with self.model.session(n_threads=2, n_ctx=n_ctx, kv_type='f16') as session:
            if session.limits.effective_n_ctx != n_ctx:
                raise RuntimeError('Native session context differs from the pinned decoding contract')
            truncated = False
            try:
                native = session.run(samples, diarize='on', timestamps='segment')
            except self.runtime.OutputTruncated as exc:
                truncated = True
                native = exc.partial_result
            if native is None:
                result = unresolved('native_generation_budget_exhausted', metadata={
                    'native_output_truncated': True, 'token_limit_reached': True,
                    'finish_reason': 'native_output_truncated'})
            else:
                result = strict_result(native.raw_text, duration, truncated=truncated)
        result['metadata'].update(provenance)
        result['metadata'].update(inference_run=True, seconds=time.monotonic() - began)
        return result
