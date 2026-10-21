# PUBLIC SOURCE INSPECTION ONLY. This is not a runnable distribution.
"""Bounded no-retry continuation from a pinned stopped checkpoint."""

raise RuntimeError("Inspection-only transformed excerpt: do not execute or import; private dependencies and pins are intentionally omitted.")

import hashlib,json,os,time
from pathlib import Path
from passive import compare
from product_transition import mapped,pinned_json,validate as validate_product_transition

def require(ok,reason):
    if not ok:raise RuntimeError(reason)

def validate(release):
    r=release['resume'];p=release['plan']
    for key in ('previous_release','previous_launch','previous_terminal','baseline_receipt','completed_manifest','original_launch','clock_evidence','recovered_history','accounting_evidence'):
        require(r[key] in release['source_pins'],'Missing conserved recovery pin: '+key)
    require(release.get('measurement_protocol')=='visible-pid-namespace-rss-v1','Revised measurement protocol must be explicit')
    history=pinned_json(release,r['recovered_history'])
    require(history['status']=='historical_lower_bounds_not_terminal' and history['interrupted_owner_cleanup']=='UNKNOWN' and history['original_wait_available'] is False and history['historical_request_accounting']=='UNKNOWN','Lost executor history cannot imply completion')
    require(history['observer_requests_lower_bound']>=REDACTED_COORDINATE_A73B320DC0 and history['decoded_bytes_lower_bound']>=REDACTED_COORDINATE_64715B3C02,'Historical lowerbounds regressed')
    require(r['historical_observation_lower_bounds']==dict(requests=history['observer_requests_lower_bound'],decoded_bytes=history['decoded_bytes_lower_bound']),'Historical lowerbounds must match pinned evidence')
    accounting=pinned_json(release,r['accounting_evidence'])
    old=pinned_json(release,r['previous_release'])
    prior_history=pinned_json(release,old['resume']['recovered_history'])
    prior_accounting=pinned_json(release,old['resume']['accounting_evidence'])
    run23=pinned_json(release,old['resume']['previous_release'])
    run23_history=pinned_json(release,run23['resume']['recovered_history'])
    run23_accounting=pinned_json(release,run23['resume']['accounting_evidence'])
    run20=pinned_json(release,run23['resume']['previous_release'])
    run20_history=pinned_json(release,run20['resume']['recovered_history'])
    run20_accounting=pinned_json(release,run20['resume']['accounting_evidence'])
    run17=pinned_json(release,run20['resume']['previous_release'])
    require(Path(old['output']).name=='prior-segment-output-F' and Path(run23['output']).name=='prior-segment-output-E'
            and Path(run20['output']).name=='prior-segment-output-D'
            and Path(run17['output']).name=='prior-segment-output-C','Fixed lost-run accounting lineage changed')
    require_accounting(pinned_json(release,run17['resume']['recovered_history']),
                       pinned_json(release,run17['resume']['accounting_evidence']),r['observation_allowance'])
    terminal=pinned_json(release,run20_accounting['terminal'])
    wait=pinned_json(release,run20_accounting['root_wait'])
    require(pinned_json(release,r['previous_terminal'])==terminal,'Previous terminal differs from accounting terminal')
    require_terminal_accounting(run20_history,run20_accounting,r['observation_allowance'],terminal,wait,run17)
    require_loss20_accounting(run23_history,run23_accounting,r['observation_allowance'],run20_history,run20)
    require_loss23_accounting(prior_history,prior_accounting,r['observation_allowance'],run23_history,run23)
    require_loss25_accounting(history,accounting,r['observation_allowance'],prior_history,old)
    require(accounting['source_release']==r['previous_release'],'Accounting release provenance changed')
    require(accounting['prior_history']==old['resume']['recovered_history']
            and accounting['prior_accounting']==old['resume']['accounting_evidence']
            and accounting['source_output']==old['output'],'Lost-run accounting anchor changed')
    require(history['latest_accounting_evidence']==dict(path=r['accounting_evidence'],
                sha256=release['source_pins'][r['accounting_evidence']]),'History accounting provenance changed')
    for key in ('source_release','prior_history','prior_accounting','baseline_phase1_receipt'):
        require(accounting['source_pins'].get(accounting[key])==release['source_pins'].get(accounting[key])
                and accounting[key] in accounting['source_pins'],'Accounting provenance pin missing: '+key)
    require(type(r['previous_status_rounds']) is int and r['previous_status_rounds']==accounting['status_rounds_charge'],'Status rounds must carry terminal-derived conservative bound')
    require(r['last_sample_at']==accounting['last_sample_at'],'Latest sample accounting changed')
    for path,sha in accounting['source_pins'].items():
        require(release['source_pins'].get(path)==sha,'Accounting evidence missing or changed')
    for path,sha in history['source_pins'].items():
        require(release['source_pins'].get(path)==sha,'Historical evidence missing or changed')
    launch=pinned_json(release,r['original_launch'])
    require(r['term_deadline_unix']==launch['term_deadline_unix'] and r['kill_ceiling_unix']==launch['kill_ceiling_unix'],'Original absolute deadlines changed')
    for key in ('original_launch','term_deadline_unix','kill_ceiling_unix','started_lower','started_upper',
                'schedule_offset','session_id','capture_id','restart_receipt','restart_receipt_sha256'):
        require(r[key]==old['resume'][key],'Original recovery invariant changed: '+key)
    require(r['historical_positive_stop'] is False,'Historical cleanup remains unknown')
    old_installed,new_installed=validate_product_transition(release,old)
    require_inherited_disk_capacity(release,old,run23)
    for key in ('run_root','database','inputs_manifest','monitor','qualified_smoke'):
        require(release[key]==old[key],'Recovery target/pin changed: '+key)
    for key,value in old['controls']['environment'].items():
        if key not in ('HOME','TMPDIR'):require(release['controls']['environment'].get(key)==mapped(value,old_installed,new_installed),'Recovery environment changed: '+key)
    require(set(release['controls']['environment'])==set(old['controls']['environment']),'Recovery environment keys changed')
    require(release['controls']['environment']['HOME']==release['output'] and release['controls']['environment']['TMPDIR']==str(Path(release['output'])/'tmp'),'Recovery environment paths')
    require(set(release['controls'])==set(old['controls']),'Recovery control keys changed')
    for key,value in old['controls'].items():
        if key not in ('environment','server_argv'):
            require(release['controls'][key]==('0.2.0rc3.dev66' if key=='package_version' else mapped(value,old_installed,new_installed)),'Recovery control changed: '+key)
    require(p['session_id']==r['session_id'] and p['capture_id']==r['capture_id'],'Recovery identity mismatch')
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==release['controls']['boot_id'],'Recovery boot changed')
    require(0<=r['started_upper']-r['started_lower']<=REDACTED_COORDINATE_FCAA871F3B,'Clock reconstruction interval')
    clock=pinned_json(release,r['clock_evidence'])
    for key in ('started_lower','started_upper','schedule_offset','previous_status_rounds','last_sample_at'):
        require(r[key]==clock[key],'Clock/counter evidence mismatch: '+key)
    require(abs(r['schedule_offset']-(REDACTED_COORDINATE_BFB321F43B-480))<=REDACTED_COORDINATE_20BBF3CD4B,'Original schedule offset')
    require(0<r['outer_timeout_seconds']<=86280,'Recovery outer duration')
    require(time.time()+r['outer_timeout_seconds']<=r['term_deadline_unix'],'Recovery would extend TERM')
    require(r['kill_ceiling_unix']==r['term_deadline_unix']+120,'Original kill reserve')
    baseline=pinned_json(release,r['baseline_receipt'])
    require(baseline['status']=='complete' and baseline['before']==baseline['after'] and baseline['changed']==[],'Stopped baseline conservation')
    require(baseline['exclusive_locks_acquired']==2 and baseline['historical_positive_stop'] is False,'Lost executor history must remain unknown')
    require(r['observation_allowance']==dict(historical_accounting='UNKNOWN',segment_get_cap=8192,segment_decoded_cap=268435456,individual_body_cap=524288,individual_seconds=5),'Explicit bounded recovered observation allowance')
    prior_baseline=pinned_json(release,old['resume']['baseline_receipt'])
    run23_baseline=pinned_json(release,run23['resume']['baseline_receipt'])
    require_loss20_baseline(run23_baseline,terminal,wait)
    require_loss23_baseline(prior_baseline,terminal,wait,run23_baseline)
    require_baseline16_provenance(old,prior_baseline,prior_accounting,run23)
    require_loss25_baseline(baseline,terminal,wait,prior_baseline)
    require_baseline17_provenance(release,baseline,accounting,old)
    require(release['source_pins'][r['completed_manifest']]==baseline['completed_manifest_sha256'],
            'Current completed manifest pin differs from stopped baseline')
    require(baseline['lost_run20_release']==dict(path=run23['resume']['previous_release'],sha256=release['source_pins'][run23['resume']['previous_release']]),
            'Baseline lost-run release provenance changed')
    for key in ('lost_run20_release','lost_run20_launch','retained_root_artifact'):
        pin=baseline[key]
        require(set(pin)=={'path','sha256'} and release['source_pins'].get(pin['path'])==pin['sha256'],
                'Lost-run baseline provenance pin missing: '+key)
    require(type(r['completed_inputs']) is int and r['completed_inputs']==74 and r['resume_input_id']=='repeated-prefix-075'
            and r['boundary_kind']=='partial' and r['restart_already_done'] is True,'Exact stopped partial075 continuation required')
    j=baseline['journal']
    require_checkpoint(j,j)
    from endurance import Input,resume_boundary
    metadata=json.loads(Path(release['inputs_manifest']).read_bytes())['inputs']
    inputs=tuple(Input(row['index'],row['path'],tuple(row['signature']),row['sha256']) for row in metadata)
    resume_boundary(inputs,r,baseline)
    require_restart(r,baseline,p['restart_after'])
    if r['restart_already_done']:
        require(release['source_pins'].get(r['restart_receipt'])==r['restart_receipt_sha256'],'Restart receipt pin missing')
        restart=json.loads(Path(r['restart_receipt']).read_bytes())
        for path,sha in restart['source_pins'].items():
            require(release['source_pins'].get(path)==sha,'Restart evidence pin missing')
    require(r['retry_post_allowed'] is False,'Retry POST must remain prohibited')
    require(baseline['recovered']['chunk']['id']==FAILED_CHUNK and baseline['recovered']['chunk']['status']=='completed' and baseline['recovered']['chunk']['next_offset']==REDACTED_COORDINATE_80B8062FEF and baseline['recovered']['failed_count']==0,'Completed former failure proof')
    c=baseline['completed']
    require(all(type(c[k]) is int and c[k]>=0 for k in ('completed','result_rows','origin','end','eos')) and c['completed']>0 and c['origin']==p['origin'] and c['end']==p['origin']+j['published_frames'] and c['eos']==0,'Stopped completed baseline geometry')
    return baseline



