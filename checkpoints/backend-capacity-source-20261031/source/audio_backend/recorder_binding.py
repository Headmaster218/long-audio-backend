"""Disabled local session/source binding proposal journal; never an HTTP client.

An explicit target is an unverified operator declaration. Journal creation is not
server reservation, source ownership transfer, or permission to ingest/delete.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from .recorder_delivery import Refused
from .recorder_sequence import _verified_records, _report
from .recorder_snapshot import _deadline, _json, _open_directory, _same_directory

SCHEMA='recorder-binding-proposal-v1'
PENDING='binding.pending.json'
FINAL='binding.json'
MAX_BYTES=65536


def require(ok,reason):
    if not ok:raise Refused(reason)


def target(origin,backend_id,session_id,capture_id,capture_origin_frame=0):
    require(type(origin) is str and 1<=len(origin)<=512 and all(33<=ord(c)<=126 for c in origin),'binding_origin')
    require('?' not in origin and '#' not in origin,'binding_origin')
    try:
        parts=urlsplit(origin);port=parts.port;host=parts.hostname
    except ValueError as exc:raise Refused('binding_origin') from exc
    require(parts.scheme in ('http','https') and host and parts.username is None and parts.password is None and parts.path in ('','/'),'binding_origin')
    if ':' in host:
        try:host='['+ipaddress.IPv6Address(host).compressed+']'
        except ValueError as exc:raise Refused('binding_origin') from exc
    else:require(re.fullmatch(r'[a-z0-9.-]{1,253}',host),'binding_origin')
    require(parts.scheme!='http' or host in ('127.0.0.1','localhost','[::1]'),'binding_https_required')
    require(port is None or 1<=port<=65535,'binding_port')
    if port==(443 if parts.scheme=='https' else 80):port=None
    normalized=parts.scheme+'://'+host+(':'+str(port) if port else '')
    for value in (backend_id,session_id,capture_id):
        require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',value),'binding_target_id')
    require(type(capture_origin_frame) is int and 0<=capture_origin_frame<2**53,'binding_origin_frame')
    return dict(origin=normalized,backend_id=backend_id,session_id=session_id,capture_id=capture_id,capture_origin_frame=capture_origin_frame)


def _proposal(paths,destination,remaining):
    records,_=_verified_records(paths,remaining);report=_report(records)
    require(len(report['captures'])==1,'binding_multiple_captures')
    capture=report['captures'][0]
    require(not capture['predecessor_not_supplied'] and not capture['conflicting_ordinals'],'binding_incomplete_or_conflicting_prefix')
    require(all(edge['relation']=='declared_adjacent' for edge in capture['edges']),'binding_unresolved_boundary')
    unique={}
    for row in records:unique.setdefault(row['metadata']['sequence'],row)
    sources=[];end=destination['capture_origin_frame']
    for seq,row in sorted(unique.items()):
        metadata=row['metadata'];delivery=row['delivery']
        require(metadata['sample_rate_hz']==16000 and metadata['channels']==1,'binding_requires_pcm16_mono_16000')
        # Key excludes content: a changed body for the same source must conflict,
        # not silently obtain a new retry key and produce duplicate work.
        identity=dict(schema=SCHEMA,target={key:destination[key] for key in ('origin','backend_id','session_id','capture_id')},source_id=delivery['source_id'])
        key='recorder-source-'+hashlib.sha256(_json(identity)).hexdigest()
        next_end=end+metadata['frames'];require(next_end<2**53,'binding_frame_overflow')
        sources.append(dict(source_id=delivery['source_id'],source_key=key,sequence=seq,epoch=metadata['epoch'],
                            local_metadata_sha256=row['local_metadata_sha256'],wav_sha256=delivery['wav_sha256'],
                            pcm_sha256=delivery['pcm_sha256'],frames=metadata['frames'],start_frame=end,end_frame=next_end))
        end=next_end
    warnings=capture['warning_codes']
    return dict(schema=SCHEMA,target=destination,capture_identity_sha256=capture['capture_identity_sha256'],sources=sources,
                planned_end_frame=end,warning_codes=warnings,activation='disabled',target_identity_verified=False,
                server_reservation_proven=False,physical_continuity_proven=False,strict_import_ready=False)


def build_proposal(paths,*,origin,backend_id,session_id,capture_id,capture_origin_frame=0,deadline_seconds=120):
    destination=target(origin,backend_id,session_id,capture_id,capture_origin_frame)
    return _proposal(paths,destination,_deadline(deadline_seconds))


def _read_journal(path,remaining):
    dfd=_open_directory(path)
    try:
        names=[]
        with os.scandir(dfd) as entries:
            for entry in entries:
                names.append(entry.name);require(len(names)<=2,'binding_unexpected_files')
        require(set(names)=={PENDING,FINAL},'binding_incomplete')
        data=[]
        for name in (PENDING,FINAL):
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=dfd)
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno());require(stat.S_ISREG(info.st_mode) and 0<info.st_size<=MAX_BYTES,'binding_journal_size')
                remaining();raw=stream.read(MAX_BYTES+1);require(len(raw)==info.st_size,'binding_journal_changed')
                after=os.fstat(stream.fileno());named=os.stat(name,dir_fd=dfd,follow_symlinks=False)
                stamp=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
                require(stamp(info)==stamp(after)==stamp(named),'binding_journal_changed')
                data.append(raw)
        require(data[0]==data[1],'binding_receipt_mismatch');_same_directory(path,dfd)
    finally:os.close(dfd)
    try:value=json.loads(data[0])
    except (ValueError,UnicodeError,RecursionError) as exc:raise Refused('binding_journal_json') from exc
    require(type(value) is dict and set(value)=={'proposal','sha256'} and _json(value)==data[0],'binding_journal_schema')
    require(hashlib.sha256(_json(value['proposal'])).hexdigest()==value['sha256'],'binding_journal_checksum')
    return value,data[0]


def bind_proposal(paths,journal,*,origin,backend_id,session_id,capture_id,capture_origin_frame=0,deadline_seconds=120):
    remaining=_deadline(deadline_seconds)
    destination=target(origin,backend_id,session_id,capture_id,capture_origin_frame)
    proposal=_proposal(paths,destination,remaining)
    value=dict(proposal=proposal,sha256=hashlib.sha256(_json(proposal)).hexdigest());raw=_json(value)
    require(len(raw)<=MAX_BYTES,'binding_journal_size')
    journal=Path(journal);pfd=_open_directory(journal.parent)
    try:
        parent=os.fstat(pfd)
        require(parent.st_uid==os.geteuid() and parent.st_mode&0o022==0,'binding_private_parent_required')
        remaining()
        try:os.mkdir(journal.name,0o700,dir_fd=pfd)
        except FileExistsError:
            _,existing=_read_journal(journal,remaining)
            require(existing==raw,'binding_conflict')
            return dict(status='existing_exact_observation',journal_sha256=value['sha256'],strict_import_ready=False,
                        publication_durability_proven=False)
        dfd=os.open(journal.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=pfd)
        try:
            fd=os.open(PENDING,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dfd)
            with os.fdopen(fd,'wb') as file:
                file.write(raw);file.flush();remaining();os.fsync(file.fileno())
            os.link(PENDING,FINAL,src_dir_fd=dfd,dst_dir_fd=dfd,follow_symlinks=False)
            remaining();os.fsync(dfd);_same_directory(journal,dfd)
        finally:os.close(dfd)
        remaining();os.fsync(pfd)
    finally:os.close(pfd)
    return dict(status='created_and_fsynced',journal_sha256=value['sha256'],strict_import_ready=False,
                server_reservation_proven=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshots',nargs='+',type=Path)
    parser.add_argument('--origin',required=True);parser.add_argument('--backend-id',required=True)
    parser.add_argument('--session-id',required=True);parser.add_argument('--capture-id',required=True)
    parser.add_argument('--capture-origin-frame',type=int,default=0)
    parser.add_argument('--journal',type=Path,help='Explicit NEW private journal directory; omitted means read-only proposal')
    args=parser.parse_args(argv)
    options=dict(origin=args.origin,backend_id=args.backend_id,session_id=args.session_id,capture_id=args.capture_id,capture_origin_frame=args.capture_origin_frame)
    try:result=build_proposal(args.snapshots,**options) if args.journal is None else bind_proposal(args.snapshots,args.journal,**options)
    except (OSError,Refused) as exc:
        print(json.dumps(dict(error=str(exc) if isinstance(exc,Refused) else 'binding_file_operation_failed',strict_import_ready=False,preserve_existing_files=True)));return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
