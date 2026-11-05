"""Opt-in, bounded local inbox reconciliation for one explicit span.

Configuration alone performs no HTTP or intake. Every run requires an explicit
exclusive writer and span-local closure acknowledgment. There is no daemon,
implicit session creation, inference retry or directory cleanup. The separate
initialize command explicitly creates one session with no uncertain-POST retry. Export conflicts stop by
default; explicit recovery permits bounded fresh GET-only attempts, never replacement.
"""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time

from . import recorder_capture_intake as engine
from . import recorder_incremental_span_intake as intake
from . import recorder_incremental_span_status as status
from . import recorder_incremental_span_export as exporter
from . import recorder_export_recovery as export_recovery
from . import recorder_runner_capacity as capacity
from . import recorder_inbox as inbox
from . import recorder_receiver_publication as receiver
from . import recorder_intake as single
from . import recording_uploader as u
from .recorder_delivery import Refused, inspect_delivery, MAX_METADATA

SCHEMA = 'recorder-inbox-runner-v1'
CONFIG_SCHEMA = 'recorder-inbox-runner-config-v1'
CONFIG_SCHEMA_V2 = 'recorder-inbox-runner-config-v2'
CONFIG_KEYS = frozenset(('schema', 'inbox_dir', 'job_dir', 'output_dir', 'api',
    'session_id', 'capture_id', 'span_origin', 'max_hours', 'max_inputs'))
MAX_ACTIONS = 195
MAX_REQUESTS = 65536
MAX_NETWORK_WRITES = 8192
MAX_NETWORK_BYTES = 256 * 1024**2
MAX_WATCH_POLLS = 4096
MAX_WATCH_ACTIONS = 4096
require = single.require
NATIVE_EXPORT_POLICY = 'native-turns-v1'


def native_export_options(native_turns, recovery_attempts=0):
    """Default-off export policy; revision recovery remains baseline-only."""
    require(type(native_turns) is bool, 'runner_native_turn_option')
    require(not (native_turns and recovery_attempts), 'runner_native_turn_recovery_unsupported')
    return {'native_turns': True} if native_turns else {}


def _export_artifacts(intent):
    native = 'export_policy' in intent
    if native:
        require(intent['export_policy'] == NATIVE_EXPORT_POLICY, 'runner_export_policy_invalid')
        require('recovery_attempts' not in intent, 'runner_native_turn_recovery_unsupported')
    return {'export.json', 'transcript.txt'} | ({'native-turns.json'} if native else set())


def check_export_policy(root, native_turns):
    """A saved export intent pins policy, including already committed exports."""
    path = root/'export.intent.json'
    if os.path.lexists(path):
        saved = single.manifest_read(path)
        _export_artifacts(saved)
        require(('export_policy' in saved) == native_turns, 'runner_export_policy_changed')


def _same(a, b):
    return single._json(a) == single._json(b)


def _hash(value):
    return hashlib.sha256(single._json(value)).hexdigest()


def _names(path, maximum):
    result = []
    with os.scandir(path) as entries:
        for entry in entries:
            result.append(entry.name)
            require(len(result) <= maximum, 'runner_directory_entry_bound')
    return set(result)


def _path(value):
    require(type(value) is str and 0 < len(value) <= 4096, 'runner_absolute_path_required')
    path = Path(value)
    require(path.is_absolute() and path == path.resolve(strict=False), 'runner_absolute_path_required')
    return path


def _outside_git(path):
    require(not any((parent/'.git').is_file() or os.path.lexists(parent/'.git/HEAD')
                    for parent in (path, *path.parents)),
            'runner_private_state_in_repository')


def _config(value, state):
    require(type(value) is dict and value.get('schema') in (CONFIG_SCHEMA,CONFIG_SCHEMA_V2), 'runner_config_fields')
    keys=CONFIG_KEYS | ({'reviewed_identity_handoff'} if value['schema']==CONFIG_SCHEMA_V2 else set())
    require(set(value) in (keys,keys|{'receiver_publication'}), 'runner_config_fields')
    if value['schema']==CONFIG_SCHEMA_V2:
        from .recorder_runner_initialize import validate_handoff
        validate_handoff(value['reviewed_identity_handoff'])
    destination = single.target(value['api'], 'operator-selected-unverified',
                                value['session_id'], value['capture_id'], 0)
    require(value['api'] == destination['origin'], 'runner_canonical_api_required')
    require(type(value['max_hours']) is int and 1 <= value['max_hours'] <= 48 and
            type(value['max_inputs']) is int and 1 <= value['max_inputs'] <= 192,
            'runner_capacity_bounds')
    intake.POLICY.validate_header(dict(initial_intent=True, span_origin=value['span_origin'],
        acknowledgments=dict(exclusive_writer=True, span_local_closure=True)))
    paths = [state, _path(value['inbox_dir']), _path(value['job_dir'])]
    if value['output_dir'] is not None:
        paths.append(_path(value['output_dir']))
    for index, path in enumerate(paths):
        single.private_directory(path.parent)
        require(not any(path.is_relative_to(other) or other.is_relative_to(path)
                        for other in paths[:index]), 'runner_trees_must_be_disjoint')
    single.private_directory(paths[1])
    if 'receiver_publication' in value:
        received = receiver.validate_binding(value['receiver_publication'], value['inbox_dir'])
        private_roots = [Path(received['state_dir']), Path(received['staging_dir'])]
        require(not any(path.is_relative_to(other) or other.is_relative_to(path)
                        for path in paths for other in private_roots), 'runner_trees_must_be_disjoint')
    for path in (state, paths[2], *paths[3:]): _outside_git(path)
    require(len(single._json(value)) <= single.MAX_MANIFEST, 'runner_config_size')
    return destination


def _origin(record):
    return intake.POLICY.header_fields(record)['span_origin']


def config_from_delivery(*, first_delivery, inbox_dir, job_dir, output_dir, api,
                         session_id, capture_id, max_hours=12, max_inputs=192, receiver_state_dir=None):
    """Read only an operator-selected first delivery; never search/select newest."""
    path = _path(str(first_delivery)); root = _path(str(inbox_dir))
    require(path.parent == root and inbox.NAME.fullmatch(path.name), 'runner_first_delivery_outside_inbox')
    report = inspect_delivery(path, validate_metadata=True)
    require(report['attempt'] == path.name, 'runner_attempt_directory_mismatch')
    record = engine.candidate_record(path, report, 0, lambda: 120)
    intake.POLICY.validate_edge(None, record)
    destination = single.target(api, 'operator-selected-unverified', session_id, capture_id, 0)
    config = dict(schema=CONFIG_SCHEMA, inbox_dir=str(root), job_dir=str(job_dir),
        output_dir=None if output_dir is None else str(output_dir), api=destination['origin'],
        session_id=session_id, capture_id=capture_id, span_origin=_origin(record),
        max_hours=max_hours, max_inputs=max_inputs)
    if receiver_state_dir is not None:
        config['receiver_publication'] = receiver.bind(receiver_state_dir)
        receiver.validate_binding(config['receiver_publication'], root)
    return config


def _intent(config, path, report, ordinal):
    return dict(schema=SCHEMA, config_sha256=_hash(config), ordinal=ordinal,
                path=str(path), delivery=report)


