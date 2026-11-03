"""Finite synthetic physical evidence only. Never activates a production profile."""
import argparse
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import sqlite3
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audio_backend.native_owned_cell_store import runtime_fingerprints, Budget, QUALIFIED_RUNTIMES

PAGE = 4096
MAX_DB = 24 * 1024**2
MAX_JOURNAL = 32 * 1024**2
TARGET_LIMIT = 58 * 1024**2
ARTIFACT_LIMIT = 256 * 1024**2
MAGIC = bytes.fromhex('d9d505f920a163d7')
ROWS = 6000
PAYLOAD = b'A' * 4000
EXPECTED_PACKAGE = '6ac9a15ee2c393bd994e6b3784ed3f5fb97c9b4f986cbce03dccbad6226778df'


def sha(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    with path.open('x') as out:
        json.dump(value, out, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())


def bounded_read(path, limit):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError('file_bound')
    return data


def identity(directory):
    fingerprints = runtime_fingerprints(Budget())
    active = asdict(QUALIFIED_RUNTIMES[0])
    db = sqlite3.connect(':memory:')
    try:
        source = db.execute('SELECT sqlite_source_id()').fetchone()[0]
        options = sha(json.dumps(sorted(r[0] for r in db.execute('PRAGMA compile_options')),
                                separators=(',', ':'), ensure_ascii=False).encode())
    finally:
        db.close()
    # No candidate RuntimeQualification object, patched selector or verifier.
    for key, value in fingerprints.items():
        expected = EXPECTED_PACKAGE if key == 'sqlite3_package_sha256' else active[key]
        if value != expected:
            raise RuntimeError('unexpected_runtime_field:' + key)
    if (source != active['sqlite_source_id'] or sqlite3.sqlite_version != active['sqlite_version']
            or options != active['compile_options_sha256'] or directory.stat().st_dev != active['filesystem_device']):
        raise RuntimeError('unexpected_sqlite_platform_profile')
    package = Path(sqlite3.__file__).resolve().parent
    leaves = sorted(package.glob('*.py')) + sorted((package/'__pycache__').glob('*.pyc'))
    if len(leaves)>16:raise RuntimeError('package_inventory_bound')
    inventory = {str(p.relative_to(package)):{'bytes':p.stat().st_size,
                 'sha256':sha(bounded_read(p,256*1024))} for p in leaves}
    return dict(package_inventory=inventory,fingerprints=fingerprints, sqlite_source_id=source, sqlite_version=sqlite3.sqlite_version,
                compile_options_sha256=options, filesystem_device=directory.stat().st_dev,
                production_profile_activated=False)


def connect(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?vfs=unix', uri=True, timeout=0, isolation_level=None)
    for statement in ('journal_mode=DELETE', 'page_size=4096', 'synchronous=FULL', 'temp_store=MEMORY',
                      'busy_timeout=0', 'cache_spill=OFF', 'cache_size=-4096', 'mmap_size=0',
                      'foreign_keys=ON', 'trusted_schema=OFF', 'max_page_count=6144'):
        db.execute('PRAGMA ' + statement)
    expected = dict(journal_mode='delete', page_size=4096, synchronous=2, temp_store=2,
                    busy_timeout=0, cache_spill=0, cache_size=-4096, mmap_size=0,
                    foreign_keys=1, trusted_schema=0, max_page_count=6144)
    for key, value in expected.items():
        if db.execute('PRAGMA ' + key).fetchone()[0] != value:
            raise RuntimeError('pragma:' + key)
    return db


def child(phase, directory, parent_pid):
    # A stopped child must not outlive a killed supervisor.
    import ctypes
    if os.getppid() != parent_pid:raise RuntimeError('supervisor_already_gone')
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'parent_death_signal')
    if os.getppid() != parent_pid:raise RuntimeError('supervisor_changed')
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (25, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_JOURNAL, MAX_JOURNAL))
    signal.alarm(45)
    current = identity(directory)
    path = directory / 'cells.sqlite3'
    db = connect(path)
    try:
        if phase == 'fill':
            db.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY, payload BLOB NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            db.executemany('INSERT INTO fixture VALUES (?,?)', ((n, PAYLOAD) for n in range(ROWS)))
            db.execute('COMMIT')
            rows = ROWS
            while rows < 6144:
                try:
                    db.execute('INSERT INTO fixture VALUES (?,?)', (rows, PAYLOAD))
                except sqlite3.OperationalError as exc:
                    if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_FULL:
                        raise
                    break
                rows += 1
            else:
                raise RuntimeError('expected_sqlite_full_not_observed')
            pages = db.execute('PRAGMA page_count').fetchone()[0]
            if pages != 6144 or db.in_transaction:
                raise RuntimeError('full_page_ceiling_or_transaction_state')
            save(directory / 'expected_rows.json', {'rows': rows, 'pages': pages, 'sqlite_full': True})
        elif phase == 'crash':
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE fixture SET payload=?', (b'B' * 4000,))
            save(directory / 'before_commit.json', {'identity': current, 'stage': 'before_commit'})
            os.kill(os.getpid(), signal.SIGSTOP)
            db.execute('COMMIT')
            save(directory / 'missed_commit.json', {'committed': True})
        elif phase == 'recover':
            if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise RuntimeError('integrity_check')
            row = db.execute('SELECT count(*),min(id),max(id),sum(payload!=?) FROM fixture', (PAYLOAD,)).fetchone()
            rows = json.loads((directory/'expected_rows.json').read_text())['rows']
            if row != (rows, 0, rows - 1, 0):
                raise RuntimeError('recovered_rows')
        else:
            raise ValueError('phase')
    finally:
        db.close()
    save(directory / (phase + '.json'), {'closed': True, 'identity': current})


