"""Explicit, volatile, one-slot MOSS service; no backlog or invocation retry."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import threading
import time
import uuid

from .moss_handy_controller import MossController, OwnedDriver
from .moss_handy_profile import NAME
from .native_execution_permit import AdmissionError
from .native_owned_support_cache import LOCK_SECONDS, ReadError

NAMESPACE = 'moss-handy-jobs'
SLOT = 'slot'
MAX_ACCEPTED = 64
TTL_SECONDS = 900
MAX_LEDGER_BYTES = 32768
MAX_RETAINED_BYTES = 1024 * 1024
MAX_RESULT_BYTES, MAX_RAW_BYTES = 262144, 131072
MAX_DETAIL_BYTES, MAX_LIST_BYTES = 16384, 32768
CLEAN_TERMINAL = frozenset(('returned_diagnostic', 'failed', 'cancelled', 'invalidated'))


class JobError(ReadError):
    def __init__(self, status_code, reason, summary):
        super().__init__(status_code, reason)
        self.summary = summary


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('ascii')


def ownership_fence(data_dir):
    """Noncreating descriptor-relative check; no symlink is followed."""
    root = Path(data_dir)
    if not root.is_absolute() or len(root.parts) > 256:
        raise RuntimeError('MOSS ownership namespace unavailable')
    if not _descriptor_support():
        return _absent_namespace_fence(root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(root.anchor, flags)
        for component in root.parts[1:]:
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                return
            os.close(descriptor)
            descriptor = child
        root_info = os.fstat(descriptor)
        if (root.lstat().st_dev, root.lstat().st_ino) != (root_info.st_dev, root_info.st_ino):
            raise RuntimeError('MOSS ownership namespace unavailable')
        try:
            namespace = os.open(NAMESPACE, flags, dir_fd=descriptor)
        except FileNotFoundError:
            return
        try:
            info = _private_directory_fd(descriptor, NAMESPACE, namespace, root_info.st_dev)
            with os.scandir(namespace) as entries:
                names = [entry.name for _, entry in zip(range(2), entries)]
            if names not in ([], [SLOT]):
                raise RuntimeError('MOSS ownership namespace unavailable')
            if names:
                slot = os.open(SLOT, flags, dir_fd=namespace)
                try:
                    _private_directory_fd(namespace, SLOT, slot, info.st_dev)
                    with os.scandir(slot) as entries:
                        if next(entries, None) is not None:
                            raise RuntimeError('MOSS ownership unresolved')
                    _private_directory_fd(namespace, SLOT, slot, info.st_dev)
                finally:
                    os.close(slot)
            _private_directory_fd(descriptor, NAMESPACE, namespace, root_info.st_dev)
        finally:
            os.close(namespace)
    except OSError:
        raise RuntimeError('MOSS ownership namespace unavailable') from None
    finally:
        if descriptor is not None:os.close(descriptor)


def _descriptor_support():
    return (os.name == 'posix' and all(hasattr(os, name) for name in ('O_DIRECTORY', 'O_NOFOLLOW', 'getuid'))
            and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
            and os.scandir in os.supports_fd)


def _absent_namespace_fence(root):
    """OFF-only compatibility: absence is sufficient; existing evidence is not."""
    try:
        for parent in reversed((root, *root.parents)):
            try:
                info = parent.lstat()
            except FileNotFoundError:
                return
            if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)):
                raise RuntimeError('MOSS ownership is uncertain; inspect the namespace on a supported Linux host')
        try:
            (root / NAMESPACE).lstat()
        except FileNotFoundError:
            return
        raise RuntimeError('MOSS ownership namespace exists; inspect it on a supported Linux host before startup')
    except OSError:
        raise RuntimeError('MOSS ownership is uncertain; inspect the namespace on a supported Linux host') from None


def _private_directory_fd(parent, name, descriptor, device):
    info = os.fstat(descriptor)
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.getuid() or info.st_dev != device
            or (named.st_dev, named.st_ino, named.st_mode) != (info.st_dev, info.st_ino, info.st_mode)):
        raise RuntimeError('MOSS ownership namespace unavailable')
    return info


def create_namespace(data_dir):
    if sys.platform != 'linux' or not _descriptor_support():
        raise RuntimeError('Enabled MOSS jobs requires the supported Linux ownership primitives')
    ownership_fence(data_dir)
    namespace = Path(data_dir) / NAMESPACE
    namespace.mkdir(mode=0o700, exist_ok=True)
    slot = namespace / SLOT
    slot.mkdir(mode=0o700, exist_ok=True)
    ownership_fence(data_dir)
    return slot


class _ServiceGate:
    def __init__(self, owner, record):
        self.owner, self.record = owner, record

    def check(self, phase):
        owner, record = self.owner, self.record
        if (owner.clock() >= record['deadline']
                or owner.cache.clock() >= record['token'].target.expires):
            record['cancelled'].set()
        if record['lease'].cancelled.is_set():
            record['cancelled'].set()
        owner.permit.check_optional(record, phase)
        if phase in ('before_input', 'before_admission'):
            owner.cache.revalidate_dispatch(record['token'])
        if record['cancelled'].is_set():
            raise AdmissionError('optional_cancelled')

    def inputs_written(self):
        self.record['lease'].release_pcm()

    def cleaning(self):
        self.record['state'] = 'cleaning'
        self.owner.permit.cleaning(self.record)

    def cleanup_sample(self):
        # Resource failure is diagnostic during the unchanged owned cleanup.
        try:
            self.owner.permit._resource_check(reserve=False)
        except Exception:
            self.record['cancelled'].set()


class MossJobs:
    def __init__(self, store, cache, permit, *, clock=time.monotonic,
                 _driver_factory=OwnedDriver, _thread_factory=threading.Thread):
        self.store, self.cache, self.permit, self.clock = store, cache, permit, clock
        self.jobs_epoch = uuid.uuid4().hex
        self.directory = create_namespace(store.settings.data_dir)
        self._driver_factory, self._thread_factory = _driver_factory, _thread_factory
        self._lock = threading.RLock()
        self._keys, self._records = {}, []
        self._live = None
        self._closed = False
        self.stopped = False
        cache.jobs_owner = self

    @contextmanager
    def _gate(self):
        if not self._lock.acquire(timeout=LOCK_SECONDS):
            raise ReadError(503, 'jobs_lock_unavailable')
        try:
            yield
        finally:
            self._lock.release()

    def _enabled(self):
        return (not self._closed and bool(getattr(self.store.settings, 'moss_jobs_enabled', False))
                and self.permit._configuration_current())

    def _epoch(self, value):
        if value != self.jobs_epoch:
            raise ReadError(409, 'jobs_epoch_changed')

    def _expire(self):
        record = self._live
        if record is None:
            return
        expired = (self.clock() >= record['deadline']
                   or self.cache.clock() >= record['token'].target.expires)
        if not self._enabled() or expired or record['invalidated']:
            record['cancelled'].set()
            record['result'] = record['raw'] = None
            record['expired'] = expired
            if record['cleaned']:
                self._retire(record)

    def _retire(self, record):
        record['purge_pending'] = True
        if not record['cleaned']:
            raise ReadError(503, 'cleanup_pending')
        lease = record['lease']
        if lease is not None and not lease.released and not lease.release():
            raise ReadError(503, 'dispatch_cleanup_pending')
        controller = record['controller']
        if controller is not None and record['controller_id'] is not None:
            try:
                controller.discard(record['controller_id'])
            except Exception:
                raise ReadError(503, 'discard_unavailable') from None
        record['result'] = record['raw'] = None
        record['state'] = 'expired' if record['expired'] else 'retired'
        record['controller'] = record['lease'] = record['token'] = None
        record['thread'] = None
        record['purge_pending'] = False
        self._live = None

    def _cancel_record(self, record):
        record['cancelled'].set()
        # Callback may run while submit holds its manager gate. It never joins.
        controller, jid = record.get('controller'), record.get('controller_id')
        thread = record.get('thread')
        # Driver gates consume the shared event without waiting on the
        # controller lock held by a cleanup retry. Only an unstarted prepared
        # job needs synchronous no-child cleanup after a failed handoff.
        if (controller is not None and jid is not None and record['state'] == 'reserved'
                and (thread is None or not thread.is_alive())):
            try:
                controller.cancel(jid)
            except Exception:
                pass

    def submit(self, sid, cid, request, key, *, _admission_open=None):
        self._epoch(request['jobs_epoch'])
        if request['profile'] != NAME:
            raise ReadError(409, 'profile_mismatch')
        self.cache.authorize_session(sid)
        digest = hashlib.sha256(encoded([sid, cid, request])).hexdigest()
        with self._gate():
            self._expire()
            previous = self._keys.get((sid, key))
            if previous is not None:
                if previous['digest'] != digest:
                    raise ReadError(409, 'idempotency_conflict')
                if previous['state'] in ('expired', 'retired') or previous['expired']:
                    raise JobError(410, 'job_retired', self._summary(previous))
                return 200, self._summary(previous)
            if not self._enabled():
                raise ReadError(409, 'feature_disabled')
            if len(self._records) >= MAX_ACCEPTED:
                raise ReadError(429, 'epoch_capacity')
            if self._live is not None and not self._live['cleaned']:
                raise ReadError(409, 'job_slot_occupied')
            lease = self.cache.acquire_dispatch(sid, cid,
                expected_capture_epoch=request['expected_capture_epoch'],
                expected_revision=request['expected_revision'], support_id=request['support_id'])
            record = dict(job_id=uuid.uuid4().hex, session_id=sid, chunk_id=cid,
                sequence=len(self._records) + 1, digest=digest, key=key, profile=NAME,
                support_id=request['support_id'], capture_epoch=request['expected_capture_epoch'],
                revision=request['expected_revision'], deadline=self.clock() + TTL_SECONDS,
                state='reserved', cleaned=False, expired=False, invalidated=False, purge_pending=False, invocation_possible=False,
                cancelled=lease.cancelled, lease=lease, token=lease.token, controller=None,
                controller_id=None, thread=None, result=None, raw=None, raw_reason='not_terminal')
            try:
                self.permit.reserve_optional(record, record['cancelled'], lambda:self._cancel_record(record))
            except AdmissionError as exc:
                lease.release()
                status = 409 if exc.reason == 'primary_or_optional_busy' else 503
                raise ReadError(status, exc.reason) from None
            try:
                # Fresh source gate after permit reservation, before retiring old content.
                self.cache.revalidate_dispatch(lease.token)
                if self._live is not None:
                    self._retire(self._live)
                candidate = [[x['session_id'], x['key'], x['digest'], x['job_id']] for x in self._records]
                candidate.append([sid, key, digest, record['job_id']])
                if len(encoded(candidate)) > MAX_LEDGER_BYTES:
                    raise ReadError(429, 'epoch_capacity')
                if _admission_open is not None and not _admission_open():
                    raise ReadError(503, 'response_closed')
            except BaseException:
                self.permit.release_optional(record, cleaned=True)
                lease.release()
                raise
            self._records.append(record)
            self._keys[(sid, key)] = record
            self._live = record
            try:
                gate = _ServiceGate(self, record)
                controller = MossController(self.directory, profile_name=NAME,
                    model_path=self.store.settings.moss_jobs_model_path,
                    _driver_factory=self._driver_factory, _service_gate=gate,
                    _service_context={'jobs_epoch':self.jobs_epoch, 'capture_epoch':self.cache.epoch,
                                      'source_binding':lease.token.target.binding})
                record['controller'] = controller
                record['controller_id'] = controller.submit(lease.support, lease.pcm)
                thread = self._thread_factory(target=self._run, args=(record,), name='moss-job', daemon=True)
                record['thread'] = thread
                thread.start()
            except BaseException:
                self._cancel_record(record)
                self._finish(record, preparation_failed=True)
            return 202, self._summary(record)

    def _run(self, record):
        record['state'] = 'running'
        try:
            record['controller'].run(record['controller_id'])
        except BaseException:
            self._cancel_record(record)
        finally:
            self._finish(record)

    def _finish(self, record, preparation_failed=False):
        with self._lock:
            try:
                self._finish_locked(record, preparation_failed)
            except BaseException:
                # A projection/source failure is never ownership release proof.
                record['state'] = 'cleanup_blocked'
                record['cleaned'] = False
                record['result'] = record['raw'] = None
                self.permit.release_optional(record, cleaned=False)

    def _finish_locked(self, record, preparation_failed=False):
        controller, jid = record['controller'], record['controller_id']
        status = None
        try:
            if controller is not None and jid is not None:
                status = controller.status(jid)  # One bounded terminal serialization.
                receipt = status['cleanup'] or {}
                cleaned = (receipt.get('cleanup_verified') is True
                    and receipt.get('original_handle_waited_and_reaped') is True
                    and receipt.get('remaining_live_owned') == [] and not receipt.get('cleanup_failure'))
            else:
                # No process was ever handed a job. A partial claim still blocks.
                cleaned = preparation_failed and not any(self.directory.iterdir())
        except Exception:
            cleaned = False
        record['cleaned'] = cleaned
        record['invocation_possible'] = bool(status and status['invocation_possible'])
        if not cleaned:
            record['state'] = 'cleanup_blocked'
            record['raw'] = record['result'] = None
            self.permit.release_optional(record, cleaned=False)
            return
        valid = not record['cancelled'].is_set() and self._enabled() and self.clock() < record['deadline']
        if valid:
            try:
                self.cache.revalidate_dispatch(record['token'])
            except Exception:
                valid = False
                record['invalidated'] = True
                record['cancelled'].set()
        if status is None:
            record['state'] = 'failed'
        elif not valid:
            record['state'] = 'cancelled' if record['cancelled'].is_set() else 'invalidated'
        else:
            record['state'] = status['state']
        record['raw'] = record['result'] = None
        if valid and status is not None:
            raw = controller.exact_raw(jid)
            receipt = status.get('native_receipt') or {}
            record['raw_reason'] = receipt.get('raw_unavailable_reason') or 'no_native_result'
            if type(raw) is bytes and len(raw) <= MAX_RAW_BYTES:
                record['raw'] = raw
                record['raw_reason'] = None
            if status.get('publication_valid') and status.get('result') is not None:
                result = encoded(status['result'])
                if len(result) <= MAX_RESULT_BYTES:
                    record['result'] = result
            if sum(len(x) for x in (record['raw'], record['result']) if x is not None) + MAX_RESULT_BYTES + 65536 > MAX_RETAINED_BYTES:
                record['raw'] = record['result'] = None
                record['state'] = 'failed'
        record['expired'] = (self.clock() >= record['deadline']
                             or self.cache.clock() >= record['token'].target.expires)
        record['lease'].release()
        self.permit.release_optional(record, cleaned=True)
        if record['invalidated'] or record['expired']:
            try:self._retire(record)
            except Exception:record['purge_pending'] = True

    def _summary(self, record):
        # Fixed public vocabulary: no error strings, PIDs, paths, or proof bytes.
        return {'job_id':record['job_id'], 'session_id':record['session_id'],
            'chunk_id':record['chunk_id'], 'sequence':record['sequence'],
            'jobs_epoch':self.jobs_epoch, 'state':record['state'], 'profile':NAME,
            'support_id':record['support_id'], 'capture_epoch':record['capture_epoch'],
            'revision':record['revision'],
            'cleanup_pending':not record['cleaned'] or record['purge_pending'] or bool(record['lease'] and not record['lease'].released),
            'process_cleanup_verified':record['cleaned'],
            'memory_cleanup_pending':record['purge_pending'] or bool(record['lease'] and not record['lease'].released),
            'invocation_possible':record['invocation_possible'],
            'publication_cancelled':record['cancelled'].is_set(),
            'result_available':record['result'] is not None and not record['cancelled'].is_set(),
            'raw_available':record['raw'] is not None and not record['cancelled'].is_set(),
            'raw_unavailable_reason':record['raw_reason'], 'source_current':'not_checked',
            'retention':{'kind':'volatile', 'ttl_seconds':TTL_SECONDS, 'expired':record['expired']},
            'eos':None, 'complete_content':False}

    def _find(self, sid, jid):
        for record in self._records:
            if record['session_id'] == sid and record['job_id'] == jid:
                return record
        raise ReadError(404, 'job_not_found')

    def list(self, sid, *, after=0, limit=20, expected_jobs_epoch=None):
        self.cache.authorize_session(sid)
        with self._gate():
            if expected_jobs_epoch is not None:self._epoch(expected_jobs_epoch)
            self._expire()
            rows = [x for x in self._records if x['session_id'] == sid and x['sequence'] > after]
            selected = rows[:limit]
            body = encoded({'jobs_epoch':self.jobs_epoch, 'jobs':[self._summary(x) for x in selected],
                'next_cursor':selected[-1]['sequence'] if selected else after, 'has_more':len(rows) > limit})
            if len(body) > MAX_LIST_BYTES:raise ReadError(503, 'projection_capacity')
            return body

    def detail(self, sid, jid):
        self.cache.authorize_session(sid)
        with self._gate():
            self._expire()
            body = encoded(self._summary(self._find(sid, jid)))
            if len(body) > MAX_DETAIL_BYTES:raise ReadError(503, 'projection_capacity')
            return body

    def content(self, sid, jid, kind):
        self.cache.authorize_session(sid)
        with self._gate():
            self._expire()
            record = self._find(sid, jid)
            if record['expired'] or record['state'] in ('retired', 'expired'):
                raise JobError(410, 'job_retired', self._summary(record))
            if not record['cleaned']:raise ReadError(409, 'job_not_terminal')
            if record['cancelled'].is_set():raise ReadError(409, 'publication_invalidated')
            self.cache.revalidate_dispatch(record['token'])
            body = record[kind]
            if body is None:raise ReadError(409, 'raw_unavailable' if kind == 'raw' else 'result_unavailable')
            return body

    def cancel(self, sid, jid, epoch):
        self._epoch(epoch)
        self.cache.authorize_session(sid)
        with self._gate():
            self._expire()
            record = self._find(sid, jid)
            if record['state'] in ('retired', 'expired'):
                raise JobError(410, 'job_retired', self._summary(record))
            self._cancel_record(record)
            record['raw'] = record['result'] = None
            thread = record['thread']
            if record['state'] == 'cleanup_blocked' and (thread is None or not thread.is_alive()):
                retry = self._thread_factory(target=self._retry_cleanup, args=(record,), name='moss-cleanup', daemon=True)
                record['thread'] = retry
                try:retry.start()
                except Exception:record['thread'] = None
            return (200 if record['cleaned'] else 202), self._summary(record)

    def _retry_cleanup(self, record):
        try:
            if record['controller'] is not None and record['controller_id'] is not None:
                record['controller'].retry_cleanup(record['controller_id'])
        except Exception:
            pass
        finally:
            self._finish(record)

    def invalidate_session(self, sid):
        record = self._live
        if record is not None and record['session_id'] == sid:
            record['invalidated'] = True
            self._cancel_record(record)
            record['raw'] = record['result'] = None
            try:
                with self._gate():
                    if record is self._live and record['cleaned']:
                        self._retire(record)
            except Exception:
                record['purge_pending'] = True
            return (not record['cleaned'] or record['purge_pending']
                    or bool(record['lease'] and not record['lease'].released))
        return False

    def close_admission(self):
        self._closed = True
        self.permit.close_admission()
        if self._live is not None:self._cancel_record(self._live)

    def stop(self):
        self.close_admission()
        record = self._live
        if record is not None:
            thread = record['thread']
            if thread is not None and thread.is_alive():thread.join(timeout=15)
            if (thread is not None and thread.is_alive()) or not record['cleaned']:
                return False
            try:
                with self._gate():self._retire(record)
            except Exception:
                return False
        self.stopped = True
        return True

    def health(self):
        record = self._live
        return {'enabled':self._enabled(), 'jobs_epoch':self.jobs_epoch,
            'cleanup_pending':bool(record and (not record['cleaned'] or record['purge_pending'] or (record['lease'] and not record['lease'].released))),
            'accepted_jobs':len(self._records), 'epoch_capacity':MAX_ACCEPTED,
            'permit':self.permit.status()}


def notify_deleted(store, sid):
    from .native_owned_support_cache import lookup
    try:
        cache = lookup(store)
        owner = cache.jobs_owner if cache is not None else None
        return owner.invalidate_session(sid) if owner is not None else None
    except Exception:
        return True
