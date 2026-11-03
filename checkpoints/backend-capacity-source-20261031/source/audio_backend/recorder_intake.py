"""Explicit one-delivery intake into an existing strict session, never a watcher.

Requires one exclusive operator/writer for the session/capture and local job.
Producer declarations are checked but are not authenticated hardware provenance.
No session creation, resampling, gap filling, inference retry or original deletion.
"""
import argparse
import copy
import fcntl
import json
import os
import re
from pathlib import Path
import stat
import sys

from . import recording_uploader as u
from .recorder_binding import target
from .recorder_delivery import Refused, inspect_delivery
from .recorder_snapshot import create_snapshot, inspect_snapshot, _json

SCHEMA = 'explicit-recorder-intake-v1'
MAX_MANIFEST = 16384


def require(ok, reason):
    if not ok:
        raise Refused(reason)


def validate_delivery(path):
    report = inspect_delivery(path, validate_metadata=True)
    required = dict(sequence=0, epoch=0, frame_start=0, boundary='START', close_reason='STOP',
                    sample_rate_hz=16000, channels=1, trusted_previous_seam=False)
    require(all(report[k] == v for k, v in required.items()), 'intake_requires_complete_initial_mono16k_capture')
    require(0 < report['frames'] <= 2 * 3600 * 16000, 'intake_two_hour_bound')
    return report


def private_directory(path):
    require(path.absolute() == path.resolve(strict=True), 'intake_symlink_directory')
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and info.st_mode & 0o077 == 0, 'intake_private_directory_required')


def control(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
            'intake_unsafe_control_file')


def manifest_read(path):
    control(path)
    require(path.stat().st_size <= MAX_MANIFEST, 'intake_manifest_size')
    with path.open('rb') as stream:
        raw = stream.read(MAX_MANIFEST + 1)
    value = json.loads(raw)
    require(len(raw) <= MAX_MANIFEST and _json(value) == raw, 'intake_manifest_noncanonical')
    return value


def manifest_write(path, value):
    raw = _json(value)
    require(len(raw) <= MAX_MANIFEST, 'intake_manifest_size')
    # Exclusive creation, never repair/overwrite an interrupted job in place.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    u._fsync_dir(path.parent)


class IntakeUploader(u.Uploader):
    """Stricter reconciliation for this opt-in command; legacy defaults unchanged."""
    def _match(self, row, remote):
        require(len(remote['sources']) <= 3, 'intake_server_source_count')
        # Existing append matcher also requires persisted create intent, canonical
        # policy/format, exact source set on frozen pages, and frozen EOS identity.
        checked = copy.deepcopy(row)
        result = u.LegacyAppendUploader._match(self, checked, remote)
        row.clear(); row.update(checked)
        return result

    def _saved_pages(self):
        d = self.journal.data
        present = []; released = []
        for row in d['pages']:
            remote = self.get(self._page_path(row['number']), missing=True)
            if remote is None:
                require(not row.get('server_id') and not row['released'], 'intake_server_page_disappeared')
            else:
                self._match(row, remote); present.append(remote)
                require(not row['released'] or remote['released'], 'intake_server_release_disappeared')
                if remote['released']: released.append(remote)
        status = self.get(self.base, missing=True)
        if status is None:
            require(not present, 'intake_server_capture_disappeared')
            contract = self.get('/sessions/' + self.sid + '/recording-contract')
            require(contract['capture'] is None and not contract['has_chunks'], 'intake_session_no_longer_empty')
        else:
            end = released[-1]['end_frame'] if released else self.origin
            pending = [r for r in present if not r['released']]
            number = pending[0]['page_number'] if pending else None
            remote_number = status['pending_page']['page_number'] if status['pending_page'] else None
            require((status['capture_id'], status['capture_origin_frame'], status['mode']) ==
                    (self.capture, self.origin, 'sealed_pages') and status['state'] != 'aborted'
                    and status['policy'] == 'sealed-recording-exact-overlap-v1'
                    and status['format'] == 'wav-pcm16le-16000-mono'
                    and status['page_count'] == len(present) and status['released_pages'] == len(released)
                    and status['capture_end_frame'] == end and remote_number == number,
                    'intake_capture_history_differs')
            saved_eos = d.get('finish') or any(r.get('seal', {}).get('eos') for r in d['pages'])
            require(not status['eos'] or (saved_eos and d.get('clean_eof') and
                    end == self.origin + d['emitted_frames']), 'intake_unexpected_eos')
            require(not d['complete'] or status['eos'], 'intake_eos_disappeared')
        self.journal.save()


def check_contract(contract, sid, frames):
    required = dict(schema='recording-session-contract-v1', session_id=sid,
        processing_mode='legacy', strict_primary_enabled=True,
        strict_primary_policy='strict-primary-payload-v2', source_format='wav-pcm16le-16000-mono',
        sample_rate_hz=16000, channels=1, sample_width_bytes=2,
        max_page_work_chunks=28, work_chunk_frames=168000)
    require(all(contract.get(k) == v for k, v in required.items()), 'intake_incompatible_strict_contract')
    require(contract.get('strict_primary_enabled') is True, 'intake_incompatible_strict_contract')
    require(contract.get('source_idempotency') == dict(scope='session_capture',
            bound_fields=['page','wav_sha256','source_start_frame','format'], changed_body='conflict'),
            'intake_incompatible_idempotency_contract')
    piece_frames = min(frames, u.SOURCE_FRAMES)
    require(type(contract.get('max_source_frames')) is int and contract['max_source_frames'] >= piece_frames
            and type(contract.get('max_source_bytes')) is int and contract['max_source_bytes'] >= 44 + 2 * piece_frames,
            'intake_server_source_limits_too_small')


