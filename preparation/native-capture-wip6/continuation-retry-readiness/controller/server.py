"""Actual legacy app plus bounded private control; no replacement provider."""
from __future__ import annotations
import dataclasses
from contextlib import closing
import functools
import hashlib
import math
import importlib.util
import inspect
import json
import os
from pathlib import Path
import socket
import sys
import sqlite3
import threading
import time
from common import (Admission, FRAMES, MIB, require, atomic_json, bounded_read,
                    fixed_wav, retain_raw, sha, canonical)


def load_admission(spec, deadlines):
    pin = spec['storage_admission']['helper']
    path = Path(pin['path'])
    require(sha(bounded_read(path, MIB, deadlines.check)) == pin['sha256'], 'admission_helper_pin')
    desc = importlib.util.spec_from_file_location('capture_storage_admission', path)
    module = importlib.util.module_from_spec(desc)
    desc.loader.exec_module(module)
    return module


def snapshot(store, path, check):
    """Whole primary logical snapshot; first is retained, later comparisons stream to hash.

    SQL CASE gates each row before Python materialization. A progress handler bounds
    virtual-machine work, while typed fields stream in <=16KiB encoding chunks. No
    complete SQL literal, entire JSON string, or second snapshot copy is constructed.
    """
    path = Path(path)
    retain = not path.exists()
    out = path.open('xb') if retain else None
    digest, written, rows, steps, total_sql_bytes = hashlib.sha256(), 0, 0, 0, 0
    def emit(raw):
        nonlocal written
        check()
        require(len(raw) <= 16384 and written+len(raw) <= 512*1024, 'whole_primary_snapshot_cap')
        digest.update(raw);written += len(raw)
        if out is not None:out.write(raw)
    def value(item):
        if item is None:emit(b'N;');return
        if type(item) is int:emit(('I'+str(item)+';').encode());return
        if type(item) is float:
            require(math.isfinite(item), 'snapshot_nonfinite_SQL_real')
            emit(('F'+item.hex()+';').encode());return
        require(type(item) in (str,bytes), 'snapshot_SQL_type')
        # SQLite prechecks byte lengths. Characters <= bytes, so this is a safe second check.
        require(len(item) <= 8*MIB, 'snapshot_field_bound')
        emit(b'T:' if isinstance(item,str) else b'B:')
        for at in range(0,len(item),4096):
            raw=item[at:at+4096].encode('utf-8') if isinstance(item,str) else item[at:at+4096]
            emit(str(len(raw)).encode()+b':');emit(raw)
        emit(b'0:;')
    def row(items):
        emit(('R'+str(len(items))+':').encode())
        for item in items:value(item)
        emit(b'\n')
    def progress():
        nonlocal steps
        steps += 1000
        try:
            check();require(steps <= 20_000_000, 'snapshot_SQL_VM_budget')
            return 0
        except BaseException:return 1
    quote=lambda name:'"'+name.replace('"','""')+'"'
    try:
        with closing(sqlite3.connect(f'file:{store.path}?mode=ro',uri=True,timeout=1)) as db:
            db.set_progress_handler(progress,1000);db.execute('BEGIN')
            emit(b'SQLITE-LOGICAL-SNAPSHOT-V1\n')
            schema=[]
            for record in db.execute("SELECT type,name,tbl_name,CASE WHEN length(CAST(sql AS BLOB))<=65536 THEN sql END, coalesce(length(CAST(sql AS BLOB)),0) FROM sqlite_master ORDER BY type,name"):
                require(len(schema)<256 and record[4]<=65536,'snapshot_schema_bound')
                row(record[:4]);schema.append(record)
            tables=[r[1] for r in schema if r[0]=='table']
            require(len(tables)<=128,'snapshot_table_cap')
            for table in tables:
                require(type(table) is str and len(table)<=256,'snapshot_table_name')
                columns=[r[1] for r in db.execute('PRAGMA table_info('+quote(table)+')')]
                require(1<=len(columns)<=128,'snapshot_column_cap')
                row(['TABLE',table,*columns])
                lengths='+'.join('coalesce(length(CAST('+quote(c)+' AS BLOB)),0)' for c in columns)
                extent=db.execute('SELECT count(*),coalesce(sum('+lengths+'),0),coalesce(max('+lengths+'),0) FROM '+quote(table)).fetchone()
                total_sql_bytes += extent[1]
                require(extent[0]<=10000-rows and total_sql_bytes<=512*1024 and extent[2]<=8*MIB,
                        'snapshot_SQL_pre_materialization_bound')
                condition='('+lengths+')<='+str(8*MIB)
                selected=','.join('CASE WHEN '+condition+' THEN '+quote(c)+' END' for c in columns)
                order=','.join(quote(c) for c in columns)
                query='SELECT '+condition+','+selected+' FROM '+quote(table)+' ORDER BY '+order
                for record in db.execute(query):
                    check();rows+=1
                    require(rows<=10000 and record[0]==1,'snapshot_row_count_or_bytes')
                    row(record[1:])
            db.rollback()
        if out is not None:
            out.flush();os.fsync(out.fileno())
        check()
        return {'bytes':written,'sha256':digest.hexdigest(), 'rows':rows,'SQL_VM_steps_upper':steps+1000,
                'retained':retain,'artifact':path.name if retain else None,
                'scope':'whole typed SQLite logical snapshot; no tables or columns excluded'}
    finally:
        if out is not None:out.close()


