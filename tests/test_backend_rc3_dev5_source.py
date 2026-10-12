"""Fresh rc3.dev5 source closure; externally frozen and stdlib only.

Set RC3_DEV5_INDEX and RC3_DEV5_SOURCE_SEAL to reviewed external files.
No production module or historical pytest gate is imported or executed.
"""
from __future__ import annotations

from functools import lru_cache
from contextlib import contextmanager
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
BASE = '762509bddf4c3f1604c446d485034be8882de6f7'
BASE_PARENT = '2c841d84a9b3204e510628f4aef17fe90334e76c'
BASE_TREE = '8ce9f6fc4a70cb018175c742e9ead04c136c4148'
VERSION = '0.2.0rc3.dev5'
POLICY = 'docs/FRAGMENT_STAGING_POLICY.json'
MANIFEST = 'docs/BACKEND_RC3_DEV5_SOURCE.json'
GATE = 'tests/test_backend_rc3_dev5_source.py'
HISTORICAL = 'tests/test_backend_rc3_dev4_source.py'
GUARD = 'scripts/source_staging_guard.py'
MODULE = 'audio_backend/moss_handy_jobs.py'
ADDED = frozenset(('audio_backend/moss_handy_http.py', 'audio_backend/moss_handy_jobs.py', 'audio_backend/native_execution_permit.py', 'docs/BACKEND_RC3_DEV5_SOURCE.json', 'docs/MOSS_HANDY_JOBS.md', 'tests/test_backend_rc3_dev5_source.py', 'tests/test_moss_handy_jobs.py', 'tests/test_moss_handy_jobs_api.py', 'tests/test_native_execution_permit.py'))
CHANGED = frozenset(('audio_backend/__init__.py', 'audio_backend/__main__.py', 'audio_backend/app.py', 'audio_backend/fragment_http.py', 'audio_backend/lifecycle.py', 'audio_backend/moss_handy_controller.py', 'audio_backend/native_owned_support_cache.py', 'audio_backend/resources.py', 'audio_backend/settings.py', 'audio_backend/worker.py', 'docs/FRAGMENT_STAGING_POLICY.json', 'docs/MOSS_HANDY_EXPERIMENTAL.md', 'docs/NATIVE_OWNED_SUPPORT_CAPTURE.md', 'pyproject.toml', 'tests/test_backend_rc3_dev4_source.py', 'tests/test_moss_handy_controller.py', 'tests/test_native_owned_support_cache.py'))
DELTA = ADDED | CHANGED
MAX_FILES, MAX_TOTAL_BYTES, MAX_FILE_BYTES = 331, 4_300_000, 250_000
DESIGN_SHA256 = '765e7f1b4c53069518ff64f67f02d2c1452ec09749ca51177a98f5952a30ac11'
ADDENDUM_SHA256 = 'ae52e30843dc8015491bdb6a4296550e594b8440c7a398cde9e9a6ab6ba9c4f4'
PURPOSE = 'Fresh rc3.dev5 default-OFF volatile Handy MOSS jobs source; bounded model-free evidence only when separately reported; no restored qualification'
SCOPE = 'Explicit default-OFF legacy CPU/native-support/overlap-none Handy MOSS jobs; one slot, zero backlog, bounded source/response leases and shared primary/optional permit; no fragment provider coexistence'
QUALIFICATION = 'Source-only preparation is not test execution; model-free fixtures and tiny stdlib fake children require separately reported frozen-source authorization/results; no acoustic, live native service concurrency, long optional recording, full-suite, Windows/Vulkan or production qualification'
OFF_EXCEPTION = 'Noncreating ownership check even when OFF; missing descriptor primitives allow only bounded symlink/reparse-free namespace absence and refuse existing/uncertain ownership; enabled jobs require Linux primitives; configured fragment factory initialize/store-schema hook executes once under InstanceLock after authoritative ownership fence, preserving SQL body but changing startup timing; no-factory ordinary legacy adds no provider call or SQL'
BOUNDS = {'accepted_keys_per_epoch': 64, 'backlog': 0, 'service_threads': 1,
          'support_frames': 320000, 'support_pcm_bytes': 640000,
          'dispatch_pin_bytes': 768 * 1024, 'response_leases': 2,
          'shared_response_bytes': 1536 * 1024, 'retained_result_bytes': 1024 * 1024,
          'raw_bytes': 128 * 1024, 'result_bytes': 256 * 1024,
          'ttl_seconds_from_acceptance': 900, 'scratch_bytes': 2 * 1024 * 1024,
          'mutation_bytes': 1024, 'query_bytes': 1024, 'query_fields': 4,
          'list_page_rows': 20, 'list_page_bytes': 32 * 1024,
          'detail_bytes': 16 * 1024, 'wrapper_working_reserve_bytes': 16 * 1024 * 1024}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def git(*args, index=None, data=None, root=ROOT):
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1')
    if index is not None:
        env['GIT_INDEX_FILE'] = str(index)
    return subprocess.run(['git', *args], cwd=root, env=env, input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True, timeout=20).stdout


def safe_name(name):
    parts = PurePosixPath(name).parts
    require(bool(parts) and name == str(PurePosixPath(name)) and
            not name.startswith('/') and '..' not in parts and
            '.git' not in parts and '\\' not in name, 'Unsafe source path')


def read_paths(root, names):
    require(len(names) == len(set(names)) <= MAX_FILES, 'Duplicate or excess source paths')
    files, modes, total = {}, {}, 0
    for name in sorted(names):
        safe_name(name)
        path = root
        for component in PurePosixPath(name).parts:
            path = path / component
            require(not path.is_symlink(), 'Source path contains a symlink')
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                'Source path must be a regular, unlinked file')
        require(info.st_size <= MAX_FILE_BYTES, 'Source file exceeds byte cap')
        total += info.st_size
        require(total <= MAX_TOTAL_BYTES, 'Source total exceeds byte cap')
        data = path.read_bytes()
        require(len(data) == info.st_size, 'Source changed during read')
        files[name] = data
        modes[name] = '100755' if info.st_mode & 0o111 else '100644'
    return files, modes


def read_git_snapshot(ref, index=None):
    rows = git('ls-files', '--stage', '-z', index=index) if ref == ':' else git('ls-tree', '-r', '-z', ref)
    entries = {}
    for row in rows.split(b'\0'):
        if not row:
            continue
        header, raw_name = row.split(b'\t', 1)
        name = raw_name.decode('utf-8')
        safe_name(name)
        fields = header.decode('ascii').split()
        if ref == ':':
            mode, oid, stage = fields
            require(stage == '0', 'Unmerged source index')
        else:
            mode, kind, oid = fields
            require(kind == 'blob', 'Non-blob source entry')
        require(mode in ('100644', '100755') and name not in entries, 'Nonregular or duplicate Git entry')
        entries[name] = (mode, oid)
    require(len(entries) <= MAX_FILES, 'Git snapshot exceeds file cap')
    oids = sorted({oid for _, oid in entries.values()})
    request = ''.join(oid + '\n' for oid in oids).encode('ascii')
    headers = git('cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)', data=request).splitlines()
    require(len(headers) == len(oids), 'Incomplete Git object size response')
    sizes = {}
    for oid, header in zip(oids, headers):
        got, kind, size = header.decode('ascii').split()
        require(got == oid and kind == 'blob', 'Unexpected Git object')
        size = int(size)
        require(0 <= size <= MAX_FILE_BYTES, 'Git object exceeds file cap')
        sizes[oid] = size
    require(sum(sizes[oid] for _, oid in entries.values()) <= MAX_TOTAL_BYTES, 'Git snapshot exceeds byte cap')
    output = git('cat-file', '--batch', data=request)
    blobs, offset = {}, 0
    for oid in oids:
        end = output.index(b'\n', offset)
        require(output[offset:end] == f'{oid} blob {sizes[oid]}'.encode(), 'Unexpected Git blob header')
        start, finish = end + 1, end + 1 + sizes[oid]
        data = output[start:finish]
        require(output[finish:finish + 1] == b'\n', 'Incomplete Git blob response')
        identity = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        require(identity == oid, 'Git blob content does not match object identity')
        blobs[oid] = data
        offset = finish + 1
    require(offset == len(output), 'Extra Git blob output')
    return ({name: blobs[oid] for name, (_, oid) in entries.items()},
            {name: mode for name, (mode, _) in entries.items()})


