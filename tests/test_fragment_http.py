"""Model-free HTTP integration. Injected providers never load neural libraries."""
from dataclasses import asdict
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path
import shutil
import threading
import wave

import pytest
from fastapi.testclient import TestClient

from audio_backend.app import create_app
from audio_backend.settings import Settings
from audio_backend.fragment_http import SQLiteEvidenceStore, HTTPRuntimeStore
from audio_backend.fragment_runtime_contracts import NativeStatus, RuntimePolicy, StaleWork
from audio_backend.fragment_model_bridges import AnalyzerBridge, NativeFactory, CallBudget

RATE = 16000


def wav(frames=RATE, channels=1, rate=RATE, offset=0):
    out = io.BytesIO()
    with wave.open(out, 'wb') as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        # Exact periodic PCM is deliberately NOT treated as overlap evidence.
        stream.writeframes((b'\x12\x34' * frames) * channels)
    return out.getvalue()


class Native:
    fingerprint = 'http-fake-native-v1'

    def __init__(self, factory):
        self.factory = factory
        self.provenance = {'test_only': True}
        self.finished = False

    def reset(self, key, start):
        self.key, self.start, self.end = key, start, start
        self.factory.log.append(('reset', list(key), start))

    def push(self, pcm):
        assert pcm.start == self.end and not self.finished
        self.end = pcm.end
        self.factory.log.append(('push', list(self.key), pcm.start, pcm.end, pcm.pcm_sha256))

    def status(self):
        turns = tuple({'start_frame': self.start, 'end_frame': self.end, 'speaker': local, 'instance_id': local}
                      for local in ('a', 'b')) if self.start < self.end else ()
        return NativeStatus(self.end, self.end, self.end, self.finished, turns)

    def finish(self, control):
        assert not self.finished
        self.factory.log.append(('finish', list(self.key), control))
        self.finished = True
        return self.status()

    def evidence(self):
        return {'key': list(self.key), 'status': asdict(self.status()), 'calls': []}

    def close(self):
        pass


class RawAnalyzer:
    def __init__(self, owner):
        self.owner = owner

    def analyze(self, path, **kwargs):
        with wave.open(path) as source:
            seconds = source.getnframes()/RATE
        end = min(.2, seconds/2)
        raw = {'segments': [{'speaker_local_id': 'a', 'text': ' hello', 'timestamp_kind': 'fake_native_support',
                            '_units': [{'start': 0., 'end': end, 'ownership_anchor': end,
                                        'text': ' hello', 'raw_text': ' hello', 'confidence': .73}]}],
               'events': [{'start': 0., 'end': seconds, 'label': 'test_sound', 'score': .37,
                           'source_label': 'fake_class', 'time_precision': 'window_support'}],
               'metadata': {'asr_backend': 'model_free_fake'}}
        if self.owner.mutate_raw:
            self.owner.mutate_raw(raw)
        return raw

    def close(self):
        pass


class Guard:
    def check(self, *args):
        pass


class Factory:
    policy = RuntimePolicy(native_fingerprint=Native.fingerprint, analyzer_fingerprint='http-fake-analyzer-v1')

    def __init__(self):
        self.log = []
        self.mutate_raw = None
        self.fail_close = ()
        self.close_exception = RuntimeError
        self.close_attempts = []
        self.closed = threading.Event()

    def __call__(self, context):
        self.last_context = context
        budget = CallBudget({'analyzer': {'calls': 1000, 'frames': 100_000_000}})
        analyzer = AnalyzerBridge(RawAnalyzer(self), fingerprint=self.policy.analyzer_fingerprint,
            evidence_store=context.evidence_store, temp_root=context.temp_root, verify=lambda: {},
            budget=budget, guard=Guard(), provenance={'test_only': True}, source_bindings={})
        native = NativeFactory(lambda: Native(self))
        close_analyzer, close_native = analyzer.close, native.close
        def aclose():
            self.close_attempts.append('analyzer')
            close_analyzer()
            if 'analyzer' in self.fail_close:
                raise self.close_exception('injected analyzer close failure')
        def nclose():
            live = native.current is not None
            close_native()
            if live:
                self.close_attempts.append('native')
                self.closed.set()
                if 'native' in self.fail_close:
                    raise self.close_exception('injected native close failure')
        analyzer.close, native.close = aclose, nclose
        return analyzer, native


