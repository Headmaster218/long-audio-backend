"""Run existing finite target evidence steps; never approve, install or launch.

Use the intended fresh target virtualenv with -I -B. Test tooling stays external.
Any failed/interrupted output is retained and cannot be resumed or overwritten.
"""
import _sqlite3
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import site
import sqlite3
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

SCHEMA='portable-target-evidence-preparation-v1'

def require(value,code):
    if not value:raise ValueError(code)

def check_deadline(deadline):
    if deadline is not None:require(time.monotonic()<deadline,'preparation_deadline')

def digest(path,deadline=None):
    with path.open('rb') as stream:
        result=hashlib.sha256()
        for block in iter(lambda:stream.read(1024*1024),b''):
            check_deadline(deadline);result.update(block)
    return result.hexdigest()

def canonical(value):
    path=Path(value)
    require(path.is_absolute() and path==path.resolve(strict=False),'canonical_absolute_path_required')
    for parent in (path,*path.parents):require(not parent.is_symlink(),'symlink_refused')
    return path

def private(path):
    item=path.stat();require(stat.S_ISDIR(item.st_mode) and item.st_uid==os.getuid() and item.st_mode&0o077==0,'private_owned_directory_required')

def inventory(root,maximum_bytes=1024**3,maximum_files=20000,deadline=None):
    result={};total=0;entries=0;pending=[root]
    while pending:
        check_deadline(deadline);directory=pending.pop()
        with os.scandir(directory) as scan:
            for entry in scan:
                check_deadline(deadline);entries+=1
                require(entries<=maximum_files*2,'inventory_entry_bound')
                path=Path(entry.path);info=entry.stat(follow_symlinks=False)
                require(not stat.S_ISLNK(info.st_mode),'inventory_symlink_refused')
                if stat.S_ISDIR(info.st_mode):pending.append(path);continue
                require(stat.S_ISREG(info.st_mode),'inventory_regular_files_required')
                total+=info.st_size;require(total<=maximum_bytes and len(result)<maximum_files,'inventory_bound')
                stamp=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
                value=digest(path,deadline);require(stamp(path.stat())==stamp(info),'inventory_file_changed')
                result[str(path.relative_to(root))]=dict(bytes=info.st_size,sha256=value)
    return result

def source_inventory(source,manifest,manifest_sha256,deadline=None):
    require(manifest.stat().st_size<=4*1024**2 and digest(manifest,deadline)==manifest_sha256,'source_manifest_hash')
    value=json.loads(manifest.read_bytes());require(value['schema']=='standalone-source-checkpoint-v1','source_manifest_schema')
    expected={}
    for row in value['files']:
        name=row['path'];p=Path(name)
        require(not p.is_absolute() and '..' not in p.parts and str(p)==name and name not in expected,'source_manifest_path')
        expected[name]=dict(bytes=row['size'],sha256=row['sha256'])
    require(expected==inventory(source,128*1024**2,5000,deadline),'source_bytes_or_roster_changed')
    require((source/'qualification/prepare_target_evidence.py').resolve()==Path(__file__).resolve(),'run_selected_source_tool')
    return expected

def dependencies(deadline=None):
    paths={canonical(p) for p in site.getsitepackages() if Path(p).is_dir()}
    paths.add(canonical(str(Path(sqlite3.__file__).parent)))
    result={str(path):inventory(path,deadline=deadline) for path in sorted(paths)}
    files={Path(sys.executable).resolve()}
    extension=getattr(_sqlite3,'__file__',None)
    if extension is not None:files.add(Path(extension).resolve())
    else:require(_sqlite3.__spec__.origin=='built-in','unsupported_sqlite_binding')
    for line in Path('/proc/self/maps').read_text().splitlines():
        fields=line.split()
        if len(fields)>=6 and 'libsqlite3' in fields[-1] and fields[-1].startswith('/'):
            files.add(Path(fields[-1]).resolve())
    result['runtime_binary_files']={str(f):dict(bytes=f.stat().st_size,sha256=digest(f,deadline)) for f in sorted(files)}
    return result

