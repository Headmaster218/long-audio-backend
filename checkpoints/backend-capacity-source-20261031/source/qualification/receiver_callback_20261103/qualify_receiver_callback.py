#!/usr/bin/env python3
"""Reviewed-only real FTPS lifecycle callback and model-free admission qualification."""
from fixture_support import *

PYTHON = '/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python'
TEST_TOOLS = Path('/workspace/scratch/12ec3121e93f/audio-recovery-test-tools-20261023b')


def source_pins(android, backend, tool_manifest):
    assembled = json.loads((android/'ASSEMBLED_SOURCE.json').read_text())
    need(assembled['source_commit'] == ANDROID_PIN, 'Android commit')
    files = [android/'ASSEMBLED_SOURCE.json']
    for row in assembled['files']:
        item = android/row['path']
        need(item.stat().st_size == row['bytes'] and digest(item) == row['sha256'], 'Android source integrity')
        files.append(item)
    files += list((backend/'audio_backend').rglob('*.py'))
    files += [backend/'tests'/name for name in ('test_extended_recorder_intake.py',
        'test_recorder_sequence.py', 'test_recorder_metadata.py')]
    files += [path for path in (android/'app/build/ftps-wire-dependencies').rglob('*') if path.is_file()]
    files += list(HERE.glob('*.py')) + [HERE/'PLAN.md', tool_manifest]
    rows = tool_manifest.read_text().splitlines()
    need(len(rows) == 1304, 'exact prior test-tool file count')
    total = 0
    for row in rows:
        checksum, name = row.split('  ', 1)
        item = Path(name)
        need(item.is_relative_to(TEST_TOOLS) and digest(item) == checksum, 'test-tool integrity')
        total += item.stat().st_size
    need(total == 17116775, 'test-tool total bytes')
    return dict(schema='receiver-callback-source-pins-v1', android_commit=ANDROID_PIN,
        backend_baseline_commit=BACKEND_BASE, python=PYTHON, test_tool_files=1304,
        test_tool_bytes=total, files={str(p):digest(p) for p in sorted(set(files))})


def imports(args):
    need(sys.version.split()[0] == '3.12.14' and Path(sys.executable).resolve() == Path(PYTHON).resolve(), 'exact validated Python')
    need('PYTHONOPTIMIZE' not in os.environ and sys.flags.optimize == 0, 'PYTHONOPTIMIZE must be unset')
    need(os.environ.get('PYTHONPATH') == str(TEST_TOOLS), 'explicit unchanged test-tools PYTHONPATH')
    android, backend = args.android.resolve(), args.backend.resolve()
    deps = android/'app/build/ftps-wire-dependencies/installed'
    sys.path[:0] = [str(deps), str(backend), str(backend/'tests')]
    manifest = json.loads((android/'app/src/testFixtures/ftps-wire/dependencies.json').read_text())
    for row in manifest['packages']:
        archive = deps.parent/'archives'/row['filename']
        need(archive.stat().st_size == row['size'] and digest(archive) == row['sha256'], 'dependency archive')
        need(importlib.metadata.version(row['name']) == row['version'], 'dependency version')
    from audio_backend import recorder_receiver_publication as receiver
    from audio_backend import recorder_receiver_completion as completion
    from audio_backend import recorder_inbox_runner as runner
    from audio_backend import recording_uploader as uploader
    from audio_backend.recorder_delivery import inspect_delivery
    from audio_backend.recorder_metadata import decode
    from test_extended_recorder_intake import Peer
    import receiver_callback_server
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import rsa
    from OpenSSL import SSL
    versions = {name:importlib.metadata.version(name) for name in ('cryptography', 'cffi',
        'typing_extensions', 'setuptools', 'wheel', 'pyftpdlib', 'pyOpenSSL', 'pyasyncore', 'pyasynchat', 'pytest')}
    modules = {}
    for name, module in sorted(sys.modules.items()):
        filename = getattr(module, '__file__', None)
        if filename and Path(filename).is_file():
            modules[name] = dict(file=str(Path(filename).resolve()), sha256=digest(filename))
    return dict(interpreter=sys.executable, python=sys.version.split()[0], versions=versions,
        modules=modules, keys_created=False, server_started=False, network_connections_started=False)


