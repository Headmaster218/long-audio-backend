#!/usr/bin/env python3
"""Fixed phase launcher. Calling this is execution and needs separate review."""
import time
LAUNCH_START=time.monotonic()
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('portable_bootstrap',HERE/'bootstrap.py')
b=importlib.util.module_from_spec(s);s.loader.exec_module(b)

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--config-sha256',required=True)
    p.add_argument('--phase',choices=tuple(b.PHASES),required=True);args=p.parse_args()
    launch=b.launch_binding(LAUNCH_START,args.phase);work_end=launch['absolute_end_monotonic']-60
    b.verify_packet();c=b.load_config(args.config,args.config_sha256)
    b.admission(c,work_end,prospective=args.phase=='download')
    source,total=b.source_inventory(c['roots']['product'],work_end)
    b.require(source==b.assets()['source']['files'] and total==4651663,'source_inventory_mismatch')
    evidence=Path(c['roots']['work'])/'evidence'
    claim=evidence/(args.phase+'.claim')
    b.write_new(claim,{'schema':'portable-single-dispatch-v1','phase':args.phase,'attempts':1},4096)
    spec=b.make_spec(c,args.config,args.config_sha256,args.phase,launch)
    path=evidence/(args.phase+'.spec.json');pin=b.write_new(path,spec)
    argv=[c['host']['python'],'-I','-B','-S',str(HERE/'acquisition_guard.py'),'--spec',str(path),'--spec-sha256',pin['sha256']]
    print(json.dumps({'status':'SAME_PID_GUARD_EXEC','phase':args.phase,'spec':str(path),'spec_sha256':pin['sha256']}),flush=True)
    b.validate_launch(launch,args.phase)
    os.execve(c['host']['python'],argv,dict(os.environ))
    raise b.Refused('exec_returned')

if __name__=='__main__':
    try:main()
    except Exception as error:print(json.dumps({'status':'REFUSED_BEFORE_DISPATCH','error_type':type(error).__name__}),file=sys.stderr);raise SystemExit(2)
