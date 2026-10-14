# Unexecuted portable template; generate every configuration placeholder before use.
import time
START=time.monotonic()
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
V8=BASE/'@GENERATED_GUARD_ROOT_REL@'
OUT=BASE/'@GENERATED_ACTUAL_RECORDS_ROOT_REL@'
OLD_OUT=BASE/'@GENERATED_BASE_ACTUAL_RECORDS_ROOT_REL@'
OLD_RUN=BASE/'@GENERATED_BASE_ACTUAL_RUN_REL@'
HOST=Path('@GENERATED_HOST_PYTHON@')
PYTHON=BASE/'@GENERATED_RUNTIME_ROOT_REL@/bin/python3.12'
MIB=2**20;GIB=2**30
PY_PIN={'bytes':int('@GENERATED_PYTHON_EXECUTABLE_BYTES@'),'sha256':'@GENERATED_PYTHON_EXECUTABLE_SHA256@'}
GUARD_PIN={'bytes':int('@GENERATED_PROCESS_GUARD_SOURCE_BYTES@'),'sha256':'@GENERATED_PROCESS_GUARD_SOURCE_SHA256@'}
BOOT_PIN={'bytes':int('@GENERATED_CENSUS_SOURCE_BYTES@'),'sha256':'@GENERATED_CENSUS_SOURCE_SHA256@'}
HARNESS_MANIFEST={'bytes': int('@GENERATED_HARNESS_MANIFEST_BYTES@'), 'sha256': '@GENERATED_HARNESS_MANIFEST_SHA256@'}
ADMISSION_SHA='@GENERATED_RESOURCE_ADMISSION_SHA256@'
CONTROLS={'release.json':16384,'config.json':2*MIB,'spec.json':256*1024,'claim.json':1024,
    'owner-result.json':32768,'OWNER_COMPLETION.json':16384,'OWNER_ACCEPTANCE.json':32768,'authority.json':4096}
PREREQS={n+'.json' for n in ('cell_store','cell_store-review','decoder','decoder-review','fixtures','split',
    'admission','hash_inventory','outer_guard','outer-review','derivation','focused-acceptance')}
RUN_LEAVES={'index.json':65536,'index.tmp':65536,'receipt.json':65536,'receipt.tmp':65536,
    'output.log':256*1024,'STOP':512,'inventory-result.json':16384}
CAPS={'acquisition':7*GIB,'runtime':512*MIB,'proof':512*MIB,'actual':544*MIB,'source':64*MIB,'evidence':64*MIB}
WORK_END=START+540;ABSOLUTE_END=START+600
def need(value,why):
    if not value:raise RuntimeError(why)
def check(end=None):need(time.monotonic()<(WORK_END if end is None else end),'owner_deadline')
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def path(value):
    need(type(value) is str and value.startswith('/') and '\\' not in value and '\0' not in value and
        all(p not in ('','.','..') for p in value.split('/')[1:]),'canonical_absolute_path')
    p=Path(value);need(str(p)==value and p.resolve()==p,'path_alias')
    for ancestor in (p,*p.parents):need(not ancestor.is_symlink(),'symlink_path')
    return p
def signature(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def read(name,cap,pin=None,end=None,*,hash_only=False):
    check(end);p=path(str(name));before=p.lstat()
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        opened=os.fstat(fd)
        need(signature(before)==signature(opened) and stat.S_ISREG(opened.st_mode) and opened.st_nlink==1 and
            opened.st_size<=cap,'bounded_ordinary_descriptor')
        h=hashlib.sha256();parts=[];size=0
        while size<opened.st_size:
            check(end);block=os.read(fd,min(65536,opened.st_size-size));need(block,'short_read')
            size+=len(block);h.update(block)
            if not hash_only:parts.append(block)
        need(not os.read(fd,1),'file_grew')
        need(signature(opened)==signature(os.fstat(fd))==signature(p.lstat()),'descriptor_changed')
        observed={'bytes':size,'sha256':h.hexdigest()}
        if pin is not None:need(observed==pin,'file_pin:'+str(p))
        return (None if hash_only else b''.join(parts)),observed
    finally:os.close(fd)
def decode(raw):
    def pairs(items):
        result={}
        for k,v in items:need(k not in result,'duplicate_JSON_key');result[k]=v
        return result
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda v:need(False,'nonfinite_JSON'))
    need(type(value) is dict,'JSON_object');return value
