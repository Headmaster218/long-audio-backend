"""One fixed durable-cell source for the existing volatile MOSS job owner.

No scheduler, thread, queue, SQL connection or copied-PCM pool lives here. The
cell owner retains an abandoned delivery until positive drainage. A claimed pin
moves to the manager's existing record; unsuccessful acceptance retains it in
this adapter until the exact registry removal is acknowledged.
"""
from dataclasses import asdict, dataclass
import hashlib
import threading
import time
import uuid

from .native_owned_cell_store import Operation, OP_SECONDS, LOCK_SECONDS
from .native_owned_cells import (Unavailable, SourceSupport, canonical, digest,
                                integer, text, validate_document, PROOF_BYTES)
from .native_owned_support_cache import ReadError
from .moss_handy_profile import NAME, MODEL_SHA256, RUNTIME_SHA256


@dataclass(frozen=True, slots=True)
class CellDispatchToken:
    origin: str
    owner: object
    adapter_epoch: str
    primary_identity: object
    generation: object
    session_id: str
    cell_id: str
    sequence: int
    revision: int
    state: str
    proof_sha256: str
    proof: bytes
    support: SourceSupport
    qualification_sha256: str
    source_binding: str
    profile: str = NAME
    model_sha256: str = MODEL_SHA256
    runtime_sha256: str = RUNTIME_SHA256

    def projection(self):
        value = {'origin': self.origin, 'cell_id': self.cell_id,
                 'sequence': self.sequence, 'revision': self.revision,
                 'proof_sha256': self.proof_sha256, 'support': self.support.to_dict(),
                 'proof_url': '/sessions/' + self.session_id + '/native-owned-cells/' + self.cell_id}
        if len(canonical(value)) > 4096:raise ReadError(503, 'cell_projection_capacity')
        return value


class CellLease:
    def __init__(self, source, token, pin):
        self.source, self.token, self.pin = source, token, pin
        self.support = token.support
        # This is source revocation/release only. Never alias the job event.
        self.cancelled = pin.cancelled

    @property
    def pcm(self):return self.pin._pcm

    @property
    def released(self):return self.pin.release_confirmed

    def release_pcm(self):
        # Full original 768 KiB reservation remains charged until cleanup.
        pass

    def release(self):return self.source.release(self)


