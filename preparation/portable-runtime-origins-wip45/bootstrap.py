#!/usr/bin/env python3
"""Guarded phase source derived from immutable portable-recovery-bootstrap.
Private GO/config/evidence must be reviewed separately; no process adoption.
"""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

HERE=Path(__file__).resolve().parent
MIB=2**20
GIB=2**30
PYTHON_PIN={'bytes':30894944,'sha256':'fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7'}
PRODUCT={'commit':'1fc066910ca78b2e57d7a28f79b91cafc06f5dd5','tree':'6ca0c2b0e045c5ea8fdb9449bbaf6172d251b527','file_count':351,'bytes':4651663}
CAPS={'acquisition':7*GIB,'runtime':512*MIB,'proof':512*MIB,'actual':544*MIB,'source':64*MIB,'evidence':64*MIB}
LIMITS={'global_disk_bytes':12*GIB,'category_bytes':CAPS,'ordinary_free_floor_bytes':11*2**29,
 'minimum_available_bytes':2952790016,'rss_bytes':256*MIB,'address_space_bytes':GIB,
 'cpu':3,'nice':19,'acquisition_absolute_seconds':10800,'cleanup_seconds':60,
 'read_timeout_seconds':30,'rate_bytes_per_second':4*MIB,'block_bytes':256*1024,
 'attempts_per_artifact':1,'automatic_retries':0,'receipt_reserve_bytes':MIB}
PHASES={
 'runtime-fixtures':('runtime-fixtures','runtime/test_install.py',300),
 'runtime-host-inventory':('runtime-host-inventory','runtime/probe_runtime.py',600),
 'runtime-wheel-audit':('runtime-wheel-audit','runtime/install.py',300),
 'assets-model-audit':('assets-model-audit','runtime/install.py',300),
 'assets-corpus-select':('assets-corpus-select','runtime/install.py',1800),
 'runtime-build-install':('runtime-build-install','runtime/install.py',1800),
 'runtime-import':('runtime-import','runtime/probe_runtime.py',600),
 'assets-model-extract':('assets-model-extract','runtime/install.py',600),
 'assets-corpus-extract':('assets-corpus-extract','runtime/install.py',1800),
 'assets-input-derive':('assets-input-derive','runtime/install.py',300)}
FILE_CEILINGS={'bootstrap.py':80*1024,'config.schema.json':24*1024,
 'acquisition_guard.py':32*1024,'guard_driver.py':44*1024,
 'runtime/install.py':120*1024,'runtime/probe_runtime.py':80*1024,
 'runtime/test_install.py':68*1024,'runtime/runtime.lock.json':128*1024,
 'runtime/source-map.json':32*1024,'PACKET_MANIFEST.json':16*1024}
RUNTIME_ALLOCATION={'status':'PENDING_ALLOCATION_CENSUS','final_bytes':320*MIB,
 'build_bytes':128*MIB,'ancestor_bytes':64*MIB}
OWNER_ACCEPTANCE_PENDING='PENDING_OWNER_ACCEPTANCE_ORIGINAL_TOOL_REAP_REVIEW'

class Refused(RuntimeError): pass


class PromotionRace(Refused): pass


def require(ok,reason):
    if not ok: raise Refused(reason)


def closed(value,keys,reason):
    require(type(value) is dict and set(value)==set(keys),reason)


def check_deadline(end):
    require(time.monotonic()<end,'deadline')


def path_value(raw,exists=True):
    require(type(raw) is str and 0<len(raw)<=4096 and '\x00' not in raw,'path_shape')
    p=Path(raw)
    require(p.is_absolute() and str(p)==raw and '..' not in p.parts and p.resolve(strict=False)==p,'canonical_path_required')
    for q in (p,*p.parents): require(not q.is_symlink(),'symlink_path')
    if exists: require(p.exists(),'required_path_missing')
    return p


def pin_file(path,maximum=32*MIB,end=None):
    p=Path(path);before=p.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_size<=maximum,'regular_file_cap')
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK); h=hashlib.sha256();n=0
    key=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
    try:
        require(key(before)==key(os.fstat(fd)),'file_open_race')
        while True:
            if end is not None:check_deadline(end)
            block=os.read(fd,min(65536,maximum+1-n))
            if not block:break
            n+=len(block);require(n<=maximum,'file_growth_cap');h.update(block)
        require(key(before)==key(os.fstat(fd))==key(p.lstat()),'file_changed')
    finally:os.close(fd)
    return {'bytes':n,'sha256':h.hexdigest()}


def write_new(path,value,maximum=MIB):
    p=Path(path);raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    require(len(raw)<=maximum,'output_metadata_cap')
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    directory=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(directory)
    finally:os.close(directory)
    return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


def process_identity():
    raw=Path('/proc/self/stat').read_text();items=raw[raw.rindex(')')+2:].split()
    return {'pid':os.getpid(),'start_ticks':int(items[19]),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'pid_namespace':os.readlink('/proc/self/ns/pid')}



def integer(value,minimum=0,maximum=2**63-1):
    require(type(value) is int and minimum<=value<=maximum,'integer_type_or_range')
    return value

def number(value,minimum=0,maximum=2**63-1):
    require(type(value) in (int,float) and math.isfinite(value) and minimum<=value<=maximum,'finite_number_type_or_range')
    return value

def text_value(value,maximum=4096):
    require(type(value) is str and 0<len(value)<=maximum and '\x00' not in value,'text_type_or_range')
    return value

def digest_value(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None,'digest_shape')
    return value

def strict_json(raw,maximum=MIB):
    require(type(raw) in (bytes,str) and len(raw)<=maximum,'json_size_type')
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,'duplicate_json_key');result[key]=value
        return result
    try:
        result=json.loads(raw,object_pairs_hook=pairs,
          parse_constant=lambda value:(_ for _ in ()).throw(Refused('nonfinite_json')))
    except (ValueError,UnicodeError,RecursionError) as error:
        raise Refused('invalid_json') from error
    data_only(result)
    return result

def data_only(value,depth=0):
    require(depth<=32,'metadata_depth')
    if value is None or type(value) is bool:return
    if type(value) is int:integer(value,-2**63);return
    if type(value) is float:number(value,-2**63);return
    if type(value) is str:
        require(len(value)<=16384 and '\x00' not in value,'metadata_string');return
    if type(value) is list:
        require(len(value)<=8192,'metadata_list_cap')
        for item in value:data_only(item,depth+1)
        return
    require(type(value) is dict and len(value)<=8192,'metadata_object')
    for key,item in value.items():text_value(key,256);data_only(item,depth+1)

def json_file(path,maximum=MIB,sha=None):
    p=Path(path);pin=pin_file(p,maximum)
    if sha is not None:require(pin['sha256']==digest_value(sha),'json_pin_mismatch')
    raw=p.read_bytes()
    require(len(raw)==pin['bytes'] and hashlib.sha256(raw).hexdigest()==pin['sha256'],'json_changed')
    return strict_json(raw,maximum)

def lexical_path(value):
    text_value(value)
    p=Path(value)
    require(p.is_absolute() and str(p)==value and '..' not in p.parts and '\\' not in value,'absolute_lexical_path')
    return p

def reference(value,maximum=MIB):
    closed(value,('path','bytes','sha256'),'reference_fields')
    lexical_path(value['path']);integer(value['bytes'],1,maximum);digest_value(value['sha256'])
    return value

def identity_data(value):
    closed(value,('pid','start_ticks','boot_id','pid_namespace'),'identity_fields')
    integer(value['pid'],1);integer(value['start_ticks'],1)
    require(type(value['boot_id']) is str and re.fullmatch('[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}',value['boot_id']) is not None,'boot_id_shape')
    require(type(value['pid_namespace']) is str and re.fullmatch(r'pid:\[[0-9]+\]',value['pid_namespace']) is not None,'pid_namespace_shape')
    return value

def artifact_rows(expected_assets):
    require(type(expected_assets) is list and len(expected_assets)==38,'expected_38_assets')
    rows=[];ids=set();destinations=set()
    for item in expected_assets:
        closed(item,('id','url','bytes','sha256','destination','group'),'expected_asset_fields')
        require(type(item['id']) is str and re.fullmatch('[a-zA-Z0-9._-]{1,96}',item['id']) is not None and item['id'] not in ids,'asset_id')
        text_value(item['url']);require(item['url'].startswith('https://'),'asset_https_url')
        integer(item['bytes'],1,4*GIB-1);digest_value(item['sha256'])
        name=text_value(item['destination'])
        require(not name.startswith('/') and '\\' not in name and all(x not in ('','.','..') for x in name.split('/')) and name not in destinations,'asset_destination')
        require(type(item['group']) is str and item['group'] in ('models','wheels','corpus'),'asset_group')
        ids.add(item['id']);destinations.add(name)
        rows.append({k:item[k] for k in ('id','destination','bytes','sha256')})
    require(sum(item['bytes'] for item in rows)==5150756491,'expected_asset_total')
    return rows