def _file(root, ordinal, kind):
    return root / ('input-%03d.%s.json' % (ordinal, kind))


def _durable(path, value=None):
    """Re-fsync existing immutable evidence before an initial/retried effect."""
    current = single.manifest_read(path)
    if value is not None: require(_same(current, value), 'runner_control_changed')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        require(u._signature(os.fstat(fd)) == u._signature(path.lstat()), 'runner_control_replaced')
        os.fsync(fd)
    finally: os.close(fd)
    u._fsync_dir(path.parent)
    return current


def configure(*, state_dir, config, first_delivery=None):
    """Create a fresh private immutable binding. No target discovery or HTTP."""
    state = _path(str(state_dir)); _config(config, state)
    require(not os.path.lexists(state) and not os.path.lexists(config['job_dir']),
            'runner_fresh_state_and_job_required')
    if config['output_dir'] is not None:
        exporter._destination(config['output_dir'])
    intent = None
    if first_delivery is not None:
        path = _path(str(first_delivery))
        require(path.parent == Path(config['inbox_dir']) and inbox.NAME.fullmatch(path.name),
                'runner_first_delivery_outside_inbox')
        report = inspect_delivery(path, validate_metadata=True)
        record = engine.candidate_record(path, report, 0, lambda: 120)
        require(report['attempt'] == path.name and _same(_origin(record), config['span_origin']),
                'runner_first_delivery_binding')
        intake.POLICY.validate_edge(None, record)
        require(capacity.inspect(config,[],0,report,u.Guard(u.Limits()))['allowed'],
                'first_delivery_capacity')
        intent = _intent(config, path, report, 0)
    state.mkdir(mode=0o700); u._fsync_dir(state.parent)
    single.manifest_write(state/'config.json', config)
    lock = engine.held_lock(state/'runner.lock', create=True)
    try:
        if intent is not None: single.manifest_write(_file(state, 0, 'intent'), intent)
    finally: os.close(lock)
    return dict(schema=SCHEMA, status='configured', network_requests=0,
                intake_started=False, automatic_intake_enabled=False)


def _done(intent):
    return dict(schema=SCHEMA, config_sha256=intent['config_sha256'], intent_sha256=_hash(intent))


def _ledger(root, config, *, logical_preparing=False):
    allowed = {'config.json', 'runner.lock', 'finalize.intent.json', 'finalize.done.json',
               'export.intent.json', 'export.done.json'}
    from . import recorder_logical_boundary as logical
    if os.path.lexists(root/logical.RUNNER_ANCHOR):
        allowed.add(logical.RUNNER_ANCHOR)
        if not logical_preparing:logical.verify_anchor(Path(config['job_dir']),root)
    if config['schema']==CONFIG_SCHEMA_V2 or os.path.lexists(root/'initialization'):
        from .recorder_runner_initialize import validate_completed
        validate_completed(root,config);allowed.add('initialization')
    intents = []; completed = 0; pending = False
    names = _names(root, 2*config['max_inputs']+8)
    for index in range(config['max_inputs']):
        name = _file(root, index, 'intent').name; done = _file(root, index, 'done').name
        if name not in names:
            break
        require(not pending, 'runner_multiple_pending_inputs')
        allowed.update((name, done))
        value = single.manifest_read(root/name)
        require(type(value) is dict and set(value) == {'schema','config_sha256','ordinal','path','delivery'}
                and _same(value, _intent(config, value['path'], value['delivery'], index)),
                'runner_input_intent_binding')
        path = _path(value['path']); report = value['delivery']
        require(path.parent == Path(config['inbox_dir']) and inbox.NAME.fullmatch(path.name)
                and report['attempt'] == path.name
                and type(report['sequence']) is int
                and report['sequence'] == config['span_origin']['first_sequence']+index,
                'runner_input_path_or_sequence')
        if done in names:
            require(_same(single.manifest_read(root/done), _done(value)), 'runner_input_receipt_binding')
            completed += 1
        else: pending = True
        intents.append(value)
    require(names <= allowed, 'runner_unknown_or_noncontiguous_state')
    for kind in ('finalize','export'):
        ip = root/(kind+'.intent.json'); dp = root/(kind+'.done.json')
        if os.path.lexists(dp): require(os.path.lexists(ip), 'runner_receipt_without_intent')
        if os.path.lexists(ip):
            require(not pending and intents, 'runner_terminal_with_pending_input')
            expected = dict(schema=SCHEMA, config_sha256=_hash(config), kind=kind,
                            input_intents_sha256=_hash(intents))
            saved = single.manifest_read(ip)
            if kind == 'export' and type(saved) is dict and 'recovery_attempts' in saved:
                bound = saved['recovery_attempts']
                require(type(bound) is int and 2 <= bound <= 3, 'runner_export_recovery_bound')
                expected['recovery_attempts'] = bound
            if kind == 'export':
                _export_artifacts(saved)
                if 'export_policy' in saved:expected['export_policy'] = saved['export_policy']
            require(_same(saved, expected), 'runner_terminal_intent_binding')
            if kind == 'finalize' and os.path.lexists(dp):
                require(_same(single.manifest_read(dp), _done(expected)), 'runner_finalize_receipt_binding')
            if kind == 'export': require(os.path.lexists(root/'finalize.done.json'), 'runner_export_before_finalize')
    return intents, completed


def _terminal_allowed(config, intents, remaining):
    if not intents:return False
    job = Path(config['job_dir'])
    from . import recorder_logical_boundary as logical
    if logical.extra(job):
        header = single.manifest_read(job/'capture.json')
        receipts, records = engine.registry(job,header,remaining,policy=intake.POLICY)
        policy = intake.POLICY.for_job(job,header,receipts,records,remaining)
        return policy.finalizable(intents[-1]['delivery'])
    return intake.POLICY.finalizable(intents[-1]['delivery'])


def _stable(report):
    # Attempt nonce is transport packaging, not a new source/content identity.
    return {key: value for key, value in report.items() if key != 'attempt'}


def _unpublished(path, remaining):
    """Observe a narrow marker-absent publication shape; never authorize intake."""
    remaining()
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before = os.fstat(directory)
        names = _names(path, 2)
        require(names <= {'audio.wav','segment.v1.bin'}, 'runner_unpublished_members')
        sizes = 0; signatures = {}
        for name in names:
            remaining()
            item = os.stat(name, dir_fd=directory, follow_symlinks=False)
            maximum = inbox.MAX_WAV if name == 'audio.wav' else MAX_METADATA
            require(stat.S_ISREG(item.st_mode) and item.st_nlink == 1 and
                    0 <= item.st_size <= maximum, 'runner_unpublished_file_bound')
            signatures[name] = u._signature(item)
            if name == 'audio.wav': sizes += item.st_size
        require(_names(path, 2) == names, 'runner_unpublished_changed')
        for name, signature in signatures.items():
            require(u._signature(os.stat(name, dir_fd=directory, follow_symlinks=False)) == signature,
                    'runner_unpublished_changed')
        require(u._signature(before) == u._signature(os.fstat(directory)) == u._signature(path.lstat()),
                'runner_unpublished_changed')
        remaining(); return sizes
    finally: os.close(directory)


