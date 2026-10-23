"""Fixed A uploader. Non-EOS publication advances; completion waits until EOS."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time

from a_input import AInput, plan, validate_page, validate_final, FRAMES, RATE, ORIGIN, END, SHA256
from http_input import RequestLedger
from launch_binding import prepare
from service_cli import write_once
from uploader_cli import api, run_owned


def collect(call, piece, *, deadline, clock=time.monotonic, sleep=time.sleep):
    def post(route, path, body):
        return call('POST', route, path, body=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    session = post('create_recording', '/sessions', {'name': '', 'identity_group': 'A-anonymous', 'processing_mode': 'legacy'})
    sid = session.get('id')
    if not isinstance(sid, str) or re.fullmatch('[a-f0-9]{32}', sid) is None:
        raise ValueError('unexpected anonymous session ID')
    base = '/sessions/'+sid+'/recordings/A-fixed'
    receipts = {}
    for page in plan():
        number = page['number']
        post('create_source', base+'/pages', {'page_number': number, 'capture_origin_frame': ORIGIN})
        for revision, source in enumerate(page['sources'], 1):
            data = piece(source)
            if len(data) != 44+source['frames']*2 or len(data) > 4000000:
                raise ValueError('A piece byte envelope differs')
            receipts[source['index']] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            accepted = call('PUT', 'source_bytes', base+f'/pages/{number}/sources?source_start_frame='+str(ORIGIN+source['offset']),
                body=data, headers={'Content-Type': 'audio/wav', 'Idempotency-Key': 'A-source-'+str(source['index'])})
            del data
            if type(accepted.get('revision')) is not int or accepted['revision'] != revision or accepted.get('duplicate') is not False:
                raise ValueError('A source acceptance revision differs')
        post('seal_page', base+f'/pages/{number}/seal', {'idempotency_key': f'A-seal-{number}',
            'expected_revision': len(page['sources']), 'end_frame': ORIGIN+page['offset']+page['frames'], 'eos': number == 3})
        while True:
            if clock() >= deadline: raise TimeoutError('A page publication deadline')
            current = call('GET', 'page_status', base+f'/pages/{number}')
            if validate_page(current, page, receipts):
                if clock() >= deadline: raise TimeoutError('late A page publication')
                break  # Published+released, NOT completed. A tail may wait for next page.
            sleep(min(1., max(0, deadline-clock())))
    while True:
        if clock() >= deadline: raise TimeoutError('A processed completion deadline')
        pages = [call('GET', 'page_status', base+f'/pages/{number}') for number in (1, 2, 3)]
        chunks = call('GET', 'page_status', '/sessions/'+sid+'/chunks?after=0&limit=70')
        capture = call('GET', 'page_status', base)
        if validate_final(pages, chunks, capture, receipts):
            if clock() >= deadline: raise TimeoutError('late A processed completion')
            return dict(completed_chunks=69, accepted_sources=8, pages=3, eos_pages=1,
                frames=FRAMES, seconds=FRAMES/RATE, input_sha256=SHA256, source_window_seconds=[840, 1560],
                capture_origin=ORIGIN, capture_end=END, exact_offsets=True, exact_frame_conservation=True,
                probes='UNKNOWN', vector_success='UNKNOWN', accuracy='NOT_EVALUATED', source_receipts=receipts)
        sleep(min(2.5, max(0, deadline-clock())))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--without-trial-observer', action='store_true', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--uploader-lock-fd', type=int, required=True)
    parser.add_argument('--deadline', type=float, required=True)
    args = parser.parse_args(argv)
    value, _ = prepare(args.manifest, args.manifest_sha256, deadline=args.deadline, output=args.output)
    if value['qualification'] != 'A-operational': raise ValueError('fixed A manifest required')
    if not 1 <= len(os.sched_getaffinity(0)) <= 2: raise ValueError('CPU restriction missing')
    from audio_backend.resources import InstanceLock
    owner = InstanceLock(args.output/'journal/uploader.lock'); owner.adopt(args.uploader_lock_fd)
    def action():
        with AInput(value['input']['path'], args.deadline) as source, (args.output/'uploader-requests.jsonl').open('x', encoding='utf-8') as stream:
            ledger = RequestLedger(stream)
            def call(method, route, path, **kwargs):
                return api(value['port'], method, route, path, deadline=args.deadline, ledger=ledger, **kwargs)
            result = collect(call, source.piece, deadline=args.deadline)
        write_once(args.output/'uploader-result.json', result)
    run_owned(owner, action)


if __name__ == '__main__': main()
