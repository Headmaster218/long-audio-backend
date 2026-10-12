"""Actual dev7 app plus bounded passive cell evidence; no replacement provider."""
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
from common import (Admission, FRAMES, CELL_FRAMES, input_pins, MIB, require, atomic_json, bounded_read,
                    fixed_wav, retain_raw, sha, canonical)


def load_admission(spec, deadlines):
    pin = spec['storage_admission']['helper']
    path = Path(pin['path'])
    require(sha(bounded_read(path, MIB, deadlines.check)) == pin['sha256'], 'admission_helper_pin')
    desc = importlib.util.spec_from_file_location('capture_storage_admission', path)
    module = importlib.util.module_from_spec(desc)
    desc.loader.exec_module(module)
    return module


def admit_native_turns(admission, turns, *, frames=336000):
    """Exact inherited native-turn admission with the one authorized frame bound.

    The immutable helper's analyzer validators remain original. Its native helper
    is fixed to307040, so this finite copy changes only that literal; all predicate
    utilities/caps and the original Rejected exception still come from the helper.
    """
    admission.require(type(frames) is int and frames==336000,'fixed native frame count')
    admission.require(type(turns) is list and len(turns)<=admission.MAX_TURNS,'native turn limit')
    admission.require(len(admission.encoded(turns))<=2**20,'native turn bytes')
    labels=set()
    for turn in turns:
        admission.require(type(turn) is dict,'native turn object required')
        label=turn.get('speaker_local_id')
        admission.require(type(label) is str and 0<len(label)<=128,'native label type or length')
        labels.add(label)
        start,end=turn.get('start'),turn.get('end')
        admission.require(type(start) in (int,float) and type(end) in (int,float) and
                math.isfinite(start) and math.isfinite(end) and
                0<=start<end<=frames/16000,'native turn interval')
    admission.require(len(labels)<=admission.MAX_LABELS,'native local label limit')
    return frozenset(labels)


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
    def __init__(self, spec, deadlines, journal, capture_allowed, ledger=None):
        self.spec, self.deadlines, self.journal = spec, deadlines, journal
        self.out = Path(spec['output_dir'])
        self.capture_allowed = capture_allowed
        self.ledger = ledger
        self._original_waits = {}
        self.labels, self.turns, self.epoch = None, None, None
        self.raw_count = 0
        self.admission = load_admission(spec, deadlines)
        cap = int(capture_allowed)
        self.rpc = Admission({name: cap for name in ('reset', 'push', 'finish', 'turns')},
                             deadlines.check, journal.emit, ['reset', 'push', 'finish', 'turns'])
        self.jobs = Admission({'analyzer': cap, 'commit': cap, 'worker_start': 1,
                               'cell_prepare': cap, 'cell_committed': cap}, deadlines.check, journal.emit)
        self.core = None
        self.claimed = 0
        self.claim_source = None
        self._observation_lock = threading.RLock()
        self._preparing = self._prepared_cells = self._publication = None
        self._witness = None
        self.cell_witness = {'prepared': False, 'commit_returned_true': False,
            'committed_called': False, 'core_frames': [0, FRAMES], 'cells': [],
            'publication': None, 'witness_artifact': 'cell-witness.json', 'errors': []}
        self._copied_lease = None
        self._pin_receipt_done = threading.Event()
        self._publication_saved = False
        self.cell_pin_cleanup = dict(acquire_entered=0, accepted_entered=0, release_entered=0,
            acquired=0, accepted=0, release_requested=0,
            registry_removal_acknowledged=0, release_confirmed=0, permit_release_checked=0,
            exact_registry_absent=False, original_release_returned_true=False)

    def observe(self, event, action):
        """Diagnostics may fail the gate, never replace a producer's answer."""
        try:
            action()
        except Exception as exc:
            with self._observation_lock:
                if len(self.cell_witness['errors']) < 8:
                    self.cell_witness['errors'].append({'event': event,
                        'exception': type(exc).__name__, 'detail': str(exc)[:256]})
            try:self.journal.emit('cell_observation_failed', event_name=event, exception=type(exc).__name__)
            except Exception:pass

    def cell_state(self):
        """Observe publication events; never call Operation.wait/claim/abandon."""
        with self._observation_lock:
            operation = self._publication
            if operation is not None:
                result = None
                if operation.done.is_set():
                    raw = canonical(operation.result)
                    require(len(raw) <= 8192, 'cell_publication_result_cap')
                    result = json.loads(raw)
                self.cell_witness['publication'] = {'kind': 'Operation',
                    'operation_kind': operation.kind, 'done': operation.done.is_set(),
                    'drained': operation.drained.is_set(),
                    'slot_released': operation.slot_released.is_set(),
                    'status': None if result is None else result.get('status'), 'result': result}
                if result is not None and not self._publication_saved and self._witness is not None:
                    self._witness['committed_completion'] = self.cell_witness['publication']
                    self.save_witness()
                    self._publication_saved = True
            raw = canonical({'cell_witness': self.cell_witness,
                             'cell_pin_cleanup': self.cell_pin_cleanup})
            require(len(raw) <= 32768, 'cell_observer_cap')
            return json.loads(raw)

    def save_witness(self):
        with self._observation_lock:
            if self._witness is not None:
                atomic_json(self.out/'cell-witness.json', self._witness, MIB)

    def begin_cells(self, obj, chunk, native, offset, end, result):
        require(self.capture_allowed and self._witness is None, 'single_cell_prepare_observation')
        require(offset == 0. and end == FRAMES/16000 and obj.origin_frame == 0 and
                obj.scope_start == 0., 'cell_committed_core_geometry')
        evidence = {'chunk_id': chunk['id'], 'start': 0, 'end': FRAMES,
                    'native': native['state'], 'core_cut': result['metadata']['core_cut']}
        witness = obj.witness
        self._witness = {'schema': 'dev7-original-cell-witness-v1', 'chunk': chunk,
            'native': native, 'offset': offset, 'end': end, 'scope_start': obj.scope_start,
            'origin_frame': obj.origin_frame, 'analyzer_start': obj.analyzer_start,
            'analyzer_pcm_sha256': None if obj.analyzer is None else sha(obj.analyzer),
            'mapping': None, 'receipts': [], 'commit_evidence': evidence,
            'commit_sha256': sha(canonical(evidence)), 'candidates': [],
            'witness': {key: getattr(witness, key) for key in ('failure','epoch','provenance',
                'state','ring_start','ring_end','replay_start','raw_turns','actions','finished')}}
        # Bound all copied metadata before the producer runs. No ring copy/replay.
        raw = canonical(self._witness)
        require(len(raw) <= MIB, 'cell_witness_metadata_cap')
        self._witness = json.loads(raw)
        self._preparing = (obj.authority, threading.get_ident())

    def mapped_cells(self, obj, parts):
        require(type(parts) is tuple and 1 <= len(parts) <= 128, 'cell_mapping_observer_cap')
        self._witness['mapping'] = [dataclasses.asdict(part) for part in parts]
        ids = sorted({part.chunk_id for part in parts})
        require(len(ids) == 1 and ids[0] == self._witness['chunk']['id'], 'isolated_source_mapping')
        owner = obj.owner
        require(owner._lock.acquire(timeout=.05), 'observer_receipt_lock')
        try:
            receipts = [owner._receipts.get(cid) for cid in ids]
            require(all(receipt is not None for receipt in receipts), 'observed_receipt_missing')
            self._witness['receipts'] = [{'receipt': dataclasses.asdict(receipt),
                'receipt_id': receipt.receipt_id} for receipt in receipts]
        finally:owner._lock.release()

    def prepared_cells(self, obj, cells):
        require(type(cells) is tuple and len(cells) <= 2, 'cell_candidate_count_cap')
        self._prepared_cells = cells
        candidates = []
        previous = -1
        for index, cell in enumerate(cells):
            require(type(cell.proof) is bytes and len(cell.proof) <= 65536 and
                    type(cell.pcm) is bytes and len(cell.pcm) <= CELL_FRAMES*2,
                    'cell_original_material_cap')
            support = cell.support.to_dict()
            bounds = [support['start_frame'], support['end_frame']]
            require(bounds in ([0,CELL_FRAMES],[CELL_FRAMES,FRAMES]) and
                    bounds[0] > previous and cell.commit_start == 0 and cell.commit_end == FRAMES,
                    'cell_original_candidate_geometry')
            previous = bounds[0]
            proof_name, pcm_name = f'cell-{index}-proof.json', f'cell-{index}.pcm16le'
            for name, raw in ((proof_name,cell.proof),(pcm_name,cell.pcm)):
                with (self.out/name).open('xb') as file:file.write(raw)
            candidates.append({'cell_id': cell.cell_id, 'selected': True,
                'support': support, 'proof_sha256': sha(cell.proof), 'pcm_sha256': sha(cell.pcm),
                'proof_artifact': proof_name, 'pcm_artifact': pcm_name,
                'source': [dataclasses.asdict(part) for part in cell.parts],
                'commit_sha256': cell.commit_sha256})
        self._witness['candidates'] = candidates
        self._witness['capture_reason'] = obj.last_reason
        self.cell_witness.update(prepared=True, cells=candidates, capture_reason=obj.last_reason)
        self.save_witness()

    def committed_cells(self, obj, cells, value):
        from audio_backend.native_owned_cell_store import Operation
        require(cells is self._prepared_cells and self.cell_witness['commit_returned_true'],
                'original_cell_commit_order_or_identity')
        self.cell_witness['committed_called'] = True
        if isinstance(value, Operation):
            require(value.kind == 'publish' and value.payload is cells, 'cell_publication_original_identity')
            self._publication = value
        else:
            require(type(value) is dict and len(canonical(value)) <= 8192, 'cell_commit_return_cap')
            self.cell_witness['publication'] = {'kind': 'dict', 'status': value.get('status'),
                'result': json.loads(canonical(value)), 'done': None, 'drained': None, 'slot_released': None}
        self._witness['commit_returned_true'] = True
        self._witness['committed_return'] = self.cell_state()['cell_witness']['publication']
        self.save_witness()

    def acquired_cell(self, lease):
        require(self.capture_allowed and self._copied_lease is None, 'one_original_cell_dispatch')
        pin, token = lease.pin, lease.token
        require(token.origin == 'durable_cell' and pin.owner is token.owner and
                pin.cell_id == token.cell_id, 'cell_dispatch_pin_identity')
        require(pin.owner._lock.acquire(timeout=.05), 'observer_pin_lock')
        try:require(pin.owner._pins.get(id(pin)) is pin, 'copied_pin_not_registered')
        finally:pin.owner._lock.release()
        self._copied_lease = lease
        self.cell_pin_cleanup.update(acquired=1, cell_id=token.cell_id,
            proof_sha256=token.proof_sha256, copied_pcm_sha256=sha(pin._pcm))

    def released_cell(self, owner, pin, registered, value):
        if value is not True:return
        require(owner._lock.acquire(timeout=.05), 'observer_pin_release_lock')
        try:absent = id(pin) not in owner._pins
        finally:owner._lock.release()
        require(registered and absent and pin.release_done.is_set() and pin._released and
                pin._pcm == b'', 'exact_copied_pin_registry_removal_unconfirmed')
        self.cell_pin_cleanup.update(registry_removal_acknowledged=1, release_confirmed=1,
            exact_registry_absent=True, original_release_returned_true=True)
        self._pin_receipt_done.set()
        self.journal.emit('exact_cell_pin_registry_removal_acknowledged',
                          cell_id=pin.cell_id, copied_pcm_dropped=True)

    def permit_release_observation(self, token):
        """Qualification only. The caller always delegates original release."""
        lease = token.get('lease')
        self._pin_receipt_done.wait(.05)
        require(lease is self._copied_lease and token.get('process_cleanup_verified') is True and
                lease.released and self.cell_pin_cleanup['registry_removal_acknowledged'] == 1 and
                self.cell_pin_cleanup['exact_registry_absent'],
                'cell_process_and_exact_pin_cleanup_required_before_permit_reuse')
        self.cell_pin_cleanup['permit_release_checked'] += 1

    def original_wait_observation(self, original, value):
        identity = self.ledger.original_keys.get(id(original))
        require(identity is not None and identity in self.ledger.owned and
                type(value) is int and original.returncode == value,
                'original_handle_wait_observation')
        self._original_waits[id(original)] = {'pid': original.pid,
            'start_ticks': identity[1], 'namespace': self.ledger.namespace,
            'original_waited': True, 'returncode': value}

    def primary_cleanup_state(self, app):
        """No wait, reap, close or signal. Track returned original waits and idle native."""
        if self.ledger is None:return {'confirmed': False, 'reason': 'ledger_unavailable'}
        live = self.ledger.census()
        primary = []
        prefix = [self.spec['python'], '-I', '-B', '-S',
                  str(Path(__file__).resolve().with_name('bootstrap.py')), '--mode']
        permit = None if app.state.moss_jobs is None else app.state.moss_jobs.permit
        residents = {}
        if permit is not None:
            require(permit._condition.acquire(timeout=.05), 'observer_primary_handles_lock')
            try:
                require(len(permit._handles) <= 16, 'observer_primary_handles_cap')
                for key, row in permit._handles.items():
                    residents[key] = {'original': row['proc'], 'pid': row['pid'],
                        'created': row['created'], 'idle': row['idle'],
                        'resident_callback_present': row['resident'] is not None,
                        'known_children': dict(row['children'])}
            finally:permit._condition.release()
        allowed_live = set()
        for original in self.ledger.originals:
            argv = original.args
            if not isinstance(argv, (list,tuple)) or list(argv[:6]) != prefix:continue
            role = argv[6]
            if role not in ('native','analyzer','guard'):continue
            identity = self.ledger.original_keys.get(id(original))
            record = {'mode': role, 'pid': original.pid, 'start_ticks': None if identity is None else identity[1],
                'original_waited': False, 'returncode': original.returncode,
                'terminal_cleanup_confirmed': False, 'verified_signal_gap': False,
                'resident_idle_confirmed': False}
            waited = self._original_waits.get(id(original))
            if waited is not None:
                record.update(waited)
                path = self.out/f'{role}-{original.pid}-terminal.json'
                cleanup_path = self.out/f'{role}-{original.pid}-cleanup.json'
                if path.exists() and cleanup_path.exists():
                    raw = bounded_read(path, 128*1024, self.deadlines.cleanup_check)
                    terminal = json.loads(raw)
                    cleanup = json.loads(bounded_read(cleanup_path, 131072, self.deadlines.cleanup_check))
                    record['terminal_cleanup_confirmed'] = (terminal.get('status') == 'COMPLETE' and
                        terminal.get('pid') == original.pid and terminal.get('mode') == role and
                        terminal.get('spec_sha256') == self.spec['_sha256'] and
                        terminal.get('cleanup') == 'CONFIRMED' and not terminal.get('failures') and
                        cleanup.get('cleanup') == 'CONFIRMED' and not cleanup.get('remaining_owned') and
                        all(item.get('original_waited') is True for item in cleanup.get('originals', [])))
                    record.update(terminal_artifact=path.name, terminal_sha256=sha(raw))
                if role in ('native','analyzer') and original.returncode in (-9,-15):
                    observer = getattr(self.spec.get('_observe_live_mappings'), '__self__', None)
                    observation = {} if observer is None else observer.observations.get(role,{})
                    # Same inherited missing-terminal signal-gap policy: wait is
                    # independently observed; live native contents are not inferred.
                    record['verified_signal_gap'] = (observation.get('status') == 'VERIFIED' and
                        observation.get('pid') == original.pid and not any(
                            (item['pid'],item['start_ticks']) == identity for item in live))
                    record['live_mapping_observation'] = dict(observation)
            resident = residents.get(id(original))
            if role == 'native' and resident is not None:
                actual = [item for item in live if (item['pid'],item['start_ticks']) == identity]
                record['resident_idle_confirmed'] = (resident['original'] is original and
                    resident['pid'] == original.pid and resident['idle'] is True and
                    resident['resident_callback_present'] and len(actual) == 1 and
                    actual[0]['state'] != 'Z' and original.returncode is None)
                record['permit_identity'] = {k:v for k,v in resident.items() if k != 'original'}
                if record['resident_idle_confirmed']:allowed_live.add(identity)
            primary.append(record)
        remaining = [item for item in live if (item['pid'],item['start_ticks']) not in allowed_live]
        roles = [row['mode'] for row in primary]
        confirmed = (self.capture_allowed and roles.count('native') == roles.count('analyzer') == 1 and
            roles.count('guard') == 3 and not remaining and not self.ledger.errors and
            all(row['resident_idle_confirmed'] or row['original_waited'] and
                (row['terminal_cleanup_confirmed'] or row['verified_signal_gap']) for row in primary))
        result = {'confirmed': confirmed, 'originals': primary, 'remaining_known_owned': remaining,
            'scope': 'original wait/reap for ended primary children; exact permitted native resident idle'}
        require(len(canonical(result)) <= 16384, 'primary_cleanup_observer_cap')
        return result

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
            self.labels = admit_native_turns(self.admission, result['turns'], frames=FRAMES)
        elif action == 'finish':
            require(result.get('finished') is True and all(round(result[k]*16000) == FRAMES
                    for k in ('input_seconds', 'processed_seconds', 'stable_seconds')),
                    'native_incomplete_eof')

    def install(self):
        from audio_backend.native_worker import NativeProcess
        from audio_backend.resources import AnalyzerProcess
        from audio_backend.storage import Store
        from audio_backend.native_owned_cell_capture import Capture, PrimaryAuthority
        from audio_backend.native_owned_cell_store import CellStore
        from audio_backend.native_owned_cell_jobs import CellJobsSource
        from audio_backend.native_execution_permit import NativeExecutionPermit
        from audio_backend.worker import Worker
        if self.ledger is not None:
            original_wait = self.ledger.real_popen.wait
            origin = str(Path(inspect.getsourcefile(original_wait)).resolve())
            pins = {**self.spec['runtime_files'], **self.spec['installed_files']}
            require(origin in pins, 'original_wait_symbol_unpinned')
            self.journal.emit('bound_server_original_symbol', module=original_wait.__module__,
                qualname=original_wait.__qualname__, origin=origin, sha256=pins[origin]['sha256'])
            @functools.wraps(original_wait)
            def wait(original, *args, **kwargs):
                value = original_wait(original, *args, **kwargs)
                if any(item is original for item in self.ledger.originals):
                    self.observe('original_process_wait_return',
                        lambda: self.original_wait_observation(original, value))
                return value
            self.ledger.real_popen.wait = wait
        for owner, symbol in ((NativeProcess,'request'), (AnalyzerProcess,'analyze'),
                              (Store,'commit_window'), (Store,'claim'), (Worker,'start'),
                              (Capture,'prepare'), (Capture,'committed'), (PrimaryAuthority,'mapping'),
                              (CellJobsSource,'acquire_dispatch'), (CellJobsSource,'accepted'),
                              (CellJobsSource,'release'), (CellStore,'_release_pin'),
                              (NativeExecutionPermit,'release_optional')):
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
            with self._observation_lock:
                self.cell_witness['commit_returned_true'] = value is True
            self.journal.emit('production_commit_return', committed=value is True)
            return value
        Store.commit_window = commit
        # Original rolling NativeWitness.finalize is deliberately untouched: its
        # <=20-second contract cannot qualify the genuine 21-second core.
        original_mapping = PrimaryAuthority.mapping
        @functools.wraps(original_mapping)
        def mapping(obj, *args, **kwargs):
            value = original_mapping(obj, *args, **kwargs)
            if self._preparing == (obj, threading.get_ident()):
                self.observe('original_mapping_return', lambda: self.mapped_cells(obj, value))
            return value
        PrimaryAuthority.mapping = mapping
        original_prepare = Capture.prepare
        @functools.wraps(original_prepare)
        def prepare(obj, chunk, native, offset, end, result):
            self.observe('before_cell_prepare', lambda: self.begin_cells(obj, chunk, native, offset, end, result))
            try:
                value = self.jobs.invoke('cell_prepare', original_prepare, obj, chunk, native, offset, end, result)
                self.observe('original_cell_prepare_return', lambda: self.prepared_cells(obj, value))
                return value
            finally:self._preparing = None
        Capture.prepare = prepare
        original_committed = Capture.committed
        @functools.wraps(original_committed)
        def committed(obj, cells):
            value = self.jobs.invoke('cell_committed', original_committed, obj, cells)
            self.observe('original_cell_committed_return', lambda: self.committed_cells(obj, cells, value))
            return value
        Capture.committed = committed
        original_acquire = CellJobsSource.acquire_dispatch
        @functools.wraps(original_acquire)
        def acquire_dispatch(obj, *args, **kwargs):
            self.cell_pin_cleanup['acquire_entered'] += 1
            value = original_acquire(obj, *args, **kwargs)
            self.observe('original_cell_dispatch_acquired', lambda: self.acquired_cell(value))
            return value
        CellJobsSource.acquire_dispatch = acquire_dispatch
        original_accepted = CellJobsSource.accepted
        @functools.wraps(original_accepted)
        def accepted(obj, lease):
            self.cell_pin_cleanup['accepted_entered'] += 1
            value = original_accepted(obj, lease)
            def observe_accepted():
                require(lease is self._copied_lease and self.cell_pin_cleanup['accepted'] == 0,
                        'original_cell_dispatch_acceptance')
                self.cell_pin_cleanup['accepted'] += 1
            self.observe('original_cell_dispatch_accepted', observe_accepted)
            return value
        CellJobsSource.accepted = accepted
        original_release = CellJobsSource.release
        @functools.wraps(original_release)
        def release(obj, lease):
            self.cell_pin_cleanup['release_entered'] += 1
            if lease is self._copied_lease:
                self.cell_pin_cleanup['release_requested'] += 1
            return original_release(obj, lease)
        CellJobsSource.release = release
        original_release_pin = CellStore._release_pin
        @functools.wraps(original_release_pin)
        def release_pin(obj, pin):
            tracked = self._copied_lease is not None and self._copied_lease.pin is pin
            registered = False
            if tracked:
                # Do not retain either owner lock across the original call.
                if obj._lock.acquire(timeout=.05):
                    try:registered = obj._pins.get(id(pin)) is pin
                    finally:obj._lock.release()
            value = original_release_pin(obj, pin)
            if tracked:self.observe('original_cell_pin_release',
                lambda: self.released_cell(obj, pin, registered, value))
            return value
        CellStore._release_pin = release_pin
        original_permit_release = NativeExecutionPermit.release_optional
        @functools.wraps(original_permit_release)
        def release_optional(obj, token, *, cleaned):
            if cleaned and type(token) is dict and token.get('origin') == 'durable_cell':
                self.observe('optional_permit_release', lambda: self.permit_release_observation(token))
            return original_permit_release(obj, token, cleaned=cleaned)
        NativeExecutionPermit.release_optional = release_optional
        original_claim = Store.claim
        @functools.wraps(original_claim)
        def claim(obj):
            value = original_claim(obj)
            if value is not None:
                observe_first_claim(self,value)
            return value
        Store.claim = claim
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


