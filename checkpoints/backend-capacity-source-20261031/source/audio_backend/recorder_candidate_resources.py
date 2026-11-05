"""Fail-closed, local-only resource proof for the optional finite-plan drain.

All primary/backend access is observation-only. This module does not create a
cgroup, change a resource limit, load a model, or control an external service.
Only the original optional Popen and its subreaper-owned children are controlled.
Sampling is an observation, not a hard 6 GiB RSS guarantee.
"""
from __future__ import annotations

import ast
import ctypes
import hashlib
import json
import tempfile
from dataclasses import asdict
import ipaddress
import os
from pathlib import Path, PurePosixPath
import selectors
import shutil
import signal
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

import psutil

from .fragment_model_guard import Identity, ResourceLimit, descendants, process_table, rss_records

GiB = 1024 ** 3
MiB = 1024 ** 2
HARD_CAP = 10 * GiB
TARGET = 6 * GiB
HEADROOM = 2 * GiB
OPTIONAL_ENVELOPE = int(3.5 * GiB) + 64 * MiB
DISK_RESERVE = int(5.5 * GiB)
_PENDING = []  # Never discard an unresolved original Popen handle.
_PENDING_SIGNALS = {}  # Original handlers stay deferred for that full lifetime.


class CandidateResourceBlocked(ResourceLimit):
    def __init__(self, reason, prerequisite='Use a directly visible strict CPU backend, a verified <=10 GiB zero-swap memory boundary, and the fixed pinned CPU candidate runtime.'):
        self.reason, self.prerequisite = reason, prerequisite
        super().__init__('candidate_resource_blocked: ' + reason + ' ' + prerequisite)


def require(condition, reason):
    if not condition:
        raise CandidateResourceBlocked(reason)


def endpoint(origin):
    try:
        parsed = urlsplit(origin)
        require(parsed.scheme == 'http' and not parsed.username and not parsed.password and
                parsed.path in ('', '/') and not parsed.query and not parsed.fragment, 'unsupported_api_origin')
        host = parsed.hostname
        # No DNS or proxy interpretation. localhost is the one supported alias.
        address = ipaddress.ip_address('127.0.0.1' if host == 'localhost' else host)
        require(address.is_loopback, 'remote_primary_unobservable')
        return str(address), parsed.port or 80
    except (ValueError, TypeError) as exc:
        raise CandidateResourceBlocked('invalid_loopback_api_origin') from exc


def launcher_args(command, module):
    """Accept only the product's explicit -m or exact isolated bootstrap shape."""
    require(bool(command) and Path(command[0]).name.startswith('python'), 'unrecognized_listener_launcher')
    if len(command) >= 3 and command[1:3] == ['-m', module]:
        return command[3:]
    if len(command) >= 5 and command[1:5] == ['-I', '-B', '-m', module]:
        return command[5:]
    require(len(command) >= 5 and command[1:4] == ['-I', '-B', '-c'], 'proxy_or_unrecognized_listener')
    try:
        tree = ast.parse(command[4])
        root = tree.body[1].value.args[1].value
        require(type(root) is str and Path(root).is_absolute(), 'invalid_launcher_package_root')
        expected = ast.parse('import sys; sys.path.insert(0, ' + repr(root) + '); from ' + module + ' import main; main()')
        require(ast.dump(tree) == ast.dump(expected), 'proxy_or_unrecognized_bootstrap')
    except (SyntaxError, AttributeError, IndexError, TypeError) as exc:
        raise CandidateResourceBlocked('proxy_or_unrecognized_bootstrap') from exc
    return command[5:]


def option(arguments, name, default=None):
    matches = [i for i, value in enumerate(arguments) if value == name or value.startswith(name + '=')]
    require(len(matches) <= 1, 'ambiguous_launcher_argument_' + name)
    if not matches:
        return default
    index = matches[0]
    if '=' in arguments[index]:
        return arguments[index].split('=', 1)[1]
    require(index + 1 < len(arguments), 'missing_launcher_argument_' + name)
    return arguments[index + 1]


