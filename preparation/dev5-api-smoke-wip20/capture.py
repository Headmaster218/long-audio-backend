"""One fixed real capture via actual loopback HTTP; restarts only after proof of reaping."""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import stat
import socket
import threading
import subprocess
import time
from urllib.parse import urlencode
from common import (FRAMES, MIB, PCM_SHA, WAV_SHA, FLOAT_SHA, require, bounded_read,
                    atomic_json, fixed_wav, sha, canonical)


class Client:
    def __init__(self, port, token, deadlines, journal, maximum=384):
        self.port, self.token, self.deadlines, self.journal = port, token, deadlines, journal
        self.calls = 0
        self.response_bytes = 0
        self.polls = 0
        self.maximum = maximum

    def request(self, method, path, *, body=None, json_body=None, auth=True, expected=200, private=False, idempotency_key=None):
        self.deadlines.check()
        require(self.calls < self.maximum and path.startswith('/') and len(path) <= 4096, 'HTTP_request_cap')
        self.calls += 1
        headers = {'Connection': 'close'}
        if idempotency_key is not None:
            require(method == 'POST' and path.endswith('/moss-jobs') and
                    type(idempotency_key) is str and 1 <= len(idempotency_key) <= 64 and
                    all(c.isascii() and (c.isalnum() or c in '_-') for c in idempotency_key),
                    'bounded_moss_idempotency_key')
            headers['Idempotency-Key'] = idempotency_key
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
            require(len(actual_source) <= 331, 'source_file_count_grew')
    require(len(actual_source) == 331 and actual_source == set(spec['source_files']) and actual_source <= seen,
            'qualified_source_inventory_changed')
    for alias in spec.get('runtime_aliases', []):
        path = Path(alias['path'])
        require(path.is_symlink() and os.readlink(path) == alias['literal_target'] and
                str(path.resolve()) == alias['resolved_target'] and str(path.resolve()) in seen, 'runtime_alias_changed')
    require(time.monotonic() < end and not deadlines.stop.exists(), 'full_hash_final_deadline')
    return {'files': len(items), 'bytes': total, 'elapsed_seconds': time.monotonic()-began,
            'all_matched': True, 'source_files': 331}


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
        self.client = Client(ready['port'], spec['settings']['api_key'], deadlines, journal,384 if mode == 'capture' else 32)

    def wait_json(self, path, end):
        while not path.exists():
            self.deadlines.check(end)
            require(self.log_error is None, 'server_log_error')
            require(self.proc.poll() is None, 'server_exited_before_receipt')
            self.ledger.census()
            time.sleep(min(.05, max(0., end-time.monotonic())))
        return json.loads(bounded_read(path, 2*MIB, lambda: self.deadlines.check(end)))

    def command(self, action, **fields):
        require(self.sequence < (256 if self.mode == 'capture' else 32), 'private_command_cap')
        index = self.sequence
        self.sequence += 1
        atomic_json(self.dir/f'{index:03}.command.json', {'action': action, **fields},4096)
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
        final = json.loads(bounded_read(self.dir/'server-final.json',65536,self.deadlines.check))
        # Bootstrap children have nested receipts; the unchanged MOSS direct child does not.
        for original in receipt['originals']:
            if original['pid'] == final.get('moss_original_pid'):
                from bootstrap import moss_cleanup_evidence
                moss_cleanup_evidence(final.get('moss_terminal'))
                require(original['original_waited'] and original['returncode'] == 0,'moss_original_wait')
                continue
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
    for group_name,group in value['counts'].items():
        if type(group) is not dict:continue
        for name,row in group.items():
            if type(row) is not dict or 'entered' not in row:continue
            expected = 1 if group_name == 'jobs' and name == 'worker_start' else 0
            require(row['entered'] == expected and row.get('rejected',0) == 0,
                    'unexpected_restart_provider_call')
            if expected:require(row['returned'] == row['accepted'] == 1,'ordinary_worker_start_missing')
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
    private = json.loads(bounded_read(output/'private-claimed-source.json', MIB, deadlines.check))
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


def enter_phase(spec, deadlines, name, server=None):
    record = deadlines.enter(name,spec_sha256=spec['_sha256'])
    atomic_json(Path(spec['output_dir'])/'phase-window.json',record,4096)
    if server is not None:
        server.command('phase',record=record)
    return record


def zero_leases(state, *, deleted=False):
    cache = state['cache']
    require(cache['lease_count'] == cache['lease_bytes'] == 0 and
            not cache['response_cleanup_pending'] and not cache['dispatch_present'] and
            not cache['dispatch_cleanup_pending'], 'leases_or_dispatch_unreleased')
    if deleted:
        require(cache['entries'] == cache['retained_bytes'] == 0 and
                not cache['cleanup_pending'], 'deleted_cache_not_erased')
        require(not state['jobs'].get('live',False),'deleted_job_content_not_retired')
    permit = state['permit']
    if permit is not None:
        require(not permit['optional_present'] and permit['primary_count'] == 0 and
                permit['unavailable'] is None, 'permit_not_idle_or_unavailable')


