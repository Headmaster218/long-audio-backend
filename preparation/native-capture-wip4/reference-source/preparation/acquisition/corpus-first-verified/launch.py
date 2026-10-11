"""One closed 18,000-second first-corpus stage; ownership helpers unchanged."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
PREP = HERE.parent.parent
GUARD_SHA = '3934eb162fc61ba06f4c9e740016e0da04f7eba62e41d5d5e77bbc399971b305'
OLD_STAGES = {'synthetic': 300, 'model_runtime': 600, 'corpus': 1800, 'wheels_env': 600}
STAGE = 'corpus_first'
ABSOLUTE_SECONDS = 18000
WORK_SECONDS = 17940
BASE_SPEC = PREP/'acquisition/continuation/model_runtime.timeout30.spec.json'
BASE_SPEC_SHA = '12bcf30b77f693f35925bb0d3ec18e5ef61d3a61e64f7db7ce75ff821753256b'
HANDLE = PREP/'acquisition/remaining-native/TERMINAL_QUALIFICATION.json'
HANDLE_SHA = '4b935333183d3799245b5d2bb0bf368fddb5c01965e5707364a98dbeb3de0fc9'
SOURCE_CLOSURE_SHA = '326bb27209360f673df5633efa0ef95d8e65651cf7135cbe5b83cd0806bcc444'
RUNTIME_CLOSURE_SHA = '25ba33551ecd751ad1b4b0b3662907bfb6d41e3c162e21a950f93a623f3d084f'
BASE_EXTERNAL_CLOSURE_SHA = 'a52ba349ae17a6a7d9b6281fe128cd6da1596f3c4f707b99b8019da4e0779175'
PYTHON = '/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python3.12'
ENTRY = HERE/'corpus_first.py'
SPEC = HERE/'corpus_first.spec.json'
MODEL_SUCCESS = HERE/'MODEL_SUCCESS.json'
FREEZE = HERE/'CORPUS_FIRST_FREEZE.json'
TESTS = HERE/'test_corpus_first.py'
TEST_IDS = HERE/'TEST_IDS.json'


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def extend(g):
    g.require(g.STAGES == OLD_STAGES, 'prior_stage_map_changed')
    g.STAGES = {**OLD_STAGES, STAGE: ABSOLUTE_SECONDS}


def validate_spec(g, spec, own_sha, entry_sha):
    """Pure validation: no reads, imports, model access, or network."""
    fields = {'schema', 'stage', 'absolute_seconds', 'work_seconds', 'address_space_cap_bytes',
              'rss_cap_bytes', 'scratch_cap_bytes', 'argv', 'cwd', 'guard_sha256', 'source_root',
              'source_files', 'runtime_files', 'external_files', 'corpus_first'}
    g.require(type(spec) is dict and set(spec) == fields, 'closed_spec_fields')
    g.require(spec['schema'] == 2 and spec['stage'] == STAGE, 'only_closed_corpus_first_stage')
    g.require(type(spec['absolute_seconds']) is int and spec['absolute_seconds'] == ABSOLUTE_SECONDS,
              'closed_absolute_budget')
    g.require(type(spec['work_seconds']) is int and spec['work_seconds'] == WORK_SECONDS,
              'closed_work_budget')
    g.require(spec['guard_sha256'] == GUARD_SHA, 'base_guard_pin')
    g.require(spec['address_space_cap_bytes'] == 2**30 and spec['rss_cap_bytes'] == 256*2**20 and
              spec['scratch_cap_bytes'] == 7*2**30, 'unchanged_resource_caps')
    g.require(spec['argv'] == [PYTHON, '-I', '-B', '-S', str(ENTRY)], 'closed_entrypoint_and_prefix')
    g.require(spec['cwd'] == str(PREP) and
              spec['source_root'] == str(PREP.parent/'reconstruction-rc3-dev3'), 'closed_roots')
    g.require(canonical_sha(spec['source_files']) == SOURCE_CLOSURE_SHA, 'source_closure')
    g.require(spec['runtime_files'] == {spec['argv'][0]: 'fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7'} and
              canonical_sha(spec['runtime_files']) == RUNTIME_CLOSURE_SHA, 'singleton_runtime_equals_argv_zero')
    gate = spec['corpus_first']
    g.require(type(gate) is dict and set(gate) == {'status', 'model_success_sha256',
              'prepared_freeze_sha256', 'tests_sha256', 'test_ids_sha256', 'completed_setup_pins'},
              'closed_gate_fields')
    g.require(gate['status'] == 'MODEL_SEAL_BOUND', 'pending_model_seal')
    g.require(all(type(gate[k]) is str and re.fullmatch('[0-9a-f]{64}', gate[k])
                  for k in gate if k not in ('status', 'completed_setup_pins')), 'pending_or_invalid_gate_pin')
    setup = gate['completed_setup_pins']
    g.require(type(setup) is dict and len(setup) <= 8, 'completed_setup_pin_count')
    g.require(all(type(k) is str and Path(k).is_absolute() and '..' not in Path(k).parts and
                  Path(k).is_relative_to(PREP) and type(v) is str and re.fullmatch('[0-9a-f]{64}', v)
                  for k, v in setup.items()), 'completed_setup_pins_invalid')
    extra = {str(HERE/'launch.py'): own_sha, str(ENTRY): entry_sha,
             str(BASE_SPEC): BASE_SPEC_SHA, str(HANDLE): HANDLE_SHA,
             str(MODEL_SUCCESS): gate['model_success_sha256'],
             str(FREEZE): gate['prepared_freeze_sha256'],
             str(TESTS): gate['tests_sha256'], str(TEST_IDS): gate['test_ids_sha256']}
    g.require(not (set(extra) & set(setup)), 'completed_setup_pin_overlap')
    extra.update(setup)
    external = spec['external_files']
    g.require(type(external) is dict and all(external.get(k) == v for k, v in extra.items()),
              'closed_extra_pins')
    base = {k: v for k, v in external.items() if k not in extra}
    g.require(canonical_sha(base) == BASE_EXTERNAL_CLOSURE_SHA, 'inherited_external_closure')


def load_guard():
    path = PREP/'guard/acquisition_guard.py'
    if path.is_symlink() or path.stat().st_size != 32648 or hashlib.sha256(path.read_bytes()).hexdigest() != GUARD_SHA:
        raise RuntimeError('unchanged acquisition guard required')
    module_spec = importlib.util.spec_from_file_location('corpus_first_base_guard', path)
    g = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(g)
    return g


def install_metadata_bounds(g, worker):
    original_write = g.write_json
    written = set()
    def bounded_guard_write(path, value):
        path = Path(path)
        g.require(path.name in ('index.json', 'receipt.json') and path not in written,
                  'fixed_guard_metadata_destination')
        limit = (12 if path.name == 'index.json' else 19)*1024
        try:
            worker.bounded_json_size(value, limit, lambda: g.clock_check(g.ABSOLUTE_END))
        except worker.MetadataLimit:
            fallback = {'schema': 1, 'status': 'FAIL_CLOSED',
                'failure': {'error_type': 'MetadataBudgetExceeded'},
                'cleanup': value.get('cleanup', 'UNKNOWN'),
                'original_handle_waited': value.get('original_handle_waited') is True,
                'full_receipt_published': False}
            worker.bounded_json_size(fallback, 1024, lambda: g.clock_check(g.ABSOLUTE_END))
            written.add(path); original_write(path, fallback)
            raise
        written.add(path)
        return original_write(path, value)
    g.write_json = bounded_guard_write


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--spec', required=True, type=Path)
    p.add_argument('--spec-sha256', required=True)
    args = p.parse_args()
    g = load_guard()
    g.ABSOLUTE_END = g.START + ABSOLUTE_SECONDS
    g.require(args.spec == SPEC and args.spec.resolve() == SPEC, 'one_closed_spec_path')
    raw = g.read_regular(SPEC, g.MAX_SPEC_BYTES, g.ABSOLUTE_END)
    g.require(hashlib.sha256(raw).hexdigest() == args.spec_sha256, 'spec_pin')
    spec = json.loads(raw)
    own_sha = g.digest(HERE/'launch.py')
    entry_sha = g.digest(ENTRY)
    extend(g)
    validate_spec(g, spec, own_sha, entry_sha)
    network = g.platform_network(os.environ, STAGE)
    clean = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
             'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', **network}
    os.environ.clear()
    os.environ.update(clean)

    worker_spec = importlib.util.spec_from_file_location('corpus_metadata_bounds', ENTRY)
    worker = importlib.util.module_from_spec(worker_spec); worker_spec.loader.exec_module(worker)
    install_metadata_bounds(g, worker)

    class CorpusFirstGuard(g.Guard):
        def __init__(self, config, sha):
            super().__init__(config, sha)
            self.receipt['closed_corpus_first_adapter'] = {
                'path': str(HERE/'launch.py'), 'sha256': own_sha, 'entry_sha256': entry_sha,
                'base_guard_sha256': GUARD_SHA, 'spec_sha256': sha,
                'original_stage_map': OLD_STAGES, 'added_stage': {STAGE: ABSOLUTE_SECONDS},
                'work_seconds': WORK_SECONDS, 'source_closure_sha256': SOURCE_CLOSURE_SHA,
                'runtime_closure_sha256': RUNTIME_CLOSURE_SHA,
                'inherited_external_closure_sha256': BASE_EXTERNAL_CLOSURE_SHA,
                'model_success_sha256': config['corpus_first']['model_success_sha256'],
                'completed_setup_pins': config['corpus_first']['completed_setup_pins'],
                'ownership_helpers': 'unchanged inherited functions', 'automatic_retries': 0,
                'deadline_renewals': 0, 'metadata_partitions_bytes': worker.METADATA_PARTITIONS}

    g.Guard = CorpusFirstGuard
    return g.main()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({'status': 'REFUSED_BEFORE_DISPATCH',
                          'failure': 'closed corpus-first adapter rejected'}), file=sys.stderr)
        raise SystemExit(2) from None
