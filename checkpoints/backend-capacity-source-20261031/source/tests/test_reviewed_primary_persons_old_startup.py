"""One separately authorized old-binary child; excluded from the ordinary gate.

Explicit standalone prerequisites (absence is a failure, never skip credit):
  REVIEWED_PERSON_DEV60_ROOT: absolute pinned installed package import root.
  REVIEWED_PERSON_DEV60_MANIFEST: JSON with files[path] = {bytes, sha256}.
  REVIEWED_PERSON_DEV60_MANIFEST_SHA256: independently approved manifest digest.

Only audio_backend/**/*.py entries are admitted; their complete inventory and
bytes must match the supplied old installation. The test embeds no old code or
runtime and has no workspace-specific fixture reference. The child uses the
reviewed gate's interpreter and unmodified old Store/Settings modules. It opens
one backup of a fresh synthetic bridge DB, with models/workers disabled.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from reviewed_person_bridge_fixtures import env


def _pinned_baseline():
    required = (
        "REVIEWED_PERSON_DEV60_ROOT", "REVIEWED_PERSON_DEV60_MANIFEST",
        "REVIEWED_PERSON_DEV60_MANIFEST_SHA256",
    )
    assert all(os.environ.get(key) for key in required), (
        "The separately selected old-startup gate requires all three pinned baseline prerequisites")
    root, manifest = (Path(os.environ[key]) for key in required[:2])
    assert root.is_absolute() and manifest.is_absolute()
    assert root.is_dir() and manifest.is_file()
    assert manifest.stat().st_size <= 1024 * 1024
    raw = manifest.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == os.environ[required[2]]
    document = json.loads(raw)
    files = document["files"]
    expected = {name: metadata for name, metadata in files.items()
                if name.startswith("audio_backend/") and name.endswith(".py")}
    assert {"audio_backend/storage.py", "audio_backend/settings.py",
            "audio_backend/recording_speaker_history.py"} <= set(expected)
    assert "audio_backend/reviewed_primary_persons.py" not in expected
    assert 1 <= len(expected) <= 256
    actual = {str(path.relative_to(root)) for path in (root / "audio_backend").rglob("*.py")}
    assert actual == set(expected)
    total = 0
    for relative, metadata in expected.items():
        path = root / relative
        assert ".." not in Path(relative).parts and not path.is_symlink()
        assert 0 <= metadata["bytes"] <= 1024 * 1024
        assert path.stat().st_size == metadata["bytes"]
        total += metadata["bytes"]
        assert total <= 16 * 1024 * 1024
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]
    return root


def _complete_logical_schema_snapshot(path):
    """Snapshot every table and schema object in this bounded synthetic DB."""
    assert path.stat().st_size <= 32 * 1024**2
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        schema = tuple(db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
        assert len(schema) <= 512
        metadata = tuple((name, db.execute("PRAGMA " + name).fetchone()[0]) for name in
                         ("application_id", "user_version", "schema_version", "journal_mode"))
        tables = [row[1] for row in schema if row[0] == "table"]
        assert len(tables) <= 256
        content, total_rows, total_bytes = [], 0, 0
        for name in tables:
            quote = '"' + name.replace('"', '""') + '"'
            records = []
            for row in db.execute("SELECT * FROM " + quote):
                assert len(row) <= 128
                record = repr(tuple(row))
                total_rows += 1
                total_bytes += len(record.encode())
                assert total_rows <= 4096 and total_bytes <= 32 * 1024**2
                records.append(record)
            content.append((name, tuple(sorted(records))))
    return schema, metadata, tuple(content)


def test_unmodified_pinned_dev60_store_refuses_bridge_before_identity_writes(env, tmp_path):
    baseline = _pinned_baseline()
    env.activate()
    env.associate(env.observe())
    target = tmp_path / "old-startup.sqlite3"
    with sqlite3.connect(env.store.path) as source, sqlite3.connect(target) as copied:
        source.backup(copied)
    before = _complete_logical_schema_snapshot(target)
    script = r'''
import os, resource, sys
resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
# A 64-KiB file ceiling could stop SQLite before the intended schema guard.
# Admit a bounded 32-MiB database/WAL file; expected child log remains <=64 KiB.
resource.setrlimit(resource.RLIMIT_FSIZE, (32*1024**2, 32*1024**2))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
sys.dont_write_bytecode = True
sys.pycache_prefix = os.path.join(os.path.dirname(sys.argv[2]), 'never_written_pycache')
assert not os.path.exists(sys.pycache_prefix)
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import audio_backend.storage as old_storage
import audio_backend.settings as old_settings
assert Path(old_storage.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
settings = old_settings.Settings(data_dir=Path(sys.argv[2]).parent,
    start_worker=False, disk_reserve_bytes=0)
try:
    old_storage.Store(Path(sys.argv[2]), settings)
except ValueError as exc:
    assert str(exc) == 'Incompatible recording history schema', str(exc)
    print('old_dev60_refused_before_writer')
else:
    raise AssertionError('Unmodified old dev60 unexpectedly accepted bridge schema')
'''
    log = tmp_path / "old-startup.log"
    with log.open("wb") as output:
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(baseline), str(target)],
            cwd=tmp_path, env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
                               "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
            stdout=output, stderr=subprocess.STDOUT, timeout=20, check=False,
        )
    assert log.stat().st_size <= 64 * 1024
    report = log.read_text()
    assert result.returncode == 0, report
    assert report.strip() == "old_dev60_refused_before_writer"
    # Every history/provenance table and every schema object must be conserved.
    # Do not reopen the target with the new migration implementation.
    assert _complete_logical_schema_snapshot(target) == before