def refread(ref,cap=2*MIB,end=None):
    need(type(ref) is dict and set(ref)=={'path','bytes','sha256'} and type(ref['bytes']) is int and
        0<=ref['bytes']<=cap,'exact_metadata_reference')
    raw,_=read(ref['path'],cap,{k:ref[k] for k in ('bytes','sha256')},end)
    return decode(raw)
def write_new(name,value,cap,end=None):
    check(end);p=path(str(name));raw=canonical(value)+b'\n';need(len(raw)<=cap,'metadata_output_cap')
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        view=memoryview(raw)
        while view:
            check(end);n=os.write(fd,view[:65536]);need(n>0,'metadata_write');view=view[n:]
        os.fsync(fd)
    finally:os.close(fd)
    return {'path':str(p),'bytes':len(raw),'sha256':sha(raw)}
def load(name,p,pin):
    read(p,128*1024,pin)
    s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def tree(root,end,limit):
    total=count=0
    def visit(p):
        nonlocal total,count
        check(end);s=p.lstat();count+=1
        need(count<=limit and s.st_dev==BASE.stat().st_dev,'output_count_or_device')
        need(stat.S_ISDIR(s.st_mode) or (stat.S_ISREG(s.st_mode) and s.st_nlink==1),'ordinary_owned_output')
        total+=max(s.st_size,s.st_blocks*512)
        if stat.S_ISDIR(s.st_mode):
            for x in p.iterdir():visit(x)
    if root.exists():visit(root)
    return total,count
def output_audit(actual_run,end):
    total=controls=0;inventory=OUT/'inventory/runs'
    if OUT.exists():
        need({p.name for p in OUT.iterdir()}<={'actual','prerequisites'},'control_root_union')
        for p in OUT.iterdir():
            need(p.is_dir() and not p.is_symlink(),'control_directory')
            if p.name=='prerequisites':
                for f in p.iterdir():need(f.name in PREREQS and f.is_file() and f.stat().st_size<=65536,'prerequisite_leaf')
            else:
                for f in p.iterdir():
                    if p.name=='inventory' and f.name=='runs':continue
                    need(f.name in CONTROLS and f.is_file() and f.stat().st_size<=CONTROLS[f.name],'control_leaf')
        total,_=tree(OUT,end,256);charge,_=tree(inventory,end,32);controls=total-charge
        need(controls<=8*MIB and charge<=MIB,'control_or_inventory_run_cap')
    if inventory.exists():
        runs=list(inventory.iterdir());need(len(runs)<=1,'one_inventory_original')
        for run in runs:
            need(run.is_dir() and run.name.startswith('run-') and len(run.name)==12,'inventory_run_name')
            for p in run.iterdir():
                if p.name in ('home','cache','tmp'):need(p.is_dir() and not any(p.iterdir()),'inventory_empty_scratch')
                else:need(p.name in RUN_LEAVES and p.is_file() and p.stat().st_size<=RUN_LEAVES[p.name],'inventory_output_union')
    charge,_=tree(actual_run,end,8192);total+=charge
    for retained,entries in ((OLD_OUT,256),(OLD_RUN,8192)):
        amount,_=tree(retained,end,entries);total+=amount
    need(total<=544*MIB,'shared_actual_544MiB_output_cap');return total
def known_atomic(error,actual_run):
    if not error.filename:return False
    p=Path(error.filename)
    return (p.is_relative_to(actual_run) or p.is_relative_to(OUT/'inventory/runs')) and (
        p.name.endswith('.pending') or p.name in ('index.tmp','receipt.tmp'))
def resources(config,actual_run,end):
    for attempt in range(3):
        try:return resources_once(config,actual_run,end)
        except FileNotFoundError as error:need(known_atomic(error,actual_run) and attempt<2,'unapproved_census_disappearance')
    raise RuntimeError('unreachable_census')
