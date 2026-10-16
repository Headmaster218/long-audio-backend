"""Durable erasure intent and explicit terminal stream cancellation.

Filesystem changes cannot participate in a SQLite transaction. Forget live rows
first, retaining a private, replayable cleanup receipt, then unlink owned files.
No cleanup pass materializes all session paths in memory.
"""
from pathlib import Path
import re
import time


def init_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS session_file_cleanup (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
        path TEXT NOT NULL, created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT, UNIQUE(session_id,path))''')
    db.execute('CREATE INDEX IF NOT EXISTS cleanup_session ON session_file_cleanup(session_id,id)')
    db.execute('CREATE INDEX IF NOT EXISTS cleanup_path ON session_file_cleanup(path)')


def unlink_owned(store, raw):
    """Never follow a retained path outside this service's upload directory."""
    path = Path(raw)
    uploads = (store.settings.data_dir / 'uploads').resolve()
    page_temp = re.fullmatch(r'ro-[0-9a-f]{32}-page-[0-9a-f]{32}-(?:source-[0-9a-f]{32}|work-[0-9]+)\.part', path.name) is not None
    if not path.is_absolute() or path.parent.resolve() != uploads or (path.suffix != '.audio' and not page_temp):
        raise ValueError('Cleanup path is not an owned uploaded audio file')
    # Unlink a symlink itself rather than resolving and deleting its target.
    path.unlink(missing_ok=True)


def cleanup_pending(store, delete_file=None, sid=None):
    """Try each pending path once; failed intents survive crashes and retries.

    Exceptions from an individual unlink are recorded, not hidden by reporting
    successful physical erasure. Process death after unlink but before receipt
    removal is harmless: the default unlink tolerates an already absent file.
    """
    delete_file = delete_file or (lambda raw: unlink_owned(store, raw))
    after = 0
    while True:
        with store.db() as db:
            rows = db.execute('''SELECT id,path FROM session_file_cleanup
                WHERE id>? AND (? IS NULL OR session_id=?) ORDER BY id LIMIT 64''',
                (after, sid, sid)).fetchall()
        if not rows:
            break
        for row in rows:
            after = row['id']
            try:
                delete_file(row['path'])
            except Exception as exc:
                with store.db() as db:
                    db.execute('''UPDATE session_file_cleanup SET attempts=attempts+1,error=?
                        WHERE id=?''', (f'{type(exc).__name__}: {exc}'[:512], row['id']))
            else:
                with store.db() as db:
                    db.execute('DELETE FROM session_file_cleanup WHERE id=?', (row['id'],))
    with store.db() as db:
        pending = db.execute('''SELECT COUNT(*) FROM session_file_cleanup
            WHERE ? IS NULL OR session_id=?''', (sid, sid)).fetchone()[0]
    return {'cleanup_pending': pending}


def delete_session(store, sid, delete_file=None):
    """Commit logical deletion and cleanup intent before deleting any source."""
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        session = db.execute('SELECT deleted,identity_group FROM sessions WHERE id=?', (sid,)).fetchone()
        if session is None:
            raise KeyError('Session not found')
        if not session['deleted']:
            if db.execute('SELECT 1 FROM fragment_http_sessions WHERE session_id=? AND cleanup_error IS NOT NULL',(sid,)).fetchone():
                raise ValueError('Provider ownership cleanup is unresolved; session erasure is fenced')
            if db.execute("SELECT 1 FROM chunks WHERE session_id=? AND status='running' LIMIT 1", (sid,)).fetchone():
                raise ValueError('Pause or cancel the running chunk first, then wait for worker idle')
            db.execute('''INSERT OR IGNORE INTO session_file_cleanup(session_id,path,created)
                SELECT session_id,path,? FROM chunks WHERE session_id=?''', (time.time(), sid))
            from .recording_overlap import erase as erase_recording
            erase_recording(db, sid, store.settings.data_dir / 'uploads')
            # Keep only a scrubbed ID tombstone for idempotent cleanup retry.
            # The change-feed erasure trigger removes historical text snapshots.
            db.execute("UPDATE sessions SET deleted=1,name='',identity_group='',created=0,timeline_end=0,processing_mode='legacy' WHERE id=?", (sid,))
            from .moss_result_store import erase_session as erase_moss_results
            erase_moss_results(db, sid)
            from .moss_automatic import erase_session as erase_automatic
            erase_automatic(db,sid)
            from .fragment_http import erase
            erase(db,sid)
            for table in ('segments', 'events', 'speaker_turns'):
                db.execute(f'DELETE FROM {table} WHERE chunk_id IN (SELECT id FROM chunks WHERE session_id=?)', (sid,))
            db.execute('DELETE FROM speaker_observations WHERE session_id=?', (sid,))
            # Explicit stream and event-candidate ledgers cascade from chunks.
            db.execute('DELETE FROM chunks WHERE session_id=?', (sid,))
            db.execute('DELETE FROM corrections WHERE session_id=?', (sid,))
            db.execute('''DELETE FROM speaker_identity_generations WHERE identity_group=?
                AND NOT EXISTS (SELECT 1 FROM sessions WHERE identity_group=? AND deleted=0)''',
                (session['identity_group'],session['identity_group']))
            # Reclaim empty group policy only after all trust/history is explicitly
            # erased. A group with surviving sessions retains its immutable mode.
            if not db.execute('SELECT 1 FROM sessions WHERE identity_group=? AND deleted=0',(session['identity_group'],)).fetchone():
                from .verified_identity import reset
                reset(db,session['identity_group'])
                db.execute('DELETE FROM identity_group_policy WHERE identity_group=?',(session['identity_group'],))
                if db.execute("SELECT 1 FROM speakers WHERE identity_group=? AND state='reviewed_reference' LIMIT 1",(session['identity_group'],)).fetchone():
                    db.execute("DELETE FROM speakers WHERE identity_group=? AND state='reviewed_reference'",(session['identity_group'],))
            # Row DELETE triggers may have appended tombstones after the first
            # erasure trigger. Erase those too, within the same transaction.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='result_changes'").fetchone():
                db.execute('DELETE FROM result_changes WHERE session_id=?', (sid,))
    # Primary deletion is already committed before optional memory is touched.
    from .native_owned_cell_capture import notify_deleted as notify_cells_deleted
    cells_pending=notify_cells_deleted(store,sid)
    from .native_owned_support_cache import notify_deleted
    optional_pending=notify_deleted(store,sid)
    from .moss_handy_jobs import notify_deleted as notify_jobs_deleted
    jobs_pending=notify_jobs_deleted(store,sid)
    result = cleanup_pending(store, delete_file, sid)
    if optional_pending is not None:
        result['native_owned_support_cleanup_pending']=optional_pending
    if jobs_pending is not None:
        result['moss_jobs_cleanup_pending']=jobs_pending
    if cells_pending is not None:
        result['native_owned_cells_cleanup_pending']=cells_pending
    return {'deleted': sid, **result}


def cancel_descendants(db, cid):
    """Cancel pending successors and close this continuity scope atomically.

    A cancelled predecessor cannot satisfy ordered stream replay. Give dependent
    jobs an explicit terminal outcome instead of leaving them queued forever.
    Completed prefixes and all original audio/results remain available.
    """
    from .moss_automatic import cancel_session
    session=db.execute('SELECT session_id FROM chunks WHERE id=?',(cid,)).fetchone()
    if session is not None:cancel_session(db,session[0])
    own = db.execute('SELECT * FROM stream_chunks WHERE chunk_id=?', (cid,)).fetchone()
    if not own:
        return 0
    batch = db.execute("SELECT id FROM recording_batches WHERE session_id=? AND mode='sealed_pages' AND ?='recording.'||id", (own['session_id'], own['stream_id'])).fetchone()
    if batch:
        from .recording_overlap import abort_capture
        abort_capture(db, batch['id'])
        return 0
    count = db.execute('''UPDATE chunks SET status='cancelled',error=? WHERE
        status IN ('queued','running','paused','failed','waiting_input') AND id IN (
        SELECT chunk_id FROM stream_chunks WHERE session_id=? AND stream_id=? AND position>?)''',
        ('Cancelled because an earlier stream chunk was cancelled; committed results retained',
         own['session_id'], own['stream_id'], own['position'])).rowcount
    db.execute('''UPDATE stream_chunks SET stream_final=1 WHERE chunk_id=(
        SELECT chunk_id FROM stream_chunks WHERE session_id=? AND stream_id=?
        ORDER BY position DESC LIMIT 1)''', (own['session_id'], own['stream_id']))
    return count


def recover_cancelled_streams(db):
    """Repair dependent jobs stranded by older versions, using disk-backed SQL."""
    from .recording_overlap import abort_capture
    for row in db.execute("""SELECT b.id FROM recording_batches b WHERE b.mode='sealed_pages' AND EXISTS (
        SELECT 1 FROM recording_work w JOIN chunks c ON c.id=w.chunk_id WHERE w.batch_id=b.id AND c.status='cancelled')"""):
        abort_capture(db,row['id'])
    db.execute('''UPDATE chunks SET status='cancelled',error=
        'Cancelled because an earlier stream chunk was cancelled; committed results retained'
        WHERE status IN ('queued','running','paused','failed','waiting_input')
        AND session_id IN (SELECT id FROM sessions WHERE processing_mode='legacy') AND EXISTS (
            SELECT 1 FROM stream_chunks own JOIN stream_chunks prior
            ON prior.session_id=own.session_id AND prior.stream_id=own.stream_id
            AND prior.position<own.position JOIN chunks previous ON previous.id=prior.chunk_id
            WHERE own.chunk_id=chunks.id AND previous.status='cancelled')''')
    db.execute('''UPDATE stream_chunks AS tail SET stream_final=1 WHERE stream_final=0
        AND session_id IN (SELECT id FROM sessions WHERE processing_mode='legacy')
        AND session_id NOT IN (SELECT session_id FROM recording_batches WHERE mode='sealed_pages')
        AND NOT EXISTS (SELECT 1 FROM stream_chunks later WHERE later.session_id=tail.session_id
            AND later.stream_id=tail.stream_id AND later.position>tail.position)
        AND EXISTS (SELECT 1 FROM stream_chunks member JOIN chunks c ON c.id=member.chunk_id
            WHERE member.session_id=tail.session_id AND member.stream_id=tail.stream_id
            AND c.status='cancelled')''')
