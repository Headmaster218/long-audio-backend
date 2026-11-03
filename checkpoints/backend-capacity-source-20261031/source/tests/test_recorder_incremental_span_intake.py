"""Incremental span admission: strict ASGI evidence and separate simulated peers.

Tiny PCM files prove bounded ingestion/replay, not 45 minutes of runtime. The
45-minute geometry case below is explicitly metadata-only and cannot qualify
native inference, physical continuity or end-to-end long-duration processing.
"""
import copy
import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest
from audio_backend import recorder_capture_intake as capture
from audio_backend import recorder_incremental_span_intake as span
from audio_backend import recorder_intake as single
from audio_backend import recording_uploader as u
from audio_backend.recorder_sequence import _record
from audio_backend.recorder_snapshot import create_snapshot
from test_extended_recorder_intake import Peer
from test_recorder_sequence import fixture as segment
from test_recording_session_contract import handler_app, session, HEADERS
from test_strict_uploader_admission import StrictRoutes, AdmissionBoundary


def fixture(tmp_path, count=9, terminal='INTERRUPTION'):
    paths = []
    for index in range(count):
        path = tmp_path / ('delivery-' + str(index))
        segment(path, 23+index, epoch=7, start=1000+4*index, boundary='RESTART',
                run='selected-process', close=terminal if index==count-1 else 'ROTATION', seam=index>0)
        paths.append(path)
    return paths, dict(job_dir=tmp_path/'job', api='http://127.0.0.1:8765',
        session_id='known', capture_id='extended', exclusive_writer=True,
        acknowledge_span_local_closure=True,
        limits=u.Limits(disk_floor=0, deadline_seconds=120, poll_seconds=.001))


@pytest.mark.parametrize('lose_put', [False, True])
def test_nine_original_deliveries_reach_actual_strict_asgi_sources(tmp_path, lose_put):
    paths, args = fixture(tmp_path)
    original_hashes = [{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()} for path in paths]
    with handler_app(tmp_path/'server') as (client, app):
        routes = StrictRoutes(client, lose_put)
        args.update(session_id=session(client), transport=routes)
        for index, path in enumerate(paths):
            result = span.run(**args, delivery=path, start=index==0)
            assert result['inputs']==index+1 and result['staged_frames']==4*(index+1)
            assert result['published_frames']==0 and not result['capture_finalized']
            assert result['span']['original_epoch']==7
            assert result['span']['original_first_sequence']==23
            assert result['span']['original_epoch_frame_start']==1000
            assert not result['analysis_completion_verified']
        assert all(method=='GET' for method, _, _ in routes.calls)
        assert span.run(**args, delivery=paths[-1])==result
        with pytest.raises(AdmissionBoundary):
            span.run(**args, finalize=True)
        saved = json.loads((args['job_dir']/'uploader/uploader.json').read_text())
        assert saved['finalize_requested'] and saved['active_page']['seal']['eos'] is True
        assert saved['active_page']['seal']['end_frame']==36
        assert len(routes.puts)==9 and len(set(key for key, _ in routes.puts))==9
        page = client.get('/sessions/'+args['session_id']+'/recordings/extended/pages/1',headers=HEADERS).json()
        assert not page['released'] and len(page['sources'])==9
        assert [s['source_start_frame'] for s in page['sources']]==list(range(0,36,4))
        committed = list(routes.puts)
        with pytest.raises(AdmissionBoundary):
            span.run(**args, finalize=True)
        assert routes.puts==committed
        assert [{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()} for path in paths]==original_hashes
        assert not app.state.worker.thread.is_alive()
        with app.state.store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM recording_work').fetchone()[0]==0


class LostResponsePeer(Peer):
    """Synthetic successful publication only; never real ASGI/native evidence."""
    def __init__(self):
        super().__init__(); self.lost = set()

    def request(self, method, path, **kwargs):
        result = super().request(method, path, **kwargs)
        kind = 'put' if method=='PUT' else 'seal' if path.endswith('/seal') else None
        if kind and kind not in self.lost:
            self.lost.add(kind)
            raise u.Uncertain('Synthetic committed '+kind+' response loss')
        return result