def validate_fresh_result(result,expected_assets):
    """Pure-data fresh-v2 gate. No filesystem reads and no live identity queries."""
    data_only(result)
    keys=('schema','status','failure','verified_artifacts','expected_artifacts','network_bytes',
      'attempts_per_missing_asset_this_phase','reused_artifacts','reused_verified_bytes',
      'newly_received_artifacts','newly_received_bytes','final_verified_artifacts',
      'final_verified_bytes','prior_attempt_preserved','prior_failed_nemotron_request_preserved',
      'prior_after_verified','reconciliation','automatic_retries','ranges','archives_extracted',
      'installs','provider_imports','model_loads','model_calls','outer_terminal_required')
    closed(result,keys,'fresh_result_fields')
    require(result['schema']=='portable-download-result-v2' and result['status']=='VERIFIED_DOWNLOAD_ONLY','fresh_result_schema_status')
    require(result['failure'] is None and result['prior_after_verified'] is None,'fresh_result_no_prior_or_failure')
    expected={'expected_artifacts':38,'network_bytes':5150756491,
      'attempts_per_missing_asset_this_phase':1,'reused_artifacts':0,'reused_verified_bytes':0,
      'newly_received_artifacts':38,'newly_received_bytes':5150756491,
      'final_verified_artifacts':38,'final_verified_bytes':5150756491,
      'automatic_retries':0,'archives_extracted':0,'installs':0,'provider_imports':0,'model_loads':0,'model_calls':0}
    for key,wanted in expected.items():integer(result[key]);require(result[key]==wanted,'fresh_count_'+key)
    for key in ('prior_attempt_preserved','prior_failed_nemotron_request_preserved','ranges'):
        require(result[key] is False,'fresh_false_'+key)
    require(result['outer_terminal_required'] is True,'outer_terminal_required')
    rows=artifact_rows(expected_assets)
    require(type(result['verified_artifacts']) is list and len(result['verified_artifacts'])==38,'fresh_result_rows')
    for got,wanted in zip(result['verified_artifacts'],rows):
        closed(got,('id','destination','bytes','sha256'),'verified_asset_fields')
        text_value(got['id']);text_value(got['destination']);integer(got['bytes'],1);digest_value(got['sha256'])
        require(got==wanted,'fresh_exact_ordered_artifact_pin')
    counts={key:expected[key] for key in ('reused_artifacts','reused_verified_bytes','newly_received_artifacts','newly_received_bytes','final_verified_artifacts','final_verified_bytes')}
    closed(result['reconciliation'],counts,'fresh_reconciliation_fields')
    for key,wanted in counts.items():integer(result['reconciliation'][key]);require(result['reconciliation'][key]==wanted,'fresh_reconciliation_value')
    return result

def process_data(value,observed=False,owner=False):
    keys=('pid','start_ticks','ppid','state','rss_bytes') if owner else ('pid','start_ticks','create_time','state','ppid','rss','boot_id','pid_namespace')
    if observed:keys=(*keys,'pid_namespace_verified_while_alive')
    closed(value,keys,'process_fields')
    for key in ('pid','start_ticks'):integer(value[key],1)
    integer(value['ppid']);integer(value['rss_bytes' if owner else 'rss'])
    require(type(value['state']) is str and value['state'] in ('R','S','D','Z','T','t','X','I','P'),'process_state')
    if not owner:
        number(value['create_time'])
        identity_data({k:value[k] for k in ('pid','start_ticks','boot_id','pid_namespace')})
    if observed:require(type(value['pid_namespace_verified_while_alive']) is bool,'namespace_verification_type')
    return value

def string_list(value,maximum=256):
    require(type(value) is list and len(value)<=maximum,'string_list_type_cap')
    for item in value:text_value(item)
    return value

def validate_guard_receipt(guard,expected_spec_sha,seconds=10800):
    data_only(guard);digest_value(expected_spec_sha)
    keys=('argv','caps','category_caps','charged_paths','child_reaped_highwater_rss','cleanup',
      'cleanup_seconds','elapsed_seconds','environment_keys','errors','exit_code',
      'final_child_highwater_plus_supervisor_rss','final_direct_child_pids','final_source_pin_verified',
      'fresh_instance','latest_required_free','limits_are_sampled','minimum_disk_free',
      'minimum_host_available','observed','original','original_handle_waited',
      'peak_charged_scratch_bytes','peak_sampled_owned_rss','platform_network_keys_present',
      'platform_network_value_recording','process_discovery','python_version','reaped',
      'run_directory','runtime_files','runtime_qualified','runtime_scope','sample_gap_seconds_max',
      'sample_interval_seconds','schema','signal_events','spec_sha256','status','stop_path',
      'supervisor','unresolved_owned')
    closed(guard,keys,'download_guard_fields')
    integer(guard['schema']);integer(guard['exit_code'])
    require(guard['schema']==1 and guard['status']=='PASS' and guard['cleanup']=='CONFIRMED' and guard['exit_code']==0,'download_guard_pass')
    for key in ('original_handle_waited','final_source_pin_verified','fresh_instance','limits_are_sampled'):
        require(guard[key] is True,'guard_true_'+key)
    require(guard['runtime_qualified'] is False and guard['platform_network_value_recording']=='NONE','guard_no_runtime_or_network_value_claim')
    require(guard['spec_sha256']==expected_spec_sha,'guard_spec_pin')
    for key in ('errors','unresolved_owned','final_direct_child_pids','signal_events'):
        require(type(guard[key]) is list and guard[key]==[],'guard_empty_'+key)
    for key in ('argv','charged_paths','environment_keys','platform_network_keys_present'):string_list(guard[key])
    for key in ('process_discovery','python_version','runtime_scope'):text_value(guard[key])
    lexical_path(guard['run_directory']);lexical_path(guard['stop_path'])
    require(guard['stop_path']==str(Path(guard['run_directory'])/'STOP'),'guard_stop_path')
    for key in ('child_reaped_highwater_rss','final_child_highwater_plus_supervisor_rss','latest_required_free',
      'minimum_disk_free','minimum_host_available','peak_charged_scratch_bytes','peak_sampled_owned_rss'):integer(guard[key])
    for key in ('cleanup_seconds','elapsed_seconds','sample_gap_seconds_max','sample_interval_seconds'):number(guard[key])
    require(guard['cleanup_seconds']<=60 and guard['elapsed_seconds']<=seconds and guard['sample_interval_seconds']==0.05,'guard_time_bounds')
    require(guard['peak_sampled_owned_rss']<=256*MIB and guard['final_child_highwater_plus_supervisor_rss']<=256*MIB and guard['peak_charged_scratch_bytes']+MIB<=12*GIB,'guard_sampled_caps')
    require(guard['minimum_host_available']>=LIMITS['minimum_available_bytes'] and guard['minimum_disk_free']>=LIMITS['ordinary_free_floor_bytes'],'guard_resource_floors')
    closed(guard['category_caps'],CAPS,'guard_category_fields')
    for key,wanted in CAPS.items():integer(guard['category_caps'][key]);require(guard['category_caps'][key]==wanted,'guard_category_cap')
    caps=guard['caps'];expected={'owned_rss_bytes':256*MIB,'scratch_bytes':12*GIB,
      'host_available_min':LIMITS['minimum_available_bytes'],'disk_free_min':LIMITS['ordinary_free_floor_bytes'],
      'receipt_reserve_bytes':MIB,'work_seconds':seconds-60,'absolute_seconds':seconds,
      'ordinary_free_floor_bytes':LIMITS['ordinary_free_floor_bytes']}
    closed(caps,(*expected,'address_space_soft_hard','remaining_aggregate_free_reservation'),'guard_caps_fields')
    for key,wanted in expected.items():number(caps[key]) if key=='work_seconds' else integer(caps[key]);require(caps[key]==wanted,'guard_cap_value')
    require(type(caps['address_space_soft_hard']) is list and len(caps['address_space_soft_hard'])==2,'guard_address_pair')
    for item in caps['address_space_soft_hard']:integer(item);require(item==GIB,'guard_address_cap')
    require(caps['remaining_aggregate_free_reservation'] is True,'guard_aggregate_reservation')
    process_data(guard['supervisor']);process_data(guard['original'],observed=True)
    require(type(guard['observed']) is list and 1<=len(guard['observed'])<=64,'guard_observed_cap')
    seen=set()
    for item in guard['observed']:
        process_data(item,observed=True);key=(item['pid'],item['start_ticks'])
        require(key not in seen,'guard_duplicate_owned_identity');seen.add(key)
        require(item['boot_id']==guard['supervisor']['boot_id'] and item['pid_namespace']==guard['supervisor']['pid_namespace'],'guard_internal_historical_namespace')
    require(guard['original'] in guard['observed'] and guard['original']['ppid']==guard['supervisor']['pid'],'guard_original_parent')
    require(type(guard['reaped']) is list and len(guard['reaped'])<=64,'guard_reaped_cap')
    for item in guard['reaped']:
        closed(item,('pid','start_ticks','exit_code','maxrss_bytes'),'reaped_fields')
        integer(item['pid'],1);integer(item['start_ticks'],1);integer(item['exit_code'],-255,255);integer(item['maxrss_bytes'])
        require((item['pid'],item['start_ticks']) in seen,'reaped_owned_identity')
    require(type(guard['runtime_files']) is dict and len(guard['runtime_files'])==1,'guard_host_only_runtime')
    for path,sha in guard['runtime_files'].items():lexical_path(path);require(digest_value(sha)==PYTHON_PIN['sha256'],'guard_host_pin')
    return guard

