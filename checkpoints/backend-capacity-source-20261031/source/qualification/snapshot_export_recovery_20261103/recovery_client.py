"""Pinned explicit GET-only recovery client; no inference or service lifecycle."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snapshot_http as host


def prepare(spec_path, spec_sha256, output):
    import json
    import os
    spec_path = Path(spec_path)
    host.need(spec_path.is_file() and not spec_path.is_symlink()
              and host.digest(spec_path) == spec_sha256, 'client_spec_pin')
    spec = json.loads(spec_path.read_bytes())
    root = host.verify_source(spec['source_root'], spec['source_manifest'], spec['source_manifest_sha256'])
    host.need(Path(__file__).resolve() == root/'qualification/snapshot_export_recovery_20261103/recovery_client.py',
              'client_source_location')
    roots = [Path(p).resolve() for p in spec['protected_roots']]
    host.need(root in roots and Path(spec['original_root']).resolve() in roots, 'client_protected_roots')
    output = Path(output).absolute()
    host.need(output.parent == output.parent.resolve(strict=True) and not os.path.lexists(output)
              and all(not output.is_relative_to(p) for p in roots)
              and not output.is_relative_to(Path(spec['snapshot']).resolve().parent), 'client_output_separation')
    guard = host.ProtectedPaths([*roots, Path(spec['snapshot']).resolve()]).install()
    host.validate_binding(spec)
    sys.path.insert(0, str(root))
    return spec, output, guard


def main(argv=None):
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--spec-sha256', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    spec, output, guard = prepare(args.spec, args.spec_sha256, args.output)
    # Do not import the product until source identity and mutation fences exist.
    from audio_backend import recorder_export_recovery as recovery
    host.need(Path(recovery.__file__).resolve() == Path(spec['source_root']).resolve()/'audio_backend/recorder_export_recovery.py',
              'client_module_identity')
    result = recovery.main(['--job-dir',spec['job_dir'],'--api',spec['api'],
        '--session-id',spec['session_id'],'--capture-id',spec['capture_id'],
        '--output-dir',str(output),'--exclusive-writer','--attempts','2','--deadline-seconds','90'])
    host.need(guard.denied == 0, 'client_protected_write_denied')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
