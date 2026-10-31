"""Synthetic source-only tests. Root executor must run; not an acoustic gate."""
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from audio_backend import trial_sampling_census as census


def manifest():
    values = {key: 'a' * 64 for key in census._MANIFEST_HASHES}
    values.update(source_frames=16000, origin_frame=0, start_frame=0,
                  end_frame=16000, interval_frames=960000, sampling_policy='test')
    values['observer_sha256'] = hashlib.sha256(Path(census.__file__).read_bytes()).hexdigest()
    return values


def sink(tmp_path, **kwargs):
    return census.TrialCensus(tmp_path / 'census', run_id='b' * 64, epoch=0,
                              manifest=manifest(), **kwargs)


def test_roundtrip_never_certifies_completeness(tmp_path):
    value = sink(tmp_path)
    assert value.append('WINDOW_BEGIN', 'c' * 64, {'local_start': 0, 'local_end': 16000})
    assert value.append('COMMIT_INTENT', 'c' * 64, {})
    value.close()
    result = census.inspect(tmp_path / 'census')
    assert result['events'] == 2
    assert result['complete_census_certified'] is False
    assert result['status'] == 'INTEGRITY_ONLY_NOT_COMPLETE'
    assert (tmp_path / 'census' / 'terminal.reserve').stat().st_size == census.RESERVE_BYTES


def test_memory_capture_no_mutable_or_unbounded_fields():
    source = {'due': True, 'history_revision': 2, 'reason': 'none'}
    captured = census.capture_fields(source)
    source['due'] = False
    assert captured['due'] is True
    for fields in ({'reason': []}, {'unknown': 1}, {'reason': 'x' * 97},
                   {'frame': 2**63}, {'reason': '\u4e2d'}):
        with pytest.raises(ValueError):census.capture_fields(fields)


def test_event_cap_exact_and_one_over_sticky(tmp_path):
    value = sink(tmp_path, event_limit=2)
    assert value.append('WINDOW_BEGIN', 'c' * 64, {})
    assert value.append('COMMIT_ACK', 'c' * 64, {'outcome': 'true'})
    size = (tmp_path / 'census' / 'events.jsonl').stat().st_size
    assert not value.append('CALLBACK_RESULT', 'c' * 64, {})
    assert not value.healthy
    assert not value.append('TERMINAL_RESULT', 'c' * 64, {})
    assert (tmp_path / 'census' / 'events.jsonl').stat().st_size == size
    value.close()


def test_window_cap_exact_and_one_over(tmp_path):
    value = sink(tmp_path, window_limit=1)
    assert value.append('WINDOW_BEGIN', 'c' * 64, {})
    assert not value.append('WINDOW_BEGIN', 'd' * 64, {})
    value.close()


def test_caps_cannot_be_raised(tmp_path):
    for options in ({'event_limit': 8193}, {'window_limit': 2049}):
        with pytest.raises(ValueError):sink(tmp_path, **options)
    assert not (tmp_path / 'census').exists()


@pytest.mark.parametrize('failure', ['write', 'fsync'])
def test_io_failure_is_sticky_and_never_product_exception(tmp_path, failure):
    value = sink(tmp_path)
    with patch.object(census.os, failure, side_effect=OSError('synthetic')):
        assert value.append('WINDOW_BEGIN', 'c' * 64, {}) is False
    assert not value.healthy
    assert value.failure == 'append_failed'
    assert not value.append('WINDOW_BEGIN', 'd' * 64, {})
    value.close()


def test_partial_tail_is_preserved_and_rejected(tmp_path):
    value = sink(tmp_path)
    real_write = census.os.write
    calls = []
    def torn(fd, data):
        if calls:raise OSError('synthetic')
        calls.append(1)
        return real_write(fd, data[:7])
    with patch.object(census.os, 'write', side_effect=torn):
        assert not value.append('WINDOW_BEGIN', 'c' * 64, {})
    before = (tmp_path / 'census' / 'events.jsonl').read_bytes()
    value.close()
    assert (tmp_path / 'census' / 'events.jsonl').read_bytes() == before
    with pytest.raises(census.CensusError):census.inspect(tmp_path / 'census')


def test_no_reopen_or_replace_existing_evidence(tmp_path):
    value = sink(tmp_path)
    value.close()
    with pytest.raises(FileExistsError):sink(tmp_path)


@pytest.mark.parametrize('phase,key', [('COMMIT_ACK', 'c' * 64), ('WINDOW_BEGIN', 'bad')])
def test_unknown_window_or_bad_key_invalidates(tmp_path, phase, key):
    value = sink(tmp_path)
    assert not value.append(phase, key, {})
    value.close()


