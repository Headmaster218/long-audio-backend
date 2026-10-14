#!/usr/bin/env python3
"""Run one artifact per invocation under a pinned resource guard."""
import time
START = time.monotonic()
ABSOLUTE_END = START + 600
WORK_END = START + 540
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import stat
import sys

BASE = Path('/GENERATED_WORKSPACE')
HERE = BASE/'GENERATED_SOURCE_ROOT/GENERATED_SELECTED_PACKET'
OUT = BASE/'GENERATED_WORK_ROOT/evidence/GENERATED_SELECTED_EVIDENCE'
V8 = BASE/'GENERATED_SOURCE_ROOT/GENERATED_GUARD_PACKET'
HOST = '/GENERATED_PYTHON_EXECUTABLE'
HOST_SHA = 'GENERATED_HOST_SHA256'
FILES = {'selected.py':40960,'owner.py':24576,'test_selected.py':16384,'TECHNICAL.md':8192}
ARTIFACTS = ('audioset','sensevoice','native')
EVIDENCE_CAP = 524288
CONTROL_CAP = 32768
PHASE = 'assets-model-extract'
S = None


def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def check(end=WORK_END):
    need(time.monotonic() < end, 'owner deadline')


def pin(path, cap, end=WORK_END):
    """Only small controls/source and the single pinned Python executable."""
    check(end)
    path = Path(path)
    for ancestor in (path,*path.parents):
        need(not ancestor.is_symlink(), 'pin symlink path')
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= cap, 'source/control regular cap')
        key = lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        h = hashlib.sha256()
        n = 0
        while True:
            check(end)
            block = os.read(fd,min(65536,cap+1-n))
            if not block:
                break
            n += len(block)
            need(n <= cap,'source/control growth cap')
            h.update(block)
        need(key(before) == key(os.fstat(fd)) == key(path.lstat()),'source/control changed')
        return dict(bytes=n,sha256=h.hexdigest())
    finally:
        os.close(fd)


def read_json(path, cap, expected_sha=None, end=WORK_END):
    observed = pin(path,cap,end)
    if expected_sha is not None:
        need(observed['sha256'] == expected_sha,'control SHA256 mismatch')
    raw = Path(path).read_bytes()
    need(len(raw) == observed['bytes'] and hashlib.sha256(raw).hexdigest() == observed['sha256'],'control changed after pin')
    def pairs(items):
        data = {}
        for k,v in items:
            need(k not in data,'duplicate JSON key')
            data[k] = v
        return data
    return json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda value:(_ for _ in ()).throw(RuntimeError('nonfinite JSON')))


