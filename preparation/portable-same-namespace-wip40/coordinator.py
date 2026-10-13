#!/usr/bin/env python3
"""Fixed, one-shot owner sequence. Source review is not execution approval."""
import time
START = time.monotonic()
ABSOLUTE_END = START + 12180
import ctypes
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import stat
import subprocess
import sys

ROOT = Path('/ABSOLUTE/REVIEWED_WORKSPACE')
SOURCE = ROOT / 'REVIEWED_SOURCE_RECOVERY'
HERE = SOURCE / 'REVIEWED_COORDINATOR_DIRECTORY'
OUT = HERE / 'output'
PACKET = ROOT / 'REVIEWED_BOOTSTRAP_PACKET'
PRODUCT = SOURCE / 'REVIEWED_PRODUCT_SOURCE'
HISTORY = SOURCE / 'REVIEWED_PRESERVED_HISTORY'
WORK = ROOT / 'REVIEWED_FRESH_WORK'
CONFIG = WORK / 'evidence/config.json'
RUNS = WORK / 'evidence/runs'
RUNTIME = ROOT / 'REVIEWED_ABSENT_RUNTIME_CONTAINER'
PROOF = ROOT / 'REVIEWED_PROOF_SOURCE'
HOST = '/ABSOLUTE/REVIEWED_HOST_PYTHON'
BOOT = 'REVIEWED_CURRENT_BOOT_ID'
MANIFEST_SHA = 'a7176dbb08b425e6e01ad5b4e26603073a2773336f55dcb04c5a0734c24bf1ee'
ASSETS_SHA = '90edcc2ad8b791dd77239e739dfc1964503ab0a201379f11f531039df0da8e06'
HOST_SHA = 'fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7'
MIB = 2**20
PINNED = {
    SOURCE / 'RECOVERY_RECEIPT.json': (REVIEWED_RESTORATION_RECEIPT_BYTES, 'REVIEWED_RESTORATION_RECEIPT_SHA256'),
    HISTORY / 'ORIGINS.json': (REVIEWED_ORIGINS_BYTES, 'REVIEWED_ORIGINS_SHA256'),
    HISTORY / 'REFUSAL.json': (REVIEWED_REFUSAL_BYTES, 'REVIEWED_REFUSAL_SHA256'),
    HISTORY / 'work-original/evidence/config.json': (REVIEWED_PRESERVED_CONFIG_BYTES, 'REVIEWED_PRESERVED_CONFIG_SHA256')}
CAPS = {'startup.json':32768, 'review-marker.json':65536, 'approval.pending':4096,
        'approval.json':4096, 'approval.consumed.json':4096, 'terminal.json':65536,
        'STOP':4096}
for _phase in ('configure', 'guard-fixtures', 'pacing-fixtures', 'download'):
    CAPS[_phase + '.log'] = 65536
    CAPS[_phase + '.json'] = 32768
STATE = {'schema':'fixed-owner-sequence-v1', 'status':'PREPARING', 'phases':[],
         'original_handles':[], 'errors':[], 'signal_events':[], 'cleanup':'UNKNOWN',
         'historical_interrupted_acquisition':'UNRESOLVED; no old cleanup claim',
         'peak_sampled_coordinator_rss':0, 'sampled_rss_ceiling_bytes':32*MIB,
         'limits_are_sampled':True, 'absolute_start_monotonic':START,
         'absolute_end_monotonic':ABSOLUTE_END, 'execution_attempts':1}
ACTIVE = None
PIDFD = None
LOGFD = None
LOG_USED = 0
CHILD_END = None
PHASE_START = None
PHASE_BUDGET = 0
CREDIT = 0.0
SIGNALLED = False
SELF = None
NAMESPACE = None
CONFIG_SHA = None
SOURCE_SHA = None
CONTRACT_SHA = None
OUTPUT_ID = None
PINS = None
RESULTS = []
PAGE = os.sysconf('SC_PAGE_SIZE')


class Refused(RuntimeError):
    pass


def need(condition, reason):
    if not condition:
        raise Refused(reason)


