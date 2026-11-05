"""Read-only controlled-producer capacity arithmetic and stopped inventory.

No HTTP, mkdir, lock creation, cleanup, replay, or receiver activation. Capacity
is conditional on declared mono 16 kHz PCM16 cadence, never upload authority.
"""
import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path

from . import recorder_capture_profile as capture
from . import recorder_receiver_completion as completion
from . import recorder_receiver_publication as publication
from . import recorder_ftps_receiver as receiver
from . import recorder_inbox_runner as runner
from . import recorder_intake as single
from . import recording_uploader as u

require = single.require
RESERVE = sum(completion.LIMITS.values())


def profile(*, segment_seconds=300, max_attempts=144, max_raw_bytes=8*1024**3,
            max_hours=12, max_inputs=192, generations=3, sample_rate=16000,
            channels=1, sample_width=2, phase_max_actions=16, phase_max_polls=192,
            max_total_actions=32768, max_total_polls=4096, max_requests=4096,
            max_network_writes=1024, max_network_bytes=256*1024**2):
    for value, upper in ((max_attempts,256),(max_raw_bytes,8*1024**3),(generations,8),
            (phase_max_actions,4096),(phase_max_polls,4096),(max_total_actions,65536),
            (max_total_polls,65536),(max_requests,65536),(max_network_writes,8192),
            (max_network_bytes,runner.MAX_NETWORK_BYTES)):
        require(type(value) is int and 1 <= value <= upper, 'capacity_profile_bound')
    simulation = capture.plan(segment_seconds=segment_seconds,max_hours=max_hours,
        max_inputs=max_inputs,expected_seconds=min(48*3600,max_attempts*segment_seconds),
        producer_mode='terminal-aware')
    issues=[]
    supported=(sample_rate,channels,sample_width)==(16000,1,2)
    if not supported: issues.append('unsupported_format_unqualified')
    if max_attempts >= max_inputs: issues.append('strict_analysis_input_headroom_required')
    if simulation['full_rotations_before_limit'] < max_attempts:
        issues.append('cadence_exceeds_analysis_duration_count_or_source_slots')
    # Full WAV header/metadata allowance per controlled original; this deliberately
    # overestimates small RIFF headers. Arbitrary ancillary WAV chunks are excluded.
    per_input=segment_seconds*16000*2+65536+completion.LIMITS[publication.FILES[0]]+completion.LIMITS[publication.FILES[1]]
    peak_raw=(max_attempts-1)*per_input+RESERVE
    peak_publication=2*max_attempts*per_input+RESERVE
    if peak_raw > max_raw_bytes: issues.append('raw_full_attempt_reservation_shortfall')
    if peak_publication > publication.MAX_STORAGE_BYTES: issues.append('publication_reservation_shortfall')
    if phase_max_actions < 3: issues.append('phase_actions_cannot_finalize_status_export')
    if phase_max_polls <= max_attempts: issues.append('phase_polls_cannot_drain_full_backlog')
    # Conservative upper allowance: every edge consumes all discovery rounds plus
    # one transition; final phase may reserve an action allowance on every discovery round.
    edges=generations-1
    reserved_actions=(edges*(phase_max_polls+1)+phase_max_polls)*phase_max_actions
    reserved_polls=edges*2*phase_max_polls+2*phase_max_polls
    if reserved_actions>max_total_actions: issues.append('aggregate_action_reservation_shortfall')
    if reserved_polls>max_total_polls: issues.append('aggregate_poll_reservation_shortfall')
    bootstrap_requests=edges*6
    if max_requests < bootstrap_requests: issues.append('initializer_request_reserve_shortfall')
    if max_network_writes < edges: issues.append('initializer_write_reserve_shortfall')
    if max_network_bytes < max(1,edges)*6*u.JSON_BYTES: issues.append('initializer_response_reserve_shortfall')
    return dict(schema='recorder-capacity-preflight-v1',read_only=True,network_requests=0,
        filesystem_writes=0,conditional_profile_fits=not issues,issues=issues,
        controlled_producer_only=True,runtime_completion_guaranteed=False,
        arbitrary_uploads_qualified=False,indefinite_operation=False,physical_continuity_proven=False,
        terminal_aware_stop_required=True,full_final_rotation_is_not_eos=True,
        segment_seconds=segment_seconds,target_frames=segment_seconds*16000,
        max_attempts=max_attempts,max_raw_bytes=max_raw_bytes,max_hours=max_hours,max_inputs=max_inputs,
        generations=generations,per_generation_seconds=max_attempts*segment_seconds,
        full_attempt_reservation_bytes=RESERVE,controlled_input_upper_bytes=per_input,
        clean_profile_peak_raw_reservation_bytes=peak_raw,
        clean_profile_peak_publication_reservation_bytes=peak_publication,
        worst_case_reserved_actions=reserved_actions,worst_case_reserved_polls=reserved_polls,
        initializer_min_requests_only=bootstrap_requests,initializer_min_writes_only=edges,
        initializer_response_reservation_only=max(1,edges)*6*u.JSON_BYTES,
        identity_preservation_budget_not_included=True,
        network_and_deadline_sufficiency_unproven=True,
        incomplete_attempts_may_rotate_earlier=True,capture_profile=simulation)


