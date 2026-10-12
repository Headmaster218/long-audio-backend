"""Dormant finite static inspection. No SDK import, dlopen, inference, or resolver.
Run only after exact independent review and separate output-storage admission.
"""
import time
START = time.monotonic()
ABSOLUTE_END = START + 60.0
WORK_END = ABSOLUTE_END - 10.0
import os
import sys
import json
import hashlib
import stat
import resource
import signal
import selectors
import subprocess
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREP = HERE.parent.parent
OUTPUT = PREP / 'build/runtime-static-inventory.json'
LIMIT = 64 * 1024 * 1024
MAX_REPORT = 768 * 1024
MAX_STDOUT = 32 * 1024
MAX_STDERR = 4096
MAX_ALL_STDOUT = 384 * 1024
MAX_ALL_STDERR = 32 * 1024
LEDGER = []
TOTAL_IO = [0, 0]
OWNED_RSS_MAX = 0
STOP = False


class Refusal(Exception):
    pass


def require(ok, reason):
    if not ok:
        raise Refusal(reason)


def check(end=WORK_END):
    require(not STOP, 'stop_received')
    require(time.monotonic() < end, 'absolute_deadline')


def stop_handler(signum, frame):
    global STOP
    STOP = True


def hard_end(signum, frame):
    # Work ends ten seconds earlier; owned cleanup must already have completed.
    os._exit(124)


