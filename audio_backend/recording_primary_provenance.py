"""Prospective native-primary attribution. Immutable audit, never identity authority.

Only the observed producer/ownership/primary-transaction path creates packets.
Logical charges are exact payload plus bounded indexed headers, not SQLite/WAL
allocation promises. No embedding, transcript text, source pin or old-row backfill.
"""
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import time

from . import transcript_contract as contract

SCHEMA = 'recording-native-primary-provenance-v1'
TABLE = 'recording_primary_packets_v1'
USAGE = 'recording_primary_usage_v1'
MAX_BODY = 65536
MAX_HEADERS = 8
MAX_COUNTER = (1 << 63) - 1
REASONS = ('retained', 'existing', 'empty', 'unknown_only', 'unsupported',
    'shape', 'normalization', 'attribution', 'capture_unavailable', 'source_changed',
    'core_unavailable', 'row_changed', 'capacity', 'recording_quota', 'global_quota',
    'disk_headroom', 'not_recording')
UNIT_FIELDS = ('start', 'end', 'ownership_anchor', 'anchor_kind', 'text', 'raw_text',
    'confidence', 'unit_kind', 'source_track_id')
SEGMENT_FIELDS = ('start', 'end', 'text', 'raw_text', 'speaker_local_id',
    'confidence', 'asr_backend', 'timestamp_kind', 'source_track_id')


class Refused(Exception):
    """Known optional refusal before any uncertain database/storage outcome."""
    def __init__(self, reason):
        if reason not in REASONS:raise ValueError('Unknown primary provenance reason')
        super().__init__(reason)


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('ascii')


def digest(value):return hashlib.sha256(encoded(value)).hexdigest()


def bounded(value):
    raw = encoded(value)
    if len(raw) > MAX_BODY:raise Refused('capacity')
    return raw


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):raise Refused('shape')
    return value


def _label(value):
    if value is not None and (type(value) is not str or not 1 <= len(value) <= 128):
        raise Refused('normalization')
    return value


def _text_size(value):
    if value is None:return 0
    if type(value) is not str:raise Refused('shape')
    if len(value) > MAX_BODY:raise Refused('capacity')
    return len(value.encode('utf-8'))


def _fingerprint_shape(segment):
    """Bound only audited producer fields before encoding; never copy extras."""
    size = 0
    for row, fields in ((segment, SEGMENT_FIELDS), *[(u, UNIT_FIELDS) for u in segment['_units']]):
        for key in fields:
            value = row.get(key)
            if key in ('start', 'end', 'ownership_anchor', 'confidence'):
                if value is not None:_number(value)
            else:size += _text_size(value)
            if size > MAX_BODY:raise Refused('capacity')
    return size


def _segment_hash(segment):
    # Fingerprint the same explicit fields before/after generic validation.
    # Arbitrary producer extras, including vectors, are never serialized.
    row = {k: segment.get(k) for k in SEGMENT_FIELDS}
    row['_units'] = [{k: unit.get(k) for k in UNIT_FIELDS} for unit in segment['_units']]
    return digest(row)


def enabled(settings):
    from .settings import primary_only_enabled
    return primary_only_enabled(settings) or bool(getattr(settings, 'recording_speaker_links_enabled', False)
        and getattr(settings, 'moss_automatic_enabled', False)
        and getattr(settings, 'native_owned_bounded_core_support_enabled', False)
        and getattr(settings, 'asr_backend', None) == 'sensevoice'
        and getattr(settings, 'diarization_backend', None) == 'nemotron')


def observe(result, native):
    """Capture before validate_result, which clips endpoints and normalizes IDs."""
    try:
        if native is None:raise Refused('capture_unavailable')
        from .lexical_evidence import PRIMARY_TIMESTAMP
        if any(str(k).startswith('_primary_') for k in result):raise Refused('shape')
        turns = native['turns']
        if result.get('metadata', {}).get('diarization_turns') != turns:
            raise Refused('attribution')
        if type(turns) is not list or len(turns) > 4096:raise Refused('shape')
        labels = []
        for turn in turns:
            if type(turn) is not dict or set(turn) != {'start', 'end', 'speaker_local_id'}:raise Refused('shape')
            if not 0 <= _number(turn['start']) < _number(turn['end']):raise Refused('shape')
            label = _label(turn.get('speaker_local_id'))
            if label is None:raise Refused('attribution')
            if label not in labels:labels.append(label)
            if len(labels) > 8:raise Refused('shape')
        turn_geometry = [[t['start'], t['end'], labels.index(t['speaker_local_id'])] for t in turns]
        bounded(turn_geometry)
        rows, units = [], []
        if type(result.get('segments')) is not list or len(result['segments']) > 128:raise Refused('capacity')
        text_bytes = 0
        for pi, segment in enumerate(result['segments']):
            if type(segment) is not dict or any(str(k).startswith('_primary_') for k in segment):raise Refused('shape')
            if ((segment.get('asr_backend') or result.get('metadata', {}).get('asr_backend')) != 'sensevoice'
                    or (segment.get('timestamp_kind') or result.get('metadata', {}).get('timestamp_semantics')) != PRIMARY_TIMESTAMP):
                raise Refused('unsupported')
            if type(segment.get('_units')) is not list or len(segment['_units']) > 4096:raise Refused('capacity')
            label = _label(segment.get('speaker_local_id'))
            if not segment['_units']:raise Refused('unsupported')
            if any(type(u) is not dict for u in segment['_units']):raise Refused('shape')
            text_bytes += _fingerprint_shape(segment)
            if text_bytes > MAX_BODY:raise Refused('capacity')
            for ui, unit in enumerate(segment['_units']):
                if any(str(k).startswith('_primary_') for k in unit):raise Refused('shape')
                if len(units) >= 4096:raise Refused('capacity')
                if unit.get('anchor_kind') != 'ctc_emission' or unit.get('unit_kind') is not None:
                    raise Refused('unsupported')
                a, b, anchor = (_number(unit[k]) for k in ('start', 'end', 'ownership_anchor'))
                if anchor != a or a < 0 or b < a:raise Refused('attribution')
                matches = [i for i, t in enumerate(turns) if t['start'] - .03 <= a <= t['end'] + .03]
                active = {turns[i]['speaker_local_id'] for i in matches}
                assigned = next(iter(active)) if len(active) == 1 else None
                if label != assigned:raise Refused('attribution')
                units.append([pi, ui, a, b, anchor, labels.index(label) if label is not None else None, matches])
                if len(units) % 32 == 0:bounded([turn_geometry, rows, units])
            rows.append(_segment_hash(segment))
        observation = {'labels': labels, 'turns': turn_geometry,
            'units': units, 'rows': rows, 'native_sha256': digest(native)}
        bounded(observation)
        return observation, 'pending'
    except Refused as exc:return None, str(exc)
    except (KeyError, TypeError, ValueError, OverflowError):return None, 'shape'


