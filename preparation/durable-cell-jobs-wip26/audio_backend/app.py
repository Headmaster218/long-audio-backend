from contextlib import asynccontextmanager
from datetime import datetime
from fractions import Fraction
import asyncio
import hashlib
import hmac
import json
import math
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Annotated, Literal
from fastapi import FastAPI, HTTPException, Request, Header, Query
from pydantic import BaseModel, Field, ConfigDict
from starlette.concurrency import run_in_threadpool
from .settings import Settings
from .storage import Store, ident
from .worker import Worker
from .resources import InstanceLock, check_resources, run_bounded, tree_rss
from . import change_feed
from .boundary_store import finish_stream
from .fragment_cpu_provider import CPUFactory as _CPUFactory

_CPU_INITIALIZE = _CPUFactory.initialize

FORMATS='wav,flac,ogg,mp3,mov,matroska,webm,aac'


def _assign_native_read_lease(scope,lease):
    # A sync endpoint can finish on its thread after ASGI cancellation. Setting
    # then rechecking the close event covers both sides of the handoff race.
    scope['_native_support_read_lease']=lease
    done=scope.get('_native_support_read_done')
    if done is not None and done.is_set():
        if scope.get('_native_support_read_lease') is lease:
            scope.pop('_native_support_read_lease',None)
        lease.release()
        return False
    return True


