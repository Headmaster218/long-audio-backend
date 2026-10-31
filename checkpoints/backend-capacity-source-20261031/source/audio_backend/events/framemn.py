"""Default-off, local-only FrameMN10 temporal candidate side channel.

Importing this module never imports torch, torchvision or torchaudio. Inference
is sequential CPU float32, batch one, using pinned local bytes only. Outputs are
unverified score-grid support, not calibrated probabilities or precise onsets.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import threading

import numpy as np

from .temporal_decode import (
    GRID_SAMPLES, MAX_ANALYZER_SAMPLES, PROFILE, SAMPLE_RATE, TILE_FRAMES,
    TILE_SAMPLES, decode_knock_tiles,
)
from ._vendor.framemn.labelmap import LABELS, LABELMAP_SHA256

CHECKPOINT_BYTES = 15537114
CHECKPOINT_SHA256 = '4a0fe320d5369987b772394c51881fb20a602967e6842f72f3e5c8181065ece7'
UPSTREAM_COMMIT = '1aa47e482f7e89904cba2338999345025d8b4e36'
TARGET_INDEX = 228
CLASS_COUNT = 447
MAX_TOP_LABELS = 10
INTRAOP_THREADS = 1
# Suppress age-/gender-specific human labels, including singing and laughter.
PRIVATE_LABELS = frozenset({
    'Male speech, man speaking', 'Female speech, woman speaking',
    'Child speech, kid speaking', 'Baby cry, infant cry', 'Children shouting',
    'Children playing', 'Baby laughter', 'Child singing', 'Female singing',
    'Male singing',
})


class FrameMNError(RuntimeError):
    """A bounded, path-free adapter failure code safe for execution receipts."""


def _checkpoint_signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_verified_checkpoint(checkpoint_path):
    """Hash the same regular-file descriptor later passed to weights_only load."""
    fd = None
    handle = None
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(os.fspath(checkpoint_path), flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FrameMNError('framemn_checkpoint_not_regular')
        if info.st_size != CHECKPOINT_BYTES:
            raise FrameMNError('framemn_checkpoint_size')
        handle = os.fdopen(fd, 'rb')
        fd = None
        digest = hashlib.sha256()
        remaining = CHECKPOINT_BYTES
        while remaining:
            block = handle.read(min(1024 * 1024, remaining))
            if not block:
                raise FrameMNError('framemn_checkpoint_changed')
            digest.update(block)
            remaining -= len(block)
        if handle.read(1):
            raise FrameMNError('framemn_checkpoint_changed')
        if _checkpoint_signature(os.fstat(handle.fileno())) != _checkpoint_signature(info):
            raise FrameMNError('framemn_checkpoint_changed')
        if digest.hexdigest() != CHECKPOINT_SHA256:
            raise FrameMNError('framemn_checkpoint_digest')
        handle.seek(0)
        return handle, _checkpoint_signature(info)
    except Exception as exc:
        if handle is not None:
            handle.close()
        if fd is not None:
            os.close(fd)
        if isinstance(exc, FrameMNError):
            raise
        raise FrameMNError('framemn_checkpoint_unavailable') from None


def _build_model(torch):
    """The only import boundary for the namespaced optional inference closure."""
    from ._vendor.framemn.wrappers import FrameMNWrapper, PredictionsWrapper
    base = FrameMNWrapper(width_mult=1.0)
    dim = base.state_dict()['frame_mn.features.16.1.bias'].shape[0]
    return PredictionsWrapper(base, embed_dim=dim)


def _validate_state(torch, state, expected):
    if not isinstance(state, dict) or set(state) != set(expected):
        raise FrameMNError('framemn_checkpoint_keys')
    for key, tensor in state.items():
        reference = expected[key]
        if (not isinstance(tensor, torch.Tensor) or tensor.device.type != 'cpu'
                or tensor.shape != reference.shape or tensor.dtype != reference.dtype
                or not bool(torch.isfinite(tensor).all())):
            raise FrameMNError('framemn_checkpoint_tensor')


def _load_local_model(checkpoint_path):
    handle, signature = _open_verified_checkpoint(checkpoint_path)
    with handle:
        try:
            import torch
            # This conservative process-wide intra-op reduction occurs only on
            # explicit optional activation. Never change inter-op thread state.
            torch.set_num_threads(INTRAOP_THREADS)
            model = _build_model(torch).float().cpu()
        except Exception:
            raise FrameMNError('framemn_dependency_or_architecture') from None
        try:
            state = torch.load(handle, map_location='cpu', weights_only=True)
            if _checkpoint_signature(os.fstat(handle.fileno())) != signature:
                raise FrameMNError('framemn_checkpoint_changed')
            _validate_state(torch, state, model.state_dict())
            model.load_state_dict(state, strict=True)
            del state
            model.float().cpu().eval()
        except FrameMNError:
            raise
        except Exception:
            raise FrameMNError('framemn_checkpoint_load') from None
    return torch, model


def _summarize_scores(scores, frame_count):
    """Immediately reduce a transient score tile; no full scores escape."""
    if (not isinstance(scores, np.ndarray) or scores.shape != (CLASS_COUNT, TILE_FRAMES)
            or scores.dtype != np.dtype(np.float32) or not np.isfinite(scores).all()
            or np.any(scores < 0) or np.any(scores > 1)
            or type(frame_count) is not int or not 1 <= frame_count <= TILE_FRAMES):
        raise FrameMNError('framemn_score_contract')
    valid = scores[:, :frame_count]
    target = valid[TARGET_INDEX].copy()
    peaks = valid.max(axis=1)
    # Deterministic tie-breaking uses the pinned class order. Filter before top-k.
    indices = sorted((i for i, label in enumerate(LABELS) if label not in PRIVATE_LABELS),
                     key=lambda i: (-float(peaks[i]), i))[:MAX_TOP_LABELS]
    top = [{'label': LABELS[i], 'score': float(peaks[i])} for i in indices]
    return target, top


class FrameMNDetector:
    """An explicitly constructed optional detector; no eager checkpoint loading.

    Execution counters reset per detect. model_call_count counts completed model
    forwards, including forwards whose outputs subsequently fail validation.
    model_inference_executed records entry into the inference block. No partial
    candidates are returned if any tile, checkpoint or decoder check fails.
    """

    def __init__(self, checkpoint_path, profile=PROFILE):
        if profile != PROFILE:
            raise ValueError('framemn_profile_unsupported')
        try:
            self._checkpoint_path = Path(checkpoint_path)
        except Exception:
            raise ValueError('framemn_checkpoint_path') from None
        self.profile = profile
        self._torch = None
        self._model = None
        self._lock = threading.Lock()
        self.model_call_count = 0
        self.expected_model_calls = 0
        self.model_inference_executed = False
        self.provenance = {
            'model': 'FrameMN10',
            'checkpoint_sha256': CHECKPOINT_SHA256,
            'checkpoint_integrity': 'local_first_seen_sha256_not_publisher_digest',
            'labelmap_sha256': LABELMAP_SHA256,
            'labelmap_identity': 'audioset_strong_447_at_' + UPSTREAM_COMMIT,
            'upstream_commit': UPSTREAM_COMMIT,
            'context_semantics': ('independent_160000_sample_tiles;median9_across_tile_seams;'
                                  'reflect_only_analyzer_endpoints'),
            'profile': PROFILE,
        }

    def _ensure_loaded(self):
        if self._model is None:
            self._torch, self._model = _load_local_model(self._checkpoint_path)

    def _infer_tile(self, tile, frame_count):
        torch, model = self._torch, self._model
        try:
            torch.set_num_threads(INTRAOP_THREADS)
            with torch.inference_mode():
                self.model_inference_executed = True
                mel = model.mel_forward(torch.from_numpy(tile[None, :]))
                logits, weak = model(mel)
                self.model_call_count += 1
                del weak, mel
                if (tuple(logits.shape) != (1, CLASS_COUNT, TILE_FRAMES)
                        or logits.device.type != 'cpu' or logits.dtype != torch.float32
                        or not bool(torch.isfinite(logits).all())):
                    raise FrameMNError('framemn_logits_contract')
                scores = torch.sigmoid(logits)[0].detach().cpu().numpy()
                del logits
                target, top = _summarize_scores(scores, frame_count)
                del scores
                return target, top
        except FrameMNError:
            raise
        except Exception:
            raise FrameMNError('framemn_inference_failed') from None

    def detect(self, samples):
        # Nonblocking admission prevents two callers from interleaving model work
        # or corrupting the per-call receipt counters.
        if not self._lock.acquire(blocking=False):
            raise FrameMNError('framemn_busy')
        try:
            self.model_call_count = 0
            self.expected_model_calls = 0
            self.model_inference_executed = False
            if (not isinstance(samples, np.ndarray) or samples.ndim != 1
                    or samples.dtype != np.dtype(np.float32)
                    or len(samples) > MAX_ANALYZER_SAMPLES or not np.isfinite(samples).all()):
                raise ValueError('framemn_input_contract')
            if not len(samples):
                return []
            self.expected_model_calls = (len(samples) + TILE_SAMPLES - 1) // TILE_SAMPLES
            self._ensure_loaded()
            target_stream = np.empty((len(samples) + GRID_SAMPLES - 1) // GRID_SAMPLES,
                                     dtype=np.float32)
            summaries = []
            for start in range(0, len(samples), TILE_SAMPLES):
                count = min(TILE_SAMPLES, len(samples) - start)
                tile = np.zeros(TILE_SAMPLES, dtype=np.float32)
                tile[:count] = samples[start:start + count]
                frames = (count + GRID_SAMPLES - 1) // GRID_SAMPLES
                target, top = self._infer_tile(tile, frames)
                first = start // GRID_SAMPLES
                target_stream[first:first + frames] = target
                summaries.append(top)
                del tile, target, top
            temporals = decode_knock_tiles(target_stream, len(samples))
            del target_stream
            return [{
                'payload_kind': 'framemn_temporal_v1',
                'start': temporal['tile_start_sample'] / SAMPLE_RATE,
                'end': (temporal['tile_start_sample'] + temporal['tile_sample_count']) / SAMPLE_RATE,
                'time_precision': 'model_score_grid', 'onset_precision_seconds': None,
                'method': 'framemn10_knock_temporal_experimental',
                'model_inference_executed': True,
                'top_labels': top, 'category_scores': {}, 'temporal': temporal,
                'candidate_provenance': dict(self.provenance),
            } for temporal, top in zip(temporals, summaries)]
        finally:
            self._lock.release()
