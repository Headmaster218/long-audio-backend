"""One optional embedding child, reusing the MOSS owned-process primitives.

Importing is model-free. Only the admitted child imports the existing optional
extractor. No gallery, identity enrollment, downloads, inference retry or worker.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import time
from types import SimpleNamespace
import uuid

from .moss_handy_controller import OwnedDriver
from .moss_handy_profile import canonical
from .moss_handy_runner import (check_directory, directory_identity, process_identity,
    read_owned, write_owned, samples_from_pcm, float_bytes)

KIND = 'recording-speaker-probes-v1'
STAGING_BYTES = 512 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
WORK_SECONDS, CLEANUP_SECONDS = 10., 5.
MODEL_SHA256 = '1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b'
BINDING_ID = '02763d940b59563aeeb7e70cc1b0b12542b6d09fa1f0c7be76f33faedb293be0'


def expected_space():
    from .embedding_space import _descriptor, validate_space
    descriptor = _descriptor(BINDING_ID, SimpleNamespace(provider='cpu', num_threads=2))
    return descriptor, validate_space(descriptor)


def cleanup_verified(receipt):
    return (type(receipt) is dict and receipt.get('cleanup_verified') is True
        and receipt.get('original_handle_waited_and_reaped') is True
        and receipt.get('remaining_live_owned') == [] and not receipt.get('cleanup_failure'))


class ProbeChildFailure(RuntimeError):
    """A positively observed nonzero child exit, without private output."""


def failure_observation(stage, reason, child_dispatch, error=None):
    """Only fixed observations, never messages, arguments, SQL or input data."""
    from .recording_speaker_link_store import (FAILURE_SCHEMA, FAILURE_STAGES,
        FAILURE_REASONS, FAILURE_DISPATCH, FAILURE_ERROR_TYPES, validate_failure)
    error_type = ('RuntimeError' if type(error) is ProbeChildFailure else
                  type(error).__name__ if error is not None else 'other')
    raw = canonical({'schema':FAILURE_SCHEMA,
        'stage':stage if stage in FAILURE_STAGES else 'unknown',
        'reason':reason if reason in FAILURE_REASONS else 'other',
        'child_dispatch':child_dispatch if child_dispatch in FAILURE_DISPATCH else 'unknown',
        'error_type':error_type if error_type in FAILURE_ERROR_TYPES else 'other'})
    validate_failure(raw)
    return raw


class ProbeDriver(OwnedDriver):
    """Narrow OwnedDriver specialization; no second background supervisor."""
    def __init__(self, directory, request_sha):
        super().__init__(directory, request_sha)
        # CandidateGuard's own_cap includes the caller. Keep its existing 3.5GiB
        # aggregate reserve and separately enforce a sampled child-only 1GiB cap.
        self.limits['artifact_cap'] = STAGING_BYTES
        self.guard.artifact_cap = STAGING_BYTES
        self.guard.deadline = time.monotonic() + WORK_SECONDS

    def wait(self, cancelled):
        while self.proc.poll() is None:
            if self._service_gate is not None:self._service_gate.check('running')
            if cancelled.is_set():raise RuntimeError('Optional probe interrupted')
            observed = self.guard.check()
            child_rss = sum(row['rss'] for row in observed['processes'] if row['pid'] != os.getpid())
            if child_rss > 1024**3:raise RuntimeError('Optional probe child RSS cap')
            check_directory(self.directory, self.identity)
            time.sleep(.05)
        if self.proc.returncode != 0:raise ProbeChildFailure('Optional probe child failed')
        result = json.loads(read_owned(self.directory, 'probe-result.json', identity=self.identity))
        return result

    def cleanup(self, stop):
        from .qwen_candidate_runner import CandidateGuard, process_cleanup
        service = self._service_gate
        class CleanupGuard(CandidateGuard):
            def check(inner, *args, **kwargs):
                if service is not None:
                    try:service.cleanup_sample()
                    except Exception:pass
                return super().check(*args, **kwargs)
        cleanup = CleanupGuard(self.directory, caller=self.guard.caller,
            deadline=CLEANUP_SECONDS, **self.limits)
        cleanup.own_roots = self.guard.own_roots
        cleanup.known_own.update(self.guard.known_own)
        if self.proc is not None and self.proc.stdin is not None and not self.proc.stdin.closed:
            self.proc.stdin.close()
        receipt = {}
        process_cleanup(self.proc, cleanup, receipt, stop)
        receipt.update(discovery_complete=False, containment='observed-owned-identities-only')
        self.guard.known_own.update(cleanup.known_own)
        return receipt


class ProbeOperation:
    """Single owned invocation; unverified cleanup keeps claim/driver reachable."""
    def __init__(self, directory, request, crops, *, gate, driver_factory=ProbeDriver, work_deadline=None):
        if work_deadline is not None and (type(work_deadline) not in (int,float)
                or not math.isfinite(work_deadline)):
            raise ValueError('Invalid absolute probe deadline')
        self.work_deadline = work_deadline
        self.directory, self.request, self.crops = Path(directory), request, crops
        self.identity = directory_identity(self.directory)
        if any(self.directory.iterdir()):raise RuntimeError('MOSS slot was not discarded')
        self.gate, self.factory = gate, driver_factory
        self.driver = None
        self.claim = self.claim_identity = None
        self.cleaned = False
        self.receipt = None
        self.started = False
        self.failure_json = None
        self.child_dispatch = 'not_attempted'

    def _work_checkpoint(self):
        if self.work_deadline is not None and time.monotonic() >= self.work_deadline:
            raise TimeoutError('Probe work deadline')

    def _observe_failure(self, stage, reason, error=None):
        if self.failure_json is None:
            self.failure_json = failure_observation(stage, reason, self.child_dispatch, error)

    def _claim_matches(self):
        info = (self.directory / 'owner.json').lstat()
        if ((info.st_dev, info.st_ino) != self.claim_identity
                or read_owned(self.directory, 'owner.json', identity=self.identity) != self.claim):
            raise RuntimeError('Probe claim changed')

    def run(self, cancelled):
        if self.started:raise RuntimeError('Probe inference is never replayed')
        self.started = True
        result = None
        stage = 'prepare'
        try:
            self._work_checkpoint()
            self.gate.check('before_input')
            self._work_checkpoint()
            self.claim = canonical({'schema':KIND, 'owner':uuid.uuid4().hex,
                'job_id':self.request['job_id'], 'parent_identity':process_identity(os.getpid()),
                'directory_identity':self.identity})
            write_owned(self.directory, 'owner.json', self.claim, identity=self.identity)
            info = (self.directory / 'owner.json').lstat()
            self.claim_identity = (info.st_dev, info.st_ino)
            request = canonical(self.request)
            write_owned(self.directory, 'request.json', request, identity=self.identity)
            for index, crop in enumerate(self.crops):
                write_owned(self.directory, 'probe-%d.pcm' % index, crop, identity=self.identity)
            self.crops = ()
            stage = 'child_start'
            self.driver = self.factory(self.directory, hashlib.sha256(request).hexdigest())
            self.driver._service_gate = self.gate
            if self.work_deadline is not None:
                # Primary admission owns this clock; construction cannot renew it.
                guard = getattr(self.driver,'guard',None)
                if guard is not None:guard.deadline = min(guard.deadline,self.work_deadline)
            self._work_checkpoint()
            self.child_dispatch = 'possible'  # start may lose its acknowledgement.
            self.driver.start(cancelled)
            stage = 'child_wait'
            result = self.driver.wait(cancelled)
            self._work_checkpoint()
            if cancelled.is_set():raise RuntimeError('Probe interrupted')
            stage = 'return_validation'
            self._validate_return(result)
            self._work_checkpoint()
            self.child_dispatch = 'returned_validated'
            return result
        except BaseException as error:
            reason = ('optional_cancelled' if cancelled.is_set() else
                'pre_dispatch_deadline' if self.work_deadline is not None and self.child_dispatch == 'not_attempted'
                    and time.monotonic() >= self.work_deadline else
                'invalid_return' if stage == 'return_validation' else
                'child_failed' if type(error) is ProbeChildFailure else 'other')
            self._observe_failure(stage, reason, error)
            raise
        finally:
            self.crops = ()
            try:self.cleanup()
            except BaseException as error:
                self._observe_failure('cleanup', 'cleanup_unverified', error)
                raise

    def _validate_return(self, result):
        from .embedding_space import validate_space
        from .recording_speaker_links import valid_vector
        if (type(result) is not dict or len(canonical(result)) > MAX_OUTPUT_BYTES
                or set(result) != {'kind','job_id','space','space_id','nodes','compute_attempts'}
                or result['kind'] != KIND or result['job_id'] != self.request['job_id']
                or result['space_id'] != self.request['space_id']
                or validate_space(result['space']) != self.request['space_id']
                or type(result['nodes']) is not list or len(result['nodes']) != len(self.request['crops'])
                or result['compute_attempts'] != len(self.request['crops'])):
            raise ValueError('Probe output binding invalid')
        for expected, actual in zip(self.request['crops'], result['nodes']):
            if (set(actual) != {'index','pcm_sha256','vector'}
                    or actual['index'] != expected['index']
                    or actual['pcm_sha256'] != expected['pcm_sha256']):
                raise ValueError('Probe crop output changed')
            valid_vector(actual['vector'])

    def cleanup(self):
        if self.cleaned:return True
        self.gate.cleaning()
        if self.driver is None:
            receipt = {'cleanup_verified':True, 'original_handle_waited_and_reaped':True,
                       'remaining_live_owned':[]}
        else:
            try:receipt = self.driver.cleanup(True)
            except BaseException as error:
                self._observe_failure('cleanup', 'cleanup_unverified', error)
                receipt = {'cleanup_verified':False}
        self.receipt = receipt
        if not cleanup_verified(receipt):
            self._observe_failure('cleanup', 'cleanup_unverified')
            return False
        try:
            if self.claim is not None and self.claim_identity is None:
                # A write acknowledgement was lost; absence of a child does not
                # prove the private claim can safely be discarded.
                self._observe_failure('cleanup', 'cleanup_unverified')
                return False
            if self.claim_identity is not None:
                self._claim_matches()
                (self.directory / 'owner.json').unlink()
                self.claim_identity = None
                self.claim = None
            check_directory(self.directory, self.identity)
            for path in self.directory.iterdir():path.unlink()
        except BaseException as error:
            self._observe_failure('cleanup', 'cleanup_unverified', error)
            return False
        self.cleaned = True
        return True


def child(directory, request, *, identity, check):
    """Invoked by moss_handy_runner after original-parent admission and sandbox."""
    if (type(request) is not dict or set(request) != {'kind','job_id','space_id','model_path','crops'}
            or request['kind'] != KIND or type(request['crops']) is not list
            or not 1 <= len(request['crops']) <= 4):
        raise ValueError('Probe request fields invalid')
    model = Path(request['model_path'])
    if (not model.is_absolute() or model.resolve() != model or not model.is_file()
            or any(path.is_symlink() for path in (model,*model.parents))):
        raise ValueError('Canonical existing local speaker model required')
    _, space_id = expected_space()
    if request['space_id'] != space_id:raise ValueError('Probe space changed')
    from .moss_handy_profile import runtime_site_paths
    import sys
    for root in runtime_site_paths():
        if str(root) not in sys.path:sys.path.append(str(root))
    check()
    import numpy as np
    import sherpa_onnx as sherpa
    from .embedding_space import SherpaSpaceAttestor
    config = sherpa.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=2, provider='cpu')
    attestor = SherpaSpaceAttestor()
    before = attestor.begin(sherpa, config, MODEL_SHA256)
    if before['descriptor'] != expected_space()[0]:raise ValueError('Unqualified embedding implementation')
    check()
    extractor = sherpa.SpeakerEmbeddingExtractor(config)
    rows = []
    try:
        if extractor.dim != 512:raise ValueError('Probe dimension changed')
        for index, crop in enumerate(request['crops']):
            if (type(crop) is not dict or set(crop) != {'index','pcm_sha256','float32_sha256'}
                    or crop['index'] != index):raise ValueError('Probe crop manifest invalid')
            check()
            pcm = read_owned(directory, 'probe-%d.pcm' % index, identity=identity)
            if len(pcm) != 64000 or hashlib.sha256(pcm).hexdigest() != crop['pcm_sha256']:
                raise ValueError('Probe PCM binding changed')
            samples = samples_from_pcm(pcm)
            if hashlib.sha256(float_bytes(samples)).hexdigest() != crop['float32_sha256']:
                raise ValueError('Probe float input binding changed')
            stream = extractor.create_stream()
            stream.accept_waveform(16000, samples)
            stream.input_finished()
            if not extractor.is_ready(stream):raise ValueError('Probe not ready')
            check()
            vector = np.asarray(extractor.compute(stream), dtype=np.float32)
            norm = np.linalg.norm(vector)
            if vector.shape != (512,) or not np.isfinite(vector).all() or not np.isfinite(norm) or norm <= 0:
                raise ValueError('Probe vector invalid')
            vector = vector / norm
            rows.append({'index':index, 'pcm_sha256':crop['pcm_sha256'], 'vector':[float(x) for x in vector]})
            del stream, vector, samples, pcm
            check()
        descriptor, observed_id = attestor.finish(before, extractor)
        if observed_id != space_id:raise ValueError('Probe embedding space changed')
        result = canonical({'kind':KIND, 'job_id':request['job_id'], 'space':descriptor,
            'space_id':space_id, 'nodes':rows, 'compute_attempts':len(rows)})
        if len(result) > MAX_OUTPUT_BYTES:raise ValueError('Probe output capacity')
        check()
        write_owned(directory, 'probe-result.json', result, identity=identity)
    finally:
        # Sherpa exposes no close; reference release is not proof of cleanup.
        # The owner always positively waits/reaps the child before publication.
        del extractor
