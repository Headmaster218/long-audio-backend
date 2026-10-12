"""Closed static readelf worker beneath the unchanged qualified acquisition guard.
Source preparation only until separate exact review and serial storage admission.
"""
import time
START=time.monotonic()
import os
import sys
import stat
import json
import re
import hashlib
import resource
import signal
import subprocess
import importlib.util
from pathlib import Path
HERE=Path(__file__).resolve().parent
PREP=HERE.parent.parent
ALLOWLIST_SHA='0203fc9da45c45ee4cd7abc44289411518c41b9a71781b2bed72141c1889c7aa'
COMMON_SHA='98225798e5398fa4c6d0764f6bc4177eb3f85446ac65d38c38ecb039a974d316'
LOG_CAP=8192
REPORT_CAP=384*1024
ORIGINALS=[]
PINS={}
INSPECTION_END=None
BUILD=None
c=None
g=None
GEOMETRY_SHA='c7456c05a9d7bdd958b99d6ff8b2a88350f7856a7bf8d1b793a9f0f3f61a57a9'
GEOMETRY_QUALIFICATION='receipts/SQLITE_V2_GEOMETRY_QUALIFICATION.json'
GEOMETRY_QUALIFICATION_SHA='ca523151480bdab022df21a42c492f0c6ca7c54a58a1f92d6bf4cd4201f70102'


def require(ok, reason):
    if not ok:raise RuntimeError(reason)


def check():
    c.check()
    require(INSPECTION_END is not None and time.monotonic()<INSPECTION_END,'local_inspection_cutoff')


def output_membership():
    if BUILD is None:return
    count=0
    with os.scandir(BUILD) as entries:
        for entry in entries:
            count+=1;require(count<=64,'output_membership_count')
            st=entry.stat(follow_symlinks=False)
            require(stat.S_ISREG(st.st_mode) and st.st_nlink==1,'output_type')
            if entry.name=='runtime-static-inventory.json':cap=REPORT_CAP
            else:
                require(entry.name.endswith('.log') and entry.name[:-4] in PINS,'unexpected_output')
                cap=LOG_CAP
            require(st.st_size<=cap,'output_size')


def category():
    output_membership()
    return g.category(checker=check)


def bounded(path, cap, end=None):
    return c.regular(path,maximum=cap,checker=check,contents=True)


def identity(st):return c.identity(st)


def verify_open(pin):
    check()
    path=Path(pin['path'])
    require(str(path.resolve(strict=True))==str(path),'canonical_candidate')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size==pin['bytes'],'candidate_size')
        h=hashlib.sha256();count=0
        while True:
            check();block=os.read(fd,min(65536,pin['bytes']-count+1))
            if not block:break
            count+=len(block);require(count<=pin['bytes'],'candidate_growth');h.update(block)
        require(count==pin['bytes'] and h.hexdigest()==pin['sha256'],'candidate_pin')
        require(identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'candidate_changed')
        check();return fd,identity(before)
    except BaseException:
        os.close(fd);raise


def expand(path):return str(PREP)+path[5:] if path.startswith('@prep/') else path


def load_allowlist():
    raw=bounded(HERE/'allowlist.json',48*1024)
    require(hashlib.sha256(raw).hexdigest()==ALLOWLIST_SHA,'allowlist_pin')
    data=json.loads(raw)
    require(data['schema']=='rc3-dev3-static-runtime-allowlist-v1' and data['root']==str(PREP),'allowlist_scope')
    for pin in data['candidates']:pin['path']=expand(pin['path'])
    for alias in data['aliases']:
        alias['path']=expand(alias['path']);alias['resolved_target']=expand(alias['resolved_target'])
    for pin in data['inventories']:pin['path']=expand(pin['path'])
    return data


