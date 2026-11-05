"""Model-free snapshots and tiny, isolated, owned-process cleanup controls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from audio_backend import recorder_candidate_resources as r


class Snapshot:
    def __init__(self, cgroup=False):
        self.owner = r.Identity(10, 1.)
        self.primary = r.Identity(30, 3.)
        self.ns = ('pid:[1]', 'mnt:[1]', 'net:[1]', 'cgroup:[1]')
        self.identities = {10: self.owner, 20: r.Identity(20, 2.), 30: self.primary, 40: r.Identity(40, 4.)}
        self.commands = {
            30: ['python3', '-I', '-B', '-c', "import sys; sys.path.insert(0, '/pkg'); from audio_backend.__main__ import main; main()",
                 '--strict-primary', '--instance-lock-fd', '7', '--data-dir', '/data', '--device', 'cpu',
                 '--asr-backend', 'sensevoice', '--diarization-backend', 'nemotron', '--host', '127.0.0.1', '--port', '8765'],
            20: ['python3', '-I', '-B', '-m', 'audio_backend.strict_supervisor', 'serve', '--data-dir', '/data']}
        self.mem = dict(MemTotal=9*r.GiB, MemAvailable=8*r.GiB, SwapTotal=0, SwapFree=0, swap_devices=0)
        self.ram = 9*r.GiB
        self.inode = '456'
        self.gpu = False
        self.processes = {10: (1, 1.), 20: (1, 2.), 30: (20, 3.), 40: (10, 4.)}
        self.memory_by_pid = {10: 64*r.MiB, 20: 32*r.MiB, 30: r.GiB, 40: 128*r.MiB}
        self.member = {pid: '/limited/' + str(pid) for pid in self.identities}
        self.mount = dict(root='/', path='/sys/fs/cgroup', options='ro', type='cgroup2')
        self.cg = dict(path='/sys/fs/cgroup/limited', membership='/limited', mount=self.mount,
                       **{'memory.max': 9*r.GiB, 'memory.current': r.GiB, 'memory.swap.max': 0, 'memory.swap.current': 0}) if cgroup else None

    def visibility(self): return self.ns
    def no_gpu(self):
        r.require(not self.gpu, 'accessible_accelerator_device')
        return 'readable_dev_no_accelerator_nodes'
    def physical_ram(self): return self.ram
    def identity(self, pid):
        r.require(pid in self.identities, 'identity_unreadable')
        return self.identities[pid]
    def namespaces(self, pid): return self.ns
    def command(self, pid): return self.commands[pid]
    def parent(self, pid): return 20
    def primary_source_files(self, pid): return {}
    def listener(self, address, port): return self.primary, self.inode
    def cgroup_boundary(self, identities): return copy.deepcopy(self.cg)
    def cgroup_values(self, boundary):
        r.require(self.cg['memory.swap.max'] == self.cg['memory.swap.current'] == 0, 'cgroup_swap_not_zero')
        return dict(self.cg)
    def membership(self, pid): return self.member[pid]
    def mounts(self): return [self.mount]
    def memory(self): return dict(self.mem)
    def table(self): return dict(self.processes)
    def rss(self, selected):
        return [dict(pid=pid, created=identity.created, rss=self.memory_by_pid.get(pid, 0), status='running')
                for pid, identity in selected.items()]
    def disk(self, path): return 20*r.GiB


def gate(snapshot=None):
    return r.preflight('http://127.0.0.1:8765', reader=snapshot or Snapshot())


def test_guest_admission_labels_cpu_execution_not_hardware_absence():
    observed = gate()
    assert observed.proof['mode'] == 'whole_guest_physical_ram'
    assert observed.proof['gpu_hardware_absence_verified'] is False
    assert observed.proof['primary_observation_only'] is True
    assert observed.primary_identity == {'pid': 30, 'created': 3.}
    assert not hasattr(observed, 'terminate_owned')


@pytest.mark.parametrize('origin', ['https://127.0.0.1:8765', 'http://example.test:8765',
    'http://10.0.0.1:8765', 'http://127.0.0.1:8765/path', 'http://user@127.0.0.1:8765', 'http://127.0.0.1:8765?x=1'])
def test_remote_and_ambiguous_origins_block(origin):
    with pytest.raises(r.CandidateResourceBlocked):
        r.preflight(origin, reader=Snapshot())


@pytest.mark.parametrize('mutation', [
    lambda s: s.commands[30].__setitem__(4, 'import proxy; proxy.main()'),
    lambda s: s.commands[30].__setitem__(s.commands[30].index('cpu'), 'vulkan'),
    lambda s: s.commands[20].__setitem__(4, 'some_proxy'),
    lambda s: s.commands[20].__setitem__(-1, '/other'),
    lambda s: s.mem.__setitem__('SwapTotal', 1),
    lambda s: s.mem.__setitem__('swap_devices', 1),
    lambda s: setattr(s, 'gpu', True),
    lambda s: setattr(s, 'ram', 8*r.GiB),
    lambda s: (setattr(s, 'ram', 11*r.GiB), s.mem.__setitem__('MemTotal', 11*r.GiB)),
    lambda s: s.mem.__setitem__('MemAvailable', 5*r.GiB),
    lambda s: s.memory_by_pid.__setitem__(30, 3*r.GiB),
])
def test_preflight_negative_controls(mutation):
    snapshot = Snapshot(); mutation(snapshot)
    with pytest.raises(r.CandidateResourceBlocked): gate(snapshot)


@pytest.mark.parametrize('mutation', [
    lambda s: setattr(s, 'inode', 'changed'),
    lambda s: s.identities.__setitem__(30, r.Identity(30, 99.)),
    lambda s: s.identities.pop(30),
    lambda s: s.processes.pop(30),
    lambda s: s.mem.__setitem__('SwapFree', 1),
    lambda s: s.mem.__setitem__('MemAvailable', r.GiB),
    lambda s: setattr(s, 'gpu', True),
    lambda s: s.memory_by_pid.__setitem__(30, 6*r.GiB),
    lambda s: setattr(s, 'ns', ('pid:[2]', 'mnt:[1]', 'net:[1]', 'cgroup:[1]')),
])
def test_periodic_primary_visibility_and_resource_changes_block(mutation):
    snapshot = Snapshot(); observed = gate(snapshot); mutation(snapshot)
    with pytest.raises(r.CandidateResourceBlocked): observed.sample(snapshot.identities[40])


def test_cgroup_common_ancestor_and_verified_child_inheritance():
    snapshot = Snapshot(cgroup=True); observed = gate(snapshot)
    assert observed.mode == 'common_cgroup_v2'
    proof = observed.sample(snapshot.identities[40])
    assert observed.memberships[40] == '/limited/40'
    assert proof['cgroup_remaining_bytes'] == 8*r.GiB


@pytest.mark.parametrize('mutation', [
    lambda s: s.cg.__setitem__('memory.max', 10*r.GiB),
    lambda s: s.cg.__setitem__('memory.swap.max', 1),
    lambda s: s.cg.__setitem__('memory.swap.current', 1),
    lambda s: s.cg.__setitem__('memory.current', 8*r.GiB),
    lambda s: s.member.__setitem__(30, '/other'),
    lambda s: s.member.__setitem__(40, '/other'),
    lambda s: s.member.__setitem__(10, '/limited/new'),
    lambda s: s.mount.__setitem__('path', '/othermount'),
])
def test_cgroup_migration_limit_growth_swap_and_visibility_block(mutation):
    snapshot = Snapshot(cgroup=True); observed = gate(snapshot); mutation(snapshot)
    with pytest.raises(r.CandidateResourceBlocked): observed.sample(snapshot.identities[40])


def test_unreadable_observation_is_not_zero():
    snapshot = Snapshot(); observed = gate(snapshot)
    def denied(selected): raise PermissionError('fixture denied')
    snapshot.rss = denied
    with pytest.raises(r.CandidateResourceBlocked, match='visibility_or_read_failed'):
        observed.sample()


def test_inconsistent_listener_read_blocks():
    snapshot = Snapshot(); observed = gate(snapshot); calls = [0]
    def changing(*args):
        calls[0] += 1
        return snapshot.primary, snapshot.inode if calls[0] == 1 else 'replacement'
    snapshot.listener = changing
    with pytest.raises(r.CandidateResourceBlocked, match='inconsistent_primary'):
        observed.sample()


def test_retained_primary_descendant_remains_observed_after_reparenting():
    snapshot = Snapshot(); snapshot.processes[50] = (30, 5.)
    snapshot.memory_by_pid[50] = 100*r.MiB
    observed = gate(snapshot)
    snapshot.processes[50] = (1, 5.)
    assert observed.sample()['combined_rss_bytes'] == r.GiB + 196*r.MiB


def proc_socket_fixture(tmp_path, *, address='0100007F', owners=(22,)):
    proc = tmp_path/'proc'; (proc/'net').mkdir(parents=True)
    header = 'sl local_address rem_address st tx_queue rx tr retr uid timeout inode\n'
    (proc/'net/tcp').write_text(header + f' 0: {address}:223D 00000000:0000 0A 00000000:00000000 00:00000000 00000000 1000 0 123 1\n')
    (proc/'net/tcp6').write_text(header)
    for pid in owners:
        fd = proc/str(pid)/'fd'; fd.mkdir(parents=True)
        (fd/'3').symlink_to('socket:[123]')
    reader = r.LinuxReader(proc=proc)
    reader.identity = lambda pid: r.Identity(pid, 1.)
    return reader


def test_actual_socket_inode_parser_and_owner(tmp_path):
    reader = proc_socket_fixture(tmp_path)
    assert reader.listener('127.0.0.1', 8765) == (r.Identity(22, 1.), '123')


@pytest.mark.parametrize('kwargs', [dict(address='00000000'), dict(owners=(22, 23)), dict(owners=())])
def test_ambiguous_wildcard_and_unowned_listener_block(tmp_path, kwargs):
    with pytest.raises(r.CandidateResourceBlocked):
        proc_socket_fixture(tmp_path, **kwargs).listener('127.0.0.1', 8765)


def test_missing_dev_inventory_is_not_absence(tmp_path):
    with pytest.raises(FileNotFoundError):
        r.LinuxReader(dev=tmp_path/'missing').no_gpu()


def test_no_sysfs_claim_is_made_for_cpu_selected_execution(tmp_path):
    dev = tmp_path/'dev'; dev.mkdir()
    assert r.LinuxReader(sys=tmp_path/'missing', dev=dev).no_gpu() == 'readable_dev_no_accelerator_nodes'


def test_fake_cgroup_files_require_swap_zero(tmp_path):
    directory = tmp_path/'group'; directory.mkdir()
    for name, value in {'memory.max':9*r.GiB, 'memory.current':r.GiB, 'memory.swap.max':0, 'memory.swap.current':0}.items():
        (directory/name).write_text(str(value))
    reader = r.LinuxReader()
    assert reader.cgroup_values({'path':str(directory)})['memory.max'] == 9*r.GiB
    (directory/'memory.swap.max').write_text('max')
    with pytest.raises(ValueError): reader.cgroup_values({'path':str(directory)})


@pytest.mark.parametrize('child,deadline,expected', [
    ("print('bounded fixture')", 5, 'completed'),
    ('import time; time.sleep(30)', .15, 'resource_blocked'),
    ("import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)", 5, 'resource_blocked'),
    ("print('x'*200000)", 5, 'resource_blocked'),
])
def test_dedicated_harmless_process_cleanup(tmp_path, child, deadline, expected):
    output = tmp_path/'entry'; output.mkdir(mode=0o700)
    script = '''
import json, os, sys
from pathlib import Path
from audio_backend import recorder_candidate_resources as r
class Reader:
    def disk(self, path): return 20*r.GiB
class Gate:
    owner=r.Identity.bind(os.getpid())
    project_roots=()
    known_project={}
    proof={}
    reader=Reader()
    def sample(self,*args,**kwargs): return {}
    def validate_command(self,*args): pass
gate=Gate()
result=r.supervise([sys.executable,'-c',sys.argv[2]],gate=gate,output_dir=Path(sys.argv[1]),deadline_seconds=float(sys.argv[3]))
print(json.dumps(result))
'''
    result = subprocess.run([sys.executable, '-c', script, str(output), child, str(deadline)],
                            capture_output=True, text=True, timeout=12, check=True)
    value = json.loads(result.stdout)
    assert value['status'] == expected, value
    assert value['cleanup']['cleanup_verified'] is True, value
    assert value['cleanup']['original_handle_waited_and_reaped'] is True, value
    assert value['cleanup']['remaining_live_owned'] == [], value
    assert (output/'supervisor.stdout.txt').stat().st_size <= 128*1024


def test_generated_strict_launcher_parent_flags_and_child_bootstrap():
    # The actual product parent launcher uses -I -B -m, while _child_command
    # deliberately produces -I -B -c. Both exact forms are supported.
    snapshot = Snapshot()
    assert snapshot.commands[20][1:5] == ['-I', '-B', '-m', 'audio_backend.strict_supervisor']
    assert gate(snapshot).primary == snapshot.primary
    for options in (['-I', '-B', '-E', '-m'], ['-I', '-m'], ['-B', '-m']):
        with pytest.raises(r.CandidateResourceBlocked):
            r.launcher_args(['python3', *options, 'audio_backend.strict_supervisor'], 'audio_backend.strict_supervisor')


def test_cgroup_zero_swap_is_effective_even_if_host_swap_exists():
    snapshot = Snapshot(cgroup=True)
    snapshot.mem.update(SwapTotal=r.GiB, SwapFree=r.GiB, swap_devices=1)
    assert gate(snapshot).mode == 'common_cgroup_v2'


def test_cgroup_limit_mutation_between_reads_is_not_accepted():
    snapshot = Snapshot(cgroup=True); observed = gate(snapshot)
    original = snapshot.cgroup_values; calls = [0]
    def changing(boundary):
        calls[0] += 1
        result = original(boundary)
        if calls[0] > 1: result['memory.max'] += r.GiB
        return result
    snapshot.cgroup_values = changing
    with pytest.raises(r.CandidateResourceBlocked, match='changed_during_sample'):
        observed.sample(snapshot.identities[40])


def test_cleanup_uncertainty_retains_original_then_bounded_retry(tmp_path):
    output = tmp_path/'entry'; output.mkdir(mode=0o700)
    script = """
