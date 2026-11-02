"""Finite test-only SIGKILL barriers; generated component data, no descendants."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import pytest
from audio_backend import storage, speaker_support as support, strict_payload_v2 as payload, support_vectors
from audio_backend.resources import InstanceLock
from test_strict_payload_v2 import component_store, completed_component, component_frontier, configuration
from test_strict_archive_v2 import positive_close
from synthetic_store_process import source_identity, inspect_database


def digest(raw): return hashlib.sha256(raw).hexdigest()


def atomic_json(path, value):
    raw = json.dumps(value, sort_keys=True).encode()
    temporary = path.with_suffix('.tmp')
    with temporary.open('xb') as output:
        output.write(raw); output.flush(); os.fsync(output.fileno())
    os.replace(temporary, path)


def files(root):
    directory = root / 'strict-payload-v2'
    return {str(p.relative_to(root)): {'sha256':digest(p.read_bytes()), 'bytes':p.stat().st_size}
            for p in sorted(directory.rglob('*')) if p.is_file()}


def barrier(evidence, mode, nonce, root, state):
    # Test evidence is outside the Store namespace. No Store operation occurs
    # after this atomic acknowledgement; parent must kill/reap this exact child.
    atomic_json(evidence / 'barrier.json', {'mode':mode, 'nonce':nonce, 'pid':os.getpid(),
        'files':files(root), 'state':state})
    while True: time.sleep(1.)


class PlacementConnection:
    """Forward every real SQLite operation; block only the known placement COMMIT."""
    def __init__(self, original, pause, after, capture=None):
        object.__setattr__(self, '_db', original)
        object.__setattr__(self, '_pause', pause)
        object.__setattr__(self, '_after', after)
        object.__setattr__(self, '_placement', False)
        object.__setattr__(self, '_reservation', False)
        object.__setattr__(self, '_capture', capture)
    def __getattr__(self, name): return getattr(self._db, name)
    def __setattr__(self, name, value):
        if name.startswith('_'): object.__setattr__(self, name, value)
        else: setattr(self._db, name, value)
    def execute(self, sql, args=()):
        result = self._db.execute(sql, args)
        if sql.startswith("UPDATE strict_payload_unit SET state='committed'"):
            self._placement = True
        if sql.startswith('INSERT INTO strict_payload_unit VALUES('): self._reservation = True
        return result
    def commit(self):
        if self._placement and not self._after: self._pause(self._db)
        self._db.commit()
        if self._reservation and self._capture is not None: self._capture(self._db)
        if self._placement and self._after: self._pause(self._db)


class LinkBarrier:
    def __init__(self, original, pause): self.original, self.pause = original, pause
    def __getattr__(self, name): return getattr(self.original, name)
    def link(self, source, destination, **kwargs):
        assert Path(source).suffix == '.stage' and Path(destination).suffix == '.unit'
        assert Path(source).is_file() and not Path(destination).exists()
        self.pause()
        return self.original.link(source, destination, **kwargs)


def db_state(db, cid):
    row = dict(db.execute('SELECT * FROM strict_payload_unit WHERE cid=?', (cid,)).fetchone())
    return {'unit':row,
        'locators': [dict(x) for x in db.execute('SELECT * FROM strict_payload_object ORDER BY kind,owner_id')],
        'hot_pcm': db.execute('SELECT pcm IS NOT NULL FROM speaker_support WHERE chunk_id=?', (cid,)).fetchone()[0],
        'hot_vector': db.execute('SELECT v.raw_vector IS NOT NULL FROM speaker_support_vectors v JOIN speaker_support s ON s.id=v.support_id WHERE s.chunk_id=?', (cid,)).fetchone()[0],
        'units':db.execute('SELECT count(*) FROM strict_payload_unit').fetchone()[0]}


def writer(mode, root, evidence, nonce):
    root.mkdir(); lock = InstanceLock(root / 'instance.lock'); lock.acquire()
    patch = pytest.MonkeyPatch(); store = component_store(root)
    value = completed_component(store)
    witness, _ = component_frontier(value, patch)
    receipt = {'source':source_identity(), 'policy':store.settings.strict_primary_policy,
        'cid':value.cid, 'sid':value.sid, 'support_id':value.support['id'],
        'stream_id':value.receipt.stream_id, 'frames':value.receipt.frames,
        'audio_sha256':digest(value.audio), 'audio_bytes':len(value.audio),
        'pcm_sha256':digest(value.support['pcm']), 'vector_sha256':digest(value.vector['raw_vector']),
        'nonce':nonce, 'mode':mode, 'pid':os.getpid()}
    atomic_json(evidence / 'receipt.json', receipt)
    budget = payload.PayloadBudget()
    if mode == 'before_link':
        captured = []
        original = storage.sqlite3.connect
        patch.setattr(storage.sqlite3, 'connect', lambda *a, **kw:
            PlacementConnection(original(*a, **kw), lambda db: None, False,
                lambda db: captured.append(db_state(db, value.cid))))
        def pause():
            assert len(captured) == 1
            state = captured[0]
            assert state['unit']['state'] == 'reserved' and state['hot_pcm'] == state['hot_vector'] == 1
            assert not state['locators']
            barrier(evidence, mode, nonce, root, state)
        patch.setattr(payload, 'os', LinkBarrier(payload.os, pause))
    else:
        original = storage.sqlite3.connect
        def pause(db):
            state = db_state(db, value.cid)
            assert state['unit']['state'] == 'committed'
            assert state['hot_pcm'] == state['hot_vector'] == 0 and len(state['locators']) == 2
            assert db.in_transaction is (mode == 'before_commit')
            barrier(evidence, mode, nonce, root, state)
        patch.setattr(storage.sqlite3, 'connect', lambda *a, **kw:
            PlacementConnection(original(*a, **kw), pause, mode == 'after_commit'))
    # Returning means the requested boundary was not hit: never report success.
    store.maintain_payloads(witness, budget)
    raise AssertionError('crash boundary was not reached')


def reader(mode, root, evidence, nonce):
    receipt = json.loads((evidence / 'receipt.json').read_text())
    observed = json.loads((evidence / 'barrier.json').read_text())
    assert receipt['nonce'] == observed['nonce'] == nonce
    assert receipt['mode'] == observed['mode'] == mode and receipt['source'] == source_identity()
    lock = InstanceLock(root / 'instance.lock'); lock.acquire()
    store = None; patch = pytest.MonkeyPatch()
    try:
        contender = InstanceLock(root / 'instance.lock')
        with pytest.raises(RuntimeError): contender.acquire()
        store = storage.Store(root / 'primary.sqlite', configuration(root))
        store.moss_automatic = SimpleNamespace(primary_only=True)
        inspect_database(store)
        assert files(root) == observed['files']
        with store.db() as db: state = db_state(db, receipt['cid'])
        expected = {'before_link':'reserved', 'before_commit':'published', 'after_commit':'committed'}[mode]
        assert state['unit']['state'] == expected and state['units'] == 1
        for key in ('unit_id', 'sequence', 'bytes', 'bucket', 'evidence_sha256', 'manifest_sha256'):
            assert state['unit'][key] == observed['state']['unit'][key]
        if mode != 'before_link':
            for key in ('file_sha256', 'signature'):
                assert state['unit'][key] == observed['state']['unit'][key]
        assert state['hot_pcm'] == state['hot_vector'] == (0 if mode == 'after_commit' else 1)
        assert len(state['locators']) == (2 if mode == 'after_commit' else 0)
        audio = support.audio(store, receipt['sid'], receipt['support_id'])
        assert len(audio) == receipt['audio_bytes'] and digest(audio) == receipt['audio_sha256']
        value = SimpleNamespace(store=store, sid=receipt['sid'],
            receipt=SimpleNamespace(stream_id=receipt['stream_id'], frames=receipt['frames']))
        witness, _ = component_frontier(value, patch)
        budget = payload.PayloadBudget(); deadline = budget.deadline
        if mode == 'before_link':
            with pytest.raises(payload.PayloadCapacityError, match='payload_reserved_unit_pending'):
                store.maintain_payloads(witness, budget)
            with store.db() as db: assert db_state(db, receipt['cid']) == state
            assert all(not name.endswith('.unit') for name in files(root))
        elif mode == 'before_commit':
            result = store.maintain_payloads(witness, budget)
            assert result['state'] == 'committed' and result['resumed'] is True
            assert result['unit_id'] == state['unit']['unit_id']
            with store.db() as db:
                final = db_state(db, receipt['cid'])
                assert final['units'] == 1 and final['locators'] == observed['state']['locators']
                assert final['hot_pcm'] == final['hot_vector'] == 0
                for key in ('unit_id', 'sequence', 'bytes', 'file_sha256', 'signature'):
                    assert final['unit'][key] == state['unit'][key]
        else:
            assert state['locators'] == observed['state']['locators']
        assert budget.deadline == deadline
        assert files(root) == observed['files']
        audio = support.audio(store, receipt['sid'], receipt['support_id'])
        assert digest(audio) == receipt['audio_sha256']
        if mode != 'before_link':
            read_budget = payload.PayloadBudget(); read_deadline = read_budget.deadline
            with store.db() as db:
                db.execute('BEGIN')
                with store.payload_resolver(db, budget=read_budget) as resolver:
                    row = support._row(db, receipt['sid'], receipt['support_id'], True, payload=resolver)
                    assert row['actual_pcm_bytes'] == len(row['pcm']) > 0
                    assert digest(row['pcm']) == receipt['pcm_sha256']
                    vector, normalized = support_vectors.read(db, receipt['support_id'], payload=resolver)
                    assert digest(vector['raw_vector']) == receipt['vector_sha256']
                    assert normalized[0] == 1.
            assert read_budget.deadline == read_deadline
            assert read_budget.sql_steps > 0 and read_budget.read_bytes > 0
            exhausted = payload.PayloadBudget(); exhausted.sql_steps = payload.base.MAX_STEPS
            deadline = exhausted.deadline
            with pytest.raises(payload.base.ArchiveLimit):
                with store.db() as db:
                    db.execute('BEGIN')
                    with store.payload_resolver(db, budget=exhausted) as resolver:
                        support._row(db, receipt['sid'], receipt['support_id'], True, payload=resolver)
            assert exhausted.deadline == deadline and exhausted.sql_steps >= payload.base.MAX_STEPS
        inspect_database(store)
        assert files(root) == observed['files']
    finally:
        patch.undo()
        if store is not None: positive_close(store)
        lock.release()
    print(json.dumps({'mode':mode, 'pid':os.getpid(), 'nonce':nonce, 'verified':True}))


if __name__ == '__main__':
    assert len(sys.argv) == 6
    role, mode, root, evidence, nonce = sys.argv[1:]
    assert mode in ('before_link', 'before_commit', 'after_commit')
    root, evidence = Path(root).resolve(), Path(evidence).resolve()
    assert not evidence.is_relative_to(root) and not root.is_relative_to(evidence)
    (writer if role == 'writer' else reader)(mode, root, evidence, nonce)