def require_inherited_disk_capacity(release,old,run23):
    """Continue run25's approved 10 GiB envelope without another amendment."""
    require(Path(old['output']).name=='prior-segment-output-F' and Path(run23['output']).name=='prior-segment-output-E',
            'Fixed disk amendment lineage changed')
    require_disk_capacity_amendment(old,run23)
    for path,sha in old['source_pins'].items():
        require(release['source_pins'].get(path)==sha,'Inherited disk amendment evidence lost or changed')
    require(set(release['plan'])==set(old['plan']) and all(type(release['plan'][key]) is type(value)
            and release['plan'][key]==value for key,value in old['plan'].items())
            and release['plan']['disk_bytes']==10737418240,'Inherited 10 GiB plan cannot change')
    require(release['disk_capacity_amendment']==old['disk_capacity_amendment']
            and release['disk_completion_scope']==old['disk_completion_scope']
            and release['disk_measurement_protocol']==old['disk_measurement_protocol'],
            'Historical disk amendment and bounded scope must remain unchanged')
    protocol=str(Path(__file__).parent/'disk-protocol.json')
    previous=str(Path(release['resume']['previous_release']).parent.parent/'disk-protocol.json')
    require(release['source_pins'].get(previous)==old['source_pins'].get(previous)
            and release['source_pins'].get(protocol)==old['source_pins'].get(previous),
            'Inherited disk protocol bytes must retain the qualified pin')
    require(pinned_json(release,protocol)==pinned_json(release,previous),
            'Inherited disk protocol changed')
    for key in ('term_deadline_unix','kill_ceiling_unix'):
        require(release['resume'][key]==old['resume'][key],'Original disk amendment deadline changed')


def require_disk_capacity_amendment(release,old):
    """Only the separately pinned 8-to-10 GiB protocol amendment changes the plan."""
    p=release['plan'];previous=old['plan']
    require(set(p)==set(previous),'Recovery plan keys changed')
    require(type(previous['disk_bytes']) is int and previous['disk_bytes']==8589934592
            and type(p['disk_bytes']) is int and p['disk_bytes']==10737418240,
            'Only the explicit 8-to-10 GiB disk amendment is qualified')
    for key,value in previous.items():
        if key!='disk_bytes':
            require(type(p[key]) is type(value) and p[key]==value,'Recovery plan changed: '+key)
    amendment=release.get('disk_capacity_amendment')
    required={'protocol_document','prior_disk_bytes','new_disk_bytes','prior_completion_verdict',
              'completion_capacity_proven','all_originals_retained'}
    require(isinstance(amendment,dict) and set(amendment)==required,
            'Explicit pinned disk capacity amendment required')
    values=dict(prior_disk_bytes=8589934592,new_disk_bytes=10737418240,
                prior_completion_verdict='capacity_limited_incomplete',
                completion_capacity_proven=False,all_originals_retained=True)
    for key,value in values.items():
        require(type(amendment[key]) is type(value) and amendment[key]==value,
                'Disk amendment scope changed: '+key)
    pin=amendment['protocol_document']
    require(isinstance(pin,dict) and set(pin)=={'path','sha256'}
            and release['source_pins'].get(pin['path'])==pin['sha256'],
            'Disk amendment document pin missing')
    document=pinned_json(release,pin['path'])
    require(set(document)==set(values)|{'schema','prior_release','accounting_evidence','capacity_audit',
                'disk_protocol','limits_unchanged','envelope_provenance','rationale'}
            and document['schema']=='bounded-disk-capacity-amendment-v1',
            'Exact disk amendment document schema required')
    for key,value in values.items():
        require(type(document[key]) is type(value) and document[key]==value,
                'Disk amendment document changed: '+key)
    require(isinstance(document['rationale'],str) and bool(document['rationale'].strip()),
            'Disk amendment retained-copy rationale required')
    for key in ('prior_release','accounting_evidence','capacity_audit','disk_protocol','envelope_provenance'):
        evidence=document[key]
        require(isinstance(evidence,dict) and set(evidence)=={'path','sha256'}
                and release['source_pins'].get(evidence['path'])==evidence['sha256'],
                'Disk amendment evidence pin missing: '+key)
        require(hashlib.sha256(Path(evidence['path']).read_bytes()).hexdigest()==evidence['sha256'],
                'Disk amendment evidence changed: '+key)
    r=release['resume']
    require(document['prior_release']==dict(path=r['previous_release'],sha256=release['source_pins'][r['previous_release']])
            and document['accounting_evidence']==dict(path=r['accounting_evidence'],sha256=release['source_pins'][r['accounting_evidence']]),
            'Disk amendment must retain the exact lost-run release and accounting')
    limits=dict(memory_bytes=6442450944,disk_floor=5905580032,
                final_snapshot_reserve_bytes=536870912,terminal_reserve_bytes=67108864,
                term_deadline_unix=r['term_deadline_unix'],kill_ceiling_unix=r['kill_ceiling_unix'])
    require(document['limits_unchanged']==limits and p['memory_bytes']==limits['memory_bytes']
            and p['disk_floor']==limits['disk_floor']
            and r['term_deadline_unix']==old['resume']['term_deadline_unix']
            and r['kill_ceiling_unix']==old['resume']['kill_ceiling_unix'],
            'Disk amendment cannot change memory, free floor, reserves or deadlines')
    protocol=pinned_json(release,document['disk_protocol']['path'])
    previous_path=str(Path(r['previous_release']).parent.parent/'disk-protocol.json')
    previous_protocol=pinned_json(release,previous_path)
    require(set(protocol)==set(previous_protocol),'Disk measurement protocol keys changed')
    for key,value in previous_protocol.items():
        if key not in ('disk_bytes','completion_capacity','reservation_scope_limit'):
            require(type(protocol[key]) is type(value) and protocol[key]==value,
                    'Disk measurement algorithm or reserve changed: '+key)
    require(protocol['disk_bytes']==p['disk_bytes']
            and protocol['name']==release['disk_measurement_protocol']=='fd-relative-sampled-disk-v1'
            and protocol['free_floor_bytes']==p['disk_floor']
            and protocol['future_final_snapshot_reserve_bytes']==limits['final_snapshot_reserve_bytes']
            and protocol['terminal_reserve_bytes']==limits['terminal_reserve_bytes']
            and protocol['hard_peak_guarantee'] is False,
            'Disk amendment protocol does not match the exact plan and reserve limits')
    for key in ('completion_capacity','reservation_scope_limit'):
        require(isinstance(protocol[key],str) and bool(protocol[key].strip()),
                'Disk amendment scope disclosure missing: '+key)
    require(document['envelope_provenance']['sha256']=='REDACTED_PRIVATE_EVIDENCE_SHA256',
            'Original root-chosen test-envelope provenance changed')
    audit=pinned_json(release,document['capacity_audit']['path'])
    require(audit['schema']=='revised-10gib-recovery-capacity-proposal-v1'
            and audit['status']=='source_only_quantitative_proposal_not_launch_authorization',
            'Separate bounded capacity audit required')
    decision=audit['decision']
    require(decision['old_disk_ceiling_bytes']==previous['disk_bytes']
            and decision['proposed_disk_ceiling_bytes']==p['disk_bytes']
            and decision['explicit_increase_bytes']==p['disk_bytes']-previous['disk_bytes']==2147483648
            and decision['old_8gib_verdict']=='capacity-limited/incomplete; no measured breach asserted'
            and decision['full_remaining_completion_capacity_proven'] is False
            and decision['possible_capacity_stop'] is True,
            'Capacity audit cannot promote the historical verdict or guarantee completion')
    retained=audit['retained_allocation'];arithmetic=audit['capacity_arithmetic']
    require(retained['sampled_runroot_charge_bytes']==REDACTED_COORDINATE_F1D5BEA2B8
            and retained['recovery_database_family_allocation_bytes']==REDACTED_COORDINATE_09801CEF28
            and arithmetic['under_10gib_after_512_plus_64_MiB_reserve_bytes']==REDACTED_COORDINATE_CEF7E3DC06
            ==p['disk_bytes']-retained['sampled_runroot_charge_bytes']
              -limits['final_snapshot_reserve_bytes']-limits['terminal_reserve_bytes']
            and arithmetic['extra_ordinary_growth_margin_bytes']==2147483648,
            'Exact retained-evidence capacity arithmetic changed')
    constraints=audit['unchanged_constraints']
    for key,value in dict(memory_bytes=limits['memory_bytes'],filesystem_free_floor_bytes=limits['disk_floor'],
            future_main_snapshot_reserve_bytes=limits['final_snapshot_reserve_bytes'],
            terminal_evidence_helper_reserve_bytes=limits['terminal_reserve_bytes'],
            observer_requests_carried=REDACTED_COORDINATE_09C2A7BBA8,observer_decoded_bytes_carried=REDACTED_COORDINATE_4F8B747129,status_rounds_carried=REDACTED_COORDINATE_A183251A84,
            observer_budget_reset=False,all_historical_UNKNOWN_preserved=True,
            originals_and_all_recovery_copies_preserved=True,no_deletion_or_silent_exclusion=True,
            planned_restart_045_already_done=True).items():
        require(type(constraints[key]) is type(value) and constraints[key]==value,
                'Capacity audit conserved limit or history changed: '+key)
    clock=constraints['original_clock_deadlines']
    for key in ('term_deadline_unix','kill_ceiling_unix','started_lower','started_upper','schedule_offset',
                'previous_status_rounds','last_sample_at'):
        require(clock[key]==r[key],'Capacity audit original clock changed: '+key)
    require(clock['accounting_evidence']==r['accounting_evidence']
            and clock['accounting_sha256']==release['source_pins'][r['accounting_evidence']]
            and audit['sampling_limitations']['hard_peak_guarantee'] is False,
            'Capacity audit accounting or sampled enforcement scope changed')
    for evidence in audit['source_pins']+[retained['inventory']]:
        require(release['source_pins'].get(evidence['path'])==evidence['sha256'],
                'Capacity audit source evidence pin missing')
    scope=release['disk_completion_scope']
    require(scope['existing_disk_limit_bytes']==p['disk_bytes']
            and scope['full_remaining_completion_capacity_proven'] is False
            and scope['possible_capacity_stop'] is True
            and scope['future_final_snapshot_reserve_bytes']==limits['final_snapshot_reserve_bytes']
            and scope['terminal_reserve_bytes']==limits['terminal_reserve_bytes'],
            'Disk amendment must retain bounded capacity-stop and reserve disclosures')


