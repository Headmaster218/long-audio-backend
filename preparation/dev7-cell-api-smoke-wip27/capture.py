"""Gate A durable-cell capture; extant-source restarts only after positive reaping."""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import stat
import struct
import socket
import threading
import subprocess
import time
from urllib.parse import urlencode
from common import (FRAMES, CELL_FRAMES, MIB, ORIGINAL_BYTES, ORIGINAL_SHA, ORIGINAL_INTERVAL, require, bounded_read,
                    atomic_json, fixed_wav, sha, canonical)


class Client:
    def __init__(self, port, token, deadlines, journal, maximum=384):
        self.port, self.token, self.deadlines, self.journal = port, token, deadlines, journal
        self.calls = 0
        self.response_bytes = 0
        self.polls = 0
        self.maximum = maximum

    def request(self, method, path, *, body=None, json_body=None, auth=True, expected=200, private=False, idempotency_key=None, response_max=8*MIB):
        self.deadlines.check()
        require(type(response_max) is int and 1 <= response_max <= 8*MIB, 'HTTP_response_limit')
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
            require(response.length is None or response.length <= response_max, 'HTTP_declared_body_cap')
            data = bytearray()
            while True:
                self.deadlines.check(end)
                if connection.sock is not None:
                    connection.sock.settimeout(max(.001, end-time.monotonic()))
                part = response.read(min(65536, response_max + 1-len(data)))
                if not part:
                    break
                data.extend(part)
                require(len(data) <= response_max, 'HTTP_body_cap')
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
            connection.close()  # Every body is drained before optional reads and clean restarts.

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
            require(len(actual_source) <= 346, 'source_file_count_grew')
    require(len(actual_source) == 346 and actual_source == set(spec['source_files']) and actual_source <= seen,
            'qualified_source_inventory_changed')
    for alias in spec.get('runtime_aliases', []):
        path = Path(alias['path'])
        require(path.is_symlink() and os.readlink(path) == alias['literal_target'] and
                str(path.resolve()) == alias['resolved_target'] and str(path.resolve()) in seen, 'runtime_alias_changed')
    require(time.monotonic() < end and not deadlines.stop.exists(), 'full_hash_final_deadline')
    return {'files': len(items), 'bytes': total, 'elapsed_seconds': time.monotonic()-began,
            'all_matched': True, 'source_files': 346}


class Server:
    def __init__(self, spec, deadlines, journal, ledger, mode):
        self.spec, self.deadlines, self.journal, self.ledger = spec, deadlines, journal, ledger
        self.mode = mode
        self.stopped_and_reaped = False
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
        self.stopped_and_reaped = True
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
    if 'cell_witness' in value:
        witness = value['cell_witness']
        require(not witness['errors'] and not witness['prepared'] and not witness['committed_called'] and
                not witness['commit_returned_true'], 'unexpected_restart_cell_producer')
    if 'cell_pin_cleanup' in value:
        require(all(value['cell_pin_cleanup'][name] == 0 for name in
            ('acquire_entered','accepted_entered','release_entered','acquired','accepted','release_requested',
             'registry_removal_acknowledged','release_confirmed','permit_release_checked')),
            'unexpected_restart_optional_lease_work')


def assert_primary_equal(first, second):
    require(first['snapshot']['bytes'] == second['snapshot']['bytes'] and
            first['snapshot']['sha256'] == second['snapshot']['sha256'], 'optional_reads_changed_primary')


def cell_page(client, base, expected_epoch=None):
    """Read the complete at-most-two-cell result through bounded real pagination."""
    rows, after, epoch = [], 0, expected_epoch
    for _ in range(3):
        query = {} if after == 0 else {'after':after, 'expected_jobs_epoch':epoch}
        if epoch is not None:query['expected_jobs_epoch'] = epoch
        page = client.json('GET', base + ('?' + urlencode(query) if query else ''),
                           private=True, response_max=65536)
        require(type(page['cells']) is list and type(page['has_more']) is bool,
                'bounded_cell_page_shape')
        if epoch is None:epoch = page['jobs_epoch']
        require(page['jobs_epoch'] == epoch and len(rows) + len(page['cells']) <= 2,
                'cell_page_epoch_or_count')
        for row in page['cells']:
            require(type(row['sequence']) is int and row['sequence'] > after,
                    'cell_sequence_not_increasing')
            after = row['sequence']; rows.append(row)
        require(page['next_cursor'] == after, 'cell_cursor_mismatch')
        if not page['has_more']:
            return {'jobs_epoch':epoch, 'cells':rows, 'next_cursor':after, 'has_more':False}
        require(page['cells'], 'empty_continuing_cell_page')
    require(False, 'cell_page_cap')


def cell_detail(client, base, row, epoch):
    from audio_backend.native_owned_cells import validate_document, POLICY_SHA256
    proof, headers = client.request('GET', f"{base}/{row['cell_id']}",
                                    private=True, response_max=65536)
    doc, support, _ = validate_document(proof)
    require(sha(proof) == row['proof_sha256'] and
            row['cell_id'] == sha(canonical({'policy':POLICY_SHA256, 'support':support.to_dict()})) and
            row['support_id'] == support.support_id and
            row['state'] == 'retained' and
            all(row[name] == getattr(support,name) for name in
                ('start_frame','end_frame','core_start_frame','core_end_frame')),
            'cell_list_detail_binding')
    expected = {'x-native-cell-id':row['cell_id'], 'x-native-cell-sequence':str(row['sequence']),
        'x-native-cell-revision':str(row['revision']), 'x-native-cell-proof-sha256':sha(proof),
        'x-native-cell-state':'retained', 'x-native-cell-pcm-retained':'true', 'x-moss-jobs-epoch':epoch}
    require(all(headers.get(name) == value for name,value in expected.items()), 'cell_detail_header_binding')
    return doc, proof, headers


def validate_partition_selection(partitions, retained_bounds):
    """Gate A requires the actual substantial selection; an unselected tail is absent."""
    require(type(partitions) is list and len(partitions) == 2 and
            all(type(row) is dict and type(row.get('selected')) is bool for row in partitions) and
            [row.get('frames') for row in partitions] == [[0,CELL_FRAMES],[CELL_FRAMES,FRAMES]],
            'exact_committed_cell_partition_required')
    require(partitions[0]['selected'], 'substantial_selected_cell_required')
    expected = [row['frames'] for row in partitions if row['selected']]
    require(type(retained_bounds) is list and retained_bounds == expected,
            'selected_retained_partition_mismatch')
    return {'tail_retained':partitions[1]['selected'], 'committed_core_frames':[0,FRAMES]}


