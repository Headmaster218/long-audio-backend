#!/usr/bin/env python3
"""Same reviewed download guard with existing platform routing/trust only."""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
GUARD_SHA='3934eb162fc61ba06f4c9e740016e0da04f7eba62e41d5d5e77bbc399971b305'

def load_guard():
    path=HERE/'acquisition_guard.py'
    if path.is_symlink() or path.stat().st_size>131072 or hashlib.sha256(path.read_bytes()).hexdigest()!=GUARD_SHA:
        raise RuntimeError('guard changed')
    spec=importlib.util.spec_from_file_location('network_launch_guard',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def environment(current, guard):
    clean={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
           'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1'}
    clean.update(guard.platform_network(current,'model_runtime'))
    return clean

def main():
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--spec-sha256',required=True)
    a=p.parse_args();guard=load_guard()
    # Values are supplied solely in this process environment, never argv or receipts.
    env=environment(os.environ,guard)
    os.execve(sys.executable,[sys.executable,'-I','-B','-S',str(HERE/'acquisition_guard.py'),
        '--spec',a.spec,'--spec-sha256',a.spec_sha256],env)

if __name__=='__main__':main()