@lru_cache(maxsize=1)
def baseline():
    require(git('rev-parse', BASE + '^{tree}').decode().strip() == BASE_TREE, 'Published base tree differs')
    headers = git('cat-file', 'commit', BASE).split(b'\n\n', 1)[0].splitlines()
    require([line[7:].decode() for line in headers if line.startswith(b'parent ')] == [BASE_PARENT],
            'Published base sole parent differs')
    files, modes = read_git_snapshot(BASE)
    require(len(files) == 322 and sum(map(len, files.values())) == 3_865_952, 'Published base size differs')
    return MappingProxyType(files), MappingProxyType(modes)


def historical_adapter(original):
    doc_end = original.index(b'"""', 3) + 3
    start = original.index(b'\ndef current_snapshot():') + 1
    end = original.index(b'\n\nclass SourceClosureTests', start)
    docstring = b'"""Immutable rc3.dev4 source proof against published 762509bd; stdlib only.\n\nThe current_snapshot adapter checks the exact published tree and sole parent.\nOriginal verifier/mutation bodies are preserved. This focused historical source\ncheck does not restore an earlier full-suite or acoustic qualification.\nThe separate rc3.dev5 gate owns current worktree/index/external-seal checks.\n"""'
    snapshot = b"def current_snapshot():\n    check_isolation()\n    revision = '762509bddf4c3f1604c446d485034be8882de6f7'\n    headers = git('cat-file', 'commit', revision).split(b'\\n\\n', 1)[0].splitlines()\n    require([line[5:].decode() for line in headers if line.startswith(b'tree ')] ==\n            ['8ce9f6fc4a70cb018175c742e9ead04c136c4148'], 'Historical rc3.dev4 tree differs')\n    require([line[7:].decode() for line in headers if line.startswith(b'parent ')] ==\n            [BASE], 'Historical rc3.dev4 sole parent differs')\n    git('merge-base', '--is-ancestor', revision, 'HEAD')\n    files, modes = read_git_snapshot(revision)\n    return files, dict(files), modes, dict(modes), canonical(expected_seal(files))\n"
    return docstring + original[doc_end:start] + snapshot + original[end:]


def expected_policy(base):
    old = json.loads(base[POLICY])
    return dict(old, purpose=PURPOSE, maximum_files=MAX_FILES, maximum_total_bytes=MAX_TOTAL_BYTES,
                allowed_files=sorted(set(old['allowed_files']) | ADDED))


def expected_manifest(files, base, base_modes):
    base_inventory = [{'path': name, **pin(base[name]), 'mode': base_modes[name]} for name in sorted(base)]
    return {
        'schema': 'reviewed-backend-rc3-dev5-source-v1', 'version': VERSION,
        'base_commit': BASE, 'base_tree': BASE_TREE,
        'base_files': 322, 'base_bytes': 3_865_952,
        'base_source_inventory_sha256': hashlib.sha256(canonical(base_inventory)).hexdigest(),
        'scope': SCOPE, 'qualification': QUALIFICATION,
        'design_package_sha256': DESIGN_SHA256, 'startup_addendum_sha256': ADDENDUM_SHA256,
        'composed_inventory': {'added': sorted(ADDED), 'changed': sorted(CHANGED), 'total': 26},
        'default_off_startup_exception': OFF_EXCEPTION, 'runtime_bounds': BOUNDS,
        'source_budget': {'maximum_files': MAX_FILES, 'maximum_total_bytes': MAX_TOTAL_BYTES,
                          'maximum_file_bytes': MAX_FILE_BYTES},
        'historical_proof': 'Immutable published rc3.dev4 current_snapshot/docstring adapter only; original verifier and mutation bodies preserved',
        'external_seal': 'Manifest excludes itself; independent external seal pins both this manifest and the source gate',
        'files': [{'path': name, **pin(files[name]), 'mode': '100644',
                   **({'supersedes': pin(base[name])} if name in base else {'base_absent': True})}
                  for name in sorted(DELTA - {MANIFEST})],
    }


def expected_seal(files):
    return {'schema': 'rc3-dev5-external-source-seal-v1', 'base_commit': BASE, 'base_tree': BASE_TREE,
            'gate': {'path': GATE, **pin(files[GATE])},
            'manifest': {'path': MANIFEST, **pin(files[MANIFEST])}}


@lru_cache(maxsize=1)
def privacy_guard():
    base, _ = baseline()
    require((ROOT / GUARD).read_bytes() == base[GUARD], 'Privacy guard differs from published source')
    spec = importlib.util.spec_from_file_location('_rc3_dev5_unchanged_privacy_guard', ROOT / GUARD)
    require(spec is not None and spec.loader is not None, 'Cannot load unchanged privacy guard')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.MAX_BYTES == MAX_FILE_BYTES, 'Privacy per-file cap differs')
    return module



def syntax(data):
    try:
        return ast.parse(data.decode('utf-8'))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise ValueError('Source must be valid Python text') from exc


def shape(node):
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def member(tree, *names):
    for name in names:
        nodes = [node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name]
        require(len(nodes) == 1, 'Missing or repeated contract definition')
        tree = nodes[0]
    return tree


def exact_definition(data, *names):
    node = member(syntax(data), *names)
    return ast.get_source_segment(data.decode('utf-8'), node)


def sql_calls(data):
    # Compare the complete call expression, including interpolation, parameter
    # bindings and ordering, rather than merely searching for SQL keywords.
    nodes = [node for node in ast.walk(syntax(data)) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr in ('execute', 'executemany', 'executescript')]
    return [shape(node) for node in sorted(nodes, key=lambda node: (node.lineno, node.col_offset))]


def verify_sql_and_unchanged_definitions(files, base):
    for name in sorted(CHANGED):
        if name.startswith('audio_backend/') and name.endswith('.py'):
            require(sql_calls(files[name]) == sql_calls(base[name]), 'Original SQL call/body/order changed')
    for name in sorted(ADDED):
        if name.startswith('audio_backend/') and name.endswith('.py'):
            require(not sql_calls(files[name]), 'Optional modules may not add SQL calls')
            require(not any(isinstance(node, (ast.Import, ast.ImportFrom)) and
                            ('sqlite3' in [alias.name.split('.')[0] for alias in node.names] or
                             isinstance(node, ast.ImportFrom) and (node.module or '').split('.')[0] == 'sqlite3')
                            for node in ast.walk(syntax(files[name]))), 'Optional modules may not import SQLite')
    for name, definitions in (
        ('audio_backend/resources.py', (('tree_rss',), ('kill_tree',), ('InstanceLock',), ('AnalyzerProcess',))),
        ('audio_backend/settings.py', (('Settings', 'inference_config'),)),
        ('audio_backend/lifecycle.py', (('init_schema',), ('unlink_owned',), ('cleanup_pending',),
                                        ('cancel_descendants',), ('recover_cancelled_streams',))),
    ):
        for names in definitions:
            require(exact_definition(files[name], *names) == exact_definition(base[name], *names),
                    'Unapproved primary/resource/cleanup definition changed')
    old = member(syntax(base['audio_backend/lifecycle.py']), 'delete_session')
    new = member(syntax(files['audio_backend/lifecycle.py']), 'delete_session')
    require([shape(node) for node in new.body[:2]] == [shape(node) for node in old.body[:2]],
            'Primary deletion transaction changed')



