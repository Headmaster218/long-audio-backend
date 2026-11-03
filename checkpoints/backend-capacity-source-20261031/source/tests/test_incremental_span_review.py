"""Independent v3 span review with real files and hard process restart.

The protocol peer is synthetic. No native analysis, deployment or long-audio
qualification is performed by this suite.
"""
import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from audio_backend import recorder_capture_intake as capture
from audio_backend import recorder_incremental_span_intake as span
from audio_backend import recording_uploader as u
from test_extended_recorder_intake import Peer
from test_recorder_sequence import fixture as segment


class ObservedPeer(Peer):
    def __init__(self):
        super().__init__()
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        return super().request(method, path, **kwargs)


def options(tmp_path):
    return dict(job_dir=tmp_path/'job', api='http://127.0.0.1:8765',
                session_id='known', capture_id='extended', exclusive_writer=True,
                acknowledge_span_local_closure=True, transport=ObservedPeer(),
                limits=u.Limits(disk_floor=0, deadline_seconds=60, poll_seconds=.001))


def delivery(tmp_path, index, *, count=12, terminal='INTERRUPTION', first=71,
             origin=(1 << 63) - 1 - 64):
    path = tmp_path/('delivery-' + str(index))
    segment(path, first + index, epoch=27, start=origin + 4*index,
            run='independent-review', boundary='RESTART', seam=index > 0,
            close=terminal if index == count - 1 else 'ROTATION')
    return path


def contents(root):
    return {str(p.relative_to(root)):p.read_bytes()
            for p in root.rglob('*') if p.is_file()}


ACTIVE_CRASH = '''
import json, os, sys
sys.path.insert(0, 'tests')
from audio_backend import recorder_capture_intake as capture
from audio_backend import recorder_incremental_span_intake as span
from audio_backend import recording_uploader as u
from test_extended_recorder_intake import Peer
args = json.loads(sys.argv[1])
make = capture.CaptureUploader._new_piece
def crash(self, row):
    result = make(self, row)
    if result is not None:
        os._exit(77)
    return result
capture.CaptureUploader._new_piece = crash
span.run(**args, transport=Peer(), limits=u.Limits(disk_floor=0, deadline_seconds=30))
'''


def active_crash(args, path):
    wire = {k:str(v) if isinstance(v, Path) else v for k,v in args.items()
            if k not in ('transport', 'limits')}
    wire['delivery'] = str(path)
    result = subprocess.run([sys.executable, '-c', ACTIVE_CRASH, json.dumps(wire)],
                            capture_output=True, timeout=45)
    assert result.returncode == 77, result.stderr.decode()


def test_twelve_originals_hard_restart_active_piece_latest_retry_and_finalize(tmp_path):
    args = options(tmp_path)
    paths = [delivery(tmp_path, index) for index in range(12)]
    originals = {str(path):contents(path) for path in paths}
    for index, path in enumerate(paths):
        if index in (5, 11):
            active_crash(args, path)
            state = json.loads((args['job_dir']/'uploader/uploader.json').read_text())
            assert state['active_input']['clean_eof'] is False
            assert state['active_input']['frames'] == 4
            # Only the incomplete input may rebuild a lost owned spool piece.
            piece = state['active_page']['pieces'][-1]
            (args['job_dir']/'uploader'/piece['name']).unlink()
            before = contents(args['job_dir'])
            if index == 5:
                with pytest.raises(capture.Refused, match='pending_delivery'):
                    span.run(**args, delivery=paths[index+1])
            else:
                with pytest.raises(capture.Refused, match='pending_delivery'):
                    span.run(**args, finalize=True)
            assert contents(args['job_dir']) == before
        result = span.run(**args, delivery=path, start=index == 0)
        assert result['frames'] == (index+1)*4 and result['inputs'] == index+1
        assert result['max_inputs'] == 192 and not result['capture_finalized']
        if index:
            before = contents(args['job_dir'])
            with pytest.raises(capture.Refused, match='exact_latest'):
                span.run(**args, delivery=paths[index-1])
            assert contents(args['job_dir']) == before
        assert span.run(**args, delivery=path)['frames'] == result['frames']
    assert not args['transport'].puts and not args['transport'].eos
    final = span.run(**args, finalize=True)
    assert final['capture_finalized'] and final['frames'] == 48 and final['inputs'] == 12
    assert len(args['transport'].puts) == len(set(args['transport'].puts)) == 12
    sources = args['transport'].pages[1]['sources']
    assert [source['source_start_frame'] for source in sources] == list(range(0, 48, 4))
    puts = list(args['transport'].puts)
    assert span.run(**args, finalize=True) == final and args['transport'].puts == puts
    assert originals == {str(path):contents(path) for path in paths}


@pytest.mark.parametrize('change', ['path', 'nonce'])
def test_latest_retry_binds_original_path_and_envelope_not_only_audio(tmp_path, change):
    args = options(tmp_path)
    first = delivery(tmp_path, 0)
    span.run(**args, delivery=first, start=True)
    selected = first
    if change == 'path':
        selected = tmp_path/'copied-original'
        shutil.copytree(first, selected)
    else:
        marker = first/'delivery.v1.txt'
        rows = marker.read_text().splitlines()
        rows[1] = rows[1][:-32] + '1'*32
        marker.write_text('\n'.join(rows) + '\n')
    before = contents(args['job_dir'])
    args['transport'].calls.clear()
    with pytest.raises(capture.Refused):
        span.run(**args, delivery=selected)
    assert contents(args['job_dir']) == before
    assert all(method == 'GET' for method, _ in args['transport'].calls)


