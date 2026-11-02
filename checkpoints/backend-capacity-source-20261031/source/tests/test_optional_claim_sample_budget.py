"""Independent model-free real-sampler caller-clip versus global-budget checks."""
import os
from types import SimpleNamespace
import pytest
from audio_backend import native_execution_permit as mod


def settings():
    return SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,
                           disk_reserve_bytes=0,native_owned_support_enabled=False)


@pytest.mark.parametrize('case',['expired','clipped','wall','rows','unmarked_clipped','observer_clipped'])
def test_real_sampler_classifies_only_authenticated_optional_caller_clip(monkeypatch,case):
    original=mod.service_sample;calls=[0]
    def clock():
        calls[0]+=1
        if case=='rows':return 0.
        if case=='expired':return 1.
        if case=='wall':return 0. if calls[0]==1 else .251
        return 0. if calls[0]<3 else .101
    caller=SimpleNamespace(pid=os.getpid(),create_time=lambda:1.)
    monkeypatch.setattr(mod.psutil,'Process',lambda pid:caller)
    count=mod.MAX_ROWS+1 if case=='rows' else 2
    monkeypatch.setattr(mod.psutil,'process_iter',lambda attrs:(
        SimpleNamespace(info={'pid':os.getpid()+i,'ppid':0}) for i in range(count)))
    def canonical(config,**kwargs):return original(config,clock=clock,**kwargs)
    monkeypatch.setattr(mod,'service_sample',canonical)
    permit=mod.NativeExecutionPermit(settings(),sampler=canonical)
    with pytest.raises(mod.AdmissionError) as caught:
        permit._take_sample(sample_deadline=.5 if case=='expired' else .1,
            optional_claim_deadline=case!='unmarked_clipped',observe=case=='observer_clipped')
    error=caught.value
    if case in ('expired','clipped'):
        assert error.reason=='optional_claim_check_deadline'
        assert error._optional_claim_deadline is mod._OPTIONAL_SAMPLE
        assert mod._authenticated_exhaustion(error) is None
        assert not permit.sampling_recovery_pending and permit._unavailable is None
    else:
        assert error.reason=='process_sample_budget'
        assert permit.sampling_recovery_pending
        assert mod._authenticated_exhaustion(error)[0]==('rows' if case=='rows' else 'wall')
    assert permit._sample_flight is None


def test_identity_failure_coincident_with_claim_deadline_remains_original(monkeypatch):
    now=[0.]
    def identity():now[0]=.101;raise mod.psutil.AccessDenied(os.getpid())
    monkeypatch.setattr(mod.psutil,'Process',lambda pid:SimpleNamespace(create_time=identity))
    with pytest.raises(mod.AdmissionError) as caught:
        mod.service_sample(settings(),clock=lambda:now[0],deadline=.1,optional_claim_deadline=True)
    assert caught.value.reason=='process_sample_unavailable'
    assert caught.value._sample_observation['cause_class']=='AccessDenied'
    assert getattr(caught.value,'_optional_claim_deadline',None) is None


@pytest.mark.parametrize('marker',['exact','foreign','missing','subclass'])
def test_custom_sampler_or_observer_cannot_forge_clip_exemption(monkeypatch,marker):
    class Subclass(mod.AdmissionError):pass
    error=Subclass('optional_claim_check_deadline') if marker=='subclass' else mod.AdmissionError('optional_claim_check_deadline')
    if marker!='missing':error._optional_claim_deadline=mod._OPTIONAL_SAMPLE if marker!='foreign' else object()
    def custom(_):raise error
    permit=mod.NativeExecutionPermit(settings(),sampler=custom)
    with pytest.raises(mod.AdmissionError):
        permit._take_sample(observe=True,sample_deadline=.1,optional_claim_deadline=True)
    assert permit._unavailable=='primary_ownership_uncertain'
    assert permit._primary_observation_failure is not None and permit._sample_flight is None