def native_moss_receipt(value):
    """These are existing child-reported attempts, not independent native forwards."""
    from bootstrap import moss_cleanup_evidence
    moss_cleanup_evidence(value)
    receipt = value.get('native_receipt')
    require(type(receipt) is dict and all(receipt.get(k) == 1 and type(receipt[k]) is int
            for k in ('construct_attempts','session_attempts','run_attempts')),
            'one_moss_child_reported_attempt_required')
    require(receipt.get('model_closed') is True and receipt.get('session_closed') is True and
            not receipt.get('close_errors') and receipt.get('error_type') is None and
            receipt.get('eos') is None and receipt.get('generated_token_count') is None,
            'moss_native_success_receipt_required')
    return receipt


def run_controller(spec, deadlines, journal, ledger):
    output = Path(spec['output_dir'])
    require(not (output/'capture').exists() and not Path(spec['data_dir']).exists(), 'fresh_one_attempt_required')
    require(type(spec['settings']['api_key']) is str and 16 <= len(spec['settings']['api_key']) <= 128,
            'fixed_harness_bearer_required')
    enter_phase(spec,deadlines,'preflight')
    prehash = verify_inventory(spec,deadlines,end=deadlines.phase_end)
    atomic_json(output/'prehash.json',prehash)
    proof,wav,pcm = fixed_wav(spec['input_wav'],deadlines.check,encoded_pin=True)
    journal.emit('fixed_input_verified',**proof)
    server = Server(spec,deadlines,journal,ledger,'capture')
    client = server.client
    require(server.ready['cache']['present'] and server.ready['cache']['entries'] == 0 and
            server.ready['jobs']['present'] and server.ready['jobs']['accepted_jobs'] == 0,
            'fresh_enabled_owners')
    no_new_calls(server.ready)
    session = client.json('POST','/sessions',json_body={'name':'Fixed dev5 API smoke','processing_mode':'legacy'},expected=201)
    sid = session['id']
    upload = client.json('PUT',f'/sessions/{sid}/chunks?timeline_start=0',body=wav,expected=202)
    cid = upload['chunk']['id']
    require(upload['duplicate'] is False and upload['chunk']['duration'] == FRAMES/16000,
            'one_original_upload_required')
    atomic_json(output/'source-upload.json',{'session':session,'upload':upload,'input':proof},2*MIB)
    primary = server.wait_json(output/'primary-window.json',deadlines.phase_end)
    deadlines.bind_primary(primary,spec['_sha256'])
    completed = None
    for _ in range(120):
        row = client.json('GET',f'/chunks/{cid}')
        if row['status'] in ('completed','failed','cancelled','paused'):
            completed = row;break
        time.sleep(min(5.,max(0.,deadlines.phase_end-time.monotonic())))
    require(completed is not None,'bounded_primary_poll_exhausted')
    atomic_json(output/'terminal-chunk.json',completed,2*MIB)
    require(completed['status'] == 'completed' and completed['next_offset'] == FRAMES/16000,
            'primary_capture_incomplete')
    for _ in range(20):
        health = client.json('GET','/health')
        if health['active_chunk'] is None:break
        time.sleep(.05)
    require(health['active_chunk'] is None,'worker_return_not_observed')
    enter_phase(spec,deadlines,'support',server)
    before = server.command('snapshot')
    base = f'/sessions/{sid}/native-owned-supports'
    page = client.json('GET',base,private=True)
    require(page['lexical_dispatch_available'] is True and len(page['supports']) == 1,'one_dispatchable_support')
    row = page['supports'][0]
    require(row['chunk_id'] == cid and row['selected'] is True and row['source_frames'] == FRAMES,
            'current_selected_support_required')
    query = urlencode({'expected_capture_epoch':page['capture_epoch'],'expected_revision':row['revision']})
    detail = client.json('GET',f'{base}/{cid}?{query}',private=True)
    atomic_json(output/'public-list.json',page,MIB);atomic_json(output/'public-detail.json',detail,MIB)
    reconstruction = reconstruct_capture(spec,upload,detail,deadlines)
    atomic_json(output/'independent-reconstruction.json',reconstruction,MIB)
    audio,_ = client.request('GET',f'{base}/{cid}/audio?{query}',private=True)
    require(audio == wav,'fresh_public_audio_not_exact')
    (output/'public-audio.wav').write_bytes(audio)
    jobs_base = f'/sessions/{sid}/moss-jobs'
    listing = client.json('GET',jobs_base,private=True)
    require(listing['jobs'] == [],'fresh_jobs_ledger_required')
    request = {'jobs_epoch':listing['jobs_epoch'],'expected_capture_epoch':page['capture_epoch'],
        'expected_revision':row['revision'],'support_id':detail['support_id'],
        'profile':'handy-moss-q8_0-cpu-experimental-v1'}
    state = server.command('state');zero_leases(state)
    # Submit is capped by support/admission; the unchanged first direct launch freezes compute.
    key = 'dev5-one-live-support'
    submit_path = f'{base}/{cid}/moss-jobs'
    accepted = client.json('POST',submit_path,json_body=request,idempotency_key=key,expected=202,private=True)
    jid = accepted['job_id']
    moss_window = server.wait_json(output/'moss-window.json',deadlines.phase_end)
    deadlines.enter('moss-compute',record=moss_window,spec_sha256=spec['_sha256'])
    require(accepted['jobs_epoch'] == listing['jobs_epoch'] and accepted['support_id'] == request['support_id'],
            'job_binding_mismatch')
    replay = None
    if spec['identical_key_replay']:
        replay = client.json('POST',submit_path,json_body=request,idempotency_key=key,expected=200,private=True)
        require(replay['job_id'] == jid,'idempotent_job_changed')
    terminal = None
    polls = 0
    for phase in ('moss-compute','moss-cleanup'):
        if phase == 'moss-cleanup':enter_phase(spec,deadlines,phase,server)
        while polls < 180 and deadlines.phase_end-time.monotonic() > .25:
            row = client.json('GET',f'{jobs_base}/{jid}',private=True);polls += 1
            if row['process_cleanup_verified'] and not row['cleanup_pending']:
                terminal = row;break
            server.command('state',observe_moss=phase == 'moss-compute')
            time.sleep(min(2.,max(0.,deadlines.phase_end-time.monotonic()-.1)))
        if terminal is not None:break
    require(terminal is not None and terminal['state'] == 'returned_diagnostic' and
            terminal['raw_available'] and terminal['result_available'], 'moss_success_not_terminal')
    enter_phase(spec,deadlines,'lifecycle',server)
    raw,headers = client.request('GET',f'{jobs_base}/{jid}/raw',private=True)
    result = client.json('GET',f'{jobs_base}/{jid}/result',private=True)
    require(len(raw) <= 131072 and headers.get('x-content-sha256') == sha(raw),'raw_header_identity')
    (output/'moss-public-raw.bin').write_bytes(raw)
    atomic_json(output/'moss-public-result.json',result,262144)
    state = server.command('state')
    require(state['jobs']['accepted_jobs'] == 1 and state['launches']['moss-direct'] == 1,
            'one_accepted_job_one_original_launch')
    native = native_moss_receipt(state['moss_terminal'])
    require(native['raw_retained'] is True and native['raw_bytes'] == len(raw) and native['raw_sha256'] == sha(raw),
            'moss_raw_binding')
    zero_leases(state)
    assert_primary_equal(before,server.command('snapshot'))
    cancelled = client.json('POST',f'{jobs_base}/{jid}/cancel',json_body={'jobs_epoch':listing['jobs_epoch']},private=True)
    require(cancelled['publication_cancelled'] and cancelled['process_cleanup_verified'],'terminal_cancel_failed')
    client.request('GET',f'{jobs_base}/{jid}/raw',expected=409,private=True)
    client.request('GET',f'{jobs_base}/{jid}/result',expected=409,private=True)
    assert_primary_equal(before,server.command('snapshot'))
    deleted = client.json('DELETE',f'/sessions/{sid}')
    require(deleted.get('native_owned_support_cleanup_pending') is False,'delete_cache_cleanup_pending')
    erased = server.command('state');zero_leases(erased,deleted=True)
    require(erased['jobs']['accepted_jobs'] == 1,'delete_changed_acceptance_count')
    client.request('GET',f'{base}/{cid}',expected=404,private=True)
    client.request('GET',f'{jobs_base}/{jid}',expected=404,private=True)
    atomic_json(output/'api-job-proof.json',{'accepted':accepted,'replay':replay,'terminal':terminal,
        'native_receipt':native,'counter_scope':'unchanged child-reported attempts; native forwards unknown',
        'state_before_cancel':state,'cancelled':cancelled,'deleted':deleted,'erased':erased},MIB)
    final = server.stop_and_prove()
    counts = verify_provider_counts(spec,deadlines)
    atomic_json(output/'verified-provider-counts.json',counts,2*MIB)
    require(all(final['counts']['rpc'][name]['entered'] == final['counts']['rpc'][name]['returned'] ==
                final['counts']['rpc'][name]['accepted'] == 1 and not final['counts']['rpc'][name]['rejected']
                for name in ('reset','push','finish','turns')), 'four_exact_native_RPCs_required')
    require(all(final['counts']['jobs'][name]['accepted'] == 1 for name in
                ('analyzer','commit','worker_start','witness_finalize')), 'single_primary_core_required')
    restart_epochs = []
    for mode in ('restart-on','restart-off'):
        restarted = Server(spec,deadlines,journal,ledger,mode)
        no_new_calls(restarted.ready)
        if mode == 'restart-on':
            fresh = restarted.ready
            require(fresh['cache']['present'] and fresh['cache']['entries'] == 0 and
                    fresh['cache']['epoch'] != page['capture_epoch'] and
                    fresh['jobs']['present'] and fresh['jobs']['accepted_jobs'] == 0 and
                    fresh['jobs']['jobs_epoch'] != listing['jobs_epoch'],'fresh_restart_owners_required')
            restart_epochs.append({'capture':fresh['cache']['epoch'],'jobs':fresh['jobs']['jobs_epoch']})
            restarted.client.request('GET',f'{base}/{cid}',expected=404,private=True)
            restarted.client.request('GET',f'{jobs_base}/{jid}',expected=404,private=True)
        else:
            require(not restarted.ready['cache']['present'] and not restarted.ready['jobs']['present'],
                    'OFF_owners_registered')
            restarted.client.request('GET',f'{base}/{cid}',expected=409,private=True)
            restarted.client.request('GET',f'{jobs_base}/{jid}',expected=409,private=True)
        restarted.client.request('GET',f'/sessions/{sid}',expected=404)
        no_new_calls(restarted.command('snapshot'))
        result = restarted.stop_and_prove();no_new_calls(result)
        atomic_json(output/f'{mode}-proof.json',result,131072)
    # bootstrap.finalize owns one cleanup pass followed by mandatory full posthash.
    atomic_json(output/'capture-result.json',{'status':'COMPLETE','source_id':sid,'chunk_id':cid,'job_id':jid,
        'fixed_input':proof,'actual_native_selected':True,'one_moss_job':True,'capture_epoch':page['capture_epoch'],
        'restart_epochs':restart_epochs,'original_primary_unchanged_by_optional_actions':True,
        'native_RPCs':final['counts']['rpc'],'primary_jobs':final['counts']['jobs'],
        'moss_child_reported_attempts':native,'public_requests':client.calls,
        'public_response_bytes':client.response_bytes,'inference_accuracy_claim':False,
        'EOS':'unknown','forensic_memory_erasure_claim':False,'restart_extant_source_tested':False},2*MIB)


