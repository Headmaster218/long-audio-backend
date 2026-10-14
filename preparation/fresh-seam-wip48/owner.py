#!/usr/bin/env python3
# Unexecuted portable template; generate configuration placeholders before use.
"""Bounded preparation supervisor for model-free tests and exact WAV splitting.

The original Popen, process identity, finite resource bounds, cleanup and final
source checks are retained. Generate all configuration placeholders before use.
This public source template has not been executed.
"""
import time
START=time.monotonic()
WORK_END=START+240
ABSOLUTE_END=START+300
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

BASE=Path('@GENERATED_WORKSPACE_ROOT@')
HERE=BASE/'@GENERATED_OWNER_ROOT_REL@'
PACKET=BASE/'@GENERATED_PACKET_ROOT_REL@'
CONTROLLER=PACKET/'controller'
V8=BASE/'@GENERATED_GUARD_ROOT_REL@'
OUT=BASE/'@GENERATED_PREPARATION_RECORDS_ROOT_REL@'
HOST=Path('@GENERATED_HOST_PYTHON@')
PYTHON=BASE/'@GENERATED_RUNTIME_ROOT_REL@/bin/python3.12'
PY_PIN={'bytes':int('@GENERATED_PYTHON_EXECUTABLE_BYTES@'),'sha256':'@GENERATED_PYTHON_EXECUTABLE_SHA256@'}
GUARD_PIN={'bytes':int('@GENERATED_PROCESS_GUARD_SOURCE_BYTES@'),'sha256':'@GENERATED_PROCESS_GUARD_SOURCE_SHA256@'}
BOOTSTRAP_PIN={'bytes':int('@GENERATED_CENSUS_SOURCE_BYTES@'),'sha256':'@GENERATED_CENSUS_SOURCE_SHA256@'}
MIB=2**20
CONTROL_CAP=256*1024
PREPARATION_CAP=16*MIB
ARTIFACT_CAP=12*MIB
INPUT_CAP=MIB
CONTROL_FILES={'release.json':8192,'config.json':16384,'spec.json':32768,'claim.json':1024,
    'owner-result.json':8192,'OWNER_COMPLETION.json':16384,'OWNER_ACCEPTANCE.json':16384}
RUN_FILES={'index.json':32768,'index.tmp':32768,'receipt.json':32768,'receipt.tmp':32768,
    'fixture-authority.json':16384,'split-authority.json':16384,'output.log':256*1024,'STOP':512}
INPUT_FILES={'upload-a-10p5s.wav':336044,'upload-b-10p5s.wav':336044,
    'MATERIALIZATION.json':16384,'MATERIALIZATION.json.pending':16384}
FILE_CAPS={'controller/ownership.py':10240,'controller/instrumentation.py':17408,
    'controller/admission.py':6144,'controller/common.py':20480,'controller/bootstrap.py':69632,
    'controller/server.py':67584,'controller/capture.py':102400,'controller/launch.py':20480,
    'controller/binding.py':32768,'controller/test_seam.py':143360,'controller/test_binding.py':28672,
    'config.template.json':8192,'actual-authority.template.json':2048,'README.md':4096}
MODE_STAGE={'fixtures':'runtime-fixtures','split':'assets-input-derive'}


def need(value,reason):
    if not value:raise RuntimeError(reason)


def check(end=WORK_END):need(time.monotonic()<end,'owner_deadline')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def sha(raw):return hashlib.sha256(raw).hexdigest()


def safe_path(path):
    path=Path(path)
    need(path.is_absolute() and str(path)==os.fspath(path) and path.resolve()==path,'canonical_no_alias_path')
    for ancestor in (path,*path.parents):need(not ancestor.is_symlink(),'no_symlink_path')
    return path