@pytest.fixture
def env(tmp_path):
    factory = Factory()
    settings = Settings(data_dir=tmp_path/'data', disk_reserve_bytes=0, start_worker=False,
                        process_memory_limit_bytes=384*1024**2)
    app = create_app(settings, fragment_provider_factory=factory)
    with TestClient(app) as client:
        yield app, client, factory
    shutil.rmtree(tmp_path)


def session(client):
    response = client.post('/sessions', json={'processing_mode': 'fragment_pcm16_v1'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def upload(client, sid, data=None, *, key='one', final=False, stream='capture', start=None):
    params = {'stream_id': stream, 'stream_final': final}
    if start is not None:
        params['timeline_start'] = start
    return client.put(f'/sessions/{sid}/chunks', params=params,
                      headers={'Idempotency-Key': key}, content=wav() if data is None else data)


def rows(app, query, params=()):
    with app.state.store.db() as db:
        return [dict(row) for row in db.execute(query, params)]


def process(app):
    assert app.state.fragments.process_next()


def test_opt_in_fail_closed_and_legacy_unchanged(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0))
    with TestClient(app) as client:
        assert client.post('/sessions', json={'processing_mode': 'fragment_pcm16_v1'}).status_code == 409
        assert client.post('/sessions', json={}).status_code == 201
        assert client.get('/health').json()['fragment_pcm16_v1']['configured'] is False
    shutil.rmtree(tmp_path)


def test_finalized_http_projection_overlap_and_identity_disabled(env):
    app, client, factory = env
    sid = session(client)
    response = upload(client, sid)
    assert response.status_code == 202, response.text
    cid = response.json()['chunk']['id']
    assert app.state.store.claim() is None
    assert not app.state.fragments.process_next()
    assert not rows(app, 'SELECT * FROM fragment_runs')
    assert client.post(f'/sessions/{sid}/streams/capture/finish').status_code == 200
    process(app)
    assert client.get(f'/chunks/{cid}').json()['status'] == 'completed', client.get(f'/chunks/{cid}').json()['error']
    segments = client.get(f'/sessions/{sid}/segments').json()['segments']
    assert len(segments) == 1 and segments[0]['text'] == 'hello'
    assert segments[0]['confidence'] == .73 and segments[0]['speaker_id'] is None
    assert segments[0]['speaker_local_id'].endswith(':a')
    assert not segments[0]['enrollment_available']
    assert segments[0]['projection_version'] == 'fragment-http-public-v1'
    events = client.get(f'/sessions/{sid}/events').json()['events']
    assert events[0]['score'] == .37 and events[0]['start'] == 0 and events[0]['end'] == 1
    turns = client.get(f'/sessions/{sid}/speaker-turns').json()['speaker_turns']
    assert len(turns) == 2 and all(turn['overlap'] for turn in turns)
    assert len({turn['source_track_id'] for turn in turns}) == 2
    assert not rows(app, 'SELECT * FROM speaker_observations')
    assert not rows(app, 'SELECT * FROM speakers')
    assert client.post(f'/sessions/{sid}/speakers', json={'name': 'Person', 'source_segment_id': segments[0]['id']}).status_code == 409
    assert client.patch(f"/sessions/{sid}/segments/{segments[0]['id']}", json={'speaker_id': None}).status_code == 409
    assert rows(app, 'SELECT acknowledged FROM fragment_controls')[0]['acknowledged'] == 1
    assert len([x for x in factory.log if x[0] == 'finish']) == 1


def test_duplicate_receipts_and_finish_are_exactly_once(env):
    app, client, factory = env
    sid = session(client)
    first = upload(client, sid, final=True)
    assert first.status_code == 202, first.text
    assert upload(client, sid, final=True).json()['duplicate']
    assert upload(client, sid, final=False).status_code == 409
    assert upload(client, sid, wav(2*RATE), final=True).status_code == 409
    assert upload(client, sid, key='two').status_code == 409
    for _ in range(2):
        assert client.post(f'/sessions/{sid}/streams/capture/finish').status_code == 200
    process(app)
    for _ in range(2):
        assert client.post(f'/sessions/{sid}/streams/capture/finish').status_code == 200
    assert len(rows(app, 'SELECT * FROM fragment_plans')) == 1
    assert len(rows(app, 'SELECT * FROM fragment_core_commits')) == 1
    assert not app.state.fragments.process_next()


@pytest.mark.parametrize('frames', [1, 30*RATE, 30*RATE+1, 300*RATE, 300*RATE+1])
def test_true_eos_exact_edges(env, frames):
    app, client, factory = env
    sid = session(client)
    assert upload(client, sid, wav(frames), final=True).status_code == 202
    process(app)
    run = rows(app, 'SELECT * FROM fragment_runs')[0]
    assert run['next_frame'] == frames and run['status'] == 'completed'
    assert len([x for x in factory.log if x[0] == 'finish']) == 1


@pytest.mark.parametrize('point', ['seal.after_plan', 'core.after_output', 'ack.after_control', 'public.after_rows', 'core.before_commit'])
def test_fault_atomicity_and_retry(env, point):
    app, client, factory = env
    sid = session(client)
    cid = upload(client, sid).json()['chunk']['id']
    fired = []
    def fault(value):
        if value == point and not fired:
            fired.append(value)
            raise ValueError('injected transaction failure')
    app.state.fragments.fault = fault
    finish = client.post(f'/sessions/{sid}/streams/capture/finish')
    if point == 'seal.after_plan':
        assert finish.status_code == 409
        assert not rows(app, 'SELECT * FROM fragment_plans')
        assert rows(app, 'SELECT state FROM fragment_http_sessions')[0]['state'] == 'collecting'
        assert client.post(f'/sessions/{sid}/streams/capture/finish').status_code == 200
    else:
        assert finish.status_code == 200
        process(app)
        assert client.get(f'/chunks/{cid}').json()['status'] == 'failed'
        assert not rows(app, 'SELECT * FROM fragment_core_commits')
        assert not rows(app, 'SELECT * FROM segments')
        assert not rows(app, 'SELECT * FROM events')
        assert rows(app, 'SELECT next_frame FROM fragment_runs')[0]['next_frame'] == 0
        assert rows(app, 'SELECT acknowledged FROM fragment_controls')[0]['acknowledged'] == 0
        assert client.post(f'/chunks/{cid}/retry').status_code == 200
    process(app)
    assert client.get(f'/chunks/{cid}').json()['status'] == 'completed', client.get(f'/chunks/{cid}').json()['error']
    assert len(rows(app, 'SELECT * FROM segments')) == 1


def test_whole_and_fragments_same_pcm_windows(env):
    app, client, factory = env
    hashes = []
    coverage = []
    for parts in ([65*RATE+1], [13*RATE, 22*RATE+17, 30*RATE-16]):
        sid = session(client)
        for i, frames in enumerate(parts):
            response = upload(client, sid, wav(frames), key=str(i), final=i == len(parts)-1)
            assert response.status_code == 202, response.text
        process(app)
        payloads = [json.loads(x['payload']) for x in rows(app, 'SELECT payload FROM fragment_core_commits WHERE run_id=? ORDER BY core_start', (sid,))]
        hashes.append([(p['window'], p['pcm_hash']) for p in payloads])
        coverage.append({endpoint: [{key: item[key] for key in fields} for item in client.get(
            f'/sessions/{sid}/{endpoint}').json()[endpoint.replace('-', '_')]]
            for endpoint, fields in [('segments', ('start','end','text','confidence')),
                                     ('events', ('start','end','label','score')),
                                     ('speaker-turns', ('start','end','overlap'))]})
        assert all(not x['aliases'] for x in [json.loads(rows(app, 'SELECT plan FROM fragment_plans WHERE run_id=?', (sid,))[0]['plan'])])
    assert hashes[0] == hashes[1]
    assert coverage[0] == coverage[1]


def test_malformed_format_mutation_and_metadata(env):
    app, client, factory = env
    sid = session(client)
    for data in (b'bad', wav(channels=2), wav(rate=8000), wav()[:-3]):
        assert upload(client, sid, data, final=True).status_code == 422
    assert upload(client, sid, final=True, start=1).status_code == 409
    cid = upload(client, sid).json()['chunk']['id']
    path = Path(rows(app, 'SELECT path FROM chunks WHERE id=?', (cid,))[0]['path'])
    with path.open('r+b') as out:
        out.seek(50); out.write(b'\x00\x00')
    assert client.post(f'/sessions/{sid}/streams/capture/finish').status_code == 409
    assert not rows(app, 'SELECT * FROM fragment_plans')


def test_missing_event_score_fails_closed_with_raw_retained(env):
    app, client, factory = env
    factory.mutate_raw = lambda raw: raw['events'][0].pop('score')
    sid = session(client)
    cid = upload(client, sid, final=True).json()['chunk']['id']
    process(app)
    assert client.get(f'/chunks/{cid}').json()['status'] == 'failed'
    assert not rows(app, 'SELECT * FROM segments')
    assert not rows(app, 'SELECT * FROM events')
    assert rows(app, 'SELECT * FROM fragment_http_evidence')


def test_pause_cancel_fence_never_eos_and_delete_erases_everything(env):
    app, client, factory = env
    sid = session(client)
    cid = upload(client, sid, final=True).json()['chunk']['id']
    fired = []
    def pause(point):
        if point == 'core.after_analysis' and not fired:
            fired.append(point)
            app.state.fragments.control(cid, 'pause')
    app.state.fragments.fault = pause
    process(app)
    assert client.get(f'/chunks/{cid}').json()['status'] == 'paused'
    assert not rows(app, 'SELECT * FROM fragment_core_commits')
    assert rows(app, 'SELECT acknowledged FROM fragment_controls')[0]['acknowledged'] == 0
    assert client.post(f'/chunks/{cid}/cancel').status_code == 200
    assert rows(app, 'SELECT status FROM fragment_runs')[0]['status'] == 'cancelled'
    assert client.post(f'/sessions/{sid}/streams/capture/finish').status_code == 409
    assert client.delete(f'/sessions/{sid}').status_code == 200
    for table in ('fragment_http_sessions', 'fragment_http_sources', 'fragment_http_evidence',
                  'fragment_plans', 'fragment_runs', 'fragment_controls', 'fragment_core_commits', 'result_changes'):
        assert not rows(app, f'SELECT * FROM {table}')
    with pytest.raises(StaleWork):
        factory.last_context.evidence_store.put({'late': 'must not resurrect erased evidence'})
    assert client.get(f'/sessions/{sid}/segments').status_code == 404


def test_cancel_collecting_does_not_set_stream_final_even_after_restart(env):
    app, client, factory = env
    sid = session(client)
    cid = upload(client, sid).json()['chunk']['id']
    assert client.post(f'/chunks/{cid}/cancel').status_code == 200
    app.state.store.recover(); app.state.fragments.recover()
    assert not client.get(f'/chunks/{cid}').json()['stream_final']
    assert not rows(app, 'SELECT * FROM fragment_controls')


def test_concurrent_finish_and_read_cursor_pagination(env):
    app, client, factory = env
    sid = session(client)
    upload(client, sid, wav(65*RATE))
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: app.state.fragments.finish(sid, 'capture'), range(2)))
    process(app)
    assert len(rows(app, 'SELECT * FROM fragment_plans')) == 1
    # Reads use materialized indexed rows: they do not scan or decode core commits.
    with app.state.store.db() as db:
        db.execute('ALTER TABLE fragment_core_commits RENAME TO hidden_commits')
    for endpoint in ('segments', 'speaker-turns', 'events', 'changes'):
        after = 0
        seen = []
        while True:
            result = client.get(f'/sessions/{sid}/{endpoint}', params={'after': after, 'limit': 1})
            assert result.status_code == 200, result.text
            value = result.json()
            data = value[endpoint.replace('-', '_')]
            if not data:
                break
            seen.append(data[0].get('id', data[0].get('cursor')))
            assert value['next_cursor'] > after
            after = value['next_cursor']
        assert seen and len(seen) == len(set(seen))