def resources_once(c,actual_run,end):
    output_audit(actual_run,end)
    roots=c['roots'];dev=c['host']['filesystem_device'];usage={k:0 for k in CAPS};count=0
    work=Path(roots['work']);source=(Path(roots['source_recovery']),Path(roots['packet']),
        Path(roots['proof_source']),Path(c['assets_manifest']['path']).parent)
    actual_roots=(OUT,actual_run,OLD_OUT,OLD_RUN)
    for r in actual_roots:path(str(r))
    def category(p):
        if any(p==r or p.is_relative_to(r) for r in actual_roots):return 'actual'
        if any(p==r or p.is_relative_to(r) for r in source):return 'source'
        r=Path(roots['runtime_container'])
        if p==r or p.is_relative_to(r):return 'runtime'
        for name,sub in (('acquisition','assets'),('proof','proof'),('actual','actual')):
            if p==work/sub or p.is_relative_to(work/sub):return name
        return 'evidence'
    def visit(p,root):
        nonlocal count
        check(end);count+=1;need(count<=32768,'charged_entry_cap');s=p.lstat()
        need(s.st_dev==dev,'charged_device_changed')
        need(stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode),'charged_special_file')
        if stat.S_ISLNK(s.st_mode):
            literal=os.readlink(p);target=p.resolve(strict=True)
            need(not Path(literal).is_absolute() and len(literal)<=4096 and target.is_relative_to(root) and target.is_file(),
                'charged_alias_escape')
        usage[category(p)]+=max(s.st_size,s.st_blocks*512)
        if stat.S_ISDIR(s.st_mode):
            for x in p.iterdir():visit(x,root)
    candidates=[Path(roots[k]) for k in ('source_recovery','work','runtime_container','proof_source')]+[Path(c['assets_manifest']['path']).parent]
    for root in dict.fromkeys(candidates):
        if any(root!=other and root.is_relative_to(other) for other in candidates):continue
        if not root.exists():need(root in (Path(roots['runtime_container']),Path(roots['proof_source'])),'charged_root_missing');continue
        need(root.is_dir() and not root.is_symlink(),'charged_root_type');visit(root,root)
    for name,value in usage.items():need(value<=CAPS[name],'category_cap_'+name)
    need(sum(usage.values())+MIB<=12*GIB,'global_disk_cap');return usage
def source_binding(release):
    need(release['source_manifest']['path']==str(HERE/'SOURCE_MANIFEST.json'),'owner_manifest_path')
    m=refread(release['source_manifest'],8192)
    need(m['schema']=='fresh-seam-actual-source-v1' and set(m['files'])=={'owner.py','actual_guard.py','test_diagnostics.py'} and
        m['aggregate_cap_bytes']==106*1024,'three_source_files')
    pins={str(HERE/'SOURCE_MANIFEST.json'):release['source_manifest']['sha256']}
    for name,pin in m['files'].items():
        read(HERE/name,{'owner.py':52*1024,'actual_guard.py':30*1024,'test_diagnostics.py':16*1024}[name],pin);pins[str(HERE/name)]=pin['sha256']
    need(set(p.name for p in HERE.iterdir())=={'owner.py','actual_guard.py','test_diagnostics.py','SOURCE_MANIFEST.json'},'owner_namespace')
    need(release['harness_manifest']=={'path':str(PACKET/'SOURCE_MANIFEST.json'),**HARNESS_MANIFEST},'frozen_packet_manifest')
    harness=refread(release['harness_manifest'],8192);pins[str(PACKET/'SOURCE_MANIFEST.json')]=HARNESS_MANIFEST['sha256']
    for row in harness['files']:
        p=PACKET/row['path'];read(p,row['cap_bytes'],{k:row[k] for k in ('bytes','sha256')});pins[str(p)]=row['sha256']
    sys.path.insert(0,str(PACKET/'controller'))
    import binding
    return m,pins,binding
def admission(ref):
    need(ref['sha256']==ADMISSION_SHA,'accepted_two_admission_pin');v=refread(ref,16384)
    need(v['schema']=='owner-two-selected-admission-acceptance-v1' and
        v['status']=='ACCEPTED_MODEL_FREE_ACTUAL_RUNTIME_ONLY' and v['cleanup']=='CONFIRMED' and
        v['model_execution_authorized'] is False and v['original_tool']['positive_terminal'] is True and
        v['original_tool']['exit_code']==0 and v['original_tool']['session_id']==46431 and
        v['registry_unchanged'] is True and v['temporary_namespaces_empty'] is True,'accepted_admission_scope')
    need([x['name'] for x in v['cases']]==['test_off_absent_and_enabled_unqualified_do_not_create_sql_or_thread',
        'test_seam_synthetic_real_runtime_a_profile_restart_dispatch_and_off'] and all(x['status']=='PASS' and
        x['tests_run']==1 and x['temporary_cleanup']=='EMPTY' and x['threads_cleanup']=='MAIN_ONLY' for x in v['cases']),
        'two_selected_admission_cases')
    for key in ('receipt','source','pins'):
        r=v[key];read(r['path'],65536,{k:r[k] for k in ('bytes','sha256')})
