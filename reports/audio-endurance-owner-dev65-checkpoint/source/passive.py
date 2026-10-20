# PUBLIC INSPECTION COPY. Do not execute or import.
"""Finite passive helpers, source only until separately approved.

Journal inspection uses the installed product's bounded authenticated reader,
without calling recovery/save/run. SQLite operations never instantiate Store.
"""
from __future__ import annotations

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import argparse
import fcntl
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time

MIB = 1024**2


def encode(value):
    def default(value):
        if isinstance(value, bytes): return {'blob': base64.b64encode(value).decode('ascii')}
        raise TypeError(type(value).__name__)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=default).encode()


def check(deadline):
    if time.monotonic() >= deadline: raise RuntimeError('Passive deadline')


def journal(args):
    from audio_backend import recording_uploader as u
    guard = u.Guard(u.Limits(deadline_seconds=max(.001, args.deadline-time.monotonic())))
    journal_path=Path(args.journal)
    if (not journal_path.is_absolute() or journal_path.is_symlink() or not journal_path.is_dir()
            or journal_path.resolve()!=journal_path):
        raise RuntimeError('Passive journal must be an existing non-symlink directory')
    # Explicit child adoption proves the supervisor's existing open description.
    # Never independently acquire/reopen a journal lock in this continuation.
    from audio_backend.resources import InstanceLock
    borrowed=getattr(args,'journal_lock_fd',None)
    if type(borrowed) is not int or borrowed<3:
        raise RuntimeError('Preheld journal descriptor required')
    owner=InstanceLock(journal_path/'uploader.lock').adopt(borrowed)
    lock_fd=owner.file.fileno()
    lock_path=journal_path/'uploader.lock'
    def lock_identity(info):
        return (info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    try:
        owner.verify_inherited()
        lock_info=os.fstat(lock_fd)
        j = u.PagedJournal(journal_path, guard, u)
        d = j.load()
        if d is None: raise RuntimeError('Missing append-v3 checkpoint')
        target = dict(api=args.api, session_id=args.session, capture_id=args.capture,
                      origin=args.origin, format=u.FORMAT)
        if d['identity'] != target or d['caps']['max_inputs'] != 90 or d['caps']['output_frames'] != 691200000:
            raise RuntimeError('Journal target or frozen caps changed')
        if d['caps']['input_bytes'] != 1382403960:
            raise RuntimeError('Unexpected frozen original-byte cap')
        clean = count = 0
        for item in j.iter_inputs():
            check(args.deadline)
            count += 1
            if count > 90 or item['id'] != f'repeated-prefix-{count:03d}' or item['normalized_start'] != (count-1)*7680000:
                raise RuntimeError('Input chain identity/order changed')
            if item['identity']['sha256'] != 'REDACTED_INPUT_CONTENT_SHA256':
                raise RuntimeError('Input chain content changed')
            if item['clean_eof']:
                if item['frames'] != 7680000: raise RuntimeError('Input frame count changed')
                clean += 1
        if (lock_identity(os.fstat(lock_fd))!=lock_identity(lock_info) or
                lock_identity(lock_path.lstat())!=lock_identity(lock_info)):
            raise RuntimeError('Passive lock changed during inspection')
        return dict(inputs=count, clean_inputs=clean, frames=d['emitted_frames'],
                    published_frames=d['published_frames'], active_input=d['active_input'],
                    finalized=d['complete'], pages=d['page_count'],
                    checkpoint_sha256=hashlib.sha256((Path(args.journal)/'uploader.json').read_bytes()).hexdigest())
    finally: owner.release()


def connect(path, deadline):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=1)
    db.execute('PRAGMA query_only=ON')
    db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    return db


def backup(args):
    target = Path(args.output)
    if target.exists(): raise RuntimeError('Snapshot already exists')
    with connect(args.db, args.deadline) as source, sqlite3.connect(target) as dest:
        page_size=source.execute('PRAGMA page_size').fetchone()[0]
        def progress(status, remaining, total):
            check(args.deadline)
            if total*page_size > 512*MIB or target.stat().st_size > 512*MIB:
                raise RuntimeError('Snapshot512MiB cap')
        source.backup(dest, pages=64, progress=progress, sleep=.01)
    with target.open('rb') as saved: os.fsync(saved.fileno())
    directory=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
    return dict(snapshot=str(target), bytes=target.stat().st_size)


# Stable primary result fields only. Optional history decisions may legitimately
# become historical across process restart and are reported separately, not
# falsely asserted byte-immutable. No embeddings/audio are exported.
TABLES = {'segments': 'id,start,end,text,speaker_id,speaker_local_id,confidence,speaker_match_score,speaker_status,asr_backend,timestamp_kind,raw_text,candidate_observation_id',
          'speaker_turns': 'id,start,end,speaker_id,speaker_local_id,speaker_match_score,speaker_status,overlap',
          'events': 'id,start,end,label,score'}


