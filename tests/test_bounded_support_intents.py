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


def c_target(h):
    found = [row for row in intent_rows(h) if row['target_body'] is not None
        and json.loads(row['target_body'])['identity']['components'] == C_COMPONENTS]
    assert len(found) == 1, [(row['reason'], row['plan_phase']) for row in intent_rows(h)]
    return found[0]


@pytest.fixture
def bounded_app_factory(automatic_app_factory, monkeypatch, capsys):
    """Extend the existing real lifespan fixture, changing only fake producers."""
    original_post_init = Settings.__post_init__
    state = {'windows': {}, 'last_group_end': 31.6}

    def post_init(settings):
        if settings.moss_automatic_enabled:
            settings.native_owned_bounded_core_support_enabled = True
            settings.lexical_evidence_enabled = True
            settings.window_seconds = 10.5
            settings.context_seconds = 5.
        original_post_init(settings)

    def extract(worker, chunk, start, end, wav, tmp, stop, *, capture_support=False,
                capture_lexical=False):
        assert not stop() and not capture_support
        a, b = frame(start), frame(end)
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
        return {'_lexical_sources': snapshots} if snapshots is not None else None

    def analyze(analyzer, path, **kwargs):
        native = kwargs['diarization']; a, b = state['windows'][str(path)]
        # These fixed primary units straddle two genuine core boundaries.
        # Prefix/suffix let conservation checks cover kept as well as replaced
        # characters. Estimated endpoints make no acoustic-boundary claim.
        definitions = [(18.8, 19.1, ' prefix'), (19.4, 21.7, ' alpha'),
            (22.5, 24.6, ' beta'), (30.2, state['last_group_end'], ' gamma'),
            (40., 40.4, ' suffix')]
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
        return {'segments': segments, 'events': [], 'speaker_turns': [],
            'metadata': {'asr_backend': 'sensevoice', 'timestamp_semantics': lexical.PRIMARY_TIMESTAMP,
                'diarization_turns': deepcopy(native['turns'])}}

    monkeypatch.setattr(Settings, '__post_init__', post_init)
    monkeypatch.setattr(Worker, 'extract_pcm', extract)
    monkeypatch.setattr(FakeAnalyzer, 'analyze', analyze)

    @contextmanager
    def open_app(*, sid=None, enabled=True, last_group_end=31.6):
        state['last_group_end'] = last_group_end
        with automatic_app_factory(sid=sid, automatic=enabled) as h:
            h.planning = []; h.committed_targets = []; h.timings = []; h.publications = []
            if enabled:
                assert h.owner.bounded_core_enabled
                assert h.owner.retention_store_key is not None
                assert h.owner.directory.name == 'native-owned-cells-bounded-v1'

                class Native:
                    """One real observer callback per synthetic RPC response."""
                    def __init__(self):
                        self.frames = 0;self.epoch = None;self.closed = False
                        self.calls = [];self.queries = []
                    def request(self, action, stop, **arguments):
                        assert not stop();self.calls.append(action)
                        if action == 'reset':
                            self.epoch = arguments['epoch_id'];self.frames = 0
                            response = {'state': synthetic_state(self.epoch, 0),
                                        'provenance': synthetic_provenance()}
                        elif action == 'push':
                            with wave.open(arguments['wav'], 'rb') as source:
                                self.frames += source.getnframes()
                            response = synthetic_state(self.epoch, self.frames, stable_frames=0)
                        elif action == 'finish':
                            response = synthetic_state(self.epoch, self.frames, finished=True)
                        else:
                            assert action == 'turns'
                            qa, qb = frame(arguments['start']), frame(arguments['end'])
                            assert 0 <= qa < qb <= self.frames and qb - qa <= 60 * RATE
                            self.queries.append([qa, qb]); turns = []
                            for voice, intervals in ((1, [[qa, qb]]), (2, C_COMPONENTS)):
                                for lo, hi in intervals:
                                    lo, hi = max(qa, lo), min(qb, hi)
                                    if lo < hi:turns.append({'start': lo / RATE, 'end': hi / RATE,
                                        'speaker_local_id': f'{self.epoch}:speaker_{voice}'})
                            response = {'turns': turns}
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
                        expected = kwargs.get('expected')
                        assert type(expected) is tuple and len(expected) == 2
                        assert sum(map(len, expected)) <= 65536
                        current['certificate_reads'] += 1
                        current['comparison_parameter_count'] += len(expected)
                        current['comparison_input_bytes'] += sum(map(len, expected))
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
                        record.update(commit_window_seconds=ended-began,
                            begin_to_closed_seconds=0. if current['begin'] is None else ended-current['begin'],
                            total_budget_seconds=budget.elapsed,
                            committed=int(getattr(budget, 'sql_outcome', None)=='committed'),
                            rolled_back=int(getattr(budget, 'sql_outcome', None)=='rolled_back'))
                        h.timings.append(record)
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


