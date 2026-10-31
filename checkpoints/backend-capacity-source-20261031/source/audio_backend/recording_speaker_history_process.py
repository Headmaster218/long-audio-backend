"""One comparison-only exec owner; the parent alone owns storage/publication.

Source-only implementation: execution and resource qualification are separate
review gates. Limits below are prospective ceilings, not measured guarantees.
The standalone child imports only stdlib, psutil and the unchanged comparator,
which is loaded by its exact sibling path only after the no-work ready exchange.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import math
import os
from pathlib import Path
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid

import psutil

VERSION = 1
MIB = 1024 * 1024
FRAME_BYTES, CONTROL_BYTES, ROWS = 128 * 1024, 8 * 1024, 16
BUILD_BYTES, STARTUP_BYTES, CHILD_BYTES, PARENT_BYTES = 128*MIB, 32*MIB, 160*MIB, 32*MIB
OWNER_BYTES = 192*MIB
COLD_SECONDS, INCREMENTAL_SECONDS = 300., 180.
# Includes the independently bounded 512 KiB proof/64 KiB packet decode,
# validation graph and source-page overlap. Codec allowance includes both raw
# frames, JSON strings/graphs, base64/raw-vector copies and bounded output pages.
SOURCE_WORKSPACE_BYTES, CODEC_WORKSPACE_BYTES = 16*MIB, 8*MIB
MAX_DEPTH, MAX_NODES, MAX_CONTAINER, MAX_STRING = 9, 4096, 512, 8192
_HEADER = struct.Struct('<IB')  # payload length, full-frame versus control class
_TOKEN_FIELDS = frozenset(('scope_id', 'policy', 'space_id', 'history_revision',
    'authority_revision', 'observation_count', 'witness_count', 'vector_count'))
_ENVELOPE = frozenset(('version','owner','generation','snapshot','sequence','cursor','op','payload'))
_OBSERVATION_FIELDS = frozenset(('sequence','observation_id','evidence_kind','invocation_id',
    'certificate_id','node_id','origin_kind','source_key','pcm_hash','float_hash','vector',
    'vector_sha256','attributions','spans','refs'))
_COMPARATOR_NAME = '_recording_history_isolated_comparator'


class ProcessError(RuntimeError):
    """Uncertain/malformed optional work; retain owner until positive cleanup."""


class ProcessPending(ProcessError):
    pass


class ProcessStepDeadline(ProcessError):
    """The parent's step-only RPC cutoff expired; cleanup is still unproved."""
    reason = 'comparison_step_deadline'

    def __init__(self):super().__init__(self.reason)


def _require(condition, reason):
    if not condition:
        raise ProcessError(reason)


def _integer(value, maximum=(1 << 63)-1):
    _require(type(value) is int and 0 <= value <= maximum, 'bounded_integer')
    return value


def _hex(value, count=32):
    _require(type(value) is str and len(value) == count
        and all(c in '0123456789abcdef' for c in value), 'bounded_identifier')
    return value


def _token(value):
    if hasattr(value, 'document'):
        value = value.document()
    _require(type(value) is dict and set(value) == _TOKEN_FIELDS, 'snapshot_fields')
    _hex(value['scope_id'], 64)
    for name, limit in (('policy',64),('space_id',256)):
        _require(type(value[name]) is str and 1 <= len(value[name]) <= limit, 'snapshot_text')
    for name in _TOKEN_FIELDS - {'scope_id','policy','space_id'}:
        _integer(value[name])
    _require(value['vector_count'] <= value['observation_count'], 'snapshot_vector_count')
    return dict(value)


def _cache_binding(value):
    if value is None:return None
    _require(type(value) is dict and set(value) == {'scope_id','policy','space_id','cache_nonce',
        'row_count','vector_count','score_count'}, 'cache_binding_fields')
    _hex(value['scope_id'],64); _hex(value['cache_nonce'])
    for name,limit in (('policy',64),('space_id',256)):
        _require(type(value[name]) is str and 1 <= len(value[name]) <= limit, 'cache_binding_text')
    for name in ('row_count','vector_count','score_count'):_integer(value[name])
    _require(value['vector_count'] <= value['row_count'], 'cache_vector_count')
    return dict(value)


def _cache_matches(token, cached):
    return (cached is not None and cached['row_count'] > 0
        and all(cached[name] == token[name] for name in ('scope_id','policy','space_id'))
        and cached['row_count'] <= token['observation_count']
        and cached['vector_count'] <= token['vector_count']
        and token['vector_count']-cached['vector_count'] <= token['observation_count']-cached['row_count'])