class LinuxReader:
    """Read-only Linux facts; overridable as an entire snapshot in model-free tests."""
    def __init__(self, proc=Path('/proc'), sys=Path('/sys'), dev=Path('/dev')):
        self.proc, self.sys, self.dev = Path(proc), Path(sys), Path(dev)
        self.owner = Identity.bind(os.getpid())

    def identity(self, pid):
        return Identity.bind(pid)

    def namespaces(self, pid):
        return tuple(os.readlink(self.proc / str(pid) / 'ns' / kind) for kind in ('pid', 'mnt', 'net', 'cgroup'))

    def command(self, pid):
        return (self.proc / str(pid) / 'cmdline').read_bytes().decode().rstrip('\0').split('\0')

    def parent(self, pid):
        return psutil.Process(pid).ppid()

    def mounts(self):
        result = []
        for line in (self.proc / 'self/mountinfo').read_text().splitlines():
            left, right = line.split(' - ', 1)
            fields, extra = left.split(), right.split()
            require(not any('\\' in value for value in fields[3:5]), 'escaped_mount_path_unsupported')
            result.append(dict(root=fields[3], path=fields[4], options=fields[5], type=extra[0]))
        return result

    def visibility(self):
        mounts = self.mounts()
        require(any(m['type'] == 'proc' and m['root'] == '/' and m['path'] == str(self.proc)
                    and 'hidepid=' not in m['options'] for m in mounts), 'full_procfs_unverified')
        return self.namespaces(self.owner.pid)

    def no_gpu(self):
        # This is corroboration of CPU-selected execution, NOT hardware absence.
        # /dev must be readable; missing sysfs is never interpreted as no GPU.
        names = {p.name for p in self.dev.iterdir()}
        require(not any(n == 'dri' or n == 'kfd' or n.startswith('nvidia') for n in names), 'accessible_accelerator_device')
        return 'readable_dev_no_accelerator_nodes'

    def physical_ram(self):
        return os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')

    def primary_source_files(self, pid):
        command = self.command(pid)
        # The strict supervisor's child always uses this isolated bootstrap.
        require(command[1:4] == ['-I', '-B', '-c'], 'primary_isolated_bootstrap_required')
        root = Path(ast.parse(command[4]).body[1].value.args[1].value)
        expected = Path(__file__).resolve().parent
        pins = {}
        for name in ('__main__.py', 'strict_supervisor.py', 'app.py',
                     'inference/analyzer.py', 'inference/native_diarization.py'):
            path = root / 'audio_backend' / name
            require(path.resolve(strict=True) == path and path.is_file(), 'primary_source_path_unverified')
            before = _file_signature(path)
            require(hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256((expected / name).read_bytes()).digest(),
                    'primary_cpu_source_differs')
            require(_file_signature(path) == before, 'primary_source_changed_during_read')
            pins[str(path)] = before
        return pins

    def memory(self):
        values = {}
        for line in (self.proc / 'meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            if key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'):
                parts = value.split()
                require(len(parts) == 2 and parts[1] == 'kB', 'invalid_meminfo_units')
                values[key] = int(parts[0]) * 1024
        require(set(values) == {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}, 'incomplete_meminfo')
        swaps = (self.proc / 'swaps').read_text().splitlines()
        require(bool(swaps) and swaps[0].startswith('Filename'), 'unreadable_swap_inventory')
        values['swap_devices'] = len([line for line in swaps[1:] if line.strip()])
        return values

    def _listeners(self, address, port):
        found = []
        wanted = ipaddress.ip_address(address)
        for name, family in (('tcp', socket.AF_INET), ('tcp6', socket.AF_INET6)):
            for line in (self.proc / 'net' / name).read_text().splitlines()[1:]:
                fields = line.split()
                require(len(fields) >= 10, 'unreadable_socket_table')
                raw, raw_port = fields[1].split(':')
                if fields[3] != '0A' or int(raw_port, 16) != port:
                    continue
                data = bytes.fromhex(raw)
                data = b''.join(data[i:i+4][::-1] for i in range(0, len(data), 4))
                host = ipaddress.ip_address(socket.inet_ntop(family, data))
                if host == wanted or host.is_unspecified or getattr(host, 'ipv4_mapped', None) == wanted:
                    # Wildcard and v4/v6 dual-stack ambiguity are deliberately refused.
                    require(host == wanted and host.is_loopback, 'wildcard_or_ambiguous_listener')
                    require(int(fields[9]) > 0, 'unreadable_listener_inode')
                    found.append(fields[9])
        require(len(found) == 1, 'missing_or_ambiguous_listener')
        return found[0]

    def listener(self, address, port):
        inode = self._listeners(address, port)
        owners = set()
        for directory in self.proc.iterdir():
            if not directory.name.isdigit():
                continue
            try:
                descriptors = list((directory / 'fd').iterdir())
            except FileNotFoundError:
                continue
            for descriptor in descriptors:
                try:
                    if os.readlink(descriptor) == 'socket:[' + inode + ']':
                        owners.add(int(directory.name))
                except FileNotFoundError:
                    continue
        require(len(owners) == 1, 'unreadable_or_ambiguous_socket_owner')
        identity = self.identity(owners.pop())
        require(self._listeners(address, port) == inode, 'listener_changed_during_read')
        require(self.identity(identity.pid) == identity, 'listener_identity_changed')
        return identity, inode

    def membership(self, pid):
        lines = (self.proc / str(pid) / 'cgroup').read_text().splitlines()
        values = [line[3:] for line in lines if line.startswith('0::')]
        require(len(values) == 1 and values[0].startswith('/') and '..' not in PurePosixPath(values[0]).parts,
                'cgroup_v2_membership_unreadable')
        return values[0]

    def cgroup_boundary(self, identities):
        mounts = [m for m in self.mounts() if m['type'] == 'cgroup2']
        if not mounts:
            return None
        require(len(mounts) == 1, 'ambiguous_cgroup_mount')
        mount = mounts[0]
        paths = [PurePosixPath(self.membership(i.pid)) for i in identities]
        common = PurePosixPath(os.path.commonpath([str(p) for p in paths]))
        root = PurePosixPath(mount['root'])
        require(common.is_relative_to(root), 'cgroup_membership_outside_mount')
        while common.is_relative_to(root):
            directory = Path(mount['path']) / common.relative_to(root)
            limit = (directory / 'memory.max').read_text().strip()
            if limit != 'max' and 0 < int(limit) <= HARD_CAP:
                result = dict(path=str(directory), membership=str(common), mount=mount)
                result.update(self.cgroup_values(result))
                return result
            if common == root:
                break
            common = common.parent
        return None

    def cgroup_values(self, boundary):
        directory = Path(boundary['path'])
        before = (directory / 'memory.max').read_text().strip()
        values = {name: int((directory / name).read_text().strip())
                  for name in ('memory.max', 'memory.current', 'memory.swap.max', 'memory.swap.current')}
        require(before == str(values['memory.max']) == (directory / 'memory.max').read_text().strip(),
                'cgroup_limit_changed_during_read')
        require(values['memory.max'] > 0 and values['memory.current'] >= 0, 'invalid_cgroup_memory')
        require(values['memory.swap.max'] == values['memory.swap.current'] == 0, 'cgroup_swap_not_zero')
        return values

    def table(self):
        return process_table()

    def rss(self, selected):
        return rss_records(selected)

    def disk(self, path):
        return shutil.disk_usage(path).free


