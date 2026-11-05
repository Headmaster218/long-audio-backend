"""Offline, explicit post-primary MOSS candidates. Never changes primary results."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import stat
import threading
import time
import wave

from . import recorder_incremental_span_status as status
from . import recorder_incremental_span_export as export
from . import recording_uploader as u
from . import recording_uploader_history as history
from .moss_handy_controller import MossController
from .moss_handy_profile import profile
from .moss_handy_runner import float_bytes, samples_from_pcm
from .native_lexical_alternatives import SourceSupport

NAME = 'handy-moss-q8_0-cpu-experimental-v1'
SCHEMA = 'recorder-post-primary-overlap-candidates-v1'
require = status.require

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':'),
        default=lambda raw: {'bytes_sha256': digest(raw)} if isinstance(raw, bytes) else None).encode())

def read(path, cap):
    """Read bounded private regular files without following aliases."""
    status.single.control(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(before.st_size <= cap, 'overlap_input_size')
        raw = os.read(fd, cap + 1)
        require(len(raw) == before.st_size and u._signature(before) ==
                u._signature(os.fstat(fd)) == u._signature(path.lstat()), 'overlap_input_changed')
        return raw
    finally:
        os.close(fd)

def ranges(value, frames):
    require(type(value) is list and 1 <= len(value) <= 8, 'overlap_range_count')
    previous = 0
    for row in value:
        require(type(row) is dict and set(row) == {'start_frame', 'end_frame'}, 'overlap_range_fields')
        a, b = row['start_frame'], row['end_frame']
        require(type(a) is int and type(b) is int and previous <= a < b <= frames and
                b-a <= 320000, 'overlap_range_bound')
        previous = b
    return value

def snapshot(root, baseline, cancel):
    """Existing finalized-source validators; no network, repair or adoption."""
    guard = u.Guard(u.Limits(output_frames=history.MAX_FRAMES, deadline_seconds=120), cancel.is_set)
    header = status.single.manifest_read(root / 'capture.json')
    local = status._local(root, header['target'], guard)
    header, receipts, records, _ = local
    policy = status.span.POLICY.for_job(root, header, receipts, records,
                                       lambda: status.observer._remaining(guard))
    journal, identity = status._journal(root, header['target'], header, receipts, guard, policy=policy)
    manifest_raw = read(baseline / 'complete.json', 65536)
    manifest = json.loads(manifest_raw)
    require(manifest.get('schema') == export.SCHEMA and manifest.get('status') == 'exported' and
            manifest.get('projection') == 'primary-baseline-v1' and
            set(manifest.get('artifacts', {})) == {'export.json', 'transcript.txt'}, 'overlap_export_required')
    artifacts = {}
    for name, binding in manifest['artifacts'].items():
        raw = read(baseline / name, export.MAX_OUTPUT_BYTES)
        require(binding == {'bytes': len(raw), 'sha256': digest(raw)}, 'overlap_export_digest')
        artifacts[name] = raw
    data = json.loads(artifacts['export.json']); done = data['completion']
    frames = journal.data['completed_frames']
    require(data.get('schema') == export.SCHEMA and data.get('projection') == 'primary-baseline-v1' and
            data.get('sample_rate') == 16000 and done.get('analysis_completion_verified') is True and
            done.get('blocked_chunks') == [] and done.get('frames') == frames == manifest.get('frames') and
            done.get('session_id') == header['target']['session_id'] and
            done.get('capture_id') == header['target']['capture_id'], 'overlap_export_source_mismatch')
    expected = []; start = 0
    for i, receipt in enumerate(receipts):
        delivery = receipt['delivery']; end = start + delivery['frames']
        expected.append(dict(ordinal=i, span_start_frame=start, span_end_frame=end,
            original_epoch=delivery['epoch'], original_sequence=delivery['sequence'],
            original_epoch_frame_start=delivery['frame_start'],
            original_epoch_frame_end=delivery['frame_start'] + delivery['frames']))
        start = end
    require(done.get('input_mapping') == expected, 'overlap_export_mapping_mismatch')
    require(done.get('source_content_binding') == status.source_content_binding(header, records, journal, policy),
            'overlap_export_source_content_binding_required')
    require(done.get('schema') == status.SCHEMA and done.get('status') == 'completed',
            'overlap_export_completion_required')
    require(all(done.get(key) == value for key, value in policy.result_fields(records, header).items()),
            'overlap_export_original_policy_mismatch')
    require(all(manifest.get(key) == value for key, value in data['summary'].items()),
            'overlap_export_summary_mismatch')
    guard.check()
    binding = dict(export_complete_sha256=digest(manifest_raw),
        export_sha256=digest(artifacts['export.json']), transcript_sha256=digest(artifacts['transcript.txt']),
        finalized_local_sha256=fingerprint(local), journal_sha256=fingerprint(identity), frames=frames)
    return binding, receipts, expected, data['work_text_coverage']

def pcm_for(receipts, mapping, selected, cancel):
    blocks = []
    for receipt, row in zip(receipts, mapping):
        a = max(selected['start_frame'], row['span_start_frame'])
        b = min(selected['end_frame'], row['span_end_frame'])
        if a >= b: continue
        guard = u.Guard(u.Limits(deadline_seconds=120), cancel.is_set)
        original = u.Original(str(Path(receipt['snapshot_path']) / 'audio.wav'), guard)
        try:
            with wave.open(original.file, 'rb') as stream:
                require((stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (1, 2, 16000),
                        'overlap_pcm_format')
                stream.setpos(a-row['span_start_frame']); blocks.append(stream.readframes(b-a))
            original.verify()
        finally:
            original.close()
    pcm = b''.join(blocks)
    require(len(pcm) == 2*(selected['end_frame']-selected['start_frame']), 'overlap_pcm_coverage')
    return pcm

def voice_tracks(raw, scope):
    """Label retention is independent of valid timestamps or closed phrases."""
    from .moss_handy_adapter import _PHRASE
    if raw is None: return [], ['raw_unavailable']
    text = raw.decode('utf-8'); tracks = {}; errors = []
    # Remove only recognizable time tokens from the lexical candidate. Unknown
    # bracket text is retained, including malformed timing and other debris.
    times = re.compile(r'\[(?:[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)|[+-]?(?:nan|inf))\]', re.I)
    labels = list(re.finditer(r'\[(S[0-9]{1,15})\]', text))
    prefix = text[:labels[0].start()] if labels else text
    prefix = times.sub('', prefix)
    if prefix.strip(): tracks['__unassigned__'] = [prefix]; errors.append('unassigned_text')
    for index, label in enumerate(labels):
        end = labels[index+1].start() if index+1 < len(labels) else len(text)
        body = text[label.end():end]
        # Only boundary time tokens can be formatting. Interior bracketed text
        # such as spoken code [123] must survive unchanged.
        body = re.sub(r'^(?:\s*'+times.pattern+r')+', '', body, flags=re.I)
        body = re.sub(r'(?:'+times.pattern+r'\s*)+$', '', body, flags=re.I)
        tracks.setdefault(label[1], []).append(body)
    cursor = 0
    for match in _PHRASE.finditer(text):
        if text[cursor:match.start()].strip(): errors.append('unparsed_text')
        cursor = match.end()
    if text[cursor:].strip(): errors.append('unparsed_text')
    if not labels: errors.append('no_model_labels')
    return [dict(channel=label, text=' '.join(words), scope_id=scope)
            for label, words in tracks.items()], sorted(set(errors))

def raw_label_spans(raw):
    if raw is None: return None
    text = raw.decode('utf-8')
    labels = list(re.finditer(r'\[(S[0-9]{1,15})\]', text))
    rows = [dict(channel=None, raw_text=text[:labels[0].start()] if labels else text)]
    for index, label in enumerate(labels):
        end = labels[index+1].start() if index+1 < len(labels) else len(text)
        rows.append(dict(channel=label[1], raw_text=text[label.start():end]))
    return rows

class Output:
    def __init__(self, path):
        self.path = path
        self.parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.parent_identity = export._directory_identity(os.fstat(self.parent))
        self.fd = None
        try:
            require(export._directory_identity(path.parent.lstat()) == self.parent_identity,
                    'overlap_output_parent_changed')
            os.mkdir(path.name, 0o700, dir_fd=self.parent)
            self.fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent)
            self.identity = export._directory_identity(os.fstat(self.fd)); self.check()
        except BaseException:
            self.close(); raise
    def check(self):
        status.single.private_directory(self.path)
        require(export._directory_identity(self.path.parent.lstat()) == self.parent_identity ==
                export._directory_identity(os.fstat(self.parent)), 'overlap_output_parent_changed')
        require(export._directory_identity(self.path.lstat()) == self.identity ==
                export._directory_identity(os.fstat(self.fd)), 'overlap_output_changed')
    def write(self, name, value, raw=False):
        self.check(); data = value if raw else u._json(value)
        require(len(data) <= 2*1024**2, 'overlap_output_size')
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data); stream.flush(); os.fsync(fd)
        finally: os.close(fd)
        os.fsync(self.fd); self.check()
        return dict(bytes=len(data), sha256=digest(data))
    def close(self):
        if self.fd is not None: os.close(self.fd); self.fd = None
        if self.parent is not None: os.close(self.parent); self.parent = None

def run(*, job_dir, export_dir, selected_ranges, model, output_dir, exclusive_inputs=False,
        aggregate_budget_managed=False, cancel=None, deadline_seconds=1200, _controller=MossController):
    require(exclusive_inputs is True and aggregate_budget_managed is True, 'overlap_exclusive_source_and_resource_ack_required')
    cancel = cancel or threading.Event()
    require(type(deadline_seconds) is int and 1 <= deadline_seconds <= 3600, 'overlap_deadline_bound')
    deadline = time.monotonic() + deadline_seconds
    def check():
        if time.monotonic() >= deadline: cancel.set()
        require(not cancel.is_set(), 'overlap_cancelled_or_deadline')
    check()
    root, baseline = Path(job_dir), Path(export_dir)
    for path in (root, baseline):
        require(path.is_absolute() and path.resolve(strict=True) == path, 'overlap_absolute_input')
        status.single.private_directory(path)
    output = export._destination(output_dir, protected=(root, baseline))
    locks = []; writer = None; current = None
    results = []; binding = None
    try:
        for path in (root/'intake.lock', root/'uploader/uploader.lock'):
            fd = status.engine.held_lock(path); locks.append((fd, path, u._signature(os.fstat(fd))))
        binding, receipts, mapping, work = snapshot(root, baseline, cancel)
        chosen = ranges(selected_ranges, binding['frames'])
        export._destination(output, protected=(root, baseline,
            *(Path(r['path']) for r in receipts), *(Path(r['snapshot_path']) for r in receipts)))
        check()
        # No mutable output until every selected range and source validates.
        selected_pcm = [pcm_for(receipts, mapping, row, cancel) for row in chosen]
        pinned = snapshot(root, baseline, cancel)[0]
        require(pinned == binding, 'overlap_source_changed')
        selected_profile = profile(NAME)
        plan = dict(schema=SCHEMA, source=binding, ranges=chosen, profile_sha256=selected_profile.sha256,
                    provenance='completed-export-and-finalized-originals-not-native-support-capture')
        plan_sha = digest(u._json(plan)); writer = Output(output); writer.write('plan.json', plan)
        for i, (row, pcm) in enumerate(zip(chosen, selected_pcm)):
            check()
            require(snapshot(root, baseline, cancel)[0] == binding, 'overlap_source_changed')
            require(all(u._signature(os.fstat(fd)) == u._signature(path.lstat()) == pin
                        for fd, path, pin in locks), 'overlap_lock_changed')
            # Exact digital zero is a byte-level input fact, not a VAD threshold
            # or a claim that ordinary background noise contains no speech.
            if not any(pcm):
                item = dict(range=row, state='skipped_exact_zero_pcm',
                    complete_content=False, speaker_identity_valid=False,
                    transcripts=[], parse_errors=[], raw_label_spans=None,
                    model_invoked=False, model_call_count=0,
                    reason='all_selected_pcm16le_bytes_are_zero',
                    source_binding=dict(schema='original-pcm-slice-v1',
                        pcm16le_sha256=digest(pcm), frames=len(pcm)//2,
                        input_mapping=mapping, native_core_attested=False,
                        native_owned_capture_authority=False))
                writer.write('skipped-%02d.json' % i, item)
                results.append(item)
                continue
            support = SourceSupport(binding['export_sha256'], plan_sha,
                selected_profile.payload()['runtime_manifest_sha256'],
                digest(pcm), binding['frames'], row['start_frame'], row['end_frame'],
                row['start_frame'], row['end_frame'], digest(pcm),
                digest(float_bytes(samples_from_pcm(pcm))))
            intersections = [dict(chunk_id=w['chunk_id'], span_start_frame=w['span_start_frame'],
                span_end_frame=w['span_end_frame']) for w in work
                if w['span_start_frame'] < row['end_frame'] and w['span_end_frame'] > row['start_frame']]
            scratch = output / ('owned-%02d' % i); scratch.mkdir(mode=0o700)
            controller = _controller(scratch, profile_name=NAME, model_path=str(model))
            job = controller.submit(support, pcm); current = (controller, job)
            # This durable marker never authorizes a replay, even if outcome is unknown.
            writer.write('call-%02d.intent.json' % i, dict(support=asdict(support), job_id=job,
                invocation_may_have_started=True, replay_allowed=False,
                source_binding=dict(schema='original-pcm-slice-v1',
                    support_container_semantics='caller-declared-original-slice-not-native-core',
                    native_runtime_field='fixed-MOSS-runtime-profile-identity-not-primary-native-attestation',
                    native_owned_capture_authority=False, native_core_attested=False,
                    export_declared_primary_work=intersections, input_mapping=mapping)))
            check()
            stopped = threading.Event()
            def forward_cancel():
                while not stopped.wait(.05):
                    if time.monotonic() >= deadline: cancel.set()
                    if cancel.is_set(): controller.cancel(job); return
            watcher = threading.Thread(target=forward_cancel, daemon=True); watcher.start()
            try: observed = controller.run(job)
            finally: stopped.set(); watcher.join(); current = None
            writer.write('call-%02d.status.json' % i, observed)
            cleanup = observed.get('cleanup') or {}
            require(cleanup.get('cleanup_verified') is True and
                    cleanup.get('original_handle_waited_and_reaped') is True and
                    cleanup.get('remaining_live_owned') == [], 'overlap_cleanup_unresolved')
            require(not cancel.is_set() and snapshot(root, baseline, cancel)[0] == binding,
                    'overlap_source_or_cancel_changed')
            raw = controller.exact_raw(job)
            if raw is not None: writer.write('call-%02d.raw.txt' % i, raw, raw=True)
            projection = observed.get('result') or {}
            tracks, parse_errors = voice_tracks(raw, support.support_id)
            item = dict(range=row, state=observed.get('state'), complete_content=False,
                        speaker_identity_valid=False, diagnostic=projection,
                        transcripts=tracks, parse_errors=parse_errors, raw_label_spans=raw_label_spans(raw),
                        source_binding=dict(schema='original-pcm-slice-v1', input_mapping=mapping,
                            export_declared_primary_work=intersections,
                            native_core_attested=False, native_owned_capture_authority=False,
                            core_fields_mean='original-slice-bounds-and-PCM-hash',
                            native_runtime_field='fixed-MOSS-runtime-profile-identity-not-primary-native-attestation'))
            writer.write('candidate-%02d.json' % i, item); results.append(item)
            require(observed.get('state') == 'returned_diagnostic' and observed.get('publication_valid') is True,
                    'overlap_candidate_failed')
        require(snapshot(root, baseline, cancel)[0] == binding, 'overlap_source_changed')
        check()
        require(all(u._signature(os.fstat(fd)) == u._signature(path.lstat()) == pin
                    for fd, path, pin in locks), 'overlap_lock_changed')
        result = dict(schema=SCHEMA, status='candidates_recorded', source=binding,
            selected_ranges=chosen, candidate_count=len(results),
            model_call_count=sum(r['state'] != 'skipped_exact_zero_pcm' for r in results),
            exact_zero_ranges_skipped=sum(r['state'] == 'skipped_exact_zero_pcm' for r in results),
            missing_or_unparsed_candidates=sum(
                r['state'] != 'skipped_exact_zero_pcm' and
                (not r['transcripts'] or bool(r['parse_errors'])) for r in results),
            complete_content=False,
            speaker_identity_valid=False, accuracy_verified=False, waveform_separation=False,
            unselected_audio='not_processed', primary_baseline='unchanged', replay_allowed=False)
        writer.write('complete.json', result)
        return result
    except BaseException:
        if current is not None: current[0].cancel(current[1])
        if writer is not None:
            writer.write('failure.json', dict(schema=SCHEMA, status='failed_or_interrupted',
                candidates_recorded=len(results), replay_allowed=False, source=binding))
        raise
    finally:
        if writer is not None: writer.close()
        for fd, _, _ in reversed(locks): os.close(fd)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('job-dir', 'export-dir', 'ranges', 'model', 'output-dir'):
        parser.add_argument('--'+key, required=True)
    parser.add_argument('--deadline-seconds', type=int, default=1200)
    parser.add_argument('--exclusive-inputs', action='store_true')
    parser.add_argument('--aggregate-budget-managed', action='store_true')
    args = parser.parse_args(argv); cancel = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM): signal.signal(sig, lambda *_: cancel.set())
    try:
        value = json.loads(read(Path(args.ranges), 16384))
        result = run(job_dir=args.job_dir, export_dir=args.export_dir, selected_ranges=value,
                     model=args.model, output_dir=args.output_dir, exclusive_inputs=args.exclusive_inputs,
                     aggregate_budget_managed=args.aggregate_budget_managed, cancel=cancel,
                     deadline_seconds=args.deadline_seconds)
        print(json.dumps(result)); return 0
    except Exception:
        print(json.dumps(dict(schema=SCHEMA, status='refused_or_incomplete', replay_allowed=False))); return 2

if __name__ == '__main__':
    raise SystemExit(main())
