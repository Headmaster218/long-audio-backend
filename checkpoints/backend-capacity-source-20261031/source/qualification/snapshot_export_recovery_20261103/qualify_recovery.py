"""Bounded actual GET-only recovery against a closed-snapshot projection host."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import psutil
sys.path.insert(0, str(Path(__file__).resolve().parent))
import snapshot_http as host


def load(p):return json.loads(Path(p).read_bytes())
def save(p,v):Path(p).write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')
def need(ok,why):
    if not ok:raise RuntimeError(why)


def validate_export(result, root):
    """Validate exact speech qualification shape before accepting producer artifacts."""
    root=Path(root)
    need(type(result) is dict and result.get('schema')=='recorder-explicit-export-recovery-v1'
         and result.get('status')=='exported' and type(result.get('attempts')) is int
         and 1<=result['attempts']<=2, 'recovery_schema')
    name=result.get('output')
    need(name=='attempt-%02d' % result['attempts'],'recovery_output_binding')
    folder=root/name
    need(folder.is_dir() and not folder.is_symlink() and folder==folder.resolve(),'export_output_path')
    manifest_path=folder/'complete.json'
    need(manifest_path.is_file() and not manifest_path.is_symlink(),'export_manifest_path')
    manifest=load(manifest_path)
    need(manifest==result.get('export'),'export_manifest_binding')
    need(manifest.get('schema')=='recorder-incremental-span-export-v1'
         and manifest.get('status')=='exported'
         and manifest.get('projection')=='primary-baseline-v1'
         and type(manifest.get('frames')) is int and manifest['frames']==144000
         and type(manifest.get('nonempty_text_units')) is int and manifest['nonempty_text_units']>0
         and type(manifest.get('units')) is int and manifest['units']>=manifest['nonempty_text_units']
         and manifest.get('text_status')=='primary_text_observed'
         and manifest.get('complete_content') is False and manifest.get('observation_atomic') is False,
         'speech_export_shape')
    artifacts=manifest.get('artifacts')
    need(type(artifacts) is dict and set(artifacts)=={'export.json','transcript.txt'},'export_artifact_roster')
    for name,value in artifacts.items():
        p=folder/name
        need(type(value) is dict and set(value)=={'bytes','sha256'} and type(value['bytes']) is int and value['bytes']>0
             and p.is_file() and not p.is_symlink() and p.stat().st_size==value['bytes']
             and host.digest(p)==value['sha256'],'artifact_hash')
    data=load(folder/'export.json')
    need(data.get('schema')==manifest['schema'] and data.get('projection')==manifest['projection']
         and data.get('remote_read_only') is True and data.get('summary',{}).get('frames')==144000
         and data.get('summary',{}).get('transcript_revision')==manifest.get('transcript_revision'), 'export_json_binding')
    units=data.get('units')
    need(type(units) is list and len(units)==manifest['units']
         and sum(type(row.get('text')) is str and bool(row['text'].strip()) for row in units)==manifest['nonempty_text_units'],
         'export_nonempty_binding')
    return folder,manifest


def run(spec,output):
    os.umask(0o077);output=Path(output).resolve()
    hostspec=load(spec['host_spec']);need(host.digest(spec['host_spec'])==spec['host_spec_sha256'],'host_spec_pin')
    roots=[Path(p).resolve() for p in hostspec['protected_roots']]
    source=host.verify_source(hostspec['source_root'],hostspec['source_manifest'],hostspec['source_manifest_sha256'])
    need(source in roots and Path(hostspec['original_root']).resolve() in roots,'protected_roots_binding')
    need(Path(__file__).resolve()==source/'qualification/snapshot_export_recovery_20261103/qualify_recovery.py','wrapper_source_location')
    need(not output.is_relative_to(Path(hostspec['snapshot']).resolve().parent),'output_snapshot_separation')
    need(all(not output.is_relative_to(p) for p in roots),'output_protected')
    need(not os.path.lexists(output),'fresh_output')
    protected=host.ProtectedPaths([*roots,Path(hostspec['snapshot']).resolve()]).install()
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});os.nice(19)
    output.mkdir(mode=0o700)
    before={str(p):host.inventory(p) for p in roots};save(output/'ORIGINAL_BEFORE.json',before)
    source=Path(hostspec['source_root']);host.verify_source(source,hostspec['source_manifest'],hostspec['source_manifest_sha256'])
    port=host.validate_binding(hostspec)
    with socket.socket() as check:
        check.settimeout(.2);need(check.connect_ex(('127.0.0.1',port))!=0,'original_port_still_live')
    owned=[];seen_descendants={};started=time.monotonic();deadline=started+140;peak=0;server=None
    report={'passed':False,'snapshot_only':True,'native_inference_executed':False,'inference_replayed':False,
        'snapshot_sha256':hostspec['snapshot_sha256'],'source_manifest_sha256':hostspec['source_manifest_sha256'],
        'api':hostspec['api'],'session_id':hostspec['session_id'],'capture_id':hostspec['capture_id'],
        'meaning':'New GET-only projection of closed snapshot; not original watch/export success',
        'outer_limits':{'seconds':140,'rss_bytes':512*1024**2,'cpus':1,'nice':19},
        'client_limits':{'seconds_each':90,'attempts_each':2,'clients':2}}
    cancelled=[];old={s:signal.signal(s,lambda signum,frame:cancelled.append(signum)) for s in (signal.SIGINT,signal.SIGTERM)}
    def check():
        nonlocal peak
        need(not cancelled,'cancelled');need(time.monotonic()<deadline,'overall_deadline')
        process=psutil.Process();descendants=process.children(recursive=True)
        for p in descendants:
            try:seen_descendants[p.pid]=p.create_time()
            except psutil.NoSuchProcess:pass
        need(all(p.pid in {owned_process.pid for owned_process,_,_,_ in owned} for p in descendants),'unexpected_descendant')
        rss=sum(p.memory_info().rss for p in [process,*descendants] if p.is_running())
        peak=max(peak,rss);need(rss<=512*1024**2,'overall_rss')
        need(shutil.disk_usage(output).free>=11*1024**3//2,'disk_floor')
        need(sum(p.stat().st_size for p in output.rglob('*') if p.is_file())<=256*1024**2,'output_bound')
    def spawn(command,name):
        out=(output/(name+'.stdout')).open('xb');err=(output/(name+'.stderr')).open('xb')
        p=subprocess.Popen([str(x) for x in command],stdin=subprocess.DEVNULL,stdout=out,stderr=err)
        owned.append((p,name,out,err));return p
    def until(p,predicate,label,seconds=30):
        end=min(deadline,time.monotonic()+seconds)
        while not predicate():
            check()
            if p.poll() is not None:
                need(predicate(),'child_early_exit:'+label);break
            need(time.monotonic()<end,'wait_deadline:'+label);time.sleep(.05)
        check()
    try:
        server=spawn([sys.executable,'-I','-B',Path(__file__).with_name('snapshot_http.py'),spec['host_spec'],output/'host',
                      '--spec-sha256',spec['host_spec_sha256']],'host')
        until(server,lambda:(output/'host/ready.json').exists(),'ready')
        ready=load(output/'host/ready.json');need(ready['snapshot_only'] and ready['port']==port,'ready_binding')
        results=[]
        for name in ['recovery','repeat']:
            command=[sys.executable,'-I','-B',Path(__file__).with_name('recovery_client.py'),
                spec['host_spec'],output/name,'--spec-sha256',spec['host_spec_sha256']]
            client=spawn(command,name);until(client,lambda:client.poll() is not None,name,100)
            need(client.wait(timeout=1)==0,'client_refused:'+name)
            result=json.loads((output/(name+'.stdout')).read_text())
            folder,manifest=validate_export(result,output/name)
            results.append((folder,result))
        for name in ['export.json','transcript.txt','complete.json']:
            need((results[0][0]/name).read_bytes()==(results[1][0]/name).read_bytes(),'repeat_not_identical')
        report.update(recovery=results[0][1],repeat=results[1][1],export_dirs=[str(x[0]) for x in results],repeat_byte_identical=True)
        (output/'host/STOP').touch();until(server,lambda:server.poll() is not None,'host_stop',20)
        need(server.wait(timeout=1)==0 and load(output/'host/RESULT.json')['passed'],'host_failed')
        rows=[json.loads(line) for line in (output/'host/requests.jsonl').read_text().splitlines()]
        need(rows and all(row['method']=='GET' and row['status']==200 for row in rows),'unexpected_http_effect')
        report.update(requests=len(rows),all_requests_get_only=True,host=load(output/'host/RESULT.json'),scenario_complete=True)
    except BaseException as error:
        report['error']={'type':type(error).__name__,'message':str(error)[:2048]}
    finally:
        if (output/'host').exists():(output/'host/STOP').touch(exist_ok=True)
        # Snapshot all owned descendants before stopping original Popen objects.
        for process in psutil.Process().children(recursive=True):
            try:seen_descendants[process.pid]=process.create_time()
            except psutil.NoSuchProcess:pass
        waits=[]
        for p,name,out,err in reversed(owned):
            try:
                if p.poll() is None:
                    p.terminate()
                    try:code=p.wait(timeout=15)
                    except subprocess.TimeoutExpired:p.kill();code=p.wait(timeout=5)
                else:code=p.wait(timeout=1)
                waits.append({'name':name,'pid':p.pid,'exit_code':code,'original_popen_wait_completed':True})
            except BaseException as error:report[name+'_cleanup_error']=str(error)
            finally:out.close();err.close()
        survivors=[]
        for pid,created in seen_descendants.items():
            try:
                child=psutil.Process(pid)
                if child.create_time()==created and child.is_running():
                    survivors.append(pid)
                    # These exact identities were observed below our process.
                    child.terminate()
                    try:child.wait(timeout=3)
                    except psutil.TimeoutExpired:child.kill();child.wait(timeout=3)
            except psutil.NoSuchProcess:pass
            except BaseException as error:report['descendant_cleanup_error']=str(error)
        remaining=[]
        for pid,created in seen_descendants.items():
            try:
                child=psutil.Process(pid)
                if child.create_time()==created and child.is_running():remaining.append(pid)
            except psutil.NoSuchProcess:pass
        report['descendants_remaining']=remaining
        if survivors:report['unexpected_descendant_cleanup_error']='Descendants survived original child waits'
        if remaining:report['descendant_cleanup_error']='Owned descendants remain'
        report['protected_write_denials']=protected.denied
        if protected.denied:report['protected_write_error']='Protected mutation refused'
        after={str(p):host.inventory(p) for p in roots};save(output/'ORIGINAL_AFTER.json',after)
        report['originals_unchanged']=before==after
        report['snapshot_unchanged']=host.digest(hostspec['snapshot'])==hostspec['snapshot_sha256']
        if not report['snapshot_unchanged']:report['snapshot_error']='Snapshot changed during qualification'
        try:host.verify_source(source,hostspec['source_manifest'],hostspec['source_manifest_sha256']);report['source_unchanged']=True
        except BaseException as error:report['source_error']=str(error)
        with socket.socket() as sock:
            sock.settimeout(.2);report['listener_closed']=sock.connect_ex(('127.0.0.1',port))!=0
        report.update(child_waits=waits,seconds=time.monotonic()-started,peak_sampled_rss=peak,signals=cancelled)
        report['passed']=report.get('scenario_complete') is True and report['originals_unchanged'] and report['listener_closed'] and not any(k.endswith('error') for k in report) and len(waits)==len(owned)==3 and all(w['exit_code']==0 for w in waits)
        for signum,handler in old.items():signal.signal(signum,handler)
        save(output/'RESULT.json',report)
    return 0 if report['passed'] else 1


def main():
    parser=argparse.ArgumentParser();parser.add_argument('spec',type=Path);parser.add_argument('output',type=Path);parser.add_argument('--spec-sha256',required=True)
    args=parser.parse_args();need(not args.spec.is_symlink() and host.digest(args.spec)==args.spec_sha256,'spec_pin')
    return run(load(args.spec),args.output)
if __name__=='__main__':raise SystemExit(main())
