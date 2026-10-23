"""Single short recording page, real API, no mutation retries or raw body logs."""
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import time

from http_input import RequestLedger, request
from launch_binding import prepare
from service_cli import write_once
from short_input import load_input, validate_completed, FRAMES, RATE, ORIGIN, END, SHA256


def run_owned(owner, action):
    original = traceback = None
    result = None
    try:
        owner.verify_inherited()
        result = action()
    except BaseException as exc:
        original, traceback = exc, exc.__traceback__
    try:
        owner.release()
    except BaseException as cleanup:
        if original is None: raise
        original.add_note('Uploader lock cleanup failure: '+type(cleanup).__name__)
    if original is not None:
        raise original.with_traceback(traceback)
    return result


def api(port, method, route, path, *, deadline, ledger, body=None, headers=None):
    if not path.startswith('/sessions') or '\r' in path or '\n' in path:
        raise ValueError('invalid fixed recording route')
    def operation(timeout):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
        try:
            connection.request(method, path, body=body, headers={'Connection': 'close', **(headers or {})})
            response = connection.getresponse()
            encoded = response.read(1024*1024+1)
            if len(encoded) > 1024*1024:
                raise ValueError('recording response body bound')
            return response.status, json.loads(encoded)
        finally:
            connection.close()
    return request(method, route, operation, deadline=deadline, ledger=ledger)


def collect(call, data, *, deadline, clock=time.monotonic, sleep=time.sleep):
    def post(route, path, body):
        return call('POST', route, path, body=json.dumps(body).encode(),
                    headers={'Content-Type': 'application/json'})
    session = post('create_recording', '/sessions', {'name': '', 'identity_group': 'short-anonymous',
                   'processing_mode': 'legacy'})
    sid = session.get('id')
    if not isinstance(sid, str) or re.fullmatch('[a-f0-9]{32}', sid) is None:
        raise ValueError('unexpected anonymous session ID')
    base = '/sessions/'+sid+'/recordings/short-fixed'
    post('create_source', base+'/pages', {'page_number': 1, 'capture_origin_frame': ORIGIN})
    accepted = call('PUT', 'source_bytes', base+'/pages/1/sources?source_start_frame='+str(ORIGIN),
                    body=data, headers={'Content-Type': 'audio/wav', 'Idempotency-Key': 'short-source-1'})
    revision = accepted.get('revision')
    if type(revision) is not int or revision != 1 or accepted.get('duplicate') is not False:
        raise ValueError('unexpected source acceptance revision')
    # This write is never retried, including after a transport timeout.
    post('seal_page', base+'/pages/1/seal', {'idempotency_key': 'short-seal-1',
         'expected_revision': revision, 'end_frame': END, 'eos': True})
    while True:
        if clock() >= deadline: raise TimeoutError('short upload processing deadline')
        page = call('GET', 'page_status', base+'/pages/1')
        chunks = call('GET', 'page_status', '/sessions/'+sid+'/chunks?after=0&limit=2')
        capture = call('GET', 'page_status', base)
        if validate_completed(page, chunks, capture):
            if clock() >= deadline: raise TimeoutError('late short completion')
            return dict(completed_chunks=1, accepted_sources=1, eos_pages=1, frames=FRAMES,
                        seconds=FRAMES/RATE, input_sha256=SHA256, capture_origin=ORIGIN,
                        capture_end=END, exact_offsets=True, exact_frame_conservation=True,
                        probes='UNKNOWN', vector_success='UNKNOWN', accuracy='NOT_EVALUATED')
        sleep(min(.1, max(0, deadline-clock())))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--without-trial-observer', action='store_true', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--uploader-lock-fd', type=int, required=True)
    parser.add_argument('--deadline', type=float, required=True)
    args = parser.parse_args(argv)
    value, _ = prepare(args.manifest, args.manifest_sha256,
                       deadline=args.deadline, output=args.output)
    if value['qualification'] != 'short-operational':
        raise ValueError('short manifest required')
    if not 1 <= len(os.sched_getaffinity(0)) <= 2:
        raise ValueError('CPU restriction missing')
    from audio_backend.resources import InstanceLock
    owner = InstanceLock(args.output/'journal/uploader.lock')
    owner.adopt(args.uploader_lock_fd)
    def action():
        data = load_input(value['input']['path'])
        with (args.output/'uploader-requests.jsonl').open('x', encoding='utf-8') as stream:
            ledger = RequestLedger(stream)
            def call(method, route, path, **kwargs):
                return api(value['port'], method, route, path, deadline=args.deadline,
                           ledger=ledger, **kwargs)
            result = collect(call, data, deadline=args.deadline)
        write_once(args.output/'uploader-result.json', result)
    run_owned(owner, action)


if __name__ == '__main__':
    main()