class ResourceGate:
    def __init__(self, origin, reader, runtime_profile=None):
        self.runtime_profile = runtime_profile
        self.runtime_files = {}
        self.runtime_verified = False
        self.verification_command = None
        self.reader, self.address = reader, endpoint(origin)
        self.owner = reader.owner
        self.namespaces = reader.visibility()
        self.gpu_inventory = reader.no_gpu()
        self.primary, self.inode = reader.listener(*self.address)
        require(self.primary != self.owner, 'owner_is_primary')
        primary_command = reader.command(self.primary.pid)
        args = launcher_args(primary_command, 'audio_backend' if '-m' in primary_command[1:4] else 'audio_backend.__main__')
        require(option(args, '--device', 'cpu') == 'cpu' and
                option(args, '--asr-backend') == 'sensevoice' and
                option(args, '--diarization-backend') == 'nemotron' and
                option(args, '--overlap-backend', 'none') == 'none' and '--strict-primary' in args and
                option(args, '--instance-lock-fd') is not None and
                option(args, '--data-dir') is not None and Path(option(args, '--data-dir')).is_absolute() and
                option(args, '--host', '127.0.0.1') == self.address[0] and
                int(option(args, '--port', '8765')) == self.address[1], 'unsupported_primary_cpu_launcher')
        allowed = {
            '--instance-lock-fd', '--data-dir', '--model-dir', '--host', '--port', '--device',
            '--asr-backend', '--diarization-backend', '--overlap-backend', '--native-diarization-model',
            '--native-diarization-library', '--recording-speaker-model', '--language', '--threads',
            '--native-diarization-threads', '--memory-limit-gib', '--window-seconds', '--context-seconds',
            '--speaker-support-max-records', '--recording-capture-mib', '--recording-total-mib',
            '--lexical-evidence-session-mib', '--strict-primary', '--strict-primary-policy',
            '--strict-payload-source-storage', '--strict-primary-lifetime-records', '--strict-primary-receipt-records',
            '--native-owned-support', '--native-owned-cells', '--native-owned-core-seams',
            '--native-owned-bounded-core-support', '--lexical-evidence', '--recording-speaker-links',
            '--speaker-support-review', '--recording-primary-observation-policy',
            '--recording-primary-sampling-policy', '--recording-primary-observation-interval-seconds',
            '--reviewed-primary-persons', '--verified-identity-policy'}
        flags = [arg.split('=', 1)[0] for arg in args if arg.startswith('--')]
        require(len(flags) == len(set(flags)) and set(flags).issubset(allowed), 'unapproved_primary_launcher_option')
        supervisor = reader.identity(reader.parent(self.primary.pid))
        supervisor_args = launcher_args(reader.command(supervisor.pid), 'audio_backend.strict_supervisor')
        require(option(supervisor_args, '--data-dir') == option(args, '--data-dir'), 'primary_supervisor_binding_mismatch')
        self.project_roots = (supervisor, self.primary)
        require(self.owner not in self.project_roots, 'optional_owner_is_primary_supervisor')
        self.commands = {i.pid: reader.command(i.pid) for i in self.project_roots}
        self.runtime_files.update(reader.primary_source_files(self.primary.pid))
        self.boundary = reader.cgroup_boundary((*self.project_roots, self.owner))
        self.memberships = {}
        if self.boundary:
            self.mode = 'common_cgroup_v2'
            for identity in (*self.project_roots, self.owner):
                self.memberships[identity.pid] = reader.membership(identity.pid)
        else:
            self.mode = 'whole_guest_physical_ram'
        self.memory_total = reader.memory()['MemTotal']
        require(self.memory_total == reader.physical_ram(), 'physical_ram_sources_disagree')
        self.known_project, self.known_optional = {}, {}
        self.peak_combined = 0
        self.proof = {}
        self.sample(admission=True)

    @property
    def primary_identity(self):
        return asdict(self.primary)

    def validate_command(self, command, output_dir):
        if self.verification_command is not None:
            require(command == self.verification_command, 'unrecognized_cpu_verifier_command')
            return
        require(self.runtime_verified and self.runtime_profile is not None, 'cpu_runtime_not_verified')
        from . import recorder_candidate_postphase as postphase
        postphase.verify_policy(self.runtime_profile)
        # Factory validates the product-owned bootstrap and exact option order.
        def value(name):
            require(command.count(name) == 1, 'invalid_candidate_command_option')
            return command[command.index(name) + 1]
        require(Path(value('--output-dir')) == output_dir / 'result', 'candidate_output_scope_mismatch')
        prepared = postphase.document(output_dir / 'preflight.json')
        require(type(prepared) is dict and type(prepared.get('plan')) is dict,
                'candidate_preflight_plan_missing')
        expected_plan = postphase.fingerprint(prepared['plan'])
        require(value('--expected-plan-sha256') == expected_plan, 'candidate_dispatch_plan_mismatch')
        expected = postphase.command(self.runtime_profile, Path(value('--job-dir')), Path(value('--export-dir')),
                                     Path(value('--output-dir')),
                                     int(value('--deadline-seconds')) if value('--deadline-seconds').isdigit() else float(value('--deadline-seconds')),
                                     expected_plan_sha256=expected_plan)
        require(command == expected, 'candidate_command_not_product_owned')

    def _original_exited(self, identity, process, optional):
        """Only the retained, unreaped original can supply terminal evidence."""
        if process is None:
            return False
        require(isinstance(process, subprocess.Popen) and process.pid == identity.pid and
                process.returncode is None, 'optional_original_handle_not_unreaped')
        require(self.reader.identity(identity.pid) == identity, 'optional_original_identity_changed')
        if not _exited(process):
            return False
        require(self.reader.identity(identity.pid) == identity, 'optional_original_identity_changed')
        require(not (set(optional) - {identity.pid}) and
                not [child for child in _children() if child.pid != identity.pid],
                'optional_descendants_outlived_leader')
        return True

    def sample(self, optional_identity=None, *, admission=False, original_process=None):
        try:
            return self._sample(optional_identity, admission=admission, original_process=original_process)
        except CandidateResourceBlocked:
            raise
        except (OSError, ValueError, psutil.Error, ResourceLimit) as exc:
            raise CandidateResourceBlocked('resource_visibility_or_read_failed: ' + str(exc)) from exc

    def _sample(self, optional_identity, *, admission, original_process=None, exit_retry=False):
        reader = self.reader
        require(reader.visibility() == self.namespaces and reader.no_gpu() == self.gpu_inventory,
                'resource_visibility_or_gpu_inventory_changed')
        require(reader.listener(*self.address) == (self.primary, self.inode), 'primary_listener_changed')
        for identity in (*self.project_roots, self.owner):
            require(reader.identity(identity.pid) == identity and reader.namespaces(identity.pid) == self.namespaces,
                    'primary_or_owner_identity_namespace_changed')
        for pid, command in self.commands.items():
            require(reader.command(pid) == command, 'primary_launcher_changed')
        for path, signature in self.runtime_files.items():
            require(_file_signature(Path(path)) == signature, 'verified_cpu_runtime_file_changed')
        memory = reader.memory()
        require(reader.physical_ram() == self.memory_total, 'physical_ram_changed')
        require(memory['MemTotal'] == self.memory_total, 'guest_memory_changed')
        if self.mode == 'whole_guest_physical_ram':
            require(memory['SwapTotal'] == memory['SwapFree'] == 0 and memory['swap_devices'] == 0,
                    'guest_memory_or_swap_changed')
        require(self.mode != 'whole_guest_physical_ram' or 0 < self.memory_total <= HARD_CAP, 'whole_guest_ram_exceeds_10gib')
        table = reader.table()
        project = descendants(self.project_roots, table, self.known_project.values())
        optional = descendants((optional_identity,) if optional_identity else (), table, self.known_optional.values())
        self.known_project.update(project)
        self.known_optional.update(optional)
        require(not set(project).intersection(optional), 'primary_optional_trees_overlap')
        # Include the owner wrapper as well; never put it in cleanup targets.
        selected = {**project, **optional, self.owner.pid: self.owner}
        exited_original = None
        for identity in tuple(selected.values()):
            terminal_original = (identity == optional_identity and
                                 self._original_exited(identity, original_process, optional))
            if not terminal_original:
                try:
                    require(reader.namespaces(identity.pid) == self.namespaces, 'descendant_namespace_changed')
                except FileNotFoundError:
                    # Exit can occur between WNOWAIT and the namespace read. A
                    # missing path alone never proves exit, reuse, or zero RSS.
                    if identity != optional_identity or not self._original_exited(identity, original_process, optional):
                        raise
                    require(not exit_retry, 'inconsistent_original_exit_observation')
                    # Do not return a mixture of pre-exit and terminal readings.
                    # Rebuild one complete sample; all required roots and caps
                    # are checked again, and only this exact original may retire.
                    return self._sample(optional_identity, admission=admission,
                                        original_process=original_process, exit_retry=True)
            if terminal_original:
                selected.pop(identity.pid)
                exited_original = asdict(identity)
        if self.boundary:
            require(self.boundary['mount'] in reader.mounts(), 'cgroup_mount_changed')
            values = reader.cgroup_values(self.boundary)
            require(values['memory.max'] == self.boundary['memory.max'], 'cgroup_limit_changed')
            for identity in selected.values():
                membership = reader.membership(identity.pid)
                require(PurePosixPath(membership).is_relative_to(PurePosixPath(self.boundary['membership'])),
                        'process_migrated_outside_cgroup')
                if identity.pid in self.memberships:
                    require(membership == self.memberships[identity.pid], 'pinned_process_cgroup_migrated')
                self.memberships[identity.pid] = membership
            remaining = values['memory.max'] - values['memory.current']
            require(remaining >= HEADROOM + (OPTIONAL_ENVELOPE if admission else 0), 'insufficient_cgroup_headroom')
        else:
            remaining = None
        records = reader.rss(selected)
        require({r['pid'] for r in records}.issuperset(i.pid for i in (*self.project_roots, self.owner)),
                'primary_or_owner_disappeared_during_rss_read')
        for identity in (*self.project_roots, self.owner):
            require(reader.identity(identity.pid) == identity, 'root_identity_changed_after_rss')
        combined = sum(row['rss'] for row in records)
        own_rss = sum(row['rss'] for row in records if row['pid'] in optional)
        require(combined + (OPTIONAL_ENVELOPE if admission else 0) <= TARGET, 'combined_6gib_target_exceeded')
        require(own_rss <= OPTIONAL_ENVELOPE, 'optional_3_5gib_envelope_exceeded')
        require(memory['MemAvailable'] >= HEADROOM + (OPTIONAL_ENVELOPE if admission else 0), 'insufficient_host_headroom')
        if self.boundary:
            after = reader.cgroup_values(self.boundary)
            require(after['memory.max'] == self.boundary['memory.max'], 'cgroup_limit_changed_during_sample')
            for identity in selected.values():
                require(reader.membership(identity.pid) == self.memberships[identity.pid],
                        'cgroup_migration_during_sample')
            require(after['memory.max'] - after['memory.current'] >= HEADROOM + (OPTIONAL_ENVELOPE if admission else 0),
                    'cgroup_headroom_changed_during_sample')
        require(reader.listener(*self.address) == (self.primary, self.inode), 'inconsistent_primary_observation')
        self.peak_combined = max(self.peak_combined, combined)
        self.proof = dict(schema='candidate-local-resource-proof-v1', mode=self.mode,
                          hard_memory_cap_bytes=HARD_CAP, physical_ram_bytes=self.memory_total,
                          no_swap=True, gpu_hardware_absence_verified=False,
                          cpu_execution_profile='handy-moss-q8_0-cpu-experimental-v1',
                          runtime_verified=self.runtime_verified,
                          accessible_accelerator_nodes=False, primary_identity=self.primary_identity,
                          primary_observation_only=True, exited_optional_original=exited_original,
                          combined_rss_bytes=combined,
                          optional_rss_bytes=own_rss, peak_combined_rss_bytes=self.peak_combined,
                          host_available_bytes=memory['MemAvailable'], cgroup_remaining_bytes=remaining,
                          optional_admission_reserve_bytes=OPTIONAL_ENVELOPE,
                          target_bytes=TARGET, sampled_only=True)
        return dict(self.proof)


