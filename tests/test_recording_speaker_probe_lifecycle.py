"""Model-free text-first ownership tests with existing synthetic owner fixtures."""
from copy import deepcopy
import hashlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import recording_speaker_probe_runner as runner
from audio_backend import recording_speaker_links as links
from audio_backend import transcript_contract as contract
from audio_backend.native_owned_support_cache import ReadError
from test_moss_automatic_integration import automatic_cell_jobs, manager_intent, result_rows
from test_moss_handy_controller import clean
from test_recording_speaker_links import vector
from test_transcript_http import data, SID


class SyntheticLinkStore:
    """Only optional SQL is stubbed; durable MOSS result/store stays real."""
    def __init__(self, fixture, failure=None):
        self.f, self.failure = fixture, failure
        self.reservations, self.commits, self.recomputes, self.resolutions = [], [], [], []

    def fail(self, phase):
        if self.failure == phase:raise ReadError(503, 'result_storage_unavailable'
            if phase in ('reserve','commit') else 'probe_unavailable')

    def capture_policy(self, sid, proof):
        return links.POLICY_SHA256_V2

    def capture_primary_packets(self, proof):
        return []

    def recording_for_job(self, sid, jid, sha):
        self.fail('binding')
        rows = result_rows(self.f.store)
        assert rows[0]['state'] == 'committed' and rows[0]['result_sha256'] == sha
        assert self.f.owner._live['lease'].released
        return 'synthetic-recording'

    def reserve(self, *args, **kwargs):
        self.fail('reserve')
        self.reservations.append((args, kwargs))
        return {'scope_id':'1'*64, 'token':'2'*32, 'should_probe':True, 'state':'links_stale'}

    def commit(self, admission, batch, state):
        self.fail('commit')
        self.commits.append((deepcopy(batch),state))
        return {'revision':2}

    def resolve_attempt(self, sid, rid, jid, sha, prepared, *, state, token=None):
        self.resolutions.append((jid,sha,deepcopy(prepared),state,token))
        assert all('vector' not in node for node in prepared['nodes'])
        return {'resolved':True,'state':state,'scope_id':'1'*64}

    def recompute_scope(self, scope, callback, *, cancelled=None):
        self.fail('recompute')
        self.recomputes.append(scope)
        return {'saved':not cancelled.is_set(), 'state':'source_scoped'}


def setup_probe(f, monkeypatch, failure=None, clean_result=True, preempt=False, aliases=False):
    f.store.settings.recording_speaker_links_enabled = True
    f.store.settings.recording_speaker_model_path = '/synthetic-no-model.onnx'
    sidecar = SyntheticLinkStore(f, failure)
    f.owner.speaker_links = sidecar
    observations, drivers, primary = [], [], []

    def capture(support, pcm, proof, projection, job_id, result_sha256, space, *, primary_packets=()):
        assert not f.owner._live['lease'].released
        assert f.owner._probe_staging_bytes == runner.STAGING_BYTES
        assert f.drivers[0].starts == 1
        assert f.owner._live['controller'].status(f.owner._live['controller_id'])['cleanup']['cleanup_verified']
        assert result_rows(f.store)[0]['state'] == 'accepted'
        observations.append('after_moss_reap_before_text')
        if failure == 'crop':raise ValueError('synthetic crop failure')
        nodes = [{'phrase_index':0,'probe_index':0}]
        if aliases:nodes.append({'source_kind':'native_primary','primary_source_ref':'p.1.'+'a'*64,'probe_index':0})
        return {'job_id':job_id, 'result_sha256':result_sha256,
            'policy_sha256':links.POLICY_SHA256_V2,
            'nodes':nodes, 'outcomes':[]}, (b'\1\0'*32000,)

    class Driver:
        def __init__(self,directory,sha):
            self.directory=directory; self.sha=sha; self._service_gate=None
            drivers.append(self)
        def start(self,cancelled):
            observations.append('probe_start_after_text')
            assert result_rows(f.store)[0]['state'] == 'committed'
            assert f.permit._optional is f.owner._live['speaker_probe']
            assert cancelled is not f.owner._live['cancelled']
            assert f.owner._live['lease'].released
            assert not f.owner._lock._is_owned()
            if preempt:primary.append(f.permit.primary_intent('probe'))
            if failure == 'start':raise RuntimeError('synthetic start failure')
        def wait(self,cancelled):
            if cancelled.is_set():raise RuntimeError('synthetic interrupted')
            if failure == 'wait':raise RuntimeError('synthetic extraction failure')
            request=json.loads((self.directory/'request.json').read_bytes())
            return {'kind':runner.KIND,'job_id':request['job_id'],'space':runner.expected_space()[0],
                'space_id':request['space_id'],'compute_attempts':1,
                'nodes':[{'index':0,'pcm_sha256':request['crops'][0]['pcm_sha256'],'vector':vector(1.)}]}
        def cleanup(self,stop):
            return clean() if clean_result else {'cleanup_verified':False}
    monkeypatch.setattr(links,'capture_candidates_v2',capture)
    f.owner._probe_factory=Driver
    cell,_=f.ready_cell(frames=16)
    intent=manager_intent(f,cell)
    f.owner.submit_automatic(intent)
    return sidecar,observations,drivers,primary


