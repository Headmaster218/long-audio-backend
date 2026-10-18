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

from .moss_handy_controller import MossController, OwnedDriver, failure_observation, terminal_outcome
from .moss_handy_profile import NAME, MODEL_SHA256, RUNTIME_SHA256
from .moss_result_store import MossResultStore, HistoryIdentity, HistorySnapshot, build_metadata, validate_metadata
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

    @property
    def _automatic_text(self):
        return self.record.get('automatic_key') is not None

    @property
    def _automatic_wait_remaining(self):
        return self.record['deadline'] - self.owner.clock()

    @property
    def _automatic_cancelled(self):
        return self.record['cancelled'].is_set() or self.record['lease'].cancelled.is_set()

    def check(self, phase, *, _driver_deadline=None, _driver_cancelled=None):
        owner, record = self.owner, self.record
        if (owner.clock() >= record['deadline']
                or owner._source_expired(record)):
            record['cancelled'].set()
        if not record['lease'].released and record['lease'].cancelled.is_set():
            record['cancelled'].set()
        if record['origin'] == 'durable_cell':owner.cell_source.publication_current(record['token'])
        if phase == 'running' and self._automatic_text and _driver_deadline is not None:
            def remaining():
                job_left = record['deadline'] - owner.clock()
                driver_left = _driver_deadline - time.monotonic()
                if job_left <= 0:
                    record['cancelled'].set()
                    error = AdmissionError('optional_cancelled')
                    error._optional_execution_deadline = True
                    raise error
                if driver_left <= 0:
                    from .fragment_model_guard import ResourceLimit
                    error = ResourceLimit('Candidate job deadline')
                    error._optional_execution_deadline = True
                    raise error
                return min(job_left, driver_left)
            def cancelled():
                return (self._automatic_cancelled
                    or _driver_cancelled is not None and _driver_cancelled.is_set())
            owner.permit.check_optional(record, phase,
                running_remaining=remaining, running_cancelled=cancelled)
            # The bounded wait/sample may outlast a source generation change.
            # This existing in-memory guard runs after the permit lock is released.
            if record['origin'] == 'durable_cell':owner.cell_source.publication_current(record['token'])
            remaining()
            if cancelled():
                record['cancelled'].set()
                raise AdmissionError('optional_cancelled')
        else:owner.permit.check_optional(record, phase)
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


class _SpeakerProbeGate:
    def __init__(self, owner, probe):
        self.owner, self.probe = owner, probe

    def check(self, phase):
        if not self.owner._links_enabled():self.probe['cancelled'].set()
        self.owner.permit.check_optional(self.probe, phase)
        if self.probe['cancelled'].is_set():raise AdmissionError('optional_cancelled')

    def cleaning(self):self.owner.permit.cleaning(self.probe)

    def cleanup_sample(self):
        try:self.owner.permit._resource_check(reserve=False)
        except Exception:self.probe['cancelled'].set()