def validate_observation(result, captured):
    """Fail closed if generic validation changed any captured producer row."""
    observation, status = captured
    result['_primary_status'] = status
    result.pop('_primary_observation', None)
    result.pop('_primary_provenance', None)
    if observation is None:return
    try:
        rows = result['segments']
        if len(rows) != len(observation['rows']):raise Refused('normalization')
        text_bytes = 0
        for pi, segment in enumerate(rows):
            text_bytes += _fingerprint_shape(segment)
            if text_bytes > MAX_BODY or _segment_hash(segment) != observation['rows'][pi]:
                raise Refused('normalization')
        for pi, segment in enumerate(rows):
            segment['_primary_parent_ordinal'] = pi
            for ui, unit in enumerate(segment['_units']):unit['_primary_unit_ordinal'] = ui
        result['_primary_observation'] = observation
    except (KeyError, TypeError, Refused):
        result.pop('_primary_observation', None); result['_primary_status'] = 'normalization'


def _row_content(chunk, offset, segment, metadata):
    return {'chunk_id': chunk['id'], 'start': float(chunk['timeline_start'] + offset + segment['start']),
        'end': float(chunk['timeline_start'] + offset + segment['end']), 'text': segment.get('text', ''),
        'raw_text': segment.get('raw_text'), 'asr_backend': segment.get('asr_backend') or metadata.get('asr_backend'),
        'timestamp_kind': segment.get('timestamp_kind') or metadata.get('timestamp_semantics'),
        'confidence': float(segment['confidence']) if segment.get('confidence') is not None else None,
        'speaker_local_id': segment.get('speaker_local_id'), 'speaker_window_start': float(offset)}