def test_typed_alias_nodes_charge_twice_but_probe_one_index(automatic_cell_jobs,monkeypatch):
    f = automatic_cell_jobs
    sidecar,events,drivers,_ = setup_probe(f,monkeypatch,aliases=True)
    record = f.finish()
    assert record['durable_result_available'] and len(drivers) == 1
    assert sidecar.reservations[0][1]['max_nodes'] == 2
    batch, state = sidecar.commits[0]
    assert state == 'source_scoped' and len(batch['nodes']) == 2
    assert batch['nodes'][0]['vector'] == batch['nodes'][1]['vector'] == vector(1.)
    assert f.owner._probe_staging_bytes == 0 and f.permit._optional is None


@pytest.mark.parametrize('failure',[None,'crop','binding','reserve','start','wait','commit','recompute'])
def test_durable_text_survives_every_optional_phase(automatic_cell_jobs,monkeypatch,failure):
    f=automatic_cell_jobs
    sidecar,events,drivers,primary=setup_probe(f,monkeypatch,failure)
    record=f.finish()
    assert record['durable_result_available'] and not record['storage_pending']
    assert not record['cancelled'].is_set()
    assert result_rows(f.store)[0]['state']=='committed'
    assert f.owner.results.result('session',record['job_id'])==record['history_snapshot'].result_json
    if failure in ('reserve','commit'):
        assert record['speaker_probe']['commit_pending']
        sidecar.failure=None
        assert f.owner.automatic_idle()
    assert f.owner._probe_staging_bytes==0
    assert f.permit._optional is None
    assert sum(driver.starts for driver in f.drivers)==1
    if drivers:assert events==['after_moss_reap_before_text','probe_start_after_text']