def source_inventories(data):
    loaded={};total=0
    require(len(data['inventories'])==6,'receipt_count')
    for pin in data['inventories']:
        raw=bounded(pin['path'],pin['bytes']);total+=len(raw)
        require(total<=1180011,'receipt_bytes')
        require(len(raw)==pin['bytes'] and hashlib.sha256(raw).hexdigest()==pin['sha256'],'receipt_pin')
        loaded[pin['id']]=json.loads(raw)
    require(total==1180011,'receipt_total');return loaded


def readelf_argv(step):
    # This fixed dispatch is the derivative's only command seam.
    require(step in PINS and len(PINS)==63,'closed_readelf_step')
    return ['/usr/bin/x86_64-linux-gnu-readelf','--wide','--file-header',
            '--program-headers','--dynamic','--',PINS[step]['path']]


def parse_output(raw):
    require(len(raw)<=LOG_CAP,'merged_output_cap')
    text=raw.decode('utf-8','strict')
    require(text.startswith('ELF Header:') and 'Class:                             ELF64' in text
            and 'little endian' in text and 'Advanced Micro Devices X86-64' in text,'elf_identity')
    require(not re.search(r'(?im)readelf:|warning:|error:|corrupt',text),'readelf_diagnostic')
    tags={'needed':[],'rpath':[],'runpath':[],'soname':[]}
    for line in text.splitlines():
        for word,key in [('NEEDED','needed'),('RPATH','rpath'),('RUNPATH','runpath'),('SONAME','soname')]:
            if '('+word+')' in line:
                match=re.search(r'\[([^\]]*)\]',line)
                require(match is not None,'unparsed_dynamic_tag');tags[key].append(match.group(1))
    require(all(len(v)<=64 for v in tags.values()),'dynamic_tag_count')
    interpreter=re.findall(r'\[Requesting program interpreter: ([^\]]+)\]',text)
    require(len(interpreter)<=1,'interpreter_count')
    return tags,interpreter


def analyze(rows,data):
    by_name={};by_path={r['path']:r for r in rows}
    for row in rows:
        for name in set([Path(row['path']).name]+row['elf']['soname']):
            by_name.setdefault(name,[]).append(row['path'])
    for alias in data['aliases']:
        by_name.setdefault(Path(alias['path']).name,[]).append(alias['resolved_target'])
    dirs={str(Path(p).parent) for p in by_path}
    paths=set(by_path)|{a['path'] for a in data['aliases']}
    unknowns=[]
    for row in rows:
        relations=[]
        for needed in row['elf']['needed']:
            candidates=sorted(set(by_name.get(needed,[])))
            relations.append({'needed':needed,'finite_candidates':candidates,'loaded_origin':'UNOBSERVED',
                              'ambiguous':len(candidates)>1})
            if not candidates:unknowns.append({'path':row['path'],'unresolved_needed':needed})
        row['needed_candidates']=relations
        for kind in ('rpath','runpath'):
            for value in row['elf'][kind]:
                for part in value.split(':'):
                    exp=part.replace('${ORIGIN}',str(Path(row['path']).parent)).replace('$ORIGIN',str(Path(row['path']).parent))
                    if not part or '$' in exp or not os.path.isabs(exp) or os.path.normpath(exp) not in dirs:
                        unknowns.append({'path':row['path'],'unresolved_search_path':part,'tag':kind})
        for interp in row['interpreter']:
            if interp not in paths:unknowns.append({'path':row['path'],'unresolved_interpreter':interp})
    return unknowns


def reused_runtime(source,data):
    files={}
    for pin in source['host']['stdlib_files']+source['host']['mapped_system_files']+[source['host']['python']]+[
            p for p in data['candidates'] if 'installed' not in p['roles']]:
        value={'bytes':pin['bytes'],'sha256':pin['sha256']};path=pin['path']
        require(path not in files or files[path]==value,'pin_conflict');files[path]=value
    return files


def receipt_check():
    # Failure bookkeeping can use remaining inherited work time; never renew it.
    require(time.monotonic()<float(os.environ['GUARD_WORK_END']),'receipt_work_deadline')