def verify_resource_seams(files, base):
    name = 'audio_backend/resources.py'
    before, after = syntax(base[name]), syntax(files[name])
    old_check, new_check = member(before, 'check_resources'), member(after, 'check_resources')
    require(shape(old_check.args) == shape(new_check.args) and
            [shape(node) for node in new_check.body[2:]] == [shape(node) for node in old_check.body],
            'Primary resource thresholds/body changed')
    observer = ast.parse("permit=getattr(settings, '_native_execution_permit', None)\n"
                         "if permit is not None: permit.observe_primary()\n").body
    require([shape(node) for node in new_check.body[:2]] == [shape(node) for node in observer],
            'Resource observer seam changed')
    old_run, new_run = member(before, 'run_bounded'), member(after, '_run_bounded')
    require(shape(old_run.args) == shape(new_run.args), 'Bounded process signature changed')
    # Remove only the reviewed no-op-when-OFF observation insertions; all old
    # creation, resource, exception and cleanup statements must remain exact.
    body = list(new_run.body)
    require(len(body) == len(old_run.body) and isinstance(body[-1], ast.Try),
            'Bounded process body changed')
    outer = body[-1]
    expected_bind = ast.parse("permit=getattr(settings, '_native_execution_permit', None)\n"
                              "if permit is not None: permit.bind_primary(proc)\n").body
    require([shape(node) for node in outer.body[1:3]] == [shape(node) for node in expected_bind],
            'Bounded original-handle binding differs')
    outer.body[1:3] = []
    inner = outer.body[-1]
    require(isinstance(inner, ast.Try), 'Bounded cleanup structure changed')
    finish = ast.parse("if permit is not None: permit.finish_primary(proc)\n").body
    require([shape(node) for node in inner.finalbody] == [shape(node) for node in finish],
            'Bounded final ownership observation differs')
    inner.finalbody = []
    require([shape(node) for node in body] == [shape(node) for node in old_run.body],
            'Original bounded subprocess or cleanup body changed')
    wrapper = ast.parse("""def run_bounded(args, settings, *, timeout=600, stdout_path=None, stop=None):
    permit=getattr(settings, '_native_execution_permit', None)
    if permit is None:
        return _run_bounded(args, settings, timeout=timeout, stdout_path=stdout_path, stop=stop)
    with permit.primary_span('probe', stop):
        return _run_bounded(args, settings, timeout=timeout, stdout_path=stdout_path, stop=stop)
""").body[0]
    require(shape(member(after, 'run_bounded')) == shape(wrapper), 'Primary span wrapper differs')


def verify_settings(files, base):
    name = 'audio_backend/settings.py'
    old, new = (member(syntax(data[name]), 'Settings') for data in (base, files))
    old_fields = {node.target.id: shape(node) for node in old.body if isinstance(node, ast.AnnAssign)}
    new_fields = {node.target.id: shape(node) for node in new.body if isinstance(node, ast.AnnAssign)}
    require(set(new_fields) - set(old_fields) == {'moss_jobs_enabled', 'moss_jobs_model_path'},
            'Exactly two public jobs settings are permitted')
    require(all(new_fields.get(key) == value for key, value in old_fields.items()),
            'Existing setting/default changed')
    fields = {node.target.id: node for node in new.body if isinstance(node, ast.AnnAssign)}
    require(isinstance(fields['moss_jobs_enabled'].value, ast.Constant) and
            fields['moss_jobs_enabled'].value.value is False, 'Jobs must default OFF')
    require(isinstance(fields['moss_jobs_model_path'].value, ast.Constant) and
            fields['moss_jobs_model_path'].value.value is None, 'Jobs model must have no default')
    old_post, new_post = member(old, '__post_init__'), member(new, '__post_init__')
    require([shape(node) for node in new_post.body[:2] + new_post.body[5:]] ==
            [shape(node) for node in old_post.body], 'Original settings validation changed')
    require(all(isinstance(node, ast.If) for node in new_post.body[2:5]),
            'Jobs settings validation must be three explicit bounded checks')
    cli = files['audio_backend/__main__.py'].decode('utf-8')
    old_cli = base['audio_backend/__main__.py'].decode('utf-8')
    def flags(text):
        return {node.value for node in ast.walk(ast.parse(text))
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith('--')}
    require(flags(cli) - flags(old_cli) == {'--moss-jobs', '--moss-jobs-model'},
            'Unexpected new launcher flag')


def verify_fragment_seam(files, base):
    name = 'audio_backend/fragment_http.py'
    old_tree, new_tree = syntax(base[name]), syntax(files[name])
    old = member(old_tree, 'FragmentHTTP')
    new = member(new_tree, 'FragmentHTTP')
    require([shape(node) for node in old_tree.body if node is not old] ==
            [shape(node) for node in new_tree.body if node is not new],
            'FragmentHTTP module changed outside the factory seam')
    old_methods = {node.name: node for node in old.body if isinstance(node, ast.FunctionDef)}
    new_methods = {node.name: node for node in new.body if isinstance(node, ast.FunctionDef)}
    require(set(old_methods) <= set(new_methods), 'Original fragment method missing')
    require(set(new_methods) - set(old_methods) == {'initialize_factory_once'},
            'Unexpected fragment factory seam definition')
    require([shape(node) for node in new.body if not isinstance(node, ast.FunctionDef)] ==
            [shape(node) for node in old.body if not isinstance(node, ast.FunctionDef)],
            'Fragment class changed outside initializer methods')
    expected_initializer = ast.parse("def initialize_factory_once(self):\n    with self._factory_initialization_lock:\n        if self.factory_initialization_state == 'returned':\n            return\n        if self.factory_initialization_state == 'failed':\n            raise self._factory_initialization_error\n        if self.factory_initialization_state == 'started':\n            raise RuntimeError('Factory initialization is already in progress')\n        self.factory_initialization_state = 'started'\n        try:\n            if self.factory is not None and hasattr(self.factory, 'initialize'):\n                self.factory.initialize(self.store)\n        except BaseException as exc:\n            self._factory_initialization_error = exc\n            self.factory_initialization_state = 'failed'\n            raise\n        self.factory_initialization_state = 'returned'").body[0]
    require(shape(new_methods['initialize_factory_once']) == shape(expected_initializer),
            'Initialize-once state/error/hook contract changed')
    for key, node in old_methods.items():
        if key not in ('__init__', 'process_next'):
            require(exact_definition(files[name], 'FragmentHTTP', key) ==
                    exact_definition(base[name], 'FragmentHTTP', key),
                    'Fragment processing/cleanup definition changed')
    old_init, new_init = old_methods['__init__'], new_methods['__init__']
    require(not old_init.args.kwonlyargs and len(new_init.args.kwonlyargs) == 1 and
            new_init.args.kwonlyargs[0].arg == 'defer_factory_initialization' and
            len(new_init.args.kw_defaults) == 1 and
            isinstance(new_init.args.kw_defaults[0], ast.Constant) and
            new_init.args.kw_defaults[0].value is False,
            'Direct FragmentHTTP constructor default changed')
    original_args = ast.arguments(posonlyargs=new_init.args.posonlyargs, args=new_init.args.args,
                                  vararg=new_init.args.vararg, kwonlyargs=[], kw_defaults=[],
                                  kwarg=new_init.args.kwarg, defaults=new_init.args.defaults)
    require(shape(original_args) == shape(old_init.args), 'Original fragment constructor signature changed')
    preserved = [shape(node) for node in old_init.body[:-1]]
    require([shape(node) for node in new_init.body[:len(preserved)]] == preserved,
            'Original fragment constructor SQL/fields moved or changed')
    expected_tail = ast.parse("self.factory_initialization_state = 'pending'\n"
                              "self._factory_initialization_error = None\n"
                              "self._factory_initialization_lock = threading.RLock()\n"
                              "if not defer_factory_initialization:\n    self.initialize_factory_once()\n").body
    require([shape(node) for node in new_init.body[len(preserved):]] ==
            [shape(node) for node in expected_tail], 'Unexpected constructor initialization seam')
    # Only a leading readiness guard may precede the unchanged processing body.
    old_process, new_process = old_methods['process_next'], new_methods['process_next']
    require(shape(old_process.args) == shape(new_process.args), 'Fragment processing signature changed')
    offset = len(new_process.body) - len(old_process.body)
    require(offset == 1 and [shape(node) for node in new_process.body[offset:]] ==
            [shape(node) for node in old_process.body], 'Fragment processing/cleanup body changed')
    if offset:
        guard = new_process.body[0]
        require(isinstance(guard, ast.If) and not guard.orelse and len(guard.body) == 1 and
                isinstance(guard.body[0], ast.Return) and isinstance(guard.body[0].value, ast.Constant) and
                guard.body[0].value.value is False, 'Unexpected fragment readiness guard')
        expected_guard = ast.parse("if self.factory_initialization_state != 'returned':\n    return False\n").body[0]
        require(shape(guard) == shape(expected_guard), 'Fragment readiness state changed')