class MossJobs:
    def __init__(self, store, cache, permit, *, clock=time.monotonic,
                 _driver_factory=OwnedDriver, _thread_factory=threading.Thread, cell_source=None, result_store=None,
                 speaker_links=None, _probe_factory=None):
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
        self.automatic = None
        self.speaker_links = speaker_links
        if speaker_links is not None and cell_source is not None:
            speaker_links.bind_primary_source(cell_source)
        self._probe_factory = _probe_factory
        self._probe_staging_bytes = 0
        self._probe_reconcile_after = 0.
        self._probe_reconcile_active = False
        self._pending_record = None
        self.stopped = False
        self._primary_pending = None
        self._primary_admission = None
        self._moss_admission = None
        self._history_comparison = None
        self._history_maintenance = None
        self._history_comparison_pending = False
        self._history_compare_after = 0.
        self.history = getattr(speaker_links,'history',None)
        self._history_success_capability = (self.history.bind_success_owner(self)
            if self.history is not None else None)
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
            self.cell_source.revalidate_dispatch(record['token'])
            if record.get('automatic_key') is not None:
                if self.automatic is None:raise ReadError(409,'automatic_configuration_unavailable')
                self.automatic.validate_record(record)
            return True
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
        probe = record.get('speaker_probe')
        if probe is not None and probe.get('commit_pending'):
            raise ReadError(503, 'speaker_probe_storage_pending')
        if probe is not None and (probe.get('operation') is not None and not probe['operation'].cleaned
                or record['thread'] is not None and record['thread'].is_alive()):
            raise ReadError(503, 'speaker_probe_cleanup_pending')
        if record['storage_pending']:raise ReadError(503, 'result_storage_pending')
        record['history_snapshot'] = None
        record['terminal_metadata'] = None
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
        probe = record.get('speaker_probe')
        if probe is not None:probe['cancelled'].set()
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

    def _history_enabled(self):
        return self.history is not None and self._links_enabled()

    def _history_comparison_enabled(self):
        # Passive saved-history reads never grant recomputation authority.
        return self._history_enabled() and self.history.enabled

    @contextmanager
    def _primary_guard(self, probe):
        owner = self.cell_source.owner
        if not owner._publication_guard.acquire(timeout=LOCK_SECONDS):
            raise ReadError(503,'primary_probe_publication_busy')
        try:
            from .recording_primary_probe import _check_fence
            _check_fence(self.cell_source, probe['fence'])
            if probe['cancelled'].is_set() or not self._history_enabled():
                raise ReadError(409,'primary_probe_cancelled')
            yield
            _check_fence(self.cell_source, probe['fence'])
        finally:owner._publication_guard.release()

    def prepare_primary_copy(self, capture, chunk, offset, end, draft, owned_pcm):
        """One optional live copy; this method never writes the primary DB."""
        if not self._history_enabled() or self.cell_source is None:return None
        from .native_owned_cell_store import Budget
        from .native_owned_cells import exact_frame
        from .recording_primary_probe import _fence, copy_standalone_primary
        from .recording_speaker_probe_runner import expected_space, STAGING_BYTES
        if capture.owner is not self.cell_source.owner or capture.authority.store is not self.store:return None
        with Budget().phase() as budget, capture.authority.db(budget) as db:
            db.execute('BEGIN')
            row = db.execute('''SELECT w.batch_id FROM recording_work w
                JOIN recording_batches b ON b.id=w.batch_id
                WHERE w.chunk_id=? AND b.session_id=?''', (chunk['id'],chunk['session_id'])).fetchone()
            budget.projection(256)
            if row is None:return None
            rid = row[0]
        space, space_id = expected_space()
        frame = capture.origin_frame + exact_frame(offset)
        handle = {'owner':self, 'session_id':chunk['session_id'], 'recording_id':rid,
            'source_start_frame':frame, 'chunk_id':chunk['id'], 'start':exact_frame(offset),
            'end':exact_frame(end), 'space':space, 'space_id':space_id,
            'state':'skip', 'reason':'missed', 'cancelled':threading.Event(),
            'crop':None, 'charge_bytes':0, 'thread':None, 'operation':None,
            'reservation':None, 'admitted':False, 'completed':threading.Event(),
            'validated_output':None, 'terminal_reason':None, 'thread_start_attempted':False,
            'invocation_id':uuid.uuid4().hex, 'failure_json':None, 'failure_frozen':False}
        due = self.history.primary_due(chunk['session_id'],rid,frame)
        if not due['due']:
            handle['reason'] = due['reason']; return handle
        handle['reason'] = 'insufficient_clean_support'
        if draft is None:return handle
        with self._gate():
            if self._primary_pending is not None or self._probe_staging_bytes:
                handle['reason'] = 'busy'; return handle
            self._primary_pending = handle
            self._probe_staging_bytes = handle['charge_bytes'] = STAGING_BYTES
            handle['state'] = 'provisional'
        try:
            handle['fence'] = _fence(self.cell_source)
            receipt = capture.owner.receipt(chunk['id'])
            if receipt is None:raise ReadError(409,'primary_probe_source_stale')
            handle['receipt_bytes'] = receipt.encoded
            copied = copy_standalone_primary(draft,owned_pcm,space_id)
            if copied is None:
                self._drop_primary_copy(handle); handle['state'] = 'skip'; return handle
            handle['draft_bytes'], handle['node'], handle['crop'] = copied
            if (len(handle['draft_bytes']) + len(encoded(handle['node'])) + len(handle['crop'])
                    + len(handle['receipt_bytes']) + 65536 > STAGING_BYTES):
                raise ReadError(429,'primary_probe_staging_capacity')
            return handle
        except BaseException:
            self._drop_primary_copy(handle)
            handle['state'], handle['reason'] = 'skip', 'unavailable'
            return handle

    def abort_primary_copy(self, handle):
        if type(handle) is dict and handle.get('owner') is self and handle.get('state') == 'provisional':
            handle['cancelled'].set()
            self._drop_primary_copy(handle)

    def _drop_primary_copy(self, probe):
        with self._lock:
            probe['crop'] = None
            if probe.get('charge_bytes'):
                self._probe_staging_bytes -= probe['charge_bytes']; probe['charge_bytes'] = 0
            if self._primary_pending is probe:self._primary_pending = None
            if probe.get('state') == 'terminal':
                token = (probe['reservation']['token'] if probe.get('reservation') is not None
                         else probe.get('attempt_id'))
                if token is not None:self.history.release_attempt(token)

    def _primary_current(self, probe, db):
        from .recording_primary_probe import standalone_current
        if probe['cancelled'].is_set():return False
        saved = {'header':probe['saved_header'],'packet':json.loads(probe['saved_packet_bytes'])}
        return standalone_current(db,saved,self.cell_source,probe['fence'],
                                  probe['receipt_bytes'],admitted=probe['admitted'])

    def _primary_admission_matches(self, attempt_id, saved, node, invocation_id):
        """Only the live, positively acknowledged Worker handoff may add basis."""
        from .native_owned_cells import sha
        admission = self._primary_admission
        if admission is None:return False
        probe, expected_attempt, expected_invocation, saved_sha, node_sha = admission
        if (probe is not self._primary_pending or probe.get('owner') is not self
                or probe.get('state') != 'claim_unknown' or probe['cancelled'].is_set()
                or probe.get('admitted') or probe.get('operation') is not None
                or attempt_id != expected_attempt or invocation_id != expected_invocation
                or probe.get('attempt_id') != attempt_id or probe.get('invocation_id') != invocation_id
                or probe.get('saved_header') != saved.get('header')
                or probe.get('saved_packet_bytes') != encoded(saved.get('packet'))
                or probe.get('node') != node):return False
        return hashlib.sha256(encoded(saved)).hexdigest() == saved_sha and sha(node) == node_sha

    def primary_committed(self, handle):
        """Called solely by Worker after the exact commit_window returned True."""
        if type(handle) is not dict or handle.get('owner') is not self:return False
        if handle['state'] == 'skip':
            handle['state'] = 'terminal'
            self.history.note_primary_skip(handle['session_id'],handle['recording_id'],
                handle['source_start_frame'],handle['reason'],handle['space'])
            return False
        if handle is not self._primary_pending or handle['state'] != 'provisional':return False
        from . import recording_primary_provenance as provenance
        from .recording_primary_probe import bind_standalone_primary, standalone_staging_bound
        from .native_owned_cell_store import Budget
        from .native_owned_cells import sha
        try:
            with self._primary_guard(handle):
                with Budget().phase() as budget, self.cell_source.owner.authority.db(budget) as db:
                    db.execute('BEGIN')
                    saved = provenance.read_exact(db,cid=handle['chunk_id'],start=handle['start'],end=handle['end'])
                    if saved is None or saved['header']['rid'] != handle['recording_id']:
                        raise ReadError(409,'primary_probe_packet_missing')
                    budget.projection(len(provenance.bounded(saved['packet'])))
                handle['saved_header'] = dict(saved['header'])
                handle['saved_packet_bytes'] = provenance.bounded(saved['packet'])
                handle['node'] = bind_standalone_primary(saved,handle['draft_bytes'],handle['node'])
                standalone_staging_bound(saved,handle['node'],
                    wire_bytes=len(handle['saved_packet_bytes']),receipt_bytes=len(handle['receipt_bytes']))
                handle['identity'] = {'sid':handle['session_id'],'rid':handle['recording_id'],
                    'origin_kind':'primary_commit','origin_id':saved['header']['digest'],
                    'node_sha256':sha(handle['node']),'space_id':handle['space_id']}
                handle['attempt_id'] = sha(handle['identity'])[:32]
                # An exception now is an unknown claim acknowledgement. Freeze
                # exact identity, discard audio, and allow storage closure only.
                handle['state'] = 'claim_unknown'
                self._primary_admission = (handle,handle['attempt_id'],handle['invocation_id'],
                                           hashlib.sha256(encoded(saved)).hexdigest(),sha(handle['node']))
                try:
                    reservation = self.history.claim_primary(handle['session_id'],handle['recording_id'],
                        handle['attempt_id'],saved,handle['node'],handle['receipt_bytes'],
                        current_check=lambda db:self._primary_current(handle,db),space=handle['space'],
                        admission_capability=self._history_success_capability,
                        invocation_id=handle['invocation_id'])
                finally:self._primary_admission = None
                handle['reservation'] = reservation
                if not reservation.get('should_probe'):
                    handle['crop'] = None
                    if reservation.get('state') == 'reserved':handle['state'] = 'claim_unknown'
                    else:
                        handle['state'] = 'terminal';self._drop_primary_copy(handle)
                    return False
                handle['admitted'] = True
                handle.pop('draft_bytes',None)
                handle['state'] = 'pending'
                return True
        except BaseException as exc:
            if handle['state'] == 'claim_unknown':handle['crop'] = None
            elif handle['state'] == 'pending' and handle['admitted']:
                # The claim returned positively before the enclosing source
                # fence changed. Keep its owned basis for metadata closure.
                handle['crop'] = None
                self._observe_probe_failure(handle,'dispatch_validation','source_authority_changed',exc)
                handle['terminal_reason'] = 'cancelled' if handle['cancelled'].is_set() else 'unavailable'
                handle['state'] = 'finish_pending'
            else:self._drop_primary_copy(handle)
            return False

    def primary_ready_hint(self):
        with self._gate():
            probe = self._primary_pending
            return bool(probe and probe['state'] == 'pending' and probe['admitted']
                and not probe['cancelled'].is_set() and probe['crop'] is not None)

    def primary_maintenance_hint(self):
        with self._gate():
            probe = self._primary_pending
            return bool(probe and probe['state'] in ('claim_unknown','append_unknown','finish_pending','terminal')
                        and (probe['thread'] is None or not probe['thread'].is_alive()))

    def _primary_executing(self):
        probe = self._primary_pending
        return bool(probe and (probe['state'] == 'thread_unknown' or probe['thread'] is not None and probe['thread'].is_alive()
            or probe['operation'] is not None and not probe['operation'].cleaned))

    def submit_primary(self):
        """Synchronous permit reservation inside Worker's existing automatic turn."""
        probe = self._primary_pending
        if probe is None or not self.primary_ready_hint():return False
        reserved = False
        try:
            if not self.automatic_idle():return False
            with self._gate(), self._primary_guard(probe):
                if probe is not self._primary_pending or probe['state'] != 'pending':return False
                if self._live is not None:self._retire(self._live)
                if not self.history.dispatch_current(probe['reservation'],
                        current_check=lambda db:self._primary_current(probe,db)):
                    raise ReadError(409,'primary_probe_source_stale')
                self.permit.reserve_optional(probe,probe['cancelled'],probe['cancelled'].set,
                                             owner_kind='automatic_primary_probe')
                reserved = True
                probe['state'] = 'running'
                thread = self._thread_factory(target=self._run_primary,args=(probe,),
                                              name='primary-speaker-probe',daemon=True)
                probe['thread'] = thread
                probe['thread_start_attempted'] = True
                thread.start()
            return True
        except BaseException as exc:
            if probe['thread_start_attempted'] and not probe['completed'].is_set():
                probe['cancelled'].set(); probe['state'] = 'thread_unknown'
                if reserved:self.permit.release_optional(probe,cleaned=False)
                return False
            if reserved:self.permit.release_optional(probe,cleaned=True)
            self._observe_probe_failure(probe,'permit',
                'optional_cancelled' if probe['cancelled'].is_set() else 'permit_unavailable',exc)
            probe['terminal_reason'] = probe['terminal_reason'] or 'unavailable'
            probe['state'] = 'finish_pending'
            self._maintain_primary()
            return False

    def _run_primary(self, probe):
        from .recording_speaker_probe_runner import ProbeOperation, ProbeDriver, KIND
        failure_stage, failure_reason = 'dispatch_validation', 'source_authority_changed'
        try:
            with self._primary_guard(probe):
                if not self.history.dispatch_current(probe['reservation'],
                        current_check=lambda db:self._primary_current(probe,db)):
                    raise ReadError(409,'primary_probe_source_stale')
            request = {'kind':KIND,'job_id':probe['invocation_id'],'space_id':probe['space_id'],
                'model_path':self.store.settings.recording_speaker_model_path,
                'crops':[{'index':0,'pcm_sha256':probe['node']['pcm16le_sha256'],
                          'float32_sha256':probe['node']['float32le_sha256']}]}
            failure_stage, failure_reason = 'child_start', 'other'
            operation = ProbeOperation(self.directory,request,(probe['crop'],),
                gate=_SpeakerProbeGate(self,probe),driver_factory=self._probe_factory or ProbeDriver)
            probe['operation'] = operation
            output = operation.run(probe['cancelled'])
            if not operation.cleaned or probe['cancelled'].is_set():
                raise ReadError(409,'primary_probe_cleanup_pending')
            # Freeze a detached validated result before the first durable submit.
            probe['validated_output'] = json.loads(encoded(output))
            probe['crop'] = None
            with self._gate(), self._primary_guard(probe):
                probe['state'] = 'append_unknown'
                self.history.append_primary(probe['reservation'],probe['node'],
                    probe['validated_output']['nodes'][0]['vector'],probe['invocation_id'],
                    success_capability=self._history_success_capability,
                    current_check=lambda db:self._primary_current(probe,db))
                probe['state'] = 'terminal'
        except BaseException as exc:
            if probe['state'] not in ('append_unknown','terminal'):
                operation = probe['operation']
                if (operation is not None and operation.failure_json is not None
                        and probe['failure_json'] is None and not probe['failure_frozen']):
                    probe['failure_json'] = operation.failure_json
                self._observe_probe_failure(probe,failure_stage,
                    'optional_cancelled' if probe['cancelled'].is_set() else failure_reason,exc)
                probe['terminal_reason'] = probe['terminal_reason'] or ('cancelled'
                    if probe['cancelled'].is_set() else 'unavailable')
                probe['state'] = 'finish_pending'
        finally:
            operation = probe['operation']
            clean = operation is None or operation.cleaned
            self.permit.release_optional(probe,cleaned=clean)
            if not clean:probe['state'] = 'cleanup_unknown'
            if probe['state'] == 'terminal':self._kick_history_comparison()
            probe['completed'].set()

    def _primary_success_matches(self, reservation, node, vector, invocation_id):
        from .recording_speaker_probe_runner import ProbeOperation, cleanup_verified
        probe = self._primary_pending
        if (probe is None or probe['reservation'] != reservation or probe['node'] != node
                or probe.get('invocation_id') != invocation_id or not probe['admitted']
                or probe['cancelled'].is_set()):return False
        operation = probe['operation']; output = probe['validated_output']
        if (type(operation) is not ProbeOperation or not operation.started or not operation.cleaned
                or operation.child_dispatch != 'returned_validated' or not cleanup_verified(operation.receipt)
                or output is None or output['nodes'][0]['vector'] != vector
                or operation.request['job_id'] != invocation_id
                or operation.request['space_id'] != node['space_id']
                or operation.request['crops'] != [{'index':0,'pcm_sha256':node['pcm16le_sha256'],
                                                 'float32_sha256':node['float32le_sha256']}]):return False
        operation._validate_return(output)
        return True

    def _maintain_primary(self):
        probe = self._primary_pending
        if probe is None:return True
        thread = probe['thread']
        if thread is not None and thread.is_alive():return False
        if probe['thread_start_attempted'] and not probe['completed'].is_set():return False
        if thread is not None:thread.join(timeout=0)
        operation = probe['operation']
        if operation is not None and not operation.cleaned:return False
        try:
            state = probe['state']
            if state in ('claim_unknown','append_unknown'):
                outcome = self.history.resolve_primary(probe['attempt_id'],probe['identity'],
                    failure_json=probe['failure_json'] if probe['failure_frozen'] else None)
                if not outcome.get('resolved'):return False
                probe['state'] = 'terminal'
                self._kick_history_comparison()
            elif state == 'finish_pending':
                self._freeze_probe_failure(probe,'probe_interrupted'
                    if probe['cancelled'].is_set() else 'probe_unavailable')
                self.history.finish_primary(probe['reservation'],probe['terminal_reason'],
                    failure_json=probe['failure_json'])
                probe['state'] = 'terminal'
                self._kick_history_comparison()
            if probe['state'] == 'terminal':self._drop_primary_copy(probe)
        except BaseException:return False
        return self._primary_pending is None

    def _stop_primary(self, sid=None):
        probe = self._primary_pending
        if probe is None or sid is not None and probe['session_id'] != sid:return True
        probe['cancelled'].set()
        thread = probe['thread']
        if thread is not None and thread.is_alive():thread.join(timeout=5.)
        if thread is not None and thread.is_alive():return False
        if probe['thread_start_attempted'] and not probe['completed'].is_set():return False
        operation = probe['operation']
        if operation is not None and not operation.cleaned:
            try:clean = operation.cleanup()
            except BaseException:clean = False
            self.permit.release_optional(probe,cleaned=clean)
            if not clean:return False
        if probe['state'] == 'provisional':self._drop_primary_copy(probe);return True
        if probe['state'] not in ('append_unknown','claim_unknown','terminal'):
            probe['terminal_reason'] = probe['terminal_reason'] or 'cancelled'
            probe['state'] = 'finish_pending'
        return self._maintain_primary()

    def _history_source_quiescent(self, source_version):
        with self._gate(), self.permit._condition:
            if (self._closed or self.permit._primary or self.permit._optional is not None
                    or self._pending_record is not None or self._primary_pending is not None):return False
            record = self._live
            return record is None or (record['cleaned'] and not record['storage_pending']
                and not self._probe_unresolved(record) and not record.get('speaker_probe',{}).get('commit_pending')
                and (record['thread'] is None or not record['thread'].is_alive()))

    def _kick_history_comparison(self):
        # A durable admission/closure marks work, never computes in its caller.
        self._history_comparison_pending = True

    def maintain_history_comparison(self):
        """One task/cache: no reservation is released while bytes remain owned."""
        try:
            with self._gate():
                if self._history_maintenance is not None:return False
                claim = self._history_maintenance = object()
        except ReadError:return False
        try:return self._maintain_history_comparison_owned()
        finally:
            with self._lock:
                if self._history_maintenance is claim:self._history_maintenance = None

    def _maintain_history_comparison_owned(self):
        # The short manager claim excludes a second cleanup/restart caller.
        # Its lifetime carries no lock through cleanup, census, or thread join.
        record = self._history_comparison
        if record is not None:
            thread = record['thread']
            if not record['completed'].is_set() or thread is not None and thread.is_alive():return False
            if thread is not None:thread.join(timeout=0)
            if record.get('finish_pending') is not None:
                try:self.history.finish_comparison(record['snapshot'],record['finish_pending'])
                except BaseException:return False
                record['finish_pending'] = None
            driver = record.get('driver')
            self.permit.pause_comparison(record['grant'])
            if (record['cancelled'].is_set() or not self._history_comparison_enabled()
                    or driver is None or driver.cache_binding is None):
                cancelled = record['cancelled'].is_set()
                if not self._close_history_comparison_driver(record):return False
                if not self.permit.release_comparison(record['grant']):return False
                with self._lock:
                    if self._history_comparison is record:self._history_comparison = None
                record = None
                if cancelled:return True  # This claim may close, never replace, the stopped owner.
            else:record['state'] = 'cached'
        if not self._history_comparison_enabled():return True
        now = time.monotonic()
        if not self._history_comparison_pending and now < self._history_compare_after:return True
        self._history_compare_after = now + 1.
        fresh = record is None
        if fresh:
            record = {'grant':object(),'generation':object(),'cancelled':threading.Event(),
                'completed':threading.Event(),'thread':None,'driver':None,'snapshot':None,
                'state':'starting','deadline':now + 180.,'session_id':None,'start_attempted':False,
                'task_sequence':0,'driver_active':False,'finish_pending':None}
        try:
            with self._gate():
                if fresh:
                    if self._history_comparison is not None:return False
                    self._history_comparison = record
                elif self._history_comparison is not record:return False
                # Publish the exact pending owner before census. Other callers
                # see an incomplete task, but primary never waits on census.
                record.update(completed=threading.Event(),state='sampling',start_attempted=False,
                              generation=object(),deadline=now + 180.)
                generation = record['generation']
            if fresh:
                self.permit.reserve_comparison(record['grant'],record['cancelled'],memory_bytes=192 * 1024**2)
            elif not self.permit.resume_comparison(record['grant']):
                raise AdmissionError('comparison_pending')
            with self._gate():
                if (self._history_comparison is not record or record['generation'] is not generation
                        or record['cancelled'].is_set() or not self._history_comparison_enabled()
                        or not self.permit.comparison_grant_valid(record['grant'])):
                    raise AdmissionError('comparison_pending')
                completed = threading.Event()
                thread = self._thread_factory(target=self._run_history_comparison,args=(record,),
                                              name='recording-history-compare',daemon=True)
                self.permit.bind_comparison_thread(record['grant'],thread,completed)
                record.update(thread=thread,completed=completed,state='starting',
                              start_attempted=True,finish_pending=None,execution_lane=None,reason=None)
                thread.start()
                self._history_comparison_pending = False
            return True
        except BaseException:
            if record['start_attempted'] and not record['completed'].is_set():
                record['cancelled'].set(); record['state'] = 'ownership_unknown';return False
            self.permit.pause_comparison(record['grant'])
            record['state'] = 'pending';record['completed'].set()
            if fresh:
                if self.permit.release_comparison(record['grant']):
                    with self._lock:
                        if self._history_comparison is record:self._history_comparison = None
            return False

    def _history_comparison_driver(self, record, token):
        """Start only the separately accounted pure-compute owner.

        This coordinator keeps DB and authority ownership. Neither it nor a
        primary/model thread is reniced or moved by child setup. The child may
        keep one prefix after a positively acknowledged task closure; no score
        matrix or cross-snapshot authority cache is retained in this process.
        """
        from .recording_speaker_history_process import ComparisonProcess
        driver = record['driver']
        if driver is None:
            driver = ComparisonProcess(owner_nonce=uuid.uuid4().hex,
                cancelled=record['cancelled'].is_set,
                grant_valid=lambda:self.permit.comparison_grant_valid(record['grant']),
                bind_child=lambda proc:self.permit.bind_comparison_process(
                    record['grant'],proc,owner=record['driver']),
                observe_child=lambda proc, startup=False, deadline=None:self.permit.observe_comparison_process(
                    record['grant'],proc,startup=startup,deadline=deadline),
                finish_child=lambda proc, deadline=None:self.permit.finish_comparison_process(
                    record['grant'],proc,deadline=deadline))
            # Retain the original owner before any spawn/bind can fail.
            record['driver'] = driver
            self.permit.bind_comparison_owner(record['grant'],driver)
            driver.start(record['deadline'])
        if record['driver_active']:
            driver.rebase(token)  # Same task, prefix, and original deadline.
        else:
            record['task_sequence'] += 1
            driver.begin(token,generation=record['task_sequence'],deadline=record['deadline'])
            record['driver_active'] = True
        return driver

    def _close_history_comparison_driver(self, record):
        driver = record['driver']
        if driver is None:return True
        try:
            receipt = driver.close()
            if type(receipt) is dict and receipt.get('positive') is True:
                record['driver_active'] = False
                return True
        except BaseException:
            pass
        record['state'] = 'ownership_unknown'
        record['reason'] = 'comparison_cleanup_unknown'
        return False

    def _comparison_checkpoint(self, record, snapshot=None):
        while not record['cancelled'].is_set() and time.monotonic() < record['deadline']:
            if not self._history_comparison_enabled():
                record['cancelled'].set();record['reason'] = 'comparison_configuration_changed'
                record['state'] = 'cancelled';return False
            granted = self.permit.comparison_grant_valid(record['grant'])
            if not granted:
                record['state'] = 'paused'
                try:granted = self.permit.resume_comparison(record['grant'])
                except Exception:pass
            if granted:
                if snapshot is None:return True
                # Each bounded scalar read is positively closed before compute.
                current = self.history.comparison_current(snapshot)
                if not self._history_comparison_enabled():
                    record['cancelled'].set();record['reason'] = 'comparison_configuration_changed'
                    record['state'] = 'cancelled';return False
                if current == 'stale':raise ReadError(409,'history_snapshot_changed')
                if current == 'invalid':
                    record['cancelled'].set();record['state'] = 'cancelled'
                    record['reason'] = 'history_authority_changed';return False
                if (current == 'current' and not record['cancelled'].is_set()
                        and time.monotonic() < record['deadline']
                        and self.permit.comparison_grant_valid(record['grant'])):
                    record['reason'] = None
                    return True
                record['state'] = 'pending'
                record['reason'] = 'history_currentness_pending'
            record['cancelled'].wait(min(.05,max(0.,record['deadline']-time.monotonic())))
        return False

    def _compare_history_snapshot(self, record, snapshot):
        from .recording_speaker_history_compare import Pending
        from .recording_speaker_history_process import ProcessPending
        token = snapshot['token']
        cursor = 0
        while True:
            if not self._comparison_checkpoint(record,snapshot):return None
            page = self.history.active_source_page(snapshot,after=cursor)
            if not self._comparison_checkpoint(record,snapshot):return None
            self.history.revalidate_active_sources(snapshot,page,
                checkpoint=lambda:self._comparison_checkpoint(record,snapshot))
            if page['complete']:break
            cursor = page['next_after']
        # No source read or database connection remains open across this edge.
        driver = self._history_comparison_driver(record,token)
        for reader, feeder in ((self.history.observation_page,driver.feed_observations),
                               (self.history.witness_page,driver.feed_witnesses)):
            cursor = 0
            while True:
                if not self._comparison_checkpoint(record,snapshot):return None
                page = reader(snapshot,after=cursor)
                if not self._comparison_checkpoint(record,snapshot):return None
                feeder(token,cursor,page['rows'],complete=page['complete'])
                if page['complete']:break
                cursor = page['next_after']
        page = None  # Drop the last parent projection before child compute.
        while True:
            if not self._comparison_checkpoint(record,snapshot):return None
            try:result = driver.step()
            except ProcessPending as exc:
                if str(exc) == 'comparison_grant_paused':continue
                raise
            # Even a negative terminal result belongs only to this exact
            # revision. A late alias/rival cannot inherit the old refusal.
            if not self._comparison_checkpoint(record,snapshot):return None
            record['state'] = result['state']
            if result['state'] == 'ready':break
            if result['state'] in ('refused','cancelled'):return result
        cursor = 0
        while True:
            if not self._comparison_checkpoint(record,snapshot):return None
            try:page = driver.output_page(after=cursor,limit=16)
            except (Pending,ProcessPending) as exc:
                if str(exc) == 'comparison_grant_paused' and driver.state == 'ready':continue
                raise
            if not self._comparison_checkpoint(record,snapshot):return None
            self.history.write_decision_page(snapshot,page)
            if page['complete']:break
            cursor = page['next_after']
        if not self._comparison_checkpoint(record,snapshot):return None
        return self.history.publish_comparison(snapshot,driver.decision_counts)

    def _run_history_comparison(self, record):
        snapshot = None; reason = 'unavailable'
        try:
            while self._comparison_checkpoint(record):
                if reason == 'stale' and record['snapshot'] is not None:
                    # A revocation can land between the last checkpoint and
                    # publication. Do not confuse its absent dirty scope with
                    # an append awaiting terminal evidence for 180 seconds.
                    current = self.history.comparison_current(record['snapshot'])
                    if current == 'invalid':
                        record['cancelled'].set();record['state'] = 'cancelled'
                        record['reason'] = 'history_authority_changed';return
                    if current == 'pending':
                        record['state'] = 'pending'
                        record['cancelled'].wait(min(.05,max(0.,record['deadline']-time.monotonic())))
                        continue
                snapshot = self.history.begin_comparison(after_scope=(
                    record['snapshot']['scope_seq'] - 1 if reason == 'stale' and record['snapshot'] else None))
                if snapshot is None:
                    if reason == 'stale':
                        # A newly reserved rival may temporarily make the exact
                        # dirty scope ineligible. Never refresh this task's clock.
                        record['state'] = 'pending'
                        record['cancelled'].wait(min(.05,max(0.,record['deadline']-time.monotonic())))
                        continue
                    driver = record['driver']
                    record['state'] = 'cached' if driver is not None and driver.cache_binding else 'idle'
                    return
                record['snapshot'] = snapshot
                record['session_id'] = snapshot.get('session_id')
                try:result = self._compare_history_snapshot(record,snapshot)
                except ReadError as exc:
                    if exc.reason not in ('history_snapshot_changed','history_pending'):raise
                    result = {'saved':False,'reason':'stale'}
                if result is not None and result.get('saved'):
                    # Durable publication is already acknowledged. Only a
                    # positively acknowledged child task closure permits a
                    # future task/deadline to reuse its recomputable prefix.
                    reason = 'complete'
                    record['driver'].finish_task(retain_prefix=True)
                    record['driver_active'] = False
                    record['state'] = 'published';return
                if result is None:return
                if result.get('state') in ('refused','cancelled'):
                    reason = result.get('reason','unavailable');record['reason'] = reason;return
                # An already acknowledged late append may invalidate the CAS.
                # Rebase with one verified prefix under this unchanged deadline.
                reason = 'stale'
                self.history.finish_comparison(snapshot,reason)
                snapshot = None
                # The child retains the only prefix. The next complete input
                # will rebase it under this task's original absolute deadline.
        except BaseException as exc:
            record['state'] = 'pending'
            record['reason'] = getattr(exc,'reason',None) or 'comparison_process_unavailable'
        finally:
            if record['cancelled'].is_set():
                record['reason'] = record.get('reason') or 'comparison_cancelled'
            elif time.monotonic() >= record['deadline']:
                record['state'] = 'pending';record['reason'] = 'comparison_deadline'
            if snapshot is not None and reason != 'complete':
                if record['cancelled'].is_set():reason = 'cancelled'
                elif time.monotonic() >= record['deadline']:reason = 'deadline'
                record['finish_pending'] = reason
                try:
                    self.history.finish_comparison(snapshot,reason)
                    record['finish_pending'] = None
                except BaseException:record['state'] = 'storage_pending'
            driver = record['driver']
            if driver is not None and record['state'] not in ('published','cached'):
                # Cleanup is outside every manager/permit/database lock. A
                # failed close retains this exact owner and its full charge.
                self._close_history_comparison_driver(record)
            self.permit.pause_comparison(record['grant'])
            record['completed'].set()

    def _stop_history_comparison(self, sid=None):
        try:
            with self._gate():
                record = self._history_comparison
                if record is not None:
                    if sid is not None and record['session_id'] not in (None,sid):return True
                    record['cancelled'].set()
                # Even the pre-publication maintenance window is unresolved.
                # Stop cannot report success and then let that claim restart.
                if self._history_maintenance is not None:return False
                if record is None:return True
                claim = self._history_maintenance = object()
        except ReadError:return False
        try:return self._stop_history_comparison_owned(record)
        finally:
            with self._lock:
                if self._history_maintenance is claim:self._history_maintenance = None

    def _stop_history_comparison_owned(self, record):
        driver = record['driver']
        if driver is not None:
            try:driver.cancel()
            except BaseException:return False
        thread = record['thread']
        if thread is not None and thread.is_alive():thread.join(timeout=5.)
        if thread is not None and thread.is_alive() or not record['completed'].is_set():return False
        if record.get('finish_pending') is not None:
            try:self.history.finish_comparison(record['snapshot'],record['finish_pending'])
            except BaseException:return False
        if not self._close_history_comparison_driver(record):return False
        if not self.permit.release_comparison(record['grant']):return False
        with self._lock:
            if self._history_comparison is record:self._history_comparison = None
        return True

    def _reconcile_speaker_links(self):
        self.maintain_history_comparison()
        if not self._links_enabled():return
        with self._lock:
            record = self._live
            if (self._probe_reconcile_active or time.monotonic() < self._probe_reconcile_after
                    or record is not None and (self._probe_unresolved(record)
                        or record['thread'] is not None and record['thread'].is_alive())):return
            self._probe_reconcile_active = True
        try:
            from .recording_speaker_links import reconcile_batches
            self.speaker_links.reconcile(recompute=reconcile_batches, limit=16)
        except BaseException:pass  # One optional scope/SQL failure cannot block text work.
        finally:
            with self._lock:
                self._probe_reconcile_active = False
                self._probe_reconcile_after = time.monotonic() + 1.

    def maintain_automatic_text_storage(self, automatic, *, stop):
        """Retry only the exact cleaned retained owner's immutable publication.

        This is not model admission and needs no automatic turn. The manager
        gate keeps the live record stable; the permit check holds no SQL work.
        """
        with self._gate():
            record = self._live
            if (automatic is not self.automatic or self._pending_record is not None
                    or record is None or record.get('automatic_key') is None
                    or not record['storage_pending'] or not record['cleaned']
                    or not record['process_cleanup_verified'] or record['purge_pending']):return False
            lease, thread = record.get('lease'), record.get('thread')
            if (lease is None or not lease.released or thread is not None and thread.is_alive()
                    or self._probe_unresolved(record)
                    or record.get('speaker_probe', {}).get('commit_pending')):return False
            if (record['history_snapshot'] is None and not
                    (record.get('terminal_pending') and type(record.get('terminal_metadata')) is bytes)):return False
            with self.permit._condition:
                if (self.permit._optional is not record
                        or self.permit._optional_kind != 'automatic_text'):return False
            now = time.monotonic()
            if stop() or now < automatic.next_attempt:return False
            # Reserve the existing scheduling floor only for an eligible retry.
            # Failure retains the same owner/payload. No wait or inference here.
            automatic.next_attempt = now + 1.
            self._persist_locked(record)
            return True

    def automatic_ready_hint(self):
        """Bounded metadata-only hint; never reconcile/publish under primary."""
        if self._primary_executing():return False
        with self._gate():
            if self._pending_record is not None:return False
            record=self._live
            if record is None:return True
            thread=record['thread']
            return (record['cleaned'] and not record['storage_pending']
                and not self._probe_unresolved(record)
                and not record.get('speaker_probe',{}).get('commit_pending')
                and (thread is None or not thread.is_alive()))

    def automatic_maintenance_hint(self):
        """One exact cleaned pending probe, metadata only; never model readiness."""
        if self.primary_maintenance_hint():return True
        with self._gate():
            record = self._live
            if self._pending_record is not None or record is None:return False
            probe = record.get('speaker_probe')
            thread, lease = record.get('thread'), record.get('lease')
            return bool(probe and probe.get('commit_pending') and not probe.get('commit_retrying')
                and probe.get('batch') is not None and probe.get('attempt_key') is not None
                and record['cleaned'] and record['process_cleanup_verified']
                and record['durable_result_available'] and not record['storage_pending']
                and lease is not None and lease.released and not self._probe_unresolved(record)
                and (thread is None or not thread.is_alive()))

    def automatic_idle(self):
        self._maintain_primary()
        if self._primary_executing():return False
        self._reconcile_speaker_links()
        record = self._live
        if record is not None:self._retry_probe_storage(record)
        with self._gate():
            self._drain_pending(); self._recover_acknowledged()
            record=self._live
            if record is None:return self._pending_record is None
            if record['storage_pending']:self._persist_locked(record)
            thread=record['thread']
            return (not record.get('speaker_probe',{}).get('commit_pending')
                and not self._probe_unresolved(record) and not record['storage_pending'] and record['cleaned']
                and (thread is None or not thread.is_alive()) and self._pending_record is None)

    def submit_automatic(self,intent):
        if self.automatic is None:raise ReadError(409,'automatic_configuration_unavailable')
        sid,key=intent['sid'],intent['candidate_key']
        detail=self.cell_source.detail(sid,intent['cell_id'])
        if not detail['pcm_retained']:raise ReadError(409,'cell_pcm_not_retained')
        doc=json.loads(detail['detail'])
        if hashlib.sha256(detail['detail']).hexdigest()!=intent['proof_sha256']:
            raise ReadError(409,'automatic_cell_binding_changed')
        from .native_owned_cells import SourceSupport
        request={'jobs_epoch':self.jobs_epoch,'profile':NAME,'support_id':SourceSupport.from_dict(doc['support']).support_id,
            'expected_cell_sequence':detail['sequence'],'expected_cell_revision':detail['revision'],
            'expected_proof_sha256':intent['proof_sha256']}
        from .native_owned_seam_cells import is_composite
        if is_composite(doc):request['coverage_intent']='overlapping_diagnostic'
        return self._submit('durable_cell',sid,intent['cell_id'],request,key,None,automatic=intent)

    def _submit(self, origin, sid, target, request, key, admission_open, *, automatic=None):
        self._epoch(request['jobs_epoch'])
        if request['profile'] != NAME:raise ReadError(409, 'profile_mismatch')
        self.cache.authorize_session(sid)
        digest = hashlib.sha256(encoded([origin, sid, target, request])).hexdigest()
        if automatic is not None:
            digest=hashlib.sha256(encoded({'candidate_key':key,'proof_sha256':automatic['proof_sha256'],
                'cell_id':target,'profile':NAME,'model_sha256':MODEL_SHA256,'runtime_sha256':RUNTIME_SHA256})).hexdigest()
        with self._gate():
            self._expire()
            previous = self._keys.get((sid, key)) if automatic is None else None
            if previous is not None:
                if previous['digest'] != digest:raise ReadError(409, 'idempotency_conflict')
                if previous['state'] in ('expired', 'retired') or previous['expired']:
                    raise JobError(410, 'job_retired', self._summary(previous))
                return 200, self._summary(previous)
            if automatic is not None:
                exact=self.automatic.read_candidate(key)
                if exact is None or exact['state']!='published' or exact['job_id'] is not None:
                    raise ReadError(409,'automatic_admission_closed')
            recovered = self.results.lookup_key(sid, self.jobs_epoch, key, digest)
            if recovered is not None:return 200, recovered
            if not self._enabled():raise ReadError(409, 'feature_disabled')
            self.results.preflight(sid)
            if self._live is not None and self._live['storage_pending']:
                raise ReadError(503, 'result_storage_pending')
            if automatic is None and len(self._records) >= MAX_ACCEPTED:raise ReadError(429, 'epoch_capacity')
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
                history_snapshot=None, terminal_metadata=None, terminal_pending=False,
                storage_pending=False, durable_result_available=False,
                admission_metadata=None, accepted_at=time.time(),automatic_key=key if automatic is not None else None)
            record['history_identity'] = HistoryIdentity(record['job_id'], sid, self.jobs_epoch, key, digest)
            reserved = False
            try:
                self.permit.reserve_optional(record, cancelled, lambda:self._cancel_record(record),
                    **({'owner_kind':'automatic_text'} if automatic is not None else {}))
                reserved = True
                self._validate(record)
                if self._live is not None:self._retire(self._live)
                candidate = [[x['session_id'], x['key'], x['digest'], x['job_id']] for x in self._records]
                if automatic is None:candidate.append([sid, key, digest, record['job_id']])
                if len(encoded(candidate)) > MAX_LEDGER_BYTES:raise ReadError(429, 'epoch_capacity')
                if admission_open is not None and not admission_open():raise ReadError(503, 'response_closed')
                if cancelled.is_set():raise ReadError(409, 'optional_cancelled')
                record['admission_metadata'] = self._history_metadata(record)
                admitted = self.results.reserve(record['history_identity'], record['admission_metadata'],
                    **({'automatic_key':key} if automatic is not None else {}))
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
            except BaseException as exc:
                record['preparation_failure'] = failure_observation('prepared', exc)
                self._cancel_record(record)
                self._finish(record, preparation_failed=True)
                if not record['cleaned'] and record['process_cleanup_verified']:
                    self._start_memory_cleanup(record)
            return 202, self._summary(record)

    def _register_accepted_locked(self, record):
        if record.get('automatic_key') is None:
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
            # Only bounded source copying occurs before the existing text finish.
            # The MOSS controller already positively reaped its child.
            try:self._stage_speaker_probe(record)
            except BaseException:
                if getattr(self.store.settings,'recording_speaker_links_enabled',False):
                    record.setdefault('speaker_probe',self._empty_speaker_probe())
            self._finish(record)
            self._await_memory_cleanup(record)
            try:self._run_speaker_probe(record)
            except BaseException:pass  # Text was already committed independently.

    @staticmethod
    def _probe_unresolved(record):
        probe = record.get('speaker_probe')
        return bool(probe and probe.get('operation') is not None and not probe['operation'].cleaned)

    def _links_enabled(self):
        return (self.speaker_links is not None and not self._closed
            and bool(getattr(self.store.settings, 'recording_speaker_links_enabled', False)))

    @staticmethod
    def _empty_speaker_probe():
        return {'state':'probe_unavailable', 'cancelled':threading.Event(), 'operation':None,
            'batch':None, 'crops':(), 'charge_bytes':0, 'reservation':None,
            'commit_pending':False, 'commit_retrying':False, 'terminal_state':None,
            'resolve_only':False, 'attempt_key':None, 'storage_failure':None,
            'failure_json':None, 'failure_frozen':False}

    def _stage_moss_history_binding(self, record, probe):
        """Freeze the new-vs-legacy route before any model crop is allocated."""
        if self.history is None:return
        from .native_owned_cells import validate_document
        from .recording_primary_probe import _fence
        _, _, parts = validate_document(record['token'].proof)
        with self.speaker_links._database() as db:
            recordings = set()
            for cid in sorted({part.chunk_id for part in parts}):
                row = db.execute('''SELECT w.batch_id FROM recording_work w
                    JOIN recording_batches b ON b.id=w.batch_id
                    WHERE w.chunk_id=? AND b.session_id=?''', (cid,record['session_id'])).fetchone()
                if row is None:raise ReadError(409,'primary_probe_source_stale')
                recordings.add(row[0])
        if len(recordings) != 1:raise ReadError(409,'primary_probe_source_stale')
        rid = next(iter(recordings))
        if not self.history.uses_history(record['session_id'],rid):return
        probe['history_route'] = True
        probe['history_admitted'] = False
        probe['invocation_id'] = uuid.uuid4().hex
        probe['recording_id'] = rid
        probe['fence'] = _fence(self.cell_source)
        receipts = []
        for cid in sorted({part.chunk_id for part in parts}):
            receipt = self.cell_source.owner.receipt(cid)
            if receipt is None:raise ReadError(409,'primary_probe_source_stale')
            receipts.append(receipt.encoded)
        if len(receipts) > 128:raise ReadError(429,'primary_probe_staging_capacity')
        probe['receipt_bytes'] = tuple(receipts)

    def _moss_history_current(self, record, probe, db):
        from .recording_primary_probe import detached_sources_current, standalone_current
        from .native_owned_cell_store import Receipt
        rid, proof = self.speaker_links._bound_job(db,record['session_id'],record['job_id'],
            probe['result_sha256'],probe['recording_id'])
        if rid != probe['recording_id'] or proof != probe['batch']['proof_sha256']:return False
        if record['cancelled'].is_set() or probe['cancelled'].is_set():return False
        if not detached_sources_current(db,self.cell_source,probe['fence'],
                encoded(probe['batch']['proof']),probe['receipt_bytes']):return False
        receipts = {Receipt.decode(raw).chunk_id:raw for raw in probe['receipt_bytes']}
        for saved in probe['batch'].get('primary_packets',[]):
            raw = receipts.get(saved['packet']['commit']['part']['chunk_id'])
            if raw is None or not standalone_current(db,saved,self.cell_source,probe['fence'],raw,
                    admitted=probe['history_admitted']):return False
        return True

    def _moss_admission_matches(self, attempt_id, batch, invocation_id):
        """The exact durable original MOSS result authenticates all native facts."""
        admission = self._moss_admission
        if admission is None:return False
        record, probe, expected_attempt, expected_invocation, snapshot, batch_sha = admission
        if (record is not self._live or record.get('speaker_probe') is not probe
                or not probe.get('history_route') or probe.get('history_admitted')
                or probe.get('history_attempt_id') != attempt_id or attempt_id != expected_attempt
                or probe.get('invocation_id') != invocation_id or invocation_id != expected_invocation
                or probe.get('batch') != batch or hashlib.sha256(encoded(batch)).hexdigest() != batch_sha
                or probe.get('operation') is not None
                or record['origin'] != 'durable_cell' or record.get('automatic_key') is None
                or record['cancelled'].is_set() or probe['cancelled'].is_set()
                or record['cleaned'] is not True or record['process_cleanup_verified'] is not True
                or record['durable_result_available'] is not True or record['storage_pending']
                or record['invalidated'] or record['expired']
                or record.get('lease') is None or not record['lease'].released
                or type(snapshot) is not HistorySnapshot or record.get('history_snapshot') is not snapshot
                or snapshot.identity != record['history_identity']
                or snapshot.result_json != record['result']
                or hashlib.sha256(snapshot.result_json).hexdigest() != snapshot.result_sha256
                or batch.get('session_id') != record['session_id'] or batch.get('job_id') != record['job_id']
                or batch.get('result_sha256') != snapshot.result_sha256
                or batch.get('projection_sha256') != snapshot.result_sha256
                or probe.get('result_sha256') != snapshot.result_sha256
                or record.get('token') is None or encoded(batch.get('proof')) != record['token'].proof
                or batch.get('proof_sha256') != record['token'].proof_sha256):return False
        # Current source binding is separately checked by the store's claim
        # transaction through _moss_history_current, under the publication guard.
        return True

    def _moss_success_matches(self, reservation, batch, invocation_id):
        from .recording_speaker_probe_runner import ProbeOperation, cleanup_verified
        record = self._live
        if record is None:return False
        probe = record.get('speaker_probe')
        if (not probe or not probe.get('history_route') or probe.get('reservation') != reservation
                or probe.get('batch') != batch or probe.get('invocation_id') != invocation_id
                or record['cancelled'].is_set() or probe['cancelled'].is_set()
                or not record['cleaned'] or not record['process_cleanup_verified']
                or not record['durable_result_available'] or record['storage_pending']):return False
        operation = probe.get('operation'); output = probe.get('validated_output')
        if (type(operation) is not ProbeOperation or not operation.started or not operation.cleaned
                or operation.child_dispatch != 'returned_validated' or not cleanup_verified(operation.receipt)
                or output is None or operation.request['job_id'] != invocation_id):return False
        operation._validate_return(output)
        physical = {row['index']:row for row in output['nodes']}
        return all(node.get('vector') == physical[node.get('probe_index',index)]['vector']
            and node['pcm16le_sha256'] == physical[node.get('probe_index',index)]['pcm_sha256']
            for index,node in enumerate(batch['nodes']))

    def _publish_moss_history(self, probe):
        if probe['resolve_only']:
            self._freeze_probe_failure(probe,'probe_interrupted'
                if probe['cancelled'].is_set() else 'probe_unavailable')
            outcome = self.history.resolve_moss(probe['history_attempt_id'],probe['history_identity'],
                failure_json=probe['failure_json'])
            if not outcome.get('resolved'):raise ReadError(503,'speaker_probe_storage_pending')
            probe['state'] = outcome.get('state','probe_unavailable')
            probe['commit_pending'] = False
            self.history.release_attempt(probe['reservation']['token'] if probe.get('reservation') is not None
                                         else probe['history_attempt_id'])
            self._kick_history_comparison()
            return
        record = self._live
        if record is None or record.get('speaker_probe') is not probe:
            raise ReadError(409,'primary_probe_authority_changed')
        self._freeze_probe_failure(probe,probe['terminal_state'])
        if probe['terminal_state'] == 'source_scoped' and probe['batch']['nodes']:
            with self._gate(), self._primary_guard(probe):
                # An uncertain finalize grants storage closure only. A retry
                # must not turn an unresolved pending row into a certificate.
                probe['resolve_only'] = True
                self.history.append_moss(probe['reservation'],probe['batch'],probe['invocation_id'],
                    success_capability=self._history_success_capability,
                    current_check=lambda db:self._moss_history_current(record,probe,db))
        else:
            # The reason is frozen before the first submit, including retries.
            reason = probe.setdefault('history_terminal_reason',
                'cancelled' if probe['terminal_state'] == 'probe_interrupted' else 'unavailable')
            self.history.finish_moss(probe['reservation'],reason,failure_json=probe['failure_json'])
        self._kick_history_comparison()
        probe['commit_pending'] = False
        self.history.release_attempt(probe['reservation']['token'])
        probe['state'] = 'links_stale' if probe['terminal_state'] == 'source_scoped' else probe['terminal_state']

    def _stage_speaker_probe(self, record):
        """Capture at most four owned copies while the full CellLease is charged."""
        if (not getattr(self.store.settings,'recording_speaker_links_enabled',False)
                or record['origin'] != 'durable_cell' or record.get('automatic_key') is None):return
        probe = record['speaker_probe'] = self._empty_speaker_probe()
        if not self._links_enabled():return
        from .recording_speaker_probe_runner import STAGING_BYTES, MAX_OUTPUT_BYTES, expected_space, cleanup_verified
        try:
            lease = record['lease']
            if lease.released or lease.cancelled.is_set() or record['cancelled'].is_set():return
            status = record['controller'].status(record['controller_id'])
            receipt = status.get('native_receipt') or {}
            if (not cleanup_verified(status.get('cleanup')) or not status.get('publication_valid')
                    or status.get('result') is None or not receipt.get('model_closed')
                    or not receipt.get('session_closed')):return
            self._validate(record)
            if hashlib.sha256(record['token'].proof).hexdigest() != record['token'].proof_sha256:return
            self._stage_moss_history_binding(record,probe)
            projection = {**status['result'], 'cell_binding':record['token'].projection()}
            result_sha = hashlib.sha256(encoded(projection)).hexdigest()
            # Reserve the separate fixed staging before allocating crop copies.
            with self._lock:
                if self._probe_staging_bytes:return
                self._probe_staging_bytes = probe['charge_bytes'] = STAGING_BYTES
            from .recording_speaker_links import (capture_candidates, capture_candidates_v2,
                capture_candidates_v3, POLICY_SHA256, POLICY_SHA256_V2, POLICY_SHA256_V3)
            from .native_owned_cells import Unavailable
            space, space_id = expected_space()
            policy = (POLICY_SHA256_V3 if probe.get('history_route') else
                self.speaker_links.capture_policy(record['session_id'], record['token'].proof))
            arguments = (lease.support, lease.pcm, record['token'].proof,
                         projection, record['job_id'], result_sha, space)
            if policy in (POLICY_SHA256_V2, POLICY_SHA256_V3):
                # Missing prospective packets cannot veto ordinary MOSS
                # observations or primary text. There is no historical scan.
                try:packets = self.speaker_links.capture_primary_packets(record['token'].proof)
                except (Unavailable, ReadError, ValueError, TypeError, KeyError):packets = []
                capture = capture_candidates_v3 if policy == POLICY_SHA256_V3 else capture_candidates_v2
                batch, crops = capture(*arguments, primary_packets=packets)
            elif policy == POLICY_SHA256:
                batch, crops = capture_candidates(*arguments)
            else:
                raise ValueError('Unsupported speaker probe policy')
            if probe.get('history_route'):
                from .native_owned_cell_store import Receipt
                receipts = {Receipt.decode(raw).chunk_id:raw for raw in probe['receipt_bytes']}
                for saved in batch.get('primary_packets',[]):
                    cid = saved['packet']['commit']['part']['chunk_id']
                    if cid not in receipts:
                        receipt = self.cell_source.owner.receipt(cid)
                        if receipt is None:raise ReadError(409,'primary_probe_source_stale')
                        receipts[cid] = receipt.encoded
                if len(receipts) > 128:raise ReadError(429,'primary_probe_staging_capacity')
                probe['receipt_bytes'] = tuple(receipts[cid] for cid in sorted(receipts))
            if (len(crops) > 4 or len(encoded(batch)) + sum(map(len,crops))
                    + sum(map(len,probe.get('receipt_bytes',()))) + MAX_OUTPUT_BYTES > STAGING_BYTES
                    or any(type(crop) is not bytes or len(crop) != 64000 for crop in crops)):
                raise ValueError('Probe staging capacity')
            if probe.get('history_route'):
                from .recording_speaker_history import moss_staging_bound
                moss_staging_bound(batch,probe['receipt_bytes'])
            self.cell_source.revalidate_dispatch(record['token'])
            if lease.released or lease.cancelled.is_set() or record['cancelled'].is_set():return
            probe.update(batch=batch, crops=crops, result_sha256=result_sha, space=space, space_id=space_id)
            probe['state'] = 'source_scoped' if crops else 'insufficient_clean_support'
        except BaseException:
            probe['state'] = 'probe_unavailable'
        finally:
            if probe['batch'] is None:self._drop_probe_staging(probe)

    def _drop_probe_staging(self, probe):
        if (probe.get('history_route') and probe.get('reservation') is not None
                and not probe.get('commit_pending')
                and (probe.get('operation') is None or probe['operation'].cleaned)):
            self.history.release_attempt(probe['reservation']['token'])
        probe['crops'], probe['batch'] = (), None
        with self._lock:
            if probe['charge_bytes']:
                self._probe_staging_bytes -= probe['charge_bytes']
                probe['charge_bytes'] = 0

    def _run_speaker_probe(self, record):
        """Text is already committed; this phase cannot change its lifecycle."""
        probe = record.get('speaker_probe')
        if probe is None:return
        reserved = False
        phase = 'prepare'
        failure_stage, failure_reason = 'prepare', 'other'
        batch = probe['batch']
        try:
            snapshot = record.get('history_snapshot')
            if (batch is None or not self._links_enabled() or not record['durable_result_available']
                    or record['storage_pending'] or snapshot is None
                    or snapshot.result_sha256 != probe.get('result_sha256')
                    or record['cancelled'].is_set() or not record['cleaned']
                    or not record['process_cleanup_verified']):
                probe['state'] = 'probe_unavailable'
                return
            sid, jid = record['session_id'], record['job_id']
            recording_id = self.speaker_links.recording_for_job(sid, jid, snapshot.result_sha256)
            from .recording_speaker_links import reconcile_batches
            probe['attempt_key'] = (sid,recording_id,jid,snapshot.result_sha256)
            probe['commit_pending'] = True
            probe['resolve_only'] = True  # A lost reserve acknowledgement never authorizes inference.
            probe['terminal_state'] = 'probe_unavailable'
            phase = 'reserve'
            failure_stage, failure_reason = 'reservation', 'storage_unavailable'
            if probe.get('history_route'):
                from .native_owned_cells import sha
                if recording_id != probe['recording_id']:raise ReadError(409,'primary_probe_source_stale')
                probe['history_identity'] = {'sid':sid,'rid':recording_id,'origin_kind':'moss_result',
                    'origin_id':jid,'result_sha256':snapshot.result_sha256,
                    'batch_sha256':batch['batch_sha256'],'space_id':probe['space_id']}
                probe['history_attempt_id'] = sha(probe['history_identity'])[:32]
                with self._gate(), self._primary_guard(probe):
                    self._moss_admission = (record,probe,probe['history_attempt_id'],
                        probe['invocation_id'],snapshot,hashlib.sha256(encoded(batch)).hexdigest())
                    try:
                        admission = self.history.claim_moss(sid,recording_id,probe['history_attempt_id'],
                            batch,probe['receipt_bytes'],space=probe['space'],
                            current_check=lambda db:self._moss_history_current(record,probe,db),
                            admission_capability=self._history_success_capability,
                            invocation_id=probe['invocation_id'])
                    finally:self._moss_admission = None
            else:
                admission = self.speaker_links.reserve(sid, recording_id, jid, snapshot.result_sha256,
                    batch['policy_sha256'], probe['space'], max_nodes=len(batch['nodes']), cancelled=probe['cancelled'], batch=batch)
            probe['reservation'] = admission
            if not admission.get('should_probe'):
                probe['commit_pending'] = bool(probe.get('history_route') and admission.get('state') == 'reserved')
                probe['state'] = admission.get('state', 'probe_unavailable')
                return
            if probe.get('history_route'):probe['history_admitted'] = True
            phase = 'probe'
            failure_stage, failure_reason = 'permit', 'permit_unavailable'
            if probe['crops']:
                # Fresh ownership/event after the old MOSS permit was released.
                # Primary preemption only sets the probe event, never record.cancelled.
                try:
                    self.permit.reserve_optional(probe, probe['cancelled'], probe['cancelled'].set)
                    reserved = True
                except Exception as error:
                    self._observe_probe_failure(probe, 'permit',
                        'optional_cancelled' if probe['cancelled'].is_set() else 'permit_unavailable', error)
                    probe['state'] = 'probe_interrupted' if probe['cancelled'].is_set() else 'probe_unavailable'
                if reserved:
                    # A current publication generation alone is insufficient:
                    # dispatch independently checks current source authority.
                    failure_stage, failure_reason = 'dispatch_validation', 'source_authority_changed'
                    if probe.get('history_route'):
                        with self._primary_guard(probe):
                            if not self.history.dispatch_current(probe['reservation'],
                                    current_check=lambda db:self._moss_history_current(record,probe,db)):
                                raise ReadError(409,'primary_probe_source_stale')
                    else:self.cell_source.revalidate_dispatch(record['token'])
                    failure_reason = 'other'
                    from .recording_speaker_probe_runner import ProbeOperation, ProbeDriver, KIND
                    from .recording_primary_probe import standalone_float_digest
                    # Safe now: copies and retained text no longer depend on MOSS scratch.
                    record['controller'].discard(record['controller_id'])
                    record['controller'] = record['controller_id'] = None
                    request = {'kind':KIND,
                        'job_id':probe['invocation_id'] if probe.get('history_route') else jid,
                        'space_id':probe['space_id'],
                        'model_path':self.store.settings.recording_speaker_model_path,
                        'crops':[{'index':index, 'pcm_sha256':hashlib.sha256(crop).hexdigest(),
                            'float32_sha256':standalone_float_digest(crop)}
                            for index,crop in enumerate(probe['crops'])]}
                    failure_stage = 'child_start'
                    operation = ProbeOperation(self.directory, request, probe['crops'],
                        gate=_SpeakerProbeGate(self, probe), driver_factory=self._probe_factory or ProbeDriver)
                    probe['operation'] = operation
                    try:
                        output = operation.run(probe['cancelled'])
                        if operation.cleaned and not probe['cancelled'].is_set():
                            probe['validated_output'] = json.loads(encoded(output))
                            physical = {item['index']: item for item in output['nodes']}
                            for index, node in enumerate(batch['nodes']):
                                node['vector'] = physical[node.get('probe_index', index)]['vector']
                            probe['state'] = 'source_scoped'
                        else:
                            probe['state'] = 'probe_interrupted'
                            if operation.failure_json is None:
                                self._observe_probe_failure(probe, 'cleanup',
                                    'optional_cancelled' if probe['cancelled'].is_set() else 'cleanup_unverified')
                    except BaseException:
                        probe['state'] = 'probe_interrupted' if probe['cancelled'].is_set() else 'probe_unavailable'
                    finally:
                        if probe['failure_json'] is None and not probe['failure_frozen']:
                            probe['failure_json'] = operation.failure_json
                        self.permit.release_optional(probe, cleaned=operation.cleaned)
                    if not operation.cleaned:
                        probe['state'] = 'probe_interrupted'
                        return  # Keep exact claim, handle, permit and staging charged.
            # Failed/absent probes still receive immutable bounded terminal evidence.
            if probe['state'] != 'source_scoped':
                for node in batch['nodes']:node.pop('vector', None)
            probe['commit_pending'] = True
            probe['resolve_only'] = False
            probe['terminal_state'] = probe['state']
            phase = 'publish'
            failure_stage, failure_reason = 'publication', 'storage_unavailable'
            self._publish_probe_batch(probe)
        except BaseException as exc:
            self._observe_probe_failure(probe, failure_stage,
                'optional_cancelled' if probe['cancelled'].is_set() else failure_reason, exc)
            if probe['commit_pending']:self._probe_storage_failure(probe, phase, exc)
            probe['state'] = 'capacity_exhausted' if getattr(exc,'reason',None) == 'capacity_exhausted' else 'probe_unavailable'
        finally:
            operation = probe['operation']
            clean = operation is None or operation.cleaned
            if reserved:self.permit.release_optional(probe, cleaned=clean)
            if clean and not probe['commit_pending']:self._drop_probe_staging(probe)

    @staticmethod
    def _observe_probe_failure(probe, stage, reason, error=None):
        if probe.get('failure_json') is not None or probe.get('failure_frozen'):return
        from .recording_speaker_probe_runner import failure_observation
        if reason == 'source_authority_changed':
            known = ('cell_revoked','cell_binding_changed','session_not_found',
                'automatic_source_revoked','primary_probe_source_stale')
            if (type(error) is not ReadError or type(error.reason) is not str
                    or error.reason not in known):reason = 'other'
        operation = probe.get('operation')
        probe['failure_json'] = failure_observation(stage, reason,
            operation.child_dispatch if operation is not None else 'not_attempted', error)

    @staticmethod
    def _freeze_probe_failure(probe, state):
        # A possibly committed submission owns these exact bytes forever. Later
        # storage observations remain live-only; successful outcomes stay NULL.
        if not probe.get('failure_frozen'):
            if state not in ('probe_unavailable','probe_interrupted'):
                probe['failure_json'] = None
            from .recording_speaker_link_store import validate_failure
            validate_failure(probe.get('failure_json'))
            probe['failure_frozen'] = True

    @staticmethod
    def _probe_storage_failure(probe, phase, error):
        # Immutable first fixed-field observation; no callback or raw exception.
        if probe.get('storage_failure') is not None:return
        observed = failure_observation('other', error)
        reason = error.reason if isinstance(error, ReadError) else None
        allowed = ('result_storage_unavailable', 'speaker_probe_storage_pending',
            'probe_unavailable', 'capacity_exhausted', 'session_not_found',
            'automatic_source_unplaced', 'automatic_binding_invalid',
            'automatic_history_binding_invalid', 'transcript_result_invalid',
            'transcript_result_hash_invalid', 'speaker_link_commit_conflict',
            'speaker_link_usage_invalid', 'invalid_speaker_link_reservation')
        probe['storage_failure'] = {
            'phase':phase if phase in ('prepare','reserve','probe','publish','retry_storage','cleanup_publish') else 'other',
            'error_type':'ReadError' if isinstance(error, ReadError) else observed['type'],
            'reason':reason if type(reason) is str and reason in allowed else 'other' if reason is not None else None,
            'errno':observed.get('errno')}

    def _publish_probe_batch(self, probe):
        if probe.get('history_route'):return self._publish_moss_history(probe)
        if probe['resolve_only']:
            return self._resolve_probe_attempt(probe)
        self._freeze_probe_failure(probe, probe['terminal_state'])
        try:self.speaker_links.commit(probe['reservation'], probe['batch'], state=probe['terminal_state'],
            failure_json=probe['failure_json'])
        except ReadError as exc:
            self._probe_storage_failure(probe, 'publish', exc)
            if exc.reason == 'session_not_found':
                probe['commit_pending'] = False
                probe['state'] = 'probe_interrupted'
                return  # Explicit session erasure also erases this pending copy.
            if exc.reason in ('probe_unavailable','automatic_source_unplaced','automatic_binding_invalid',
                    'automatic_history_binding_invalid','transcript_result_invalid',
                    'transcript_result_hash_invalid','speaker_link_commit_conflict',
                    'bounded_inventory_changed','bounded_inventory_source_changed','bounded_compact_changed',
                    'primary_probe_source_stale','primary_probe_authority_unavailable',
                    'primary_probe_authority_changed'):
                # Revoked current authority cannot be published or retried forever.
                # The immutable reservation still permits a historical unavailable outcome.
                return self._resolve_probe_attempt(probe)
            raise
        probe['commit_pending'] = False
        # No SQL/manager/publication lock is held while scoring originals.
        from .recording_speaker_links import reconcile_batches
        try:
            decision = self.speaker_links.recompute_scope(probe['reservation']['scope_id'],
                reconcile_batches, cancelled=probe['cancelled'])
            probe['state'] = decision['state'] if decision['saved'] else (
                probe['terminal_state'] if probe['cancelled'].is_set() else decision.get('state','links_stale'))
        except BaseException:
            probe['state'] = 'links_stale'

    def _resolve_probe_attempt(self, probe):
        if probe.get('history_route'):
            probe['resolve_only'] = True
            return self._publish_moss_history(probe)
        # Exact same-owner job/result/prepared-proof identity only. Resolving a
        # lost acknowledgement never starts inference or promotes old evidence.
        prepared = {**probe['batch'], 'nodes':[{key:value for key,value in node.items()
            if key != 'vector'} for node in probe['batch']['nodes']]}
        state = 'probe_interrupted' if probe['cancelled'].is_set() else 'probe_unavailable'
        self._freeze_probe_failure(probe, state)
        try:
            outcome = self.speaker_links.resolve_attempt(*probe['attempt_key'], prepared,
                state=state, failure_json=probe['failure_json'],
                token=probe['reservation'].get('token') if probe['reservation'] else None)
        except ReadError as exc:
            if exc.reason != 'session_not_found':raise
            outcome = {'resolved':True,'state':'probe_interrupted'}
        if not outcome['resolved']:raise ReadError(503,'speaker_probe_storage_pending')
        probe['commit_pending'] = False
        probe['state'] = outcome['state']

    def _retry_probe_storage(self, record):
        probe = record.get('speaker_probe')
        thread = record.get('thread')
        if (probe is None or not probe.get('commit_pending') or self._probe_unresolved(record)
                or thread is not None and thread.is_alive()):return
        with self._lock:
            if probe['commit_retrying']:return
            probe['commit_retrying'] = True
        try:
            self._publish_probe_batch(probe)  # Idempotent metadata only, never inference.
        except BaseException as exc:
            self._probe_storage_failure(probe, 'retry_storage', exc)
            probe['state'] = 'probe_unavailable'
        finally:
            probe['commit_retrying'] = False
            if not probe['commit_pending']:self._drop_probe_staging(probe)

    def _cleanup_speaker_probe(self, record):
        probe = record.get('speaker_probe')
        if probe is None or probe['operation'] is None:return True
        probe['cancelled'].set()
        # Cleanup-only, never another inference. The exact original handle stays
        # attached to the record until positive reap and private scratch cleanup.
        try:clean = probe['operation'].cleanup()
        except BaseException:clean = False
        self.permit.release_optional(probe, cleaned=clean)
        if clean:
            if probe['reservation'] is not None and probe['batch'] is not None:
                for node in probe['batch']['nodes']:node.pop('vector',None)
                probe['terminal_state'] = 'probe_interrupted'
                probe['commit_pending'] = True
                probe['resolve_only'] = False
                try:self._publish_probe_batch(probe)
                except BaseException as exc:
                    self._probe_storage_failure(probe, 'cleanup_publish', exc)
                    probe['state'] = 'probe_unavailable'
            if not probe['commit_pending']:self._drop_probe_staging(probe)
        return clean

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
                if record.get('terminal_metadata') is not None:
                    record['storage_pending'] = True
                    self.permit.release_optional(record, cleaned=False)
                    return
                if record['history_snapshot'] is not None:
                    record['storage_pending'] = not record['durable_result_available']
                    return
                # A projection/source failure is never ownership release proof.
                record['state'] = 'cleanup_blocked'
                record['cleaned'] = False
                record['result'] = record['raw'] = None
                self.permit.release_optional(record, cleaned=False)

    def _retained_charge(self, record):
        material = sum(len(value) for value in (record['raw'], record['result'], record['admission_metadata'], record.get('terminal_metadata')) if value is not None)
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

    @staticmethod
    def _terminal_rejection(record, phase, category):
        # Callers supply fixed literals; retain the first manager-side veto only.
        record.setdefault('manager_rejection', {'phase':phase, 'category':category})

    def _finish_locked(self, record, preparation_failed=False):
        if record['history_snapshot'] is not None or record.get('terminal_metadata') is not None:return
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
        if not valid:self._terminal_rejection(record, 'eligibility', 'eligibility_refused')
        if valid:
            try:
                self._validate(record)
            except Exception:
                self._terminal_rejection(record, 'source_validation', 'source_validation_refused')
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
                else:self._terminal_rejection(record, 'projection', 'result_capacity')
            if self._retained_charge(record) > MAX_RETAINED_BYTES:
                self._terminal_rejection(record, 'projection', 'retained_capacity')
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
            self._terminal_rejection(record, 'publication_before_release', 'publication_refused')
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
                self._terminal_rejection(record, 'publication_after_release', 'publication_refused')
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
                self._terminal_rejection(record, 'snapshot_validation', 'snapshot_refused')
                candidate = None
                record['result'] = None
        # This observation may call configuration code. The seal itself does not.
        try:record['configuration_observed'] = self._enabled()
        except Exception:
            self._terminal_rejection(record, 'configuration', 'configuration_unavailable')
            record['configuration_observed'] = False
            candidate = None
            record['result'] = record['raw'] = None
            record['cancelled'].set()
        record['cleaned'] = True
        if candidate is not None:self._seal_history_locked(record, candidate)
        if record['history_snapshot'] is None:
            # Cleanup/pin acknowledgement is settled before this immutable seal.
            # Native/failure evidence was already copied by the controller.
            if status is None:
                if controller is not None:raise ReadError(503, 'result_metadata_unavailable')
                status = {'invocation_possible':False, 'failure':record.get('preparation_failure'),
                    'cleanup':{'cleanup_verified':True, 'original_handle_waited_and_reaped':True,
                               'remaining_live_owned':[]}}
            outcome = terminal_outcome(status)
            if record.get('manager_rejection') is not None:
                outcome['manager_rejection'] = dict(record['manager_rejection'])
            outcome['state'] = record['state'] if record['state'] in CLEAN_TERMINAL else 'failed'
            outcome['copied_pin_release_acknowledged'] = record['lease'].released
            if len(encoded(outcome)) > 1024:raise ReadError(503, 'result_metadata_capacity')
            metadata = validate_metadata(record['admission_metadata'])
            metadata = encoded({**metadata, 'outcome':outcome})
            validate_metadata(metadata)
            if self._retained_charge(record) + len(metadata) > MAX_RETAINED_BYTES:
                raise ReadError(503, 'result_metadata_capacity')
            record['terminal_metadata'] = metadata
            record['terminal_interrupted'] = record['cancelled'].is_set() or record['invalidated']
            record['terminal_pending'] = record['storage_pending'] = True
            self._persist_locked(record)
        elif record.get('automatic_key') is not None:
            record['storage_pending'] = True
            self._persist_locked(record)
        else:
            self.permit.release_optional(record, cleaned=True)
            self._persist_locked(record)
        if (record['invalidated'] or record['expired']) and not record['storage_pending']:
            try:self._retire(record)
            except Exception:record['purge_pending'] = True

    def _history_metadata(self, record, status=None):
        identity = record['history_identity']
        context = {name:record[name] for name in ('origin', 'chunk_id', 'support_id', 'capture_epoch', 'revision', 'profile', 'accepted_at')}
        if record.get('automatic_key') is not None:
            context['automatic_candidate_key']=record['automatic_key']
            context['output_reconciliation']='outstanding_separate_primary_and_moss'
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
            self._terminal_rejection(record, 'seal', 'seal_refused')
            record['result'] = None
            return
        record['history_snapshot'] = candidate
        record['storage_pending'] = True

    def _persist_locked(self, record):
        snapshot = record['history_snapshot']
        key=record.get('automatic_key')
        if snapshot is None:
            if not record.get('terminal_pending'):return
            try:
                self.results.no_result(record['history_identity'], automatic_key=key,
                    interrupted=record['terminal_interrupted'], metadata_json=record['terminal_metadata'])
            except Exception as exc:
                if not (isinstance(exc,ReadError) and exc.reason in
                        ('session_not_found','automatic_publication_veto','automatic_intent_missing')):
                    record['storage_pending']=True
                    self.permit.release_optional(record,cleaned=False)
                    return
            record['terminal_pending']=record['storage_pending']=False
            self.permit.release_optional(record,cleaned=True)
            return
        if record['durable_result_available']:return
        try:
            if key is None:self.results.commit(snapshot)
            elif self.results.match_committed(snapshot,automatic_key=key):pass
            else:
                # The seal's source observations happen before these guards.
                # Only raw generation/events and the bounded SQLite transaction
                # are checked inside; never owner handoff/native/config callbacks.
                token=record['token'];owner=token.owner
                if not owner._publication_guard.acquire(timeout=LOCK_SECONDS):
                    raise ReadError(503,'automatic_publication_fence_busy')
                try:
                    # These authoritative flags are already protected by the
                    # source guard. Observe an explicit veto before cancellation
                    # can mask it; a failed exact readback never reaches here.
                    if (token.generation is not owner._epoch or owner._closed or owner._admission_closed
                            or owner._publication_revoke_pending
                            or self.cell_source._closed or self._closed
                            or not self.store.settings.moss_automatic_enabled or not self.store.settings.moss_jobs_enabled):
                        raise ReadError(409,'automatic_publication_veto')
                    with self.permit.automatic_publication_guard(record):
                        self.results.commit(snapshot,automatic_key=key)
                finally:owner._publication_guard.release()
        except Exception as exc:
            if (isinstance(exc, ReadError) and exc.reason in
                    ('session_not_found','automatic_publication_veto','automatic_intent_missing')):
                record['history_snapshot'] = None
                record['result'] = record['raw'] = None
                record['storage_pending'] = False
                record['invalidated'] = True
            else:record['storage_pending'] = True
            if key is not None:self.permit.release_optional(record,cleaned=not record['storage_pending'])
            return
        record['durable_result_available'] = True
        record['storage_pending'] = False
        if key is not None:self.permit.release_optional(record,cleaned=True)

    def _release_deleted_text_owner(self,record):
        lease=record.get('lease')
        if (record['cleaned']
                and record['process_cleanup_verified'] and lease is not None and lease.released
                and not self._probe_unresolved(record)):
            self.permit.release_optional(record,cleaned=True)

    def retry_storage(self, sid, jid, epoch):
        self._epoch(epoch)
        with self._gate():
            record = self._find(sid, jid)
            if self.results.is_deleted(sid):
                record['history_snapshot'] = None
                record['storage_pending'] = False
                self._release_deleted_text_owner(record)
                record['terminal_metadata'] = None
                record['terminal_pending'] = False
                raise ReadError(404, 'session_not_found')
            self._persist_locked(record)
        self._retry_probe_storage(record)  # Existing storage-only route never starts a model.
        with self._gate():
            return (503 if record['storage_pending'] else 200), self._summary(record)

    def _summary(self, record):
        # Fixed public vocabulary: no error strings, PIDs, paths, or proof bytes.
        value = {'job_id':record['job_id'], 'session_id':record['session_id'],
            'chunk_id':record['chunk_id'], 'sequence':record['sequence'],
            'jobs_epoch':self.jobs_epoch, 'state':record['state'], 'profile':NAME,
            'support_id':record['support_id'], 'capture_epoch':record['capture_epoch'],
            'revision':record['revision'],
            'cleanup_pending':self._probe_unresolved(record) or not record['cleaned'] or record['purge_pending'] or bool(record['lease'] and not record['lease'].released),
            'process_cleanup_verified':record['process_cleanup_verified'],
            'memory_cleanup_pending':record['purge_pending'] or bool(record['lease'] and not record['lease'].released),
            'invocation_possible':record['invocation_possible'],
            'publication_cancelled':record['cancelled'].is_set(),
            'result_available':record.get('automatic_key') is None and record['durable_result_available'] and record['cleaned'] and record['result'] is not None and not record['cancelled'].is_set(),
            'raw_available':record.get('automatic_key') is None and record['cleaned'] and record['raw'] is not None and not record['cancelled'].is_set(),
            'raw_unavailable_reason':'automatic_history_only' if record.get('automatic_key') is not None else record['raw_reason'], 'source_current':'not_checked',
            'durable_result_available':record['durable_result_available'],
            'storage_pending':record['storage_pending'],
            'reconciliation_remaining_work':self.results.recovery_remaining,
            'result_reconciliation':self.results.reconciliation_status(),
            'storage_reason':'result_storage_pending' if record['storage_pending'] else None,
            'structured_retention':'durable_until_explicit_session_deletion', 'raw_retention':'volatile',
            'retention':{'kind':'volatile', 'ttl_seconds':TTL_SECONDS, 'expired':record['expired']},
            'eos':None, 'complete_content':False}
        probe = record.get('speaker_probe')
        if probe is not None:
            value['recording_speaker_links'] = {'state':probe['state'],
                'cleanup_pending':bool(probe.get('operation') and not probe['operation'].cleaned),
                'staging_charge_bytes':probe.get('charge_bytes',0),
                'storage_pending':probe.get('commit_pending',False),
                'storage_failure':dict(probe['storage_failure']) if probe.get('storage_failure') else None}
        if record['origin'] == 'durable_cell':
            value['cell_binding'] = record['token'].projection() if record['token'] is not None else record['cell_binding']
        return value

    def _find(self, sid, jid):
        if self._live is not None and self._live.get('automatic_key') is not None:
            if (self._live['session_id'],self._live['job_id'])==(sid,jid):return self._live
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
            if record.get('automatic_key') is not None:raise ReadError(409,'automatic_history_only')
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
            if (record['state'] == 'cleanup_blocked' or self._probe_unresolved(record)) and (thread is None or not thread.is_alive()):
                retry = self._thread_factory(target=self._retry_cleanup, args=(record,), name='moss-cleanup', daemon=True)
                record['thread'] = retry
                try:retry.start()
                except Exception:pass  # Retain a possibly-started original thread reference.
            return (200 if record['cleaned'] and not self._probe_unresolved(record) else 202), self._summary(record)

    def _retry_cleanup(self, record):
        try:
            if self._probe_unresolved(record):self._cleanup_speaker_probe(record)
            if (not record['process_cleanup_verified'] and record['controller'] is not None
                    and record['controller_id'] is not None):
                record['controller'].retry_cleanup(record['controller_id'])
        except Exception:
            pass
        finally:
            self._finish(record)
            self._await_memory_cleanup(record)

    def invalidate_session(self, sid):
        primary_pending = not self._stop_primary(sid)
        comparison_pending = not self._stop_history_comparison(sid)
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
                        self._release_deleted_text_owner(record)
                        record['terminal_metadata'] = None
                        record['terminal_pending'] = False
                        probe = record.get('speaker_probe')
                        if probe is not None and not self._probe_unresolved(record):
                            probe['commit_pending'] = False
                            probe['state'] = 'probe_interrupted'
                            self._drop_probe_staging(probe)
                    if record is self._live and record['cleaned']:
                        self._retire(record)
            except Exception:
                record['purge_pending'] = True
            return (primary_pending or comparison_pending or self._probe_unresolved(record) or not record['cleaned'] or record['purge_pending']
                    or bool(record['lease'] and not record['lease'].released))
        return primary_pending or comparison_pending

    def close_admission(self):
        with self._lock:self._closed = True
        self.permit.close_admission()
        if self._live is not None:self._cancel_record(self._live)
        if self._primary_pending is not None:self._primary_pending['cancelled'].set()
        if self._history_comparison is not None:self._history_comparison['cancelled'].set()
        if self.cell_source is not None:self.cell_source.close_admission()

    def stop(self):
        self.close_admission()
        if not self._stop_primary():return False
        if not self._stop_history_comparison():return False
        if not self._drain_pending():return False
        record = self._live
        if record is not None:
            thread = record['thread']
            if thread is not None and thread.is_alive():thread.join(timeout=15)
            if thread is not None and thread.is_alive():return False
            if not record['cleaned'] and record['process_cleanup_verified']:
                self._finish(record)
            if not record['cleaned']:return False
            self._retry_probe_storage(record)
            if record.get('speaker_probe',{}).get('commit_pending'):return False
            probe = record.get('speaker_probe')
            if probe is not None and probe.get('operation') is not None and not probe['operation'].cleaned:
                if not self._cleanup_speaker_probe(record):return False
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
            'cleanup_pending':bool(record and self._probe_unresolved(record)) or bool(self._pending_record) or bool(self.cell_source and self.cell_source.cleanup_pending) or bool(record and (not record['cleaned'] or record['purge_pending'] or (record['lease'] and not record['lease'].released))),
            'speaker_probe_staging_bytes':self._probe_staging_bytes,
            'primary_probe_state':self._primary_pending['state'] if self._primary_pending else None,
            'primary_probe_crop_bytes':len(self._primary_pending['crop']) if self._primary_pending and self._primary_pending['crop'] is not None else 0,
            'history_comparison_state':self._history_comparison['state'] if self._history_comparison else None,
            'history_comparison_reason':self._history_comparison.get('reason') if self._history_comparison else None,
            'speaker_probe_state':record.get('speaker_probe',{}).get('state') if record else None,
            'speaker_probe_storage_pending':bool(record and record.get('speaker_probe',{}).get('commit_pending')),
            'speaker_probe_storage_failure':(dict(record['speaker_probe']['storage_failure'])
                if record and record.get('speaker_probe',{}).get('storage_failure') else None),
            'speaker_probe_cleanup_pending':bool(record and record.get('speaker_probe')
                and record['speaker_probe'].get('operation') and not record['speaker_probe']['operation'].cleaned),
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
