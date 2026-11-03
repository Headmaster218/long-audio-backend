"""Explicit incremental recorder intake using the existing bounded v3 uploader.

One delivery per invocation. STOP does not implicitly finalize; --finalize is a
separate operation. Interrupted/gapped/epoch-changing declarations are refused.
"""
import argparse
import copy
from dataclasses import replace
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import shutil
import sys
import time

from . import recording_uploader as u
from . import recorder_intake as single
from . import recorder_initial_intent as initial
from .recorder_batch_intake import BoundaryRefused
from .recorder_delivery import inspect_delivery, Refused, parse_envelope, MAX_METADATA
from .recorder_metadata import decode
from .recorder_sequence import _record, _edge, _record_warnings
from .recorder_snapshot import create_snapshot, inspect_snapshot

SCHEMA = 'explicit-incremental-recorder-intake-v1'
BYTES = 8 * 1024**3
require = single.require


class CaptureUploader(u.AppendUploader):
    def __new__(cls, **kwargs):
        # No legacy-v2 dispatch may bypass this wrapper's stricter checks.
        return object.__new__(cls)

    def _match(self, row, remote):
        checked = copy.deepcopy(row)
        result = super()._match(checked, remote)
        row.clear(); row.update(checked)
        return result

    def _reconcile_capture(self):
        contract = self.get('/sessions/' + self.sid + '/recording-contract')
        capture = contract['capture']
        require(capture is None or (capture['capture_id'] == self.capture
                and capture['capture_origin_frame'] == self.origin), 'capture_foreign_session_capture')
        return super()._reconcile_capture()


def held_lock(path, create=False):
    if create:
        fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd); u._fsync_dir(path.parent)
    single.control(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd); raise


def validate_report(report):
    require(report['sample_rate_hz'] == 16000 and report['channels'] == 1, 'capture_requires_mono16k')
    require(report['close_reason'] in ('ROTATION', 'STOP'), 'capture_interrupted_or_recovered_tail_unresolved')


def validate_edge(previous, current):
    report = current['delivery']; validate_report(report)
    if previous is None:
        require(report['sequence'] == report['epoch'] == report['frame_start'] == 0
                and report['boundary'] == 'START' and report['trusted_previous_seam'] is False,
                'capture_requires_initial_start')
    else:
        require(previous['delivery']['capture_identity_sha256'] == report['capture_identity_sha256'],
                'capture_producer_identity_changed')
        edge = _edge(previous, current, set())
        if edge['reasons']:
            raise BoundaryRefused(edge['reasons'])


def candidate_record(path, report, index, remaining):
    remaining()
    fd=os.open(path/'segment.v1.bin',os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_METADATA, 'capture_candidate_metadata_bound')
        data=stream.read(MAX_METADATA+1)
    require(len(data) <= MAX_METADATA and hashlib.sha256(data).hexdigest()==report['metadata_sha256'],
            'capture_candidate_metadata_changed')
    local=parse_envelope(data,report,return_local=True)
    return dict(input_index=index,delivery=report,metadata=decode(local),local_metadata_sha256=hashlib.sha256(local).hexdigest())


def registry(root, header, remaining):
    """Read bounded immutable admissions in ordinal order, never repair them."""
    directory = root / 'admissions'; single.private_directory(directory)
    names = []
    with os.scandir(directory) as entries:
        for entry in entries:
            names.append(entry.name)
            require(len(names) <= header['max_inputs'], 'capture_admission_count')
    require(set(names) == {str(i) for i in range(len(names))}, 'capture_admission_ordinal_gap')
    receipts = []; total = 0
    # Bound cumulative declared WAV bytes before hashing historical media.
    for index in range(len(names)):
        parent = directory / str(index); single.private_directory(parent)
        require(set(p.name for p in parent.iterdir()) == {'snapshot','admission.json'}, 'capture_partial_admission_preserved')
        value = single.manifest_read(parent / 'admission.json')
        require(type(value) is dict and set(value) == {'schema','ordinal','target','path','snapshot_path','delivery'}
                and value['schema'] == SCHEMA and value['ordinal'] == index and value['target'] == header['target']
                and value['snapshot_path'] == str(parent / 'snapshot'), 'capture_admission_binding_changed')
        report = value['delivery']
        require(type(report['wav_bytes']) is int and report['wav_bytes'] > 44, 'capture_admission_byte_count')
        total += report['wav_bytes']; require(total <= BYTES, 'capture_original_byte_bound')
        receipts.append(value)
    records = []; frames = 0
    for index, receipt in enumerate(receipts):
        remaining()
        record = _record(Path(receipt['snapshot_path']), index, remaining)
        require(record['delivery'] == receipt['delivery'], 'capture_snapshot_binding_changed')
        require(inspect_delivery(receipt['path'], validate_metadata=True, deadline_seconds=remaining()) == receipt['delivery'],
                'capture_original_binding_changed')
        require(record['delivery']['sequence'] == index, 'capture_explicit_sequence_order')
        validate_edge(records[-1] if records else None, record)
        frames += record['delivery']['frames']; require(frames <= header['max_hours'] * 3600 * u.RATE, 'capture_duration_bound')
        records.append(record)
    return receipts, records