import json, os, sys
from pathlib import Path
from audio_backend import recorder_candidate_resources as r
class Reader:
    def disk(self, path): return 20*r.GiB
class Gate:
    owner=r.Identity.bind(os.getpid()); project_roots=(); known_project={}; proof={}; reader=Reader()
    def sample(self,*args,**kwargs):
        if args: raise r.CandidateResourceBlocked('fixture_resource_failure')
        return {}
    def validate_command(self,*args): pass
original=r._children
calls=[0]
def deny_one_cleanup_scan():
    calls[0]+=1
    if calls[0]==2: raise PermissionError('fixture denied')
    return original()
r._children=deny_one_cleanup_scan
value=r.supervise([sys.executable,'-c','import time; time.sleep(30)'],gate=Gate(),output_dir=Path(sys.argv[1]),deadline_seconds=5)
assert not value['cleanup']['cleanup_verified']
assert r.pending_cleanup()
assert r.retry_pending_cleanup()
assert not r.pending_cleanup()
assert not original()
print(json.dumps({'retained_then_reaped':True}))
"""
    result = subprocess.run([sys.executable,'-c',script,str(output)],capture_output=True,text=True,timeout=12)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['retained_then_reaped']


def test_primary_command_from_actual_product_child_factory():
    from types import SimpleNamespace
    from audio_backend.strict_supervisor import _child_command
    args = SimpleNamespace(data_dir=Path('/data'),model_dir=Path('/models'),host='127.0.0.1',port=8765,
        native_diarization_model=Path('/native/model'),native_diarization_library=Path('/native/library'),
        recording_speaker_model=Path('/speaker'),language='auto',threads=2,native_diarization_threads=2,
        memory_limit_gib=6,recording_capture_mib=1024,recording_total_mib=4096,lexical_evidence_session_mib=32,
        profile='strict-primary-payload-v2',strict_payload_source_storage='shared-source-v1',
        strict_primary_lifetime_records=2056,strict_primary_receipt_records=4096,reviewed_primary_persons=False)
    snapshot = Snapshot(); snapshot.commands[30] = _child_command(args, 7)
    assert gate(snapshot).primary == snapshot.primary


def test_preflight_cancellation_precedes_all_observation():
    class Unused:
        def __getattribute__(self, name):
            raise AssertionError('No resource observation after cancellation')
    with pytest.raises(r.CandidateResourceBlocked, match='cancelled_before_admission'):
        r.preflight('http://127.0.0.1:8765', reader=Unused(), cancel=lambda: True)


def test_transient_initial_cleanup_error_is_resolved_by_positive_original_wait(tmp_path):
    output=tmp_path/'entry'; output.mkdir(mode=0o700)
    script="""