def _complete_cache(value):
    cached = _cache_binding(value)
    if cached is not None:
        vectors = cached['vector_count']
        _require(cached['score_count'] == vectors*(vectors-1)//2, 'cache_prefix_incomplete')
    return cached


def _task_budget(token, payload, cached, *, now):
    """Both endpoints verify the same bounded, once-selected begin contract."""
    _require(type(payload) is dict and set(payload) == {
        'budget_class','admission_start','deadline','cache_binding'}, 'begin_fields')
    cached = _complete_cache(cached)
    expected = _complete_cache(payload['cache_binding'])
    _require(expected == cached, 'task_cache_binding')
    selected = 'incremental' if _cache_matches(token,cached) else 'cold'
    _require(payload['budget_class'] == selected, 'task_budget_class')
    start,deadline = payload['admission_start'],payload['deadline']
    _require(type(start) in (int,float) and math.isfinite(start) and 0 <= start <= now,
             'task_admission_start')
    duration = INCREMENTAL_SECONDS if selected == 'incremental' else COLD_SECONDS
    _require(type(deadline) in (int,float) and math.isfinite(deadline)
        and deadline == start+duration and now < deadline, 'comparison_deadline')
    return selected,float(start),float(deadline)


def _graph_bytes(value, *, allow_bytes=False):
    """Bound shape before serialization, counting aliases again conservatively."""
    nodes = 0
    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        _require(nodes <= MAX_NODES and depth <= MAX_DEPTH, 'decoded_shape_capacity')
        size = sys.getsizeof(item)
        if item is None or type(item) is bool:
            return size
        if type(item) is int:
            _require(-(1 << 63) <= item < (1 << 63), 'decoded_integer_capacity')
            return size
        if type(item) is float:
            _require(math.isfinite(item), 'decoded_float')
            return size
        if type(item) is str:
            _require(len(item) <= MAX_STRING, 'decoded_string_capacity')
            return size
        if allow_bytes and type(item) is bytes:
            _require(len(item) == 2048, 'vector_bytes')
            return size
        if type(item) in (tuple,list):
            _require(len(item) <= MAX_CONTAINER, 'decoded_container_capacity')
            return size + sum(visit(part, depth+1) for part in item)
        if type(item) is dict:
            _require(len(item) <= MAX_CONTAINER, 'decoded_container_capacity')
            total = size
            for key, part in item.items():
                _require(type(key) is str, 'decoded_key')
                total += visit(key, depth+1) + visit(part, depth+1)
            return total
        raise ProcessError('decoded_type')
    return visit(value, 0)


def _json_preflight(raw):
    """Lexical count/depth/string bounds before json allocates any decoded graph."""
    _require(type(raw) in (bytes,bytearray) and 0 < len(raw) <= FRAME_BYTES-_HEADER.size,
             'frame_bytes')
    depth = nodes = string_size = atom_size = 0
    quoted = escaped = False
    for byte in raw:
        _require(byte < 128, 'noncanonical_json')
        if quoted:
            string_size += 1
            _require(string_size <= MAX_STRING, 'wire_string_capacity')
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
            continue
        if byte == 34:
            quoted, string_size = True, 0
            nodes += 1
        elif byte in (91,123):
            depth += 1; nodes += 1
        elif byte in (93,125):
            depth -= 1
        elif byte == 44:
            nodes += 1
        if byte in (32,9,10,13,44,58,91,93,123,125,34):
            atom_size = 0
        else:
            atom_size += 1
            _require(atom_size <= 32, 'wire_atom_capacity')
        _require(0 <= depth <= MAX_DEPTH and nodes <= MAX_NODES, 'wire_shape_capacity')
    _require(not quoted and depth == 0, 'truncated_json')


def _object_pairs(pairs):
    _require(len(pairs) <= MAX_CONTAINER, 'decoded_container_capacity')
    value = {}
    for key, part in pairs:
        _require(key not in value, 'duplicate_json_key')
        value[key] = part
    return value


def _decode(raw):
    _json_preflight(raw)
    try:
        value = json.loads(raw, object_pairs_hook=_object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ProcessError('json_constant')))
    except (ValueError,UnicodeError,RecursionError) as exc:
        raise ProcessError('malformed_json') from exc
    _graph_bytes(value)
    return value


def _encoded_size(value):
    """Exact canonical ASCII size without allocating the encoded string."""
    if value is None:return 4
    if type(value) is bool:return 4 if value else 5
    if type(value) in (int,float):return len(str(value))
    if type(value) is str:
        return 2 + sum(2 if c in '\\"' or c in '\b\f\n\r\t' else
            6 if ord(c) < 32 or 127 < ord(c) <= 65535 else
            12 if ord(c) > 65535 else 1 for c in value)
    if type(value) in (tuple,list):
        return 2 + max(0,len(value)-1) + sum(_encoded_size(part) for part in value)
    if type(value) is dict:
        return 2 + max(0,len(value)-1) + sum(_encoded_size(key)+1+_encoded_size(part)
            for key,part in value.items())
    raise ProcessError('encoded_type')


