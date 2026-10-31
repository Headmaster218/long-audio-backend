"""Model-free contracts for the frozen, non-production residual experiment."""
import json
from pathlib import Path
import tarfile
import io

import numpy as np
import pytest

from scripts.benchmarks import events_residual_development_v2 as m


def plan():
    if not m.PROTOCOL.exists():
        pytest.skip('Private frozen development protocol is not distributed in source-only checkout')
    return json.loads(m.PROTOCOL.read_text())


def test_protocol_is_exact_and_development_only():
    plan()
    assert m.digest(m.PROTOCOL) == m.PROTOCOL_SHA256
    p = plan()
    rows = p['inputs']['rows']
    assert len(rows) == len({r['id'] for r in rows}) == 138
    assert sum(r['kind'] == 'mixture' for r in rows) == 78
    assert {r['experiment_split'] for r in rows} == {'development'}


def test_historical_decisions_reproduce_without_model():
    rows = plan()['inputs']['rows']
    if not all((m.ROOT/r['baseline_cache']).exists() for r in rows):
        pytest.skip('Private historical score caches are not distributed')
    assert len(m.historical_parity(rows)) == 138


def test_every_input_hash_without_model():
    rows = plan()['inputs']['rows']
    if not all((m.ROOT/r['path']).exists() for r in rows):
        pytest.skip('Licensed development audio is not distributed')
    assert sum(len(m.samples_for(r)) for r in rows) == 1265 * 16000


def test_identity_quantization_truncates_toward_zero():
    a = np.array([-.25, -.00002, 0, .00002, .25], dtype=np.float32)
    result = m.quantize(a)
    assert np.array_equal(result, np.trunc(a * 32767).astype(np.int16).astype(np.float32)/32767)
    assert result[1] == result[3] == 0


@pytest.mark.parametrize('sample', [1., -1., 1.1])
def test_identity_saturation_rejected(sample):
    with pytest.raises(RuntimeError, match='saturate'):
        m.quantize(np.array([sample], dtype=np.float32))


def test_support_excludes_tail_and_reconstructs():
    a = np.linspace(-.2, .2, 48001, dtype=np.float32)
    raw = np.zeros(len(a), dtype=np.int16)
    branches, info = m.support(a, raw, dict(hop_size=480, delay_samples=1440))
    assert info['valid_samples'] == 46560
    assert info['excluded_tail_seconds'] == 1441/48000
    assert np.array_equal(branches['R'], a[:46560])
    assert set(branches) == {'R', 'M', 'Q0', 'unquantized'}
    assert info['reconstruction_absolute_error'] == 0


@pytest.mark.parametrize('raw', [np.zeros(10,dtype=np.int16), np.full(48000,32767,dtype=np.int16), np.full(48000,-32768,dtype=np.int16)])
def test_invalid_raw_contract_rejected(raw):
    with pytest.raises(RuntimeError, match='Malformed'):
        m.support(np.zeros(48000,dtype=np.float32),raw,dict(hop_size=480,delay_samples=1440))


def test_exact_zero_support_is_zero():
    branches, _ = m.support(np.zeros(48000,dtype=np.float32),np.zeros(48000,dtype=np.int16),dict(hop_size=480,delay_samples=1440))
    assert all(np.all(a == 0) for a in branches.values())


def test_alignment_diagnostic_cannot_select_a_new_shift():
    rng = np.random.default_rng(42)
    a = (rng.standard_normal(48000)*.05).astype(np.float32)
    for shift, passed in [(0,True),(4,False)]:
        d=1440
        raw=np.zeros(len(a),dtype=np.int16)
        raw[d+shift:]=np.trunc(a[:len(a)-d-shift]*32767).astype(np.int16)
        result=m.alignment_check(a,raw,dict(delay_samples=d),[[.1,.5]])[0]
        assert result['passed'] is passed
        assert result['applied_extra_shift'] == 0
        assert result['diagnostic_peak_offsets'] == [shift]


def test_silent_correlation_fails_closed():
    with pytest.raises(RuntimeError,match='Unidentifiable'):
        m.correlation(np.zeros(100),np.zeros(100))


def test_complete_owner_row_only():
    assert m.last_complete_metrics(b'{"rss_bytes":100}\n{"rss_bytes":999') == {'rss_bytes':100}
    with pytest.raises(RuntimeError):m.last_complete_metrics(b'{"rss_bytes":999')


def test_resource_bounds_are_strict_and_owner_cap_is_stricter():
    good = dict(own=100,owner=2*m.GIB,available=2*m.GIB,free=5*m.GIB,
                trial_bytes=100,report_bytes=100,age=1,wall=1)
    assert m.check_limits(**good) == []
    for key,value in [('own',m.GIB+1),('owner',5*m.GIB),('available',2*m.GIB-1),('free',5*m.GIB-1),
                      ('trial_bytes',512*1024**2+1),('report_bytes',64*1024**2+1),('age',10.01),('age',-1),('wall',1500.1)]:
        assert m.check_limits(**{**good,key:value})
    assert m.check_limits(**{**good,'own':m.GIB,'owner':5*m.GIB})


def cases_from_baseline(rows):
    return {r['id']:{a:{'labels':r['baseline_labels'] if a != 'Q0' else []}for a in ['R','C','M','Q0']}for r in rows}


