"""Original permit, deterministic sampling windows, real bind/retire APIs; no child/model."""
import os
import time
from types import SimpleNamespace
import pytest
from audio_backend import native_execution_permit as mod
from test_recording_speaker_history_process import permit_fixture,Handle


@pytest.mark.parametrize('window',['before_sample','during_sample','after_sample'])
@pytest.mark.parametrize('action',['bind','retire'])
def test_generation_only_resamples_once(monkeypatch,window,action):
    permit,grant,event,proc,owner,state=permit_fixture(monkeypatch)
    primary=Handle(902)
    monkeypatch.setattr(mod.psutil,'Process',lambda pid:SimpleNamespace(create_time=lambda:2.,ppid=lambda:os.getpid(),
        is_running=lambda:(primary if pid==902 else proc).returncode is None))
    if action=='retire':
        state['rows'][902]=(2.,os.getpid(),16*1024**2);permit.bind_primary(primary)
        assert id(primary) in permit._handles
    fired=[];calls=[];original_take=permit._take_sample;original_sample=permit._sampler
    def change():
        fired.append(True)
        if action=='bind':
            state['rows'][902]=(2.,os.getpid(),16*1024**2);permit.bind_primary(primary)
        else:
            primary.returncode=0;state['rows'].pop(902);permit.finish_primary(primary)
            assert id(primary) not in permit._handles
    def sample(settings):
        result=original_sample(settings)
        if window=='during_sample' and not fired:change()
        return result
    def take(**kw):
        if kw.get('comparison_deadline'):
            calls.append(kw.get('sample_deadline'))
            if window=='before_sample' and not fired:change()
        result=original_take(**kw)
        if kw.get('comparison_deadline') and window=='after_sample' and not fired:change()
        return result
    monkeypatch.setattr(permit,'_sampler',sample);monkeypatch.setattr(permit,'_take_sample',take)
    deadline=time.monotonic()+3
    result=permit.observe_comparison_process(grant,proc,deadline=deadline)
    assert result['rss']==16*1024**2 and len(fired)==1 and len(calls)==2
    assert calls[0]==deadline and calls[1]<deadline and not event.is_set()
    assert permit._comparison['sampling'] is None


@pytest.mark.parametrize('change',['revision','cancelled','closed','unavailable','owner','repeat','no_deadline'])
def test_other_changes_and_persistent_churn_refuse(monkeypatch,change):
    permit,grant,event,proc,owner,state=permit_fixture(monkeypatch)
    original=permit._take_sample;calls=[]
    def take(**kw):
        value=original(**kw);calls.append(1)
        if len(calls)==1 or change=='repeat':
            permit._sample_generation=object()
            if change=='revision':permit._comparison['revision']=object()
            if change=='cancelled':event.set()
            if change=='closed':permit._closed=True
            if change=='unavailable':permit._unavailable='test'
            if change=='owner':permit._comparison['owner']=object()
        return value
    monkeypatch.setattr(permit,'_take_sample',take)
    with pytest.raises(mod.AdmissionError):permit.observe_comparison_process(grant,proc,
        deadline=None if change=='no_deadline' else time.monotonic()+3)
    assert len(calls)<=2 and event.is_set() and permit._comparison['sampling'] is None
