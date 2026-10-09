"""Transactional, replayable public row changes; no audio paths or voice vectors."""
import json
import time
from . import overlap_store

# Explicit allowlists prevent future private schema additions leaking through the API.
DECISION_PUBLIC=('identity_decision_id','identity_profile_revision','identity_state')

def _payload(table,ref,pairs):
    base=f'json_object({pairs})'
    if table in ('segments','speaker_turns'):
        extras=','.join(f"'{column}',{ref}.{column}" for column in DECISION_PUBLIC)
        return f"json_patch({base},CASE WHEN {ref}.identity_decision_id IS NOT NULL THEN json_object({extras}) ELSE '{{}}' END)"
    return base

PUBLIC = {
    'segments': ('id','chunk_id','start','end','text','speaker_id','speaker_local_id',
                 'confidence','speaker_match_score','speaker_status','asr_backend',
                 'timestamp_kind','raw_text','speaker_window_start','revision','is_final','provisional','finality'),
    'events': ('id','chunk_id','start','end','label','score','time_precision','source_label','onset_precision_seconds','method','verification_status','inference_source','revision','is_final','provisional','finality'),
    'speaker_turns': ('id','chunk_id','start','end','speaker_id','speaker_local_id','speaker_match_score','speaker_status','overlap','speaker_window_start'),
    'event_candidates': ('id','chunk_id','analysis_offset','candidate_index','start','end','support_start','support_end','status','time_precision','onset_precision_seconds','payload','provenance'),
    'overlap_regions': overlap_store.REGION_PUBLIC+('detected_speaker_local_ids_json','conditioning_evidence_json','support_activity_json'),
    'overlap_tracks': overlap_store.TRACK_PUBLIC+('provenance_json','conditioning_evidence_json'),
    'chunks': ('id','session_id','status','duration','timeline_start','next_offset','recorded_at',
               'created','error','continuation_of','continuous','stream_id','final','stream_final',
               'tail_status','boundary_status'),
    'stream_chunks': ('chunk_id','session_id','stream_id','stream_final','upload_stream_final','position'),
    'speakers': ('id','session_id','identity_group','name','state','merged_into','created'),
}


