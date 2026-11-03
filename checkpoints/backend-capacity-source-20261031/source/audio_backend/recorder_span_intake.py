"""Explicit bounded recorder span intake into a separate known-empty session.

EOS closes only the selected span. It never asserts recorder STOP, complete
capture, a gap duration, physical continuity, or a global hardware timeline.
No watcher, session creation, resampling, padding, or inference retry.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import sys

from . import recorder_batch_intake as batch
from . import recorder_intake as single
from . import recording_uploader as u
from .recorder_delivery import inspect_delivery, Refused
from .recorder_sequence import reconcile_snapshots
from .recorder_snapshot import create_snapshot, inspect_snapshot, _deadline

SCHEMA = 'explicit-recorder-span-intake-v1'
MAX_INPUTS = 8
MAX_INPUT_BYTES = 512 * 1024 * 1024
require = single.require
SpanUploader = batch.BatchUploader


class BoundaryRefused(Refused):
    def __init__(self, reasons):
        super().__init__('span_unresolved_boundary')
        self.reasons = sorted(set(reasons))


def check_sequence(paths, reports, *, deadline_seconds=120):
    """Accept one declaration-contiguous selection, without requiring its prefix."""
    observed = reconcile_snapshots(paths, deadline_seconds=deadline_seconds)
    require(len(observed['captures']) == 1, 'span_multiple_capture_identities')
    capture = observed['captures'][0]
    require(not capture['conflicting_ordinals'] and not capture['duplicates'],
            'span_conflicting_or_duplicate_ordinal')
    reasons = [reason for edge in capture['edges'] for reason in edge['reasons']]
    if reasons:
        raise BoundaryRefused(reasons)
    first = reports[0]['sequence']
    require([r['sequence'] for r in reports] == list(range(first, first + len(reports))),
            'span_explicit_sequence_order')
    require(reports[-1]['close_reason'] in ('STOP', 'INTERRUPTION', 'RECOVERED_TAIL'),
            'span_terminal_closure_required')
    # _edge already refuses recovered-tail adjacency. Keep this explicit so a
    # future change in diagnostic reconciliation cannot quietly join such data.
    require(all(r['close_reason'] != 'RECOVERED_TAIL' for r in reports) or len(reports) == 1,
            'span_recovered_tail_requires_singleton')
    require(all(r['sample_rate_hz'] == u.RATE and r['channels'] == 1 for r in reports),
            'span_requires_mono16k')
    require(0 < sum(r['frames'] for r in reports) <= 2 * 3600 * u.RATE,
            'span_two_hour_bound')
    batch.capacity_plan([r['frames'] for r in reports])
    return observed


def span_description(reports):
    """Original coordinates remain evidence, never server capture coordinates."""
    first, last = reports[0], reports[-1]
    return dict(
        coordinate_system='span_local_frames', local_start_frame=0,
        local_end_frame=sum(r['frames'] for r in reports),
        original_capture_identity_sha256=first['capture_identity_sha256'],
        original_epoch=first['epoch'], original_first_sequence=first['sequence'],
        original_last_sequence=last['sequence'],
        original_epoch_frame_start=first['frame_start'],
        original_epoch_frame_end=last['frame_start'] + last['frames'],
        original_close_reason=last['close_reason'],
        eos_meaning='selected_span_only', recorder_stop_observed=last['close_reason'] == 'STOP',
        recovered_tail_uncertain=last['close_reason'] == 'RECOVERED_TAIL',
        capture_completeness_proven=False, physical_continuity_proven=False,
        hardware_frame_mapping='UNESTABLISHED',
        before_span_gap_frames=None, after_span_gap_frames=None)


def _held_lock(path, *, create=False):
    if create:
        fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd); u._fsync_dir(path.parent)
    single.control(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def run(*, deliveries, job_dir, api, session_id, capture_id, resume=False,
        exclusive_writer=False, acknowledge_span_local_closure=False,
        limits=None, transport=None, cancel=None):
    require(exclusive_writer is True, 'intake_exclusive_writer_ack_required')
    require(acknowledge_span_local_closure is True, 'span_local_closure_ack_required')
    require(type(deliveries) in (list, tuple) and 1 <= len(deliveries) <= MAX_INPUTS,
            'span_input_count_1_to_8')
    destination = single.target(api, 'operator-selected-unverified', session_id, capture_id, 0)
    inputs = [Path(p).absolute() for p in deliveries]
    require(len(set(inputs)) == len(inputs), 'span_duplicate_delivery_path')
    sizes = [os.stat(p / 'audio.wav', follow_symlinks=False) for p in inputs]
    require(all(stat.S_ISREG(s.st_mode) and s.st_size > 44 for s in sizes)
            and sum(s.st_size for s in sizes) <= MAX_INPUT_BYTES, 'span_input_byte_bound')
    remaining = _deadline(120)
    reports = [inspect_delivery(p, validate_metadata=True, deadline_seconds=remaining()) for p in inputs]
    require(sum(r['wav_bytes'] for r in reports) <= MAX_INPUT_BYTES, 'span_input_byte_bound')
    limits = limits or u.Limits()
    require(sum(r['frames'] for r in reports) <= limits.output_frames
            and sum(r['wav_bytes'] for r in reports) <= limits.input_bytes,
            'span_configured_input_limit')
    root = Path(job_dir).absolute()
    single.private_directory(root.parent)
    if not resume:
        root.mkdir(mode=0o700, exist_ok=False); u._fsync_dir(root.parent)
    single.private_directory(root)
    outer = _held_lock(root / 'intake.lock', create=not resume)
    owner = None
    try:
        expected = {'intake.lock', 'manifest.json', 'snapshots', 'uploader'}
        require(set(p.name for p in root.iterdir()) <= expected, 'span_unexpected_job_files')
        snapshots = [root / 'snapshots' / str(index) for index in range(len(inputs))]
        starts = []; cursor = 0
        for report in reports:
            starts.append(cursor); cursor += report['frames']
        description = span_description(reports)
        manifest = dict(schema=SCHEMA, target=destination, span=description,
            acknowledgments=dict(exclusive_writer=True, span_local_closure=True),
            inputs=[dict(path=str(path), snapshot_path=str(snap),
                         span_start_frame=start, delivery=report)
                    for path, snap, start, report in zip(inputs, snapshots, starts, reports)])
        if resume:
            require(set(p.name for p in root.iterdir()) == expected, 'span_incomplete_job')
            require(single.manifest_read(root / 'manifest.json') == manifest,
                    'span_manifest_identity_changed')
            single.private_directory(root / 'snapshots')
            single.private_directory(root / 'uploader')
            owner = _held_lock(root / 'uploader/uploader.lock')
            # No adoption, implicit new uploader, or reconstruction of missing
            # initial state after an uncertain effect, even if the target looks empty.
            single.control(root / 'uploader/uploader.json')
            require(set(p.name for p in (root / 'snapshots').iterdir()) ==
                    set(map(str, range(len(inputs)))), 'span_snapshot_inventory')
        else:
            (root / 'snapshots').mkdir(mode=0o700)
            for path, snap in zip(inputs, snapshots):
                create_snapshot(path, snap, deadline_seconds=remaining())
            (root / 'uploader').mkdir(mode=0o700)
            owner = _held_lock(root / 'uploader/uploader.lock', create=True)
        observed = check_sequence(snapshots, reports, deadline_seconds=remaining())
        require([inspect_snapshot(s, deadline_seconds=remaining())['delivery'] for s in snapshots] == reports,
                'span_snapshot_identity_changed')
        if not resume:
            # Canonical, exclusive, fsynced manifest precedes ALL network effects.
            single.manifest_write(root / 'manifest.json', manifest)
        guard = u.Guard(limits, cancel)
        http = transport or u.HTTPTransport(destination['origin'], guard)
        def make(**mode):
            inherited = u.InstanceLock(root / 'uploader/uploader.lock').adopt(os.dup(owner))
            try:
                obj = SpanUploader(**mode, session_id=session_id, capture_id=capture_id,
                    capture_origin_frame=0, state_dir=root / 'uploader', api=destination['origin'],
                    max_inputs=len(inputs), limits=limits, transport=http, cancel=cancel,
                    _inherited_lock=inherited)
                obj.guard = guard; obj.journal.guard = guard
                return obj
            except BaseException:
                inherited.release()
                raise
        probe = make(finalize=True)
        try:
            index = batch.resume_index(probe, reports, snapshots) if resume else 0
            contract = probe.get('/sessions/' + session_id + '/recording-contract')
            single.check_contract(contract, session_id, cursor)
            if not resume:
                require(contract.get('capture', False) is None and contract.get('has_chunks') is False,
                        'span_existing_session_not_empty')
        finally:
            probe.journal.lock.release()
        for position in range(index, len(inputs)):
            uploader = make(append=True, input=snapshots[position] / 'audio.wav',
                input_id=reports[position]['source_id'], input_start_frame=starts[position])
            accepted = uploader.run()
            require(accepted['input_complete'], 'span_input_not_complete')
        finalizer = make(finalize=True)
        result = finalizer.run()
        require(result['capture_finalized'] and result['frames'] == cursor,
                'span_finalization_not_verified')
        # The outer inherited uploader lock still excludes direct uploader users.
        checker = make(finalize=True)
        try:
            batch.resume_index(checker, reports, snapshots)
            checker._reconcile_capture()
        finally:
            checker.journal.lock.release()
        remaining = _deadline(120)
        require(check_sequence(snapshots, reports, deadline_seconds=remaining()) == observed and
                [inspect_snapshot(s, deadline_seconds=remaining())['delivery'] for s in snapshots] == reports and
                [inspect_delivery(p, validate_metadata=True, deadline_seconds=remaining()) for p in inputs] == reports,
                'span_original_or_snapshot_changed')
        return dict(schema=SCHEMA, status='selected_span_accepted_with_eos', span=description,
            analysis_completion_verified=False, automatic_intake_enabled=False,
            physical_continuity_proven=False, capture_completeness_proven=False,
            target_identity_authenticated=False, producer_identity_authenticated=False,
            session_id=session_id, capture_id=capture_id, frames=cursor, inputs=len(inputs),
            source_ids=[r['source_id'] for r in reports], pages=result['pages'], sources=result['sources'],
            declared_boundary_count=len(inputs)-1,
            warning_codes=observed['captures'][0]['warning_codes'], job_dir=str(root))
    finally:
        if owner is not None: os.close(owner)
        os.close(outer)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery', dest='deliveries', action='append', required=True,
                        help='Repeat in original sequence order for this fixed span (1..8)')
    for name in ('job-dir', 'api', 'session-id', 'capture-id'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--exclusive-writer', action='store_true', required=True)
    parser.add_argument('--acknowledge-span-local-closure', action='store_true', required=True,
                        help='EOS closes this selected span only, not the whole recorder capture')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--deadline-seconds', type=float, default=12*3600)
    args = vars(parser.parse_args(argv)); deadline = args.pop('deadline_seconds')
    try:
        result = run(**args, limits=u.Limits(deadline_seconds=deadline))
    except (Exception, KeyboardInterrupt) as exc:
        result = single.failure_report(exc); result['schema'] = SCHEMA
        result.update(eos_meaning='selected_span_only', capture_completeness_proven=False)
        if isinstance(exc, BoundaryRefused): result['boundary_reason_codes'] = exc.reasons
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
