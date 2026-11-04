"""Create or verify exactly one next session from a completed logical span."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import time

from . import recorder_logical_boundary as boundary
from . import recorder_inbox_runner as runner
from . import recorder_runner_initialize as initialize
from . import recorder_intake as single
from . import recording_uploader as u

require=single.require
SCHEMA='recorder-logical-next-session-v1'


def advance(*, state_dir, acknowledge_create_next_session=False, exclusive_writer=False,
            acknowledge_span_local_closure=False, deadline_seconds=120, transport=None):
    require(acknowledge_create_next_session is True and exclusive_writer is True and
            acknowledge_span_local_closure is True,'logical_next_explicit_ack_required')
    require(type(deadline_seconds) in (int,float) and 1<=deadline_seconds<=120,
            'logical_next_deadline_bound')
    guard=u.Guard(u.Limits(deadline_seconds=deadline_seconds))
    def remaining():guard.check();return max(.000001,guard.end-time.monotonic())
    state=runner._path(str(state_dir));single.private_directory(state)
    with ExitStack() as stack:
        fd=runner.engine.held_lock(state/'runner.lock');stack.callback(os.close,fd)
        config=single.manifest_read(state/'config.json');runner._config(config,state)
        intents,completed=runner._ledger(state,config);runner._job_prefix(config,intents,completed)
        require(completed==len(intents) and completed>0,'logical_next_pending_selection')
        job=Path(config['job_dir'])
        for path in (job/'intake.lock',job/'uploader/uploader.lock'):
            fd=runner.engine.held_lock(path);stack.callback(os.close,fd)
        header=single.manifest_read(job/'capture.json')
        receipts,records=runner.engine.registry(job,header,remaining,policy=runner.intake.POLICY)
        cut=boundary.verify(job,header,receipts,records,remaining)
        require(cut is not None,'logical_next_boundary_required')
        require(os.path.lexists(state/'finalize.done.json') and os.path.lexists(state/'export.done.json'),
                'logical_next_completed_export_required')
        export_intent=single.manifest_read(state/'export.intent.json')
        saved_export=single.manifest_read(state/'export.done.json')
        def check_old():
            remaining()
            require(boundary.verify(job,header,receipts,records,remaining)==cut,'logical_next_boundary_changed')
            require(runner._same(single.manifest_read(state/'export.done.json'),saved_export) and
                    runner._same(runner._export_receipt(state,config,export_intent,guard),saved_export),
                    'logical_next_export_changed')
            output=Path(config['output_dir'])
            if 'selected_attempt' in saved_export:output=output/saved_export['selected_attempt']
            manifest=single.manifest_read(output/'complete.json')
            require(hashlib.sha256(single._json(manifest)).hexdigest()==saved_export['complete_sha256'] and
                manifest.get('logical_boundary_sha256')==boundary.digest(cut) and
                manifest.get('closure_kind')=='logical_analysis_boundary' and
                manifest.get('recording_complete') is False and
                manifest.get('recognition_continuity_verified') is False and
                manifest.get('frames')==cut['frames'],'logical_next_export_boundary_binding')
        check_old()
        nxt=cut['next_session_intent'];successor=cut['successor'];first=Path(successor['path'])
        selected_config=boundary.source_config(cut,config)
        next_state=runner._path(nxt['state_dir'])
        template=runner.config_from_delivery(first_delivery=first,inbox_dir=selected_config['inbox_dir'],
            job_dir=nxt['job_dir'],output_dir=nxt['output_dir'],api=config['api'],
            session_id=initialize.PENDING,capture_id=nxt['capture_id'],max_hours=config['max_hours'],
            max_inputs=config['max_inputs'],receiver_state_dir=selected_config.get('receiver_publication',{}).get('state_dir'))
        handoff=nxt.get('reviewed_identity_handoff')
        if handoff is not None:template=dict(template,schema=runner.CONFIG_SCHEMA_V2,reviewed_identity_handoff=handoff)
        expected=initialize._intent(template,first,successor['delivery'],nxt['session_name'],handoff)
        def completed_link():
            single.private_directory(next_state)
            fd=runner.engine.held_lock(next_state/'runner.lock')
            try:
                current=single.manifest_read(next_state/'config.json');runner._config(current,next_state)
                initialize.validate_completed(next_state,current)
                require(runner._same(single.manifest_read(next_state/initialize.DIRECTORY/'intent.json'),expected)
                    and runner._same(dict(current,session_id=initialize.PENDING),template)
                    and current['session_id']!=config['session_id'],'logical_next_initialization_binding')
                following,done=runner._ledger(next_state,current);runner._job_prefix(current,following,done)
                require(following and following[0]['path']==str(first) and
                    runner._same(following[0]['delivery'],successor['delivery']), 'logical_next_first_binding')
                return current
            finally:os.close(fd)
        requests=writes=0
        complete=next_state/initialize.DIRECTORY/'complete.json'
        if os.path.lexists(complete):
            current=completed_link()  # Works after the next job has started; no HTTP.
        else:
            base=transport or u.HTTPTransport(config['api'],guard)
            class CheckedTransport:
                def request(self,method,path,**kwargs):
                    remaining()
                    if method!='GET':
                        check_old();boundary.durable(job,cut)
                    response=base.request(method,path,**kwargs)
                    if method=='POST' and path=='/sessions':
                        require(type(response) is dict and response.get('id')!=config['session_id'],
                                'logical_next_distinct_session_required')
                    return response
            left=remaining();require(left>=1,'logical_next_deadline_bound')
            result=initialize.initialize(state_dir=next_state,inbox_dir=selected_config['inbox_dir'],
                job_dir=nxt['job_dir'],first_delivery=first,api=config['api'],capture_id=nxt['capture_id'],
                output_dir=nxt['output_dir'],receiver_state_dir=selected_config.get('receiver_publication',{}).get('state_dir'),
                max_hours=config['max_hours'],max_inputs=config['max_inputs'],session_name=nxt['session_name'],
                exclusive_writer=True,acknowledge_span_local_closure=True,resume=os.path.lexists(next_state),
                deadline_seconds=min(120,left),transport=CheckedTransport(),
                **(dict(preserve_reviewed_identity_group=handoff['identity_group'],
                    identity_source_session_id=handoff['source_session_id'],identity_generation=handoff['generation'],
                    identity_policy_sha256=handoff['policy_sha256']) if handoff else {}))
            requests=result['network_requests'];writes=result['network_writes']
            current=completed_link()
        check_old()
        return dict(schema=SCHEMA,status='next_session_linked',network_requests=requests,network_writes=writes,
            boundary_sha256=boundary.digest(cut),next_config_sha256=runner._hash(current),
            next_initialization_sha256=runner._hash(expected),session_creation_confirmed=True,
            closed_span_frames=cut['frames'],next_original_sequence=successor['delivery']['sequence'],
            next_original_frame_start=successor['delivery']['frame_start'],
            next_intake_started=os.path.lexists(nxt['job_dir']),recording_complete=False,
            recognition_continuity_verified=False,atomic_rollover=False)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir',required=True)
    p.add_argument('--deadline-seconds',type=float,default=120)
    for flag in ('acknowledge-create-next-session','exclusive-writer','acknowledge-span-local-closure'):
        p.add_argument('--'+flag,action='store_true',required=True)
    try:result=advance(**vars(p.parse_args(argv)))
    except (Exception,KeyboardInterrupt):
        print(json.dumps(dict(status='refused',reason='logical_next_refused',preserve_existing_files=True,
                              retry_session_creation=False)))
        return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
