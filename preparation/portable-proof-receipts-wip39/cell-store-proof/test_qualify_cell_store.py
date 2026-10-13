#!/usr/bin/env python3
"""Unexecuted helper-test source. No real subprocess, SQLite, or product import.

All process/descriptor tests use explicit fakes. Synthetic header fixtures
exercise parser helpers and do not constitute a real rollback journal proof.
Run only after separate helper-test authorization.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve(strict=True).parent
SPEC = importlib.util.spec_from_file_location("portable_cellstore_recipe", HERE / "qualify_cell_store.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def configuration():
    return {"schema": "portable-cell-store-proof-config-v1", "product_commit": mod.PRODUCT_COMMIT,
        "product_root": "/example/product", "product_files": copy.deepcopy(mod.PRODUCT_FILES),
        "product_ast": dict(mod.AST_PINS), "recipe_files": {name: {"bytes": 1, "sha256": "0" * 64}
            for name in mod.RECIPE_CAPS}, "interpreter": "/example/python", "work_parent": "/example/secure",
        "run_name": "case-1", "runtime": {}, "shipped_profile": {}, "limits": dict(mod.LIMITS),
        "procedure": "matching-shipped-profile-only-v1"}


class FakeProcess:
    def __init__(self, returncode=None, wait_error=None):
        self.returncode = returncode
        self.calls = []
        self.wait_error = wait_error

    def poll(self):
        self.calls.append("poll")
        return self.returncode

    def kill(self):
        self.calls.append("kill")

    def wait(self, timeout):
        self.calls.append(("wait", timeout))
        if self.wait_error:
            raise self.wait_error
        self.returncode = -signal.SIGKILL
        return self.returncode


class ConfigTests(unittest.TestCase):
    def test_exact_shape(self):
        mod.validate_config(configuration())

    def test_unknown_and_missing_keys(self):
        for change in (lambda c: c.update(extra=True), lambda c: c.pop("runtime")):
            cfg = configuration()
            change(cfg)
            with self.assertRaises(mod.Refused):
                mod.validate_config(cfg)

    def test_source_limits_and_version_cannot_change(self):
        for key, value in (("product_commit", "0" * 40), ("product_files", {}),
                           ("product_ast", {}), ("limits", {}), ("procedure", "candidate-mode")):
            cfg = configuration()
            cfg[key] = value
            with self.assertRaises(mod.Refused):
                mod.validate_config(cfg)

    def test_paths_and_names_are_bounded(self):
        for path in ("relative", "/a/../b", "/" + "/".join(["a"] * 40), "/CHOOSE_LOCAL_ROOT"):
            cfg = configuration()
            cfg["work_parent"] = path
            with self.assertRaises(mod.Refused):
                mod.validate_config(cfg)
        for name in ("", "../other", "x" * 65, "has space", "/absolute"):
            cfg = configuration()
            cfg["run_name"] = name
            with self.assertRaises(mod.Refused):
                mod.validate_config(cfg)

    def test_duplicate_and_nonfinite_json_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.assertRaises(mod.Refused):
                mod.parse_json(raw)

    def test_template_is_deliberately_nonrunnable(self):
        cfg = json.loads((HERE / "config.template.json").read_text())
        with self.assertRaises(mod.Refused):
            mod.validate_config(cfg)

    def test_schema_fixed_limits_match_driver(self):
        schema = json.loads((HERE / "config.schema.json").read_text())
        self.assertEqual(schema["properties"]["limits"]["const"], mod.LIMITS)
        self.assertEqual(set(schema["required"]), mod.CONFIG_KEYS)

    def test_runtime_mismatch_is_registry_review(self):
        profile = {key: "same" for key in mod.FINGERPRINT_KEYS}
        profile.update({key: "same" for key in ("sqlite_source_id", "sqlite_version",
            "compile_options_sha256", "platform", "vfs", "filesystem_device")})
        profile.update(sector_bytes_max=65536, journal_headers_max=1, allocation_slack_bytes_max=2097152)
        observed = {key: profile[key] for key in ("sqlite_source_id", "sqlite_version",
            "compile_options_sha256", "platform", "vfs", "filesystem_device")}
        observed["fingerprint"] = {key: profile[key] for key in mod.FINGERPRINT_KEYS}
        mod.match_shipped(observed, profile)
        for key in ("sqlite_source_id", "compile_options_sha256", "filesystem_device"):
            altered = copy.deepcopy(observed)
            altered[key] = "different"
            with self.assertRaises(mod.Refused) as error:
                mod.match_shipped(altered, profile)
            self.assertEqual(error.exception.status, "REGISTRY_REVIEW_REQUIRED")
        altered = copy.deepcopy(observed)
        altered["fingerprint"]["interpreter_realpath_sha256"] = "different"
        with self.assertRaises(mod.Refused) as error:
            mod.match_shipped(altered, profile)
        self.assertEqual(error.exception.status, "REGISTRY_REVIEW_REQUIRED")


class JournalTests(unittest.TestCase):
    @staticmethod
    def header(records=1, nonce=7, pages=2, sector=512, page=4096):
        return mod.MAGIC + struct.pack(">IIIII", records, nonce, pages, sector, page)

    def test_valid_finite_header(self):
        self.assertEqual(mod.header_fields(self.header()), {"records": 1, "nonce": 7,
            "database_pages": 2, "sector_bytes": 512, "page_size": 4096})

    def test_zero_and_indefinite_record_count(self):
        for count in (0, 0xffffffff, 6145):
            with self.assertRaises(mod.Refused):
                mod.header_fields(self.header(records=count))

    def test_zero_truncated_and_wrong_magic(self):
        for raw in (bytes(28), self.header()[:27], b"WRONGMAG" + self.header()[8:]):
            with self.assertRaises(mod.Refused):
                mod.header_fields(raw)

    def test_sector_and_page_geometry(self):
        for sector in (0, 256, 513, 131072):
            with self.assertRaises(mod.Refused):
                mod.header_fields(self.header(sector=sector))
        for page in (0, 512, 8192):
            with self.assertRaises(mod.Refused):
                mod.header_fields(self.header(page=page))
        for pages in (0, 6145):
            with self.assertRaises(mod.Refused):
                mod.header_fields(self.header(pages=pages))

    def test_checksum_positions_and_wrap(self):
        page = bytearray(4096)
        positions = list(range(3896, 0, -200))
        for index in positions:
            page[index] = 255
        self.assertEqual(mod.journal_checksum(page, 0xffffffff),
                         (0xffffffff + 255 * len(positions)) & 0xffffffff)
        unchanged = mod.journal_checksum(page, 123)
        page[0] = 99
        self.assertEqual(mod.journal_checksum(page, 123), unchanged)
        page[3896] ^= 1
        self.assertNotEqual(mod.journal_checksum(page, 123), unchanged)

    def test_audit_rejects_extra_header_and_verifies_all_pages(self):
        source = (HERE / "qualify_cell_store.py").read_text()
        tree = ast.parse(source)
        audit = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "audit_journal")
        text = ast.get_source_segment(source, audit)
        for required in ("info.st_size == size", "number not in seen", "journal_checksum(page",
                         "original == page", '"headers": 1'):
            self.assertIn(required, text)


class OwnershipTests(unittest.TestCase):
    def test_exact_retained_handle_killed_then_reaped(self):
        process = FakeProcess()
        with patch.object(mod.time, "monotonic", return_value=10):
            result = mod.kill_and_reap(process, 20)
        self.assertEqual(process.calls, ["poll", "kill", ("wait", 10)])
        self.assertEqual(result, {"kill_sent": True, "returncode": -signal.SIGKILL, "reaped": True})

    def test_already_exited_is_not_crash_capture(self):
        process = FakeProcess(returncode=0)
        result = mod.kill_and_reap(process, 20)
        self.assertEqual(process.calls, ["poll"])
        self.assertFalse(result["kill_sent"])

    def test_failed_reap_is_not_cleanup_success(self):
        process = FakeProcess(wait_error=TimeoutError("synthetic timeout"))
        with patch.object(mod.time, "monotonic", return_value=10):
            with self.assertRaises(mod.Refused) as error:
                mod.kill_and_reap(process, 20)
        self.assertEqual(error.exception.status, "UNREAPED")

    def test_inherited_linux_dev_null_no_open(self):
        good = SimpleNamespace(st_mode=stat.S_IFCHR | 0o666, st_rdev=os.makedev(1, 3))
        calls = []
        def fstat(fd):
            calls.append(("fstat", fd))
            return good
        def readlink(path):
            calls.append(("readlink", path))
            return "/dev/null"
        with patch.object(mod.sys, "platform", "linux"), patch.object(mod.os, "open", side_effect=AssertionError("must not open")):
            self.assertEqual(mod.validate_inherited_stdin(fstat, readlink), 0)
        self.assertEqual(calls, [("fstat", 0), ("readlink", "/proc/self/fd/0")])

    def test_non_null_stdin_rejected(self):
        for mode, device, link in ((stat.S_IFREG, os.makedev(1, 3), "/dev/null"),
                                   (stat.S_IFCHR, os.makedev(1, 5), "/dev/zero"),
                                   (stat.S_IFCHR, os.makedev(1, 3), "/different")):
            info = SimpleNamespace(st_mode=mode, st_rdev=device)
            with patch.object(mod.sys, "platform", "linux"), self.assertRaises(mod.Refused):
                mod.validate_inherited_stdin(lambda fd: info, lambda path: link)

    def test_popen_uses_inherited_zero_and_no_devnull(self):
        tree = ast.parse((HERE / "qualify_cell_store.py").read_bytes())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "Popen"]
        self.assertEqual(len(calls), 1)
        stdin = next(k.value for k in calls[0].keywords if k.arg == "stdin")
        self.assertIsInstance(stdin, ast.Constant)
        self.assertEqual(stdin.value, 0)
        self.assertFalse(any(isinstance(n, ast.Attribute) and n.attr == "DEVNULL" for n in ast.walk(tree)))


class StaticBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (HERE / "qualify_cell_store.py").read_text()
        cls.tree = ast.parse(cls.source)
        cls.functions = {n.name: n for n in cls.tree.body if isinstance(n, ast.FunctionDef)}

    def function_text(self, name):
        return ast.get_source_segment(self.source, self.functions[name])

    def test_no_product_import_or_registry_assignment(self):
        imports = [n for n in ast.walk(self.tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any(isinstance(n, ast.ImportFrom) and (n.module or "").startswith("audio_backend") for n in imports))
        self.assertFalse(any(isinstance(n, ast.Import) and any(a.name.startswith("audio_backend") for a in n.names) for n in imports))
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                             and n.func.id == "RuntimeQualification" for n in ast.walk(self.tree)))

    def test_preflight_before_work_directory_or_process(self):
        text = self.function_text("run")
        self.assertLess(text.index("preflight(cfg, budget)"), text.index("os.mkdir(root"))
        self.assertLess(text.index("validate_inherited_stdin()"), text.index("subprocess.Popen"))
        worker = self.function_text("worker")
        self.assertLess(worker.index("preflight(cfg, budget)"), worker.index("fill_database("))

    def test_reap_before_preserve_or_recover(self):
        text = self.function_text("run")
        reap = text.index("termination = kill_and_reap")
        positive = text.index('"original_writer_not_positively_killed_and_reaped"')
        self.assertLess(reap, positive)
        self.assertLess(positive, text.index('sealed = seal_family(root / "crash", fences)'))
        self.assertLess(positive, text.index('copy_family(root / "crash", root / "recovery"'))
        self.assertLess(positive, text.index('sqlite_open(root / "recovery"'))

    def test_three_families_and_exact_budget(self):
        self.assertEqual(mod.FAMILY_NAMES, ("fill", "crash", "recovery"))
        self.assertEqual(3 * mod.LIMITS["family_bytes"] + mod.LIMITS["records_bytes"], mod.LIMITS["total_bytes"])
        self.assertEqual(mod.LIMITS["database_bytes"] + mod.LIMITS["journal_bytes"] + mod.LIMITS["slack_bytes"], mod.LIMITS["envelope_bytes"])
        bound = 6144 * (4096 + 8) + 65536
        self.assertLessEqual(bound, mod.LIMITS["journal_bytes"])

    def test_logical_and_allocated_charge_take_max(self):
        self.assertEqual(mod.charge(SimpleNamespace(st_size=10000, st_blocks=1)), 10000)
        self.assertEqual(mod.charge(SimpleNamespace(st_size=1, st_blocks=20)), 10240)
        with self.assertRaises(mod.Refused):
            mod.charge(SimpleNamespace(st_size=1))

    def test_fences_have_no_sticky_exception(self):
        text = self.function_text("ancestry")
        self.assertIn("os.O_NOFOLLOW", text)
        self.assertIn("not info.st_mode & 0o022", text)
        self.assertNotIn("S_ISVTX", text)
        family = self.function_text("scan_family")
        for item in ("st_nlink == 1", "st_uid == os.getuid()", "st_dev == info.st_dev", "not row.st_mode & 0o077"):
            self.assertIn(item, family)

    def test_missed_window_is_incomplete(self):
        text = self.function_text("capture_window")
        self.assertIn('"commit_window_missed", "INCOMPLETE"', text)
        self.assertIn('"journal_poll_capacity", "INCOMPLETE"', text)
        self.assertIn('"journal_window_deadline", "INCOMPLETE"', text)

    def test_runtime_binding_checks_precede_disk_open(self):
        text = self.function_text("runtime_observation")
        for item in ('spec.origin == "built-in"', "spec.loader is BuiltinImporter",
                     'getattr(_sqlite3, "__file__", None) is None', '"/proc/self/exe"',
                     '"/proc/self/maps"', 'sqlite3.connect(":memory:"'):
            self.assertIn(item, text)
        self.assertNotIn("sqlite_open(", text)

    def test_source_inventory_caps(self):
        sizes = {name: (HERE / name).stat().st_size for name in mod.RECIPE_CAPS}
        self.assertEqual(set(sizes), set(mod.RECIPE_CAPS))
        for name, size in sizes.items():
            self.assertGreater(size, 0)
            self.assertLessEqual(size, mod.RECIPE_CAPS[name])
        self.assertLessEqual(sum(sizes.values()), 124 * 1024)


class PersistentFenceTests(unittest.TestCase):
    def fixture(self):
        guard = mod.Fences.__new__(mod.Fences)
        guard.root = Path("/example/run")
        guard.chain = [[27, 40, 1000, 0o700]]
        names = ("", *mod.FAMILY_NAMES, "records")
        guard.fds = {name: 10 + i for i, name in enumerate(names)}
        guard.identities = {name: [27, 50 + i] for i, name in enumerate(names)}
        held, entries = {}, {}
        for name, fd in guard.fds.items():
            info = dict(st_dev=27, st_ino=guard.identities[name][1], st_uid=1000, st_mode=stat.S_IFDIR | 0o700)
            held[fd], entries[name] = SimpleNamespace(**info), SimpleNamespace(**info)
        def entry(name, **kwargs):
            return entries["" if isinstance(name, Path) else name]
        return guard, held, entries, entry

    def check(self, guard, held, entry, chain=None):
        with patch.object(mod, "ancestry", return_value=guard.chain if chain is None else chain), \
             patch.object(mod.os, "fstat", side_effect=lambda fd: held[fd]), \
             patch.object(mod.os, "stat", side_effect=entry), patch.object(mod.os, "getuid", return_value=1000):
            guard.check()

    def test_unchanged_and_explicit_readonly_seal(self):
        guard, held, entries, entry = self.fixture()
        self.check(guard, held, entry)
        for name in mod.FAMILY_NAMES:
            held[guard.fds[name]].st_mode = entries[name].st_mode = stat.S_IFDIR | 0o500
        self.check(guard, held, entry)

    def test_each_family_and_root_replacement_rejected(self):
        for name in ("", *mod.FAMILY_NAMES, "records"):
            with self.subTest(name=name):
                guard, held, entries, entry = self.fixture()
                entries[name].st_ino += 1
                with self.assertRaises(mod.Refused) as error:
                    self.check(guard, held, entry)
                self.assertEqual(error.exception.reason, "persistent_directory_replaced")

    def test_ancestor_change_rejected(self):
        guard, held, entries, entry = self.fixture()
        with self.assertRaises(mod.Refused) as error:
            self.check(guard, held, entry, [[27, 999, 1000, 0o700]])
        self.assertEqual(error.exception.reason, "persistent_ancestor_changed")

    def test_permission_owner_and_symlink_drift_rejected(self):
        for changes in ({"st_uid": 1001}, {"st_mode": stat.S_IFDIR | 0o702},
                        {"st_mode": stat.S_IFLNK | 0o700}):
            guard, held, entries, entry = self.fixture()
            for key, value in changes.items():
                setattr(entries["crash"], key, value)
            with self.assertRaises(mod.Refused):
                self.check(guard, held, entry)

    def test_descriptor_close_once_even_if_one_close_fails(self):
        guard, held, entries, entry = self.fixture()
        expected = list(guard.fds.values())
        calls = []
        def close(fd):
            calls.append(fd)
            if fd == expected[0]:
                raise OSError("synthetic close uncertainty")
        with patch.object(mod.os, "close", side_effect=close):
            with self.assertRaises(mod.Refused):
                guard.close()
            guard.close()
        self.assertEqual(calls, expected)
        self.assertEqual(guard.fds, {})

    def test_fences_surround_every_disk_boundary_in_source(self):
        source = (HERE / "qualify_cell_store.py").read_text()
        tree = ast.parse(source)
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in ("sqlite_open", "copy_family", "seal_family", "capture_window", "whole_charge"):
            text = ast.get_source_segment(source, functions[name])
            self.assertGreaterEqual(text.count("fences.check()"), 2, name)
        worker = ast.get_source_segment(source, functions["worker"])
        self.assertLess(worker.index("Fences(root, args.fence_token)"), worker.index("fill_database("))
        self.assertIn("with Fences(root, args.fence_token) as fences:", worker)


class WorkerTerminalTests(unittest.TestCase):
    @staticmethod
    def row():
        return {"event": "worker_terminal", "status": "REGISTRY_REVIEW_REQUIRED",
                "reason": "configured_runtime_changed", "phase": "worker_preflight",
                "proof_execution_started": False}

    def test_preflight_refusal_keeps_its_phase_and_status(self):
        error = mod.worker_terminal(self.row())
        self.assertEqual(error.status, "REGISTRY_REVIEW_REQUIRED")
        self.assertEqual(error.phase, "worker_preflight")
        self.assertFalse(error.proof_execution_started)
        events = mod.Events.__new__(mod.Events)
        events.rows = [self.row()]
        with self.assertRaises(mod.Refused) as caught:
            events.find("fill_closed")
        self.assertEqual(caught.exception.status, error.status)

    def test_malformed_phase_success_and_unknown_fields_rejected(self):
        for change in ({"phase": "worker_physical"}, {"proof_execution_started": 0},
                       {"status": "CORROBORATED_MATCH"}, {"extra": True}, {"reason": "x" * 4097}):
            row = self.row()
            row.update(change)
            with self.assertRaises(mod.Refused):
                mod.worker_terminal(row)

    def test_duplicate_terminal_rejected_without_real_io(self):
        raw = (mod.canonical(self.row()) + b"\n") * 2
        with patch.object(mod.os, "set_blocking"), patch.object(mod.os, "read", side_effect=[raw, b""]), \
             patch.object(mod.os, "write", side_effect=lambda fd, data: len(data)):
            events = mod.Events(99, 100)
            with self.assertRaises(mod.Refused) as error:
                events.drain()
        self.assertEqual(error.exception.reason, "duplicate_worker_terminal")

    def test_exit_redrain_preserves_late_preflight_refusal(self):
        events = mod.Events.__new__(mod.Events)
        events.rows, events.fd = [], 99
        drains = []
        def drain():
            drains.append(True)
            if len(drains) == 2:
                events.rows.append(self.row())
        events.drain = drain
        with self.assertRaises(mod.Refused) as error:
            events.wait("fill_closed", FakeProcess(returncode=2), SimpleNamespace(check=lambda: None))
        self.assertEqual(len(drains), 2)
        self.assertEqual(error.exception.status, "REGISTRY_REVIEW_REQUIRED")

    def test_unexplained_exit_remains_incomplete(self):
        events = mod.Events.__new__(mod.Events)
        events.rows, events.fd = [], 99
        events.drain = lambda: None
        with self.assertRaises(mod.Refused) as error:
            events.wait("fill_closed", FakeProcess(returncode=2), SimpleNamespace(check=lambda: None))
        self.assertEqual(error.exception.status, "INCOMPLETE")


class ReceiptPublicationTests(unittest.TestCase):
    def exercise(self, failure=None, oversized=False):
        files, order = {}, []
        root = Path("/example/run")
        class Guard:
            def check(self):
                order.append("fence")
            def close(self):
                order.append("close")
                if failure == "close":
                    raise mod.Refused("synthetic_close_failure")
        def write(path, raw, maximum):
            order.append("write:" + path.name)
            mod.require(len(raw) <= maximum, "output_capacity")
            self.assertNotIn(path.name, files)
            files[path.name] = raw
            if failure == "fsync" and path.name == "result.pending.json":
                raise OSError("synthetic fsync failure")
        def accounting(path, guard):
            order.append("account")
            self.assertEqual(set(files), {"result.json", "result.pending.json"})
            self.assertEqual(json.loads(files["result.json"])["status"], "FINALIZATION_PENDING")
            if failure == "accounting":
                raise mod.Refused("synthetic_records_capacity")
            return {"total_charge": 1000, "includes_both_receipts": True}
        def hashed(path, maximum):
            raw = files[path.name]
            value = mod.sha(raw)
            if failure == "tamper" and path.name == "result.json":
                value = "0" * 64
            return {"bytes": len(raw), "sha256": value}, None
        def replace(source, destination):
            order.append("publish")
            if failure == "publication":
                raise PermissionError("synthetic publication failure")
            files[destination.name] = files.pop(source.name)
        result = {"status": "CORROBORATED_MATCH", "synthetic_helper_fixture": True}
        if oversized:
            result["padding"] = "x" * mod.LIMITS["result_bytes"]
        error, answer = None, None
        with patch.object(mod, "write_new", side_effect=write), patch.object(mod, "whole_charge", side_effect=accounting), \
             patch.object(mod, "stable_hash", side_effect=hashed), patch.object(mod.os, "replace", side_effect=replace), \
             patch.object(mod.time, "monotonic", return_value=20 if failure == "deadline" else 0):
            try:
                answer = mod.publish_receipt(root, result, Guard(), 10)
            except BaseException as exc:
                error = exc
        return files, order, error, answer

    def test_success_only_after_accounting_and_close(self):
        files, order, error, answer = self.exercise()
        self.assertIsNone(error)
        self.assertEqual(set(files), {"result.json"})
        self.assertEqual(json.loads(files["result.json"])["status"], "CORROBORATED_MATCH")
        self.assertEqual(order[-2:], ["close", "publish"])
        self.assertLess(order.index("account"), order.index("publish"))
        self.assertTrue(answer["accounting_before_publication_bound"]["includes_both_receipts"])

    def test_accounting_fsync_close_tamper_deadline_and_publication_failures(self):
        for failure in ("accounting", "fsync", "close", "tamper", "deadline", "publication"):
            with self.subTest(failure=failure):
                files, order, error, answer = self.exercise(failure=failure)
                self.assertIsNotNone(error)
                self.assertIsNone(answer)
                self.assertEqual(json.loads(files["result.json"])["status"], "FINALIZATION_PENDING")
                if failure != "publication":
                    self.assertNotIn("publish", order)

    def test_oversized_candidate_never_publishes(self):
        files, order, error, answer = self.exercise(oversized=True)
        self.assertIsNotNone(error)
        self.assertNotIn("publish", order)
        self.assertEqual(json.loads(files["result.json"])["status"], "FINALIZATION_PENDING")

    def test_no_gate_or_state_change_follows_publication_in_source(self):
        tree = ast.parse((HERE / "qualify_cell_store.py").read_bytes())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "publish_receipt")
        last_call = function.body[-2]
        self.assertIsInstance(last_call, ast.Expr)
        self.assertEqual(last_call.value.func.attr, "replace")
        self.assertIsInstance(function.body[-1], ast.Return)
        self.assertIsInstance(function.body[-1].value, ast.Name)


class SetupCleanupTests(unittest.TestCase):
    def exercise(self, stage, secondary=False, refusal=False):
        class SetupFailure(Exception):
            pass
        original = (mod.Refused("setup_identity_refusal", "REGISTRY_REVIEW_REQUIRED") if refusal
                    else SetupFailure("injected_" + stage))
        acquired, closed, pipes, guards = [], [], [], []
        cfg = configuration()
        class Guard:
            def __init__(self, root):
                if stage == "fences":
                    raise original
                self.fds = {"root": 99}
                self.closed = 0
                guards.append(self)
            def token(self):
                return "f" * 64
            def close(self):
                self.closed += 1
                self.fds.clear()
                if secondary:
                    raise OSError("secondary_fence_close")
        def write(path, raw, maximum):
            self.assertEqual(path.name, "config.json")
            if stage == "config":
                raise original
        def opened(path, flags, mode):
            self.assertEqual(path.name, "events.jsonl")
            if stage == "event_open":
                raise original
            acquired.append(100)
            return 100
        def pipe():
            which = "control_pipe" if not pipes else "event_pipe"
            if stage == which:
                raise original
            pair = (101, 102) if not pipes else (103, 104)
            pipes.append(pair)
            acquired.extend(pair)
            return pair
        def close(fd):
            closed.append(fd)
            if secondary and fd == 100:
                raise OSError("secondary_fd_close")
        def fsync(fd):
            self.assertEqual(fd, 100)
            if secondary:
                raise OSError("secondary_fsync")
        def popen(*args, **kwargs):
            self.assertEqual(stage, "popen")
            raise original
        fake_sys = SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True), dont_write_bytecode=True)
        with patch.object(mod, "sys", fake_sys), patch.object(mod, "load_config", return_value=(cfg, b"{}\n")), \
             patch.object(mod, "preflight", return_value=((), [])), patch.object(mod, "validate_inherited_stdin", return_value=0), \
             patch.object(mod, "ancestry", return_value=[]), patch.object(mod, "Fences", Guard), \
             patch.object(mod, "write_new", side_effect=write), patch.object(mod.os.path, "lexists", return_value=False), \
             patch.object(mod.os, "umask"), patch.object(mod.os, "mkdir"), \
             patch.object(mod.os, "open", side_effect=opened), patch.object(mod.os, "pipe", side_effect=pipe), \
             patch.object(mod.os, "close", side_effect=close), patch.object(mod.os, "fsync", side_effect=fsync), \
             patch("subprocess.Popen", side_effect=popen), \
             patch.object(mod, "publish_receipt", side_effect=AssertionError("setup failure must not publish")):
            result = mod.run(SimpleNamespace(config="/example/config.json"))
        self.assertEqual(set(closed), set(acquired))
        self.assertEqual(len(closed), len(set(closed)))
        self.assertTrue(all(guard.closed == 1 and guard.fds == {} for guard in guards))
        self.assertEqual(result["phase"], "setup")
        self.assertFalse(result["proof_execution_started"])
        self.assertEqual(result["exception_type"], type(original).__name__)
        self.assertEqual(result["reason"], original.reason if refusal else "SetupFailure:injected_" + stage)
        self.assertEqual(result["status"], "REGISTRY_REVIEW_REQUIRED" if refusal else "FAILED")
        return result

    def test_each_owned_setup_acquisition_failure(self):
        for stage in ("fences", "config", "event_open", "control_pipe", "event_pipe", "popen"):
            with self.subTest(stage=stage):
                self.exercise(stage)

    def test_secondary_cleanup_errors_preserve_primary_setup_error(self):
        result = self.exercise("event_pipe", secondary=True)
        self.assertEqual([item["stage"] for item in result["cleanup_errors"]],
                         ["event_fsync", "owned_fd_close", "setup_fences_close"])
        result = self.exercise("config", secondary=True, refusal=True)
        self.assertEqual(result["cleanup_errors"][0]["stage"], "setup_fences_close")

    def test_fence_constructor_preserves_original_exception_object(self):
        original = PermissionError("injected directory open")
        closed = []
        def close(fd):
            closed.append(fd)
            if fd == 60:
                raise OSError("injected cleanup failure")
        with patch.object(mod, "ancestry", return_value=[]), \
             patch.object(mod.os, "open", side_effect=[60, 61, original]), \
             patch.object(mod.os, "close", side_effect=close):
            with self.assertRaises(PermissionError) as caught:
                mod.Fences(Path("/example/run"))
        self.assertIs(caught.exception, original)
        self.assertEqual(closed, [60, 61])
        self.assertEqual(original.__notes__, ["fence_setup_cleanup:Refused"])

    def test_config_write_preserves_original_when_its_close_also_fails(self):
        original = PermissionError("injected config write")
        with patch.object(mod, "ancestry", return_value=[]), \
             patch.object(mod.os, "open", return_value=60), \
             patch.object(mod.os, "write", side_effect=original), \
             patch.object(mod.os, "close", side_effect=OSError("injected close")) as closed:
            with self.assertRaises(PermissionError) as caught:
                mod.write_new(Path("/example/config.json"), b"x", 10)
        self.assertIs(caught.exception, original)
        closed.assert_called_once_with(60)
        self.assertEqual(original.__notes__, ["record_write_cleanup:OSError"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
