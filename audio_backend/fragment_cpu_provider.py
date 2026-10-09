"""Explicit trusted-local CPU providers. No models are imported in the service.

This is a bounded two-child topology, not an adversarial-code sandbox. Selected
frozen sherpa/ctypes runtimes may create threads, never daemonized subprocesses.
All compute admission is durable; process loss never refunds a reservation.
"""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import importlib
import copy
import json
import math
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import uuid

import psutil

from .fragment_runtime_contracts import RATE, CORE, EPOCH, PREROLL, RuntimePolicy, StaleWork, canonical, digest

SCHEMA = 'fragment-cpu-provider-v1'
MAX_WIRE = 2 * 1024**2
MAX_RECORD = 256 * 1024
PREFIXES = {'audio_backend', 'numpy', 'sherpa_onnx', 'soundfile', '_soundfile_data',
            'opencc', 'psutil', 'cffi', '_cffi_backend'}
KINDS = ('analyzer', 'analyzer_init', 'native_reset', 'native_push', 'native_finish',
         'native_turns', 'sensevoice', 'embedding', 'zipformer', 'sensevoice_load',
         'embedding_load', 'zipformer_load')
RECOVERY_TOPOLOGY = 'trusted-no-daemonizing-two-child-v1'
RECOVERY_MAX_PROCESSES = 4096
RECOVERY_SECONDS = 3.0
OPENCC_T2S = {'name':'Traditional Chinese to Simplified Chinese',
    'segmentation':{'type':'mmseg','dict':{'type':'txt','file':'TSPhrases.txt'}},
    'conversion_chain':[{'dict':{'type':'group','dicts':[
        {'type':'txt','file':'TSPhrases.txt'},{'type':'txt','file':'TSCharacters.txt'}]}}]}


def artifact_identity(st):
    # Reading may update atime; only content/ownership-relevant identity is stable.
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate protocol/config key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _bounded(value, limit=MAX_WIRE):
    data = canonical(value).encode()
    if len(data) > limit:
        raise ValueError('CPU provider envelope exceeds bound')
    return data


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f'{name} must be a positive integer')
    return value


def namespace():
    """Recovery observations are meaningful only in this boot/PID namespace."""
    if sys.platform != 'linux':
        raise ValueError('Strict CPU provider currently requires Linux process groups')
    return {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'pid_namespace': os.readlink('/proc/self/ns/pid')}


def mapped_libraries():
    with open('/proc/self/maps') as stream:
        text = stream.read(4 * 1024**2 + 1)
    if len(text) > 4 * 1024**2:
        raise ValueError('Unbounded loaded-library map')
    paths = set()
    for line in text.splitlines():
        fields = line.split(None, 5)
        if len(fields) == 6 and fields[5].startswith('/'):
            raw = fields[5]
            if raw.endswith(' (deleted)'):
                raise ValueError('Deleted loaded-provider mapping cannot be attested')
            path = Path(raw).resolve()
            if path.stat().st_ino != int(fields[4]):
                raise ValueError('Loaded mapping inode differs from current artifact')
            paths.add(str(path))
    return paths