class ServerHooks:
    def __init__(self, spec, deadlines, journal, capture_allowed):
        self.spec, self.deadlines, self.journal = spec, deadlines, journal
        self.out = Path(spec['output_dir'])
        self.capture_allowed = capture_allowed
        self.labels, self.turns, self.epoch = None, None, None
        self.raw_count = 0
        self.admission = load_admission(spec, deadlines)
        cap = int(capture_allowed)
        self.rpc = Admission({name: cap for name in ('reset', 'push', 'finish', 'turns')},
                             deadlines.check, journal.emit, ['reset', 'push', 'finish', 'turns'])
        self.jobs = Admission({'analyzer': cap, 'commit': cap, 'worker_start': cap,
                               'witness_finalize': cap}, deadlines.check, journal.emit)
        self.core = None

    def raw(self, path, name):
        target = self.out / (name+'.raw')
        if target.exists():
            return
        require(self.raw_count < 12, 'raw_file_count_cap')
        receipt = retain_raw(path, target, check=self.deadlines.cleanup_check)
        self.raw_count += 1
        atomic_json(self.out/(name+'.receipt.json'), receipt, 4096)
        self.journal.emit('raw_preservation', **receipt)

    def raw_finally(self, *items):
        original_exception = sys.exc_info()[1]
        failure = None
        for path, name in items:
            try:
                self.raw(path, name)
            except BaseException as exc:
                failure = exc
                try:
                    self.journal.emit('raw_diagnostic_failure', artifact=name,
                                      exception=type(exc).__name__, original_exception_preserved=original_exception is not None)
                except BaseException:
                    pass
        if failure is not None and original_exception is None:
            raise failure

    def active(self, role, end):
        atomic_json(self.out/f'{role}-active.json',
                    {'role': role, 'entered_at': time.monotonic(), 'deadline': float(end),
                     'spec_sha256': self.spec['_sha256']})

    def native_result(self, action, result):
        from audio_backend.native_owned_support import _turns, validate_observed_provenance
        require(type(result) is dict and '_error' not in result, 'native_error_response')
        if action == 'reset':
            require(set(result) == {'state', 'provenance'}, 'native_reset_shape')
            validate_observed_provenance(result['provenance'])
            self.epoch = result['state']['epoch_id']
            require(result['provenance']['diarization_library_sha256'] ==
                    self.spec['native_library_sha256'], 'native_loaded_library_pin')
            require(result['provenance']['diarization_runtime_files_sha256'] ==
                    self.spec['native_runtime_hashes'], 'native_loaded_runtime_pins')
        elif action == 'turns':
            require(set(result) == {'turns'}, 'native_turns_shape')
            _turns(result['turns'], FRAMES, self.epoch)
            self.turns = json.loads(canonical(result['turns']))
            self.labels = self.admission.admit_native(result['turns'], frames=FRAMES)
        elif action == 'finish':
            require(result.get('finished') is True and all(round(result[k]*16000) == FRAMES
                    for k in ('input_seconds', 'processed_seconds', 'stable_seconds')),
                    'native_incomplete_eof')

    def install(self):
        from audio_backend.native_worker import NativeProcess
        from audio_backend.resources import AnalyzerProcess
        from audio_backend.storage import Store
        from audio_backend.native_owned_support import NativeWitness
        from audio_backend.worker import Worker
        for owner, symbol in ((NativeProcess,'request'), (AnalyzerProcess,'analyze'),
                              (Store,'commit_window'), (NativeWitness,'finalize'), (Worker,'start')):
            original = getattr(owner,symbol)
            origin = str(Path(inspect.getsourcefile(original)).resolve())
            require(origin in self.spec['installed_files'], 'server_original_symbol_unpinned')
            self.journal.emit('bound_server_original_symbol', module=original.__module__,
                              qualname=original.__qualname__, origin=origin,
                              sha256=self.spec['installed_files'][origin]['sha256'])
        original_request = NativeProcess.request
        @functools.wraps(original_request)
        def request(obj, action, stop, **kwargs):
            end = self.deadlines.call_end(120)
            output = obj.root / f'{obj.counter+1}.json'
            label = f'native-{obj.counter+1}-{action}'
            def invoke():
                self.active('native', end)
                if action == 'push':
                    proof, actual, _ = fixed_wav(kwargs['wav'], lambda: self.deadlines.check(end))
                    (self.out/'native-submitted.wav').write_bytes(actual)
                    self.journal.emit('native_transport_samples', **proof)
                prior = getattr(obj, 'response_observer', None)
                def observer(result):
                    self.raw(output, label)
                    if prior is not None:
                        prior(result)
                obj.response_observer = observer
                try:
                    result = original_request(obj, action,
                        lambda: stop() or time.monotonic() >= end or self.deadlines.stop.exists(), **kwargs)
                    self.deadlines.check(end)
                finally:
                    obj.response_observer = prior
                    self.raw_finally((output, label), (output.with_suffix('.pending'), label+'-pending'))
                if action == 'turns':
                    self.spec['_observe_live_mappings']('native', obj.proc, end)
                self.deadlines.check(end)
                return result
            return self.rpc.invoke(action, invoke, validate=lambda result: self.native_result(action, result),
                                   return_check=lambda: self.deadlines.check(end))
        NativeProcess.request = request
        original_analyze = AnalyzerProcess.analyze
        @functools.wraps(original_analyze)
        def analyze(obj, wav, output, stop, diarization_path=None, analysis_options=None):
            end = self.deadlines.call_end(300)
            def before():
                require(self.capture_allowed and self.turns is not None and self.labels is not None,
                        'analyzer_without_admitted_native_turns')
                require(all(self.rpc.counts[n]['accepted'] == 1 for n in self.rpc.caps), 'native_rpc_not_complete')
                require(diarization_path is not None and analysis_options is None, 'analyzer_native_contract')
                diarization = json.loads(bounded_read(diarization_path, MIB, self.deadlines.check))
                require(diarization['turns'] == self.turns, 'prepared_native_turns_changed')
                proof, actual, _ = fixed_wav(wav, self.deadlines.check)
                (self.out/'analyzer-submitted.wav').write_bytes(actual)
                self.journal.emit('analyzer_submitted_samples', **proof)
                self.active('analyzer', end)
            def invoke():
                try:
                    result = original_analyze(obj, wav, output,
                        lambda: stop() or time.monotonic() >= end or self.deadlines.stop.exists(),
                        diarization_path=diarization_path, analysis_options=analysis_options)
                    self.raw(output, 'analyzer-result')
                    self.deadlines.check(end)
                    wire = bounded_read(self.out/'analyzer-result.raw', 8*MIB, self.deadlines.check)
                    require(json.loads(wire) == result, 'analyzer_raw_object_mismatch')
                    receipt = self.admission.admit_raw(wire, native_labels=self.labels,
                                         expected_speaker_sha256=self.spec['speaker_model_sha256'])
                    self.journal.emit('analyzer_shape_admitted', **receipt)
                    self.deadlines.check(end)
                finally:
                    self.raw_finally((output, 'analyzer-result'),
                                     (Path(output).with_suffix('.pending'), 'analyzer-result-pending'))
                self.spec['_observe_live_mappings']('analyzer', obj.proc, end)
                self.deadlines.check(end)
                return result
            return self.jobs.invoke('analyzer', invoke, before_entry=before,
                                    return_check=lambda: self.deadlines.check(end))
        AnalyzerProcess.analyze = analyze
        original_commit = Store.commit_window
        @functools.wraps(original_commit)
        def commit(obj, chunk, offset, end, result):
            def before():
                require(offset == 0. and end == FRAMES/16000 and chunk['timeline_start'] == 0., 'whole_core_geometry')
                self.admission.admit(result, native_labels=self.labels,
                           expected_speaker_sha256=self.spec['speaker_model_sha256'])
                atomic_json(self.out/'precommit.json', {'chunk': chunk, 'offset': offset,
                            'end': end, 'result': result}, 10*MIB)
            value = self.jobs.invoke('commit', original_commit, obj, chunk, offset, end, result,
                                     before_entry=before)
            self.journal.emit('production_commit_return', committed=value is True)
            return value
        Store.commit_window = commit
        original_finalize = NativeWitness.finalize
        @functools.wraps(original_finalize)
        def finalize(obj, native, offset, end):
            def invoke():
                atomic_json(self.out/'witness-before-finalize.json',
                            {'source': dataclasses.asdict(obj.source), 'states': obj.states,
                             'turns': obj.turns, 'material': obj.material, 'native': native,
                             'offset': offset, 'end': end}, MIB)
                artifact = original_finalize(obj, native, offset, end)
                # Preserve exactly the original proof and PCM. No second finalize or fabricated witness.
                (self.out/'captured-proof.json').write_bytes(artifact.proof)
                (self.out/'captured.pcm16le').write_bytes(artifact.pcm16le)
                atomic_json(self.out/'capture-status.json',
                            {'selected': artifact.selected, 'reason': artifact.reason,
                             'source': dataclasses.asdict(artifact.source),
                             'proof_sha256': sha(artifact.proof), 'pcm_sha256': sha(artifact.pcm16le),
                             'support': artifact.support.to_dict() if artifact.support else None})
                return artifact
            return self.jobs.invoke('witness_finalize', invoke)
        NativeWitness.finalize = finalize
        original_start = Worker.start
        @functools.wraps(original_start)
        def start(obj):
            return self.jobs.invoke('worker_start', original_start, obj)
        Worker.start = start
        self.journal.emit('installed_server_hooks', native_class_module=NativeProcess.__module__,
                          analyzer_class_module=AnalyzerProcess.__module__, production_factory=None)

    def counts(self):
        return {'rpc': self.rpc.counts, 'jobs': self.jobs.counts,
                'observed_native_labels': sorted(self.labels) if self.labels is not None else None}