def validate_download_guard(guard,expected_spec_sha):
    return validate_guard_receipt(guard,expected_spec_sha,10800)

def owner_handle(row,phase):
    keys=('phase','pid','argv','waited','parent_launch_monotonic','budget_seconds','identity',
      'returncode','waited_monotonic','log','new_runs')
    if phase!='configure':keys=(*keys,'original_launch','spec')
    closed(row,keys,'owner_handle_fields')
    require(row['phase']==phase and row['waited'] is True,'owner_phase_wait')
    integer(row['pid'],1);integer(row['returncode']);require(row['returncode']==0,'owner_phase_returncode')
    integer(row['budget_seconds']);require(row['budget_seconds']==({'configure':120,'download':10800}.get(phase,300)),'owner_phase_budget')
    number(row['parent_launch_monotonic']);number(row['waited_monotonic'])
    require(row['parent_launch_monotonic']<=row['waited_monotonic'],'owner_wait_not_before_launch')
    if phase=='configure':require(row['waited_monotonic']<=row['parent_launch_monotonic']+row['budget_seconds'],'owner_configure_wait_window')
    process_data(row['identity'],owner=True);require(row['identity']['pid']==row['pid'],'owner_original_pid')
    string_list(row['argv']);string_list(row['new_runs'],64);reference(row['log'])
    if phase!='configure':
        reference(row['spec']);launch_data(row['original_launch'],row['budget_seconds'])
        require(row['parent_launch_monotonic']<=row['original_launch']['start_monotonic']<row['parent_launch_monotonic']+5 and row['waited_monotonic']<=row['original_launch']['absolute_end_monotonic'],'owner_original_launch_wait_window')
        require(row['original_launch']['identity']['pid']==row['pid'] and row['original_launch']['identity']['start_ticks']==row['identity']['start_ticks'],'owner_launch_pid_ticks')
    return row

def validate_owner_terminal(owner,guard,expected_spec_sha):
    """Validate the actual historical owner schema, without trusting it as tool reap."""
    data_only(owner)
    keys=('schema','status','phases','original_handles','errors','signal_events','cleanup',
      'historical_download_49874','peak_sampled_coordinator_rss','sampled_rss_ceiling_bytes',
      'limits_are_sampled','absolute_start_monotonic','absolute_end_monotonic','execution_attempts',
      'coordinator_identity','boot_id','pid_namespace','coordinator_sha256','contract_sha256',
      'pinned_prerequisites','coordinator_overhead_seconds','loader_observations','review_marker',
      'approval_consumed','download_result','final_direct_children','elapsed_seconds')
    closed(owner,keys,'actual_fixed_owner_terminal_fields')
    require(owner['schema']=='fixed-owner-sequence-v1' and owner['status']=='VERIFIED_DOWNLOAD_ONLY' and owner['cleanup']=='CONFIRMED','owner_terminal_state')
    require(owner['limits_are_sampled'] is True,'owner_sampled_disclosure')
    for key in ('errors','signal_events','final_direct_children'):
        require(type(owner[key]) is list and owner[key]==[],'owner_empty_'+key)
    # Narrow schema-key exception: the authentic owner schema names unresolved
    # historical work. This field never grants ownership or cleanup authority.
    require(owner['historical_download_49874']=='UNRESOLVED; no old cleanup claim','no_old_cleanup_claim')
    for key in ('peak_sampled_coordinator_rss','sampled_rss_ceiling_bytes','execution_attempts'):integer(owner[key])
    require(owner['sampled_rss_ceiling_bytes']==32*MIB and owner['peak_sampled_coordinator_rss']<=32*MIB and owner['execution_attempts']==1,'owner_fixed_caps_attempt')
    for key in ('absolute_start_monotonic','absolute_end_monotonic','coordinator_overhead_seconds','elapsed_seconds'):number(owner[key])
    require(owner['absolute_end_monotonic']==owner['absolute_start_monotonic']+12180 and owner['elapsed_seconds']<=12180 and owner['coordinator_overhead_seconds']<60,'owner_time_window')
    process_data(owner['coordinator_identity'],owner=True)
    identity_data({'pid':owner['coordinator_identity']['pid'],'start_ticks':owner['coordinator_identity']['start_ticks'],'boot_id':owner['boot_id'],'pid_namespace':owner['pid_namespace']})
    for key in ('coordinator_sha256','contract_sha256'):digest_value(owner[key])
    for key in ('review_marker','approval_consumed'):reference(owner[key])
    require(type(owner['pinned_prerequisites']) is list and 1<=len(owner['pinned_prerequisites'])<=64,'owner_prerequisite_list')
    paths=set()
    for item in owner['pinned_prerequisites']:
        reference(item,32*MIB);require(item['path'] not in paths,'owner_duplicate_prerequisite');paths.add(item['path'])
    require(type(owner['loader_observations']) is list and len(owner['loader_observations'])==5,'owner_loader_observations')
    for item,stage in zip(owner['loader_observations'],('before-configure','before-guard-fixtures','before-pacing-fixtures','before-download','terminal')):
        closed(item,('stage','at_monotonic','forbidden_variables_present','ld_so_preload_lstat_absent'),'owner_loader_fields')
        require(item['stage']==stage and item['forbidden_variables_present']==[] and type(item['forbidden_variables_present']) is list and item['ld_so_preload_lstat_absent'] is True,'owner_loader_clear')
        number(item['at_monotonic'])
    names=('configure','guard-fixtures','pacing-fixtures','download')
    require(type(owner['phases']) is list and len(owner['phases'])==4 and type(owner['original_handles']) is list and len(owner['original_handles'])==4,'owner_four_phase_shape')
    for phase,pin,row in zip(names,owner['phases'],owner['original_handles']):
        reference(pin);require(Path(pin['path']).name==phase+'.json','owner_phase_receipt_path');owner_handle(row,phase)
        require(row['identity']['ppid']==owner['coordinator_identity']['pid'],'owner_internal_original_parent')
        if phase!='configure':
            identity=row['original_launch']['identity']
            require(identity['boot_id']==owner['boot_id'] and identity['pid_namespace']==owner['pid_namespace'],'owner_internal_historical_tuple')
    download=owner['download_result'];closed(download,('phase','guard_receipt','raw_result','original_wait'),'owner_download_binding_fields')
    require(download['phase']=='download' and download['original_wait']==owner['original_handles'][-1],'owner_download_original_handle')
    reference(download['guard_receipt']);reference(download['raw_result'])
    run=Path(guard['run_directory'])
    require(download['guard_receipt']['path']==str(run/'receipt.json') and download['raw_result']['path']==str(run/'download-result.json'),'owner_download_paths')
    row=download['original_wait'];supervisor=guard['supervisor']
    require(row['spec']['sha256']==expected_spec_sha and supervisor['pid']==row['pid'] and supervisor['start_ticks']==row['identity']['start_ticks'] and supervisor['ppid']==owner['coordinator_identity']['pid'],'owner_guard_internal_identity')
    require(supervisor['boot_id']==owner['boot_id'] and supervisor['pid_namespace']==owner['pid_namespace'],'owner_guard_historical_namespace')
    return owner

def same_data(left,right):
    """JSON equality with exact scalar types, so True never equals 1."""
    return json.dumps(left,sort_keys=True,allow_nan=False)==json.dumps(right,sort_keys=True,allow_nan=False)

