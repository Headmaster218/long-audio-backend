"""Private archive HTTP with execution absent and one bounded response pool."""
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from audio_backend.app import create_app
from audio_backend import moss_result_store as history
from audio_backend.settings import Settings
from test_moss_result_store import snapshot, reserve


@pytest.fixture
def history_api(tmp_path, monkeypatch):
    monkeypatch.setattr(history.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * 1024**3))
    settings = Settings(data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0)
    with patch('audio_backend.moss_handy_jobs.MossJobs', side_effect=AssertionError('no execution manager')), \
         patch('audio_backend.native_execution_permit.NativeExecutionPermit', side_effect=AssertionError('no native permit')):
        app = create_app(settings)
        with TestClient(app) as client:
            sid = client.post('/sessions', json={'name': 'historical only'}).json()['id']
            value = snapshot(sid)
            archive = app.state.moss_result_store
            reserve(archive, value); archive.commit(value)
            yield app, client, sid, value


def private(response):
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['pragma'] == 'no-cache'
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_history_is_readable_with_execution_off_and_fixed_route_precedes_live(history_api):
    app, client, sid, value = history_api
    base = f'/sessions/{sid}/moss-jobs/history'
    assert app.state.moss_jobs is None
    assert app.state.worker.native_owned_support_cache is None
    for suffix in ('', '/' + value.identity.job_id, '/' + value.identity.job_id + '/result'):
        response = client.get(base + suffix)
        assert response.status_code == 200, response.text
        private(response)
    assert client.get(base + '/' + value.identity.job_id + '/result').content == value.result_json
    assert client.get(base + '/' + value.identity.job_id).json()['raw_retention'] == 'volatile'
    assert client.get(f'/sessions/{sid}/moss-jobs/{value.identity.job_id}/raw').status_code == 409
    paths = [route.path for route in app.routes]
    assert paths.index('/sessions/{sid}/moss-jobs/history') < paths.index('/sessions/{sid}/moss-jobs/{job_id}')
    assert not (app.state.settings.data_dir / 'moss-handy-jobs').exists()


def test_bearer_session_isolation_and_all_error_paths_are_private(history_api):
    app, client, sid, value = history_api
    base = f'/sessions/{sid}/moss-jobs/history'
    app.state.settings.api_key = 'synthetic-history-token'
    for suffix in ('', '/' + value.identity.job_id, '/' + value.identity.job_id + '/result'):
        response = client.get(base + suffix)
        assert response.status_code == 401; private(response)
        allowed = client.get(base + suffix, headers={'Authorization': 'Bearer synthetic-history-token'})
        assert allowed.status_code == 200; private(allowed)
    app.state.settings.api_key = None
    other = client.post('/sessions', json={'name': 'other'}).json()['id']
    for target in ('missing', other):
        response = client.get(f'/sessions/{target}/moss-jobs/history/{value.identity.job_id}/result')
        assert response.status_code == 404; private(response)
        assert value.identity.job_id not in response.text
    for method, suffix in (('put', ''), ('get', '/bad'), ('get', '/extra/unknown')):
        private(getattr(client, method)(base + suffix))
    assert app.state.moss_response_budget._lease_count == 0


@pytest.mark.parametrize('query', ['after=01', 'after=-1', 'limit=0', 'limit=21',
    'limit=%31', 'limit=1&limit=2', 'expected_jobs_epoch=' + 'a' * 32,
    'after=9223372036854775808', 'unknown=1'])
def test_history_query_is_strict_without_live_epoch_requirement(history_api, query):
    app, client, sid, value = history_api
    response = client.get(f'/sessions/{sid}/moss-jobs/history?{query}')
    assert response.status_code == 409; private(response)
    valid = client.get(f'/sessions/{sid}/moss-jobs/history?after=1&limit=1')
    assert valid.status_code == 200; private(valid)
    assert app.state.moss_response_budget._lease_count == 0


def test_history_capacity_error_and_disconnect_release_shared_leases(history_api):
    app, client, sid, value = history_api
    pool = app.state.moss_response_budget
    leases = [pool.reserve_jobs_response('result'), pool.reserve_jobs_response('detail')]
    try:
        response = client.get(f'/sessions/{sid}/moss-jobs/history')
        assert response.status_code == 503; private(response)
        assert pool._lease_count == 2
    finally:
        for lease in leases:lease.release()
    done = threading.Event(); done.set()
    request = Request({'type': 'http', 'query_string': b'', 'headers': [],
                       '_native_support_read_done': done})
    route = next(route for route in app.routes if route.path == '/sessions/{sid}/moss-jobs/history/{job_id}/result')
    response = route.endpoint(request, sid, value.identity.job_id)
    assert response.status_code == 503; private(response)
    assert pool._lease_count == pool._lease_bytes == 0
    with patch.object(app.state.moss_result_store, 'result', side_effect=OSError('synthetic read failure')):
        response = client.get(f'/sessions/{sid}/moss-jobs/history/{value.identity.job_id}/result')
    assert response.status_code == 503; private(response)
    assert pool._lease_count == pool._lease_bytes == 0
