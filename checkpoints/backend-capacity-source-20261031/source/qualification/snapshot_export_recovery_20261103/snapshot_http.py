"""Qualification-only HTTP projection of a closed database snapshot.

Not create_app, not a service restart, and not a claim about current deployed state.
No native execution, live Store initialization, recovery, migrations or writes.
The five GET handlers and transcript middleware are extracted unchanged from pinned
production source. Read methods and transcript routes are the production functions.
"""
import ast
from dataclasses import fields, MISSING
from contextlib import contextmanager
import hashlib
import json
import math
import signal
from pathlib import Path
import sqlite3
import sys
import threading
from types import MethodType, SimpleNamespace
from urllib.parse import parse_qs, urlsplit, unquote

APP_NAMES = {'recording_contract','chunks','recording_page_list','recording_page_status','recording_status'}
LIFETIME_NAMES = {'_assign_native_read_lease','_NativeSupportReadLifetime'}
STORE_NAMES = {'session','recording_session_contract'}
RECORDING_NAMES = {'status','page_status','list_pages','_batch','_session','_page',
                   '_capture_summary','_page_summary','_page_detail'}


def need(value, reason):
    if not value: raise RuntimeError(reason)


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def verify_source(root, manifest_path, manifest_sha256):
    root=Path(root).resolve(); manifest_path=Path(manifest_path)
    need(not manifest_path.is_symlink() and digest(manifest_path)==manifest_sha256,'source_manifest_pin')
    rows=json.loads(manifest_path.read_bytes())['files']; expected={}
    for row in rows:
        path=Path(row['path'])
        need(not path.is_absolute() and '..' not in path.parts and str(path) not in expected,'source_manifest_path')
        expected[str(path)]=row
    need(bool(expected),'source_manifest_empty')
    actual={str(p.relative_to(root)):p for p in root.rglob('*') if p.is_file()
            and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')}
    need(set(actual)==set(expected),'source_roster')
    need(not any(p.is_symlink() for p in root.rglob('*')),'source_symlink')
    for name,path in actual.items():
        row=expected[name]
        need(path.stat().st_size==row.get('bytes',row.get('size')) and digest(path)==row['sha256'],'source_hash:'+name)
    return root


def extract_handlers(root):
    """Select exact AST nodes without invoking or importing the application factory."""
    tree=ast.parse((root/'audio_backend/app.py').read_text())
    factories=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_create_app']
    need(len(factories)==1,'factory_ast_identity')
    handlers=[n for n in factories[0].body if isinstance(n,ast.FunctionDef) and n.name in APP_NAMES]
    lifetime=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in LIFETIME_NAMES]
    need({n.name for n in handlers}==APP_NAMES and len(handlers)==len(APP_NAMES),'handler_ast_identity')
    need({n.name for n in lifetime}==LIFETIME_NAMES and len(lifetime)==len(LIFETIME_NAMES),'lifetime_ast_identity')
    for node in handlers:
        need(len(node.decorator_list)==1,'handler_decorator_count')
        d=node.decorator_list[0]
        need(isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr=='get'
             and isinstance(d.func.value,ast.Name) and d.func.value.id=='app','handler_get_only')
    storage=ast.parse((root/'audio_backend/storage.py').read_text())
    classes=[n for n in storage.body if isinstance(n,ast.ClassDef) and n.name=='Store']
    need(len(classes)==1,'store_ast_identity')
    methods=[n for n in classes[0].body if isinstance(n,ast.FunctionDef) and n.name in STORE_NAMES]
    need({n.name for n in methods}==STORE_NAMES and len(methods)==len(STORE_NAMES),'store_read_ast_identity')
    return handlers,lifetime,methods


