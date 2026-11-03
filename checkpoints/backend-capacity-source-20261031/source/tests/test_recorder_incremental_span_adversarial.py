"""Adversarial span-local intake evidence, using tiny media and a synthetic peer.

These tests exercise real local files/journals and hard process exits. The peer
is a protocol double, not evidence of native processing or long-duration audio.
"""
import fcntl
import json
from pathlib import Path
import subprocess
import sys

import pytest

from audio_backend import recorder_capture_intake as capture
from audio_backend import recorder_incremental_span_intake as span
from audio_backend import recorder_intake as single
from audio_backend import recording_uploader as u
from test_extended_recorder_intake import Peer
from test_recorder_sequence import fixture as segment
from test_recorder_initial_intent import CHILD


class ObservedPeer(Peer):
    def __init__(self):
        super().__init__()
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        return super().request(method, path, **kwargs)


def options(tmp_path):
    peer = ObservedPeer()
    return dict(job_dir=tmp_path/'job', api='http://127.0.0.1:8765',
                session_id='known', capture_id='extended', exclusive_writer=True,
                acknowledge_span_local_closure=True, transport=peer,
                limits=u.Limits(disk_floor=0, deadline_seconds=30, poll_seconds=.001))


def selected(tmp_path, name, seq=37, **changes):
    values = dict(epoch=9, start=400 + (seq-37)*4, boundary='UNKNOWN',
                  run='selected-run', seam=seq != 37)
    values.update(changes)
    path = tmp_path/name
    segment(path, seq, **values)
    return path


def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('changes', [
    {'seq':39}, {'start':405}, {'start':403}, {'epoch':10, 'start':0, 'seam':False},
    {'run':'changed-run'}, {'capture':'changed-capture'}, {'seam':False},
    {'boundary':'ROUTE_CHANGE_UNCERTAIN'}, {'close':'RECOVERED_TAIL', 'seam':False},
])
def test_unjoinable_next_input_leaves_job_byte_identical(tmp_path, changes):
    args = options(tmp_path)
    first = selected(tmp_path, 'first')
    bad_options = dict(changes)
    seq = bad_options.pop('seq', 38)
    bad = selected(tmp_path, 'bad', seq, **bad_options)
    span.run(**args, delivery=first, start=True)
    before = contents(args['job_dir'])
    with pytest.raises((capture.Refused, ValueError)):
        span.run(**args, delivery=bad)
    assert contents(args['job_dir']) == before
    assert not args['transport'].puts and not args['transport'].eos
    good = selected(tmp_path, 'good', 38, close='INTERRUPTION')
    assert span.run(**args, delivery=good)['frames'] == 8


@pytest.mark.parametrize('terminal', ['STOP', 'INTERRUPTION', 'RECOVERED_TAIL'])
def test_terminal_declaration_cannot_be_appended_across(tmp_path, terminal):
    args = options(tmp_path)
    first = selected(tmp_path, 'first', close=terminal)
    second = selected(tmp_path, 'second', 38)
    span.run(**args, delivery=first, start=True)
    before = contents(args['job_dir'])
    with pytest.raises((capture.Refused, ValueError)):
        span.run(**args, delivery=second)
    assert contents(args['job_dir']) == before
    assert not args['transport'].eos
    assert span.run(**args, finalize=True)['capture_finalized']


@pytest.mark.parametrize('operation', ['retry', 'append', 'finalize', 'recover'])
def test_acknowledgment_required_on_every_invocation(tmp_path, operation):
    args = options(tmp_path)
    first = selected(tmp_path, 'first', close='STOP')
    span.run(**args, delivery=first, start=True)
    before = contents(args['job_dir'])
    args.pop('acknowledge_span_local_closure')
    args['transport'].calls.clear()
    mode = {'finalize':True} if operation == 'finalize' else {'delivery':first}
    if operation == 'append':
        mode['delivery'] = selected(tmp_path, 'second', 38)
    if operation == 'recover':
        mode['recover_initial'] = True
    with pytest.raises((capture.Refused, TypeError, ValueError)):
        span.run(**args, **mode)
    assert contents(args['job_dir']) == before
    assert not args['transport'].calls


