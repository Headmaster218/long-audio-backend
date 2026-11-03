#!/usr/bin/env python3
"""Finite real FTPS-to-receiver-to-watch qualification, with a model-free API peer.

No native inference, Android device lifecycle, deployment, or persistent secrets.
Run only with reviewed source hashes and an explicit reviewed-fixture flag.
"""
import argparse
from contextlib import contextmanager
import datetime
import ftplib
import hashlib
import importlib.metadata
import io
import ipaddress
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time

ANDROID_PIN = '2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9'
BACKEND_BASE = '8204e1523c3119c65a82d0e3b94efac78257f799'
BACKEND_CHECKPOINT = '91b9f8125bf4d4bac43188e180d8d46b7fefb998'
FILES = ('audio.wav', 'segment.v1.bin', 'delivery.v1.txt')
HERE = Path(__file__).resolve().parent


def need(condition, label):
    if not condition:
        raise AssertionError(label)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def source_pins(android, backend):
    manifest = json.loads((android / 'ASSEMBLED_SOURCE.json').read_text())
    need(manifest['source_commit'] == ANDROID_PIN, 'android source pin')
    for row in manifest['files']:
        item = android / row['path']
        need(item.stat().st_size == row['bytes'] and digest(item) == row['sha256'], 'android manifest: ' + row['path'])
    files = list((backend / 'audio_backend').rglob('*.py'))
    files += [backend / 'tests/test_extended_recorder_intake.py', backend / 'tests/test_recorder_sequence.py', backend / 'tests/test_recorder_metadata.py']
    files += sorted((android / 'app/src/testFixtures/ftps-wire').rglob('*.java'))
    files += sorted((android / 'app/src/main/java/io/github/headmaster218/recorder/core').glob('*.java'))
    files += [android / 'app/src/main/java/io/github/headmaster218/recorder/android/FtpsNetwork.java',
        android / 'app/src/testFixtures/ftps-wire/dependencies.json', HERE / 'receiver_wire_server.py', Path(__file__).resolve()]
    return dict(android_commit=ANDROID_PIN, backend_baseline_commit=BACKEND_BASE, backend_checkpoint_commit=BACKEND_CHECKPOINT,
        files={str(path): digest(path) for path in sorted(files)})


def inventory(paths):
    answer = {}
    for root in paths:
        root = Path(root)
        if not root.exists():
            answer[str(root)] = None
            continue
        for path in [root, *sorted(root.rglob('*'))]:
            info = path.lstat()
            answer[str(path)] = dict(inode=info.st_ino, mode=info.st_mode,
                bytes=info.st_size if path.is_file() else None,
                sha256=digest(path) if path.is_file() else None)
    return answer


class Guard:
    def __init__(self, work):
        self.work = work
        self.started = time.monotonic()
        self.peak = 0
        self.owned = []

    def check(self):
        need(time.monotonic() - self.started < 600, '600-second suite deadline')
        need(shutil.disk_usage(self.work).free >= 5 * 1024**3, '5GiB disk floor')
        parents, rss = {}, {}
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                parents[int(entry.name)] = int(fields[1])
                rss[int(entry.name)] = int(fields[21]) * os.sysconf('SC_PAGE_SIZE')
            except (OSError, ValueError, IndexError):
                pass
        family = {os.getpid()}
        while True:
            more = {pid for pid, parent in parents.items() if parent in family} - family
            if not more:
                break
            family |= more
        self.peak = max(self.peak, sum(rss.get(pid, 0) for pid in family))
        need(self.peak <= 384 * 1024**2, '384MiB process-tree RSS')
        need(sum(p.stat().st_size for p in self.work.rglob('*') if p.is_file()) <= 8 * 1024**2, '8MiB output')

    def spawn(self, command, log, env=None):
        self.check()
        stream = Path(log).open('x')
        process = subprocess.Popen([str(x) for x in command], stdout=stream, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        self.owned.append((process, stream))
        return process

    def until(self, predicate, label, timeout=5, processes=()):
        end = time.monotonic() + timeout
        while not predicate():
            self.check()
            need(time.monotonic() <= end, 'wait deadline: ' + label)
            need(all(p.poll() is None for p in processes), 'process stopped before: ' + label)
            time.sleep(.02)
        self.check()

    def finish(self, process, timeout=30):
        self.until(lambda: process.poll() is not None, 'process completion', timeout)
        need(process.returncode == 0, 'subprocess exit: ' + str(process.returncode))

    def invoke(self, command, log, timeout=30, env=None):
        process = self.spawn(command, log, env)
        self.finish(process, timeout)
        return process

    def cleanup(self):
        result = []
        for process, stream in self.owned:
            try:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=2)
                result.append(dict(pid=process.pid, reaped=process.poll() is not None,
                    returncode=process.returncode))
            finally:
                stream.close()
        return result