def cache_state(worker):
    cache = worker.native_owned_support_cache
    if cache is None:
        return {'present': False}
    cache.drain()
    with cache._gate(allow_blocked=True):
        with cache._lease_lock:
            return {'present': True, 'epoch': cache.epoch, 'entries': len(cache._entries),
                    'retained_bytes': cache._retained, 'marker_bytes': cache._marker_bytes,
                    'pending': cache._pending is not None, 'lease_count': cache._lease_count,
                    'lease_bytes': cache._lease_bytes, 'cleanup_pending': cache._cleanup_pending.is_set(),
                    'blocked': cache._blocked.is_set(), 'revision': cache._revision}


def run_server(spec, deadlines, journal, ledger, arguments, launches):
    require(len(arguments) == 2 and arguments[0] in ('capture', 'restart-on', 'restart-off'), 'server_arguments')
    mode, command_path = arguments
    command_dir = Path(command_path)
    require(command_dir.resolve() == command_dir and command_dir.parent == Path(spec['output_dir']), 'command_scope')
    hooks = ServerHooks(spec, deadlines, journal, mode == 'capture')
    hooks.install()
    from audio_backend.app import create_app
    from audio_backend.settings import Settings
    import uvicorn
    settings = Settings(**spec['settings'])
    settings.native_owned_support_enabled = mode != 'restart-off'
    app = create_app(settings, analyzer_factory=None)
    require(app.state.worker.analyzer_factory is None, 'real_analyzer_factory_required')
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    sock.listen(16)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=sock.getsockname()[1],
                           loop='asyncio', http='h11', ws='none', lifespan='on', access_log=False,
                           log_level='warning', timeout_keep_alive=1, timeout_graceful_shutdown=15))
    errors = []
    def control():
        sequence = 0
        started = False
        try:
            while not server.should_exit:
                deadlines.check()
                if server.started and not started:
                    atomic_json(command_dir/'ready.json', {'port': sock.getsockname()[1], 'pid': os.getpid(),
                                'mode': mode, 'counts': hooks.counts(), 'cache': cache_state(app.state.worker)}, 4096)
                    started = True
                path = command_dir/f'{sequence:03}.command.json'
                if not path.exists():
                    ledger.census()
                    time.sleep(.05)
                    continue
                require(started and sequence < 20, 'private_control_sequence_cap')
                cmd = json.loads(bounded_read(path, 4096, deadlines.check))
                action = cmd.get('action')
                result = {'action': action}
                if action == 'start-worker':
                    require(mode == 'capture', 'restart_must_not_start_worker')
                    with app.state.store.db() as db:
                        rows = db.execute('SELECT * FROM chunks').fetchall()
                    require(len(rows) == 1 and rows[0]['status'] == 'queued' and
                            rows[0]['duration'] == FRAMES/16000 and rows[0]['timeline_start'] == 0,
                            'one_original_queued_input_required')
                    require(not launches['audio_backend.runner'] and not launches['audio_backend.native_runner'],
                            'provider_launched_before_worker')
                    primary = json.loads(bounded_read(Path(spec['output_dir'])/'primary-window.json', 4096, deadlines.check))
                    deadlines.bind_primary(primary, spec['_sha256'])
                    atomic_json(Path(spec['output_dir'])/'private-queued-source.json', dict(rows[0]), MIB)
                    app.state.worker.start()
                elif action in ('flag-off', 'flag-on'):
                    require(mode == 'capture', 'restart_flag_change_unapproved')
                    settings.native_owned_support_enabled = action == 'flag-on'
                elif action == 'snapshot':
                    require(app.state.worker.current_chunk is None, 'snapshot_while_worker_busy')
                    result['snapshot'] = snapshot(app.state.store, Path(spec['output_dir'])/'primary-first.snapshot', deadlines.check)
                elif action == 'cache':
                    pass
                elif action == 'stop':
                    server.should_exit = True
                else:
                    raise RuntimeError('Unknown private control command')
                result.update(counts=hooks.counts(), launches=dict(launches), cache=cache_state(app.state.worker))
                atomic_json(command_dir/f'{sequence:03}.response.json', result, 32768)
                sequence += 1
        except BaseException as exc:
            errors.append(type(exc).__name__+': '+str(exc)[:500])
            journal.emit('control_failed', detail=errors[-1])
            server.should_exit = True
    thread = threading.Thread(target=control, name='capture-private-control', daemon=True)
    thread.start()
    try:
        server.run(sockets=[sock])
    finally:
        server.should_exit = True
        thread.join(timeout=max(.001, min(2., deadlines.absolute-time.monotonic())))
        sock.close()
        atomic_json(command_dir/'server-final.json', {'counts': hooks.counts(), 'launches': launches,
                    'control_errors': errors, 'control_stopped': not thread.is_alive(),
                    'worker_stopped': not app.state.worker.thread.is_alive(),
                    'cache': cache_state(app.state.worker)}, 4096)
    require(not errors and not thread.is_alive() and not app.state.worker.thread.is_alive(), 'server_shutdown_incomplete')