def test_pause_collecting_resume_and_completed_erasure(env):
    app, client, factory = env
    sid = session(client)
    cid = upload(client, sid).json()['chunk']['id']
    assert client.post(f'/chunks/{cid}/pause').status_code == 200
    assert client.post(f'/sessions/{sid}/streams/capture/finish').status_code == 409
    assert upload(client, sid, key='blocked').status_code == 409
    assert client.post(f'/chunks/{cid}/resume').json()['status'] == 'waiting_input'
    assert client.post(f'/sessions/{sid}/streams/capture/finish').status_code == 200
    process(app)
    assert rows(app, 'SELECT * FROM fragment_core_commits')
    with app.state.store.db() as db:
        with pytest.raises(Exception, match='immutable'):
            db.execute('DELETE FROM fragment_core_commits')
    assert client.delete(f'/sessions/{sid}').status_code == 200
    assert client.delete(f'/sessions/{sid}').status_code == 200
    for table in ('segments', 'events', 'speaker_turns', 'fragment_http_evidence', 'fragment_core_commits',
                  'fragment_plans', 'fragment_controls', 'fragment_runs'):
        assert not rows(app, f'SELECT * FROM {table}')
    assert not list((app.state.settings.data_dir/'uploads').glob('*.audio'))


def test_actual_concurrent_pause_resume_and_delete_races(env):
    app, client, factory = env
    entered, release = threading.Event(), threading.Event()
    def hold(raw):
        entered.set()
        assert release.wait(5)
    factory.mutate_raw = hold
    sid = session(client)
    cid = upload(client, sid, final=True).json()['chunk']['id']
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        running = pool.submit(app.state.fragments.process_next)
        assert entered.wait(5)
        assert client.delete(f'/sessions/{sid}').status_code == 409
        assert client.post(f'/chunks/{cid}/pause').status_code == 200
        # Resume cannot let old work commit: generation changed twice.
        assert client.post(f'/chunks/{cid}/resume').status_code == 200
        release.set()
        assert running.result(timeout=5)
    assert not rows(app, 'SELECT * FROM fragment_core_commits')
    factory.mutate_raw = None
    process(app)
    assert len(rows(app, 'SELECT * FROM fragment_core_commits')) == 1
    assert client.delete(f'/sessions/{sid}').status_code == 200


