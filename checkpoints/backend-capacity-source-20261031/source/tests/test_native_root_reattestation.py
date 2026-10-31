"""Finite, model-free synthetic source proposal, not execution evidence.

Uses only newly created fixtures beneath the test working directory, which must
have safe ancestors. No original audio/database/model/API/server is opened.
The injected SQLite profile simulates mechanics, not production qualification.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

from audio_backend import native_root_reattestation as attest
from audio_backend import native_owned_cell_store as cells
from audio_backend.native_owned_cells import Unavailable, canonical
from audio_backend.native_owned_core_seams import store_policy
from audio_backend.resources import InstanceLock
from test_native_owned_cell_store import simulated_profile, SyntheticAuthority

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux inherited flock contract')
if sys.platform.startswith('linux'):
    import fcntl


def sha(raw):return hashlib.sha256(raw).hexdigest()


def snapshot(path):
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()
                for table in ('store_meta', 'sources', 'cells', 'dependencies')}
    finally:db.close()


@pytest.fixture
def case(monkeypatch):
    # Deliberately avoid /tmp's world-writable ancestor. No production safety
    # checker is monkeypatched; root chooses a safe writable synthetic workdir.
    with tempfile.TemporaryDirectory(prefix='.synthetic-root-attestation-', dir=Path.cwd()) as name:
        root = Path(name); data = root / 'data'; data.mkdir(mode=0o700)
        from audio_backend.settings import Settings
        from audio_backend.storage import Store
        primary_store = Store(data / 'jobs.sqlite3', Settings(data_dir=data, start_worker=False))
        control = data / 'instance.lock'; control.write_bytes(b'unchanged synthetic lock\n')
        native = data / 'native-owned-cells-bounded-v1'; native.mkdir(mode=0o700)
        path = native / 'cells.sqlite3'
        db = sqlite3.connect(path)
        cells.CellStore._schema(db); schema_sha = attest._schema_sha(db)
        profile = simulated_profile(data)
        settings = SimpleNamespace(data_dir=data, native_owned_cells_enabled=True,
            native_owned_core_seams_enabled=True, native_owned_bounded_core_support_enabled=True,
            moss_automatic_enabled=True, native_owned_support_enabled=True, lexical_evidence_enabled=True,
            diarization_backend='nemotron', inference_device='cpu', overlap_backend='none', start_worker=True)
        monkeypatch.setattr(cells, 'qualified_runtime', lambda _: profile)
        fd, chain = attest._walk(data, time.monotonic() + 10.); os.close(fd)
        namespace = [native.stat().st_dev, native.stat().st_ino]
        primary = (data / 'jobs.sqlite3').stat(); native_info = path.stat()
        primary_sha = sha(canonical({'path': str(data / 'jobs.sqlite3'), 'device': primary.st_dev, 'inode': primary.st_ino}))
        old_chain = [list(pair) for pair in chain]; old_chain[0][0] += 100000
        binding = [cells.SCHEMA, store_policy(bounded=True), primary_sha,
                   sha(canonical(asdict(profile))),
                   canonical({'ancestors': old_chain, 'namespace': namespace}).decode('ascii'),
                   canonical([native_info.st_dev, native_info.st_ino]).decode('ascii')]
        db.execute('INSERT INTO store_meta VALUES (1,?,?,?,?,?,?)', binding); db.commit(); db.close()
        baseline_value = {'status': 'complete', 'changed': [], 'exclusive_locks_acquired': 2,
            'ownership_status': 'both_existing_flocks_held',
            'native_binding': {'stored': dict(zip(attest.FIELDS, binding)),
                'attribution_limit': 'Synthetic collector; not a historical server observation'},
            'ownership_locks': {'instance': [control.stat().st_dev, control.stat().st_ino,
                control.stat().st_mode, control.stat().st_nlink, control.stat().st_size,
                control.stat().st_mtime_ns, control.stat().st_ctime_ns]},
            'native_ancestry_after': {'ancestors': [
                {'path': str(p), 'device': pair[0], 'inode': pair[1]}
                for p, pair in zip(reversed((data, *data.parents)), chain)],
                'namespace': {'path': str(native), 'device': namespace[0], 'inode': namespace[1]},
                'primary': {'path': str(data / 'jobs.sqlite3'), 'device': primary.st_dev, 'inode': primary.st_ino},
                'primary_identity': primary_sha}}
        baseline = root / 'baseline.json'; baseline.write_bytes(canonical(baseline_value))
        value = {'schema': attest.SCHEMA, 'data_path': str(data),
            'primary_path': str(data / 'jobs.sqlite3'), 'native_path': str(path),
            'lock_path': str(control), 'lock_identity': [control.stat().st_dev, control.stat().st_ino],
            'old_binding': binding, 'old_binding_sha256': sha(canonical(binding)),
            'new_root': list(chain[0]), 'native_schema_sha256': schema_sha,
            'baseline': {'path': str(baseline), 'sha256': sha(baseline.read_bytes())}}
        artifact = root / 'attestation.json'; artifact.write_bytes(canonical(value))
        authority = SyntheticAuthority(); authority.identity = primary_sha
        authority.store = SimpleNamespace(settings=settings)
        with control.open('rb', buffering=0) as supervisor:
            fcntl.flock(supervisor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock = InstanceLock(control).adopt(os.dup(supervisor.fileno()))
            item = SimpleNamespace(root=root, data=data, path=path, control=control,
                profile=profile, settings=settings, value=value, artifact=artifact, baseline=baseline,
                authority=authority, lock=lock, supervisor=supervisor, chain=chain, primary_store=primary_store)
            try:yield item
            finally:lock.release()


def rewrite(case, value=None):
    case.artifact.write_bytes(canonical(case.value if value is None else value))
    return sha(case.artifact.read_bytes())


def load(case):
    return attest.load(case.artifact, sha(case.artifact.read_bytes()), case.settings, case.lock)


def owner(case, admission=None):
    result = cells.CellStore(case.data / 'native-owned-cells', case.authority,
        enabled=True, qualification=case.profile, instance_lock=case.lock,
        core_seam_enabled=True, root_reattestation=admission)
    case.authority.owner = result
    return result


@contextmanager
def opened(case, admission=None):
    item = owner(case, admission)
    try:
        item._open()
        yield item
    finally:
        if item._db is not None:item._db.close(); item._db = None


def test_two_direct_owner_opens_preserve_populated_rows_proof_pcm_and_key(case):
    from test_native_owned_cells import synthetic_cell
    receipt = cells.Receipt('session', 'chunk', 'c' * 64, 16, 44, 76, (1, 1, 76, 1, 1), 0., None)
    cell = synthetic_cell(receipt_id=receipt.receipt_id)
    db = sqlite3.connect(case.path)
    db.execute('INSERT INTO sources VALUES(?,?,?,?)', ('chunk', 'session', receipt.digest, receipt.encoded))
    db.execute('INSERT INTO cells(cell_id,ownership,sid,cid,proof,pcm,state) VALUES(?,?,?,?,?,?,?)',
        (cell.cell_id, cell.ownership, cell.session_id, cell.chunk_id, cell.proof, cell.pcm, 'retained'))
    for position, values in enumerate(cells.CellStore._dependency_values(json.loads(cell.proof), cell.parts)):
        db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)', (cell.cell_id, position, *values))
    db.commit(); db.close()
    before = snapshot(case.path); bytes_before = case.path.read_bytes()
    lock_bytes = case.control.read_bytes(); key = sha(canonical(case.value['old_binding']))
    for _ in range(2):
        admission = load(case)
        with opened(case, admission) as item:
            assert item.retention_store_key == key
            assert item._ancestor_identity == case.chain
            assert item._ancestor_identity[0] != tuple(json.loads(case.value['old_binding'][4])['ancestors'][0])
            assert item._root_reattestation_diagnostic['change'] == 'root_device_only'
            assert item._receipts['chunk'] == receipt  # _open alone does not claim owner readiness
            detail = item._read(cells.Operation('detail', cell.cell_id, item._epoch, item._wake), cells.Budget())
            assert detail == {'status': 'available', 'detail': cell.proof, 'regeneration_eligible': True}
        assert snapshot(case.path) == before
        assert case.path.read_bytes() == bytes_before
    assert case.control.read_bytes() == lock_bytes


def test_without_opt_in_existing_strict_refusal(case):
    with pytest.raises(Unavailable, match='optional_primary_binding_mismatch'):
        with opened(case):pass


@pytest.mark.parametrize('index', [0, 1, 2, 3, 5])
def test_all_five_nonnamespace_fields_are_exact(case, index):
    current = list(case.value['old_binding'])
    namespace = json.loads(current[4]); namespace['ancestors'][0] = case.value['new_root']
    current[4] = canonical(namespace).decode('ascii'); current[index] += 'x'
    with pytest.raises(Unavailable, match='nonnamespace_binding_mismatch'):
        attest._compare(case.value, case.value['old_binding'], current)


@pytest.mark.parametrize('change', ['wrong_root', 'root_inode', 'suffix', 'namespace', 'old_tuple',
    'old_hash', 'schema', 'policy', 'qualification', 'database', 'logical_path', 'baseline_hash'])
def test_malformed_or_stale_artifact_refuses(case, change):
    value = deepcopy(case.value)
    if change == 'wrong_root':value['new_root'][0] += 1
    elif change == 'root_inode':value['new_root'][1] += 1
    elif change in ('suffix', 'namespace'):
        ns = json.loads(value['old_binding'][4])
        if change == 'suffix':ns['ancestors'][-1][1] += 1
        else:ns['namespace'][1] += 1
        value['old_binding'][4] = canonical(ns).decode('ascii')
    elif change == 'old_tuple':value['old_binding'][2] = 'b' * 64
    elif change == 'old_hash':value['old_binding_sha256'] = 'b' * 64
    elif change == 'schema':value['old_binding'][0] += '-unknown'
    elif change == 'policy':value['old_binding'][1] = 'b' * 64
    elif change == 'qualification':value['old_binding'][3] = 'b' * 64
    elif change == 'database':value['old_binding'][5] = '[0,1]'
    elif change == 'logical_path':value['native_path'] += '.other'
    elif change == 'baseline_hash':value['baseline']['sha256'] = 'b' * 64
    if change != 'old_hash':value['old_binding_sha256'] = sha(canonical(value['old_binding']))
    with pytest.raises((Unavailable, ValueError, OSError)):
        attest.load(case.artifact, rewrite(case, value), case.settings, case.lock)


def test_wrong_external_artifact_pin_refuses(case):
    with pytest.raises(Unavailable, match='artifact_hash_mismatch'):
        attest.load(case.artifact, '0' * 64, case.settings, case.lock)


@pytest.mark.parametrize('target', ['artifact', 'baseline', 'control', 'path'])
@pytest.mark.parametrize('unsafe', ['symlink', 'hardlink', 'writable'])
def test_unsafe_files_refuse_without_mutating(case, target, unsafe):
    path = getattr(case, target); before = path.read_bytes()
    if unsafe == 'symlink':
        moved = path.with_name(path.name + '.original'); path.rename(moved); path.symlink_to(moved)
    elif unsafe == 'hardlink':os.link(path, path.with_name(path.name + '.alias'))
    else:path.chmod(path.stat().st_mode | 0o020)
    with pytest.raises((Unavailable, ValueError, OSError)):load(case)
    assert path.read_bytes() == before


def test_wrong_uid_and_type_checks_are_not_skipped(case):
    info = case.artifact.stat()
    values = {name: getattr(info, name) for name in ('st_dev', 'st_ino', 'st_mode',
        'st_uid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
    with pytest.raises(Unavailable, match='unsafe_file'):
        attest._regular(SimpleNamespace(**{**values, 'st_uid': info.st_uid + 1}))
    with pytest.raises(Unavailable, match='unsafe_file'):
        attest._regular(SimpleNamespace(**{**values, 'st_mode': stat.S_IFIFO | 0o600}))
    with pytest.raises(Unavailable, match='unsafe_directory'):
        attest._directory(SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=os.getuid()))


def test_artifact_replacement_after_admission_refuses(case):
    admission = load(case); replacement = case.root / 'replacement.json'
    replacement.write_bytes(case.artifact.read_bytes()); replacement.replace(case.artifact)
    with pytest.raises(Unavailable, match='artifact_replaced'):
        with opened(case, admission):pass


def test_baseline_replacement_after_admission_refuses(case):
    admission = load(case); replacement = case.root / 'baseline-replacement.json'
    replacement.write_bytes(case.baseline.read_bytes()); replacement.replace(case.baseline)
    with pytest.raises(Unavailable, match='baseline_replaced'):
        with opened(case, admission):pass


def test_artifact_read_race_refuses(case, monkeypatch):
    before = snapshot(case.path)
    real = os.read; replaced = False
    def racing(fd, size):
        nonlocal replaced
        raw = real(fd, size)
        if not replaced:
            replaced = True
            other = case.root / 'racing.json'; other.write_bytes(case.artifact.read_bytes()); other.replace(case.artifact)
        return raw
    monkeypatch.setattr(attest.os, 'read', racing)
    # Atomic rename unlinks the held old inode (nlink=0), which may trip the
    # earlier regular-file guard before the exact metadata equality fence.
    with pytest.raises(Unavailable, match=r'^root_reattestation_(unsafe_file|file_replaced)$'):load(case)
    assert replaced and snapshot(case.path) == before


@pytest.mark.parametrize('payload,reason', [
    (b'{}', 'invalid_artifact_schema'),
    (b'{"schema":1,"schema":2}', 'duplicate_json_key'),
    (b'[' * 2000, 'invalid_json'),
    (b' ' * (attest.ARTIFACT_BYTES + 1), 'file_capacity')])
def test_bounded_parser_rejects_bad_inputs(case, payload, reason):
    case.artifact.write_bytes(payload)
    with pytest.raises(Unavailable, match='^root_reattestation_' + reason + '$'):load(case)


def test_noncanonical_json_refuses(case):
    case.artifact.write_text(json.dumps(case.value, indent=2))
    with pytest.raises(Unavailable, match='noncanonical_artifact'):load(case)


def test_finite_read_deadline(case):
    with pytest.raises(Unavailable, match='read_deadline'):
        attest._read_file(case.artifact, attest.ARTIFACT_BYTES, time.monotonic() - 1.)


def test_no_lock_wrong_lock_released_lock_refuse(case):
    for bad in (None, object(), InstanceLock(case.control)):
        with pytest.raises((Unavailable, ValueError)):
            attest.load(case.artifact, sha(case.artifact.read_bytes()), case.settings, bad)
    case.lock.release()
    with pytest.raises(ValueError):load(case)


@pytest.mark.parametrize('sidecar', ['cells.sqlite3-journal', 'cells.sqlite3-wal', 'cells.sqlite3-shm', 'unknown'])
def test_sidecar_or_unknown_entry_refuses(case, sidecar):
    (case.path.parent / sidecar).write_bytes(b'')
    with pytest.raises(Unavailable, match='sidecar_or_unexpected_file'):load(case)


def test_actual_native_schema_drift_refuses(case):
    db = sqlite3.connect(case.path)
    db.execute('CREATE INDEX unreviewed ON sources(digest)'); db.close()
    with pytest.raises(Unavailable, match='schema_mismatch'):load(case)


def test_actual_native_binding_drift_refuses(case):
    db = sqlite3.connect(case.path)
    db.execute("UPDATE store_meta SET primary_identity=?", ('b' * 64,)); db.commit(); db.close()
    with pytest.raises(Unavailable, match='stored_binding_mismatch'):load(case)


def test_runtime_qualification_mismatch_refuses(case, monkeypatch):
    monkeypatch.setattr(cells, 'qualified_runtime', lambda _: replace(case.profile, evidence_sha256='e' * 64))
    with pytest.raises(Unavailable, match='qualification_mismatch'):load(case)


def test_current_ancestor_fences_still_reject_root_and_suffix_drift(case):
    admission = load(case)
    with opened(case, admission) as item:
        for index in (0, -1):
            saved = item._ancestor_identity; changed = list(saved)
            changed[index] = (changed[index][0] + 1, changed[index][1]); item._ancestor_identity = tuple(changed)
            with pytest.raises(Unavailable, match='cell_ancestor_replaced'):item._scan()
            item._ancestor_identity = saved


def test_opt_in_never_creates_missing_native_database(case):
    case.path.unlink()
    with pytest.raises((Unavailable, OSError)):load(case)
    assert not case.path.exists()


def test_artifact_is_not_a_source_authority_override(case):
    admission = load(case)
    with opened(case, admission) as item:
        case.authority.valid = False
        receipt = cells.Receipt('session', 'chunk', 'c' * 64, 16, 44, 76, (1, 1, 76, 1, 1), 0., None)
        operation = cells.Operation('receipt', receipt, item._epoch, item._wake)
        with pytest.raises(Unavailable, match='source_authority_unavailable'):
            item._receipt(operation, cells.Budget())
        assert item._db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 0
        assert item.retention_store_key == case.value['old_binding_sha256']


def test_application_preflight_occurs_before_store(case, monkeypatch):
    from audio_backend import app
    from audio_backend.settings import Settings
    events = []
    def forbidden_store(*args, **kwargs):
        events.append('store'); raise AssertionError('Store must not run after rejected attestation')
    monkeypatch.setattr(app, 'Store', forbidden_store)
    settings = Settings(data_dir=case.data, start_worker=False)
    with pytest.raises(Unavailable):
        app.create_app(settings, instance_lock_fd=os.dup(case.supervisor.fileno()),
            native_root_reattestation=case.artifact, native_root_reattestation_sha256='0' * 64)
    assert events == []
    case.lock.verify_inherited(case.control)


def test_cli_requires_both_pins_and_inherited_lock(monkeypatch):
    from audio_backend import __main__ as cli
    for args in (['--native-root-reattestation', '/absent'],
                 ['--native-root-reattestation-sha256', '0' * 64],
                 ['--native-root-reattestation', '/absent', '--native-root-reattestation-sha256', '0' * 64]):
        with pytest.raises(SystemExit) as error:cli.main(args)
        assert error.value.code == 2


@pytest.mark.parametrize('change', ['float_lock_count', 'wrong_primary_path', 'wrong_ancestor_path',
                                  'wrong_lock_identity', 'wrong_binding_shape'])
def test_pinned_but_inconsistent_baseline_provenance_refuses(case, change):
    baseline = json.loads(case.baseline.read_bytes())
    if change == 'float_lock_count':baseline['exclusive_locks_acquired'] = 2.0
    elif change == 'wrong_primary_path':baseline['native_ancestry_after']['primary']['path'] += '.wrong'
    elif change == 'wrong_ancestor_path':baseline['native_ancestry_after']['ancestors'][-1]['path'] += '.wrong'
    elif change == 'wrong_lock_identity':baseline['ownership_locks']['instance'][1] += 1
    elif change == 'wrong_binding_shape':baseline['native_binding'] = []
    case.baseline.write_bytes(canonical(baseline))
    case.value['baseline']['sha256'] = sha(case.baseline.read_bytes()); rewrite(case)
    with pytest.raises(Unavailable):load(case)


def test_native_capacity_refuses_before_any_sqlite_open(case, monkeypatch):
    os.truncate(case.path, cells.DATABASE_BYTES + 1)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args); raise AssertionError('Capacity must be checked before SQLite open')
    monkeypatch.setattr(attest.sqlite3, 'connect', forbidden)
    with pytest.raises(Unavailable, match='database_capacity'):load(case)
    assert calls == []


def test_registry_cannot_reuse_owner_under_changed_admission(case, monkeypatch):
    from audio_backend import native_owned_cell_capture as capture
    admission = load(case); item = owner(case, admission)
    existing = SimpleNamespace(owner=item, authority=case.authority)
    monkeypatch.setattr(capture, '_REGISTRY', {str(case.data / 'jobs.sqlite3'): existing})
    store = SimpleNamespace(settings=case.settings, path=case.data / 'jobs.sqlite3')
    for other in (None, load(case)):
        with pytest.raises(Unavailable, match='registry_mismatch'):
            capture.register(store, instance_lock=case.lock, root_reattestation=other)
    with pytest.raises(Unavailable, match='registry_lock_mismatch'):
        capture.register(store, instance_lock=None, root_reattestation=admission)
    assert capture.register(store, instance_lock=case.lock, root_reattestation=admission) is existing


def test_successful_preflight_then_factory_failure_closes_only_child(case, monkeypatch):
    from audio_backend import app
    seen = []
    def fail_after_preflight(settings, *args, inherited_lock=None, root_reattestation=None, **kwargs):
        inherited_lock.verify_inherited(case.control)
        assert type(root_reattestation) is attest.RootReattestation
        seen.append(inherited_lock)
        raise RuntimeError('Synthetic post-preflight failure')
    monkeypatch.setattr(app, '_create_app', fail_after_preflight)
    incoming = os.dup(case.supervisor.fileno())
    with pytest.raises(RuntimeError, match='post-preflight'):
        app.create_app(case.settings, instance_lock_fd=incoming,
            native_root_reattestation=case.artifact,
            native_root_reattestation_sha256=sha(case.artifact.read_bytes()))
    assert len(seen) == 1 and seen[0].file is None
    with pytest.raises(OSError):os.fstat(incoming)
    case.lock.verify_inherited(case.control)


def test_cli_incomplete_artifact_pair_consumes_adopted_fd(case):
    from audio_backend import __main__ as cli
    incoming = os.dup(case.supervisor.fileno())
    with pytest.raises(SystemExit) as error:
        cli.main(['--data-dir', str(case.data), '--instance-lock-fd', str(incoming),
                  '--native-root-reattestation', str(case.artifact)])
    assert error.value.code == 2
    with pytest.raises(OSError):os.fstat(incoming)
    case.lock.verify_inherited(case.control)


def test_ordinary_new_store_still_works_without_opt_in(case):
    case.path.unlink()
    with opened(case) as item:
        assert item.retention_store_key != case.value['old_binding_sha256']
        assert item._root_reattestation_diagnostic is None
        assert item._ancestor_identity == case.chain



def test_two_direct_opens_preserve_bounded_proof_and_gc_store_authority(case, monkeypatch):
    # Reuse the already reviewed finite four-root producer-shaped fixture.
    # Its source authority is synthetic; no real input is read or regenerated.
    import test_bounded_support_roots as roots
    from audio_backend import moss_automatic as automatic
    from audio_backend import native_owned_core_seams as core
    key = case.value['old_binding_sha256']
    monkeypatch.setattr(roots, 'STORE_KEY', key)
    with case.primary_store.db() as db:
        core.init_schema(db)
        for sid in (roots.SID, roots.OTHER):
            db.execute('INSERT INTO sessions(id,name,identity_group,created,timeline_end) VALUES(?,?,?,?,120.)',
                       (sid, 'synthetic roots', sid, 1.))
        roots.migrate(db); bound = roots.seed_bound(db)
    cell = bound.cell; db = sqlite3.connect(case.path)
    db.execute('INSERT INTO cells(cell_id,ownership,sid,cid,proof,pcm,state) VALUES(?,?,?,?,?,?,?)',
        (cell.cell_id, cell.ownership, cell.session_id, cell.chunk_id, cell.proof, cell.pcm, 'retained'))
    for position, values in enumerate(cells.CellStore._dependency_values(bound.doc, cell.parts)):
        db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)', (cell.cell_id, position, *values))
    db.commit(); db.close()
    before = snapshot(case.path); native_bytes = case.path.read_bytes()
    tables = (automatic.TABLE, automatic.DEPENDENCIES, 'native_core_commits', 'recording_batches')
    with case.primary_store.db() as db:
        primary_before = {name: roots.rows(db, name) for name in tables}
    for _ in range(2):
        with opened(case, load(case)) as item:
            assert item.retention_store_key == key
            assert item._document(cell.cell_id, cells.Budget())[2] == cell.proof
            current_key = item._root_reattestation_diagnostic['current_binding_sha256']
            assert current_key != key
            with case.primary_store.db() as db:
                assert automatic.qualify_bounded_document(db, bound.doc, cells.Budget(), item.retention_store_key)
                assert not automatic.qualify_bounded_document(db, bound.doc, cells.Budget(), current_key)
                assert {name: roots.rows(db, name) for name in tables} == primary_before
                memory = sqlite3.connect(':memory:'); memory.row_factory = sqlite3.Row
                try:
                    memory.execute('PRAGMA foreign_keys=ON')
                    db.backup(memory)
                    assert memory.execute('PRAGMA foreign_keys').fetchone()[0] == 1
                    assert memory.execute('SELECT COUNT(*) FROM ' + automatic.DEPENDENCIES).fetchone()[0] > 0
                    automatic.erase_session(memory, roots.SID)
                    assert memory.execute('SELECT COUNT(*) FROM ' + automatic.DEPENDENCIES).fetchone()[0] == 0
                    slot = core.slot(bound.case['commits'][2])
                    assert automatic.retention_eligible(memory, item.retention_store_key, slot=slot)
                    assert not automatic.retention_eligible(memory, current_key, slot=slot)
                finally:memory.close()
        assert snapshot(case.path) == before
        assert case.path.read_bytes() == native_bytes


def test_cli_factory_real_native_owner_two_lifespans_without_listener_or_models(case, monkeypatch):
    import asyncio
    import uvicorn
    from audio_backend import __main__ as cli
    from audio_backend import app as api
    from audio_backend import native_owned_cell_capture as capture
    monkeypatch.setattr(capture, 'qualified_runtime', lambda _: case.profile)
    starts = []; completed = []
    # Only inference and listener startup are disabled. CLI parsing, fd adoption,
    # artifact loading, factory, primary Store, registry, native owner thread and
    # lifecycle cleanup are the actual production code.
    monkeypatch.setattr(api.Worker, 'start', lambda self: starts.append(self))
    def no_listener(app, **kwargs):
        async def lifecycle():
            async with app.router.lifespan_context(app):
                native = app.state.worker.native_owned_cells.owner
                assert native._ready.wait(3.), native.health()
                assert native.retention_store_key == case.value['old_binding_sha256']
                assert native._root_reattestation_diagnostic['change'] == 'root_device_only'
                assert native.instance_lock is app.state.instance_lock
                native.instance_lock.verify_inherited(case.control)
                completed.append(native)
        asyncio.run(lifecycle())
        assert app.state.instance_lock_cleanup_complete
        assert app.state.instance_lock.file is None
        assert completed[-1]._thread is not None and not completed[-1]._thread.is_alive()
    monkeypatch.setattr(uvicorn, 'run', no_listener)
    native_before = snapshot(case.path)
    for _ in range(2):
        cli.main(['--data-dir', str(case.data), '--model-dir', str(case.root / 'absent-models'),
            '--instance-lock-fd', str(os.dup(case.supervisor.fileno())),
            '--native-root-reattestation', str(case.artifact),
            '--native-root-reattestation-sha256', sha(case.artifact.read_bytes()),
            '--diarization-backend', 'nemotron', '--native-diarization-model', str(case.root / 'absent.onnx'),
            '--native-diarization-library', str(case.root / 'absent.so'),
            '--native-owned-support', '--native-owned-cells', '--native-owned-core-seams',
            '--native-owned-bounded-core-support', '--lexical-evidence',
            '--moss-jobs', '--moss-jobs-model', str(case.root / 'absent.gguf'), '--moss-automatic'])
        assert snapshot(case.path) == native_before
        assert str(case.data / 'jobs.sqlite3') not in capture._REGISTRY
        case.lock.verify_inherited(case.control)
    assert len(starts) == len(completed) == 2
    assert completed[0] is not completed[1]