def hash_only_main():
    """One separately reviewed finite hash sweep; no app/provider imports or decoding."""
    import argparse
    from types import SimpleNamespace
    parser=argparse.ArgumentParser()
    parser.add_argument('--hash-inventory',type=Path,required=True)
    parser.add_argument('--hash-inventory-sha256',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    require(args.hash_inventory==root/'HASH_INVENTORY.json','closed_hash_inventory_path')
    absolute=float(os.environ['GUARD_ABSOLUTE_END']);work=float(os.environ['GUARD_WORK_END'])
    require(work==absolute-60 and 0<absolute-time.monotonic()<=300,'hash_outer_clock')
    stop=Path(os.environ['GUARD_STOP_PATH']);run=Path(os.environ['GUARD_RUN_DIRECTORY'])
    require(run.is_absolute() and run.resolve()==run and run.parent==root/'runs','hash_run_scope')
    def alive():
        require(time.monotonic()<work and not stop.exists(),'hash_work_or_stop')
    raw=bounded_read(args.hash_inventory,4*MIB,alive)
    require(sha(raw)==args.hash_inventory_sha256,'hash_inventory_manifest_pin')
    spec=json.loads(raw)
    require(spec['schema']=='dev5-api-smoke-immutable-inventory-v1' and
            spec['source_root']==str(root.parents[1]/'reconstruction-rc3-dev5'),'hash_inventory_identity')
    expected=sha(canonical(sorted(spec['hash_inventory'],key=lambda row:row['path'])))
    require(spec['inventory_sha256']==expected,'canonical_inventory_digest')
    end=min(time.monotonic()+120.,work)
    result=verify_inventory(spec,SimpleNamespace(stop=stop),end=end)
    result.update(schema='dev5-api-smoke-hash-result-v1',status='PASS_HASH_ONLY',
        inventory_sha256=expected,inventory_manifest_sha256=args.hash_inventory_sha256,
        source_freeze_sha256=spec['source_freeze_sha256'],
        product_imports=0,audio_decodes=0,services=0,model_calls=0,
        scope='Immutable byte hashing only; later authority and terminal metadata are separately outer-pinned')
    alive();out=run/'tmp/hash-only';out.mkdir(mode=0o700,exist_ok=False)
    atomic_json(out/'hash-only-result.json',result,65536);alive()
    print(json.dumps(result,sort_keys=True),flush=True)


if __name__=='__main__':
    hash_only_main()
