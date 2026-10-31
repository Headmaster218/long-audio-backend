"""Local qualification child; original application/Worker, no inference replacements.
The supervisor owns the inherited instance lock and resource/lifetime limits.
"""
import asyncio
import json
import os
import signal
from pathlib import Path
import sys
import time

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from audio_backend.settings import Settings
from audio_backend.app import create_app


def save(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,sort_keys=True,default=str))
    temporary.replace(path)


async def serve(config,fd,out):
    import uvicorn
    settings=Settings(**config)
    app=create_app(settings,instance_lock_fd=fd)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=0,workers=1,
        access_log=False,log_level='warning',timeout_graceful_shutdown=60))
    # Separate supervisor signal avoids uvicorn replaying SIGTERM before receipt write.
    signal.signal(signal.SIGUSR1,lambda *_:setattr(server,'should_exit',True))
    task=asyncio.create_task(server.serve())
    startup_deadline=time.monotonic()+180
    permit=None
    failure=None
    try:
        while not server.started:
            if time.monotonic()>=startup_deadline:raise TimeoutError('service_startup_deadline')
            if task.done():
                await task
                raise RuntimeError('server_not_started')
            await asyncio.sleep(.05)
        permit=getattr(settings,'_native_execution_permit',None)
        cells=app.state.worker.native_owned_cells
        while not cells.owner.health()['ready']:
            if time.monotonic()>=startup_deadline:raise TimeoutError('cells_startup_deadline:'+str(cells.owner.health()))
            if task.done():
                await task
                raise RuntimeError('cells_not_ready')
            await asyncio.sleep(.05)
        save(out/'ready.json',{'pid':os.getpid(),'port':server.servers[0].sockets[0].getsockname()[1],
            'strict':settings.strict_primary_enabled,'worker_alive':app.state.worker.thread.is_alive(),
            'cells':cells.owner.health(),'inherited_lock':app.state.instance_lock._inherited})
        await task
    except BaseException as exc:
        failure={'type':type(exc).__name__,'message':str(exc)[:2048]}
        raise
    finally:
        state=app.state
        permit_clear=(permit is not None and not permit._handles and permit._comparison is None
            and permit._unavailable is None and permit._primary_observation_failure is None)
        result={'schema':'actual-service-cleanup-v1','error':failure,
            'shutdown':getattr(state,'shutdown_decision_diagnostics',None),
            'lock_closed':getattr(state,'instance_lock_cleanup_complete',False),
            'store_safe':state.store.strict_cleanup_confirmed(),
            'worker_alive':state.worker.thread.is_alive(),'permit_clear':permit_clear,
            'shutdown_errors':{name:str(getattr(state,name))[:2048] for name in
                ('worker_shutdown_error','moss_jobs_shutdown_error','native_owned_cells_shutdown_error',
                 'strict_store_cleanup_error','strict_lock_cleanup_error') if hasattr(state,name)}}
        save(out/'terminal.json',result)
        if failure is None and (not result['lock_closed'] or not result['store_safe'] or
                result['worker_alive'] or not permit_clear or result['shutdown_errors']):
            raise RuntimeError('application_cleanup_not_confirmed')


if __name__=='__main__':
    config_path,fd,out=sys.argv[1:]
    asyncio.run(serve(json.loads(Path(config_path).read_text()),int(fd),Path(out)))