def clock():
    need(time.monotonic() < ABSOLUTE_END, 'coordinator_absolute_deadline')


def canonical(path, missing=False):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=False) == path, 'noncanonical_path')
    for part in (path, *path.parents):
        need(not part.is_symlink(), 'symlink_path')
    if not missing:
        need(path.exists(), 'missing_path')
    return path


def identity(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def read(path, cap, expected=None):
    clock()
    path = canonical(path)
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= cap,
         'regular_single_link_file_cap')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        need(identity(before) == identity(os.fstat(fd)), 'file_open_race')
        chunks = []
        total = 0
        h = hashlib.sha256()
        while True:
            clock()
            block = os.read(fd, min(65536, cap + 1 - total))
            if not block:
                break
            total += len(block)
            need(total <= cap, 'read_growth_cap')
            h.update(block)
            chunks.append(block)
        need(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'file_changed')
    finally:
        os.close(fd)
    pin = {'path':str(path), 'bytes':total, 'sha256':h.hexdigest()}
    if expected is not None:
        need((total, pin['sha256']) == expected, 'fixed_file_pin_changed')
    return b''.join(chunks), pin


def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def parsed(raw):
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(Refused('nonfinite_json')))


def document(path, cap=65536, expected=None):
    raw, pin = read(path, cap, expected)
    return parsed(raw), pin


def digest(path, cap, expected=None):
    # Hash streaming: the 30 MiB host executable must not be retained in memory.
    clock()
    path = canonical(path)
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= cap,
         'digest_regular_cap')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    h = hashlib.sha256()
    n = 0
    try:
        need(identity(before) == identity(os.fstat(fd)), 'digest_open_race')
        while True:
            clock()
            block = os.read(fd, min(65536, cap + 1 - n))
            if not block:
                break
            n += len(block)
            need(n <= cap, 'digest_growth')
            h.update(block)
        need(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'digest_changed')
    finally:
        os.close(fd)
    pin = {'path':str(path), 'bytes':n, 'sha256':h.hexdigest()}
    if expected is not None:
        need((n, pin['sha256']) == expected, 'digest_pin_changed')
    return pin


def output_check():
    need(OUTPUT_ID == (OUT.lstat().st_dev, OUT.lstat().st_ino), 'output_directory_changed')
    total = max(OUT.lstat().st_size, OUT.lstat().st_blocks * 512)
    for path in OUT.iterdir():
        st = path.lstat()
        need(path.name in CAPS and stat.S_ISREG(st.st_mode) and st.st_nlink == 1
             and st.st_dev == REVIEWED_DEVICE_ID and st.st_size <= CAPS[path.name], 'output_membership_or_cap')
        total += max(st.st_size, st.st_blocks * 512)
    need(total <= MIB, 'inclusive_output_budget')
    return total


