#!/usr/bin/env python3
"""Explicit offline path migration of a copied audio-data directory.

Dry-run is the default. This never invokes service startup/cleanup or changes
IDs, timestamps, voice evidence, job states or result history.
"""
from __future__ import annotations
import argparse,contextlib,hashlib,json,os,re,sqlite3
from pathlib import Path,PurePosixPath,PureWindowsPath


class MigrationError(RuntimeError):pass


@contextlib.contextmanager
def exclusive_instance(root):
    path=root/'instance.lock'
    if path.is_symlink() or (path.exists() and not path.is_file()):raise MigrationError('Unsafe instance lock')
    f=path.open('a+b')
    try:
        f.seek(0);f.write(b'0');f.flush();f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:raise MigrationError('Data directory is in use; stop its service first')from exc
        yield
    finally:f.close()


def _old_path(raw):
    if not isinstance(raw,str) or not raw or '\x00'in raw or any(x in ('.','..')for x in re.split(r'[/\\]',raw)):
        raise MigrationError('Invalid or traversing source path')
    path=PureWindowsPath(raw) if PureWindowsPath(raw).drive else PurePosixPath(raw)
    if not path.is_absolute():raise MigrationError('Source path must be absolute')
    return path


def _fingerprint(db):
    """Canonical hash of all logical rows except the one authorized path field."""
    h=hashlib.sha256()
    for name,sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        h.update(json.dumps([name,sql],sort_keys=True).encode())
        info=db.execute('PRAGMA table_xinfo("'+name.replace('"','""')+'")').fetchall()
        columns=[r[1]for r in info]
        selected=[c for c in columns if not(name=='chunks'and c=='path')]
        if not selected:continue
        quoted=','.join('"'+c.replace('"','""')+'"'for c in selected)
        keys=[r[1]for r in sorted(info,key=lambda r:r[5])if r[5]]
        order=','.join('"'+k.replace('"','""')+'"'for k in keys)if keys else'rowid'
        rows=db.execute('SELECT '+quoted+' FROM "'+name.replace('"','""')+'" ORDER BY '+order)
        for row in rows:
            data=[{'blob_hex':v.hex()}if isinstance(v,bytes)else v for v in row]
            h.update(json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode());h.update(b'\n')
    return h.hexdigest()