import json,os,sys
from pathlib import Path
from audio_backend import recorder_candidate_resources as r
class Reader:
    def disk(self,path): return 20*r.GiB
class Gate:
    owner=r.Identity.bind(os.getpid()); project_roots=(); known_project={}; proof={}; reader=Reader()
    def sample(self,*args,**kwargs):
        if args: raise r.CandidateResourceBlocked('fixture_stop')
    def validate_command(self,*args): pass
original=r._exited
calls=[0]
def transient(process):
    calls[0]+=1
    if calls[0]==1: raise PermissionError('transient visibility fixture')
    return original(process)
r._exited=transient
result=r.supervise([sys.executable,'-c','import time;time.sleep(30)'],gate=Gate(),output_dir=Path(sys.argv[1]),deadline_seconds=5)
assert result['cleanup']['cleanup_verified'],result
assert result['cleanup']['original_handle_waited_and_reaped'],result
assert not r.pending_cleanup()
print(json.dumps(result))
"""
    result=subprocess.run([sys.executable,'-c',script,str(output)],capture_output=True,text=True,timeout=12)
    assert result.returncode==0,result.stderr


def test_extra_primary_gpu_or_optional_execution_flags_are_not_accepted():
    for extra in (['--moss-td-cpp-device','vulkan'],['--moss-jobs'],['--device','cpu']):
        snapshot=Snapshot(); snapshot.commands[30].extend(extra)
        with pytest.raises(r.CandidateResourceBlocked): gate(snapshot)


def test_resource_failure_never_signals_unrelated_primary_fixture(tmp_path):
    output = tmp_path/'entry'; output.mkdir(mode=0o700)
    sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        script = """