def inventory_origins(c,refs,b):
    need(type(refs) is dict and set(refs)=={'runtime','product','host'},'three_inventory_origins')
    rows=b.rows_map(c['inventory']['hash_inventory']);expected={}
    def add(name,record):
        b.path_text(name);need(name not in expected or expected[name]==record,'inventory_origin_conflict');expected[name]=record
    runtime=refread(refs['runtime']);need(runtime['schema']=='portable-runtime-inventory-v1' and
        len(runtime['files'])==1719 and runtime['runtime_root']==c['paths']['runtime_root'] and
        runtime['product_commit']==c['product_commit'],'accepted_runtime_inventory')
    for rel,r in runtime['files'].items():b.path_text(rel,absolute=False);add(str(Path(c['paths']['runtime_root'])/rel),r)
    product=refread(refs['product'])['source'];need(product['commit']==c['product_commit'] and len(product['files'])==351,'product_index')
    for rel,digest in product['files'].items():
        b.path_text(rel,absolute=False);name=str(Path(c['paths']['source_root'])/rel)
        need(name in rows and rows[name]['sha256']==digest,'product_expected_pin');add(name,rows[name])
    need(sum(rows[str(Path(c['paths']['source_root'])/n)]['bytes'] for n in product['files'])==product['bytes'],'product_total_bytes')
    host=refread(refs['host'])
    for group in ('allowed_files','allowed_mappings'):
        for name,r in host[group].items():
            if not Path(name).is_relative_to(BASE):add(name,r)
    reviewed=refread(c['runtime_import'],16384)['review']['mapping_review']['first_observed_files']
    need(len(reviewed)==5,'accepted_observed_mapping_count')
    for row in reviewed:add(row['path'],{k:row[k] for k in ('bytes','sha256')})
    for name,ref in c['model_acceptance'].items():
        accepted=refread(ref,16384);need(accepted['artifact']==name and accepted['status']=='ACCEPTED_MATERIALIZATION_ONLY' and
            accepted['cleanup']=='CONFIRMED' and accepted['inference_authorized'] is False,'accepted_model_origin')
        result=refread({k:accepted['result'][k] for k in ('path','bytes','sha256')},65536)
        for r in result['outputs']:
            b.path_text(r['path'],absolute=False);add(str(BASE/'@GENERATED_ASSETS_ROOT_REL@'/r['path']),{k:r[k] for k in ('bytes','sha256')})
    for name,r in c['inventory']['harness_files'].items():add(name,r)
    for role,key in (('source','input_wav'),('upload_a','upload_a'),('upload_b','upload_b')):
        name=c['paths'][key];p=c['input_pins'][role];add(name,{'bytes':672044 if role=='source' else 336044,'sha256':p['wav_sha256']})
    for key,digest in [('moss_model',b.MOSS_SHA),('speaker_model','1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b'),
        ('native_model','08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1')]:
        name=c['paths'][key];need(rows[name]['sha256']==digest,'loose_model_expected_pin');add(name,rows[name])
    need(rows==expected,'closed_inventory_expected_union')
    return rows