def _inspect(config, intents, remaining, *, unpublished=None):
    options = dict(max_entries=256, max_bytes=inbox.MAX_BYTES, deadline_seconds=remaining())
    observed = (receiver.inventory(config['receiver_publication'], config['inbox_dir'], check=remaining, **options)
                if 'receiver_publication' in config else inbox.inventory(config['inbox_dir'], **options))
    unresolved = [row for row in observed['deliveries'] if row['status'] != 'observed_valid']
    require(unpublished is not None or not unresolved, 'runner_quarantined_unresolved_delivery')
    by_sequence = {}; by_path = {}
    origin = config['span_origin']
    for row in observed['deliveries']:
        if row['status'] != 'observed_valid': continue
        report = row['delivery']; path = Path(config['inbox_dir'])/row['directory']
        by_path[str(path)] = report
        if report['capture_identity_sha256'] != origin['capture_identity_sha256']: continue
        if report['sequence'] < origin['first_sequence']: continue
        # Do not discard a conflicting run/epoch/configuration at the same source.
        by_sequence.setdefault(report['sequence'], []).append((path, report))
    for candidates in by_sequence.values():
        require(all(_same(_stable(report), _stable(candidates[0][1])) for _, report in candidates),
                'runner_conflicting_ordinal_content')
    for value in intents:
        require(value['path'] in by_path and _same(by_path[value['path']], value['delivery']),
                'runner_frozen_original_missing_or_changed')
    if unresolved:
        # Complete conflicts and every frozen original have already been checked.
        total = sum(row['delivery']['wav_bytes'] for row in observed['deliveries']
                    if row['status'] == 'observed_valid')
        for row in unresolved:
            require(row.get('reason') == 'delivery_incomplete_or_extra_files',
                    'runner_quarantined_unresolved_delivery')
            total += _unpublished(Path(config['inbox_dir'])/row['directory'], remaining)
            require(total <= inbox.MAX_BYTES, 'runner_unpublished_aggregate_byte_bound')
            unpublished.append(row['directory'])
    return by_sequence


def _binding(config, intent, index, remaining):
    path = Path(intent['path']); report = inspect_delivery(path, validate_metadata=True,
                                                          deadline_seconds=remaining())
    require(_same(report, intent['delivery']), 'runner_frozen_original_changed')
    record = engine.candidate_record(path, report, index, remaining)
    origin = _origin(record)
    for key in intake.ORIGIN_KEYS - {'first_sequence', 'epoch_frame_start'}:
        require(_same(origin[key], config['span_origin'][key]), 'runner_span_origin_changed')
    if index == 0: require(_same(origin, config['span_origin']), 'runner_span_origin_changed')
    return record


def _job_prefix(config, intents, completed, *, writes=False):
    job = Path(config['job_dir'])
    if not os.path.lexists(job):
        require(completed == 0 and not writes, 'runner_known_job_missing')
        return
    single.private_directory(job)
    if not os.path.lexists(job/'capture.json'):
        require(completed == 0 and not writes, 'runner_known_job_header_missing')
        return
    header = single.manifest_read(job/'capture.json')
    destination = single.target(config['api'], 'operator-selected-unverified',config['session_id'],config['capture_id'],0)
    require(header['schema'] == intake.SCHEMA and _same(header['target'], destination)
            and _same(header['span_origin'], config['span_origin'])
            and _same(header['max_inputs'], config['max_inputs'])
            and _same(header['max_hours'], config['max_hours']), 'runner_job_binding_changed')
    names = _names(job/'admissions', config['max_inputs'])
    require(names == {str(i) for i in range(len(names))} and completed <= len(names) <= len(intents),
            'runner_job_admission_prefix_changed')
    for index in range(len(names)):
        path = job/'admissions'/str(index)/'admission.json'
        if not os.path.lexists(path):
            require(index >= completed and not writes, 'runner_admission_missing')
            continue
        receipt = single.manifest_read(path); intent = intents[index]
        expected = dict(schema=intake.SCHEMA, ordinal=index, target=destination,
            path=intent['path'], snapshot_path=str(path.parent/'snapshot'), delivery=intent['delivery'])
        require(_same(receipt, expected), 'runner_admission_binding_changed')
    if writes: require(len(names) == len(intents), 'runner_pending_admission_before_write')


class _WatchTransportStopped(Refused):
    """Do not let the underlying uploader retry an uncertain watch request."""
    def __init__(self, cause):
        super().__init__('runner_watch_transport_stopped_no_retry')
        self.cause = cause


class _Transport:
    def __init__(self, http, guard, config, max_requests, max_writes, check, *,
                 watch=False, max_bytes=MAX_NETWORK_BYTES, reviewed_identity_handoff=None,
                 busy_read_retries=0):
        require(type(busy_read_retries) is int and 0 <= busy_read_retries <= 3,
                'runner_busy_read_retry_bound')
        require(not busy_read_retries or watch, 'runner_busy_read_retry_watch_only')
        self.http, self.guard, self.config, self.check = http, guard, config, check
        self.max_requests, self.max_writes = max_requests, max_writes
        self.requests = self.writes = self.bytes = 0
        self.watch, self.max_bytes = watch, max_bytes
        self.export_recovery_phase = False
        self.reviewed_identity_handoff = reviewed_identity_handoff
        self.busy_read_retries = busy_read_retries
        self.busy_read_retries_used = 0

    def _busy_read_target(self, path):
        """Only canonical reads used by the recorder, never arbitrary GET routes."""
        base = '/sessions/'+self.config['session_id']
        capture = base+'/recordings/'+self.config['capture_id']
        if path in (base, base+'/recording-contract', capture, base+'/chunks?limit=1'):
            return True
        if (re.fullmatch(re.escape(capture)+r'/pages/[1-9][0-9]{0,8}', path) or
                re.fullmatch(re.escape(base)+r'/chunks\?after=(?:0|[1-9][0-9]{0,18})&limit=(?:16|500)', path)):
            return True
        handoff = self.reviewed_identity_handoff
        return handoff is not None and path in (
            '/sessions/'+handoff['source_session_id'],
            '/identity-groups/'+handoff['identity_group']+'/reviewed-person-bank')

    def check_identity(self):
        if self.reviewed_identity_handoff is not None:
            from .recorder_runner_initialize import check_reviewed_identity
            check_reviewed_identity(self.request,self.reviewed_identity_handoff,self.config['session_id'])

    def request(self, method, path, **kwargs):
        retry = False
        while True:
            try:
                return self._attempt(method, path, kwargs, retry)
            except _WatchTransportStopped as stopped:
                exc = stopped.cause
                if not (self.watch and not self.export_recovery_phase and method == 'GET' and
                        not kwargs and self._busy_read_target(path) and type(exc) is u.HTTPFailure and
                        exc.status == 503 and exc.admission_reason in u.STRICT_BUSY_REASONS and
                        self.busy_read_retries_used < self.busy_read_retries):
                    raise
                # One invocation-wide allowance; never restart a helper or write.
                # All guards, including local bindings, run again in _attempt.
                delay = exc.retry_after
                if type(delay) not in (int, float) or not 0 <= delay <= 3600:
                    delay = 1
                self.guard.pause(min(max(.1, delay), max(0, self.guard.end-time.monotonic())))
                retry = True

    def _attempt(self, method, path, kwargs, retry):
        self.guard.check(); require(method in ('GET','PUT','POST'), 'runner_http_method')
        require(not self.export_recovery_phase or (method == 'GET' and not kwargs),
                'runner_export_recovery_get_only')
        base = '/sessions/'+self.config['session_id']
        identity_read = self.reviewed_identity_handoff is not None and method=='GET' and not kwargs and path in (
            '/sessions/'+self.reviewed_identity_handoff['source_session_id'],
            '/identity-groups/'+self.reviewed_identity_handoff['identity_group']+'/reviewed-person-bank')
        require(path == base or path.startswith(base+'/') or identity_read, 'runner_http_target')
        if method != 'GET':
            require(path.startswith(base+'/recordings/'+self.config['capture_id']+'/'), 'runner_http_write_target')
            self.check_identity()
        require(self.requests < self.max_requests and (method == 'GET' or self.writes < self.max_writes),
                'runner_network_request_bound')
        if self.watch or self.export_recovery_phase:
            # Reserve the largest decoded response before issuing the request.
            require(self.bytes + u.JSON_BYTES <= self.max_bytes, 'runner_network_byte_bound')
        self.check(method != 'GET')
        self.requests += 1; self.writes += method != 'GET'
        self.busy_read_retries_used += retry
        # Keep legacy default watch accounting unchanged. Opt-in busy recovery
        # must retain failed-response reservations across its extra attempts.
        reserved = self.export_recovery_phase or (self.watch and self.busy_read_retries > 0)
        if reserved: self.bytes += u.JSON_BYTES
        try:
            value = self.http.request(method, path, **kwargs)
        except (u.HTTPFailure, u.Uncertain) as exc:
            # Missing resources are a normal part of exact initial reconciliation.
            # Every other failure stops watch, including helper-internal retries.
            revision = (self.export_recovery_phase and method == 'GET' and not kwargs and
                type(exc) is u.HTTPFailure and exc.status == 409 and
                exc.response_reason == 'transcript_revision_changed' and
                path.startswith(base+'/transcript?cursor=') and
                path.endswith('&projection=whole-primary-v1'))
            if self.watch and not revision and not (method == 'GET' and isinstance(exc, u.HTTPFailure) and exc.status == 404):
                raise _WatchTransportStopped(exc) from exc
            raise
        self.guard.check()
        require(type(value) is dict, 'runner_response_object')
        raw = single._json(value)
        require(len(raw) <= u.JSON_BYTES, 'runner_response_bound')
        if not self.export_recovery_phase:
            self.bytes += len(raw) - (u.JSON_BYTES if reserved else 0)
        require(self.bytes <= self.max_bytes, 'runner_network_byte_bound')
        return value