import json,os,sys
from pathlib import Path
from audio_backend import recorder_candidate_resources as r
class Reader:
    def disk(self,path): return 20*r.GiB
class Gate:
    owner=r.Identity.bind(os.getpid())
    project_roots=(r.Identity.bind(int(sys.argv[2])),)
    known_project={project_roots[0].pid:project_roots[0]}
    proof={}; reader=Reader()
    def sample(self,*args,**kwargs):
        if args: raise r.CandidateResourceBlocked('fixture_resource_failure')
    def validate_command(self,*args): pass
result=r.supervise([sys.executable,'-c','import time;time.sleep(30)'],gate=Gate(),output_dir=Path(sys.argv[1]),deadline_seconds=5)
assert result['cleanup']['cleanup_verified'],result
print(json.dumps(result))
"""
        result = subprocess.run([sys.executable,'-c',script,str(output),str(sentinel.pid)],
                                capture_output=True,text=True,timeout=12)
        assert result.returncode == 0, result.stderr
        assert sentinel.poll() is None
    finally:
        sentinel.kill(); sentinel.wait(timeout=5)


def test_candidate_dispatch_digest_is_bound_to_owned_preflight(tmp_path, monkeypatch):
    from audio_backend import recorder_candidate_postphase as postphase
    entry = tmp_path/'entry'; entry.mkdir(mode=0o700)
    prepared = {'plan': {'source': 'fixture-source', 'ranges': [{'start_frame': 0, 'end_frame': 16}]}}
    path = entry/'preflight.json'
    path.write_text(json.dumps(prepared)); path.chmod(0o600)
    profile = {'settings': {'python': '/fixture/python', 'model': '/fixture/model'}}
    monkeypatch.setattr(postphase, 'verify_policy', lambda value: None)
    observed = gate(); observed.runtime_profile = profile; observed.runtime_verified = True
    expected = postphase.fingerprint(prepared['plan'])
    command = postphase.command(profile, Path('/job'), Path('/baseline'), entry/'result', 540,
                                expected_plan_sha256=expected)
    observed.validate_command(command, entry)
    changed = list(command); changed[-1] = '0'*64
    with pytest.raises(r.CandidateResourceBlocked, match='candidate_dispatch_plan_mismatch'):
        observed.validate_command(changed, entry)
    with pytest.raises(r.CandidateResourceBlocked, match='invalid_candidate_command_option'):
        observed.validate_command(command[:-2], entry)
    with pytest.raises(r.CandidateResourceBlocked, match='invalid_candidate_command_option'):
        observed.validate_command(command + command[-2:], entry)
    prepared['plan']['source'] = 'replacement-source'
    path.write_text(json.dumps(prepared))
    with pytest.raises(r.CandidateResourceBlocked, match='candidate_dispatch_plan_mismatch'):
        observed.validate_command(command, entry)


@pytest.mark.parametrize('cleanup_mode', ['direct', 'postphase'])
def test_pending_cleanup_defers_signals_across_return_gap_then_restores(tmp_path, cleanup_mode):
    output = tmp_path/'entry'; output.mkdir(mode=0o700)
    script = """
