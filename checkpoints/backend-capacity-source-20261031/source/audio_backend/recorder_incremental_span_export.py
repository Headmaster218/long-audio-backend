"""Explicit, finite, GET-only primary transcript export of a completed span.

Only a fresh private local directory is written. A complete.json manifest is
published last; without it the directory is an incomplete attempt, not an export.
"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import unicodedata

from . import recorder_incremental_span_status as status
from . import recorder_intake as single
from . import recording_uploader as u
from . import recording_uploader_history as history
from . import transcript_contract as c

SCHEMA = 'recorder-incremental-span-export-v1'
MAX_PAGES = 48 * 60
MAX_UNITS = 200000
MAX_OUTPUT_BYTES = 64 * 1024**2
MAX_NETWORK_BYTES = 256 * 1024**2
MAX_REQUESTS = 2 * (2 * status.MAX_PAGES + status.MAX_WORK_CHUNKS + 5) + 2 * MAX_PAGES + 2 * status.MAX_WORK_CHUNKS
require = single.require


@dataclass(frozen=True)
class ExportLimits:
    pages: int = MAX_PAGES
    units: int = MAX_UNITS
    output_bytes: int = MAX_OUTPUT_BYTES
    network_bytes: int = MAX_NETWORK_BYTES
    requests: int = MAX_REQUESTS

    def __post_init__(self):
        for name, maximum in zip(('pages', 'units', 'output_bytes', 'network_bytes', 'requests'),
                (MAX_PAGES, MAX_UNITS, MAX_OUTPUT_BYTES, MAX_NETWORK_BYTES, MAX_REQUESTS)):
            value = getattr(self, name)
            require(type(value) is int and 0 < value <= maximum, 'span_export_invalid_limit')


class _Transport:
    """One aggregate budget for every status and transcript GET, no retries."""
    def __init__(self, http, guard, limits):
        self.http, self.guard, self.limits = http, guard, limits
        self.requests = self.bytes = 0

    def request(self, method, path, **kwargs):
        self.guard.check()
        require(method == 'GET' and not kwargs, 'span_export_get_only')
        self.requests += 1
        require(self.requests <= self.limits.requests, 'span_export_request_bound')
        value = self.http.request('GET', path)
        self.guard.check()
        require(type(value) is dict, 'span_export_response_object')
        raw = c.encoded(value)
        require(len(raw) <= u.JSON_BYTES, 'span_export_response_bound')
        self.bytes += len(raw)
        require(self.bytes <= self.limits.network_bytes, 'span_export_network_bound')
        return value


def _directory_identity(info):
    return info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid


def _destination(value, protected=()):
    path = Path(value)
    require(path.is_absolute() and path.name not in ('', '.', '..'), 'span_export_absolute_output_required')
    single.private_directory(path.parent)
    require(not os.path.lexists(path), 'span_export_output_exists')
    require(not any(path.is_relative_to(Path(root)) for root in protected), 'span_export_output_is_input')
    # Deliberately keep transcript artifacts out of accidental source-code backups.
    require(not any((parent / '.git').is_file() or os.path.lexists(parent / '.git/HEAD')
                    for parent in (path.parent, *path.parent.parents)),
            'span_export_output_in_repository')
    return path


class _Output:
    def __init__(self, path, guard, limits):
        self.path, self.guard, self.limits = path, guard, limits
        self.parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.directory = None; self.files = {}; self.published = {}; self.bytes = 0
        try:
            single.private_directory(path.parent)
            self.parent_identity = _directory_identity(os.fstat(self.parent))
            require(self.parent_identity == _directory_identity(path.parent.lstat()), 'span_export_parent_changed')
            os.mkdir(path.name, mode=0o700, dir_fd=self.parent)
            self.directory = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent)
            self.identity = _directory_identity(os.fstat(self.directory))
            self.check()
        except BaseException:
            self.close(); raise

    def check(self):
        self.guard.check()
        single.private_directory(self.path.parent); single.private_directory(self.path)
        require(_directory_identity(os.fstat(self.parent)) == self.parent_identity ==
                _directory_identity(self.path.parent.lstat()), 'span_export_parent_changed')
        require(_directory_identity(os.fstat(self.directory)) == self.identity ==
                _directory_identity(os.stat(self.path.name, dir_fd=self.parent, follow_symlinks=False)) ==
                _directory_identity(self.path.lstat()), 'span_export_directory_changed')
        require(self.identity[2] == 0o700, 'span_export_private_output_required')

    def open(self, name):
        self.check()
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.directory)
        stream = os.fdopen(fd, 'wb')
        self.files[name] = (stream, hashlib.sha256(), 0)

    def write(self, name, raw):
        self.check()
        require(type(raw) is bytes and self.bytes + len(raw) <= self.limits.output_bytes,
                'span_export_output_byte_bound')
        stream, digest, size = self.files[name]
        info = os.fstat(stream.fileno())
        named = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid() and
                stat.S_IMODE(info.st_mode) == 0o600 and (info.st_dev, info.st_ino) == (named.st_dev, named.st_ino),
                'span_export_file_changed')
        stream.write(raw); digest.update(raw)
        self.bytes += len(raw); self.files[name] = (stream, digest, size + len(raw))

    def finish_file(self, name, final):
        self.write(name, b'')
        stream, digest, size = self.files[name]
        stream.flush(); os.fsync(stream.fileno())
        info = os.fstat(stream.fileno())
        require(info.st_size == size, 'span_export_file_changed')
        observed = hashlib.sha256(); position = 0
        while position < size:
            self.check()
            raw = os.pread(stream.fileno(), min(u.READ_BYTES, size-position), position)
            require(bool(raw), 'span_export_file_changed')
            observed.update(raw); position += len(raw)
        self.write(name, b'')
        require(observed.digest() == digest.digest() and u._signature(info) ==
                u._signature(os.fstat(stream.fileno())), 'span_export_file_changed')
        # link refuses an existing name; unlike rename it cannot overwrite.
        os.link(name, final, src_dir_fd=self.directory, dst_dir_fd=self.directory, follow_symlinks=False)
        linked = os.stat(final, dir_fd=self.directory, follow_symlinks=False)
        try:
            current = os.fstat(stream.fileno())
            require(stat.S_ISREG(linked.st_mode) and linked.st_uid == os.geteuid() and
                    stat.S_IMODE(linked.st_mode) == 0o600 and linked.st_nlink == current.st_nlink == 2 and
                    (linked.st_dev, linked.st_ino, linked.st_size) ==
                    (current.st_dev, current.st_ino, size), 'span_export_link_changed')
        except BaseException:
            # Remove only the exact newly created directory entry, never its target.
            if u._signature(linked) == u._signature(os.stat(final, dir_fd=self.directory, follow_symlinks=False)):
                os.unlink(final, dir_fd=self.directory)
            raise
        try:
            os.unlink(name, dir_fd=self.directory)
            require(u._signature(os.stat(final, dir_fd=self.directory, follow_symlinks=False)) ==
                    u._signature(os.fstat(stream.fileno())), 'span_export_link_changed')
            self.published[final] = u._signature(os.fstat(stream.fileno()))
            stream.close(); del self.files[name]
        except BaseException:
            self._remove_own_link(final, linked)
            raise
        return dict(bytes=size, sha256=digest.hexdigest())

    def _remove_own_link(self, name, expected):
        try:
            current = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino):
                os.unlink(name, dir_fd=self.directory)
                os.fsync(self.directory)
        except OSError:
            # A filesystem that also refuses unlink/sync can leave uncertain
            # publication. Never mask the original failure or claim rollback.
            pass

    def verify_published(self):
        self.check()
        for name, identity in self.published.items():
            info = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                    info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600 and
                    u._signature(info) == identity, 'span_export_published_file_changed')

    def publish(self, summary):
        self.check(); artifacts = {}
        for name in list(self.files):
            artifacts[name.removesuffix('.partial')] = self.finish_file(name, name.removesuffix('.partial'))
        self.check(); os.fsync(self.directory)
        manifest = dict(schema=SCHEMA, status='exported', **summary, artifacts=artifacts)
        self.open('complete.json.partial')
        self.write('complete.json.partial', c.encoded(manifest) + b'\n')
        self.verify_published()
        self.finish_file('complete.json.partial', 'complete.json')
        marker = os.stat('complete.json', dir_fd=self.directory, follow_symlinks=False)
        try:
            os.fsync(self.directory); os.fsync(self.parent)
        except BaseException:
            self._remove_own_link('complete.json', marker)
            raise
        return manifest

    def close(self):
        for stream, _, _ in self.files.values(): stream.close()
        self.files.clear()
        if self.directory is not None: os.close(self.directory); self.directory = None
        if self.parent is not None: os.close(self.parent); self.parent = None


def _quote(value):
    # Preserve Unicode words, but escape controls/bidi formatting and line breaks.
    raw = json.dumps(value, ensure_ascii=False)
    return ''.join(('\\u%04x' % ord(ch)) if unicodedata.category(ch) in ('Cc', 'Cf', 'Zl', 'Zp') else ch for ch in raw)


def _time(seconds):
    micros = round(seconds * 1000000)
    whole, fraction = divmod(micros, 1000000)
    minutes, second = divmod(whole, 60); hour, minute = divmod(minutes, 60)
    return f'{hour:02d}:{minute:02d}:{second:02d}.{fraction:06d}'


def _page(http, sid, block, revision, count):
    query = 'block=0' if revision is None else 'cursor=' + c.cursor(sid, block, revision)
    value = http.request('GET', '/sessions/' + sid + '/transcript?' + query + '&projection=whole-primary-v1')
    require(len(c.encoded(value)) <= c.MAX_RESPONSE_BYTES, 'span_export_transcript_response_bound')
    require(all(type(value.get(key)) is int and value[key] == expected for key, expected in (
        ('block', block), ('start_frame', block*c.BLOCK_FRAMES), ('end_frame', (block+1)*c.BLOCK_FRAMES),
        ('sample_rate', c.RATE))) and value.get('session_id') == sid and
        value.get('policy_sha256') == value.get('policy_hash') == c.POLICY_HASH and
        type(value.get('revision')) is str and c.HASH.fullmatch(value['revision']) and
        (revision is None or value['revision'] == revision) and
        value.get('complete_content') is False and value.get('eos') is None,
        'span_export_transcript_page_binding')
    expected = c.cursor(sid, block+1, value['revision']) if block+1 < count else None
    require(value.get('next_cursor') == expected, 'span_export_transcript_cursor')
    rows = value.get('baseline_reading')
    require(type(rows) is list and len(rows) <= c.MAX_PRIMARY, 'span_export_primary_page_bound')
    # Hash only the selected baseline and its page binding, never copy unrelated
    # optional transcript text or speaker-link evidence to the export.
    selected = {key: value[key] for key in ('block', 'start_frame', 'end_frame', 'sample_rate',
        'session_id', 'revision', 'policy_sha256', 'policy_hash', 'next_cursor', 'complete_content', 'eos')}
    selected['baseline_reading'] = [{key: item[key] for key in item if key != 'recording_speaker_link'}
                                  if type(item) is dict else item for item in rows]
    return selected, c.digest(selected)


def _unit(row, sid, work, frames, block, known, identities):
    require(type(row) is dict and type(row.get('reference_only')) is bool and
            row.get('source') == 'primary' and type(row.get('home_block')) is int and
            0 <= row['home_block'] <= block, 'span_export_primary_shape')
    ref = row.get('source_ref')
    require(type(ref) is str and c.parse_reference(ref)[0] == 'primary', 'span_export_primary_reference')
    if row['reference_only']:
        require(set(row) == {'source_ref', 'source', 'home_block', 'reference_only'} and ref in known and
                known[ref]['home'] == row['home_block'] < block and
                known[ref]['start'] < (block+1)*c.BLOCK_FRAMES and block*c.BLOCK_FRAMES < known[ref]['end'],
                'span_export_unresolved_reference')
        return None
    canonical = row.get('canonical_asr')
    require(type(canonical) is dict and set(canonical) == {'session_id', *c.PRIMARY_FIELDS} and
            canonical['session_id'] == sid and canonical['chunk_id'] in work,
            'span_export_foreign_primary')
    expected = c.primary_evidence(sid, canonical)
    require(c.encoded(row) == c.encoded(dict(expected, reference_only=False)) and
            expected['home_block'] == block and ref not in known and canonical['id'] not in identities,
            'span_export_primary_content_changed')
    a, b = expected['start_frame'], expected['end_frame']; chunk = work[canonical['chunk_id']]
    require(0 <= a < b <= frames and max(0,chunk['start']-5*c.RATE) <= a and
            b <= chunk['start']+chunk['frames']+5*c.RATE, 'span_export_primary_outside_span')
    known[ref] = dict(home=block, start=a, end=b)
    identities.add(canonical['id'])
    return dict(expected, start_seconds=canonical['start'], end_seconds=canonical['end'],
                start_timestamp=_time(canonical['start']), end_timestamp=_time(canonical['end']),
                frame_geometry='outward_envelope_of_original_asr_timestamps',
                speaker_identity_valid=False, accuracy_verified=False)


def run(*, job_dir, api, session_id, capture_id, output_dir, exclusive_writer=False,
        limits=None, export_limits=None, transport=None, cancel=None, native_turns=False):
    require(exclusive_writer is True, 'span_status_exclusive_writer_ack_required')
    require(type(native_turns) is bool, 'span_export_native_turn_option')
    destination = single.target(api, 'operator-selected-unverified', session_id, capture_id, 0)
    path = _destination(output_dir)
    # Anchored logical input/reservation paths are available before any GET.
    # This is only a no-write destination guard; status still performs full
    # independent authority/original validation before granting completion.
    from . import recorder_logical_boundary as logical
    if logical.extra(Path(job_dir)):
        logical.verify_anchor(Path(job_dir))
        cut = single.manifest_read(Path(job_dir)/logical.NAME)
        _destination(path,protected=(Path(job_dir),*logical.protected(cut)))
    bounds = export_limits or ExportLimits()
    require(type(bounds) is ExportLimits, 'span_export_invalid_limit')
    limits = limits or u.Limits(output_frames=history.MAX_FRAMES, deadline_seconds=120)
    guard = u.Guard(limits, cancel)
    http = _Transport(transport or u.HTTPTransport(destination['origin'], guard), guard, bounds)
    output = None; summary = None

    @contextmanager
    def read_completed(report, work, status_guard, protected):
        nonlocal output, summary
        count = (report['frames'] + c.BLOCK_FRAMES - 1) // c.BLOCK_FRAMES
        require(1 <= count <= bounds.pages, 'span_export_page_bound')
        _destination(path, protected)
        output = _Output(path, guard, bounds)
        for name in ('export.json.partial', 'transcript.txt.partial'): output.open(name)
        native_checks=[]
        if native_turns:
            from . import recorder_overlap_candidates as candidates
            from . import recorder_native_overlap as native
            # Reconstruct each exact canonical source WAV from the verified originals.
            _, local_receipts, _, _ = status._local(Path(job_dir), destination, status_guard)
            class Cancellation:
                def is_set(self):
                    guard.check()
                    return False
            native_rows=[]; native_bytes=0
            for cid,w in work.items():
                guard.check()
                selected=dict(start_frame=w['start'],end_frame=w['start']+w['frames'])
                pcm=candidates.pcm_for(local_receipts,report['input_mapping'],selected,Cancellation())
                sha=hashlib.sha256(u.LegacyAppendUploader._header(w['frames'])+pcm).hexdigest()
                endpoint=f'/sessions/{session_id}/chunks/{cid}/native-turn-evidence?source_sha256={sha}&frames={w["frames"]}'
                value=http.request('GET',endpoint)
                native.validate_chunk(value,session_id,cid,w['frames'],w['start'],sha)
                native_bytes+=len(c.encoded(value))
                require(native_bytes<=bounds.output_bytes,'span_export_native_turn_output_bound')
                native_rows.append(value);native_checks.append((endpoint,c.digest(value)))
            document=dict(schema=native.SCHEMA,source_content_binding=report['source_content_binding'],
                frames=report['frames'],chunks=native_rows,
                semantics='validated-native-turn-detection-not-recognition-or-global-identity')
            output.open('native-turns.json.partial')
            output.write('native-turns.json.partial',c.encoded(document)+b'\n')

        metadata = dict(schema=SCHEMA, projection='primary-baseline-v1', sample_rate=c.RATE,
            completion=report, complete_content=False, eos=None, speaker_identity_valid=False,
            word_alignment_verified=False, word_times=None, accuracy_verified=False,
            capture_gap_policy='before_and_after_unknown_no_gap_filling',
            timestamps='original_primary_asr_seconds_with_conservative_frame_envelopes',
            speaker_policy='anonymous_source_local_no_cross_scope_identity',
            optional_transcript_and_speaker_links='not_exported',
            missing_text_meaning='unavailable_or_empty_not_proof_of_silence_or_asr_execution',
            publication='complete.json_required', remote_read_only=True)
        output.write('export.json.partial', c.encoded(metadata)[:-1] + b',"units":[')
        output.write('transcript.txt.partial', (
            'PRIMARY BASELINE TRANSCRIPT / SELECTED SPAN ONLY\n'
            'Original ASR segment timestamps, not verified word alignment or recognition.\n'
            'Speakers are anonymous source-local labels, never a global person identity.\n'
            'Before/after capture gaps are unknown. Missing text does not establish silence.\n'
            'Optional MOSS substitutions and recording speaker links are not exported.\n'
            'Requires complete.json. Each transcript string is quoted; controls are escaped.\n\n').encode('utf-8'))
        if report.get('closure_kind') == 'logical_analysis_boundary':
            output.write('transcript.txt.partial', b'LOGICAL ANALYSIS BOUNDARY: recording is not declared complete. Recognition continuity across sessions is not verified.\n\n')
        known = {}; identities = set(); digests = []; revision = None
        coverage = {cid: dict(chunk_id=cid, span_start_frame=w['start'], span_end_frame=w['start']+w['frames'],
                             primary_units=0, nonempty_text_units=0) for cid, w in work.items()}
        units = nonempty = 0
        for block in range(count):
            status_guard.check()
            page, digest = _page(http, session_id, block, revision, count)
            revision = page['revision']; digests.append(digest); refs = set()
            continuing = {ref for ref, item in known.items() if item['end'] > block*c.BLOCK_FRAMES}
            order = []
            for row in page['baseline_reading']:
                guard.check()
                require(type(row) is dict and type(row.get('source_ref')) is str and row['source_ref'] not in refs,
                        'span_export_duplicate_primary')
                refs.add(row['source_ref'])
                unit = _unit(row, session_id, work, report['frames'], block, known, identities)
                observed = known[row['source_ref']]
                order.append((observed['start'], observed['end'], row['source_ref']))
                if unit is None: continue
                units += 1; require(units <= bounds.units, 'span_export_unit_bound')
                has_text = bool(unit['text'].strip()); nonempty += has_text
                entry = coverage[unit['canonical_asr']['chunk_id']]
                entry['primary_units'] += 1; entry['nonempty_text_units'] += has_text
                output.write('export.json.partial', (b',' if units > 1 else b'') + c.encoded(unit))
                scope = _quote(unit['speaker_scope']) if unit['speaker_scope'] is not None else 'unknown'
                line = f"[{unit['start_timestamp']} - {unit['end_timestamp']}] anonymous source-local {scope}: {_quote(unit['text'])}\n"
                output.write('transcript.txt.partial', line.encode('utf-8'))
            require(order == sorted(order), 'span_export_primary_order')
            require(continuing <= refs, 'span_export_missing_cross_block_reference')
        for entry in coverage.values():
            entry['text_status'] = 'primary_text_observed' if entry['nonempty_text_units'] else 'no_primary_text_observed'
        summary = dict(projection='primary-baseline-v1', frames=report['frames'], transcript_pages=count,
            units=units, nonempty_text_units=nonempty, text_status='primary_text_observed' if nonempty else 'no_primary_text_observed',
            transcript_revision=revision, policy_sha256=c.POLICY_HASH, complete_content=False,
            observation_atomic=False, local_publication=('manifest_commits_three_artifacts' if native_turns
                else 'manifest_commits_artifact_pair'))
        if report.get('closure_kind') == 'logical_analysis_boundary':
            summary.update({key:report[key] for key in ('closure_kind','recording_complete',
                'recognition_continuity_verified','logical_boundary_sha256','eos_meaning')})
        tail = dict(summary=summary, work_text_coverage=list(coverage.values()), transcript_page_sha256=digests)
        output.write('export.json.partial', b'],' + c.encoded(tail)[1:] + b'\n')
        if not nonempty:
            output.write('transcript.txt.partial', b'\nNO PRIMARY TEXT OBSERVED. Recognition may be absent, skipped, or empty; the reason is not established.\n')
        missing = sum(not entry['nonempty_text_units'] for entry in coverage.values())
        output.write('transcript.txt.partial', f'\nWork chunks without primary text: {missing}/{len(work)}. No silence or completeness claim.\n'.encode())
        yield
        # The second full pass catches changed text even if a broken peer reuses
        # a revision. Correct servers also reject any changed revision cursor.
        for block, expected in enumerate(digests):
            _, observed = _page(http, session_id, block, revision, count)
            require(observed == expected, 'span_export_transcript_changed_during_read')
        for endpoint, expected in native_checks:
            require(c.digest(http.request('GET',endpoint))==expected,
                    'span_export_native_turns_changed_during_read')
        output.check()

    def check_outer():
        guard.check()
        return False

    try:
        status.run(job_dir=job_dir, api=api, session_id=session_id, capture_id=capture_id,
                   exclusive_writer=exclusive_writer, limits=limits, transport=http,
                   cancel=check_outer, _completed_read=read_completed)
        guard.check()
        require(output is not None and summary is not None, 'span_export_completed_required')
        return output.publish(summary)
    except c.ReadError:
        raise status.Refused('span_export_transcript_contract_invalid') from None
    finally:
        if output is not None: output.close()


def failure_report(exc):
    report = status.failure_report(exc)
    report.update(schema=SCHEMA, status='refused', export_completed=False,
        read_only=True, remote_read_only=True, local_output_may_be_incomplete=True, local_publication_may_be_uncertain=True,
        next_step='Preserve the exact job and originals. An output without complete.json is incomplete; use a fresh private output directory for any later attempt.')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('job-dir', 'api', 'session-id', 'capture-id', 'output-dir'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--exclusive-writer', action='store_true', required=True)
    parser.add_argument('--deadline-seconds', type=float, default=120)
    parser.add_argument('--native-turns', action='store_true')
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
