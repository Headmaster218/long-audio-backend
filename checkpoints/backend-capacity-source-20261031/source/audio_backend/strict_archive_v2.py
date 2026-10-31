"""Offline, bounded archive slice. Original producer rows/files are never retired.

Only complete same-chunk proof closures are supported. This is not Worker cold
routing, concurrent service retention, or a solution to the hot-row ceiling.
"""
from contextlib import contextmanager
from collections import Counter
from pathlib import Path
import hashlib
import json
import math
import os
import re
import stat
import struct
import threading
import time
import uuid

POLICY = 'strict-primary-archive-v2'
FORMAT = 'strict-archive-complete-v2-slice1'
MAGIC = b'SAV2\x00\x01'
MAX_SECONDS = 5.0
MAX_READ = 128 * 1024**2
MAX_METADATA = 4 * 1024**2
MAX_PAYLOAD = 32 * 1024**2
BUFFER = 1024**2
MAX_STEPS = 150000
MAX_FRAMES = 168000
MAX_OUTCOMES = 4
MAX_SEGMENTS = 512
MAX_TURNS = 256
MAX_SUPPORTS = 8
MAX_SOURCES = 16
MAX_RANGES = 32
MAX_OBJECTS = 40
MAX_ARCHIVES = 16
MAX_CACHES = 40
MAX_ARCHIVE_FILE = len(MAGIC)+4+MAX_METADATA+MAX_PAYLOAD
MAX_DIRECTORY_ENTRIES = 2*(MAX_ARCHIVES+MAX_CACHES)
MAX_DIRECTORY_BYTES = MAX_ARCHIVES*MAX_ARCHIVE_FILE+MAX_CACHES*MAX_PAYLOAD
_ID = re.compile(r'^[a-f0-9]{64}$')
_PENDING_ARCHIVE_OWNER = None


def refuse_pending_archive():
    if _PENDING_ARCHIVE_OWNER is not None:
        raise _PENDING_ARCHIVE_OWNER.original


class ArchiveUnavailable(ValueError):
    pass


class ArchiveLimit(ArchiveUnavailable):
    pass


class ArchiveBudget:
    """One fixed, nonrenewing caller envelope; unrelated to the native Budget."""
    def __init__(self):
        self.deadline = time.monotonic() + MAX_SECONDS
        self.read_bytes = self.metadata_bytes = self.payload_bytes = self.sql_steps = 0
        self.sql_outcome = 'not_started'
        self.primary_sql = False
        self.first_error = None

    def check(self):
        if self.first_error is not None:
            raise self.first_error
        if time.monotonic() >= self.deadline:
            self.first_error = ArchiveLimit('archive_deadline')
            raise self.first_error

    def _charge(self, field, n, maximum):
        self.check()
        if type(n) is not int or n < 0:
            raise ArchiveLimit('archive_invalid_charge')
        value = getattr(self, field) + n
        if value > maximum:
            self.first_error = ArchiveLimit('archive_' + field)
            raise self.first_error
        setattr(self, field, value)

    def read(self, n): self._charge('read_bytes', n, MAX_READ)
    def metadata(self, n): self._charge('metadata_bytes', n, MAX_METADATA)
    def payload(self, n): self._charge('payload_bytes', n, MAX_PAYLOAD)

    def progress(self):
        self.sql_steps += 100
        try:
            if self.sql_steps >= MAX_STEPS:
                raise ArchiveLimit('archive_sql_steps')
            self.check()
        except BaseException as error:
            if self.first_error is None: self.first_error = error
            return 1
        return 0

    @contextmanager
    def notice_phase(self):
        self.check()
        yield
        self.check()


def schema_statements():
    return [
        '''CREATE TABLE IF NOT EXISTS strict_archive_checkpoint (
          archive_id TEXT PRIMARY KEY CHECK(length(archive_id)=64),
          cid TEXT NOT NULL UNIQUE, source TEXT NOT NULL CHECK(length(source)=64),
          tip INTEGER NOT NULL CHECK(tip>0 AND tip<=168000),
          store_key TEXT NOT NULL CHECK(length(store_key)=64),
          manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64),
          file_sha256 TEXT NOT NULL CHECK(length(file_sha256)=64),
          bytes INTEGER NOT NULL CHECK(bytes>0 AND bytes<=37748746))''',
        '''CREATE TABLE IF NOT EXISTS strict_archive_cache (
          cache_id TEXT PRIMARY KEY CHECK(length(cache_id)=64),
          archive_id TEXT NOT NULL REFERENCES strict_archive_checkpoint(archive_id),
          object_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
          state TEXT NOT NULL CHECK(state IN ('present','retiring','absent')),
          signature TEXT, UNIQUE(archive_id,object_id,generation))''',
        '''CREATE TRIGGER IF NOT EXISTS strict_archive_checkpoint_update BEFORE UPDATE ON strict_archive_checkpoint
          BEGIN SELECT RAISE(ABORT,'strict_archive_checkpoint_immutable'); END''',
        '''CREATE TRIGGER IF NOT EXISTS strict_archive_checkpoint_delete BEFORE DELETE ON strict_archive_checkpoint
          BEGIN SELECT RAISE(ABORT,'strict_archive_checkpoint_retained'); END''',
        '''CREATE TRIGGER IF NOT EXISTS strict_archive_cache_identity BEFORE UPDATE OF cache_id,archive_id,object_id,generation ON strict_archive_cache
          BEGIN SELECT RAISE(ABORT,'strict_archive_cache_identity'); END''',
        '''CREATE TRIGGER IF NOT EXISTS strict_archive_cache_delete BEFORE DELETE ON strict_archive_cache
          BEGIN SELECT RAISE(ABORT,'strict_archive_cache_retained'); END''',
    ]


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _jsonable(value):
    if isinstance(value, bytes): return {'$bytes': value.hex()}
    if isinstance(value, dict): return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_jsonable(v) for v in value]
    return value


def _sha(value): return hashlib.sha256(value).hexdigest()
def _signature(value): return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]
def _require(ok, reason):
    if not ok: raise ArchiveUnavailable(reason)


def _store_key(store):
    p = Path(store.path).absolute()
    s = p.stat()
    return _sha(_canonical([str(p), s.st_dev, s.st_ino]))


