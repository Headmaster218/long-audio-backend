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
require = single.require


def digest(value):
    return hashlib.sha256(single._json(value)).hexdigest()


def extra(root):
    return {NAME,ANCHOR} if any(os.path.lexists(Path(root)/n) for n in (NAME,ANCHOR)) else set()


def verify_anchor(root, state=None):
    root=Path(root)
    anchor=single.manifest_read(root/ANCHOR)
    require(type(anchor) is dict and set(anchor)=={'schema','job_dir','runner_state_dir','receipt_sha256'}
        and anchor['schema']==SCHEMA and anchor['job_dir']==str(root),'logical_boundary_anchor_fields')
    saved_state=Path(anchor['runner_state_dir'])
    require(saved_state.is_absolute() and saved_state==saved_state.resolve()
        and (state is None or saved_state==Path(state)),'logical_boundary_anchor_state')
    require(single._json(single.manifest_read(saved_state/RUNNER_ANCHOR))==single._json(anchor),
            'logical_boundary_anchor_changed')
    value=single.manifest_read(root/NAME)
    require(digest(value)==anchor['receipt_sha256'],'logical_boundary_receipt_hash_changed')
    return anchor


def durable(root, expected=None):
    from . import recorder_inbox_runner as runner
    anchor=verify_anchor(root)
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
    require(type(value) is dict and set(value) == {'schema','consent','recording_complete',
        'recognition_continuity_verified','job_dir','header_sha256','admissions_sha256',
        'runner_state_dir','runner_config_sha256','runner_intents_sha256',
        'inputs','frames','last_delivery','successor','next_session_intent'}, 'logical_boundary_fields')
    require(value['schema'] == SCHEMA and value['consent'] == 'explicit_logical_analysis_boundary'
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
    require(path.parent == Path(config['inbox_dir']), 'logical_boundary_successor_inbox')
    current = runner._binding(config,runner._intent(config,path,successor['delivery'],completed),completed,remaining)
    span.POLICY.validate_edge(records[-1],current)
    nxt = value['next_session_intent']
    require(type(nxt) is dict and set(nxt) == {'state_dir','job_dir','output_dir','capture_id','session_name'},
        'logical_boundary_next_intent_fields')
    require(type(nxt['session_name']) is str and len(nxt['session_name']) <= 200,
        'logical_boundary_next_name')
    template = runner.config_from_delivery(first_delivery=path,inbox_dir=config['inbox_dir'],
        job_dir=nxt['job_dir'],output_dir=nxt['output_dir'],api=config['api'],session_id='pending-logical-next',
        capture_id=nxt['capture_id'],max_hours=config['max_hours'],max_inputs=config['max_inputs'],
        receiver_state_dir=config.get('receiver_publication',{}).get('state_dir'))
    next_state = runner._path(nxt['state_dir']);runner._config(template,next_state)
    protected = [state,root,Path(config['inbox_dir'])]
    if config['output_dir'] is not None:protected.append(Path(config['output_dir']))
    for p in (next_state,Path(nxt['job_dir']),Path(nxt['output_dir'])):
        require(not any(p.is_relative_to(q) or q.is_relative_to(p) for q in protected),
                'logical_boundary_next_trees_overlap')
    remaining()
    return value


def protected(value):
    """No export may write into originals, control trees or reserved next trees."""
    successor=Path(value['successor']['path'])
    return (Path(value['runner_state_dir']),successor.parent,successor,
            *(Path(value['next_session_intent'][k]) for k in ('state_dir','job_dir','output_dir')))


def summary(value):
    return dict(closure_kind='logical_analysis_boundary', recording_complete=False,
        recognition_continuity_verified=False, logical_boundary_sha256=digest(value),
        logical_boundary=value, eos_meaning='selected_logical_analysis_span_only')


def prepare(*, state_dir, successor_attempt, next_state_dir, next_job_dir, next_output_dir,
            next_capture_id, next_session_name='', acknowledge_logical_analysis_boundary=False):
    from . import recorder_inbox_runner as runner
    from . import recorder_incremental_span_intake as span
    require(acknowledge_logical_analysis_boundary is True,'logical_boundary_ack_required')
    state=runner._path(str(state_dir));single.private_directory(state)
    guard=u.Guard(u.Limits(deadline_seconds=120))
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
        available=runner._inspect(config,intents,remaining).get(config['span_origin']['first_sequence']+completed,[])
        selected=[(p,r) for p,r in available if p.name == successor_attempt]
        require(len(selected)==1,'logical_boundary_exact_successor_required')
        path,report=selected[0]
        nxt=dict(state_dir=str(next_state_dir),job_dir=str(next_job_dir),output_dir=str(next_output_dir),
                 capture_id=next_capture_id,session_name=next_session_name)
        value=dict(schema=SCHEMA,consent='explicit_logical_analysis_boundary',recording_complete=False,
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
        anchor=dict(schema=SCHEMA,job_dir=str(root),runner_state_dir=str(state),receipt_sha256=digest(value))
        for target in (root/ANCHOR,state/RUNNER_ANCHOR):
            if os.path.lexists(target):runner._durable(target,anchor)
            else:single.manifest_write(target,anchor)
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
    p.add_argument('--acknowledge-logical-analysis-boundary',action='store_true',required=True)
    try:result=prepare(**vars(p.parse_args(argv)))
    except (Exception,KeyboardInterrupt):
        print(json.dumps(dict(status='refused',reason='logical_boundary_refused',preserve_existing_files=True)))
        return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__ == '__main__':raise SystemExit(main())