def readonly_authorizer(action,a,b,database,trigger):
    allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_TRANSACTION}
    if hasattr(sqlite3,'SQLITE_RECURSIVE'):allowed.add(sqlite3.SQLITE_RECURSIVE)
    if action in allowed:
        if action==sqlite3.SQLITE_FUNCTION and (b or a or '').lower() in {'load_extension','writefile'}:return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    if action==sqlite3.SQLITE_PRAGMA:
        # Production reader's bounded admission only. No journal/cache/schema writes.
        if (a or '').lower() in {'query_only','busy_timeout','database_list','table_info','index_list','index_info'}:
            if (a or '').lower()=='query_only' and str(b).lower() not in ('1','on'):return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class SQLiteSnapshotGuard:
    """Process-local guard: every SQLite connection is to the one immutable copy."""
    def __init__(self,snapshot):
        self.snapshot=Path(snapshot).resolve();self.original=None;self.connections=0
    def connect(self,database,*args,**kwargs):
        raw=str(database)
        if raw.startswith('file:'):
            parsed=urlsplit(raw); path=Path(unquote(parsed.path)).resolve()
            query=parse_qs(parsed.query)
            need(query.get('mode')==['ro'],'snapshot_connection_mode')
        else:path=Path(raw).resolve()
        need(path==self.snapshot,'snapshot_database_only')
        kwargs['uri']=True;kwargs['timeout']=0
        db=self.original(self.snapshot.as_uri()+'?mode=ro&immutable=1',*args,**kwargs)
        db.execute('PRAGMA query_only=ON');db.set_authorizer(readonly_authorizer)
        self.connections+=1
        return db
    def install(self):
        need(self.original is None,'sqlite_guard_already_installed')
        self.original=sqlite3.connect;sqlite3.connect=self.connect;return self
    def close(self):
        if self.original is not None:sqlite3.connect=self.original;self.original=None


def validate_binding(spec):
    origin=urlsplit(spec['api'])
    need(origin.scheme=='http' and origin.hostname=='127.0.0.1' and origin.port is not None
         and not origin.path and not origin.query and not origin.fragment and not origin.username,'loopback_origin')
    job=Path(spec['job_dir'])
    need(job.is_dir() and job.absolute()==job.resolve(),'job_symlink')
    for name,key in [('capture.json','job_header_sha256'),('uploader/uploader.json','job_journal_sha256')]:
        control=job/name
        need(control.absolute()==control.resolve() and control.is_file() and digest(control)==spec[key],'job_control_pin:'+name)
    header=json.loads((job/'capture.json').read_bytes())
    target=header['target']
    need(target['origin']==spec['api'] and target['session_id']==spec['session_id']
         and target['capture_id']==spec['capture_id'],'job_identity_binding')
    journal=json.loads((job/'uploader/uploader.json').read_bytes())
    need(journal['identity']['api']==spec['api'] and journal['identity']['session_id']==spec['session_id']
         and journal['identity']['capture_id']==spec['capture_id'] and journal['complete'] is True,'uploader_identity_binding')
    return origin.port


