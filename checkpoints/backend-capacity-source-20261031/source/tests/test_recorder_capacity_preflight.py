import json
import os
import pytest
from audio_backend import recorder_capacity_preflight as cap
from audio_backend import recorder_ftps_receiver as receiver
from audio_backend import recorder_receiver_publication as publication
from test_recorder_ftps_receiver import configured
from test_recorder_analysis_plan import setup
from test_recorder_receiver_completion_review import adapter


def test_controlled_profile_and_default_mismatch():
    result=cap.profile()
    assert result['conditional_profile_fits'] and result['per_generation_seconds']==43200
    assert not result['runtime_completion_guaranteed']
    assert result['clean_profile_peak_raw_reservation_bytes']<8*1024**3
    bad=cap.profile(max_attempts=256,phase_max_actions=64,phase_max_polls=64)
    assert 'strict_analysis_input_headroom_required' in bad['issues']
    assert 'phase_polls_cannot_drain_full_backlog' in bad['issues']

@pytest.mark.parametrize('kw,issue',[
    ({'segment_seconds':1},'cadence_exceeds_analysis_duration_count_or_source_slots'),
    ({'channels':2},'unsupported_format_unqualified'),
    ({'phase_max_actions':2},'phase_actions_cannot_finalize_status_export'),
    ({'max_attempts':192},'strict_analysis_input_headroom_required'),
    ({'max_raw_bytes':1382400000},'raw_full_attempt_reservation_shortfall'),
    ({'max_attempts':65,'phase_max_polls':64},'phase_polls_cannot_drain_full_backlog'),
    ({'generations':8,'phase_max_actions':64},'aggregate_action_reservation_shortfall'),
    ({'max_total_polls':1},'aggregate_poll_reservation_shortfall'),
    ({'max_requests':1},'initializer_request_reserve_shortfall'),
    ({'max_network_writes':1},'initializer_write_reserve_shortfall'),
    ({'max_network_bytes':1},'initializer_response_reserve_shortfall')])
def test_constraints(kw,issue):assert issue in cap.profile(**kw)['issues']


def test_all_seven_edges_budget():
    r=cap.profile(generations=8)
    assert r['conditional_profile_fits']
    assert r['worst_case_reserved_actions']==(7*193+192)*16
    assert r['worst_case_reserved_polls']==3072


def snapshot(root):
    return {str(p.relative_to(root)):(p.stat().st_mode,p.read_bytes()) for p in root.rglob('*') if p.is_file()}


def test_stopped_all_generations_no_writes(configured,tmp_path,monkeypatch):
    setup(configured,tmp_path,monkeypatch)
    route=tmp_path/'route-1.json'; data=json.loads(route.read_bytes()); data['max_attempts']=144
    route.write_text(json.dumps(data))
    before=snapshot(tmp_path)
    oldopen=os.open
    def readonly(path,flags,*a,**kw):
        assert not flags & (os.O_CREAT|os.O_WRONLY|os.O_RDWR|os.O_TRUNC)
        return oldopen(path,flags,*a,**kw)
    monkeypatch.setattr(os,'open',readonly)
    r=cap.inspect(receiver_config=route,runner_state=tmp_path/'runner-0')
    assert len(r['generation_inventory'])==3
    assert all(row['retained_attempts']==1 for row in r['generation_inventory'])
    assert snapshot(tmp_path)==before


def test_partial_empty_count_and_reduced_cap(configured,tmp_path,monkeypatch):
    setup(configured,tmp_path,monkeypatch)
    route=tmp_path/'route-1.json';data=json.loads(route.read_bytes())
    raw=publication.validate_binding(data['generations'][2]['binding'],data['generations'][2]['inbox_dir'])['staging_dir']
    from pathlib import Path
    # Valid attempt names taken from another generation, retained only in last namespace.
    from test_recorder_inbox import delivery
    other=delivery(tmp_path/'unused-source',99).name
    (Path(raw)/other).mkdir(mode=0o700)
    r=cap.inspect(receiver_config=route,runner_state=tmp_path/'runner-0')
    assert r['generation_inventory'][2]['unpublished_attempts']==1
    assert r['generation_inventory'][2]['conservative_unpublished_reserve_bytes']==cap.RESERVE
    data['max_attempts']=1;route.write_text(json.dumps(data));before=snapshot(tmp_path)
    with pytest.raises(publication.Refused,match='existing_inventory'):
        cap.inspect(receiver_config=route,runner_state=tmp_path/'runner-0')
    assert snapshot(tmp_path)==before


def test_live_receiver_refused(configured,tmp_path,monkeypatch):
    setup(configured,tmp_path,monkeypatch)
    with adapter(configured['state'],configured['ready']):
        with pytest.raises(publication.Refused,match='must_be_stopped'):
            cap.inspect(receiver_config=tmp_path/'route-1.json',runner_state=tmp_path/'runner-0')


def test_exact_action_reservation_boundary():
    exact=(7*193+192)*16
    assert cap.profile(generations=8,max_total_actions=exact)["conditional_profile_fits"]
    assert "aggregate_action_reservation_shortfall" in cap.profile(generations=8,max_total_actions=exact-1)["issues"]


def test_exact_backlog_needs_terminal_round():
    assert 'phase_polls_cannot_drain_full_backlog' in cap.profile(phase_max_polls=144)['issues']


def test_runner_cadence_mismatch_and_duplicate_refused(configured,tmp_path,monkeypatch):
    setup(configured,tmp_path,monkeypatch)
    route=tmp_path/'route-1.json'
    r=cap.inspect(receiver_config=route,runner_state=tmp_path/'runner-0')
    assert 'runner_target_frames_profile_mismatch' in r['issues']
    data=json.loads(route.read_bytes())
    from pathlib import Path
    cfg=publication.validate_binding(data['generations'][2]['binding'],data['generations'][2]['inbox_dir'])
    other=next(Path(configured['raw']).iterdir()).name
    (Path(cfg['staging_dir'])/other).mkdir(mode=0o700)
    with pytest.raises(publication.Refused,match='duplicate_retained'):
        cap.inspect(receiver_config=route,runner_state=tmp_path/'runner-0')


def test_detached_raw_descriptor_refused(configured,tmp_path,monkeypatch):
    setup(configured,tmp_path,monkeypatch)
    opened=os.open;swapped=False
    def swap(path,flags,*args,**kwargs):
        nonlocal swapped
        fd=opened(path,flags,*args,**kwargs)
        if str(path)==str(configured['raw']) and not swapped:
            swapped=True
            configured['raw'].rename(tmp_path/'detached-raw')
            configured['raw'].mkdir(mode=0o700)
        return fd
    monkeypatch.setattr(os,'open',swap)
    with pytest.raises(publication.Refused):
        cap.inspect(receiver_config=tmp_path/'route-1.json',runner_state=tmp_path/'runner-0')
