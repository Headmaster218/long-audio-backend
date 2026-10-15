"""Bounded model-free seam-gap reproducer; NOT acoustic/runtime qualification.

Uses real Worker, primary Store, source authority, coordinator and capture.
Native responses, ASR words and the synchronous publication sink are synthetic.
No model, decoder, server, production optional store or qualification is started.
Current missing composite evidence is an explicit GAP, never coverage success.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import struct
from types import MethodType, SimpleNamespace
import wave

import pytest

from audio_backend import native_worker
from audio_backend.boundary_store import context_parts
from audio_backend.native_owned_cell_capture import Capture, PrimaryAuthority
from audio_backend.native_owned_cell_store import Budget, Operation
from audio_backend.native_owned_cells import RATE, RING_PCM_BYTES, canonical
from audio_backend.native_owned_seam_cells import STORE_POLICY_SHA256, is_composite
from audio_backend.native_worker import NativeCoordinator
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.worker import Worker, own_core
from test_native_owned_support import synthetic_provenance, synthetic_state
from test_native_owned_seam_cells import synthetic_pieces


PERIOD = b"".join(struct.pack("<h", n * 97 - 12000) for n in range(257))


def pcm(a, b):
    """Coordinate-dependent, synthetic canonical PCM; one bounded interval."""
    assert isinstance(a, int) and isinstance(b, int) and 0 <= a < b
    count = b - a
    offset = a % 257
    return (PERIOD * ((offset + count + 256) // 257))[offset * 2:(offset + count) * 2]


def write_wav(path, data):
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(RATE)
        target.writeframes(data)


def read_wav(path):
    with wave.open(str(path), "rb") as source:
        assert (source.getnchannels(), source.getsampwidth(), source.getframerate()) == (1, 2, RATE)
        return source.readframes(source.getnframes())


def merged(intervals):
    result = []
    for a, b in sorted(intervals):
        if result and a <= result[-1][1]:result[-1][1] = max(result[-1][1], b)
        else:result.append([a, b])
    return result


def assert_disjoint_cover(intervals, a, b):
    ordered = sorted(intervals)
    assert ordered and ordered[0][0] == a and ordered[-1][1] == b
    assert all(x < y for x, y in ordered)
    assert all(left[1] == right[0] for left, right in zip(ordered, ordered[1:]))


class SyntheticSink:
    """Observes publication synchronously; makes NO durable-retention claim."""
    enabled = True
    seam_enabled = True
    _store_policy = STORE_POLICY_SHA256

    def __init__(self):
        self._epoch = object(); self.receipts = {}; self.published = []; self.authority = None

    def health(self):return {"ready": True}
    def receipt(self, cid):return self.receipts.get(cid)

    def submit(self, kind, payload):
        if kind == "receipt":
            self.receipts[payload.chunk_id] = payload
        else:
            assert kind == "publish"
            with Budget().phase() as budget:
                for cell in payload:
                    assert self.authority.cell_valid(json.loads(cell.proof), budget, publishing=True)
                    assert cell.pcm == pcm(cell.support.start_frame, cell.support.end_frame)
            self.published.extend(payload)
        operation = Operation(kind, None, self._epoch)
        operation.result = {"status": "synthetic_observed_only"}; operation.done.set()
        return operation


class SyntheticNative:
    """Producer-valid schema; synthetic batch frontiers and query-clipped turns."""
    def __init__(self, settings, harness):
        self.h = harness; self.frames = 0; self.finished = False; self.replay = None
        self.epoch = None; self.resets = []; self.calls = []

    def state(self):
        processed = self.frames if self.finished else max(0, (self.frames - 1280) // 337920 * 337920)
        stable = processed if self.finished else max(0, processed - 2 * RATE)
        return synthetic_state(self.epoch, self.frames, finished=self.finished,
                               processed_frames=processed, stable_frames=stable)

    def request(self, action, stop, **args):
        assert not stop(); self.calls.append(action)
        if action == "reset":
            self.epoch = args["epoch_id"]; self.frames = 0; self.finished = False; self.replay = None
            self.resets.append(self.epoch)
            response = {"state": self.state(), "provenance": synthetic_provenance()}
        elif action == "push":
            record = self.h.windows[str(args["wav"])]
            data = read_wav(args["wav"]); a, b = record["frames"]
            assert data == pcm(a, b) and 0 < b - a <= 339200
            if self.replay is None:self.replay = a
            assert a == self.replay + self.frames
            self.frames += b - a
            self.h.native_reads.append({"epoch": self.epoch, "frames": [a, b],
                                        "sha256": hashlib.sha256(data).hexdigest()})
            response = self.state()
        elif action == "finish":
            self.finished = True; response = self.state()
        else:
            assert action == "turns" and self.replay is not None
            qa = self.replay + round(args["start"] * RATE)
            qb = self.replay + round(args["end"] * RATE)
            turns = []
            for voice, intervals in ((1, [(qa, qb)]), (2, self.h.activity)):
                for a, b in intervals:
                    a, b = max(qa, a), min(qb, b)
                    if a < b:
                        turns.append({"start": (a - self.replay) / RATE,
                                      "end": (b - self.replay) / RATE,
                                      "speaker_local_id": f"{self.epoch}:speaker_{voice}"})
            response = {"turns": turns}
        observer = getattr(self, "response_observer", None)
        if observer:observer(response)
        return response

    def close(self):pass


def analyzer_result(native, start, end, markers):
    segments = []
    epoch = native["state"]["epoch_id"]
    for marker, voice, name in markers:
        if start <= marker - 800 and marker + 800 <= end:
            lo, hi = (marker - 800 - start) / RATE, (marker + 800 - start) / RATE
            label = f"{epoch}:speaker_{voice}"
            segments.append({"start": lo, "end": hi, "text": name,
                "speaker_local_id": label, "source_track_id": label,
                "_units": [{"start": lo, "end": hi, "text": name,
                            "ownership_anchor": (marker - start) / RATE}]})
    return {"segments": segments, "events": [], "speaker_turns": deepcopy(native["turns"]),
            "metadata": {"diarization_turns": deepcopy(native["turns"]),
                         "fixture": "synthetic bookkeeping only"}}


class Harness:
    def __init__(self, root, seams):
        self.root = root; self.windows = {}; self.native_reads = []; self.analyzer_reads = []
        self.commits = []; self.capture_steps = []; self.source_ids = []
        self.activity = merged([(round((s - .75) * RATE), round((s + .75) * RATE)) for s in seams])
        self.markers = [(round((s + delta) * RATE), voice, f"m{n}_{side}_v{voice}")
                        for n, s in enumerate(seams) for side, delta in enumerate((-.25, .25))
                        for voice in (1, 2)]
        (root / "uploads").mkdir(); (root / "tmp").mkdir()
        self.settings = Settings(data_dir=root, disk_reserve_bytes=0, start_worker=False,
                                diarization_backend="nemotron", native_diarization_model="synthetic",
                                native_diarization_library="synthetic")
        self.store = Store(root / "primary.sqlite", self.settings)
        self.sid = self.store.create_session("synthetic multicore seam")['id']
        self.owner = SyntheticSink(); self.authority = PrimaryAuthority(self.store)
        self.owner.authority = self.authority; self.authority.owner = self.owner
        self.capture = Capture(self.owner, self.authority)
        harness = self

        class Analyzer:
            def __init__(self, config):pass
            def close(self):pass
            def analyze(self, path, diarization=None):
                a, b = harness.windows[str(path)]["frames"]
                data = read_wav(path); assert data == pcm(a, b)
                harness.analyzer_reads.append({"frames": [a, b], "sha256": hashlib.sha256(data).hexdigest()})
                return analyzer_result(diarization, a, b, harness.markers)

        self.worker = Worker(self.store, self.settings, Analyzer)
        self.worker.native_owned_cells = self.capture
        self.worker.extract_pcm = MethodType(self.extract, self.worker)
        original = self.store.commit_window

        def commit(chunk, offset, end, result):
            accepted = original(chunk, offset, end, result)
            if accepted:
                a = round((chunk['timeline_start'] + offset) * RATE)
                b = round((chunk['timeline_start'] + end) * RATE)
                self.commits.append({"cid": chunk['id'], "frames": [a, b],
                                     "local": [round(offset * RATE), round(end * RATE)]})
            return accepted
        self.store.commit_window = commit
        committed = self.capture.committed

        def observe_commit(cells):
            prepare_reason = self.capture.last_seam_reason
            result = committed(cells)
            self.capture_steps.append({"prepare_reason": prepare_reason,
                "commit_reason": self.capture.last_seam_reason,
                "tail_present": self.capture._seam_tail is not None,
                "ordinary_selected": len(cells)})
            return result
        self.capture.committed = observe_commit

    def extract(self, worker, chunk, start, end, wav, tmp, stop, **kwargs):
        assert not stop(); a, b = round(start * RATE), round(end * RATE)
        assert 0 < b - a <= 60 * RATE
        data = bytearray(); parts = context_parts(self.store, chunk, start, end)
        for part in parts:
            with wave.open(part['path'], 'rb') as source:
                source.setpos(round(part['read_start'] * RATE))
                data.extend(source.readframes(round(part['read_duration'] * RATE)))
        assert bytes(data) == pcm(a, b)
        write_wav(wav, data)
        self.windows[str(wav)] = {"frames": [a, b], "source_ids": [p['id'] for p in parts]}
        return None

    def add(self, seconds, final):
        origin = sum(self.store.chunk(cid)['duration'] for cid in self.source_ids)
        a, b = round(origin * RATE), round((origin + seconds) * RATE)
        path = self.root / "uploads" / f"source{len(self.source_ids)}.part"
        write_wav(path, pcm(a, b)); data = path.read_bytes(); digest = hashlib.sha256(data).hexdigest()
        with path.open('rb') as source:
            observed = self.capture.streamed_descriptor(source.fileno(), digest, len(data))
        token = self.capture.prepare_upload(path, digest, len(data), observed)
        assert token is not None, self.capture.last_reason
        final_path = path.with_suffix('.audio'); path.rename(final_path)
        cid, created = self.store.add_chunk(self.sid, path.name, digest, final_path, seconds,
                                            stream_id="stream", stream_final=final)
        result = self.capture.finish_upload(token, self.sid, cid, final_path, created)
        assert isinstance(result, Operation) and self.owner.receipt(cid) is not None
        self.source_ids.append(cid)

    def run(self, monkeypatch):
        monkeypatch.setattr(native_worker, "NativeProcess", lambda settings: SyntheticNative(settings, self))
        monkeypatch.setattr("audio_backend.worker.check_resources", lambda *a, **k: None)
        while (chunk := self.store.claim()) is not None:self.worker.process(chunk)
        assert all(self.store.chunk(cid)['status'] == 'completed' for cid in self.source_ids)
        assert self.capture.witness.failure is None, self.capture.witness.failure
        assert len(self.capture.witness.ring) <= RING_PCM_BYTES

    def report(self, kind, gap):
        report = {"kind": kind, "synthetic": True, "acoustic_quality_tested": False,
                  "durable_optional_retention_tested": False, "optional_gap": gap,
                  "native_reads": self.native_reads, "analyzer_reads": self.analyzer_reads,
                  "primary_commits": self.commits, "capture_steps": self.capture_steps,
                  "ordinary_cells_observed": len(self.owner.published),
                  "v2_composites_observed": sum(is_composite(json.loads(c.proof)) for c in self.owner.published)}
        (self.root / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({k: report[k] for k in ('kind', 'optional_gap', 'ordinary_cells_observed', 'v2_composites_observed')}))


@pytest.mark.parametrize('kind,durations,seams,expected', [
    ('internal_core', [32], [30], [(0, 30), (30, 32)]),
    ('long_right_upload', [31, 31], [30, 31, 61], [(0, 30), (30, 31), (31, 61), (61, 62)]),
])
def test_within_epoch_primary_accounting_and_explicit_optional_gap(tmp_path, monkeypatch, kind, durations, seams, expected):
    h = Harness(tmp_path, seams)
    try:
        for i, duration in enumerate(durations):h.add(duration, i == len(durations) - 1)
        h.run(monkeypatch)
        total = sum(durations) * RATE
        assert [tuple(c['frames']) for c in h.commits] == [(a * RATE, b * RATE) for a, b in expected]
        assert_disjoint_cover([r['frames'] for r in h.native_reads], 0, total)
        assert_disjoint_cover([r['frames'] for r in h.commits], 0, total)
        assert len({r['epoch'] for r in h.native_reads}) == 1
        with h.store.db() as db:
            segments = [dict(r) for r in db.execute('SELECT text,start,end,speaker_local_id FROM segments ORDER BY id')]
            turns = [dict(r) for r in db.execute('SELECT start,end,speaker_local_id FROM speaker_turns ORDER BY id')]
        assert Counter(r['text'] for r in segments) == Counter(name for _, _, name in h.markers)
        for seam in seams:
            for delta in (-.125, .125):
                active = {r['speaker_local_id'].split(':')[-1] for r in turns if r['start'] <= seam + delta < r['end']}
                assert active == {'speaker_1', 'speaker_2'}
        assert h.owner.published and not any(is_composite(json.loads(c.proof)) for c in h.owner.published)
        assert all(not (c.support.start_frame < seam * RATE < c.support.end_frame)
                   for c in h.owner.published for seam in seams)
        for cell in h.owner.published:
            document = json.loads(cell.proof)
            with Budget().phase() as budget:
                assert h.authority.cell_valid(document, budget, publishing=False)
            if round(h.store.chunk(cell.chunk_id)['next_offset'] * RATE) > cell.commit_end:
                with Budget().phase() as budget:
                    assert not h.authority.cell_valid(document, budget, publishing=True)
        assert h.capture_steps[0]['prepare_reason'] == 'seam_terminal_core_required'
        if kind == 'long_right_upload':
            assert h.capture_steps[1]['tail_present'] is True
            assert h.capture_steps[2]['prepare_reason'] == 'seam_terminal_core_required'
            assert h.capture_steps[2]['tail_present'] is False
            # Exact source mapping around A-B remains available while B is multi-core.
            with Budget().phase() as budget:
                parts = h.authority.mapping(h.store.chunk(h.source_ids[1]), 0., 30 * RATE, 32 * RATE, budget)
                assert [p.chunk_id for p in parts] == h.source_ids
                assert h.authority.original_pcm(parts, budget) == pcm(30 * RATE, 32 * RATE)
        h.report(kind, 'No cross-core composite; long successor discards predecessor tail' if len(durations) > 1 else
                 'Same-upload internal core seam is outside v2 proof contract')
    finally:h.worker.stop()


@pytest.mark.parametrize('seam_seconds', [300, 3600])
def test_reset_boundary_seeded_prior_tail_and_observed_new_epoch_only(tmp_path, seam_seconds):
    """Prior tail/left words are seeded SYNTHETIC; no prior epoch reads claimed."""
    (tmp_path / 'tmp').mkdir()
    settings = Settings(data_dir=tmp_path, disk_reserve_bytes=0, start_worker=False)
    s = seam_seconds * RATE
    h = SimpleNamespace(windows={}, native_reads=[], activity=[(s - RATE, s + RATE)])
    owner = SyntheticSink(); capture = Capture(owner, SimpleNamespace())
    capture.scope = ('stream', 'session', 'stream'); capture.scope_start = 0.
    capture.observed_reset_generation = 1
    capture._seam_tail = synthetic_pieces()[0]  # Seeded old tail, not live commit authority.
    assert capture._seam_tail is not None

    def extract(chunk, start, end, path, temporary, stop):
        a, b = round(start * RATE), round(end * RATE)
        write_wav(path, pcm(a, b)); h.windows[str(path)] = {'frames': [a, b]}
        capture.observe_pcm('native', path, start, end)

    def factory(config):
        result = SyntheticNative(config, h); result.response_observer = capture.observe_response
        return result

    coordinator = NativeCoordinator(settings, extract, factory)
    chunk = {'id': 'right', 'session_id': 'session', 'timeline_start': float(seam_seconds),
             'duration': 32., 'metadata': None}
    info = {'stream_id': 'stream', 'start': 0., 'end': float(seam_seconds + 32),
            'final': True, 'previous': None}
    capture.begin_prepare(chunk, info)
    try:
        native = coordinator.prepare(chunk, 0., 30., info, lambda: False)
        capture.end_prepare()
        assert native is not None and native['end'] == 30.
        assert native['state']['epoch_index'] == seam_seconds // 300
        assert capture._seam_tail is None and capture.last_seam_reason == 'seam_native_reset'
        assert capture.observed_reset_generation == 2
        replay = s - 339200
        assert h.native_reads[0]['frames'][0] == replay
        assert_disjoint_cover([r['frames'] for r in h.native_reads], replay, h.native_reads[-1]['frames'][1])
        assert len(capture.witness.ring) <= RING_PCM_BYTES and capture.witness.failure is None
        aa = s + round(native['extract_start'] * RATE); ab = s + round(native['extract_end'] * RATE)
        analyzer_pcm = pcm(aa, ab)
        assert capture.witness.read(aa, ab) == analyzer_pcm
        assert aa == s - 2 * RATE and ab >= s + 30 * RATE
        markers = [(s - 4000, voice, f'left_v{voice}') for voice in (1, 2)] + [
                   (s + 4000, voice, f'right_v{voice}') for voice in (1, 2)]
        current = analyzer_result(native, aa, ab, markers)
        current = own_core(current, (s - aa) / RATE, (s + 30 * RATE - aa) / RATE, base=(aa - s) / RATE)
        assert Counter(row['text'] for row in current['segments']) == Counter(['right_v1', 'right_v2'])
        seeded_left = {'segments': [], 'events': [], 'speaker_turns': [], 'metadata': {}}
        for voice in (1, 2):
            seeded_left['segments'].append({'start': 28.7, 'end': 28.8, 'text': f'left_v{voice}',
                'speaker_local_id': f'seeded-old-epoch:speaker_{voice}',
                '_units': [{'start': 28.7, 'end': 28.8, 'text': f'left_v{voice}', 'ownership_anchor': 28.75}]})
        left = own_core(seeded_left, 0., 29.)
        assert Counter(r['text'] for r in left['segments'] + current['segments']) == Counter(name for _, _, name in markers)
        old_ids = {r['speaker_local_id'] for r in left['segments']}
        new_ids = {r['speaker_local_id'] for r in current['segments']}
        assert old_ids.isdisjoint(new_ids)
        for voice in (1, 2):
            assert any(t['speaker_local_id'].endswith(f':speaker_{voice}') and t['start'] <= .25 < t['end']
                       for t in current['speaker_turns'])
        replay_frames = sum(max(0, min(b, s) - a) for a, b in (r['frames'] for r in h.native_reads))
        forward_frames = sum(max(0, b - max(a, s)) for a, b in (r['frames'] for r in h.native_reads))
        assert replay_frames == 339200 and forward_frames >= 30 * RATE
        report = {'kind': 'reset_boundary', 'seam_seconds': seam_seconds, 'synthetic': True,
                  'prior_tail_and_left_output': 'seeded synthetic; prior reads NOT observed',
                  'seeded_left_owned_frames': [s - 29 * RATE, s],
                  'acoustic_continuity_tested': False, 'optional_gap': 'v2 cross-epoch composition rejected',
                  'observed_new_epoch_reads': h.native_reads, 'preroll_frames': replay_frames,
                  'forward_frames': forward_frames, 'primary_right_owned_frames': [s, s + 30 * RATE],
                  'old_new_local_ids_equal': False, 'previous_epoch_lookahead_accounted': False}
        (tmp_path / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({k: report[k] for k in ('kind', 'seam_seconds', 'optional_gap', 'preroll_frames', 'forward_frames')}))
    finally:coordinator.close()
