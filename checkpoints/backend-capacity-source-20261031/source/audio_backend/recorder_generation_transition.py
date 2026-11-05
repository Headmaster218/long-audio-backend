"""One bounded, explicitly authorized generation-to-analysis transition.

The receiver is controlled separately. A private route configuration declares a
selection; reading it never proves which configuration a live service is using.
Recovery uses the existing boundary, runner and initialization receipts only.
"""
import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import time

from . import recorder_inbox_runner as runner
from . import recorder_logical_boundary as boundary
from . import recorder_logical_advance as advance
from . import recorder_intake as single
from . import recording_uploader as u

SCHEMA = 'recorder-generation-transition-v1'
require = single.require


def _existing(state, config, expected, remaining):
    """Verify a complete receipt under its existing locks, without preparing."""
    def local_remaining():
        return min(120, remaining())
    with ExitStack() as stack:
        stack.callback(os.close, runner.engine.held_lock(state/'runner.lock'))
        current = single.manifest_read(state/'config.json')
        runner._config(current, state)
        require(runner._same(current, config), 'transition_runner_changed')
        intents, completed = runner._ledger(state, current)
        runner._job_prefix(current, intents, completed)
        require(completed == len(intents) and completed > 0, 'transition_pending_input')
        job = Path(current['job_dir'])
        for path in (job/'intake.lock', job/'uploader/uploader.lock'):
            stack.callback(os.close, runner.engine.held_lock(path))
        header = single.manifest_read(job/'capture.json')
        receipts, records = runner.engine.registry(job, header, local_remaining, policy=runner.intake.POLICY)
        cut = boundary.verify(job, header, receipts, records, local_remaining)
        require(cut is not None and all(runner._same(cut.get(key), value)
                for key, value in expected.items()), 'transition_saved_intent_changed')
        boundary.durable(job, cut)
        return cut