def require_fresh_acquisition(guard,result,owner,expected_assets,expected_spec_sha,*,acceptance=None,bound_documents=None):
    """Validate caller-bound original bytes, without granting execution.

    bound_documents maps the eleven acceptance reference roles to original
    bytes. Caller authenticates acceptance itself. Tool completion fields:
    schema, original_tool, acquisition_identity. Post-reap report fields:
    schema, post_reap, assets_manifest, original_tool_completion, read_limits.
    Neither references acceptance: the graph is acyclic.
    """
    validate_fresh_result(result,expected_assets)
    validate_download_guard(guard,expected_spec_sha)
    validate_owner_terminal(owner,guard,expected_spec_sha)
    require(acceptance is not None and bound_documents is not None,OWNER_ACCEPTANCE_PENDING)
    validate_owner_acceptance(acceptance,expected_assets)
    refs=acceptance['references'];closed(bound_documents,refs,'bound_documents_fields')
    documents={}
    for name,ref in refs.items():
        raw=bound_documents[name]
        require(type(raw) is bytes and len(raw)==ref['bytes'] and hashlib.sha256(raw).hexdigest()==ref['sha256'],'bound_document_bytes_'+name)
        if name not in ('coordinator_source','coordinator_contract'):documents[name]=strict_json(raw,256*1024)
    for name,value in (('download_guard',guard),('download_result',result),('coordinator_terminal',owner)):
        require(same_data(documents[name],value),'bound_document_value_'+name)
    require(refs['coordinator_source']['sha256']==owner['coordinator_sha256'] and refs['coordinator_contract']['sha256']==owner['contract_sha256'],'owner_source_contract_pins')
    for name in ('coordinator_source','coordinator_contract'):require(refs[name] in owner['pinned_prerequisites'],'owner_prerequisite')
    historical={'pid':owner['coordinator_identity']['pid'],'start_ticks':owner['coordinator_identity']['start_ticks'],'boot_id':owner['boot_id'],'pid_namespace':owner['pid_namespace']}
    require(same_data(acceptance['acquisition_identity'],historical),'accepted_original_owner_tuple')
    download=owner['download_result']
    require(refs['download_guard']==download['guard_receipt'] and refs['download_result']==download['raw_result'] and refs['download_spec']==download['original_wait']['spec'],'accepted_raw_refs')
    require(refs['download_spec']['sha256']==expected_spec_sha,'accepted_spec_pin')
    require(same_data(documents['download_claim'],{'schema':'portable-single-dispatch-v1','phase':'download','attempts':1}),'single_download_claim')
    c=documents['acquisition_config']
    closed(c,('schema','roots','host','limits','assets_sha256','packet_sha256','created_boot_id','created_pid_namespace','prior_acquisition'),'historical_config_fields')
    require(c['schema']=='portable-recovery-config-v2' and c['prior_acquisition'] is None and same_data(c['limits'],LIMITS),'historical_fresh_config')
    require(c['created_boot_id']==historical['boot_id'] and c['created_pid_namespace']==historical['pid_namespace'],'historical_config_internal_tuple')
    closed(c['roots'],('workspace','source_recovery','product','packet','work','runtime','runtime_container','proof_source'),'historical_roots_fields')
    roots={key:lexical_path(value) for key,value in c['roots'].items()}
    host=c['host'];closed(host,('python','python_bytes','python_sha256','filesystem_device','python_version'),'historical_host_fields')
    lexical_path(host['python']);integer(host['python_bytes']);integer(host['filesystem_device']);digest_value(host['python_sha256'])
    require(same_data({'bytes':host['python_bytes'],'sha256':host['python_sha256']},PYTHON_PIN) and host['python_version']=='3.12.14','historical_host_pin')
    digest_value(c['packet_sha256']);require(digest_value(c['assets_sha256'])==refs['assets_manifest']['sha256'],'historical_assets_pin')
    require(refs['acquisition_config']['path']==str(roots['work']/'evidence/config.json') and refs['download_claim']['path']==str(roots['work']/'evidence/download.claim') and refs['download_spec']['path']==str(roots['work']/'evidence/download.spec.json'),'historical_config_claim_spec_paths')
    require(refs['assets_manifest']['path']==str(roots['packet']/'assets.lock.json'),'historical_assets_path')
    assets=documents['assets_manifest']
    closed(assets,('schema','status','source','artifacts','total_download_bytes','retained_download_plus_future_extraction_ceiling','attempts_per_artifact','automatic_retries','range_requests','extraction_authorized_by_this_manifest','local_source_copies','future_extraction_ceiling_bytes','future_input'),'historical_assets_fields')
    require(assets['schema']=='portable-recovery-assets-v1' and same_data(assets['artifacts'],expected_assets),'bound_expected_assets')
    integer(assets['total_download_bytes']);require(assets['total_download_bytes']==5150756491,'bound_assets_total')
    source=assets['source'];closed(source,(*PRODUCT,'files'),'bound_product_fields')
    require(same_data({key:source[key] for key in PRODUCT},PRODUCT) and type(source['files']) is dict and len(source['files'])==351,'bound_product_identity')
    for name,sha in source['files'].items():
        text_value(name);digest_value(sha);require(not name.startswith('/') and '\\' not in name and all(part not in ('','.','..') for part in name.split('/')),'source_relative_path')
    external={item['path']:item['sha256'] for item in owner['pinned_prerequisites'] if Path(item['path']).parent==roots['packet']}
    require(len(external)==10,'historical_packet_pin_count')
    external[refs['acquisition_config']['path']]=refs['acquisition_config']['sha256']
    require(external.get(str(roots['packet']/'PACKET_MANIFEST.json'))==c['packet_sha256'] and external.get(refs['assets_manifest']['path'])==c['assets_sha256'],'historical_packet_pin_links')
    expected={'schema':3,'stage':'download','absolute_seconds':10800,'work_seconds':10740,
      'address_space_cap_bytes':GIB,'rss_cap_bytes':256*MIB,'scratch_cap_bytes':12*GIB,
      'guard_sha256':external.get(str(roots['packet']/'acquisition_guard.py')),
      'source_root':str(roots['product']),'source_files':source['files'],
      'cwd':str(roots['work']/'evidence'),'external_files':external,
      'runtime_files':{host['python']:PYTHON_PIN['sha256']},
      'config_path':refs['acquisition_config']['path'],'config_sha256':refs['acquisition_config']['sha256'],
      'phase':'download','launch':download['original_wait']['original_launch'],
      'argv':[host['python'],'-I','-B','-S',str(roots['packet']/'acquire.py'),'--config',refs['acquisition_config']['path'],'--config-sha256',refs['acquisition_config']['sha256']]}
    require(same_data(documents['download_spec'],expected) and same_data(guard['argv'],expected['argv']) and same_data(guard['runtime_files'],expected['runtime_files']),'exact_historical_spec_guard')
    require(same_data(documents['original_tool_completion'],{'schema':'portable-original-tool-completion-v1','original_tool':acceptance['original_tool'],'acquisition_identity':historical}),'original_tool_completion_binding')
    read_limits={'absolute_seconds':300,'work_seconds':240,'cleanup_seconds':60,
      'block_bytes':262144,'max_asset_files':38,'max_asset_bytes':5150756491,
      'max_metadata_bytes':262144,'max_identity_bytes':4096}
    require(same_data(documents['post_reap_report'],{'schema':'portable-post-reap-audit-v1',
      'post_reap':acceptance['post_reap'],'assets_manifest':refs['assets_manifest'],
      'original_tool_completion':refs['original_tool_completion'],'read_limits':read_limits}),'post_reap_report_binding')
    return acceptance


def launch_data(launch,seconds):
    closed(launch,('start_monotonic','absolute_end_monotonic','identity'),'launch_fields')
    number(launch['start_monotonic']);number(launch['absolute_end_monotonic']);identity_data(launch['identity'])
    require(launch['absolute_end_monotonic']==launch['start_monotonic']+seconds,'launch_budget')
    return launch

def launch_binding(start,phase):
    require(type(phase) is str and phase in PHASES,'phase_not_allowed');number(start)
    return {'start_monotonic':start,'absolute_end_monotonic':start+PHASES[phase][2],'identity':process_identity()}

def validate_launch(launch,phase):
    require(type(phase) is str and phase in PHASES,'phase_not_allowed');launch_data(launch,PHASES[phase][2])
    require(launch['start_monotonic']<=time.monotonic()<launch['absolute_end_monotonic']-60,'original_invocation_deadline')
    require(launch['identity']==process_identity(),'same_current_invocation_identity')
    return launch


def validate_owner_acceptance(value,expected_assets):
    """Pure proposed post-reap acceptance contract; never grants execution."""
    data_only(value)
    closed(value,('schema','status','execution_authorized','acquisition_identity',
      'original_tool','references','post_reap','scope'),'owner_acceptance_fields')
    require(value['schema']=='portable-fresh-owner-acceptance-v1' and value['status']=='ACCEPTED_FRESH_DOWNLOAD_ONLY' and value['execution_authorized'] is False,'owner_acceptance_scope')
    historical=identity_data(value['acquisition_identity'])
    tool=value['original_tool'];closed(tool,('session_id','terminal','exit_code','owner_stop_or_signal'),'original_tool_fields')
    integer(tool['session_id'],1);integer(tool['exit_code'])
    require(tool['terminal'] is True and tool['exit_code']==0 and tool['owner_stop_or_signal'] is False,'original_tool_terminal_success')
    refs=value['references'];names=('coordinator_source','coordinator_contract','coordinator_terminal',
      'acquisition_config','download_claim','download_spec','download_guard','download_result',
      'assets_manifest','original_tool_completion','post_reap_report')
    closed(refs,names,'accepted_reference_fields')
    paths=set()
    for name in names:
        reference(refs[name],{'original_tool_completion':16*1024,'post_reap_report':64*1024}.get(name,256*1024))
        require(refs[name]['path'] not in paths,'accepted_reference_path_alias');paths.add(refs[name]['path'])
    post=value['post_reap'];closed(post,('audit_identity','started_after_original_tool_terminal',
      'closed_destination_set','asset_count','asset_bytes','artifacts'),'post_reap_fields')
    audit=identity_data(post['audit_identity'])
    require(audit['boot_id']==historical['boot_id'],'post_reap_boot_consistency')
    require(post['started_after_original_tool_terminal'] is True and post['closed_destination_set'] is True,'post_reap_required_assertions')
    integer(post['asset_count']);integer(post['asset_bytes'])
    require(post['asset_count']==38 and post['asset_bytes']==5150756491,'post_reap_counts')
    expected=artifact_rows(expected_assets)
    require(type(post['artifacts']) is list and len(post['artifacts'])==38,'post_reap_rows')
    seen=set()
    for got,wanted in zip(post['artifacts'],expected):
        closed(got,(*wanted,'descriptor_before','descriptor_after'),'post_reap_asset_fields')
        require({key:got[key] for key in wanted}==wanted,'post_reap_exact_asset_pin')
        integer(got['bytes'],1);digest_value(got['sha256'])
        for key in ('id','destination'):text_value(got[key])
        for key in ('descriptor_before','descriptor_after'):
            descriptor=got[key]
            closed(descriptor,('dev','ino','mode','nlink','size','mtime_ns','ctime_ns'),'descriptor_fields')
            for field in descriptor:integer(descriptor[field],1 if field in ('ino','nlink') else 0)
            require(stat.S_ISREG(descriptor['mode']) and descriptor['nlink']==1 and descriptor['size']==wanted['bytes'],'descriptor_regular_single_link_exact_size')
        require(got['descriptor_before']==got['descriptor_after'],'post_reap_descriptor_changed')
        descriptor=got['descriptor_before'];key=(descriptor['dev'],descriptor['ino'])
        require(key not in seen,'post_reap_duplicate_inode');seen.add(key)
    scope=value['scope'];expected_scope={'reused_artifacts':0,'newly_received_artifacts':38,
      'network_bytes':5150756491,'archives_extracted':0,'installs':0,'provider_imports':0,
      'model_loads':0,'model_calls':0}
    closed(scope,(*expected_scope,'runtime_container_present'),'acceptance_scope_fields')
    for key,wanted in expected_scope.items():integer(scope[key]);require(scope[key]==wanted,'acceptance_scope_value')
    require(scope['runtime_container_present'] is False,'acceptance_runtime_absent')
    return value


