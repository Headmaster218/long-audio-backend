"""Internal one-island, exact-PCM scheduler with injected analysis providers.

Not connected to Worker, upload APIs, public output tables or identity banks.
All providers receive the same bounded logical reader. Provider factories must
return new process/state instances when called; replay is deliberately bounded.
"""
from dataclasses import asdict
import hashlib
import math
from pathlib import Path

from .boundaries import lexical_core
from .fragment_core import BoundedPCM16Reader, PlanCheckpoint, SourceChanged, _signature
from .fragment_runtime_contracts import (
    CONTRACT, EPOCH, MAX_ITEMS, MAX_PARTS, PREROLL, RATE, NativeStatus, PCMWindow,
    StaleWork, admit, bounded_json, digest, native_key, next_window, own_intervals,
    prefix_hash, validate_intervals, window_id, lexical_routing_anchor,
)


class LogicalPCMReader:
    """Verify each source once per safe binding lifetime; stat before/after use.

    Requires controlled, immutable files. Metadata checks do not authenticate
    arbitrarily mutable/adversarial storage. A new binding or restart rehashes
    the complete original file. Plans retain hashes, never transport paths.
    """
    def __init__(self, plan, paths):
        admit(plan)
        self.reader = BoundedPCM16Reader(plan.sources, paths, plan)
        self.verified_sources = len(plan.sources)

    def extend(self, plan, paths):
        old = self.reader
        if plan.sha256 == old.plan.sha256:
            if any(Path(paths[key]) != old.paths[key] for key in old.paths):
                raise SourceChanged('Move a source only by reopening a verified reader')
            self.verify()
            return
        admit(plan)
        PlanCheckpoint(old.plan, 0).extend(plan)
        self.verify()
        if any(Path(paths[key]) != old.paths[key] for key in old.paths):
            raise SourceChanged('Existing source transport binding changed')
        added = plan.sources[len(old.plan.sources):]
        verified = BoundedPCM16Reader(added, paths)
        old.sources.update(verified.sources)
        old.paths.update(verified.paths)
        old.signatures.update(verified.signatures)
        old.plan = plan
        old.starts = [span.logical_start for span in plan.spans]
        self.verified_sources += len(added)

    def verify(self):
        for key, path in self.reader.paths.items():
            try:
                if _signature(path) != self.reader.signatures[key]:
                    raise SourceChanged('A bound source changed since verification')
            except OSError as exc:
                raise SourceChanged('A bound source is missing or unreadable') from exc

    def read(self, start, end):
        parts = self.reader.mapping(start, end)
        if len(parts) > MAX_PARTS:
            raise ValueError('Logical window exceeds the 128 owner-part contract')
        self.verify()
        pcm = self.reader.read(start, end)
        self.verify()
        if len(pcm) != (end - start) * 2:
            raise SourceChanged('Logical reader returned a short PCM window')
        mapping = tuple({'span': asdict(part), 'source_sha256': self.reader.sources[part.fragment_id].sha256}
                        for part in parts)
        return PCMWindow(start, end, pcm, mapping, digest(mapping), hashlib.sha256(pcm).hexdigest())


