"""One bounded GET-only observation of an existing, finalized recorder span job.

This does not resume intake, replay seal/finish, retry analysis, or save a journal.
EOS remains selected-span-only. Observations are not an atomic server snapshot.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
from types import SimpleNamespace
import wave

from . import recorder_intake as single
from . import recorder_span_intake as span
from . import recording_uploader as u
from .recorder_delivery import Refused, inspect_delivery
from .recorder_snapshot import inspect_snapshot, _open_directory, _receipt

SCHEMA = 'recorder-span-status-v1'
MAX_WORK_CHUNKS = 700
CHUNK_FRAMES = 168000
CHUNK_LIMIT = 16
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
STATES = frozenset(('staging', 'queued', 'running', 'waiting_input', 'completed',
                    'failed', 'paused', 'cancelled'))
BLOCKED = frozenset(('failed', 'paused', 'cancelled'))
require = single.require


class NotReady(Refused):
    """An intact but unfinished journal must be handled by intake, not status."""


def _int(value):
    return type(value) is int


def _id(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value)


def _remaining(guard):
    guard.check()
    return min(120, max(.000001, guard.end - time.monotonic()))


def _read(path, limit, guard):
    guard.check(); single.control(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= limit,
                'span_status_control_size')
        raw = stream.read(limit + 1)
        require(len(raw) == before.st_size and
                u._signature(before) == u._signature(os.fstat(fd)) == u._signature(path.lstat()),
                'span_status_control_changed')
    guard.check()
    return raw, u._signature(before)


def _inventory(path, expected):
    # Stop at the first extra entry rather than materializing an unbounded tree.
    names = set()
    with os.scandir(path) as entries:
        for entry in entries:
            names.add(entry.name)
            require(names <= expected, 'span_status_unexpected_job_files')
    require(names == expected, 'span_status_incomplete_job')


def _local(root, destination, guard):
    """Reconstruct the frozen manifest from current originals and owned snapshots."""
    _inventory(root, {'intake.lock', 'manifest.json', 'snapshots', 'uploader'})
    for child in ('snapshots', 'uploader'):
        single.private_directory(root / child)
    raw, signature = _read(root / 'manifest.json', single.MAX_MANIFEST, guard)
    manifest = u._parse(raw)
    from .recorder_snapshot import _json
    require(_json(manifest) == raw and manifest.get('schema') == span.SCHEMA,
            'span_status_manifest_schema')
    require(manifest.get('target') == destination, 'span_status_target_changed')
    inputs = manifest.get('inputs')
    require(type(inputs) is list and 1 <= len(inputs) <= span.MAX_INPUTS,
            'span_status_input_count')
    paths = [Path(entry['path']) for entry in inputs]
    require(all(path.is_absolute() and path == path.resolve(strict=True) for path in paths)
            and len(set(paths)) == len(paths), 'span_status_input_paths')
    snapshots = [root / 'snapshots' / str(i) for i in range(len(inputs))]
    _inventory(root / 'snapshots', set(map(str, range(len(inputs)))))
    # Bound the aggregate before hashing even a single potentially large source.
    sizes = [os.stat(p / 'audio.wav', follow_symlinks=False) for p in paths + snapshots]
    require(all(stat.S_ISREG(s.st_mode) and s.st_size > 44 for s in sizes) and
            sum(s.st_size for s in sizes[:len(paths)]) <= min(span.MAX_INPUT_BYTES, guard.limits.input_bytes) and
            sum(s.st_size for s in sizes[len(paths):]) <= min(span.MAX_INPUT_BYTES, guard.limits.input_bytes),
            'span_status_input_byte_bound')
    reports = [inspect_delivery(p, validate_metadata=True, deadline_seconds=_remaining(guard)) for p in paths]
    require(0 < sum(r['frames'] for r in reports) <= min(2 * 3600 * u.RATE, guard.limits.output_frames),
            'span_status_frame_bound')
    observed = span.check_sequence(snapshots, reports, deadline_seconds=_remaining(guard))
    for snapshot, report in zip(snapshots, reports):
        observed_delivery = inspect_snapshot(snapshot, deadline_seconds=_remaining(guard))['delivery']
        # Inspector returns normalized observations; also bind exact receipt types.
        fd = _open_directory(snapshot)
        try:
            receipt, _ = _receipt(fd, lambda: _remaining(guard))
        finally:
            os.close(fd)
        require(u._json(observed_delivery) == u._json(report) == u._json(receipt['body']['delivery']),
                'span_status_snapshot_identity_changed')
    starts = []; end = 0
    for report in reports:
        starts.append(end); end += report['frames']
    expected = dict(schema=span.SCHEMA, target=destination, span=span.span_description(reports),
        acknowledgments=dict(exclusive_writer=True, span_local_closure=True),
        inputs=[dict(path=str(path), snapshot_path=str(snap), span_start_frame=start, delivery=report)
                for path, snap, start, report in zip(paths, snapshots, starts, reports)])
    require(raw == _json(expected), 'span_status_manifest_identity_changed')
    return manifest, reports, snapshots, observed, (raw, signature)


def _journal(root, destination, reports, snapshots, guard):
    raw, signature = _read(root / 'uploader/uploader.json', u.JSON_BYTES, guard)
    # Do not construct a Journal/Uploader: their lifecycle permits mkdir/save.
    journal = u.AppendJournal.__new__(u.AppendJournal)
    journal.path = root / 'uploader'; journal.guard = guard; journal.data = u._parse(raw)
    require(u._json(journal.data) == raw, 'span_status_journal_noncanonical')
    journal.validate()
    d = journal.data
    _inventory(root / 'uploader', {'uploader.lock', 'uploader.json', *d['spool']})
    require(u._json(d['identity']) == u._json(dict(api=destination['origin'], session_id=destination['session_id'],
        capture_id=destination['capture_id'], origin=0, format=u.FORMAT)), 'span_status_journal_target_changed')
    require(d['max_inputs'] == len(reports), 'span_status_journal_input_count')
    require(len(d['inputs']) <= len(reports), 'span_status_journal_input_count')
    start = 0
    for entry, report, snapshot in zip(d['inputs'], reports, snapshots):
        require(entry['id'] == report['source_id'] and entry['normalized_start'] == start and
                entry['identity']['path'] == str(snapshot / 'audio.wav') and
                entry['frames'] <= report['frames'] and
                (not entry['clean_eof'] or entry['frames'] == report['frames']),
                'span_status_journal_input_changed')
        source = u.Original(snapshot / 'audio.wav', guard)
        try:
            require(u._json(source.identity) == u._json(entry['identity']) and u._json(entry['decoder']) == u._json(dict(
                kind='canonical-wav', policy=u.POLICY, format=u.FORMAT, declared_frames=report['frames'])),
                'span_status_original_identity_changed')
        finally:
            source.close()
        start += report['frames']
    if not d['complete']:
        raise NotReady('span_status_intake_not_finalized')
    _inventory(root / 'uploader', {'uploader.lock', 'uploader.json'})
    require(d['finalize_requested'] is True and len(d['inputs']) == len(reports) and
            d['emitted_frames'] == d['published_frames'] == start and not d['spool'] and
            1 <= len(d['pages']) <= 25 and all(r['released'] is True for r in d['pages']),
            'span_status_finalized_journal_required')
    for row in d['pages']:
        create = dict(page_number=row['number'])
        if row['number'] == 1: create['capture_origin_frame'] = 0
        require(_id(row.get('server_id')) and row.get('create') == create and
                all(_int(v) for v in row['create'].values()) and row['pieces'],
                'span_status_page_intent_changed')
        seal = row.get('seal', {})
        require(set(seal) == {'idempotency_key', 'expected_revision', 'end_frame', 'eos'} and
                seal['idempotency_key'] == d['job'] + '.seal.' + str(row['number']) and
                _int(seal['expected_revision']) and seal['expected_revision'] >= 0 and
                _int(seal['end_frame']) and type(seal['eos']) is bool and
                (row is d['pages'][-1] or seal['eos'] is False) and
                all(_id(p.get('server_id')) for p in row['pieces']), 'span_status_seal_intent_changed')
    finish = d.get('finish')
    if finish is not None:
        require(finish == dict(idempotency_key=d['job'] + '.finish',
                    expected_revision=finish.get('expected_revision')) and
                _int(finish['expected_revision']) and finish['expected_revision'] >= 0 and
                all(not r['seal']['eos'] for r in d['pages']) and start % u.PAGE_FRAMES == 0,
                'span_status_finish_intent_changed')
    else:
        require(d['pages'][-1]['seal']['eos'] is True, 'span_status_missing_local_eos')
    # A syntactically valid journal alone does not bind piece digests to PCM.
    for entry, snapshot in zip(d['inputs'], snapshots):
        source = u.Original(snapshot / 'audio.wav', guard)
        try:
            require(u._json(source.identity) == u._json(entry['identity']), 'span_status_original_identity_changed')
            source.file.seek(0)
            with wave.open(source.file, 'rb') as stream:
                for piece in (p for r in d['pages'] for p in r['pieces'] if p['input_id'] == entry['id']):
                    pcm = hashlib.sha256(); wav = hashlib.sha256(u.LegacyAppendUploader._header(piece['frames']))
                    left = piece['frames']
                    while left:
                        guard.check(); block = stream.readframes(min(left, u.READ_BYTES // 2))
                        require(block and len(block) % 2 == 0, 'span_status_piece_short_read')
                        left -= len(block) // 2; pcm.update(block); wav.update(block)
                    require((pcm.hexdigest(), wav.hexdigest()) == (piece['pcm_sha256'], piece['digest']),
                            'span_status_piece_digest_changed')
            source.verify()
        finally:
            source.close()
    return d, (raw, signature)


class _Reader:
    def __init__(self, http, guard, *, max_pages=25, max_work_chunks=None,
                 max_response_bytes=None):
        if max_work_chunks is None: max_work_chunks = MAX_WORK_CHUNKS
        if max_response_bytes is None: max_response_bytes = MAX_RESPONSE_BYTES
        require(all(type(v) is int and v > 0 for v in
                    (max_pages, max_work_chunks, max_response_bytes)), 'span_status_invalid_reader_bound')
        self.http = http; self.guard = guard; self.bytes = 0; self.requests = 0
        self.max_requests = 2 * max_pages + max_work_chunks + 5
        self.max_response_bytes = max_response_bytes

    def get(self, path):
        self.guard.check(); self.requests += 1
        require(self.requests <= self.max_requests, 'span_status_request_bound')
        value = self.http.request('GET', path)  # No retry or mutation, including on 429.
        self.guard.check()
        require(type(value) is dict, 'span_status_response_object')
        raw = u._json(value)
        require(len(raw) <= u.JSON_BYTES, 'span_status_response_byte_bound')
        self.bytes += len(raw)
        require(self.bytes <= self.max_response_bytes, 'span_status_total_response_byte_bound')
        return value


def _seconds(value, frames):
    return (type(value) in (int, float) and math.isfinite(value) and value >= 0 and
            abs(value * u.RATE - frames) <= .00001)


def _capture(value, d, destination):
    require(value.get('capture_id') == destination['capture_id'] and
            value.get('capture_origin_frame') == 0 and _int(value.get('capture_origin_frame')) and
            value.get('mode') == 'sealed_pages' and value.get('policy') == 'sealed-recording-exact-overlap-v1' and
            value.get('format') == 'wav-pcm16le-16000-mono' and
            _int(value.get('page_count')) and value['page_count'] == len(d['pages']) and
            _int(value.get('released_pages')) and value['released_pages'] == len(d['pages']) and
            _int(value.get('capture_end_frame')) and value['capture_end_frame'] == d['emitted_frames'] and
            value.get('pending_page', False) is None and value.get('eos') is True and
            value.get('state') in ('queued', 'aborted') and
            _int(value.get('revision')) and value['revision'] >= 0,
            'span_status_capture_history_changed')


def _pages(reader, base, d, destination, *, max_work_chunks=MAX_WORK_CHUNKS):
    work = {}; source_ids = set(); page_ids = set(); cursor = 0
    receipts = dict(pending=0, durable=0, gaps=0, ready=True)
    matcher = SimpleNamespace(capture=destination['capture_id'], origin=0)
    for row in d['pages']:
        remote = reader.get(base + '/pages/' + str(row['number']))
        # The existing matcher changes only this detached copy, never the journal.
        u.LegacyAppendUploader._match(matcher, copy.deepcopy(row), remote)
        require(remote.get('state') == 'published' and remote.get('released') is True and
                remote.get('eos') is row['seal']['eos'] and
                _int(remote.get('page_number')) and _int(remote.get('capture_origin_frame')) and
                _int(remote.get('revision')) and remote['revision'] == row['seal']['expected_revision'] and
                _int(remote.get('start_frame')) and remote['start_frame'] == cursor and
                _int(remote.get('end_frame')) and remote['end_frame'] == row['seal']['end_frame'] and
                _int(remote.get('capture_end_frame')) and remote['capture_end_frame'] == d['emitted_frames'] and
                remote['id'] not in page_ids and len(remote['sources']) == len(row['pieces']),
                'span_status_page_history_changed')
        page_ids.add(remote['id'])
        optional = remote.get('optional_receipts')
        require(type(optional) is dict and type(optional.get('ready')) is bool and
                all(_int(optional.get(k)) and 0 <= optional[k] <= 28 for k in ('pending', 'durable', 'gaps')),
                'span_status_optional_receipt_projection')
        for key in ('pending', 'durable', 'gaps'): receipts[key] += optional[key]
        receipts['ready'] = receipts['ready'] and optional['ready']
        for source in remote['sources']:
            require(_id(source.get('id')) and source['id'] not in source_ids and
                    all(_int(source.get(k)) for k in ('frames', 'bytes', 'source_start_frame')),
                    'span_status_source_identity_changed')
            source_ids.add(source['id'])
        rows = remote.get('work_chunks')
        require(type(rows) is list and 1 <= len(rows) <= 28, 'span_status_work_count')
        require(all(item.get('receipt_state') in ('pending', 'durable', 'gap', 'cancelled') and
                    type(item.get('native_receipt_status')) is str for item in rows) and
                optional == dict(pending=sum(w['receipt_state'] == 'pending' for w in rows),
                    durable=sum(w['receipt_state'] == 'durable' for w in rows),
                    gaps=sum(w['receipt_state'] == 'gap' and w['native_receipt_status'] != 'disabled' for w in rows),
                    ready=all(w['receipt_state'] == 'durable' for w in rows)),
                'span_status_optional_receipt_projection')
        for item in rows:
            frames = min(CHUNK_FRAMES, remote['end_frame'] - cursor)
            cid = item.get('chunk_id')
            require(_id(cid) and cid not in work and frames > 0 and
                    all(_int(item.get(k)) for k in ('position', 'frames', 'capture_start_frame', 'bytes')) and
                    item['position'] == len(work) and item['frames'] == frames and
                    item['capture_start_frame'] == cursor and item['bytes'] == 44 + 2 * frames and
                    _seconds(item.get('timeline_start'), cursor) and _seconds(item.get('duration'), frames) and
                    item.get('status') in STATES, 'span_status_work_geometry_changed')
            work[cid] = dict(position=item['position'], frames=frames, start=cursor, status=item['status'])
            cursor += frames
        require(cursor == remote['end_frame'] and len(work) <= max_work_chunks,
                'span_status_work_coverage_changed')
    require(cursor == d['emitted_frames'], 'span_status_work_coverage_changed')
    return work, receipts


def _chunks(reader, path, work, *, max_work_chunks=MAX_WORK_CHUNKS):
    after = 0; found = {}
    while True:
        page = reader.get(path + '?after=' + str(after) + '&limit=' + str(CHUNK_LIMIT))
        rows = page.get('chunks'); next_cursor = page.get('next_cursor')
        require(type(rows) is list and len(rows) <= CHUNK_LIMIT and _int(next_cursor),
                'span_status_chunk_page_malformed')
        if not rows:
            require(next_cursor == after, 'span_status_chunk_cursor_changed')
            break
        for item in rows:
            cid = item.get('id'); cursor = item.get('cursor')
            require(_int(cursor) and cursor > after, 'span_status_chunk_cursor_changed')
            require(_id(cid) and cid in work and cid not in found, 'span_status_foreign_or_duplicate_chunk')
            expected = work[cid]; frames = expected['frames']; progress = item.get('next_offset')
            require(_seconds(item.get('timeline_start'), expected['start']) and
                    _seconds(item.get('duration'), frames) and item.get('status') in STATES and
                    type(progress) in (int, float) and math.isfinite(progress) and
                    0 <= progress <= frames / u.RATE + .00001 / u.RATE,
                    'span_status_chunk_geometry_changed')
            if item['status'] == 'completed':
                require(_seconds(progress, frames), 'span_status_completed_progress_shortfall')
            found[cid] = item['status']; after = cursor
            require(len(found) <= max_work_chunks, 'span_status_chunk_count_bound')
        require(next_cursor == after, 'span_status_chunk_cursor_changed')
    require(set(found) == set(work), 'span_status_missing_session_chunks')
    return found


def run(*, job_dir, api, session_id, capture_id, exclusive_writer=False,
        limits=None, transport=None, cancel=None):
    require(exclusive_writer is True, 'span_status_exclusive_writer_ack_required')
    destination = single.target(api, 'operator-selected-unverified', session_id, capture_id, 0)
    guard = u.Guard(limits or u.Limits(deadline_seconds=120), cancel)
    guard.check()
    root = Path(job_dir).absolute()
    single.private_directory(root.parent); single.private_directory(root)
    outer = span._held_lock(root / 'intake.lock'); owner = None
    try:
        single.private_directory(root / 'uploader')
        owner = span._held_lock(root / 'uploader/uploader.lock')
        locks = [(outer, root / 'intake.lock'), (owner, root / 'uploader/uploader.lock')]
        lock_identities = [u._signature(os.fstat(fd)) for fd, _ in locks]
        manifest, reports, snapshots, observed, manifest_identity = _local(root, destination, guard)
        d, journal_identity = _journal(root, destination, reports, snapshots, guard)
        http = transport or u.HTTPTransport(destination['origin'], guard)
        reader = _Reader(http, guard)
        contract_path = '/sessions/' + session_id + '/recording-contract'
        base = '/sessions/' + session_id + '/recordings/' + capture_id
        contract = reader.get(contract_path)
        single.check_contract(contract, session_id, d['emitted_frames'])
        require(all(_int(contract.get(k)) for k in ('sample_rate_hz', 'channels', 'sample_width_bytes',
                    'max_page_work_chunks', 'work_chunk_frames')), 'span_status_contract_numeric_type')
        contract_capture = contract.get('capture')
        require(type(contract_capture) is dict and
                all(contract_capture.get(k) == v for k, v in dict(capture_id=capture_id,
                    capture_origin_frame=0, capture_end_frame=d['emitted_frames'],
                    mode='sealed_pages', eos=1).items()) and
                _int(contract_capture.get('capture_origin_frame')) and
                _int(contract_capture.get('capture_end_frame')) and
                type(contract_capture.get('eos')) in (bool, int) and
                contract_capture.get('state') in ('queued', 'aborted') and
                contract.get('has_chunks') is True, 'span_status_contract_history_changed')
        capture = reader.get(base); _capture(capture, d, destination)
        require(capture['state'] == contract_capture['state'], 'span_status_capture_state_changed')
        work, receipts = _pages(reader, base, d, destination)
        chunks = _chunks(reader, '/sessions/' + session_id + '/chunks', work)
        require(u._json(reader.get(base)) == u._json(capture) and
                u._json(reader.get(contract_path)) == u._json(contract),
                'span_status_remote_history_changed_during_read')
        # Validate again before success: no mutation or repair is allowed on either path.
        require(_local(root, destination, guard) == (manifest, reports, snapshots, observed, manifest_identity) and
                _read(root / 'uploader/uploader.json', u.JSON_BYTES, guard) == journal_identity,
                'span_status_local_history_changed_during_read')
        for entry in d['inputs']:
            source = u.Original(entry['identity']['path'], guard)
            try:
                require(u._json(source.identity) == u._json(entry['identity']), 'span_status_original_identity_changed')
            finally:
                source.close()
        guard.check()
        require(all(u._signature(os.fstat(fd)) == u._signature(path.lstat()) == identity
                    for (fd, path), identity in zip(locks, lock_identities)), 'span_status_lock_changed')
        blocked = [dict(chunk_id=cid, position=item['position'], page_status=item['status'], session_status=chunks[cid])
                   for cid, item in work.items() if item['status'] in BLOCKED or chunks[cid] in BLOCKED]
        complete = capture['state'] != 'aborted' and all(
            item['status'] == chunks[cid] == 'completed' for cid, item in work.items())
        status = 'blocked' if blocked or capture['state'] == 'aborted' else 'completed' if complete else 'pending'
        return dict(schema=SCHEMA, status=status, analysis_completion_observed=complete,
            analysis_completion_verified=complete, observation_atomic=False,
            exclusive_writer_assumed=True, concurrent_writer_safety_guaranteed=False,
            remote_request_identity_verified=False, target_identity_authenticated=False,
            producer_identity_authenticated=False, accuracy_verified=False,
            span=manifest['span'], physical_continuity_proven=False, capture_completeness_proven=False,
            session_id=session_id, capture_id=capture_id, frames=d['emitted_frames'], inputs=len(reports),
            pages=len(d['pages']), work_chunks=len(work), capture_state=capture['state'], blocked_chunks=blocked,
            optional_receipts=receipts,
            page_status_counts=dict(sorted(Counter(w['status'] for w in work.values()).items())),
            session_status_counts=dict(sorted(Counter(chunks.values()).items())),
            warning_codes=observed['captures'][0]['warning_codes'], read_only=True)
    finally:
        if owner is not None: os.close(owner)
        os.close(outer)


def failure_report(exc):
    report = single.failure_report(exc)
    report.update(schema=SCHEMA, status='not_ready' if isinstance(exc, NotReady) else 'refused',
        analysis_completion_observed=False, analysis_completion_verified=False,
        possible_remote_effects=False, read_only=True, eos_meaning='selected_span_only',
        capture_completeness_proven=False, physical_continuity_proven=False,
        next_step='Preserve the exact job and originals. Inspect the reported condition; this command does not resume intake or retry analysis.')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('job-dir', 'api', 'session-id', 'capture-id'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--exclusive-writer', action='store_true', required=True)
    parser.add_argument('--deadline-seconds', type=float, default=120)
    args = vars(parser.parse_args(argv)); deadline = args.pop('deadline_seconds')
    try:
        result = run(**args, limits=u.Limits(deadline_seconds=deadline))
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps(failure_report(exc), sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