def freeze(capture, chunk, native, offset, end, result, parts, evidence, owned_pcm):
    """Detach numeric witness while the exact producer and PCM are still live."""
    from .native_owned_cells import exact_frame, RATE, sha, Unavailable
    from .native_owned_core_seams import validate_commit
    observation = result.get('_primary_observation')
    if observation is None:return
    try:
        if not result['segments']:raise Refused('empty')
        if capture.core_seams is None or digest(native) != observation['native_sha256']:
            raise Refused('capture_unavailable')
        _, observed = capture.witness.prepared(native, capture.scope_start, capture.origin_frame)
        if len(parts) != 1 or parts[0].chunk_id != chunk['id']:raise Refused('source_changed')
        a, b = parts[0].logical_start, parts[0].logical_end
        if (a != capture.origin_frame + exact_frame(offset) or b != capture.origin_frame + exact_frame(end)
                or len(owned_pcm) != 2 * (b - a) or owned_pcm != capture.witness.read(a, b)):
            raise Refused('source_changed')
        if b > observed['replay_start'] + observed['state']['stable']['frame']:
            raise Refused('capture_unavailable')
        scope = {'session_id': chunk['session_id'], 'kind': capture.scope[0], 'key': capture.scope[2],
            'origin': float(capture.scope_start).hex(), 'run': capture.core_seams.run,
            'reset': capture.observed_reset_generation, 'epoch': observed['epoch_id'], 'index': observed['epoch_index']}
        commit = {'scope': scope, 'part': asdict(parts[0]), 'native_sha256': sha(native['state']),
            'cut_sha256': sha(evidence['core_cut']), 'evidence_sha256': sha(evidence), 'runtime_sha256': sha(observed['provenance'])}
        validate_commit(commit)
        qa = capture.analyzer_start; qb = qa + len(capture.analyzer) // 2
        dictionary = []
        for ordinal, turn in enumerate(capture.witness.raw_turns):
            lo = max(qa / RATE, observed['replay_start'] / RATE + turn['start'])
            hi = min(qb / RATE, observed['replay_start'] / RATE + turn['end'])
            if hi > lo:
                dictionary.append([ordinal, observation['labels'].index(turn['speaker_local_id']),
                    observed['replay_start'] + exact_frame(turn['start']), observed['replay_start'] + exact_frame(turn['end']),
                    lo - qa / RATE, hi - qa / RATE])
        if [[t[4], t[5], t[1]] for t in dictionary] != observation['turns']:
            raise Refused('attribution')
        available = {(u[0], u[1]): u for u in observation['units']}
        units, rows, hashes, sizes = [], [], [], []
        bound = False
        for segment in result['segments']:
            selected = []
            for ordinal in segment.get('_primary_units', []):
                item = available.get(tuple(ordinal))
                if item is None:raise Refused('attribution')
                row = [*item[:6], [dictionary[i][0] for i in item[6]]]
                if item[5] is not None:bound = True
                selected.append(len(units)); units.append(row)
            if not selected:raise Refused('attribution')
            rows.append([segment['_primary_parent_ordinal'], selected])
            content = _row_content(chunk, offset, segment, result['metadata'])
            hashes.append(digest(content))
            sizes.append(sum(len(content[k].encode('utf-8')) if content[k] is not None else 0
                for k in ('text', 'raw_text', 'asr_backend', 'timestamp_kind', 'speaker_local_id')) + 256)
        if not bound:raise Refused('unknown_only')
        packet = {'schema': SCHEMA, 'commit': commit,
            'query': [qa, qb, observed['replay_start'], observed['state']['processed']['frame'],
                      observed['state']['stable']['frame'], observed['observed_action_count'], observed['true_eof']],
            'owned_pcm16le_sha256': hashlib.sha256(owned_pcm).hexdigest(),
            'labels': observation['labels'], 'turns': dictionary, 'units': units, 'rows': rows}
        bounded(packet)
        result['_primary_provenance'] = {'packet': packet, 'row_hashes': hashes, 'row_sizes': sizes}
    except Refused as exc:result['_primary_status'] = str(exc)
    except (Unavailable, KeyError, TypeError, ValueError, OverflowError):result['_primary_status'] = 'capture_unavailable'
    finally:result.pop('_primary_observation', None)


# All-or-none exact schema. Unknown versions are neither rewritten nor adopted.
# Delete accounting is plain SQL so old binaries preserve it during chunk FK
# cascade. No recording/core FK: audit outlives receipt GC and recording cleanup.
DDL = {
TABLE: f'''CREATE TABLE {TABLE} (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT CHECK(sequence>0),
    sid TEXT NOT NULL CHECK(length(sid) BETWEEN 1 AND 64),
    rid TEXT NOT NULL CHECK(length(rid) BETWEEN 1 AND 64),
    cid TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE CHECK(length(cid) BETWEEN 1 AND 64),
    core_start INTEGER NOT NULL CHECK(core_start>=0), core_end INTEGER NOT NULL CHECK(core_end>core_start),
    core_slot TEXT NOT NULL UNIQUE CHECK(length(core_slot)=64),
    digest TEXT NOT NULL CHECK(length(digest)=64),
    body BLOB NOT NULL CHECK(typeof(body)='blob' AND length(body) BETWEEN 1 AND 65536),
    charge INTEGER NOT NULL CHECK(typeof(charge)='integer' AND charge=length(body)+168+2*(length(sid)+length(rid)+length(cid))+length(core_slot)+length(digest)),
    UNIQUE(cid,core_start,core_end))''',
USAGE: f'''CREATE TABLE {USAGE} (
    scope TEXT PRIMARY KEY CHECK(length(scope)<=64),
    packets INTEGER NOT NULL CHECK(typeof(packets)='integer' AND packets BETWEEN 0 AND 9223372036854775807),
    charge INTEGER NOT NULL CHECK(typeof(charge)='integer' AND charge BETWEEN 0 AND 9223372036854775807))''',
'recording_primary_session_v1': f'CREATE INDEX recording_primary_session_v1 ON {TABLE}(sid,sequence)',
'recording_primary_recording_v1': f'CREATE INDEX recording_primary_recording_v1 ON {TABLE}(sid,rid,sequence)',
'recording_primary_immutable_v1': f'''CREATE TRIGGER recording_primary_immutable_v1 BEFORE UPDATE ON {TABLE}
    BEGIN SELECT RAISE(ABORT,'Immutable primary provenance'); END''',
'recording_primary_insert_v1': f'''CREATE TRIGGER recording_primary_insert_v1 AFTER INSERT ON {TABLE} BEGIN
    INSERT INTO {USAGE}(scope,packets,charge) VALUES('',1,NEW.charge)
        ON CONFLICT(scope) DO UPDATE SET packets=packets+1,charge=charge+NEW.charge;
    INSERT INTO {USAGE}(scope,packets,charge) VALUES(NEW.rid,1,NEW.charge)
        ON CONFLICT(scope) DO UPDATE SET packets=packets+1,charge=charge+NEW.charge; END''',
'recording_primary_erasure_v1': f'''CREATE TRIGGER recording_primary_erasure_v1 BEFORE DELETE ON {TABLE}
    WHEN NOT EXISTS(SELECT 1 FROM sessions WHERE id=OLD.sid AND deleted=1)
    BEGIN SELECT RAISE(ABORT,'Primary provenance requires explicit session erasure'); END''',
'recording_primary_delete_v1': f'''CREATE TRIGGER recording_primary_delete_v1 AFTER DELETE ON {TABLE} BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM {USAGE} WHERE scope='' AND packets>0 AND charge>=OLD.charge)
        OR NOT EXISTS(SELECT 1 FROM {USAGE} WHERE scope=OLD.rid AND packets>0 AND charge>=OLD.charge)
        THEN RAISE(ABORT,'Primary provenance accounting mismatch') END;
    UPDATE {USAGE} SET packets=packets-1,charge=charge-OLD.charge WHERE scope IN ('',OLD.rid);
    DELETE FROM {USAGE} WHERE scope=OLD.rid AND packets=0 AND charge=0; END''',
}