def inspect(*, receiver_config, runner_state, segment_seconds=300, deadline_seconds=30, **budgets):
    """Lock ALL retained namespaces stopped, then inventory without modifying bytes.

    Failure/partial/unstarted uploads have no durable retry authority: report them
    collectively as unpublished, and reserve a full allowance for each.
    """
    path=publication._path(str(receiver_config)); raw=receiver._private_file(path,16384)
    service=receiver._validate_config(json.loads(raw,object_pairs_hook=receiver._unique_pairs))
    state=runner._path(str(runner_state)); single.private_directory(state)
    config=single.manifest_read(state/'config.json'); runner._config(config,state)
    entries=service.generations or [dict(binding=service.binding,inbox_dir=str(service.inbox_dir))]
    require(any(item['binding']==config.get('receiver_publication') and item['inbox_dir']==config['inbox_dir'] for item in entries), 'capacity_runner_not_retained')
    report=profile(segment_seconds=segment_seconds,max_attempts=service.max_attempts,
        max_raw_bytes=service.max_raw_bytes,max_hours=config['max_hours'],max_inputs=config['max_inputs'],
        generations=len(entries),**budgets)
    if config['span_origin']['target_frames'] != segment_seconds*16000:
        report['issues'].append('runner_target_frames_profile_mismatch')
        report['conditional_profile_fits']=False
    remaining=publication._deadline(deadline_seconds)
    rows=[]
    all_names=set()
    with ExitStack() as stack:
        roots=[]
        for entry in entries:
            cfg=publication.validate_binding(entry['binding'],entry['inbox_dir'])
            rawdir=Path(cfg['staging_dir']); fd=os.open(rawdir,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            stack.callback(os.close,fd)
            publication.snapshot._same_directory(rawdir,fd)
            require([os.fstat(fd).st_dev,os.fstat(fd).st_ino]==cfg['identities']['staging'],'capacity_raw_identity_changed')
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise publication.Refused('capacity_receiver_must_be_stopped')
            stack.enter_context(publication._lock(Path(cfg['state_dir']),cfg,'preparation.lock',remaining,shared=True))
            stack.enter_context(publication._lock(Path(cfg['state_dir']),cfg,'publication.lock',remaining,shared=True))
            roots.append((entry,cfg,fd))
        for entry,cfg,fd in roots:
            remaining()
            names,total=completion._retained_raw(fd,completion.MAX_ATTEMPTS,completion.MAX_RAW_BYTES,remaining)
            require(not all_names.intersection(names),'capacity_duplicate_retained_attempt')
            all_names.update(names)
            inboxfd=os.open(cfg['inbox_dir'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            preparefd=os.open(Path(cfg['state_dir'])/'prepare',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                publication.snapshot._same_directory(Path(cfg['inbox_dir']),inboxfd)
                publication.snapshot._same_directory(Path(cfg['state_dir'])/'prepare',preparefd)
                require([os.fstat(inboxfd).st_dev,os.fstat(inboxfd).st_ino]==cfg['identities']['inbox'],'capacity_inbox_identity_changed')
                require([os.fstat(preparefd).st_dev,os.fstat(preparefd).st_ino]==cfg['identities']['prepare'],'capacity_prepare_identity_changed')
                published=publication._names(inboxfd,256); pending=publication._names(preparefd,256)
                require(published <= names,'capacity_published_without_raw')
                stored=publication._storage_bytes(inboxfd,preparefd,remaining)
                publication.snapshot._same_directory(Path(cfg['inbox_dir']),inboxfd)
                publication.snapshot._same_directory(Path(cfg['state_dir'])/'prepare',preparefd)
            finally: os.close(inboxfd); os.close(preparefd)
            require(len(names)<=service.max_attempts and total<=service.max_raw_bytes,'capacity_existing_inventory_exceeds_config')
            unpublished=len(names-published)
            rows.append(dict(inbox_dir=cfg['inbox_dir'],retained_attempts=len(names),
                published_attempts=len(published),unpublished_attempts=unpublished,
                pending_publications=len(pending),raw_bytes=total,publication_bytes=stored,
                conservative_unpublished_reserve_bytes=unpublished*RESERVE,
                stopped_next_raw_admission_possible=len(names)<service.max_attempts and total+RESERVE<=service.max_raw_bytes,
                pending_publication_blocks_admission=bool(pending),
                publication_headroom_available=stored+total+RESERVE<=publication.MAX_STORAGE_BYTES))
            publication.validate_binding(entry['binding'],entry['inbox_dir'])
        for entry,cfg,fd in roots:
            publication.snapshot._same_directory(Path(cfg['staging_dir']),fd)
            publication.validate_binding(entry['binding'],entry['inbox_dir'])
        require(receiver._private_file(path,16384)==raw,'capacity_receiver_config_changed')
        require(single.manifest_read(state/'config.json')==config,'capacity_runner_config_changed')
    report.update(stopped_inventory_verified=True,
        all_generations_have_admission_headroom=all(row['stopped_next_raw_admission_possible'] and
            row['publication_headroom_available'] and not row['pending_publication_blocks_admission'] for row in rows),
        actual_media_format_verified=False,live_configuration_verified=False,generation_inventory=rows,
        runner_state=str(state),existing_data_changed=False)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--receiver-config');p.add_argument('--runner-state')
    for key,default in [('segment-seconds',300),('max-attempts',144),('max-raw-bytes',8*1024**3),
        ('max-hours',12),('max-inputs',192),('generations',3),('phase-max-actions',16),
        ('phase-max-polls',192),('max-total-actions',32768),('max-total-polls',4096),
        ('max-requests',4096),('max-network-writes',1024),('max-network-bytes',256*1024**2)]:
        p.add_argument('--'+key,type=int,default=default)
    a=vars(p.parse_args(argv));rc=a.pop('receiver_config');rs=a.pop('runner_state')
    try:
        require(bool(rc)==bool(rs),'capacity_both_configs_required')
        if rc:
            for k in ('max_attempts','max_raw_bytes','max_hours','max_inputs','generations'):a.pop(k)
            result=inspect(receiver_config=rc,runner_state=rs,**a)
        else:result=profile(**a)
        print(json.dumps(result,sort_keys=True));return 0 if result['conditional_profile_fits'] else 2
    except (Exception,KeyboardInterrupt):
        print(json.dumps(dict(status='capacity_refused',network_requests=0,filesystem_writes=0,
            existing_data_changed=False)));return 2

if __name__=='__main__':raise SystemExit(main())
