"""Source-reviewed, one-shot guarded offline phases; no preparation execution."""
import time
LAUNCH_START = time.monotonic()
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

HERE = Path(__file__).resolve().parent
MIB = 2 ** 20
B = None
MODULES = {}
INPUTS = {
 'runtime-fixtures': (), 'runtime-host-inventory': ('candidate','wheel_plans','policy'),
 'runtime-wheel-audit': ('host',),
 'assets-model-audit': ('archive_specs',), 'assets-corpus-select': ('allocation',),
 'runtime-build-install': ('host','wheel_plans','allocation'),
 'runtime-import': ('host','runtime_inventory','probe_configuration'),
 'assets-model-extract': ('archive_plans','allocation'),
 'assets-corpus-extract': ('archive_plan','allocation'),
 'assets-input-derive': ('clip_plan','allocation')}
EXTRA_OUTPUTS = {'runtime-host-inventory': ('host',),
 'runtime-wheel-audit': ('wheel_plans',), 'assets-model-audit': ('archive_plans',),
 'runtime-build-install': ('runtime_inventory',)}

class Refused(RuntimeError): pass

def need(value, reason):
    if not value: raise Refused(reason)

def raw_pin(path, maximum, expected=None):
    p=Path(path);before=p.lstat()
    need(p.is_absolute() and p.resolve()==p and stat.S_ISREG(before.st_mode) and
         before.st_nlink==1 and before.st_size<=maximum,'regular_bound_input')
    sig=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        need(sig(before)==sig(os.fstat(fd)),'input_open_race')
        data=bytearray()
        while True:
            block=os.read(fd,min(65536,maximum-len(data)+1))
            if not block: break
            data.extend(block);need(len(data)<=maximum,'input_cap')
        need(sig(before)==sig(os.fstat(fd))==sig(p.lstat()),'input_changed')
    finally: os.close(fd)
    result={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    if expected is not None: need(result==expected,'input_pin')
    return bytes(data),result

def strict(raw):
    def pairs(rows):
        result={}
        for key,value in rows:
            need(key not in result,'duplicate_json_key');result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(Refused('nonfinite_json')))

def bootstrap(path, sha):
    global B
    raw,pin=raw_pin(path,65536);need(pin['sha256']==sha,'private_config_or_template_pin')
    config=strict(raw)
    packet_raw,packet_pin=raw_pin(HERE/'PACKET_MANIFEST.json',16384)
    need(packet_pin['sha256']==config['packet_sha256'],'source_packet_pin')
    packet=strict(packet_raw)
    need(config['roots']['packet']==str(HERE),'source_packet_location')
    raw_pin(HERE/'guard_driver.py',45056,packet['files']['guard_driver.py'])
    raw_pin(HERE/'bootstrap.py',69632,packet['files']['bootstrap.py'])
    if B is None:
        spec=importlib.util.spec_from_file_location('fresh_phase_bootstrap',HERE/'bootstrap.py')
        B=importlib.util.module_from_spec(spec);spec.loader.exec_module(B)
    B.verify_packet()
    return B

def module_at(relative):
    packet=B.verify_packet();need(relative in packet['files'],'closed_module_member')
    raw_pin(HERE/relative,B.FILE_CEILINGS[relative],packet['files'][relative])
    if relative not in MODULES:
        spec=importlib.util.spec_from_file_location('fresh_'+relative.replace('/','_').replace('.','_'),HERE/relative)
        value=importlib.util.module_from_spec(spec);sys.modules[spec.name]=value
        spec.loader.exec_module(value);MODULES[relative]=value
    return MODULES[relative]

def phase_directory(c, phase):
    return Path(c['roots']['work'])/'evidence/runtime-fresh-v6'/phase

def validate_phase_args(phase,args,c):
    need(phase in INPUTS and type(args) is dict and set(args)=={'inputs','outputs','options'},'phase_args_shape')
    wanted=({},{'host_verification':'CANDIDATE_BYTES_AND_OBSERVED_MAPPINGS'}) if phase in ('runtime-build-install','runtime-import') else ({},)
    need(type(args['options']) is dict and args['options'] in wanted,'closed_phase_options')
    need(type(args['inputs']) is dict and set(args['inputs'])==set(INPUTS[phase]),'phase_input_roles')
    for ref in args['inputs'].values():
        need(type(ref) is dict and set(ref)=={'path','bytes','sha256'},'phase_input_reference')
        need(type(ref['bytes']) is int and 0<ref['bytes']<=4*MIB,'phase_input_cap')
        need(type(ref['sha256']) is str and len(ref['sha256'])==64 and set(ref['sha256'])<=set('0123456789abcdef'),'phase_input_digest')
        p=Path(ref['path']);need(p.is_absolute() and p.resolve(strict=False)==p,'phase_input_path')
    outputs=args['outputs'];names={'result',*EXTRA_OUTPUTS.get(phase,())}
    need(type(outputs) is dict and set(outputs)==names,'phase_output_roles')
    for name,path in outputs.items():
        need(path==str(phase_directory(c,phase)/(name+'.json')),'phase_output_path')
    return args