def load_module(name,path,expected):
    need(pin(path,max(expected['bytes'],1)) == expected,'module exact pin')
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evidence_usage():
    """Account for all bounded auxiliary files and directories."""
    total = logical_controls = count = 0
    if not OUT.exists():
        return 0
    ordinary = {'release.json':2048,'config.json':2048,'claim.json':512,'spec.json':6144,
                'result.json':65536,'OWNER_ACCEPTANCE.json':8192,'OWNER_COMPLETION.json':2048}
    runtime = {'index.json':16384,'receipt.json':16384,'index.tmp':16384,'receipt.tmp':16384,
               'output.log':4096,'STOP':512}
    runs = {a:set() for a in ARTIFACTS}
    for base,dirs,files in os.walk(OUT,followlinks=False):
        check(ABSOLUTE_END-0.3)
        for leaf in [Path(base),*(Path(base)/x for x in files)]:
            st = leaf.lstat()
            count += 1
            need(count <= 96 and (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)),'evidence shape cap')
            total += max(st.st_size,st.st_blocks*512)
        need(all(not (Path(base)/x).is_symlink() for x in dirs),'evidence symlink directory')
        rel = Path(base).relative_to(OUT).parts
        if not rel:
            need(set(dirs).issubset(set(ARTIFACTS)|{'focused-cases'}) and set(files).issubset({'focused.result.json','focused.acceptance.json'}),'evidence root shape')
            for name in files:
                need((Path(base)/name).stat().st_size <= 4096,'focused result/acceptance cap')
            continue
        if rel[0] == 'focused-cases':
            need(len(rel) == 1 and not dirs and len(files) <= 18,'focused cases finite leaves')
            allowed = {'existing.output','flatten.output'}
            for label in ('pinned_bz2','discovered_gzip','duplicate_member','alias_substitution','wrong_digest','unsafe_name','truncated_gzip','nonzero_suffix'):
                allowed.update({label+'.input',label+'.output'})
            need(set(files).issubset(allowed),'focused exact leaf names')
            need(sum(max((Path(base)/x).stat().st_size,(Path(base)/x).stat().st_blocks*512) for x in files)+Path(base).stat().st_blocks*512 <= 81920,'focused allocated cap')
            continue
        need(rel[0] in ARTIFACTS,'evidence artifact')
        if len(rel) == 1:
            need(set(dirs).issubset({'runs'}) and set(files).issubset(ordinary),'artifact evidence names')
            for name in files:
                n = (Path(base)/name).stat().st_size
                need(n <= ordinary[name],'artifact evidence cap')
                if name in ('release.json','config.json','claim.json','spec.json'):
                    logical_controls += n
        elif len(rel) == 2:
            need(rel[1] == 'runs' and not files and len(dirs) <= 1 and all(x.startswith('run-') for x in dirs),'one owned run')
        elif len(rel) == 3:
            need(rel[1] == 'runs' and rel[2].startswith('run-') and set(dirs).issubset({'home','tmp','cache'}) and set(files).issubset(runtime),'guard evidence names')
            runs[rel[0]].add(rel[2])
            for name in files:
                need((Path(base)/name).stat().st_size <= runtime[name],'guard evidence leaf cap')
        else:
            need(len(rel) == 4 and rel[1] == 'runs' and rel[2].startswith('run-') and rel[3] in ('home','tmp','cache') and not dirs and not files,'empty offline guard scratch')
    need(total <= EVIDENCE_CAP and logical_controls <= CONTROL_CAP,'model evidence/control reserve')
    return total


class Adapter:
    """Only the three bootstrap functions used by the unchanged Guard."""
    def __init__(self,original,config,config_path,config_sha,source_files,external,guard_sha):
        self.original = original
        self.config = config
        self.config_path = str(config_path)
        self.config_sha = config_sha
        self.source_files = source_files
        self.external = external
        self.guard_sha = guard_sha

    def load_config(self,path,sha):
        need(path == self.config_path and sha == self.config_sha,'fixed config reference')
        got = read_json(path,2048,sha,ABSOLUTE_END-0.3)
        need(got == self.config,'fixed current config changed')
        return got

    def make_spec(self,c,path,sha,phase,launch):
        need(c == self.config and path == self.config_path and sha == self.config_sha and phase == PHASE and launch == c['invocation']['launch'],'fixed current spec binding')
        return dict(schema=3,stage=PHASE,absolute_seconds=600,work_seconds=540,
            address_space_cap_bytes=1024**3,rss_cap_bytes=256*2**20,scratch_cap_bytes=12*1024**3,
            guard_sha256=self.guard_sha,source_root=str(HERE),source_files=self.source_files,
            cwd=str(OUT/c['artifact']),external_files=self.external,runtime_files={HOST:HOST_SHA},
            config_path=path,config_sha256=sha,phase=phase,launch=launch,
            argv=[HOST,'-I','-B','-S',str(HERE/'selected.py'),c['artifact']])

    def resource_usage(self,c,end):
        check(end)
        need(c == self.config,'census fixed config')
        evidence_usage()
        return self.original.resource_usage(c,end)