def test_port_contains_even_baseexception():
    class Broken:
        healthy = True
        def append(self, *args):raise KeyboardInterrupt()
        def invalidate(self):raise RuntimeError()
    port = census.ObserverPort(Broken())
    assert port.observe('WINDOW_BEGIN', 'c' * 64, {}) is False
    assert not port.healthy


def test_wrong_observer_source_hash_rejected_before_creating(tmp_path):
    values = manifest();values['observer_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        census.TrialCensus(tmp_path / 'census', run_id='b' * 64, epoch=0, manifest=values)
    assert not (tmp_path / 'census').exists()


def test_event_size_has_no_truncation(tmp_path):
    value = sink(tmp_path)
    fields = {key: 'x' * 96 for key in list(census._FIELDS)[:12]}
    assert not value.append('WINDOW_BEGIN', 'c' * 64, fields)
    assert (tmp_path / 'census' / 'events.jsonl').stat().st_size == 0
    value.close()


def test_tamper_detected(tmp_path):
    value = sink(tmp_path)
    assert value.append('WINDOW_BEGIN', 'c' * 64, {'reason': 'none'})
    value.close()
    target = tmp_path / 'census' / 'events.jsonl'
    target.write_bytes(target.read_bytes().replace(b'none', b'evil'))
    with pytest.raises(census.CensusError):census.inspect(tmp_path / 'census')


def test_unrelated_product_sources_unchanged():
    # Stage 2 changes only these explicit optional hook integration files.
    root = Path(__file__).resolve().parents[1]
    baseline = root.parent / 'audio-crop-policy-candidate-rev02-20261022'
    if not baseline.exists():pytest.skip('baseline copy not supplied')
    for old in (baseline / 'audio_backend').rglob('*.py'):
        relative = old.relative_to(baseline)
        if relative.name in {'worker.py', 'native_owned_cell_capture.py', 'moss_handy_jobs.py', 'recording_speaker_history.py'}:continue
        assert (root / relative).read_bytes() == old.read_bytes(), str(relative)


def test_exact_encoded_event_boundary(tmp_path):
    value = sink(tmp_path)
    row = {'schema_version': 1, 'run_id': value.run_id, 'epoch': value.epoch,
           'seq': 1, 'previous_sha256': '0' * 64,
           'manifest_sha256': value._manifest_sha, 'phase': 'WINDOW_BEGIN',
           'opportunity': 'c' * 64, 'fields': {}}
    row['event_sha256'] = census._sha(census._encoded(row))
    length = len(census._encoded(row)) + 1
    with patch.object(census, 'MAX_EVENT_BYTES', length):
        assert value.append('WINDOW_BEGIN', 'c' * 64, {})
        assert not value.append('WINDOW_BEGIN', 'd' * 64, {'reason': 'x'})
    assert (tmp_path / 'census' / 'events.jsonl').stat().st_size == length
    value.close()


def test_real_hard_count_caps(tmp_path):
    value = sink(tmp_path)
    # Actual disk writes and bounds checks; suppress fsync ONLY for this count
    # test. Separate tests inject write/fsync errors and normal tests fsync.
    with patch.object(census.os, 'fsync'):
        for number in range(census.MAX_WINDOWS):
            assert value.append('WINDOW_BEGIN', f'{number:064x}', {})
        for number in range(census.MAX_EVENTS - census.MAX_WINDOWS):
            assert value.append('PREPARE_RESULT', f'{0:064x}', {'outcome': 'synthetic'})
        assert not value.append('PREPARE_RESULT', f'{0:064x}', {})
    value.close()
    result = census.inspect(tmp_path / 'census')
    assert result['windows'] == 2048
    assert result['events'] == 8192
    assert sum(p.stat().st_size for p in (tmp_path / 'census').iterdir()) < census.MAX_BYTES


def test_real_hard_window_cap(tmp_path):
    value = sink(tmp_path)
    with patch.object(census.os, 'fsync'):
        for number in range(census.MAX_WINDOWS):
            assert value.append('WINDOW_BEGIN', f'{number:064x}', {})
        assert not value.append('WINDOW_BEGIN', f'{census.MAX_WINDOWS:064x}', {})
    value.close()


def test_close_fsync_failure_stays_invalid(tmp_path):
    value = sink(tmp_path)
    with patch.object(census.os, 'fsync', side_effect=OSError('synthetic')):
        value.close()
    assert not value.healthy
    assert value.failure == 'terminal_failed'
