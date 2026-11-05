"""Finite, immutable analysis handoff plan over already prepared generations."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time

from . import recorder_analysis_handoff as handoff
from . import recorder_ftps_receiver as receiver
from . import recorder_receiver_publication as publication
from . import recorder_receiver_routing as routing
from . import recorder_runner_initialize as initialize
from . import recorder_inbox_runner as runner
from . import recorder_logical_boundary as boundary
from . import recorder_intake as single
from . import recording_uploader as u

SCHEMA = 'recorder-finite-analysis-plan-v1'
INTENT = 'plan.intent.json'
MAX_PLAN_BYTES = 65536
MAX_TOTAL_ACTIONS = 65536
MAX_TOTAL_POLLS = 65536
EDGE_KEYS = {'route_config','next_state_dir','next_job_dir','next_output_dir','next_capture_id','next_session_name'}
IDENTITY_KEYS = {'preserve_reviewed_identity_group','identity_generation','identity_policy_sha256'}
require = single.require


def _read(path):
    raw = receiver._private_file(path, MAX_PLAN_BYTES)
    return raw, json.loads(raw, object_pairs_hook=receiver._unique_pairs)


def _validate(plan_file, control, guard):
    raw, plan = _read(plan_file)
    require(type(plan) is dict and set(plan) in ({'schema','state_dir','edges'},
            {'schema','state_dir','edges','identity'}) and plan['schema'] == SCHEMA,
            'plan_fields')
    require(type(plan['edges']) is list and len(plan['edges']) <= 7, 'plan_edge_bound')
    initial = runner._path(plan['state_dir']); single.private_directory(initial)
    config = single.manifest_read(initial/'config.json'); runner._config(config, initial)
    require(config['output_dir'] is not None and 'receiver_publication' in config, 'plan_initial_publication_export_required')
    identity = plan.get('identity', {})
    require(type(identity) is dict and set(identity) in (set(), IDENTITY_KEYS), 'plan_identity_fields')
    if identity:
        initialize.reviewed_handoff(config['session_id'], identity['preserve_reviewed_identity_group'],
            identity['identity_generation'], identity['identity_policy_sha256'])
    trees = [control, initial, Path(config['job_dir']), Path(config['output_dir'])]
    routes = []; templates = []; current = config; endpoint = None; generations = None
    for edge in plan['edges']:
        guard.check()
        require(type(edge) is dict and set(edge) == EDGE_KEYS, 'plan_edge_fields')
        require(type(edge['next_session_name']) is str and len(edge['next_session_name']) <= 200, 'plan_session_name')
        route = runner._path(edge['route_config'])
        route_raw = receiver._private_file(route, 16384)
        data = json.loads(route_raw, object_pairs_hook=receiver._unique_pairs)
        service = receiver._validate_config(data)
        require(service.generations is not None, 'plan_generations_required')
        entries, selected = routing.validate(service.generations, service.binding, service.inbox_dir)
        old = dict(binding=current['receiver_publication'], inbox_dir=current['inbox_dir'])
        old_indices = [i for i,item in enumerate(service.generations) if runner._same(item, old)]
        require(len(old_indices) == 1 and selected == old_indices[0]+1, 'plan_adjacent_forward_generation_required')
        invariant = {key:value for key,value in data.items() if key not in ('binding','inbox_dir')}
        if endpoint is None:
            endpoint = invariant; generations = service.generations
        require(runner._same(endpoint, invariant), 'plan_endpoint_or_generations_changed')
        pair, provenance, configs = boundary.routing_selection(route, current)
        require(provenance['config_sha256'] == hashlib.sha256(route_raw).hexdigest(), 'plan_route_changed')
        routes.append(dict(path=str(route), sha256=provenance['config_sha256'], provenance=provenance))
        state = runner._path(edge['next_state_dir'])
        template = dict(current, session_id=initialize.PENDING, job_dir=str(runner._path(edge['next_job_dir'])),
            output_dir=str(runner._path(edge['next_output_dir'])), capture_id=edge['next_capture_id'],
            inbox_dir=pair['inbox_dir'], receiver_publication=pair['binding'])
        # Origin is only a syntactic placeholder here; actual origins come from verified originals.
        template.pop('reviewed_identity_handoff', None); template['schema'] = runner.CONFIG_SCHEMA
        runner._config(template, state)
        trees.extend((state, Path(template['job_dir']), Path(template['output_dir'])))
        templates.append(template); current = template
    if generations is None:
        generations = [dict(binding=config['receiver_publication'], inbox_dir=config['inbox_dir'])]
    for item in generations:
        cfg = publication.validate_binding(item['binding'], item['inbox_dir'])
        trees.extend(Path(cfg[k]) for k in ('state_dir','staging_dir','inbox_dir'))
    for i, path in enumerate(trees):
        guard.check(); runner._outside_git(path); single.private_directory(path.parent)
        require(not any(path.is_relative_to(other) or other.is_relative_to(path) for other in trees[:i]),
                'plan_trees_overlap')
    for file in [plan_file, *(Path(row['path']) for row in routes)]:
        require(not any(file.is_relative_to(tree) for tree in trees), 'plan_control_file_inside_work_tree')
    require(receiver._private_file(plan_file, MAX_PLAN_BYTES) == raw, 'plan_changed')
    intent = dict(schema=SCHEMA, plan_file=str(plan_file), plan_sha256=hashlib.sha256(raw).hexdigest(),
        initial_config_sha256=boundary.digest(config), plan=plan, routes=routes)
    require(len(single._json(intent)) <= single.MAX_MANIFEST, 'plan_intent_size')
    return plan, config, intent, templates


def _existing_destinations(plan, config, templates, resumed):
    """Reject foreign destinations and uncertain creation before any chain HTTP."""
    previous_state = Path(plan['state_dir']); previous_job = Path(config['job_dir'])
    for edge, template in zip(plan['edges'], templates):
        state = Path(edge['next_state_dir']); job = Path(edge['next_job_dir']); output = Path(edge['next_output_dir'])
        if any(os.path.lexists(p) for p in (state, job, output)):
            require(resumed and os.path.lexists(previous_job/boundary.NAME), 'plan_unowned_existing_destination')
            boundary.verify_anchor(previous_job, previous_state)
            cut = single.manifest_read(previous_job/boundary.NAME)
            expected = dict(state_dir=str(state),job_dir=str(job),output_dir=str(output),
                capture_id=edge['next_capture_id'],session_name=edge['next_session_name'])
            require(all(runner._same(cut['next_session_intent'].get(k),v) for k,v in expected.items()),
                    'plan_saved_destination_changed')
            if os.path.lexists(state):
                single.private_directory(state)
                root = state/initialize.DIRECTORY; single.private_directory(root)
                saved = single.manifest_read(root/'intent.json')
                require(all(runner._same(saved['template'].get(k), template[k]) for k in
                    ('api','job_dir','output_dir','capture_id','inbox_dir','receiver_publication','max_hours','max_inputs')),
                    'plan_initialization_template_changed')
                require(saved['request']['body']['name'] == edge['next_session_name'], 'plan_initialization_name_changed')
                require(not os.path.lexists(root/'post.intent.json') or os.path.lexists(root/'created.json'),
                        'plan_uncertain_creation_no_retry')
                if os.path.lexists(state/'config.json'):
                    existing = single.manifest_read(state/'config.json'); runner._config(existing, state)
                    require(all(runner._same(existing.get(k),template[k]) for k in
                        ('api','job_dir','output_dir','capture_id','inbox_dir','receiver_publication','max_hours','max_inputs')),
                        'plan_existing_config_changed')
                    if os.path.lexists(root/'complete.json'):initialize.validate_completed(state, existing)
            else:
                require(not os.path.lexists(job) and not os.path.lexists(output), 'plan_destination_without_state')
        previous_state, previous_job = state, job


class PhaseBudget:
    """Conservative phase reservations, retained even when a phase does less work."""
    def __init__(self, actions, polls, guard):
        self.max_actions,self.max_polls,self.guard = actions,polls,guard
        self.actions=self.polls=0

    def reserve(self, actions, polls):
        self.guard.check()
        if self.actions+actions > self.max_actions or self.polls+polls > self.max_polls:return False
        self.actions+=actions;self.polls+=polls
        return True


def run(*, plan_file, control_dir, acknowledge_finite_plan=False, exclusive_writer=False,
        acknowledge_span_local_closure=False, deadline_seconds=120, max_requests=4096,
        max_network_writes=1024, max_network_bytes=runner.MAX_NETWORK_BYTES,
        max_total_actions=32768, max_total_polls=4096, phase_max_actions=64,
        phase_max_polls=64, poll_seconds=1, export_recovery_attempts=0,busy_read_retries=0,
        limits=None, transport=None, cancel=None, native_turns=False,
        candidate_postphase=False, candidate_config=None):
    from . import recorder_candidate_postphase as candidates
    candidate_policy = candidates.policy(candidate_config, enabled=candidate_postphase, native_turns=native_turns)
    native_options = runner.native_export_options(native_turns, export_recovery_attempts)
    require(acknowledge_finite_plan is True and exclusive_writer is True and
            acknowledge_span_local_closure is True, 'plan_explicit_ack_required')
    require(type(deadline_seconds) in (int,float) and math.isfinite(deadline_seconds)
            and 1 <= deadline_seconds <= 86400, 'plan_deadline_bound')
    require(type(poll_seconds) in (int,float) and math.isfinite(poll_seconds) and .1 <= poll_seconds <= 3600,
            'plan_poll_interval_bound')
    for value,maximum in ((max_requests,runner.MAX_REQUESTS),(max_network_writes,runner.MAX_NETWORK_WRITES),
        (phase_max_actions,runner.MAX_WATCH_ACTIONS),(phase_max_polls,runner.MAX_WATCH_POLLS),
        (max_total_actions,MAX_TOTAL_ACTIONS),(max_total_polls,MAX_TOTAL_POLLS)):
        require(type(value) is int and 1 <= value <= maximum, 'plan_invocation_bound')
    require(type(max_network_bytes) is int and u.JSON_BYTES <= max_network_bytes <= runner.MAX_NETWORK_BYTES,
            'plan_network_byte_bound')
    require(type(export_recovery_attempts) is int and export_recovery_attempts in (0,2,3), 'plan_export_recovery_bound')
    require(type(busy_read_retries) is int and 0 <= busy_read_retries <= 3, 'plan_busy_read_bound')
    class PlanGuard(u.Guard):
        verify = None
        def check(self):
            super().check()
            if self.verify is not None:self.verify()
    guard = PlanGuard(u.Limits(deadline_seconds=deadline_seconds), cancel)
    control = runner._path(str(control_dir)); single.private_directory(control)
    file = runner._path(str(plan_file))
    plan, config, intent, templates = _validate(file, control, guard)
    for state in (plan['state_dir'], *(edge['next_state_dir'] for edge in plan['edges'])):
        runner.check_export_policy(Path(state), native_turns)
    if native_turns:intent['export_policy'] = runner.NATIVE_EXPORT_POLICY
    if candidate_policy is not None:
        candidates.validate_locations(candidate_policy,plan,config,templates,control,file)
        intent['candidate_policy'] = candidate_policy
    require(len(single._json(intent)) <= single.MAX_MANIFEST, 'plan_intent_size')
    require(runner._names(control,2) <= {'chain.lock',INTENT}, 'plan_unknown_control_file')
    lock = runner.engine.held_lock(control/'chain.lock', create=not os.path.lexists(control/'chain.lock'))
    try:
        resumed = os.path.lexists(control/INTENT)
        if resumed:require(runner._same(single.manifest_read(control/INTENT),intent), 'plan_saved_intent_changed')
        _existing_destinations(plan, config, templates, resumed)
        if candidate_policy is not None:
            candidates.initialize_ledger(candidate_policy,intent,resumed=resumed)
        if not resumed:single.manifest_write(control/INTENT,intent)
        runner._durable(control/INTENT,intent)
        control_identity=(control.stat().st_dev,control.stat().st_ino)
        lock_signature=u._signature(os.fstat(lock))
        def verify():
            single.private_directory(control)
            require((control.stat().st_dev,control.stat().st_ino)==control_identity and
                u._signature(os.fstat(lock))==lock_signature==u._signature((control/'chain.lock').lstat()),
                'plan_lock_changed')
            require(hashlib.sha256(receiver._private_file(file,MAX_PLAN_BYTES)).hexdigest()==intent['plan_sha256'],
                    'plan_changed')
            require(boundary.digest(single.manifest_read(Path(plan['state_dir'])/'config.json'))==intent['initial_config_sha256'],
                    'plan_initial_config_changed')
            require(runner._same(single.manifest_read(control/INTENT),intent), 'plan_saved_intent_changed')
            for row in intent['routes']:
                require(hashlib.sha256(receiver._private_file(Path(row['path']),16384)).hexdigest()==row['sha256'],
                        'plan_route_changed')
            for edge,template in zip(plan['edges'],templates):
                path=Path(edge['next_state_dir'])/'config.json'
                if os.path.lexists(path):
                    current_config=single.manifest_read(path)
                    require(all(runner._same(current_config.get(k),template[k]) for k in
                        ('api','job_dir','output_dir','capture_id','inbox_dir','receiver_publication','max_hours','max_inputs')),
                        'plan_destination_config_changed')
        guard.verify=verify
        def remaining():
            guard.check();return min(120,max(.000001,guard.end-time.monotonic()))
        base = transport or u.HTTPTransport(config['api'],guard)
        class Network:
            requests=writes=bytes=0
            mutation_stopped=False
            def request(self,method,path,**kwargs):
                guard.check()
                require(method=='GET' or not self.mutation_stopped,'plan_mutation_review_required')
                require(self.requests<max_requests and (method=='GET' or self.writes<max_network_writes)
                        and self.bytes+u.JSON_BYTES<=max_network_bytes,'plan_network_bound')
                self.requests+=1;self.writes+=method!='GET';self.bytes+=u.JSON_BYTES
                try:
                    value=base.request(method,path,**kwargs);guard.check()
                    require(type(value) is dict and len(single._json(value))<=u.JSON_BYTES,'plan_response_bound')
                except BaseException:
                    if method!='GET':self.mutation_stopped=True
                    raise
                self.bytes+=len(single._json(value))-u.JSON_BYTES
                return value
        network=Network(); phases=PhaseBudget(max_total_actions,max_total_polls,guard); completed=0
        def result(status,phase,**extra):
            value = dict(schema=SCHEMA,status=status,phase=phase,completed_edges=completed,
                unused_edges=len(plan['edges'])-completed,network_requests=network.requests,
                network_writes=network.writes,network_response_bytes=network.bytes,
                reserved_action_allowance=phases.actions,reserved_poll_allowance=phases.polls,
                max_total_actions=max_total_actions,max_total_polls=max_total_polls,
                invocation_bounded=True,automatic_retry=False,preserve_existing_files=True,
                recording_complete=False,recognition_continuity_verified=False,
                receiver_process_changed=False,**extra)
            if candidate_policy is not None:
                value['primary_status'] = status
                if status != 'plan_complete' or extra.get('terminal_stop_verified') is not True:
                    value['enhancement'] = candidates.status('enhancement_deferred',reason='primary_not_safely_complete',
                        candidate_attempts_started=0)
                else:
                    try:
                        states = [plan['state_dir'], *(e['next_state_dir'] for e in plan['edges'][:completed])]
                        generations=[]
                        for state_path in states:
                            cfg=single.manifest_read(Path(state_path)/'config.json')
                            generations.append((Path(cfg['job_dir']),Path(cfg['output_dir'])))
                        value['enhancement'] = candidates.drain(policy=candidate_policy,plan_intent=intent,
                            generations=generations,api_origin=config['api'],cancel=cancel,verify=verify)
                    except (Exception,KeyboardInterrupt) as exc:
                        value['enhancement'] = candidates.status('candidate_review_required',reason=single.failure_report(exc))
            return value
        def budgets():
            return dict(max_requests=max_requests-network.requests,max_network_writes=max_network_writes-network.writes,
                max_network_bytes=max_network_bytes-network.bytes)
        def available(values):
            return values['max_requests']>=1 and values['max_network_writes']>=1 and values['max_network_bytes']>=u.JSON_BYTES
        current=plan['state_dir']
        try:
            for edge in plan['edges']:
                guard.check(); allowance=budgets()
                if not available(allowance):return result('plan_paused','handoff',stop_reason='network_budget')
                if guard.end-time.monotonic()<1:return result('plan_paused','handoff',stop_reason='deadline_reached')
                linked=handoff._coordinate(state_dir=current,**edge,**plan.get('identity',{}),
                    acknowledge_automatic_handoff=True,exclusive_writer=True,acknowledge_span_local_closure=True,
                    deadline_seconds=remaining(),max_actions=phase_max_actions,max_polls=phase_max_polls,
                    poll_seconds=poll_seconds,export_recovery_attempts=export_recovery_attempts,
                    busy_read_retries=busy_read_retries,limits=limits,transport=network,cancel=cancel,
                    link_only=True,allow_physical_stop=True,phase_budget=phases,shared_guard=guard,**native_options,**allowance)
                guard.check()
                if network.mutation_stopped:
                    return result('plan_refused','mutation_review_required',stop_details=linked)
                if linked['status']=='handoff_terminal':
                    return result('plan_complete','early_physical_stop',terminal_stop_verified=True,stop_details=linked)
                if linked['status']!='handoff_linked':
                    return result('plan_refused' if linked['status']=='handoff_refused' else 'plan_paused',
                        'handoff',stop_details=linked)
                completed+=1;current=edge['next_state_dir']
            guard.check(); state=Path(current)
            final_config=single.manifest_read(state/'config.json');runner._config(final_config,state)
            require(not os.path.lexists(Path(final_config['job_dir'])/boundary.NAME),'plan_final_unplanned_boundary')
            def final_snapshot():
                fd=runner.engine.held_lock(state/'runner.lock')
                try:
                    guard.check()
                    require(runner._same(single.manifest_read(state/'config.json'),final_config),'plan_final_config_changed')
                    require(not boundary.extra(Path(final_config['job_dir'])),'plan_final_unplanned_boundary')
                    intents,count=runner._ledger(state,final_config);runner._job_prefix(final_config,intents,count)
                    candidates=runner._inspect(final_config,intents,remaining)
                    guard.check()
                    return intents,count,bool(candidates.get(final_config['span_origin']['first_sequence']+count))
                finally:os.close(fd)
            for _ in range(phase_max_polls):
                intents,count,more=final_snapshot()
                terminal=(count==len(intents) and count>0 and intents[-1]['delivery']['close_reason']=='STOP')
                allowance=budgets()
                if not available(allowance):return result('plan_paused','terminal_watch',stop_reason='network_budget')
                polls=phase_max_polls if terminal else 1
                if not phases.reserve(phase_max_actions,polls):
                    return result('plan_paused','terminal_watch',stop_reason='phase_budget')
                following=runner.watch(state_dir=state,exclusive_writer=True,acknowledge_span_local_closure=True,
                    finalize=True,export=True,deadline_seconds=remaining(),max_actions=phase_max_actions,max_polls=polls,
                    poll_seconds=poll_seconds,export_recovery_attempts=export_recovery_attempts,busy_read_retries=busy_read_retries,
                    limits=limits,transport=network,cancel=cancel,**native_options,**allowance)
                guard.check()
                if network.mutation_stopped:
                    return result('plan_refused','mutation_review_required',stop_details=following)
                if following['status']=='exported':
                    intents,count,more=final_snapshot()
                    require(count==len(intents) and count>0 and intents[-1]['delivery']['close_reason']=='STOP',
                            'plan_final_physical_stop_required')
                    return result('plan_complete','terminal_export',terminal_stop_verified=True,stop_details=following)
                if terminal or following['status']!='poll_limit' or following.get('last_status') not in (None,'waiting_for_input'):
                    return result('plan_paused','terminal_watch',stop_details=following)
                intents,count,more=final_snapshot()
                require(count==len(intents),'plan_final_pending_input')
                if count>0 and intents[-1]['delivery']['close_reason']=='ROTATION' and not more:
                    return result('plan_exhausted','final_rotation',terminal_stop_verified=False,stop_details=following)
                if not intents and not more:
                    return result('plan_paused','terminal_watch',stop_reason='waiting_for_input',stop_details=following)
                # Only confirmed intake progress continues; terminal work gets one bounded full watch.
            return result('plan_paused','terminal_watch',stop_reason='discovery_poll_limit')
        except (Exception,KeyboardInterrupt) as exc:
            return result('plan_refused','review_required',stop_details=single.failure_report(exc))
    finally:os.close(lock)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('plan-file','control-dir'):p.add_argument('--'+name,required=True)
    for name in ('acknowledge-finite-plan','exclusive-writer','acknowledge-span-local-closure'):
        p.add_argument('--'+name,action='store_true',required=True)
    for name,default in (('max-requests',4096),('max-network-writes',1024),('max-network-bytes',runner.MAX_NETWORK_BYTES),
        ('max-total-actions',32768),('max-total-polls',4096),('phase-max-actions',64),('phase-max-polls',64)):
        p.add_argument('--'+name,type=int,default=default)
    p.add_argument('--deadline-seconds',type=float,default=120);p.add_argument('--poll-seconds',type=float,default=1)
    p.add_argument('--export-recovery-attempts',type=int,choices=(0,2,3),default=0)
    p.add_argument('--native-turns',action='store_true')
    p.add_argument('--candidate-postphase',action='store_true')
    p.add_argument('--candidate-config')
    p.add_argument('--busy-read-retries',type=int,choices=range(4),default=0)
    try:value=run(**vars(p.parse_args(argv)))
    except (Exception,KeyboardInterrupt):value=dict(schema=SCHEMA,status='plan_refused',preserve_existing_files=True,automatic_retry=False)
    from . import recorder_candidate_postphase as candidates
    if candidates.pending_cleanup():
        value['candidate_cleanup'] = dict(status='cleanup_incomplete',requires_operator_attention=True,
            original_owner_retained=True,new_work_allowed=False,
            detail='Only owned-child cleanup will continue. Restore process visibility or resolve the owned child; no inference retry or primary control occurs.')
    try:print(json.dumps(value,sort_keys=True),flush=True)
    finally:
        # A broken redirected stdout cannot discard a pending owned handle.
        if candidates.retain_cleanup():
            candidates.cleanup_notice(dict(schema=SCHEMA,status='candidate_cleanup_complete',primary_status=value['status'],
                new_candidate_work_started=False,automatic_retry=False))
    if value['status']=='plan_complete':
        return 0 if value.get('enhancement',{}).get('status','candidate_drain_complete')=='candidate_drain_complete' else 3
    return 3 if value['status'] in ('plan_paused','plan_exhausted') else 2


if __name__=='__main__':raise SystemExit(main())
