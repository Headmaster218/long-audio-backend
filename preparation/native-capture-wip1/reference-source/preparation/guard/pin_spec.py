#!/usr/bin/env python3
"""Prepare one reviewable spec only. Does not launch, download, or install."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
HERE=Path(__file__).resolve().parent
PREP=HERE.parent
WORKSPACE=PREP.parent
STAGES={'synthetic':300,'model_runtime':600,'corpus':1800,'wheels_env':600}

def read(path, maximum=32*2**20):
    path=Path(path)
    before=path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size>maximum:raise ValueError('regular_pin_required')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        opened=os.fstat(fd)
        if (before.st_dev,before.st_ino)!=(opened.st_dev,opened.st_ino):raise ValueError('pin_open_race')
        pieces=[];total=0
        while True:
            chunk=os.read(fd,min(65536,maximum+1-total))
            if not chunk:break
            total+=len(chunk)
            if total>maximum:raise ValueError('pin_growth')
            pieces.append(chunk)
        after=os.fstat(fd);current=path.lstat()
        key=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        if key(before)!=key(after) or key(after)!=key(current):raise ValueError('pin_changed')
        return b''.join(pieces)
    finally:os.close(fd)

def sha(path):return hashlib.sha256(read(path)).hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=STAGES,required=True)
    p.add_argument('--entrypoint',type=Path,required=True);p.add_argument('--external',type=Path,action='append',default=[])
    p.add_argument('--output',type=Path,required=True);p.add_argument('--work-seconds',type=int)
    p.add_argument('args',nargs=argparse.REMAINDER);a=p.parse_args()
    entry=a.entrypoint.absolute();output=a.output.absolute()
    if entry.resolve()!=entry or not entry.is_relative_to(PREP) or entry.suffix!='.py':raise ValueError('private_entrypoint_required')
    if output.resolve()!=output or not output.is_relative_to(PREP):raise ValueError('private_spec_output_required')
    work=STAGES[a.stage]-60 if a.work_seconds is None else a.work_seconds
    if not 0<work<=STAGES[a.stage]-60:raise ValueError('work_outside_stage')
    source=WORKSPACE/'reconstruction-rc3-dev3'
    if a.stage=='synthetic':
        source=HERE/'fixtures'
        source_files={str(q.relative_to(source)):sha(q) for q in sorted(source.iterdir())}
    else:
        path=WORKSPACE/'reconstruction-rc3-dev3-real-design'/'DESIGN_MANIFEST.json'
        if sha(path)!='5c71577ca40f5f3a220b3982582a14dbbfa2da4fd642a0220592fc490e99d6ec':raise ValueError('design_pin_changed')
        design=json.loads(read(path));source_files={q['path']:q['sha256'] for q in design['source_files']}
    external=set([entry]+[q.absolute() for q in a.external])
    if a.stage=='synthetic':external.update([HERE/'qualified_guard.snapshot.py',HERE/'guard-test-ids.json'])
    for path in external:
        if path.resolve()!=path or not path.is_relative_to(WORKSPACE):raise ValueError('external_pin_outside_workspace')
    if not 1<=len(external)<=64:raise ValueError('external_pin_count')
    extra=a.args[1:] if a.args[:1]==['--'] else a.args
    executable=Path(sys.executable).resolve()
    spec={'schema':2,'stage':a.stage,'absolute_seconds':STAGES[a.stage],'work_seconds':work,
          'address_space_cap_bytes':2**30,'guard_sha256':sha(HERE/'acquisition_guard.py'),
          'source_root':str(source),'source_files':source_files,'cwd':str(PREP),
          'runtime_files':{str(executable):sha(executable)},
          'external_files':{str(path):sha(path) for path in sorted(external)},
          'argv':[str(executable),'-I','-B','-S',str(entry)]+extra}
    with output.open('x') as f:json.dump(spec,f,indent=2,sort_keys=True);f.write('\n')
    print(json.dumps({'spec':str(output),'sha256':sha(output),'status':'PREPARED_NOT_EXECUTED'}))

if __name__=='__main__':main()
