"""Bounded local inbox/public-speech qualification over actual loopback HTTP.

This opt-in harness is not a receiver or deployment. It owns one explicit empty
session and controlled local delivery arrivals. Unchanged capacity guards own
native lifetime. Never launch until the exact candidate/plan has been reviewed.
Raw audio, transcript, local state and databases belong only in a private output
outside any repository. Adversarial variants are test copies, never producer data.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT)]
from capacity_run import Gate, REPO, digest_file, save, source_pins
from strict_uploader_smoke import smoke_configuration
from strict_incremental_span_probe import Trace, inventory, need, verify_status
from audio_backend import recording_uploader as u
from audio_backend.recorder_delivery import inspect_delivery, parse_marker, parse_envelope

FIXTURE_SHA256 = '735895855f8de14f84992216ee7b28fb7359ce74fb30a86aed6ed5e93e80dd56'
FILES = ('audio.wav', 'segment.v1.bin', 'delivery.v1.txt')


def private_directory(path):
    Path(path).mkdir(mode=0o700, exist_ok=False)


def private_write(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def arrive(source, inbox, *, partial=False):
    """Copy pinned original bytes; final marker is the last arrival."""
    report = inspect_delivery(source, validate_metadata=True)
    target = Path(inbox) / report['attempt']
    private_directory(target)
    for name in (FILES[:2] if partial else FILES):
        private_write(target / name, (Path(source) / name).read_bytes())
    return target


def complete_arrival(source, target):
    private_write(Path(target) / 'delivery.v1.txt', (Path(source) / 'delivery.v1.txt').read_bytes())
    return inspect_delivery(target, validate_metadata=True)


def variant(source, inbox, *, conflict=False, suffix='f' * 32):
    """Make a disclosed adversarial copy, never modify/admit original evidence.

    A duplicate changes only transport-attempt nonce. A content conflict flips
    one PCM bit and updates the checksum declarations in that copy. Both decode
    as valid deliveries of the same producer ordinal; neither is a genuine new
    Java producer output and the conflict must never be admitted.
    """
    source = Path(source); marker = parse_marker((source / 'delivery.v1.txt').read_bytes())
    envelope = (source / 'segment.v1.bin').read_bytes()
    local = parse_envelope(envelope, marker, return_local=True)
    wav = (source / 'audio.wav').read_bytes()
    if conflict:
        changed = bytearray(wav); changed[44] ^= 1; changed = bytes(changed)
        from audio_backend.recorder_metadata import decode
        metadata = decode(local)
        new_wav = hashlib.sha256(changed).hexdigest()
        new_pcm = hashlib.sha256(changed[44:]).hexdigest()
        body = local[:-32]
        for old, new in ((metadata['wav_sha256'], new_wav), (metadata['pcm_sha256'], new_pcm)):
            need(body.count(old.encode()) == 1, 'variant_hash_occurrence')
            body = body.replace(old.encode(), new.encode())
        local = body + hashlib.sha256(body).digest()
        envelope = envelope[:-len(local)] + local
        wav = changed
    wav_hash = hashlib.sha256(wav).hexdigest()
    binding = hashlib.sha256((marker['source_id'] + '\n' + wav_hash + '\n' + marker['revision']).encode()).hexdigest()
    attempt = 'r-' + binding + '-' + suffix
    body = '\n'.join(('RECORDER-DELIVERY-V1', attempt, marker['revision'], marker['source_id'],
        str(len(wav)), wav_hash, str(len(envelope)), hashlib.sha256(envelope).hexdigest(), '')).encode()
    target = Path(inbox) / attempt; private_directory(target)
    for name, raw in zip(FILES, (wav, envelope, body)):
        private_write(target / name, raw)
    report = inspect_delivery(target, validate_metadata=True)
    need(report['sequence'] == inspect_delivery(source, validate_metadata=True)['sequence'], 'variant_ordinal')
    return target


def verify_fixture(path):
    path = Path(path)
    need(path.stat().st_size <= 128 * 1024 and digest_file(path) == FIXTURE_SHA256, 'fixture_pin')
    value = json.loads(path.read_bytes()); entries = value['deliveries']
    need(value['passed'] is True and len(entries) == 9 and value['total_selected_frames'] == 144000,
         'fixture_geometry')
    reports = []
    for index, entry in enumerate(entries):
        source = Path(entry['path']); report = inspect_delivery(source, validate_metadata=True)
        need(report == entry['delivery'], 'fixture_delivery_changed')
        need(all(digest_file(source / name) == entry['hashes'][name] for name in FILES), 'fixture_bytes_changed')
        need(report['sequence'] == index + 2 and report['epoch'] == 7 and report['frames'] == 16000
             and report['frame_start'] == 32000 + index * 16000
             and report['close_reason'] == ('INTERRUPTION' if index == 8 else 'ROTATION'), 'fixture_declarations')
        reports.append(report)
    return value, reports


def run_child(spec, output):
    """One fresh runner/status/export process, real HTTP with a bounded trace."""
    from audio_backend import recorder_inbox_runner as runner
    from audio_backend import recorder_incremental_span_status as status
    from audio_backend import recorder_incremental_span_export as export
    limits = u.Limits(deadline_seconds=120, poll_seconds=.1)
    transport = Trace(spec['api'], u.Guard(limits), get_only=spec['operation'] in ('status', 'export-repeat'))
    report = dict(schema='local-inbox-loopback-child-v1', phase=spec['phase'], pid=os.getpid(),
                  requests=transport.calls, returned=False)
    try:
        if spec['operation'] == 'runner':
            report['result'] = runner.run(state_dir=Path(spec['state_dir']), exclusive_writer=True,
                acknowledge_span_local_closure=True, limits=limits, transport=transport,
                deadline_seconds=120, **spec.get('runner_args', {}))
        else:
            kwargs = dict(job_dir=Path(spec['job_dir']), api=spec['api'], session_id=spec['session_id'],
                capture_id=spec['capture_id'], exclusive_writer=True, limits=limits, transport=transport)
            before = inventory([Path(spec['job_dir']), *map(Path, spec['deliveries'])])
            report['result'] = (status.run(**kwargs) if spec['operation'] == 'status' else
                export.run(**kwargs, output_dir=Path(spec['repeat_output_dir'])))
            report['local_files_unchanged'] = inventory([Path(spec['job_dir']), *map(Path, spec['deliveries'])]) == before
            need(report['local_files_unchanged'], 'read_only_phase_changed_inputs')
        report['returned'] = True
    except BaseException as error:
        report['error'] = dict(type=type(error).__name__, message=str(error)[:2048])
    finally:
        save(Path(output), report)
    # A deliberate refused runner operation is an observed result, not a lost child.
    return 0


def child_phase(gate, base, phase, *, operation='runner', **runner_args):
    spec = dict(base, phase=phase, operation=operation, runner_args=runner_args)
    spec_path = gate.out / (phase + '-spec.json'); output = gate.out / (phase + '.json')
    save(spec_path, spec)
    command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), 'child', str(spec_path), str(output)]
    with (gate.out / (phase + '.stdout')).open('xb') as stdout, (gate.out / (phase + '.stderr')).open('xb') as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, cwd=ROOT)
        code = None
        try:
            while process.poll() is None:
                gate.check(); time.sleep(.05)
            code = process.wait(timeout=1)
        finally:
            if process.poll() is None:
                process.terminate()
                try: code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); code = process.wait(timeout=5)
            elif code is None:
                code = process.wait(timeout=1)
            save(gate.out / (phase + '-wait.json'), dict(pid=process.pid,
                original_popen_wait_completed=True, exit_code=code))
    need(code == 0, 'child_failed:' + phase)
    report = json.loads(output.read_bytes())
    need(not any(row['method'] == 'POST' and row['path'] == '/sessions' for row in report['requests']),
         'runner_created_session')
    return report


def assert_reads_only(phase):
    need(all(row['method'] == 'GET' for row in phase['requests']), 'unexpected_mutation:' + phase['phase'])


def assert_returned(phase):
    need(phase['returned'] is True and 'error' not in phase, 'runner_failed:' + phase['phase'])
    return phase['result']


def assert_refused(phase):
    need(phase['returned'] is False and 'error' in phase, 'expected_refusal:' + phase['phase'])
    assert_reads_only(phase)


def check_exports(output, reports):
    paths = [Path(output) / directory for directory in ('export', 'export-repeat')]
    for name in ('export.json', 'transcript.txt', 'complete.json'):
        files = [path / name for path in paths]
        need(all(p.is_file() and not p.is_symlink() and p.stat().st_mode & 0o077 == 0 for p in files),
             'private_export_required')
        need(files[0].read_bytes() == files[1].read_bytes(), 'export_repeat_changed')
    manifest = json.loads((paths[0] / 'complete.json').read_bytes())
    need(manifest.get('status') == 'exported' and manifest.get('schema') == 'recorder-incremental-span-export-v1'
         and set(manifest.get('artifacts', {})) == {'export.json', 'transcript.txt'}, 'export_manifest_shape')
    for name, expected in manifest['artifacts'].items():
        need(expected == dict(bytes=(paths[0] / name).stat().st_size, sha256=digest_file(paths[0] / name)),
             'export_manifest_hash_or_length')
    document = json.loads((paths[0] / 'export.json').read_bytes())
    verify_status(document['completion'], reports)
    nonempty = sum(bool(unit['text'].strip()) for unit in document['units'])
    need(nonempty > 0 and manifest['nonempty_text_units'] == nonempty
         and manifest['units'] == len(document['units']) and manifest['frames'] == 144000
         and manifest['complete_content'] is False, 'no_real_primary_speech_text')
    return dict(repeat_identical=True, nonempty_text_units=nonempty, units=len(document['units']),
        artifact_bytes={name: (paths[0] / name).stat().st_size for name in ('export.json', 'transcript.txt', 'complete.json')},
        accuracy_verified=False, speaker_identity_verified=False, complete_capture_proven=False)


def start_service(gate, assets):
    checked = []
    for item in json.loads(Path(assets).read_bytes())['files']:
        if item.get('role') == 'sample':
            continue
        path = Path(item['path'])
        need(path.stat().st_size == item['bytes'] and digest_file(path) == item['sha256'], 'asset_pin')
        checked.append(item)
    save(gate.out / 'ASSET_VERIFIED.json', checked); gate.check()
    from audio_backend.native_owned_cell_store import qualified_runtime, runtime_fingerprints, Budget
    qualification = qualified_runtime(gate.data)
    need(qualification is not None, 'no_shipped_runtime_qualification')
    observed = runtime_fingerprints(Budget())
    need(not {key: value for key, value in observed.items() if getattr(qualification, key) != value},
         'runtime_binary_qualification_mismatch')
    available = sorted(os.sched_getaffinity(0)); need(len(available) >= 2, 'two_cpu_unavailable')
    os.sched_setaffinity(0, set(available[:2]))
    save(gate.out / 'config.json', smoke_configuration(REPO.parent, gate.data))
    gate.hold = os.open(gate.data / 'instance.lock', os.O_RDONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    fcntl.flock(gate.hold, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: setattr(gate, 'stop_requested', True))
    with (gate.out / 'service.stdout').open('xb') as stdout, (gate.out / 'service.stderr').open('xb') as stderr:
        gate.process = subprocess.Popen([sys.executable, '-I', '-B', str(REPO / 'tools/capacity_service.py'),
            str(gate.out / 'config.json'), str(gate.hold), str(gate.out)], pass_fds=(gate.hold,),
            stdout=stdout, stderr=stderr, cwd=REPO, start_new_session=True)
    while not (gate.out / 'ready.json').exists():
        gate.check(); time.sleep(.1)
    ready = json.loads((gate.out / 'ready.json').read_bytes())
    need(all(ready[k] is True for k in ('strict', 'inherited_lock', 'worker_alive')), 'startup_not_ready')
    gate.port = ready['port']; gate.set_phase('processing')
    return ready


def exercise(gate, fixture, reports):
    from audio_backend import recorder_inbox_runner as runner
    from audio_backend import recorder_incremental_span_intake as intake
    paths = [Path(row['path']) for row in fixture['deliveries']]
    for name in ('inbox', 'retained-conflict'):
        private_directory(gate.out / name)
    # Exactly one harness-created session, never a product-runner side effect.
    sid = gate.request(('POST', '/sessions', 201, {'name': 'Bounded public speech inbox loopback'}, {}))['id']
    base = dict(api='http://127.0.0.1:' + str(gate.port), session_id=sid, capture_id='inbox-loopback',
        state_dir=str(gate.out / 'runner'), job_dir=str(gate.out / 'intake'),
        repeat_output_dir=str(gate.out / 'export-repeat'), deliveries=[str(p) for p in paths])
    first = fixture['deliveries'][0]
    origin = intake.POLICY.header_fields(dict(delivery=first['delivery'], metadata=first['original_metadata']))['span_origin']
    config = dict(schema=runner.CONFIG_SCHEMA, inbox_dir=str(gate.out / 'inbox'), job_dir=base['job_dir'],
        output_dir=str(gate.out / 'export'), api=base['api'], session_id=sid, capture_id=base['capture_id'],
        span_origin=origin, max_hours=1, max_inputs=9)
    configured = runner.configure(state_dir=Path(base['state_dir']), config=config)
    need(configured['network_requests'] == 0 and configured['intake_started'] is False, 'configure_mutated_remote')
    save(gate.out / 'runner-spec.json', base)
    phases = []; completed = 0
    def invoke(name, expected, *, read_only=True, **kwargs):
        value = child_phase(gate, base, name, **kwargs)
        outcome = assert_returned(value)
        need(outcome['inputs'] == expected, 'runner_admitted_count:' + name)
        need(len(list(Path(base['state_dir']).glob('input-*.done.json'))) == expected,
             'runner_admission_receipt_count:' + name)
        if read_only: assert_reads_only(value)
        phases.append(dict(phase=name, pid=value['pid'], inputs=expected, status=outcome['status'],
            requests=len(value['requests']), writes=sum(row['method'] != 'GET' for row in value['requests'])))
        return outcome
    def refuse(name, reason, expected, **kwargs):
        before = inventory([Path(base['state_dir']), Path(base['job_dir'])])
        value = child_phase(gate, base, name, **kwargs); assert_refused(value)
        need(value['error']['message'] == reason, 'unexpected_refusal_reason:' + name)
        need(inventory([Path(base['state_dir']), Path(base['job_dir'])]) == before,
             'refusal_changed_durable_state:' + name)
        need(len(list(Path(base['state_dir']).glob('input-*.done.json'))) == expected,
             'refusal_changed_admitted_count:' + name)
        phases.append(dict(phase=name, pid=value['pid'], inputs=expected, refusal=reason,
            requests=len(value['requests']), writes=0))
    outcome = invoke('empty-inbox', 0)
    need(outcome['status'] == 'waiting_for_input', 'empty_inbox_status')
    first_target = arrive(paths[0], gate.out / 'inbox', partial=True)
    refuse('partial-arrival', 'runner_quarantined_unresolved_delivery', 0)
    complete_arrival(paths[0], first_target)
    invoke('first-arrival', 1)
    for path in paths[1:4]: arrive(path, gate.out / 'inbox')
    invoke('first-four', 4, max_actions=3)
    # These are fresh processes. The native worker is deliberately not restarted.
    invoke('restart-same-path-replay', 4)
    duplicate = variant(paths[3], gate.out / 'inbox', suffix='e' * 32)
    invoke('frozen-exact-content-duplicate', 4)
    fifth = arrive(paths[4], gate.out / 'inbox')
    conflict = variant(paths[4], gate.out / 'inbox', conflict=True)
    conflict_before = {name: digest_file(conflict / name) for name in FILES}
    refuse('content-conflict', 'runner_conflicting_ordinal_content', 4)
    refuse('conflict-selection-cannot-override', 'runner_conflicting_ordinal_content', 4,
           select_attempt=fifth.name)
    # Explicit qualification-operator action, not a runner recovery path. Preserve
    # all adversarial bytes outside inbox and record the decision and hashes.
    retained = gate.out / 'retained-conflict' / conflict.name
    conflict.rename(retained)
    need({name: digest_file(retained / name) for name in FILES} == conflict_before, 'conflict_copy_lost')
    save(gate.out / 'OPERATOR_CONFLICT_ISOLATION.json', dict(
        actor='explicit-qualification-harness-operator', runner_performed=False,
        reason='retain adversarial differing-content attempt separately; original selected input remains',
        original_path=str(conflict), retained_path=str(retained), hashes=conflict_before,
        deleted=False, rewritten=False, source_fixture_unchanged=True))
    invoke('after-operator-isolation', 5)
    sixth = arrive(paths[5], gate.out / 'inbox')
    second_attempt = variant(paths[5], gate.out / 'inbox', suffix='d' * 32)
    refuse('new-duplicate-needs-selection', 'runner_explicit_duplicate_selection_required', 5)
    invoke('explicit-duplicate-selection', 6, select_attempt=sixth.name)
    for path in paths[6:]: arrive(path, gate.out / 'inbox')
    invoke('terminal-arrivals', 9, max_actions=3)
    outcome = invoke('terminal-awaits-explicit-finalize', 9)
    need(outcome['status'] == 'awaiting_finalize', 'terminal_auto_finalized')
    invoke('explicit-finalize', 9, read_only=False, finalize=True)
    # Poll through the runner's status composition. Each observation is a new
    # process and shares the unchanged overall resource/deadline Gate.
    index = 0
    while True:
        outcome = invoke('analysis-status-%03d' % index, 9)
        if outcome.get('primary_completion_observed') is True:
            need(outcome['status'] == 'completed', 'status_completion_mismatch'); break
        need(outcome['status'] in ('processing', 'pending', 'in_progress'), 'unexpected_analysis_status')
        index += 1; gate.check(); time.sleep(.1)
    exported = invoke('runner-export', 9, export=True)
    need(exported['status'] == 'exported' and exported['export_completed'] is True, 'runner_export_not_complete')
    repeated = invoke('runner-export-restart', 9, export=True)
    need(repeated['status'] == 'exported' and repeated['export_completed'] is True, 'runner_export_restart_changed')
    observed = child_phase(gate, base, 'independent-status', operation='status')
    verify_status(assert_returned(observed), reports); assert_reads_only(observed)
    repeated_export = child_phase(gate, base, 'independent-export-repeat', operation='export-repeat')
    assert_returned(repeated_export); assert_reads_only(repeated_export)
    need(observed['local_files_unchanged'] and repeated_export['local_files_unchanged'], 'independent_read_mutated_inputs')
    export_result = check_exports(gate.out, reports)
    page = gate.request(('GET', '/sessions/' + sid + '/recordings/inbox-loopback/pages/1', 200, None, {}))
    need(page['state'] == 'published' and page['released'] is True and page['eos'] is True
         and len(page['sources']) == 9, 'native_page_geometry')
    for index, path in enumerate(paths):
        need(inspect_delivery(gate.out / 'inbox' / reports[index]['attempt'], validate_metadata=True) == reports[index],
             'arrived_original_changed')
    need({name: digest_file(retained / name) for name in FILES} == conflict_before, 'retained_conflict_changed')
    return dict(scenario_complete=True, session_id=sid, explicit_session_posts=1,
        inputs=9, input_frames=144000, original_epoch=7, original_sequences=list(range(2, 11)),
        original_frame_start=32000, original_frame_end=176000, terminal_reason='INTERRUPTION',
        clean_runner_process_restart=True, partial_arrival_refused=True,
        exact_repeat_no_duplicate_admission=True, differing_content_refused=True,
        conflict_selection_cannot_override=True, new_duplicate_requires_selection=True,
        conflict_copy_preserved=True, original_input_paths_preserved=True,
        phases=phases, private_export=export_result, independent_get_only_repeat=True)


def run(output, assets, fixture_path):
    os.umask(0o077)
    output = Path(output).resolve()
    need(not any((p / '.git').exists() for p in (output, *output.parents)), 'output_in_repository')
    fixture, reports = verify_fixture(fixture_path)
    originals = inventory([Path(row['path']) for row in fixture['deliveries']])
    private_directory(output); private_directory(output / 'data')
    gate = Gate(output, 1, 300); gate.journal = (output / 'http.jsonl').open('x')
    pins = source_pins(); save(output / 'SOURCE_PINS.json', pins)
    result = dict(schema='public-speech-local-inbox-loopback-v1', passed=False, actual_loopback_http=True,
        ftps_receiver_test=False, deployed=False, device_test=False, native_service_restart_test=False,
        abrupt_runner_crash_test=False, product_session_autocreation=False)
    try:
        result['startup'] = start_service(gate, assets)
        result.update(exercise(gate, fixture, reports))
    except BaseException as error:
        result['error'] = dict(type=type(error).__name__, message=str(error)[:2048])
    finally:
        gate.set_phase('stopping')
        try: result['shutdown'] = gate.stop()
        except BaseException as error:
            result['shutdown_error'] = dict(type=type(error).__name__, message=str(error)[:2048])
            try: result['forced_cleanup'] = gate.force_cleanup('inbox_qualification_shutdown_failed')
            except BaseException as cleanup: result['forced_cleanup_error'] = str(cleanup)[:2048]
        if gate.hold is not None and 'forced_cleanup_error' not in result:
            os.close(gate.hold)
        gate.journal.close()
        result['source_unchanged'] = source_pins() == pins
        result['originals_unchanged'] = inventory([Path(row['path']) for row in fixture['deliveries']]) == originals
        result['peak_sampled_rss'] = gate.peak_rss
        result['passed'] = not any(k.endswith('error') for k in result) and result['source_unchanged'] and result['originals_unchanged'] and result.get('scenario_complete') is True
        save(output / 'RESULT.json', result)
    return 0 if result['passed'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='mode', required=True)
    parent = sub.add_parser('run'); parent.add_argument('output', type=Path)
    parent.add_argument('--assets', required=True, type=Path); parent.add_argument('--fixture', required=True, type=Path)
    child = sub.add_parser('child'); child.add_argument('spec', type=Path); child.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    if args.mode == 'run': return run(args.output, args.assets, args.fixture)
    need(args.spec.stat().st_size <= 65536 and not args.output.exists(), 'child_input_bounds')
    return run_child(json.loads(args.spec.read_bytes()), args.output)


if __name__ == '__main__':
    raise SystemExit(main())
