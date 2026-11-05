"""One bounded automatic handoff between explicitly prepared analysis generations."""
import argparse
import json
import math
import os
from pathlib import Path
import time

from . import recorder_generation_transition as transition
from . import recorder_inbox_runner as runner
from . import recorder_logical_boundary as boundary
from . import recorder_intake as single
from . import recording_uploader as u

SCHEMA = 'recorder-automatic-analysis-handoff-v1'
require = single.require


def handoff(*, state_dir, route_config, next_state_dir, next_job_dir,
            next_output_dir, next_capture_id, next_session_name='',
            acknowledge_automatic_handoff=False, exclusive_writer=False,
            acknowledge_span_local_closure=False, deadline_seconds=120,
            max_polls=64, poll_seconds=5, max_actions=256, max_requests=1024,
            max_network_writes=512, max_network_bytes=runner.MAX_NETWORK_BYTES,
            export_recovery_attempts=0, busy_read_retries=0, native_turns=False,
            limits=None, transport=None, cancel=None,
            preserve_reviewed_identity_group=None, identity_generation=None, identity_policy_sha256=None):
    """Discover, link, and watch one successor; standalone behavior is unchanged."""
    return _coordinate(**locals())


def _coordinate(*, state_dir, route_config, next_state_dir, next_job_dir,
            next_output_dir, next_capture_id, next_session_name='',
            acknowledge_automatic_handoff=False, exclusive_writer=False,
            acknowledge_span_local_closure=False, deadline_seconds=120,
            max_polls=64, poll_seconds=5, max_actions=256, max_requests=1024,
            max_network_writes=512, max_network_bytes=runner.MAX_NETWORK_BYTES,
            export_recovery_attempts=0, busy_read_retries=0, native_turns=False,
            limits=None, transport=None, cancel=None,
            preserve_reviewed_identity_group=None, identity_generation=None, identity_policy_sha256=None,
                link_only=False, phase_budget=None, shared_guard=None, allow_physical_stop=False):
    """Discover a unique contiguous successor, link once, then run its intake.

    Only clean missing-input observations repeat. Uncertain mutations and bounded
    pauses inside transition or successor intake are returned, never replayed.
    The supplied route must select the intended successor and retain the old one.
    """
    native_options = runner.native_export_options(native_turns, export_recovery_attempts)
    require(acknowledge_automatic_handoff is True and exclusive_writer is True
            and acknowledge_span_local_closure is True, 'handoff_explicit_ack_required')
    require(type(deadline_seconds) in (int, float) and math.isfinite(deadline_seconds)
            and 1 <= deadline_seconds <= 86400, 'handoff_deadline_bound')
    require(type(poll_seconds) in (int, float) and math.isfinite(poll_seconds)
            and .1 <= poll_seconds <= 3600, 'handoff_poll_bound')
    for value, maximum in ((max_polls, runner.MAX_WATCH_POLLS),
                           (max_actions, runner.MAX_WATCH_ACTIONS),
                           (max_requests, runner.MAX_REQUESTS),
                           (max_network_writes, runner.MAX_NETWORK_WRITES)):
        require(type(value) is int and 1 <= value <= maximum, 'handoff_invocation_bound')
    require(type(max_network_bytes) is int and u.JSON_BYTES <= max_network_bytes <= runner.MAX_NETWORK_BYTES,
            'handoff_network_byte_bound')
    require(type(export_recovery_attempts) is int and export_recovery_attempts in (0,2,3), 'handoff_export_recovery_bound')
    require(type(busy_read_retries) is int and 0 <= busy_read_retries <= 3, 'handoff_busy_read_bound')
    guard = shared_guard or u.Guard(u.Limits(deadline_seconds=deadline_seconds), cancel)
    def remaining():
        guard.check()
        return min(120, max(.000001, guard.end-time.monotonic()))
    state = runner._path(str(state_dir))
    single.private_directory(state)
    config = single.manifest_read(state/'config.json'); runner._config(config, state)
    runner.check_export_policy(state, native_turns)
    runner.check_export_policy(runner._path(str(next_state_dir)), native_turns)
    pair, provenance, _ = boundary.routing_selection(route_config, config)
    base = transport or u.HTTPTransport(config['api'], guard)
    class Budget:
        requests = writes = bytes = 0
        def request(self, method, path, **kwargs):
            guard.check()
            require(self.requests < max_requests and (method == 'GET' or self.writes < max_network_writes)
                    and self.bytes + u.JSON_BYTES <= max_network_bytes, 'handoff_network_bound')
            self.requests += 1; self.writes += method != 'GET'; self.bytes += u.JSON_BYTES
            value = base.request(method, path, **kwargs)
            guard.check()
            require(type(value) is dict and len(single._json(value)) <= u.JSON_BYTES, 'handoff_response_bound')
            self.bytes += len(single._json(value))-u.JSON_BYTES
            return value
    checked = Budget()
    common = dict(exclusive_writer=True, acknowledge_span_local_closure=True,
        max_actions=max_actions, max_polls=1, poll_seconds=poll_seconds,
        max_requests=max_requests, max_network_writes=max_network_writes,
        max_network_bytes=max_network_bytes, limits=limits, transport=checked, cancel=cancel,
        busy_read_retries=busy_read_retries, **native_options)
    def result(status, phase, **extra):
        return dict(schema=SCHEMA, status=status, phase=phase, invocation_bounded=True,
            network_requests=checked.requests, network_writes=checked.writes,
            network_response_bytes=checked.bytes, automatic_retry=False,
            preserve_existing_files=True, recording_complete=False,
            recognition_continuity_verified=False, receiver_process_changed=False,
            per_phase_max_actions=max_actions, discovery_max_polls=max_polls,
            total_action_upper_bound=(max_polls+2)*max_actions,
            total_poll_upper_bound=3*max_polls, **extra)
    def physical_stop_prefix():
        guard.check()
        lock = runner.engine.held_lock(state/'runner.lock')
        try:
            require(runner._same(single.manifest_read(state/'config.json'), config), 'handoff_runner_changed')
            intents, completed = runner._ledger(state, config)
            runner._job_prefix(config, intents, completed)
            if not (completed == len(intents) and completed > 0
                    and intents[-1]['delivery']['close_reason'] == 'STOP'
                    and not boundary.extra(Path(config['job_dir']))):return False
            runner._inspect(config, intents, remaining)
            guard.check()
            return True
        finally:os.close(lock)

    def finish_terminal():
        require(physical_stop_prefix(), 'handoff_physical_stop_required')
        if phase_budget is not None and not phase_budget.reserve(max_actions, max_polls):
            return result('handoff_paused', 'terminal_export', stop_reason='phase_budget')
        terminal_budgets = dict(common, max_polls=max_polls,
            max_requests=max_requests-checked.requests, max_network_writes=max_network_writes-checked.writes,
            max_network_bytes=max_network_bytes-checked.bytes)
        if (terminal_budgets['max_requests'] < 1 or terminal_budgets['max_network_writes'] < 1
                or terminal_budgets['max_network_bytes'] < u.JSON_BYTES):
            return result('handoff_paused', 'terminal_export', stop_reason='network_budget')
        terminal = runner.watch(state_dir=state, finalize=True, export=True,
            export_recovery_attempts=export_recovery_attempts, deadline_seconds=remaining(), **terminal_budgets)
        guard.check()
        require(physical_stop_prefix(), 'handoff_physical_stop_changed')
        return result('handoff_terminal' if terminal['status'] == 'exported' else 'handoff_paused',
            'terminal_export', physical_stop_observed=True, stop_details=terminal)

    try:
        for poll in range(max_polls):
            guard.check()
            require(runner._same(single.manifest_read(state/'config.json'), config), 'handoff_runner_changed')
            current_pair, current_provenance, _ = boundary.routing_selection(route_config, config)
            require(runner._same(current_pair, pair) and runner._same(current_provenance, provenance),
                    'handoff_route_changed')
            # A saved cut is already an exact durable selection. Never rediscover it.
            cut_path = Path(config['job_dir'])/boundary.NAME
            if os.path.lexists(cut_path):
                saved_successor = single.manifest_read(cut_path)['successor']['delivery']
                successor = saved_successor['attempt']
                successor_sha256 = boundary.digest(saved_successor)
                break
            if allow_physical_stop and physical_stop_prefix():return finish_terminal()
            drain_budgets = dict(common, max_requests=max_requests-checked.requests,
                max_network_writes=max_network_writes-checked.writes, max_network_bytes=max_network_bytes-checked.bytes)
            if (drain_budgets['max_requests'] < 1 or drain_budgets['max_network_writes'] < 1
                    or drain_budgets['max_network_bytes'] < u.JSON_BYTES):
                return result('handoff_paused', 'drain_old_span', stop_reason='network_budget')
            if phase_budget is not None and not phase_budget.reserve(max_actions, 1):
                return result('handoff_paused', 'drain_old_span', stop_reason='phase_budget')
            drained = runner.watch(state_dir=state, deadline_seconds=remaining(), **drain_budgets)
            if shared_guard is not None:guard.check()
            if allow_physical_stop and (drained['status'] in ('awaiting_finalize', 'exported') or
                    drained['status'] == 'poll_limit' and drained.get('last_status') in (None, 'waiting_for_input')):
                if physical_stop_prefix():return finish_terminal()
            if drained['status'] != 'poll_limit' or drained.get('last_status') not in (None, 'waiting_for_input'):
                return result('handoff_paused', 'drain_old_span', stop_details=drained)
            lock = runner.engine.held_lock(state/'runner.lock')
            try:
                require(runner._same(single.manifest_read(state/'config.json'), config), 'handoff_runner_changed')
                intents, completed = runner._ledger(state, config)
                runner._job_prefix(config, intents, completed)
                require(completed == len(intents), 'handoff_pending_input')
                old_candidates = runner._inspect(config, intents, remaining)
                if old_candidates.get(config['span_origin']['first_sequence']+completed):
                    # The one-poll drain made confirmed progress but more old input remains.
                    continue
                if not intents:
                    candidates = []
                else:
                    require(intents[-1]['delivery']['close_reason'] == 'ROTATION', 'handoff_rotation_required')
                    selected = dict(config, inbox_dir=pair['inbox_dir'], receiver_publication=pair['binding'])
                    found = runner._inspect(selected, [], remaining)
                    candidates = found.get(config['span_origin']['first_sequence']+completed, [])
                    require(len(candidates) <= 1, 'handoff_ambiguous_successor')
                if candidates:
                    successor = candidates[0][0].name
                    successor_sha256 = boundary.digest(candidates[0][1])
                    break
            finally:
                os.close(lock)
            if poll+1 < max_polls:
                guard.pause(min(poll_seconds, remaining()))
        else:
            return result('handoff_paused', 'wait_for_successor', stop_reason='poll_limit')
        if guard.end-time.monotonic() < 1:
            return result('handoff_paused', 'transition', stop_reason='deadline_reached')
        transition_budgets = dict(common, max_polls=max_polls,
            max_requests=max_requests-checked.requests, max_network_writes=max_network_writes-checked.writes,
            max_network_bytes=max_network_bytes-checked.bytes)
        if (transition_budgets['max_requests'] < 1 or transition_budgets['max_network_writes'] < 1
                or transition_budgets['max_network_bytes'] < u.JSON_BYTES):
            return result('handoff_paused', 'transition', stop_reason='network_budget')
        if phase_budget is not None and not phase_budget.reserve(max_actions, max_polls):
            return result('handoff_paused', 'transition', stop_reason='phase_budget')
        linked = transition.transition(state_dir=state, route_config=route_config,
            successor_attempt=successor, next_state_dir=next_state_dir, next_job_dir=next_job_dir,
            next_output_dir=next_output_dir, next_capture_id=next_capture_id,
            next_session_name=next_session_name, acknowledge_logical_analysis_boundary=True,
            acknowledge_create_next_session=True, finalize=True, export=True,
            export_recovery_attempts=export_recovery_attempts,
            deadline_seconds=remaining(),
            preserve_reviewed_identity_group=preserve_reviewed_identity_group,
            identity_generation=identity_generation, identity_policy_sha256=identity_policy_sha256,
            expected_config_sha256=boundary.digest(config), expected_routing_provenance=provenance,
            expected_successor_sha256=successor_sha256, **transition_budgets)
        if shared_guard is not None:guard.check()
        if linked['status'] != 'transition_complete':
            return result('handoff_paused', 'transition', stop_details=linked)
        if link_only:
            return result('handoff_linked', 'next_session_linked', next_session_linked=True, transition=linked)
        next_budgets = dict(common, max_polls=max_polls,
            max_requests=max_requests-checked.requests, max_network_writes=max_network_writes-checked.writes,
            max_network_bytes=max_network_bytes-checked.bytes)
        if (next_budgets['max_requests'] < 1 or next_budgets['max_network_writes'] < 1
                or next_budgets['max_network_bytes'] < u.JSON_BYTES):
            return result('handoff_paused', 'successor_intake', next_session_linked=True, stop_reason='network_budget')
        following = runner.watch(state_dir=next_state_dir, finalize=True, export=True,
            export_recovery_attempts=export_recovery_attempts,
            deadline_seconds=remaining(), **next_budgets)
        return result('handoff_complete' if following['status'] == 'exported' else 'handoff_paused',
            'successor_intake', next_session_linked=True, transition=linked, stop_details=following)
    except (Exception, KeyboardInterrupt) as exc:
        failure = single.failure_report(exc)
        return result('handoff_refused', 'review_required', stop_details=failure)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('state-dir','route-config','next-state-dir','next-job-dir','next-output-dir','next-capture-id'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--next-session-name', default='')
    for name in ('preserve-reviewed-identity-group','identity-generation','identity-policy-sha256'):
        p.add_argument('--'+name)
    for name in ('acknowledge-automatic-handoff','exclusive-writer','acknowledge-span-local-closure'):
        p.add_argument('--'+name, action='store_true', required=True)
    for name, default in (('max-polls',64),('max-actions',256),('max-requests',1024),
                          ('max-network-writes',512),('max-network-bytes',runner.MAX_NETWORK_BYTES)):
        p.add_argument('--'+name, type=int, default=default)
    p.add_argument('--export-recovery-attempts', type=int, choices=(0,2,3), default=0)
    p.add_argument('--native-turns', action='store_true')
    p.add_argument('--busy-read-retries', type=int, choices=range(4), default=0)
    p.add_argument('--deadline-seconds', type=float, default=120)
    p.add_argument('--poll-seconds', type=float, default=5)
    try:
        value = handoff(**vars(p.parse_args(argv)))
    except (Exception, KeyboardInterrupt):
        value = dict(schema=SCHEMA,status='handoff_refused',preserve_existing_files=True,automatic_retry=False)
    print(json.dumps(value, sort_keys=True))
    return 0 if value['status']=='handoff_complete' else 3 if value['status']=='handoff_paused' else 2


if __name__ == '__main__':
    raise SystemExit(main())