def test_source_mutation_after_seal_rejected_before_publication(env):
    app, client, factory = env
    sid = session(client)
    cid = upload(client, sid, final=True).json()['chunk']['id']
    path = Path(rows(app, 'SELECT path FROM chunks')[0]['path'])
    with path.open('r+b') as source:
        source.seek(50); source.write(b'\x00\x00')
    process(app)
    assert client.get(f'/chunks/{cid}').json()['status'] == 'failed'
    assert not rows(app, 'SELECT * FROM fragment_core_commits')
    assert not rows(app, 'SELECT * FROM segments')


def test_durable_300s_restart_uses_new_epoch_bounded_preroll(env):
    app, client, factory = env
    sid = session(client)
    cid = upload(client, sid, wav(330*RATE+1), final=True).json()['chunk']['id']
    class Crash(BaseException):
        pass
    crashed = []
    def crash(point):
        if point == 'core.after_commit' and not crashed:
            run = rows(app, 'SELECT next_frame FROM fragment_runs')[0]
            if run['next_frame'] == 300*RATE:
                crashed.append(point)
                raise Crash()
    app.state.fragments.fault = crash
    with pytest.raises(Crash):
        process(app)
    old = rows(app, 'SELECT window_id,payload_hash FROM fragment_core_commits ORDER BY core_start')
    assert len(old) == 10 and not [x for x in factory.log if x[0] == 'finish']
    app.state.store.recover(); app.state.fragments.recover()
    process(app)
    assert client.get(f'/chunks/{cid}').json()['status'] == 'completed'
    assert old == rows(app, 'SELECT window_id,payload_hash FROM fragment_core_commits ORDER BY core_start LIMIT 10')
    resets = [x for x in factory.log if x[0] == 'reset']
    assert len(resets) == 2 and resets[1][1][2] == 1 and resets[1][2] == 300*RATE-339200
    assert len([x for x in factory.log if x[0] == 'finish']) == 1
    assert rows(app, 'SELECT next_frame FROM fragment_runs')[0]['next_frame'] == 330*RATE+1
    assert rows(app, 'SELECT acknowledged FROM fragment_controls')[0]['acknowledged'] == 1