def verify_sources(root,baseline,digest,check):
    root=Path(root)
    require(root.is_absolute() and root.resolve()==root and not root.is_symlink(),'Source root confinement')
    for relative,wanted in baseline['source_evidence'].items():
        check();p=root/relative
        require(not Path(relative).is_absolute() and '..' not in Path(relative).parts
                and p.is_relative_to(root) and p.resolve()==p and not p.is_symlink(),'Source confinement')
        before=p.stat();identity=[before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns]
        require(identity==wanted['identity'] and digest(p)==wanted['sha256'],'Stopped source changed before recovery')
        for key,actual in (('mode',before.st_mode),('uid',before.st_uid),('nlink',before.st_nlink)):
            if key in wanted:require(actual==wanted[key],'Stopped native source metadata changed: '+key)
        after=p.stat();require(identity==[after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns],'Source changed while verifying')
    expected={str(root/k) for k in baseline['source_evidence']}
    actual=set()
    for directory in (root/'qualified-journal',root/'prior-segment-output-F'):
        require(directory.is_dir() and directory.resolve()==directory,'Stopped evidence directory confinement')
        for path in directory.rglob('*'):
            require(not path.is_symlink(),'Stopped evidence symlink')
            if path.is_file():actual.add(str(path))
    for path in (root/'qualified-data').glob('jobs.sqlite3*'):
        require(path.is_file() and not path.is_symlink(),'Stopped primary family changed')
        actual.add(str(path))
    actual.add(str(root/'qualified-data/instance.lock'))
    native=root/'qualified-data/native-owned-cells-bounded-v1'
    require(native.is_dir() and native.resolve()==native and not native.is_symlink(),'Stopped native namespace changed')
    for path in native.iterdir():
        require(path.name in {'cells.sqlite3','cells.sqlite3-journal','cells.sqlite3-wal','cells.sqlite3-shm'}
                and path.is_file() and not path.is_symlink(),'Unexpected stopped native source')
        actual.add(str(path))
    require(str(native/'cells.sqlite3') in expected,'Stopped native database evidence missing')
    require(actual==expected,'Stopped source file set changed')

def preserve(baseline,old_manifest,new_manifest,new_summary):
    count=compare(old_manifest,new_manifest)
    require(count==baseline['completed']['completed'] and new_summary['completed']>=count and new_summary['origin']==baseline['completed']['origin'] and new_summary['end']>=baseline['completed']['end'] and new_summary['eos']==0,'Recovery lost completed result/frontier')
    return count


FAILED_CHUNK='REDACTED_HISTORICAL_CHUNK'

def require_checkpoint(j, expected):
    from endurance import FRAMES
    require(j==expected,'Checkpoint differs from pinned stopped journal')
    for key in ('inputs','clean_inputs','frames','published_frames','pages'):
        require(type(j.get(key)) is int and j[key]>=0,'Invalid checkpoint integer: '+key)
    count=j['clean_inputs'];active=j.get('active_input')
    require(0<=count<90 and 'active_input' in j and j.get('finalized') is False,'Checkpoint boundary/finalization')
    require(0<=j['published_frames']<=j['frames'] and j['published_frames']%4704000==0 and j['pages']*4704000>=j['frames'],'Checkpoint staged page frontier')
    if active is None:
        require(j['inputs']==count and j['frames']==count*FRAMES,'Clean checkpoint geometry')
    else:
        require(isinstance(active,dict) and type(active.get('frames')) is int and 0<=active['frames']<=FRAMES,'Partial checkpoint cursor')
        require(type(active.get('normalized_start')) is int and active['normalized_start']==count*FRAMES and active.get('clean_eof') is False,'Partial checkpoint prefix')
        require(j['inputs']==count+1 and j['frames']==count*FRAMES+active['frames'],'Partial checkpoint geometry')
    return j['published_frames']