def test_pending_page_slot_bound_not_relaxed_for_more_inputs(tmp_path):
    args = options(tmp_path)
    paths = [delivery(tmp_path, index, count=15, origin=100001) for index in range(15)]
    for index, path in enumerate(paths[:14]):
        span.run(**args, delivery=path, start=index == 0)
    before = contents(args['job_dir'])
    with pytest.raises(capture.Refused, match='source_slots_exhausted_no_automatic_eos'):
        span.run(**args, delivery=paths[14])
    assert contents(args['job_dir']) == before
    assert not args['transport'].puts and not args['transport'].eos


def test_initial_recovery_is_refused_after_any_normal_checkpoint(tmp_path):
    args = options(tmp_path)
    first = delivery(tmp_path, 0)
    span.run(**args, delivery=first, start=True)
    before = contents(args['job_dir'])
    args['transport'].calls.clear()
    with pytest.raises(capture.Refused, match='pristine_uploader'):
        span.run(**args, delivery=first, recover_initial=True)
    assert contents(args['job_dir']) == before and not args['transport'].calls


def test_occupied_target_never_admits_selected_initial_source(tmp_path):
    args = options(tmp_path)
    first = delivery(tmp_path, 0)
    peer = args['transport']

    class Occupied:
        def request(self, method, path, **kwargs):
            assert method == 'GET'
            result = peer.request(method, path, **kwargs)
            if path.endswith('/recording-contract'):
                result['capture'] = dict(capture_id='foreign', capture_origin_frame=0)
            return result

    args['transport'] = Occupied()
    with pytest.raises(capture.Refused, match='session_not_empty'):
        span.run(**args, delivery=first, start=True)
    assert not list((args['job_dir']/'admissions').iterdir())
    assert not (args['job_dir']/'uploader/uploader.json').exists()


@pytest.mark.parametrize('field', [
    'header_target', 'header_bytes', 'intent_header_target', 'intent_report',
    'receipt_target', 'receipt_ordinal', 'receipt_sequence', 'receipt_frame_start',
    'receipt_channels', 'receipt_seam', 'snapshot_report',
])
def test_new_span_canonical_identity_rejects_bool_integer_aliases(tmp_path, field):
    args = options(tmp_path)
    first = delivery(tmp_path, 0, first=0, origin=0)
    span.run(**args, delivery=first, start=True)
    root = args['job_dir']
    if field.startswith('header_'):
        target = root/'capture.json'
        value = capture.single.manifest_read(target)
        if field == 'header_target':
            value['target']['capture_origin_frame'] = False
        else:
            value['input_bytes'] = float(value['input_bytes'])
    elif field.startswith('intent_'):
        target = root/'initial-intent.json'
        value = capture.single.manifest_read(target)
        if field == 'intent_header_target':
            value['header']['target']['capture_origin_frame'] = False
        else:
            value['delivery']['channels'] = True
    elif field == 'snapshot_report':
        target = root/'admissions/0/snapshot/snapshot.json'
        value = json.loads(target.read_text())
        value['body']['delivery']['channels'] = True
        value['sha256'] = hashlib.sha256(capture.single._json(value['body'])).hexdigest()
    else:
        target = root/'admissions/0/admission.json'
        value = capture.single.manifest_read(target)
        if field == 'receipt_target':
            value['target']['capture_origin_frame'] = False
        elif field == 'receipt_ordinal':
            value['ordinal'] = False
        else:
            key = {'receipt_sequence':'sequence', 'receipt_frame_start':'frame_start',
                   'receipt_channels':'channels', 'receipt_seam':'trusted_previous_seam'}[field]
            value['delivery'][key] = True if key == 'channels' else 0 if key == 'trusted_previous_seam' else False
    # Canonical JSON with a changed scalar type remains syntactically valid.
    # Snapshot pending/receipt are hardlinks, so both names observe these bytes.
    target.write_bytes(capture.single._json(value))
    before = contents(root)
    args['transport'].calls.clear()
    with pytest.raises((capture.Refused, u.Incomplete, ValueError)):
        span.run(**args, delivery=first)
    assert contents(root) == before and not args['transport'].calls


@pytest.mark.parametrize('field', ['target', 'report', 'bytes', 'ordinal', 'snapshot'])
def test_initial_recovery_aliases_refuse_before_repair_or_network(tmp_path, monkeypatch, field):
    args = options(tmp_path)
    first = delivery(tmp_path, 0, first=0, origin=0)

    class BeforeCheckpoint(BaseException):
        pass

    def stop(self):
        raise BeforeCheckpoint()

    with monkeypatch.context() as patch:
        patch.setattr(capture.CaptureUploader, 'run', stop)
        with pytest.raises(BeforeCheckpoint):
            span.run(**args, delivery=first, start=True)
    root = args['job_dir']
    assert not (root/'uploader/uploader.json').exists()
    if field == 'ordinal':
        path = root/'admissions/0/admission.json'
        value = capture.single.manifest_read(path)
        value['ordinal'] = False
    elif field == 'snapshot':
        path = root/'admissions/0/snapshot/snapshot.json'
        value = json.loads(path.read_text())
        value['body']['delivery']['channels'] = True
        value['sha256'] = hashlib.sha256(capture.single._json(value['body'])).hexdigest()
    else:
        path = root/'initial-intent.json'
        value = capture.single.manifest_read(path)
        if field == 'target':
            value['header']['target']['capture_origin_frame'] = False
        elif field == 'report':
            value['delivery']['channels'] = True
        else:
            value['header']['input_bytes'] = float(value['header']['input_bytes'])
    path.write_bytes(capture.single._json(value))
    before = contents(root)
    args['transport'].calls.clear()
    with pytest.raises((capture.Refused, u.Incomplete, ValueError)):
        span.run(**args, delivery=first, recover_initial=True)
    assert contents(root) == before and not args['transport'].calls
