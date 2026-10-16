"""Numeric-only parser diagnostics; no acoustic providers or original databases."""
import json

import pytest

from audio_backend import moss_handy_adapter as adapter
from audio_backend import moss_result_store as history
from audio_backend.moss_handy_profile import NAME
from audio_backend.native_lexical_alternatives import SourceSupport
from audio_backend.storage import Store
import test_moss_result_store as history_fixture
from test_moss_result_store import archive_fixture


def support(origin=0):
    return SourceSupport('1'*64, '2'*64, '3'*64, '4'*64, origin+320000,
        origin, origin+320000, origin, origin+320000, '5'*64, '6'*64)


def project(raw, **kwargs):
    if type(raw) is str:
        raw = raw.encode()
    return adapter.project(raw, support=kwargs.pop('support', support()),
                           profile_name=NAME, **kwargs)


def without_diagnostic(result):
    return {key: value for key, value in result.items() if key != 'rejected_timestamps'}


def prior_fields(raw, monkeypatch, **kwargs):
    # The new attachment is strictly after the unchanged old-size decision.
    with monkeypatch.context() as patch:
        patch.setattr(adapter, '_attach_rejected_timestamps', lambda result, *args: result)
        return project(raw, **kwargs)


@pytest.mark.parametrize('start,end,reason', [
    ('2', '1', 'end_not_after_start'), ('1', '1', 'end_not_after_start'),
    ('0', '20.000000000000004', 'end_after_support'),
    ('30', '25', 'end_not_after_start'), ('30', '31', 'end_after_support'),
    ('9'*400, '0', 'nonfinite'), ('0', '9'*400, 'nonfinite')])
def test_exact_rejection_reason_priority_and_untimed_parity(start, end, reason, monkeypatch):
    raw = f'[{start}][S1]private words[{end}]'
    result = project(raw)
    diagnostic = result['rejected_timestamps']
    assert diagnostic['total'] == 1 and diagnostic['omitted'] == 0
    assert diagnostic['support_duration_seconds'] == 20
    assert diagnostic['pairs'][0]['reason'] == reason
    assert diagnostic['reason_counts'][reason] == 1
    assert sum(diagnostic['reason_counts'].values()) == 1
    assert result['schema_errors'] == ['invalid_model_timestamps']
    assert result['diagnostic_phrases'] == []
    assert result['alternative']['text'] == 'private words'
    assert adapter.encoded(without_diagnostic(result)) == adapter.encoded(prior_fields(raw, monkeypatch))
    assert len(adapter.encoded(result)) <= adapter.MAX_RESULT_BYTES


@pytest.mark.parametrize('start,end,category,side', [
    (float('nan'), 1., 'nan', 'start'),
    (float('inf'), 1., 'positive_infinity', 'start'),
    (float('-inf'), 1., 'negative_infinity', 'start'),
    (0., float('nan'), 'nan', 'end'),
    (0., float('inf'), 'positive_infinity', 'end'),
    (0., float('-inf'), 'negative_infinity', 'end')])
def test_nonfinite_synthetic_scalar_serialization(start, end, category, side):
    # NaN/negative infinity are not accepted tokens in the actual regex.
    reason, pair = adapter._rejected_timestamp(2, start, end)
    assert reason == 'nonfinite' and pair[side] is None
    assert pair[side+'_nonfinite'] == category
    encoded = adapter.encoded(pair)
    assert json.loads(encoded) == pair
    assert b'NaN' not in encoded and b'Infinity' not in encoded


def test_negative_synthetic_scalar_priority():
    reason, pair = adapter._rejected_timestamp(1, -2., -3.)
    assert reason == 'negative_start' and pair['start'] == -2.


@pytest.mark.parametrize('token', ['-1', 'NaN', 'Infinity', '-Infinity', '+1', '1e309'])
def test_unmatched_tokens_do_not_manufacture_numeric_evidence(token, monkeypatch):
    raw = f'[{token}][S1]secret[1]'
    result = project(raw)
    assert 'rejected_timestamps' not in result
    assert result['schema_errors'] == ['unparsed_text', 'no_phrases']
    assert adapter.encoded(result) == adapter.encoded(prior_fields(raw, monkeypatch))


def test_eight_pair_limit_and_original_matched_ordinals():
    # The ninth speaker is skipped before timestamp validation, but still has
    # its matched ordinal. Later invalid S0 pairs must start at ordinal 10.
    raw = ''.join(f'[0][S{i}]speaker-private[1]' for i in range(8))
    raw += '[20][S8]skipped-private[0]'
    raw += ''.join('[2][S0]rejected-private[1]' for _ in range(12))
    result = project(raw)
    diagnostic = result['rejected_timestamps']
    assert diagnostic['total'] == 12 and diagnostic['omitted'] == 4
    assert [pair['ordinal'] for pair in diagnostic['pairs']] == list(range(10, 18))
    assert diagnostic['reason_counts']['end_not_after_start'] == 12
    assert len(result['diagnostic_phrases']) == 8
    encoded = adapter.encoded(diagnostic)
    for private in (b'private', b'S0', b'S8', b'raw_text', b'speaker', b'path'):
        assert private not in encoded
    assert set(diagnostic) == {'schema', 'support_duration_seconds', 'total',
                              'omitted', 'reason_counts', 'pairs'}
    assert all(set(pair) == {'ordinal', 'start', 'end', 'start_nonfinite',
                            'end_nonfinite', 'reason'} for pair in diagnostic['pairs'])


