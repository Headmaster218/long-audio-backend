"""Strict private model-free routes, using the existing response-lifetime lease."""
import json
import asyncio
import re
from starlette.responses import JSONResponse, Response
from fastapi import Request
from .moss_handy_http import PRIVATE
from .native_owned_support_cache import ReadError
from . import reviewed_primary_persons as core


def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result:core.fail('duplicate_json_field')
        result[key]=value
    return result


def _query(raw,fields,*,integer_after=False):
    if type(raw) is not bytes or len(raw)>4096:core.fail('invalid_query')
    try:text=raw.decode('ascii')
    except UnicodeError:core.fail('invalid_query')
    result={}
    for pair in text.split('&') if text else []:
        if pair.count('=')!=1:core.fail('invalid_query')
        key,value=pair.split('=')
        if key not in fields or key in result:core.fail('invalid_query')
        if key=='limit':
            if not re.fullmatch(r'[1-9][0-9]?',value):core.fail('invalid_query')
            value=int(value);core._page_limit(value)
        elif key=='include_unavailable':
            if value not in ('true','false'):core.fail('invalid_query')
            value=value=='true'
        elif key=='after' and integer_after:
            if not re.fullmatch(r'(0|[1-9][0-9]{0,18})',value):core.fail('invalid_query')
            value=int(value)
        elif not re.fullmatch(r'[A-Za-z0-9_-]{1,2048}',value):core.fail('invalid_query')
        result[key]=value
    return result