def test_pure_capacity_refusal_never_probes_or_reconciles(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs
    sidecar,events,drivers,_=setup_probe(f,monkeypatch)
    def refuse(*args,**kwargs):
        sidecar.reservations.append((args,kwargs))
        return {'scope_id':'1'*64,'token':None,'should_probe':False,'state':'capacity_exhausted'}
    def forbidden(*args,**kwargs): pytest.fail('Pure capacity refusal ran optional computation')
    monkeypatch.setattr(sidecar,'reserve',refuse)
    monkeypatch.setattr(sidecar,'recompute_scope',forbidden)
    monkeypatch.setattr(runner,'ProbeOperation',forbidden)
    monkeypatch.setattr(runner,'ProbeDriver',forbidden)
    record=f.finish()
    assert record['durable_result_available'] and not record['storage_pending']
    assert f.owner.results.result('session',record['job_id'])==record['history_snapshot'].result_json
    probe=record['speaker_probe']
    assert probe['state']=='capacity_exhausted' and probe['reservation']['token'] is None
    assert not probe['commit_pending'] and not record['cancelled'].is_set()
    assert len(sidecar.reservations)==1
    assert not sidecar.commits and not sidecar.recomputes and not sidecar.resolutions and not drivers
    assert events==['after_moss_reap_before_text']
    assert f.permit._optional is None and f.owner._probe_staging_bytes==0


def test_primary_preemption_cancels_probe_only(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs
    sidecar,events,drivers,primary=setup_probe(f,monkeypatch,preempt=True)
    try:
        record=f.finish()
        assert record['speaker_probe']['cancelled'].is_set()
        assert not record['cancelled'].is_set() and record['durable_result_available']
        assert sidecar.commits[0][1]=='probe_interrupted'
        assert f.permit._optional is None
    finally:
        for intent in primary:intent.release()


def test_uncertain_probe_cleanup_retains_permit_claim_and_stage(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs
    sidecar,events,drivers,primary=setup_probe(f,monkeypatch,clean_result=False)
    record=f.finish();probe=record['speaker_probe']
    assert record['durable_result_available'] and not record['cancelled'].is_set()
    assert probe['operation'].driver is drivers[0]
    assert f.permit._optional is probe and f.owner._probe_staging_bytes==runner.STAGING_BYTES
    assert (f.owner.directory/'owner.json').exists()
    assert not f.owner.automatic_idle()
    with pytest.raises(ReadError):f.owner._retire(record)
    # Explicit fake cleanup recovery uses the same handle, never another start.
    drivers[0].cleanup=lambda stop:clean()
    assert f.owner._cleanup_speaker_probe(record)
    assert f.permit._optional is None and f.owner._probe_staging_bytes==0
    assert len(drivers)==1


def test_optional_import_or_config_failure_cannot_skip_finish(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs
    f.store.settings.recording_speaker_links_enabled=True
    cell,_=f.ready_cell(frames=16)
    f.owner.submit_automatic(manager_intent(f,cell))
    monkeypatch.setattr(f.owner,'_stage_speaker_probe',lambda _:(_ for _ in ()).throw(ImportError('synthetic')))
    record=f.finish()
    assert record['durable_result_available'] and result_rows(f.store)[0]['state']=='committed'


def test_storage_retry_never_starts_a_probe(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs
    sidecar,events,drivers,primary=setup_probe(f,monkeypatch)
    original=f.owner.results.commit
    monkeypatch.setattr(f.owner.results,'commit',lambda *a,**kw:(_ for _ in ()).throw(ReadError(503,'synthetic')))
    record=f.finish()
    assert record['storage_pending'] and not drivers
    assert record['speaker_probe']['state']=='probe_unavailable'
    assert f.owner._probe_staging_bytes==0
    monkeypatch.setattr(f.owner.results,'commit',original)
    assert f.owner.retry_storage('session',record['job_id'],f.owner.jobs_epoch)[0]==200
    assert not drivers and record['durable_result_available']


@pytest.mark.parametrize('state',['provisional_linked','capacity_exhausted'])
def test_annotation_whitelist_preserves_source_content_and_never_exports_vectors(data,monkeypatch,state):
    job=data.job(1,0,10.5)
    before=data.reader.evidence(SID,'m.'+job['jid']+'.'+job['sha']+'.all')
    data.reader.speaker_links_enabled=True
    saved={'state':state,'revision':2,'decision_revision':2,'policy_sha256':links.POLICY_SHA256,
        'space_id':runner.expected_space()[1],'scope_id':'1'*64,
        'annotations':[{'job_id':job['jid'],'result_sha256':job['sha'],'phrase_index':0,
            'status':'provisional_linked','provisional_speaker_id':'recording-speaker-synthetic',
            'node_id':'2'*64,'vector':vector(1.),'proof':{'private':True}}]}
    from audio_backend import recording_speaker_link_store as store
    monkeypatch.setattr(store,'read_session_revision',lambda *_:'3'*64)
    monkeypatch.setattr(store,'read_saved_for_recording',lambda *_:saved)
    page=data.page()
    raw=json.dumps(page)
    assert 'recording-speaker-synthetic' in raw and '"vector"' not in raw and '"private"' not in raw
    assert data.reader.evidence(SID,'m.'+job['jid']+'.'+job['sha']+'.all')==before


def test_default_off_does_not_import_optional_model_modules():
    import sys
    from audio_backend.settings import Settings
    settings=Settings(start_worker=False)
    assert not settings.recording_speaker_links_enabled
    assert 'sherpa_onnx' not in sys.modules


def test_probe_lost_commit_ack_retries_same_bytes_without_inference(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs
    sidecar,events,drivers,primary=setup_probe(f,monkeypatch)
    original=sidecar.commit
    seen=[]
    def lost_ack(admission,batch,state):
        seen.append(contract.encoded(batch))
        if len(seen)==1:
            original(admission,batch,state)
            raise ReadError(503,'synthetic_lost_ack')
        assert seen[0]==seen[1]
        return {'revision':2}
    sidecar.commit=lost_ack
    record=f.finish()
    assert record['speaker_probe']['commit_pending']
    assert f.permit._optional is None and record['durable_result_available']
    assert f.owner.automatic_idle()
    assert len(drivers)==1 and len(sidecar.commits)==1
    assert f.owner._probe_staging_bytes==0


def test_session_delete_while_probe_commit_pending_discards_only_own_copy(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs
    sidecar,events,drivers,primary=setup_probe(f,monkeypatch,failure='commit')
    record=f.finish()
    assert record['speaker_probe']['commit_pending'] and f.permit._optional is None
    f.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'");f.db.commit()
    f.owner.invalidate_session('session')
    assert not record['speaker_probe']['commit_pending']
    assert record['speaker_probe']['batch'] is None and f.owner._probe_staging_bytes==0
    assert len(drivers)==1


def test_lost_reserve_ack_is_terminal_without_any_probe_spawn(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs
    sidecar,events,drivers,primary=setup_probe(f,monkeypatch)
    original=sidecar.reserve
    def lost_ack(*args,**kwargs):
        original(*args,**kwargs)
        raise ReadError(503,'result_storage_unavailable')
    sidecar.reserve=lost_ack
    record=f.finish()
    assert record['durable_result_available'] and not drivers
    assert record['speaker_probe']['commit_pending'] and f.permit._optional is None
    assert f.owner.automatic_idle()
    assert len(sidecar.resolutions)==1 and not drivers
    assert record['speaker_probe']['state']=='probe_unavailable'
    assert f.owner._probe_staging_bytes==0


def test_prechild_construction_failure_resolves_same_consumed_attempt(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs
    sidecar,events,drivers,primary=setup_probe(f,monkeypatch)
    monkeypatch.setattr(runner,'ProbeOperation',lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('synthetic construction failure')))
    record=f.finish()
    assert record['durable_result_available'] and not drivers
    assert record['speaker_probe']['commit_pending'] and f.permit._optional is None
    assert f.owner.automatic_idle()
    assert len(sidecar.resolutions)==1 and len(sidecar.reservations)==1
    assert not drivers and f.owner._probe_staging_bytes==0


def test_corrupt_saved_annotation_does_not_hide_committed_text(data,monkeypatch):
    job=data.job(1,0,10.5)
    before=data.reader.evidence(SID,'m.'+job['jid']+'.'+job['sha']+'.all')
    data.reader.speaker_links_enabled=True
    from audio_backend import recording_speaker_link_store as store
    monkeypatch.setattr(store,'read_session_revision',lambda *_:'3'*64)
    def unavailable(*args):raise ReadError(503,'probe_unavailable')
    monkeypatch.setattr(store,'read_saved_for_recording',unavailable)
    page=data.page()
    assert 'probe_unavailable' in json.dumps(page)
    assert data.reader.evidence(SID,'m.'+job['jid']+'.'+job['sha']+'.all')==before


@pytest.mark.parametrize('headroom',[0,100])
def test_optional_annotation_capacity_preserves_exact_text_and_evidence(headroom):
    value={'reading_status':'provisional_display_preference','reading':[{'text':'',
        'source_ref':'m.'+'1'*32+'.'+'2'*64+'.0','content_sha256':'3'*64,
        'speaker_scope':'source','person_identity':False}]}
    value['reading'][0]['text']='x'*(contract.MAX_RESPONSE_BYTES-len(contract.encoded(value))-headroom)
    baseline=deepcopy(value)
    assert len(contract.response(baseline))==contract.MAX_RESPONSE_BYTES-headroom
    value['reading'][0]['recording_speaker_link']={'provisional_speaker_id':'synthetic','extra':'z'*400}
    value['recording_speaker_links']={'enabled':True,'scopes':[]}
    returned=json.loads(contract.transcript_response(value))
    assert 'recording_speaker_links' not in returned
    assert returned.pop('reading_status')=='provisional_links_omitted_cap'
    baseline.pop('reading_status')
    assert returned==baseline


def test_existing_idle_path_continues_bounded_model_free_reconciliation(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs
    sidecar,events,drivers,primary=setup_probe(f,monkeypatch)
    record=f.finish()
    calls=[]
    def reconcile(**kwargs):
        assert not f.owner._lock._is_owned()
        assert kwargs['limit']==16
        calls.append(kwargs)
        return {'remaining_work':True}
    sidecar.reconcile=reconcile
    assert f.owner.automatic_idle()
    assert len(calls)==1 and len(drivers)==1
    assert f.owner.automatic_idle() and len(calls)==1
    f.owner._probe_reconcile_after=0
    assert f.owner.automatic_idle() and len(calls)==2 and len(drivers)==1


@pytest.mark.parametrize('reason',[
    'bounded_inventory_changed','bounded_inventory_source_changed','bounded_compact_changed'])
@pytest.mark.parametrize('resolution_lost_ack',[False,True])
def test_bounded_publication_revocation_resolves_without_native_replay(
        automatic_cell_jobs,monkeypatch,reason,resolution_lost_ack):
    f=automatic_cell_jobs
    sidecar,events,drivers,primary=setup_probe(f,monkeypatch)
    attempts=[]; resolutions=sidecar.resolve_attempt
    def revoked(admission,batch,state):
        attempts.append(contract.encoded(batch))
        raise ReadError(409,reason)
    def resolve(*args,**kwargs):
        assert f.owner._probe_staging_bytes > 0
        if resolution_lost_ack and not sidecar.resolutions:
            resolutions(*args,**kwargs)
            raise ReadError(503,'result_storage_unavailable')
        return resolutions(*args,**kwargs)
    sidecar.commit=revoked;sidecar.resolve_attempt=resolve
    record=f.finish();probe=record['speaker_probe']
    assert record['durable_result_available'] and result_rows(f.store)[0]['state']=='committed'
    assert len(drivers)==1 and len(sidecar.reservations)==1 and not sidecar.commits
    assert not sidecar.recomputes and len(sidecar.resolutions)==1
    assert f.permit._optional is None  # The existing probe child was positively cleaned.
    if resolution_lost_ack:
        assert probe['commit_pending'] and f.owner._probe_staging_bytes > 0
        assert f.owner.automatic_idle()
        assert len(sidecar.resolutions)==2 and len(attempts)==2
        assert attempts[0]==attempts[1]
    else:
        assert len(attempts)==1
    assert not probe['commit_pending'] and probe['state']=='probe_unavailable'
    assert f.owner._probe_staging_bytes==0 and len(drivers)==1
    assert all('vector' not in node for call in sidecar.resolutions for node in call[2]['nodes'])
