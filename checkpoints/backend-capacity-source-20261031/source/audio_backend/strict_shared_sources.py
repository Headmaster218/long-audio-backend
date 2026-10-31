"""Fresh source-identity shared WAV bodies. Append-only; no GC or file adoption.

Not content-address deduplication: a digest names a full identity tuple, whose
stored bytes must also match. Distinct source identities never share authority.
"""
import json
import os
import re
import stat
from pathlib import Path

MAX_BODIES=262144
MAX_BYTES=64*1024**3


def schema_statements():
    return [
    '''CREATE TABLE IF NOT EXISTS strict_shared_source_usage (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1),
      bodies INTEGER NOT NULL CHECK(typeof(bodies)='integer' AND bodies BETWEEN 0 AND 262144),
      reserved INTEGER NOT NULL CHECK(typeof(reserved)='integer' AND reserved BETWEEN 0 AND 68719476736),
      allocated INTEGER NOT NULL CHECK(typeof(allocated)='integer' AND allocated>=0 AND allocated+reserved<=68719476736))''',
    '''CREATE TABLE IF NOT EXISTS strict_shared_source_body (
      body_id TEXT PRIMARY KEY CHECK(length(body_id)=64), identity TEXT NOT NULL CHECK(length(identity) BETWEEN 1 AND 2048),
      bytes INTEGER NOT NULL CHECK(typeof(bytes)='integer' AND bytes BETWEEN 44 AND 3905536),
      sha256 TEXT NOT NULL CHECK(length(sha256)=64), frames INTEGER NOT NULL CHECK(typeof(frames)='integer' AND frames BETWEEN 1 AND 1920000),
      state TEXT NOT NULL CHECK(state IN ('reserved','published')),
      reserved INTEGER NOT NULL CHECK(typeof(reserved)='integer' AND reserved>0),
      allocated INTEGER, signature TEXT,
      CHECK((state='reserved' AND allocated IS NULL AND signature IS NULL) OR
        (state='published' AND typeof(allocated)='integer' AND allocated>0 AND length(signature) BETWEEN 1 AND 512)))''',
    '''CREATE TRIGGER IF NOT EXISTS strict_shared_source_insert BEFORE INSERT ON strict_shared_source_body
      WHEN NEW.state!='reserved' OR strict_payload_transition('shared',NEW.body_id)!=1
      BEGIN SELECT RAISE(ABORT,'shared_source_insert_unauthorized'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_shared_source_update BEFORE UPDATE ON strict_shared_source_body
      WHEN OLD.state!='reserved' OR NEW.state!='published' OR NEW.body_id IS NOT OLD.body_id
        OR NEW.identity IS NOT OLD.identity OR NEW.bytes IS NOT OLD.bytes OR NEW.sha256 IS NOT OLD.sha256
        OR NEW.frames IS NOT OLD.frames OR NEW.reserved IS NOT OLD.reserved
        OR strict_payload_transition('shared',OLD.body_id)!=1
      BEGIN SELECT RAISE(ABORT,'shared_source_immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_shared_source_delete BEFORE DELETE ON strict_shared_source_body
      BEGIN SELECT RAISE(ABORT,'shared_source_retained'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_shared_usage_insert BEFORE INSERT ON strict_shared_source_usage
      WHEN NEW.singleton!=1 OR NEW.bodies!=0 OR NEW.reserved!=0 OR NEW.allocated!=0
      BEGIN SELECT RAISE(ABORT,'shared_usage_insert'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_shared_usage_update BEFORE UPDATE ON strict_shared_source_usage
      WHEN NEW.singleton IS NOT OLD.singleton OR strict_payload_transition('shared_usage','1')!=1
      BEGIN SELECT RAISE(ABORT,'shared_usage_unauthorized'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_shared_usage_delete BEFORE DELETE ON strict_shared_source_usage
      BEGIN SELECT RAISE(ABORT,'shared_usage_retained'); END''',
    ]


def reference(store,spec):
    from . import strict_payload_v2 as p
    identity=spec['_shared_identity']
    raw=p._canonical(identity).decode()
    p._require(0<len(raw)<=2048,'shared_source_identity_capacity')
    return {'body_id':p._sha(raw.encode()),'identity':identity,'bytes':spec['bytes'],
        'sha256':spec['sha256'],'frames':spec['_frames']}