def read(path,cap,expected=None,end=WORK_END):
    check(end);path=safe_path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=cap,'ordinary_bounded_file')
        chunks=[];size=0
        while True:
            check(end);block=os.read(fd,min(65536,cap+1-size))
            if not block:break
            size+=len(block);need(size<=cap,'bounded_file_growth');chunks.append(block)
        key=lambda x:(x.st_dev,x.st_ino,x.st_mode,x.st_nlink,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        need(key(before)==key(os.fstat(fd))==key(path.lstat()),'metadata_or_source_changed')
        raw=b''.join(chunks);observed={'bytes':size,'sha256':sha(raw)}
        if expected is not None:need(observed==expected,'exact_file_pin:'+str(path))
        return raw,observed
    finally:os.close(fd)


def decode(raw):
    def pairs(items):
        result={}
        for k,v in items:need(k not in result,'duplicate_JSON_key');result[k]=v
        return result
    def bad(value):raise RuntimeError('nonfinite_JSON')
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=bad)
    need(type(value) is dict,'JSON_object');return value


def json_read(path,cap,expected=None,end=WORK_END):
    raw,pin=read(path,cap,expected,end);return decode(raw),pin


def ref_read(ref,cap,end=WORK_END):
    need(type(ref) is dict and set(ref)=={'path','bytes','sha256'},'exact_reference_fields')
    return json_read(ref['path'],cap,{k:ref[k] for k in ('bytes','sha256')},end)[0]


def write_new(path,value,cap):
    check();path=safe_path(path);raw=canonical(value)+b'\n';need(len(raw)<=cap,'new_metadata_cap')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        view=memoryview(raw)
        while view:
            count=os.write(fd,view[:65536]);need(count>0,'metadata_write_stalled');view=view[count:]
        os.fsync(fd)
    finally:os.close(fd)
    return {'path':str(path),'bytes':len(raw),'sha256':sha(raw)}


def directory(path):
    path=safe_path(path)
    if not path.exists():path.mkdir(mode=0o700)
    need(path.is_dir(),'expected_directory');return path


def charged_tree(root,end,*,max_entries):
    total=count=0
    def walk(path):
        nonlocal total,count
        check(end);st=path.lstat();count+=1
        need(count<=max_entries and st.st_dev==BASE.stat().st_dev,'output_entry_or_device_bound')
        need(stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode),'ordinary_output_shape')
        if stat.S_ISREG(st.st_mode):need(st.st_nlink==1,'output_link_alias')
        total+=max(st.st_size,st.st_blocks*512)
        if stat.S_ISDIR(st.st_mode):
            for child in path.iterdir():walk(child)
    if root.exists():walk(root)
    return total,count


def audit_outputs_once(end):
    """Closed union; every directory and failed/pending file is charged once."""
    total=0
    if OUT.exists():
        need({p.name for p in OUT.iterdir()}<=set(MODE_STAGE),'control_phase_union')
        controls,_=charged_tree(OUT,end,max_entries=64)
        for phase in OUT.iterdir():
            need(phase.is_dir() and not phase.is_symlink(),'control_phase_directory')
            for path in phase.iterdir():
                need(path.name in CONTROL_FILES and path.is_file() and not path.is_symlink() and
                    path.stat().st_size<=CONTROL_FILES[path.name],'control_leaf_union')
        need(controls<=CONTROL_CAP,'inclusive_control_capacity');total+=controls
    runs=PACKET/'runs'
    if runs.exists():
        names=list(runs.iterdir());need(len(names)<=2,'exact_two_preparation_runs')
        for run in names:
            need(run.is_dir() and run.name.startswith('run-') and len(run.name)==12 and
                all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in run.name[4:]),'fresh_run_name')
            need({p.name for p in run.iterdir()}<=set(RUN_FILES)|{'home','tmp','cache'},'run_output_union')
            authorities=[n for n in ('fixture-authority.json','split-authority.json') if (run/n).exists()]
            need(len(authorities)<=1,'one_phase_authority_per_run')
            for path in run.iterdir():
                if path.name in RUN_FILES:
                    need(path.is_file() and not path.is_symlink() and path.stat().st_size<=RUN_FILES[path.name],
                        'run_leaf_cap')
                elif path.name in ('home','cache'):
                    need(path.is_dir() and not any(path.iterdir()),'empty_offline_home_and_cache')
                else:
                    need(path.is_dir() and {p.name for p in path.iterdir()}<={'fixture-artifacts'},'tmp_namespace')
                    artifacts=path/'fixture-artifacts'
                    if artifacts.exists():
                        need(authorities==['fixture-authority.json'],'fixtures_only_artifact_namespace')
                        charge,count=charged_tree(artifacts,end,max_entries=4096)
                        need(charge<=ARTIFACT_CAP and count<=4096,'retained_fixture_artifact_capacity')
        charge,_=charged_tree(runs,end,max_entries=4160);total+=charge
    inputs=PACKET/'input'
    if inputs.exists():
        need({p.name for p in inputs.iterdir()}<=set(INPUT_FILES),'exact_split_output_union')
        for path in inputs.iterdir():
            need(path.is_file() and not path.is_symlink() and path.stat().st_size<=INPUT_FILES[path.name],
                'split_output_leaf_cap')
        charge,_=charged_tree(inputs,end,max_entries=5);need(charge<=INPUT_CAP,'inclusive_split_input_capacity');total+=charge
    need(total<=PREPARATION_CAP,'inclusive_preparation_output_capacity')
    return total


