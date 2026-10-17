"""Bounded indexed transcript reads in one query-only SQLite snapshot."""
from contextlib import contextmanager
import hashlib
import math
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote

from . import transcript_contract as c
from .moss_automatic import TABLE as AUTO, POLICY as AUTO_POLICY
from .moss_result_store import TABLE as HISTORY
from .moss_handy_adapter import validate_timestamp_policy, validate_adjustment
from .native_owned_cells import POLICY_SHA256 as CELL_POLICY
from .native_owned_core_seams import POLICY_SHA256 as SEAM_POLICY, RESET_POLICY_SHA256
from .native_lexical_alternatives import SourceSupport
from .native_owned_support_cache import ReadError, _identifier
from .transcript_projection import project

READ_SECONDS, MAX_VM_STEPS = .75, 250000
MAX_CHUNKS = 128
CONTEXT_SECONDS = 5
_PRIMARY_SQL = ','.join('s.' + field for field in c.PRIMARY_FIELDS)
_PRIMARY_LENGTH = '+'.join("COALESCE(length(CAST(s." + field + " AS BLOB)),0)"
    for field in ('text', 'raw_text', 'asr_backend', 'timestamp_kind', 'speaker_local_id'))


class TranscriptStore:
    def __init__(self, store, *, clock=time.monotonic):
        self.path, self.clock = Path(store.path).resolve(), clock
        self.speaker_links_enabled = bool(getattr(getattr(store,'settings',None),'recording_speaker_links_enabled',False))
        st = self.path.stat()
        self.database_identity = (st.st_dev, st.st_ino)

    @contextmanager
    def snapshot(self, sid):
        _identifier(sid)
        deadline, steps, db = self.clock() + READ_SECONDS, 0, None
        def check():
            if self.clock() >= deadline or steps >= MAX_VM_STEPS:
                c.fail('unresolved_capacity')
        def progress():
            nonlocal steps
            steps += 100
            return int(steps >= MAX_VM_STEPS or self.clock() >= deadline)
        try:
            st = self.path.stat()
            if (st.st_dev, st.st_ino) != self.database_identity:
                c.fail('transcript_storage_unavailable')
            db = sqlite3.connect('file:' + quote(str(self.path), safe='/') + '?mode=ro',
                                uri=True, timeout=0)
            db.row_factory = sqlite3.Row
            db.set_progress_handler(progress, 100)
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA busy_timeout=0')
            db.execute('BEGIN')
            # First read is the visibility/tombstone linearization point.
            session = db.execute('SELECT deleted,timeline_end,transcript_revision FROM sessions WHERE id=?', (sid,)).fetchone()
            if session is None or session['deleted']:
                c.fail('session_not_found', 404)
            check()
            yield db, session, check
            check()
        except (OSError, sqlite3.Error):
            check()
            c.fail('transcript_storage_unavailable')
        finally:
            if db is not None: db.close()

    @staticmethod
    def _chunks(db, sid, block):
        start, end = block * c.BLOCK_FRAMES / c.RATE, (block + 1) * c.BLOCK_FRAMES / c.RATE
        lookback, lookahead = max(0., start - CONTEXT_SECONDS), end + CONTEXT_SECONDS
        # Chunk timeline intervals are disjoint. Seek the predecessor rather
        # than assuming uploaded chunks have the recording producer's length.
        chunks = list(db.execute('''SELECT id,timeline_start,duration FROM chunks INDEXED BY chunk_timeline
            WHERE session_id=? AND timeline_start<=? ORDER BY timeline_start DESC LIMIT 1''', (sid, lookback)))
        chunks += list(db.execute('''SELECT id,timeline_start,duration FROM chunks INDEXED BY chunk_timeline
            WHERE session_id=? AND timeline_start>? AND timeline_start<?
            ORDER BY timeline_start LIMIT ?''', (sid, lookback, lookahead, MAX_CHUNKS + 1)))
        if len(chunks) > MAX_CHUNKS: c.fail('unresolved_capacity')
        previous_end = None
        for chunk in chunks:
            a, duration = chunk['timeline_start'], chunk['duration']
            c.primary_frame(a); c.primary_frame(duration, end=True)
            if duration <= 0 or previous_end is not None and a < previous_end:
                c.fail('transcript_chunk_geometry_unresolved')
            previous_end = a + duration
            c.primary_frame(previous_end, end=True)
        # A predecessor before a gap contributes no support or ASR context.
        return [chunk for chunk in chunks if chunk['timeline_start'] + chunk['duration'] > lookback]

    @staticmethod
    def _extent(db, sid, session):
        # Each valid support ends inside its last source chunk. Published chunk
        # intervals are disjoint, so the last indexed chunk bounds every placed
        # support, even if session.timeline_end is lagging. No duration cap or
        # session-wide binding traversal is needed.
        extent = c.primary_frame(session['timeline_end'], end=True)
        tail = db.execute('''SELECT timeline_start,duration FROM chunks INDEXED BY chunk_timeline
            WHERE session_id=? ORDER BY timeline_start DESC LIMIT 1''', (sid,)).fetchone()
        if tail is not None:
            c.primary_frame(tail['timeline_start'])
            c.primary_frame(tail['duration'], end=True)
            if tail['duration'] <= 0: c.fail('transcript_chunk_geometry_unresolved')
            extent = max(extent, c.primary_frame(tail['timeline_start'] + tail['duration'], end=True))
        return extent

    @staticmethod
    def _primary(db, sid, block, chunks):
        start, end = block * c.BLOCK_FRAMES / c.RATE, (block + 1) * c.BLOCK_FRAMES / c.RATE
        lengths, total = [], 0
        for chunk in chunks:
            rows = db.execute(f'''SELECT s.id,{_PRIMARY_LENGTH} AS bytes FROM segments s INDEXED BY segments_chunk
                WHERE s.chunk_id=? AND s.start<? AND s.end>? ORDER BY s.start,s.id LIMIT ?''',
                (chunk['id'], end, start, c.MAX_PRIMARY + 1 - len(lengths))).fetchall()
            for row in rows:
                total += row['bytes']
                lengths.append((row['id'], chunk, row['bytes']))
            if len(lengths) > c.MAX_PRIMARY or total > c.MAX_PRIMARY_BYTES:
                c.fail('unresolved_capacity')
        values = []
        for ident, chunk, size in lengths:
            row = db.execute(f'SELECT {_PRIMARY_SQL} FROM segments s WHERE s.id=? AND s.chunk_id=?',
                             (ident, chunk['id'])).fetchone()
            evidence = c.primary_evidence(sid, row)
            # This narrow view does not invent placement for malformed primary
            # geometry; no unit is sliced to fit its chunk or a display block.
            a = c.primary_frame(chunk['timeline_start'])
            b = c.primary_frame(chunk['timeline_start'] + chunk['duration'], end=True)
            if evidence['start_frame'] < max(0,a - CONTEXT_SECONDS*c.RATE) or evidence['end_frame'] > b + CONTEXT_SECONDS*c.RATE:
                c.fail('transcript_primary_geometry_unresolved')
            values.append(evidence)
        return sorted(values, key=lambda item: (item['start_frame'], item['canonical_asr']['id']))

    _DESCRIPTOR_SQL = f'''SELECT a.seq,a.candidate_key,a.kind,a.state,a.reason,a.job_id,
        a.jobs_epoch,a.request_sha256,a.proof_sha256,a.cell_id,a.cid1,a.cid2,
        length(a.binding) AS binding_bytes,
        h.session_id AS history_sid,h.original_jobs_epoch AS history_epoch,
        h.canonical_request_sha256 AS history_request,h.idempotency_key AS history_key,h.archive_seq,h.schema_version,
        h.state AS history_state,h.result_sha256,length(h.result_json) AS result_bytes,
        length(h.metadata_json) AS metadata_bytes
        FROM {AUTO} a {{index}} LEFT JOIN {HISTORY} h ON h.job_id=a.job_id {{where}}'''

    @staticmethod
    def _check_descriptor_lengths(rows):
        total = 0
        for row in rows:
            size = row['binding_bytes']
            if type(size) is not int or not 1 <= size <= 4096: c.fail('unresolved_capacity')
            total += size
        if total > c.MAX_BINDING_BYTES: c.fail('unresolved_capacity')
        return [dict(row) for row in rows]

    @classmethod
    def _descriptors(cls, db, sid, chunks):
        # Seek both aliases in every state. Deduplicate before binding loads,
        # exact placement, intersection, and the 16-result admission check.
        # No session-wide intent query or result/binding payload scan is used.
        sequences = set()
        for chunk in chunks:
            for alias in (1, 2):
                rows = db.execute(f'''SELECT seq FROM {AUTO} INDEXED BY auto_cid{alias}
                    WHERE cid{alias}=? AND sid=? LIMIT ?''',
                    (chunk['id'], sid, c.MAX_BINDINGS + 1)).fetchall()
                sequences.update(row['seq'] for row in rows)
                if len(sequences) > c.MAX_BINDINGS: c.fail('unresolved_capacity')
        query = cls._DESCRIPTOR_SQL.format(index='', where='WHERE a.seq=? AND a.sid=?')
        rows = [db.execute(query, (seq, sid)).fetchone() for seq in sorted(sequences)]
        return cls._check_descriptor_lengths(rows)

    def _revision(self, db, sid, session):
        counter = session['transcript_revision']
        if type(counter) is not int or counter < 0:
            c.fail('transcript_revision_unavailable')
        change = db.execute('''SELECT cursor FROM result_changes INDEXED BY result_changes_session
            WHERE session_id=? ORDER BY cursor DESC LIMIT 1''', (sid,)).fetchone()
        links_revision = None
        if self.speaker_links_enabled:
            from .recording_speaker_link_store import read_session_revision
            links_revision = read_session_revision(db, sid)
        value = {'policy': c.POLICY_HASH, 'session': sid,
            'primary_change_max': change[0] if change else 0,
            'transcript_revision': counter, 'timeline_end': session['timeline_end']}
        if self.speaker_links_enabled:value['speaker_links_revision'] = links_revision
        return c.digest(value)

    @staticmethod
    def _binding(db, sid, row):
        raw = db.execute(f'SELECT binding FROM {AUTO} WHERE seq=? AND sid=?', (row['seq'], sid)).fetchone()[0]
        row['binding_sha256'] = hashlib.sha256(raw).hexdigest() if type(raw) is bytes else None
        binding = c.parse_json(raw, 4096)
        if (type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != row['candidate_key']
                or type(binding) is not dict or c.encoded(binding) != raw
                or set(binding) != {'automation_policy', 'selection_policy', 'kind', 'session_id',
                                     'parts', 'source_aliases', 'support_frames', 'core_frames'}
                or binding['automation_policy'] != AUTO_POLICY or binding['session_id'] != sid
                or binding['kind'] != row['kind']
                or binding['selection_policy'] != {'ordinary': CELL_POLICY,
                    'core_seam': SEAM_POLICY, 'reset_seam': RESET_POLICY_SHA256}.get(row['kind'])):
            c.fail('automatic_binding_invalid')
        parts, aliases, support = binding['parts'], binding['source_aliases'], binding['support_frames']
        if (type(parts) is not list or not 1 <= len(parts) <= 2
                or type(aliases) is not list or len(aliases) != len(parts)
                or type(support) is not list or len(support) != 2
                or any(type(n) is not int or not 0 <= n <= c.MAX_FRAME for n in support)
                or not 0 < support[1] - support[0] <= 320000):
            c.fail('automatic_binding_invalid')
        cores = binding['core_frames']
        if (type(cores) is not list or len(cores) != (1 if row['kind'] == 'ordinary' else 2)
                or any(type(pair) is not list or len(pair) != 2
                    or any(type(n) is not int or not 0 <= n <= c.MAX_FRAME for n in pair)
                    or pair[0] >= pair[1] for pair in cores)
                or cores[0][0] > support[0] or cores[-1][1] < support[1]
                or len(cores) == 2 and cores[0][1] != cores[1][0]):
            c.fail('automatic_binding_invalid')
        # Commit roots may repeat for two distinct cores in the same upload.
        # Only their expected source-root list is unique; actual aliases stay strict.
        indexed_aliases = list(dict.fromkeys(
            [row['cid1']] + ([row['cid2']] if row['cid2'] is not None else [])))
        if [alias.get('chunk_id') if type(alias) is dict else None for alias in aliases] != indexed_aliases:
            c.fail('automatic_binding_invalid')
        offsets, seen, at, cancelled = [], set(), support[0], False
        for part, alias in zip(parts, aliases):
            if (type(part) is not dict or set(part) != {'source_sha256', 'source_frames', 'source_start',
                    'source_end', 'logical_start', 'logical_end'}
                    or type(alias) is not dict or set(alias) != {'chunk_id', 'receipt_sha256'}
                    or type(part['source_sha256']) is not str or c.HASH.fullmatch(part['source_sha256']) is None
                    or type(alias['receipt_sha256']) is not str or c.HASH.fullmatch(alias['receipt_sha256']) is None
                    or any(type(part[name]) is not int or not 0 <= part[name] <= c.MAX_FRAME
                           for name in part if name != 'source_sha256')
                    or not 0 <= part['source_start'] < part['source_end'] <= part['source_frames']
                    or part['logical_start'] != at
                    or part['source_end'] - part['source_start'] != part['logical_end'] - at):
                c.fail('automatic_binding_invalid')
            cid = _identifier(alias['chunk_id'])
            if cid in seen or cid not in (row['cid1'], row['cid2']): c.fail('automatic_binding_invalid')
            seen.add(cid)
            source = db.execute('''SELECT c.session_id,c.digest,c.timeline_start,c.duration,c.status,
                w.frames,w.receipt_id,w.capture_start_frame,b.capture_origin_frame,b.session_id AS batch_sid
                FROM chunks c LEFT JOIN recording_work w ON w.chunk_id=c.id
                LEFT JOIN recording_batches b ON b.id=w.batch_id WHERE c.id=?''', (cid,)).fetchone()
            if (source is None or source['session_id'] != sid or source['frames'] is None
                    or source['capture_origin_frame'] is None or source['batch_sid'] != sid
                    or source['digest'] != part['source_sha256'] or source['frames'] != part['source_frames']
                    or source['receipt_id'] != alias['receipt_sha256']
                    or c.frame(source['duration']) != part['source_frames']
                    or c.frame(source['timeline_start']) != source['capture_start_frame'] - source['capture_origin_frame']):
                c.fail('automatic_source_unplaced')
            offsets.append(c.frame(source['timeline_start']) + part['source_start'] - part['logical_start'])
            cancelled |= source['status'] == 'cancelled'
            at = part['logical_end']
        if at != support[1] or len(set(offsets)) != 1: c.fail('automatic_source_unplaced')
        start, end = support[0] + offsets[0], support[1] + offsets[0]
        if not 0 <= start < end <= c.MAX_FRAME: c.fail('automatic_source_unplaced')
        return binding, start, end, cancelled

    @staticmethod
    def _result(db, sid, row, binding, start, end, cancelled):
        if (row['history_sid'] != sid or row['history_epoch'] != row['jobs_epoch']
                or row['history_request'] != row['request_sha256'] or row['schema_version'] != 1):
            c.fail('automatic_history_binding_invalid')
        if (type(row['result_bytes']) is not int or not 1 <= row['result_bytes'] <= c.MAX_RESULT_BYTES
                or type(row['metadata_bytes']) is not int or not 1 <= row['metadata_bytes'] <= c.MAX_METADATA_BYTES):
            c.fail('unresolved_capacity')
        saved = db.execute(f'SELECT result_json,metadata_json FROM {HISTORY} WHERE job_id=? AND session_id=?',
                           (row['job_id'], sid)).fetchone()
        raw, meta_raw = saved['result_json'], saved['metadata_json']
        if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != row['result_sha256']:
            c.fail('transcript_result_hash_invalid')
        result, meta = c.parse_json(raw, c.MAX_RESULT_BYTES), c.parse_json(meta_raw, c.MAX_METADATA_BYTES)
        try:
            support = SourceSupport.from_dict(meta['source_support'])
            identity, cell, context = meta['identity'], meta['cell_binding'], meta['context']
            if (identity['job_id'] != row['job_id'] or identity['session_id'] != sid
                    or identity['original_jobs_epoch'] != row['jobs_epoch']
                    or identity['canonical_request_sha256'] != row['request_sha256']
                    or identity['idempotency_key'] != row['history_key']
                    or context['automatic_candidate_key'] != row['candidate_key']
                    or context['origin'] != 'durable_cell'
                    or context['support_id'] != support.support_id
                    or cell['cell_id'] != row['cell_id'] or cell['proof_sha256'] != row['proof_sha256']
                    or cell['support'] != meta['source_support']
                    or result['cell_binding'] != cell
                    or [support.start_frame, support.end_frame] != binding['support_frames']
                    or [support.core_start_frame, support.core_end_frame] != [binding['core_frames'][0][0], binding['core_frames'][-1][1]]
                    or support.source_generation != c.digest({'schema': 'immutable-source-cell-generation-v1', 'sources': binding['parts']})
                    or support.plan_sha256 != c.digest({'schema': 'bounded-cell-mapping-v1', 'parts': binding['parts']})
                    or result['support_id'] != support.support_id
                    or result['schema'] != 'moss-handy-diagnostic-v1'
                    or result['complete_content'] is not False or result['eos'] is not None
                    or result['timestamp_quality'] != 'model_generated_unverified'
                    or result['speaker_identity_valid'] is not False or result['profile_update_allowed'] is not False):
                raise ValueError()
            phrases = result['diagnostic_phrases']
            if type(phrases) is not list or len(phrases) > 256: raise ValueError()
            validate_timestamp_policy(result)
            projected, labels = [], set()
            for index, phrase in enumerate(phrases):
                a, b, label, text = (phrase[key] for key in ('start', 'end', 'speaker_local_id', 'raw_text'))
                if (type(a) not in (int, float) or type(b) not in (int, float)
                        or not math.isfinite(a) or not math.isfinite(b) or not 0 <= a < b <= (end - start) / c.RATE
                        or type(label) is not str or not 1 <= len(label) <= 64
                        or type(text) is not str or not text.strip()
                        or phrase['timing_scope'] != 'support-relative-model-unverified'
                        or phrase['speaker_identity_valid'] is not False): raise ValueError()
                adjustment = validate_adjustment(result, phrase, support)
                labels.add(label)
                if len(labels) > 8: raise ValueError()
                projected.append({'source_ref': f"m.{row['job_id']}.{row['result_sha256']}.{index}",
                    'source': 'moss', 'home_block': start // c.BLOCK_FRAMES,
                    'text': text, 'raw_text': text, 'start': a, 'end': b,
                    'session_start': start / c.RATE + a, 'session_end': start / c.RATE + b,
                    'speaker_scope': [row['job_id'], row['result_sha256'], label],
                    'word_times': None, 'timestamp_quality': 'model_generated_unverified',
                    'complete_content': False, 'eos': None})
                if adjustment is not None:
                    # The intersection ends at the exact already-placed support
                    # frame, not a second rounded addition of model seconds.
                    projected[-1]['session_end'] = end / c.RATE
                    projected[-1]['timestamp_adjustment'] = dict(phrase['timestamp_adjustment'])
            errors = result['schema_errors']
            if type(errors) is not list: raise ValueError()
        except (KeyError, TypeError, ValueError, OverflowError):
            c.fail('transcript_result_invalid')
        eligible = bool(projected) and not errors and not cancelled
        candidate = {key: row[key] for key in ('candidate_key', 'job_id', 'result_sha256', 'archive_seq', 'kind')}
        candidate.update(start_frame=start, end_frame=end, home_block=start // c.BLOCK_FRAMES,
            phrases=projected, complete_content=False, eos=None, eligible=eligible,
            ineligible_reason=None if eligible else 'source_cancelled' if cancelled else 'native_parse_unresolved' if errors else 'no_timed_phrases',
            result_ref=f"m.{row['job_id']}.{row['result_sha256']}.all",
            inline_timing_projection='partial_or_unavailable' if errors or not projected else 'all_retained_timed_phrases',
            omitted_lexical_content='inspect_exact_result' if errors or not projected else 'none_reported',
            authority='historical_snapshot', source_current='not_checked')
        return candidate, result, meta

    def _speaker_annotations(self, db, sid, candidates, intersecting):
        """Read only the saved small decisions for page-local recording jobs."""
        if not self.speaker_links_enabled:return []
        from .recording_speaker_link_store import read_saved_for_recording
        scopes, by_job = {}, {}
        for row, *_ in intersecting:
            recording_ids = []
            for cid in (row['cid1'], row['cid2']):
                if cid is None:continue
                record = db.execute('SELECT batch_id FROM recording_work WHERE chunk_id=?', (cid,)).fetchone()
                if record is not None:recording_ids.append(record[0])
            if not recording_ids or len(set(recording_ids)) != 1:continue
            rid = recording_ids[0]
            if rid not in scopes:
                try:scopes[rid] = read_saved_for_recording(db, sid, rid)
                except (ReadError, ValueError, TypeError, AttributeError):
                    # An optional malformed decision cannot hide durable text.
                    # Scalar sidecar headers still bind this page's cursor.
                    scopes[rid] = {'state':'probe_unavailable','reason':'saved_links_unavailable'}
            by_job[row['job_id']] = scopes[rid]
        for candidate in candidates:
            saved = by_job.get(candidate['job_id'], {'state':'probe_unavailable'})
            for index, phrase in enumerate(candidate['phrases']):
                phrase['recording_speaker_link'] = c.speaker_annotation(saved,
                    candidate['job_id'], candidate['result_sha256'], index)
        # Only small scalar metadata is included at page level. All vectors,
        # native proof and per-crop geometry remain private immutable evidence.
        return [{'recording_id':rid, **{key:saved.get(key) for key in
            ('scope_id','state','revision','decision_revision','policy_sha256','space_id')}}
            for rid,saved in sorted(scopes.items())]

    def page(self, sid, *, block=0, cursor=None):
        if type(block) is not int or not 0 <= block <= c.MAX_FRAME // c.BLOCK_FRAMES:
            c.fail('invalid_transcript_block', 409)
        token = c.parse_cursor(cursor, sid) if cursor is not None else None
        if token is not None:
            if block != 0 and block != token['block']: c.fail('invalid_transcript_cursor', 409)
            block = token['block']
        with self.snapshot(sid) as (db, session, check):
            revision = self._revision(db, sid, session)
            if token is not None and token['revision'] != revision:
                c.fail('transcript_revision_changed', 409)
            chunks = self._chunks(db, sid, block)
            rows = self._descriptors(db, sid, chunks)
            intersecting, notices = [], []
            start, end = block * c.BLOCK_FRAMES, (block + 1) * c.BLOCK_FRAMES
            extent = self._extent(db, sid, session)
            for row in rows:
                check()
                try:
                    binding, a, b, cancelled = self._binding(db, sid, row)
                    if a < end and start < b:
                        extent = max(extent, b)
                        if row['history_state'] == 'committed':
                            intersecting.append((row, binding, a, b, cancelled))
                        else:
                            notices.append({'candidate_key': row['candidate_key'], 'job_id': row['job_id'],
                                'status': row['history_state'] or row['state'], 'reason': row['reason'],
                                'start_frame': a, 'end_frame': b, 'home_block': a // c.BLOCK_FRAMES,
                                'complete_content': False, 'eos': None})
                except ReadError as exc:
                    if exc.reason == 'unresolved_capacity': raise
                    notices.append({'candidate_key': row['candidate_key'], 'job_id': row['job_id'],
                        'status': 'unsupported_unplaced', 'reason': exc.reason,
                        'scope': 'page_local_source_alias'})
            if len(intersecting) > c.MAX_RESULTS: c.fail('unresolved_capacity')
            sizes = [row['result_bytes'] for row, *_ in intersecting]
            if any(type(n) is not int or not 1 <= n <= c.MAX_RESULT_BYTES for n in sizes) or sum(sizes) > c.MAX_RESULTS_BYTES:
                c.fail('unresolved_capacity')
            primary = self._primary(db, sid, block, chunks)
            candidates = [self._result(db, sid, *item)[0] for item in intersecting]
            link_scopes = self._speaker_annotations(db, sid, candidates, intersecting)
            check()
            value = project(block, primary, candidates, notices)
            value.update(session_id=sid, revision=revision, policy_sha256=c.POLICY_HASH,
                recording_speaker_links={'enabled':self.speaker_links_enabled, 'scopes':link_scopes,
                    'authority':'anonymous_provisional_recording_only', 'automatic_gallery_enrollment':False},
                reading_status='provisional_display_preference', complete_content=False, eos=None,
                history_scope='automatic_timestamped_recording_only',
                unsupported_history='unbound_manual_and_legacy_history_is_not_placed',
                unplaced_history_check='not_performed_by_time_page',
                binding_audit_path=f'/sessions/{sid}/transcript/binding-audit',
                next_cursor=c.cursor(sid, block + 1, revision) if end < extent else None)
            if not self.speaker_links_enabled:value.pop('recording_speaker_links')
            return c.transcript_response(value)

    def binding_audit(self, sid, *, cursor=None):
        """Keyset-paged placement audit, independent of temporal discovery.

        Only unplaced bindings are emitted. The cursor always advances past the
        last EXAMINED row, including an empty output page. A time page cannot
        establish that missing/moved source history does not exist.
        """
        token = c.parse_audit_cursor(cursor, sid) if cursor is not None else None
        after = token['after'] if token is not None else 0
        with self.snapshot(sid) as (db, session, check):
            revision = self._revision(db, sid, session)
            if token is not None and token['revision'] != revision:
                c.fail('transcript_revision_changed', 409)
            query = self._DESCRIPTOR_SQL.format(index='INDEXED BY auto_session_seq',
                where='WHERE a.sid=? AND a.seq>? ORDER BY a.seq LIMIT ?')
            rows = db.execute(query, (sid, after, c.AUDIT_BINDINGS + 1)).fetchall()
            more = len(rows) > c.AUDIT_BINDINGS
            rows = self._check_descriptor_lengths(rows[:c.AUDIT_BINDINGS])
            unplaced = []
            for row in rows:
                check()
                try:
                    self._binding(db, sid, row)
                except ReadError as exc:
                    if exc.reason == 'unresolved_capacity': raise
                    unplaced.append({'seq': row['seq'], 'candidate_key': row['candidate_key'],
                        'job_id': row['job_id'], 'automatic_state': row['state'],
                        'history_state': row['history_state'], 'status': 'unsupported_unplaced',
                        'reason': exc.reason})
            last = rows[-1]['seq'] if rows else after
            check()
            return c.response({'session_id': sid, 'revision': revision, 'policy_sha256': c.POLICY_HASH,
                'audit_scope': 'automatic_binding_placement_only',
                'result_schema_checked': False, 'source_current': 'not_checked',
                'unplaced_bindings': unplaced, 'examined': len(rows),
                'last_examined_seq': last, 'has_more': more,
                'next_cursor': c.audit_cursor(sid, last, revision) if more else None})

    def evidence(self, sid, ref):
        kind, ident, sha, index = c.parse_reference(ref)
        with self.snapshot(sid) as (db, session, check):
            if kind == 'primary':
                row = db.execute(f'''SELECT {_PRIMARY_LENGTH} AS bytes FROM segments s
                    JOIN chunks ch ON ch.id=s.chunk_id WHERE s.id=? AND ch.session_id=?''', (ident, sid)).fetchone()
                if row is None: c.fail('source_not_found', 404)
                if row['bytes'] > c.MAX_PRIMARY_BYTES: c.fail('unresolved_capacity')
                row = db.execute(f'SELECT {_PRIMARY_SQL} FROM segments s WHERE s.id=?', (ident,)).fetchone()
                evidence = c.primary_evidence(sid, row)
                if evidence['content_sha256'] != sha: c.fail('primary_evidence_changed', 409)
                return c.response(evidence)
            row = db.execute(f'''SELECT archive_seq,state,result_sha256,length(result_json) AS result_bytes,
                length(metadata_json) AS metadata_bytes FROM {HISTORY} WHERE job_id=? AND session_id=?''', (ident, sid)).fetchone()
            if row is None: c.fail('source_not_found', 404)
            if row['state'] != 'committed' or row['result_sha256'] != sha:
                c.fail('moss_evidence_changed', 409)
            if (type(row['result_bytes']) is not int or not 1 <= row['result_bytes'] <= c.MAX_RESULT_BYTES
                    or not 1 <= row['metadata_bytes'] <= c.MAX_METADATA_BYTES): c.fail('unresolved_capacity')
            saved = db.execute(f'SELECT result_json FROM {HISTORY} WHERE job_id=? AND session_id=?', (ident, sid)).fetchone()[0]
            if type(saved) is not bytes or hashlib.sha256(saved).hexdigest() != sha:
                c.fail('transcript_result_hash_invalid')
            result = c.parse_json(saved, c.MAX_RESULT_BYTES)
            payload = result
            if index != 'all':
                phrases = result.get('diagnostic_phrases') if type(result) is dict else None
                if type(phrases) is not list or index >= len(phrases): c.fail('source_not_found', 404)
                payload = phrases[index]
            return c.response({'source_ref': ref, 'source': 'moss', 'job_id': ident,
                'result_sha256': sha, 'index': index, 'archive_seq': row['archive_seq'],
                'authority': 'historical_snapshot', 'source_current': 'not_checked',
                'source_valid_at_commit': 'not_asserted', 'complete_content': False,
                'eos': None, 'word_times': None, 'evidence': payload})
