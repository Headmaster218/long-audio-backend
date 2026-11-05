"""One finite GET-only observation of an existing finalized incremental span.

Only explicit incremental-span headers and append-v3 histories are accepted.
No adoption, migration, intake replay, journal repair or worker retry is possible.
EOS remains selected-span-only; observations are not an atomic server snapshot.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import wave

from . import recorder_capture_intake as engine
from . import recorder_incremental_span_intake as span
from . import recorder_initial_intent as initial
from . import recorder_intake as single
from . import recorder_span_status as observer
from . import recording_uploader as u
from . import recording_uploader_history as history
from .recorder_delivery import Refused
from .recorder_snapshot import _json, _open_directory, _receipt

SCHEMA = 'recorder-incremental-span-status-v1'
MAX_PAGES = 588
MAX_WORK_CHUNKS = 16464
MAX_RESPONSE_BYTES = 256 * 1024 * 1024
require = single.require
NotReady = observer.NotReady


def _control(path, guard, *, journal=False):
    raw, identity = observer._read(path, u.JSON_BYTES if journal else single.MAX_MANIFEST, guard)
    value = u._parse(raw)
    require((u._json(value) if journal else _json(value)) == raw,
            'incremental_span_status_control_noncanonical')
    return value, (raw, identity)


def _header(value, destination):
    require(set(value) == {'schema', 'target', 'max_hours', 'max_inputs', 'input_bytes',
                          'initial_intent', 'span_origin', 'acknowledgments'} and
            value['schema'] == span.SCHEMA and u._json(value['target']) == u._json(destination) and
            type(value['max_hours']) is int and 1 <= value['max_hours'] <= 48 and
            type(value['max_inputs']) is int and 1 <= value['max_inputs'] <= history.MAX_INPUTS and
            type(value['input_bytes']) is int and value['input_bytes'] == engine.BYTES,
            'incremental_span_status_header_changed')
    span.POLICY.validate_header(value)


def _local(root, destination, guard):
    """Validate originals, admissions and snapshots without changing a byte."""
    observer._inventory(root, {'capture.json', initial.NAME, 'intake.lock', 'admissions', 'uploader'} | span.POLICY.extra_job_files(root))
    for path in (root, root / 'admissions', root / 'uploader'):
        single.private_directory(path)
    header, header_identity = _control(root / 'capture.json', guard)
    _header(header, destination)
    intent, intent_identity = _control(root / initial.NAME, guard)
    names = set()
    with os.scandir(root / 'admissions') as entries:
        for entry in entries:
            names.add(entry.name)
            require(len(names) <= header['max_inputs'], 'incremental_span_status_input_count')
    require(names == set(map(str, range(len(names)))) and names,
            'incremental_span_status_admission_order')
    controls = []; paths = []; snapshots = []; declared_bytes = declared_frames = 0
    directories = [root, root / 'admissions', root / 'uploader']
    for index in range(len(names)):
        parent = root / 'admissions' / str(index)
        single.private_directory(parent)
        observer._inventory(parent, {'admission.json', 'snapshot'})
        receipt, identity = _control(parent / 'admission.json', guard)
        require(type(receipt.get('ordinal')) is int and receipt['ordinal'] == index,
                'incremental_span_status_admission_ordinal')
        report = receipt['delivery']
        require(type(report.get('wav_bytes')) is int and report['wav_bytes'] > 44 and
                type(report.get('frames')) is int and report['frames'] > 0,
                'incremental_span_status_input_size')
        declared_bytes += report['wav_bytes']; declared_frames += report['frames']
        require(declared_bytes <= min(engine.BYTES, guard.limits.input_bytes) and
                declared_frames <= min(header['max_hours'] * 3600 * u.RATE, guard.limits.output_frames),
                'incremental_span_status_input_bound')
        path = Path(receipt['path']); snapshot = parent / 'snapshot'
        require(path.is_absolute() and path == path.resolve(strict=True) and path not in paths,
                'incremental_span_status_original_path')
        paths.append(path); snapshots.append(snapshot); controls.append(identity)
        single.private_directory(snapshot); directories.extend((parent, snapshot))
    # Check actual aggregate WAV sizes before any expensive inspector hashing.
    for sources in (paths, snapshots):
        sizes = [os.stat(path / 'audio.wav', follow_symlinks=False).st_size for path in sources]
        require(sum(sizes) == declared_bytes and sum(sizes) <= min(engine.BYTES, guard.limits.input_bytes),
                'incremental_span_status_actual_byte_bound')
    remaining = lambda: observer._remaining(guard)
    receipts, records = engine.registry(root, header, remaining, policy=span.POLICY)
    require(len(receipts) == len(names), 'incremental_span_status_admissions_changed')
    for index, (receipt, record, snapshot) in enumerate(zip(receipts, records, snapshots)):
        require(_json(receipt) == controls[index][0] and
                u._json(receipt['delivery']) == u._json(record['delivery']),
                'incremental_span_status_admission_changed')
        fd = _open_directory(snapshot)
        try:
            snapshot_receipt, _ = _receipt(fd, remaining)
        finally:
            os.close(fd)
        require(u._json(snapshot_receipt['body']['delivery']) == u._json(record['delivery']),
                'incremental_span_status_snapshot_receipt_changed')
    require(u._json(intent) == u._json(initial.intent(root, header, paths[0], records[0]['delivery'])),
            'incremental_span_status_initial_intent_changed')
    logical_policy = span.POLICY.for_job(root,header,receipts,records,remaining)
    controls.append(logical_policy.logical_boundary)
    identities = [u._signature(path.lstat()) for path in directories]
    return header, receipts, records, (header_identity, intent_identity, controls, identities)


class _ReadOnlyJournal(history.PagedJournal):
    """Only existing pure validators/readers are used; never call __init__/load."""
    def _read(self, path, cap):
        single.control(path)
        raw = super()._read(path, cap)
        require(u._json(u._parse(raw)) == raw, 'incremental_span_status_archive_noncanonical')
        identity = u._signature(path.lstat())
        require(path not in self.observed_files or self.observed_files[path] == identity,
                'incremental_span_status_archive_replaced')
        self.observed_files[path] = identity
        return raw


class _Pages:
    """A bounded, repeatedly authenticated view, not a retained page registry."""
    def __init__(self, journal): self.journal = journal
    def __len__(self): return self.journal.data['page_count']
    def __iter__(self): return self.journal.iter_pages()


def _inventory_history(journal):
    d = journal.data
    journal.guard.check(); journal._check_directories()
    single.private_directory(journal.path); single.private_directory(journal.history)
    observer._inventory(journal.path, {'uploader.lock', 'uploader.json', 'uploader.history'})
    observer._inventory(journal.history, {
        *(f'input-{i:04d}.json' for i in range(1, d['inputs_done'] + 1)),
        *(f'page-{i:04d}.json' for i in range(1, d['pages_done'] + 1))})
    # Preserve the uploader's allocated metadata/state ceilings without invoking
    # its census/budget lifecycle or requiring free disk for a read-only action.
    total = sum(journal._allocated(path.stat()) for path in (journal.path, journal.history))
    names = [journal.path / 'uploader.lock', journal.path / 'uploader.json']
    names.extend(journal.archive_path(kind, number) for kind, count in
                 (('input', d['inputs_done']), ('page', d['pages_done'])) for number in range(1, count + 1))
    allowance = 2 * u.JSON_BYTES + history.PAGE_ARCHIVE_BYTES + 65536
    for path in names:
        journal.guard.check(); single.control(path)
        total += journal._allocated(journal._regular(path))
        require(total + allowance <= history.METADATA_BYTES and total + allowance <= history.STATE_BYTES,
                'incremental_span_status_allocated_metadata_bound')


def _journal(root, destination, header, receipts, guard, policy=span.POLICY):
    d, checkpoint_identity = _control(root / 'uploader/uploader.json', guard, journal=True)
    # Do not instantiate an Uploader or Journal whose constructor may mkdir/lock/save.
    journal = _ReadOnlyJournal.__new__(_ReadOnlyJournal)
    journal.u = u; journal.guard = guard; journal.path = root / 'uploader'
    journal.root_identity = journal._directory(journal.path)
    journal.history = journal.path / 'uploader.history'
    single.private_directory(journal.history)
    journal.history_identity = journal._directory(journal.history)
    journal.data = d; journal.digest_bindings = {'input': bytearray(), 'page': bytearray()}
    journal.observed_files = {}; journal.ready = False
    journal.validate_checkpoint()
    require(u._json(d['identity']) == u._json(dict(api=destination['origin'],
            session_id=destination['session_id'], capture_id=destination['capture_id'], origin=0, format=u.FORMAT)) and
            u._json(d['caps']) == u._json(dict(output_frames=header['max_hours'] * 3600 * u.RATE,
            input_bytes=engine.BYTES, max_inputs=header['max_inputs'])),
            'incremental_span_status_journal_target_or_caps_changed')
    if not d['complete']:
        raise NotReady('incremental_span_status_intake_not_finalized')
    require(d['finalize_requested'] is True and
            all(d[key] is None for key in ('active_input', 'active_page', 'archive_intent', 'retirement')) and
            not d['spool'] and 1 <= d['pages_done'] == d['page_count'] <= MAX_PAGES and
            1 <= d['inputs_done'] == d['input_count'] == len(receipts) <= header['max_inputs'],
            'incremental_span_status_finalized_checkpoint_required')
    require(policy.finalizable(receipts[-1]['delivery']),
            'incremental_span_status_true_terminal_closure_required')
    _inventory_history(journal)
    journal._bind('input'); journal._bind('page')
    journal.validate_pending_archive(); journal.validate_history()
    start = 0
    for entry, receipt in zip(journal.iter_inputs(), receipts):
        report = receipt['delivery']
        require(entry['id'] == report['source_id'] and entry['normalized_start'] == start and
                entry['frames'] == report['frames'] and entry['clean_eof'] is True and
                entry['identity']['path'] == str(Path(receipt['snapshot_path']) / 'audio.wav') and
                u._json(entry['decoder']) == u._json(dict(kind='canonical-wav', policy=u.POLICY,
                    format=u.FORMAT, declared_frames=report['frames'])),
                'incremental_span_status_input_history_changed')
        start += report['frames']
    require(d['emitted_frames'] == d['published_frames'] == d['completed_frames'] == start,
            'incremental_span_status_finalized_frame_frontier')
    last = None
    for row in journal.iter_pages():
        create = dict(page_number=row['number'])
        if row['number'] == 1: create['capture_origin_frame'] = 0
        require(row['released'] is True and observer._id(row.get('server_id')) and
                u._json(row.get('create')) == u._json(create) and row['pieces'] and
                all(observer._id(piece.get('server_id')) for piece in row['pieces']),
                'incremental_span_status_page_intent_changed')
        last = row
    finish = d.get('finish')
    if finish is not None:
        require(type(finish) is dict and set(finish) == {'idempotency_key', 'expected_revision'} and
                finish['idempotency_key'] == d['job'] + '.finish' and
                type(finish['expected_revision']) is int and finish['expected_revision'] >= 0 and
                all(row['seal']['eos'] is False for row in journal.iter_pages()) and start % u.PAGE_FRAMES == 0,
                'incremental_span_status_finish_intent_changed')
    else:
        require(last['seal']['eos'] is True, 'incremental_span_status_missing_local_eos')
    _piece_hashes(journal, guard)
    return journal, checkpoint_identity


def _piece_hashes(journal, guard):
    """One source at a time and one page at a time, with finite read buffers."""
    pieces = (piece for row in journal.iter_pages() for piece in row['pieces'])
    piece = next(pieces, None)
    for entry in journal.iter_inputs():
        source = u.Original(entry['identity']['path'], guard)
        try:
            require(u._json(source.identity) == u._json(entry['identity']),
                    'incremental_span_status_original_identity_changed')
            source.file.seek(0)
            with wave.open(source.file, 'rb') as stream:
                cursor = 0
                while piece is not None and piece['input_id'] == entry['id']:
                    require(piece['input_start'] == cursor, 'incremental_span_status_piece_offset')
                    pcm = hashlib.sha256(); wav = hashlib.sha256(u.LegacyAppendUploader._header(piece['frames']))
                    left = piece['frames']
                    while left:
                        guard.check(); block = stream.readframes(min(left, u.READ_BYTES // 2))
                        require(block and len(block) % 2 == 0, 'incremental_span_status_piece_short_read')
                        left -= len(block) // 2; pcm.update(block); wav.update(block)
                    require((pcm.hexdigest(), wav.hexdigest()) == (piece['pcm_sha256'], piece['digest']),
                            'incremental_span_status_piece_digest_changed')
                    cursor += piece['frames']; piece = next(pieces, None)
                require(cursor == entry['frames'] and not stream.readframes(1),
                        'incremental_span_status_piece_coverage_changed')
            source.verify()
        finally:
            source.close()
    require(piece is None, 'incremental_span_status_extra_piece')


def source_content_binding(header, records, journal, policy):
    """Versioned export-time binding of already validated local/remote receipts.

    Hashes associate the observed baseline with exact originals and target;
    they are not authentication of the remote service or the producer.
    """
    def rows_hash(rows):
        digest = hashlib.sha256()
        for row in rows:
            journal.guard.check()
            raw = u._json(row); digest.update(len(raw).to_bytes(8, 'big')); digest.update(raw)
        return digest.hexdigest()
    return dict(schema='recorder-export-source-content-v1',
        target=header['target'], header_sha256=hashlib.sha256(u._json(header)).hexdigest(),
        originals=[dict(ordinal=i, delivery=record['delivery']) for i, record in enumerate(records)],
        input_history_sha256=rows_hash(journal.iter_inputs()),
        remote_page_receipts_sha256=rows_hash(journal.iter_pages()),
        closure_sha256=hashlib.sha256(u._json(policy.result_fields(records,header))).hexdigest(),
        frames=journal.data['completed_frames'],
        authentication='operator-selected-target-and-observed-receipts-not-remote-authentication')


def _contract(contract, session_id, capture_id, frames):
    single.check_contract(contract, session_id, frames)
    require(all(observer._int(contract.get(k)) for k in ('sample_rate_hz', 'channels',
        'sample_width_bytes', 'max_page_work_chunks', 'work_chunk_frames')),
        'incremental_span_status_contract_numeric_type')
    capture = contract.get('capture')
    require(type(capture) is dict and all(capture.get(k) == v for k, v in dict(
        capture_id=capture_id, capture_origin_frame=0, capture_end_frame=frames,
        mode='sealed_pages', eos=1).items()) and
        observer._int(capture.get('capture_origin_frame')) and observer._int(capture.get('capture_end_frame')) and
        type(capture.get('eos')) in (bool, int) and capture.get('state') in ('queued', 'aborted') and
        contract.get('has_chunks') is True, 'incremental_span_status_contract_history_changed')
    return capture


def run(*, job_dir, api, session_id, capture_id, exclusive_writer=False,
        limits=None, transport=None, cancel=None, _completed_read=None):
    require(exclusive_writer is True, 'span_status_exclusive_writer_ack_required')
    destination = single.target(api, 'operator-selected-unverified', session_id, capture_id, 0)
    guard = u.Guard(limits or u.Limits(output_frames=history.MAX_FRAMES, deadline_seconds=120), cancel)
    guard.check()
    root = Path(job_dir).absolute()
    single.private_directory(root.parent); single.private_directory(root)
    outer = engine.held_lock(root / 'intake.lock'); owner = None
    try:
        single.private_directory(root / 'uploader')
        owner = engine.held_lock(root / 'uploader/uploader.lock')
        locks = [(outer, root / 'intake.lock'), (owner, root / 'uploader/uploader.lock')]
        lock_identities = [u._signature(os.fstat(fd)) for fd, _ in locks]
        local = _local(root, destination, guard)
        header, receipts, records, _ = local
        policy = span.POLICY.for_job(root,header,receipts,records,lambda:observer._remaining(guard))
        journal, checkpoint_identity = _journal(root, destination, header, receipts, guard, policy=policy)
        d = dict(journal.data, pages=_Pages(journal))
        max_pages = journal.max_pages; max_work = max_pages * 28
        require(max_pages <= MAX_PAGES and max_work <= MAX_WORK_CHUNKS,
                'incremental_span_status_history_bound')
        reader = observer._Reader(transport or u.HTTPTransport(destination['origin'], guard), guard,
            max_pages=max_pages, max_work_chunks=max_work, max_response_bytes=MAX_RESPONSE_BYTES)
        contract_path = '/sessions/' + session_id + '/recording-contract'
        base = '/sessions/' + session_id + '/recordings/' + capture_id
        contract = reader.get(contract_path)
        contract_capture = _contract(contract, session_id, capture_id, d['emitted_frames'])
        capture = reader.get(base); observer._capture(capture, d, destination)
        require(capture['state'] == contract_capture['state'], 'span_status_capture_state_changed')
        work, optional = observer._pages(reader, base, d, destination, max_work_chunks=max_work)
        chunks = observer._chunks(reader, '/sessions/' + session_id + '/chunks', work, max_work_chunks=max_work)
        require(u._json(reader.get(base)) == u._json(capture) and
                u._json(reader.get(contract_path)) == u._json(contract),
                'span_status_remote_history_changed_during_read')
        require(_local(root, destination, guard) == local and
                _control(root / 'uploader/uploader.json', guard, journal=True)[1] == checkpoint_identity,
                'incremental_span_status_local_history_changed_during_read')
        _inventory_history(journal); journal.validate_history(); _piece_hashes(journal, guard)
        guard.check()
        require(all(u._signature(os.fstat(fd)) == u._signature(path.lstat()) == identity
                    for (fd, path), identity in zip(locks, lock_identities)), 'span_status_lock_changed')
        blocked = [dict(chunk_id=cid, position=item['position'], page_status=item['status'], session_status=chunks[cid])
                   for cid, item in work.items() if item['status'] in observer.BLOCKED or chunks[cid] in observer.BLOCKED]
        complete = capture['state'] != 'aborted' and all(
            item['status'] == chunks[cid] == 'completed' for cid, item in work.items())
        status = 'blocked' if blocked or capture['state'] == 'aborted' else 'completed' if complete else 'pending'
        mapping = []; start = 0
        for ordinal, record in enumerate(records):
            report = record['delivery']; end = start + report['frames']
            mapping.append(dict(ordinal=ordinal, original_sequence=report['sequence'], original_epoch=report['epoch'],
                original_epoch_frame_start=report['frame_start'], original_epoch_frame_end=report['frame_start'] + report['frames'],
                span_start_frame=start, span_end_frame=end)); start = end
        result = dict(schema=SCHEMA, status=status, analysis_completion_observed=complete,
            analysis_completion_verified=complete, observation_atomic=False,
            exclusive_writer_assumed=True, concurrent_writer_safety_guaranteed=False,
            remote_request_identity_verified=False, target_identity_authenticated=False,
            producer_identity_authenticated=False, accuracy_verified=False, physical_continuity_proven=False,
            **policy.result_fields(records, header), input_mapping=mapping,
            source_content_binding=source_content_binding(header, records, journal, policy),
            session_id=session_id, capture_id=capture_id, frames=d['emitted_frames'], inputs=len(receipts),
            pages=d['page_count'], work_chunks=len(work), capture_state=capture['state'], blocked_chunks=blocked,
            optional_receipts=optional,
            page_status_counts=dict(sorted(Counter(w['status'] for w in work.values()).items())),
            session_status_counts=dict(sorted(Counter(chunks.values()).items())),
            warning_codes=sorted({warning for record in records for warning in engine._record_warnings(record['metadata'])}),
            read_only=True)
        if _completed_read is not None:
            # Internal read-only extension used by the explicit transcript exporter.
            # Both existing locks and the exact bound local observation stay alive.
            require(complete, 'incremental_span_export_completed_required')
            reader.max_requests *= 2
            protected = (root, *(Path(receipt['path']) for receipt in receipts))
            if policy.logical_boundary is not None:
                from . import recorder_logical_boundary as logical
                protected += logical.protected(policy.logical_boundary)
            with _completed_read(result, work, guard, protected):
                after_work, after_optional = observer._pages(reader, base, d, destination,
                    max_work_chunks=max_work)
                after_chunks = observer._chunks(reader, '/sessions/' + session_id + '/chunks',
                    after_work, max_work_chunks=max_work)
                require(u._json((after_work, after_optional, after_chunks)) ==
                        u._json((work, optional, chunks)),
                        'incremental_span_status_work_changed_during_read')
            require(u._json(reader.get(base)) == u._json(capture) and
                    u._json(reader.get(contract_path)) == u._json(contract),
                    'span_status_remote_history_changed_during_read')
            require(_local(root, destination, guard) == local and
                    _control(root / 'uploader/uploader.json', guard, journal=True)[1] == checkpoint_identity,
                    'incremental_span_status_local_history_changed_during_read')
            _inventory_history(journal); journal.validate_history(); _piece_hashes(journal, guard)
            guard.check()
            require(all(u._signature(os.fstat(fd)) == u._signature(path.lstat()) == identity
                        for (fd, path), identity in zip(locks, lock_identities)), 'span_status_lock_changed')
        return result
    finally:
        if owner is not None: os.close(owner)
        os.close(outer)


def failure_report(exc):
    report = observer.failure_report(exc); report['schema'] = SCHEMA
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('job-dir', 'api', 'session-id', 'capture-id'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--exclusive-writer', action='store_true', required=True)
    parser.add_argument('--deadline-seconds', type=float, default=120)
    args = vars(parser.parse_args(argv)); deadline = args.pop('deadline_seconds')
    try:
        result = run(**args, limits=u.Limits(output_frames=history.MAX_FRAMES, deadline_seconds=deadline))
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps(failure_report(exc), sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