def initialize(store):
    """Install after other migrations, before accepting uploads or worker activity.

    Triggers share the originating write transaction. AUTOINCREMENT cursors remain
    monotonic after session erasure. No RAM cache or unbounded response is used.
    """
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS result_changes (
            cursor INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            entity TEXT NOT NULL, entity_id TEXT NOT NULL, operation TEXT NOT NULL,
            data TEXT, created REAL NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS result_changes_session ON result_changes(session_id,cursor)')
        db.execute('CREATE TABLE IF NOT EXISTS change_feed_migrations (name TEXT PRIMARY KEY)')
        had_snapshot=bool(db.execute("SELECT 1 FROM change_feed_migrations WHERE name='initial_snapshot_v1'").fetchone())
        had_candidate_v2=bool(db.execute("SELECT 1 FROM change_feed_migrations WHERE name='speaker_candidate_public_v2'").fetchone())
        for table, allowed in PUBLIC.items():
            columns = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
            if not columns: continue
            key='chunk_id' if table=='stream_chunks' else 'id'
            selected = [column for column in allowed if column in columns]
            for operation, timing, ref in [('upsert','INSERT','NEW'), ('upsert','UPDATE','NEW'), ('delete','DELETE','OLD')]:
                name = f'feed_{table}_{timing.lower()}'
                db.execute(f'DROP TRIGGER IF EXISTS {name}')
                pairs = ','.join(f"'{column}',{ref}.{column}" for column in selected)
                payload = 'NULL' if operation == 'delete' else _payload(table,ref,pairs)
                if table in ('segments','events','speaker_turns','event_candidates','overlap_regions','overlap_tracks'):
                    source = f'SELECT session_id FROM chunks WHERE id={ref}.chunk_id'
                elif table == 'speakers':
                    source = f'SELECT id AS session_id FROM sessions WHERE identity_group={ref}.identity_group AND deleted=0'
                else:
                    source = f'SELECT {ref}.session_id AS session_id'
                condition=''
                if table in ('segments','speaker_turns'):
                    condition=f"WHEN NOT EXISTS(SELECT 1 FROM identity_feed_suppression x JOIN sessions s ON s.identity_group=x.identity_group JOIN chunks c ON c.session_id=s.id WHERE c.id={ref}.chunk_id)"
                db.execute(f'''CREATE TRIGGER {name} AFTER {timing} ON {table} {condition} BEGIN
                    INSERT INTO result_changes(session_id,entity,entity_id,operation,data,created)
                    SELECT session_id,'{table}',CAST({ref}.{key} AS TEXT),'{operation}',{payload},
                    (julianday('now')-2440587.5)*86400 FROM ({source}); END''')
        # Existing installations need an initial replay snapshot. Run once only.
        if not db.execute("SELECT 1 FROM change_feed_migrations WHERE name='initial_snapshot_v1'").fetchone():
            for table, allowed in PUBLIC.items():
                columns = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                if not columns: continue
                key='chunk_id' if table=='stream_chunks' else 'id'
                pairs = ','.join(f"'{c}',t.{c}" for c in allowed if c in columns)
                if table in ('segments','events','speaker_turns','event_candidates','overlap_regions','overlap_tracks'):
                    source = f'{table} t JOIN chunks c ON c.id=t.chunk_id JOIN sessions s ON s.id=c.session_id'
                elif table == 'speakers':
                    source = 'speakers t JOIN sessions s ON s.identity_group=t.identity_group'
                else:
                    source = f'{table} t JOIN sessions s ON s.id=t.session_id'
                db.execute(f'''INSERT INTO result_changes(session_id,entity,entity_id,operation,data,created)
                    SELECT s.id,?,CAST(t.{key} AS TEXT),'upsert',{_payload(table,'t',pairs)},? FROM {source}
                    WHERE s.deleted=0''',(table,time.time()))
            db.execute("INSERT INTO change_feed_migrations VALUES('initial_snapshot_v1')")
        if not db.execute("SELECT 1 FROM change_feed_migrations WHERE name='speaker_candidate_public_v2'").fetchone():
            if had_snapshot:
                # Backfill only changed/new public tables after an existing feed
                # upgrade. New installations already received the full snapshot.
                for table in ('segments','events','speaker_turns','event_candidates','overlap_regions','overlap_tracks'):
                    columns={r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                    if not columns:continue
                    pairs=','.join(f"'{c}',t.{c}" for c in PUBLIC[table] if c in columns)
                    db.execute(f'''INSERT INTO result_changes(session_id,entity,entity_id,operation,data,created)
                        SELECT s.id,?,CAST(t.id AS TEXT),'upsert',{_payload(table,'t',pairs)},? FROM {table} t
                        JOIN chunks c ON c.id=t.chunk_id JOIN sessions s ON s.id=c.session_id
                        WHERE s.deleted=0''',(table,time.time()))
            db.execute("INSERT INTO change_feed_migrations VALUES('speaker_candidate_public_v2')")
        if not db.execute("SELECT 1 FROM change_feed_migrations WHERE name='overlap_support_conditioning_v3'").fetchone():
            # Re-emit safe current candidate rows once for clients whose previous
            # feed omitted the new support/conditioning fields. An older v1
            # installation already received these fields in the v2 block above.
            if had_snapshot and had_candidate_v2:
                for table in ('overlap_regions','overlap_tracks'):
                    columns={r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                    if not columns:continue
                    pairs=','.join(f"'{c}',t.{c}" for c in PUBLIC[table] if c in columns)
                    db.execute(f"""INSERT INTO result_changes(session_id,entity,entity_id,operation,data,created)
                        SELECT s.id,?,CAST(t.id AS TEXT),'upsert',{_payload(table,'t',pairs)},? FROM {table} t
                        JOIN chunks c ON c.id=t.chunk_id JOIN sessions s ON s.id=c.session_id
                        WHERE s.deleted=0""",(table,time.time()))
            db.execute("INSERT INTO change_feed_migrations VALUES('overlap_support_conditioning_v3')")
        db.execute('DROP TRIGGER IF EXISTS feed_identity_bank')
        db.execute('''CREATE TRIGGER feed_identity_bank AFTER UPDATE OF revision ON identity_group_policy
            WHEN NEW.mode='verified_review_v1' AND NEW.revision!=OLD.revision BEGIN
            INSERT INTO result_changes(session_id,entity,entity_id,operation,data,created)
            SELECT id,'identity_bank',NEW.identity_group,'upsert',json_object(
                'identity_mode',NEW.mode,'profile_revision',NEW.revision,'availability_sequence',NEW.availability,
                'invalidate_before_revision',NEW.revision,'invalidates_automatic_assignments',json('true'),'reason',NEW.change_reason,
                'refresh_canonical_persons',json('true'),
                'clears_all_identity_assignments',json(CASE WHEN NEW.change_reason='identity_reset' THEN 'true' ELSE 'false' END),
                'policy_sha256',NEW.policy_sha256,'space_id',NEW.space_id,
                'generation',(SELECT generation FROM speaker_identity_generations WHERE identity_group=NEW.identity_group)),
                (julianday('now')-2440587.5)*86400 FROM sessions
                WHERE identity_group=NEW.identity_group AND deleted=0; END''')
        # Erasure includes historical text and identity payloads, not just live rows.
        db.execute('''CREATE TRIGGER IF NOT EXISTS feed_session_erasure AFTER UPDATE OF deleted ON sessions
            WHEN NEW.deleted=1 BEGIN DELETE FROM result_changes WHERE session_id=NEW.id; END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS feed_session_delete AFTER DELETE ON sessions
            BEGIN DELETE FROM result_changes WHERE session_id=OLD.id; END''')


def changes(store, sid, after=0, limit=500):
    store.session(sid)
    with store.db() as db:
        db.execute('BEGIN')
        rows = [dict(row) for row in db.execute('''SELECT cursor,entity,entity_id,operation,data,created
            FROM result_changes WHERE session_id=? AND cursor>? ORDER BY cursor LIMIT ?''', (sid,after,limit))]
        from .verified_identity import group_policy
        group=db.execute('SELECT identity_group FROM sessions WHERE id=?',(sid,)).fetchone()[0]
        policy=group_policy(db,group)
        from . import identity_canonical
        canonical=identity_canonical.snapshot(db,group,policy)
        high_watermark = db.execute('SELECT COALESCE(MAX(cursor),0) FROM result_changes WHERE session_id=?',(sid,)).fetchone()[0]
    for row in rows:
        if row['data'] is not None:
            row['data'] = json.loads(row['data'])
            if row['entity'] in ('segments','speaker_turns'):
                from .verified_identity import project_result
                project_result(row['data'],policy,canonical)
            elif row['entity']=='speakers':
                identity_canonical.project_person(row['data'],policy,canonical)
            if row['entity']=='event_candidates':
                payload=json.loads(row['data'].pop('payload'))
                row['data']['provenance']=json.loads(row['data']['provenance'])
                row['data'].update(payload)
            elif row['entity']=='overlap_regions':row['data']=overlap_store.public_region(row['data'])
            elif row['entity']=='overlap_tracks':row['data']=overlap_store.public_track(row['data'])

            for field in ('provenance','metadata'):
                if row['entity']=='chunks' and row['data'].get(field):
                    row['data'][field]=json.loads(row['data'][field])
    cursor=rows[-1]['cursor'] if rows else after
    return {'changes':rows,'next_cursor':cursor,'high_watermark':high_watermark,
            'has_more':cursor<high_watermark}