def bounded_literal(node, depth=0):
    # Evaluate only tiny literal arithmetic/data in source. Never compile/import
    # product code and never resolve a callable, attribute or arbitrary name.
    require(depth <= 12, 'Contract literal nesting exceeds cap')
    if isinstance(node, ast.Constant):
        require(type(node.value) in (int, float, str, bool, type(None)), 'Invalid contract literal')
        if isinstance(node.value, str):
            require(len(node.value) <= 256, 'Contract string exceeds cap')
        elif type(node.value) in (int, float):
            require(abs(node.value) <= 2**63, 'Contract number exceeds cap')
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult, ast.Pow)):
        left, right = bounded_literal(node.left, depth + 1), bounded_literal(node.right, depth + 1)
        require(type(left) in (int, float) and type(right) in (int, float), 'Non-numeric contract expression')
        if isinstance(node.op, ast.Pow):
            require(type(right) is int and 0 <= right <= 32 and abs(left) <= 65536,
                    'Contract exponent exceeds cap')
            value = left ** right
        else:
            value = left + right if isinstance(node.op, ast.Add) else left * right
        require(abs(value) <= 2**63, 'Contract expression exceeds cap')
        return value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'int' and len(node.args) == 1 and not node.keywords:
        value = bounded_literal(node.args[0], depth + 1)
        require(type(value) in (int, float), 'Non-numeric int literal')
        return int(value)
    if isinstance(node, ast.Dict):
        require(len(node.keys) <= 16 and all(key is not None for key in node.keys), 'Contract dictionary exceeds cap')
        pairs = [(bounded_literal(key, depth + 1), bounded_literal(value, depth + 1))
                 for key, value in zip(node.keys, node.values)]
        require(all(type(key) is str for key, _ in pairs) and len(dict(pairs)) == len(pairs),
                'Invalid contract dictionary key')
        return dict(pairs)
    raise ValueError('Nonliteral contract expression')


def source_constants(data, names):
    values = {}
    for node in syntax(data).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        pairs = [(target, node.value)]
        if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
            require(len(target.elts) == len(node.value.elts), 'Unbalanced constant assignment')
            pairs = zip(target.elts, node.value.elts)
        for key, value in pairs:
            if isinstance(key, ast.Name) and key.id in names:
                require(key.id not in values, 'Repeated bounded constant')
                values[key.id] = bounded_literal(value)
    require(set(values) == set(names), 'Missing explicit bounded source constant')
    return values


def verify_runtime_bounds(files):
    contracts = {
        'audio_backend/moss_handy_jobs.py': {
            'NAMESPACE': 'moss-handy-jobs', 'SLOT': 'slot', 'MAX_ACCEPTED': 64, 'TTL_SECONDS': 900,
            'MAX_LEDGER_BYTES': 32768, 'MAX_RETAINED_BYTES': 1048576,
            'MAX_RESULT_BYTES': 262144, 'MAX_RAW_BYTES': 131072,
            'MAX_DETAIL_BYTES': 16384, 'MAX_LIST_BYTES': 32768},
        'audio_backend/moss_handy_http.py': {
            'MAX_BODY_BYTES': 1024, 'MAX_QUERY_BYTES': 1024, 'RECEIVE_SECONDS': 2.,
            'PRIVATE': {'Cache-Control':'private, no-store', 'Pragma':'no-cache',
                        'X-Content-Type-Options':'nosniff'}},
        'audio_backend/native_execution_permit.py': {
            'MOSS_ENVELOPE': 3758096384, 'WRAPPER_RESERVE': 16777216,
            'HOST_RESERVE': 2147483648, 'DISK_RESERVE': 5905580032,
            'MAX_ROWS': 4096, 'MAX_IDENTITIES': 128, 'SAMPLE_SECONDS': .250},
        'audio_backend/native_owned_support_cache.py': {
            'MAX_RETAINED_BYTES': 4194304, 'MAX_SUPPORTS': 4, 'MAX_MARKERS': 64,
            'TTL_SECONDS': 900, 'MAX_PCM_BYTES': 640000, 'MAX_DISPATCH_BYTES': 786432,
            'MAX_READ_LEASES': 2, 'MAX_READ_BYTES': 1572864,
            'MAX_ROWS': 1024, 'MAX_SQL_BYTES': 262144, 'MAX_VM_STEPS': 150000,
            'MAX_SOURCE_BYTES': 41943040, 'READ_SECONDS': .5, 'LOCK_SECONDS': .005,
            'READ_RESERVATIONS': {'list': 786432, 'detail': 131072, 'audio': 786432,
                'jobs_result': 786432, 'jobs_raw': 262144, 'jobs_detail': 131072,
                'jobs_list': 262144, 'jobs_mutation': 131072}},
    }
    for name, expected in contracts.items():
        require(source_constants(files[name], expected) == expected, 'Runtime bound/default namespace changed')