def identity(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def bounded(path, cap, end=WORK_END):
    check(end)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= cap, 'bounded_file')
        data = bytearray()
        while True:
            check(end)
            block = os.read(fd, min(65536, cap + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            require(len(data) <= cap, 'bounded_read_overflow')
        require(len(data) == before.st_size and identity(before) == identity(os.fstat(fd)),
                'bounded_file_changed')
        check(end)
        return bytes(data)
    finally:
        os.close(fd)


def verify_open(pin):
    check()
    fd = os.open(pin['path'], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size == pin['bytes'], 'candidate_size')
        require(str(Path(pin['path']).resolve(strict=True)) == pin['path'], 'candidate_not_canonical')
        digest = hashlib.sha256()
        count = 0
        while True:
            check()
            block = os.read(fd, min(256 * 1024, pin['bytes'] - count + 1))
            if not block:
                break
            count += len(block)
            require(count <= pin['bytes'], 'candidate_grew')
            digest.update(block)
        require(count == pin['bytes'] and digest.hexdigest() == pin['sha256'], 'candidate_hash')
        require(identity(before) == identity(os.fstat(fd)), 'candidate_changed')
        os.lseek(fd, 0, os.SEEK_SET)
        check()
        return fd, identity(before)
    except BaseException:
        os.close(fd)
        raise


def rss(pid):
    # Only this helper and its retained original live readelf child; no process census.
    check(ABSOLUTE_END)
    fd = os.open('/proc/%d/status' % pid, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        raw = os.read(fd, 8193)
    finally:
        os.close(fd)
    require(len(raw) <= 8192, 'status_cap')
    match = re.search(rb'^VmRSS:\s+(\d+) kB$', raw, re.M)
    if match is None:
        # Reaped/exiting identity cannot authorize successful inspection by itself.
        return None
    return int(match.group(1)) * 1024


def memory_check(entry=None):
    global OWNED_RSS_MAX
    own = rss(os.getpid())
    require(own is not None, 'own_rss_missing')
    child = 0
    if entry is not None and not entry['waited']:
        require(entry['pidfd'] is not None, 'pidfd_missing')
        child = rss(entry['original'].pid)
        if child is None:
            child = 0
    OWNED_RSS_MAX = max(OWNED_RSS_MAX, own + child)
    require(OWNED_RSS_MAX <= LIMIT, 'owned_sampled_rss_over_64MiB')


def close_pipes(entry):
    for pipe in (entry['original'].stdout, entry['original'].stderr):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def cleanup(entry):
    # Ownership exists even when pidfd/identity observation failed.
    child = entry['original']
    if child is None:
        return
    if entry['waited']:
        close_pipes(entry)
        if entry['pidfd'] is not None:
            os.close(entry['pidfd'])
            entry['pidfd'] = None
        return
    remaining = ABSOLUTE_END - time.monotonic()
    if remaining <= 0:
        raise Refusal('cleanup_absolute_deadline')
    try:
        if entry['pidfd'] is not None:
            signal.pidfd_send_signal(entry['pidfd'], signal.SIGKILL, None, 0)
        else:
            # Unwaited original child PID cannot be recycled; exact original only.
            child.kill()
    except ProcessLookupError:
        pass
    try:
        entry['returncode'] = child.wait(timeout=max(0.001, ABSOLUTE_END-time.monotonic()-0.2))
        entry['waited'] = True
    finally:
        close_pipes(entry)
        if entry['pidfd'] is not None:
            os.close(entry['pidfd'])
            entry['pidfd'] = None
    require(time.monotonic() < ABSOLUTE_END, 'late_cleanup')


def inspect(pin, tool_fd):
    fd, before = verify_open(pin)
    entry = None
    selector = selectors.DefaultSelector()
    outputs = [bytearray(), bytearray()]
    try:
        check()
        memory_check()
        # The executable and inspected input are already pinned open descriptions.
        entry = {'original': None, 'pidfd': None, 'waited': False,
                 'returncode': None, 'observation_failed': False, 'path': pin['path']}
        LEDGER.append(entry)
        entry['original'] = subprocess.Popen(
            ['/proc/self/fd/%d' % tool_fd, '--wide', '--file-header',
             '--program-headers', '--dynamic', '--', '/proc/self/fd/%d' % fd],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, pass_fds=(tool_fd, fd), shell=False,
            cwd=str(HERE), env={'LC_ALL': 'C', 'LANG': 'C', 'TZ': 'UTC'},
            start_new_session=True)
        # Popen return is retained in the preallocated ledger before observation.
        original = entry['original']
        try:
            entry['pidfd'] = os.pidfd_open(original.pid, 0)
        except BaseException:
            entry['observation_failed'] = True
            raise
        for index, pipe in enumerate((original.stdout, original.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, index)
        while selector.get_map():
            check()
            memory_check(entry)
            events = selector.select(timeout=min(0.05, max(0.0, WORK_END-time.monotonic())))
            check()
            for key, _ in events:
                index = key.data
                block = os.read(key.fileobj.fileno(), 4096)
                if not block:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                outputs[index].extend(block)
                TOTAL_IO[index] += len(block)
                require(len(outputs[index]) <= (MAX_STDOUT, MAX_STDERR)[index], 'child_output_cap')
                require(TOTAL_IO[index] <= (MAX_ALL_STDOUT, MAX_ALL_STDERR)[index], 'aggregate_output_cap')
        check()
        # No poll/communicate or replacement Popen. A positive original wait is mandatory.
        entry['returncode'] = original.wait(timeout=max(0.001, WORK_END-time.monotonic()))
        entry['waited'] = True
        check()
        memory_check()
        require(entry['returncode'] == 0, 'readelf_nonzero')
        require(not outputs[1], 'readelf_stderr')
        require(identity(os.fstat(fd)) == before, 'inspected_file_changed')
        text = outputs[0].decode('utf-8', 'strict')
        require(text.startswith('ELF Header:'), 'not_elf_readelf_output')
        require('Class:                             ELF64' in text and
                'little endian' in text and 'Advanced Micro Devices X86-64' in text,
                'unsupported_elf_identity')
        tags = {'needed': [], 'rpath': [], 'runpath': [], 'soname': []}
        for line in text.splitlines():
            for word, key in [('NEEDED', 'needed'), ('RPATH', 'rpath'),
                              ('RUNPATH', 'runpath'), ('SONAME', 'soname')]:
                if '(' + word + ')' in line:
                    match = re.search(r'\[([^\]]*)\]', line)
                    require(match is not None, 'unparsed_dynamic_tag')
                    tags[key].append(match.group(1))
        require(all(len(v) <= 64 for v in tags.values()), 'dynamic_tag_count')
        interpreter = re.findall(r'\[Requesting program interpreter: ([^\]]+)\]', text)
        require(len(interpreter) <= 1, 'interpreter_count')
        return {'path': pin['path'], 'bytes': pin['bytes'], 'sha256': pin['sha256'],
                'roles': pin['roles'], 'elf': tags, 'interpreter': interpreter,
                'stdout': text, 'stderr': '', 'original_wait_returncode': entry['returncode'],
                'file_identity': list(before)}
    finally:
        selector.close()
        if entry is not None:
            cleanup(entry)
        os.close(fd)


def expand(path):
    return str(PREP) + path[5:] if path.startswith('@prep/') else path


def load_allowlist(expected):
    raw = bounded(HERE/'allowlist.json', 48 * 1024)
    require(hashlib.sha256(raw).hexdigest() == expected, 'allowlist_hash')
    data = json.loads(raw)
    require(data['schema'] == 'rc3-dev3-static-runtime-allowlist-v1', 'allowlist_schema')
    require(data['root'] == str(PREP), 'fixed_preparation_root')
    for pin in data['candidates']:
        pin['path'] = expand(pin['path'])
    for alias in data['aliases']:
        alias['path'] = expand(alias['path'])
        alias['resolved_target'] = expand(alias['resolved_target'])
    for item in data['inventories']:
        item['path'] = expand(item['path'])
    data['tool_path'] = expand(data['tool_path'])
    data['host_python']['path'] = expand(data['host_python']['path'])
    return data


def source_inventories(data):
    loaded = {}
    total = 0
    for pin in data['inventories']:
        raw = bounded(pin['path'], pin['bytes'])
        total += len(raw)
        require(total <= 2 * 1024 * 1024, 'source_inventory_total')
        require(len(raw) == pin['bytes'] and hashlib.sha256(raw).hexdigest() == pin['sha256'],
                'source_inventory_changed')
        loaded[pin['id']] = json.loads(raw)
    return loaded


def analyze(rows, data):
    by_name = {}
    by_path = {r['path']: r for r in rows}
    for row in rows:
        for name in set([Path(row['path']).name] + row['elf']['soname']):
            by_name.setdefault(name, []).append(row['path'])
    for alias in data['aliases']:
        by_name.setdefault(Path(alias['path']).name, []).append(alias['resolved_target'])
    allowed_dirs = {str(Path(p).parent) for p in by_path}
    allowed_paths = set(by_path) | {x['path'] for x in data['aliases']}
    unknowns = []
    for row in rows:
        relations = []
        for needed in row['elf']['needed']:
            candidates = sorted(set(by_name.get(needed, [])))
            relations.append({'needed': needed, 'finite_candidates': candidates,
                              'loaded_origin': 'UNOBSERVED', 'ambiguous': len(candidates) > 1})
            if not candidates:
                unknowns.append({'path': row['path'], 'unresolved_needed': needed})
        row['needed_candidates'] = relations
        for kind in ('rpath', 'runpath'):
            for value in row['elf'][kind]:
                for part in value.split(':'):
                    expanded = part.replace('${ORIGIN}', str(Path(row['path']).parent))
                    expanded = expanded.replace('$ORIGIN', str(Path(row['path']).parent))
                    normalized = os.path.normpath(expanded)
                    # No filesystem search, recursive resolution, cache lookup, or additions.
                    if not part or '$' in expanded or not os.path.isabs(expanded) or normalized not in allowed_dirs:
                        unknowns.append({'path': row['path'], 'unresolved_search_path': part, 'tag': kind})
        for interp in row['interpreter']:
            if interp not in allowed_paths:
                unknowns.append({'path': row['path'], 'unresolved_interpreter': interp})
    return unknowns


def reused_runtime(source, data):
    files = {}
    def add(pin):
        path = pin['path']
        value = {'bytes': pin['bytes'], 'sha256': pin['sha256']}
        require(path not in files or files[path] == value, 'reused_pin_conflict')
        files[path] = value
    for pin in source['host']['stdlib_files'] + source['host']['mapped_system_files']:
        add(pin)
    add(source['host']['python'])
    for pin in data['candidates']:
        if 'installed' not in pin['roles']:
            add(pin)
    return files


def save(report):
    # Single fresh file only; partial is charged and preserved on a failed write.
    check(ABSOLUTE_END)
    payload = json.dumps(report, sort_keys=True, separators=(',', ':')).encode() + b'\n'
    require(len(payload) <= MAX_REPORT, 'report_size_cap')
    require(not OUTPUT.exists(), 'output_exists')
    pending = OUTPUT.with_name(OUTPUT.name + '.pending')
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            check(ABSOLUTE_END)
            offset += os.write(fd, payload[offset:offset+65536])
        os.fsync(fd)
    finally:
        os.close(fd)
    check(ABSOLUTE_END)
    os.rename(pending, OUTPUT)
    directory = os.open(OUTPUT.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    check(ABSOLUTE_END)


def main():
    require(len(sys.argv) == 4 and sys.argv[1] == '--inspect-reviewed-static-only', 'explicit_static_mode_required')
    require(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode,
            'required_I_B_S_flags')
    require(os.environ.get('RUNTIME_STATIC_OUTPUT_RESERVATION_BYTES') == '1048576',
            'parent_1MiB_storage_reservation_required')
    require(len(sys.argv[2]) == 64 and len(sys.argv[3]) == 64, 'freeze_arguments')
    os.sched_setaffinity(0, {3})
    os.setpriority(os.PRIO_PROCESS, 0, 19)
    resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_REPORT, MAX_REPORT))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGALRM, hard_end)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, ABSOLUTE_END-time.monotonic()))
    source_raw = bounded(__file__, 32 * 1024)
    require(hashlib.sha256(source_raw).hexdigest() == sys.argv[2], 'source_hash')
    data = load_allowlist(sys.argv[3])
    require(Path(sys.executable).resolve() == Path(data['host_python']['path']), 'host_python_origin')
    host_root = str(Path(data['host_python']['path']).parent.parent)
    require(all(p in [host_root+'/lib/python312.zip', host_root+'/lib/python3.12',
                      host_root+'/lib/python3.12/lib-dynload'] for p in sys.path), 'host_sys_path')
    for module in tuple(sys.modules.values()):
        origin = getattr(module, '__file__', None)
        require(origin is None or str(Path(origin).resolve()).startswith(host_root+'/lib/python3.12/')
                or str(Path(origin).resolve()) == str(HERE/'inspect_runtime.py'), 'host_module_origin')
    host_fd, _ = verify_open(data['host_python'])
    os.close(host_fd)
    source = source_inventories(data)
    pins = data['candidates']
    require(1 <= len(pins) <= 80 and len({p['path'] for p in pins}) == len(pins), 'candidate_count')
    require(len(pins) == data['candidate_count'] and
            sum(p['bytes'] for p in pins) == data['candidate_bytes'] <= 384*1024*1024,
            'candidate_total_bytes')
    require(all(0 < p['bytes'] <= 128*1024*1024 for p in pins), 'candidate_per_file_bytes')
    require(len(data['aliases']) <= 32, 'alias_count')
    pin_paths = {p['path'] for p in pins}
    for alias in data['aliases']:
        check()
        p = Path(alias['path'])
        require(p.is_symlink() and os.readlink(p) == alias['literal_target'] and
                str(p.resolve(strict=True)) == alias['resolved_target'] and
                alias['resolved_target'] in pin_paths, 'alias_changed_or_unpinned')
    tool = next(p for p in pins if p['path'] == data['tool_path'])
    tool_fd, tool_identity = verify_open(tool)
    rows = []
    error = None
    try:
        # Exact sequence: tool first, finite tool dependencies next, application candidates last.
        for pin in pins:
            check()
            rows.append(inspect(pin, tool_fd))
        require(identity(os.fstat(tool_fd)) == tool_identity, 'tool_changed')
    except BaseException as exc:
        error = type(exc).__name__ + ':' + str(exc)[:512]
    finally:
        for entry in LEDGER:
            cleanup(entry)
        os.close(tool_fd)
    check(ABSOLUTE_END)
    unknowns = analyze(rows, data)
    complete = error is None and not unknowns and len(rows) == len(pins)
    report = {
        'schema': 'rc3-dev3-static-runtime-inspection-v1',
        'status': 'STATIC_CANDIDATES_CLOSED_NOT_RUNTIME_QUALIFIED' if complete else 'INCOMPLETE',
        'actual_run_authorized': False, 'inference_calls': 0,
        'source_sha256': sys.argv[2], 'allowlist_sha256': sys.argv[3],
        'source_inventory_pins': data['inventories'],
        'rows': rows, 'unknowns': unknowns, 'error': error,
        'runtime_files': reused_runtime(source, data), 'runtime_aliases': data['aliases'],
        'controller_composition': data['controller_composition'],
        'known_ambiguities': data['known_ambiguities'],
        'limits': {'absolute_seconds': 60, 'cleanup_reserve_seconds': 10,
                   'cpu_affinity': [3], 'nice': 19, 'per_process_AS_bytes': LIMIT,
                   'owned_sampled_RSS_limit_bytes': LIMIT,
                   'owned_sampled_RSS_max_bytes': OWNED_RSS_MAX,
                   'stdout_bytes': TOTAL_IO[0], 'stderr_bytes': TOTAL_IO[1],
                   'report_cap_bytes': MAX_REPORT, 'reserved_output_bytes': 1048576},
        'ownership': [{'path': e['path'], 'original_pid': e['original'].pid,
                       'original_waited': e['waited'], 'returncode': e['returncode'],
                       'identity_observation_failed': e['observation_failed']} for e in LEDGER],
        'remaining_gates': data['remaining_gates'],
        'elapsed_seconds': time.monotonic()-START,
    }
    require(all(e['waited'] for e in LEDGER), 'unwaited_original')
    save(report)
    return 0 if complete else 2


if __name__ == '__main__':
    try:
        code = main()
    except BaseException as exc:
        for retained in LEDGER:
            cleanup(retained)
        os.write(2, ('STATIC_INVENTORY_REFUSED:' + type(exc).__name__ + ':' + str(exc)[:512] + '\n').encode())
        code = 2
    sys.exit(code)