def durable_admission(receipt, remaining):
    """Re-establish positive fsync evidence for observed complete admission bytes.

    A prior failed fsync may leave readable canonical files. Reading them is not
    a durability proof, so retry must positively sync owned files/directories
    before allowing a new uploader registration or media request.
    """
    snapshot=Path(receipt['snapshot_path']);parent=snapshot.parent
    require(single.manifest_read(parent/'admission.json')==receipt,'capture_admission_changed_before_sync')
    for path in [snapshot/name for name in ('audio.wav','segment.v1.bin','delivery.v1.txt','snapshot.pending.json','snapshot.json')] + [parent/'admission.json']:
        remaining()
        fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode),'capture_admission_sync_regular')
            os.fsync(fd)
        finally:os.close(fd)
    for directory in (snapshot,parent,parent.parent):
        remaining();u._fsync_dir(directory)
    require(inspect_snapshot(snapshot,deadline_seconds=remaining())['delivery']==receipt['delivery']
            and single.manifest_read(parent/'admission.json')==receipt,'capture_admission_changed_after_sync')


def verify_prefix(uploader, receipts, header, *, recover_initial=False):
    d = uploader.journal.load()
    if d is None:
        require(not receipts or recover_initial, 'capture_missing_uploader_journal')
        return None, []
    expected = dict(api=header['target']['origin'], session_id=header['target']['session_id'],
                    capture_id=header['target']['capture_id'], origin=0, format=u.FORMAT)
    require(d['identity'] == expected and d['caps'] == dict(output_frames=header['max_hours']*3600*u.RATE,
            input_bytes=BYTES,max_inputs=header['max_inputs']), 'capture_uploader_target_or_caps_changed')
    entries = list(uploader._iter_inputs())
    require(len(entries) <= len(receipts) <= len(entries) + 1, 'capture_registry_prefix_count')
    start = 0
    for index, entry in enumerate(entries):
        receipt = receipts[index]; report = receipt['delivery']
        require(entry['id'] == report['source_id'] and entry['identity']['path'] == str(Path(receipt['snapshot_path']) / 'audio.wav')
                and entry['normalized_start'] == start and entry['frames'] <= report['frames'], 'capture_uploader_input_binding_changed')
        if entry['clean_eof']:
            require(entry['frames'] == report['frames'], 'capture_completed_input_length_changed')
        else:
            require(index == len(entries)-1 and len(receipts) == len(entries), 'capture_active_input_requires_exact_retry')
        start += report['frames']
    if d['finalize_requested']:
        require(len(entries) == len(receipts) and all(e['clean_eof'] for e in entries)
                and receipts and receipts[-1]['delivery']['close_reason'] == 'STOP', 'capture_premature_finalization')
    return d, entries


def check_capacity(d, report, header):
    emitted = d['emitted_frames'] if d else 0
    used = d['input_bytes'] if d else 0
    require(emitted + report['frames'] <= header['max_hours']*3600*u.RATE and used + report['wav_bytes'] <= BYTES,
            'capture_remaining_capacity')
    page = d['active_page'] if d else None
    pieces = page['pieces'] if page and not page['released'] else []
    remaining = min(u.PAGE_FRAMES - sum(p['frames'] for p in pieces), header['max_hours']*3600*u.RATE-emitted)
    require(len(pieces) + math.ceil(remaining/u.SOURCE_FRAMES) <= u.PAGE_SOURCES,
            'capture_source_slots_exhausted_no_automatic_eos')


