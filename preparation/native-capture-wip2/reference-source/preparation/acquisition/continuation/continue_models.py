#!/usr/bin/env python3
"""One explicit whole-artifact continuation, retaining every previous attempt."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import time

HERE=Path(__file__).absolute().parent
PREP=HERE.parent.parent
WORKSPACE=PREP.parent
ACQUIRE_PIN={'bytes': 53119, 'sha256': 'a79053fae502ed0621c94aa94aafc634fed7997a423d210fb806a2587ea391c1'}
MANIFEST_SHA='b189c4da6a8a28d1ca27ebba75c65a118d9b14cb1496f0e4b641f0385c79417b'
TERMINAL_SHA='6a981b020917fde51ddde964d0616288a7463c5ff3e106a1f29c1669a1fe683f'
TERMINAL_NAME='MODEL_RUNTIME_PLATFORM_TERMINAL.json'
REUSED_ID='eres2net-base-zh-cn'
REUSED_BYTES=39593761
OLD_PART_BYTES=96041420
OLD_PART_SHA='58ba4e1e41ac33b250a22db4196741ee48e3e20e4110debaeac1b12db5ff7899'
REMAINING_IDS=('zipformer-small-audioset','sensevoice-small-int8','sensevoice-model-license',
               'nemotron3-q8','linux-x86_64-cpu')
NEW_BYTES=387737866
GEOMETRY=7029617290
CONTINUATION_GEOMETRY=7125658710
CAP=7516192768
HEADROOM=390534058


def bootstrap_check():
    # No fallback clock and no extension of the incoming work deadline.
    if 'GUARD_WORK_END' not in os.environ or time.monotonic()>=float(os.environ['GUARD_WORK_END']):
        raise RuntimeError('guard work boundary')
    stop=os.environ.get('GUARD_STOP_PATH')
    if not stop or Path(stop).exists() or Path(stop).is_symlink():
        raise RuntimeError('guard STOP boundary')


def identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def load_acquire():
    """Hash the exact unchanged helper before importing it; stdlib only."""
    path=HERE.parent/'acquire.py';bootstrap_check()
    before=path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size!=ACQUIRE_PIN['bytes']:
        raise RuntimeError('acquisition helper size/type')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        if identity(before)!=identity(os.fstat(fd)):raise RuntimeError('acquisition helper open race')
        total=0;digest=hashlib.sha256()
        while True:
            bootstrap_check();block=os.read(fd,65536)
            if not block:break
            total+=len(block)
            if total>ACQUIRE_PIN['bytes']:raise RuntimeError('acquisition helper grew')
            digest.update(block)
        if identity(before)!=identity(os.fstat(fd)) or identity(before)!=identity(path.lstat()):
            raise RuntimeError('acquisition helper changed')
        if total!=ACQUIRE_PIN['bytes'] or digest.hexdigest()!=ACQUIRE_PIN['sha256']:
            raise RuntimeError('acquisition helper hash')
    finally:os.close(fd)
    module_spec=importlib.util.spec_from_file_location('unchanged_native_acquire',path)
    module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
    return module


def prior_pins(acquire,terminal,workspace,ctx):
    """All nine files, including the entire old part, are streamed and rehashed."""
    acquire.require(len(terminal['pins'])==9,'exact nine original terminal pins')
    seen=set();results=[]
    for pin in terminal['pins']:
        ctx.check()
        name=pin['path']
        acquire.require(type(name) is str and 0<len(name.encode('utf-8'))<=acquire.MAX_PATH and
            all(32<=ord(ch)<127 for ch in name) and '\\' not in name and ':' not in name and
            not name.startswith('/') and all(piece not in ('','.','..') for piece in name.split('/')),
            'unsafe prior evidence path')
        # Unlike new archive outputs, a sealed prior .part is required evidence.
        acquire.require(name not in seen,'duplicate prior pin');seen.add(name)
        path=ctx.checked(Path(workspace)/name)
        results.append({'path':name}|acquire.hash_file(path,ctx,pin))
    return results


def validate_terminal(acquire,terminal,manifest):
    acquire.require(terminal['schema']=='model-runtime-incomplete-terminal-v1' and
                    terminal['status']=='INCOMPLETE_DEADLINE','terminal status changed')
    wait=terminal['wait']
    acquire.require(wait['tool_session_id']==42248 and wait['original_handle_waited'] is True and
        wait['cleanup']=='CONFIRMED' and wait['unresolved_owned']==[] and wait['model_calls']==0 and
        wait['verified_complete_artifacts']==1 and wait['complete_artifact_bytes']==REUSED_BYTES and
        wait['incomplete_part_bytes']==OLD_PART_BYTES and wait['partial_has_no_successful_final_hash'] is True,
        'original attempt is not closed exact incomplete attempt')
    acquire.require(len(terminal['pins'])==9,'exact original pin count')
    reused=next(x for x in manifest['artifacts'] if x['id']==REUSED_ID)
    suffix=PREP.name+'/'+reused['destination']
    speaker=[x for x in terminal['pins'] if x['path']==suffix]
    acquire.require(len(speaker)==1 and speaker[0]['bytes']==reused['bytes']==REUSED_BYTES and
                    speaker[0]['sha256']==reused['sha256'],'reused speaker terminal identity')
    parts=[x for x in terminal['pins'] if x['path'].endswith('.part')]
    acquire.require(len(parts)==1 and parts[0]['bytes']==OLD_PART_BYTES and
                    parts[0]['sha256']==OLD_PART_SHA,'old event part terminal identity')
    acquire.require(GEOMETRY+OLD_PART_BYTES==CONTINUATION_GEOMETRY and
                    CONTINUATION_GEOMETRY<CAP and CAP-CONTINUATION_GEOMETRY==HEADROOM,
                    'continuation retained disk geometry')


def exact_filtered_manifest(acquire,manifest):
    acquire.require(len(manifest['artifacts'])==35,'original full artifact count')
    models=[x for x in manifest['artifacts'] if x['stage']=='models']
    acquire.require(tuple(x['id'] for x in models)==(REUSED_ID,)+REMAINING_IDS,'original model artifact order')
    acquire.require(models[0]['bytes']==REUSED_BYTES and sum(x['bytes'] for x in models[1:])==NEW_BYTES,
                    'original model byte totals')
    result=copy.deepcopy(manifest)
    result['artifacts']=copy.deepcopy(models[1:])
    acquire.require(tuple(x['id'] for x in result['artifacts'])==REMAINING_IDS,'remaining whole-artifact filter')
    acquire.require(result['mandatory_runtime_files']==manifest['mandatory_runtime_files'],
                    'full final model verification must include reused speaker')
    return result


def missing_destinations(acquire,filtered,ctx):
    """Reject existing finals, parts, model trees and success manifests up front."""
    targets=[]
    for artifact in filtered['artifacts']:
        target=ctx.checked(ctx.root/artifact['destination'])
        targets.extend((target,ctx.part(target)))
        spec=filtered['archives'].get(artifact['id'])
        if spec:
            tree=ctx.root/spec['output']
            if spec.get('root') and not spec.get('strip_root'):tree=tree/spec['root']
            targets.append(ctx.checked(tree))
            targets.append(ctx.checked(ctx.root/'receipts'/('extract-'+target.name+'.json')))
    targets.extend((ctx.root/'receipts/models-verified.json',ctx.root/'receipts/stage-model_runtime.json',
                    ctx.root/'licenses/native-diarization/OpenMDW-1.1.txt'))
    for target in targets:
        ctx.check();ctx.checked(target)
        acquire.require(not target.exists() and not target.is_symlink(),'new continuation destination collision')
    return [str(x.relative_to(ctx.root)) for x in targets]


class CleanupChecks:
    """Read-only terminal integrity check within the same absolute deadline."""
    def __init__(self,ctx):self.ctx=ctx
    def check(self):self.ctx.check(cleanup=True)
    def checked(self,path):return self.ctx.checked(path)


def perform(acquire,ctx,manifest,terminal,workspace,runner=None):
    """Caller must first compare the full manifest and exact sealed terminal."""
    receipt_path=ctx.run_directory/'continuation-result.json'
    acquire.require(not receipt_path.exists() and not receipt_path.is_symlink(),'continuation receipt already exists')
    report={'schema':'model-runtime-whole-artifact-continuation-v1','status':'RUNNING',
        'phase_scope':'five remaining model/runtime/license artifacts from byte zero; full final model verification',
        'reused_artifact_id':REUSED_ID,'reused_expected_bytes':REUSED_BYTES,
        'new_expected_bytes':NEW_BYTES,'old_incomplete_part_retained_bytes':OLD_PART_BYTES,
        'old_part_used_for_network_transfer':False,'range_requests':False,'automatic_retries':0,
        'original_geometry_bytes':GEOMETRY,'conservative_with_old_part_bytes':CONTINUATION_GEOMETRY,
        'retained_cap_bytes':CAP,'conservative_headroom_bytes':HEADROOM,
        'work_end':ctx.deadline-acquire.CLEANUP,'absolute_end':ctx.deadline,'model_calls':0,
        'prior_before_verified':False,'prior_after_verified':False,'new_verified_bytes':None}
    error=None
    try:
        report['prior_before']=prior_pins(acquire,terminal,workspace,ctx)
        report['prior_before_verified']=True;report['reused_verified_bytes']=REUSED_BYTES
        filtered=exact_filtered_manifest(acquire,manifest)
        report['remaining_artifact_ids']=[x['id'] for x in filtered['artifacts']]
        report['fresh_destinations']=missing_destinations(acquire,filtered,ctx)
        ctx.check()
        (runner if runner is not None else acquire.run_stage)('model_runtime',filtered,ctx)
        ctx.check()
        report['new_verified_bytes']=NEW_BYTES
        report['status']='VERIFIED'
    except BaseException as exc:
        error=exc;report.update(status='FAILED',failure=acquire.failure_summary(exc))
    finally:
        # No more downloads or extraction after any failure. Only prove old evidence unchanged.
        try:
            report['prior_after']=prior_pins(acquire,terminal,workspace,CleanupChecks(ctx))
            report['prior_after_verified']=True
        except BaseException as exc:
            report.update(status='FAILED',prior_after_failure=acquire.failure_summary(exc))
            if error is None:error=exc
        report['new_network_bytes']=ctx.network_bytes
        acquire.write_json(receipt_path,report,ctx,cleanup=True)
    if error is not None:raise error
    return report


def main(acquire):
    acquire.require(sys.argv[1:]==[],'no mutable continuation arguments')
    ctx=acquire.context_from_env(str(PREP))
    manifest=acquire.small_json(HERE.parent/'ASSET_MANIFEST.json',MANIFEST_SHA)
    acquire.require(manifest==acquire.build_manifest(WORKSPACE),'original full manifest equality')
    terminal=acquire.small_json(HERE.parent/TERMINAL_NAME,TERMINAL_SHA)
    validate_terminal(acquire,terminal,manifest)
    perform(acquire,ctx,manifest,terminal,WORKSPACE)


def entry():
    try:acquire=load_acquire()
    except BaseException:
        # Trusted safe-summary helper is unavailable; never format bootstrap exception text.
        sys.stderr.write('{"status":"FAILED","failure":{"error_type":"AcquisitionBootstrapFailure"}}\n')
        return 1
    return acquire.cli_boundary(lambda:main(acquire))

if __name__=='__main__':raise SystemExit(entry())