def require_restart(resume,baseline,restart_after):
    done=resume.get('restart_already_done')
    require(type(done) is bool,'Explicit restart history required')
    count=baseline['journal']['clean_inputs']
    if not done:
        require(count<restart_after,'Passed planned restart without conservation evidence')
        return False
    require(count>=restart_after,'Restart evidence precedes completed boundary')
    path=resume.get('restart_receipt');sha=resume.get('restart_receipt_sha256')
    require(isinstance(path,str) and isinstance(sha,str) and len(sha)==64,'Restart receipt pin required')
    data=Path(path).read_bytes()
    require(hashlib.sha256(data).hexdigest()==sha,'Restart receipt changed')
    proof=json.loads(data)
    require(proof.get('schema')=='planned-restart-conservation-v1' and proof.get('status')=='conserved','Restart receipt status')
    require(type(proof.get('restart_after')) is int and proof['restart_after']==restart_after and type(proof.get('accepted_input_count')) is int and proof['accepted_input_count']==restart_after and proof.get('accepted_input_id')==f'repeated-prefix-{restart_after:03d}','Restart input acceptance')
    require(type(proof.get('positive_stop_generation')) is int and proof['positive_stop_generation']==0 and proof.get('positive_stop') is True and proof.get('positive_original_wait') is True and proof.get('latest_generation_cleanup')=='UNKNOWN','Restart generation cleanup proof')
    before=proof['before'];after=proof['after']
    require(type(proof.get('preserved_completed')) is int and proof['preserved_completed']==REDACTED_COORDINATE_7931AA2A1B and before['completed']==REDACTED_COORDINATE_7931AA2A1B,'Qualified planned restart count')
    for key in ('completed','result_rows','origin','end','eos','sha256'):
        require(before[key]==after[key],'Restart conservation differs: '+key)
    require(before['result_rows']==REDACTED_COORDINATE_EB79D7629D and before['origin']==REDACTED_COORDINATE_24EB33C5F8 and before['end']==REDACTED_COORDINATE_C7529010D9 and before['eos']==0,'Qualified planned restart geometry')
    for item in (before,after):
        require(proof['source_pins'].get(item['path'])==item['sha256'],'Restart manifest evidence pin')
        require(hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256'],'Restart manifest changed')
    require(baseline['completed']['completed']>=REDACTED_COORDINATE_7931AA2A1B,'Baseline precedes conserved restart')
    return True


def require_lost_baseline(baseline):
    require(baseline['status']=='complete' and baseline['before']==baseline['after'] and baseline['changed']==[] and type(baseline['exclusive_locks_acquired']) is int and baseline['exclusive_locks_acquired']==2,'Exact immutable dual-lock baseline required')
    require(baseline['latest_original_wait_available'] is False and baseline['latest_owner_cleanup']=='UNKNOWN' and baseline.get('latest_original_wait') is None and baseline.get('latest_stop') is None and baseline['ownership_status']=='both_existing_flocks_held' and baseline['latest_cleanup_proof']=='UNKNOWN; no original wait/terminal required or inferred','Latest lost executor remains UNKNOWN under present dual-lock proof')


def require_accounting(history,accounting,allowance):
    require(accounting['schema']=='lost-executor-durable-accounting-v1' and accounting['status']=='derived_evidence_proposal_not_terminal','Qualified lost-executor accounting evidence')
    h=accounting['http'];charge=h['conservative_continuation_charge']
    require(history['observer_budget_reset'] is False and h['observer_budget_reset'] is False and h['allowance_unchanged']==allowance,'Observation allowance reset or changed')
    require(charge['kind']=='conservative_bound_not_actual_usage' and history['observer_charge_kind']==charge['kind'],'Accounting bound is not measured final usage')
    require(history['latest_owner_cleanup']=='UNKNOWN' and history['latest_original_wait_available'] is False,'Latest history cleanup must remain UNKNOWN')
    require(history['observed_recovered_usage']==dict(requests=h['recovered_segment_request_counter_lower_bound'],decoded_bytes=h['recovered_segment_success_return_decoded_lower_bound']),'Observed lower bounds distinct from charged bound')
    fields=(charge['requests'],charge['decoded_bytes'],h['recovered_segment_request_counter_lower_bound'],h['recovered_segment_success_return_decoded_lower_bound'],h['missing_slot_count'],charge['body_reserve_each'],charge['body_reserve_slots'],charge['body_reserve_total'])
    require(all(type(value) is int and value>=0 for value in fields),'Invalid accounting counter')
    require(charge['additional_unlogged_tail_request_reserve']==1 and charge['requests']==h['recovered_segment_request_counter_lower_bound']+1,'Bounded post-restart request tail')
    require(h['missing_slot_count']==len(h['missing_sequence_slots']) and len(set(h['missing_sequence_slots']))==h['missing_slot_count'],'Accounting missing-slot evidence')
    require(charge['body_reserve_each']==allowance['individual_body_cap']+1 and charge['body_reserve_slots']==h['missing_slot_count']+1 and charge['body_reserve_total']==charge['body_reserve_each']*charge['body_reserve_slots'],'Conservative failed/tail body reserve')
    require(charge['decoded_bytes']==h['recovered_segment_success_return_decoded_lower_bound']+charge['body_reserve_total'],'Conservative decoded carry arithmetic')
    require(h['recovered_segment_request_counter_lower_bound']>=h['prior_carried_requests'] and h['recovered_segment_success_return_decoded_lower_bound']==h['prior_carried_success_return_decoded_bytes']+h['durable_body_bytes'],'Prior observer use regressed')
    require(history['observer_requests']==charge['requests'] and history['decoded_bytes']==charge['decoded_bytes'],'Observer conservative bound must carry exactly')
    require(charge['requests']<=allowance['segment_get_cap'] and charge['decoded_bytes']<=allowance['segment_decoded_cap'],'Observation allowance exhausted')


def require_terminal_accounting(history,accounting,allowance,terminal,wait,old):
    """Carry the stopped run17 charged counters, not invented historical usage."""
    require(accounting['schema']=='positive-terminal-observer-accounting-v1'
            and accounting['status']=='positive_terminal_cumulative_charge','Qualified positive-terminal accounting evidence')
    require(accounting['observer_budget_reset'] is False and history['observer_budget_reset'] is False
            and accounting['historical_request_accounting']=='UNKNOWN'
            and history['historical_request_accounting']=='UNKNOWN','Terminal accounting cannot reset historical uncertainty')
    require(history['observer_charge_kind']=='cumulative_terminal_charge_with_historical_conservative_reserves',
            'Cumulative charges must retain conservative history')
    require(history['interrupted_owner_cleanup']=='UNKNOWN' and history['original_wait_available'] is False
            and history['latest_owner_cleanup']=='positive' and history['latest_original_wait_available'] is True,
            'Latest positive stop must remain separate from older UNKNOWN cleanup')
    require(terminal['observation_allowance']==allowance==old['resume']['observation_allowance'],'Observation allowance changed')
    require(terminal['original_global_observer_quota_proven'] is False
            and terminal['historical_positive_stop'] is False and terminal['interrupted_owner_cleanup']=='UNKNOWN',
            'Terminal cannot imply historical global quota or cleanup proof')
    for key,terminal_key,expected in (('requests','observer_requests',REDACTED_COORDINATE_E41D64DB57),('decoded_bytes','decoded_bytes',REDACTED_COORDINATE_FAD31DA6EC)):
        require(type(accounting[key]) is int and accounting[key]==expected
                and type(terminal[terminal_key]) is int and terminal[terminal_key]==expected
                and type(history[terminal_key]) is int and history[terminal_key]==expected,'Terminal cumulative observer charge changed')
    require(accounting['requests']<=allowance['segment_get_cap']
            and accounting['decoded_bytes']<=allowance['segment_decoded_cap'],'Observation allowance exhausted')
    carry=terminal['observer_carry_charge']
    require(carry['requests']==REDACTED_COORDINATE_1C918023D6 and carry['decoded_bytes']==REDACTED_COORDINATE_129FEF76C1
            and accounting['requests']>=carry['requests'] and accounting['decoded_bytes']>=carry['decoded_bytes'],
            'Earlier conservative charge was lost')
    fields=('prior_status_rounds','durable_completed_rounds','uncompleted_round_reserve','status_rounds_charge')
    require(all(type(accounting[k]) is int for k in fields),'Invalid terminal status-round counters')
    require(accounting['prior_status_rounds']==old['resume']['previous_status_rounds']==REDACTED_COORDINATE_F15223DCC0
            and accounting['durable_completed_rounds']==60 and accounting['uncompleted_round_reserve']==1
            and accounting['status_rounds_charge']==REDACTED_COORDINATE_F15223DCC0+60+1,'Terminal status-round conservation changed')
    require(accounting['last_sample_at']==REDACTED_COORDINATE_9E967B95C5,'Pinned final sample changed')
    require(terminal['clock_reconstruction']==old['resume'],'Terminal previous-release reconstruction changed')
    require(type(wait['returncode']) is int and wait['returncode']==1 and wait['positive_original_wait'] is True,
            'Original failed-run root wait must be positive')
    require(terminal['operations_pass'] is False and terminal['recovered_operations_pass'] is False
            and terminal['uninterrupted_endurance_pass'] is False,'Failed prior run cannot become a pass')
    require(terminal['positive_stop'] is True and not terminal.get('cleanup_or_readback_error')
            and terminal['recovery_baseline_completed_preserved']==REDACTED_COORDINATE_D8EC3903C8
            and terminal['supervisor_ownership_retained_through_terminal_fsync'] is True
            and terminal['supervisor_ownership_release_after_terminal_authorized'] is True,'Latest terminal conservation proof')
    stop=terminal['stops'][-1]
    require(stop['segment_output']=='prior-segment-output-C' and stop['positive'] is True and stop['survivors']==[]
            and stop['errors']==[] and stop['lifespan'] is True and stop['forced'] is False
            and type(stop['generation']) is int and stop['generation']==0,'Latest terminal cleanup not positive')
    require({row['name'] for row in stop['cleanup']}=={'server','uploader'} and len(stop['cleanup'])==2
            and all(row['positive_original_wait'] is True and type(row['returncode']) is int for row in stop['cleanup']),
            'Latest child waits incomplete')
    require(history['stops']==terminal['stops'],'Historical stop sequence changed')
    for source in ('prior-segment-output-A','prior-segment-output-B'):
        unknown=[row for row in terminal['stops'] if row.get('source')==source]
        require(len(unknown)==1 and unknown[0]['positive']=='UNKNOWN'
                and unknown[0]['original_wait_available'] is False,'Earlier lost executor cleanup cannot be upgraded')
    for key in ('disk_peak','log_bytes','peak_charged','peak_rss','max_sample_gap','sampled_roles',
                'namespace_exit_gaps','disk_census_history','original_recheck_counts','original_recheck_history'):
        require(history[key]==terminal[key],'Recovered terminal history changed: '+key)


def require_positive_baseline(baseline,terminal,wait):
    require(baseline['status']=='complete' and baseline['before']==baseline['after']==baseline['source_evidence']
            and baseline['changed']==[] and type(baseline['exclusive_locks_acquired']) is int
            and baseline['exclusive_locks_acquired']==2 and baseline['ownership_status']=='both_existing_flocks_held',
            'Exact immutable dual-lock baseline required')
    require(baseline['historical_positive_stop'] is False and baseline['interrupted_owner_cleanup']=='UNKNOWN'
            and baseline['original_wait_available'] is False and baseline['historical_lost_executor']=='prior-segment-output-A'
            and baseline['historical_other_lost_executor']=='prior-segment-output-B','Earlier baseline history must remain UNKNOWN')
    require(baseline['latest_run']=='prior-segment-output-C' and baseline['latest_original_wait_available'] is True
            and baseline['latest_owner_cleanup']=='positive' and baseline['latest_original_wait']==wait
            and baseline['latest_stop']==terminal['stops'][-1],'Stopped baseline positive terminal/wait mismatch')
    require(baseline['completed']==terminal['readback'] and baseline['completed_manifest_sha256']==baseline['completed']['manifest_sha256'],
            'Stopped completed baseline differs from terminal readback')
    require(all(type(baseline['completed'][key]) is int for key in ('completed','result_rows','origin','end','eos'))
            and {key:baseline['completed'][key] for key in ('completed','result_rows','origin','end','eos')}
            ==dict(completed=REDACTED_COORDINATE_D8EC3903C8,result_rows=REDACTED_COORDINATE_75AA8C109B,origin=REDACTED_COORDINATE_24EB33C5F8,end=REDACTED_COORDINATE_C7529010D9,eos=0),'Exact stopped run17 completed geometry')
    require(baseline['prior2042_preserved']==REDACTED_COORDINATE_D8EC3903C8 and baseline['prior587_preserved']==REDACTED_COORDINATE_82A93B152B
            and baseline['restart2013_before_preserved']==baseline['restart2013_after_preserved']==REDACTED_COORDINATE_7931AA2A1B,
            'Stopped baseline result/restart preservation changed')
    require(baseline['native_ancestry_before']==baseline['native_ancestry_after'],'Stopped native ancestry changed')
    j=baseline['journal']
    require((j['inputs'],j['clean_inputs'],j['frames'],j['published_frames'],j['pages'])==(46,45,REDACTED_COORDINATE_AAC3F5EB03,REDACTED_COORDINATE_61682EB8F8,74)
            and j['finalized'] is False,'Exact stopped partial046 geometry')
    active=j['active_input']
    require(active['id']=='repeated-prefix-046' and active['frames']==REDACTED_COORDINATE_B01236484B
            and active['normalized_start']==REDACTED_COORDINATE_3E05375E3C and active['clean_eof'] is False,'Exact stopped partial046 cursor')


def require_loss20_baseline(baseline,terminal,wait):
    require(baseline['status']=='complete' and baseline['before']==baseline['after']==baseline['source_evidence']
            and baseline['changed']==[] and type(baseline['exclusive_locks_acquired']) is int
            and baseline['exclusive_locks_acquired']==2 and baseline['ownership_status']=='both_existing_flocks_held',
            'Exact immutable dual-lock lost-run baseline required')
    require(baseline['historical_positive_stop'] is False and baseline['interrupted_owner_cleanup']=='UNKNOWN'
            and baseline['original_wait_available'] is False and baseline['historical_lost_executor']=='prior-segment-output-A'
            and baseline['historical_other_lost_executor']=='prior-segment-output-B','Earlier baseline history must remain UNKNOWN')
    require(baseline['latest_run']=='prior-segment-output-D' and baseline['latest_original_wait_available'] is False
            and baseline['latest_owner_cleanup']=='UNKNOWN' and baseline.get('latest_original_wait') is None
            and baseline.get('latest_stop') is None and baseline['latest_cleanup_proof']==
            'UNKNOWN: lost run20 original handle; current dual locks exclude cooperating holders only, not orphan or historical-cleanup proof',
            'Latest lost run20 cleanup cannot be inferred from present ownership')
    require(baseline['prior_run17_original_wait_available'] is True
            and baseline['prior_run17_owner_cleanup']=='positive'
            and baseline['prior_run17_original_wait']==wait and baseline['prior_run17_stop']==terminal['stops'][-1],
            'Prior positive run17 evidence changed')
    c=baseline['completed']
    require(all(type(c[key]) is int for key in ('completed','result_rows','origin','end','eos'))
            and {key:c[key] for key in ('completed','result_rows','origin','end','eos')}
            ==dict(completed=REDACTED_COORDINATE_C311B90801,result_rows=REDACTED_COORDINATE_8765A797E5,origin=REDACTED_COORDINATE_24EB33C5F8,end=REDACTED_COORDINATE_E687E4ECD6,eos=0)
            and baseline['completed_manifest_sha256']==c['manifest_sha256']
            =='REDACTED_PRIVATE_EVIDENCE_SHA256',
            'Exact stopped run20 completed geometry/hash changed')
    require(baseline['prior2042_preserved']==REDACTED_COORDINATE_D8EC3903C8 and baseline['prior587_preserved']==REDACTED_COORDINATE_82A93B152B
            and baseline['restart2013_before_preserved']==baseline['restart2013_after_preserved']==REDACTED_COORDINATE_7931AA2A1B,
            'Stopped baseline result/restart preservation changed')
    require(baseline['native_ancestry_before']==baseline['native_ancestry_after'],'Stopped native ancestry changed')
    j=baseline['journal']
    require(all(type(j[key]) is int for key in ('inputs','clean_inputs','frames','published_frames','pages'))
            and (j['inputs'],j['clean_inputs'],j['frames'],j['published_frames'],j['pages'])
            ==(67,66,REDACTED_COORDINATE_6991B86629,REDACTED_COORDINATE_3A2CA9525B,REDACTED_COORDINATE_9537F32EC7) and j['finalized'] is False,'Exact stopped partial067 geometry')
    active=j['active_input']
    require(active['id']=='repeated-prefix-067' and type(active['frames']) is int and active['frames']==REDACTED_COORDINATE_A1B9726EA0
            and type(active['normalized_start']) is int and active['normalized_start']==REDACTED_COORDINATE_163BC245B5
            and active['clean_eof'] is False,'Exact stopped partial067 cursor')
    observed=baseline['observed_boundary']
    require(observed==dict(active_input=active,archive_intent=None,completed_inputs=66,inputs=67,
                next_input_id='repeated-prefix-067',retirement=None,staged_frames=4704000),
            'Stopped partial067 observed boundary changed')
    require(baseline['recovered']['states']==dict(completed=REDACTED_COORDINATE_C311B90801,queued=6,running=1,staging=28),
            'Stopped run20 work states changed')


def require_loss20_accounting(history,accounting,allowance,prior_history,old):
    """Add loss20 durable evidence and bounded tail to the already charged carry."""
    require(accounting['schema']=='loss20-carried-accounting-v2'
            and accounting['status']=='derived_evidence_proposal_not_terminal','Qualified loss20 accounting evidence')
    require(accounting['observer_budget_reset'] is False and history['observer_budget_reset'] is False
            and accounting['historical_request_accounting']==history['historical_request_accounting']=='UNKNOWN',
            'Loss20 accounting cannot reset history or imply global usage proof')
    require(history['observer_charge_kind']=='cumulative_conservative_charge_with_historical_reserves'
            and accounting['latest_owner_cleanup']==history['latest_owner_cleanup']=='UNKNOWN'
            and accounting['latest_original_wait_available'] is history['latest_original_wait_available'] is False,
            'Lost run20 must remain UNKNOWN; cumulative charges are not measured usage')
    require(allowance==old['resume']['observation_allowance']==accounting['allowance'],'Observation allowance changed')
    require(accounting['prior_carried']==dict(requests=REDACTED_COORDINATE_E41D64DB57,decoded_bytes=REDACTED_COORDINATE_FAD31DA6EC,
                kind='cumulative_terminal_charge_including_prior_conservative_reserves')
            and prior_history['observer_requests']==REDACTED_COORDINATE_E41D64DB57 and prior_history['decoded_bytes']==REDACTED_COORDINATE_FAD31DA6EC,
            'Prior terminal charge including conservative reserves was lost')
    durable=accounting['durable_http'];charge=accounting['conservative_charge'];prefix=accounting['charged_prefix']
    for section in (accounting['prior_carried'],prefix,charge):
        require(all(type(section[key]) is int and section[key]>=0 for key in ('requests','decoded_bytes')),
                'Invalid loss20 charged counter')
    for key in ('rows','first_sequence','last_sequence','body_bytes','new_request_attempts_lower_bound'):
        require(type(durable[key]) is int and durable[key]>=0,'Invalid loss20 durable counter')
    require(durable['rows']==REDACTED_COORDINATE_40A7AE9A07 and durable['first_sequence']==REDACTED_COORDINATE_00F6112FE5 and durable['last_sequence']==REDACTED_COORDINATE_25F2044C9F
            and durable['body_bytes']==REDACTED_COORDINATE_7732C574B4 and durable['missing_sequences']==list(range(REDACTED_COORDINATE_A56B54E1C5,REDACTED_COORDINATE_00F6112FE5))
            and durable['new_request_attempts_lower_bound']==REDACTED_COORDINATE_51CFE39433
            and durable['rows']+len(durable['missing_sequences'])==durable['new_request_attempts_lower_bound'],
            'Exact loss20 durable HTTP evidence changed')
    require(prefix['requests']==REDACTED_COORDINATE_25F2044C9F and prefix['decoded_bytes']==REDACTED_COORDINATE_5488CF329F and prefix['is_actual_usage'] is False
            and prefix['requests']==accounting['prior_carried']['requests']+durable['new_request_attempts_lower_bound']
            and prefix['decoded_bytes']==accounting['prior_carried']['decoded_bytes']+durable['body_bytes'],
            'Loss20 charged-prefix conservation changed')
    require(charge==dict(requests=REDACTED_COORDINATE_7DCCA41D83,decoded_bytes=REDACTED_COORDINATE_0B760BF1C8,unlogged_tail_requests=1,
                body_reserve_slots=8,body_reserve_each=REDACTED_COORDINATE_1DCFAA9A1F,body_reserve_bytes=REDACTED_COORDINATE_F8B2403F10,
                kind='conditional_conservative_bound_not_actual_usage')
            and all(type(charge[key]) is int for key in ('unlogged_tail_requests','body_reserve_slots','body_reserve_each','body_reserve_bytes'))
            and charge['requests']==prefix['requests']+charge['unlogged_tail_requests']
            and charge['body_reserve_slots']==len(durable['missing_sequences'])+charge['unlogged_tail_requests']
            and charge['body_reserve_each']==allowance['individual_body_cap']+1
            and charge['decoded_bytes']==prefix['decoded_bytes']+charge['body_reserve_slots']*charge['body_reserve_each'],
            'Loss20 conservative failed/tail reserve changed')
    require(type(history['observer_requests']) is int and history['observer_requests']==charge['requests']
            and type(history['decoded_bytes']) is int and history['decoded_bytes']==charge['decoded_bytes']
            and charge['requests']<=allowance['segment_get_cap'] and charge['decoded_bytes']<=allowance['segment_decoded_cap'],
            'Loss20 exact carry changed or allowance exhausted')
    counters=('prior_status_rounds','durable_completed_rounds','uncompleted_round_reserve','status_rounds_charge')
    require(all(type(accounting[key]) is int for key in counters)
            and accounting['prior_status_rounds']==old['resume']['previous_status_rounds']==REDACTED_COORDINATE_BC57590A33
            and accounting['durable_completed_rounds']==REDACTED_COORDINATE_8D23CF6C86 and accounting['uncompleted_round_reserve']==1
            and accounting['status_rounds_charge']==REDACTED_COORDINATE_BC57590A33+REDACTED_COORDINATE_8D23CF6C86+1
            and accounting['last_sample_at']==REDACTED_COORDINATE_C38F9F247A,'Loss20 status/sample carry changed')
    require(history['stops']==prior_history['stops']+[
                dict(source='prior-segment-output-D',positive='UNKNOWN',original_wait_available=False)],
            'Loss20 cleanup history changed or promoted')
    resources=accounting['resources']
    for key,expected in (('disk_peak',accounting['disk']['peak_lower_bound']),
            ('log_bytes',accounting['logs']['carried_plus_new_log_lower_bound']),
            ('peak_charged',resources['peak_charged_lower_bound']),('peak_rss',resources['peak_rss_lower_bound']),
            ('namespace_exit_gaps',resources['namespace_exit_gaps_lower_bound'])):
        require(type(history[key]) is int and history[key]==expected and history[key]>=prior_history[key],
                'Loss20 resource lower-bound carry changed: '+key)
    require(history['max_sample_gap']==max(prior_history['max_sample_gap'],
                resources['gap_between_last_prior_and_first_current_logged_samples'])
            and history['sampled_roles']==prior_history['sampled_roles'],
            'Loss20 sample gap or sampled roles changed')
    require(history['disk_census_history']==prior_history['disk_census_history']+[accounting['disk']]
            and history['original_recheck_counts']==prior_history['original_recheck_counts']
            ==accounting['original_recheck']['prior_lower_bound_counts'],
            'Loss20 disk/recheck history changed')
    recheck=history['original_recheck_history']
    require({key:value for key,value in recheck.items() if key!='prior_segment_output_D'}==prior_history['original_recheck_history']
            and recheck['prior_segment_output_D']['new_exact_calls'] is None
            and recheck['prior_segment_output_D']['new_exact_outcome_counts'] is None
            and recheck['prior_segment_output_D']['totals']=='UNKNOWN',
            'Unobserved loss20 recheck counters cannot become proven totals')


def require_loss23_baseline(baseline,terminal,wait,prior_baseline):
    require(baseline['status']=='complete' and baseline['before']==baseline['after']==baseline['source_evidence']
            and baseline['changed']==[] and type(baseline['exclusive_locks_acquired']) is int
            and baseline['exclusive_locks_acquired']==2 and baseline['ownership_status']=='both_existing_flocks_held',
            'Exact immutable dual-lock lost-run baseline required')
    require(baseline['historical_positive_stop'] is False and baseline['interrupted_owner_cleanup']=='UNKNOWN'
            and baseline['original_wait_available'] is False and baseline['historical_lost_executor']=='prior-segment-output-A'
            and baseline['historical_other_lost_executor']=='prior-segment-output-B','Earlier baseline history must remain UNKNOWN')
    require(baseline['latest_run']=='prior-segment-output-E' and baseline['latest_original_wait_available'] is False
            and baseline['latest_owner_cleanup']=='UNKNOWN' and baseline.get('latest_original_wait') is None
            and baseline.get('latest_stop') is None and baseline['latest_cleanup_proof']==
            'UNKNOWN: lost run23 original handle; current dual locks exclude cooperating holders only, not orphan or historical-cleanup proof',
            'Latest lost run23 cleanup cannot be inferred from present ownership')
    require(baseline['prior_run17_original_wait_available'] is True
            and baseline['prior_run17_owner_cleanup']=='positive'
            and baseline['prior_run17_original_wait']==wait and baseline['prior_run17_stop']==terminal['stops'][-1],
            'Prior positive run17 evidence changed')
    c=baseline['completed']
    require(all(type(c[key]) is int for key in ('completed','result_rows','origin','end','eos'))
            and {key:c[key] for key in ('completed','result_rows','origin','end','eos')}
            ==dict(completed=REDACTED_COORDINATE_C311B90801,result_rows=REDACTED_COORDINATE_8765A797E5,origin=REDACTED_COORDINATE_24EB33C5F8,end=REDACTED_COORDINATE_E687E4ECD6,eos=0)
            and baseline['completed_manifest_sha256']==c['manifest_sha256']
            =='REDACTED_PRIVATE_EVIDENCE_SHA256',
            'Exact stopped run23 completed geometry/hash changed')
    require(baseline['prior2042_preserved']==REDACTED_COORDINATE_D8EC3903C8 and baseline['prior587_preserved']==REDACTED_COORDINATE_82A93B152B
            and baseline['restart2013_before_preserved']==baseline['restart2013_after_preserved']==REDACTED_COORDINATE_7931AA2A1B,
            'Stopped baseline result/restart preservation changed')
    require(baseline['native_ancestry_before']==baseline['native_ancestry_after'],'Stopped native ancestry changed')
    j=baseline['journal']
    require(all(type(j[key]) is int for key in ('inputs','clean_inputs','frames','published_frames','pages'))
            and (j['inputs'],j['clean_inputs'],j['frames'],j['published_frames'],j['pages'])
            ==(67,66,REDACTED_COORDINATE_6991B86629,REDACTED_COORDINATE_3A2CA9525B,REDACTED_COORDINATE_9537F32EC7) and j['finalized'] is False,'Exact stopped partial067 geometry')
    active=j['active_input']
    require(active['id']=='repeated-prefix-067' and type(active['frames']) is int and active['frames']==REDACTED_COORDINATE_A1B9726EA0
            and type(active['normalized_start']) is int and active['normalized_start']==REDACTED_COORDINATE_163BC245B5
            and active['clean_eof'] is False,'Exact stopped partial067 cursor')
    observed=baseline['observed_boundary']
    require(observed==dict(active_input=active,archive_intent=None,completed_inputs=66,inputs=67,
                next_input_id='repeated-prefix-067',retirement=None,staged_frames=4704000),
            'Stopped partial067 observed boundary changed')
    require(baseline['recovered']['states']==dict(completed=REDACTED_COORDINATE_C311B90801,queued=6,running=1,staging=28),
            'Stopped run23 work states changed')

    require(baseline['historical_run20_cleanup']=='UNKNOWN' and baseline['prior2989_preserved']==REDACTED_COORDINATE_C311B90801,
            'Earlier lost run20 cleanup or completed preservation changed')
    require(baseline['journal']==prior_baseline['journal'],
            'Exact unchanged run23 journal cursor required')
    require(baseline['recovered']['failed_count']==0,'Failed work exists; automatic retry forbidden')


def require_baseline16_provenance(release,baseline,accounting,old):
    """Keep phase1 observations, phase2 readback and helper-source uncertainty separate."""
    phase_pin=baseline['phase1_receipt']
    require(set(phase_pin)=={'path','sha256'} and phase_pin['path']==accounting['baseline_phase1_receipt']
            and release['source_pins'].get(phase_pin['path'])==phase_pin['sha256'],
            'Baseline16 phase1 accounting linkage changed')
    phase=pinned_json(release,phase_pin['path'])
    require(phase['status']=='complete' and phase['before']==phase['after']==phase['source_evidence']
            ==baseline['before']==baseline['after']==baseline['source_evidence'] and phase['changed']==[]
            and phase['latest_run']=='prior-segment-output-E' and phase['latest_owner_cleanup']=='UNKNOWN'
            and phase['latest_original_wait_available'] is False,'Phase1 and phase2 stopped-source evidence differs')
    require(phase['native_ancestry_before']==phase['native_ancestry_after']==baseline['native_ancestry_before']
            and all(phase['ownership_locks'][role]==baseline['ownership_locks'][role]
                    for role in ('instance','uploader')),'Phase1 ancestry or lock identity changed')
    native_key='qualified-data/native-owned-cells-bounded-v1/cells.sqlite3'
    prior=pinned_json(release,old['resume']['baseline_receipt'])
    reuse=baseline['native_reuse']
    require(reuse==dict(source_baseline=old['resume']['baseline_receipt'],
                baseline_sha256=release['source_pins'][old['resume']['baseline_receipt']],
                raw_copy=str(Path(old['resume']['baseline_receipt']).parent/'native-raw/cells.sqlite3'),
                raw_copy_sha256=baseline['source_evidence'][native_key]['sha256'],exact_current_source_match=True)
            and baseline['source_evidence'][native_key]==prior['source_evidence'][native_key]
            and baseline['native_binding']==prior['native_binding']
            and release['source_pins'].get(reuse['raw_copy'])==reuse['raw_copy_sha256'],
            'Exact reused native evidence changed')
    snapshot=str(Path(release['resume']['baseline_receipt']).parent/'stopped.sqlite3')
    require(release['source_pins'].get(snapshot)==baseline['snapshot_sha256']
            ==baseline['source_evidence']['qualified-data/jobs.sqlite3']['sha256']
            and baseline['source_evidence']['qualified-data/jobs.sqlite3']['identity'][2]==REDACTED_COORDINATE_54F5CE4B6B
            and baseline['source_evidence']['qualified-data/jobs.sqlite3-wal']['identity'][2]==0,
            'Baseline16 exact single-copy/empty-WAL provenance changed')
    previous_launch=pinned_json(release,release['resume']['previous_launch'])
    require(previous_launch['argv']==old['wrapper_argv']
            and previous_launch['term_deadline_unix']==old['resume']['term_deadline_unix']
            and previous_launch['kill_ceiling_unix']==old['resume']['kill_ceiling_unix'],
            'Lost run23 launch does not match the qualified predecessor')
    provenance=release['baseline16_provenance']
    require(set(provenance)=={'phase2_root_wait','provenance_audit','prelaunch_helper_sha_available','exact_executed_helper_bytes_proven'}
            and provenance['prelaunch_helper_sha_available'] is False
            and provenance['exact_executed_helper_bytes_proven'] is False,
            'Baseline16 helper-source uncertainty cannot be promoted to proof')
    for key in ('phase2_root_wait','provenance_audit'):
        pin=provenance[key]
        require(set(pin)=={'path','sha256'} and release['source_pins'].get(pin['path'])==pin['sha256'],
                'Baseline16 provenance pin missing: '+key)
        evidence=pinned_json(release,pin['path'])
        if key=='phase2_root_wait':
            require(type(evidence['returncode']) is int and evidence['returncode']==0
                    and evidence['positive_original_wait'] is True,
                    'Baseline16 collector needs its own positive root wait, not a lost-run wait')
            collector_wait=evidence
        else:
            require(evidence['status']=='current_readonly_audit_complete'
                    and evidence['root_receipt']==collector_wait,
                    'Baseline16 current audit is not the collector wait evidence')
            audited={row['path']:row for row in evidence['current_audit']}
            manifest=release['resume']['completed_manifest']
            require(set(audited)=={snapshot,manifest}
                    and audited[snapshot]['sha256']==baseline['snapshot_sha256']
                    and audited[snapshot]['matches_phase1_primary_hash'] is True
                    and audited[manifest]['sha256']==baseline['completed_manifest_sha256']
                    and all(row['matches_baseline16_receipt'] is True for row in audited.values()),
                    'Baseline16 audited data conservation changed')
            for path,sha in evidence['evidence_pins'].items():
                require(release['source_pins'].get(path)==sha,'Baseline16 audit evidence pin missing')


def require_loss23_accounting(history,accounting,allowance,prior_history,old):
    """Add loss23 durable evidence and bounded tail to the already charged carry."""
    require(accounting['schema']=='loss23-carried-accounting-v2'
            and accounting['status']=='derived_evidence_proposal_not_terminal','Qualified loss23 accounting evidence')
    require(accounting['observer_budget_reset'] is False and history['observer_budget_reset'] is False
            and accounting['historical_request_accounting']==history['historical_request_accounting']=='UNKNOWN',
            'Loss23 accounting cannot reset history or imply global usage proof')
    require(history['observer_charge_kind']=='cumulative_conservative_charge_with_historical_reserves'
            and accounting['latest_owner_cleanup']==history['latest_owner_cleanup']=='UNKNOWN'
            and accounting['latest_original_wait_available'] is history['latest_original_wait_available'] is False,
            'Lost run23 must remain UNKNOWN; cumulative charges are not measured usage')
    require(allowance==old['resume']['observation_allowance']==accounting['allowance'],'Observation allowance changed')
    require(accounting['prior_carried']==dict(requests=REDACTED_COORDINATE_7DCCA41D83,decoded_bytes=REDACTED_COORDINATE_0B760BF1C8,
                kind='cumulative_conservative_charge_with_historical_reserves')
            and prior_history['observer_requests']==REDACTED_COORDINATE_7DCCA41D83 and prior_history['decoded_bytes']==REDACTED_COORDINATE_0B760BF1C8,
            'Prior cumulative charge including conservative reserves was lost')
    durable=accounting['durable_http'];charge=accounting['conservative_charge'];prefix=accounting['charged_prefix']
    for section in (accounting['prior_carried'],prefix,charge):
        require(all(type(section[key]) is int and section[key]>=0 for key in ('requests','decoded_bytes')),
                'Invalid loss23 charged counter')
    for key in ('rows','first_sequence','last_sequence','body_bytes','new_request_attempts_lower_bound'):
        require(type(durable[key]) is int and durable[key]>=0,'Invalid loss23 durable counter')
    require(durable['rows']==7 and durable['first_sequence']==REDACTED_COORDINATE_8835F492A7 and durable['last_sequence']==REDACTED_COORDINATE_670A7F467E
            and durable['body_bytes']==REDACTED_COORDINATE_0283E6282A and durable['missing_sequences']==list(range(REDACTED_COORDINATE_BC805497F8,REDACTED_COORDINATE_8835F492A7))
            and durable['new_request_attempts_lower_bound']==15
            and durable['rows']+len(durable['missing_sequences'])==durable['new_request_attempts_lower_bound'],
            'Exact loss23 durable HTTP evidence changed')
    require(prefix['requests']==REDACTED_COORDINATE_670A7F467E and prefix['decoded_bytes']==REDACTED_COORDINATE_7D8AE98500 and prefix['is_actual_usage'] is False
            and prefix['requests']==accounting['prior_carried']['requests']+durable['new_request_attempts_lower_bound']
            and prefix['decoded_bytes']==accounting['prior_carried']['decoded_bytes']+durable['body_bytes'],
            'Loss23 charged-prefix conservation changed')
    require(charge==dict(requests=REDACTED_COORDINATE_09C2A7BBA8,decoded_bytes=REDACTED_COORDINATE_4F8B747129,unlogged_tail_requests=1,
                body_reserve_slots=9,body_reserve_each=REDACTED_COORDINATE_1DCFAA9A1F,body_reserve_bytes=REDACTED_COORDINATE_D3BDD718D3,
                kind='conditional_conservative_bound_not_actual_usage')
            and all(type(charge[key]) is int for key in ('unlogged_tail_requests','body_reserve_slots','body_reserve_each','body_reserve_bytes'))
            and charge['requests']==prefix['requests']+charge['unlogged_tail_requests']
            and charge['body_reserve_slots']==len(durable['missing_sequences'])+charge['unlogged_tail_requests']
            and charge['body_reserve_each']==allowance['individual_body_cap']+1
            and charge['decoded_bytes']==prefix['decoded_bytes']+charge['body_reserve_slots']*charge['body_reserve_each'],
            'Loss23 conservative failed/tail reserve changed')
    require(type(history['observer_requests']) is int and history['observer_requests']==charge['requests']
            and type(history['decoded_bytes']) is int and history['decoded_bytes']==charge['decoded_bytes']
            and charge['requests']<=allowance['segment_get_cap'] and charge['decoded_bytes']<=allowance['segment_decoded_cap'],
            'Loss23 exact carry changed or allowance exhausted')
    counters=('prior_status_rounds','durable_completed_rounds','uncompleted_round_reserve','status_rounds_charge')
    require(all(type(accounting[key]) is int for key in counters)
            and accounting['prior_status_rounds']==old['resume']['previous_status_rounds']==REDACTED_COORDINATE_434DBD07BC
            and accounting['durable_completed_rounds']==1 and accounting['uncompleted_round_reserve']==1
            and accounting['status_rounds_charge']==REDACTED_COORDINATE_434DBD07BC+1+1
            and accounting['last_sample_at']==REDACTED_COORDINATE_95CF99B035,'Loss23 status/sample carry changed')
    require(history['stops']==prior_history['stops']+[
                dict(source='prior-segment-output-E',positive='UNKNOWN',original_wait_available=False)],
            'Loss23 cleanup history changed or promoted')
    resources=accounting['resources']
    for key,expected in (('disk_peak',accounting['disk']['peak_lower_bound']),
            ('log_bytes',accounting['logs']['prior_carried_log_bytes']+accounting['logs']['new_saved_log_bytes']),
            ('peak_charged',resources['peak_charged_lower_bound']),('peak_rss',resources['peak_rss_lower_bound']),
            ('namespace_exit_gaps',resources['namespace_exit_gaps_lower_bound'])):
        require(type(history[key]) is int and history[key]==expected and history[key]>=prior_history[key],
                'Loss23 resource lower-bound carry changed: '+key)
    require(history['max_sample_gap']==max(prior_history['max_sample_gap'],
                resources['new_logged_sample_gap'])
            and history['sampled_roles']==prior_history['sampled_roles'],
            'Loss23 sample gap or sampled roles changed')
    require(history['disk_census_history']==prior_history['disk_census_history']+[accounting['disk']]
            and history['original_recheck_counts']==prior_history['original_recheck_counts']
            ==accounting['original_recheck']['prior_lower_bound_counts'],
            'Loss23 disk/recheck history changed')
    recheck=history['original_recheck_history']
    require({key:value for key,value in recheck.items() if key!='prior_segment_output_E'}==prior_history['original_recheck_history']
            and recheck['prior_segment_output_E']['new_exact_calls'] is None
            and recheck['prior_segment_output_E']['new_exact_outcome_counts'] is None
            and recheck['prior_segment_output_E']['totals']=='UNKNOWN',
            'Unobserved loss23 recheck counters cannot become proven totals')


def require_loss25_baseline(baseline,terminal,wait,prior_baseline):
    require(baseline['status']=='complete' and baseline['before']==baseline['after']==baseline['source_evidence']
            and baseline['changed']==[] and type(baseline['exclusive_locks_acquired']) is int
            and baseline['exclusive_locks_acquired']==2 and baseline['ownership_status']=='both_existing_flocks_held',
            'Exact immutable dual-lock lost-run baseline required')
    require(baseline['historical_positive_stop'] is False and baseline['interrupted_owner_cleanup']=='UNKNOWN'
            and baseline['original_wait_available'] is False and baseline['historical_lost_executor']=='prior-segment-output-A'
            and baseline['historical_other_lost_executor']=='prior-segment-output-B','Earlier baseline history must remain UNKNOWN')
    require(baseline['latest_run']=='prior-segment-output-F' and baseline['latest_original_wait_available'] is False
            and baseline['latest_owner_cleanup']=='UNKNOWN' and baseline.get('latest_original_wait') is None
            and baseline.get('latest_stop') is None and baseline['latest_cleanup_proof']==
            'UNKNOWN: lost run25 original handle; current dual locks exclude cooperating holders only, not orphan or historical-cleanup proof',
            'Latest lost run25 cleanup cannot be inferred from present ownership')
    require(baseline['prior_run17_original_wait_available'] is True
            and baseline['prior_run17_owner_cleanup']=='positive'
            and baseline['prior_run17_original_wait']==wait and baseline['prior_run17_stop']==terminal['stops'][-1],
            'Prior positive run17 evidence changed')
    c=baseline['completed']
    require(all(type(c[key]) is int for key in ('completed','result_rows','origin','end','eos'))
            and {key:c[key] for key in ('completed','result_rows','origin','end','eos')}
            ==dict(completed=REDACTED_COORDINATE_C9DC1FB0C0,result_rows=REDACTED_COORDINATE_0B42D71285,origin=REDACTED_COORDINATE_24EB33C5F8,end=REDACTED_COORDINATE_C16F598A23,eos=0)
            and baseline['completed_manifest_sha256']==c['manifest_sha256']
            =='REDACTED_PRIVATE_EVIDENCE_SHA256',
            'Exact stopped run25 completed geometry/hash changed')
    require(baseline['prior2042_preserved']==REDACTED_COORDINATE_D8EC3903C8 and baseline['prior587_preserved']==REDACTED_COORDINATE_82A93B152B
            and baseline['restart2013_before_preserved']==baseline['restart2013_after_preserved']==REDACTED_COORDINATE_7931AA2A1B,
            'Stopped baseline result/restart preservation changed')
    require(baseline['native_ancestry_before']==baseline['native_ancestry_after'],'Stopped native ancestry changed')
    j=baseline['journal']
    require(all(type(j[key]) is int for key in ('inputs','clean_inputs','frames','published_frames','pages'))
            and (j['inputs'],j['clean_inputs'],j['frames'],j['published_frames'],j['pages'])
            ==(75,74,REDACTED_COORDINATE_1CBBC62834,REDACTED_COORDINATE_6478B9C579,REDACTED_COORDINATE_89AA1E5800) and j['finalized'] is False,'Exact stopped partial075 geometry')
    active=j['active_input']
    require(active['id']=='repeated-prefix-075' and type(active['frames']) is int and active['frames']==REDACTED_COORDINATE_07C9188141
            and type(active['normalized_start']) is int and active['normalized_start']==REDACTED_COORDINATE_3472831082
            and active['clean_eof'] is False,'Exact stopped partial075 cursor')
    observed=baseline['observed_boundary']
    require(observed==dict(active_input=active,archive_intent=None,completed_inputs=74,inputs=75,
                next_input_id='repeated-prefix-075',retirement=None,staged_frames=4704000),
            'Stopped partial075 observed boundary changed')
    require(baseline['recovered']['states']==dict(completed=REDACTED_COORDINATE_C9DC1FB0C0,queued=14,running=1,staging=28),
            'Stopped run25 work states changed')

    require(baseline['historical_run20_cleanup']=='UNKNOWN' and baseline['prior2989_preserved']==REDACTED_COORDINATE_C311B90801,
            'Earlier lost run20 cleanup or completed preservation changed')
    require(prior_baseline['journal']['clean_inputs']==66 and prior_baseline['completed']['completed']==REDACTED_COORDINATE_C311B90801
            and baseline['historical_cleanup_unknown']==['prior-segment-output-A','prior-segment-output-B',
                'prior-segment-output-D','prior-segment-output-E','prior-segment-output-F'],
            'Run25 progress must preserve the fixed prior baseline and UNKNOWN history')
    require(baseline['recovered']['failed_count']==0,'Failed work exists; automatic retry forbidden')



def require_loss25_accounting(history,accounting,allowance,prior_history,old):
    """Add loss25 durable evidence and bounded tail to the already charged carry."""
    require(accounting['schema']=='loss25-carried-accounting-v2'
            and accounting['status']=='derived_evidence_proposal_not_terminal','Qualified loss25 accounting evidence')
    require(accounting['observer_budget_reset'] is False and history['observer_budget_reset'] is False
            and accounting['historical_request_accounting']==history['historical_request_accounting']=='UNKNOWN',
            'Loss25 accounting cannot reset history or imply global usage proof')
    require(history['observer_charge_kind']=='cumulative_conservative_charge_with_historical_reserves'
            and accounting['latest_owner_cleanup']==history['latest_owner_cleanup']=='UNKNOWN'
            and accounting['latest_original_wait_available'] is history['latest_original_wait_available'] is False,
            'Lost run25 must remain UNKNOWN; cumulative charges are not measured usage')
    require(allowance==old['resume']['observation_allowance']==accounting['allowance'],'Observation allowance changed')
    require(accounting['prior_carried']==dict(requests=REDACTED_COORDINATE_09C2A7BBA8,decoded_bytes=REDACTED_COORDINATE_4F8B747129,
                kind='cumulative_conservative_charge_with_historical_reserves')
            and prior_history['observer_requests']==REDACTED_COORDINATE_09C2A7BBA8 and prior_history['decoded_bytes']==REDACTED_COORDINATE_4F8B747129,
            'Prior cumulative charge including conservative reserves was lost')
    durable=accounting['durable_http'];charge=accounting['conservative_charge'];prefix=accounting['charged_prefix']
    for section in (accounting['prior_carried'],prefix,charge):
        require(all(type(section[key]) is int and section[key]>=0 for key in ('requests','decoded_bytes')),
                'Invalid loss25 charged counter')
    for key in ('rows','first_sequence','last_sequence','body_bytes','new_request_attempts_lower_bound'):
        require(type(durable[key]) is int and durable[key]>=0,'Invalid loss25 durable counter')
    require(durable['rows']==REDACTED_COORDINATE_A934C24475 and durable['first_sequence']==REDACTED_COORDINATE_69BE0182F4 and durable['last_sequence']==REDACTED_COORDINATE_5C7A722D8D
            and durable['body_bytes']==REDACTED_COORDINATE_026AADE9A0 and durable['missing_sequences']==list(range(REDACTED_COORDINATE_36BE8183AD,REDACTED_COORDINATE_69BE0182F4))
            and durable['new_request_attempts_lower_bound']==REDACTED_COORDINATE_26D228663F
            and durable['rows']+len(durable['missing_sequences'])==durable['new_request_attempts_lower_bound'],
            'Exact loss25 durable HTTP evidence changed')
    require(prefix['requests']==REDACTED_COORDINATE_5C7A722D8D and prefix['decoded_bytes']==REDACTED_COORDINATE_8259092687 and prefix['is_actual_usage'] is False
            and prefix['requests']==accounting['prior_carried']['requests']+durable['new_request_attempts_lower_bound']
            and prefix['decoded_bytes']==accounting['prior_carried']['decoded_bytes']+durable['body_bytes'],
            'Loss25 charged-prefix conservation changed')
    require(charge==dict(requests=REDACTED_COORDINATE_7CA2552FFD,decoded_bytes=REDACTED_COORDINATE_BC330203F5,unlogged_tail_requests=1,
                body_reserve_slots=10,body_reserve_each=REDACTED_COORDINATE_1DCFAA9A1F,body_reserve_bytes=REDACTED_COORDINATE_DC64623A21,
                kind='conditional_conservative_bound_not_actual_usage')
            and all(type(charge[key]) is int for key in ('unlogged_tail_requests','body_reserve_slots','body_reserve_each','body_reserve_bytes'))
            and charge['requests']==prefix['requests']+charge['unlogged_tail_requests']
            and charge['body_reserve_slots']==len(durable['missing_sequences'])+charge['unlogged_tail_requests']
            and charge['body_reserve_each']==allowance['individual_body_cap']+1
            and charge['decoded_bytes']==prefix['decoded_bytes']+charge['body_reserve_slots']*charge['body_reserve_each'],
            'Loss25 conservative failed/tail reserve changed')
    require(type(history['observer_requests']) is int and history['observer_requests']==charge['requests']
            and type(history['decoded_bytes']) is int and history['decoded_bytes']==charge['decoded_bytes']
            and charge['requests']<=allowance['segment_get_cap'] and charge['decoded_bytes']<=allowance['segment_decoded_cap'],
            'Loss25 exact carry changed or allowance exhausted')
    counters=('prior_status_rounds','durable_completed_rounds','uncompleted_round_reserve','status_rounds_charge')
    require(all(type(accounting[key]) is int for key in counters)
            and accounting['prior_status_rounds']==old['resume']['previous_status_rounds']==REDACTED_COORDINATE_A183251A84
            and accounting['durable_completed_rounds']==REDACTED_COORDINATE_6566230E3A and accounting['uncompleted_round_reserve']==1
            and accounting['status_rounds_charge']==REDACTED_COORDINATE_A183251A84+REDACTED_COORDINATE_6566230E3A+1
            and accounting['last_sample_at']==REDACTED_COORDINATE_76A1FC3BB4,'Loss25 status/sample carry changed')
    require(history['stops']==prior_history['stops']+[
                dict(source='prior-segment-output-F',positive='UNKNOWN',original_wait_available=False)],
            'Loss25 cleanup history changed or promoted')
    resources=accounting['resources']
    for key,expected in (('disk_peak',accounting['disk']['peak_lower_bound']),
            ('log_bytes',accounting['logs']['prior_carried_log_bytes']+accounting['logs']['new_saved_log_bytes']),
            ('peak_charged',resources['peak_charged_lower_bound']),('peak_rss',resources['peak_rss_lower_bound']),
            ('namespace_exit_gaps',resources['namespace_exit_gaps_lower_bound'])):
        require(type(history[key]) is int and history[key]==expected and history[key]>=prior_history[key],
                'Loss25 resource lower-bound carry changed: '+key)
    require(history['max_sample_gap']==max(prior_history['max_sample_gap'],
                resources['new_logged_sample_gap'])
            and history['sampled_roles']==prior_history['sampled_roles'],
            'Loss25 sample gap or sampled roles changed')
    require(history['disk_census_history']==prior_history['disk_census_history']+[accounting['disk']]
            and history['original_recheck_counts']==prior_history['original_recheck_counts']
            ==accounting['original_recheck']['prior_lower_bound_counts'],
            'Loss25 disk/recheck history changed')
    recheck=history['original_recheck_history']
    require({key:value for key,value in recheck.items() if key!='prior_segment_output_F'}==prior_history['original_recheck_history']
            and recheck['prior_segment_output_F']['new_exact_calls'] is None
            and recheck['prior_segment_output_F']['new_exact_outcome_counts'] is None
            and recheck['prior_segment_output_F']['totals']=='UNKNOWN',
            'Unobserved loss25 recheck counters cannot become proven totals')


    require(accounting['disk_capacity_amendment']==old['disk_capacity_amendment']
            and accounting['retained_root_artifact']==old['product_transition']['native_root_reattestation']
            and accounting['disk']['disk_ceiling_bytes']==10737418240,
            'Loss25 accounting must preserve the inherited disk amendment and artifact')


def require_baseline17_provenance(release,baseline,accounting,old):
    """Bind new sidecar-free copies and collector manifests without clearing old uncertainty."""
    require(release['baseline16_provenance']==old['baseline16_provenance'],
            'Historical baseline16 source-attribution caveat changed')
    phase_pin=baseline['phase1_receipt']
    require(set(phase_pin)=={'path','sha256'} and phase_pin['path']==accounting['baseline_phase1_receipt']
            and release['source_pins'].get(phase_pin['path'])==phase_pin['sha256'],
            'Baseline17 phase1 accounting linkage changed')
    phase=pinned_json(release,phase_pin['path'])
    require(phase['status']=='complete' and phase['before']==phase['after']==phase['source_evidence']
            ==baseline['before']==baseline['after']==baseline['source_evidence'] and phase['changed']==[]
            and phase['journal']==baseline['journal'] and phase['latest_run']=='prior-segment-output-F'
            and phase['latest_owner_cleanup']=='UNKNOWN' and phase['latest_original_wait_available'] is False,
            'Phase1 and phase2 stopped-source or cursor evidence differs')
    require(phase['native_ancestry_before']==phase['native_ancestry_after']==baseline['native_ancestry_before']
            and all(phase['ownership_locks'][role]==baseline['ownership_locks'][role]
                    for role in ('instance','uploader')),'Phase1 ancestry or lock identity changed')
    require(phase['historical_baseline16_source_attribution']==
            'Missing prelaunch helper-SHA attestation remains; separate provenance note is pinned, not repaired',
            'Historical missing helper-source attestation cannot be repaired retroactively')
    lineage={old['resume']['baseline_receipt']:release['source_pins'][old['resume']['baseline_receipt']],
             release['resume']['previous_release']:release['source_pins'][release['resume']['previous_release']],
             release['resume']['previous_launch']:release['source_pins'][release['resume']['previous_launch']],
             old['baseline16_provenance']['provenance_audit']['path']:old['baseline16_provenance']['provenance_audit']['sha256']}
    require(baseline['current_lineage_pins']==lineage,'Baseline17 exact predecessor provenance changed')
    for evidence in (phase['evidence_pins'],baseline['current_lineage_pins']):
        for path,sha in evidence.items():
            require(release['source_pins'].get(path)==sha,'Baseline17 source evidence pin missing')
    launch=pinned_json(release,release['resume']['previous_launch'])
    require(launch['argv']==old['wrapper_argv']
            and launch['term_deadline_unix']==old['resume']['term_deadline_unix']
            and launch['kill_ceiling_unix']==old['resume']['kill_ceiling_unix'],
            'Lost run25 launch does not match the qualified predecessor')
    directory=Path(release['resume']['baseline_receipt']).parent
    snapshot=str(directory/'stopped.sqlite3');native=str(directory/'native.sqlite3')
    source=baseline['source_evidence'];primary_key='qualified-data/jobs.sqlite3'
    native_key='qualified-data/native-owned-cells-bounded-v1/cells.sqlite3'
    require({key for key in source if key.startswith(primary_key)}=={primary_key}
            and {key for key in source if key.startswith('qualified-data/native-owned-cells-bounded-v1/')}=={native_key},
            'Baseline17 exact sidecar-free source families required')
    require(release['source_pins'].get(snapshot)==baseline['snapshot_sha256']==source[primary_key]['sha256']
            and source[primary_key]['identity'][2]==REDACTED_COORDINATE_803D1382BC
            and baseline['snapshot_copy_identity'][2:5]==[REDACTED_COORDINATE_D92C71F093,1,REDACTED_COORDINATE_803D1382BC],
            'Baseline17 exact main snapshot provenance changed')
    copied=baseline['native_copy']
    artifact=pinned_json(release,release['product_transition']['native_root_reattestation']['path'])
    require(set(copied)=={'path','sha256','bytes','identity','schema_sha256','copied_only_immutable_query'}
            and copied['path']==native and copied['bytes']==source[native_key]['identity'][2]==REDACTED_COORDINATE_85F5EAD397
            and copied['sha256']==source[native_key]['sha256']==release['source_pins'].get(native)
            and copied['identity'][2:5]==[REDACTED_COORDINATE_D92C71F093,1,REDACTED_COORDINATE_85F5EAD397]
            and copied['schema_sha256']==artifact['native_schema_sha256']
            and copied['copied_only_immutable_query'] is True,
            'Baseline17 new native copy or exact schema changed')
    prior=pinned_json(release,old['resume']['baseline_receipt'])
    require(baseline['native_binding']==prior['native_binding']
            and source[native_key]['sha256']!=prior['source_evidence'][native_key]['sha256']
            and source[primary_key]['sha256']!=prior['source_evidence'][primary_key]['sha256'],
            'Baseline17 must prove changed data with the same exact stored binding')
    provenance=release['baseline17_provenance']
    require(set(provenance)=={'phase1_root_wait','phase2_root_wait','phase1_source_manifest',
                'phase2_source_manifest','latest_run_cleanup'} and provenance['latest_run_cleanup']=='UNKNOWN',
            'Collector waits cannot establish lost run25 cleanup')
    for number,receipt,reserved in ((1,phase,REDACTED_COORDINATE_1D0F2369CC),(2,baseline,REDACTED_COORDINATE_9C4CC9EC78)):
        prefix='phase'+str(number);wait_pin=provenance[prefix+'_root_wait']
        manifest_pin=provenance[prefix+'_source_manifest']
        for pin in (wait_pin,manifest_pin):
            require(set(pin)=={'path','sha256'} and release['source_pins'].get(pin['path'])==pin['sha256'],
                    'Baseline17 collector provenance pin missing')
        wait=pinned_json(release,wait_pin['path']);manifest=pinned_json(release,manifest_pin['path'])
        require(type(wait['returncode']) is int and wait['returncode']==0
                and type(wait['start']) in (int,float) and type(wait['finish']) in (int,float)
                and 0<wait['start']<=wait['finish']
                and wait['manifest_sha256']==manifest_pin['sha256']
                and wait['reserve_bytes']==reserved
                and wait['disk_bytes_before']+reserved<=10737418240
                and wait['free_bytes_before']-reserved>=5905580032,
                'Baseline17 saved collector wait/manifest/admission changed')
        helper_dir=Path(manifest_pin['path']).parent
        helper=str(helper_dir/'probe.py');passive=str(helper_dir/'passive.py')
        proposal_path=str(helper_dir/'launch-proposal.json')
        require(set(manifest)=={helper,passive,proposal_path},'Baseline17 exact helper manifest changed')
        for path,sha in manifest.items():
            require(release['source_pins'].get(path)==sha,'Baseline17 helper source pin missing')
        check=receipt['helper_source_check']
        require(check['expected_sha256']==check['observed_sha256_at_entry']==manifest[helper],
                'Baseline17 helper entry hash changed')
        proposal=pinned_json(release,proposal_path)
        require(proposal['argv'][-2:]==[helper,manifest[helper]],'Baseline17 helper launch hash argument changed')
        for path,sha in proposal['evidence_pins'].items():
            require(release['source_pins'].get(path)==sha,'Baseline17 collector launch evidence pin missing')