class _Owner:
    def __init__(self, store, budget):
        self.store, self.budget = store, budget
        self.thread = threading.get_ident()
        self._db_allowed = False
        self.fds = []
        self.failed_closes = set()
        self.scan = None
        self.scan_close_failed = False
        self.original = None
        self.unknown = False
        self.lease = False

    def database_allowed(self):
        return (self.store._strict_archive_owner is self and self._db_allowed
                and self.thread == threading.get_ident() and not self.unknown and not self.lease)

    def remember(self, error):
        if self.original is None: self.original = error

    def uncertain(self, error):
        global _PENDING_ARCHIVE_OWNER
        self.remember(error)
        if _PENDING_ARCHIVE_OWNER is None: _PENDING_ARCHIVE_OWNER = self
        self.unknown = True
        if self.store._strict_original_error is None:
            self.store._strict_original_error = self.original
        self.store.strict_cleanup_unknown(self, kind='archive', error=error)

    def opened(self, fd):
        _require(len(self.fds) < 4, 'archive_descriptor_cap')
        self.fds.append(fd)
        return fd

    def close(self, fd):
        _require(fd in self.fds, 'archive_unknown_descriptor')
        if fd in self.failed_closes: raise self.original
        self.failed_closes.add(fd)
        try: os.close(fd)
        except BaseException as error:
            self.uncertain(error)
            raise
        self.fds.remove(fd)
        self.failed_closes.remove(fd)

    def close_scan(self):
        if self.scan is None: return
        if self.scan_close_failed: raise self.original
        self.scan_close_failed = True
        try: self.scan.close()
        except BaseException as error:
            self.uncertain(error)
            raise self.original
        self.scan = None
        self.scan_close_failed = False

    def cleanup(self):
        if self.scan is not None and not self.scan_close_failed:
            try: self.close_scan()
            except BaseException: pass
        # A failed close is never repeated, including another descriptor with
        # the same recycled integer. Retain its original owner until teardown.
        for fd in list(reversed(self.fds)):
            if fd in self.failed_closes: continue
            try: self.close(fd)
            except BaseException: pass


@contextmanager
def _operation(store, budget):
    from .storage import StrictMutationBusy
    refuse_pending_archive()
    _require(type(budget) is ArchiveBudget, 'archive_original_budget_required')
    budget.check()
    cfg = store.settings
    from .settings import archive_store_only, primary_only_enabled
    _require(archive_store_only(cfg) and primary_only_enabled(cfg)
             and getattr(cfg,'native_owned_compact_gc_enabled',True) is False
             and getattr(cfg,'native_owned_receipt_gc_enabled',True) is False, 'archive_profile_required')
    _require(getattr(store, '_strict_policy', None) == POLICY
             and getattr(cfg, 'strict_primary_policy', None) == POLICY
             and getattr(cfg, 'strict_primary_enabled', False) is True
             and getattr(cfg, 'offline', False) is True
             and getattr(cfg, 'start_worker', True) is False, 'archive_offline_v2_required')
    if store._strict_uncertain: store._strict_refuse()
    if not store._strict_gate.acquire(False): raise StrictMutationBusy('strict_archive_busy')
    owner = None
    try:
        if store._strict_uncertain: store._strict_refuse()
        if (getattr(store, '_strict_archive_owner', None) is not None
                or store._strict_depth != 0 or any(x is not None for x in store._strict_connections)
                or store._strict_worker_owner is not None or store._strict_publication_owner is not None):
            raise StrictMutationBusy('strict_archive_busy')
        _require(store.strict_cleanup_confirmed() is True, 'archive_cleanup_unconfirmed')
        owner = _Owner(store, budget)
        store._strict_archive_owner = owner
        try:
            yield owner
        except BaseException as error:
            owner.remember(error)
            raise
        finally:
            if not owner.lease: owner.cleanup()
            if owner.unknown:
                raise owner.original
            if not owner.lease:
                _require(not owner.fds, 'archive_descriptors_retained')
                if store._strict_archive_owner is owner: store._strict_archive_owner = None
    finally:
        store._strict_gate.release()


@contextmanager
def _db(owner, *, write=False):
    _require(not owner._db_allowed and not owner.lease, 'archive_db_scope')
    owner._db_allowed = True
    try:
        with owner.store.db(budget=owner.budget) as db:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
    except BaseException as error:
        owner.remember(error)
        if owner.store.strict_cleanup_uncertain() or owner.budget.sql_outcome in ('commit_attempted','unknown'):
            owner.uncertain(error)
        raise owner.original
    finally:
        owner._db_allowed = False


_TABLES = {'chunks','sessions','strict_recording_policy','strict_recording_control','strict_recording_outcome',
           'native_core_commits','recording_primary_packets_v1','segments','speaker_turns','speaker_support',
           'speaker_support_vectors','speaker_support_sources','speaker_identity_generations','speaker_observations',
           'recording_work','recording_batches','recording_pages','recording_sources','recording_ranges',
           'stream_chunks','strict_archive_checkpoint','strict_archive_cache'}


def _rows(owner, db, table, where, args, cap, *, exclude=()):
    # Internal table/where strings only. Size preflight precedes materialization.
    _require(table in _TABLES and type(cap) is int and 1 <= cap <= MAX_SEGMENTS, 'archive_query_shape')
    columns = [r['name'] for r in db.execute('PRAGMA table_info(' + table + ')')]
    _require(0 < len(columns) <= 64, 'archive_table_shape')
    columns = [c for c in columns if c not in exclude]
    size_sql = ','.join('COALESCE(length(CAST("'+c+'" AS BLOB)),0)' for c in columns)
    sizes = db.execute('SELECT '+size_sql+' FROM '+table+' WHERE '+where+' LIMIT ?', (*args, cap+1)).fetchall()
    _require(len(sizes) <= cap, 'archive_' + table + '_count')
    total = sum(sum(row) for row in sizes)
    owner.budget.read(total)
    owner.budget.metadata(total)
    _require(total <= MAX_METADATA, 'archive_projection_limit')
    result = [dict(r) for r in db.execute('SELECT '+','.join('"'+c+'"' for c in columns)
               +' FROM '+table+' WHERE '+where+' LIMIT ?', (*args, cap+1))]
    _require(len(result) == len(sizes), 'archive_snapshot_changed')
    return result


def _one(rows, reason):
    _require(len(rows) == 1, reason)
    return rows[0]


def _read_fd(owner, fd, n):
    _require(type(n) is int and 0 <= n <= BUFFER, 'archive_read_size')
    owner.budget.check()
    try: data = os.read(fd, n)
    except BaseException as error: owner.remember(error); raise
    owner.budget.read(len(data))
    return data


def _write_fd(owner, fd, data):
    _require(len(data) <= BUFFER, 'archive_write_size')
    view = memoryview(data)
    while view:
        owner.budget.check()
        try: count = os.write(fd, view)
        except BaseException as error: owner.remember(error); raise
        _require(count > 0, 'archive_short_write')
        view = view[count:]


def _write_bytes(owner, fd, data):
    for pos in range(0, len(data), BUFFER): _write_fd(owner, fd, data[pos:pos+BUFFER])


def _open_file(owner, path, flags, mode=0o600):
    owner.budget.check()
    _require(len(owner.fds)<4,'archive_descriptor_cap')
    try: return owner.opened(os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, mode))
    except BaseException as error: owner.remember(error); raise