def verify_http_and_startup(files, base):
    name = 'audio_backend/app.py'
    old_tree, new_tree = syntax(base[name]), syntax(files[name])
    old_app, new_app = member(old_tree, 'create_app'), member(new_tree, 'create_app')
    old_life, new_life = member(old_app, 'lifespan'), member(new_app, 'lifespan')
    require(shape(old_app.args) == shape(new_app.args), 'Application construction signature changed')
    require(len(new_life.body) == 3 and isinstance(new_life.body[0], ast.Nonlocal) and
            new_life.body[0].names == ['jobs'] and
            shape(new_life.body[1]) == shape(ast.parse('lock.acquire()').body[0]) and
            isinstance(new_life.body[2], ast.Try), 'InstanceLock must precede authoritative startup')
    startup = new_life.body[2]
    fence = ast.parse('ownership_fence(settings.data_dir)\nfragments.initialize_factory_once()').body
    require([shape(node) for node in startup.body[:2]] == [shape(node) for node in fence],
            'Authoritative ownership fence must precede initializer and competing work')
    enabled = [node for node in startup.body if isinstance(node, ast.If) and
               shape(node.test) == shape(ast.parse('settings.moss_jobs_enabled', mode='eval').body)]
    require(len(enabled) == 1 and startup.body.index(enabled[0]) == 3 and not enabled[0].orelse,
            'Optional service must be constructed only in enabled post-fence branch')
    old_startup = old_life.body[1]
    require(isinstance(old_startup, ast.Try), 'Published lifespan structure differs')
    require([shape(node) for node in startup.body[2:] if node is not enabled[0]] ==
            [shape(node) for node in old_startup.body],
            'Original startup cleanup/recovery/Worker order changed')
    constructors = [node for node in ast.walk(new_app) if isinstance(node, ast.Call) and
                    isinstance(node.func, ast.Name) and node.func.id == 'FragmentHTTP']
    expected_constructor = ast.parse('FragmentHTTP(store,settings,fragment_provider_factory,defer_factory_initialization=True)', mode='eval').body
    require(len(constructors) == 1 and shape(constructors[0]) == shape(expected_constructor),
            'Application must opt into deferred factory initialization even when OFF')
    old_prefix = old_app.body[:old_app.body.index(old_life)]
    new_prefix = new_app.body[:new_app.body.index(new_life)]
    preliminary = ast.parse("from .moss_handy_jobs import ownership_fence\n"
                            "ownership_fence(settings.data_dir)\n"
                            "if settings.moss_jobs_enabled and fragment_provider_factory is not None:\n"
                            "    raise ValueError('MOSS jobs does not support fragment provider coexistence')\n").body
    require([shape(node) for node in new_prefix[1:4]] == [shape(node) for node in preliminary] and
            shape(new_prefix[-1]) == shape(ast.parse('jobs=None').body[0]),
            'Preliminary fence/configuration startup seam changed')
    preserved_prefix = [shape(node) for node in new_prefix[:1] + new_prefix[4:-1]]
    old_fragment = next(node for node in old_prefix if isinstance(node, ast.Assign) and
                        isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and
                        node.value.func.id == 'FragmentHTTP')
    new_fragment = next(node for node in new_prefix if isinstance(node, ast.Assign) and
                        node.value is constructors[0])
    preserved_prefix[preserved_prefix.index(shape(new_fragment))] = shape(old_fragment)
    require(preserved_prefix == [shape(node) for node in old_prefix],
            'Unapproved construction-time call or SQL/provider ordering change')
    initializers = [node for node in ast.walk(new_app) if isinstance(node, ast.Call) and
                    isinstance(node.func, ast.Attribute) and node.func.attr == 'initialize_factory_once']
    require(len(initializers) == 1 and initializers[0] is startup.body[1].value,
            'Deferred initializer may run only after authoritative startup fence')
    makers = [node for node in ast.walk(new_app) if isinstance(node, ast.Call) and
              isinstance(node.func, ast.Name) and node.func.id in ('MossJobs', 'NativeExecutionPermit')]
    require(len(makers) == 2 and all(any(node is child for child in ast.walk(enabled[0])) for node in makers),
            'Optional owner or permit constructed outside enabled startup')
    identity = ast.parse('_CPU_INITIALIZE = _CPUFactory.initialize').body[0]
    require(sum(shape(node) == shape(identity) for node in new_tree.body) == 1,
            'Original shipped initializer identity must be captured at module scope')
    for names in (('probe',), ('_assign_native_read_lease',), ('create_app', 'auth')):
        require(exact_definition(files[name], *names) == exact_definition(base[name], *names),
                'Existing probe/auth/response lease handoff changed')
    last = ast.parse('register_routes(app, lambda:jobs, _assign_native_read_lease)\n'
                     'app.add_middleware(_NativeSupportReadLifetime)\nreturn app').body
    require([shape(node) for node in new_app.body[-3:]] == [shape(node) for node in last],
            'Shared outer response lifetime must enclose route/auth errors')
    # The old outer lease release/error behavior remains exact, with only the
    # route-family predicate extended to include jobs paths.
    old_outer = member(old_tree, '_NativeSupportReadLifetime', '__call__')
    new_outer = member(new_tree, '_NativeSupportReadLifetime', '__call__')
    require([shape(node) for node in old_outer.body[1:]] == [shape(node) for node in new_outer.body[1:]],
            'Outer ASGI release/error behavior changed')
    predicate = ast.parse("private=scope.get('type')=='http' and any(part in scope.get('path','') for part in ('/native-owned-supports','/moss-jobs'))").body[0]
    require(shape(new_outer.body[0]) == shape(predicate), 'Jobs private response coverage changed')
    # These small source-only AST pins independently reject resealed changes to
    # the reviewed closed parsers, bounded receiver, response/raw projection and
    # failure/lock-release proof. They do not execute any application code.
    pins = {'audio_backend/moss_handy_http.py': [(('_unique',), '8f0e0c25bb0e9cdd66633aead8235c29f8463685733b385b9fbfa276c933bf94'), (('parse_body',), '87bf4662b2b8b8e5f72f33ccee9362dfc4dd09fceec086e04fde07aade51ab8a'), (('parse_query',), 'fea4f581481df1a4b00c272b423ef7efdb32b94f20a0ddbbf8c7ee0e51aa2ffe'), (('receive_body',), '29b4740030e2633d08f2f03856570b81c7565c700a0e7cace11603394ec25ccc'), (('register_routes',), 'c142615678e5a57a5f3cb06f96eeb46741b04f7de8c13ae218bb45dd1e31b884')], 'audio_backend/app.py': [(('create_app', 'lifespan'), '288e5f55aab8b43a7494ee0e4af4eac3b80f3eea56c2739ced5933f6d94ad8e9')], 'audio_backend/worker.py': [(('Worker', 'loop'), '6bb2a542c0f0d68376ff8b318ebb10a39415ffaf5e06f7509670d154d1717544'), (('Worker', '_native_process_factory'), '2652ec0803b0864082a1d5dd904b412e6e41b7ea2e34fef1a9d67dd45255c5a2'), (('Worker', 'process'), 'aec0aff52949482f7b186c058d7f2614e46db0bb41b6b9234451a37cef27d5b5')], 'audio_backend/moss_handy_jobs.py': [(('_ServiceGate', 'check'), 'ba9b829f079f9ce7997e745805ee26eef1514c0434acf1e00280bdf8839fc04f'), (('ownership_fence',), '1a82dbccebe0cf0e7c4730221e5a223ab334f329c94cd67d08e0ef51248aae0a'), (('_descriptor_support',), 'acac9dffc6520f92201327bbc1f8e7c2936cae4724e63b17065953258f6a1ade'), (('_absent_namespace_fence',), '7922fb73a63fe2bbc26d8e3ee9782d1468f4045a7e4d9d9f844befa7cee9d411'), (('_private_directory_fd',), '502cb1e59ab59d6064281aaa42a4ed12d83a83612c430a008ea2c09e3df8ddab'), (('create_namespace',), '41e62945c8a1ee282ebbf9728a047ddd40744d9011d24b704e11ef9050b852f3')], 'audio_backend/moss_handy_controller.py': [(('MossController', '__init__'), '7b00f6197544585e558c607e42d3f1d037f4e70188eb5178c0ea2494fa7f2cb0')]}
    pins['audio_backend/settings.py'] = [(('Settings', '__post_init__'), '7079fc6b665accb8af4db36b8662facdcda51364688c5c5fe941280c60abb132')]
    for path, definitions in pins.items():
        for names, expected in definitions:
            node = member(syntax(files[path]), *names)
            digest = hashlib.sha256(shape(node).encode('utf-8')).hexdigest()
            require(digest == expected, 'Reviewed HTTP/startup/source admission contract changed')
    routes = []
    for node in member(syntax(files['audio_backend/moss_handy_http.py']), 'register_routes').body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                    require(len(decorator.args) == 1 and isinstance(decorator.args[0], ast.Constant),
                            'Nonliteral jobs route')
                    routes.append((decorator.func.attr, decorator.args[0].value))
    require(routes == [
        ('post', '/sessions/{sid}/native-owned-supports/{cid}/moss-jobs'),
        ('get', '/sessions/{sid}/moss-jobs'),
        ('get', '/sessions/{sid}/moss-jobs/{job_id}'),
        ('get', '/sessions/{sid}/moss-jobs/{job_id}/result'),
        ('get', '/sessions/{sid}/moss-jobs/{job_id}/raw'),
        ('post', '/sessions/{sid}/moss-jobs/{job_id}/cancel')], 'Exact six-route inventory changed')