def expected_environment(run, work_end, absolute_end):
    e={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
       'HOME':str(run/'home'),'TMPDIR':str(run/'tmp'),'XDG_CACHE_HOME':str(run/'cache'),
       'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','PYTHONHASHSEED':'0',
       'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1',
       'NUMEXPR_NUM_THREADS':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
       'GUARD_RUN_DIRECTORY':str(run),'GUARD_STOP_PATH':str(run/'STOP'),
       'GUARD_WORK_END':str(work_end),'GUARD_ABSOLUTE_END':str(absolute_end),
       'PIP_NO_CACHE_DIR':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1','PYTHONUSERBASE':str(run/'home'),
       'HF_HOME':str(run/'cache'),'UV_CACHE_DIR':str(run/'cache'),
       'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null',
       'GIT_TERMINAL_PROMPT':'0','GIT_OPTIONAL_LOCKS':'0'}
    return e

def phase_context(config_path,config_sha,phase,ordinary=False,handoff_sha=None):
    b=bootstrap(config_path,config_sha)
    c=b.load_config(config_path,config_sha,require_current=False)
    need(c['invocation']['phase']==phase,'guarded_phase_identity')
    leaf=phase_directory(c,phase)
    run=Path(os.environ.get('GUARD_RUN_DIRECTORY',''))
    need(run.is_absolute() and run.resolve()==run and run.parent==leaf/'runs','guard_run_origin')
    index=b.json_file(run/'index.json',MIB)
    spec_raw,spec_pin=raw_pin(leaf/'spec.json',MIB)
    spec=b.strict_json(spec_raw)
    mine=b.process_identity();original=index['original'];supervisor=index['supervisor']
    need(index['status']=='GATED' and index['run_directory']==str(run) and
         index['stop_path']==str(run/'STOP') and index['spec_sha256']==spec_pin['sha256'] and
         index['argv']==spec['argv'],'original_guard_index_binding')
    need(all(original[k]==mine[k] for k in ('pid','start_ticks','boot_id','pid_namespace')) and
         original['ppid']==supervisor['pid'] and
         all(supervisor[k]==c['invocation']['identity'][k] for k in ('pid','start_ticks','boot_id','pid_namespace')),
         'original_child_and_guard_tuple')
    if ordinary:
        need(phase=='runtime-import' and sys.orig_argv==ordinary_argv(c,config_path,config_sha,handoff_sha) and Path.cwd()==run/'cwd','ordinary_guarded_argv')
        need(not sys.flags.isolated and not sys.flags.no_site and sys.flags.no_user_site and sys.dont_write_bytecode,'ordinary_flags')
    else:
        need(sys.orig_argv==spec['argv'] and Path.cwd()==Path(spec['cwd']),'guarded_argv_cwd')
        need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,'isolated_phase_flags')
    absolute=c['invocation']['launch']['absolute_end_monotonic'];work=absolute-60
    need(float(os.environ['GUARD_ABSOLUTE_END'])==absolute and float(os.environ['GUARD_WORK_END'])==work,'phase_deadline_binding')
    expected=expected_environment(run,work,absolute)
    expected['GUARD_WORK_END']=os.environ['GUARD_WORK_END'];expected['GUARD_ABSOLUTE_END']=os.environ['GUARD_ABSOLUTE_END']
    need(dict(os.environ)==expected,'exact_offline_guard_environment')
    install=module_at('runtime/install.py')
    last=[0.]
    def check():
        now=time.monotonic();need(now<work and not (run/'STOP').exists(),'phase_stop_or_work_deadline')
        if phase in ('runtime-build-install','runtime-import'):return
        if phase not in ('runtime-host-inventory','runtime-wheel-audit','assets-model-audit','assets-corpus-select','runtime-build-install','runtime-import') or now-last[0]>=1.:
            b.admission(c,work);last[0]=time.monotonic()
    context={'roots':{'runtime':c['roots']['runtime'],
        'build':str(Path(c['roots']['runtime_container'])/'build-v1'),
        'evidence':str(leaf),'assets':str(Path(c['roots']['work'])/'assets'),
        'input':str(Path(c['roots']['work'])/'assets/input')},
        'limits':({'allocation_ref':c['phase_args']['inputs']['allocation']} if 'allocation' in c['phase_args']['inputs'] else {}),'phase':phase,'work_end':work,'absolute_end':absolute}
    ctx=install.Context(context,check);ctx.owner_config=c
    if phase in ('runtime-build-install','runtime-import'):
        ctx.full_census=lambda:b.admission(c,work)
    ctx.check(force=True)
    inputs={name:b.strict_json(b.read_bound(ref,4*MIB),4*MIB) for name,ref in c['phase_args']['inputs'].items()}
    return c,ctx,inputs,c['phase_args']['outputs']

def emit(ctx,outputs,name,value):
    need(name in outputs,'unapproved_phase_output')
    p=Path(outputs[name]);need(p.parent==ctx.roots['evidence'],'phase_output_scope')
    cap=32768 if name=='result' else 4*MIB
    ctx.check(force=True);B.admission(ctx.owner_config,ctx.config['work_end']);pin=B.write_new(p,value,cap);ctx.check(force=True)
    return {'path':str(p),**pin}