def _directory(owner):
    data = Path(owner.store.settings.data_dir).absolute()
    _require(data.is_dir() and not data.is_symlink(), 'archive_data_directory')
    root = data / 'strict-archive-v2'
    try: root.mkdir(mode=0o700)
    except FileExistsError: pass
    fd = _open_file(owner, root, os.O_RDONLY | os.O_DIRECTORY)
    _require(stat.S_ISDIR(os.fstat(fd).st_mode), 'archive_directory')
    # Sync the containing directory as well: an archive root created by this
    # operation is not durable merely because its own contents were synced.
    parent = _open_file(owner, data, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(parent)
    except BaseException as error: owner.remember(error); raise
    finally: _close(owner,parent)
    return root, fd


def _reserve_directory(owner,directory,bytes_per_name):
    """Count all retained names, including failed stages; never sweep orphans."""
    _require(type(bytes_per_name) is int and 0<bytes_per_name<=MAX_ARCHIVE_FILE,
             'archive_directory_reservation')
    owner.budget.check()
    _require(owner.scan is None,'archive_directory_iterator_owned')
    owner.scan=os.scandir(directory)
    count=total=0
    try:
        for entry in owner.scan:
            owner.budget.check();count+=1
            _require(count+2<=MAX_DIRECTORY_ENTRIES,'archive_directory_entry_capacity')
            name=entry.name
            archive=bool(re.fullmatch(r'[a-f0-9]{64}\.archive|\.stage-[a-f0-9]{32}',name))
            cache=bool(re.fullmatch(r'[a-f0-9]{64}\.[1-9][0-9]{0,9}\.cache|\.cache-stage-[a-f0-9]{32}',name))
            _require(archive or cache,'archive_directory_unknown_entry')
            info=entry.stat(follow_symlinks=False)
            _require(stat.S_ISREG(info.st_mode) and 0<=info.st_size<=(MAX_ARCHIVE_FILE if archive else MAX_PAYLOAD),
                     'archive_directory_unsafe_entry')
            total+=info.st_size
            _require(total+2*bytes_per_name<=MAX_DIRECTORY_BYTES,'archive_directory_byte_capacity')
        _require(count+2<=MAX_DIRECTORY_ENTRIES and total+2*bytes_per_name<=MAX_DIRECTORY_BYTES,
                 'archive_directory_capacity')
    except BaseException as error:owner.remember(error);raise
    finally:owner.close_scan()
    space=os.fstatvfs(directory)
    reserve=max(owner.store.settings.disk_reserve_bytes,owner.store.settings.recording_disk_reserve_bytes)
    _require(space.f_bavail*space.f_frsize>=reserve+2*bytes_per_name,'archive_disk_reserve')
    owner.budget.check()


def _file_digest(owner, path, expected_size, expected_signature=None, *, wave_frames=None):
    fd = _open_file(owner, path, os.O_RDONLY)
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_size == expected_size, 'archive_source_size')
        signature = _signature(before)
        if expected_signature is not None: _require(signature == list(expected_signature), 'archive_source_signature')
        h = hashlib.sha256(); head = bytearray(); count = 0
        while count < expected_size:
            block = _read_fd(owner, fd, min(BUFFER, expected_size-count))
            _require(block, 'archive_source_short')
            h.update(block); count += len(block)
            if len(head) < 65536: head.extend(block[:65536-len(head)])
        _require(not _read_fd(owner, fd, 1) and _signature(os.fstat(fd)) == signature, 'archive_source_changed')
        if wave_frames is not None: _wav(bytes(head), expected_size, wave_frames)
        return h.hexdigest(), signature
    except BaseException as error: owner.remember(error); raise
    finally: _close(owner,fd)


def _wav(head, size, frames):
    _require(head[:4] == b'RIFF' and head[8:12] == b'WAVE'
             and len(head) >= 44 and struct.unpack_from('<I', head, 4)[0] + 8 == size, 'archive_wave_header')
    offset, fmt = 12, False
    while offset + 8 <= len(head):
        tag, n = head[offset:offset+4], struct.unpack_from('<I', head, offset+4)[0]
        start = offset + 8
        if tag == b'fmt ':
            _require(n >= 16 and start+16 <= len(head), 'archive_wave_format')
            fmt = struct.unpack_from('<HHIIHH', head, start) == (1,1,16000,32000,2,16)
        if tag == b'data':
            _require(fmt and n == frames*2 and start+n == size, 'archive_wave_extent')
            return start
        offset = start+n+(n%2)
    raise ArchiveUnavailable('archive_wave_data')


def _blob_hash(owner, db, table, column, key, size, *, finite=False):
    _require((table,column) in (('speaker_support','pcm'),('speaker_support_vectors','raw_vector')), 'archive_blob_kind')
    h = hashlib.sha256(); offset = 0
    while offset < size:
        n = min(BUFFER, size-offset)
        row = db.execute('SELECT substr('+column+',?,?) FROM '+table+' WHERE id=? AND length('+column+')=?',
                         (offset+1,n,key,size)).fetchone()
        _require(row is not None and type(row[0]) is bytes and len(row[0]) == n, 'archive_blob_changed')
        block = row[0]; owner.budget.read(n)
        if finite:
            _require(n%4 == 0 and all(x & 0x7f800000 != 0x7f800000 for (x,) in struct.iter_unpack('<I',block)), 'archive_nonfinite_pcm')
        h.update(block); offset += n
    return h.hexdigest()


def _source_spec(owner, object_id, kind, path, size, digest, frames, signature=None):
    path = Path(path)
    uploads = Path(owner.store.settings.data_dir).absolute() / 'uploads'
    _require(path.is_absolute() and path.parent == uploads and 0 < frames <= MAX_FRAMES
             and 44 <= size <= frames*2+65536, 'archive_owned_source')
    actual, sig = _file_digest(owner, path, size, signature, wave_frames=frames)
    _require(actual == digest, 'archive_source_digest')
    return {'object_id':object_id, 'kind':kind, 'bytes':size, 'sha256':digest,
            '_path':str(path), '_signature':sig, '_frames':frames}


def _range_equal(owner, work_spec, source_spec, row):
    a = _open_file(owner, work_spec['_path'], os.O_RDONLY)
    b = None
    try:
        b = _open_file(owner, source_spec['_path'], os.O_RDONLY)
        _require(_signature(os.fstat(a)) == work_spec['_signature']
                 and _signature(os.fstat(b)) == source_spec['_signature'], 'archive_range_signature')
        ah = _read_fd(owner,a,min(65536,work_spec['bytes']))
        bh = _read_fd(owner,b,min(65536,source_spec['bytes']))
        ao = _wav(ah,work_spec['bytes'],work_spec['_frames'])
        bo = _wav(bh,source_spec['bytes'],source_spec['_frames'])
        os.lseek(a,ao+row['work_start_frame']*2,os.SEEK_SET)
        os.lseek(b,bo+row['source_start_frame']*2,os.SEEK_SET)
        left = row['frames']*2
        while left:
            n = min(BUFFER,left)
            x,y = _read_fd(owner,a,n),_read_fd(owner,b,n)
            _require(len(x)==n and x==y, 'archive_range_pcm_conflict')
            left -= n
        _require(_signature(os.fstat(a)) == work_spec['_signature']
                 and _signature(os.fstat(b)) == source_spec['_signature'], 'archive_range_changed')
    except BaseException as error: owner.remember(error); raise
    finally: _close_all(owner,*([b,a] if b is not None else [a]))