def _validate_analyzer(result, pcm):
    if not isinstance(result, dict) or set(result) not in ({'segments', 'events'}, {'segments', 'events', 'evidence_ref'}):
        raise ValueError('Analyzer must return bounded native segments and event intervals')
    if 'evidence_ref' in result:
        from .fragment_evidence import validate_reference
        validate_reference(result['evidence_ref'])
    segments = result['segments']
    if not isinstance(segments, list) or len(segments) > MAX_ITEMS:
        raise ValueError('Bounded analyzer segments required')
    duration = (pcm.end - pcm.start) / RATE
    count = 0
    for segment in segments:
        if not isinstance(segment, dict) or set(segment) != {'source_track_id', '_units'}:
            raise ValueError('Explicit source-scoped native units required')
        track = segment['source_track_id']
        if not isinstance(track, str) or not track or len(track) > 256:
            raise ValueError('Bounded analyzer source track required')
        if not isinstance(segment['_units'], list) or not segment['_units']:
            raise ValueError('Legacy un-timed text is unsupported')
        for unit in segment['_units']:
            count += 1
            if count > MAX_ITEMS or not isinstance(unit, dict) or set(unit) != {
                'start', 'end', 'ownership_anchor', 'text', 'unit_kind'
            }:
                raise ValueError('Bounded model-native lexical units required')
            a, b, anchor = (unit[key] for key in ('start', 'end', 'ownership_anchor'))
            if any(type(x) not in (int, float) or not math.isfinite(x) for x in (a, b, anchor)) or not (
                0 <= a < b <= duration and a <= anchor <= b
            ):
                raise ValueError('Invalid native lexical support/anchor')
            if not isinstance(unit['text'], str) or not unit['text'] or len(unit['text']) > 4096:
                raise ValueError('Bounded native lexical text required')
            if unit['unit_kind'] not in ('native_word', 'native_phrase'):
                raise ValueError('No inferred/interpolated character timing')
    validate_intervals(result['events'], pcm.start, pcm.end, 'event')
    bounded_json(result)