CACHE_SCOPE_CHARGE = 512
CACHE_USAGE_COLUMN = ("INTEGER NOT NULL DEFAULT 0 CHECK(typeof(cache_scope_charge)='integer' "
    "AND cache_scope_charge>=0 AND cache_scope_charge%512=0 "
    "AND cache_scope_charge<=CASE WHEN scope='' THEN 32768 ELSE 512 END "
    "AND charge>=cache_scope_charge)")
CACHE_USAGE_DDL = DDL[USAGE][:-1] + ', cache_scope_charge ' + CACHE_USAGE_COLUMN + ')'


def primary_cache_usage(db, rid=None, budget=None):
    """Marker accounting is real charge, including zero-packet recordings."""
    # Count bounded rows without projecting unconstrained batch IDs. Collector
    # callers account every small result in their existing operation budget.
    if budget is not None:budget.projection(16)
    count = db.execute('''SELECT COUNT(*) FROM (SELECT 1 FROM recording_batches
        WHERE primary_cache_policy IS NOT NULL LIMIT 65)''').fetchone()[0]
    if count > 64:raise ValueError('Primary cache marker accounting mismatch')
    marked = False
    if rid is not None:
        if budget is not None:budget.projection(16)
        marked = db.execute('''SELECT 1 FROM recording_batches WHERE id=?
            AND primary_cache_policy IS NOT NULL''',(rid,)).fetchone() is not None
    expected = count * CACHE_SCOPE_CHARGE
    scopes = ('',) if rid is None else ('', rid)
    result = {}
    for scope in scopes:
        if budget is not None:budget.projection(32)
        row = db.execute(f'''SELECT
            CASE WHEN typeof(packets)='integer' THEN packets ELSE NULL END,
            CASE WHEN typeof(charge)='integer' THEN charge ELSE NULL END,
            CASE WHEN typeof(cache_scope_charge)='integer' THEN cache_scope_charge ELSE NULL END
            FROM {USAGE} WHERE scope=?''', (scope,)).fetchone()
        if row is None:
            if scope == '' or marked:raise ValueError('Missing primary cache accounting')
            result[scope] = (0, 0, 0); continue
        values = tuple(row)
        if (any(type(value) is not int or not 0 <= value <= MAX_COUNTER for value in values)
                or values[1] < values[2]
                or values[2] != (expected if scope == '' else CACHE_SCOPE_CHARGE if marked else 0)):
            raise ValueError('Primary cache marker accounting mismatch')
        result[scope] = values
    return result


def primary_cache_admission(store, db, rid):
    """Only known optional pre-write refusals may leave a fresh batch unmarked."""
    _id(rid)
    usage = primary_cache_usage(db, rid)
    if usage[rid] != (0, 0, 0):raise ValueError('Fresh primary cache scope already has accounting')
    for scope, limit, reason in (('', store.settings.recording_primary_provenance_global_bytes, 'global_quota'),
            (rid, store.settings.recording_primary_provenance_recording_bytes, 'recording_quota')):
        if usage[scope][1] + CACHE_SCOPE_CHARGE > limit:raise Refused(reason)
    if usage[''][2] + CACHE_SCOPE_CHARGE > 32768:raise Refused('capacity')
    headroom(store, CACHE_SCOPE_CHARGE)


def init_schema(db):
    names = tuple(DDL)
    rows = db.execute('SELECT name,sql FROM sqlite_master WHERE name IN (' + ','.join('?' for _ in names) + ')', names).fetchall()
    if rows:
        if len(rows) != len(names) or any(row['sql'] not in ((DDL[USAGE], CACHE_USAGE_DDL)
                if row['name'] == USAGE else (DDL[row['name']],)) for row in rows):
            raise ValueError('Unknown primary provenance schema; migration is not supported')
        return
    for sql in DDL.values():db.execute(sql)
    db.execute(f"INSERT INTO {USAGE} VALUES('',0,0)")