def main():
    global S
    need(len(sys.argv) == 3 and sys.argv[1] in ARTIFACTS and len(sys.argv[2]) == 64,'artifact and release SHA256 required')
    artifact,release_sha = sys.argv[1:]
    need(sys.executable == HOST,'pinned host path')
    need(3 in os.sched_getaffinity(0),'CPU3 unavailable')
    os.sched_setaffinity(0,{3})
    os.nice(max(0,19-os.getpriority(os.PRIO_PROCESS,0)))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3))
    need(pin(HOST,int('GENERATED_HOST_BYTES')) == dict(bytes=int('GENERATED_HOST_BYTES'),sha256=HOST_SHA),'pinned Python bytes')
    release_path = OUT/artifact/'release.json'
    release = read_json(release_path,2048,release_sha)
    need(set(release) == {'schema','status','artifact','packet_sha256','guard','bootstrap','focused_acceptance_sha256'},'release fields')
    need(release['schema'] == 'fixed-selected-model-release-v1' and release['status'] == 'SOURCE_AND_FOCUSED_ACCEPTED_EXECUTION_RELEASE' and release['artifact'] == artifact,'released artifact')
    need(type(release['focused_acceptance_sha256']) is str and len(release['focused_acceptance_sha256']) == 64,'focused acceptance pin')
    focused_path = OUT/'focused.acceptance.json'
    focused = read_json(focused_path,4096,release['focused_acceptance_sha256'])
    need(focused.get('status') == 'ACCEPTED_FOCUSED_SYNTHETIC_ONLY' and focused.get('packet_sha256') == release['packet_sha256'] and focused.get('original_exit_code') == 0 and focused.get('original_terminal') is True,'focused terminal-success binding')
    packet_path = HERE/'PACKET_MANIFEST.json'
    packet = read_json(packet_path,8192,release['packet_sha256'])
    need(set(packet) == {'schema','files','source_logical_cap','source_inclusive_cap','controls_logical_cap'} and packet['schema'] == 'public-selected-model-template-v1','source manifest fields')
    need(packet['source_logical_cap'] == 98304 and packet['source_inclusive_cap'] == 1048576 and packet['controls_logical_cap'] == CONTROL_CAP and set(packet['files']) == set(FILES),'source inventory/caps')
    need(set(p.name for p in HERE.iterdir()) == set(FILES)|{'PACKET_MANIFEST.json'},'closed five-file packet')
    source_files = {}
    source_total = 0
    source_allocated = HERE.stat().st_blocks*512
    for relative,cap in FILES.items():
        wanted = packet['files'][relative]
        need(set(wanted) == {'bytes','sha256'} and wanted['bytes'] <= cap and pin(HERE/relative,cap) == wanted,'source file pin')
        source_files[relative] = wanted['sha256']
        source_total += wanted['bytes']
        st = (HERE/relative).stat()
        source_allocated += max(st.st_size,st.st_blocks*512)
    source_total += packet_path.stat().st_size
    source_allocated += max(packet_path.stat().st_size,packet_path.stat().st_blocks*512)
    need(source_total <= 98304 and source_allocated <= 1048576,'source packet charge')
    source_files['PACKET_MANIFEST.json'] = release['packet_sha256']
    S = load_module('model_fixed_selected',HERE/'selected.py',packet['files']['selected.py'])
    S.END = WORK_END
    for name in ('guard','bootstrap'):
        need(set(release[name]) == {'path','bytes','sha256'},'v8 dependency reference')
        expected_path = V8/('acquisition_guard.py' if name == 'guard' else 'bootstrap.py')
        need(release[name]['path'] == str(expected_path),'v8 fixed dependency path')
        need(pin(expected_path,81920) == {k:release[name][k] for k in ('bytes','sha256')},'v8 dependency pin')
    guard = load_module('model_reused_guard',V8/'acquisition_guard.py',{k:release['guard'][k] for k in ('bytes','sha256')})
    bootstrap = load_module('model_census_bootstrap',V8/'bootstrap.py',{k:release['bootstrap'][k] for k in ('bytes','sha256')})
    # No bootstrap.main, load_config, make_spec, or runtime prerequisite is invoked.
    spec_data = S.prepare(artifact)
    need(S.path_descriptor(S.ASSETS/spec_data['archive']) == spec_data['acquired']['descriptor_after'],'fresh accepted archive descriptor')
    projection = S.absence_and_projection(spec_data)
    guard.START,guard.ABSOLUTE_END = START,ABSOLUTE_END
    guard.BOOT_ID = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    guard.PID_NS = os.readlink('/proc/self/ns/pid')
    guard.TICKS,guard.PAGE = os.sysconf('SC_CLK_TCK'),os.sysconf('SC_PAGE_SIZE')
    guard.B_TIME = next(int(r.split()[1]) for r in Path('/proc/stat').read_text().splitlines() if r.startswith('btime '))
    own = guard.proc(os.getpid())
    launch = dict(start_monotonic=START,work_end_monotonic=WORK_END,absolute_end_monotonic=ABSOLUTE_END,
                  identity={k:own[k] for k in ('pid','start_ticks','boot_id','pid_namespace')})
    c = dict(schema='fixed-selected-model-config-v1',artifact=artifact,
        roots=dict(workspace=str(BASE),source_recovery=str(BASE/'GENERATED_SOURCE_ROOT'),packet=str(HERE),product=str(HERE),
            work=str(BASE/'GENERATED_WORK_ROOT'),runtime_container=str(BASE/'GENERATED_RUNTIME_ROOT'),
            proof_source=str(BASE/'GENERATED_PROOF_ROOT')),
        host=dict(filesystem_device=int('GENERATED_FILESYSTEM_DEVICE')),assets_manifest=dict(path=str(BASE/'GENERATED_BOOTSTRAP_ROOT/assets.lock.json')),
        limits=dict(category_bytes=bootstrap.CAPS),invocation=dict(phase=PHASE,launch=launch),
        release_sha256=release_sha,acquisition_projection=projection['acquisition_projection'])
    phase_dir = OUT/artifact
    guard.CHECKS,guard.WORKSPACE = phase_dir,BASE
    # Prospective allocation and the full unchanged Guard.sample both apply.
    evidence_usage()
    usage = bootstrap.resource_usage(c,WORK_END)
    need(usage['acquisition']+projection['acquisition_projection'] <= 7*1024**3,'prospective asset allocation')
    need(usage['evidence']+EVIDENCE_CAP <= bootstrap.CAPS['evidence'],'prospective evidence allocation')
    need(sum(usage.values())+projection['acquisition_projection']+EVIDENCE_CAP+2**20 <= 12*1024**3,'prospective global allocation')
    need(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'),'pidfds required')
    need(ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0) == 0,'subreaper failed')
    need(not guard.children(os.getpid()),'preexisting owner children')
    # An exclusive claim persists even after failure: no automatic replay.
    S.json_new(phase_dir/'claim.json',dict(schema='fixed-model-single-attempt-v1',artifact=artifact,attempts=1),512)
    S.json_new(phase_dir/'config.json',c,2048)
    config_path = phase_dir/'config.json'
    config_sha = pin(config_path,2048)['sha256']
    external = {str(path):sha for path,(size,sha) in S.INPUT_PINS.items()}
    if artifact == 'native':
        external.update({str(S.PRODUCT/path):sha for path,(size,sha) in S.COPIES.items()})
    external.update({str(V8/'acquisition_guard.py'):release['guard']['sha256'],str(V8/'bootstrap.py'):release['bootstrap']['sha256'],
                     str(release_path):release_sha,str(focused_path):release['focused_acceptance_sha256'],str(config_path):config_sha,
                     str(phase_dir/'claim.json'):pin(phase_dir/'claim.json',512)['sha256']})
    adapter = Adapter(bootstrap,c,config_path,config_sha,source_files,external,release['guard']['sha256'])
    guard.CONFIG,guard.BOOTSTRAP = c,adapter
    fixed_spec = adapter.make_spec(c,str(config_path),config_sha,PHASE,launch)
    S.json_new(phase_dir/'spec.json',fixed_spec,6144)
    spec_sha = pin(phase_dir/'spec.json',6144)['sha256']
    (phase_dir/'runs').mkdir(mode=0o700)
    runner = guard.Guard(fixed_spec,spec_sha)
    def stop(signum,frame):
        runner.signal_received = signum
    for signum in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
        signal.signal(signum,stop)
    result = runner.run_command()
    evidence_usage()
    print(json.dumps(dict(status='OWNER_RETURNED_PENDING_OUTER_ACCEPTANCE',artifact=artifact,
                         guard_status=runner.receipt['status'],guard_result=result,
                         start_monotonic=START,absolute_end_monotonic=ABSOLUTE_END)),flush=True)
    return result


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps(dict(status='OWNER_REFUSED',reason=str(error)[:300])),flush=True)
        raise SystemExit(2)