def _pcm_digest(owner,spec,start,end):
    _require(type(start) is int and type(end) is int and 0<=start<end<=spec['_frames'],'archive_pcm_interval')
    fd=_open_file(owner,spec['_path'],os.O_RDONLY)
    try:
        _require(_signature(os.fstat(fd))==spec['_signature'],'archive_pcm_signature')
        header=_read_fd(owner,fd,min(65536,spec['bytes']))
        offset=_wav(header,spec['bytes'],spec['_frames'])
        os.lseek(fd,offset+start*2,os.SEEK_SET)
        h=hashlib.sha256();left=(end-start)*2
        while left:
            block=_read_fd(owner,fd,min(BUFFER,left));_require(block,'archive_pcm_short')
            h.update(block);left-=len(block)
        _require(_signature(os.fstat(fd))==spec['_signature'],'archive_pcm_changed')
        return h.hexdigest()
    except BaseException as error:owner.remember(error);raise
    finally:_close(owner,fd)


def _collect(owner, cid):
    from . import strict_recording as strict
    from . import native_owned_core_seams as core
    from . import recording_primary_provenance as pp
    from . import lexical_evidence as lexical
    from . import speaker_support as support
    from . import support_vectors as vectors
    from .native_owned_cell_store import Receipt
    _require(type(cid) is str and re.fullmatch('[a-f0-9]{32}',cid), 'archive_chunk_id')
    meta, specs = {}, []
    with _db(owner) as db:
        get = lambda table,where,args,cap,**kw: _rows(owner,db,table,where,args,cap,**kw)
        chunk = _one(get('chunks','id=?',(cid,),1),'archive_chunk_missing')
        sid = chunk['session_id']
        session = _one(get('sessions','id=?',(sid,),1),'archive_session_missing')
        policy = _one(get('strict_recording_policy','sid=?',(sid,),1),'archive_policy_missing')
        control = _one(get('strict_recording_control','cid=?',(cid,),1),'archive_control_missing')
        frames = strict.frame(chunk['duration'])
        _require(not db.execute("SELECT 1 FROM strict_recording_control WHERE state='active' LIMIT 1").fetchone(),
                 'archive_active_producer')
        _require(session['deleted']==0 and policy['policy']==POLICY and 0<frames<=MAX_FRAMES
                 and chunk['status']=='completed' and chunk['next_offset']==chunk['duration']
                 and control['state']=='completed' and control['nonce'] is None
                 and control['sid']==sid and control['source']==chunk['digest'] and control['frames']==frames,
                 'archive_completed_prefix_required')
        meta.update(chunk=chunk,session=session,policy=policy,control=control)
        outcomes = get('strict_recording_outcome','cid=?',(cid,),MAX_OUTCOMES)
        outcomes.sort(key=lambda r:r['start_frame'])
        cursor = 0
        for out in outcomes:
            _require(out['status'] in ('retained','existing','empty'),'archive_unbound_outcome_unsupported')
            _require(out['start_frame']==cursor and out['end_frame']>cursor and out['source']==chunk['digest'], 'archive_outcome_gap')
            cursor=out['end_frame']
        _require(outcomes and cursor==frames, 'archive_terminal_outcome_missing')
        meta['outcomes']=outcomes
        work = _one(get('recording_work','chunk_id=?',(cid,),1),'archive_work_missing')
        batch = _one(get('recording_batches','id=?',(work['batch_id'],),1),'archive_batch_missing')
        _require(batch['session_id']==sid and work['frames']==frames and work['receipt_state']=='durable'
                 and type(work['native_receipt']) is bytes, 'archive_receipt_unavailable')
        receipt = Receipt.decode(work['native_receipt'])
        stream = get('stream_chunks','chunk_id=?',(cid,),1)
        _require(receipt.session_id==sid and receipt.chunk_id==cid and receipt.digest==chunk['digest']
                 and receipt.frames==frames and receipt.timeline_start==chunk['timeline_start']
                 and receipt.size==work['bytes'] and receipt.receipt_id==work['receipt_id']
                 and list(receipt.signature)==json.loads(work['file_signature'])
                 and receipt.stream_id==(stream[0]['stream_id'] if stream else None), 'archive_receipt_binding')
        spec = _source_spec(owner,'work','canonical_source',chunk['path'],receipt.size,
                            receipt.digest,frames,receipt.signature)
        specs.append(spec)
        meta.update(work=work,batch=batch,stream=stream)
        if work['page_id'] is not None:
            page=_one(get('recording_pages','id=?',(work['page_id'],),1),'archive_page_missing')
            same_page=get('recording_work','page_id=?',(work['page_id'],),1)
            _require(page['released']==1 and page['state']=='published' and page['batch_id']==batch['id']
                     and len(same_page)==1 and page['end_frame']-page['start_frame']==frames,
                     'archive_single_published_page_required')
            meta['page']=page
        else: meta['page']=None
        ranges=get('recording_ranges','chunk_id=?',(cid,),MAX_RANGES)
        _require(ranges, 'archive_source_ranges_missing')
        source_ids=sorted({r['source_id'] for r in ranges})
        _require(len(source_ids)<=MAX_SOURCES, 'archive_source_count')
        sources=get('recording_sources','id IN ('+','.join('?' for _ in source_ids)+')',tuple(source_ids),MAX_SOURCES)
        _require(len(sources)==len(source_ids),'archive_original_source_missing')
        source_map={r['id']:r for r in sources}; source_specs={}
        for src in sources:
            _require(src['batch_id']==batch['id'] and src['source_start_frame'] is not None
                     and src['format']=='wav-pcm16le-16000-mono',
                     'archive_source_binding')
            _require(not db.execute('SELECT 1 FROM recording_ranges WHERE source_id=? AND chunk_id!=? LIMIT 1',
                                    (src['id'],cid)).fetchone(), 'archive_external_source_mapping')
            obj=_source_spec(owner,'source_'+src['id'],'recording_source',src['path'],src['bytes'],src['digest'],src['frames'])
            source_specs[src['id']]=obj;specs.append(obj)
        coverage=[]
        for r in ranges:
            src=source_map[r['source_id']]
            _require(r['batch_id']==batch['id'] and r['relation'] in ('owner','exact_alias')
                     and type(r['frames']) is int and r['frames']>0
                     and 0<=r['work_start_frame']<r['work_start_frame']+r['frames']<=frames
                     and 0<=r['source_start_frame']<r['source_start_frame']+r['frames']<=src['frames']
                     and r['capture_start_frame']==work['capture_start_frame']+r['work_start_frame']
                     and r['capture_start_frame']==src['source_start_frame']+r['source_start_frame'], 'archive_range_binding')
            _range_equal(owner,spec,source_specs[r['source_id']],r)
            if r['relation']=='owner':coverage.append((r['work_start_frame'],r['work_start_frame']+r['frames']))
        cursor=0
        for lo,hi in sorted(coverage):
            _require(lo==cursor,'archive_source_coverage');cursor=hi
        _require(cursor==frames,'archive_source_coverage')
        meta.update(sources=sources,ranges=ranges)
        commits=get('native_core_commits','cid=?',(cid,),MAX_OUTCOMES)
        _require(len(commits)==len(outcomes),'archive_compact_count')
        by_slot={r['slot']:r for r in commits}
        def source_current(part):
            return (part['session_id']==sid and part['chunk_id']==cid and part['source_sha256']==receipt.digest
                    and part['receipt_sha256']==receipt.receipt_id and part['source_frames']==frames)
        packet_rows=get(pp.TABLE,'cid=?',(cid,),MAX_OUTCOMES)
        for header in packet_rows:
            _require(header['sid']==sid and header['cid']==cid and header['rid']==batch['id']
                     and all(type(header[k]) is str and 1<=len(header[k])<=64 for k in ('sid','cid','rid'))
                     and type(header['core_slot']) is str and _ID.fullmatch(header['core_slot'])
                     and type(header['digest']) is str and _ID.fullmatch(header['digest'])
                     and type(header['body']) is bytes and 0<len(header['body'])<=pp.MAX_BODY
                     and all(type(header[k]) is int for k in ('sequence','core_start','core_end','charge'))
                     and 0<header['sequence']<=pp.MAX_COUNTER
                     and 0<=header['core_start']<header['core_end']<=frames
                     and 0<header['charge']<=4*pp.MAX_BODY,
                     'archive_packet_header_bound')
        packets=[]
        for out in outcomes:
            row=by_slot.get(out['slot']);_require(row is not None,'archive_compact_missing')
            _require(row['state']=='none' and row['pending'] is None,'archive_external_compact_dependency')
            commit=json.loads(row['body']);core.validate_commit(commit)
            _require(row['sid']==sid and row['cid']==cid and row['scope_key']==core.scope_key(commit['scope'])
                     and row['logical_end']==commit['part']['logical_end']
                     and core.canonical(commit,core.COMPACT_BYTES)==row['body'] and core.slot(commit)==out['slot']
                     and source_current(commit['part']) and commit['part']['source_start']==out['start_frame']
                     and commit['part']['source_end']==out['end_frame']
                     and chunk['timeline_start']==float.fromhex(commit['scope']['origin'])
                         +(commit['part']['logical_start']-commit['part']['source_start'])/16000, 'archive_compact_binding')
            # Original packet reader/currentness perform their own bounded SQL
            # projections; reserve a conservative full envelope before calling.
            owner.budget.read(4*pp.MAX_BODY)
            owner.budget.metadata(2*pp.MAX_BODY+4096)
            saved=pp.read_exact(db,cid=cid,start=out['start_frame'],end=out['end_frame'])
            if out['status'] in ('retained','existing'):
                _require(saved is not None and saved['header']['digest']==out['packet']
                         and pp.current(db,saved,source_current=source_current), 'archive_packet_currentness')
                _require(_pcm_digest(owner,spec,out['start_frame'],out['end_frame'])
                         ==saved['packet']['owned_pcm16le_sha256'],'archive_packet_pcm_binding')
                packets.append(saved)
            else:_require(saved is None and out['packet'] is None,'archive_outcome_packet')
        _require(len(packet_rows)==len(packets),'archive_extra_packet')
        meta.update(commits=commits,packets=packets,packet_rows=packet_rows)
        segments=get('segments','chunk_id=?',(cid,),MAX_SEGMENTS)
        turns=get('speaker_turns','chunk_id=?',(cid,),MAX_TURNS)
        _require(all(all(r.get(k) is None for k in ('speaker_id','identity_decision_id','identity_profile_revision'))
                     for r in segments+turns),'archive_external_person_identity')
        lexical_refs={}
        for row in segments:
            if row.get('lexical_evidence') is not None:
                draft=json.loads(row['lexical_evidence'])
                _require(type(draft.get('source_parts')) is list and all(p.get('id')==cid for p in draft['source_parts']),
                         'archive_external_lexical_source')
                inv=lexical.current_inventory(db,sid,row,
                    source_lookup=lambda ident: chunk if ident==cid else None)
                _require(inv is not None and all(p['id']==cid for p in inv['inventory']['source_parts']), 'archive_lexical_dependency')
                lexical_refs[row['id']]=inv['source_ref']
        ids={r['id'] for r in segments}
        referenced=[]
        for saved in packets:
            for pref,lref in saved['packet']['refs']:
                ident=int(pref.split('.')[1]);_require(ident in ids,'archive_primary_row_missing')
                referenced.append(ident)
                if lref is not None:_require(lexical_refs.get(ident)==lref,'archive_lexical_binding')
        _require(len(referenced)==len(set(referenced)) and set(referenced)==ids,'archive_primary_row_unbound')
        _require(sum(len(x['packet']['turns']) for x in packets)<=MAX_TURNS,'archive_native_turn_capacity')
        projected=[]
        for saved in packets:
            packet=saved['packet'];part=packet['commit']['part']
            origin=float.fromhex(packet['commit']['scope']['origin'])
            for _,label,a,b,_,_ in packet['turns']:
                lo,hi=max(a,part['logical_start']),min(b,part['logical_end'])
                if lo<hi:projected.append((origin+lo/16000,origin+hi/16000,packet['labels'][label]))
        _require(Counter(projected)==Counter((r['start'],r['end'],r['speaker_local_id']) for r in turns),
                 'archive_turn_geometry_unbound')
        meta.update(segments=segments,turns=turns,
                    turn_projection_authority='Current Store snapshot. Packet binds clipped geometry/local label multiplicities only; row IDs/status/overlap/support fields are not packet-authenticated.')
        supports=get('speaker_support','chunk_id=?',(cid,),MAX_SUPPORTS,exclude=('pcm',))
        observations=get('speaker_observations','chunk_id=?',(cid,),MAX_SUPPORTS)
        obs_ids={o['id'] for o in observations}
        support_ids={r['id'] for r in supports}
        _require(all(r.get('speaker_support_id') is None or r['speaker_support_id'] in support_ids for r in segments+turns),
                 'archive_support_reference_missing')
        _require(all(r.get('candidate_observation_id') is None or r['candidate_observation_id'] in obs_ids for r in turns),
                 'archive_turn_observation_dependency')
        _require(all(o.get('enrolled_speaker_id') is None and o.get('matched_speaker_id') is None
                     and o['session_id']==sid and o['identity_group']==session['identity_group']
                     for o in observations),'archive_external_observation_identity')
        _require(all(r.get('candidate_observation_id') is None or r['candidate_observation_id'] in obs_ids for r in segments),
                 'archive_observation_dependency')
        generation=get('speaker_identity_generations','identity_group=?',(session['identity_group'],),1)
        support_sources=[]; vector_rows=[]
        for row in supports:
            manifest=json.loads(row['manifest']);support.validate_manifest(manifest)
            _require(row['session_id']==sid and row['identity_group']==session['identity_group']
                     and row['status']=='available' and 0<row['pcm_bytes']<=support.MAX_PCM_BYTES
                     and row['pcm_bytes']==manifest['support']['pooled_sample_count']*4
                     and _sha(support.canonical(manifest))==row['manifest_sha256']
                     and all(p['chunk_id']==cid and p['source_sha256']==receipt.digest for p in manifest['source_parts'])
                     and (row['observation_id'] is None or row['observation_id'] in obs_ids)
                     and len(generation)==1 and row['generation']==generation[0]['generation'], 'archive_support_binding')
            links=get('speaker_support_sources','support_id=?',(row['id'],),1)
            _require(len(links)==1 and links[0]['chunk_id']==cid,'archive_support_source_dependency')
            support_sources.extend(links)
            digest=_blob_hash(owner,db,'speaker_support','pcm',row['id'],row['pcm_bytes'],finite=True)
            _require(digest==manifest['support']['pooled_pcm_sha256'],'archive_support_pcm')
            specs.append({'object_id':'support_'+row['id'],'kind':'support_pcm','bytes':row['pcm_bytes'],'sha256':digest,
                          '_table':'speaker_support','_column':'pcm','_key':row['id']})
            vector_header=_one(get('speaker_support_vectors','support_id=?',(row['id'],),1,
                                   exclude=('raw_vector',)),'archive_vector_missing')
            lengths=db.execute('SELECT length(raw_vector),length(CAST(space AS BLOB)) FROM speaker_support_vectors WHERE support_id=?',(row['id'],)).fetchone()
            _require(lengths is not None and 0<lengths[0]<=65536 and 0<lengths[1]<=16384,'archive_vector_bound')
            # Original read() projects all vector metadata again and its exact
            # support owner join. Preflight above bounds every text column.
            original_projection=len(_canonical(_jsonable(vector_header)))+len(_canonical({
                k:row[k] for k in ('identity_group','generation','manifest','manifest_sha256')}))
            owner.budget.read(lengths[0]+original_projection)
            owner.budget.metadata(original_projection)
            vector,_=vectors.read(db,row['id'])
            _require({k:v for k,v in vector.items() if k!='raw_vector'}==vector_header,
                     'archive_vector_snapshot_changed')
            _require(vector['normalization']==vectors.NORMALIZATION,'archive_vector_normalization')
            specs.append({'object_id':'vector_'+vector['id'],'kind':'vector','bytes':len(vector['raw_vector']),
                          'sha256':vector['vector_sha256'],'_table':'speaker_support_vectors','_column':'raw_vector','_key':vector['id']})
            vector_rows.append({k:v for k,v in vector.items() if k!='raw_vector'})
        _require(len(supports)==len(vector_rows),'archive_vector_missing')
        meta.update(supports=supports,support_sources=support_sources,vectors=vector_rows,
                    observations=observations,generation=generation)
    _require(len(specs)<=MAX_OBJECTS,'archive_object_count')
    public=[{k:s[k] for k in ('object_id','kind','bytes','sha256')} for s in specs]
    _require(len({s['object_id'] for s in public})==len(public),'archive_duplicate_object')
    manifest={'format':FORMAT,'policy':POLICY,'store_key':_store_key(owner.store),'cid':cid,
              'source':chunk['digest'],'tip':frames,'metadata':_jsonable(meta),'objects':public}
    raw=_canonical(manifest)
    _require(len(raw)<=MAX_METADATA,'archive_metadata_limit')
    # Projection is cumulative for the API, including the guarded recheck.
    owner.budget.metadata(len(raw))
    return manifest,specs