def save(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as out:json.dump(value,out,sort_keys=True,indent=2);out.write('\n');out.flush();os.fsync(out.fileno())

TEST_CHILD = r'''
import hashlib,json,os,resource,signal,sys
from pathlib import Path
s,p,t=map(Path,sys.argv[1:4])
os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3));resource.setrlimit(resource.RLIMIT_CPU,(90,100));signal.alarm(120)
h=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
os.environ.update(TARGET_OBSERVATION=str(p/'target.json'),TARGET_SHA256=h(p/'target.json'),TARGET_AUDIT=str(p/'audit.json'),TARGET_AUDIT_SHA256=h(p/'audit.json'),TARGET_EVIDENCE=str(p/'physical'),TARGET_RESULT_SHA256=h(p/'physical/RESULT.json'),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
# Standard library precedes external tools; source/test roots deliberately select
# the unchanged source under qualification, never the installed product.
sys.path[:0]=[str(s),str(s/'tests')];sys.path.append(str(t))
import pytest
assert Path(pytest.__file__).resolve().is_relative_to(t)
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider',str(s/'qualification/test_target_candidate_cellstore.py'),'--basetemp',str(p/'test-temp'),'--junitxml',str(p/'candidate-tests.xml')]))
'''


def prepare(*,source,manifest,manifest_sha256,output,test_tools,acknowledge_physical_probes=False):
    started=time.monotonic();deadline=started+600
    require(acknowledge_physical_probes is True,'explicit_physical_probe_ack_required')
    require(sys.platform=='linux' and sys.flags.isolated and sys.dont_write_bytecode,'linux_isolated_no_bytecode_required')
    require(sys.prefix!=sys.base_prefix,'fresh_target_virtualenv_required')
    require(len(os.sched_getaffinity(0))>=2,'two_cpus_required')
    source,manifest,out,tools=map(canonical,(source,manifest,output,test_tools))
    private(out.parent);require(not os.path.lexists(out),'fresh_output_required')
    require(not any(out.is_relative_to(p) or p.is_relative_to(out) for p in (source,tools,Path(sys.prefix))),'output_overlap_refused')
    protected=[source,tools,Path(sys.prefix).resolve()]
    for i,path in enumerate(protected):
        require(not any(path.is_relative_to(other) or other.is_relative_to(path) for other in protected[:i]),'source_tools_target_overlap')
    for package_path in site.getsitepackages():
        pp=Path(package_path).resolve()
        require(not tools.is_relative_to(pp) and not pp.is_relative_to(tools),'test_tools_must_be_external')
    before_source=source_inventory(source,manifest,manifest_sha256,deadline)
    before_tools=inventory(tools,128*1024**2,10000,deadline)
    require('pytest/__init__.py' in before_tools,'external_pytest_required')
    before_dependencies=dependencies(deadline)
    env={k:v for k,v in os.environ.items() if not (k.startswith('PYTHON') or k.startswith('PYTEST') or k.startswith('TARGET_'))}
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    os.umask(0o077);out.mkdir(mode=0o700)
    result=dict(schema=SCHEMA,status='failed',review_needed=False,production_profile_activated=False,
        services_started=False,models_loaded=False,approval_generated=False,automatic_retry=False,commands=[],
        target_executable=sys.executable,source_manifest_sha256=manifest_sha256)
    active=None
    def interrupted(signum,frame):raise InterruptedError('preparation_interrupted')
    old={sig:signal.signal(sig,interrupted) for sig in (signal.SIGINT,signal.SIGTERM)}
    def run(name,args,timeout):
        nonlocal active
        require(time.monotonic()-started<600,'total_deadline')
        command=[sys.executable,'-I','-B',*map(str,args)]
        row=dict(name=name,argv=command,timeout_seconds=timeout);result['commands'].append(row)
        save(out/(name+'.command.json'),row)
        with (out/(name+'.stdout')).open('x') as stdout,(out/(name+'.stderr')).open('x') as stderr:
            try:
                mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGINT,signal.SIGTERM})
                try:
                    active=subprocess.Popen(command,cwd=source,env=env,stdout=stdout,stderr=stderr,start_new_session=True,
                        preexec_fn=lambda:signal.pthread_sigmask(signal.SIG_SETMASK,mask))
                finally:signal.pthread_sigmask(signal.SIG_SETMASK,mask)
                code=active.wait(timeout=min(timeout,max(1,600-(time.monotonic()-started))))
                row['exit_code']=code
            finally:
                cleanup_mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGINT,signal.SIGTERM})
                try:
                    if active is not None and active.poll() is None:
                        # Physical supervisor handles TERM/waits its child, which
                        # also retains the original parent-death SIGKILL guard.
                        active.terminate()
                        try:row['cleanup_exit_code']=active.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            os.killpg(active.pid,signal.SIGKILL);row['forced_group_kill']=True;row['cleanup_exit_code']=active.wait(timeout=10)
                    active=None
                finally:signal.pthread_sigmask(signal.SIG_SETMASK,cleanup_mask)
        require(code==0,'step_failed:'+name)
    try:
        save(out/'source-before.json',before_source);save(out/'test-tools-before.json',before_tools);save(out/'dependencies-before.json',before_dependencies)
        (out/'target-data').mkdir(mode=0o700)
        run('observation',[source/'qualification/target_runtime_observation.py','--data-dir',out/'target-data','--output',out/'target.json'],30)
        target=digest(out/'target.json')
        run('physical',[source/'qualification/cellstore_crash_probe.py',out/'physical','--target-observation',out/'target.json','--target-sha256',target],270)
        run('audit',[source/'qualification/audit_target_cellstore_evidence.py','--evidence',out/'physical','--source',source,'--report',out/'audit.json','--target-observation',out/'target.json','--target-sha256',target],120)
        run('candidate-tests',['-c',TEST_CHILD,source,out,tools],130)
        suites=list(ET.parse(out/'candidate-tests.xml').getroot().iter('testsuite'))
        require(sum(int(s.get('tests',0)) for s in suites)==9 and all(int(s.get(k,0))==0 for s in suites for k in ('errors','failures','skipped')),'nine_real_passes_required')
        run('observation-after',[source/'qualification/target_runtime_observation.py','--data-dir',out/'target-data','--output',out/'target-after.json'],30)
        require(json.loads((out/'target.json').read_bytes())==json.loads((out/'target-after.json').read_bytes()),'target_identity_changed')
        require(before_source==source_inventory(source,manifest,manifest_sha256,deadline),'source_changed')
        require(before_tools==inventory(tools,128*1024**2,10000,deadline),'test_tools_changed')
        after_dependencies=dependencies(deadline);save(out/'dependencies-after.json',after_dependencies)
        require(before_dependencies==after_dependencies,'target_dependencies_changed')
        receipt=dict(schema='target-candidate-regression-receipt-v1',tests_passed=9,exit_code=0,production_profile_activated=False,
            source_manifest_sha256=manifest_sha256,source_module_sha256=digest(source/'audio_backend/native_owned_cell_store.py'),
            target_sha256=target,audit_sha256=digest(out/'audit.json'),physical_result_sha256=digest(out/'physical/RESULT.json'),
            junit_sha256=digest(out/'candidate-tests.xml'),test_tools_inventory_sha256=digest(out/'test-tools-before.json'),
            dependency_inventory_sha256=digest(out/'dependencies-before.json'))
        save(out/'candidate-tests-receipt.json',receipt)
        result.update(status='review_needed',review_needed=True,qualified=False,
            hashes={name:digest(out/name) for name in ('target.json','audit.json','physical/RESULT.json','candidate-tests-receipt.json')})
    except BaseException as error:
        result.update(error_type=type(error).__name__,status='failed',review_needed=False)
    finally:
        for sig,handler in old.items():signal.signal(sig,handler)
        result['elapsed_seconds']=time.monotonic()-started
        # Bounded final observation even after a failed step; never approval.
        final_deadline=min(deadline,time.monotonic()+30) if result['status']=='review_needed' else time.monotonic()+30
        checks=(('target_dependencies_unchanged',lambda:dependencies(final_deadline)==before_dependencies),
                ('source_unchanged',lambda:source_inventory(source,manifest,manifest_sha256,final_deadline)==before_source),
                ('test_tools_unchanged',lambda:inventory(tools,128*1024**2,10000,final_deadline)==before_tools))
        for key,observe in checks:
            try:result[key]=observe()
            except Exception:result[key]=False
        if not all(result[key] for key,_ in checks):result.update(status='failed',review_needed=False)
        save(out/'RESULT.json',result)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','manifest','manifest-sha256','output','test-tools'):p.add_argument('--'+name,required=True)
    p.add_argument('--acknowledge-physical-probes',action='store_true',required=True)
    try:result=prepare(**vars(p.parse_args(argv)))
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) and str(error).replace('_','').isalnum() and len(str(error))<=80 else 'input_or_environment_validation_failed'
        result=dict(schema=SCHEMA,status='refused',reason=code,review_needed=False,automatic_retry=False)
    print(json.dumps(result,sort_keys=True));return 0 if result['status']=='review_needed' else 2

if __name__=='__main__':raise SystemExit(main())