def transition(*, state_dir, route_config, successor_attempt, next_state_dir,
               next_job_dir, next_output_dir, next_capture_id, next_session_name='',
               acknowledge_logical_analysis_boundary=False, finalize=False, export=False,
               acknowledge_create_next_session=False, exclusive_writer=False,
               acknowledge_span_local_closure=False, deadline_seconds=120,
               max_actions=256, max_polls=256, poll_seconds=5, max_requests=1024,
               max_network_writes=512, max_network_bytes=runner.MAX_NETWORK_BYTES,
               export_recovery_attempts=0, limits=None, transport=None, cancel=None,
               preserve_reviewed_identity_group=None,identity_generation=None,identity_policy_sha256=None,
               busy_read_retries=0, expected_config_sha256=None, expected_routing_provenance=None,
               expected_successor_sha256=None, native_turns=False):
    """Prepare the exact cut, finish/export the old span, then link one successor.

All phases share a monotonic deadline and outer network budgets. The old runner
retains its independent bounds; next initialization reserves up to six requests
and one session POST (eleven requests with reviewed-bank preservation), and never
retries an uncertain POST. A bounded pause is not success.
"""
    native_options = runner.native_export_options(native_turns, export_recovery_attempts)
    require(all(flag is True for flag in (acknowledge_logical_analysis_boundary,
        finalize, export, acknowledge_create_next_session, exclusive_writer,
        acknowledge_span_local_closure)), 'transition_explicit_ack_required')
    require(type(deadline_seconds) in (int, float) and math.isfinite(deadline_seconds)
            and 1 <= deadline_seconds <= 86400, 'transition_deadline_bound')
    for value, maximum in ((max_actions, runner.MAX_WATCH_ACTIONS),
                           (max_polls, runner.MAX_WATCH_POLLS),
                           (max_requests, runner.MAX_REQUESTS),
                           (max_network_writes, runner.MAX_NETWORK_WRITES)):
        require(type(value) is int and 1 <= value <= maximum, 'transition_invocation_bound')
    require(type(poll_seconds) in (int, float) and math.isfinite(poll_seconds)
            and .1 <= poll_seconds <= 3600, 'transition_poll_bound')
    require(type(max_network_bytes) is int and u.JSON_BYTES <= max_network_bytes <= runner.MAX_NETWORK_BYTES,
            'transition_network_byte_bound')
    require(type(export_recovery_attempts) is int and export_recovery_attempts in (0, 2, 3),
            'transition_export_recovery_bound')
    require(type(busy_read_retries) is int and 0 <= busy_read_retries <= 3,
            'transition_busy_read_retry_bound')
    require(type(successor_attempt) is str and runner.inbox.NAME.fullmatch(successor_attempt),
            'transition_exact_successor_required')
    require(type(next_session_name) is str and len(next_session_name) <= 200,
            'transition_next_name_bound')
    guard = u.Guard(u.Limits(deadline_seconds=deadline_seconds), cancel)
    def remaining():
        guard.check()
        return max(.000001, guard.end-time.monotonic())

    state = runner._path(str(state_dir))
    single.private_directory(state)
    lock = runner.engine.held_lock(state/'runner.lock')
    try:
        config = single.manifest_read(state/'config.json')
        runner._config(config, state)
        runner.check_export_policy(state, native_turns)
        runner.check_export_policy(runner._path(str(next_state_dir)), native_turns)
        require(config['output_dir'] is not None, 'transition_old_export_required')
        pair, provenance, _ = boundary.routing_selection(route_config, config)
        require(expected_config_sha256 is None or boundary.digest(config) == expected_config_sha256,
                'transition_discovery_config_changed')
        require(expected_routing_provenance is None or runner._same(provenance, expected_routing_provenance),
                'transition_discovery_route_changed')
    finally:
        os.close(lock)
    nxt = dict(state_dir=str(runner._path(str(next_state_dir))),
        job_dir=str(runner._path(str(next_job_dir))),
        output_dir=str(runner._path(str(next_output_dir))),
        capture_id=next_capture_id, session_name=next_session_name)
    from . import recorder_runner_initialize as initialize
    supplied_identity=any(item is not None for item in (preserve_reviewed_identity_group,identity_generation,identity_policy_sha256))
    handoff=initialize.reviewed_handoff(config['session_id'] if supplied_identity else None,
        preserve_reviewed_identity_group,identity_generation,identity_policy_sha256)
    if handoff is not None:
        nxt.update(reviewed_identity_handoff=handoff,session_creation_request=initialize.creation_request(
            config['api'],next_session_name,handoff))
    # The caller supplies the exact attempt, never an inferred newest arrival.
    first = Path(pair['inbox_dir'])/successor_attempt
    expected = dict(next_session_intent=nxt, routing_provenance=provenance,
                    successor_publication=pair)
    job = Path(config['job_dir'])
    if not os.path.lexists(job/boundary.NAME):
        boundary.prepare(state_dir=state, successor_attempt=successor_attempt,
            next_state_dir=nxt['state_dir'], next_job_dir=nxt['job_dir'],
            next_output_dir=nxt['output_dir'], next_capture_id=next_capture_id,
            next_session_name=next_session_name, acknowledge_logical_analysis_boundary=True,
            next_inbox_dir=pair['inbox_dir'], next_receiver_state_dir=pair['binding']['state_dir'],
            route_config=provenance['config_path'], deadline_seconds=min(120, remaining()), cancel=cancel,
            preserve_reviewed_identity_group=preserve_reviewed_identity_group,identity_generation=identity_generation,
            identity_policy_sha256=identity_policy_sha256)
    cut = _existing(state, config, expected, remaining)
    require(cut['successor']['path'] == str(first), 'transition_saved_successor_changed')
    require(expected_successor_sha256 is None or boundary.digest(cut['successor']['delivery']) == expected_successor_sha256,
            'transition_discovery_successor_changed')

    def result(status, phase, **extra):
        return dict(schema=SCHEMA, status=status, phase=phase,
            boundary_sha256=boundary.digest(cut), invocation_bounded=True,
            receiver_runtime_verified=False, receiver_process_changed=False,
            atomic_rollover=False, recording_complete=False,
            recognition_continuity_verified=False, **extra)

    base = transport or u.HTTPTransport(config['api'], guard)
    class CheckedTransport:
        requests=writes=response_bytes=0
        def request(self, method, path, **kwargs):
            guard.check()
            if handoff is not None:
                if method!='GET' and path.startswith('/sessions/'+config['session_id']+'/'):
                    initialize.check_reviewed_identity(self.request,handoff,config['session_id'])
            require(self.requests<max_requests and (method=='GET' or self.writes<max_network_writes),
                    'transition_identity_network_request_bound' if handoff is not None else 'transition_network_request_bound')
            require(self.response_bytes+u.JSON_BYTES<=max_network_bytes,
                    'transition_identity_network_byte_bound' if handoff is not None else 'transition_network_byte_bound')
            self.requests+=1;self.writes+=method!='GET'
            # Failed responses and interruptions retain the conservative charge.
            # Each actual underlying request, including identity reads, counts once.
            self.response_bytes+=u.JSON_BYTES
            value = base.request(method, path, **kwargs)
            guard.check()
            require(type(value) is dict and len(single._json(value))<=u.JSON_BYTES,
                    'transition_identity_response_bound' if handoff is not None else 'transition_response_bound')
            self.response_bytes+=len(single._json(value))-u.JSON_BYTES
            return value
    checked = CheckedTransport()
    if handoff is not None and not os.path.lexists(state/'export.done.json'):
        initialize.check_reviewed_identity(checked.request,handoff,config['session_id'])
    finished = runner.watch(state_dir=state, exclusive_writer=True,
        acknowledge_span_local_closure=True, finalize=True, export=True,
        export_recovery_attempts=export_recovery_attempts, max_actions=max_actions,
        max_polls=max_polls, poll_seconds=poll_seconds, max_requests=max_requests,
        max_network_writes=max_network_writes, max_network_bytes=max_network_bytes,
        deadline_seconds=remaining(), limits=limits, transport=checked, cancel=cancel,
        busy_read_retries=busy_read_retries, **native_options)
    requests, writes = checked.requests, checked.writes
    if finished['status'] != 'exported':
        return result('transition_paused', 'finish_old_span', stop_reason=finished['status'],
            network_requests=requests, network_writes=writes,
            network_response_bytes=checked.response_bytes,
            stop_details={key:finished[key] for key in ('status', 'reason', 'category', 'error_type',
                'http_status', 'admission_reason', 'possible_remote_effects', 'busy_read_retries',
                'busy_read_retries_used') if key in finished},
            old_export_verified=False, next_session_linked=False,
            preserve_existing_files=True, automatic_retry=False)
    if guard.end-time.monotonic() < 1:
        return result('transition_paused', 'link_next_session', stop_reason='deadline_reached',
            network_requests=requests, network_writes=writes,
            network_response_bytes=checked.response_bytes,
            old_export_verified=True, next_session_linked=False,
            preserve_existing_files=True, automatic_retry=False)
    _existing(state, config, expected, remaining)
    if not os.path.lexists(Path(nxt['state_dir'])/initialize.DIRECTORY/'complete.json'):
        # Never strand post.intent on a coordinator budget refusal that is known
        # not to have dispatched. Reserve the whole bounded bootstrap first.
        bootstrap_requests = 11 if handoff is not None else 6
        if (checked.requests+bootstrap_requests>max_requests or checked.writes+1>max_network_writes
                or checked.response_bytes+bootstrap_requests*u.JSON_BYTES>max_network_bytes):
            return result('transition_paused','link_next_session',stop_reason=(
                'identity_initialization_budget' if handoff is not None else 'initialization_budget'),
                network_requests=checked.requests,network_writes=checked.writes,
                network_response_bytes=checked.response_bytes,
                old_export_verified=True,next_session_linked=False,
                preserve_existing_files=True,automatic_retry=False)
    following = advance.advance(state_dir=state, acknowledge_create_next_session=True,
        exclusive_writer=True, acknowledge_span_local_closure=True,
        deadline_seconds=min(120, remaining()), transport=checked)
    guard.check()
    return result('transition_complete', 'next_session_linked',
        network_requests=checked.requests, network_writes=checked.writes,
        network_response_bytes=checked.response_bytes,
        old_export_verified=True, next_session_linked=True,
        next_config_sha256=following['next_config_sha256'],
        next_initialization_sha256=following['next_initialization_sha256'],
        closed_span_frames=following['closed_span_frames'],
        next_original_sequence=following['next_original_sequence'],
        next_original_frame_start=following['next_original_frame_start'],
        next_intake_started=following['next_intake_started'])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('state-dir', 'route-config', 'successor-attempt', 'next-state-dir',
                 'next-job-dir', 'next-output-dir', 'next-capture-id'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--next-session-name', default='')
    for name in ('preserve-reviewed-identity-group','identity-generation','identity-policy-sha256'):p.add_argument('--'+name)
    for name in ('acknowledge-logical-analysis-boundary', 'finalize', 'export',
                 'acknowledge-create-next-session', 'exclusive-writer',
                 'acknowledge-span-local-closure'):
        p.add_argument('--'+name, action='store_true', required=True)
    p.add_argument('--deadline-seconds', type=float, default=120)
    p.add_argument('--max-actions', type=int, default=256)
    p.add_argument('--max-polls', type=int, default=256)
    p.add_argument('--poll-seconds', type=float, default=5)
    p.add_argument('--max-requests', type=int, default=1024)
    p.add_argument('--max-network-writes', type=int, default=512)
    p.add_argument('--max-network-bytes', type=int, default=runner.MAX_NETWORK_BYTES)
    p.add_argument('--export-recovery-attempts', type=int, choices=(0, 2, 3), default=0)
    p.add_argument('--native-turns', action='store_true')
    p.add_argument('--busy-read-retries', type=int, choices=range(4), default=0)
    try:
        value = transition(**vars(p.parse_args(argv)))
    except (Exception, KeyboardInterrupt):
        print(json.dumps(dict(schema=SCHEMA, status='refused', reason='generation_transition_refused',
            preserve_existing_files=True, automatic_retry=False, retry_session_creation=False,
            receiver_runtime_verified=False, receiver_process_changed=False)))
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0 if value['status'] == 'transition_complete' else 3


if __name__ == '__main__':
    raise SystemExit(main())