def save(report):
    receipt_check();output_membership()
    raw=json.dumps(report,sort_keys=True,separators=(',',':')).encode()+b'\n'
    require(len(raw)<=REPORT_CAP,'report_cap')
    resource.setrlimit(resource.RLIMIT_FSIZE,(REPORT_CAP,REPORT_CAP))
    with (BUILD/'runtime-static-inventory.json').open('xb') as out:
        for offset in range(0,len(raw),65536):receipt_check();out.write(raw[offset:offset+65536])
        out.flush();os.fsync(out.fileno())
    receipt_check();output_membership()


def bind_geometry_qualification():
    raw=bounded(PREP/GEOMETRY_QUALIFICATION,1497)
    require(len(raw)==1497 and hashlib.sha256(raw).hexdigest()==GEOMETRY_QUALIFICATION_SHA,'geometry_qualification_pin')
    q=json.loads(raw)
    require(q['status']=='PASS' and q['cases']==18 and q['cleanup']=='CONFIRMED'
            and q['source_final_verified'] is True and q['run']=='run-2ztbey_o','geometry_qualification_result')
    pins=q['pins']
    require(type(pins) is dict and len(pins)==9 and sum(p['bytes'] for p in pins.values())==21124,'qualification_nine_pin_bounds')
    for rel,pin in pins.items():
        require(type(pin['bytes']) is int and 0<pin['bytes']<=8192,'qualification_pin_size')
        path=c.safe_path(PREP,rel)
        require(c.regular(path,maximum=pin['bytes'],checker=check)==pin,'qualification_dependency_pin')
    return {'path':str(PREP/GEOMETRY_QUALIFICATION),'bytes':1497,'sha256':GEOMETRY_QUALIFICATION_SHA,'pins':pins}


def boot():
    global c,g,INSPECTION_END,BUILD
    require(sys.argv[1:]==[],'no_worker_arguments')
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,'isolated_flags')
    path=PREP/'installation/common.py'
    # Outer guard has independently prehashed this exact file; recheck before importing it.
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        st=os.fstat(fd);require(stat.S_ISREG(st.st_mode) and st.st_size==14162,'common_size')
        raw=os.read(fd,14163)
        require(len(raw)==14162 and hashlib.sha256(raw).hexdigest()==COMMON_SHA,'common_pin')
        require((st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)==
                tuple(getattr(os.fstat(fd),k) for k in ('st_ino','st_size','st_mtime_ns','st_ctime_ns')),'common_changed')
    finally:os.close(fd)
    spec=importlib.util.spec_from_file_location('static_runtime_common',path)
    c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
    c.check();INSPECTION_END=min(START+180.0,float(os.environ['GUARD_WORK_END']))
    require(float(os.environ['GUARD_ABSOLUTE_END'])-float(os.environ['GUARD_WORK_END'])==60.,'outer_cleanup_contract')
    check();c.privacy()
    qualification=bind_geometry_qualification()
    geometry=PREP/'storage-proof/geometry.py'
    raw=c.regular(geometry,maximum=9561,checker=check,contents=True)
    require(len(raw)==9561 and hashlib.sha256(raw).hexdigest()==GEOMETRY_SHA,'geometry_pin')
    spec=importlib.util.spec_from_file_location('static_runtime_geometry',geometry)
    g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
    require(os.sched_getaffinity(0)=={3} and os.getpriority(os.PRIO_PROCESS,0)==19,'inherited_CPU_nice')
    # Supplemental per-process AS; the unchanged guard owns aggregate RSS/high-water evidence.
    resource.setrlimit(resource.RLIMIT_AS,(64*1024*1024,64*1024*1024))
    resource.setrlimit(resource.RLIMIT_FSIZE,(LOG_CAP,REPORT_CAP))
    run=Path(os.environ['GUARD_RUN_DIRECTORY'])
    BUILD=c.safe_path(PREP,'build/runtime-inventory-'+run.name)
    BUILD.mkdir(mode=0o700)  # Exclusive, run-bound top-level build classification.
    output_membership()
    return qualification


