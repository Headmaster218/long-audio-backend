"""Model-free internal runtime tests. Synthetic plans are NOT matcher evidence."""
from dataclasses import replace
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import wave

import pytest

from audio_backend.fragment_core import (
    Alias, Seam, Source, SourceChanged, SourcePlan, Span, UnsupportedAudio, inspect_pcm16_source,
)
from audio_backend.fragment_matcher import BoundaryEvidence, Continuity
from audio_backend.fragment_runtime import LogicalPCMReader, LogicalRunner
from audio_backend.fragment_runtime_contracts import (
    CORE, EPOCH, PREROLL, RATE, CorruptLedger, NativeStatus, RuntimePolicy, StaleWork,
    UnsupportedDiscontinuity, control_id, digest, native_key, next_window, prefix_hash, window_id,
)
from audio_backend.fragment_runtime_store import RuntimeStore

CONTINUOUS = Continuity('continuous', same_capture=True, replay_possible=False)


def wav(path, pcm, rate=RATE, channels=1):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(pcm)
    return path


def plan_for(sources, *, final=True, trims=None, relation='continuous'):
    """Construct already-decided synthetic source plans without matcher calls."""
    sources = tuple(sources)
    first = sources[0]
    spans = [Span(first.fragment_id, 0, first.frames, 0, first.frames, 0)]
    aliases, seams = [], []
    cursor, island = first.frames, 0
    for i, source in enumerate(sources[1:], 1):
        trim = (trims or {}).get(i, 0)
        evidence = BoundaryEvidence('trim', 'unique_informative_exact_overlap', trim_frames=trim,
                                    candidate_frames=(trim,)) if trim else BoundaryEvidence('abstain', 'no_exact_overlap_evidence')
        declaration = CONTINUOUS if relation == 'continuous' else (
            Continuity('gap', gap_frames=17) if relation == 'gap' else Continuity('unknown'))
        status = 'verified_overlap' if trim else 'declared_continuity_only'
        if relation != 'continuous':
            status = 'declared_gap' if relation == 'gap' else 'unknown_continuity'
        seams.append(Seam(sources[i - 1].fragment_id, source.fragment_id, declaration, evidence, cursor, status))
        if relation != 'continuous':
            cursor += declaration.gap_frames or 0
            island += 1
        if trim:
            aliases.append(Alias(source.fragment_id, 0, trim, cursor - trim, cursor, island))
        if source.frames > trim:
            spans.append(Span(source.fragment_id, trim, source.frames, cursor, cursor + source.frames - trim, island))
            cursor += source.frames - trim
    return SourcePlan(sources, tuple(spans), tuple(aliases), tuple(seams), final).validate()