def test_total_at_phrase_capacity_is_exact_and_result_is_bounded():
    result = project('[2][S1]x[1]' * adapter.MAX_PHRASES)
    diagnostic = result['rejected_timestamps']
    assert diagnostic['total'] == 256 and diagnostic['omitted'] == 248
    assert len(diagnostic['pairs']) == 8
    assert diagnostic['reason_counts']['end_not_after_start'] == 256
    assert len(adapter.encoded(result)) <= adapter.MAX_RESULT_BYTES


@pytest.mark.parametrize('origin', [0, 160000, 2**53 - 1 - 320000])
def test_support_relative_coordinates_never_gain_origin_or_clamping(origin, monkeypatch):
    raw = '[0][S1]accepted[20][0][S1]rejected[20.000000000000004]'
    source = support(origin)
    result = project(raw, support=source)
    assert result['diagnostic_phrases'][0]['end'] == 20
    pair = result['rejected_timestamps']['pairs'][0]
    assert pair['start'] == 0 and pair['end'] == 20.000000000000004
    assert result['rejected_timestamps']['support_duration_seconds'] == 20
    assert adapter.encoded(without_diagnostic(result)) == adapter.encoded(
        prior_fields(raw, monkeypatch, support=source))


def test_cross_phrase_nonmonotonicity_is_unchanged(monkeypatch):
    raw = '[4][S1]a[8][1][S2]b[5][8][S1]c[9]'
    result = project(raw)
    assert [p['start'] for p in result['diagnostic_phrases']] == [4, 1, 8]
    assert result['schema_errors'] == [] and 'rejected_timestamps' not in result
    assert adapter.encoded(result) == adapter.encoded(prior_fields(raw, monkeypatch))


@pytest.mark.parametrize('raw', [b'\xff', b'[2][S1]x[1]'*257, b'', b'not parsed', b'[0][S1]x[1]'])
def test_early_and_zero_rejection_results_stay_byte_identical(raw, monkeypatch):
    result = project(raw)
    assert 'rejected_timestamps' not in result
    assert adapter.encoded(result) == adapter.encoded(prior_fields(raw, monkeypatch))


@pytest.mark.parametrize('text', ['a'*65537, '\x00'*50000, '中'*30000])
def test_existing_projection_oversize_decisions_remain_exact(text, monkeypatch):
    raw = '[2][S1]'+text+'[1]'
    old = prior_fields(raw, monkeypatch)
    result = project(raw)
    assert adapter.encoded(without_diagnostic(result)) == adapter.encoded(old)
    assert result['alternative'] is None
    assert result['rejected_timestamps']['total'] == 1
    assert len(adapter.encoded(result)) <= adapter.MAX_RESULT_BYTES


def test_existing_result_oversize_path_is_not_changed(monkeypatch):
    raw = '[2][S1]'+('secret '*400)+'[1]'
    monkeypatch.setattr(adapter, 'MAX_RESULT_BYTES', 1200)
    old = prior_fields(raw, monkeypatch)
    result = project(raw)
    assert old['schema_errors'] == ['result_projection_oversize']
    assert adapter.encoded(without_diagnostic(result)) == adapter.encoded(old)
    assert len(adapter.encoded(result)) <= adapter.MAX_RESULT_BYTES


def test_header_omission_does_not_remove_old_content(monkeypatch):
    raw = '[2][S1]kept untimed content[1]'
    old = prior_fields(raw, monkeypatch)
    monkeypatch.setattr(adapter, 'MAX_RESULT_BYTES', len(adapter.encoded(old)))
    result = project(raw)
    assert 'rejected_timestamps' not in result
    assert adapter.encoded(result) == adapter.encoded(old)
    assert len(adapter.encoded(result)) == adapter.MAX_RESULT_BYTES


def test_only_new_pairs_trim_to_the_available_capacity(monkeypatch):
    raw = '[2][S1]x[1]'*12
    full = project(raw)
    diagnostic = dict(full['rejected_timestamps'], pairs=[], omitted=12)
    header_only = {**without_diagnostic(full), 'rejected_timestamps': diagnostic}
    monkeypatch.setattr(adapter, 'MAX_RESULT_BYTES', len(adapter.encoded(header_only)))
    result = project(raw)
    assert result == header_only
    assert result['rejected_timestamps']['reason_counts']['end_not_after_start'] == 12
    assert len(adapter.encoded(result)) == adapter.MAX_RESULT_BYTES


def test_finite_numbers_and_no_raw_overflow_token():
    token = '9'*400
    diagnostic = project(f'[{token}][S1]private text[1]')['rejected_timestamps']
    pair = diagnostic['pairs'][0]
    assert pair['start'] is None and pair['start_nonfinite'] == 'positive_infinity'
    assert pair['end'] == 1 and pair['end_nonfinite'] is None
    encoded = adapter.encoded(diagnostic)
    assert token.encode() not in encoded and b'private text' not in encoded
    json.loads(encoded, parse_constant=lambda _: pytest.fail('Nonfinite JSON token'))


@pytest.mark.parametrize('include_diagnostic', [False, True])
def test_existing_history_reopens_exact_new_and_old_result_bytes(archive_fixture, include_diagnostic):
    store, archive, sid = archive_fixture
    result = project('[2][S1]synthetic[1]')
    if not include_diagnostic:
        result = without_diagnostic(result)
    value = history_fixture.snapshot(sid, result=adapter.encoded(result))
    history_fixture.reserve(archive, value)
    archive.commit(value)
    reopened = history.MossResultStore(Store(store.path, store.settings))
    saved = reopened.result(sid, value.identity.job_id)
    assert saved == value.result_json
    assert ('rejected_timestamps' in json.loads(saved)) == include_diagnostic
