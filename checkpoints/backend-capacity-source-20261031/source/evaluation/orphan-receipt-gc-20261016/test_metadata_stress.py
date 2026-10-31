"""Accelerated, metadata-only quiet-stream retention stress; no PCM qualification.

Synthetic primary rows, absent PCM, and an explicit synthetic receipt observer
stand in for source-file ingestion and acoustic work. That observer substitutes
both receipt-file checking and receipt_valid primary-row revalidation. Actual production receipt
SQL admission, compact savepoint admission, scheduler predicates, owner budgets,
collectors, optional reopen, and bounded startup sweeps are exercised unchanged.

Eight compact/receipt scheduling opportunities follow each released page. Only scheduler due times
are translated from a simulated idle clock to the real monotonic clock. Owner
operation deadlines and measured work remain real. Every pair pays the actual
completion-based one-second floor, plus measured work; no real sleeps are used.
The modeled idle allowance is a lower bound excluding owner-loop wake/handoff
latency. It describes conditional idle opportunity, not ASR throughput.
Busy foreground activity can postpone every one of these opportunities.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import resource
import shutil
import time

import pytest

from audio_backend import native_owned_cell_store as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import moss_result_store as history
from audio_backend.native_owned_cells import RATE, SourceSpan, canonical, sha
from test_native_owned_receipt_gc import FreshReceipts, Q, PRIMARY_TABLES
from test_native_owned_compact_gc import rows_hash, file_hash
from test_native_owned_support import synthetic_provenance

ARTIFACT_LIMIT = 240 * 1024**2  # Parent reserves16MiB for source/control/reports.
PAIRS = 8
PAGE = 28
EPOCH = 300 * RATE
REPLAY = 339200
IMMUTABLE = tuple(t for t in PRIMARY_TABLES if t != 'native_core_commits')
CASES = [('pilot', 2940, 280, 288), ('12h', 43200, 4115, 4238),
         ('48h', 172800, 16458, 16951)]


def cid_at(position):
    return hashlib.sha256(f'metadata-source-{position}'.encode()).hexdigest()[:32]


def artifacts(root):
    total = 0
    for p in root.rglob('*'):
        if p.is_file():
            info = p.stat()
            total += max(info.st_size, info.st_blocks * 512)
    assert total <= ARTIFACT_LIMIT, (total, ARTIFACT_LIMIT)
    return total


class MetadataStream(FreshReceipts):
    """No source bytes exist; only receipt-file authority is synthetic."""
    def __init__(self, root, name):
        super().__init__(root)
        self.name = name
        self.bid = 'metadata-batch-' + self.sid
        self.stream = 'recording.' + self.bid
        self.frames = 0
        self.positions = 0
        self.compact_admissions = 0
        self.pages = 0
        self.virtual_now = 0.
        self.virtual_due = 0.
        self.expected = {}
        self.latest_commit = None
        self.stats = {'fixture': 'SYNTHETIC metadata only; source PCM is absent',
            'cadence': 'eight paired scheduling opportunities per page; simulated idle time',
            'case': name, 'retired_receipts': 0, 'retired_compacts': 0,
            'max_receipts': 0, 'max_compacts': 0, 'max_cells': 0,
            'maintenance_calls': 0, 'retention_refresh_calls': 0, 'scheduler_refusal_checks': 0,
            'gc_opportunities': 0, 'compact_occupancy_refusals': 0,
            'maintenance_seconds': 0., 'admission_seconds': 0.,
            'startup_seconds': 0., 'startup_sweeps': 0,
            'modeled_idle_lower_bound_seconds': 0., 'max_page_idle_lower_bound_seconds': 0.,
            'max_owner_elapsed': 0., 'max_sql_steps': 0,
            'max_sql_projection_bytes': 0, 'max_scanned': 0,
            'max_retired_per_pass': 0, 'max_prefix_rows': 0,
            'max_prefix_keys': 0, 'max_body_bytes': 0,
            'max_cursor_bytes': 0, 'history_checkpoints': [],
            'trend': [], 'restart': None, 'finished': False}
        # Deliberately NOT an acoustic/file qualification. Exact generated
        # receipt identity replaces receipt_valid file and primary-row revalidation.
        # Collector primary-row checks still run unchanged; no file is opened.
        self.authority.receipt_valid = self.synthetic_receipt_valid
        with self.store.db() as db:
            db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,state,
                capture_origin_frame,capture_end_frame,created,mode,eos)
                VALUES(?,?,?,'open',0,0,0,'sealed_pages',0)''',
                (self.bid, self.sid, 'SYNTHETIC metadata stress'))
        self.sweep_startup()

    def synthetic_receipt_valid(self, receipt, budget):
        budget.check()
        return self.expected.get(receipt.chunk_id) == receipt

    def observe_budget(self, budget):
        assert budget.seconds == 2.
        assert budget.elapsed <= budget.seconds
        assert budget.steps <= cells.SQL_STEPS
        assert budget.sql_bytes <= cells.SQL_PROJECTION_BYTES
        s = self.stats
        s['max_owner_elapsed'] = max(s['max_owner_elapsed'], budget.elapsed)
        s['max_sql_steps'] = max(s['max_sql_steps'], budget.steps)
        s['max_sql_projection_bytes'] = max(s['max_sql_projection_bytes'], budget.sql_bytes)

    def sweep_startup(self):
        before = self.source_rows()
        began = time.monotonic()
        # Existing cleanup pages contain <=20 sources. This is a finite bound
        # from the unchanged4096 cap, not a loop forcing GC until it succeeds.
        for _ in range((cells.MAX_RECEIPTS + cells.PAGE_ROWS - 1) // cells.PAGE_ROWS + 2):
            if not self.owner._reconcile.is_set():break
            assert not self.owner._schedule_receipt_gc()
            assert not self.owner._schedule_compact_gc()
            self.stats['scheduler_refusal_checks'] += 2
            budget = cells.Budget.for_owner()
            with budget.phase():
                self.optional.set_progress_handler(budget.progress, 100)
                try:
                    self.owner._scan(budget)
                    self.owner._sweep(budget)
                    self.owner._scan(budget)
                finally:self.optional.set_progress_handler(None, 0)
            self.observe_budget(budget)
            self.stats['startup_sweeps'] += 1
        assert not self.owner._reconcile.is_set()
        assert self.source_rows() == before
        elapsed = time.monotonic() - began
        self.stats['startup_seconds'] += elapsed
        self.virtual_now += elapsed

    def admit_receipt(self, receipt):
        assert self.owner._slot is None
        self.expected[receipt.chunk_id] = receipt
        op = cells.Operation('receipt', receipt, self.owner._epoch)
        budget = cells.Budget.for_owner(op)
        self.owner._slot = op
        began = time.monotonic()
        try:
            with budget.phase():
                self.optional.set_progress_handler(budget.progress, 100)
                self.owner._scan(budget)
                result = self.owner._receipt(op, budget)
                self.owner._scan(budget)
            assert result == {'status': 'receipt_retained', 'receipt_id': receipt.receipt_id}
            assert self.owner.receipt(receipt.chunk_id) == receipt
            self.observe_budget(budget)
        finally:
            self.optional.set_progress_handler(None, 0)
            self.owner._slot = None; op.done.set(); op.slot_released.set()
        self.stats['admission_seconds'] += time.monotonic() - began

    def admit_compacts(self, db, chunk, receipt, logical_start):
        cursor = logical_start
        while cursor < logical_start + receipt.frames:
            index = cursor // EPOCH
            finish = min(logical_start + receipt.frames, (index + 1) * EPOCH)
            a, b = cursor - logical_start, finish - logical_start
            native = {'synthetic': True, 'epoch_id': f'{index + 1:032x}', 'epoch_index': index}
            cut = {'nominal': (b - a) / RATE, 'actual': (b - a) / RATE, 'reason': 'fixed_or_final'}
            evidence = {'chunk_id': receipt.chunk_id, 'start': a, 'end': b,
                        'native': native, 'core_cut': cut}
            part = SourceSpan(self.sid, receipt.chunk_id, receipt.receipt_id,
                receipt.digest, receipt.frames, a, b, cursor, finish)
            commit = {'scope': {'session_id': self.sid, 'kind': 'stream', 'key': self.stream,
                'origin': (0.).hex(), 'run': self.capture.core_seams.run, 'reset': index + 1,
                'epoch': f'{index + 1:032x}', 'index': index}, 'part': asdict(part),
                'native_sha256': sha(native), 'cut_sha256': sha(cut),
                'evidence_sha256': sha(evidence), 'runtime_sha256': sha(synthetic_provenance())}
            packet = {'commit': commit, 'proof': None, 'reason': 'core_first_or_unavailable_left'}
            result = {'_native_core_seam': packet,
                      'metadata': {'native_diarization': native, 'core_cut': cut}}
            started = time.monotonic()
            reason = core.transaction(db, chunk, a / RATE, b / RATE, result)
            self.stats['admission_seconds'] += time.monotonic() - started
            assert reason == 'core_first_or_unavailable_left', reason
            assert db.execute('SELECT body FROM native_core_commits WHERE slot=?',
                              (core.slot(commit),)).fetchone()[0] == canonical(commit, core.COMPACT_BYTES)
            self.latest_commit = commit
            self.compact_admissions += 1
            cursor = finish

    def append_page(self, count, total_frames):
        number = self.pages + 1
        page_id = self.bid + '-page-' + str(number)
        start = self.frames
        end = min(total_frames, start + count * Q)
        final = end == total_frames
        with self.store.db() as db:
            # Row generation is synthetic. RecordingOverlap upload/release and
            # audio decoding are deliberately not being throughput-qualified.
            db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,state,released,
                start_frame,end_frame,eos,created) VALUES(?,?,?,'published',1,?,?,?,0)''',
                (page_id, self.bid, number, start, end, int(final)))
            db.execute('UPDATE recording_batches SET capture_end_frame=?,eos=?,state=? WHERE id=?',
                       (end, int(final), 'queued' if final else 'open', self.bid))
            for _ in range(count):
                position = self.positions
                frames = min(Q, total_frames - self.frames)
                assert frames > 0 and (frames == Q or final)
                cid = cid_at(position)
                digest = hashlib.sha256(('SYNTHETIC absent PCM ' + cid).encode()).hexdigest()
                size = 44 + 2 * frames
                sig = (1, position + 1, size, 1, 1)
                path = self.root / 'uploads' / (cid + '.absent')
                assert not path.exists()
                receipt = cells.Receipt(self.sid, cid, digest, frames, 44, size, sig,
                                        self.frames / RATE, self.stream)
                db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
                    timeline_start,status,next_offset,created,metadata) VALUES(?,?,?,?,?,?,?,'completed',?,?,?)''',
                    (cid, self.sid, cid, digest, str(path), frames / RATE, self.frames / RATE,
                     frames / RATE, position, '{"fixture":"SYNTHETIC metadata; PCM absent"}'))
                db.execute('''INSERT INTO stream_chunks(chunk_id,session_id,stream_id,position,
                    stream_final,upload_stream_final) VALUES(?,?,?,?,?,?)''',
                    (cid, self.sid, self.stream, position, int(final and self.frames + frames == end),
                     int(final and self.frames + frames == end)))
                db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,
                    capture_start_frame,native_receipt_status,native_receipt,receipt_id,receipt_state,
                    page_id,file_signature) VALUES(?,?,?,?,?,?,'receipt_retained',?,?,'durable',?,?)''',
                    (cid, self.bid, position, frames, size, self.frames, receipt.encoded,
                     receipt.receipt_id, page_id, canonical(list(sig)).decode()))
                source_id = 'source-' + cid
                db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,
                    frames,bytes,source_start_frame,format,created,page_id) VALUES(?,?,?,?,?,?,?,?,?,0,?)''',
                    (source_id, self.bid, source_id, digest, str(path) + '.original', frames,
                     size, self.frames, 'wav-pcm16le', page_id))
                db.execute('INSERT INTO recording_ranges VALUES(?,?,?,?,?,?,?,?)',
                           (self.bid, source_id, cid, 0, self.frames, 0, frames, 'owner'))
                db.execute('INSERT INTO segments(chunk_id,start,end,text,speaker_status) VALUES(?,0,?,?,?)',
                           (cid, frames / RATE, 'SYNTHETIC ' + cid, 'unknown'))
                db.execute('INSERT INTO speaker_turns(chunk_id,start,end,speaker_status,overlap) VALUES(?,0,?,?,0)',
                           (cid, frames / RATE, 'unknown'))
                db.execute('INSERT INTO events(chunk_id,start,end,label,score) VALUES(?,0,?,?,.5)',
                           (cid, frames / RATE, 'SYNTHETIC'))
                self.admit_receipt(receipt)
                self.admit_compacts(db, {'id': cid, 'session_id': self.sid}, receipt, self.frames)
                self.frames += frames
                self.positions += 1
        self.pages = number
        # Admission and subsequent maintenance are serialized, like this quiet
        # workload's owner. No synthetic source receipt bypasses _receipt SQL.
        self.observe_occupancy('before')
        self.virtual_now = max(self.virtual_now, self.frames / RATE)

    def observe_occupancy(self, phase):
        sources = self.optional.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
        retained_cells = self.optional.execute('SELECT COUNT(*) FROM cells').fetchone()[0]
        with self.store.db() as db:
            compacts = db.execute('SELECT COUNT(*) FROM native_core_commits').fetchone()[0]
        assert sources <= cells.MAX_RECEIPTS == 4096
        assert compacts <= core.MAX_COMPACTS == 2056
        assert retained_cells <= cells.MAX_CELLS == 1024
        for name, value in [('receipts', sources), ('compacts', compacts), ('cells', retained_cells)]:
            self.stats['max_' + name] = max(self.stats['max_' + name], value)
        return {'page': self.pages, 'phase': phase, 'sources': sources,
                'compacts': compacts, 'cells': retained_cells}

    def one_gc(self, kind):
        schedule = getattr(self.owner, '_schedule_' + kind)
        self.stats['gc_opportunities'] += 1
        if not schedule():
            # A real refresh may report only the one protected latest compact.
            assert kind == 'compact_gc'
            assert self.owner._retention_snapshot[2]['compacts']['occupied'] in (0, 1)
            assert self.owner._slot is None and not self.owner._reconcile.is_set()
            assert not self.owner._closed and not self.owner._admission_closed
            assert self.owner.compact_gc_enabled and not self.owner._lock.locked()
            assert time.monotonic() >= self.owner._compact_gc_next
            self.stats['compact_occupancy_refusals'] += 1
            return None
        op = self.owner._slot
        assert op is not None and op.kind == kind
        assert not self.owner._schedule_receipt_gc()
        assert not self.owner._schedule_compact_gc()
        self.stats['scheduler_refusal_checks'] += 2
        budget = cells.Budget.for_owner(op)
        try:
            result = getattr(self.owner, '_collect_' + ('receipts' if kind == 'receipt_gc' else 'compacts'))(op, budget)
            assert result['status'] in ('retired', 'no_eligible_receipts', 'no_eligible_compacts'), result
            assert result['scanned'] <= 8 and result['retired'] <= 8
            assert result.get('prefix_rows', 0) <= 28
            assert result['body_bytes'] <= 32768
            self.observe_budget(budget)
            s = self.stats
            s['maintenance_calls'] += 1
            s['retired_' + ('receipts' if kind == 'receipt_gc' else 'compacts')] += result['retired']
            for field, value in [('max_scanned', result['scanned']), ('max_retired_per_pass', result['retired']),
                    ('max_prefix_rows', result.get('prefix_rows', 0)), ('max_body_bytes', result['body_bytes']),
                    ('max_prefix_keys', len(self.owner._receipt_gc_prefixes)),
                    ('max_cursor_bytes', max(len(self.owner._receipt_gc_cursor.encode()), len(self.owner._compact_gc_cursor.encode())))]:
                s[field] = max(s[field], value)
            assert len(self.owner._receipt_gc_prefixes) <= 8
            assert len(self.owner._receipt_gc_cursor.encode()) <= 256
            assert len(self.owner._compact_gc_cursor.encode()) <= 256
            return result
        finally:
            self.owner._slot = None; op.done.set(); op.slot_released.set()

    def maintain_page(self):
        page_start = self.virtual_now
        real_start = time.monotonic()
        for _ in range(PAIRS):
            # Translate ONLY scheduler timestamps after the simulated required
            # idle interval has elapsed. Never patch time.monotonic or budgets.
            self.virtual_now = max(self.virtual_now, self.virtual_due)
            real_now = time.monotonic()
            self.owner._compact_gc_next = real_now - .001
            self.owner._receipt_gc_next = real_now - .001
            budget = cells.Budget.for_owner()
            began = time.monotonic()
            self.owner._refresh_retention(budget)
            self.stats['retention_refresh_calls'] += 1
            self.observe_budget(budget)
            self.one_gc('compact_gc')
            self.one_gc('receipt_gc')
            completion = time.monotonic()
            elapsed = completion - began
            # Both real scheduler predicates must enforce the paired completion
            # floor before its timestamp is translated for the next opportunity.
            assert self.owner._receipt_gc_next > completion
            assert self.owner._compact_gc_next >= self.owner._receipt_gc_next
            assert not self.owner._schedule_receipt_gc()
            assert not self.owner._schedule_compact_gc()
            self.stats['scheduler_refusal_checks'] += 2
            self.virtual_now += elapsed
            self.virtual_due = self.virtual_now + cells.RETENTION_REFRESH_SECONDS
        # Pay the last completion floor too, before allowing any next page.
        self.virtual_now = self.virtual_due
        work = time.monotonic() - real_start
        required = self.virtual_now - page_start
        assert required >= PAIRS * cells.RETENTION_REFRESH_SECONDS
        assert required < PAGE * Q / RATE, required
        self.stats['maintenance_seconds'] += work
        self.stats['modeled_idle_lower_bound_seconds'] += required
        self.stats['max_page_idle_lower_bound_seconds'] = max(self.stats['max_page_idle_lower_bound_seconds'], required)
        self.stats['trend'].append(self.observe_occupancy('after'))
        self.check_replay()

    def check_replay(self):
        cutoff = max(0, self.frames // EPOCH * EPOCH - REPLAY)
        expected = {cid_at(p) for p in range(cutoff // Q, self.positions)}
        durable = {row[0] for row in self.optional.execute('SELECT cid FROM sources')}
        assert expected <= durable, (cutoff, expected - durable)
        assert set(self.owner._receipts) == durable
        assert self.latest_commit['part']['chunk_id'] in durable
        with self.store.db() as db:
            assert db.execute('SELECT body FROM native_core_commits WHERE slot=?',
                (core.slot(self.latest_commit),)).fetchone()[0] == canonical(self.latest_commit, core.COMPACT_BYTES)
            assert db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0] == self.positions
            assert db.execute('SELECT COUNT(*) FROM recording_work WHERE receipt_state="durable"').fetchone()[0] == self.positions

    def preservation(self):
        with self.store.db() as db:primary = rows_hash(db, IMMUTABLE)
        return (primary, rows_hash(self.optional, ('cells', 'dependencies')),
                {p.name: file_hash(p) for p in self.extra_paths})

    def restart(self):
        before = self.source_rows()
        history_before = self.preservation()
        began = time.monotonic()
        self.restart_memory()
        open_elapsed = time.monotonic() - began
        assert self.owner._receipt_gc_prefixes == {}
        assert self.owner._receipt_gc_retired == 0 and self.owner._compact_gc_retired == 0
        assert self.source_rows() == before
        self.sweep_startup()
        assert self.preservation() == history_before
        self.stats['startup_seconds'] += open_elapsed
        self.virtual_now += open_elapsed
        self.stats['restart'] = {'after_page': self.pages, 'receipts': len(before),
            'empty_prefix_on_reopen': True, 'durable_rows_unchanged': True,
            'first_prefix_page': None, 'caught_up_at_page': None}

    def close(self):
        assert self.owner._thread is None and not self.worker.thread.is_alive()
        self.owner._db = None; self.optional.close()
        artifacts(self.root)


@pytest.mark.parametrize('name,seconds,sources,compacts', CASES, ids=[r[0] for r in CASES])
def test_accelerated_metadata_retention(tmp_path, name, seconds, sources, compacts):
    report_path = Path(__file__).resolve().parents[1] / 'reports' / ('metadata-stress-r1-' + name + '.json')
    assert not report_path.exists(), 'Preserve prior stress evidence'
    fixture = MetadataStream(tmp_path, name)
    began = time.monotonic()
    try:
        total = seconds * RATE
        assert sources == (total + Q - 1) // Q
        assert compacts == sources + (total - 1) // EPOCH - (total - 1) // (2100 * RATE)
        page_count = (sources + PAGE - 1) // PAGE
        checkpoint_pages = {1, page_count // 2, page_count // 2 + 1, page_count}
        for page in range(1, page_count + 1):
            fixture.append_page(min(PAGE, sources - fixture.positions), total)
            if page == 1:fixture.archive(cid_at(0))
            checkpoint = page in checkpoint_pages or page % 32 == 0
            before = fixture.preservation() if checkpoint else None
            fixture.maintain_page()
            if before is not None:
                assert fixture.preservation() == before
                fixture.stats['history_checkpoints'].append(page)
            restart = fixture.stats['restart']
            if restart is not None:
                prefix = fixture.owner._receipt_gc_prefixes.get((fixture.sid, fixture.bid))
                if prefix is not None:
                    if restart['first_prefix_page'] is None:
                        # Eight bounded calls cannot prove more than eight pages
                        # on the first opportunity after a real optional reopen.
                        assert prefix['page_number'] <= PAIRS
                        restart['first_prefix_page'] = prefix['page_number']
                    if prefix['page_number'] == page and restart['caught_up_at_page'] is None:
                        restart['caught_up_at_page'] = page
            if page == page_count // 2:fixture.restart()
            assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 <= 1024**3
            assert artifacts(tmp_path) <= ARTIFACT_LIMIT
        s = fixture.stats
        assert fixture.positions == sources and fixture.compact_admissions == compacts
        assert s['gc_opportunities'] == page_count * PAIRS * 2
        assert s['maintenance_calls'] + s['compact_occupancy_refusals'] == s['gc_opportunities']
        assert s['retention_refresh_calls'] == page_count * PAIRS
        assert s['retired_receipts'] > 0 and s['retired_compacts'] > 0
        assert s['restart']['caught_up_at_page'] is not None
        # Quiet-stream growth must settle, rather than merely postponing a cap.
        assert s['trend'][-1]['sources'] <= 2 * PAGE
        assert s['trend'][-1]['compacts'] <= 2 * PAGE
        assert not any((tmp_path / 'uploads').iterdir())
        s.update(finished=True, source_admissions=sources, compact_admissions=compacts,
            pages=page_count, audio_hours=seconds / 3600,
            gc_opportunities_per_audio_hour=s['gc_opportunities'] / (seconds / 3600),
            maintenance_calls_per_audio_hour=s['maintenance_calls'] / (seconds / 3600),
            retention_refresh_calls_per_audio_hour=s['retention_refresh_calls'] / (seconds / 3600),
            combined_maintenance_calls=s['maintenance_calls'] + s['retention_refresh_calls'],
            combined_maintenance_calls_per_audio_hour=(s['maintenance_calls'] + s['retention_refresh_calls']) / (seconds / 3600),
            measured_maintenance_seconds_per_audio_hour=s['maintenance_seconds'] / (seconds / 3600),
            modeled_idle_lower_bound_seconds_per_audio_hour=s['modeled_idle_lower_bound_seconds'] / (seconds / 3600),
            artifact_bytes=artifacts(tmp_path), peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            tail_idle_lower_bound_seconds=s['max_page_idle_lower_bound_seconds'],
            limitations=['absent PCM; synthetic receipt_valid replaces both file and primary-row revalidation',
                'no acoustic, decoder, model, real-duration, or busy-foreground throughput qualification',
                'simulated page publication and idle cadence; real scheduler predicates and operation budgets',
                'modeled idle allowance is a lower bound excluding owner-loop wake/handoff latency',
                'measured maintenance time includes retention refreshes; startup sweeps reported separately',
                'one midpoint optional-owner reopen; no crash/VFS durability claim'])
    finally:
        fixture.stats['wall_seconds'] = time.monotonic() - began
        try:
            fixture.close()
            fixture.stats['closed'] = True
        finally:report_path.write_text(json.dumps(fixture.stats, indent=2) + '\n')
        if fixture.stats['finished']:
            shutil.rmtree(tmp_path)  # Only successful, positively closed fixtures.
