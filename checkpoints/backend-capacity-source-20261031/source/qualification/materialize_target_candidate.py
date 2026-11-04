"""Materialize reviewed source-only candidates; never infer approval or launch them."""
import argparse
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qualification.target_runtime_observation import validate as validate_target
from audio_backend.native_owned_cell_store import RuntimeQualification

MODULE = 'audio_backend/native_owned_cell_store.py'
BASIS_SCHEMA = 'reviewed-isolated-target-materialization-v1'
BASIS_KEYS = {'schema', 'accepted_for_fresh_bounded_smoke', 'general_production_rollout_approved',
    'existing_store_migration_authorized', 'source_manifest_sha256', 'source_module_sha256',
    'target_sha256', 'audit_sha256', 'physical_result_sha256', 'candidate_tests_sha256'}
SOURCE_SUFFIXES = {'', '.py', '.java', '.json', '.md', '.patch', '.sql', '.toml', '.txt'}


def sha(raw):return hashlib.sha256(raw).hexdigest()


def no_symlinks(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():raise ValueError('symlink_refused')
    return path


def read(path, maximum=1024*1024):
    path = no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= maximum:
            raise ValueError('regular_file_bound')
        with os.fdopen(fd, 'rb', closefd=False) as stream:raw = stream.read(maximum+1)
        after = os.fstat(fd)
        signature = lambda st: (st.st_dev, st.st_ino, st.st_mode, st.st_nlink, st.st_uid,
            st.st_gid, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if signature(before) != signature(after) or len(raw) != before.st_size:raise ValueError('input_changed')
        return raw
    finally:os.close(fd)


def digest(value):
    if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('sha256_required')
    return value


def document(path, expected):
    raw = read(path)
    if sha(raw) != digest(expected):raise ValueError('input_hash_mismatch')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique), raw


def relative(name):
    if (type(name) is not str or not name or '\\' in name
            or PurePosixPath(name).is_absolute() or str(PurePosixPath(name)) != name
            or any(p in ('.', '..', '.git', '__pycache__') for p in PurePosixPath(name).parts)
            or PurePosixPath(name).suffix not in SOURCE_SUFFIXES):
        raise ValueError('source_only_relative_path_required')
    return name


def patch_profile(raw, observed, basis_hash):
    tree = ast.parse(raw)
    assignments = [n for n in tree.body if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name) and n.target.id == 'QUALIFIED_RUNTIMES']
    if len(assignments) != 1:raise ValueError('single_runtime_assignment_required')
    value = assignments[0].value
    if not isinstance(value, ast.Tuple) or len(value.elts) != 1:raise ValueError('single_runtime_required')
    call = value.elts[0]
    if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name)
            or call.func.id != 'RuntimeQualification' or call.args):
        raise ValueError('literal_runtime_required')
    values = {k.arg: ast.literal_eval(k.value) for k in call.keywords}
    if None in values or len(values) != len(call.keywords):raise ValueError('runtime_keywords_required')
    RuntimeQualification(**values)
    bounds = {'sector_bytes_max':65536, 'journal_headers_max':1, 'allocation_slack_bytes_max':2097152}
    if any(values.get(k) != v for k,v in bounds.items()):raise ValueError('reviewed_bounds_changed')
    changes = dict(observed, evidence_sha256=basis_hash)
    updated = dict(values, **changes)
    RuntimeQualification(**updated)
    lines = raw.splitlines(keepends=True)
    starts = [0]
    for line in lines:starts.append(starts[-1]+len(line))
    edits = []
    for keyword in call.keywords:
        if keyword.arg in changes:
            n = keyword.value
            edits.append((starts[n.lineno-1]+n.col_offset,
                starts[n.end_lineno-1]+n.end_col_offset, repr(changes[keyword.arg]).encode()))
    if len(edits) != len(changes):raise ValueError('runtime_identity_fields_missing')
    patched = raw
    for start,end,value in sorted(edits, reverse=True):patched = patched[:start]+value+patched[end:]
    # No whole-file templating: only reviewed identity literal byte ranges change.
    ast.parse(patched)
    return patched