def fixture_phase(module,ctx):
    import unittest
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(module.RuntimeRecipeFixtures)
    wanted=suite.countTestCases();need(0<wanted<=256,'fixture_case_cap')
    result=unittest.TestResult();suite.run(result)
    ctx.check()
    value={'schema':'portable-runtime-fixtures-v1','status':'PASS' if result.wasSuccessful() else 'FAIL',
        'tests_run':result.testsRun,'expected_tests':wanted,
        'failures':[str(test) for test,_ in result.failures],
        'errors':[str(test) for test,_ in result.errors],'skipped':len(result.skipped),
        'network_requests':0,'model_loads':0,'model_calls':0,'outer_owner_terminal_required':True}
    need(len(value['failures'])+len(value['errors'])<=256,'fixture_failure_count_cap')
    return value

def discover_host(c,ctx,discovery):
    install=module_at('runtime/install.py');probe=module_at('runtime/probe_runtime.py')
    need(type(discovery) is dict and set(discovery)=={'schema','stdlib_root','stdlib_zip','base_prefix','system_paths'},'host_discovery_fields')
    need(discovery['schema']=='portable-host-discovery-v1','host_discovery_schema')
    prefix=Path(c['host']['python']).parent.parent
    need(discovery['base_prefix']==str(prefix) and discovery['stdlib_root']==str(prefix/'lib/python3.12') and
         discovery['stdlib_zip']==str(prefix/'lib/python312.zip'),'host_python_layout')
    probe.startup_loader_absence()
    need(not os.path.lexists(discovery['stdlib_zip']),'startup_zip_absent')
    paths=discovery['system_paths']
    need(type(paths) is list and len(paths)<=256 and len(paths)==len(set(paths)),'host_candidate_cap')
    observed=probe.mapping_paths()
    candidates=sorted(set(paths)|set(observed)|{'/usr/bin/ffmpeg','/usr/bin/ffprobe'})
    candidates=[p for p in candidates if p!=c['host']['python'] and not Path(p).is_relative_to(Path(discovery['stdlib_root']))]
    need(len(candidates)<=256,'host_system_file_cap')
    budget=probe.HostReadBudget();budget.reserve(install.PYTHON_PIN['bytes'])
    stdlib=probe.stdlib_inventory(install,ctx,discovery['stdlib_root'],budget)
    system={}
    for path in candidates:
        p=install.canonical(path);size=p.stat().st_size
        budget.reserve(size);system[path]=install.hash_file(p,ctx,maximum=128*MIB)
    native={};data=[]
    for path,pin in {c['host']['python']:install.PYTHON_PIN,**system}.items():
        with install.bound_regular(path,ctx,128*MIB) as stream:magic=stream.read(4)
        budget.reserve(4)
        if magic==b'\x7fELF':native[path]=probe.elf_metadata(install,ctx,path,pin,budget)
        else:data.append(path)
    host={'native_metadata':native,'system_data':data,'python':{'path':c['host']['python'],'pin':dict(install.PYTHON_PIN),'version':'3.12.14'},
        'base_prefix':discovery['base_prefix'],'stdlib':stdlib,
        'stdlib_zip':{'path':discovery['stdlib_zip'],'pin':None},'system_files':system,
        'observed_mappings':observed,
        'upload_tools':{'status':'PENDING_INDEPENDENT_ELF_GRAPH_AND_VERSION_REVIEW',
            'ffmpeg':system.get('/usr/bin/ffmpeg'),'ffprobe':system.get('/usr/bin/ffprobe')},
        'whole_host_read_bytes':budget.total,'independent_review_required':True,
        'genuine_upload_api_qualified':False,'cell_store_qualified':False}
    need(len(json.dumps(host,sort_keys=True).encode())<=4*MIB,'host_manifest_cap')
    return host

def audit_wheels(c,ctx,inputs):
    install=module_at('runtime/install.py')
    lock=B.json_file(HERE/'runtime/runtime.lock.json',131072,c['runtime_lock_sha256'])
    paths={row['id']:str(ctx.roots['assets']/row['destination']) for row in lock['wheels']}
    plans,ledger=install.prepare_locked_wheels(ctx,paths,lock['wheels'])
    return {'schema':'portable-runtime-wheel-plans-v1','plans':plans,'archives':[],
        'ledger':{'members':ledger.members,'expanded':ledger.expanded},
        'compressed_bytes':62806893,'artifacts':30,'materialized_bytes':0,
        'host_input_sha256':c['phase_args']['inputs']['host']['sha256'],
        'independent_allocation_review_required':True,'execution_authorized':False}