@pytest.mark.parametrize('capacity', [1, 64, 128])
def test_frozen_capacity_not_expanded_on_reopen(tmp_path, capacity):
    args = options(tmp_path)
    first = selected(tmp_path, 'first')
    result = span.run(**args, delivery=first, start=True, max_inputs=capacity, max_hours=2)
    assert result['max_inputs'] == capacity and result['max_hours'] == 2
    before = contents(args['job_dir'])
    with pytest.raises(capture.Refused, match='frozen_capacity_changed'):
        span.run(**args, delivery=first, max_inputs=192)
    with pytest.raises(capture.Refused, match='frozen_capacity_changed'):
        span.run(**args, delivery=first, max_hours=12)
    assert contents(args['job_dir']) == before
    assert span.run(**args, delivery=first)['max_inputs'] == capacity


@pytest.mark.parametrize('lock_name', ['intake.lock', 'uploader/uploader.lock'])
def test_held_existing_lock_prevents_network_or_mutation(tmp_path, lock_name):
    args = options(tmp_path)
    first = selected(tmp_path, 'first')
    span.run(**args, delivery=first, start=True)
    second = selected(tmp_path, 'second', 38)
    before = contents(args['job_dir'])
    args['transport'].calls.clear()
    with (args['job_dir']/lock_name).open('rb') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            span.run(**args, delivery=second)
    assert contents(args['job_dir']) == before and not args['transport'].calls


@pytest.mark.parametrize('creator', ['capture', 'span'])
def test_capture_and_span_jobs_cannot_cross_open(tmp_path, creator):
    args = options(tmp_path)
    first = tmp_path/'first'
    segment(first, 0, close='STOP')
    capture_args = {k:v for k,v in args.items() if k != 'acknowledge_span_local_closure'}
    if creator == 'capture':
        capture.run(**capture_args, delivery=first, start=True)
    else:
        span.run(**args, delivery=first, start=True)
    before = contents(args['job_dir'])
    args['transport'].calls.clear()
    with pytest.raises((capture.Refused, ValueError)):
        if creator == 'capture':
            span.run(**args, delivery=first)
        else:
            capture.run(**capture_args, delivery=first)
    assert contents(args['job_dir']) == before and not args['transport'].calls


def interrupted(tmp_path, stage):
    args = options(tmp_path)
    first = selected(tmp_path, 'first')
    wire = {k:str(v) if isinstance(v, Path) else v for k,v in args.items()
            if k not in ('limits', 'transport')}
    wire.update(delivery=str(first), start=True)
    contract = args['transport'].request('GET', '/sessions/known/recording-contract')
    # Patch the common engine, but invoke the distinct public span entrypoint.
    code = CHILD.replace('from audio_backend import recorder_capture_intake as c',
        'from audio_backend import recorder_capture_intake as c\nfrom audio_backend import recorder_incremental_span_intake as span')
    code = code.replace('c.run(**args,', 'span.run(**args,')
    child = subprocess.run([sys.executable, '-c', code, json.dumps(wire),
                            json.dumps(contract), stage], capture_output=True, timeout=40)
    assert child.returncode == 74, child.stderr.decode()
    args['transport'].calls.clear()
    return first, args


@pytest.mark.parametrize('stage', ['intent', 'header_prefix', 'scaffold',
                                  'snapshot_prefix', 'snapshot_complete', 'admission_prefix'])
def test_actual_process_crash_recovers_only_exact_initial_span(tmp_path, stage):
    first, args = interrupted(tmp_path, stage)
    before = {p:(p.stat().st_ino, p.read_bytes()) for p in args['job_dir'].rglob('*') if p.is_file()}
    result = span.run(**args, delivery=first, recover_initial=True)
    assert result['frames'] == 4 and result['inputs'] == 1 and not result['capture_finalized']
    for path, (inode, data) in before.items():
        assert path.stat().st_ino == inode and path.read_bytes().startswith(data)
    assert all(method == 'GET' for method, _ in args['transport'].calls)
    second = selected(tmp_path, 'second', 38, close='STOP')
    assert span.run(**args, delivery=second)['frames'] == 8
    assert span.run(**args, finalize=True)['capture_finalized']


