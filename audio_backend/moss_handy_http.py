"""Strict private jobs/cell routes; requests select neither files nor runtimes."""
import asyncio
import hashlib
import json
import re
import threading
import time

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from .moss_handy_profile import NAME
from .native_owned_support_cache import ReadError, _identifier

MAX_BODY_BYTES, MAX_QUERY_BYTES, RECEIVE_SECONDS = 1024, 1024, 2.
MAX_CELL_PUBLIC_BYTES = 65536
MAX_CELL_SEQUENCE = (1 << 63) - 1
PRIVATE = {'Cache-Control':'private, no-store', 'Pragma':'no-cache', 'X-Content-Type-Options':'nosniff'}
HEX32 = re.compile(r'[a-f0-9]{32}\Z')
HEX64 = re.compile(r'[a-f0-9]{64}\Z')
KEY = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
DECIMAL = re.compile(r'(?:0|[1-9][0-9]{0,18})\Z')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:raise ValueError('duplicate')
        result[key] = value
    return result


def parse_body(raw, kind):
    if type(raw) is not bytes or len(raw) > MAX_BODY_BYTES:
        raise ReadError(409, 'invalid_body')
    try:
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise ReadError(409, 'invalid_body') from None
    fields = {'jobs_epoch'}
    if kind == 'submit':
        fields |= {'expected_capture_epoch', 'expected_revision', 'support_id', 'profile'}
    elif kind == 'cell_submit':
        fields |= {'expected_cell_sequence', 'expected_cell_revision',
                   'expected_proof_sha256', 'support_id', 'profile'}
    if type(value) is not dict:
        raise ReadError(409, 'invalid_body')
    if kind == 'cell_submit' and set(value) == fields | {'coverage_intent'}:
        if value['coverage_intent'] != 'overlapping_diagnostic':
            raise ReadError(409, 'invalid_body')
    elif set(value) != fields:
        raise ReadError(409, 'invalid_body')
    if type(value['jobs_epoch']) is not str or HEX32.fullmatch(value['jobs_epoch']) is None:
        raise ReadError(409, 'invalid_body')
    if kind == 'submit' and (type(value['expected_capture_epoch']) is not str
            or HEX32.fullmatch(value['expected_capture_epoch']) is None
            or type(value['expected_revision']) is not int or not 0 <= value['expected_revision'] < 10**19
            or type(value['support_id']) is not str or HEX64.fullmatch(value['support_id']) is None
            or value['profile'] != NAME):
        raise ReadError(409, 'invalid_body')
    if kind == 'cell_submit' and (
            type(value['expected_cell_sequence']) is not int
            or not 1 <= value['expected_cell_sequence'] <= MAX_CELL_SEQUENCE
            or type(value['expected_cell_revision']) is not int
            or not 1 <= value['expected_cell_revision'] <= MAX_CELL_SEQUENCE
            or type(value['expected_proof_sha256']) is not str
            or HEX64.fullmatch(value['expected_proof_sha256']) is None
            or type(value['support_id']) is not str or HEX64.fullmatch(value['support_id']) is None
            or type(value['profile']) is not str or value['profile'] != NAME):
        raise ReadError(409, 'invalid_body')
    return value


def parse_query(raw, kind):
    if type(raw) is not bytes or len(raw) > MAX_QUERY_BYTES:
        raise ReadError(409, 'invalid_query')
    try:text = raw.decode('ascii')
    except UnicodeError:raise ReadError(409, 'invalid_query') from None
    fields = text.split('&') if text else []
    if len(fields) > 4:raise ReadError(409, 'invalid_query')
    allowed = {'after', 'limit', 'expected_jobs_epoch'} if kind in ('list', 'cells_list') else set()
    result = {}
    for field in fields:
        if field.count('=') != 1:raise ReadError(409, 'invalid_query')
        key, value = field.split('=')
        if key not in allowed or key in result:raise ReadError(409, 'invalid_query')
        if key == 'expected_jobs_epoch':
            if HEX32.fullmatch(value) is None:raise ReadError(409, 'invalid_query')
            result[key] = value
        else:
            if DECIMAL.fullmatch(value) is None:raise ReadError(409, 'invalid_query')
            result[key] = int(value)
    if not 1 <= result.get('limit', 20) <= 20:raise ReadError(409, 'invalid_query')
    if result.get('after', 0) and 'expected_jobs_epoch' not in result:
        raise ReadError(409, 'jobs_epoch_required')
    if kind == 'cells_list' and result.get('after', 0) > MAX_CELL_SEQUENCE:
        raise ReadError(409, 'invalid_query')
    return result