def _export_receipt(root, config, intent, guard):
    artifacts = _export_artifacts(intent)
    names = artifacts | {'complete.json'}
    if 'recovery_attempts' in intent:
        return _recovery_export_receipt(root, config, intent, guard)
    output = Path(config['output_dir']); single.private_directory(output)
    directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        held = os.fstat(directory)
        require(_names(output, len(names)) == names, 'runner_export_artifacts_changed')
        def read(name, maximum, *, expected_size=None, collect=False):
            guard.check()
            fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
            try:
                before = os.fstat(fd)
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                        and before.st_uid == os.geteuid() and before.st_mode & 0o077 == 0
                        and 0 < before.st_size <= maximum, 'runner_export_artifact_bound')
                require(expected_size is None or before.st_size == expected_size, 'runner_export_artifact_bound')
                digest = hashlib.sha256(); chunks = []; size = 0
                while size < before.st_size:
                    guard.check()
                    raw = os.read(fd, min(u.READ_BYTES,before.st_size-size))
                    require(bool(raw),'runner_export_artifact_changed')
                    size += len(raw); digest.update(raw)
                    if collect: chunks.append(raw)
                guard.check()
                require(os.read(fd,1) == b'' and u._signature(before) == u._signature(os.fstat(fd)) ==
                        u._signature(os.stat(name,dir_fd=directory,follow_symlinks=False)),
                        'runner_export_artifact_changed')
                return digest.hexdigest(), b''.join(chunks)
            finally: os.close(fd)
        complete_hash, raw = read('complete.json',single.MAX_MANIFEST,collect=True)
        manifest = json.loads(raw)
        require(type(manifest) is dict and manifest.get('schema') == exporter.SCHEMA and manifest.get('status') == 'exported'
                and type(manifest.get('artifacts')) is dict
                and set(manifest['artifacts']) == artifacts, 'runner_export_manifest_changed')
        if 'export_policy' in intent:
            require(manifest.get('local_publication') == 'manifest_commits_three_artifacts',
                    'runner_export_manifest_changed')
        total = 0
        for name, expected in manifest['artifacts'].items():
            require(type(expected) is dict and set(expected) == {'bytes','sha256'} and
                    type(expected['bytes']) is int and 0 < expected['bytes'] <= exporter.MAX_OUTPUT_BYTES
                    and type(expected['sha256']) is str and re.fullmatch('[0-9a-f]{64}',expected['sha256']),
                    'runner_export_artifact_bound')
            total += expected['bytes']; require(total <= exporter.MAX_OUTPUT_BYTES,'runner_export_artifact_bound')
            digest, _ = read(name,exporter.MAX_OUTPUT_BYTES,expected_size=expected['bytes'])
            require(digest == expected['sha256'], 'runner_export_artifact_changed')
        guard.check(); single.private_directory(output)
        require(u._signature(held) == u._signature(os.fstat(directory)) == u._signature(output.lstat()),
                'runner_export_directory_changed')
        require(_names(output,len(names)) == names,'runner_export_artifacts_changed')
        return dict(**_done(intent), complete_sha256=complete_hash)
    finally: os.close(directory)


