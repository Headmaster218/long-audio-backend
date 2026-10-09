"""Explicit continuous-upload bookkeeping; committed transcript rows never revised."""


def init_stream_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS stream_chunks (
        chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
        session_id TEXT NOT NULL, stream_id TEXT NOT NULL,
        stream_final INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL,
        upload_stream_final INTEGER NOT NULL DEFAULT 0,
        UNIQUE(session_id,stream_id,position))''')
    columns={r['name'] for r in db.execute('PRAGMA table_info(stream_chunks)')}
    if 'upload_stream_final' not in columns:
        db.execute('ALTER TABLE stream_chunks ADD COLUMN upload_stream_final INTEGER NOT NULL DEFAULT 0')
        db.execute('UPDATE stream_chunks SET upload_stream_final=stream_final')
    db.execute('CREATE INDEX IF NOT EXISTS stream_order ON stream_chunks(session_id,stream_id,position)')
    _init_stream_time_index(db)


def _init_stream_time_index(db):
    """Atomic one-time derived index migration, without public receipt updates."""
    existed=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stream_time_index'").fetchone())
    db.execute('SAVEPOINT stream_time_index_migration')
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS stream_time_index (
            chunk_id TEXT PRIMARY KEY REFERENCES stream_chunks(chunk_id) ON DELETE CASCADE,
            session_id TEXT NOT NULL, stream_id TEXT NOT NULL, position INTEGER NOT NULL,
            timeline_start REAL NOT NULL CHECK(timeline_start>=0),
            timeline_end REAL NOT NULL CHECK(timeline_end>timeline_start))''')
        db.execute('CREATE INDEX IF NOT EXISTS stream_time_end ON stream_time_index(session_id,stream_id,timeline_end,position)')
        db.execute('''CREATE TRIGGER IF NOT EXISTS stream_time_insert AFTER INSERT ON stream_chunks BEGIN
            SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM chunks WHERE id=NEW.chunk_id AND session_id=NEW.session_id)
                THEN RAISE(ABORT,'Stream source session mismatch') END;
            INSERT INTO stream_time_index(chunk_id,session_id,stream_id,position,timeline_start,timeline_end)
                SELECT NEW.chunk_id,NEW.session_id,NEW.stream_id,NEW.position,timeline_start,timeline_start+duration
                FROM chunks WHERE id=NEW.chunk_id;
            END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS stream_time_membership_update
            AFTER UPDATE OF session_id,stream_id,position ON stream_chunks BEGIN
            SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM chunks WHERE id=NEW.chunk_id AND session_id=NEW.session_id)
                THEN RAISE(ABORT,'Stream source session mismatch') END;
            UPDATE stream_time_index SET session_id=NEW.session_id,stream_id=NEW.stream_id,position=NEW.position
                WHERE chunk_id=NEW.chunk_id;
            END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS stream_source_time_immutable
            BEFORE UPDATE OF timeline_start,duration ON chunks
            WHEN (NEW.timeline_start IS NOT OLD.timeline_start OR NEW.duration IS NOT OLD.duration)
                AND EXISTS(SELECT 1 FROM stream_chunks WHERE chunk_id=OLD.id)
            BEGIN SELECT RAISE(ABORT,'Continuous stream source times are immutable'); END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS stream_index_time_immutable
            BEFORE UPDATE OF timeline_start,timeline_end ON stream_time_index
            WHEN NEW.timeline_start IS NOT OLD.timeline_start OR NEW.timeline_end IS NOT OLD.timeline_end
            BEGIN SELECT RAISE(ABORT,'Continuous stream indexed times are immutable'); END''')
        if not existed:
            db.execute('''INSERT INTO stream_time_index(chunk_id,session_id,stream_id,position,timeline_start,timeline_end)
                SELECT sc.chunk_id,sc.session_id,sc.stream_id,sc.position,c.timeline_start,c.timeline_start+c.duration
                FROM stream_chunks sc JOIN chunks c ON c.id=sc.chunk_id AND c.session_id=sc.session_id''')
            if db.execute('SELECT sc.chunk_id FROM stream_chunks sc LEFT JOIN stream_time_index ti ON ti.chunk_id=sc.chunk_id WHERE ti.chunk_id IS NULL LIMIT 1').fetchone():
                raise ValueError('Cannot migrate stream index: invalid source membership')
        db.execute('RELEASE stream_time_index_migration')
    except BaseException:
        db.execute('ROLLBACK TO stream_time_index_migration');db.execute('RELEASE stream_time_index_migration')
        raise