def crash_open(args):
    """Separate process exits with a complete-looking marker writer still live."""
    sys.path[:0] = [str(args.backend.resolve())]
    from audio_backend import recorder_receiver_publication as receiver
    from audio_backend import recorder_receiver_completion as completion
    source = args.raw[0]
    state = args.receiver_state
    config = receiver._config(state)
    adapter = completion.ReceiverCompletion(binding=receiver.bind(state), inbox_dir=Path(config['inbox_dir']),
        exclusive_receiver=True, deadline_seconds=10)
    name = source.name[:-32] + 'f'*32
    need(name != source.name, 'distinct uncertainty nonce')
    attempt = adapter.begin_attempt(name)
    for member in ('audio.wav', 'segment.v1.bin'):
        writer = adapter.open_writer(attempt, member)
        writer.stream.write((source/member).read_bytes())
        writer.stream.close()
        outcome = adapter.writer_closed(writer, successful=True)
        need(outcome['status'] == 'receiving', 'two successes cannot publish')
    writer = adapter.open_writer(attempt, 'delivery.v1.txt')
    writer.stream.write((source/'delivery.v1.txt').read_bytes().replace(source.name.encode(), name.encode()))
    writer.stream.flush()
    os.fsync(writer.stream.fileno())
    need(not writer.stream.closed, 'uncertain marker handle remains open')
    print(json.dumps(dict(attempt=name, path=str(Path(config['staging_dir'])/name),
        marker_handle_open=True, successful_files=outcome['successful_files'],
        marker_callback_invoked=False, exit_without_adapter_close=True), sort_keys=True), flush=True)
    os._exit(0)


def restart(args):
    sys.path[:0] = [str(args.backend.resolve()), str(args.backend.resolve()/'tests')]
    from audio_backend import recorder_receiver_publication as receiver
    from audio_backend import recorder_receiver_completion as completion
    from audio_backend import recorder_inbox_runner as runner
    from audio_backend import recording_uploader as uploader
    state = args.receiver_state
    inbox = Path(receiver._config(state)['inbox_dir'])
    before = inventory([inbox, args.runner_state, args.job, state, *args.raw])
    adapter = completion.ReceiverCompletion(binding=receiver.bind(state), inbox_dir=inbox,
        exclusive_receiver=True, deadline_seconds=10)
    refusals = []
    try:
        for raw in args.raw:
            try:
                adapter.begin_attempt(raw.name)
                raise AssertionError('restart adopted existing attempt')
            except receiver.Refused as error:
                refusals.append(dict(attempt=raw.name, reason=str(error)))
    finally:
        adapter.close()
    class NoRequests:
        def request(self, *a, **kw):
            raise AssertionError('restart made a peer request')
    watched = runner.run(state_dir=args.runner_state, exclusive_writer=True,
        acknowledge_span_local_closure=True, max_actions=4, deadline_seconds=10,
        transport=NoRequests(), limits=uploader.Limits(disk_floor=0))
    need(watched['status'] == 'awaiting_finalize' and watched['inputs'] == 1
        and watched['actions'] == watched['network_requests'] == 0, 'restart runner outcome')
    need(inventory([inbox, args.runner_state, args.job, state, *args.raw]) == before, 'restart preserved exact state')
    print(json.dumps(dict(refusals=refusals, runner=watched, unchanged=True), sort_keys=True))