def test_nine_short_inputs_synthetic_completion_replays_exactly(tmp_path):
    paths, args = fixture(tmp_path)
    peer = LostResponsePeer(); args['transport'] = peer
    for index, path in enumerate(paths):
        result = span.run(**args, delivery=path, start=index==0)
        assert not peer.eos and not result['capture_finalized']
    finalized = span.run(**args, finalize=True)
    assert finalized['capture_finalized'] and finalized['published_frames']==36
    assert finalized['span']['original_last_sequence']==31
    assert finalized['span']['original_epoch_frame_end']==1036
    assert finalized['span']['original_close_reason']=='INTERRUPTION'
    assert finalized['span']['eos_meaning']=='selected_span_only'
    assert not finalized['span']['recorder_stop_observed']
    assert not finalized['capture_completeness_proven'] and not finalized['analysis_completion_verified']
    assert peer.lost=={'put','seal'} and len(peer.puts)==9 and len(peer.pages)==1
    checkpoint = (args['job_dir']/'uploader/uploader.json').read_bytes()
    puts = list(peer.puts)
    assert span.run(**args, finalize=True)==finalized
    assert peer.puts==puts and (args['job_dir']/'uploader/uploader.json').read_bytes()==checkpoint
    with pytest.raises(capture.Refused,match='finalization_fenced'):
        span.run(**args, delivery=paths[-1])


def test_45_minute_geometry_is_metadata_only_simulation(tmp_path):
    # Only these tiny four-frame files are real. The following geometry is
    # explicitly simulated; no 45-minute audio ingestion/runtime is claimed.
    paths, _ = fixture(tmp_path)
    records = []
    frame_count = 300*u.RATE; original_origin = 900*u.RATE
    for index, path in enumerate(paths):
        snap = tmp_path/('snapshot-'+str(index)); create_snapshot(path, snap)
        record = copy.deepcopy(_record(snap,index,lambda:30))
        for container in (record['delivery'], record['metadata']):
            container['frame_start']=original_origin+index*frame_count
            container['frames']=frame_count
        record['metadata']['target_frames']=frame_count
        record['delivery']['wav_bytes']=44+2*frame_count
        span.POLICY.validate_edge(records[-1] if records else None,record)
        records.append(record)
    fields = span.POLICY.header_fields(records[0])
    header = dict(schema=span.SCHEMA, initial_intent=True, max_hours=12,max_inputs=192,**fields)
    span.POLICY.validate_header(header)
    receipts = [dict(delivery=record['delivery']) for record in records]
    for index, record in enumerate(records):
        assert span.POLICY.ordinal(record['delivery'],header)==index
        assert span.POLICY.input_start(receipts,index)==index*frame_count
        capture.check_capacity(dict(emitted_frames=index*frame_count,input_bytes=index*(44+2*frame_count),active_page=None),
                               record['delivery'],header)
    description = span.POLICY.result_fields(records,header)['span']
    assert len(records)==9 and description['local_end_frame']==45*60*u.RATE
    assert description['original_epoch_frame_start']==original_origin
    assert description['original_close_reason']=='INTERRUPTION'


def test_192_small_inputs_scaled_protocol_preserves_bounded_registry(tmp_path,monkeypatch):
    # Real tiny original/snapshot/journal files, but eight-frame synthetic pages
    # permit exercising the maximum input count quickly. NOT real page/runtime geometry.
    monkeypatch.setattr(u,'PAGE_FRAMES',8); monkeypatch.setattr(u,'SOURCE_FRAMES',4)
    paths,args = fixture(tmp_path,192)
    peer = Peer();args['transport']=peer
    for index,path in enumerate(paths):
        result = span.run(**args,delivery=path,start=index==0)
        assert result['inputs']==index+1 and result['max_inputs']==192
    assert not peer.eos and not result['capture_finalized']
    admissions = args['job_dir']/'admissions'
    assert len(list(admissions.iterdir()))==192
    assert all((p/'admission.json').stat().st_size<=single.MAX_MANIFEST for p in admissions.iterdir())
    before=(args['job_dir']/'capture.json').read_bytes()
    with pytest.raises(capture.Refused,match='frozen_capacity_changed'):
        span.run(**args,delivery=paths[-1],max_inputs=193)
    extra=tmp_path/'extra';segment(extra,215,epoch=7,start=1768,boundary='RESTART',run='selected-process',close='STOP')
    with pytest.raises(capture.Refused,match='next_sequence'):
        span.run(**args,delivery=extra)
    finalized=span.run(**args,finalize=True)
    assert finalized['inputs']==192 and finalized['published_frames']==768 and finalized['capture_finalized']
    assert len(peer.puts)==192 and len(peer.pages)==96
    assert before==(args['job_dir']/'capture.json').read_bytes()
    assert span.run(**args,finalize=True)==finalized


def test_policy_is_immutable_and_legacy_capture_stays_distinct():
    with pytest.raises(FrozenInstanceError):
        span.POLICY.schema=capture.SCHEMA
    assert capture.CAPTURE_POLICY.schema==capture.SCHEMA
    assert not capture.CAPTURE_POLICY.header_keys
    assert span.POLICY.schema!=capture.SCHEMA
