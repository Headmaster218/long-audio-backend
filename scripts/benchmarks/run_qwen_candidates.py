#!/usr/bin/env python3
"""Explicit offline Qwen candidates; no HTTP or primary ASR integration.

freeze/list/show/cancel are model-free. run requires an exact frozen SHA256 and
explicit authorization receipt; it never automatically retries an old attempt.
Runtime JSON is qwen-local-runtime-v1: model/python/native_library file receipts
{path,resolved_path,bytes,sha256}, files[] wrapper/native inventory, package_version,
native_commit, model_id, model_revision and license. No assets are downloaded.
Limits also require cleanup_seconds (5..30) and cleanup_write_bytes (256 KiB..1 MiB,
at most half the artifact cap). This finite terminal-only reserve is admitted
before model work; it does not renew a compute deadline or permit another call.
This offline prototype accepts at most 1024 windows (5h41m20s at full 20s each);
the 2 MiB manifest cap may bind earlier. It does not yet support a single 12h
Qwen run. Longer jobs need separately reviewed paged/lazy inventory, not a cap
increase or a claim borrowed from the existing primary long-audio pipeline.
preflight_seconds is explicit (<=600) and also counts inside timeout_seconds,
the overall preflight+compute deadline. Full source/model verification is kept;
startup can read the same bytes repeatedly, so short-clip timing must include it.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from audio_backend.fragment_core import SourcePlan
from audio_backend.fragment_runtime_contracts import digest
from audio_backend.qwen_candidate_runner import read_json, freeze, run, atomic_json, validate_limits, make_guard, preflight_scope
from audio_backend.qwen_candidate_store import CandidateStore
from audio_backend.qwen_candidates import public_candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('freeze', 'run', 'list', 'show', 'cancel'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-plan', type=Path)
    parser.add_argument('--source-bindings', type=Path)
    parser.add_argument('--runtime-manifest', type=Path)
    parser.add_argument('--limits', type=Path)
    parser.add_argument('--source-scope')
    parser.add_argument('--source-generation', type=int, default=0)
    parser.add_argument('--frozen', type=Path)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--candidate-id')
    parser.add_argument('--after', type=int, default=0)
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args(argv)
    out = args.output.absolute()
    if out.resolve() != out:
        parser.error('Output directory must not contain symlinks')
    if args.mode == 'freeze':
        if not all((args.source_plan, args.source_bindings, args.runtime_manifest, args.limits, args.source_scope)):
            parser.error('freeze requires source-plan/bindings/runtime-manifest/limits/source-scope')
        limits = validate_limits(read_json(args.limits))
        guard = make_guard(ROOT, limits, read_only=True)
        with preflight_scope(guard, limits['preflight_seconds']):
            # Input metadata is regular-file/bounded before parsing; the same
            # preflight guard covers full source scans and runtime hashes.
            plan_value = read_json(args.source_plan)
            plan = SourcePlan.from_json(json.dumps(plan_value))
            value = freeze(plan, read_json(args.source_bindings), read_json(args.runtime_manifest),
                           limits, source_scope=args.source_scope,
                           source_generation=args.source_generation, guard=guard)
        import shutil
        if shutil.disk_usage(out.parent).free < 5 * 1024**3 + 2 * 1024**2:
            parser.error('Insufficient disk headroom for frozen metadata')
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_json(out / 'frozen.json', value)
        result = {'frozen_sha256': digest(value), 'windows': len(value['requests']),
                  'submitted_frames_if_run_once': sum(r['float32_count'] for r in value['requests']),
                  'inference_executed': False}
    elif args.mode == 'run':
        if not all((args.frozen, args.expected_sha256, args.approval)):
            parser.error('run requires frozen/expected-sha256/approval')
        result = run(args.frozen.absolute(), args.expected_sha256, read_json(args.approval), out)
    else:
        if not (out / 'qwen-candidates.sqlite3').is_file():
            parser.error('Candidate ledger does not exist; no implicit initialization')
        store = CandidateStore(out)
        if args.mode == 'list':
            result = store.events(args.after, args.limit)
        elif args.mode == 'cancel':
            store.cancel(); result = {'cancel_requested': True, 'automatic_retry': False}
        else:
            if not args.candidate_id:
                parser.error('show requires candidate-id')
            row = store.request(args.candidate_id)
            result = {'request': row['request'], 'status': row['status'], 'attempts': [], 'candidate_only': True}
            for attempt in row['attempts']:
                evidence = attempt['evidence'] or {}
                public = {'attempt_id': attempt['id'], 'status': attempt['status'],
                          'execution_started': bool(attempt['started']),
                          'execution_returned': bool(attempt['returned']), 'retry_of': attempt['retry_of']}
                if 'native' in evidence:
                    public['candidate'] = public_candidate(row['request'], evidence['native'])
                if 'runtime' in evidence:
                    public['runtime'] = {key: evidence['runtime'][key] for key in (
                        'package_version', 'native_version', 'native_commit', 'actual_backend',
                        'device', 'library_sha256', 'model_sha256', 'runtime_manifest_sha256')
                        if key in evidence['runtime']}
                if 'failure' in evidence:
                    public['failure_type'] = (evidence['failure'] or {}).get('type')
                result['attempts'].append(public)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