def test_missing_initial_intent_never_guessed_after_crash(tmp_path):
    first, args = interrupted(tmp_path, 'intent')
    (args['job_dir']/'initial-intent.json').unlink()
    before = contents(args['job_dir'])
    with pytest.raises((capture.Refused, FileNotFoundError, ValueError)):
        span.run(**args, delivery=first, recover_initial=True)
    assert contents(args['job_dir']) == before and not args['transport'].calls


@pytest.mark.parametrize('field', ['span_origin', 'first_sequence', 'epoch', 'epoch_frame_start',
                                  'run_identity_sha256', 'capture_identity_sha256',
                                  'acknowledgments', 'initial_intent'])
@pytest.mark.parametrize('coordinated', [False, True])
def test_missing_or_changed_origin_cannot_reinterpret_job(tmp_path, field, coordinated):
    args = options(tmp_path)
    first = selected(tmp_path, 'first')
    span.run(**args, delivery=first, start=True)
    root = args['job_dir']
    header = single.manifest_read(root/'capture.json')
    assert header['schema'] == 'explicit-incremental-recorder-span-intake-v1'
    assert header['span_origin']['first_sequence'] == 37
    assert header['span_origin']['epoch'] == 9
    assert header['span_origin']['epoch_frame_start'] == 400
    if field in ('span_origin', 'acknowledgments', 'initial_intent'):
        header.pop(field)
    elif field.endswith('sha256'):
        header['span_origin'][field] = 'f'*64
    else:
        header['span_origin'][field] += 1
    (root/'capture.json').write_bytes(single._json(header))
    if coordinated:
        intent = single.manifest_read(root/'initial-intent.json')
        intent['header'] = header
        (root/'initial-intent.json').write_bytes(single._json(intent))
    before = contents(root)
    args['transport'].calls.clear()
    with pytest.raises((capture.Refused, ValueError)):
        span.run(**args, delivery=first)
    assert contents(root) == before and not args['transport'].calls


@pytest.mark.parametrize('field', ['first_sequence', 'epoch', 'epoch_frame_start'])
def test_recovery_rejects_forged_intent_origin_before_repair(tmp_path, field):
    first, args = interrupted(tmp_path, 'intent')
    root = args['job_dir']
    intent = single.manifest_read(root/'initial-intent.json')
    intent['header']['span_origin'][field] += 1
    (root/'initial-intent.json').write_bytes(single._json(intent))
    before = contents(root)
    with pytest.raises((capture.Refused, ValueError)):
        span.run(**args, delivery=first, recover_initial=True)
    assert contents(root) == before and not args['transport'].calls


def test_original_coordinates_and_bytes_survive_local_normalization(tmp_path):
    args = options(tmp_path)
    paths = [selected(tmp_path, 'first'), selected(tmp_path, 'second', 38, close='INTERRUPTION')]
    originals = {path:contents(path) for path in paths}
    for index, path in enumerate(paths):
        result = span.run(**args, delivery=path, start=index == 0)
        assert result['inputs'] == index+1 and result['frames'] == 4*(index+1)
        assert not result['capture_finalized'] and not args['transport'].eos
        assert contents(path) == originals[path]
        admission = single.manifest_read(args['job_dir']/f'admissions/{index}/admission.json')
        assert admission['ordinal'] == index and admission['schema'] == result['schema']
        assert admission['delivery']['sequence'] == 37+index
        assert admission['delivery']['epoch'] == 9
        assert admission['delivery']['frame_start'] == 400+index*4
        snapshot = args['job_dir']/f'admissions/{index}/snapshot'
        for name in ('audio.wav', 'segment.v1.bin', 'delivery.v1.txt'):
            assert (snapshot/name).read_bytes() == originals[path][name]
    finalized = span.run(**args, finalize=True)
    assert finalized['capture_finalized'] and finalized['published_frames'] == 8
    assert [source['source_start_frame'] for page in args['transport'].pages.values()
            for source in page['sources']] == [0, 4]
    assert args['transport'].status()['capture_end_frame'] == 8