class CellJobsSource:
    def __init__(self, owner, store, *, config_allowed=lambda: True):
        self.owner, self.store = owner, store
        self.epoch = uuid.uuid4().hex
        self._configuration_allowed = config_allowed
        self._identity = owner.authority.identity
        self._lock = threading.Lock()
        self._pending = None
        self._closed = False

    def _current(self):
        if (self._closed or not self._configuration_allowed()
                or not getattr(self.store.settings, 'native_owned_cells_enabled', False)
                or not getattr(self.store.settings, 'moss_jobs_enabled', False)):
            raise ReadError(409, 'feature_disabled')
        if (self.owner.authority.owner is not self.owner
                or self.owner.authority.identity != self._identity):
            raise ReadError(409, 'cell_revoked')
        if self.owner.qualification is None or not self.owner.health()['ready']:
            raise ReadError(503, 'cell_owner_unavailable')

    @staticmethod
    def _error(result):
        reason = result.get('reason', 'cell_owner_unavailable')
        if reason in ('session_unavailable', 'session_deleted_after_read', 'cell_session_unavailable'):
            return ReadError(404, 'session_not_found')
        if reason == 'cell_unavailable':return ReadError(404, 'cell_unavailable')
        if reason in ('pcm_not_retained', 'cell_pcm_not_retained'):
            return ReadError(409, 'cell_pcm_not_retained')
        if reason in ('cell_binding_changed', 'expected_cell_mismatch'):
            return ReadError(409, 'cell_binding_changed')
        if reason in ('revoked_generation', 'source_authority_unavailable', 'source_changed_after_read',
                      'source_changed_after_list_read', 'cell_revoked', 'generation_changed'):
            return ReadError(409, 'cell_revoked')
        if reason == 'response_closed':return ReadError(503, 'response_closed')
        return ReadError(503, 'cell_owner_unavailable')

    def _observe_pending(self):
        pending = self._pending
        if pending is None:return True
        if isinstance(pending, Operation):
            if pending.drained.is_set() and pending.slot_released.is_set():self._pending = None; return True
        elif pending.released:self._pending = None; return True
        return False

    @property
    def cleanup_pending(self):
        if not self._lock.acquire(timeout=LOCK_SECONDS):return True
        try:return not self._observe_pending()
        finally:self._lock.release()

    def _read(self, kind, payload):
        self._current()
        result = self.owner.submit(kind, payload)
        if isinstance(result, Operation):result = result.wait(OP_SECONDS)
        if result.get('status') != 'available':raise self._error(result)
        self._current()
        return result

    def list(self, sid, after=0, limit=20):
        text(sid); integer(after, (1 << 63) - 1); integer(limit, 20, 1)
        return self._read('jobs_list', (sid, after, limit))

    def detail(self, sid, cell_id):
        text(sid); digest(cell_id)
        return self._read('jobs_detail', (sid, cell_id))

    def acquire_dispatch(self, sid, cell_id, request, *, admission_open=None):
        text(sid); digest(cell_id)
        for name in ('expected_cell_sequence', 'expected_cell_revision'):
            integer(request[name], (1 << 63) - 1, 1)
        digest(request['expected_proof_sha256']); digest(request['support_id'])
        self._current()
        if not self._lock.acquire(timeout=LOCK_SECONDS):raise ReadError(503, 'cell_acquisition_pending')
        op = None; lease = None
        try:
            if not self._observe_pending():raise ReadError(503, 'cell_acquisition_pending')
            generation = self.owner._epoch
            payload = (sid, cell_id, request['expected_cell_sequence'], request['expected_cell_revision'],
                       request['expected_proof_sha256'], request['support_id'], generation)
            op = self.owner.submit('dispatch', payload)
            if not isinstance(op, Operation):raise self._error(op)
            self._pending = op
            op.wait(OP_SECONDS)
            # No user callback runs under the cell owner's or delivery lock.
            opened = not self._closed and (admission_open is None or admission_open())
            result = op.claim(admission_open=opened)
            if result.get('status') != 'available':
                op.slot_released.wait(max(0., op.deadline - time.monotonic()))
                self._observe_pending()
                raise self._error(result)
            pin = result['pin']
            try:
                if not op.slot_released.wait(max(0., op.deadline - time.monotonic())):
                    raise ReadError(503, 'cell_acquisition_pending')
                proof = result['detail']
                doc, support, _ = validate_document(proof)
                if (len(proof) > PROOF_BYTES or hashlib.sha256(proof).hexdigest() != request['expected_proof_sha256']
                        or support.support_id != request['support_id'] or result['owner_generation'] is not generation):
                    raise ReadError(409, 'cell_binding_changed')
                qualification = hashlib.sha256(canonical(asdict(self.owner.qualification))).hexdigest()
                binding = hashlib.sha256(canonical({'origin': 'durable_cell', 'adapter_epoch': self.epoch,
                    'session_id': sid, 'cell_id': cell_id, 'sequence': result['sequence'],
                    'revision': result['revision'], 'state': result['state'], 'proof_sha256': request['expected_proof_sha256'],
                    'primary_identity': self._identity, 'qualification_sha256': qualification, 'profile': NAME,
                    'model_sha256': MODEL_SHA256, 'runtime_sha256': RUNTIME_SHA256})).hexdigest()
                token = CellDispatchToken('durable_cell', self.owner, self.epoch, self._identity,
                    generation, sid, cell_id, result['sequence'], result['revision'], result['state'],
                    request['expected_proof_sha256'], proof, support, qualification, binding)
                token.projection()
                lease = CellLease(self, token, pin)
                self._pending = lease
                self._current()
                if generation is not self.owner._epoch:raise ReadError(409, 'cell_revoked')
                return lease
            except BaseException:
                pin.release()
                # Pin retains its owner; keep the single claimed acquisition.
                if lease is None:
                    lease = object.__new__(CellLease)
                    lease.source, lease.token, lease.pin = self, None, pin
                    lease.cancelled = pin.cancelled
                self._pending = lease
                raise
        except BaseException:
            if op is not None and isinstance(op, Operation) and lease is None:
                op.abandon('dispatch_not_accepted')
            raise
        finally:self._lock.release()

    def accepted(self, lease):
        if not self._lock.acquire(timeout=LOCK_SECONDS):raise ReadError(503, 'cell_acquisition_pending')
        try:
            if self._pending is not lease:raise ReadError(503, 'cell_acquisition_changed')
            self._pending = None
        finally:self._lock.release()

    def release(self, lease):
        lease.pin.release()
        if not lease.pin.release_confirmed:
            lease.pin.release_done.wait(OP_SECONDS)
        return lease.pin.release_confirmed

    def abandon(self, lease):
        # Called before acceptance: keep exactly this acquisition while pending.
        confirmed = self.release(lease)
        if not self._lock.acquire(timeout=LOCK_SECONDS):return False
        try:
            if self._pending is None:self._pending = lease
            if self._pending is lease and confirmed:self._pending = None
            return confirmed and self._pending is None
        finally:self._lock.release()

    def revalidate_dispatch(self, token):
        self._current()
        if (type(token) is not CellDispatchToken or token.owner is not self.owner
                or token.adapter_epoch != self.epoch or token.primary_identity != self._identity
                or token.generation is not self.owner._epoch):
            raise ReadError(409, 'cell_revoked')
        if (token.origin != 'durable_cell' or token.profile != NAME
                or token.model_sha256 != MODEL_SHA256 or token.runtime_sha256 != RUNTIME_SHA256
                or token.qualification_sha256 != hashlib.sha256(canonical(asdict(self.owner.qualification))).hexdigest()):
            raise ReadError(409, 'cell_binding_changed')
        result = self._read('validate_dispatch', (token.session_id, token.cell_id, token.sequence,
            token.revision, token.proof_sha256, token.support.support_id, token.generation))
        if result['owner_generation'] is not token.generation:raise ReadError(409, 'cell_revoked')
        if (result['detail'] != token.proof or result['support'] != token.support
                or result['state'] != token.state):raise ReadError(409, 'cell_binding_changed')
        return True

    def publication_current(self, token):
        # Immediate post-serialization check; fresh authority was read just before.
        self._current()
        if (token.owner is not self.owner or token.adapter_epoch != self.epoch
                or token.generation is not self.owner._epoch):raise ReadError(409, 'cell_revoked')
        return True

    def close_admission(self):
        self._closed = True
        pending = self._pending
        if isinstance(pending, Operation):pending.abandon('admission_closed')
        elif pending is not None:pending.pin.release()
        self.owner.close_admission()
