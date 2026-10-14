# Unexecuted portable template; generate configuration placeholders before use.
"""Non-spawning launch boundary. The process supervisor retains the original Popen.

The generated configuration and live process-supervisor identity must match the
run contract before this module returns one exact argv. It never spawns a process.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from binding import load_actual


def authorized_argv(config_path, check=lambda:None):
    """Non-spawning projection; bootstrap separately verifies live owner admission."""
    from binding import load_config, validate_authority
    raw,config=load_config(config_path,os.environ.get('FRESH_SEAM_CONFIG_SHA256'),check,ready=True)
    authority_path=os.environ.get('FRESH_SEAM_AUTHORITY')
    require(authority_path is not None,'actual_authority_required')
    ordinary_path(authority_path,check);authority_raw=bounded_read(authority_path,4096,check)
    require(sha(authority_raw)==os.environ.get('FRESH_SEAM_AUTHORITY_SHA256'),'authority_pin_changed')
    validate_authority(decode(authority_raw),raw,config)
    return [config['paths']['python'], '-I', '-B', '-S',
            str(Path(config['paths']['harness_root'])/'bootstrap.py'), '--mode', 'controller',
            '--spec', str(config_path)]


# Fixed prerequisite readers are kept beside the launch boundary, with no dispatch.
import os
from binding import (MIB, SOURCE_HEAD, SOURCE_WAV_SHA, LIMITS, Refused, canonical, require, sha,
    read_pin, read_source_pin, subject_digest, exact, rows_map, validate_input_pins,
    decode, bounded_read, ordinary_path)

def verify_physical_prerequisite(config,kind,check):
    accepted=read_pin(config['prerequisites'][kind],check)
    require(set(accepted)==({'schema','kind','status','subject_sha256','entries','proof_config','review'} |
            ({'prior_helper_setup'} if kind=='cell_store' else set())) and
        accepted['schema']=='fresh-seam-prerequisite-acceptance-v1' and accepted['kind']==kind and
        accepted['status']=='ACCEPTED' and accepted['subject_sha256']==subject_digest(config),'prerequisite_acceptance')
    entries=accepted['entries'];want=['setup','regression','physical'] if kind=='cell_store' else ['decoder_only']
    require(type(entries) is list and [r.get('phase') for r in entries]==want,'prerequisite_exact_phases')
    review=read_pin(accepted['review'],check)
    require(set(review)=={'schema','status','subject_sha256','kind','entries_sha256','import_guard_relabelled'} and
        review['schema']=='fresh-seam-prerequisite-review-v1' and review['status']=='ACCEPTED' and
        review['subject_sha256']==accepted['subject_sha256'] and review['kind']==kind and
        review['entries_sha256']==sha(canonical(entries)) and review['import_guard_relabelled'] is False,
        'independent_prerequisite_review')
    outcomes={}
    for entry in entries:
        require(set(entry)=={'phase','receipt','owner_source','completion'},'phase_entry_fields')
        read_source_pin(entry['owner_source'],check)
        phase=read_pin(entry['receipt'],check,32768); name=entry['phase']
        require(phase.get('schema')==('cellstore-repair-owner-phase-v1' if kind=='cell_store' else
                'cellstore-decoder-owner-phase-v1') and phase.get('phase')==name and
            phase.get('status')=='ACCEPTED_PHASE_PENDING_OUTER_TOOL_COMPLETION' and
            phase.get('outer_original_tool_terminal_required') is True and
            phase.get('original_import_outer_guard_status')=='FAIL_CLOSED' and
            phase.get('original_import_final_source_pin_verified') is False,'phase_scope')
        if name=='decoder_only':
            require(entry['receipt']['sha256']=='@GENERATED_DECODER_RESULT_SHA256@' and
                entry['owner_source']['sha256']=='@GENERATED_DECODER_SOURCE_SHA256@' and
                {k:phase['source'][k] for k in ('bytes','sha256')}=={k:entry['owner_source'][k] for k in ('bytes','sha256')} and
                phase.get('no_setup_or_physical_rerun') is True and phase.get('physical_status_preserved')=='FAILED' and
                phase.get('frozen_owner_source')=={'bytes':int('@GENERATED_PRIOR_STORAGE_SOURCE_BYTES@'),'sha256':'@GENERATED_PRIOR_STORAGE_SOURCE_SHA256@'},
                'completed_decoder_v2_exact_binding')
        else:
            require(entry['owner_source']['sha256']=='@GENERATED_STORAGE_REPAIR_SOURCE_SHA256@' and
                {k:phase['source'][k] for k in ('bytes','sha256')}=={k:entry['owner_source'][k] for k in ('bytes','sha256')} and
                phase.get('prior_failed_physical_preserved') is True and phase.get('helper_cases_rerun')==0 and
                phase.get('production_qualification_changed') is False and
                phase.get('prior_original_completion',{}).get('session_id')==int('@GENERATED_PRIOR_PROCESS_SESSION_ID@') and
                phase['prior_original_completion']['exit_code']==2,'repaired_physical_phase_source_scope')
        exact(phase.get('limits'),{'work_seconds':540,'cleanup_seconds':60,'total_seconds':600,'cpu':3,
            'nice':19,'sampled_rss_bytes':512*MIB,'address_space_bytes':1024*MIB},'phase_limits')
        commands=phase.get('commands');expected_count={'setup':2,'regression':1,'physical':1,'decoder_only':4}[name]
        require(type(commands) is list and len(commands)==expected_count and all(
            row.get('original_handle_retained') is True and row.get('positive_wait') is True and
            type(row.get('returncode')) is int and row['returncode']==0 and row.get('cleanup')=='DIRECT_ORIGINAL_REAPED' and
            'failure' not in row and not row.get('output_truncated_failure') for row in commands), 'positive_original_child_waits')
        completion=read_pin(entry['completion'],check,32768)
        if name=='decoder_only':
            require(entry['completion']['sha256']=='@GENERATED_DECODER_COMPLETION_SHA256@' and
                completion.get('schema')=='owner-decoder-acceptance-v1' and
                completion.get('status')=='ACCEPTED_DECODER_FUNCTIONAL_ONLY' and
                completion.get('all_original_commands_exit_zero_and_reaped') is True and
                completion.get('model_execution_authorized') is False and
                completion.get('original_tool',{}).get('positive_terminal') is True and
                completion['original_tool']['exit_code']==0 and completion['original_tool']['session_id']==int('@GENERATED_DECODER_PROCESS_SESSION_ID@') and
                {k:completion['receipt'][k] for k in ('path','bytes','sha256')}==entry['receipt'] and
                {k:completion['source'][k] for k in ('path','bytes','sha256')}==entry['owner_source'],
                'decoder_original_tool_positive_completion')
        else:
            require(set(completion)=={'schema','terminal','exit_code','original_session_id','original_handle_waited',
                'cleanup','owner_source_sha256','phase_receipts'} and
                completion['schema']=='fresh-seam-original-tool-completion-v1' and completion['terminal'] is True and
                completion['original_handle_waited'] is True and completion['cleanup']=='CONFIRMED' and
                type(completion['original_session_id']) is int and completion['original_session_id']>0 and
                completion['owner_source_sha256']==entry['owner_source']['sha256'] and
                entry['receipt'] in completion['phase_receipts'] and type(completion['exit_code']) is int and
                completion['exit_code']==0 and completion['original_session_id']==int('@GENERATED_STORAGE_PROCESS_SESSION_ID@'), 'original_tool_completion')
        # Repaired phases require their own exit0; the preserved old combined exit remains2.
        outcomes[name]=phase['outcome']
    if kind=='cell_store':
        cfg=read_pin(accepted['proof_config'],check,65536)
        require(cfg.get('schema')=='portable-cell-store-proof-config-v1' and cfg.get('product_commit')==SOURCE_HEAD and
            cfg.get('product_root')==config['paths']['source_root'] and cfg.get('interpreter')==config['paths']['python'],
            'physical_proof_config_identity')
        prior_ref=accepted['prior_helper_setup'];prior=read_pin(prior_ref,check,32768)
        require(prior_ref['sha256']=='@GENERATED_STORAGE_HELPER_RESULT_SHA256@' and
            prior.get('schema')=='cellstore-decoder-owner-phase-v1' and prior.get('phase')=='setup' and
            prior.get('status')=='ACCEPTED_PHASE_PENDING_OUTER_TOOL_COMPLETION' and
            prior.get('outcome',{}).get('helper_cases_passed')==48 and len(prior.get('commands',[]))==3 and
            all(row.get('positive_wait') is True and type(row.get('returncode')) is int and row['returncode']==0 and
                row.get('cleanup')=='DIRECT_ORIGINAL_REAPED' for row in prior['commands']), 'retained_48_helper_setup')
        setup=outcomes['setup'];physical=outcomes['physical'];regression=outcomes['regression']
        require(setup.get('helper_cases_rerun')==0 and setup.get('prior_helper_cases_passed')==48 and
            setup.get('config',{}).get('sha256')==accepted['proof_config']['sha256'] and
            setup.get('new_driver')=={'bytes':int('@GENERATED_STORAGE_DRIVER_SOURCE_BYTES@'),'sha256':'@GENERATED_STORAGE_DRIVER_SOURCE_SHA256@'},
            'fresh_repaired_setup')
        require(regression.get('cap_bytes')==64*MIB and regression.get('result',{}).get('status')=='REGRESSION_PASSED' and
            regression['result'].get('cleanup_confirmed') is True and
            regression['result'].get('physical_qualification') is False and
            regression['result'].get('receipt_sha256')==regression.get('receipt',{}).get('sha256'),'near_cap_regression_binding')
        termination={'kill_sent':True,'returncode':-9,'reaped':True}
        require(physical.get('driver_status')=='CORROBORATED_MATCH' and
            physical.get('original_writer_termination')==termination and physical.get('physical_cap_bytes')==193*MIB and
            physical.get('writer_count_owned_by_driver')==1 and physical.get('power_loss_claim') is False,'physical_outcome')
        ref={'path':physical['driver_receipt_path'],**{k:physical['driver_receipt'][k] for k in ('bytes','sha256')}}
        require(ref['sha256']=='@GENERATED_STORAGE_PHYSICAL_RESULT_SHA256@','fresh_v2_physical_receipt')
        driver=read_pin(ref,check,262144)
        require(driver.get('status')=='CORROBORATED_MATCH' and driver.get('fresh_evidence') is True and
            driver.get('production_qualification_changed') is False and driver.get('runtime_matches_shipped') is True and
            driver.get('termination')==termination and driver.get('config_sha256')==accepted['proof_config']['sha256'] and
            driver.get('claims',{}).get('power_loss') is False and
            driver.get('capture',{}).get('observed_changed_page_one') is True and
            driver.get('journal_audit',{}).get('all_checksums_valid') is True and
            driver.get('journal_audit',{}).get('all_pages_match_baseline') is True and
            driver['recovery']['database']==driver['baseline']['database'] and
            driver['recovery']['oracle']==driver['baseline']['oracle'],'raw_physical_driver_receipt')
    else:
        require(accepted['proof_config'] is None,'decoder_has_no_physical_config')
        result=outcomes['decoder_only'];source=result.get('source_wave',{})
        require(result.get('frames')==336000 and result.get('pcm_bytes')==672000 and
            result.get('pcm_sha256')==config['input_pins']['source']['pcm16_sha256'] and
            source.get('bytes')==672044 and source.get('sha256')==SOURCE_WAV_SHA and
            result.get('provider_calls')==0 and result.get('one_extra_frame_requested_eof_check') is True and
            result.get('full_static_dynamic_library_closure_claimed') is False,'decoder_real_outcome')
        rows=rows_map(config['inventory']['hash_inventory'])
        for name in ('ffmpeg','ffprobe'):
            observed=result['versions'][name]['binary'];path=config['paths'][name]
            expected_version={'ffmpeg':'@GENERATED_FFMPEG_VERSION_OUTPUT_SHA256@',
                'ffprobe':'@GENERATED_FFPROBE_VERSION_OUTPUT_SHA256@'}[name]
            require(result['versions'][name]['version_stdout']['sha256']==expected_version,'exact_decoder_version_stdout')
            require(rows.get(path)=={k:observed[k] for k in ('bytes','sha256')},'decoder_binary_inventory_binding')


def verify_gate_receipt(config,stage,check):
    value=read_pin(config['prerequisites'][stage],check)
    require(set(value)=={'schema','stage','status','subject_sha256','cleanup','original_tool_exit_code',
        'original_handle_waited','result'} and value['schema']=='fresh-seam-gate-receipt-v1' and
        value['stage']==stage and value['status']=='ACCEPTED' and value['subject_sha256']==subject_digest(config) and
        value['cleanup']=='CONFIRMED' and value['original_tool_exit_code']==0 and
        value['original_handle_waited'] is True,'fresh_gate_receipt')
    result=value['result']
    if stage=='split':
        require(set(result)=={'input_descriptor','source_frames','split_frames','input_pins','output_paths',
            'outputs','output_readback','concatenated_pcm_equal','split_wav_digests_distinct','actual_execution_authorized'},
            'split_result_fields')
        exact(result['input_descriptor'],config['input_descriptor'],'split_input_descriptor')
        require(result['source_frames']==[0,336000] and result['split_frames']==[[0,168000],[168000,336000]] and
            result['input_pins']==validate_input_pins(config['input_pins']) and result['output_readback'] is True and
            result['concatenated_pcm_equal'] is True and result['split_wav_digests_distinct'] is True and
            result['actual_execution_authorized'] is False,'six_role_split_binding')
        paths={role:config['paths']['upload_'+role] for role in ('a','b')}
        exact(result['output_paths'],paths,'split_output_paths')
        expected=[{'path':paths[role],'bytes':336044,'sha256':config['input_pins']['upload_'+role]['wav_sha256']}
                  for role in ('a','b')]
        exact(result['outputs'],expected,'split_exact_two_outputs')
    elif stage=='fixtures':
        require(set(result)=={'schema','label','tests_run','failures','errors','skips','ids','actual_acoustic_calls',
            'historical_runs_adopted','harness_files'} and result['schema']=='fresh-seam-model-free-result-v1' and
            result['label']=='EXPLICIT_SYNTHETIC_MODEL_FREE' and result['tests_run']==42 and
            result['failures']==result['errors']==result['skips']==result['actual_acoustic_calls']==0 and
            result['historical_runs_adopted'] is False and type(result['ids']) is list and
            len(result['ids'])==len(set(result['ids']))==42 and
            result['harness_files']==config['inventory']['harness_files'],'fresh_42_group_gate')
        manifest=decode(bounded_read(Path(config['paths']['harness_root']).parent/'SOURCE_MANIFEST.json',8192,check))
        exact(result['ids'],manifest['fixture_ids'],'exact_42_group_ids')
    else:
        rows=config['inventory']['hash_inventory']
        require(set(result)=={'all_matched','inventory_sha256','files','source_files','input_files','input_uploads'} and
            result['all_matched'] is True and result['inventory_sha256']==sha(canonical(sorted(rows,key=lambda r:r['path']))) and
            result['files']==len(rows) and result['source_files']==351 and result['input_files']==3 and
            result['input_uploads']==2,'fresh_inventory_gate')


def verify_live_owner(config,raw,owner_source,check):
    from ownership import proc,key
    ref_path=os.environ.get('FRESH_SEAM_OUTER_INDEX');require(ref_path is not None,'outer_original_index_required')
    ordinary_path(ref_path,check);index_raw=bounded_read(ref_path,16384,check)
    require(sha(index_raw)==os.environ.get('FRESH_SEAM_OUTER_INDEX_SHA256'),'outer_index_pin')
    index=decode(index_raw)
    require(set(index)=={'schema','status','config_sha256','authority_sha256','owner_source_sha256','run_directory',
        'original','supervisor','argv','origin','work_end','absolute_end'} and
        index['schema']=='fresh-seam-original-invocation-v1' and index['status']=='GATED' and
        index['config_sha256']==sha(raw) and index['authority_sha256']==os.environ.get('FRESH_SEAM_AUTHORITY_SHA256') and
        index['owner_source_sha256']==owner_source['sha256'] and index['run_directory']==config['paths']['output_dir'],
        'fresh_original_invocation_binding')
    exact(index['argv'],[config['paths']['python'],'-I','-B','-S',
        str(Path(config['paths']['harness_root'])/'bootstrap.py'),'--mode','controller','--spec',
        os.environ.get('FRESH_SEAM_CONFIG')],'outer_exact_controller_argv')
    require(index['absolute_end']==index['origin']+1800 and index['work_end']==index['origin']+1740 and
        float(os.environ['GUARD_ABSOLUTE_END'])==index['absolute_end'] and
        float(os.environ['GUARD_WORK_END'])==index['work_end'] and
        os.environ['GUARD_RUN_DIRECTORY']==index['run_directory'] and
        os.environ['GUARD_STOP_PATH']==str(Path(index['run_directory'])/'STOP'),'outer_immutable_deadlines')
    require(key(proc(index['supervisor']['pid']))==key(index['supervisor']) and
            key(proc(index['original']['pid']))==key(index['original']),'live_original_owner_identity')
    current=proc(os.getpid())
    for _ in range(32):
        if key(current)==key(index['original']):break
        require(current['ppid']>1,'bootstrap_outside_original_tree');current=proc(current['ppid'])
    else:raise Refused('bootstrap_ancestry_bound')
    require(current['ppid']==index['supervisor']['pid'],'original_owner_parent_changed')



def preparation_child():
    """Same original PID after the unchanged Guard gate; fixture/split only."""
    import sys,runpy
    require(len(sys.argv)==5 and sys.argv[1]=='--preparation-child' and
        sys.argv[2] in ('fixtures','split'),'closed_preparation_child_arguments')
    mode,config_path,config_sha=sys.argv[2:]
    from binding import load_config
    _,config=load_config(config_path,config_sha)
    require(not any(k.startswith('FRESH_SEAM_') for k in os.environ),'no_inherited_preparation_or_actual_authority')
    run=Path(os.environ['GUARD_RUN_DIRECTORY'])
    require(run.parent==Path(config['paths']['harness_root']).parent/'runs' and run.resolve()==run,'preparation_run_scope')
    index=decode(bounded_read(run/'index.json',32768))
    authority_path=run/('fixture-authority.json' if mode=='fixtures' else 'split-authority.json')
    ordinary_path(authority_path);raw=bounded_read(authority_path,16384);authority=decode(raw)
    from ownership import proc,key
    current=proc(os.getpid())
    require(index.get('status')=='GATED' and key(index['original'])==key(current) and
        current['ppid']==index['supervisor']['pid'] and key(proc(current['ppid']))==key(index['supervisor']) and
        key(authority['original'])==key(current) and authority['config_sha256']==config_sha,
        'same_original_preparation_gate')
    prefix='FRESH_SEAM_FIXTURE' if mode=='fixtures' else 'FRESH_SEAM_SPLIT'
    os.environ.update(FRESH_SEAM_CONFIG=config_path,FRESH_SEAM_CONFIG_SHA256=config_sha)
    os.environ[prefix+'_AUTHORITY']=str(authority_path);os.environ[prefix+'_AUTHORITY_SHA256']=sha(raw)
    target=Path(config['paths']['harness_root'])/('test_binding.py' if mode=='fixtures' else 'capture.py')
    sys.argv=[str(target)]
    runpy.run_path(str(target),run_name='__main__')


if __name__ == '__main__':
    preparation_child()
