"""Owner-run read-only exact Guard.verify_pin; does not create or dispatch Guard."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
PREP=HERE.parent
GUARD_SHA='3934eb162fc61ba06f4c9e740016e0da04f7eba62e41d5d5e77bbc399971b305'

def main():
    if len(sys.argv)!=3 or sys.argv[1] not in ('geometry.spec.json','installed-store.spec.json'):
        raise RuntimeError('fixed proof specification required')
    exe=PREP/'environment/bin/python3.12'
    if Path(sys.executable).resolve()!=exe:raise RuntimeError('copied interpreter required')
    path=HERE/sys.argv[1];raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if digest!=sys.argv[2]:raise RuntimeError('exact spec hash required')
    guard=PREP/'guard/acquisition_guard.py'
    if hashlib.sha256(guard.read_bytes()).hexdigest()!=GUARD_SHA:raise RuntimeError('unchanged guard required')
    s=importlib.util.spec_from_file_location('finite_proof_guard_precheck',guard)
    g=importlib.util.module_from_spec(s);s.loader.exec_module(g)
    obj=g.Guard.__new__(g.Guard)
    obj.spec=json.loads(raw);obj.end=g.START+240;obj.rss_cap=g.MAX_RSS;obj.disk_cap=g.MAX_DISK;obj.receipt={}
    g.Guard.verify_pin(obj)
    print(json.dumps({'status':'PASS_NO_DISPATCH','spec_sha256':digest,
          'interpreter':str(exe),'runtime_files':obj.spec['runtime_files'],
          'guard_sha256':GUARD_SHA,'external_files':len(obj.spec['external_files']),
          'source_files':len(obj.spec['source_files']),'child_created':False,'model_imports':0},sort_keys=True))
if __name__=='__main__':main()
