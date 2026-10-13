#!/usr/bin/env python3
"""Unexecuted portable source recipe for a finite CellStore storage proof.

This program never imports the product, installs a runtime, edits a registry,
constructs RuntimeQualification, or manufactures a rollback journal. Its
same-schema stress rows are synthetic storage shapes, not producer evidence.
Only the exact shipped runtime binding is admitted. Run only after the source,
generated local config, resource envelope, and execution have been reviewed.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import stat
import struct
import sys
import time

MIB = 1024 * 1024
PRODUCT_COMMIT = "1fc066910ca78b2e57d7a28f79b91cafc06f5dd5"
PRODUCT_FILES = {
    "audio_backend/__init__.py": {"bytes": 107, "sha256": "6cecf857a4ee554462be2a39f7409c3e05d450798c6565a1eedb25df702cef18"},
    "audio_backend/native_lexical_alternatives.py": {"bytes": 10505, "sha256": "321745aa3bef2462cb5b2b196d16f9ffe78d70c8bf20aeaf36e701ffc45d708f"},
    "audio_backend/native_owned_cells.py": {"bytes": 25688, "sha256": "ab282afa1877052f4c1da1566bb07f7c6b82d28749b98068cd45b578edcb4eb6"},
    "audio_backend/native_owned_seam_cells.py": {"bytes": 20744, "sha256": "8e8de861894aca297d94c8b5032eb53e2e42c05fb25cfc78fb3bb956877b2518"},
    "audio_backend/native_owned_cell_store.py": {"bytes": 67659, "sha256": "c9d5bbc8a1a2b0626d3eeede176264e4fe468359154692788331b202877d82d3"},
}
AST_PINS = {
    "_check_parents": "eb575124a76c0efc1a964852ec67e6ca28bc638e5a3b6a0eb94456a5c5483a92",
    "_scan": "9b747596c7a6067edf406bc4210f569547b62a7c4b3f2d07b0e2326ad4274676",
    "_open": "e04b7e33b99fc0e76c8208f1662cfa209cda6a756d3ff6aee05b49be7d1d005a",
    "_schema": "db57a753f7aab4287fcd978b21a833e23574d1cea3447364544dc17adb6b4e12",
    "_transaction": "c6f088bfc189d6239b4b6ff3d23cc1af783b43a41aca8511996b94fce8f63c9e",
    "_receipt": "5b34f8792abba7df8877590085d511bc3f09cc0aab260ab8c5c8dd82787e5c35",
    "_publish": "635da5613699016488cbd87e387d05de5c06da760de1564e1f6f0015a6429779",
    "_insert_cell": "3542b164f4dbfc488a49c87273251b51669ca5cd7d7fbcb7201e7d40adeb3bad",
    "_expire": "1d290e03290b94f8c2444d93cb9bd8121266c814428985f2682a16c831bd7a92",
    "_sweep": "d84a8f06607398b06595d9d884092f1f80bef9c906e604dda9562e3701b17389",
}
RECIPE_CAPS = {"README.md": 12 * 1024, "qualify_cell_store.py": 64 * 1024,
               "config.schema.json": 12 * 1024, "config.template.json": 4 * 1024,
               "test_qualify_cell_store.py": 32 * 1024}
LIMITS = {
    "owner_seconds": 300, "work_seconds": 240, "cleanup_seconds": 60,
    "child_cpu_seconds": 240, "child_address_space_bytes": 512 * MIB,
    "child_file_bytes": 32 * MIB, "child_open_files": 64,
    "io_bytes": 65536, "read_bytes": 1024 * MIB,
    "config_bytes": 65536, "events_bytes": 256 * 1024,
    "result_bytes": 256 * 1024, "records_bytes": MIB,
    "page_size": 4096, "max_page_count": 6144, "near_limit_pages": 6143,
    "database_bytes": 24 * MIB, "journal_bytes": 32 * MIB,
    "slack_bytes": 2 * MIB, "envelope_bytes": 58 * MIB,
    "family_bytes": 64 * MIB, "total_bytes": 193 * MIB,
    "journal_sector_max": 65536, "journal_headers_max": 1,
    "cells": 1024, "receipts": 4096, "dependencies_per_cell": 128,
    "fill_attempts": 1024, "oracle_rows": 137000,
    "oracle_row_bytes": MIB, "oracle_bytes": 32 * MIB,
    "sqlite_steps": 5000000, "poll_count": 20000, "poll_seconds": 15,
    "event_count": 256,
}
PRAGMAS = ("PRAGMA synchronous=FULL", "PRAGMA temp_store=MEMORY", "PRAGMA busy_timeout=0",
           "PRAGMA cache_spill=OFF", "PRAGMA cache_size=-4096", "PRAGMA mmap_size=0",
           "PRAGMA foreign_keys=ON", "PRAGMA trusted_schema=OFF", "PRAGMA max_page_count=6144")
READBACKS = {"page_size": 4096, "max_page_count": 6144, "synchronous": 2,
             "temp_store": 2, "busy_timeout": 0, "cache_spill": 0, "mmap_size": 0,
             "foreign_keys": 1, "trusted_schema": 0, "auto_vacuum": 0}
FAMILY_NAMES = ("fill", "crash", "recovery")
DB_NAME = "cells.sqlite3"
ALLOWED_FILES = frozenset((DB_NAME, DB_NAME + "-journal", DB_NAME + "-wal", DB_NAME + "-shm"))
MAGIC = bytes.fromhex("d9d505f920a163d7")
FINGERPRINT_KEYS = frozenset(("interpreter_bytes", "interpreter_sha256",
    "interpreter_realpath_sha256", "sqlite3_package_sha256", "sqlite3_binding",
    "sqlite3_extension_sha256", "sqlite3_library_sha256", "sqlite3_library_realpath_sha256"))
CONFIG_KEYS = frozenset(("schema", "product_commit", "product_root", "product_files",
    "product_ast", "recipe_files", "interpreter", "work_parent", "run_name",
    "runtime", "shipped_profile", "limits", "procedure"))


class Refused(Exception):
    def __init__(self, reason, status="FAILED", *, phase=None, proof_started=None):
        super().__init__(reason)
        self.reason, self.status = reason, status
        if phase is not None:
            self.phase = phase
        if proof_started is not None:
            self.proof_execution_started = proof_started


def require(ok, reason, status="FAILED"):
    if not ok:
        raise Refused(reason, status)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def charge(info):
    require(hasattr(info, "st_blocks"), "allocated_blocks_unavailable")
    return max(info.st_size, info.st_blocks * 512)


class Budget:
    def __init__(self, seconds=240):
        self.started = time.monotonic()
        self.deadline = self.started + seconds
        self.read_bytes = 0
        self.steps = 0

    def check(self):
        require(time.monotonic() < self.deadline, "work_deadline", "INCOMPLETE")
        require(self.read_bytes <= LIMITS["read_bytes"], "read_capacity")
        require(self.steps <= LIMITS["sqlite_steps"], "sqlite_step_capacity")

    def read(self, count):
        self.read_bytes += count
        self.check()

    def progress(self):
        self.steps += 100
        try:
            self.check()
        except Refused:
            return 1
        return 0


def absolute(path):
    value = Path(path)
    require(value.is_absolute() and ".." not in value.parts and len(value.parts) <= 33,
            "absolute_bounded_path_required", "REGISTRY_REVIEW_REQUIRED")
    require(len(os.fsencode(value)) <= 4096, "path_capacity")
    return value


def ancestry(path, require_owned=True):
    """No sticky-directory exception; observations are not a same-UID lock."""
    path = absolute(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    fd = os.open("/", flags)
    chain = []
    try:
        for index, name in enumerate(("/",) + path.parts[1:]):
            if index:
                child = os.open(name, flags, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    require((opened.st_dev, opened.st_ino) == (entry.st_dev, entry.st_ino), "ancestor_entry_changed")
                except BaseException:
                    os.close(child)
                    raise
                os.close(fd)
                fd = child
            info = os.fstat(fd)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.getuid())
                    and not info.st_mode & 0o022, "unsafe_ancestor")
            chain.append([info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)])
        require(not require_owned or os.fstat(fd).st_uid == os.getuid(), "parent_not_owned")
        return chain
    finally:
        os.close(fd)


class Fences:
    """Retained root/family descriptors plus fresh no-follow ancestry checks."""
    def __init__(self, root, expected_token=None):
        self.root, self.fds, self.identities = absolute(root), {}, {}
        self.chain = ancestry(self.root)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            self.fds[""] = os.open(self.root, flags)
            for name in (*FAMILY_NAMES, "records"):
                self.fds[name] = os.open(name, flags, dir_fd=self.fds[""])
            for name, fd in self.fds.items():
                info = os.fstat(fd)
                self.identities[name] = [info.st_dev, info.st_ino]
            self.check()
            require(expected_token is None or expected_token == self.token(), "worker_directory_binding_mismatch")
        except BaseException as original:
            try:
                self.close()
            except BaseException as cleanup:
                original.add_note("fence_setup_cleanup:" + type(cleanup).__name__)
            raise

    def token(self):
        return sha(canonical({"chain": self.chain, "directories": self.identities}))

    def check(self):
        require(ancestry(self.root) == self.chain, "persistent_ancestor_changed")
        require(set(self.fds) == {"", *FAMILY_NAMES, "records"}, "closed_directory_fence")
        for name, fd in self.fds.items():
            current = os.fstat(fd)
            entry = (os.stat(name, dir_fd=self.fds[""], follow_symlinks=False) if name
                     else os.stat(self.root, follow_symlinks=False))
            for info in (current, entry):
                require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                        and not info.st_mode & 0o077, "unsafe_persistent_directory")
                require([info.st_dev, info.st_ino] == self.identities[name], "persistent_directory_replaced")

    def close(self):
        errors = []
        for name in tuple(self.fds):
            fd = self.fds.pop(name)
            try:
                os.close(fd)
            except OSError as exc:
                errors.append(type(exc).__name__)
        require(not errors, "directory_descriptor_close_unconfirmed")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def stable_read(path, maximum, budget=None):
    path = absolute(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and 0 <= info.st_size <= maximum,
                "file_capacity_or_type")
        chunks, left = [], info.st_size
        while left:
            part = os.read(fd, min(left, LIMITS["io_bytes"]))
            require(bool(part), "file_truncated")
            if budget:
                budget.read(len(part))
            chunks.append(part)
            left -= len(part)
        require(signature(info) == signature(os.fstat(fd))
                == signature(os.stat(path, follow_symlinks=False)), "file_changed")
        return b"".join(chunks), info
    finally:
        os.close(fd)


def stable_hash(path, maximum, budget=None):
    path = absolute(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= maximum, "hash_file_capacity")
        value, left = hashlib.sha256(), info.st_size
        while left:
            part = os.read(fd, min(left, LIMITS["io_bytes"]))
            require(bool(part), "hash_file_truncated")
            if budget:
                budget.read(len(part))
            value.update(part)
            left -= len(part)
        require(signature(info) == signature(os.fstat(fd))
                == signature(os.stat(path, follow_symlinks=False)), "hash_file_changed")
        return {"bytes": info.st_size, "sha256": value.hexdigest()}, info
    finally:
        os.close(fd)


def recipe_inventory():
    root = Path(__file__).resolve(strict=True).parent
    result = {}
    for name, maximum in RECIPE_CAPS.items():
        result[name], _ = stable_hash(root / name, maximum)
    require(sum(row["bytes"] for row in result.values()) <= 124 * 1024, "recipe_total_capacity")
    return result


def product_source(root):
    """Read pinned source as data; do not import or execute product ASTs."""
    root = absolute(root)
    ancestry(root)
    for name, expected in PRODUCT_FILES.items():
        actual, _ = stable_hash(root / name, 250000)
        require(actual == expected, "product_source_mismatch", "REGISTRY_REVIEW_REQUIRED")
    raw, _ = stable_read(root / "audio_backend/native_owned_cell_store.py", 250000)
    tree = ast.parse(raw)
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CellStore")
    methods = {n.name: n for n in owner.body if isinstance(n, ast.FunctionDef)}
    pins = {name: sha(ast.dump(methods[name], annotate_fields=True,
                include_attributes=False).encode()) for name in AST_PINS}
    require(pins == AST_PINS, "product_ast_mismatch", "REGISTRY_REVIEW_REQUIRED")
    assignment = next(n for n in tree.body if isinstance(n, ast.AnnAssign)
                      and isinstance(n.target, ast.Name) and n.target.id == "QUALIFIED_RUNTIMES")
    require(isinstance(assignment.value, ast.Tuple) and len(assignment.value.elts) == 1,
            "registry_shape_changed", "REGISTRY_REVIEW_REQUIRED")
    call = assignment.value.elts[0]
    require(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "RuntimeQualification" and not call.args, "registry_shape_changed")
    profile = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
    statements = next(n for n in methods["_schema"].body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "statements" for t in n.targets))
    schema = ast.literal_eval(statements.value)
    pragma_loop = next(n for n in ast.walk(methods["_open"]) if isinstance(n, ast.For)
                       and isinstance(n.target, ast.Name) and n.target.id == "pragma")
    require(ast.literal_eval(pragma_loop.iter) == PRAGMAS, "pragma_ast_mismatch")
    require(len(schema) == 7 and all(type(s) is str for s in schema), "schema_shape_changed")
    return profile, schema


def runtime_observation(work_parent, budget):
    """Real built-in binding plus bounded memory-only source/build observation."""
    import sqlite3
    import _sqlite3
    from importlib.machinery import BuiltinImporter
    require(sys.platform == "linux", "linux_required", "REGISTRY_REVIEW_REQUIRED")
    spec = _sqlite3.__spec__
    require(spec is not None and spec.name == "_sqlite3" and spec.origin == "built-in"
            and spec.loader is BuiltinImporter and getattr(_sqlite3, "__file__", None) is None,
            "unqualified_sqlite_binding_form", "REGISTRY_REVIEW_REQUIRED")
    executable = Path(sys.executable).resolve(strict=True)
    executable_hash, info = stable_hash(executable, 64 * MIB, budget)
    actual = os.stat("/proc/self/exe")
    require((actual.st_dev, actual.st_ino, actual.st_size) == (info.st_dev, info.st_ino, info.st_size),
            "interpreter_container_mismatch", "REGISTRY_REVIEW_REQUIRED")
    package = Path(sqlite3.__file__).resolve(strict=True).parent
    require(Path(sqlite3.__file__).name == "__init__.py"
            and Path(sqlite3.dbapi2.__file__).resolve(strict=True) == package / "dbapi2.py",
            "sqlite_package_origin_mismatch", "REGISTRY_REVIEW_REQUIRED")
    inventory, entries = {}, 0
    for directory in (package, package / "__pycache__"):
        if directory.name == "__pycache__" and not os.path.lexists(directory):
            continue
        require(stat.S_ISDIR(directory.lstat().st_mode), "sqlite_package_directory_alias")
        with os.scandir(directory) as stream:
            for entry in stream:
                entries += 1
                require(entries <= 32, "sqlite_package_entry_capacity")
                if not entry.name.endswith(".pyc" if directory.name == "__pycache__" else ".py"):
                    continue
                require(len(inventory) < 16, "sqlite_package_file_capacity")
                inventory[str(Path(entry.path).relative_to(package))], _ = stable_hash(
                    Path(entry.path).resolve(strict=True), 256 * 1024, budget)
    require({"__init__.py", "dbapi2.py"}.issubset(inventory), "sqlite_package_missing")
    with open("/proc/self/maps", "rb", buffering=0) as stream:
        mappings = stream.read(256 * 1024 + 1)
    budget.read(len(mappings))
    require(len(mappings) <= 256 * 1024, "runtime_mapping_capacity")
    for line in mappings.splitlines():
        fields = line.split(None, 5)
        require(not (len(fields) == 6 and b"libsqlite3" in fields[5]),
                "unexpected_separate_sqlite_mapping", "REGISTRY_REVIEW_REQUIRED")
    db = sqlite3.connect(":memory:", timeout=0., isolation_level=None)
    try:
        db.set_progress_handler(budget.progress, 100)
        source_id = db.execute("SELECT sqlite_source_id()").fetchone()[0]
        options = sorted(row[0] for row in db.execute("PRAGMA compile_options"))
        require(len(options) <= 512, "compile_options_capacity")
    finally:
        db.close()
    fingerprint = {"interpreter_bytes": executable_hash["bytes"],
        "interpreter_sha256": executable_hash["sha256"],
        "interpreter_realpath_sha256": sha(os.fsencode(executable)),
        "sqlite3_package_sha256": sha(canonical(inventory)),
        "sqlite3_binding": "observed-built-in-no-separate-sqlite-mapping-v1",
        "sqlite3_extension_sha256": None, "sqlite3_library_sha256": None,
        "sqlite3_library_realpath_sha256": None}
    return {"fingerprint": fingerprint, "interpreter_realpath": str(executable),
        "python_version": sys.version, "sqlite_source_id": source_id,
        "sqlite_version": sqlite3.sqlite_version, "compile_options": options,
        "compile_options_sha256": sha(canonical(options)), "package_inventory": inventory,
        "platform": "linux", "vfs": "unix", "filesystem_device": Path(work_parent).stat().st_dev}


def match_shipped(observed, profile):
    expected = {key: profile[key] for key in FINGERPRINT_KEYS}
    fields = ("sqlite_source_id", "sqlite_version", "compile_options_sha256",
              "platform", "vfs", "filesystem_device")
    require(observed["fingerprint"] == expected
            and all(observed[key] == profile[key] for key in fields),
            "shipped_runtime_binding_mismatch", "REGISTRY_REVIEW_REQUIRED")
    require(profile["sector_bytes_max"] == LIMITS["journal_sector_max"]
            and profile["journal_headers_max"] == 1
            and profile["allocation_slack_bytes_max"] == LIMITS["slack_bytes"],
            "shipped_envelope_changed", "REGISTRY_REVIEW_REQUIRED")


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def parse_json(raw):
    def bad_constant(value):
        raise Refused("nonfinite_json")
    return json.loads(raw, object_pairs_hook=reject_duplicates, parse_constant=bad_constant)


def validate_config(cfg):
    require(type(cfg) is dict and set(cfg) == CONFIG_KEYS, "config_keys")
    require(cfg["schema"] == "portable-cell-store-proof-config-v1"
            and cfg["procedure"] == "matching-shipped-profile-only-v1", "config_version")
    require(cfg["product_commit"] == PRODUCT_COMMIT and cfg["product_files"] == PRODUCT_FILES
            and cfg["product_ast"] == AST_PINS and cfg["limits"] == LIMITS,
            "config_source_or_limits_mismatch", "REGISTRY_REVIEW_REQUIRED")
    for name in ("product_root", "interpreter", "work_parent"):
        require(type(cfg[name]) is str and "CHOOSE_" not in cfg[name], "unresolved_config")
        absolute(cfg[name])
    name = cfg["run_name"]
    require(type(name) is str and 1 <= len(name) <= 64
            and all(c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in name),
            "run_name_shape")
    require(type(cfg["recipe_files"]) is dict and set(cfg["recipe_files"]) == set(RECIPE_CAPS), "recipe_keys")
    require(type(cfg["runtime"]) is dict and type(cfg["shipped_profile"]) is dict, "runtime_shape")


def preflight(cfg, budget):
    validate_config(cfg)
    require(recipe_inventory() == cfg["recipe_files"], "recipe_source_changed", "REGISTRY_REVIEW_REQUIRED")
    profile, schema = product_source(cfg["product_root"])
    require(profile == cfg["shipped_profile"], "shipped_profile_changed", "REGISTRY_REVIEW_REQUIRED")
    chain = ancestry(cfg["work_parent"])
    require(Path(cfg["interpreter"]).resolve(strict=True) == Path(sys.executable).resolve(strict=True),
            "configured_interpreter_not_running", "REGISTRY_REVIEW_REQUIRED")
    observed = runtime_observation(cfg["work_parent"], budget)
    require(observed == cfg["runtime"], "configured_runtime_changed", "REGISTRY_REVIEW_REQUIRED")
    match_shipped(observed, profile)
    return schema, chain


def write_new(path, raw, maximum):
    require(len(raw) <= maximum, "output_capacity")
    path = absolute(path)
    ancestry(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        offset = 0
        while offset < len(raw):
            count = os.write(fd, raw[offset:offset + LIMITS["io_bytes"]])
            require(count > 0, "short_output_write")
            offset += count
        os.fsync(fd)
    except BaseException as original:
        try:
            os.close(fd)
        except BaseException as cleanup:
            original.add_note("record_write_cleanup:" + type(cleanup).__name__)
        raise
    else:
        os.close(fd)


def configure(args):
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
            "invoke_with_I_S_B")
    parent, product = absolute(args.work_parent), absolute(args.product_root)
    ancestry(parent)
    profile, _ = product_source(product)
    observation = runtime_observation(parent, Budget())
    match_shipped(observation, profile)  # Mismatch creates no config or work files.
    cfg = {"schema": "portable-cell-store-proof-config-v1", "product_commit": PRODUCT_COMMIT,
        "product_root": str(product), "product_files": PRODUCT_FILES, "product_ast": AST_PINS,
        "recipe_files": recipe_inventory(), "interpreter": str(Path(sys.executable).resolve(strict=True)),
        "work_parent": str(parent), "run_name": args.run_name, "runtime": observation,
        "shipped_profile": profile, "limits": LIMITS, "procedure": "matching-shipped-profile-only-v1"}
    validate_config(cfg)
    raw = canonical(cfg) + b"\n"
    write_new(absolute(args.config_out), raw, LIMITS["config_bytes"])
    return {"status": "CONFIGURED_NOT_EXECUTED", "config_sha256": sha(raw),
            "config_path": str(absolute(args.config_out)), "runtime_matches_shipped": True,
            "qualification_created": False}


def load_config(path, expected_hash=None):
    raw, _ = stable_read(absolute(path), LIMITS["config_bytes"])
    require(expected_hash is None or sha(raw) == expected_hash, "config_changed", "REGISTRY_REVIEW_REQUIRED")
    cfg = parse_json(raw)
    validate_config(cfg)
    return cfg, raw


def scan_family(directory, expected_ancestors=None):
    directory = absolute(directory)
    parents = ancestry(directory.parent)
    require(expected_ancestors is None or parents == expected_ancestors, "ancestor_identity_changed")
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            "unsafe_family_directory")
    files, total, logical = {}, charge(info), 0
    with os.scandir(directory) as stream:
        for entry in stream:
            require(entry.name in ALLOWED_FILES, "unexpected_family_entry")
            row = entry.stat(follow_symlinks=False)
            require(stat.S_ISREG(row.st_mode) and row.st_nlink == 1 and row.st_uid == os.getuid()
                    and row.st_dev == info.st_dev and not row.st_mode & 0o077, "unsafe_family_file")
            files[entry.name] = {"bytes": row.st_size, "allocated_charge": charge(row),
                "dev": row.st_dev, "ino": row.st_ino, "mode": stat.S_IMODE(row.st_mode)}
            total += charge(row)
            logical += row.st_size
    result = {"directory": {"dev": info.st_dev, "ino": info.st_ino,
              "mode": stat.S_IMODE(info.st_mode), "allocated_charge": charge(info)},
              "files": files, "allocated_charge": total, "logical_bytes": logical,
              "slack_bytes": total - logical, "ancestors": parents}
    require(total <= LIMITS["family_bytes"] and total <= LIMITS["envelope_bytes"], "family_capacity")
    require(result["slack_bytes"] <= LIMITS["slack_bytes"], "allocated_slack_capacity")
    require(files.get(DB_NAME, {}).get("bytes", 0) <= LIMITS["database_bytes"]
            and files.get(DB_NAME + "-journal", {}).get("bytes", 0) <= LIMITS["journal_bytes"],
            "database_or_journal_capacity")
    require(not any(name.endswith(("-wal", "-shm")) for name in files), "wal_shm_counted_then_rejected")
    return result


def whole_charge(root, fences):
    """Three families plus a separately bounded records/control namespace."""
    fences.check()
    root_info = root.lstat()
    require(stat.S_ISDIR(root_info.st_mode) and not root_info.st_mode & 0o077, "unsafe_run_root")
    require(set(os.listdir(root)) == set(FAMILY_NAMES) | {"records"}, "unexpected_run_entry")
    families = {name: scan_family(root / name) for name in FAMILY_NAMES}
    records = root / "records"
    rec_info = records.lstat()
    require(stat.S_ISDIR(rec_info.st_mode) and rec_info.st_uid == os.getuid()
            and not rec_info.st_mode & 0o077, "unsafe_records_directory")
    count = charge(root_info) + charge(rec_info)
    allowed = {"config.json": LIMITS["config_bytes"], "events.jsonl": LIMITS["events_bytes"],
               "result.json": LIMITS["result_bytes"], "result.pending.json": LIMITS["result_bytes"]}
    with os.scandir(records) as stream:
        for entry in stream:
            require(entry.name in allowed, "unexpected_record")
            row = entry.stat(follow_symlinks=False)
            require(stat.S_ISREG(row.st_mode) and row.st_nlink == 1 and row.st_uid == os.getuid()
                    and row.st_dev == root_info.st_dev and not row.st_mode & 0o077
                    and row.st_size <= allowed[entry.name], "unsafe_or_oversized_record")
            count += charge(row)
    require(count <= LIMITS["records_bytes"], "records_capacity")
    total = count + sum(item["allocated_charge"] for item in families.values())
    require(total <= LIMITS["total_bytes"], "aggregate_capacity")
    fences.check()
    return {"families": families, "records_and_control_charge": count, "total_charge": total}


def copy_family(source, destination, budget, fences):
    fences.check()
    before = scan_family(source)
    require(not os.listdir(destination), "copy_destination_not_empty")
    copied = {}
    for name in sorted(before["files"]):
        fences.check()
        expected, info = stable_hash(source / name, LIMITS["journal_bytes"], budget)
        src = os.open(source / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        dst = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            require(signature(info) == signature(os.fstat(src)), "copy_source_changed")
            value, remaining = hashlib.sha256(), info.st_size
            while remaining:
                block = os.read(src, min(remaining, LIMITS["io_bytes"]))
                require(bool(block), "copy_source_truncated")
                budget.read(len(block))
                value.update(block)
                offset = 0
                while offset < len(block):
                    count = os.write(dst, block[offset:])
                    require(count > 0, "copy_short_write")
                    offset += count
                remaining -= len(block)
            os.fsync(dst)
            require(signature(info) == signature(os.fstat(src))
                    == signature(os.stat(source / name, follow_symlinks=False)), "copy_source_changed")
            require(value.hexdigest() == expected["sha256"], "copy_digest_mismatch")
        finally:
            os.close(src)
            os.close(dst)
        actual, _ = stable_hash(destination / name, LIMITS["journal_bytes"], budget)
        require(actual == expected, "copy_verification_mismatch")
        copied[name] = actual
    after = scan_family(source)
    require(after == before, "copy_family_changed")
    scan_family(destination)
    fences.check()
    return copied


def sqlite_open(directory, schema, budget, fences, create=False):
    import sqlite3
    fences.check()
    scan_family(directory)
    path = directory / DB_NAME
    require(create == (not path.exists()), "sqlite_open_mode")
    db = sqlite3.connect(path.as_uri() + ("?mode=rwc&vfs=unix" if create else "?mode=rw&vfs=unix"),
                         uri=True, timeout=0., isolation_level=None)
    try:
        fences.check()
        scan_family(directory)
        db.set_progress_handler(budget.progress, 100)
        require(db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete", "journal_mode")
        if create:
            db.execute("PRAGMA page_size=4096")
        for statement in PRAGMAS:
            db.execute(statement)
        require(all(db.execute("PRAGMA " + key).fetchone()[0] == value
                    for key, value in READBACKS.items()), "pragma_readback")
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2359296)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 256 * 1024)
        db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        if create:
            db.execute("BEGIN IMMEDIATE")
            for statement in schema:
                db.execute(statement)
            db.execute("COMMIT")
        return db
    except BaseException:
        db.close()
        raise


ORACLE_TABLES = (("store_meta", "id"), ("sources", "cid"), ("cells", "seq"),
                 ("dependencies", "cell_id,position"), ("sqlite_sequence", "name"))


def logical_oracle(db, budget):
    value, counts, byte_count, row_count = hashlib.sha256(), {}, 0, 0
    def feed(item):
        nonlocal byte_count
        if item is None:
            tag, raw = b"N", b""
        elif type(item) is int:
            tag, raw = b"I", str(item).encode("ascii")
        elif type(item) is float:
            tag, raw = b"F", struct.pack(">d", item)
        elif type(item) is str:
            tag, raw = b"T", item.encode("utf-8")
        elif type(item) is bytes:
            tag, raw = b"B", item
        else:
            raise Refused("oracle_value_type")
        require(len(raw) <= LIMITS["oracle_row_bytes"], "oracle_value_capacity")
        byte_count += len(raw)
        require(byte_count <= LIMITS["oracle_bytes"], "oracle_byte_capacity")
        value.update(tag + struct.pack(">Q", len(raw)))
        for offset in range(0, len(raw), LIMITS["io_bytes"]):
            value.update(raw[offset:offset + LIMITS["io_bytes"]])
        return len(raw)
    for table, order in ORACLE_TABLES:
        feed(table)
        cursor = db.execute("SELECT * FROM " + table + " ORDER BY " + order)
        feed(canonical([item[0] for item in cursor.description]))
        count = 0
        for row in cursor:
            budget.check()
            row_count += 1
            require(row_count <= LIMITS["oracle_rows"], "oracle_row_capacity")
            value.update(b"R" + struct.pack(">I", len(row)))
            require(sum(feed(item) for item in row) <= LIMITS["oracle_row_bytes"], "oracle_projection_capacity")
            count += 1
        counts[table] = count
    return {"sha256": value.hexdigest(), "counts": counts, "value_bytes": byte_count,
            "encoding": "typed-length-prefixed-row-stream-v1"}


def database_checks(db):
    require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "integrity_check")
    require(db.execute("PRAGMA foreign_key_check").fetchone() is None, "foreign_key_check")
    pages = db.execute("PRAGMA page_count").fetchone()[0]
    require(pages <= LIMITS["max_page_count"], "page_count_capacity")
    return {"page_count": pages, "freelist_count": db.execute("PRAGMA freelist_count").fetchone()[0]}


def row_state(db):
    return [db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
            for name in ("cells", "sources", "dependencies")] + [
                db.execute("SELECT coalesce(MAX(seq),0) FROM cells").fetchone()[0]]


def fill_database(directory, schema, budget, emit, fences):
    import sqlite3
    db = sqlite_open(directory, schema, budget, fences, create=True)
    attempts, inserted, full_count, max_pages, max_charge = 0, 0, 0, 0, 0
    try:
        db.execute("INSERT INTO store_meta VALUES(1,?,?,?,?,?,?)", (
            "native-owned-cell-store-v1", "SYNTHETIC_STORAGE_SHAPE_ONLY", "NO_PRIMARY_AUTHORITY",
            "NOT_A_RUNTIME_QUALIFICATION", "STORAGE_PROOF_NAMESPACE", "STORAGE_PROOF_DATABASE"))
        db.execute("INSERT INTO sources VALUES(?,?,?,?)", ("source", "synthetic", "0" * 64, b"S" * 1024))
        shapes = ((640000, 65536), (65536, 4096), (4096, 256), (512, 16), (1, 1))
        reached = False
        for pcm_size, proof_size in shapes:
            while not reached:
                budget.check()
                fences.check()
                attempts += 1
                require(attempts <= LIMITS["fill_attempts"] and inserted < LIMITS["cells"], "fill_attempt_capacity")
                before = row_state(db)
                cell_id = sha(("synthetic-cell-" + str(inserted)).encode())
                try:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("INSERT INTO cells(cell_id,ownership,sid,cid,proof,pcm,state) VALUES(?,?,?,?,?,?,?)",
                        (cell_id, sha(("ownership-" + str(inserted)).encode()), "synthetic", "source",
                         b"P" * proof_size, b"A" * pcm_size, "retained"))
                    db.execute("INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)",
                        (cell_id, 0, "source", "0" * 64, "1" * 64, 0, 1, 0, 1))
                    db.execute("COMMIT")
                except sqlite3.Error as exc:
                    if db.in_transaction:
                        db.execute("ROLLBACK")
                    require(getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL,
                            "unexpected_fill_sqlite_error")
                    require(row_state(db) == before and not db.in_transaction, "sql_full_rollback_mismatch")
                    full_count += 1
                    emit({"event": "sqlite_full", "attempt": attempts, "pcm_bytes": pcm_size,
                          "proof_bytes": proof_size, "rollback_row_state": before})
                    break
                inserted += 1
                fences.check()
                pages = db.execute("PRAGMA page_count").fetchone()[0]
                max_pages = max(max_pages, pages)
                scan = scan_family(directory)
                max_charge = max(max_charge, scan["allocated_charge"])
                require(pages <= LIMITS["max_page_count"], "fill_page_capacity")
                reached = pages >= LIMITS["near_limit_pages"]
            if reached:
                break
        require(reached and full_count > 0, "near_limit_or_sql_full_not_observed", "INCOMPLETE")
        checks = database_checks(db)
        oracle = logical_oracle(db, budget)
        result = {"oracle": oracle, "checks": checks, "attempts": attempts, "cells": inserted,
                  "sqlite_full_count": full_count, "max_observed_page_count": max_pages,
                  "max_observed_allocated_charge": max_charge}
    finally:
        db.close()
    fences.check()
    require(set(scan_family(directory)["files"]) == {DB_NAME}, "baseline_not_closed")
    result["database"], _ = stable_hash(directory / DB_NAME, LIMITS["database_bytes"], budget)
    return result


def validate_inherited_stdin(fstat=os.fstat, readlink=os.readlink):
    info = fstat(0)
    require(sys.platform == "linux" and stat.S_ISCHR(info.st_mode)
            and os.major(info.st_rdev) == 1 and os.minor(info.st_rdev) == 3
            and readlink("/proc/self/fd/0") == "/dev/null", "inherited_fd0_must_be_linux_dev_null")
    return 0


def child_limits():
    import resource
    for kind, limit in ((resource.RLIMIT_CPU, LIMITS["child_cpu_seconds"]),
                        (resource.RLIMIT_AS, LIMITS["child_address_space_bytes"]),
                        (resource.RLIMIT_FSIZE, LIMITS["child_file_bytes"]),
                        (resource.RLIMIT_NOFILE, LIMITS["child_open_files"])):
        _, old_hard = resource.getrlimit(kind)
        require(old_hard == resource.RLIM_INFINITY or old_hard >= limit, "existing_resource_limit_stricter")
        resource.setrlimit(kind, (limit, limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.alarm(LIMITS["work_seconds"])


def control_read(fd, expected, budget):
    data = bytearray()
    while b"\n" not in data:
        budget.check()
        ready, _, _ = select.select([fd], [], [], min(.05, max(0., budget.deadline - time.monotonic())))
        if ready:
            block = os.read(fd, 128)
            require(bool(block), "control_eof", "INCOMPLETE")
            data.extend(block)
            require(len(data) <= 128, "control_capacity")
    require(bytes(data) == expected.encode() + b"\n", "control_message")


def worker(args, phase):
    child_limits()
    validate_inherited_stdin()
    budget = Budget()
    cfg, _ = load_config(args.config, args.config_sha256)
    schema, _ = preflight(cfg, budget)
    root = absolute(cfg["work_parent"]) / cfg["run_name"]
    with Fences(root, args.fence_token) as fences:
        phase.update(phase="worker_physical", proof_started=True)
        count = 0
        def emit(record):
            nonlocal count
            count += 1
            require(count <= LIMITS["event_count"], "worker_event_capacity")
            raw = canonical(record) + b"\n"
            require(len(raw) <= 16384, "worker_event_size")
            offset = 0
            while offset < len(raw):
                offset += os.write(1, raw[offset:])
        baseline = fill_database(root / "fill", schema, budget, emit, fences)
        emit({"event": "fill_closed", "baseline": baseline})
        control_read(args.control_fd, "crash_copy_ready", budget)
        fences.check()
        actual, _ = stable_hash(root / "crash" / DB_NAME, LIMITS["database_bytes"], budget)
        require(actual == baseline["database"], "crash_baseline_copy_mismatch")
        db = sqlite_open(root / "crash", schema, budget, fences)
        try:
            require(logical_oracle(db, budget) == baseline["oracle"], "crash_baseline_oracle")
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT cell_id,length(proof),length(pcm) FROM cells ORDER BY seq").fetchall()
            require(0 < len(rows) <= LIMITS["cells"], "crash_row_capacity")
            for cell_id, proof_size, pcm_size in rows:
                budget.check()
                fences.check()
                require(1 <= proof_size <= 65536 and 1 <= pcm_size <= 640000, "crash_payload_shape")
                db.execute("UPDATE cells SET proof=?,pcm=?,revision=revision+1 WHERE cell_id=?",
                           (b"Q" * proof_size, b"B" * pcm_size, cell_id))
            scan = scan_family(root / "crash")
            emit({"event": "crash_ready", "rows": len(rows), "scan": scan,
                  "statement_kind": "conservative-same-schema-overwrite-not-production-publication"})
            control_read(args.control_fd, "commit", budget)
            fences.check()
            db.execute("COMMIT")
            emit({"event": "commit_finished", "meaning": "capture_window_missed"})
        finally:
            db.close()
        return {"status": "INCOMPLETE", "reason": "writer_commit_finished"}


def header_fields(raw):
    require(len(raw) >= 28 and raw[:8] == MAGIC, "journal_magic", "INCOMPLETE")
    records, nonce, database_pages, sector, page = struct.unpack(">IIIII", raw[8:28])
    require(1 <= records <= LIMITS["max_page_count"], "journal_record_count", "INCOMPLETE")
    require(1 <= database_pages <= LIMITS["max_page_count"] and page == LIMITS["page_size"],
            "journal_page_geometry")
    require(512 <= sector <= LIMITS["journal_sector_max"] and sector & (sector - 1) == 0,
            "journal_sector_geometry")
    return {"records": records, "nonce": nonce, "database_pages": database_pages,
            "sector_bytes": sector, "page_size": page}


def journal_checksum(page, nonce):
    value = nonce
    for index in range(len(page) - 200, 0, -200):
        value = (value + page[index]) & 0xffffffff
    return value


def audit_journal(path, baseline_path, budget):
    """Exactly one finite header; no synthetic header or indefinite nRec accepted."""
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and 28 <= info.st_size <= LIMITS["journal_bytes"], "journal_file_shape")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    base = os.open(baseline_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fields = header_fields(os.pread(fd, 28, 0))
        size = fields["sector_bytes"] + fields["records"] * (fields["page_size"] + 8)
        require(info.st_size == size, "journal_extent_or_additional_header")
        base_info = os.fstat(base)
        require(base_info.st_size == fields["database_pages"] * fields["page_size"], "journal_baseline_extent")
        seen = set()
        for index in range(fields["records"]):
            offset = fields["sector_bytes"] + index * (fields["page_size"] + 8)
            raw = os.pread(fd, fields["page_size"] + 8, offset)
            budget.read(len(raw))
            require(len(raw) == fields["page_size"] + 8, "journal_record_truncated")
            number = struct.unpack(">I", raw[:4])[0]
            require(1 <= number <= fields["database_pages"] and number not in seen, "journal_page_number")
            seen.add(number)
            page = raw[4:-4]
            require(journal_checksum(page, fields["nonce"]) == struct.unpack(">I", raw[-4:])[0],
                    "journal_checksum_mismatch")
            original = os.pread(base, fields["page_size"], (number - 1) * fields["page_size"])
            budget.read(len(original))
            require(original == page, "journal_original_page_mismatch")
        require(signature(info) == signature(os.fstat(fd))
                == signature(path.lstat()) and signature(base_info) == signature(os.fstat(base)), "journal_audit_file_changed")
        fields.update({"headers": 1, "all_checksums_valid": True, "all_pages_match_baseline": True,
                       "bytes": info.st_size, "fsync_completion_established": False,
                       "power_loss_durability_established": False})
        return fields
    finally:
        os.close(fd)
        os.close(base)


def worker_terminal(row):
    require(type(row) is dict and set(row) == {"event", "status", "reason", "phase", "proof_execution_started"},
            "worker_terminal_shape")
    require(row["event"] == "worker_terminal" and row["status"] in
            ("FAILED", "INCOMPLETE", "REGISTRY_REVIEW_REQUIRED") and type(row["reason"]) is str
            and len(row["reason"]) <= 4096, "worker_terminal_values")
    require(row["phase"] in ("worker_preflight", "worker_physical")
            and type(row["proof_execution_started"]) is bool
            and row["proof_execution_started"] == (row["phase"] == "worker_physical"), "worker_terminal_phase")
    return Refused(row["reason"], row["status"], phase=row["phase"],
                   proof_started=row["proof_execution_started"])


class Events:
    def __init__(self, fd, output_fd):
        self.fd, self.output_fd = fd, output_fd
        self.pending = bytearray()
        self.rows, self.bytes, self.eof, self.terminals = [], 0, False, 0
        os.set_blocking(fd, False)

    def drain(self):
        while not self.eof:
            try:
                raw = os.read(self.fd, 16384)
            except BlockingIOError:
                break
            if not raw:
                self.eof = True
                break
            self.bytes += len(raw)
            require(self.bytes <= LIMITS["events_bytes"], "events_capacity")
            offset = 0
            while offset < len(raw):
                count = os.write(self.output_fd, raw[offset:])
                require(count > 0, "events_short_write")
                offset += count
            self.pending.extend(raw)
            while b"\n" in self.pending:
                line, _, rest = self.pending.partition(b"\n")
                self.pending = bytearray(rest)
                require(len(line) <= 16384 and len(self.rows) < LIMITS["event_count"], "event_shape_capacity")
                row = parse_json(line)
                require(type(row) is dict, "worker_event_not_object")
                if row.get("event") == "worker_terminal":
                    worker_terminal(row)
                    self.terminals += 1
                    require(self.terminals == 1, "duplicate_worker_terminal")
                self.rows.append(row)
            require(len(self.pending) <= 16384, "unterminated_event_capacity")

    def find(self, name):
        for row in self.rows:
            if row.get("event") == "worker_terminal":
                raise worker_terminal(row)
            if row.get("event") == name:
                return row
        return None

    def wait(self, name, process, budget):
        while True:
            budget.check()
            self.drain()
            row = self.find(name)
            if row is not None:
                return row
            if process.poll() is not None:
                self.drain()  # Pick up a terminal written between the first drain and exit.
                row = self.find(name)
                if row is not None:
                    return row
                raise Refused("writer_exited_before_" + name, "INCOMPLETE")
            select.select([self.fd], [], [], .01)


def kill_and_reap(process, deadline):
    """Use only the retained original Popen, never a reconstructed PID target."""
    if process.poll() is not None:
        return {"kill_sent": False, "returncode": process.returncode, "reaped": True}
    process.kill()
    remaining = deadline - time.monotonic()
    require(remaining > 0, "cleanup_deadline", "UNREAPED")
    try:
        returncode = process.wait(timeout=remaining)
    except Exception as exc:
        raise Refused("original_writer_reap_unconfirmed:" + type(exc).__name__, "UNREAPED") from exc
    return {"kill_sent": True, "returncode": returncode, "reaped": True}


def seal_family(directory, fences):
    fences.check()
    scan = scan_family(directory)
    for name in scan["files"]:
        os.chmod(directory / name, 0o400, follow_symlinks=False)
    os.chmod(directory, 0o500, follow_symlinks=False)
    fences.check()
    return scan_family(directory)


def capture_window(process, events, crash, baseline_page, budget, observe, fences):
    deadline = min(budget.deadline, time.monotonic() + LIMITS["poll_seconds"])
    maximum = 0
    for poll in range(LIMITS["poll_count"]):
        budget.check()
        fences.check()
        require(time.monotonic() < deadline, "journal_window_deadline", "INCOMPLETE")
        events.drain()
        events.find("__no_such_event__")  # Propagate a structured worker refusal.
        require(process.poll() is None and not any(r.get("event") == "commit_finished" for r in events.rows),
                "commit_window_missed", "INCOMPLETE")
        try:
            scan = scan_family(crash)
        except FileNotFoundError as exc:
            raise Refused("journal_disappeared_during_observation", "INCOMPLETE") from exc
        maximum = max(maximum, scan["allocated_charge"])
        observe(scan)
        journal = crash / (DB_NAME + "-journal")
        try:
            fd = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except FileNotFoundError:
            time.sleep(.0005)
            continue
        try:
            raw = os.pread(fd, 28, 0)
            budget.read(len(raw))
        finally:
            os.close(fd)
        if len(raw) == 28 and raw[:8] == MAGIC:
            fields = header_fields(raw)
            db_fd = os.open(crash / DB_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                page = os.pread(db_fd, LIMITS["page_size"], 0)
                budget.read(len(page))
            finally:
                os.close(db_fd)
            if page != baseline_page and len(page) == LIMITS["page_size"]:
                fences.check()
                return {"poll": poll, "observed_header": fields, "observed_changed_page_one": True,
                        "max_observed_allocated_charge": maximum}
        time.sleep(.0005)
    raise Refused("journal_poll_capacity", "INCOMPLETE")


def publish_receipt(root, result, fences, deadline):
    """No success at the terminal name until cleanup and staged accounting pass.

    Both names already exist when replaced: the charged candidate replaces our
    charged non-success placeholder. No new file/name or validation follows the
    final replacement. This is not a hostile same-UID or power-loss guarantee.
    """
    target, pending = root / "records/result.json", root / "records/result.pending.json"
    placeholder = canonical({"status": "FINALIZATION_PENDING", "terminal": False}) + b"\n"
    result["receipt_protocol"] = "staged-accounted-placeholder-replacement-v2"
    raw = canonical(result) + b"\n"
    fences.check()
    write_new(target, placeholder, LIMITS["result_bytes"])
    write_new(pending, raw, LIMITS["result_bytes"])
    accounting = whole_charge(root, fences)  # Includes both closed, fsynced files.
    require(stable_hash(target, LIMITS["result_bytes"])[0] ==
            {"bytes": len(placeholder), "sha256": sha(placeholder)}, "receipt_placeholder_changed")
    require(stable_hash(pending, LIMITS["result_bytes"])[0] ==
            {"bytes": len(raw), "sha256": sha(raw)}, "staged_receipt_changed")
    fences.check()
    require(time.monotonic() < deadline, "receipt_finalization_deadline", "INCOMPLETE")
    answer = {**result, "accounting_before_publication_bound": accounting, "receipt_sha256": sha(raw)}
    fences.close()  # A close failure must leave the non-success placeholder.
    os.replace(pending, target)  # Last state change; no fallible gate follows.
    return answer


def note_cleanup(result, stage, exc):
    """Keep a primary error; cleanup must neither mask it nor permit success."""
    errors = result.setdefault("cleanup_errors", [])
    if len(errors) < 8:
        errors.append({"stage": stage, "type": type(exc).__name__, "reason": str(exc)[:256]})
    if result["status"] == "CORROBORATED_MATCH":
        result.update(status="FAILED", reason="cleanup_unconfirmed", phase="cleanup")


def run(args):
    import subprocess
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode, "invoke_with_I_S_B")
    started = time.monotonic()
    overall_deadline = started + LIMITS["owner_seconds"]
    budget = Budget(LIMITS["work_seconds"])
    cfg, config_raw = load_config(args.config)
    schema, work_chain = preflight(cfg, budget)  # No work directory or disk SQLite yet.
    validate_inherited_stdin()
    root = absolute(cfg["work_parent"]) / cfg["run_name"]
    require(not os.path.lexists(root), "run_root_already_exists")
    os.umask(0o077)
    os.mkdir(root, 0o700)
    for name in (*FAMILY_NAMES, "records"):
        os.mkdir(root / name, 0o700)
    require(ancestry(cfg["work_parent"]) == work_chain, "work_parent_changed")
    run_chain = ancestry(root)
    fences = None
    event_output = control_read_fd = control_write_fd = event_read_fd = event_write_fd = -1
    process, events, result = None, None, None
    maxima = {name: 0 for name in FAMILY_NAMES}
    try:
        fences = Fences(root)
        write_new(root / "records/config.json", config_raw, LIMITS["config_bytes"])
        event_output = os.open(root / "records/events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        control_read_fd, control_write_fd = os.pipe()
        event_read_fd, event_write_fd = os.pipe()
        argv = [cfg["interpreter"], "-I", "-S", "-B", str(Path(__file__).resolve(strict=True)),
                "_worker", "--config", str(root / "records/config.json"),
                "--config-sha256", sha(config_raw), "--control-fd", str(control_read_fd),
                "--fence-token", fences.token()]
        process = subprocess.Popen(argv, stdin=0, stdout=event_write_fd, stderr=event_write_fd,
            close_fds=True, pass_fds=(control_read_fd,), start_new_session=True,
            env={"LANG": "C", "LC_ALL": "C"})
        closing, control_read_fd = control_read_fd, -1
        os.close(closing)
        closing, event_write_fd = event_write_fd, -1
        os.close(closing)
        events = Events(event_read_fd, event_output)
        baseline = events.wait("fill_closed", process, budget)["baseline"]
        maxima["fill"] = baseline["max_observed_allocated_charge"]
        baseline_hash, _ = stable_hash(root / "fill" / DB_NAME, LIMITS["database_bytes"], budget)
        require(baseline_hash == baseline["database"], "baseline_hash_mismatch")
        copied = copy_family(root / "fill", root / "crash", budget, fences)
        require(copied == {DB_NAME: baseline_hash}, "baseline_copy_shape")
        seal_family(root / "fill", fences)
        fd = os.open(root / "fill" / DB_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            baseline_page = os.pread(fd, LIMITS["page_size"], 0)
        finally:
            os.close(fd)
        os.write(control_write_fd, b"crash_copy_ready\n")
        ready = events.wait("crash_ready", process, budget)
        maxima["crash"] = ready["scan"]["allocated_charge"]
        whole_charge(root, fences)
        os.write(control_write_fd, b"commit\n")
        def observed(scan):
            maxima["crash"] = max(maxima["crash"], scan["allocated_charge"])
        capture = capture_window(process, events, root / "crash", baseline_page, budget, observed, fences)
        termination = kill_and_reap(process, min(overall_deadline, time.monotonic() + LIMITS["cleanup_seconds"]))
        require(termination == {"kill_sent": True, "returncode": -signal.SIGKILL, "reaped": True},
                "original_writer_not_positively_killed_and_reaped", "INCOMPLETE")
        events.drain()
        events.find("__no_such_event__")
        require(not events.pending, "partial_writer_event", "INCOMPLETE")
        fences.check()
        crash_before = scan_family(root / "crash", run_chain)
        require(set(crash_before["files"]) == {DB_NAME, DB_NAME + "-journal"}, "interrupted_journal_missing", "INCOMPLETE")
        crash_hash, _ = stable_hash(root / "crash" / DB_NAME, LIMITS["database_bytes"], budget)
        require(crash_hash != baseline_hash, "interrupted_database_unchanged", "INCOMPLETE")
        journal = audit_journal(root / "crash" / (DB_NAME + "-journal"), root / "fill" / DB_NAME, budget)
        fences.check()
        sealed = seal_family(root / "crash", fences)
        preserved = {name: stable_hash(root / "crash" / name, LIMITS["journal_bytes"], budget)[0]
                     for name in sorted(sealed["files"])}
        recovery_input = copy_family(root / "crash", root / "recovery", budget, fences)
        require(recovery_input == preserved, "recovery_copy_mismatch")
        maxima["recovery"] = scan_family(root / "recovery")["allocated_charge"]
        whole_charge(root, fences)
        db = sqlite_open(root / "recovery", schema, budget, fences)
        try:
            recovery_checks = database_checks(db)
            recovery_oracle = logical_oracle(db, budget)
        finally:
            db.close()
        fences.check()
        recovery_hash, _ = stable_hash(root / "recovery" / DB_NAME, LIMITS["database_bytes"], budget)
        require(recovery_hash == baseline_hash and recovery_oracle == baseline["oracle"], "recovery_not_exact")
        require(set(scan_family(root / "recovery")["files"]) == {DB_NAME}, "recovery_journal_not_removed_by_sqlite")
        require(scan_family(root / "crash") == sealed, "preserved_crash_metadata_changed")
        require({name: stable_hash(root / "crash" / name, LIMITS["journal_bytes"], budget)[0]
                 for name in preserved} == preserved, "preserved_crash_bytes_changed")
        seal_family(root / "recovery", fences)
        # Recheck all exact runtime/source/config bindings after disk work.
        preflight(cfg, budget)
        require(ancestry(cfg["work_parent"]) == work_chain and ancestry(root) == run_chain, "final_ancestor_changed")
        result = {"status": "CORROBORATED_MATCH", "fresh_evidence": True,
            "phase": "physical_proof", "proof_execution_started": True,
            "production_qualification_changed": False, "runtime_matches_shipped": True,
            "historical_evidence_digest_reference": cfg["shipped_profile"]["evidence_sha256"],
            "config_sha256": sha(config_raw), "recipe_files": cfg["recipe_files"],
            "baseline": baseline, "capture": capture, "termination": termination,
            "journal_audit": journal, "preserved_crash": preserved,
            "recovery": {"database": recovery_hash, "oracle": recovery_oracle, "checks": recovery_checks},
            "max_observed_family_charges": maxima, "proof_accounting_before_receipt": whole_charge(root, fences),
            "owner_seconds": time.monotonic() - started, "read_bytes_controller": budget.read_bytes,
            "claims": {"process_crash_case": True, "power_loss": False, "independent_fsync_completion": False,
                       "universal_filesystem_quota": False, "hostile_same_uid_race_safety": False,
                       "rss_hard_bound": False, "real_producer_data": False,
                       "tiny_v1_v2_admission_gates": "SEPARATE_NOT_RUN"}}
    except BaseException as exc:
        result = {"status": getattr(exc, "status", "FAILED"),
                  "phase": getattr(exc, "phase", "physical_proof" if process is not None else "setup"),
                  "proof_execution_started": getattr(exc, "proof_execution_started", process is not None),
                  "exception_type": type(exc).__name__, "exception_notes": list(getattr(exc, "__notes__", ()))[:8],
                  "reason": getattr(exc, "reason", type(exc).__name__ + ":" + str(exc)),
                  "config_sha256": sha(config_raw), "fresh_evidence": False,
                  "production_qualification_changed": False, "run_root": str(root)}
    finally:
        if process is not None and process.poll() is None:
            try:
                cleanup = kill_and_reap(process, min(overall_deadline, time.monotonic() + LIMITS["cleanup_seconds"]))
                result["failure_cleanup"] = cleanup
            except BaseException as exc:
                result["status"] = "UNREAPED"
                result["cleanup_reason"] = str(exc)
                result["copies_and_recovery_forbidden"] = True
        if events is not None:
            try:
                events.drain()
                if result["status"] == "CORROBORATED_MATCH":
                    events.find("__no_such_event__")
            except BaseException as exc:
                result["event_drain_error"] = str(exc)
                if result["status"] == "CORROBORATED_MATCH":
                    result["status"] = "FAILED"
        if event_output >= 0:
            try:
                os.fsync(event_output)
            except OSError as exc:
                note_cleanup(result, "event_fsync", exc)
        for fd in (control_read_fd, control_write_fd, event_read_fd, event_write_fd, event_output):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError as exc:
                    note_cleanup(result, "owned_fd_close", exc)
    require(result is not None, "missing_terminal_result")
    if process is None:
        if fences is not None:
            try:
                fences.close()
            except BaseException as exc:
                note_cleanup(result, "setup_fences_close", exc)
        return result  # Do not attempt receipt publication after failed setup.
    try:
        if result["status"] == "CORROBORATED_MATCH":
            preflight(cfg, budget)
            fences.check()
        result = publish_receipt(root, result, fences, overall_deadline)
    except BaseException as exc:
        result = {"status": "FINALIZATION_FAILED", "phase": "receipt_finalization",
                "proof_execution_started": result.get("proof_execution_started", True),
                "reason": str(exc), "success_publication_unconfirmed": True,
                "config_sha256": sha(config_raw), "run_root": str(root)}
    if fences.fds:
        try:
            fences.close()
        except BaseException as exc:
            result = {"status": "FINALIZATION_FAILED", "phase": "receipt_finalization",
                      "reason": "directory_descriptor_close:" + str(exc), "success_publication_unconfirmed": True}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    make = sub.add_parser("configure", help="make a reviewed local config; never qualifies a runtime")
    make.add_argument("--product-root", required=True)
    make.add_argument("--work-parent", required=True)
    make.add_argument("--run-name", required=True)
    make.add_argument("--config-out", required=True)
    for name in ("preflight", "run", "_worker"):
        command = sub.add_parser(name)
        command.add_argument("--config", required=True)
        if name == "_worker":
            command.add_argument("--config-sha256", required=True)
            command.add_argument("--control-fd", required=True, type=int)
            command.add_argument("--fence-token", required=True)
    args = parser.parse_args(argv)
    worker_phase = {"phase": "worker_preflight", "proof_started": False}
    try:
        if args.mode == "configure":
            result = configure(args)
        elif args.mode == "_worker":
            result = worker(args, worker_phase)
        elif args.mode == "preflight":
            require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode, "invoke_with_I_S_B")
            cfg, raw = load_config(args.config)
            preflight(cfg, Budget())
            result = {"status": "PREFLIGHT_ONLY", "config_sha256": sha(raw),
                      "disk_sqlite_opened": False, "qualification_created": False}
        else:
            result = run(args)
    except BaseException as exc:
        result = {"status": getattr(exc, "status", "FAILED"),
                  "phase": "input_or_preflight", "proof_execution_started": False,
                  "reason": getattr(exc, "reason", type(exc).__name__ + ":" + str(exc)),
                  "qualification_created": False}
    if args.mode == "_worker":
        result = {"event": "worker_terminal", "status": result["status"],
                  "reason": result.get("reason", "writer_finished_without_capture"),
                  "phase": worker_phase["phase"],
                  "proof_execution_started": worker_phase["proof_started"]}
    print(canonical(result).decode("ascii"), flush=True)
    return 0 if result["status"] in ("CONFIGURED_NOT_EXECUTED", "PREFLIGHT_ONLY", "CORROBORATED_MATCH") else 2


if __name__ == "__main__":
    raise SystemExit(main())
