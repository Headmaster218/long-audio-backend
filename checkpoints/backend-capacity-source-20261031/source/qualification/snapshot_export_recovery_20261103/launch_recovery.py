"""Explicit outer timeout/wait and evidence-inventory owner; recovery only."""
import argparse
import json
import os
from pathlib import Path
import resource
import signal
import shutil
import subprocess
import sys
import time
import psutil
sys.path.insert(0,str(Path(__file__).resolve().parent))
import snapshot_http as host


def command(python,wrapper,spec,output,spec_hash):
    return ['/usr/bin/timeout','--signal=TERM','--kill-after=15s','160s',str(python),'-I','-B',
            str(wrapper),str(spec),str(output),'--spec-sha256',spec_hash]


def check_resources(output, family, stop):
    """Outer sampled bounds remain effective if the inner wrapper stalls."""
    try:
        rss=0
        for process in family:
            try:
                if process.is_running():rss+=process.memory_info().rss
            except psutil.NoSuchProcess:pass
        host.need(rss<=512*1024**2,'launch_family_rss_bound')
        host.need(shutil.disk_usage(output).free>=11*1024**3//2,'launch_disk_floor')
        size=sum(p.stat().st_size for p in Path(output).rglob('*') if p.is_file())
        host.need(size<=256*1024**2,'launch_output_bound')
        return rss
    except BaseException:
        stop()
        raise


def run(spec_path,spec_hash,output):
    os.umask(0o077);spec_path=Path(spec_path);output=Path(output).absolute()
    host.need(spec_path.is_file() and not spec_path.is_symlink() and host.digest(spec_path)==spec_hash,'launch_spec_pin')
    spec=json.loads(spec_path.read_bytes());host_path=Path(spec['host_spec'])
    host.need(host.digest(host_path)==spec['host_spec_sha256'] and not host_path.is_symlink(),'launch_host_spec_pin')
    hs=json.loads(host_path.read_bytes());root=host.verify_source(hs['source_root'],hs['source_manifest'],hs['source_manifest_sha256'])
    here=root/'qualification/snapshot_export_recovery_20261103'
    host.need(Path(__file__).resolve()==here/'launch_recovery.py','launch_source_location')
    roots=[Path(p).resolve() for p in hs['protected_roots']];snapshot=Path(hs['snapshot']).resolve()
    host.need(root in roots and Path(hs['original_root']).resolve() in roots,'launch_protected_roots')
    host.need(output.parent==output.parent.resolve(strict=True) and not os.path.lexists(output)
              and all(not output.is_relative_to(p) for p in roots)
              and not output.is_relative_to(snapshot.parent),'launch_output_separation')
    guard=host.ProtectedPaths([*roots,snapshot]).install()
    before={str(p):host.inventory(p) for p in roots};snapshot_before=host.digest(snapshot)
    host.need(snapshot_before==hs['snapshot_sha256'],'launch_snapshot_pin')
    output.mkdir(mode=0o700)
    (output/'ORIGINAL_BEFORE.json').write_text(json.dumps(before,sort_keys=True)+'\n')
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});os.nice(19)
    started=time.monotonic();usage=resource.getrusage(resource.RUSAGE_CHILDREN);peak=0;observed={}
    child=None;report={'passed':False,'snapshot_only':True,'native_inference_executed':False}
    signals=[];saved={s:signal.signal(s,lambda signum,frame:signals.append(signum)) for s in (signal.SIGINT,signal.SIGTERM)}
    def stop_group():
        if child is not None:
            try:
                if os.getpgid(child.pid)==child.pid:os.killpg(child.pid,signal.SIGKILL)
                else:child.kill()
            except ProcessLookupError:pass
    try:
        with (output/'wrapper.stdout').open('xb') as stdout,(output/'wrapper.stderr').open('xb') as stderr:
            child=subprocess.Popen(command(sys.executable,here/'qualify_recovery.py',spec_path,output/'run',spec_hash),
                stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
            # timeout owns its process group; wrapper and all children must inherit it.
            while child.poll() is None:
                family=psutil.Process().children(recursive=True)
                for process in family:
                    try:observed[process.pid]=process.create_time()
                    except psutil.NoSuchProcess:pass
                peak=max(peak,check_resources(output,[psutil.Process(),*family],stop_group))
                if signals or time.monotonic()-started>180:
                    report['outer_stop_error']='Interrupted or timeout supervisor exceeded bound';stop_group();break
                time.sleep(.05)
            try:report['exit_code']=child.wait(timeout=5);report['original_popen_wait_completed']=True
            except subprocess.TimeoutExpired:
                stop_group();report['exit_code']=child.wait(timeout=5);report['original_popen_wait_completed']=True
        result_path=output/'run/RESULT.json'
        report['wrapper']=json.loads(result_path.read_bytes()) if result_path.is_file() else None
    except BaseException as error:
        report['error']={'type':type(error).__name__,'message':str(error)[:1024]}
    finally:
        if child is not None and child.poll() is None:stop_group()
        if child is not None and not report.get('original_popen_wait_completed'):
            try:report['exit_code']=child.wait(timeout=5);report['original_popen_wait_completed']=True
            except BaseException as error:report['wait_error']=type(error).__name__
        survivors=[]
        for pid,created in observed.items():
            try:
                process=psutil.Process(pid)
                if process.create_time()==created and process.is_running():
                    survivors.append(pid);process.kill()
                    try:process.wait(timeout=3)
                    except psutil.TimeoutExpired:report['descendant_wait_error']='Observed descendant failed to exit'
            except psutil.NoSuchProcess:pass
        report['survivors_after_timeout_wait']=survivors
        after={str(p):host.inventory(p) for p in roots}
        (output/'ORIGINAL_AFTER.json').write_text(json.dumps(after,sort_keys=True)+'\n')
        report['originals_unchanged']=before==after;report['snapshot_unchanged']=host.digest(snapshot)==snapshot_before
        try:host.verify_source(root,hs['source_manifest'],hs['source_manifest_sha256']);report['source_unchanged']=True
        except BaseException as error:report['source_error']=str(error)
        end_usage=resource.getrusage(resource.RUSAGE_CHILDREN)
        report.update(seconds=time.monotonic()-started,peak_sampled_total_family_rss=peak,
            child_user_cpu_seconds=end_usage.ru_utime-usage.ru_utime,child_system_cpu_seconds=end_usage.ru_stime-usage.ru_stime,
            protected_write_denials=guard.denied,signals=signals,snapshot_sha256=snapshot_before)
        report['passed']=(report.get('exit_code')==0 and report.get('original_popen_wait_completed') is True
            and isinstance(report.get('wrapper'),dict) and report['wrapper'].get('passed') is True
            and report['originals_unchanged'] and report['snapshot_unchanged'] and report.get('source_unchanged') is True
            and not survivors and not guard.denied and not signals and not any(k.endswith('error') for k in report))
        for signum,handler in saved.items():signal.signal(signum,handler)
        (output/'RESULT.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return 0 if report['passed'] else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('spec',type=Path);p.add_argument('output',type=Path);p.add_argument('--spec-sha256',required=True)
    a=p.parse_args();return run(a.spec,a.spec_sha256,a.output)
if __name__=='__main__':raise SystemExit(main())