def audit(baseline, database, journal):
    if not 0 < len(baseline) == len(database) <= MAX_DB or len(baseline) % PAGE:
        raise ValueError('database_size')
    if not 28 <= len(journal) <= MAX_JOURNAL or journal[:8] != MAGIC:
        raise ValueError('journal_header')
    count, nonce, pages, sector, size = struct.unpack('>5I', journal[8:28])
    if not (1 <= count <= 6144 and 1 <= pages <= 6144 and size == PAGE
            and 512 <= sector <= 65536 and sector & (sector - 1) == 0
            and pages * PAGE == len(baseline)):
        raise ValueError('journal_geometry')
    end = sector + count * (PAGE + 8)
    if end > len(journal) or len(journal)-end >= sector or any(journal[28:sector]) or any(journal[end:]):
        raise ValueError('unsupported_journal_tail_or_headers')
    restored = bytearray(database)
    seen = set()
    for offset in range(sector, end, PAGE + 8):
        number = struct.unpack('>I', journal[offset:offset+4])[0]
        page = journal[offset+4:offset+4+PAGE]
        check = struct.unpack('>I', journal[offset+4+PAGE:offset+8+PAGE])[0]
        if not 1 <= number <= pages or number in seen:
            raise ValueError('journal_page_identity')
        if (nonce + sum(page[index] for index in range(PAGE-200, -1, -200))) & 0xffffffff != check:
            raise ValueError('journal_checksum')
        seen.add(number)
        if page != baseline[(number-1)*PAGE:number*PAGE]:
            raise ValueError('journal_not_original_page')
        restored[(number-1)*PAGE:number*PAGE] = page
    changed = sum(baseline[i:i+PAGE] != database[i:i+PAGE] for i in range(0, len(baseline), PAGE))
    if not changed or restored != baseline:
        raise ValueError('no_meaningful_reconstructable_crash')
    return dict(changed_pages=changed, journal_records=count, journal_headers=1, sector_bytes=sector,
                baseline_sha256=sha(baseline), database_sha256=sha(database), journal_sha256=sha(journal),
                reconstructed_sha256=sha(restored))


