"""Explicit fixed batch of declared-adjacent recorder segments, not a watcher.

All inputs and the final STOP must be present before sending media. Missing
ordinals, epoch changes and uncertain boundaries are refused, never repaired.
"""
import argparse
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import sys
import stat

from . import recording_uploader as u
from . import recorder_intake as single
from .recorder_delivery import inspect_delivery, Refused
from .recorder_sequence import reconcile_snapshots
from .recorder_snapshot import create_snapshot, inspect_snapshot, _deadline

SCHEMA = 'explicit-recorder-batch-intake-v1'
MAX_INPUTS = 8
require = single.require


class BoundaryRefused(Refused):
    def __init__(self, reasons):
        super().__init__('batch_unresolved_boundary')
        self.reasons = sorted(set(reasons))


def capacity_plan(lengths):
    """Mirror v2 append's pending-page reservation without reading PCM or HTTP."""
    frames = slots = 0
    for length in lengths:
        require(slots + math.ceil((u.PAGE_FRAMES - frames) / u.SOURCE_FRAMES) <= u.PAGE_SOURCES,
                'batch_pending_page_source_capacity')
        left = length
        while left:
            if frames == u.PAGE_FRAMES:
                frames = slots = 0
            piece = min(left, u.SOURCE_FRAMES, u.PAGE_FRAMES - frames)
            frames += piece; left -= piece; slots += 1
            require(slots <= u.PAGE_SOURCES, 'batch_pending_page_source_capacity')


def check_sequence(paths, reports, *, deadline_seconds=120):
    observed = reconcile_snapshots(paths, deadline_seconds=deadline_seconds)
    require(len(observed['captures']) == 1, 'batch_multiple_capture_identities')
    capture = observed['captures'][0]
    require(not capture['predecessor_not_supplied'] and not capture['conflicting_ordinals']
            and not capture['duplicates'], 'batch_missing_prefix_or_duplicate')
    reasons = [reason for edge in capture['edges'] for reason in edge['reasons']]
    if reasons:
        raise BoundaryRefused(reasons)
    first = reports[0]
    require(first['sequence'] == first['epoch'] == first['frame_start'] == 0
            and first['boundary'] == 'START' and first['trusted_previous_seam'] is False,
            'batch_requires_initial_start')
    require([r['sequence'] for r in reports] == list(range(len(reports))), 'batch_explicit_sequence_order')
    require(reports[-1]['close_reason'] == 'STOP', 'batch_requires_final_stop')
    require(all(r['sample_rate_hz'] == 16000 and r['channels'] == 1 for r in reports),
            'batch_requires_mono16k')
    lengths = [r['frames'] for r in reports]
    require(0 < sum(lengths) <= 2 * 3600 * u.RATE, 'batch_two_hour_bound')
    capacity_plan(lengths)
    return observed


class BatchUploader(u.LegacyAppendUploader):
    def _reconcile_capture(self):
        contract = self.get('/sessions/' + self.sid + '/recording-contract')
        capture = contract['capture']
        require(capture is None or (capture['capture_id'] == self.capture
                and capture['capture_origin_frame'] == self.origin), 'batch_foreign_session_capture')
        return super()._reconcile_capture()

    def _match(self, row, remote):
        checked = copy.deepcopy(row)
        result = super()._match(checked, remote)
        row.clear(); row.update(checked)
        return result


def resume_index(uploader, reports, snapshots):
    """Resume the active input first, never replay a different completed input."""
    d = uploader.journal.load()
    require(d is not None, 'batch_missing_uploader_journal')
    require(len(d['inputs']) <= len(reports), 'batch_journal_input_count')
    start = 0; active = None
    for index, entry in enumerate(d['inputs']):
        report = reports[index]
        require(entry['id'] == report['source_id'] and entry['identity']['path'] == str(snapshots[index] / 'audio.wav')
                and entry['normalized_start'] == start and entry['frames'] <= report['frames'],
                'batch_journal_input_binding_changed')
        if entry['clean_eof']:
            require(entry['frames'] == report['frames'], 'batch_journal_completed_length_changed')
        else:
            require(index == len(d['inputs']) - 1, 'batch_journal_nonfinal_active_input')
            active = index
        start += report['frames']
    if d['finalize_requested']:
        require(active is None and len(d['inputs']) == len(reports), 'batch_premature_finalization')
        return len(reports)
    return active if active is not None else len(d['inputs'])


