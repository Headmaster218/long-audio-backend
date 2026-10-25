"""Root-executed independent run02 audit. Author performs source review only.

No product imports. Original databases are read as bytes only. SQLite is opened
only in memory or against a newly created ordinary recovery copy.
"""
import argparse
import base64
import hashlib
import json
import os
import resource
import shutil
import signal
import sqlite3
import stat
import struct
import sys
import time
from pathlib import Path

PAGE, PAGES = 4096, 6144
DB_BYTES, JOURNAL_MAX = PAGE * PAGES, 32 * 1024**2
ARTIFACT_MAX, RSS_MAX = 192 * 1024**2, 256 * 1024**2
PREP_SHA = 'c3b8192aec3330b56549e9a5853fa21192d83fff780c598f75f21dfe14254497'
GATE_SHA = 'f6fce6bfff8fd011740890f554e023f95dca5d83461e5ae16b00304b8aaf46c1'
PINS_SHA = 'c18fa0049e696913c6ed378aa825510b78dcace4f74ec7ff83c48424916a48e2'
SOURCE_SHA = '168d5af6727b27ae3a85127893c994f8533b659ac0c02bdc87908a65c475af3f'
TABLES = ('sqlite_master', 'store_meta', 'sources', 'cells', 'dependencies', 'sqlite_sequence')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


class Bounds:
    def __init__(self, evidence, output):
        self.evidence, self.output = evidence, output
        self.deadline = time.monotonic() + 120
        self.peak_rss = self.peak_charge = self.samples = 0

    def check(self):
        require(time.monotonic() < self.deadline, 'audit deadline')
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                self.peak_rss = max(self.peak_rss, int(line.split()[1]) * 1024)
        self.peak_charge = max(self.peak_charge, charge(self.evidence) + charge(self.output))
        self.samples += 1
        require(self.peak_rss <= RSS_MAX, 'sampled audit-process RSS')
        require(self.peak_charge <= ARTIFACT_MAX - 65536, 'original run02 plus new audit artifact reserve')
        fs = os.statvfs(self.output)
        require(fs.f_bavail * fs.f_frsize >= 256 * 1024**2, 'free disk floor')


def regular(path, maximum=32 * 1024**2):
    info = path.lstat()
    # Read-only runtime inputs are legitimately owned by the runtime publisher,
    # not this workspace user. Exact hashes establish their expected contents.
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe input file')
    require(info.st_size <= maximum, 'input extent')
    return info


def charge(root):
    total = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        for path in [Path(folder)] + [Path(folder) / name for name in files]:
            info = path.lstat()
            require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), 'unexpected artifact type')
            total += max(info.st_size, info.st_blocks * 512)
        for name in dirs:
            require(not (Path(folder) / name).is_symlink(), 'artifact directory alias')
    return total


def hash_file(path, bounds=None, maximum=32 * 1024**2):
    info = regular(path, maximum)
    signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            result.update(block)
            if bounds:
                bounds.check()
    now = path.lstat()
    require(signature == (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns, now.st_ctime_ns), 'file changed during hashing')
    return result.hexdigest()


def read_json(path):
    regular(path, 1024**2)
    return json.loads(path.read_bytes())


def inventory(root, bounds):
    answer = {}
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            require(not (Path(folder) / name).is_symlink(), 'input directory alias')
        for name in files:
            path = Path(folder) / name
            answer[str(path.relative_to(root))] = {'bytes': regular(path).st_size, 'sha256': hash_file(path, bounds)}
    return answer