def known_atomic_race(error):
    if not error.filename:return False
    path=Path(error.filename)
    return (path.is_relative_to(PACKET/'runs') or path.is_relative_to(PACKET/'input')) and (
        path.name.endswith('.pending') or path.name in ('index.tmp','receipt.tmp'))


def audit_outputs(end):
    for attempt in range(3):
        try:return audit_outputs_once(end)
        except FileNotFoundError as error:
            need(known_atomic_race(error) and attempt<2,'unapproved_output_disappearance')
    raise RuntimeError('unreachable_output_census')


def load_module(name,path,pin):
    read(path,max(pin['bytes'],1),pin)
    desc=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(desc)
    desc.loader.exec_module(module);return module


class Adapter:
    """Three-function adapter for the external process guard."""
    def __init__(self,bootstrap,c,path,pin,source_files,external,mode,config_ref):
        self.original=bootstrap;self.c=c;self.path=str(path);self.pin=pin
        self.source_files=source_files;self.external=external;self.mode=mode;self.config_ref=config_ref
    def load_config(self,path,digest):
        need(path==self.path and digest==self.pin['sha256'],'fixed_owner_config_reference')
        actual,_=json_read(path,16384,self.pin,ABSOLUTE_END-.3)
        need(actual==self.c,'fixed_owner_config_changed');return actual
    def make_spec(self,c,path,digest,phase,launch):
        need(c==self.c and path==self.path and digest==self.pin['sha256'] and
            phase==MODE_STAGE[self.mode] and launch==c['invocation']['launch'],'fixed_preparation_spec')
        return {'schema':3,'stage':phase,'absolute_seconds':300,'work_seconds':240,
            'address_space_cap_bytes':2**30,'rss_cap_bytes':256*MIB,'scratch_cap_bytes':12*2**30,
            'guard_sha256':GUARD_PIN['sha256'],'source_root':str(CONTROLLER),'source_files':self.source_files,
            'cwd':str(OUT/self.mode),'external_files':self.external,
            'runtime_files':{str(HOST):PY_PIN['sha256'],str(PYTHON):PY_PIN['sha256']},
            'config_path':path,'config_sha256':digest,'phase':phase,'launch':launch,
            'argv':[str(PYTHON),'-I','-B','-S',str(CONTROLLER/'launch.py'),'--preparation-child',self.mode,
                    self.config_ref['path'],self.config_ref['sha256']]}
    def resource_usage(self,c,end):
        need(c==self.c,'fixed_resource_census_config');audit_outputs(end)
        for attempt in range(3):
            try:return self.original.resource_usage(c,end)
            except FileNotFoundError as error:
                # A full new census follows only a known, currently owned atomic rename.
                need(known_atomic_race(error) and attempt<2,'unapproved_global_census_disappearance')
        raise RuntimeError('unreachable_global_census')


