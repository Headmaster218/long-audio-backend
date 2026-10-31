"""Finite policy boundaries only; Settings construction opens no model or DB."""
import pytest

from audio_backend.settings import Settings


DEFAULTS = {
    'moss_result_max_rows': 65536,
    'moss_result_max_rows_per_session': 16384,
    'moss_result_max_bytes': 2 * 1024**3,
    'moss_result_max_bytes_per_session': 1024**3,
}
MAXIMA = {
    'moss_result_max_rows': 1048576,
    'moss_result_max_rows_per_session': 262144,
    'moss_result_max_bytes': 64 * 1024**3,
    'moss_result_max_bytes_per_session': 16 * 1024**3,
}


def config(tmp_path, **overrides):
    return Settings(data_dir=tmp_path, start_worker=False, **overrides)


def test_defaults_and_hard_maxima_allocate_nothing(tmp_path):
    settings = config(tmp_path)
    assert {key: getattr(settings, key) for key in DEFAULTS} == DEFAULTS
    settings = config(tmp_path, **MAXIMA)
    assert {key: getattr(settings, key) for key in MAXIMA} == MAXIMA
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('name', tuple(DEFAULTS))
@pytest.mark.parametrize('value', [False, True, 0, -1, 1.5, '100', None])
def test_nonpositive_and_noninteger_limits_are_rejected(tmp_path, name, value):
    with pytest.raises(ValueError):
        config(tmp_path, **{name: value})


@pytest.mark.parametrize('name', tuple(MAXIMA))
def test_each_hard_ceiling_is_enforced(tmp_path, name):
    with pytest.raises(ValueError):
        config(tmp_path, **{**MAXIMA, name: MAXIMA[name] + 1})


@pytest.mark.parametrize('unit', ['rows', 'bytes'])
def test_session_limit_must_not_exceed_global(tmp_path, unit):
    global_key = 'moss_result_max_' + unit
    session_key = global_key + '_per_session'
    with pytest.raises(ValueError):
        config(tmp_path, **{global_key: DEFAULTS[session_key] - 1})


def test_smallest_complete_pending_envelope_is_accepted(tmp_path):
    limits = dict(moss_result_max_rows=1, moss_result_max_rows_per_session=1,
                  moss_result_max_bytes=294912, moss_result_max_bytes_per_session=294912)
    assert config(tmp_path, **limits).moss_result_max_bytes == 294912
    for name in ('moss_result_max_bytes', 'moss_result_max_bytes_per_session'):
        with pytest.raises(ValueError):
            config(tmp_path, **{**limits, name: 294911})