def _recovery_export_receipt(root, config, intent, guard):
    """Bind all attempt files, not just the successful child; never adopt a crash."""
    output = Path(config['output_dir']); single.private_directory(output)
    original = u._signature(output.lstat()); tree = {}; identities = {output:original}; total = 0
    def read(path, cap):
        nonlocal total
        guard.check(); single.control(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                    before.st_uid == os.geteuid() and stat.S_IMODE(before.st_mode) == 0o600 and
                    0 <= before.st_size <= cap, 'runner_recovery_artifact_bound')
            total += before.st_size
            require(total <= exporter.MAX_OUTPUT_BYTES + 16384, 'runner_recovery_output_bound')
            digest = hashlib.sha256(); chunks = []; size = 0
            while size < before.st_size:
                guard.check(); data = os.read(fd, min(u.READ_BYTES, before.st_size-size))
                require(bool(data), 'runner_recovery_artifact_changed')
                size += len(data); digest.update(data)
                if path.name == 'recovery.jsonl': chunks.append(data)
            require(os.read(fd, 1) == b'' and u._signature(before) == u._signature(os.fstat(fd)) ==
                    u._signature(path.lstat()), 'runner_recovery_artifact_changed')
            identities[path] = u._signature(before)
            tree[str(path.relative_to(output))] = dict(bytes=size, sha256=digest.hexdigest())
            return b''.join(chunks)
        finally: os.close(fd)
    raw = read(output/'recovery.jsonl', 16384)
    records = [json.loads(line) for line in raw.splitlines()]
    require(3 <= len(records) <= 7 and len(records) % 2 == 1, 'runner_recovery_audit_shape')
    count = (len(records)-1)//2
    require(count <= intent['recovery_attempts'], 'runner_recovery_attempt_bound')
    limits = export_recovery.RecoveryLimits(attempts=intent['recovery_attempts'])
    expected = dict(schema=export_recovery.SCHEMA, job_dir=config['job_dir'],
        session_id=config['session_id'], capture_id=config['capture_id'], remote_read_only=True,
        inference_replay=False, attempts=limits.attempts, requests_bound=limits.requests,
        reserved_network_bound=limits.network_bytes, output_bound=limits.output_bytes, audit_output_bound=16384)
    require(_same(records[0], expected), 'runner_recovery_audit_binding')
    expected_names = {'recovery.jsonl', *(f'attempt-{n:02d}' for n in range(1,count+1))}
    require(_names(output,4) == expected_names, 'runner_recovery_tree_changed')
    previous_requests = 0
    for number in range(1,count+1):
        name = f'attempt-{number:02d}'; directory = output/name
        single.private_directory(directory); identity = u._signature(directory.lstat())
        identities[directory] = identity
        require(_same(records[2*number-1],dict(attempt=number,output=name,status='started')),
                'runner_recovery_audit_binding')
        end = records[2*number]
        require(type(end) is dict and end.get('attempt') == number and
                end.get('status') == ('exported' if number == count else 'revision_changed'),
                'runner_recovery_audit_binding')
        keys = {'attempt','status','requests','reserved_network_bytes'} | (
            {'partial_preserved'} if number < count else {'manifest','transcript_revision'})
        require(set(end) == keys and type(end['requests']) is int and
                previous_requests < end['requests'] <= limits.requests and
                type(end['reserved_network_bytes']) is int and
                end['reserved_network_bytes'] == end['requests'] * u.JSON_BYTES <= limits.network_bytes,
                'runner_recovery_audit_budget')
        previous_requests = end['requests']
        if number < count:
            require(end.get('partial_preserved') is True, 'runner_recovery_audit_binding')
        else:
            require(end.get('manifest') == name+'/complete.json', 'runner_recovery_audit_binding')
        names = {'export.json','transcript.txt','complete.json'} if number == count else {
            'export.json.partial','transcript.txt.partial'}
        require(_names(directory,3) == names, 'runner_recovery_attempt_changed')
        for filename in sorted(names): read(directory/filename, exporter.MAX_OUTPUT_BYTES)
        require(u._signature(directory.lstat()) == identity, 'runner_recovery_attempt_changed')
    require(u._signature(output.lstat()) == original and _names(output,4) == expected_names,
            'runner_recovery_tree_changed')
    child_intent = {key:value for key,value in intent.items() if key != 'recovery_attempts'}
    selected = dict(config, output_dir=str(output/f'attempt-{count:02d}'))
    child = _export_receipt(root, selected, child_intent, guard)
    manifest = single.manifest_read(Path(selected['output_dir'])/'complete.json')
    require(manifest.get('transcript_revision') == records[-1]['transcript_revision'],
            'runner_recovery_audit_revision')
    guard.check()
    require(all(u._signature(path.lstat()) == identity for path,identity in identities.items()),
            'runner_recovery_artifact_changed')
    return dict(**_done(intent), complete_sha256=child['complete_sha256'],
                recovery_tree_sha256=_hash(tree), export_attempts=count,
                revision_conflicts=count-1, selected_attempt=f'attempt-{count:02d}')