def complete_host(c,ctx,inputs):
    """One bounded static closure census; no payload staging or native execution."""
    i=module_at('runtime/install.py');p=module_at('runtime/probe_runtime.py')
    lock=B.json_file(HERE/'runtime/runtime.lock.json',131072,c['runtime_lock_sha256'])
    accepted=inputs['wheel_plans'];candidate=inputs['candidate']
    need(type(accepted) is dict and accepted.get('schema')=='portable-runtime-wheel-plans-v1' and
         accepted.get('artifacts')==30 and accepted.get('compressed_bytes')==62806893 and
         accepted.get('materialized_bytes')==0 and accepted.get('archives')==[] and
         accepted.get('host_input_sha256')==c['phase_args']['inputs']['candidate']['sha256'],
         'accepted_candidate_wheel_plan_binding')
    need(set(accepted['plans'])=={row['id'] for row in lock['wheels']},'exact_locked_native_plan_source')
    owner=B.strict_json(B.read_bound(c['acquisition']['owner_acceptance'],256*1024),256*1024)
    assets=B.strict_json(B.read_bound(c['assets_manifest'],98304),98304)
    manifest={row['id']:row for row in assets['artifacts']}
    audited={row['id']:row for row in owner['post_reap']['artifacts']}
    paths={};descriptors={}
    for row in lock['wheels']:
        name=row['id'];asset=manifest[name];observed=audited[name]
        need(all(asset[key]==observed[key] for key in ('id','destination','bytes','sha256')) and
             all(row[key]==asset[key] for key in ('bytes','sha256')),'native_wheel_original_pin')
        paths[name]=str(ctx.roots['assets']/asset['destination'])
        descriptors[name]=observed['descriptor_after']
    class MetadataBudget:
        def __init__(self):self.total=0
        def reserve(self,count):
            need(type(count) is int and 0<=count<=65536 and self.total+count<=16*MIB,
                 'native_elf_metadata_read_cap')
            self.total+=count
    budget=MetadataBudget()
    def elf_reader(raw,pin):return p.elf_metadata_bytes(raw,pin,ctx,budget)
    print(json.dumps({'phase':'runtime-host-inventory','event':'native-members-start','members':49}),flush=True)
    native=i.inspect_native_wheel_members(ctx,paths,lock['wheels'],accepted['plans'],descriptors,elf_reader)
    native['ledger']['elf_metadata_bytes']=budget.total
    need(native['ledger']['compressed_zip_read_bytes']+budget.total<=128*MIB,'joint_native_read_cap')
    host=p.complete_host_closure(i,ctx,candidate,native['candidates'],inputs['policy'])
    host['completion_provenance']={'candidate':c['phase_args']['inputs']['candidate'],
        'wheel_plans':c['phase_args']['inputs']['wheel_plans'],'policy':c['phase_args']['inputs']['policy'],
        'native_member_ledger':native['ledger'],'native_executions':0,
        'actual_loader_selection':'PENDING_IMPORT_MAPPING_CHECK',
        'sqlite_builtin_origin':'PENDING_SEPARATE_QUALIFICATION'}
    need(len(json.dumps(host,sort_keys=True,allow_nan=False).encode())<=4*MIB,'completed_host_output_cap')
    print(json.dumps({'phase':'runtime-host-inventory','event':'static-closure-complete',
        'members':len(native['candidates']),'system_files':len(host['system_files'])}),flush=True)
    return host

def audit_models(c,ctx,inputs):
    install=module_at('runtime/install.py');specs=inputs['archive_specs']
    need(type(specs) is dict and set(specs)=={'schema','artifacts'} and specs['schema']=='portable-runtime-archive-specs-v1','archive_specs')
    rows=specs['artifacts'];need(type(rows) is list and len(rows)==3 and {r['id']:r['spec']['asset_class'] for r in rows}=={'zipformer-small-audioset':'audioset','sensevoice-small-int8':'sensevoice','linux-x86_64-cpu':'native'},'three_model_archive_classes')
    archives=[]
    for row in rows:
        need(set(row)=={'id','spec'},'archive_spec_fields')
        asset,path=asset_input(c,ctx,row['id']);spec=row['spec']
        install.audit_progress(ctx,'archive','start',row['id'])
        plan=install.audit_archive_plan(path,spec,ctx,{k:asset[k] for k in ('bytes','sha256')})
        install.audit_progress(ctx,'archive','end',row['id'])
        archives.append({'id':row['id'],'spec':spec,'plan':plan})
    return {'schema':'portable-runtime-model-plans-v1','archives':archives,
        'materialized_bytes':0,'independent_allocation_review_required':True,'execution_authorized':False}

def select_corpus(c,ctx,inputs):
    install=module_at('runtime/install.py');ref=c['acquisition']['owner_acceptance']
    owner=B.strict_json(B.read_bound(ref,256*1024),256*1024)
    rows=[r for r in owner['post_reap']['artifacts'] if {k:r[k] for k in ('bytes','sha256')}==install.OFFICIAL_ARCHIVE_PIN]
    need(len(rows)==1,'one_accepted_official_archive');row=rows[0]
    asset,path=asset_input(c,ctx,row['id'])
    need(all(row[k]==asset[k] for k in ('id','destination','bytes','sha256')),'selected_archive_manifest_binding')
    return install.select_corpus_wav(ctx,path,install.OFFICIAL_ARCHIVE_PIN,row['descriptor_after'],inputs['allocation'],ref)

def asset_input(c,ctx,identifier):
    assets=B.strict_json(B.read_bound(c['assets_manifest'],98304),98304)
    rows=[r for r in assets['artifacts'] if r['id']==identifier]
    need(len(rows)==1,'closed_asset_identity');row=rows[0]
    return row,ctx.roots['assets']/row['destination']

