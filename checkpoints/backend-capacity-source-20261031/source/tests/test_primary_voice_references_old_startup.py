"""Exactly one separately gated unmodified dev61 interpreter, not a model run.

Required pins: PRIMARY_REFERENCE_DEV61_ROOT, PRIMARY_REFERENCE_DEV61_MANIFEST,
PRIMARY_REFERENCE_DEV61_MANIFEST_SHA256. The manifest's complete
audio_backend/**/*.py inventory must match the existing installed package.
The one child opens only a backup of one fresh synthetic dev62 reference DB.
Missing prerequisites fail; this test must never receive skip/pass credit.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from audio_backend import primary_voice_references as voice
from reviewed_person_bridge_fixtures import GROUP, env


def _pinned_dev61():
    names = (
        "PRIMARY_REFERENCE_DEV61_ROOT", "PRIMARY_REFERENCE_DEV61_MANIFEST",
        "PRIMARY_REFERENCE_DEV61_MANIFEST_SHA256",
    )
    assert all(os.environ.get(name) for name in names), "All three dev61 baseline pins are required"
    root, manifest = (Path(os.environ[name]) for name in names[:2])
    assert root.is_absolute() and manifest.is_absolute()
    assert root.is_dir() and manifest.is_file() and not root.is_symlink()
    assert manifest.stat().st_size <= 1024 * 1024
    raw = manifest.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == os.environ[names[2]]
    document = json.loads(raw)
    assert document["version"] == "0.2.0rc3.dev61"
    expected = {
        name: metadata for name, metadata in document["files"].items()
        if name.startswith("audio_backend/") and name.endswith(".py")
    }
    assert {
        "audio_backend/__init__.py", "audio_backend/storage.py",
        "audio_backend/settings.py", "audio_backend/recording_speaker_history.py",
        "audio_backend/reviewed_primary_persons.py",
    } <= set(expected)
    assert "audio_backend/primary_voice_references.py" not in expected
    assert 1 <= len(expected) <= 256
    assert {
        str(path.relative_to(root)) for path in (root / "audio_backend").rglob("*.py")
    } == set(expected)
    total = 0
    for relative, metadata in expected.items():
        path = root / relative
        assert ".." not in Path(relative).parts and not path.is_symlink()
        assert path.resolve().is_relative_to(root.resolve())
        assert 0 <= metadata["bytes"] <= 1024 * 1024
        assert path.stat().st_size == metadata["bytes"]
        total += metadata["bytes"]
        assert total <= 16 * 1024 * 1024
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]
    return root


def _logical_snapshot(path):
    assert path.stat().st_size <= 32 * 1024**2
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        schema = tuple(db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
        assert len(schema) <= 512
        metadata = tuple(
            (name, db.execute("PRAGMA " + name).fetchone()[0])
            for name in ("application_id", "user_version", "schema_version", "journal_mode"))
        tables = [row[1] for row in schema if row[0] == "table"]
        assert len(tables) <= 256
        contents, total_rows, total_bytes = [], 0, 0
        for name in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            records = []
            for row in db.execute("SELECT * FROM " + quoted):
                assert len(row) <= 128
                record = repr(tuple(row))
                total_rows += 1
                total_bytes += len(record.encode())
                assert total_rows <= 4096 and total_bytes <= 32 * 1024**2
                records.append(record)
            contents.append((name, tuple(sorted(records))))
    return schema, metadata, tuple(contents)


def test_unmodified_pinned_dev61_store_refuses_dev62_reference_schema_without_mutation(env, tmp_path):
    baseline = _pinned_dev61()
    env.activate()
    item = env.observe(pcm=True, vector=True)
    claim = env.associate(item)
    request = {
        **env.fence(), "association_event_id": claim["event_id"],
        "binding_sha256": claim["binding_sha256"],
        "certificate_id": item.certificate["certificate_id"],
        "vector_sha256": item.certificate["vector_sha256"],
        "space_id": item.node["space_id"],
        "attestation": deepcopy(voice.ATTESTATION),
    }
    voice.enroll(env.store, GROUP, request)
    target = tmp_path / "old-dev61-refusal.sqlite3"
    with sqlite3.connect(env.store.path) as source, sqlite3.connect(target) as copied:
        source.backup(copied)
    before = _logical_snapshot(target)
    script = r'''
import os, resource, sys
resource.setrlimit(resource.RLIMIT_AS, (512*1024**2, 512*1024**2))
resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
resource.setrlimit(resource.RLIMIT_FSIZE, (32*1024**2, 32*1024**2))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
sys.dont_write_bytecode = True
sys.pycache_prefix = os.path.join(os.path.dirname(sys.argv[2]), 'never_written_dev61_pycache')
assert not os.path.exists(sys.pycache_prefix)
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import audio_backend
import audio_backend.storage as old_storage
import audio_backend.settings as old_settings
assert audio_backend.__version__ == '0.2.0rc3.dev61'
root = Path(sys.argv[1]).resolve()
assert Path(old_storage.__file__).resolve().is_relative_to(root)
settings = old_settings.Settings(data_dir=Path(sys.argv[2]).parent,
    start_worker=False, disk_reserve_bytes=0, reviewed_primary_persons_enabled=True)
try:
    old_storage.Store(Path(sys.argv[2]), settings)
except ValueError as exc:
    assert str(exc) == 'Incompatible recording history schema', str(exc)
else:
    raise AssertionError('Unmodified dev61 unexpectedly accepted dev62 reference schema')
origins = [Path(m.__file__).resolve() for n,m in sys.modules.items()
    if (n == 'audio_backend' or n.startswith('audio_backend.')) and getattr(m,'__file__',None)]
assert origins and all(path.is_relative_to(root) for path in origins)
print('unmodified_dev61_refused_dev62_before_writer')
'''
    log = tmp_path / "old-dev61-refusal.log"
    with log.open("wb") as output:
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(baseline), str(target)],
            cwd=tmp_path,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
                 "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
            stdout=output, stderr=subprocess.STDOUT, timeout=20, check=False)
    assert log.stat().st_size <= 64 * 1024
    report = log.read_text()
    assert result.returncode == 0, report
    assert report.strip() == "unmodified_dev61_refused_dev62_before_writer"
    # Do not reopen with the new migration code before this complete comparison.
    assert _logical_snapshot(target) == before
    assert not (tmp_path / "never_written_dev61_pycache").exists()