def _file_signature(path):
    require(path.resolve(strict=True) == path and not path.is_symlink(), 'resource_pin_path_changed')
    info = path.stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def preflight(api_origin, *, runtime_profile=None, cancel=lambda: False, reader=None):
    try:
        require(not cancel(), 'optional_cancelled_before_admission')
        if reader is not None:
            # Private injection seam for model-free snapshots; not a CLI option.
            return ResourceGate(api_origin, reader, runtime_profile)
        require(type(runtime_profile) is dict, 'fixed_cpu_runtime_profile_required')
        from . import recorder_candidate_postphase as postphase
        from . import moss_td_cpp_assets as assets
        postphase.verify_policy(runtime_profile)
        gate = ResourceGate(api_origin, LinuxReader(), runtime_profile)
        code_paths = list((postphase.ROOT / 'audio_backend').rglob('*.py'))
        code_paths += list((postphase.ROOT / 'audio_backend/profiles').glob('*.json'))
        code_paths += [Path(runtime_profile['python_executable']), assets.MANIFEST_PATH]
        venv = Path(runtime_profile['settings']['python']).parent.parent / 'pyvenv.cfg'
        if venv.exists(): code_paths.append(venv)
        gate.runtime_files.update({str(p): _file_signature(p) for p in code_paths})
        temporary = tempfile.mkdtemp(prefix='candidate-resource-verify-')
        root = Path(temporary)
        try:
            bootstrap = ('import sys; sys.pycache_prefix = ' + repr(str(root / 'unused-pycache')) + '; '
                'sys.path.insert(0, ' + repr(str(postphase.ROOT)) + '); '
                'from audio_backend.moss_handy_profile import NAME, profile, runtime_site_paths, verify_runtime, verify_model; '
                'sys.path.extend(str(p) for p in runtime_site_paths()); '
                'chosen, paths, hashes = verify_runtime(NAME); verify_model(sys.argv[1]); '
                'import json; from pathlib import Path; '
                'files = list(paths.values()) + [Path(sys.argv[1])]; '
                'signature = lambda p: [p.stat().st_dev, p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns]; '
                'print(json.dumps(dict(profile=chosen.sha256, backend=chosen.payload()["backend"], '
                'files={str(p): signature(p) for p in files})))')
            gate.verification_command = [runtime_profile['settings']['python'], '-I', '-B', '-S', '-c',
                                         bootstrap, runtime_profile['settings']['model']]
            result = supervise(gate.verification_command, gate=gate, output_dir=root,
                               deadline_seconds=120, artifact_cap=MiB, cancel=cancel)
            require(result['status'] == 'completed' and result['cleanup']['cleanup_verified'],
                    'cpu_runtime_verifier_failed_' + result.get('reason', str(result['exit_code'])))
            receipt = json.loads((root / 'supervisor.stdout.txt').read_text())
            require(receipt['profile'] == runtime_profile['profile_sha256'] and receipt['backend'] == 'cpu',
                    'cpu_runtime_verifier_identity_mismatch')
            gate.runtime_files.update(receipt['files'])
            require(type(gate.runtime_files) is dict and 1 <= len(gate.runtime_files) <= 1024,
                    'cpu_runtime_verifier_inventory_invalid')
        finally:
            if pending_cleanup():
                for item in _PENDING: item['temporary'] = temporary
            else:
                shutil.rmtree(temporary)
        gate.verification_command = None
        gate.runtime_verified = True
        postphase.verify_policy(runtime_profile)
        gate.sample(admission=True)
        require(not cancel(), 'optional_cancelled_after_verification')
        return gate
    except CandidateResourceBlocked:
        raise
    except (OSError, ValueError, TypeError, KeyError, psutil.Error, ResourceLimit) as exc:
        raise CandidateResourceBlocked('resource_admission_unverifiable: ' + str(exc)) from exc


