"""Closed2400-second stage adapter; all qualified ownership helpers inherited."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
PREP=HERE.parents[1]
GUARD_SHA='3934eb162fc61ba06f4c9e740016e0da04f7eba62e41d5d5e77bbc399971b305'
OLD_STAGES={'synthetic':300,'model_runtime':600,'corpus':1800,'wheels_env':600}
STAGE='model_runtime_continuation'

def load_guard():
    path=PREP/'guard/acquisition_guard.py'
    if path.is_symlink() or path.stat().st_size>131072 or hashlib.sha256(path.read_bytes()).hexdigest()!=GUARD_SHA:
        raise RuntimeError('unchanged acquisition guard required')
    s=importlib.util.spec_from_file_location('continuation_base_guard',path)
    g=importlib.util.module_from_spec(s);s.loader.exec_module(g)
    return g

def extend(g):
    g.require(g.STAGES==OLD_STAGES,'prior_stage_map_changed')
    g.STAGES={**OLD_STAGES,STAGE:2400}

def validate_spec(g,spec):
    g.require(type(spec) is dict and spec.get('stage')==STAGE,'only_closed_continuation_stage')
    g.require(type(spec.get('absolute_seconds')) is int and spec['absolute_seconds']==2400,'closed_absolute_budget')
    g.require(type(spec.get('work_seconds')) is int and spec['work_seconds']==2340,'closed_work_budget')
    g.require(spec.get('guard_sha256')==GUARD_SHA,'base_guard_pin')
    g.require(spec.get('argv',[None]*5)[4]==str(HERE/'continue_models.py'),'closed_entrypoint')
    g.require(spec.get('external_files',{}).get(str(HERE/'launch.py'))==hashlib.sha256((HERE/'launch.py').read_bytes()).hexdigest(),'adapter_pin')

def main():
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True,type=Path);p.add_argument('--spec-sha256',required=True);args=p.parse_args()
    g=load_guard();g.require(args.spec.resolve().is_relative_to(PREP),'spec_scope')
    raw=g.read_regular(args.spec,g.MAX_SPEC_BYTES,g.ABSOLUTE_END)
    g.require(hashlib.sha256(raw).hexdigest()==args.spec_sha256,'spec_pin')
    spec=json.loads(raw);extend(g);validate_spec(g,spec)
    network=g.platform_network(os.environ,STAGE)
    clean={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1',**network}
    os.environ.clear();os.environ.update(clean)
    class ContinuationGuard(g.Guard):
        def __init__(self,config,sha):
            super().__init__(config,sha)
            self.receipt['closed_continuation_adapter']={'path':str(HERE/'launch.py'),'sha256':config['external_files'][str(HERE/'launch.py')], 'base_guard_sha256':GUARD_SHA,'spec_sha256':sha,'original_stage_map':OLD_STAGES,'added_stage':{STAGE:2400},'ownership_helpers':'unchanged inherited functions'}
    g.Guard=ContinuationGuard
    return g.main()

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print(json.dumps({'status':'REFUSED_BEFORE_DISPATCH','failure':'closed continuation adapter rejected'}),file=sys.stderr)
        raise SystemExit(2) from None
