"""Read-only audit of the 4s + 4s + 3s recorder batch native silence smoke.

Use --repo for the frozen candidate and --run for its completed output directory.
The input DB and media are never modified. This validates retained artifacts, not
physical capture continuity, Android-device provenance, FTPS, or speech accuracy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import wave


def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(65536):
            value.update(block)
    return value.hexdigest()


def load(path):
    need(path.stat().st_size <= 2 * 1024**2, 'oversized_json')
    return json.loads(path.read_bytes())


def pcm(path):
    with wave.open(str(path), 'rb') as audio:
        need((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) == (1, 2, 16000, 'NONE'), 'noncanonical_pcm')
        frames = audio.getnframes()
        need(0 < frames <= 480000, 'smoke_frame_bound')
        value = audio.readframes(frames + 1)
        need(len(value) == frames * 2, 'pcm_length')
        return value


def audit(repo, run):
    result = load(run/'RESULT.json')
    terminal = load(run/'terminal.json')
    need(result['passed'] is True, 'harness_not_passed')
    need(result['runtime_binary_differences'] == {}, 'runtime_binary_difference')
    need(result['startup']['strict'] is True and result['startup']['inherited_lock'] is True, 'non_strict_start')
    need(result['shutdown']['exit_code'] == 0 and result['shutdown']['terminal'] == terminal, 'shutdown_identity')
    need(terminal['error'] is None and terminal['service_cleanup_error'] is None and terminal['shutdown_errors'] == {}, 'cleanup_error')
    need(terminal['lock_closed'] and terminal['permit_clear'] and terminal['store_safe'] and not terminal['worker_alive'], 'cleanup_predicates')
    need(all(terminal['shutdown']['predicates'].values()), 'shutdown_predicates')
    need(terminal['committed_claim_late_count'] == 0, 'late_claim')
    pins = load(run/'SOURCE_PINS.json')
    pinned_names = {row['path'] for row in pins}
    observed_names = {str(path.relative_to(repo)) for directory in ('audio_backend', 'tools') for path in (repo/directory).rglob('*.py') if '__pycache__' not in path.parts}
    need(pinned_names == observed_names, 'source_inventory_changed')
    for row in pins:
        path = repo/row['path']
        need(path.stat().st_size == row['bytes'] and digest(path) == row['sha256'], 'source_pin_changed:'+row['path'])
    # Import only after verifying the frozen candidate's source inventory.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(repo))
    from audio_backend.recorder_delivery import inspect_delivery
    from audio_backend.recorder_snapshot import inspect_snapshot
    from audio_backend.recorder_sequence import reconcile_snapshots
    manifest = load(run/'intake/manifest.json')
    journal = load(run/'intake/uploader/uploader.json')
    inputs = manifest['inputs']
    need(len(inputs) == 3, 'expected_three_inputs')
    need(journal['complete'] and journal['finalize_requested'] and journal['spool'] == [], 'journal_not_complete')
    need(journal['max_inputs'] == 3 and len(journal['inputs']) == 3 and len(journal['pages']) == 1, 'journal_geometry')
    reports = [item['delivery'] for item in inputs]
    need([r['frames'] for r in reports] == [64000,64000,48000], 'expected_4_4_3_seconds')
    need([r['frame_start'] for r in reports] == [0,64000,128000], 'producer_placement')
    snapshots = [Path(item['snapshot_path']) for item in inputs]
    producer_pcm = []
    for index, (item, entry) in enumerate(zip(inputs, journal['inputs'])):
        path = Path(item['path'])
        need(inspect_delivery(path, validate_metadata=True) == item['delivery'], 'producer_changed')
        need(inspect_snapshot(snapshots[index])['delivery'] == item['delivery'], 'snapshot_changed')
        need(entry['id'] == item['delivery']['source_id'] and entry['clean_eof'], 'journal_input_identity')
        need(entry['frames'] == reports[index]['frames'] and entry['normalized_start'] == reports[index]['frame_start'], 'journal_input_placement')
        need(entry['identity']['path'] == str(snapshots[index]/'audio.wav'), 'journal_snapshot_path')
        need(entry['identity']['sha256'] == reports[index]['wav_sha256'], 'journal_snapshot_digest')
        need(digest(path/'audio.wav') == digest(snapshots[index]/'audio.wav') == reports[index]['wav_sha256'], 'snapshot_audio_digest')
        producer_pcm.append(pcm(path/'audio.wav'))
    sequence = reconcile_snapshots(snapshots)
    need(len(sequence['captures']) == 1 and all(e['relation']=='declared_adjacent' for e in sequence['captures'][0]['edges']), 'sequence_unresolved')
    combined = b''.join(producer_pcm)
    need(len(combined) == 352000 and pcm(run/'synthetic.wav') == combined, 'concatenation_geometry')
    need(not any(combined), 'expected_silence_fixture')
    upload = result['upload']
    need(upload == result['upload_reconciled_resume'], 'completed_resume_changed')
    need(upload['frames'] == journal['emitted_frames'] == journal['published_frames'] == 176000, 'frontier_mismatch')
    need(upload['inputs'] == upload['sources'] == 3 and upload['pages'] == 1, 'upload_geometry')
    need(result['injected_lost_published_seal_responses'] == 1, 'injected_response_loss_count')
    need(not upload['physical_continuity_proven'] and not result['accuracy_qualified'] and not result['recorder_provenance_qualified'], 'overstated_qualification')
    db_path = run/'data/jobs.sqlite3'
    need(not Path(str(db_path)+'-wal').exists(), 'uncheckpointed_wal')
    db_before = digest(db_path)
    db = sqlite3.connect(db_path.as_uri()+'?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    try:
        def rows(sql):
            return [dict(row) for row in db.execute(sql)]
        need(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'sqlite_quick_check')
        sessions = rows('SELECT id, timeline_end FROM sessions')
        batches = rows('SELECT * FROM recording_batches')
        pages = rows('SELECT * FROM recording_pages')
        sources = rows('SELECT * FROM recording_sources ORDER BY source_start_frame')
        work = rows('SELECT w.*,c.path,c.digest,c.status,c.duration,c.timeline_start,c.metadata FROM recording_work w JOIN chunks c ON c.id=w.chunk_id ORDER BY w.position')
        ranges = rows('SELECT * FROM recording_ranges ORDER BY capture_start_frame')
        need(len(sessions)==len(batches)==len(pages)==1 and len(sources)==3 and len(work)==2, 'db_counts')
        need(sessions[0]['id']==result['session_id']==batches[0]['session_id'] and sessions[0]['timeline_end']==11.0, 'db_session_identity')
        need(batches[0]['capture_id']=='synthetic-smoke' and batches[0]['capture_origin_frame']==0 and batches[0]['capture_end_frame']==176000 and batches[0]['eos']==1, 'db_capture_extent')
        page = pages[0]; saved_page = journal['pages'][0]
        need(page['state']=='published' and page['released']==page['eos']==1 and (page['start_frame'],page['end_frame'])==(0,176000), 'db_page_unpublished')
        need(page['id']==saved_page['server_id'] and page['seal_key']==saved_page['seal']['idempotency_key'], 'seal_key_mismatch')
        frozen = json.loads(page['seal_request'])
        need(all(frozen[k]==saved_page['seal'][k] for k in ('end_frame','eos','expected_revision')), 'seal_body_mismatch')
        need([s['source_start_frame'] for s in sources]==[0,64000,128000] and [s['frames'] for s in sources]==[64000,64000,48000], 'db_source_placement')
        retained = []
        for index, source in enumerate(sources):
            piece = saved_page['pieces'][index]
            need(source['id']==piece['server_id'] and source['idempotency_key']==piece['key'], 'source_identity_mismatch')
            need(source['digest']==piece['digest']==reports[index]['wav_sha256']==digest(source['path']), 'source_digest_mismatch')
            value = pcm(source['path']); need(value==producer_pcm[index], 'source_pcm_mismatch'); retained.append(value)
        need(b''.join(retained)==combined, 'source_concatenation_mismatch')
        need([w['frames'] for w in work]==[168000,8000] and [w['capture_start_frame'] for w in work]==[0,168000], 'work_placement')
        need(all(w['status']=='completed' and w['receipt_state']=='durable' and w['native_receipt_status']=='receipt_retained' for w in work), 'native_work_incomplete')
        need(b''.join(pcm(w['path']) for w in work)==combined, 'work_pcm_mismatch')
        native = []
        for row in work:
            need(digest(row['path'])==row['digest'], 'work_file_digest')
            meta = json.loads(row['metadata'])
            need(meta['diarization_backend']=='nemotron3-native' and meta['silence_fast_path'] and meta['models_executed']==['Nemotron-3'], 'native_silence_path')
            need(meta['native_diarization']['finished'] and meta['native_diarization']['processed_until']==11.0, 'native_not_finished')
            native.append(meta['model_execution'])
        need(len(ranges)==4 and sum(r['frames'] for r in ranges)==176000 and all(r['relation']=='owner' for r in ranges), 'owner_range_coverage')
        source_map = {r['id']:r for r in sources}; work_map = {r['chunk_id']:r for r in work}
        cursor = 0
        for row in ranges:
            need(row['capture_start_frame']==cursor, 'range_gap_or_overlap')
            source = source_map[row['source_id']]; target = work_map[row['chunk_id']]
            need(row['capture_start_frame']==source['source_start_frame']+row['source_start_frame']==target['capture_start_frame']+row['work_start_frame'], 'range_coordinate_mismatch')
            cursor += row['frames']
        stream = rows('SELECT * FROM stream_chunks ORDER BY position')
        need(len(stream)==2 and [r['stream_final'] for r in stream]==[0,1] and [r['upload_stream_final'] for r in stream]==[0,1], 'duplicate_or_missing_stream_final')
        policies = rows('SELECT * FROM strict_recording_policy')
        need(len(policies)==1 and policies[0]['policy']=='strict-primary-payload-v2', 'strict_policy_changed')
        controls = rows('SELECT state FROM strict_recording_control')
        need(len(controls)==2 and all(r['state']=='completed' for r in controls), 'strict_control_incomplete')
    finally:
        db.close()
    need(digest(db_path)==db_before, 'audit_db_modified')
    need(result['source_unchanged'] and result['input_unchanged'] and result['external_batch_unchanged'], 'harness_immutability_failure')
    return dict(schema='recorder-batch-smoke-independent-audit-v1', passed=True, inputs=3, sources=3, pages=1, sessions=1,
                frames=176000, source_frames=[64000,64000,48000], source_starts=[0,64000,128000],
                chunks=2, work_frames=[168000,8000], owner_ranges=4,
                concatenated_pcm_sha256=hashlib.sha256(combined).hexdigest(), native_execution=native,
                reported_lost_published_seal_responses=1, fault_injection_evidence='frozen harness source and RESULT counter; per-attempt uploader HTTP transcript not retained',
                exact_saved_seal_matches_db=True, completed_resume_equal=True, candidate_source_pins_verified=len(pins),
                cleanup_verified=True, database_read_only_verified=True, physical_continuity_proven=False,
                ftps_transfer_qualified=False, device_recording_qualified=False, speech_accuracy_qualified=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    report=audit(args.repo.resolve(),args.run.resolve())
    text=json.dumps(report,sort_keys=True,indent=2)+'\n'
    if args.output:
        with args.output.open('x') as stream:
            stream.write(text)
    print(text,end='')


if __name__=='__main__':
    main()
