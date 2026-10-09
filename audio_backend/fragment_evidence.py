"""Private, bounded, content-addressed evidence. No public or identity store.

One experiment owns an exclusive lease for the entire file-write/SQL-commit
sequence. File fsync precedes rename and directory fsync, which precede SQL.
A crash may leave an orphan; referenced corruption always fails closed.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from .fragment_runtime_contracts import CorruptLedger, bounded_json, canonical

SCHEMA = 'fragment-real-evidence-v1'
MAX_EVIDENCE_BYTES = 2 * 1024**2
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def validate_reference(ref):
    if (not isinstance(ref, dict) or set(ref) != {'schema', 'sha256', 'bytes'}
        or ref['schema'] != SCHEMA or not isinstance(ref['sha256'], str)
        or not _SHA.fullmatch(ref['sha256']) or type(ref['bytes']) is not int
        or not 0 < ref['bytes'] <= MAX_EVIDENCE_BYTES):
        raise ValueError('Invalid bounded private evidence reference')
    return ref


class EvidenceStore:
    def __init__(self, root, *, reserve=lambda size: None, fault=lambda point: None):
        self._owner_thread = threading.get_ident()
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.resolve() != self.root:
            raise ValueError('Evidence root cannot contain symlinks')
        self.reserve, self.fault = reserve, fault
        self._lease = os.open(self.root / '.lease', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self._lease)
            self._lease = None
            raise RuntimeError('Experiment evidence is already leased; no concurrent cleanup/writer')

    def _open(self):
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError('Evidence lease has one owning thread; concurrent writers/cleanup are unsupported')
        if self._lease is None:
            raise RuntimeError('Evidence lease is closed')

    def close(self):
        if self._lease is not None:
            self._open()
            os.close(self._lease)
            self._lease = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def put(self, value):
        self._open()
        encoded = bounded_json(value, MAX_EVIDENCE_BYTES).encode()
        ref = {'schema': SCHEMA, 'sha256': hashlib.sha256(encoded).hexdigest(), 'bytes': len(encoded)}
        destination = self.root / (ref['sha256'] + '.json')
        if destination.exists():
            self.read(ref)
            return ref
        self.reserve(len(encoded))
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self.fault('evidence.after_fsync')
            os.replace(temporary, destination)
            directory = os.open(self.root, os.O_DIRECTORY | os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.fault('evidence.after_rename')
            self.read(ref)
            return ref
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def read(self, ref):
        self._open()
        validate_reference(ref)
        path = self.root / (ref['sha256'] + '.json')
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                encoded = stream.read(MAX_EVIDENCE_BYTES + 1)
            if len(encoded) != ref['bytes'] or hashlib.sha256(encoded).hexdigest() != ref['sha256']:
                raise CorruptLedger('Referenced evidence bytes/hash mismatch')
            value = json.loads(encoded)
            if canonical(value).encode() != encoded:
                raise CorruptLedger('Evidence is not canonical JSON')
            return value
        except (OSError, ValueError) as exc:
            raise CorruptLedger('Missing, unsafe or malformed referenced evidence') from exc

    def verify_core(self, ref, payload, run_id):
        value = self.read(ref)
        binding = {name: payload[name] for name in (
            'window_id', 'window', 'revision', 'plan_hash', 'contract', 'policy_hash',
            'mapping', 'map_hash', 'pcm_hash')}
        binding['run_id'] = run_id
        if canonical(value.get('binding')) != canonical(binding):
            raise CorruptLedger('Evidence is not bound to the committed logical read/core')
        return value

    def require_external_roots(self):
        """Persist the need for a complete external-ledger root inventory."""
        self._open()
        marker = self.root/'.external-roots-required'
        if marker.exists():
            if marker.is_symlink() or marker.read_bytes() != b'private-stage-roots-v1\n':
                raise CorruptLedger('Invalid external evidence-root marker')
            return
        self.reserve(23)
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, b'private-stage-roots-v1\n'); os.fsync(fd)
        finally:
            os.close(fd)
        directory = os.open(self.root, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def cleanup(self, references, *, external_reference_provider=None):
        """Offline under the held experiment lease and caller's SQL transaction."""
        self._open()
        refs = list(references)
        if (self.root/'.external-roots-required').exists():
            self.require_external_roots()
            if external_reference_provider is None:
                raise CorruptLedger('Complete external evidence-root inventory required before cleanup')
            refs.extend(external_reference_provider())
        elif external_reference_provider is not None:
            refs.extend(external_reference_provider())
        for ref in refs:
            self.read(ref)  # Never clean around referenced corruption.
        keep = {ref['sha256'] + '.json' for ref in refs}
        removed = []
        for path in self.root.iterdir():
            if path.name in keep or path.name in ('.lease', '.external-roots-required'):
                continue
            if path.is_symlink() or not path.is_file():
                raise CorruptLedger('Unknown unsafe object in evidence root')
            if path.name.startswith('.pending-') or _SHA.fullmatch(path.stem) and path.suffix == '.json':
                path.unlink()
                removed.append(path.name)
            else:
                raise CorruptLedger('Unknown object in evidence root; cleanup refused')
        directory = os.open(self.root, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return removed