def run(*, job_dir, api, session_id, capture_id, delivery=None, start=False, finalize=False,
        exclusive_writer=False, max_hours=None, max_inputs=None, limits=None, transport=None, cancel=None,
        recover_initial=False):
    require(exclusive_writer is True, 'intake_exclusive_writer_ack_required')
    require((delivery is not None) != bool(finalize) and not (start and finalize), 'capture_choose_delivery_or_finalize')
    require(type(recover_initial) is bool and not (recover_initial and (start or finalize)),
            'capture_recovery_requires_existing_initial_delivery')
    destination = single.target(api, 'operator-selected-unverified', session_id, capture_id, 0)
    root = Path(job_dir).absolute(); single.private_directory(root.parent)
    guard = u.Guard(limits or u.Limits(),cancel)
    def remaining():
        guard.check()
        return min(120,max(.000001,guard.end-time.monotonic()))
    if start:
        hours = 12 if max_hours is None else max_hours; count = u.APPEND_DEFAULT_INPUTS if max_inputs is None else max_inputs
        require(type(hours) is int and 1 <= hours <= 48 and type(count) is int and 1 <= count <= u.APPEND_MAX_INPUTS, 'capture_capacity_options')
        path=Path(delivery).absolute()
        report=inspect_delivery(path,validate_metadata=True,deadline_seconds=remaining())
        validate_edge(None,candidate_record(path,report,0,remaining))
        root.mkdir(mode=0o700,exist_ok=False); u._fsync_dir(root.parent)
    single.private_directory(root)
    outer = held_lock(root/'intake.lock',create=start)
    owner = None
    try:
        if start:
            header = dict(schema=SCHEMA,target=destination,max_hours=hours,max_inputs=count,input_bytes=BYTES,initial_intent=True)
            initial_value=initial.intent(root,header,path,report)
            single.manifest_write(root/initial.NAME,initial_value)
            # The immutable intent must be durable before any scaffold/admission.
            initial.complete_scaffold(root,initial_value,remaining)
        intent_recovery=recover_initial and os.path.lexists(root/initial.NAME)
        if intent_recovery:
            initial_value=initial.read(root,destination,delivery,remaining)
            header=initial_value['header']
        else:
            header=single.manifest_read(root/'capture.json')
        require(set(header) == {'schema','target','max_hours','max_inputs','input_bytes'} |
                ({'initial_intent'} if header.get('initial_intent') is True else set()) and header['schema'] == SCHEMA
                and header['target'] == destination and header['input_bytes'] == BYTES
                and type(header['max_hours']) is int and 1 <= header['max_hours'] <= 48
                and type(header['max_inputs']) is int and 1 <= header['max_inputs'] <= u.APPEND_MAX_INPUTS,
                'capture_header_identity_changed')
        require((max_hours is None or max_hours == header['max_hours']) and
                (max_inputs is None or max_inputs == header['max_inputs']), 'capture_frozen_capacity_changed')
        effective = replace(limits or u.Limits(), output_frames=header['max_hours']*3600*u.RATE,input_bytes=BYTES)
        guard.limits=effective
        http = transport or u.HTTPTransport(destination['origin'],guard)
        if intent_recovery:
            from .recorder_initial_snapshot import recover_snapshot
            require(header.get('initial_intent') is True,'capture_initial_intent_header')
            report=initial_value['delivery'];path=Path(initial_value['path'])
            validate_edge(None,candidate_record(path,report,0,remaining))
            require(report['frames']<=header['max_hours']*3600*u.RATE and report['wav_bytes']<=BYTES,
                    'capture_remaining_capacity')
            initial.scaffold_preflight(root,initial_value)
            recover_snapshot(path,root/'admissions/0/snapshot',report,remaining,validate_only=True)
            # Hold any existing uploader lock before observing/repairing state.
            lock=root/'uploader/uploader.lock'
            if os.path.lexists(lock):owner=held_lock(lock)
            contract=http.request('GET','/sessions/'+session_id+'/recording-contract')
            single.check_contract(contract,session_id,report['frames'])
            require(contract['capture'] is None and not contract['has_chunks'],'capture_existing_session_not_empty')
            reserve=effective.disk_floor+effective.spool_bytes+report['wav_bytes']+16*1024**2
            require(shutil.disk_usage(root).free>=reserve,'capture_snapshot_disk_reserve')
            initial.complete_file(root/initial.NAME,single._json(initial_value),remaining)
            initial.complete_scaffold(root,initial_value,remaining)
        expected={'capture.json','intake.lock','admissions','uploader'}
        if header.get('initial_intent') is True:
            expected.add(initial.NAME)
            initial_value=single.manifest_read(root/initial.NAME)
            require(initial_value==initial.intent(root,header,Path(initial_value['path']),initial_value['delivery']),
                    'capture_initial_intent_binding')
        require(set(p.name for p in root.iterdir()) == expected,'capture_unexpected_job_files')
        single.private_directory(root/'uploader')
        if owner is None:
            owner=held_lock(root/'uploader/uploader.lock',create=start or (intent_recovery and not os.path.lexists(root/'uploader/uploader.lock')))
        # Reject lost initial state, including an admitted first delivery whose
        # upload checkpoint never became durable. No implicit fresh adoption.
        if recover_initial:
            # Only an unstarted uploader can be reconstructed. No checkpoint,
            # temp checkpoint, history or media may be ignored or overwritten.
            require(set(p.name for p in (root/'uploader').iterdir()) == {'uploader.lock'},
                    'capture_recovery_requires_pristine_uploader')
        elif not start:
            single.control(root/'uploader/uploader.json')
        if intent_recovery:
            parent=root/'admissions/0'
            if not parent.exists():parent.mkdir(mode=0o700)
            recover_snapshot(path,parent/'snapshot',report,remaining)
            receipt=dict(schema=SCHEMA,ordinal=0,target=destination,path=str(path),snapshot_path=str(parent/'snapshot'),delivery=report)
            initial.complete_file(parent/'admission.json',single._json(receipt),remaining)
            u._fsync_dir(parent.parent)
        def make(**mode):
            inherited = u.InstanceLock(root/'uploader/uploader.lock').adopt(os.dup(owner))
            try:
                obj = CaptureUploader(**mode,session_id=session_id,capture_id=capture_id,capture_origin_frame=0,
                    state_dir=root/'uploader',api=destination['origin'],max_hours=header['max_hours'],
                    max_inputs=header['max_inputs'],max_original_bytes=BYTES,limits=effective,
                    transport=http,cancel=cancel,_inherited_lock=inherited)
                obj.guard=guard;obj.journal.guard=guard
                return obj
            except BaseException:
                inherited.release();raise
        receipts, records = registry(root,header,remaining)
        if header.get('initial_intent') is True and receipts:
            require(receipts[0]['path']==initial_value['path'] and receipts[0]['delivery']==initial_value['delivery'],
                    'capture_initial_admission_intent_changed')
        if recover_initial:
            require(len(receipts) == 1 and receipts[0]['ordinal'] == 0
                    and receipts[0]['path'] == str(Path(delivery).absolute()),
                    'capture_recovery_requires_exact_initial_admission')
        probe = make(finalize=True)
        try:
            d, entries = verify_prefix(probe,receipts,header,recover_initial=recover_initial)
            contract = probe.get('/sessions/'+session_id+'/recording-contract')
            if d is None:
                require(contract['capture'] is None and not contract['has_chunks'], 'capture_existing_session_not_empty')
            else:
                probe._reconcile_capture()
            if finalize:
                require(receipts and receipts[-1]['delivery']['close_reason'] == 'STOP', 'capture_stop_required_before_finalize')
                require(len(entries)==len(receipts) and all(e['clean_eof'] for e in entries), 'capture_pending_delivery_before_finalize')
                single.check_contract(contract,session_id,max(r['delivery']['frames'] for r in receipts))
                mode=dict(finalize=True)
            else:
                path=Path(delivery).absolute()
                report=inspect_delivery(path,validate_metadata=True,deadline_seconds=remaining());validate_report(report)
                single.check_contract(contract,session_id,report['frames'])
                index=report['sequence']
                if index < len(receipts):
                    require(index == len(receipts)-1 and receipts[index]['path']==str(path)
                            and receipts[index]['delivery']==report, 'capture_retry_requires_exact_latest_delivery')
                    require(not d or not d['finalize_requested'], 'capture_finalization_fenced_use_finalize')
                    receipt=receipts[index]
                else:
                    require(index==len(receipts) and index < header['max_inputs'], 'capture_next_sequence_required')
                    require(len(entries)==len(receipts) and all(e['clean_eof'] for e in entries), 'capture_pending_delivery_requires_retry')
                    require(not d or not d['finalize_requested'], 'capture_finalization_fenced_use_finalize')
                    check_capacity(d,report,header)
                    candidate=candidate_record(path,report,index,remaining)
                    validate_edge(records[-1] if records else None,candidate)
                    reserve=effective.disk_floor+effective.spool_bytes+report['wav_bytes']+16*1024**2
                    require(shutil.disk_usage(root).free >= reserve,'capture_snapshot_disk_reserve')
                    # Validate candidate metadata+adjacency before publishing its
                    # receipt. Ordinary append never repairs a partial snapshot;
                    # explicit initial recovery requires its durable intent.
                    parent=root/'admissions'/str(index);parent.mkdir(mode=0o700)
                    snapshot=parent/'snapshot';create_snapshot(path,snapshot,deadline_seconds=remaining())
                    record=_record(snapshot,index,remaining)
                    require(record==candidate,'capture_candidate_changed')
                    validate_edge(records[-1] if records else None,record)
                    receipt=dict(schema=SCHEMA,ordinal=index,target=destination,path=str(path),snapshot_path=str(snapshot),delivery=report)
                    single.manifest_write(parent/'admission.json',receipt);u._fsync_dir(parent.parent)
                    receipts.append(receipt);records.append(record)
                mode=dict(append=True,input=Path(receipt['snapshot_path'])/'audio.wav',input_id=report['source_id'],
                          input_start_frame=report['frame_start'])
        finally:
            probe.journal.lock.release()
        for receipt in receipts:
            durable_admission(receipt,remaining)
        uploader=make(**mode)
        result=uploader.run()
        checker=make(finalize=True)
        try:
            verify_prefix(checker,receipts,header)
            checker._reconcile_capture()
        finally:
            checker.journal.lock.release()
        after, _ = registry(root,header,remaining)
        require(after==receipts,'capture_admission_changed_after_upload')
        # Published history and staged media remain separate in this result.
        return dict(schema=SCHEMA,status='accepted_with_eos' if result['capture_finalized'] else 'delivery_staged_without_eos',
            analysis_completion_verified=False,automatic_intake_enabled=False,physical_continuity_proven=False,
            hardware_frame_mapping='UNESTABLISHED',producer_identity_authenticated=False,target_identity_authenticated=False,
            session_id=session_id,capture_id=capture_id,frames=result['frames'],published_frames=result['published_frames'],
            staged_frames=result['staged_frames'],inputs=result['inputs'],pages=result['pages'],sources=result['sources'],
            capture_finalized=result['capture_finalized'],warning_codes=sorted({w for record in records for w in _record_warnings(record['metadata'])}),
            max_hours=header['max_hours'],max_inputs=header['max_inputs'],job_dir=str(root))
    finally:
        if owner is not None:os.close(owner)
        os.close(outer)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('job-dir','api','session-id','capture-id'):parser.add_argument('--'+name,required=True)
    group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--delivery');group.add_argument('--finalize',action='store_true')
    parser.add_argument('--recover-initial',action='store_true',
                        help='Explicit initial recovery before any uploader checkpoint; exact partial prefixes require durable initial intent')
    parser.add_argument('--start',action='store_true');parser.add_argument('--exclusive-writer',action='store_true',required=True)
    parser.add_argument('--max-hours',type=int);parser.add_argument('--max-inputs',type=int)
    parser.add_argument('--deadline-seconds',type=float,default=12*3600)
    args=vars(parser.parse_args(argv));deadline=args.pop('deadline_seconds')
    try:result=run(**args,limits=u.Limits(deadline_seconds=deadline))
    except (Exception,KeyboardInterrupt) as exc:
        result=single.failure_report(exc);result['schema']=SCHEMA
        if isinstance(exc,BoundaryRefused):result['boundary_reason_codes']=exc.reasons
        print(json.dumps(result,sort_keys=True),file=sys.stderr);return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