import json,os,signal,sys
from pathlib import Path
from audio_backend import recorder_candidate_resources as r
class Reader:
    def disk(self,path): return 20*r.GiB
class Gate:
    owner=r.Identity.bind(os.getpid()); project_roots=(); known_project={}; proof={}; reader=Reader()
    def sample(self,*args,**kwargs):
        if args: raise r.CandidateResourceBlocked('fixture_resource_failure')
    def validate_command(self,*args): pass
called=[]
def original_handler(number,frame): called.append(number)
for number in (signal.SIGINT,signal.SIGTERM): signal.signal(number,original_handler)
original_children=r._children
calls=[0]
def denied_once():
    calls[0]+=1
    if calls[0]==2: raise PermissionError('fixture cleanup visibility gap')
    return original_children()
r._children=denied_once
value=r.supervise([sys.executable,'-c','import time;time.sleep(30)'],gate=Gate(),output_dir=Path(sys.argv[1]),deadline_seconds=5)
assert not value['cleanup']['cleanup_verified']
assert r.pending_cleanup() and r._PENDING_SIGNALS
# These signals target only this isolated test owner, never any service.
for number in (signal.SIGINT,signal.SIGTERM): signal.raise_signal(number)
assert called==[],called
assert r.pending_cleanup()
if sys.argv[2]=='postphase':
    from audio_backend.recorder_candidate_postphase import retain_cleanup
    assert retain_cleanup(_sleep=lambda _: None)
else:
    assert r.retry_pending_cleanup()
assert not r.pending_cleanup() and not r._PENDING_SIGNALS
for number in (signal.SIGINT,signal.SIGTERM):
    assert signal.getsignal(number) is original_handler
    signal.raise_signal(number)
assert called==[signal.SIGINT,signal.SIGTERM],called
assert not original_children()
print(json.dumps({'deferred_through_gap_then_restored':True}))
"""
    result = subprocess.run([sys.executable,'-c',script,str(output),cleanup_mode],capture_output=True,text=True,timeout=12)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['deferred_through_gap_then_restored']