def verify_metadata(release,c,b,actual):
    need(actual,'only_one_actual_retry_mode')
    b.validate_config(c,ready=True)
    from launch import verify_derivation
    baseline,derivation=verify_derivation(c,check)
    need(c['inventory'] is not None and c['input_pins'] is not None and all(c['paths'].values()),'current_inventory_and_paths_required')
    need(c['paths']['harness_root']==str(PACKET/'controller'),'frozen_harness_path')
    from launch import verify_gate_receipt,verify_physical_prerequisite
    for name in ('cell_store','decoder'):verify_physical_prerequisite(c,name,check)
    for name in ('fixtures','split')+ (('hash_inventory',) if actual else ()):verify_gate_receipt(c,name,check)
    admission(release['admission']);reserve=release['resource_admission']
    need(set(reserve)=={'retained_unknown_rss_bytes','aggregate_target_bytes','unknown_records'} and
        reserve['retained_unknown_rss_bytes']==512*MIB and reserve['aggregate_target_bytes']==6*GIB and
        4*GIB+reserve['retained_unknown_rss_bytes']<=reserve['aggregate_target_bytes'],'retained_unknown_reserve')
    unknown=reserve['unknown_records'];need(len(unknown)==2,'two_unknown_originals')
    for ref,pid,digest in zip(unknown,(int('@GENERATED_UNKNOWN_FIRST_PROCESS_SESSION_ID@'),int('@GENERATED_UNKNOWN_SECOND_PROCESS_SESSION_ID@')),('@GENERATED_UNKNOWN_FIRST_RECORD_SHA256@',
            '@GENERATED_UNKNOWN_SECOND_RECORD_SHA256@')):
        need(ref['sha256']==digest,'unknown_receipt_pin');v=refread(ref,16384)
        need(v['original_tool_session_id']==pid and v['status']=='INCOMPLETE_ORIGINAL_TOOL_HANDLE_LOST' and
            v['read_only_observation']['cleanup']=='UNKNOWN' and v['read_only_observation']['positive_original_wait'] is False,
            'unknown_state_preserved')
        for f in v['retained_files']:read(f['path'],128*1024,{k:f[k] for k in ('bytes','sha256')})
    rows=inventory_origins(c,release['inventory_origins'],b)
    need(len(rows)<=8192 and sum(r['bytes'] for r in rows.values())<=12*GIB,'finite_hash_inventory_budget')
    need(release['inventory_budget']=={'rows':len(rows),'bytes':sum(r['bytes'] for r in rows.values()),'max_rows':8192,
        'max_bytes':12*GIB,'block_bytes':65536,'passes':1},'declared_exact_inventory_budget')
    imported=refread(c['runtime_import'],16384)
    need(imported['status']=='ACCEPTED_IMPORT_ONLY_WITH_SEPARATE_FAILED_OUTER_GUARD' and
        imported['raw_guard']['status']=='FAIL_CLOSED' and imported['execution_authorized'] is False,'preserved_import_guard_failure')
    if actual:
        review=refread(release['outer_review'],65536);contract=refread(c['prerequisites']['outer_guard'],65536)
        need(review=={'schema':'fresh-seam-actual-owner-review-v1','status':'ACCEPTED_DORMANT_SOURCE',
            'source_manifest':release['source_manifest'],'harness_manifest':release['harness_manifest'],
            'subject_sha256':b.subject_digest(c),'limits':b.LIMITS,'phases':c['phases']},'independent_outer_source_review')
        need(contract=={'schema':'fresh-seam-outer-contract-v1','status':'REVIEWED_DORMANT','subject_sha256':b.subject_digest(c),
            'owner_source':{'path':str(HERE/'owner.py'),**release['_manifest']['files']['owner.py']},'limits':b.LIMITS,
            'phases':c['phases'],'original_handle_retention':True,'pidfd_identity_checks':True,'cleanup_required':'CONFIRMED'},
            'exact_outer_contract')
        need(release['inventory_acceptance']==derivation['base_inventory_acceptance'],'unchanged_inventory_receipt_reference')
        acceptance=refread(release['inventory_acceptance'],32768)
        need(acceptance['schema']=='fresh-seam-inventory-owner-acceptance-v1' and acceptance['status']=='ACCEPTED_HASH_ONLY' and
            acceptance['cleanup']=='CONFIRMED' and acceptance['original_tool']['positive_terminal'] is True and
            acceptance['original_tool']['exit_code']==0 and acceptance['source_manifest']['sha256']=='@GENERATED_BASE_OWNER_MANIFEST_SHA256@' and
            acceptance['inventory_sha256']==sha(canonical(sorted(baseline['inventory']['hash_inventory'],key=lambda r:r['path']))) and
            acceptance['subject_sha256']==b.subject_digest(c) and acceptance['actual_authorized'] is False,'inventory_owner_acceptance')
        g=refread(acceptance['guard_receipt'],65536)
        need(g['status']=='PASS' and g['cleanup']=='CONFIRMED' and g['original_handle_waited'] is True and
            g['exit_code']==0 and g['final_source_pin_verified'] is True and not g['errors'],'inventory_original_guard_success')
    else:need(release['authority'] is None and release['inventory_acceptance'] is None,'inventory_never_actual_authority')
    return rows