def write(name, value):
    need(name in CAPS, 'unnamed_output')
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    need(len(raw) <= CAPS[name], 'output_file_cap')
    output_check()
    fd = os.open(OUT / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        directory = os.open(OUT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    output_check()
    return {'path':str(OUT / name), 'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest()}


def process(pid):
    raw = Path('/proc/%s/stat' % pid).read_text()
    need(len(raw) <= 8192, 'proc_stat_cap')
    fields = raw[raw.rindex(')') + 2:].split()
    return {'pid':pid, 'start_ticks':int(fields[19]), 'ppid':int(fields[1]),
            'state':fields[0], 'rss_bytes':max(0, int(fields[21])) * PAGE}


def direct_children():
    result = []
    count = 0
    with os.scandir('/proc') as entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            clock()
            count += 1
            need(count <= 1024, 'namespace_pid_count_cap')
            try:
                p = process(int(entry.name))
            except FileNotFoundError:
                continue
            if p['ppid'] == os.getpid():
                result.append(p)
    return result


def scope():
    clock()
    need(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == BOOT, 'boot_changed')
    need(os.readlink('/proc/self/ns/pid') == NAMESPACE, 'namespace_changed')
    own = process(os.getpid())
    need(own['start_ticks'] == SELF['start_ticks'], 'coordinator_identity_changed')
    need(os.sched_getaffinity(0) == {3} and os.getpriority(os.PRIO_PROCESS, 0) == 19,
         'coordinator_scheduling_changed')
    need(resource.getrlimit(resource.RLIMIT_AS) == (2**30, 2**30), 'address_space_changed')
    STATE['peak_sampled_coordinator_rss'] = max(STATE['peak_sampled_coordinator_rss'], own['rss_bytes'])
    need(own['rss_bytes'] <= 32*MIB, 'sampled_coordinator_rss_cap')
    if OUTPUT_ID is not None:
        output_check()


def no_children():
    need(ACTIVE is None, 'active_original_handle')
    for _ in range(2):
        scope()
        need(not direct_children(), 'live_or_unwaited_direct_children')
        time.sleep(0.05)


def account():
    overhead = time.monotonic() - START - CREDIT
    STATE['coordinator_overhead_seconds'] = overhead
    need(overhead < 60, 'coordinator_overhead_cleanup_budget')


def preservation():
    pins = [digest(path, size, (size, sha)) for path, (size, sha) in PINNED.items()]
    origins, _ = document(HISTORY / 'ORIGINS.json', 4096)
    expected_paths = {'work-original', 'work-original/evidence',
                      'work-original/evidence/runs', 'work-original/evidence/config.json'}
    need(len(origins['relocations']) == 4, 'preservation_count')
    seen = set()
    for row in origins['relocations']:
        old, saved = row['original'], row['preserved']
        path = canonical(saved['path'])
        rel = str(path.relative_to(HISTORY))
        seen.add(rel)
        need({k:v for k,v in old.items() if k != 'path'} ==
             {k:v for k,v in saved.items() if k != 'path'}, 'preservation_origin_changed')
        st = path.lstat()
        observed = {'dev':st.st_dev, 'ino':st.st_ino, 'mode':stat.S_IMODE(st.st_mode),
                    'mtime_ns':st.st_mtime_ns, 'bytes':st.st_size, 'nlink':st.st_nlink,
                    'type':'regular' if stat.S_ISREG(st.st_mode) else 'directory'}
        need(observed == {k:saved[k] for k in observed}, 'preserved_identity_changed')
    need(seen == expected_paths, 'preserved_membership')
    need(set((HISTORY / 'work-original').iterdir()) == {HISTORY / 'work-original/evidence'}, 'history_work_extra')
    need({p.name for p in (HISTORY / 'work-original/evidence').iterdir()} == {'config.json','runs'}, 'history_evidence_extra')
    need(not list((HISTORY / 'work-original/evidence/runs').iterdir()), 'history_runs_changed')
    return pins


def immutable():
    scope()
    result = preservation()
    result.append(digest(HERE / 'coordinator.py', 32768, (Path(__file__).stat().st_size, SOURCE_SHA)))
    contract_pin = digest(HERE / 'contract.json', 16384)
    need(contract_pin['sha256'] == CONTRACT_SHA, 'contract_changed')
    result.append(contract_pin)
    result.append(digest(HOST, 30894944, (30894944, HOST_SHA)))
    manifest, pin = document(PACKET / 'PACKET_MANIFEST.json', 16384)
    need(pin['sha256'] == MANIFEST_SHA, 'manifest_changed')
    result.append(pin)
    need({p.name for p in PACKET.iterdir()} == set(manifest['files']) | {'PACKET_MANIFEST.json'}, 'packet_membership')
    for name, row in sorted(manifest['files'].items()):
        need('/' not in name and name not in ('.','..'), 'packet_filename')
        result.append(digest(PACKET / name, 98304, (row['bytes'],row['sha256'])))
    assets, pin = document(PACKET / 'assets.lock.json', 98304)
    need(pin['sha256'] == ASSETS_SHA and len(assets['artifacts']) == 38 and
         assets['total_download_bytes'] == 5150756491, 'assets_identity')
    actual = {}
    total = 0
    for base, dirs, files in os.walk(PRODUCT, followlinks=False,
                                     onerror=lambda error: (_ for _ in ()).throw(error)):
        for name in dirs:
            canonical(Path(base) / name)
        for name in files:
            path = Path(base) / name
            row = digest(path, 32*MIB)
            need(path.stat().st_dev == REVIEWED_DEVICE_ID, 'product_device')
            total += row['bytes']
            need(len(actual) < 351 and total <= 4651663, 'product_bounds')
            actual[str(path.relative_to(PRODUCT))] = row['sha256']
    need(len(actual) == 351 and total == 4651663 and actual == assets['source']['files'], 'product_changed')
    need(not RUNTIME.exists(), 'runtime_container_must_remain_absent')
    if CONFIG_SHA is not None:
        c, pin = document(CONFIG)
        need(pin['sha256'] == CONFIG_SHA and c['created_boot_id'] == BOOT and
             c['created_pid_namespace'] == NAMESPACE and c['prior_acquisition'] is None and
             c['packet_sha256'] == MANIFEST_SHA and c['assets_sha256'] == ASSETS_SHA, 'config_changed')
        result.append(pin)
    scope()
    return result


def record_error(error):
    if len(STATE['errors']) < 16:
        STATE['errors'].append(type(error).__name__ + ': ' + str(error)[:240])


def loader(stage):
    keys = ('LD_PRELOAD','LD_LIBRARY_PATH','LD_AUDIT','GLIBC_TUNABLES')
    present = [key for key in keys if key in os.environ]
    try:
        Path('/etc/ld.so.preload').lstat()
        preload_absent = False
    except FileNotFoundError:
        preload_absent = True
    row = {'stage':stage,'at_monotonic':time.monotonic(),
           'forbidden_variables_present':present,'ld_so_preload_lstat_absent':preload_absent}
    STATE.setdefault('loader_observations',[]).append(row)
    need(not present and preload_absent, 'loader_precondition')


def drain():
    global LOG_USED
    while ACTIVE is not None:
        try:
            block = os.read(ACTIVE.stdout.fileno(), 8192)
        except BlockingIOError:
            return
        if not block:
            return
        need(LOG_USED + len(block) <= 65536, 'phase_log_cap')
        os.write(LOGFD, block)
        LOG_USED += len(block)


def send_original(sig):
    need(PIDFD is not None, 'no_retained_child_pidfd')
    try:
        signal.pidfd_send_signal(PIDFD, sig)
        STATE['signal_events'].append({'pid':ACTIVE.pid, 'signal':int(sig),
                                      'at_monotonic':time.monotonic()})
    except ProcessLookupError:
        pass


def wait_original(deadline, checks=True):
    if STATE['original_handles'][-1]['waited']:
        return STATE['original_handles'][-1]['returncode']
    while time.monotonic() < deadline:
        if checks:
            scope()
            drain()
        else:
            try:
                drain()
            except BaseException as error:
                record_error(error)
        if os.waitid(os.P_PID, ACTIVE.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
            code = ACTIVE.wait(timeout=0)
            STATE['original_handles'][-1].update(waited=True, returncode=code,
                                                waited_monotonic=time.monotonic())
            return code
        time.sleep(min(0.1, max(0, deadline-time.monotonic())))
    raise Refused('original_child_deadline')


def phase(name, seconds, argv):
    global ACTIVE, PIDFD, LOGFD, LOG_USED, CHILD_END, PHASE_START, PHASE_BUDGET, CREDIT
    no_children()
    account()
    immutable()
    need(not SIGNALLED and not (OUT / 'STOP').exists(), 'owner_stop')
    loader('before-'+name)
    parent_start = time.monotonic()
    need(parent_start + seconds < ABSOLUTE_END, 'insufficient_fixed_phase_window')
    before_runs = {p.name for p in RUNS.iterdir()} if RUNS.exists() else set()
    LOG_USED = 0
    LOGFD = os.open(OUT / (name+'.log'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    PHASE_START = parent_start
    PHASE_BUDGET = seconds
    CHILD_END = parent_start + seconds
    ACTIVE = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              close_fds=True, start_new_session=True, env=dict(os.environ))
    # Save the original handle immediately; never replace it with a fresh lookup.
    row = {'phase':name, 'pid':ACTIVE.pid, 'argv':argv, 'waited':False,
           'parent_launch_monotonic':parent_start, 'budget_seconds':seconds}
    STATE['original_handles'].append(row)
    os.set_blocking(ACTIVE.stdout.fileno(), False)
    original = process(ACTIVE.pid)
    need(original['ppid'] == os.getpid(), 'original_parent')
    PIDFD = os.pidfd_open(ACTIVE.pid, 0)
    again = process(ACTIVE.pid)
    need((again['start_ticks'],again['ppid']) == (original['start_ticks'],os.getpid()), 'original_pidfd_identity')
    need(os.readlink('/proc/%s/ns/pid' % ACTIVE.pid) == NAMESPACE, 'child_namespace')
    row['identity'] = original
    if name != 'configure':
        # The driver's complete stdout line is published only after spec fsync.
        published = False
        while time.monotonic() < CHILD_END:
            scope()
            drain()
            raw = (OUT / (name+'.log')).read_bytes()
            for line in raw.splitlines():
                if not line.startswith(b'{'):
                    continue
                try:
                    value = parsed(line)
                except (ValueError, UnicodeError):
                    continue
                if value.get('status') == 'SAME_PID_GUARD_EXEC':
                    spec, sp = document(WORK / 'evidence' / (name+'.spec.json'), MIB)
                    launch = spec['launch']
                    need(value['phase'] == name and value['spec_sha256'] == sp['sha256'] and
                         launch['identity'] == {'pid':ACTIVE.pid,'start_ticks':original['start_ticks'],
                                               'boot_id':BOOT,'pid_namespace':NAMESPACE} and
                         launch['absolute_end_monotonic'] == launch['start_monotonic'] + seconds and
                         parent_start <= launch['start_monotonic'] < parent_start + 5 and
                         launch['absolute_end_monotonic'] < ABSOLUTE_END,
                         'original_driver_clock_or_identity')
                    CHILD_END = launch['absolute_end_monotonic']
                    PHASE_START = launch['start_monotonic']
                    row['original_launch'] = launch
                    row['spec'] = sp
                    print(json.dumps({'status':'PHASE_STARTED','phase':name,
                                      'original_identity':launch['identity'],
                                      'absolute_end_monotonic':CHILD_END}),flush=True)
                    published = True
                    break
            if published or os.waitid(os.P_PID, ACTIVE.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                break
            time.sleep(0.1)
    code = wait_original(CHILD_END)
    drain()
    ACTIVE.stdout.close()
    ACTIVE = None
    os.close(PIDFD)
    PIDFD = None
    os.fsync(LOGFD)
    os.close(LOGFD)
    LOGFD = None
    CREDIT += min(seconds, time.monotonic()-PHASE_START)
    PHASE_START = None
    row['log'] = digest(OUT / (name+'.log'), 65536)
    row['new_runs'] = sorted({p.name for p in RUNS.iterdir()} - before_runs)
    STATE['phases'].append(write(name+'.json', row))
    no_children()
    account()
    need(code == 0, 'nonzero_original_'+name)
    need(not SIGNALLED and not (OUT / 'STOP').exists(), 'owner_stop')
    return row


def guard_result(row, count=None):
    name = row['phase']
    raw, _ = read(OUT / (name+'.log'), 65536)
    lines = [parsed(line) for line in raw.splitlines() if line.startswith(b'{')]
    need(lines and lines[-1].get('status') == 'PASS', 'guard_stdout_not_pass')
    path = canonical(lines[-1]['receipt'])
    need(path.parent.parent == RUNS and path.name == 'receipt.json' and
         path.parent.name in row['new_runs'], 'guard_receipt_origin')
    receipt, rp = document(path, MIB//2)
    need(receipt['status'] == 'PASS' and receipt['cleanup'] == 'CONFIRMED' and
         receipt['original_handle_waited'] is True and receipt['exit_code'] == 0 and
         receipt['final_source_pin_verified'] is True and receipt['errors'] == [] and
         receipt['unresolved_owned'] == [] and receipt['final_direct_child_pids'] == [] and
         receipt['spec_sha256'] == row['spec']['sha256'] and
         receipt['supervisor']['pid'] == row['pid'] and
         receipt['supervisor']['start_ticks'] == row['identity']['start_ticks'] and
         receipt['supervisor']['boot_id'] == BOOT and receipt['supervisor']['pid_namespace'] == NAMESPACE,
         'guard_pass_cleanup_source_wait_required')
    filename = name+'.json' if count is not None else 'download-result.json'
    value, vp = document(path.parent / filename, 32768)
    if count is not None:
        need(value['status'] == 'PASS' and value['tests_run'] == count and
             value['failures'] == [] and value['errors'] == [] and value['skipped'] == 0 and
             all(value[k] == 0 for k in ('network_requests','model_loads','model_calls')), 'raw_fixture_result')
        if count == 24:
            need(value['original_child_waits'] == [
                {'mode':'success','waited':True,'returncode':0},
                {'mode':'nonzero','waited':True,'returncode':1}], 'fixture_original_waits')
    else:
        need(value['status'] == 'VERIFIED_DOWNLOAD_ONLY' and value['expected_artifacts'] == 38 and
             value['final_verified_artifacts'] == 38 and value['final_verified_bytes'] == 5150756491 and
             value['network_bytes'] == 5150756491 and value['newly_received_artifacts'] == 38 and
             value['newly_received_bytes'] == 5150756491 and value['reused_artifacts'] == 0 and
             value['automatic_retries'] == 0 and value['ranges'] is False and
             all(value[k] == 0 for k in ('archives_extracted','installs','provider_imports','model_loads','model_calls')),
             'download_raw_reconciliation')
        assets, _ = document(PACKET / 'assets.lock.json', 98304)
        expected = [{k:a[k] for k in ('id','destination','bytes','sha256')} for a in assets['artifacts']]
        need(value['verified_artifacts'] == expected, 'exact38_result_artifacts')
    return {'phase':name, 'guard_receipt':rp, 'raw_result':vp, 'original_wait':row}


def verify_result_pins():
    for result in RESULTS:
        for key in ('guard_receipt','raw_result'):
            p = result[key]
            digest(p['path'], MIB//2, (p['bytes'],p['sha256']))
        row = result['original_wait']
        for key in ('spec','log'):
            p = row[key]
            digest(p['path'], MIB, (p['bytes'],p['sha256']))


def approval():
    global CREDIT
    no_children()
    current = immutable()
    verify_result_pins()
    account()
    begin = time.monotonic()
    end = begin + 600
    need(end + 10800 < ABSOLUTE_END, 'full_review_and_download_windows_required')
    marker = {'schema':'fixed-owner-review-marker-v1', 'nonce':os.urandom(32).hex(),
              'coordinator_identity':SELF, 'boot_id':BOOT, 'pid_namespace':NAMESPACE,
              'packet_sha256':MANIFEST_SHA, 'assets_sha256':ASSETS_SHA,
              'config_sha256':CONFIG_SHA, 'coordinator_sha256':SOURCE_SHA,
              'contract_sha256':CONTRACT_SHA, 'immutable_pins':current,
              'fixture_results':RESULTS, 'original_handles':STATE['original_handles'],
              'review_start_monotonic':begin, 'review_end_monotonic':end,
              'absolute_end_monotonic':ABSOLUTE_END, 'active_children':[],
              'requested_next_phase':'download', 'artifacts':38, 'bytes':5150756491,
              'rate_bytes_per_second':4194304, 'attempts_per_artifact':1,
              'automatic_retries':0, 'download_absolute_seconds':10800}
    mp = write('review-marker.json', marker)
    STATE['review_marker'] = mp
    print(json.dumps({'status':'AWAITING_INDEPENDENT_REVIEW','marker':mp,'expires_monotonic':end}), flush=True)
    approved = None
    try:
        while time.monotonic() < end:
            scope()
            need(not direct_children(), 'child_during_review')
            need(not SIGNALLED and not (OUT / 'STOP').exists(), 'owner_stop_during_review')
            if (OUT / 'approval.json').exists():
                value, pin = document(OUT / 'approval.json', 4096)
                expected = {'schema':'fixed-owner-approval-v1', 'decision':'APPROVE_DOWNLOAD_ONCE',
                            'reviewer':'REVIEWED_INDEPENDENT_REVIEWER',
                            'marker_sha256':mp['sha256'], 'nonce':marker['nonce'],
                            'boot_id':BOOT, 'pid_namespace':NAMESPACE,
                            'config_sha256':CONFIG_SHA, 'review_end_monotonic':end}
                need(value == expected, 'approval_refused_or_binding_changed')
                need(not (OUT / 'approval.pending').exists() and
                     not (OUT / 'approval.consumed.json').exists(), 'approval_not_single_use')
                digest(OUT / 'review-marker.json', 65536, (mp['bytes'],mp['sha256']))
                need(immutable() == current, 'review_immutable_pins_changed')
                verify_result_pins()
                need(time.monotonic() < end and not direct_children(), 'approval_expired_or_child')
                os.rename(OUT / 'approval.json', OUT / 'approval.consumed.json')
                consumed = digest(OUT / 'approval.consumed.json', 4096, (pin['bytes'],pin['sha256']))
                approved = consumed
                break
            time.sleep(min(0.2, max(0, end-time.monotonic())))
        need(approved is not None, 'approval_expired')
        STATE['approval_consumed'] = approved
    finally:
        CREDIT += min(600, time.monotonic()-begin)
    no_children()
    account()
    need(time.monotonic() + 10800 < ABSOLUTE_END, 'fresh_download_window_unavailable')


def cleanup():
    global ACTIVE, PIDFD, LOGFD, CREDIT, PHASE_START
    if ACTIVE is not None:
        # Let the original guard retain its complete existing work/cleanup window.
        try:
            wait_original(min(CHILD_END, ABSOLUTE_END-2), checks=False)
        except BaseException as error:
            record_error(error)
            used = time.monotonic()-START-CREDIT-min(PHASE_BUDGET,time.monotonic()-PHASE_START)
            fallback_end = min(ABSOLUTE_END-1, time.monotonic()+max(0,60-used))
            for sig, grace in ((signal.SIGTERM, 2), (signal.SIGKILL, 5)):
                try:
                    send_original(sig)
                    wait_original(min(fallback_end,time.monotonic()+grace), checks=False)
                    break
                except BaseException as failure:
                    record_error(failure)
        if STATE['original_handles'][-1]['waited']:
            ACTIVE.stdout.close()
            ACTIVE = None
        if PIDFD is not None:
            os.close(PIDFD)
            PIDFD = None
    if LOGFD is not None:
        os.fsync(LOGFD)
        os.close(LOGFD)
        LOGFD = None
    try:
        remaining = direct_children()
        STATE['final_direct_children'] = remaining
        STATE['cleanup'] = 'CONFIRMED' if ACTIVE is None and not remaining else 'UNKNOWN'
    except BaseException as error:
        record_error(error)
        STATE['cleanup'] = 'UNKNOWN'


def main():
    global SELF, NAMESPACE, SOURCE_SHA, CONTRACT_SHA, OUTPUT_ID, CONFIG_SHA, PINS, SIGNALLED
    need(len(sys.argv) == 5 and sys.argv[1] == '--coordinator-sha256' and
         sys.argv[3] == '--contract-sha256', 'fixed_launch_argv')
    SOURCE_SHA, CONTRACT_SHA = sys.argv[2], sys.argv[4]
    need(all(len(s) == 64 and set(s) <= set('0123456789abcdef') for s in (SOURCE_SHA,CONTRACT_SHA)), 'launch_hash_shape')
    need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and
         Path(sys.executable).resolve() == Path(HOST) and sys.version_info[:3] == (3,12,14), 'isolated_pinned_host')
    need(canonical(__file__) == HERE / 'coordinator.py' and ROOT.stat().st_dev == REVIEWED_DEVICE_ID, 'fixed_location_device')
    need({p.name for p in HERE.iterdir()} == {'coordinator.py','contract.json'}, 'one_shot_output_already_present')
    need(not WORK.exists() and not WORK.is_symlink() and not RUNTIME.exists(), 'fresh_work_required')
    need(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'), 'pidfds_required')
    SELF = process(os.getpid())
    NAMESPACE = os.readlink('/proc/self/ns/pid')
    os.sched_setaffinity(0, {3})
    os.nice(max(0,19-os.getpriority(os.PRIO_PROCESS,0)))
    resource.setrlimit(resource.RLIMIT_AS, (2**30,2**30))
    resource.setrlimit(resource.RLIMIT_CORE, (0,0))
    need(ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0) == 0, 'coordinator_subreaper_required')
    def stop(sig, frame):
        global SIGNALLED
        SIGNALLED = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    PINS = immutable()
    contract, _ = document(HERE / 'contract.json',16384)
    need(contract['schema'] == 'fixed-owner-contract-v1', 'contract_schema')
    no_children()
    OUT.mkdir(mode=0o700)
    OUTPUT_ID = (OUT.stat().st_dev, OUT.stat().st_ino)
    STATE.update(coordinator_identity=SELF, boot_id=BOOT, pid_namespace=NAMESPACE,
                 coordinator_sha256=SOURCE_SHA, contract_sha256=CONTRACT_SHA,
                 pinned_prerequisites=PINS)
    write('startup.json', STATE)
    prefix = [HOST,'-I','-B','-S']
    configure = prefix + [str(PACKET/'bootstrap.py'),'configure','--product',str(PRODUCT),
        '--source-recovery',str(SOURCE),'--work',str(WORK),'--runtime',str(RUNTIME/'runtime'),
        '--proof-source',str(PROOF)]
    row = phase('configure',120,configure)
    c, cp = document(CONFIG)
    raw, _ = read(OUT/'configure.log',65536)
    value = parsed(raw)
    need(value['status'] == 'CONFIGURED_SOURCE_ONLY' and value['sha256'] == cp['sha256'] and
         value['bytes'] == cp['bytes'] and value['execution_authorized'] is False and
         c['created_boot_id'] == BOOT and c['created_pid_namespace'] == NAMESPACE, 'configure_receipt')
    CONFIG_SHA = cp['sha256']
    for name, count in (('guard-fixtures',24),('pacing-fixtures',37)):
        argv = prefix + [str(PACKET/'guard_driver.py'),'--config',str(CONFIG),
                         '--config-sha256',CONFIG_SHA,'--phase',name]
        RESULTS.append(guard_result(phase(name,300,argv),count))
    approval()
    verify_result_pins()
    argv = prefix + [str(PACKET/'guard_driver.py'),'--config',str(CONFIG),
                     '--config-sha256',CONFIG_SHA,'--phase','download']
    result = guard_result(phase('download',10800,argv))
    STATE['download_result'] = result
    immutable()
    no_children()
    account()
    STATE['status'] = 'VERIFIED_DOWNLOAD_ONLY'


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        record_error(error)
        STATE['status'] = 'FAILED_TERMINAL_NO_RETRY'
    finally:
        try:
            cleanup()
        except BaseException as error:
            record_error(error)
            STATE['cleanup'] = 'UNKNOWN'
        try:
            loader('terminal')
        except BaseException as error:
            record_error(error)
        STATE['elapsed_seconds'] = time.monotonic()-START
        if STATE['cleanup'] != 'CONFIRMED' or STATE['errors']:
            STATE['status'] = 'FAILED_TERMINAL_NO_RETRY'
        try:
            if OUTPUT_ID is not None:
                terminal = write('terminal.json', STATE)
                print(json.dumps({'status':STATE['status'],'cleanup':STATE['cleanup'],'terminal':terminal}),flush=True)
            else:
                print(json.dumps({'status':'REFUSED_BEFORE_OUTPUT','errors':STATE['errors']}),flush=True)
        except BaseException as error:
            print(json.dumps({'status':'TERMINAL_WRITE_FAILED','error_type':type(error).__name__}),flush=True)
    raise SystemExit(0 if STATE['status'] == 'VERIFIED_DOWNLOAD_ONLY' else 2)