def _encode(value, full=False):
    # Shape preflight precedes json's string/buffer allocations. Max shape plus
    # worst-case escaped strings is covered by the fixed codec workspace.
    graph_bytes = _graph_bytes(value)
    _require(graph_bytes <= CODEC_WORKSPACE_BYTES//2, 'encoded_graph_capacity')
    maximum = FRAME_BYTES if full else CONTROL_BYTES
    _require(_encoded_size(value)+_HEADER.size <= maximum, 'encoded_frame_capacity')
    raw = json.dumps(value, sort_keys=True, separators=(',',':'),
        ensure_ascii=True, allow_nan=False).encode('ascii')
    maximum = FRAME_BYTES if full else CONTROL_BYTES
    _require(len(raw)+_HEADER.size <= maximum, 'encoded_frame_capacity')
    return _HEADER.pack(len(raw), int(full)) + raw


def _wait_socket(channel, *, writable, deadline, checkpoint, clock=time.monotonic):
    while True:
        checkpoint()
        remaining = .025 if deadline is None else min(.025, deadline-clock())
        if remaining <= 0:
            raise ProcessError('comparison_deadline')
        try:
            reads, writes, _ = select.select([] if writable else [channel],
                [channel] if writable else [], [], remaining)
        except (ValueError,OSError) as exc:
            raise ProcessError('channel_closed') from exc
        if writes if writable else reads:
            return


def _send(channel, value, *, full=False, deadline, checkpoint, clock=time.monotonic):
    raw = _encode(value, full)
    view = memoryview(raw)
    sent = 0
    while sent < len(view):
        _wait_socket(channel, writable=True, deadline=deadline, checkpoint=checkpoint, clock=clock)
        try:
            count = channel.send(view[sent:])
        except (BlockingIOError,InterruptedError):
            continue
        _require(count > 0, 'channel_eof')
        sent += count


def _receive(channel, *, deadline, checkpoint, clock=time.monotonic):
    def exact(count):
        result = bytearray(count)
        view = memoryview(result)
        got = 0
        while got < count:
            _wait_socket(channel, writable=False, deadline=deadline, checkpoint=checkpoint, clock=clock)
            try:
                amount = channel.recv_into(view[got:], count-got)
            except (BlockingIOError,InterruptedError):
                continue
            _require(amount > 0, 'channel_eof')
            got += amount
        return result
    length, full = _HEADER.unpack(exact(_HEADER.size))
    _require(full in (0,1), 'frame_class')
    maximum = FRAME_BYTES if full else CONTROL_BYTES
    _require(0 < length <= maximum-_HEADER.size, 'frame_capacity')
    raw = exact(length)
    value = _decode(raw)
    _require(type(value) is dict and set(value) == _ENVELOPE, 'envelope_fields')
    _require(value['version'] == VERSION and type(value['version']) is int, 'protocol_version')
    _hex(value['owner']); _integer(value['generation']); _integer(value['sequence']); _integer(value['cursor'])
    _require(type(value['op']) is str and type(value['payload']) is dict, 'envelope_shape')
    row_frame = value['op'] in ('observations','witnesses','output') and 'rows' in value['payload']
    _require(bool(full) == row_frame, 'frame_operation_class')
    return value


def _os_identity_component(value):
    # POSIX device/inode values may use the unsigned high bit. Keep their exact
    # representation local to this identity field; generic wire integers remain
    # signed64. Decimal spelling is generated from a validated OS integer only.
    _require(type(value) is int and 0 <= value < (1 << 64), 'os_identity_capacity')
    return str(value)


def _binary_identity(path):
    resolved = Path(path).resolve(strict=True)
    value = resolved.stat()
    _require(stat.S_ISREG(value.st_mode), 'executable_not_regular')
    return [_os_identity_component(value.st_dev),_os_identity_component(value.st_ino)]


def _choose_cpu():
    _require(sys.platform == 'linux', 'comparison_execution_lane_unavailable')
    allowed = os.sched_getaffinity(0)
    _require(len(allowed) >= 2, 'comparison_cpu_unavailable')
    return max(allowed)


def _validate_lane(process, ready, cpu):
    _require(process.cpu_affinity() == [cpu] and process.nice() == 19,
             'comparison_execution_lane_unverified')
    _require(ready['affinity'] == [cpu] and ready['nice'] == 19,
             'comparison_execution_lane_unverified')


def _configure_child_lane(cpu):
    _require(sys.platform == 'linux' and cpu in os.sched_getaffinity(0), 'child_cpu_unavailable')
    os.setpriority(os.PRIO_PROCESS, 0, 19)
    os.sched_setaffinity(0, {cpu})
    _require(os.getpriority(os.PRIO_PROCESS, 0) == 19 and os.sched_getaffinity(0) == {cpu},
             'child_cpu_unverified')


class ComparisonProcess:
    """Parent's scalar-only driver. Store/manager callbacks never enter child.

    bind_child retains the original Popen before its first identity observation;
    finish_child independently verifies wait and bounded observed-family closure.
    A failed constructor/start never releases that original owner implicitly.
    """
    def __init__(self, *, owner_nonce=None, cancelled=lambda: False, grant_valid=lambda: True,
                 bind_child, observe_child, finish_child, clock=time.monotonic):
        self.owner_nonce = _hex(owner_nonce or uuid.uuid4().hex)
        self.cancelled, self.grant_valid, self.clock = cancelled, grant_valid, clock
        self._bind_child, self._observe_child, self._finish_child = bind_child, observe_child, finish_child
        self.process = self.identity = None
        self._channel = self._remote = None
        self._sequence = self._generation = 0
        self._token = None
        self._deadline = None
        self._outstanding = self._poisoned = self._started = self._task_closed = False
        self._buffer_bytes = self._socket_bytes = self._page_bytes = 0
        self._last_observation = 0.
        self._pause_sent = self._cancel_sent = False
        self._request_lock = threading.Lock()
        self.status = {'state':'new','reason':None,'required_bytes':0,'counts':{}}
        self.cache_binding = None
        self._authenticated_owner = self._closed_cache = None
        self._budget_class = self._admission_start = None
        self.cleanup = {'positive':False,'positive_wait':False,'control_channel_closed':False}
        self.execution_lane = None

    @property
    def state(self):return self.status['state']
    @property
    def reason(self):return self.status.get('reason')
    @property
    def required_bytes(self):return self.status.get('required_bytes',0)
    @property
    def decision_counts(self):
        _require(self.state == 'ready' and 'decision_counts' in self.status, 'comparison_not_ready')
        return dict(self.status['decision_counts'])

    def comparison_parent_bytes(self):
        # This is a conservative prospective graph/wire charge, not process RSS.
        if self.comparison_resources_closed():return 0
        return SOURCE_WORKSPACE_BYTES + CODEC_WORKSPACE_BYTES + self._socket_bytes + self._page_bytes

    def comparison_resources_closed(self):
        return (self._channel is None and self._remote is None and not self._outstanding
                and self._buffer_bytes == self._page_bytes == 0)

    def _account_page(self, value):
        charge = _graph_bytes(value, allow_bytes=True)
        # Retain original source graph plus a transformed transport graph, both
        # base64/raw forms, encoder strings, receive graph and OS queues.
        prospective = SOURCE_WORKSPACE_BYTES + CODEC_WORKSPACE_BYTES + self._socket_bytes + charge*3
        _require(prospective <= PARENT_BYTES, 'parent_transport_capacity')
        self._page_bytes = charge*3

    def _observe(self, *, startup=False, deadline=None):
        result = self._observe_child(self.process, startup=startup,
            deadline=self._deadline if deadline is None else deadline)
        _require(type(result) is dict and type(result.get('rss')) is int, 'child_observation_unavailable')
        _require(0 <= result['rss'] <= (STARTUP_BYTES if startup else CHILD_BYTES), 'child_rss_capacity')
        _require(self.comparison_parent_bytes() <= PARENT_BYTES, 'parent_transport_capacity')
        self._last_observation = self.clock()
        return result

    def _signal(self, signum):
        # psutil's identity-rechecked send_signal prevents signalling a reused
        # PID. No group signals, model handles or ambient process enumeration.
        if self.process is None or self.identity is None:return False
        try:
            if self.process.poll() is not None:return True
            process = psutil.Process(self.process.pid)
            _require(process.create_time() == self.identity['create_time'], 'child_identity_changed')
            _require(process.ppid() == os.getpid() and process.uids().effective == os.geteuid(),
                     'child_identity_changed')
            process.send_signal(signum)
            return True
        except (psutil.NoSuchProcess,psutil.ZombieProcess):
            return self.process.poll() is not None
        except (psutil.Error,OSError,ProcessError):
            return False

    def cancel(self):
        """Nonblocking exact-owner cancellation; call without manager/DB locks."""
        self._cancel_sent = True
        return self._signal(signal.SIGUSR1)

    def _checkpoint(self, *, cleanup=False, startup=False, deadline=None):
        if cleanup:return
        if self.cancelled():
            self.cancel()
            raise ProcessError('comparison_cancelled')
        if not startup:
            if self.grant_valid():
                self._pause_sent = False  # Only a positive grant permits another pause edge.
            elif not self._pause_sent:
                self._pause_sent = True
                _require(self._signal(signal.SIGUSR2), 'comparison_pause_unresolved')
        if not startup and self.clock()-self._last_observation >= .050:
            self._observe(deadline=deadline)

    def start(self, deadline):
        _require(not self._started and self.process is None and not self.cleanup['positive'],
                 'duplicate_child_start')
        _require(type(deadline) in (float,int) and math.isfinite(deadline) and deadline > self.clock(),
                 'comparison_deadline')
        self._started = True
        self._deadline = float(deadline)
        ready_deadline = min(self._deadline,self.clock()+.250)
        cpu = _choose_cpu()
        self._channel, self._remote = socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        for channel in (self._channel,self._remote):
            channel.setblocking(False)
            for option in (socket.SO_SNDBUF,socket.SO_RCVBUF):
                channel.setsockopt(socket.SOL_SOCKET,option,64*1024)
                amount = channel.getsockopt(socket.SOL_SOCKET,option)
                _require(0 < amount <= 512*1024, 'socket_buffer_capacity')
                self._socket_bytes += amount
        parent_birth = psutil.Process().create_time()
        argv = [sys.executable,'-I','-B',str(Path(__file__).resolve()),'--comparison-child',
            str(self._remote.fileno()),self.owner_nonce,str(os.getpid()),repr(parent_birth),str(cpu)]
        expected_executable = _binary_identity(sys.executable)
        _require(len(json.dumps(argv)) < CONTROL_BYTES, 'startup_argv_capacity')
        try:
            self.process = subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,close_fds=True,pass_fds=(self._remote.fileno(),),
                cwd=str(Path(__file__).resolve().parent))
            self._remote.close(); self._remote = None
            _require(self._bind_child(self.process) is True, 'child_binding_unconfirmed')
            process = psutil.Process(self.process.pid)
            birth = process.create_time()
            self.identity = {'pid':self.process.pid,'create_time':birth,'parent_pid':os.getpid(),
                'uid':os.geteuid(),'argv':argv,'executable_identity':expected_executable}
            def identity_check(final=False):
                _require(self.clock() < ready_deadline and self.process.poll() is None, 'ready_deadline_or_exit')
                _require(process.pid == self.process.pid and process.create_time() == birth, 'ready_identity_changed')
                _require(process.ppid() == os.getpid() and process.uids().effective == os.geteuid(), 'ready_parent_uid')
                observed = process.cmdline()
                _require(observed == argv or not final and observed == [], 'ready_unexpected_argv')
                # Family observation uses the permit's bounded service sampler.
                sample = self._observe(startup=True,deadline=ready_deadline)
                _require(sample.get('family_size') == 1, 'ready_unexpected_family')
                if final:
                    _require(_binary_identity(sys.executable) == expected_executable
                        and _binary_identity(process.exe()) == expected_executable, 'ready_wrong_executable')
            identity_check()
            ready = self._request('ready',deadline=ready_deadline,startup=True)
            identity_check(final=True)
            for name in ('pid','create_time','parent_pid','uid','argv','executable_identity'):
                _require(ready.get(name) == self.identity[name], 'ready_ack_identity')
            _require(ready.get('input_decoded') is False and ready.get('comparison_imported') is False,
                     'ready_work_before_authentication')
            _validate_lane(process,ready,cpu)
            self.execution_lane = {'pid':self.process.pid,'cpu':cpu,'nice':19}
            self._task_closed = True
            self._authenticated_owner = self._owner_signature()
            self.status = {'state':'idle','reason':None,'required_bytes':0,'counts':{}}
            return dict(ready)
        except BaseException:
            self._poisoned = True
            self.status.update(state='ownership_unknown',reason='comparison_startup_failed')
            raise

    def _request(self, op, *, payload=None, cursor=0, token=None, generation=None,
                 deadline=None, startup=False, cleanup=False):
        _require(self._channel is not None and (cleanup or not self._poisoned), 'channel_unavailable')
        _require(self._request_lock.acquire(blocking=False), 'outstanding_command')
        try:
            _require(not self._outstanding, 'outstanding_command')
            limit = deadline if deadline is not None else self._deadline
            if not cleanup:
                if not startup and not self.grant_valid():raise ProcessPending('comparison_grant_paused')
                self._checkpoint(startup=startup,deadline=limit)
            snapshot = self._token if token is None else _token(token)
            command = {'version':VERSION,'owner':self.owner_nonce,
                'generation':self._generation if generation is None else generation,
                'snapshot':snapshot,'sequence':self._sequence,'cursor':cursor,'op':op,'payload':payload or {}}
            self._sequence += 1
            limit = deadline if deadline is not None else self._deadline
            self._outstanding, self._buffer_bytes = True, CODEC_WORKSPACE_BYTES
            checkpoint = lambda:self._checkpoint(cleanup=cleanup,startup=startup,deadline=limit)
            _send(self._channel,command,full=op in ('observations','witnesses'),
                deadline=limit,checkpoint=checkpoint,clock=self.clock)
            answer = _receive(self._channel,deadline=limit,checkpoint=checkpoint,clock=self.clock)
            _require(all(answer[name] == command[name] for name in _ENVELOPE-{'payload'}), 'response_binding')
            _require('error' not in answer['payload'], 'child_protocol_error')
            if not cleanup and not startup:self._observe(deadline=limit)
            return answer['payload']
        except ProcessPending:
            raise
        except BaseException:
            self._poisoned = True
            raise
        finally:
            self._outstanding, self._buffer_bytes = False, 0
            self._request_lock.release()

    def _accept_status(self, status):
        _require(type(status) is dict and type(status.get('state')) is str, 'status_shape')
        _graph_bytes(status)
        required = {'state','reason','required_bytes','counts','cache_binding','old_rows','prefix_rows','prefix_scores'}
        optional = {'loaded_rows','loaded_vectors','loaded_witnesses','decision_counts','task_closed'}
        _require(required <= set(status) and set(status) <= required | optional, 'status_fields')
        _require(status['state'] in ('idle','cached','loading','paused','pending','ready','refused','cancelled'), 'status_state')
        _require(status['reason'] is None or type(status['reason']) is str and len(status['reason']) <= 128,
                 'status_reason')
        for name in ('required_bytes','old_rows','prefix_rows','prefix_scores','loaded_rows','loaded_vectors','loaded_witnesses'):
            if name in status:_integer(status[name])
        counts = status['counts']
        _require(type(counts) is dict and len(counts) <= 64, 'status_counts')
        for key,value in counts.items():
            _require(type(key) is str and len(key) <= 64, 'status_count_name'); _integer(value)
        _cache_binding(status['cache_binding'])
        if 'decision_counts' in status:
            counts = status['decision_counts']
            _require(type(counts) is dict and set(counts) == {'annotation','merge','round',
                'total_rows','independent_observations'}, 'decision_counts')
            for value in counts.values():_integer(value)
        self.status = status
        self.cache_binding = status.get('cache_binding')
        return dict(status)

    def _owner_signature(self):
        identity = self.identity
        _require(self._started and self.process is not None and type(identity) is dict
            and self.process.pid == identity.get('pid'), 'task_owner_identity')
        return (self.owner_nonce,self.process,identity['pid'],identity['create_time'],
            identity['parent_pid'],identity['uid'],tuple(identity['argv']),
            tuple(identity['executable_identity']))

    def closed_cache_binding(self):
        # Public status is not evidence of task closure. This private snapshot
        # originates only from authenticated ready or a valid finish reply.
        _require(self._task_closed and self._token is None and not self._poisoned
            and not self._outstanding and not self.cleanup['positive'], 'task_not_closed')
        _require(self._authenticated_owner is not None
            and self._owner_signature() == self._authenticated_owner
            and self.process.poll() is None, 'task_owner_identity')
        _require(self.state == ('idle' if self._closed_cache is None else 'cached')
            and self.cache_binding == self._closed_cache, 'task_cache_binding')
        return _complete_cache(self._closed_cache)

    def select_budget(self, token):
        token = _token(token)
        cached = self.closed_cache_binding()
        return ('incremental' if _cache_matches(token,cached) else 'cold'),cached

    def begin(self, token, *, generation, deadline, admission_start, budget_class, cache_binding):
        token = _token(token)
        _integer(generation)
        _require(self._task_closed and self._token is None and generation > self._generation, 'task_not_closed')
        payload = {'deadline':deadline,'admission_start':admission_start,
            'budget_class':budget_class,'cache_binding':cache_binding}
        selected,start,deadline = _task_budget(token,payload,self.closed_cache_binding(),now=self.clock())
        result = self._request('begin',token=token,generation=generation,deadline=deadline,
                               payload=payload)
        self._token,self._generation,self._deadline = token,generation,float(deadline)
        self._budget_class,self._admission_start = selected,start
        self._task_closed = False
        self._closed_cache = None
        return self._accept_status(result)

    def rebase(self, token):
        token = _token(token)
        _require(self._token is not None and not self._task_closed, 'no_active_task')
        result = self._request('rebase',token=token,payload={'previous_snapshot':self._token})
        self._token = token
        return self._accept_status(result)

    def _feed(self, op, token, after, rows, complete):
        _require(_token(token) == self._token, 'stale_page')
        _require(type(rows) in (list,tuple) and len(rows) <= 32 and type(complete) is bool, 'page_shape')
        _integer(after)
        self._account_page(rows)
        try:
            # Preserve exact source-page membership; only repartition transport.
            offset = 0
            while offset < len(rows) or not rows and offset == 0:
                subset = rows[offset:offset+ROWS]
                outgoing = []
                for row in subset:
                    _require(type(row) is dict, 'row_shape')
                    value = dict(row)
                    if op == 'observations':
                        _require(set(value) == _OBSERVATION_FIELDS, 'observation_fields')
                        vector = value['vector']
                        _require(vector is None or type(vector) is bytes and len(vector) == 2048, 'vector_bytes')
                        value['vector'] = None if vector is None else base64.b64encode(vector).decode('ascii')
                    outgoing.append(value)
                result = self._request(op,cursor=after+offset,payload={'rows':outgoing,
                    'complete':complete and offset+len(subset) == len(rows)})
                self._accept_status(result)
                offset += len(subset)
                if not rows:break
        finally:
            self._page_bytes = 0

    def feed_observations(self, token, after, rows, *, complete):
        return self._feed('observations',token,after,rows,complete)

    def feed_witnesses(self, token, after, rows, *, complete):
        return self._feed('witnesses',token,after,rows,complete)

    def step(self):
        # This clips only this RPC (including its resource observations). It
        # never renews the original task deadline or bounds feed/output/close.
        deadline = min(self._deadline,self.clock()+.250)
        def expired(cause=None):
            if self.clock() < deadline:return
            self._poisoned = True
            if deadline < self._deadline:raise ProcessStepDeadline() from cause
            raise ProcessError('comparison_deadline') from cause
        expired()
        try:
            result = self._request('step',deadline=deadline)
        except BaseException as exc:
            # Includes a clipped census failure. Classification uses the
            # locally owned operation/deadlines/clock, never exception text.
            expired(exc)
            raise
        expired()  # A scheduler-delayed reply cannot revive this channel.
        return self._accept_status(result)

    def output_page(self, after=0, limit=ROWS):
        _integer(after); _require(type(limit) is int and 1 <= limit <= ROWS, 'output_limit')
        page = self._request('output',cursor=after,payload={'limit':limit})
        _require(set(page) == {'token','rows','next_after','complete','total_rows'}, 'output_fields')
        _require(page['token'] == self._token and type(page['rows']) is list
            and len(page['rows']) <= limit and type(page['complete']) is bool, 'output_binding')
        next_after,total = _integer(page['next_after']),_integer(page['total_rows'])
        _require(next_after == after+len(page['rows']) and page['complete'] == (next_after == total)
            and next_after <= total and (page['rows'] or page['complete']), 'output_coverage')
        _require(total == self.decision_counts['total_rows'], 'output_count')
        for ordinal,row in enumerate(page['rows'],after+1):
            _require(type(row) is dict and row.get('ordinal') == ordinal, 'output_ordinal')
        self._account_page(page)
        return page

    def finish_task(self, *, retain_prefix=True):
        _require(type(retain_prefix) is bool and self._token is not None, 'finish_task_state')
        result = self._request('finish',payload={'retain_prefix':retain_prefix})
        _require(result.get('task_closed') is True, 'task_closure_unconfirmed')
        accepted = self._accept_status(result)
        cached = _complete_cache(self.cache_binding)
        _require(self.state == ('cached' if cached is not None else 'idle')
            and result['old_rows'] == 0
            and result['prefix_rows'] == (0 if cached is None else cached['row_count'])
            and result['prefix_scores'] == (0 if cached is None else cached['score_count']), 'task_closure_binding')
        _require((cached is None and not retain_prefix) or cached is not None and retain_prefix
            and all(cached[name] == self._token[name] for name in ('scope_id','policy','space_id'))
            and cached['row_count'] == self._token['observation_count']
            and cached['vector_count'] == self._token['vector_count'], 'task_closure_prefix')
        _require(self._owner_signature() == self._authenticated_owner, 'task_owner_identity')
        self._closed_cache = cached  # Independent of the mutable status graph.
        self._token,self._task_closed = None,True
        self._page_bytes = 0
        return accepted

    def close(self, *, timeout=5.):
        """Retry the same original wait/family closure; never create a successor.

        A lost protocol acknowledgement is diagnostic. OS ownership closes only
        after original Popen.wait and the permit's independent family check.
        """
        if self.cleanup['positive']:return dict(self.cleanup)
        _require(type(timeout) in (int,float) and 0 <= timeout <= 5., 'cleanup_timeout')
        deadline = self.clock()+timeout
        receipt = self.cleanup
        try:
            if self._channel is not None and not self._outstanding and not self._poisoned:
                try:
                    answer = self._request('close',cleanup=True,deadline=min(deadline,self.clock()+.250))
                    receipt['close_ack'] = answer.get('discarded') is True
                except BaseException:
                    receipt['protocol_error'] = 'close_ack_unconfirmed'
            else:
                receipt.setdefault('protocol_error','channel_unresolved')
        finally:
            for name in ('_channel','_remote'):
                channel = getattr(self,name)
                if channel is not None:
                    try:channel.close()
                    finally:setattr(self,name,None)
            self._buffer_bytes = self._page_bytes = self._socket_bytes = 0
            self.cache_binding = None
            self._closed_cache = None
            self.status = {'state':'closed','reason':receipt.get('protocol_error'),'required_bytes':0,'counts':{}}
            receipt['control_channel_closed'] = self._channel is self._remote is None
        if self.process is None:
            receipt.update(positive=True,no_child=True)
            return dict(receipt)
        # Each stage consumes the same absolute cleanup budget. A missing or
        # reused identity is never guessed to be safe to signal.
        for signum,seconds in ((None,.250),(signal.SIGTERM,1.),(signal.SIGKILL,3.75)):
            try:
                if signum is not None and self.process.poll() is None:
                    if not self._signal(signum):break
                    receipt['termination_requested'] = True
                    if signum == signal.SIGKILL:receipt['force_kill'] = True
                self.process.wait(timeout=max(0.,min(seconds,deadline-self.clock())))
                receipt.update(positive_wait=True,returncode=self.process.returncode)
                break
            except subprocess.TimeoutExpired:
                if self.clock() >= deadline:break
            except (OSError,ChildProcessError):
                break
        if receipt['positive_wait'] and self.clock() < deadline:
            try:receipt['positive'] = bool(self._finish_child(self.process,deadline=deadline)) and self.comparison_resources_closed()
            except BaseException:receipt['positive'] = False
        return dict(receipt)


