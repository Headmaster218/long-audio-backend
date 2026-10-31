"""Process-local hot quota qualification; cold SQL placement is audited at owned reopen.

No persisted trust or counter. Existing immutable locator/unit and authorized
hot-to-cold transitions preserve the qualified cold census; cold reads still
validate their own evidence. Unqualified connections use the original census.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import stat

INDEXES=(
    ('payload_hot_pcm_v1','CREATE INDEX payload_hot_pcm_v1 ON speaker_support(id) WHERE pcm IS NOT NULL'),
    ('payload_hot_vector_v1','CREATE INDEX payload_hot_vector_v1 ON speaker_support_vectors(id) WHERE raw_vector IS NOT NULL'),
)


def _identity(path):
    info=Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid():
        raise ValueError('payload_hot_database_identity')
    return info.st_dev,info.st_ino


def _policy(store):
    return (store._strict_enabled,store._strict_policy,store._strict_source_storage,
        store._strict_lifetime_records,store._strict_receipt_records,
        store.settings.strict_primary_enabled,store.settings.strict_primary_policy,
        store.settings.strict_payload_source_storage)


def _indexes(db,create=False):
    for name,sql in INDEXES:
        row=db.execute('SELECT type,sql FROM sqlite_master WHERE name=?',(name,)).fetchone()
        if row is None and create:
            db.execute(sql);row=('index',sql)
        if row is None or tuple(row)!=('index',sql):raise ValueError('payload_hot_index_schema')


def _schema(db):
    # Bind the cold-placement invariants, not unrelated app/Worker lazy tables.
    rows=db.execute("""SELECT name,type,sql FROM sqlite_master WHERE tbl_name IN
      ('speaker_support','speaker_support_vectors','strict_payload_unit','strict_payload_object','strict_payload_policy')
      ORDER BY name LIMIT 129""").fetchall()
    if len(rows)>128 or any(len(str(value or ''))>8192 for row in rows for value in row):
        raise ValueError('payload_hot_schema_capacity')
    return tuple(tuple(row) for row in rows)


@dataclass(frozen=True)
class Qualification:
    owner: object
    path: str
    identity: tuple
    policy: tuple
    schema_version: int
    schema: tuple


def qualify(store):
    """Only Store initialization under its original exclusive owner may qualify."""
    if not store._initializing or not store._initializer_lock_owned:
        raise ValueError('payload_hot_owned_initialization_required')
    if getattr(store,'_payload_hot_qualification',None) is not None:
        raise ValueError('payload_hot_already_qualified')
    from .strict_payload_v2 import _full_hot_bytes,POLICY
    if not store._strict_enabled or store._strict_policy!=POLICY:
        raise ValueError('payload_hot_policy')
    path=str(Path(store.path).resolve());identity=_identity(store.path);policy=_policy(store)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        _full_hot_bytes(db)
        _indexes(db,create=True)
        version=db.execute('PRAGMA schema_version').fetchone()[0]
        schema=_schema(db)
    # Never publish capability until the audit/index transaction committed.
    if _identity(store.path)!=identity or _policy(store)!=policy:
        raise ValueError('payload_hot_qualification_changed')
    store._payload_hot_qualification=Qualification(store,path,identity,policy,version,schema)


def bind(store,db):
    """Capability is connection/Store bound, not inferred from index existence."""
    checked_version=None
    def qualified():
        nonlocal checked_version
        proof=getattr(store,'_payload_hot_qualification',None)
        if proof is None:return 0
        if (type(proof) is not Qualification or proof.owner is not store
                or str(Path(store.path).resolve())!=proof.path
                or _identity(store.path)!=proof.identity or _policy(store)!=proof.policy):
            raise ValueError('payload_hot_qualification_changed')
        version=db.execute('PRAGMA schema_version').fetchone()[0]
        if version!=checked_version:
            # Recheck the ORIGINAL frozen invariant schema. Do not requalify cold
            # history or accept changed guards when unrelated lazy DDL appears.
            if _schema(db)!=proof.schema:raise ValueError('payload_hot_schema_changed')
            checked_version=version
        main=next((r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main'),None)
        if main!=proof.path:raise ValueError('payload_hot_connection_changed')
        return 1
    db.create_function('payload_hot_qualified_v1',0,qualified)
