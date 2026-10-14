# Unexecuted portable template; generate configuration placeholders before use.
"""One dormant genuine two-upload seam gate; no forced inference or selection."""
from __future__ import annotations
import hashlib
import http.client
import json
import math
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
from common import (FRAMES, CELL_FRAMES, MIB, require, bounded_read,
                    atomic_json, fixed_wav, sha, canonical)


class Client:
    def __init__(self, port, token, deadlines, journal, maximum=384, budget=None):
        self.port, self.token, self.deadlines, self.journal = port, token, deadlines, journal
        self.calls = 0
        self.response_bytes = 0
        self.polls = 0
        self.maximum = maximum
        self.budget = {'calls':0,'response_bytes':0} if budget is None else budget

    def request(self, method, path, *, body=None, json_body=None, auth=True, expected=200, private=False, idempotency_key=None, response_max=8*MIB):
        self.deadlines.check()
        require(type(response_max) is int and 1 <= response_max <= 8*MIB, 'HTTP_response_limit')
        require(self.calls < self.maximum and path.startswith('/') and len(path) <= 4096, 'HTTP_request_cap')
        require(self.budget['calls'] < 384, 'HTTP_shared_request_cap')
        self.calls += 1; self.budget['calls'] += 1
        headers = {'Connection': 'close'}
        if idempotency_key is not None:
            require((method == 'PUT' and '/chunks?' in path or method == 'POST' and path.endswith('/moss-jobs')) and
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
            require(idempotency_key in ('upload-a','upload-b','upload-overlap'), 'closed_upload_key')
            headers['Content-Type'] = 'audio/wav'
        if method == 'POST' and path.endswith('/moss-jobs'):
            require(type(body) is bytes and len(body) <= 1024,'moss_request_1024_byte_cap')
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
            self.budget['response_bytes'] += len(data)
            require(self.budget['response_bytes'] <= 16*MIB, 'HTTP_aggregate_response_cap')
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

    A generated configuration supplies the exact closed inventory. No
    wildcard pin generation or accepting the current bytes occurs during execution.
    """
    items = spec['hash_inventory']
    require(type(items) is list and 1 <= len(items) <= 32768, 'full_hash_inventory_required')
    required = set(spec['installed_files']) | set(spec['runtime_files']) | set(spec['harness_files'])
    require(required <= {item['path'] for item in items}, 'full_hash_missing_execution_files')
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
    require(type(spec['input_uploads']) is dict and set(spec['input_uploads']) == {'a','b'} and
            {spec['input_wav'],*spec['input_uploads'].values()} <= seen, 'full_hash_missing_input_pair')
    from common import validate_input_pins
    pins = validate_input_pins(spec['input_pins'])
    by_path = {row['path']:row for row in items}
    for role,path in [('source',spec['input_wav']),('upload_a',spec['input_uploads']['a']),('upload_b',spec['input_uploads']['b'])]:
        require(by_path[path]['sha256'] == pins[role]['wav_sha256'] and
                by_path[path]['bytes'] == 44+2*pins[role]['frames'], 'full_hash_input_role_pin')
    source = Path(spec['source_root'])
    actual_source = set()
    for root, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [d for d in dirs if Path(root) != source or d != '.git']
        require(not any((Path(root)/d).is_symlink() for d in dirs), 'source_directory_alias')
        for name in files:
            actual_source.add(str(Path(root)/name))
            require(len(actual_source) <= spec['source_file_count'], 'source_file_count_grew')
    require(len(actual_source) == spec['source_file_count'] and actual_source == set(spec['source_files']) and actual_source <= seen,
            'qualified_source_inventory_changed')
    require(sum(by_path[path]['bytes'] for path in actual_source) == spec['source_file_bytes'],'qualified_source_bytes_changed')
    for alias in spec.get('runtime_aliases', []):
        path = Path(alias['path'])
        require(path.is_symlink() and os.readlink(path) == alias['literal_target'] and
                str(path.resolve()) == alias['resolved_target'] and str(path.resolve()) in seen, 'runtime_alias_changed')
    require(time.monotonic() < end and not deadlines.stop.exists(), 'full_hash_final_deadline')
    return {'files': len(items), 'bytes': total, 'elapsed_seconds': time.monotonic()-began,
            'all_matched': True, 'source_files':spec['source_file_count'], 'input_files':3, 'input_uploads':2,
            'inventory_sha256':sha(canonical(sorted(items,key=lambda row:row['path'])))}


class Server:
    def __init__(self, spec, deadlines, journal, ledger, mode):
        self.spec, self.deadlines, self.journal, self.ledger = spec, deadlines, journal, ledger
        self.mode = mode
        self.stopped_and_reaped = False
        self.stop_attempted = False
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
        self.client = Client(ready['port'], spec['settings']['api_key'], deadlines, journal,384 if mode == 'capture' else 32,
                             budget=spec.setdefault('_http_budget',{'calls':0,'response_bytes':0}))

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
        self.stop_attempted = True
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
    require(value['counts'].get('nonempty_claims',0) == 0,'unexpected_restart_nonempty_claim')
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
    """Read the complete at-most-three-cell result through bounded real pagination."""
    rows, after, epoch = [], 0, expected_epoch
    for _ in range(3):
        query = {} if after == 0 else {'after':after, 'expected_jobs_epoch':epoch}
        if epoch is not None:query['expected_jobs_epoch'] = epoch
        page = client.json('GET', base + ('?' + urlencode(query) if query else ''),
                           private=True, response_max=65536)
        require(type(page['cells']) is list and type(page['has_more']) is bool,
                'bounded_cell_page_shape')
        if epoch is None:epoch = page['jobs_epoch']
        require(page['jobs_epoch'] == epoch and len(rows) + len(page['cells']) <= 3,
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
    from audio_backend.native_owned_cells import validate_document
    proof, headers = client.request('GET', f"{base}/{row['cell_id']}",
                                    private=True, response_max=65536)
    doc, support, _ = validate_document(proof)
    require(sha(proof) == row['proof_sha256'] and
            row['cell_id'] == sha(canonical({'policy':doc['policy'], 'support':support.to_dict()})) and
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
    """Inherited isolated v1 partition regression; unused by the genuine seam gate."""
    require(type(partitions) is list and len(partitions) == 2 and
            all(type(row) is dict and type(row.get('selected')) is bool for row in partitions) and
            [row.get('frames') for row in partitions] == [[0,CELL_FRAMES],[CELL_FRAMES,FRAMES]],
            'exact_committed_cell_partition_required')
    require(partitions[0]['selected'], 'substantial_selected_cell_required')
    expected = [row['frames'] for row in partitions if row['selected']]
    require(type(retained_bounds) is list and retained_bounds == expected,
            'selected_retained_partition_mismatch')
    return {'tail_retained':partitions[1]['selected'], 'committed_core_frames':[0,FRAMES]}


def validate_seam_geometry(doc):
    """Finite actual-profile geometry, separate from product proof validation."""
    require(type(doc) is dict and doc.get('schema') == 'native-owned-seam-composite-v2', 'required_v2_composite')
    require(all(type(doc['scope'][k]) is int for k in ('epoch_index','reset_generation')) and
        all(type(x) is int for x in doc['geometry'].values()) and
        all(type(doc['support'][k]) is int for k in ('source_frames','core_start_frame','core_end_frame','start_frame','end_frame')),
        'strict_seam_frame_types')
    require(doc['scope']['stream_id'] == 'seam-a' and doc['scope']['origin_seconds_hex'] == float(64).hex() and
        doc['scope']['epoch_index'] == 0 and doc['scope']['reset_generation'] == 1,'one_original_seam_scope')
    require(doc['geometry'] == {'seam_frame':168000,'left_frames':160000,'right_frames':160000,
        'start_frame':8000,'end_frame':328000},'fixed_two_contribution_geometry')
    support = doc['support']
    require([support[k] for k in ('source_frames','core_start_frame','core_end_frame','start_frame','end_frame')] ==
        [336000,0,336000,8000,328000], 'two_core_support_envelope')
    require(len(doc['commits']) == len(doc['dependencies']) == len(doc['native_witnesses']) == 2,
        'exact_two_sources_commits_witnesses')
    require(len({c['chunk_id'] for c in doc['commits']}) == 2 and
        len({p['receipt_sha256'] for p in doc['dependencies']}) == 2,'distinct_original_sources_receipts')
    for index,(commit,part) in enumerate(zip(doc['commits'],doc['dependencies'])):
        require(all(type(commit[k]) is int for k in ('source_origin_frame','local_start_frame','local_end_frame',
            'logical_start_frame','logical_end_frame')) and
            all(type(part[k]) is int for k in ('source_frames','source_start','source_end','logical_start','logical_end')),
            'strict_commit_and_contribution_frame_types')
        require([commit[k] for k in ('source_origin_frame','local_start_frame','local_end_frame',
            'logical_start_frame','logical_end_frame')] == [index*168000,0,168000,index*168000,(index+1)*168000],
            'actual_two_disjoint_commit_geometry')
        require(part['chunk_id'] == commit['chunk_id'] and part['source_frames'] == 168000 and
            [part[k] for k in ('source_start','source_end','logical_start','logical_end')] ==
            ([8000,168000,8000,168000] if index == 0 else [0,160000,168000,328000]),
            'actual_positive_source_contribution')
    b = doc['commits'][1]
    require(type(doc['publication_anchor']['start']) is int and type(doc['publication_anchor']['end']) is int and
        doc['publication_anchor'] == {'chunk_id':b['chunk_id'],'start':0,'end':168000,
        'sha256':b['evidence_sha256']},'original_B_publication_anchor')
    require(all(type(v) is int for k in ('frame_range','coverage_envelope_frames') for v in doc['coverage'][k]) and
        doc['coverage']['kind'] == 'seam_overlap_alternative' and
        doc['coverage']['frame_range'] == [8000,328000] and
        doc['coverage']['coverage_envelope_frames'] == [0,336000] and
        doc['coverage']['source_support_core_semantics'] == 'two_committed_core_envelope' and
        doc['coverage']['adds_primary_audio'] is False and doc['coverage']['automatic_dispatch'] is False,
        'diagnostic_only_two_core_coverage')
    return True


def same_response_seam_overlap(responses):
    """Strict crossing overlap in ONE original response, never a response union."""
    require(type(responses) is list and len(responses) == 2,'two_original_turn_responses')
    for response_index,response in enumerate(responses):
        require(type(response) is dict and set(response) == {'turns'} and
            type(response['turns']) is list and len(response['turns']) <= 256,'bounded_original_turn_response')
        turns = response['turns']
        for turn in turns:
            require(type(turn) is dict and set(turn) == {'start','end','speaker_local_id'} and
                type(turn['speaker_local_id']) is str and 1 <= len(turn['speaker_local_id']) <= 128 and
                all(type(turn[k]) in (int,float) and math.isfinite(turn[k]) for k in ('start','end')) and
                0 <= turn['start'] < turn['end'] <= 21., 'strict_finite_original_turns')
        for i,one in enumerate(turns):
            for j in range(i+1,len(turns)):
                two = turns[j]
                lo = max(round(one['start']*16000),round(two['start']*16000))
                hi = min(round(one['end']*16000),round(two['end']*16000))
                if one['speaker_local_id'] != two['speaker_local_id'] and lo < 168000 < hi:
                    return {'response_index':response_index,'turn_indexes':[i,j],
                        'overlap_frames':[lo,hi],'seam_frame':168000,
                        'labels':[one['speaker_local_id'],two['speaker_local_id']],
                        'original_response_sha256':sha(canonical(response)),
                        'same_original_response':True,'selected_pair_may_differ':True}
    require(False,'missing_same_response_overlap_crossing_seam')


def validate_dual_authority(value):
    require(value['session_valid'] is True and len(value['sources']) == 2 and
        all(s['receipt_valid'] is True and s['receipt'] is not None and s['row'] is not None
            for s in value['sources']) and
        len({s['receipt_id'] for s in value['sources']}) == 2,'two_current_source_receipts_required')
    composed = value['composite']
    require(composed is not None and type(composed['commit_current']) is list and
        len(composed['commit_current']) == 2 and all(v is True for v in composed['commit_current']) and
        composed['adjacent'] is True and composed['composed_current'] is True,
        'both_current_commits_and_composed_authority_required')
    return True


def validate_primary_counts(counts, launches):
    for name,expected in {'reset':1,'push':2,'finish':1,'turns':2}.items():
        row = counts['rpc'][name]
        require(all(type(row[k]) is int for k in ('entered','returned','accepted','exceptions','rejected')),'strict_RPC_counter_types')
        require(row['entered'] == row['returned'] == row['accepted'] == expected and
            row['exceptions'] == row['rejected'] == 0,'six_exact_native_RPCs_required')
    for name,expected in {'analyzer':2,'commit':2,'worker_start':1,'cell_prepare':2,
                         'cell_committed':2,'analyzer_process_constructor':3}.items():
        row = counts['jobs'][name]
        require(all(type(row[k]) is int for k in ('entered','returned','accepted','exceptions','rejected')),'strict_job_counter_types')
        require(row['entered'] == row['returned'] == row['accepted'] == expected and
            row['exceptions'] == row['rejected'] == 0,'exact_primary_job_counts')
    require(type(counts['nonempty_claims']) is int and counts['nonempty_claims'] == 3,'exact_three_nonempty_claims')
    require(all(type(launches.get(k)) is int for k in ('audio_backend.native_runner','audio_backend.runner','audio_backend.guard')),
        'strict_launch_counter_types')
    require(launches.get('audio_backend.native_runner') == 1 and
        launches.get('audio_backend.runner') == 3 and
        launches.get('audio_backend.guard') == 9,'exact_primary_process_launches')
    return True


def validate_job_replay(before, after, accepted, replay, binding):
    require(all(type(state['jobs']['accepted_jobs']) is int and type(state['launches']['moss-direct']) is int and
        all(type(state['cell_pin_cleanup'][key]) is int for key in
            ('acquire_entered','accepted_entered','acquired','accepted','rejected_acquisitions'))
        for state in (before,after)), 'strict_replay_counter_types')
    require(replay['job_id'] == accepted['job_id'] and replay['cell_binding'] == binding and
        before['jobs']['accepted_jobs'] == after['jobs']['accepted_jobs'] == 1 and
        before['launches']['moss-direct'] == after['launches']['moss-direct'] == 1,
        'same_key_job_or_dispatch_changed')
    require(before['cell_pin_cleanup'] == after['cell_pin_cleanup'] or all(
        before['cell_pin_cleanup'][name] == after['cell_pin_cleanup'][name]
        for name in ('acquire_entered','accepted_entered','acquired','accepted','rejected_acquisitions')),
        'same_key_reacquisition')
    return True


def validate_delete_observation(authority, source_paths_absent):
    require(all(type(authority[k]) is int for k in ('cleanup_pending','chunk_count','stream_receipt_rows')) and
        type(source_paths_absent) is list and len(source_paths_absent) == 2 and all(type(v) is bool for v in source_paths_absent),
        'strict_delete_observation_types')
    require(authority['session_valid'] is False and authority['session_tombstoned'] is True and
        authority['cleanup_pending'] == authority['chunk_count'] == authority['stream_receipt_rows'] == 0 and
        len(authority['sources']) == 2 and all(row['row'] is None and row['receipt'] is None and
            row['receipt_id'] is None and row['receipt_valid'] is False for row in authority['sources']) and
        source_paths_absent == [True,True], 'final_grouped_session_revocation_incomplete')
    return True


def reconstruct_capture(spec, uploads, page, details, deadlines):
    """Reconstruct v1 and v2 from original RPCs, two commits and split PCM."""
    import dataclasses
    from audio_backend.native_owned_cells import (RollingWitness, SourceSpan, make_cells,
        intersect_mapping, float32_digest)
    from audio_backend.native_owned_seam_cells import Piece, make_composite, is_composite
    output = Path(spec['output_dir']);pins = spec['input_pins']
    pcms = {}; records = {}; precommits = {};receipts = []
    for index,role in enumerate(('a','b')):
        _,_,pcm = fixed_wav(spec['input_uploads'][role],deadlines.check,encoded_pin=True,
            role='upload_'+role,pins=pins['upload_'+role]);pcms[role] = pcm
        _,_,native_pcm = fixed_wav(output/f'native-submitted-{role}.wav',deadlines.check,
            role='upload_'+role,pins=pins['upload_'+role])
        require(native_pcm == pcm,'original_native_split_PCM_mismatch')
        _,_,analyzer_pcm = fixed_wav(output/f'analyzer-submitted-{role}.wav',deadlines.check,
            role='analyzer_'+role,pins=pins['analyzer_'+role]);pcms['analyzer_'+role] = analyzer_pcm
        recorded = json.loads(bounded_read(output/f'cell-witness-{role}.json',MIB,deadlines.check))
        require(recorded.get('schema') == 'fresh-seam-cell-witness-v1', 'fresh_witness_schema')
        precommit = json.loads(bounded_read(output/f'precommit-{role}.json',MIB,deadlines.check))
        records[role] = recorded;precommits[role] = precommit
        chunk = recorded['chunk'];public = uploads[role]['chunk']
        require(chunk['id'] == public['id'] and chunk['session_id'] == public['session_id'] and
            chunk['digest'] == pins['upload_'+role]['wav_sha256'] and chunk['duration'] == 10.5 and
            chunk['timeline_start'] == (64.,74.5)[index] and chunk['next_offset'] == 0,
            'original_claim_public_source_binding')
        require(recorded['offset'] == 0. and recorded['end'] == 10.5 and recorded['scope_start'] == 64. and
            recorded['origin_frame'] == index*168000 and recorded['analyzer_start'] == (0,136000)[index] and
            recorded['reset_generation'] == 1 and recorded['commit_returned_true'] is True and
            recorded['committed_called'] is True,'observed_two_original_true_commit_geometry')
        require(len(recorded['receipts']) == 1,'one_receipt_per_original_commit')
        receipt = recorded['receipts'][0];receipts.append(receipt)
        require(receipt['receipt_id'] == sha(canonical(receipt['receipt'])) and all(
            receipt['receipt'][key] == value for key,value in {'session_id':chunk['session_id'],
            'chunk_id':chunk['id'],'digest':pins['upload_'+role]['wav_sha256'],'frames':168000,
            'data_offset':44,'size':336044,'timeline_start':(64.,74.5)[index],'stream_id':'seam-a'}.items()),
            'original_split_receipt_binding')
        evidence = {'chunk_id':chunk['id'],'start':0,'end':168000,
            'native':recorded['native']['state'],'core_cut':precommit['result']['metadata']['core_cut']}
        require(precommit['chunk'] == chunk and precommit['offset'] == 0. and precommit['end'] == 10.5 and
            precommit['result']['metadata']['native_diarization'] == evidence['native'] and
            recorded['commit_evidence'] == evidence and recorded['commit_sha256'] == sha(canonical(evidence)),
            'original_two_commit_evidence_binding')
    full = pcms['a']+pcms['b']
    # The product helper deliberately refuses >20 seconds; source qualification is WAV-local.
    _, _, source_pcm = fixed_wav(spec['input_wav'], deadlines.check, encoded_pin=True,
                                 role='source', pins=pins['source'])
    require(full == source_pcm and len(full) == 2*FRAMES, 'source_upload_PCM_concatenation')
    require(pcms['analyzer_a'] == full[:400000] and pcms['analyzer_b'] == full[272000:] and
        pcms['analyzer_a'][272000:] == pcms['analyzer_b'][:128000], 'two_context_original_PCM_overlap')

    witness = RollingWitness();reconstructed = [];pieces = [];responses = []
    for index,action in enumerate(('reset','push','push','finish','turns','turns'),1):
        wire = json.loads(bounded_read(output/f'native-rpc-{index}-{action}.json',MIB,deadlines.check))
        if action == 'push':
            role = 'a' if index == 2 else 'b';start = 0 if role == 'a' else 168000
            witness.observe_pcm(start,start+168000,pcms[role])
        witness.observe(wire);require(witness.failure is None,'original_native_witness_incomplete')
        if action != 'turns':continue
        role = 'a' if index == 5 else 'b';ci = index-5;recorded = records[role];chunk = recorded['chunk']
        require(wire == json.loads(bounded_read(output/f'native-turns-{role}.json',MIB,deadlines.check)),
            'original_native_turns_artifact_binding');responses.append(wire)
        parts = tuple(SourceSpan(**part) for part in recorded['mapping'])
        require(len(parts) == 1 and parts[0].chunk_id == chunk['id'] and
            parts[0].receipt_sha256 == receipts[ci]['receipt_id'] and
            parts[0].source_sha256 == pins['upload_'+role]['wav_sha256'] and
            parts[0].session_id == chunk['session_id'] and parts[0].source_frames == 168000 and
            (parts[0].source_start,parts[0].source_end,parts[0].logical_start,parts[0].logical_end) ==
            (0,168000,ci*168000,(ci+1)*168000),'original_disjoint_source_mapping')
        cells = make_cells(witness=witness,native=recorded['native'],scope_start=64.,origin_frame=ci*168000,
            core_start=ci*168000,core_end=(ci+1)*168000,analyzer_start=(0,136000)[ci],
            analyzer_pcm=pcms['analyzer_'+role],mapping=parts,chunk_id=chunk['id'],commit_start=0,
            commit_end=168000,commit_sha256=recorded['commit_sha256'],commit_evidence=recorded['commit_evidence'])
        require(len(cells) <= 1,'one_v1_cell_per_core_bound');reconstructed.extend(cells)
        turns,native_proof = witness.prepared(recorded['native'],64.,ci*168000)
        scope = {'session_id':chunk['session_id'],'stream_id':'seam-a','origin_seconds_hex':float(64).hex(),
            'epoch_id':native_proof['epoch_id'],'epoch_index':0,'reset_generation':1}
        commit = {'chunk_id':chunk['id'],'source_origin_frame':ci*168000,'local_start_frame':0,
            'local_end_frame':168000,'logical_start_frame':ci*168000,'logical_end_frame':(ci+1)*168000,
            'evidence':recorded['commit_evidence'],'evidence_sha256':recorded['commit_sha256']}
        lo,hi = ((8000,168000),(168000,328000))[ci];pcm = full[lo*2:hi*2]
        native_witness = {'commit_index':0,'query_start_frame':(0,136000)[ci],
            'query_end_frame':(200000,336000)[ci],'analyzer_start_frame':(0,136000)[ci],
            'analyzer_end_frame':(200000,336000)[ci],'analyzer_pcm16le_sha256':sha(pcms['analyzer_'+role]),
            'piece_pcm16le_sha256':sha(pcm),'native':native_proof,
            'turns':[{'label':t.label,'original':[t.start,t.end]} for t in turns]}
        part = intersect_mapping(parts,lo,hi)[0];piece = Piece(scope,commit,native_witness,part,pcm);pieces.append(piece)
        observed_piece = recorded['seam_pieces']['tail' if ci == 0 else 'prefix']
        require(observed_piece == {'scope':scope,'commit':commit,'witness':native_witness,
            'part':dataclasses.asdict(part),'pcm_sha256':sha(pcm),'frames':160000},
            'independent_original_seam_piece_mismatch')
    require(witness.finished and witness.ring_end == FRAMES,'true_original_native_EOF')
    crossing = same_response_seam_overlap(responses)
    composite = make_composite(*pieces);reconstructed.append(composite)
    doc = json.loads(composite.proof);validate_seam_geometry(doc)
    require(composite.pcm == full[16000:656000] and sha(composite.pcm) == pins['composite']['pcm16_sha256'],
        'independent_composite_PCM')
    require(float32_digest(composite.pcm) == pins['composite']['float32_sha256'], 'composite_float32_digest')
    expected = {cell.cell_id:cell for cell in reconstructed}
    require(len(expected) == len(page['cells']) and 1 <= len(expected) <= 3 and
        set(expected) == {r['cell_id'] for r in page['cells']},'all_actual_retained_cells_reconstructed')
    candidates = {row['cell_id']:row for record in records.values() for row in record['candidates']}
    require(set(candidates) == set(expected),'original_publication_candidates_changed')
    lineage = []
    for row,detail in zip(page['cells'],details):
        cell = expected[row['cell_id']];candidate = candidates[cell.cell_id]
        proof = bounded_read(output/candidate['proof_artifact'],65536,deadlines.check)
        pcm = bounded_read(output/candidate['pcm_artifact'],640000,deadlines.check)
        require(proof == cell.proof == detail['proof'] and pcm == cell.pcm and
            sha(proof) == candidate['proof_sha256'] == row['proof_sha256'] and
            sha(pcm) == candidate['pcm_sha256'] == cell.support.pcm16le_sha256 and
            candidate['support'] == cell.support.to_dict() and
            sha(b''.join(struct.pack('<f',x[0]/32768.) for x in struct.iter_unpack('<h',pcm))) ==
            cell.support.float32le_sha256,'independent_actual_cell_material_mismatch')
        lineage.append({'cell_id':cell.cell_id,'sequence':row['sequence'],'revision':row['revision'],
            'proof_sha256':sha(proof),'pcm_sha256':sha(pcm),'support':cell.support.to_dict()})
    selected = [i for i,detail in enumerate(details) if is_composite(detail['document']) and
        detail['proof'] == composite.proof]
    require(len(selected) == 1,'one_natural_production_composite')
    return {'exact_source_core_witness_reconstruction':True,'cells':lineage,
        'composite_cell_id':composite.cell_id,'source_receipt_sha256':[r['receipt_id'] for r in receipts],
        'source_receipts':receipts,'committed_core_frames':[[0,168000],[168000,336000]],
        'selected_cell_frames':[8000,328000],'same_response_overlap':crossing,
        'analyzer_context_frames':[[0,200000],[136000,336000]],'repeated_context_frames':64000,
        'lexical_seam_observations':[{k:precommits[r]['result']['metadata'].get(k) for k in
            ('boundary_alignment_matches','boundary_alignment_used','boundary_policy')} for r in ('a','b')],
        'semantic_dedup_claim':False,'session_seconds':[64.5,84.5],
        'source_wav_local_frames':[8000,328000],'upload_seam_claim':True,'epoch_transition_claim':False}


def verify_provider_counts(spec, deadlines):
    output = Path(spec['output_dir'])
    native = list(output.glob('native-*-counts.json'));analyzers = list(output.glob('analyzer-*-counts.json'))
    require(len(native) == 1 and len(analyzers) == 3,'exact_provider_counter_files_required')
    n = json.loads(bounded_read(native[0],MIB,deadlines.check))
    for name,count in {'native_model_create':1,'native_stream_open':1,'native_push':12,
        'native_finish':1,'native_segments':4}.items():
        row = n['abi'][name]
        require(row['entered'] == row['returned'] == row['accepted'] == count and
            row['rejected'] == row['exceptions'] == 0,'native_ABI_terminal_counts')
    require(n['native_push_frames'] == [32000,32000,32000,32000,32000,8000]*2 and
        all(type(x) is int for x in n['native_push_frames']) and n['native_frames'] == FRAMES and n['native_float32_sha256'] ==
        spec['input_pins']['source']['float32_sha256'],'native_ABI_split_concatenation_pin')
    caps = {'event_compute':4,'embedding_compute':8,'sensevoice_decode':32,
        'event_constructor':1,'embedding_constructor':1,'sensevoice_constructor':1}
    aggregate = {name:0 for name in caps};values = [];waiting = 0;streams = 0
    for path in analyzers:
        a = json.loads(bounded_read(path,MIB,deadlines.check));values.append(a)
        for name,cap in caps.items():
            row = a['sdk'][name]
            require(row['entered'] == row['returned'] == row['accepted'] <= cap and
                row['exceptions'] == row['rejected'] == 0,'per_child_SDK_terminal_counts')
            aggregate[name] += row['entered']
        lifecycle = a['lifecycle']['analyzer_constructor']
        require(lifecycle['entered'] == lifecycle['returned'] == lifecycle['accepted'] == 1 and
            lifecycle['rejected'] == lifecycle['exceptions'] == 0,'three_genuine_AudioAnalyzer_constructions')
        inference = sum(a['sdk'][name]['entered'] for name in ('event_compute','embedding_compute','sensevoice_decode'))
        require(a['streams_created'] <= 44 and a['unreleased_observed_streams'] == 0,'per_child_stream_cap')
        streams += a['streams_created']
        if inference == 0:
            waiting += 1
            require(a['streams_created'] == 0 and all(a['sdk'][name]['entered'] == 0 for name in caps),
                'waiting_analyzer_must_have_no_SDK_work')
        else:
            require(a['sdk']['sensevoice_decode']['entered'] >= 1 and
                a['sdk']['sensevoice_constructor']['entered'] == 1,'both_calls_genuine_SenseVoice_required')
    require(waiting == 1 and streams <= 88 and all(aggregate[name] <= cap*2 for name,cap in caps.items()),
        'aggregate_SDK_caps_not_multiplied_by_three')
    guards = list(output.glob('guard-*-terminal.json'));programs = []
    require(len(guards) == 9,'exact_guard_terminal_count')
    for path in guards:
        value = json.loads(bounded_read(path,128*1024,deadlines.check))
        require(value['status'] == 'COMPLETE' and value['cleanup'] == 'CONFIRMED' and
            len(value['non_python_children']) == 1,'guard_original_program_terminal')
        programs.append(Path(value['non_python_children'][0]['program']).name)
    require(programs.count('ffprobe') == 3 and programs.count('ffmpeg') == 6,'exact_probe_extract_entries')
    return {'native':n,'analyzers':values,'aggregate_SDK':aggregate,'streams':streams,
        'waiting_analyzers_zero_inference':waiting,'ffprobe_entries':3,'ffmpeg_entries':6,
        'Sherpa_inference_entries':sum(aggregate[k] for k in ('event_compute','embedding_compute','sensevoice_decode')),
        'hidden_native_model_forward_counts':'unknown_not_exposed'}


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
    require(receipt['acquire_entered'] == 2 and receipt['accepted_entered'] == 1 and
        receipt['rejected_acquisitions'] == 1 and receipt['rejected_pins_acquired'] == receipt['rejected_pins_released'],
        'one_accepted_and_one_rejected_cell_acquisition')
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
    read-only, length-first, bounded by three cells, two receipts and a VM clock.
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
        expected_receipts = reconstruction.get('source_receipts')
        source_count = len(expected_receipts) if expected_receipts is not None else 1
        require(1 <= source_count <= 2,'closed_source_receipt_count')
        dependency_count = sum(len(d['document']['dependencies']) for d in details)
        require(db.execute('SELECT COUNT(*) FROM cells').fetchone()[0] == len(page['cells']) and
            db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == source_count and
            db.execute('SELECT COUNT(*) FROM dependencies').fetchone()[0] == dependency_count and
            db.execute('SELECT COUNT(*) FROM store_meta').fetchone()[0] == 1,'closed_cell_exact_row_counts')
        lengths = db.execute("""SELECT seq,length(CAST(cell_id AS BLOB)),length(CAST(ownership AS BLOB)),
            length(CAST(sid AS BLOB)),length(CAST(cid AS BLOB)),length(proof),length(pcm),
            length(CAST(state AS BLOB)),revision FROM cells ORDER BY seq LIMIT 4""").fetchall()
        require(len(lengths) == len(page['cells']) <= 3,'closed_cell_row_limit')
        observed = []
        for index,row in enumerate(lengths):
            expected = page['cells'][index];doc = details[index]['document']
            require(row[0] == expected['sequence'] and row[1] == row[2] == 64 and
                1 <= row[3] <= 128 and 1 <= row[4] <= 128 and 1 <= row[5] <= 65536 and
                row[6] == 2*(expected['end_frame']-expected['start_frame']) <= 640000 and
                1 <= row[7] <= 32 and row[8] == expected['revision'],'closed_cell_row_lengths')
            saved = db.execute('SELECT cell_id,ownership,sid,cid,state,revision,proof FROM cells WHERE seq=?',(row[0],)).fetchone()
            cell_id,ownership,sid,cid,state,revision,proof = saved
            charge(proof);charge(canonical(list(saved[:-1])))
            composite = doc.get('schema') == 'native-owned-seam-composite-v2'
            anchor = doc['publication_anchor'] if composite else doc['commit']
            if composite:
                scope = doc['scope']
                expected_ownership = sha(canonical({'schema':'two-commit-seam-ownership-v2',
                    'session_id':scope['session_id'],'stream_id':scope['stream_id'],'epoch_id':scope['epoch_id'],
                    'commits':[[c['chunk_id'],c['local_start_frame'],c['local_end_frame']] for c in doc['commits']],
                    'geometry':doc['geometry'],'policy':doc['policy']}))
            else:
                expected_ownership = sha(canonical({'session':sid,'chunk':cid,
                    'core':[anchor['start'],anchor['end']],
                    'cell':[expected['start_frame'],expected['end_frame']]}))
            require(cell_id == expected['cell_id'] and proof == details[index]['proof'] and state == 'retained' and
                sid == doc['dependencies'][0]['session_id'] and cid == anchor['chunk_id'] and
                ownership == expected_ownership,'closed_cell_row_binding')
            digest = hashlib.sha256()
            for at in range(0,row[6],65536):
                block = db.execute('SELECT substr(pcm,?,?) FROM cells WHERE seq=?',
                    (at+1,min(65536,row[6]-at),row[0])).fetchone()[0]
                require(type(block) is bytes and len(block) == min(65536,row[6]-at),'closed_cell_pcm_read_length')
                digest.update(charge(block))
            require(digest.hexdigest() == doc['support']['pcm16le_sha256'],'closed_cell_pcm_hash')
            lengths_dep = db.execute("""SELECT position,length(CAST(cid AS BLOB)),length(CAST(digest AS BLOB)),
                length(CAST(receipt_id AS BLOB)) FROM dependencies WHERE cell_id=? ORDER BY position LIMIT 3""",(cell_id,)).fetchall()
            require(len(lengths_dep) == len(doc['dependencies']) <= 2 and all(
                dep[0] == position and 1 <= dep[1] <= 128 and dep[2:] == (64,64)
                for position,dep in enumerate(lengths_dep)),'closed_cell_dependency_lengths')
            dependencies = []
            for position,part in enumerate(doc['dependencies']):
                dep = db.execute('SELECT cid,digest,receipt_id,source_start,source_end,logical_start,logical_end FROM dependencies WHERE cell_id=? AND position=?',(cell_id,position)).fetchone()
                charge(canonical(list(dep)))
                require(list(dep) == [part[k] for k in ('chunk_id','source_sha256','receipt_sha256',
                    'source_start','source_end','logical_start','logical_end')],'closed_cell_dependency_binding')
                dependencies.append(list(dep))
            observed.append({'sequence':row[0],'cell_id':cell_id,'ownership':ownership,'sid':sid,'cid':cid,
                'state':state,'revision':revision,'proof_sha256':sha(proof),'pcm_sha256':digest.hexdigest(),
                'pcm_bytes':row[6],'dependencies':dependencies})
        lengths_source = db.execute('SELECT length(CAST(cid AS BLOB)),length(CAST(sid AS BLOB)),length(CAST(digest AS BLOB)),length(receipt) FROM sources ORDER BY cid LIMIT 3').fetchall()
        require(len(lengths_source) == source_count and all(1 <= x[0] <= 128 and 1 <= x[1] <= 128 and
            x[2] == 64 and 1 <= x[3] <= 1024 for x in lengths_source),'closed_source_receipt_lengths')
        sources = []
        for cid,sid,digest,receipt in db.execute('SELECT cid,sid,digest,receipt FROM sources ORDER BY cid LIMIT 3'):
            charge(receipt);charge(canonical([cid,sid,digest]));receipt_hash = sha(receipt)
            if expected_receipts is not None:
                matching = [r for r in expected_receipts if r['receipt']['chunk_id'] == cid]
                require(len(matching) == 1 and matching[0]['receipt_id'] == receipt_hash and
                    matching[0]['receipt'] == json.loads(receipt) and matching[0]['receipt']['digest'] == digest,
                    'closed_two_source_receipt_binding')
            else:
                require(cid == observed[0]['cid'] and sid == observed[0]['sid'] and
                    digest == spec['input_pins']['wav_sha256'] and
                    receipt_hash == reconstruction['source_receipt_sha256'],'closed_source_receipt_binding')
            sources.append({'chunk_id':cid,'session_id':sid,'digest':digest,'receipt_sha256':receipt_hash})
        meta_lengths = db.execute("""SELECT length(CAST(schema AS BLOB)),length(CAST(policy AS BLOB)),
            length(CAST(primary_identity AS BLOB)),length(CAST(qualification AS BLOB)),
            length(CAST(namespace_identity AS BLOB)),length(CAST(database_identity AS BLOB)) FROM store_meta WHERE id=1""").fetchone()
        require(meta_lengths is not None and all(type(n) is int and 1 <= n <= 8192 for n in meta_lengths),
                'closed_cell_meta_lengths')
        meta = db.execute('SELECT schema,policy,primary_identity,qualification,namespace_identity,database_identity FROM store_meta WHERE id=1').fetchone()
        meta_hash = sha(charge(canonical(list(meta))))
        body = {'cells':observed,'sources':sources,
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
    """Observe original publication completion and handoff release, never operate it."""
    require(type(maximum) is int and 1 <= maximum <= 64,'publication_observation_cap')
    for _ in range(maximum):
        deadlines.check();state = server.command('state');witness = state['cell_witness']
        require(not witness['errors'],'cell_observer_error')
        records = witness['records']
        if len(records) == 2 and all(row['committed_called'] for row in records):
            ready = True
            for index,row in enumerate(records):
                require(row['prepared'] and row['commit_returned_true'] and
                    row['core_frames'] == [[0,168000],[168000,336000]][index],
                    'two_genuine_original_true_commits_required')
                publication = row['publication']
                if publication is None:ready = False;continue
                if publication['kind'] == 'dict':
                    require(index == 0 and row['seam_reason'] == 'seam_tail_promoted' and
                        publication['status'] == 'no_native_overlap','only_unselected_A_no_publication')
                    continue
                require(publication['kind'] == 'Operation' and publication['operation_kind'] == 'publish',
                    'original_publish_operation_required')
                if publication['done']:
                    require(publication['result']['status'] == 'durable','original_cell_publication_failed')
                ready = ready and publication['done'] and publication['slot_released']
            if ready and state['cells']['handoffs_active_or_pending'] == 0:return state
        sleep(min(.25,max(0.,deadlines.phase_end-time.monotonic())))
    require(False,'bounded_publication_observation_exhausted')


def run_controller(spec, deadlines, journal, ledger):
    output = Path(spec['output_dir'])
    active = None; active_mode = None
    try:
        require(not (output/'capture').exists() and not Path(spec['data_dir']).exists(),'fresh_one_attempt_required')
        require(type(spec['settings']['api_key']) is str and 16 <= len(spec['settings']['api_key']) <= 128,
            'fixed_harness_bearer_required')
        require(spec['identical_key_replay'] is True and spec['settings']['native_owned_seam_cells_enabled'] is True,
            'exact_seam_profile_required')
        prehash = spec.get('_prehash_result')
        require(type(prehash) is dict and prehash['all_matched'] is True and prehash['source_files'] == spec['source_file_count'] and
            deadlines.phase == 'preflight','shared_before_producer_prehash_required')
        atomic_json(output/'prehash.json',prehash)
        waves = {};proofs = {}
        for role in ('a','b'):
            proof,wav,_ = fixed_wav(spec['input_uploads'][role],deadlines.check,encoded_pin=True,
                role='upload_'+role,pins=spec['input_pins']['upload_'+role])
            waves[role] = wav;proofs[role] = proof
        require(proofs['a']['wav_sha256'] != proofs['b']['wav_sha256'],'fixed_digest_conflict_prerequisite_failed')
        active = None; active_mode = 'capture'
        server = active = Server(spec,deadlines,journal,ledger,'capture');client = server.client
        require(server.ready['cache']['present'] and server.ready['cache']['entries'] == 0 and
            server.ready['jobs']['present'] and server.ready['jobs']['accepted_jobs'] == 0 and
            server.ready['cells']['ready'] and server.ready['cells']['enabled'],'fresh_enabled_owners')
        no_new_calls(server.ready)
        session = client.json('POST','/sessions',json_body={'name':'fresh-seam-v1',
            'processing_mode':'legacy'},expected=201);sid = session['id']
        def upload_path(role, start=None):
            return f'/sessions/{sid}/chunks?'+urlencode({'timeline_start':(64. if role == 'a' else 74.5) if start is None else start,
                'stream_id':'seam-a','stream_final':'false' if role == 'a' else 'true'})
        uploads = {};matrix = []
        before_a = server.command('state')
        uploads['a'] = client.json('PUT',upload_path('a'),body=waves['a'],idempotency_key='upload-a',expected=202)
        aid = uploads['a']['chunk']['id']
        require(uploads['a']['duplicate'] is False and uploads['a']['chunk']['duration'] == 10.5,'fresh_A_required')
        primary = server.wait_json(output/'primary-window.json',deadlines.phase_end)
        deadlines.bind_primary(primary,spec['_sha256'])
        waiting = None
        for _ in range(24):
            row = client.json('GET',f'/chunks/{aid}')
            if row['status'] == 'waiting_input':waiting = row;break
            require(row['status'] in ('queued','running'),'A_failed_before_waiting')
            time.sleep(min(5.,max(0.,deadlines.phase_end-time.monotonic())))
        require(waiting is not None and waiting['next_offset'] == 0,'A_must_wait_without_commit')
        waiting_state = None
        for _ in range(20):
            current = server.command('state')
            if current['active_chunk'] is None:waiting_state = current;break
            time.sleep(.1)
        require(waiting_state is not None and waiting_state['counts']['nonempty_claims'] == 1 and
            waiting_state['counts']['rpc']['reset']['accepted'] == waiting_state['counts']['rpc']['push']['accepted'] == 1 and
            waiting_state['counts']['rpc']['finish']['entered'] == waiting_state['counts']['rpc']['turns']['entered'] == 0 and
            waiting_state['counts']['jobs']['analyzer']['entered'] == waiting_state['counts']['jobs']['commit']['entered'] == 0 and
            waiting_state['counts']['jobs']['analyzer_process_constructor']['accepted'] == 1,'exact_waiting_A_work')
        first_authority = server.command('authority',session_id=sid,chunk_ids=[aid])['authority']
        require(first_authority['sources'][0]['receipt_valid'] and first_authority['chunk_count'] ==
            first_authority['stream_receipt_rows'] == 1 and len(first_authority['receipt_registry']) ==
            len(first_authority['uploaded_files']) == 1,'durable_A_receipt_before_matrix')
        matrix.append({'case':'A_fresh','status':202,'response':uploads['a'],'before':before_a,'after':waiting_state})
        def stable_primary(value):
            return {k:v for k,v in value['counts'].items() if k != 'empty_claims'}
        for case,body,key,path,status,probe_delta in (
            ('A_exact_replay',waves['a'],'upload-a',upload_path('a'),202,0),
            ('A_digest_conflict',waves['b'],'upload-a',upload_path('a'),409,0),
            ('A_metadata_conflict',waves['a'],'upload-a',upload_path('a',64.5),409,0),
            ('new_key_overlap',waves['a'],'upload-overlap',upload_path('a'),409,1)):
            before = server.command('state')
            response = client.json('PUT',path,body=body,idempotency_key=key,expected=status)
            after = server.command('state')
            require(stable_primary(before) == stable_primary(after),'idempotency_matrix_requeued_or_produced')
            require(all(after['launches'][name] == count+(probe_delta if name == 'audio_backend.guard' else 0)
                for name,count in before['launches'].items()),'upload_duplicate_probe_or_inference_drift')
            authority = server.command('authority',session_id=sid,chunk_ids=[aid])['authority']
            require(authority == first_authority,'same_key_or_overlap_changed_original_receipt')
            if status == 202:
                require(response['duplicate'] is True and response['chunk']['id'] == aid,'A_replay_original_chunk')
            matrix.append({'case':case,'status':status,'response':response,'before':before,'after':after,
                'original_authority':authority})
        before_b = server.command('state')
        uploads['b'] = client.json('PUT',upload_path('b'),body=waves['b'],idempotency_key='upload-b',expected=202)
        bid = uploads['b']['chunk']['id'];ids = [aid,bid]
        require(uploads['b']['duplicate'] is False and bid != aid and uploads['b']['chunk']['duration'] == 10.5,
            'fresh_distinct_B_required')
        matrix.append({'case':'B_fresh','status':202,'response':uploads['b'],'before':before_b,
            'after':server.command('state')})
        completed = {};polls = 0
        while polls < 156 and len(completed) < 2:
            for role,cid in zip(('a','b'),ids):
                if role in completed or polls >= 156:continue
                row = client.json('GET',f'/chunks/{cid}');polls += 1
                require(row['status'] not in ('failed','cancelled','paused'),'primary_split_capture_failed')
                if row['status'] == 'completed':
                    require(row['next_offset'] == 10.5,'complete_original_local_core_required');completed[role] = row
            if len(completed) < 2:time.sleep(min(5.,max(0.,deadlines.phase_end-time.monotonic())))
        require(len(completed) == 2,'bounded_two_core_primary_poll_exhausted')
        for role,row in completed.items():atomic_json(output/f'terminal-chunk-{role}.json',row,MIB)
        for _ in range(20):
            current = server.command('state')
            if current['active_chunk'] is None:break
            time.sleep(.1)
        require(current['active_chunk'] is None,'worker_return_not_observed')
        enter_phase(spec,deadlines,'support',server)
        state = await_cell_publication(server,deadlines)
        require(state['primary_cleanup']['confirmed'] is True,'primary_original_cleanup_before_optional_required')
        zero_leases(state);validate_primary_counts(state['counts'],state['launches'])
        before_replay = server.command('state')
        b_replay = client.json('PUT',upload_path('b'),body=waves['b'],idempotency_key='upload-b',expected=202)
        after_replay = server.command('state')
        require(b_replay['duplicate'] is True and b_replay['chunk']['id'] == bid and
            stable_primary(before_replay) == stable_primary(after_replay) and
            before_replay['launches'] == after_replay['launches'],'post_completion_B_replay_requeued_or_produced')
        matrix.append({'case':'B_exact_replay','status':202,'response':b_replay,
            'before':before_replay,'after':after_replay})
        require(len(matrix) == 7,'exact_seven_upload_requests')
        atomic_json(output/'source-uploads.json',{'session':session,'uploads':uploads,'input_pins':proofs},MIB)
        atomic_json(output/'upload-idempotency.json',{'requests':matrix,'waiting_A':waiting,
            'original_A_authority':first_authority,'exact_PUTs':7},MIB)
        source_session = client.json('GET',f'/sessions/{sid}');before = server.command('snapshot')
        base = f'/sessions/{sid}/native-owned-cells';page = cell_page(client,base)
        require(1 <= len(page['cells']) <= 3,'bounded_genuine_selected_cells_required')
        details = []
        for index,row in enumerate(page['cells']):
            doc,exact,headers = cell_detail(client,base,row,page['jobs_epoch'])
            details.append({'document':doc,'proof':exact,'headers':headers})
            with (output/f'public-cell-{index}-proof.json').open('xb') as file:file.write(exact)
            atomic_json(output/f'public-cell-{index}-headers.json',headers,4096)
        atomic_json(output/'public-list.json',page,65536)
        reconstruction = reconstruct_capture(spec,uploads,page,details,deadlines)
        atomic_json(output/'independent-reconstruction.json',reconstruction,MIB)
        atomic_json(output/'seam-overlap-witness.json',reconstruction['same_response_overlap'],65536)
        matches = [(row,detail) for row,detail in zip(page['cells'],details)
            if row['cell_id'] == reconstruction['composite_cell_id'] and
            detail['document'].get('schema') == 'native-owned-seam-composite-v2']
        require(len(matches) == 1,'select_composite_by_kind_proof_scope_geometry')
        selected,selected_detail = matches[0];doc = selected_detail['document'];validate_seam_geometry(doc)
        cell_id = selected['cell_id']
        authority = server.command('authority',session_id=sid,chunk_ids=ids,cell_id=cell_id)['authority']
        validate_dual_authority(authority)
        for role,commit in zip(('a','b'),doc['commits']):
            require(completed[role]['metadata']['native_diarization'] == commit['evidence']['native'] and
                completed[role]['metadata']['core_cut'] == commit['evidence']['core_cut'],'public_original_commit_binding')
        atomic_json(output/'dual-authority-positive.json',authority,65536)
        jobs_base = f'/sessions/{sid}/moss-jobs'
        listing = client.json('GET',jobs_base,private=True,response_max=65536)
        require(listing['jobs'] == [] and listing['jobs_epoch'] == page['jobs_epoch'],'fresh_jobs_ledger_required')
        basic = {'jobs_epoch':listing['jobs_epoch'],'expected_cell_sequence':selected['sequence'],
            'expected_cell_revision':selected['revision'],'expected_proof_sha256':selected['proof_sha256'],
            'support_id':selected['support_id'],'profile':'handy-moss-q8_0-cpu-experimental-v1'}
        request = dict(basic,coverage_intent='overlapping_diagnostic')
        binding = {'origin':'durable_cell','cell_id':cell_id,'sequence':selected['sequence'],
            'revision':selected['revision'],'proof_sha256':selected['proof_sha256'],'support':doc['support'],
            'proof_url':f'{base}/{cell_id}','coverage':doc['coverage']}
        submit_path = f'{base}/{cell_id}/moss-jobs';negatives = []
        for key,body,reason in (('seam-moss-missing',basic,'coverage_intent_required'),
            ('seam-moss-wrong',dict(basic,coverage_intent='primary'),'invalid_body')):
            prior = server.command('state')
            rejected = client.json('POST',submit_path,json_body=body,idempotency_key=key,
                expected=409,private=True,response_max=65536)
            require(rejected.get('detail') == reason,'exact_coverage_intent_rejection')
            released = None
            for _ in range(16):
                latest = server.command('state')
                require(latest['jobs']['accepted_jobs'] == latest['launches']['moss-direct'] == 0,
                    'rejected_intent_dispatched_provider')
                pin = latest['cell_pin_cleanup']
                if latest['cells']['pins'] == latest['cells']['handoffs_active_or_pending'] == 0 and                 pin['rejected_pins_acquired'] == pin['rejected_pins_released']:
                    released = latest;break
                time.sleep(.05)
            require(released is not None,'rejected_request_pin_cleanup_incomplete')
            if reason == 'invalid_body':
                require(prior['cell_pin_cleanup'] == released['cell_pin_cleanup'],'wrong_literal_not_parser_rejected')
            negatives.append({'key':key,'request':body,'response':rejected,'before':prior,'after':released})
        zero_leases(server.command('state'))
        key = 'seam-moss-a'
        accepted = client.json('POST',submit_path,json_body=request,idempotency_key=key,
            expected=202,private=True,response_max=65536);jid = accepted['job_id']
        moss_window = server.wait_json(output/'moss-window.json',deadlines.phase_end)
        deadlines.enter('moss-compute',record=moss_window,spec_sha256=spec['_sha256'])
        require(accepted['jobs_epoch'] == listing['jobs_epoch'] and accepted['support_id'] == request['support_id'] and
            accepted['cell_binding'] == binding,'diagnostic_job_binding_mismatch')
        before_repeat = server.command('state')
        replay = client.json('POST',submit_path,json_body=request,idempotency_key=key,expected=200,private=True,response_max=65536)
        after_repeat = server.command('state');validate_job_replay(before_repeat,after_repeat,accepted,replay,binding)
        omitted = client.json('POST',submit_path,json_body=basic,idempotency_key=key,expected=409,private=True,response_max=65536)
        after_omission = server.command('state')
        require(omitted.get('detail') == 'idempotency_conflict','same_success_key_omitted_intent_conflict')
        validate_job_replay(after_repeat,after_omission,accepted,replay,binding)
        terminal = None;polls = 0
        for phase in ('moss-compute','moss-cleanup'):
            if phase == 'moss-cleanup':enter_phase(spec,deadlines,phase,server)
            while polls < 180 and deadlines.phase_end-time.monotonic() > .25:
                row = client.json('GET',f'{jobs_base}/{jid}',private=True,response_max=65536);polls += 1
                if row['process_cleanup_verified'] and not row['cleanup_pending']:terminal = row;break
                server.command('state',observe_moss=phase == 'moss-compute')
                time.sleep(min(2.,max(0.,deadlines.phase_end-time.monotonic()-.1)))
            if terminal is not None:break
        require(terminal is not None and terminal['state'] == 'returned_diagnostic' and terminal['raw_available'] and
            terminal['result_available'] and not terminal['memory_cleanup_pending'] and
            terminal['cell_binding'] == binding,'moss_success_not_terminal')
        enter_phase(spec,deadlines,'lifecycle',server)
        raw,headers = client.request('GET',f'{jobs_base}/{jid}/raw',private=True,response_max=131072)
        with (output/'moss-public-raw.bin').open('xb') as file:file.write(raw)
        require(headers.get('x-content-sha256') == sha(raw),'moss_raw_first_binding')
        result = client.json('GET',f'{jobs_base}/{jid}/result',private=True,response_max=262144)
        require(result['cell_binding'] == binding,'moss_result_coverage_binding')
        atomic_json(output/'moss-public-result.json',result,262144)
        state = server.command('state');native = native_moss_receipt(state['moss_terminal'])
        require(state['jobs']['accepted_jobs'] == state['launches']['moss-direct'] == 1 and
            native['raw_retained'] is True and native['raw_bytes'] == len(raw) and native['raw_sha256'] == sha(raw),
            'one_actual_MOSS_raw_binding')
        pin_cleanup = cell_cleanup(state,cell_id,selected['proof_sha256'])
        require(pin_cleanup['copied_pcm_sha256'] == doc['support']['pcm16le_sha256'],'exact_composite_dispatch_PCM')
        unchanged_cells(client,base,page,details,listing['jobs_epoch'])
        require(all(client.json('GET',f'/chunks/{cid}') == completed[role] for role,cid in zip(('a','b'),ids)),
            'optional_actions_changed_original_sources')
        after_optional = server.command('snapshot');assert_primary_equal(before,after_optional)
        atomic_json(output/'api-job-proof.json',{'request':request,'idempotency_key':key,'accepted':accepted,
            'negative_requests':negatives,'replay':replay,'omitted_intent_conflict':omitted,'terminal':terminal,
            'native_receipt':native,'cell_pin_cleanup':pin_cleanup,'before_repeat':before_repeat,
            'after_repeat':after_repeat,'after_omission':after_omission,'state_after_result':state,
            'counter_scope':'unchanged child-reported attempts; hidden forwards unknown'},MIB)
        final = server.stop_and_prove();require(not final['cell_witness']['errors'],'final_cell_observer_error')
        validate_primary_counts(final['counts'],final['launches'])
        counts = verify_provider_counts(spec,deadlines);atomic_json(output/'verified-provider-counts.json',counts,2*MIB)
        closed_capture = closed_cell_snapshot(server,final,spec,deadlines,page,details,reconstruction)
        active = None; active_mode = 'restart-off'
        off = active = Server(spec,deadlines,journal,ledger,'restart-off');no_new_calls(off.ready)
        require(not off.ready['cache']['present'] and not off.ready['jobs']['present'] and
            not off.ready['cells']['enabled'],'OFF_owners_enabled')
        require(off.client.json('GET',f'/sessions/{sid}') == source_session and all(
            off.client.json('GET',f'/chunks/{cid}') == completed[role] for role,cid in zip(('a','b'),ids)),
            'OFF_extant_sources_changed')
        for path in (base,f'{base}/{cell_id}',jobs_base,f'{jobs_base}/{jid}'):
            off.client.request('GET',path,expected=409,private=True,response_max=65536)
        off_snapshot = off.command('snapshot');assert_primary_equal(before,off_snapshot);no_new_calls(off_snapshot)
        off_final = off.stop_and_prove();no_new_calls(off_final)
        closed_off = closed_cell_snapshot(off,off_final,spec,deadlines,page,details,reconstruction)
        require(all(closed_capture[k] == closed_off[k] for k in ('database_identity','namespace_identity','logical_sha256')),
            'OFF_restart_changed_closed_cell_lineage')
        atomic_json(output/'restart-off-proof.json',{'final':off_final,'closed_database_before':closed_capture,
            'closed_database_after':closed_off,'closed_database_logical_preservation':True,
            'snapshot':off_snapshot['snapshot']},MIB)
        active = None; active_mode = 'restart-on'
        on = active = Server(spec,deadlines,journal,ledger,'restart-on');no_new_calls(on.ready)
        fresh = on.ready
        require(fresh['cache']['present'] and fresh['cache']['entries'] == 0 and fresh['jobs']['present'] and
            fresh['jobs']['accepted_jobs'] == fresh['jobs']['key_count'] == 0 and
            fresh['jobs']['jobs_epoch'] != listing['jobs_epoch'] and fresh['cells']['ready'],'fresh_ON_owners_required')
        epoch = fresh['jobs']['jobs_epoch'];reopened = unchanged_cells(on.client,base,page,details,epoch)
        require(on.client.json('GET',f'/sessions/{sid}') == source_session and all(
            on.client.json('GET',f'/chunks/{cid}') == completed[role] for role,cid in zip(('a','b'),ids)),
            'ON_extant_sources_changed')
        fresh_jobs = on.client.json('GET',jobs_base,private=True,response_max=65536)
        require(fresh_jobs['jobs'] == [] and fresh_jobs['jobs_epoch'] == epoch,'old_volatile_jobs_adopted')
        for suffix in ('','/result','/raw'):
            on.client.request('GET',f'{jobs_base}/{jid}{suffix}',expected=404,private=True,response_max=65536)
        on_authority = on.command('authority',session_id=sid,chunk_ids=ids,cell_id=cell_id)['authority']
        validate_dual_authority(on_authority)
        require(on_authority['sources'] == authority['sources'],'ON_receipt_or_commit_lineage_changed')
        pre_delete = on.command('snapshot');assert_primary_equal(before,pre_delete);no_new_calls(pre_delete)
        source_paths = [Path(row['row']['path']) for row in on_authority['sources']]
        deletion = on.client.json('DELETE',f'/sessions/{sid}')
        require(deletion.get('deleted') == sid,'one_final_public_session_DELETE')
        denied = {}
        for path in (f'/sessions/{sid}',f'/chunks/{aid}',f'/chunks/{bid}',base,f'{base}/{cell_id}'):
            denied[path] = on.client.json('GET',path,expected=404,private=path.startswith(base),response_max=65536)
        deleted_authority = None;after_delete = None
        for _ in range(16):
            observed = on.command('authority',session_id=sid,chunk_ids=ids)
            candidate = observed['authority'];absent = [not os.path.lexists(path) for path in source_paths]
            if (candidate['cleanup_pending'] == 0 and candidate['chunk_count'] == 0 and
                candidate['stream_receipt_rows'] == 0 and candidate['receipt_registry'] == candidate['uploaded_files'] == [] and
                all(r['receipt'] is None for r in candidate['sources']) and absent == [True,True] and
                not observed['cells']['cleanup_pending'] and
                observed['cells']['pins'] == observed['cells']['handoffs_active_or_pending'] == 0):
                validate_delete_observation(candidate,absent);deleted_authority = candidate;after_delete = observed;break
            time.sleep(.1)
        require(deleted_authority is not None,'final_DELETE_cleanup_honestly_pending_or_incomplete')
        no_new_calls(after_delete);on_final = on.stop_and_prove();no_new_calls(on_final)
        atomic_json(output/'restart-on-proof.json',{'final':on_final,'cells_before_delete':reopened,
            'current_authority_before_delete':on_authority,'snapshot_before_delete':pre_delete['snapshot'],
            'old_job_404_scope':'volatile-ledger non-adoption only; not revocation','seam_production_enabled':False},MIB)
        atomic_json(output/'session-delete-proof.json',{'request_count':1,'response':deletion,
            'public_denials':denied,'terminal_authority':deleted_authority,'source_paths_absent':[True,True],
            'actual_independent_per_upload_revocation':False,'both_sources_revoked_together':True,
            'original_completed_job_postdelete_raw_result_actual':False,'no_repeat_DELETE_or_repair':True},MIB)
        atomic_json(output/'primary-equality.json',{'before_optional':before['snapshot'],
            'after_optional':after_optional['snapshot'],'OFF_extant':off_snapshot['snapshot'],
            'ON_pre_delete':pre_delete['snapshot'],'all_equal_before_intentional_final_DELETE':True},65536)
        atomic_json(output/'cell-lineage-restart.json',{'cells':reconstruction['cells'],
            'source_receipt_sha256':reconstruction['source_receipt_sha256'],'committed_core_frames':[[0,168000],[168000,336000]],
            'restart_order':['restart-off','restart-on'],'restart_jobs_epochs':[epoch],
            'extant_source_preserved_before_final_DELETE':True,'old_job_result_key_adoption':False,
            'restart_submits':0,'restart_provider_calls':0,'final_grouped_session_revocation':True},MIB)
        atomic_json(output/'capture-result.json',{'status':'PROVISIONAL_CAPTURE_COMPLETE','genuine_durable_cell_gate':True,
            'retained_restart_verified':True,'source_id':sid,'chunk_ids':ids,'job_id':jid,'cell_id':cell_id,
            'fixed_input':proofs,'actual_native_selected':True,'one_moss_job':True,
            'jobs_epoch':listing['jobs_epoch'],'restart_epochs':[epoch],'real_commit_returns':2,
            'same_response_overlap':True,'current_authorities':2,'session_revocation':True,
            'actual_independent_per_upload_revocation':False,'original_completed_job_postdelete_raw_result_actual':False,
            'original_primary_unchanged_by_optional_actions':True,'committed_core_frames':[[0,168000],[168000,336000]],
            'selected_cell_frames':[8000,328000],'native_RPCs':final['counts']['rpc'],
            'primary_jobs':final['counts']['jobs'],'moss_child_reported_attempts':native,
            'public_requests':spec['_http_budget']['calls'],'public_response_bytes':spec['_http_budget']['response_bytes'],
            'inference_accuracy_claim':False,'EOS':'unknown','forensic_memory_erasure_claim':False,
            'restart_extant_source_tested':True,'native_inflight_claim':False,'upload_seam_claim':True,
            'epoch_transition_claim':False,'intentional_final_session_DELETE':True},2*MIB)

    except BaseException as producer_error:
        # One initialized owner only. Existing per-call and immutable phase clocks
        # still bound this cooperative attempt; bootstrap always owns forced cleanup.
        stop = {'mode':active_mode,'status':'NOT_INITIALIZED'}
        if active is not None:
            if active.stopped_and_reaped:
                stop['status'] = 'ALREADY_STOPPED'
            elif active.stop_attempted:
                stop['status'] = 'ALREADY_ATTEMPTED'
            else:
                try:
                    deadlines.check()
                    active.stop_and_prove()
                    stop['status'] = 'STOPPED'
                except BaseException as stop_error:
                    stop.update(status='FAILED',exception=type(stop_error).__name__,detail=str(stop_error)[:500])
        try:journal.emit('controller_abort_stop',**stop)
        except BaseException:pass
        try:
            atomic_json(output/'capture-result.json',{'status':'INCOMPLETE',
                'error':type(producer_error).__name__,'detail':str(producer_error)[:500],
                'no_retry_or_sample_substitution':True,'cooperative_stop':stop},65536)
        except BaseException:pass
        raise


def split_role_intervals():
    return {'source':(0,336000),'upload_a':(0,168000),'upload_b':(168000,336000),
        'analyzer_a':(0,200000),'analyzer_b':(136000,336000),'composite':(8000,328000)}


def canonical_pcm16_header(frames):
    require(type(frames) is int and frames in (168000,200000,320000,336000),'closed_PCM16_header_geometry')
    return struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',36+2*frames,b'WAVE',b'fmt ',16,
        1,1,16000,32000,2,16,b'data',2*frames)


def split_block_contributions(at, block):
    require(type(at) is int and 0 <= at < 336000 and type(block) is bytes and
        0 < len(block) <= 32768 and len(block)%2 == 0 and at+len(block)//2 <= 336000,
        'bounded_split_PCM_block_geometry')
    end = at+len(block)//2
    return {role:block[2*(max(at,lo)-at):2*(min(end,hi)-at)]
        for role,(lo,hi) in split_role_intervals().items() if max(at,lo) < min(end,hi)}


def check_split_targets(targets, receipt_path):
    require(not any(os.path.lexists(path) for path in (*targets.values(),receipt_path,
        receipt_path.with_name(receipt_path.name+'.pending'))),'fresh_materialization_only')


def materialize_main():
    """Dormant separate split phase: one source pass, two exclusive outputs, no decoder."""
    from binding import (load_config, decode, ordinary_path, subject_digest)
    from ownership import proc,key
    import resource
    config_path=os.environ.get('FRESH_SEAM_CONFIG')
    require(config_path is not None,'separate_split_config_required')
    config_raw,config=load_config(config_path,os.environ.get('FRESH_SEAM_CONFIG_SHA256'))
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and
        Path(sys.executable).resolve()==Path(config['paths']['python']),'split_isolated_current_interpreter')
    root=Path(config['paths']['harness_root']).parent
    require(root==Path(__file__).resolve().parents[1],'split_harness_origin')
    original=Path(config['paths']['input_wav'])
    inherited={'path':str(original),'bytes':672044,'sha256':config['input_descriptor']['sha256']}
    targets={role:Path(config['paths']['upload_'+role]) for role in ('a','b')}
    receipt_path=root/'input/MATERIALIZATION.json'
    check_split_targets(targets,receipt_path)
    require(not any(k in os.environ for k in ('FRESH_SEAM_AUTHORITY','FRESH_SEAM_AUTHORITY_SHA256')),
            'split_cannot_carry_actual_authority')
    authority_path=os.environ.get('FRESH_SEAM_SPLIT_AUTHORITY')
    require(authority_path is not None,'separate_split_authority_required')
    ordinary_path(authority_path);authority_raw=bounded_read(authority_path,16384)
    require(sha(authority_raw)==os.environ.get('FRESH_SEAM_SPLIT_AUTHORITY_SHA256'),'split_authority_pin')
    authority=decode(authority_raw)
    run=Path(os.environ['GUARD_RUN_DIRECTORY']);stop=Path(os.environ['GUARD_STOP_PATH'])
    absolute=float(os.environ['GUARD_ABSOLUTE_END']);work=float(os.environ['GUARD_WORK_END'])
    require(work==absolute-60 and 0<absolute-time.monotonic()<=300 and
        run.is_absolute() and run.resolve()==run and run.parent==root/'runs' and stop==run/'STOP',
        'split_immutable_outer_geometry')
    identity=proc(os.getpid())
    require(set(authority)=={'schema','authorized','config_sha256','run_directory','original','source_pin',
            'output_paths','harness_files'} and authority['schema']=='fresh-seam-split-authority-v1' and
        authority['authorized'] is True and authority['config_sha256']==sha(config_raw) and
        authority['run_directory']==str(run) and key(authority['original'])==key(identity) and
        authority['source_pin']==inherited and authority['output_paths']=={r:str(p) for r,p in targets.items()},
        'split_exact_original_authority')
    from binding import verify_harness,path_map
    verify_harness({'harness_root':str(root/'controller'),'harness_files':path_map(authority['harness_files'])},lambda:None)
    require(os.sched_getaffinity(0)=={3} and resource.getrlimit(resource.RLIMIT_AS)==(2**30,2**30) and
        os.getpriority(os.PRIO_PROCESS,0)==19,'split_resource_caps')
    def alive():require(time.monotonic()<work and not stop.exists(),'split_work_or_STOP')
    def signature(st):return [st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns]
    header = canonical_pcm16_header
    intervals = split_role_intervals()
    hashes = {role:{'wav':hashlib.sha256(header(hi-lo)),'pcm':hashlib.sha256(),
        'float':hashlib.sha256(),'frames':0} for role,(lo,hi) in intervals.items()}
    fd = os.open(original,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    outputs = {}
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size == 672044 and
            before.st_nlink == 1 and original.resolve() == original,'fixed_retained_source_shape')
        actual_header = os.read(fd,44);alive()
        require(actual_header == header(336000),'retained_source_canonical_PCM16_header')
        require(targets['a'].parent.resolve() == targets['a'].parent and
            not targets['a'].parent.is_symlink(),'materialization_directory_alias')
        targets['a'].parent.mkdir(mode=0o700,exist_ok=True)
        for role,path in targets.items():
            outputs[role] = path.open('xb');outputs[role].write(header(168000))
        at = 0
        while at < 336000:
            alive();count = min(16384,336000-at);block = os.read(fd,count*2)
            require(len(block) == count*2,'split_source_short_read')
            for role,part in split_block_contributions(at,block).items():
                item = hashes[role]
                item['wav'].update(part);item['pcm'].update(part);item['frames'] += len(part)//2
                floats = b''.join(struct.pack('<f',x[0]/32768.) for x in struct.iter_unpack('<h',part))
                require(len(floats) <= 65536,'split_float_transfer_cap');item['float'].update(floats)
                if role in ('upload_a','upload_b'):outputs[role[-1]].write(part)
            at += count
        require(os.read(fd,1) == b'','split_source_grew')
        for file in outputs.values():
            require(file.tell() == 336044,'split_output_exact_bytes');file.flush();os.fsync(file.fileno());file.close()
        outputs.clear()
        after = os.fstat(fd)
        require(signature(before) == signature(after) == signature(original.stat(follow_symlinks=False)),
            'split_source_changed')
        pins = {role:{'frames':item['frames'],'wav_sha256':item['wav'].hexdigest(),
            'pcm16_sha256':item['pcm'].hexdigest(),'float32_sha256':item['float'].hexdigest()}
            for role,item in hashes.items()}
        require(pins['source']['frames'] == 336000 and pins['source']['wav_sha256'] == inherited['sha256'],
            'accepted_source_encoded_pin')
        expected_pcm = pins['source']['pcm16_sha256']
        require(pins['upload_a']['wav_sha256'] != pins['upload_b']['wav_sha256'],
            'fixed_digest_conflict_prerequisite_failed')
        joined = hashlib.sha256();readbacks = {}
        for role,path in targets.items():
            rfd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
            try:
                start = os.fstat(rfd);raw_header = os.read(rfd,44)
                require(stat.S_ISREG(start.st_mode) and start.st_nlink == 1 and start.st_size == 336044 and
                    raw_header == header(168000),'split_readback_shape')
                wav_hash = hashlib.sha256(raw_header);pcm_hash = hashlib.sha256();float_hash = hashlib.sha256();total = 0
                while part := os.read(rfd,32768):
                    alive();total += len(part);require(total <= 336000 and len(part)%2 == 0,'split_readback_growth')
                    wav_hash.update(part);pcm_hash.update(part);joined.update(part)
                    float_hash.update(b''.join(struct.pack('<f',x[0]/32768.) for x in struct.iter_unpack('<h',part)))
                observed = {'frames':total//2,'wav_sha256':wav_hash.hexdigest(),
                    'pcm16_sha256':pcm_hash.hexdigest(),'float32_sha256':float_hash.hexdigest()}
                require(observed == pins['upload_'+role] and signature(start) == signature(os.fstat(rfd)) ==
                    signature(path.stat(follow_symlinks=False)),'split_readback_pin_or_identity')
                readbacks[role] = {'path':str(path),'bytes':336044,'descriptor':signature(start),'pins':observed}
            finally:os.close(rfd)
        require(joined.hexdigest() == expected_pcm,'split_concatenation_PCM_mismatch')
        require(signature(before) == signature(os.fstat(fd)) == signature(original.stat(follow_symlinks=False)),
            'split_source_changed_after_readback')
        receipt = {'schema':'fresh-seam-split-result-v1','status':'MATERIALIZED_PENDING_OWNER_ACCEPTANCE',
            'subject_sha256':subject_digest(config),'config_sha256':sha(config_raw),
            'original_process_identity':identity,'input_descriptor':config['input_descriptor'],
            'source_frames':[0,336000],'split_frames':[[0,168000],[168000,336000]],'input_pins':pins,
            'output_paths':{k:str(v) for k,v in targets.items()},
            'outputs':[{'path':str(targets[r]),'bytes':336044,'sha256':pins['upload_'+r]['wav_sha256']} for r in ('a','b')],
            'source_descriptor_before':signature(before),'source_descriptor_after':signature(after),
            'output_readbacks':readbacks,'output_readback':True,'concatenated_pcm_equal':True,
            'split_wav_digests_distinct':True,'max_transfer_bytes':65536,
            'no_resampling_padding_looping':True,'independent_pin_review_required':True,
            'actual_execution_authorized':False}
        alive();atomic_json(receipt_path,receipt,16384);alive()
        print(json.dumps(receipt,sort_keys=True),flush=True)
    finally:
        for file in outputs.values():file.close()
        os.close(fd)


if __name__ == '__main__':
    materialize_main()