def _subreaper(value=None):
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
        raise CandidateResourceBlocked('subreaper_state_unreadable')
    if value is not None:
        require(libc.prctl(36, int(value), 0, 0, 0) == 0, 'subreaper_ownership_unavailable')
        require(_subreaper() == int(value), 'subreaper_ownership_unverified')
    return previous.value


def _children():
    return psutil.Process().children(recursive=True)


def _exited(process):
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def _cleanup(process, excluded, timeout=5., *, retain=True, original_reaped=False):
    """Dedicated subreaper only; prior uncertainty needs positive later evidence."""
    waited, remaining = original_reaped, []
    end = time.monotonic() + timeout
    # Cancellation of the known original does not depend on a successful PPid scan.
    if not waited:
        try:
            require(process.pid not in excluded, 'cleanup_would_target_primary')
            if not _exited(process):
                os.kill(process.pid, signal.SIGKILL)
        except (OSError, ResourceLimit):
            pass  # The bounded loop can establish positive later evidence.
    while time.monotonic() < end:
        try:
            remaining = []
            for child in reversed(_children()):
                require(child.pid not in excluded, 'cleanup_would_target_primary')
                if child.pid == process.pid and not waited:
                    continue
                identity = Identity.bind(child.pid)
                if child.status() != psutil.STATUS_ZOMBIE:
                    if child.create_time() == identity.created:
                        child.kill()
                    remaining.append(child.pid)
                elif child.ppid() == os.getpid():
                    try:
                        os.waitpid(child.pid, os.WNOHANG)
                    except ChildProcessError:
                        pass  # A subsequent complete child scan must confirm absence.
            if not waited:
                if not _exited(process):
                    os.kill(process.pid, signal.SIGKILL)
                elif not remaining and all(c.pid == process.pid for c in _children()):
                    # The only positive original wait is on the original handle.
                    process.wait(timeout=max(.01, end - time.monotonic()))
                    waited = True
            if waited:
                remaining = [child.pid for child in _children()]
                if not remaining:
                    return dict(cleanup_verified=True, original_handle_waited_and_reaped=True,
                                remaining_live_owned=[])
        except psutil.NoSuchProcess:
            pass
        except (OSError, psutil.Error, ResourceLimit, subprocess.TimeoutExpired):
            break
        time.sleep(.02)
    if retain:
        item = next((item for item in _PENDING if item.get('process') is process), None)
        if item is None:
            item = dict(process=process, excluded=set(excluded), prior_subreaper=None)
            _PENDING.append(item)
        item['original_reaped'] = waited
    return dict(cleanup_verified=False, original_handle_waited_and_reaped=waited,
                remaining_live_owned=remaining, ownership_uncertain=True)