def run(*, delivery, job_dir, api, session_id, capture_id, resume=False,
        exclusive_writer=False, limits=None, transport=None, cancel=None):
    require(exclusive_writer is True, 'intake_exclusive_writer_ack_required')
    destination = target(api, 'operator-selected-unverified', session_id, capture_id, 0)
    api = destination['origin']
    delivery = Path(delivery).absolute()
    report = validate_delivery(delivery)
    root = Path(job_dir).absolute()
    # A controlled parent prevents directory replacement by another OS user.
    private_directory(root.parent)
    if not resume:
        root.mkdir(mode=0o700, exist_ok=False); u._fsync_dir(root.parent)
        fd = os.open(root / 'intake.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd); u._fsync_dir(root)
    private_directory(root); control(root / 'intake.lock')
    fd = os.open(root / 'intake.lock', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(set(p.name for p in root.iterdir()) <= {'intake.lock','manifest.json','snapshot','uploader'}, 'intake_unexpected_job_files')
        manifest = dict(schema=SCHEMA, target=destination, delivery_path=str(delivery),
                        snapshot_path=str(root / 'snapshot'), delivery=report)
        if resume:
            require(manifest_read(root / 'manifest.json') == manifest, 'intake_manifest_identity_changed')
            private_directory(root / 'uploader')
            # Missing state could follow a remote effect; never silently start over.
            control(root / 'uploader/uploader.json')
        else:
            create_snapshot(delivery, root / 'snapshot')
            (root / 'uploader').mkdir(mode=0o700)
            manifest_write(root / 'manifest.json', manifest)
        observed = inspect_snapshot(root / 'snapshot')
        require(observed['delivery'] == report, 'intake_snapshot_identity_changed')
        limits = limits or u.Limits()
        uploader = IntakeUploader(input=root / 'snapshot/audio.wav', session_id=session_id,
            capture_id=capture_id, capture_origin_frame=0, state_dir=root / 'uploader', final=True,
            api=api, limits=limits, transport=transport, cancel=cancel)
        contract = uploader.get('/sessions/' + session_id + '/recording-contract')
        check_contract(contract, session_id, report['frames'])
        result = uploader.run()
        # run() released its lock. The outer private job lock remains held, and
        # exclusive operator ownership is required throughout this final GET.
        uploader._saved_pages()
        require(result['frames'] == report['frames'], 'intake_uploaded_frame_mismatch')
        require(inspect_snapshot(root / 'snapshot') == observed and validate_delivery(delivery) == report,
                'intake_original_or_snapshot_changed')
        return dict(schema=SCHEMA, status='accepted_with_eos', analysis_completion_verified=False,
                    target_identity_authenticated=False, producer_identity_authenticated=False,
                    physical_continuity_proven=False, automatic_intake_enabled=False,
                    session_id=session_id, capture_id=capture_id, source_id=report['source_id'],
                    wav_sha256=report['wav_sha256'], frames=result['frames'], pages=result['pages'],
                    sources=result['sources'], job_dir=str(root))
    finally:
        os.close(fd)


def failure_report(exc):
    category = 'verification_or_local_state'
    detail = str(exc)
    extra = {}
    if isinstance(exc, u.HTTPFailure):
        extra['http_status'] = exc.status
        category = {401:'authentication_required',403:'access_refused',404:'target_missing',
                    409:'server_conflict',413:'server_limit',429:'server_capacity',507:'server_storage'}.get(exc.status, 'http_error')
    elif isinstance(exc, (u.Uncertain, OSError)):
        category = 'transport_or_local_io_uncertain'
    elif isinstance(exc, KeyboardInterrupt):
        category = 'operator_interrupted'
    elif isinstance(exc, u.Incomplete):
        if detail.startswith('Uploader deadline reached;'):
            category = 'deadline_pending_or_uncertain'
        for state in ('failed','paused','cancelled'):
            if detail.startswith('Admission blocked by worker ' + state + ':'):
                category = 'worker_' + state
    reason = detail if isinstance(exc, Refused) and re.fullmatch('[a-z0-9_]{1,128}', detail) else 'intake_not_verified_complete'
    return dict(schema=SCHEMA, status='unresolved', category=category, error_type=type(exc).__name__,
        reason=reason, possible_remote_effects=True, preserve_job_and_original=True,
        next_step='Inspect the exact job and server, including blocked workers; resume only this job. No inference retry is automatic. Do not create a replacement capture.', **extra)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery', required=True)
    parser.add_argument('--job-dir', required=True)
    parser.add_argument('--api', required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--capture-id', required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--exclusive-writer', action='store_true', required=True,
                        help='Assert no other writer uses this session/capture or job directory')
    parser.add_argument('--deadline-seconds', type=float, default=12*3600)
    args = vars(parser.parse_args(argv))
    deadline = args.pop('deadline_seconds')
    try:
        result = run(**args, limits=u.Limits(deadline_seconds=deadline))
    except (Exception, KeyboardInterrupt) as exc:
        # Never echo server bodies, authorization headers, local metadata or
        # exception text. An interrupted write/fsync may already have committed.
        result = failure_report(exc)
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