def main():
    qualification=boot();rows=[];receipts=[];error=None;data=None;source=None
    try:
        data=load_allowlist();source=source_inventories(data)
        require(Path(sys.executable).resolve()==Path(data['host_python']['path']),'host_python_origin')
        fd,_=verify_open(data['host_python']);os.close(fd)
        host_root=str(Path(data['host_python']['path']).parent.parent)
        require(all(p in [host_root+'/lib/python312.zip',host_root+'/lib/python3.12',
                         host_root+'/lib/python3.12/lib-dynload'] for p in sys.path),'host_sys_path')
        require(data['candidate_count']==len(data['candidates'])==63 and
                data['candidate_bytes']==sum(p['bytes'] for p in data['candidates'])==183119537,'exact_candidate_bounds')
        require(len({p['path'] for p in data['candidates']})==63 and
                all(0<p['bytes']<=128*1024*1024 for p in data['candidates']),'candidate_bounds')
        require(len(data['aliases'])==17,'alias_count')
        targets={p['path'] for p in data['candidates']}
        for alias in data['aliases']:
            check();p=Path(alias['path'])
            require(p.is_symlink() and os.readlink(p)==alias['literal_target'] and
                    str(p.resolve(strict=True))==alias['resolved_target'] and alias['resolved_target'] in targets,'alias_pin')
        PINS.update({'elf-%04d'%i:p for i,p in enumerate(data['candidates'])})
        tool=next(p for p in data['candidates'] if p['path']=='/usr/bin/x86_64-linux-gnu-readelf')
        tool_fd,tool_identity=verify_open(tool)
        try:
            for step,pin in PINS.items():
                check();fd,before=verify_open(pin)
                try:
                    child(step,receipts)
                    require(identity(os.fstat(fd))==before==identity(Path(pin['path']).lstat()),'inspected_identity_changed')
                    require(identity(os.fstat(tool_fd))==tool_identity==identity(Path(tool['path']).lstat()),'tool_identity_changed')
                    log=BUILD/(step+'.log');raw=bounded(log,LOG_CAP)
                    tags,interpreter=parse_output(raw);check()
                    rows.append({'path':pin['path'],'bytes':pin['bytes'],'sha256':pin['sha256'],
                                 'roles':pin['roles'],'elf':tags,'interpreter':interpreter,
                                 'merged_stdout_stderr':{'path':str(log),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()},
                                 'file_identity':list(before)})
                finally:os.close(fd)
        finally:os.close(tool_fd)
    except BaseException as exc:
        error=c.failure_summary(exc)
    unknowns=analyze(rows,data) if data is not None else []
    complete=(error is None and len(rows)==63 and not unknowns and all(x['original_waited'] for x in receipts))
    report={'schema':'rc3-dev3-guarded-static-runtime-inspection-v2','status':'STATIC_CANDIDATES_CLOSED_NOT_RUNTIME_QUALIFIED' if complete else 'INCOMPLETE',
            'actual_run_authorized':False,'inference_calls':0,'geometry_qualification':qualification,'rows':rows,'unknowns':unknowns,'failure':error,
            'ownership_substeps':receipts,'terminal_ownership_resource_evidence':'Only the unchanged outer guard terminal receipt can qualify this attempt.',
            'retained_merged_logs':[str(BUILD/(step+'.log')) for step in PINS if (BUILD/(step+'.log')).exists()],
            'allowlist_sha256':ALLOWLIST_SHA,'elapsed_seconds':time.monotonic()-START,'inspection_cutoff':INSPECTION_END,
            'limits':{'inspection_seconds':180,'outer_seconds':300,'outer_work_seconds':240,'outer_cleanup_seconds':60,
                      'launches_max':63,'merged_log_each_bytes':LOG_CAP,'merged_log_total_max_bytes':63*LOG_CAP,
                      'report_bytes':REPORT_CAP,'worker_outputs_allocated_ceiling_including_build_dir':913408,
                      'future_separate_storage_reservation_bytes':1048576,'reservation_claimed_by_worker':False}}
    if data is not None:
        report.update(source_inventory_pins=data['inventories'],runtime_aliases=data['aliases'],
                      controller_composition=data['controller_composition'],known_ambiguities=data['known_ambiguities'],
                      remaining_gates=data['remaining_gates'])
    if data is not None and source is not None:report['runtime_files']=reused_runtime(source,data)
    save(report)
    require(complete,'static_inventory_incomplete')


