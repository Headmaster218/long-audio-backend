"""Explicit bounded GET-only re-export; no watch, upload, inference or resume.

All attempts start from block zero in distinct directories. Old output is never
opened or altered. Only exact typed cursor revision conflicts permit a restart.
A successful child complete.json retains all original export consistency gates.
"""
import argparse
from dataclasses import dataclass, replace
import json
from pathlib import Path
import sys

from . import recorder_incremental_span_export as export
from . import recording_uploader as u
from . import recording_uploader_history as history
from . import transcript_contract as c

SCHEMA = 'recorder-explicit-export-recovery-v1'

@dataclass(frozen=True)
class RecoveryLimits:
    attempts: int = 2
    requests: int = export.MAX_REQUESTS
    network_bytes: int = export.MAX_NETWORK_BYTES
    output_bytes: int = export.MAX_OUTPUT_BYTES

    def __post_init__(self):
        for name, maximum in (('attempts', 3), ('requests', export.MAX_REQUESTS),
                ('network_bytes', export.MAX_NETWORK_BYTES), ('output_bytes', export.MAX_OUTPUT_BYTES)):
            value = getattr(self, name)
            export.require(type(value) is int and 0 < value <= maximum, 'export_recovery_invalid_limit')

class _RevisionChanged(u.Incomplete):
    pass

class _GET:
    def __init__(self, transport, guard, limits, sid):
        self.transport, self.guard, self.limits = transport, guard, limits
        self.prefix = '/sessions/' + sid + '/transcript?cursor='
        self.requests = self.reserved_bytes = 0

    def request(self, method, path, **kwargs):
        self.guard.check()
        export.require(method == 'GET' and not kwargs, 'export_recovery_get_only')
        export.require(self.requests < self.limits.requests, 'export_recovery_request_bound')
        # Reserve the underlying HTTP transport's full response cap BEFORE each
        # GET, including errors. Failed/unknown responses cannot escape budgets.
        export.require(self.reserved_bytes + u.JSON_BYTES <= self.limits.network_bytes,
                       'export_recovery_network_bound')
        self.requests += 1; self.reserved_bytes += u.JSON_BYTES
        try:
            value = self.transport.request('GET', path)
        except u.HTTPFailure as exc:
            self.guard.check()
            if (type(exc) is u.HTTPFailure and exc.status == 409 and
                    exc.response_reason == 'transcript_revision_changed' and
                    path.startswith(self.prefix) and path.endswith('&projection=whole-primary-v1')):
                raise _RevisionChanged('transcript_revision_changed') from None
            raise
        self.guard.check()
        export.require(type(value) is dict and len(c.encoded(value)) <= u.JSON_BYTES,
                       'export_recovery_response_bound')
        return value


class _LocalPin:
    """Use production validators; bind every known local source/control identity."""
    def __init__(self, job_dir, destination, guard):
        self.root = Path(job_dir).absolute()
        export.require(self.root == self.root.resolve(strict=True), 'export_recovery_job_path')
        self.destination, self.guard = destination, guard
        self.baseline, self.protected, self.identities = self.observe()
        self.check_full()

    def observe(self):
        status = export.status; root = self.root; locks = []
        try:
            for name in ('intake.lock', 'uploader/uploader.lock'):
                locks.append(status.engine.held_lock(root/name))
            local = status._local(root, self.destination, self.guard)
            header, receipts, records, _ = local
            policy = status.span.POLICY.for_job(root,header,receipts,records,
                lambda: status.observer._remaining(self.guard))
            journal, checkpoint = status._journal(root, self.destination, header, receipts, self.guard, policy=policy)
            protected = (root, *(Path(receipt['path']) for receipt in receipts))
            paths = {root, root/'admissions', root/'uploader', root/'uploader/uploader.history',
                     root/'capture.json', root/status.initial.NAME, root/'intake.lock',
                     root/'uploader/uploader.lock', root/'uploader/uploader.json', *journal.observed_files}
            if policy.logical_boundary is not None:
                from . import recorder_logical_boundary as logical
                cut = policy.logical_boundary
                protected += logical.protected(cut)
                state = Path(cut['runner_state_dir']); successor = Path(cut['successor']['path'])
                paths.update((root/logical.NAME,root/logical.ANCHOR,state,state/'config.json',
                              state/logical.RUNNER_ANCHOR,successor))
                for index in range(cut['inputs']):
                    paths.update(state/('input-%03d.%s.json' % (index,kind)) for kind in ('intent','done'))
                paths.update(successor/name for name in ('audio.wav','segment.v1.bin','delivery.v1.txt'))
            for receipt in receipts:
                source = Path(receipt['path']); snapshot = Path(receipt['snapshot_path'])
                paths.update((source, snapshot, snapshot.parent, snapshot.parent/'admission.json'))
                paths.update(source/name for name in ('audio.wav','segment.v1.bin','delivery.v1.txt'))
                paths.update(snapshot/name for name in ('audio.wav','segment.v1.bin','delivery.v1.txt',
                                                       'snapshot.pending.json','snapshot.json'))
            identities = {path: u._signature(path.lstat()) for path in paths}
            # Signatures include inode, size, mtime and ctime; no mutation or new locks.
            return (local, checkpoint, dict(journal.observed_files)), protected, identities
        finally:
            for fd in reversed(locks): export.os.close(fd)

    def check(self):
        self.guard.check()
        export.require(all(u._signature(path.lstat()) == identity
                           for path, identity in self.identities.items()),
                       'export_recovery_local_identity_changed')

    def check_full(self):
        self.check()
        baseline, protected, identities = self.observe()
        export.require((baseline, protected, identities) ==
                       (self.baseline, self.protected, self.identities),
                       'export_recovery_local_identity_changed')
        self.check()