def build_audit(ctx):
    build=ctx.roots['build'];tmp=Path(os.environ['TMPDIR'])
    def hook(event,args):
        if event.startswith(('socket.','subprocess.','os.spawn')) or event in ('os.system','os.fork','os.forkpty','os.exec','pty.spawn','ctypes.dlopen','sqlite3.connect'):
            raise Refused('build_action_denied')
        if event=='open' and args and isinstance(args[0],(str,bytes)):
            p=Path(os.fsdecode(args[0])).absolute();flags=args[2] if len(args)>2 and type(args[2]) is int else 0
            mode=args[1] if len(args)>1 and isinstance(args[1],str) else ''
            if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND) or any(x in mode for x in 'wax+'):
                owned=any(p.is_relative_to(r) for r in (build/'source',build/'wheels',tmp))
                exact=str(p) in ctx.admission['targets'] and flags&os.O_EXCL
                evidence=p.is_relative_to(ctx.roots['evidence']) and flags&os.O_EXCL
                need(owned or exact or evidence,'build_write_scope')
        if event=='os.mkdir':
            need(isinstance(args[0],(str,bytes)) and any(Path(os.fsdecode(args[0])).absolute().is_relative_to(r) for r in (build,ctx.roots['runtime'],ctx.roots['evidence'])),'build_mkdir_scope')
        if event in ('os.remove','os.rename','os.rmdir','os.symlink','os.link'):
            need(event not in ('os.symlink','os.link') and all(not isinstance(p,(str,bytes)) or any(Path(os.fsdecode(p)).absolute().is_relative_to(r) for r in (build/'source',build/'wheels',tmp)) for p in args[:2]),'build_mutation_scope')
    sys.addaudithook(hook)

CONTINUATION_PROGRESS_IDS=('retained-files','retained-backend-audit',
 'wheel-sherpa-onnx-core','wheel-soundfile','wheel-starlette','wheel-typing-extensions',
 'wheel-typing-inspection','wheel-uvicorn','transcribe-cpp','transcribe-cpp-native',
 'retained-backend-install','final-inventory')


def continue_runtime(c,ctx,inputs):
    """One owner-sealed interrupted build; retain exact bytes and fill missing targets."""
    i=module_at('runtime/install.py');p=module_at('runtime/probe_runtime.py')
    need(c['phase_args']['options']=={'host_verification':'CANDIDATE_BYTES_AND_OBSERVED_MAPPINGS'},'continuation_candidate_mode')
    lock=B.json_file(HERE/'runtime/runtime.lock.json',131072,c['runtime_lock_sha256'])
    plans=inputs['wheel_plans']['plans']
    need(inputs['wheel_plans']['host_input_sha256']==c['phase_args']['inputs']['host']['sha256'] and
         set(plans)=={row['id'] for row in lock['wheels']},'continuation_wheel_host_pair')
    marker=lambda edge,name:i.audit_progress(ctx,'wheel',edge,name)
    marker('start',CONTINUATION_PROGRESS_IDS[0])
    p.verify_host(i,ctx,inputs['host']);i.admit_materialization(ctx)
    state=i.validate_build_continuation(ctx)
    retained=state['retained_runtime'];expected=state['expected_runtime']
    all_wheel_files={}
    for row in lock['wheels']:
        if row['role']!='runtime':continue
        for name,pin in plans[row['id']]['members'].items():
            relative=i.SITE+'/'+name
            need(relative not in all_wheel_files,'continuation_cross_wheel_collision')
            all_wheel_files[relative]=pin
    need(all_wheel_files=={name:pin for name,pin in expected.items() if name.startswith(i.SITE+'/')},'continuation_exact_runtime_plans')
    need(set(expected)-set(all_wheel_files)=={'bin/python3.12','pyvenv.cfg'} and
         len(retained)==1452 and len(expected)==1614,'continuation_fixed_inventory')
    marker('end',CONTINUATION_PROGRESS_IDS[0])
    host=inputs['host'];site=ctx.roots['build']/'site'
    need(sys.path==[host['stdlib_zip']['path'],host['stdlib']['root'],str(Path(host['stdlib']['root'])/'lib-dynload')],'isolated_continuation_search_paths')
    sys.path.insert(0,str(site));build_audit(ctx)
    versions=lock['selection']['build_only_distributions']
    i.verify_requirement_closure([plans[row['id']] for row in lock['wheels'] if row['role']=='build'],versions)
    i.verify_requirement_closure([plans[row['id']] for row in lock['wheels'] if row['role']=='runtime'],lock['selection']['runtime_distributions'])
    marker('start',CONTINUATION_PROGRESS_IDS[1])
    wheel,backend=i.reuse_backend_wheel(ctx,state['source_pins'],versions,state['backend_pin'])
    i.verify_requirement_closure([backend],lock['selection']['runtime_distributions'],('inference',))
    marker('end',CONTINUATION_PROGRESS_IDS[1])
    pending=[row for row in lock['wheels'] if row['role']=='runtime' and
             any(i.SITE+'/'+name not in retained for name in plans[row['id']]['members'])]
    need(tuple(row['id'] for row in pending)==CONTINUATION_PROGRESS_IDS[2:10] and
         sum(i.SITE+'/'+name not in retained for row in pending for name in plans[row['id']]['members'])==162,
         'continuation_exact_remaining_artifacts')
    collisions=set()
    for row in pending:
        marker('start',row['id'])
        i.materialize_remaining_wheel(ctx,asset_input(c,ctx,row['id'])[1],row,plans[row['id']],retained,collisions)
        marker('end',row['id'])
    marker('start',CONTINUATION_PROGRESS_IDS[10])
    i.materialize_wheel(ctx,state['backend_pin']['path'],wheel,backend,'runtime',collisions)
    marker('end',CONTINUATION_PROGRESS_IDS[10])
    files=dict(expected)
    for name,pin in backend['members'].items():
        relative=i.SITE+'/'+name
        need(relative not in files,'continuation_backend_collision');files[relative]=pin
    marker('start',CONTINUATION_PROGRESS_IDS[11])
    i.complete_tree(ctx,ctx.roots['runtime'],files,320*MIB,{'bin/python3.12':0o755})
    ctx.check(force=True)
    marker('end',CONTINUATION_PROGRESS_IDS[11])
    return {'schema':'portable-runtime-inventory-v1','status':'VERIFIED_RUNTIME_BYTES','runtime_root':str(ctx.roots['runtime']),'files':files,'host_sha256':c['phase_args']['inputs']['host']['sha256'],'product_commit':i.PRODUCT_COMMIT,'outer_owner_terminal_required':True}