def fixture_acceptance(ref,manifest_sha,config_sha,ids):
    value=ref_read(ref,16384)
    need(set(value)=={'schema','status','manifest_sha256','config_sha256','fixture_ids_sha256','tests_run',
        'failures','errors','skips','cleanup','original_tool','guard_receipt','actual_authorized'} and
        value['schema']=='fresh-seam-fixture-owner-acceptance-v1' and value['status']=='ACCEPTED_MODEL_FREE_ONLY' and
        value['manifest_sha256']==manifest_sha and value['config_sha256']==config_sha and
        value['fixture_ids_sha256']==sha(canonical(ids)) and type(value['tests_run']) is int and value['tests_run']==42 and
        all(type(value[k]) is int and value[k]==0 for k in ('failures','errors','skips')) and
        value['cleanup']=='CONFIRMED' and value['actual_authorized'] is False,'completed_42_group_acceptance')
    tool=value['original_tool'];need(tool.get('positive_terminal') is True and type(tool.get('exit_code')) is int and
        tool['exit_code']==0 and type(tool.get('session_id')) is int,'fixture_outer_positive_original')
    guard=ref_read(value['guard_receipt'],32768)
    need(guard.get('status')=='PASS' and guard.get('cleanup')=='CONFIRMED' and guard.get('original_handle_waited') is True and
        guard.get('exit_code')==0 and guard.get('final_source_pin_verified') is True and not guard.get('errors'),
        'fixture_inner_positive_original')
    return value