def build_app(spec):
    root=verify_source(spec['source_root'],spec['source_manifest'],spec['source_manifest_sha256'])
    snapshot=Path(spec['snapshot']).resolve()
    need(snapshot.is_file() and not Path(spec['snapshot']).is_symlink() and digest(snapshot)==spec['snapshot_sha256'],'snapshot_pin')
    need(snapshot != Path(spec['original_database']).resolve(),'snapshot_must_be_copy')
    original_database=Path(spec['original_database'])
    need(original_database.absolute()==original_database.resolve() and original_database.is_file()
         and digest(original_database)==spec['snapshot_sha256'],'closed_snapshot_exact_copy')
    validate_binding(spec)
    config_path=Path(spec['original_config_path'])
    need(config_path.absolute()==config_path.resolve() and config_path.is_file()
         and digest(config_path)==spec['original_config_sha256'],'original_config_pin')
    original_config=json.loads(config_path.read_bytes())
    need(original_config==spec['original_config'],'original_config_binding')
    sys.path.insert(0,str(root))
    from fastapi import FastAPI,Query
    from starlette.responses import JSONResponse,Response
    from audio_backend.recording_overlap import RecordingOverlap
    from audio_backend.settings import Settings
    from audio_backend.native_owned_support_cache import ReadResponseBudget
    from audio_backend.transcript_http import register_routes
    handlers,lifetime,methods=extract_handlers(root)
    # Read defaults only. Do not instantiate Settings, Store, Worker or any model.
    cfg={f.name:f.default for f in fields(Settings) if f.default is not MISSING}
    cfg.update(spec['original_config']);cfg['data_dir']=snapshot.parent;cfg['start_worker']=False
    settings=SimpleNamespace(**cfg)
    class Holder:
        path=snapshot
        _strict_enabled=spec['original_config']['strict_primary_enabled']
        @contextmanager
        def db(self):
            connection=sqlite3.connect(snapshot.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=0)
            connection.row_factory=sqlite3.Row
            try:yield connection
            finally:connection.close()
    store=Holder();store.settings=settings
    namespace={'__package__':'audio_backend','__name__':'snapshot_production_handlers',
               'math':math,'threading':threading,'Query':Query,'JSONResponse':JSONResponse,
               'store':store,'settings':settings}
    for node in methods:
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(root/'audio_backend/storage.py'),'exec'),namespace)
        setattr(store,node.name,MethodType(namespace[node.name],store))
    recordings=SimpleNamespace(store=store,settings=settings)
    for name in RECORDING_NAMES:setattr(recordings,name,MethodType(getattr(RecordingOverlap,name),recordings))
    app=FastAPI(openapi_url=None,docs_url=None,redoc_url=None)
    namespace.update(app=app,recordings=recordings)
    for node in lifetime+handlers:exec(compile(ast.Module(body=[node],type_ignores=[]),str(root/'audio_backend/app.py'),'exec'),namespace)
    budget=ReadResponseBudget()
    register_routes(app,store,namespace['_assign_native_read_lease'],lambda:budget)
    app.add_middleware(namespace['_NativeSupportReadLifetime'])
    # Method fence is outermost, before any handler or database connection.
    class GETOnly:
        def __init__(self,app):self.app=app
        async def __call__(self,scope,receive,send):
            if scope['type']=='http' and scope.get('method')!='GET':
                return await Response(status_code=405)(scope,receive,send)
            return await self.app(scope,receive,send)
    app.add_middleware(GETOnly)
    app.state.snapshot_only=True;app.state.response_budget=budget
    app.state.sqlite_guard=SQLiteSnapshotGuard(snapshot).install()
    try:
        with store.db() as db:
            sessions=db.execute('SELECT id FROM sessions WHERE deleted=0').fetchall()
            need([row[0] for row in sessions]==[spec['session_id']],'snapshot_session_binding')
            batch=db.execute('SELECT capture_id,eos FROM recording_batches WHERE session_id=?',(spec['session_id'],)).fetchall()
            need(len(batch)==1 and batch[0][0]==spec['capture_id'] and batch[0][1]==1,'snapshot_capture_binding')
            chunks=db.execute('SELECT status FROM chunks WHERE session_id=?',(spec['session_id'],)).fetchall()
            need(bool(chunks) and all(row[0]=='completed' for row in chunks),'snapshot_completed_required')
    except BaseException:
        app.state.sqlite_guard.close();raise
    return app


class ProtectedPaths:
    """Defense-in-depth for cooperative qualification code, not a kernel sandbox.

    Audit events lack dir_fd for open and omit os.write. Explicit wrappers cover
    those Python APIs; SQLite additionally has its own immutable/authorizer guard.
    """
    def __init__(self,paths):
        self.paths=tuple(Path(p).resolve() for p in paths);self.active=True;self.denied=0
        self.originals={}
    def resolve(self,value,dir_fd=None):
        import os
        if isinstance(value,int):
            target=os.readlink('/proc/self/fd/'+str(value))
            return Path(target.removesuffix(' (deleted)')).resolve() if target.startswith('/') else None
        if not isinstance(value,(str,bytes,Path)):return None
        path=Path(os.fsdecode(value))
        if not path.is_absolute() and dir_fd not in (None,-100):
            base=self.resolve(dir_fd)
            need(base is not None,'protected_dir_fd_unresolved')
            path=base/path
        return path.resolve()
    def protects(self,value,dir_fd=None):
        path=self.resolve(value,dir_fd)
        return path is not None and any(path==root or root in path.parents for root in self.paths)
    def check(self,value,dir_fd=None):
        if self.active and self.protects(value,dir_fd):
            self.denied+=1;raise PermissionError('protected_snapshot_original_mutation')
    def audit(self,event,args):
        import os
        if not self.active:return
        if event=='open':
            path,mode,flags=args
            writing=(isinstance(mode,str) and any(ch in mode for ch in 'wax+')) or (
                isinstance(flags,int) and bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)))
            if writing:self.check(path)
        elif event in {'os.remove','os.rmdir'}:self.check(args[0],args[1])
        elif event in {'os.mkdir','os.chmod'}:self.check(args[0],args[2])
        elif event in {'os.chown','os.utime'}:self.check(args[0],args[3])
        elif event=='os.truncate':self.check(args[0])
        elif event in {'os.rename','os.link'}:
            self.check(args[0],args[2]);self.check(args[1],args[3])
        elif event=='os.symlink':self.check(args[1],args[2])
    def install(self):
        import os,fcntl
        for entry in Path('/proc/self/fd').iterdir():
            try:
                fd=int(entry.name);flags=fcntl.fcntl(fd,fcntl.F_GETFL)
                need((flags & os.O_ACCMODE)==os.O_RDONLY or not self.protects(fd),'inherited_writable_protected_fd')
            except FileNotFoundError:continue
            except OSError as error:
                if error.errno==9:continue
                raise
        self.originals['open']=os.open
        def guarded_open(path,flags,mode=0o777,*,dir_fd=None):
            if flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):self.check(path,dir_fd)
            return self.originals['open'](path,flags,mode,dir_fd=dir_fd)
        os.open=guarded_open
        for name in ('write','pwrite','writev','ftruncate','fchmod','fchown'):
            if not hasattr(os,name):continue
            original=getattr(os,name);self.originals[name]=original
            def guarded(fd,*args,_original=original,**kwargs):
                self.check(fd);return _original(fd,*args,**kwargs)
            setattr(os,name,guarded)
        sys.addaudithook(self.audit);return self
    def close(self):
        import os
        self.active=False
        for name,original in self.originals.items():setattr(os,name,original)