def test_sealed_provider_policy_mutation_is_rejected(env):
    from dataclasses import replace
    app, client, factory = env
    sid = session(client)
    cid = upload(client, sid, final=True).json()['chunk']['id']
    factory.policy = replace(factory.policy, analyzer_fingerprint='changed-provider')
    process(app)
    assert client.get(f'/chunks/{cid}').json()['status'] == 'failed'
    assert not rows(app, 'SELECT * FROM fragment_core_commits')
    assert not factory.log


def test_duplicate_workers_publish_once(env):
    app, client, factory = env
    sid = session(client)
    upload(client, sid, final=True)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: app.state.fragments.process_next(), range(2)))
    assert sorted(results) == [False, True]
    assert len(rows(app, 'SELECT * FROM fragment_core_commits')) == 1
    assert len(rows(app, 'SELECT * FROM segments')) == 1


@pytest.mark.parametrize('failures', [('analyzer',), ('native',), ('analyzer','native')])
def test_both_provider_cleanup_attempts_and_primary_failure_survive(env, failures):
    app, client, factory = env
    factory.fail_close = failures
    factory.mutate_raw = lambda raw: raw['events'][0].pop('score')
    sid = session(client)
    cid = upload(client, sid, final=True).json()['chunk']['id']
    process(app)
    assert factory.close_attempts == ['analyzer', 'native']
    chunk = client.get(f'/chunks/{cid}').json()
    assert chunk['status'] == 'failed'
    assert 'Invalid genuine evidence event score' in chunk['error']
    assert 'Provider cleanup failed' in chunk['error']
    assert client.get('/health').json()['fragment_pcm16_v1']['cleanup_error']
    assert client.post(f'/chunks/{cid}/retry').status_code == 200
    assert not app.state.fragments.process_next()  # Ownership is unresolved.