def run(*, deliveries, job_dir, api, session_id, capture_id, resume=False,
        exclusive_writer=False, limits=None, transport=None, cancel=None):
    require(exclusive_writer is True, 'intake_exclusive_writer_ack_required')
    require(type(deliveries) in (list, tuple) and 2 <= len(deliveries) <= MAX_INPUTS, 'batch_input_count_2_to_8')
    destination = single.target(api, 'operator-selected-unverified', session_id, capture_id, 0)
    inputs = [Path(p).absolute() for p in deliveries]
    require(len(set(inputs)) == len(inputs), 'batch_duplicate_delivery_path')
    sizes = [os.stat(p / 'audio.wav', follow_symlinks=False) for p in inputs]
    require(all(stat.S_ISREG(info.st_mode) and info.st_size > 44 for info in sizes)
            and sum(info.st_size for info in sizes) <= 512 * 1024 * 1024, 'batch_input_byte_bound')
    remaining = _deadline(120)
    reports = [inspect_delivery(p, validate_metadata=True, deadline_seconds=remaining()) for p in inputs]
    require(sum(r['wav_bytes'] for r in reports) <= 512 * 1024 * 1024, 'batch_input_byte_bound')
    limits = limits or u.Limits()
    require(sum(r['frames'] for r in reports) <= limits.output_frames
            and sum(r['wav_bytes'] for r in reports) <= limits.input_bytes, 'batch_configured_input_limit')
    root = Path(job_dir).absolute(); single.private_directory(root.parent)
    if not resume:
        root.mkdir(mode=0o700, exist_ok=False); u._fsync_dir(root.parent)
        fd = os.open(root / 'intake.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd); u._fsync_dir(root)
    single.private_directory(root); single.control(root / 'intake.lock')
    fd = os.open(root / 'intake.lock', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(set(p.name for p in root.iterdir()) <= {'intake.lock','manifest.json','snapshots','uploader'}, 'batch_unexpected_job_files')
        snapshots = [root / 'snapshots' / str(index) for index in range(len(inputs))]
        manifest = dict(schema=SCHEMA, target=destination, inputs=[dict(path=str(path), snapshot_path=str(snap), delivery=report)
            for path, snap, report in zip(inputs, snapshots, reports)])
        if resume:
            require(single.manifest_read(root / 'manifest.json') == manifest, 'batch_manifest_identity_changed')
            single.private_directory(root / 'snapshots'); single.private_directory(root / 'uploader')
            single.control(root / 'uploader/uploader.json')
            require(set(p.name for p in (root / 'snapshots').iterdir()) == set(map(str, range(len(inputs)))), 'batch_snapshot_inventory')
        else:
            (root / 'snapshots').mkdir(mode=0o700)
            for path, snap in zip(inputs, snapshots):
                create_snapshot(path, snap, deadline_seconds=remaining())
            (root / 'uploader').mkdir(mode=0o700)
        observed = check_sequence(snapshots, reports, deadline_seconds=remaining())
        require([inspect_snapshot(s, deadline_seconds=remaining())['delivery'] for s in snapshots] == reports, 'batch_snapshot_identity_changed')
        if not resume:
            single.manifest_write(root / 'manifest.json', manifest)
        # One shared guard bounds the whole batch, not a fresh deadline per input.
        guard = u.Guard(limits, cancel)
        http = transport or u.HTTPTransport(destination['origin'], guard)
        common = dict(session_id=session_id, capture_id=capture_id, capture_origin_frame=0,
            state_dir=root / 'uploader', api=destination['origin'], max_inputs=len(inputs),
            limits=limits, transport=http, cancel=cancel)
        probe = BatchUploader(finalize=True, **common); probe.guard = guard; probe.journal.guard = guard
        contract = probe.get('/sessions/' + session_id + '/recording-contract')
        single.check_contract(contract, session_id, sum(r['frames'] for r in reports))
        index = resume_index(probe, reports, snapshots) if resume else 0
        starts = []; cursor = 0
        for report in reports:
            starts.append(cursor); cursor += report['frames']
        for position in range(index, len(inputs)):
            uploader = BatchUploader(append=True, input=snapshots[position] / 'audio.wav',
                input_id=reports[position]['source_id'], input_start_frame=starts[position], **common)
            uploader.guard = guard; uploader.journal.guard = guard
            accepted = uploader.run()
            require(accepted['input_complete'], 'batch_input_not_complete')
        finalizer = BatchUploader(finalize=True, **common)
        finalizer.guard = guard; finalizer.journal.guard = guard
        result = finalizer.run()
        require(result['capture_finalized'] and result['frames'] == cursor, 'batch_finalization_not_verified')
        finalizer._reconcile_capture()
        remaining = _deadline(120)
        require(check_sequence(snapshots, reports, deadline_seconds=remaining()) == observed and
                [inspect_snapshot(s, deadline_seconds=remaining())['delivery'] for s in snapshots] == reports and
                [inspect_delivery(p, validate_metadata=True, deadline_seconds=remaining()) for p in inputs] == reports,
                'batch_original_or_snapshot_changed')
        return dict(schema=SCHEMA, status='accepted_with_eos', analysis_completion_verified=False,
            automatic_intake_enabled=False, physical_continuity_proven=False,
            target_identity_authenticated=False, producer_identity_authenticated=False,
            session_id=session_id, capture_id=capture_id, frames=cursor, inputs=len(inputs),
            source_ids=[r['source_id'] for r in reports], pages=result['pages'], sources=result['sources'],
            declared_boundary_count=len(inputs)-1, warning_codes=observed['captures'][0]['warning_codes'], job_dir=str(root))
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery', dest='deliveries', action='append', required=True,
                        help='Repeat in sequence order for the complete fixed batch (2..8)')
    for name in ('job-dir','api','session-id','capture-id'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--exclusive-writer', action='store_true', required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--deadline-seconds', type=float, default=12*3600)
    args = vars(parser.parse_args(argv)); deadline = args.pop('deadline_seconds')
    try:
        result = run(**args, limits=u.Limits(deadline_seconds=deadline))
    except (Exception, KeyboardInterrupt) as exc:
        result = single.failure_report(exc); result['schema'] = SCHEMA
        if isinstance(exc, BoundaryRefused): result['boundary_reason_codes'] = exc.reasons
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