def run(*, job_dir, api, session_id, capture_id, output_dir, exclusive_writer=False,
        limits=None, recovery_limits=None, export_limits=None, transport=None, cancel=None, native_turns=False):
    export.require(type(native_turns) is bool, 'span_export_native_turn_option')
    export.require(not native_turns, 'export_recovery_native_turns_unsupported')
    export.require(exclusive_writer is True, 'span_status_exclusive_writer_ack_required')
    bounds = recovery_limits or RecoveryLimits()
    export.require(type(bounds) is RecoveryLimits, 'export_recovery_invalid_limit')
    destination = export.single.target(api, 'operator-selected-unverified', session_id, capture_id, 0)
    limits = limits or u.Limits(output_frames=history.MAX_FRAMES, deadline_seconds=120)
    guard = u.Guard(limits, cancel)
    pin = _LocalPin(job_dir, destination, guard)
    path = export._destination(output_dir, protected=pin.protected)
    base = export_limits or export.ExportLimits()
    export.require(type(base) is export.ExportLimits, 'span_export_invalid_limit')
    # Fixed aggregate disk allocation, not a fresh total budget at every restart.
    per_attempt = replace(base, output_bytes=min(base.output_bytes, bounds.output_bytes // bounds.attempts))
    http = _GET(transport or u.HTTPTransport(destination['origin'], guard), guard, bounds, session_id)
    audit = export._Output(path, guard, export.ExportLimits(output_bytes=16384))
    def record(value):
        audit.write('recovery.jsonl.partial', c.encoded(value) + b'\n')
        stream = audit.files['recovery.jsonl.partial'][0]
        stream.flush()
        export.os.fsync(stream.fileno())
    def check():
        pin.check()
        audit.check()
        return False
    try:
        audit.open('recovery.jsonl.partial')
        record(dict(schema=SCHEMA, job_dir=str(job_dir), session_id=session_id, capture_id=capture_id,
                    remote_read_only=True, inference_replay=False, attempts=bounds.attempts,
                    requests_bound=bounds.requests, reserved_network_bound=bounds.network_bytes,
                    output_bound=bounds.output_bytes, audit_output_bound=16384))
        for number in range(1, bounds.attempts + 1):
            pin.check_full()
            name = 'attempt-%02d' % number
            record(dict(attempt=number, output=name, status='started'))
            try:
                result = export.run(job_dir=job_dir, api=api, session_id=session_id,
                    capture_id=capture_id, output_dir=path/name, exclusive_writer=True,
                    limits=limits, export_limits=per_attempt, transport=http, cancel=check)
            except _RevisionChanged:
                pin.check_full()
                record(dict(attempt=number, status='revision_changed', partial_preserved=True,
                            requests=http.requests, reserved_network_bytes=http.reserved_bytes))
                if number == bounds.attempts:
                    raise export.status.Refused('export_recovery_attempts_exhausted') from None
                continue
            except BaseException:
                # Leave all files untouched; no blanket cleanup or automatic retry.
                try:
                    record(dict(attempt=number, status='refused', partial_preserved=True,
                                requests=http.requests, reserved_network_bytes=http.reserved_bytes))
                except BaseException:
                    # Preserve the original refusal/cancellation if the audit
                    # medium also fails. The last durable transition is enough
                    # to distinguish this from successful completion.
                    pass
                raise
            pin.check_full()
            record(dict(attempt=number, status='exported', manifest=name+'/complete.json',
                        transcript_revision=result['transcript_revision'], requests=http.requests,
                        reserved_network_bytes=http.reserved_bytes))
            audit.finish_file('recovery.jsonl.partial', 'recovery.jsonl')
            export.os.fsync(audit.directory); export.os.fsync(audit.parent)
            return dict(schema=SCHEMA, status='exported', output=name, export=result,
                        attempts=number, requests=http.requests, reserved_network_bytes=http.reserved_bytes)
    finally:
        audit.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('job-dir', 'api', 'session-id', 'capture-id', 'output-dir'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--exclusive-writer', action='store_true', required=True)
    parser.add_argument('--native-turns', action='store_true',
                        help='Unsupported with recovery; fails before any effect')
    parser.add_argument('--attempts', type=int, choices=(1, 2, 3), default=2)
    parser.add_argument('--deadline-seconds', type=float, default=120)
    args = vars(parser.parse_args(argv))
    attempts = args.pop('attempts'); deadline = args.pop('deadline_seconds')
    try:
        result = run(**args, recovery_limits=RecoveryLimits(attempts=attempts),
            limits=u.Limits(output_frames=history.MAX_FRAMES, deadline_seconds=deadline))
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps(export.failure_report(exc), sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True)); return 0

if __name__ == '__main__':
    raise SystemExit(main())