def verify_sources(preparation, pins, bounds):
    require(hash_file(preparation / 'SHA256SUMS', bounds) == PREP_SHA, 'preparation manifest changed')
    preparation_files = {}
    for line in (preparation / 'SHA256SUMS').read_text().splitlines():
        digest, relative = line.split('  ', 1)
        relative = Path(relative)
        require(relative.parts[0] == preparation.name and len(relative.parts) == 2, 'preparation manifest path')
        require(hash_file(preparation.parent / relative, bounds) == digest, 'prepared input mismatch')
        preparation_files[str(relative)] = digest
    require(len(preparation_files) == 8, 'preparation inventory count')
    require(hash_file(preparation / 'gate.py', bounds) == GATE_SHA, 'gate drift')
    require(hash_file(preparation / 'pins.json', bounds) == PINS_SHA, 'pins drift')
    require(hash_file(preparation / 'product.sha256', bounds) == SOURCE_SHA, 'source manifest drift')
    source = Path(pins['source'])
    require(source.is_absolute() and source.resolve() == source, 'source path alias')
    expected = {}
    for line in (preparation / 'product.sha256').read_text().splitlines():
        digest, name = line.split('  ', 1)
        require(name.startswith('./') and '..' not in Path(name).parts, 'source manifest path')
        require(name[2:] not in expected, 'duplicate source path')
        expected[name[2:]] = digest
    actual = {}
    for folder, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [name for name in dirs if name != '.git']
        for name in dirs:
            require(not (Path(folder) / name).is_symlink(), 'source directory alias')
        for name in files:
            require(not name.endswith(('.pyc', '.pyo')), 'unexpected product bytecode')
            path = Path(folder) / name
            actual[str(path.relative_to(source))] = hash_file(path, bounds, 16 * 1024**2)
    require(actual == expected and len(actual) == 943, 'complete product inventory mismatch')
    return {'files': len(actual), 'manifest_sha256': SOURCE_SHA, 'preparation_manifest_sha256': PREP_SHA}


def verify_runtime(pins, recorded, bounds):
    import _sqlite3
    from importlib.machinery import BuiltinImporter
    executable = Path(sys.executable).resolve()
    require(str(executable) == pins['python'], 'interpreter path')
    require(executable.stat().st_size == pins['python_bytes'], 'interpreter size')
    interpreter_hash = hash_file(executable, bounds)
    require(interpreter_hash == pins['python_sha256'], 'interpreter bytes')
    process_binary = os.stat('/proc/self/exe')
    binary = executable.stat()
    require((process_binary.st_dev, process_binary.st_ino) == (binary.st_dev, binary.st_ino), 'executed interpreter inode')
    require(_sqlite3.__spec__.origin == 'built-in' and _sqlite3.__spec__.loader is BuiltinImporter and getattr(_sqlite3, '__file__', None) is None, 'SQLite binding form')
    maps = Path('/proc/self/maps').read_bytes()
    require(len(maps) <= 256 * 1024 and b'libsqlite3' not in maps, 'SQLite mapping form')
    package = Path(sqlite3.__file__).resolve().parent
    require(Path(sqlite3.dbapi2.__file__).resolve() == package / 'dbapi2.py', 'SQLite package origin')
    current = {}
    for directory, pattern in ((package, '*.py'), (package / '__pycache__', '*.pyc')):
        if directory.exists():
            require(not directory.is_symlink(), 'SQLite package directory alias')
            for path in sorted(directory.glob(pattern)):
                current[str(path.relative_to(package))] = {'bytes': regular(path, 256 * 1024).st_size, 'sha256': hash_file(path, bounds, 256 * 1024)}
    require(current == recorded['package_inventory'], 'SQLite package per-file inventory')
    package_hash = hashlib.sha256(encoded(current)).hexdigest()
    require(package_hash == pins['sqlite3_package_sha256'], 'SQLite package digest')
    fingerprints = {'interpreter_bytes': binary.st_size, 'interpreter_sha256': interpreter_hash,
                    'interpreter_realpath_sha256': hashlib.sha256(os.fsencode(executable)).hexdigest(),
                    'sqlite3_package_sha256': package_hash, 'sqlite3_binding': 'observed-built-in-no-separate-sqlite-mapping-v1',
                    'sqlite3_extension_sha256': None, 'sqlite3_library_sha256': None, 'sqlite3_library_realpath_sha256': None}
    require(fingerprints == recorded['fingerprints'], 'independent runtime fingerprint comparison')
    db = sqlite3.connect(':memory:')
    try:
        source_id = db.execute('SELECT sqlite_source_id()').fetchone()[0]
        options = sorted(row[0] for row in db.execute('PRAGMA compile_options'))
    finally:
        db.close()
    require(source_id == pins['sqlite_source_id'] and sqlite3.sqlite_version == pins['sqlite_version'], 'SQLite build identity')
    require(len(options) <= 512 and hashlib.sha256(encoded(options)).hexdigest() == pins['compile_options_sha256'], 'SQLite compile options')
    require(bounds.evidence.stat().st_dev == bounds.output.stat().st_dev == pins['filesystem_device'], 'filesystem device')
    for name in ('sqlite_version', 'sqlite_source_id', 'compile_options_sha256', 'filesystem_device'):
        require(recorded[name] == pins[name], 'recorded runtime identity mismatch')
    require(recorded['source_module_sha256'] == hash_file(Path(pins['source']) / 'audio_backend' / 'native_owned_cell_store.py', bounds), 'recorded module identity')
    return {'sqlite_version': sqlite3.sqlite_version, 'sqlite_source_id': source_id, 'package_sha256': pins['sqlite3_package_sha256'], 'interpreter_sha256': pins['python_sha256']}


