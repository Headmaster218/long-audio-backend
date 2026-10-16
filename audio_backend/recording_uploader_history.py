"""Bounded append-v3 storage; immutable receipts plus one atomic checkpoint.

No list/map of historical records is retained. Two authenticity-only bytearrays
hold at most (588 + 128) * 32 = 22,912 bytes. A chain is fully checked against its
committed head before its bindings become usable; every later read checks the
bound raw bytes again. This is a finite authentication cache, not constant-size
history memory. JSON records and remote responses are consumed individually.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat

POLICY = 'immutable-files-contiguous-mono16k-v3'
MAX_FRAMES = 48 * 3600 * 16000
DEFAULT_FRAMES = 12 * 3600 * 16000
MAX_INPUTS = 128
DEFAULT_INPUTS = 64
MAX_INPUT_BYTES = 64 * 1024**3
METADATA_BYTES = 32 * 1024**2
STATE_BYTES = 48 * 1024**2
PAGE_ARCHIVE_BYTES = 32 * 1024
INPUT_ARCHIVE_BYTES = 12 * 1024
ZERO = '0' * 64


class PagedJournal:
    """The caller holds the shared uploader lock for every operation."""
    def __init__(self, directory, guard, api):
        self.u = api; self.guard = guard
        self.path = Path(directory).absolute()
        self.path.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink(): self.fail('State directory cannot be a symlink')
        self.root_identity = self._directory(self.path)
        self.history = self.path / 'uploader.history'
        self.history_identity = None
        self.lock = api.InstanceLock(self.path / 'uploader.lock')
        self.data = None; self.max_spool = 0; self.metadata_allocated = 0; self.live_metadata_allocated = 0
        self.digest_bindings = {'input': bytearray(), 'page': bytearray()}
        self.ready = False

    def fail(self, message): raise self.u.Incomplete(message)

    def _directory(self, path):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            self.fail('Unsafe append history directory')
        return info.st_dev, info.st_ino

    def _check_directories(self):
        if self._directory(self.path) != self.root_identity: self.fail('State directory identity changed')
        if self.history_identity is not None and self._directory(self.history) != self.history_identity:
            self.fail('History directory identity changed')

    def _pending_link_pair(self, path, info):
        """Read-only recognition of one exact no-replace promotion crash state.

        Both names are charged until recovery. No unlink occurs until all old
        chains and identities have been reconciled. All other aliases fail.
        """
        try:
            intent = self.data['archive_intent']
            if not intent or info.st_nlink != 2: return False
            kind = intent['kind']; ordinal = intent['ordinal']
            destination = self.archive_path(kind, ordinal); temporary = self.history / 'archive.next'
            if path not in (destination, temporary): return False
            first, second = destination.lstat(), temporary.lstat()
            if (not stat.S_ISREG(first.st_mode) or not stat.S_ISREG(second.st_mode)
                    or first.st_nlink != 2 or second.st_nlink != 2
                    or (first.st_dev, first.st_ino) != (second.st_dev, second.st_ino)
                    or (info.st_dev, info.st_ino) != (first.st_dev, first.st_ino)):
                return False
            cap = INPUT_ARCHIVE_BYTES if kind == 'input' else PAGE_ARCHIVE_BYTES
            if first.st_size > cap: return False
            payload = self.data['active_input' if kind == 'input' else 'active_page']
            expected = self.u._json(self._envelope(kind, ordinal, payload, intent['previous']))
            if hashlib.sha256(expected).hexdigest() != intent['digest']: return False
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if self.u._signature(before) != self.u._signature(first) or before.st_nlink != 2: return False
                raw = stream.read(cap + 1); after = os.fstat(stream.fileno())
            return (raw == expected and self.u._signature(before) == self.u._signature(after)
                    and self.u._signature(destination.lstat()) == self.u._signature(before)
                    and self.u._signature(temporary.lstat()) == self.u._signature(before)
                    and destination.lstat().st_nlink == temporary.lstat().st_nlink == 2)
        except (KeyError, TypeError, AttributeError, ValueError, OSError):
            return False

    def _regular(self, path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or (info.st_nlink != 1 and not self._pending_link_pair(path, info)):
            self.fail('Owned metadata/spool must be single-link regular files')
        return info

    @staticmethod
    def _allocated(info): return max(info.st_size, info.st_blocks * 512)

    def _read(self, path, cap):
        self.guard.check(); self._check_directories()
        try: before = self._regular(path)
        except FileNotFoundError: self.fail('Missing committed metadata record')
        if before.st_size > cap: self.fail('Bounded metadata size exceeded')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            held = os.fstat(stream.fileno())
            if self.u._signature(held) != self.u._signature(before) or held.st_nlink != before.st_nlink:
                self.fail('Metadata identity changed while opening')
            raw = stream.read(cap + 1)
            if (self.u._signature(os.fstat(stream.fileno())) != self.u._signature(held)
                    or self.u._signature(self._regular(path)) != self.u._signature(held)):
                self.fail('Metadata changed while reading')
        if len(raw) > cap: self.fail('Bounded metadata size exceeded')
        return raw

    def archive_path(self, kind, ordinal):
        if kind not in ('input', 'page') or type(ordinal) is not int or ordinal < 1:
            self.fail('Invalid archive identity')
        return self.history / f'{kind}-{ordinal:04d}.json'

    def owned(self, name):
        d = self.data
        match = re.fullmatch(re.escape(d['job']) + r'-p([1-9][0-9]{0,2})-s([0-9]|1[0-5])\.wav(?:\.part)?', name) if isinstance(name, str) else None
        if not match or int(match[1]) > self.max_pages: self.fail('Unowned append-v3 spool name')
        path = self.path / name
        if os.path.lexists(path): self._regular(path)
        return path

    @property
    def max_pages(self): return (self.data['caps']['output_frames'] + self.u.PAGE_FRAMES - 1) // self.u.PAGE_FRAMES

    def initialize(self, target, max_inputs):
        if os.path.lexists(self.history): self.fail('History directory exists without this journal; no adoption')
        d = {'policy': POLICY, 'job': self.u.uuid.uuid4().hex, 'identity': target,
             'caps': {'output_frames': self.guard.limits.output_frames,
                      'input_bytes': self.guard.limits.input_bytes, 'max_inputs': max_inputs},
             'inputs_done': 0, 'inputs_head': ZERO, 'pages_done': 0, 'pages_head': ZERO,
             'input_count': 0, 'page_count': 0, 'source_count': 0, 'input_bytes': 0,
             'completed_frames': 0, 'archived_frames': 0, 'archived_sources': 0,
             'emitted_frames': 0, 'published_frames': 0, 'active_input': None, 'active_page': None,
             'archive_intent': None, 'retirement': None, 'spool': [],
             'complete': False, 'finalize_requested': False}
        self.data = d
        # Empty checkpoints may predate mkdir after a crash; nonempty histories may not.
        self.ready = True; self._census(); self.save()
        self.history.mkdir(mode=0o700); self.u._fsync_dir(self.path)
        self.history_identity = self._directory(self.history); self._census()

    def _census(self):
        """One startup census. Later accounting touches only fixed live controls."""
        self._check_directories(); total = self._allocated(self.path.stat())
        for name in ('uploader.json', 'uploader.json.next', 'uploader.lock'):
            path = self.path / name
            if os.path.lexists(path): total += self._allocated(self._regular(path))
        if os.path.lexists(self.history):
            self.history_identity = self._directory(self.history)
            total += self._allocated(self.history.stat())
            intent = self.data.get('archive_intent')
            with os.scandir(self.history) as entries:
                for entry in entries:
                    match = re.fullmatch(r'(input|page)-([0-9]{4})\.json', entry.name)
                    if match:
                        kind, ordinal = match[1], int(match[2]); count = self.data['inputs_done' if kind == 'input' else 'pages_done']
                        if not 1 <= ordinal <= count and not (intent and kind == intent['kind'] and ordinal == intent['ordinal']):
                            self.fail('Unbound/orphan history archive; preserve it')
                    elif entry.name == 'archive.next':
                        if not intent: self.fail('Unbound archive temporary; preserve it')
                    else: self.fail('Unexpected history entry; preserve it')
                    total += self._allocated(self._regular(Path(entry.path)))
                    if total > METADATA_BYTES: self.fail('Append metadata allocated-byte budget reached')
        self.metadata_allocated = total; self.live_metadata_allocated = self._live_metadata()
        self.budget()

    def _live_metadata(self):
        total = self._allocated(self.path.stat())
        if self.history_identity is not None: total += self._allocated(self.history.stat())
        for path in (self.path / 'uploader.json', self.path / 'uploader.json.next', self.path / 'uploader.lock', self.history / 'archive.next'):
            if os.path.lexists(path): total += self._allocated(self._regular(path))
        return total

    def _refresh_live(self):
        current = self._live_metadata()
        self.metadata_allocated += current - self.live_metadata_allocated
        self.live_metadata_allocated = current

    def budget(self, reserve=0, metadata_reserve=0):
        self.guard.check(); self._check_directories(); self._refresh_live()
        allocated = logical = 0
        for name in self.data.get('spool', []):
            path = self.owned(name)
            if path.exists():
                info = self._regular(path); allocated += self._allocated(info); logical += info.st_size
        self.max_spool = max(self.max_spool, logical)
        # Reserves include coexistence of current/next checkpoint and allocation rounding.
        allowance = 2 * self.u.JSON_BYTES + PAGE_ARCHIVE_BYTES + 65536
        if allocated + reserve > self.guard.limits.spool_bytes:
            self.fail('One-page allocated-byte spool budget reached')
        if self.metadata_allocated + metadata_reserve + allowance > METADATA_BYTES:
            self.fail('Append metadata allocated-byte budget reached; unsupported allocation geometry')
        if self.metadata_allocated + allocated + reserve + metadata_reserve + allowance > STATE_BYTES:
            self.fail('Total owned append state budget reached')
        if shutil.disk_usage(self.path).free < self.guard.limits.disk_floor + reserve + metadata_reserve + allowance:
            self.fail('Disk safety reservation reached; retained incomplete prefix')

    def _atomic_checkpoint(self, raw):
        tmp = self.path / 'uploader.json.next'
        if os.path.lexists(tmp): self._regular(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            if os.fstat(stream.fileno()).st_nlink != 1: self.fail('Aliased checkpoint temporary')
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, self.path / 'uploader.json'); self.u._fsync_dir(self.path)
        self._refresh_live()

    def save(self):
        if not self.ready: self.fail('Journal must be fully verified before mutation')
        self.validate_checkpoint(); self.budget()
        raw = self.u._json(self.data)
        if len(raw) > self.u.JSON_BYTES: self.fail('Checkpoint JSON exceeds bound')
        self._atomic_checkpoint(raw)

    def load(self):
        self.ready = False; self.digest_bindings = {'input': bytearray(), 'page': bytearray()}
        path = self.path / 'uploader.json'
        if not os.path.lexists(path):
            if os.path.lexists(self.history): self.fail('History exists without its checkpoint')
            return None
        self.data = self.u._parse(self._read(path, self.u.JSON_BYTES))
        self.validate_checkpoint()
        if not os.path.lexists(self.history):
            if self.data['inputs_done'] or self.data['pages_done'] or self.data['archive_intent']:
                self.fail('Committed history directory is missing')
        else: self.history_identity = self._directory(self.history)
        self._census()
        self._bind('input'); self._bind('page')
        self.validate_pending_archive(); self.validate_history(); self.ready = True
        return self.data

    def _envelope(self, kind, ordinal, payload, previous):
        return {'policy': POLICY, 'job': self.data['job'], 'kind': kind, 'ordinal': ordinal,
                'previous': previous, 'payload': payload}

    def _check_envelope(self, envelope, kind, ordinal, previous):
        if (set(envelope) != {'policy', 'job', 'kind', 'ordinal', 'previous', 'payload'}
                or envelope['policy'] != POLICY or envelope['job'] != self.data['job']
                or envelope['kind'] != kind or type(envelope['ordinal']) is not int
                or envelope['ordinal'] != ordinal or envelope['previous'] != previous):
            self.fail('History chain identity/order mismatch')

    def _bind(self, kind):
        prefix = 'inputs' if kind == 'input' else 'pages'; previous = ZERO; bindings = bytearray()
        cap = INPUT_ARCHIVE_BYTES if kind == 'input' else PAGE_ARCHIVE_BYTES
        for ordinal in range(1, self.data[prefix + '_done'] + 1):
            raw = self._read(self.archive_path(kind, ordinal), cap)
            envelope = self.u._parse(raw)
            try: self._check_envelope(envelope, kind, ordinal, previous)
            except (KeyError, TypeError, ValueError, AttributeError, UnicodeError) as exc:
                raise self.u.Incomplete('Malformed bounded archive envelope') from exc
            digest = hashlib.sha256(raw).digest(); previous = digest.hex(); bindings.extend(digest)
        if previous != self.data[prefix + '_head']: self.fail('History terminal committed head mismatch')
        if len(bindings) > (MAX_INPUTS if kind == 'input' else 588) * 32:
            self.fail('Authentication binding index exceeds finite bound')
        self.digest_bindings[kind] = bindings

    def read_archive(self, kind, ordinal):
        bindings = self.digest_bindings[kind]; start = (ordinal - 1) * 32
        if ordinal < 1 or start + 32 > len(bindings): self.fail('History record is not terminal-bound')
        cap = INPUT_ARCHIVE_BYTES if kind == 'input' else PAGE_ARCHIVE_BYTES
        raw = self._read(self.archive_path(kind, ordinal), cap)
        if hashlib.sha256(raw).digest() != bindings[start:start + 32]: self.fail('Bound history bytes changed after validation')
        return self.u._parse(raw)['payload']

    def iter_inputs(self):
        for ordinal in range(1, self.data['inputs_done'] + 1): yield self.read_archive('input', ordinal)
        if self.data['active_input'] is not None: yield self.data['active_input']

    def iter_pages(self):
        for ordinal in range(1, self.data['pages_done'] + 1): yield self.read_archive('page', ordinal)
        if self.data['active_page'] is not None: yield self.data['active_page']

    def _integer(self, value, low, high, message):
        if type(value) is not int or not low <= value <= high: self.fail(message)

    def _input(self, entry):
        if len(self.u._json(entry)) > 8192: self.fail('Append input metadata bound')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', entry['id']): self.fail('Invalid input ID')
        if len(entry['identity']['path'].encode()) > 4096: self.fail('Input path bound')
        identity = entry['identity']; signature = identity['signature']
        if (not isinstance(signature, list) or len(signature) != 5 or any(type(x) is not int for x in signature)
                or not re.fullmatch('[a-f0-9]{64}', identity['sha256'])): self.fail('Invalid original identity')
        self._integer(signature[2], 1, self.data['caps']['input_bytes'], 'Original bytes bound')
        self._integer(entry['frames'], 0, self.data['caps']['output_frames'], 'Input frame bound')
        self._integer(entry['normalized_start'], 0, self.data['caps']['output_frames'], 'Input placement bound')
        if type(entry['clean_eof']) is not bool or entry['clean_eof'] and not entry['frames']:
            self.fail('Input clean EOF mismatch')
        if not isinstance(entry['decoder'], dict): self.fail('Invalid decoder recipe')

    def _page(self, row):
        u = self.u; d = self.data; number = row['number']; start = (number - 1) * u.PAGE_FRAMES
        self._integer(number, 1, self.max_pages, 'Append page bound')
        if type(row['released']) is not bool: self.fail('Page release flag mismatch')
        if len(u._json({k:v for k,v in row.items() if k != 'pieces'})) > 2048: self.fail('Page control metadata bound')
        if not isinstance(row['pieces'], list) or len(row['pieces']) > u.PAGE_SOURCES: self.fail('Page source bound')
        cursor = start
        for index, piece in enumerate(row['pieces']):
            if len(u._json(piece)) > 1024: self.fail('Piece metadata bound')
            if (any(type(piece[k]) is not int for k in ('frames', 'input_start', 'normalized_start', 'capture_start', 'bytes'))
                    or not 0 < piece['frames'] <= u.SOURCE_FRAMES or piece['input_start'] < 0
                    or piece['normalized_start'] != cursor or piece['capture_start'] != d['identity']['origin'] + cursor
                    or piece['bytes'] != piece['frames'] * 2 + 44
                    or piece['name'] != f"{d['job']}-p{number}-s{index}.wav"
                    or piece['key'] != f"{d['job']}.p{number}.s{index}"
                    or not re.fullmatch('[a-f0-9]{64}', piece['digest'])
                    or not re.fullmatch('[a-f0-9]{64}', piece['pcm_sha256'])
                    or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', piece['input_id'])):
                self.fail('Append piece lineage/identity mismatch')
            if piece.get('server_id') is not None and (not isinstance(piece['server_id'], str) or not 1 <= len(piece['server_id']) <= 128):
                self.fail('Source receipt ID bound')
            cursor += piece['frames']
        if cursor > min(start + u.PAGE_FRAMES, d['caps']['output_frames']): self.fail('Page frame bound')
        if row.get('server_id') is not None and (not isinstance(row['server_id'], str) or not 1 <= len(row['server_id']) <= 128):
            self.fail('Page receipt ID bound')
        if row.get('create') is not None:
            expected = {'page_number': number}
            if number == 1: expected['capture_origin_frame'] = d['identity']['origin']
            if row['create'] != expected: self.fail('Frozen page create mismatch')
        if row.get('seal'):
            seal = row['seal']
            if (set(seal) != {'idempotency_key', 'expected_revision', 'end_frame', 'eos'}
                    or seal['idempotency_key'] != d['job'] + '.seal.' + str(number)
                    or type(seal['expected_revision']) is not int or seal['expected_revision'] < 0
                    or type(seal['end_frame']) is not int or seal['end_frame'] != d['identity']['origin'] + cursor
                    or type(seal['eos']) is not bool or seal['eos'] and not d['finalize_requested']):
                self.fail('Frozen append seal mismatch')
        if row['released']:
            if (not row.get('seal') or not row.get('server_id') or not row.get('create')
                    or any(not p.get('server_id') for p in row['pieces']) or not row['pieces']
                    or not row['seal']['eos'] and cursor - start != u.PAGE_FRAMES):
                self.fail('Released page authority/fullness mismatch')
        return cursor

    def validate_checkpoint(self):
        d = self.data; u = self.u
        try:
            if d['policy'] != POLICY or not re.fullmatch('[a-f0-9]{32}', d['job']): self.fail('Append-v3 policy/job mismatch; no migration')
            caps = d['caps']
            if set(caps) != {'output_frames', 'input_bytes', 'max_inputs'}: self.fail('Frozen caps mismatch')
            self._integer(caps['output_frames'], 1, MAX_FRAMES, 'Duration capacity bound')
            self._integer(caps['input_bytes'], 1, MAX_INPUT_BYTES, 'Original byte capacity bound')
            self._integer(caps['max_inputs'], 1, MAX_INPUTS, 'Input registry capacity bound')
            self._integer(d['identity']['origin'], 0, (1 << 53) - 1 - caps['output_frames'], 'Exact capture origin bound')
            if d['identity']['format'] != u.FORMAT: self.fail('Frozen format mismatch')
            for key, high in (('inputs_done', caps['max_inputs']), ('input_count', caps['max_inputs']), ('pages_done', self.max_pages), ('page_count', self.max_pages), ('source_count', 16 * self.max_pages), ('archived_sources', 16 * self.max_pages), ('input_bytes', caps['input_bytes']), ('completed_frames', caps['output_frames']), ('archived_frames', caps['output_frames']), ('emitted_frames', caps['output_frames']), ('published_frames', caps['output_frames'])):
                self._integer(d[key], 0, high, key + ' bound')
            for key in ('inputs_head', 'pages_head'):
                if not re.fullmatch('[a-f0-9]{64}', d[key]): self.fail('Archive head mismatch')
            for key in ('complete', 'finalize_requested'):
                if type(d[key]) is not bool: self.fail('Finalization flags mismatch')
            entry, row = d['active_input'], d['active_page']
            if entry is not None: self._input(entry)
            if row is not None: end = self._page(row)
            else: end = d['archived_frames']
            if (d['input_count'] != d['inputs_done'] + (entry is not None)
                    or d['page_count'] != d['pages_done'] + (row is not None)
                    or row is not None and row['number'] != d['pages_done'] + 1
                    or entry is not None and entry['normalized_start'] != d['completed_frames']
                    or d['emitted_frames'] != d['completed_frames'] + (entry['frames'] if entry else 0)
                    or end != d['emitted_frames']
                    or d['source_count'] != d['archived_sources'] + (len(row['pieces']) if row else 0)
                    or d['published_frames'] != (end if row and row['released'] else d['archived_frames'])):
                self.fail('Checkpoint count/frame frontiers mismatch')
            if d['finalize_requested'] and (not d['input_count'] or entry and not entry['clean_eof']): self.fail('Finalization requires clean EOF')
            if d['complete'] and (not d['finalize_requested'] or d['published_frames'] != d['emitted_frames'] or row is not None or not d['emitted_frames']):
                self.fail('Invalid final checkpoint')
            if d.get('finish'):
                finish = d['finish']
                if (not d['finalize_requested'] or set(finish) != {'idempotency_key', 'expected_revision'}
                        or finish['idempotency_key'] != d['job'] + '.finish'
                        or type(finish['expected_revision']) is not int or finish['expected_revision'] < 0): self.fail('Frozen finish mismatch')
            if not isinstance(d['spool'], list) or len(d['spool']) > 32 or len(set(d['spool'])) != len(d['spool']): self.fail('Spool ledger bound')
            allowed = set(); required = set()
            if row:
                for piece in row['pieces']:
                    allowed.update((piece['name'], piece['name'] + '.part')); required.add(piece['name'])
                if len(row['pieces']) < 16:
                    name = f"{d['job']}-p{row['number']}-s{len(row['pieces'])}.wav"; allowed.update((name, name + '.part'))
            retirement = d['retirement']
            if retirement:
                if row is not None or set(retirement) != {'page', 'digest', 'names'} or retirement['page'] != d['pages_done'] or retirement['digest'] != d['pages_head']:
                    self.fail('Retirement is not bound to the committed page')
                if not isinstance(retirement['names'], list) or len(retirement['names']) > 32 or len(set(retirement['names'])) != len(retirement['names']): self.fail('Retirement name bound')
                allowed.update(retirement['names']); required.update(retirement['names'])
            if not required <= set(d['spool']) or not set(d['spool']) <= allowed: self.fail('Pending spool ownership ledger mismatch')
            for name in d['spool']: self.owned(name)
            intent = d['archive_intent']
            if intent:
                if set(intent) != {'kind', 'ordinal', 'digest', 'previous'}: self.fail('Archive transition shape mismatch')
                kind = intent['kind']; prefix = 'inputs' if kind == 'input' else 'pages'
                payload = entry if kind == 'input' else row
                if kind not in ('input', 'page') or payload is None or retirement: self.fail('Archive transition lacks active authority')
                if kind == 'input' and not entry['clean_eof'] or kind == 'page' and not row['released']: self.fail('Archive transition before completion')
                if intent['ordinal'] != d[prefix + '_done'] + 1 or intent['previous'] != d[prefix + '_head']: self.fail('Archive transition frontier mismatch')
                raw = u._json(self._envelope(kind, intent['ordinal'], payload, intent['previous']))
                if hashlib.sha256(raw).hexdigest() != intent['digest']: self.fail('Archive transition payload mismatch')
            if len(u._json({k: v for k, v in d.items() if k not in ('active_input', 'active_page')})) > 16384:
                self.fail('Append global metadata bound')
            if len(u._json(d)) > u.JSON_BYTES: self.fail('Checkpoint JSON bound')
        except (KeyError, TypeError, ValueError, AttributeError, UnicodeError) as exc:
            raise u.Incomplete('Malformed bounded append-v3 checkpoint') from exc

    def validate_history(self):
        try: self._validate_history()
        except (KeyError, TypeError, ValueError, AttributeError, UnicodeError) as exc:
            raise self.u.Incomplete('Malformed bound append history') from exc

    def _validate_history(self):
        d = self.data; count = size = cursor = 0
        for entry in self.iter_inputs():
            self._input(entry); count += 1; size += entry['identity']['signature'][2]
            if entry['normalized_start'] != cursor or count <= d['inputs_done'] and not entry['clean_eof']:
                self.fail('Input history order/EOF mismatch')
            # <=128 bounded scans, without an ID/path/inode registry in RAM.
            for prior_number in range(1, min(count, d['inputs_done'] + 1)):
                prior = self.read_archive('input', prior_number)
                if (prior['id'] == entry['id'] or prior['identity']['path'] == entry['identity']['path']
                        or prior['identity']['signature'][:2] == entry['identity']['signature'][:2]):
                    self.fail('Duplicate original ID/path/inode in history')
            cursor += entry['frames']
            if count == d['inputs_done'] and cursor != d['completed_frames']: self.fail('Completed input frontier mismatch')
        if (count, size, cursor) != (d['input_count'], d['input_bytes'], d['emitted_frames']): self.fail('Input history totals mismatch')
        inputs = iter(self.iter_inputs()); entry = next(inputs, None); local = pages = sources = emitted = archived = 0
        for row in self.iter_pages():
            pages += 1; end = self._page(row)
            if row['number'] != pages or pages <= d['pages_done'] and not row['released']: self.fail('Page history order mismatch')
            for piece in row['pieces']:
                while entry is not None and local == entry['frames']:
                    entry = next(inputs, None); local = 0
                if (entry is None or piece['input_id'] != entry['id'] or piece['input_start'] != local
                        or piece['normalized_start'] != entry['normalized_start'] + local
                        or local + piece['frames'] > entry['frames']): self.fail('Streamed original/page lineage mismatch')
                local += piece['frames']; emitted += piece['frames']; sources += 1
            if end != emitted: self.fail('Page history frame mismatch')
            if pages == d['pages_done']: archived = emitted
            if row.get('seal', {}).get('eos') and pages != d['page_count']: self.fail('EOS before final page')
        if (pages, sources, emitted, archived) != (d['page_count'], d['source_count'], d['emitted_frames'], d['archived_frames']):
            self.fail('Page history totals mismatch')
        retirement = d['retirement']
        if retirement:
            page = self.read_archive('page', retirement['page'])
            allowed = {n for p in page['pieces'] for n in (p['name'], p['name'] + '.part')}
            if not set(retirement['names']) <= allowed: self.fail('Retirement names differ from bound archive')

    def validate_pending_archive(self):
        intent = self.data['archive_intent']
        if not intent: return
        kind = intent['kind']; payload = self.data['active_input' if kind == 'input' else 'active_page']
        raw = self.u._json(self._envelope(kind, intent['ordinal'], payload, intent['previous']))
        cap = INPUT_ARCHIVE_BYTES if kind == 'input' else PAGE_ARCHIVE_BYTES
        destination = self.archive_path(kind, intent['ordinal']); temporary = self.history / 'archive.next'
        if os.path.lexists(destination) and self._read(destination, cap) != raw:
            self.fail('Crash archive differs from committed exact intent')
        if os.path.lexists(temporary):
            partial = self._read(temporary, cap)
            if partial != raw[:len(partial)]: self.fail('Archive temporary differs from committed exact intent')

    def _promote_archive(self, temporary, destination):
        """Atomic no-replace publication; the exact two-link window is recoverable."""
        os.link(temporary, destination, follow_symlinks=False)

    def _archive(self, kind):
        if not self.ready: self.fail('Journal must be fully verified before archival')
        d = self.data; prefix = 'inputs' if kind == 'input' else 'pages'
        payload = d['active_input' if kind == 'input' else 'active_page']
        if payload is None: return
        if d['archive_intent'] and d['archive_intent']['kind'] != kind: self.fail('Another archive transition is pending')
        ordinal = d[prefix + '_done'] + 1
        raw = self.u._json(self._envelope(kind, ordinal, payload, d[prefix + '_head']))
        cap = INPUT_ARCHIVE_BYTES if kind == 'input' else PAGE_ARCHIVE_BYTES
        if len(raw) > cap: self.fail('Archive envelope size bound')
        digest = hashlib.sha256(raw).hexdigest()
        if not d['archive_intent']:
            d['archive_intent'] = {'kind': kind, 'ordinal': ordinal, 'previous': d[prefix + '_head'], 'digest': digest}
            self.save()  # Exact authority precedes any archive file creation.
        self.validate_checkpoint(); self.validate_pending_archive(); self.budget(metadata_reserve=cap + 4096)
        if self.history_identity is None:
            self.history.mkdir(mode=0o700); self.u._fsync_dir(self.path); self.history_identity = self._directory(self.history)
        destination = self.archive_path(kind, ordinal); tmp = self.history / 'archive.next'
        old_archive = 0
        if os.path.lexists(destination):
            old_archive = self._allocated(self._regular(destination))
            if self._read(destination, cap) != raw: self.fail('Crash archive differs from committed exact intent')
        else:
            if os.path.lexists(tmp): self._regular(tmp)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'wb') as stream:
                if os.fstat(stream.fileno()).st_nlink != 1: self.fail('Aliased archive temporary')
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            # No overwrite/adoption; the exclusive writer promotes only its saved intent.
            self._promote_archive(tmp, destination); self.u._fsync_dir(self.history)
        if os.path.lexists(tmp):
            # A leftover is owned solely through the already-durable exact transition.
            self._regular(tmp); tmp.unlink(); self.u._fsync_dir(self.history)
        self.metadata_allocated += self._allocated(self._regular(destination)) - old_archive
        self._refresh_live()
        d[prefix + '_done'] = ordinal; d[prefix + '_head'] = digest; d['archive_intent'] = None
        if kind == 'input':
            d['completed_frames'] += payload['frames']; d['active_input'] = None
        else:
            d['archived_frames'] = d['published_frames']; d['archived_sources'] += len(payload['pieces'])
            d['active_page'] = None
            d['retirement'] = {'page': ordinal, 'digest': digest, 'names': list(d['spool'])}
        # A failed pointer save invalidates this in-memory journal; callers must reopen.
        try: self.save()
        except BaseException:
            self.ready = False; raise
        self.digest_bindings[kind].extend(bytes.fromhex(digest))

    def archive_input(self): self._archive('input')
    def archive_page(self): self._archive('page')

    def recover(self):
        if not self.ready: self.fail('Journal must be fully verified before recovery')
        if self.history_identity is None:
            self.history.mkdir(mode=0o700); self.u._fsync_dir(self.path); self.history_identity = self._directory(self.history)
        if self.data['archive_intent']: self._archive(self.data['archive_intent']['kind'])
        if self.data['active_input'] and self.data['active_input']['clean_eof']: self.archive_input()
        if self.data['active_page'] and self.data['active_page']['released']: self.archive_page()
        self.cleanup()

    def cleanup(self):
        if not self.ready: self.fail('Journal must be fully verified before cleanup')
        d = self.data; retirement = d['retirement']
        if retirement:
            # Reauthenticate the page before deleting anything, even after startup scan.
            page = self.read_archive('page', retirement['page'])
            allowed = {n for p in page['pieces'] for n in (p['name'], p['name'] + '.part')}
            if retirement['digest'] != d['pages_head'] or not set(retirement['names']) <= allowed:
                self.fail('Unbound retirement cleanup')
            for name in retirement['names']: self.owned(name).unlink(missing_ok=True)
            self.u._fsync_dir(self.path); d['spool'] = []; d['retirement'] = None; self.save()
        row = d['active_page']; keep = {p['name'] for p in row['pieces']} if row else set()
        for name in d['spool']:
            if name not in keep: self.owned(name).unlink(missing_ok=True)
        self.u._fsync_dir(self.path)
        d['spool'] = [name for name in d['spool'] if name in keep]; self.save()
