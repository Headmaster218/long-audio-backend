#!/usr/bin/env python3
"""Read-only exact admission, then unchanged acquire.main with fixed corpus argv."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import types

HERE = Path(__file__).absolute().parent
PREP = HERE.parent.parent
WORKSPACE = PREP.parent
ACQUIRE = HERE.parent/'acquire.py'
ACQUIRE_BYTES = 53119
ACQUIRE_SHA = 'a79053fae502ed0621c94aa94aafc634fed7997a423d210fb806a2587ea391c1'
MANIFEST_SHA = 'b189c4da6a8a28d1ca27ebba75c65a118d9b14cb1496f0e4b641f0385c79417b'
GUARD_SHA = '3934eb162fc61ba06f4c9e740016e0da04f7eba62e41d5d5e77bbc399971b305'
MODEL_SPEC_SHA = '1d258d6baa890d02c0d076cff9de9883f0e8fc514e4780b45c7c7b7e339b5ab4'
TERMINAL_SHA = '4b935333183d3799245b5d2bb0bf368fddb5c01965e5707364a98dbeb3de0fc9'
MODEL_RUN = PREP/'runs/run-rb_5sr8x'
PYTHON = '/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python3.12'
PYTHON_SHA = 'fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7'
MODEL_SUCCESS = HERE/'MODEL_SUCCESS.json'
MODEL_RECEIPTS = {'guard': MODEL_RUN/'receipt.json',
                  'continuation': MODEL_RUN/'remaining-native-result.json',
                  'stage': PREP/'receipts/stage-model_runtime.json',
                  'models': PREP/'receipts/models-verified.json',
                  'terminal': PREP/'acquisition/remaining-native/TERMINAL_QUALIFICATION.json'}
CORPUS = {'bytes': 3673718355, 'destination': 'corpus/Eval_Ali.tar.gz',
          'id': 'alimeeting-eval',
          'sha256': 'dc47343b2474b5ebcf458927e878155f6ddeb59c85e685b3645c32a1f9578d92',
          'stage': 'corpus',
          'url': 'https://speech-lab-share-data.oss-cn-shanghai.aliyuncs.com/AliMeeting/openlr/Eval_Ali.tar.gz'}
FIXED_ARGV = [str(ACQUIRE), 'run', '--prep-root', str(PREP), '--stage', 'corpus',
              '--manifest', str(HERE.parent/'ASSET_MANIFEST.json')]


# Cumulative 384 KiB bookkeeping envelope. Prior corpus-first draft remains charged separately.
METADATA_PARTITIONS = {'prepared_sources_specs_and_seal': 128*1024,
    'synthetic_guard_result_and_log': 64*1024, 'actual_guard_and_log': 32*1024,
    'actual_worker_json': 64*1024, 'owner_reviews_handles_terminal': 64*1024,
    'contingency_retained_failures': 32*1024}
CLAIM = 'receipts/corpus-first-verified.spent'


def validate_quotas(a, manifest):
    spec = manifest['archives']['alimeeting-eval']
    a.require(spec == {'kind': 'gz', 'root': None, 'output': 'corpus/selected',
              'scan': 16*2**30, 'output_cap': 402982176, 'members': 4096,
              'selected': manifest['selected_corpus_members']}, 'exact inherited parser quotas')
    a.require(len(spec['selected']) == 2 and sum(x['bytes'] for x in spec['selected']) == 402982176,
              'two exact selected member output cap')
    a.require(a.RATE == 4*2**20 and a.BLOCK == 256*1024 and a.MAX_EXT == 65536 and
              a.MAX_PATH == 4096, 'inherited streaming and extension limits')
    a.require(sum(METADATA_PARTITIONS.values()) == 384*1024, 'aggregate bookkeeping cap')


class MetadataLimit(RuntimeError): pass


def bounded_json_size(value, limit, check):
    """No output or giant dumps allocation before a complete bounded count."""
    nodes = 0
    def visit(item, depth=0):
        nonlocal nodes
        check(); nodes += 1
        if nodes > 32768 or depth > 16: raise MetadataLimit()
        kind = type(item)
        if kind is str:
            if len(item) > 8192: raise MetadataLimit()
        elif kind is dict:
            if len(item) > 8192: raise MetadataLimit()
            for key, child in item.items():
                if type(key) is not str: raise MetadataLimit()
                visit(key, depth+1); visit(child, depth+1)
        elif kind in (list, tuple):
            if len(item) > 8192: raise MetadataLimit()
            for child in item: visit(child, depth+1)
        elif kind is int:
            if item.bit_length() > 128: raise MetadataLimit()
        elif kind is float:
            if not math.isfinite(item): raise MetadataLimit()
        elif item is not None and kind is not bool:
            raise MetadataLimit()
    if type(limit) is not int or limit < 1: raise MetadataLimit()
    visit(value)
    total = 1  # The exact existing writers append one newline.
    encoder = json.JSONEncoder(sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False)
    for chunk in encoder.iterencode(value):
        check(); total += len(chunk)  # ensure_ascii guarantees one byte/character.
        if total > limit: raise MetadataLimit()
    return total


class MetadataBudget:
    """Count whole serialized trees before any receipt or partial is opened."""
    def __init__(self, a, ctx):
        self.a, self.ctx, self.original = a, ctx, a.write_json
        self.used = 0; self.seen = set()
        self.allowed = {ctx.run_directory/'corpus-first-admission.json',
            ctx.run_directory/'receipts/download-alimeeting-eval.json',
            ctx.run_directory/'receipts/extract-Eval_Ali.tar.gz.json',
            ctx.root/'receipts/extract-Eval_Ali.tar.gz.json',
            ctx.root/'receipts/fixed-input.json', ctx.root/'receipts/stage-corpus.json'}
    def write(self, path, value, ctx, cleanup=False):
        path = Path(path)
        self.a.require(ctx.root == self.ctx.root and ctx.run_directory == self.ctx.run_directory and
                       ctx.deadline == self.ctx.deadline, 'immutable metadata context')
        self.a.require(path in self.allowed and path not in self.seen, 'fixed metadata output or duplicate')
        limit = min(16*1024, METADATA_PARTITIONS['actual_worker_json']-self.used)
        size = bounded_json_size(value, limit, lambda: ctx.check(cleanup=cleanup))
        self.used += size; self.seen.add(path)
        return self.original(path, value, ctx, cleanup=cleanup)


def bootstrap_check():
    work = float(os.environ['GUARD_WORK_END'])
    end = float(os.environ['GUARD_ABSOLUTE_END'])
    stop = Path(os.environ['GUARD_STOP_PATH'])
    if not math.isfinite(work) or not math.isfinite(end) or not 59.9 <= end-work <= 60.1:
        raise RuntimeError('guard work boundary')
    if time.monotonic() >= work or stop.exists() or stop.is_symlink():
        raise RuntimeError('guard STOP boundary')


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def load_acquire():
    """Execute the exact hashed helper bytes; no second pathname read race."""
    bootstrap_check()
    before = ACQUIRE.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size != ACQUIRE_BYTES:
        raise RuntimeError('acquisition helper size/type')
    fd = os.open(ACQUIRE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if identity(before) != identity(os.fstat(fd)):
            raise RuntimeError('acquisition helper open race')
        data = bytearray()
        while True:
            bootstrap_check()
            block = os.read(fd, 65536)
            if not block:
                break
            data.extend(block)
            if len(data) > ACQUIRE_BYTES:
                raise RuntimeError('acquisition helper grew')
        if identity(before) != identity(os.fstat(fd)) or identity(before) != identity(ACQUIRE.lstat()):
            raise RuntimeError('acquisition helper changed')
        if len(data) != ACQUIRE_BYTES or hashlib.sha256(data).hexdigest() != ACQUIRE_SHA:
            raise RuntimeError('acquisition helper hash')
    finally:
        os.close(fd)
    module = types.ModuleType('unchanged_corpus_acquire')
    module.__file__ = str(ACQUIRE)
    exec(compile(bytes(data), str(ACQUIRE), 'exec'), module.__dict__)
    return module


def read_json(a, ctx, path, expected_sha=None, expected_bytes=None):
    """Bounded, clocked metadata read with exact content hash where supplied."""
    path = ctx.checked(path)
    cap = 64*1024 if expected_bytes is None else expected_bytes
    a.require(type(cap) is int and 0 < cap <= 64*1024, 'admission JSON size cap')
    with a.bound_regular(path, ctx, cap) as stream:
        raw = stream.read(cap+1)
        a.require(len(raw) <= cap and (expected_bytes is None or len(raw) == expected_bytes),
                  'admission JSON exact bytes')
    if expected_sha is not None:
        a.require(hashlib.sha256(raw).hexdigest() == expected_sha, 'admission JSON pin mismatch')
    ctx.admission_read_bytes = getattr(ctx, 'admission_read_bytes', 0) + len(raw)
    a.require(ctx.admission_read_bytes <= 256*1024, 'cumulative admission JSON read cap')
    ctx.check()
    return json.loads(raw)


def checked_pin(a, pin, expected_path):
    a.require(type(pin) is dict and set(pin) == {'path', 'bytes', 'sha256'}, 'exact admission pin fields')
    a.require(pin['path'] == str(expected_path), 'admission pin target changed')
    a.require(type(pin['bytes']) is int and 0 < pin['bytes'] <= 64*1024 and
              type(pin['sha256']) is str and re.fullmatch('[0-9a-f]{64}', pin['sha256']),
              'invalid admission pin')
    return pin


def validate_model_success(a, seal, records, manifest):
    """Pure terminal qualification; it cannot turn an incomplete model run into success."""
    a.require(seal.get('schema') == 'corpus-first-model-success-v2' and seal.get('status') == 'PASS',
              'model seal incomplete')
    a.require(seal.get('original_tool_session_id') == 38367 and seal.get('run_directory') == str(MODEL_RUN),
              'wrong original model run')
    a.require(seal.get('model_spec_sha256') == MODEL_SPEC_SHA and
              seal.get('acquisition_helper_sha256') == ACQUIRE_SHA, 'model source binding')
    wait = seal.get('original_wait', {})
    a.require(wait.get('tool_session_id') == 38367 and wait.get('exit_code') == 0 and
              wait.get('original_handle_waited') is True and wait.get('cleanup') == 'CONFIRMED' and
              wait.get('unresolved_owned') == [] and wait.get('wait_chunk') == 'dcc1a7', 'original model wait not complete')
    a.require(type(seal.get('pins')) is dict and set(seal['pins']) == set(MODEL_RECEIPTS) and
              set(records) == set(MODEL_RECEIPTS), 'five exact model receipt pins required')
    for role, path in MODEL_RECEIPTS.items():
        checked_pin(a, seal['pins'][role], path)
    guard = records['guard']
    a.require(guard.get('status') == 'PASS' and guard.get('exit_code') == 0 and
              guard.get('original_handle_waited') is True and guard.get('cleanup') == 'CONFIRMED' and
              guard.get('unresolved_owned') == [] and guard.get('final_direct_child_pids') == [] and
              guard.get('errors') == [] and guard.get('final_source_pin_verified') is True and
              guard.get('spec_sha256') == MODEL_SPEC_SHA and guard.get('run_directory') == str(MODEL_RUN) and
              guard.get('runtime_files') == {PYTHON: PYTHON_SHA}, 'model guard not complete')
    caps = guard.get('caps', {})
    a.require(caps.get('absolute_seconds') == 1200 and caps.get('work_seconds') == 1140 and
              caps.get('owned_rss_bytes') == 256*2**20 and caps.get('scratch_bytes') == 7*2**30,
              'model guard caps changed')
    terminal = records['terminal']
    a.require(terminal.get('schema') == 'remaining-assets-terminal-v1' and
              terminal.get('status') == 'PASS_ASSET_ACQUISITION_ONLY' and
              terminal.get('original_tool_session') == 38367 and terminal.get('wait_chunk') == 'dcc1a7' and
              terminal.get('original_wait_and_cleanup') == 'CONFIRMED' and terminal.get('exit_code') == 0 and
              terminal.get('prior_before_and_after_equal') is True and
              terminal.get('model_loads') == 0 and terminal.get('inference_calls') == 0 and
              terminal.get('new_network_bytes') == 113695605 and
              type(terminal.get('pins')) is list and len(terminal['pins']) == 16,
              'completed six-asset terminal missing')
    a.require(seal['pins']['terminal']['sha256'] == TERMINAL_SHA, 'exact completed terminal pin')
    full = terminal.get('full_recheck', {})
    a.require(full.get('regular_files') == 85 and full.get('regular_bytes') == 776701550 and
              full.get('native_aliases') == 11 and full.get('native_membership_exact') is True,
              'complete acquisition inventory missing')
    continuation = records['continuation']
    a.require(continuation.get('schema') == 'closed-remaining-two-native-v1' and
              continuation.get('status') == 'VERIFIED' and continuation.get('phase') == 'VERIFIED' and
              continuation.get('prior_before_verified') is True and continuation.get('prior_after_verified') is True and
              continuation.get('prior_before') == continuation.get('prior_after') and
              continuation.get('new_verified_bytes') == 113695605 and
              continuation.get('new_network_bytes') == 113695605 and
              continuation.get('reused_expected_bytes') == 313636022 and
              continuation.get('old_partial_retained_bytes') == 96041420 and
              continuation.get('old_partial_used') is False and continuation.get('model_calls') == 0 and
              continuation.get('model_loads') == 0 and continuation.get('installs') == 0,
              'successful remaining-two acquisition missing')
    stage = records['stage']
    a.require(stage.get('status') == 'VERIFIED' and stage.get('stage') == 'model_runtime' and
              stage.get('model_calls') == 0 and stage.get('model_loads') == 0 and stage.get('installs') == 0,
              'model stage incomplete')
    models = records['models']
    expected = [{'path': p.get('path', p.get('destination')), 'bytes': p['bytes'], 'sha256': p['sha256']}
                for p in manifest['mandatory_runtime_files']]
    a.require(models.get('status') == 'VERIFIED' and models.get('models') == expected and
              models.get('model_calls') == 0 and models.get('model_loads') == 0 and
              type(models.get('native_inventory')) is list and bool(models['native_inventory']),
              'full mandatory model verification missing')


def verify_mandatory_models(a, manifest, ctx):
    """Full read-only rehash: 85 regulars, exact 11 aliases, no verify_models call."""
    records = []
    pins = {}
    for pin in manifest['mandatory_runtime_files']:
        relative = pin.get('path', pin.get('destination'))
        a.safe_name(relative)
        pins[relative] = {'bytes': pin['bytes'], 'sha256': pin['sha256']}
    for item in manifest['artifacts']:
        if item['stage'] == 'models' and item['id'] in manifest['archives']:
            pins[item['destination']] = {'bytes': item['bytes'], 'sha256': item['sha256']}
    native = ctx.root/'vendor/native-diarization/linux-x86_64-cpu'
    expected = ctx.model_records['models']['native_inventory']
    a.require(len(expected) == 86, 'exact native inventory size')
    by_path = {}
    expected_dirs = {''}
    for item in expected:
        relative = item['path']; a.safe_name(relative)
        a.require(relative not in by_path, 'duplicate native inventory')
        by_path[relative] = item
        parent = Path(relative).parent
        while str(parent) != '.':
            expected_dirs.add(str(parent)); parent = parent.parent
        if item['kind'] == 'regular':
            pins[str((native/relative).relative_to(ctx.root))] = {'bytes': item['bytes'], 'sha256': item['sha256']}
        else:
            a.require(item['kind'] == 'symlink', 'unexpected native inventory type')
    pending = [native]; seen = set(); directories = set(); aliases = []
    while pending:
        ctx.check(); directory = ctx.checked(pending.pop())
        directories.add(str(directory.relative_to(native)) if directory != native else '')
        a.require(directories <= expected_dirs, 'unrecorded native directory')
        with os.scandir(directory) as entries:
            for entry in entries:
                ctx.check(); path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    a.require(len(pending) <= len(expected_dirs), 'native traversal cap')
                    continue
                relative = str(path.relative_to(native)); item = by_path.get(relative)
                a.require(item is not None and relative not in seen, 'native membership changed')
                seen.add(relative)
                if item['kind'] == 'regular':
                    a.require(entry.is_file(follow_symlinks=False), 'native regular type changed')
                else:
                    before = path.lstat()
                    a.require(stat.S_ISLNK(before.st_mode), 'native alias type changed')
                    literal = os.readlink(path)
                    target = path.resolve(strict=True)
                    a.require(literal == item['literal_target'] and target == native/item['resolved_regular_path'] and
                              target.is_relative_to(native) and target.is_file() and not target.is_symlink() and
                              identity(before) == identity(path.lstat()), 'native alias changed')
                    a.require(by_path.get(item['resolved_regular_path'], {}).get('kind') == 'regular',
                              'native alias target absent from inventory')
                    aliases.append({'path': relative, 'literal_target': literal,
                                    'resolved_regular_path': item['resolved_regular_path']})
    a.require(seen == set(by_path) and directories == expected_dirs and len(aliases) == 11,
              'exact native membership incomplete')
    a.require(len(pins) == 85 and sum(x['bytes'] for x in pins.values()) == 776701550,
              'full acquisition regular scope changed')
    for relative in sorted(pins):
        ctx.check()
        records.append({'path': relative} | a.hash_file(ctx.checked(ctx.root/relative), ctx, pins[relative]))
    raw = json.dumps(records, sort_keys=True, separators=(',', ':')).encode()
    return {'regular_files': len(records), 'regular_bytes': sum(x['bytes'] for x in records),
            'regular_commitment_sha256': hashlib.sha256(raw).hexdigest(),
            'native_aliases': len(aliases), 'native_membership_exact': True,
            'model_loads': 0, 'model_calls': 0, 'writes_during_model_verification': 0}


def load_admission(a, ctx, manifest):
    index = read_json(a, ctx, ctx.run_directory/'index.json')
    adapter = index.get('closed_corpus_first_adapter', {})
    a.require(index.get('status') == 'GATED' and index.get('run_directory') == str(ctx.run_directory) and
              index.get('argv') == [PYTHON, '-I', '-B', '-S', str(HERE/'corpus_first.py')], 'closed corpus index')
    spec = read_json(a, ctx, HERE/'corpus_first.spec.json', index.get('spec_sha256'))
    gate = spec['corpus_first']
    a.require(spec.get('stage') == 'corpus_first' and spec.get('absolute_seconds') == 18000 and
              spec.get('work_seconds') == 17940 and gate.get('status') == 'MODEL_SEAL_BOUND',
              'corpus admission is pending')
    a.require(adapter.get('base_guard_sha256') == GUARD_SHA and
              adapter.get('spec_sha256') == index.get('spec_sha256') and
              adapter.get('original_stage_map') == {'synthetic': 300, 'model_runtime': 600, 'corpus': 1800, 'wheels_env': 600} and
              adapter.get('added_stage') == {'corpus_first': 18000}, 'closed adapter binding')
    a.require(type(gate.get('model_success_sha256')) is str and
              re.fullmatch('[0-9a-f]{64}', gate['model_success_sha256']) and
              adapter.get('model_success_sha256') == gate['model_success_sha256'], 'pending or mismatched model pin')
    a.require(spec['external_files'].get(str(ACQUIRE)) == ACQUIRE_SHA, 'unchanged acquire pin')
    seal = read_json(a, ctx, MODEL_SUCCESS, gate['model_success_sha256'])
    a.require(type(seal.get('pins')) is dict and set(seal['pins']) == set(MODEL_RECEIPTS), 'five model pins required')
    records = {}
    for role, path in MODEL_RECEIPTS.items():
        pin = checked_pin(a, seal['pins'][role], path)
        records[role] = read_json(a, ctx, path, pin['sha256'], pin['bytes'])
    validate_model_success(a, seal, records, manifest)
    checked = set()
    a.require(sum(p['bytes'] for p in records['terminal']['pins']) <= 128*1024,
              'cumulative terminal metadata pin cap')
    for pin in records['terminal']['pins']:
        path = ctx.checked(ctx.root/pin['path'])
        a.require(path not in checked and '..' not in Path(pin['path']).parts and
                  not Path(pin['path']).is_absolute(), 'terminal pin scope or duplicate')
        checked.add(path)
        a.require(type(pin['bytes']) is int and 0 <= pin['bytes'] <= 64*1024,
                  'terminal metadata pin size')
        a.hash_file(path, ctx, pin)
    ctx.model_records = records
    return gate


def dispatch(a, ctx, manifest, admission, run_command, verifier=None):
    """Network-capable command is unreachable until terminal gates and all model hashes pass."""
    ctx.check()
    a.require([item for item in manifest['artifacts'] if item['stage'] == 'corpus'] == [CORPUS],
              'single official corpus identity changed')
    gate = admission(a, ctx, manifest)
    models = (verifier or verify_mandatory_models)(a, manifest, ctx)
    # A first attempt never consumes a partial archive or overwrites a completed corpus.
    for relative in ('corpus', 'receipts/stage-corpus.json', 'receipts/fixed-input.json',
                     'receipts/extract-Eval_Ali.tar.gz.json', 'input', CLAIM):
        target = ctx.checked(ctx.root/relative)
        a.require(not target.exists() and not target.is_symlink(), 'corpus first-attempt output already exists')
    ctx.check()
    claim = ctx.parents(ctx.root/CLAIM)
    with a.exclusive(claim) as out:
        out.flush(); os.fsync(out.fileno())
    a.fsync_dir(claim.parent)
    a.write_json(ctx.run_directory/'corpus-first-admission.json', {
        'schema': 'corpus-first-admission-v1', 'status': 'VERIFIED_BEFORE_CORPUS_NETWORK',
        'model_success_sha256': gate['model_success_sha256'],
        'complete_acquisition_rehashed': models, 'network_bytes_before_corpus': ctx.network_bytes,
        'fixed_argv': FIXED_ARGV, 'automatic_retries': 0, 'range_requests': False,
        'deadline_renewals': 0, 'model_calls': 0}, ctx)
    a.require(ctx.network_bytes == 0, 'network activity before corpus admission')
    ctx.check()
    return run_command()


def main(a):
    a.require(sys.argv[1:] == [], 'no mutable corpus-first arguments')
    ctx = a.context_from_env(str(PREP))
    manifest = read_json(a, ctx, HERE.parent/'ASSET_MANIFEST.json', MANIFEST_SHA)
    validate_quotas(a, manifest)
    a.require(manifest == a.build_manifest(WORKSPACE), 'original full manifest equality')

    def exact_command():
        # Calling the unchanged parser and main retains its official manifest equality and all helper limits.
        sys.argv = list(FIXED_ARGV)
        return a.main()

    budget = MetadataBudget(a, ctx)
    a.write_json = budget.write
    return dispatch(a, ctx, manifest, load_admission, exact_command)


def entry():
    try:
        a = load_acquire()
    except BaseException:
        sys.stderr.write('{"status":"FAILED","failure":{"error_type":"CorpusBootstrapFailure"}}\n')
        return 1
    return a.cli_boundary(lambda: main(a))


if __name__ == '__main__':
    raise SystemExit(entry())
