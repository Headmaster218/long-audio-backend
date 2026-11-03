"""Write-ahead initial intake intent; never infer missing intent or replace bytes."""
import os
from pathlib import Path
import stat

from . import recorder_intake as single
from . import recording_uploader as u
from .recorder_delivery import inspect_delivery

SCHEMA = 'recorder-initial-intent-v1'
NAME = 'initial-intent.json'
require = single.require


def intent(root, header, delivery, report):
    return dict(schema=SCHEMA, job_dir=str(root), header=header,
                path=str(delivery), delivery=report)


def read(root, destination, delivery, remaining, *, same=None):
    same = same or (lambda actual, expected: actual == expected)
    value=single.manifest_read(root/NAME)
    require(type(value) is dict and set(value)=={'schema','job_dir','header','path','delivery'}
            and value['schema']==SCHEMA and value['job_dir']==str(root)
            and value['path']==str(Path(delivery).absolute()), 'capture_initial_intent_binding')
    header=value['header']
    require(type(header) is dict and same(header.get('target'),destination),'capture_initial_intent_target')
    require(same(inspect_delivery(value['path'],validate_metadata=True,deadline_seconds=remaining()),value['delivery']),
            'capture_initial_intent_original_changed')
    return value


def check_file(path, raw):
    """An absent file or exact existing prefix only; no writes during preflight."""
    if not os.path.lexists(path):return
    single.control(path)
    info=path.stat()
    require(info.st_size<=len(raw),'capture_initial_control_oversized')
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        held=os.fstat(stream.fileno())
        require((held.st_dev,held.st_ino)==(info.st_dev,info.st_ino),'capture_initial_control_replaced')
        data=stream.read(len(raw)+1)
        require(data==raw[:len(data)] and len(data)==info.st_size,'capture_initial_control_prefix_mismatch')
        require(u._signature(os.fstat(stream.fileno()))==u._signature(info)
                and u._signature(path.stat())==u._signature(info),'capture_initial_control_changed')


def complete_file(path, raw, remaining):
    check_file(path,raw);remaining()
    exists=os.path.lexists(path)
    flags=os.O_WRONLY|os.O_NOFOLLOW|os.O_NONBLOCK
    fd=os.open(path,flags|(os.O_APPEND if exists else os.O_CREAT|os.O_EXCL),0o600)
    try:
        info=os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.geteuid()
                and info.st_mode&0o077==0 and info.st_size<=len(raw),'capture_initial_control_unsafe')
        # Re-read through the path before extending this held inode.
        check_file(path,raw)
        actual=path.stat()
        require((actual.st_dev,actual.st_ino,actual.st_size)==(info.st_dev,info.st_ino,info.st_size),
                'capture_initial_control_replaced')
        tail=memoryview(raw)[info.st_size:]
        while tail:
            remaining();n=os.write(fd,tail);require(n>0,'capture_initial_control_write');tail=tail[n:]
        remaining();os.fsync(fd)
    finally:os.close(fd)
    u._fsync_dir(path.parent);check_file(path,raw)
    require(path.stat().st_size==len(raw),'capture_initial_control_incomplete')


def scaffold_preflight(root, value):
    require(set(p.name for p in root.iterdir())<=
            {NAME,'capture.json','intake.lock','admissions','uploader'},'capture_unexpected_job_files')
    check_file(root/'capture.json',single._json(value['header']))
    for name in ('admissions','uploader'):
        directory=root/name
        if os.path.lexists(directory):single.private_directory(directory)
    if (root/'admissions').exists():
        require(set(p.name for p in (root/'admissions').iterdir())<={'0'},'capture_recovery_requires_initial_only')
        parent=root/'admissions/0'
        if os.path.lexists(parent):
            single.private_directory(parent)
            require(set(p.name for p in parent.iterdir())<={'snapshot','admission.json'},'capture_partial_admission_unexpected')
            receipt=dict(schema=value['header']['schema'],ordinal=0,target=value['header']['target'],
                path=value['path'],snapshot_path=str(parent/'snapshot'),delivery=value['delivery'])
            check_file(parent/'admission.json',single._json(receipt))
    if (root/'uploader').exists():
        require(set(p.name for p in (root/'uploader').iterdir())<={'uploader.lock'},
                'capture_recovery_requires_pristine_uploader')
        if os.path.lexists(root/'uploader/uploader.lock'):
            single.control(root/'uploader/uploader.lock')
            require((root/'uploader/uploader.lock').stat().st_size==0,'capture_initial_lock_not_empty')


def complete_scaffold(root,value,remaining):
    scaffold_preflight(root,value)
    for name in ('admissions','uploader'):
        directory=root/name
        if not directory.exists():directory.mkdir(mode=0o700)
        single.private_directory(directory);u._fsync_dir(directory)
    complete_file(root/'capture.json',single._json(value['header']),remaining)
    u._fsync_dir(root)