def execute(args, pins):
    preflight = imports(args)
    os.umask(0o077)
    need(sys.platform == 'linux' and os.sched_getaffinity(0) == {3}
        and os.getpriority(os.PRIO_PROCESS, 0) >= 19, 'taskset -c 3 nice -n 19 required')
    from audio_backend import recorder_receiver_publication as receiver
    from audio_backend import recorder_inbox_runner as runner
    from audio_backend import recording_uploader as uploader
    from audio_backend.recorder_delivery import inspect_delivery
    from audio_backend.recorder_metadata import decode
    from test_extended_recorder_intake import Peer
    android, backend, work = args.android.resolve(), args.backend.resolve(), args.output.resolve()
    deps = android/'app/build/ftps-wire-dependencies/installed'
    need(not work.exists(), 'fresh private output required')
    work.mkdir(mode=0o700)
    guard = Guard(work)
    keys = work/'ephemeral-keys'
    keys.mkdir(mode=0o700)
    report = dict(schema='receiver-callback-wire-qualification-v1', passed=False,
        model_free=True, native_inference_executed=False, android_device_executed=False,
        manual_publication=False, source_pins=pins, preflight=preflight,
        limits=dict(seconds=600, server_seconds=60, client_seconds=30,
        process_tree_rss=384*1024**2, output_bytes=8*1024**2,
        wire_bytes=2*1024**2, sessions=5, disk_floor=5*1024**3))
    watch_thread = None
    cancel = threading.Event()
    observations, peer_calls, watch_result = [], [], {}
    old_inventory = receiver.inbox.inventory
    def interrupted(signum, frame):
        raise KeyboardInterrupt('termination signal '+str(signum))
    old_signals = {sig:signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        report['certificates'] = prepare_tls(keys)
        guard.invoke(['keytool', '-importcert', '-noprompt', '-storetype', 'PKCS12', '-keystore', keys/'trust.p12',
            '-storepass', 'synthetic', '-alias', 'fixture-ca', '-file', keys/'ca.pem'], work/'trust-setup.log')
        java = ['java', '-Xmx64m', '-XX:MaxMetaspaceSize=48m', '-XX:ReservedCodeCacheSize=8m',
            '-XX:+UseSerialGC', '-XX:ActiveProcessorCount=1', '-Xss256k']
        classes = work/'classes'
        classes.mkdir()
        sources = sorted((android/'app/src/testFixtures/ftps-wire').rglob('*.java'))
        sources += sorted((android/'app/src/main/java/io/github/headmaster218/recorder/core').glob('*.java'))
        sources += [android/'app/src/main/java/io/github/headmaster218/recorder/android/FtpsNetwork.java']
        source_list = work/'javac-sources.txt'
        source_list.write_text('\n'.join(str(p) for p in sources)+'\n')
        guard.invoke(java+['-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '8', '-target', '8',
            '-Xlint:all,-options', '-Werror', '-d', classes, '@'+str(source_list)], work/'compile.log', 60)
        report['compiled_java_sources'] = len(sources)
        case = work/'wire'
        case.mkdir(mode=0o700)
        env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(deps), str(backend), str(TEST_TOOLS))), PYTHONDONTWRITEBYTECODE='1')
        server = guard.spawn([sys.executable, '-B', HERE/'receiver_callback_server.py', case,
            keys/'server.pem', keys/'server.key'], work/'server.log', env)
        guard.until(lambda:(case/'ready.json').exists(), 'server ready', processes=[server])
        address = json.loads((case/'ready.json').read_text())
        need(address['host'] == '127.0.0.1', 'loopback server')
        inbox, receiver_state = Path(address['inbox']), Path(address['receiver_state'])
        client_command = java+['-Djavax.net.ssl.trustStore='+str(keys/'trust.p12'),
            '-Djavax.net.ssl.trustStorePassword=synthetic', '-Djavax.net.ssl.trustStoreType=PKCS12',
            '-cp', classes, 'io.github.headmaster218.recorder.android.WireClient']
        client_started = time.monotonic()
        client = guard.spawn(client_command+['success', case/'client-success', str(address['port'])], work/'client-success.log')
        guard.until(lambda:(case/'marker-held.json').exists(), 'live partial marker', 10, [server, client])
        held = json.loads((case/'marker-held.json').read_text())
        raw, marker = Path(held['path']).parent, Path(held['path'])
        need(0 < held['size'] <= 32 and not held['handle_closed'] and held['live_writers'] == 1, 'true held writer')
        need(len(held['callbacks']) == 2 and all(row['successful'] for row in held['callbacks']), 'audio and metadata close callbacks')
        need(not (case/'completed-delivery.json').exists(), 'no marker success yet')
        runner_state, job = work/'runner', work/'job'
        local_manifests = list((case/'client-success/spool').glob('*.ready/manifest.bin'))
        need(len(local_manifests) == 1, 'one producer source')
        metadata = decode(local_manifests[0].read_bytes())
        origin = {key:metadata[key] for key in ('epoch', 'sample_rate_hz', 'channels', 'requested_input',
            'actual_input', 'boundary', 'target_frames')}
        origin.update(capture_identity_sha256=hashlib.sha256(metadata['capture_id'].encode()).hexdigest(),
            run_identity_sha256=hashlib.sha256(metadata['run_id'].encode()).hexdigest(),
            first_sequence=metadata['sequence'], epoch_frame_start=metadata['frame_start'])
        config = dict(schema=runner.CONFIG_SCHEMA, inbox_dir=str(inbox), job_dir=str(job), output_dir=None,
            api='http://127.0.0.1:8765', session_id='known', capture_id='extended', span_origin=origin,
            max_hours=1, max_inputs=1, receiver_publication=receiver.bind(receiver_state))
        runner.configure(state_dir=runner_state, config=config)
        before = inventory([inbox, runner_state, job])
        class ObservedPeer(Peer):
            def request(self, method, path, **kwargs):
                peer_calls.append(dict(method=method, path=path, monotonic=time.monotonic()))
                return super().request(method, path, **kwargs)
        peer = ObservedPeer()
        def observed_inventory(*a, **kw):
            result = old_inventory(*a, **kw)
            observations.append(dict(monotonic=time.monotonic(), entries=len(result['deliveries']), peer_calls=len(peer_calls)))
            return result
        receiver.inbox.inventory = observed_inventory
        def watching():
            try:
                watch_result['result'] = runner.watch(state_dir=runner_state, exclusive_writer=True,
                    acknowledge_span_local_closure=True, limits=uploader.Limits(disk_floor=0), transport=peer,
                    deadline_seconds=30, poll_seconds=.1, max_polls=256, max_actions=4,
                    max_requests=128, max_network_writes=64, max_network_bytes=8*1024**2, cancel=cancel.is_set)
            except BaseException as error:
                watch_result['error'] = dict(type=type(error).__name__, message=str(error))
        watch_thread = threading.Thread(target=watching, name='production-runner-watch', daemon=True)
        watch_thread.start()
        guard.until(lambda:len(observations)>=2, 'two held empty polls', processes=[server, client])
        need(watch_thread.is_alive() and not watch_result and not peer_calls, 'same waiting watch without calls')
        need(inventory([inbox, runner_state, job]) == before and marker.stat().st_size == held['size'], 'partial upload isolated')
        report['partial_marker_hold'] = dict(observation=held, polls=list(observations), unchanged=True, peer_calls=0)
        (case/'release-marker').touch()
        guard.finish(client, max(.1, 30-(time.monotonic()-client_started)))
        receipt = json.loads((work/'client-success.log').read_text().splitlines()[-1])
        completion = json.loads((case/'completed-delivery.json').read_text())
        need(receipt['status'] == 'verified' and receipt['local_unchanged'] and receipt['all_raw_sockets_closed']
            and not receipt['cleanup_invoked'], 'production Java verified result')
        need(receipt['callback_registrations'] == receipt['callback_unregistrations'], 'Java callbacks released')
        need(completion['handle_closed'] and completion['live_writers'] == 0 and completion['successful']
            and completion['result']['status'] == 'published'
            and completion['result']['successful_files'] == sorted(FILES)
            and completion['result']['pending_writers'] == 0
            and completion['duplicate_result'] == completion['result'], 'closed successful callback')
        guard.until(lambda:not watch_thread.is_alive(), 'same watch admission', 10, [server])
        watch_thread.join()
        need('error' not in watch_result, 'watch failed '+str(watch_result))
        watched = watch_result['result']
        need(watched['status'] == 'awaiting_finalize' and watched['inputs'] == watched['actions'] == 1
            and not watched['analysis_completion_verified'], 'one model-free admission')
        original = {name:(raw/name).read_bytes() for name in FILES}
        expected, target = inspect_delivery(raw, validate_metadata=True), inbox/raw.name
        need(inspect_delivery(target, validate_metadata=True) == expected, 'exact published semantics')
        need(all((target/n).read_bytes() == original[n] and (target/n).stat().st_ino != (raw/n).stat().st_ino for n in FILES), 'independent exact snapshot')
        frozen, frozen_raw = inventory([inbox, runner_state, job]), inventory([raw])
        tls = ssl.create_default_context(cafile=str(keys/'ca.pem'))
        with ftplib.FTP_TLS(context=tls, timeout=5) as ftp:
            ftp.connect('127.0.0.1', address['port'])
            ftp.login('synthetic', 'synthetic'); ftp.prot_p(); ftp.cwd('/fixture/'+raw.name)
            try:
                ftp.storbinary('STOR audio.wav', io.BytesIO(b'overwrite-must-not-start'))
                raise AssertionError('sealed attempt accepted another STOR')
            except ftplib.error_perm as error:
                restorage_refusal = str(error)
            ftp.quit()
        need(inventory([raw]) == frozen_raw and inventory([inbox, runner_state, job]) == frozen, 'refused STOR made no mutation')
        (case/'interrupt-marker').touch()
        failed_client = guard.spawn(client_command+['partial_disconnect', case/'client-failure', str(address['port'])], work/'client-failure.log')
        guard.finish(failed_client, 30)
        failed_receipt = json.loads((work/'client-failure.log').read_text().splitlines()[-1])
        failed = json.loads((case/'failed-delivery.json').read_text())
        failed_raw = Path(failed['path']).parent
        need(failed_receipt['status'] == 'expected_failure' and failed_receipt['reconcile_only']
            and failed_receipt['reconcile_failed'] and failed_receipt['local_unchanged'], 'actual partial failure and readonly reconciliation')
        need(failed['handle_closed'] and failed['live_writers'] == 0 and not failed['successful']
            and failed['result']['status'] == 'failed'
            and failed['result']['reason'] == 'completion_transfer_failed'
            and failed['duplicate_result'] == failed['result']
            and 0 < failed['bytes'] <= 32 and not (inbox/failed_raw.name).exists(), 'incomplete callback never publishes')
        need(inventory([inbox, runner_state, job]) == frozen, 'failed attempt cannot alter selection')
        (case/'stop').touch()
        guard.finish(server, 3)
        facts = json.loads((case/'server-result.json').read_text())
        need(facts['all_closed'] and facts['live_writers_at_exit'] == 0 and not facts['errors'], 'server closed without errors')
        need(len(facts['callbacks']) == 6 and sum(r['successful'] for r in facts['callbacks']) == 5, 'actual callback count')
        need(facts['sessions'] == 4 and len(facts['opens']) == 6, 'exact sessions and admitted opens')
        need(len([r for r in facts['commands'] if r['cmd']=='STOR']) == 7, 'six admitted and one refused STOR')
        need(len(facts['refusals']) == 1 and facts['refusals'][0]['operation'] == 'open', 'sealed admission refusal')
        guard.invoke([sys.executable, '-B', Path(__file__).resolve(), '--crash-open', '--backend', backend,
            '--receiver-state', receiver_state, '--raw', raw], work/'crash-open.json', 10,
            dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        crash_facts = json.loads((work/'crash-open.json').read_text())
        crash_raw = Path(crash_facts['path'])
        need(inspect_delivery(crash_raw, validate_metadata=True)['attempt'] == crash_raw.name,
            'uncertain disk bytes are a structurally valid full delivery')
        need(not (inbox/crash_raw.name).exists() and inventory([inbox, runner_state, job]) == frozen,
            'process exit cannot publish complete-looking files')
        guard.invoke([sys.executable, '-B', Path(__file__).resolve(), '--restart', '--backend', backend,
            '--receiver-state', receiver_state, '--runner-state', runner_state, '--job', job,
            '--raw', raw, '--raw', failed_raw, '--raw', crash_raw], work/'restart.json', 10,
            dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        need(inventory([inbox, runner_state, job]) == frozen, 'fresh-process preserved selection')
        report.update(passed=True, client=receipt, failed_client=failed_receipt,
            completion=completion, incomplete_callback=failed, watch=watched, peer_calls=peer_calls,
            callback_facts=facts['callbacks'], trusted_scope=address['trusted_scope'],
            duplicate_stor_refusal=restorage_refusal, crash_open=crash_facts, restart=json.loads((work/'restart.json').read_text()),
            all_three_files_exact=True, selected_input_preserved=True,
            wire=dict(sessions=facts['sessions'], stor_commands=7, data_bytes=facts['data_bytes'],
                control_tls=facts['control_tls'], data_tls=facts['data_tls']),
            raw_server_report_sha256=digest(case/'server-result.json'))
    except BaseException as error:
        report['failure'] = dict(type=type(error).__name__, message=str(error))
        raise
    finally:
        cancel.set()
        if watch_thread is not None: watch_thread.join(timeout=2)
        receiver.inbox.inventory = old_inventory
        report['owned_processes'] = guard.cleanup()
        ports = set()
        if (work/'wire/ready.json').exists(): ports.add(json.loads((work/'wire/ready.json').read_text())['port'])
        if (work/'wire/server-result.json').exists(): ports.update(json.loads((work/'wire/server-result.json').read_text())['listeners'])
        checks = []
        for port in sorted(ports):
            with socket.socket() as probe:
                probe.settimeout(.2)
                checks.append(dict(port=port, closed=probe.connect_ex(('127.0.0.1', port)) != 0))
        report['cleanup_listener_checks'] = checks
        report['watch_thread_closed'] = watch_thread is None or not watch_thread.is_alive()
        if not all(row['closed'] for row in checks) or not report['watch_thread_closed']:
            report['passed'] = False
            report['cleanup_failure'] = 'listener or thread still alive'
        shutil.rmtree(keys)
        report['ephemeral_keys_removed'] = not keys.exists()
        report['peak_process_tree_rss'] = guard.peak
        report['seconds'] = round(time.monotonic()-guard.started, 3)
        try: report['source_hashes_unchanged_at_exit'] = source_pins(android, backend, args.test_tool_manifest) == pins
        except BaseException as error:
            report['source_hashes_unchanged_at_exit'] = False
            report['source_hash_check_error'] = type(error).__name__
        if not report['source_hashes_unchanged_at_exit']: report['passed'] = False
        for sig, previous in old_signals.items(): signal.signal(sig, previous)
        save(work/'report.json', report)
        print('REPORT '+str(work/'report.json'), flush=True)
    need(report['passed'], 'qualification or cleanup failed; preserve evidence')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('android', 'backend', 'pins', 'record-pins', 'output', 'test-tool-manifest', 'preflight',
                 'receiver-state', 'runner-state', 'job'):
        parser.add_argument('--'+name, type=Path)
    parser.add_argument('--raw', type=Path, action='append')
    parser.add_argument('--execute-reviewed-fixture', action='store_true')
    parser.add_argument('--restart', action='store_true')
    parser.add_argument('--crash-open', action='store_true')
    args = parser.parse_args()
    need(args.backend is not None, 'backend required')
    if args.restart:
        restart(args); return
    if args.crash_open:
        crash_open(args); return
    need(args.android is not None and args.test_tool_manifest is not None, 'Android and test-tools manifest required')
    pins = source_pins(args.android.resolve(), args.backend.resolve(), args.test_tool_manifest.resolve())
    if args.record_pins:
        need(not args.record_pins.exists(), 'fresh pins output required')
        save(args.record_pins, pins); print('PINS '+str(args.record_pins)); return
    if args.preflight:
        need(not args.preflight.exists(), 'fresh preflight output required')
        save(args.preflight, imports(args)); print('PREFLIGHT '+str(args.preflight)); return
    need(args.execute_reviewed_fixture and args.pins and args.output, 'reviewed flag/pins/fresh output required')
    need(json.loads(args.pins.read_text()) == pins, 'exact reviewed source pins')
    execute(args, pins)


if __name__ == '__main__':
    main()
