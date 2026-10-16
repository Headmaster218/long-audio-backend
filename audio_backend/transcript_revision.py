"""Transactional transcript invalidation, independent of public change-feed SQL.

Install only after the automatic, retained-history and recording schemas exist.
The caller owns the migration transaction. No historical payload/geometry scan,
new table, timestamp proxy, or archive-sequence high-water mark is needed.
"""
from .moss_automatic import TABLE as AUTO
from .moss_result_store import TABLE as HISTORY

COLUMN = ("transcript_revision INTEGER NOT NULL DEFAULT 0 "
          "CHECK(typeof(transcript_revision)='integer' AND transcript_revision>=0)")


def _cid_owners(cid):
    # The intent's retained sid still matters after its chunk moves or vanishes.
    return [f'SELECT session_id FROM chunks WHERE id={cid}',
            f'SELECT sid FROM {AUTO} INDEXED BY auto_cid1 WHERE cid1={cid}',
            f'SELECT sid FROM {AUTO} INDEXED BY auto_cid2 WHERE cid2={cid}']


def _owners(table, ref):
    if table == AUTO:
        return [f'SELECT {ref}.sid']
    if table == HISTORY:
        return [f'SELECT {ref}.session_id',
                f'SELECT sid FROM {AUTO} INDEXED BY auto_job WHERE job_id={ref}.job_id']
    if table == 'segments':
        return [f'SELECT session_id FROM chunks WHERE id={ref}.chunk_id']
    if table == 'chunks':
        return [f'SELECT {ref}.session_id', *_cid_owners(f'{ref}.id')]
    if table == 'recording_work':
        return _cid_owners(f'{ref}.chunk_id')
    # recording_work's existing UNIQUE(batch_id,position) supplies the seek.
    # CROSS JOIN fixes work-before-intent order; both alias indexes are needed.
    return [f'SELECT {ref}.session_id',
            f'''SELECT c.session_id FROM recording_work w
                JOIN chunks c ON c.id=w.chunk_id WHERE w.batch_id={ref}.id''',
            *[f'''SELECT a.sid FROM recording_work w
                CROSS JOIN {AUTO} a INDEXED BY auto_cid{number}
                WHERE w.batch_id={ref}.id AND a.cid{number}=w.chunk_id'''
              for number in (1, 2)]]


FIELDS = {
    AUTO: ('seq', 'candidate_key', 'sid', 'kind', 'binding', 'proof_sha256', 'cell_id',
           'cid1', 'cid2', 'state', 'reason', 'job_id', 'jobs_epoch', 'request_sha256'),
    HISTORY: ('archive_seq', 'job_id', 'session_id', 'original_jobs_epoch',
              'idempotency_key', 'canonical_request_sha256', 'state', 'schema_version',
              'metadata_json', 'result_json', 'result_sha256'),
    'segments': ('id', 'chunk_id', 'start', 'end', 'text', 'raw_text', 'asr_backend',
                 'timestamp_kind', 'confidence', 'speaker_local_id', 'speaker_window_start'),
    'chunks': ('id', 'session_id', 'digest', 'timeline_start', 'duration', 'status'),
    'recording_work': ('chunk_id', 'batch_id', 'frames', 'receipt_id', 'capture_start_frame'),
    'recording_batches': ('id', 'session_id', 'capture_origin_frame'),
}


def _schema():
    schema = {}
    for table, fields in FIELDS.items():
        for operation, refs in (('INSERT', ('NEW',)), ('UPDATE', ('OLD', 'NEW')), ('DELETE', ('OLD',))):
            name = f'transcript_revision_v1_{table}_{operation.lower()}'
            event, condition = operation, ''
            if operation == 'UPDATE':
                event += ' OF ' + ','.join(fields)
                condition = 'WHEN ' + ' OR '.join(f'OLD.{field} IS NOT NEW.{field}' for field in fields)
            # A batch delete must see work rows before foreign-key cascades.
            timing = 'BEFORE' if operation == 'DELETE' else 'AFTER'
            owners = ' UNION '.join(query for ref in refs for query in _owners(table, ref))
            schema[name] = f'''CREATE TRIGGER {name} {timing} {event} ON {table} {condition} BEGIN
                UPDATE sessions SET transcript_revision=CASE WHEN transcript_revision<9223372036854775807
                    THEN transcript_revision+1 ELSE RAISE(ROLLBACK,'Transcript revision exhausted') END
                    WHERE id IN ({owners}); END'''
    return schema


SCHEMA = _schema()


def init_schema(db):
    """Atomically add the counter and exact v1 triggers; reject partial shapes."""
    if not db.in_transaction:
        raise ValueError('Transcript revision migration requires a transaction')
    columns = {row['name']: row for row in db.execute('PRAGMA table_info(sessions)')}
    existing = [db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
                for name in SCHEMA]
    present = 'transcript_revision' in columns
    if any(existing) and (not all(existing) or not present) or present and not all(existing):
        raise ValueError('Incomplete transcript revision schema')
    if not present:
        db.execute(f'ALTER TABLE sessions ADD COLUMN {COLUMN}')
    else:
        definition = db.execute("SELECT sql FROM sqlite_master WHERE name='sessions'").fetchone()[0]
        if ' '.join(COLUMN.split()) not in ' '.join(definition.split()):
            raise ValueError('Incompatible transcript revision column')
    for (name, sql), row in zip(SCHEMA.items(), existing):
        if row is None:
            db.execute(sql)
        elif ' '.join(row[0].split()) != ' '.join(sql.split()):
            raise ValueError('Incompatible transcript revision trigger: ' + name)
