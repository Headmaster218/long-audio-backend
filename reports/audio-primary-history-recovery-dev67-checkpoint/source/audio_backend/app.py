from contextlib import asynccontextmanager
from datetime import datetime
from fractions import Fraction
import asyncio
import errno
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
from starlette.responses import JSONResponse
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
        private=scope.get('type')=='http' and any(part in scope.get('path','') for part in ('/native-owned-supports','/native-owned-cells','/moss-jobs','/moss-automatic','/transcript','/identity-groups/','/primary-observations'))
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
class RecordingFinishRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    expected_revision: int=Field(ge=0)
    idempotency_key: str=Field(min_length=1,max_length=128)

class RecordingPageRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    page_number: int=Field(ge=1,le=1024)
    capture_origin_frame: int | None=Field(default=None,ge=0,le=(1<<53)-1)

class RecordingSealRequest(RecordingFinishRequest):
    end_frame: int=Field(ge=0,le=(1<<53)-1)
    eos: bool=False

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


# A failed inherited-owner shutdown cannot lose the last child fd through GC.
_INHERITED_APPS_PENDING_CLEANUP = []


def create_app(settings=None,analyzer_factory=None,*,fragment_provider_factory=None,instance_lock_fd=None,
               native_root_reattestation=None,native_root_reattestation_sha256=None):
    """Optional explicit prelocked handoff, before any Store/filesystem setup.

    In inherited mode the descriptor is consumed. The supervisor must retain
    its own duplicate until its original child handle has positively waited.
    The default factory/lifespan timing is unchanged.
    """
    lock=None
    try:
        if instance_lock_fd is not None:
            data_dir=Path(settings.data_dir) if settings is not None else Path(os.getenv('AUDIO_DATA_DIR','./audio-data'))
            lock=InstanceLock(data_dir/'instance.lock')
            lock.adopt(instance_lock_fd)
        settings=settings or Settings()
        if lock is not None: lock.verify_inherited(settings.data_dir/'instance.lock')
        if (native_root_reattestation is None) != (native_root_reattestation_sha256 is None):
            raise ValueError('Root re-attestation requires both artifact path and exact SHA256')
        admission=None
        if native_root_reattestation is not None:
            if fragment_provider_factory is not None:
                raise ValueError('Root re-attestation excludes fragment providers')
            from .native_root_reattestation import load
            admission=load(native_root_reattestation,native_root_reattestation_sha256,settings,lock)
        return _create_app(settings,analyzer_factory,
            fragment_provider_factory=fragment_provider_factory,inherited_lock=lock,
            root_reattestation=admission)
    except BaseException:
        if lock is not None: lock.release()
        raise


