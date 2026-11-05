"""Private bounded native-turn evidence, with original strict source authority."""
import re
import threading
from fastapi import Request
from starlette.responses import JSONResponse, Response
from .moss_handy_http import PRIVATE
from .native_owned_support_cache import ReadError
from . import strict_payload_v2 as payload


def query(raw):
    if type(raw) is not bytes or len(raw)>160:raise ReadError(409,'native_turn_request')
    try:text=raw.decode('ascii')
    except UnicodeError:raise ReadError(409,'native_turn_request') from None
    parts={}
    for item in text.split('&'):
        pair=item.split('=')
        if len(pair)!=2 or pair[0] not in ('source_sha256','frames') or pair[0] in parts:
            raise ReadError(409,'native_turn_request')
        parts[pair[0]]=pair[1]
    if set(parts)!={'source_sha256','frames'} or re.fullmatch('[a-f0-9]{64}',parts['source_sha256']) is None or re.fullmatch('[1-9][0-9]{0,5}',parts['frames']) is None:
        raise ReadError(409,'native_turn_request')
    parts['frames']=int(parts['frames'])
    return parts


def register_routes(app,store,assign_lease,get_response_budget):
    staging=threading.Lock()
    @app.get('/sessions/{sid}/chunks/{cid}/native-turn-evidence')
    def read(request:Request,sid:str,cid:str):
        lease=None;admitted=False
        try:
            selected=query(request.scope.get('query_string',b''))
            if not staging.acquire(False):raise ReadError(503,'native_turn_capacity')
            admitted=True
            lease=get_response_budget().reserve_jobs_response('result')
            if not assign_lease(request.scope,lease):raise ReadError(503,'response_closed')
            done=request.scope.get('_native_support_read_done')
            result=payload.read_native_turn_evidence(store,sid=sid,cid=cid,
                cancel=done.is_set if done is not None else None,**selected)
            if done is not None and done.is_set():raise ReadError(503,'response_closed')
            return Response(payload._canonical(result),media_type='application/json',headers=PRIVATE)
        except ReadError as exc:
            if lease is not None:lease.release()
            return JSONResponse({'detail':exc.reason},status_code=exc.status_code,headers=PRIVATE)
        except (payload.PayloadIntegrityError,payload.base.ArchiveUnavailable,ValueError):
            if lease is not None:lease.release()
            return JSONResponse({'detail':'native_turn_evidence_unavailable'},status_code=409,headers=PRIVATE)
        except Exception:
            if lease is not None:lease.release()
            return JSONResponse({'detail':'native_turn_evidence_unavailable'},status_code=503,headers=PRIVATE)
        finally:
            if admitted:staging.release()
