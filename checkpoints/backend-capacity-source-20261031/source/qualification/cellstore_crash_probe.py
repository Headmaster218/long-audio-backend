"""Finite production-DDL physical evidence with synthetic rows; never activates a profile."""
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
from audio_backend.native_owned_cell_store import runtime_fingerprints, Budget, QUALIFIED_RUNTIMES, SQL_PROJECTION_BYTES
from audio_backend.native_owned_cells import HANDOFF_BYTES

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


def identity(directory, target_observation=None, target_sha256=None):
    if target_observation is not None:
        from qualification.target_runtime_observation import probe_identity
        return probe_identity(directory, target_observation, target_sha256)
    if target_sha256 is not None:
        raise ValueError('target_observation_path_required')
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
                    foreign_keys=1, trusted_schema=0, max_page_count=6144, auto_vacuum=0)
    for key, value in expected.items():
        if db.execute('PRAGMA ' + key).fetchone()[0] != value:
            raise RuntimeError('pragma:' + key)
    for category,value in ((sqlite3.SQLITE_LIMIT_LENGTH,HANDOFF_BYTES),(sqlite3.SQLITE_LIMIT_SQL_LENGTH,SQL_PROJECTION_BYTES),(sqlite3.SQLITE_LIMIT_ATTACHED,0)):
        db.setlimit(category,value)
        if db.getlimit(category)!=value:raise RuntimeError('sqlite_limit')
    return db


def typed_oracle(db):
    tables = {'store_meta':'id','sources':'cid','cells':'seq',
              'dependencies':'cell_id,position','sqlite_sequence':'name','sqlite_master':'type,name'}
    schema = db.execute("SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name").fetchall()
    if {r[1] for r in schema if r[0]=='table'} != set(tables)-{'sqlite_master'}:
        raise RuntimeError('schema_roster')
    def typed(value):
        if value is None:return ['null']
        if type(value) is int:return ['integer',str(value)]
        if type(value) is float:return ['real',value.hex()]
        if type(value) is str:return ['text',value]
        if type(value) is bytes:return ['blob',len(value),sha(value)]
        raise RuntimeError('unexpected_sql_type')
    result = {'schema_sha256':sha(json.dumps(schema,separators=(',',':')).encode()),'tables':{}}
    for table,order in tables.items():
        digest=hashlib.sha256();count=0
        for row in db.execute('SELECT * FROM '+table+' ORDER BY '+order):
            digest.update(json.dumps([typed(v) for v in row],separators=(',',':')).encode()+b'\n');count+=1
        columns=[row[1] for row in db.execute('PRAGMA table_info('+table+')')]
        result['tables'][table]={'columns':columns,'rows':count,'typed_sha256':digest.hexdigest()}
    if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError('schema_integrity')
    if db.execute('SELECT count(*) FROM dependencies d LEFT JOIN cells c ON c.cell_id=d.cell_id LEFT JOIN sources s ON s.cid=d.cid WHERE c.cell_id IS NULL OR s.cid IS NULL OR c.cid!=d.cid OR s.digest!=d.digest').fetchone()[0]:
        raise RuntimeError('source_dependency_relationship')
    receipts={cid:sha(raw) for cid,raw in db.execute('SELECT cid,receipt FROM sources')}
    if any(receipts[cid]!=receipt_id for cid,receipt_id in db.execute('SELECT cid,receipt_id FROM dependencies')):
        raise RuntimeError('receipt_relationship')
    return result


