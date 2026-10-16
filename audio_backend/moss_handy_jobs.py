"""One-slot MOSS execution with immutable historical snapshots; no inference retry."""
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
from .moss_handy_profile import NAME, MODEL_SHA256, RUNTIME_SHA256
from .moss_result_store import MossResultStore, HistoryIdentity, HistorySnapshot, build_metadata
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
                or owner._source_expired(record)):
            record['cancelled'].set()
        if not record['lease'].released and record['lease'].cancelled.is_set():
            record['cancelled'].set()
        if record['origin'] == 'durable_cell':owner.cell_source.publication_current(record['token'])
        owner.permit.check_optional(record, phase)
        if phase in ('before_input', 'before_spawn', 'before_admission'):
            owner._validate(record)
        if record['cancelled'].is_set():
            raise AdmissionError('optional_cancelled')

    def inputs_written(self):
        self.record['lease'].release_pcm()

    def cleaning(self):
        if self.record['cleanup_deadline'] is None:
            self.record['cleanup_deadline'] = time.monotonic() + 60.
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
                 _driver_factory=OwnedDriver, _thread_factory=threading.Thread, cell_source=None, result_store=None):
        self.store, self.cache, self.permit, self.clock = store, cache, permit, clock
        self.jobs_epoch = uuid.uuid4().hex
        self.results = result_store if result_store is not None else MossResultStore(store)
        self.directory = create_namespace(store.settings.data_dir)
        self._driver_factory, self._thread_factory = _driver_factory, _thread_factory
        self._lock = threading.RLock()
        self._keys, self._records = {}, []
        self._live = None
        self._closed = False
        self.cell_source = cell_source
        self._pending_record = None
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

    def _source_expired(self, record):
        return (record['origin'] == 'rolling_support'
                and self.cache.clock() >= record['token'].target.expires)

    def _validate(self, record):
        if record['origin'] == 'durable_cell':
            return self.cell_source.revalidate_dispatch(record['token'])
        return self.cache.revalidate_dispatch(record['token'])

    def _drain_pending(self):
        # Manager -> adapter -> cell delivery/owner. The cell owner never calls
        # this manager or permit. Permit callbacks run outside its condition.
        record = self._pending_record
        if record is not None:
            if not self.cell_source.abandon(record['lease']):return False
            self.permit.release_optional(record, cleaned=True)
            thread = record['thread']
            if thread is not None and thread.is_alive():return False
            self._pending_record = None
        return self.cell_source is None or not self.cell_source.cleanup_pending

    def _require_cells(self):
        if not self._enabled() or self.cell_source is None:
            raise ReadError(409, 'feature_disabled')
        self.cell_source._current()

    def cells_list(self, sid, *, after=0, limit=20, expected_jobs_epoch=None):
        self.cache.authorize_session(sid)
        with self._gate():
            self._require_cells()
            if expected_jobs_epoch is not None:self._epoch(expected_jobs_epoch)
            if after and expected_jobs_epoch is None:raise ReadError(409, 'jobs_epoch_required')
            value = self.cell_source.list(sid, after, limit)
            body = encoded({'jobs_epoch': self.jobs_epoch, 'cells': value['cells'],
                            'next_cursor': value['next_cursor'], 'has_more': value.get('has_more', False)})
            if len(body) > 65536:raise ReadError(503, 'projection_capacity')
            return body

    def cell_detail(self, sid, cell_id):
        self.cache.authorize_session(sid)
        with self._gate():
            self._require_cells()
            value = self.cell_source.detail(sid, cell_id)
            return {'proof': value['detail'], 'sequence': value['sequence'], 'revision': value['revision'],
                'proof_sha256': value['proof_sha256'], 'state': value['state'],
                'pcm_retained': value['pcm_retained'], 'cell_id': cell_id, 'jobs_epoch': self.jobs_epoch}

    def _expire(self):
        record = self._live
        if record is None:
            return
        expired = (self.clock() >= record['deadline']
                   or self._source_expired(record))
        if not self._enabled() or expired or record['invalidated']:
            record['cancelled'].set()
            record['result'] = record['raw'] = None
            record['expired'] = expired
            if record['cleaned'] and not record['storage_pending']:
                self._retire(record)

    def _retire(self, record):
        if record['storage_pending']:raise ReadError(503, 'result_storage_pending')
        record['history_snapshot'] = None
        record['admission_metadata'] = None
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
        if record['origin'] == 'durable_cell':record['cell_binding'] = record['token'].projection()
        record['controller'] = record['lease'] = record['token'] = None
        record['thread'] = None
        record['purge_pending'] = False
        self._live = None

    def _cancel_record(self, record):
        record['cancelled'].set()
        if record is self._pending_record or (record.get('process_cleanup_verified')
                and record.get('lease') is not None and record['lease'].released):
            try:
                with self._gate():
                    self._drain_pending()
                    self._recover_acknowledged()
            except Exception:pass
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
        return self._submit('rolling_support', sid, cid, request, key, _admission_open)

    def submit_cell(self, sid, cell_id, request, key, *, _admission_open=None):
        return self._submit('durable_cell', sid, cell_id, request, key, _admission_open)

    def _submit(self, origin, sid, target, request, key, admission_open):
        self._epoch(request['jobs_epoch'])
        if request['profile'] != NAME:raise ReadError(409, 'profile_mismatch')
        self.cache.authorize_session(sid)
        digest = hashlib.sha256(encoded([origin, sid, target, request])).hexdigest()
        with self._gate():
            self._expire()
            previous = self._keys.get((sid, key))
            if previous is not None:
                if previous['digest'] != digest:raise ReadError(409, 'idempotency_conflict')
                if previous['state'] in ('expired', 'retired') or previous['expired']:
                    raise JobError(410, 'job_retired', self._summary(previous))
                return 200, self._summary(previous)
            recovered = self.results.lookup_key(sid, self.jobs_epoch, key, digest)
            if recovered is not None:return 200, recovered
            if not self._enabled():raise ReadError(409, 'feature_disabled')
            self.results.preflight(sid)
            if self._live is not None and self._live['storage_pending']:
                raise ReadError(503, 'result_storage_pending')
            if len(self._records) >= MAX_ACCEPTED:raise ReadError(429, 'epoch_capacity')
            if not self._drain_pending():raise ReadError(503, 'cell_acquisition_pending')
            if self._live is not None:
                previous_thread = self._live['thread']
                if not self._live['cleaned'] or (previous_thread is not None and previous_thread.is_alive()):
                    raise ReadError(409, 'job_slot_occupied')
            if origin == 'durable_cell':
                self._require_cells()
                lease = self.cell_source.acquire_dispatch(sid, target, request, admission_open=admission_open)
                capture_epoch, revision = self.cell_source.epoch, request['expected_cell_revision']
                cancelled = threading.Event()
            else:
                lease = self.cache.acquire_dispatch(sid, target,
                    expected_capture_epoch=request['expected_capture_epoch'],
                    expected_revision=request['expected_revision'], support_id=request['support_id'])
                capture_epoch, revision = request['expected_capture_epoch'], request['expected_revision']
                cancelled = lease.cancelled
            record = dict(job_id=uuid.uuid4().hex, session_id=sid, chunk_id=target, origin=origin,
                sequence=len(self._records) + 1, digest=digest, key=key, profile=NAME,
                support_id=request['support_id'], capture_epoch=capture_epoch,
                revision=revision, deadline=self.clock() + TTL_SECONDS,
                state='reserved', cleaned=False, process_cleanup_verified=False, cleanup_deadline=None,
                expired=False, invalidated=False, purge_pending=False, invocation_possible=False,
                cancelled=cancelled, lease=lease, token=lease.token, controller=None,
                controller_id=None, thread=None, result=None, raw=None, raw_reason='not_terminal',
                history_snapshot=None, storage_pending=False, durable_result_available=False,
                admission_metadata=None, accepted_at=time.time())
            record['history_identity'] = HistoryIdentity(record['job_id'], sid, self.jobs_epoch, key, digest)
            reserved = False
            try:
                self.permit.reserve_optional(record, cancelled, lambda:self._cancel_record(record))
                reserved = True
                self._validate(record)
                if self._live is not None:self._retire(self._live)
                candidate = [[x['session_id'], x['key'], x['digest'], x['job_id']] for x in self._records]
                candidate.append([sid, key, digest, record['job_id']])
                if len(encoded(candidate)) > MAX_LEDGER_BYTES:raise ReadError(429, 'epoch_capacity')
                if admission_open is not None and not admission_open():raise ReadError(503, 'response_closed')
                if cancelled.is_set():raise ReadError(409, 'optional_cancelled')
                record['admission_metadata'] = self._history_metadata(record)
                admitted = self.results.reserve(record['history_identity'], record['admission_metadata'])
                if admitted.get('admission_unknown'):raise JobError(409, 'admission_unknown', admitted)
                if origin == 'durable_cell':self.cell_source.accepted(lease)
                self._register_accepted_locked(record)
            except BaseException as exc:
                self._records[:] = [item for item in self._records if item is not record]
                if self._keys.get((sid, key)) is record:self._keys.pop((sid, key))
                if self._live is record:self._live = None
                if origin == 'durable_cell':
                    if self.cell_source.abandon(lease):
                        if reserved:self.permit.release_optional(record, cleaned=True)
                    elif reserved:
                        self._pending_record = record
                        self.permit.release_optional(record, cleaned=False)
                        self._start_pending_cleanup(record)
                else:
                    lease.release()
                    if reserved:self.permit.release_optional(record, cleaned=True)
                if isinstance(exc, AdmissionError):
                    raise ReadError(409 if exc.reason == 'primary_or_optional_busy' else 503, exc.reason) from None
                raise
            try:
                gate = _ServiceGate(self, record)
                source_binding = (lease.token.source_binding if origin == 'durable_cell' else lease.token.target.binding)
                controller = MossController(self.directory, profile_name=NAME,
                    model_path=self.store.settings.moss_jobs_model_path,
                    _driver_factory=self._driver_factory, _service_gate=gate,
                    _service_context={'jobs_epoch':self.jobs_epoch, 'capture_epoch':capture_epoch,
                                      'source_binding':source_binding})
                record['controller'] = controller
                record['controller_id'] = controller.submit(lease.support, lease.pcm)
                thread = self._thread_factory(target=self._run, args=(record,), name='moss-job', daemon=True)
                record['thread'] = thread
                thread.start()
            except BaseException:
                self._cancel_record(record)
                self._finish(record, preparation_failed=True)
                if not record['cleaned'] and record['process_cleanup_verified']:
                    self._start_memory_cleanup(record)
            return 202, self._summary(record)

    def _register_accepted_locked(self, record):
        self._records.append(record)
        self._keys[(record['session_id'], record['key'])] = record
        self._live = record

    def _run(self, record):
        record['state'] = 'running'
        try:
            record['controller'].run(record['controller_id'])
        except BaseException:
            self._cancel_record(record)
        finally:
            self._finish(record)
            self._await_memory_cleanup(record)

    def _start_pending_cleanup(self, record):
        # The same single service handoff, cleanup only; no accepted key or
        # controller exists. A failed thread start leaves the exact record held.
        record['cleanup_deadline'] = time.monotonic() + .5
        try:
            thread = self._thread_factory(target=self._pending_cleanup, args=(record,),
                name='moss-cleanup', daemon=True)
            record['thread'] = thread; thread.start()
        except Exception:pass  # Retain a possibly-started original thread reference.

    def _pending_cleanup(self, record):
        record['lease'].pin.release_done.wait(max(0., record['cleanup_deadline'] - time.monotonic()))
        try:
            with self._gate():
                if self._pending_record is record:self._drain_pending()
        except Exception:pass

    def _start_memory_cleanup(self, record):
        thread = record['thread']
        if thread is not None and thread.is_alive():return
        try:
            thread = self._thread_factory(target=self._await_memory_cleanup, args=(record,),
                name='moss-cleanup', daemon=True)
            record['thread'] = thread; thread.start()
        except Exception:pass  # Retain a possibly-started original thread reference.

    def _await_memory_cleanup(self, record):
        if (record['origin'] != 'durable_cell' or record['cleaned']
                or not record['process_cleanup_verified']):return
        # Share the original finite native cleanup phase, not another 60s.
        # On exhaustion the owner/permit remain blocked for explicit internal
        # health/stop or cancellation recovery; time never proves cleanup.
        if record['cleanup_deadline'] is None:record['cleanup_deadline'] = time.monotonic() + .5
        record['lease'].pin.release_done.wait(max(0., record['cleanup_deadline'] - time.monotonic()))
        if record['lease'].released:self._finish(record)

    def _recover_acknowledged(self):
        # No session authorization is required to finish cleanup after deletion.
        # Called only under the manager gate, with no permit condition held.
        record = self._live
        if record is None or record['cleaned'] or not record['process_cleanup_verified']:return
        thread = record['thread']
        if (thread is None or not thread.is_alive()) and record['lease'].released:
            self._finish_locked(record)

    def _finish(self, record, preparation_failed=False):
        with self._lock:
            try:
                self._finish_locked(record, preparation_failed)
            except BaseException:
                if record['history_snapshot'] is not None:
                    record['storage_pending'] = not record['durable_result_available']
                    return
                # A projection/source failure is never ownership release proof.
                record['state'] = 'cleanup_blocked'
                record['cleaned'] = False
                record['result'] = record['raw'] = None
                self.permit.release_optional(record, cleaned=False)

    def _retained_charge(self, record):
        material = sum(len(value) for value in (record['raw'], record['result'], record['admission_metadata']) if value is not None)
        snapshot = record['history_snapshot']
        if snapshot is not None:
            material += len(snapshot.metadata_json) + (0 if snapshot.result_json is record['result'] else len(snapshot.result_json))
        # Charge the original bounded controller projection, ledger and retained
        # tombstone bindings as well as the live proof/parsed-proof reservation.
        ledger = encoded([[r['session_id'], r['key'], r['digest'], r['job_id']] for r in self._records])
        bindings = sum(len(encoded(r['cell_binding'])) for r in self._records if 'cell_binding' in r)
        proof = (2 * len(record['token'].proof) + len(encoded(record['token'].projection()))
                 if record['origin'] == 'durable_cell' else 65536)
        return material + MAX_RESULT_BYTES + len(ledger) + bindings + proof

    def _finish_locked(self, record, preparation_failed=False):
        if record['history_snapshot'] is not None:return
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
        record['process_cleanup_verified'] = record['process_cleanup_verified'] or cleaned
        cleaned = record['process_cleanup_verified']
        record['cleaned'] = False
        record['invocation_possible'] = bool(status and status['invocation_possible'])
        if not cleaned:
            record['state'] = 'cleanup_blocked'
            record['raw'] = record['result'] = None
            self.permit.release_optional(record, cleaned=False)
            return
        valid = not record['cancelled'].is_set() and self._enabled() and self.clock() < record['deadline']
        if valid:
            try:
                self._validate(record)
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
                projection = status['result']
                if record['origin'] == 'durable_cell':
                    projection = {**projection, 'cell_binding':record['token'].projection()}
                result = encoded(projection)
                if len(result) <= MAX_RESULT_BYTES:
                    record['result'] = result
            if self._retained_charge(record) > MAX_RETAINED_BYTES:
                record['raw'] = record['result'] = None
                record['state'] = 'failed'
        record['expired'] = (self.clock() >= record['deadline']
                             or self._source_expired(record))
        # Serialization is unlocked with respect to source revocation. Recheck
        # identity and publication event before either byte projection is public.
        try:
            if record['origin'] == 'durable_cell':self.cell_source.publication_current(record['token'])
            if record['cancelled'].is_set() or not self._enabled() or self.clock() >= record['deadline']:
                raise ReadError(409, 'publication_invalidated')
        except Exception:
            record['raw'] = record['result'] = None
            record['cancelled'].set()
        released = record['lease'].release()
        if record['origin'] == 'durable_cell' and not record['lease'].released:
            record['state'] = 'cleanup_blocked'
            record['raw'] = record['result'] = None
            self.permit.release_optional(record, cleaned=False)
            return
        # Release may have waited for the owner. A revoke/cancel during that
        # unlocked interval must not make retained bytes publicly eligible.
        if record['origin'] == 'durable_cell':
            try:
                self.cell_source.publication_current(record['token'])
                if record['cancelled'].is_set() or self.clock() >= record['deadline']:
                    raise ReadError(409, 'publication_invalidated')
            except Exception:
                record['raw'] = record['result'] = None
                record['cancelled'].set()
        candidate = None
        if record['result'] is not None:
            try:
                metadata = self._history_metadata(record, status)
                candidate = HistorySnapshot(record['history_identity'], metadata, record['result'],
                                            hashlib.sha256(record['result']).hexdigest())
                if self._retained_charge(record) + len(metadata) > MAX_RETAINED_BYTES:
                    raise ReadError(503, 'result_metadata_capacity')
                self.results.validate_snapshot(candidate)
            except Exception:
                candidate = None
                record['result'] = None
        # This observation may call configuration code. The seal itself does not.
        try:record['configuration_observed'] = self._enabled()
        except Exception:
            record['configuration_observed'] = False
            candidate = None
            record['result'] = record['raw'] = None
            record['cancelled'].set()
        record['cleaned'] = True
        if candidate is not None:self._seal_history_locked(record, candidate)
        self.permit.release_optional(record, cleaned=True)
        if record['history_snapshot'] is not None:self._persist_locked(record)
        else:
            try:self.results.no_result(record['history_identity'])
            except Exception:pass  # Keep the charged reservation for bounded recovery.
        if (record['invalidated'] or record['expired']) and not record['storage_pending']:
            try:self._retire(record)
            except Exception:record['purge_pending'] = True

    def _history_metadata(self, record, status=None):
        identity = record['history_identity']
        context = {name:record[name] for name in ('origin', 'chunk_id', 'support_id', 'capture_epoch', 'revision', 'profile', 'accepted_at')}
        outcome, observed = {}, {}
        if status is not None:
            receipt = status.get('native_receipt') or {}
            outcome = {name:receipt.get(name) for name in ('construct_attempts', 'session_attempts', 'run_attempts',
                'truncated', 'aborted', 'model_closed', 'session_closed', 'raw_bytes', 'raw_sha256', 'raw_unavailable_reason')}
            outcome.update(state=record['state'], process_cleanup_verified=True, copied_pin_release_acknowledged=True,
                           eos=None, generated_token_count=None, complete_content=False)
            observed = {'model_sha256':MODEL_SHA256, 'runtime_sha256':RUNTIME_SHA256,
                        'profile_sha256':status['result']['profile_sha256']}
            context['sealed_at'] = time.time()
        return build_metadata(identity, context=context, support=record['lease'].support.to_dict(),
            cell_binding=record['token'].projection() if record['origin'] == 'durable_cell' else None,
            observed=observed, outcome=outcome)

    def _seal_history_locked(self, record, candidate):
        # S: completed validation observations, never a global source-current fence.
        # No SQL, source-owner, native or configuration callback belongs here.
        if (record['history_snapshot'] is not None or record['cancelled'].is_set()
                or self._closed or not self.store.settings.moss_jobs_enabled
                or not record['configuration_observed'] or self.clock() >= record['deadline']
                or not record['cleaned'] or not record['lease'].released
                or not record['process_cleanup_verified'] or record['invalidated'] or record['expired']):
            record['result'] = None
            return
        record['history_snapshot'] = candidate
        record['storage_pending'] = True

    def _persist_locked(self, record):
        snapshot = record['history_snapshot']
        if snapshot is None or record['durable_result_available']:return
        try:
            self.results.commit(snapshot)
        except Exception as exc:
            if isinstance(exc, ReadError) and exc.reason == 'session_not_found':
                record['history_snapshot'] = None
                record['result'] = record['raw'] = None
                record['storage_pending'] = False
                record['invalidated'] = True
            else:record['storage_pending'] = True
            return
        record['durable_result_available'] = True
        record['storage_pending'] = False

    def retry_storage(self, sid, jid, epoch):
        self._epoch(epoch)
        with self._gate():
            record = self._find(sid, jid)
            if self.results.is_deleted(sid):
                record['history_snapshot'] = None
                record['storage_pending'] = False
                raise ReadError(404, 'session_not_found')
            self._persist_locked(record)
            return (503 if record['storage_pending'] else 200), self._summary(record)

    def _summary(self, record):
        # Fixed public vocabulary: no error strings, PIDs, paths, or proof bytes.
        value = {'job_id':record['job_id'], 'session_id':record['session_id'],
            'chunk_id':record['chunk_id'], 'sequence':record['sequence'],
            'jobs_epoch':self.jobs_epoch, 'state':record['state'], 'profile':NAME,
            'support_id':record['support_id'], 'capture_epoch':record['capture_epoch'],
            'revision':record['revision'],
            'cleanup_pending':not record['cleaned'] or record['purge_pending'] or bool(record['lease'] and not record['lease'].released),
            'process_cleanup_verified':record['process_cleanup_verified'],
            'memory_cleanup_pending':record['purge_pending'] or bool(record['lease'] and not record['lease'].released),
            'invocation_possible':record['invocation_possible'],
            'publication_cancelled':record['cancelled'].is_set(),
            'result_available':record['durable_result_available'] and record['cleaned'] and record['result'] is not None and not record['cancelled'].is_set(),
            'raw_available':record['cleaned'] and record['raw'] is not None and not record['cancelled'].is_set(),
            'raw_unavailable_reason':record['raw_reason'], 'source_current':'not_checked',
            'durable_result_available':record['durable_result_available'],
            'storage_pending':record['storage_pending'],
            'reconciliation_remaining_work':self.results.recovery_remaining,
            'result_reconciliation':self.results.reconciliation_status(),
            'storage_reason':'result_storage_pending' if record['storage_pending'] else None,
            'structured_retention':'durable_until_explicit_session_deletion', 'raw_retention':'volatile',
            'retention':{'kind':'volatile', 'ttl_seconds':TTL_SECONDS, 'expired':record['expired']},
            'eos':None, 'complete_content':False}
        if record['origin'] == 'durable_cell':
            value['cell_binding'] = record['token'].projection() if record['token'] is not None else record['cell_binding']
        return value

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
            if kind == 'result' and record['storage_pending']:raise ReadError(409, 'result_storage_pending')
            if record['expired'] or record['state'] in ('retired', 'expired'):
                raise JobError(410, 'job_retired', self._summary(record))
            if not record['cleaned']:raise ReadError(409, 'job_not_terminal')
            if record['cancelled'].is_set():raise ReadError(409, 'publication_invalidated')
            self._validate(record)
            if kind == 'result' and not record['durable_result_available']:raise ReadError(409, 'result_unavailable')
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
                except Exception:pass  # Retain a possibly-started original thread reference.
            return (200 if record['cleaned'] else 202), self._summary(record)

    def _retry_cleanup(self, record):
        try:
            if (not record['process_cleanup_verified'] and record['controller'] is not None
                    and record['controller_id'] is not None):
                record['controller'].retry_cleanup(record['controller_id'])
        except Exception:
            pass
        finally:
            self._finish(record)
            self._await_memory_cleanup(record)

    def invalidate_session(self, sid):
        record = self._live
        if record is not None and record['session_id'] == sid:
            record['invalidated'] = True
            self._cancel_record(record)
            record['raw'] = record['result'] = None
            try:
                with self._gate():
                    if self.results.is_deleted(sid):
                        record['history_snapshot'] = None
                        record['storage_pending'] = record['durable_result_available'] = False
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
        if self.cell_source is not None:self.cell_source.close_admission()

    def stop(self):
        self.close_admission()
        if not self._drain_pending():return False
        record = self._live
        if record is not None:
            thread = record['thread']
            if thread is not None and thread.is_alive():thread.join(timeout=15)
            if thread is not None and thread.is_alive():return False
            if not record['cleaned'] and record['process_cleanup_verified']:
                self._finish(record)
            if not record['cleaned']:return False
            try:
                with self._gate():
                    if record['storage_pending']:self._persist_locked(record)
                    if record['storage_pending']:return False
                    self._retire(record)
            except Exception:
                return False
        self.stopped = True
        return True

    def health(self):
        try:
            with self._gate():
                self._drain_pending()
                self._recover_acknowledged()
        except Exception:pass
        record = self._live
        return {'enabled':self._enabled(), 'jobs_epoch':self.jobs_epoch,
            'cleanup_pending':bool(self._pending_record) or bool(self.cell_source and self.cell_source.cleanup_pending) or bool(record and (not record['cleaned'] or record['purge_pending'] or (record['lease'] and not record['lease'].released))),
            'storage_pending':bool(record and record['storage_pending']),
            'result_reconciliation':self.results.reconciliation_status(),
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