def load_release(mode,digest):
    need(mode=='actual' and len(digest)==64 and all(x in '0123456789abcdef' for x in digest),'closed_release_mode')
    raw,rpin=read(OUT/mode/'release.json',16384);need(rpin['sha256']==digest,'independent_release_pin');r=decode(raw)
    need(set(r)=={'schema','mode','authorized','source_manifest','harness_manifest','config','authority','inventory_origins',
        'inventory_budget','resource_admission','admission','outer_review','inventory_acceptance'} and r['schema']=='fresh-seam-owner-release-v1' and
        r['mode']==mode and r['authorized'] is True,'explicit_phase_release')
    manifest,pins,b=source_binding(r);r['_manifest']=manifest
    need(r['config']['path']==str(OUT/mode/'config.json'),'fixed_config_path')
    raw,_=read(r['config']['path'],2*MIB,{k:r['config'][k] for k in ('bytes','sha256')});c=decode(raw)
    verify_metadata(r,c,b,mode=='actual')
    if mode=='actual':
        need(r['authority']['path']==str(OUT/'actual/authority.json'),'separate_authority_path')
        authority=refread(r['authority'],4096);b.validate_authority(authority,raw,c)
    for ref in [r['config'],c['diagnostic_derivation'],r['admission'],*r['resource_admission']['unknown_records'],*r['inventory_origins'].values(),*[v for v in c['prerequisites'].values() if v]]:
        pins[ref['path']]=ref['sha256']
    for key in ('authority','outer_review','inventory_acceptance'):
        if r[key]:pins[r[key]['path']]=r[key]['sha256']
    pins[str(OUT/mode/'release.json')]=digest
    return r,c,raw,pins,b
def current_identity(pid):
    raw=Path(f'/proc/{pid}/stat').read_text();fields=raw[raw.rindex(')')+2:].split()
    return {'pid':pid,'start_ticks':int(fields[19])},int(fields[1])
class Adapter:
    def __init__(self,owner_config,release,c,config_raw,pins,mode,guard_pin,launch,source_files,bearer):
        self.owner_config=owner_config;self.release=release;self.c=c;self.raw=config_raw;self.pins=pins;self.mode=mode
        self.guard_pin=guard_pin;self.launch=launch;self.source_files=source_files;self.bearer=bearer;self.projection=None
    def load_config(self,name,digest):
        need(name==self.release['config']['path'] and digest==sha(self.raw),'guard_exact_config_reference')
        read(name,2*MIB,{'bytes':len(self.raw),'sha256':digest},ABSOLUTE_END-.2)
        if self.projection:refread(self.projection,16384,ABSOLUTE_END-.2)
        return self.owner_config
    def make_spec(self,c,name,digest,phase,launch):
        actual=self.mode=='actual';stage='fresh-seam-actual' if actual else 'runtime-host-inventory'
        need(c==self.owner_config and phase==stage and launch==self.launch,'guard_closed_projection')
        argv=([str(PYTHON),'-I','-B','-S',str(PACKET/'controller/bootstrap.py'),'--mode','controller','--spec',name] if actual else
            [str(HOST),'-I','-B','-S',str(HERE/'owner.py'),'--inventory-child',self.release['_sha256']])
        result={'schema':3,'stage':stage,'absolute_seconds':1800 if actual else 600,'work_seconds':1740 if actual else 540,
            'address_space_cap_bytes':8*GIB if actual else GIB,'rss_cap_bytes':4*GIB if actual else 256*MIB,
            'scratch_cap_bytes':12*GIB,'guard_sha256':self.guard_pin['sha256'],'source_root':str(HERE),
            'source_files':self.source_files,'cwd':str(OUT/self.mode),'external_files':self.pins,
            'runtime_files':{str(HOST):PY_PIN['sha256'],str(PYTHON):PY_PIN['sha256']},
            'config_path':name,'config_sha256':digest,'phase':stage,'launch':launch,'argv':argv}
        if actual:result['actual_run']=self.c['paths']['output_dir']
        return result
    def resource_usage(self,c,end):
        need(c==self.owner_config,'exact_resource_config');return resources(c,Path(self.c['paths']['output_dir']),end)
    def bind_actual_environment(self,env,spec,run):
        need(self.mode=='actual' and spec['actual_run']==str(run),'actual_only_environment')
        r=self.release
        env.update({'FRESH_SEAM_CONFIG':r['config']['path'],'FRESH_SEAM_CONFIG_SHA256':r['config']['sha256'],
            'FRESH_SEAM_AUTHORITY':r['authority']['path'],'FRESH_SEAM_AUTHORITY_SHA256':r['authority']['sha256'],
            'FRESH_SEAM_OUTER_INDEX':str(run/'outer-index.json'),'FRESH_SEAM_OWNER_SHA256':r['_manifest']['files']['owner.py']['sha256'],
            'FRESH_SEAM_BEARER':self.bearer})
        for name in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS'):env[name]='2'
