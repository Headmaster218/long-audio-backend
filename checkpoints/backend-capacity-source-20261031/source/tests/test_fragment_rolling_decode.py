"""Opt-in compact rolling checks use only the existing frozen four-second files."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import threading
import time

import pytest

import audio_backend.fragment_rolling_decode as r
from audio_backend.fragment_core import SourceChanged, UnsupportedAudio
from audio_backend.fragment_decoded_source import DecodeCancelled, DecodeLimit, Limits, inspect_decoded_source


@pytest.fixture(scope='module')
def cases():
    value = os.environ.get('AUDIO_COMPRESSED_FIXTURES')
    if not value:
        pytest.skip('Existing frozen fixtures required; no downloads or re-encoding')
    root = Path(value)
    result = {}
    for name, suffix in [('whole4_opus', '.ogg'), ('whole4_aac', '.m4a')]:
        path = root / 'encoded' / (name + suffix)
        receipt = inspect_decoded_source(path, enabled=True)
        pcm = (root / 'pcm' / (name + '_decode.pcm')).read_bytes()
        result[name] = path, receipt, pcm
    return result


def wait_done(reader, timeout=3):
    deadline = time.monotonic() + timeout
    while not reader.decoder_reaped and time.monotonic() < deadline:
        time.sleep(.01)
    assert reader.decoder_reaped
    assert not reader._thread.is_alive()
    assert not Path(f'/proc/{reader.decoder_pid}').exists()


@pytest.mark.parametrize('name', ['whole4_opus', 'whole4_aac'])
def test_one_decode_forward_overlap_final_hash(cases, name):
    path, receipt, pcm = cases[name]
    with r.RollingDecodedReader(path, receipt, enabled=True) as reader:
        first = reader.read(1, 17777)
        assert first.pcm == pcm[2:17777 * 2]
        assert first.verification == 'provisional-until-this-session-completes'
        assert reader.completed_receipt is None
        assert reader.compressed_and_recipe_identity_verified is True
        assert reader.state == 'open-provisional'
        for start in range(16001, receipt.emitted_frames, 17003):
            end = min(start + 18000, receipt.emitted_frames)
            assert reader.read(start, end).pcm == pcm[start * 2:end * 2]
        completion = reader.finish()
        assert completion.session_id == first.session_id == reader.session_id
        assert completion.emitted_frames == receipt.emitted_frames
        assert completion.emitted_pcm_sha256 == hashlib.sha256(pcm).hexdigest()
        assert completion.automatic_admission is False
        assert reader.state == 'verified-complete'
        assert reader.decoder_invocations == 1
        assert reader.max_ring_bytes <= 2 * receipt.sample_rate * 2
        assert reader.max_queued_bytes <= r.CHUNK
        assert reader.ring_capacity == 2 * receipt.sample_rate * 2
        wait_done(reader)
        last = receipt.emitted_frames
        assert reader.read(last - 997, last).pcm == pcm[-1994:]
        # Already issued chunks do not silently mutate into verified objects.
        assert first.verification == 'provisional-until-this-session-completes'
    if name.endswith('aac'):
        assert receipt.endpoint_status.startswith('unresolved-aac')
        assert receipt.emitted_frames == 192512


def test_cache_hit_miss_no_hidden_replay(cases):
    path, receipt, pcm = cases['whole4_opus']
    with r.RollingDecodedReader(path, receipt, enabled=True) as reader:
        assert reader.read(96000, 144000).pcm == pcm[192000:288000]
        start = reader.ring_start
        assert reader.read(start, start + 33).pcm == pcm[start * 2:(start + 33) * 2]
        with pytest.raises(r.ReplayRequired) as caught:
            reader.read(0, 1)
        assert caught.value.cached_start == start
        assert reader.decoder_invocations == 1
        assert reader.state == 'open-provisional'
        reader.finish()


def test_backpressure_pause_resume_and_cancel(cases):
    path, receipt, _ = cases['whole4_opus']
    with r.RollingDecodedReader(path, receipt, enabled=True) as reader:
        first = reader.read(0, 160)
        deadline = time.monotonic() + 1
        while reader._queue.empty() and time.monotonic() < deadline:
            time.sleep(.005)
        assert reader._queue.qsize() == 1
        assert reader.completed_receipt is None
        before = Path(f'/proc/{reader.decoder_pid}/stat').read_text().split()
        time.sleep(.15)
        after = Path(f'/proc/{reader.decoder_pid}/stat').read_text().split()
        assert sum(map(int, after[13:15])) - sum(map(int, before[13:15])) <= 3
        assert reader.read(160, 1000).pcm
        reader.cancel()
        wait_done(reader)
        assert reader.state == 'cancelled'
        assert reader.completed_receipt is None
        assert first.verification.startswith('provisional')
        with pytest.raises(DecodeCancelled):
            reader.read(0, 1)


def test_idle_expiry_reaps_without_consumer(cases):
    path, receipt, _ = cases['whole4_opus']
    with r.RollingDecodedReader(path, receipt, enabled=True, idle_seconds=.15) as reader:
        window = reader.read(0, 100)
        wait_done(reader)
        assert reader.state == 'failed-invalidated'
        assert reader.completed_receipt is None
        assert window.verification.startswith('provisional')
        with pytest.raises(DecodeLimit, match='Idle'):
            reader.finish()


def test_external_cancel_works_during_pause(cases):
    path, receipt, _ = cases['whole4_opus']
    event = threading.Event()
    with r.RollingDecodedReader(path, receipt, enabled=True, cancel=event.is_set) as reader:
        reader.read(0, 100)
        event.set()
        wait_done(reader)
        assert reader.completed_receipt is None
        with pytest.raises(DecodeCancelled):
            reader.finish()


def test_close_before_eos_invalidates(cases):
    path, receipt, _ = cases['whole4_opus']
    reader = r.RollingDecodedReader(path, receipt, enabled=True)
    first = reader.read(0, 100)
    reader.close()
    wait_done(reader)
    assert reader.state == 'closed-unverified'
    assert reader.completed_receipt is None
    assert first.verification.startswith('provisional')
    reader.close()  # idempotent cleanup


def test_mismatched_full_hash_invalidates_all_chunks(cases):
    path, receipt, _ = cases['whole4_opus']
    wrong = replace(receipt, emitted_pcm_sha256='0' * 64)
    with r.RollingDecodedReader(path, wrong, enabled=True) as reader:
        first = reader.read(0, 100)
        assert first.pcm
        assert reader.completed_receipt is None
        with pytest.raises(SourceChanged, match='Full rolling'):
            reader.finish()
        wait_done(reader)
        assert reader.state == 'failed-invalidated'
        assert reader.completed_receipt is None


def test_source_replacement_invalidates(cases, tmp_path):
    path, receipt, _ = cases['whole4_opus']
    target = tmp_path / 'copy.ogg'
    target.write_bytes(path.read_bytes())
    with r.RollingDecodedReader(target, receipt, enabled=True) as reader:
        reader.read(0, 100)
        replacement = tmp_path / 'new.ogg'
        replacement.write_bytes(path.read_bytes())
        replacement.replace(target)
        with pytest.raises(SourceChanged):
            reader.read(100, 200)
        wait_done(reader)
        assert reader.completed_receipt is None


def test_guards_do_not_advance_reader(cases):
    path, receipt, _ = cases['whole4_opus']
    with pytest.raises(UnsupportedAudio, match='default OFF'):
        r.RollingDecodedReader(path, receipt)
    with pytest.raises(ValueError):
        r.RollingDecodedReader(path, receipt, enabled=True, idle_seconds=float('nan'))
    with r.RollingDecodedReader(path, receipt, enabled=True) as reader:
        for start, end in [(True, 10), (0, 48001), (-1, 2), (100, 99), (192000, 192001)]:
            with pytest.raises(ValueError):
                reader.read(start, end)
        assert reader.consumed_end == 0
        reader._consumer_lock.acquire()
        try:
            with pytest.raises(RuntimeError, match='one read'):
                reader.read(0, 1)
        finally:
            reader._consumer_lock.release()


@pytest.mark.parametrize('code, error', [
    ('import os;os.write(1,b"x"*200);raise SystemExit(2)', UnsupportedAudio),
    ('import os;os.write(2,b"x"*20000)', DecodeLimit),
    ('import os;os.write(1,b"x"*101)', UnsupportedAudio),
    ('import time;time.sleep(5)', DecodeLimit),
])
def test_failure_cleanup_no_completed_receipt(cases, monkeypatch, code, error):
    path, receipt, _ = cases['whole4_opus']
    monkeypatch.setattr(r, '_decoder_command', lambda *args: ['/usr/bin/python3', '-c', code])
    # One-second whole-session budget includes setup and consumer pauses.
    with r.RollingDecodedReader(path, receipt, enabled=True, limits=Limits(wall_seconds=1.0), idle_seconds=.8) as reader:
        with pytest.raises(error):
            reader.finish()
        wait_done(reader)
        assert reader.completed_receipt is None
        assert reader.state == 'failed-invalidated'


def test_sample_guard_applies_while_paused(cases, monkeypatch):
    path, receipt, _ = cases['whole4_opus']
    with r.RollingDecodedReader(path, receipt, enabled=True) as reader:
        reader.read(0, 100)
        def fail(*args):
            raise DecodeLimit('Simulated resource reserve breach')
        monkeypatch.setattr(reader._source.guard, 'check', fail)
        wait_done(reader)
        with pytest.raises(DecodeLimit, match='reserve breach'):
            reader.finish()
        assert reader.completed_receipt is None


def test_pairing_and_exclusive_session_lease(cases):
    path, receipt, _ = cases['whole4_opus']
    with r.RollingDecodedReader(path, receipt, enabled=True) as reader:
        window = reader.read(0, 100)
        assert not reader.is_verified_window(window, None)
        with pytest.raises(RuntimeError, match='one active'):
            r.RollingDecodedReader(path, receipt, enabled=True)
        completion = reader.finish()
        assert reader.is_verified_window(window, completion)
        assert not reader.is_verified_window(window, replace(completion))
        assert not reader.is_verified_window(replace(window, pcm=b'x' * len(window.pcm)), completion)
        assert not reader.is_verified_window(replace(window, integrity_token='é' * 64), completion)
        # Completion releases the lease even though the first object remains alive.
        with r.RollingDecodedReader(path, receipt, enabled=True) as other:
            other_window = other.read(0, 100)
            assert not other.is_verified_window(other_window, completion)
            other.cancel()
            assert not other.is_verified_window(other_window, completion)
    # An unsuccessful constructor must release its lease too.
    with pytest.raises(SourceChanged):
        r.RollingDecodedReader(path, replace(receipt, compressed_sha256='0' * 64), enabled=True)
    with r.RollingDecodedReader(path, receipt, enabled=True) as again:
        again.close()