def database_identity(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise OSError('Unsafe primary provenance database')
    return info.st_dev, info.st_ino


def headroom(store, incremental):
    """Provenance-local sampled fixed family, never execution-permit admission."""
    from .moss_result_store import FREE_FLOOR, WRITE_ALLOWANCE, SCRATCH_ALLOWANCE, CELL_ALLOWANCE
    sizes = []
    for suffix in ('', '-wal', '-shm'):
        path = Path(str(store.path) + suffix)
        try:info = path.lstat()
        except FileNotFoundError:
            if not suffix:raise
            sizes.append(0); continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise OSError('Unsafe primary provenance database family')
        if not suffix and (info.st_dev, info.st_ino) != store._primary_provenance_identity:
            raise OSError('Primary provenance database replaced')
        sizes.append(info.st_size)
    baseline = (max(FREE_FLOOR, store.settings.disk_reserve_bytes) + WRITE_ALLOWANCE + SCRATCH_ALLOWANCE
        + sizes[1] + (CELL_ALLOWANCE if getattr(store.settings, 'native_owned_cells_enabled', False) else 0))
    free = shutil.disk_usage(Path(store.path).parent).free
    if free < baseline:raise OSError('Baseline database disk reserve unavailable')
    required = baseline + 4 * incremental + 65536
    if free < required:raise Refused('disk_headroom')
    return required


def _charge(raw, sid, rid, cid, slot, sha):
    # Exact UTF-8 indexed strings; IDs are restricted to bounded ASCII below.
    return len(raw) + 168 + 2 * (len(sid) + len(rid) + len(cid)) + len(slot) + len(sha)


def _id(value):
    if type(value) is not str or not 1 <= len(value) <= 64 or not value.isascii():raise Refused('shape')
    return value


def persist(store, db, chunk, offset, end, result, row_ids):
    """Same primary transaction after rows and surviving immutable core commit."""
    from .native_owned_cells import exact_frame, canonical, Unavailable
    from .native_owned_core_seams import slot, validate_commit, COMPACT_BYTES
    if not db.in_transaction:raise ValueError('Primary provenance requires primary transaction')
    draft = result.get('_primary_provenance')
    if draft is None:return result.get('_primary_status', 'capture_unavailable') if result.get('_primary_status') != 'pending' else 'capture_unavailable'
    handoff = result.get('_native_bounded_support')
    if type(handoff) is not dict or handoff.get('reserved') is not True:return 'capacity'
    budget = handoff['budget']
    budget.check()
    allowance = sum(draft['row_sizes']) + 8192
    elapsed = budget.elapsed + (time.monotonic() - budget._began if budget._began is not None else 0.)
    # Prospective admission preserves the existing primary finalization reserve.
    if budget.steps + 4000 + 512 * len(row_ids) > 146000 or budget.sql_bytes + allowance > 248 * 1024 or elapsed > budget.seconds - .05:
        return 'capacity'
    budget.reserve_projection(allowance)
    db.execute('SAVEPOINT recording_primary_optional')
    try:
        try:
            packet = json.loads(bounded(draft['packet']))
            commit = packet['commit']; part = validate_commit(commit); core_slot = slot(commit)
        except (ValueError, TypeError, KeyError, Unavailable) as exc:raise Refused('shape') from exc
        sid, cid = _id(chunk['session_id']), _id(chunk['id'])
        membership = db.execute('''SELECT b.id FROM recording_work w JOIN recording_batches b ON b.id=w.batch_id
            JOIN sessions s ON s.id=b.session_id WHERE w.chunk_id=? AND s.id=? AND s.deleted=0''', (cid, sid)).fetchone()
        if membership is None:raise Refused('not_recording')
        rid = _id(membership[0])
        if (part.chunk_id != cid or part.session_id != sid or
                (part.source_start, part.source_end) != (exact_frame(offset), exact_frame(end))):raise Refused('core_unavailable')
        actual = db.execute('SELECT body=? FROM native_core_commits WHERE slot=?',
            (canonical(commit, COMPACT_BYTES), core_slot)).fetchone()
        if actual is None or not actual[0]:raise Refused('core_unavailable')
        current = db.execute('SELECT session_id,digest,duration,timeline_start,next_offset,status FROM chunks WHERE id=?', (cid,)).fetchone()
        if (current is None or current['session_id'] != sid or current['digest'] != part.source_sha256
                or exact_frame(current['duration']) != part.source_frames
                or current['timeline_start'] != float.fromhex(commit['scope']['origin']) + (part.logical_start - part.source_start) / 16000
                or current['status'] not in ('running', 'paused')):
            raise Refused('source_changed')
        if len(row_ids) != len(packet['rows']) or len(row_ids) != len(draft['row_hashes']):raise Refused('row_changed')
        refs = []
        for index, (ident, expected, expected_size) in enumerate(zip(row_ids, draft['row_hashes'], draft['row_sizes'])):
            size = db.execute('SELECT ' + '+'.join('COALESCE(length(CAST(' + k + ' AS BLOB)),0)'
                for k in ('text', 'raw_text', 'asr_backend', 'timestamp_kind', 'speaker_local_id')) + '+256 FROM segments WHERE id=?', (ident,)).fetchone()
            if size is None or size[0] != expected_size:raise Refused('row_changed')
            row = db.execute('SELECT ' + ','.join(contract.PRIMARY_FIELDS) + ' FROM segments WHERE id=?', (ident,)).fetchone()
            if (row is None or digest({k: row[k] for k in contract.PRIMARY_FIELDS if k != 'id'}) != expected
                    or not _row_binding(packet, index, row)):
                raise Refused('row_changed')
            ref = contract.primary_evidence(sid, row)['source_ref']
            lref = None
            lexical_size = db.execute('SELECT length(CAST(lexical_evidence AS BLOB)) FROM segments WHERE id=?', (ident,)).fetchone()[0]
            if lexical_size is not None and 0 < lexical_size <= MAX_BODY and budget.sql_bytes + lexical_size + 2048 <= 248 * 1024:
                budget.reserve_projection(lexical_size + 2048)
                lexical = db.execute('SELECT lexical_evidence FROM segments WHERE id=? AND length(CAST(lexical_evidence AS BLOB))=?', (ident, lexical_size)).fetchone()
                if lexical is not None:
                    from .lexical_evidence import current_inventory
                    inventory = current_inventory(db, sid, dict(row, lexical_evidence=lexical[0]))
                    if inventory is not None:lref = inventory['source_ref']
            refs.append([ref, lref])
        packet['refs'] = refs
        try:validate_packet(packet)
        except (ValueError, TypeError, KeyError, Unavailable) as exc:raise Refused('shape') from exc
        raw = bounded(packet); sha = hashlib.sha256(raw).hexdigest()
        charge = _charge(raw, sid, rid, cid, core_slot, sha)
        existing = db.execute(f'SELECT digest,body=? AS same FROM {TABLE} WHERE cid=? AND core_start=? AND core_end=?',
            (raw, cid, part.source_start, part.source_end)).fetchone()
        if existing is not None:
            if existing['digest'] != sha or not existing['same']:raise Refused('row_changed')
            return 'existing'
        for scope, limit, reason in (('', store.settings.recording_primary_provenance_global_bytes, 'global_quota'),
                (rid, store.settings.recording_primary_provenance_recording_bytes, 'recording_quota')):
            usage = db.execute(f'SELECT charge,packets FROM {USAGE} WHERE scope=?', (scope,)).fetchone()
            if scope == '' and usage is None:raise ValueError('Missing primary provenance accounting')
            used = usage['charge'] if usage else 0
            if used + charge > limit:raise Refused(reason)
            if usage and usage['packets'] == MAX_COUNTER:raise Refused('capacity')
        headroom(store, charge)
        db.execute(f'''INSERT INTO {TABLE}(sid,rid,cid,core_start,core_end,core_slot,digest,body,charge)
            VALUES(?,?,?,?,?,?,?,?,?)''', (sid, rid, cid, part.source_start, part.source_end, core_slot, sha, raw, charge))
        return 'retained'
    except Refused as exc:
        db.execute('ROLLBACK TO recording_primary_optional')
        return str(exc)
    finally:db.execute('RELEASE recording_primary_optional')


def retain_status(provenance, status, offset):
    if status not in REASONS:raise ValueError('Invalid primary provenance status')
    totals = provenance.setdefault('recording_primary_provenance', {'schema': SCHEMA, 'counts': {}, 'windows': 0})
    counts = totals['counts']
    if any(key not in REASONS or type(value) is not int or value < 0 for key, value in counts.items()):
        raise ValueError('Invalid primary provenance summary')
    counts[status] = min(MAX_COUNTER, counts.get(status, 0) + 1)
    totals['windows'] = min(MAX_COUNTER, totals['windows'] + 1)
    totals['last_window_offset'] = offset


def _row_binding(packet, index, row):
    part = packet['commit']['part']; scope = packet['commit']['scope']
    units = [packet['units'][i] for i in packet['rows'][index][1]]
    local = {unit[5] for unit in units}
    if len(local) != 1:return False
    label = next(iter(local)); label = None if label is None else packet['labels'][label]
    origin_frame = part['logical_start'] - part['source_start']
    origin = float.fromhex(scope['origin']) + origin_frame / 16000
    base = (packet['query'][0] - origin_frame) / 16000
    offset = part['source_start'] / 16000
    start = origin + offset + (units[0][2] + base - offset)
    end = origin + offset + max(unit[3] + base - offset for unit in units)
    return (row['chunk_id'] == part['chunk_id'] and row['speaker_local_id'] == label
        and row['speaker_window_start'] == offset and row['start'] == start and row['end'] == end)


def validate_packet(packet, header=None):
    """Strict numeric semantics; canonical JSON/digest alone is insufficient."""
    from .native_owned_core_seams import validate_commit, slot
    from .native_owned_cells import integer, digest as native_digest, RATE
    fields = {'schema', 'commit', 'query', 'owned_pcm16le_sha256', 'labels', 'turns', 'units', 'rows', 'refs'}
    if type(packet) is not dict or set(packet) != fields or packet['schema'] != SCHEMA:
        raise ValueError('Invalid primary provenance packet shape')
    raw = bounded(packet); part = validate_commit(packet['commit']); scope = packet['commit']['scope']
    native_digest(packet['owned_pcm16le_sha256'])
    query = packet['query']
    if type(query) is not list or len(query) != 7:raise ValueError('Invalid primary query')
    qa, qb, replay, processed, stable, actions, eof = query
    for value in query[:6]:integer(value)
    if (not qa <= part.logical_start < part.logical_end <= qb or qb - qa > 60 * RATE
            or part.logical_end > replay + stable or stable > processed or actions < 1 or type(eof) is not bool):
        raise ValueError('Invalid primary query bounds')
    labels = packet['labels']
    if type(labels) is not list or not 1 <= len(labels) <= 8 or len(set(labels)) != len(labels):
        raise ValueError('Invalid primary labels')
    if any(label not in {scope['epoch'] + ':speaker_' + str(i) for i in range(1, 9)} for label in labels):
        raise ValueError('Invalid primary label scope')
    turns = packet['turns']
    if type(turns) is not list or not 1 <= len(turns) <= 4096:raise ValueError('Invalid primary turns')
    last = -1; by_ordinal = {}
    for turn in turns:
        if type(turn) is not list or len(turn) != 6:raise ValueError('Invalid primary turn')
        ordinal, label, a, b, lo, hi = turn
        for value in turn[:4]:integer(value)
        _number(lo); _number(hi)
        if (ordinal <= last or ordinal >= 4096 or label >= len(labels) or a >= b
                or lo != max(qa / RATE, replay / RATE + (a - replay) / RATE) - qa / RATE
                or hi != min(qb / RATE, replay / RATE + (b - replay) / RATE) - qa / RATE or lo >= hi):
            raise ValueError('Invalid primary turn bounds')
        by_ordinal[ordinal] = turn; last = ordinal
    units = packet['units']; rows = packet['rows']; refs = packet['refs']
    if (type(units) is not list or not 1 <= len(units) <= 4096 or type(rows) is not list
            or not 1 <= len(rows) <= 128 or type(refs) is not list or len(refs) != len(rows)):
        raise ValueError('Invalid primary unit/row capacity')
    originals = set(); any_bound = False
    for unit in units:
        if type(unit) is not list or len(unit) != 7:raise ValueError('Invalid primary unit')
        parent, ordinal, a, b, anchor, label, matches = unit
        integer(parent); integer(ordinal)
        for value in (a, b, anchor):_number(value)
        if parent >= 1000 or ordinal >= 4096 or not 0 <= a <= b <= (qb - qa) / RATE + .15 or anchor != a:
            raise ValueError('Invalid primary unit geometry')
        if (parent, ordinal) in originals:raise ValueError('Duplicate primary unit')
        originals.add((parent, ordinal))
        if label is not None:integer(label, len(labels) - 1)
        if type(matches) is not list:raise ValueError('Invalid primary turn ordinals')
        for match in matches:integer(match, 4095)
        expected = [t[0] for t in turns if t[4] - .03 <= a <= t[5] + .03]
        if matches != expected:raise ValueError('Invalid primary turn matching')
        active = {by_ordinal[i][1] for i in expected}
        assigned = next(iter(active)) if len(active) == 1 else None
        if label != assigned:raise ValueError('Invalid primary assignment')
        any_bound |= label is not None
    if not any_bound:raise ValueError('Unbound primary packet')
    if [(u[0], u[1]) for u in units] != sorted(originals):raise ValueError('Invalid original unit order')
    used = []; last_parent = -1; seen_refs = set()
    for row, refs_row in zip(rows, refs):
        if type(row) is not list or len(row) != 2 or type(row[1]) is not list or not row[1]:raise ValueError('Invalid primary row')
        parent, indices = row; integer(parent)
        if parent < last_parent:raise ValueError('Invalid primary row order')
        last_parent = parent
        for index in indices:
            integer(index)
            if index >= len(units) or units[index][0] != parent:raise ValueError('Invalid primary row mapping')
            used.append(index)
        if type(refs_row) is not list or len(refs_row) != 2:raise ValueError('Invalid primary refs')
        pref, lref = refs_row
        if type(pref) is not str or len(pref) > 90:raise ValueError('Invalid primary ref')
        bits = pref.split('.')
        if len(bits) != 3 or bits[0] != 'p' or not bits[1].isdigit() or str(int(bits[1])) != bits[1] or int(bits[1]) < 1:
            raise ValueError('Invalid primary ref')
        native_digest(bits[2])
        if pref in seen_refs:raise ValueError('Duplicate primary ref')
        seen_refs.add(pref)
        if lref is not None:
            if type(lref) is not str:raise ValueError('Invalid lexical ref')
            lexical = lref.split('.')
            if len(lexical) != 3 or lexical[:2] != ['l', bits[1]]:raise ValueError('Invalid lexical ref')
            native_digest(lexical[2])
    if used != list(range(len(units))):raise ValueError('Invalid primary final row order')
    if header is not None:
        required = {'sequence', 'sid', 'rid', 'cid', 'core_start', 'core_end', 'core_slot', 'digest', 'charge'}
        if not required <= set(header.keys()) or set(header.keys()) - required - {'n'}:raise ValueError('Invalid primary header shape')
        integer(header['sequence'], MAX_COUNTER, 1)
        for key in ('sid', 'rid', 'cid'):_id(header[key])
        if (header['sid'] != part.session_id or header['cid'] != part.chunk_id
                or header['core_start'] != part.source_start or header['core_end'] != part.source_end
                or header['core_slot'] != slot(packet['commit']) or header['digest'] != hashlib.sha256(raw).hexdigest()
                or header['charge'] != _charge(raw, header['sid'], header['rid'], header['cid'], header['core_slot'], header['digest'])):
            raise ValueError('Invalid primary header binding')
    return packet


def _read_packet(db, header):
    if not db.in_transaction:raise ValueError('Primary provenance read requires a transaction')
    if not 1 <= header['n'] <= MAX_BODY:raise ValueError('Invalid provenance body bound')
    row = db.execute(f'SELECT body FROM {TABLE} WHERE sequence=? AND length(body)=? AND length(body)<=?',
        (header['sequence'], header['n'], MAX_BODY)).fetchone()
    if row is None:raise ValueError('Provenance changed during read')
    raw = row[0]; value = json.loads(raw, object_pairs_hook=contract._unique)
    if bounded(value) != raw:raise ValueError('Invalid provenance encoding')
    validate_packet(value, header)
    return {'header': {k: header[k] for k in header.keys() if k != 'n'}, 'packet': value}


def read_page(db, *, sid, rid=None, after=0):
    """At most eight indexed headers and 64 KiB total packet bodies per call."""
    if not db.in_transaction:raise ValueError('Primary provenance read requires a transaction')
    _id(sid)
    if type(after) is not int or not 0 <= after <= MAX_COUNTER:raise ValueError('Invalid provenance cursor')
    if rid is None:
        sql = f'SELECT sequence,sid,rid,cid,core_start,core_end,core_slot,digest,charge,length(body) AS n FROM {TABLE} INDEXED BY recording_primary_session_v1 WHERE sid=? AND sequence>? ORDER BY sequence LIMIT 8'
        args = (sid, after)
    else:
        _id(rid)
        sql = f'SELECT sequence,sid,rid,cid,core_start,core_end,core_slot,digest,charge,length(body) AS n FROM {TABLE} INDEXED BY recording_primary_recording_v1 WHERE rid=? AND sequence>? AND sid=? ORDER BY sequence LIMIT 8'
        args = (rid, after, sid)
    headers = db.execute(sql, args).fetchall(); output = []; total = 0; cursor = after
    for h in headers:
        if not 1 <= h['n'] <= MAX_BODY:raise ValueError('Invalid provenance body bound')
        if total + h['n'] > MAX_BODY:break
        output.append(_read_packet(db, h))
        total += h['n']; cursor = h['sequence']
    return {'packets': output, 'next_cursor': cursor, 'body_bytes': total}


def read_exact(db, *, cid, start, end):
    """Historical absence always abstains; it never synthesizes old evidence."""
    _id(cid)
    if not db.in_transaction:raise ValueError('Primary provenance read requires a transaction')
    row = db.execute(f'SELECT sequence,sid,rid,cid,core_start,core_end,core_slot,digest,charge,length(body) AS n FROM {TABLE} WHERE cid=? AND core_start=? AND core_end=?', (cid, start, end)).fetchone()
    return None if row is None else _read_packet(db, row)


def current(db, saved, *, source_current):
    """Revalidate current authority; audit packet remains available on refusal.

    source_current is the caller's existing receipt/source authority, not a pin.
    Missing authority cannot be replaced by the retained proof itself.
    """
    from .native_owned_core_seams import slot, COMPACT_BYTES
    from .native_owned_cells import canonical, exact_frame
    if not db.in_transaction:raise ValueError('Primary provenance current read requires a transaction')
    packet = validate_packet(saved['packet'], saved['header']); commit = packet['commit']; part = commit['part']
    fields = ('sequence', 'sid', 'rid', 'cid', 'core_start', 'core_end', 'core_slot', 'digest', 'charge')
    persisted = db.execute(f'SELECT 1 FROM {TABLE} WHERE ' + ' AND '.join(k + '=?' for k in fields) + ' AND body=?',
        (*[saved['header'][k] for k in fields], bounded(packet))).fetchone()
    if persisted is None:return False
    live = db.execute('''SELECT 1 FROM sessions s JOIN recording_batches b ON b.session_id=s.id
        JOIN recording_work w ON w.batch_id=b.id WHERE s.id=? AND s.deleted=0 AND b.id=? AND w.chunk_id=?''',
        (part['session_id'], saved['header']['rid'], part['chunk_id'])).fetchone()
    if live is None:return False
    row = db.execute('SELECT body=? FROM native_core_commits WHERE slot=?', (canonical(commit, COMPACT_BYTES), slot(commit))).fetchone()
    if row is None or not row[0]:return False
    source = db.execute('SELECT session_id,digest,duration,timeline_start,next_offset,status FROM chunks WHERE id=?', (part['chunk_id'],)).fetchone()
    if (source is None or source['session_id'] != part['session_id'] or source['digest'] != part['source_sha256']
            or exact_frame(source['duration']) != part['source_frames']
            or source['timeline_start'] != float.fromhex(commit['scope']['origin']) + (part['logical_start'] - part['source_start']) / 16000
            or exact_frame(source['next_offset']) < part['source_end']
            or source['status'] == 'cancelled' or source_current is None or not source_current(part)):
        return False
    projected = 0
    for index, (ref, _) in enumerate(packet['refs']):
        ident = int(ref.split('.')[1])
        size = db.execute('SELECT ' + '+'.join('COALESCE(length(CAST(' + k + ' AS BLOB)),0)'
            for k in ('text', 'raw_text', 'asr_backend', 'timestamp_kind', 'speaker_local_id')) + '+512 FROM segments WHERE id=?', (ident,)).fetchone()
        if size is None:return False
        projected += size[0]
        if projected > MAX_BODY:return False
        row = db.execute('SELECT ' + ','.join(contract.PRIMARY_FIELDS) + ' FROM segments WHERE id=?', (ident,)).fetchone()
        if (row is None or not _row_binding(packet, index, row)
                or contract.primary_evidence(part['session_id'], row)['source_ref'] != ref):return False
    return True


def erased(db, sid):
    """Check the ordinary chunk cascade; no second deletion/accounting path."""
    if db.execute(f'SELECT 1 FROM {TABLE} WHERE sid=? LIMIT 1', (sid,)).fetchone():
        raise ValueError('Primary provenance survived session erasure')