def main():
    global WORK_END,ABSOLUTE_END
    need(len(sys.argv)==3,'exact_owner_arguments')
    need(Path(sys.executable)==HOST and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
        'pinned_isolated_owner_python')
    mode,release_sha=sys.argv[1:];need(mode=='actual','closed_retry_actual_mode')
    actual=True;WORK_END=START+(1740 if actual else 540);ABSOLUTE_END=START+(1800 if actual else 600)
    need(not any(k.startswith('FRESH_SEAM_') for k in os.environ),'no_inherited_authority')
    cpus={0,1} if actual else {3};need(cpus<=os.sched_getaffinity(0),'required_cpus_unavailable')
    os.sched_setaffinity(0,cpus);os.setpriority(os.PRIO_PROCESS,0,19);os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_AS,(8*GIB,8*GIB) if actual else (GIB,GIB));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    read(HOST,32*MIB,PY_PIN);read(PYTHON,32*MIB,PY_PIN)
    r,c,raw,pins,b=load_release(mode,release_sha);r['_sha256']=release_sha
    run=path(c['paths']['output_dir']);need(not run.exists(),'actual_run_must_be_fresh')
    phase=OUT/mode;need(phase.is_dir() and not any((phase/n).exists() for n in ('claim.json','spec.json','owner-result.json')),
        'one_released_phase_attempt')
    if not actual:need(not (OUT/'inventory/runs').exists(),'fresh_inventory_run_namespace')
    guard_path=HERE/'actual_guard.py' if actual else V8/'acquisition_guard.py'
    guard_pin=r['_manifest']['files']['actual_guard.py'] if actual else GUARD_PIN
    guard=load('fresh_seam_actual_guard' if actual else 'fresh_seam_inventory_guard',guard_path,guard_pin)
    pins[str(guard_path)]=guard_pin['sha256'];pins[str(V8/'bootstrap.py')]=BOOT_PIN['sha256']
    read(V8/'bootstrap.py',128*1024,BOOT_PIN)
    guard.START=START;guard.ABSOLUTE_END=ABSOLUTE_END;guard.CHECKS=PACKET if actual else phase;guard.WORKSPACE=BASE
    guard.BOOT_ID=Path('/proc/sys/kernel/random/boot_id').read_text().strip();guard.PID_NS=os.readlink('/proc/self/ns/pid')
    guard.TICKS=os.sysconf('SC_CLK_TCK');guard.PAGE=os.sysconf('SC_PAGE_SIZE')
    guard.B_TIME=next(int(x.split()[1]) for x in Path('/proc/stat').read_text().splitlines() if x.startswith('btime '))
    own=guard.proc(os.getpid());launch={'start_monotonic':START,'work_end_monotonic':WORK_END,
        'absolute_end_monotonic':ABSOLUTE_END,'identity':{k:own[k] for k in ('pid','start_ticks','boot_id','pid_namespace')}}
    oc={'roots':{'workspace':str(BASE),'source_recovery':str(BASE/'@GENERATED_SOURCE_AREA_REL@'),'packet':str(HERE),
            'product':str(HERE),'work':str(BASE/'@GENERATED_WORK_ROOT_REL@'),'runtime_container':str(BASE/'@GENERATED_RUNTIME_CONTAINER_REL@'),
            'proof_source':str(BASE/'@GENERATED_STORAGE_PROOF_ROOT_REL@')},'host':{'filesystem_device':BASE.stat().st_dev},
        'assets_manifest':{'path':str(BASE/'@GENERATED_ASSETS_MANIFEST_REL@')},'limits':{'category_bytes':CAPS}}
    usage=resources(oc,run,WORK_END);need(sum(usage.values())+max(0,544*MIB-usage['actual'])+MIB<=12*GIB,'remaining_actual_global_reserve')
    need(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'),'pidfds_required')
    need(ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)==0,'subreaper_required')
    need(not guard.children(os.getpid()),'preexisting_owned_child')
    claim=write_new(phase/'claim.json',{'schema':'fresh-seam-owner-claim-v1','mode':mode,'release_sha256':release_sha,'attempt':1},1024)
    pins[claim['path']]=claim['sha256']
    source_files={n:p['sha256'] for n,p in r['_manifest']['files'].items()}
    source_files['SOURCE_MANIFEST.json']=r['source_manifest']['sha256']
    adapter=Adapter(oc,r,c,raw,pins,mode,guard_pin,launch,source_files,os.urandom(24).hex() if actual else None)
    guard.CONFIG=oc;guard.BOOTSTRAP=adapter
    spec=adapter.make_spec(oc,r['config']['path'],sha(raw),'fresh-seam-actual' if actual else 'runtime-host-inventory',launch)
    sref=write_new(phase/'spec.json',spec,256*1024)
    if not actual:(phase/'runs').mkdir(mode=0o700)
    else:
        if not (PACKET/'runs').exists():(PACKET/'runs').mkdir(mode=0o700)
        need(not any((PACKET/'runs').iterdir()),'one_fresh_retry_run_only')
    original_write=guard.write_json
    def publish(name,value):
        original_write(name,value)
        if actual and name.name=='index.json' and value.get('status')=='GATED':
            need(value['argv']==spec['argv'] and value['spec_sha256']==sref['sha256'],'original_exact_gated_spec')
            index={'schema':'fresh-seam-original-invocation-v1','status':'GATED','config_sha256':sha(raw),
                'authority_sha256':r['authority']['sha256'],'owner_source_sha256':r['_manifest']['files']['owner.py']['sha256'],
                'run_directory':str(run),'original':{k:value['original'][k] for k in ('pid','start_ticks')},
                'supervisor':{k:value['supervisor'][k] for k in ('pid','start_ticks')},'argv':spec['argv'],
                'origin':START,'work_end':WORK_END,'absolute_end':ABSOLUTE_END}
            projected=write_new(run/'outer-index.json',index,16384)
            adapter.projection=projected
        output_audit(run,ABSOLUTE_END-.2)
    guard.write_json=publish
    runner=guard.Guard(spec,sref['sha256'])
    for signum in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(signum,lambda sig,frame:setattr(runner,'signal_received',sig))
    code=runner.run_command();output_audit(run,ABSOLUTE_END-.2)
    _,receipt_pin=read(runner.run/'receipt.json',65536,end=ABSOLUTE_END-.2)
    result={'schema':'fresh-seam-owner-result-v1','mode':mode,'status':'INCOMPLETE','guard_returncode':code,
        'guard_receipt':{'path':str(runner.run/'receipt.json'),**receipt_pin},'source_manifest':r['source_manifest'],
        'config_sha256':sha(raw),'original_tool_terminal_required':True,'partial_outputs_retained':True}
    if code==0:
        need(runner.receipt['status']=='PASS' and runner.receipt['cleanup']=='CONFIRMED' and
            runner.receipt['original_handle_waited'] is True and runner.receipt['exit_code']==0 and
            runner.receipt['final_source_pin_verified'] is True,'positive_original_guard_result')
        target=run/'capture-result.json' if actual else runner.run/'inventory-result.json'
        child,cpin=read(target,2*MIB if actual else 16384,end=ABSOLUTE_END-.2);value=decode(child)
        need(value.get('status')==('COMPLETE' if actual else 'HASHED_PENDING_ORIGINAL_COMPLETION'),'complete_child_outcome')
        result.update(status='COMPLETE_PENDING_ORIGINAL_TOOL_COMPLETION',child_result={'path':str(target),**cpin})
    write_new(phase/'owner-result.json',result,32768,ABSOLUTE_END-.2)
    output_audit(run,ABSOLUTE_END-.1);print(json.dumps(result,sort_keys=True),flush=True);return code
if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status':'OWNER_REFUSED_OR_INCOMPLETE','reason':str(error)[:500],'partial_outputs_retained':True}),flush=True)
        raise SystemExit(2)
