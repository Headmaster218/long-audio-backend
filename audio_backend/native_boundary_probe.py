"""Isolated P1/P2 mechanics probe. No ASR; importing does not admit inference.

Call run_arm only from a separately approved, frozen and resource-supervised
entrypoint. Inject reviewed NativeBridge/NativeProcess; no scheduler duplication.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
from pathlib import Path
import tempfile
import wave

from .fragment_core import BoundedPCM16Reader, SourcePlan, Span, Seam, inspect_pcm16_source
from .fragment_matcher import Continuity, BoundaryEvidence
from .fragment_runtime import LogicalPCMReader, LogicalRunner
from .fragment_runtime_contracts import CORE, EPOCH, RATE, RuntimePolicy, digest
from .fragment_runtime_store import RuntimeStore

BRIDGE_COMMIT = '957255700a8493f82750f821b65c8a2797326396'
RUNTIME_COMMIT = '39f34e494952595c3882ca0b74a6c6a0bfcee9ab'
SOURCE_SHA = '3e2560b19bee6952c7c7ce041b0f1ea8a7ea9468044c4eea79d2a2c67e24ab0f'
ARMS = {'P1_whole': (480001, (0, 480001)),
        'P1_split_late_eos': (480001, (0, 480000, 480001)),
        'P2_whole': (5280001, (0, 5280001)),
        'P2_split_late_eos_restart': (5280001, (0, 4800000, 5013520, 5280000, 5280001))}
LIMITS = {'P1': {'native_reset': {'calls': 2, 'frames': 0},
                 'native_push': {'calls': 2, 'frames': 960002},
                 'native_finish': {'calls': 2, 'frames': 0},
                 'native_turns': {'calls': 4, 'frames': 0}},
          'P2': {'native_reset': {'calls': 4, 'frames': 0},
                 'native_push': {'calls': 18, 'frames': 12198404},
                 'native_finish': {'calls': 2, 'frames': 0},
                 'native_turns': {'calls': 20, 'frames': 0}}}


class DiagnosticAnalyzer:
    """Empty lexical/event projection; raw all-speaker native evidence retained."""
    fingerprint = 'native-boundary-diagnostic-no-asr-v1'

    def __init__(self):
        self.observations = []

    def bind_context(self, snapshot, window, native):
        self.context = snapshot, window, native

    def analyze(self, pcm, status):
        snapshot, window, native = self.context
        self.observations.append({'run_id': snapshot.run_id, 'window': asdict(window),
                                  'pcm_sha256': pcm.pcm_sha256, 'map_sha256': pcm.map_sha256,
                                  'native': compact_native_snapshot(native)})
        return {'segments': [], 'events': []}


@contextmanager
def materialize(source_path, arm, temp_root, guard, *, expected_sha=SOURCE_SHA):
    """Serial bounded source reads; no complete-recording cache or downloads."""
    end, cuts = ARMS[arm]
    original = inspect_pcm16_source('original', source_path)
    if original.sha256 != expected_sha or original.frames < end:
        raise ValueError('Pinned original AMI source changed or too short')
    reader = BoundedPCM16Reader((original,), {'original': source_path})
    with tempfile.TemporaryDirectory(prefix=arm+'-', dir=temp_root) as directory:
        paths, sources, lineage = {}, [], []
        for i, (start, stop) in enumerate(zip(cuts, cuts[1:])):
            key, path = str(i), Path(directory)/f'{i}.wav'
            guard.check(44+2*(stop-start))
            sha = hashlib.sha256()
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(RATE)
                for a in range(start, stop, 60*RATE):
                    guard.check()
                    pcm = reader.read_source('original', a, min(stop, a+60*RATE))
                    sha.update(pcm); output.writeframesraw(pcm)
            sources.append(inspect_pcm16_source(key, path)); paths[key] = path
            lineage.append({'fragment_id': key, 'original_sha256': original.sha256,
                            'source_start': start, 'source_end': stop,
                            'wav_sha256': sources[-1].sha256, 'pcm_sha256': sha.hexdigest()})
        def plan(count, final=False):
            selected = tuple(sources[:count])
            # Fixture-only externally established contiguous ownership. This is
            # deliberately NOT a blind matcher success and permits no trim.
            spans, seams, cursor = [], [], 0
            for i, source in enumerate(selected):
                spans.append(Span(source.fragment_id, 0, source.frames, cursor, cursor+source.frames, 0))
                if i:
                    seams.append(Seam(selected[i-1].fragment_id, source.fragment_id,
                        Continuity('continuous', same_capture=True, replay_possible=False),
                        BoundaryEvidence('abstain', 'fixture_declared_contiguous_no_matcher_claim'),
                        cursor, 'declared_continuity_only'))
                cursor += source.frames
            value = SourcePlan(selected, tuple(spans), (), tuple(seams), final).validate()
            if value.end_frame != cuts[count]:
                raise ValueError('Fixture lineage differs from declared no-trim ownership')
            return value
        yield {'paths': paths, 'plan': plan, 'lineage': lineage, 'count': len(sources)}


def run_arm(arm, prepared, output, make_factory, guard, *, native_fingerprint=None):
    """make_factory(policy, final_control, evidence_sink) returns NativeFactory.

    Caller owns durable pre-attempt budget reservation, frozen-file verification,
    actual NativeProcess construction and resource/watchdog admission. No retries.
    """
    if arm not in ARMS:
        raise ValueError('Unknown frozen probe arm')
    output = Path(output)
    path = output/(arm+'.sqlite')
    if path.exists():
        raise ValueError('Fresh attempt required; automatic resume/retry forbidden')
    analyzer = DiagnosticAnalyzer()
    policy = RuntimePolicy(native_fingerprint=native_fingerprint or 'boundary-native-'+arm,
                           analyzer_fingerprint=analyzer.fingerprint)
    store = RuntimeStore(path)
    final = prepared['plan'](prepared['count'], True)
    # Compute final intent using the reviewed store, in a private temporary ledger.
    with tempfile.TemporaryDirectory(dir=output) as td:
        intent_store = RuntimeStore(Path(td)/'intent.sqlite')
        final_control = intent_store.create(arm, final, policy).control
    evidence, receipts, restarts = [], [], []
    factory = make_factory(policy, final_control, evidence.append)
    runner = LogicalRunner(store, arm, prepared['paths'], analyzer, factory)
    def record(action):
        s = store.snapshot(arm)
        if s.control is None and factory.current and factory.current.status().finished:
            raise AssertionError('False terminal flush on fragment receipt')
        receipts.append({'action': action, 'revision': s.revision, 'cursor': s.next_frame,
                         'plan_hash': s.plan.sha256, 'control': s.control,
                         'native': compact_native_snapshot(factory.current) if factory.current else None})
    def drain(stop_at=None):
        for _ in range(20):
            guard.check()
            if stop_at is not None and store.snapshot(arm).next_frame == stop_at:
                return
            state = runner.step()
            if state != 'committed':
                record(state); return
        raise RuntimeError('Unexpected scheduler step bound')
    def append(plan, operation):
        s = store.snapshot(arm)
        store.append(arm, plan, expected_revision=s.revision, operation_id=operation)
        # Idempotent same operation must preserve identities and revision.
        again = store.append(arm, plan, expected_revision=s.revision, operation_id=operation)
        if again.revision != s.revision+1:
            raise AssertionError('Non-idempotent durable append')
        record(operation)
    try:
        if arm.endswith('whole'):
            store.create(arm, final, policy)
        else:
            store.create(arm, prepared['plan'](1), policy)
            record('first_fragment_receipt_no_eos')
            if arm.startswith('P2'):
                drain()  # Actual input ending at 300 s, explicitly nonterminal.
            for count in range(2, prepared['count']+1):
                append(prepared['plan'](count), 'fragment-'+str(count))
            drain(stop_at=EPOCH if arm.startswith('P2') else None)
            if arm.startswith('P2'):
                before = store.snapshot(arm)
                if before.next_frame != EPOCH:
                    raise RuntimeError('Blocked: 30 s real lookahead did not stabilize epoch 0')
                committed_ids = [p['window_id'] for p in store.commits(arm)]
                factory.close()
                store = RuntimeStore(path)
                runner = LogicalRunner(store, arm, prepared['paths'], analyzer, factory)
                after = store.snapshot(arm)
                if before != after or committed_ids != [p['window_id'] for p in store.commits(arm)]:
                    raise AssertionError('Durable restart changed publication identities')
                restarts.append({'cursor': after.next_frame, 'epoch': 1,
                                 'expected_replay_start': 4460800, 'window_ids': committed_ids})
            append(final, 'explicit-true-eos')
        drain()
        snapshot, commits = store.snapshot(arm), store.commits(arm)
        if snapshot.status != 'completed' or snapshot.next_frame != ARMS[arm][0]:
            raise RuntimeError('Probe did not reach true terminal acknowledgement')
        expected = [(a, min(a+CORE, final.end_frame)) for a in range(0, final.end_frame, CORE)]
        actual = [(p['window']['core_start'], p['window']['core_end']) for p in commits]
        if actual != expected or actual[-1][1]-actual[-1][0] != 1:
            raise AssertionError('Missing last-sample core or fabricated extra ownership')
        return {'arm': arm, 'quality_claim': 'native mechanics only; no ASR, DER or speaker identity claim',
                'snapshot': asdict(snapshot), 'commits': commits, 'receipts': receipts,
                'restarts': restarts, 'observations': analyzer.observations,
                'native_epochs': evidence, 'source_lineage': prepared['lineage']}
    finally:
        close_preserving_primary(factory)


def compact_native_snapshot(native):
    """Typed current state only; raw cumulative wire history is stored once.

    This is a diagnostic state snapshot, not a hash/reference to an unpersisted
    raw record. Complete terminal epoch wire records have separate file refs.
    """
    status = asdict(native.status())
    turns = status.pop('turns')
    return {'key': list(native.key), 'replay_start': native.replay_start,
            'status': status, 'all_turns_count':len(turns), 'all_turns_sha256':digest(turns),
            'wire_calls_observed': len(native.calls)}


def compact_arm_record(result, output):
    """Immutable per-core hashes reference the durable ledger, never copy raw histories."""
    ledger = Path(output)/(result['arm']+'.sqlite')
    with ledger.open('rb') as stream:
        sha = hashlib.file_digest(stream,'sha256').hexdigest()
    return {**result,
            'commits':[{'window_id':p['window_id'], 'core_start':p['window']['core_start'],
                        'core_end':p['window']['core_end'], 'payload_sha256':digest(p)}
                       for p in result['commits']],
            'core_ledger':{'record_file':ledger.name, 'bytes':ledger.stat().st_size,'sha256':sha}}


def all_speaker_signature(commits):
    """Anonymous exact interval-set comparison independently within each epoch.

    Merge adjacent/overlapping intervals per slot; sort complete activity profiles
    lexicographically, ignoring arbitrary slot names. Equal multisets mean a
    one-to-one anonymous assignment exists, including silent/overlap behavior.
    Never assign globally across native resets. No tolerance or time shift.
    """
    grouped = {}
    for p in commits:
        epoch = p['window']['epoch_index']
        for turn in p['turns']:
            grouped.setdefault(epoch, {}).setdefault(turn['speaker'], []).append(
                (turn['start_frame'], turn['end_frame']))
    result = {}
    for epoch, speakers in grouped.items():
        profiles = []
        for spans in speakers.values():
            merged = []
            for a, b in sorted(spans):
                if merged and a <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
                else:
                    merged.append((a, b))
            profiles.append(tuple(merged))
        result[epoch] = sorted(profiles)
    return result


def compare_pair(whole, split):
    a, b = whole['commits'], split['commits']
    pcm = [(x['window']['core_start'], x['window']['core_end'], x['pcm_hash']) for x in a]
    other = [(x['window']['core_start'], x['window']['core_end'], x['pcm_hash']) for x in b]
    return {'same_core_read_pcm': pcm == other,
            'anonymous_all_speaker_intervals_exact': all_speaker_signature(a) == all_speaker_signature(b),
            'whole_signature': all_speaker_signature(a), 'split_signature': all_speaker_signature(b),
            'tolerance_frames': 0, 'assignment_scope': 'independent native epoch; no global identity',
            'quality_claim': 'path equivalence only, not correctness versus human reference'}



class EmergencyJournal:
    """Preallocated, bounded failure storage; never consult a failed normal guard.

    The first native failure record and a terminal summary fit separate reserved
    regions. Length-prefixed JSON records are fsynced in place, with no rename or
    post-failure allocation. It is diagnostic evidence, not a retry mechanism.
    """
    CAPACITY = 8*1024**2
    def __init__(self, path, guard):
        import os
        import threading
        self.path = Path(path)
        self.lock = threading.Lock()
        self.native_saved = False
        guard.check(self.CAPACITY)
        self.fd = os.open(self.path, os.O_RDWR|os.O_CREAT|os.O_EXCL, 0o600)
        try:
            os.posix_fallocate(self.fd, 0, self.CAPACITY)
            os.fsync(self.fd)
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                close_error = None
                try:
                    os.close(directory_fd)
                except BaseException as exc:
                    close_error = exc
                if close_error is not None:
                    import sys
                    primary = sys.exc_info()[1]
                    if primary is None:
                        raise close_error
                    primary.add_note('Emergency directory close failed: '+repr(close_error))
        except BaseException as primary:
            fd, self.fd = self.fd, None
            try:
                os.close(fd)
            except BaseException as close_error:
                primary.add_note('Emergency file close failed: '+repr(close_error))
            raise
        self.errors = []

    def write(self, value, *, native=False):
        import os
        from .fragment_runtime_contracts import bounded_json
        with self.lock:
            if native and self.native_saved:
                return
            offset, capacity = (0, 7*1024**2) if native else (7*1024**2, 1024**2)
            data = bounded_json(value, capacity-16).encode()
            # A failed/partial write leaves an invalid length, never valid JSON.
            os.pwrite(self.fd, b'0'*16, offset)
            done = 0
            while done < len(data):
                n = os.pwrite(self.fd, data[done:], offset+16+done)
                if n <= 0: raise OSError('Short emergency journal write')
                done += n
            os.fsync(self.fd)
            os.pwrite(self.fd, f'{len(data):016d}'.encode(), offset)
            os.fsync(self.fd)
            if native: self.native_saved = True

    def retain(self, value, *, native=False, primary=None):
        try:
            self.write(value, native=native)
        except BaseException as failure:
            note = 'Emergency evidence retention failed: '+repr(failure)
            self.errors.append(note)
            if primary is not None and hasattr(primary, 'add_note'):
                primary.add_note(note)
            # Diagnostic failure must never replace the primary model/guard error.

    def close(self):
        import os
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)


def raw_native_snapshot(native):
    """Failure-only snapshot avoids verify/status calls on already failed state."""
    return {'key': list(getattr(native, 'key', ())),
            'replay_start': getattr(native, 'replay_start', None),
            'status': asdict(native._status) if getattr(native, '_status', None) else None,
            'provenance': getattr(native, 'provenance', {}),
            'calls': getattr(native, 'calls', [])}


def close_preserving_primary(factory):
    import sys
    primary = sys.exc_info()[1]
    try:
        factory.close()
    except BaseException as failure:
        if primary is None:
            raise
        if hasattr(primary, 'add_note'):
            primary.add_note('Native cleanup/evidence error: '+repr(failure))


def recording_factory(base_factory, persist, emergency):
    """Wrapper retains first failure and always delegates owned native cleanup."""
    from .fragment_model_bridges import NativeFactory
    class RecordingFactory(NativeFactory):
        def close(self):
            import sys
            primary = sys.exc_info()[1]
            current, self.current = self.current, None
            if current is None:
                return
            failure = None
            try:
                if getattr(current, 'process', None) is not None:
                    raw = raw_native_snapshot(current)
                    try:
                        persist(raw)
                    except BaseException as exc:
                        failure = exc
                        emergency.retain({'kind':'native_close_failure', 'error':repr(exc),
                                          'native':raw}, native=True, primary=primary or exc)
            finally:
                try:
                    current.close()
                except BaseException as exc:
                    if failure is None: failure = exc
                    else: failure.add_note('Owned native close also failed: '+repr(exc))
            if failure is not None:
                if primary is None: raise failure
                primary.add_note('Native cleanup/evidence error: '+repr(failure))
    return RecordingFactory(base_factory)


def validate_execution_binding(protocol, output, guard, watchdog):
    import os
    from .fragment_model_compare import require_private_output
    from .fragment_model_guard import Identity
    expected = Path(protocol['real_execution']['output'])
    output = require_private_output(output)
    if (str(expected) != str(expected.resolve()) or output != expected
        or guard.root != output or watchdog.guard is not guard
        or tuple(guard.own_roots) != (Identity.bind(os.getpid()),)
        or Path(watchdog.report_path).resolve().parent != output):
        raise ValueError('Execution output, guard, watchdog or owned PID binding mismatch')
    return output

def execute_native_stage(frozen, approval, output, guard, watchdog):
    """Root-only admission seam; no implicit approval, downloads, retries or CLI.

    frozen contains protocol, config and complete FrozenFiles closure. The root
    supplies a live ResourceGuard/Watchdog with an independently checked project
    roster. This function must run in its own supervised process on CPU 8.
    """
    import json
    import os
    import time
    from types import SimpleNamespace
    from .fragment_model_bridges import CallBudget, NativeBridge, NativeFactory
    from .fragment_model_compare import atomic_json, native_provenance, require_private_output
    from .fragment_model_protocol import FrozenFiles
    stage = approval.get('stage')
    protocol = frozen['protocol']
    if stage == 'P2_split_remainder':
        from .native_boundary_remainder import validate_remainder, reconstruct_whole
        limits, selected_arms = validate_remainder(frozen)
    else:
        limits, selected_arms = LIMITS.get(stage), [a for a in ARMS if a.startswith(stage or '!')]
    if (approval.get('freeze_sha256') != digest(frozen) or limits is None
        or approval.get('limits') != limits
        or not approval.get('authorization_reference')
        or approval.get('project_roster_verified') is not True):
        raise ValueError('Matching independent root admission, exact stage limits and verified roster required')
    if (os.sched_getaffinity(0) != {8} or guard.own_cap > 1024**3
        or guard.aggregate_cap > 6*1024**3 or guard.artifact_cap > 64*1024**2
        or guard.host_reserve < 2*1024**3 or guard.disk_reserve < int(5.5*1024**3)
        or not watchdog.thread or not watchdog.thread.is_alive()):
        raise ValueError('Real-native CPU/RSS/disk/host/watchdog admission mismatch')
    output = validate_execution_binding(protocol, output, guard, watchdog)
    output.mkdir(parents=True,exist_ok=True)
    files = FrozenFiles(frozen['files']); files.verify(full=True);guard.check()
    # Durable before any model construction; an uncertain failure consumes stage.
    marker=output/(stage+'-attempt.json')
    started={'stage':stage,'reserved':limits,'authorization_reference':approval['authorization_reference'],
             'freeze_sha256':digest(frozen),'began_unix':time.time(),'status':'attempt_started'}
    with marker.open('x') as stream:
        json.dump(started,stream);stream.flush();os.fsync(stream.fileno())
    fd=os.open(output,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    # One combined 1800-second wall window begins at first real-stage admission.
    all_attempts=[json.loads(p.read_text()) for p in output.glob('P*-attempt.json')]
    allowed_freezes = {digest(frozen)}
    if stage == 'P2_split_remainder':
        allowed_freezes.add(frozen['remainder']['parent_freeze_sha256'])
    if any(a['freeze_sha256'] not in allowed_freezes for a in all_attempts):
        raise ValueError('Cannot mix protocol freezes or reset aggregate attempt scope')
    remaining=1800-(time.time()-min(a['began_unix'] for a in all_attempts))
    if remaining<=0:raise TimeoutError('Combined P1/P2 deadline exhausted')
    guard.deadline=min(guard.deadline,time.monotonic()+remaining)
    from .native_worker import NativeProcess
    temp=output/'tmp';temp.mkdir(exist_ok=True)
    config=dict(frozen['config']);config.update(data_dir=str(output),
        _fragment_expected_imports=frozen['files'],_fragment_native_max_response_bytes=2*1024**2)
    budget=CallBudget(limits)
    results=[reconstruct_whole(frozen['remainder'])] if stage == 'P2_split_remainder' else []
    provenance=native_provenance(config)
    settings=SimpleNamespace(data_dir=output,disk_reserve_bytes=int(5.5*1024**3),
        process_memory_limit_bytes=1024**3,cpu_threads=1,inference_config=lambda:config)
    emergency = EmergencyJournal(output/(stage+'-emergency.bin'), guard)
    saved_records = []
    def make_factory(policy,control,sink):
        def persist(value):
            saved_records.append(value)
            try:
                atomic_json(output/(stage+'-native-'+str(len(saved_records))+'.json'),value,guard)
            except BaseException as exc:
                emergency.retain({'kind':'native_evidence_failure', 'error':repr(exc),
                                  'native':value}, native=True, primary=exc)
                raise
            path = output/(stage+'-native-'+str(len(saved_records))+'.json')
            sink({'record_file':path.name, 'bytes':path.stat().st_size,
                  'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                  'native_key':value.get('key'), 'kind':'bounded_raw_native_epoch_record'})
        def failure_sink(value):
            # NativeBridge invokes this while already handling its primary error.
            # Never replace that error if normal persistence is unavailable.
            import sys
            primary = sys.exc_info()[1]
            emergency.retain({'kind':'native_failure', 'native':value}, native=True, primary=primary)
            try: persist(value)
            except BaseException as failure:
                if primary is not None: primary.add_note('Normal failure evidence write failed: '+repr(failure))
        return recording_factory(lambda:NativeBridge(lambda:NativeProcess(settings),
            fingerprint=policy.native_fingerprint,expected_provenance=provenance,
            temp_root=temp,verify=files.verify,budget=budget,guard=guard,
            eos_control=control['control_id'],eos_epoch=control['epoch'],
            eos_final_frame=control['final_frame'],failure_sink=failure_sink,expected_imports=frozen['files']),
            persist, emergency)
    try:
        for arm in selected_arms:
            with materialize(protocol['source']['path'],arm,temp,guard) as prepared:
                expected={(r['start'],r['end']):r['pcm_sha256'] for r in protocol['source']['ranges']}
                if any(expected.get((r['source_start'],r['source_end'])) != r['pcm_sha256'] for r in prepared['lineage']):
                    raise ValueError('Exact pinned source-range PCM changed')
                result=run_arm(arm,prepared,output,make_factory,guard,
                    native_fingerprint=digest({'freeze':digest(frozen),'role':'native-boundary'}))
                atomic_json(output/(arm+'.json'),compact_arm_record(result,output),guard);results.append(result)
        files.verify(full=True)
        summary={'stage':stage,'status':'completed','comparison':compare_pair(*results),
                 'actual_attempts':budget.records,'budget_used':budget.used,
                 'resources':guard.check(),'freeze_sha256':digest(frozen)}
        if stage == 'P2_split_remainder':
            prior = frozen['remainder']['actual_consumed_p2']
            summary.update(original_failed_stage_preserved=True, original_p2_budget_used=prior,
                combined_p2_budget_used={k:{n:prior[k][n]+budget.used[k][n] for n in ('calls','frames')}
                                        for k in prior}, whole_native_rerun=False)
        atomic_json(output/(stage+'-result.json'),summary,guard)
        return summary
    except BaseException as exc:
        # Already-reserved diagnostics survive a tripped ordinary guard. Preserve
        # the exact original exception even if the disk itself can no longer write.
        failure = {'type':type(exc).__name__, 'error':str(exc),
                   'budget_used':budget.used, 'attempts':budget.records}
        emergency.retain(failure, primary=exc)
        try:
            atomic_json(output/(stage+'-failure.json'),failure,guard)
        except BaseException as write_error:
            exc.add_note('Normal terminal failure write failed: '+repr(write_error))
        raise
    finally:
        close_preserving_primary(emergency)