def validate_config(c,current_identity=None):
    """Pure-data config validator; new and historical identities are independent."""
    data_only(c)
    closed(c,('schema','status','execution_authorized','roots','host','limits','assets_manifest',
      'packet_sha256','runtime_lock_sha256','acquisition','invocation','accepted_phases',
      'runtime_allocation','execution_release','phase_args'),'config_fields')
    require(c['schema']=='portable-runtime-fresh-config-v1' and c['status']=='REVIEWED_PRIVATE_PHASE_CONFIG' and c['execution_authorized'] is True,'reviewed_private_config_identity')
    closed(c['execution_release'],('template','go'),'execution_release_fields')
    for item in c['execution_release'].values():reference(item,65536)
    args=c['phase_args'];closed(args,('inputs','outputs','options'),'phase_args_fields')
    require(type(args['inputs']) is dict and len(args['inputs'])<=3 and type(args['outputs']) is dict and 1<=len(args['outputs'])<=2 and type(args['options']) is dict and args['options'] in ({},{'host_verification':'CANDIDATE_BYTES_AND_OBSERVED_MAPPINGS'}),'phase_args_shape')
    for name,item in args['inputs'].items():text_value(name,64);reference(item,4*MIB)
    for name,path in args['outputs'].items():text_value(name,64);lexical_path(path)
    closed(c['roots'],('workspace','source_recovery','product','packet','work','runtime','runtime_container','proof_source'),'roots_fields')
    roots={key:lexical_path(value) for key,value in c['roots'].items()}
    require(roots['packet'].parent==roots['source_recovery'] and roots['product'].parent==roots['source_recovery'] and roots['packet']!=roots['product'],'packet_product_source_scope')
    require(roots['runtime_container'].parent==roots['workspace'] and roots['runtime']==roots['runtime_container']/'runtime','runtime_container_scope')
    charged=('source_recovery','work','runtime_container','proof_source')
    for key in charged:require(roots[key]!=roots['workspace'] and roots[key].is_relative_to(roots['workspace']),'charged_root_scope')
    for offset,key in enumerate(charged):
        for other in charged[offset+1:]:require(not roots[key].is_relative_to(roots[other]) and not roots[other].is_relative_to(roots[key]),'charged_roots_overlap')
    host=c['host'];closed(host,('python','python_bytes','python_sha256','filesystem_device','python_version'),'host_fields')
    lexical_path(host['python']);integer(host['python_bytes'],1);digest_value(host['python_sha256']);integer(host['filesystem_device'])
    require({'bytes':host['python_bytes'],'sha256':host['python_sha256']}==PYTHON_PIN and host['python_version']=='3.12.14','host_pin')
    closed(c['limits'],LIMITS,'limits_fields')
    for key,wanted in LIMITS.items():
        if key=='category_bytes':
            closed(c['limits'][key],CAPS,'category_fields')
            for name,cap in CAPS.items():integer(c['limits'][key][name]);require(c['limits'][key][name]==cap,'category_cap')
        else:integer(c['limits'][key]);require(c['limits'][key]==wanted,'limit_value')
    reference(c['assets_manifest'],98304)
    old_packet=Path(c['assets_manifest']['path']).parent
    require(old_packet!=roots['workspace'] and old_packet.is_relative_to(roots['workspace']),'acquisition_source_scope')
    for key in ('work','runtime_container','proof_source'):
        require(not old_packet.is_relative_to(roots[key]) and not roots[key].is_relative_to(old_packet),'acquisition_source_overlap')
    for key in ('packet_sha256','runtime_lock_sha256'):digest_value(c[key])
    acquired=c['acquisition'];closed(acquired,('historical_identity','config','guard_receipt','download_result','owner_terminal','owner_acceptance'),'acquisition_fields')
    identity_data(acquired['historical_identity'])
    for key in ('config','guard_receipt','download_result'):reference(acquired[key])
    if acquired['owner_terminal'] is not None:reference(acquired['owner_terminal'])
    if acquired['owner_acceptance'] is not None:reference(acquired['owner_acceptance'],256*1024)
    invocation=c['invocation'];closed(invocation,('identity','phase','launch','attempts','cleanup_seconds'),'invocation_fields')
    identity_data(invocation['identity'])
    require(type(invocation['phase']) is str and invocation['phase'] in PHASES,'invocation_phase')
    integer(invocation['attempts']);integer(invocation['cleanup_seconds'])
    require(invocation['attempts']==1 and invocation['cleanup_seconds']==60,'invocation_budget')
    launch_data(invocation['launch'],PHASES[invocation['phase']][2])
    require(invocation['identity']==invocation['launch']['identity'],'configure_launch_same_invocation')
    if current_identity is not None:
        identity_data(current_identity);require(invocation['identity']==current_identity,'current_invocation_changed')
    require(type(c['accepted_phases']) is list and len(c['accepted_phases'])<=len(PHASES),'accepted_phase_cap')
    seen=set()
    for accepted in c['accepted_phases']:
        keys=('phase','config','guard_receipt','result','independent_acceptance')
        if 'source_revision' in accepted:
            require(invocation['phase']=='runtime-import' and accepted.get('phase')=='runtime-build-install' and
                    args['options']=={'host_verification':'CANDIDATE_BYTES_AND_OBSERVED_MAPPINGS'},'source_revision_phase_scope')
            reference(accepted['source_revision'],16384);keys+=('source_revision',)
        closed(accepted,keys,'accepted_phase_fields')
        name=accepted['phase'];require(type(name) is str and name in PHASES and name not in seen and name!=invocation['phase'],'accepted_phase_identity')
        seen.add(name)
        for key in ('config','guard_receipt','result','independent_acceptance'):reference(accepted[key])
    allocation=c['runtime_allocation'];closed(allocation,RUNTIME_ALLOCATION,'allocation_fields')
    require(allocation['status']==RUNTIME_ALLOCATION['status'],'pending_allocation_census')
    for key in ('final_bytes','build_bytes','ancestor_bytes'):
        integer(allocation[key]);require(allocation[key]==RUNTIME_ALLOCATION[key],'fixed_runtime_partition')
    return c

def configure_invocation(private_template,phase,launch_start,execution_release):
    """Capture current tuple once; the driver must first verify template and GO."""
    require(type(private_template) is dict and private_template.get('invocation') is None and private_template.get('execution_release') is None and private_template.get('status')=='PRIVATE_PHASE_TEMPLATE' and private_template.get('execution_authorized') is False,'fresh_template_required')
    c=strict_json(json.dumps(private_template,allow_nan=False),65536)
    launch=launch_binding(launch_start,phase)
    c.update(status='REVIEWED_PRIVATE_PHASE_CONFIG',execution_authorized=True,execution_release=execution_release)
    c['invocation']={'identity':dict(launch['identity']),'phase':phase,'launch':launch,'attempts':1,'cleanup_seconds':60}
    return validate_config(c,current_identity=launch['identity'])


def verify_packet():
    value=json_file(HERE/'PACKET_MANIFEST.json',FILE_CEILINGS['PACKET_MANIFEST.json'])
    closed(value,('schema','status','files','execution_authorized','tests_executed'),'packet_fields')
    require(value['schema']=='portable-runtime-fresh-packet-v1' and value['status']=='SOURCE_ONLY_DRAFT_UNADMITTED' and value['execution_authorized'] is False and value['tests_executed'] is False,'source_only_packet_identity')
    require(type(value['files']) is dict and set(value['files'])==set(FILE_CEILINGS)-{'PACKET_MANIFEST.json'},'packet_exact_nine_pins')
    actual=set()
    for base,dirs,files in os.walk(HERE,followlinks=False):
        for name in dirs:require(not (Path(base)/name).is_symlink(),'packet_directory_symlink')
        for name in files:actual.add(str((Path(base)/name).relative_to(HERE)))
    require(actual==set(FILE_CEILINGS),'packet_exact_ten_files')
    for name,row in value['files'].items():
        closed(row,('bytes','sha256'),'packet_file_pin_fields');integer(row['bytes'],1,FILE_CEILINGS[name]);digest_value(row['sha256'])
        require(pin_file(HERE/name,FILE_CEILINGS[name])==row,'packet_file_changed')
    return value

