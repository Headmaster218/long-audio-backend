"""One guarded wheels_env phase: exact acquisition, offline install and build.
The acquisition clock is not restarted. No runtime native import or model call.
"""
import importlib.util
from pathlib import Path
import sys
HERE = Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('native_install_common', HERE/'common.py')
c=importlib.util.module_from_spec(s);s.loader.exec_module(c)
import json
import os
import signal
import subprocess
import time
import venv

ACQUISITION = {
    'acquire.py':'a79053fae502ed0621c94aa94aafc634fed7997a423d210fb806a2587ea391c1',
    'ASSET_MANIFEST.json':'b189c4da6a8a28d1ca27ebba75c65a118d9b14cb1496f0e4b641f0385c79417b',
}
ORIGINALS=[]


def acquire_exact_wheels():
    for name,expected in ACQUISITION.items():
        c.require(c.regular(c.PREP/'acquisition'/name,checker=c.check)['sha256']==expected,'acquisition pin')
    acquire=c.privacy()
    ctx=acquire.context_from_env(str(c.PREP))
    manifest=acquire.small_json(c.PREP/'acquisition/ASSET_MANIFEST.json')
    c.require(manifest==acquire.build_manifest(c.WORKSPACE),'acquisition manifest changed')
    acquire.run_stage('wheels_env',manifest,ctx)
    audit=c.read_json(c.PREP/'receipts/wheel-audit.json',c.check)
    stage=c.read_json(c.PREP/'receipts/stage-wheels_env.json',c.check)
    c.require(stage['status']=='VERIFIED' and stage['stage']=='wheels_env' and
              stage['model_loads']==stage['model_calls']==stage['installs']==0,'wheel stage receipt')
    c.require(audit['status']=='VERIFIED' and audit['install_performed'] is False and
              audit['aggregate']['members']<=8192 and audit['aggregate']['expanded_bytes']<=200*2**20,
              'wheel audit receipt')
    rows=c.packages(); expected={r['filename'] for r in rows}
    actual,_,_=c.scan(c.PREP/'wheels',c.check)
    c.require(set(actual)==expected,'wheel exact membership')
    for row in rows:c.verify_expected(c.PREP/'wheels'/row['filename'],row,c.check)
    c.require(len(audit['wheels'])==28 and all(x['status']=='VERIFIED' and
              x['materialized_member_bytes']==0 for x in audit['wheels']),'wheel per-file audit')
    return audit


def create_environment():
    target=c.PREP/'environment'
    c.require(not target.exists() and not target.is_symlink(),'environment already exists')
    target.mkdir(mode=0o700);(target/'lib64').mkdir()
    venv.EnvBuilder(system_site_packages=False,clear=False,symlinks=False,with_pip=False,
                    upgrade=False,upgrade_deps=False).create(str(target))
    cfg=c.regular(target/'pyvenv.cfg',maximum=65536,contents=True,checker=c.check).decode()
    c.require('include-system-site-packages = false\n' in cfg,'venv global packages')
    rows,_,_=c.scan(target,c.check)
    c.require(all('__pycache__' not in Path(x).parts for x in rows),'venv bytecode')
    host=c.read_json(HERE/'HOST_RUNTIME.json',c.check)
    for name in ('python','python3','python3.12'):
        c.verify_expected(target/'bin'/name,host['python'],c.check)
    c.category()


