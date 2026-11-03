#!/usr/bin/env python3
"""Read-only audit of an existing callback run; never launches or imports services.

The executed backend is checked against its immutable restore manifest and the
recorded pins, not against the subsequently edited working checkout. Outputs are
sanitized counts/hashes only. Original evidence is never changed or replaced.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

FILES = ('audio.wav', 'segment.v1.bin', 'delivery.v1.txt')


def need(value, reason):
    if not value:
        raise AssertionError(reason)


def read(path):
    path = Path(path)
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode), 'audit requires regular files')
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if info.st_uid == os.geteuid():
        flags |= getattr(os, 'O_NOATIME', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        return stream.read()


def digest(path):
    return hashlib.sha256(read(path)).hexdigest()


def load(path):
    return json.loads(read(path))


def verify_hashes(files):
    for name, expected in files.items():
        need(digest(name) == expected, 'pinned file hash changed')
    return len(files)


def verify_restore(source, manifest):
    actual = {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()}
    need(actual == set(manifest['files']), 'restored source file set changed')
    total = 0
    for name, row in manifest['files'].items():
        raw = read(source/name)
        total += len(raw)
        need(len(raw) == row['bytes'] and hashlib.sha256(raw).hexdigest() == row['sha256']
             and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == row['git_blob'],
             'restored source blob changed')
    need(total == manifest['bytes'] and len(actual) == manifest['file_count'], 'restored source totals')
    return len(actual), total


def verify_copies(raw, published, selected):
    need({p.name for p in raw.iterdir()} == set(FILES), 'raw delivery members')
    need({p.name for p in published.iterdir()} == set(FILES), 'published delivery members')
    facts = {}
    for name in FILES:
        paths = [root/name for root in (raw, published, selected)]
        data = [read(p) for p in paths]
        need(data[0] == data[1] == data[2], 'selected snapshot bytes differ')
        infos = [p.stat() for p in paths]
        need(len({(s.st_dev, s.st_ino) for s in infos}) == 3, 'copies share inode')
        need(all(s.st_nlink == 1 for s in infos), 'copies share hard links')
        need(all(s.st_mode & 0o077 == 0 for s in infos[1:]), 'owned copies not private')
        facts[name] = dict(bytes=len(data[0]), sha256=hashlib.sha256(data[0]).hexdigest())
    marker = read(raw/'delivery.v1.txt').decode().splitlines()
    need(len(marker) == 8 and marker[0] == 'RECORDER-DELIVERY-V1'
         and marker[1] == raw.name and int(marker[4]) == facts['audio.wav']['bytes']
         and marker[5] == facts['audio.wav']['sha256']
         and int(marker[6]) == facts['segment.v1.bin']['bytes']
         and marker[7] == facts['segment.v1.bin']['sha256'], 'marker transport binding')
    return facts


def audit(run, pins_path, restored_path, expected_report_sha256, expected_restore_sha256):
    run = Path(run).resolve()
    need(digest(run/'report.json') == expected_report_sha256, 'unexpected run report hash')
    need(digest(restored_path) == expected_restore_sha256, 'unexpected restore manifest hash')
    report = load(run/'report.json')
    need(report['passed'] and report['model_free'] and not report['native_inference_executed']
         and not report['android_device_executed'] and not report['manual_publication'], 'qualification scope')
    pins, restored = load(pins_path), load(restored_path)
    need(report['source_pins'] == pins and report['source_hashes_unchanged_at_exit'], 'run pins differ')
    pin_count = verify_hashes(pins['files'])
    root = Path(restored_path).parent
    file_count, source_bytes = verify_restore(root/'backend-source', restored)
    preflight = report['preflight']
    need(preflight['python'] == '3.12.14' and not any(preflight[key] for key in
         ('keys_created', 'network_connections_started', 'server_started')), 'preflight scope')
    for module in preflight['modules'].values():
        need(digest(module['file']) == module['sha256'], 'imported module changed')
    tools = root/'existing-test-tools.sha256'
    count = total = 0
    for row in read(tools).decode().splitlines():
        checksum, name = row.split('  ', 1)
        need(digest(name) == checksum, 'test tools changed')
        count += 1; total += Path(name).stat().st_size
    need((count, total) == (pins['test_tool_files'], pins['test_tool_bytes']), 'test-tool totals')

    server = load(run/'wire/server-result.json')
    need(digest(run/'wire/server-result.json') == report['raw_server_report_sha256'], 'server report hash')
    callbacks, opens = server['callbacks'], server['opens']
    need(callbacks == report['callback_facts'] and len(callbacks) == len(opens) == 6,
         'callback/open correspondence')
    need(len({row['path'] for row in opens}) == 6, 'duplicate accepted writer')
    for opened, closed in zip(opens, callbacks):
        need(opened['path'] == closed['path'] and opened['monotonic'] < closed['monotonic']
             and closed['handle_closed'] and closed['live_writers'] == 0, 'actual close ordering')
        file = Path(closed['path'])
        need(file.is_relative_to(run) and file.stat().st_size == closed['bytes']
             and digest(file) == closed['sha256'], 'callback bytes changed')
    need([c['successful'] for c in callbacks] == [True]*5+[False], 'actual transfer outcomes')
    success, failure = callbacks[2], callbacks[5]
    need(success == load(run/'wire/completed-delivery.json') == report['completion'], 'success evidence differs')
    need(failure == load(run/'wire/failed-delivery.json') == report['incomplete_callback'], 'failure evidence differs')
    need(success['result']['status'] == 'published' and success['result']['pending_writers'] == 0
         and success['result']['successful_files'] == sorted(FILES)
         and success['duplicate_result'] == success['result'], 'final successful closure')
    need(failure['result']['status'] == 'failed' and failure['result']['reason'] == 'completion_transfer_failed'
         and failure['duplicate_result'] == failure['result'] and failure['bytes'] == 32,
         'incomplete transfer publication')
    held = report['partial_marker_hold']
    need(held['observation'] == load(run/'wire/marker-held.json') and held['observation']['size'] == 32
         and not held['observation']['handle_closed'] and held['observation']['live_writers'] == 1
         and len(held['observation']['callbacks']) == 2 and held['unchanged'] and held['peer_calls'] == 0,
         'held marker evidence')
    need(len(held['polls']) >= 2 and all(row['entries'] == row['peer_calls'] == 0 and
         row['monotonic'] < success['monotonic'] for row in held['polls']), 'watch admitted partial upload')
    need(report['peer_calls'] and all(row['monotonic'] > success['monotonic'] and row['method'] == 'GET'
         for row in report['peer_calls']), 'peer work before completion or unexpected network write')

    raw = Path(success['path']).parent
    published = run/'wire/inbox'/raw.name
    selected = run/'job/admissions/0/snapshot'
    facts = verify_copies(raw, published, selected)
    need(list((run/'wire/inbox').iterdir()) == [published], 'unexpected inbox publication')
    need({p.name for p in (run/'job/admissions').iterdir()} == {'0'}, 'unexpected admission')
    need(not list((run/'wire/receiver/prepare').iterdir()), 'unexpected retained preparation')
    intent = load(run/'runner/input-000.intent.json')
    admission = load(run/'job/admissions/0/admission.json')
    need(intent['path'] == admission['path'] == str(published) and intent['ordinal'] == admission['ordinal'] == 0
         and intent['delivery'] == admission['delivery'] == success['result']['publication']['delivery'],
         'original admission path changed')
    need(load(run/'runner/input-000.done.json')['intent_sha256'] == digest(run/'runner/input-000.intent.json'),
         'runner intent completion binding')
    need(load(selected/'snapshot.json')['body']['files'] == facts, 'selected snapshot manifest differs')
    journal = load(run/'job/uploader/uploader.json')
    need(journal['input_count'] == journal['inputs_done'] == 1 and journal['completed_frames'] == 8192
         and journal['published_frames'] == 0 and not journal['finalize_requested'] and not journal['complete'],
         'unintended finalize or analysis claim')
    need(report['watch']['status'] == 'awaiting_finalize' and report['watch']['inputs'] == report['watch']['actions'] == 1
         and not report['watch']['analysis_completion_verified'], 'watch outcome')

    for name, key in [('client-success.log', 'client'), ('client-failure.log', 'failed_client')]:
        receipt = json.loads(read(run/name).decode().splitlines()[-1])
        need(receipt == report[key] and receipt['all_raw_sockets_closed'] and receipt['local_unchanged']
             and not receipt['cleanup_invoked'] and receipt['callback_registrations'] == receipt['callback_unregistrations'],
             'Java client receipt')
    need(report['client']['status'] == 'verified' and report['failed_client']['status'] == 'expected_failure'
         and report['failed_client']['reconcile_only'] and report['failed_client']['reconcile_failed'], 'Java reconciliation')
    commands = server['commands']
    need(sum(row['cmd'] == 'STOR' for row in commands) == 7 and server['sessions'] == 4
         and all(row['tls'] for row in commands if row['cmd'] != 'AUTH'), 'TLS command scope')
    need(not any(row['cmd'] in ('STOR', 'MKD', 'DELE', 'RMD', 'RNFR', 'RNTO') for row in commands if row['session'] == 4),
         'reconciliation performed writes')
    refusal = server['refusals']
    need(len(refusal) == 1 and refusal[0]['reason'] == 'completion_attempt_not_receiving'
         and refusal[0]['path'] == str(raw/'audio.wav') and refusal[0]['size'] == facts['audio.wav']['bytes'],
         'duplicate STOR not rejected before truncation')
    for name, row in server['remote_files'].items():
        file = run/'wire/remote'/name
        need(file.stat().st_size == row['size'] and digest(file) == row['sha256'], 'raw files changed after server exit')
    crash, restart = load(run/'crash-open.json'), load(run/'restart.json')
    need(crash == report['crash_open'] and restart == report['restart'] and crash['marker_handle_open']
         and not crash['marker_callback_invoked'] and crash['exit_without_adapter_close'], 'crash evidence')
    crash_raw = Path(crash['path'])
    need(read(crash_raw/'audio.wav') == read(raw/'audio.wav')
         and read(crash_raw/'segment.v1.bin') == read(raw/'segment.v1.bin')
         and read(crash_raw/'delivery.v1.txt') == read(raw/'delivery.v1.txt').replace(raw.name.encode(), crash_raw.name.encode()),
         'crash complete-looking bytes differ')
    expected_attempts = {raw.name, Path(failure['path']).parent.name, crash_raw.name}
    need({r['attempt'] for r in restart['refusals']} == expected_attempts and len(restart['refusals']) == 3
         and all(r['reason'] == 'completion_existing_attempt_not_adopted' for r in restart['refusals'])
         and restart['unchanged'] and restart['runner']['actions'] == restart['runner']['network_requests'] == 0,
         'restart adopted, republished or replayed')
    need({p.name for p in (run/'wire/remote/fixture').iterdir()} == expected_attempts, 'unexpected raw attempt')
    need(server['all_closed'] and server['live_writers_at_exit'] == 0 and not server['errors']
         and report['watch_thread_closed'] and report['ephemeral_keys_removed']
         and not (run/'ephemeral-keys').exists(), 'cleanup failure')
    checks = report['cleanup_listener_checks']
    need({c['port'] for c in checks} == set(server['listeners']) and all(c['closed'] for c in checks), 'listener cleanup records')
    need(all(p['reaped'] and p['returncode'] == 0 for p in report['owned_processes']), 'subprocess cleanup')
    need(report['seconds'] <= 600 and report['peak_process_tree_rss'] <= 384*1024**2
         and server['data_bytes'] == report['wire']['data_bytes'] <= 2*1024**2
         and sum(p.stat().st_size for p in run.rglob('*') if p.is_file()) <= 8*1024**2, 'resource bounds')
    return dict(schema='receiver-callback-independent-audit-v1', passed=True,
        run_report_sha256=expected_report_sha256, reviewed_pins_sha256=digest(pins_path),
        restored_manifest_sha256=expected_restore_sha256,
        executed_checkpoint=restored['commit'], restored_files=file_count, restored_bytes=source_bytes,
        pinned_files=pin_count, imported_modules=len(preflight['modules']), test_tool_files=count,
        test_tool_bytes=total, callbacks=6, successful_callbacks=5, incomplete_callbacks=1,
        held_empty_polls=len(held['polls']), selected_inputs=1, selected_frames=8192,
        bytes_exact_independent_copies=True, successful_duplicate_cached=True,
        failed_duplicate_cached=True, blocked_duplicate_stor=True, restart_refusals=3,
        restart_actions=0, restart_requests=0, listener_checks=len(checks),
        reaped_subprocesses=len(report['owned_processes']), keys_removed=True,
        seconds=report['seconds'], peak_rss_bytes=report['peak_process_tree_rss'],
        wire_bytes=server['data_bytes'], model_free=True, native_inference=False,
        device_qualification=False, deployed_receiver=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('run', 'pins', 'restored-manifest', 'output'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--report-sha256', required=True)
    parser.add_argument('--restored-sha256', required=True)
    args = parser.parse_args()
    result = audit(args.run, args.pins, args.restored_manifest, args.report_sha256, args.restored_sha256)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(result, stream, sort_keys=True, indent=2); stream.write('\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