def main():
    need(len(sys.argv)==3 and sys.argv[1] in MODE_STAGE,'closed_fixtures_or_split_mode')
    mode,release_sha=sys.argv[1:];need(len(release_sha)==64,'release_sha256_required')
    need(Path(sys.executable)==HOST and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
        'pinned_isolated_owner_interpreter')
    need(not any(k.startswith('FRESH_SEAM_') for k in os.environ),'no_inherited_preparation_or_actual_authority')
    os.umask(0o077);need(3 in os.sched_getaffinity(0),'cpu3_unavailable');os.sched_setaffinity(0,{3})
    os.setpriority(os.PRIO_PROCESS,0,19);resource.setrlimit(resource.RLIMIT_AS,(2**30,2**30))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    read(HOST,32*MIB,PY_PIN);read(PYTHON,32*MIB,PY_PIN)
    release_path=OUT/mode/'release.json';raw,pin=read(release_path,8192)
    need(pin['sha256']==release_sha,'preparation_config_pin');release=decode(raw)
    need(set(release)=={'schema','mode','authorized','manifest','owner','harness_config','fixture_acceptance',
            'actual_authorized'} and release['schema']=='fresh-seam-preparation-release-v1' and
        release['mode']==mode and release['authorized'] is True and release['actual_authorized'] is False,
        'separate_once_preparation_release')
    need(release['owner']['path']==str(HERE/'owner.py') and release['owner']['bytes']<=32768,'owner_source_reference')
    read(HERE/'owner.py',32768,{k:release['owner'][k] for k in ('bytes','sha256')})
    need(release['manifest']['path']==str(PACKET/'SOURCE_MANIFEST.json'),'packet_manifest_path')
    manifest=ref_read(release['manifest'],8192)
    need(manifest.get('schema')=='fresh-seam-source-manifest-v1' and manifest.get('status')=='PREPARED_UNEXECUTED' and
        manifest.get('aggregate_cap_bytes')==640*1024 and manifest.get('fixture_group_count')==42 and
        set(row['path'] for row in manifest['files'])==set(FILE_CAPS) and len(manifest['files'])==14,
        'closed_15_file_source_manifest')
    ids=manifest['fixture_ids'];need(type(ids) is list and ids==sorted(set(ids)) and len(ids)==42,'exact_42_ids')
    source_files={};harness_files={};external={str(release_path):release_sha,
        str(PACKET/'SOURCE_MANIFEST.json'):release['manifest']['sha256'],str(HERE/'owner.py'):release['owner']['sha256'],
        str(V8/'acquisition_guard.py'):GUARD_PIN['sha256'],str(V8/'bootstrap.py'):BOOTSTRAP_PIN['sha256']}
    logical=release['manifest']['bytes']
    for row in manifest['files']:
        relative=row['path'];need(row['cap_bytes']==FILE_CAPS[relative] and row['bytes']<=FILE_CAPS[relative],'source_leaf_cap')
        _,observed=read(PACKET/relative,FILE_CAPS[relative],{k:row[k] for k in ('bytes','sha256')});logical+=observed['bytes']
        if relative.startswith('controller/'):
            source_files[Path(relative).name]=row['sha256'];harness_files[str(PACKET/relative)]=observed
        else:external[str(PACKET/relative)]=row['sha256']
    need(logical==manifest['aggregate_bytes'] and logical<=640*1024,'inclusive_source_bytes')
    need({p.name for p in CONTROLLER.iterdir()}==set(source_files),'closed_controller_namespace')
    config_ref=release['harness_config'];need(config_ref['path']==str(PACKET/'config.template.json'),'preparation_template_path')
    harness_config=ref_read(config_ref,8192)
    need(harness_config['workspace_root']==str(BASE) and harness_config['paths']['harness_root']==str(CONTROLLER) and
        harness_config['paths']['python']==str(PYTHON) and harness_config['paths']['output_dir'] is None and
        harness_config['input_pins'] is None and harness_config['inventory'] is None and
        all(v is None for v in harness_config['prerequisites'].values()),'inert_preparation_config')
    need(config_ref['sha256']==external[str(PACKET/'config.template.json')],'manifest_template_pin')
    for relative,digest in harness_config['fixture_source_pins'].items():
        need(relative in {'audio_backend/boundaries.py','audio_backend/event_candidate_store.py',
            'audio_backend/worker.py','audio_backend/native_owned_cells.py'},'closed_pure_helper_source')
        path=BASE/'@GENERATED_PRODUCT_ROOT_REL@'/relative
        _,observed=read(path,262144);need(observed['sha256']==digest,'pure_product_helper_source_pin')
        external[str(path)]=digest
    if mode=='fixtures':need(release['fixture_acceptance'] is None,'fixture_no_prior_acceptance')
    else:
        need(release['fixture_acceptance']['path']==str(OUT/'fixtures/OWNER_ACCEPTANCE.json'),'prior_fixture_acceptance_path')
        fixture_acceptance(release['fixture_acceptance'],release['manifest']['sha256'],config_ref['sha256'],ids)
        external[release['fixture_acceptance']['path']]=release['fixture_acceptance']['sha256']
        need(not any((PACKET/'input'/name).exists() for name in INPUT_FILES),'fresh_split_outputs_only')
    phase=OUT/mode;need(phase.is_dir() and not any((phase/n).exists() for n in ('claim.json','config.json','spec.json','owner-result.json')),
        'single_phase_attempt_no_replay')
    audit_outputs(WORK_END)
    bootstrap=load_module('seam_preparation_census',V8/'bootstrap.py',BOOTSTRAP_PIN)
    guard=load_module('seam_preparation_guard',V8/'acquisition_guard.py',GUARD_PIN)
    guard.START=START;guard.ABSOLUTE_END=ABSOLUTE_END;guard.CHECKS=PACKET;guard.WORKSPACE=BASE
    guard.BOOT_ID=Path('/proc/sys/kernel/random/boot_id').read_text().strip();guard.PID_NS=os.readlink('/proc/self/ns/pid')
    guard.TICKS=os.sysconf('SC_CLK_TCK');guard.PAGE=os.sysconf('SC_PAGE_SIZE')
    guard.B_TIME=next(int(r.split()[1]) for r in Path('/proc/stat').read_text().splitlines() if r.startswith('btime '))
    own=guard.proc(os.getpid());launch={'start_monotonic':START,'work_end_monotonic':WORK_END,
        'absolute_end_monotonic':ABSOLUTE_END,'identity':{k:own[k] for k in ('pid','start_ticks','boot_id','pid_namespace')}}
    config={'schema':'fresh-seam-preparation-owner-config-v1','mode':mode,
        'roots':{'workspace':str(BASE),'source_recovery':str(BASE/'@GENERATED_SOURCE_AREA_REL@'),'packet':str(HERE),
            'product':str(CONTROLLER),'work':str(BASE/'@GENERATED_WORK_ROOT_REL@'),
            'runtime_container':str(BASE/'@GENERATED_RUNTIME_CONTAINER_REL@'),'proof_source':str(BASE/'@GENERATED_STORAGE_PROOF_ROOT_REL@')},
        'host':{'filesystem_device':BASE.stat().st_dev},
        'assets_manifest':{'path':str(BASE/'@GENERATED_ASSETS_MANIFEST_REL@')},
        'limits':{'category_bytes':bootstrap.CAPS},'invocation':{'phase':MODE_STAGE[mode],'launch':launch},
        'release_sha256':release_sha,'manifest_sha256':release['manifest']['sha256'],
        'preparation_output_cap_bytes':PREPARATION_CAP,'actual_authorized':False}
    usage=bootstrap.resource_usage(config,WORK_END)
    need(usage['source']+PREPARATION_CAP<=bootstrap.CAPS['source'],'preparation_source_partition_headroom')
    need(usage['evidence']+CONTROL_CAP<=bootstrap.CAPS['evidence'],'preparation_evidence_headroom')
    need(sum(usage.values())+PREPARATION_CAP+MIB<=12*2**30,'prospective_global_preparation_allocation')
    need(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'),'pidfds_required')
    need(ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)==0,'subreaper_required')
    need(not guard.children(os.getpid()),'no_preexisting_owned_children')
    claim=write_new(phase/'claim.json',{'schema':'fresh-seam-preparation-claim-v1','mode':mode,'attempts':1,
        'release_sha256':release_sha,'actual_authorized':False},1024)
    cref=write_new(phase/'config.json',config,16384)
    external[cref['path']]=cref['sha256'];external[claim['path']]=claim['sha256']
    adapter=Adapter(bootstrap,config,phase/'config.json',{k:cref[k] for k in ('bytes','sha256')},source_files,external,mode,config_ref)
    guard.CONFIG=config;guard.BOOTSTRAP=adapter
    spec=adapter.make_spec(config,cref['path'],cref['sha256'],MODE_STAGE[mode],launch)
    sref=write_new(phase/'spec.json',spec,32768)
    directory(PACKET/'runs');need(len(list((PACKET/'runs').iterdir()))==(0 if mode=='fixtures' else 1),'one_run_per_phase')
    original_write=guard.write_json
    def publish_guard_record(path,value):
        original_write(path,value)
        if path.name=='index.json' and value.get('status')=='GATED':
            need(value['argv']==spec['argv'] and value['spec_sha256']==sref['sha256'],'gated_exact_original_spec')
            original={k:value['original'][k] for k in ('pid','start_ticks')}
            authority={'schema':'fresh-seam-model-free-authority-v1' if mode=='fixtures' else 'fresh-seam-split-authority-v1',
                'authorized':True,'config_sha256':config_ref['sha256'],'run_directory':str(path.parent),
                'original':original,'harness_files':harness_files}
            if mode=='fixtures':authority['fixture_ids_sha256']=sha(canonical(ids))
            else:
                authority['source_pin']={'path':harness_config['paths']['input_wav'],
                    'bytes':672044,'sha256':harness_config['input_descriptor']['sha256']}
                authority['output_paths']={r:harness_config['paths']['upload_'+r] for r in ('a','b')}
            write_new(path.parent/('fixture-authority.json' if mode=='fixtures' else 'split-authority.json'),authority,16384)
        audit_outputs(ABSOLUTE_END-.3)
    guard.write_json=publish_guard_record
    runner=guard.Guard(spec,sref['sha256'])
    def stop(signum,frame):runner.signal_received=signum
    for signum in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(signum,stop)
    code=runner.run_command();audit_outputs(ABSOLUTE_END-.3)
    receipt_path=runner.run/'receipt.json';_,rpin=read(receipt_path,32768,end=ABSOLUTE_END-.3)
    result={'schema':'fresh-seam-preparation-owner-result-v1','mode':mode,'status':'INCOMPLETE',
        'guard_returncode':code,'guard_receipt':{'path':str(receipt_path),**rpin},
        'manifest_sha256':release['manifest']['sha256'],'config_sha256':config_ref['sha256'],
        'original_tool_terminal_required':True,'actual_authorized':False}
    if code==0:
        need(runner.receipt['cleanup']=='CONFIRMED' and runner.receipt['original_handle_waited'] is True and
            runner.receipt['exit_code']==0 and runner.receipt['final_source_pin_verified'] is True,'positive_original_guard_result')
        if mode=='fixtures':
            raw,_=read(runner.run/'output.log',256*1024,end=ABSOLUTE_END-.3)
            lines=raw.splitlines();need(lines,'fixture_result_line_missing');child=decode(lines[-1])
            need(child.get('schema')=='fresh-seam-model-free-result-v1' and child.get('label')=='EXPLICIT_SYNTHETIC_MODEL_FREE' and
                child.get('ids')==ids and child.get('harness_files')==harness_files and child.get('tests_run')==42 and
                all(child.get(k)==0 for k in ('failures','errors','skips','actual_acoustic_calls')) and
                child.get('historical_runs_adopted') is False,'exact_fresh_42_group_result')
            result.update(tests_run=42,failures=0,errors=0,skips=0,fixture_ids_sha256=sha(canonical(ids)),
                child_result_sha256=sha(lines[-1]),child_result_bytes=len(lines[-1]),actual_runtime_AdmissionTests_run=False)
        else:
            material,mpin=json_read(PACKET/'input/MATERIALIZATION.json',16384,end=ABSOLUTE_END-.3)
            need(material.get('schema')=='fresh-seam-split-result-v1' and
                material.get('status')=='MATERIALIZED_PENDING_OWNER_ACCEPTANCE' and
                material.get('config_sha256')==config_ref['sha256'] and material.get('actual_execution_authorized') is False and
                all(material.get(k) is True for k in ('output_readback','concatenated_pcm_equal','split_wav_digests_distinct')),
                'new_split_readback_result')
            result['materialization']={'path':str(PACKET/'input/MATERIALIZATION.json'),**mpin}
        result['status']='PREPARATION_COMPLETE_PENDING_ORIGINAL_TOOL_COMPLETION'
    check(ABSOLUTE_END-.3)
    # Final metadata publication fits in cleanup reserve; do not reset the work clock.
    raw=canonical(result)+b'\n';need(len(raw)<=8192,'owner_result_cap')
    fd=os.open(phase/'owner-result.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        need(os.write(fd,raw)==len(raw),'owner_result_short_write');os.fsync(fd)
    finally:os.close(fd)
    audit_outputs(ABSOLUTE_END-.1);print(json.dumps(result,sort_keys=True),flush=True)
    return code


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status':'OWNER_REFUSED_OR_INCOMPLETE','reason':str(error)[:500],
            'actual_authorized':False}),flush=True)
        raise SystemExit(2)
