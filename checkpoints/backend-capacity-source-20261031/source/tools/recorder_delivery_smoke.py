"""Explicit bounded local producer-delivery integration experiment, not an importer.

Accept one complete synthetic capture in an exclusively controlled local parent.
No FTPS connection, authentication claim, session retry, journal activation,
resampling, gap filling, original mutation, or automatic ingestion is performed.
The child strict smoke retains its real runtime, resource and cleanup gates.
"""
import argparse
import json
from pathlib import Path
import sys
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strict_uploader_smoke as smoke
from audio_backend.recorder_delivery import inspect_delivery, Refused
from audio_backend.recorder_snapshot import create_snapshot, inspect_snapshot


def validate_delivery(directory):
    report = inspect_delivery(directory, validate_metadata=True)
    required = {'sequence': 0, 'epoch': 0, 'frame_start': 0, 'boundary': 'START',
                'close_reason': 'STOP', 'sample_rate_hz': 16000, 'channels': 1,
                'trusted_previous_seam': False}
    if any(report[key] != value for key, value in required.items()):
        raise Refused('experiment_requires_one_complete_initial_mono16k_capture')
    if not 0 < report['frames'] <= 30 * 16000:
        raise Refused('experiment_duration_bound')
    smoke.validate_input(Path(directory) / 'audio.wav', report['wav_sha256'])
    return report


def durable_mapping(database, expected, sid):
    db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {name: [dict(row) for row in db.execute('select * from ' + name + ' limit 3')]
                  for name in ('recording_sources', 'recording_batches', 'recording_pages')}
        if any(len(rows) != 1 for rows in tables.values()):
            raise Refused('experiment_requires_single_backend_source_batch_page')
        source, batch, page = (tables[name][0] for name in ('recording_sources', 'recording_batches', 'recording_pages'))
        ok = (source['digest'] == expected['wav_sha256'] and source['frames'] == expected['frames']
              and source['bytes'] == expected['wav_bytes'] and source['source_start_frame'] == 0
              and source['batch_id'] == batch['id'] == page['batch_id'] and source['page_id'] == page['id']
              and batch['session_id'] == sid and batch['capture_id'] == 'synthetic-smoke'
              and batch['capture_origin_frame'] == page['start_frame'] == 0
              and batch['capture_end_frame'] == page['end_frame'] == expected['frames']
              and page['state'] == 'published' and page['released'] == page['eos'] == batch['eos'] == 1)
        if not ok or smoke.digest_file(Path(source['path'])) != expected['wav_sha256']:
            raise Refused('experiment_durable_mapping_mismatch')
        return dict(session_id=batch['session_id'], capture_id=batch['capture_id'],
                    batch_id=batch['id'], page_id=page['id'], page_number=page['page_number'],
                    backend_source_id=source['id'], backend_idempotency_key=source['idempotency_key'],
                    source_start_frame=source['source_start_frame'], frames=source['frames'],
                    wav_sha256=source['digest'], capture_origin_frame=batch['capture_origin_frame'])
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('delivery', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--assets', required=True, type=Path)
    args = parser.parse_args(argv)
    out = args.output.resolve()
    out.mkdir(mode=0o700, exist_ok=False)
    result = dict(schema='recorder-delivery-integration-experiment-v1', passed=False,
                  input_kind='explicit_producer_delivery_experiment',
                  nested_input_kind_is_legacy_label=True,
                  automatic_ingestion_enabled=False, recorder_hardware_qualified=False,
                  physical_continuity_proven=False, source_identity_authenticated=False)
    original_argv = sys.argv
    try:
        before = validate_delivery(args.delivery)
        result['delivery'] = before
        snapshot = out / 'snapshot'
        result['snapshot_created'] = create_snapshot(args.delivery, snapshot)
        observed = inspect_snapshot(snapshot)
        result['snapshot_observed'] = observed
        if observed['delivery'] != before:
            raise Refused('experiment_snapshot_delivery_mismatch')
        # This explicit experiment passes only verified bytes to the existing
        # single-file API. Producer metadata is not silently promoted to trusted
        # hardware provenance or persisted as an application capture identity.
        sys.argv = [str(Path(smoke.__file__)), str(out / 'service'), '--assets', str(args.assets.resolve()),
                    '--input', str(snapshot / 'audio.wav'), '--input-sha256', before['wav_sha256']]
        code = smoke.main()
        result['service_exit_code'] = code
        service = json.loads((out / 'service/RESULT.json').read_text())
        result['service_result'] = service
        if code != 0 or not service['passed'] or service['shutdown']['exit_code'] != 0:
            raise Refused('experiment_service_not_cleanly_completed')
        result['durable_mapping'] = durable_mapping(out / 'service/data/jobs.sqlite3', before, service['session_id'])
        result['original_unchanged'] = validate_delivery(args.delivery) == before
        result['snapshot_unchanged'] = inspect_snapshot(snapshot) == observed
        result['tested_mapping'] = dict(producer_source_id=before['source_id'],
            producer_capture_sha256=before['capture_identity_sha256'],
            wav_sha256=before['wav_sha256'], frames=before['frames'],
            session_id=service.get('session_id'), uploader_capture_id='synthetic-smoke',
            metadata_imported_to_backend=False, durable_recorder_binding_established=False)
        result['passed'] = (code == 0 and service['passed'] and result['original_unchanged']
                            and result['snapshot_unchanged'] and service['input_frames'] == before['frames'])
    except Exception as exc:
        result['error'] = dict(type=type(exc).__name__, message=str(exc)[:2048])
    finally:
        sys.argv = original_argv
        smoke.save(out / 'RESULT.json', result)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