def prepare_tls(keys):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic receiver fixture CA')])
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic loopback receiver')])

    def build(name, public, ca=False):
        builder = x509.CertificateBuilder().subject_name(name).issuer_name(ca_name).public_key(public)
        builder = builder.serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
        builder = builder.not_valid_after(now + datetime.timedelta(days=1))
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
        if not ca:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        return builder.sign(ca_key, hashes.SHA256())
    ca = build(ca_name, ca_key.public_key(), True)
    leaf = build(server_name, server_key.public_key())
    (keys / 'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (keys / 'server.pem').write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (keys / 'server.key').write_bytes(server_key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return dict(ca_sha256=ca.fingerprint(hashes.SHA256()).hex(), server_sha256=leaf.fingerprint(hashes.SHA256()).hex())


def restart(backend, state, receiver_state, raw):
    sys.path[:0] = [str(backend), str(backend / 'tests')]
    from audio_backend import recorder_receiver_publication as receiver
    from audio_backend import recorder_inbox_runner as runner
    from audio_backend import recording_uploader as uploader
    class NoRequests:
        def request(self, *args, **kwargs):
            raise AssertionError('restart made an unexpected API request')
    published = receiver.publish_completed(state_dir=receiver_state, delivery=raw, completed_delivery=True, deadline_seconds=10)
    watched = runner.run(state_dir=state, exclusive_writer=True, acknowledge_span_local_closure=True,
        max_actions=4, deadline_seconds=10, transport=NoRequests(), limits=uploader.Limits(disk_floor=0))
    need(published['status'] == 'already_published', 'restart publication')
    need(watched['status'] == 'awaiting_finalize' and watched['inputs'] == 1
        and watched['actions'] == watched['network_requests'] == 0, 'restart watcher')
    print(json.dumps(dict(publication=published, runner=watched), sort_keys=True))


def execute(args, pins):
    os.umask(0o077)
    need(sys.platform == 'linux' and os.sched_getaffinity(0) == {3}
        and os.getpriority(os.PRIO_PROCESS, 0) >= 19, 'run under taskset -c 3 nice -n 19')
    android, backend = args.android.resolve(), args.backend.resolve()
    deps = android / 'app/build/ftps-wire-dependencies/installed'
    sys.path[:0] = [str(deps), str(backend), str(backend / 'tests')]
    manifest = json.loads((android / 'app/src/testFixtures/ftps-wire/dependencies.json').read_text())
    for item in manifest['packages']:
        archive = deps.parent / 'archives' / item['filename']
        need(archive.stat().st_size == item['size'] and digest(archive) == item['sha256'], 'dependency archive integrity')
        need(importlib.metadata.version(item['name']) == item['version'], 'dependency version')
    need(sum(p.stat().st_size for p in deps.rglob('*') if p.is_file()) <= manifest['install_limit_bytes'], 'dependency installed bound')
    from audio_backend import recorder_receiver_publication as receiver
    from audio_backend import recorder_inbox_runner as runner
    from audio_backend import recording_uploader as uploader
    from audio_backend.recorder_delivery import inspect_delivery
    from audio_backend.recorder_metadata import decode
    from test_extended_recorder_intake import Peer

    work = args.output.resolve()
    need(not work.exists(), 'fresh output required')
    work.mkdir(mode=0o700)
    guard = Guard(work)
    keys = work / 'ephemeral-keys'
    keys.mkdir(mode=0o700)
    report = dict(schema='receiver-ftps-wire-qualification-v1', passed=False,
        model_free=True, native_inference_executed=False, android_device_executed=False,
        source_pins=pins, limits=dict(seconds=600, server_seconds=60, client_seconds=30,
        process_tree_rss=384*1024**2, output_bytes=8*1024**2, wire_bytes=2*1024**2,
        max_sessions=5, disk_floor=5*1024**3), dependencies=manifest,
        observed_runtime_versions={name:importlib.metadata.version(name) for name in
            ('cryptography', 'cffi', 'typing_extensions', 'setuptools', 'wheel', 'pyftpdlib', 'pyOpenSSL', 'pyasyncore', 'pyasynchat')})
    watch_thread = publish_thread = None
    cancel = threading.Event()
    release_scan = threading.Event()
    old_inventory, old_lock = receiver.inbox.inventory, receiver._lock
    observations, peer_calls = [], []
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
        java_sources = sorted((android/'app/src/testFixtures/ftps-wire').rglob('*.java'))
        java_sources += sorted((android/'app/src/main/java/io/github/headmaster218/recorder/core').glob('*.java'))
        java_sources += [android/'app/src/main/java/io/github/headmaster218/recorder/android/FtpsNetwork.java']
        source_list = work/'javac-sources.txt'
        source_list.write_text('\n'.join(str(p) for p in java_sources)+'\n')
        guard.invoke(java+['-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '8', '-target', '8',
            '-Xlint:all,-options', '-Werror', '-d', classes, '@'+str(source_list)], work/'compile.log', 60)
        case = work/'wire'
        case.mkdir(mode=0o700)
        env = dict(os.environ, PYTHONPATH=str(deps), PYTHONDONTWRITEBYTECODE='1')
        server = guard.spawn([sys.executable, '-B', HERE/'receiver_wire_server.py', case,
            keys/'server.pem', keys/'server.key'], work/'server.log', env)
        guard.until(lambda: (case/'ready.json').exists(), 'server ready', processes=[server])
        address = json.loads((case/'ready.json').read_text())
        need(address['host'] == '127.0.0.1', 'server loopback address')
        client_started = time.monotonic()
        client = guard.spawn(java+['-Djavax.net.ssl.trustStore='+str(keys/'trust.p12'),
            '-Djavax.net.ssl.trustStorePassword=synthetic', '-Djavax.net.ssl.trustStoreType=PKCS12',
            '-cp', classes, 'io.github.headmaster218.recorder.android.WireClient', 'success', case,
            str(address['port'])], work/'client.log')
        guard.until(lambda: (case/'marker-held.json').exists(), 'live partial marker', timeout=10, processes=[server, client])
        held = json.loads((case/'marker-held.json').read_text())
        raw = Path(held['path']).parent
        marker = raw/'delivery.v1.txt'
        need(0 < held['size'] <= 32 and not held['handle_closed'] and marker.stat().st_size == held['size'], 'partial marker observation')
        need(not (case/'completed-delivery.json').exists(), 'partial marker not completed')
        inbox = work/'inbox'
        inbox.mkdir(mode=0o700)
        receiver_state, runner_state, job = work/'receiver', work/'runner', work/'job'
        receiver.configure(state_dir=receiver_state, staging_dir=raw.parent, inbox_dir=inbox, exclusive_receiver=True)
        sources = list((case/'spool').glob('*.ready/manifest.bin'))
        need(len(sources) == 1, 'one producer source manifest')
        metadata = decode(sources[0].read_bytes())
        origin = {key: metadata[key] for key in ('epoch', 'sample_rate_hz', 'channels', 'requested_input',
            'actual_input', 'boundary', 'target_frames')}
        origin.update(capture_identity_sha256=hashlib.sha256(metadata['capture_id'].encode()).hexdigest(),
            run_identity_sha256=hashlib.sha256(metadata['run_id'].encode()).hexdigest(),
            first_sequence=metadata['sequence'], epoch_frame_start=metadata['frame_start'])
        config = dict(schema=runner.CONFIG_SCHEMA, inbox_dir=str(inbox), job_dir=str(job), output_dir=None,
            api='http://127.0.0.1:8765', session_id='known', capture_id='extended', span_origin=origin,
            max_hours=1, max_inputs=1, receiver_publication=receiver.bind(receiver_state))
        runner.configure(state_dir=runner_state, config=config)
        before = inventory([inbox, runner_state, job])
        hold_scan, scan_held, publication_lock_requested = threading.Event(), threading.Event(), threading.Event()
        watch_result, publication_result = {}, {}
        class ObservedPeer(Peer):
            def request(self, method, path, **kwargs):
                peer_calls.append(dict(method=method, path=path, monotonic=time.monotonic()))
                return super().request(method, path, **kwargs)
        peer = ObservedPeer()
        def observed_inventory(*a, **kw):
            if hold_scan.is_set() and not scan_held.is_set():
                scan_held.set()
                need(release_scan.wait(5), 'scan release deadline')
            result = old_inventory(*a, **kw)
            observations.append(dict(monotonic=time.monotonic(), entries=len(result['deliveries']), peer_calls=len(peer_calls)))
            return result
        @contextmanager
        def observed_lock(state, config, name, remaining, *, shared=False):
            if name == 'publication.lock' and not shared:
                publication_lock_requested.set()
            with old_lock(state, config, name, remaining, shared=shared):
                yield
        receiver.inbox.inventory, receiver._lock = observed_inventory, observed_lock
        def watching():
            try:
                watch_result['result'] = runner.watch(state_dir=runner_state, exclusive_writer=True,
                    acknowledge_span_local_closure=True, limits=uploader.Limits(disk_floor=0), transport=peer,
                    deadline_seconds=30, poll_seconds=.1, max_polls=256, max_actions=4,
                    max_requests=128, max_network_writes=64, max_network_bytes=8*1024**2,
                    cancel=cancel.is_set)
            except BaseException as error:
                watch_result['error'] = dict(type=type(error).__name__, message=str(error))
        watch_thread = threading.Thread(target=watching, name='production-runner-watch', daemon=True)
        watch_thread.start()
        guard.until(lambda: len(observations) >= 2, 'two empty polls', processes=[server, client])
        need(watch_thread.is_alive() and not watch_result and not peer_calls, 'held watch active without calls')
        need(inventory([inbox, runner_state, job]) == before, 'partial marker caused no published/intake effects')
        need(marker.stat().st_size == held['size'], 'marker remained partial across watch polls')
        report['partial_marker_hold'] = dict(observation=held, polls=list(observations),
            unchanged_inventory=True, peer_calls=0, same_watch_alive=True)
        (case/'release-marker').touch()
        guard.finish(client, max(.1, 30-(time.monotonic()-client_started)))
        receipt = json.loads((work/'client.log').read_text().splitlines()[-1])
        completion = json.loads((case/'completed-delivery.json').read_text())
        need(completion['handles_closed'] and receipt['status'] == 'verified' and receipt['local_unchanged']
            and not receipt['cleanup_invoked'] and receipt['all_raw_sockets_closed'], 'actual FtpsNetwork success')
        need(receipt['callback_registrations'] == receipt['callback_unregistrations'], 'callbacks released')
        need(receipt['source_hash_before'] == receipt['source_hash_after'], 'source preserved')
        need(watch_thread.is_alive() and not peer_calls and not list(inbox.iterdir()), 'completed STOR alone not published')
        original = {name:(raw/name).read_bytes() for name in FILES}
        expected = inspect_delivery(raw, validate_metadata=True)
        hold_scan.set()
        guard.until(scan_held.is_set, 'active inventory shared lock', processes=[server])
        def publishing():
            try:
                publication_result['result'] = receiver.publish_completed(state_dir=receiver_state,
                    delivery=raw, completed_delivery=True, deadline_seconds=10)
            except BaseException as error:
                publication_result['error'] = dict(type=type(error).__name__, message=str(error))
        publish_thread = threading.Thread(target=publishing, name='receiver-publication', daemon=True)
        publish_thread.start()
        guard.until(publication_lock_requested.is_set, 'publisher requested exclusive lock', processes=[server])
        time.sleep(.15)
        guard.check()
        need(publish_thread.is_alive() and not publication_result and watch_thread.is_alive()
            and not list(inbox.iterdir()) and not peer_calls, 'publisher waits for active inventory')
        report['coordination'] = dict(shared_inventory_held=True, publisher_exclusive_lock_wait_observed=True,
            published_before_inventory_release=False, same_watch_alive=True)
        release_scan.set()
        guard.until(lambda: not publish_thread.is_alive(), 'publish return', processes=[server])
        publish_thread.join()
        need('error' not in publication_result, 'publication error: '+str(publication_result))
        need(publication_result['result']['status'] == 'published', 'publication status')
        guard.until(lambda: not watch_thread.is_alive(), 'same watch admission', timeout=10, processes=[server])
        watch_thread.join()
        need('error' not in watch_result, 'watch error: '+str(watch_result))
        result = watch_result['result']
        need(result['status'] == 'awaiting_finalize' and result['inputs'] == 1 and result['actions'] == 1
            and not result['analysis_completion_verified'], 'final watcher outcome: '+str(result))
        target = inbox/raw.name
        need(inspect_delivery(target, validate_metadata=True) == expected, 'published exact semantic identity')
        need(all((target/n).read_bytes() == original[n] and (target/n).stat().st_ino != (raw/n).stat().st_ino for n in FILES), 'independent exact files')
        derived = runner.config_from_delivery(first_delivery=target, inbox_dir=inbox, job_dir=job,
            output_dir=None, api=config['api'], session_id='known', capture_id='extended',
            max_hours=1, max_inputs=1, receiver_state_dir=receiver_state)
        need(derived == config, 'manifest-derived preconfiguration exactly matches validated published delivery')
        frozen = inventory([inbox, runner_state, job])
        repeat = receiver.publish_completed(state_dir=receiver_state, delivery=raw, completed_delivery=True, deadline_seconds=10)
        need(repeat['status'] == 'already_published' and inventory([inbox, runner_state, job]) == frozen, 'repeat publication exact idempotence')
        tls = ssl.create_default_context(cafile=str(keys/'ca.pem'))
        def restorable_stor(files):
            with ftplib.FTP_TLS(context=tls, timeout=5) as ftp:
                ftp.connect('127.0.0.1', address['port'])
                ftp.login('synthetic', 'synthetic')
                ftp.prot_p()
                ftp.cwd('/fixture/'+raw.name)
                for name, data in files.items():
                    ftp.storbinary('STOR '+name, io.BytesIO(data), blocksize=1024)
                ftp.quit()
        restorable_stor({'audio.wav':original['audio.wav'][:128]})
        need((raw/'audio.wav').stat().st_size == 128 and inventory([inbox, runner_state, job]) == frozen, 'later truncated raw STOR isolated')
        try:
            receiver.publish_completed(state_dir=receiver_state, delivery=raw, completed_delivery=True, deadline_seconds=10)
            raise AssertionError('truncated re-publication accepted')
        except receiver.Refused as error:
            truncated_reason = str(error)
        need(inventory([inbox, runner_state, job]) == frozen, 'truncated re-publication preserved frozen input')
        # Same source/WAV/revision/attempt, valid changed producer run declaration.
        changed = original['segment.v1.bin'].replace(b'synthetic-wire-run', b'synthetic-wire-alt')
        need(changed != original['segment.v1.bin'] and len(changed) == len(original['segment.v1.bin']), 'conflict replacement length')
        offset = 4
        for _ in range(2):
            count = int.from_bytes(changed[offset:offset+2], 'big')
            offset += 2+count
        offset += 4
        local = changed[offset:]
        changed = changed[:offset]+local[:-32]+hashlib.sha256(local[:-32]).digest()
        marker_lines = original['delivery.v1.txt'].decode('ascii').split('\n')
        marker_lines[7] = hashlib.sha256(changed).hexdigest()
        changed_marker = '\n'.join(marker_lines).encode('ascii')
        restorable_stor({'audio.wav':original['audio.wav'], 'segment.v1.bin':changed, 'delivery.v1.txt':changed_marker})
        conflicting = inspect_delivery(raw, validate_metadata=True)
        need(conflicting['run_identity_sha256'] != expected['run_identity_sha256'], 'valid conflicting raw declaration')
        try:
            receiver.publish_completed(state_dir=receiver_state, delivery=raw, completed_delivery=True, deadline_seconds=10)
            raise AssertionError('conflicting re-publication accepted')
        except receiver.Refused as error:
            conflict_reason = str(error)
        need(conflict_reason == 'receiver_existing_attempt_conflict' and inventory([inbox, runner_state, job]) == frozen, 'valid conflict preserves selected original')
        restorable_stor(original)
        need(inspect_delivery(raw, validate_metadata=True) == expected, 'wire restoration exact')
        restart_env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        guard.invoke([sys.executable, '-B', Path(__file__).resolve(), '--restart', '--backend', backend,
            '--runner-state', runner_state, '--receiver-state', receiver_state, '--raw', raw], work/'restart.json', 10, restart_env)
        need(inventory([inbox, runner_state, job]) == frozen, 'fresh process restart leaves state unchanged')
        receiver.inbox.inventory, receiver._lock = old_inventory, old_lock
        collision_root = work/'collision'
        collision_root.mkdir(mode=0o700)
        for name in ('staging', 'inbox'):
            (collision_root/name).mkdir(mode=0o700)
        collision_source = collision_root/'staging'/raw.name
        shutil.copytree(raw, collision_source)
        receiver.configure(state_dir=collision_root/'state', staging_dir=collision_root/'staging',
            inbox_dir=collision_root/'inbox', exclusive_receiver=True)
        collision_target = collision_root/'inbox'/raw.name
        collision_target.mkdir(mode=0o700)
        collision_inode = collision_target.stat().st_ino
        try:
            receiver.publish_completed(state_dir=collision_root/'state', delivery=collision_source,
                completed_delivery=True, deadline_seconds=10)
            raise AssertionError('empty target overwritten')
        except receiver.Refused as error:
            collision_reason = str(error)
        need(collision_target.stat().st_ino == collision_inode and not list(collision_target.iterdir()), 'empty target remains exact')
        (case/'stop').touch()
        guard.finish(server, 3)
        facts = json.loads((case/'server-result.json').read_text())
        need(facts['all_closed'] and not facts['errors'] and facts['sessions'] <= 5
            and facts['data_bytes'] <= 2*1024**2, 'server bounds/cleanup')
        need(all(row['tls'] for row in facts['commands'] if row['cmd'] in ('USER', 'PASS')), 'protected credentials')
        need(all(row['cmd'] not in ('DELE', 'RMD', 'RNFR', 'RNTO') for row in facts['commands']), 'ordinary STOR only')
        need(len([row for row in facts['commands'] if row['cmd'] == 'STOR']) == 10, 'expected genuine STOR transcript')
        for port in facts['listeners']:
            with socket.socket() as probe:
                probe.settimeout(.2)
                need(probe.connect_ex(('127.0.0.1',port)) != 0, 'listener survived cleanup')
        need(source_pins(android, backend) == pins, 'source hashes changed during run')
        report.update(passed=True, client=receipt, completion=completion, publication=publication_result['result'],
            watch=result, model_free_peer_calls=peer_calls, inventory_observations=observations,
            duplicate=repeat['status'], truncated_restor_refusal=truncated_reason,
            valid_conflict_refusal=conflict_reason, empty_target_refusal=collision_reason,
            restart=json.loads((work/'restart.json').read_text()), frozen_inbox_and_runner_preserved=True,
            wire=dict(sessions=facts['sessions'], stor_commands=10, data_bytes=facts['data_bytes'],
                control_tls=facts['control_tls'], data_tls=facts['data_tls'], all_closed=True,
                all_recorded_listeners_closed=True), raw_server_report_sha256=digest(case/'server-result.json'))
    except BaseException as error:
        report['failure'] = dict(type=type(error).__name__, message=str(error))
        raise
    finally:
        cancel.set()
        release_scan.set()
        if watch_thread is not None:
            watch_thread.join(timeout=2)
        if publish_thread is not None:
            publish_thread.join(timeout=2)
        receiver.inbox.inventory, receiver._lock = old_inventory, old_lock
        report['owned_processes'] = guard.cleanup()
        ports = set()
        ready_path = work/'wire/ready.json'
        facts_path = work/'wire/server-result.json'
        if ready_path.exists():
            ports.add(json.loads(ready_path.read_text())['port'])
        if facts_path.exists():
            ports.update(json.loads(facts_path.read_text())['listeners'])
        listener_checks = []
        for port in sorted(ports):
            with socket.socket() as probe:
                probe.settimeout(.2)
                listener_checks.append(dict(port=port, closed=probe.connect_ex(('127.0.0.1', port)) != 0))
        report['cleanup_listener_checks'] = listener_checks
        report['watch_thread_closed'] = watch_thread is None or not watch_thread.is_alive()
        report['publisher_thread_closed'] = publish_thread is None or not publish_thread.is_alive()
        if not all(row['closed'] for row in listener_checks) or not report['watch_thread_closed'] or not report['publisher_thread_closed']:
            report['passed'] = False
            report['cleanup_failure'] = 'listener or thread did not stop within its cleanup bound'
        shutil.rmtree(keys)
        report['ephemeral_keys_removed'] = not keys.exists()
        report['peak_process_tree_rss'] = guard.peak
        report['seconds'] = round(time.monotonic()-guard.started, 3)
        try:
            report['source_hashes_unchanged_at_exit'] = source_pins(android, backend) == pins
        except BaseException as error:
            report['source_hashes_unchanged_at_exit'] = False
            report['source_hash_check_error'] = type(error).__name__
        if not report['source_hashes_unchanged_at_exit']:
            report['passed'] = False
        for sig, previous in old_signals.items():
            signal.signal(sig, previous)
        save(work/'report.json', report)
        print('REPORT '+str(work/'report.json'), flush=True)
    need(report['passed'], 'qualification or cleanup failed; preserve report')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android', type=Path)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--pins', type=Path)
    parser.add_argument('--record-pins', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--execute-reviewed-fixture', action='store_true')
    parser.add_argument('--restart', action='store_true')
    for name in ('runner-state', 'receiver-state', 'raw'):
        parser.add_argument('--'+name, type=Path)
    args = parser.parse_args()
    if args.restart:
        restart(args.backend.resolve(), args.runner_state, args.receiver_state, args.raw)
        return
    need(args.android is not None, 'android path required')
    pins = source_pins(args.android.resolve(), args.backend.resolve())
    if args.record_pins:
        need(not args.record_pins.exists(), 'new pins destination required')
        save(args.record_pins, pins)
        print('PINS '+str(args.record_pins))
        return
    need(args.execute_reviewed_fixture and args.pins and args.output, 'reviewed flag, pins, output required')
    need(json.loads(args.pins.read_text()) == pins, 'reviewed exact pins required')
    execute(args, pins)


if __name__ == '__main__':
    main()