def read_bound(ref,maximum=MIB):
    reference(ref,maximum);path=path_value(ref['path'])
    pin=pin_file(path,maximum)
    require(pin=={key:ref[key] for key in ('bytes','sha256')},'reference_pin_changed')
    raw=path.read_bytes()
    require(len(raw)==ref['bytes'] and hashlib.sha256(raw).hexdigest()==ref['sha256'],'reference_read_changed')
    return raw

def driver_module():
    packet=verify_packet();path=HERE/'guard_driver.py'
    require(pin_file(path,FILE_CEILINGS['guard_driver.py'])==packet['files']['guard_driver.py'],'driver_source_changed')
    spec=importlib.util.spec_from_file_location('portable_runtime_phase_driver',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result

def phase_context(config_path,config_sha,phase):
    return driver_module().phase_context(config_path,config_sha,phase)

def phase_main(phase,module,args=None):
    return driver_module().phase_main(phase,module,args)

def source_revision(c,row,previous):
    """One reviewed import diagnostic derivative; historical execution stays v6."""
    require(c['invocation']['phase']=='runtime-import' and row['phase']=='runtime-build-install' and
            previous['invocation']['phase']=='runtime-build-install' and
            c['phase_args']['options']==previous['phase_args']['options']==
            {'host_verification':'CANDIDATE_BYTES_AND_OBSERVED_MAPPINGS'},'source_revision_phase_scope')
    ref=reference(row['source_revision'],16384)
    value=strict_json(read_bound(ref,16384),16384)
    closed(value,('schema','status','prior_packet','current_packet_sha256','build_acceptance',
                  'source_review','prior_import_failure','scope'),'source_revision_fields')
    require(value['schema']=='portable-runtime-import-source-revision-v1' and
            value['status']=='ACCEPTED_IMPORT_DIAGNOSTIC_REVISION_ONLY','source_revision_state')
    require(same_data(value['scope'],{'phase':'runtime-import','revision':'MODULE_ORIGIN_DIAGNOSTIC_ONLY',
            'runtime_bytes_unchanged':True,'build_relabelled':False,'constructor_policy_unchanged':True}),
            'source_revision_scope')
    for name,cap in (('prior_packet',16384),('build_acceptance',MIB),
                     ('source_review',65536),('prior_import_failure',MIB)):
        reference(value[name],cap)
    require(value['current_packet_sha256']==c['packet_sha256'] and
            value['prior_packet']['sha256']==previous['packet_sha256'] and
            c['packet_sha256']!=previous['packet_sha256'] and
            same_data(value['build_acceptance'],row['independent_acceptance']),'source_revision_packet_links')
    root=lexical_path(previous['roots']['packet'])
    require(root.parent==Path(c['roots']['source_recovery']) and root!=HERE and
            value['prior_packet']['path']==str(root/'PACKET_MANIFEST.json'),'source_revision_prior_root')
    namespace='evidence/runtime-fresh-v6'
    require(row['config']['path']==str(Path(previous['roots']['work'])/namespace/'runtime-build-install/config.json'),
            'source_revision_prior_namespace')
    packet=strict_json(read_bound(value['prior_packet'],16384),16384)
    closed(packet,('schema','status','execution_authorized','tests_executed','files'),'source_revision_packet_fields')
    require(packet['schema']=='portable-runtime-fresh-packet-v1' and
            packet['execution_authorized'] is False and packet['tests_executed'] is False and
            packet['status']=='SOURCE_ONLY_DRAFT_UNADMITTED' and
            set(packet['files'])==set(FILE_CEILINGS)-{'PACKET_MANIFEST.json'},'source_revision_packet_shape')
    references=[ref,*[value[name] for name in ('prior_packet','build_acceptance','source_review','prior_import_failure')]]
    for name,pin in packet['files'].items():
        closed(pin,('bytes','sha256'),'source_revision_file_pin')
        integer(pin['bytes'],1,FILE_CEILINGS[name]);digest_value(pin['sha256'])
        require(pin_file(root/name,FILE_CEILINGS[name])==pin,'source_revision_historical_file_changed')
        references.append({'path':str(root/name),**pin})
    review=strict_json(read_bound(value['source_review'],65536),65536)
    require(review.get('schema')=='portable-runtime-origin-focused-v1' and review.get('status')=='PASS' and
            review.get('original_packet_sha256')==previous['packet_sha256'] and
            review.get('current_packet_sha256')==c['packet_sha256'],'source_revision_focused_review')
    groups=review.get('groups')
    require(type(groups) is list and len(groups)==6 and
            all(type(item) is dict and type(item.get('case')) is int and item.get('case')==number and item.get('status')=='PASS'
                for number,item in enumerate(groups,1)) and review.get('fixture_ids')==list(range(1,90)) and
            all(type(item) is int for item in review['fixture_ids']),
            'source_revision_focused_groups')
    for key,cap in (('checker',65536),('origins',16384)):
        nested=reference(review.get(key),cap);read_bound(nested,cap);references.append(nested)
    read_bound(value['prior_import_failure'],MIB)
    accepted=strict_json(read_bound(value['build_acceptance']),MIB)
    require(accepted.get('schema')=='portable-runtime-phase-acceptance-v1' and
            accepted.get('phase')=='runtime-build-install','source_revision_build_acceptance')
    for key in ('config','guard_receipt','result'):
        require(same_data(accepted.get(key),row[key]),'source_revision_build_ref')
        references.append(row[key])
    for nested in [accepted['original_tool_completion'],*accepted['outputs'].values()]:
        reference(nested,4*MIB);references.append(nested)
    guard=strict_json(read_bound(row['guard_receipt']),MIB)
    spec_path=Path(row['config']['path']).parent/'spec.json'
    spec=json_file(spec_path,MIB,guard['spec_sha256'])
    references.append({'path':str(spec_path),**pin_file(spec_path,MIB)})
    # Pin every historical external document too; no archive/runtime payload is read.
    require(type(spec.get('external_files')) is dict and len(spec['external_files'])<=256,
            'source_revision_external_count')
    for path,wanted in spec['external_files'].items():
        lexical_path(path);digest_value(wanted);pin=pin_file(path,4*MIB)
        require(pin['sha256']==wanted,'source_revision_historical_external_changed')
        references.append({'path':path,**pin})
    paths={}
    for nested in references:
        require(nested['path'] not in paths or paths[nested['path']]==nested,
                'source_revision_reference_conflict')
        paths[nested['path']]=nested
    require(len(paths)<=320,'source_revision_reference_cap')
    return {'packet':packet,'root':root,'namespace':namespace,'references':list(paths.values())}


def validate_phase_acceptance(c,phase):
    """Validate prior phase/tool byte links; the current GO pins this marker."""
    rows=[row for row in c['accepted_phases'] if row['phase']==phase]
    require(len(rows)==1,'independently_accepted_prior_phase_required');row=rows[0]
    value=strict_json(read_bound(row['independent_acceptance']),MIB)
    closed(value,('schema','status','phase','config','guard_receipt','result','outputs','original_tool_completion'),'phase_acceptance_fields')
    require(value['schema']=='portable-runtime-phase-acceptance-v1' and value['status']=='ACCEPTED_PHASE_BYTES_ONLY' and value['phase']==phase,'phase_acceptance_state')
    for key in ('config','guard_receipt','result'):
        reference(value[key]);require(same_data(value[key],row[key]),'phase_acceptance_ref_link')
    roles={'runtime-host-inventory':('host',),'runtime-wheel-audit':('wheel_plans',),'assets-model-audit':('archive_plans',),'runtime-build-install':('runtime_inventory',)}.get(phase,())
    closed(value['outputs'],roles,'phase_accepted_output_roles')
    for ref in value['outputs'].values():reference(ref,4*MIB);read_bound(ref,4*MIB)
    reference(value['original_tool_completion'],16384)
    paths=[row['independent_acceptance']['path'],*(value[key]['path'] for key in ('config','guard_receipt','result','original_tool_completion')),*[ref['path'] for ref in value['outputs'].values()]]
    require(len(paths)==len(set(paths)),'phase_acceptance_reference_cycle')
    previous=strict_json(read_bound(row['config'],65536),65536);validate_config(previous)
    require(previous['invocation']['phase']==phase,'prior_phase_config_freeze')
    historical=source_revision(c,row,previous) if 'source_revision' in row else None
    require(historical is not None or previous['packet_sha256']==c['packet_sha256'],'prior_phase_config_freeze')
    guard=strict_json(read_bound(row['guard_receipt']),MIB)
    validate_guard_receipt(guard,guard['spec_sha256'],PHASES[phase][2])
    identity={key:guard['supervisor'][key] for key in ('pid','start_ticks','boot_id','pid_namespace')}
    require(identity==previous['invocation']['identity'],'prior_guard_config_tuple')
    spec_path=Path(row['config']['path']).parent/'spec.json'
    spec=json_file(spec_path,MIB,guard['spec_sha256'])
    require(same_data(spec,make_spec(previous,row['config']['path'],row['config']['sha256'],phase,historical=historical)) and same_data(guard['argv'],spec['argv']),'prior_exact_guard_spec')
    require(row['guard_receipt']['path']==str(Path(guard['run_directory'])/'receipt.json'),'prior_guard_receipt_path')
    tool=strict_json(read_bound(value['original_tool_completion'],16384),16384)
    closed(tool,('schema','phase','original_tool','guard_identity'),'phase_tool_completion_fields')
    require(tool['schema']=='portable-runtime-tool-completion-v1' and tool['phase']==phase and same_data(tool['guard_identity'],identity),'phase_tool_guard_tuple')
    original=tool['original_tool'];closed(original,('session_id','terminal','exit_code','owner_stop_or_signal'),'phase_original_tool_fields')
    integer(original['session_id'],1);integer(original['exit_code'])
    require(original['terminal'] is True and original['exit_code']==0 and original['owner_stop_or_signal'] is False,'phase_original_tool_reap')
    result=strict_json(read_bound(row['result']),MIB)
    if phase=='runtime-build-install':
        expected={'schema':'portable-runtime-build-result-v1','status':'VERIFIED_RUNTIME_BYTES','runtime_inventory':value['outputs']['runtime_inventory'],'outer_owner_terminal_required':True,'model_calls':0}
        require(same_data(previous['phase_args']['options'],c['phase_args']['options']),'accepted_build_host_mode')
        if previous['phase_args']['options']=={'host_verification':'CANDIDATE_BYTES_AND_OBSERVED_MAPPINGS'}:
            expected.update(host_verification='CANDIDATE_BYTES_AND_OBSERVED_MAPPINGS',static_native_closure_complete=False)
        require(same_data(result,expected),'prior_build_result')
        require(value['outputs']['runtime_inventory']==c['phase_args']['inputs']['runtime_inventory'],'accepted_runtime_inventory_link')
        for key in ('runtime','runtime_container'):require(previous['roots'][key]==c['roots'][key],'accepted_build_destination')
        require(previous['phase_args']['inputs']['host']==c['phase_args']['inputs']['host'],'accepted_build_host_closure')
    return value

def runtime_inventory(c):
    """Bind the import interpreter to an independently sealed complete runtime."""
    require(c['invocation']['phase']=='runtime-import','runtime_origin_phase')
    validate_phase_acceptance(c,'runtime-build-install')
    refs=c['phase_args']['inputs'];require(set(refs)=={'host','runtime_inventory','probe_configuration'},'runtime_import_inputs')
    value=strict_json(read_bound(refs['runtime_inventory'],4*MIB),4*MIB)
    closed(value,('schema','status','runtime_root','files','host_sha256','product_commit','outer_owner_terminal_required'),'runtime_inventory_fields')
    require(value['schema']=='portable-runtime-inventory-v1' and value['status']=='VERIFIED_RUNTIME_BYTES' and value['outer_owner_terminal_required'] is True,'runtime_inventory_state')
    root=path_value(c['roots']['runtime'])
    require(value['runtime_root']==str(root) and value['product_commit']==PRODUCT['commit'] and value['host_sha256']==refs['host']['sha256'],'runtime_inventory_origin_host_product')
    files=value['files'];require(type(files) is dict and 1<=len(files)<=16384,'runtime_inventory_count')
    total=0
    for name,pin in files.items():
        text_value(name);require(not name.startswith('/') and '\\' not in name and all(part not in ('','.','..') for part in name.split('/')),'runtime_inventory_relative_path')
        require(not any(part.endswith('.pth') or part.startswith(('sitecustomize','usercustomize')) for part in name.split('/')),'runtime_startup_hook')
        closed(pin,('bytes','sha256'),'runtime_file_pin_fields');integer(pin['bytes'],0,128*MIB);digest_value(pin['sha256']);total+=pin['bytes']
    require(total<=320*MIB and same_data(files.get('bin/python3.12'),PYTHON_PIN),'runtime_interpreter_seal')
    found=set();actual_dirs=set();expected_dirs={'.'}
    for name in files:expected_dirs.update(str(parent) for parent in Path(name).parents)
    charged=0;entries=0;end=c['invocation']['launch']['absolute_end_monotonic']-60
    def failed(error):raise error
    for base,dirs,names in os.walk(root,followlinks=False,onerror=failed):
        check_deadline(end)
        for path in [Path(base),*(Path(base)/name for name in names)]:
            info=path.lstat();entries+=1;require(entries<=16384 and info.st_dev==c['host']['filesystem_device'],'runtime_tree_scope')
            require(stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink==1),'runtime_tree_regular')
            charged+=max(info.st_size,info.st_blocks*512);require(charged<=320*MIB,'runtime_final_allocation')
            if stat.S_ISDIR(info.st_mode):actual_dirs.add(str(path.relative_to(root)))
            if path.is_file():
                name=str(path.relative_to(root));require(name in files,'runtime_unsealed_file')
                require(stat.S_IMODE(info.st_mode)==(0o755 if name=='bin/python3.12' else 0o644),'runtime_file_mode')
                require(pin_file(path,128*MIB,end)==files[name],'runtime_file_changed');found.add(name)
        for name in dirs:require(not (Path(base)/name).is_symlink(),'runtime_directory_alias')
    require(found==set(files) and actual_dirs==expected_dirs,'runtime_inventory_membership')
    return value