def fill_schema(db,directory):
    from audio_backend.native_owned_cell_store import CellStore,Receipt
    CellStore._schema(db)  # Unmodified production DDL, indexes and constraints.
    db.execute('INSERT INTO store_meta VALUES(1,?,?,?,?,?,?)',
               ('native-owned-cell-store-v1','synthetic-policy','synthetic-primary','synthetic-qualification','synthetic-namespace','synthetic-database'))
    db.execute('BEGIN IMMEDIATE')
    for index in range(2048):
        cid=sha(('source-%04d'%index).encode())
        receipt=Receipt('synthetic',cid,'a'*64,320000,44,640044,(27,index+1,640044,1,1),0.,None)
        db.execute('INSERT INTO sources VALUES(?,?,?,?)',(cid,'synthetic','a'*64,receipt.encoded))
    db.execute('COMMIT')
    count=0;full_count=0
    for size in (640000,65536,4096,256,2):
        while count<1024:
            cid=sha(('source-%04d'%(count%2048)).encode());cell=sha(('cell-%04d'%count).encode())
            try:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO cells(cell_id,ownership,sid,cid,proof,pcm,state) VALUES(?,?,?,?,?,?,?)',
                           (cell,sha(('ownership-%04d'%count).encode()),'synthetic',cid,b'{"synthetic":true,"pad":"'+b'A'*min(size,65500)+b'"}',b'A'*size,'retained'))
                db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)',
                           (cell,0,cid,'a'*64,sha(db.execute('SELECT receipt FROM sources WHERE cid=?',(cid,)).fetchone()[0]),0,size//2,0,size//2))
                db.execute('COMMIT')
            except sqlite3.OperationalError as exc:
                if db.in_transaction:db.execute('ROLLBACK')
                if getattr(exc,'sqlite_errorcode',None)!=sqlite3.SQLITE_FULL:raise
                full_count+=1;break
            count+=1
    pages=db.execute('PRAGMA page_count').fetchone()[0]
    if not full_count or pages!=6144 or not 1<=count<=1024:
        raise RuntimeError('production_full_ceiling_not_observed:'+str((pages,count,full_count)))
    save(directory/'expected_rows.json',{'cells':count,'sources':2048,'pages':pages,'sqlite_full':True,'full_events':full_count})
    save(directory/'oracle.json',typed_oracle(db))


def child(phase, directory, parent_pid, target_observation=None, target_sha256=None):
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
    current = identity(directory, target_observation, target_sha256)
    db = connect(directory/'store/cells.sqlite3')
    try:
        if phase=='fill':fill_schema(db,directory)
        elif phase=='crash':
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE cells SET pcm=zeroblob(length(pcm)),proof=zeroblob(length(proof))")
            db.execute('UPDATE sources SET digest=?,receipt=CAST(replace(CAST(receipt AS TEXT),?,?) AS BLOB)',('c'*64,'a'*64,'c'*64))
            db.execute('UPDATE dependencies SET digest=?,receipt_id=?',('c'*64,'d'*64))
            db.execute("UPDATE store_meta SET primary_identity='synthetic-changed'")
            save(directory/'before_commit.json',{'identity':current,'stage':'before_commit'})
            os.kill(os.getpid(),signal.SIGSTOP)
            db.execute('COMMIT')
            save(directory/'missed_commit.json',{'committed':True})
        elif phase=='recover':
            expected=json.loads((directory/'oracle.json').read_text())
            if typed_oracle(db)!=expected:raise RuntimeError('typed_oracle_recovery_mismatch')
        else:raise ValueError('phase')
    finally:db.close()
    save(directory/(phase+'.json'),{'closed':True,'identity':current})


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
    namespace=directory/'store'
    if not namespace.exists():return {'charged_bytes':0,'logical_bytes':0,'slack_bytes':0}
    files=list(namespace.iterdir())
    if any(p.name not in ('cells.sqlite3','cells.sqlite3-journal') for p in files):
        raise RuntimeError('unexpected_database_artifact')
    root=namespace.stat();directory_charge=max(root.st_size,root.st_blocks*512)
    logical=sum(p.stat().st_size for p in files)
    charged=directory_charge+sum(max(p.stat().st_size,p.stat().st_blocks*512) for p in files)
    if charged>TARGET_LIMIT or charged-logical>2*1024**2:
        raise RuntimeError('target_allocation_bound')
    for p in files:
        if p.stat().st_size>(MAX_DB if p.name=='cells.sqlite3' else MAX_JOURNAL):
            raise RuntimeError('individual_file_capacity')
    return dict(charged_bytes=charged,logical_bytes=logical,slack_bytes=charged-logical,directory_charge=directory_charge)


def supervise(out, target_observation=None, target_sha256=None):
    if target_observation is not None:
        target_observation = Path(target_observation).absolute()
        identity(out.parent, target_observation, target_sha256)
    elif target_sha256 is not None:
        raise ValueError('target_observation_path_required')
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
    result = {'schema': 'finite-cellstore-schema-crash-probe-v1', 'passed': False,
              'production_profile_activated': False, 'power_loss_qualified': False}
    baseline_dir = out / 'baseline'
    baseline_dir.mkdir(mode=0o700)
    (baseline_dir/'store').mkdir(mode=0o700)
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
                from qualification.target_runtime_observation import child_arguments
                active = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                    '--child', phase, '--parent-pid', str(os.getpid()), str(directory)] + child_arguments(target_observation, target_sha256),
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
        result['identity'] = identity(out, target_observation, target_sha256)
        waited(launch('fill', baseline_dir), 'fill', 0)
        baseline = bounded_read(baseline_dir/'store/cells.sqlite3', MAX_DB)
        result['fill_allocation'] = allocation(baseline_dir)
        oracle = (baseline_dir/'oracle.json').read_bytes()
        result['typed_oracle']=json.loads(oracle)
        expected_rows = (baseline_dir/'expected_rows.json').read_bytes()
        result['full_capacity'] = json.loads(expected_rows)
        captured = None
        for attempt in range(1, 5):
            directory = out / ('attempt-' + str(attempt)); directory.mkdir(mode=0o700)
            (directory/'store').mkdir(mode=0o700)
            with (directory/'store/cells.sqlite3').open('xb') as stream:
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
                journal_path = directory/'store/cells.sqlite3-journal'
                if not journal_path.exists():continue
                with journal_path.open('rb') as stream:header = stream.read(28)
                with (directory/'store/cells.sqlite3').open('rb') as stream:prefix = stream.read(65536)
                if header[:8] != MAGIC or prefix == baseline[:65536]:continue
                database = bounded_read(directory/'store/cells.sqlite3', MAX_DB)
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
        recovery = out/'recovery';recovery.mkdir(mode=0o700);(recovery/'store').mkdir(mode=0o700)
        (recovery/'store/cells.sqlite3').write_bytes(database)
        (recovery/'store/cells.sqlite3-journal').write_bytes(journal)
        (recovery/'expected_rows.json').write_bytes(expected_rows)
        (recovery/'oracle.json').write_bytes(oracle)
        check()
        waited(launch('recover', recovery), 'recover', 0)
        recovered = bounded_read(recovery/'store/cells.sqlite3', MAX_DB)
        if recovered != baseline or (recovery/'store/cells.sqlite3-journal').exists():
            raise RuntimeError('recovery_not_exact')
        result['recovery_allocation'] = allocation(recovery)
        if bounded_read(captured/'store/cells.sqlite3', MAX_DB) != database or bounded_read(captured/'store/cells.sqlite3-journal', MAX_JOURNAL) != journal:
            raise RuntimeError('captured_pair_changed')
        result['identity_after'] = identity(out, target_observation, target_sha256)
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
    parser.add_argument('--target-observation', type=Path)
    parser.add_argument('--target-sha256')
    args = parser.parse_args()
    if args.child:
        child(args.child, args.output.resolve(), args.parent_pid, args.target_observation, args.target_sha256)
    else:
        raise SystemExit(supervise(args.output.resolve(), args.target_observation, args.target_sha256))
