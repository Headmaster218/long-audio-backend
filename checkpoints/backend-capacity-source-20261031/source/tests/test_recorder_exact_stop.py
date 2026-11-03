"""Exact-full STOP admission, and no inference from ROTATION-only input.

Set RECORDER_EXACT_STOP_FIXTURE to the actual Java producer output to repeat
cross-language admission. StrictRoutes stops before native seal/publication.
"""
import os
import json
from pathlib import Path
import pytest
from audio_backend import recorder_capture_intake as capture
from audio_backend import recorder_batch_intake as batch
from audio_backend import recording_uploader as u
from audio_backend.recorder_delivery import inspect_delivery, parse_envelope
from audio_backend.recorder_metadata import decode
from test_recorder_sequence import fixture as segment
from test_recording_session_contract import handler_app, session
from test_strict_uploader_admission import StrictRoutes, AdmissionBoundary


def metadata(path):
    report = inspect_delivery(path, validate_metadata=True)
    return decode(parse_envelope((path/'segment.v1.bin').read_bytes(), report, return_local=True))


@pytest.fixture(params=['synthetic', 'java_producer'])
def deliveries(request, tmp_path):
    if request.param == 'java_producer':
        value = os.environ.get('RECORDER_EXACT_STOP_FIXTURE')
        if not value:
            pytest.skip('Explicit local Java producer output not supplied')
        paths = list(Path(value).glob('r-*'))
        assert len(paths) == 3
        paths.sort(key=lambda p: metadata(p)['sequence'])
    else:
        paths = [tmp_path/f'delivery-{i}' for i in range(3)]
        for i, path in enumerate(paths):
            segment(path, i, close='STOP' if i == 2 else 'ROTATION')
    rows = [metadata(p) for p in paths]
    assert [r['sequence'] for r in rows] == [0, 1, 2]
    assert [r['close_reason'] for r in rows] == ['ROTATION', 'ROTATION', 'STOP']
    assert all(r['frames'] == r['target_frames'] for r in rows)
    return paths, sum(r['frames'] for r in rows)


def options(tmp_path, client, routes):
    return dict(job_dir=tmp_path/'job', api='http://127.0.0.1:8765',
                session_id=session(client), capture_id='exact-stop', exclusive_writer=True,
                limits=u.Limits(disk_floor=0, deadline_seconds=30, poll_seconds=.001),
                transport=routes)


def test_full_stop_batch_and_resume(deliveries, tmp_path):
    paths, frames = deliveries
    before = [{p.name:p.read_bytes() for p in root.iterdir()} for root in paths]
    with handler_app(tmp_path/'server') as (client, app):
        routes = StrictRoutes(client, lose_put=True)
        args = options(tmp_path, client, routes)
        with pytest.raises(AdmissionBoundary):
            batch.run(**args, deliveries=paths)
        state = json.loads((args['job_dir']/'uploader/uploader.json').read_text())
        assert sum(x['frames'] for x in state['inputs']) == frames
        assert state['finalize_requested'] and state['pages'][0]['seal']['eos'] is True
        assert len(routes.puts) == 3
        saved = list(routes.puts)
        with pytest.raises(AdmissionBoundary):
            batch.run(**args, deliveries=paths, resume=True)
        assert routes.puts == saved and not app.state.worker.thread.is_alive()
    assert before == [{p.name:p.read_bytes() for p in root.iterdir()} for root in paths]


def test_incremental_rotation_requires_stop_then_explicit_finalize(deliveries, tmp_path):
    paths, frames = deliveries
    with handler_app(tmp_path/'server') as (client, app):
        routes = StrictRoutes(client, lose_put=True)
        args = options(tmp_path, client, routes)
        for index, path in enumerate(paths):
            result = capture.run(**args, delivery=path, start=index == 0)
            assert result['status'] == 'delivery_staged_without_eos'
            assert not result['capture_finalized'] and not result['physical_continuity_proven']
            if index < 2:
                with pytest.raises(capture.Refused, match='stop_required'):
                    capture.run(**args, finalize=True)
                assert routes.puts == []
        assert result['frames'] == frames
        assert all(method == 'GET' for method, _, _ in routes.calls)
        with pytest.raises(AdmissionBoundary):
            capture.run(**args, finalize=True)
        assert len(routes.puts) == 3
        saved = list(routes.puts)
        with pytest.raises(AdmissionBoundary):
            capture.run(**args, finalize=True)
        assert routes.puts == saved and not app.state.worker.thread.is_alive()


def test_historical_rotation_only_batch_refused_before_http(tmp_path):
    paths = [tmp_path/f'old-{i}' for i in range(3)]
    for index, path in enumerate(paths):
        segment(path, index, close='ROTATION')
    class NoNetwork:
        def request(self, *args, **kwargs):
            pytest.fail('ROTATION-only capture must refuse before HTTP')
    with pytest.raises(batch.Refused):
        batch.run(deliveries=paths, job_dir=tmp_path/'job', api='http://127.0.0.1:8765',
                  session_id='known', capture_id='old', exclusive_writer=True,
                  limits=u.Limits(disk_floor=0), transport=NoNetwork())