def load_config(path,sha,*,require_current=True):
    digest_value(sha)
    c=validate_config(json_file(path,65536,sha),current_identity=process_identity() if require_current else None)
    roots=c['roots'];phase=c['invocation']['phase'];launch=c['invocation']['launch']
    require(Path(roots['packet'])==HERE,'packet_config_location')
    require(Path(path)==Path(roots['work'])/'evidence/runtime-fresh-v8'/phase/'config.json','phase_config_location')
    if require_current:validate_launch(launch,phase)
    else:require(launch['start_monotonic']<=time.monotonic()<launch['absolute_end_monotonic'],'original_phase_deadline')
    for key,value in roots.items():path_value(value,exists=key not in ('runtime','runtime_container','proof_source'))
    require(Path(roots['workspace']).stat().st_dev==c['host']['filesystem_device'],'filesystem_device_changed')
    host_python=path_value(c['host']['python']);current_python=Path(sys.executable).resolve()
    runtime_python=Path(roots['runtime'])/'bin/python3.12'
    require(sys.version_info[:3]==(3,12,14) and (current_python==host_python or (not require_current and phase=='runtime-import' and current_python==runtime_python)),'current_interpreter_origin')
    require(pin_file(host_python)==PYTHON_PIN,'current_host_bytes')
    verify_packet()
    require(pin_file(HERE/'PACKET_MANIFEST.json',FILE_CEILINGS['PACKET_MANIFEST.json'])['sha256']==c['packet_sha256'],'packet_freeze_changed')
    require(pin_file(HERE/'runtime/runtime.lock.json',FILE_CEILINGS['runtime/runtime.lock.json'])['sha256']==c['runtime_lock_sha256'],'runtime_lock_changed')
    lock=json_file(HERE/'runtime/runtime.lock.json',FILE_CEILINGS['runtime/runtime.lock.json'],c['runtime_lock_sha256'])
    require(same_data(lock['acquisition_manifest'],{key:c['assets_manifest'][key] for key in ('bytes','sha256')}) and same_data(lock['product'],PRODUCT),'frozen_asset_manifest_product_binding')
    release=c['execution_release'];template=strict_json(read_bound(release['template'],65536),65536)
    go=strict_json(read_bound(release['go'],4096),4096)
    expected_go={'schema':'portable-runtime-phase-go-v1','decision':'RUN_ONCE','phase':phase,'packet_sha256':c['packet_sha256'],'config_template_sha256':release['template']['sha256']}
    require(same_data(go,expected_go),'exact_independent_phase_go')
    require(type(template) is dict and template.get('invocation') is None and template.get('execution_release') is None and template.get('status')=='PRIVATE_PHASE_TEMPLATE' and template.get('execution_authorized') is False,'reviewed_template_shape')
    template.update(invocation=c['invocation'],execution_release=release,status=c['status'],execution_authorized=True)
    require(same_data(template,c),'template_config_changed')
    acquired=c['acquisition'];require(acquired['owner_acceptance'] is not None and acquired['owner_terminal'] is not None,OWNER_ACCEPTANCE_PENDING)
    acceptance=strict_json(read_bound(acquired['owner_acceptance'],256*1024),256*1024)
    assets=strict_json(read_bound(c['assets_manifest'],98304),98304)
    validate_owner_acceptance(acceptance,assets['artifacts']);refs=acceptance['references']
    require(acquired['owner_acceptance']['path'] not in {ref['path'] for ref in refs.values()},'acceptance_graph_cycle')
    for key,role in (('config','acquisition_config'),('guard_receipt','download_guard'),('download_result','download_result'),('owner_terminal','coordinator_terminal')):
        require(acquired[key]==refs[role],'private_acquisition_binding')
    require(c['assets_manifest']==refs['assets_manifest'] and acquired['historical_identity']==acceptance['acquisition_identity'],'private_acquisition_tuple_manifest')
    for ref in refs.values():require(Path(ref['path']).is_relative_to(Path(roots['workspace'])),'private_evidence_scope')
    bound={name:read_bound(ref,256*1024) for name,ref in refs.items()}
    original_config=strict_json(bound['acquisition_config'])
    for key in ('runtime','runtime_container'):
        require(roots[key]==original_config['roots'][key],'accepted_runtime_reservation_changed')
    require_fresh_acquisition(strict_json(bound['download_guard']),strict_json(bound['download_result']),strict_json(bound['coordinator_terminal']),assets['artifacts'],refs['download_spec']['sha256'],acceptance=acceptance,bound_documents=bound)
    driver_module().validate_phase_args(phase,c['phase_args'],c)
    for ref in c['phase_args']['inputs'].values():read_bound(ref,4*MIB)
    for row in c['accepted_phases']:
        for key in ('config','guard_receipt','result','independent_acceptance'):read_bound(row[key])
    if phase=='runtime-import':runtime_inventory(c)
    return c

