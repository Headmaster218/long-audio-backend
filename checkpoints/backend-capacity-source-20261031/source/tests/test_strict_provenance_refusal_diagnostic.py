"""Mandatory provenance failures retain bounded reason without writes or retry."""
from types import SimpleNamespace
import pytest
from audio_backend import strict_recording as strict
from audio_backend.recording_primary_provenance import REASONS


@pytest.mark.parametrize('reason',[r for r in REASONS if r not in ('retained','existing')])
def test_known_refusal_is_diagnostic_only(reason,monkeypatch):
    monkeypatch.setattr(strict,'require_prepared',lambda *a:SimpleNamespace(status='retained'))
    monkeypatch.setattr(strict,'active',lambda *a:None)
    class NoDatabase:
        def execute(self,*a):pytest.fail('refused provenance reached writes/reads')
    suffix=';budget_unavailable' if reason=='capacity' else ''
    with pytest.raises(ValueError,match='^strict_provenance_required:'+reason+suffix+'$'):
        strict.commit_outcome(NoDatabase(),{},0,1,{},reason,[])


@pytest.mark.parametrize('reason',[None,{},'x'*100000,'capacity\nsecret'])
def test_unknown_refusal_never_echoed(reason,monkeypatch):
    monkeypatch.setattr(strict,'require_prepared',lambda *a:SimpleNamespace(status='retained'))
    monkeypatch.setattr(strict,'active',lambda *a:None)
    with pytest.raises(ValueError,match='^strict_provenance_required:invalid_status$'):
        strict.commit_outcome(None,{},0,1,{},reason,[])


def test_capacity_reports_only_existing_finite_counters(monkeypatch):
    from audio_backend.native_owned_cell_store import Budget
    monkeypatch.setattr(strict,'require_prepared',lambda *a:SimpleNamespace(status='retained'))
    monkeypatch.setattr(strict,'active',lambda *a:None)
    budget=Budget();budget.steps=140000;budget.sql_bytes=200000;budget.elapsed=.46
    before=dict(vars(budget))
    result={'_native_bounded_support':{'budget':budget},'private':'never log me'}
    with pytest.raises(ValueError) as caught:
        strict.commit_outcome(None,{},0,1,result,'capacity',[])
    assert str(caught.value)=='strict_provenance_required:capacity;steps=140000;sql_bytes=200000;elapsed_ms=460.0;limit_ms=500.0'
    assert vars(budget)==before


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,2**54,'private',True])
def test_capacity_malformed_budget_is_sanitized(value,monkeypatch):
    from audio_backend.native_owned_cell_store import Budget
    monkeypatch.setattr(strict,'require_prepared',lambda *a:SimpleNamespace(status='retained'))
    monkeypatch.setattr(strict,'active',lambda *a:None)
    budget=Budget();budget.steps=value
    with pytest.raises(ValueError,match='^strict_provenance_required:capacity;budget_unavailable$'):
        strict.commit_outcome(None,{},0,1,{'_native_bounded_support':{'budget':budget}},'capacity',[])
