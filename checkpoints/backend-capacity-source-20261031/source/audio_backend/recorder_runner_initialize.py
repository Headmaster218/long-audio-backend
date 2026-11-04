"""Explicit one-session bootstrap; no upload and no automatic retry of creation."""
import os
from pathlib import Path

from . import recorder_inbox_runner as runner
from . import recorder_intake as single
from . import recording_uploader as u
from .recorder_delivery import inspect_delivery

SCHEMA = 'recorder-runner-initialize-v1'
DIRECTORY = 'initialization'
PENDING = 'pending-session-creation'
require = single.require


def _saved(path, expected):
    if os.path.lexists(path):return runner._durable(path,expected)
    single.manifest_write(path,expected)
    return expected


def _intent(template, first, report, name):
    return dict(schema=SCHEMA, template=template, first_delivery=str(first), delivery=report,
        request=dict(method='POST',origin=template['api'],path='/sessions',
                     body=dict(name=name,processing_mode='legacy')))


def validate_completed(state, config):
    """Every later run verifies optional bootstrap receipts before network effects."""
    root=state/DIRECTORY;single.private_directory(root)
    require(runner._names(root,4)=={'intent.json','post.intent.json','created.json','complete.json'},
            'runner_initialization_incomplete')
    intent=single.manifest_read(root/'intent.json')
    require(type(intent) is dict and set(intent)=={'schema','template','first_delivery','delivery','request'}
            and intent['schema']==SCHEMA,'runner_initialization_intent')
    template=dict(config,session_id=PENDING)
    request=intent['request']
    require(type(request) is dict and set(request)=={'method','origin','path','body'}
            and type(request['body']) is dict and set(request['body'])=={'name','processing_mode'}
            and type(request['body']['name']) is str and len(request['body']['name'])<=200
            and runner._same(intent,_intent(template,intent['first_delivery'],intent['delivery'],request['body']['name'])),
            'runner_initialization_binding_changed')
    binding=dict(schema=SCHEMA,intent_sha256=runner._hash(intent))
    require(runner._same(single.manifest_read(root/'post.intent.json'),binding),
            'runner_initialization_post_binding')
    require(runner._same(single.manifest_read(root/'created.json'),dict(binding,session_id=config['session_id'])),
            'runner_initialization_session_binding')
    first=runner._intent(config,intent['first_delivery'],intent['delivery'],0)
    require(runner._same(single.manifest_read(runner._file(state,0,'intent')),first),
            'runner_initialization_first_binding')
    require(runner._same(single.manifest_read(root/'complete.json'),dict(binding,
        config_sha256=runner._hash(config),first_intent_sha256=runner._hash(first))),
        'runner_initialization_completion_binding')


