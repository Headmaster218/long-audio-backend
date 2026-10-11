"""Source-only fixture guard: one 300/240/60 window with bounded receipt writes."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def main():
    p = argparse.ArgumentParser(); p.add_argument('--spec', required=True, type=Path)
    p.add_argument('--spec-sha256', required=True); args = p.parse_args()
    launcher = module('fixture_closed_launcher', HERE/'launch.py')
    g = launcher.load_guard()
    g.require(args.spec == HERE/'synthetic.spec.json' and args.spec.resolve() == args.spec,
              'fixed_synthetic_spec_path')
    raw = g.read_regular(args.spec, 16384, g.ABSOLUTE_END)
    g.require(hashlib.sha256(raw).hexdigest() == args.spec_sha256, 'synthetic_spec_pin')
    spec = json.loads(raw)
    g.require(spec['stage'] == 'synthetic' and spec['absolute_seconds'] == 300 and
              spec['work_seconds'] == 240 and g.STAGES == launcher.OLD_STAGES,
              'unchanged_synthetic_stage_and_budget')
    g.require(spec['runtime_files'] == {launcher.PYTHON:
              'fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7'} and
              spec['argv'] == [launcher.PYTHON, '-I', '-B', '-S', str(HERE/'test_corpus_first.py')],
              'exact_synthetic_runtime_and_entry')
    for path in (HERE/'launch.py', HERE/'corpus_first.py', Path(__file__).resolve()):
        g.require(spec['external_files'].get(str(path)) == g.digest(path), 'synthetic_adapter_pin')
    worker = module('fixture_metadata_bounds', HERE/'corpus_first.py')
    launcher.install_metadata_bounds(g, worker)
    return g.main()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('{"status":"REFUSED_BEFORE_DISPATCH","failure":"SyntheticAdapterRefused"}')
        raise SystemExit(2) from None