def reconstruct_capture(spec, upload, page, details, deadlines):
    """Pure reconstruction from actual original RPC/producer/commit observations."""
    from audio_backend.native_owned_cells import (RollingWitness, SourceSpan, make_cells,
                                                 selection, partition, float32_digest)
    output = Path(spec['output_dir'])
    recorded = json.loads(bounded_read(output/'cell-witness.json', MIB, deadlines.check))
    private = json.loads(bounded_read(output/'private-claimed-source.json', MIB, deadlines.check))
    require(private['id'] == upload['chunk']['id'] and private['session_id'] == upload['chunk']['session_id'] and
            private['digest'] == spec['input_pins']['wav_sha256'] and private['duration'] == FRAMES/16000 and
            private['timeline_start'] == private['next_offset'] == 0., 'private_public_source_binding')
    require(recorded['chunk'] == private and recorded['offset'] == 0. and recorded['end'] == FRAMES/16000 and
            recorded['scope_start'] == recorded['origin_frame'] == recorded['analyzer_start'] == 0,
            'observed_cell_core_geometry')
    _, _, original_pcm = fixed_wav(spec['input_wav'], deadlines.check, encoded_pin=True, pins=spec['input_pins'])
    _, _, native_pcm = fixed_wav(output/'native-submitted.wav', deadlines.check, pins=spec['input_pins'])
    _, _, analyzer_pcm = fixed_wav(output/'analyzer-submitted.wav', deadlines.check, pins=spec['input_pins'])
    require(original_pcm == native_pcm == analyzer_pcm, 'original_native_analyzer_pcm_mismatch')
    witness = RollingWitness()
    for index, action in enumerate(('reset','push','finish','turns'), 1):
        if action == 'push':witness.observe_pcm(0, FRAMES, native_pcm)
        wire = json.loads(bounded_read(output/f'native-{index}-{action}.raw', 8*MIB, deadlines.check))
        witness.observe(wire)
    require(witness.failure is None and witness.finished and witness.ring_end == FRAMES,
            'independent_native_witness_incomplete')
    parts = tuple(SourceSpan(**part) for part in recorded['mapping'])
    require(len(parts) == 1 and parts[0].session_id == private['session_id'] and
            parts[0].chunk_id == private['id'] and parts[0].source_sha256 == spec['input_pins']['wav_sha256'] and
            parts[0].source_frames == FRAMES and parts[0].source_start == parts[0].logical_start == 0 and
            parts[0].source_end == parts[0].logical_end == FRAMES, 'isolated_original_receipt_mapping')
    require(type(recorded['receipts']) is list and len(recorded['receipts']) == 1, 'one_original_upload_receipt')
    receipt = recorded['receipts'][0]
    require(receipt['receipt_id'] == parts[0].receipt_sha256 == sha(canonical(receipt['receipt'])) and
            all(receipt['receipt'][key] == value for key,value in {'session_id':private['session_id'],
                'chunk_id':private['id'],'digest':spec['input_pins']['wav_sha256'],'frames':FRAMES,
                'data_offset':44,'size':672044,'timeline_start':0.,'stream_id':None}.items()),
            'original_upload_receipt_binding')
    precommit = json.loads(bounded_read(output/'precommit.json', 10*MIB, deadlines.check))
    evidence = {'chunk_id':private['id'], 'start':0, 'end':FRAMES,
                'native':recorded['native']['state'], 'core_cut':precommit['result']['metadata']['core_cut']}
    require(precommit['chunk'] == private and precommit['offset'] == 0. and precommit['end'] == FRAMES/16000 and
            precommit['result']['metadata']['native_diarization'] == recorded['native']['state'] and
            recorded['commit_evidence'] == evidence and recorded['commit_sha256'] == sha(canonical(evidence)),
            'original_commit_evidence_binding')
    cells = make_cells(witness=witness, native=recorded['native'], scope_start=recorded['scope_start'],
        origin_frame=recorded['origin_frame'], core_start=0, core_end=FRAMES,
        analyzer_start=recorded['analyzer_start'], analyzer_pcm=analyzer_pcm, mapping=parts,
        chunk_id=private['id'], commit_start=0, commit_end=FRAMES,
        commit_sha256=recorded['commit_sha256'], commit_evidence=evidence)
    require(1 <= len(cells) <= 2 and len(page['cells']) == len(cells) and
            len(recorded['candidates']) == len(cells), 'actual_selected_cell_count')
    turns, _ = witness.prepared(recorded['native'], recorded['scope_start'], recorded['origin_frame'])
    partitions = []
    for a,b in partition(0,FRAMES):
        selected, overlaps = selection(turns,a,b)
        partitions.append({'frames':[a,b], 'selected':selected, 'turns':overlaps})
    validate_partition_selection(partitions,[[cell.support.start_frame,cell.support.end_frame] for cell in cells])
    lineage = []
    for index, cell in enumerate(cells):
        proof = bounded_read(output/f'cell-{index}-proof.json',65536,deadlines.check)
        pcm = bounded_read(output/f'cell-{index}.pcm16le',640000,deadlines.check)
        row = page['cells'][index]; candidate = recorded['candidates'][index]
        require(cell.cell_id == row['cell_id'] == candidate['cell_id'] and
                proof == cell.proof == details[index]['proof'] and pcm == cell.pcm and
                sha(pcm) == candidate['pcm_sha256'] == cell.support.pcm16le_sha256 and
                sha(proof) == candidate['proof_sha256'] and
                candidate['support'] == cell.support.to_dict() and
                float32_digest(pcm) == cell.support.float32le_sha256 and
                pcm == original_pcm[2*cell.support.start_frame:2*cell.support.end_frame],
                'independent_cell_reconstruction_mismatch')
        lineage.append({'cell_id':cell.cell_id, 'sequence':row['sequence'], 'revision':row['revision'],
            'proof_sha256':sha(proof), 'pcm_sha256':sha(pcm), 'support':cell.support.to_dict()})
    require(lineage[0]['support']['start_frame'] == 0 and lineage[0]['support']['end_frame'] == CELL_FRAMES,
            'required_retained_substantial_cell')
    return {'exact_source_core_witness_reconstruction':True, 'committed_core_frames':[0,FRAMES],
        'partition_selection':partitions, 'cells':lineage, 'source_receipt_sha256':parts[0].receipt_sha256,
        'tail_retained':len(cells) == 2, 'tail_absence_allowed_only_when_unselected':True,
        'upload_seam_claim':False, 'epoch_transition_claim':False}


