"""Authenticated app GETs; share the existing response-buffer lifetime gate."""
import re
import threading

from fastapi import Request
from starlette.responses import JSONResponse, Response

from .moss_handy_http import PRIVATE
from .native_owned_support_cache import ReadError, _identifier
from .transcript_contract import MAX_FRAME, BLOCK_FRAMES
from .transcript_store import TranscriptStore


def parse_query(raw, *, detail=False, audit=False):
    if type(raw) is not bytes or len(raw) > 1024: raise ReadError(409, 'invalid_query')
    try: text = raw.decode('ascii')
    except UnicodeError: raise ReadError(409, 'invalid_query') from None
    result = {}
    fields = text.split('&') if text else []
    if detail and fields or len(fields) > 2: raise ReadError(409, 'invalid_query')
    for field in fields:
        if field.count('=') != 1: raise ReadError(409, 'invalid_query')
        name, value = field.split('=')
        if name not in ('block', 'cursor') or name in result: raise ReadError(409, 'invalid_query')
        if audit and name != 'cursor': raise ReadError(409, 'invalid_query')
        if name == 'block':
            if re.fullmatch(r'(?:0|[1-9][0-9]{0,10})', value) is None:
                raise ReadError(409, 'invalid_query')
            value = int(value)
            if value > MAX_FRAME // BLOCK_FRAMES: raise ReadError(409, 'invalid_query')
        elif re.fullmatch(r'[A-Za-z0-9_-]{1,512}', value) is None:
            raise ReadError(409, 'invalid_query')
        result[name] = value
    return result


def register_routes(app, store, assign_lease, get_response_budget):
    reader = TranscriptStore(store)
    # At most one bounded metadata/result staging operation. Response leases
    # continue to cover buffers after the synchronous reader leaves this gate.
    staging = threading.Lock()

    def read(request, sid, source_ref=None, *, audit=False):
        lease, admitted = None, False
        try:
            _identifier(sid)
            query = parse_query(request.scope.get('query_string', b''), detail=source_ref is not None, audit=audit)
            if not staging.acquire(blocking=False): raise ReadError(503, 'read_capacity')
            admitted = True
            lease = get_response_budget().reserve_jobs_response('result')
            if not assign_lease(request.scope, lease): raise ReadError(503, 'response_closed')
            if audit:
                body = reader.binding_audit(sid, **query)
            elif source_ref is None:
                body = reader.page(sid, **query)
            else:
                body = reader.evidence(sid, source_ref)
            done = request.scope.get('_native_support_read_done')
            if done is not None and done.is_set(): raise ReadError(503, 'response_closed')
            return Response(body, media_type='application/json', headers=PRIVATE)
        except ReadError as exc:
            if lease is not None: lease.release()
            return JSONResponse({'detail': exc.reason}, status_code=exc.status_code, headers=PRIVATE)
        except Exception:
            if lease is not None: lease.release()
            return JSONResponse({'detail': 'transcript_unavailable'}, status_code=503, headers=PRIVATE)
        finally:
            if admitted: staging.release()

    @app.get('/sessions/{sid}/transcript')
    def transcript(request: Request, sid: str):
        return read(request, sid)

    @app.get('/sessions/{sid}/transcript/binding-audit')
    def binding_audit(request: Request, sid: str):
        return read(request, sid, audit=True)

    @app.get('/sessions/{sid}/transcript/evidence/{source_ref}')
    def evidence(request: Request, sid: str, source_ref: str):
        return read(request, sid, source_ref)
