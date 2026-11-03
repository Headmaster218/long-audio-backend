"""One explicitly reviewed finite watch against fresh real loopback native service.

Controlled local genuine producer arrivals, not FTPS/device/deployment evidence.
Harness-only observers call the unchanged marker validator and HTTP transport;
they never bypass checks, manufacture replies, or insert transcript/model data.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'qualification'), str(ROOT / 'tools'), str(ROOT)]
import qualify_local_inbox_loopback as baseline
from capacity_run import Gate, digest_file, save, source_pins
from strict_incremental_span_probe import Trace, inventory, need, verify_status
from audio_backend import recording_uploader as u
from audio_backend.recorder_delivery import inspect_delivery

FILES = baseline.FILES
WATCH_BOUNDS = dict(max_actions=256, max_requests=1024, max_network_writes=512,
    max_network_bytes=256 * 1024**2, deadline_seconds=120, poll_seconds=.1, max_polls=512)


def publish_noreplace(source, destination):
    """Linux harness-only atomic publication; an existing target is never replaced."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), str(destination))


def arrive(source, inbox, staging, *, partial=False):
    """Publish a stable exact-byte directory, not an accidental torn file write."""
    prepared = baseline.arrive(source, staging, partial=partial)
    target = Path(inbox) / prepared.name
    publish_noreplace(prepared, target)
    return target


def complete_arrival(source, target, staging):
    marker = Path(staging) / ('marker-' + Path(target).name)
    baseline.private_write(marker, (Path(source) / 'delivery.v1.txt').read_bytes())
    destination = Path(target) / 'delivery.v1.txt'
    publish_noreplace(marker, destination)
    return inspect_delivery(target, validate_metadata=True)


def verify_fixture(path, expected_sha256):
    path = Path(path)
    need(len(expected_sha256) == 64 and path.stat().st_size <= 128 * 1024
         and digest_file(path) == expected_sha256, 'fixture_pin')
    value = json.loads(path.read_bytes())
    need(value.get('passed') is True and value.get('terminal_reason') == 'STOP'
         and value.get('terminal_api') == 'PcmSegmentWriter.stop()'
         and value.get('producer_commit') == '2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9'
         and value.get('current_source_commit_verified') is True
         and value.get('producer_checkout_clean') is True
         and value.get('selected_pcm_sha256') == '824962e469c1b5c3969804b2846b1428fe4a194a1744d5599aa595ae7f69af73'
         and value.get('total_selected_frames') == 144000 and len(value['deliveries']) == 9,
         'fixture_geometry_or_provenance')
    reports = []
    for index, row in enumerate(value['deliveries']):
        source = Path(row['path']); report = inspect_delivery(source, validate_metadata=True)
        need(report == row['delivery'] and all(digest_file(source / name) == row['hashes'][name]
             for name in FILES), 'fixture_bytes_changed')
        need(report['sequence'] == index + 2 and report['epoch'] == 7 and report['frames'] == 16000
             and report['frame_start'] == 32000 + index * 16000
             and report['close_reason'] == ('STOP' if index == 8 else 'ROTATION'), 'fixture_declarations')
        reports.append(report)
    return value, reports


class Events:
    def __init__(self, path):
        self.stream = Path(path).open('x'); self.count = 0
    def emit(self, event, **fields):
        need(self.count < 4096, 'event_bound')
        self.count += 1
        value = dict(event=event, index=self.count, monotonic=time.monotonic(), pid=os.getpid(), **fields)
        self.stream.write(json.dumps(value, sort_keys=True) + '\n')
        self.stream.flush(); os.fsync(self.stream.fileno())
    def close(self): self.stream.close()


def read_events(path):
    path = Path(path)
    if not path.exists(): return []
    need(path.stat().st_size <= 4 * 1024**2, 'event_file_bound')
    raw = path.read_bytes()
    # A concurrent append can be observed before its final newline.
    return [json.loads(line) for line in raw.split(b'\n')[:-1]]