def _run(*, state_dir, exclusive_writer=False, acknowledge_span_local_closure=False,
         finalize=False, export=False, export_recovery_attempts=0, select_attempt=None, max_actions=1, max_requests=1024,
         max_network_writes=512, deadline_seconds=120, limits=None, transport=None, cancel=None,
         poll_seconds=None, max_polls=1, max_network_bytes=MAX_NETWORK_BYTES, wait_for_marker_last=False, watching=False,
         busy_read_retries=0, native_turns=False):
    native_options = native_export_options(native_turns, export_recovery_attempts)
    started = time.monotonic()
    require(exclusive_writer is True and acknowledge_span_local_closure is True,
            'runner_explicit_acknowledgments_required')
    require(type(finalize) is bool and type(export) is bool, 'runner_authorization_type')
    require(type(export_recovery_attempts) is int and export_recovery_attempts in (0,2,3),
            'runner_export_recovery_bound')
    require(not export_recovery_attempts or export, 'runner_export_recovery_requires_export')
    require(type(busy_read_retries) is int and 0 <= busy_read_retries <= 3,
            'runner_busy_read_retry_bound')
    require(not busy_read_retries or watching, 'runner_busy_read_retry_watch_only')
    for value, maximum in ((max_actions,MAX_WATCH_ACTIONS if watching else MAX_ACTIONS),(max_requests,MAX_REQUESTS),(max_network_writes,MAX_NETWORK_WRITES)):
        require(type(value) is int and 1 <= value <= maximum, 'runner_invocation_bound')
    require(type(deadline_seconds) in (int,float) and 0 < deadline_seconds <= 86400,
            'runner_deadline_bound')
    if watching:
        require(type(wait_for_marker_last) is bool, 'runner_marker_wait_type')
        require(type(poll_seconds) in (int,float) and .1 <= poll_seconds <= 3600,
                'runner_poll_interval_bound')
        require(type(max_polls) is int and 1 <= max_polls <= MAX_WATCH_POLLS,
                'runner_poll_bound')
        require(type(max_network_bytes) is int and u.JSON_BYTES <= max_network_bytes <= MAX_NETWORK_BYTES,
                'runner_network_byte_bound')
    require(select_attempt is None or type(select_attempt) is str and inbox.NAME.fullmatch(select_attempt),
            'runner_selection_format')
    root = _path(str(state_dir)); single.private_directory(root.parent); single.private_directory(root)
    lock = engine.held_lock(root/'runner.lock')
    try:
        root_identity = (root.stat().st_dev, root.stat().st_ino)
        lock_identity = u._signature(os.fstat(lock))
        config = single.manifest_read(root/'config.json'); _config(config, root)
        check_export_policy(root, native_turns)
        require(not export or config['output_dir'] is not None, 'runner_export_destination_required')
        effective = replace(limits or u.Limits(), deadline_seconds=deadline_seconds,
                            output_frames=config['max_hours']*3600*u.RATE)
        guard = u.Guard(effective, cancel)
        if watching or export_recovery_attempts: guard.end = started + deadline_seconds
        def remaining():
            guard.check(); return min(120, max(.000001, guard.end-time.monotonic()))
        intents, completed = _ledger(root, config)
        _job_prefix(config, intents, completed)
        if not intents:
            require(not os.path.lexists(config['job_dir']), 'runner_unbound_existing_job')
        def check(writes):
            guard.check()
            if watching:
                single.private_directory(root)
                single.control(root/'runner.lock')
                require((root.stat().st_dev,root.stat().st_ino) == root_identity and
                        u._signature(os.fstat(lock)) == u._signature((root/'runner.lock').lstat()) == lock_identity,
                        'runner_lock_or_state_replaced')
            require(_same(single.manifest_read(root/'config.json'), config), 'runner_config_changed')
            check_export_policy(root, native_turns)
            current, count = _ledger(root, config)
            require(_same(current,intents) and count == completed, 'runner_ledger_changed')
            _job_prefix(config, intents, completed, writes=writes)
            if intents and len(intents) > completed:
                _binding(config, intents[-1], len(intents)-1, remaining)
        from .recorder_runner_initialize import completed_handoff
        handoff = completed_handoff(root,config)
        http = _Transport(transport or u.HTTPTransport(config['api'], guard), guard, config,
                          max_requests, max_network_writes, check,
                          watch=watching, max_bytes=max_network_bytes,reviewed_identity_handoff=handoff,
                          busy_read_retries=busy_read_retries)
        actions = 0; helper_actions = 0; polls = 0; last_status = None
        def result(outcome, **extra):
            if watching:
                extra.update(polls=polls, helper_actions=helper_actions,
                    network_response_bytes=http.bytes, last_status=last_status,
                    continuous_polling_enabled=True, automatic_resume_enabled=False,
                    daemon_installed=False, invocation_bounded=True)
                if busy_read_retries:
                    extra.update(busy_read_retries=busy_read_retries,
                        busy_read_retries_used=http.busy_read_retries_used)
            return dict(schema=SCHEMA, status=outcome, inputs=completed,
                next_sequence=config['span_origin']['first_sequence']+completed,
                actions=actions, network_requests=http.requests, network_writes=http.writes,
                automatic_intake_enabled=watching, eos_meaning='selected_span_only',
                analysis_completion_verified=False, capture_completeness_proven=False,
                physical_continuity_proven=False, **(dict(network_response_bytes_include_recovery_reservations=True)
                    if export_recovery_attempts else {}), **extra)
        def helper(function, **kwargs):
            nonlocal helper_actions
            guard.check(); helper_actions += 1
            return function(**kwargs)
        def pause(outcome):
            nonlocal last_status
            last_status = outcome
            if actions >= max_actions: return result('action_limit')
            if polls >= max_polls: return result('poll_limit')
            guard.pause(min(poll_seconds, max(.000001, guard.end-time.monotonic())))
            check(False)
            return None
        def options():
            guard.check()
            return dict(job_dir=config['job_dir'], api=config['api'], session_id=config['session_id'],
                capture_id=config['capture_id'], exclusive_writer=True,
                limits=replace(effective,deadline_seconds=max(.000001,guard.end-time.monotonic())),
                transport=http, cancel=cancel)
        def terminal_intent(kind):
            value = dict(schema=SCHEMA, config_sha256=_hash(config), kind=kind,
                         input_intents_sha256=_hash(intents))
            if kind == 'export' and export_recovery_attempts:
                value['recovery_attempts'] = export_recovery_attempts
            if kind == 'export' and native_turns:
                value['export_policy'] = NATIVE_EXPORT_POLICY
            path = root/(kind+'.intent.json')
            if not os.path.lexists(path): single.manifest_write(path,value)
            _durable(path,value); return value
        def reconcile():
            nonlocal actions, helper_actions, polls, completed, select_attempt
            while actions < max_actions:
                if watching:
                    check(False)
                    if polls >= max_polls: return result('poll_limit')
                    polls += 1
                unpublished = [] if watching and wait_for_marker_last else None
                candidates = _inspect(config,intents,remaining,unpublished=unpublished)
                guard.check()
                if unpublished:
                    # Known interrupted terminal work is not a benign arrival
                    # wait, even though this pass would otherwise make no calls.
                    if os.path.lexists(root/'finalize.intent.json') and not os.path.lexists(root/'finalize.done.json'):
                        require(finalize, 'runner_finalize_resume_authorization_required')
                    if os.path.lexists(root/'export.intent.json') and not os.path.lexists(root/'export.done.json') and os.path.lexists(config['output_dir']):
                        raise Refused('runner_export_publication_uncertain_preserve_output')
                    # A waiting publication must not mask an already known
                    # selection error among otherwise complete next arrivals.
                    if len(intents) > completed and select_attempt is not None:
                        require(Path(intents[-1]['path']).name == select_attempt,
                                'runner_frozen_selection_changed')
                    if len(intents) == completed and not os.path.lexists(root/'finalize.intent.json') and not (
                            intents and _terminal_allowed(config,intents,remaining)):
                        available = candidates.get(config['span_origin']['first_sequence']+completed, [])
                        if available:
                            if select_attempt is None:
                                require(len(available) == 1, 'runner_explicit_duplicate_selection_required')
                            else:
                                require(sum(path.name == select_attempt for path, _ in available) == 1,
                                        'runner_selected_attempt_not_found')
                    stopped = pause('waiting_for_publication')
                    if stopped is not None: return stopped
                    continue
                _durable(root/'config.json',config)
                if intake.POLICY.extra_job_files(Path(config['job_dir'])):
                    require(_terminal_allowed(config,intents,remaining), 'logical_boundary_invalid')
                if os.path.lexists(root/'export.done.json'):
                    saved = single.manifest_read(root/'export.done.json')
                    expected = _export_receipt(root,config,single.manifest_read(root/'export.intent.json'),guard)
                    require(_same(saved,expected),'runner_export_receipt_changed')
                    return result('exported',export_completed=True, **({key:saved[key] for key in
                        ('export_attempts','revision_conflicts','selected_attempt')} if 'recovery_attempts' in
                        single.manifest_read(root/'export.intent.json') else {}))
                if os.path.lexists(root/'finalize.done.json'):
                    if os.path.lexists(root/'export.intent.json') and os.path.lexists(config['output_dir']):
                        raise Refused('runner_export_publication_uncertain_preserve_output')
                    actions += 1
                    observed = helper(status.run, **options())
                    if export and observed['status'] == 'completed':
                        if watching and actions >= max_actions: return result('action_limit')
                        terminal = terminal_intent('export')
                        require(not os.path.lexists(config['output_dir']), 'runner_export_publication_uncertain_preserve_output')
                        if watching: actions += 1
                        if export_recovery_attempts:
                            def recovery_cancel():
                                guard.check()
                                return False
                            http.export_recovery_phase = True
                            try:
                                selected = options(); selected['cancel'] = recovery_cancel
                                helper(export_recovery.run, **selected, output_dir=config['output_dir'],
                                    recovery_limits=export_recovery.RecoveryLimits(attempts=export_recovery_attempts))
                            finally:
                                http.export_recovery_phase = False
                        else:
                            helper(exporter.run, **options(), output_dir=config['output_dir'], **native_options)
                        receipt = _export_receipt(root,config,terminal,guard)
                        single.manifest_write(root/'export.done.json',receipt)
                        return result('exported',export_completed=True, **({key:receipt[key] for key in
                            ('export_attempts','revision_conflicts','selected_attempt')} if export_recovery_attempts else {}))
                    if watching and observed['status'] == 'pending':
                        require(observed['analysis_completion_verified'] is False, 'runner_pending_result_changed')
                        stopped = pause('pending')
                        if stopped is not None: return stopped
                        continue
                    return result(observed['status'],analysis_status=observed['status'],
                                  primary_completion_observed=observed['analysis_completion_verified'])
                if os.path.lexists(root/'finalize.intent.json'):
                    require(finalize, 'runner_finalize_resume_authorization_required')
                    _durable(root/'finalize.intent.json')
                    actions += 1; helper(intake.run, **options(),finalize=True,acknowledge_span_local_closure=True)
                    terminal = single.manifest_read(root/'finalize.intent.json')
                    single.manifest_write(root/'finalize.done.json',_done(terminal)); continue
                if completed == len(intents) and intents and _terminal_allowed(config,intents,remaining):
                    if not finalize: return result('awaiting_finalize')
                    terminal_intent('finalize'); continue
                if len(intents) == completed:
                    if completed == config['max_inputs']:
                        observed_capacity = capacity.inspect(config,intents,completed,None,guard)
                        return result('input_capacity_reached',capacity=observed_capacity)
                    next_sequence = config['span_origin']['first_sequence']+completed
                    available = candidates.get(next_sequence, [])
                    if not available:
                        if not watching: return result('waiting_for_input')
                        stopped = pause('waiting_for_input')
                        if stopped is not None: return stopped
                        continue
                    if select_attempt is None:
                        require(len(available) == 1, 'runner_explicit_duplicate_selection_required')
                        path, report = available[0]
                    else:
                        chosen = [(path,report) for path,report in available if path.name == select_attempt]
                        require(len(chosen) == 1,'runner_selected_attempt_not_found')
                        path,report = chosen[0]; select_attempt = None
                    value = _intent(config,path,report,completed)
                    current = _binding(config,value,completed,remaining)
                    previous = _binding(config,intents[-1],completed-1,remaining) if intents else None
                    intake.POLICY.validate_edge(previous,current)
                    observed_capacity = capacity.inspect(config,intents,completed,report,guard)
                    if not observed_capacity['allowed']:
                        return result('capacity_reached',capacity=observed_capacity)
                    single.manifest_write(_file(root,completed,'intent'),value); intents.append(value)
                else:
                    if select_attempt is not None:
                        require(Path(intents[-1]['path']).name == select_attempt,'runner_frozen_selection_changed')
                        select_attempt = None
                value = intents[-1]
                _durable(_file(root,completed,'intent'),value)
                _binding(config,value,completed,remaining)
                job = Path(config['job_dir']); fresh = not os.path.lexists(job)
                recovery = not fresh and not os.path.lexists(job/'uploader/uploader.json')
                require(not recovery or completed == 0, 'runner_checkpoint_missing_after_admission')
                http.check_identity()
                actions += 1
                accepted = helper(intake.run, **options(),delivery=value['path'],start=fresh,recover_initial=recovery,
                    acknowledge_span_local_closure=True,max_hours=config['max_hours'],max_inputs=config['max_inputs'])
                require(accepted['inputs'] == completed+1 and not accepted['capture_finalized'], 'runner_intake_result_changed')
                _job_prefix(config,intents,completed,writes=True)
                single.manifest_write(_file(root,completed,'done'),_done(value)); completed += 1
            guard.check()
            return result('action_limit')
        try:
            return reconcile()
        except (Exception, KeyboardInterrupt) as exc:
            if not watching: raise
            cause = exc.cause if isinstance(exc, _WatchTransportStopped) else exc
            failure = single.failure_report(cause)
            failure.pop('schema'); failure.pop('status')
            if type(cause) is u.HTTPFailure and cause.admission_reason in u.STRICT_BUSY_REASONS:
                failure['admission_reason'] = cause.admission_reason
            if isinstance(exc, _WatchTransportStopped): failure['reason'] = str(exc)
            stopped = 'cancelled' if isinstance(cause, (InterruptedError, KeyboardInterrupt)) or (cancel and cancel()) else (
                'deadline_reached' if time.monotonic() >= guard.end else 'unresolved')
            return result(stopped, preserve_inbox_state_job_and_output=True, **failure)
    finally: os.close(lock)