class _NativeSupportReadLifetime:
    """Hold bounded response buffers until the outer ASGI send has finished."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        private=scope.get('type')=='http' and any(part in scope.get('path','') for part in ('/native-owned-supports','/native-owned-cells','/moss-jobs'))
        if not private:return await self.app(scope,receive,send)
        done=threading.Event()
        scope['_native_support_read_done']=done
        async def private_receive():
            message=await receive()
            if message['type']=='http.disconnect':done.set()
            return message
        async def private_send(message):
            if private and message['type']=='http.response.start':
                headers=[(k,v) for k,v in message.get('headers',[])
                         if k.lower() not in (b'cache-control',b'pragma',b'x-content-type-options')]
                message=dict(message,headers=headers+[(b'cache-control',b'private, no-store'),
                    (b'pragma',b'no-cache'),(b'x-content-type-options',b'nosniff')])
            await send(message)
        try:await self.app(scope,private_receive,private_send)
        finally:
            done.set()
            lease=scope.pop('_native_support_read_lease',None)
            if lease is not None:lease.release()

class SessionRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(default='',max_length=200)
    identity_group: str=Field(default='default',min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_.-]+$')
    identity_mode: Literal['legacy','verified_review_v1'] | None = None
    processing_mode: Literal['legacy','fragment_pcm16_v1'] = 'legacy'
class ReviewAttestation(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    version: Literal['all_pooled_support_single_identified_voice_v1']
    reviewed_all_pooled_support: bool
    single_identified_voice: bool
    no_other_overlapping_voice: bool

class ReviewMutation(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    expected_generation: str=Field(pattern=r'^[a-f0-9]{32}$')
    expected_revision: int=Field(ge=0)
    idempotency_key: str=Field(min_length=1,max_length=128)

class IdentityReviewRequest(ReviewMutation):
    kind: Literal['positive','negative']
    support_id: str=Field(pattern=r'^[a-f0-9]{32}$')
    vector_id: str=Field(pattern=r'^[a-f0-9]{32}$')
    manifest_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    vector_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    space_id: str=Field(pattern=r'^[a-f0-9]{64}$')
    person_id: str | None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    new_person_name: str | None=Field(default=None,min_length=1,max_length=200)
    attestation: ReviewAttestation

class IdentityRelationRequest(ReviewMutation):
    kind: Literal['same_person','different_people']
    person_id: str=Field(pattern=r'^[a-f0-9]{32}$')
    other_person_id: str=Field(pattern=r'^[a-f0-9]{32}$')
    confirmed: bool

class SpeakerRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str | None=Field(default=None,max_length=200)
    merge_into: str | None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    undo_merge: bool=False
class EnrollmentRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(min_length=1,max_length=200)
    source_segment_id: int=Field(gt=0)

class SpeakerExampleRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_segment_id: int=Field(gt=0)

class SegmentRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    speaker_id: str | None=Field(default=None,pattern=r'^[a-f0-9]{32}$')


def probe(path,settings):
    output=path.with_suffix('.probe.json')
    try:
        run_bounded([settings.ffprobe,'-v','error','-protocol_whitelist','file,pipe',
                     '-format_whitelist',FORMATS,'-show_entries',
                     'format=format_name,duration:stream=codec_type,codec_name,sample_rate,duration_ts,time_base',
                     '-of','json',path],settings,timeout=60,stdout_path=output)
        result=json.loads(output.read_text())
        duration=float(result.get('format',{}).get('duration',0))
        audio=[s for s in result.get('streams',[]) if s.get('codec_type')=='audio']
        # WAV/FLAC at the canonical rate have an exact sample-count time base.
        # The six-decimal format duration can round down across a sample edge.
        # Compressed codecs/container edit lists are not exact decoded counts.
        if len(audio)==1 and result.get('format',{}).get('format_name') in ('wav','flac'):
            stream=audio[0]
            if stream.get('sample_rate')=='16000' and (stream.get('codec_name','').startswith('pcm_') or stream.get('codec_name')=='flac'):
                try:
                    frames=Fraction(stream['duration_ts'])*Fraction(stream['time_base'])*16000
                    if frames.denominator==1 and frames>0:duration=int(frames)/16000
                except (KeyError,ValueError,TypeError,ZeroDivisionError):pass
        if not math.isfinite(duration) or not 0<duration<=settings.max_audio_seconds: raise ValueError('Invalid or excessive audio duration')
        if not audio: raise ValueError('File has no audio track')
        return duration
    finally: output.unlink(missing_ok=True)


def create_app(settings=None,analyzer_factory=None,*,fragment_provider_factory=None):
    settings=settings or Settings()
    from .moss_handy_jobs import ownership_fence
    ownership_fence(settings.data_dir)  # Early diagnostic only; repeated under lock.
    if settings.moss_jobs_enabled and fragment_provider_factory is not None:
        raise ValueError('MOSS jobs does not support fragment provider coexistence')
    for folder in ('uploads','tmp'):
        (settings.data_dir/folder).mkdir(parents=True,exist_ok=True)
    store=Store(settings.data_dir/'jobs.sqlite3',settings)
    from .fragment_http import FragmentHTTP, is_experimental, no_identity_mutation
    fragments=FragmentHTTP(store,settings,fragment_provider_factory,defer_factory_initialization=True)
    change_feed.initialize(store)
    worker=Worker(store,settings,analyzer_factory)
    lock=InstanceLock(settings.data_dir/'instance.lock')
    upload_lock=threading.Lock()
    jobs=None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal jobs
        registered_cells=None
        lock.acquire()
        try:
            ownership_fence(settings.data_dir)
            fragments.initialize_factory_once()
            if settings.native_owned_support_enabled:
                try:
                    from .native_owned_support_cache import register
                    worker.native_owned_support_cache=register(store)
                except Exception:
                    # Optional capture has no authority to reject primary startup.
                    worker.native_owned_support_cache=None
            if settings.native_owned_cells_enabled or os.path.lexists(settings.data_dir/'native-owned-cells'):
                try:
                    from .native_owned_cell_capture import register as register_cells
                    registered_cells=register_cells(store,fragment_provider_factory=fragment_provider_factory,instance_lock=lock)
                    worker.native_owned_cells=registered_cells
                    if worker.native_owned_cells is not None:worker.native_owned_cells.owner.start()
                except Exception:
                    app.state.native_owned_cells_status='registration_unavailable'
            if settings.moss_jobs_enabled:
                from .native_execution_permit import NativeExecutionPermit
                from .moss_handy_jobs import MossJobs
                if worker.native_owned_support_cache is None:
                    raise RuntimeError('MOSS jobs requires native support owner')
                permit=NativeExecutionPermit(settings)
                permit._configuration_current=lambda: (fragments.factory is None
                    and settings.moss_jobs_enabled and settings.native_owned_support_enabled
                    and settings.diarization_backend=='nemotron' and settings.inference_device=='cpu'
                    and settings.overlap_backend=='none' and settings.start_worker)
                settings._native_execution_permit=permit
                cell_source=None
                if settings.native_owned_cells_enabled and registered_cells is not None:
                    try:
                        from .native_owned_cell_jobs import CellJobsSource
                        cell_owner=registered_cells.owner
                        cell_source=CellJobsSource(cell_owner,store,config_allowed=lambda:
                            settings.native_owned_cells_enabled
                            and worker.native_owned_cells is registered_cells
                            and registered_cells.owner is cell_owner
                            and registered_cells.authority.owner is cell_owner
                            and permit._configuration_current())
                    except Exception:
                        # Durable cell unavailability does not disable rolling
                        # jobs or acquire any new native execution authority.
                        app.state.native_owned_cell_jobs_status='adapter_unavailable'
                jobs=MossJobs(store,worker.native_owned_support_cache,permit,cell_source=cell_source)
                app.state.moss_jobs=jobs
            from .data_paths import verify_owned_data_paths
            verify_owned_data_paths(store)
            from .lifecycle import cleanup_pending
            cleanup_pending(store)
            # Only this process owns the directory, so incomplete upload/window remnants are safe to remove.
            with os.scandir(settings.data_dir/'tmp') as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False): shutil.rmtree(entry.path)
                    else: os.unlink(entry.path)
            with store.db() as db, os.scandir(settings.data_dir/'uploads') as entries:
                for entry in entries:
                    if entry.name.endswith('.part'):
                        os.unlink(entry.path)
                    elif entry.name.endswith('.audio'):
                        path=Path(entry.path)
                        raw=str(path.resolve())
                        if not db.execute('SELECT 1 FROM chunks WHERE path=? LIMIT 1',(raw,)).fetchone() and not db.execute('SELECT 1 FROM session_file_cleanup WHERE path=? LIMIT 1',(str(path),)).fetchone(): path.unlink()
            store.recover()
            fragments.recover()
            if settings.start_worker: worker.start()
            yield
        finally:
            unwinding=sys.exc_info()[0] is not None
            if jobs is not None:
                try:jobs.close_admission()
                except Exception as exc:app.state.moss_jobs_shutdown_error=exc
            # Keep the originally registered capture alive through job/pin
            # cleanup. The fallback also covers a caller-provided capture when
            # no owner was registered at startup.
            cells=registered_cells if registered_cells is not None else worker.native_owned_cells
            if cells is not None:
                try:cells.owner.close_admission()
                except Exception as exc:
                    app.state.native_owned_cells_shutdown_error=exc
                    app.state.native_owned_cells_status='cleanup_pending'
            if worker.native_owned_support_cache is not None:
                try:worker.native_owned_support_cache.close()
                except Exception:pass
            stopped=False
            try:stopped=worker.stop()
            except Exception as exc:app.state.worker_shutdown_error=exc
            jobs_stopped=jobs is None
            if jobs is not None:
                try:jobs_stopped=jobs.stop()
                except Exception as exc:
                    if not hasattr(app.state,'moss_jobs_shutdown_error'):
                        app.state.moss_jobs_shutdown_error=exc
            cells_stopped=cells is None
            if cells is not None:
                try:
                    from .native_owned_cell_capture import unregister as unregister_cells
                    cells_stopped=unregister_cells(cells)
                except Exception as exc:
                    if not hasattr(app.state,'native_owned_cells_shutdown_error'):
                        app.state.native_owned_cells_shutdown_error=exc
                if not cells_stopped:app.state.native_owned_cells_status='cleanup_pending'
            initializer_safe=fragments.factory_initialization_state!='failed'
            if not initializer_safe:
                factory=fragments.factory
                initializer_safe=(type(factory) is _CPUFactory and 'initialize' not in vars(factory)
                    and getattr(factory.initialize,'__self__',None) is factory
                    and getattr(factory.initialize,'__func__',None) is _CPU_INITIALIZE)
            if stopped and jobs_stopped and cells_stopped and fragments.cleanup_error is None and initializer_safe:
                if jobs is not None and getattr(settings,'_native_execution_permit',None) is jobs.permit:
                    del settings._native_execution_permit
                lock.release()
            else:
                # Keep the live instance lock referenced while ownership is
                # unresolved. A subsequent process also sees the durable fence.
                app.state.shutdown_blocked=True
                if not unwinding:
                    raise RuntimeError('Shutdown blocked: provider cleanup is unresolved')

    from . import __version__
    app=FastAPI(title='Local incremental audio analysis',version=__version__,lifespan=lifespan)
    app.state.store=store; app.state.worker=worker; app.state.settings=settings; app.state.fragments=fragments
    app.state.instance_lock=lock
    app.state.moss_jobs=None
    app.state.native_owned_cells_status='disabled_absent'

    @app.middleware('http')
    async def auth(request,call_next):
        from starlette.responses import JSONResponse
        if settings.api_key:
            token=request.headers.get('authorization','')
            if not hmac.compare_digest(token,'Bearer '+settings.api_key):
                return JSONResponse({'detail':'Invalid or missing bearer token'},status_code=401)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing(request,exc):
        from starlette.responses import JSONResponse
        return JSONResponse({'detail':str(exc)},status_code=404)
    @app.exception_handler(ValueError)
    async def invalid(request,exc):
        from starlette.responses import JSONResponse
        return JSONResponse({'detail':str(exc)},status_code=409)
    @app.exception_handler(OverflowError)
    async def capacity(request,exc):
        from starlette.responses import JSONResponse
        return JSONResponse({'detail':str(exc)},status_code=429,headers={'Retry-After':'30'})

    @app.get('/health')
    def health():
        readiness={'ready':True,'status':'disabled','checks':'not_requested','errors':[]}
        if settings.overlap_backend!='none':
            from .overlap_candidates import overlap_readiness
            readiness=overlap_readiness(settings.inference_config())
        return {'status':'blocked' if fragments.cleanup_error or (jobs is not None and jobs.health()['cleanup_pending']) else 'ok',
                **({'moss_jobs':jobs.health()} if jobs is not None else {}),
                **({'native_owned_cells':worker.native_owned_cells.health()} if worker.native_owned_cells is not None
                   else ({'native_owned_cells':{'enabled':True,'status':app.state.native_owned_cells_status,'ready':False}}
                         if settings.native_owned_cells_enabled or app.state.native_owned_cells_status!='disabled_absent' else {})),
                'fragment_cleanup_pending':fragments.cleanup_error,
                'worker_alive':worker.thread.is_alive(),'active_chunk':worker.current_chunk,
                'process_tree_rss_bytes':tree_rss(),'memory_budget_bytes':settings.process_memory_limit_bytes,
                'memory_budget_scope':'RAM process tree only; VRAM requires target-hardware measurement',
                'asr_backend':settings.asr_backend,'diarization_backend':settings.diarization_backend,'overlap_backend':settings.overlap_backend,
                'overlap_status':'disabled' if settings.overlap_backend=='none' else ('ready' if readiness['ready'] else 'unavailable'),
                'overlap_experimental':settings.overlap_backend!='none','overlap_readiness':readiness,
                'inference_device':settings.inference_device,
                'speaker_policy':{'calibration_profile_id':settings.speaker_calibration_profile['profile_id'] if settings.speaker_calibration_profile else None,
                                  'single_threshold':settings.speaker_match_threshold,'verified_centroid_threshold':settings.speaker_centroid_threshold,
                                  'novelty_threshold':settings.speaker_new_threshold,'ambiguity_margin':settings.speaker_match_margin,
                                  'profile_strategy':settings.speaker_profile_strategy,'automatic_template_updates':False},
                'speaker_support_review':{'enabled':settings.speaker_support_enabled,
                    'max_records':settings.speaker_support_max_records,'total_pcm_bytes':settings.speaker_support_total_pcm_bytes,
                    'max_pcm_bytes':settings.speaker_support_max_pcm_bytes,'identity_learning_available':bool(settings.verified_identity_policy)},
                'queue_limits':{'work_units':settings.max_pending_chunks,'legacy_setting_name':'max_pending_chunks',
                                'explicit_stream_unit':'one continuous stream','independent_chunk_unit':'one chunk',
                                'pending_fragments_per_stream':128,'minimum_nonfinal_fragment_seconds':0.5,
                                'maximum_pending_receipts_for_new_work':128*settings.max_pending_chunks,
                                'failed_stream_receipts_count_toward_limit':True},
                'disk_free_bytes':shutil.disk_usage(settings.data_dir).free,
                'fragment_pcm16_v1': {'configured':fragment_provider_factory is not None,'experimental':True,'finalized_only':True,'cleanup_error':fragments.cleanup_error},
                'inference_ready': None, 'note':'Health is liveness, not model-readiness. Run model validation before ingest.'}

    @app.post('/sessions',status_code=201)
    def create_session(body:SessionRequest): return store.create_session(body.name,body.identity_group,body.identity_mode,body.processing_mode)
    @app.get('/sessions/{sid}')
    def get_session(sid:str): return store.session(sid)
    @app.get('/sessions/{sid}/chunks')
    def chunks(sid:str,after:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500)):
        store.session(sid)
        with store.db() as db:
            rows=[dict(r) for r in db.execute('SELECT rowid AS cursor,id,status,duration,timeline_start,next_offset,recorded_at,created,error FROM chunks WHERE session_id=? AND rowid>? ORDER BY rowid LIMIT ?',(sid,after,limit))]
        return {'chunks':rows,'next_cursor':rows[-1]['cursor'] if rows else after}

    @app.put('/sessions/{sid}/chunks',status_code=202)
    async def upload(sid:str,request:Request,
                     idempotency_key:Annotated[str,Header(min_length=1,max_length=128)],
                     timeline_start:float | None=Query(None,ge=0),
                     recorded_at:datetime | None=None,
                     stream_id:str | None=Query(None,min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.-]+$'),
                     stream_final:bool=Query(False)):
        """Stream raw audio bytes (not multipart). Idempotency-Key is scoped to the session."""
        experimental=is_experimental(store,sid)
        if experimental and stream_id is None:
            raise HTTPException(422,'Experimental finalized PCM16 mode requires explicit continuous stream_id')
        if stream_final and stream_id is None:
            raise HTTPException(422,'stream_final requires stream_id')
        if timeline_start is not None and not math.isfinite(timeline_start):
            raise HTTPException(422,'timeline_start must be finite')
        if not store.duplicate(sid,idempotency_key): store.capacity(sid,stream_id)
        if not upload_lock.acquire(blocking=False): raise HTTPException(429,'Another upload is active',headers={'Retry-After':'10'})
        path=settings.data_dir/'uploads'/f'{ident()}.part'; total=0; digest=hashlib.sha256(); final=None
        cell_upload=None;cell_streamed_identity=None
        try:
            try: check_resources(settings)
            except RuntimeError as e: raise HTTPException(507,str(e))
            length=request.headers.get('content-length')
            if length:
                try: declared=int(length)
                except ValueError: raise HTTPException(400,'Invalid Content-Length')
                if declared<0 or declared>settings.max_upload_bytes: raise HTTPException(413,'Upload size limit exceeded')
                try: check_resources(settings,declared)
                except RuntimeError as e: raise HTTPException(507,str(e))
            with path.open('xb') as out:
                async for data in request.stream():
                    total+=len(data)
                    if total>settings.max_upload_bytes: raise HTTPException(413,'Upload size limit exceeded')
                    try: check_resources(settings,len(data))
                    except RuntimeError as e: raise HTTPException(507,str(e))
                    digest.update(data)
                    await run_in_threadpool(out.write,data)
                await run_in_threadpool(out.flush)
                await run_in_threadpool(os.fsync,out.fileno())
                if worker.native_owned_cells is not None:
                    try:cell_streamed_identity=await run_in_threadpool(worker.native_owned_cells.streamed_descriptor,out.fileno(),digest.hexdigest(),total)
                    except Exception:pass
            if not total: raise HTTPException(400,'Empty audio upload')
            sha=digest.hexdigest(); old=store.duplicate(sid,idempotency_key)
            if old:
                if old['digest']!=sha: raise HTTPException(409,'Idempotency key already used for different bytes')
                existing=store.chunk(old['id'])
                with store.db() as db:
                    receipt=db.execute('SELECT * FROM stream_chunks WHERE chunk_id=?',(old['id'],)).fetchone()
                original_stream=receipt['stream_id'] if receipt else None
                original_final=bool(receipt['upload_stream_final']) if receipt else False
                same_recorded=(recorded_at is None and existing['recorded_at'] is None) or (
                    recorded_at is not None and existing['recorded_at'] is not None and
                    recorded_at==datetime.fromisoformat(existing['recorded_at']))
                if (original_stream!=stream_id or original_final!=stream_final or not same_recorded or
                    (timeline_start is not None and (existing['timeline_start']!=timeline_start if experimental else abs(existing['timeline_start']-timeline_start)>1e-8))):
                    raise HTTPException(409,'Idempotency key already used with different upload metadata')
                if worker.native_owned_cells is not None:
                    try:await run_in_threadpool(worker.native_owned_cells.duplicate,old['id'],sha)
                    except Exception:pass
                return {'chunk':existing,'duplicate':True}
            source=None
            try:
                if experimental:
                    from .fragment_core import inspect_pcm16_source
                    source=await run_in_threadpool(inspect_pcm16_source,'pending',path)
                    if source.format != (16000,1,2,'wav-pcm16le') or source.sha256 != sha:
                        raise ValueError('Original 16 kHz mono PCM16 WAV is required')
                    duration=source.frames/16000
                else:
                    duration=await run_in_threadpool(probe,path,settings)
            except Exception as e: raise HTTPException(422,f'Cannot validate supported audio: {e}')
            if worker.native_owned_cells is not None:
                try:cell_upload=await run_in_threadpool(worker.native_owned_cells.prepare_upload,path,sha,total,cell_streamed_identity)
                except Exception:pass
            final=path.with_suffix('.audio'); path.replace(final)
            cid,created=store.add_chunk(sid,idempotency_key,sha,final,duration,timeline_start,recorded_at.isoformat() if recorded_at else None,stream_id=stream_id,stream_final=stream_final,fragment_source=source)
            if not created: final.unlink(missing_ok=True)
            if worker.native_owned_cells is not None:
                try:await run_in_threadpool(worker.native_owned_cells.finish_upload,cell_upload,sid,cid,final,created)
                except Exception:pass
            final=None
            worker.notify()
            return {'chunk':store.chunk(cid),'duplicate':not created}
        finally:
            if cell_upload is not None:
                try:cell_upload.close()
                except Exception:pass
            path.unlink(missing_ok=True)
            if final: final.unlink(missing_ok=True)
            upload_lock.release()

    @app.post('/sessions/{sid}/streams/{stream_id}/finish')
    def finish(sid:str,stream_id:str):
        result=fragments.finish(sid,stream_id) if is_experimental(store,sid) else finish_stream(store,sid,stream_id)
        worker.notify()
        return result

    @app.get('/sessions/{sid}/changes')
    def changes(sid:str,after:int=Query(0,ge=0),limit:int=Query(500,ge=1,le=1000)):
        return change_feed.changes(store,sid,after,limit)

    @app.get('/chunks/{cid}')
    def get_chunk(cid:str): return store.chunk(cid)
    @app.post('/chunks/{cid}/{action}')
    def control(cid:str,action:str):
        if action not in ('pause','resume','retry','cancel'): raise HTTPException(404,'Unknown action')
        store.set_state(cid,action); worker.notify()
        return store.chunk(cid)
    @app.get('/sessions/{sid}/segments')
    def segments(sid:str,after:int=Query(0,ge=0),limit:int=Query(500,ge=1,le=1000)):
        return store.results(sid,after,limit)
    @app.get('/sessions/{sid}/events')
    def events(sid:str,after:int=Query(0,ge=0),limit:int=Query(500,ge=1,le=1000)):
        return store.events(sid,after,limit)
    @app.get('/sessions/{sid}/event-candidates')
    def event_candidates(sid:str,after:int=Query(0,ge=0),limit:int=Query(500,ge=1,le=500)):
        if is_experimental(store,sid):
            raise HTTPException(409,'Event candidate public projection is unsupported in fragment_pcm16_v1; candidate evidence is retained privately')
        return store.event_candidates(sid,after,limit)
    @app.get('/sessions/{sid}/overlap-candidates')
    def overlap_candidates(sid:str,after:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500)):
        if is_experimental(store,sid):
            raise HTTPException(409,'Overlap text candidate projection is unsupported in fragment_pcm16_v1')
        return store.overlap_candidates(sid,after,limit)
    @app.get('/sessions/{sid}/speaker-turns')
    def speaker_turns(sid:str,after:int=Query(0,ge=0),limit:int=Query(500,ge=1,le=1000)):
        return store.speaker_turns(sid,after,limit)
    @app.post('/sessions/{sid}/speakers',status_code=201)
    def enroll(sid:str,body:EnrollmentRequest):
        no_identity_mutation(store,sid)
        if not body.name.strip(): raise HTTPException(422,'Name must not be blank')
        return store.enroll_speaker(sid,body.name.strip(),body.source_segment_id)

    @app.post('/sessions/{sid}/speakers/{pid}/examples',status_code=201)
    def add_speaker_example(sid:str,pid:str,body:SpeakerExampleRequest):
        no_identity_mutation(store,sid)
        return store.add_speaker_example(sid,pid,body.source_segment_id)

    @app.get('/sessions/{sid}/speakers')
    def speakers(sid:str): return {'speakers':store.speakers(sid)}
    @app.patch('/sessions/{sid}/speakers/{pid}')
    def correction(sid:str,pid:str,body:SpeakerRequest):
        no_identity_mutation(store,sid)
        if sum((body.name is not None,body.merge_into is not None,body.undo_merge))!=1: raise HTTPException(422,'Provide one of name, merge_into, undo_merge')
        store.correct_speaker(sid,pid,body.name,body.merge_into,body.undo_merge)
        return {'speakers':store.speakers(sid)}
    @app.patch('/sessions/{sid}/segments/{segment_id}')
    def assign(sid:str,segment_id:int,body:SegmentRequest):
        no_identity_mutation(store,sid)
        store.assign_segment(sid,segment_id,body.speaker_id)
        return {'updated':segment_id}
    @app.get('/sessions/{sid}/segments/{segment_id}/speaker-support')
    def segment_support(sid:str,segment_id:int):
        from . import speaker_support
        from starlette.responses import JSONResponse
        return JSONResponse(speaker_support.metadata(store,sid,segment_id=segment_id),headers={'Cache-Control':'private, no-store'})

    @app.get('/sessions/{sid}/speaker-turns/{turn_id}/speaker-support')
    def turn_support(sid:str,turn_id:int):
        from . import speaker_support
        from starlette.responses import JSONResponse
        return JSONResponse(speaker_support.metadata(store,sid,turn_id=turn_id),headers={'Cache-Control':'private, no-store'})

    @app.get('/sessions/{sid}/speaker-support/{support_id}')
    def review_support(sid:str,support_id:str):
        from . import speaker_support
        from starlette.responses import JSONResponse
        return JSONResponse(speaker_support.metadata(store,sid,support_id),headers={'Cache-Control':'private, no-store'})

    @app.get('/sessions/{sid}/speaker-support/{support_id}/audio')
    def review_support_audio(sid:str,support_id:str):
        from . import speaker_support
        from starlette.responses import Response
        return Response(speaker_support.audio(store,sid,support_id),media_type='audio/wav',
                        headers={'Cache-Control':'private, no-store','Content-Disposition':'inline; filename="speaker-support.wav"'})

    def private_json(value):
        from starlette.responses import JSONResponse
        return JSONResponse(value,headers={'Cache-Control':'private, no-store'})

    def owned_support_response(request,sid,kind,cid=None):
        from starlette.responses import JSONResponse,Response,StreamingResponse
        headers={'Cache-Control':'private, no-store'}
        if not settings.native_owned_support_enabled:
            return JSONResponse({'detail':'native_owned_support_disabled'},status_code=409,headers=headers)
        from .native_owned_support_cache import parse_query,ReadError
        cache=worker.native_owned_support_cache
        if cache is None:
            return JSONResponse({'detail':'native_owned_support_unavailable'},status_code=503,headers=headers)
        result=None
        try:
            query=parse_query(request.scope.get('query_string',b''),kind)
            if kind=='list':result=cache.read_list(sid,**query)
            elif kind=='detail':result=cache.read_detail(sid,cid,**query)
            else:result=cache.read_audio(sid,cid,**query)
            done=request.scope.get('_native_support_read_done')
            if done is not None and done.is_set():
                result.lease.release()
                result=None
                return JSONResponse({'detail':'native_owned_support_read_closed'},status_code=503,headers=headers)
            if kind=='audio':
                # Header and retained immutable PCM are sent separately. A second
                # complete WAV copy would exceed the response's byte reservation.
                chunks=result.body
                async def audio_chunks():
                    for block in chunks:yield block
                response=StreamingResponse(audio_chunks(),status_code=result.status_code,media_type=result.media_type,
                    headers={**headers,'Content-Length':str(sum(map(len,chunks)))})
            else:
                response=Response(result.body,status_code=result.status_code,media_type=result.media_type,headers=headers)
            if not _assign_native_read_lease(request.scope,result.lease):
                result=None;response=None
                return JSONResponse({'detail':'native_owned_support_read_closed'},status_code=503,headers=headers)
            return response
        except ReadError as exc:
            if result is not None:result.lease.release()
            return JSONResponse({'detail':exc.reason},status_code=exc.status_code,headers=headers)
        except Exception:
            if result is not None:result.lease.release()
            return JSONResponse({'detail':'native_owned_support_unavailable'},status_code=503,headers=headers)

    @app.get('/sessions/{sid}/native-owned-supports')
    def owned_supports(request:Request,sid:str):
        return owned_support_response(request,sid,'list')

    @app.get('/sessions/{sid}/native-owned-supports/{cid}')
    def owned_support(request:Request,sid:str,cid:str):
        return owned_support_response(request,sid,'detail',cid)

    @app.get('/sessions/{sid}/native-owned-supports/{cid}/audio')
    def owned_support_audio(request:Request,sid:str,cid:str):
        return owned_support_response(request,sid,'audio',cid)

    @app.get('/sessions/{sid}/identity-bank')
    def identity_bank(sid:str):
        from .verified_identity import bank
        return private_json(bank(store,sid))

    @app.get('/sessions/{sid}/identity-reviews')
    def identity_reviews(sid:str,after:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500)):
        from .verified_identity import events
        return private_json(events(store,sid,after,limit))

    @app.get('/sessions/{sid}/identity-reviews/{event_id}')
    def identity_review_detail(sid:str,event_id:str):
        from .verified_identity import event_detail
        return private_json(event_detail(store,sid,event_id))

    @app.post('/sessions/{sid}/identity-reviews')
    def add_identity_review(sid:str,body:IdentityReviewRequest):
        no_identity_mutation(store,sid)
        from .verified_identity import mutate
        return private_json(mutate(store,sid,body.model_dump(),operator_context='bearer_authenticated_local_api' if settings.api_key else 'unauthenticated_local_api'))

    @app.post('/sessions/{sid}/identity-reviews/{event_id}/replace')
    def replace_identity_review(sid:str,event_id:str,body:IdentityReviewRequest):
        no_identity_mutation(store,sid)
        from .verified_identity import mutate
        return private_json(mutate(store,sid,body.model_dump(),previous=event_id,operator_context='bearer_authenticated_local_api' if settings.api_key else 'unauthenticated_local_api'))

    @app.post('/sessions/{sid}/identity-reviews/{event_id}/retract')
    def retract_identity_review(sid:str,event_id:str,body:ReviewMutation):
        no_identity_mutation(store,sid)
        from .verified_identity import mutate
        return private_json(mutate(store,sid,body.model_dump(),previous=event_id,retract=True,operator_context='bearer_authenticated_local_api' if settings.api_key else 'unauthenticated_local_api'))

    @app.get('/sessions/{sid}/identity-decisions/{decision_id}')
    def identity_decision(sid:str,decision_id:str):
        from .verified_identity import decision
        return private_json(decision(store,sid,decision_id))

    @app.get('/sessions/{sid}/identity-relations')
    def identity_relations(sid:str,after:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500)):
        from .identity_canonical import events
        return private_json(events(store,sid,after,limit))

    @app.post('/sessions/{sid}/identity-relations')
    def add_identity_relation(sid:str,body:IdentityRelationRequest):
        no_identity_mutation(store,sid)
        from .identity_canonical import mutate
        return private_json(mutate(store,sid,body.model_dump(),operator_context='bearer_authenticated_local_api' if settings.api_key else 'unauthenticated_local_api'))

    @app.post('/sessions/{sid}/identity-relations/{event_id}/retract')
    def retract_identity_relation(sid:str,event_id:str,body:ReviewMutation):
        no_identity_mutation(store,sid)
        from .identity_canonical import mutate
        return private_json(mutate(store,sid,body.model_dump(),previous=event_id,operator_context='bearer_authenticated_local_api' if settings.api_key else 'unauthenticated_local_api'))

    @app.delete('/identity-groups/{group}/speakers')
    def reset_speakers(group:str):
        if worker.current_chunk or upload_lock.locked(): raise HTTPException(409,'Wait for worker and upload to become idle')
        from .identity_reset import reset_group
        return reset_group(store,group)

    @app.delete('/sessions/{sid}')
    def delete(sid:str):
        if fragments.active_sid==sid:
            raise HTTPException(409,'Wait until the fragment worker releases this session after pause/cancel')
        if worker.current_chunk:
            with store.db() as db:
                active=db.execute('SELECT session_id FROM chunks WHERE id=?',(worker.current_chunk,)).fetchone()
            if active and active[0]==sid: raise HTTPException(409,'Wait until worker releases this session after pause/cancel')
        if upload_lock.locked(): raise HTTPException(409,'Wait for upload to finish before deleting sessions')
        result=store.delete_session(sid)
        return {**result,'note':('Source cleanup is pending; retry this DELETE or restart after resolving file access.' if result['cleanup_pending'] else 'Application deletion is not forensic secure erasure; protect disk and backups.')}
    # Added last so this lifetime gate encloses the auth/streaming middleware.
    from .moss_handy_http import register_routes
    register_routes(app, lambda:jobs, _assign_native_read_lease)
    app.add_middleware(_NativeSupportReadLifetime)
    return app

# Importing this module has no filesystem side effects.
# Launch with python -m audio_backend, or uvicorn audio_backend.app:create_app --factory.