def _copy_spec(owner, out, spec):
    h=hashlib.sha256();size=spec['bytes'];owner.budget.payload(size)
    if '_path' in spec:
        fd=_open_file(owner,spec['_path'],os.O_RDONLY)
        try:
            _require(_signature(os.fstat(fd))==spec['_signature'],'archive_copy_source_changed')
            offset=0
            while offset<size:
                block=_read_fd(owner,fd,min(BUFFER,size-offset))
                _require(block,'archive_copy_short');_write_fd(owner,out,block);h.update(block);offset+=len(block)
            _require(not _read_fd(owner,fd,1) and _signature(os.fstat(fd))==spec['_signature'],'archive_copy_source_changed')
        except BaseException as error:owner.remember(error);raise
        finally:_close(owner,fd)
    else:
        with _db(owner) as db:
            offset=0
            while offset<size:
                n=min(BUFFER,size-offset)
                row=db.execute('SELECT substr('+spec['_column']+',?,?) FROM '+spec['_table']
                    +' WHERE id=? AND length('+spec['_column']+')=?',(offset+1,n,spec['_key'],size)).fetchone()
                _require(row is not None and type(row[0]) is bytes and len(row[0])==n,'archive_copy_blob_changed')
                owner.budget.read(n);_write_fd(owner,out,row[0]);h.update(row[0]);offset+=n
    _require(h.hexdigest()==spec['sha256'],'archive_copy_digest')