def check_journal(baseline, interrupted, journal, bounds):
    require(regular(baseline).st_size == regular(interrupted).st_size == DB_BYTES, 'database physical extent')
    size = regular(journal).st_size
    require(28 <= size <= JOURNAL_MAX, 'journal physical extent')
    seen = set()
    with journal.open('rb') as stream, baseline.open('rb') as old:
        header = stream.read(28)
        require(header[:8] == bytes.fromhex('d9d505f920a163d7'), 'journal magic')
        count, seed, original_pages, sector, page_size = struct.unpack('>5I', header[8:])
        require(1 <= count <= PAGES and original_pages == PAGES and page_size == PAGE, 'journal page bounds')
        require(512 <= sector <= 65536 and sector & (sector - 1) == 0, 'journal sector bound')
        end = sector + count * (PAGE + 8)
        require(end <= size and size - end < sector, 'single header extent')
        stream.seek(sector)
        for _ in range(count):
            record = stream.read(PAGE + 8)
            require(len(record) == PAGE + 8, 'truncated page record')
            number = int.from_bytes(record[:4], 'big')
            payload = record[4:-4]
            require(1 <= number <= PAGES and number not in seen, 'journal page number')
            seen.add(number)
            # At page size 4096 SQLite samples offsets 3896,3696,...,96.
            checksum = (seed + sum(payload[96::200])) & 0xffffffff
            require(checksum == int.from_bytes(record[-4:], 'big'), 'journal checksum')
            old.seek((number - 1) * PAGE)
            require(payload == old.read(PAGE), 'journal baseline preimage')
            if len(seen) % 64 == 0:
                bounds.check()
        require(not any(stream.read()), 'unexpected journal trailer or second header')
    changed = []
    with baseline.open('rb') as old, interrupted.open('rb') as new:
        for number in range(1, PAGES + 1):
            if old.read(PAGE) != new.read(PAGE):
                changed.append(number)
            if number % 64 == 0:
                bounds.check()
    require(changed and set(changed).issubset(seen), 'changed pages not covered by journal')
    return {'records': count, 'changed_pages': len(changed), 'headers': 1, 'sector_bytes': sector, 'journal_bytes': size,
            'database_sha256': hash_file(interrupted, bounds), 'journal_sha256': hash_file(journal, bounds),
            'changed_page_numbers': changed}


def typed_oracle(db, bounds):
    def value(item):
        if item is None:
            return ['null']
        if type(item) is bytes:
            return ['blob', base64.b64encode(item).decode('ascii')]
        if type(item) is int:
            return ['integer', str(item)]
        if type(item) is float:
            return ['real', item.hex()]
        if type(item) is str:
            return ['text', item]
        raise RuntimeError('unrecognized SQLite value type')
    answer = {}
    for table in TABLES:
        cursor = db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')
        columns = [entry[0] for entry in cursor.description]
        digest, count = hashlib.sha256(), 0
        for row in cursor:
            raw = encoded([value(item) for item in row])
            digest.update(len(raw).to_bytes(4, 'big'))
            digest.update(raw)
            count += 1
            bounds.check()
        answer[table] = {'columns': columns, 'rows': count, 'typed_rows_sha256': digest.hexdigest()}
    return answer


