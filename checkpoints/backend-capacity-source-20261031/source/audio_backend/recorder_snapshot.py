"""Explicit bounded local delivery snapshot; never ingest or delete originals.

Creates a NEW directory only. Existing or interrupted destinations are never
mutated under the required exclusively controlled private-parent precondition.
Inspection reconciles observed bytes, not crash durability or authenticity.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from .recorder_delivery import inspect_delivery, Refused, BLOCK, MAX_MARKER, MAX_METADATA, MAX_WAV

FILES = ('delivery.v1.txt', 'segment.v1.bin', 'audio.wav')
PENDING = 'snapshot.pending.json'
RECEIPT = 'snapshot.json'
MAX_RECEIPT = 8192
SCHEMA = 'recorder-owned-snapshot-v1'
DISK_HEADROOM = 16 * 1024 * 1024


def _require(ok, reason):
    if not ok: raise Refused(reason)


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii') + b'\n'


def _stamp(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _deadline(seconds):
    _require(type(seconds) in (int,float) and 0 < seconds <= 120, 'snapshot_deadline_bounds')
    end=time.monotonic()+seconds
    def remaining():
        left=end-time.monotonic()
        _require(left>0,'snapshot_deadline_exceeded')
        return min(120,left)
    return remaining


def _open_directory(path):
    return os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)


def _same_directory(path,dfd):
    actual=os.stat(path,follow_symlinks=False);held=os.fstat(dfd)
    _require(stat.S_ISDIR(actual.st_mode) and (actual.st_dev,actual.st_ino)==(held.st_dev,held.st_ino),'snapshot_directory_replaced')


def _file_hash(dfd,name,limit,remaining):
    fd=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=dfd)
    with os.fdopen(fd,'rb') as source:
        before=_stamp(os.fstat(source.fileno()))
        _require(stat.S_ISREG(before[2]) and before[3]<=limit,'snapshot_regular_size')
        digest=hashlib.sha256(); size=0
        while True:
            remaining(); block=source.read(min(BLOCK,limit-size+1))
            if not block:break
            size+=len(block); _require(size<=limit,'snapshot_file_size'); digest.update(block)
        _require(size==before[3] and _stamp(os.fstat(source.fileno()))==before,'snapshot_file_changed')
        _require(_stamp(os.stat(name,dir_fd=dfd,follow_symlinks=False))==before,'snapshot_file_replaced')
    return dict(bytes=size,sha256=digest.hexdigest())


def _copy(source_fd,dest_fd,name,limit,expected,remaining):
    fd=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=source_fd)
    with os.fdopen(fd,'rb') as source:
        before=_stamp(os.fstat(source.fileno()))
        _require(stat.S_ISREG(before[2]) and before[3]==expected['bytes'] and before[3]<=limit,'snapshot_source_size')
        out=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dest_fd)
        with os.fdopen(out,'wb') as target:
            size=0; digest=hashlib.sha256()
            while True:
                remaining(); block=source.read(min(BLOCK,limit-size+1))
                if not block:break
                size+=len(block);_require(size<=limit,'snapshot_copy_size');digest.update(block);target.write(block)
            _require(dict(bytes=size,sha256=digest.hexdigest())==expected,'snapshot_source_hash')
            _require(_stamp(os.fstat(source.fileno()))==before and _stamp(os.stat(name,dir_fd=source_fd,follow_symlinks=False))==before,'snapshot_source_changed')
            target.flush();remaining();os.fsync(target.fileno())


def _inventory(dfd,remaining):
    return {name:_file_hash(dfd,name,limit,remaining) for name,limit in zip(FILES,(MAX_MARKER,MAX_METADATA,MAX_WAV))}


def _receipt(dfd,remaining):
    fd=os.open(RECEIPT,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=dfd)
    with os.fdopen(fd,'rb') as stream:
        before=_stamp(os.fstat(stream.fileno()))
        _require(stat.S_ISREG(before[2]) and 0<before[3]<=MAX_RECEIPT,'snapshot_receipt_size')
        remaining();raw=stream.read(MAX_RECEIPT+1)
        _require(len(raw)==before[3] and _stamp(os.fstat(stream.fileno()))==before,'snapshot_receipt_changed')
        _require(_stamp(os.stat(RECEIPT,dir_fd=dfd,follow_symlinks=False))==before,'snapshot_receipt_replaced')
    try: value=json.loads(raw)
    except (ValueError,UnicodeError,RecursionError) as exc:raise Refused('snapshot_receipt_json') from exc
    _require(_json(value)==raw,'snapshot_receipt_noncanonical')
    _require(type(value) is dict and set(value)=={'body','sha256'} and type(value['body']) is dict,'snapshot_receipt_schema')
    body=value['body'];_require(hashlib.sha256(_json(body)).hexdigest()==value['sha256'],'snapshot_receipt_checksum')
    _require(set(body)=={'schema','files','delivery'} and body['schema']==SCHEMA,'snapshot_receipt_schema')
    _require(type(body['files']) is dict and set(body['files'])==set(FILES) and type(body['delivery']) is dict,'snapshot_receipt_schema')
    return value,raw


def inspect_snapshot(directory,*,deadline_seconds=120):
    remaining=_deadline(deadline_seconds)
    dfd=_open_directory(directory)
    try:
        names=[]
        with os.scandir(dfd) as entries:
            for entry in entries:
                names.append(entry.name);_require(len(names)<=5,'snapshot_unexpected_entries')
        _require(set(names)==set(FILES)|{PENDING,RECEIPT},'snapshot_incomplete_or_unexpected')
        value,raw=_receipt(dfd,remaining)
        pending=_file_hash(dfd,PENDING,MAX_RECEIPT,remaining)
        _require(pending==dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()),'snapshot_pending_mismatch')
        actual=_inventory(dfd,remaining)
        _require(actual==value['body']['files'],'snapshot_content_mismatch')
        _same_directory(directory,dfd)
        delivery=inspect_delivery(directory,deadline_seconds=remaining(),validate_metadata=True)
        _same_directory(directory,dfd)
        _require(delivery==value['body']['delivery'],'snapshot_delivery_mismatch')
        # Semantic reads and receipt verification must concern the same bytes.
        _require(_inventory(dfd,remaining)==actual and _receipt(dfd,remaining)[1]==raw,'snapshot_changed_during_inspection')
    finally:
        os.close(dfd)
    return dict(schema=SCHEMA,observed_snapshot_verified=True,publication_durability_proven=False,
                strict_import_ready=False,delivery=delivery)


def create_snapshot(source,destination,*,deadline_seconds=120):
    remaining=_deadline(deadline_seconds)
    source=Path(source);destination=Path(destination)
    # Validate before creating anything. There is no fallback to transport-only.
    delivery=inspect_delivery(source,deadline_seconds=remaining(),validate_metadata=True)
    sfd=_open_directory(source)
    try:
        expected=_inventory(sfd,remaining)
        _require(expected['audio.wav']['sha256']==delivery['wav_sha256'] and expected['segment.v1.bin']['sha256']==delivery['metadata_sha256'],'snapshot_preflight_changed')
        # Anchor creation and parent-directory persistence to the same parent
        # descriptor. One creator wins; never mutate an existing destination.
        pfd=_open_directory(destination.parent)
        try:
            parent=os.fstat(pfd)
            _require(parent.st_uid==os.geteuid() and parent.st_mode & 0o022==0,'snapshot_private_parent_required')
            free=os.fstatvfs(pfd)
            _require(free.f_bavail*free.f_frsize>=sum(v['bytes'] for v in expected.values())+MAX_RECEIPT+DISK_HEADROOM,'snapshot_disk_headroom')
            remaining();os.mkdir(destination.name,0o700,dir_fd=pfd)
            dfd=os.open(destination.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=pfd)
            try:
                for name,limit in zip(FILES,(MAX_MARKER,MAX_METADATA,MAX_WAV)):
                    _copy(sfd,dfd,name,limit,expected[name],remaining)
                _same_directory(destination,dfd)
                copied=inspect_delivery(destination,deadline_seconds=remaining(),validate_metadata=True)
                _same_directory(destination,dfd)
                _require(copied==delivery and _inventory(dfd,remaining)==expected,'snapshot_copy_validation')
                body=dict(schema=SCHEMA,files=expected,delivery=delivery)
                raw=_json(dict(body=body,sha256=hashlib.sha256(_json(body)).hexdigest()))
                _require(len(raw)<=MAX_RECEIPT,'snapshot_receipt_size')
                fd=os.open(PENDING,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dfd)
                with os.fdopen(fd,'wb') as receipt:
                    receipt.write(raw);receipt.flush();remaining();os.fsync(receipt.fileno())
                # Atomic non-overwriting publication of exact closed bytes.
                # Keep the pending link: interrupted state is never deleted.
                os.link(PENDING,RECEIPT,src_dir_fd=dfd,dst_dir_fd=dfd,follow_symlinks=False)
                remaining();os.fsync(dfd)
                _same_directory(destination,dfd)
            finally:
                os.close(dfd)
            remaining();os.fsync(pfd)
        finally:
            os.close(pfd)
    finally:
        os.close(sfd)
    # Success is returned only after the original descriptors positively close.
    return dict(schema=SCHEMA,created_and_fsynced=True,strict_import_ready=False,
                immutable_publication_proven=False,delivery=delivery)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    create=sub.add_parser('create');create.add_argument('source',type=Path);create.add_argument('destination',type=Path)
    inspect=sub.add_parser('inspect');inspect.add_argument('directory',type=Path)
    args=parser.parse_args(argv)
    try:
        result=create_snapshot(args.source,args.destination) if args.action=='create' else inspect_snapshot(args.directory)
    except (OSError,Refused) as exc:
        print(json.dumps(dict(error=str(exc) if isinstance(exc,Refused) else 'snapshot_file_operation_failed',
                              strict_import_ready=False,preserve_existing_files=True)))
        return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