def build_runtime(c,ctx,inputs):
    if 'continuation' in inputs['allocation']:
        return continue_runtime(c,ctx,inputs)
    i=module_at('runtime/install.py');p=module_at('runtime/probe_runtime.py')
    lock=B.json_file(HERE/'runtime/runtime.lock.json',131072,c['runtime_lock_sha256'])
    p.verify_host(i,ctx,inputs['host']);i.admit_materialization(ctx)
    fresh=audit_wheel_bytes(c,ctx,lock)
    plans,ledger=fresh
    need(plans==inputs['wheel_plans']['plans'] and inputs['wheel_plans']['host_input_sha256']==c['phase_args']['inputs']['host']['sha256'],'accepted_wheel_plan_changed')
    source=B.strict_json(B.read_bound(c['assets_manifest'],98304),98304)['source']['files']
    pins={}
    for name,sha in source.items():
        pin=i.hash_file(Path(c['roots']['product'])/name,ctx,maximum=32*MIB)
        need(pin['sha256']==sha,'product_source_pin');pins[name]=pin
    need(len(pins)==351 and sum(p['bytes'] for p in pins.values())==4651663,'product_source_closure')
    i.copy_product(ctx,c['roots']['product'],pins)
    collisions={'runtime':set(),'build':set()};files={}
    for row in lock['wheels']:
        if row['role']=='build':i.materialize_wheel(ctx,asset_input(c,ctx,row['id'])[1],row,plans[row['id']],'build',collisions['build'])
    site=ctx.roots['build']/'site';host=inputs['host']
    need(sys.path==[host['stdlib_zip']['path'],host['stdlib']['root'],str(Path(host['stdlib']['root'])/'lib-dynload')],'isolated_build_search_paths')
    sys.path.insert(0,str(site));build_audit(ctx)
    versions=lock['selection']['build_only_distributions']
    i.verify_requirement_closure([plans[r['id']] for r in lock['wheels'] if r['role']=='build'],versions)
    wheel,backend,_=i.build_backend(ctx,pins,versions,ledger)
    i.verify_requirement_closure([plans[r['id']] for r in lock['wheels'] if r['role']=='runtime'],lock['selection']['runtime_distributions'])
    i.verify_requirement_closure([backend],lock['selection']['runtime_distributions'],('inference',))
    files.update(i.prepare_interpreter(ctx,host))
    for row in lock['wheels']:
        if row['role']=='runtime':
            plan=plans[row['id']];i.materialize_wheel(ctx,asset_input(c,ctx,row['id'])[1],row,plan,'runtime',collisions['runtime'])
            files.update({i.SITE+'/'+name:pin for name,pin in plan['members'].items()})
    i.materialize_wheel(ctx,ctx.roots['build']/'wheels'/('long_audio_backend-'+i.PRODUCT_VERSION+'-py3-none-any.whl'),wheel,backend,'runtime',collisions['runtime'])
    files.update({i.SITE+'/'+name:pin for name,pin in backend['members'].items()})
    i.complete_tree(ctx,ctx.roots['runtime'],files,320*MIB,{'bin/python3.12':0o755})
    for name,pin in pins.items():i.hash_file(Path(c['roots']['product'])/name,ctx,pin)
    return {'schema':'portable-runtime-inventory-v1','status':'VERIFIED_RUNTIME_BYTES','runtime_root':str(ctx.roots['runtime']),'files':files,'host_sha256':c['phase_args']['inputs']['host']['sha256'],'product_commit':i.PRODUCT_COMMIT,'outer_owner_terminal_required':True}

def audit_wheel_bytes(c,ctx,lock):
    i=module_at('runtime/install.py')
    return i.prepare_locked_wheels(ctx,{r['id']:str(asset_input(c,ctx,r['id'])[1]) for r in lock['wheels']},lock['wheels'])

def ordinary_argv(c,path,sha,handoff):
    need(type(handoff) is str and len(handoff)==64 and set(handoff)<=set('0123456789abcdef'),'handoff_sha')
    return [str(Path(c['roots']['runtime'])/'bin/python3.12'),'-B',str(HERE/'guard_driver.py'),'--phase','runtime-import','--config',str(path),'--config-sha256',sha,'--normal-startup','--handoff-sha256',handoff]