def test_c_full_target_wait_bind_publish_result_transcript_and_disabled_restart(bounded_app_factory, capsys):
    with bounded_app_factory() as h:
        chunks, waiting = advance_to_waiter(h)
        original_target = waiting['target_body']; owner_key = waiting['candidate_key']
        assert process_primary(h) == chunks[-1]['id']
        assert h.timings[-1]['binding_calls'] > 0
        assert h.timings[-1]['bounded_validation_calls'] == h.timings[-1]['binding_calls']
        bound = c_target(h); doc = json.loads(bound['proof'])
        assert bound['candidate_key'] == owner_key and bound['target_body'] == original_target
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
        drain_automatic(h)
        completed = c_target(h)
        assert completed['state'] == 'committed' and completed['job_id'] is not None
        counts = [record for cell_id, record in h.publications if cell_id == completed['cell_id']]
        assert len(counts) == 1 and counts[0]['publish_returned_durable'] == 1
        assert counts[0]['fresh_boundaries'] == counts[0]['certificate_reads'] == 3
        assert counts[0]['comparison_parameter_count'] == 6
        assert counts[0]['comparison_input_bytes'] == 3*(len(bound['proof'])+len(bound['target_body']))
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
    with bounded_app_factory() as h:
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
    """Tiny real schemas and exact commits, without an owner thread or files."""
    from audio_backend import storage, transcript_revision
    from audio_backend import native_owned_cells as cells
    from test_bounded_core_support import SID
    used = []
    def create(schema=None):
        directory = tmp_path / ('local-ledger-' + str(len(used)))
        directory.mkdir()
        store = storage.Store(directory / 'primary.sqlite3', Settings(data_dir=directory, start_worker=False))
        with store.db() as db:
            core.init_schema(db)
            db.execute('INSERT INTO sessions(id,name,identity_group,created,timeline_end) VALUES(?,?,?,1.,1.)',
                (SID, 'synthetic support-local', SID))
            db.execute('BEGIN IMMEDIATE') if not db.in_transaction else None
            automatic.init_bounded_schema(db, enable=True); transcript_revision.init_bounded_schema(db)
            value = _support_local_seed(db, schema)
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


def _support_local_seed(db, schema):
    from types import SimpleNamespace
    from audio_backend import native_owned_cells as cells
    from test_bounded_core_support import SID, synthetic_case, final_cell, seam_origin
    batch = 'local-synthetic-batch'; store_key = '5' * 64
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
    db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,created,
        capture_origin_frame,native_core_ordinal,retention_store_key) VALUES(?,?,?,1.,0,4,?)''',
        (batch, SID, 'synthetic-capture', store_key))
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
        db.execute('''INSERT INTO native_core_commits(slot,sid,cid,scope_key,logical_end,body,
            pending,state,reason,recording_batch_id,commit_ordinal,retention_store_key)
            VALUES(?,?,?,?,?,?,NULL,'none','synthetic',?,?,?)''',
            (core.slot(commit), SID, source['id'], core.scope_key(commit['scope']),
             part['logical_end'], cells.canonical(commit, core.COMPACT_BYTES), batch, index + 1, store_key))
    _insert_local_parent(db, parent)
    origin = case['target']['origins'][0]
    proof, binding = origin['proof'].encode('ascii'), origin['binding'].encode('ascii')
    original_doc, support, _ = cells.validate_document(proof)
    proposal = {'key': hashlib.sha256(binding).hexdigest(), 'sid': SID, 'kind': 'core_seam',
        'binding': binding, 'proof': proof, 'proof_sha256': hashlib.sha256(proof).hexdigest(),
        'cell_id': cells.sha({'policy': original_doc['policy'], 'support': support.to_dict()}),
        'slot1': core.slot(case['commits'][0]), 'slot2': core.slot(case['commits'][1]),
        'cid1': 'source-0', 'cid2': 'source-1', 'target': case['target']}
    context = {'batch_id': batch, 'ordinal': 2, 'retention_store_key': store_key}; now = time.time()
    with Budget().phase() as budget:
        waiting, status = automatic._register_target(db, proposal, context,
            cells.sha(case['commits'][0]['scope']), now, budget)
        assert status == 'registered'
        context = {**context, 'ordinal': 4}
        final = {'target': case['target'], 'commits': doc['commits'], 'query': doc['query'],
            'evidence': doc['right_evidence'], 'support': doc['geometry'], 'pcm': cell.pcm}
        ident = automatic._bind_target(db, waiting, final, context, snapshot, budget, now,
                                      support_local=schema is None)
        row = automatic._read_v2(db, key=proposal['key'], budget=budget)
        assert ident == row['cell_id']
        if schema is not None:assert row['proof'] == cell.proof
        doc = json.loads(row['proof'])
        assert automatic.qualify_bounded_document(db, doc, budget, store_key)
    assert len(doc['commits']) == 4 and doc['geometry'] == {'start_frame':16,'end_frame':24}
    if schema is None:
        assert doc['inventories']['scan_frames'] == [16,24]
        assert {s['id'] for s in doc['inventories']['sources']} == {'source-1','source-2'}
    assert {root['cid'] for root in doc['dependency_roots']} == set(case['sources'])
    return SimpleNamespace(case=case,doc=doc,row=row,key=proposal['key'],parent=parent,
        context=context,original=proposal,store_key=store_key)


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