def _load_comparator():
    path = Path(__file__).with_name('recording_speaker_history_compare.py')
    spec = importlib.util.spec_from_file_location(_COMPARATOR_NAME,path)
    _require(spec is not None and spec.loader is not None, 'comparator_source_unavailable')
    module = importlib.util.module_from_spec(spec)
    sys.modules[_COMPARATOR_NAME] = module
    spec.loader.exec_module(module)
    return module


def _child_main(fd, owner, parent_pid, parent_birth, cpu):
    _hex(owner)
    channel = socket.socket(fileno=fd)
    channel.setblocking(False)
    process = psutil.Process()
    parent = psutil.Process(parent_pid)
    _require(os.getppid() == parent_pid and parent.create_time() == parent_birth
        and parent.uids().effective == os.geteuid(), 'child_parent_identity')
    _configure_child_lane(cpu)
    flags = {'cancelled':False,'paused':False}
    signal.signal(signal.SIGUSR1,lambda *_:flags.update(cancelled=True))
    signal.signal(signal.SIGUSR2,lambda *_:flags.update(paused=True))
    sequence = generation = 0
    token = deadline = build = prefix = comparator = cache_nonce = None
    budget_class = admission_start = None
    ready_seen = False
    output_after = 0
    baseline = process.memory_info().rss
    _require(baseline <= STARTUP_BYTES, 'child_startup_capacity')

    def parent_alive():
        if os.getppid() != parent_pid:return False
        try:
            # MSG_PEEK never consumes a command or creates a queue. EOF checks
            # occur at existing Work callbacks and every idle receive quantum.
            readable,_,_ = select.select([channel],[],[],0)
            if readable and channel.recv(1,socket.MSG_PEEK | socket.MSG_DONTWAIT) == b'':return False
        except BlockingIOError:pass
        except OSError:return False
        return True

    def cancelled():return flags['cancelled'] or not parent_alive()

    def checkpoint():
        _require(not cancelled(), 'child_cancelled_or_parent_eof')
        _require(parent.create_time() == parent_birth and parent.uids().effective == os.geteuid(),
                 'child_parent_identity')

    def resources():
        _require(process.memory_info().rss <= CHILD_BYTES, 'child_rss_capacity')
        required = 0 if build is None else build.required_bytes
        cached = 0 if prefix is None else prefix.owned_bytes
        _require(baseline + max(required,cached) + CODEC_WORKSPACE_BYTES <= CHILD_BYTES,
                 'child_prospective_capacity')

    def binding():
        active = prefix if build is None else build.score_prefix
        if active is None:return None
        scope_id,policy,space_id = active.binding
        return {'scope_id':scope_id,'policy':policy,'space_id':space_id,'cache_nonce':cache_nonce,
            'row_count':active.row_count,'vector_count':active.vector_count,'score_count':len(active.scores)}

    def status():
        value = ({'state':'cached' if prefix is not None else 'idle','reason':None,
            'required_bytes':0 if prefix is None else prefix.owned_bytes,'counts':{}} if build is None else build._status())
        cached = binding()
        value.update(cache_binding=cached,old_rows=0 if build is None else build._old_rows,
            prefix_rows=0 if cached is None else cached['row_count'],
            prefix_scores=0 if cached is None else cached['score_count'])
        if build is not None and build.state == 'ready':value['decision_counts'] = build.decision_counts
        return value

    def drop_build(retain):
        nonlocal build,prefix,cache_nonce
        if build is not None:
            prefix = build.score_prefix if retain else None
            build.discard(); build = None
        elif not retain:prefix = None
        if prefix is None:cache_nonce = None

    try:
        while True:
            checkpoint(); resources()
            command = _receive(channel,deadline=deadline,checkpoint=checkpoint)
            _require(command['owner'] == owner and command['sequence'] == sequence, 'command_binding')
            sequence += 1
            op,payload = command['op'],command['payload']
            _require(type(payload) is dict, 'command_payload')
            if op == 'begin':
                _require(ready_seen and token is None and command['generation'] > generation, 'begin_state')
                new_token = _token(command['snapshot'])
            elif op == 'rebase':
                _require(token is not None and command['generation'] == generation, 'rebase_state')
                _require(payload.get('previous_snapshot') == token, 'rebase_previous_snapshot')
                new_token = _token(command['snapshot'])
            else:
                _require(command['snapshot'] == token and command['generation'] == generation, 'active_snapshot_binding')
            if op not in ('observations','witnesses','output'):
                _require(command['cursor'] == 0, 'control_cursor')
            answer = None
            close_after = finish_after = False
            if op == 'ready':
                _require(not ready_seen and token is None and not payload, 'ready_state')
                _require(comparator is None and build is None and _COMPARATOR_NAME not in sys.modules,
                         'ready_work_before_authentication')
                ready_seen = True
                answer = {'pid':os.getpid(),'create_time':process.create_time(),'parent_pid':os.getppid(),
                    'uid':os.geteuid(),'argv':[sys.executable,'-I','-B',*sys.argv],
                    'executable_identity':_binary_identity(sys.executable),'input_decoded':False,
                    'comparison_imported':False,'affinity':process.cpu_affinity(),'nice':process.nice(),
                    'startup_rss':baseline}
            elif op in ('begin','rebase'):
                if op == 'begin':
                    budget_class,admission_start,deadline = _task_budget(
                        new_token,payload,binding(),now=time.monotonic())
                    generation = command['generation']
                else:
                    _require(set(payload) == {'previous_snapshot'}, 'rebase_fields')
                    drop_build(True)  # Do not reset the original task clock.
                _require(deadline > time.monotonic(), 'comparison_deadline')
                token = new_token
                if comparator is None:
                    comparator = _load_comparator()
                    baseline = max(baseline,process.memory_info().rss)
                if prefix is not None and not _cache_matches(token,binding()):
                    prefix = None; cache_nonce = None
                if prefix is None:cache_nonce = uuid.uuid4().hex
                flags['paused'] = False
                build = comparator.ComparisonBuild(token,admitted_bytes=BUILD_BYTES,
                    cancelled=cancelled,grant_valid=lambda:not flags['paused'],deadline=deadline,prefix=prefix)
                prefix = None
                output_after = 0
                answer = status()
            elif op in ('observations','witnesses'):
                _require(build is not None and set(payload) == {'rows','complete'}, 'feed_state')
                rows = payload['rows']
                _require(type(rows) is list and len(rows) <= ROWS and type(payload['complete']) is bool, 'feed_shape')
                if op == 'observations':
                    for row in rows:
                        _require(type(row) is dict and set(row) == _OBSERVATION_FIELDS, 'observation_fields')
                        vector = row['vector']
                        if vector is not None:
                            _require(type(vector) is str and len(vector) == 2732, 'encoded_vector_size')
                            try:raw = base64.b64decode(vector,validate=True)
                            except ValueError as exc:raise ProcessError('encoded_vector') from exc
                            _require(len(raw) == 2048, 'decoded_vector_size')
                            row['vector'] = raw
                    build.feed_observations(token,command['cursor'],rows,complete=payload['complete'])
                else:build.feed_witnesses(token,command['cursor'],rows,complete=payload['complete'])
                answer = status()
            elif op == 'step':
                _require(build is not None and not payload, 'step_state')
                flags['paused'] = False
                build.step()
                answer = status()
            elif op == 'output':
                _require(build is not None and set(payload) == {'limit'} and command['cursor'] == output_after,
                         'output_state')
                limit = payload['limit']
                _require(type(limit) is int and 1 <= limit <= ROWS, 'output_limit')
                flags['paused'] = False
                answer = build.output_page(output_after,limit)
                output_after = answer['next_after']
            elif op == 'finish':
                _require(build is not None and build.state == 'ready' and set(payload) == {'retain_prefix'}
                    and type(payload['retain_prefix']) is bool
                    and output_after == build.decision_counts['total_rows'], 'unfinished_task')
                drop_build(payload['retain_prefix'])
                answer = status(); answer['task_closed'] = True
                finish_after = True
            elif op == 'close':
                _require(not payload, 'close_fields')
                drop_build(False)
                answer = {'discarded':True}
                close_after = True
            else:raise ProcessError('unknown_command')
            resources(); checkpoint()
            response = {name:command[name] for name in _ENVELOPE-{'payload'}}
            response['payload'] = answer
            _send(channel,response,full=op == 'output',deadline=deadline,checkpoint=checkpoint)
            # Closure is acknowledged under the old exact active binding. Only
            # subsequent idle frames have snapshot=None and may start a new task.
            if finish_after:token = deadline = None
            if close_after:return
            command = response = answer = payload = None
    finally:
        if build is not None:build.discard()
        prefix = None
        channel.close()


if __name__ == '__main__':
    # Fixed positional entry point, no general job payload or model/config flags.
    _require(len(sys.argv) == 7 and sys.argv[1] == '--comparison-child', 'child_arguments')
    _child_main(int(sys.argv[2]),sys.argv[3],int(sys.argv[4]),float(sys.argv[5]),int(sys.argv[6]))