def test_worker_remains_alive_after_cleanup_failure(env):
    app, client, factory = env
    factory.fail_close = ('analyzer','native')
    sid = session(client)
    upload(client, sid, final=True)
    app.state.worker.start()
    assert factory.closed.wait(5)
    assert app.state.worker.thread.is_alive()
    assert client.get('/health').json()['worker_alive']
    app.state.worker.stop()
    assert client.delete(f'/sessions/{sid}').status_code == 409
    with pytest.raises(ValueError,match='ownership cleanup'):
        app.state.store.delete_session(sid)
    # A fresh controller cannot treat restarting as cleanup proof.
    from audio_backend.fragment_http import FragmentHTTP
    restarted = FragmentHTTP(app.state.store,app.state.settings,factory)
    assert restarted.cleanup_error and restarted.active_sid == sid
    legacy = app.state.store.create_session('legacy')['id']
    legacy_cid,_ = app.state.store.add_chunk(legacy,'legacy','x','/unused.audio',1.)
    from audio_backend.worker import Worker
    after_restart = Worker(app.state.store,app.state.settings)
    called = []
    def forbid_claim():
        called.append(True)
        raise AssertionError('Cleanup blocker must fence legacy scheduling too')
    app.state.store.claim = forbid_claim
    after_restart.wake.wait = lambda timeout: after_restart.stop_event.set()
    after_restart.loop()
    assert not called and app.state.store.chunk(legacy_cid)['status'] == 'queued'


