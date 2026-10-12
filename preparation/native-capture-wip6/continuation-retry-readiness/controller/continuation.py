"""Two deleted-source restarts on a bounded clone. Never uploads or starts work."""
import hashlib
import json
import importlib.util
import os
from pathlib import Path
import sqlite3
import stat
import time
from contextlib import closing
from common import require, bounded_read, canonical, sha, atomic_json
from capture import Server, no_new_calls, assert_primary_equal, verify_inventory, verify_provider_counts
from bootstrap import accounting_check, verify_directory_aliases


def admission(spec, check):
    from outer_ledger import admission as counted_admission
    return counted_admission(spec, check)


def clone_database(contract, target, check):
    expected = ('jobs.sqlite3', 'jobs.sqlite3-wal', 'jobs.sqlite3-shm')
    rows = contract['clone_files']
    require(type(rows) is list and tuple(Path(x['path']).name for x in rows) == expected and
            sum(x['bytes'] for x in rows) == 934880, 'fixed_closed_clone_shape')
    target = Path(target)
    require(not target.exists() and not target.is_symlink(), 'clone_target_exists')
    target.mkdir(mode=0o700)
    copied = []
    identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink,
                          s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    for pin in rows:
        check()
        source = Path(pin['path'])
        require(source.resolve(strict=True) == source and source.parent == Path(contract['original_data_dir']),
                'clone_source_scope')
        before = source.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                before.st_uid == os.geteuid() and before.st_size == pin['bytes'] <= 768*1024,
                'clone_source_shape')
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        out = None
        try:
            require(identity(before) == identity(os.fstat(fd)), 'clone_source_open_race')
            out = os.open(target/source.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            digest, count = hashlib.sha256(), 0
            while True:
                check()
                block = os.read(fd, 16384)
                if not block:
                    break
                count += len(block)
                require(count <= pin['bytes'], 'clone_source_growth')
                digest.update(block)
                pending = memoryview(block)
                while pending:
                    check()
                    written = os.write(out, pending)
                    require(written > 0, 'clone_write_failed')
                    pending = pending[written:]
            os.fsync(out)
            require(count == pin['bytes'] and digest.hexdigest() == pin['sha256'] and
                    identity(before) == identity(os.fstat(fd)) == identity(source.lstat()), 'clone_source_pin')
            copied.append(dict(pin, destination=str(target/source.name)))
        finally:
            if out is not None:
                os.close(out)
            os.close(fd)
    (target/'uploads').mkdir(mode=0o700)
    (target/'tmp').mkdir(mode=0o700)
    return copied


def assert_deleted_clone(path, check):
    # Open only the fresh clone; close even on rejection. No original DB SQLite open.
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=1)) as db:
        db.set_progress_handler(lambda: (check(), 0)[1], 1000)
        for table in ('chunks', 'session_file_cleanup'):
            require(db.execute('SELECT count(*) FROM '+table).fetchone()[0] == 0,
                    'clone_has_extant_source_or_pending_file_cleanup')


def require_server_observation(spec, pid, check):
    path = Path(spec['output_dir'])/f'server-{pid}-terminal.json'
    raw = bounded_read(path, 32*1024, check)
    row = json.loads(raw)
    require(row.get('status') == 'COMPLETE' and row.get('pid') == pid and
            row.get('mode') == 'server' and row.get('spec_sha256') == spec['_sha256'] and
            row.get('cleanup') == 'CONFIRMED' and row.get('failures') == [] and
            row.get('mappings', {}).get('status') == 'PATHS_OBSERVED_NOT_REHASHED',
            'required_new_server_mapping_observation')
    return {'path': str(path), 'sha256': sha(raw), 'bytes': len(raw)}


def run_controller(spec, deadlines, journal, ledger):
    contract = spec['_continuation']
    output = Path(spec['output_dir'])
    require(deadlines.work-deadlines.clock() > 0 and not (output/'restart-on').exists() and
            not (output/'restart-off').exists(), 'fresh_continuation_required')
    atomic_json(output/'admission.json', admission(spec, deadlines.check), 8192)
    copied = clone_database(contract, spec['data_dir'], deadlines.check)
    atomic_json(output/'clone.json', {'files': copied, 'originals_modified': False}, 8192)
    assert_deleted_clone(Path(spec['data_dir'])/'jobs.sqlite3', deadlines.check)
    old = Path(contract['original_output_dir'])
    upload = json.loads(bounded_read(old/'source-upload.json', 8192, deadlines.check))
    page = json.loads(bounded_read(old/'public-list.json', 8192, deadlines.check))
    sid, cid = upload['session']['id'], upload['upload']['chunk']['id']
    require(all(type(x) is str and len(x) == 32 and all(c in '0123456789abcdef' for c in x)
                for x in (sid, cid)), 'sealed_original_identifiers')
    counts = verify_provider_counts(dict(spec, output_dir=str(old)), deadlines)
    baseline = None
    results = []
    for mode in ('restart-on', 'restart-off'):
        deadlines.check()
        verify_directory_aliases(spec['runtime_directory_aliases'], deadlines.check, require)
        server = Server(spec, deadlines, journal, ledger, mode)
        no_new_calls(server.ready)
        state = server.ready['cache']
        if mode == 'restart-on':
            require(state['present'] and state['entries'] == 0 and state['epoch'] != page['capture_epoch'],
                    'fresh_empty_restart_epoch')
        else:
            require(state['present'] is False, 'off_restart_cache_registered')
        before = server.command('snapshot')
        no_new_calls(before)
        if baseline is None:
            baseline = before
        else:
            assert_primary_equal(baseline, before)
        server.client.request('GET', f'/sessions/{sid}/native-owned-supports/{cid}',
                              expected=404 if mode == 'restart-on' else 409, private=True)
        server.client.request('GET', f'/sessions/{sid}', expected=404)
        after = server.command('snapshot')
        no_new_calls(after)
        assert_primary_equal(before, after)
        final = server.stop_and_prove()
        no_new_calls(final)
        terminal = require_server_observation(spec, server.proc.pid, deadlines.check)
        require(server.client.calls == 2, 'two_read_only_requests_per_restart')
        results.append({'mode': mode, 'new_provider_calls': 0, 'new_provider_launches': 0,
                        'original_waited': True, 'snapshot': after['snapshot'], 'terminal': terminal})
        atomic_json(output/(mode+'-proof.json'), final, 8192)
        admission(spec, deadlines.check)
    end = min(deadlines.posthash, time.monotonic()+120)
    check = lambda: accounting_check(deadlines, end)
    posthash = verify_inventory(spec, deadlines, end=end)
    verify_directory_aliases(spec['runtime_directory_aliases'], check, require)
    admission(spec, check)
    atomic_json(output/'posthash.json', posthash, 8192)
    atomic_json(output/'continuation-result.json', {
        'status': 'COMPLETE_ZERO_INFERENCE_CONTINUATION', 'original_capture_status': 'INCOMPLETE',
        'original_server_mappings': 'UNOBSERVED_NOT_RETROACTIVELY_RECOVERED',
        'original_seal': contract['original_seal'], 'independent_review': contract['independent_review'],
        'original_inventory_sha256': contract['original_inventory_sha256'],
        'retained_original_provider_counts_sha256': sha(canonical(counts)),
        'restarts': results, 'model_calls': 0, 'provider_constructors': 0, 'uploads': 0,
        'public_requests': 4, 'posthash': posthash,
        'scope': 'Read-only HTTP on a fresh clone; normal SQLite startup writes affect only the clone.'}, 16384)
