"""Small in-memory oracle controls only; not physical qualification."""
import importlib.util
from pathlib import Path
import sqlite3

from audio_backend.native_owned_cell_store import CellStore

spec=importlib.util.spec_from_file_location('cellstore_physical_fixture',Path(__file__).resolve().parents[1]/'qualification/cellstore_crash_probe.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


def test_oracle_covers_master_sequence_columns_and_type():
    db=sqlite3.connect(':memory:',isolation_level=None)
    try:
        CellStore._schema(db)
        first=p.typed_oracle(db)
        assert set(first['tables'])=={'sqlite_master','sqlite_sequence','store_meta','sources','cells','dependencies'}
        assert first['tables']['sqlite_sequence']['columns']==['name','seq']
        db.execute("INSERT INTO sqlite_sequence VALUES('synthetic',1)")
        changed=p.typed_oracle(db)
        assert first['tables']['sqlite_sequence']!=changed['tables']['sqlite_sequence']
        db.execute("UPDATE sqlite_sequence SET seq=CAST('1' AS TEXT)")
        assert p.typed_oracle(db)['tables']['sqlite_sequence']!=changed['tables']['sqlite_sequence']
    finally:db.close()


def test_production_pragmas_and_limits(tmp_path):
    db=p.connect(tmp_path/'fresh.sqlite3')
    try:
        assert db.execute('PRAGMA auto_vacuum').fetchone()[0]==0
        assert db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)==p.HANDOFF_BYTES
        assert db.getlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH)==p.SQL_PROJECTION_BYTES
        assert db.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED)==0
    finally:db.close()
