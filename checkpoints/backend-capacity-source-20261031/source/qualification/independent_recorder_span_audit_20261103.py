"""Independent read-only audit of the fixed generated native span smoke.

Supply production source, isolated candidate, completed run and a new audit
directory outside all three. Original evidence is hashed before and afterward;
SQLite is opened only on an audit copy. This is saved-artifact verification,
not independent observation of historical wait calls, physical capture, speech
accuracy, long-duration operation or deployment qualification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import wave


def need(value, reason):
    if not value:
        raise RuntimeError(reason)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def load(path):
    need(path.stat().st_size <= 4 * 1024**2, 'oversized_json')
    return json.loads(path.read_bytes())


def inventory(root):
    rows = []
    total = 0
    for path in sorted(root.rglob('*')):
        need(not path.is_symlink(), 'evidence_symlink')
        if path.is_file():
            size = path.stat().st_size
            total += size
            need(total <= 1024**3 and len(rows) < 20000, 'evidence_capacity')
            rows.append(dict(path=str(path.relative_to(root)), bytes=size, sha256=digest(path)))
    return rows


def pcm(path):
    with wave.open(str(path), 'rb') as audio:
        need((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
              audio.getcomptype()) == (1, 2, 16000, 'NONE'), 'noncanonical_pcm')
        frames = audio.getnframes()
        need(0 < frames <= 480000, 'smoke_frame_bound')
        value = audio.readframes(frames + 1)
        need(len(value) == frames * 2, 'pcm_length')
        return value


def verify_sources(source, candidate, run):
    pins = load(run / 'SOURCE_PINS.json')
    names = {row['path'] for row in pins}
    def source_names(root):
        return {str(path.relative_to(root)) for directory in ('audio_backend', 'tools')
                for path in (root / directory).rglob('*.py') if '__pycache__' not in path.parts}
    need(names == source_names(candidate) == source_names(source), 'source_inventory_changed')
    native_name = 'audio_backend/native_owned_cell_store.py'
    basis_name = 'qualification/ACCEPTED_RUNTIME_BASIS_20261103.json'
    basis = load(source / basis_name)
    need(basis['accepted_for_fresh_bounded_smoke'] is True and
         basis['general_production_rollout_approved'] is False, 'not_accepted_limited_basis')
    need((source / basis_name).read_bytes() == (candidate / basis_name).read_bytes(), 'candidate_basis_changed')
    original = (source / native_name).read_text()
    need(digest(source / native_name) ==
         'efbd83e985482d47d3337e7290cddfacc680ca77c49452c904c18457211393d8',
         'production_profile_changed')
    expected = original.replace(
        "evidence_sha256='d1eafbcf6bde0071d301f488ba82e69fa3bbadc446c62775276e4998efbdbe67'",
        "evidence_sha256='" + digest(source / basis_name) + "'").replace(
        "sqlite3_package_sha256='096bb1a3902a75a2c3723f2d7863a8d3e9ed627bd862e6538eca2bc517d6e29b'",
        "sqlite3_package_sha256='" + basis['sqlite3_package_sha256'] + "'")
    need((candidate / native_name).read_text() == expected, 'not_exact_accepted_two_literal_candidate')
    for row in pins:
        path = candidate / row['path']
        need(path.stat().st_size == row['bytes'] and digest(path) == row['sha256'], 'candidate_pin_changed:' + row['path'])
        if row['path'] != native_name:
            need(digest(source / row['path']) == row['sha256'], 'unexpected_candidate_difference:' + row['path'])
    return pins


def audit(source, candidate, run, audit_dir):
    for root in (source, candidate, run):
        need(root != audit_dir and root not in audit_dir.parents, 'audit_dir_inside_input')
    need(not audit_dir.exists(), 'audit_dir_exists')
    before = inventory(run)
    envelope = run.parent
    envelope_names = ('LAUNCH_PLAN.json', 'launch.sh', 'ASSETS.json',
                      'CANDIDATE_SOURCE_PINS.json', 'OUTER_WAIT.json', 'timing.txt')
    envelope_before = {name: digest(envelope / name) for name in envelope_names}
    plan = load(envelope / 'LAUNCH_PLAN.json')
    need(plan['asset_manifest_sha256'] == envelope_before['ASSETS.json'] and
         plan['candidate_pins_sha256'] == envelope_before['CANDIDATE_SOURCE_PINS.json'], 'launch_pin_mismatch')
    outer = load(envelope / 'OUTER_WAIT.json')
    need(outer == {'observed_exit_code': 0, 'original_shell_wait_completed': True}, 'outer_wait_failed')
    timing = re.fullmatch(r'real_seconds=([0-9.]+) user_seconds=([0-9.]+) sys_seconds=([0-9.]+)\n?',
                          (envelope / 'timing.txt').read_text())
    need(timing is not None, 'outer_timing_invalid')
    wall, user, system = map(float, timing.groups())
    need(0 < wall < plan['external_timeout_seconds'] == 480 and user >= 0 and system >= 0,
         'outer_timing_limit')
    pins = verify_sources(source, candidate, run)
    result = load(run / 'RESULT.json')
    terminal = load(run / 'terminal.json')
    need(result['passed'] is True and result['input_kind'] == 'explicit_recorder_span', 'smoke_not_passed_span')
    need(result['runtime_binary_differences'] == {}, 'runtime_binary_difference')
    need(result['startup']['strict'] is True and result['startup']['inherited_lock'] is True and
         result['startup']['worker_alive'] is True, 'non_strict_start')
    need(result['shutdown']['exit_code'] == 0 and result['shutdown']['terminal'] == terminal, 'shutdown_identity')
    need(terminal['error'] is None and terminal['service_cleanup_error'] is None and
         terminal['shutdown_errors'] == {}, 'cleanup_error')
    need(terminal['lock_closed'] and terminal['permit_clear'] and terminal['store_safe'] and
         not terminal['worker_alive'], 'cleanup_predicates')
    need(all(terminal['shutdown']['predicates'].values()) and
         terminal['committed_claim_late_count'] == 0, 'terminal_owner_failure')
    need(not any(key.endswith('error') for key in result), 'result_error')
    # Only now import the pinned candidate. No models or service are started.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(candidate))
    from audio_backend.native_owned_cell_store import qualified_runtime, runtime_fingerprints, Budget
    from audio_backend.recorder_delivery import inspect_delivery
    from audio_backend.recorder_snapshot import inspect_snapshot
    from audio_backend.recorder_sequence import reconcile_snapshots
    from audio_backend.recorder_span_intake import span_description
    q = qualified_runtime(run / 'data')
    need(q is not None and (run / 'data').stat().st_dev == 27, 'unqualified_device')
    observed = runtime_fingerprints(Budget())
    need(all(getattr(q, key) == value for key, value in observed.items()), 'current_runtime_mismatch')
    check = sqlite3.connect(':memory:')
    try:
        q.verify(check, run / 'data', Budget())
    finally:
        check.close()
    assets = load(run / 'ASSET_VERIFIED.json')
    expected_assets = [row for row in load(envelope / 'ASSETS.json')['files'] if row.get('role') != 'sample']
    need(assets == expected_assets and len(assets) >= 32, 'asset_inventory_scope')
    for row in assets:
        path = Path(row['path'])
        need(path.stat().st_size == row['bytes'] and digest(path) == row['sha256'], 'asset_changed')
    manifest = load(run / 'intake/manifest.json')
    journal = load(run / 'intake/uploader/uploader.json')
    inputs = manifest['inputs']
    need(len(inputs) == 3, 'expected_three_inputs')
    original_inputs_before = {item['path']: inventory(Path(item['path'])) for item in inputs}
    reports = [item['delivery'] for item in inputs]
    need([r['frames'] for r in reports] == [64000, 64000, 48000], 'fixture_lengths')
    need([r['sequence'] for r in reports] == [11, 12, 13] and
         [r['epoch'] for r in reports] == [7, 7, 7] and
         [r['frame_start'] for r in reports] == [100003, 164003, 228003], 'original_coordinates')
    need([r['close_reason'] for r in reports] == ['ROTATION', 'ROTATION', 'INTERRUPTION'], 'original_closure')
    need([item['span_start_frame'] for item in inputs] == [0, 64000, 128000], 'span_local_coordinates')
    need(manifest['acknowledgments'] == {'exclusive_writer': True, 'span_local_closure': True}, 'missing_acknowledgments')
    description = manifest['span']
    need(description == span_description(reports), 'manifest_description')
    need(description['eos_meaning'] == 'selected_span_only' and
         description['hardware_frame_mapping'] == 'UNESTABLISHED' and
         description['before_span_gap_frames'] is None and description['after_span_gap_frames'] is None and
         not description['recorder_stop_observed'] and not description['recovered_tail_uncertain'] and
         not description['capture_completeness_proven'] and not description['physical_continuity_proven'],
         'overstated_span_scope')
    need(journal['complete'] and journal['finalize_requested'] and journal['spool'] == [], 'journal_not_complete')
    need(journal['max_inputs'] == len(journal['inputs']) == 3 and len(journal['pages']) == 1, 'journal_geometry')
    snapshots = [Path(item['snapshot_path']) for item in inputs]
    combined = []
    for index, (item, entry) in enumerate(zip(inputs, journal['inputs'])):
        path = Path(item['path'])
        need(inspect_delivery(path, validate_metadata=True) == item['delivery'], 'producer_changed')
        need(inspect_snapshot(snapshots[index])['delivery'] == item['delivery'], 'snapshot_changed')
        need(entry['id'] == reports[index]['source_id'] and entry['clean_eof'], 'journal_input_identity')
        need(entry['frames'] == reports[index]['frames'] and
             entry['normalized_start'] == item['span_start_frame'], 'journal_input_placement')
        need(entry['identity']['path'] == str(snapshots[index] / 'audio.wav') and
             entry['identity']['sha256'] == reports[index]['wav_sha256'], 'journal_snapshot_identity')
        need(digest(path / 'audio.wav') == digest(snapshots[index] / 'audio.wav') ==
             reports[index]['wav_sha256'], 'snapshot_audio_digest')
        combined.append(pcm(path / 'audio.wav'))
    sequence = reconcile_snapshots(snapshots)
    need(len(sequence['captures']) == 1 and
         all(e['relation'] == 'declared_adjacent' and not e['reasons']
             for e in sequence['captures'][0]['edges']), 'sequence_unresolved')
    combined = b''.join(combined)
    need(len(combined) == 352000 and not any(combined) and
         pcm(run / 'synthetic.wav') == combined, 'generated_silence_geometry')
    upload = result['upload']
    need(upload == result['upload_reconciled_resume'] == result['upload_second_resume'], 'completed_resume_changed')
    frozen_seal = journal['pages'][0]['seal']
    expected_replay = dict(method='POST',
        path='/sessions/' + result['session_id'] + '/recordings/synthetic-smoke/pages/1/seal',
        key=None, body=frozen_seal)
    resumed = result['span_resume_mutations']
    need(expected_replay in result['span_mutations'] and len(resumed) == 2 and
         all(row == expected_replay for row in resumed), 'resume_not_exact_frozen_seal_only')
    need(result['span_resume_check'] == dict(no_new_sources=True, exact_frozen_seal_replays=2),
         'resume_check_mismatch')
    need(upload['status'] == 'selected_span_accepted_with_eos' and upload['span'] == description, 'returned_span_changed')
    need(upload['frames'] == journal['emitted_frames'] == journal['published_frames'] == 176000 and
         upload['inputs'] == upload['sources'] == result['page']['source_count'] == 3 and
         upload['pages'] == 1, 'upload_geometry')
    need(upload['source_ids'] == [r['source_id'] for r in reports], 'source_ids_changed')
    for key in ('analysis_completion_verified', 'automatic_intake_enabled', 'physical_continuity_proven',
                'capture_completeness_proven', 'target_identity_authenticated', 'producer_identity_authenticated'):
        need(upload[key] is False, 'overstated_upload:' + key)
    need(result['injected_lost_published_seal_responses'] == 1 and
         not result['accuracy_qualified'] and not result['recorder_provenance_qualified'], 'overstated_harness')
    need(result['source_unchanged'] and result['input_unchanged'] and result['external_batch_unchanged'], 'harness_immutability')
    need(0 < result['peak_sampled_rss'] <= 6 * 1024**3, 'sampled_rss_limit')
    original_db = run / 'data/jobs.sqlite3'
    sidecars = [Path(str(original_db) + suffix) for suffix in ('-wal', '-shm', '-journal')]
    # Closed successful service should leave no recovery work. Never run recovery
    # against originals, and do not ignore WAL by silently setting immutable.
    need(not any(path.exists() for path in sidecars), 'unclosed_database_sidecars')
    audit_dir.mkdir(mode=0o700)
    db_path = audit_dir / 'jobs.sqlite3'
    shutil.copyfile(original_db, db_path)
    need(digest(db_path) == digest(original_db), 'database_copy_changed')
    db = sqlite3.connect(db_path.as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    native = []
    try:
        def rows(sql):
            return [dict(row) for row in db.execute(sql)]
        need(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'sqlite_integrity')
        sessions = rows('SELECT id,timeline_end FROM sessions')
        batches = rows('SELECT * FROM recording_batches')
        pages = rows('SELECT * FROM recording_pages')
        sources = rows('SELECT * FROM recording_sources ORDER BY source_start_frame')
        work = rows('SELECT w.*,c.path,c.digest,c.status,c.duration,c.next_offset,c.metadata '
                    'FROM recording_work w JOIN chunks c ON c.id=w.chunk_id ORDER BY w.position')
        ranges = rows('SELECT * FROM recording_ranges ORDER BY capture_start_frame')
        need(len(sessions) == len(batches) == len(pages) == 1 and len(sources) == 3 and len(work) == 2, 'db_counts')
        need(sessions[0]['id'] == result['session_id'] == batches[0]['session_id'] ==
             manifest['target']['session_id'] == upload['session_id'] and sessions[0]['timeline_end'] == 11.0,
             'session_identity')
        batch = batches[0]
        need(batch['capture_id'] == upload['capture_id'] == 'synthetic-smoke' and
             (batch['capture_origin_frame'], batch['capture_end_frame'], batch['eos']) == (0, 176000, 1), 'db_span_extent')
        page = pages[0]
        saved_page = journal['pages'][0]
        need((page['state'], page['released'], page['eos'], page['start_frame'], page['end_frame']) ==
             ('published', 1, 1, 0, 176000), 'db_page_unpublished')
        need(page['id'] == saved_page['server_id'] and page['seal_key'] ==
             saved_page['seal']['idempotency_key'], 'frozen_seal_identity')
        frozen = json.loads(page['seal_request'])
        need(all(frozen[k] == saved_page['seal'][k] for k in ('end_frame', 'eos', 'expected_revision')), 'frozen_seal_body')
        need([s['source_start_frame'] for s in sources] == [0, 64000, 128000] and
             [s['frames'] for s in sources] == [64000, 64000, 48000], 'db_source_geometry')
        retained = []
        for index, row in enumerate(sources):
            piece = saved_page['pieces'][index]
            need(row['id'] == piece['server_id'] and row['idempotency_key'] == piece['key'], 'source_identity_mismatch')
            need(row['digest'] == piece['digest'] == reports[index]['wav_sha256'] == digest(row['path']), 'source_digest')
            need(row['batch_id'] == batch['id'] and row['page_id'] == page['id'], 'source_owner')
            retained.append(pcm(row['path']))
        need(b''.join(retained) == combined, 'source_pcm_mismatch')
        need([w['frames'] for w in work] == [168000, 8000] and
             [w['capture_start_frame'] for w in work] == [0, 168000], 'work_placement')
        need(all(w['status'] == 'completed' and w['receipt_state'] == 'durable' and
                 w['native_receipt_status'] == 'receipt_retained' and
                 w['next_offset'] == w['duration'] == w['frames'] / 16000 for w in work), 'native_work_incomplete')
        need(b''.join(pcm(w['path']) for w in work) == combined, 'work_pcm_mismatch')
        for row in work:
            need(digest(row['path']) == row['digest'], 'work_file_digest')
            meta = json.loads(row['metadata'])
            need(meta['diarization_backend'] == 'nemotron3-native' and meta['silence_fast_path'] and
                 meta['models_executed'] == ['Nemotron-3'], 'native_silence_path')
            need(meta['native_diarization']['finished'] and
                 meta['native_diarization']['processed_until'] == 11.0, 'native_not_finished')
            native.append(meta['model_execution'])
        need(len(ranges) == 4 and sum(r['frames'] for r in ranges) == 176000 and
             all(r['relation'] == 'owner' for r in ranges), 'owner_range_coverage')
        source_map = {r['id']: r for r in sources}
        work_map = {r['chunk_id']: r for r in work}
        cursor = 0
        for row in ranges:
            need(row['capture_start_frame'] == cursor, 'range_gap_or_overlap')
            s = source_map[row['source_id']]
            w = work_map[row['chunk_id']]
            need(cursor == s['source_start_frame'] + row['source_start_frame'] ==
                 w['capture_start_frame'] + row['work_start_frame'], 'range_coordinates')
            cursor += row['frames']
        stream = rows('SELECT * FROM stream_chunks ORDER BY position')
        need(len(stream) == 2 and [r['stream_final'] for r in stream] == [0, 1] and
             [r['upload_stream_final'] for r in stream] == [0, 1], 'stream_final_count')
        policies = rows('SELECT * FROM strict_recording_policy')
        need(len(policies) == 1 and policies[0]['policy'] == 'strict-primary-payload-v2', 'strict_policy')
        controls = rows('SELECT state,nonce FROM strict_recording_control')
        need(len(controls) == 2 and all(r['state'] == 'completed' and r['nonce'] is None for r in controls), 'control_incomplete')
    finally:
        db.close()
    need(inventory(run) == before, 'original_evidence_modified')
    need({name: digest(envelope / name) for name in envelope_names} == envelope_before,
         'original_envelope_modified')
    need({item['path']: inventory(Path(item['path'])) for item in inputs} == original_inputs_before,
         'original_delivery_modified')
    verify_sources(source, candidate, run)
    return dict(schema='recorder-span-native-independent-audit-v1', passed=True,
        sessions=1, inputs=3, sources=3, pages=1, frames=176000, chunks=2,
        original_epoch=7, original_sequence=[11, 12, 13], original_frame_start=100003,
        original_close_reason='INTERRUPTION', selected_span_only_eos=True,
        completed_exact_resume_count=2, completed_resume_no_new_sources=True,
        exact_frozen_seal_replays=len(resumed),
        frozen_seal_sha256=hashlib.sha256(json.dumps(frozen_seal, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest(),
        source_pcm_exact=True, work_pcm_exact=True, owner_ranges_exact=True,
        concatenated_pcm_sha256=hashlib.sha256(combined).hexdigest(), native_execution=native,
        current_runtime_verified=observed, candidate_source_pins_verified=len(pins),
        current_assets_verified=len(assets), terminal_cleanup_verified=True,
        original_outer_wait_exit_code=0, elapsed_seconds=wall, aggregate_cpu_seconds=user + system,
        peak_sampled_rss=result['peak_sampled_rss'], envelope_hashes=envelope_before,
        original_evidence_unchanged=True, database_inspected_on_separate_readonly_copy=True,
        evidence=before, limitations=[
            'Original wait/reap calls are saved harness receipts, not independently re-observed history.',
            'Seal response loss and repeated-resume exact seal replay use pinned harness instrumentation.',
            'Generated silence exercises native diarization, not speech ASR accuracy or speaker identity.',
            'No device, FTPS, physical continuity, capture completeness, deployment or endurance qualification.',
            'Sampled resources are not instantaneous hard quotas.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'candidate', 'run', 'audit-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.source.resolve(), args.candidate.resolve(), args.run.resolve(), args.audit_dir.resolve())
    report['audit_source_sha256'] = digest(Path(__file__))
    text = json.dumps(report, sort_keys=True, indent=2) + '\n'
    with (args.audit_dir / 'INDEPENDENT_AUDIT.json').open('x') as stream:
        stream.write(text)
    print(json.dumps({k: v for k, v in report.items() if k not in ('evidence', 'native_execution')}, sort_keys=True))


if __name__ == '__main__':
    main()
