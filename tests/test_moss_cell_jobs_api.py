"""Finite simulated cell HTTP/lifetime cases; no model or native process.

The real routes, response reservations and owner handoff are exercised against
the explicitly simulated storage/driver fixture. These tests do not qualify a
production storage runtime, actual in-flight native cancellation or acoustics.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from audio_backend import moss_handy_http as http
from audio_backend import moss_handy_jobs as jobs
from audio_backend import native_owned_cell_capture as capture
from audio_backend import native_owned_cell_store as cells
from audio_backend import native_owned_support_cache as support_cache
from audio_backend.app import create_app, _NativeSupportReadLifetime, _assign_native_read_lease
from audio_backend.native_owned_cell_jobs import CellJobsSource
from audio_backend.native_execution_permit import NativeExecutionPermit
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.settings import Settings
from audio_backend.worker import Worker
from test_moss_handy_jobs import RetainedTemporary, ManualThread
from test_moss_handy_controller import clean
from test_native_execution_permit import sample
from test_native_owned_cell_jobs import CellJobsFixture
from test_native_owned_cell_store import simulated_profile

# All new standalone application fixtures are retained by the existing bounded
# runner convention. No caller-supplied directory or cleanup traversal is used.
TemporaryDirectory = RetainedTemporary


class CellJobsAPI(CellJobsFixture):
    def setUp(self):
        super().setUp()
        self.app = FastAPI()
        http.register_routes(self.app, lambda: self.owner, _assign_native_read_lease)
        self.app.add_middleware(_NativeSupportReadLifetime)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        super().tearDown()

    def cell_path(self, cell=None, sid='session'):
        return '/sessions/' + sid + '/native-owned-cells' + ('/' + cell.cell_id if cell else '')

    def post_cell(self, cell, body, key='cell'):
        return self.client.post(self.cell_path(cell) + '/moss-jobs', json=body,
                                headers={'Idempotency-Key': key})

    def job_path(self, record, suffix=''):
        return '/sessions/session/moss-jobs/' + record['job_id'] + suffix

    def private(self, response):
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertEqual(response.headers['pragma'], 'no-cache')
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')

    def endpoint(self, path):
        return next(route.endpoint for route in self.app.routes if getattr(route, 'path', None) == path)

    def request(self, body, done=None):
        scope = {'type': 'http', 'query_string': b'',
            'headers': [(b'content-type', b'application/json'), (b'idempotency-key', b'cell')],
            '_native_support_read_done': done or threading.Event()}
        received = False
        async def receive():
            nonlocal received
            if not received:
                received = True
                return {'type': 'http.request', 'body': jobs.encoded(body)}
            await asyncio.Event().wait()
        return Request(scope, receive)

    async def observed(self, event):
        for _ in range(100):
            if event.is_set():return
            await asyncio.sleep(.01)
        self.fail('bounded simulated handoff did not reach its barrier')

    def test_three_cell_routes_share_existing_jobs_manager_and_flags(self):
        paths = [route.path for route in self.app.routes if '/native-owned-cells' in getattr(route, 'path', '')]
        self.assertEqual(paths, ['/sessions/{sid}/native-owned-cells',
            '/sessions/{sid}/native-owned-cells/{cell_id}',
            '/sessions/{sid}/native-owned-cells/{cell_id}/moss-jobs'])
        cell, body = self.ready_cell()
        self.assertIs(self.owner.cell_source, self.source)
        self.assertIs(self.cache.jobs_owner, self.owner)
        for flag in ('native_owned_cells_enabled', 'moss_jobs_enabled'):
            with self.subTest(flag=flag), patch.object(self.store.settings, flag, False):
                self.assertEqual(self.client.get(self.cell_path()).status_code, 409)
                self.assertEqual(self.post_cell(cell, body).status_code, 409)
        self.assertFalse(self.owner._records); self.assertFalse(self.drivers)
        with TemporaryDirectory() as directory:
            app = create_app(Settings(data_dir=directory, start_worker=False, disk_reserve_bytes=0))
            with TestClient(app) as client:
                self.assertEqual(client.get('/sessions/absent/native-owned-cells').status_code, 409)
                self.assertIsNone(app.state.moss_jobs)
                self.assertIsNone(app.state.worker.native_owned_cells)
            self.assertFalse((Path(directory) / 'native-owned-cells').exists())
            self.assertFalse((Path(directory) / jobs.NAMESPACE).exists())

    def test_live_cell_list_is_bounded_and_cursor_does_not_skip(self):
        expected = [self.ready_cell(index=index, frames=16)[0].cell_id for index in range(21)]
        for limit in (1, 20):
            found = []; cursor = 0
            for _ in range(24):
                query = {'after': cursor, 'limit': limit, 'expected_jobs_epoch': self.owner.jobs_epoch}
                response = self.client.get(self.cell_path(), params=query)
                self.assertEqual(response.status_code, 200, response.text); self.private(response)
                self.assertLessEqual(len(response.content), 65536)
                page = response.json(); self.assertEqual(page['jobs_epoch'], self.owner.jobs_epoch)
                self.assertLessEqual(len(page['cells']), limit)
                rows = page['cells']
                self.assertTrue(all(row['sequence'] > cursor for row in rows))
                self.assertGreaterEqual(page['next_cursor'], rows[-1]['sequence'] if rows else cursor)
                found.extend(row['cell_id'] for row in rows)
                if page['next_cursor'] == cursor:break
                cursor = page['next_cursor']
            self.assertEqual(found, expected)
        wrong = self.client.get(self.cell_path(), params={'after': 1, 'expected_jobs_epoch': '0' * 32})
        self.assertEqual(wrong.status_code, 409)
        original = self.cell_owner.authority.cell_valid
        def filter_one(doc, budget, *, publishing):
            return doc['support']['start_frame'] != 16 and original(doc, budget, publishing=publishing)
        found = []; cursor = 0
        with patch.object(self.cell_owner.authority, 'cell_valid', side_effect=filter_one):
            for _ in range(24):
                response = self.client.get(self.cell_path(), params={'after': cursor, 'limit': 1,
                    'expected_jobs_epoch': self.owner.jobs_epoch})
                self.assertEqual(response.status_code, 200, response.text)
                page = response.json(); found.extend(row['cell_id'] for row in page['cells'])
                if page['next_cursor'] == cursor:break
                self.assertGreater(page['next_cursor'], cursor); cursor = page['next_cursor']
        self.assertEqual(found, expected[:1] + expected[2:])

    def test_detail_returns_exact_proof_and_explicit_retention_headers(self):
        cell, body = self.ready_cell(index=25)
        response = self.client.get(self.cell_path(cell))
        self.assertEqual(response.status_code, 200, response.text); self.private(response)
        self.assertEqual(response.content, cell.proof)
        self.assertEqual(response.headers['content-type'], 'application/json')
        self.assertEqual(int(response.headers['content-length']), len(cell.proof))
        self.assertEqual(response.headers['x-native-cell-id'], cell.cell_id)
        self.assertEqual(int(response.headers['x-native-cell-sequence']), body['expected_cell_sequence'])
        self.assertEqual(int(response.headers['x-native-cell-revision']), body['expected_cell_revision'])
        self.assertEqual(response.headers['x-native-cell-proof-sha256'], hashlib.sha256(cell.proof).hexdigest())
        self.assertEqual(response.headers['x-native-cell-state'], 'retained')
        self.assertEqual(response.headers['x-native-cell-pcm-retained'], 'true')
        self.assertEqual(response.headers['x-moss-jobs-epoch'], self.owner.jobs_epoch)
        self.assertEqual(self.cache._lease_count, 0)
        metadata = self.owner.cell_detail('session', cell.cell_id)
        maximum = b'{"x":"' + b'x' * (65536 - 8) + b'"}'
        self.assertEqual(len(maximum), 65536)
        maximum_detail = {**metadata, 'proof': maximum, 'proof_sha256': hashlib.sha256(maximum).hexdigest()}
        # Isolate the HTTP envelope bound from the separately tested proof schema.
        with patch.object(self.owner, 'cell_detail', return_value=maximum_detail):
            response = self.client.get(self.cell_path(cell))
            self.assertEqual(response.status_code, 200); self.assertEqual(response.content, maximum)
            self.assertEqual(response.headers['content-length'], '65536')

    def test_unknown_or_metadata_only_cell_never_claims_available_pcm(self):
        cell, body = self.ready_cell()
        unknown = self.client.get(self.cell_path() + '/' + '0' * 64)
        self.assertEqual((unknown.status_code, unknown.json()['detail']), (404, 'cell_unavailable'))
        self.assertEqual(self.operation('expire', cell.cell_id)['status'], 'expired')
        detail = self.client.get(self.cell_path(cell))
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.content, cell.proof)
        self.assertEqual(detail.headers['x-native-cell-state'], 'not_retained_pcm')
        self.assertEqual(detail.headers['x-native-cell-pcm-retained'], 'false')
        request = dict(body, expected_cell_revision=int(detail.headers['x-native-cell-revision']))
        denied = self.post_cell(cell, request)
        self.assertEqual((denied.status_code, denied.json()['detail']), (409, 'cell_pcm_not_retained'))
        self.assertFalse(self.drivers); self.assertFalse(self.owner._records)

    def test_session_and_cross_session_authority_precede_cell_disclosure(self):
        cell, body = self.ready_cell(); self.chunk(sid='other')
        for sid in ('unknown', 'other'):
            for suffix in ('', '/moss-jobs'):
                path = self.cell_path(cell, sid) + suffix
                response = (self.client.post(path, json=body, headers={'Idempotency-Key': 'cross'})
                            if suffix else self.client.get(path))
                self.assertEqual(response.status_code, 404, response.text); self.private(response)
                for secret in (cell.cell_id, body['support_id'], body['expected_proof_sha256']):
                    self.assertNotIn(secret, response.text)
        self.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'"); self.db.commit()
        self.assertEqual(self.client.get(self.cell_path(cell)).status_code, 404)
        self.assertFalse(self.owner._records)

    def test_all_cell_success_and_error_paths_are_private(self):
        cell, _ = self.ready_cell()
        for method, path in (('get', self.cell_path()), ('get', self.cell_path(cell)),
                ('get', self.cell_path(cell) + '/unknown'), ('put', self.cell_path()),
                ('get', self.cell_path() + '/invalid'), ('get', self.cell_path() + '?limit=0')):
            self.private(getattr(self.client, method)(path))
        with TemporaryDirectory() as directory:
            app = create_app(Settings(data_dir=directory, start_worker=False,
                                      disk_reserve_bytes=0, api_key='synthetic-token'))
            with TestClient(app) as client:
                for suffix in ('', '/unknown', '/unknown/moss-jobs'):
                    response = client.get('/sessions/absent/native-owned-cells' + suffix)
                    self.assertEqual(response.status_code, 401); self.private(response)

    def test_submit_rejects_extra_duplicate_nested_and_nonfinite_fields(self):
        cell, body = self.ready_cell(); path = self.cell_path(cell) + '/moss-jobs'
        variants = ({**body, 'path': '/private'}, {**body, 'expected_cell_sequence': True},
            {**body, 'expected_cell_revision': False}, {**body, 'expected_cell_sequence': 0},
            {**body, 'expected_cell_revision': -1}, {**body, 'expected_cell_sequence': 1 << 63},
            {**body, 'expected_cell_revision': 1.0}, {**body, 'expected_proof_sha256': []},
            {**body, 'support_id': {'nested': 'x'}}, {**body, 'profile': 'other'})
        with patch.object(self.owner, 'submit_cell', side_effect=AssertionError('parser must reject')):
            for candidate in variants:
                response = self.client.post(path, json=candidate, headers={'Idempotency-Key': 'key'})
                self.assertEqual(response.status_code, 409, response.text); self.private(response)
            raw = jobs.encoded(body)
            for malformed in (raw[:-1] + b',"jobs_epoch":"' + body['jobs_epoch'].encode() + b'"}',
                              raw.replace(b'"expected_cell_revision":1', b'"expected_cell_revision":NaN'),
                              b'{"jobs_epoch":Infinity}', b'[[[[[]]]]]'):
                response = self.client.post(path, content=malformed,
                    headers={'Content-Type': 'application/json', 'Idempotency-Key': 'key'})
                self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse(self.cell_owner._pins); self.assertFalse(self.owner._records)

    def test_body_header_and_key_limits_share_original_mutation_slot(self):
        cell, body = self.ready_cell(); path = self.cell_path(cell) + '/moss-jobs'
        headers = {'Content-Type': 'application/json', 'Idempotency-Key': 'key'}
        for raw, extra in ((b'x' * 1025, {}), (b'{}', {'Content-Encoding': 'gzip'}),
                (b'{}', {'Content-Type': 'text/plain'}), (b'{}', {'Content-Length': '1025'}),
                (jobs.encoded(body), {'Idempotency-Key': 'x' * 65}),
                (jobs.encoded(body), {'Idempotency-Key': 'space key'})):
            self.assertEqual(self.client.post(path, content=raw, headers={**headers, **extra}).status_code, 409)
        for duplicate in ('Content-Length', 'Idempotency-Key', 'Content-Type'):
            extra = [(duplicate, str(len(jobs.encoded(body))) if duplicate == 'Content-Length'
                      else 'key' if duplicate == 'Idempotency-Key' else 'application/json')] * 2
            ordinary = [(name, value) for name, value in headers.items() if name != duplicate]
            response = self.client.post(path, content=jobs.encoded(body), headers=ordinary + extra)
            self.assertEqual(response.status_code, 409, response.text)
        padded = jobs.encoded(body); padded += b' ' * (1024 - len(padded))
        self.assertEqual(http.parse_body(padded, 'cell_submit'), body)
        async def receive_cases():
            async def slow():await asyncio.sleep(.02); return {'type': 'http.request', 'body': b'{}'}
            request = Request({'type': 'http', 'headers': [(b'content-type', b'application/json')]}, slow)
            with patch.object(http, 'RECEIVE_SECONDS', .001), self.assertRaises(ReadError) as caught:
                await http.receive_body(request)
            self.assertEqual(caught.exception.reason, 'body_timeout')
            async def disconnected():return {'type': 'http.disconnect'}
            request = Request({'type': 'http', 'headers': [(b'content-type', b'application/json')]}, disconnected)
            with self.assertRaises(ReadError) as caught:await http.receive_body(request)
            self.assertEqual(caught.exception.reason, 'request_disconnected')
        asyncio.run(receive_cases())
        endpoint = self.endpoint('/sessions/{sid}/native-owned-cells/{cell_id}/moss-jobs')
        rolling = self.endpoint('/sessions/{sid}/native-owned-supports/{cid}/moss-jobs')
        cancel = self.endpoint('/sessions/{sid}/moss-jobs/{job_id}/cancel')
        async def scenario():
            entered = asyncio.Event(); release = asyncio.Event()
            async def blocked(request):
                await request.receive(); entered.set(); await release.wait(); return jobs.encoded(body)
            with patch.object(http, 'receive_body', side_effect=blocked):
                request = self.request(body)
                first = asyncio.create_task(endpoint(request, 'session', cell.cell_id))
                await entered.wait()
                try:
                    self.assertEqual((await rolling(self.request(body), 'session', 'chunk')).status_code, 429)
                    self.assertEqual((await cancel(self.request(body), 'session', 'a' * 32)).status_code, 429)
                finally:release.set()
                result = await first; self.assertEqual(result.status_code, 202)
                request.scope.pop('_native_support_read_lease').release()
        asyncio.run(scenario()); self.finish()

    def test_query_and_identifier_limits_precede_owner_access(self):
        cell, _ = self.ready_cell()
        with patch.object(self.owner, 'cells_list', side_effect=AssertionError('invalid query reached owner')):
            for query in ('after=01', 'after=1', 'limit=0', 'limit=21', 'limit=%31', 'after=-1',
                          'unknown=1', 'limit=1&limit=2', 'after=1.0', 'x=' + 'x' * 1024,
                          'after=9223372036854775808&expected_jobs_epoch=' + self.owner.jobs_epoch):
                response = self.client.get(self.cell_path() + '?' + query)
                self.assertEqual(response.status_code, 409, response.text)
        with patch.object(self.owner, 'cell_detail', side_effect=AssertionError('invalid ID reached owner')):
            for identity in ('g' * 64, 'a' * 63, 'a' * 65, 'A' * 64):
                self.assertEqual(self.client.get(self.cell_path() + '/' + identity).status_code, 409)
        self.assertFalse(self.owner._records); self.assertFalse(self.drivers)

    def test_response_capacity_rejects_before_source_copy_or_acceptance(self):
        cell, body = self.ready_cell()
        first = self.cache.reserve_jobs_response('list'); second = self.cache.reserve_jobs_response('detail')
        try:
            with patch.object(self.cell_owner, 'submit', side_effect=AssertionError('response capacity must precede source')):
                for response in (self.client.get(self.cell_path()), self.client.get(self.cell_path(cell)), self.post_cell(cell, body)):
                    self.assertEqual(response.status_code, 503, response.text); self.private(response)
            self.assertEqual(self.cache._lease_count, 2)
            self.assertLessEqual(self.cache._lease_bytes, 1536 * 1024)
            self.assertFalse(self.cell_owner._pins); self.assertFalse(self.owner._records)
        finally:first.release(); second.release()
        self.assertEqual(self.cache._lease_count, 0)

    def test_response_closed_before_acceptance_abandons_cell_read(self):
        cell, body = self.ready_cell(); entered = threading.Event(); release = threading.Event()
        disconnected = threading.Event()
        original = self.cell_owner.authority.cell_valid
        def blocked(*args, **kwargs):
            entered.set(); release.wait(1.); return original(*args, **kwargs)
        async def scenario():
            path = self.cell_path(cell) + '/moss-jobs'
            scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                'method': 'POST', 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
                'root_path': '', 'query_string': b'', 'client': ('synthetic', 1), 'server': ('synthetic', 80),
                'headers': [(b'content-type', b'application/json'), (b'idempotency-key', b'cell')]}
            received = False; sent = []
            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {'type': 'http.request', 'body': jobs.encoded(body), 'more_body': False}
                await self.observed(entered)
                disconnected.set()
                return {'type': 'http.disconnect'}
            async def send(message):sent.append(message)
            async def unblock():
                await self.observed(disconnected)
                await asyncio.sleep(0)
                self.assertTrue(scope['_native_support_read_done'].is_set())
                release.set()
            with patch.object(self.cell_owner.authority, 'cell_valid', side_effect=blocked):
                unblocking = asyncio.create_task(unblock())
                try:await self.app(scope, receive, send)
                finally:release.set(); await unblocking
                for _ in range(100):
                    if not self.source.cleanup_pending and self.cell_owner._slot is None:break
                    await asyncio.sleep(.01)
            starts = [message for message in sent if message['type'] == 'http.response.start']
            self.assertEqual(len(starts), 1); self.assertEqual(starts[0]['status'], 503)
        try:asyncio.run(scenario())
        finally:release.set()
        self.assertFalse(self.source.cleanup_pending); self.assertFalse(self.cell_owner._pins)
        self.assertFalse(self.owner._keys); self.assertFalse(self.drivers)

    def test_disconnect_after_acceptance_keeps_manager_job_owned(self):
        cell, body = self.ready_cell(); entered = threading.Event(); release = threading.Event(); finished = threading.Event()
        original = self.owner.submit_cell
        def accepted(*args, **kwargs):
            result = original(*args, **kwargs); entered.set()
            try:release.wait(1.); return result
            finally:finished.set()
        endpoint = self.endpoint('/sessions/{sid}/native-owned-cells/{cell_id}/moss-jobs')
        async def scenario():
            done = threading.Event(); request = self.request(body, done)
            with patch.object(self.owner, 'submit_cell', side_effect=accepted):
                task = asyncio.create_task(endpoint(request, 'session', cell.cell_id))
                await self.observed(entered); task.cancel(); done.set()
                with self.assertRaises(asyncio.CancelledError):await task
                request.scope.pop('_native_support_read_lease').release(); release.set()
                await self.observed(finished); await asyncio.sleep(.01)
        try:asyncio.run(scenario())
        finally:release.set()
        self.assertEqual(len(self.owner._records), 1)
        self.assertFalse(self.owner._live['cancelled'].is_set())
        record = self.finish(); self.assertTrue(record['cleaned'])
        self.assertEqual(self.client.get(self.job_path(record, '/raw')).status_code, 200)

    def test_asgi_completion_disconnect_and_send_failure_release_once(self):
        class Lease:
            def __init__(self):self.calls = 0
            def release(self):self.calls += 1
        async def scenario(path, mode):
            lease = Lease(); scope = {'type': 'http', 'path': path}
            async def receive():return {'type': 'http.disconnect'}
            async def endpoint(scope, receive, send):
                _assign_native_read_lease(scope, lease)
                if mode == 'disconnect':await receive()
                await send({'type': 'http.response.start', 'status': 200, 'headers': []})
                await send({'type': 'http.response.body', 'body': b'{}'})
            async def send(message):
                if mode == 'failure':raise RuntimeError('explicit simulated send failure')
            try:await _NativeSupportReadLifetime(endpoint)(scope, receive, send)
            except RuntimeError:
                if mode != 'failure':raise
            self.assertEqual(lease.calls, 1); self.assertTrue(scope['_native_support_read_done'].is_set())
            late = Lease(); self.assertFalse(_assign_native_read_lease(scope, late)); self.assertEqual(late.calls, 1)
        for path in ('/sessions/s/native-owned-cells', '/sessions/s/native-owned-cells/c',
                     '/sessions/s/native-owned-cells/c/moss-jobs', '/sessions/s/moss-jobs/j/result',
                     '/sessions/s/moss-jobs/j/raw'):
            for mode in ('complete', 'disconnect', 'failure'):
                asyncio.run(scenario(path, mode))

    def test_already_authorized_response_may_finish_after_delete(self):
        cell, body = self.ready_cell(); self.assertEqual(self.post_cell(cell, body).status_code, 202)
        record = self.finish(); lease = self.cache.reserve_jobs_response('raw')
        admitted = self.owner.content('session', record['job_id'], 'raw')
        self.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'"); self.db.commit()
        self.cell_owner.revoked(); self.owner.invalidate_session('session')
        sent = []
        async def scenario():
            scope = {'type': 'http', 'path': self.job_path(record, '/raw')}
            async def endpoint(scope, receive, send):
                _assign_native_read_lease(scope, lease)
                await send({'type': 'http.response.body', 'body': admitted})
            async def receive():return {'type': 'http.disconnect'}
            async def send(message):sent.append(message['body'])
            await _NativeSupportReadLifetime(endpoint)(scope, receive, send)
        asyncio.run(scenario())
        self.assertEqual(sent, [admitted]); self.assertEqual(self.cache._lease_count, 0)
        self.assertEqual(self.client.get(self.job_path(record, '/raw')).status_code, 404)
        self.assertEqual(self.client.get(self.cell_path(cell)).status_code, 404)

    def test_direct_and_alias_store_deletion_cancel_cell_job(self):
        cell, body = self.ready_cell(); self.post_cell(cell, body); record = self.owner._live
        key = str(Path(self.store.path).absolute())
        db_key = support_cache._db_key(self.store)
        # Exercise the unchanged post-commit notifiers with both Store identities.
        # The minimal primary fixture mirrors only its committed deleted flag.
        invalidations = []
        registered_capture = SimpleNamespace(owner=self.cell_owner,
            invalidate_seam=invalidations.append)
        with patch.dict(capture._REGISTRY, {key: registered_capture}), \
             patch.dict(support_cache._registry, {db_key: self.cache}):
            self.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'"); self.db.commit()
            for index, store in enumerate((self.store, SimpleNamespace(path=self.store.path)), 1):
                self.assertTrue(capture.notify_deleted(store, 'session'))
                self.assertEqual(invalidations, ['capture_revoked'] * index)
                jobs.notify_deleted(store, 'session')
                self.assertTrue(record['cancelled'].is_set())
        self.assertEqual(invalidations, ['capture_revoked', 'capture_revoked'])
        self.finish(); self.assertFalse(self.drivers); self.assertFalse(self.cell_owner._pins)
        self.assertEqual(self.client.get(self.job_path(record, '/raw')).status_code, 404)

    def test_reads_validate_row_lineage_and_source_after_cleanup(self):
        cell, body = self.ready_cell(); self.post_cell(cell, body); record = self.finish()
        for suffix in ('/result', '/raw'):
            self.assertEqual(self.client.get(self.job_path(record, suffix)).status_code, 200)
        with patch.object(self.cell_owner, 'submit', return_value={'status': 'busy', 'reason': 'one_total_handoff_slot'}):
            self.assertEqual(self.client.get(self.job_path(record, '/raw')).status_code, 503)
        # Let the real startup sweep finish before temporarily denying source
        # authority; otherwise it could correctly delete this row mid-fixture.
        deadline = time.monotonic() + 1.5
        self.cell_owner._wake.set()
        while self.cell_owner._reconcile.is_set() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(self.cell_owner._reconcile.is_set(), 'startup reconciliation did not finish')
        # The shared synthetic flag invalidates session and cell authority.
        # Session denial has precedence and must not be relabelled revocation.
        self.cell_owner.authority.valid = False
        try:
            for suffix in ('/result', '/raw'):
                response = self.client.get(self.job_path(record, suffix))
                self.assertEqual((response.status_code, response.json()),
                                 (404, {'detail': 'session_not_found'}))
                self.private(response)
        finally:self.cell_owner.authority.valid = True
        for suffix in ('/result', '/raw'):
            self.assertEqual(self.client.get(self.job_path(record, suffix)).status_code, 200)
        def source_denied(doc, budget, *, publishing):
            budget.check(); return False
        with patch.object(self.cell_owner.authority, 'cell_valid', side_effect=source_denied):
            self.assertTrue(self.cell_owner.authority.session_valid('session', cells.Budget()))
            for suffix in ('/result', '/raw'):
                response = self.client.get(self.job_path(record, suffix))
                self.assertEqual((response.status_code, response.json()),
                                 (409, {'detail': 'cell_revoked'}))
                self.private(response)
        for suffix in ('/result', '/raw'):
            self.assertEqual(self.client.get(self.job_path(record, suffix)).status_code, 200)
        self.assertEqual(self.operation('expire', cell.cell_id)['status'], 'expired')
        for suffix in ('/result', '/raw'):
            response = self.client.get(self.job_path(record, suffix))
            self.assertEqual((response.status_code, response.json()),
                             (409, {'detail': 'cell_binding_changed'}))
            self.private(response)
        self.assertFalse(self.cell_owner._pins); self.assertEqual(self.cache._lease_count, 0)
        self.assertEqual(sum(driver.starts for driver in self.drivers), 1)

    def test_cell_and_rolling_requests_cannot_share_active_slot_or_key(self):
        cell, body = self.ready_cell(); chunk, _, _, artifact = self.capture()
        rolling = {'jobs_epoch': self.owner.jobs_epoch, 'expected_capture_epoch': self.cache.epoch,
            'expected_revision': self.cache._entries[('session', chunk['id'])].revision,
            'support_id': artifact.support.support_id, 'profile': jobs.NAME}
        self.assertEqual(self.post_cell(cell, body, 'same').status_code, 202)
        path = '/sessions/session/native-owned-supports/' + chunk['id'] + '/moss-jobs'
        conflict = self.client.post(path, json=rolling, headers={'Idempotency-Key': 'same'})
        self.assertEqual((conflict.status_code, conflict.json()['detail']), (409, 'idempotency_conflict'))
        busy = self.client.post(path, json=rolling, headers={'Idempotency-Key': 'other'})
        self.assertEqual((busy.status_code, busy.json()['detail']), (409, 'job_slot_occupied'))
        self.assertIsNone(self.cache._dispatch); self.assertEqual(len(self.cell_owner._pins), 1)
        self.finish()
        self.assertEqual(self.client.post(path, json=rolling, headers={'Idempotency-Key': 'other'}).status_code, 202)
        self.assertEqual(len(self.cell_owner._pins), 0); self.assertIsNotNone(self.cache._dispatch)
        record = self.finish()
        self.assertNotIn('cell_binding', self.client.get(self.job_path(record)).json())

    def test_cell_binding_result_does_not_rewrite_native_raw_or_timestamps(self):
        cell, body = self.ready_cell(index=300)
        observed = []; read = self.cell_owner._read
        def observe_validation(op, budget):
            before = tuple(self.cell_owner._pins)
            result = read(op, budget)  # Real owner response, returned untouched.
            if op.kind == 'validate_dispatch':
                observed.append((result, before, tuple(self.cell_owner._pins)))
            return result
        with patch.object(self.cell_owner, '_read', side_effect=observe_validation):
            accepted = self.post_cell(cell, body)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        self.assertTrue(observed, 'HTTP acceptance must cross the genuine owner validation boundary')
        for result, before, after in observed:
            self.assertEqual(result['status'], 'available')
            self.assertEqual(result['detail'], cell.proof)
            self.assertEqual(result['proof_sha256'], hashlib.sha256(cell.proof).hexdigest())
            self.assertEqual(result['sequence'], body['expected_cell_sequence'])
            self.assertEqual(result['revision'], body['expected_cell_revision'])
            self.assertEqual(result['support'], cell.support)
            self.assertEqual(before, after); self.assertNotIn('pin', result)
        record = self.owner._live; original = record['controller']._factory
        raw = b' \n[0][S1]synthetic[1]\t'
        def factory(directory, digest):
            driver = original(directory, digest); driver.raw = raw; return driver
        record['controller']._factory = factory; self.finish()
        response = self.client.get(self.job_path(record, '/raw'))
        self.assertEqual(response.content, raw)
        result = self.client.get(self.job_path(record, '/result')).json()
        binding = result['cell_binding']
        self.assertEqual(binding['origin'], 'durable_cell')
        self.assertEqual(binding['cell_id'], cell.cell_id)
        self.assertEqual(binding['support'], cell.support.to_dict())
        self.assertEqual(binding['proof_sha256'], hashlib.sha256(cell.proof).hexdigest())
        self.assertLessEqual(len(jobs.encoded(binding)), 4096)
        self.assertEqual(self.client.get(binding['proof_url']).content, cell.proof)
        self.assertFalse(result['complete_content']); self.assertIsNone(result['eos'])
        self.assertNotIn('word_alignment', result)

    def test_inflight_cancel_has_pending_and_positive_cleanup_states(self):
        cell, body = self.ready_cell(); self.post_cell(cell, body); record = self.owner._live
        original = record['controller']._factory
        def factory(directory, digest):
            driver = original(directory, digest)
            driver.cleanup_value = {**clean(), 'cleanup_verified': False, 'original_handle_waited_and_reaped': False}
            driver.callback = lambda: self.owner.cancel('session', record['job_id'], self.owner.jobs_epoch)
            return driver
        record['controller']._factory = factory; self.finish()
        pending = self.client.get(self.job_path(record)).json()
        self.assertTrue(pending['publication_cancelled']); self.assertTrue(pending['cleanup_pending'])
        self.assertFalse(pending['process_cleanup_verified']); self.assertTrue(self.cell_owner._pins)
        self.drivers[0].cleanup_value = clean()
        response = self.client.post(self.job_path(record, '/cancel'), json={'jobs_epoch': self.owner.jobs_epoch})
        self.assertEqual(response.status_code, 202)
        record['thread'].run()
        done = self.client.get(self.job_path(record)).json()
        self.assertTrue(done['process_cleanup_verified']); self.assertFalse(done['cleanup_pending'])
        self.assertFalse(done['raw_available']); self.assertEqual(self.drivers[0].starts, 1)

    def test_primary_preemption_routes_wait_for_pin_ack(self):
        cell, body = self.ready_cell(); self.post_cell(cell, body); record = self.owner._live
        intent = self.permit.primary_intent('probe')
        entered = threading.Event(); stop = threading.Event()
        def primary():
            try:intent.wait(stop.is_set); entered.set()
            except InterruptedError:pass
        waiter = threading.Thread(target=primary, name='simulated-cell-primary-intent', daemon=True)
        waiter.start()
        try:
            with patch.object(record['lease'], 'release', return_value=False):
                record['cleanup_deadline'] = time.monotonic()
                self.finish()
                pending = self.client.get(self.job_path(record)).json()
                self.assertTrue(pending['process_cleanup_verified']); self.assertTrue(pending['memory_cleanup_pending'])
                self.assertFalse(entered.wait(.03)); self.assertIsNotNone(self.permit._optional)
            self.client.post(self.job_path(record, '/cancel'), json={'jobs_epoch': self.owner.jobs_epoch})
            record['thread'].run()
            self.assertTrue(entered.wait(1.)); self.assertFalse(self.cell_owner._pins)
        finally:stop.set(); intent.release(); waiter.join(1.)
        self.assertFalse(waiter.is_alive()); self.assertFalse(self.drivers)

    def test_shutdown_orders_admission_jobs_cleanup_then_cell_close(self):
        for failure in (False, True):
            with self.subTest(optional_admission_error=failure), TemporaryDirectory() as directory:
                events = []; original_stop = Worker.stop
                owner = SimpleNamespace(start=lambda: events.append('cell_start'))
                def close_admission():
                    events.append('cell_admission')
                    if failure:raise RuntimeError('explicit simulated admission failure')
                owner.close_admission = close_admission
                captured = SimpleNamespace(owner=owner, authority=SimpleNamespace(owner=owner))
                fake_jobs = SimpleNamespace(permit=None, close_admission=lambda: events.append('jobs_admission'),
                    stop=lambda: (events.append('jobs_stop'), True)[1])
                def make_jobs(store, cache, permit, *, cell_source):
                    fake_jobs.permit = permit; self.assertIsNotNone(cell_source); return fake_jobs
                def stop(worker):events.append('worker_stop'); return original_stop(worker)
                config = Settings(data_dir=directory, disk_reserve_bytes=0, native_owned_cells_enabled=True,
                    native_owned_support_enabled=True, moss_jobs_enabled=True, diarization_backend='nemotron',
                    native_diarization_model='synthetic', native_diarization_library='synthetic',
                    moss_jobs_model_path='/synthetic-model.fixture')
                app = create_app(config)
                with patch.object(capture, 'register', return_value=captured), \
                     patch.object(capture, 'unregister', side_effect=lambda _: (events.append('cell_close'), True)[1]), \
                     patch('audio_backend.native_owned_cell_jobs.CellJobsSource', return_value=object()), \
                     patch.object(jobs, 'MossJobs', side_effect=make_jobs), \
                     patch.object(Worker, 'start', lambda _: None), patch.object(Worker, 'stop', stop):
                    with TestClient(app):pass
                self.assertEqual(events[-5:], ['jobs_admission', 'cell_admission', 'worker_stop', 'jobs_stop', 'cell_close'])
                self.assertIsNone(app.state.instance_lock.file)
                if failure:self.assertIsInstance(app.state.native_owned_cells_shutdown_error, RuntimeError)

    def test_shutdown_unresolved_pin_or_owner_retains_instance_lock(self):
        for unresolved in ('owner', 'jobs'):
            with self.subTest(unresolved=unresolved), TemporaryDirectory() as directory:
                owner = SimpleNamespace(start=lambda: None, close_admission=lambda: None)
                captured = SimpleNamespace(owner=owner, authority=SimpleNamespace(owner=owner))
                fake_jobs = SimpleNamespace(permit=None, close_admission=lambda: None, stop=lambda: unresolved != 'jobs')
                def make_jobs(store, cache, permit, *, cell_source):fake_jobs.permit = permit; return fake_jobs
                config = Settings(data_dir=directory, disk_reserve_bytes=0, native_owned_cells_enabled=True,
                    native_owned_support_enabled=True, moss_jobs_enabled=True, diarization_backend='nemotron',
                    native_diarization_model='synthetic', native_diarization_library='synthetic',
                    moss_jobs_model_path='/synthetic-model.fixture')
                app = create_app(config)
                with patch.object(capture, 'register', return_value=captured), \
                     patch.object(capture, 'unregister', return_value=unresolved != 'owner'), \
                     patch('audio_backend.native_owned_cell_jobs.CellJobsSource', return_value=object()), \
                     patch.object(jobs, 'MossJobs', side_effect=make_jobs), patch.object(Worker, 'start', lambda _: None):
                    with self.assertRaisesRegex(RuntimeError, 'Shutdown blocked'):
                        with TestClient(app):pass
                self.assertTrue(app.state.shutdown_blocked); self.assertIsNotNone(app.state.instance_lock.file)
                self.assertIs(app.state.worker.native_owned_cells, captured)
                app.state.instance_lock.release()  # Explicit no-process fake-owner teardown only.

    def test_on_off_restart_recovers_cells_but_never_adopts_old_jobs(self):
        cell, body = self.ready_cell(); old_epoch = self.owner.jobs_epoch
        self.post_cell(cell, body); self.finish(); self.assertTrue(self.owner.stop())
        self.assertTrue(self.cell_owner.close())
        authority = self.cell_owner.authority
        self.cell_owner = cells.CellStore(self.cell_owner.directory, authority, enabled=True,
                                         qualification=simulated_profile(self.root))
        authority.owner = self.cell_owner; self.cell_owner.start()
        self.assertTrue(self.cell_owner._ready.wait(3.))
        self.source = CellJobsSource(self.cell_owner, self.store)
        self.permit = NativeExecutionPermit(self.store.settings, sampler=lambda _: sample())
        factory = self.owner._driver_factory
        self.owner = jobs.MossJobs(self.store, self.cache, self.permit, cell_source=self.source,
            clock=self.clock, _driver_factory=factory, _thread_factory=ManualThread)
        self.assertNotEqual(old_epoch, self.owner.jobs_epoch)
        self.assertEqual(self.client.get(self.cell_path(cell)).content, cell.proof)
        self.assertFalse(self.owner._records); self.assertEqual(len(self.drivers), 1)
        self.assertEqual(self.post_cell(cell, body).status_code, 409)
        with patch.object(self.store.settings, 'native_owned_cells_enabled', False):
            self.assertEqual(self.client.get(self.cell_path()).status_code, 409)
        with TemporaryDirectory() as directory:
            slot = jobs.create_namespace(Path(directory)); (slot / 'owner.json').write_bytes(b'{}')
            for enabled in (False, True):
                config = Settings(data_dir=directory, disk_reserve_bytes=0, moss_jobs_enabled=enabled,
                    native_owned_support_enabled=enabled, diarization_backend='nemotron',
                    native_diarization_model='synthetic', native_diarization_library='synthetic',
                    moss_jobs_model_path='/synthetic-model.fixture' if enabled else None)
                with self.assertRaises(RuntimeError):create_app(config)

    def test_state_and_error_projections_are_bounded_and_private(self):
        cell, body = self.ready_cell(); self.post_cell(cell, body); record = self.finish()
        for path in (self.cell_path(), self.cell_path(cell), self.job_path(record), '/sessions/session/moss-jobs'):
            response = self.client.get(path); self.private(response)
            self.assertLessEqual(len(response.content), 65536)
            self.assertNotIn(str(self.root), response.text); self.assertNotIn('"pid"', response.text)
        with patch.object(self.owner, 'cell_detail', side_effect=RuntimeError('PRIVATE/PATH/TRANSCRIPT')):
            response = self.client.get(self.cell_path(cell))
            self.assertEqual(response.status_code, 503); self.private(response)
            self.assertEqual(response.json(), {'detail': 'cells_unavailable'})
        with patch.object(self.cell_owner, 'submit', return_value={'status': 'busy', 'reason': 'private_unknown_reason'}):
            response = self.client.get(self.cell_path())
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {'detail': 'cell_owner_unavailable'})
        metadata = self.owner.cell_detail('session', cell.cell_id); oversized = b'x' * 65537
        with patch.object(self.owner, 'cell_detail', return_value={**metadata, 'proof': oversized,
                          'proof_sha256': hashlib.sha256(oversized).hexdigest()}):
            response = self.client.get(self.cell_path(cell))
            self.assertEqual((response.status_code, response.json()['detail']), (503, 'cell_response_capacity'))
        self.assertEqual(self.cache._lease_count, 0)


    def test_seam_parser_accepts_only_exact_old_or_literal_extended_shape(self):
        cell, body = self.ready_composite(); legacy = dict(body); legacy.pop('coverage_intent')
        self.assertEqual(http.parse_body(jobs.encoded(legacy), 'cell_submit'), legacy)
        self.assertEqual(http.parse_body(jobs.encoded(body), 'cell_submit'), body)
        self.assertLessEqual(len(jobs.encoded(body)), 1024)
        variants = ({**body, 'coverage_intent':None}, {**body, 'coverage_intent':True},
                    {**body, 'coverage_intent':{}}, {**body, 'coverage_intent':'new_primary_audio'},
                    {**body, 'extra':0}, {'jobs_epoch':body['jobs_epoch'], 'coverage_intent':'overlapping_diagnostic'})
        with patch.object(self.owner, 'submit_cell', side_effect=AssertionError('reject before manager')):
            for request in variants:
                response = self.post_cell(cell, request)
                self.assertEqual((response.status_code, response.json()['detail']), (409, 'invalid_body'))
                self.private(response)
            raw = jobs.encoded(body)
            duplicate = raw[:-1] + b',"coverage_intent":"overlapping_diagnostic"}'
            response = self.client.post(self.cell_path(cell) + '/moss-jobs', content=duplicate,
                headers={'Content-Type':'application/json', 'Idempotency-Key':'cell'})
            self.assertEqual((response.status_code, response.json()['detail']), (409, 'invalid_body'))
        for kind in ('submit', 'cancel'):
            with self.assertRaises(ReadError):http.parse_body(jobs.encoded(body), kind)
        self.assertFalse(self.cell_owner._pins); self.assertFalse(self.owner._records); self.assertFalse(self.drivers)

    def test_seam_http_intent_rejection_is_target_version_scoped_and_drained(self):
        composite, body = self.ready_composite(); missing = dict(body); missing.pop('coverage_intent')
        response = self.post_cell(composite, missing)
        self.assertEqual((response.status_code, response.json()['detail']), (409, 'coverage_intent_required'))
        self.private(response); self.assertFalse(self.source.cleanup_pending); self.assertFalse(self.cell_owner._pins)
        ordinary, legacy = self.ready_cell()
        response = self.post_cell(ordinary, {**legacy, 'coverage_intent':'overlapping_diagnostic'})
        self.assertEqual((response.status_code, response.json()['detail']), (409, 'coverage_intent_not_allowed'))
        self.private(response); self.assertFalse(self.source.cleanup_pending); self.assertFalse(self.cell_owner._pins)
        self.assertFalse(self.owner._records); self.assertFalse(self.owner._keys); self.assertFalse(self.drivers)
        response = self.post_cell(composite, body)
        self.assertEqual(response.status_code, 202, response.text); self.finish()
        self.assertEqual(sum(driver.starts for driver in self.drivers), 1)

    def test_seam_http_coverage_is_exact_in_list_detail_job_and_result(self):
        cell, body = self.ready_composite(); expected = json.loads(cell.proof)['coverage']
        before = tuple(self.db.execute('SELECT status,next_offset,metadata FROM chunks').fetchone())
        listing = self.client.get(self.cell_path()); self.assertEqual(listing.status_code, 200)
        row = next(item for item in listing.json()['cells'] if item['cell_id'] == cell.cell_id)
        self.assertEqual(row['coverage'], expected)
        detail = self.client.get(self.cell_path(cell)); self.assertEqual(detail.content, cell.proof)
        self.assertEqual(detail.json()['coverage'], expected)
        self.assertEqual(detail.json()['publication_anchor']['chunk_id'], 'right')
        self.assertEqual(detail.json()['support']['core_end_frame'], 32)
        response = self.post_cell(cell, body); self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()['cell_binding']['coverage'], expected)
        record = self.finish()
        summary = self.client.get(self.job_path(record)); result = self.client.get(self.job_path(record, '/result'))
        for projection in (summary.json(), result.json()):
            self.assertEqual(projection['cell_binding']['coverage'], expected)
            self.assertLessEqual(len(jobs.encoded(projection['cell_binding'])), 4096)
        raw = self.client.get(self.job_path(record, '/raw'))
        self.assertEqual(raw.content, b'[0][S1]synthetic[1]')
        self.assertFalse(result.json()['complete_content']); self.assertIsNone(result.json()['eos'])
        for observed in (listing, detail, response, summary, result, raw):self.private(observed)
        self.assertFalse(expected['adds_primary_audio']); self.assertFalse(expected['automatic_dispatch'])
        self.assertEqual(before, tuple(self.db.execute('SELECT status,next_offset,metadata FROM chunks').fetchone()))
        self.assertFalse(self.cell_owner._pins); self.assertEqual(sum(driver.starts for driver in self.drivers), 1)

    def test_seam_http_exact_repeat_has_one_job_pin_and_dispatch(self):
        cell, body = self.ready_composite(); first = self.post_cell(cell, body)
        self.assertEqual(first.status_code, 202, first.text)
        with patch.object(self.source, 'acquire_dispatch', side_effect=AssertionError('no second pin acquisition')):
            second = self.post_cell(cell, body)
            self.assertEqual(second.status_code, 200); self.assertEqual(second.json()['job_id'], first.json()['job_id'])
            missing = dict(body); missing.pop('coverage_intent')
            conflict = self.post_cell(cell, missing)
            self.assertEqual((conflict.status_code, conflict.json()['detail']), (409, 'idempotency_conflict'))
        self.assertEqual(len(self.cell_owner._pins), 1); self.assertEqual(len(self.owner._records), 1)
        self.finish()
        ordinary, legacy = self.ready_cell()
        conflict = self.post_cell(ordinary, legacy)
        self.assertEqual((conflict.status_code, conflict.json()['detail']), (409, 'idempotency_conflict'))
        with patch.object(self.source, 'acquire_dispatch', side_effect=AssertionError('no postcompletion reacquisition')):
            repeat = self.post_cell(cell, body)
            self.assertEqual(repeat.status_code, 200); self.assertEqual(repeat.json()['job_id'], first.json()['job_id'])
        self.assertEqual(sum(driver.starts for driver in self.drivers), 1); self.assertFalse(self.cell_owner._pins)

    def test_seam_off_restart_exposes_retained_coverage_with_new_jobs_epoch_only(self):
        cell, body = self.ready_composite(); self.post_cell(cell, body); self.finish()
        self.assertTrue(self.owner.stop()); self.assertTrue(self.cell_owner.close())
        self.cell_owner = cells.CellStore(self.cell_owner.directory, self.authority, enabled=True,
            seam_enabled=False, qualification=simulated_profile(self.root), instance_lock=object())
        self.authority.owner = self.cell_owner; self.cell_owner.start(); self.assertTrue(self.cell_owner._ready.wait(3.))
        self.source = CellJobsSource(self.cell_owner, self.store)
        self.permit = NativeExecutionPermit(self.store.settings, sampler=lambda _: sample())
        factory = self.owner._driver_factory
        self.owner = jobs.MossJobs(self.store, self.cache, self.permit, cell_source=self.source,
            clock=self.clock, _driver_factory=factory, _thread_factory=ManualThread)
        self.assertNotEqual(self.owner.jobs_epoch, body['jobs_epoch']); self.assertFalse(self.owner._records)
        self.assertEqual(self.client.get(self.cell_path(cell)).content, cell.proof)
        self.assertEqual(self.client.get(self.cell_path()).json()['cells'][0]['coverage'], json.loads(cell.proof)['coverage'])
        self.assertEqual(self.post_cell(cell, body).status_code, 409)
        self.assertEqual(len(self.drivers), 1)
        current = {**body, 'jobs_epoch':self.owner.jobs_epoch}
        self.assertEqual(self.post_cell(cell, current).status_code, 202); self.finish()
        self.assertEqual(sum(driver.starts for driver in self.drivers), 2)
        with patch.object(self.store.settings, 'native_owned_cells_enabled', False):
            self.assertEqual(self.client.get(self.cell_path(cell)).status_code, 409)
        self.assertFalse(self.cell_owner._pins)