def make_spec(c,config_path,config_sha,phase,launch=None,*,historical=None):
    validate_config(c);digest_value(config_sha);lexical_path(str(config_path))
    require(type(phase) is str and phase in PHASES and c['invocation']['phase']==phase,'spec_phase')
    launch=c['invocation']['launch'] if launch is None else launch
    require(launch==c['invocation']['launch'],'spec_launch_binding')
    if historical is None:
        packet=verify_packet();packet_root=HERE;namespace='evidence/runtime-fresh-v8'
    else:
        require(phase=='runtime-build-install' and set(historical)=={'packet','root','namespace','references'},'historical_spec_scope')
        packet=historical['packet'];packet_root=historical['root'];namespace=historical['namespace']
        require(packet_root==Path(c['roots']['packet']) and namespace=='evidence/runtime-fresh-v6','historical_spec_root')
    external={str(packet_root/name):row['sha256'] for name,row in packet['files'].items()}
    external[str(packet_root/'PACKET_MANIFEST.json')]=c['packet_sha256'];external[str(config_path)]=config_sha
    claim=Path(config_path).parent/'claim.json'
    require(same_data(json_file(claim,4096),{'schema':'portable-single-dispatch-v1','phase':phase,'attempts':1}),'phase_single_attempt_claim')
    external[str(claim)]=pin_file(claim,4096)['sha256']
    for ref in (*c['execution_release'].values(),c['assets_manifest'],*(c['acquisition'][key] for key in ('config','guard_receipt','download_result','owner_terminal','owner_acceptance'))):
        require(ref is not None,OWNER_ACCEPTANCE_PENDING);external[ref['path']]=ref['sha256']
    acceptance=strict_json(read_bound(c['acquisition']['owner_acceptance'],256*1024),256*1024)
    for ref in acceptance['references'].values():external[ref['path']]=ref['sha256']
    for ref in c['phase_args']['inputs'].values():external[ref['path']]=ref['sha256']
    if phase=='runtime-build-install':
        allocation=strict_json(read_bound(c['phase_args']['inputs']['allocation'],4*MIB),4*MIB)
        if 'continuation' in allocation:
            continuation=allocation['continuation']
            closed(continuation,('schema','failure_seal','preservation','original_allocation'),'continuation_source_refs')
            require(continuation['schema']=='portable-runtime-build-continuation-v1','continuation_source_schema')
            for name in ('failure_seal','preservation','original_allocation'):
                ref=reference(continuation[name],4*MIB)
                require(ref['path'] not in external or external[ref['path']]==ref['sha256'],'continuation_source_conflict')
                external[ref['path']]=ref['sha256']
    for row in c['accepted_phases']:
        for key in ('config','guard_receipt','result','independent_acceptance'):external[row[key]['path']]=row[key]['sha256']
        if 'source_revision' in row:
            previous=strict_json(read_bound(row['config'],65536),65536)
            revision=source_revision(c,row,previous)
            for ref in revision['references']:
                require(ref['path'] not in external or external[ref['path']]==ref['sha256'],'source_revision_external_conflict')
                external[ref['path']]=ref['sha256']
    assets=strict_json(read_bound(c['assets_manifest'],98304),98304)
    stage,script,seconds=PHASES[phase]
    executable=c['host']['python'];runtime_files={executable:PYTHON_PIN['sha256']}
    if phase=='runtime-import':
        runtime_inventory(c);executable=str(Path(c['roots']['runtime'])/'bin/python3.12')
        runtime_files[executable]=PYTHON_PIN['sha256']
    return {'schema':3,'stage':stage,'absolute_seconds':seconds,'work_seconds':seconds-60,
      'address_space_cap_bytes':GIB,'rss_cap_bytes':256*MIB,'scratch_cap_bytes':12*GIB,
      'guard_sha256':packet['files']['acquisition_guard.py']['sha256'],'source_root':c['roots']['product'],
      'source_files':assets['source']['files'],'cwd':str(Path(c['roots']['work'])/namespace/phase),
      'external_files':external,'runtime_files':runtime_files,
      'config_path':str(config_path),'config_sha256':config_sha,'phase':phase,'launch':launch,
      'argv':[executable,'-I','-B','-S',str(packet_root/'guard_driver.py'),'--config',str(config_path),'--config-sha256',config_sha,'--phase',phase]}


def _resource_usage_once(c,end):
    roots=c['roots'];dev=c['host']['filesystem_device'];usage={k:0 for k in CAPS};count=0
    work=Path(roots['work']);source=(Path(roots['source_recovery']),Path(roots['packet']),Path(roots['proof_source']),Path(c['assets_manifest']['path']).parent)
    def category(p):
        if any(p==r or p.is_relative_to(r) for r in source):return 'source'
        r=Path(roots['runtime_container'])
        if p==r or p.is_relative_to(r):return 'runtime'
        for name,sub in (('acquisition','assets'),('proof','proof'),('actual','actual')):
            if p==work/sub or p.is_relative_to(work/sub):return name
        return 'evidence'
    def visit(p,root):
        nonlocal count
        check_deadline(end);count+=1;require(count<=32768,'charged_entry_cap')
        try:st=p.lstat()
        except FileNotFoundError:
            if p.parent==work/'assets/tmp' and re.fullmatch(r'[0-9a-f]{64}\.part',p.name):raise PromotionRace('known_part_promotion_rescan')
            if p.is_relative_to(work/'evidence/runtime-fresh-v8'/c['invocation']['phase']/'runs') and ('tmp' in p.parts or p.name.endswith('.tmp')):return
            raise
        require(st.st_dev==dev,'charged_filesystem_changed')
        require(stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode),'charged_special_file')
        if stat.S_ISLNK(st.st_mode):
            literal=os.readlink(p);target=p.resolve(strict=True)
            require(not Path(literal).is_absolute() and len(literal)<=4096 and target.is_relative_to(root) and target.is_file(),'charged_alias_escape')
        usage[category(p)]+=max(st.st_size,st.st_blocks*512)
        if stat.S_ISDIR(st.st_mode):
            with os.scandir(p) as entries:
                for entry in entries:visit(Path(entry.path),root)
    candidates=[Path(roots[key]) for key in ('source_recovery','work','runtime_container','proof_source')]+[Path(c['assets_manifest']['path']).parent]
    for root in dict.fromkeys(candidates):
        if any(root!=other and root.is_relative_to(other) for other in candidates):continue
        if not root.exists():require(root in (Path(roots['runtime_container']),Path(roots['proof_source'])),'charged_root_missing');continue
        require(root.is_dir() and not root.is_symlink(),'charged_root_type');visit(root,root)
    for name,total in usage.items():require(total<=CAPS[name],'category_cap_'+name)
    require(sum(usage.values())+MIB<=12*GIB,'global_disk_cap')
    return usage


def resource_usage(c,end):
    for attempt in range(3):
        check_deadline(end)
        try:return _resource_usage_once(c,end)
        except PromotionRace:
            if attempt==2:raise Refused('promotion_rescan_exhausted')
    raise Refused('unreachable_census')


def admission(c,end,prospective=False):
    validate_config(c);require(prospective is False,'no_download_phase_in_runtime_draft')
    usage=resource_usage(c,end);total=sum(usage.values())
    mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    available=int(mem['MemAvailable'].split()[0])*1024
    fs=os.statvfs(c['roots']['workspace']);free=fs.f_bavail*fs.f_frsize
    required=LIMITS['ordinary_free_floor_bytes']+LIMITS['global_disk_bytes']-total
    require(available>=LIMITS['minimum_available_bytes'],'host_available_floor')
    require(free>=required,'free_floor_with_remaining_global_reservation')
    require(3 in os.sched_getaffinity(0),'cpu3_unavailable')
    return {'charged_by_category':usage,'charged_bytes':total,'free_bytes':free,'available_bytes':available,'required_free_bytes':required}

def main():
    print(json.dumps({'status':'REFUSED_BEFORE_DISPATCH',
      'reason':'SOURCE_ONLY_DRAFT_UNADMITTED'},sort_keys=True),file=sys.stderr)
    return 2

if __name__=='__main__':
    raise SystemExit(main())