async def receive_body(request):
    headers = request.scope.get('headers', ())
    types = [v for k, v in headers if k.lower() == b'content-type']
    encodings = [v for k, v in headers if k.lower() == b'content-encoding']
    lengths = [v for k, v in headers if k.lower() == b'content-length']
    if types != [b'application/json'] or encodings or len(lengths) > 1:
        raise ReadError(409, 'invalid_content_type')
    expected = None
    if lengths:
        try:
            text = lengths[0].decode('ascii')
            if DECIMAL.fullmatch(text) is None:raise ValueError()
            expected = int(text)
            if expected > MAX_BODY_BYTES:raise ValueError()
        except (ValueError, UnicodeError):raise ReadError(409, 'invalid_content_length') from None
    result = bytearray()
    deadline = time.monotonic() + RECEIVE_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:raise ReadError(409, 'body_timeout')
        try:message = await asyncio.wait_for(request.receive(), remaining)
        except asyncio.TimeoutError:raise ReadError(409, 'body_timeout') from None
        if message['type'] == 'http.disconnect':raise ReadError(409, 'request_disconnected')
        if message['type'] != 'http.request':raise ReadError(409, 'invalid_body')
        block = message.get('body', b'')
        if len(result) + len(block) > MAX_BODY_BYTES:raise ReadError(409, 'body_capacity')
        result.extend(block)
        if not message.get('more_body', False):break
    if expected is not None and expected != len(result):raise ReadError(409, 'invalid_content_length')
    return bytes(result)


