"""One service/Store: normal API A complete, then B; one shared deadline/ledger."""
import argparse
import json
import os
from pathlib import Path
import re
import time

from ab_profile import KIND, GROUP, PROFILE, validate_arm
from a_input import AInput
from b_input import BInput
from a_uploader_cli import collect as collect_a
from b_uploader_cli import collect as collect_b
from http_input import RequestLedger
from launch_binding import prepare
from service_cli import write_once
from uploader_cli import api, run_owned


def collect(call, pieces, *, deadline, clock=time.monotonic, sleep=time.sleep):
    sessions, results = {}, {}
    for arm, collector in [('A', collect_a), ('B', collect_b)]:
        if clock() >= deadline: raise TimeoutError('AB shared deadline before arm')
        def arm_call(method, route, path, **kwargs):
            if method == 'POST' and path == '/sessions':
                if arm in sessions: raise ValueError('AB duplicate session creation forbidden')
                body = json.loads(kwargs['body'])
                if body != dict(name='', identity_group=arm+'-anonymous', processing_mode='legacy'):
                    raise ValueError('unexpected arm session request')
                body.update(identity_group=GROUP, identity_mode='verified_review_v1')
                kwargs['body'] = json.dumps(body).encode()
                response = call(method, route, path, **kwargs)
                sid = response.get('id')
                if (not isinstance(sid, str) or re.fullmatch('[a-f0-9]{32}', sid) is None
                        or sid in sessions.values() or response.get('identity_group') != GROUP):
                    raise ValueError('AB distinct session/shared group not established')
                sessions[arm] = sid
                return response
            return call(method, route, path, **kwargs)
        results[arm] = collector(arm_call, pieces[arm], deadline=deadline, clock=clock, sleep=sleep)
        validate_arm(arm, results[arm])
        if arm not in sessions: raise ValueError('AB arm missing accepted session')
        if clock() >= deadline: raise TimeoutError('AB shared deadline after arm')
    if set(sessions) != {'A', 'B'} or len(set(sessions.values())) != 2:
        raise ValueError('AB requires two distinct completed sessions')
    return dict(completed_chunks=138, frames=23040000, seconds=1440,
                accepted_sources=16, pages=6, eos_pages=2, exact_offsets=True,
                exact_frame_conservation=True, integration_profile=PROFILE,
                sessions=sessions, arms=results, probes='UNKNOWN', vector_success='UNKNOWN',
                identity_learning='NOT_QUALIFIED', accuracy='NOT_EVALUATED')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--without-trial-observer', action='store_true', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--uploader-lock-fd', type=int, required=True)
    parser.add_argument('--deadline', type=float, required=True)
    args = parser.parse_args(argv)
    value, _ = prepare(args.manifest, args.manifest_sha256, deadline=args.deadline, output=args.output)
    if value['qualification'] != KIND: raise ValueError('explicit AB manifest required')
    if not 1 <= len(os.sched_getaffinity(0)) <= 2: raise ValueError('CPU restriction missing')
    from audio_backend.resources import InstanceLock
    owner = InstanceLock(args.output/'journal/uploader.lock'); owner.adopt(args.uploader_lock_fd)
    def action():
        with AInput(value['input']['A']['path'], args.deadline) as source_a, BInput(value['input']['B']['path'], args.deadline) as source_b, (args.output/'uploader-requests.jsonl').open('x', encoding='utf-8') as stream:
            ledger = RequestLedger(stream)
            def call(method, route, path, **kwargs):
                return api(value['port'], method, route, path, deadline=args.deadline, ledger=ledger, **kwargs)
            result = collect(call, {'A': source_a.piece, 'B': source_b.piece}, deadline=args.deadline)
        write_once(args.output/'uploader-result.json', result)
    run_owned(owner, action)


if __name__ == '__main__': main()