def pending_cleanup():
    """The dedicated CLI must remain alive while this is true."""
    return bool(_PENDING)


def retry_pending_cleanup():
    """One bounded, owned-only cleanup attempt. Never starts optional work."""
    for item in list(_PENDING):
        process = item.get('process')
        if process is None:
            continue  # Unknown spawn without an original handle is not invented as clean.
        if not item.get('reaped'):
            result = _cleanup(process, item['excluded'], timeout=1., retain=False,
                              original_reaped=item.get('original_reaped', False))
            item['original_reaped'] = result['original_handle_waited_and_reaped']
            if not result['cleanup_verified']:
                continue
            item['reaped'] = True
        try:
            prior = item.get('prior_subreaper')
            if prior is not None:
                _subreaper(prior)
            temporary = item.get('temporary')
            if temporary is not None:
                shutil.rmtree(temporary)
        except (OSError, CandidateResourceBlocked):
            continue
        _PENDING.remove(item)
    if not _PENDING:
        for signum, previous in list(_PENDING_SIGNALS.items()):
            signal.signal(signum, previous)
            del _PENDING_SIGNALS[signum]
    return not _PENDING


def _artifact_bytes(directory):
    total = 0
    for parent, dirs, files in os.walk(directory, followlinks=False):
        for name in (*dirs, *files):
            path = Path(parent) / name
            require(not path.is_symlink(), 'optional_artifact_symlink')
        for name in files:
            try:
                total += (Path(parent) / name).stat().st_size
            except FileNotFoundError:
                pass
    return total


