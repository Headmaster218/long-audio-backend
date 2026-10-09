"""Bounded, synthetic, model-free experiment; no production admission."""
from dataclasses import replace
import array
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import wave

import pytest

from audio_backend.fragment_core import (BoundedPCM16Reader, SourcePlan, SourceChanged,
    UnsupportedAudio, Span, Seam, Alias, inspect_pcm16_source, _layout, _seam_status)
from audio_backend.fragment_matcher import Continuity, BoundaryEvidence, match_boundary
from audio_backend.fragment_normalizer import (FinalizedPCM16Normalizer, FILTER, STEREO,
    FINGERPRINT, MAX_NATIVE_READ)

CONT = Continuity('continuous', same_capture=True, replay_possible=False)


def pcm(rate, channels, n=None, cancellation=False):
    rng = random.Random(1928 + rate)
    values = array.array('h')
    for _ in range(n or (2 * rate + 1)):
        v = rng.randrange(-12000, 12001)
        values.append(v)
        if channels == 2:
            values.append(-v if cancellation else rng.randrange(-12000, 12001))
    if sys.byteorder != 'little':
        values.byteswap()
    return values.tobytes()


def wav(path, data, rate, channels):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(channels); out.setsampwidth(2); out.setframerate(rate); out.writeframes(data)


def plan_for(tmp, data, rate, channels, cuts=None, overlap=0):
    n = len(data) // (channels * 2)
    cuts = cuts or [0, n]
    sources, paths, seams = [], {}, []
    cursor = 0
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        trim = overlap if i == 1 else 0
        path = tmp / f'{len(list(tmp.glob("*.wav")))}-{i}.wav'
        wav(path, data[(a-trim)*channels*2:b*channels*2], rate, channels)
        source = inspect_pcm16_source(str(i), path)
        sources.append(source); paths[str(i)] = path
        if i:
            if trim:
                left = paths[str(i-1)]
                with wave.open(str(left), 'rb') as stream: tail = stream.readframes(stream.getnframes())
                with wave.open(str(path), 'rb') as stream: head = stream.readframes(stream.getnframes())
                evidence = match_boundary(tail, head, sample_rate=rate, channels=channels, declaration=CONT)
                assert evidence.trim_frames == trim
            else:
                # Explicit already-decided no-overlap seam for logical-reader tests.
                # We do not claim the matcher can infer continuity from one frame.
                evidence = BoundaryEvidence('abstain', 'no_exact_overlap_evidence')
            seams.append(Seam(str(i-1), str(i), CONT, evidence, cursor, _seam_status(CONT, evidence)))
        cursor += source.frames - trim
    spans, aliases = _layout(sources, seams)
    return SourcePlan(tuple(sources), tuple(spans), tuple(aliases), tuple(seams), True).validate(), paths


def whole(tmp, data, rate, channels):
    src, out = tmp / 'reference.wav', tmp / 'reference.pcm'
    wav(src, data, rate, channels)
    subprocess.run(['/usr/bin/ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-threads', '1',
        '-filter_threads', '1', '-filter_complex_threads', '1', '-i', str(src), '-map', '0:a:0', '-vn',
        '-af', (STEREO if channels == 2 else '') + FILTER, '-ac', '1', '-ar', '16000',
        '-c:a', 'pcm_s16le', '-threads', '1', '-f', 's16le', '-y', str(out)], check=True, timeout=15)
    result = out.read_bytes()  # Synthetic <=2.01-second reference only.
    src.unlink(); out.unlink()
    return result


def adapter(plan, paths, tmp):
    return FinalizedPCM16Normalizer(plan, paths, temp_root=tmp, enabled=True)