def import_runtime(c,ctx,inputs,args):
    i=module_at('runtime/install.py');p=module_at('runtime/probe_runtime.py');host=inputs['host']
    cfg=inputs['probe_configuration']
    need(type(cfg) is dict and set(cfg)=={'allowed_files','builtin_modules','allowed_mappings','entry_points','model_audio_roots'},'probe_controls')
    cfg=dict(cfg);run=Path(os.environ['GUARD_RUN_DIRECTORY']);cwd=run/'cwd'
    if not args.normal_startup:need(not os.path.lexists(cwd),'fresh_probe_cwd');cwd.mkdir(mode=0o700)
    packet=B.verify_packet()
    cfg.update(recipe_root=str(HERE/'runtime'),packet_root=str(HERE),recipe_files={name.split('/',1)[1]:pin for name,pin in packet['files'].items() if name.startswith('runtime/')},clean_cwd=str(cwd),empty_home=str(run/'home'),environment=dict(os.environ),phase_config_path=args.config,config_sha256=args.config_sha256,runtime_seal=c['phase_args']['inputs']['runtime_inventory'],host_seal=c['phase_args']['inputs']['host'],ordinary_paths=[str(HERE),host['stdlib_zip']['path'],host['stdlib']['root'],str(Path(host['stdlib']['root'])/'lib-dynload'),str(ctx.roots['runtime']/i.SITE)])
    if not args.normal_startup:p.same_pid_exec(i,ctx,host,inputs['runtime_inventory']['files'],cfg)
    cfg['ordinary_argv']=ordinary_argv(c,args.config,args.config_sha256,args.handoff_sha256)
    handoff=B.json_file(ctx.roots['evidence']/'probe-handoff.json',65536,args.handoff_sha256)
    p.validate_ordinary_state(ctx,host,cfg,handoff)
    lock=B.json_file(HERE/'runtime/runtime.lock.json',131072,c['runtime_lock_sha256'])
    result=p.import_only(i,ctx,host,inputs['runtime_inventory']['files'],cfg,lock['selection']['runtime_distributions'],lock['provider_entry_points'])
    raw=json.dumps(result,sort_keys=True,allow_nan=False);need(len(raw.encode())<=MIB,'bounded_import_pipe_report')
    print(raw,flush=True)
    return None

def extract_assets(c,ctx,inputs,phase):
    i=module_at('runtime/install.py');i.admit_materialization(ctx)
    if phase=='assets-input-derive':
        row=inputs['clip_plan'];need(set(row)=={'plan','review'},'clip_input_fields')
        for key in ('official_archive_path','source_path'):need(Path(row['review'][key]).is_relative_to(ctx.roots['assets']),'clip_asset_root')
        return i.materialize_canonical_clip(ctx,row['plan'],row['review'])
    rows=inputs['archive_plans'] if phase=='assets-model-extract' else [inputs['archive_plan']]
    need(type(rows) is list and len(rows)==(3 if phase=='assets-model-extract' else 1),'archive_operation_count')
    need({r['spec']['asset_class'] for r in rows}==({'audioset','sensevoice','native'} if len(rows)==3 else {'corpus'}),'archive_phase_classes')
    reports=[]
    for row in rows:
        need(set(row)=={'id','spec','plan','review'},'archive_input_fields')
        asset,path=asset_input(c,ctx,row['id']);need(row['spec']['archive_pin']=={k:asset[k] for k in ('bytes','sha256')},'archive_manifest_pin')
        result=i.materialize_archive(ctx,path,row['spec'],row['plan'],row['review'])
        reports.append({'id':row['id'],'plan_sha256':i.canonical_digest(row['plan']),'files':len(result['files']),'output_readback':True})
    result={'schema':'portable-runtime-extraction-result-v1','status':'MATERIALIZED_PINNED_BYTES','artifacts':reports,'outer_owner_terminal_required':True,'model_calls':0}
    if phase=='assets-corpus-extract':
        op=i.require_materialization_charge(ctx,'assets',row['plan'])
        wav=[r for r in row['plan']['regulars'].values() if {k:r[k] for k in ('bytes','sha256')}==i.OFFICIAL_WAV_PIN]
        need(len(wav)==1,'official_selected_source')
        result['clip_plan']=i.audit_canonical_clip(ctx,path,row['spec']['archive_pin'],row['plan'],ctx.roots['assets']/op['prefix']/wav[0]['path'])
    return result

