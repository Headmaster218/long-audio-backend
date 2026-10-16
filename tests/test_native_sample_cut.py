"""Model-free sample-cut regression; synthetic speech is not the lost live trace."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from audio_backend import native_worker
from audio_backend.native_owned_cells import RATE, Unavailable, exact_frame
from audio_backend.native_owned_cells import Turn
from audio_backend.native_owned_cell_capture import _sample_gap_cut
from test_same_epoch_core_seams import factory
from test_multicore_seam_coverage import SyntheticNative


def install_gap_native(h, monkeypatch, gaps=((596304,604976),)):
    """Canonical raw endpoints, normal state/frontier/PCM/observer path."""
    class Native(SyntheticNative):
        def request(self, action, stop, **args):
            if action != 'turns':return super().request(action,stop,**args)
            assert not stop(); self.calls.append(action)
            qa=self.replay+round(args['start']*RATE)
            qb=self.replay+round(args['end']*RATE)
            intervals=[];cursor=qa
            for a,b in gaps:
                if qa<a<qb:intervals.append((cursor,a))
                if b>qa:cursor=max(cursor,b)
            if cursor<qb:intervals.append((cursor,qb))
            response={'turns':[{'start':(a-self.replay)/RATE,'end':(b-self.replay)/RATE,
                'speaker_local_id':self.epoch+':speaker_1'} for a,b in intervals if a<b]}
            observer=getattr(self,'response_observer',None)
            if observer:observer(response)
            return response
    monkeypatch.setattr(native_worker,'NativeProcess',lambda settings:Native(settings,h))


def require_capture(h, monkeypatch):
    original=h.worker._cell_call
    def call(method,*args):
        if method == 'native_core_cut':return h.capture.native_core_cut(*args)
        if method in ('prepare','prepare_core_seam'):
            return getattr(h.capture,method)(*args,required=True)
        return original(method,*args)
    monkeypatch.setattr(h.worker,'_cell_call',call)


@pytest.mark.parametrize('odd', [False, True])
def test_worker_sample_cut_propagates_to_next_offset_compacts_and_mapping(factory,monkeypatch,odd):
    h=factory(());install_gap_native(h,monkeypatch,((596304,604976+int(odd)),));require_capture(h,monkeypatch)
    ids=[h.add(10.5,final=False) for _ in range(6)]
    for _ in range(3):h.worker.process(h.store.claim())
    seen=[];original=h.capture.prepare
    def prepare(chunk,native,offset,end,result,**kw):
        seen.append({'origin':chunk['timeline_start'],'offset':offset,'end':end,'end_hex':end.hex(),
            'end_frames':end*RATE,'native':deepcopy(native),'core_cut':result['metadata']['core_cut']})
        return original(chunk,native,offset,end,result,**kw)
    monkeypatch.setattr(h.capture,'prepare',prepare)
    h.worker.process(h.store.claim())
    row=seen[0]
    assert row['origin']==31.5 and row['end']==96640/RATE
    assert seen[1]['offset']==row['end'] and seen[1]['end']==139840/RATE
    assert h.store.chunk(ids[3])['next_offset']==139840/RATE
    docs=[json.loads(r['body'])['part'] for r in h.rows() if r['cid']==ids[3]]
    assert [(p['logical_start'],p['logical_end']) for p in docs]==[(504000,600640),(600640,643840)]
    assert [(p['source_start'],p['source_end']) for p in docs]==[(0,96640),(96640,139840)]
    directory=Path(os.environ['RESET_REPLAY_REPORT_DIR']);directory.mkdir(exist_ok=True)
    (directory/f'corrected-worker-{odd}.json').write_text(json.dumps(seen,indent=2))


@pytest.mark.parametrize('base', [0,31*RATE+8000,300*RATE,48*3600*RATE])
@pytest.mark.parametrize('odd', [0,1])
def test_sample_gap_cut_exact_boundaries_nonzero_long_reset_and_odd_midpoint(base,odd):
    a=base; b=base+12*RATE; start=base+2*RATE;target=base+10*RATE
    turns=(Turn(a,base+8*RATE,'v'),Turn(base+9*RATE+odd,b,'v'))
    cut=_sample_gap_cut(turns,start,target,a,b,30*RATE)
    assert cut==base+round(8.5*RATE)
    assert start<cut<=target and turns[0].end<cut<turns[1].start
    assert (turns[0].end+turns[1].start)-2*cut==odd


@pytest.mark.parametrize('turns', [(),(Turn(0,12*RATE,'v'),)])
def test_empty_or_no_gap_keeps_exact_nominal_frame(turns):
    assert _sample_gap_cut(turns,2*RATE,139840+2*RATE,0,14*RATE,30*RATE)==139840+2*RATE


def test_quarter_sample_lower_guard_moves_only_inward():
    # A user window of160001frames has a40000.25frame guard. The selected
    # internal lower edge uses the first allowed sample; source edges stay exact.
    turns=(Turn(0,100000,'v'),Turn(130000,200000,'v'))
    assert _sample_gap_cut(turns,110000,160000,0,200000,160001)==119999+1


@pytest.mark.parametrize('value',[6.00003125,6.0000001,float('nan'),float('inf'),True])
def test_external_fractional_and_nonfinite_boundaries_remain_strict(value):
    with pytest.raises(Unavailable):exact_frame(value)


def test_invalid_witness_and_changed_analyzer_turns_cannot_select(factory,monkeypatch,capsys):
    h=factory(());install_gap_native(h,monkeypatch);require_capture(h,monkeypatch)
    ids=[h.add(10.5,final=False) for _ in range(6)]
    for _ in range(3):h.worker.process(h.store.claim())
    original=h.capture.native_core_cut
    def invalid(chunk,native,offset,nominal,result,*rest):
        result['metadata']['diarization_turns']=[]
        return original(chunk,native,offset,nominal,result,*rest)
    monkeypatch.setattr(h.capture,'native_core_cut',invalid)
    with pytest.raises(Unavailable,match='native_cut_transform_mismatch'):
        h.worker.process(h.store.claim())
    assert h.store.chunk(ids[3])['next_offset']==0.


@pytest.mark.parametrize('fault',['fractional_native_edge','invalid_witness'])
def test_bad_native_evidence_refuses_before_primary_progress(factory,monkeypatch,fault):
    h=factory(());install_gap_native(h,monkeypatch,((596304,604976.5 if fault=='fractional_native_edge' else 604976),))
    require_capture(h,monkeypatch)
    ids=[h.add(10.5,final=False) for _ in range(6)]
    for _ in range(3):h.worker.process(h.store.claim())
    if fault=='invalid_witness':
        original=h.capture.native_core_cut
        def failed(*args):
            h.capture.witness.fail('synthetic_invalid_witness')
            return original(*args)
        monkeypatch.setattr(h.capture,'native_core_cut',failed)
    with pytest.raises(Unavailable):h.worker.process(h.store.claim())
    assert h.store.chunk(ids[3])['next_offset']==0.
    assert h.rows()[-1]['logical_end']==504000


@pytest.mark.parametrize('odd',[False,True])
def test_new_cut_owns_boundary_lexical_units_and_event_samples_once(factory,monkeypatch,odd):
    import test_same_epoch_core_seams as harness_module
    from audio_backend.event_candidate_store import _sample_boundary
    h=factory(());install_gap_native(h,monkeypatch,((596304,604976+int(odd)),));require_capture(h,monkeypatch)
    cut=600640
    h.markers=[(cut+d,1,name) for d,name in [(-1,'before'),(0,'at'),(1,'after')]]
    original=harness_module.analyzer_result
    def result(native,a,b,markers):
        value=original(native,a,b,markers)
        if a<=cut-1<cut+1<=b:
            value['metadata']['event_candidates']=[{'start':(cut-1-a)/RATE,'end':(cut+1-a)/RATE,
                'top_labels':[{'label':'Door','score':.25}],
                'category_scores':{'door':{'source_label':'Door','score':.25}},'model_inference_executed':True}]
        return value
    monkeypatch.setattr(harness_module,'analyzer_result',result)
    [h.add(10.5,final=False) for _ in range(6)]
    for _ in range(4):h.worker.process(h.store.claim())
    with h.store.db() as db:
        texts=[r[0] for r in db.execute('SELECT text FROM segments')]
    assert sorted(texts)==['after','at','before']
    events=h.store.event_candidates(h.sid)['event_candidates']
    assert [(_sample_boundary(x['start']),_sample_boundary(x['end'])) for x in events]==[(cut-1,cut),(cut,cut+1)]


from test_moss_automatic_integration import automatic_app_factory,process_primary,read_database
from test_restart_replay_core_seams import install_frontier_provider,append_page


@pytest.mark.parametrize('invalid',[False,True])
def test_actual_automatic_worker_uses_required_sample_cut(automatic_app_factory,monkeypatch,invalid):
    with automatic_app_factory() as h:
        install_frontier_provider(h,monkeypatch)
        factory=h.worker._native_process_factory
        def native_factory(settings):
            native=factory(settings);original=native.request
            def request(action,stop,**kw):
                if action!='turns':return original(action,stop,**kw)
                native.calls.append(action);a=kw['start'];b=kw['end']
                turns=[]
                for lo,hi in ((a,min(b,37.269)),(max(a,37.811),b)):
                    if lo<hi:turns.append({'start':lo,'end':hi,'speaker_local_id':native.epoch+':speaker_1'})
                answer={'turns':turns};h.worker._native_response(answer);return answer
            native.request=request;return native
        monkeypatch.setattr(h.worker,'_native_process_factory',native_factory)
        append_page(h,1,0,round(31.5*RATE))
        process_primary(h);process_primary(h)
        append_page(h,2,round(31.5*RATE),63*RATE)
        process_primary(h);process_primary(h)
        if invalid:
            def denied(*args):raise Unavailable('synthetic_required_cut_refusal')
            monkeypatch.setattr(h.capture,'native_core_cut',denied)
            with pytest.raises(Unavailable,match='synthetic_required_cut_refusal'):process_primary(h)
        else:process_primary(h)
        with read_database(h.store) as db:
            row=db.execute('SELECT * FROM chunks WHERE timeline_start=31.5').fetchone()
            parts=[json.loads(r[0])['part'] for r in db.execute('SELECT body FROM native_core_commits WHERE cid=? ORDER BY logical_end',(row['id'],))]
        assert row['next_offset']==(0. if invalid else 8.74)
        assert [(p['source_start'],p['source_end']) for p in parts]==([] if invalid else [(0,96640),(96640,139840)])
        assert not h.drivers


@pytest.mark.parametrize('legacy',[1e308,10**1000,float('nan'),float('inf'),True])
def test_refusal_diagnostic_never_replaces_original_error(factory,legacy):
    h=factory(())
    with pytest.raises(Unavailable,match='^native_cut_witness_unavailable$'):
        h.capture.native_core_cut({},None,0.,1.,{},30.,legacy)


@pytest.mark.parametrize('scope_start',[0.,.123456789,-17.123456789])
@pytest.mark.parametrize('epoch_index',[0,1,576])
def test_witnessed_capture_preserves_negative_context_non_grid_origins_and_long_epochs(scope_start,epoch_index):
    from audio_backend.native_owned_cell_capture import Capture
    from audio_backend.native_owned_cell_store import Budget
    from test_native_owned_cells import observed_window
    replay=max(0,epoch_index*300*RATE-339200)
    origin_frame=epoch_index*300*RATE+504000
    a=origin_frame-2*RATE;b=origin_frame+12*RATE
    witness,native,pcm,_=observed_window(b-replay,scope_start=scope_start,epoch_index=epoch_index)
    raw=[{'start':(a-replay)/RATE,'end':(origin_frame+92304-replay)/RATE,'speaker_local_id':witness.epoch+':speaker_1'},
         {'start':(origin_frame+100976-replay)/RATE,'end':(b-replay)/RATE,'speaker_local_id':witness.epoch+':speaker_1'}]
    witness.observe({'turns':raw})
    native.update(end=8.74,extract_start=-2.,extract_end=12.,turns=[dict(t,
        start=max(a/RATE,replay/RATE+t['start'])-a/RATE,
        end=min(b/RATE,replay/RATE+t['end'])-a/RATE) for t in raw])
    cap=object.__new__(Capture);cap.scope=('stream','s','v');cap.scope_start=scope_start
    cap.origin_frame=origin_frame;cap.witness=witness;cap.budget=Budget();cap.analyzer_start=a
    cap.analyzer=pcm[(a-replay)*2:(b-replay)*2]
    chunk={'timeline_start':scope_start+origin_frame/RATE}
    result={'metadata':{'diarization_turns':deepcopy(native['turns'])}}
    assert cap.native_core_cut(chunk,native,0.,8.74,result,30.,6.039999999999999)==96640/RATE
    assert list(witness.raw_turns)==raw
    # Configuration is not an external sample boundary: fractional windows
    # remain accepted; the lower guard selects the first allowed sample inward.
    assert cap.native_core_cut(chunk,native,0.,8.74,result,10.50001,6.115)==97840/RATE
