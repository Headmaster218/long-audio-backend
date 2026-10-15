"""Portable model-free bookkeeping through real Store/Worker/CellStore.

Native turns and ASR words are synthetic. No model/network/provider is invoked.
The existing simulated_profile fixture supplies test-only runtime qualification;
these tests do not qualify binaries, SQLite/VFS allocation or crash durability.
Production runtime admission remains covered separately by AdmissionTests.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import time
from types import MethodType

import pytest

from audio_backend import native_owned_core_seams as core
from audio_backend import native_worker
from audio_backend.native_owned_cell_capture import Capture, PrimaryAuthority
from audio_backend.native_owned_cell_store import CellStore, Operation, Budget
from audio_backend.native_owned_cells import canonical, sha, RATE, Unavailable, Cell
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.worker import Worker
from test_native_owned_cell_store import simulated_profile
from test_multicore_seam_coverage import (Harness, SyntheticNative, analyzer_result, pcm,
    read_wav, write_wav, merged, assert_disjoint_cover)


def wait(op):
    assert isinstance(op, Operation), op
    assert op.slot_released.wait(4), op.result
    assert op.done.wait(1), op.result
    return op.result


class RealHarness(Harness):
    def __init__(self, root, seams, monkeypatch):
        self.root = root; self.windows = {}; self.native_reads = []; self.analyzer_reads = []
        self.commits = []; self.capture_steps = []; self.source_ids = []; self.packets = []
        self.activity = merged([(round((s - .75) * RATE), round((s + .75) * RATE)) for s in seams])
        self.markers = [(round((s + d) * RATE), v, f'm{n}_{side}_v{v}')
            for n, s in enumerate(seams) for side, d in enumerate((-.25, .25)) for v in (1, 2)]
        (root / 'uploads').mkdir(); (root / 'tmp').mkdir()
        self.settings = Settings(data_dir=root, disk_reserve_bytes=0, start_worker=False,
            diarization_backend='nemotron', native_diarization_model='synthetic', native_diarization_library='synthetic',
            native_owned_cells_enabled=True, native_owned_core_seams_enabled=True)
        self.store = Store(root / 'primary.sqlite', self.settings)
        self.sid = self.store.create_session('core seams model-free')['id']
        self.authority = PrimaryAuthority(self.store)
        self.owner = CellStore(root / 'native-owned-cells', self.authority, enabled=True,
            qualification=simulated_profile(root), core_seam_enabled=True)
        self.authority.owner = self.owner; self.owner.start()
        assert self.owner._ready.wait(4), self.owner.health()
        self.capture = Capture(self.owner, self.authority)
        h = self
        class Analyzer:
            def __init__(self, config):pass
            def close(self):pass
            def analyze(self, path, diarization=None):
                a, b = h.windows[str(path)]['frames']; data = read_wav(path)
                assert data == pcm(a, b)
                h.analyzer_reads.append({'frames': [a, b], 'sha256': hashlib.sha256(data).hexdigest()})
                return analyzer_result(diarization, a, b, h.markers)
        self.worker = Worker(self.store, self.settings, Analyzer)
        self.worker.native_owned_cells = self.capture
        self.worker.extract_pcm = MethodType(self.extract, self.worker)
        original = self.store.commit_window
        def commit(chunk, offset, end, result):
            if '_native_core_seam' in result:self.packets.append(deepcopy(result['_native_core_seam']))
            accepted = original(chunk, offset, end, result)
            if accepted:
                self.commits.append({'cid':chunk['id'], 'frames':[round((chunk['timeline_start']+offset)*RATE),
                    round((chunk['timeline_start']+end)*RATE)], 'local':[round(offset*RATE), round(end*RATE)]})
            return accepted
        self.store.commit_window = commit
        monkeypatch.setattr(native_worker, 'NativeProcess', lambda settings: SyntheticNative(settings, self))
        monkeypatch.setattr('audio_backend.worker.check_resources', lambda *a, **k: None)

    def add(self, seconds, final=True, isolated=False):
        origin = sum(self.store.chunk(cid)['duration'] for cid in self.source_ids)
        path = self.root / 'uploads' / f'source{len(self.source_ids)}.part'
        write_wav(path, pcm(round(origin*RATE), round((origin+seconds)*RATE)))
        raw = path.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
        with path.open('rb') as source:observed = self.capture.streamed_descriptor(source.fileno(), digest, len(raw))
        token = self.capture.prepare_upload(path, digest, len(raw), observed)
        assert token is not None, self.capture.last_reason
        final_path = path.with_suffix('.audio'); path.rename(final_path)
        options={} if isolated else {'stream_id':'stream','stream_final':final}
        cid, created = self.store.add_chunk(self.sid, path.name, digest, final_path, seconds, **options)
        assert wait(self.capture.finish_upload(token, self.sid, cid, final_path, created))['status'] == 'receipt_retained'
        self.source_ids.append(cid)
        return cid

    def run(self, monkeypatch=None):
        while (chunk := self.store.claim()) is not None:self.worker.process(chunk)
        assert all(self.store.chunk(cid)['status'] == 'completed' for cid in self.source_ids)
        self.settle()

    def settle(self):
        # Test-side bounded waiting only; product drain is nonwaiting.
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            op = self.owner._slot
            if op is not None:wait(op)
            self.capture.drain_core_seams()
            with self.store.db() as db:
                pending = db.execute("SELECT COUNT(*) FROM native_core_commits WHERE state='pending'").fetchone()[0]
            if not pending and self.owner._slot is None and self.capture.core_seams.flight is None:return
            time.sleep(.01)
        raise AssertionError((self.capture.health(), self.rows()))

    def rows(self):
        with self.store.db() as db:return [dict(r) for r in db.execute('SELECT * FROM native_core_commits ORDER BY logical_end')]

    def cells(self):
        op=self.owner._slot
        if op is not None:wait(op)
        result = wait(self.owner.submit('list', (self.sid, 0)))
        assert result['status']=='available', result
        found=[]
        for item in result['cells']:
            detail = wait(self.owner.submit('detail', item['cell_id']))
            assert detail['status']=='available', detail
            doc = json.loads(detail['detail'])
            if core.is_core(doc):found.append((item, doc))
        return found

    def close(self):
        if self.worker.native:self.worker.native.close()
        for pin in tuple(self.owner._pins.values()):pin.release()
        self.owner.close(.5)
        if self.owner._thread:self.owner._thread.join(4)
        assert not self.owner._thread.is_alive()


@pytest.fixture
def factory(tmp_path, monkeypatch):
    created=[]
    def make(seams=(30,), name=None):
        path=tmp_path / (name or f'case{len(created)}'); path.mkdir()
        h=object.__new__(RealHarness)
        try:h.__init__(path, seams, monkeypatch)
        except BaseException:
            owner=getattr(h,'owner',None)
            if owner is not None:
                owner.close(.5)
                if owner._thread:owner._thread.join(4)
                assert not owner._thread or not owner._thread.is_alive()
            raise
        created.append(h); return h
    yield make
    for h in reversed(created):
        h.close()
        shutil.rmtree(h.root)


@pytest.mark.parametrize('durations,seams,ranges',[
    ([62], [30,60], [(0,30),(30,60),(60,62)]),
    ([31,31], [30,31,61], [(0,30),(30,31),(31,61),(61,62)]),
])
def test_real_worker_source_commit_cell_same_and_distinct_uploads(factory,durations,seams,ranges):
    h=factory(seams)
    for i,d in enumerate(durations):h.add(d,i==len(durations)-1)
    h.run(); cells=h.cells()
    assert len(cells)==len(seams), (h.capture.health(),h.rows())
    assert [r['frames'] for r in h.commits]==[[a*RATE,b*RATE] for a,b in ranges]
    assert_disjoint_cover([r['frames'] for r in h.commits],0,sum(durations)*RATE)
    with h.store.db() as db:
        texts=[r[0] for r in db.execute('SELECT text FROM segments')]
        turns=[dict(r) for r in db.execute('SELECT * FROM speaker_turns')]
    assert Counter(texts)==Counter(name for _,_,name in h.markers)
    assert all({t['speaker_local_id'].split(':')[-1] for t in turns if t['start']<=s+.125<t['end']}
               == {'speaker_1','speaker_2'} for s in seams)
    for item,doc in cells:
        lo,hi=doc['geometry']['start_frame'],doc['geometry']['end_frame']
        assert doc['query']['query_start_frame']<=lo<hi<=doc['query']['query_end_frame']
        assert doc['support']['pcm16le_sha256']==hashlib.sha256(pcm(lo,hi)).hexdigest()
        assert len({p['chunk_id'] for p in doc['dependencies']})==len(doc['dependencies'])
        assert {t['label'].split(':')[-1] for t in doc['query']['turns']}=={'speaker_1','speaker_2'}
        assert doc['coverage']['adds_primary_audio'] is False
        with Budget().phase() as budget:assert h.authority.cell_valid(doc,budget,publishing=True)
        read=wait(h.owner.submit('read',item['cell_id']))
        assert read['status']=='available',read
        assert read['pin'].read()==pcm(lo,hi);read['pin'].release()
    # First source metadata was overwritten, but immutable per-core receipts survive.
    assert h.store.chunk(h.source_ids[0])['metadata']['core_cut']['actual']==durations[0]
    assert all(len(r['body'])<=core.COMPACT_BYTES for r in h.rows())
    assert len(h.native_reads)==len([a for a in h.worker.native.process.calls if a=='push'])
    assert len(h.analyzer_reads)==len(ranges)


def test_no_overlap_receipt_then_later_overlap(factory):
    h=factory((60,));h.add(62);h.run()
    assert len(h.rows())==3
    cells=h.cells();assert len(cells)==1
    assert cells[0][1]['geometry']['seam_frame']==60*RATE
    assert h.rows()[1]['reason']=='no_same_response_native_overlap'


def stop_after_first_core(h,monkeypatch):
    original=h.store.commit_window
    def commit(chunk,offset,end,result):
        ok=original(chunk,offset,end,result)
        if ok and end==30.:raise InterruptedError('test crash after primary commit')
        return ok
    monkeypatch.setattr(h.store,'commit_window',commit)
    with pytest.raises(InterruptedError):h.worker.process(h.store.claim())
    monkeypatch.setattr(h.store,'commit_window',original)
    return original


def test_capture_restart_same_epoch_identity_rejected(factory,monkeypatch):
    h=factory();cid=h.add(32);stop_after_first_core(h,monkeypatch)
    old_run=h.capture.core_seams.run
    if h.worker.native:h.worker.native.close()
    h.worker.native=None
    h.capture=Capture(h.owner,h.authority);h.worker.native_owned_cells=h.capture
    assert h.capture.core_seams.run!=old_run
    h.worker.process(h.store.chunk(cid));h.settle()
    assert len(h.rows())==2
    assert h.rows()[1]['reason']=='core_seam_capture_domain_changed'
    assert not h.cells()


@pytest.mark.parametrize('phase',['before','after'])
def test_primary_commit_crash_boundary(factory,monkeypatch,phase):
    h=factory();cid=h.add(32)
    original=h.store.commit_window
    def commit(chunk,offset,end,result):
        if offset==30 and phase=='before':raise InterruptedError('test before transaction')
        answer=original(chunk,offset,end,result)
        if offset==30:raise InterruptedError('test after transaction before acknowledgement')
        return answer
    monkeypatch.setattr(h.store,'commit_window',commit)
    with pytest.raises(InterruptedError):h.worker.process(h.store.claim())
    assert len(h.rows())==(1 if phase=='before' else 2)
    assert h.store.chunk(cid)['next_offset']==(30 if phase=='before' else 32)
    if phase=='after':
        assert h.rows()[-1]['pending'] is not None
        h.capture=Capture(h.owner,h.authority);h.worker.native_owned_cells=h.capture
        h.settle();assert len(h.cells())==1


@pytest.mark.parametrize('limit,reason',[('MAX_COMPACTS','core_receipt_row_capacity'),('MAX_PENDING','core_pending_row_capacity')])
def test_capacity_preserves_primary(factory,monkeypatch,limit,reason):
    h=factory();h.add(32)
    monkeypatch.setattr(core,limit,0)
    h.run()
    assert h.store.results(h.sid)['segments']
    assert h.store.chunk(h.source_ids[0])['metadata']['native_core_seam']['status']==reason
    assert not h.cells()


def test_payload_overflow_preserves_core_and_primary(factory,monkeypatch):
    h=factory();h.add(32)
    original=h.store.commit_window
    def commit(chunk,offset,end,result):
        if offset==30:result['_native_core_seam']['proof']=b'x'*(core.PENDING_BYTES+1)
        return original(chunk,offset,end,result)
    monkeypatch.setattr(h.store,'commit_window',commit)
    h.run();assert len(h.rows())==2;assert h.rows()[-1]['reason']=='core_pending_payload_capacity'
    assert h.rows()[-1]['pending'] is None;assert not h.cells()


def hold_pending(h,monkeypatch):
    # Keep both optional publication classes off the real owner during primary.
    original=h.owner.submit
    def submit(kind,payload):return {'status':'busy'} if kind=='publish' else original(kind,payload)
    monkeypatch.setattr(h.owner,'submit',submit)
    while (chunk:=h.store.claim()) is not None:h.worker.process(chunk)
    monkeypatch.setattr(h.owner,'submit',original)
    assert h.rows()[-1]['state']=='pending'


def test_busy_then_restart_drains_exact_candidate(factory,monkeypatch):
    h=factory();h.add(32);hold_pending(h,monkeypatch)
    proof=h.rows()[-1]['pending'];old=h.owner
    old.close(.5);old._thread.join(4);assert not old._thread.is_alive()
    h.owner=CellStore(old.directory,h.authority,enabled=True,qualification=simulated_profile(h.root),core_seam_enabled=True)
    h.authority.owner=h.owner;h.owner.start();assert h.owner._ready.wait(4),h.owner.health()
    h.capture=Capture(h.owner,h.authority);h.worker.native_owned_cells=h.capture
    h.settle();assert len(h.cells())==1
    assert canonical(h.cells()[0][1])==proof


def test_owner_commit_before_ack_replays_exactly_once(factory,monkeypatch):
    h=factory();h.add(32);hold_pending(h,monkeypatch)
    original=h.owner._publish;called=[]
    def publish(op,budget):
        result=original(op,budget)
        if not called:called.append(1);raise RuntimeError('test owner committed before acknowledgement')
        return result
    monkeypatch.setattr(h.owner,'_publish',publish)
    h.settle();assert len(h.cells())==1
    with sqlite3.connect(h.owner.path) as db:assert db.execute('SELECT COUNT(*) FROM cells').fetchone()[0]==1
    assert h.rows()[-1]['state']=='published'


@pytest.mark.parametrize('action',['cancel','delete','revoke','mutate'])
def test_pending_cannot_resurrect_after_authority_loss(factory,monkeypatch,action):
    h=factory();cid=h.add(32);hold_pending(h,monkeypatch)
    if action=='cancel':
        with h.store.db() as db:db.execute("UPDATE chunks SET status='cancelled' WHERE id=?",(cid,))
    elif action=='delete':h.store.delete_session(h.sid)
    elif action=='revoke':h.owner.revoked()
    else:
        with h.store.db() as db:path=Path(db.execute('SELECT path FROM chunks WHERE id=?',(cid,)).fetchone()[0])
        with path.open('r+b') as f:f.seek(44+30*RATE*2);f.write(b'\x55\x66')
    for _ in range(3):h.capture.drain_core_seams()
    op=h.owner._slot
    if op is not None:wait(op)
    assert not any(r['state']=='published' for r in h.rows())
    with sqlite3.connect(h.owner.path) as db:assert db.execute('SELECT COUNT(*) FROM cells').fetchone()[0]==0


def test_same_slot_different_evidence_conflicts_without_overwrite(factory):
    h=factory();h.add(32);h.run()
    old=h.rows()[0];packet=deepcopy(h.packets[0]);packet['commit']['scope']['run']='f'*32
    cid=h.source_ids[0];chunk=h.store.chunk(cid);native=h.packets[0]['commit']
    # Reuse genuine primary result evidence from the committed right proof is not
    # necessary here: the packet's original evidence hash binds metadata below.
    cell=h.cells()[0][1];evidence=deepcopy(cell['right_evidence'])
    evidence['start']=0;evidence['end']=30*RATE
    evidence['core_cut']={'nominal':30.,'actual':30.,'reason':'fixed_or_final'}
    # This is a different valid candidate for the SAME first slot, not an overwrite.
    packet['commit']['native_sha256']=sha(evidence['native'])
    packet['commit']['cut_sha256']=sha(evidence['core_cut'])
    packet['commit']['evidence_sha256']=sha(evidence)
    result={'metadata':{'native_diarization':evidence['native'],'core_cut':evidence['core_cut']},'_native_core_seam':packet}
    with h.store.db() as db:
        assert core.transaction(db,chunk,0.,30.,result)=='core_slot_evidence_conflict'
    assert h.rows()[0]['body']==old['body']


def test_proof_rejects_query_gap_reset_domain_and_hash_change(factory):
    h=factory();h.add(32);h.run();doc=h.cells()[0][1]
    for mutate in (
        lambda d:d['query'].__setitem__('query_start_frame',d['geometry']['seam_frame']),
        lambda d:d['commits'][0]['scope'].__setitem__('reset',d['commits'][0]['scope']['reset']+1),
        lambda d:d['commits'][0]['scope'].__setitem__('index',1),
        lambda d:d['query'].__setitem__('support_pcm16le_sha256','0'*64),
    ):
        changed=deepcopy(doc);mutate(changed)
        with pytest.raises(Unavailable):core.validate_document(canonical(changed))


def test_projection_budget_and_no_old_store_adoption(factory):
    h=factory();h.add(32);h.run();item,doc=h.cells()[0]
    with Budget().phase() as budget:
        assert core.current(h.authority,doc,budget)
        assert budget.sql_bytes<256*1024
    h.owner.close(.5);h.owner._thread.join(4)
    old=CellStore(h.owner.directory,h.authority,enabled=True,qualification=simulated_profile(h.root),seam_enabled=True)
    old.start();old._thread.join(4)
    assert not old._ready.is_set()
    assert old.health()['status']=='optional_primary_binding_mismatch'
    old.close(.5)


def test_changed_end_conflicts_at_same_start(factory):
    h=factory();h.add(32);h.run();old=h.rows()[0];doc=h.cells()[0][1]
    packet=deepcopy(h.packets[0]);packet['proof']=None
    p=packet['commit']['part'];p['source_end']-=RATE;p['logical_end']-=RATE
    evidence=deepcopy(doc['right_evidence']);evidence['start']=0;evidence['end']=29*RATE
    evidence['core_cut']={'nominal':30.,'actual':29.,'reason':'neural_speech_gap'}
    packet['commit'].update(native_sha256=sha(evidence['native']),cut_sha256=sha(evidence['core_cut']),evidence_sha256=sha(evidence))
    assert core.slot(packet['commit'])==old['slot']
    result={'metadata':{'native_diarization':evidence['native'],'core_cut':evidence['core_cut']},'_native_core_seam':packet}
    with h.store.db() as db:
        assert core.transaction(db,h.store.chunk(h.source_ids[0]),0.,29.,result)=='core_slot_evidence_conflict'
    assert h.rows()[0]['body']==old['body']


def test_actual_global_receipt_limit_preserves_primary(factory,monkeypatch):
    h=factory();h.add(32);original=h.store.commit_window
    def commit(chunk,offset,end,result):
        if offset==0:
            raw=canonical(result['_native_core_seam']['commit'],core.COMPACT_BYTES)
            with h.store.db() as db:
                db.executemany('INSERT INTO native_core_commits VALUES(?,?,?,?,?,?,NULL,?,?)',
                    ((f'{i:064x}',h.sid,chunk['id'],'0'*64,0,raw,'none','fixture_capacity') for i in range(core.MAX_COMPACTS)))
        return original(chunk,offset,end,result)
    monkeypatch.setattr(h.store,'commit_window',commit);h.run()
    assert len(h.rows())==2056
    assert h.store.chunk(h.source_ids[0])['metadata']['native_core_seam']['status']=='core_receipt_row_capacity'
    assert h.store.results(h.sid)['segments'];assert not h.cells()


def test_full_pending_bound_and_projection_screen(factory,monkeypatch):
    h=factory();h.add(32);original=h.store.commit_window
    def commit(chunk,offset,end,result):
        if offset==30:
            packet=result['_native_core_seam'];raw=canonical(packet['commit'],core.COMPACT_BYTES)
            with h.store.db() as db:
                db.executemany('INSERT INTO native_core_commits VALUES(?,?,?,?,?,?,?,?,?)',
                    ((f'{i:064x}',h.sid,chunk['id'],'0'*64,0,raw,b'x'*core.PENDING_BYTES,'gap','fixture_payload')
                     for i in range(core.MAX_PENDING)))
        return original(chunk,offset,end,result)
    monkeypatch.setattr(h.store,'commit_window',commit);h.run()
    assert h.rows()[-1]['reason']=='core_pending_row_capacity'
    assert h.store.results(h.sid)['segments']
    # Aggregate pending payload cannot be projected by drain: only a length-first
    # single-row read is permitted. Existing owner SQL budget remains untouched.
    with h.store.db() as db:
        assert db.execute('SELECT sum(length(pending)) FROM native_core_commits').fetchone()[0]==4*core.PENDING_BYTES
    assert not h.cells()


def test_isolated_upload_internal_core(factory):
    h=factory();h.add(32,isolated=True);h.run();cells=h.cells()
    assert len(cells)==1
    assert cells[0][1]['commits'][0]['scope']['kind']=='upload'
    assert len(cells[0][1]['dependencies'])==1


def test_actual_native_reset_is_explicitly_unsupported(factory):
    h=factory((300,));h.add(302);h.run()
    assert len(h.worker.native.process.resets)==2
    assert h.rows()[-1]['reason']=='core_seam_reset_unsupported'
    assert not h.cells()
    assert_disjoint_cover([r['frames'] for r in h.commits],0,302*RATE)


def test_v3_cell_jobs_dispatch_intent_exact_pin_and_revocation(factory):
    from audio_backend.native_owned_cell_jobs import CellJobsSource
    from audio_backend.native_owned_support_cache import ReadError
    h=factory();h.add(32);h.run();item,doc=h.cells()[0]
    h.settings.moss_jobs_enabled=True
    source=CellJobsSource(h.owner,h.store)
    detail=source.detail(h.sid,item['cell_id'])
    request={'expected_cell_sequence':detail['sequence'],'expected_cell_revision':detail['revision'],
        'expected_proof_sha256':hashlib.sha256(canonical(doc)).hexdigest(),
        'support_id':detail['support'].support_id,'coverage_intent':'overlapping_diagnostic'}
    for intent in (None,'new_primary_audio'):
        bad=dict(request)
        if intent is None:bad.pop('coverage_intent')
        else:bad['coverage_intent']=intent
        with pytest.raises(ReadError) as caught:source.acquire_dispatch(h.sid,item['cell_id'],bad)
        assert caught.value.reason=='coverage_intent_required'
        deadline=time.monotonic()+2
        while source.cleanup_pending and time.monotonic()<deadline:time.sleep(.01)
        assert not source.cleanup_pending and not h.owner._pins
    lease=source.acquire_dispatch(h.sid,item['cell_id'],request)
    assert lease.token.proof==canonical(doc)
    assert lease.token.support.to_dict()==doc['support']
    assert source.revalidate_dispatch(lease.token)
    called=[]
    def fake_executor(bound):
        assert source.revalidate_dispatch(bound.token)
        called.append(hashlib.sha256(bound.pcm).hexdigest())
    fake_executor(lease)
    assert called==[doc['support']['pcm16le_sha256']]
    source.accepted(lease);assert lease.release()
    assert not source.cleanup_pending and not h.owner._pins
    lease=source.acquire_dispatch(h.sid,item['cell_id'],request)
    h.owner.revoked()
    with pytest.raises(ReadError):fake_executor(lease)
    assert len(called)==1
    assert source.abandon(lease)
    assert not source.cleanup_pending and not h.owner._pins


@pytest.mark.parametrize('boundary',[300,3600])
def test_reset_geometry_at_epoch_and_hour_boundary_is_not_adopted(factory,boundary):
    h=factory();h.add(32);h.run();doc=h.cells()[0][1]
    commits=deepcopy(doc['commits']);delta=(boundary-30)*RATE
    for i,c in enumerate(commits):
        c['part']['logical_start']+=delta;c['part']['logical_end']+=delta
        c['scope']['index']=boundary//300-1+i
        c['scope']['epoch']=str(i+1)*32;c['scope']['reset']=i+1
    with pytest.raises(Unavailable,match='core_seam_reset_unsupported'):core.geometry(commits)
    assert h.capture.health()['core_seam_reset_support']=='unsupported'


def test_ack_cancellation_between_select_and_update_is_not_acknowledged(factory,monkeypatch):
    from contextlib import contextmanager
    h=factory();h.add(32);hold_pending(h,monkeypatch);row=h.rows()[-1]
    original=h.store.db;cancelled=[]
    class Connection:
        def __init__(self,db):self.db=db
        def execute(self,sql,args=()):
            if sql.startswith('UPDATE native_core_commits SET state=?,reason=?,pending=NULL') and not cancelled:
                self.db.execute("UPDATE native_core_commits SET state='cancelled',pending=NULL,reason='test_cancel' WHERE slot=?",(row['slot'],))
                cancelled.append(True)
            return self.db.execute(sql,args)
    @contextmanager
    def racing_db():
        with original() as db:yield Connection(db)
    monkeypatch.setattr(h.store,'db',racing_db)
    assert h.capture.core_seams._status(row['slot'],'published','core_published',hashlib.sha256(row['pending']).hexdigest()) is False
    monkeypatch.setattr(h.store,'db',original)
    assert h.rows()[-1]['state']=='cancelled'


def test_repeated_revoked_drain_writes_once(factory,monkeypatch):
    from contextlib import contextmanager
    h=factory();h.add(32);hold_pending(h,monkeypatch);h.owner.revoked()
    original=h.store.db;writes=[]
    class Connection:
        def __init__(self,db):self.db=db
        def execute(self,sql,args=()):
            if "reason='core_owner_revoked'" in sql:writes.append(sql)
            return self.db.execute(sql,args)
    @contextmanager
    def observed_db():
        with original() as db:yield Connection(db)
    monkeypatch.setattr(h.store,'db',observed_db)
    h.capture.core_seams.next_attempt=0.
    for _ in range(5):h.capture.drain_core_seams()
    assert len(writes)==1
    assert h.capture.core_seams.revocation_done
    assert h.capture.health()['core_seam_pending'] is False


def test_owner_commit_then_revoke_before_ack_invalidates_cell(factory,monkeypatch):
    h=factory();h.add(32);hold_pending(h,monkeypatch)
    h.capture.core_seams.next_attempt=0.;h.capture.drain_core_seams()
    assert h.capture.core_seams.flight is not None
    slot_id,op,cell_id,_=h.capture.core_seams.flight
    result=wait(op);assert result['status']=='durable',result
    proof=h.rows()[-1]['pending'];assert proof is not None
    h.owner.revoked();h.capture.core_seams.next_attempt=0.;h.capture.drain_core_seams()
    assert h.rows()[-1]['state']=='cancelled'
    with Budget().phase() as budget:assert not h.authority.cell_valid(json.loads(proof),budget,publishing=False)
    detail=wait(h.owner.submit('detail',cell_id))
    assert detail['status']=='unavailable'
    assert h.capture.core_seams.flight is None


def test_interruption_after_optional_insert_rolls_back_whole_primary_transaction(factory,monkeypatch):
    h=factory();cid=h.add(32);original=core.transaction
    def interrupted(db,chunk,offset,end,result):
        outcome=original(db,chunk,offset,end,result)
        if offset==30:
            assert db.execute('SELECT COUNT(*) FROM native_core_commits').fetchone()[0]==2
            raise InterruptedError('fixture transaction interruption before primary UPDATE')
        return outcome
    monkeypatch.setattr(core,'transaction',interrupted)
    with pytest.raises(InterruptedError):h.worker.process(h.store.claim())
    assert len(h.rows())==1 and h.store.chunk(cid)['next_offset']==30
    with h.store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM segments WHERE start>=30').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM native_core_commits WHERE state='pending'").fetchone()[0]==0


@pytest.mark.parametrize('mode',['advanced','continued_advance','cancelled','mutated','equal_frontier'])
def test_v1_postcommit_supersession_is_not_global_revocation(factory,monkeypatch,mode):
    import threading
    h=factory();cid=h.add(62 if mode=='continued_advance' else 32)
    inserted=threading.Event();released=threading.Event()
    classifying=threading.Event();advanced_again=threading.Event();in_classification=[False]
    original_superseded=h.authority.v1_publication_superseded;original_session=h.authority.session_valid
    def superseded(doc,budget):
        in_classification[0]=True
        try:return original_superseded(doc,budget)
        finally:in_classification[0]=False
    def session(sid,budget):
        answer=original_session(sid,budget)
        if mode=='continued_advance' and in_classification[0]:
            classifying.set();assert advanced_again.wait(2)
        return answer
    original_insert=h.owner._insert_cell;original_commit=h.store.commit_window
    original_submit=h.owner.submit;ordinary=[];generation=h.owner._epoch
    def submit(kind,payload):
        op=original_submit(kind,payload)
        if kind=='publish' and isinstance(op,Operation) and 'schema' not in json.loads(payload[0].proof):ordinary.append(op)
        return op
    def insert(op,cell,budget,with_pcm):
        answer=original_insert(op,cell,budget,with_pcm)
        if cell.commit_start==0 and not inserted.is_set():
            inserted.set();assert released.wait(2)
        return answer
    def commit(chunk,offset,end,result):
        if mode=='continued_advance' and offset==60:
            assert classifying.wait(2)
            answer=original_commit(chunk,offset,end,result)
            advanced_again.set();return answer
        if offset!=30:return original_commit(chunk,offset,end,result)
        assert inserted.wait(2)
        if mode=='equal_frontier':
            with h.store.db() as db:
                metadata=json.loads(db.execute('SELECT metadata FROM chunks WHERE id=?',(cid,)).fetchone()[0])
                metadata['core_cut']['nominal']+=1
                db.execute('UPDATE chunks SET metadata=? WHERE id=?',(json.dumps(metadata),cid))
            released.set();raise InterruptedError('fixture equal frontier metadata replacement')
        answer=original_commit(chunk,offset,end,result)
        if mode=='cancelled':
            with h.store.db() as db:db.execute("UPDATE chunks SET status='cancelled' WHERE id=?",(cid,))
        elif mode=='mutated':
            with h.store.db() as db:path=Path(db.execute('SELECT path FROM chunks WHERE id=?',(cid,)).fetchone()[0])
            with path.open('r+b') as stream:stream.seek(44);stream.write(b'\x34\x12')
        released.set();return answer
    monkeypatch.setattr(h.owner,'submit',submit);monkeypatch.setattr(h.owner,'_insert_cell',insert)
    monkeypatch.setattr(h.store,'commit_window',commit)
    monkeypatch.setattr(h.authority,'v1_publication_superseded',superseded)
    monkeypatch.setattr(h.authority,'session_valid',session)
    try:
        if mode=='equal_frontier':
            with pytest.raises(InterruptedError):h.worker.process(h.store.claim())
        else:h.worker.process(h.store.claim())
        assert ordinary
        result=wait(ordinary[0]);assert result['status']=='unknown'
        h.settle()
        if mode in ('advanced','continued_advance'):
            if mode=='continued_advance':
                assert classifying.is_set() and advanced_again.is_set()
                assert h.store.chunk(cid)['next_offset']==62 and h.store.chunk(cid)['status']=='completed'
            assert result['reason']=='primary_commit_advanced_after_publication'
            assert h.owner._epoch is generation
            assert len(h.cells())==1
            assert h.rows()[1]['state']=='published'
        else:
            assert result['reason']=='source_changed_after_publication_commit'
            assert h.owner._epoch is not generation
            assert not h.cells()
    finally:
        released.set();classifying.set();advanced_again.set()
