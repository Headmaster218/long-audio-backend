"""Read the reviewed public history without rewriting historical provenance IDs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

BASELINE = '0a27197979c8e96fa560432f7a15f49c9e1965fb'
MAP_PATH = 'docs/PUBLIC_HISTORY_MAP.json'
MAP_SHA256 = '12382b2866595a034281cfe2324241eb5e9de7b235382eb88c53ad7ab3a9bea7'


class HistoryError(ValueError):
    """Unknown, missing or mismatched public history is never a file absence."""


class PublicHistory:
    """Only declared revisions and explicit read operations are supported.

    The map is a reviewed source artifact, not a search heuristic. No branch-name,
    timestamp, subject, abbreviated-ID or original-object fallback is permitted.
    """

    def __init__(self, root):
        self.root = Path(root)
        raw = (self.root / MAP_PATH).read_bytes()
        if hashlib.sha256(raw).hexdigest() != MAP_SHA256:
            raise HistoryError('Public revision map differs from the reviewed digest')
        value = json.loads(raw)
        if set(value) != {'schema', 'baseline', 'revisions'} or value['schema'] != 'reviewed-public-history-v1':
            raise HistoryError('Invalid public revision map schema')
        self.pins = {BASELINE: value['baseline']}
        if len(value['revisions']) != 15 or value['baseline']['public'] != BASELINE:
            raise HistoryError('Incomplete public revision map')
        for row in value['revisions']:
            if set(row) != {'original', 'public', 'tree', 'parents'} or row['original'] in self.pins:
                raise HistoryError('Duplicate or invalid public revision entry')
            self.pins[row['original']] = {key: row[key] for key in ('public', 'tree', 'parents')}
        for original, pin in self.pins.items():
            if set(pin) != {'public', 'tree', 'parents'} or not isinstance(pin['parents'], list):
                raise HistoryError('Invalid public commit pin')
            if not all(isinstance(ref, str) and re.fullmatch('[0-9a-f]{40}', ref)
                       for ref in [original, pin['public'], pin['tree'], *pin['parents']]):
                raise HistoryError('Only full literal Git IDs are allowed')
        self.originals = {pin['public']: original for original, pin in self.pins.items()}
        if len(self.originals) != len(self.pins):
            raise HistoryError('Ambiguous public revision mapping')

    def _git(self, *args):
        env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1')
        try:
            return subprocess.check_output(['git', '--literal-pathspecs', *args],
                                           cwd=self.root, env=env, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as exc:
            raise HistoryError('Required public Git object or operation unavailable') from exc

    def resolve(self, original):
        if not isinstance(original, str) or original not in self.pins:
            raise HistoryError('Revision is not explicitly reviewed')
        pin = self.pins[original]
        headers = self._git('cat-file', 'commit', pin['public']).split(b'\n\n', 1)[0].splitlines()
        trees = [line[5:].decode() for line in headers if line.startswith(b'tree ')]
        parents = [line[7:].decode() for line in headers if line.startswith(b'parent ')]
        if trees != [pin['tree']] or parents != pin['parents']:
            raise HistoryError('Public commit tree or ordered parents differ')
        for oid, kind in [(pin['tree'], b'tree'), *((ref, b'commit') for ref in parents)]:
            if self._git('cat-file', '-t', oid).strip() != kind:
                raise HistoryError('Missing or invalid public tree/parent object')
        return pin['public']

    def file_oid(self, original, path):
        if (not isinstance(path, str) or not path or str(PurePosixPath(path)) != path
                or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts
                or any(char in path for char in '\x00\n\r:') or path.startswith('-')):
            raise HistoryError('Only literal repository-relative paths are allowed')
        public = self.resolve(original)  # Must succeed before considering absence.
        entry = self._git('ls-tree', '-z', public, '--', path)
        if not entry:
            return None
        metadata, name = entry.rstrip(b'\x00').split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        if name.decode() != path or mode not in {'100644', '100755'} or kind != 'blob':
            raise HistoryError('Historical path is not one regular file')
        if self._git('cat-file', '-t', oid).strip() != b'blob':
            raise HistoryError('Required historical blob is unavailable')
        return oid

    def read_file(self, original, path):
        oid = self.file_oid(original, path)
        if oid is None:
            raise FileNotFoundError(path)
        return self._git('cat-file', 'blob', oid)

    def merge_base(self, *originals):
        if len(originals) != 2:
            raise HistoryError('Only an explicitly reviewed two-parent merge is supported')
        oid = self._git('merge-base', '--all', *(self.resolve(ref) for ref in originals)).decode().strip()
        if oid not in self.originals:
            raise HistoryError('Merge base is not one reviewed revision')
        result = self.originals[oid]
        self.resolve(result)
        return result

    def parents(self, original):
        self.resolve(original)
        try:
            result = [self.originals[oid] for oid in self.pins[original]['parents']]
        except KeyError as exc:
            raise HistoryError('Parent is not one reviewed revision') from exc
        for ref in result:
            self.resolve(ref)
        return result