@pytest.mark.parametrize('explicit', [False, True])
def test_integer_adjacency_ignores_float_sum_roundoff(env, explicit):
    app, client, factory = env
    sid = session(client)
    cursor = 0
    for i, frames in enumerate((17600, 35200, 16000)):
        response = upload(client, sid, wav(frames), key=str(i), final=i == 2,
                          start=cursor/RATE if explicit else None)
        assert response.status_code == 202, response.text
        assert response.json()['chunk']['timeline_start'] == cursor/RATE
        cursor += frames
    process(app)
    assert rows(app, 'SELECT next_frame FROM fragment_runs')[0]['next_frame'] == cursor


def test_raw_event_precision_and_unavailable_candidate_projection(env):
    app, client, factory = env
    def raw_evidence(raw):
        raw['events'][0]['onset_precision_seconds'] = 2.4
        raw['metadata']['event_candidates'] = [{'private_candidate': 'retained'}]
    factory.mutate_raw = raw_evidence
    sid = session(client)
    cid = upload(client, sid, final=True).json()['chunk']['id']
    process(app)
    assert client.get(f'/chunks/{cid}').json()['status'] == 'completed'
    assert client.get(f'/sessions/{sid}/events').json()['events'][0]['onset_precision_seconds'] == 2.4
    for endpoint in ('event-candidates','overlap-candidates'):
        assert client.get(f'/sessions/{sid}/{endpoint}').status_code == 409
    evidence = rows(app, 'SELECT payload FROM fragment_http_evidence')
    assert any('private_candidate' in item['payload'] for item in evidence)


def test_baseexception_cleanup_preserves_original_crash_and_closes_both(env):
    app, client, factory = env
    factory.fail_close = ('analyzer','native')
    factory.close_exception = SystemExit
    sid = session(client)
    upload(client,sid,final=True)
    class PrimaryCrash(BaseException):
        pass
    def crash(point):
        if point == 'core.after_read':
            raise PrimaryCrash('primary failure survives cleanup')
    app.state.fragments.fault = crash
    with pytest.raises(PrimaryCrash,match='primary failure'):
        process(app)
    assert factory.close_attempts == ['analyzer','native']
    assert 'SystemExit' in app.state.fragments.cleanup_error
    assert rows(app,'SELECT cleanup_error FROM fragment_http_sessions')[0]['cleanup_error']


@pytest.mark.parametrize('point', ['cleanup.before_record','cleanup.before_release'])
def test_durable_ownership_marker_survives_cleanup_persistence_failure(env, point):
    app, client, factory = env
    if point == 'cleanup.before_record':
        factory.fail_close = ('analyzer','native')
    sid = session(client)
    upload(client,sid,final=True)
    def fail_write(value):
        if value == point:
            raise OSError('simulated cleanup diagnostic storage failure')
    app.state.fragments.fault = fail_write
    process(app)
    from audio_backend.fragment_http import FragmentHTTP, OWNERSHIP_PENDING
    assert rows(app,'SELECT cleanup_error FROM fragment_http_sessions')[0]['cleanup_error'] == OWNERSHIP_PENDING
    restarted = FragmentHTTP(app.state.store,app.state.settings,factory)
    assert restarted.cleanup_error == OWNERSHIP_PENDING and restarted.active_sid == sid
    assert not restarted.process_next()
    with pytest.raises(ValueError,match='ownership cleanup'):
        app.state.store.delete_session(sid)


def test_lost_terminal_response_keeps_public_ids_and_eos_once(env):
    app, client, factory = env
    sid = session(client)
    cid = upload(client,sid,final=True).json()['chunk']['id']
    fired = []
    def lost_response(point):
        if point == 'core.after_commit' and not fired:
            fired.append(True)
            raise RuntimeError('committed terminal response lost')
    app.state.fragments.fault = lost_response
    process(app)
    original = client.get(f'/sessions/{sid}/segments').json()
    assert client.get(f'/chunks/{cid}').json()['status'] == 'completed'
    assert client.post(f'/sessions/{sid}/streams/capture/finish').status_code == 200
    assert upload(client,sid,final=True).json()['duplicate']
    assert not app.state.fragments.process_next()
    assert client.get(f'/sessions/{sid}/segments').json() == original
    assert len([x for x in factory.log if x[0] == 'finish']) == 1
    assert rows(app,'SELECT acknowledged FROM fragment_controls')[0]['acknowledged'] == 1