class TrafficBudget:
    def __init__(self,deadline,stop):
        self.deadline=deadline;self.stop=stop;self.lock=threading.Lock()
        self.requests=0;self.response_bytes=0;self.active=0;self.failure=None
    def fail(self,reason):
        self.failure=self.failure or reason;self.stop();raise RuntimeError(reason)
    def enter(self,scope):
        import time
        with self.lock:
            self.requests+=1
            if self.requests>256 or time.monotonic()>=self.deadline:self.fail('snapshot_request_bound')
            if len(scope.get('query_string',b''))>1024 or not scope.get('query_string',b'').isascii() or len(scope.get('path','').encode())>2048 or len(scope.get('method',''))>16:
                self.fail('snapshot_request_size_bound')
            if self.failure:self.fail(self.failure)
            self.active+=1
    def body(self,raw):
        with self.lock:
            self.response_bytes+=len(raw)
            if self.response_bytes>64*1024**2:self.fail('snapshot_response_bound')
    def leave(self):
        with self.lock:self.active-=1


def inventory(root):
    root=Path(root);result={}
    for path in [root,*sorted(root.rglob('*'))]:
        st=path.lstat();need(not path.is_symlink(),'protected_tree_symlink')
        result[str(path.relative_to(root))]={'bytes':st.st_size,'mtime_ns':st.st_mtime_ns,
            'ctime_ns':st.st_ctime_ns,'sha256':digest(path) if path.is_file() else None}
    return result