def verify_provider_counts(spec, deadlines):
    output = Path(spec['output_dir'])
    native = list(output.glob('native-*-counts.json'))
    analyzer = list(output.glob('analyzer-*-counts.json'))
    require(len(native) == len(analyzer) == 1, 'exact_provider_counter_files_required')
    n = json.loads(bounded_read(native[0], MIB, deadlines.check))
    a = json.loads(bounded_read(analyzer[0], MIB, deadlines.check))
    required = {'native_model_create': 1, 'native_stream_open': 1, 'native_push': 11,
                'native_finish': 1, 'native_segments': 2}
    for name, count in required.items():
        row = n['abi'][name]
        require(row['entered'] == row['returned'] == row['accepted'] == count and
                row['rejected'] == row['exceptions'] == 0, 'native_ABI_terminal_counts')
    require(n['native_frames'] == FRAMES and n['native_float32_sha256'] == spec['input_pins']['float32_sha256'], 'native_ABI_input_pin')
    maximums = {'event_compute': 8, 'embedding_compute': 8, 'sensevoice_decode': 32,
                'event_constructor': 1, 'embedding_constructor': 1, 'sensevoice_constructor': 1}
    for name, cap in maximums.items():
        row = a['sdk'][name]
        require(row['entered'] == row['returned'] == row['accepted'] <= cap and
                row['rejected'] == row['exceptions'] == 0, 'SDK_terminal_counts')
    require(a['lifecycle']['analyzer_constructor']['accepted'] == 1, 'one_analyzer_constructor_required')
    require(a['sdk']['sensevoice_constructor']['accepted'] == 1 and
            a['sdk']['sensevoice_decode']['accepted'] >= 1, 'actual_SenseVoice_execution_required')
    total = sum(a['sdk'][name]['entered'] for name in ('event_compute','embedding_compute','sensevoice_decode'))
    require(total <= 48, 'SDK_total_cap')
    return {'native': n, 'analyzer': a, 'Sherpa_inference_entries': total,
            'hidden_native_model_forward_counts': 'unknown_not_exposed'}


def enter_phase(spec, deadlines, name, server=None):
    record = deadlines.enter(name,spec_sha256=spec['_sha256'])
    atomic_json(Path(spec['output_dir'])/'phase-window.json',record,4096)
    if server is not None:
        server.command('phase',record=record)
    return record


def zero_leases(state):
    cache = state['cache']
    require(cache['lease_count'] == cache['lease_bytes'] == 0 and
            not cache['response_cleanup_pending'] and not cache['dispatch_present'] and
            not cache['dispatch_cleanup_pending'], 'leases_or_dispatch_unreleased')
    require(state['cells']['pins'] == 0 and state['cells']['handoffs_active_or_pending'] == 0 and
            not state['cells']['cleanup_pending'], 'cell_pins_or_handoff_unreleased')
    permit = state['permit']
    if permit is not None:
        require(not permit['optional_present'] and permit['primary_count'] == 0 and
                permit['unavailable'] is None,
                'permit_not_idle_or_unavailable')


def cell_cleanup(state, cell_id, proof_hash):
    receipt = state['cell_pin_cleanup']
    require(not state['cell_witness']['errors'], 'cell_observer_error')
    require(receipt['acquire_entered'] == receipt['accepted_entered'] == 1, 'one_cell_acquisition_entry')
    require(receipt['acquired'] == receipt['accepted'] == receipt['registry_removal_acknowledged'] ==
            receipt['release_confirmed'] == 1 and receipt['release_requested'] >= 1 and
            receipt['permit_release_checked'] >= 1 and receipt['cell_id'] == cell_id and
            receipt['proof_sha256'] == proof_hash and receipt['exact_registry_absent'] is True and
            receipt['original_release_returned_true'] is True, 'independent_cell_pin_cleanup_unconfirmed')
    zero_leases(state)
    return receipt


def unchanged_cells(client, base, page, details, epoch):
    fresh = cell_page(client,base,epoch)
    require(fresh['cells'] == page['cells'], 'durable_cell_rows_changed')
    for index,row in enumerate(fresh['cells']):
        _, proof, _ = cell_detail(client,base,row,epoch)
        require(proof == details[index]['proof'], 'durable_cell_proof_changed')
    return fresh


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