def materialize(*, source, manifest, basis, basis_sha256, target, audit, physical, candidate_tests, output):
    source = no_symlinks(source)
    output = no_symlinks(output)
    if output.exists():raise ValueError('output_must_be_new')
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('output_must_be_separate')
    if not output.parent.is_dir():raise ValueError('output_parent_required')
    approval, _ = document(basis, basis_sha256)
    if (type(approval) is not dict or set(approval) != BASIS_KEYS or approval['schema'] != BASIS_SCHEMA
            or approval['accepted_for_fresh_bounded_smoke'] is not True
            or approval['general_production_rollout_approved'] is not False
            or approval['existing_store_migration_authorized'] is not False):
        raise ValueError('explicit_reviewed_basis_required')
    for name in BASIS_KEYS:
        if name.endswith('_sha256'):digest(approval[name])
    frozen, _ = document(target, approval['target_sha256'])
    observed = validate_target(frozen)
    audit_data, _ = document(audit, approval['audit_sha256'])
    result, _ = document(physical, approval['physical_result_sha256'])
    tests, _ = document(candidate_tests, approval['candidate_tests_sha256'])
    if (audit_data.get('status') != 'accepted_production_schema_saved_byte_evidence'
            or audit_data.get('production_activation_approved') is not False
            or any(audit_data.get(k) is not True for k in ('exact_recovery', 'evidence_unchanged',
                'source_runtime_inventory_matches', 'lifecycle_receipts_consistent'))
            or audit_data.get('evidence_inventory', {}).get('RESULT.json', {}).get('sha256')
                != approval['physical_result_sha256']):raise ValueError('audit_binding_mismatch')
    if (result.get('passed') is not True or result.get('production_profile_activated') is not False
            or result.get('power_loss_qualified') is not False
            or result.get('identity') != result.get('identity_after')
            or result.get('identity', {}).get('target_observation_sha256') != approval['target_sha256']
            or result.get('source_module_sha256') != approval['source_module_sha256']):
        raise ValueError('physical_binding_mismatch')
    identity = result['identity']
    actual = dict(identity['fingerprints'], **{k:identity[k] for k in ('platform', 'vfs',
        'filesystem_device', 'sqlite_source_id', 'sqlite_version', 'compile_options_sha256')})
    if actual != observed:raise ValueError('target_binding_mismatch')
    if (tests.get('schema') != 'target-candidate-regression-receipt-v1'
            or type(tests.get('tests_passed')) is not int or tests['tests_passed'] < 9
            or type(tests.get('exit_code')) is not int or tests['exit_code'] != 0
            or tests.get('production_profile_activated') is not False
            or any(tests.get(k) != approval[k] for k in ('target_sha256', 'audit_sha256',
                'physical_result_sha256', 'source_module_sha256', 'source_manifest_sha256'))):
        raise ValueError('candidate_tests_binding_mismatch')
    listing, _ = document(manifest, approval['source_manifest_sha256'])
    if listing.get('schema') != 'standalone-source-checkpoint-v1':raise ValueError('source_manifest_schema')
    entries = listing.get('files')
    if type(entries) is not list or not 1 <= len(entries) <= 4096:raise ValueError('source_manifest_bound')
    files = {}; total = 0
    for entry in entries:
        name = relative(entry['path'])
        if name in files:raise ValueError('duplicate_source_path')
        raw = read(source/name)
        total += len(raw)
        if total > 64*1024**2:raise ValueError('source_total_bound')
        if (type(entry['size']) is not int or len(raw) != entry['size']
                or sha(raw) != digest(entry['sha256'])):raise ValueError('source_manifest_mismatch')
        if 'sha' in entry and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() != entry['sha']:
            raise ValueError('source_git_blob_mismatch')
        files[name] = raw
    if MODULE not in files or sha(files[MODULE]) != approval['source_module_sha256']:
        raise ValueError('source_module_mismatch')
    physical_sources = result.get('source_manifest')
    if type(physical_sources) is not dict or not 1 <= len(physical_sources) <= 4096:
        raise ValueError('physical_source_manifest_required')
    for name, expected in physical_sources.items():
        relative(name)
        if name not in files or sha(files[name]) != digest(expected):
            raise ValueError('physical_source_manifest_mismatch')
    if MODULE not in physical_sources:raise ValueError('physical_module_binding_required')
    original = files[MODULE]
    patched = patch_profile(original, observed, basis_sha256)
    patch = ''.join(difflib.unified_diff(original.decode().splitlines(True), patched.decode().splitlines(True),
        fromfile='original/'+MODULE, tofile='candidate/'+MODULE)).encode()
    # All validation precedes output creation. Partial I/O failure has no completion receipt.
    output.mkdir(mode=0o700)
    candidate = output/'source'; candidate.mkdir(mode=0o700)
    for name, raw in files.items():
        destination = candidate/name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with destination.open('xb') as stream:stream.write(patched if name == MODULE else raw)
    with (output/'candidate.patch').open('xb') as stream:stream.write(patch)
    for name, raw in files.items():
        if read(source/name) != raw:raise ValueError('source_changed_during_copy')
        if read(candidate/name) != (patched if name == MODULE else raw):raise ValueError('candidate_copy_mismatch')
    receipt = dict(schema='isolated-target-materialization-receipt-v1', completed=True,
        production_profile_activated=False, service_started=False, reviewed_basis_sha256=basis_sha256,
        source_manifest_sha256=approval['source_manifest_sha256'], source_module_sha256=sha(original),
        candidate_module_sha256=sha(patched), patch_sha256=sha(patch), files=len(files),
        candidate_files={name:sha(patched if name == MODULE else raw) for name,raw in files.items()})
    with (output/'RECEIPT.json').open('x') as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2); stream.write('\n');stream.flush();os.fsync(stream.fileno())
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'manifest', 'basis', 'target', 'audit', 'physical', 'candidate-tests', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--basis-sha256', required=True)
    args = vars(parser.parse_args())
    receipt = materialize(**args)
    print(json.dumps({k:v for k,v in receipt.items() if k != 'candidate_files'},sort_keys=True))


if __name__ == '__main__':main()