CONTEXT_PARTS_SQL='''SELECT c.id,c.path,c.session_id,c.timeline_start,c.duration,
    ti.timeline_start AS indexed_start,ti.timeline_end AS indexed_end
    FROM stream_time_index ti INDEXED BY stream_time_end JOIN chunks c ON c.id=ti.chunk_id
    WHERE ti.session_id=? AND ti.stream_id=? AND ti.timeline_end>?
    ORDER BY ti.timeline_end,ti.position LIMIT 4097'''


def register_stream_chunk(db,cid,sid,stream_id,stream_final=False):
    if stream_id is None:
        if stream_final:raise ValueError('stream_final requires stream_id')
        return
    if not isinstance(stream_id,str) or not stream_id.strip() or len(stream_id)>128:
        raise ValueError('stream_id must be a nonempty string of at most 128 characters')
    previous=db.execute('''SELECT sc.*,c.timeline_start,c.duration FROM stream_chunks sc
        JOIN chunks c ON c.id=sc.chunk_id WHERE sc.session_id=? AND sc.stream_id=?
        ORDER BY sc.position DESC LIMIT 1''',(sid,stream_id)).fetchone()
    current=db.execute('SELECT timeline_start,duration FROM chunks WHERE id=?',(cid,)).fetchone()
    if previous:
        if previous['stream_final']:raise ValueError('Stream is finalized; use a new stream_id')
        if abs(previous['timeline_start']+previous['duration']-current['timeline_start'])>.001:
            raise ValueError('Continuous stream chunks must be contiguous on the session timeline')
    db.execute('INSERT INTO stream_chunks(chunk_id,session_id,stream_id,stream_final,position,upload_stream_final) VALUES(?,?,?,?,?,?)',
               (cid,sid,stream_id,int(stream_final),previous['position']+1 if previous else 0,int(stream_final)))
    db.execute("UPDATE chunks SET status='queued',error=NULL WHERE status='waiting_input' AND id IN (SELECT chunk_id FROM stream_chunks WHERE session_id=? AND stream_id=?)",(sid,stream_id))


def check_duplicate_stream(db,cid,stream_id,stream_final):
    row=db.execute('SELECT stream_id,stream_final,upload_stream_final FROM stream_chunks WHERE chunk_id=?',(cid,)).fetchone()
    if (row is None)!=(stream_id is None) or row and row['stream_id']!=stream_id:
        raise ValueError('Idempotency key already used with different stream membership')
    if row and bool(stream_final)!=bool(row['upload_stream_final']):
        raise ValueError('Idempotency key already used with a different final flag')


def finish_stream(store,sid,stream_id):
    store.session(sid)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM stream_chunks WHERE session_id=? AND stream_id=? ORDER BY position DESC LIMIT 1',(sid,stream_id)).fetchone()
        if not row:raise KeyError('Stream not found in session')
        db.execute('UPDATE stream_chunks SET stream_final=1 WHERE chunk_id=?',(row['chunk_id'],))
        db.execute("UPDATE chunks SET status='queued',error=NULL WHERE status='waiting_input' AND id IN (SELECT chunk_id FROM stream_chunks WHERE session_id=? AND stream_id=?)",(sid,stream_id))
        return {'stream_id':stream_id,'stream_final':True,'last_chunk_id':row['chunk_id']}