def completed(args):
    output = Path(args.output)
    count = rows = total = 0
    aggregate = hashlib.sha256()
    with connect(args.db, args.deadline) as db, output.open('xb') as stream:
        db.execute('BEGIN')
        for chunk in db.execute("SELECT id,digest,timeline_start,duration,next_offset,metadata,provenance FROM chunks WHERE session_id=? AND status='completed' ORDER BY id", (args.session,)):
            check(args.deadline)
            count += 1
            if count > 8192: raise RuntimeError('Completed chunk bound')
            digest = hashlib.sha256(encode(chunk))
            for table, columns in TABLES.items():
                digest.update(table.encode())
                for result in db.execute(f'SELECT {columns} FROM {table} WHERE chunk_id=? ORDER BY id', (chunk[0],)):
                    check(args.deadline)
                    raw = encode(result)
                    rows += 1; total += len(raw)
                    if rows > 1048576 or len(raw) > MIB or total > 512*MIB:
                        raise RuntimeError('Primary result scan cap')
                    digest.update(len(raw).to_bytes(8, 'big')); digest.update(raw)
            raw = encode(dict(id=chunk[0], sha256=digest.hexdigest()))+b'\n'
            if stream.tell()+len(raw) > 4*MIB: raise RuntimeError('Completed manifest cap')
            stream.write(raw); aggregate.update(raw)
        batch = db.execute('SELECT capture_origin_frame,capture_end_frame,eos FROM recording_batches WHERE session_id=? AND capture_id=?', (args.session,args.capture)).fetchone()
        if batch is None: raise RuntimeError('Missing recording batch')
        db.row_factory = sqlite3.Row
        sampling_rows = db.execute('''SELECT s.sampling_policy,s.examined_count,s.examined_bucket,s.last_reason,s.last_frame,s.counters,s.scope_seq
            FROM recording_history_sampling_v2 s JOIN recording_history_scopes_v1 h USING(scope_seq)
            JOIN recording_batches b ON b.id=h.recording_id
            WHERE h.session_id=? AND b.capture_id=? LIMIT 2''', (args.session,args.capture)).fetchall()
        if len(sampling_rows) != 1: raise RuntimeError('One production v2 scope required')
        sampling = dict(sampling_rows[0])
        if sampling['sampling_policy'] != 'fresh-window-bounded-v2' or not 0 <= sampling['examined_count'] <= 3:
            raise RuntimeError('Production sampling policy/bound changed')
        sampling['counters'] = json.loads(sampling['counters'])
        # Aggregate bounded SQL, not the short smoke's <=64 attempts collector.
        sampling['attempt_states'] = {row[0]:row[1] for row in db.execute(
            'SELECT state,COUNT(*) FROM recording_history_attempts_v1 WHERE scope_seq=? GROUP BY state LIMIT 8', (sampling['scope_seq'],))}
        sampling['optional_receipt_states'] = {row[0]:row[1] for row in db.execute(
            '''SELECT w.receipt_state,COUNT(*) FROM recording_work w JOIN recording_batches b ON b.id=w.batch_id
               WHERE b.session_id=? AND b.capture_id=? GROUP BY w.receipt_state LIMIT 16''', (args.session,args.capture))}
        stream.flush(); os.fsync(stream.fileno())
    return dict(completed=count, result_rows=rows, manifest_sha256=aggregate.hexdigest(),
                origin=batch[0], end=batch[1], eos=batch[2], sampling=sampling)



FAILED_CHUNK = 'redacted-historical-completed-chunk'

