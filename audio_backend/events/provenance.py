"""Bounded identities for local event artifacts; hashing never loads a model."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .detector import LABELS, configured_thresholds


def effective_event_config(config):
    """Resolve the detector's actual policy, including its fixed silence threshold."""
    thresholds = configured_thresholds(config)
    return {
        'event_threshold': float(config.get('event_threshold', .25)),
        'event_thresholds': {label: 1. if label == 'silence' else float(thresholds[label])
                             for label in LABELS},
        'event_release_ratio': float(config.get('event_release_ratio', 1.)),
        'event_silence_peak': float(config.get('event_silence_peak', 1e-5)),
    }


def _stat_identity(stat):
    # ctime also catches same-length rewrites with restored mtime; inode/device
    # catches atomic replacements. These values are never public provenance.
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class EventArtifactHashes:
    """At most two entries per analyzer, replaced rather than accumulated.

    Unchanged calls stat the files, without rereading model bytes. Missing or
    unstable files fail closed. This is a local change detector, not protection
    against an adversary capable of restoring all filesystem metadata.
    """
    FIELDS = {'events': 'event_model_sha256', 'labels': 'event_labelmap_sha256'}

    def __init__(self):
        self._cache = {}

    def snapshot(self, paths):
        result = {}
        for key, field in self.FIELDS.items():
            path = Path(paths[key]).resolve()
            signature = (path, _stat_identity(path.stat()))
            cached = self._cache.get(key)
            if cached is None or cached[0] != signature:
                digest = hashlib.sha256()
                with path.open('rb') as source:
                    if _stat_identity(os.fstat(source.fileno())) != signature[1]:
                        raise RuntimeError('Event artifact changed before hashing')
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(block)
                    if _stat_identity(os.fstat(source.fileno())) != signature[1]:
                        raise RuntimeError('Event artifact changed during hashing')
                if _stat_identity(path.stat()) != signature[1]:
                    raise RuntimeError('Event artifact changed during hashing')
                cached = (signature, digest.hexdigest())
                self._cache[key] = cached
            result[field] = cached[1]
        return result

    def verify_unchanged(self, paths):
        # detect_events constructs a fresh tagger/labelmap on each call. Reject
        # concurrent changes rather than attribute old output to new file bytes.
        for key in self.FIELDS:
            path = Path(paths[key]).resolve()
            if (path, _stat_identity(path.stat())) != self._cache[key][0]:
                raise RuntimeError('Event artifacts changed during analysis; retry with stable files')
