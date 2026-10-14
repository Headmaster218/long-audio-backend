# Unexecuted portable template; generate every configuration placeholder before use.
"""Strict bounded configuration and execution-gate validation. No provider imports.

All metadata is bounded, duplicate-rejecting JSON. Missing evidence remains pending;
readiness is never inferred from a path, an old PASS label, or materialized model bytes.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import stat
from common import (MIB, PHASES, SOURCE_HEAD, SOURCE_WAV_SHA, ROLE_FRAMES,
                    Refused, bounded_read, canonical, require, sha, validate_input_pins)

SCHEMA = 'fresh-seam-config-v1'
AUTHORITY_SCHEMA = 'fresh-seam-actual-authority-v1'
WITNESS_SCHEMA = 'fresh-seam-cell-witness-v1'
SCOPE = 'one-genuine-two-upload-seam-and-one-composite-moss-job'
MOSS_SHA = '64ec654dc6ffcfdfe180422dffce1d33422b0c30959b7edfd131bad77ee35039'
IMPORT_SHA = '@GENERATED_RUNTIME_IMPORT_SHA256@'
MODEL_SHAS = {
    'audioset':'@GENERATED_AUDIOSET_MATERIALIZATION_SHA256@',
    'sensevoice':'@GENERATED_SENSEVOICE_MATERIALIZATION_SHA256@',
    'native':'@GENERATED_NATIVE_MATERIALIZATION_SHA256@'}
SOURCE_REL = '@GENERATED_PRODUCT_ROOT_REL@'
RUNTIME_REL = '@GENERATED_RUNTIME_ROOT_REL@'
HARNESS_REL = '@GENERATED_PACKET_ROOT_REL@/controller'
BASE_HARNESS_REL = '@GENERATED_BASE_PACKET_ROOT_REL@/controller'
INPUT_REL = '@GENERATED_INPUT_WAV_REL@'
NATIVE_INSTALL_REL = '@GENERATED_ASSETS_ROOT_REL@/vendor/native-diarization/linux-x86_64-cpu/installation.json'
HARNESS_CAPS = {'ownership.py':10*1024,'instrumentation.py':17*1024,'admission.py':6*1024,
    'common.py':26*1024,'bootstrap.py':72*1024,'server.py':70*1024,'capture.py':100*1024,
    'launch.py':28*1024,'binding.py':40*1024,'test_seam.py':140*1024,'test_binding.py':28*1024}
LIMITS = {'outer_seconds':1800,'primary_seconds':900,'posthash_seconds':120,'cleanup_seconds':60,
    'cpus':[0,1],'address_space_bytes':8*2**30,'aggregate_sampled_rss_bytes':4*2**30,
    'incremental_disk_bytes':544*MIB,'nice':19,'sampled_not_hard_quota':True}
COUNTERS = {'PUT_requests':7,'fresh_receipts':2,'nonempty_claims':3,'native_RPCs':6,
    'ABI_push_blocks':[32000,32000,32000,32000,32000,8000]*2,'analyzer_children':3,
    'analyzes':2,'true_commits':2,'decode_probe_children':9,'ffprobe':3,'ffmpeg':6,
    'retained_cells_min':1,'retained_cells_max':3,'MOSS_POSTs':5,'accepted_MOSS_jobs':1,
    'MOSS_children':1,'serial_server_lifetimes':3,'restart_providers':0,'grouped_DELETE':1,
    'SDK_max':{'SenseVoice':64,'each_model_constructor':2,'embedding':16,'event':8,'streams':88}}
SETTINGS = {'asr_backend':'sensevoice','diarization_backend':'nemotron','inference_device':'cpu',
    'cpu_threads':2,'native_diarization_threads':2,'overlap_backend':'none','speaker_support_enabled':False,
    'native_owned_support_enabled':True,'native_owned_cells_enabled':True,'native_owned_seam_cells_enabled':True,
    'moss_jobs_enabled':True,'start_worker':True,'language':'auto','offline':True,'window_seconds':30.,
    'context_seconds':2.,'max_upload_bytes':MIB,'max_sessions':1,'max_pending_chunks':2,
    'max_audio_seconds':21.,'process_memory_limit_bytes':4*2**30,'disk_reserve_bytes':0}
PATH_KEYS = {'source_root','runtime_root','harness_root','input_wav','upload_a','upload_b','output_dir',
    'python','site_packages','stdlib','dynload','model_dir','native_model','native_library',
    'native_installation','moss_model','moss_native_library','speaker_model','ffmpeg','ffprobe'}
PREREQUISITES = {'cell_store','decoder','split','fixtures','hash_inventory','outer_guard'}
FIELDS = {'schema','workspace_root','paths','product_commit','input_descriptor','runtime_import',
    'model_acceptance','moss_sha256','limits','phases','counters','settings','input_pins',
    'inventory','prerequisites','witness_schema','fixture_source_pins','diagnostic_derivation'}


def exact(value, expected, reason):
    require(canonical(value) == canonical(expected), reason)


def digest(value):
    require(type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value),
            'sha256_shape')
    return value


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_JSON_key:' + key)
        result[key] = value
    return result


def decode(raw):
    require(type(raw) is bytes and len(raw) <= 4*MIB, 'config_byte_cap')
    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs,
                           parse_constant=lambda x: (_ for _ in ()).throw(Refused('nonfinite_JSON')))
    except (ValueError, UnicodeError) as exc:
        raise Refused('invalid_JSON') from exc
    require(type(value) is dict, 'metadata_object_required')
    return value


def path_text(value, *, absolute=True):
    # Reject duplicate/ambiguous spellings BEFORE Path() can normalize them.
    require(type(value) is str and 0 < len(value.encode()) <= 4096 and '\0' not in value and
            '\\' not in value and value.startswith('/') == absolute, 'path_type_or_root')
    parts = value.split('/')[1:] if absolute else value.split('/')
    require(parts and all(p not in ('','.','..') for p in parts), 'noncanonical_path_spelling')
    require(str(Path(value)) == value, 'normalized_path_forbidden')
    return value


def ordinary_path(path, check=lambda:None, *, exists=True):
    path_text(str(path)); check(); path = Path(path)
    require(path.resolve(strict=exists) == path, 'path_alias')
    if exists:
        st = os.lstat(path)
        require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, 'ordinary_single_link_file_required')
    return path


def pin(value, *, pending=False):
    if pending and value is None:
        return None
    require(type(value) is dict and set(value) == {'path','bytes','sha256'}, 'pin_exact_fields')
    path_text(value['path']); digest(value['sha256'])
    require(type(value['bytes']) is int and 0 <= value['bytes'] <= 8*2**30, 'pin_byte_range')
    return value


def read_pin(value, check=lambda:None, maximum=4*MIB):
    pin(value); require(value['bytes'] <= maximum, 'metadata_pin_cap')
    path = ordinary_path(value['path'], check)
    raw = bounded_read(path, maximum, check)
    require(len(raw) == value['bytes'] and sha(raw) == value['sha256'], 'metadata_pin_mismatch')
    return decode(raw)


def path_map(value, *, maximum=32768):
    require(type(value) is dict and 0 < len(value) <= maximum, 'path_map_count')
    result = {}
    for name, record in value.items():
        path_text(name)
        require(name not in result and type(record) is dict and set(record) == {'bytes','sha256'},
                'duplicate_or_invalid_path_map')
        pin({'path':name,**record}); result[name] = dict(record)
    return result


def rows_map(rows):
    require(type(rows) is list and 0 < len(rows) <= 32768, 'inventory_rows_count')
    result = {}
    for row in rows:
        pin(row)
        require(row['path'] not in result, 'duplicate_inventory_path')
        result[row['path']] = {'bytes':row['bytes'],'sha256':row['sha256']}
    return result


def validate_config(config, *, ready=False):
    require(type(config) is dict and set(config) == FIELDS and config['schema'] == SCHEMA, 'strict_config_schema')
    root = Path(path_text(config['workspace_root']))
    paths = config['paths']; require(type(paths) is dict and set(paths) == PATH_KEYS, 'strict_path_fields')
    for value in paths.values():
        if value is not None: path_text(value)
    expected = {'source_root':SOURCE_REL,'runtime_root':RUNTIME_REL,'harness_root':HARNESS_REL,
        'input_wav':INPUT_REL,'native_installation':NATIVE_INSTALL_REL,
        'python':RUNTIME_REL+'/bin/python3.12','site_packages':RUNTIME_REL+'/lib/python3.12/site-packages',
        'model_dir':'@GENERATED_ASSETS_ROOT_REL@/models',
        'speaker_model':'@GENERATED_ASSETS_ROOT_REL@/models/speaker.onnx',
        'moss_model':'@GENERATED_ASSETS_ROOT_REL@/models/MOSS-Transcribe-Diarize-Q8_0.gguf',
        'native_model':'@GENERATED_ASSETS_ROOT_REL@/models/native-diarization/Nemotron-3-Diarization.q8_0.gguf'}
    for key, rel in expected.items():
        require(paths[key] == str(root/rel), 'wrong_current_path:' + key)
    component = root/HARNESS_REL; component = component.parent
    input_component = root/BASE_HARNESS_REL; input_component = input_component.parent
    require(paths['upload_a'] == str(input_component/'input/upload-a-10p5s.wav') and
            paths['upload_b'] == str(input_component/'input/upload-b-10p5s.wav'), 'exact_two_upload_paths')
    if paths['output_dir'] is not None:
        output = Path(paths['output_dir'])
        require(output.parent == component/'runs' and output.name.startswith('run-') and
                len(output.name) == 12 and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in output.name[4:]),
                'fresh_run_path_shape')
    require(config['product_commit'] == SOURCE_HEAD, 'wrong_product_commit')
    exact(config['input_descriptor'], {'bytes':672044,'sha256':SOURCE_WAV_SHA,'frames':336000,
        'rate':16000,'channels':1,'sample_bytes':2,'coordinates':'source-WAV-local'}, 'wrong_input_binding')
    require(config['witness_schema'] == WITNESS_SCHEMA and config['moss_sha256'] == MOSS_SHA,
            'wrong_witness_or_MOSS_binding')
    for name, expected_value in [('limits',LIMITS),('counters',COUNTERS),('settings',SETTINGS),
            ('phases',{k:list(v) for k,v in PHASES.items()})]:
        exact(config[name], expected_value, 'closed_' + name)
    imported = pin(config['runtime_import'])
    require(imported == {'path':str(root/'@GENERATED_RUNTIME_IMPORT_RECORD_REL@'),
                        'bytes':int('@GENERATED_RUNTIME_IMPORT_BYTES@'),'sha256':IMPORT_SHA}, 'wrong_independent_import_review')
    require(type(config['model_acceptance']) is dict and set(config['model_acceptance']) == set(MODEL_SHAS),
            'model_acceptance_keys')
    for name, expected_sha in MODEL_SHAS.items():
        ref = pin(config['model_acceptance'][name])
        require(ref['path'] == str(root/f'@GENERATED_MODEL_RECORDS_ROOT_REL@/{name}/OWNER_ACCEPTANCE.json') and
                ref['sha256'] == expected_sha, 'wrong_model_acceptance:' + name)
    require(type(config['fixture_source_pins']) is dict and set(config['fixture_source_pins']) == {
        'audio_backend/boundaries.py','audio_backend/event_candidate_store.py','audio_backend/worker.py',
        'audio_backend/native_owned_cells.py'}, 'fixture_source_namespace')
    for relative, value in config['fixture_source_pins'].items(): path_text(relative,absolute=False); digest(value)
    if config['input_pins'] is not None: validate_input_pins(config['input_pins'])
    require(type(config['prerequisites']) is dict and set(config['prerequisites']) == PREREQUISITES,
            'prerequisite_namespace')
    for value in config['prerequisites'].values(): pin(value,pending=True)
    inventory = config['inventory']
    if inventory is not None:
        require(type(inventory) is dict and set(inventory) == {'installed_files','runtime_files','harness_files',
            'source_files','hash_inventory','runtime_aliases','directory_aliases','native_runtime_hashes'}, 'inventory_fields')
        maps = {key:path_map(inventory[key],maximum=16384 if key=='installed_files' else 4096)
                for key in ('installed_files','runtime_files','harness_files')}
        require(not (set(maps['installed_files']) & set(maps['runtime_files'])) and
                not (set(maps['harness_files']) & (set(maps['installed_files']) | set(maps['runtime_files']))),
                'inventory_map_namespaces_overlap')
        allrows = rows_map(inventory['hash_inventory'])
        for mapping in maps.values():
            require(all(allrows.get(name) == value for name,value in mapping.items()), 'inventory_map_conflict')
        sources = inventory['source_files']
        require(type(sources) is list and len(sources) == 351 and len(set(sources)) == len(sources), 'source_file_set')
        for name in sources:
            path_text(name); require(Path(name).is_relative_to(Path(paths['source_root'])) and name in allrows,
                                     'source_inventory_missing_or_outside')
        require(set(maps['harness_files']) == {str(Path(paths['harness_root'])/n) for n in HARNESS_CAPS},
                'exact_harness_inventory')
        aliases = inventory['runtime_aliases']; require(type(aliases) is list and len(aliases) <= 4096, 'runtime_alias_cap')
        seen = set()
        for alias in aliases:
            require(type(alias) is dict and set(alias) == {'path','literal_target','resolved_target'}, 'alias_fields')
            path_text(alias['path']); path_text(alias['resolved_target'])
            require(alias['path'] not in seen and alias['path'] not in allrows and alias['resolved_target'] in allrows and
                    type(alias['literal_target']) is str and 0<len(alias['literal_target'])<=4096, 'alias_duplicate_or_target')
            seen.add(alias['path'])
        require(type(inventory['native_runtime_hashes']) is dict, 'native_runtime_hashes_shape')
        for name,value in inventory['native_runtime_hashes'].items():
            path_text(name,absolute=False); require('/' not in name,'native_companion_basename'); digest(value)
        exact(inventory['directory_aliases'], [{'path':'/lib','literal_target':'usr/lib','normalized_target':'/usr/lib'},
            {'path':'/lib64','literal_target':'usr/lib64','normalized_target':'/usr/lib64'}], 'directory_alias_contract')
    require(paths['ffmpeg']=='/usr/bin/ffmpeg' and paths['ffprobe']=='/usr/bin/ffprobe','fixed_decoder_paths')
    pin(config['diagnostic_derivation'],pending=True)
    if ready:
        require(config['diagnostic_derivation'] is not None,'diagnostic_derivation_required')
        require(all(value is not None for value in paths.values()) and config['input_pins'] is not None and
                inventory is not None and all(config['prerequisites'].values()), 'pending_fresh_prerequisite')
    return config


def validate_authority(authority, config_raw, config):
    require(type(authority) is dict and set(authority) == {'schema','authorized','config_sha256','scope','run_directory'},
            'actual_authority_required')
    require(authority['schema'] == AUTHORITY_SCHEMA and authority['authorized'] is True,
            'actual_authority_absent_or_false')
    require(authority['config_sha256'] == sha(config_raw) and authority['scope'] == SCOPE and
            authority['run_directory'] == config['paths']['output_dir'] and authority['run_directory'] is not None,
            'actual_authority_binding')


def verify_prerequisites(config, config_raw, check):
    """Current receipt slots have exact fresh schemas; no synthetic historical PASS."""
    imported = read_pin(config['runtime_import'],check)
    require(imported.get('schema') == 'independent-runtime-import-review-v1' and
            imported.get('status') == 'ACCEPTED_IMPORT_ONLY_WITH_SEPARATE_FAILED_OUTER_GUARD' and
            imported.get('raw_guard',{}).get('status') == 'FAIL_CLOSED' and
            imported.get('execution_authorized') is False, 'import_only_review_scope')
    for name, ref in config['model_acceptance'].items():
        value = read_pin(ref,check)
        require(value.get('schema') == 'owner-selected-model-acceptance-v1' and
                value.get('status') == 'ACCEPTED_MATERIALIZATION_ONLY' and value.get('artifact') == name and
                value.get('cleanup') == 'CONFIRMED' and value.get('inference_authorized') is False,
                'materialization_only_review_scope')
        result_ref={k:value['result'][k] for k in ('path','bytes','sha256')}
        result=read_pin(result_ref,check,65536)
        require(result.get('schema')=='fixed-selected-model-result-v1' and result.get('artifact')==name and
            result.get('status')=='MATERIALIZED_PENDING_OWNER_ACCEPTANCE' and result.get('model_loads')==0 and
            result.get('model_calls')==0 and result.get('native_runtime_qualified') is False,'selected_result_scope')
        output_rows=result.get('outputs');require(type(output_rows) is list and
            len(output_rows)==(90 if name=='native' else 2),'selected_output_count')
        inventory=rows_map(config['inventory']['hash_inventory']);seen=set()
        for row in output_rows:
            relative=path_text(row['path'],absolute=False)
            target=str(Path(config['workspace_root'])/'@GENERATED_ASSETS_ROOT_REL@'/relative)
            require(target not in seen,'selected_output_duplicate');seen.add(target)
            require(inventory.get(target)=={k:row[k] for k in ('bytes','sha256')},'accepted_selected_output_pin')
        if name=='native':
            installation_path=config['paths']['native_installation']
            require(installation_path in seen,'native_installation_not_accepted')
            install=read_pin({'path':installation_path,**inventory[installation_path]},check,32768)
            expected=install.get('settings',{})
            require(expected.get('native_diarization_model')==config['paths']['native_model'] and
                expected.get('native_diarization_library')==config['paths']['native_library'] and
                expected.get('native_diarization_threads')==2 and
                install.get('materialization')=='DATA_ONLY_NO_MODEL_LOAD_OR_NATIVE_QUALIFICATION',
                'accepted_native_installation_settings')
            libdir=Path(config['paths']['native_library']).parent
            companions={Path(path).name:row['sha256'] for path,row in inventory.items()
                if Path(path).parent==libdir and any(x in Path(path).name.lower() for x in ('nemo_speech_asr','ggml','llama'))}
            require(config['inventory']['native_runtime_hashes']==companions and companions,
                    'native_companion_closed_inventory')
    from launch import verify_physical_prerequisite, verify_gate_receipt, verify_live_owner
    for kind in ('cell_store','decoder'):
        verify_physical_prerequisite(config,kind,check)
    for stage in ('split','fixtures','hash_inventory'):
        verify_gate_receipt(config,stage,check)
    outer=read_pin(config['prerequisites']['outer_guard'],check)
    require(set(outer)=={'schema','status','subject_sha256','owner_source','limits','phases',
            'original_handle_retention','pidfd_identity_checks','cleanup_required'} and
        outer['schema']=='fresh-seam-outer-contract-v1' and outer['status']=='REVIEWED_DORMANT' and
        outer['subject_sha256']==subject_digest(config) and outer['original_handle_retention'] is True and
        outer['pidfd_identity_checks'] is True and outer['cleanup_required']=='CONFIRMED','outer_contract')
    exact(outer['limits'],LIMITS,'outer_limits');exact(outer['phases'],config['phases'],'outer_phases')
    read_source_pin(outer['owner_source'],check)
    verify_live_owner(config,config_raw,outer['owner_source'],check)



def subject_digest(config):
    """Acyclic current-data binding. The execution gate pins the entire final config."""
    value={k:config[k] for k in FIELDS-{'prerequisites','inventory','input_pins','diagnostic_derivation'}}
    value['paths']=dict(value['paths'],output_dir=None,
        harness_root=str(Path(config['workspace_root'])/BASE_HARNESS_REL))
    return sha(canonical(value))


def read_source_pin(ref,check):
    pin(ref);ordinary_path(ref['path'],check)
    require(ref['bytes']<=128*1024,'prerequisite_source_cap')
    raw=bounded_read(ref['path'],128*1024,check)
    require(len(raw)==ref['bytes'] and sha(raw)==ref['sha256'],'prerequisite_source_pin')



def verify_fixture_invocation(ids):
    import resource,time,sys
    config_path=os.environ['FRESH_SEAM_CONFIG']
    raw,config=load_config(config_path,os.environ['FRESH_SEAM_CONFIG_SHA256'])
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and
        Path(sys.executable).resolve()==Path(config['paths']['python']),'fixture_isolated_current_interpreter')
    authority=os.environ.get('FRESH_SEAM_FIXTURE_AUTHORITY');require(authority is not None,'fixture_authority_required')
    ordinary_path(authority);araw=bounded_read(authority,16384)
    require(sha(araw)==os.environ['FRESH_SEAM_FIXTURE_AUTHORITY_SHA256'],'fixture_authority_pin')
    value=decode(araw)
    require(set(value)=={'schema','authorized','config_sha256','fixture_ids_sha256','run_directory','original',
            'harness_files'} and value['schema']=='fresh-seam-model-free-authority-v1' and value['authorized'] is True and
        value['config_sha256']==sha(raw) and value['fixture_ids_sha256']==sha(canonical(ids)) and
        value['run_directory']==os.environ['GUARD_RUN_DIRECTORY'],'fixture_exact_authority')
    run=Path(path_text(value['run_directory']))
    require(run.parent==Path(config['paths']['harness_root']).parent/'runs' and run.resolve()==run and
        os.environ['GUARD_STOP_PATH']==str(run/'STOP') and not Path(os.environ['GUARD_STOP_PATH']).exists(),
        'fixture_run_scope')
    work=float(os.environ['GUARD_WORK_END']);end=float(os.environ['GUARD_ABSOLUTE_END'])
    require(work==end-60 and 0<end-time.monotonic()<=300 and os.sched_getaffinity(0)=={3} and
        resource.getrlimit(resource.RLIMIT_AS)==(2**30,2**30) and os.getpriority(os.PRIO_PROCESS,0)==19,'fixture_limits')
    from ownership import proc,key
    require(key(proc(os.getpid()))==key(value['original']),'fixture_original_process_identity')
    verify_harness({'harness_root':config['paths']['harness_root'],'harness_files':path_map(value['harness_files'])},lambda:None)

def project_spec(config, config_raw, path):
    """Pure projection for retained controller logic after all actual gates."""
    validate_config(config,ready=True)
    paths=config['paths']; inventory=config['inventory']; rows=rows_map(inventory['hash_inventory'])
    settings=dict(config['settings'],data_dir=str(Path(paths['output_dir'])/'data'),
        model_dir=paths['model_dir'],native_diarization_model=paths['native_model'],
        native_diarization_library=paths['native_library'],moss_jobs_model_path=paths['moss_model'],
        ffmpeg=paths['ffmpeg'],ffprobe=paths['ffprobe'],api_key=os.environ.get('FRESH_SEAM_BEARER'))
    require(type(settings['api_key']) is str and 16<=len(settings['api_key'])<=128,'fresh_bearer_required')
    for key in ('speaker_model','native_model','native_library','moss_model','moss_native_library','ffmpeg','ffprobe'):
        require(paths[key] in rows,'model_or_program_missing_inventory:' + key)
    require(rows[paths['moss_model']]['sha256']==MOSS_SHA,'wrong_official_MOSS_pin')
    require(rows[paths['speaker_model']]['sha256']=='1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b' and
        rows[paths['native_model']]['sha256']=='08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1',
        'wrong_speaker_or_native_model_pin')
    require(paths['ffmpeg']!=paths['ffprobe'],'decoder_paths_distinct')
    result=dict(inventory,**{key:paths[key] for key in ('source_root','harness_root','python','site_packages',
        'stdlib','dynload','input_wav','output_dir','moss_native_library')},
        data_dir=settings['data_dir'],settings=settings,input_pins=config['input_pins'],
        input_uploads={'a':paths['upload_a'],'b':paths['upload_b']},cpus=config['limits']['cpus'],
        source_file_count=len(inventory['source_files']),source_file_bytes=sum(rows[n]['bytes'] for n in inventory['source_files']),
        speaker_model_sha256=rows[paths['speaker_model']]['sha256'],native_library_sha256=rows[paths['native_library']]['sha256'],
        allowed_programs=[paths['ffmpeg'],paths['ffprobe']],identical_key_replay=True,
        storage_admission={'helper':{'path':str(Path(paths['harness_root'])/'admission.py'),
            **inventory['harness_files'][str(Path(paths['harness_root'])/'admission.py')]}},
        _sha256=sha(config_raw),_path=str(path))
    return result


def load_config(path, expected, check=lambda:None, *, ready=False):
    digest(expected); ordinary_path(path,check)
    raw=bounded_read(path,4*MIB,check); require(sha(raw)==expected,'config_pin_changed')
    value=decode(raw); validate_config(value,ready=ready)
    return raw,value


def load_actual(path, check=lambda:None):
    require(str(path)==os.environ.get('FRESH_SEAM_CONFIG'),'actual_exact_config_path')
    raw,config=load_config(path,os.environ.get('FRESH_SEAM_CONFIG_SHA256'),check,ready=True)
    authority_path=os.environ.get('FRESH_SEAM_AUTHORITY')
    require(authority_path is not None,'actual_authority_required')
    ordinary_path(authority_path,check)
    authority_raw=bounded_read(authority_path,4096,check)
    require(sha(authority_raw)==os.environ.get('FRESH_SEAM_AUTHORITY_SHA256'),'authority_pin_changed')
    validate_authority(decode(authority_raw),raw,config)
    from launch import verify_derivation
    verify_derivation(config,check)
    verify_prerequisites(config,raw,check)
    return project_spec(config,raw,path)


def verify_harness(spec, check):
    root=Path(spec['harness_root']); require(root==Path(__file__).resolve().parent,'harness_origin_mismatch')
    require({p.name for p in root.iterdir()}==set(HARNESS_CAPS),'harness_exact_directory_namespace')
    total=0
    for name,maximum in HARNESS_CAPS.items():
        path=ordinary_path(root/name,check); raw=bounded_read(path,maximum,check)
        require(spec['harness_files'].get(str(path))=={'bytes':len(raw),'sha256':sha(raw)},'harness_file_pin')
        total+=len(raw)
    require(total<=640*1024,'harness_aggregate_cap')
    return {'directory':str(root),'files':len(HARNESS_CAPS),'bytes':total,'all_pins_matched':True}


def publishable(value, cleanup, failures, producer_error):
    require(value.get('status')=='PROVISIONAL_CAPTURE_COMPLETE' and
            value.get('posthash',{}).get('all_matched') is True and cleanup.get('cleanup')=='CONFIRMED' and
            not cleanup.get('remaining_owned') and not failures and producer_error is None,
            'incomplete_cleanup_cannot_publish_success')
    return True


def profile_contract():
    return {'counters':COUNTERS,'revocation':{'old_completed_job_postdelete_raw_result_actual':False,
            'actual_final_public_session_DELETE':1}}


def fixture_source():
    path=os.environ.get('FRESH_SEAM_CONFIG'); require(path is not None,'fixture_config_required')
    raw,config=load_config(path,os.environ.get('FRESH_SEAM_CONFIG_SHA256'))
    return Path(config['paths']['source_root']),config['fixture_source_pins']