async def serve_snapshot(spec,output):
    """Explicit bounded host; caller must independently review before invoking."""
    import asyncio
    import os
    import time
    import shutil
    import psutil
    import uvicorn
    output=Path(output).resolve();original=Path(spec['original_root']).resolve()
    need(Path(spec['original_database']).resolve().is_relative_to(original),'original_database_root')
    need(Path(spec['job_dir']).resolve().is_relative_to(original),'original_job_root')
    need(not output.is_relative_to(original) and output!=Path(spec['snapshot']).resolve().parent,'private_output_separation')
    roots=[Path(value).resolve() for value in spec['protected_roots']]
    need(bool(roots) and original in roots and Path(spec['source_root']).resolve() in roots
         and len(set(roots))==len(roots),'protected_roots_binding')
    need(all(root.is_dir() and root!=Path('/') and not output.is_relative_to(root) for root in roots),'protected_roots_scope')
    output.mkdir(mode=0o700,exist_ok=False)
    before={str(root):inventory(root) for root in roots};snapshot_hash=digest(spec['snapshot'])
    protected=ProtectedPaths([*roots,Path(spec['snapshot']).resolve()]).install()
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});os.nice(19)
    start=time.monotonic();deadline=start+120;peak=0
    stop_requested=[]
    handlers={sig:signal.signal(sig,lambda signum,frame:stop_requested.append(signum)) for sig in (signal.SIGINT,signal.SIGTERM)}
    budget=TrafficBudget(deadline,lambda:stop_requested.append('traffic_limit'))
    app=None;server=None;task=None;report={'passed':False,'snapshot_only':True,'native_execution':False}
    try:
        app=build_app(spec)
        # All network responses retain actual handler bytes; this only bounds traffic.
        class RequestBounds:
            def __init__(self,app):self.app=app
            async def __call__(self,scope,receive,send):
                if scope['type']!='http':return await self.app(scope,receive,send)
                budget.enter(scope)
                status=None
                async def observed_send(message):
                    nonlocal status
                    if message['type']=='http.response.start':status=message['status']
                    if message['type']=='http.response.body':
                        budget.body(message.get('body',b''))
                    await send(message)
                try:return await self.app(scope,receive,observed_send)
                finally:
                    budget.leave()
                    with (output/'requests.jsonl').open('a') as trace:
                        target=scope['path']+('?' + scope['query_string'].decode('ascii') if scope.get('query_string') else '')
                        trace.write(json.dumps({'method':scope['method'],'path':target,'status':status})+'\n')
        app.add_middleware(RequestBounds)
        port=validate_binding(spec)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,workers=1,
            lifespan='off',access_log=False,log_level='warning',timeout_graceful_shutdown=10,limit_concurrency=4))
        task=asyncio.create_task(server.serve())
        ready=False
        while not task.done():
            need(time.monotonic()<deadline,'snapshot_host_deadline')
            process=psutil.Process();family=[process,*process.children(recursive=True)]
            need(len(family)==1,'snapshot_unexpected_child')
            rss=sum(p.memory_info().rss for p in family);peak=max(peak,rss)
            need(rss<=384*1024**2,'snapshot_rss_bound')
            need(shutil.disk_usage(output).free>=11*1024**3//2,'snapshot_disk_floor')
            need(sum(p.stat().st_size for p in output.rglob('*') if p.is_file())<=8*1024**2,'snapshot_output_bound')
            if server.started and not ready:
                (output/'ready.json').write_text(json.dumps({'snapshot_only':True,'host':'127.0.0.1','port':port,
                    'session_id':spec['session_id'],'capture_id':spec['capture_id'],'snapshot_sha256':snapshot_hash})+'\n');ready=True
            if stop_requested or (output/'STOP').exists():server.should_exit=True
            await asyncio.sleep(.05)
        await task
        need(ready,'snapshot_never_ready');need(budget.failure is None,'snapshot_traffic_failed');report['passed']=True
    except BaseException as error:
        report['error']={'type':type(error).__name__,'message':str(error)[:1024]}
    finally:
        if server is not None:server.should_exit=True
        if task is not None and not task.done():
            try:await asyncio.wait_for(asyncio.shield(task),12)
            except BaseException as error:
                report['shutdown_error']=type(error).__name__;report['passed']=False
                task.cancel()
                try:await asyncio.wait_for(asyncio.shield(task),3)
                except BaseException as cancelled:report['cancel_outcome']=type(cancelled).__name__
        # SQLite and protected-path guards remain installed until process exit,
        # including unconfirmed shutdown. Outer owner must kill/wait if needed.
        if app is not None:
            report['response_leases_remaining']=app.state.response_budget._lease_count
            if report['response_leases_remaining']:report['passed']=False
        report.update(original_tree_unchanged={str(root):inventory(root) for root in roots}==before,
            snapshot_unchanged=digest(spec['snapshot'])==snapshot_hash,protected_write_denials=protected.denied,
            requests=budget.requests,response_bytes=budget.response_bytes,traffic_failure=budget.failure,active_requests=budget.active,peak_sampled_rss=peak,seconds=time.monotonic()-start)
        if not report['original_tree_unchanged'] or not report['snapshot_unchanged'] or protected.denied or budget.failure or budget.active:report['passed']=False
        report['server_task_awaited']=task is not None and task.done()
        if not report['server_task_awaited']:report['passed']=False
        report['stop_signals']=stop_requested
        for signum,handler in handlers.items():signal.signal(signum,handler)
        (output/'RESULT.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    return 0 if report['passed'] else 1


def main():
    import argparse,asyncio,os
    parser=argparse.ArgumentParser();parser.add_argument('spec',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--spec-sha256',required=True);args=parser.parse_args();os.umask(0o077)
    need(args.spec.is_file() and not args.spec.is_symlink() and digest(args.spec)==args.spec_sha256,'spec_pin')
    return asyncio.run(serve_snapshot(json.loads(args.spec.read_bytes()),args.output))

if __name__=='__main__':raise SystemExit(main())