def migrate(data_dir,from_root,*,apply=False):
    supplied=Path(data_dir).absolute()
    if supplied.is_symlink() or not supplied.is_dir():raise MigrationError('New data directory must be an existing real copied directory')
    root=supplied.resolve();old=_old_path(from_root)
    if isinstance(old,PurePosixPath)and str(old)==str(root):raise MigrationError('Refusing source directory; specify a different copied destination')
    if isinstance(old,PureWindowsPath)and os.name=='nt'and old==PureWindowsPath(str(root)):
        raise MigrationError('Refusing source directory; specify a different copied destination')
    dbpath=root/'jobs.sqlite3';uploads=root/'uploads'
    if dbpath.is_symlink()or not dbpath.is_file()or dbpath.stat().st_nlink!=1:raise MigrationError('Database must be a regular independent copy, not a link')
    if uploads.is_symlink()or not uploads.is_dir()or uploads.resolve()!=root/'uploads':raise MigrationError('Unsafe or missing uploads directory')
    if isinstance(old,PurePosixPath):
        source=Path(str(old))/'jobs.sqlite3'
        if source.exists()and os.path.samefile(source,dbpath):raise MigrationError('Source and destination database are the same file')
    with exclusive_instance(root):
        for suffix in ('-wal','-journal','-shm'):
            path=Path(str(dbpath)+suffix)
            if path.is_symlink()or(path.exists()and not path.is_file()):raise MigrationError('Unsafe SQLite coordination sidecar')
            if suffix!='-shm'and path.exists()and path.stat().st_size:
                raise MigrationError('Nonempty SQLite WAL/journal: use a clean stopped/checkpointed copy; do not delete sidecars')
        # A mode=ro WAL connection does NOT exclude a concurrent writer, even
        # with BEGIN EXCLUSIVE. Acquire the real write reservation first, then
        # enforce query_only for dry-run; no rows are written in that branch.
        mode='rw'
        with contextlib.closing(sqlite3.connect(dbpath.as_uri()+'?mode='+mode,uri=True,timeout=0))as db:
            try:
                db.execute('BEGIN EXCLUSIVE')
                if not apply:db.execute('PRAGMA query_only=ON')
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():raise MigrationError('Database integrity/foreign-key check failed')
                tables={r[0]for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'chunks','session_file_cleanup'}<=tables:raise MigrationError('Unsupported schema: chunks and cleanup-intent tables required')
                if db.execute('SELECT 1 FROM session_file_cleanup LIMIT 1').fetchone():raise MigrationError('Pending cleanup intents: finish cleanup on the original service, stop, then make a fresh copy')
                if db.execute("SELECT 1 FROM chunks WHERE status='running' LIMIT 1").fetchone():raise MigrationError('Running job state: pause/stop the original service cleanly, then make a fresh copy')
                changes=[];targets=set();hash_checks=[]
                for cid,raw,digest in db.execute('SELECT id,path,digest FROM chunks ORDER BY id'):
                    source=_old_path(raw)
                    if type(source)is not type(old)or source.parent!=old/'uploads':raise MigrationError('Stored path is outside the declared old uploads directory')
                    name=source.name
                    if not re.fullmatch(r'[0-9a-f]{32}\.audio',name):raise MigrationError('Unexpected upload filename; expected32hex.audio')
                    target=uploads/name;key=str(target).casefold()
                    if key in targets:raise MigrationError('Two chunk paths collide at destination')
                    targets.add(key)
                    if target.is_symlink()or not target.is_file()or target.resolve().parent!=uploads:raise MigrationError('Missing or unsafe copied upload: '+name)
                    if not isinstance(digest,str)or not re.fullmatch('[0-9a-f]{64}',digest):raise MigrationError('Invalid recorded audio digest')
                    with os.fdopen(os.open(target,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)),'rb')as stream:
                        stat=os.fstat(stream.fileno());actual=hashlib.file_digest(stream,'sha256').hexdigest();after=os.fstat(stream.fileno())
                    if actual!=digest or(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise MigrationError('Copied upload changed or SHA256 mismatch: '+name)
                    changes.append((str(target),cid,raw));hash_checks.append((target,(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)))
                before=_fingerprint(db)
                if apply:
                    if not hasattr(db,'setconfig')or not hasattr(sqlite3,'SQLITE_DBCONFIG_ENABLE_TRIGGER'):raise MigrationError('Python3.12+ SQLite connection-local trigger control required')
                    for target,stat in hash_checks:
                        now=target.stat()
                        if target.is_symlink()or(now.st_dev,now.st_ino,now.st_size,now.st_mtime_ns)!=stat:raise MigrationError('Copied upload changed after validation')
                    prior=db.getconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_TRIGGER)
                    db.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_TRIGGER,False)
                    try:
                        for new,cid,previous in changes:
                            cursor=db.execute('UPDATE chunks SET path=? WHERE id=? AND path=?',(new,cid,previous))
                            if cursor.rowcount!=1:raise MigrationError('Chunk changed during migration')
                    finally:db.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_TRIGGER,prior)
                    if _fingerprint(db)!=before:raise MigrationError('Unexpected non-path change; transaction rolled back')
                    db.commit()
                else:db.rollback()
                return {'applied':apply,'dry_run':not apply,'chunks_validated':len(changes),'paths_updated':len(changes)if apply else 0,
                        'data_dir':str(root),'declared_old_root':str(old),'preserved_non_path_rows_sha256':before,
                        'note':'Only chunks.path changes. Original directory never opened for writing. Lock/SQLite coordination files may be created.'}
            except sqlite3.Error as exc:
                db.rollback();raise MigrationError('SQLite unavailable/locked or invalid: '+str(exc))from exc
            except BaseException:
                db.rollback();raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',required=True,help='NEW copied directory; never the original')
    parser.add_argument('--from-root',required=True,help='Exact old data root, POSIX or Windows absolute path')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true',help='Commit path-only migration after all checks; default is dry-run')
    mode.add_argument('--dry-run',action='store_true',help='Validate only (the default)')
    args=parser.parse_args()
    try:print(json.dumps(migrate(args.data_dir,args.from_root,apply=args.apply),ensure_ascii=False,indent=2))
    except(MigrationError,OSError,ValueError)as exc:parser.exit(2,'Migration refused: '+str(exc)+'\n')

if __name__=='__main__':main()
