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
COMPONENT=ROOT
WORKSPACE=ROOT.parent
SOURCE=WORKSPACE/'UNQUALIFIED_PRODUCT_SOURCE_ROOT'
RUNTIME=WORKSPACE/'UNQUALIFIED_RUNTIME_ROOT'
PYTHON=RUNTIME/'bin/python3.12'
PYTHON_SHA='UNQUALIFIED_PYTHON_SHA256'
GUARD=WORKSPACE/'UNQUALIFIED_GUARD_ROOT/actual_guard.py'
GUARD_SHA='UNQUALIFIED_GUARD_SHA256'
OLD_GUARD=WORKSPACE/'UNQUALIFIED_HISTORICAL_GUARD_ROOT/actual_guard.py'
OLD_GUARD_SHA='UNQUALIFIED_HISTORICAL_GUARD_SHA256'
ADMISSION=WORKSPACE/'UNQUALIFIED_STORAGE_ROOT/admission.py'
ADMISSION_SHA='UNQUALIFIED_STORAGE_ADMISSION_SHA256'
RUN=ROOT/'runs/UNQUALIFIED_ACTUAL_RUN'
STAGES={'api_smoke_fixtures':300,'api_smoke_materialize':300,'api_smoke_hash':300,
        'api_smoke_validate':300,'api_smoke_actual':1500}
SOURCES=('ownership.py','instrumentation.py','common.py','bootstrap.py','server.py','capture.py','launch.py','test_smoke.py')
META=('PROFILE.json','BASELINE_PINS.json','FIXTURE_IDS.json','SOURCE_FREEZE.json','AST_PARITY.json','README.md',
      'ASSETS.json','controller.template.json','outer.template.json','authority.template.json',
      'readiness/COMBINED_IMPORT_TERMINAL.json','readiness/ORIGINS.json',
      'RETRY_BINDINGS.json','ISOLATED_ENTRY_SUPPLEMENT.json')
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
            |{str(ROOT/'entry/isolated_entry.py'),str(ROOT/'entry/test_isolated_entry.py')}
            |{row['path'] for row in profile_data()['evidence_pins']})