def new_recovery(evidence, output, fill, interrupted, journal, bounds):
    namespace = output / 'namespace'
    namespace.mkdir(mode=0o700)
    copied = namespace / 'cells.sqlite3'
    copied_journal = namespace / 'cells.sqlite3-journal'
    for original, destination in ((interrupted, copied), (journal, copied_journal)):
        shutil.copyfile(original, destination)
        require(destination.stat().st_ino != original.stat().st_ino, 'copy is not independent')
        require(hash_file(original, bounds) == hash_file(destination, bounds), 'copy bytes differ')
    bounds.check()
    db = sqlite3.connect(copied.as_uri() + '?mode=rw&vfs=unix', uri=True, timeout=0, isolation_level=None)
    try:
        require(db.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'delete', 'copy journal mode')
        settings = {'synchronous': 2, 'temp_store': 2, 'busy_timeout': 0, 'cache_spill': 0, 'cache_size': -4096,
                    'mmap_size': 0, 'foreign_keys': 1, 'trusted_schema': 0, 'max_page_count': PAGES}
        for name, expected in settings.items():
            db.execute('PRAGMA ' + name + '=' + str(expected))
            require(db.execute('PRAGMA ' + name).fetchone()[0] == expected, 'copy pragma readback')
        require(db.execute('PRAGMA page_size').fetchone()[0] == PAGE, 'copy page size')
        require(db.execute('PRAGMA page_count').fetchone()[0] == PAGES, 'copy page count')
        require(db.execute('PRAGMA auto_vacuum').fetchone()[0] == 0, 'copy auto-vacuum')
        for limit, target in ((sqlite3.SQLITE_LIMIT_LENGTH, 2359296), (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 262144), (sqlite3.SQLITE_LIMIT_ATTACHED, 0)):
            db.setlimit(limit, target)
            require(db.getlimit(limit) == target, 'copy SQLite limit')
        require(db.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'copy integrity')
        require(db.execute('PRAGMA foreign_key_check').fetchall() == [], 'copy foreign keys')
        actual = typed_oracle(db, bounds)
        require(actual == fill['oracle'], 'copy typed six-table oracle')
        for pcm, revision in db.execute('SELECT pcm,revision FROM cells ORDER BY seq'):
            require(type(pcm) is bytes and pcm == b'Z' * len(pcm) and revision == 1, 'recovered synthetic payload and revision')
            bounds.check()
    finally:
        db.close()
    require(not copied_journal.exists(), 'copy recovery journal retained')
    require(hash_file(copied, bounds) == fill['database_sha256'], 'copy recovery exact baseline bytes')
    require(hash_file(evidence / 'recovery' / 'namespace' / 'cells.sqlite3', bounds) == fill['database_sha256'], 'original recovered output bytes')
    return {'exact_baseline_bytes': True, 'typed_six_table_oracle': actual, 'integrity': 'ok', 'foreign_keys': 'ok', 'originals_opened_by_sqlite': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--preparation', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--self-sha256', required=True)
    args = parser.parse_args()
    require(sys.dont_write_bytecode, 'root must use -B')
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    os.nice(19)
    def expired(signum, frame):
        raise TimeoutError('120-second independent audit alarm')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 120)
    for path in (args.evidence, args.preparation, args.output):
        require(path.is_absolute() and path.resolve() == path, 'canonical audit paths')
    require(args.output.parent == args.evidence.parent == args.preparation.parent, 'audit sibling directories required')
    require(not args.output.exists() and not args.output.is_symlink(), 'fresh audit output required')
    require(args.output.parent.stat().st_uid == os.getuid(), 'owned audit parent required')
    require(hash_file(Path(__file__).resolve()) == args.self_sha256, 'audit source pin')
    args.output.mkdir(mode=0o700)
    bounds = Bounds(args.evidence, args.output)
    terminal = {'status': 'INCONCLUSIVE', 'production_profile_activated': False, 'originals_opened_by_sqlite': False}
    try:
        before = inventory(args.evidence, bounds)
        pins = read_json(args.preparation / 'pins.json')
        source = verify_sources(args.preparation, pins, bounds)
        gate = read_json(args.evidence / 'terminal.json')
        require(gate['status'] == 'PHYSICAL_GATE_COMPLETE_REVIEW_REQUIRED' and gate['production_profile_activated'] is False, 'gate terminal status')
        require(gate['harness_sha256'] == GATE_SHA and gate['pins_sha256'] == PINS_SHA and gate['source_manifest_sha256'] == SOURCE_SHA, 'gate provenance')
        require(gate['all_original_children_waited'] is True and gate['captured_attempt'] == 'crash-1', 'lifecycle receipt')
        require(gate['maximum_phase_seconds'] == 120 and gate['per_process_address_space'] == 1024**3, 'reported phase/process bounds')
        require(len(gate['cpu_affinity']) <= 2 and gate['peak_sampled_aggregate_rss'] <= RSS_MAX and gate['peak_charged_artifacts'] <= ARTIFACT_MAX, 'reported resource peaks')
        require(gate['peak_sampled_namespace_charge'] <= 58 * 1024**2 and gate['peak_sampled_namespace_slack'] <= 2 * 1024**2, 'reported namespace peaks')
        accounting = read_json(args.evidence / 'terminal-accounting.json')
        require(accounting['artifact_cap'] == ARTIFACT_MAX and accounting['closing_receipt_reserved_bytes'] == 65536, 'closing accounting contract')
        require(accounting['charged_bytes_including_terminal'] <= charge(args.evidence) <= accounting['charged_bytes_including_terminal'] + 65536, 'retained artifact accounting')
        waits = gate['original_waits']
        require([(entry['phase'], entry['returncode']) for entry in waits] == [('identity', 0), ('fill', 0), ('crash-captured', -9), ('recover', 0)], 'original child receipts')
        require(all(entry['original_wait'] is True for entry in waits) and len({entry['pid'] for entry in waits}) == 4, 'child wait ownership receipts')
        for phase in ('identity', 'fill', 'crash-1', 'recovery'):
            require(read_json(args.evidence / phase / 'identity.json') == gate['identity'], 'phase identity disagreement')
        runtime = verify_runtime(pins, gate['identity'], bounds)
        fill = read_json(args.evidence / 'fill' / 'fill.json')
        require(fill['page_count'] == PAGES and fill['sqlite_full_observed'] is True, 'full allocation receipt')
        require(fill['oracle']['cells']['rows'] == fill['oracle']['dependencies']['rows'] == 42 and fill['oracle']['sources']['rows'] == 128, 'synthetic workload rows')
        baseline = args.evidence / 'fill' / 'namespace' / 'cells.sqlite3'
        original = args.evidence / 'crash-1'
        interrupted = original / 'namespace' / 'cells.sqlite3'
        journal = original / 'namespace' / 'cells.sqlite3-journal'
        require(hash_file(baseline, bounds) == fill['database_sha256'], 'retained baseline bytes')
        capture = read_json(original / 'capture.json')
        require(capture == gate['crash_attempts'][0] and len(gate['crash_attempts']) == 1, 'capture receipt agreement')
        require(capture['captured'] is True and capture['stop_acknowledged'] is True and capture['kill_signal'] == 9, 'STOP/KILL receipt')
        precommit = read_json(original / 'before-commit.json')
        require(precommit['pid'] == waits[2]['pid'] and precommit['stage'] == 'before_real_COMMIT', 'precommit ownership receipt')
        require(precommit['mutation_kind'] == 'existing_pcm_incremental_blob' and precommit['mutated_rows'] == 42 and precommit['page_count_before'] == precommit['page_count_after'] == PAGES, 'fixed BLOB workload receipt')
        require(precommit['payload_bytes_written'] == sum(entry['pcm_bytes'] for entry in fill['attempts'] if entry.get('result') == 'COMMITTED'), 'payload byte receipt agreement')
        require(not (original / 'commit-returned.json').exists(), 'unexpected completed COMMIT receipt')
        journal_result = check_journal(baseline, interrupted, journal, bounds)
        require({key: value for key, value in journal_result.items() if key != 'changed_page_numbers'} == capture['audit'], 'independent journal audit disagreement')
        recovery = new_recovery(args.evidence, args.output, fill, interrupted, journal, bounds)
        require(inventory(args.evidence, bounds) == before, 'original evidence changed')
        require(verify_sources(args.preparation, pins, bounds) == source, 'source changed during independent audit')
        require(verify_runtime(pins, gate['identity'], bounds) == runtime, 'runtime changed during independent audit')
        require(hash_file(Path(__file__).resolve(), bounds) == args.self_sha256, 'audit helper drift')
        bounds.check()
        terminal.update(status='INDEPENDENT_POSTRUN_CHECK_COMPLETE_REVIEW_REQUIRED', source=source, runtime=runtime,
                        journal=journal_result, recovery=recovery, evidence_inventory=before, original_evidence_unchanged=True,
                        lifecycle_receipts_consistent=True, signal_history_independently_reobserved=False,
                        original_root_exit_independently_reobserved=False, audit_sha256=args.self_sha256)
    except BaseException as exc:
        terminal.update(error_class=type(exc).__name__, error=str(exc)[:256])
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        terminal.update(peak_sampled_audit_process_rss=bounds.peak_rss, peak_combined_run02_and_audit_charge=bounds.peak_charge,
                        samples=bounds.samples, artifact_cap=ARTIFACT_MAX, rss_cap=RSS_MAX, cpu_affinity=sorted(os.sched_getaffinity(0)))
        raw = encoded(terminal) + b'\n'
        require(len(raw) <= 32768, 'audit receipt extent')
        fd = os.open(args.output / 'audit.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        require(charge(args.evidence) + charge(args.output) <= ARTIFACT_MAX, 'final combined artifact charge')


if __name__ == '__main__':
    main()