@pytest.mark.parametrize('rate', [44100, 48000])
@pytest.mark.parametrize('channels', [1, 2])
@pytest.mark.parametrize('overlap', [False, True])
def test_whole_fragments_overlap_restart_random(rate, channels, overlap, tmp_path):
    data = pcm(rate, channels)
    ref = whole(tmp_path, data, rate, channels)
    n = len(data) // (channels * 2)
    cuts = [0, rate+137, 2*rate, n]
    plan, paths = plan_for(tmp_path, data, rate, channels, cuts, rate//2 if overlap else 0)
    assert plan.spans[-1].source_end - plan.spans[-1].source_start == 1
    reader = adapter(plan, paths, tmp_path)
    assert reader.native_end == n and reader.canonical_end == len(ref)//2
    assert reader.plan.aliases == plan.aliases
    got = b''.join(reader.read(a, min(a+8000, reader.canonical_end)).pcm
                   for a in range(0, reader.canonical_end, 8000))
    assert got == ref
    plan_file = tmp_path / 'plan.json'; plan_file.write_text(plan.to_json())
    restored = adapter(SourcePlan.from_json(plan_file.read_text()), paths, tmp_path)
    rng = random.Random(332)
    windows = [(0,1), (reader.canonical_end-1, reader.canonical_end)]
    for _ in range(12):
        a = rng.randrange(reader.canonical_end)
        windows.append((a, min(reader.canonical_end, a+rng.randrange(1,8001))))
    for a, b in reversed(windows):
        result = restored.read(a,b)
        assert result.pcm == ref[2*a:2*b] == reader.read(a,b).pcm
        assert len(result.pcm) == 2*(b-a)
        assert result.native_context_end-result.native_context_start <= MAX_NATIVE_READ
        assert result.owners and result.normalizer_fingerprint == FINGERPRINT
        assert result.native_end == n and result.plan_sha256 == plan.sha256
    assert reader.max_native_read <= MAX_NATIVE_READ
    assert reader.max_temp_bytes <= 512*1024 and reader.max_tree_rss <= 256*1024**2
    assert not list(tmp_path.glob('fragment-normalizer-*'))


@pytest.mark.parametrize('rate', [44100, 48000])
@pytest.mark.parametrize('channels', [1, 2])
def test_floor_with_tiny_physical_receipts(rate, channels, tmp_path):
    data = pcm(rate, channels, 123)
    plan, paths = plan_for(tmp_path, data, rate, channels, [0,1,2,7,17,31,67,122,123])
    read = adapter(plan, paths, tmp_path)
    ref = whole(tmp_path, data, rate, channels)
    assert read.canonical_end == len(ref)//2
    assert b''.join(read.read(i,i+1).pcm for i in range(read.canonical_end)) == ref


def test_fail_closed(tmp_path):
    data = pcm(44100, 1, 9000)
    plan, paths = plan_for(tmp_path, data, 44100, 1)
    with pytest.raises(UnsupportedAudio): FinalizedPCM16Normalizer(plan, paths, temp_root=tmp_path)
    with pytest.raises(UnsupportedAudio): adapter(replace(plan, final=False), paths, tmp_path)
    tiny, tiny_paths = plan_for(tmp_path, data[:244], 44100, 1)
    with pytest.raises(UnsupportedAudio): adapter(tiny, tiny_paths, tmp_path)
    other, other_paths = plan_for(tmp_path, data, 16000, 1)
    with pytest.raises(UnsupportedAudio): adapter(other, other_paths, tmp_path)
    with pytest.raises(UnsupportedAudio):
        FinalizedPCM16Normalizer(plan, paths, temp_root=tmp_path, enabled=True, executable='/bin/true')
    read = adapter(plan, paths, tmp_path)
    for a, b in [(-1,1), (True,1), (0,8001), (1,0), (0,read.canonical_end+1)]:
        with pytest.raises(ValueError): read.read(a,b)
    assert read.read(0,0).pcm == b''
    with paths['0'].open('r+b') as stream:
        stream.seek(46); stream.write(b'\x02\x01')
    with pytest.raises(SourceChanged): read.read(0,1)
    with pytest.raises(SourceChanged): adapter(plan, paths, tmp_path)


def test_stereo_alias_cancellation_rejected_before_mix(tmp_path):
    rate = 44100
    data = pcm(rate, 2, cancellation=True)
    plan, paths = plan_for(tmp_path, data, rate, 2, [0,rate+137,2*rate+1], rate//2)
    good = adapter(plan, paths, tmp_path)
    assert set(good.read(0,8000).pcm) == {0}
    alias = plan.aliases[0]
    # Opposite channel edits preserve the mono average. Rebind source hash to
    # model a forged/stale seam assertion, so alias-byte validation is required.
    with paths[alias.fragment_id].open('r+b') as stream:
        stream.seek(44); v = int.from_bytes(stream.read(2), 'little', signed=True)
        stream.seek(44); stream.write((v+1).to_bytes(2,'little',signed=True)); stream.write((-v-1).to_bytes(2,'little',signed=True))
    updated = tuple(inspect_pcm16_source(s.fragment_id, paths[s.fragment_id]) for s in plan.sources)
    forged = replace(plan, sources=updated).validate()
    with pytest.raises(SourceChanged, match='all-channel alias'):
        adapter(forged, paths, tmp_path)


def test_short_reader_and_malformed_rejected(tmp_path, monkeypatch):
    plan, paths = plan_for(tmp_path, pcm(48000,1,1000),48000,1)
    read = adapter(plan,paths,tmp_path)
    monkeypatch.setattr(read.reader, 'read', lambda a,b: b'\x00\x00')
    with pytest.raises(SourceChanged, match='Short logical'): read.read(0,1)
    path = tmp_path/'bad.wav'; path.write_bytes(b'not a wav')
    with pytest.raises((UnsupportedAudio, EOFError)): inspect_pcm16_source('bad',path)
    payload = json.loads(plan.to_json()); payload['spans'][0]['logical_end'] += 1
    with pytest.raises(ValueError): SourcePlan.from_json(json.dumps(payload))

@pytest.mark.parametrize('rate', [44100, 48000])
def test_last_native_impulse_and_seam_windows(rate, tmp_path):
    n = 2*rate+1
    values = array.array('h', [0])*n
    cuts = [0, rate*317//1000+1, rate+7, 2*rate, n]
    for c in cuts[1:-1]:
        for d in (-1,0,1):
            if c+d < n: values[c+d] = 21000 if d == 0 else -13000
    values[-1] = 30000
    if sys.byteorder != 'little': values.byteswap()
    data = values.tobytes()
    plan, paths = plan_for(tmp_path,data,rate,1,cuts)
    reader = adapter(plan,paths,tmp_path)
    ref = whole(tmp_path,data,rate,1)
    assert reader.canonical_end == len(ref)//2
    for cut in cuts[1:]:
        center = (cut*16000+rate-1)//rate
        a,b = max(0,center-64), min(reader.canonical_end,center+64)
        assert reader.read(a,b).pcm == ref[2*a:2*b]


def test_unknown_gap_mixed_and_missing_source(tmp_path):
    from audio_backend.fragment_core import _layout, _seam_status
    data = pcm(44100,1,2000)
    plan, paths = plan_for(tmp_path,data,44100,1,[0,1000,2000])
    for declaration in (Continuity('unknown'), Continuity('gap',gap_frames=100)):
        seam = replace(plan.seams[0], declaration=declaration,
                       status=_seam_status(declaration,plan.seams[0].evidence))
        spans, aliases = _layout(plan.sources,[seam])
        changed = replace(plan,spans=tuple(spans),aliases=tuple(aliases),seams=(seam,))
        with pytest.raises(UnsupportedAudio): adapter(changed,paths,tmp_path)
    mixed = replace(plan,sources=(plan.sources[0],replace(plan.sources[1],sample_rate=48000)))
    with pytest.raises(UnsupportedAudio): adapter(mixed,paths,tmp_path)
    reader = adapter(plan,paths,tmp_path)
    paths['0'].unlink()
    with pytest.raises(SourceChanged): reader.read(reader.canonical_end-1,reader.canonical_end)


def test_exact_output_count_is_enforced(tmp_path,monkeypatch):
    plan,paths = plan_for(tmp_path,pcm(48000,1,1000),48000,1)
    reader = adapter(plan,paths,tmp_path)
    monkeypatch.setattr(reader,'_resample',lambda data: b'')
    with pytest.raises(UnsupportedAudio,match='Exact canonical'): reader.read(0,1)


def test_constructor_never_uses_unbounded_path_read_bytes(tmp_path, monkeypatch):
    plan, paths = plan_for(tmp_path,pcm(44100,1,1000),44100,1)
    def forbidden(*args, **kwargs):
        raise AssertionError('Unbounded Path.read_bytes is forbidden in adapter/source binding')
    monkeypatch.setattr(Path,'read_bytes',forbidden)
    reader = adapter(plan,paths,tmp_path)
    assert len(reader.read(0,100).pcm) == 200