def profile_data():
    value=json.loads(read(ROOT/'PROFILE.json'))
    require(value['schema']=='dev7-cell-api-profile-v1' and value['source_root']==str(SOURCE)
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


def materialize_contract(spec):
    require(spec.get('stage')=='api_smoke_materialize', 'closed_materialization_stage')
    stage_profile(spec)
    require(spec.get('argv')==[str(PYTHON),'-I','-B','-S',str(HERE/'capture.py'),'--materialize-only'] and
            spec.get('input_target')==str(ROOT/'input/primary-21s.wav') and
            spec.get('materialization_target')==str(ROOT/'input/MATERIALIZATION.json') and
            spec.get('original_bytes')==402905680 and
            spec.get('original_frames')==[23400320,23736320] and
            spec.get('channel')==0 and spec.get('actual_run_authorized') is False,
            'closed_materialization_entry')
    require(spec.get('fixture_terminal',{}).get('path')==str(ROOT/'readiness/FIXTURES_TERMINAL.json') and
            set(spec.get('external_files',{}))==common_names()|{str(ROOT/'readiness/FIXTURES_TERMINAL.json')},
            'closed_materialization_externals')


def validation_contract(spec):
    """One false-authority directory/inventory gate; never actual admission."""
    require(spec.get('stage')=='api_smoke_validate','closed_validation_stage')
    stage_profile(spec)
    require(spec.get('actual_run_authorized') is False and
            spec.get('validation_only') is True and
            set(spec.get('validation_controller',{}))=={'path','sha256'} and
            spec['validation_controller']['path']==str(ROOT/'controller.validation.json') and
            set(spec.get('validation_authority',{}))=={'path','sha256'} and
            spec['validation_authority']['path']==str(ROOT/'VALIDATION_AUTHORITY.json') and
            set(spec.get('inventory_manifest',{}))=={'path','sha256'} and
            spec['inventory_manifest']['path']==str(ROOT/'HASH_INVENTORY.json') and
            spec.get('argv')==[str(PYTHON),'-I','-B','-S',str(HERE/'bootstrap.py'),
                '--validate-only','--spec',str(ROOT/'controller.validation.json'),
                '--spec-sha256',spec['validation_controller']['sha256']],
            'closed_validation_entry')
    expected=common_names()|{str(ROOT/name) for name in ('controller.validation.json',
        'VALIDATION_AUTHORITY.json','HASH_INVENTORY.json','readiness/FIXTURES_TERMINAL.json')}
    require(set(spec.get('external_files',{}))==expected and
            spec.get('fixture_terminal',{}).get('path')==str(ROOT/'readiness/FIXTURES_TERMINAL.json'),
            'closed_validation_externals')
    require(not any(path.exists() or path.is_symlink() for path in
            (ROOT/'ACTUAL_AUTHORITY.json',ROOT/'controller.actual.json',ROOT/'actual.spec.json',RUN)),
            'validation_requires_absent_actual_targets')


def validate_source_binding(spec,freeze):
    require(spec.get('source_root')==str(SOURCE) and
            spec.get('source_files')==freeze['product_source_files'] and
            freeze['product_source_count']==346 and freeze['product_source_bytes']==4487300,
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
    require(controller['storage_admission']['receipt']['path']==str(ROOT/'readiness/RESOURCE_ADMISSION.json') and
            storage.get('schema')=='dev7-cell-api-storage-admission-v1' and
            storage.get('status')=='PROSPECTIVE_CAPACITY_RESERVED' and
            storage.get('fresh_guard_readmission_required') is True and
            storage.get('does_not_authorize_actual_execution') is True and
            storage.get('does_not_qualify_physical_store_behavior') is True and
            storage.get('global_cap_bytes')==12*2**30 and
            storage.get('ordinary_free_floor_bytes')==11*2**29 and
            storage.get('old_unknown_disk_bytes')==320*2**20 and
            storage.get('old_unknown_rss_bytes')==512*2**20 and
            storage.get('component_path')==str(ROOT) and
            storage.get('component_cap_bytes')==512*2**20 and
            storage.get('partitions')==chosen['component_partitions'] and
            type(storage.get('global_prospective_bytes')) is int and
            storage['global_prospective_bytes']<=12*2**30 and
            type(storage.get('observed_free_bytes')) is int and
            type(storage.get('required_free_bytes')) is int and
            storage['observed_free_bytes']>=storage['required_free_bytes']>=11*2**29+320*2**20,
            'prospective_storage_schema')
    imported=read_pin(controller['installed_runtime']['receipt'])
    require(controller['installed_runtime']['receipt']==chosen['runtime_import_local'] and
            imported.get('schema')=='dev5-combined-runtime-import-terminal-v1' and
            imported.get('status')=='PASS_IMPORT_ONLY' and imported.get('original_tool_wait_reaped') is True and
            imported.get('model_session_audio_inference_service_network_entries')==0,'qualified_import_only')
    fixtures=read_pin(controller['model_free_tests']['receipt'])
    require(controller['model_free_tests']['receipt']==controller['positive_owned_probe']['receipt'] and
            fixtures.get('schema')=='dev7-cell-api-fixtures-terminal-v1' and
            fixtures.get('status')=='PASS' and fixtures.get('cleanup')=='CONFIRMED' and
            fixtures.get('tests_run')==chosen['fixture_count'] and
            fixtures.get('fixture_ids_sha256')==chosen['fixture_ids_sha256'] and fixtures.get('failures')==0 and fixtures.get('errors')==0 and
            fixtures.get('actual_acoustic_calls')==0 and fixtures.get('original_tool_wait_reaped') is True and
            fixtures.get('source_freeze_sha256')==controller['source_freeze_sha256'],
            'exact_fixture_and_positive_owned_terminal_required')
    manifest=read_pin(controller['inventory_manifest'])
    require(controller['inventory_manifest']['path']==str(ROOT/'HASH_INVENTORY.json') and
            manifest.get('schema')=='dev7-cell-api-immutable-inventory-v1' and
            manifest.get('preparation_status')=='FINALIZED_INPUT_BOUND' and not manifest.get('pending_input_rows') and
            manifest.get('source_freeze_sha256')==controller['source_freeze_sha256'] and
            manifest.get('hash_inventory')==controller['hash_inventory'] and
            manifest.get('inventory_sha256')==hashlib.sha256(canonical(sorted(controller['hash_inventory'],
                key=lambda row:row['path']))).hexdigest(),'exact_immutable_inventory')
    hashed=read_pin(controller['posthash_probe']['receipt'])
    require(hashed.get('schema')=='dev7-cell-api-hash-terminal-v1' and
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
    review=read_pin(controller['preparation_review'])
    require(controller['preparation_review']['path']==str(ROOT/'readiness/INDEPENDENT_PREPARATION_REVIEW.json') and
            review.get('schema')=='dev7-cell-api-independent-preparation-review-v1' and
            review.get('status')=='ACCEPTED_SOURCE_AND_MODEL_FREE' and
            review.get('source_freeze_sha256')==controller['source_freeze_sha256'] and
            review.get('fixtures_terminal_sha256')==controller['model_free_tests']['receipt']['sha256'] and
            review.get('hash_terminal_sha256')==controller['posthash_probe']['receipt']['sha256'] and
            review.get('actual_execution_authorized') is False, 'independent_preparation_review_required')


def configure(g):
    require(g.WORKSPACE==WORKSPACE and g.MAX_DISK==12*2**30 and g.ACTUAL_RSS==4*2**30 and
            g.ACTUAL_VISIBLE_RSS==6*2**30 and g.ACTUAL_AS==8*2**30 and g.CLEANUP_RESERVE==60 and
            g.OLD_UNKNOWN_DISK==320*2**20 and g.ORDINARY_FREE_FLOOR==11*2**29 and g.POLL==.05,
            'qualified_guard_geometry')
    original={name:value for name,value in vars(g.Guard).items() if callable(value)}
    original_scratch=g.scratch_bytes
    g.CHECKS=ROOT;g.STAGES=dict(STAGES)
    def accounting(end,reserve=0):
        retained=original_scratch(end,[WORKSPACE/'UNQUALIFIED_RETAINED_FAILED_ROOT'])
        require(type(retained) is int and 0<=retained<=32*2**20,'sealed_failed_component_cap')
        total=original_scratch(end,[COMPONENT])
        # One fresh component; old roots remain in the unmodified global census.
        run_total=original_scratch(end,[ROOT/'runs']) if (ROOT/'runs').exists() else 0
        actual=original_scratch(end,[RUN]) if RUN.exists() else 0
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
                controller.get('schema')=='dev7-cell-api-controller-v1' and
                controller.get('actual_run_authorized') is True and
                controller.get('source_root')==str(SOURCE) and controller.get('source_file_count')==346 and
                controller.get('source_head')=='ebc7a02fb4420c0089f2a146ec6bf9dcc23137b4' and
                controller.get('source_tree')=='41a091d97438238c88491a4c543002b47e647c72' and
                controller.get('source_freeze_sha256')==sha(ROOT/'SOURCE_FREEZE.json'),
                'current_dev7_controller_required')
        approval=g.read_pin_json(controller['approval_receipt'],end)
        require(controller['approval_receipt']['path']==str(ROOT/'ACTUAL_AUTHORITY.json') and
                approval.get('schema')=='dev7-cell-api-authority-v1' and
                approval.get('actual_capture_approved') is True and
                approval.get('scope')=='one-genuine-21s-primary-and-one-durable-cell-moss-job' and
                approval.get('source_head')==controller['source_head'] and
                approval.get('source_freeze_sha256')==controller['source_freeze_sha256'] and
                type(approval.get('authorization_reference')) is str and
                0<len(approval['authorization_reference'])<=2048,'separate_exact_root_GO')
        require(controller['output_dir']==str(RUN/'tmp/smoke') and controller['data_dir']==str(RUN/'tmp/smoke/data')
                and controller['python']==str(PYTHON) and controller['outer_seconds']==1500
                and controller['primary_seconds']==600 and controller['posthash_seconds']==120
                and controller['cleanup_seconds']==60,'closed_actual_paths_clocks')
        require(controller.get('identical_key_replay') is True,'explicit_replay_scope')
        chosen=profile_data()
        require(controller['input_wav']==str(ROOT/'input/primary-21s.wav') and
                all(controller['settings'].get(k)==v for k,v in chosen['model_settings'].items()),
                'closed_existing_model_and_input_paths')
        from common import input_pins, SOURCE_INDEX_SHA, validate_input_pins
        require(controller.get('source_index_sha256')==SOURCE_INDEX_SHA and
                validate_input_pins(controller.get('input_pins'))==input_pins(), 'materialized_input_required')
        material=controller['materialization_receipt']
        require(material['path']==str(ROOT/'input/MATERIALIZATION.json'), 'current_input_materialization_path')
        materialized=g.read_pin_json(material,end)
        require(materialized.get('input_pins')==controller['input_pins'], 'materialization_receipt_pin_agreement')
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
        required += [{'path':controller['input_wav'],'bytes':672044,'sha256':controller['input_pins']['wav_sha256']},
                     {'path':material['path'],'sha256':material['sha256']}]
        require(set(controller['source_files'])=={str(SOURCE/name) for name in freeze['product_source_files']},
                'current_source_full_inventory_membership')
        for row in required:
            require(row['path'] in seen and all(seen[row['path']].get(k)==v for k,v in row.items()),
                    'full_inventory_missing_or_changed:'+row['path'])
        readiness_contract(controller,lambda pin:g.read_pin_json(pin,end))
        require(spec['argv']==[str(PYTHON),'-I','-B','-S',str(HERE/'bootstrap.py'),'--mode','controller',
                '--spec',str(ROOT/'controller.actual.json')],'closed_actual_entry')
        require(set(spec['external_files'])==common_names()|{str(ROOT/'controller.actual.json'),str(ROOT/'ACTUAL_AUTHORITY.json'),
                    controller['inventory_manifest']['path'],material['path'],controller['preparation_review']['path']}|{controller[key]['receipt']['path'] for key in
                    ('storage_admission','installed_runtime','posthash_probe','model_free_tests','positive_owned_probe')},
                'closed_actual_externals')
        accounting(end,max(g.RECEIPT_RESERVE,448*2**20-sum(
            original_scratch(end,[path]) if path.exists() else 0 for path in (RUN,))))
        require(original_scratch(end)+g.RECEIPT_RESERVE<=g.MAX_DISK,'global_reservation')
        return controller,controller,[COMPONENT],RUN
    def verify_capture_posthash(self,end):
        output=Path(self.controller['output_dir'])
        value=json.loads(g.read_regular(output/'capture-result.json',2*2**20,end))
        post=json.loads(g.read_regular(output/'posthash.json',2**20,end))
        rows=self.controller['hash_inventory']
        require(value.get('status')=='COMPLETE' and value.get('posthash')==post and
                value.get('actual_native_selected') is True and value.get('one_moss_job') is True and
                value.get('genuine_durable_cell_gate') is True and
                value.get('committed_core_frames')==[0,336000] and
                value.get('selected_cell_frames')==[0,320000] and
                value.get('retained_restart_verified') is True and
                post.get('all_matched') is True and post.get('source_files')==346 and
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
                    'api_smoke_fixtures':'fixtures.spec.json','api_smoke_materialize':'materialize.spec.json',
                    'api_smoke_validate':'validation.spec.json'}[spec['stage']])
    require(args.spec==expected and spec['schema']==3 and spec['guard_sha256']==GUARD_SHA and
            spec['source_root']==str(SOURCE) and spec['cwd']==str(ROOT) and
            spec['runtime_files']=={str(PYTHON):PYTHON_SHA},'closed_outer_binding')
    freeze=json.loads(read(ROOT/'SOURCE_FREEZE.json'))
    validate_source_binding(spec,freeze)
    if spec['stage']=='api_smoke_fixtures':
        require(set(spec['external_files'])==common_names() and
                spec['argv']==[str(PYTHON),'-I','-B','-S',str(HERE/'test_smoke.py')],
                'closed_model_free_entry')
    module=importlib.util.spec_from_file_location('qualified_dev7_actual_guard',GUARD)
    guard=importlib.util.module_from_spec(module);module.loader.exec_module(guard)
    configure(guard)
    if spec['stage']=='api_smoke_validate':
        validation_contract(spec)
        terminal=guard.read_pin_json(spec['fixture_terminal'],guard.ABSOLUTE_END)
        chosen=profile_data()
        require(terminal.get('schema')=='dev7-cell-api-fixtures-terminal-v1' and
                terminal.get('status')=='PASS' and terminal.get('cleanup')=='CONFIRMED' and
                terminal.get('tests_run')==chosen['fixture_count'] and
                terminal.get('fixture_ids_sha256')==chosen['fixture_ids_sha256'] and
                terminal.get('failures')==terminal.get('errors')==terminal.get('actual_acoustic_calls')==0 and
                terminal.get('source_freeze_sha256')==sha(ROOT/'SOURCE_FREEZE.json') and
                terminal.get('original_tool_wait_reaped') is True,'validation_requires_fixture_terminal')
        authority=guard.read_pin_json(spec['validation_authority'],guard.ABSOLUTE_END)
        controller=guard.read_pin_json(spec['validation_controller'],guard.ABSOLUTE_END)
        require(authority.get('schema')=='dev7-cell-api-validation-authority-v1' and
                authority.get('validation_approved') is True and authority.get('actual_capture_approved') is False and
                authority.get('scope')=='shared-controller-directory-and-full-inventory-only' and
                authority.get('source_freeze_sha256')==sha(ROOT/'SOURCE_FREEZE.json') and
                controller.get('schema')=='dev7-cell-api-validation-controller-v1' and
                controller.get('validation_only') is True and controller.get('actual_run_authorized') is False and
                controller.get('validation_authority')==spec['validation_authority'] and
                controller.get('inventory_manifest')==spec['inventory_manifest'],
                'false_actual_validation_authority_required')
    if spec['stage']=='api_smoke_materialize':
        materialize_contract(spec)
        terminal=guard.read_pin_json(spec['fixture_terminal'],guard.ABSOLUTE_END)
        chosen=profile_data()
        require(terminal.get('schema')=='dev7-cell-api-fixtures-terminal-v1' and
                terminal.get('status')=='PASS' and terminal.get('cleanup')=='CONFIRMED' and
                terminal.get('tests_run')==chosen['fixture_count'] and
                terminal.get('fixture_ids_sha256')==chosen['fixture_ids_sha256'] and
                terminal.get('failures')==terminal.get('errors')==terminal.get('actual_acoustic_calls')==0 and
                terminal.get('source_freeze_sha256')==sha(ROOT/'SOURCE_FREEZE.json') and
                terminal.get('original_tool_wait_reaped') is True, 'materialization_requires_exact_fixture_terminal')
    if spec['stage']=='api_smoke_hash':
        require(spec['inventory_manifest']['path']==str(ROOT/'HASH_INVENTORY.json') and
                set(spec['external_files'])==common_names()|{str(ROOT/'HASH_INVENTORY.json'),spec['fixture_terminal']['path']} and
                spec['argv']==[str(PYTHON),'-I','-B','-S',str(HERE/'capture.py'),
                    '--hash-inventory',str(ROOT/'HASH_INVENTORY.json'),
                    '--hash-inventory-sha256',spec['inventory_manifest']['sha256']], 'closed_hash_only_entry')
        terminal=guard.read_pin_json(spec['fixture_terminal'],guard.ABSOLUTE_END)
        require(terminal.get('schema')=='dev7-cell-api-fixtures-terminal-v1' and
                terminal.get('status')=='PASS' and terminal.get('cleanup')=='CONFIRMED' and
                terminal.get('tests_run')==profile_data()['fixture_count'] and
                terminal.get('fixture_ids_sha256')==profile_data()['fixture_ids_sha256'] and terminal.get('actual_acoustic_calls')==0 and
                terminal.get('source_freeze_sha256')==sha(ROOT/'SOURCE_FREEZE.json') and
                terminal.get('original_tool_wait_reaped') is True,'hash_requires_exact_fixture_terminal')
        manifest=guard.read_pin_json(spec['inventory_manifest'],guard.ABSOLUTE_END)
        require(manifest.get('preparation_status')=='FINALIZED_INPUT_BOUND' and
                not manifest.get('pending_input_rows') and
                any(row.get('path')==str(ROOT/'input/primary-21s.wav') and row.get('bytes')==672044
                    for row in manifest.get('hash_inventory',[])), 'hash_requires_materialized_input_inventory')
    return guard.main()


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print(json.dumps({'status':'REFUSED_BEFORE_DISPATCH','error_type':'ApiSmokeAdapterRefused'}))
        raise SystemExit(2) from None