def run_watch_child(spec, output):
    from audio_backend import recorder_inbox_runner as runner
    events = Events(spec['watch_events'])
    cancelled = []
    previous = {sig: signal.signal(sig, lambda signum, frame: cancelled.append(signum))
                for sig in (signal.SIGINT, signal.SIGTERM)}
    limits = u.Limits(deadline_seconds=120, poll_seconds=.1)
    class ObservedTrace(Trace):
        def request(self, method, path, **kwargs):
            events.emit('request_start', method=method, path=path, request_count=len(self.calls) + 1)
            try: result = super().request(method, path, **kwargs)
            except BaseException as error:
                events.emit('request_error', method=method, path=path, request_count=len(self.calls),
                    error_type=type(error).__name__, status=getattr(error, 'status', None))
                raise
            events.emit('request_return', method=method, path=path, request_count=len(self.calls))
            return result
    transport = ObservedTrace(spec['api'], u.Guard(limits))
    original = runner._unpublished
    original_export = runner.exporter.run
    original_intake = runner.intake.run
    pending_count = 0
    def observed_unpublished(path, remaining):
        nonlocal pending_count
        result = original(path, remaining)
        pending_count += 1
        need(pending_count <= 128, 'pending_observation_bound')
        events.emit('marker_pending_validated', path=str(path), wav_bytes=result,
            request_count=len(transport.calls), writes=sum(row['method'] != 'GET' for row in transport.calls))
        return result
    runner._unpublished = observed_unpublished
    def observed_export(**kwargs):
        events.emit('export_enter', request_count=len(transport.calls))
        result = original_export(**kwargs)
        events.emit('export_return', request_count=len(transport.calls))
        return result
    runner.exporter.run = observed_export
    def observed_intake(**kwargs):
        if kwargs.get('finalize'):
            state = Path(spec['state_dir'])
            count = len(list(state.glob('input-*.done.json')))
            last = json.loads((state / ('input-%03d.intent.json' % (count - 1))).read_bytes())
            events.emit('finalize_enter', request_count=len(transport.calls),
                inputs=count, last_admitted_delivery=last['delivery'])
        result = original_intake(**kwargs)
        if kwargs.get('finalize'):
            events.emit('finalize_return', request_count=len(transport.calls),
                capture_finalized=result.get('capture_finalized'))
        return result
    runner.intake.run = observed_intake
    report = dict(schema='native-inbox-watch-child-v1', phase='finite-watch', pid=os.getpid(),
                  requests=transport.calls, returned=False)
    try:
        events.emit('watch_enter', bounds=WATCH_BOUNDS, finalize=True, export=True, wait_for_marker_last=True)
        report['result'] = runner.watch(state_dir=Path(spec['state_dir']), exclusive_writer=True,
            acknowledge_span_local_closure=True, finalize=True, export=True, wait_for_marker_last=True,
            limits=limits, transport=transport, cancel=lambda: bool(cancelled), **WATCH_BOUNDS)
        report['returned'] = True
        events.emit('watch_return', result=report['result'])
    except BaseException as error:
        report['error'] = dict(type=type(error).__name__, message=str(error)[:2048])
    finally:
        runner._unpublished = original
        runner.exporter.run = original_export
        runner.intake.run = original_intake
        for sig, handler in previous.items(): signal.signal(sig, handler)
        report['pending_observations'] = pending_count
        save(Path(output), report); events.close()
    return 0


def reap(process, output):
    code = None
    try:
        if process.poll() is None:
            process.terminate()
            try: code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); code = process.wait(timeout=5)
        else: code = process.wait(timeout=1)
    finally:
        save(Path(output), dict(pid=process.pid, original_popen_wait_completed=code is not None, exit_code=code))
    return code


def wait_for(gate, process, predicate, reason):
    end = min(gate.deadline, time.monotonic() + 30)
    while not predicate():
        gate.check()
        need(process.poll() is None, 'watch_exited_before:' + reason)
        need(time.monotonic() < end, 'observation_deadline:' + reason)
        time.sleep(.025)
    gate.check()