def verify_source_contracts(files, base):
    require(len(ADDED) == 9 and len(CHANGED) == 17 and len(DELTA) == 26,
            'Composed inventory must have exactly 26 paths')
    verify_sql_and_unchanged_definitions(files, base)
    verify_resource_seams(files, base)
    verify_settings(files, base)
    verify_fragment_seam(files, base)
    verify_runtime_bounds(files)
    verify_http_and_startup(files, base)
    # Immutable profiles, providers, planners, storage and owned cleanup are
    # outside CHANGED and already required byte-for-byte equal to published base.
    for name in ('audio_backend/native_execution_permit.py', 'audio_backend/moss_handy_jobs.py',
                 'audio_backend/moss_handy_http.py'):
        syntax(files[name])


def verify(files, index, modes, index_modes, seal, base=None, base_modes=None):
    if base is None or base_modes is None:
        base, base_modes = baseline()
    require(files == index, 'Working source and explicit candidate index disagree')
    require(set(files) == set(base) | ADDED and len(files) == MAX_FILES, 'Source inventory differs')
    require({name for name in files if files[name] != base.get(name)} == DELTA, 'Source delta differs')
    require(all(files[name] == base[name] for name in set(base) - CHANGED), 'Unapproved base source changed')
    require(modes == index_modes == {**base_modes, **{name: '100644' for name in ADDED}}, 'Source modes differ')
    require(files[HISTORICAL] == historical_adapter(base[HISTORICAL]), 'Historical proof adapter differs')
    verify_source_contracts(files, base)
    for name in ('pyproject.toml', 'audio_backend/__init__.py'):
        require(base[name].count(b'0.2.0rc3.dev4') == 1, 'Base version target not unique')
        require(files[name] == base[name].replace(b'0.2.0rc3.dev4', VERSION.encode()), 'Version-only replacement differs')
    policy = expected_policy(base)
    require(files[POLICY] == canonical(policy), 'Policy differs from exact budget/allowlist-only update')
    require(files[MANIFEST] == canonical(expected_manifest(files, base, base_modes)), 'Manifest is noncanonical or stale')
    require(seal == canonical(expected_seal(files)), 'External gate/manifest seal differs')
    require(all(len(data) <= MAX_FILE_BYTES for data in files.values()) and
            sum(map(len, files.values())) <= MAX_TOTAL_BYTES, 'Source byte budget exceeded')
    require(privacy_guard().validate(files, policy, require_manifest=False)['findings'] == 0, 'Privacy findings')


def external_file(variable):
    value = os.environ.get(variable)
    require(bool(value), 'Missing explicit external ' + variable)
    path = Path(value)
    require(path.is_absolute(), 'External proof paths must be absolute')
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'External proof path contains a symlink')
    path = path.resolve(strict=True)
    require(not path.is_relative_to(ROOT), 'External proof must be outside source checkout')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'External proof must be a regular unlinked file')
    require(info.st_size <= 100_000, 'External proof exceeds cap')
    return path