def run(*, state_dir, exclusive_writer=False, acknowledge_span_local_closure=False,
        finalize=False, export=False, export_recovery_attempts=0, select_attempt=None, max_actions=1, max_requests=1024,
        max_network_writes=512, deadline_seconds=120, limits=None, transport=None, cancel=None, native_turns=False):
    """One bounded reconciliation. Existing manual-resume behavior is unchanged."""
    return _run(state_dir=state_dir, exclusive_writer=exclusive_writer,
        acknowledge_span_local_closure=acknowledge_span_local_closure, finalize=finalize,
        export=export, export_recovery_attempts=export_recovery_attempts, select_attempt=select_attempt, max_actions=max_actions,
        max_requests=max_requests, max_network_writes=max_network_writes,
        deadline_seconds=deadline_seconds, limits=limits, transport=transport, cancel=cancel,
        **native_export_options(native_turns, export_recovery_attempts))


def watch(*, state_dir, exclusive_writer=False, acknowledge_span_local_closure=False,
          finalize=False, export=False, export_recovery_attempts=0, select_attempt=None, max_actions=256, max_requests=1024,
          max_network_writes=512, max_network_bytes=MAX_NETWORK_BYTES, deadline_seconds=120,
          poll_seconds=5, max_polls=256, wait_for_marker_last=False, limits=None, transport=None, cancel=None,
          busy_read_retries=0, native_turns=False):
    """Finite watch. By default only clean missing input and pending status repeat.

    The original runner lock, exact configuration, transport and all budgets stay
    live for the entire invocation. Every refusal/uncertain effect requires an
    explicit new invocation. Explicit busy-read recovery repeats only allowlisted
    exact GETs after typed strict-store admission failures, at most three times in
    total. Export recovery keeps its separate transcript-revision contract.
    """
    return _run(state_dir=state_dir, exclusive_writer=exclusive_writer,
        acknowledge_span_local_closure=acknowledge_span_local_closure, finalize=finalize,
        export=export, export_recovery_attempts=export_recovery_attempts, select_attempt=select_attempt, max_actions=max_actions,
        max_requests=max_requests, max_network_writes=max_network_writes,
        max_network_bytes=max_network_bytes, deadline_seconds=deadline_seconds,
        poll_seconds=poll_seconds, max_polls=max_polls, wait_for_marker_last=wait_for_marker_last, watching=True, limits=limits,
        transport=transport, cancel=cancel, busy_read_retries=busy_read_retries,
        **native_export_options(native_turns, export_recovery_attempts))


