"""Exactly three pinned loader --list observations. No target entrypoint/inference.
Dormant until exact review, model-free checks and fresh serial admission.
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
LOADER='/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'
PROGRAMS={'loader-ffmpeg':'/usr/bin/ffmpeg','loader-ffprobe':'/usr/bin/ffprobe',
          'loader-readelf':'/usr/bin/x86_64-linux-gnu-readelf'}
SYSTEM='/usr/lib/x86_64-linux-gnu'
LOADER_ALIASES=('/lib64/ld-linux-x86-64.so.2','/usr/lib64/ld-linux-x86-64.so.2')
ROOT_LINKS={'/lib':'usr/lib','/lib64':'usr/lib64'}
ALTERNATIVE_CHAINS={
    SYSTEM+'/libblas.so.3':('/etc/alternatives/libblas.so.3-x86_64-linux-gnu',SYSTEM+'/blas/libblas.so.3','libblas.so.3.12.1',505696),
    SYSTEM+'/liblapack.so.3':('/etc/alternatives/liblapack.so.3-x86_64-linux-gnu',SYSTEM+'/lapack/liblapack.so.3','liblapack.so.3.12.1',7604088)}
ALTERNATIVE_LINK_ROOT={path:first for first,(middle,last,tail,size) in ALTERNATIVE_CHAINS.items() for path in (first,middle,last)}
LOG_CAP=32768
REPORT_CAP=163840
MAX_FILES=256
MAX_FILE_BYTES=128*1024*1024
MAX_TOTAL_BYTES=384*1024*1024
MAX_ALIASES=256
ORIGINALS=[]
BUILD=None
INSPECTION_END=None
c=None
g=None
FILE_ALIASES={}
DIRECTORY_ALIASES={}
SPELLINGS={}


def require(ok,reason):
    if not ok:raise RuntimeError(reason)


def check():
    c.check();require(INSPECTION_END is not None and time.monotonic()<INSPECTION_END,'local_cutoff')


def output_membership():
    if BUILD is None:return
    count=0
    with os.scandir(BUILD) as entries:
        for entry in entries:
            count+=1;require(count<=4,'output_count')
            st=entry.stat(follow_symlinks=False)
            require(stat.S_ISREG(st.st_mode) and st.st_nlink==1,'output_type')
            cap=REPORT_CAP if entry.name=='loader-context.json' else LOG_CAP
            require(entry.name=='loader-context.json' or entry.name in {s+'.log' for s in PROGRAMS},'output_name')
            require(st.st_size<=cap,'output_cap')


def category():
    output_membership();return g.category(checker=check)


def bounded(path,cap):return c.regular(path,maximum=cap,checker=check,contents=True)


def loader_argv(step):
    require(type(step) is str and step in PROGRAMS,'closed_loader_target')
    return [LOADER,'--list',PROGRAMS[step]]


def readelf_argv(step):
    # Compatibility seam keeps the accepted original child body AST identical.
    return loader_argv(step)


def allowed_spelling(path):
    require(type(path) is str and len(path)<=512 and path.startswith('/') and
            re.fullmatch(r'/[A-Za-z0-9_+./-]+',path) is not None,'reported_path_syntax')
    require('//' not in path and '..' not in Path(path).parts and '.' not in Path(path).parts
            and str(Path(path))==path,'reported_path_normalization')
    require(path.startswith(SYSTEM+'/') or path.startswith('/lib/x86_64-linux-gnu/')
            or path in LOADER_ALIASES,'reported_path_outside_system_roots')
    require(len(Path(path).parts)<=10,'reported_path_depth')
    return path


def parse_listing(raw):
    require(type(raw) is bytes and len(raw)<=LOG_CAP,'loader_output_cap')
    lines=raw.decode('ascii','strict').splitlines()
    require(1<=len(lines)<=258,'loader_line_count')
    rows=[];names=set();vdso=False
    for line in lines:
        text=line.strip()
        if not text:continue
        m=re.fullmatch(r'linux-vdso\.so\.1 \(0x[0-9a-fA-F]+\)',text)
        if m:
            require(not vdso,'duplicate_vdso');vdso=True;continue
        arrow='/lib64/ld-linux-x86-64.so.2 => '+LOADER
        if text.startswith(arrow+' '):
            text='/lib64/ld-linux-x86-64.so.2'+text[len(arrow):]
        m=re.fullmatch(r'([A-Za-z0-9_+.-]+) => (/[^\s]+) \(0x[0-9a-fA-F]+\)',text)
        if m:soname,path=m.groups()
        else:
            m=re.fullmatch(r'(/[^\s]+) \(0x[0-9a-fA-F]+\)',text)
            require(m is not None,'loader_unparsed_or_warning_line')
            path=m.group(1);require(path==LOADER or path in LOADER_ALIASES or
                                   path=='/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2','unexpected_direct_loader_line')
            soname='ld-linux-x86-64.so.2'
        allowed_spelling(path)
        require(soname not in names and len(rows)<MAX_FILES,'loader_duplicate_or_count')
        names.add(soname);rows.append({'soname':soname,'reported_path':path})
    require(rows,'empty_loader_listing')
    return rows,vdso


def record_alias(store,path,literal,resolved):
    row={'path':path,'literal_target':literal,('normalized_target' if store is DIRECTORY_ALIASES else 'resolved_target'):resolved}
    require(path not in (FILE_ALIASES if store is DIRECTORY_ALIASES else DIRECTORY_ALIASES),'alias_kind_conflict')
    require(path not in store or store[path]==row,'alias_changed')
    store[path]=row
    require(len(FILE_ALIASES)+len(DIRECTORY_ALIASES)<=MAX_ALIASES,'alias_count')


def checked_alternative(first,readlink):
    require(first in ALTERNATIVE_CHAINS,'unapproved_alternative')
    middle,last,tail,size=ALTERNATIVE_CHAINS[first]
    require(readlink(first)==middle and readlink(middle)==last and readlink(last)==tail,'alternative_target_changed')
    return str(Path(last).parent/tail)


def spelling_union(targets):
    result={}
    for target in targets:
        for row in target['libraries'].values():
            path,canonical=row['reported_path'],row['canonical_path']
            require(path not in result or result[path]==canonical,'reported_spelling_conflict')
            result[path]=canonical
    return result


def checked_report(raw):
    require(len(raw)<=REPORT_CAP,'report_cap')
    return raw


def root_context():
    check()
    for name,literal in ROOT_LINKS.items():
        require(stat.S_ISLNK(os.lstat(name).st_mode) and os.readlink(name)==literal,'directory_alias_changed')
    require(stat.S_ISDIR(os.lstat(SYSTEM).st_mode),'system_root_not_directory')
    for first,(_,_,_,size) in ALTERNATIVE_CHAINS.items():
        st=os.lstat(checked_alternative(first,os.readlink))
        require(stat.S_ISREG(st.st_mode) and st.st_size==size,'alternative_final_type_size')
    try:st=os.lstat('/etc/ld.so.preload')
    except FileNotFoundError:return
    raise RuntimeError('preload_present_refused_without_reading')


def system_normalize(path):
    path=os.path.normpath(path)
    if path.startswith('/lib/x86_64-linux-gnu/'):
        record_alias(DIRECTORY_ALIASES,'/lib','usr/lib','/usr/lib')
        path='/usr'+path
    require(path.startswith(SYSTEM+'/') and len(Path(path).parts)<=10,'alias_escaped_allowed_system_root')
    return path


def checked_alias_target(literal):
    require('..' not in Path(literal).parts,'alias_parent_component')
    return literal


def resolve_system(reported):
    allowed_spelling(reported);root_context()
    if reported in LOADER_ALIASES:
        require(stat.S_ISLNK(os.lstat(reported).st_mode) and
                os.readlink(reported)=='../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2','loader_alias_changed')
        record_alias(FILE_ALIASES,reported,'../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',LOADER)
        if reported.startswith('/lib64/'):
            record_alias(DIRECTORY_ALIASES,'/lib64','usr/lib64','/usr/lib64')
        SPELLINGS[reported]=LOADER;return LOADER
    path=system_normalize(reported);seen=set();pending=[];hops=0
    while True:
        check();require(path not in seen,'alias_loop');seen.add(path)
        parts=Path(path).relative_to(SYSTEM).parts;current=Path(SYSTEM);restart=False
        for index,part in enumerate(parts):
            check();current=current/part;st=current.lstat();tail=parts[index+1:]
            if str(current) in ALTERNATIVE_CHAINS:
                require(not tail,'alternative_not_final_component')
                first=str(current);middle,last,literal,size=ALTERNATIVE_CHAINS[first]
                path=checked_alternative(first,os.readlink)
                pending.extend([(first,middle,False),(middle,last,False),(last,literal,False)])
                restart=True;break
            if stat.S_ISLNK(st.st_mode):
                hops+=1;require(hops<=8,'alias_hop_cap');literal=checked_alias_target(os.readlink(current))
                require(len(literal)<=512,'alias_target_length')
                target=Path(literal) if Path(literal).is_absolute() else current.parent/literal
                expanded=system_normalize(str(target.joinpath(*tail)))
                pending.append((str(current),literal,bool(tail)))
                path=expanded;restart=True;break
            require(stat.S_ISDIR(st.st_mode) if tail else stat.S_ISREG(st.st_mode),'candidate_component_type')
        if not restart:break
    for alias,literal,is_directory in pending:
        if is_directory:
            # Directory targets are context only, never controller runtime_aliases.
            target=os.path.normpath(str(Path(literal) if Path(literal).is_absolute() else Path(alias).parent/literal))
            target=system_normalize(target+'/__directory_marker__').removesuffix('/__directory_marker__')
            record_alias(DIRECTORY_ALIASES,alias,literal,target)
        else:record_alias(FILE_ALIASES,alias,literal,path)
    if reported!=path:
        SPELLINGS[reported]=path
    require(len(SPELLINGS)<=MAX_FILES*3,'spelling_count')
    return path


def verify_pin(pin):
    check();actual=c.regular(pin['path'],maximum=pin['bytes'],checker=check)
    require(actual=={'bytes':pin['bytes'],'sha256':pin['sha256']},'fixed_pin_changed')
    return c.identity(Path(pin['path']).lstat())


def hash_candidates(paths):
    require(1<=len(paths)<=MAX_FILES,'candidate_count')
    files={};total=0
    for path in sorted(paths):
        check();require(path.startswith(SYSTEM+'/'),'candidate_root')
        st=Path(path).lstat();require(stat.S_ISREG(st.st_mode) and 0<st.st_size<=MAX_FILE_BYTES,'candidate_type_size')
        total+=st.st_size;require(total<=MAX_TOTAL_BYTES,'candidate_aggregate_bytes')
        files[path]=c.regular(path,maximum=st.st_size,checker=check)
        require(files[path]['bytes']==st.st_size,'candidate_size_changed')
    return files,total


def verify_source_pin(path,expected,size):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        st=os.fstat(fd);require(stat.S_ISREG(st.st_mode) and st.st_size==size,'bootstrap_file_size')
        raw=os.read(fd,size+1)
        require(len(raw)==size and hashlib.sha256(raw).hexdigest()==expected,'bootstrap_file_pin')
        key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        require(key(st)==key(os.fstat(fd))==key(Path(path).lstat()),'bootstrap_file_changed')
        return raw
    finally:os.close(fd)


def boot():
    global c,g,INSPECTION_END,BUILD
    require(sys.argv[1:]==[] and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,'closed_isolated_entry')
    common=PREP/'installation/common.py'
    verify_source_pin(common,'98225798e5398fa4c6d0764f6bc4177eb3f85446ac65d38c38ecb039a974d316',14162)
    spec=importlib.util.spec_from_file_location('loader_common',common);c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
    c.check();INSPECTION_END=min(START+180.,float(os.environ['GUARD_WORK_END']))
    require(float(os.environ['GUARD_ABSOLUTE_END'])-float(os.environ['GUARD_WORK_END'])==60.,'outer_cleanup_contract')
    check();c.privacy()
    require(os.sched_getaffinity(0)=={3} and os.getpriority(os.PRIO_PROCESS,0)==19,'inherited_CPU_nice')
    require(resource.getrlimit(resource.RLIMIT_AS)==(1073741824,1073741824),'inherited_1GiB_AS')
    require(all(name not in os.environ for name in ('LD_PRELOAD','LD_LIBRARY_PATH','LD_AUDIT','GLIBC_TUNABLES')),'unapproved_loader_environment')
    manifest=json.loads(bounded(HERE/'MANIFEST.json',4096))
    require(manifest['programs']==PROGRAMS and manifest['loader']['path']==LOADER,'fixed_manifest_scope')
    qpin=manifest['geometry_qualification'];qraw=bounded(PREP/qpin['path'],qpin['bytes'])
    require(len(qraw)==qpin['bytes'] and hashlib.sha256(qraw).hexdigest()==qpin['sha256'],'geometry_receipt_pin')
    q=json.loads(qraw)
    require(q['status']=='PASS' and q['cases']==18 and q['cleanup']=='CONFIRMED' and q['source_final_verified'] is True,'geometry_qualification')
    require(len(q['pins'])==9 and sum(p['bytes'] for p in q['pins'].values())==21124,'qualification_bounds')
    for rel,pin in q['pins'].items():
        require(0<pin['bytes']<=8192,'qualification_file_cap')
        require(c.regular(c.safe_path(PREP,rel),maximum=pin['bytes'],checker=check)==pin,'qualification_dependency_pin')
    geo=manifest['geometry'];geometry=PREP/geo['path']
    require(hashlib.sha256(bounded(geometry,geo['bytes'])).hexdigest()==geo['sha256'],'geometry_source_pin')
    spec=importlib.util.spec_from_file_location('loader_geometry',geometry);g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
    run=Path(os.environ['GUARD_RUN_DIRECTORY']);BUILD=c.safe_path(PREP,'build/runtime-loader-'+run.name);BUILD.mkdir(mode=0o700)
    resource.setrlimit(resource.RLIMIT_FSIZE,(LOG_CAP,REPORT_CAP))
    root_context();output_membership()
    return manifest


def save(report):
    require(time.monotonic()<float(os.environ['GUARD_WORK_END']),'receipt_work_deadline')
    output_membership();raw=json.dumps(report,sort_keys=True,separators=(',',':')).encode()+b'\n'
    checked_report(raw)
    resource.setrlimit(resource.RLIMIT_FSIZE,(REPORT_CAP,REPORT_CAP))
    with (BUILD/'loader-context.json').open('xb') as out:
        for offset in range(0,len(raw),65536):
            require(time.monotonic()<float(os.environ['GUARD_WORK_END']),'receipt_work_deadline');out.write(raw[offset:offset+65536])
        out.flush();os.fsync(out.fileno())
    output_membership()


def main():
    manifest=boot();receipts=[];targets=[];files={};total=0;failure=None;static={};cache_identity=None
    try:
        sealpin=manifest['static_seal'];raw=bounded(PREP/sealpin['path'],sealpin['bytes'])
        require(hashlib.sha256(raw).hexdigest()==sealpin['sha256'],'static_seal_pin');seal=json.loads(raw)
        require(seal['accepted_rows']==seal['waited_zero_exit_originals']==63 and seal['cleanup']=='CONFIRMED' and seal['source_final_verified'] is True,'static_predecessor')
        spin=manifest['static_report'];raw=bounded(PREP/spin['path'],spin['bytes'])
        require(hashlib.sha256(raw).hexdigest()==spin['sha256'],'static_report_pin');static=json.loads(raw)
        cache_identity=verify_pin(manifest['cache']);fixed={p['path']:p for p in [manifest['loader'],*manifest['targets']]}
        require(set(fixed)=={LOADER,*PROGRAMS.values()},'four_fixed_binary_pins')
        identities={name:verify_pin(pin) for name,pin in fixed.items()}
        all_paths=set()
        for step,target in PROGRAMS.items():
            check();root_context();require(c.identity(Path(LOADER).lstat())==identities[LOADER] and
                c.identity(Path(target).lstat())==identities[target],'fixed_identity_changed')
            child(step,receipts)
            require(c.identity(Path(LOADER).lstat())==identities[LOADER] and
                c.identity(Path(target).lstat())==identities[target],'fixed_identity_changed')
            log=BUILD/(step+'.log');raw=bounded(log,LOG_CAP);rows,vdso=parse_listing(raw);bindings={}
            for row in rows:
                path=resolve_system(row['reported_path']);all_paths.add(path)
                require(len(all_paths)<=MAX_FILES,'candidate_count')
                bindings[row['soname']]={'reported_path':row['reported_path'],'canonical_path':path}
            targets.append({'target':target,'argv':loader_argv(step),'libraries':bindings,'virtual_vdso_observed':vdso,
                'merged_log':{'path':str(log),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}})
        require({p:q for p,q in spelling_union(targets).items() if p!=q}==SPELLINGS,'spelling_union_not_lossless')
        files,total=hash_candidates(all_paths)
        for path,pin in files.items():
            require(path not in static['runtime_files'] or static['runtime_files'][path]==pin,'base_candidate_pin_conflict')
        for target in targets:
            previous=next(r for r in static['rows'] if r['path']==target['target'])
            require(set(previous['elf']['needed']).issubset(target['libraries']),'needed_missing_from_listing')
        base_aliases={a['path']:a for a in static['runtime_aliases']}
        for alias in tuple(FILE_ALIASES.values()):
            require(alias['path'] not in base_aliases or base_aliases[alias['path']]==alias,'base_alias_pin_conflict')
            require(alias['resolved_target'] in files and os.readlink(alias['path'])==alias['literal_target']
                and (checked_alternative(ALTERNATIVE_LINK_ROOT[alias['path']],os.readlink) if alias['path'] in ALTERNATIVE_LINK_ROOT else resolve_system(alias['path']))==alias['resolved_target'],'alias_postcheck')
        root_context();require(verify_pin(manifest['cache'])==cache_identity,'cache_context_changed')
        for name,pin in fixed.items():require(verify_pin(pin)==identities[name],'fixed_binary_postcheck')
        check()
    except BaseException as exc:failure=c.failure_summary(exc)
    complete=failure is None and len(targets)==3 and len(receipts)==3 and all(r['original_waited'] and r['returncode']==0 for r in receipts)
    report={'schema':'rc3-dev3-loader-context-candidates-v1','status':'COMPLETE_OBSERVED_LOADER_CONTEXT_ONLY' if complete else 'INCOMPLETE',
        'inference_calls':0,'target_entrypoints_requested':False,'actual_run_authorized':False,'failure':failure,
        'candidate_files':files,'candidate_total_bytes':total,'runtime_aliases':list(FILE_ALIASES.values()) if complete else [],
        'directory_aliases':list(DIRECTORY_ALIASES.values()),'per_target':targets,
        'ownership_substeps':receipts,'context':{'loader':manifest['loader'],'cache':manifest['cache'],
            'cache_pre_post_identity_verified':complete,'preload_absent_before_after':complete,
            'argv_mode':'--list only; no --inhibit-cache, ldd or target entrypoint',
            'environment':'unchanged guard allowlist; no LD_PRELOAD/LD_LIBRARY_PATH/LD_AUDIT/GLIBC_TUNABLES; C locale',
            'limits':'Observed fresh-loader context only, not universal dependency resolution, constructor/ABI proof, passive actual mappings, SONAME-reuse behavior, lazy dlopen closure or later ffmpeg loaded origins.'},
        'unknowns':([x for x in static.get('unknowns',[]) if not complete or 'unresolved_search_path' in x]+([{'probe_failure':failure}] if failure else [])),
        'prior_static_unknowns':static.get('unknowns',[]),'static_search_paths_not_resolved_by_this_probe':[
            x for x in static.get('unknowns',[]) if 'unresolved_search_path' in x],
        'limits':{'calls':3,'local_seconds':180,'outer_seconds':[300,240,60],'owned_RSS_bytes':67108864,
            'inherited_AS_bytes':1073741824,'candidates':MAX_FILES,'candidate_file_bytes':MAX_FILE_BYTES,
            'candidate_total_bytes':MAX_TOTAL_BYTES,'aliases':MAX_ALIASES,'alias_hops':8,
            'merged_log_each_bytes':LOG_CAP,'report_bytes':REPORT_CAP,'output_allocated_ceiling':266240},
        'elapsed_seconds':time.monotonic()-START,'original_guard_terminal_receipt_required':True}
    save(report);require(complete,'loader_probe_incomplete')


# The exact accepted original child body is appended without modification.
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
    try:main()
    except BaseException as exc:
        if c is not None:sys.stderr.write(json.dumps({'status':'INCOMPLETE','failure':c.failure_summary(exc)})+'\n')
        else:sys.stderr.write('{"status":"INCOMPLETE","failure":"bootstrap"}\n')
        raise SystemExit(2)
