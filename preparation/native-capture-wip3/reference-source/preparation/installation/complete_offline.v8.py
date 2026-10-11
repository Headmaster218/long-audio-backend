"""Closed completion of the exact sealed pre-install failure. No reacquisition."""
import importlib.util
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('prior_install_preparation',HERE/'prepare_environment.py')
prior=importlib.util.module_from_spec(s);s.loader.exec_module(prior)
c=prior.c
import os
import signal
import subprocess
import time
ORIGINALS=[]
SEAL_SHA='fbd126b2f979163dd20f2a2862f170967f142a82a4d4341aa9b4acdbd1f8aff6'
OLD_SOURCE=c.PREP/'runs/run-udsgk4o8/tmp/source'
def closed_state(seal,site_members):
    c.require(seal['status']=='FAILED_BEFORE_PACKAGE_INSTALL' and seal['installed_site_empty'] is True
              and seal['successful_install_steps']==0 and len(seal['pins'])==385,
              'closed terminal precondition')
    c.require(not site_members,'installed site is not empty')
def preflight():
    c.check();c.verify_base();c.category()
    path=HERE/'INDEPENDENT_TERMINAL_QUALIFICATION.json'
    c.require(c.regular(path,checker=c.check)['sha256']==SEAL_SHA,'terminal seal changed')
    seal=c.read_json(path,c.check);closed_state(seal,list(c.staged_site().iterdir()))
    for pin in seal['pins']:
        path=Path(pin['path']);c.require(path.is_relative_to(c.PREP),'seal path scope')
        c.verify_expected(path,pin,c.check)
    rows=c.packages();wheels,_,_=c.scan(c.PREP/'wheels',c.check)
    c.require(set(wheels)=={x['filename'] for x in rows},'retained wheel membership')
    for row in rows:c.verify_expected(c.PREP/'wheels'/row['filename'],row,c.check)
    env,_,_=c.scan(c.PREP/'environment',c.check)
    expected={str(Path(x['path']).relative_to(c.PREP/'environment')) for x in seal['pins']
              if Path(x['path']).is_relative_to(c.PREP/'environment')}
    c.require(set(env)==expected,'retained environment membership')
    c.source_verify(c.SOURCE);c.source_verify(OLD_SOURCE)
    build,_,_=c.scan(c.PREP/'build',c.check)
    c.require(set(build)=={'install.log','installation-result.json'},'closed build membership')
    for name in ('wheels','wheels-install.json','backend-wheel.json','backend-install.json','installed-verification.json'):
        path=c.PREP/'build'/name;c.require(not path.exists() and not path.is_symlink(),'completion output exists')
    return seal
def child(step, receipt):
    c.check();c.category()
    log=Path(os.environ['GUARD_RUN_DIRECTORY'])/'tmp/offline-completion'/(step+'.log')
    env=dict(os.environ)
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
                    Path(env['GUARD_STOP_PATH']).touch(exist_ok=True)
                else:
                    try:signal.pidfd_send_signal(pidfd,signal.SIGKILL)
                    except ProcessLookupError:pass
                    try:
                        left=c.remaining_timeout(float(env['GUARD_ABSOLUTE_END']),time.monotonic(),5.,reserve=2.)
                        code=p.wait(timeout=left);waited=True
                    except (c.Refused,subprocess.TimeoutExpired):
                        pass
            raise
        finally:
            if pidfd is not None:os.close(pidfd)
            receipt.append({'step':step,'original_retained':p is not None,'original_waited':waited,
                            'returncode':code,'error':error,'sample_gap_seconds_max':max_gap,
                            'peak_category_charged_bytes':peak})
def main():
    c.require(sys.argv[1:]==[],'closed driver accepts no arguments')
    c.check();seal=preflight()
    def no_network(event,args):
        if event.startswith('socket.'):raise PermissionError('offline completion denies sockets')
    sys.addaudithook(no_network)
    out=Path(os.environ['GUARD_RUN_DIRECTORY'])/'tmp/offline-completion';out.mkdir()
    result={'status':'RUNNING','prior_seal_sha256':SEAL_SHA,'substeps':[]}
    try:
        prior.copy_source()
        for step in ('install','build','backend','verify'):child(step,result['substeps'])
        c.source_verify(c.SOURCE);c.source_verify(OLD_SOURCE);c.verify_base()
        for pin in seal['pins']:c.verify_expected(pin['path'],pin,c.check)
        result.update(status='VERIFIED',category=c.category())
    except BaseException as exc:
        result.update(status='FAILED',failure=c.failure_summary(exc));raise
    finally:
        def cleanup():c.require(time.monotonic()<float(os.environ['GUARD_ABSOLUTE_END'])-1,'receipt deadline')
        c.write_json(out/'result.json',result,cleanup)
if __name__=='__main__':raise SystemExit(c.cli_boundary(main))
