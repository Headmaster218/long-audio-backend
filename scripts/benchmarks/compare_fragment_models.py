#!/usr/bin/env python3
"""Default is model-free protocol output. Real stages require explicit receipts."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from audio_backend.fragment_model_compare import Watchdog, atomic_json, execute_base, execute_moss, require_private_output
from audio_backend.fragment_model_guard import Identity, ResourceGuard, ResourceLimit
from audio_backend.fragment_model_protocol import freeze, proposal, validate_approval
from audio_backend.fragment_runtime_contracts import digest


def main():
    os.chdir(ROOT)  # NativeProcess -m imports must originate from this frozen checkout.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['protocol', 'freeze', 'base', 'moss'], nargs='?', default='protocol')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--moss-config', type=Path, help='Optional separately staged hash-only MOSS closure')
    parser.add_argument('--extra-assets', type=Path, help='JSON list of explicit additional existing consumed file paths')
    parser.add_argument('--freeze', type=Path)
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.mode == 'protocol':
        print(json.dumps(proposal(), indent=2)); return
    if args.output is None:
        parser.error('--output is required')
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error('All experiment outputs must be inside the isolated checkout')
    output = require_private_output(output)
    output.mkdir(parents=True, exist_ok=True)
    if args.mode == 'freeze':
        if not all((args.manifest, args.source_root, args.config)):
            parser.error('freeze needs --manifest, --source-root, and --config')
        guard = ResourceGuard(ROOT, (Identity.bind(os.getpid()),), own_cap=512*1024**2, deadline=600)
        guard.check()
        extra = json.loads(args.extra_assets.read_text()) if args.extra_assets else []
        config = json.loads(args.config.read_text())
        frozen = freeze(args.source_root, args.manifest, config, extra_assets=extra,
                        moss_config=json.loads(args.moss_config.read_text()) if args.moss_config else None)
        atomic_json(output/'freeze.json', frozen, guard)
        print(json.dumps({'freeze_sha256': digest(frozen), 'files': len(frozen['files']), 'models_executed': 0})); return
    if not args.approval or not args.freeze:
        parser.error('No inference admission without --freeze and --approval; parent authorization is required separately')
    frozen, approval = json.loads(args.freeze.read_text()), json.loads(args.approval.read_text())
    validate_approval(approval, frozen, args.mode)
    cpus = approval.get('cpu_affinity')
    if (not isinstance(cpus, list) or not 1 <= len(cpus) <= 2
        or any(type(c) is not int or c not in os.sched_getaffinity(0) for c in cpus)):
        parser.error('Explicit available execution CPUs required')
    roots = approval.get('project_roots', [])
    mode = approval.get('resource_observation_mode', 'same_namespace')
    feeds = []
    if mode == 'asynchronous_external_feed':
        from audio_backend.fragment_resource_feed import EnduranceFeed
        feed_configs = approval.get('external_feeds')
        if not isinstance(feed_configs, list) or not feed_configs or not approval.get('asynchronous_resource_contract_approved'):
            parser.error('Cross-namespace mode requires separately approved feed paths/freshness and estimate caveat')
        feeds = [EnduranceFeed(**config) for config in feed_configs]
    elif mode != 'same_namespace':
        parser.error('Unknown resource observation mode')
    if not isinstance(roots, list) or not (roots or feeds):
        parser.error('Explicit externally identified project PID/create-time roots required; no cross-namespace guess')
    project = tuple(Identity(**r) for r in roots)
    own_cap = approval.get('own_rss_cap_bytes')
    maximum_cap = 1024**3 if args.mode == 'base' else int(3.5*1024**3)
    if type(own_cap) is not int or not 0 < own_cap <= maximum_cap:
        parser.error('Explicit bounded experiment RSS cap required')
    os.sched_setaffinity(0, cpus)
    # Per-process child environment only. No service or installed environment change.
    for key in ('OMP_NUM_THREADS', 'OMP_THREAD_LIMIT', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[key] = '1'
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONDONTWRITEBYTECODE='1')
    (output/'tmp').mkdir(exist_ok=True)
    os.environ['TMPDIR'] = str(output/'tmp')
    guard = ResourceGuard(ROOT, (Identity.bind(os.getpid()),), project, own_cap=own_cap, deadline=1800, external_feeds=feeds)
    guard.check()
    watchdog = Watchdog(guard, output/'watchdog-failure.json')
    watchdog.start()
    try:
        function = execute_base if args.mode == 'base' else execute_moss
        result = function(frozen, approval, output, guard, watchdog)
        atomic_json(output/(args.mode+'-resource-summary.json'), {
            'peak_own_rss': guard.peak_own, 'peak_aggregate_project_rss': guard.peak_aggregate,
            'minimum_host_available': guard.minimum_host, 'minimum_disk_free': guard.minimum_disk,
            'sampled_enforcement_only': True, 'project_roots': roots,
            'aggregate_observation': 'asynchronous_estimate_not_simultaneous' if feeds else 'same_namespace_sample',
            'last_external_samples': guard.latest_external}, guard)
        print(json.dumps({'status': 'terminal', 'result_sha256': digest(result)}))
    finally:
        watchdog.close()
        guard.terminate_owned()


if __name__ == '__main__':
    main()
