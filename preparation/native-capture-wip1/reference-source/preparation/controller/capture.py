"""One fixed real capture via actual loopback HTTP; restarts only after proof of reaping."""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path
import stat
import socket
import threading
import subprocess
import time
from urllib.parse import urlencode
from common import (FRAMES, MIB, PCM_SHA, WAV_SHA, FLOAT_SHA, require, bounded_read,
                    atomic_json, fixed_wav, sha, canonical)


class Client:
    def __init__(self, port, token, deadlines, journal):
        self.port, self.token, self.deadlines, self.journal = port, token, deadlines, journal
        self.calls = 0
        self.response_bytes = 0
        self.polls = 0

    def request(self, method, path, *, body=None, json_body=None, auth=True, expected=200, private=False):
        self.deadlines.check()
        require(self.calls < 256 and path.startswith('/') and len(path) <= 4096, 'HTTP_request_cap')
        self.calls += 1
        headers = {'Connection': 'close'}
        if auth:
            headers['Authorization'] = 'Bearer '+self.token
        if json_body is not None:
            body = canonical(json_body)
            headers['Content-Type'] = 'application/json'
        if method == 'PUT':
            headers.update({'Idempotency-Key': 'fixed-one-original-input', 'Content-Type': 'audio/wav'})
        require(body is None or isinstance(body, bytes) and len(body) <= MIB, 'HTTP_request_body_cap')
        end = self.deadlines.call_end(10)
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=max(.01, end-time.monotonic()))
        def abort():
            try:
                if connection.sock is not None:
                    connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                connection.close()
        timer = threading.Timer(max(.001, end-time.monotonic()), abort)
        timer.daemon = True
        timer.start()
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            require(response.length is None or response.length <= 8*MIB, 'HTTP_declared_body_cap')
            data = bytearray()
            while True:
                self.deadlines.check(end)
                if connection.sock is not None:
                    connection.sock.settimeout(max(.001, end-time.monotonic()))
                part = response.read(min(65536, 8*MIB + 1-len(data)))
                if not part:
                    break
                data.extend(part)
                require(len(data) <= 8*MIB, 'HTTP_body_cap')
            self.deadlines.check(end)
            self.response_bytes += len(data)
            require(self.response_bytes <= 64*MIB, 'HTTP_aggregate_response_cap')
            headers = {k.lower(): v for k, v in response.getheaders()}
            self.journal.emit('HTTP_response', method=method, path=path, status=response.status,
                              bytes=len(data), sha256=sha(data), headers=headers)
            require(response.status == expected, f'HTTP_expected_{expected}_got_{response.status}')
            if private:
                control = headers.get('cache-control', '').lower()
                require('private' in control and 'no-store' in control, 'missing_private_no_store')
            return bytes(data), headers
        finally:
            timer.cancel()
            connection.close()  # Every body is drained before optional reads/deletion.

    def json(self, *args, **kwargs):
        return json.loads(self.request(*args, **kwargs)[0])


def verify_inventory(spec, deadlines, *, end):
    """Full immutable source/runtime/model/input hash, bounded and no-follow.

    Parent supplies the exact closed inventory after installation/acquisition. No
    wildcard pin generation or accepting the current bytes occurs during execution.
    """
    items = spec['hash_inventory']
    require(type(items) is list and 1 <= len(items) <= 32768, 'full_hash_inventory_required')
    total, seen = 0, set()
    began = time.monotonic()
    for item in items:
        require(time.monotonic() < end, 'full_hash_deadline')
        require(not deadlines.stop.exists(), 'outer_STOP')
        path = Path(item['path'])
        require(str(path) not in seen, 'duplicate_full_hash_path')
        seen.add(str(path))
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_size == item['bytes'], 'full_hash_file_shape')
            digest, count = hashlib.sha256(), 0
            while block := os.read(fd, 65536):
                require(time.monotonic() < end, 'full_hash_deadline')
                require(not deadlines.stop.exists(), 'outer_STOP')
                count += len(block)
                require(count <= item['bytes'], 'full_hash_growth')
                digest.update(block)
            after = os.fstat(fd)
            signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            require(signature(before) == signature(after) == signature(path.stat(follow_symlinks=False)),
                    'full_hash_mutation')
            require(count == item['bytes'] and digest.hexdigest() == item['sha256'], 'full_hash_pin')
            total += count
        finally:
            os.close(fd)
    required = set(spec['installed_files']) | set(spec['runtime_files']) | set(spec['harness_files'])
    require(required <= seen, 'full_hash_missing_execution_files')
    require(str(Path(spec['input_wav'])) in seen, 'full_hash_missing_input')
    source = Path(spec['source_root'])
    actual_source = set()
    for root, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [d for d in dirs if Path(root) != source or d != '.git']
        require(not any((Path(root)/d).is_symlink() for d in dirs), 'source_directory_alias')
        for name in files:
            actual_source.add(str(Path(root)/name))
            require(len(actual_source) <= 311, 'source_file_count_grew')
    require(len(actual_source) == 311 and actual_source == set(spec['source_files']) and actual_source <= seen,
            'qualified_source_inventory_changed')
    for alias in spec.get('runtime_aliases', []):
        path = Path(alias['path'])
        require(path.is_symlink() and os.readlink(path) == alias['literal_target'] and
                str(path.resolve()) == alias['resolved_target'] and str(path.resolve()) in seen, 'runtime_alias_changed')
    require(time.monotonic() < end and not deadlines.stop.exists(), 'full_hash_final_deadline')
    return {'files': len(items), 'bytes': total, 'elapsed_seconds': time.monotonic()-began,
            'all_matched': True, 'source_files': 311}