def observe_first_claim(hooks, value):
    """Preserve the exact claimed row; never manufacture a queued source."""
    require(hooks.capture_allowed and hooks.claimed == 0, 'unexpected_nonempty_claim')
    now = hooks.deadlines.clock()
    require(now <= hooks.deadlines.origin+180., 'primary_claim_admission_late')
    require(value['status'] == 'queued' and value['duration'] == FRAMES/16000 and
            value['timeline_start'] == 0 and value['next_offset'] == 0 and
            value['digest'] == hooks.spec['input_pins']['wav_sha256'], 'claimed_source_geometry')
    require(type(value['id']) is str and type(value['session_id']) is str, 'claimed_source_identity')
    hooks.claimed += 1
    record = {'phase':'primary','created_at':now,'deadline':now+600.,'spec_sha256':hooks.spec['_sha256']}
    hooks.deadlines.bind_primary(record,hooks.spec['_sha256'])
    hooks.claim_source = json.loads(canonical(value))
    atomic_json(hooks.out/'private-claimed-source.json',hooks.claim_source,MIB)
    atomic_json(hooks.out/'primary-window.json',record,4096)
    hooks.journal.emit('first_original_claim_observed',chunk_id=value['id'],window=record)


def read_only_state(app, spec):
    """Separate bounded read locks; no expire/drain/revalidate/sample/release."""
    worker = app.state.worker
    cache = worker.native_owned_support_cache
    result = {'active_chunk':worker.current_chunk,'cache':{'present':False},
              'jobs':{'present':False},'permit':None}
    if cache is not None:
        require(cache._lock.acquire(timeout=.05),'observer_cache_lock')
        try:
            dispatch = cache._dispatch
            result['cache'] = {'present':True,'epoch':cache.epoch,'entries':len(cache._entries),
                'retained_bytes':cache._retained,'marker_bytes':cache._marker_bytes,
                'pending':cache._pending is not None,'cleanup_pending':cache._cleanup_pending.is_set(),
                'blocked':cache._blocked.is_set(),'revision':cache._revision,
                'dispatch_present':dispatch is not None,
                'dispatch_cleanup_pending':cache._dispatch_cleanup_pending.is_set(),
                'dispatch_released':None if dispatch is None else dispatch.released,
                'source_binding':None if dispatch is None else dispatch.token.target.binding,
                'response_cleanup_pending':cache._lease_cleanup_pending.is_set()}
        finally:cache._lock.release()
        require(cache._lease_lock.acquire(timeout=.05),'observer_lease_lock')
        try:
            result['cache'].update(lease_count=cache._lease_count,lease_bytes=cache._lease_bytes)
        finally:cache._lease_lock.release()
    owner = app.state.moss_jobs
    controller = None
    if owner is not None:
        require(owner._lock.acquire(timeout=.05),'observer_jobs_lock')
        try:
            record = owner._live
            result['jobs'] = {'present':True,'jobs_epoch':owner.jobs_epoch,
                'accepted_jobs':len(owner._records),'key_count':len(owner._keys),'live':record is not None,
                'stopped':owner.stopped,'closed':owner._closed}
            if record is not None:
                result['jobs'].update({key:record[key] for key in
                    ('job_id','support_id','capture_epoch','revision','state','cleaned','purge_pending','invocation_possible')})
                result['jobs']['publication_cancelled'] = record['cancelled'].is_set()
                controller = record['controller']
        finally:owner._lock.release()
        permit = owner.permit
        require(permit._condition.acquire(timeout=.05),'observer_permit_lock')
        try:
            result['permit'] = {'state':permit._state,'optional_present':permit._optional is not None,
                'primary_count':len(permit._primary),'claim_probe':permit._probe,
                'unavailable':permit._unavailable,'closed':permit._closed,
                'resident_handles':len(permit._handles)}
        finally:permit._condition.release()
    if controller is not None:
        require(controller._lock.acquire(timeout=.05),'observer_controller_lock')
        try:
            job = controller._job
            if job is not None and result['jobs'].get('cleaned'):
                value = {key:job.get(key) for key in ('job_id','state','native_receipt','cleanup')}
                raw = canonical(value)
                require(len(raw) <= 32768,'moss_observer_receipt_cap')
                spec['_moss_terminal'] = json.loads(raw)
        finally:controller._lock.release()
    cells = worker.native_owned_cells
    result['cells'] = {'present': cells is not None}
    if cells is not None:
        result['cells'].update(cells.health())
    result['moss_terminal'] = spec.get('_moss_terminal')
    original = spec.get('_moss_original')
    result['moss_original_pid'] = None if original is None else original.pid
    result['moss_mappings'] = spec.get('_moss_map_final',{'status':'UNOBSERVED','reason':'no_completed_observation'})
    require(len(canonical(result)) <= 65536,'observer_result_cap')
    return result


