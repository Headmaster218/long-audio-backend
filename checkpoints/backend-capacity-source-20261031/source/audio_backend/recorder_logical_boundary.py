"""Explicit logical analysis closure, never a producer recording STOP."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path

from . import recorder_intake as single
from . import recorder_capture_intake as engine
from . import recording_uploader as u

NAME = 'logical-boundary.json'
ANCHOR = 'logical-boundary.intent.json'
RUNNER_ANCHOR = 'logical-cut.intent.json'
SCHEMA = 'recorder-logical-analysis-boundary-v1'
SCHEMA_V2 = 'recorder-logical-analysis-boundary-v2'
SCHEMA_V3 = 'recorder-logical-analysis-boundary-v3'
require = single.require


def digest(value):
    return hashlib.sha256(single._json(value)).hexdigest()


def extra(root):
    return {NAME,ANCHOR} if any(os.path.lexists(Path(root)/n) for n in (NAME,ANCHOR)) else set()


def routing_selection(route_config, old_config):
    """Read declared immutable routing only; never claim a live endpoint switch."""
    from . import recorder_ftps_receiver as receiver
    from . import recorder_receiver_publication as publication
    from . import recorder_receiver_routing as routing
    require('receiver_publication' in old_config,'logical_cross_inbox_old_publication_required')
    path=receiver._path(str(route_config));raw=receiver._private_file(path,16384)
    data=json.loads(raw,object_pairs_hook=receiver._unique_pairs);service=receiver._validate_config(data)
    require(service.generations is not None,'logical_cross_inbox_routing_required')
    entries,active=routing.validate(service.generations,service.binding,service.inbox_dir)
    old=dict(binding=old_config['receiver_publication'],inbox_dir=old_config['inbox_dir'])
    new=dict(binding=service.binding,inbox_dir=str(service.inbox_dir))
    require(sum(single._json(item)==single._json(old) for item in service.generations)==1 and
            single._json(old)!=single._json(new),'logical_cross_inbox_route_selection')
    configs=[publication.validate_binding(b,i) for b,i,_ in entries]
    require(receiver._private_file(path,16384)==raw,'logical_cross_inbox_route_changed')
    provenance=dict(config_path=str(path),config_sha256=hashlib.sha256(raw).hexdigest(),
                    old_generation=old,new_generation=new)
    return new,provenance,configs


def cross_inbox(value):
    return value['schema']==SCHEMA_V2 or (value['schema']==SCHEMA_V3 and 'successor_publication' in value)


def source_config(value, config):
    """Return the exact successor namespace; v1 never acquires cross-inbox scope."""
    if not cross_inbox(value):return config
    require(value['schema'] in (SCHEMA_V2,SCHEMA_V3),'logical_boundary_schema')
    selected,provenance,_=routing_selection(value['routing_provenance']['config_path'],config)
    require(single._json(selected)==single._json(value['successor_publication']) and
            single._json(provenance)==single._json(value['routing_provenance']),
            'logical_cross_inbox_route_changed')
    return dict(config,inbox_dir=selected['inbox_dir'],receiver_publication=selected['binding'])


def _published_successor(pair, attempt, remaining, generations):
    from . import recorder_receiver_publication as publication
    require(type(attempt) is str and publication.inbox.NAME.fullmatch(attempt) is not None,
            'logical_cross_inbox_attempt')
    ready=Path(pair['inbox_dir']);cfg=publication.validate_binding(pair['binding'],ready)
    state=Path(cfg['state_dir']);raw=Path(cfg['staging_dir'])/attempt;published=ready/attempt
    def unambiguous():
        for other in generations:
            if other['state_dir']==cfg['state_dir']:continue
            require(not any(os.path.lexists(Path(other[k])/attempt) for k in ('staging_dir','inbox_dir')),
                    'logical_cross_inbox_attempt_collision')
    with publication._lock(state,cfg,'publication.lock',remaining,shared=True):
        unambiguous()
        with ExitStack() as stack:
            observations=[]
            for path in (raw,published):
                fd,before,stamps,files,report=publication._observe(path,remaining)
                stack.callback(os.close,fd);observations.append((path,fd,before,stamps,files,report))
            require(single._json(observations[0][5])==single._json(observations[1][5]) and
                    observations[0][4]==observations[1][4],'logical_cross_inbox_publication_copy_changed')
            for path,fd,before,stamps,_,_ in observations:publication._still(path,fd,before,stamps)
            unambiguous()
            ownership=dict(raw_attempt_identity=publication._identity(raw),
                           published_attempt_identity=publication._identity(published))
            return published,observations[1][5],ownership


def verify_anchor(root, state=None):
    root=Path(root)
    anchor=single.manifest_read(root/ANCHOR)
    require(type(anchor) is dict and set(anchor)=={'schema','job_dir','runner_state_dir','receipt_sha256'}
        and anchor['schema'] in (SCHEMA,SCHEMA_V2,SCHEMA_V3) and anchor['job_dir']==str(root),'logical_boundary_anchor_fields')
    saved_state=Path(anchor['runner_state_dir'])
    require(saved_state.is_absolute() and saved_state==saved_state.resolve()
        and (state is None or saved_state==Path(state)),'logical_boundary_anchor_state')
    require(single._json(single.manifest_read(saved_state/RUNNER_ANCHOR))==single._json(anchor),
            'logical_boundary_anchor_changed')
    value=single.manifest_read(root/NAME)
    require(value.get('schema')==anchor['schema'] and digest(value)==anchor['receipt_sha256'],'logical_boundary_receipt_hash_changed')
    return anchor


def durable(root, expected=None):
    from . import recorder_inbox_runner as runner
    anchor=verify_anchor(root)
    value=single.manifest_read(Path(root)/NAME)
    if cross_inbox(value):
        config=single.manifest_read(Path(value['runner_state_dir'])/'config.json')
        require(digest(config)==value['runner_config_sha256'],'logical_boundary_runner_changed')
        source_config(value,config)
    require(expected is None or anchor['receipt_sha256']==digest(expected), 'logical_boundary_effect_authority_changed')
    for path in (Path(root)/ANCHOR,Path(anchor['runner_state_dir'])/RUNNER_ANCHOR,Path(root)/NAME):
        runner._durable(path)
    require(verify_anchor(root)==anchor, 'logical_boundary_effect_authority_changed')


class EffectTransport:
    def __init__(self, transport, root, expected):
        self.transport,self.root,self.expected=transport,root,expected

    def request(self, method, path, **kwargs):
        if method != 'GET':durable(self.root,self.expected)
        return self.transport.request(method,path,**kwargs)


def verify(root, header, receipts, records, remaining):
    """Authenticate complete original prefix and successor, without side effects."""
    root = Path(root)
    if not extra(root):return None
    verify_anchor(root)
    value = single.manifest_read(root/NAME)
    _verify_value(root,header,receipts,records,remaining,value)
    require(single._json(single.manifest_read(root/NAME)) == single._json(value), 'logical_boundary_changed')
    return value


def _verify_value(root,header,receipts,records,remaining,value,*,preparing=False):
    from . import recorder_inbox_runner as runner
    from . import recorder_incremental_span_intake as span
    require(type(value) is dict and value.get('schema') in (SCHEMA,SCHEMA_V2,SCHEMA_V3), 'logical_boundary_schema')
    added={'successor_publication','routing_provenance','successor_ownership'} if cross_inbox(value) else set()
    require(set(value) == {'schema','consent','recording_complete',
        'recognition_continuity_verified','job_dir','header_sha256','admissions_sha256',
        'runner_state_dir','runner_config_sha256','runner_intents_sha256',
        'inputs','frames','last_delivery','successor','next_session_intent'} | added, 'logical_boundary_fields')
    require(value['consent'] == 'explicit_logical_analysis_boundary'
        and value['recording_complete'] is False and value['recognition_continuity_verified'] is False,
        'logical_boundary_consent')
    require(receipts and len(receipts) == len(records) and value['job_dir'] == str(root)
        and value['header_sha256'] == digest(header) and value['admissions_sha256'] == digest(receipts)
        and type(value['inputs']) is int and value['inputs'] == len(receipts)
        and type(value['frames']) is int and value['frames'] == sum(r['delivery']['frames'] for r in receipts)
        and single._json(value['last_delivery']) == single._json(receipts[-1]['delivery'])
        and receipts[-1]['delivery']['close_reason'] == 'ROTATION', 'logical_boundary_prefix_changed')
    state = runner._path(value['runner_state_dir']);single.private_directory(state)
    config = single.manifest_read(state/'config.json');runner._config(config,state)
    require(config['job_dir'] == str(root) and digest(config) == value['runner_config_sha256'],
        'logical_boundary_runner_changed')
    intents, completed = runner._ledger(state,config,logical_preparing=preparing)
    require(completed == len(intents) == len(receipts) and digest(intents) == value['runner_intents_sha256'],
        'logical_boundary_selection_changed')
    successor = value['successor']
    require(type(successor) is dict and set(successor) == {'path','delivery'}, 'logical_boundary_successor_fields')
    path = runner._path(successor['path'])
    selected_config=source_config(value,config)
    require(path.parent == Path(selected_config['inbox_dir']), 'logical_boundary_successor_inbox')
    if cross_inbox(value):
        pair,_,generations=routing_selection(value['routing_provenance']['config_path'],config)
        observed_path,report,ownership=_published_successor(pair,path.name,remaining,generations)
        require(observed_path==path and single._json(report)==single._json(successor['delivery']) and
                single._json(ownership)==single._json(value['successor_ownership']),
                'logical_cross_inbox_successor_changed')
    current = runner._binding(selected_config,runner._intent(selected_config,path,successor['delivery'],completed),completed,remaining)
    span.POLICY.validate_edge(records[-1],current)
    nxt = value['next_session_intent']
    identity_fields={'reviewed_identity_handoff','session_creation_request'} if value['schema']==SCHEMA_V3 else set()
    require(type(nxt) is dict and set(nxt) == {'state_dir','job_dir','output_dir','capture_id','session_name'}|identity_fields,
        'logical_boundary_next_intent_fields')
    require(type(nxt['session_name']) is str and len(nxt['session_name']) <= 200,
        'logical_boundary_next_name')
    if identity_fields:
        from . import recorder_runner_initialize as initialize
        handoff=initialize.validate_handoff(nxt['reviewed_identity_handoff'])
        require(handoff['source_session_id']==config['session_id'] and
            single._json(nxt['session_creation_request'])==single._json(initialize.creation_request(
                config['api'],nxt['session_name'],handoff)), 'logical_boundary_identity_binding')
    template = runner.config_from_delivery(first_delivery=path,inbox_dir=selected_config['inbox_dir'],
        job_dir=nxt['job_dir'],output_dir=nxt['output_dir'],api=config['api'],session_id='pending-logical-next',
        capture_id=nxt['capture_id'],max_hours=config['max_hours'],max_inputs=config['max_inputs'],
        receiver_state_dir=selected_config.get('receiver_publication',{}).get('state_dir'))
    next_state = runner._path(nxt['state_dir']);runner._config(template,next_state)
    protected = [state,root,Path(config['inbox_dir'])]
    if cross_inbox(value):
        _,_,generations=routing_selection(value['routing_provenance']['config_path'],config)
        protected.extend(Path(g[k]) for g in generations for k in ('state_dir','staging_dir','inbox_dir'))
    if config['output_dir'] is not None:protected.append(Path(config['output_dir']))
    for p in (next_state,Path(nxt['job_dir']),Path(nxt['output_dir'])):
        require(not any(p.is_relative_to(q) or q.is_relative_to(p) for q in protected),
                'logical_boundary_next_trees_overlap')
    remaining()
    return value


def protected(value):
    """No export may write into originals, control trees or reserved next trees."""
    successor=Path(value['successor']['path'])
    paths=(Path(value['runner_state_dir']),successor.parent,successor,
            *(Path(value['next_session_intent'][k]) for k in ('state_dir','job_dir','output_dir')))
    if cross_inbox(value):
        config=single.manifest_read(Path(value['runner_state_dir'])/'config.json')
        require(digest(config)==value['runner_config_sha256'],'logical_boundary_runner_changed')
        source_config(value,config)
        _,_,generations=routing_selection(value['routing_provenance']['config_path'],config)
        paths+=tuple(Path(g[k]) for g in generations for k in ('state_dir','staging_dir','inbox_dir'))
        paths+=(Path(value['routing_provenance']['config_path']),)
    return paths



def summary(value):
    return dict(closure_kind='logical_analysis_boundary', recording_complete=False,
        recognition_continuity_verified=False, logical_boundary_sha256=digest(value),
        logical_boundary=value, eos_meaning='selected_logical_analysis_span_only')


def prepare(*, state_dir, successor_attempt, next_state_dir, next_job_dir, next_output_dir,
            next_capture_id, next_session_name='', acknowledge_logical_analysis_boundary=False,
            next_inbox_dir=None,next_receiver_state_dir=None,route_config=None,deadline_seconds=120,cancel=None,
            preserve_reviewed_identity_group=None,identity_generation=None,identity_policy_sha256=None):
    from . import recorder_inbox_runner as runner
    from . import recorder_incremental_span_intake as span
    require(acknowledge_logical_analysis_boundary is True,'logical_boundary_ack_required')
    state=runner._path(str(state_dir));single.private_directory(state)
    require(type(deadline_seconds) in (int,float) and 0<deadline_seconds<=120,'logical_boundary_deadline_bound')
    supplied=(next_inbox_dir is not None,next_receiver_state_dir is not None,route_config is not None)
    require(all(supplied) or not any(supplied),'logical_cross_inbox_explicit_arguments_required')
    guard=u.Guard(u.Limits(deadline_seconds=deadline_seconds),cancel)
    guard.check()
    def remaining():guard.check();return max(.000001,guard.end-__import__('time').monotonic())
    with ExitStack() as stack:
        fd=engine.held_lock(state/'runner.lock');stack.callback(os.close,fd)
        config=single.manifest_read(state/'config.json');runner._config(config,state)
        job=Path(config['job_dir'])
        if os.path.lexists(job/NAME):verify_anchor(job,state)
        require(not os.path.lexists(state/RUNNER_ANCHOR) or os.path.lexists(job/ANCHOR),
                'logical_boundary_anchor_order')
        intents,completed=runner._ledger(state,config,logical_preparing=True);runner._job_prefix(config,intents,completed)
        require(completed == len(intents) and completed > 0,'logical_boundary_pending_input')
        require(intents[-1]['delivery']['close_reason'] == 'ROTATION',
                'logical_boundary_rotation_required')
        require(not any(os.path.lexists(state/n) for n in ('finalize.intent.json','export.intent.json')),
                'logical_boundary_terminal_already_requested')
        root=Path(config['job_dir'])
        checkpoint=runner.status._control(root/'uploader/uploader.json',guard,journal=True)[1]
        runner.capacity.inspect(config,intents,completed,None,guard)
        for p in (root/'intake.lock',root/'uploader/uploader.lock'):
            fd=engine.held_lock(p);stack.callback(os.close,fd)
        require(runner.status._control(root/'uploader/uploader.json',guard,journal=True)[1] == checkpoint,
                'logical_boundary_checkpoint_changed')
        header=single.manifest_read(root/'capture.json')
        receipts,records=engine.registry(root,header,remaining,policy=span.POLICY)
        added={}
        if all(supplied):
            from . import recorder_receiver_publication as publication
            pair,provenance,generations=routing_selection(route_config,config)
            requested=dict(inbox_dir=str(runner._path(str(next_inbox_dir))),
                binding=publication.bind(runner._path(str(next_receiver_state_dir))))
            require(single._json(pair)==single._json(requested),'logical_cross_inbox_route_selection')
            path,report,ownership=_published_successor(pair,successor_attempt,remaining,generations)
            added=dict(successor_publication=pair,routing_provenance=provenance,successor_ownership=ownership)
        else:
            available=runner._inspect(config,intents,remaining).get(config['span_origin']['first_sequence']+completed,[])
            selected=[(p,r) for p,r in available if p.name == successor_attempt]
            require(len(selected)==1,'logical_boundary_exact_successor_required')
            path,report=selected[0]
        nxt=dict(state_dir=str(next_state_dir),job_dir=str(next_job_dir),output_dir=str(next_output_dir),
                 capture_id=next_capture_id,session_name=next_session_name)
        from . import recorder_runner_initialize as initialize
        supplied_identity=any(item is not None for item in (preserve_reviewed_identity_group,identity_generation,identity_policy_sha256))
        handoff=initialize.reviewed_handoff(config['session_id'] if supplied_identity else None,
            preserve_reviewed_identity_group,identity_generation,identity_policy_sha256)
        if handoff is not None:
            nxt.update(reviewed_identity_handoff=handoff,session_creation_request=initialize.creation_request(
                config['api'],next_session_name,handoff))
        value=dict(schema=SCHEMA_V3 if handoff else SCHEMA_V2 if added else SCHEMA,**added,consent='explicit_logical_analysis_boundary',recording_complete=False,
            recognition_continuity_verified=False,job_dir=str(root),header_sha256=digest(header),
            admissions_sha256=digest(receipts),runner_state_dir=str(state),runner_config_sha256=digest(config),
            runner_intents_sha256=digest(intents),inputs=completed,
            frames=sum(r['delivery']['frames'] for r in receipts),last_delivery=receipts[-1]['delivery'],
            successor=dict(path=str(path),delivery=report),next_session_intent=nxt)
        # Validate the entire proposed receipt before its first durable write.
        validate_proposal(root,header,receipts,records,remaining,value)
        if not any(os.path.lexists(p) for p in (root/ANCHOR,state/RUNNER_ANCHOR,root/NAME)):
            require(not any(os.path.lexists(nxt[k]) for k in ('state_dir','job_dir','output_dir')),
                    'logical_boundary_fresh_next_required')
        anchor=dict(schema=value['schema'],job_dir=str(root),runner_state_dir=str(state),receipt_sha256=digest(value))
        for target in (root/ANCHOR,state/RUNNER_ANCHOR):
            remaining()
            if os.path.lexists(target):runner._durable(target,anchor)
            else:single.manifest_write(target,anchor)
        remaining()
        if os.path.lexists(root/NAME):
            require(single._json(single.manifest_read(root/NAME)) == single._json(value),
                    'logical_boundary_existing_receipt_changed')
        else:
            require(not any(os.path.lexists(nxt[k]) for k in ('state_dir','job_dir','output_dir')),
                    'logical_boundary_fresh_next_required')
            single.manifest_write(root/NAME,value)
        durable(root)
        verify(root,header,receipts,records,remaining)
    return dict(status='logical_boundary_prepared',network_requests=0,session_created=False,
                recording_complete=False,recognition_continuity_verified=False,receipt_sha256=digest(value))


def validate_proposal(root,header,receipts,records,remaining,value):
    # Shared validator accepts an in-memory proposed receipt without creating a
    # temporary authority file. Existing complete receipts still use read-back.
    return _verify_value(root,header,receipts,records,remaining,value,preparing=True)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('state-dir','successor-attempt','next-state-dir','next-job-dir','next-output-dir','next-capture-id'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--next-session-name',default='')
    for name in ('preserve-reviewed-identity-group','identity-generation','identity-policy-sha256'):p.add_argument('--'+name)
    for name in ('next-inbox-dir','next-receiver-state-dir','route-config'):p.add_argument('--'+name)
    p.add_argument('--deadline-seconds',type=float,default=120)
    p.add_argument('--acknowledge-logical-analysis-boundary',action='store_true',required=True)
    try:result=prepare(**vars(p.parse_args(argv)))
    except (Exception,KeyboardInterrupt):
        print(json.dumps(dict(status='refused',reason='logical_boundary_refused',preserve_existing_files=True)))
        return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__ == '__main__':raise SystemExit(main())