def exercise(gate, fixture, reports):
    from audio_backend import recorder_inbox_runner as runner
    paths = [Path(row['path']) for row in fixture['deliveries']]
    inbox = gate.out / 'inbox'; baseline.private_directory(inbox)
    staging = gate.out / 'arrival-staging'; baseline.private_directory(staging)
    first = arrive(paths[0], inbox, staging)
    sid = gate.request(('POST', '/sessions', 201, {'name': 'Bounded genuine STOP finite inbox watch'}, {}))['id']
    base = dict(api='http://127.0.0.1:' + str(gate.port), session_id=sid, capture_id='inbox-watch',
        state_dir=str(gate.out / 'runner'), job_dir=str(gate.out / 'intake'),
        repeat_output_dir=str(gate.out / 'export-repeat'), deliveries=[str(path) for path in paths],
        watch_events=str(gate.out / 'watch-events.jsonl'))
    config = runner.config_from_delivery(first_delivery=first, inbox_dir=inbox, job_dir=base['job_dir'],
        output_dir=str(gate.out / 'export'), api=base['api'], session_id=sid,
        capture_id=base['capture_id'], max_hours=1, max_inputs=9)
    configured = runner.configure(state_dir=Path(base['state_dir']), config=config, first_delivery=first)
    need(configured['network_requests'] == 0 and configured['intake_started'] is False, 'configure_mutated_remote')
    save(gate.out / 'watch-spec.json', dict(base, watch_bounds=WATCH_BOUNDS, finalize=True, export=True,
                                         wait_for_marker_last=True, first_delivery=str(first)))
    events = Events(gate.out / 'arrival-events.jsonl')
    events.emit('configured', first_delivery=str(first), session_id=sid, explicit_session_posts=1)
    state = Path(base['state_dir']); job = Path(base['job_dir'])
    def count(): return len(list(state.glob('input-*.done.json')))
    def watch_events(): return read_events(base['watch_events'])
    command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), 'watch-child',
               str(gate.out / 'watch-spec.json'), str(gate.out / 'watch-result.json')]
    code = None; hold = None
    with (gate.out / 'watch.stdout').open('xb') as stdout, (gate.out / 'watch.stderr').open('xb') as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, cwd=ROOT)
        try:
            wait_for(gate, process, lambda: count() == 1, 'first_admission')
            events.emit('first_admitted', inputs=count(), watch_pid=process.pid)
            before = inventory([state, job])
            target = arrive(paths[1], inbox, staging, partial=True)
            events.emit('partial_arrival', path=str(target), inputs=count())
            wait_for(gate, process, lambda: len([row for row in watch_events()
                if row['event'] == 'marker_pending_validated' and row['path'] == str(target)]) >= 2,
                'two_marker_pending_validations')
            pending = [row for row in watch_events() if row['event'] == 'marker_pending_validated']
            need(len(pending) >= 2 and pending[1]['monotonic'] > pending[0]['monotonic']
                 and pending[0]['request_count'] == pending[1]['request_count']
                 and pending[0]['writes'] == pending[1]['writes'] == 0,
                 'pending_marker_network_activity')
            after = inventory([state, job])
            need(after == before and count() == 1, 'pending_marker_mutated_state')
            hold = dict(path=str(target), first=pending[0], second=pending[1],
                        runner_intake_inventory_unchanged=True, inputs=1,
                        before_inventory=before, after_inventory=after)
            save(gate.out / 'MARKER_LAST_HOLD.json', hold)
            events.emit('pending_hold_verified', inputs=count(), request_count=pending[1]['request_count'], writes=0)
            complete_arrival(paths[1], target, staging)
            events.emit('marker_published', path=str(target), ordinal=1)
            wait_for(gate, process, lambda: count() == 2, 'second_admission')
            for index, path in enumerate(paths[2:], 2):
                target = arrive(path, inbox, staging)
                events.emit('complete_arrival', ordinal=index, path=str(target), close_reason=reports[index]['close_reason'])
                wait_for(gate, process, lambda: count() == index + 1, 'admission_%s' % index)
                events.emit('admitted', inputs=count(), ordinal=index)
            while process.poll() is None:
                gate.check(); time.sleep(.05)
        finally:
            code = reap(process, gate.out / 'watch-wait.json'); events.close()
    need(code == 0, 'watch_child_failed')
    watched = json.loads((gate.out / 'watch-result.json').read_bytes())
    outcome = baseline.assert_returned(watched)
    need(outcome['status'] == 'exported' and outcome['export_completed'] is True and outcome['inputs'] == 9
         and outcome['continuous_polling_enabled'] is True and outcome['invocation_bounded'] is True
         and outcome['polls'] > 9, 'watch_did_not_export')
    need(not any(row['method'] == 'POST' and row['path'] == '/sessions' for row in watched['requests']),
         'watch_created_session')
    observed = baseline.child_phase(gate, base, 'independent-status', operation='status')
    verify_status(baseline.assert_returned(observed), reports); baseline.assert_reads_only(observed)
    repeated = baseline.child_phase(gate, base, 'independent-export-repeat', operation='export-repeat')
    baseline.assert_returned(repeated); baseline.assert_reads_only(repeated)
    exported = baseline.check_exports(gate.out, reports)
    page = gate.request(('GET', '/sessions/' + sid + '/recordings/inbox-watch/pages/1', 200, None, {}))
    need(page['state'] == 'published' and page['released'] is True and page['eos'] is True
         and len(page['sources']) == 9, 'native_page_geometry')
    for index, path in enumerate(paths):
        target = inbox / reports[index]['attempt']
        need(inspect_delivery(target, validate_metadata=True) == reports[index]
             and all(digest_file(target / name) == fixture['deliveries'][index]['hashes'][name] for name in FILES),
             'arrived_original_changed')
    return dict(scenario_complete=True, session_id=sid, explicit_session_posts=1,
        one_foreground_watch_process=True, watch_pid=watched['pid'], watch=outcome,
        inputs=9, input_frames=144000, original_epoch=7, original_sequences=list(range(2, 11)),
        original_frame_start=32000, original_frame_end=176000, terminal_reason='STOP',
        marker_last_observed=True, marker_last_no_http_writes=True,
        marker_last_hold=hold, private_export=exported, independent_get_only_repeat=True)