def _close(owner, fd):
    try:owner.close(fd)
    except BaseException:
        raise owner.original


def _close_all(owner,*fds):
    for fd in fds:
        if fd in owner.failed_closes: continue
        try: owner.close(fd)
        except BaseException: pass
    if owner.unknown: raise owner.original


def _read_exact(owner,fd,n):
    _require(type(n) is int and 0<=n<=MAX_METADATA,'archive_exact_read_bound')
    parts=[];left=n
    while left:
        data=_read_fd(owner,fd,min(BUFFER,left));_require(data,'archive_short_read')
        parts.append(data);left-=len(data)
    return b''.join(parts)


def _unique(pairs):
    value={}
    for k,v in pairs:
        _require(k not in value,'archive_duplicate_json_key');value[k]=v
    return value


def _load_archive(owner,root,checkpoint):
    archive_id=checkpoint['archive_id']
    _require(type(archive_id) is str and _ID.fullmatch(archive_id)
             and checkpoint['store_key']==_store_key(owner.store),'archive_checkpoint_owner')
    path=root/(archive_id+'.archive')
    fd=_open_file(owner,path,os.O_RDONLY)
    try:
        before=os.fstat(fd);signature=_signature(before)
        _require(stat.S_ISREG(before.st_mode) and before.st_size==checkpoint['bytes']
                 and before.st_size<=len(MAGIC)+4+MAX_METADATA+MAX_PAYLOAD,'archive_file_bound')
        prefix=_read_exact(owner,fd,len(MAGIC)+4)
        _require(prefix[:len(MAGIC)]==MAGIC,'archive_magic')
        n=struct.unpack('>I',prefix[len(MAGIC):])[0]
        _require(0<n<=MAX_METADATA,'archive_manifest_bound')
        owner.budget.metadata(n);raw=_read_exact(owner,fd,n)
        _require(_sha(raw)==checkpoint['manifest_sha256']==archive_id,'archive_manifest_digest')
        manifest=json.loads(raw,object_pairs_hook=_unique)
        _require(_canonical(manifest)==raw and set(manifest)=={'format','policy','store_key','cid','source','tip','metadata','objects'}
                 and manifest['format']==FORMAT and manifest['policy']==POLICY
                 and all(manifest[k]==checkpoint[k] for k in ('store_key','cid','source','tip')),
                 'archive_manifest_binding')
        objects=manifest['objects']
        _require(type(objects) is list and 1<=len(objects)<=MAX_OBJECTS and type(manifest['metadata']) is dict,
                 'archive_objects')
        h=hashlib.sha256(prefix+raw);seen=set();offset=len(prefix)+n;offsets={}
        for obj in objects:
            _require(type(obj) is dict and set(obj)=={'object_id','kind','bytes','sha256'}
                     and type(obj['object_id']) is str and re.fullmatch('[a-z0-9_]{1,100}',obj['object_id'])
                     and obj['object_id'] not in seen and obj['kind'] in ('canonical_source','recording_source','support_pcm','vector')
                     and type(obj['bytes']) is int and 0<obj['bytes']<=MAX_PAYLOAD
                     and type(obj['sha256']) is str and _ID.fullmatch(obj['sha256']), 'archive_object_shape')
            seen.add(obj['object_id']);owner.budget.payload(obj['bytes']);offsets[obj['object_id']]=offset
            oh=hashlib.sha256();left=obj['bytes']
            while left:
                data=_read_fd(owner,fd,min(BUFFER,left));_require(data,'archive_object_short')
                oh.update(data);h.update(data);left-=len(data)
            _require(oh.hexdigest()==obj['sha256'],'archive_object_digest');offset+=obj['bytes']
        _require(offset==checkpoint['bytes'] and not _read_fd(owner,fd,1)
                 and h.hexdigest()==checkpoint['file_sha256']
                 and _signature(os.fstat(fd))==signature,'archive_file_integrity')
        return manifest,signature,offsets
    except BaseException as error:owner.remember(error);raise
    finally:_close(owner,fd)


