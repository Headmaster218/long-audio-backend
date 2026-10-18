"""Fresh model-free end-to-end primary history contracts.

Only native/ASR responses, PCM fixtures and probe backend edges are synthetic.
Capture, Worker commits, manager, permit, ProbeOperation, SQLite history and the
complete comparator are production source. Execution requires the review gate.
"""
from contextlib import contextmanager
from copy import deepcopy
import io
import json
from pathlib import Path
import threading
import time
import wave

import pytest

from audio_backend import lexical_evidence as lexical
from audio_backend import recording_primary_probe as primary
from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_probe_runner as runner
from audio_backend import transcript_contract as transcript
from audio_backend.native_owned_cells import RATE
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.settings import Settings
from audio_backend.worker import Worker
from test_moss_automatic_integration import automatic_app_factory
from test_native_owned_support_api import FakeAnalyzer
from test_recording_speaker_links import vector
from test_moss_handy_controller import clean
from test_recording_primary_probe import synthetic_primary_case


def _wav(frames=50 * RATE):
    assert frames <= 50 * RATE
    pcm = b''.join(bytes((index + 1,0)) * min(10 * RATE,frames-index*10*RATE)
                   for index in range((frames+10*RATE-1)//(10*RATE)))
    out = io.BytesIO()
    with wave.open(out,'wb') as target:
        target.setnchannels(1);target.setsampwidth(2);target.setframerate(RATE);target.writeframes(pcm)
    return out.getvalue()


def _turns(frames, epoch):
    seconds = frames / RATE
    turns = [{'start':0.,'end':seconds,'speaker_local_id':epoch+':speaker_1'}]
    turns += [{'start':float(index+9),'end':min(seconds,index+9.5),
               'speaker_local_id':epoch+':speaker_2'}
              for index in range(0,int(seconds),10) if index+9 < seconds]
    return turns


def _analyze(analyzer,path,**kwargs):
    native = kwargs['diarization']
    label = native['state']['epoch_id'] + ':speaker_1'
    with wave.open(str(path),'rb') as source:duration=source.getnframes()/RATE
    assert 2.5 <= duration <= 10.5
    end=duration-.25
    unit = {'start':.5,'end':end,'ownership_anchor':.5,'anchor_kind':'ctc_emission',
            'text':'synthetic','raw_text':'synthetic'}
    return {'segments':[{'start':.5,'end':end,'text':'synthetic','raw_text':'synthetic',
        'speaker_local_id':label,'source_track_id':label,'confidence':0.,
        'asr_backend':'sensevoice','timestamp_kind':lexical.PRIMARY_TIMESTAMP,'_units':[unit]}],
        'speaker_turns':deepcopy(native['turns']),'events':[],
        'metadata':{'asr_backend':'sensevoice','timestamp_semantics':lexical.PRIMARY_TIMESTAMP,
                    'diarization_turns':deepcopy(native['turns'])}}


def _extract_history(worker,chunk,start,end,wav,tmp,stop,*,capture_support=False,capture_lexical=False):
    """Synthetic decoder over the real explicit multi-chunk source mapping."""
    from audio_backend.boundary_store import context_parts
    assert not capture_support
    parts=context_parts(worker.store,chunk,start,end)
    with worker.store.db() as db:
        sources=lexical.snapshot_parts(db,parts,chunk['session_id']) if capture_lexical else None
    cursor=0
    with wave.open(str(wav),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(RATE)
        for index,part in enumerate(parts):
            frames=round(part['read_duration']*RATE);remaining=frames
            with wave.open(part['path'],'rb') as source:
                source.setpos(round(part['read_start']*RATE))
                while remaining:
                    block=source.readframes(min(remaining,RATE))
                    assert block and len(block)%2==0
                    output.writeframesraw(block);remaining-=len(block)//2
            if sources is not None:sources[index]['analyzer_frames']=[cursor,cursor+frames]
            cursor+=frames
    assert cursor==round((end-start)*RATE)
    return {'_lexical_sources':sources} if sources is not None else None


def ingest_recording(h,*,frames):
    """Real finalized recording, <=5 real10.5s work chunks, no geometry override."""
    from audio_backend.recording_overlap import CHUNK_FRAMES
    count=(frames+CHUNK_FRAMES-1)//CHUNK_FRAMES
    assert 1<=count<=5
    prefix=f'/sessions/{h.sid}/recordings/synthetic-capture'
    response=h.client.put(prefix+'/sources',params={'source_start_frame':900007},
        content=_wav(frames),headers={'Idempotency-Key':'source-one'})
    assert response.status_code==202,response.text
    revision=response.json()['revision']
    response=h.client.post(prefix+'/finish',json={'idempotency_key':'finish-one','expected_revision':revision})
    assert response.status_code==202,response.text
    # Each chunk requires the ordinary one-second outbox admission/ack ticks.
    # The bound scales only with this fixture's <=5 real receipt operations.
    deadline=time.monotonic()+2*count+4
    while time.monotonic()<deadline:
        flight=h.worker.recordings._flight
        operation=flight['operation'] if flight is not None else h.owner._slot
        remaining=max(0.,deadline-time.monotonic())
        if operation is not None and not operation.slot_released.wait(min(3.,remaining)):break
        h.worker.recordings.advance(h.capture)
        response=h.client.get(prefix)
        assert response.status_code==200,response.text
        if response.json()['state']=='queued':break
        assert response.json()['state']=='pending_receipts',response.text
        remaining=max(0.,deadline-time.monotonic())
        floor=max(0.,h.worker.recordings._next_tick-time.monotonic())
        time.sleep(min(remaining,max(.01,floor)))
    assert response.json()['state']=='queued',response.text
    with h.store.db() as db:
        rows=[dict(row) for row in db.execute('''SELECT c.* FROM chunks c JOIN recording_work w
            ON w.chunk_id=c.id WHERE c.session_id=? ORDER BY w.position''',(h.sid,))]
    assert len(rows)==count and all(row['status']=='queued' for row in rows)
    assert sum(round(row['duration']*RATE) for row in rows)==frames
    return tuple(row['id'] for row in rows)


@pytest.fixture
def primary_history_factory(automatic_app_factory,monkeypatch):
    original = Settings.__post_init__
    def configure(settings):
        if settings.moss_automatic_enabled:
            settings.asr_backend = 'sensevoice';settings.lexical_evidence_enabled = True
            settings.native_owned_bounded_core_support_enabled = True
            settings.recording_speaker_links_enabled = True
            settings.recording_primary_observation_policy = history.POLICY
            settings.recording_speaker_model_path = str(Path(settings.data_dir)/'ABSENT-HISTORY-MODEL')
            settings.window_seconds = 10.5;settings.context_seconds = 0.
        original(settings)
    monkeypatch.setattr(Settings,'__post_init__',configure)
    monkeypatch.setattr(Worker,'extract_pcm',_extract_history)
    monkeypatch.setattr(FakeAnalyzer,'analyze',_analyze)
    monkeypatch.setattr('test_native_owned_support_api.synthetic_turns',_turns)
    monkeypatch.setattr('test_native_owned_support_api.wav_bytes',_wav)
    @contextmanager
    def opened():
        with automatic_app_factory() as h:
            h.probes = []
            class Driver:
                def __init__(self,directory,digest):
                    self.directory,self.digest=directory,digest;self.starts=0;self.cleanups=0;self.cleanup_ok=True
                    h.probes.append(self)
                def start(self,cancelled):
                    self.starts += 1
                    assert h.capture.analyzer is None
                    assert not any(item.granted or item.entered for item in h.jobs.permit._primary.values())
                    assert not h.jobs._lock._is_owned()
                    assert h.jobs.permit._optional_kind == 'automatic_primary_probe'
                    assert not Path(h.store.settings.recording_speaker_model_path).exists()
                def wait(self,cancelled):
                    request=json.loads((self.directory/'request.json').read_bytes())
                    assert not cancelled.is_set()
                    return {'kind':runner.KIND,'job_id':request['job_id'],
                        'space':runner.expected_space()[0],'space_id':request['space_id'],'compute_attempts':1,
                        'nodes':[{'index':0,'pcm_sha256':request['crops'][0]['pcm_sha256'],
                                  'vector':vector(0.,1.) if len(h.probes)==3 else vector(1.,0.)}]}
                def cleanup(self,stop):
                    self.cleanups += 1
                    return clean() if self.cleanup_ok else {'cleanup_verified':False}
            h.jobs._probe_factory = Driver
            try:yield h
            finally:
                for item in (h.jobs._primary_pending,h.jobs._history_comparison):
                    if item and item['thread'] and item['thread'].is_alive():
                        item['cancelled'].set();item['thread'].run()
                h.jobs._stop_primary();h.jobs._stop_history_comparison()
    return opened


def _cohort(h):
    """One real primary cohort and ordinary cleanup, without an optional turn."""
    h.automatic.next_attempt = 0.
    cache=h.worker.native_owned_support_cache;watermark=cache.preclaim()
    chunk=h.store.claim();assert chunk is not None
    h.worker.current_chunk=chunk['id'];h.worker._fair_core_handoff=True;normal=False
    try:
        with h.jobs.permit.primary_span('worker'):
            h.worker._begin_native_support(chunk,watermark)
            try:
                yielded=h.worker.process(chunk);normal=True
                if yielded is True:h.store.requeue_core(chunk)
            finally:
                h.worker._finish_native_support();h.jobs.permit.finish_cohort(normal=normal)
                # FakeNative has no process handle. Positively close that exact
                # fake model edge as normal cohort cleanup, preserving the owner.
                if h.worker.native:h.worker.native.close();h.worker.native=None
    finally:
        h.worker._fair_core_handoff=False;h.worker.current_chunk=None
    return chunk['id']


def _turn(h):
    original=_primary_rows(h)
    h.automatic.next_attempt=0.
    h.worker._automatic_handoff(request=True)
    pending=h.jobs._primary_pending
    if pending and pending['thread'] and pending['thread'].is_alive():
        waiting=h.jobs.permit.primary_intent('worker')
        try:
            assert not waiting.granted and not pending['cancelled'].is_set()
            pending['thread'].run()
        finally:waiting.release()
    live=h.jobs._live
    if live and live['thread'] and live['thread'].is_alive():live['thread'].run()
    h.jobs._maintain_primary()
    h.jobs.maintain_history_comparison()
    comparison=h.jobs._history_comparison
    if comparison and comparison['thread'] and comparison['thread'].is_alive():comparison['thread'].run()
    h.jobs.maintain_history_comparison()
    assert _primary_rows(h)==original


def _primary_rows(h):
    with h.store.db() as db:
        return [dict(row) for row in db.execute('SELECT '+','.join('s.'+field for field in transcript.PRIMARY_FIELDS)+
            ' FROM segments s JOIN chunks c ON c.id=s.chunk_id WHERE c.session_id=? ORDER BY s.id',(h.sid,))]


def _counts(h):
    rows=_observations(h)
    with h.store.db() as db:
        attempts=db.execute(f'SELECT COUNT(*) FROM {history.ATTEMPTS}').fetchone()[0]
    return {'observations':len(rows),'vectors':sum(row['evidence_kind']=='vector' for row in rows),
        'pending':sum(row['evidence_kind']=='pending' for row in rows),
        'candidate_missing':sum(row['evidence_kind']=='candidate_missing' for row in rows),'attempts':attempts}


def _observations(h):
    """Read the bounded real typed rows and their retained admission basis."""
    with h.store.db() as db:
        rows=[dict(row) for row in db.execute(f'''SELECT c.*,a.basis,a.state AS attempt_state,
            a.invocation_id AS attempt_invocation_id FROM {history.CERTIFICATES} c
            JOIN {history.ATTEMPTS} a ON a.token=c.attempt ORDER BY c.scope_seq,c.sequence''')]
        assert len(rows)<=3
        for row in rows:
            assert len(row['observation_id'])==64 and row['basis'] is not None
            assert len(row['invocation_id'])==32 and row['invocation_id']==row['attempt_invocation_id']
            kinds={item[0] for item in db.execute(f'SELECT kind FROM {history.REFS} WHERE attempt=?',(row['attempt'],))}
            assert kinds=={'receipt','packet'}
            dependencies={item[0] for item in db.execute(
                f'SELECT kind FROM {history.DEPENDENCIES} WHERE attempt=?',(row['attempt'],))}
            assert dependencies=={'source','mapping','primary'}
            if row['evidence_kind']=='vector':
                assert row['attempt_state']=='success' and len(row['vector'])==2048
                assert len(row['certificate_id'])==len(row['vector_sha256'])==64
            else:
                assert row['evidence_kind'] in ('pending','candidate_missing')
                assert row['attempt_state']==('reserved' if row['evidence_kind']=='pending' else 'failure')
                assert row['certificate_id'] is row['vector'] is row['vector_sha256'] is None
        for scope in db.execute(f'SELECT * FROM {history.SCOPES} WHERE scope_seq>0'):
            owned=[row for row in rows if row['scope_seq']==scope['scope_seq']]
            assert scope['observation_count']==len(owned)
            assert scope['vector_count']==sum(row['evidence_kind']=='vector' for row in owned)
            assert scope['history_revision']==len(owned)+sum(row['evidence_kind']!='pending' for row in owned)
    return rows


def _observation_identity(row):
    return tuple(row[field] for field in ('scope_seq','sequence','attempt','observation_id','invocation_id','header','basis'))


def _passive_without_positive(h,reason):
    """Pending or unknown-rival saved reads cannot manufacture a positive."""
    before=_primary_rows(h);probes=len(h.probes);task=h.jobs._history_comparison
    thread=task['thread'] if task else None
    response=h.client.get(f'/sessions/{h.sid}/transcript')
    assert response.status_code==200,response.text
    page=response.json()
    assert [row['text'] for row in page['baseline_reading']]==[row['text'] for row in before]
    for row in page['baseline_reading']:
        annotation=row.get('recording_speaker_link')
        assert annotation is None or (annotation['status']!='provisional_linked'
                                      and annotation.get('provisional_speaker_id') is None)
    with h.store.db() as db:
        scope=db.execute(f'SELECT * FROM {history.SCOPES} WHERE scope_seq>0').fetchone()
        headers=[json.loads(row[0]) for row in db.execute(f'SELECT header FROM {history.CERTIFICATES} ORDER BY sequence')]
        saved=history.read_saved_for_refs(db,h.sid,scope['recording_id'],
            [(row['ref_kind'],row['ref_key']) for row in headers])
    assert saved['history_status']=='pending' and saved['reason']==reason and saved['annotations']==[]
    assert len(h.probes)==probes and _primary_rows(h)==before
    assert h.jobs._history_comparison is task and (task is None or task['thread'] is thread)


def _passive_saved(h):
    before=len(h.probes)
    primary_before=_primary_rows(h)
    task_before=h.jobs._history_comparison
    thread_before=task_before['thread'] if task_before else None
    output=h.client.get(f'/sessions/{h.sid}/transcript')
    assert output.status_code==200,output.text
    page=output.json()
    assert [row['text'] for row in page['baseline_reading']]==[row['text'] for row in primary_before]
    assert _primary_rows(h)==primary_before
    assert h.jobs._history_comparison is task_before
    assert task_before is None or task_before['thread'] is thread_before
    with h.store.db() as db:
        scope=db.execute(f'SELECT * FROM {history.SCOPES} WHERE scope_seq>0').fetchone()
        headers=[json.loads(row[0]) for row in db.execute(f'SELECT header FROM {history.CERTIFICATES} ORDER BY sequence')]
        refs=[(row['ref_kind'],row['ref_key']) for row in headers]
        saved=history.read_saved_for_refs(db,h.sid,scope['recording_id'],refs)
    assert len(h.probes)==before and saved['history_status']=='current' and saved['complete']
    counts=_counts(h)
    assert saved['observation_count']==counts['observations'] and saved['vector_count']==counts['vectors']
    public={row['source_ref']:row.get('recording_speaker_link') for row in page['baseline_reading']}
    expected={row['primary_source_ref']:row for row in saved['annotations']}
    assert len([value for value in public.values() if value is not None])==len(expected)
    for ref,row in expected.items():
        annotation=public[ref]
        assert annotation is not None and annotation['primary_source_ref']==ref
        assert annotation['status']==row['status']
        assert annotation['provisional_speaker_id']==row['provisional_speaker_id']
        assert annotation['history_status']=='current' and annotation['speaker_identity_valid'] is False
    return saved


def test_three_natural_groups_aab_real_cleanup_and_passive_read(primary_history_factory,monkeypatch):
    with primary_history_factory() as h:
        cids=ingest_recording(h,frames=50*RATE)
        selected=[];comparisons=[];cache=None;prefix=None
        original=h.jobs.submit_primary
        original_compare=h.jobs._compare_history_snapshot
        def observe_comparison(record,snapshot):
            previous=record.get('prefix')
            result=original_compare(record,snapshot)
            build=record['build']
            comparisons.append({'previous':previous,'prefix':build.score_prefix,
                'old_rows':build._old_rows,'dot_products':build.work.counts.get('dot_products',0),
                'rows':build.score_prefix.row_count,'scores':len(build.score_prefix.scores)})
            return result
        def primary_attempt():selected.append('primary');return original()
        def text_refusal(intent):
            selected.append('text');raise ReadError(409,'synthetic_bounded_text_refusal')
        monkeypatch.setattr(h.jobs,'submit_primary',primary_attempt)
        monkeypatch.setattr(h.jobs,'submit_automatic',text_refusal)
        monkeypatch.setattr(h.jobs,'_compare_history_snapshot',observe_comparison)
        expected_observations=[1,2,2,3,3]
        expected_vectors=[1,1,2,2,3]
        expected_pending=[0,1,0,1,0]
        expected_revisions=[2,3,4,5,6]
        for index,cid in enumerate(cids):
            previous_probe=h.jobs._primary_pending
            previous_rows=_observations(h)
            previous_counts=_counts(h)
            previous_primary=_primary_rows(h)
            assert _cohort(h)==cid
            # Finite manually interleaved cohorts prove progress with a retained
            # cache, not concurrent CPU/GIL throughput or a wall-clock SLO.
            assert h.store.chunk(cid)['next_offset']==(8. if index==4 else 10.5)
            assert _primary_rows(h)[:len(previous_primary)]==previous_primary
            rows=_observations(h)
            probe=h.jobs._primary_pending
            assert probe is not None and probe['state']=='pending'
            if previous_probe is not None:
                # A text turn left this exact claim queued. The next real
                # primary cohort still commits, without another observation
                # or replacing/replaying the retained source opportunity.
                assert index in (2,4) and probe is previous_probe
                assert rows==previous_rows and _counts(h)==previous_counts
            else:
                assert index in (0,1,3) and len(rows)==len(previous_rows)+1
                assert rows[:-1]==previous_rows
            reservation=probe['reservation']
            assert probe['thread'] is probe['operation'] is None
            assert len(probe['crop'])==64000 and h.jobs._probe_staging_bytes==runner.STAGING_BYTES
            assert rows[-1]['evidence_kind']=='pending'
            assert rows[-1]['attempt']==probe['attempt_id']==reservation['token']
            assert rows[-1]['invocation_id']==probe['invocation_id']==reservation['invocation_id']
            with h.store.db() as db:
                pending_claim=dict(db.execute(f'SELECT * FROM {history.ATTEMPTS} WHERE token=?',
                    (probe['attempt_id'],)).fetchone())
            assert pending_claim['state']=='reserved' and pending_claim['reserved_bytes']>0
            assert pending_claim['staging_bytes']>0 and pending_claim['invocation_id']==probe['invocation_id']
            _passive_without_positive(h,'pending')
            identities=[_observation_identity(row) for row in rows]
            _turn(h)
            assert len(h.probes)==expected_vectors[index]
            assert [_observation_identity(row) for row in _observations(h)]==identities
            assert _counts(h)=={'observations':expected_observations[index],'vectors':expected_vectors[index],
                'pending':expected_pending[index],'candidate_missing':0,'attempts':expected_observations[index]}
            current=h.jobs._history_comparison
            assert current['state']=='cached' and current['build'] is None
            if cache is not None:
                assert current is cache and current['prefix'] is prefix
            cache,prefix=current,current['prefix']
            assert prefix.row_count==len(h.probes)
            assert h.jobs.permit.status()['comparison_reserved_bytes']==128*1024**2
            with h.store.db() as db:
                scope=db.execute(f'SELECT * FROM {history.SCOPES} WHERE scope_seq>0').fetchone()
                assert scope['history_revision']==expected_revisions[index]
                assert scope['building_slot'] is None
                if expected_pending[index]:
                    assert scope['current_slot'] is None
                    assert dict(db.execute(f'SELECT * FROM {history.ATTEMPTS} WHERE token=?',
                        (probe['attempt_id'],)).fetchone())==pending_claim
                else:
                    assert scope['current_slot'] is not None
                    decision=db.execute(f'SELECT * FROM {history.DECISIONS} WHERE scope_seq=? AND slot=?',
                        (scope['scope_seq'],scope['current_slot'])).fetchone()
                    assert decision['complete'] and decision['history_revision']==scope['history_revision']
                    assert decision['observation_count']==decision['vector_count']==expected_vectors[index]
            if expected_pending[index]:
                assert h.jobs._primary_pending is probe and probe['reservation'] is reservation
                assert probe['state']=='pending' and probe['thread'] is probe['operation'] is None
                assert _observations(h)==rows
                assert prefix.row_count<expected_observations[index]
                # Cached scores remain internal; the admitted pending rival
                # clears the old public decision until this same claim closes.
                _passive_without_positive(h,'pending')
            else:
                assert h.jobs._primary_pending is None and probe['crop'] is None
        assert selected==['primary','text','primary','text','primary']
        assert [row['old_rows'] for row in comparisons]==[0,1,2]
        assert [row['dot_products'] for row in comparisons]==[0,1,2]
        assert [row['rows'] for row in comparisons]==[1,2,3]
        assert [row['scores'] for row in comparisons]==[0,1,3]
        assert comparisons[0]['previous'] is None
        assert all(row['prefix'] is prefix for row in comparisons)
        assert all(row['previous'] is prefix for row in comparisons[1:])
        assert _counts(h)=={'observations':3,'vectors':3,'pending':0,'candidate_missing':0,'attempts':3}
        assert len(h.probes)==3 and all(item.starts==1 for item in h.probes)
        assert [h.store.chunk(cid)['next_offset'] for cid in cids]==[10.5,10.5,10.5,10.5,8.]
        with h.store.db() as db:
            scope=db.execute(f'SELECT * FROM {history.SCOPES} WHERE scope_seq>0').fetchone()
            assert scope['observation_count']==scope['vector_count']==3 and scope['history_revision']==6
            assert scope['current_slot'] is not None
            saved=db.execute(f'SELECT * FROM {history.DECISIONS} WHERE scope_seq=? AND slot=?',
                             (scope['scope_seq'],scope['current_slot'])).fetchone()
            assert saved['complete'] and saved['history_revision']==scope['history_revision']
            assert saved['observation_count']==saved['vector_count']==3
            headers=[json.loads(row[0]) for row in db.execute(f'SELECT header FROM {history.CERTIFICATES} ORDER BY sequence')]
            assert len({row['pcm_hash'] for row in headers})==3
            assert db.execute(f'SELECT COUNT(*) FROM {history.ROWS} WHERE kind=\'annotation\' AND scope_seq=? AND slot=?',
                              (scope['scope_seq'],scope['current_slot'])).fetchone()[0]==3
        assert h.jobs._primary_pending is None and h.jobs._probe_staging_bytes==0
        assert h.jobs.permit._optional is None
        assert h.jobs._history_comparison['state']=='cached'
        assert h.jobs.permit.status()['comparison_reserved_bytes']==128*1024**2
        saved=_passive_saved(h)
        assert [row['status'] for row in saved['annotations']]==['provisional_linked','provisional_linked','source_scoped']
        assert saved['annotations'][0]['provisional_speaker_id']==saved['annotations'][1]['provisional_speaker_id']


def test_two_natural_groups_abstain_without_competitor(primary_history_factory,monkeypatch):
    with primary_history_factory() as h:
        cids=ingest_recording(h,frames=30*RATE)
        monkeypatch.setattr(h.jobs,'submit_automatic',lambda intent:(_ for _ in ()).throw(
            ReadError(409,'synthetic_bounded_text_refusal')))
        for cid in cids:
            assert _cohort(h)==cid
            _turn(h)
        assert [h.store.chunk(cid)['next_offset'] for cid in cids]==[10.5,10.5,9.]
        assert len(h.probes)==2
        assert _counts(h)=={'observations':2,'vectors':2,'pending':0,'candidate_missing':0,'attempts':2}
        with h.store.db() as db:
            scope=db.execute(f'SELECT * FROM {history.SCOPES} WHERE scope_seq>0').fetchone()
            assert scope['observation_count']==scope['vector_count']==2 and scope['history_revision']==4
        saved=_passive_saved(h)
        assert len(saved['annotations'])==2
        assert all(row['status']=='source_scoped' and row['provisional_speaker_id'] is None
                   for row in saved['annotations'])


@pytest.mark.parametrize('failure',['optional_copy','commit_false','commit_unknown','seam'])
def test_optional_abort_and_primary_unknown_ack_never_probe(primary_history_factory,monkeypatch,failure):
    with primary_history_factory() as h:
        cid=ingest_recording(h,frames=10*RATE)[0]
        if failure=='optional_copy':
            monkeypatch.setattr(h.jobs,'prepare_primary_copy',lambda *a,**kw:(_ for _ in ()).throw(ValueError('synthetic')))
        elif failure=='seam':
            monkeypatch.setattr(h.capture,'prepare_core_seam',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('synthetic')))
        else:
            original=h.store.commit_window
            def commit(*args):
                if failure=='commit_false':return False
                assert original(*args)
                raise RuntimeError('synthetic lost primary acknowledgement')
            monkeypatch.setattr(h.store,'commit_window',commit)
        if failure in ('commit_unknown','seam'):
            with pytest.raises(RuntimeError):_cohort(h)
        else:_cohort(h)
        assert h.jobs._primary_pending is None and h.jobs._probe_staging_bytes==0
        assert not h.probes and _counts(h)=={
            'observations':0,'vectors':0,'pending':0,'candidate_missing':0,'attempts':0}
        assert h.store.chunk(cid)['next_offset']==(10. if failure in ('optional_copy','commit_unknown') else 0.)


@pytest.mark.parametrize('phase',['claim','append'])
def test_lost_history_ack_resolves_storage_without_second_invocation(primary_history_factory,monkeypatch,phase):
    with primary_history_factory() as h:
        ingest_recording(h,frames=10*RATE)
        name='claim_primary' if phase=='claim' else 'append_primary'
        original=getattr(h.jobs.history,name);calls=[]
        def lost(*args,**kwargs):
            value=original(*args,**kwargs);calls.append(value)
            raise ReadError(503,'synthetic_lost_ack')
        monkeypatch.setattr(h.jobs.history,name,lost)
        _cohort(h)
        admitted=_observations(h)
        assert len(admitted)==1 and admitted[0]['evidence_kind']=='pending'
        assert admitted[0]['invocation_id']==h.jobs._primary_pending['invocation_id']
        _passive_without_positive(h,'pending')
        if phase=='append':_turn(h)
        else:h.jobs._maintain_primary()
        assert len(calls)==1
        assert len(h.probes)==(1 if phase=='append' else 0)
        assert _counts(h)=={'observations':1,'vectors':int(phase=='append'),
            'pending':0,'candidate_missing':int(phase=='claim'),'attempts':1}
        assert _observation_identity(_observations(h)[0])==_observation_identity(admitted[0])
        assert h.jobs._primary_pending is None
        for _ in range(2):h.jobs._maintain_primary()
        assert len(calls)==1 and len(h.probes)==(1 if phase=='append' else 0)


def test_fresh_probe_cleanup_proof_retains_owner_until_positive_closure(primary_history_factory,monkeypatch):
    with primary_history_factory() as h:
        cid=ingest_recording(h,frames=10*RATE)[0];assert _cohort(h)==cid
        committed=_primary_rows(h);probe=h.jobs._primary_pending
        admitted=_observations(h)[0]
        assert admitted['evidence_kind']=='pending' and admitted['invocation_id']==probe['invocation_id']
        _passive_without_positive(h,'pending')
        original=h.jobs._probe_factory
        def unclosed(directory,digest):
            driver=original(directory,digest);driver.cleanup_ok=False;return driver
        monkeypatch.setattr(h.jobs,'_probe_factory',unclosed)
        assert h.jobs.permit.request_automatic_turn()
        with h.jobs.permit.automatic_turn() as granted:
            assert granted and h.jobs.submit_primary()
        probe['thread'].run()
        operation=probe['operation'];driver=h.probes[0]
        assert type(operation) is runner.ProbeOperation and operation.started and not operation.cleaned
        assert probe['state']=='cleanup_unknown' and probe['completed'].is_set()
        assert h.jobs._primary_pending is probe and h.jobs.permit._optional is probe
        assert probe['crop'] is not None and h.jobs._probe_staging_bytes==runner.STAGING_BYTES
        assert operation.claim is not None and (h.jobs.directory/'owner.json').exists()
        assert not h.jobs._maintain_primary() and not h.jobs.submit_primary()
        assert not h.jobs._stop_primary()
        assert h.jobs._primary_pending is probe and h.jobs.permit._optional is probe
        assert not operation.cleaned and driver.cleanups==2
        with h.store.db() as db:
            attempt=db.execute(f'SELECT * FROM {history.ATTEMPTS} WHERE token=?',(probe['attempt_id'],)).fetchone()
            assert attempt['state']=='reserved' and attempt['staging_bytes']>0
        assert _counts(h)=={'observations':1,'vectors':0,'pending':1,'candidate_missing':0,'attempts':1}
        _passive_without_positive(h,'pending')
        driver.cleanup_ok=True
        assert h.jobs._stop_primary()
        assert operation.cleaned and runner.cleanup_verified(operation.receipt)
        assert driver.cleanups==3 and driver.starts==1 and len(h.probes)==1
        assert h.jobs._primary_pending is None and h.jobs.permit._optional is None
        assert probe['crop'] is None and h.jobs._probe_staging_bytes==0
        assert not tuple(h.jobs.directory.iterdir())
        assert _counts(h)=={'observations':1,'vectors':0,'pending':0,'candidate_missing':1,'attempts':1}
        assert _observation_identity(_observations(h)[0])==_observation_identity(admitted)
        with h.store.db() as db:
            attempt=db.execute(f'SELECT * FROM {history.ATTEMPTS} WHERE token=?',(probe['attempt_id'],)).fetchone()
            assert attempt['state']=='failure' and attempt['reserved_bytes']==attempt['staging_bytes']==0
        assert h.jobs._maintain_primary() and not h.jobs.submit_primary()
        assert driver.starts==1 and _primary_rows(h)==committed and h.store.chunk(cid)['next_offset']==10.
        # The retained missing candidate is an unknown rival, never silently
        # omitted from the real complete comparator or passive HTTP view.
        h.jobs.maintain_history_comparison()
        comparison=h.jobs._history_comparison
        assert comparison is not None and comparison['thread'].is_alive()
        comparison['thread'].run()
        assert comparison['build'].state=='refused' and comparison['build'].reason=='unknown_rival'
        assert h.jobs._stop_history_comparison()
        _passive_without_positive(h,'unknown_rival')
        assert driver.starts==1 and len(h.probes)==1


def test_manager_staging_refusal_consumes_source_opportunity_after_primary_commit(primary_history_factory,monkeypatch):
    with primary_history_factory() as h:
        cid=ingest_recording(h,frames=10*RATE)[0];acknowledged=[];handles=[]
        original=h.jobs.primary_committed
        def observe_commit(handle):
            acknowledged.append(_primary_rows(h));handles.append(handle)
            return original(handle)
        monkeypatch.setattr(h.jobs,'primary_committed',observe_commit)
        # The real manager copies/bounds the normal draft, then refuses its
        # deliberately tiny staging grant; primary SQL remains untouched.
        with monkeypatch.context() as capacity:
            capacity.setattr(runner,'STAGING_BYTES',1)
            assert _cohort(h)==cid
        assert len(handles)==len(acknowledged)==1 and acknowledged[0]
        handle=handles[0]
        assert handle['state']=='terminal' and handle['reason']=='unavailable'
        assert handle['crop'] is None and handle['charge_bytes']==0
        assert h.jobs._primary_pending is None and h.jobs._probe_staging_bytes==0
        assert _primary_rows(h)==acknowledged[0] and h.store.chunk(cid)['next_offset']==10.
        assert _counts(h)=={'observations':0,'vectors':0,'pending':0,'candidate_missing':0,'attempts':0} and not h.probes
        with h.store.db() as db:
            scope=dict(db.execute(f'SELECT * FROM {history.SCOPES} WHERE scope_seq>0').fetchone())
        assert scope['high_water']==handle['source_start_frame'] and scope['skipped_count']==1
        for _ in range(2):
            assert h.jobs.history.primary_due(h.sid,handle['recording_id'],handle['source_start_frame'])=={
                'due':False,'reason':'missed'}
            assert h.jobs._maintain_primary() and not h.jobs.submit_primary()
        assert _primary_rows(h)==acknowledged[0] and not h.probes


def test_unknown_primary_thread_start_ack_retains_owner_and_never_replays(primary_history_factory,monkeypatch):
    with primary_history_factory() as h:
        cid=ingest_recording(h,frames=10*RATE)[0];assert _cohort(h)==cid
        committed=_primary_rows(h);probe=h.jobs._primary_pending;starts=[]
        admitted=_observations(h)[0]
        assert admitted['evidence_kind']=='pending' and admitted['invocation_id']==probe['invocation_id']
        original=h.jobs._thread_factory
        def lose_ack(**kwargs):
            thread=original(**kwargs)
            if kwargs['name']=='primary-speaker-probe':
                start=thread.start
                def unknown():
                    starts.append(thread);start();raise RuntimeError('synthetic lost thread-start acknowledgement')
                thread.start=unknown
            return thread
        monkeypatch.setattr(h.jobs,'_thread_factory',lose_ack)
        assert h.jobs.permit.request_automatic_turn()
        with h.jobs.permit.automatic_turn() as granted:
            assert granted and not h.jobs.submit_primary()
        assert starts==[probe['thread']] and probe['thread'].is_alive()
        assert probe['state']=='thread_unknown' and not probe['completed'].is_set()
        assert h.jobs.permit._optional is probe and probe['crop'] is not None
        assert h.jobs._probe_staging_bytes==runner.STAGING_BYTES
        assert not h.jobs._maintain_primary() and not h.jobs._stop_primary()
        assert not h.jobs.submit_primary() and starts==[probe['thread']]
        assert h.jobs._primary_pending is probe and h.jobs.permit._optional is probe
        assert _counts(h)=={'observations':1,'vectors':0,'pending':1,'candidate_missing':0,'attempts':1}
        _passive_without_positive(h,'pending')
        # Only completion of the original possibly-started thread closes it.
        # Cancellation is observed before a fresh ProbeOperation is constructed.
        probe['thread'].run()
        assert probe['completed'].is_set() and probe['operation'] is None
        assert h.jobs._maintain_primary()
        assert h.jobs._primary_pending is None and h.jobs.permit._optional is None
        assert probe['crop'] is None and h.jobs._probe_staging_bytes==0
        assert not h.jobs.submit_primary() and starts==[probe['thread']] and not h.probes
        assert _counts(h)=={'observations':1,'vectors':0,'pending':0,'candidate_missing':1,'attempts':1}
        assert _observation_identity(_observations(h)[0])==_observation_identity(admitted)
        with h.store.db() as db:
            row=db.execute(f'SELECT * FROM {history.ATTEMPTS} WHERE token=?',(probe['attempt_id'],)).fetchone()
            assert row['state']=='failure' and row['reserved_bytes']==row['staging_bytes']==0
        assert _primary_rows(h)==committed and h.store.chunk(cid)['next_offset']==10.


@pytest.mark.parametrize('action',['configuration_off','stop'])
def test_pending_crop_configuration_off_or_stop_closes_only_unstarted_owner(primary_history_factory,action):
    with primary_history_factory() as h:
        cid=ingest_recording(h,frames=10*RATE)[0];assert _cohort(h)==cid
        committed=_primary_rows(h);probe=h.jobs._primary_pending
        admitted=_observations(h)[0]
        assert admitted['evidence_kind']=='pending' and admitted['invocation_id']==probe['invocation_id']
        assert probe['state']=='pending' and probe['crop'] is not None
        assert probe['thread'] is probe['operation'] is None
        assert h.jobs._probe_staging_bytes==runner.STAGING_BYTES
        if action=='configuration_off':
            h.store.settings.recording_speaker_links_enabled=False
            _turn(h)
        else:assert h.jobs.stop()
        assert probe['thread'] is probe['operation'] is None and not h.probes
        assert h.jobs._primary_pending is None and h.jobs._probe_staging_bytes==0
        assert probe['crop'] is None and h.jobs.permit._optional is None
        assert _counts(h)=={'observations':1,'vectors':0,'pending':0,'candidate_missing':1,'attempts':1}
        assert _observation_identity(_observations(h)[0])==_observation_identity(admitted)
        with h.store.db() as db:
            row=db.execute(f'SELECT * FROM {history.ATTEMPTS} WHERE token=?',(probe['attempt_id'],)).fetchone()
            assert row['state']=='failure' and row['reserved_bytes']==row['staging_bytes']==0
        assert h.jobs._maintain_primary() and not h.jobs.submit_primary()
        assert _primary_rows(h)==committed and h.store.chunk(cid)['next_offset']==10.


def test_primary_permit_is_thread_bound_and_queued_primary_does_not_preempt(primary_history_factory):
    with primary_history_factory() as h:
        ingest_recording(h,frames=10*RATE);_cohort(h)
        permit=h.jobs.permit;probe=h.jobs._primary_pending
        from audio_backend.native_execution_permit import AdmissionError
        with pytest.raises(AdmissionError):
            permit.reserve_optional(probe,probe['cancelled'],probe['cancelled'].set,owner_kind='automatic_primary_probe')
        assert permit.request_automatic_turn()
        with permit.automatic_turn() as granted:
            assert granted and h.jobs.submit_primary()
        waiting=permit.primary_intent('worker')
        try:
            assert not waiting.granted and not probe['cancelled'].is_set()
            probe['thread'].run()
            assert not probe['cancelled'].is_set()
        finally:waiting.release()
        h.jobs._maintain_primary()
        assert _counts(h)=={'observations':1,'vectors':1,'pending':0,'candidate_missing':0,'attempts':1}


def test_standalone_graph_capacity_refuses_without_truncation():
    _,cell,packets=synthetic_primary_case()
    draft={'packet':{key:value for key,value in packets[0]['packet'].items() if key!='refs'}}
    original=deepcopy(draft)
    draft['packet']['units']=draft['packet']['units']*(primary.MAX_STANDALONE_UNITS+1)
    before=deepcopy(draft)
    with pytest.raises(Exception,match='primary_probe_staging_capacity'):
        primary.copy_standalone_primary(draft,cell.pcm,runner.expected_space()[1])
    assert draft==before and original['packet']['rows']==draft['packet']['rows']