def run_server(spec, deadlines, journal, ledger, arguments, launches):
    require(len(arguments) == 2 and arguments[0] in ('capture', 'restart-on', 'restart-off'), 'server_arguments')
    mode, command_path = arguments
    command_dir = Path(command_path)
    require(command_dir.resolve() == command_dir and command_dir.parent == Path(spec['output_dir']), 'command_scope')
    hooks = ServerHooks(spec, deadlines, journal, mode == 'capture', ledger)
    hooks.install()
    from audio_backend.app import create_app
    from audio_backend.settings import Settings
    import uvicorn
    values = dict(spec['settings'],start_worker=True)
    if mode == 'restart-off':
        values.update(native_owned_support_enabled=False,native_owned_cells_enabled=False,
                      moss_jobs_enabled=False,moss_jobs_model_path=None)
    settings = Settings(**values)
    app = create_app(settings, analyzer_factory=None)
    require(app.state.worker.analyzer_factory is None, 'real_analyzer_factory_required')
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    sock.listen(16)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=sock.getsockname()[1],
                           loop='asyncio', http='h11', ws='none', lifespan='on', access_log=False,
                           log_level='warning', timeout_keep_alive=1, timeout_graceful_shutdown=15))
    errors = []
    ready_deadline = time.monotonic()+30.
    def control():
        sequence = 0
        started = False
        try:
            while not server.should_exit:
                deadlines.service_check()
                if server.started and not started:
                    cells = app.state.worker.native_owned_cells
                    if cells is not None and cells.health()['status'] == 'initializing':
                        require(time.monotonic() < ready_deadline, 'cell_owner_startup_deadline')
                        ledger.census(); time.sleep(.05); continue
                    atomic_json(command_dir/'ready.json', {'port': sock.getsockname()[1], 'pid': os.getpid(),
                                'mode': mode, 'counts': hooks.counts(), 'primary_cleanup': hooks.primary_cleanup_state(app), **hooks.cell_state(), **read_only_state(app,spec)}, 65536)
                    started = True
                path = command_dir/f'{sequence:03}.command.json'
                if not path.exists():
                    ledger.census()
                    time.sleep(.05)
                    continue
                require(started and sequence < (256 if mode == 'capture' else 32), 'private_control_sequence_cap')
                cmd = json.loads(bounded_read(path, 4096, deadlines.service_check))
                action = cmd.get('action')
                result = {'action': action}
                if action == 'phase':
                    require(mode == 'capture' and set(cmd) == {'action','record'}, 'phase_control_shape')
                    record = cmd['record']
                    require(record['phase'] in ('support','moss-compute','moss-cleanup','lifecycle'),
                            'phase_control_scope')
                    deadlines.enter(record['phase'],record=record,spec_sha256=spec['_sha256'])
                elif action == 'state':
                    from bootstrap import observe_moss_once
                    if cmd.get('observe_moss') is True:
                        require(mode == 'capture','restart_observation_forbidden')
                        observe_moss_once(spec,deadlines,ledger)
                elif action == 'snapshot':
                    require(app.state.worker.current_chunk is None, 'snapshot_while_worker_busy')
                    result['snapshot'] = snapshot(app.state.store, Path(spec['output_dir'])/'primary-first.snapshot', deadlines.check)
                elif action == 'cache':
                    pass
                elif action == 'stop':
                    server.should_exit = True
                else:
                    raise RuntimeError('Unknown private control command')
                result.update(counts=hooks.counts(), launches=dict(launches), primary_cleanup=hooks.primary_cleanup_state(app), **hooks.cell_state(), **read_only_state(app,spec))
                atomic_json(command_dir/f'{sequence:03}.response.json', result, 65536)
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
                    'primary_cleanup': hooks.primary_cleanup_state(app), **hooks.cell_state(), **read_only_state(app,spec)}, 65536)
    require(not errors and not thread.is_alive() and not app.state.worker.thread.is_alive(), 'server_shutdown_incomplete')