class Manifest:
    def __init__(self, value):
        value = _json(_bounded(value))
        if set(value) != {'schema', 'config', 'files', 'limits', 'native_provenance'} or value['schema'] != SCHEMA:
            raise ValueError('Unsupported explicit CPU provider manifest')
        self._value, self._hash = value, digest(value)
        self._config, self._files, self._limits = value['config'], value['files'], value['limits']
        expected = {'asr_backend': 'sensevoice', 'diarization_backend': 'nemotron', 'device': 'cpu',
                    'cpu_threads': 2, 'native_diarization_threads': 2, 'language': 'auto',
                    'overlap_backend': 'none', 'speaker_support_enabled': False, 'offline': True,
                    'max_chunk_seconds': 34.0, 'max_segment_seconds': 5.0,
                    'native_diarization_epoch_max_seconds': 360.0, 'non_speech_gate': True,
                    'event_threshold': .25, 'event_release_ratio': 1.0, 'event_silence_peak': 1e-5,
                    'speaker_min_query_seconds': 2.0, 'chinese_script': 'simplified'}
        paths = {'model_dir', 'native_diarization_model', 'native_diarization_library'}
        if set(self._config) != set(expected) | paths or any(self._config.get(k) != v or type(self._config[k]) is not type(v) for k, v in expected.items()):
            raise ValueError('CPU manifest differs from the admitted trusted provider configuration')
        for key in paths:
            p = Path(self._config[key])
            if not p.is_absolute() or str(p.resolve()) != str(p):
                raise ValueError('Explicit canonical local provider paths required')
        if not isinstance(self._files, dict) or not 1 <= len(self._files) <= 4096:
            raise ValueError('Bounded complete local file inventory required')
        for path, entry in self._files.items():
            if (not Path(path).is_absolute() or str(Path(path).resolve()) != path or
                set(entry) != {'sha256', 'bytes'} or type(entry['bytes']) is not int or entry['bytes'] <= 0 or
                not isinstance(entry['sha256'], str) or len(entry['sha256']) != 64 or
                any(c not in '0123456789abcdef' for c in entry['sha256'])):
                raise ValueError('Invalid pinned local file')
        model = Path(self._config['model_dir'])
        required = [str(Path(sys.executable).resolve()), self._config['native_diarization_model'],
                    self._config['native_diarization_library']]
        required += [str(Path(__file__).resolve().parent/name) for name in (
            'fragment_cpu_provider.py','fragment_cpu_meter.py','fragment_model_bridges.py',
            'fragment_http.py','fragment_runtime.py','fragment_runtime_store.py',
            'fragment_runtime_contracts.py','fragment_core.py','app.py','worker.py')]
        required += [str(model / name) for name in ('sensevoice/model.int8.onnx', 'sensevoice/tokens.txt',
            'speaker.onnx', 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx',
            'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv')]
        if any(path not in self._files for path in required):
            raise ValueError('Manifest omits an actual model/runtime/interpreter asset')
        keys = {'max_source_frames', 'max_run_attempts', 'max_core_attempts', 'request_seconds',
                'handshake_seconds', 'run_seconds', 'tree_rss_bytes', 'host_reserve_bytes',
                'disk_reserve_bytes', 'scratch_bytes', 'retained_bytes'}
        if set(self._limits) != keys:
            raise ValueError('Every finite resource and attempt ceiling must be explicit')
        for key in keys:
            _positive(self._limits[key], key)
        if self._limits['max_core_attempts'] > self._limits['max_run_attempts']:
            raise ValueError('Core attempts cannot exceed run attempts')
        if not self._limits['handshake_seconds'] <= self._limits['request_seconds'] <= self._limits['run_seconds']:
            raise ValueError('Invalid provider deadline ordering')
        provenance = value['native_provenance']
        for field, path in (('diarization_model_sha256', self._config['native_diarization_model']),
                            ('diarization_library_sha256', self._config['native_diarization_library'])):
            if provenance.get(field) != self._files[path]['sha256']:
                raise ValueError('Native expected provenance differs from manifest assets')
        if provenance.get('diarization_wrapper_cpu_limit') != 2 or provenance.get('diarization_cpu_affinity_enforced') is not True:
            raise ValueError('Explicit CPU-capped native provenance required')
        library = Path(self._config['native_diarization_library'])
        companions = {p.name: str(p) for p in library.parent.iterdir() if p.is_file() and not p.is_symlink()
                      and any(part in p.name.lower() for part in ('nemo_speech_asr','ggml','llama'))}
        expected_companions = provenance.get('diarization_runtime_files_sha256')
        if not isinstance(expected_companions, dict) or set(companions) != set(expected_companions):
            raise ValueError('Native companion inventory differs before model load')
        for name, path in companions.items():
            if path not in self._files or self._files[path]['sha256'] != expected_companions[name]:
                raise ValueError('Native companion bytes lack explicit pre-load pin')
        self.identities = {}

    @property
    def value(self): return copy.deepcopy(self._value)
    @property
    def hash(self): return self._hash
    @property
    def config(self): return copy.deepcopy(self._config)
    @property
    def files(self): return copy.deepcopy(self._files)
    @property
    def limits(self): return dict(self._limits)

    @classmethod
    def load(cls, path, expected_hash):
        path = Path(path)
        if path.stat().st_size > MAX_WIRE:
            raise ValueError('Oversized CPU manifest')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError('Explicit CPU manifest file hash differs')
        return cls(_json(data))

    def verify(self, full=False):
        for name, entry in self._files.items():
            path = Path(name)
            st = path.stat()
            identity = artifact_identity(st)
            if path.resolve() != path or st.st_size != entry['bytes']:
                raise ValueError('Pinned provider file substituted')
            if full or name not in self.identities:
                if sha256(path) != entry['sha256'] or artifact_identity(path.stat()) != identity:
                    raise ValueError('Pinned provider file changed')
                self.identities[name] = identity
            elif self.identities[name] != identity:
                raise ValueError('Provider file changed after admission')
        return self.receipt()

    def verify_receipt(self, receipt):
        if not isinstance(receipt, dict) or set(receipt) != {'interpreter', 'modules'} or not receipt['modules']:
            raise ValueError('Missing actual imported-provider attestation')
        for entry in [receipt['interpreter'], *receipt['modules'].values()]:
            path = entry.get('path')
            if path not in self._files or entry != {'path': path, **self._files[path]}:
                raise ValueError('Actual imported-provider attestation differs')
        if receipt['interpreter']['path'] != str(Path(sys.executable).resolve()):
            raise ValueError('Child interpreter differs from admitted interpreter')

    def receipt(self):
        modules = {}
        for name, module in list(sys.modules.items()):
            path = getattr(module, '__file__', None)
            qualified = getattr(getattr(module,'__spec__',None),'name',name) or name
            if path and qualified.split('.')[0] in PREFIXES:
                path = str(Path(path).resolve())
                if path not in self._files or sha256(path) != self._files[path]['sha256']:
                    raise ValueError('Actual imported provider bytes were not pinned: ' + name)
                modules[qualified] = {'path': path, **self._files[path]}
        interpreter = str(Path(sys.executable).resolve())
        return {'interpreter': {'path': interpreter, **self._files[interpreter]}, 'modules': modules}

    def preload(self, role):
        names = ['numpy', 'soundfile']
        if role == 'analyzer':
            names += ['sherpa_onnx', 'opencc']
        for name in names:
            importlib.import_module(name)
        receipt = self.receipt()
        assets = self.runtime_assets(role) if role == 'analyzer' else None
        return receipt, assets

    def runtime_assets(self, role, mapped=None):
        """Verify selected native objects and OpenCC data before construction.

        System interpreter/OS libraries remain the trusted platform boundary;
        framework package objects and native model companions are explicit.
        """
        mapped = mapped_libraries() if mapped is None else set(mapped)
        selected = {p for p in mapped if 'site-packages/' in p and ('.so' in Path(p).name or p.endswith('.pyd'))}
        snd = {p for p in mapped if 'libsndfile' in Path(p).name}
        if len(snd) != 1:
            raise ValueError('Actual soundfile runtime mapping is ambiguous or missing')
        selected.update(snd)
        if role == 'analyzer':
            module = sys.modules.get('opencc')
            if module is None or 'soundfile' not in sys.modules or 'sherpa_onnx' not in sys.modules:
                raise ValueError('Inference runtime must be preloaded and attested before models')
            root = Path(module.__file__).resolve().parent
            selected.update(str(root / name) for name in ('config/t2s.json','dictionary/TSPhrases.txt','dictionary/TSCharacters.txt'))
        elif role == 'native':
            library = Path(self._config['native_diarization_library'])
            native = {p for p in mapped if p == str(library) or any(part in Path(p).name.lower() for part in ('nemo_speech_asr','ggml','llama'))}
            allowed = {str(library)} | {str(library.parent/name) for name in self._value['native_provenance']['diarization_runtime_files_sha256']}
            if str(library) not in native or not native <= allowed:
                raise ValueError('Actual native companion mapping differs before model construction')
            selected.update(native)
        else:
            raise ValueError('Unknown runtime role')
        result = {}
        for path in selected:
            if path not in self._files or sha256(path) != self._files[path]['sha256']:
                raise ValueError('Unpinned or mutated consumed runtime data/object: ' + path)
            result[path] = self._files[path]
        if role == 'analyzer':
            config = root / 'config/t2s.json'
            if config.stat().st_size > 65536 or _json(config.read_bytes()) != OPENCC_T2S:
                raise ValueError('OpenCC dictionary chain differs from admitted t2s closure')
        return result