def test_recovery_from_matched_control_never_counts_as_gain():
    rows=plan()['inputs']['rows']
    cases=cases_from_baseline(rows)
    for r in rows:
        if r['kind']=='mixture'and r['source_type']in ['train','subway']:
            label='subway'if r['source_type']=='subway'else'driving'
            for arm in ['R','C','M']:cases[r['id']][arm]['labels']=[label]
    result=m.evaluate(rows,cases)
    for arm in ['R','C']:
        assert not result[arm]['passed']
        assert all(not item['new_recovery_ids']for item in result[arm]['targets'].values())


def test_q0_anywhere_fails_and_union_does_not_promote_silence():
    rows=plan()['inputs']['rows']
    cases=cases_from_baseline(rows)
    row=next(r for r in rows if r['kind']=='speech_control')
    cases[row['id']]['R']['labels']=['silence']
    cases[row['id']]['Q0']['labels']=['driving']
    result=m.evaluate(rows,cases)
    for arm in ['R','C']:
        assert not result[arm]['passed']
        assert any(e['gate']=='Q0_target_nomination'for e in result[arm]['failed_gates'])
        decision=next(x for x in result[arm]['annotations']if x['id']==row['id'])
        assert decision['policy_labels']==row['baseline_labels']


def test_clean_view_loss_is_not_hidden_by_primary_union():
    rows=plan()['inputs']['rows'];cases=cases_from_baseline(rows)
    row=next(r for r in rows if r['kind']=='source'and set(r['labels'])&set(r['baseline_labels'])&m.TARGETS)
    cases[row['id']]['R']['labels']=[]
    result=m.evaluate(rows,cases)['R']
    assert any(e['gate']=='clean_transport_view_loss'and e['id']==row['id']for e in result['failed_gates'])
    decision=next(x for x in result['annotations']if x['id']==row['id'])
    assert decision['policy_labels']==sorted(row['baseline_labels'])


def test_evidence_is_not_overwritten(tmp_path):
    p=tmp_path/'result.json';m.dump(p,{'first':1})
    with pytest.raises(RuntimeError):m.dump(p,{'replacement':2})
    assert json.loads(p.read_text())=={'first':1}


def test_archive_new_member_is_rejected_before_execution(tmp_path):
    archive=tmp_path/'model.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        info=tarfile.TarInfo('LICENSE');info.size=3;tar.addfile(info,io.BytesIO(b'new'))
    with pytest.raises(RuntimeError,match='Unexpected archive'):
        m.geometry(archive,tmp_path)


def test_privacy_audit_rejects_audio_and_credentials(tmp_path):
    m.dump(tmp_path/'good.json',{'category_scores':{'driving':.3},'waveform_sha256':'123'})
    assert m.privacy_audit(tmp_path)['passed']
    m.dump(tmp_path/'bad.json',{'speaker_embeddings':[1,2,3]})
    assert not m.privacy_audit(tmp_path)['passed']
    (tmp_path/'audio.wav').write_bytes(b'RIFF')
    assert any('unexpected_file_type'in x for x in m.privacy_audit(tmp_path)['findings'])


def test_oversized_archive_member_rejected_before_payload_read(tmp_path):
    archive=tmp_path/'oversized.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        info=tarfile.TarInfo('tmp/export/enc.onnx');info.size=16*1024**2+1
        tar.addfile(info)
    with pytest.raises(RuntimeError,match='member size cap'):
        m.geometry(archive,tmp_path)


def test_archive_input_cap_checked_before_tar_parse(tmp_path):
    archive=tmp_path/'oversized-input.tar.gz'
    with archive.open('wb') as f:f.truncate(16*1024**2+1)
    with pytest.raises(RuntimeError,match='input size cap'):
        m.geometry(archive,tmp_path)


def test_fixed_global_headroom_restores_float_units_without_clipping():
    a=np.full(48000,1.24,dtype=np.float32)
    raw=np.full(48000,20000,dtype=np.int16)
    branches,info=m.support(a,raw,dict(hop_size=480,delay_samples=1440))
    expected_q=2*np.trunc((.5*a[:46560])*32767).astype(np.int16).astype(np.float32)/32767
    assert np.array_equal(branches['M'],expected_q)
    assert np.max(branches['M'])>1
    assert info['restored_identity_over_unit_samples']==46560
    assert info['restored_enhanced_over_unit_samples']==46560
    assert info['internal_identity_peak']<1
    expected_enhanced=2*np.float32(20000)/np.float32(32767)
    assert np.allclose(branches['R'],a[:46560]-expected_enhanced,rtol=0,atol=2e-7)
    assert np.max(np.abs(branches['Q0']))<2/32767


def test_half_scale_still_rejects_real_integer_range_hazard():
    with pytest.raises(RuntimeError,match='saturate'):
        m.support(np.full(48000,2.,dtype=np.float32),np.zeros(48000,dtype=np.int16),dict(hop_size=480,delay_samples=1440))


def test_v1_source_and_protocol_remain_unchanged():
    assert m.digest(m.ROOT/'scripts/benchmarks/events_residual_development.py')=='a8c138f53ca41a14ce42ec274baf1f0c6716f1e7de23c78ddaa8c80b7cbb9989'
    old=m.ROOT/'docs/research/EVENT_RESIDUAL_DEVELOPMENT_PROTOCOL_v1.json'
    if old.exists():assert m.digest(old)=='f234afed2f7738ef79cc84befc7526bd885d7cc6ab28bd425137c9c7661559fa'
