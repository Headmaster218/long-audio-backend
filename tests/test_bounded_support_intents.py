"""Fresh prospective bounded support through the existing application pipeline.

Authored for a separate execution gate. Every source is generated PCM, native
responses and ASR units are fixed synthetic fixtures, and MOSS uses FakeDriver.
The in-process TestClient opens no listening server. No old job, model, decoder,
download, production runtime qualification or acoustic-quality claim is involved.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
import sqlite3
import time
import wave

import pytest

from audio_backend import lexical_evidence as lexical
from audio_backend import moss_automatic as automatic
from audio_backend import moss_result_store as history
from audio_backend import native_owned_core_seams as core
from audio_backend import transcript_contract as transcript
from audio_backend.boundary_store import context_parts
from audio_backend.native_owned_cells import RATE, Unavailable
from audio_backend.native_owned_cell_store import Budget
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.settings import Settings
from audio_backend.worker import Worker
from test_moss_automatic_integration import (
    automatic_app_factory, process_primary, drain_automatic, primary_output)
from test_multicore_seam_coverage import pcm as counter_pcm
from test_native_owned_support import synthetic_provenance, synthetic_state
from test_native_owned_support_api import FakeAnalyzer


def frame(seconds):
    return round(seconds * RATE)


C_COMPONENTS = [[frame(a), frame(b)] for a, b in (
    (20.771, 20.901), (22.881, 24.489), (30.481, 30.919))]
C_SUPPORT = [frame(19.4), frame(31.6)]


def fixture_pcm(a, b):
    """Coordinate-dependent bytes including a declared zero-valued interval."""
    value = bytearray(counter_pcm(a, b))
    lo, hi = max(a, frame(24.)), min(b, frame(24.25))
    if lo < hi:value[2 * (lo - a):2 * (hi - a)] = bytes(2 * (hi - lo))
    return bytes(value)


def wav_bytes(a, b):
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1);target.setsampwidth(2);target.setframerate(RATE)
        target.writeframes(fixture_pcm(a, b))
    return output.getvalue()


def intent_rows(h):
    with h.store.db() as db:
        return [dict(row) for row in db.execute(
            f'SELECT * FROM {automatic.TABLE} WHERE sid=? ORDER BY seq', (h.sid,))]


def resolved_target(row):
    target, _ = automatic._resolve_target_body(row['target_body'], row['proof'], row['plan_phase'])
    return target


def c_target(h):
    found = [row for row in intent_rows(h) if row['target_body'] is not None
        and resolved_target(row)['identity']['components'] == C_COMPONENTS]
    assert len(found) == 1, [(row['reason'], row['plan_phase']) for row in intent_rows(h)]
    return found[0]


def _switch_owner_reference_wire(h, key, wire):
    """Test-only atomic equivalent-wire switch against the real owner ledger."""
    with h.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        before = automatic._read_v2(db, key=key)
        doc, _, _ = automatic.validate_document(before['proof'])
        assert wire in (automatic.canonical(doc['target']),
            automatic._bound_target_reference(before['proof'], doc['target']))
        assert len(before['proof']) + len(wire) <= 65536
        usage_before = _reference_accounting(db, before['sid'])
        delta = len(wire) - len(before['target_body'])
        assert db.execute(f'UPDATE {automatic.TABLE} SET target_body=?,planning_bytes=?,charge_bytes=charge_bytes+? '
            'WHERE candidate_key=? AND target_body=? AND proof=?',
            (wire, len(wire), delta, key, before['target_body'], before['proof'])).rowcount == 1
        automatic._charge(db, before['sid'], (0, delta, 0, delta))
        after = automatic._read_v2(db, key=key)
        assert after['target_body'] == wire and resolved_target(after) == doc['target']
        assert all(after[name] == before[name] for name in ('proof','proof_sha256','candidate_key',
            'target_key','cell_id','binding','state','plan_phase','plan_revision','job_id'))
        usage_after = _reference_accounting(db, before['sid'])
        assert [tuple(b-a for a,b in zip(left,right)) for left,right in zip(usage_before,usage_after)] == [(0,delta,0,delta)]*2
        return after


@pytest.fixture
def bounded_app_factory(automatic_app_factory, monkeypatch, capsys):
    """Extend the existing real lifespan fixture, changing only fake producers."""
    original_post_init = Settings.__post_init__
    state = {'windows': {}, 'last_group_end': 31.6, 'support_seconds': None,
             'natural_producer': False}

    def post_init(settings):
        if settings.moss_automatic_enabled:
            settings.native_owned_bounded_core_support_enabled = True
            settings.lexical_evidence_enabled = True
            settings.window_seconds = 30. if state['natural_producer'] else 10.5
            settings.context_seconds = 2. if state['natural_producer'] else 5.
            if state['support_seconds'] is not None:
                settings.native_owned_bounded_core_support_seconds = state['support_seconds']
        original_post_init(settings)

    def extract(worker, chunk, start, end, wav, tmp, stop, *, capture_support=False,
                capture_lexical=False):
        assert not stop() and not capture_support
        a, b = frame(start), frame(end)
        if state['natural_producer'] and capture_lexical:
            origin = chunk['timeline_start']; origin_frame = frame(origin)
            assert start == origin + (a-origin_frame) / RATE
            assert end == origin + (b-origin_frame) / RATE
            assert worker.native.process.queries[-1] == [a, b]
        else:
            assert start == a / RATE and end == b / RATE
        parts = context_parts(worker.store, chunk, start, end)
        snapshots = None
        if capture_lexical:
            with worker.store.db() as db:
                snapshots = lexical.snapshot_parts(db, parts, chunk['session_id'])
        data = bytearray(); cursor = 0
        for index, part in enumerate(parts):
            with wave.open(part['path'], 'rb') as source:
                source.setpos(frame(part['read_start']))
                block = source.readframes(frame(part['read_duration']))
            data.extend(block)
            if snapshots is not None:
                snapshots[index]['analyzer_frames'] = [cursor, cursor + len(block) // 2]
            cursor += len(block) // 2
        assert bytes(data) == fixture_pcm(a, b)
        with wave.open(str(wav), 'wb') as target:
            target.setnchannels(1);target.setsampwidth(2);target.setframerate(RATE)
            target.writeframes(data)
        state['windows'][str(wav)] = (a, b)
        if state['natural_producer'] and capture_lexical:
            state['lexical_windows'][str(wav)] = deepcopy(snapshots)
        return {'_lexical_sources': snapshots} if snapshots is not None else None

    def analyze(analyzer, path, **kwargs):
        native = kwargs['diarization']; a, b = state['windows'][str(path)]
        # These fixed primary units straddle two genuine core boundaries.
        # Prefix/suffix let conservation checks cover kept as well as replaced
        # characters. Estimated endpoints make no acoustic-boundary claim.
        definitions = [(18.8, 19.1, ' prefix'), (19.4, 21.7, ' alpha'),
            (22.5, 24.6, ' beta'), (30.2, state['last_group_end'], ' gamma'),
            (40., 40.4, ' suffix')]
        if state['natural_producer']:
            # Fixed opaque two-Han parents. Do not split them into characters,
            # clip them to extraction, or make an early partial P1 observation.
            definitions = [(19.4, 21.7, '甲乙'), (21.5, 31.6, '丙丁')]
        units = []
        for lo, hi, value in definitions:
            lo, hi = frame(lo), frame(hi)
            if a <= lo < hi <= b:
                units.append({'start': (lo - a) / RATE, 'end': (hi - a) / RATE,
                    'ownership_anchor': (lo - a) / RATE, 'text': value,
                    'raw_text': value, 'anchor_kind': 'ctc_emission'})
        segments = []
        if units:
            segments.append({'start': units[0]['start'], 'end': max(u['end'] for u in units),
                'text': ''.join(u['text'] for u in units).strip(),
                'raw_text': ''.join(u['raw_text'] for u in units).strip(),
                'asr_backend': 'sensevoice', 'timestamp_kind': lexical.PRIMARY_TIMESTAMP,
                '_units': units})
        if state['natural_producer']:
            state['analysis'].append({'window': [a, b],
                'units': [[a + frame(u['start']), a + frame(u['end']), u['text']]
                          for u in units],
                'sources': state['lexical_windows'][str(path)]})
        return {'segments': segments, 'events': [], 'speaker_turns': [],
            'metadata': {'asr_backend': 'sensevoice', 'timestamp_semantics': lexical.PRIMARY_TIMESTAMP,
                'diarization_turns': deepcopy(native['turns'])}}

    monkeypatch.setattr(Settings, '__post_init__', post_init)
    monkeypatch.setattr(Worker, 'extract_pcm', extract)
    monkeypatch.setattr(FakeAnalyzer, 'analyze', analyze)

    @contextmanager
    def open_app(*, sid=None, enabled=True, last_group_end=31.6, support_seconds=None,
                 natural_producer=False):
        state['last_group_end'] = last_group_end
        state['support_seconds'] = support_seconds
        state['natural_producer'] = natural_producer
        state['analysis'] = []; state['lexical_windows'] = {}
        with automatic_app_factory(sid=sid, automatic=enabled) as h:
            h.planning = []; h.committed_targets = []; h.timings = []; h.publications = []
            if natural_producer:h.natural_analysis = state['analysis']
            if enabled:
                assert h.store.settings.native_owned_bounded_core_support_seconds == (.75 if support_seconds is None else support_seconds)
                assert h.owner.bounded_core_enabled
                assert h.owner.retention_store_key is not None
                assert h.owner.directory.name == 'native-owned-cells-bounded-v1'

                class Native:
                    """One real observer callback per synthetic RPC response."""
                    def __init__(self):
                        self.frames = 0;self.epoch = None;self.closed = False
                        self.calls = [];self.queries = []
                        self.progress = []
                    def request(self, action, stop, **arguments):
                        assert not stop();self.calls.append(action)
                        if action == 'reset':
                            self.epoch = arguments['epoch_id'];self.frames = 0
                            response = {'state': synthetic_state(self.epoch, 0),
                                        'provenance': synthetic_provenance()}
                        elif action == 'push':
                            with wave.open(arguments['wav'], 'rb') as source:
                                old = self.frames;self.frames += source.getnframes()
                                if natural_producer:
                                    assert source.readframes(self.frames-old) == fixture_pcm(old, self.frames)
                            if natural_producer:
                                processed, stable = {
                                    339200: (0, 0), 504000: (337920, 305920),
                                    843200: (675840, 643840), 1008000: (855040, 823040),
                                }[self.frames]
                                response = synthetic_state(self.epoch, self.frames,
                                    processed_frames=processed, stable_frames=stable)
                            else:response = synthetic_state(self.epoch, self.frames, stable_frames=0)
                        elif action == 'finish':
                            response = synthetic_state(self.epoch, self.frames, finished=True)
                        else:
                            assert action == 'turns'
                            qa, qb = frame(arguments['start']), frame(arguments['end'])
                            assert 0 <= qa < qb <= self.frames and qb - qa <= 60 * RATE
                            self.queries.append([qa, qb]); turns = []
                            overlap = [[332336, 336000]] if natural_producer else C_COMPONENTS
                            for voice, intervals in ((1, [[qa, qb]]), (2, overlap)):
                                for lo, hi in intervals:
                                    lo, hi = max(qa, lo), min(qb, hi)
                                    if lo < hi:turns.append({'start': lo / RATE, 'end': hi / RATE,
                                        'speaker_local_id': f'{self.epoch}:speaker_{voice}'})
                            response = {'turns': turns}
                        if natural_producer and action != 'turns':
                            current = response['state'] if action == 'reset' else response
                            self.progress.append([frame(current[key]) for key in
                                ('input_seconds', 'processed_seconds', 'stable_seconds')]
                                + [int(current['finished'])])
                        h.worker._native_response(response)
                        return response
                    def close(self):self.closed = True

                def native_factory(settings):
                    instance = Native();h.natives.append(instance);return instance
                monkeypatch.setattr(h.worker, '_native_process_factory', native_factory)
                # Fixed numeric synthetic diagnostics only. No SQL body, row,
                # source identifier or payload is retained by these hooks.
                from audio_backend import native_owned_cell_capture as capture
                publication = [None]
                publish = h.owner._publish; cell_current = h.owner._cell_current
                read_v2 = automatic._read_v2
                def measured_read(*args, **kwargs):
                    current = publication[0]
                    if current is not None and kwargs.get('budget') is current['budget']:
                        current['certificate_reads'] += 1
                        original_db = args[0]
                        class ComparisonDB:
                            def execute(self, sql, parameters=()):
                                if 'proof=? AS proof_same' in sql:
                                    operands = [p for p in parameters if type(p) is bytes]
                                    assert sum(map(len, operands)) <= 65536
                                    current['comparison_parameter_count'] += len(operands)
                                    current['comparison_input_bytes'] += sum(map(len, operands))
                                return original_db.execute(sql, parameters)
                        args = (ComparisonDB(), *args[1:])
                    return read_v2(*args, **kwargs)
                def measured_current(op, cell, budget):
                    current = publication[0]; before = budget.sql_bytes
                    if current is not None:current['fresh_boundaries'] += 1
                    try:return cell_current(op, cell, budget)
                    finally:
                        if current is not None:current['authority_projected_bytes'] += budget.sql_bytes-before
                def measured_publish(op, budget):
                    if op.kind != 'publish_auto':return publish(op, budget)
                    assert len(h.publications) < 32
                    current = {'budget': budget, 'fresh_boundaries': 0, 'certificate_reads': 0,
                        'comparison_parameter_count': 0, 'comparison_input_bytes': 0,
                        'authority_projected_bytes': 0, 'publish_returned_durable': 0}
                    publication[0] = current; before = budget.sql_bytes; began = time.monotonic()
                    cell_id = op.payload[1].cell_id
                    try:
                        result = publish(op, budget)
                        current['publish_returned_durable'] = int(result.get('status') == 'durable')
                        return result
                    finally:
                        publication[0] = None
                        record = {key: value for key, value in current.items() if key != 'budget'}
                        record.update(total_projected_bytes=budget.sql_bytes-before,
                                      publication_seconds=time.monotonic()-began)
                        h.publications.append((cell_id, record))
                        if not natural_producer:
                            with capsys.disabled():print('bounded_publication_counts '+json.dumps(record, sort_keys=True), flush=True)
                monkeypatch.setattr(automatic, '_read_v2', measured_read)
                monkeypatch.setattr(h.owner, '_cell_current', measured_current)
                monkeypatch.setattr(h.owner, '_publish', measured_publish)
                timing = [None]
                primary_db = h.store.db
                @contextmanager
                def timed_db(*, budget=None):
                    with primary_db(budget=budget) as db:
                        current = timing[0]
                        if current is not None and budget is current['budget']:
                            def trace(statement):
                                if statement == 'BEGIN IMMEDIATE':
                                    current['begin'] = time.monotonic()
                            db.set_trace_callback(trace)
                        yield db
                monkeypatch.setattr(h.store, 'db', timed_db)
                def measure(module, name, field, *, binding=False, validator=False):
                    original = getattr(module, name)
                    def measured(*args, **kwargs):
                        current = timing[0]
                        if current is None:return original(*args, **kwargs)
                        if validator and not current['binding_depth']:
                            return original(*args, **kwargs)
                        began = time.monotonic()
                        if binding:
                            current['binding_depth'] += 1; current['binding_calls'] += 1
                        if validator:current['bounded_validation_calls'] += 1
                        try:return original(*args, **kwargs)
                        finally:
                            current[field] += time.monotonic() - began
                            if binding:current['binding_depth'] -= 1
                    monkeypatch.setattr(module, name, measured)
                measure(automatic, '_bind_target', 'binding_seconds', binding=True)
                measure(core, 'make_bounded_cell', 'cell_construction_seconds')
                measure(core, 'validate_bounded_document', 'bounded_validation_seconds', validator=True)
                measure(capture, 'bounded_primary_snapshot', 'inventory_scan_seconds')
                commit_window = h.store.commit_window
                def commit(chunk, offset, end, result):
                    handoff = result.get('_native_bounded_support')
                    assert handoff is not None
                    h.planning.append({'end': frame(chunk['timeline_start'] + end),
                        'origins': len(handoff['origins']), 'true_eof': handoff['true_eof'],
                        'plans': [{'components': p['target']['identity']['components'],
                            'reason': p.get('reason'), 'has_pcm': 'pcm' in p,
                            'support': p.get('support')} for p in handoff['plans']]})
                    assert len(h.timings) < 32  # This fresh 42-second fixture has at most 14 cores.
                    budget = handoff['budget']; began = time.monotonic()
                    assert budget.seconds == h.store.settings.native_owned_bounded_core_support_seconds
                    current = {'budget': budget, 'begin': None, 'binding_depth': 0,
                        'precommit_budget_seconds': budget.elapsed,
                        'binding_calls': 0, 'bounded_validation_calls': 0,
                        'binding_seconds': 0., 'cell_construction_seconds': 0.,
                        'bounded_validation_seconds': 0., 'inventory_scan_seconds': 0.}
                    timing[0] = current
                    try:accepted = commit_window(chunk, offset, end, result)
                    finally:
                        ended = time.monotonic(); timing[0] = None
                        record = {key: value for key, value in current.items()
                                  if key not in ('budget', 'begin', 'binding_depth')}
                        record.update(configured_bounded_seconds=budget.seconds, commit_window_seconds=ended-began,
                            begin_to_closed_seconds=0. if current['begin'] is None else ended-current['begin'],
                            total_budget_seconds=budget.elapsed,
                            committed=int(getattr(budget, 'sql_outcome', None)=='committed'),
                            rolled_back=int(getattr(budget, 'sql_outcome', None)=='rolled_back'))
                        h.timings.append(record)
                        if not natural_producer:
                            with capsys.disabled():print('bounded_synthetic_timing '+json.dumps(record, sort_keys=True), flush=True)
                    if accepted:
                        assert handoff['budget'] is result['_native_core_seam']['_bounded_budget']
                        h.committed_targets.append({'end': frame(chunk['timeline_start'] + end),
                            'targets': intent_rows(h),
                            'notice': h.store.chunk(chunk['id'])['metadata']['bounded_core_support']})
                    return accepted
                monkeypatch.setattr(h.store, 'commit_window', commit)
                # The inherited fixture manually schedules Worker.process, so
                # explicitly exercise its pre-claim metadata-only startup turn.
                h.automatic.maintain_bounded_waiters()
            yield h
    return open_app


def ingest(h):
    prefix = f'/sessions/{h.sid}/recordings/fresh-bounded-synthetic'
    response = h.client.put(prefix + '/sources', params={'source_start_frame': 900007},
        content=wav_bytes(0, 42 * RATE), headers={'Idempotency-Key': 'bounded-source'})
    assert response.status_code == 202, response.text
    response = h.client.post(prefix + '/finish', json={
        'idempotency_key': 'bounded-finish', 'expected_revision': response.json()['revision']})
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 12.
    while time.monotonic() < deadline:
        flight = h.worker.recordings._flight
        operation = flight['operation'] if flight is not None else h.owner._slot
        if operation is not None:assert operation.slot_released.wait(3.), operation.result
        h.worker.recordings.advance(h.capture)
        status = h.client.get(prefix)
        assert status.status_code == 200, status.text
        if status.json()['state'] == 'queued':break
        assert status.json()['state'] == 'pending_receipts', status.text
        time.sleep(max(.01, min(.1, h.worker.recordings._next_tick - time.monotonic())))
    else:pytest.fail('fresh four-chunk receipt outbox did not finish within its fixture bound')
    with h.store.db() as db:
        chunks = [dict(row) for row in db.execute('''SELECT c.* FROM chunks c
            JOIN recording_work w ON w.chunk_id=c.id WHERE c.session_id=? ORDER BY w.position''', (h.sid,))]
    assert [row['duration'] for row in chunks] == [10.5] * 4
    return chunks


def advance_to_waiter(h):
    chunks = ingest(h)
    for expected in chunks[:3]:assert process_primary(h) == expected['id']
    row = c_target(h)
    assert row['plan_phase'] == 'waiting' and row['state'] == 'queued'
    assert row['job_id'] is None and not h.drivers and h.jobs._live is None
    assert h.jobs.permit._optional is None and not h.owner._pins
    assert h.capture.analyzer is None and not h.capture.active
    plan = next(p for p in h.planning[-1]['plans'] if p['components'] == C_COMPONENTS)
    assert plan['reason'] == 'authority_unavailable' and not plan['has_pcm']
    assert h.planning[-1]['true_eof'] is False, 'native EOF lookahead is not committed EOF'
    target = json.loads(row['target_body'])
    assert target['identity']['components'] == C_COMPONENTS
    assert len(core.validate_target(target)[1]) == 2
    assert hashlib.sha256(target['origins'][0]['binding'].encode('ascii')).hexdigest() == row['candidate_key']
    return chunks, row


def assert_conservation(h, page):
    spans = [item for item in page['reading'] if item['source'] == 'primary_lexical']
    spans += [span for alternative in page['alternatives'] if alternative['reason'] == 'replaced_primary'
              for span in alternative['replaced_spans']]
    refs = [item['source_ref'] for item in page['reading'] if not item['reference_only']]
    assert len(refs) == len(set(refs))
    # Every eligible fixture support is represented once in the selected or
    # unselected alternatives. A whole-parent branch is permitted only when
    # none of those endpoints lies strictly inside that parent.
    supports = {support['candidate_key']: support
        for group in page['selected_runs'] + page['alternatives']
        for support in group.get('supports', []) if support['eligible']}
    assert {(s['start_frame'], s['end_frame']) for s in supports.values()} == {
        (frame(19.4), frame(21.7)), (frame(22.5), frame(31.6)), tuple(C_SUPPORT)}
    endpoints = {point for s in supports.values() for point in (s['start_frame'], s['end_frame'])}
    with h.store.db() as db:
        rows = [dict(row) for row in db.execute('''SELECT s.* FROM segments s JOIN chunks c
            ON c.id=s.chunk_id WHERE c.session_id=? ORDER BY s.id''', (h.sid,))]
        assert [(row['start'], row['end']) for row in rows] == [(18.8, 21.7), (22.5, 31.6), (40., 40.4)]
        for row in rows:
            saved = lexical.current_inventory(db, h.sid, row)
            assert saved is not None
            parent = transcript.primary_evidence(h.sid, row)
            matching = [item for item in spans
                if item.get('parent_source_ref') == parent['source_ref']]
            assert len(matching) == len({item['source_ref'] for item in matching})
            selected = {item['source_ref']: item for item in matching}
            ordered = sorted(selected.values(), key=lambda item: item['unit_range'][0])
            interior = any(parent['start_frame'] < point < parent['end_frame'] for point in endpoints)
            if not interior:
                assert not ordered  # The expected whole-parent fixture path.
                intact = [item for item in page['reading']
                    if item['source_ref'] == parent['source_ref']]
                replaced = [item for alternative in page['alternatives']
                    if alternative['reason'] == 'replaced_primary'
                    for item in alternative['primary_reading']
                    if item['source_ref'] == parent['source_ref']]
                assert len(intact) + len(replaced) == 1
                whole = (intact + replaced)[0]
                assert whole['source'] == 'primary' and whole['reference_only'] is False
                for key, value in parent.items():assert whole[key] == value
                for key in ('text', 'raw_text'):
                    assert whole[key].encode('utf-8') == parent[key].encode('utf-8')
                covering = [run for run in page['selected_runs']
                    if parent['source_ref'] in run['primary_source_refs']]
                if replaced:
                    assert len(covering) == 1
                    assert covering[0]['start_frame'] <= parent['start_frame']
                    assert parent['end_frame'] <= covering[0]['end_frame']
                else:assert covering == []
                continue
            # Parent1 is genuinely partially selected: full context carried in
            # primary_reading can never excuse a missing granular partition.
            assert ordered, parent['source_ref']
            assert ordered[0]['unit_range'][0] == 0
            assert ordered[-1]['unit_range'][1] == len(saved['inventory']['units'])
            assert all(a['unit_range'][1] == b['unit_range'][0] for a, b in zip(ordered, ordered[1:]))
            for field, extent in (('text', 'display_span'), ('raw_text', 'raw_span')):
                assert ordered[0][extent][0] == 0
                assert ordered[-1][extent][1] == len(parent[field])
                assert all(a[extent][1] == b[extent][0] for a, b in zip(ordered, ordered[1:]))
                assert ''.join(item[field] for item in ordered).encode('utf-8') == parent[field].encode('utf-8')


def test_natural_order_cross_upload_normal_producer_lifecycle(bounded_app_factory, capsys, monkeypatch):
    """Prospective normal producer route; a natural refusal is a failing result.

    Fixed page timing, native progress, opaque parents and origin order are not
    tuned to obtain admission. Seam 3/4 aliases the ordinary-3 owner while the
    owner-anchor prefix preserves its original [3,5] wait horizon.
    """
    overlap = [[332336, 336000]]
    selected = [310400, 505600]
    with bounded_app_factory(support_seconds=1.0, natural_producer=True) as h:
        # This supported 1.0s primary budget is explicitly NONDEFAULT. The
        # production .75s default and all non-opt-in fixture behavior stay put.
        assert h.store.settings.native_owned_bounded_core_support_seconds == 1.0
        assert (h.store.settings.window_seconds, h.store.settings.context_seconds) == (30., 2.)
        h.store.settings.api_key = 'synthetic-natural-producer-token'
        h.client.headers['Authorization'] = 'Bearer ' + h.store.settings.api_key
        prefix = f'/sessions/{h.sid}/recordings/natural-producer-cross-upload'
        observations = []; diagnostic_bytes = [0]; uploads = []

        def emit(value):
            # Bounded scalar evidence only, including the exact retained reason.
            # No proof, identifier, SQL body, text payload or PCM is printed.
            wire = 'natural_producer ' + json.dumps(value, sort_keys=True, separators=(',', ':'))
            diagnostic_bytes[0] += len(wire.encode('ascii')) + 1
            if diagnostic_bytes[0] >= 8192:
                pytest.fail('natural producer scalar diagnostics exceeded the fixed 8KiB limit')
            with capsys.disabled():print(wire, flush=True)

        def publish_page(number, start, end, eos):
            response = h.client.post(prefix + '/pages',
                json={'page_number': number, 'capture_origin_frame': 0})
            assert response.status_code == 202, response.text
            page = prefix + f'/pages/{number}'
            response = h.client.put(page + '/sources', params={'source_start_frame': start},
                content=wav_bytes(start, end), headers={'Idempotency-Key': f'natural-upload-{number}'})
            assert response.status_code == 202, response.text
            uploads.append(response.json()['source_id'])
            response = h.client.post(page + '/seal', json={
                'idempotency_key': f'natural-seal-{number}',
                'expected_revision': response.json()['revision'], 'end_frame': end, 'eos': eos})
            assert response.status_code == 202, response.text
            deadline = time.monotonic() + 12.
            while time.monotonic() < deadline:
                flight = h.worker.recordings._flight
                operation = flight['operation'] if flight is not None else h.owner._slot
                if operation is not None:assert operation.slot_released.wait(3.), operation.result
                h.worker.recordings.advance(h.capture)
                response = h.client.get(page)
                assert response.status_code == 200, response.text
                if response.json()['state'] == 'published':break
                assert response.json()['state'] in ('sealed', 'preparing', 'pending_receipts'), response.text
                time.sleep(max(.01, min(.1, h.worker.recordings._next_tick-time.monotonic())))
            else:pytest.fail('natural producer page receipts did not publish within the fixture bound')
            with h.store.db() as db:
                return [dict(row) for row in db.execute('''SELECT c.* FROM chunks c
                    JOIN recording_work w ON w.chunk_id=c.id
                    WHERE c.session_id=? ORDER BY w.position''', (h.sid,))]

        commit_window = h.store.commit_window
        def observe_commit(chunk, offset, end, result):
            accepted = commit_window(chunk, offset, end, result)
            if not accepted:return accepted
            handoff = result['_native_bounded_support']
            with h.store.db() as db:
                compacts = [dict(row) for row in db.execute('''SELECT slot,commit_ordinal
                    FROM native_core_commits WHERE sid=? ORDER BY commit_ordinal''', (h.sid,))]
                ordinals = {row['slot']: row['commit_ordinal'] for row in compacts}
                usage = automatic.usage(db, h.sid)
            def origin_order(target):
                return [[ordinals.get(core.slot(c), 0) for c in origin['commits']]
                        for origin in target['origins']]
            def roots(target):
                return sorted({ordinal for group in origin_order(target) for ordinal in group})
            matching = [row for row in intent_rows(h) if row['target_body'] is not None
                        and resolved_target(row)['identity']['components'] == overlap]
            saved = []
            for row in matching:
                target = resolved_target(row)
                bounded = json.loads(row['proof']).get('schema') == core.BOUNDED_SCHEMA
                if bounded:automatic.validate_document(row['proof'])
                saved.append({'origins': origin_order(target), 'roots': roots(target),
                    'wait': [row['wait_start_ordinal'], row['wait_last_ordinal']],
                    'phase': row['plan_phase'], 'state': row['state'], 'reason': row['reason'],
                    'proof': len(row['proof']), 'full': len(automatic.canonical(target)),
                    # A waiting ordinary proof has no admissible bound reference.
                    'ref': len(automatic._bound_target_reference(row['proof'], target)) if bounded else None,
                    'stored': len(row['target_body']), 'charge': row['charge_bytes'],
                    'planning': row['planning_bytes']})
            plans = [{'origins': origin_order(plan['target']), 'roots': roots(plan['target']),
                'reason': plan.get('reason'), 'pcm': int('pcm' in plan),
                'support': plan.get('support'), 'full': len(automatic.canonical(plan['target']))}
                for plan in handoff['plans'] if plan['target']['identity']['components'] == overlap]
            part = result['_native_core_seam']['commit']['part']
            notice = h.store.chunk(chunk['id'])['metadata']['bounded_core_support']
            record = {'ordinal': handoff['batch']['ordinal'],
                'core': [part['logical_start'], part['logical_end']],
                'new_origins': [origin_order(item['target'])[0] for item in handoff['origins']],
                'plans': plans, 'saved': saved, 'usage': usage,
                'notice': {key: notice[key] for key in ('status', 'reason', 'capacity', 'registered', 'bound')
                           if key in notice}}
            observations.append(record); emit(record)
            # Accounting assertions follow the diagnostic, including on refusal.
            with h.store.db() as db:_reference_accounting(db, h.sid)
            return accepted
        monkeypatch.setattr(h.store, 'commit_window', observe_commit)

        chunks = publish_page(1, 0, 504000, False)
        assert len(chunks) == 3 and [row['duration'] for row in chunks] == [10.5] * 3
        assert process_primary(h) == chunks[0]['id']
        assert process_primary(h) == chunks[1]['id']
        partial = h.store.chunk(chunks[1]['id'])
        emit({'event': 'first_page_wait', 'offset': frame(partial['next_offset']),
              'state': partial['status'], 'progress': h.natives[0].progress,
              'analysis': [[*item['window'], len(item['units'])] for item in h.natural_analysis]})
        assert partial['status'] == 'waiting_input' and frame(partial['next_offset']) == 137920
        assert h.natives[0].progress == [[0, 0, 0, 0], [339200, 0, 0, 0],
                                         [504000, 337920, 305920, 0]]
        assert h.natural_analysis[-1]['window'] == [136000, 337920]
        assert h.natural_analysis[-1]['units'] == [], 'P1 is not yet wholly inside the actual extraction'
        assert len(observations) == 2 and not intent_rows(h)
        # Only the observed stable19.12 wait permits publishing the second page.
        chunks = publish_page(2, 504000, 1008000, True)
        assert len(uploads) == len(set(uploads)) == 2
        assert len(chunks) == 6 and [row['duration'] for row in chunks] == [10.5] * 6
        assert process_primary(h) == chunks[1]['id']
        for chunk in chunks[2:]:assert process_primary(h) == chunk['id']

        # Diagnose the entire genuine producer trajectory before asking for a
        # successful binding. Never discard seam3/4 or repair the saved horizon.
        matching = [row for row in intent_rows(h) if row['target_body'] is not None
                    and resolved_target(row)['identity']['components'] == overlap]
        emit({'event': 'primary_done', 'count': len(matching),
              'progress': h.natives[0].progress, 'queries': h.natives[0].queries,
              'phases': [[row['plan_phase'], row['state'], row['reason']] for row in matching]})
        assert [item['core'] for item in observations[:5]] == [
            [0, 168000], [168000, 305920], [305920, 336000],
            [336000, 504000], [504000, 672000]]
        assert len(observations) == 7
        assert len(h.natives) == 1 and h.natives[0].progress == [
            [0, 0, 0, 0], [339200, 0, 0, 0], [504000, 337920, 305920, 0],
            [843200, 675840, 643840, 0], [1008000, 855040, 823040, 0],
            [1008000, 1008000, 1008000, 1]]
        assert h.natives[0].calls.count('reset') == h.natives[0].calls.count('finish') == 1
        assert observations[2]['new_origins'] == [[3], [2, 3]]
        assert observations[2]['saved'][0]['origins'] == [[3], [2, 3]]
        assert observations[2]['saved'][0]['roots'] == [2, 3]
        assert observations[2]['saved'][0]['wait'] == [3, 5]
        assert observations[3]['new_origins'] == [[3, 4]], 'the natural same-target alias must remain'
        assert observations[3]['saved'][0]['roots'] == [2, 3, 4]
        assert observations[3]['saved'][0]['wait'] == [3, 5]
        # Every supplied unit came from a complete actual analyzer extraction.
        for item in h.natural_analysis:
            a, b = item['window']
            assert all(a <= lo < hi <= b for lo, hi, _ in item['units'])
            assert item['sources'] and sum(p['analyzer_frames'][1]-p['analyzer_frames'][0]
                                           for p in item['sources']) == b-a
        with h.store.db() as db:
            parents = [dict(row) for row in db.execute('''SELECT s.* FROM segments s
                JOIN chunks c ON c.id=s.chunk_id WHERE c.session_id=? ORDER BY s.start,s.id''', (h.sid,))]
            assert [(transcript.primary_frame(row['start']), transcript.primary_frame(row['end'], end=True),
                     row['text'], row['raw_text']) for row in parents] == [
                (310400, 347200, '甲乙', '甲乙'), (344000, 505600, '丙丁', '丙丁')]
            assert [row['chunk_id'] for row in parents] == [chunks[1]['id'], chunks[2]['id']]
            parent_evidence = []
            for index, row in enumerate(parents):
                current = lexical.current_inventory(db, h.sid, row)
                assert current is not None and len(current['inventory']['units']) == 1
                inventory = current['inventory']
                assert inventory['source_parts'] == h.natural_analysis[index+2]['sources']
                origin = chunks[index+1]['timeline_start']
                # Repeat the normal analyzer -> lexical_core -> commit_window
                # arithmetic order. Do not round stored floats to the literals
                # 19.4/21.7/21.5/31.6 or mutate their whole-row proof evidence.
                window = h.natural_analysis[index+2]['window']
                core_start = [305920, 336000][index]
                offset = (core_start-frame(origin))/RATE
                base = (window[0]-frame(origin))/RATE
                owned_start = (offset-base)+base
                expected = [origin+offset+((point-window[0])/RATE+base-owned_start)
                            for point in [(310400, 347200), (344000, 505600)][index]]
                assert [row['start'], row['end']] == expected
                assert [frame(origin+inventory['coordinates'][key]) for key in
                        ('core_start', 'core_end')] == [[305920, 336000], [336000, 504000]][index]
                unit = current['inventory']['units'][0]
                assert unit['anchor_kind'] == 'ctc_emission' and unit['ownership_anchor'] == unit['start']
                assert unit['granular_eligible'] is False
                parent_evidence.append(transcript.primary_evidence(h.sid, row))
        assert len(matching) == 1
        bound = matching[0]
        target = resolved_target(bound)
        ordinary = json.loads(target['origins'][0]['proof'])
        assert [ordinary['support']['start_frame'], ordinary['support']['end_frame']] == [305920, 336000]
        assert ordinary['support']['start_frame'] < 310400 < ordinary['support']['end_frame'] < 347200
        # This is deliberately a positive route gate, not an expected-refusal
        # test. The preceding scalar records preserve the exact natural failure.
        assert bound['plan_phase'] == 'bound', json.dumps({
            'phase': bound['plan_phase'], 'state': bound['state'], 'reason': bound['reason'],
            'wait': [bound['wait_start_ordinal'], bound['wait_last_ordinal']],
            'last_notice': observations[-1]['notice']}, sort_keys=True)

        doc, support, parts = automatic.validate_document(bound['proof'])
        owner_key = bound['candidate_key']; proof = bound['proof']
        full = automatic.canonical(doc['target'])
        reference = automatic._bound_target_reference(proof, doc['target'])
        emit({'event': 'bound_sizes', 'proof': len(proof), 'full': len(full), 'ref': len(reference),
              'full_combined_fits': int(len(proof)+len(full) <= 65536),
              'reference_combined_fits': int(len(proof)+len(reference) <= 65536)})
        assert proof == automatic.canonical(doc) and full == automatic.canonical(target)
        assert bound['target_body'] == reference
        assert max(len(proof), len(full), len(reference)) <= 65536
        assert len(proof) + len(reference) <= 65536
        assert doc['schema'] == core.BOUNDED_SCHEMA
        assert [support.start_frame, support.end_frame] == selected
        assert doc['geometry'] == {'start_frame': selected[0], 'end_frame': selected[1]}
        with h.store.db() as db:
            ordinal_by_slot = {row['slot']: row['commit_ordinal'] for row in db.execute(
                'SELECT slot,commit_ordinal FROM native_core_commits WHERE sid=?', (h.sid,))}
            assert [ordinal_by_slot[core.slot(c)] for c in doc['commits']] == [2, 3, 4, 5]
            assert [max(0, min(selected[1], c['part']['logical_end'])-
                max(selected[0], c['part']['logical_start'])) for c in doc['commits']] == [0, 25600, 168000, 1600]
            assert any(root['slot'] == core.slot(doc['commits'][0]) and root['kind'] & 1
                       for root in doc['dependency_roots'])
            for root in doc['dependency_roots']:
                assert automatic.retention_root(db, cid=root['cid'])
                if root['slot'] is not None:assert automatic.retention_root(db, slot=root['slot'])
            ranges = [dict(row) for row in db.execute('''SELECT r.*,s.path FROM recording_ranges r
                JOIN recording_sources s ON s.id=r.source_id JOIN recording_batches b ON b.id=r.batch_id
                WHERE b.session_id=? AND r.relation='owner' ORDER BY r.capture_start_frame''', (h.sid,))]
        counts = {source_id: 0 for source_id in uploads}; original_pcm = bytearray()
        for item in ranges:
            a = max(selected[0], item['capture_start_frame'])
            b = min(selected[1], item['capture_start_frame']+item['frames'])
            if a >= b:continue
            counts[item['source_id']] += b-a
            with wave.open(item['path'], 'rb') as source:
                source.setpos(item['source_start_frame']+a-item['capture_start_frame'])
                original_pcm.extend(source.readframes(b-a))
        assert [counts[source_id] for source_id in uploads] == [193600, 1600]
        assert bytes(original_pcm) == fixture_pcm(*selected)
        assert doc['support']['pcm16le_sha256'] == hashlib.sha256(original_pcm).hexdigest()
        query = doc['query']
        assert [query['query_start_frame'], query['query_end_frame']] in h.natives[0].queries
        assert query['query_pcm16le_sha256'] == hashlib.sha256(fixture_pcm(
            query['query_start_frame'], query['query_end_frame'])).hexdigest()
        with Budget().phase() as budget:
            assert automatic.authority_valid(h.capture.authority, owner_key, doc, budget)
        with Budget().phase() as budget:
            assert not automatic.authority_valid(h.capture.authority, '0' * 64, doc, budget)
        # Read the real reference-form owner row during actual admission; no
        # equivalent-wire switching or target/compact/manual enqueue is used.
        admitted = []; cell_current = h.owner._cell_current
        def reference_admission(op, cell, budget):
            if op.kind == 'publish_auto' and op.payload[0] == owner_key:
                row = next(row for row in intent_rows(h) if row['candidate_key'] == owner_key)
                admitted.append([len(row['proof']), len(row['target_body']), row['target_body'] == reference])
                assert row['proof'] == proof and row['target_body'] == reference
                assert sum(admitted[-1][:2]) <= 65536
            return cell_current(op, cell, budget)
        monkeypatch.setattr(h.owner, '_cell_current', reference_admission)
        primary = primary_output(h)
        drain_automatic(h)
        completed = next(row for row in intent_rows(h) if row['candidate_key'] == owner_key)
        emit({'event': 'admitted', 'calls': admitted, 'proof': len(proof),
              'full': len(full), 'ref': len(reference), 'state': completed['state']})
        assert completed['state'] == 'committed' and completed['job_id'] is not None
        assert len(admitted) == 3 and all(item[2] for item in admitted)
        assert len(h.drivers) == sum(driver.starts for driver in h.drivers) == 1
        with h.store.db() as db:
            jobs = db.execute(f'SELECT job_id FROM {history.TABLE} WHERE session_id=?', (h.sid,)).fetchall()
        assert [row[0] for row in jobs] == [completed['job_id']]
        proof_url = f"/sessions/{h.sid}/native-owned-cells/{completed['cell_id']}"
        assert h.client.get(proof_url, headers={'Authorization': ''}).status_code == 401
        response = h.client.get(proof_url)
        assert response.status_code == 200 and response.content == proof
        assert response.headers['X-Native-Cell-Proof-SHA256'] == hashlib.sha256(proof).hexdigest()
        read = h.owner.submit('read', completed['cell_id'])
        assert read.slot_released.wait(3.) and read.result['status'] == 'available'
        pin = read.result['pin']
        try:assert pin.read() == fixture_pcm(*selected)
        finally:pin.release()
        result_url = f"/sessions/{h.sid}/moss-jobs/history/{completed['job_id']}/result"
        response = h.client.get(result_url)
        assert response.status_code == 200
        assert response.content == h.jobs.results.result(h.sid, completed['job_id'])
        qualified = []
        for params in ({}, {'projection': 'lexical-estimated-v1'}):
            response = h.client.get(f'/sessions/{h.sid}/transcript', params=params)
            assert response.status_code == 200 and len(response.content) <= transcript.MAX_RESPONSE_BYTES
            page = response.json()
            runs = [run for run in page['selected_runs'] if owner_key in run['candidate_keys']]
            assert len(runs) == 1 and runs[0]['candidate_keys'] == [owner_key]
            assert [runs[0]['start_frame'], runs[0]['end_frame']] == selected
            candidates = [item for item in runs[0]['supports'] if item['candidate_key'] == owner_key]
            assert len(candidates) == 1 and candidates[0]['eligible']
            assert candidates[0]['job_id'] == completed['job_id']
            qualified.append({key: candidates[0][key] for key in
                ('candidate_key', 'job_id', 'result_sha256', 'start_frame', 'end_frame', 'eligible')})
            represented = [item for item in page['reading'] if item['source'] == 'primary']
            represented += [item for alt in page['alternatives'] if alt['reason'] == 'replaced_primary'
                            for item in alt['primary_reading']]
            for parent in parent_evidence:
                same = [item for item in represented if item['source_ref'] == parent['source_ref']]
                assert len(same) == 1 and same[0]['reference_only'] is False
                for key, value in parent.items():assert same[0][key] == value
                for key in ('text', 'raw_text'):
                    assert same[0][key].encode('utf-8') == parent[key].encode('utf-8')
            assert not [item for item in page['reading'] if item['source'] == 'primary_lexical']
        assert qualified[0] == qualified[1]
        assert primary_output(h) == primary


def test_c_full_target_wait_bind_publish_result_transcript_and_disabled_restart(bounded_app_factory, capsys, monkeypatch):
    with bounded_app_factory(support_seconds=1.0) as h:
        # Supported nondefault correctness qualification; run75 retains the
        # observed .75-second default finalization refusal unchanged.
        assert h.store.settings.native_owned_bounded_core_support_seconds == 1.0
        chunks, waiting = advance_to_waiter(h)
        original_target = waiting['target_body']; owner_key = waiting['candidate_key']
        assert process_primary(h) == chunks[-1]['id']
        assert h.timings[-1]['binding_calls'] > 0
        assert h.timings[-1]['bounded_validation_calls'] == h.timings[-1]['binding_calls']
        bound = c_target(h); doc = json.loads(bound['proof'])
        assert bound['candidate_key'] == owner_key
        assert automatic.canonical(resolved_target(bound)) == original_target
        assert resolved_target(bound)['origins'] == json.loads(original_target)['origins']
        assert bound['target_body'] == automatic._bound_target_reference(bound['proof'], doc['target'])
        assert json.loads(bound['target_body'])['proof_sha256'] == bound['proof_sha256']
        assert bound['plan_phase'] == 'bound' and doc['schema'] == core.BOUNDED_SCHEMA
        assert doc['inventories']['schema'] == core.SUPPORT_LOCAL_INVENTORY_SCHEMA
        assert doc['inventories']['scope_policy'] == core.SUPPORT_LOCAL_SCOPE_POLICY
        assert 'atoms' not in doc['inventories']
        assert all(set(parent) == {'row'} for parent in doc['inventories']['parents'])
        assert len(bound['proof']) + len(bound['target_body']) <= 65536
        with capsys.disabled():print('compact_c_envelope '+json.dumps({
            'inventory_bytes': len(core.canonical(doc['inventories'])),
            'proof_bytes': len(bound['proof']), 'target_bytes': len(bound['target_body']),
            'combined_bytes': len(bound['proof']) + len(bound['target_body'])}, sort_keys=True), flush=True)
        assert [doc['geometry']['start_frame'], doc['geometry']['end_frame']] == C_SUPPORT
        assert doc['target']['identity']['components'] == C_COMPONENTS
        assert len(doc['commits']) == 3
        assert all(parent['row']['id'] < 1000 for parent in doc['inventories']['parents'])
        assert doc['support']['pcm16le_sha256'] == hashlib.sha256(fixture_pcm(*C_SUPPORT)).hexdigest()
        assert any(qa <= C_SUPPORT[0] < C_SUPPORT[1] <= qb for qa, qb in h.natives[0].queries)
        assert h.natives[0].calls.count('reset') == h.natives[0].calls.count('finish') == 1
        with Budget().phase() as budget:
            assert automatic.authority_valid(h.capture.authority, owner_key, doc, budget)
        primary = primary_output(h)
        reference_wire = bound['target_body']; full_wire = automatic.canonical(doc['target'])
        assert len(bound['proof']) + len(full_wire) <= 65536
        expected_wires = (reference_wire, full_wire, reference_wire)
        current = h.owner._cell_current; switched = []; owner_epoch = h.owner._epoch
        def equivalent_wire_current(op, cell, budget):
            if cell.cell_id != bound['cell_id']:return current(op, cell, budget)
            assert op.kind == 'publish_auto' and op.payload[0] == owner_key
            assert h.owner._epoch is owner_epoch and len(switched) < 3
            wire = expected_wires[len(switched)]
            _switch_owner_reference_wire(h, owner_key, wire)
            # This is the real owner callback, including current epoch, exact
            # receipts/files, all roots and the shared owner deadline/budget.
            accepted = current(op, cell, budget)
            assert h.owner._epoch is owner_epoch
            switched.append((wire, accepted))
            return accepted
        with monkeypatch.context() as patch:
            patch.setattr(h.owner, '_cell_current', equivalent_wire_current)
            drain_automatic(h)
        assert [wire for wire, _ in switched] == list(expected_wires)
        assert all(accepted is True for _, accepted in switched)
        completed = c_target(h)
        assert completed['state'] == 'committed' and completed['job_id'] is not None
        assert completed['target_body'] == reference_wire and h.owner._epoch is owner_epoch
        counts = [record for cell_id, record in h.publications if cell_id == completed['cell_id']]
        assert len(counts) == 1 and counts[0]['publish_returned_durable'] == 1
        assert counts[0]['fresh_boundaries'] == counts[0]['certificate_reads'] == 3
        assert counts[0]['comparison_parameter_count'] == 6
        assert counts[0]['comparison_input_bytes'] == 3*len(bound['proof'])+2*len(reference_wire)+len(full_wire)
        assert counts[0]['total_projected_bytes'] <= 262144
        with h.store.db() as db:
            jobs = db.execute(f'''SELECT r.job_id FROM {history.TABLE} r
                JOIN {automatic.TABLE} a ON a.job_id=r.job_id
                WHERE a.candidate_key=?''', (owner_key,)).fetchall()
        assert [row[0] for row in jobs] == [completed['job_id']]
        # All dispatches use the one existing owner/controller path; one target
        # owns one job even though other legitimate original targets coexist.
        assert sum(driver.starts for driver in h.drivers) == len(h.drivers)
        read = h.owner.submit('read', completed['cell_id'])
        assert read.slot_released.wait(3.) and read.result['status'] == 'available'
        pin = read.result['pin']
        try:assert pin.read() == fixture_pcm(*C_SUPPORT)
        finally:pin.release()
        response = h.client.get(f'/sessions/{h.sid}/transcript', params={'projection': 'lexical-estimated-v1'})
        assert response.status_code == 200, response.text
        assert len(response.content) <= transcript.MAX_RESPONSE_BYTES
        (h.owner.directory.parent / 'c-transcript-response.json').write_bytes(response.content)
        page = response.json()
        selected = [key for run in page['selected_runs'] for key in run['candidate_keys']]
        assert owner_key in selected and len(selected) == len(set(selected))
        assert_conservation(h, page)
        assert primary_output(h) == primary
        with h.store.db() as db:
            for root in doc['dependency_roots']:
                assert automatic.retention_root(db, cid=root['cid'])
                if root['slot'] is not None:assert automatic.retention_root(db, slot=root['slot'])
        sid = h.sid; saved = h.jobs.results.result(sid, completed['job_id'])
        result_url = f"/sessions/{sid}/moss-jobs/history/{completed['job_id']}/result"
        ledger = intent_rows(h); starts = sum(driver.starts for driver in h.drivers)
    with bounded_app_factory(sid=sid, enabled=False) as restarted:
        assert restarted.jobs is None and not restarted.automatic.current()
        response = restarted.client.get(result_url)
        assert response.status_code == 200 and response.content == saved
        assert intent_rows(restarted) == ledger
        assert primary_output(restarted) == primary
        assert not restarted.drivers and not restarted.natives
        assert starts >= 1


@pytest.mark.parametrize('last_end,expected_roots,terminal', [
    (33.2, 4, False), (34.2, None, True)])
def test_four_core_acceptance_and_fifth_short_core_refusal(bounded_app_factory, last_end, expected_roots, terminal):
    with bounded_app_factory(last_group_end=last_end) as h:
        chunks, waiting = advance_to_waiter(h)
        # The remaining genuine 10.5-second work source commits one-second
        # cores. This declared input policy is fixed before any later output.
        h.store.settings.window_seconds = 1.
        assert process_primary(h) == chunks[-1]['id']
        row = c_target(h)
        assert row['candidate_key'] == waiting['candidate_key']
        assert frame(last_end - 19.4) < 20 * RATE
        if terminal:
            assert row['plan_phase'] == 'closed' and row['job_id'] is None
            assert row['reason'] in ('commitment_capacity', 'unresolved_commit_horizon')
            assert not h.drivers
        else:
            assert row['plan_phase'] == 'bound'
            doc = json.loads(row['proof'])
            assert len(doc['commits']) == expected_roots
            assert doc['geometry'] == {'start_frame': frame(19.4), 'end_frame': frame(last_end)}
            assert doc['target']['identity']['components'] == C_COMPONENTS
            assert doc['support']['pcm16le_sha256'] == hashlib.sha256(
                fixture_pcm(frame(19.4), frame(last_end))).hexdigest()


def test_actual_retention_refusal_preserves_primary_and_cannot_bind(bounded_app_factory, monkeypatch):
    with bounded_app_factory() as h:
        chunks, waiting = advance_to_waiter(h)
        # A real retained-inventory refusal on the arriving core cannot be
        # repaired by trusting the earlier prospective draft or cropping again.
        monkeypatch.setattr(lexical, 'persist', lambda *args, **kwargs: 'session_capacity')
        assert process_primary(h) == chunks[-1]['id']
        row = c_target(h)
        assert row['candidate_key'] == waiting['candidate_key']
        assert row['plan_phase'] != 'bound' and row['job_id'] is None
        current = h.store.chunk(chunks[-1]['id'])
        assert current['status'] == 'completed'
        notice = current['metadata']['bounded_core_support']
        assert notice['owned_targets_preserved'] is True
        assert notice['status'] == 'waiting'
        assert notice['reason'] == 'bounded_inventory_not_retained'
        with h.store.db() as db:
            last = db.execute('SELECT text,lexical_evidence FROM segments WHERE chunk_id=?',
                              (chunks[-1]['id'],)).fetchall()
        assert last and all(item['lexical_evidence'] is None for item in last)
        assert not h.drivers and h.jobs._live is None and not h.owner._pins


def test_hard_optional_sql_error_rolls_back_primary_and_target_state(bounded_app_factory):
    with bounded_app_factory() as h:
        chunks, waiting = advance_to_waiter(h)
        before = intent_rows(h)
        with h.store.db() as db:
            db.execute(f'''CREATE TRIGGER bounded_synthetic_full BEFORE UPDATE OF plan_phase
                ON {automatic.TABLE} WHEN NEW.plan_phase='bound'
                BEGIN SELECT RAISE(ABORT,'synthetic hard optional SQL error'); END''')
        with pytest.raises(sqlite3.DatabaseError, match='synthetic hard optional SQL error'):
            process_primary(h)
        assert intent_rows(h) == before
        last = h.store.chunk(chunks[-1]['id'])
        assert last['next_offset'] == 0.
        with h.store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM segments WHERE chunk_id=?', (last['id'],)).fetchone()[0] == 0
        assert not h.drivers and h.jobs._live is None


def test_heavy_primary_sql_uses_same_budget_and_rolls_back_every_write(bounded_app_factory, monkeypatch):
    with bounded_app_factory() as h:
        chunks, waiting = advance_to_waiter(h)
        before = intent_rows(h)
        with h.store.db() as db:
            ordinal = db.execute('''SELECT native_core_ordinal FROM recording_batches
                WHERE session_id=?''', (h.sid,)).fetchone()[0]
            compacts = db.execute('SELECT COUNT(*) FROM native_core_commits WHERE sid=?', (h.sid,)).fetchone()[0]
            # This bounded arithmetic workload is inside actual primary SQL,
            # before optional finalization. No budget cap is changed or reset.
            db.execute('''CREATE TRIGGER bounded_heavy_primary BEFORE INSERT ON segments
                BEGIN SELECT sum(n) FROM
                    (WITH RECURSIVE work(n) AS (VALUES(1) UNION ALL
                     SELECT n+1 FROM work WHERE n<100000) SELECT n FROM work);
                END''')
        observed = []; commit = h.store.commit_window
        def counted(chunk, offset, end, result):
            budget = result['_native_bounded_support']['budget']
            # Claiming already advanced the public chunk-status revision in a
            # separate transaction; only the impending primary writes roll back.
            with h.store.db() as db:
                revision = db.execute('SELECT transcript_revision FROM sessions WHERE id=?', (h.sid,)).fetchone()[0]
            observed.append((budget, budget.steps, revision))
            return commit(chunk, offset, end, result)
        monkeypatch.setattr(h.store, 'commit_window', counted)
        with pytest.raises(sqlite3.OperationalError, match='interrupted'):
            process_primary(h)
        assert len(observed) == 1
        budget, prepared_steps, revision = observed[0]
        assert prepared_steps > 0 and budget.steps > prepared_steps
        assert intent_rows(h) == before and c_target(h)['candidate_key'] == waiting['candidate_key']
        assert h.store.chunk(chunks[-1]['id'])['next_offset'] == 0.
        with h.store.db() as db:
            assert db.execute('SELECT transcript_revision FROM sessions WHERE id=?', (h.sid,)).fetchone()[0] == revision
            assert db.execute('SELECT native_core_ordinal FROM recording_batches WHERE session_id=?', (h.sid,)).fetchone()[0] == ordinal
            assert db.execute('SELECT COUNT(*) FROM native_core_commits WHERE sid=?', (h.sid,)).fetchone()[0] == compacts
            assert db.execute('SELECT COUNT(*) FROM segments WHERE chunk_id=?', (chunks[-1]['id'],)).fetchone()[0] == 0
        assert not h.drivers and h.jobs._live is None and not h.owner._pins


@pytest.mark.parametrize('error,reason', [
    (Unavailable('bounded_planning_capacity'), 'bounded_planning_capacity'),
    (Unavailable('bounded_clock_capacity'), 'bounded_clock_capacity'),
    (ReadError(503, 'result_storage_unavailable'), 'result_storage_unavailable'),
    (sqlite3.OperationalError('synthetic uncertain metadata write'), 'bounded_waiter_maintenance_unavailable')])
def test_waiter_maintenance_refusal_does_not_prevent_primary_claim(bounded_app_factory, monkeypatch, error, reason):
    with bounded_app_factory() as h:
        class ReachedPrimaryClaim(Exception):pass
        before = intent_rows(h); startup = h.automatic._bounded_startup
        deadlines = dict(h.automatic._bounded_deadlines); reached = []
        def refused(**kwargs):raise error
        def claim(*args, **kwargs):
            reached.append(True)
            raise ReachedPrimaryClaim()
        monkeypatch.setattr(h.automatic, 'maintain_bounded_waiters', refused)
        monkeypatch.setattr(h.store, 'claim', claim)
        with pytest.raises(ReachedPrimaryClaim):h.worker.loop()
        assert reached == [True] and h.automatic.last_reason == reason
        assert intent_rows(h) == before
        assert h.automatic._bounded_startup is startup
        assert h.automatic._bounded_deadlines == deadlines
        assert not h.drivers and not h.natives and h.jobs._live is None
        assert h.jobs.permit._optional is None and not h.owner._pins


def test_compact_inventory_old_validator_refuses_before_owner_admission(bounded_app_factory, monkeypatch):
    from audio_backend import native_owned_cells as cells
    from test_bounded_core_support import source60_inventory_oracle
    with bounded_app_factory() as h:
        chunks, _ = advance_to_waiter(h)
        assert process_primary(h) == chunks[-1]['id']
        row = c_target(h); doc, support, parts = cells.validate_document(row['proof'])
        assert doc['inventories']['schema'] == core.SUPPORT_LOCAL_INVENTORY_SCHEMA
        assert doc['inventories']['scope_policy'] == core.SUPPORT_LOCAL_SCOPE_POLICY
        oracle = source60_inventory_oracle(); anchor = core.anchor(doc['commits'][-1])
        submitted = []; submit = h.owner.submit
        def observed_submit(kind, payload, *args, **kwargs):
            if kind == 'publish_auto':submitted.append(kind)
            return submit(kind, payload, *args, **kwargs)
        monkeypatch.setattr(h.owner, 'submit', observed_submit)
        # The pinned source60 validator refuses this exact candidate before a
        # Cell can reach the owner. This does not simulate an entire old binary.
        with monkeypatch.context() as old:
            old.setattr(core, 'validate_bounded_document', oracle['validate_bounded_document'])
            with pytest.raises(Unavailable, match='^bounded_inventory_policy$'):
                cell = cells.Cell(support, parts, row['proof'], fixture_pcm(*C_SUPPORT), h.sid,
                    anchor['chunk_id'], anchor['start'], anchor['end'], anchor['sha256'])
                h.owner.submit('publish_auto', (row['candidate_key'], cell))
        assert not submitted and not h.publications
        assert c_target(h) == row
        assert row['state'] == 'queued' and row['job_id'] is None
        assert not h.drivers and h.jobs._live is None and not h.owner._pins


@pytest.mark.parametrize('boundary', [1, 2, 3])
def test_compact_inventory_each_fresh_boundary_rejects_current_row_or_source_mutation(
        bounded_app_factory, monkeypatch, boundary):
    from audio_backend import native_owned_cells as cells
    from audio_backend.native_owned_cell_store import Operation
    with bounded_app_factory(support_seconds=1.0) as h:
        # Supported nondefault correctness qualification; run75 retains the
        # observed .75-second default finalization refusal unchanged.
        assert h.store.settings.native_owned_bounded_core_support_seconds == 1.0
        chunks, _ = advance_to_waiter(h)
        assert process_primary(h) == chunks[-1]['id']
        row = c_target(h); doc, support, parts = cells.validate_document(row['proof'])
        assert doc['inventories']['schema'] == core.SUPPORT_LOCAL_INVENTORY_SCHEMA
        assert doc['inventories']['scope_policy'] == core.SUPPORT_LOCAL_SCOPE_POLICY
        anchor = core.anchor(doc['commits'][-1])
        cell = cells.Cell(support, parts, row['proof'], fixture_pcm(*C_SUPPORT), h.sid,
            anchor['chunk_id'], anchor['start'], anchor['end'], anchor['sha256'])
        # A new outside-support parent is discovered by the same fresh query
        # but cannot revoke the selected whole-parent closure.
        with h.store.db() as db:
            cid=doc['commits'][0]['part']['chunk_id']
            db.execute("INSERT INTO segments(chunk_id,start,end,text,speaker_status) VALUES(?,0.,.5,'outside support','unknown')",(cid,))
        with Budget().phase() as budget:
            assert automatic.authority_valid(h.capture.authority,row['candidate_key'],doc,budget)
        original = h.owner._cell_current; calls = []
        def mutate(op, proposed, budget):
            calls.append(True)
            if len(calls) == boundary:
                with h.store.db() as db:
                    if boundary == 2:
                        source = doc['inventories']['sources'][0]
                        replacement = ('e' if source['digest'] == 'f' * 64 else 'f') * 64
                        assert db.execute('UPDATE chunks SET digest=? WHERE id=?',
                            (replacement, source['id'])).rowcount == 1
                    else:
                        parent = doc['inventories']['parents'][0]['row']
                        assert db.execute('UPDATE segments SET text=? WHERE id=?',
                            (parent['text'] + '甲', parent['id'])).rowcount == 1
            return original(op, proposed, budget)
        monkeypatch.setattr(h.owner, '_cell_current', mutate)
        operation = h.owner.submit('publish_auto', (row['candidate_key'], cell))
        assert isinstance(operation, Operation) and operation.slot_released.wait(3.)
        assert operation.result['status'] == 'unknown'
        assert operation.result['reason'] == {1: 'primary_commit_or_source_unavailable',
            2: 'bounded_inventory_source_changed',
            3: 'source_changed_after_publication_commit'}[boundary]
        assert len(calls) == boundary
        db = sqlite3.connect(h.owner.path.as_uri() + '?mode=ro', uri=True)
        try:retained = db.execute('SELECT proof FROM cells WHERE cell_id=?', (cell.cell_id,)).fetchone()
        finally:db.close()
        assert retained is None if boundary < 3 else retained[0] == row['proof']
        assert all(record['publish_returned_durable'] == 0 for _, record in h.publications)
        assert c_target(h)['state'] == 'queued' and c_target(h)['job_id'] is None
        assert not h.drivers and h.jobs._live is None and not h.owner._pins


@pytest.fixture
def support_local_ledger_factory(tmp_path):
    """Tiny real schemas; default explicitly retains historical full-bound rows."""
    from audio_backend import storage, transcript_revision
    from audio_backend import native_owned_cells as cells
    from test_bounded_core_support import SID
    used = []
    def create(schema=None, *, reference=False, waiting=False, fixed=False):
        directory = tmp_path / ('local-ledger-' + str(len(used)))
        directory.mkdir()
        store = storage.Store(directory / 'primary.sqlite3', Settings(data_dir=directory, start_worker=False))
        with store.db() as db:
            core.init_schema(db)
            db.execute('INSERT INTO sessions(id,name,identity_group,created,timeline_end) VALUES(?,?,?,1.,1.)',
                (SID, 'synthetic support-local', SID))
            db.execute('BEGIN IMMEDIATE') if not db.in_transaction else None
            automatic.init_bounded_schema(db, enable=True); transcript_revision.init_bounded_schema(db)
            value = _support_local_seed(db, schema, reference=reference, waiting=waiting, fixed=fixed)
        value.store = store; used.append(value)
        return value
    return create


def _support_local_parent(case):
    from test_bounded_core_support import SID, primary_row
    row = primary_row(1, 16, 40, cid='source-1', text='甲乙')
    parts = []
    for index, cid in enumerate(('source-1', 'source-2')):
        source = case['sources'][cid]
        parts.append({**source, 'seek_seconds': 0., 'requested_seconds': source['duration'],
                      'analyzer_frames': [index * 16, (index + 1) * 16]})
    units = []
    for index, (a, b, text) in enumerate(((0, 8, '甲'), (12, 24, '乙'))):
        units.append({'start': a/RATE, 'end': b/RATE, 'ownership_anchor': a/RATE,
            'text': text, 'raw_text': text, 'anchor_kind': 'ctc_emission',
            'display_span': [index, index+1], 'raw_span': [index, index+1], 'granular_eligible': True})
    raw = {'schema': lexical.SCHEMA, 'primary_sha256': lexical.primary_sha(SID, row),
        'source_provenance': lexical.SOURCE_NOTE, 'source_parts': parts,
        'timestamp_semantics': lexical.TIME_NOTE,
        'coordinates': {'axis': 'chunk_relative_seconds', 'core_start': 0.,
            'core_end': 24/RATE, 'analyzer_start': 0.}, 'units': units}
    row['lexical_evidence'] = lexical.encoded(raw).decode('ascii')
    assert lexical.current_inventory(None, SID, row, source_lookup=case['sources'].get) is not None
    return row


def _insert_local_parent(db, row):
    names = list(row)
    db.execute('INSERT INTO segments(' + ','.join(names) + ',speaker_status) VALUES(' +
        ','.join('?' for _ in names) + ",'unknown')", tuple(row[name] for name in names))


def _insert_reference_commit(db, commit, ordinal, batch, store_key):
    from audio_backend import native_owned_cells as cells
    part = commit['part']
    db.execute("""INSERT INTO native_core_commits(slot,sid,cid,scope_key,logical_end,body,
        pending,state,reason,recording_batch_id,commit_ordinal,retention_store_key)
        VALUES(?,?,?,?,?,?,NULL,'none','synthetic',?,?,?)""",
        (core.slot(commit), part['session_id'], part['chunk_id'], core.scope_key(commit['scope']),
         part['logical_end'], cells.canonical(commit, core.COMPACT_BYTES), batch, ordinal, store_key))


def _support_local_seed(db, schema, *, reference=False, waiting=False, fixed=False):
    from types import SimpleNamespace
    from audio_backend import native_owned_cells as cells
    from test_bounded_core_support import SID, synthetic_case, final_cell, seam_origin
    batch = 'local-synthetic-batch'; store_key = '5' * 64
    if fixed:
        # Exact, pinned fixed73 construction. No tuning, search or padding.
        from test_bounded_core_support import (ordinary_origin, primary_row,
            compact_snapshot_for, query_for_snapshot)
        case = synthetic_case([0, 8000, 16000, 24000, 32000], [(2000, 4000)],
            origin_support=(0, 8000), separate_sources=True)
        first = case['target']
        second = ordinary_origin(case, 0, (1000, 7000))
        third = seam_origin(case, 0)
        if fixed == 'ordinary_first':
            case['target'] = core.append_target_origin(core.append_target_origin(first, second), third)
        else:
            # Same three authentic originals, retaining the separate seam-first
            # comparison whose genuine anchor2 permits horizon4.
            case['target'] = core.append_target_origin(
                core.append_target_origin(core.original_target(third), first['origins'][0]), second)
        assert case['target']['key'] == first['key'] and len(case['target']['origins']) == 3
        parent = primary_row(1, 0, 32000, cid='source-0', text='synthetic')
        snapshot = compact_snapshot_for(case, [parent])
        shape = core.select_support(case['target'], case['commits'], snapshot, 0, 32000)
        assert shape == {'start_frame': 0, 'end_frame': 32000}
        local = core.localize_inventory(case['target'], case['commits'], snapshot, 0, 32000, shape)
        query, pcm = query_for_snapshot(case, local)
        cell = core.make_bounded_cell(case['commits'], query, case['evidences'][-1],
            pcm, case['target'], local)
        doc = json.loads(cell.proof)
    else:
        case = synthetic_case([0, 16, 32, 48, 64], [[18, 22]], origin_index=1, separate_sources=True)
        case['target'] = core.original_target(seam_origin(case, 0))
        parent = _support_local_parent(case)
        cell = final_cell(case, [parent]); doc = json.loads(cell.proof)
        assert doc['geometry'] == {'start_frame': 16, 'end_frame': 24}
        snapshot = core.lexical_snapshot(SID, [parent], case['sources'], 0., scan_frames=[0,64],
            schema=schema or core.COMPACT_INVENTORY_SCHEMA)
        if schema is not None:
            cell = core.make_bounded_cell(doc['commits'],doc['query'],doc['right_evidence'],cell.pcm,case['target'],snapshot)
            doc = json.loads(cell.proof)
    registration_ordinal = (1 if fixed == 'ordinary_first' else 2) if fixed else 4
    db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,created,
        capture_origin_frame,native_core_ordinal,retention_store_key) VALUES(?,?,?,1.,0,?,?)''',
        (batch, SID, 'synthetic-capture', registration_ordinal, store_key))
    for index, commit in enumerate(case['commits']):
        part = commit['part']; source = case['sources'][part['chunk_id']]
        db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
            timeline_start,status,next_offset,created,metadata) VALUES(?,?,?,?,?,?,?,'completed',?,1.,?)''',
            (source['id'], SID, source['id'], source['digest'], '/synthetic-not-opened/' + source['id'],
             source['duration'], source['timeline_start'], source['duration'], '{"synthetic":true}'))
        db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,
            capture_start_frame,native_receipt_status,receipt_id,receipt_state)
            VALUES(?,?,?,?,?,?,'synthetic',?,'durable')''',
            (source['id'], batch, index, part['source_frames'], part['source_frames'] * 2,
             part['logical_start'], part['receipt_sha256']))
        if index < registration_ordinal:
            _insert_reference_commit(db, commit, index + 1, batch, store_key)
    if not fixed:_insert_local_parent(db, parent)
    origin = case['target']['origins'][0]
    proof, binding = origin['proof'].encode('ascii'), origin['binding'].encode('ascii')
    original_doc, support, _ = cells.validate_document(proof)
    proposal = {'key': hashlib.sha256(binding).hexdigest(), 'sid': SID, 'kind': json.loads(binding)['kind'],
        'binding': binding, 'proof': proof, 'proof_sha256': hashlib.sha256(proof).hexdigest(),
        'cell_id': cells.sha({'policy': original_doc['policy'], 'support': support.to_dict()}),
        'slot1': core.slot(origin['commits'][0]),
        'slot2': core.slot(origin['commits'][1]) if len(origin['commits']) == 2 else None,
        'cid1': origin['commits'][0]['part']['chunk_id'],
        'cid2': origin['commits'][1]['part']['chunk_id'] if len(origin['commits']) == 2 else None,
        'target': case['target']}
    context = {'batch_id': batch, 'ordinal': len(origin['commits']), 'retention_store_key': store_key}; now = time.time()
    with Budget().phase() as budget:
        registration = {**proposal, 'target': core.original_target(origin)} if fixed == 'ordinary_first' else proposal
        initial, status = automatic._register_target(db, registration, context,
            cells.sha(case['commits'][0]['scope']), now, budget)
        assert status == 'registered'
        first_registration = initial
        available = registration_ordinal
        if fixed == 'ordinary_first':
            assert initial['wait_start_ordinal'] == 1 and initial['wait_last_ordinal'] == 4
            initial, status = automatic._register_target(db, {'target': core.original_target(second)},
                context, cells.sha(case['commits'][0]['scope']), now, budget)
            assert status == 'alias_registered' and initial['wait_last_ordinal'] == 4
            _insert_reference_commit(db, case['commits'][1], 2, batch, store_key)
            db.execute('UPDATE recording_batches SET native_core_ordinal=2 WHERE id=?', (batch,))
            context = {**context, 'ordinal': 2}; available = 2
            initial, status = automatic._register_target(db, {'target': core.original_target(third)},
                context, cells.sha(case['commits'][0]['scope']), now, budget)
            assert status == 'alias_registered' and initial['wait_last_ordinal'] == 4
            assert initial['wait_start_ordinal'] == first_registration['wait_start_ordinal'] == 1
            assert initial['candidate_key'] == first_registration['candidate_key']
            assert initial['target_body'] == cells.canonical(case['target'])
            assert json.loads(initial['target_body'])['origins'][0] == origin
        if fixed:
            # Each registration above sees precisely its already committed
            # ordinals. The final arriving commits and whole parent appear only
            # at this final4 snapshot; no future compact authorizes an earlier step.
            for index in range(available,4):
                _insert_reference_commit(db,case['commits'][index],index+1,batch,store_key)
            db.execute('UPDATE recording_batches SET native_core_ordinal=4 WHERE id=?',(batch,))
            _insert_local_parent(db,parent)
        context = {**context, 'ordinal': 4}
        final = {'target': case['target'], 'commits': doc['commits'], 'query': doc['query'],
            'evidence': doc['right_evidence'], 'support': doc['geometry'], 'pcm': cell.pcm}
        if waiting:
            return SimpleNamespace(case=case, doc=doc, row=initial, key=proposal['key'],
                parent=parent, context=context, original=proposal, store_key=store_key,
                final=final, snapshot=snapshot, cell=cell, initial=initial,
                first_registration=first_registration)
        ident = automatic._bind_target(db, initial, final, context, snapshot, budget, now,
                                      support_local=schema is None)
        row = automatic._read_v2(db, key=proposal['key'], budget=budget)
        assert ident == row['cell_id']
        if schema is not None:assert row['proof'] == cell.proof
        doc = json.loads(row['proof'])
        if not reference:
            # Historical full-row compatibility fixture, explicitly reconstructed
            # in test setup. Production never rewrites an already-bound row.
            full = automatic.canonical(doc['target'])
            delta = len(full) - len(row['target_body'])
            assert len(row['proof']) + len(full) <= automatic.PROOF_BYTES
            db.execute(f'UPDATE {automatic.TABLE} SET target_body=?,planning_bytes=?,charge_bytes=charge_bytes+? WHERE seq=?',
                (full, len(full), delta, row['seq']))
            automatic._charge(db, SID, (0, delta, 0, delta))
            row = automatic._read_v2(db, key=proposal['key'], budget=budget)
            assert row['target_body'] == full
        assert automatic.qualify_bounded_document(db, doc, budget, store_key)
    assert len(doc['commits']) == 4
    if not fixed:
        assert doc['geometry'] == {'start_frame':16,'end_frame':24}
        if schema is None:
            assert doc['inventories']['scan_frames'] == [16,24]
            assert {s['id'] for s in doc['inventories']['sources']} == {'source-1','source-2'}
    assert {root['cid'] for root in doc['dependency_roots']} == set(case['sources'])
    return SimpleNamespace(case=case,doc=doc,row=row,key=proposal['key'],parent=parent,
        context=context,original=proposal,store_key=store_key,final=final,
        snapshot=snapshot,cell=cell,initial=initial,first_registration=first_registration)


def _local_inventory_current(f):
    from audio_backend.native_owned_cell_capture import PrimaryAuthority
    with Budget().phase() as budget:
        return PrimaryAuthority(f.store).bounded_inventory_current(f.doc['inventories'],budget)


def _local_binding(f):
    from audio_backend.transcript_store import TranscriptStore
    from test_bounded_core_support import SID
    with f.store.db() as db:
        return TranscriptStore._binding_v2(db,SID,dict(f.row))


@pytest.mark.parametrize('schema',[core.INVENTORY_SCHEMA,core.COMPACT_INVENTORY_SCHEMA],ids=['v1','v2'])
def test_support_local_historical_freshness_stays_whole_band(support_local_ledger_factory,schema):
    from test_bounded_core_support import primary_row
    f=support_local_ledger_factory(schema); before=f.row['proof']
    assert _local_inventory_current(f) and _local_binding(f)[1:3]==(16,24)
    with f.store.db() as db:_insert_local_parent(db,primary_row(2,48,56,cid='source-3'))
    assert not _local_inventory_current(f)
    with pytest.raises(ReadError,match='^bounded_inventory_changed$'):_local_binding(f)
    with f.store.db() as db:
        assert db.execute(f'SELECT proof FROM {automatic.TABLE} WHERE seq=?',(f.row['seq'],)).fetchone()[0]==before


@pytest.mark.parametrize('mutation',['outside','endpoint','insert','remove','text','outside_units'])
def test_support_local_whole_parent_membership_freshness(support_local_ledger_factory,mutation):
    from test_bounded_core_support import SID,primary_row
    f=support_local_ledger_factory()
    assert _local_inventory_current(f) and _local_binding(f)[1:3]==(16,24)
    with f.store.db() as db:
        if mutation=='outside':_insert_local_parent(db,primary_row(2,48,56,cid='source-3'))
        elif mutation=='endpoint':
            _insert_local_parent(db,primary_row(2,8,16,cid='source-0'))
            _insert_local_parent(db,primary_row(3,24,28,cid='source-1'))
        elif mutation=='insert':_insert_local_parent(db,primary_row(2,18,20,cid='source-1'))
        elif mutation=='remove':db.execute('DELETE FROM segments WHERE id=1')
        elif mutation=='text':db.execute("UPDATE segments SET text='changed whole parent' WHERE id=1")
        else:
            row=deepcopy(f.parent); raw=json.loads(row['lexical_evidence'])
            row['text']=row['raw_text']='甲丙'
            raw['units'][1]['text']=raw['units'][1]['raw_text']='丙'
            raw['primary_sha256']=lexical.primary_sha(SID,row)
            row['lexical_evidence']=lexical.encoded(raw).decode('ascii')
            assert lexical.current_inventory(None,SID,row,source_lookup=f.case['sources'].get) is not None
            assert raw['units'][1]['start']+f.case['sources']['source-1']['timeline_start']>=24/RATE
            db.execute('UPDATE segments SET text=?,raw_text=? WHERE id=1',(row['text'],row['raw_text']))
            db.execute('UPDATE segments SET lexical_evidence=? WHERE id=1',(row['lexical_evidence'],))
    if mutation in ('outside','endpoint'):
        assert _local_inventory_current(f) and _local_binding(f)[1:3]==(16,24)
    else:
        assert not _local_inventory_current(f)
        with pytest.raises(ReadError,match='^bounded_inventory_changed$'):_local_binding(f)


@pytest.mark.parametrize('mutation',['context_append','own_source','outside_lexical_source'])
def test_support_local_source_closure_freshness(support_local_ledger_factory,mutation):
    from test_bounded_core_support import SID
    f=support_local_ledger_factory()
    assert _local_inventory_current(f) and _local_binding(f)[1:3]==(16,24)
    with f.store.db() as db:
        if mutation=='context_append':
            db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
                timeline_start,status,next_offset,created) VALUES('context-new',?,'context-new',?,
                '/synthetic-not-opened',?,?,'completed',?,1.)''',(SID,'d'*64,16/RATE,64/RATE,16/RATE))
        else:
            cid='source-1' if mutation=='own_source' else 'source-2'
            if mutation=='outside_lexical_source':assert f.case['sources'][cid]['timeline_start']>=24/RATE
            db.execute('UPDATE chunks SET digest=? WHERE id=?',('e'*64,cid))
    if mutation=='context_append':
        assert _local_inventory_current(f) and _local_binding(f)[1:3]==(16,24)
    else:
        with pytest.raises(Unavailable,match='^bounded_inventory_source_changed$'):_local_inventory_current(f)
        with pytest.raises(ReadError,match='^bounded_inventory_source_changed$'):_local_binding(f)


@pytest.mark.parametrize('mutation',['original_cancel','original_digest','original_receipt','final_cancel'])
def test_support_local_noncontributing_native_roots_remain_authoritative(support_local_ledger_factory,mutation):
    f=support_local_ledger_factory();before=f.row['proof'];cid='source-0'
    _,originals=core.validate_target(f.doc['target'])
    assert any(c['part']['chunk_id']==cid for c in originals)
    assert all(p['chunk_id']!=cid for p in f.doc['dependencies'])
    assert all(s['id']!=cid for s in f.doc['inventories']['sources'])
    assert any(r['cid']==cid and r['kind']&1 for r in f.doc['dependency_roots'])
    assert _local_binding(f)[1:3]==(16,24)
    with f.store.db() as db:
        if mutation=='original_cancel':db.execute("UPDATE chunks SET status='cancelled' WHERE id=?",(cid,))
        elif mutation=='original_digest':db.execute('UPDATE chunks SET digest=? WHERE id=?',('e'*64,cid))
        elif mutation=='original_receipt':db.execute('UPDATE recording_work SET receipt_id=? WHERE chunk_id=?',('e'*64,cid))
        else:db.execute("UPDATE native_core_commits SET state='cancelled' WHERE slot=?",(core.slot(f.case['commits'][-1]),))
    # No selected lexical dependency changed: the independent native-root fence
    # must still refuse, even when that original root has zero selected PCM.
    assert _local_inventory_current(f)
    reason='bounded_compact_changed' if mutation=='final_cancel' else 'automatic_source_unplaced'
    with pytest.raises(ReadError,match='^'+reason+'$'):_local_binding(f)
    with f.store.db() as db:
        assert automatic.retention_root(db,cid=cid)
        assert db.execute(f'SELECT proof FROM {automatic.TABLE} WHERE seq=?',(f.row['seq'],)).fetchone()[0]==before


@pytest.mark.parametrize('kind',['actual','unsafe'])
def test_support_local_capacity_notice_is_fixed_and_preserves_rollback(support_local_ledger_factory,monkeypatch,kind):
    from audio_backend import native_owned_cells as cells
    from test_bounded_core_support import primary_row
    f=support_local_ledger_factory(); before=f.row['proof']
    encoded=json.dumps({'payload':'private synthetic text'*100},sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
    annotations=[{}] if kind=='actual' else [{}, {'capacity_phase':'private synthetic text'},
        {'encoded_bytes':True}, {'encoded_bytes':-1}, {'encoded_bytes':float('nan')},
        {'encoded_bytes':cells.MAX_FRAME+1}, {'maximum_bytes':-1}, {'maximum_bytes':len(encoded)}]
    for index,annotation in enumerate(annotations):
        with f.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            _insert_local_parent(db,primary_row(2+index,48,56,cid='source-3',text='primary survives'))
            def refuse(*args,**kwargs):
                db.execute("UPDATE segments SET text='optional rolled back' WHERE id=?",(2+index,))
                try:cells.canonical({'payload':'private synthetic text'*100},64)
                except Unavailable as exc:
                    for name,value in annotation.items():setattr(exc,name,value)
                    raise
            handoff={'waiters':[f.row],'batch':f.context,'reserved':True,'budget':Budget(),
                'origins':[f.original],'plans':[]}
            # The unannotated compact-verification site is deliberately unknown;
            # it must never guess an inner serialization object.
            site='_register_target' if kind=='actual' else '_exact_compacts'
            with monkeypatch.context() as patch:
                patch.setattr(automatic,site,refuse)
                notice=automatic.enqueue_bounded(db,[],{'commit':f.case['commits'][-1]},handoff,
                    {'session_id':f.row['sid']},[],None)
            assert notice['reason']=='serialization_capacity' and notice['owned_targets_preserved'] is True
            assert notice['status']=='waiting'
            assert db.execute('SELECT text FROM segments WHERE id=?',(2+index,)).fetchone()[0]=='primary survives'
            assert db.execute(f'SELECT proof FROM {automatic.TABLE} WHERE seq=?',(f.row['seq'],)).fetchone()[0]==before
            assert db.in_transaction
        if not annotation:
            phase='target_registration' if kind=='actual' else 'unknown'
            assert notice['capacity']=={'phase':phase,'encoded_bytes':len(encoded),'limit_bytes':64}
        else:assert 'capacity' not in notice
        raw=json.dumps(notice,sort_keys=True,separators=(',',':')).encode('ascii')
        assert len(raw)<512 and b'private synthetic text' not in raw and b'payload' not in raw
    if kind=='actual':
        # Source binding and the dependency list have their own fixed narrow
        # catches; this checks exact exception propagation, not a new writer.
        def fail_binding(*args,**kwargs):return cells.canonical({'payload':'private synthetic text'*100},64)
        with monkeypatch.context() as patch:
            patch.setattr(automatic,'source_binding',fail_binding)
            with pytest.raises(Unavailable,match='^serialization_capacity$') as caught:
                automatic.source_binding_v2('core_seam',f.doc,None,None,f.context)
        assert caught.value.capacity_phase=='source_binding'
        assert caught.value.encoded_bytes==len(encoded) and caught.value.maximum_bytes==64


@pytest.mark.parametrize('guard',[
    'row_certificate','expected_certificate','stored_certificate','alias_certificate',
    'new_certificate','new_charge','bound_certificate','bound_charge'])
def test_certificate_capacity_guard_operands(support_local_ledger_factory,monkeypatch,guard):
    """Reach each existing predicate, without reducing any production limit."""
    from test_bounded_core_support import synthetic_case,ordinary_origin
    assert (automatic.PROOF_BYTES,automatic.BINDING_BYTES,automatic.ACCOUNTING_BYTES,
            automatic.MAX_CHARGE)==(65536,4096,16384,86016)

    class LengthDB:
        def __init__(self,sizes):self.sizes=sizes;self.calls=[]
        def execute(self,sql,parameters):
            assert sql.startswith('SELECT length(binding),length(proof),length(target_body) FROM ')
            self.calls.append((sql,parameters));assert len(self.calls)==1
            return self
        def fetchone(self):return self.sizes

    binding=None; builds=[]; database=None
    with monkeypatch.context() as patch:
        if guard in ('bound_certificate','bound_charge'):
            f=support_local_ledger_factory(); proof=f.row['proof']
            target=automatic._bound_target_reference(proof,f.doc['target'])
            assert len(proof)+len(target)<=automatic.PROOF_BYTES
            row={**f.row,'plan_phase':'waiting','state':'queued','job_id':None}
            proposal={'target':f.case['target'],'commits':f.doc['commits'],
                'query':f.doc['query'],'evidence':f.doc['right_evidence'],
                'support':f.doc['geometry'],'pcm':b'\0\0'*(f.doc['geometry']['end_frame']-
                                                          f.doc['geometry']['start_frame'])}
            make_cell=core.make_bounded_cell
            def measured_cell(*args,**kwargs):
                cell=make_cell(*args,**kwargs)
                assert cell.proof==proof
                builds.append(len(cell.proof));return cell
            patch.setattr(core,'make_bounded_cell',measured_cell)
            if guard=='bound_certificate':
                # Defense-only corrupted encoder seam; not authentic capacity
                # benefit. Real compact checks and frozen Cell validation run.
                target+=b' '*(automatic.PROOF_BYTES+1-len(proof)-len(target))
                def oversized_reference(actual_proof,actual_target):
                    assert actual_proof==proof and actual_target==proposal['target']
                    return target
                patch.setattr(automatic,'_bound_target_reference',oversized_reference)
            else:
                # Defense-only seam. With a valid <=4096-byte binding, the
                # preceding certificate cap makes this charge guard unreachable.
                assert automatic.ACCOUNTING_BYTES+automatic.PROOF_BYTES+automatic.BINDING_BYTES==automatic.MAX_CHARGE
                binding=b'b'*(automatic.MAX_CHARGE-automatic.ACCOUNTING_BYTES-len(proof)-len(target)+1)
                assert len(binding)>automatic.BINDING_BYTES
                def oversized_binding(kind,doc,support,parts,context):
                    assert kind==row['kind'] and doc==f.doc and context==f.context
                    return binding
                patch.setattr(automatic,'source_binding_v2',oversized_binding)
            def invoke():
                with f.store.db() as db:
                    return automatic._bind_target(db,row,proposal,f.context,
                        f.doc['inventories'],None,time.time())
        else:
            case=synthetic_case([0,64],[[18,22]],origin_support=(0,32))
            target=automatic.canonical(case['target'])
            proof=b'p'*(automatic.PROOF_BYTES+1-len(target))
            if guard=='row_certificate':
                row={name:0 for name in automatic.V2_COLUMNS}
                row.update(target_key=case['target']['key'],target_body=target)
                def invoke():return automatic.validate_v2_row(None,row,proof)
            elif guard=='expected_certificate':
                def invoke():return automatic._read_v2(None,key='a'*64,expected=(proof,target))
            elif guard=='stored_certificate':
                proof=b'p'*automatic.PROOF_BYTES;target=b't';binding=b'b'*23
                database=LengthDB((len(binding),len(proof),len(target)))
                def invoke():return automatic._read_v2(database,key='a'*64)
            elif guard=='alias_certificate':
                alias=core.original_target(ordinary_origin(case,support=(1,33)))
                merged=core.append_target_origin(case['target'],alias['origins'][0])
                original_target=target;target=automatic.canonical(merged)
                proof=b'p'*(automatic.PROOF_BYTES-len(original_target))
                assert len(proof)+len(original_target)==automatic.PROOF_BYTES
                assert len(target)>len(original_target)
                # Supply only the earlier lookup result at its byte boundary;
                # genuine target/alias validation and serialization still run.
                existing={'plan_phase':'waiting','target_body':original_target,'proof':proof}
                lookups=[]
                def lookup(db,*,target_key,budget):
                    assert target_key==alias['key'];lookups.append(target_key)
                    return existing
                patch.setattr(automatic,'_read_v2',lookup)
                def invoke():return automatic._register_target(None,{'target':alias},{},'a'*64,1.,None)
            else:
                database=LengthDB(None)
                if guard=='new_charge':
                    proof=case['target']['origins'][0]['proof'].encode('ascii')
                    assert len(proof)+len(target)<=automatic.PROOF_BYTES
                    # Deliberately invalid binding supplied at registration's
                    # input seam, never claimed to be reachable from valid input.
                    assert automatic.ACCOUNTING_BYTES+automatic.PROOF_BYTES+automatic.BINDING_BYTES==automatic.MAX_CHARGE
                    binding=b'b'*(automatic.MAX_CHARGE-automatic.ACCOUNTING_BYTES-len(proof)-len(target)+1)
                    assert len(binding)>automatic.BINDING_BYTES
                proposal={'target':case['target'],'proof':proof,'binding':binding or b'b'}
                def invoke():return automatic._register_target(database,proposal,{},'a'*64,1.,None)
        expected={'guard':guard,'check':'certificate','proof_bytes':len(proof),
            'target_bytes':len(target),'total_bytes':len(proof)+len(target),
            'minimum_bytes':2,'limit_bytes':65536}
        if guard=='stored_certificate':expected['binding_bytes']=len(binding)
        if guard in ('new_charge','bound_charge'):
            expected.update(check='charge',binding_bytes=len(binding),accounting_bytes=16384,
                total_bytes=16384+len(proof)+len(target)+len(binding),minimum_bytes=0,limit_bytes=86016)
        assert expected['total_bytes']>expected['limit_bytes']
        with pytest.raises(Unavailable,match='^bounded_certificate_capacity$') as caught:invoke()
        assert type(caught.value) is Unavailable and caught.value.args==('bounded_certificate_capacity',)
        assert caught.value.certificate_capacity==expected
        if database is not None:assert len(database.calls)==1
        if guard=='alias_certificate':assert lookups==[alias['key']]
        if guard in ('bound_certificate','bound_charge'):
            assert builds==[len(proof)]
            with f.store.db() as db:
                assert dict(db.execute(f'SELECT * FROM {automatic.TABLE} WHERE seq=?',
                    (f.row['seq'],)).fetchone())==f.row


@pytest.mark.parametrize('site',['row','expected','stored'])
def test_certificate_capacity_malformed_inputs(site):
    """Fixed malformed/lower-bound inputs retain only genuinely known counts."""
    guard={'row':'row_certificate','expected':'expected_certificate','stored':'stored_certificate'}[site]
    shape={'guard':guard,'check':'shape'}
    certificate={'guard':guard,'check':'certificate','minimum_bytes':2,'limit_bytes':65536}
    if site=='row':
        cases=[
            ((None,b't'),{**shape,'target_bytes':1}),
            (('private synthetic text',b't'),{**shape,'target_bytes':1}),
            ((bytearray(b'p'),b't'),{**shape,'target_bytes':1}),
            ((memoryview(b'p'),b't'),{**shape,'target_bytes':1}),
            ((b'p','/private/synthetic.wav'),{**shape,'proof_bytes':1}),
            ((b'p',False),{**shape,'proof_bytes':1}),
            ((False,True),shape),
            ((b'',b''),{**certificate,'proof_bytes':0,'target_bytes':0,'total_bytes':0}),
            ((b'',b't'),{**certificate,'proof_bytes':0,'target_bytes':1,'total_bytes':1})]
        def invoke(value):
            proof,target=value;row={name:0 for name in automatic.V2_COLUMNS}
            row.update(target_key='a'*64,target_body=target)
            return automatic.validate_v2_row(None,row,proof)
    elif site=='expected':
        cases=[
            ([b'p',b't'],shape),((b'p',),shape),((b'p',b't',b'x'),shape),
            ('/private/synthetic.wav',shape),
            ((None,b't'),{**shape,'target_bytes':1}),
            ((bytearray(b'p'),b't'),{**shape,'target_bytes':1}),
            ((b'p',memoryview(b't')),{**shape,'proof_bytes':1}),
            ((b'p',False),{**shape,'proof_bytes':1}),((False,True),shape),
            ((b'',b''),{**certificate,'proof_bytes':0,'target_bytes':0,'total_bytes':0}),
            ((b'p',b''),{**certificate,'proof_bytes':1,'target_bytes':0,'total_bytes':1})]
        def invoke(value):return automatic._read_v2(None,key='a'*64,expected=value)
    else:
        class LengthDB:
            def __init__(self,sizes):self.sizes=sizes;self.calls=0
            def execute(self,sql,parameters):
                assert sql.startswith('SELECT length(binding),length(proof),length(target_body) FROM ')
                self.calls+=1;assert self.calls==1;return self
            def fetchone(self):return self.sizes
        # None is a missing SQL length, not a missing column or a coerced zero.
        cases=[
            ((None,1,1),{**shape,'proof_bytes':1,'target_bytes':1}),
            ((1,None,1),{**shape,'binding_bytes':1,'target_bytes':1}),
            ((1,1,None),{**shape,'binding_bytes':1,'proof_bytes':1}),
            ((True,1,1),{**shape,'proof_bytes':1,'target_bytes':1}),
            ((1,True,0),{**shape,'binding_bytes':1,'target_bytes':0}),
            ((1,0,False),{**shape,'binding_bytes':1,'proof_bytes':0}),
            ((-1,1,1),{**shape,'proof_bytes':1,'target_bytes':1}),
            ((1,-1,0),{**shape,'binding_bytes':1,'target_bytes':0}),
            ((1,0,-1),{**shape,'binding_bytes':1,'proof_bytes':0}),
            ((1,'private synthetic text',1),{**shape,'binding_bytes':1,'target_bytes':1}),
            ((1,1.,1),{**shape,'binding_bytes':1,'target_bytes':1}),
            ((1,float('nan'),1),{**shape,'binding_bytes':1,'target_bytes':1}),
            ((1,1,float('inf')),{**shape,'binding_bytes':1,'proof_bytes':1}),
            ((1,automatic.MAX_FRAME+1,0),{**shape,'binding_bytes':1,'target_bytes':0}),
            ((0,1,1),{'guard':guard,'check':'binding','binding_bytes':0,
                'proof_bytes':1,'target_bytes':1,'total_bytes':0,'minimum_bytes':1,'limit_bytes':4096}),
            ((4097,1,1),{'guard':guard,'check':'binding','binding_bytes':4097,
                'proof_bytes':1,'target_bytes':1,'total_bytes':4097,'minimum_bytes':1,'limit_bytes':4096}),
            ((1,0,0),{**certificate,'binding_bytes':1,'proof_bytes':0,'target_bytes':0,'total_bytes':0}),
            ((1,0,1),{**certificate,'binding_bytes':1,'proof_bytes':0,'target_bytes':1,'total_bytes':1})]
        def invoke(value):return automatic._read_v2(LengthDB(value),key='a'*64)
        assert automatic._read_v2(LengthDB(None),key='a'*64) is None
    for value,expected in cases:
        with pytest.raises(Unavailable,match='^bounded_certificate_capacity$') as caught:invoke(value)
        assert type(caught.value) is Unavailable and caught.value.args==('bounded_certificate_capacity',)
        actual=caught.value.certificate_capacity
        assert actual==expected
        assert all(type(number) is int and 0<=number<=automatic.MAX_FRAME
            for name,number in actual.items() if name not in ('guard','check'))
        if actual['check']=='shape':
            assert not {'total_bytes','accounting_bytes','minimum_bytes','limit_bytes'} & actual.keys()
        elif actual['check']=='certificate':
            assert actual['total_bytes']==actual['proof_bytes']+actual['target_bytes']<2
        else:
            assert actual['check']=='binding' and actual['total_bytes']==actual['binding_bytes']
            assert actual['total_bytes']<actual['minimum_bytes'] or actual['total_bytes']>actual['limit_bytes']
        assert automatic._certificate_notice(caught.value)==expected


@pytest.mark.parametrize('kind',['safe','unsafe','unannotated'])
def test_certificate_capacity_notice_preserves_rollback(support_local_ledger_factory,monkeypatch,kind):
    """Real optional savepoint rollback is independent of diagnostic screening."""
    from test_bounded_core_support import primary_row
    f=support_local_ledger_factory()
    base={'guard':'bound_certificate','check':'certificate','proof_bytes':65536,
        'target_bytes':1,'total_bytes':65537,'minimum_bytes':2,'limit_bytes':65536}
    if kind=='safe':
        annotations=[{**base,'guard':guard} for guard in ('row_certificate','expected_certificate',
            'alias_certificate','new_certificate','bound_certificate')]
        annotations.extend([
            {**base,'guard':'stored_certificate','binding_bytes':1},
            {'guard':'stored_certificate','check':'binding','proof_bytes':1,'target_bytes':1,
                'binding_bytes':0,'total_bytes':0,'minimum_bytes':1,'limit_bytes':4096},
            {'guard':'stored_certificate','check':'binding','proof_bytes':1,'target_bytes':1,
                'binding_bytes':4097,'total_bytes':4097,'minimum_bytes':1,'limit_bytes':4096},
            {'guard':'expected_certificate','check':'certificate','proof_bytes':0,'target_bytes':1,
                'total_bytes':1,'minimum_bytes':2,'limit_bytes':65536},
            {'guard':'row_certificate','check':'shape','target_bytes':1},
            {'guard':'stored_certificate','check':'shape','proof_bytes':1,'binding_bytes':1}])
        annotations.extend({'guard':guard,'check':'charge','proof_bytes':65535,'target_bytes':1,
            'binding_bytes':4097,'accounting_bytes':16384,'total_bytes':86017,
            'minimum_bytes':0,'limit_bytes':86016} for guard in ('new_charge','bound_charge'))
        # Include the longest guard and maximal safe integer widths in complete
        # notices, plus all charge fields together. No raw operand is serialized.
        annotations.extend([
            {'guard':'expected_certificate','check':'certificate','proof_bytes':automatic.MAX_FRAME,
                'target_bytes':0,'total_bytes':automatic.MAX_FRAME,'minimum_bytes':2,'limit_bytes':65536},
            {'guard':'stored_certificate','check':'binding','proof_bytes':automatic.MAX_FRAME,
                'target_bytes':automatic.MAX_FRAME,'binding_bytes':automatic.MAX_FRAME,
                'total_bytes':automatic.MAX_FRAME,'minimum_bytes':1,'limit_bytes':4096},
            {'guard':'bound_charge','check':'charge','proof_bytes':automatic.MAX_FRAME-16386,
                'target_bytes':1,'binding_bytes':1,'accounting_bytes':16384,
                'total_bytes':automatic.MAX_FRAME,'minimum_bytes':0,'limit_bytes':86016}])
    elif kind=='unsafe':
        changes=[{'payload':'private synthetic text'}, {'path':'/private/synthetic.wav'},
            {'sql':'SELECT secret FROM synthetic'}, {'guard':'private synthetic text'},
            {'guard':True}, {'check':'/private/synthetic.wav'}, {'check':False},
            {'proof_bytes':True}, {'target_bytes':-1}, {'total_bytes':automatic.MAX_FRAME+1},
            {'limit_bytes':float('nan')}, {'proof_bytes':float('inf')}, {'target_bytes':1.},
            {'minimum_bytes':False}, {'total_bytes':65538}, {'minimum_bytes':0},
            {'limit_bytes':65535}, {'accounting_bytes':16384}, {'binding_bytes':1},
            {'check':'charge'}, {'check':'binding'}, {'guard':'new_charge'},
            {'guard':'stored_certificate'}, {'proof_bytes':1,'total_bytes':2}]
        annotations=[None,'private synthetic text',['/private/synthetic.wav'],{}]
        annotations.extend({**base,**change} for change in changes)
        annotations.extend({key:value for key,value in base.items() if key!=missing}
            for missing in ('guard','check','proof_bytes','target_bytes','total_bytes'))
        annotations.append({'guard':'new_charge','check':'charge','proof_bytes':65535,
            'target_bytes':1,'binding_bytes':4097,'accounting_bytes':16383,
            'total_bytes':86016,'minimum_bytes':0,'limit_bytes':86016})
    else:annotations=[None]
    for index,annotation in enumerate(annotations):
        error=Unavailable('bounded_certificate_capacity')
        if kind!='unannotated':error.certificate_capacity=annotation
        after_fault=[];faults=[]
        with f.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            ident=2+index
            _insert_local_parent(db,primary_row(ident,48,56,cid='source-3',text='primary survives'))
            primary_before=dict(db.execute('SELECT * FROM segments WHERE id=?',(ident,)).fetchone())
            owner_before=dict(db.execute(f'SELECT * FROM {automatic.TABLE} WHERE seq=?',
                (f.row['seq'],)).fetchone())
            def no_serialization(*args,**kwargs):raise AssertionError('diagnostic serialized a payload')
            with monkeypatch.context() as patch:
                def refuse(*args,**kwargs):
                    db.execute("UPDATE segments SET text='private synthetic text',raw_text='/private/synthetic.wav' WHERE id=?",(ident,))
                    db.execute(f'UPDATE {automatic.TABLE} SET proof=?,binding=?,target_body=? WHERE seq=?',
                        (b'private synthetic text',b'/private/synthetic.wav',b'SELECT secret FROM synthetic',f.row['seq']))
                    faults.append(error)
                    # Only rollback/release may follow this already-known fault.
                    # The notice path may not serialize another copy of payloads.
                    db.set_trace_callback(after_fault.append)
                    patch.setattr(automatic,'canonical',no_serialization)
                    patch.setattr(automatic.json,'dumps',no_serialization)
                    raise error
                patch.setattr(automatic,'_exact_compacts',refuse)
                handoff={'waiters':[f.row],'batch':f.context,'reserved':True,'budget':Budget(),
                    'origins':[f.original],'plans':[]}
                try:
                    notice=automatic.enqueue_bounded(db,[],{'commit':f.case['commits'][-1]},handoff,
                        {'session_id':f.row['sid']},[],None)
                finally:db.set_trace_callback(None)
            assert faults==[error] and error.args==('bounded_certificate_capacity',)
            assert after_fault==['ROLLBACK TO bounded_optional','RELEASE bounded_optional']
            expected={'schema':'bounded-core-support-notice-v1','status':'waiting',
                'reason':'bounded_certificate_capacity','owned_targets_preserved':True}
            if kind=='safe':
                expected['capacity']=annotation
                if annotation['check']!='shape':
                    assert (annotation['total_bytes']<annotation['minimum_bytes'] or
                            annotation['total_bytes']>annotation['limit_bytes'])
            assert notice==expected
            if kind=='safe':assert notice['capacity'] is not annotation
            assert db.in_transaction
            assert dict(db.execute('SELECT * FROM segments WHERE id=?',(ident,)).fetchone())==primary_before
            assert dict(db.execute(f'SELECT * FROM {automatic.TABLE} WHERE seq=?',
                (f.row['seq'],)).fetchone())==owner_before
        raw=json.dumps(notice,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
        assert len(raw)<512
        assert all(token not in raw for token in (b'private synthetic text',b'/private/synthetic.wav',
            b'SELECT secret FROM synthetic',b'payload',b'traceback',f.key.encode('ascii')))


# Bound-target reference contracts. These metadata fixtures use fresh real
# schemas and synthetic certificates only; they never open a historical DB.
def _reference_snapshot(f):
    with f.store.db() as db:
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
            for table in (automatic.TABLE, automatic.DEPENDENCIES, automatic.USAGE,
                'segments', 'chunks', 'recording_batches', 'recording_work', 'native_core_commits')}


def _reference_accounting(db, sid):
    rows = [dict(row) for row in db.execute(f'SELECT * FROM {automatic.TABLE}')]
    for scope, actual in zip(('', sid), automatic.usage(db, sid)):
        chosen = rows if not scope else [row for row in rows if row['sid'] == scope]
        assert tuple(actual) == (len(chosen), sum(row['charge_bytes'] for row in chosen),
            sum(row['state'] in automatic.ACTIVE for row in chosen),
            sum(len(row['proof']) + len(row['target_body']) for row in chosen))
        for row in chosen:
            assert row['planning_bytes'] == len(row['target_body'])
            assert row['charge_bytes'] == (automatic.ACCOUNTING_BYTES + len(row['binding']) +
                len(row['proof']) + len(row['target_body']))
    return automatic.usage(db, sid)


def _reference_row(f, db=None):
    if db is not None:
        return dict(db.execute(f'SELECT * FROM {automatic.TABLE} WHERE candidate_key=?', (f.key,)).fetchone())
    with f.store.db() as db:return _reference_row(f, db)


def _set_reference_wire(f, wire, *, proof=None, stale=None):
    """Explicit corruption/equivalence fixture; never a production converter."""
    with f.store.db() as db:
        row = _reference_row(f, db); proof = row['proof'] if proof is None else proof
        certificate_delta = len(proof) + len(wire) - len(row['proof']) - len(row['target_body'])
        charge = row['charge_bytes'] if stale == 'charge_bytes' else row['charge_bytes'] + certificate_delta
        planning = row['planning_bytes'] if stale == 'planning_bytes' else len(wire)
        db.execute(f'UPDATE {automatic.TABLE} SET proof=?,proof_sha256=?,target_body=?,planning_bytes=?,charge_bytes=? WHERE seq=?',
            (proof, hashlib.sha256(proof).hexdigest(), wire, planning, charge, row['seq']))
        automatic._charge(db, row['sid'], (0, charge - row['charge_bytes'], 0, certificate_delta))
    return _reference_row(f)


def _reference_expected(f):
    return f.row['proof'], automatic.canonical(f.doc['target'])


def _reference_identity(f):
    from types import SimpleNamespace
    return SimpleNamespace(session_id=f.row['sid'], job_id='1'*32,
        original_jobs_epoch='2'*32, canonical_request_sha256='3'*64)


class _ReferenceComparisonDB:
    """Record actual bound inputs without retaining any payload in reports."""
    def __init__(self, db):self.db=db; self.calls=[]; self.comparisons=[]
    def execute(self, sql, parameters=()):
        self.calls.append((sql, parameters))
        if 'proof=? AS proof_same' in sql:
            operands = [value for value in parameters if type(value) is bytes]
            self.comparisons.append((len(operands), sum(map(len, operands))))
        return self.db.execute(sql, parameters)


def _horizon_reset_waiter(db, f, target, ordinal):
    """Fresh test ledger setup from a real synthetic origin certificate."""
    from audio_backend import native_owned_cells as cells
    origin = target['origins'][0]
    proof, binding = origin['proof'].encode('ascii'), origin['binding'].encode('ascii')
    doc, support, _ = cells.validate_document(proof)
    commits = origin['commits']
    proposal = {'key': hashlib.sha256(binding).hexdigest(), 'sid': f.row['sid'],
        'kind': json.loads(binding)['kind'], 'binding': binding, 'proof': proof,
        'proof_sha256': hashlib.sha256(proof).hexdigest(),
        'cell_id': cells.sha({'policy': doc['policy'], 'support': support.to_dict()}),
        'slot1': core.slot(commits[0]), 'slot2': core.slot(commits[1]) if len(commits) == 2 else None,
        'cid1': commits[0]['part']['chunk_id'],
        'cid2': commits[1]['part']['chunk_id'] if len(commits) == 2 else None, 'target': target}
    db.execute(f'DELETE FROM {automatic.DEPENDENCIES}')
    db.execute(f'DELETE FROM {automatic.TABLE}')
    db.execute(f'UPDATE {automatic.USAGE} SET rows=0,charge=0,pending=0,proofs=0')
    row, status = automatic._register_target(db, proposal, {**f.context, 'ordinal': ordinal},
        cells.sha(commits[0]['scope']), f.row['created'], Budget())
    assert status == 'registered'
    f.key = row['candidate_key']; f.row = row
    _reference_accounting(db, row['sid'])
    return row


@pytest.mark.parametrize('case_name', [
    'ordinary_initial', 'seam_initial', 'forward_alias', 'late_earlier_alias', 'duplicate_alias'])
def test_wait_horizon_prefix_initial_and_alias_cases(support_local_ledger_factory, case_name):
    from test_bounded_core_support import ordinary_origin, seam_origin
    f = support_local_ledger_factory(waiting=True)
    ordinary = core.original_target(ordinary_origin(f.case, 1))
    earlier = core.original_target(seam_origin(f.case, 0))
    forward = core.original_target(seam_origin(f.case, 1))
    target = earlier if case_name == 'seam_initial' else ordinary
    with f.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        initial = _horizon_reset_waiter(db, f, target, 2)
        assert [initial['wait_start_ordinal'], initial['wait_last_ordinal']] == [
            2, 4 if case_name == 'seam_initial' else 5]
        row = initial
        if case_name in ('forward_alias', 'late_earlier_alias', 'duplicate_alias'):
            row, status = automatic._register_target(db, {'target': forward},
                {**f.context, 'ordinal': 3}, initial['plan_scope_key'], initial['created']+1, Budget())
            assert status == 'alias_registered' and row['wait_last_ordinal'] == 5
            assert resolved_target(row)['origins'] == ordinary['origins']+forward['origins']
        if case_name == 'late_earlier_alias':
            row, status = automatic._register_target(db, {'target': earlier},
                {**f.context, 'ordinal': 3}, initial['plan_scope_key'], initial['created']+2, Budget())
            assert status == 'alias_registered' and row['wait_last_ordinal'] == 4
            assert resolved_target(row)['origins'] == ordinary['origins']+forward['origins']+earlier['origins']
        if case_name == 'duplicate_alias':
            before = row; accounting = _reference_accounting(db, row['sid'])
            row, status = automatic._register_target(db, {'target': forward},
                {**f.context, 'ordinal': 4}, initial['plan_scope_key'], initial['created']+3, Budget())
            assert status == 'already_owned' and row == before
            assert _reference_accounting(db, row['sid']) == accounting
        target = resolved_target(row); _, roots = core.validate_target(target)
        expected_roots = {'ordinary_initial': [2], 'seam_initial': [1, 2],
            'forward_alias': [2, 3], 'late_earlier_alias': [1, 2, 3], 'duplicate_alias': [2, 3]}[case_name]
        ordinals = {item['slot']: item['commit_ordinal'] for item in db.execute(
            'SELECT slot,commit_ordinal FROM native_core_commits')}
        assert [ordinals[core.slot(root)] for root in roots] == expected_roots
        assert automatic._wait_horizon(2, target, roots) == row['wait_last_ordinal']
        assert all(row[key] == initial[key] for key in
            ('candidate_key', 'kind', 'binding', 'proof', 'cell_id', 'wait_start_ordinal', 'created', 'wait_deadline'))
        assert target['origins'][0] == resolved_target(initial)['origins'][0]
        assert automatic._read_v2(db, key=f.key) == row
        _reference_accounting(db, row['sid'])


@pytest.mark.parametrize('case_name', [
    'tighter', 'closed', 'anchor_ordinal', 'nonanchor_gap', 'cancelled', 'deleted', 'old_reader'])
def test_wait_horizon_prefix_saved_expiry_and_authority(support_local_ledger_factory, case_name):
    from test_bounded_core_support import ordinary_origin, seam_origin
    f = support_local_ledger_factory(waiting=True)
    ordinary = core.original_target(ordinary_origin(f.case, 1))
    forward = core.original_target(seam_origin(f.case, 1))
    earlier = core.original_target(seam_origin(f.case, 0))
    with f.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        initial = _horizon_reset_waiter(db, f, ordinary, 2)
        row, status = automatic._register_target(db, {'target': forward},
            {**f.context, 'ordinal': 3}, initial['plan_scope_key'], initial['created']+1, Budget())
        assert status == 'alias_registered' and row['wait_last_ordinal'] == 5
        if case_name == 'tighter':
            db.execute(f'UPDATE {automatic.TABLE} SET wait_last_ordinal=3 WHERE candidate_key=?', (f.key,))
            row = automatic._read_v2(db, key=f.key)
            row, status = automatic._register_target(db, {'target': earlier}, f.context,
                row['plan_scope_key'], row['created']+2, Budget())
            assert status == 'alias_registered' and row['wait_last_ordinal'] == 3
            assert automatic._waiting_reason(row, f.context, row['plan_scope_key'], row['created']) == 'unresolved_commit_horizon'
        elif case_name == 'closed':
            automatic._terminal(db, row, 'no_result', 'unresolved_commit_horizon')
            row = automatic._read_v2(db, key=f.key)
        elif case_name == 'anchor_ordinal':
            db.execute('UPDATE native_core_commits SET commit_ordinal=7 WHERE slot=?',
                (core.slot(f.case['commits'][1]),))
        elif case_name == 'nonanchor_gap':
            # Existing authority permits non-anchor stored-ordinal gaps. The
            # alias uses min(saved5,prefix4), never the non-anchor ordinal8.
            db.execute('UPDATE native_core_commits SET commit_ordinal=8 WHERE slot=?',
                (core.slot(f.case['commits'][2]),))
            db.execute('UPDATE recording_batches SET native_core_ordinal=9 WHERE id=?', (f.context['batch_id'],))
            row, status = automatic._register_target(db, {'target': earlier},
                {**f.context, 'ordinal': 9}, row['plan_scope_key'], row['created']+2, Budget())
            assert status == 'alias_registered' and row['wait_last_ordinal'] == 4
        elif case_name == 'cancelled':
            db.execute("UPDATE native_core_commits SET state='cancelled' WHERE slot=?",
                (core.slot(f.case['commits'][2]),))
        elif case_name == 'deleted':
            db.execute('DELETE FROM native_core_commits WHERE slot=?', (core.slot(f.case['commits'][2]),))
        accounting = _reference_accounting(db, row['sid'])
    before = _reference_snapshot(f)
    with f.store.db() as db:
        if case_name == 'anchor_ordinal':
            with pytest.raises(Unavailable, match='^bounded_wait_metadata$'):
                automatic._read_v2(db, key=f.key)
        else:
            saved = automatic._read_v2(db, key=f.key)
            assert saved == row
            target = resolved_target(saved); _, roots = core.validate_target(target)
            if case_name in ('cancelled', 'deleted'):
                with pytest.raises(Unavailable, match='^bounded_compact_changed$'):
                    automatic._exact_compacts(db, roots, f.context, Budget())
            elif case_name == 'nonanchor_gap':
                context = {**f.context, 'ordinal': 9}
                automatic._exact_compacts(db, roots, context, Budget())
                assert saved['wait_last_ordinal'] == 4 < 8
                assert automatic._waiting_reason(saved, context, saved['plan_scope_key'], saved['created']) == 'unresolved_commit_horizon'
            elif case_name in ('tighter', 'closed'):
                unchanged, status = automatic._register_target(db, {'target': earlier}, f.context,
                    saved['plan_scope_key'], saved['created']+3, Budget())
                assert status == 'already_owned' and unchanged == saved
                assert saved['wait_last_ordinal'] == (3 if case_name == 'tighter' else 5)
                if case_name == 'closed':assert saved['state'] == 'no_result' and saved['plan_phase'] == 'closed'
            else:
                assert case_name == 'old_reader'
                assert json.loads(saved['target_body'])['schema'] == core.TARGET_SCHEMA
                assert 'schema' not in json.loads(saved['proof'])
                # Verified pre-edit against dev40 moss_automatic.py:597. The
                # pinned source70 full-row reader has this identical predicate;
                # this makes no reference-row compatibility claim.
                assert 'or not start<=last<=start+min(3,4-len(originals))\n' in _SOURCE70_TARGET_READER_ORACLE
                old = _source70_target_reader_oracle()
                with pytest.raises(Unavailable, match='^bounded_wait_metadata$'):
                    old['validate_v2_row'](db, saved, saved['proof'])
            assert all(saved[key] == initial[key] for key in
                ('candidate_key', 'kind', 'binding', 'proof', 'cell_id', 'wait_start_ordinal', 'created', 'wait_deadline'))
        assert _reference_accounting(db, row['sid']) == accounting
    assert _reference_snapshot(f) == before


def test_wait_horizon_root_order_and_frame_gap_refuse():
    from test_bounded_core_support import synthetic_case, seam_origin
    case = synthetic_case([0, 16, 32, 48], [(18, 22)], origin_index=1, separate_sources=True)
    target = core.original_target(seam_origin(case, 0))
    _, roots = core.validate_target(target)
    assert automatic._wait_horizon(2, target, roots) == 4
    reversed_target = deepcopy(target)
    reversed_target['origins'][0]['commits'].reverse()
    gapped_target = deepcopy(target)
    part = gapped_target['origins'][0]['commits'][1]['part']
    part['logical_start'] += 1; part['source_start'] += 1
    for invalid in (reversed_target, gapped_target):
        with pytest.raises(Unavailable, match='^source_gap$'):
            # The helper only consumes validated roots; invalid geometry must
            # fail at the existing validation boundary, before horizon use.
            _, invalid_roots = core.validate_target(invalid)
            automatic._wait_horizon(2, invalid, invalid_roots)


def test_bound_target_reference_fixed_authentic_bind(support_local_ledger_factory, capsys):
    from audio_backend import native_owned_cells as cells
    original = support_local_ledger_factory(waiting=True, fixed='ordinary_first')
    original_doc, _, original_parts = cells.validate_document(original.cell.proof)
    original_full = cells.canonical(original_doc['target'])
    assert (len(original.cell.proof), len(original_full)) == (39449, 26894)
    assert len(original_parts) == 4 and original.row['wait_start_ordinal'] == 1
    assert original.first_registration['wait_last_ordinal'] == 4
    assert original.row['wait_last_ordinal'] == 4
    assert original.row['candidate_key'] == original.first_registration['candidate_key']
    assert original.row['target_body'] == original_full
    assert automatic._waiting_reason(original.row, original.context,
        original.row['plan_scope_key'], original.row['created']) is None
    original_before = _reference_snapshot(original)
    # Premerged ordinary-first at arrival2 is also not a lawful registration:
    # its first anchor remains1. Roll back every tentative write after the exact
    # unchanged metadata refusal, with no old database or production replay.
    with original.store.db() as db:
        db.execute('SAVEPOINT premerged_negative')
        try:
            db.execute(f'DELETE FROM {automatic.TABLE}')
            db.execute(f'UPDATE {automatic.USAGE} SET rows=0,charge=0,pending=0,proofs=0')
            db.execute('DELETE FROM native_core_commits WHERE commit_ordinal>2')
            db.execute('UPDATE recording_batches SET native_core_ordinal=2 WHERE id=?',
                (original.context['batch_id'],))
            with pytest.raises(Unavailable,match='^bounded_wait_metadata$'):
                automatic._register_target(db,original.original,{**original.context,'ordinal':2},
                    original.row['plan_scope_key'],original.row['created'],Budget())
        finally:
            db.execute('ROLLBACK TO premerged_negative');db.execute('RELEASE premerged_negative')
    assert _reference_snapshot(original) == original_before
    # Retain the independent seam-first comparison, including its real bind.
    f = support_local_ledger_factory(reference=True, fixed=True)
    row = f.row; doc, support, parts = cells.validate_document(row['proof'])
    assert f.initial['wait_start_ordinal'] == 2 and f.initial['wait_last_ordinal'] == 4
    assert automatic._waiting_reason(f.initial, f.context, f.initial['plan_scope_key'],
        f.initial['created']) is None
    assert doc['target']['origins'] == [original_doc['target']['origins'][2],
        original_doc['target']['origins'][0], original_doc['target']['origins'][1]]
    assert doc['commits'] == original_doc['commits']
    assert doc['inventories'] == original_doc['inventories']
    assert row['candidate_key'] != original.row['candidate_key']
    assert row['kind'] == 'core_seam' and original.row['kind'] == 'ordinary'
    assert row['slot2'] is not None and original.row['slot2'] is None
    assert row['proof_sha256'] != hashlib.sha256(original.cell.proof).hexdigest()
    assert doc['target']['key'] == original_doc['target']['key']
    for selected in (original,f):
        for origin in selected.case['target']['origins']:
            _,origin_support,_=cells.validate_document(origin['proof'].encode('ascii'))
            assert not (origin_support.start_frame<=0 and origin_support.end_frame>=32000)
        assert core.select_support(selected.case['target'],selected.case['commits'],
            selected.snapshot,0,32000)=={'start_frame':0,'end_frame':32000}
    assert _reference_snapshot(original) == original_before
    # The ordinary-first caller now enters its genuine ordinal4 binding attempt
    # without changing target order, PCM, source authority or certificate caps.
    with original.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        handoff={'waiters':[original.row],'batch':original.context,'reserved':True,
            'budget':Budget(),'origins':[],'plans':[original.final],
            'snapshot':original.snapshot,'draft_ids':[]}
        notice=automatic.enqueue_bounded(db,[],{'commit':original.case['commits'][-1]},
            handoff,{'session_id':original.row['sid']},[],None)
        assert notice['bound']==1 and notice['status']=='bound'
        original.row=automatic._read_v2(db,key=original.key)
        assert original.row['state']=='queued' and original.row['plan_phase']=='bound'
        assert original.row['candidate_key']==original.first_registration['candidate_key']
        assert original.row['job_id'] is None
        _reference_accounting(db,original.row['sid'])
    # Explicit saved-tight legacy control. A wider current helper never widens
    # this stored expiry, turns its terminal refusal into success, or reopens it.
    tight = support_local_ledger_factory(waiting=True, fixed='ordinary_first')
    with tight.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute(f'UPDATE {automatic.TABLE} SET wait_last_ordinal=3 WHERE candidate_key=?', (tight.key,))
        saved=automatic._read_v2(db,key=tight.key)
        assert automatic._waiting_reason(saved,tight.context,saved['plan_scope_key'],saved['created'])=='unresolved_commit_horizon'
        handoff={'waiters':[saved],'batch':tight.context,'reserved':True,
            'budget':Budget(),'origins':[],'plans':[tight.final],
            'snapshot':tight.snapshot,'draft_ids':[]}
        notice=automatic.enqueue_bounded(db,[],{'commit':tight.case['commits'][-1]},
            handoff,{'session_id':saved['sid']},[],None)
        assert notice['bound']==0 and notice['reason']=='unresolved_commit_horizon'
        closed=automatic._read_v2(db,key=tight.key)
        assert closed['state']=='no_result' and closed['plan_phase']=='closed'
        assert all(closed[key]==saved[key] for key in
            ('proof','target_body','cell_id','wait_start_ordinal','wait_last_ordinal','created','wait_deadline'))
        assert closed['job_id'] is None
        accounting=_reference_accounting(db,closed['sid'])
        again,status=automatic._register_target(db,tight.original,tight.context,
            closed['plan_scope_key'],closed['created']+1,Budget())
        assert status=='already_owned' and again==closed
        assert _reference_accounting(db,closed['sid'])==accounting
    for selected, order in ((original, 'ordinary-first-lifecycle'), (f, 'seam-first-lifecycle-variant')):
        row=selected.row; doc,support,parts=cells.validate_document(row['proof'])
        full = cells.canonical(doc['target']); reference = row['target_body']
        assert doc['target'] == selected.case['target'] and len(doc['target']['origins']) == 3
        assert (support.start_frame, support.end_frame) == (0, 32000)
        assert len(doc['commits']) == len(parts) == len({part.chunk_id for part in parts}) == 4
        assert all(part.logical_end > part.logical_start for part in parts)
        assert selected.cell.pcm == b'\0\0' * 32000
        assert row['proof'] == selected.cell.proof
        assert len(row['proof']) <= 65536 < len(row['proof']) + len(full)
        assert reference == automatic._bound_target_reference(row['proof'], doc['target'])
        assert json.loads(reference) == {'schema': 'bounded-target-proof-reference-v1',
            'target_key': doc['target']['key'], 'target_sha256': hashlib.sha256(full).hexdigest(),
            'proof_sha256': hashlib.sha256(row['proof']).hexdigest()}
        assert reference == json.dumps(json.loads(reference),sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
        assert len(reference) <= 512 and len(row['proof']) + len(reference) <= 65536
        assert resolved_target(row) == doc['target']
        assert row['planning_bytes'] == len(reference) and row['plan_revision'] == selected.initial['plan_revision'] + 1
        assert all(row[key]==selected.initial[key] for key in
            ('candidate_key','wait_start_ordinal','wait_last_ordinal','created','wait_deadline'))
        with selected.store.db() as db:
            _reference_accounting(db, row['sid'])
            observed = _ReferenceComparisonDB(db)
            assert automatic.qualify_bounded_document(observed, doc, Budget(), selected.store_key, automatic_key=selected.key)
            assert observed.comparisons == [(2, len(row['proof']) + len(reference))]
        with capsys.disabled():
            print('bound_target_reference_fixed ' + json.dumps({'synthetic_only': True,
                'historical_reproduction': False, 'ownership_order': order,
                'ordinary_first_lifecycle': 'bound', 'saved_tight_legacy': 'unresolved_commit_horizon',
                'origins': 3, 'positive_parts': 4, 'distinct_uploads': 4,
                'proof_bytes': len(row['proof']), 'full_target_bytes': len(full),
                'reference_bytes': len(reference), 'stored_certificate_bytes': len(row['proof']) + len(reference),
                'actual_charge_bytes': row['charge_bytes']}, sort_keys=True), flush=True)


def test_bound_target_reference_legacy_full_compatibility(support_local_ledger_factory):
    from audio_backend import native_owned_cells as cells
    # One finite compatibility case covers the four dispatch states, keeping
    # exact historical full wire/proof/binding/digests through close and reopen.
    for waiting in (True, False):
        f = support_local_ledger_factory(waiting=waiting, fixed='ordinary_first' if waiting else False)
        saved = {name: f.row[name] for name in ('target_body', 'proof', 'binding',
            'candidate_key', 'target_key', 'proof_sha256', 'cell_id')}
        assert json.loads(saved['target_body'])['schema'] == core.TARGET_SCHEMA
        for closed in (False, True):
            with f.store.db() as db:
                if closed:
                    automatic._terminal(db, _reference_row(f, db), 'no_result', 'synthetic_terminal')
                row, validated = automatic._read_v2(db, key=f.key, with_validation=True)
                assert {name: row[name] for name in saved} == saved
                assert validated[0] == f.case['target']
                assert validated[1:] == cells.validate_document(saved['proof'])
                assert row['plan_phase'] == ('closed' if closed else 'waiting' if waiting else 'bound')
                _reference_accounting(db, row['sid'])
            # Every iteration reopens the real database; no format rewrite is
            # triggered by reading either historical state.
            assert {name: _reference_row(f)[name] for name in saved} == saved


@pytest.mark.parametrize('mutation', ['unknown_schema', 'noncanonical', 'extra_key',
    'missing_key', 'wrong_type', 'invalid_digest', 'wrong_target_key',
    'wrong_target_hash', 'wrong_proof_hash', 'substituted_proof'])
def test_bound_target_reference_tamper_refusal(support_local_ledger_factory, mutation):
    f = support_local_ledger_factory(reference=True)
    value = json.loads(f.row['target_body']); proof = f.row['proof']
    if mutation == 'unknown_schema':value['schema'] = 'bounded-target-proof-reference-v2'
    elif mutation == 'extra_key':value['target'] = f.doc['target']
    elif mutation == 'missing_key':del value['proof_sha256']
    elif mutation == 'wrong_type':value['target_key'] = True
    elif mutation == 'invalid_digest':value['proof_sha256'] = 'A' * 64
    elif mutation == 'wrong_target_key':value['target_key'] = 'e' * 64
    elif mutation == 'wrong_target_hash':value['target_sha256'] = 'e' * 64
    elif mutation == 'wrong_proof_hash':value['proof_sha256'] = 'e' * 64
    elif mutation == 'substituted_proof':
        # A different fully valid historical inventory certificate for the same
        # target cannot satisfy this exact proof reference, even with row digest updated.
        other = support_local_ledger_factory(core.COMPACT_INVENTORY_SCHEMA, reference=True)
        proof = other.row['proof']; assert proof != f.row['proof']
    wire = automatic.canonical(value)
    if mutation == 'noncanonical':wire += b' '
    _set_reference_wire(f, wire, proof=proof)
    with f.store.db() as db:db.execute(f"UPDATE {automatic.TABLE} SET state='published' WHERE candidate_key=?", (f.key,))
    before = _reference_snapshot(f); identity = _reference_identity(f)
    reason = ('bounded_target_reference_policy' if mutation == 'unknown_schema' else
        'bounded_target_reference_shape' if mutation in ('noncanonical','extra_key','missing_key') else
        'invalid_digest' if mutation in ('wrong_type','invalid_digest') else 'bounded_target_reference_binding')
    with f.store.db() as db:
        statements = []; db.set_trace_callback(statements.append)
        for read in (lambda: automatic._read_v2(db, key=f.key),
                     lambda: automatic.admit(db, f.key, identity)):
            with pytest.raises(Unavailable, match="^"+reason+"$"):read()
        db.set_trace_callback(None)
    assert not any(sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for sql in statements)
    assert _reference_snapshot(f) == before and _reference_row(f)['job_id'] is None
    with f.store.db() as db:
        observed = _ReferenceComparisonDB(db)
        assert automatic._read_v2(observed, key=f.key, expected=_reference_expected(f), reference_aware=True) is None
        assert len(observed.comparisons) <= 1


@pytest.mark.parametrize('phase', ['ordinary_waiting', 'ordinary_closed_waiting', 'bounded_proof_waiting'])
def test_bound_target_reference_phase_refusal(support_local_ledger_factory, phase):
    f = support_local_ledger_factory(reference=True, waiting=phase != 'bounded_proof_waiting',
        fixed='ordinary_first' if phase != 'bounded_proof_waiting' else False)
    with f.store.db() as db:
        if phase == 'ordinary_closed_waiting':
            automatic._terminal(db, f.row, 'no_result', 'synthetic_closed_waiting')
        elif phase == 'bounded_proof_waiting':
            db.execute(f"UPDATE {automatic.TABLE} SET plan_phase='waiting' WHERE candidate_key=?", (f.key,))
    wire = automatic._bound_target_reference(f.row['proof'], f.case['target'])
    _set_reference_wire(f, wire)
    before = _reference_snapshot(f)
    with f.store.db() as db:
        with pytest.raises(Unavailable, match='^bounded_target_reference_phase$'):
            automatic._read_v2(db, key=f.key)
    assert _reference_snapshot(f) == before


@pytest.mark.parametrize('direction', ['full_to_reference', 'reference_to_full'])
def test_bound_target_reference_database_callback_equivalence(support_local_ledger_factory, direction):
    from audio_backend.native_owned_cell_capture import PrimaryAuthority
    f = support_local_ledger_factory(reference=direction == 'reference_to_full')
    proof = f.row['proof']; full = automatic.canonical(f.doc['target'])
    ref = automatic._bound_target_reference(proof, f.doc['target'])
    assert len(proof) + len(full) <= 65536
    old, new = (full, ref) if direction == 'full_to_reference' else (ref, full)
    boundaries = []
    for index, wire in enumerate((old, new, new)):
        if index == 1:_set_reference_wire(f, wire)
        # Database-callback equivalence only: this exact fresh callback is
        # invoked by core.current. The full C test separately switches both
        # directions through all three real owner/receipt/file fences.
        with Budget().phase() as budget:
            authority = PrimaryAuthority(f.store)
            assert authority._read(budget, 'automatic_intent', lambda db:
                automatic.qualify_bounded_document(db, f.doc, budget,
                    f.store_key, automatic_key=f.key))
        with f.store.db() as db:
            observed = _ReferenceComparisonDB(db)
            row, resolved = automatic._read_v2(observed, key=f.key, expected=(proof, full),
                reference_aware=True, with_validation=True, budget=Budget())
            assert row['target_body'] == wire and row['proof'] == proof and resolved[0] == f.doc['target']
            assert observed.comparisons == [(2, len(proof) + len(wire))]
            _reference_accounting(db, row['sid'])
            boundaries.append(row['proof_sha256'])
    assert boundaries == [f.row['proof_sha256']] * 3


@pytest.mark.parametrize('stale', ['planning_bytes', 'charge_bytes'])
def test_bound_target_reference_stale_switch_accounting(support_local_ledger_factory, stale):
    f = support_local_ledger_factory()
    _set_reference_wire(f, automatic._bound_target_reference(f.row['proof'], f.doc['target']), stale=stale)
    before = _reference_snapshot(f)
    with f.store.db() as db:
        with pytest.raises(Unavailable, match='^bounded_original_owner_binding$'):
            automatic._read_v2(db, key=f.key, expected=_reference_expected(f), reference_aware=True)
    assert _reference_snapshot(f) == before


def test_bound_target_reference_exact_comparison_contract(support_local_ledger_factory, monkeypatch):
    f = support_local_ledger_factory(reference=True)
    proof, full = _reference_expected(f); ref = f.row['target_body']
    assert len(full) != len(ref)
    for wire in (full, ref):
        _set_reference_wire(f, wire)
        with f.store.db() as db:
            observed = _ReferenceComparisonDB(db); budget = Budget()
            row = automatic._read_v2(observed, key=f.key, expected=(proof, full),
                reference_aware=True, budget=budget)
            assert row['target_body'] == wire
            selects = [(sql, values) for sql, values in observed.calls
                if f'FROM {automatic.TABLE} ' in sql]
            assert len(selects) == 2 and selects[0][0].startswith('SELECT length(binding),length(proof),length(target_body)')
            assert 'proof=? AS proof_same' in selects[1][0]
            assert selects[1][1] == (proof, full if wire == full else None, ref if wire == ref else None, f.key)
            assert observed.comparisons == [(2, len(proof) + len(wire))]
            assert budget.sql_bytes == (len(row['binding']) + 1024 + 32 + 320 +
                sum(160 + len(root['cid']) for root in f.doc['dependency_roots']))
    # A byte mismatch with matching length gets one comparison and no fallback.
    _set_reference_wire(f, ref[:-1] + b'!')
    with f.store.db() as db:
        observed = _ReferenceComparisonDB(db)
        assert automatic._read_v2(observed, key=f.key, expected=(proof, full), reference_aware=True) is None
        assert len(observed.calls) == 2 and len(observed.comparisons) == 1
    # Neither option matches this exact length, so only the length preflight runs.
    _set_reference_wire(f, ref + b' ')
    with f.store.db() as db:
        observed = _ReferenceComparisonDB(db)
        assert automatic._read_v2(observed, key=f.key, expected=(proof, full), reference_aware=True) is None
        assert len(observed.calls) == 1 and not observed.comparisons
    _set_reference_wire(f, full)
    # Defense-only equal-length encoder seam. Two matching booleans may never
    # silently pick the first option. This is not an authentic wire format.
    with monkeypatch.context() as patch, f.store.db() as db:
        patch.setattr(automatic, '_bound_target_reference', lambda actual, target: full)
        observed = _ReferenceComparisonDB(db)
        if len(proof) + 2 * len(full) <= automatic.PROOF_BYTES:
            assert automatic._read_v2(observed, key=f.key, expected=(proof, full), reference_aware=True) is None
            assert observed.comparisons == [(3, len(proof) + 2 * len(full))]
        else:
            with pytest.raises(Unavailable, match='^bounded_certificate_capacity$'):
                automatic._read_v2(observed, key=f.key, expected=(proof, full), reference_aware=True)
            assert len(observed.calls) == 1 and not observed.comparisons
    # Defense-only input arithmetic at the immutable 65536-byte boundary. The
    # strict validator is replaced solely to reach an otherwise impossible
    # equal-length full/reference operand shape; no authority result is used.
    padded = b'p' * (automatic.PROOF_BYTES - len(full))
    class LengthOnly:
        def __init__(self):self.calls=[]
        def execute(self, sql, values):
            assert sql.startswith('SELECT length(binding),length(proof),length(target_body)')
            self.calls.append((sql, values)); assert len(self.calls) == 1
            return self
        def fetchone(self):return (len(f.row['binding']), len(padded), len(full))
    db = LengthOnly()
    with monkeypatch.context() as patch:
        patch.setattr(automatic, 'validate_document', lambda raw: (f.doc, None, None))
        patch.setattr(automatic, '_bound_target_reference', lambda raw, target: full)
        with pytest.raises(Unavailable, match='^bounded_certificate_capacity$') as caught:
            automatic._read_v2(db, key=f.key, expected=(padded, full), reference_aware=True)
    assert caught.value.certificate_capacity == {'guard': 'expected_certificate', 'check': 'certificate',
        'proof_bytes': len(padded), 'target_bytes': 2 * len(full),
        'total_bytes': len(padded) + 2 * len(full), 'minimum_bytes': 2, 'limit_bytes': 65536}
    assert len(db.calls) == 1


@pytest.mark.parametrize('sign', ['positive', 'zero', 'negative'])
def test_bound_target_reference_signed_accounting_delta(support_local_ledger_factory, monkeypatch, sign):
    f = support_local_ledger_factory(waiting=True)
    local = core.localize_inventory(f.case['target'], f.final['commits'], f.snapshot,
        f.final['query']['query_start_frame'], f.final['query']['query_end_frame'], f.final['support'])
    cell = core.make_bounded_cell(f.final['commits'], f.final['query'], f.final['evidence'],
        f.final['pcm'], f.case['target'], local)
    doc = json.loads(cell.proof)
    binding = automatic.source_binding_v2(f.row['kind'], doc, cell.support, cell.parts, f.context)
    ref = automatic._bound_target_reference(cell.proof, doc['target'])
    delta = {'positive': 1, 'zero': 0, 'negative': -1}[sign]
    # Explicit arithmetic-only input seam: old bytes below are not a valid
    # waiting certificate. They make exact signed deltas reachable without a
    # cap change or pretending that padding is authentic storage benefit.
    old_proof = b'p' * (len(cell.proof) + len(ref) - len(f.row['target_body']) - delta)
    old_binding = b'b' * len(binding)
    assert 1 <= len(old_proof) and len(old_proof) + len(f.row['target_body']) <= 65536
    assert 1 <= len(old_binding) <= 4096
    with f.store.db() as db:
        charge = automatic.ACCOUNTING_BYTES + len(old_proof) + len(old_binding) + len(f.row['target_body'])
        old_cert = len(f.row['proof']) + len(f.row['target_body'])
        db.execute(f'UPDATE {automatic.TABLE} SET proof=?,proof_sha256=?,binding=?,charge_bytes=? WHERE seq=?',
            (old_proof, hashlib.sha256(old_proof).hexdigest(), old_binding, charge, f.row['seq']))
        automatic._charge(db, f.row['sid'], (0, charge-f.row['charge_bytes'], 0,
            len(old_proof)+len(f.row['target_body'])-old_cert))
        before = _reference_accounting(db, f.row['sid'])
        row = _reference_row(f, db); reserves=[]; deltas=[]
        capacity = automatic.capacity; charge_usage = automatic._charge
        def reserve(database, sid, **values):
            reserves.append(values); return capacity(database, sid, **values)
        def apply(database, sid, values):
            deltas.append(values); return charge_usage(database, sid, values)
        with monkeypatch.context() as patch:
            patch.setattr(automatic, 'capacity', reserve); patch.setattr(automatic, '_charge', apply)
            assert automatic._bind_target(db, row, f.final, f.context, f.snapshot, Budget(),
                row['created'], support_local=True) == cell.cell_id
        bound = automatic._read_v2(db, key=f.key)
        after = _reference_accounting(db, f.row['sid'])
        assert bound['proof'] == cell.proof and bound['binding'] == binding and bound['target_body'] == ref
        assert bound['planning_bytes'] == len(ref) and bound['plan_revision'] == row['plan_revision']+1
        assert reserves == [{'rows': 0, 'pending': 0, 'proofs': max(0, delta), 'charge': max(0, delta)}]
        assert deltas == [(0, delta, 0, delta)]
        assert [tuple(b-a for a,b in zip(old,new)) for old,new in zip(before,after)] == [(0,delta,0,delta)]*2


@pytest.mark.parametrize('failure', ['compare_and_swap', 'after_charge_sql_fault'])
def test_bound_target_reference_failed_bind_atomicity(support_local_ledger_factory, monkeypatch, failure):
    f = support_local_ledger_factory(waiting=True); before = _reference_snapshot(f); charged=[]
    apply = automatic._charge
    def charge(db, sid, delta):
        apply(db, sid, delta); charged.append(delta)
    with monkeypatch.context() as patch:
        patch.setattr(automatic, '_charge', charge)
        if failure == 'after_charge_sql_fault':
            with f.store.db() as db:
                db.execute(f'''CREATE TRIGGER reference_bind_abort BEFORE UPDATE OF plan_phase ON {automatic.TABLE}
                    WHEN NEW.plan_phase='bound' BEGIN SELECT RAISE(ABORT,'reference bind hard fault'); END''')
            error, reason = sqlite3.DatabaseError, 'reference bind hard fault'
        else:error, reason = Unavailable, '^bounded_bind_compare_and_swap$'
        with pytest.raises(error, match=reason):
            with f.store.db() as db:
                row = dict(f.row)
                if failure == 'compare_and_swap':row['plan_revision'] += 1
                automatic._bind_target(db, row, f.final, f.context, f.snapshot, Budget(),
                    row['created'], support_local=True)
    assert len(charged) == 1
    assert _reference_snapshot(f) == before
    with f.store.db() as db:
        row = automatic._read_v2(db, key=f.key)
        assert row == f.row and row['plan_phase'] == 'waiting'
        assert row['target_body'] == automatic.canonical(f.case['target'])
        _reference_accounting(db, row['sid'])


@pytest.mark.parametrize('root_kind', ['original_zero_pcm', 'final_commit', 'whole_parent_lexical_source'])
def test_bound_target_reference_all_root_authority(support_local_ledger_factory, root_kind):
    f = support_local_ledger_factory(reference=True); before=f.row['proof']; cid='source-0'
    if root_kind == 'original_zero_pcm':
        _, originals = core.validate_target(f.doc['target'])
        assert any(commit['part']['chunk_id'] == cid for commit in originals)
        assert all(part['chunk_id'] != cid for part in f.doc['dependencies'])
        assert all(source['id'] != cid for source in f.doc['inventories']['sources'])
    elif root_kind == 'final_commit':cid=f.doc['commits'][-1]['part']['chunk_id']
    else:
        cid='source-2'
        assert any(source['id'] == cid for source in f.doc['inventories']['sources'])
        assert f.case['sources'][cid]['timeline_start'] >= f.doc['geometry']['end_frame']/RATE
        assert all(part['chunk_id'] != cid for part in f.doc['dependencies'])
    assert _local_binding(f)[1:3] == (16,24)
    with f.store.db() as db:
        automatic._check_dependencies(db, f.row['seq'], f.doc['dependency_roots'])
        for root in f.doc['dependency_roots']:
            assert automatic.retention_root(db, cid=root['cid'])
            if root['slot'] is not None:assert automatic.retention_root(db, slot=root['slot'])
        automatic._terminal(db, f.row, 'no_result', 'synthetic_terminal_roots')
        if root_kind == 'final_commit':
            db.execute("UPDATE native_core_commits SET state='cancelled' WHERE slot=?", (core.slot(f.doc['commits'][-1]),))
        else:db.execute('UPDATE chunks SET digest=? WHERE id=?', ('e'*64,cid))
    reason = {'original_zero_pcm': 'automatic_source_unplaced', 'final_commit': 'bounded_compact_changed',
        'whole_parent_lexical_source': 'bounded_inventory_source_changed'}[root_kind]
    with pytest.raises(ReadError, match='^'+reason+'$'):_local_binding(f)
    with f.store.db() as db:
        terminal=automatic._read_v2(db,key=f.key)
        assert terminal['plan_phase']=='closed' and terminal['proof']==before
        assert terminal['target_body']==f.row['target_body']
        automatic._check_dependencies(db, terminal['seq'], f.doc['dependency_roots'])
        assert automatic.retention_root(db,cid=cid)
        _reference_accounting(db, terminal['sid'])


def _reference_controller(f, *, submissions=None, publications=None):
    from types import SimpleNamespace
    submissions = [] if submissions is None else submissions
    publications = [] if publications is None else publications
    @contextmanager
    def database(*, write=False, budget=None):
        with f.store.db() as db:yield db
    identity = _reference_identity(f)
    def submit(selected):
        with f.store.db() as db:automatic.admit(db, selected['candidate_key'], identity)
        submissions.append(selected['candidate_key'])
    def publish(*args):publications.append(args); raise AssertionError('unexpected publication')
    jobs=SimpleNamespace(_enabled=lambda: True, automatic_idle=lambda: True, submit_automatic=submit)
    capture=SimpleNamespace(owner=SimpleNamespace(_slot=None, submit=publish))
    return automatic.Automatic(f.store, SimpleNamespace(_database=database), capture=capture, jobs=jobs)


def test_bound_target_reference_terminal_cancel_restart_lost_ack(support_local_ledger_factory):
    from types import SimpleNamespace
    from threading import Event
    f = support_local_ledger_factory(reference=True); submitted=[]; published=[]
    controller = _reference_controller(f, submissions=submitted, publications=published)
    row=f.row; identity=_reference_identity(f); event=Event(); event.set()
    op=SimpleNamespace(slot_released=event, deadline=time.monotonic()+1., result={
        'status':'durable', 'cells':[{'cell_id':row['cell_id'], 'status':'existing'}]})
    # The exact already-retained owner acknowledgement advances once. A lost
    # caller acknowledgement after the durable ledger write is not a new job.
    controller.flight=(f.key,op,row['cell_id']); controller._finish_flight(lambda:False)
    assert submitted==[f.key] and controller.flight is None and not published
    accepted=_reference_row(f); assert accepted['state']=='accepted' and accepted['job_id']==identity.job_id
    controller._admit_key(f.key); assert submitted==[f.key]
    with f.store.db() as db:
        automatic.complete(db,f.key,identity)
        committed=automatic._read_v2(db,key=f.key)
        before=_reference_accounting(db,row['sid'])
        automatic.complete(db,f.key,identity)
        assert automatic._read_v2(db,key=f.key)==committed
        assert _reference_accounting(db,row['sid'])==before
    assert committed['state']=='committed' and committed['plan_phase']=='closed'
    assert all(committed[name]==row[name] for name in ('proof','proof_sha256','target_body','binding','charge_bytes'))
    restarted=_reference_controller(f,submissions=submitted,publications=published)
    assert not restarted.current()  # Fixture settings remain automatic-disabled.
    assert restarted.read_candidate(f.key,proof=True)==committed
    assert restarted._snapshot() is False
    restarted._admit_key(f.key); assert submitted==[f.key] and not published
    # Accepted-unknown recovery and explicit cancel retain their own bound refs,
    # charges and roots. They never erase a proof or create a retry instruction.
    for outcome in ('interrupted','cancelled'):
        other=support_local_ledger_factory(reference=True); other_identity=_reference_identity(other)
        with other.store.db() as db:
            if outcome=='interrupted':
                db.execute(f"UPDATE {automatic.TABLE} SET state='published' WHERE candidate_key=?",(other.key,))
                automatic.admit(db,other.key,other_identity)
                automatic.reconcile_job(db,other_identity.job_id)
            else:automatic.cancel_session(db,other.row['sid'])
            terminal=automatic._read_v2(db,key=other.key)
            assert terminal['state']==outcome and terminal['plan_phase']=='closed'
            assert terminal['proof']==other.row['proof'] and terminal['target_body']==other.row['target_body']
            for root in other.doc['dependency_roots']:
                assert automatic.retention_root(db,cid=root['cid'])
                if root['slot'] is not None:assert automatic.retention_root(db,slot=root['slot'])
            _reference_accounting(db,terminal['sid'])
        fresh=_reference_controller(other)
        assert fresh._snapshot() is False and fresh.read_candidate(other.key,proof=True)==terminal


def test_bound_target_reference_pinned_old_reader_refuses(support_local_ledger_factory, monkeypatch):
    f=support_local_ledger_factory(reference=True); old=_source70_target_reader_oracle()
    assert '_resolve_target_body' not in old and '_bound_target_reference' not in old
    with f.store.db() as db:
        with pytest.raises(Unavailable,match='^core_seam_shape$'):
            old['validate_v2_row'](db,f.row,f.row['proof'])
    before=_reference_snapshot(f); published=[]; admitted=[]
    controller=_reference_controller(f,submissions=admitted,publications=published)
    # Pin the complete old read/validation closure, not merely a shape lambda
    # that might call the new resolver indirectly. This is one old-reader
    # refusal contract, not a claim that an old binary is globally read-only.
    with monkeypatch.context() as patch:
        patch.setattr(automatic,'_read_v2',old['_read_v2'])
        with pytest.raises(Unavailable,match='^core_seam_shape$'):
            controller.read_candidate(f.key,proof=True)
        f.store.settings.moss_automatic_enabled=True
        controller.step()
        assert controller.flight is None and not published and not admitted
        with f.store.db() as db:
            db.execute(f"UPDATE {automatic.TABLE} SET state='published' WHERE candidate_key=?",(f.key,))
        published_before=_reference_snapshot(f)
        with pytest.raises(Unavailable,match='^core_seam_shape$'):controller._admit_key(f.key)
        with f.store.db() as db:
            with pytest.raises(Unavailable,match='^core_seam_shape$'):
                automatic.admit(db,f.key,_reference_identity(f))
        assert _reference_snapshot(f)==published_before and not published and not admitted
    with f.store.db() as db:
        db.execute(f"UPDATE {automatic.TABLE} SET state='queued' WHERE candidate_key=?",(f.key,))
    assert _reference_snapshot(f)==before


def test_bound_target_reference_caps_and_quota_boundaries(support_local_ledger_factory):
    from audio_backend import native_owned_cells as cells
    assert (automatic.PROOF_BYTES,automatic.BINDING_BYTES,automatic.MAX_CHARGE,
        automatic.BOUND_TARGET_REFERENCE_BYTES)==(65536,4096,86016,512)
    f=support_local_ledger_factory(reference=True,fixed=True)
    proof,full=_reference_expected(f); ref=f.row['target_body']
    assert len(proof)+len(ref)<=65536<len(proof)+len(full)
    # The reference cannot legalize its over-cap full equivalent or an over-cap
    # proof. The private historical expected tuple keeps its pre-SQL contract.
    with pytest.raises(Unavailable,match='^bounded_certificate_capacity$'):
        automatic._read_v2(None,key=f.key,expected=(proof,full))
    oversized=b'p'*65537
    with pytest.raises(Unavailable,match='^proof_capacity$'):
        automatic._resolve_target_body(ref,oversized,'bound')
    full_row={**f.row,'target_body':full,'planning_bytes':len(full),
        'charge_bytes':f.row['charge_bytes']+len(full)-len(ref)}
    with pytest.raises(Unavailable,match='^bounded_certificate_capacity$'):
        automatic.validate_v2_row(None,full_row,proof)
    class LengthOnly:
        def __init__(self,sizes):self.sizes=sizes;self.calls=0
        def execute(self,sql,values):
            assert sql.startswith('SELECT length(binding),length(proof),length(target_body)')
            self.calls+=1;assert self.calls==1;return self
        def fetchone(self):return self.sizes
    for sizes in ((4097,len(proof),len(ref)),(1,65536,1)):
        db=LengthOnly(sizes)
        with pytest.raises(Unavailable,match='^bounded_certificate_capacity$'):
            automatic._read_v2(db,key=f.key)
        assert db.calls==1
    # Every quota is still inclusive at its exact current remaining capacity,
    # and the next byte/row/pending item refuses without changing either scope.
    with f.store.db() as db:
        totals=automatic.usage(db,f.row['sid'])
        limits=((automatic.MAX_ROWS,automatic.MAX_BYTES,automatic.MAX_PENDING,automatic.MAX_PROOFS),
            (automatic.SESSION_ROWS,automatic.SESSION_BYTES,automatic.SESSION_PENDING,automatic.SESSION_PROOFS))
        for column,name in enumerate(('rows','charge','pending','proofs')):
            remaining=min(limit[column]-have[column] for have,limit in zip(totals,limits))
            values=dict(rows=0,charge=0,pending=0,proofs=0);values[name]=remaining
            assert automatic.capacity(db,f.row['sid'],**values)==totals
            values[name]+=1
            reason={'rows':'rows','charge':'bytes','pending':'pending','proofs':'proof_bytes'}[name]
            with pytest.raises(ReadError,match='^automatic_'+reason+'_capacity$'):
                automatic.capacity(db,f.row['sid'],**values)
        assert automatic.usage(db,f.row['sid'])==totals
        _reference_accounting(db,f.row['sid'])
    before=_reference_snapshot(f)
    with pytest.raises(sqlite3.IntegrityError):
        with f.store.db() as db:
            db.execute(f'UPDATE {automatic.TABLE} SET charge_bytes=? WHERE candidate_key=?',
                (automatic.MAX_CHARGE+1,f.key))
    assert _reference_snapshot(f)==before
    assert cells.PROOF_BYTES==automatic.PROOF_BYTES


# Frozen exact source70 reader and automatic-helper closure from source SHA256
# d99722d4da243e9c928399e126b092993897a38cb03b81dfdfd53d6a923e7d02.
_SOURCE70_TARGET_READER_SHA256 = '886a51090b2244517005ec64010cb36305a8a303aa1df1f77e20bf0df6f019fd'
_SOURCE70_TARGET_READER_ORACLE = (
    'def has_bounded_schema(db):\n'
    "    return db.execute('SELECT 1 FROM sqlite_master WHERE type=\\'table\\' AND name=?',(DEPENDENCIES,)).fetchone() is not None\n"
    '\n'
    'def extended_row(db,row,*,proof=False):\n'
    '    if row is None:return None\n'
    '    result=dict(row)\n'
    '    if all(name in result for name in V2_COLUMNS):return result\n'
    '    if has_bounded_schema(db):\n'
    "        names=','.join(V2_COLUMNS)\n"
    "        extra=db.execute(f'SELECT {names} FROM {TABLE} WHERE seq=?',(row['seq'],)).fetchone()\n"
    '        result.update(dict(extra))\n'
    '    else:result.update({name:None for name in V2_COLUMNS})\n'
    '    return result\n'
    '\n'
    'def _original_dependencies(target):\n'
    '    from .native_owned_core_seams import validate_target,slot\n'
    '    _,commits=validate_target(target)\n'
    "    return sorted(({'kind':1,'cid':c['part']['chunk_id'],'slot':slot(c)} for c in commits),\n"
    "                  key=lambda item:(item['cid'],item['slot']))\n"
    '\n'
    'def _check_dependencies(db,seq,expected,budget=None):\n'
    "    values=db.execute(f'''SELECT dependency_kind,ordinal,cid,slot FROM {DEPENDENCIES}\n"
    "        WHERE intent_seq=? ORDER BY ordinal LIMIT 133''',(seq,)).fetchall()\n"
    "    if budget is not None:budget.projection(sum(160+len(r['cid']) for r in values))\n"
    "    actual=[{'kind':r['dependency_kind'],'cid':r['cid'],'slot':r['slot']} for r in values]\n"
    "    if [r['ordinal'] for r in values]!=list(range(len(expected))) or actual!=expected:\n"
    "        raise Unavailable('bounded_dependency_index_mismatch')\n"
    '\n'
    'def source_binding(kind,doc,support,parts,commits):\n'
    "    binding={'automation_policy':POLICY,'selection_policy':doc['policy'],\n"
    "        'kind':kind,'session_id':parts[0].session_id,\n"
    "        'parts':[p.content() for p in parts],\n"
    "        'source_aliases':[{'chunk_id':p.chunk_id,'receipt_sha256':p.receipt_sha256} for p in parts],\n"
    "        'support_frames':[support.start_frame,support.end_frame],\n"
    "        'core_frames':[[c['part']['logical_start'],c['part']['logical_end']] for c in commits]}\n"
    '    return canonical(binding,BINDING_BYTES)\n'
    '\n'
    'def source_binding_v2(kind,doc,support,parts,context):\n'
    '    try:\n'
    "        binding=json.loads(source_binding(kind,doc,support,parts,doc['commits']))\n"
    "        binding.update(schema='automatic-recording-overlap-v2',target_key=doc['target']['key'],\n"
    "            target_sha256=doc['target_sha256'],dependency_roots=doc['dependency_roots'],\n"
    "            plan_ordinal=context['ordinal'],plan_batch_id=context['batch_id'],\n"
    "            retention_store_key=context['retention_store_key'])\n"
    '        return canonical(binding,BINDING_BYTES)\n'
    '    except Unavailable as exc:\n'
    "        if str(exc)=='serialization_capacity' and getattr(exc,'capacity_phase',None) is None:\n"
    "            exc.capacity_phase='source_binding'\n"
    '        raise\n'
    '\n'
    'def _certificate_detail(guard, proof_bytes=None, target_bytes=None, binding_bytes=None):\n'
    '    """Fixed numeric annotation only; never inspect or serialize a payload."""\n'
    "    fields = {'proof_bytes': proof_bytes, 'target_bytes': target_bytes}\n"
    "    if guard in ('stored_certificate', 'new_charge', 'bound_charge'):\n"
    "        fields['binding_bytes'] = binding_bytes\n"
    "    result = {'guard': guard, 'check': 'shape'}\n"
    '    result.update((key, value) for key, value in fields.items()\n'
    '                  if type(value) is int and 0 <= value <= MAX_FRAME)\n'
    '    if len(result) != len(fields) + 2:\n'
    '        return result\n'
    "    if guard == 'stored_certificate' and not 1 <= binding_bytes <= BINDING_BYTES:\n"
    "        result.update(check='binding', total_bytes=binding_bytes,\n"
    '                      minimum_bytes=1, limit_bytes=BINDING_BYTES)\n'
    "    elif guard in ('new_charge', 'bound_charge'):\n"
    '        total = ACCOUNTING_BYTES + proof_bytes + target_bytes + binding_bytes\n'
    '        if total <= MAX_FRAME:\n'
    "            result.update(check='charge', accounting_bytes=ACCOUNTING_BYTES,\n"
    '                          total_bytes=total, minimum_bytes=0, limit_bytes=MAX_CHARGE)\n'
    '    else:\n'
    '        total = proof_bytes + target_bytes\n'
    '        if total <= MAX_FRAME:\n'
    "            result.update(check='certificate', total_bytes=total,\n"
    '                          minimum_bytes=2, limit_bytes=PROOF_BYTES)\n'
    '    return result\n'
    '\n'
    'def _certificate_capacity(guard, proof_bytes=None, target_bytes=None, binding_bytes=None):\n'
    "    error = Unavailable('bounded_certificate_capacity')\n"
    '    error.certificate_capacity = _certificate_detail(guard, proof_bytes, target_bytes, binding_bytes)\n'
    '    return error\n'
    '\n'
    'def validate_v2_row(db,row,proof,budget=None):\n'
    '    """Strict version dispatch. Legacy candidate hashing is never relaxed."""\n'
    '    from .native_owned_core_seams import validate_target,slot,BOUNDED_SCHEMA\n'
    '    from .native_owned_cells import digest,text\n'
    '    row=extended_row(db,row)\n'
    "    if row['target_key'] is None:\n"
    "        if any(row[name] is not None for name in V2_COLUMNS):raise Unavailable('automatic_mixed_version')\n"
    '        return None\n'
    "    if any(row[name] is None for name in V2_COLUMNS):raise Unavailable('automatic_mixed_version')\n"
    "    raw=row['target_body']\n"
    '    if type(raw) is not bytes or type(proof) is not bytes or not 2<=len(raw)+len(proof)<=PROOF_BYTES:\n'
    "        raise _certificate_capacity('row_certificate', len(proof) if type(proof) is bytes else None, len(raw) if type(raw) is bytes else None)\n"
    '    target=json.loads(raw); identity,originals=validate_target(target)\n'
    "    if canonical(target)!=raw or target['key']!=row['target_key'] or identity['scope']['session_id']!=row['sid']:\n"
    "        raise Unavailable('bounded_target_binding')\n"
    "    first=target['origins'][0]; original_binding=first['binding'].encode('ascii')\n"
    "    first_commits=first['commits']; first_slots=[slot(c) for c in first_commits]\n"
    "    digest(row['target_key']);digest(row['plan_scope_key']);text(row['plan_batch_id'])\n"
    "    start,last,revision=(row[name] for name in ('wait_start_ordinal','wait_last_ordinal','plan_revision'))\n"
    '    if (any(type(n) is not int or not 0<=n<(1<<63)-1 for n in (start,last,revision)) or start<1\n'
    '            or not start<=last<=start+min(3,4-len(originals))\n'
    "            or row['plan_scope_key']!=sha(first_commits[0]['scope'])\n"
    "            or row['kind']!=json.loads(original_binding)['kind']\n"
    '            or any(type(row[name]) not in (int,float) or not math.isfinite(row[name])\n'
    "                   for name in ('created','updated','wait_deadline'))\n"
    "            or row['wait_deadline']!=row['created']+300.\n"
    "            or row['plan_phase'] not in ('waiting','bound','closed')\n"
    "            or revision<(0 if row['plan_phase']=='waiting' else 1)\n"
    "            or (row['plan_phase']=='closed')!=(row['state'] not in ACTIVE)\n"
    "            or row['plan_phase']=='waiting' and row['state']!='queued'):\n"
    "        raise Unavailable('bounded_wait_metadata')\n"
    '    if budget is not None:budget.projection(320)\n'
    "    anchor=db.execute('''SELECT recording_batch_id,commit_ordinal,retention_store_key\n"
    "        FROM native_core_commits WHERE slot=?''',(first_slots[-1],)).fetchone()\n"
    "    if (anchor is None or anchor['recording_batch_id']!=row['plan_batch_id']\n"
    "            or anchor['commit_ordinal']!=start):raise Unavailable('bounded_wait_metadata')\n"
    "    digest(anchor['retention_store_key'])\n"
    "    if (hashlib.sha256(original_binding).hexdigest()!=row['candidate_key']\n"
    "            or row['slot1']!=first_slots[0] or row['slot2']!=(first_slots[1] if len(first_slots)==2 else None)\n"
    "            or row['cid1']!=first_commits[0]['part']['chunk_id']\n"
    "            or row['cid2']!=(first_commits[1]['part']['chunk_id'] if len(first_commits)==2 else None)\n"
    "            or row['planning_bytes']!=len(raw)\n"
    "            or row['charge_bytes']!=ACCOUNTING_BYTES+len(raw)+len(proof)+len(row['binding'])\n"
    "            or row['proof_sha256']!=hashlib.sha256(proof).hexdigest()):\n"
    "        raise Unavailable('bounded_original_owner_binding')\n"
    '    doc,support,parts=validate_document(proof)\n'
    "    if row['cell_id']!=sha({'policy':doc['policy'],'support':support.to_dict()}):\n"
    "        raise Unavailable('bounded_cell_binding')\n"
    "    if doc.get('schema')==BOUNDED_SCHEMA:\n"
    "        if row['plan_phase'] not in ('bound','closed') or doc['target']!=target:raise Unavailable('bounded_phase_binding')\n"
    "        binding=json.loads(row['binding'])\n"
    "        context={'ordinal':binding.get('plan_ordinal'),'batch_id':binding.get('plan_batch_id'),\n"
    "                 'retention_store_key':binding.get('retention_store_key')}\n"
    "        digest(context['retention_store_key'])\n"
    "        ordinal=context['ordinal']\n"
    "        if (type(ordinal) is not int or not row['wait_start_ordinal']<=ordinal<=row['wait_last_ordinal']\n"
    "                or context['batch_id']!=row['plan_batch_id']\n"
    "                or row['binding']!=source_binding_v2(row['kind'],doc,support,parts,context)):\n"
    "            raise Unavailable('bounded_final_binding')\n"
    "        expected=doc['dependency_roots']\n"
    '    else:\n'
    "        if row['plan_phase'] not in ('waiting','closed') or row['binding']!=original_binding or proof!=first['proof'].encode('ascii'):\n"
    "            raise Unavailable('bounded_waiting_binding')\n"
    "        if row['job_id'] is not None:raise Unavailable('bounded_waiter_admitted')\n"
    '        expected=_original_dependencies(target)\n'
    "    _check_dependencies(db,row['seq'],expected,budget)\n"
    '    return target,doc,support,parts\n'
    '\n'
    'def _read_v2(db,*,key=None,target_key=None,budget=None,with_validation=False,expected=None):\n'
    '    # Publication already holds the validated canonical certificate. Compare\n'
    '    # those exact bytes in this fresh snapshot rather than project them again.\n'
    '    # No result/row survives the callback; other readers keep the full read.\n'
    '    if expected is not None:\n'
    '        if (type(expected) is not tuple or len(expected)!=2\n'
    '                or any(type(raw) is not bytes for raw in expected)\n'
    '                or not 2<=sum(map(len,expected))<=PROOF_BYTES):\n'
    "            raise _certificate_capacity('expected_certificate', len(expected[0]) if type(expected) is tuple and len(expected)==2 and type(expected[0]) is bytes else None, len(expected[1]) if type(expected) is tuple and len(expected)==2 and type(expected[1]) is bytes else None)\n"
    "    field,value=('candidate_key',key) if key is not None else ('target_key',target_key)\n"
    "    sizes=db.execute(f'SELECT length(binding),length(proof),length(target_body) FROM {TABLE} WHERE {field}=?',(value,)).fetchone()\n"
    '    if sizes is None:return None\n'
    '    if (any(type(n) is not int for n in sizes) or not 1<=sizes[0]<=BINDING_BYTES\n'
    "            or not 2<=sizes[1]+sizes[2]<=PROOF_BYTES):raise _certificate_capacity('stored_certificate', sizes[1], sizes[2], sizes[0])\n"
    '    if expected is None:\n'
    '        if budget is not None:budget.projection(sum(sizes)+1024)\n'
    "        row=dict(db.execute(f'SELECT {COLUMNS},proof,'+','.join(V2_COLUMNS)+f' FROM {TABLE} WHERE {field}=?',(value,)).fetchone())\n"
    '    else:\n'
    '        proof,target=expected\n'
    '        if (sizes[1],sizes[2])!=(len(proof),len(target)):return None\n'
    '        if budget is not None:budget.projection(sizes[0]+1024+32)\n'
    "        names=','.join(name for name in V2_COLUMNS if name!='target_body')\n"
    "        found=db.execute(f'''SELECT {COLUMNS},{names},proof=? AS proof_same,\n"
    '            target_body=? AS target_same FROM {TABLE} WHERE {field}=?\n'
    "            AND typeof(proof)='blob' AND typeof(target_body)='blob' ''',\n"
    '            (proof,target,value)).fetchone()\n'
    "        if found is None or found['proof_same']!=1 or found['target_same']!=1:return None\n"
    "        row=dict(found);del row['proof_same'];del row['target_same']\n"
    '        row.update(proof=proof,target_body=target)\n'
    "    validated=validate_v2_row(db,row,row['proof'],budget)\n"
    '    return (row,validated) if with_validation else row\n'
)
_SOURCE70_UNCHANGED_HELPER_PINS = {'native_owned_core_seams': '3608374af430d7e7396ecf32d726f02a96356a35bed89c85ea15f35c6609bdfc', 'native_owned_cells': '416718496c6ba1f3c8eaeb701a694c7882654bbd890c8af54c58ed60d7dbb6f3', 'native_owned_seam_cells': 'f0c5addf9932027d5fb76bdeadf1d03d50b5efd75785ee946e27ebad649b6489'}


def _source70_target_reader_oracle():
    from pathlib import Path
    from audio_backend import native_owned_cells, native_owned_seam_cells
    assert hashlib.sha256(_SOURCE70_TARGET_READER_ORACLE.encode()).hexdigest() == _SOURCE70_TARGET_READER_SHA256
    for module in (core, native_owned_cells, native_owned_seam_cells):
        assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == _SOURCE70_UNCHANGED_HELPER_PINS[module.__name__.rsplit('.',1)[1]]
    # Retain only imported primitives and immutable module data. Every local
    # automatic helper reachable by the old reader is defined by frozen text.
    namespace = {name:value for name,value in vars(automatic).items()
        if getattr(value,'__module__',None) != automatic.__name__}
    exec(compile(_SOURCE70_TARGET_READER_ORACLE,'<pinned-source70-target-reader>','exec'),namespace)
    assert '_resolve_target_body' not in namespace and '_bound_target_reference' not in namespace
    assert namespace['validate_v2_row'].__globals__ is namespace
    assert namespace['_read_v2'].__globals__ is namespace
    return namespace


# Prospective SQL reservations are mechanism tests against fresh synthetic rows.
# The raised pressure below is declared test input, not historical replay data.
@pytest.mark.parametrize('phase,over', [
    ('planning', False), ('planning', True),
    ('finalization', False), ('finalization', True),
    ('notice', False), ('notice', True),
    ('operation', False), ('operation', True)], ids=[
    'planning_equal', 'planning_over', 'finalization_equal', 'finalization_over',
    'notice_equal', 'notice_over', 'operation_equal', 'operation_over'])
def test_projection_reservation_boundary(monkeypatch, phase, over):
    from types import SimpleNamespace
    from audio_backend import native_owned_cell_store as cellstore
    monkeypatch.setattr(cellstore, 'time', SimpleNamespace(monotonic=lambda: 10.))
    diagnostics = []
    monkeypatch.setattr(cellstore, '_diagnostic', lambda *args: diagnostics.append(True))
    limits = {'planning': 128*1024, 'finalization': 252*1024,
              'notice': 256*1024, 'operation': 256*1024}
    budget = Budget.for_bounded(.75)
    limit = limits[phase]; budget.sql_bytes = limit - 17
    before = (budget.elapsed, budget.steps, budget.read_bytes, budget.sql_bytes)
    manager = budget.phase() if phase == 'operation' else getattr(budget, phase + '_phase')()
    with manager:
        if over:
            reason = 'operation_work_capacity' if phase == 'operation' else 'bounded_planning_capacity'
            with pytest.raises(Unavailable, match='^' + reason + '$'):
                budget.reserve_projection(18)
            assert (budget.elapsed, budget.steps, budget.read_bytes, budget.sql_bytes) == before
        else:
            budget.reserve_projection(17)
            assert budget.sql_bytes == limit
            assert (budget.elapsed, budget.steps, budget.read_bytes) == before[:3]
        budget.check()
        assert budget._refusal_diagnosed is False and diagnostics == []
    assert budget.sql_bytes == (limit - 17 if over else limit)


@pytest.mark.parametrize('exhaustion', ['elapsed', 'deadline', 'steps', 'sql', 'read'])
def test_projection_reservation_current_exhaustion_is_hard(monkeypatch, exhaustion):
    from types import SimpleNamespace
    from audio_backend import native_owned_cell_store as cellstore
    monkeypatch.setattr(cellstore, 'time', SimpleNamespace(monotonic=lambda: 10.))
    diagnostics = []
    monkeypatch.setattr(cellstore, '_diagnostic', lambda *args: diagnostics.append(True))
    budget = Budget(); budget.sql_bytes = 23
    if exhaustion == 'elapsed':budget.elapsed = budget.seconds + .01
    elif exhaustion == 'deadline':budget.deadline = 10.
    elif exhaustion == 'steps':budget.steps = cellstore.SQL_STEPS + 1
    elif exhaustion == 'sql':budget.sql_bytes = cellstore.SQL_PROJECTION_BYTES + 1
    else:budget.read_bytes = cellstore.SOURCE_READ_BYTES + 1
    before = (budget.elapsed, budget.steps, budget.read_bytes, budget.sql_bytes)
    reason = 'cooperative_deadline' if exhaustion in ('elapsed', 'deadline') else 'operation_work_capacity'
    with pytest.raises(Unavailable, match='^' + reason + '$'):
        budget.reserve_projection(1)
    assert (budget.elapsed, budget.steps, budget.read_bytes, budget.sql_bytes) == before
    assert budget._refusal_diagnosed is True and diagnostics == [True]


@pytest.mark.parametrize('count', [True, -1, 256*1024 + 1, .5],
                         ids=['bool', 'negative', 'oversized', 'fractional'])
def test_projection_reservation_invalid_count(count):
    budget = Budget(); budget.sql_bytes = 23
    before = (budget.elapsed, budget.steps, budget.read_bytes, budget.sql_bytes)
    with pytest.raises(Unavailable, match='^invalid_integer_geometry$'):
        budget.reserve_projection(count)
    assert (budget.elapsed, budget.steps, budget.read_bytes, budget.sql_bytes) == before
    assert budget._refusal_diagnosed is False


def test_projection_reservation_keeps_prior_debits():
    budget = Budget()
    with budget.planning_phase():
        budget.reserve_projection(128*1024 - 19)
        budget.reserve_projection(7)
        before = budget.sql_bytes
        with pytest.raises(Unavailable, match='^bounded_planning_capacity$'):
            budget.reserve_projection(13)
        assert budget.sql_bytes == before == 128*1024 - 12
        budget.reserve_projection(12)
        assert budget.sql_bytes == 128*1024
    assert budget.sql_bytes == 128*1024
    assert budget._refusal_diagnosed is False


def test_post_work_projection_overrun_remains_charged(monkeypatch):
    from audio_backend import native_owned_cell_store as cellstore
    diagnostics = []
    monkeypatch.setattr(cellstore, '_diagnostic', lambda *args: diagnostics.append(True))
    budget = Budget(); budget.sql_bytes = cellstore.SQL_PROJECTION_BYTES - 7
    with pytest.raises(Unavailable, match='^operation_work_capacity$'):
        budget.projection(8)
    assert budget.sql_bytes == cellstore.SQL_PROJECTION_BYTES + 1
    assert budget._refusal_diagnosed is True and diagnostics == [True]


@pytest.mark.parametrize('site', ['waiter', 'intent', 'parent', 'source_descriptor'])
def test_projection_reservation_precedes_payload_sql(support_local_ledger_factory, site):
    from audio_backend import native_owned_cell_capture as capture
    from test_bounded_core_support import primary_row
    f = support_local_ledger_factory(waiting=True)
    limit = 128*1024; events = []
    payload_prefix = 'SELECT ' + automatic.COLUMNS + ',proof,'
    parent_prefix = 'SELECT ' + ','.join(transcript.PRIMARY_FIELDS) + ',lexical_evidence FROM segments'
    def trace(statement):
        # Retain only fixed categories. Expanded SQL and payloads are never logged.
        if statement.startswith(payload_prefix):events.append('intent_body')
        elif statement.startswith('SELECT seq,length(binding)'):events.append('waiter_lengths')
        elif statement.startswith('SELECT length(binding),length(proof),length(target_body)'):
            events.append('intent_lengths')
        elif statement.startswith('SELECT s.id,'):events.append('parent_lengths')
        elif statement.startswith(parent_prefix):events.append('parent_body')
        elif statement.startswith('SELECT length(id),length(session_id),length(digest)'):
            events.append('descriptor_lengths')
        elif statement.startswith('SELECT id,session_id,digest,timeline_start,duration'):
            events.append('descriptor_body')
    with f.store.db() as db:
        if site == 'parent':
            _insert_local_parent(db, primary_row(2, 18, 20, cid='source-1', text='second synthetic parent'))
        def read(budget):
            if site == 'waiter':
                return automatic.read_waiters(db, f.context['batch_id'],
                    core.scope_key(f.case['commits'][0]['scope']), budget)
            if site == 'intent':return automatic._read_v2(db, key=f.key, budget=budget)
            return capture.bounded_primary_snapshot(db, f.row['sid'], 0., [0, 64], budget)
        # Establish valid real rows and source validation with an ordinary budget.
        baseline = Budget.for_bounded(1.0)
        valid = read(baseline)
        if site == 'waiter':assert len(valid) == 1 and valid[0]['candidate_key'] == f.key
        elif site == 'intent':assert valid['candidate_key'] == f.key
        else:
            assert [row['id'] for row in valid['rows']] == ([1, 2] if site == 'parent' else [1])
            assert set(valid['sources']) == set(f.case['sources'])
        if site in ('waiter', 'intent'):
            count = sum(len(f.row[name]) for name in ('binding', 'proof', 'target_body')) + 1024
            initial = limit - count + 1; accepted_inside = 0
        else:
            variable = ('text', 'raw_text', 'asr_backend', 'timestamp_kind', 'speaker_local_id', 'lexical_evidence')
            sizes = [256 + sum(len(row[name].encode('utf-8')) if row[name] is not None else 0
                              for name in variable)
                     for row in db.execute('SELECT * FROM segments ORDER BY start,id')]
            chunks_charge = len(f.case['sources']) * 256
            if site == 'parent':
                assert len(sizes) == 2
                count = sizes[1]; accepted_inside = chunks_charge + sizes[0]
            else:
                assert len(sizes) == 1
                count = 1024; accepted_inside = chunks_charge + sizes[0]
            initial = limit - accepted_inside - count + 1
        budget = Budget.for_bounded(1.0)
        with budget.planning_phase():
            budget.reserve_projection(initial)
            db.set_trace_callback(trace)
            try:
                with pytest.raises(Unavailable, match='^bounded_planning_capacity$'):
                    read(budget)
            finally:db.set_trace_callback(None)
            assert budget.sql_bytes == initial + accepted_inside
            assert budget.sql_bytes + count == limit + 1
            assert budget._refusal_diagnosed is False
            budget.check()
        if site in ('waiter', 'intent'):
            assert events == [('waiter_lengths' if site == 'waiter' else 'intent_lengths')]
        elif site == 'parent':
            assert 'parent_lengths' in events
            assert 'parent_body' not in events and 'descriptor_body' not in events
        else:
            assert 'parent_lengths' in events and events.count('parent_body') == 1
            assert 'descriptor_lengths' not in events and 'descriptor_body' not in events
        # Transcript reads use the real legacy projection/check protocol and
        # retain its independent 4 MiB accounting, including charged overruns.
        from audio_backend.transcript_store import _CertificateReadBudget
        checks = []; legacy = _CertificateReadBudget(lambda: checks.append(True))
        read(legacy)
        assert legacy.sql_bytes == baseline.sql_bytes and checks
        prior_events = list(events); events.clear()
        legacy = _CertificateReadBudget()
        legacy.sql_bytes = transcript.MAX_BINDING_BYTES - accepted_inside - count + 1
        db.set_trace_callback(trace)
        try:
            with pytest.raises(ReadError, match='^unresolved_capacity$'):
                read(legacy)
        finally:db.set_trace_callback(None)
        assert legacy.sql_bytes == transcript.MAX_BINDING_BYTES + 1
        assert events == prior_events


def test_prospective_capacity_rolls_back_optional_and_commits_primary(bounded_app_factory, monkeypatch):
    """Synthetic higher consumed-byte pressure, never a replay of hidden values."""
    with bounded_app_factory(support_seconds=1.0) as h:
        chunks, waiting = advance_to_waiter(h)
        tables = (automatic.TABLE, automatic.DEPENDENCIES, automatic.USAGE)
        def optional_rows(db):
            return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                    for table in tables}
        with h.store.db() as db:
            before = optional_rows(db); usage_before = _reference_accounting(db, h.sid)
            compacts_before = db.execute('SELECT COUNT(*) FROM native_core_commits WHERE sid=?', (h.sid,)).fetchone()[0]
            ordinal_before = db.execute('SELECT native_core_ordinal FROM recording_batches WHERE session_id=?', (h.sid,)).fetchone()[0]
        publications_before = list(h.publications)
        enqueue = automatic.enqueue_bounded; dependencies = automatic._write_dependencies
        read_v2 = automatic._read_v2; reserve = Budget.reserve_projection
        state = {'active': False, 'mutated': False, 'in_full_read': False, 'injected': False,
                 'refused': False, 'body_reads': 0, 'size_reads': 0, 'cleanup': []}
        payload_prefix = 'SELECT ' + automatic.COLUMNS + ',proof,'
        class TracedDB:
            def __init__(self, db):self.db = db
            @property
            def in_transaction(self):return self.db.in_transaction
            def set_progress_handler(self, callback, count):return self.db.set_progress_handler(callback, count)
            def execute(self, sql, parameters=()):
                if sql.startswith(payload_prefix):state['body_reads'] += 1
                elif sql.startswith('SELECT length(binding),length(proof),length(target_body)'):
                    state['size_reads'] += 1
                if sql in ('ROLLBACK TO bounded_optional', 'RELEASE bounded_optional'):
                    state['cleanup'].append(sql)
                return self.db.execute(sql, parameters)
        def mutated_dependencies(db, seq, roots):
            result = dependencies(db, seq, roots)
            if state['active'] and not state['injected']:
                current = optional_rows(db)
                # All three real optional ledgers have changed before pressure.
                if all(current[table] != before[table] for table in tables):
                    _reference_accounting(db, h.sid)
                    state['mutated'] = True
            return result
        def full_read(*args, **kwargs):
            armed = (state['active'] and kwargs.get('budget') is state.get('budget')
                     and kwargs.get('expected') is None)
            old = state['in_full_read']; state['in_full_read'] = armed
            bodies, sizes, injected = state['body_reads'], state['size_reads'], state['injected']
            try:return read_v2(*args, **kwargs)
            finally:
                state['in_full_read'] = old
                if not injected and state['injected']:
                    assert state['size_reads'] == sizes + 1
                    assert state['body_reads'] == bodies
        def pressured_reservation(budget, count):
            if (state['active'] and state['mutated'] and state['in_full_read']
                    and not state['injected'] and budget is state['budget']):
                assert budget._planning_limits == (.98, 148000, 252*1024)
                assert budget.primary_sql and state['db'].in_transaction
                # Add declared synthetic consumed pressure at this real screened
                # full-row call site. Do not change caps, elapsed time or steps.
                pressure = 252*1024 - 1
                assert count > 4097 and pressure + count > 256*1024
                assert budget.sql_bytes < pressure <= 252*1024
                state.update(injected=True, earlier_bytes=budget.sql_bytes, pressure=pressure,
                             requested=count, diagnostic_before=budget._refusal_diagnosed)
                budget.sql_bytes = pressure
                try:return reserve(budget, count)
                except Unavailable as exc:
                    assert str(exc) == 'bounded_planning_capacity'
                    assert budget.sql_bytes == pressure
                    assert budget._refusal_diagnosed is state['diagnostic_before']
                    state['refused'] = True
                    raise
            return reserve(budget, count)
        def observed_enqueue(db, cells, packet, handoff, chunk, lexical_rows, lexical_status):
            assert chunk['id'] == chunks[-1]['id'] and not state['active']
            assert all(optional_rows(db)[table] == before[table] for table in tables)
            state.update(active=True, budget=handoff['budget'], db=db)
            try:
                notice = enqueue(TracedDB(db), cells, packet, handoff, chunk, lexical_rows, lexical_status)
                assert state['injected'] and state['refused'] and state['mutated']
                assert db.in_transaction
                assert all(optional_rows(db)[table] == before[table] for table in tables)
                assert _reference_accounting(db, h.sid) == usage_before
                assert handoff['budget'].sql_bytes == state['pressure']
                state['notice'] = notice
                return notice
            finally:state['active'] = False
        monkeypatch.setattr(automatic, '_write_dependencies', mutated_dependencies)
        monkeypatch.setattr(automatic, '_read_v2', full_read)
        monkeypatch.setattr(Budget, 'reserve_projection', pressured_reservation)
        monkeypatch.setattr(automatic, 'enqueue_bounded', observed_enqueue)
        assert process_primary(h) == chunks[-1]['id']
        assert state['cleanup'] == ['ROLLBACK TO bounded_optional', 'RELEASE bounded_optional']
        assert state['notice'] == {'schema': 'bounded-core-support-notice-v1', 'status': 'waiting',
            'reason': 'bounded_planning_capacity', 'owned_targets_preserved': True}
        budget = state['budget']
        assert budget.sql_outcome == 'committed' and budget.sql_bytes == state['pressure']
        assert budget.seconds == 1.0 and budget.sql_bytes == 252*1024 - 1
        assert budget.sql_bytes + state['requested'] > 256*1024
        last = h.store.chunk(chunks[-1]['id'])
        assert last['status'] == 'completed' and last['next_offset'] == last['duration']
        assert last['metadata']['bounded_core_support'] == state['notice']
        assert last['metadata']['lexical_evidence']['status'] == 'retained'
        with h.store.db() as db:
            assert all(optional_rows(db)[table] == before[table] for table in tables)
            assert _reference_accounting(db, h.sid) == usage_before
            saved = [dict(row) for row in db.execute('SELECT * FROM segments WHERE chunk_id=? ORDER BY id', (last['id'],))]
            assert saved and all(row['lexical_evidence'] is not None for row in saved)
            assert all(lexical.current_inventory(db, h.sid, row) is not None for row in saved)
            assert ''.join(row['text'] for row in saved).strip() == 'suffix'
            assert db.execute('SELECT COUNT(*) FROM native_core_commits WHERE sid=?', (h.sid,)).fetchone()[0] == compacts_before + 1
            assert db.execute('SELECT native_core_ordinal FROM recording_batches WHERE session_id=?', (h.sid,)).fetchone()[0] == ordinal_before + 1
            assert db.execute(f'SELECT COUNT(*) FROM {automatic.TABLE} WHERE sid=? AND job_id IS NOT NULL', (h.sid,)).fetchone()[0] == 0
        assert c_target(h)['candidate_key'] == waiting['candidate_key']
        assert c_target(h)['plan_phase'] == 'waiting'
        assert h.publications == publications_before
        assert not h.drivers and h.jobs._live is None and h.jobs.permit._optional is None
        assert not h.owner._pins and h.capture.analyzer is None and not h.capture.active


@pytest.mark.parametrize('failure', ['rollback', 'release', 'interrupted', 'consumed_sql', 'expired_time'])
def test_optional_reservation_uncertainty_stays_hard(support_local_ledger_factory, monkeypatch, failure):
    from test_bounded_core_support import primary_row
    f = support_local_ledger_factory(waiting=True)
    before = _reference_snapshot(f); budget = Budget.for_bounded(1.0)
    tables = (automatic.TABLE, automatic.DEPENDENCIES, automatic.USAGE)
    evidence = {'mutated': False, 'cleanup': [], 'restored': False, 'handlers': []}
    exact_compacts = automatic._exact_compacts
    def optional_rows(db):
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in tables}
    class FaultDB:
        def __init__(self, db):self.db = db
        @property
        def in_transaction(self):return self.db.in_transaction
        def set_progress_handler(self, callback, count):
            evidence['handlers'].append((callback, count))
            return self.db.set_progress_handler(callback, count)
        def execute(self, sql, parameters=()):
            if sql in ('ROLLBACK TO bounded_optional', 'RELEASE bounded_optional'):
                evidence['cleanup'].append(sql)
                if failure == 'rollback' and sql.startswith('ROLLBACK TO'):
                    raise sqlite3.OperationalError('synthetic rollback failure')
                if failure == 'release' and sql.startswith('RELEASE'):
                    raise sqlite3.OperationalError('synthetic release failure')
            cursor = self.db.execute(sql, parameters)
            if sql == 'ROLLBACK TO bounded_optional':
                current = optional_rows(self.db)
                assert all(current[table] == before[table] for table in tables)
                assert self.db.execute('SELECT text FROM segments WHERE id=2').fetchone()[0] == 'primary pending'
                assert self.db.in_transaction
                evidence['restored'] = True
            return cursor
    def mutate_then_refuse(db, commits, context, current_budget):
        exact_compacts(db, commits, context, current_budget)
        assert not evidence['mutated'] and current_budget is budget
        # Real writes within the optional savepoint, all checked after cleanup.
        db.execute("UPDATE segments SET text='optional pending' WHERE id=2")
        db.execute(f"UPDATE {automatic.TABLE} SET reason='synthetic optional pending' WHERE seq=?", (f.row['seq'],))
        automatic._charge(db, f.row['sid'], (0, 1, 0, 0))
        db.execute(f'DELETE FROM {automatic.DEPENDENCIES} WHERE intent_seq=?', (f.row['seq'],))
        current = optional_rows(db)
        assert all(current[table] != before[table] for table in tables)
        evidence['mutated'] = True
        if failure == 'interrupted':
            db.set_progress_handler(lambda: 1, 1)
            db.execute('WITH RECURSIVE work(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM work WHERE n<1000) SELECT sum(n) FROM work').fetchone()
            pytest.fail('actual SQLite progress interruption did not propagate')
        if failure == 'consumed_sql':budget.sql_bytes = 256*1024 + 1
        elif failure == 'expired_time':budget.elapsed = budget.seconds + .01
        else:budget.sql_bytes = 252*1024
        budget.reserve_projection(1)
        pytest.fail('exhausted optional reservation returned')
    monkeypatch.setattr(automatic, '_exact_compacts', mutate_then_refuse)
    error_type = sqlite3.OperationalError if failure in ('rollback', 'release', 'interrupted') else Unavailable
    with pytest.raises(error_type) as caught:
        with f.store.db(budget=budget) as db:
            db.execute('BEGIN IMMEDIATE')
            _insert_local_parent(db, primary_row(2, 48, 56, cid='source-3', text='primary pending'))
            handoff = {'waiters': [f.row], 'batch': f.context, 'reserved': True, 'budget': budget,
                       'origins': [f.original], 'plans': []}
            automatic.enqueue_bounded(FaultDB(db), [], {'commit': f.case['commits'][-1]}, handoff,
                {'session_id': f.row['sid']}, [], None)
            pytest.fail('hard optional uncertainty returned a notice')
    assert evidence['mutated'] and budget.sql_outcome == 'rolled_back'
    after = _reference_snapshot(f)
    assert all(after[table] == before[table] for table in before)
    assert evidence['handlers'][-1] == (budget.progress, 100)
    if failure == 'rollback':
        assert str(caught.value) == 'synthetic rollback failure'
        assert evidence['cleanup'] == ['ROLLBACK TO bounded_optional'] and not evidence['restored']
    elif failure == 'interrupted':
        assert caught.value.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT
        assert evidence['cleanup'] == [] and not evidence['restored']
    else:
        assert evidence['cleanup'] == ['ROLLBACK TO bounded_optional', 'RELEASE bounded_optional']
        assert evidence['restored']
        if failure == 'release':assert str(caught.value) == 'synthetic release failure'
        else:
            assert str(caught.value) == 'bounded_planning_capacity'
            assert budget._refusal_diagnosed is True
            if failure == 'consumed_sql':assert budget.sql_bytes == 256*1024 + 1
            else:assert budget.elapsed > budget.seconds