def copy_source():
    rows=c.source_verify(c.SOURCE)
    target=Path(os.environ['GUARD_RUN_DIRECTORY'])/'tmp/source'
    c.require(not target.exists(),'copied source exists');target.mkdir(mode=0o700)
    for rel,row in rows.items():
        c.check();src=c.safe_path(c.SOURCE,rel);out=c.safe_path(target,rel)
        out.parent.mkdir(parents=True,exist_ok=True)
        # Every source file is <=32 MiB in the frozen source guard. Copy with bounded reads.
        before=src.lstat();fd=os.open(src,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            c.require(c.identity(before)==c.identity(os.fstat(fd)),'source open identity')
            with out.open('xb') as dst:
                while block:=os.read(fd,65536):c.check();dst.write(block)
            c.require(c.identity(before)==c.identity(os.fstat(fd))==c.identity(src.lstat()),'source copy changed')
        finally:os.close(fd)
        c.verify_expected(out,row,c.check)
    c.source_verify(target);c.category()
    return target


def child(step, receipt):
    c.check();c.category()
    log=c.PREP/'build'/(step+'.log')
    env=dict(os.environ)
    # Inherit only the guard's already-clean environment. All scratch/cache destinations
    # remain in the current owned run; the category includes every guard run.
    for key in ('PYTHONPATH','PYTHONHOME','LD_PRELOAD','LD_LIBRARY_PATH','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE'):env.pop(key,None)
    env.update({'PYTHONSAFEPATH':'1','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1',
                'PYTHONPYCACHEPREFIX':str(Path(env['GUARD_RUN_DIRECTORY'])/'tmp/never-bytecode'),
                'PIP_CONFIG_FILE':os.devnull,'PIP_NO_INDEX':'1','PIP_NO_CACHE_DIR':'1',
                'PIP_DISABLE_PIP_VERSION_CHECK':'1','SOURCE_DATE_EPOCH':'0'})
    p=None;pidfd=None;waited=False;code=None;error=None
    max_gap=0.;last=time.monotonic();peak=0
    with log.open('xb') as output:
        try:
            p=subprocess.Popen([str(c.PREP/'environment/bin/python3.12'),'-I','-B','-S',
                                str(HERE/'step.py'),step],cwd=c.PREP,env=env,
                               stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT)
            ORIGINALS.append(p)  # Retain the original before any identity operation or wait.
            # Our unreaped original child PID cannot be reused. No poll/wait precedes pidfd_open.
            pidfd=os.pidfd_open(p.pid,0)
            while True:
                c.check();value=c.category();peak=max(peak,value['charged_bytes'])
                now=time.monotonic();max_gap=max(max_gap,now-last);last=now
                c.require(log.stat().st_size<=2**20,'substep log cap')
                if p.poll() is not None:break
                time.sleep(min(.05,max(0,float(env['GUARD_WORK_END'])-time.monotonic())))
            code=p.wait(timeout=c.remaining_timeout(float(env['GUARD_WORK_END']),time.monotonic(),.5));waited=True
            c.check();c.category();c.require(code==0,'substep nonzero: '+step)
        except BaseException as exc:
            error=c.failure_summary(exc)
            if p is not None and not waited:
                if pidfd is None:
                    # Cannot prove a signal target. Ask the outer qualified supervisor to stop.
                    Path(env['GUARD_STOP_PATH']).touch(exist_ok=True)
                else:
                    try:signal.pidfd_send_signal(pidfd,signal.SIGKILL)
                    except ProcessLookupError:pass
                    try:
                        left=c.remaining_timeout(float(env['GUARD_ABSOLUTE_END']),time.monotonic(),5.,reserve=2.)
                        code=p.wait(timeout=left);waited=True
                    except (c.Refused,subprocess.TimeoutExpired):
                        # No renewed wait after expiry. Preserve the original and
                        # report uncertainty for the qualified outer supervisor.
                        pass
            raise
        finally:
            if pidfd is not None:os.close(pidfd)
            receipt.append({'step':step,'original_retained':p is not None,'original_waited':waited,
                            'returncode':code,'error':error,'sample_gap_seconds_max':max_gap,
                            'peak_category_charged_bytes':peak})


def main():
    c.require(sys.argv[1:]==[],'no mutable installation arguments')
    c.check();start=time.monotonic();host=c.verify_base();initial=c.category()
    c.require(initial['charged_bytes']+38442241+2**20+c.RESERVE<=c.CAP,'wheel acquisition category admission')
    c.require(not (c.PREP/'build').exists(),'build root already exists')
    (c.PREP/'build').mkdir(mode=0o700)
    receipt={'schema':'native-installation-result-v1','status':'RUNNING','substeps':[],
             'models_loaded':0,'model_calls':0,'service_started':False,'native_import_smoke':False,
             'work_end':float(os.environ['GUARD_WORK_END']),
             'absolute_end':float(os.environ['GUARD_ABSOLUTE_END'])}
    try:
        audit=acquire_exact_wheels();receipt['wheel_expansion']=audit['aggregate']
        create_environment();copy_source()
        for step in ('install','build','backend','verify'):child(step,receipt['substeps'])
        c.source_verify(c.SOURCE);c.verify_base()
        receipt.update(status='VERIFIED',elapsed_seconds=time.monotonic()-start,category=c.category(),
                       interpreter_copies=3,interpreter_copy_bytes=3*host['python']['bytes'])
    except BaseException as exc:
        receipt.update(status='FAILED',error=c.failure_summary(exc))
        raise
    finally:
        # Bounded failure receipt uses the outer cleanup reserve, never starts more work.
        end=float(os.environ['GUARD_ABSOLUTE_END'])
        def cleanup_check():c.require(time.monotonic()<end-1,'cleanup receipt deadline')
        c.write_json(c.PREP/'build/installation-result.json',receipt,cleanup_check)

if __name__=='__main__':raise SystemExit(c.cli_boundary(main))