def register_routes(app,store,assign_lease,get_response_budget):
    async def invoke(request,fn,*args,fields=(),body=False):
        lease=None
        operation=None
        submitted=False
        try:
            core._check_enabled(store)
            operation=get_response_budget().reserve_bridge_operation()
            query=_query(request.scope.get('query_string',b''),set(fields),integer_after=fn is core.relations)
            lease=get_response_budget().reserve_jobs_response('result')
            if not assign_lease(request.scope,lease):core.fail('response_closed',503)
            if body:
                if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':core.fail('json_required')
                raw=bytearray()
                async for chunk in request.stream():
                    if len(raw)+len(chunk)>core.MAX_REQUEST:core.fail('request_capacity',429)
                    raw.extend(chunk)
                try:
                    value=json.loads(raw,object_pairs_hook=_object,
                        parse_constant=lambda _:core.fail('invalid_json'))
                except (ValueError,UnicodeError,RecursionError):core.fail('invalid_json')
                if type(value) is not dict:core.fail('invalid_request_fields')
                args=(*args,value)
            done=request.scope.get('_native_support_read_done')
            def execute():
                cancel_token=core.CONTEXT.set(done)
                operation_token=core.OPERATION.set(operation)
                try:
                    result=fn(store,*args,**query)
                    if done is not None and done.is_set():core.fail('response_closed',503)
                    return core._encode(result,core.PAGE_BYTES)
                finally:
                    core.OPERATION.reset(operation_token)
                    core.CONTEXT.reset(cancel_token)
                    operation.release()
            # Shield the submitted future, not the request. Request cancellation
            # may return immediately, but cannot cancel a queued/running worker
            # or release its reservation before actual serialization completes.
            future=asyncio.get_running_loop().run_in_executor(None,execute)
            submitted=True
            def consume_completion(completed):
                if not completed.cancelled():completed.exception()
            future.add_done_callback(consume_completion)
            encoded=await asyncio.shield(future)
            return Response(encoded,media_type='application/json',headers=PRIVATE)
        except (core.BridgeError,ReadError) as exc:
            if lease is not None:lease.release()
            return JSONResponse({'detail':exc.reason},status_code=exc.status_code,headers=PRIVATE)
        except KeyError:
            if lease is not None:lease.release()
            return JSONResponse({'detail':'not_found'},status_code=404,headers=PRIVATE)
        except OverflowError:
            if lease is not None:lease.release()
            return JSONResponse({'detail':'review_capacity'},status_code=429,headers=PRIVATE)
        except ValueError as exc:
            if lease is not None:lease.release()
            known={'stale_generation','stale_profile_revision','idempotency_conflict',
                'canonical_conflicting_people','duplicate_different_people_constraint',
                'duplicate_or_conflicting_positive_evidence','canonical_contradictory_review'}
            return JSONResponse({'detail':str(exc) if str(exc) in known else 'invalid_identity_request'},status_code=409,headers=PRIVATE)
        except Exception:
            if lease is not None:lease.release()
            return JSONResponse({'detail':'reviewed_person_unavailable'},status_code=503,headers=PRIVATE)
        finally:
            if operation is not None and not submitted:operation.release()

    @app.put('/identity-groups/{group}/reviewed-person-bank')
    async def activate(request:Request,group:str):
        return await invoke(request,core.activate_bank,group,body=True)

    @app.get('/identity-groups/{group}/reviewed-person-bank')
    async def bank(request:Request,group:str):
        return await invoke(request,core.bank,group)

    @app.get('/sessions/{sid}/recordings/{rid}/primary-observations')
    async def observations(request:Request,sid:str,rid:str):
        return await invoke(request,core.observations,sid,rid,fields=('after','limit'))

    @app.get('/sessions/{sid}/recordings/{rid}/primary-observations/{observation_id}')
    async def observation(request:Request,sid:str,rid:str,observation_id:str):
        return await invoke(request,core.observation,sid,rid,observation_id)

    @app.post('/identity-groups/{group}/primary-associations')
    async def associate(request:Request,group:str):
        return await invoke(request,core.mutate,group,body=True)

    @app.post('/identity-groups/{group}/primary-associations/{event_id}/replace')
    async def replace(request:Request,group:str,event_id:str):
        return await invoke(request,lambda st,g,r:core.mutate(st,g,r,previous=event_id),group,body=True)

    @app.post('/identity-groups/{group}/primary-associations/{event_id}/retract')
    async def retract(request:Request,group:str,event_id:str):
        return await invoke(request,lambda st,g,r:core.mutate(st,g,r,previous=event_id,retract=True),group,body=True)

    @app.delete('/identity-groups/{group}/primary-associations/{event_id}')
    async def erase(request:Request,group:str,event_id:str):
        return await invoke(request,core.erase,group,event_id,body=True)

    @app.get('/identity-groups/{group}/primary-associations')
    async def associations(request:Request,group:str):
        return await invoke(request,core.events,group,fields=('after','limit'))

    @app.get('/identity-groups/{group}/primary-associations/{event_id}')
    async def association(request:Request,group:str,event_id:str):
        return await invoke(request,core.event_detail,group,event_id)

    @app.get('/identity-groups/{group}/speakers')
    async def persons(request:Request,group:str):
        return await invoke(request,core.persons,group,fields=('cursor','limit'))

    @app.patch('/identity-groups/{group}/speakers/{person_id}')
    async def rename(request:Request,group:str,person_id:str):
        return await invoke(request,core.rename,group,person_id,body=True)

    @app.get('/identity-groups/{group}/speakers/{person_id}/occurrences')
    async def occurrences(request:Request,group:str,person_id:str):
        return await invoke(request,core.occurrences,group,person_id,fields=('cursor','limit','include_unavailable'))

    @app.get('/identity-groups/{group}/identity-relations')
    async def relations(request:Request,group:str):
        return await invoke(request,core.relations,group,fields=('after','limit'))

    @app.post('/identity-groups/{group}/identity-relations')
    async def relate(request:Request,group:str):
        return await invoke(request,core.relate,group,body=True)

    @app.post('/identity-groups/{group}/identity-relations/{event_id}/retract')
    async def retract_relation(request:Request,group:str,event_id:str):
        return await invoke(request,lambda st,g,r:core.relate(st,g,r,previous=event_id),group,body=True)