def check_isolation():
    require(not any(os.environ.get(key) for key in (
        'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
        'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
        'GIT_CONFIG', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS')), 'Ambient Git override prohibited')
    common = Path(git('rev-parse', '--git-common-dir').decode().strip())
    common = (ROOT / common).resolve() if not common.is_absolute() else common.resolve()
    require((ROOT / '.git').is_dir() and not (ROOT / '.git').is_symlink() and common == ROOT / '.git',
            'Checkout is not a self-contained ordinary clone')
    require(not (common / 'objects/info/alternates').exists() and not (common / 'info/grafts').exists(),
            'Git alternates or grafts prohibited')
    require(not git('for-each-ref', 'refs/replace').strip(), 'Git replacement refs prohibited')
    require(git('rev-parse', '--is-shallow-repository').strip() == b'false', 'Shallow source history prohibited')
    for path in (common / 'objects').rglob('*'):
        require(not path.is_symlink(), 'Git object symlink prohibited')
        if path.is_file():
            require(path.stat().st_nlink == 1, 'Git object hardlink prohibited')
    require(not (common / 'MERGE_HEAD').exists(), 'Merge in progress')
    git('merge-base', '--is-ancestor', BASE, 'HEAD')


def current_snapshot():
    check_isolation()
    index_path, seal_path = external_file('RC3_DEV5_INDEX'), external_file('RC3_DEV5_SOURCE_SEAL')
    require(index_path != seal_path, 'Index and seal must be separate files')
    names = git('ls-files', '--cached', '--others', '--exclude-standard', '-z', index=index_path)
    names = [name.decode('utf-8') for name in names.split(b'\0') if name]
    files, modes = read_paths(ROOT, names)
    index, index_modes = read_git_snapshot(':', index_path)
    head = git('rev-parse', 'HEAD').decode().strip()
    if head != BASE:
        headers = git('cat-file', 'commit', head).split(b'\n\n', 1)[0].splitlines()
        require([line[7:].decode() for line in headers if line.startswith(b'parent ')] == [BASE],
                'Fresh candidate must have exactly the published base as parent')
        committed, committed_modes = read_git_snapshot(head)
        require((committed, committed_modes) == (files, modes), 'Committed candidate differs from source')
    return files, index, modes, index_modes, seal_path.read_bytes()


class SourceClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current = current_snapshot()
        cls.base, cls.base_modes = baseline()
        verify(*cls.current, cls.base, cls.base_modes)

    def test_exact_fresh_source(self):
        verify(*self.current, self.base, self.base_modes)

    def test_historical_adapter_preserves_verifier_and_mutation_bodies(self):
        original, adapted = self.base[HISTORICAL], self.current[0][HISTORICAL]
        self.assertEqual(adapted, historical_adapter(original))
        original_doc_end = original.index(b'"""', 3) + 3
        adapted_doc_end = adapted.index(b'"""', 3) + 3
        self.assertEqual(original[original_doc_end:original.index(b'def current_snapshot():')],
                         adapted[adapted_doc_end:adapted.index(b'def current_snapshot():')])
        anchor = b'\n\nclass SourceClosureTests'
        self.assertEqual(original[original.index(anchor):], adapted[adapted.index(anchor):])

    def test_privacy_exclusions_and_limit_are_unchanged(self):
        old, new = json.loads(self.base[POLICY]), json.loads(self.current[0][POLICY])
        self.assertEqual(old['excluded_categories'], new['excluded_categories'])
        self.assertEqual(self.base[GUARD], self.current[0][GUARD])
        self.assertEqual(privacy_guard().MAX_BYTES, MAX_FILE_BYTES)

    def reject_mutation(self, mutation):
        files, index, modes, index_modes = map(dict, self.current[:4])
        seal = self.current[4]
        regenerate = (mutation not in ('contract_docs', 'contract_test') and
                      mutation.startswith(('version_', 'policy_', 'historical_', 'privacy_', 'contract_', 'immutable_')))
        if mutation == 'missing':
            del files['README.md']
        elif mutation == 'extra':
            files['docs/unreviewed.md'] = b'synthetic\n'
        elif mutation in ('module', 'contract_test', 'contract_docs', 'gate', 'runner', 'cli', 'runtime', 'history', 'guard'):
            name = {'module': MODULE, 'contract_test': 'tests/test_moss_handy_controller.py',
                    'contract_docs': 'docs/MOSS_HANDY_EXPERIMENTAL.md', 'gate': GATE,
                    'runner': 'audio_backend/moss_handy_runner.py',
                    'cli': 'audio_backend/__main__.py',
                    'runtime': 'audio_backend/worker.py', 'history': 'docs/PUBLIC_HISTORY_MAP.json', 'guard': GUARD}[mutation]
            files[name] += b'\n'
        elif mutation.startswith('immutable_'):
            name = {
                'immutable_storage': 'audio_backend/storage.py',
                'immutable_native': 'audio_backend/native_runner.py',
                'immutable_provider': 'audio_backend/fragment_cpu_provider.py',
                'immutable_cleanup': 'audio_backend/qwen_candidate_runner.py',
                'immutable_profile': 'audio_backend/moss_handy_profile.py',
                'immutable_model': 'audio_backend/profiles/moss_handy_q8_0.json',
                'immutable_manifest': 'docs/BACKEND_RC3_DEV4_SOURCE.json',
                'immutable_guard': GUARD,
            }[mutation]
            files[name] += b'\n'
        elif mutation.startswith('contract_'):
            if mutation == 'contract_sql':
                name = 'audio_backend/lifecycle.py'
                self.assertIn(b'BEGIN IMMEDIATE', files[name])
                files[name] = files[name].replace(b'BEGIN IMMEDIATE', b'BEGIN DEFERRED', 1)
            elif mutation == 'contract_added_sql':
                files[MODULE] += b'\ndef unapproved_sql(db):\n    db.execute("SELECT 1")\n'
            elif mutation == 'contract_cleanup':
                name = 'audio_backend/resources.py'
                self.assertIn(b'def kill_tree(proc):', files[name])
                files[name] = files[name].replace(b'def kill_tree(proc):', b'def kill_tree(proc):\n    return', 1)
            elif mutation == 'contract_fragment':
                name = 'audio_backend/fragment_http.py'
                self.assertIn(b'def recover(self):', files[name])
                files[name] = files[name].replace(b'def recover(self):', b'def recover(self):\n        return', 1)
            elif mutation == 'contract_dependency':
                name = 'pyproject.toml'
                self.assertIn(b'psutil>=6,<8', files[name])
                files[name] = files[name].replace(b'psutil>=6,<8', b'psutil>=6,<9', 1)
            elif mutation == 'contract_off':
                name = 'audio_backend/settings.py'
                tree = syntax(files[name])
                node = next(node for node in member(tree, 'Settings').body
                            if isinstance(node, ast.AnnAssign) and node.target.id == 'moss_jobs_enabled')
                fragment = ast.get_source_segment(files[name].decode(), node).encode()
                self.assertIn(b'False', fragment)
                files[name] = files[name].replace(fragment, fragment.replace(b'False', b'True'), 1)
            elif mutation == 'contract_factory_default':
                name = 'audio_backend/fragment_http.py'
                node = member(syntax(files[name]), 'FragmentHTTP', '__init__')
                line = files[name].splitlines(keepends=True)[node.lineno - 1]
                self.assertIn(b'False', line)
                files[name] = files[name].replace(line, line.replace(b'False', b'True'), 1)
            elif mutation == 'contract_inference_config':
                name = 'audio_backend/settings.py'
                self.assertIn(b'def inference_config(self):', files[name])
                files[name] = files[name].replace(b'def inference_config(self):',
                    b'def inference_config(self):\n        self.moss_jobs_enabled = True', 1)
            elif mutation == 'contract_bounds':
                self.assertIn(b'MAX_ACCEPTED = 64', files[MODULE])
                files[MODULE] = files[MODULE].replace(b'MAX_ACCEPTED = 64', b'MAX_ACCEPTED = 65', 1)
            elif mutation == 'contract_http_bounds':
                name = 'audio_backend/moss_handy_http.py'
                self.assertIn(b'1024, 1024, 2.', files[name])
                files[name] = files[name].replace(b'1024, 1024, 2.', b'1025, 1024, 2.', 1)
            elif mutation == 'contract_http_schema':
                name = 'audio_backend/moss_handy_http.py'
                self.assertIn(b"fields = {'jobs_epoch'}", files[name])
                files[name] = files[name].replace(b"fields = {'jobs_epoch'}", b"fields = {'jobs_epoch', 'path'}", 1)
            elif mutation == 'contract_http_raw':
                name = 'audio_backend/moss_handy_http.py'
                self.assertIn(b'return Response(body,', files[name])
                files[name] = files[name].replace(b'return Response(body,', b'return Response(body.strip(),', 1)
            elif mutation == 'contract_http_route':
                name = 'audio_backend/moss_handy_http.py'
                self.assertIn(b'/moss-jobs/{job_id}/cancel', files[name])
                files[name] = files[name].replace(b'/moss-jobs/{job_id}/cancel', b'/moss-jobs/{job_id}/retry', 1)
            elif mutation == 'contract_startup_order':
                name = 'audio_backend/app.py'
                fragment = b'ownership_fence(settings.data_dir)\n            fragments.initialize_factory_once()'
                self.assertIn(fragment, files[name])
                files[name] = files[name].replace(fragment,
                    b'fragments.initialize_factory_once()\n            ownership_fence(settings.data_dir)', 1)
            elif mutation == 'contract_startup_defer':
                name = 'audio_backend/app.py'
                self.assertIn(b'defer_factory_initialization=True', files[name])
                files[name] = files[name].replace(b'defer_factory_initialization=True', b'defer_factory_initialization=False', 1)
            elif mutation == 'contract_initializer_identity':
                name = 'audio_backend/app.py'
                self.assertIn(b"initializer_safe=fragments.factory_initialization_state!='failed'", files[name])
                files[name] = files[name].replace(b"initializer_safe=fragments.factory_initialization_state!='failed'",
                    b'initializer_safe=True', 1)
            elif mutation == 'contract_sqlite_import':
                files[MODULE] += b'\nimport sqlite3\n'
            else:
                self.fail('Unknown source contract mutation')
        elif mutation.startswith('version_'):
            name = 'pyproject.toml' if mutation == 'version_project' else 'audio_backend/__init__.py'
            files[name] = files[name].replace(VERSION.encode(), b'0.2.0rc3.dev6')
        elif mutation.startswith('policy_'):
            value = json.loads(files[POLICY])
            kind = mutation.removeprefix('policy_')
            if kind == 'count': value['maximum_files'] += 1
            elif kind == 'total': value['maximum_total_bytes'] += 1
            elif kind == 'privacy': value['excluded_categories'].pop()
            elif kind == 'allowlist': value['allowed_files'].append('docs/unreviewed.md')
            elif kind == 'purpose': value['purpose'] += ' altered'
            files[POLICY] = canonical(value)
        elif mutation.startswith('historical_'):
            if mutation == 'historical_verifier':
                files[HISTORICAL] = files[HISTORICAL].replace(b'require(files == index', b'require(files == files')
            else:
                files[HISTORICAL] = files[HISTORICAL].replace(BASE.encode(), b'0' * 40)
        elif mutation.startswith('privacy_'):
            if mutation == 'privacy_secret':
                files[MODULE] += b'\n# ' + b'sk-' + b'synthetic' * 5 + b'\n'
            elif mutation == 'privacy_path':
                files[MODULE] += b'\n# ' + b'/' + b'workspace/' + b'synthetic\n'
            elif mutation == 'privacy_binary':
                files[MODULE] += b'\xff'
            else:
                files[MODULE] += b'\0'
        elif mutation.startswith('manifest_'):
            value = json.loads(files[MANIFEST])
            kind = mutation.removeprefix('manifest_')
            if kind == 'schema': value['schema'] = 'unreviewed'
            elif kind == 'base': value['base_commit'] = '0' * 40
            elif kind == 'tree': value['base_tree'] = '0' * 40
            elif kind == 'version': value['version'] = '0.2.0rc3.dev6'
            elif kind == 'scope': value['scope'] += ' altered'
            elif kind == 'duplicate': value['files'].append(value['files'][0])
            elif kind == 'self': value['files'].append({'path': MANIFEST, **pin(files[MANIFEST])})
            elif kind == 'order': value['files'].reverse()
            elif kind == 'pin': value['files'][0]['sha256'] = '0' * 64
            elif kind == 'missing': value['files'].pop()
            elif kind == 'bounds': value['runtime_bounds']['accepted_keys_per_epoch'] = 65
            elif kind == 'inventory': value['composed_inventory']['total'] = 25
            elif kind == 'off_exception': value['default_off_startup_exception'] = 'unchanged timing'
            files[MANIFEST] = canonical(value) + (b'\n' if kind == 'format' else b'')
            seal = canonical(expected_seal(files))
        elif mutation == 'mode':
            modes[MODULE] = '100755'
            index_modes = dict(modes)
        elif mutation == 'index_mode': index_modes[MODULE] = '100755'
        elif mutation == 'index': index['README.md'] += b'\n'
        elif mutation == 'seal': seal += b'\n'
        elif mutation == 'overlarge': files[MODULE] += b'x' * MAX_FILE_BYTES
        else: self.fail('Unknown mutation')
        if regenerate:
            files[MANIFEST] = canonical(expected_manifest(files, self.base, self.base_modes))
            seal = canonical(expected_seal(files))
        if mutation != 'index': index = dict(files)
        with self.assertRaises(ValueError, msg=mutation):
            verify(files, index, modes, index_modes, seal, self.base, self.base_modes)


def _mutation_case(name):
    def test(self):
        self.reject_mutation(name)
    test.__name__ = 'test_rejects_' + name
    return test


for _name in ('missing', 'extra', 'module', 'contract_test', 'contract_docs', 'gate', 'runner', 'cli',
              'runtime', 'history', 'guard', 'version_project', 'version_module',
              'policy_count', 'policy_total', 'policy_privacy', 'policy_allowlist', 'policy_purpose',
              'historical_verifier', 'historical_fixture', 'privacy_secret', 'privacy_path',
              'privacy_binary', 'privacy_nul', 'manifest_schema', 'manifest_base', 'manifest_tree',
              'manifest_version', 'manifest_scope', 'manifest_duplicate', 'manifest_self',
              'manifest_order', 'manifest_pin', 'manifest_missing', 'manifest_bounds', 'manifest_inventory',
              'manifest_off_exception', 'manifest_format', 'immutable_storage', 'immutable_native',
              'immutable_provider', 'immutable_cleanup', 'immutable_profile', 'immutable_model',
              'immutable_manifest', 'immutable_guard', 'contract_sql', 'contract_added_sql',
              'contract_cleanup', 'contract_fragment', 'contract_dependency', 'contract_off',
              'contract_factory_default', 'contract_inference_config', 'contract_bounds', 'contract_sqlite_import',
              'contract_http_bounds', 'contract_http_schema', 'contract_http_raw', 'contract_http_route',
              'contract_startup_order', 'contract_startup_defer', 'contract_initializer_identity', 'mode', 'index_mode', 'index', 'seal', 'overlarge'):
    setattr(SourceClosureTests, 'test_rejects_' + _name, _mutation_case(_name))


@contextmanager
def retained_fixture(*, prefix):
    """Keep successful, failed and interrupted fixture artifacts for the guard."""
    parent = Path(os.environ['GUARD_RUN_DIRECTORY']) / 'tmp' if os.environ.get('GUARD_RUN_DIRECTORY') else Path(tempfile.mkdtemp(prefix=prefix))
    directory = parent / (prefix + uuid.uuid4().hex)
    directory.mkdir(parents=True, mode=0o700)
    yield str(directory)


class SourceDiscoveryTests(unittest.TestCase):
    def test_git_discovery_includes_tracked_and_unignored_paths(self):
        with retained_fixture(prefix='rc3-dev5-source-fixture-') as directory:
            root = Path(directory)
            git('init', '--quiet', '--template=', root=root)
            (root / '.gitignore').write_bytes(b'ignored\n')
            (root / 'tracked').write_bytes(b'synthetic')
            (root / 'unignored').write_bytes(b'synthetic')
            (root / 'ignored').write_bytes(b'synthetic')
            git('add', '--', '.gitignore', 'tracked', root=root)
            rows = git('ls-files', '--cached', '--others', '--exclude-standard', '-z', root=root)
            self.assertEqual(set(rows.split(b'\0')) - {b''}, {b'.gitignore', b'tracked', b'unignored'})

    def test_rejects_symlink_file(self):
        with retained_fixture(prefix='rc3-dev5-source-fixture-') as directory:
            root = Path(directory)
            (root / 'regular').write_bytes(b'synthetic')
            (root / 'link').write_bytes(b'synthetic')
            with patch.object(Path, 'is_symlink', lambda path: path.name == 'link'), self.assertRaises(ValueError):
                read_paths(root, ['link'])

    def test_rejects_symlink_directory(self):
        with retained_fixture(prefix='rc3-dev5-source-fixture-') as directory:
            root = Path(directory)
            (root / 'real').mkdir()
            (root / 'real' / 'source').write_bytes(b'synthetic')
            (root / 'link').mkdir()
            with patch.object(Path, 'is_symlink', lambda path: path.name == 'link'), self.assertRaises(ValueError):
                read_paths(root, ['link/source'])

    def test_rejects_hardlink_file(self):
        with retained_fixture(prefix='rc3-dev5-source-fixture-') as directory:
            root = Path(directory)
            (root / 'regular').write_bytes(b'synthetic')
            info = (root / 'regular').lstat()
            fake = SimpleNamespace(st_mode=info.st_mode, st_nlink=2, st_size=info.st_size)
            with patch.object(Path, 'lstat', return_value=fake), self.assertRaises(ValueError):
                read_paths(root, ['regular'])

    def test_rejects_missing_file(self):
        with retained_fixture(prefix='rc3-dev5-source-fixture-') as directory:
            with self.assertRaises(FileNotFoundError): read_paths(Path(directory), ['missing'])

    def test_rejects_directory_file(self):
        with retained_fixture(prefix='rc3-dev5-source-fixture-') as directory:
            root = Path(directory)
            (root / 'directory').mkdir()
            with self.assertRaises(ValueError): read_paths(root, ['directory'])

    def test_rejects_duplicate_path(self):
        with self.assertRaises(ValueError): read_paths(ROOT, ['README.md', 'README.md'])

    def test_rejects_unsafe_path(self):
        for path in ('../source', '/source', './source', 'a//source', '.git/config', 'a\\source'):
            with self.subTest(path=path), self.assertRaises(ValueError): safe_name(path)

    def test_rejects_oversize_before_read(self):
        with retained_fixture(prefix='rc3-dev5-source-fixture-') as directory:
            root = Path(directory)
            (root / 'large').write_bytes(b'x' * (MAX_FILE_BYTES + 1))
            with self.assertRaises(ValueError): read_paths(root, ['large'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