def run(output, assets, fixture_path, fixture_sha256):
    os.umask(0o077); output = Path(output).resolve()
    baseline.require_nonrepository_output(output)
    fixture, reports = verify_fixture(fixture_path, fixture_sha256)
    originals = inventory([Path(row['path']) for row in fixture['deliveries']])
    baseline.private_directory(output); baseline.private_directory(output / 'data')
    gate = Gate(output, 1, 300); gate.journal = (output / 'http.jsonl').open('x')
    pins = source_pins(); save(output / 'SOURCE_PINS.json', pins)
    result = dict(schema='public-speech-native-inbox-watch-v1', passed=False, actual_loopback_http=True,
        ftps_receiver_test=False, deployed=False, device_test=False, native_service_restart_test=False,
        abrupt_runner_crash_test=False, product_session_autocreation=False)
    try:
        result['startup'] = baseline.start_service(gate, assets)
        result.update(exercise(gate, fixture, reports))
    except BaseException as error:
        result['error'] = dict(type=type(error).__name__, message=str(error)[:2048])
    finally:
        gate.set_phase('stopping')
        try: result['shutdown'] = gate.stop()
        except BaseException as error:
            result['shutdown_error'] = dict(type=type(error).__name__, message=str(error)[:2048])
            try: result['forced_cleanup'] = gate.force_cleanup('inbox_watch_shutdown_failed')
            except BaseException as cleanup: result['forced_cleanup_error'] = str(cleanup)[:2048]
        if gate.hold is not None and 'forced_cleanup_error' not in result: os.close(gate.hold)
        gate.journal.close()
        result['source_unchanged'] = source_pins() == pins
        result['originals_unchanged'] = inventory([Path(row['path']) for row in fixture['deliveries']]) == originals
        result['peak_sampled_rss'] = gate.peak_rss
        result['passed'] = not any(k.endswith('error') for k in result) and result['source_unchanged'] \
            and result['originals_unchanged'] and result.get('scenario_complete') is True
        save(output / 'RESULT.json', result)
    return 0 if result['passed'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='mode', required=True)
    parent = sub.add_parser('run'); parent.add_argument('output', type=Path)
    parent.add_argument('--assets', required=True, type=Path); parent.add_argument('--fixture', required=True, type=Path)
    parent.add_argument('--fixture-sha256', required=True)
    child = sub.add_parser('watch-child'); child.add_argument('spec', type=Path); child.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    if args.mode == 'run': return run(args.output, args.assets, args.fixture, args.fixture_sha256)
    need(args.spec.stat().st_size <= 65536 and not args.output.exists(), 'child_input_bounds')
    return run_watch_child(json.loads(args.spec.read_bytes()), args.output)


if __name__ == '__main__': raise SystemExit(main())