def stream_info(store,chunk):
    with store.db() as db:
        own=db.execute('SELECT * FROM stream_chunks WHERE chunk_id=?',(chunk['id'],)).fetchone()
        if not own:return None
        first=db.execute('''SELECT c.timeline_start FROM stream_chunks sc JOIN chunks c ON c.id=sc.chunk_id
            WHERE sc.session_id=? AND sc.stream_id=? ORDER BY sc.position LIMIT 1''',(own['session_id'],own['stream_id'])).fetchone()
        last=db.execute('''SELECT c.timeline_start,c.duration,sc.stream_final FROM stream_chunks sc JOIN chunks c ON c.id=sc.chunk_id
            WHERE sc.session_id=? AND sc.stream_id=? ORDER BY sc.position DESC LIMIT 1''',(own['session_id'],own['stream_id'])).fetchone()
        prev=db.execute('''SELECT c.* FROM stream_chunks sc JOIN chunks c ON c.id=sc.chunk_id
            WHERE sc.session_id=? AND sc.stream_id=? AND sc.position=?''',(own['session_id'],own['stream_id'],own['position']-1)).fetchone()
        return {**dict(own),'start':first['timeline_start'],
                'end':last['timeline_start']+last['duration'],'final':bool(last['stream_final']),
                'previous':dict(prev) if prev else None}


def context_parts(store,chunk,start,end):
    """Read only explicit same-stream files intersecting a <=60s context range."""
    if end-start>60.01 or start>=end:raise ValueError('Stream context must be positive and bounded to 60 seconds')
    with store.db() as db:
        own=db.execute('SELECT * FROM stream_chunks WHERE chunk_id=?',(chunk['id'],)).fetchone()
        if not own:return [dict(chunk,read_start=start-chunk['timeline_start'],read_duration=end-start)]
        # Seek past old history, then stop at the first future interval. A SQL
        # start<end residual would keep scanning all future rows after lastmatch.
        rows=db.execute(CONTEXT_PARTS_SQL,(own['session_id'],own['stream_id'],start))
        parts=[];cursor=start
        for row in rows:
            if row['indexed_start']>=end:break
            if (row['session_id']!=own['session_id'] or
                abs(row['timeline_start']-row['indexed_start'])>1e-6 or
                abs(row['timeline_start']+row['duration']-row['indexed_end'])>1e-6):
                raise ValueError('Continuous stream time index differs from its source')
            if len(parts)>=4096:raise ValueError('More than4096 stream fragments in one context window')
            a=max(start,row['timeline_start']);b=min(end,row['indexed_end'])
            if abs(a-cursor)>.002:raise ValueError('Gap in explicit continuous-stream context')
            part={k:row[k] for k in ('id','path','session_id','timeline_start','duration')}
            parts.append(dict(part,read_start=a-row['timeline_start'],read_duration=b-a));cursor=b
        if abs(cursor-end)>.002:raise ValueError('Incomplete continuous-stream context')
        return parts


def wait_for_input(store,cid,observed_end,observed_final):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        latest=db.execute("""SELECT c.timeline_start+c.duration AS endpoint,sc.stream_final
            FROM stream_chunks own JOIN stream_chunks sc ON own.session_id=sc.session_id AND own.stream_id=sc.stream_id
            JOIN chunks c ON c.id=sc.chunk_id WHERE own.chunk_id=? ORDER BY sc.position DESC LIMIT 1""",(cid,)).fetchone()
        changed=latest and (abs(latest['endpoint']-observed_end)>.00001 or bool(latest['stream_final'])!=observed_final)
        db.execute("UPDATE chunks SET status=? WHERE id=? AND status='running'",('queued' if changed else 'waiting_input',cid))


def wake_stream_successor(db,cid):
    own=db.execute('SELECT * FROM stream_chunks WHERE chunk_id=?',(cid,)).fetchone()
    if own:
        db.execute("UPDATE chunks SET status='queued' WHERE status='waiting_input' AND id IN (SELECT chunk_id FROM stream_chunks WHERE session_id=? AND stream_id=? AND position=?)",(own['session_id'],own['stream_id'],own['position']+1))