def allocation(directory):
    files = list(directory.iterdir())
    target = [p for p in files if p.name.startswith('cells.sqlite3')]
    if any(p.name not in ('cells.sqlite3', 'cells.sqlite3-journal') for p in target):
        raise RuntimeError('unexpected_database_artifact')
    logical = sum(p.stat().st_size for p in target)
    charged = sum(p.stat().st_blocks * 512 for p in target)
    if charged > TARGET_LIMIT or max(0, charged-logical) > 2 * 1024**2:
        raise RuntimeError('target_allocation_bound')
    return dict(charged_bytes=charged, logical_bytes=logical, slack_bytes=max(0, charged-logical))


def supervise(out):
    out.mkdir(mode=0o700, exist_ok=False)
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    available = sorted(os.sched_getaffinity(0))
    if len(available) < 2:raise RuntimeError('two_cpu_unavailable')
    os.sched_setaffinity(0, set(available[:2]))
    end = time.monotonic() + 240
    active = None
    def interrupted(signum, frame):raise InterruptedError('supervisor_signal:' + str(signum))
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,interrupted)
    waits = []
    result = {'schema': 'finite-sqlite-crash-probe-v1', 'passed': False,
              'production_profile_activated': False, 'power_loss_qualified': False}
    baseline_dir = out / 'baseline'
    baseline_dir.mkdir()
    script_hash = sha(Path(__file__).read_bytes())
    source_hash = sha((ROOT / 'audio_backend/native_owned_cell_store.py').read_bytes())
    def manifest():
        return {str(p.relative_to(ROOT)):sha(p.read_bytes()) for directory in
                (ROOT/'audio_backend',ROOT/'qualification') for p in sorted(directory.glob('*.py'))}
    pins = manifest()
    peak_target = 0
    def check():
        nonlocal peak_target
        if time.monotonic() >= end:
            raise TimeoutError('probe_deadline')
        charged = 0
        for p in out.rglob('*'):
            try:
                if p.is_file():charged += p.stat().st_blocks*512
            except FileNotFoundError:
                pass  # Concurrent journal removal; not a stable-allocation proof.
        for directory in out.iterdir():
            if directory.is_dir():
                try:peak_target = max(peak_target, allocation(directory)['charged_bytes'])
                except FileNotFoundError:pass
        if charged > ARTIFACT_LIMIT:
            raise RuntimeError('artifact_capacity')
    def launch(phase, directory):
        nonlocal active
        check()
        oldmask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            with (directory / (phase+'.stderr')).open('xb') as stderr:
                active = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                    '--child', phase, '--parent-pid', str(os.getpid()), str(directory)],
                    stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, oldmask)
        return active
    def waited(proc, phase, expected):
        until = min(end,time.monotonic()+45)
        while proc.poll() is None:
            check()
            if time.monotonic() >= until:raise TimeoutError('child_wait_deadline')
            time.sleep(.001)
        code = proc.wait()
        waits.append(dict(pid=proc.pid, phase=phase, returncode=code, original_wait=True))
        if code != expected:
            raise RuntimeError('child_exit:' + phase + ':' + str(code))
    def stopped(proc):
        until = min(end, time.monotonic()+3)
        while time.monotonic() < until:
            pid, status = os.waitpid(proc.pid, os.WNOHANG | os.WUNTRACED)
            if pid:
                if os.WIFSTOPPED(status) and os.WSTOPSIG(status) == signal.SIGSTOP:
                    return
                # Preserve consumed terminal status for Popen's later original wait.
                if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                    proc.returncode = os.waitstatus_to_exitcode(status)
                    raise RuntimeError('child_exited_before_stop')
            time.sleep(.001)
        raise TimeoutError('stop_ack_deadline')
    try:
        result['identity'] = identity(out)
        waited(launch('fill', baseline_dir), 'fill', 0)
        baseline = bounded_read(baseline_dir/'cells.sqlite3', MAX_DB)
        result['fill_allocation'] = allocation(baseline_dir)
        expected_rows = (baseline_dir/'expected_rows.json').read_bytes()
        result['full_capacity'] = json.loads(expected_rows)
        captured = None
        for attempt in range(1, 5):
            directory = out / ('attempt-' + str(attempt)); directory.mkdir()
            with (directory/'cells.sqlite3').open('xb') as stream:
                stream.write(baseline);stream.flush();os.fsync(stream.fileno())
            (directory/'expected_rows.json').write_bytes(expected_rows)
            proc = launch('crash', directory)
            while not (directory/'before_commit.json').exists():
                check()
                if proc.poll() is not None:raise RuntimeError('crash_child_early_exit')
                time.sleep(.001)
            stopped(proc)
            for sample in range(200):
                check()
                os.kill(proc.pid, signal.SIGCONT)
                time.sleep(.0002)
                if proc.poll() is not None:break
                os.kill(proc.pid, signal.SIGSTOP)
                stopped(proc)
                journal_path = directory/'cells.sqlite3-journal'
                if not journal_path.exists():continue
                with journal_path.open('rb') as stream:header = stream.read(28)
                with (directory/'cells.sqlite3').open('rb') as stream:prefix = stream.read(65536)
                if header[:8] != MAGIC or prefix == baseline[:65536]:continue
                database = bounded_read(directory/'cells.sqlite3', MAX_DB)
                journal = bounded_read(journal_path, MAX_JOURNAL)
                observation = audit(baseline, database, journal)
                result['crash_allocation'] = allocation(directory)
                (out/'captured-database.bin').write_bytes(database)
                (out/'captured-journal.bin').write_bytes(journal)
                result['audit'] = observation
                result['capture'] = {'attempt':attempt,'sample':sample,'stop_acknowledged':True}
                os.kill(proc.pid, signal.SIGKILL)
                waited(proc, 'crash-captured', -signal.SIGKILL)
                captured = directory
                break
            if captured:break
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGCONT)
            waited(proc, 'missed-crash-attempt', 0)
        if captured is None:raise RuntimeError('no_valid_crash_captured')
        recovery = out/'recovery';recovery.mkdir()
        (recovery/'cells.sqlite3').write_bytes(database)
        (recovery/'cells.sqlite3-journal').write_bytes(journal)
        (recovery/'expected_rows.json').write_bytes(expected_rows)
        check()
        waited(launch('recover', recovery), 'recover', 0)
        recovered = bounded_read(recovery/'cells.sqlite3', MAX_DB)
        if recovered != baseline or (recovery/'cells.sqlite3-journal').exists():
            raise RuntimeError('recovery_not_exact')
        result['recovery_allocation'] = allocation(recovery)
        if bounded_read(captured/'cells.sqlite3', MAX_DB) != database or bounded_read(captured/'cells.sqlite3-journal', MAX_JOURNAL) != journal:
            raise RuntimeError('captured_pair_changed')
        result['identity_after'] = identity(out)
        if result['identity_after'] != result['identity']:raise RuntimeError('runtime_changed')
        if script_hash != sha(Path(__file__).read_bytes()) or source_hash != sha((ROOT/'audio_backend/native_owned_cell_store.py').read_bytes()):
            raise RuntimeError('source_changed')
        check()
        if manifest()!=pins:raise RuntimeError('source_manifest_changed')
        result['passed'] = True
    except BaseException as exc:
        result['passed'] = False
        result['error'] = {'type':type(exc).__name__,'message':str(exc)[:2048]}
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        if active is not None and active.poll() is None:
            os.kill(active.pid, signal.SIGKILL)
            code = active.wait(timeout=5)
            waits.append(dict(pid=active.pid, phase='failed-cleanup', returncode=code, original_wait=True))
        result.update(original_waits=waits, harness_sha256=script_hash, source_module_sha256=source_hash,
                      source_manifest=pins,peak_sampled_target_bytes=peak_target,
                      allocation_observation='sampled_not_instantaneous_or_power_loss')
        save(out/'RESULT.json', result)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--child', choices=['fill','crash','recover'])
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.child:
        child(args.child, args.output.resolve(), args.parent_pid)
    else:
        raise SystemExit(supervise(args.output.resolve()))