def initialize(*, state_dir, inbox_dir, job_dir, first_delivery, api, capture_id,
        output_dir=None,receiver_state_dir=None,max_hours=12,max_inputs=192,
        session_name='',exclusive_writer=False,acknowledge_span_local_closure=False,
        resume=False,deadline_seconds=30,transport=None):
    require(exclusive_writer is True and acknowledge_span_local_closure is True,
            'runner_initialize_acknowledgments_required')
    require(type(session_name) is str and len(session_name)<=200,'runner_session_name_bound')
    require(type(deadline_seconds) in (int,float) and 1<=deadline_seconds<=120,
            'runner_initialize_deadline_bound')
    guard=u.Guard(u.Limits(deadline_seconds=deadline_seconds))
    state=runner._path(str(state_dir));first=runner._path(str(first_delivery))
    template=runner.config_from_delivery(first_delivery=first,inbox_dir=inbox_dir,job_dir=job_dir,
        output_dir=output_dir,api=api,session_id=PENDING,capture_id=capture_id,max_hours=max_hours,
        max_inputs=max_inputs,receiver_state_dir=receiver_state_dir)
    runner._config(template,state)
    require(not os.path.lexists(job_dir),'runner_fresh_job_required')
    if output_dir is not None:runner.exporter._destination(output_dir)
    report=inspect_delivery(first,validate_metadata=True)
    require(runner.capacity.inspect(template,[],0,report,guard)['allowed'],
            'first_delivery_capacity')
    intent=_intent(template,first,report,session_name)
    guard.check()
    if not resume:
        require(not os.path.lexists(state),'runner_initialize_fresh_state_required')
        state.mkdir(mode=0o700);u._fsync_dir(state.parent)
        lock=runner.engine.held_lock(state/'runner.lock',create=True)
    else:
        single.private_directory(state)
        lock=runner.engine.held_lock(state/'runner.lock')
    requests=writes=0
    try:
        root=state/DIRECTORY
        if not resume:
            root.mkdir(mode=0o700);u._fsync_dir(state)
            single.manifest_write(root/'intent.json',intent)
        single.private_directory(root)
        require(runner._names(state,4)<={'runner.lock',DIRECTORY,'config.json','input-000.intent.json'},
                'runner_initialize_unknown_state')
        require(runner._names(root,4)<={'intent.json','post.intent.json','created.json','complete.json'},
                'runner_initialize_unknown_receipt')
        runner._durable(root/'intent.json',intent)
        binding=dict(schema=SCHEMA,intent_sha256=runner._hash(intent))
        if os.path.lexists(root/'complete.json'):
            config=single.manifest_read(state/'config.json');validate_completed(state,config)
        posted=os.path.lexists(root/'post.intent.json');created=os.path.lexists(root/'created.json')
        if posted:runner._durable(root/'post.intent.json',binding)
        require(not created or posted,'runner_session_receipt_without_post_intent')
        configured=os.path.lexists(state/'config.json')
        selected=os.path.lexists(runner._file(state,0,'intent'))
        require(not (configured or selected) or (posted and created),
                'runner_binding_without_confirmed_creation')
        require(not selected or configured,'runner_first_intent_without_config')
        require(not posted or created,'runner_session_creation_uncertain_no_retry')
        if created:
            saved=runner._durable(root/'created.json')
            require(type(saved) is dict and set(saved)=={'schema','intent_sha256','session_id'}
                    and runner._same(saved,dict(binding,session_id=saved['session_id'])),
                    'runner_session_creation_receipt_changed')
            confirmed=dict(template,session_id=saved['session_id'])
            runner._config(confirmed,state)
            if configured:runner._durable(state/'config.json',confirmed)
            if selected:runner._durable(runner._file(state,0,'intent'),runner._intent(confirmed,first,report,0))
        http=transport or u.HTTPTransport(template['api'],guard)
        def request(method,path,**kwargs):
            nonlocal requests,writes
            guard.check();require(requests<6 and (method=='GET' or writes<1),'runner_initialize_request_bound')
            runner._durable(root/'intent.json',intent)
            requests+=1;writes+=method!='GET'
            value=http.request(method,path,**kwargs)
            guard.check();require(type(value) is dict and len(single._json(value))<=u.JSON_BYTES,
                                  'runner_initialize_response_bound')
            return value
        # The existing API exposes health before creation, but strict contract is session-scoped.
        health=request('GET','/health')
        require(health.get('status')=='ok','runner_initialize_server_not_ready')
        if not posted:
            # Revalidate exact selected bytes and local paths immediately before the one mutation.
            require(runner._same(inspect_delivery(first,validate_metadata=True),report),
                    'runner_initialize_first_delivery_changed')
            runner._config(template,state)
            single.manifest_write(root/'post.intent.json',binding)
            response=request('POST','/sessions',body=intent['request']['body'])
            sid=response.get('id')
            single.target(template['api'],'operator-selected-unverified',sid,capture_id,0)
            single.manifest_write(root/'created.json',dict(binding,session_id=sid))
        else:
            saved=runner._durable(root/'created.json')
            require(type(saved) is dict and set(saved)=={'schema','intent_sha256','session_id'}
                    and runner._same(saved,dict(binding,session_id=saved['session_id'])),
                    'runner_session_creation_receipt_changed')
            sid=saved['session_id']
        config=dict(template,session_id=sid);runner._config(config,state)
        session=request('GET','/sessions/'+sid)
        require(session.get('id')==sid and session.get('name')==session_name
                and session.get('processing_mode')=='legacy','runner_created_session_identity_changed')
        contract=request('GET','/sessions/'+sid+'/recording-contract')
        single.check_contract(contract,sid,report['frames'])
        require(contract.get('capture') is None and contract.get('has_chunks') is False,
                'runner_created_session_not_empty')
        chunks=request('GET','/sessions/'+sid+'/chunks?limit=1')
        require(chunks.get('chunks')==[],'runner_created_session_not_empty')
        try:request('GET','/sessions/'+sid+'/recordings/'+capture_id)
        except u.HTTPFailure as error:
            if error.status!=404:raise
        else:raise single.Refused('runner_created_capture_already_exists')
        require(runner._same(inspect_delivery(first,validate_metadata=True),report),
                'runner_initialize_first_delivery_changed')
        runner._config(config,state)
        require(not os.path.lexists(job_dir),'runner_fresh_job_required')
        if output_dir is not None:runner.exporter._destination(output_dir)
        initial=runner._intent(config,first,report,0)
        _saved(state/'config.json',config);_saved(runner._file(state,0,'intent'),initial)
        _saved(root/'complete.json',dict(binding,config_sha256=runner._hash(config),
            first_intent_sha256=runner._hash(initial)))
        validate_completed(state,config)
        return dict(schema=SCHEMA,status='configured',network_requests=requests,network_writes=writes,
            session_creation_confirmed=True,intake_started=False,automatic_intake_enabled=False)
    finally:os.close(lock)