def failed(args):
    """Read-only exact failed-work proof; never invokes product control APIs."""
    with connect(args.db, args.deadline) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        row = db.execute('SELECT * FROM chunks WHERE id=? AND session_id=?',
                         (FAILED_CHUNK, args.session)).fetchone()
        if row is None: raise RuntimeError('Missing exact failed chunk')
        row = dict(row)
        expected = (REDACTED_RECOVERED_REQUESTS_OR_PRIOR_SEQUENCE, 'failed', REDACTED_PRIOR_CHUNK_START, REDACTED_PRIOR_CHUNK_DURATION, 0.0, 'OperationalError: database is locked')
        if tuple(row[k] for k in ('seq','status','timeline_start','duration','next_offset','error')) != expected:
            raise RuntimeError('Exact failed-work state changed')
        side_tables = dict.fromkeys(('segments','speaker_turns','events','speaker_observations',
            'overlap_regions','overlap_tracks','overlap_support_diagnostics','event_candidates',
            'speaker_support','speaker_support_sources','identity_decisions','identity_decision_sources',
            'fragment_http_sources','recording_history_sources_v1'),'chunk_id')
        side_tables.update(dict.fromkeys(('native_core_commits','moss_automatic_dependencies_v2',
                                         'recording_primary_packets_v1'),'cid'))
        counts = {table: db.execute('SELECT COUNT(*) FROM '+table+' WHERE '+column+'=?',
                                   (FAILED_CHUNK,)).fetchone()[0] for table,column in side_tables.items()}
        scaffold = {}
        for table in ('stream_chunks','stream_time_index','recording_work','recording_ranges'):
            rows = db.execute('SELECT * FROM '+table+' WHERE chunk_id=?', (FAILED_CHUNK,)).fetchall()
            if len(rows)!=1: raise RuntimeError('Failed work scaffold count changed')
            scaffold[table] = hashlib.sha256(encode([tuple(x) for x in rows])).hexdigest()
        batch = db.execute('SELECT b.id,b.session_id,b.capture_id,b.state,b.blockers,b.eos FROM recording_batches b JOIN recording_work w ON w.batch_id=b.id WHERE w.chunk_id=?', (FAILED_CHUNK,)).fetchone()
        if batch is None or batch['session_id']!=args.session or batch['capture_id']!=args.capture or batch['eos']!=0:
            raise RuntimeError('Failed work capture relationship changed')
        if any(counts.values()): raise RuntimeError('Failed work already has primary results')
        sole = db.execute("SELECT id FROM chunks WHERE session_id=? AND status='failed' ORDER BY id LIMIT 2",
                          (args.session,)).fetchall()
        if [r[0] for r in sole] != [FAILED_CHUNK]: raise RuntimeError('Unexpected additional failed work')
        # Match public GET shape exactly, excluding filesystem path.
        row.pop('path')
        for key in ('metadata','provenance'):
            if row[key]: row[key] = json.loads(row[key])
        return dict(chunk=row, result_counts=counts, scaffold_sha256=scaffold, batch=dict(batch), failed_ids=[FAILED_CHUNK])

def recovered(args):
    """Former failed chunk is complete; no retry permission or API write."""
    with connect(args.db,args.deadline) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        row=db.execute('SELECT * FROM chunks WHERE id=? AND session_id=?',(FAILED_CHUNK,args.session)).fetchone()
        if row is None:raise RuntimeError('Recovered chunk missing')
        row=dict(row)
        if tuple(row[k] for k in ('seq','status','timeline_start','duration','next_offset','error'))!=(REDACTED_RECOVERED_REQUESTS_OR_PRIOR_SEQUENCE,'completed',REDACTED_PRIOR_CHUNK_START,REDACTED_PRIOR_CHUNK_DURATION,REDACTED_PRIOR_CHUNK_DURATION,None):
            raise RuntimeError('Former failed chunk is not complete')
        failed=db.execute("SELECT COUNT(*) FROM chunks WHERE session_id=? AND status='failed'",(args.session,)).fetchone()[0]
        if failed:raise RuntimeError('Failed work exists; automatic retry prohibited')
        states=dict(db.execute('SELECT status,COUNT(*) FROM chunks WHERE session_id=? GROUP BY status',(args.session,)).fetchall())
        batch=db.execute('SELECT b.id,b.session_id,b.capture_id,b.eos FROM recording_batches b JOIN recording_work w ON w.batch_id=b.id WHERE w.chunk_id=?',(FAILED_CHUNK,)).fetchone()
        if batch is None or batch['capture_id']!=args.capture or batch['session_id']!=args.session or batch['eos']!=0:raise RuntimeError('Recovered work capture changed')
        row.pop('path')
        for key in ('metadata','provenance'):
            if row[key]:row[key]=json.loads(row[key])
        return dict(chunk=row,batch=dict(batch),failed_count=failed,states=states)

def compare(before, after):
    """Streaming subset equality: new completions allowed, old results immutable."""
    for path in (Path(before),Path(after)):
        if path.is_symlink() or not path.is_file() or path.stat().st_size>4*MIB:
            raise RuntimeError('Completed manifest file bound')
    def rows(stream):
        previous=''; count=0
        while True:
            raw=stream.readline(4097)
            if not raw: return
            if len(raw)>4096: raise RuntimeError('Manifest row bound')
            row=json.loads(raw); count+=1
            if count>8192 or set(row)!={'id','sha256'} or not isinstance(row['id'],str) or row['id']<=previous:
                raise RuntimeError('Manifest duplicate/order/shape')
            previous=row['id']; yield row
    count=0
    with Path(before).open('rb') as a, Path(after).open('rb') as b:
        after_rows=rows(b); current=next(after_rows,None)
        for wanted in rows(a):
            while current is not None and current['id']<wanted['id']:
                current=next(after_rows,None)
            if current!=wanted: raise RuntimeError('Completed result changed/lost')
            count+=1
        # Check the whole new manifest, including rows after the final old ID.
        for _ in after_rows: pass
    return count