def closed_cell_snapshot(server, stopped, spec, deadlines, page, details, reconstruction):
    """Closed-database logical observation only, never a live CellStore read.

    The original server and its children must already be positively reaped.
    Immutable mode plus absent journal/WAL/SHM forbids recovery. All SQL is
    read-only, length-first, bounded by two cells, one receipt and a VM clock.
    """
    import sqlite3
    require(server.stopped_and_reaped and server.proc.returncode == 0,
            'closed_cell_snapshot_original_server_not_reaped')
    cells = stopped['cells']
    require(cells['present'] and cells['status'] == 'closed' and not cells['owner_alive'] and
            not cells['ready'] and not cells['cleanup_pending'] and
            cells['pins'] == cells['handoffs_active_or_pending'] == 0 and
            not stopped['cell_witness']['errors'] and not server.ledger.census(),
            'closed_cell_owner_cleanup_unconfirmed')
    path = Path(spec['data_dir'])/'native-owned-cells/cells.sqlite3'
    require(path.is_absolute() and path.resolve() == path and len(path.parents) <= 33,
            'closed_cell_database_path_alias')
    end = deadlines.call_end(5.)
    def check():deadlines.check(end)
    def namespace():
        check(); ancestors = []
        for parent in path.parents:
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode), 'closed_cell_parent_alias_or_kind')
            ancestors.append([str(parent),info.st_dev,info.st_ino])
        entries = []
        with os.scandir(path.parent) as scan:
            for entry in scan:
                entries.append(entry.name)
                require(len(entries) <= 4,'closed_cell_family_capacity')
        require(entries == ['cells.sqlite3'], 'closed_cell_journal_or_unexpected_family_member')
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid() and
                4096 <= info.st_size <= 24*MIB, 'closed_cell_database_shape')
        return ancestors,info
    ancestors,before = namespace()
    signature = lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    db = None;steps = 0; projected = 0
    def progress():
        nonlocal steps
        steps += 100
        try:check();require(steps <= 2_000_000,'closed_cell_SQL_VM_cap');return 0
        except BaseException:return 1
    def charge(raw):
        nonlocal projected
        check();projected += len(raw)
        require(projected <= 2*MIB,'closed_cell_projection_cap')
        return raw
    try:
        require(signature(before) == signature(os.fstat(fd)), 'closed_cell_database_identity_changed')
        db = sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1&vfs=unix',uri=True,timeout=0.,isolation_level=None)
        db.set_progress_handler(progress,100)
        for pragma in ('PRAGMA query_only=ON','PRAGMA mmap_size=0','PRAGMA temp_store=MEMORY',
                       'PRAGMA trusted_schema=OFF','PRAGMA busy_timeout=0'):
            db.execute(pragma)
        require(db.execute('PRAGMA query_only').fetchone()[0] == 1 and
                db.execute('PRAGMA mmap_size').fetchone()[0] == 0 and
                db.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'delete',
                'closed_cell_read_only_pragmas')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MIB)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH,16384)
        db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED,0)
        allowed = {sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION}
        db.set_authorizer(lambda action,*args:sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        require(db.execute('SELECT COUNT(*) FROM cells').fetchone()[0] == len(page['cells']) and
                db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 1 and
                db.execute('SELECT COUNT(*) FROM dependencies').fetchone()[0] == len(page['cells']) and
                db.execute('SELECT COUNT(*) FROM store_meta').fetchone()[0] == 1,
                'closed_cell_exact_row_counts')
        lengths = db.execute("""SELECT seq,length(CAST(cell_id AS BLOB)),length(CAST(ownership AS BLOB)),
            length(CAST(sid AS BLOB)),length(CAST(cid AS BLOB)),length(proof),length(pcm),
            length(CAST(state AS BLOB)),revision FROM cells ORDER BY seq LIMIT 3""").fetchall()
        require(len(lengths) == len(page['cells']) <= 2,'closed_cell_row_limit')
        observed = []
        for index,row in enumerate(lengths):
            expected = page['cells'][index]; doc = details[index]['document']
            require(row[0] == expected['sequence'] and row[1] == row[2] == 64 and
                    1 <= row[3] <= 128 and 1 <= row[4] <= 128 and 1 <= row[5] <= 65536 and
                    row[6] == 2*(expected['end_frame']-expected['start_frame']) <= 640000 and
                    1 <= row[7] <= 32 and row[8] == expected['revision'],'closed_cell_row_lengths')
            saved = db.execute('SELECT cell_id,ownership,sid,cid,state,revision,proof FROM cells WHERE seq=?',(row[0],)).fetchone()
            cell_id,ownership,sid,cid,state,revision,proof = saved
            charge(proof);charge(canonical(list(saved[:-1])))
            require(cell_id == expected['cell_id'] and proof == details[index]['proof'] and state == 'retained' and
                    sid == doc['dependencies'][0]['session_id'] and cid == doc['commit']['chunk_id'] and
                    ownership == sha(canonical({'session':sid,'chunk':cid,'core':[0,FRAMES],
                        'cell':[expected['start_frame'],expected['end_frame']]})), 'closed_cell_row_binding')
            digest = hashlib.sha256()
            for at in range(0,row[6],65536):
                block = db.execute('SELECT substr(pcm,?,?) FROM cells WHERE seq=?',
                                   (at+1,min(65536,row[6]-at),row[0])).fetchone()[0]
                require(type(block) is bytes and len(block) == min(65536,row[6]-at),'closed_cell_pcm_read_length')
                digest.update(charge(block))
            require(digest.hexdigest() == doc['support']['pcm16le_sha256'],'closed_cell_pcm_hash')
            lengths_dep = db.execute("""SELECT position,length(CAST(cid AS BLOB)),length(CAST(digest AS BLOB)),
                length(CAST(receipt_id AS BLOB)) FROM dependencies WHERE cell_id=? ORDER BY position LIMIT 2""",(cell_id,)).fetchall()
            require(len(lengths_dep) == 1 and lengths_dep[0][0] == 0 and
                    1 <= lengths_dep[0][1] <= 128 and lengths_dep[0][2:] == (64,64),'closed_cell_dependency_lengths')
            dep = db.execute('SELECT cid,digest,receipt_id,source_start,source_end,logical_start,logical_end FROM dependencies WHERE cell_id=? AND position=0',(cell_id,)).fetchone()
            charge(canonical(list(dep)));part = doc['dependencies'][0]
            require(list(dep) == [part[k] for k in ('chunk_id','source_sha256','receipt_sha256',
                'source_start','source_end','logical_start','logical_end')],'closed_cell_dependency_binding')
            observed.append({'sequence':row[0],'cell_id':cell_id,'ownership':ownership,'sid':sid,'cid':cid,
                'state':state,'revision':revision,'proof_sha256':sha(proof),'pcm_sha256':digest.hexdigest(),
                'pcm_bytes':row[6],'dependency':list(dep)})
        length = db.execute('SELECT length(CAST(cid AS BLOB)),length(CAST(sid AS BLOB)),length(CAST(digest AS BLOB)),length(receipt) FROM sources LIMIT 2').fetchall()
        require(len(length) == 1 and 1 <= length[0][0] <= 128 and 1 <= length[0][1] <= 128 and
                length[0][2] == 64 and 1 <= length[0][3] <= 1024,'closed_source_receipt_lengths')
        cid,sid,digest,receipt = db.execute('SELECT cid,sid,digest,receipt FROM sources LIMIT 1').fetchone()
        charge(receipt);charge(canonical([cid,sid,digest]))
        require(cid == observed[0]['cid'] and sid == observed[0]['sid'] and
                digest == spec['input_pins']['wav_sha256'] and
                sha(receipt) == reconstruction['source_receipt_sha256'],'closed_source_receipt_binding')
        meta_lengths = db.execute("""SELECT length(CAST(schema AS BLOB)),length(CAST(policy AS BLOB)),
            length(CAST(primary_identity AS BLOB)),length(CAST(qualification AS BLOB)),
            length(CAST(namespace_identity AS BLOB)),length(CAST(database_identity AS BLOB)) FROM store_meta WHERE id=1""").fetchone()
        require(meta_lengths is not None and all(type(n) is int and 1 <= n <= 8192 for n in meta_lengths),
                'closed_cell_meta_lengths')
        meta = db.execute('SELECT schema,policy,primary_identity,qualification,namespace_identity,database_identity FROM store_meta WHERE id=1').fetchone()
        meta_hash = sha(charge(canonical(list(meta))))
        body = {'cells':observed,'source':{'chunk_id':cid,'session_id':sid,'digest':digest,'receipt_sha256':sha(receipt)},
                'store_meta_sha256':meta_hash}
    finally:
        if db is not None:db.close()
        try:
            after_ancestors,after = namespace()
            require(ancestors == after_ancestors and signature(before) == signature(os.fstat(fd)) == signature(after),
                    'closed_cell_snapshot_mutated_database_or_namespace')
        finally:os.close(fd)
    return {'scope':'closed-database logical preservation; no live owner or recovery',
        'original_server_pid':server.proc.pid,'original_server_waited_and_reaped':True,
        'database_identity':[before.st_dev,before.st_ino],'namespace_identity':ancestors,
        'database_bytes':before.st_size,'logical_sha256':sha(canonical(body)),**body,
        'projected_bytes':projected,'SQL_VM_steps_upper':steps+100,'query_only':True,'immutable':True,
        'journal_wal_shm_absent':True}


def await_cell_publication(server, deadlines, *, sleep=time.sleep, maximum=64):
    """Observe only the original async publish; never wait/claim/retry its work."""
    require(type(maximum) is int and 1 <= maximum <= 64, 'publication_observation_cap')
    for _ in range(maximum):
        deadlines.check()
        state = server.command('state')
        witness = state['cell_witness']
        require(not witness['errors'], 'cell_observer_error')
        publication = witness['publication']
        if publication is not None:
            require(publication['kind'] == 'Operation' and publication['operation_kind'] == 'publish',
                    'original_publish_operation_required')
            if publication['done']:
                require(publication['result']['status'] == 'durable', 'original_cell_publication_failed')
                if publication['slot_released']:
                    require(witness['prepared'] and witness['commit_returned_true'] and
                            witness['committed_called'] and witness['core_frames'] == [0,FRAMES],
                            'genuine_durable_committed_core_required')
                    return state
        sleep(min(.25,max(0.,deadlines.phase_end-time.monotonic())))
    require(False,'bounded_publication_observation_exhausted')


def run_controller(spec, deadlines, journal, ledger):
    output = Path(spec['output_dir'])
    require(not (output/'capture').exists() and not Path(spec['data_dir']).exists(), 'fresh_one_attempt_required')
    require(type(spec['settings']['api_key']) is str and 16 <= len(spec['settings']['api_key']) <= 128,
            'fixed_harness_bearer_required')
    require(spec['identical_key_replay'] is True, 'exactly_one_same_key_repeat_required')
    enter_phase(spec,deadlines,'preflight')
    prehash = verify_inventory(spec,deadlines,end=deadlines.phase_end)
    atomic_json(output/'prehash.json',prehash)
    proof,wav,pcm = fixed_wav(spec['input_wav'],deadlines.check,encoded_pin=True,pins=spec['input_pins'])
    journal.emit('fixed_input_verified',**proof)
    server = Server(spec,deadlines,journal,ledger,'capture')
    client = server.client
    require(server.ready['cache']['present'] and server.ready['cache']['entries'] == 0 and
            server.ready['jobs']['present'] and server.ready['jobs']['accepted_jobs'] == 0 and
            server.ready['cells']['ready'] and server.ready['cells']['enabled'], 'fresh_enabled_owners')
    no_new_calls(server.ready)
    session = client.json('POST','/sessions',json_body={'name':'Fixed dev7 cell API smoke','processing_mode':'legacy'},expected=201)
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
    state = await_cell_publication(server,deadlines)
    require(state['primary_cleanup']['confirmed'] is True, 'primary_original_cleanup_before_optional_required')
    zero_leases(state)
    source_session = client.json('GET',f'/sessions/{sid}')
    before = server.command('snapshot')
    base = f'/sessions/{sid}/native-owned-cells'
    page = cell_page(client,base)
    require(1 <= len(page['cells']) <= 2, 'one_or_two_genuine_selected_cells_required')
    details = []
    for index,row in enumerate(page['cells']):
        doc, exact, headers = cell_detail(client,base,row,page['jobs_epoch'])
        details.append({'document':doc, 'proof':exact, 'headers':headers})
        (output/f'public-cell-{index}-proof.json').write_bytes(exact)
        atomic_json(output/f'public-cell-{index}-headers.json',headers,4096)
    atomic_json(output/'public-list.json',page,65536)
    reconstruction = reconstruct_capture(spec,upload,page,details,deadlines)
    atomic_json(output/'independent-reconstruction.json',reconstruction,MIB)
    committed = details[0]['document']['commit_evidence']
    require(completed['metadata']['native_diarization'] == committed['native'] and
            completed['metadata']['core_cut'] == committed['core_cut'], 'public_committed_evidence_changed')
    selected = page['cells'][0]
    cell_id = selected['cell_id']
    jobs_base = f'/sessions/{sid}/moss-jobs'
    listing = client.json('GET',jobs_base,private=True,response_max=65536)
    require(listing['jobs'] == [] and listing['jobs_epoch'] == page['jobs_epoch'], 'fresh_jobs_ledger_required')
    request = {'jobs_epoch':listing['jobs_epoch'],'expected_cell_sequence':selected['sequence'],
        'expected_cell_revision':selected['revision'],'expected_proof_sha256':selected['proof_sha256'],
        'support_id':selected['support_id'],'profile':'handy-moss-q8_0-cpu-experimental-v1'}
    binding = {'origin':'durable_cell','cell_id':cell_id,'sequence':selected['sequence'],
        'revision':selected['revision'],'proof_sha256':selected['proof_sha256'],
        'support':details[0]['document']['support'],'proof_url':f'{base}/{cell_id}'}
    state = server.command('state');zero_leases(state)
    # Exactly one new durable-cell job; the unchanged direct launch freezes compute.
    key = 'dev7-one-durable-cell'
    submit_path = f'{base}/{cell_id}/moss-jobs'
    accepted = client.json('POST',submit_path,json_body=request,idempotency_key=key,expected=202,private=True,response_max=65536)
    jid = accepted['job_id']
    moss_window = server.wait_json(output/'moss-window.json',deadlines.phase_end)
    deadlines.enter('moss-compute',record=moss_window,spec_sha256=spec['_sha256'])
    require(accepted['jobs_epoch'] == listing['jobs_epoch'] and accepted['support_id'] == request['support_id'] and
            accepted['cell_binding'] == binding,'job_binding_mismatch')
    before_repeat = server.command('state')
    replay = client.json('POST',submit_path,json_body=request,idempotency_key=key,expected=200,private=True,response_max=65536)
    after_repeat = server.command('state')
    require(replay['job_id'] == jid and replay['cell_binding'] == binding and
            before_repeat['jobs']['accepted_jobs'] == after_repeat['jobs']['accepted_jobs'] == 1 and
            before_repeat['launches']['moss-direct'] == after_repeat['launches']['moss-direct'] == 1 and
            all(before_repeat['cell_pin_cleanup'][name] == after_repeat['cell_pin_cleanup'][name] == 1
                for name in ('acquire_entered','accepted_entered','acquired','accepted')), 'same_key_redispatch_or_binding_changed')
    terminal = None
    polls = 0
    for phase in ('moss-compute','moss-cleanup'):
        if phase == 'moss-cleanup':enter_phase(spec,deadlines,phase,server)
        while polls < 180 and deadlines.phase_end-time.monotonic() > .25:
            row = client.json('GET',f'{jobs_base}/{jid}',private=True,response_max=65536);polls += 1
            if row['process_cleanup_verified'] and not row['cleanup_pending']:
                terminal = row;break
            server.command('state',observe_moss=phase == 'moss-compute')
            time.sleep(min(2.,max(0.,deadlines.phase_end-time.monotonic()-.1)))
        if terminal is not None:break
    require(terminal is not None and terminal['state'] == 'returned_diagnostic' and
            terminal['raw_available'] and terminal['result_available'] and
            not terminal['memory_cleanup_pending'] and terminal['cell_binding'] == binding,
            'moss_success_not_terminal')
    enter_phase(spec,deadlines,'lifecycle',server)
    raw,headers = client.request('GET',f'{jobs_base}/{jid}/raw',private=True,response_max=131072)
    result = client.json('GET',f'{jobs_base}/{jid}/result',private=True,response_max=262144)
    require(headers.get('x-content-sha256') == sha(raw) and result['cell_binding'] == binding,'raw_result_cell_binding')
    (output/'moss-public-raw.bin').write_bytes(raw)
    atomic_json(output/'moss-public-result.json',result,262144)
    state = server.command('state')
    require(state['jobs']['accepted_jobs'] == 1 and state['launches']['moss-direct'] == 1,
            'one_accepted_job_one_original_launch')
    native = native_moss_receipt(state['moss_terminal'])
    require(native['raw_retained'] is True and native['raw_bytes'] == len(raw) and native['raw_sha256'] == sha(raw),
            'moss_raw_binding')
    pin_cleanup = cell_cleanup(state,cell_id,selected['proof_sha256'])
    require(pin_cleanup['copied_pcm_sha256'] == details[0]['document']['support']['pcm16le_sha256'],
            'moss_exact_copied_cell_pcm_binding')
    unchanged_cells(client,base,page,details,listing['jobs_epoch'])
    require(client.json('GET',f'/chunks/{cid}') == completed,'optional_actions_changed_source')
    assert_primary_equal(before,server.command('snapshot'))
    atomic_json(output/'api-job-proof.json',{'request':request,'idempotency_key':key,'accepted':accepted,
        'replay':replay,'terminal':terminal,'native_receipt':native,'cell_pin_cleanup':pin_cleanup,
        'before_repeat':before_repeat,'after_repeat':after_repeat,
        'counter_scope':'unchanged child-reported attempts; native forwards unknown',
        'state_after_result':state,'no_cancel_or_delete_performed':True},MIB)
    final = server.stop_and_prove()
    require(not final['cell_witness']['errors'], 'final_cell_observer_error')
    counts = verify_provider_counts(spec,deadlines)
    atomic_json(output/'verified-provider-counts.json',counts,2*MIB)
    require(all(final['counts']['rpc'][name]['entered'] == final['counts']['rpc'][name]['returned'] ==
                final['counts']['rpc'][name]['accepted'] == 1 and not final['counts']['rpc'][name]['rejected']
                for name in ('reset','push','finish','turns')), 'four_exact_native_RPCs_required')
    require(all(final['counts']['jobs'][name]['accepted'] == 1 for name in
                ('analyzer','commit','worker_start','cell_prepare','cell_committed')), 'single_primary_core_required')
    restart_epochs = []
    closed_before_off = None
    for mode in ('restart-on','restart-off'):
        restarted = Server(spec,deadlines,journal,ledger,mode)
        no_new_calls(restarted.ready)
        restart_before = restarted.command('snapshot')
        require(restarted.client.json('GET',f'/sessions/{sid}') == source_session and
                restarted.client.json('GET',f'/chunks/{cid}') == completed,'restart_extant_source_changed')
        if mode == 'restart-on':
            fresh = restarted.ready
            require(fresh['cache']['present'] and fresh['cache']['entries'] == 0 and
                    fresh['jobs']['present'] and fresh['jobs']['accepted_jobs'] == fresh['jobs']['key_count'] == 0 and
                    fresh['jobs']['jobs_epoch'] != listing['jobs_epoch'] and fresh['cells']['ready'],
                    'fresh_restart_owners_required')
            epoch = fresh['jobs']['jobs_epoch']; restart_epochs.append(epoch)
            reopened = unchanged_cells(restarted.client,base,page,details,epoch)
            fresh_jobs = restarted.client.json('GET',jobs_base,private=True,response_max=65536)
            require(fresh_jobs['jobs'] == [] and fresh_jobs['jobs_epoch'] == epoch,'old_jobs_or_keys_adopted')
            for suffix in ('','/result','/raw'):
                restarted.client.request('GET',f'{jobs_base}/{jid}{suffix}',expected=404,private=True,response_max=65536)
            # Old epoch/key is checked read-only; no submit is replayed on restart.
        else:
            require(not restarted.ready['cache']['present'] and not restarted.ready['jobs']['present'] and
                    not restarted.ready['cells']['enabled'],'OFF_owners_registered')
            for path in (base,f'{base}/{cell_id}',jobs_base,f'{jobs_base}/{jid}'):
                restarted.client.request('GET',path,expected=409,private=True,response_max=65536)
            reopened = None
        restart_after = restarted.command('snapshot')
        assert_primary_equal(restart_before,restart_after);no_new_calls(restart_after)
        stopped = restarted.stop_and_prove();no_new_calls(stopped)
        closed = closed_cell_snapshot(restarted,stopped,spec,deadlines,page,details,reconstruction)
        if mode == 'restart-on':closed_before_off = closed
        else:
            require(closed_before_off is not None and all(closed[name] == closed_before_off[name]
                for name in ('database_identity','namespace_identity','logical_sha256')),
                'OFF_restart_changed_closed_cell_lineage')
        atomic_json(output/f'{mode}-proof.json',{'final':stopped,'extant_source':completed,
            **({'closed_database_before':closed_before_off,'closed_database_after':closed,
                 'closed_database_logical_preservation':True} if mode == 'restart-off' else {}),
            'cells':reopened,'snapshot_before_reads':restart_before['snapshot'],
            'snapshot_after_reads':restart_after['snapshot']},MIB)
    atomic_json(output/'cell-lineage-restart.json',{'cells':reconstruction['cells'],
        'source_receipt_sha256':reconstruction['source_receipt_sha256'],'committed_core_frames':[0,FRAMES],
        'restart_jobs_epochs':restart_epochs,'extant_source_preserved':True,'cells_preserved':True,
        'ON_preservation_scope':'public cell API identity and canonical proof',
        'OFF_preservation_scope':'closed-database logical rows, proofs, PCM hashes and receipt lineage',
        'old_job_result_key_adoption':False,'restart_submits':0,'restart_provider_calls':0},MIB)
    # bootstrap.finalize owns one cleanup pass followed by mandatory full posthash.
    atomic_json(output/'capture-result.json',{'status':'COMPLETE','genuine_durable_cell_gate':True,'retained_restart_verified':True,'source_id':sid,'chunk_id':cid,'job_id':jid,
        'cell_id':cell_id,'fixed_input':proof,'actual_native_selected':True,'one_moss_job':True,
        'jobs_epoch':listing['jobs_epoch'],'restart_epochs':restart_epochs,
        'original_primary_unchanged_by_optional_actions':True,'committed_core_frames':[0,FRAMES],
        'selected_cell_frames':[0,CELL_FRAMES],'tail_retained':reconstruction['tail_retained'],
        'native_RPCs':final['counts']['rpc'],'primary_jobs':final['counts']['jobs'],
        'moss_child_reported_attempts':native,'public_requests':client.calls,
        'public_response_bytes':client.response_bytes,'inference_accuracy_claim':False,
        'EOS':'unknown','forensic_memory_erasure_claim':False,'restart_extant_source_tested':True,
        'native_inflight_claim':False,'upload_seam_claim':False,'epoch_transition_claim':False,
        'no_cancel_or_delete_performed':True},2*MIB)


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
    require(spec['schema']=='dev7-cell-api-immutable-inventory-v1' and
            spec['source_root']==str(root.parent/'UNQUALIFIED_PRODUCT_SOURCE_ROOT'),'hash_inventory_identity')
    expected=sha(canonical(sorted(spec['hash_inventory'],key=lambda row:row['path'])))
    require(spec['inventory_sha256']==expected,'canonical_inventory_digest')
    end=min(time.monotonic()+120.,work)
    result=verify_inventory(spec,SimpleNamespace(stop=stop),end=end)
    result.update(schema='dev7-cell-api-hash-terminal-v1',status='PASS_HASH_ONLY',
        inventory_sha256=expected,inventory_manifest_sha256=args.hash_inventory_sha256,
        source_freeze_sha256=spec['source_freeze_sha256'],
        product_imports=0,audio_decodes=0,services=0,model_calls=0,
        scope='Immutable byte hashing only; later authority and terminal metadata are separately outer-pinned')
    alive();out=run/'tmp/hash-only';out.mkdir(mode=0o700,exist_ok=False)
    atomic_json(out/'hash-only-result.json',result,65536);alive()
    print(json.dumps(result,sort_keys=True),flush=True)


def materialize_main():
    """Inert finite preparation mode. Requires a separately admitted guard run.

    Hash the entire original with a 64 KiB buffer, then copy only the fixed
    channel/frame interval. No decoding library, resampling, padding or models.
    Partial outputs are preserved and never silently reused or overwritten.
    """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--materialize-only',action='store_true',required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    original = root.parent/'UNQUALIFIED_PRIVATE_INPUT_ROOT/UNQUALIFIED_ORIGINAL_INPUT.wav'
    target = root/'input/primary-21s.wav'; receipt_path = root/'input/MATERIALIZATION.json'
    absolute = float(os.environ['GUARD_ABSOLUTE_END']); work = float(os.environ['GUARD_WORK_END'])
    require(work == absolute-60 and 0 < absolute-time.monotonic() <= 300, 'materialization_outer_clock')
    stop = Path(os.environ['GUARD_STOP_PATH']); run = Path(os.environ['GUARD_RUN_DIRECTORY'])
    require(run.is_absolute() and run.resolve() == run and run.parent == root/'runs' and
            run.name.startswith('run-') and len(run.name) == 12 and
            all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in run.name[4:]) and
            stop == run/'STOP', 'materialization_exact_run_scope')
    require(not any(os.path.lexists(path) for path in
            (target,receipt_path,receipt_path.with_name(receipt_path.name+'.pending'))), 'fresh_materialization_only')
    def alive():
        require(time.monotonic() < work and not stop.exists(), 'materialization_work_or_stop')
    index_raw = bounded_read(run/'index.json',65536,alive)
    index = json.loads(index_raw)
    spec_raw = bounded_read(root/'materialize.spec.json',4*MIB,alive)
    materialize_spec = json.loads(spec_raw)
    python = root.parent/'UNQUALIFIED_RUNTIME_ROOT/bin/python3.12'
    argv = [str(python),'-I','-B','-S',str(Path(__file__).resolve()),'--materialize-only']
    from ownership import proc
    identity = proc(os.getpid())
    require(index['status'] == 'GATED' and index['run_directory'] == str(run) and
            index['stop_path'] == str(stop) and index['spec_sha256'] == sha(spec_raw) and
            index['argv'] == materialize_spec['argv'] == argv and
            index['original']['pid'] == identity['pid'] and
            index['original']['start_ticks'] == identity['start_ticks'] and
            index['supervisor']['pid'] == identity['ppid'] and
            index['caps']['work_seconds'] == 240 and index['caps']['absolute_seconds'] == 300 and
            materialize_spec['stage'] == 'api_smoke_materialize' and
            materialize_spec['cwd'] == os.getcwd() == str(root) and
            materialize_spec['input_target'] == str(target) and
            materialize_spec['materialization_target'] == str(receipt_path) and
            materialize_spec['original_bytes'] == ORIGINAL_BYTES and
            materialize_spec['original_frames'] == list(ORIGINAL_INTERVAL) and
            materialize_spec['channel'] == 0 and materialize_spec['actual_run_authorized'] is False,
            'materialization_guard_original_invocation_binding')
    signature = lambda st:(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
    fd = os.open(original,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size == ORIGINAL_BYTES and
                original.resolve() == original, 'fixed_original_file_shape')
        digest, count = hashlib.sha256(), 0
        while block := os.read(fd,65536):
            alive(); count += len(block);require(count <= ORIGINAL_BYTES,'original_hash_growth');digest.update(block)
        require(count == ORIGINAL_BYTES and digest.hexdigest() == ORIGINAL_SHA, 'full_original_hash_pin')
        require(signature(before) == signature(os.fstat(fd)) == signature(original.stat(follow_symlinks=False)),
                'original_changed_during_hash')
        header = os.pread(fd,65536,0);alive()
        require(len(header) == 65536 and header[:4] == b'RIFF' and header[8:12] == b'WAVE' and
                struct.unpack_from('<I',header,4)[0]+8 == ORIGINAL_BYTES,'original_RIFF_geometry')
        at, format_seen, data_offset = 12, False, None
        for _ in range(128):
            require(at+8 <= len(header),'original_header_capacity')
            kind,size = struct.unpack_from('<4sI',header,at); start,end = at+8,at+8+size
            require(end+(size&1) <= ORIGINAL_BYTES,'original_chunk_extent')
            if kind == b'fmt ':
                require(not format_seen and size == 40 and end <= len(header) and
                        struct.unpack_from('<HHIIHH',header,start) == (65534,8,16000,256000,16,16) and
                        struct.unpack_from('<HH',header,start+16) == (22,16) and
                        header[start+24:start+40] == bytes.fromhex('0100000000001000800000aa00389b71'),
                        'fixed_original_extensible_PCM16_geometry')
                format_seen = True
            elif kind == b'data':
                require(format_seen and start == 80 and size == 'UNQUALIFIED_INPUT_PCM_BYTES' and end == ORIGINAL_BYTES,
                        'fixed_original_data_extent')
                data_offset = start;break
            at = end+(size&1)
        require(data_offset == 80 and ORIGINAL_INTERVAL[1]-ORIGINAL_INTERVAL[0] == FRAMES,
                'fixed_original_interval')
        require(target.parent.resolve() == target.parent and not target.parent.is_symlink(), 'materialization_directory_alias')
        target.parent.mkdir(mode=0o700,exist_ok=True)
        wav_header = struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',36+2*FRAMES,b'WAVE',b'fmt ',16,
                                 1,1,16000,32000,2,16,b'data',2*FRAMES)
        pcm_hash, float_hash, wav_hash = hashlib.sha256(),hashlib.sha256(),hashlib.sha256(wav_header)
        with target.open('xb') as out:
            out.write(wav_header)
            for frame in range(ORIGINAL_INTERVAL[0],ORIGINAL_INTERVAL[1],4096):
                alive();frames = min(4096,ORIGINAL_INTERVAL[1]-frame)
                block = os.pread(fd,frames*16,data_offset+frame*16)
                require(len(block) == frames*16,'original_bounded_extract_short_read')
                mono = bytearray(frames*2);mono[0::2] = block[0::16];mono[1::2] = block[1::16]
                encoded = bytes(mono);out.write(encoded);pcm_hash.update(encoded);wav_hash.update(encoded)
                float_hash.update(b''.join(struct.pack('<f',sample[0]/32768.) for sample in struct.iter_unpack('<h',encoded)))
            require(out.tell() == 672044,'canonical_materialized_WAV_bytes')
            out.flush();os.fsync(out.fileno())
        require(signature(before) == signature(os.fstat(fd)) == signature(original.stat(follow_symlinks=False)),
                'original_changed_during_extract')
        pins = {'frames':FRAMES,'wav_sha256':wav_hash.hexdigest(),'pcm16_sha256':pcm_hash.hexdigest(),
                'float32_sha256':float_hash.hexdigest()}
        checked,_,_ = fixed_wav(target,alive,encoded_pin=True,pins=pins)
        require(checked == pins and target.stat().st_size == 672044,'materialized_readback_pin')
        receipt = {'schema':'dev7-fixed-input-materialization-v1','status':'MATERIALIZED_HASHED',
            'guard_spec_sha256':sha(spec_raw),'guard_index_sha256':sha(index_raw),
            'guard_run_directory':str(run),'original_process_identity':identity,
            'original_path':str(original),'original_sha256':ORIGINAL_SHA,'original_bytes':ORIGINAL_BYTES,
            'original_frames':list(ORIGINAL_INTERVAL),'channel':0,'input_pins':pins,
            'output_path':str(target),'output_bytes':672044,'sample_rate':16000,'channels':1,'sample_bytes':2,
            'streaming_hash_buffer_bytes':65536,'extract_buffer_bytes':65536,
            'no_resampling_padding_looping':True,'independent_pin_review_required':True,
            'actual_execution_authorized':False}
        alive();atomic_json(receipt_path,receipt,65536);alive()
        print(json.dumps(receipt,sort_keys=True),flush=True)
    finally:os.close(fd)


if __name__=='__main__':
    if '--materialize-only' in sys.argv[1:]:materialize_main()
    else:hash_only_main()
