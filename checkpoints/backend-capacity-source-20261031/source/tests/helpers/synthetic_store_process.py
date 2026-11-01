"""Bounded generated-data child; no models/Worker/descendants. Never an app launcher."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import pytest
from audio_backend import speaker_support as support
from audio_backend import strict_payload_v2 as payload
from audio_backend.resources import InstanceLock
from audio_backend.storage import Store
from test_strict_payload_v2 import component_store, completed_component, make_cold, configuration
from test_strict_archive_v2 import positive_close


def digest(body):
    return hashlib.sha256(body).hexdigest()


def source_identity():
    # Bind all local product/test source and fixture bytes, not just the edited
    # module. Python/SQLite versions are recorded; third-party installations are
    # not exhaustively attested by this component test.
    files = sorted(p for base in (ROOT / 'audio_backend', ROOT / 'tests')
                   for p in base.rglob('*') if p.is_file() and
                   p.suffix in ('.py', '.json', '.sql', '.txt') and '__pycache__' not in p.parts)
    assert all(not p.is_symlink() for p in files)
    return {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in files}



def inspect_database(store):
    with store.db() as db:
        assert db.execute('PRAGMA integrity_check').fetchall()[0][0] == 'ok'
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def run(mode, root):
    root.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(root / 'instance.lock')
    lock.acquire()
    # Independently demonstrate a second actual lock cannot enter this lifetime.
    contender = InstanceLock(root / 'instance.lock')
    try:
        contender.acquire()
    except RuntimeError:
        pass
    else:
        contender.release()
        raise AssertionError('instance lock was not exclusive')
    store = None
    patch = pytest.MonkeyPatch()
    try:
        if mode == 'writer':
            store = component_store(root)
            value = make_cold(completed_component(store), patch)
            audio = support.audio(store, value.sid, value.support['id'])
            inspect_database(store)
            receipt = {'schema': 'synthetic-process-reopen-v1', 'producer_pid': os.getpid(),
                'runtime': {'python': sys.version, 'sqlite': sqlite3.sqlite_version},
                'source': source_identity(), 'policy': store.settings.strict_primary_policy,
                'sid': value.sid, 'support_id': value.support['id'],
                'audio_sha256': digest(audio), 'audio_bytes': len(audio),
                'unit_relative': str(value.unit_path.relative_to(root)),
                'unit_sha256': digest(value.unit_path.read_bytes())}
            (root / 'generated_receipt.json').write_text(json.dumps(receipt, sort_keys=True))
        elif mode == 'reader':
            receipt = json.loads((root / 'generated_receipt.json').read_text())
            assert receipt['schema'] == 'synthetic-process-reopen-v1'
            assert receipt['source'] == source_identity()
            assert receipt['runtime'] == {'python': sys.version, 'sqlite': sqlite3.sqlite_version}
            cfg = configuration(root)
            assert cfg.strict_primary_policy == receipt['policy']
            store = Store(root / 'primary.sqlite', cfg)
            store.moss_automatic = SimpleNamespace(primary_only=True)
            inspect_database(store)
            unit = root / receipt['unit_relative']
            assert unit.resolve().is_relative_to(root.resolve())
            assert digest(unit.read_bytes()) == receipt['unit_sha256']
            audio = support.audio(store, receipt['sid'], receipt['support_id'])
            assert digest(audio) == receipt['audio_sha256'] and len(audio) == receipt['audio_bytes']
            budget = payload.PayloadBudget()
            deadline = budget.deadline
            with store.db() as db:
                db.execute('BEGIN')
                assert db.execute('SELECT pcm FROM speaker_support WHERE id=?', (receipt['support_id'],)).fetchone()[0] is None
                with store.payload_resolver(db, budget=budget) as resolver:
                    row = support._row(db, receipt['sid'], receipt['support_id'], True, payload=resolver)
                    assert row['actual_pcm_bytes'] == len(row['pcm']) > 0
            assert budget.deadline == deadline and budget.sql_steps > 0 and budget.read_bytes > 0
            exhausted = payload.PayloadBudget()
            exhausted.sql_steps = payload.base.MAX_STEPS
            deadline = exhausted.deadline
            with pytest.raises(payload.base.ArchiveLimit):
                with store.db() as db:
                    db.execute('BEGIN')
                    with store.payload_resolver(db, budget=exhausted) as resolver:
                        support._row(db, receipt['sid'], receipt['support_id'], True, payload=resolver)
            assert exhausted.deadline == deadline and exhausted.sql_steps >= payload.base.MAX_STEPS
            assert digest(unit.read_bytes()) == receipt['unit_sha256']
        else:
            raise ValueError('unknown child mode')
    finally:
        # No retries of uncertain original owners; failure exits nonzero.
        patch.undo()
        if store is not None:
            positive_close(store)
        lock.release()
    print(json.dumps({'mode': mode, 'pid': os.getpid(), 'clean_exit_ready': True}))


if __name__ == '__main__':
    assert len(sys.argv) == 3
    run(sys.argv[1], Path(sys.argv[2]).resolve())