@pytest.fixture(scope='session')
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp('shared_runtime_pcm')
    # 65 s + one sample, nonperiodic deterministic ramp, small fixture only.
    frames = 65 * RATE + 1
    pcm = b''.join(struct.pack('<h', ((i * 7919 + i // 97) % 30001) - 15000) for i in range(frames))
    paths = {'whole': wav(root / 'whole.wav', pcm)}
    cuts = (0, 28 * RATE + 5520, 43 * RATE + 3472, frames)
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        paths[str(i)] = wav(root / f'part{i}.wav', pcm[2*a:2*b])
    paths['alias'] = wav(root / 'alias.wav', pcm[2*(cuts[1]-RATE):2*cuts[1]])
    paths['alias2'] = wav(root / 'alias2.wav', pcm[2*(cuts[1]-RATE):2*cuts[1]])
    paths['short'] = wav(root / 'short.wav', pcm[:3200])
    paths['one'] = wav(root / 'one.wav', pcm[:2])
    paths['grid'] = wav(root / 'grid.wav', pcm[:2*CORE])
    paths['grid_context'] = wav(root / 'grid_context.wav', pcm[:2*(CORE+2*RATE)])
    paths['tail'] = wav(root / 'tail.wav', pcm[:2*(CORE+1)])
    sources = {key: inspect_pcm16_source(key, path) for key, path in paths.items()}
    return root, pcm, paths, sources


class FakeNative:
    fingerprint = 'injected-native-v1'

    def __init__(self, log, turns=(), lag=0, quantize=1, bad_finish=False):
        self.log, self.turns, self.lag, self.quantize, self.bad_finish = log, turns, lag, quantize, bad_finish
        self.finished = False

    def reset(self, key, start):
        self.key, self.start, self.end = key, start, start
        self.log.append(('reset', key, start))

    def push(self, pcm):
        assert not self.finished
        assert pcm.start == self.end
        self.end = pcm.end
        self.log.append(('push', self.key, pcm.start, pcm.end, pcm.pcm_sha256))

    def status(self):
        stable = self.end if self.finished else max(self.start, self.end - self.lag)
        activity = max(self.start, self.end - self.end % self.quantize)
        turns = tuple({**t, 'start_frame': max(t['start_frame'], self.start),
                       'end_frame': min(t['end_frame'], activity)} for t in self.turns
                      if max(t['start_frame'], self.start) < min(t['end_frame'], activity))
        return NativeStatus(self.end, stable, activity, self.finished, turns)

    def finish(self, control):
        assert not self.finished, 'Runner must cache same-process finish acknowledgement'
        self.log.append(('finish', self.key, control))
        if self.bad_finish:
            raise RuntimeError('Injected native finish failure')
        self.finished = True
        return self.status()


class FakeAnalyzer:
    fingerprint = 'injected-analyzer-v1'

    def __init__(self, units=(), events=()):
        self.units, self.events, self.reads = units, events, []

    def analyze(self, pcm, native):
        self.reads.append((pcm.start, pcm.end, pcm.pcm_sha256, pcm.map_sha256))
        tracks = {}
        for unit in self.units:
            if pcm.start <= unit['a'] and unit['b'] <= pcm.end:
                tracks.setdefault(unit.get('track', 'a'), []).append({
                    'start': (unit['a'] - pcm.start) / RATE, 'end': (unit['b'] - pcm.start) / RATE,
                    'ownership_anchor': (unit['anchor'] - pcm.start) / RATE,
                    'text': unit['text'], 'unit_kind': 'native_word',
                })
        events = [{**e, 'start_frame': max(e['start_frame'], pcm.start),
                   'end_frame': min(e['end_frame'], pcm.end)} for e in self.events
                  if max(e['start_frame'], pcm.start) < min(e['end_frame'], pcm.end)]
        return {'segments': [{'source_track_id': track, '_units': units} for track, units in tracks.items()],
                'events': events}


def setup(tmp_path, media, key='short', final=True, policy=None, units=(), events=(), **native_args):
    _, _, paths, sources = media
    plan = plan_for([sources[key]], final=final)
    store = RuntimeStore(tmp_path / 'ledger.sqlite')
    store.create('run', plan, policy or RuntimePolicy())
    log = []
    analyzer = FakeAnalyzer(units, events)
    factory = lambda: FakeNative(log, **native_args)
    return store, LogicalRunner(store, 'run', paths, analyzer, factory), log


def drain(runner):
    for _ in range(200):
        status = runner.step()
        if status != 'committed':
            return status
    raise AssertionError('Unbounded scheduler loop')


def crash_at(point):
    def fault(actual):
        if actual == point:
            raise RuntimeError('Injected crash at ' + point)
    return fault


def test_reference_fragmented_pcm_grid_and_whole_lexical_ownership(tmp_path, media):
    _, reference, paths, sources = media
    units = [dict(a=28*RATE, b=29*RATE, anchor=28*RATE+8000, text='individual'),
             dict(a=30*RATE-2000, b=30*RATE+2000, anchor=30*RATE, text='boundary'),
             dict(a=31*RATE, b=31*RATE+1000, anchor=31*RATE+500, text='出出'),
             dict(a=31*RATE+1100, b=31*RATE+2100, anchor=31*RATE+1600, text='出出'),
             dict(a=32*RATE, b=32*RATE+1000, anchor=32*RATE+500, text='same', track='a'),
             dict(a=32*RATE, b=32*RATE+1000, anchor=32*RATE+500, text='same', track='b')]
    outputs = []
    for arm, sequence in [('whole', ['whole']), ('split', ['0', '1', '2'])]:
        store = RuntimeStore(tmp_path / (arm + '.sqlite'))
        plan = plan_for([sources[key] for key in sequence])
        store.create('run', plan)
        analyzer, log = FakeAnalyzer(units), []
        runner = LogicalRunner(store, 'run', paths, analyzer, lambda: FakeNative(log))
        assert drain(runner) == 'completed'
        rows = store.commits('run')
        assert [(r['window']['core_start'], r['window']['core_end']) for r in rows] == [
            (0, CORE), (CORE, 2*CORE), (2*CORE, len(reference)//2)]
        assert len([x for x in log if x[0] == 'finish']) == 1
        outputs.append((rows, analyzer.reads, log))
    whole, split = outputs
    assert [(a, b, sha) for a, b, sha, _ in whole[1]] == [(a, b, sha) for a, b, sha, _ in split[1]]
    assert [(x[2], x[3], x[4]) for x in whole[2] if x[0] == 'push'] == [
        (x[2], x[3], x[4]) for x in split[2] if x[0] == 'push']
    assert [r['window_id'] for r in whole[0]] == [r['window_id'] for r in split[0]]
    assert [r['segments'] for r in whole[0]] == [r['segments'] for r in split[0]]
    segments = [s for r in split[0] for s in r['segments']]
    assert sum(s['text'].count('individual') for s in segments) == 1
    assert any(s['start'] == 28 and s['end'] == 29 for s in segments)
    assert all('boundary' not in s['text'] for s in split[0][0]['segments'])
    assert sum(s['text'].count('出出') for s in segments) == 2
    assert sum(s['text'].count('same') for s in segments) == 2


@pytest.mark.parametrize('key', ['short', 'one', 'tail', 'grid'])
@pytest.mark.parametrize('context', [0, 2*RATE])
def test_short_tails_exact_sample_and_no_padding(tmp_path, media, key, context):
    store, runner, _ = setup(tmp_path, media, key, policy=RuntimePolicy(context_frames=context), quantize=160)
    assert drain(runner) == 'completed'
    snapshot = store.snapshot('run')
    assert snapshot.next_frame == media[3][key].frames
    assert snapshot.control['acknowledged'] == 1
    assert sum(r['window']['core_end'] - r['window']['core_start'] for r in store.commits('run')) == snapshot.next_frame
    assert all(r['window']['read_end'] <= snapshot.next_frame for r in store.commits('run'))
    if key == 'one':
        assert snapshot.checkpoint['activity_until'] == 0  # exact input receipt, no invented activity


def test_live_tail_wait_append_wake_and_fixed_grid(tmp_path, media):
    _, _, paths, sources = media
    store = RuntimeStore(tmp_path / 'ledger.sqlite')
    first = plan_for([sources['0']], final=False)
    store.create('run', first)
    runner = LogicalRunner(store, 'run', paths, FakeAnalyzer(), lambda: FakeNative([]))
    assert runner.step() == 'waiting'
    token = (1, 0)
    second = plan_for([sources['0'], sources['1']], final=False)
    store.append('run', second, expected_revision=1, operation_id='second')
    assert store.changed('run', token)
    assert runner.step() == 'committed'
    assert store.snapshot('run').next_frame == CORE
    assert runner.reader.verified_sources == 2
    assert runner.step() == 'waiting'
    final = plan_for([sources[str(i)] for i in range(3)])
    store.append('run', final, expected_revision=2, operation_id='last')
    assert drain(runner) == 'completed'
    assert runner.reader.verified_sources == 3
    assert len(store.commits('run')) == 3


def test_alias_only_append_chain_idempotence_and_no_pcm_change(tmp_path, media):
    _, pcm, paths, sources = media
    plan = plan_for([sources['0']], final=False)
    store = RuntimeStore(tmp_path / 'ledger.sqlite')
    store.create('run', plan, RuntimePolicy(context_frames=0))
    runner = LogicalRunner(store, 'run', paths, FakeAnalyzer(), lambda: FakeNative([]))
    assert runner.step() == 'waiting'
    before = LogicalPCMReader(plan, paths).read(0, plan.end_frame)
    aliased = plan_for([sources['0'], sources['alias'], sources['alias2']], final=False, trims={1: RATE, 2: RATE})
    after = store.append('run', aliased, expected_revision=1, operation_id='aliases')
    assert after.plan.end_frame == plan.end_frame and after.revision == 2
    assert store.changed('run', (1, 0))
    assert store.append('run', aliased, expected_revision=1, operation_id='aliases').revision == 2
    assert runner.step() == 'waiting'
    same = runner.reader.read(0, plan.end_frame)
    assert same.pcm_sha256 == before.pcm_sha256 and same.map_sha256 == before.map_sha256
    assert same.pcm == pcm[:2*plan.end_frame]
    final = replace(aliased, final=True)
    store.append('run', final, expected_revision=2, operation_id='final')
    assert drain(runner) == 'completed'
    assert len(store.commits('run')) == 1
    assert len(store.snapshot('run').plan.aliases) == 2


@pytest.mark.parametrize('point', ['create.before_plan', 'create.after_plan', 'plan.after_intent',
                                   'create.before_commit', 'create.after_commit'])
def test_create_crash_atomic_plan_and_intent(tmp_path, media, point):
    store = RuntimeStore(tmp_path / 'ledger.sqlite', crash_at(point))
    plan = plan_for([media[3]['short']])
    with pytest.raises(RuntimeError, match='Injected crash'):
        store.create('run', plan)
    reopened = RuntimeStore(store.path)
    if point != 'create.after_commit':
        with pytest.raises(KeyError):
            reopened.snapshot('run')
    result = reopened.create('run', plan)
    assert result.revision == 1 and result.next_frame == 0 and result.control


@pytest.mark.parametrize('point', ['append.after_plan', 'plan.after_intent', 'append.after_state',
                                   'append.before_commit', 'append.after_commit'])
def test_append_final_crash_atomic_revision_and_intent(tmp_path, media, point):
    store, _, _ = setup(tmp_path, media, final=False)
    final = replace(store.snapshot('run').plan, final=True)
    store.fault = crash_at(point)
    with pytest.raises(RuntimeError, match='Injected crash'):
        store.append('run', final, expected_revision=1, operation_id='final')
    reopened = RuntimeStore(store.path)
    result = reopened.snapshot('run')
    assert result.revision == (2 if point == 'append.after_commit' else 1)
    assert bool(result.control) == (point == 'append.after_commit')
    result = reopened.append('run', final, expected_revision=1, operation_id='final')
    assert result.revision == 2 and result.control


@pytest.mark.parametrize('point', ['core.after_read', 'core.after_analysis', 'native.after_push',
                                   'finish.before_request', 'finish.after_request', 'finish.after_response',
                                   'core.after_output', 'ack.after_control', 'core.after_cursor',
                                   'core.before_commit', 'core.after_commit'])
def test_output_finish_crash_reopen_no_duplicate_publication(tmp_path, media, point):
    unit = dict(a=0, b=1000, anchor=500, text='word')
    store, runner, log = setup(tmp_path, media, units=[unit])
    runner.fault = crash_at(point)
    store.fault = crash_at(point)
    with pytest.raises(RuntimeError, match='Injected crash'):
        runner.step()
    reopened = RuntimeStore(store.path)
    snap = reopened.snapshot('run')
    committed = point == 'core.after_commit'
    assert snap.next_frame == (1600 if committed else 0)
    assert bool(snap.tail) == committed
    assert bool(snap.control['acknowledged']) == committed
    assert len(reopened.commits('run')) == int(committed)
    fresh = LogicalRunner(reopened, 'run', media[2], FakeAnalyzer([unit]), lambda: FakeNative(log))
    assert fresh.step() == 'completed'
    rows = reopened.commits('run')
    assert len(rows) == 1 and rows[0]['segments'][0]['text'] == 'word'
    assert len({r['window_id'] for r in rows}) == 1
    assert fresh.step() == 'completed'


@pytest.mark.parametrize('point', ['finish.before_request', 'finish.after_request', 'finish.after_response',
                                   'ack.after_control', 'empty_ack.after_state',
                                   'empty_ack.before_commit', 'empty_ack.after_commit'])
def test_zero_new_sample_eos_crash_and_missing_process_replay(tmp_path, media, point):
    store, runner, log = setup(tmp_path, media, key='grid', final=False, policy=RuntimePolicy(context_frames=0))
    assert runner.step() == 'committed'
    assert runner.step() == 'waiting'
    before = store.commits('run')
    final = replace(store.snapshot('run').plan, final=True)
    store.append('run', final, expected_revision=1, operation_id='final')
    runner = LogicalRunner(store, 'run', media[2], FakeAnalyzer(), lambda: FakeNative(log), crash_at(point))
    store.fault = crash_at(point)
    with pytest.raises(RuntimeError, match='Injected crash'):
        runner.step()
    reopened = RuntimeStore(store.path)
    assert len(reopened.commits('run')) == 1
    assert reopened.snapshot('run').control['acknowledged'] == (point == 'empty_ack.after_commit')
    runner = LogicalRunner(reopened, 'run', media[2], FakeAnalyzer(), lambda: FakeNative(log))
    assert runner.step() == 'completed'
    assert reopened.commits('run') == before
    assert len([x for x in log if x[0] == 'push']) >= 2  # restore actual final epoch, not a fake ack


def test_append_during_analysis_rejects_stale_work_and_keeps_native_identity(tmp_path, media):
    store, runner, log = setup(tmp_path, media, key='grid_context', final=False)
    final = replace(store.snapshot('run').plan, final=True)
    def race(point):
        if point == 'core.after_analysis':
            store.append('run', final, expected_revision=1, operation_id='final')
    runner.fault = race
    with pytest.raises(StaleWork):
        runner.step()
    assert store.snapshot('run').next_frame == 0 and not store.commits('run')
    runner.fault = lambda point: None
    assert drain(runner) == 'completed'
    resets = [x for x in log if x[0] == 'reset']
    assert resets[0][1] == resets[1][1]
    assert len(store.commits('run')) == 2


def test_wait_registration_has_no_lost_append_wakeup(tmp_path, media):
    store, runner, _ = setup(tmp_path, media, final=False)
    def race(point):
        if point == 'wait.before_register':
            store.append('run', replace(store.snapshot('run').plan, final=True),
                         expected_revision=1, operation_id='final')
    runner.fault = race
    with pytest.raises(StaleWork):
        runner.step()
    assert store.snapshot('run').status == 'running'
    runner.fault = lambda point: None
    assert runner.step() == 'completed'


def test_cancel_during_analysis_prevents_publication_without_new_eos(tmp_path, media):
    store, runner, log = setup(tmp_path, media, key='grid_context', final=False)
    runner.fault = lambda point: store.cancel('run') if point == 'core.after_analysis' else None
    with pytest.raises(StaleWork):
        runner.step()
    snapshot = store.snapshot('run')
    assert snapshot.status == 'cancelled' and snapshot.control is None
    assert not store.commits('run') and not [x for x in log if x[0] == 'finish']
    assert runner.step() == 'cancelled'


@pytest.mark.parametrize('kind', ['mutate', 'delete', 'truncate', 'replace'])
def test_source_mutation_between_analysis_and_commit_is_rejected(tmp_path, media, kind):
    pcm = media[1][:3200]
    path = wav(tmp_path / 'source.wav', pcm)
    source = inspect_pcm16_source('source', path)
    plan = plan_for([source])
    store = RuntimeStore(tmp_path / 'ledger.sqlite')
    store.create('run', plan)
    def mutate(point):
        if point != 'core.after_analysis':
            return
        if kind == 'delete':
            path.unlink()
        elif kind == 'replace':
            wav(tmp_path / 'new.wav', pcm).replace(path)
        elif kind == 'truncate':
            path.write_bytes(path.read_bytes()[:-2])
        else:
            data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(data)
    runner = LogicalRunner(store, 'run', {'source': path}, FakeAnalyzer(), lambda: FakeNative([]), mutate)
    with pytest.raises(SourceChanged):
        runner.step()
    assert store.snapshot('run').next_frame == 0 and not store.commits('run')


@pytest.mark.parametrize('relation', ['gap', 'unknown'])
def test_discontinuity_rejected_before_any_provider(tmp_path, media, relation):
    plan = plan_for([media[3]['0'], media[3]['1']], relation=relation)
    store = RuntimeStore(tmp_path / 'ledger.sqlite')
    with pytest.raises(UnsupportedDiscontinuity):
        store.create('run', plan)
    with pytest.raises(KeyError):
        store.snapshot('run')


@pytest.mark.parametrize('rate,channels', [(8000, 1), (44100, 1), (48000, 1), (16000, 2)])
def test_unsupported_formats_fail_explicitly(tmp_path, rate, channels):
    source = Source('fake', '0'*64, rate, channels, 100)
    with pytest.raises(UnsupportedAudio):
        RuntimeStore(tmp_path / 'ledger.sqlite').create('run', plan_for([source]))


def test_two_speakers_and_event_interval_unions(tmp_path, media):
    # Annotation-shaped fake intervals only; no real recording or inference.
    turns = [dict(start_frame=0, end_frame=75520, speaker='a', instance_id='a1'),
             dict(start_frame=48320, end_frame=176480, speaker='b', instance_id='b1')]
    events = [dict(start_frame=39680, end_frame=82176, event='laughter', instance_id='l1'),
              dict(start_frame=CORE-1000, end_frame=2*CORE+1000, event='long', instance_id='l2')]
    store, runner, _ = setup(tmp_path, media, key='whole', events=events, turns=turns)
    assert drain(runner) == 'completed'
    rows = store.commits('run')
    a = [t for r in rows for t in r['turns'] if t['speaker'] == 'a']
    b = [t for r in rows for t in r['turns'] if t['speaker'] == 'b']
    assert sum(t['end_frame']-t['start_frame'] for t in a) == 75520  # 4.72 s
    assert sum(t['end_frame']-t['start_frame'] for t in b) == 128160  # 8.01 s
    assert sum(max(0, min(x['end_frame'], y['end_frame'])-max(x['start_frame'], y['start_frame']))
               for x in a for y in b) == 27200  # 1.70 s
    laughter = [e for r in rows for e in r['events'] if e['instance_id'] == 'l1']
    assert sum(e['end_frame']-e['start_frame'] for e in laughter) == 42496
    ongoing = [e for r in rows for e in r['events'] if e['instance_id'] == 'l2']
    assert len(ongoing) == 3 and len({e['instance_id'] for e in ongoing}) == 1
    assert sum(e['end_frame']-e['start_frame'] for e in ongoing) == CORE+2000


def test_native_finish_failure_keeps_intent_pending(tmp_path, media):
    store, runner, _ = setup(tmp_path, media, bad_finish=True)
    with pytest.raises(RuntimeError, match='finish failure'):
        runner.step()
    assert store.snapshot('run').control['acknowledged'] == 0
    assert store.snapshot('run').next_frame == 0 and not store.commits('run')


def test_native_live_frontier_waits_without_finish(tmp_path, media):
    store, runner, log = setup(tmp_path, media, key='grid_context', final=False, lag=3*RATE)
    assert runner.step() == 'waiting_native'
    assert not store.commits('run') and not [x for x in log if x[0] == 'finish']
    store.append('run', replace(store.snapshot('run').plan, final=True), expected_revision=1, operation_id='final')
    # Durable EOS releases this previously unstable core before terminal
    # ownership, without prematurely acknowledging the output/control ledger.
    assert runner.step() == 'committed'
    assert store.snapshot('run').control['acknowledged'] == 0
    assert runner.step() == 'completed'
    assert len([x for x in log if x[0] == 'finish']) == 1


def test_sql_is_authoritative_and_detects_state_or_payload_tampering(tmp_path, media):
    store, runner, _ = setup(tmp_path, media)
    assert runner.step() == 'completed'
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute("UPDATE fragment_plans SET plan='{}'")
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute("UPDATE fragment_core_commits SET payload='{}'")
        db.execute("UPDATE fragment_runs SET checkpoint='{}'")
    with pytest.raises(CorruptLedger, match='state hash'):
        store.snapshot('run')


def test_accepted_prefix_mutation_and_final_extension_rejected(tmp_path, media):
    store, _, _ = setup(tmp_path, media, final=False)
    original = store.snapshot('run').plan
    altered = plan_for([replace(original.sources[0], sha256='f'*64)], final=False)
    with pytest.raises(ValueError, match='rewrites'):
        store.append('run', altered, expected_revision=1, operation_id='bad')
    store.append('run', replace(original, final=True), expected_revision=1, operation_id='final')
    with pytest.raises(ValueError, match='true EOS'):
        store.append('run', plan_for([*original.sources, media[3]['one']]),
                     expected_revision=2, operation_id='late')


@pytest.mark.parametrize('end', [EPOCH, 12*EPOCH])
def test_exact_epoch_and_hour_eos_targets_last_actual_sample_without_audio_fixture(tmp_path, end):
    # Metadata/control math only. Actual bounded replay is covered separately.
    plan = plan_for([Source('fake', '0'*64, RATE, 1, end)])
    store = RuntimeStore(tmp_path / 'ledger.sqlite')
    snap = store.create('run', plan)
    assert snap.control['epoch'] == end//EPOCH-1
    assert snap.control['final_frame'] == end


@pytest.fixture(scope='session')
def epoch_media(tmp_path_factory):
    root = tmp_path_factory.mktemp('epoch_runtime_pcm')
    end = EPOCH + CORE
    cuts = [0, EPOCH-1234, EPOCH+5678, end]
    # Streaming generation keeps construction well below memory/disk limits.
    block = bytes(range(256))*125
    paths, sources = {}, {}
    for key, a, b in [('whole_epoch', 0, end), *[(f'epoch{i}', a, b) for i, (a, b) in enumerate(zip(cuts, cuts[1:]))]]:
        path = root / (key+'.wav')
        with wave.open(str(path), 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(RATE)
            for start in range(a, b, RATE):
                count = min(RATE, b-start)
                # Continuous sample-byte phase even at non-grid source cuts.
                offset = (start*2) % len(block)
                raw = (block+block)[offset:offset+count*2]
                out.writeframesraw(raw)
        paths[key] = path
        sources[key] = inspect_pcm16_source(key, path)
    return paths, sources


def test_epoch_cross_file_replay_bounds_and_restart(tmp_path, epoch_media):
    paths, sources = epoch_media
    outputs = []
    for arm, keys in [('whole', ['whole_epoch']), ('split', ['epoch0', 'epoch1', 'epoch2'])]:
        store = RuntimeStore(tmp_path / (arm+'.sqlite'))
        store.create('run', plan_for([sources[key] for key in keys]))
        log = []
        runner = LogicalRunner(store, 'run', paths, FakeAnalyzer(), lambda: FakeNative(log))
        assert runner.step() == 'committed'
        # Resume from SQL at a 30 s cursor: same native epoch identity and bytes.
        first_pushes = [row for row in log if row[0] == 'push']
        runner = LogicalRunner(RuntimeStore(store.path), 'run', paths, FakeAnalyzer(), lambda: FakeNative(log))
        assert drain(runner) == 'completed'
        reset0 = [row for row in log if row[0] == 'reset' and row[1][2] == 0]
        reset1 = [row for row in log if row[0] == 'reset' and row[1][2] == 1]
        assert len(reset0) == 2 and reset0[0] == reset0[1]
        assert len(reset1) == 1 and reset1[0][2] == EPOCH-PREROLL
        after_restart = [row for row in log if row[0] == 'push'][len(first_pushes):2*len(first_pushes)]
        assert after_restart == first_pushes
        assert all(row[3]-row[2] <= 60*RATE for row in log if row[0] == 'push')
        assert max(row[3]-max(0, row[1][2]*EPOCH-PREROLL) for row in log if row[0] == 'push') <= 360*RATE
        assert [row[1][2] for row in log if row[0] == 'finish'] == [1]
        rows = store.commits('run')
        assert [r['window']['core_start'] for r in rows] == list(range(0, EPOCH+1, CORE))
        outputs.append(rows)
    assert [r['pcm_hash'] for r in outputs[0]] == [r['pcm_hash'] for r in outputs[1]]
    assert [r['window_id'] for r in outputs[0]] == [r['window_id'] for r in outputs[1]]


@pytest.mark.parametrize('recovery', [True, False])
def test_lexical_jitter_tail_survives_database_restart(tmp_path, media, recovery):
    if recovery:
        earlier = [(28.8,29.2,'first'),(29.7,30.7,' second')]
        later = [(28.6,29.0,'first'),(29.4,30.0,' second'),(30.4,31.0,' third')]
        expected = ['first', 'second third']
    else:
        earlier = [(29.2,29.8,'first'),(30.3,30.8,' second')]
        later = [(29.8,30.6,'first'),(30.7,31.2,' second')]
        expected = ['first', 'second']
    def units(words):
        return [dict(a=round(a*RATE), b=round(b*RATE), anchor=round((a+b)*RATE/2), text=text)
                for a,b,text in words]
    store, runner, log = setup(tmp_path, media, key='grid_context', units=units(earlier))
    assert runner.step() == 'committed'
    prior = store.snapshot('run').tail
    assert prior
    runner = LogicalRunner(RuntimeStore(store.path), 'run', media[2], FakeAnalyzer(units(later)), lambda: FakeNative(log))
    assert runner.step() == 'completed'
    rows = store.commits('run')
    assert [' '.join(s['text'] for s in r['segments']) for r in rows] == expected
    if recovery:
        assert rows[1]['segments'][0]['start'] < 30  # intentional alignment transfer, whole native interval


def test_exact_same_core_concurrent_race_has_one_commit(tmp_path, media):
    store, first, _ = setup(tmp_path, media)
    second = LogicalRunner(RuntimeStore(store.path), 'run', media[2], FakeAnalyzer(), lambda: FakeNative([]))
    first.fault = lambda point: second.step() if point == 'core.after_analysis' else None
    with pytest.raises(StaleWork):
        first.step()
    assert store.snapshot('run').status == 'completed'
    assert len(store.commits('run')) == 1
    assert first.step() == 'completed'


@pytest.mark.parametrize('point', ['wait.after_state', 'wait.before_commit', 'wait.after_commit',
                                   'cancel.after_state', 'cancel.before_commit', 'cancel.after_commit'])
def test_wait_cancel_transaction_boundaries(tmp_path, media, point):
    store, _, _ = setup(tmp_path, media, final=False)
    store.fault = crash_at(point)
    action = (lambda: store.cancel('run')) if point.startswith('cancel') else (lambda: store.register_wait('run', 1, 0))
    with pytest.raises(RuntimeError, match='Injected crash'):
        action()
    snapshot = RuntimeStore(store.path).snapshot('run')
    expected = ('cancelled' if point.startswith('cancel') else 'waiting') if point.endswith('after_commit') else 'running'
    assert snapshot.status == expected and snapshot.control is None and snapshot.next_frame == 0


@pytest.mark.parametrize('field', ['map_hash', 'mapping', 'policy_hash', 'checkpoint', 'tail', 'turns'])
def test_publication_boundary_rejects_forged_envelopes(tmp_path, media, field):
    store, runner, _ = setup(tmp_path, media)
    original_commit = store.commit
    def forged(snapshot, window, payload, tail, checkpoint, response=None):
        payload = copy.deepcopy(payload)
        if field == 'checkpoint':
            checkpoint = {**checkpoint, 'consumed_until': 0}
            payload['checkpoint'] = checkpoint
        elif field == 'tail':
            tail = [{'bogus': True}]
            payload['tail'] = tail
        elif field == 'turns':
            payload['turns'] = [{'bogus': True}]
        elif field == 'mapping':
            payload['mapping'] = []
        else:
            payload[field] = 'f'*64
        return original_commit(snapshot, window, payload, tail, checkpoint, response)
    store.commit = forged
    with pytest.raises(ValueError):
        runner.step()
    assert not store.commits('run') and store.snapshot('run').next_frame == 0


def test_alias_only_final_after_committed_grid_has_no_extra_output(tmp_path, media):
    source = media[3]['grid']
    path = wav(tmp_path / 'alias.wav', media[1][2*(CORE-RATE):2*CORE])
    alias = inspect_pcm16_source('alias_grid', path)
    paths = {**media[2], 'alias_grid': path}
    store = RuntimeStore(tmp_path / 'ledger.sqlite')
    store.create('run', plan_for([source], final=False), RuntimePolicy(context_frames=0))
    log = []
    runner = LogicalRunner(store, 'run', paths, FakeAnalyzer(), lambda: FakeNative(log))
    assert runner.step() == 'committed'
    before = store.commits('run')
    prefix = store.snapshot('run').prefix_hash
    final = plan_for([source, alias], final=True, trims={1: RATE})
    store.append('run', final, expected_revision=1, operation_id='alias-final')
    assert store.snapshot('run').prefix_hash == prefix
    assert runner.step() == 'completed'
    assert store.commits('run') == before
    assert len([row for row in log if row[0] == 'push']) == 1
    assert len([row for row in log if row[0] == 'finish']) == 1


def test_moved_equal_binding_reopens_and_wrong_binding_stops(tmp_path, media):
    plan = plan_for([media[3]['short']])
    moved = tmp_path / 'moved.wav'; moved.write_bytes(media[2]['short'].read_bytes())
    assert LogicalPCMReader(plan, {'short': moved}).read(0, plan.end_frame).pcm == media[1][:3200]
    with pytest.raises(SourceChanged):
        LogicalPCMReader(plan, {'short': media[2]['one']})


def test_too_many_owner_parts_rejected_before_read(tmp_path, media):
    original = media[3]['one']
    sources = [replace(original, fragment_id=f'p{i}') for i in range(129)]
    reader = LogicalPCMReader(plan_for(sources), {source.fragment_id: media[2]['one'] for source in sources})
    with pytest.raises(ValueError, match='128 owner-part'):
        reader.read(0, 129)


def test_invalid_finished_consumption_and_fingerprints_fail_closed(tmp_path, media):
    store, runner, _ = setup(tmp_path, media)
    class InvalidNative(FakeNative):
        def finish(self, control):
            self.finished = True
            return NativeStatus(self.end-1, self.end-1, self.end-1, True)
    runner.native_factory = lambda: InvalidNative([])
    with pytest.raises(ValueError, match='acknowledgement'):
        runner.step()
    assert store.snapshot('run').control['acknowledged'] == 0
    class DifferentNative(FakeNative):
        fingerprint = 'different'
    runner.native_factory = lambda: DifferentNative([])
    with pytest.raises(ValueError, match='fingerprint'):
        runner.step()
    assert not store.commits('run')


def test_native_stability_uses_available_bounded_lookahead(tmp_path, epoch_media):
    paths, sources = epoch_media
    store = RuntimeStore(tmp_path / 'ledger.sqlite')
    store.create('run', plan_for([sources['whole_epoch']], final=False))
    log = []
    runner = LogicalRunner(store, 'run', paths, FakeAnalyzer(), lambda: FakeNative(log, lag=3*RATE))
    for _ in range(10):
        assert runner.step() == 'committed'
    assert store.snapshot('run').next_frame == EPOCH
    pushes = [row for row in log if row[0] == 'push' and row[1][2] == 0]
    assert pushes[-1][3] > EPOCH+2*RATE
    assert pushes[-1][3] <= 360*RATE
    assert not [row for row in log if row[0] == 'finish']


def test_unreleasable_final_frontier_fails_instead_of_false_epoch_finish(tmp_path, epoch_media):
    paths, sources = epoch_media
    store = RuntimeStore(tmp_path / 'ledger.sqlite')
    store.create('run', plan_for([sources['whole_epoch']]))
    log = []
    runner = LogicalRunner(store, 'run', paths, FakeAnalyzer(), lambda: FakeNative(log, lag=400*RATE))
    with pytest.raises(ValueError, match='Unsupported final native frontier'):
        runner.step()
    assert not [row for row in log if row[0] == 'finish']
    assert store.snapshot('run').next_frame == 0
    assert store.snapshot('run').control['acknowledged'] == 0


@pytest.mark.parametrize('field', ['plan', 'policy', 'tail', 'checkpoint', 'prefix_hash'])
def test_caller_snapshot_cannot_replace_durable_plan_or_state(tmp_path, media, field):
    store, runner, _ = setup(tmp_path, media)
    original_commit = store.commit
    def substituted(snapshot, window, payload, tail, checkpoint, response=None):
        if field == 'plan':
            source = replace(snapshot.plan.sources[0], sha256='f'*64)
            other = plan_for([source])
            snapshot = replace(snapshot, plan=other)
            payload['plan_hash'] = other.sha256
            for mapping in payload['mapping']:
                mapping['source_sha256'] = source.sha256
            payload['map_hash'] = digest(payload['mapping'])
            checkpoint['input_prefix_hash'] = prefix_hash(other, checkpoint['consumed_until'])
        elif field == 'policy':
            policy = replace(snapshot.policy, analyzer_fingerprint='substitute')
            snapshot = replace(snapshot, policy=policy)
            payload['policy_hash'] = policy.sha256
            payload['window_id'] = window_id(snapshot.run_id, window, policy)
        else:
            snapshot = replace(snapshot, **{field: ['forged'] if field == 'tail' else (
                {'forged': True} if field == 'checkpoint' else 'f'*64)})
        return original_commit(snapshot, window, payload, tail, checkpoint, response)
    store.commit = substituted
    with pytest.raises((StaleWork, ValueError)):
        runner.step()
    assert not store.commits('run') and store.snapshot('run').next_frame == 0


def test_owned_native_units_keep_individual_intervals_and_kind(tmp_path, media):
    units = [dict(a=0, b=200, anchor=100, text='word'), dict(a=300, b=500, anchor=400, text=' phrase')]
    store, runner, _ = setup(tmp_path, media, units=units)
    original = runner.analyzer.analyze
    def phrase(pcm, native):
        result = original(pcm, native)
        result['segments'][0]['_units'][1]['unit_kind'] = 'native_phrase'
        return result
    runner.analyzer.analyze = phrase
    assert runner.step() == 'completed'
    segments = store.commits('run')[0]['segments']
    assert [(s['start'], s['end'], s['ownership_anchor'], s['native_unit_kind']) for s in segments] == [
        (0, 200/RATE, 100/RATE, 'native_word'), (300/RATE, 500/RATE, 400/RATE, 'native_phrase')]


@pytest.mark.parametrize('frames,context', [(CORE+1, 1), (CORE+18, 2*RATE)])
def test_exact_frame_support_survives_float_rebase(tmp_path, frames, context):
    path = wav(tmp_path / 'source.wav', b'\x01\x00'*frames)
    source = inspect_pcm16_source('source', path)
    store = RuntimeStore(tmp_path / 'ledger.sqlite')
    store.create('run', plan_for([source]), RuntimePolicy(context_frames=context))
    analyzer = FakeAnalyzer([dict(a=CORE, b=frames, anchor=CORE, text='tail')])
    runner = LogicalRunner(store, 'run', {'source': path}, analyzer, lambda: FakeNative([]))
    assert drain(runner) == 'completed'
    rows = store.commits('run')
    assert sum(s['text'] == 'tail' for row in rows for s in row['segments']) == 1
    assert abs(rows[-1]['segments'][0]['end']-frames/RATE) <= 1e-9


def test_endpoint_anchor_on_one_frame_is_retained_unchanged(tmp_path, media):
    store, runner, _ = setup(tmp_path, media, key='one', units=[dict(a=0, b=1, anchor=1, text='word')])
    assert runner.step() == 'completed'
    segment = store.commits('run')[0]['segments'][0]
    assert (segment['start'], segment['end'], segment['ownership_anchor']) == (0, 1/RATE, 1/RATE)
    assert segment['start'] <= segment['routing_anchor'] < segment['end']
    assert store.snapshot('run').next_frame == 1


@pytest.mark.parametrize('provider', ['analyzer', 'native'])
def test_provider_fingerprint_mutation_during_call_blocks_commit(tmp_path, media, provider):
    store, runner, _ = setup(tmp_path, media)
    if provider == 'analyzer':
        class ChangedAnalyzer(FakeAnalyzer):
            def analyze(self, pcm, native):
                self.fingerprint = 'changed'
                return super().analyze(pcm, native)
        runner.analyzer = ChangedAnalyzer()
    else:
        class ChangedNative(FakeNative):
            def finish(self, control):
                result = super().finish(control)
                self.fingerprint = 'changed'
                return result
        runner.native_factory = lambda: ChangedNative([])
    with pytest.raises(ValueError, match='fingerprint'):
        runner.step()
    assert store.snapshot('run').next_frame == 0 and not store.commits('run')
    assert store.snapshot('run').control['acknowledged'] == 0


def test_publication_tolerance_does_not_allow_outside_support(tmp_path, media):
    store, runner, _ = setup(tmp_path, media, units=[dict(a=0, b=1600, anchor=1, text='word')])
    original = store.commit
    def outside(snapshot, window, payload, tail, checkpoint, response=None):
        payload['segments'][0]['end'] += 1e-8  # 10 ns, deliberately beyond the 1 ns allowance
        return original(snapshot, window, payload, tail, checkpoint, response)
    store.commit = outside
    with pytest.raises(ValueError, match='outside read window'):
        runner.step()
    assert not store.commits('run')


@pytest.mark.parametrize('context', [0, 2*RATE])
def test_ordinary_final_endpoint_retains_original_anchor_and_support(tmp_path, media, context):
    store, runner, _ = setup(tmp_path, media, key='grid', policy=RuntimePolicy(context_frames=context),
                            units=[dict(a=CORE-1000, b=CORE, anchor=CORE, text='end')])
    assert runner.step() == 'completed'
    unit = store.commits('run')[0]['segments'][0]
    assert (unit['start'], unit['end'], unit['ownership_anchor']) == ((CORE-1000)/RATE, 30., 30.)
    assert unit['routing_anchor'] < 30


def test_live_endpoint_owned_before_restart_and_zero_new_sample_eos(tmp_path, media):
    words = [dict(a=CORE-1000, b=CORE, anchor=CORE, text='end')]
    store, runner, log = setup(tmp_path, media, key='grid', final=False,
                               policy=RuntimePolicy(context_frames=0), units=words)
    assert runner.step() == 'committed'
    before = store.commits('run')
    assert [s['text'] for s in before[0]['segments']] == ['end']
    assert before[0]['segments'][0]['ownership_anchor'] == 30
    assert store.snapshot('run').tail[0]['emitted'] is True
    reopened = RuntimeStore(store.path)
    final = replace(reopened.snapshot('run').plan, final=True)
    reopened.append('run', final, expected_revision=1, operation_id='final')
    fresh = LogicalRunner(reopened, 'run', media[2], FakeAnalyzer(words), lambda: FakeNative(log))
    assert fresh.step() == 'completed'
    assert reopened.commits('run') == before
    assert len([row for row in log if row[0] == 'finish']) == 1


def test_support_end_left_route_and_crossing_boundary_right_route_survive_restart(tmp_path, media):
    words = [dict(a=CORE-2000, b=CORE, anchor=CORE, text='left'),
             dict(a=CORE-1000, b=CORE+1000, anchor=CORE, text='right')]
    store, runner, log = setup(tmp_path, media, key='grid_context', units=words)
    assert runner.step() == 'committed'
    fresh = LogicalRunner(RuntimeStore(store.path), 'run', media[2], FakeAnalyzer(words), lambda: FakeNative(log))
    assert fresh.step() == 'completed'
    rows = store.commits('run')
    assert [[s['text'] for s in row['segments']] for row in rows] == [['left'], ['right']]
    assert [s['ownership_anchor'] for row in rows for s in row['segments']] == [30., 30.]
    assert rows[0]['segments'][0]['routing_anchor'] < 30 == rows[1]['segments'][0]['routing_anchor']


@pytest.mark.parametrize('context,tail_frames', [(1, 1), (2*RATE, 18)])
def test_support_end_routing_handles_global_float_roundtrip(tmp_path, context, tail_frames):
    end = CORE+tail_frames
    path = wav(tmp_path/'source.wav', b'\x01\x00'*end)
    source = inspect_pcm16_source('source', path)
    store = RuntimeStore(tmp_path/'ledger.sqlite')
    store.create('run', plan_for([source]), RuntimePolicy(context_frames=context))
    analyzer = FakeAnalyzer([dict(a=CORE, b=end, anchor=end, text='last')])
    runner = LogicalRunner(store, 'run', {'source':path}, analyzer, lambda:FakeNative([]))
    assert drain(runner) == 'completed'
    units = [s for row in store.commits('run') for s in row['segments']]
    assert len(units) == 1 and units[0]['text'] == 'last'
    assert units[0]['ownership_anchor'] == units[0]['end']
    assert units[0]['routing_anchor'] < end/RATE


def test_endpoint_crash_after_output_then_restart_publishes_once(tmp_path, media):
    words = [dict(a=0,b=1,anchor=1,text='last')]
    store, runner, _ = setup(tmp_path, media, key='one', units=words)
    store.fault = crash_at('core.after_output')
    with pytest.raises(RuntimeError, match='Injected crash'):
        runner.step()
    reopened = RuntimeStore(store.path)
    assert not reopened.commits('run')
    runner = LogicalRunner(reopened,'run',media[2],FakeAnalyzer(words),lambda:FakeNative([]))
    assert runner.step() == 'completed'
    assert [s['text'] for row in reopened.commits('run') for s in row['segments']] == ['last']


@pytest.mark.parametrize('count', [2, 257])
def test_tail_preserves_distinct_native_anchors_sharing_one_route(tmp_path, media, count):
    words = [dict(a=0, b=1600, anchor=(.025+i*1e-12)*RATE, text='same') for i in range(count)]
    store, runner, _ = setup(tmp_path, media, units=words)
    assert runner.step() == 'completed'
    tail = store.snapshot('run').tail
    expected = [word['anchor']/RATE for word in words][-256:]
    assert [unit['ownership_anchor'] for unit in tail] == expected
    assert len(set(expected)) == min(count, 256)
    assert {unit['routing_anchor'] for unit in tail} == {.025}