def _public(checkpoint,manifest):
    return {**{k:checkpoint[k] for k in ('archive_id','cid','source','tip','manifest_sha256','file_sha256','bytes')},
            'objects':manifest['objects']}


def _checkpoint(owner,archive_id):
    _require(type(archive_id) is str and _ID.fullmatch(archive_id),'archive_id')
    with _db(owner) as db:
        return _one(_rows(owner,db,'strict_archive_checkpoint','archive_id=?',(archive_id,),1),'archive_checkpoint_missing')


def archive_completed_chunk(store,cid,budget):
    with _operation(store,budget) as owner:
        manifest,specs=_collect(owner,cid)
        raw=_canonical(manifest);archive_id=_sha(raw)
        with _db(owner) as db:
            existing=_rows(owner,db,'strict_archive_checkpoint','cid=?',(cid,),1)
            if not existing:
                _require(db.execute('SELECT COUNT(*) FROM strict_archive_checkpoint').fetchone()[0]<MAX_ARCHIVES,
                         'archive_checkpoint_capacity')
        root,directory=_directory(owner)
        if existing:
            row=existing[0]
            _require(row['archive_id']==archive_id,'archive_existing_prefix_changed')
            old,_,_=_load_archive(owner,root,row)
            _require(_canonical(old)==raw,'archive_existing_manifest_changed')
            _close(owner,directory)
            budget.check()
            return _public(row,old)
        size=len(MAGIC)+4+len(raw)+sum(x['bytes'] for x in specs)
        _reserve_directory(owner,directory,size)
        stage=root/('.stage-'+uuid.uuid4().hex)
        fd=_open_file(owner,stage,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        try:
            _write_bytes(owner,fd,MAGIC+struct.pack('>I',len(raw))+raw)
            for spec in specs:_copy_spec(owner,fd,spec)
            os.fsync(fd)
        except BaseException as error:owner.remember(error);raise
        finally:_close(owner,fd)
        size=len(MAGIC)+4+len(raw)+sum(x['bytes'] for x in specs)
        file_hash,_=_file_digest(owner,stage,size)
        final=root/(archive_id+'.archive')
        try:os.link(stage,final,follow_symlinks=False)
        except FileExistsError:
            actual,_=_file_digest(owner,final,size)
            _require(actual==file_hash,'archive_existing_file_conflict')
        os.fsync(directory)
        os.unlink(stage)
        os.fsync(directory)
        verified,_=_file_digest(owner,final,size)
        _require(verified==file_hash,'archive_published_file_changed')
        _close(owner,directory)
        row={'archive_id':archive_id,'cid':cid,'source':manifest['source'],'tip':manifest['tip'],
             'store_key':manifest['store_key'],'manifest_sha256':archive_id,'file_sha256':file_hash,'bytes':size}
        # Gate is still held. The original Store and all source evidence are
        # rechecked, not a caller assertion or an old retained manifest.
        fresh,_=_collect(owner,cid)
        _require(_canonical(fresh)==raw,'archive_checkpoint_source_changed')
        with _db(owner,write=True) as db:
            _require(db.execute('SELECT COUNT(*) FROM strict_archive_checkpoint').fetchone()[0]<MAX_ARCHIVES,
                     'archive_checkpoint_capacity')
            db.execute('INSERT INTO strict_archive_checkpoint VALUES(?,?,?,?,?,?,?,?)',tuple(row[k] for k in
                ('archive_id','cid','source','tip','store_key','manifest_sha256','file_sha256','bytes')))
        budget.check()
        return _public(row,manifest)


class ArchiveSourceLease:
    """One original owned descriptor; close is never retried after uncertainty."""
    def __init__(self,owner,fd,row,obj,path):
        self._owner,self._fd=owner,fd
        self.archive_id,self.object_id=row['archive_id'],row['object_id']
        self.cache_id,self.generation=row['cache_id'],row['generation']
        self.path,self.signature=str(path),json.loads(row['signature'])
        self._size,self._position=obj['bytes'],0
        self._closed=False

    def read(self,size):
        _require(type(size) is int and 0<=size<=BUFFER,'archive_lease_read_bound')
        _require(not self._closed,'archive_lease_closed')
        owner=self._owner
        _require(threading.get_ident()==owner.thread,'archive_lease_thread')
        if owner.unknown:raise owner.original
        _require(owner.store._strict_archive_owner is owner,'archive_lease_owner')
        if not owner.store._strict_gate.acquire(False):
            from .storage import StrictMutationBusy
            raise StrictMutationBusy('strict_archive_busy')
        try:
            _require(_signature(os.fstat(self._fd))==self.signature,'archive_cache_changed')
            data=_read_fd(owner,self._fd,min(size,self._size-self._position))
            if self._position<self._size and size:_require(data,'archive_cache_short')
            self._position+=len(data)
            return data
        except BaseException as error:owner.remember(error);raise
        finally:owner.store._strict_gate.release()

    def close(self):
        owner=self._owner
        _require(threading.get_ident()==owner.thread,'archive_lease_thread')
        if owner.unknown:raise owner.original
        if self._closed:return True
        if not owner.store._strict_gate.acquire(False):
            from .storage import StrictMutationBusy
            raise StrictMutationBusy('strict_archive_busy')
        try:
            _require(owner.store._strict_archive_owner is owner,'archive_lease_owner')
            # Mandatory close is not suppressed by an expired read budget.
            _close(owner,self._fd)
            _require(not owner.fds,'archive_lease_descriptors')
            owner.lease=False;owner.store._strict_archive_owner=None;self._closed=True
            return True
        finally:owner.store._strict_gate.release()

    def __enter__(self):return self
    def __exit__(self,kind,error,tb):
        if error is not None:self._owner.remember(error)
        self.close()
        return False


def _cache_path(root,row):
    _require(_ID.fullmatch(row['cache_id']) and type(row['generation']) is int and 0<row['generation']<2**31,
             'archive_cache_identity')
    _require(row['cache_id']==_sha(_canonical([row['archive_id'],row['object_id'],row['generation']])),
             'archive_cache_generation_binding')
    return root/(row['cache_id']+'.'+str(row['generation'])+'.cache')


def open_archived_source(store,archive_id,object_id,budget):
    with _operation(store,budget) as owner:
        checkpoint=_checkpoint(owner,archive_id)
        root,directory=_directory(owner)
        manifest,archive_sig,offsets=_load_archive(owner,root,checkpoint)
        objects=[o for o in manifest['objects'] if o['object_id']==object_id]
        obj=_one(objects,'archive_object_missing')
        with _db(owner) as db:
            latest=db.execute('SELECT MAX(generation) FROM strict_archive_cache WHERE archive_id=? AND object_id=?',(archive_id,object_id)).fetchone()[0]
            old=(_rows(owner,db,'strict_archive_cache','archive_id=? AND object_id=? AND generation=?',(archive_id,object_id,latest),1) if latest is not None else [])
            if not old or old[0]['state']=='absent':
                _require(db.execute('SELECT COUNT(*) FROM strict_archive_cache').fetchone()[0]<MAX_CACHES,'archive_cache_capacity')
        if old:
            _require(old[0]['archive_id']==archive_id and old[0]['object_id']==object_id,'archive_cache_binding')
            _require(old[0]['state']!='retiring','archive_retirement_pending')
        if old and old[0]['state']=='present':
            row=old[0];path=_cache_path(root,row)
            hashed,sig=_file_digest(owner,path,obj['bytes'],json.loads(row['signature']))
            _require(hashed==obj['sha256'],'archive_cache_digest')
        else:
            generation=old[0]['generation']+1 if old else 1
            cache_id=_sha(_canonical([archive_id,object_id,generation]))
            row={'cache_id':cache_id,'archive_id':archive_id,'object_id':object_id,
                 'generation':generation,'state':'present','signature':None}
            path=_cache_path(root,row)
            _reserve_directory(owner,directory,obj['bytes'])
            stage=root/('.cache-stage-'+uuid.uuid4().hex)
            source=_open_file(owner,root/(archive_id+'.archive'),os.O_RDONLY)
            target=None
            try:
                _require(_signature(os.fstat(source))==archive_sig,'archive_copy_archive_changed')
                target=_open_file(owner,stage,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
                os.lseek(source,offsets[object_id],os.SEEK_SET)
                h=hashlib.sha256();left=obj['bytes']
                while left:
                    block=_read_fd(owner,source,min(BUFFER,left));_require(block,'archive_fetch_short')
                    _write_fd(owner,target,block);h.update(block);left-=len(block)
                _require(h.hexdigest()==obj['sha256'] and _signature(os.fstat(source))==archive_sig,'archive_fetch_digest')
                os.fsync(target)
            except BaseException as error:owner.remember(error);raise
            finally:_close_all(owner,*([target,source] if target is not None else [source]))
            os.link(stage,path,follow_symlinks=False)
            os.fsync(directory);os.unlink(stage);os.fsync(directory)
            hashed,sig=_file_digest(owner,path,obj['bytes'])
            _require(hashed==obj['sha256'],'archive_fetch_published_digest')
            row['signature']=json.dumps(sig,separators=(',',':'))
            with _db(owner,write=True) as db:
                if old:
                    current=db.execute('SELECT state FROM strict_archive_cache WHERE cache_id=? AND generation=?',(old[0]['cache_id'],old[0]['generation'])).fetchone()
                    _require(current is not None and current[0]=='absent','archive_cache_update_changed')
                _require(db.execute('SELECT COUNT(*) FROM strict_archive_cache').fetchone()[0]<MAX_CACHES,'archive_cache_capacity')
                db.execute('INSERT INTO strict_archive_cache VALUES(?,?,?,?,?,?)',tuple(row[k] for k in
                           ('cache_id','archive_id','object_id','generation','state','signature')))
        _close(owner,directory)
        fd=_open_file(owner,path,os.O_RDONLY)
        _require(_signature(os.fstat(fd))==json.loads(row['signature']),'archive_lease_incarnation')
        lease=ArchiveSourceLease(owner,fd,row,obj,path)
        budget.check()
        owner.lease=True
        return lease


def _path_absent(path):
    try: path.lstat()
    except FileNotFoundError: return True
    return False


def retire_cached_source(store,cache_id,budget):
    with _operation(store,budget) as owner:
        _require(type(cache_id) is str and _ID.fullmatch(cache_id),'archive_cache_id')
        with _db(owner) as db:
            row=_one(_rows(owner,db,'strict_archive_cache','cache_id=?',(cache_id,),1),'archive_cache_missing')
            latest=db.execute('SELECT MAX(generation) FROM strict_archive_cache WHERE archive_id=? AND object_id=?',(row['archive_id'],row['object_id'])).fetchone()[0]
            _require(latest==row['generation'],'archive_stale_cache_generation')
        checkpoint=_checkpoint(owner,row['archive_id'])
        root,directory=_directory(owner)
        manifest,_,_=_load_archive(owner,root,checkpoint)
        obj=_one([o for o in manifest['objects'] if o['object_id']==row['object_id']],'archive_object_missing')
        path=_cache_path(root,row)
        if row['state']=='absent':
            _require(_path_absent(path),'archive_absent_replaced')
            _close(owner,directory)
            budget.check()
            return {'cache_id':cache_id,'generation':row['generation'],'state':'absent'}
        if row['state']=='present':
            actual,_=_file_digest(owner,path,obj['bytes'],json.loads(row['signature']))
            _require(actual==obj['sha256'],'archive_retirement_digest')
            with _db(owner,write=True) as db:
                changed=db.execute("UPDATE strict_archive_cache SET state='retiring' WHERE cache_id=? AND generation=? AND state='present' AND signature=?",
                    (cache_id,row['generation'],row['signature'])).rowcount
                _require(changed==1,'archive_retirement_changed')
        else:_require(row['state']=='retiring','archive_cache_state')
        try:
            current=path.lstat()
        except FileNotFoundError:
            # Only the exact already-committed retiring intent permits absence.
            current=None
        if current is not None:
            _require(stat.S_ISREG(current.st_mode) and _signature(current)==json.loads(row['signature']),
                     'archive_retirement_replaced')
            os.unlink(path)
        _require(_path_absent(path),'archive_retirement_not_absent')
        os.fsync(directory)
        _close(owner,directory)
        _require(_path_absent(path),'archive_retirement_reappeared')
        with _db(owner,write=True) as db:
            changed=db.execute("UPDATE strict_archive_cache SET state='absent' WHERE cache_id=? AND generation=? AND state='retiring' AND signature=?",
                (cache_id,row['generation'],row['signature'])).rowcount
            _require(changed==1,'archive_retirement_checkpoint_changed')
        budget.check()
        return {'cache_id':cache_id,'generation':row['generation'],'state':'absent'}