def supervise(command, *, gate, output_dir, deadline_seconds, cancel=lambda: False,
              artifact_cap=64 * MiB):
    """Run one fixed optional command in a dedicated single-threaded CLI owner.

    A blocked/failed result is never evidence that a durable intent is fresh.
    The caller must retain its uncertain ledger entry if cleanup is not proven.
    """
    process, pipes, logs = None, selectors.DefaultSelector(), []
    spawn_attempted = False
    prior_subreaper, prior_signals = None, {}
    received = []
    cleanup = dict(cleanup_verified=True, original_handle_waited_and_reaped=False, remaining_live_owned=[])
    result = dict(status='resource_blocked', exit_code=None, cleanup=cleanup, resource_proof=dict(gate.proof))
    try:
        require(not _PENDING, 'previous_optional_cleanup_unresolved')
        require(gate.owner == Identity.bind(os.getpid()), 'resource_gate_owner_changed')
        require(threading.current_thread() is threading.main_thread() and threading.active_count() == 1,
                'dedicated_single_threaded_owner_required')
        require(not _children(), 'dedicated_owner_has_existing_children')
        require(type(command) is list and command and all(type(v) is str for v in command), 'invalid_optional_command')
        gate.validate_command(command, Path(output_dir))
        require(type(deadline_seconds) in (int, float) and 0 < deadline_seconds <= 3060, 'invalid_optional_deadline')
        require(type(artifact_cap) is int and 0 < artifact_cap <= 64 * MiB, 'invalid_artifact_cap')
        directory = Path(output_dir)
        require(directory.is_absolute() and directory.resolve(strict=True) == directory and directory.is_dir(),
                'invalid_optional_output_directory')
        info = directory.stat()
        directory_identity = (info.st_dev, info.st_ino)
        require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, 'optional_output_not_private')
        require(gate.reader.disk(directory) - artifact_cap >= DISK_RESERVE, 'insufficient_optional_disk_reserve')
        gate.sample(admission=True)
        require(not cancel(), 'optional_cancelled_before_spawn')
        # Preserve the original process-local state. No system/global settings change.
        prior_subreaper = _subreaper(1)
        for signum in (signal.SIGINT, signal.SIGTERM):
            prior_signals[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda number, frame: received.append(number) if not received else None)
        for name in ('stdout', 'stderr'):
            fd = os.open(directory / ('supervisor.' + name + '.txt'),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            logs.append(os.fdopen(fd, 'wb', buffering=0))
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1',
                           TRANSFORMERS_OFFLINE='1', CUDA_VISIBLE_DEVICES='')
        from .moss_handy_profile import check_loader_context
        check_loader_context()
        # Final read-only recheck occurs after preparation and before the only Popen.
        require(_artifact_bytes(directory) + 256 * 1024 <= artifact_cap, 'optional_artifact_admission_exceeded')
        gate.sample(admission=True)
        require(not received and not cancel(), 'optional_cancelled_before_spawn')
        spawn_attempted = True
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
                                   env=environment)
        identity = Identity.bind(process.pid)
        sizes = {}
        for index, stream in enumerate((process.stdout, process.stderr)):
            os.set_blocking(stream.fileno(), False)
            pipes.register(stream, selectors.EVENT_READ, index)
            sizes[index] = 0
        end = time.monotonic() + deadline_seconds
        while True:
            require(not received and not cancel(), 'optional_cancelled')
            require(time.monotonic() < end, 'optional_deadline_exceeded')
            require(directory.resolve(strict=True) == directory and
                    (directory.stat().st_dev, directory.stat().st_ino) == directory_identity, 'optional_output_directory_changed')
            gate.sample(identity, original_process=process)
            require(gate.reader.disk(directory) >= DISK_RESERVE, 'optional_disk_reserve_breached')
            require(_artifact_bytes(directory) <= artifact_cap, 'optional_artifact_cap_exceeded')
            for key, _ in pipes.select(.05):
                raw = os.read(key.fileobj.fileno(), 65536)
                if not raw:
                    pipes.unregister(key.fileobj)
                    continue
                count = min(len(raw), 128 * 1024 - sizes[key.data])
                if count:
                    logs[key.data].write(raw[:count])
                    sizes[key.data] += count
                require(count == len(raw), 'optional_log_cap_exceeded')
            if _exited(process):
                # A naturally exited leader with live/adopted children is not success.
                require(not [c for c in _children() if c.pid != process.pid], 'optional_descendants_outlived_leader')
                gate.sample(identity, original_process=process)
                if pipes.get_map():
                    continue  # Drain bounded pipes through EOF before reading the receipt.
                result['status'] = 'completed'
                break
    except (CandidateResourceBlocked, OSError, psutil.Error, ResourceLimit, ValueError) as exc:
        result['reason'] = getattr(exc, 'reason', type(exc).__name__)
    except BaseException as exc:
        result['status'], result['reason'] = 'failed', type(exc).__name__
    finally:
        if process is None and spawn_attempted:
            try:
                remaining = [child.pid for child in _children()]
                if remaining:
                    result['cleanup'] = dict(cleanup_verified=False, original_handle_waited_and_reaped=False,
                                             remaining_live_owned=remaining, ownership_uncertain=True)
                    result['status'], result['reason'] = 'failed', 'spawn_outcome_unverified'
                    _PENDING.append(dict(process=None, prior_subreaper=prior_subreaper))
            except (OSError, psutil.Error):
                result['cleanup'] = dict(cleanup_verified=False, original_handle_waited_and_reaped=False,
                                         remaining_live_owned=None, ownership_uncertain=True)
                _PENDING.append(dict(process=None, prior_subreaper=prior_subreaper))
        if process is not None:
            excluded = {gate.owner.pid, *(identity.pid for identity in gate.project_roots), *gate.known_project}
            cleanup = _cleanup(process, excluded)
            result['cleanup'] = cleanup
            for item in _PENDING:
                if item.get('process') is process:
                    item['prior_subreaper'] = prior_subreaper
            if cleanup['original_handle_waited_and_reaped']:
                result['exit_code'] = process.returncode
            if not cleanup['cleanup_verified']:
                result['status'], result['reason'] = 'failed', 'optional_cleanup_unverified'
            elif result['status'] == 'completed' and result['exit_code'] != 0:
                result['status'] = 'failed'
        for key in list(pipes.get_map().values()):
            key.fileobj.close()
        pipes.close()
        for stream in logs:
            stream.close()
        if _PENDING:
            # Keep the already-installed nonthrowing handlers active through the
            # handoff to the exceptional cleanup-only wait. No default-handler gap.
            for signum, previous in prior_signals.items():
                _PENDING_SIGNALS.setdefault(signum, previous)
        else:
            for signum, previous in prior_signals.items():
                signal.signal(signum, previous)
        if prior_subreaper is not None and result['cleanup']['cleanup_verified']:
            try:
                _subreaper(prior_subreaper)
            except CandidateResourceBlocked:
                result['status'], result['reason'] = 'failed', 'subreaper_restore_unverified'
        result['resource_proof'] = dict(gate.proof)
    return result