class Server:
    def __init__(self, spec, deadlines, journal, ledger, mode):
        self.spec, self.deadlines, self.journal, self.ledger = spec, deadlines, journal, ledger
        self.mode = mode
        self.dir = Path(spec['output_dir']) / mode
        self.dir.mkdir()
        self.sequence = 0
        self.log = (self.dir/'server.log').open('xb')
        self.log_error = None
        self.proc = ledger.spawn([spec['python'], '-I', '-B', '-S', str(Path(spec['harness_root'])/'bootstrap.py'),
                                 '--mode', 'server', '--spec', spec['_path'], '--', mode, str(self.dir)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                cwd=spec['output_dir'], env=dict(os.environ))
        def drain_log():
            written = 0
            try:
                while True:
                    block = self.proc.stdout.read(4096)
                    if not block:break
                    require(written+len(block) <= 2*MIB, 'server_log_2MiB_cap')
                    self.log.write(block);written += len(block)
            except BaseException as exc:
                self.log_error = type(exc).__name__+': '+str(exc)[:200]
                try:ledger.stop_original(self.proc)
                except BaseException:pass
            finally:
                self.proc.stdout.close();self.log.close()
        self.log_thread = threading.Thread(target=drain_log, name='bounded-server-log', daemon=True)
        self.log_thread.start()
        ready = self.wait_json(self.dir/'ready.json', deadlines.call_end(30))
        require(ready['pid'] == self.proc.pid and ready['mode'] == mode, 'HTTP_server_identity')
        self.ready = ready
        self.client = Client(ready['port'], spec['settings']['api_key'], deadlines, journal)

    def wait_json(self, path, end):
        while not path.exists():
            self.deadlines.check(end)
            require(self.log_error is None, 'server_log_error')
            require(self.proc.poll() is None, 'server_exited_before_receipt')
            self.ledger.census()
            time.sleep(min(.05, max(0., end-time.monotonic())))
        return json.loads(bounded_read(path, 2*MIB, lambda: self.deadlines.check(end)))

    def command(self, action):
        require(self.sequence < 20, 'private_command_cap')
        index = self.sequence
        self.sequence += 1
        atomic_json(self.dir/f'{index:03}.command.json', {'action': action})
        return self.wait_json(self.dir/f'{index:03}.response.json', self.deadlines.call_end(20))

    def stop_and_prove(self):
        self.command('stop')
        end = self.deadlines.call_end(35)
        while self.proc.poll() is None:
            self.deadlines.check(end)
            self.ledger.census()
            time.sleep(.05)
        code = self.proc.wait(timeout=0)  # Positive original handle wait, never process absence alone.
        self.log_thread.join(timeout=1)
        require(not self.log_thread.is_alive() and self.log_error is None, 'server_log_drain_incomplete')
        require(code == 0, 'server_nonzero_exit')
        receipt = json.loads(bounded_read(Path(self.spec['output_dir'])/f'server-{self.proc.pid}-cleanup.json',
                                          MIB, self.deadlines.check))
        require(receipt['cleanup'] == 'CONFIRMED' and not receipt['remaining_owned'] and
                all(row['original_waited'] for row in receipt['originals']), 'provider_original_waits_unconfirmed')
        # Every owned Python provider/guard must have its own original-child cleanup receipt.
        for original in receipt['originals']:
            matches = list(Path(self.spec['output_dir']).glob(f'*-{original["pid"]}-cleanup.json'))
            require(len(matches) == 1, 'missing_child_original_handle_receipt')
            child = json.loads(bounded_read(matches[0], MIB, self.deadlines.check))
            require(child['cleanup'] == 'CONFIRMED' and not child['remaining_owned'] and
                    all(row['original_waited'] for row in child['originals']), 'nested_original_wait_unconfirmed')
        for _ in range(2):
            require(not self.ledger.census(), 'owned_descendants_remain_before_restart')
            time.sleep(.05)
        final = json.loads(bounded_read(self.dir/'server-final.json', 2*MIB, self.deadlines.check))
        require(final['control_stopped'] and final['worker_stopped'] and not final['control_errors'],
                'server_thread_cleanup_incomplete')
        self.journal.emit('restart_barrier_passed', server_pid=self.proc.pid, original_wait=code)
        return final


def no_new_calls(value):
    require(all(row['entered'] == 0 and row['rejected'] == 0 for group in value['counts'].values()
                if type(group) is dict for row in group.values() if type(row) is dict and 'entered' in row),
            'unexpected_restart_provider_call')
    if 'launches' in value:
        require(not any(value['launches'].values()), 'unexpected_restart_provider_launch')


def assert_primary_equal(first, second):
    require(first['snapshot']['bytes'] == second['snapshot']['bytes'] and
            first['snapshot']['sha256'] == second['snapshot']['sha256'], 'optional_reads_changed_primary')


def reconstruct_capture(spec, upload, detail, deadlines):
    """Independent model-free replay using retained actual wire/material observations."""
    from audio_backend.native_owned_support import SourceMaterial, NativeWitness
    output = Path(spec['output_dir'])
    recorded = json.loads(bounded_read(output/'witness-before-finalize.json', MIB, deadlines.check))
    private = json.loads(bounded_read(output/'private-queued-source.json', MIB, deadlines.check))
    require(private['id'] == upload['chunk']['id'] and private['session_id'] == upload['chunk']['session_id'] and
            private['digest'] == WAV_SHA and private['duration'] == FRAMES/16000 and
            private['next_offset'] == 0., 'private_public_source_binding')
    source = SourceMaterial.inspect(private, check=deadlines.check)
    witness = NativeWitness(source)
    reset = json.loads(bounded_read(output/'native-1-reset.raw', 8*MIB, deadlines.check))
    push = json.loads(bounded_read(output/'native-2-push.raw', 8*MIB, deadlines.check))
    finish = json.loads(bounded_read(output/'native-3-finish.raw', 8*MIB, deadlines.check))
    turns = json.loads(bounded_read(output/'native-4-turns.raw', 8*MIB, deadlines.check))
    witness.observe(reset)
    witness.observe_pcm('native', output/'native-submitted.wav', 0., FRAMES/16000)
    witness.observe(push)
    witness.observe(finish)
    witness.observe(turns)
    witness.observe_pcm('analyzer', output/'analyzer-submitted.wav', 0., FRAMES/16000)
    reconstructed = witness.finalize(recorded['native'], recorded['offset'], recorded['end'])
    proof = bounded_read(output/'captured-proof.json', MIB, deadlines.check)
    pcm = bounded_read(output/'captured.pcm16le', MIB, deadlines.check)
    require(reconstructed.selected and reconstructed.proof == proof and reconstructed.pcm16le == pcm,
            'independent_source_core_witness_reconstruction_mismatch')
    support = reconstructed.support.to_dict()
    require(detail['support'] == support and sha(pcm) == PCM_SHA, 'public_support_proof_mismatch')
    return {'exact_source_core_witness_reconstruction': True, 'proof_sha256': sha(proof),
            'source_generation': source.source_generation, 'plan_sha256': source.plan_sha256,
            'support': support}


def verify_provider_counts(spec, deadlines):
    output = Path(spec['output_dir'])
    native = list(output.glob('native-*-counts.json'))
    analyzer = list(output.glob('analyzer-*-counts.json'))
    require(len(native) == len(analyzer) == 1, 'exact_provider_counter_files_required')
    n = json.loads(bounded_read(native[0], MIB, deadlines.check))
    a = json.loads(bounded_read(analyzer[0], MIB, deadlines.check))
    required = {'native_model_create': 1, 'native_stream_open': 1, 'native_push': 10,
                'native_finish': 1, 'native_segments': 2}
    for name, count in required.items():
        row = n['abi'][name]
        require(row['entered'] == row['returned'] == row['accepted'] == count and
                row['rejected'] == row['exceptions'] == 0, 'native_ABI_terminal_counts')
    require(n['native_frames'] == FRAMES and n['native_float32_sha256'] == FLOAT_SHA, 'native_ABI_input_pin')
    maximums = {'event_compute': 7, 'embedding_compute': 8, 'sensevoice_decode': 32,
                'event_constructor': 1, 'embedding_constructor': 1, 'sensevoice_constructor': 1}
    for name, cap in maximums.items():
        row = a['sdk'][name]
        require(row['entered'] == row['returned'] == row['accepted'] <= cap and
                row['rejected'] == row['exceptions'] == 0, 'SDK_terminal_counts')
    require(a['lifecycle']['analyzer_constructor']['accepted'] == 1, 'one_analyzer_constructor_required')
    require(a['sdk']['sensevoice_constructor']['accepted'] == 1 and
            a['sdk']['sensevoice_decode']['accepted'] >= 1, 'actual_SenseVoice_execution_required')
    total = sum(a['sdk'][name]['entered'] for name in ('event_compute','embedding_compute','sensevoice_decode'))
    require(total <= 47, 'SDK_total_cap')
    return {'native': n, 'analyzer': a, 'Sherpa_inference_entries': total,
            'hidden_native_model_forward_counts': 'unknown_not_exposed'}


def run_controller(spec, deadlines, journal, ledger):
    require(deadlines.work-time.monotonic() >= 600, 'fixed_600s_work_allowance_missing')
    output = Path(spec['output_dir'])
    require(not (output/'capture').exists() and not Path(spec['data_dir']).exists(), 'fresh_one_attempt_required')
    require(type(spec['settings']['api_key']) is str and 16 <= len(spec['settings']['api_key']) <= 128,
            'fixed_harness_bearer_required')
    prehash = verify_inventory(spec, deadlines, end=deadlines.work)
    atomic_json(output/'prehash.json', prehash)
    proof, wav, pcm = fixed_wav(spec['input_wav'], deadlines.check, encoded_pin=True)
    journal.emit('fixed_input_verified', **proof)
    server = Server(spec, deadlines, journal, ledger, 'capture')
    client = server.client
    require(not server.ready['cache'].get('entries') and server.ready['cache'].get('present'), 'fresh_enabled_cache')
    no_new_calls(server.ready)
    session = client.json('POST', '/sessions', json_body={'name': 'Fixed real native capture',
                                                       'processing_mode': 'legacy'}, expected=201)
    sid = session['id']
    upload = client.json('PUT', f'/sessions/{sid}/chunks?timeline_start=0', body=wav, expected=202)
    cid = upload['chunk']['id']
    require(upload['duplicate'] is False and upload['chunk']['status'] == 'queued' and
            upload['chunk']['duration'] == FRAMES/16000, 'original_upload_not_queued')
    atomic_json(output/'source-upload.json', {'session': session, 'upload': upload, 'input': proof}, 2*MIB)
    primary_started = time.monotonic()
    require(deadlines.work-primary_started >= 600, 'fixed_600s_allowance_after_prehash_upload_missing')
    primary = {'created_at': primary_started, 'deadline': primary_started+600, 'spec_sha256': spec['_sha256']}
    atomic_json(output/'primary-window.json', primary)
    deadlines.bind_primary(primary, spec['_sha256'])
    server.command('start-worker')
    completed = None
    for index in range(180):
        deadlines.check()
        row = client.json('GET', f'/chunks/{cid}')
        if row['status'] in ('completed', 'failed', 'cancelled', 'paused'):
            completed = row
            break
        time.sleep(min(2., max(0., deadlines.work-time.monotonic())))
    require(completed is not None, 'bounded_chunk_poll_exhausted')
    atomic_json(output/'terminal-chunk.json', completed, 2*MIB)
    require(completed['status'] == 'completed' and completed['next_offset'] == FRAMES/16000,
            'actual_primary_incomplete')
    # Worker completion and optional publish happen immediately after commit; wait only for that return.
    for _ in range(20):
        health = client.json('GET', '/health')
        if health['active_chunk'] is None:
            break
        time.sleep(.05)
    require(health['active_chunk'] is None, 'worker_return_not_observed')
    before = server.command('snapshot')
    base = f'/sessions/{sid}/native-owned-supports'
    page = client.json('GET', base, private=True)
    atomic_json(output/'public-list.json', page, MIB)
    require(page['lexical_dispatch_available'] is False and len(page['supports']) == 1, 'one_native_support_required')
    row = page['supports'][0]
    require(row['chunk_id'] == cid and row['selected'] is True and row['source_frames'] == FRAMES,
            'actual_native_overlap_not_selected')
    query = urlencode({'expected_capture_epoch': page['capture_epoch'], 'expected_revision': row['revision']})
    detail = client.json('GET', f'{base}/{cid}?{query}', private=True)
    atomic_json(output/'public-detail.json', detail, MIB)
    reconstruction = reconstruct_capture(spec, upload, detail, deadlines)
    atomic_json(output/'independent-reconstruction.json', reconstruction, MIB)
    audio, _ = client.request('GET', f'{base}/{cid}/audio?{query}', private=True)
    (output/'public-audio.wav').write_bytes(audio)
    require(audio == wav, 'public_audio_not_byte_exact_fixed_WAV')
    assert_primary_equal(before, server.command('snapshot'))
    client.request('GET', base, auth=False, expected=401, private=True)
    client.request('GET', f'{base}/{cid}?expected_revision={row["revision"]+1}', expected=409, private=True)
    server.command('flag-off')
    client.request('GET', f'{base}/{cid}', expected=409, private=True)
    server.command('flag-on')
    require(client.json('GET', f'{base}/{cid}?{query}', private=True) == detail, 'restored_live_support_changed')
    assert_primary_equal(before, server.command('snapshot'))
    live = server.command('cache')
    require(live['cache']['entries'] == 1 and live['cache']['lease_count'] == 0 and
            live['cache']['lease_bytes'] == 0, 'live_cache_or_drained_leases_unproved')
    deleted = client.json('DELETE', f'/sessions/{sid}')
    require(deleted.get('native_owned_support_cleanup_pending') is False, 'truthful_live_delete_cleanup_required')
    client.request('GET', f'{base}/{cid}', expected=404, private=True)
    erased = server.command('cache')
    require(erased['cache']['entries'] == 0 and erased['cache']['retained_bytes'] == 0 and
            erased['cache']['lease_count'] == 0 and erased['cache']['lease_bytes'] == 0 and
            not erased['cache']['cleanup_pending'], 'live_cache_erasure_unconfirmed')
    atomic_json(output/'live-delete.json', {'public_result': deleted, 'before': live, 'after': erased}, 2*MIB)
    final = server.stop_and_prove()
    counts = verify_provider_counts(spec, deadlines)
    atomic_json(output/'verified-provider-counts.json', counts, 2*MIB)
    require(all(final['counts']['rpc'][name]['entered'] == final['counts']['rpc'][name]['returned'] ==
                final['counts']['rpc'][name]['accepted'] == 1 and not final['counts']['rpc'][name]['rejected']
                for name in ('reset','push','finish','turns')), 'four_exact_native_RPCs_required')
    require(all(final['counts']['jobs'][name]['accepted'] == 1 for name in
                ('analyzer','commit','worker_start','witness_finalize')), 'single_primary_core_required')
    # Deleted source restart is intentionally distinct from losing an extant source on restart.
    restart_epochs = []
    for mode in ('restart-on', 'restart-off'):
        restarted = Server(spec, deadlines, journal, ledger, mode)
        no_new_calls(restarted.ready)
        if mode == 'restart-on':
            state = restarted.ready['cache']
            require(state['present'] and state['epoch'] != page['capture_epoch'] and state['entries'] == 0,
                    'fresh_empty_restart_epoch_required')
            restart_epochs.append(state['epoch'])
            restarted.client.request('GET', f'{base}/{cid}', expected=404, private=True)
        else:
            require(restarted.ready['cache']['present'] is False, 'OFF_restart_must_not_register_cache')
            restarted.client.request('GET', f'{base}/{cid}', expected=409, private=True)
        restarted.client.request('GET', f'/sessions/{sid}', expected=404)
        restart_snapshot = restarted.command('snapshot')
        no_new_calls(restart_snapshot)
        result = restarted.stop_and_prove()
        no_new_calls(result)
        atomic_json(output/f'{mode}-proof.json', result, 2*MIB)
    # Waits precede full posthash; accepted outer guard still owns its final cleanup reserve.
    posthash = verify_inventory(spec, deadlines, end=min(deadlines.posthash, time.monotonic()+120))
    atomic_json(output/'posthash.json', posthash)
    atomic_json(output/'capture-result.json', {'status': 'COMPLETE', 'source_id': sid, 'chunk_id': cid,
                'fixed_input': proof, 'actual_native_selected': True, 'capture_epoch': page['capture_epoch'],
                'restart_epochs': restart_epochs, 'original_primary_unchanged_by_optional_reads': True,
                'native_RPCs': final['counts']['rpc'], 'primary_jobs': final['counts']['jobs'],
                'public_requests': client.calls, 'public_response_bytes': client.response_bytes,
                'posthash': posthash, 'inference_accuracy_claim': False,
                'forensic_memory_erasure_claim': False, 'restart_extant_source_tested': False}, 2*MIB)
