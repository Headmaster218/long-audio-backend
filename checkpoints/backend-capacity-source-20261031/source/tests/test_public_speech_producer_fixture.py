"""Model-free checks for explicit public PCM pinning, not ASR qualification."""
import hashlib
import importlib.util
from pathlib import Path
import wave

import pytest

FILE = Path(__file__).resolve().parents[1] / 'qualification/generate_public_speech_java_deliveries.py'
SPEC = importlib.util.spec_from_file_location('public_speech_generator', FILE)
GEN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GEN)


def wav(path, frames=192000, rate=16000):
    pcm = (b'\x01\x02\x03\x04' * ((frames + 1) // 2))[:frames * 2]
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return hashlib.sha256(path.read_bytes()).hexdigest(), pcm


def test_exact_pcm_without_reencoding(tmp_path):
    path = tmp_path / 'sample.wav'
    sha, pcm = wav(path)
    assert GEN.pcm_crop(path, sha, 16000) == pcm[32000:]


@pytest.mark.parametrize('pin', ['', 'g' * 64, '0' * 64])
def test_input_requires_matching_explicit_pin(tmp_path, pin):
    path = tmp_path / 'sample.wav'
    wav(path)
    with pytest.raises(ValueError):
        GEN.pcm_crop(path, pin, 0)


@pytest.mark.parametrize('frames,rate,start', [(175999,16000,0), (192000,8000,0), (192000,16000,-1)])
def test_geometry_and_format_bound(tmp_path, frames, rate, start):
    path = tmp_path / 'sample.wav'
    sha, _ = wav(path, frames, rate)
    with pytest.raises(ValueError):
        GEN.pcm_crop(path, sha, start)


def test_symlink_refused(tmp_path):
    path = tmp_path / 'sample.wav'
    sha, _ = wav(path)
    link = tmp_path / 'link.wav'
    link.symlink_to(path)
    with pytest.raises(ValueError):
        GEN.pcm_crop(link, sha, 0)