def register_routes(app, get_manager, assign_lease):
    mutation = threading.Lock()

    def error(exc):
        body={'detail':exc.reason}
        if hasattr(exc,'summary'):body['job']=exc.summary
        return JSONResponse(body, status_code=exc.status_code, headers=PRIVATE)

    def manager_for(sid, jid=None):
        _identifier(sid)
        if jid is not None and (type(jid) is not str or HEX32.fullmatch(jid) is None):
            raise ReadError(409, 'invalid_identifier')
        owner = get_manager()
        if owner is None:raise ReadError(409, 'feature_disabled')
        return owner

    def read(request, sid, kind, jid=None):
        lease = None
        try:
            owner = manager_for(sid, jid)
            query = parse_query(request.scope.get('query_string', b''), kind)
            lease = owner.cache.reserve_jobs_response(kind)
            if not assign_lease(request.scope, lease):raise ReadError(503, 'response_closed')
            if kind == 'list':body = owner.list(sid, **query)
            elif kind == 'detail':body = owner.detail(sid, jid)
            else:body = owner.content(sid, jid, kind)
            headers = dict(PRIVATE)
            media = 'application/json'
            if kind == 'raw':
                media = 'text/plain; charset=utf-8'
                headers.update({'Content-Length':str(len(body)),
                    'X-MOSS-Raw-Kind':'exact-native-raw-text-utf8',
                    'X-Content-SHA256':hashlib.sha256(body).hexdigest()})
            return Response(body, media_type=media, headers=headers)
        except ReadError as exc:
            if lease is not None:lease.release()
            return error(exc)
        except Exception:
            if lease is not None:lease.release()
            return error(ReadError(503, 'jobs_unavailable'))

    def read_cells(request, sid, cell_id=None):
        lease = None
        try:
            _identifier(sid)
            if cell_id is not None and (type(cell_id) is not str or HEX64.fullmatch(cell_id) is None):
                raise ReadError(409, 'invalid_identifier')
            query = parse_query(request.scope.get('query_string', b''),
                                'cells_list' if cell_id is None else 'detail')
            owner = manager_for(sid)
            lease = owner.cache.reserve_jobs_response('list' if cell_id is None else 'detail')
            if not assign_lease(request.scope, lease):raise ReadError(503, 'response_closed')
            headers = dict(PRIVATE)
            if cell_id is None:
                body = owner.cells_list(sid, **query)
            else:
                detail = owner.cell_detail(sid, cell_id)
                body = detail['proof']
                if (type(detail['cell_id']) is not str or detail['cell_id'] != cell_id
                        or type(detail['sequence']) is not int or not 1 <= detail['sequence'] <= MAX_CELL_SEQUENCE
                        or type(detail['revision']) is not int or not 1 <= detail['revision'] <= MAX_CELL_SEQUENCE
                        or type(detail['proof_sha256']) is not str or HEX64.fullmatch(detail['proof_sha256']) is None
                        or type(detail['jobs_epoch']) is not str or HEX32.fullmatch(detail['jobs_epoch']) is None
                        or detail['state'] not in ('retained', 'not_retained_pcm')
                        or type(detail['pcm_retained']) is not bool
                        or detail['pcm_retained'] != (detail['state'] == 'retained')):
                    raise ReadError(503, 'cell_detail_unavailable')
                headers.update({'X-Native-Cell-ID':cell_id,
                    'X-Native-Cell-Sequence':str(detail['sequence']),
                    'X-Native-Cell-Revision':str(detail['revision']),
                    'X-Native-Cell-Proof-SHA256':detail['proof_sha256'],
                    'X-Native-Cell-State':detail['state'],
                    'X-Native-Cell-PCM-Retained':'true' if detail['pcm_retained'] else 'false',
                    'X-MOSS-Jobs-Epoch':detail['jobs_epoch']})
            if type(body) is not bytes or not 1 <= len(body) <= MAX_CELL_PUBLIC_BYTES:
                raise ReadError(503, 'cell_response_capacity')
            if cell_id is not None and hashlib.sha256(body).hexdigest() != detail['proof_sha256']:
                raise ReadError(503, 'cell_detail_unavailable')
            return Response(body, media_type='application/json', headers=headers)
        except ReadError as exc:
            if lease is not None:lease.release()
            return error(exc)
        except Exception:
            if lease is not None:lease.release()
            return error(ReadError(503, 'cells_unavailable'))

    async def mutate(request, sid, kind, target):
        if not mutation.acquire(blocking=False):return error(ReadError(429, 'mutation_capacity'))
        lease = task = disconnect = None
        admission_closed = threading.Event()
        try:
            owner = manager_for(sid, target if kind == 'cancel' else None)
            if kind == 'submit':_identifier(target)
            elif kind == 'cell_submit' and (type(target) is not str or HEX64.fullmatch(target) is None):
                raise ReadError(409, 'invalid_identifier')
            parse_query(request.scope.get('query_string', b''), 'mutation')
            raw = await receive_body(request)
            body = parse_body(raw, kind)
            key = None
            if kind in ('submit', 'cell_submit'):
                keys = [v for k, v in request.scope.get('headers', ()) if k.lower() == b'idempotency-key']
                try:
                    key = keys[0].decode('ascii') if len(keys) == 1 else ''
                except UnicodeError:key = ''
                if KEY.fullmatch(key) is None:raise ReadError(409, 'invalid_idempotency_key')
            lease = owner.cache.reserve_jobs_response('mutation')
            if not assign_lease(request.scope, lease):raise ReadError(503, 'response_closed')
            done = request.scope.get('_native_support_read_done')
            async def watch_disconnect():
                try:
                    # The final request body was consumed above. The next valid
                    # ASGI event is disconnect; any trailing request data also
                    # fails admission closed. Exactly one receive stays scoped
                    # to this request, with no thread or persistent watcher.
                    await request.receive()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                admission_closed.set()
                if done is not None:done.set()
            if kind == 'cell_submit':disconnect = asyncio.create_task(watch_disconnect())
            def perform():
                if admission_closed.is_set() or done is not None and done.is_set():
                    raise ReadError(503, 'response_closed')
                if kind == 'submit':
                    return owner.submit(sid, target, body, key,
                        _admission_open=lambda: not admission_closed.is_set() and not lease.released
                            and (done is None or not done.is_set()))
                if kind == 'cell_submit':
                    return owner.submit_cell(sid, target, body, key,
                        _admission_open=lambda: not admission_closed.is_set() and not lease.released
                            and (done is None or not done.is_set()))
                return owner.cancel(sid, target, body['jobs_epoch'])
            task = asyncio.create_task(run_in_threadpool(perform))
            # Ownership after acceptance belongs to the manager, not this request.
            status, result = await asyncio.shield(task)
            return JSONResponse(result, status_code=status, headers=PRIVATE)
        except ReadError as exc:
            if lease is not None:lease.release()
            return error(exc)
        except Exception:
            if lease is not None:lease.release()
            return error(ReadError(503, 'jobs_unavailable'))
        finally:
            admission_closed.set()
            if disconnect is not None:
                disconnect.cancel()
                try:await disconnect
                except asyncio.CancelledError:pass
            if task is not None and not task.done():
                def finished(future):
                    try:future.exception()
                    except BaseException:pass
                    mutation.release()
                task.add_done_callback(finished)
            else:
                mutation.release()

    @app.post('/sessions/{sid}/native-owned-supports/{cid}/moss-jobs')
    async def submit(request: Request, sid: str, cid: str):
        return await mutate(request, sid, 'submit', cid)

    @app.get('/sessions/{sid}/native-owned-cells')
    def cells_list(request: Request, sid: str):
        return read_cells(request, sid)

    @app.get('/sessions/{sid}/native-owned-cells/{cell_id}')
    def cell_detail(request: Request, sid: str, cell_id: str):
        return read_cells(request, sid, cell_id)

    @app.post('/sessions/{sid}/native-owned-cells/{cell_id}/moss-jobs')
    async def submit_cell(request: Request, sid: str, cell_id: str):
        return await mutate(request, sid, 'cell_submit', cell_id)

    @app.get('/sessions/{sid}/moss-jobs')
    def listing(request: Request, sid: str):
        return read(request, sid, 'list')

    @app.get('/sessions/{sid}/moss-jobs/{job_id}')
    def detail(request: Request, sid: str, job_id: str):
        return read(request, sid, 'detail', job_id)

    @app.get('/sessions/{sid}/moss-jobs/{job_id}/result')
    def result(request: Request, sid: str, job_id: str):
        return read(request, sid, 'result', job_id)

    @app.get('/sessions/{sid}/moss-jobs/{job_id}/raw')
    def raw(request: Request, sid: str, job_id: str):
        return read(request, sid, 'raw', job_id)

    @app.post('/sessions/{sid}/moss-jobs/{job_id}/cancel')
    async def cancel(request: Request, sid: str, job_id: str):
        return await mutate(request, sid, 'cancel', job_id)