def preflight(*, state_dir, select_attempt=None, deadline_seconds=120):
    """Finite local-only next-delivery report, without selecting or admitting."""
    require(type(deadline_seconds) in (int,float) and 0 < deadline_seconds <= 120,
            'runner_deadline_bound')
    require(select_attempt is None or type(select_attempt) is str and inbox.NAME.fullmatch(select_attempt),
            'runner_selection_format')
    root = _path(str(state_dir)); single.private_directory(root)
    lock = engine.held_lock(root/'runner.lock')
    try:
        config = single.manifest_read(root/'config.json'); _config(config,root)
        intents, completed = _ledger(root,config); _job_prefix(config,intents,completed)
        if not intents:
            require(not os.path.lexists(config['job_dir']), 'runner_unbound_existing_job')
        base = dict(schema=SCHEMA, network_requests=0, network_writes=0,
                    selection_written=False, automatic_intake_enabled=False,
                    remote_completion_checked=False, analysis_completion_verified=False)
        if len(intents) != completed:
            return dict(base,status='pending_selection_preserved',inputs=completed,
                        exact_existing_retry_required=True)
        guard = u.Guard(u.Limits(deadline_seconds=deadline_seconds))
        def remaining():
            guard.check(); return max(.000001,guard.end-time.monotonic())
        if intake.POLICY.extra_job_files(Path(config['job_dir'])):
            require(_terminal_allowed(config,intents,remaining), 'logical_boundary_invalid')
        # Terminal receipt precedence is local-only. Never invoke status.run,
        # uploader recovery or inference to turn saved receipts into live claims.
        if os.path.lexists(root/'export.done.json'):
            saved = single.manifest_read(root/'export.done.json')
            expected = _export_receipt(root,config,single.manifest_read(root/'export.intent.json'),guard)
            require(_same(saved,expected),'runner_export_receipt_changed')
            return dict(base,status='exported_locally_verified',export_artifacts_verified=True)
        if os.path.lexists(root/'finalize.done.json'):
            return dict(base,status='finalized_locally_recorded',
                        export_intent_present=os.path.lexists(root/'export.intent.json'))
        if os.path.lexists(root/'finalize.intent.json'):
            return dict(base,status='finalize_pending',explicit_finalize_resume_required=True)
        if intents and _terminal_allowed(config,intents,remaining):
            _binding(config,intents[-1],completed-1,remaining)
            return dict(base,status='awaiting_finalize',explicit_finalize_required=True)
        if completed == config['max_inputs']:
            return dict(base,status='capacity_reached',capacity=capacity.inspect(config,intents,completed,None,guard))
        available = _inspect(config,intents,remaining).get(config['span_origin']['first_sequence']+completed,[])
        if not available:
            return dict(base,status='waiting_for_input',capacity=capacity.inspect(config,intents,completed,None,guard),
                        next_delivery_checked=False)
        if select_attempt is not None: available = [(p,r) for p,r in available if p.name == select_attempt]
        require(len(available) == 1,'runner_explicit_duplicate_selection_required')
        path, report = available[0]
        current = _binding(config,_intent(config,path,report,completed),completed,remaining)
        previous = _binding(config,intents[-1],completed-1,remaining) if intents else None
        intake.POLICY.validate_edge(previous,current)
        observed = capacity.inspect(config,intents,completed,report,guard)
        return dict(base,status='capacity_available' if observed['allowed'] else 'capacity_reached',
                    capacity=observed,next_delivery_checked=True)
    finally:
        os.close(lock)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='command',required=True)
    setup = modes.add_parser('configure',help='Local-only immutable binding from an explicitly selected first delivery')
    for name in ('state-dir','inbox-dir','job-dir','first-delivery','api','session-id','capture-id'):
        setup.add_argument('--'+name,required=True)
    setup.add_argument('--output-dir'); setup.add_argument('--receiver-state-dir')
    setup.add_argument('--max-hours',type=int,default=12)
    setup.add_argument('--max-inputs',type=int,default=192)
    initialize = modes.add_parser('initialize',help='Explicitly create one fresh session and bind; never retry uncertain creation')
    for name in ('state-dir','inbox-dir','job-dir','first-delivery','api','capture-id'):
        initialize.add_argument('--'+name,required=True)
    initialize.add_argument('--output-dir');initialize.add_argument('--receiver-state-dir')
    initialize.add_argument('--session-name',default='')
    for name in ('preserve-reviewed-identity-group','identity-generation','identity-policy-sha256','identity-source-session-id'):
        initialize.add_argument('--'+name)
    initialize.add_argument('--max-hours',type=int,default=12)
    initialize.add_argument('--max-inputs',type=int,default=192)
    initialize.add_argument('--deadline-seconds',type=float,default=30)
    initialize.add_argument('--resume',action='store_true')
    initialize.add_argument('--exclusive-writer',action='store_true',required=True)
    initialize.add_argument('--acknowledge-span-local-closure',action='store_true',required=True)
    capacity_parser = modes.add_parser('preflight',help='Read-only local capacity report; no selection or HTTP')
    capacity_parser.add_argument('--state-dir',required=True)
    capacity_parser.add_argument('--select-attempt')
    capacity_parser.add_argument('--deadline-seconds',type=float,default=120)
    for mode in ('run', 'watch'):
        execute = modes.add_parser(mode,help=('One bounded reconciliation' if mode == 'run'
            else 'Finite foreground polling; explicit resume after any refusal'))
        execute.add_argument('--state-dir',required=True)
        execute.add_argument('--exclusive-writer',action='store_true',required=True)
        execute.add_argument('--acknowledge-span-local-closure',action='store_true',required=True)
        execute.add_argument('--finalize',action='store_true'); execute.add_argument('--export',action='store_true')
        execute.add_argument('--export-recovery-attempts',type=int,choices=(0,2,3),default=0)
        execute.add_argument('--native-turns',action='store_true')
        execute.add_argument('--select-attempt'); execute.add_argument('--max-actions',type=int,default=256 if mode == 'watch' else 1)
        execute.add_argument('--max-requests',type=int,default=1024); execute.add_argument('--max-network-writes',type=int,default=512)
        execute.add_argument('--deadline-seconds',type=float,default=120)
        if mode == 'watch':
            execute.add_argument('--busy-read-retries',type=int,choices=range(4),default=0)
            execute.add_argument('--poll-seconds',type=float,default=5)
            execute.add_argument('--max-polls',type=int,default=256)
            execute.add_argument('--wait-for-marker-last',action='store_true')
            execute.add_argument('--max-network-bytes',type=int,default=MAX_NETWORK_BYTES)
    args = vars(parser.parse_args(argv)); command=args.pop('command')
    previous_signals = {}; received_signal = []
    try:
        if command == 'watch':
            def cancelled(signum, frame):
                if not received_signal: received_signal.append(signum)
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_signals[signum] = signal.signal(signum, cancelled)
            args['cancel'] = lambda: bool(received_signal)
        if command == 'configure':
            state=args.pop('state_dir'); first=args['first_delivery']; config=config_from_delivery(**args)
            value=configure(state_dir=state,config=config,first_delivery=first)
        elif command == 'initialize':
            from .recorder_runner_initialize import initialize
            value=initialize(**args)
        elif command == 'preflight': value=preflight(**args)
        else: value=(watch if command == 'watch' else run)(**args)
    except (Exception,KeyboardInterrupt) as exc:
        value=single.failure_report(exc); value.update(schema=SCHEMA,automatic_intake_enabled=False,
            preserve_inbox_state_job_and_output=True)
        print(json.dumps(value,sort_keys=True),file=sys.stderr)
        return (128 + received_signal[0]) if received_signal else 2
    finally:
        for signum, handler in previous_signals.items(): signal.signal(signum, handler)
    failed = value['status'] in ('unresolved','cancelled','deadline_reached')
    print(json.dumps(value,sort_keys=True),file=sys.stderr if failed else sys.stdout)
    return (128 + received_signal[0]) if received_signal else (2 if failed else 0)


if __name__ == '__main__': raise SystemExit(main())
