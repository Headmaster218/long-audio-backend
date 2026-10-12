"""Closed stage adapter; the qualified guard remains the original process owner.

Preparation is inert. No actual authority is created by this source package.
The baseline is the independently qualified dev5 three-delta derivative of the
historical actual guard, not a new ownership implementation.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
WORKSPACE=ROOT.parent
SOURCE=WORKSPACE/'reconstruction-rc3-dev5'
RUNTIME=WORKSPACE/'reconstruction-rc3-dev5-api-prep/runtime'
PYTHON=RUNTIME/'bin/python3.12'
PYTHON_SHA='392a44f371cf0c701d7ca83ad3fa5d4e87184e59d4f8e7b96fa482288d5f45f1'
GUARD=WORKSPACE/'reconstruction-rc3-dev5-checks-final2/guard/actual_guard.py'
GUARD_SHA='5ca69ba2ad94bfcac730b2da1763872ac28afd53b261150760484578dd8d1030'
OLD_GUARD=WORKSPACE/'reconstruction-rc3-dev3-real-prep/actual-guard/actual_guard.py'
OLD_GUARD_SHA='10e155eaca24f7c56518e07cc3b10b46157d8957330d42b963c9b840b3535a78'
ADMISSION=WORKSPACE/'reconstruction-rc3-dev3-real-prep/storage-proof/admission.py'
ADMISSION_SHA='64a2a437f5ca222c0910ac002f0089cba168e285622e4ca61c93d0f855c690a6'
RUN=ROOT/'runs/run-PRIVATE_TEMPLATE'
STAGES={'api_smoke_fixtures':300,'api_smoke_hash':300,'api_smoke_actual':1500}
SOURCES=('ownership.py','instrumentation.py','common.py','bootstrap.py','server.py','capture.py','launch.py','test_smoke.py')
META=('PROFILE.json','BASELINE_PINS.json','FIXTURE_IDS.json','SOURCE_FREEZE.json','AST_PARITY.json','README.md',
      'readiness/COMBINED_IMPORT_TERMINAL.json','readiness/STORAGE_INITIAL_ADMISSION.json','readiness/ORIGINS.json')
OVERRIDES=('stage_seconds','profile','actual_admission','scratch_bytes','Guard.verify_capture_posthash')


def require(value,reason):
    if not value:raise ValueError(reason)


def read(path,maximum=2**20):
    path=Path(path)
    require(path.is_absolute() and path.resolve()==path and path.is_file() and not path.is_symlink()
            and path.stat().st_size<=maximum,'regular_bounded_metadata')
    raw=path.read_bytes();require(len(raw)<=maximum,'metadata_growth');return raw


def sha(path):return hashlib.sha256(read(path,64*2**20)).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def source_names():return {str(HERE/name) for name in SOURCES}
def common_names():
    return (source_names()|{str(ROOT/name) for name in META}|{str(GUARD),str(OLD_GUARD),str(ADMISSION)}
            |{row['path'] for row in profile_data()['evidence_pins']})


def profile_data():
    value=json.loads(read(ROOT/'PROFILE.json'))
    require(value['schema']=='dev5-api-smoke-profile-v1' and value['source_root']==str(SOURCE)
            and value['runtime_root']==str(RUNTIME) and value['actual_run_authorized'] is False,
            'closed_preparation_profile')
    for row in value['evidence_pins']:
        require(sha(Path(row['path']))==row['sha256'],'readiness_evidence_pin')
    return value


def stage_profile(spec):
    require(spec.get('stage') in STAGES,'closed_stage')
    actual=spec['stage']=='api_smoke_actual'
    maximum=STAGES[spec['stage']]
    require(type(spec.get('absolute_seconds')) is int and spec['absolute_seconds']==maximum and
            type(spec.get('work_seconds')) is int and spec['work_seconds']==maximum-60,'clock_partition')
    address,rss,cpus=(8*2**30,4*2**30,[0,1]) if actual else (2**30,256*2**20,[3])
    require(spec.get('address_space_cap_bytes')==address and type(spec['address_space_cap_bytes']) is int and
            spec.get('rss_cap_bytes')==rss and type(spec['rss_cap_bytes']) is int and
            spec.get('cpus')==cpus and all(type(x) is int for x in spec['cpus']) and
            spec.get('scratch_cap_bytes')==12*2**30,'closed_resources')
    if not actual:require('controller_spec' not in spec and 'final_manifest' not in spec,'fixtures_cannot_authorize_actual')
    return actual,set(cpus),address,rss


def validate_source_binding(spec,freeze):
    require(spec.get('source_root')==str(SOURCE) and
            spec.get('source_files')==freeze['product_source_files'] and
            freeze['product_source_count']==331 and freeze['product_source_bytes']==4098577,
            'exact_product_source_closure')


def budget_projection(metadata,fixtures,startup,actual,slack=0):
    values=(metadata,fixtures,startup,actual,slack)
    limits=(16*2**20,16*2**20,16*2**20,448*2**20,16*2**20)
    require(all(type(x) is int and 0<=x<=cap for x,cap in zip(values,limits)) and
            sum(values)<=512*2**20,'smoke_component_reservation')
    return sum(values)


def readiness_contract(controller,read_pin):
    """Exact real receipt schemas; historical evidence never becomes generic PASS."""
    chosen=profile_data()
    storage=read_pin(controller['storage_admission']['receipt'])
    require(controller['storage_admission']['receipt']==chosen['storage_admission'] and
            storage.get('schema')=='dev5-api-smoke-storage-admission-v1' and
            storage.get('status')=='PROSPECTIVE_CAPACITY_RESERVED' and
            storage.get('fresh_guard_readmission_required') is True and
            storage.get('does_not_authorize_actual_execution') is True and
            storage.get('does_not_qualify_physical_store_behavior') is True and
            storage.get('global_cap_bytes')==12*2**30 and
            storage.get('ordinary_free_floor_bytes')==11*2**29 and
            storage.get('old_unknown_disk_bytes')==320*2**20 and
            storage.get('old_unknown_rss_bytes')==512*2**20 and
            storage.get('components')==chosen['storage_components'], 'prospective_storage_schema')
    imported=read_pin(controller['installed_runtime']['receipt'])
    require(controller['installed_runtime']['receipt']==chosen['runtime_import_local'] and
            imported.get('schema')=='dev5-combined-runtime-import-terminal-v1' and
            imported.get('status')=='PASS_IMPORT_ONLY' and imported.get('original_tool_wait_reaped') is True and
            imported.get('model_session_audio_inference_service_network_entries')==0,'qualified_import_only')
    fixtures=read_pin(controller['model_free_tests']['receipt'])
    require(controller['model_free_tests']['receipt']==controller['positive_owned_probe']['receipt'] and
            fixtures.get('schema')=='dev5-api-smoke-fixtures-terminal-v1' and
            fixtures.get('status')=='PASS' and fixtures.get('cleanup')=='CONFIRMED' and
            fixtures.get('tests_run')==41 and fixtures.get('failures')==0 and fixtures.get('errors')==0 and
            fixtures.get('actual_acoustic_calls')==0 and fixtures.get('original_tool_wait_reaped') is True and
            fixtures.get('source_freeze_sha256')==controller['source_freeze_sha256'],
            'exact_41_fixture_and_positive_owned_terminal_required')
    manifest=read_pin(controller['inventory_manifest'])
    require(controller['inventory_manifest']['path']==str(ROOT/'HASH_INVENTORY.json') and
            manifest.get('schema')=='dev5-api-smoke-immutable-inventory-v1' and
            manifest.get('source_freeze_sha256')==controller['source_freeze_sha256'] and
            manifest.get('hash_inventory')==controller['hash_inventory'] and
            manifest.get('inventory_sha256')==hashlib.sha256(canonical(sorted(controller['hash_inventory'],
                key=lambda row:row['path']))).hexdigest(),'exact_immutable_inventory')
    hashed=read_pin(controller['posthash_probe']['receipt'])
    require(hashed.get('schema')=='dev5-api-smoke-hash-terminal-v1' and
            hashed.get('status')=='PASS_HASH_ONLY' and hashed.get('cleanup')=='CONFIRMED' and
            hashed.get('original_tool_wait_reaped') is True and hashed.get('all_matched') is True and
            hashed.get('source_freeze_sha256')==controller['source_freeze_sha256'] and
            hashed.get('inventory_manifest_sha256')==controller['inventory_manifest']['sha256'] and
            hashed.get('inventory_sha256')==manifest['inventory_sha256'] and
            hashed.get('files')==len(controller['hash_inventory']) and
            hashed.get('bytes')==sum(row['bytes'] for row in controller['hash_inventory']) and
            type(hashed.get('elapsed_seconds')) in (int,float) and 0<=hashed['elapsed_seconds']<=120 and
            all(hashed.get(k)==0 for k in ('product_imports','audio_decodes','services','model_calls')),
            'exact_full_inventory_hash_terminal_required')


def configure(g):
    require(g.WORKSPACE==WORKSPACE and g.MAX_DISK==12*2**30 and g.ACTUAL_RSS==4*2**30 and
            g.ACTUAL_VISIBLE_RSS==6*2**30 and g.ACTUAL_AS==8*2**30 and g.CLEANUP_RESERVE==60 and
            g.OLD_UNKNOWN_DISK==320*2**20 and g.ORDINARY_FREE_FLOOR==11*2**29 and g.POLL==.05,
            'qualified_guard_geometry')
    original={name:value for name,value in vars(g.Guard).items() if callable(value)}
    original_scratch=g.scratch_bytes
    g.CHECKS=ROOT;g.STAGES=dict(STAGES)
    def accounting(end,reserve=0):
        total=original_scratch(end,[ROOT])
        runs=ROOT/'runs'
        run_total=original_scratch(end,[runs]) if runs.exists() else 0
        actual=original_scratch(end,[RUN]) if RUN.exists() else 0
        # Startup authority is dormant; its separate 16 MiB remains reserved.
        budget_projection(total-run_total,run_total-actual,0,actual,min(reserve,16*2**20))
        require(total+max(reserve,16*2**20)<=512*2**20,'smoke_root_terminal_reserve')
        return total
    def scratch_bytes(end,roots=None):
        accounting(end,g.RECEIPT_RESERVE)
        return original_scratch(end,roots)
    def stage_seconds(spec):
        stage_profile(spec);return spec['absolute_seconds'],spec['work_seconds']
    def actual_admission(spec,end):
        g.clock_check(end)
        require(spec['stage']=='api_smoke_actual' and spec['controller_spec']==spec['final_manifest'],
                'one_actual_controller_pin')
        controller=g.read_pin_json(spec['controller_spec'],end)
        require(spec['controller_spec']['path']==str(ROOT/'controller.actual.json') and
                controller.get('schema')=='dev5-api-smoke-controller-v1' and
                controller.get('actual_run_authorized') is True and
                controller.get('source_root')==str(SOURCE) and controller.get('source_file_count')==331 and
                controller.get('source_head')=='e6791e6d1b78a14c113529d809b8486349be1146' and
                controller.get('source_tree')=='0aa2ac344d4513d9e9cfd68897957303f92e550d' and
                controller.get('source_freeze_sha256')==sha(ROOT/'SOURCE_FREEZE.json'),
                'current_dev5_controller_required')
        approval=g.read_pin_json(controller['approval_receipt'],end)
        require(controller['approval_receipt']['path']==str(ROOT/'ACTUAL_AUTHORITY.json') and
                approval.get('schema')=='dev5-api-smoke-authority-v1' and
                approval.get('actual_capture_approved') is True and
                approval.get('scope')=='one-fresh-primary-and-one-moss-job' and
                approval.get('source_head')==controller['source_head'] and
                approval.get('source_freeze_sha256')==controller['source_freeze_sha256'] and
                type(approval.get('authorization_reference')) is str and
                0<len(approval['authorization_reference'])<=2048,'separate_exact_root_GO')
        require(controller['output_dir']==str(RUN/'tmp/smoke') and controller['data_dir']==str(RUN/'tmp/smoke/data')
                and controller['python']==str(PYTHON) and controller['outer_seconds']==1500
                and controller['primary_seconds']==600 and controller['posthash_seconds']==120
                and controller['cleanup_seconds']==60,'closed_actual_paths_clocks')
        require(type(controller.get('identical_key_replay')) is bool,'explicit_replay_scope')
        chosen=profile_data()
        require(controller['input_wav']==chosen['immutable_assets'][-1]['path'] and
                all(controller['settings'].get(k)==v for k,v in chosen['model_settings'].items()),
                'closed_existing_model_and_input_paths')
        rows=controller['hash_inventory'];seen={row['path']:row for row in rows}
        require(len(seen)==len(rows)<=32768,'unique_full_inventory')
        runtime=json.loads(read(Path(chosen['runtime_inventory']['path'])))
        require(runtime['file_count']==2090 and runtime['payload_bytes']==224293277,'combined_runtime_geometry')
        required=list(chosen['immutable_assets'])+chosen['immutable_host_files']+chosen['evidence_pins']
        required += [{'path':str(RUNTIME/row['destination']),'bytes':row['bytes'],'sha256':row['sha256']}
                     for row in runtime['files']]
        freeze=json.loads(read(ROOT/'SOURCE_FREEZE.json'))
        required += [{'path':str(SOURCE/name),'sha256':digest} for name,digest in freeze['product_source_files'].items()]
        required += freeze['harness_files']+freeze['metadata_files']
        require(set(controller['source_files'])=={str(SOURCE/name) for name in freeze['product_source_files']},
                'current_source_full_inventory_membership')
        for row in required:
            require(row['path'] in seen and all(seen[row['path']].get(k)==v for k,v in row.items()),
                    'full_inventory_missing_or_changed:'+row['path'])
        readiness_contract(controller,lambda pin:g.read_pin_json(pin,end))
        require(spec['argv']==[str(PYTHON),'-I','-B','-S',str(HERE/'bootstrap.py'),'--mode','controller',
                '--spec',str(ROOT/'controller.actual.json')],'closed_actual_entry')
        require(set(spec['external_files'])==common_names()|{str(ROOT/'controller.actual.json'),str(ROOT/'ACTUAL_AUTHORITY.json'),
                    controller['inventory_manifest']['path']}|{controller[key]['receipt']['path'] for key in
                    ('storage_admission','installed_runtime','posthash_probe','model_free_tests','positive_owned_probe')},
                'closed_actual_externals')
        accounting(end,max(g.RECEIPT_RESERVE,448*2**20-(original_scratch(end,[RUN]) if RUN.exists() else 0)))
        require(original_scratch(end)+g.RECEIPT_RESERVE<=g.MAX_DISK,'global_reservation')
        return controller,controller,[ROOT],RUN
    def verify_capture_posthash(self,end):
        output=Path(self.controller['output_dir'])
        value=json.loads(g.read_regular(output/'capture-result.json',2*2**20,end))
        post=json.loads(g.read_regular(output/'posthash.json',2**20,end))
        rows=self.controller['hash_inventory']
        require(value.get('status')=='COMPLETE' and value.get('posthash')==post and
                value.get('actual_native_selected') is True and value.get('one_moss_job') is True and
                post.get('all_matched') is True and post.get('source_files')==331 and
                post.get('files')==len(rows) and post.get('bytes')==sum(row['bytes'] for row in rows) and
                type(post.get('elapsed_seconds')) in (int,float) and 0<=post['elapsed_seconds']<=120,
                'mandatory_complete_full_posthash')
        self.receipt['controller_full_posthash_verified']=True
        self.receipt['controller_full_posthash']=post
    g.stage_seconds=stage_seconds;g.profile=stage_profile;g.actual_admission=actual_admission
    g.scratch_bytes=scratch_bytes;g.Guard.verify_capture_posthash=verify_capture_posthash
    require(all(getattr(g.Guard,name) is value for name,value in original.items()
                if name!='verify_capture_posthash'),'all_other_guard_methods_identical')
    return original


def ownership_ast():
    require(sha(GUARD)==GUARD_SHA and sha(OLD_GUARD)==OLD_GUARD_SHA,'guard_baseline_pins')
    node=next(n for n in ast.parse(read(GUARD)).body if isinstance(n,ast.ClassDef) and n.name=='Guard')
    return {n.name:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest()
            for n in node.body if isinstance(n,ast.FunctionDef) and n.name!='verify_capture_posthash'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--spec',type=Path,required=True)
    parser.add_argument('--spec-sha256',required=True);args=parser.parse_args()
    require(sha(GUARD)==GUARD_SHA and sha(OLD_GUARD)==OLD_GUARD_SHA and sha(ADMISSION)==ADMISSION_SHA,
            'unchanged_baseline_pins')
    require(Path(sys.executable).resolve()==PYTHON and sha(PYTHON)==PYTHON_SHA,'selected_interpreter')
    require(sha(args.spec)==args.spec_sha256,'spec_pin')
    spec=json.loads(read(args.spec));actual,*_=stage_profile(spec);profile_data()
    expected=ROOT/({'api_smoke_actual':'actual.spec.json','api_smoke_hash':'hash.spec.json',
                    'api_smoke_fixtures':'fixtures.spec.json'}[spec['stage']])
    require(args.spec==expected and spec['schema']==3 and spec['guard_sha256']==GUARD_SHA and
            spec['source_root']==str(SOURCE) and spec['cwd']==str(ROOT) and
            spec['runtime_files']=={str(PYTHON):PYTHON_SHA},'closed_outer_binding')
    freeze=json.loads(read(ROOT/'SOURCE_FREEZE.json'))
    validate_source_binding(spec,freeze)
    if spec['stage']=='api_smoke_fixtures':
        require(set(spec['external_files'])==common_names() and
                spec['argv']==[str(PYTHON),'-I','-B','-S',str(HERE/'test_smoke.py')],
                'closed_model_free_entry')
    module=importlib.util.spec_from_file_location('qualified_dev5_actual_guard',GUARD)
    guard=importlib.util.module_from_spec(module);module.loader.exec_module(guard)
    configure(guard)
    if spec['stage']=='api_smoke_hash':
        require(spec['inventory_manifest']['path']==str(ROOT/'HASH_INVENTORY.json') and
                set(spec['external_files'])==common_names()|{str(ROOT/'HASH_INVENTORY.json'),spec['fixture_terminal']['path']} and
                spec['argv']==[str(PYTHON),'-I','-B','-S',str(HERE/'capture.py'),
                    '--hash-inventory',str(ROOT/'HASH_INVENTORY.json'),
                    '--hash-inventory-sha256',spec['inventory_manifest']['sha256']], 'closed_hash_only_entry')
        terminal=guard.read_pin_json(spec['fixture_terminal'],guard.ABSOLUTE_END)
        require(terminal.get('schema')=='dev5-api-smoke-fixtures-terminal-v1' and
                terminal.get('status')=='PASS' and terminal.get('cleanup')=='CONFIRMED' and
                terminal.get('tests_run')==41 and terminal.get('actual_acoustic_calls')==0 and
                terminal.get('source_freeze_sha256')==sha(ROOT/'SOURCE_FREEZE.json') and
                terminal.get('original_tool_wait_reaped') is True,'hash_requires_exact_fixture_terminal')
        guard.read_pin_json(spec['inventory_manifest'],guard.ABSOLUTE_END)
    return guard.main()


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print(json.dumps({'status':'REFUSED_BEFORE_DISPATCH','error_type':'ApiSmokeAdapterRefused'}))
        raise SystemExit(2) from None