def phase_main(phase,module=None,args=None):
    need(args is not None and all(hasattr(args,k) for k in ('config','config_sha256','phase')),
         'UNADMITTED_missing_guarded_arguments')
    need(args.phase==phase,'phase_argument_changed')
    c,ctx,inputs,outputs=phase_context(args.config,args.config_sha256,phase,getattr(args,'normal_startup',False),getattr(args,'handoff_sha256',None))
    expected=B.PHASES[phase][1]
    if module is None:module=module_at(expected)
    need(Path(module.__file__).resolve()==HERE/expected,'closed_phase_module')
    if phase=='runtime-fixtures':
        result=fixture_phase(module,ctx)
    elif phase=='runtime-host-inventory':
        host=complete_host(c,ctx,inputs)
        ref=emit(ctx,outputs,'host',host)
        result={'schema':'portable-runtime-host-result-v1','status':'COMPLETED_PINNED_STATIC_HOST_CLOSURE',
                'host':ref,'imports_executed':False,'tool_executions':0,'model_calls':0,
                'independent_review_required':True,'outer_owner_terminal_required':True}
    elif phase=='runtime-wheel-audit':
        plans=audit_wheels(c,ctx,inputs);ref=emit(ctx,outputs,'wheel_plans',plans)
        result={'schema':'portable-runtime-wheel-audit-result-v1','status':'VERIFIED_WHEEL_BYTES_ONLY',
                'wheel_plans':ref,'artifacts':30,'compressed_bytes':62806893,
                'materialized_bytes':0,'model_calls':0,'outer_owner_terminal_required':True}
    elif phase=='assets-model-audit':
        plans=audit_models(c,ctx,inputs);ref=emit(ctx,outputs,'archive_plans',plans)
        result={'schema':'portable-runtime-model-audit-result-v1','status':'AUDITED_MODEL_ARCHIVES_ONLY',
                'archive_plans':ref,'artifacts':3,'materialized_bytes':0,'model_calls':0,'outer_owner_terminal_required':True}
    elif phase=='assets-corpus-select':result=select_corpus(c,ctx,inputs)
    elif phase=='runtime-build-install':
        inventory=build_runtime(c,ctx,inputs);ref=emit(ctx,outputs,'runtime_inventory',inventory)
        result={'schema':'portable-runtime-build-result-v1','status':'VERIFIED_RUNTIME_BYTES','runtime_inventory':ref,'outer_owner_terminal_required':True,'model_calls':0}
        if c['phase_args']['options']=={'host_verification':'CANDIDATE_BYTES_AND_OBSERVED_MAPPINGS'}:
            result.update(host_verification='CANDIDATE_BYTES_AND_OBSERVED_MAPPINGS',static_native_closure_complete=False)
    elif phase=='runtime-import':
        import_runtime(c,ctx,inputs,args);return 0
    else:result=extract_assets(c,ctx,inputs,phase)
    output=emit(ctx,outputs,'result',result)
    print(json.dumps({'phase':phase,'status':result['status'],'result':output},sort_keys=True),flush=True)
    return 0 if result['status']!='FAIL' else 1

def launch(args):
    b=bootstrap(args.template,args.template_sha256)
    raw,tp=raw_pin(args.template,65536);template=b.strict_json(raw,65536)
    raw,gp=raw_pin(args.go,4096);need(gp['sha256']==args.go_sha256,'phase_go_pin')
    go=b.strict_json(raw,4096)
    wanted={'schema':'portable-runtime-phase-go-v1','decision':'RUN_ONCE','phase':args.phase,
            'packet_sha256':template['packet_sha256'],'config_template_sha256':tp['sha256']}
    need(b.same_data(go,wanted),'closed_phase_go')
    release={'template':{'path':str(Path(args.template)),**tp},'go':{'path':str(Path(args.go)),**gp}}
    c=b.configure_invocation(template,args.phase,LAUNCH_START,release)
    leaf=phase_directory(c,args.phase)
    need(leaf.parent.resolve()==leaf.parent and not os.path.lexists(leaf),'fresh_phase_leaf')
    need(all(not os.path.lexists(p) for p in c['phase_args']['outputs'].values()),'fresh_phase_outputs')
    b.admission(c,c['invocation']['launch']['absolute_end_monotonic']-60)
    if not leaf.parent.exists():leaf.parent.mkdir(mode=0o700)
    leaf.mkdir(mode=0o700)
    claim={'schema':'portable-single-dispatch-v1','phase':args.phase,'attempts':1}
    b.write_new(leaf/'claim.json',claim,4096)
    cp=b.write_new(leaf/'config.json',c,65536)
    b.load_config(leaf/'config.json',cp['sha256'])
    spec=b.make_spec(c,leaf/'config.json',cp['sha256'],args.phase,c['invocation']['launch'])
    sp=b.write_new(leaf/'spec.json',spec,MIB)
    b.validate_launch(c['invocation']['launch'],args.phase)
    argv=[c['host']['python'],'-I','-B','-S',str(HERE/'acquisition_guard.py'),
          '--spec',str(leaf/'spec.json'),'--spec-sha256',sp['sha256']]
    print(json.dumps({'status':'SAME_PID_GUARD_EXEC','phase':args.phase,'spec':str(leaf/'spec.json'),
                      'spec_sha256':sp['sha256']},sort_keys=True),flush=True)
    os.execve(argv[0],argv,dict(os.environ))
    raise Refused('exec_returned')

def main(args=None):
    argv=sys.argv[1:] if args is None else args
    need(type(argv) is list and argv,'UNADMITTED_missing_phase_arguments')
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',choices=tuple(INPUTS),required=True)
    if '--config' in argv:
        parser.add_argument('--config',required=True);parser.add_argument('--config-sha256',required=True)
        parser.add_argument('--normal-startup',action='store_true');parser.add_argument('--handoff-sha256')
        parsed=parser.parse_args(argv);return phase_main(parsed.phase,args=parsed)
    parser.add_argument('--template',required=True);parser.add_argument('--template-sha256',required=True)
    parser.add_argument('--go',required=True);parser.add_argument('--go-sha256',required=True)
    return launch(parser.parse_args(argv))

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status':'REFUSED_OR_FAILED_CLOSED','reason':type(error).__name__+': '+str(error)[:240]}),flush=True)
        raise SystemExit(2) from None
