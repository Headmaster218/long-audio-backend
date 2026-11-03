"""Bounded uploader smoke through unchanged real strict native service.

Uses existing capacity resource/startup/cleanup guards. Never substitutes a model,
changes strict gates, or retries session creation. Local output contains generated
WAV/state and must not be committed. This is not recorder provenance or accuracy.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capacity_run import Gate, REPO, configuration, digest_file, save, source_pins
sys.path.insert(0, str(REPO))
from audio_backend.recording_uploader import Uploader, Limits


def validate_input(path, expected_sha):
    """Read-only bounded canonical sample check, before starting any service."""
    if not expected_sha or len(expected_sha) != 64 or any(c not in '0123456789abcdef' for c in expected_sha):
        raise ValueError('input_sha256_required')
    if not path.is_file() or path.stat().st_size > 16000 * 30 * 2 + 4096:
        raise ValueError('input_file_bound')
    if digest_file(path) != expected_sha:
        raise ValueError('input_sha256_mismatch')
    with wave.open(str(path), 'rb') as audio:
        frames = audio.getnframes()
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, 'NONE'):
            raise ValueError('input_format')
        if not 0 < frames <= 30 * 16000 or len(audio.readframes(frames + 1)) != frames * 2:
            raise ValueError('input_frame_bound')
    return frames


def smoke_configuration(workspace, data, language=None):
    """Explicit experiment language; inherited capacity defaults stay unchanged."""
    if language is not None and language not in {'auto', 'zh', 'en', 'ja', 'ko', 'yue'}:
        raise ValueError('unsupported_smoke_language')
    cfg = configuration(workspace, data)
    if language is not None:
        cfg['language'] = language
    return cfg



def verify_span_resume_mutations(initial, resumed, seal_path, seal_body):
    """Completed resume may replay only the exact already-frozen EOS seal."""
    expected = dict(method='POST', path=seal_path, key=None, body=seal_body)
    if expected not in initial or any(row != expected for row in resumed):
        raise RuntimeError('span_resume_new_or_changed_mutation')
    return dict(no_new_sources=True, exact_frozen_seal_replays=len(resumed))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--input-sha256')
    parser.add_argument('--language', choices=('auto', 'zh', 'en', 'ja', 'ko', 'yue'),
                        help='Explicit decode language; omission preserves the capacity profile default')
    parser.add_argument('--recorder-span-delivery', type=Path, action='append', help='Repeat for one selected closed span totaling <=30s; EOS is span-local only')
    parser.add_argument('--recorder-incremental-span-delivery', type=Path, action='append', help='Repeat 2..32 inputs totaling <10.5s; fresh append processes, native completion, then GET-only v3 status')
    parser.add_argument('--recorder-incremental-delivery', type=Path, action='append', help='Repeat to test separate append calls then explicit finalize')
    parser.add_argument('--recorder-batch-delivery', type=Path, action='append', help='Repeat for a fixed complete batch with total duration <=30s')
    parser.add_argument('--recorder-delivery', type=Path, help='Exercise explicit known-session intake with a complete <=30s delivery')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(mode=0o700, exist_ok=False)
    (out / 'data').mkdir(mode=0o700)
    gate = Gate(out, 1, 300)
    result = {'schema': 'strict-uploader-synthetic-smoke-v1', 'passed': False,
              'accuracy_qualified': False, 'recorder_provenance_qualified': False}
    gate.journal = (out / 'http.jsonl').open('x')
    pins = source_pins()
    save(out / 'SOURCE_PINS.json', pins)
    try:
        frames = 176000
        batch_reports = None
        incremental_span_mode = args.recorder_incremental_span_delivery is not None
        if incremental_span_mode:
            if any(x is not None for x in (args.input, args.input_sha256, args.recorder_delivery,
                    args.recorder_span_delivery, args.recorder_batch_delivery, args.recorder_incremental_delivery)):
                raise ValueError('recorder_incremental_span_conflicts_with_other_input')
            from strict_incremental_span_probe import MAX_INPUTS, inventory
            if not 2 <= len(args.recorder_incremental_span_delivery) <= MAX_INPUTS:
                raise ValueError('recorder_incremental_span_input_count')
            args.recorder_batch_delivery = args.recorder_incremental_span_delivery
            original_span_inventory = inventory(args.recorder_batch_delivery)
        span_mode = args.recorder_span_delivery is not None
        if span_mode:
            if any(x is not None for x in (args.input, args.input_sha256, args.recorder_delivery,
                    args.recorder_batch_delivery, args.recorder_incremental_delivery)):
                raise ValueError('recorder_span_conflicts_with_other_input')
            args.recorder_batch_delivery = args.recorder_span_delivery
        incremental_mode = args.recorder_incremental_delivery is not None
        if incremental_mode:
            if args.recorder_batch_delivery is not None:
                raise ValueError('recorder_incremental_conflicts_with_batch')
            args.recorder_batch_delivery = args.recorder_incremental_delivery
        if args.recorder_batch_delivery is not None:
            from audio_backend.recorder_delivery import inspect_delivery
            if args.input is not None or args.input_sha256 is not None or args.recorder_delivery is not None:
                raise ValueError('recorder_batch_conflicts_with_other_input')
            if not incremental_span_mode and not (1 if span_mode else 2) <= len(args.recorder_batch_delivery) <= 8:
                raise ValueError('recorder_batch_input_count')
            batch_reports = [inspect_delivery(p, validate_metadata=True) for p in args.recorder_batch_delivery]
            frames = sum(validate_input(p / 'audio.wav', r['wav_sha256'])
                         for p, r in zip(args.recorder_batch_delivery, batch_reports))
            if frames > 30 * 16000:
                raise ValueError('recorder_batch_duration_bound')
            # This probe intentionally stages one partial page, then finalizes.
            if incremental_span_mode and frames >= 168000:
                raise ValueError('recorder_incremental_span_short_probe_bound')
            result['input_kind'] = ('explicit_recorder_incremental_span' if incremental_span_mode else 'explicit_recorder_span' if span_mode else
                'explicit_recorder_incremental' if incremental_mode else 'explicit_recorder_batch')
        if args.recorder_delivery is not None:
            from audio_backend.recorder_intake import validate_delivery
            if args.input is not None or args.input_sha256 is not None:
                raise ValueError('recorder_delivery_conflicts_with_input')
            delivery_report = validate_delivery(args.recorder_delivery)
            args.input = args.recorder_delivery / 'audio.wav'
            args.input_sha256 = delivery_report['wav_sha256']
        if args.input is not None:
            frames = validate_input(args.input, args.input_sha256)
            result['input_kind'] = 'explicit_recorder_delivery' if args.recorder_delivery is not None else 'explicit_public_sample'
            result['input_sha256'] = args.input_sha256
        elif args.input_sha256 is not None:
            raise ValueError('input_path_required')
        # Pin existing model/runtime assets, excluding the previously exposed speech sample.
        assets = json.loads(args.assets.read_text())
        checked = []
        for asset in assets['files']:
            if asset.get('role') == 'sample':
                continue
            path = Path(asset['path'])
            if path.stat().st_size != asset['bytes'] or digest_file(path) != asset['sha256']:
                raise RuntimeError('asset_hash:' + str(path))
            checked.append(asset)
        save(out / 'ASSET_VERIFIED.json', checked)
        gate.check()
        # Read-only diagnostic refuses known binary mismatches before a service wait.
        # This is not requalification and cannot authorize an unshipped profile.
        from audio_backend.native_owned_cell_store import qualified_runtime, runtime_fingerprints, Budget
        qualification = qualified_runtime(gate.data)
        if qualification is None:
            raise RuntimeError('no_shipped_runtime_qualification')
        observed = runtime_fingerprints(Budget())
        differences = {key: {'expected': getattr(qualification, key), 'observed': value}
                       for key, value in observed.items() if getattr(qualification, key) != value}
        result['runtime_binary_differences'] = differences
        if differences:
            raise RuntimeError('runtime_binary_qualification_mismatch')
        available = sorted(os.sched_getaffinity(0))
        if len(available) < 2:
            raise RuntimeError('two_cpu_unavailable')
        os.sched_setaffinity(0, set(available[:2]))
        cfg = smoke_configuration(REPO.parent, gate.data, args.language)
        result['asr_language'] = cfg['language']
        result['asr_language_explicit'] = args.language is not None
        save(out / 'config.json', cfg)
        lock = gate.data / 'instance.lock'
        gate.hold = os.open(lock, os.O_RDONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        fcntl.flock(gate.hold, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: setattr(gate, 'stop_requested', True))
        with (out / 'service.stdout').open('xb') as stdout, (out / 'service.stderr').open('xb') as stderr:
            gate.process = subprocess.Popen([sys.executable, '-I', '-B', str(REPO / 'tools/capacity_service.py'),
                str(out / 'config.json'), str(gate.hold), str(out)], pass_fds=(gate.hold,),
                stdout=stdout, stderr=stderr, cwd=REPO, start_new_session=True)
        while not (out / 'ready.json').exists():
            gate.check()
            time.sleep(.1)
        ready = json.loads((out / 'ready.json').read_text())
        if not all(ready[k] is True for k in ('strict', 'inherited_lock', 'worker_alive')):
            raise RuntimeError('startup_not_ready')
        gate.port = ready['port']
        gate.set_phase('processing')
        result['startup'] = ready
        # One single-shot POST. No session creation retry even on lost response.
        sid = gate.request(('POST', '/sessions', 201, {'name': 'Synthetic strict uploader smoke'}, {}))['id']
        result['session_id'] = sid
        original = out / ('sample.wav' if args.input is not None else 'synthetic.wav')
        if batch_reports is not None:
            with wave.open(str(original), 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
                for path in args.recorder_batch_delivery:
                    with wave.open(str(path / 'audio.wav'), 'rb') as source:
                        while block := source.readframes(32768):
                            output.writeframesraw(block)
        elif args.input is not None:
            shutil.copyfile(args.input, original)
            if validate_input(original, args.input_sha256) != frames:
                raise RuntimeError('copied_input_frames')
        else:
            with wave.open(str(original), 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                for _ in range(11):
                    audio.writeframesraw(b'\0\0' * 16000)
        result['input_frames'] = frames
        before = digest_file(original)
        def cancelled():
            gate.check()
            return False
        if incremental_span_mode:
            from strict_incremental_span_probe import child_phase, verify_completed_repeat
            spec_path = out / 'incremental-spec.json'
            spec = dict(deliveries=[str(p.resolve()) for p in args.recorder_batch_delivery],
                frames=[r['frames'] for r in batch_reports], split=len(batch_reports) // 2,
                job_dir=str(out / 'intake'), api='http://127.0.0.1:' + str(gate.port),
                session_id=sid, capture_id='synthetic-smoke')
            save(spec_path, spec)
            first = child_phase(gate, spec_path, 'first')
            second = child_phase(gate, spec_path, 'second')
            final = child_phase(gate, spec_path, 'finalize')
            repeated = child_phase(gate, spec_path, 'repeat')
            result['incremental_stages'] = first['stages'] + second['stages']
            result['incremental_clean_process_restart'] = dict(first_pid=first['pid'], second_pid=second['pid'],
                midpoint_replay=second['midpoint_replay'], abrupt_crash_test=False, service_restart_test=False)
            result['upload'] = final['result']; result['upload_reconciled_resume'] = repeated['result']
            result['injected_lost_published_seal_responses'] = final['injected_lost_published_seal_responses']
            result['incremental_repeat_check'] = verify_completed_repeat(final['requests'], repeated['requests'])
            if result['upload'] != result['upload_reconciled_resume']:
                raise RuntimeError('incremental_span_repeat_result_changed')
        elif batch_reports is not None:
            from audio_backend.recorder_batch_intake import run as intake_run
            intake_args = dict(deliveries=args.recorder_batch_delivery, job_dir=out / 'intake',
                session_id=sid, capture_id='synthetic-smoke', exclusive_writer=True,
                api='http://127.0.0.1:' + str(gate.port), cancel=cancelled,
                limits=Limits(deadline_seconds=120, poll_seconds=.1))
            # Actual service commit/publication, then discard exactly one
            # successful EOS response to exercise saved seal reconciliation.
            from audio_backend.recording_uploader import HTTPTransport, Guard, Uncertain
            class LosePublishedSeal(HTTPTransport):
                lost = 0
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.mutations = []
                def request(self, method, path, **kwargs):
                    if method != 'GET':
                        self.mutations.append({'method': method, 'path': path, 'key': kwargs.get('key'),
                            'body': json.loads(json.dumps(kwargs.get('body'), sort_keys=True))})
                    reply = super().request(method, path, **kwargs)
                    if method == 'POST' and path.endswith('/seal') and reply.get('released') and not self.lost:
                        self.lost += 1
                        raise Uncertain('Injected response loss after actual published seal')
                    return reply
            transport = LosePublishedSeal(intake_args['api'], Guard(intake_args['limits'], cancelled))
            intake_args['transport'] = transport
            if span_mode:
                from audio_backend.recorder_span_intake import run as span_run
                intake_args['acknowledge_span_local_closure'] = True
                result['upload'] = span_run(**intake_args)
                mutations = list(transport.mutations)
                result['upload_reconciled_resume'] = span_run(**intake_args, resume=True)
                result['upload_second_resume'] = span_run(**intake_args, resume=True)
                result['span_mutations'] = mutations
                result['span_resume_mutations'] = transport.mutations[len(mutations):]
                frozen = json.loads((out / 'intake/uploader/uploader.json').read_text())['pages'][0]['seal']
                result['span_resume_check'] = verify_span_resume_mutations(mutations,
                    result['span_resume_mutations'],
                    '/sessions/' + sid + '/recordings/synthetic-smoke/pages/1/seal', frozen)
                if result['upload_second_resume'] != result['upload']:
                    raise RuntimeError('span_repeat_resume_changed_result')
                description = result['upload']['span']
                if description['eos_meaning'] != 'selected_span_only' or description['capture_completeness_proven']:
                    raise RuntimeError('span_closure_scope_changed')
            elif incremental_mode:
                from audio_backend.recorder_capture_intake import run as capture_run
                incremental_args = {k:v for k,v in intake_args.items() if k != 'deliveries'}
                stages=[]
                for index, delivery in enumerate(args.recorder_batch_delivery):
                    stage=capture_run(**incremental_args, delivery=delivery, start=index==0)
                    if stage['capture_finalized'] or stage['published_frames'] or stage['status'] != 'delivery_staged_without_eos':
                        raise RuntimeError('short_incremental_stage_premature_eos_or_publication')
                    stages.append(stage)
                result['incremental_stages']=stages
                repeated=capture_run(**incremental_args,delivery=args.recorder_batch_delivery[-1])
                if repeated != stages[-1]:
                    raise RuntimeError('incremental_latest_retry_changed_result')
                result['upload']=capture_run(**incremental_args,finalize=True)
                result['upload_reconciled_resume']=capture_run(**incremental_args,finalize=True)
            else:
                result['upload'] = intake_run(**intake_args)
                result['upload_reconciled_resume'] = intake_run(**intake_args, resume=True)
            result['injected_lost_published_seal_responses'] = transport.lost
            if result['upload'] != result['upload_reconciled_resume'] or transport.lost != 1:
                raise RuntimeError('batch_intake_resume_or_loss_test_failed')
        elif args.recorder_delivery is not None:
            from audio_backend.recorder_intake import run as intake_run
            intake_args = dict(delivery=args.recorder_delivery, job_dir=out / 'intake',
                session_id=sid, capture_id='synthetic-smoke', exclusive_writer=True,
                api='http://127.0.0.1:' + str(gate.port), cancel=cancelled,
                limits=Limits(deadline_seconds=120, poll_seconds=.1))
            result['upload'] = intake_run(**intake_args)
            result['upload_reconciled_resume'] = intake_run(**intake_args, resume=True)
            if result['upload'] != result['upload_reconciled_resume']:
                raise RuntimeError('intake_resume_changed_result')
        else:
            uploader = Uploader(input=original, session_id=sid, capture_id='synthetic-smoke',
                capture_origin_frame=0, state_dir=out / 'uploader', final=True,
                api='http://127.0.0.1:' + str(gate.port), cancel=cancelled,
                limits=Limits(deadline_seconds=240, poll_seconds=.1))
            result['upload'] = uploader.run()
        base = '/sessions/' + sid
        while True:
            rows = gate.request(('GET', base + '/chunks?limit=20', 200, None, {}))['chunks']
            if any(row['status'] in ('failed', 'paused', 'cancelled') for row in rows):
                raise RuntimeError('synthetic_chunk_failed:' + str([(r['id'], r['status']) for r in rows]))
            if rows and all(row['status'] == 'completed' for row in rows):
                expected = [min(168000, frames - start) / 16000 for start in range(0, frames, 168000)]
                if len(rows) != len(expected) or [r['duration'] for r in rows] != expected:
                    raise RuntimeError('synthetic_chunk_geometry')
                result['chunks'] = [{'id': r['id'], 'status': r['status'], 'duration': r['duration']} for r in rows]
                break
            time.sleep(.2)
        page = gate.request(('GET', base + '/recordings/synthetic-smoke/pages/1', 200, None, {}))
        result['page'] = {'state': page['state'], 'released': page['released'], 'eos': page['eos']}
        if span_mode or incremental_span_mode:
            result['page']['source_count'] = len(page['sources'])
            if len(page['sources']) != len(batch_reports):
                raise RuntimeError('span_source_count_changed_after_resume')
        if page['state'] != 'published' or not page['released'] or not page['eos']:
            raise RuntimeError('page_not_published_eos')
        if incremental_span_mode:
            from strict_incremental_span_probe import child_phase, verify_status, inventory
            observed = child_phase(gate, spec_path, 'status')
            repeated = child_phase(gate, spec_path, 'status-repeat')
            verify_status(observed['result'], batch_reports)
            if observed['result'] != repeated['result']:
                raise RuntimeError('incremental_span_status_repeat_changed')
            result['status_observation'] = observed['result']
            result['status_repeat_identical'] = True
            result['status_get_only'] = all(row['method'] == 'GET'
                for phase in (observed, repeated) for row in phase['requests'])
            result['original_span_inventory_unchanged'] = inventory(args.recorder_batch_delivery) == original_span_inventory
            if not result['status_get_only'] or not result['original_span_inventory_unchanged']:
                raise RuntimeError('incremental_span_status_or_originals_changed')
        result['input_unchanged'] = digest_file(original) == before
        if not result['input_unchanged']:
            raise RuntimeError('input_changed')
    except BaseException as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)[:2048]}
    finally:
        gate.set_phase('stopping')
        try:
            result['shutdown'] = gate.stop()
        except BaseException as exc:
            result['shutdown_error'] = {'type': type(exc).__name__, 'message': str(exc)[:2048]}
            try:
                result['forced_cleanup'] = gate.force_cleanup('smoke_shutdown_failed')
            except BaseException as cleanup:
                result['forced_cleanup_error'] = str(cleanup)[:2048]
        if gate.hold is not None and 'forced_cleanup_error' not in result:
            os.close(gate.hold)
        gate.journal.close()
        result['source_unchanged'] = source_pins() == pins
        result['peak_sampled_rss'] = gate.peak_rss
        result['passed'] = (not any(k.endswith('error') for k in result) and result['source_unchanged']
                            and result.get('input_unchanged') is True)
        if args.input is not None:
            try:
                result['external_input_unchanged'] = digest_file(args.input) == args.input_sha256
            except OSError:
                result['external_input_unchanged'] = False
            result['passed'] = result['passed'] and result['external_input_unchanged']
        if args.recorder_batch_delivery is not None:
            try:
                from audio_backend.recorder_delivery import inspect_delivery
                result['external_batch_unchanged'] = batch_reports is not None and [inspect_delivery(p, validate_metadata=True)
                    for p in args.recorder_batch_delivery] == batch_reports
            except Exception:
                result['external_batch_unchanged'] = False
            result['passed'] = result['passed'] and result['external_batch_unchanged']
        save(out / 'RESULT.json', result)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
