"""Exact opt-in primary-only path with synthetic producer edges, no models.

The real app, recording ingestion, Worker loop/handoff, bounded capture/SQLite,
permit, ProbeOperation, owner and tiny isolated comparator remain in use.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from audio_backend import moss_automatic as auto
from audio_backend import native_owned_cell_capture as capture_module
from audio_backend import native_owned_cell_store as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_primary_provenance as provenance
from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_probe_runner as runner
from audio_backend.app import create_app
from audio_backend.native_owned_cells import RATE, Unavailable
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.settings import Settings
from audio_backend.storage import _ClaimDeferred
from audio_backend.worker import Worker
from test_moss_handy_controller import FakeDriver, clean
from test_moss_handy_jobs import ManualThread
from test_native_owned_cell_store import simulated_profile
from test_native_owned_support_api import FakeAnalyzer, FakeNative
from test_recording_speaker_history_integration import (
    _analyze, _turns, _wav, _extract_history, _comparison_sample, ingest_recording,
    _cohort, _counts, _observations, _primary_rows, _passive_saved,
    _passive_without_positive,
)
from test_recording_speaker_links import vector


def primary_options(root):
    return dict(data_dir=Path(root), disk_reserve_bytes=0, start_worker=True,
        window_seconds=10.5, context_seconds=0., asr_backend='sensevoice',
        diarization_backend='nemotron', inference_device='cpu', overlap_backend='none',
        native_diarization_model='synthetic-native-model', native_diarization_library='synthetic-native-library',
        native_owned_support_enabled=True, native_owned_cells_enabled=True,
        native_owned_core_seams_enabled=True, native_owned_bounded_core_support_enabled=True,
        lexical_evidence_enabled=True, recording_speaker_links_enabled=True,
        recording_primary_observation_policy=history.POLICY,
        recording_speaker_model_path=str(Path(root)/'ABSENT-PRIMARY-SPEAKER-MODEL'),
        moss_jobs_enabled=False, moss_automatic_enabled=False, moss_jobs_model_path=None)


def _forbidden(*args, **kwargs):
    raise AssertionError('primary-only path attempted MOSS work')


@pytest.fixture
def primary_app_factory(tmp_path, monkeypatch):
    """Construct Settings correctly before app creation; no post-init rewrite."""
    monkeypatch.setattr(capture_module, 'qualified_runtime', simulated_profile)
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    monkeypatch.setattr(Worker, 'extract_pcm', _extract_history)
    monkeypatch.setattr(FakeAnalyzer, 'analyze', _analyze)
    monkeypatch.setattr('test_native_owned_support_api.synthetic_turns', _turns)
    monkeypatch.setattr('test_native_owned_support_api.wav_bytes', _wav)
    monkeypatch.setattr('audio_backend.worker.check_resources', lambda *a, **kw: None)

    @contextmanager
    def opened(*, mode='primary', sid=None, root=None):
        directory=Path(root or tmp_path)
        if mode=='passive':
            options=dict(data_dir=directory, disk_reserve_bytes=0, start_worker=False,
                recording_speaker_history_read_enabled=True)
        else:
            options=primary_options(directory)
            if mode=='combined':
                options.update(moss_jobs_enabled=True, moss_automatic_enabled=True,
                    moss_jobs_model_path=str(directory/'ABSENT-MOSS-MODEL'))
            else:assert mode=='primary'
        app=create_app(Settings(**options), analyzer_factory=FakeAnalyzer)
        with TestClient(app) as client:
            worker=app.state.worker; jobs=app.state.moss_jobs; capture=worker.native_owned_cells
            h=SimpleNamespace(app=app, client=client, worker=worker, store=app.state.store,
                capture=capture, owner=capture.owner if capture else None, jobs=jobs,
                automatic=app.state.moss_automatic, sid=sid, probes=[], natives=[], drivers=[])
            if capture is not None:assert capture.owner._ready.wait(3.),capture.health()
            if h.sid is None:h.sid=client.post('/sessions',json={'name':'synthetic primary-only'}).json()['id']
            class QueryBoundNative(FakeNative):
                def request(self,action,stop,**kwargs):
                    if action!='turns':return super().request(action,stop,**kwargs)
                    self.calls.append(action)
                    turns=[]
                    for turn in _turns(self.frames,self.epoch):
                        a,b=max(kwargs['start'],turn['start']),min(kwargs['end'],turn['end'])
                        if a<b:turns.append(dict(turn,start=a,end=b))
                    result={'turns':turns};self.response_observer(result);return result
            def native_factory(settings):
                native=QueryBoundNative(settings);native.response_observer=worker._native_response
                h.natives.append(native);return native
            monkeypatch.setattr(worker,'_native_process_factory',native_factory)
            if jobs is not None:
                jobs._thread_factory=ManualThread
                jobs.permit._sampler=lambda settings:_comparison_sample(h)
                def text_driver(directory,digest):
                    assert mode=='combined'
                    driver=FakeDriver(directory,digest);h.drivers.append(driver);return driver
                jobs._driver_factory=text_driver
                class Probe:
                    def __init__(self,directory,digest):
                        self.directory,self.digest=directory,digest
                        self.starts=self.cleanups=0;self.cleanup_ok=True;h.probes.append(self)
                    def start(self,cancelled):
                        self.starts+=1
                        assert not cancelled.is_set() and h.capture.analyzer is None
                        assert not any(item.granted or item.entered for item in jobs.permit._primary.values())
                        assert not jobs._lock._is_owned()
                        assert jobs.permit._optional_kind in (('automatic_primary_probe','preemptible') if mode=='combined' else ('automatic_primary_probe',))
                        assert not Path(h.store.settings.recording_speaker_model_path).exists()
                    def wait(self,cancelled):
                        raw=(self.directory/'request.json').read_bytes()
                        assert hashlib.sha256(raw).hexdigest()==self.digest and not cancelled.is_set()
                        request=json.loads(raw)
                        assert 1<=len(request['crops'])<=3
                        return {'kind':runner.KIND,'job_id':request['job_id'],
                            'space':runner.expected_space()[0],'space_id':request['space_id'],
                            'compute_attempts':len(request['crops']),'nodes':[{'index':crop['index'],
                            'pcm_sha256':crop['pcm_sha256'],
                            'vector':vector(0.,1.) if len(h.probes)==3 else vector(1.,0.)}
                            for crop in request['crops']]}
                    def cleanup(self,stop):
                        self.cleanups+=1
                        return clean() if self.cleanup_ok else {'cleanup_verified':False}
                jobs._probe_factory=Probe
            try:
                yield h
                assert not getattr(h,'forbidden_calls',[])
            finally:
                if jobs is not None:
                    for item in (jobs._primary_pending,jobs._history_comparison):
                        if item and item['thread'] and item['thread'].is_alive():
                            item['cancelled'].set();item['thread'].run()
                    for probe in h.probes:probe.cleanup_ok=True
                    jobs._stop_primary();jobs._stop_history_comparison()
        assert not worker.thread.is_alive() and all(native.closed for native in h.natives)
        assert capture is None or capture.owner._thread is None or not capture.owner._thread.is_alive()
        assert app.state.instance_lock.file is None
    return opened


def _tripwires(h, monkeypatch):
    h.forbidden_calls=[]
    def forbidden(*args,**kwargs):
        h.forbidden_calls.append('MOSS work')
        raise AssertionError('primary-only path attempted MOSS work')
    for target,name in ((capture_module,'make_cells'),(h.capture,'bounded_prepare'),
            (h.capture,'committed'),(h.capture,'drain_core_seams'),
            (auto,'enqueue'),(auto,'enqueue_bounded'),(auto,'release_wait_pressure'),
            (h.jobs,'submit_automatic'),(h.automatic,'_snapshot'),
            (h.automatic,'_select_key'),(h.automatic,'read_candidate')):
        monkeypatch.setattr(target,name,forbidden)
    submit=h.owner.submit
    def checked(kind,*args,**kwargs):
        if kind in ('publish','publish_auto','publish_core_seam'):forbidden()
        return submit(kind,*args,**kwargs)
    monkeypatch.setattr(h.owner,'submit',checked)


def _handoff(h, *, compare=True):
    """Run only fake threads already admitted by the production handoff."""
    h.worker._automatic_handoff(request=True)
    probe=h.jobs._primary_pending
    if probe and probe['thread'] and probe['thread'].is_alive():
        queued=h.jobs.permit.primary_intent('worker')
        try:
            assert not queued.granted and not queued.entered and not probe['cancelled'].is_set()
            probe['thread'].run()
        finally:queued.release()
    if compare:
        # Production maintenance is reached by the handoff before/after the
        # turn. It may have queued a comparison before the fake probe returned.
        task=h.jobs._history_comparison
        if task and task['thread'] and task['thread'].is_alive():task['thread'].run()


def _idle_until(h, predicate, *, seconds=7., compare=True):
    deadline=time.monotonic()+seconds
    while not predicate():
        assert time.monotonic()<deadline,'primary-only idle maintenance did not settle'
        _handoff(h,compare=compare)
        if not predicate():time.sleep(min(.05,max(0.,h.automatic.next_attempt-time.monotonic())))


def _loop(h,cids,expected_vectors):
    """Real finite Worker loop; fake model/comparison coordinators are drained."""
    handoff=h.worker._automatic_handoff;claim=h.store.claim;deadline=time.monotonic()+15.
    def checked_claim(*args,**kwargs):
        assert time.monotonic()<deadline,'finite Worker loop did not finish'
        return claim(*args,**kwargs)
    def dispatch(*,request=False):
        handoff(request=request)
        pending=h.jobs._primary_pending
        if pending and pending['thread'] and pending['thread'].is_alive():
            pending['thread'].run()
            # Respect the production one-second floor before metadata cleanup.
            time.sleep(max(0.,h.automatic.next_attempt-time.monotonic()))
            handoff(request=True)
        comparison=h.jobs._history_comparison
        if comparison and comparison['thread'] and comparison['thread'].is_alive():comparison['thread'].run()
        if (all(h.store.chunk(cid)['status']=='completed' for cid in cids)
                and _counts(h)['vectors']==expected_vectors and h.jobs._primary_pending is None):
            with h.store.db() as db:
                scope=db.execute(f'SELECT current_slot FROM {history.SCOPES} WHERE scope_seq>0').fetchone()
            if scope and scope[0] is not None:h.worker.stop_event.set()
    with patch.object(h.store,'claim',checked_claim),patch.object(h.worker,'_automatic_handoff',dispatch):
        h.worker.loop()
    h.worker.stop_event.clear()


def _assert_primary_packet(h,cid):
    with h.store.db() as db:
        db.execute('BEGIN')
        rows=db.execute('SELECT id,text,raw_text FROM segments WHERE chunk_id=?',(cid,)).fetchall()
        saved=provenance.read_exact(db,cid=cid,start=0,end=round(h.store.chunk(cid)['duration']*RATE))
        assert saved is not None and len(saved['packet']['refs'])==len(rows)==1
        assert rows[0]['text']==rows[0]['raw_text']=='synthetic'
        assert saved['packet']['refs'][0][0].startswith('p.'+str(rows[0]['id'])+'.')
        compact=db.execute('SELECT body,pending FROM native_core_commits WHERE cid=?',(cid,)).fetchone()
        assert json.loads(compact[0])==saved['packet']['commit'] and compact[1] is None
        assert db.execute(f'SELECT COUNT(*) FROM {auto.TABLE}').fetchone()[0]==0
    return saved


def test_worker_loop_aab_packets_charges_and_revision_current_http(primary_app_factory,monkeypatch):
    with primary_app_factory() as h:
        _tripwires(h,monkeypatch)
        cids=ingest_recording(h,frames=30*RATE)
        _loop(h,cids,3)
        packets=[_assert_primary_packet(h,cid) for cid in cids]
        assert len(h.probes)==3 and all(p.starts==p.cleanups==1 for p in h.probes)
        assert _counts(h)==dict(observations=3,vectors=3,pending=0,candidate_missing=0,attempts=3)
        observations=_observations(h)
        assert len({json.loads(row['header'])['pcm_hash'] for row in observations})==3
        with h.store.db() as db:
            assert all(tuple(row)==(0,0) for row in db.execute(f'SELECT reserved_bytes,staging_bytes FROM {history.ATTEMPTS}'))
            assert db.execute(f'SELECT COUNT(*) FROM {provenance.TABLE}').fetchone()[0]==3
        assert h.jobs._probe_staging_bytes==0 and h.jobs.permit._optional is None
        saved=_passive_saved(h)
        assert [r['status'] for r in saved['annotations']]==['provisional_linked','provisional_linked','source_scoped']
        assert saved['annotations'][0]['provisional_speaker_id']==saved['annotations'][1]['provisional_speaker_id']
        before=h.client.get(f'/sessions/{h.sid}/transcript').json()
        with h.store.db() as db:db.execute("UPDATE segments SET text='changed' WHERE id=(SELECT MIN(id) FROM segments)")
        after=h.client.get(f'/sessions/{h.sid}/transcript').json()
        assert after['revision']!=before['revision']
        assert after['baseline_reading'][0]['text']=='changed'
        annotation=after['baseline_reading'][0].get('recording_speaker_link')
        assert annotation is None or annotation.get('provisional_speaker_id') is None
        assert len(h.probes)==3 and not h.drivers


def test_two_observations_abstain_and_idle_queued_maintenance(primary_app_factory,monkeypatch):
    with primary_app_factory() as h:
        _tripwires(h,monkeypatch)
        cids=ingest_recording(h,frames=20*RATE)
        for cid in cids:
            assert _cohort(h)==cid
            _idle_until(h,lambda:h.jobs._primary_pending is None and
                h.jobs._history_comparison is not None and h.jobs._history_comparison['state']=='cached')
        assert len(h.probes)==2
        saved=_passive_saved(h)
        assert len(saved['annotations'])==2
        assert all(r['status']=='source_scoped' and r['provisional_speaker_id'] is None for r in saved['annotations'])
        assert not h.automatic.current() and h.automatic.coordinator_current()


def _capacity_refusals(primary_app_factory,monkeypatch,tmp_path):
    """Real global row ceilings; cold fillers are metadata, never evidence."""
    assert core.MAX_COMPACTS==2056 and cells.MAX_RECEIPTS==4096
    for capacity in ('compact','receipt'):
      with primary_app_factory(root=tmp_path/capacity) as h:
        _tripwires(h,monkeypatch)
        assert h.owner._slot is None
        if capacity=='receipt':
            # Finite metadata-only occupancy, no cold audio or accepted history.
            # The novel receipt itself must be generated/admitted by ingestion.
            fillers=[]
            for index in range(cells.MAX_RECEIPTS):
                cid=f'synthetic-cold-receipt-{index:05d}'
                receipt=cells.Receipt(h.sid,cid,'c'*64,16,44,76,(27,index+1,76,1,1),0.,None)
                fillers.append((cid,h.sid,receipt.digest,receipt.encoded))
            with sqlite3.connect(h.owner.path,timeout=0) as db:
                assert db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==0
                db.executemany('INSERT INTO sources VALUES(?,?,?,?)',fillers)
                before=list(db.execute('SELECT * FROM sources ORDER BY cid'))
                assert len(before)==cells.MAX_RECEIPTS
        cid,=ingest_recording(h,frames=10*RATE)
        if capacity=='compact':
            # Rows satisfy storage shape/FK only; deliberately no compact proof,
            # provenance, observation, MOSS ordinal or replay authority is made.
            fillers=[(f'synthetic-cold-compact-{i:05d}',h.sid,cid,'synthetic-cold',i,
                b'{}',None,'none','synthetic_capacity_only') for i in range(core.MAX_COMPACTS)]
            with h.store.db() as db:
                db.executemany('INSERT INTO native_core_commits (slot,sid,cid,scope_key,logical_end,body,pending,state,reason) VALUES(?,?,?,?,?,?,?,?,?)',fillers)
                before=[tuple(row) for row in db.execute('SELECT * FROM native_core_commits ORDER BY slot')]
                assert len(before)==core.MAX_COMPACTS
        else:
            with h.store.db() as db:
                row=db.execute('''SELECT receipt_state,native_receipt_status,native_receipt,
                    receipt_id,receipt_attempts FROM recording_work WHERE chunk_id=?''',(cid,)).fetchone()
                assert row[0:2]==('gap','source_receipt_capacity') and row[4]>=1
                actual=cells.Receipt.decode(row[2])
                assert actual.chunk_id==cid and actual.receipt_id==row[3]
            status=h.client.get(f'/sessions/{h.sid}/recordings/synthetic-capture').json()
            assert status['state']=='queued' and status['optional_receipts']['gaps']==1
            assert status['optional_receipts']['durable']==status['optional_receipts']['pending']==0
            assert h.owner.receipt(cid) is None
        assert _cohort(h)==cid
        chunk=h.store.chunk(cid)
        assert chunk['status']=='completed' and chunk['next_offset']==10.
        assert [row['text'] for row in _primary_rows(h)]==['synthetic']
        assert not h.probes and h.jobs._primary_pending is None and h.jobs._probe_staging_bytes==0
        assert _counts(h)==dict(observations=0,vectors=0,pending=0,candidate_missing=0,attempts=0)
        with h.store.db() as db:
            assert db.execute(f'SELECT COUNT(*) FROM {provenance.TABLE}').fetchone()[0]==0
            compacts=[tuple(row) for row in db.execute('SELECT * FROM native_core_commits ORDER BY slot')]
        if capacity=='compact':
            assert chunk['metadata']['native_core_seam']['status']=='core_receipt_row_capacity'
            assert compacts==before
        else:
            assert compacts==[]
            with sqlite3.connect(h.owner.path,timeout=0) as db:
                assert list(db.execute('SELECT * FROM sources ORDER BY cid'))==before
        assert core.MAX_COMPACTS==2056 and cells.MAX_RECEIPTS==4096


@pytest.mark.parametrize('refusal',['capture','capacity','provenance','crop'])
def test_optional_refusal_preserves_committed_text(primary_app_factory,monkeypatch,tmp_path,refusal):
    if refusal=='capacity':
        _capacity_refusals(primary_app_factory,monkeypatch,tmp_path)
        return
    with primary_app_factory() as h:
        _tripwires(h,monkeypatch)
        cid,=ingest_recording(h,frames=10*RATE)
        if refusal=='capture':
            monkeypatch.setattr(h.capture.authority,'original_pcm',lambda *a:(_ for _ in ()).throw(Unavailable('source_read_capacity')))
        elif refusal=='provenance':h.store.settings.recording_primary_provenance_recording_bytes=1
        else:monkeypatch.setattr(h.jobs,'prepare_primary_copy',lambda *a:(_ for _ in ()).throw(ValueError('synthetic optional crop refusal')))
        assert _cohort(h)==cid
        assert h.store.chunk(cid)['status']=='completed' and h.store.chunk(cid)['next_offset']==10.
        assert [row['text'] for row in _primary_rows(h)]==['synthetic']
        assert not h.probes and h.jobs._primary_pending is None and h.jobs._probe_staging_bytes==0
        with h.store.db() as db:
            assert db.execute(f'SELECT COUNT(*) FROM {provenance.TABLE}').fetchone()[0]==int(refusal=='crop')
            assert db.execute(f'SELECT COUNT(*) FROM {history.ATTEMPTS}').fetchone()[0]==0


def test_sql_and_consumed_budget_fail_hard_without_primary_retry(primary_app_factory,monkeypatch,tmp_path):
    for failure in ('sql','budget'):
      with primary_app_factory(root=tmp_path/failure) as h:
        _tripwires(h,monkeypatch)
        cid,=ingest_recording(h,frames=10*RATE)
        if failure=='sql':
            with h.store.db() as db:db.execute("CREATE TRIGGER reject_primary BEFORE INSERT ON segments BEGIN SELECT RAISE(ABORT,'synthetic SQL failure'); END")
        else:
            start=h.capture.start_bounded
            def exhaust(result):
                start(result);result['_native_bounded_support']['budget'].steps=150001
            monkeypatch.setattr(h.capture,'start_bounded',exhaust)
        with pytest.raises((sqlite3.Error,Unavailable)):_cohort(h)
        assert h.store.chunk(cid)['next_offset']==0. and _primary_rows(h)==[]
        assert not h.probes and h.jobs._primary_pending is None and h.jobs._probe_staging_bytes==0


def test_primary_unknown_commit_ack_never_dispatches(primary_app_factory,monkeypatch):
    with primary_app_factory() as h:
        _tripwires(h,monkeypatch)
        cid,=ingest_recording(h,frames=10*RATE)
        commit=h.store.commit_window;calls=[]
        def unknown(*args):
            assert commit(*args) is True;calls.append(True)
            raise RuntimeError('synthetic lost primary ACK')
        monkeypatch.setattr(h.store,'commit_window',unknown)
        with pytest.raises(RuntimeError,match='lost primary ACK'):_cohort(h)
        assert calls==[True] and h.store.chunk(cid)['next_offset']==10.
        _assert_primary_packet(h,cid)
        _handoff(h)
        assert not h.probes and h.jobs._primary_pending is None and _counts(h)['attempts']==0


def test_history_append_ack_and_configuration_closure_resolve_without_replay(primary_app_factory,monkeypatch):
    with primary_app_factory() as h:
        _tripwires(h,monkeypatch)
        cid,=ingest_recording(h,frames=10*RATE)
        append=h.jobs.history.append_primary;calls=[]
        def unknown(*args,**kwargs):
            result=append(*args,**kwargs);calls.append(result)
            raise ReadError(503,'synthetic_append_ack')
        monkeypatch.setattr(h.jobs.history,'append_primary',unknown)
        assert _cohort(h)==cid
        _handoff(h,compare=False)
        pending=h.jobs._primary_pending
        assert len(h.probes)==1 and len(calls)==1
        assert pending is not None and pending['state']=='append_unknown'
        saved=_primary_rows(h)
        h.store.settings.recording_primary_observation_policy='off'
        _idle_until(h,lambda:h.jobs._primary_pending is None,compare=False)
        assert len(h.probes)==1 and len(calls)==1 and _primary_rows(h)==saved
        assert _counts(h)==dict(observations=1,vectors=1,pending=0,candidate_missing=0,attempts=1)


def test_busy_claim_is_bounded_even_after_configuration_closes(primary_app_factory,monkeypatch):
    with primary_app_factory() as h:
        _tripwires(h,monkeypatch)
        cid,=ingest_recording(h,frames=10*RATE)
        other=sqlite3.connect(h.store.path,timeout=0)
        try:
            other.execute('BEGIN IMMEDIATE')
            for closed in (False,True):
                if closed:h.store.settings.recording_primary_observation_policy='off'
                with pytest.raises(_ClaimDeferred):h.store.claim(probe=True)
                assert h.store.chunk(cid)['status']=='queued'
        finally:other.rollback();other.close()
        assert not h.probes


def test_pending_revocation_and_uncertain_cleanup_keep_exact_owner(primary_app_factory,monkeypatch):
    with primary_app_factory() as h:
        _tripwires(h,monkeypatch)
        cid,=ingest_recording(h,frames=10*RATE)
        assert _cohort(h)==cid
        probe=h.jobs._primary_pending;original=h.jobs._probe_factory
        def unclosed(*args):
            driver=original(*args);driver.cleanup_ok=False;return driver
        monkeypatch.setattr(h.jobs,'_probe_factory',unclosed)
        _handoff(h,compare=False)
        assert probe['state']=='cleanup_unknown' and h.jobs._primary_pending is probe
        assert h.jobs.permit._optional is probe and probe['crop'] is not None
        assert len(h.probes)==1 and not probe['operation'].cleaned
        h.store.settings.moss_jobs_enabled=True;h.store.settings.moss_automatic_enabled=True
        h.store.settings.moss_jobs_model_path=str(h.store.settings.data_dir/'ABSENT-MOSS-MODEL')
        assert not h.jobs.permit._configuration_current()
        assert h.client.get(f'/sessions/{h.sid}/moss-jobs').status_code==409
        assert h.automatic.before_window(h.store.chunk(cid)) is True
        _handoff(h,compare=False)
        assert h.jobs._primary_pending is probe and h.jobs.permit._optional is probe
        h.probes[0].cleanup_ok=True
        _idle_until(h,lambda:h.jobs._primary_pending is None,compare=False)
        assert probe['operation'].cleaned and probe['crop'] is None
        assert len(h.probes)==1 and h.probes[0].starts==1
        assert h.jobs.permit._optional is None and _counts(h)['candidate_missing']==1
        assert h.store.chunk(cid)['next_offset']==10. and [r['text'] for r in _primary_rows(h)]==['synthetic']


def test_combined_history_rivals_and_dormant_queues_survive_primary_only_reopen(primary_app_factory,monkeypatch):
    from test_moss_automatic_integration import seed_intent
    from test_recording_speaker_history_integration import _turn
    from audio_backend import recording_speaker_links as links
    with primary_app_factory(mode='combined') as h:
        # Bounded diagnostics only: original stage runs once, errors rethrow.
        # None of these transient helpers is an authority/currentness callback.
        stage_events=[]
        def observe(target,name):
            original=getattr(target,name)
            def run(*args,**kwargs):
                try:
                    result=original(*args,**kwargs)
                except BaseException as error:
                    if len(stage_events)<8:
                        reason=getattr(error,'reason',None)
                        stage_events.append((name,type(error).__name__,
                            reason[:120] if type(reason) is str else None))
                    raise
                if len(stage_events)<8:stage_events.append((name,'returned',None))
                return result
            monkeypatch.setattr(target,name,run)
        observe(h.jobs,'_stage_moss_history_binding')
        observe(links,'capture_candidates_v3')
        observe(history,'moss_staging_bound')
        cids=ingest_recording(h,frames=20*RATE)
        assert _cohort(h)==cids[0]
        _turn(h)
        # The established one-second FakeDriver phrase admits authentic MOSS
        # history with a native-primary duplicate and its witnesses. It does
        # not qualify a separately vectorized MOSS phrase. The retained prior
        # three-second fixture hit the unchanged history staging-capacity guard.
        _turn(h)
        assert len(h.drivers)==1 and h.drivers[0].starts==1
        staged=(h.jobs._live or {}).get('speaker_probe',{})
        probe_diagnostic={key:staged.get(key) for key in
            ('state','history_route','history_admitted','terminal_state','commit_pending')}
        probe_diagnostic['live_present']=h.jobs._live is not None
        del staged
        assert _cohort(h)==cids[1]
        pending=h.jobs._primary_pending
        assert pending is not None
        assert h.jobs._stop_primary()
        with h.store.db() as db:
            attempts=[dict(r) for r in db.execute(f'SELECT * FROM {history.ATTEMPTS}')]
            assert any(r['origin_kind']=='moss_result' and r['state']=='success' for r in attempts), (stage_events,probe_diagnostic)
            assert any(r['origin_kind']=='primary_commit' and r['state']=='failure' for r in attempts)
            historical=[tuple(r) for r in db.execute(f'SELECT * FROM {history.CERTIFICATES} ORDER BY sequence')]
            assert any(r['evidence_kind']=='candidate_missing' for r in db.execute(f'SELECT * FROM {history.CERTIFICATES}'))
            # These are deliberately synthetic dormant queue/outbox markers,
            # not evidence or permission to dispatch or alter accepted history.
            queued=seed_intent(db,h.sid,101,state='queued')
            waiting=seed_intent(db,h.sid,102,state='queued')
            db.execute(f"UPDATE {auto.TABLE} SET target_key=?,plan_phase='waiting',wait_deadline=?,plan_revision=0 WHERE candidate_key=?",
                ('e'*64,time.time()+300.,waiting['candidate_key']))
            db.execute("UPDATE native_core_commits SET pending=?,state='pending',reason='synthetic_dormant_outbox' WHERE cid=?",
                (b'synthetic-dormant-seam',cids[0]))
        sid=h.sid
    with primary_app_factory(mode='primary',sid=sid) as reopened:
        _tripwires(reopened,monkeypatch)
        with reopened.store.db() as db:
            before={table:[tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                for table in (auto.TABLE,auto.USAGE,'native_core_commits')}
            assert [tuple(r) for r in db.execute(f'SELECT * FROM {history.CERTIFICATES} ORDER BY sequence')]==historical
        # Startup recovery is complete; only comparison/cleanup may progress.
        reopened.worker._automatic_handoff(request=True)
        task=reopened.jobs._history_comparison
        assert task is not None and task['thread'].is_alive()
        task['thread'].run()
        assert task['state']=='refused' and task['reason']=='unknown_rival'
        _passive_without_positive(reopened,'unknown_rival')
        reopened.automatic.maintain_bounded_waiters()
        reopened.automatic.step()
        with reopened.store.db() as db:
            after={table:[tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                for table in before}
            assert [tuple(r) for r in db.execute(f'SELECT * FROM {history.CERTIFICATES} ORDER BY sequence')]==historical
        assert after==before and not reopened.probes and not reopened.drivers and not reopened.natives