def derive_limits(frames, limits):
    if type(frames) is not int or not 0 < frames <= limits['max_source_frames']:
        raise ValueError('Finalized audio exceeds explicitly configured run admission')
    cores = (frames + CORE - 1) // CORE
    epochs = (frames + EPOCH - 1) // EPOCH
    attempts, retries = limits['max_core_attempts'], limits['max_run_attempts'] - 1
    reads = frames + max(0, cores - 1) * 4 * RATE
    calls = cores * attempts
    # Conservative finite inventory derived from recording size and admitted
    # retry/replay count. Actual waveform inventories remain checked precompute.
    event_calls = cores * 13 * attempts
    replay_frames = (epochs + retries) * 360 * RATE
    pushes = (epochs + retries) * ((360 * RATE + PREROLL - 1) // PREROLL + 2)
    value = {'analyzer': (calls, reads * attempts), 'analyzer_init': (retries + 1, 0),
             'native_reset': (epochs + retries, 0), 'native_push': (pushes, replay_frames),
             'native_finish': (retries + 1, 0), 'native_turns': (pushes + epochs + retries + 1, 0),
             'sensevoice': (calls * 32, (reads + cores * 2 * RATE) * attempts),
             'embedding': (calls * 8, reads * attempts), 'zipformer': (event_calls, event_calls * 5 * RATE),
             'sensevoice_load': (retries + 1, 0), 'embedding_load': (calls, 0), 'zipformer_load': (calls, 0)}
    return {kind: {'calls': pair[0], 'frames': pair[1]} for kind, pair in value.items()}


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS fragment_cpu_runs (
          session_id TEXT PRIMARY KEY, manifest TEXT NOT NULL, plan TEXT NOT NULL,
          limits TEXT NOT NULL, used TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
          deadline REAL, retained INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS fragment_cpu_leases (
          lease TEXT PRIMARY KEY, session_id TEXT NOT NULL, generation INTEGER NOT NULL,
          manifest TEXT NOT NULL, namespace TEXT NOT NULL, scratch TEXT NOT NULL,
          state TEXT NOT NULL, receipt TEXT);
        CREATE INDEX IF NOT EXISTS fragment_cpu_lease_session ON fragment_cpu_leases(session_id,state);
        CREATE TABLE IF NOT EXISTS fragment_cpu_processes (
          lease TEXT NOT NULL, token TEXT NOT NULL, identity TEXT NOT NULL,
          state TEXT NOT NULL, receipt TEXT, PRIMARY KEY(lease,token));
        CREATE TABLE IF NOT EXISTS fragment_cpu_tickets (
          ticket_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, lease TEXT NOT NULL,
          kind TEXT NOT NULL, frames INTEGER NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS fragment_cpu_ticket_scope ON fragment_cpu_tickets(lease,ticket_id);
        CREATE INDEX IF NOT EXISTS fragment_cpu_ticket_session ON fragment_cpu_tickets(session_id,ticket_id);
        CREATE INDEX IF NOT EXISTS fragment_cpu_ticket_core ON fragment_cpu_tickets(session_id,kind,json_extract(payload,'$.context'));
    ''')


def erase(db, sid):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='fragment_cpu_runs'").fetchone():
        return
    if db.execute("SELECT 1 FROM fragment_cpu_leases WHERE session_id=? AND state!='released'", (sid,)).fetchone():
        raise ValueError('Provider ownership is unresolved')
    db.execute('DELETE FROM fragment_cpu_processes WHERE lease IN (SELECT lease FROM fragment_cpu_leases WHERE session_id=?)', (sid,))
    for table in ('fragment_cpu_tickets', 'fragment_cpu_leases', 'fragment_cpu_runs'):
        db.execute(f'DELETE FROM {table} WHERE session_id=?', (sid,))


@contextmanager
def transaction(path):
    db = sqlite3.connect(path, timeout=.1)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


class Budget:
    def __init__(self, owner):
        self.owner, self.records = owner, []

    def begin_window(self):
        self.records.clear()

    @property
    def used(self):
        with sqlite3.connect(self.owner.path, timeout=.1) as db:
            return _json(db.execute('SELECT used FROM fragment_cpu_runs WHERE session_id=?', (self.owner.sid,)).fetchone()[0])

    def reserve(self, kind, frames=0, *, calls=1, context=None):
        if kind not in KINDS or type(frames) is not int or frames < 0 or type(calls) is not int or calls <= 0:
            raise ValueError('Invalid model attempt reservation')
        self.owner.check()
        ticket = {'kind': kind, 'calls': calls, 'frames': frames, 'context': context, 'status': 'reserved',
                  'scope': {'session': self.owner.sid, 'plan': self.owner.context.snapshot.plan.sha256,
                            'manifest': self.owner.manifest.hash, 'generation': self.owner.context.generation,
                            'window': getattr(self.owner, 'window', None)}}
        encoded = _bounded(ticket, MAX_RECORD).decode()
        with transaction(self.owner.path) as db:
            self.owner.fence(db)
            row = db.execute('SELECT * FROM fragment_cpu_runs WHERE session_id=?', (self.owner.sid,)).fetchone()
            used, limits = _json(row['used']), _json(row['limits'])
            if used[kind]['calls'] + calls > limits[kind]['calls'] or used[kind]['frames'] + frames > limits[kind]['frames']:
                raise ValueError('Durable model attempt budget exhausted before compute')
            if kind == 'analyzer':
                count = db.execute("SELECT COUNT(*) FROM fragment_cpu_tickets WHERE session_id=? AND kind='analyzer' AND json_extract(payload,'$.context')=?", (self.owner.sid, context)).fetchone()[0]
                if count >= self.owner.manifest.limits['max_core_attempts']:
                    raise ValueError('Core attempt budget exhausted')
            used[kind]['calls'] += calls
            used[kind]['frames'] += frames
            self.owner.retain(db, len(encoded))
            cur = db.execute('INSERT INTO fragment_cpu_tickets(session_id,lease,kind,frames,payload) VALUES(?,?,?,?,?)',
                (self.owner.sid, self.owner.lease, kind, frames, encoded))
            ticket['ticket_id'] = cur.lastrowid
            db.execute('UPDATE fragment_cpu_runs SET used=? WHERE session_id=?', (canonical(used), self.owner.sid))
        if len(self.records) >= 512:
            raise ValueError('Per-window call record bound exceeded; reservation remains consumed')
        self.records.append(ticket)
        return ticket

    def update(self, ticket, **fields):
        self.owner.check()
        with transaction(self.owner.path) as db:
            self.owner.fence(db)
            row = db.execute('SELECT * FROM fragment_cpu_tickets WHERE ticket_id=? AND lease=?', (ticket['ticket_id'], self.owner.lease)).fetchone()
            if row is None:
                raise ValueError('Model ticket does not belong to this owner')
            value = _json(row['payload'])
            if set(fields) & {'kind', 'frames', 'calls', 'context', 'ticket_id', 'scope'}:
                raise ValueError('Reservation identity is immutable')
            if ('status' in fields and (fields['status'] not in {'completed', 'failed'} or
                value['status'] in {'completed', 'failed'} and fields['status'] != value['status'])):
                raise ValueError('Model status cannot move backwards')
            if 'execution_started' in fields and fields['execution_started'] is not True:
                raise ValueError('Execution receipt cannot be undone')
            value.update(fields)
            encoded = _bounded(value, MAX_RECORD).decode()
            self.owner.retain(db, max(0, len(encoded) - len(row['payload'])))
            db.execute('UPDATE fragment_cpu_tickets SET payload=? WHERE ticket_id=?', (encoded, ticket['ticket_id']))
        ticket.update(fields)
        for record in self.records:
            if record['ticket_id'] == ticket['ticket_id']:
                record.update(fields)

    def complete(self, ticket):
        self.update(ticket, status='completed')


def _io(sock, data=None, size=None, *, check):
    output = bytearray()
    view = memoryview(data) if data is not None else None
    while len(view) if view is not None else len(output) < size:
        check()
        ready = select.select([] if view is not None else [sock], [sock] if view is not None else [], [], .05)
        if not any(ready[:2]):
            continue
        try:
            if view is not None:
                count = sock.send(view)
                if not count:
                    raise EOFError('Provider transport closed during send')
                view = view[count:]
            else:
                block = sock.recv(min(65536, size - len(output)))
                if not block:
                    raise EOFError('Provider transport closed during receive')
                output.extend(block)
        except (BlockingIOError, InterruptedError):
            continue
    return bytes(output)


def send(sock, value, check):
    data = _bounded(value)
    _io(sock, struct.pack('!I', len(data)) + data, check=check)


def receive(sock, check):
    size = struct.unpack('!I', _io(sock, size=4, check=check))[0]
    if not 0 < size <= MAX_WIRE:
        raise ValueError('Oversized or empty provider frame')
    return _json(_io(sock, size=size, check=check))


class Owner:
    def __init__(self, context, manifest):
        self.context, self.manifest = context, manifest
        self.path, self.sid = context.evidence_store.store.path, context.snapshot.run_id
        self.lease, self.lock = uuid.uuid4().hex, threading.RLock()
        self.children, self.errors = {}, []
        self.closed, self.failed = threading.Event(), None
        self.ns = namespace()
        service = psutil.Process()
        self.identity = {'pid': service.pid, 'created': service.create_time(),
                         'command': service.cmdline(), 'executable': service.exe()}
        admission = {'kind': 'owner_admission', 'owner': self.identity,
                     'namespace': self.ns, 'topology': RECOVERY_TOPOLOGY}
        _bounded(admission, MAX_RECORD)
        self.root = context.settings.data_dir / 'fragment-work' / self.lease
        self.budget = Budget(self)
        with transaction(self.path) as db:
            self.fence(db)
            row = db.execute('SELECT * FROM fragment_cpu_runs WHERE session_id=?', (self.sid,)).fetchone()
            if row is None or row['manifest'] != manifest.hash or row['plan'] != context.snapshot.plan.sha256:
                raise ValueError('Provider run admission differs from sealed plan/manifest')
            if row['attempts'] >= manifest.limits['max_run_attempts']:
                raise ValueError('Durable run-attempt ceiling exhausted')
            if db.execute("SELECT 1 FROM fragment_cpu_leases WHERE state!='released' LIMIT 1").fetchone():
                raise ValueError('Earlier provider ownership remains unresolved')
            self.deadline = row['deadline'] or time.time() + manifest.limits['run_seconds']
            db.execute('UPDATE fragment_cpu_runs SET attempts=attempts+1,deadline=? WHERE session_id=?', (self.deadline, self.sid))
            db.execute('INSERT INTO fragment_cpu_leases VALUES(?,?,?,?,?,?,?,?)',
                (self.lease, self.sid, context.generation, manifest.hash, canonical(self.ns), str(self.root),
                 'pending', canonical(admission)))
        # Publish the manager before filesystem/process construction can fail.
        context.ownership['manager'] = self
        context.evidence_store.provider_owner = self
        context.ownership.pop('no_process_started', None)
        if self.root.resolve() != self.root:
            raise ValueError('Provider scratch parent is not a canonical owned directory')
        self.root.mkdir(parents=True, exist_ok=False)
        self.watch = threading.Thread(target=self._watch, daemon=True, name='fragment-cpu-watch')
        self.watch.start()

    def fence(self, db):
        row = db.execute('SELECT h.generation,h.state,s.deleted FROM fragment_http_sessions h JOIN sessions s ON s.id=h.session_id WHERE session_id=?', (self.sid,)).fetchone()
        if row is None or row['deleted'] or row['generation'] != self.context.generation or row['state'] != 'running':
            raise StaleWork('Provider control generation changed')

    def retain(self, db, size):
        row = db.execute('SELECT retained FROM fragment_cpu_runs WHERE session_id=?', (self.sid,)).fetchone()
        if row['retained'] + size > self.manifest.limits['retained_bytes']:
            raise ValueError('Configured retained-evidence allowance exhausted')
        db.execute('UPDATE fragment_cpu_runs SET retained=retained+? WHERE session_id=?', (size, self.sid))

    def check(self, additional_disk=0):
        if self.failed:
            raise RuntimeError(self.failed)
        if self.context.stop():
            raise StaleWork('Provider stopped by control or shutdown')
        limits = self.manifest.limits
        if time.time() >= self.deadline:
            raise TimeoutError('Configured run wall deadline reached')
        from .resources import tree_rss
        if tree_rss() >= min(limits['tree_rss_bytes'], self.context.settings.process_memory_limit_bytes):
            raise RuntimeError('Provider aggregate service-tree RSS ceiling reached')
        if psutil.virtual_memory().available < limits['host_reserve_bytes']:
            raise RuntimeError('Provider host-memory reserve reached')
        if shutil.disk_usage(self.context.settings.data_dir).free < max(limits['disk_reserve_bytes'], self.context.settings.disk_reserve_bytes) + additional_disk:
            raise RuntimeError('Provider projected disk reserve reached')
        total, count = 0, 0
        if self.root.exists():
            for path in self.root.rglob('*'):
                count += 1
                if path.is_symlink() or count > 1024:
                    raise RuntimeError('Unsafe or unbounded provider scratch')
                if path.is_file():
                    total += path.stat().st_size
        if total + additional_disk > limits['scratch_bytes']:
            raise RuntimeError('Provider projected scratch allowance reached')

    def reserve_component(self, name, size):
        self.check(size)

    def _watch(self):
        while not self.closed.wait(.05):
            try:
                self.check()
                with self.lock:
                    for child in list(self.children.values()):
                        if child.proc.poll() is None:
                            descendants = psutil.Process(child.proc.pid).children(recursive=True)
                            if descendants:
                                child.unknown = [{'pid': p.pid, 'created': p.create_time()} for p in descendants[:16]]
                                raise RuntimeError('Unexpected descendant violates admitted two-child topology')
            except BaseException as exc:
                self.failed = f'{type(exc).__name__}: {exc}'[:1000]
                with self.lock:
                    for child in list(self.children.values()):
                        try:
                            child.signal_stop()
                        except BaseException as cleanup:
                            self.errors.append('Watchdog signal: ' + str(cleanup)[:500])
                return

    def close(self):
        self.closed.set()
        errors = []
        with self.lock:
            for child in list(self.children.values()):
                try:
                    child.close()
                except BaseException as exc:
                    errors.append(f'{type(exc).__name__}: {exc}'[:500])
        if hasattr(self, 'watch') and self.watch is not threading.current_thread():
            self.watch.join(timeout=2)
        if errors or self.children or self.errors:
            raise RuntimeError('Unresolved provider ownership: ' + '; '.join(errors + self.errors))

    def release(self):
        if self.children or self.errors:
            raise RuntimeError('Cannot release unresolved provider ownership')
        if self.root.resolve() != self.root:
            raise ValueError('Provider scratch path changed before cleanup')
        if self.root.exists():
            shutil.rmtree(self.root)
        with transaction(self.path) as db:
            if db.execute("SELECT 1 FROM fragment_cpu_processes WHERE lease=? AND state!='reaped'", (self.lease,)).fetchone():
                raise RuntimeError('A provider child lacks positive wait acknowledgement')
            db.execute("UPDATE fragment_cpu_leases SET state='released',receipt=? WHERE lease=? AND state='pending'",
                (canonical({'kind': 'all_direct_children_wait_reaped', 'namespace': self.ns, 'scratch_removed': True}), self.lease))


class Process:
    """Retained Popen + separately framed RPC; stdout/stderr retain zero bytes."""
    def __init__(self, owner, role, command=None):
        if any(os.environ.get(name) for name in ('LD_LIBRARY_PATH','LD_PRELOAD','LD_AUDIT')):
            raise ValueError('Ambient loader overrides are unsupported by the pinned CPU launcher')
        self.owner, self.role, self.unknown = owner, role, []
        self.token, self.counter, self.done = uuid.uuid4().hex, 0, False
        self.proc = None
        parent, child = socket.socketpair()
        parent.setblocking(False)
        self.sock = parent
        env = {**os.environ, 'AUDIO_FRAGMENT_FD': str(child.fileno()), 'AUDIO_FRAGMENT_ROLE': role,
               'AUDIO_FRAGMENT_LEASE': owner.lease, 'AUDIO_FRAGMENT_PROCESS': self.token,
               'TMPDIR':str(owner.root),'TMP':str(owner.root),'TEMP':str(owner.root),
               'AUDIO_PARENT_PID': str(owner.identity['pid']),
               'AUDIO_PARENT_CREATED': str(owner.identity['created']), 'OMP_NUM_THREADS': '2',
               'OMP_THREAD_LIMIT': '2', 'OPENBLAS_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2',
               'NUMEXPR_NUM_THREADS': '2', 'PYTHONDONTWRITEBYTECODE': '1'}
        command = command or [sys.executable, '-m', 'audio_backend.native_runner' if role == 'native' else 'audio_backend.runner', '--fragment-serve']
        try:
            if (not isinstance(command, (list, tuple)) or not command or not command[0]
                    or any(not isinstance(part, str) or '\0' in part for part in command)):
                raise ValueError('Exact nonempty provider argument vector required')
            command = list(command)
            admission = {'owner': owner.identity, 'command': command, 'lease': owner.lease,
                         'token': self.token, 'topology': RECOVERY_TOPOLOGY}
            identity = {'pid': None, 'created': None, 'pgid': None, 'role': role,
                        'namespace': owner.ns, 'admission': admission}
            _bounded(identity, MAX_RECORD)
            with owner.lock:
                owner.check()
                with transaction(owner.path) as db:
                    owner.fence(db)
                    db.execute('INSERT INTO fragment_cpu_processes VALUES(?,?,?,?,NULL)',
                               (owner.lease, self.token, canonical(identity), 'spawning'))
                self.proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, pass_fds=(child.fileno(),), env=env, start_new_session=True)
                # Retain before identity registration; constructor failure always
                # signals/waits this exact handle, even if /proc inspection fails.
                owner.children[self.token] = self
                self.identity = dict(identity, pid=self.proc.pid,
                                     created=psutil.Process(self.proc.pid).create_time(), pgid=self.proc.pid)
                with transaction(owner.path) as db:
                    owner.fence(db)
                    db.execute('UPDATE fragment_cpu_processes SET identity=?,state=? WHERE lease=? AND token=?',
                               (canonical(self.identity), 'owned', owner.lease, self.token))
            hello = self.exchange('hello', {'manifest': owner.manifest.value, 'root': str(owner.root)}, handshake=True)
            if hello.get('role') != role or hello.get('namespace') != owner.ns:
                raise ValueError('Child handshake role/namespace differs')
            owner.manifest.verify_receipt(hello.get('attestation'))
            construct = {}
            if role == 'native':
                ticket = next((r for r in reversed(owner.budget.records) if r['kind'] == 'native_reset' and r['status'] == 'reserved'), None)
                if ticket is None:
                    raise ValueError('Native construction lacks durable reset admission')
                construct['ticket'] = ticket
            owner.manifest.verify_receipt(self.exchange('construct', construct).get('attestation'))
        except BaseException:
            try:
                self.close()
            except BaseException as cleanup:
                owner.errors.append('Construction cleanup: ' + str(cleanup)[:500])
            raise
        finally:
            child.close()

    def signal_stop(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                # The retained live leader still owns this newly created group.
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                self.proc.kill()

    def group_members(self):
        if self.proc is None or not hasattr(self, 'identity'):
            return []
        members = []
        deadline = time.monotonic() + RECOVERY_SECONDS
        for count, proc in enumerate(psutil.process_iter(['pid','create_time']), 1):
            if count > RECOVERY_MAX_PROCESSES or time.monotonic() >= deadline:
                raise RuntimeError('Provider group inventory exceeded its finite scan allowance')
            try:
                if os.getpgid(proc.pid) == self.identity['pgid']:
                    item = {'pid':proc.pid,'created':proc.info['create_time']}
                    retained = proc.pid==self.proc.pid and proc.info['create_time']==self.identity['created']
                    if not retained and proc.environ().get('AUDIO_FRAGMENT_PROCESS') != self.token:
                        raise RuntimeError('Unverified process in dedicated provider group')
                    members.append(item)
                    if len(members)>16:raise RuntimeError('Unexpected descendant inventory overflow')
            except (ProcessLookupError,psutil.NoSuchProcess):
                pass
        return members

    def exchange(self, action, body, handshake=False, stop=None):
        self.counter += 1
        binding = {'request_id': f'{self.token}:{self.counter}', 'lease': self.owner.lease,
                   'manifest': self.owner.manifest.hash, 'action': action, 'input_hash': digest(body)}
        deadline = time.monotonic() + self.owner.manifest.limits['handshake_seconds' if handshake else 'request_seconds']
        def check():
            if stop is not None and stop():
                raise StaleWork('Provider caller interrupted request')
            self.owner.check()
            if time.monotonic() >= deadline:
                raise TimeoutError('Provider request deadline reached')
            if self.proc.poll() is not None:
                raise EOFError('Provider exited before bound response')
        try:
            send(self.sock, {'binding': binding, 'body': body}, check)
            rpc_sequence = 0
            while True:
                value = receive(self.sock, check)
                if value.get('binding') != binding:
                    raise ValueError('Stale or mismatched provider response binding')
                if 'rpc' in value:
                    rpc_sequence += 1
                    if value.get('sequence') != rpc_sequence:
                        raise ValueError('Duplicate/out-of-order provider reservation RPC')
                    result = self.rpc(value['rpc'], value['body'])
                    send(self.sock, {'binding': binding, 'sequence': rpc_sequence, 'body': result}, check)
                    continue
                result = value['body']
                if value.get('body_hash') != digest(result):
                    raise ValueError('Provider response content hash differs')
                self.owner.manifest.verify()
                if 'error' in value:
                    raise RuntimeError(value['error'])
                return result
        except BaseException:
            try:
                self.signal_stop()
            except BaseException as cleanup:
                self.owner.errors.append('Request cleanup: ' + str(cleanup)[:500])
            raise

    def rpc(self, operation, body):
        budget = self.owner.budget
        if operation == 'reserve':
            return budget.reserve(**body)
        if operation == 'update':
            budget.update(body['ticket'], **body['fields'])
            return body['ticket']
        if operation == 'check':
            if 'attestation' in body:
                self.owner.manifest.verify_receipt(body['attestation'])
                for path, entry in body.get('runtime_assets', {}).items():
                    if self.owner.manifest.files.get(path) != entry:
                        raise ValueError('Precompute runtime artifact differs from manifest')
                self.owner.context.evidence_store.put({'kind':'provider_precompute_attestation',
                    'lease':self.owner.lease,'role':self.role,'manifest':self.owner.manifest.hash,
                    'attestation':body['attestation'],'runtime_assets':body.get('runtime_assets',{})})
            self.owner.check(body.get('additional_disk', 0))
            return True
        if operation == 'failure':
            return self.owner.context.evidence_store.put(body)
        raise ValueError('Unadmitted provider RPC operation')

    def request(self, action, stop, **kwargs):
        if stop():
            raise StaleWork('Native request interrupted')
        result = self.exchange(action, self._input(kwargs), stop=stop)
        observer = getattr(self, 'response_observer', None)
        if observer is not None:
            observer(result)
        return result

    def _input(self, body):
        body = dict(body)
        if body.get('wav'):
            path = Path(body['wav'])
            if not path.resolve().is_relative_to(self.owner.root) or path.is_symlink():
                raise ValueError('Provider WAV leaves owned scratch')
            body['wav_sha256'] = sha256(path)
        return body

    def analyze(self, wav, *, diarization, analysis_options):
        result = self.exchange('analyze', self._input({'wav': str(wav), 'diarization': diarization,
                                                      'analysis_options': analysis_options}))
        self.owner.manifest.verify_receipt(result.get('attestation'))
        return result['raw']

    def close(self):
        with self.owner.lock:
            return self._close()

    def _close(self):
        if self.done:
            return
        errors, group, waited, code = [], None, self.proc is None, None
        try:
            group = self.group_members()
        except BaseException as exc:
            errors.append(exc)
        if group:
            descendants = [item for item in group if item['pid'] != self.proc.pid]
            if descendants:
                self.unknown = descendants
            try:
                os.killpg(self.identity['pgid'], signal.SIGKILL)
            except BaseException as exc:
                errors.append(exc)
        # Every cleanup action is independent. Group inspection/signaling cannot
        # prevent the original retained handle from being killed and waited.
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    self.proc.kill()
            except BaseException as exc:
                errors.append(exc)
        try:
            self.sock.close()
        except BaseException as exc:
            errors.append(exc)
        if self.proc is not None:
            try:
                code = self.proc.wait(timeout=3)
                waited = True
            except BaseException as exc:
                errors.append(exc)
        if waited:
            try:
                if self.group_members():
                    errors.append(RuntimeError('Provider group still contains live/unknown ownership'))
            except BaseException as exc:
                errors.append(exc)
        if self.unknown:
            errors.append(RuntimeError('Unexpected descendants require explicit reconciliation'))
        receipt = {'wait_returncode':code,'wait_reaped':waited and self.proc is not None,
                   'spawn_failed_before_handle':self.proc is None,'identity':getattr(self,'identity',None),
                   'unexpected_descendants':self.unknown,
                   'cleanup_errors':[f'{type(e).__name__}: {e}'[:500] for e in errors]}
        try:
            with transaction(self.owner.path) as db:
                db.execute('UPDATE fragment_cpu_processes SET state=?,receipt=? WHERE lease=? AND token=?',
                           ('reaped' if waited and not errors else 'cleanup_pending',canonical(receipt),self.owner.lease,self.token))
        except BaseException as exc:
            errors.append(exc)
        if errors:
            raise errors[0]
        self.done = True
        self.owner.children.pop(self.token,None)


class RemoteBudget:
    def __init__(self, rpc):
        self.rpc, self.records = rpc, []

    def reserve(self, kind, frames=0, *, calls=1, context=None):
        ticket = self.rpc('reserve', {'kind': kind, 'frames': frames, 'calls': calls, 'context': context})
        if len(self.records) >= 512:
            raise ValueError('Child per-window receipt allowance reached')
        self.records.append(ticket)
        return ticket

    def update(self, ticket, **fields):
        ticket.update(self.rpc('update', {'ticket': ticket, 'fields': fields}))

    def complete(self, ticket):
        self.update(ticket, status='completed')


def child_main(model_factory=None):
    """Only runner/native_runner call this branch. Test injection is in-process,
    never an environment/config option admitting arbitrary provider code.
    """
    os.nice(max(0, 19 - os.getpriority(os.PRIO_PROCESS, 0)))
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    from .runner import watch_parent
    threading.Thread(target=watch_parent, daemon=True).start()
    sock = socket.socket(fileno=int(os.environ['AUDIO_FRAGMENT_FD']))
    sock.setblocking(False)
    role, lease = os.environ['AUDIO_FRAGMENT_ROLE'], os.environ['AUDIO_FRAGMENT_LEASE']
    provider = manifest = None
    last, current, sequence = 0, None, 0
    deadline = time.monotonic() + 60
    def check():
        if time.monotonic() > deadline:
            raise TimeoutError('Child transport deadline')
    def rpc(operation, body):
        nonlocal sequence
        sequence += 1
        send(sock, {'binding': current, 'rpc': operation, 'sequence': sequence, 'body': body}, check)
        response = receive(sock, check)
        if response.get('binding') != current or response.get('sequence') != sequence:
            raise ValueError('Reservation acknowledgement binding differs')
        return response['body']
    class Guard:
        def check(self, additional_disk=0):
            body = {'additional_disk':additional_disk}
            if model_factory is None:
                body.update(attestation=manifest.receipt(), runtime_assets=manifest.runtime_assets(role))
            return rpc('check', body)
        def reserve_component(self, name, size):
            return self.check(size)
    budget = RemoteBudget(rpc)
    # Selected providers are thread-only. This catches accidental Python helper
    # launches; frozen native libraries remain trusted, not sandboxed.
    def prohibit_spawn(event, args):
        if event in {'subprocess.Popen', 'os.fork', 'os.forkpty', 'os.posix_spawn', 'os.exec', 'os.system'}:
            raise RuntimeError('Unexpected helper spawn in admitted CPU provider')
    sys.addaudithook(prohibit_spawn)
    try:
        while True:
            value = receive(sock, check)
            current, body, sequence = value['binding'], value['body'], 0
            counter = int(current['request_id'].rsplit(':', 1)[1])
            if (counter != last + 1 or current['request_id'].rsplit(':',1)[0] != os.environ['AUDIO_FRAGMENT_PROCESS']
                or current['lease'] != lease or current['input_hash'] != digest(body)):
                raise ValueError('Child request identity/input differs')
            last = counter
            action = current['action']
            try:
                if action == 'hello':
                    if manifest is not None:
                        raise ValueError('Duplicate child handshake')
                    manifest = Manifest(body['manifest'])
                    if current['manifest'] != manifest.hash:
                        raise ValueError('Child manifest binding differs')
                    manifest.verify(full=True)
                    root = Path(body['root']).resolve()
                    if role == 'analyzer' and model_factory is None:
                        from .inference.analyzer import AudioAnalyzer
                        manifest.preload(role)
                    elif role == 'native' and model_factory is None:
                        from .inference.native_diarization import NativeDiarizer
                        manifest.preload(role)
                    result = {'attestation': manifest.receipt(), 'role': role, 'namespace': namespace()}
                else:
                    if manifest is None or current['manifest'] != manifest.hash:
                        raise ValueError('Model request before verified child handshake')
                    deadline = time.monotonic() + manifest.limits['request_seconds']
                    manifest.verify()
                    if action == 'construct':
                        if provider is not None:
                            raise ValueError('Duplicate model construction')
                        if model_factory is not None:
                            ticket = budget.reserve('analyzer_init') if role == 'analyzer' else body['ticket']
                            budget.update(ticket,execution_started=True)
                            provider = model_factory(role, manifest.config, budget, Guard())
                            if role == 'analyzer':budget.complete(ticket)
                        elif role == 'analyzer':
                            from .fragment_cpu_meter import make_cpu_metered_analyzer
                            provider = make_cpu_metered_analyzer(manifest.config, budget, Guard(), raw_failure_sink=lambda raw: rpc('failure', raw))
                        else:
                            # Confirm its durable reservation before loading the
                            # C ABI/model, not merely before the first push.
                            budget.update(body['ticket'], execution_started=True)
                            provider = NativeDiarizer(manifest.config, before_model_create=lambda: manifest.runtime_assets('native'))
                        result = {'constructed': role, 'attestation': manifest.receipt()}
                    else:
                        if provider is None:
                            raise ValueError('Compute before admitted construction')
                        if body.get('wav'):
                            path = Path(body['wav'])
                            if path.is_symlink() or not path.resolve().is_relative_to(root) or sha256(path) != body['wav_sha256']:
                                raise ValueError('Actual child waveform path/hash differs')
                        budget.records.clear()
                        if action == 'analyze' and role == 'analyzer':
                            raw = provider.analyze(body['wav'], diarization=body['diarization'], analysis_options=body['analysis_options'])
                            result = {'raw': raw, 'attestation': manifest.receipt()}
                        elif role == 'native' and action == 'reset':
                            result = {'state': provider.reset(body['epoch_id']), 'provenance': provider.provenance()}
                            result['provenance'].update(diarization_wrapper_cpu_limit=2, diarization_cpu_affinity_enforced=True)
                        elif role == 'native' and action == 'push':
                            result = provider.push(body['wav'])
                        elif role == 'native' and action == 'turns':
                            result = {'turns': provider.turns(body['start'], body['end'], allow_provisional=True)}
                        elif role == 'native' and action == 'finish':
                            result = provider.finish()
                        else:
                            raise ValueError('Unadmitted provider action')
                        if body.get('wav') and sha256(body['wav']) != body['wav_sha256']:
                            raise ValueError('Child waveform changed during compute')
                        manifest.verify()
                        if role == 'native':
                            result['implementation_attestation'] = manifest.receipt()
                send(sock, {'binding': current, 'body': result, 'body_hash': digest(result)}, check)
                if manifest:
                    deadline = time.monotonic() + manifest.limits['run_seconds']
            except BaseException as exc:
                result = {'type': type(exc).__name__, 'message': str(exc)[:2000]}
                send(sock, {'binding': current, 'body': result, 'body_hash': digest(result), 'error': str(result)}, check)
                raise
    finally:
        if provider is not None:
            provider.close()
        sock.close()


class CPUFactory:
    def __init__(self, manifest, *, process_factory=Process):
        self.manifest, self.process_factory = manifest, process_factory
        self.policy = RuntimePolicy(native_fingerprint=digest({'native': manifest.hash}),
                                    analyzer_fingerprint=digest({'analyzer': manifest.hash}))

    def initialize(self, store):
        self.store = store
        with store.db() as db:
            initialize(db)

    def admit(self, db, sid, plan):
        limits = derive_limits(plan.end_frame, self.manifest.limits)
        used = {kind: {'calls': 0, 'frames': 0} for kind in limits}
        db.execute('INSERT INTO fragment_cpu_runs(session_id,manifest,plan,limits,used) VALUES(?,?,?,?,?)',
                   (sid, self.manifest.hash, plan.sha256, canonical(limits), canonical(used)))

    def __call__(self, context):
        from .fragment_model_bridges import AnalyzerBridge, NativeBridge, NativeFactory
        from .native_worker import NativeProcess
        from .resources import AnalyzerProcess
        context.ownership['no_process_started'] = True
        self.manifest.verify(full=True)
        owner = Owner(context, self.manifest)
        class Bridge(AnalyzerBridge):
            def bind_context(self, *args):
                from .fragment_runtime_contracts import window_id
                owner.window = window_id(args[0].run_id, args[1], args[0].policy)
                owner.budget.begin_window()
                return super().bind_context(*args)
        analyzer_process = AnalyzerProcess(context.settings, None, managed_transport=self.process_factory(owner, 'analyzer'))
        class Analyzer:
            def analyze(self, wav, **kwargs):
                return analyzer_process.analyze_raw(wav, **kwargs)
            def close(self):
                analyzer_process.close()
        analyzer = Bridge(Analyzer(), fingerprint=self.policy.analyzer_fingerprint,
            evidence_store=context.evidence_store, temp_root=owner.root, verify=self.manifest.verify,
            budget=owner.budget, guard=owner, provenance={'manifest': self.manifest.hash, 'config': self.manifest.config},
            source_bindings={key: {'path': str(path)} for key, path in context.paths.items()})
        snapshot = context.snapshot
        def native():
            return NativeBridge(lambda: NativeProcess(context.settings, managed_transport=self.process_factory(owner, 'native')),
                fingerprint=self.policy.native_fingerprint, expected_provenance=self.manifest.value['native_provenance'],
                temp_root=owner.root, verify=self.manifest.verify, budget=owner.budget, guard=owner,
                eos_control=snapshot.control['control_id'], eos_epoch=(snapshot.plan.end_frame-1)//EPOCH,
                eos_final_frame=snapshot.plan.end_frame, failure_sink=context.evidence_store.put,
                expected_imports=self.manifest.files)
        return analyzer, NativeFactory(native)


def _identity_status(identity):
    if (not isinstance(identity, dict) or type(identity.get('pid')) is not int or identity['pid'] <= 0
            or type(identity.get('created')) not in (int, float) or not math.isfinite(identity['created'])
            or identity['created'] <= 0):
        return 'unknown'
    try:
        return 'pid_reused' if psutil.Process(identity['pid']).create_time() != identity['created'] else 'alive'
    except psutil.ZombieProcess:
        return 'unknown'
    except psutil.NoSuchProcess:
        return 'gone'
    except (psutil.Error, OSError):
        return 'unknown'


def _scan_lease_processes(lease, ns, owner, admissions, groups, discover):
    """Bounded, fail-closed observation; no signals and no wait/reaping claims.

    After the original owner is gone it cannot launch another child. The
    admitted children never daemonize or create helpers. A fork-before-exec
    child still has the owner's argv/executable, so matching those is unknown
    ownership rather than evidence of absence. No arbitrary-code claim is made.
    """
    members, candidates, errors = [], [], []
    deadline = time.monotonic() + RECOVERY_SECONDS
    commands = [entry['command'] for entry in admissions.values()]
    try:
        for count, proc in enumerate(psutil.process_iter(), 1):
            if count > RECOVERY_MAX_PROCESSES or time.monotonic() >= deadline:
                errors.append('Process inventory exceeded its finite scan allowance')
                break
            try:
                created = proc.create_time()
                pgid = os.getpgid(proc.pid)
                if pgid in groups:
                    members.append({'pid': proc.pid, 'created': created, 'pgid': pgid})
                if discover:
                    command, env, parent = proc.cmdline(), proc.environ(), proc.ppid()
                    token = env.get('AUDIO_FRAGMENT_PROCESS')
                    relevant = (env.get('AUDIO_FRAGMENT_LEASE') == lease or token in admissions
                                or command in commands or command == owner['command']
                                or parent == owner['pid'])
                    # During exec, cmdline can transiently be empty. A user
                    # process with the owner's executable remains unresolved.
                    if not command and proc.exe() == owner['executable']:
                        relevant = True
                    if relevant:
                        entry = admissions.get(token)
                        verified = (entry is not None and env.get('AUDIO_FRAGMENT_LEASE') == lease
                                    and command == entry['command']
                                    and env.get('AUDIO_FRAGMENT_ROLE') == entry['role']
                                    and env.get('AUDIO_PARENT_PID') == str(owner['pid'])
                                    and env.get('AUDIO_PARENT_CREATED') == str(owner['created'])
                                    and created >= owner['created'] and pgid == proc.pid
                                    and os.readlink(f'/proc/{proc.pid}/ns/pid') == ns['pid_namespace'])
                        candidates.append({'pid': proc.pid, 'created': created, 'pgid': pgid,
                                           'parent_pid': parent, 'token': token,
                                           'status': 'admitted_alive' if verified else 'unverified_candidate'})
                if proc.create_time() != created:
                    errors.append('Process identity changed during ownership scan')
            except psutil.ZombieProcess:
                errors.append(f'Cannot fully inspect zombie process {proc.pid}')
                break
            except (ProcessLookupError, psutil.NoSuchProcess):
                # The observed process exited; the trusted topology cannot
                # transfer admission to an unobserved daemon or grandchild.
                continue
            except (psutil.Error, OSError) as exc:
                errors.append(f'Cannot inspect process {proc.pid}: {type(exc).__name__}')
                break
            if len(members) + len(candidates) > 16:
                errors.append('Unexpected admitted ownership inventory overflow')
                break
    except (psutil.Error, OSError) as exc:
        errors.append('Cannot enumerate ownership: ' + type(exc).__name__)
    if time.monotonic() >= deadline and not errors:
        errors.append('Process inventory exceeded its finite scan allowance')
    return {'complete': not errors, 'errors': errors,
            'group_members': sorted(members, key=lambda item: item['pid']),
            'candidates': sorted(candidates, key=lambda item: item['pid'])}


def inspect_lease(path, lease, manifest):
    """Read-only termination observations, never invented historical reaping.

    The CLI caller holds exclusive InstanceLock. Crash-gap recovery additionally
    needs durable admission, the original owner positively gone in this boot/PID
    namespace, and a complete scan of the trusted no-daemonizing topology.
    """
    with sqlite3.connect(path, timeout=.1) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM fragment_cpu_leases WHERE lease=?', (lease,)).fetchone()
        if row is None or row['manifest'] != manifest.hash or row['state'] != 'pending':
            raise ValueError('Exact pending lease/manifest required')
        ns = namespace()
        if _json(row['namespace']) != ns:
            raise ValueError('Different boot or PID namespace cannot prove old ownership gone')
        session = db.execute('SELECT generation,state FROM fragment_http_sessions WHERE session_id=?', (row['session_id'],)).fetchone()
        if session is None:
            raise ValueError('Lease control session is missing')
        process_rows = [dict(item) for item in db.execute('SELECT * FROM fragment_cpu_processes WHERE lease=? ORDER BY token', (lease,))]
        lease_admission = _json(row['receipt']) if row['receipt'] else {}
        owner = lease_admission.get('owner')
        owner_valid = (lease_admission.get('kind') == 'owner_admission'
                       and lease_admission.get('namespace') == ns
                       and lease_admission.get('topology') == RECOVERY_TOPOLOGY
                       and isinstance(owner, dict) and isinstance(owner.get('command'), list)
                       and bool(owner['command']) and all(isinstance(part, str) for part in owner['command'])
                       and isinstance(owner.get('executable'), str) and bool(owner['executable']))
        owner_status = _identity_status(owner) if owner_valid else 'unknown'
        observations, admissions, groups = [], {}, set()
        requires_discovery = not process_rows
        unresolved = not process_rows
        for item in process_rows:
            identity = _json(item['identity'])
            prior = _json(item['receipt']) if item['receipt'] else {}
            status = 'unknown'
            if item['state'] == 'reaped' and (prior.get('wait_reaped') or prior.get('spawn_failed_before_handle')):
                status = 'recorded_wait_or_no_spawn'
            else:
                unresolved = True
            if isinstance(identity, dict) and identity.get('namespace') == ns:
                if type(identity.get('pgid')) is int and identity['pgid'] > 0:
                    groups.add(identity['pgid'])
                if status != 'recorded_wait_or_no_spawn':
                    status = _identity_status(identity)
                admission = identity.get('admission', {})
                command = admission.get('command')
                if (owner_valid and admission.get('owner') == owner
                        and admission.get('lease') == lease and admission.get('token') == item['token']
                        and admission.get('topology') == RECOVERY_TOPOLOGY
                        and identity.get('role') in {'analyzer', 'native'}
                        and isinstance(command, list) and command
                        and all(isinstance(part, str) for part in command)):
                    admissions[item['token']] = dict(admission, role=identity['role'])
            missing_identity = (not isinstance(identity, dict) or identity.get('pid') is None
                                or item['state'] == 'spawning')
            if status != 'recorded_wait_or_no_spawn' and missing_identity:
                requires_discovery = True
                status = 'unregistered'
            observations.append({'token': item['token'], 'identity': identity, 'status': status, 'prior': prior})
            for child in prior.get('unexpected_descendants',[]):
                unresolved = True
                observations.append({'token': item['token'] + ':descendant', 'identity': child,
                                     'status': _identity_status(child),
                                     'prior': {'historical_wait_reaping_not_inferred': True}})
        owner_gone = owner_status in {'gone', 'pid_reused'}
        discover = requires_discovery and owner_valid and owner_gone
        scan = _scan_lease_processes(lease, ns, owner, admissions, groups, discover)
        if discover and scan['complete']:
            for item in observations:
                if item['status'] == 'unregistered' and item['token'] in admissions:
                    matches = [candidate for candidate in scan['candidates'] if candidate['token'] == item['token']]
                    if len(matches) == 1 and matches[0]['status'] == 'admitted_alive':
                        item['status'], item['observed_identity'] = 'admitted_alive', matches[0]
                    elif not scan['candidates']:
                        item['status'] = 'no_admitted_child_observed'
        allowed = {'recorded_wait_or_no_spawn', 'gone', 'pid_reused', 'no_admitted_child_observed'}
        value = {'lease': dict(row), 'session': dict(session), 'namespace': ns,
                 'owner': {'identity': owner, 'status': owner_status}, 'process_rows': process_rows,
                 'processes': observations, 'group_members': scan['group_members'], 'scan': scan,
                 'resolvable': (scan['complete'] and not scan['group_members'] and not scan['candidates']
                                and (not unresolved or owner_gone)
                                and (not requires_discovery or discover)
                                and all(item['status'] in allowed for item in observations))}
        return {'inspection': value, 'sha256': digest(value)}


def reconcile_lease(path, lease, manifest, expected_inspection):
    inspected = inspect_lease(path, lease, manifest)
    if inspected['sha256'] != expected_inspection or not inspected['inspection']['resolvable']:
        raise ValueError('Ownership inspection changed or lacks positive termination evidence')
    value = inspected['inspection']
    root = Path(value['lease']['scratch'])
    expected = Path(path).parent / 'fragment-work' / lease
    if root != expected or root.resolve() != root:
        raise ValueError('Lease scratch no longer belongs to this data directory')
    with transaction(path) as db:
        row = db.execute('SELECT * FROM fragment_cpu_leases WHERE lease=?', (lease,)).fetchone()
        if row is None:
            raise ValueError('Lease changed during reconciliation')
        session = db.execute('SELECT generation,state FROM fragment_http_sessions WHERE session_id=?', (row['session_id'],)).fetchone()
        process_rows = [dict(item) for item in db.execute('SELECT * FROM fragment_cpu_processes WHERE lease=? ORDER BY token', (lease,))]
        if session is None or dict(row) != value['lease'] or dict(session) != value['session'] or process_rows != value['process_rows']:
            raise ValueError('Lease/control generation changed during reconciliation')
        if root.exists():
            shutil.rmtree(root)
        receipt = {'kind': 'operator_reconciled_termination', 'inspection': value,
                   'inspection_hash': expected_inspection, 'historical_wait_reaping_not_inferred': True}
        db.execute("UPDATE fragment_cpu_leases SET state='released',receipt=? WHERE lease=?", (canonical(receipt), lease))
        db.execute('UPDATE fragment_http_sessions SET cleanup_error=NULL WHERE session_id=?', (row['session_id'],))
    return receipt