class LogicalRunner:
    """One step either commits one core, acknowledges zero-output EOS, or waits.

    Native provider: fingerprint; reset(key, replay_start); push(PCMWindow);
    status()->NativeStatus; finish(control_id)->NativeStatus. The input receipt
    and observation frontier are distinct (see NativeStatus).
    Analyzer: fingerprint; analyze(PCMWindow, NativeStatus)->segments/events.
    Neither provider may write public outputs. No whole-recording PCM cache.
    """
    def __init__(self, store, run_id, paths, analyzer, native_factory, fault=None):
        self.store, self.run_id, self.paths = store, run_id, paths
        self.analyzer, self.native_factory = analyzer, native_factory
        self.fault = fault or (lambda point: None)
        self.reader = None
        self.native = None
        self.key = None
        self.pushed_until = None
        self.native_prefix = None
        self.finished_response = None

    def _reader(self, snapshot):
        if self.reader is None:
            self.reader = LogicalPCMReader(snapshot.plan, self.paths)
        else:
            self.reader.extend(snapshot.plan, self.paths)
        if self.analyzer.fingerprint != snapshot.policy.analyzer_fingerprint:
            raise ValueError('Analyzer fingerprint differs from durable policy')
        return self.reader

    def _verify_providers(self, snapshot):
        if self.analyzer.fingerprint != snapshot.policy.analyzer_fingerprint:
            raise ValueError('Analyzer fingerprint changed during processing')
        if self.native is None or self.native.fingerprint != snapshot.policy.native_fingerprint:
            raise ValueError('Native fingerprint changed during processing')

    def _native(self, snapshot, epoch, desired_end, finish=False):
        key = native_key(self.run_id, epoch, snapshot.policy)
        start = max(0, epoch * EPOCH - PREROLL)
        if desired_end - start > 360 * RATE:
            raise ValueError('Native replay exceeds bounded epoch input')
        if self.key != key:
            self.native = self.native_factory()
            if self.native.fingerprint != snapshot.policy.native_fingerprint:
                raise ValueError('Native fingerprint differs from durable policy')
            self.native.reset(key, start)
            self.key, self.pushed_until = key, start
            self.native_prefix = prefix_hash(snapshot.plan, start)
            self.finished_response = None
        if self.native.fingerprint != snapshot.policy.native_fingerprint:
            raise ValueError('Live native fingerprint changed during run')
        if prefix_hash(snapshot.plan, self.pushed_until) != self.native_prefix:
            raise SourceChanged('Native replay input owner prefix changed')
        if self.finished_response is not None and desired_end > self.pushed_until:
            raise ValueError('Cannot push into a finished native instance')
        while self.pushed_until < desired_end:
            end = min(desired_end, self.pushed_until + 60 * RATE)
            pcm = self.reader.read(self.pushed_until, end)
            self.native.push(pcm)
            self.pushed_until = end
            self.native_prefix = prefix_hash(snapshot.plan, end)
            self.fault('native.after_push')
        if finish:
            control = snapshot.control
            if not control or control['acknowledged'] or control['epoch'] != epoch or desired_end != control['final_frame']:
                raise ValueError('Native finish requires matching durable true-EOS intent')
            if self.finished_response is None:
                self.fault('finish.before_request')
                status = self.native.finish(control['control_id'])
                # Fault here simulates a sent request with no durable/known reply.
                self.fault('finish.after_request')
                if not isinstance(status, NativeStatus):
                    raise ValueError('Native finish returned no typed acknowledgement')
                status.validate(self.pushed_until, start)
                if not status.finished:
                    raise ValueError('Native finish did not confirm completion')
                self.finished_response = {
                    'control_id': control['control_id'], 'native_key': list(key),
                    'consumed_until': status.consumed_until, 'stable_until': status.stable_until,
                    'activity_until': status.activity_until, 'finished': True,
                }
                self.fault('finish.after_response')
        status = self.native.status()
        if not isinstance(status, NativeStatus):
            raise ValueError('Native status must use the injected contract')
        status.validate(self.pushed_until, start)
        if finish and not status.finished or status.finished and self.finished_response is None:
            raise ValueError('Native status contradicts requested terminal state')
        return status

    def step(self):
        snapshot = self.store.snapshot(self.run_id)
        if snapshot.status in ('cancelled', 'completed'):
            return snapshot.status
        try:
            self._reader(snapshot)
            window = next_window(snapshot.plan, snapshot.next_frame, snapshot.policy)
            if window is None:
                if snapshot.control and not snapshot.control['acknowledged']:
                    self._native(snapshot, snapshot.control['epoch'], snapshot.plan.end_frame, finish=True)
                    self.reader.verify()
                    self._verify_providers(snapshot)
                    self.store.acknowledge_empty(snapshot, self.finished_response)
                    return 'completed'
                self.fault('wait.before_register')
                self.store.register_wait(self.run_id, snapshot.revision, snapshot.next_frame)
                return 'waiting'
            # Native stability may require more lookahead than the analyzer's
            # 2 s context. Feed only this bounded epoch plus right context,
            # through the same reader. Never finish an ordinary epoch seam.
            native_end = min(snapshot.plan.end_frame, window.epoch_end + snapshot.policy.context_frames)
            status = self._native(snapshot, window.epoch_index, native_end, finish=window.finish_native)
            # A model frontier can lag beyond analyzer context. Advance
            # bounded native lookahead while real input is available; waiting
            # without consuming that input would otherwise never make progress.
            native_limit = min(snapshot.plan.end_frame, max(0, window.epoch_start-PREROLL)+360*RATE)
            while status.stable_until < window.core_end and not status.finished and self.pushed_until < native_limit:
                status = self._native(snapshot, window.epoch_index,
                                      min(native_limit, self.pushed_until+PREROLL))
            if status.stable_until < window.core_end and not status.finished:
                if snapshot.control and snapshot.control['epoch'] == window.epoch_index:
                    # Intent is durable already. Finish may release an earlier
                    # unstable core; acknowledge only with the last core commit.
                    status = self._native(snapshot, window.epoch_index, snapshot.plan.end_frame, finish=True)
                else:
                    if snapshot.plan.final:
                        raise ValueError('Unsupported final native frontier before the last-sample epoch; no implicit epoch drain')
                    self.store.register_wait(self.run_id, snapshot.revision, snapshot.next_frame)
                    return 'waiting_native'
            pcm = self.reader.read(window.read_start, window.read_end)
            self.fault('core.after_read')
            if hasattr(self.analyzer, 'bind_context'):
                self.analyzer.bind_context(snapshot, window, self.native)
            result = self.analyzer.analyze(pcm, status)
            _validate_analyzer(result, pcm)
            self.fault('core.after_analysis')
            # Source tracks are epoch-local evidence, never global identities.
            scope = digest(list(self.key))
            # Publish each native unit separately. lexical_core normally
            # assembles adjacent units from one segment, which would erase the
            # individual native intervals in this reviewable output envelope.
            prepared = []
            base = pcm.start/RATE
            for segment in result['segments']:
                track = scope+':'+segment['source_track_id']
                for unit in segment['_units']:
                    a, b, anchor = (unit[key]+base for key in ('start', 'end', 'ownership_anchor'))
                    route = lexical_routing_anchor(a, b, anchor)
                    global_unit = {**unit, 'start': a, 'end': b, 'ownership_anchor': route}
                    prepared.append({'source_track_id': track, 'native_unit_kind': unit['unit_kind'],
                                     'native_start': a, 'native_end': b, 'ownership_anchor': anchor,
                                     'routing_anchor': route, '_units': [global_unit]})
            result['segments'] = prepared
            previous = [{**item, 'ownership_anchor': item['routing_anchor']} for item in snapshot.tail]
            # Global bounds are converted once from exact frames. Only the
            # separate routing point is adjusted; native evidence is untouched.
            owned = lexical_core(result, window.core_start/RATE, window.core_end/RATE, previous, 0.)
            if result['segments'] and owned is None:
                raise ValueError('Analyzer omitted supported lexical units')
            for segment in owned or []:
                segment['start'] = segment.pop('native_start')
                segment['end'] = segment.pop('native_end')
            tail = result.get('metadata', {}).get('boundary_tail', [])
            # Mirror lexical_core's ordered filter/truncation, not a mapping
            # keyed by timestamps/text: distinct native anchors may share a
            # rounded route, including more than 256 otherwise identical units.
            native_tail = [unit for unit in prepared
                           if window.core_end/RATE-4 <= unit['routing_anchor'] <= window.core_end/RATE+4][-256:]
            if len(native_tail) != len(tail):
                raise ValueError('Lexical tail no longer matches native unit ordering')
            for item, original in zip(tail, native_tail):
                if (item['start'], item['end'], item['text'], item['source_track_id'], item['ownership_anchor']) != (
                    original['native_start'], original['native_end'], original['_units'][0]['text'],
                    original['source_track_id'], original['routing_anchor']
                ):
                    raise ValueError('Lexical tail unit correspondence changed')
                item['ownership_anchor'] = original['ownership_anchor']
                item['routing_anchor'] = original['routing_anchor']
            checkpoint = {'native_key': list(self.key), 'replay_start': max(0, window.epoch_start - PREROLL),
                          'consumed_until': status.consumed_until, 'stable_until': status.stable_until,
                          'activity_until': status.activity_until, 'finished': status.finished,
                          'input_prefix_hash': self.native_prefix}
            payload = {
                'window_id': window_id(self.run_id, window, snapshot.policy), 'window': asdict(window),
                'revision': snapshot.revision, 'plan_hash': snapshot.plan.sha256, 'contract': CONTRACT,
                'policy_hash': snapshot.policy.sha256, 'mapping': pcm.mapping, 'map_hash': pcm.map_sha256,
                'pcm_hash': pcm.pcm_sha256, 'segments': owned or [],
                'turns': own_intervals(status.turns, window.core_start, window.core_end, scope),
                'events': own_intervals(result['events'], window.core_start, window.core_end,
                                        window_id(self.run_id, window, snapshot.policy)),
                'tail': tail, 'checkpoint': checkpoint,
            }
            if 'evidence_ref' in result:
                payload['evidence_ref'] = result['evidence_ref']
                if hasattr(self.analyzer, 'finalize_evidence'):
                    payload['evidence_ref'] = self.analyzer.finalize_evidence(payload, result.get('metadata', {}))
            bounded_json(payload)
            self.reader.verify()
            self._verify_providers(snapshot)
            self.store.commit(snapshot, window, payload, tail, checkpoint,
                              self.finished_response if window.finish_native else None)
            return 'completed' if window.finish_native else 'committed'
        except BaseException:
            # Any uncertain push/finish or stale transaction is replayed in a
            # new instance, so retries never append to possibly finished state.
            self.native, self.key, self.finished_response = None, None, None
            raise
