#!/usr/bin/env python3
"""Named, bounded synthetic archive-selection cases."""
import time
START = time.monotonic()
import signal
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import resource
import tarfile
import bz2
import zlib

HERE = Path(__file__).resolve().parent
OUT = Path('/GENERATED_WORKSPACE/GENERATED_WORK_ROOT/evidence/GENERATED_SELECTED_EVIDENCE')
CASES = ('pinned_bz2','discovered_gzip','duplicate_member','alias_substitution','wrong_digest',
         'unsafe_name','truncated_gzip','nonzero_suffix','existing_output','native_alias_cycle',
         'native_alias_escape','flatten_verified_regular')


def main():
    start = START
    def wall_alarm(signum,frame):
        raise RuntimeError("focused 60-second wall deadline")
    signal.signal(signal.SIGALRM,wall_alarm)
    signal.setitimer(signal.ITIMER_REAL,max(0.001,START+60-time.monotonic()))
    need = lambda value,label: value or (_ for _ in ()).throw(RuntimeError(label))
    need(os.sched_getaffinity(0) == {3} and os.getpriority(os.PRIO_PROCESS,0) == 19,'fixture owner CPU/nice')
    need(resource.getrlimit(resource.RLIMIT_AS) == (1024**3,1024**3),'fixture owner AS')
    spec = importlib.util.spec_from_file_location('fixed_selected_fixture',HERE/'selected.py')
    s = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s)
    s.END = start+55
    data = b'synthetic selected payload\n'
    sha = hashlib.sha256(data).hexdigest()
    root = OUT/'focused-cases'
    root.mkdir(mode=0o700)
    s.ASSETS = root
    results = []

    def tar_bytes(rows):
        memory = io.BytesIO()
        with tarfile.open(fileobj=memory,mode='w',format=tarfile.USTAR_FORMAT) as archive:
            for name,payload,kind,target in rows:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = target
                info.size = len(payload)
                archive.addfile(info,io.BytesIO(payload))
        need(len(memory.getvalue()) <= 20480,'synthetic tar cap')
        return memory.getvalue()

    def scan_case(label,rows,compression='bz2',expected=sha,fail=False,suffix=b'',truncate=False):
        raw = tar_bytes(rows)+suffix
        compressed = bz2.compress(raw) if compression == 'bz2' else zlib.compress(raw,wbits=31)
        if truncate:
            compressed = compressed[:-5]
        need(len(compressed) <= 2048,'synthetic compressed cap')
        archive_path = root/(label+'.input')
        archive_path.write_bytes(compressed)
        cfg = dict(archive=archive_path.name,compression=compression,decoded_cap=65536,
                   acquired=dict(bytes=len(compressed),descriptor_after=s.path_descriptor(archive_path)),
                   selected={'safe.bin':(label+'.output',len(data),expected)})
        failed = False
        try:
            output,scan = s.materialize_archive(cfg,root)
            need(output['safe.bin']['sha256'] == sha and scan['fresh_compressed_hash'] is False,'selected observation')
        except Exception:
            failed = True
        need(failed == fail,label+' expectation')
        results.append(dict(case=label,status='PASS'))

    row = [('safe.bin',data,tarfile.REGTYPE,'')]
    scan_case('pinned_bz2',row)
    scan_case('discovered_gzip',row,'gzip',None)
    scan_case('duplicate_member',row+row,fail=True)
    scan_case('alias_substitution',[('safe.bin',b'',tarfile.SYMTYPE,'other.bin')],fail=True)
    scan_case('wrong_digest',row,expected='0'*64,fail=True)
    scan_case('unsafe_name',[('../safe.bin',data,tarfile.REGTYPE,'')],fail=True)
    scan_case('truncated_gzip',row,'gzip',fail=True,truncate=True)
    scan_case('nonzero_suffix',row,fail=True,suffix=b'x')
    s.write_stream(root,'existing.output',io.BytesIO(data),len(data),sha)
    refused = False
    try:
        s.write_stream(root,'existing.output',io.BytesIO(data),len(data),sha)
    except FileExistsError:
        refused = True
    need(refused,'existing output exclusive')
    results.append(dict(case='existing_output',status='PASS'))
    headers_path = s.EVIDENCE/'model-materialization-inputs/NATIVE_HEADERS.json'
    metadata = s.fixed_json(headers_path)
    s.native_inventory(metadata)
    for label,target in [('native_alias_cycle',None),('native_alias_escape','../../../../outside')]:
        mutated = json.loads(json.dumps(metadata))
        alias = next(r for r in mutated['members'] if r['kind']=='symlink')
        alias['target'] = Path(alias['path']).name if target is None else target
        alias['resolved'] = alias['path'] if target is None else 'outside'
        refused = False
        try:
            s.native_inventory(mutated)
        except Exception:
            refused = True
        need(refused,label)
        results.append(dict(case=label,status='PASS'))
    source = root/'pinned_bz2.output'
    before = s.path_descriptor(source)
    with source.open('rb') as stream:
        got = s.write_stream(root,'flatten.output',stream,len(data),sha)
    need(got['sha256'] == sha and s.path_descriptor(source) == before,'flatten verified regular')
    results.append(dict(case='flatten_verified_regular',status='PASS'))
    need(tuple(r['case'] for r in results) == CASES,'exact focused cases')
    leaves = list(root.iterdir())
    need(len(leaves) <= 18 and all(p.is_file() and not p.is_symlink() for p in leaves),'focused leaf inventory')
    inclusive = root.stat().st_blocks*512+sum(max(p.stat().st_size,p.stat().st_blocks*512) for p in leaves)
    need(inclusive <= 81920 and time.monotonic()-start < 55,'focused evidence/time cap')
    result = dict(schema='fixed-selected-focused-v1',status='PASS',cases=results,
                  synthetic_only=True,real_archive_payload_reads=0,model_loads=0,model_calls=0,
                  evidence_inclusive_bytes=inclusive,elapsed_seconds=time.monotonic()-start)
    s.json_new(OUT/'focused.result.json',result,4096)
    print(json.dumps(result,sort_keys=True),flush=True)


if __name__ == '__main__':
    main()