def _directory(owner,body_id,*,create):
    from . import strict_payload_v2 as p
    p._require(type(body_id) is str and re.fullmatch('[a-f0-9]{64}',body_id),'shared_body_id')
    data=Path(owner.store.settings.data_dir).absolute();root=data/'strict-shared-sources-v1';directory=root/body_id[:4]
    p._require(data.is_dir() and not data.is_symlink(),'shared_data_directory')
    for item,parent in ((root,data),(directory,root)):
        if create:
            try:item.mkdir(mode=0o700)
            except FileExistsError:pass
        p._require(item.is_dir() and not item.is_symlink(),'shared_body_directory')
        if create:
            fd=p._open_file(owner,parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:p._close(owner,fd)
    fd=p._open_file(owner,directory,os.O_RDONLY|os.O_DIRECTORY)
    return directory,fd


def _row_matches(p,row,ref):
    return (row['body_id']==ref['body_id'] and row['identity']==p._canonical(ref['identity']).decode()
        and row['bytes']==ref['bytes'] and row['sha256']==ref['sha256'] and row['frames']==ref['frames'])


def validate(owner,ref):
    """Fully hash one published body inside the caller's existing envelope."""
    from . import strict_payload_v2 as p
    with p._db(owner,shared_acquire=True) as db:
        row=db.execute('SELECT * FROM strict_shared_source_body WHERE body_id=?',(ref['body_id'],)).fetchone()
        p._require(row is not None and _row_matches(p,row,ref) and row['state']=='published','shared_source_binding')
        row=dict(row)
    directory,fd=_directory(owner,ref['body_id'],create=False);p._close(owner,fd)
    path=directory/(ref['body_id']+'.body')
    digest,signature=p._file_digest(owner,path,row['bytes'],json.loads(row['signature']),wave_frames=row['frames'])
    p._require(digest==row['sha256'],'shared_source_digest')
    info=os.stat(path,follow_symlinks=False)
    p._require(stat.S_ISREG(info.st_mode) and info.st_blocks*512==row['allocated']
        and p._signature(info)==signature,'shared_source_allocation')
    return row


def ensure(owner,spec):
    from . import strict_payload_v2 as p
    ref=reference(owner.store,spec);key=ref['body_id'];owner.budget.projection(len(p._canonical(ref)))
    with p._db(owner,shared_acquire=True) as db:
        row=db.execute('SELECT * FROM strict_shared_source_body WHERE body_id=?',(key,)).fetchone()
        if row is not None:
            p._require(_row_matches(p,row,ref),'shared_source_identity_changed')
            p._require(row['state']=='published','shared_source_reserved_unknown')
    if row is not None:
        validate(owner,ref);return ref
    directory,dfd=_directory(owner,key,create=True)
    reservation=2*((spec['bytes']+4095)//4096)*4096
    try:
        with p._db(owner,shared_acquire=True) as db:
            owner.scan=os.scandir(dfd);count=0;reserved_inodes={}
            try:
                for entry in owner.scan:
                    owner.budget.check();count+=1
                    p._require(count+2<=128 and re.fullmatch('[a-f0-9]{64}\\.(body|stage)',entry.name)
                        and entry.name[:4]==key[:4],'shared_directory_unknown')
                    p._require(entry.name not in (key+'.body',key+'.stage'),'shared_unexpected_file')
                    info=entry.stat(follow_symlinks=False)
                    registered=db.execute('SELECT * FROM strict_shared_source_body WHERE body_id=?',(entry.name[:64],)).fetchone()
                    p._require(registered is not None and stat.S_ISREG(info.st_mode),'shared_unregistered_file')
                    if registered['state']=='published':
                        p._require(entry.name.endswith('.body') and info.st_size==registered['bytes']
                            and info.st_blocks*512==registered['allocated']
                            and p._signature(info)==json.loads(registered['signature']),'shared_registered_file_changed')
                    else:
                        # Unknown publication remains fully reserved. Never
                        # promote/adopt this file, even if its digest matches.
                        p._require(registered['state']=='reserved' and 0<=info.st_size<=registered['bytes']
                            and info.st_blocks*512<=registered['reserved'],'shared_unknown_charge')
                        inode=(info.st_dev,info.st_ino)
                        prior=reserved_inodes.setdefault(entry.name[:64],inode)
                        p._require(prior==inode,'shared_unknown_multiple_inodes')
            finally:owner.close_scan()
        space=os.fstatvfs(dfd);reserve=max(owner.store.settings.disk_reserve_bytes,owner.store.settings.recording_disk_reserve_bytes)
        p._require(space.f_bavail*space.f_frsize>=reserve+reservation,'shared_disk_reserve')
        with p._db(owner,write=True,shared_acquire=True) as db:
            owner.transition_db=db;owner.transition={('shared',key),('shared_usage','1')}
            try:
                db.execute('INSERT OR IGNORE INTO strict_shared_source_usage VALUES(1,0,0,0)')
                usage=db.execute('SELECT bodies,reserved,allocated FROM strict_shared_source_usage WHERE singleton=1').fetchone()
                p._require(usage[0]<MAX_BODIES and usage[1]+usage[2]+reservation<=MAX_BYTES,'shared_source_capacity')
                db.execute('INSERT INTO strict_shared_source_body VALUES(?,?,?,?,?,\'reserved\',?,NULL,NULL)',
                    (key,p._canonical(ref['identity']).decode(),spec['bytes'],spec['sha256'],spec['_frames'],reservation))
                db.execute('UPDATE strict_shared_source_usage SET bodies=bodies+1,reserved=reserved+? WHERE singleton=1',(reservation,))
            finally:owner.transition.clear();owner.transition_db=None
        stage=directory/(key+'.stage');final=directory/(key+'.body')
        out=p._open_file(owner,stage,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        try:p.base._copy_spec(owner,out,spec);os.fsync(out)
        finally:p._close(owner,out)
        digest,_=p._file_digest(owner,stage,spec['bytes'],wave_frames=spec['_frames'])
        p._require(digest==spec['sha256'],'shared_copy_digest')
        os.link(stage,final,follow_symlinks=False);os.fsync(dfd);os.unlink(stage);os.fsync(dfd)
        digest,signature=p._file_digest(owner,final,spec['bytes'],wave_frames=spec['_frames'])
        info=os.stat(final,follow_symlinks=False);allocated=info.st_blocks*512
        p._require(digest==spec['sha256'] and p._signature(info)==signature and 0<allocated<=reservation,'shared_publish_integrity')
    finally:p._close(owner,dfd)
    with p._db(owner,write=True,shared_acquire=True) as db:
        owner.transition_db=db;owner.transition={('shared',key),('shared_usage','1')}
        try:
            p._require(db.execute("UPDATE strict_shared_source_body SET state='published',allocated=?,signature=? WHERE body_id=? AND state='reserved'",
                (allocated,p._canonical(signature).decode(),key)).rowcount==1,'shared_reservation_changed')
            db.execute('UPDATE strict_shared_source_usage SET reserved=reserved-?,allocated=allocated+? WHERE singleton=1',(reservation,allocated))
        finally:owner.transition.clear();owner.transition_db=None
    return ref
