#!/usr/bin/env python3
"""Cache-preserving engineering continuation of sealed residual development v2.

Imports the unchanged scientific functions. No completed case, timing smoke or
original parity inference is repeated. Requires separate exact-plan approval.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.benchmarks import events_residual_development_v2 as frozen

SELF=Path(__file__).resolve()
TEST=ROOT/'tests/test_events_residual_resume.py'
PLAN=ROOT/'docs/research/EVENT_RESIDUAL_V2_CONTINUATION_1.json'
OLD=ROOT/'reports/event-residual-development-v2'
OUT=ROOT/'reports/event-residual-development-v2-continuation-1'
EXTRA_BUDGET_SECONDS=600.
SCIENTIFIC_SHA='a935101d33ea9ea05af0b454a193b4304c303b648dd99fea954002b46088f408'
PROTOCOL_SHA='3c12a11caf5431d61e3dc70e4a22a3c39e81c6d41af35751cf465cfcea9173ac'


def tail_read(path,maximum=131072):
    with path.open('rb') as f:
        f.seek(max(0,path.stat().st_size-maximum))
        return f.read(maximum)


def metrics_snapshot(path):
    """The final row itself must be complete; never borrow an older complete row."""
    before=path.stat()
    data=tail_read(path)
    after=path.stat()
    identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
    if identity(before)!=identity(after):raise RuntimeError('Owner metrics changed during read')
    if not data.endswith(b'\n'):raise RuntimeError('Owner metrics final row is incomplete')
    lines=data.splitlines()
    if not lines:raise RuntimeError('Owner metrics has no final row')
    row=json.loads(lines[-1])
    for key in ['rss_bytes','host_available_memory_bytes','disk_free_bytes','processes','wall_seconds']:
        if key not in row:raise RuntimeError('Owner metrics missing '+key)
    return row,after.st_mtime,dict(device=after.st_dev,inode=after.st_ino,size=after.st_size,mtime_ns=after.st_mtime_ns)


def owner_snapshot(owner_dir,*,now=time.time,monotonic=time.monotonic,sleep=time.sleep,retry_seconds=1.):
    """Owner-confirmed two-phase progress schema; optional wall is not a clock.

    Both progress.utc and a stable, newline-complete metrics file mtime must be
    fresh. Bounded rereads handle atomic progress replacement/append races. No
    old row behind an incomplete tail is accepted, even when progress is fresh.
    """
    start=monotonic();last_error=None
    while True:
        try:
            row,mtime,identity=metrics_snapshot(owner_dir/'metrics.jsonl')
            progress=json.loads((owner_dir/'progress.json').read_text())
            when=dt.datetime.fromisoformat(progress['utc']).timestamp()
            ages=[now()-mtime,now()-when]
            if not all(math.isfinite(age) and 0<=age<=10 for age in ages):
                raise RuntimeError('Owner telemetry stale or future-dated')
            return row,progress,max(ages),identity
        except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:
            last_error=exc
        elapsed=monotonic()-start
        if elapsed>=retry_seconds:
            raise RuntimeError('Owner telemetry unavailable after bounded reread: '+str(last_error))
        sleep(min(.05,retry_seconds-elapsed))


def resume_scope(rows,completed,requested):
    ids=[r['id']for r in rows]
    if len(set(ids))!=len(ids) or not set(completed)<=set(ids):raise RuntimeError('Invalid completed IDs')
    remaining=[id for id in ids if id not in completed]
    if requested!=remaining:raise RuntimeError('Continuation must contain exactly uncached cases in frozen order')
    return remaining


class Guard(frozen.Guard):
    """Same limits and own-only child management; corrected read-only telemetry."""
    def __init__(self,private,out,owner_dir,prior_active_seconds):
        self.prior_active_seconds=prior_active_seconds
        self.last_owner_wall=None
        super().__init__(private,out,owner_dir)

    def sample(self,stage):
        owner,progress,age,snapshot=owner_snapshot(self.owner_dir)
        identities=sorted((int(p['pid']),float(p['created']))for p in owner['processes'])
        if self.owner_identity is None:self.owner_identity=identities[:3]
        if not all(item in identities for item in self.owner_identity):raise RuntimeError('Owner process identity changed')
        wall=float(owner['wall_seconds'])
        if self.last_owner_wall is not None and wall<self.last_owner_wall:raise RuntimeError('Owner metrics time regressed')
        self.last_owner_wall=wall
        if self.me.create_time()!=self.identity:raise RuntimeError('Own PID identity changed')
        own=0;processes=[]
        for p in [self.me]+self.me.children(recursive=True):
            try:
                row=dict(pid=p.pid,created=p.create_time(),rss_bytes=p.memory_info().rss,
                         threads=p.num_threads(),affinity=p.cpu_affinity())
            except (self.psutil.NoSuchProcess,self.psutil.ZombieProcess):continue
            if row['affinity']!=[8]:raise RuntimeError('Trial-owned process escaped CPU 8')
            own+=row['rss_bytes'];processes.append(row)
        available=min(self.psutil.virtual_memory().available,int(owner['host_available_memory_bytes']))
        free=min(shutil.disk_usage(self.private).free,int(owner['disk_free_bytes']))
        report_bytes=sum(frozen.size_tree(p)for p in [self.out,OLD,ROOT/'reports/event-residual-development-v1'])
        trial_bytes=(frozen.size_tree(self.private)+report_bytes+
                     sum(p.stat().st_size for p in [SELF,TEST,frozen.SELF,frozen.TEST,PLAN]))
        elapsed=time.monotonic()-self.started
        failures=frozen.check_limits(own,int(owner['rss_bytes']),available,free,trial_bytes,report_bytes,age,elapsed)
        if elapsed>EXTRA_BUDGET_SECONDS:failures.append('Continuation active execution exceeds 600 seconds')
        row=dict(utc=frozen.utc(),stage=stage,own_rss_bytes=own,owner_rss_bytes=int(owner['rss_bytes']),
                 aggregate_rss_bytes=own+int(owner['rss_bytes']),owner_age_seconds=age,
                 owner_metrics_snapshot=snapshot,owner_progress_utc=progress['utc'],owner_identities=identities,
                 own_processes=processes,host_available_bytes=available,disk_free_bytes=free,
                 trial_disk_bytes=trial_bytes,persistent_results_bytes=report_bytes,
                 continuation_active_seconds=elapsed,v2_cumulative_active_seconds=self.prior_active_seconds+elapsed,
                 failures=failures)
        with(self.out/'resources.jsonl').open('a')as f:f.write(json.dumps(row)+'\n')
        self.peak_own=max(self.peak_own,own);self.peak_aggregate=max(self.peak_aggregate,own+int(owner['rss_bytes']))
        self.min_available=min(self.min_available,available);self.min_free=min(self.min_free,free)
        if failures:raise RuntimeError('resource_guard_failed: '+'; '.join(failures))
        return row

    def summary(self):
        summary=super().summary()
        summary.update(prior_v2_active_seconds=self.prior_active_seconds,
                       v2_cumulative_active_seconds=self.prior_active_seconds+summary['active_seconds'],
                       continuation_budget_seconds=EXTRA_BUDGET_SECONDS)
        return summary


def verify_bound_files(plan):
    for relative,expected in plan['bound_files'].items():
        path=ROOT/relative
        if frozen.digest(path)!=expected:raise RuntimeError('Bound evidence changed: '+relative)
    if frozen.digest(frozen.SELF)!=SCIENTIFIC_SHA or frozen.digest(frozen.PROTOCOL)!=PROTOCOL_SHA:
        raise RuntimeError('Frozen scientific source/protocol changed')


def combined_cases(plan,new_cases):
    cases={}
    for row in plan['completed_cases']:
        value=json.loads((ROOT/row['path']).read_text())
        if value['id']!=row['id']or frozen.digest(ROOT/row['path'])!=row['sha256']:
            raise RuntimeError('Completed case cache changed')
        cases[row['id']]=value
    for id,value in new_cases.items():
        if id in cases:raise RuntimeError('Attempt to replace completed case')
        cases[id]=value
    return cases


def reproduce_cached(cases):
    from audio_backend.events.detector import postprocess
    checked=0
    for id,case in cases.items():
        for arm in ['M','R','Q0','C']:
            expected=sorted({e['label']for e in postprocess(case[arm]['details'])})
            if expected!=case[arm]['labels']:raise RuntimeError('Cached decision mismatch: '+id+':'+arm)
            checked+=1
    return checked


def run(args):
    import numpy as np
    os.sched_setaffinity(0,{8});os.environ.update(frozen.THREAD_ENV)
    if frozen.digest(PLAN)!=args.approved_plan_sha256:raise RuntimeError('Exact approved continuation plan hash required')
    plan=json.loads(PLAN.read_text());verify_bound_files(plan)
    if plan['continuation_active_budget_seconds']!=600 or plan['quality_promotion_allowed']:
        raise RuntimeError('Unexpected continuation scope')
    protocol=json.loads(frozen.PROTOCOL.read_text());rows=protocol['inputs']['rows']
    old=combined_cases(plan,{})
    remaining=resume_scope(rows,old,plan['remaining_ids'])
    if len(old)!=111 or len(remaining)!=27:raise RuntimeError('Wrong continuation denominators')
    private=Path(args.private).resolve();assets=private/'assets';scratch=private/'scratch'
    if OUT.exists():raise RuntimeError('Continuation output already exists; no silent retry')
    OUT.mkdir();scratch.mkdir(exist_ok=False);guard=None;new={}
    try:
        guard=Guard(private,OUT,ROOT/'reports/endurance_v03/moss_cpp_q8_native_overlap_v03',plan['prior_v2_active_seconds'])
        frozen.preflight(assets,protocol)
        from scripts.benchmarks.endurance_runtime import verify
        verify(json.loads((OLD/'runtime-files.json').read_text()))
        if frozen.digest(shutil.which('ffmpeg'))!=plan['ffmpeg_sha256']:raise RuntimeError('FFmpeg changed')
        if reproduce_cached(old)!=444:raise RuntimeError('Incomplete prior cache')
        if len(frozen.historical_parity(rows))!=138:raise RuntimeError('Historical parity cache changed')
        geom=frozen.geometry(assets/'DeepFilterNet3_onnx.tar.gz',assets)
        for path in [SELF,TEST]:
            relative=str(path.relative_to(ROOT))
            if subprocess.run(['git','diff','--quiet','HEAD','--',relative],cwd=ROOT).returncode:
                raise RuntimeError('Continuation source differs from committed source')
            subprocess.run(['git','ls-files','--error-unmatch',relative],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
        frozen.dump(OUT/'execution-freeze.json',dict(utc=frozen.utc(),resumed_from=plan['resumed_from'],
            continuation_plan_sha256=frozen.digest(PLAN),scientific_protocol_sha256=PROTOCOL_SHA,
            scientific_helper_sha256=SCIENTIFIC_SHA,orchestration_helper_sha256=frozen.digest(SELF),
            orchestration_test_sha256=frozen.digest(TEST),
            git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            prior_v2_active_seconds=plan['prior_v2_active_seconds'],continuation_budget_seconds=600,
            original_budget_unused_seconds=plan['original_budget_unused_seconds'],
            remaining_ids=remaining,completed_cache_count=len(old),
            timing_reused_sha256=frozen.digest(OLD/'timing-passed.json'),
            parity_reused_sha256=frozen.digest(OLD/'original-parity.json'),
            scope='Only the 27 uncached candidate cases; no timing/parity/completed-case inference replay.',
            args=sys.argv,geometry=geom,quality_promotion_allowed=False))
        for index,row in enumerate(rows):
            if row['id']not in remaining:continue
            a=frozen.resample(frozen.samples_for(row),16000,48000,guard,'fixture:'+row['id']+':upsample')
            raw=frozen.native(a,assets,geom,guard,'fixture:'+row['id']+':native')
            branches,info=frozen.support(a,raw,geom)
            transformed={key:frozen.resample(value,48000,16000,guard,'fixture:'+row['id']+':'+key+':downsample')for key,value in branches.items()}
            if len({len(v)for v in transformed.values()})!=1:raise RuntimeError('Branch support lengths differ')
            case=dict(support=info,historical_baseline_labels=row['baseline_labels'],
                context_interpretation='actual_more_than_5s_observed_support'if info['valid_end_seconds']>5 else'at_most_5s_observed_support_padding_changes_pooling_only')
            for arm in ['M','R','Q0','C']:
                data=transformed['M'if arm=='C'else arm]
                case[arm]=frozen.score(data,guard,'fixture:'+row['id']+':score:'+arm,10 if arm=='C'else 5)
                case[arm]['waveform_sha256']=hashlib.sha256(data.tobytes()).hexdigest()
                # Durable per-branch evidence is new orchestration only. Scientific
                # outputs are exactly those returned by the unchanged v2 helper.
                frozen.dump(OUT/f'partial-{index:03d}-{arm}.json',dict(id=row['id'],arm=arm,
                    support=info,context_interpretation=case['context_interpretation'],result=case[arm]))
            new[row['id']]=dict(id=row['id'],**case)
            frozen.dump(OUT/f'case-{index:03d}.json',new[row['id']])
            print('RESUMED',len(new),len(remaining),row['id'],flush=True)
        cases=combined_cases(plan,new)
        decisions=frozen.evaluate(rows,cases)
        frozen.dump(OUT/'decisions.json',decisions)
        if reproduce_cached(cases)!=552:raise RuntimeError('Full cached decision reproduction failed')
        disk_new={r['id']:r for p in OUT.glob('case-*.json')for r in [json.loads(p.read_text())]}
        if frozen.evaluate(rows,combined_cases(plan,disk_new))!=decisions:raise RuntimeError('Combined decision replay mismatch')
        from audio_backend.events.metrics import presence_metrics,group_presence
        metrics={};sources=[r for r in rows if r['kind']=='source']
        for arm in ['baseline','M','R','C','Q0']:
            pred={r['id']:set(r['baseline_labels']if arm=='baseline'else cases[r['id']][arm]['labels'])for r in rows}
            metrics[arm]=dict(annotation_aware=presence_metrics(rows,pred,sorted(frozen.TARGETS)),
                source_group_annotation_aware=group_presence(sources,pred,sorted(frozen.TARGETS)),
                source_group_closed_set_weak_label_proxy=group_presence(sources,pred,sorted(frozen.TARGETS),True),
                independent_source_groups=len({r['source_group']for r in sources}),
                confidence_interval_warning='SNR repetitions are correlated; mixture intervals are descriptive, not independent-recording confidence.')
        frozen.dump(OUT/'metrics.json',metrics)
        verify_bound_files(plan);frozen.preflight(assets,protocol)
        frozen.dump(OUT/'summary.json',dict(status='completed_fixed_v2_with_engineering_continuation',
            original_complete_cases=111,new_complete_cases=len(new),combined_cases=len(cases),
            arms=decisions,quality_promoted=False,prior_failed_gates_remain_binding=True,
            resources=guard.summary(),v1_plus_nonneural_diagnostic_active_seconds=plan['prior_v1_and_diagnostic_active_seconds'],
            all_experiment_active_seconds=plan['prior_v1_and_diagnostic_active_seconds']+guard.summary()['v2_cumulative_active_seconds'],
            original_budget_unused_seconds=plan['original_budget_unused_seconds'],
            preserved_original_hashes=True,cached_branch_decisions_reproduced=552,
            correlated_input_media_seconds=1265,original_source_media_seconds=330,
            no_heldout_inference=True,no_production_changes=True))
    except BaseException as exc:
        frozen.dump(OUT/'failure.json',dict(utc=frozen.utc(),type=type(exc).__name__,error=str(exc),
            remaining_ids=[id for id in remaining if id not in new],new_completed_ids=list(new),
            resources=guard.summary()if guard else None,quality_promoted=False))
        raise
    finally:
        if guard:
            frozen.dump(OUT/'stage-costs.json',guard.stages)
            frozen.dump(OUT/'resource-summary.json',guard.summary())
            audit=frozen.privacy_audit(OUT);frozen.dump(OUT/'privacy-audit.json',audit)
            if not audit['passed']:raise RuntimeError('Continuation report privacy audit failed')
        if scratch.exists():shutil.rmtree(scratch)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private',required=True)
    parser.add_argument('--approved-plan-sha256',required=True)
    run(parser.parse_args())


if __name__=='__main__':main()