def _create_app(settings,analyzer_factory=None,*,fragment_provider_factory=None,inherited_lock=None,
                root_reattestation=None):
    settings=settings or Settings()
    from .moss_handy_jobs import ownership_fence
    ownership_fence(settings.data_dir)  # Early diagnostic only; repeated under lock.
    from .settings import coordinator_enabled, primary_only_enabled, primary_only_configuration
    primary_only = primary_only_enabled(settings)
    if coordinator_enabled(settings) and fragment_provider_factory is not None:
        raise ValueError('Native job coordinator does not support fragment provider coexistence')
    if root_reattestation is not None:
        # Repeat the read-only anchors immediately before the first mutation.
        root_reattestation._recheck(inherited_lock)
    for folder in ('uploads','tmp'):
        (settings.data_dir/folder).mkdir(parents=True,exist_ok=True)
    store=Store(settings.data_dir/'jobs.sqlite3',settings)
    from .moss_result_store import MossResultStore
    from .native_owned_support_cache import ReadResponseBudget, lookup as lookup_support_cache
    results=MossResultStore(store)
    from .moss_automatic import Automatic
    automatic=Automatic(store,results)
    store.moss_automatic=automatic
    response_budget=ReadResponseBudget()
    store._reviewed_person_response_budget=response_budget
    def history_budget():
        registered=lookup_support_cache(store)
        return registered.response_budget if registered is not None else response_budget
    from .fragment_http import FragmentHTTP, is_experimental, no_identity_mutation
    fragments=FragmentHTTP(store,settings,fragment_provider_factory,defer_factory_initialization=True)
    change_feed.initialize(store)
    worker=Worker(store,settings,analyzer_factory)
    worker.moss_automatic=automatic
    lock=inherited_lock or InstanceLock(settings.data_dir/'instance.lock')
    upload_lock=threading.Lock()
    from .recording_overlap import RecordingOverlap, RecordingStorageCapacityError
    recordings=RecordingOverlap(store)
    worker.recordings=recordings
    jobs=None
    speaker_links=None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal jobs, speaker_links
        registered_cells=None
        app.state.instance_lock_lifespan_started=True
        lock.acquire()
        if lock._inherited and app not in _INHERITED_APPS_PENDING_CLEANUP:
            _INHERITED_APPS_PENDING_CLEANUP.append(app)
        try:
            ownership_fence(settings.data_dir)
            try:
                results.bind_recovery(lock)
                results.reconcile()
            except Exception:
                app.state.moss_result_recovery_status='unavailable'
            if settings.recording_speaker_links_enabled:
                try:
                    from .recording_speaker_link_store import RecordingSpeakerLinkStore
                    from .recording_speaker_links import reconcile_batches
                    speaker_links=RecordingSpeakerLinkStore(store)
                    speaker_links.bind_recovery(lock)
                    speaker_links.reconcile(recompute=reconcile_batches)
                except Exception:
                    # Optional annotations never prevent primary/text startup.
                    speaker_links=None
                    app.state.recording_speaker_links_status='probe_unavailable'
            if speaker_links is not None and speaker_links.history is not None:
                try:
                    speaker_links.history.bind_recovery(lock)
                    speaker_links.history.reconcile_primary()
                except Exception:
                    # Keep optional history admission closed. A pending storage
                    # acknowledgement is retried by ordinary maintenance.
                    pass
            fragments.initialize_factory_once()
            if settings.native_owned_support_enabled:
                try:
                    from .native_owned_support_cache import register
                    worker.native_owned_support_cache=register(store, response_budget=response_budget)
                except Exception:
                    # Optional capture has no authority to reject primary startup.
                    worker.native_owned_support_cache=None
            if settings.native_owned_cells_enabled or os.path.lexists(settings.data_dir/'native-owned-cells'):
                try:
                    from .native_owned_cell_capture import register as register_cells
                    registered_cells=register_cells(store,fragment_provider_factory=fragment_provider_factory,instance_lock=lock,
                        root_reattestation=root_reattestation)
                    worker.native_owned_cells=registered_cells
                    if registered_cells is not None and settings.native_owned_compact_gc_enabled:
                        registered_cells.bind_retention_worker(worker)
                    if worker.native_owned_cells is not None:worker.native_owned_cells.owner.start()
                except Exception:
                    app.state.native_owned_cells_status='registration_unavailable'
            if coordinator_enabled(settings):
                from .native_execution_permit import NativeExecutionPermit
                from .moss_handy_jobs import MossJobs
                if worker.native_owned_support_cache is None:
                    raise RuntimeError('MOSS jobs requires native support owner')
                permit=NativeExecutionPermit(settings)
                permit._configuration_current=lambda: (fragments.factory is None
                    and settings.moss_jobs_enabled and settings.native_owned_support_enabled
                    and settings.diarization_backend=='nemotron' and settings.inference_device=='cpu'
                    and settings.overlap_backend=='none' and settings.start_worker)
                if primary_only:
                    startup_configuration=primary_only_configuration(settings)
                    support_owner=worker.native_owned_support_cache
                    startup_capture=registered_cells
                    startup_owner=registered_cells.owner if registered_cells is not None else None
                    startup_authority=registered_cells.authority if registered_cells is not None else None
                    permit._configuration_current=lambda: (primary_only_enabled(settings)
                        and primary_only_configuration(settings)==startup_configuration
                        and fragment_provider_factory is None and fragments.factory is None
                        and settings._native_execution_permit is permit
                        and worker.native_owned_support_cache is support_owner
                        and worker.native_owned_cells is startup_capture
                        and startup_capture is not None and startup_capture.owner is startup_owner
                        and startup_capture.authority is startup_authority
                        and startup_authority.owner is startup_owner)
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
                            and (not primary_only or permit._configuration_current())
                            and registered_cells.authority.owner is cell_owner
                            and (not primary_only or cell_source is not None and cell_source.owner is cell_owner)
                            and permit._configuration_current())
                    except Exception:
                        # Durable cell unavailability does not disable rolling
                        # jobs or acquire any new native execution authority.
                        app.state.native_owned_cell_jobs_status='adapter_unavailable'
                jobs=MossJobs(store,worker.native_owned_support_cache,permit,cell_source=cell_source,result_store=results,speaker_links=speaker_links)
                app.state.moss_jobs=jobs
                automatic.capture=registered_cells; automatic.jobs=jobs; jobs.automatic=automatic
                if (settings.moss_automatic_enabled or primary_only) and (registered_cells is None or cell_source is None):
                    raise RuntimeError('Automatic MOSS requires qualified durable cells')
            from .data_paths import verify_owned_data_paths
            verify_owned_data_paths(store)
            from .recording_overlap import verify_paths as verify_recording_paths
            verify_recording_paths(store)
            from .lifecycle import cleanup_pending
            cleanup_pending(store)
            # Only this process owns the directory, so incomplete upload/window remnants are safe to remove.
            with os.scandir(settings.data_dir/'tmp') as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False): shutil.rmtree(entry.path)
                    else: os.unlink(entry.path)
            with store.db() as db, os.scandir(settings.data_dir/'uploads') as entries:
                for entry in entries:
                    # Page-owned originals and orphan staging remain quota-charged.
                    # Only exact-page retry or journalled session erasure may remove them.
                    if entry.name.startswith('ro-') and '-page-' in entry.name:
                        continue
                    if entry.name.endswith('.part'):
                        os.unlink(entry.path)
                    elif entry.name.endswith('.audio'):
                        path=Path(entry.path)
                        raw=str(path.resolve())
                        if not db.execute('SELECT 1 FROM chunks WHERE path=? LIMIT 1',(raw,)).fetchone() and not db.execute('SELECT 1 FROM session_file_cleanup WHERE path=? LIMIT 1',(str(path),)).fetchone() and not db.execute('SELECT 1 FROM recording_sources WHERE path=? LIMIT 1',(raw,)).fetchone(): path.unlink()
            store.recover()
            fragments.recover()
            if settings.start_worker: worker.start()
            yield
        finally:
            unwinding=sys.exc_info()[0] is not None
            if speaker_links is not None and speaker_links.history is not None:
                speaker_links.history.revoke_recovery()
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
                results.revoke_recovery()
                if speaker_links is not None:speaker_links.revoke_recovery()
                lock.release()
                app.state.instance_lock_cleanup_complete=True
                if lock._inherited: _INHERITED_APPS_PENDING_CLEANUP.remove(app)
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
    app.state.instance_lock_lifespan_started=False
    app.state.instance_lock_cleanup_complete=False
    app.state.recordings=recordings
    app.state.moss_jobs=None
    app.state.moss_automatic=automatic
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
    @app.exception_handler(RecordingStorageCapacityError)
    async def recording_storage_capacity(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=507)

    @app.get('/health')
    def health():
        readiness={'ready':True,'status':'disabled','checks':'not_requested','errors':[]}
        if settings.overlap_backend!='none':
            from .overlap_candidates import overlap_readiness
            readiness=overlap_readiness(settings.inference_config())
        return {'status':'blocked' if fragments.cleanup_error or (jobs is not None and jobs.health()['cleanup_pending']) else 'ok',
                **({'moss_jobs':jobs.health()} if jobs is not None else {}),
                **({'moss_automatic':automatic.health()} if settings.moss_automatic_enabled else {}),
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

    @app.post('/sessions/{sid}/recordings/{capture_id}/pages',status_code=202)
    def recording_page_create(sid:str,capture_id:str,body:RecordingPageRequest):
        if not upload_lock.acquire(blocking=False):
            raise HTTPException(429,'Another upload or recording finalization is active')
        try:return recordings.create_page(sid,capture_id,body.page_number,body.capture_origin_frame)
        except RuntimeError as exc:raise HTTPException(507,str(exc))
        except OSError as exc:
            if exc.errno in (errno.ENOSPC,errno.EDQUOT):raise HTTPException(507,str(exc)) from exc
            raise
        finally:upload_lock.release()

    @app.get('/sessions/{sid}/recordings/{capture_id}/pages')
    def recording_page_list(sid:str,capture_id:str,after:int=Query(0,ge=0,le=1024),limit:int=Query(32,ge=1,le=64)):
        return JSONResponse(recordings.list_pages(sid,capture_id,after,limit),headers={'Cache-Control':'private, no-store'})

    @app.get('/sessions/{sid}/recordings/{capture_id}/pages/{page_number}')
    def recording_page_status(sid:str,capture_id:str,page_number:int):
        return JSONResponse(recordings.page_status(sid,capture_id,page_number),headers={'Cache-Control':'private, no-store'})

    @app.put('/sessions/{sid}/recordings/{capture_id}/pages/{page_number}/sources',status_code=202)
    async def recording_page_source(sid:str,capture_id:str,page_number:int,request:Request,
            idempotency_key:Annotated[str,Header(min_length=1,max_length=128)],
            source_start_frame:int | None=Query(None,ge=0,le=(1<<53)-1)):
        from .recording_overlap import MAX_SOURCE_BYTES,BLOCK_FRAMES
        if not upload_lock.acquire(blocking=False):
            raise HTTPException(429,'Another upload or recording finalization is active')
        path=None;reservation=None
        try:
            with store.db() as db:
                recordings._session(db,sid);batch=recordings._batch(db,sid,capture_id)
                page=recordings._page(db,batch['id'],page_number)
            path=recordings.source_path(batch,page)
            maximum=min(MAX_SOURCE_BYTES,settings.max_upload_bytes)
            length=request.headers.get('content-length')
            if length is not None:
                try:declared=int(length)
                except ValueError:raise HTTPException(400,'Invalid Content-Length')
                if not 0<declared<=maximum:raise HTTPException(413,'Recording source size limit exceeded')
            else:declared=maximum
            reservation=recordings.reserve_page_bytes(batch,page,declared)
            total=0
            with path.open('xb') as out:
                async for data in request.stream():
                    total+=len(data)
                    if total>declared:raise HTTPException(413,'Recording upload exceeds reserved size')
                    for start in range(0,len(data),BLOCK_FRAMES*2):
                        block=memoryview(data)[start:start+BLOCK_FRAMES*2]
                        recordings._page_resources(len(block))
                        await run_in_threadpool(out.write,block)
                if length is not None and total!=declared:raise HTTPException(400,'Recording Content-Length mismatch')
                await run_in_threadpool(out.flush)
                await run_in_threadpool(os.fsync,out.fileno())
            receipt=await run_in_threadpool(recordings.accept_page,sid,capture_id,page_number,idempotency_key,path,source_start_frame)
            return {**recordings.page_status(sid,capture_id,page_number),**receipt}
        except RuntimeError as exc:raise HTTPException(507,str(exc))
        except OSError as exc:
            if exc.errno in (errno.ENOSPC,errno.EDQUOT):raise HTTPException(507,str(exc)) from exc
            raise
        finally:
            if path is not None:path.unlink(missing_ok=True)
            if reservation is not None:recordings.release_reservation(reservation)
            upload_lock.release()

    @app.post('/sessions/{sid}/recordings/{capture_id}/pages/{page_number}/seal',status_code=202)
    async def recording_page_seal(sid:str,capture_id:str,page_number:int,body:RecordingSealRequest):
        if not upload_lock.acquire(blocking=False):
            raise HTTPException(429,'Another upload or recording finalization is active')
        try:
            result=await run_in_threadpool(recordings.seal_page,sid,capture_id,page_number,
                body.idempotency_key,body.expected_revision,body.end_frame,body.eos,worker.native_owned_cells)
            if result['state'] in ('published','pending_receipts'):worker.notify()
            return result
        except RuntimeError as exc:raise HTTPException(507,str(exc))
        except OSError as exc:
            if exc.errno in (errno.ENOSPC,errno.EDQUOT):raise HTTPException(507,str(exc)) from exc
            raise
        finally:upload_lock.release()

    @app.put('/sessions/{sid}/recordings/{capture_id}/sources',status_code=202)
    async def recording_source(sid:str,capture_id:str,request:Request,
            idempotency_key:Annotated[str,Header(min_length=1,max_length=128)],
            source_start_frame:int | None=Query(None,ge=0,le=(1<<53)-1)):
        """Original PCM16 mono16k WAV body; capture_id declares shared sample coordinates."""
        from .recording_overlap import MAX_SOURCE_BYTES, BLOCK_FRAMES
        if not upload_lock.acquire(blocking=False):
            raise HTTPException(429,'Another upload or recording finalization is active')
        path=None
        try:
            batch=recordings.begin(sid,capture_id)
            path=recordings.source_path(batch)
            maximum=min(MAX_SOURCE_BYTES,settings.max_upload_bytes)
            length=request.headers.get('content-length')
            if length is not None:
                try:declared=int(length)
                except ValueError:raise HTTPException(400,'Invalid Content-Length')
                if not 0<declared<=maximum:raise HTTPException(413,'Recording source size limit exceeded')
                recordings.admit_bytes(batch['id'],declared)
            total=0
            with path.open('xb') as out:
                async for data in request.stream():
                    total+=len(data)
                    if total>maximum:raise HTTPException(413,'Recording source size limit exceeded')
                    recordings.admit_bytes(batch['id'],len(data))
                    # ASGI may supply a whole request block; copies and writes
                    # remain bounded independently of the original WAV length.
                    for start in range(0,len(data),BLOCK_FRAMES*2):
                        await run_in_threadpool(out.write,memoryview(data)[start:start+BLOCK_FRAMES*2])
                await run_in_threadpool(out.flush)
                await run_in_threadpool(os.fsync,out.fileno())
            receipt=await run_in_threadpool(recordings.accept,sid,capture_id,idempotency_key,path,source_start_frame)
            return {**recordings.status(sid,capture_id),**receipt}
        except RuntimeError as exc:
            raise HTTPException(507,str(exc))
        finally:
            if path is not None:path.unlink(missing_ok=True)
            upload_lock.release()

    @app.get('/sessions/{sid}/recordings/{capture_id}')
    def recording_status(sid:str,capture_id:str):
        from starlette.responses import JSONResponse
        return JSONResponse(recordings.status(sid,capture_id),headers={'Cache-Control':'private, no-store'})

    @app.post('/sessions/{sid}/recordings/{capture_id}/cancel')
    def recording_cancel(sid:str,capture_id:str):
        result=recordings.cancel(sid,capture_id)
        worker.notify()
        return result

    @app.post('/sessions/{sid}/recordings/{capture_id}/finish',status_code=202)
    async def recording_finish(sid:str,capture_id:str,body:RecordingFinishRequest):
        if not upload_lock.acquire(blocking=False):
            raise HTTPException(429,'Another upload or recording finalization is active')
        try:
            result=await run_in_threadpool(recordings.finish,sid,capture_id,body.idempotency_key,
                                           body.expected_revision,worker.native_owned_cells)
            if result['state'] in ('queued','pending_receipts'):worker.notify()
            return result
        except RuntimeError as exc:
            raise HTTPException(507,str(exc))
        finally:upload_lock.release()

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
    app.state.moss_result_store=results
    app.state.moss_response_budget=response_budget
    register_routes(app, lambda:None if primary_only else jobs, _assign_native_read_lease, result_store=results, get_response_budget=history_budget, automatic=automatic)
    from .transcript_http import register_routes as register_transcript_routes
    register_transcript_routes(app, store, _assign_native_read_lease, history_budget)
    from .reviewed_primary_persons_http import register_routes as register_reviewed_person_routes
    register_reviewed_person_routes(app, store, _assign_native_read_lease, history_budget)
    app.add_middleware(_NativeSupportReadLifetime)
    return app

# Importing this module has no filesystem side effects.
# Launch with python -m audio_backend, or uvicorn audio_backend.app:create_app --factory.