# CLOSED_CHILD_DERIVATIVE inserted below; source preparation does not execute it.
def child(step, receipt):
    check();category()
    log=BUILD/(step+'.log')
    env=dict(os.environ)
    for key in ('PYTHONPATH','PYTHONHOME','LD_PRELOAD','LD_LIBRARY_PATH','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE'):env.pop(key,None)
    env.update({'PYTHONSAFEPATH':'1','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1',
                'PYTHONPYCACHEPREFIX':str(Path(env['GUARD_RUN_DIRECTORY'])/'tmp/never-bytecode'),
                'PIP_CONFIG_FILE':os.devnull,'PIP_NO_INDEX':'1','PIP_NO_CACHE_DIR':'1',
                'PIP_DISABLE_PIP_VERSION_CHECK':'1','SOURCE_DATE_EPOCH':'0'})
    env['GUARD_WORK_END']=str(INSPECTION_END)
    env['LANG']='C';env['LC_ALL']='C'
    p=None;pidfd=None;waited=False;code=None;error=None
    max_gap=0.;last=time.monotonic();peak=0
    with log.open('xb') as output:
        try:
            check()
            p=subprocess.Popen(readelf_argv(step),cwd=c.PREP,env=env,
                               stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT)
            ORIGINALS.append(p)  # Retain the original before any identity operation or wait.
            pidfd=os.pidfd_open(p.pid,0)
            while True:
                check();value=category();peak=max(peak,value['charged_bytes'])
                now=time.monotonic();max_gap=max(max_gap,now-last);last=now
                c.require(log.stat().st_size<=LOG_CAP,'substep log cap')
                if p.poll() is not None:break
                time.sleep(min(.05,max(0,float(env['GUARD_WORK_END'])-time.monotonic())))
            code=p.wait(timeout=c.remaining_timeout(float(env['GUARD_WORK_END']),time.monotonic(),.5));waited=True
            check();category();c.require(code==0,'substep nonzero: '+step)
        except BaseException as exc:
            error=c.failure_summary(exc)
            if p is not None and not waited:
                if pidfd is None:
                    Path(env['GUARD_STOP_PATH']).touch(exist_ok=True)
                else:
                    try:signal.pidfd_send_signal(pidfd,signal.SIGKILL)
                    except ProcessLookupError:pass
                    try:
                        left=c.remaining_timeout(float(env['GUARD_ABSOLUTE_END']),time.monotonic(),5.,reserve=2.)
                        code=p.wait(timeout=left);waited=True
                    except (c.Refused,subprocess.TimeoutExpired):
                        pass
            raise
        finally:
            if pidfd is not None:os.close(pidfd)
            receipt.append({'step':step,'original_retained':p is not None,'original_waited':waited,
                            'returncode':code,'error':error,'sample_gap_seconds_max':max_gap,
                            'peak_category_charged_bytes':peak})


if __name__=='__main__':
    try:
        main()
    except BaseException as exc:
        if c is not None:
            sys.stderr.write(json.dumps({'status':'INCOMPLETE','failure':c.failure_summary(exc)})+'\n')
        else:sys.stderr.write('{"status":"INCOMPLETE","failure":"bootstrap"}\n')
        raise SystemExit(2)
