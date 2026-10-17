"""Prospective, private retention of complete post-adapter CTC contributions.

This is stored provenance, not fresh PCM attestation or certified word timing.
No reader/selection policy uses this optional inventory. Database errors propagate.
"""
import hashlib
import json
import math
import unicodedata

from . import transcript_contract as contract

SCHEMA = 'post-adapter-ctc-inventory-v1'
MAX_SEGMENT_UNITS, MAX_WINDOW_UNITS, MAX_PARTS = 512, 4096, 16
MAX_SEGMENT_BYTES, MAX_WINDOW_BYTES, MAX_SESSION_BYTES = 65536, 524288, 33554432
MAX_UNIT_CHARS = 8192
COLUMN = 'lexical_evidence TEXT'
BUDGET_COLUMN = ("lexical_evidence_bytes INTEGER NOT NULL DEFAULT 0 "
    "CHECK(typeof(lexical_evidence_bytes)='integer' AND lexical_evidence_bytes>=0 "
    "AND lexical_evidence_bytes<=33554432)")
SOURCE_FIELDS = ('id', 'session_id', 'digest', 'timeline_start', 'duration')
TIME_NOTE = 'post-adapter context-shifted millisecond-rounded/clamped CTC starts; estimated ends'
SOURCE_NOTE = 'stored_source_rows_and_measured_analyzer_ranges_not_pcm_attestation'
PRIMARY_TIMESTAMP = 'Native CTC token starts; interval ends are estimated, not forced alignment'


class Rejected(ValueError):
    """Only a bounded optional-input rejection; never a database failure."""


def reject(reason='invalid'):
    raise Rejected(reason)


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value): reject()
    return value


def text(value, maximum=MAX_UNIT_CHARS):
    if type(value) is not str or len(value) > maximum: reject('capacity')
    try:
        if len(value.encode('utf-8')) > maximum * 4: reject('capacity')
    except UnicodeError: reject('unsupported_text')
    return value


def encoded(value):
    return contract.encoded(value)


def _group_supported(value):
    """Conservative future eligibility only; never split or normalize a unit."""
    value = value.strip()
    if not value: return True
    if any(unicodedata.category(c)[0] in 'MCZ' or c.isspace() for c in value): return False
    if all(ord(c) < 128 and (c.isalnum() or c in "'.,!?;:-") for c in value): return True
    # Existing adapter keeps CJK characters independent. Other scripts, emoji,
    # combining sequences and multi-character non-ASCII groups stay ineligible.
    return len(value) == 1 and (0x3400 <= ord(value) <= 0x4dbf or
        0x4e00 <= ord(value) <= 0x9fff or 0x20000 <= ord(value) <= 0x3134f or
        unicodedata.category(value).startswith('P'))


def _assemble(units, key):
    parts, spans, cursor, last = [], [], 0, ''
    for unit in units:
        value = unit[key]
        separator = (' ' if unit.get('unit_kind') == 'native_phrase' and cursor
            and value and not last.isspace()
            and not value[0].isspace() else '')
        part = separator + value
        parts.append(part); spans.append([cursor, cursor + len(part)]); cursor += len(part)
        if part:last=part[-1]
        if cursor > MAX_SEGMENT_BYTES: reject('capacity')
    joined = ''.join(parts)
    left = len(joined) - len(joined.lstrip())
    final = joined.strip()
    spans = [[max(0, min(len(final), a-left)), max(0, min(len(final), b-left))] for a,b in spans]
    return final, spans


class Capture:
    """Bounded observation of the existing assembly, with no primary mutation."""
    def __init__(self, core_start, core_end, analyzer_start):
        self.coordinates = {'axis': 'chunk_relative_seconds', 'core_start': core_start,
            'core_end': core_end, 'analyzer_start': analyzer_start}
        self.items, self.total, self.reason = {}, 0, None

    def add(self, segment, unit):
        if self.reason: return
        try:
            if unit.get('anchor_kind') != 'ctc_emission' or unit.get('unit_kind') is not None:
                reject('unsupported_adapter')
            values = self.items.setdefault(id(segment), [])
            if len(values) >= MAX_SEGMENT_UNITS or self.total >= MAX_WINDOW_UNITS: reject('capacity')
            item = {key: number(unit[key]) for key in ('start','end','ownership_anchor')}
            if item['end'] < item['start']: reject()
            item.update(text=text(unit['text']), raw_text=text(unit.get('raw_text',unit['text'])),
                anchor_kind='ctc_emission')
            # Preserve source-local track declarations; no cross-track inference.
            track = unit.get('source_track_id')
            if track is not None: item['source_track_id'] = text(track,128)
            values.append(item); self.total += 1
        except (Rejected, KeyError, TypeError) as exc:
            self.reason = str(exc) if isinstance(exc, Rejected) else 'invalid'
            self.items.clear()

    def finish(self, segments):
        if self.reason: return self.reason
        try:
            prepared = []
            for segment in segments:
                units = self.items.get(id(segment))
                if not units: reject('missing')
                display, display_spans = _assemble(units, 'text')
                raw, raw_spans = _assemble(units, 'raw_text')
                if display != segment['text'] or raw != segment['raw_text']: reject('reconstruction')
                for unit, ds, rs in zip(units, display_spans, raw_spans):
                    unit.update(display_span=ds,raw_span=rs,
                        granular_eligible=_group_supported(unit['text']) and _group_supported(unit['raw_text']))
                value = {'coordinates':dict(self.coordinates), 'units':units}
                if len(encoded(value)) > MAX_SEGMENT_BYTES: reject('capacity')
                prepared.append(value)
            for segment, value in zip(segments,prepared): segment['_lexical_evidence'] = value
            return None
        except (Rejected, KeyError, TypeError, UnicodeError) as exc:
            return str(exc) if isinstance(exc,Rejected) else 'invalid'


def snapshot_parts(db, parts, sid):
    """Snapshot only already-selected extraction inputs, before their decode."""
    if type(parts) is not list or not 1 <= len(parts) <= MAX_PARTS: return None
    snapshots = []
    for part in parts:
        row = db.execute('SELECT id,session_id,digest,timeline_start,duration FROM chunks WHERE id=?',
            (part['id'],)).fetchone()
        if row is None or row['session_id'] != sid or any(row[k] != part[k]
                for k in ('id','session_id','timeline_start','duration')): return None
        value = dict(row)
        value.update(seek_seconds=part['read_start'],requested_seconds=part['read_duration'])
        snapshots.append(value)
    return snapshots


def _sources(value, sid):
    if type(value) is not list or not 1 <= len(value) <= MAX_PARTS: reject('source_unavailable')
    cursor = 0; declared_end=None; seen=set()
    for part in value:
        if type(part) is not dict or len(part) != 8 or set(part) != {*SOURCE_FIELDS,'seek_seconds','requested_seconds','analyzer_frames'}:
            reject('source_unavailable')
        for key in ('id','session_id'): text(part[key],128)
        if part['session_id'] != sid or part['id'] in seen: reject('source_unavailable')
        seen.add(part['id'])
        if type(part['digest']) is not str or contract.HASH.fullmatch(part['digest']) is None: reject('source_unavailable')
        for key in ('timeline_start','duration','seek_seconds','requested_seconds'): number(part[key])
        if (part['timeline_start'] < 0 or part['duration'] <= 0 or part['seek_seconds'] < -.002
                or part['requested_seconds'] <= 0 or
                part['seek_seconds'] + part['requested_seconds'] > part['duration'] + .002): reject('source_unavailable')
        declared_start=part['timeline_start']+part['seek_seconds']
        if declared_end is not None and abs(declared_start-declared_end)>.002: reject('source_unavailable')
        declared_end=declared_start+part['requested_seconds']
        frames = part['analyzer_frames']
        if type(frames) is not list or len(frames) != 2 or any(type(n) is not int for n in frames): reject('source_unavailable')
        if frames[0] != cursor or not frames[0] < frames[1] <= 962560: reject('source_unavailable')
        cursor = frames[1]
    return value


def _sources_current(db, parts):
    for part in parts:
        row = db.execute('SELECT id,session_id,digest,timeline_start,duration FROM chunks WHERE id=?',
            (part['id'],)).fetchone()
        if row is None or any(row[k] != part[k] for k in SOURCE_FIELDS): return False
    return True


def _draft(value, segment):
    if type(value) is not dict or len(value) != 2 or set(value) != {'coordinates','units'}: reject('missing')
    coordinates = value['coordinates']
    if type(coordinates) is not dict or len(coordinates) != 4 or set(coordinates) != {'axis','core_start','core_end','analyzer_start'}: reject()
    if coordinates['axis'] != 'chunk_relative_seconds': reject()
    for key in ('core_start','core_end','analyzer_start'): number(coordinates[key])
    if coordinates['core_end'] <= coordinates['core_start']: reject()
    units = value['units']
    if type(units) is not list or not 1 <= len(units) <= MAX_SEGMENT_UNITS: reject('capacity')
    for unit in units:
        required = {'start','end','ownership_anchor','text','raw_text','anchor_kind','display_span','raw_span','granular_eligible'}
        if type(unit) is not dict or not 9 <= len(unit) <= 10 or not required <= set(unit) or set(unit)-required-{'source_track_id'}: reject()
        if unit['anchor_kind'] != 'ctc_emission': reject('unsupported_adapter')
        for key in ('start','end','ownership_anchor'): number(unit[key])
        if unit['end'] < unit['start'] or unit['ownership_anchor'] != unit['start']: reject()
        for key in ('text','raw_text'): text(unit[key])
        if 'source_track_id' in unit: text(unit['source_track_id'],128)
        expected = _group_supported(unit['text']) and _group_supported(unit['raw_text'])
        if type(unit['granular_eligible']) is not bool or unit['granular_eligible'] != expected: reject()
    for key,span_key in (('text','display_span'),('raw_text','raw_span')):
        final, spans = _assemble(units,key)
        if final != segment[key] or any(u[span_key] != s for u,s in zip(units,spans)): reject('reconstruction')
        for unit in units:
            if type(unit[span_key]) is not list or any(type(n) is not int for n in unit[span_key]): reject()
    if len(encoded(value)) > MAX_SEGMENT_BYTES: reject('capacity')
    return value



def _geometry(draft, segment, chunk, parts, *, absolute=False):
    """Validate transformations without pretending decoded frames map to source PCM."""
    coordinates=draft['coordinates'];base=coordinates['analyzer_start'];lo=coordinates['core_start']
    origin=chunk['timeline_start'];offset=segment['speaker_window_start'] if absolute else lo
    number(offset)
    if abs(lo-offset)>1e-9: reject('coordinates')
    if segment['asr_backend'] != 'sensevoice' or segment['timestamp_kind'] != PRIMARY_TIMESTAMP:
        reject('unsupported_adapter')
    declared_start=parts[0]['timeline_start']+parts[0]['seek_seconds']
    declared_end=parts[-1]['timeline_start']+parts[-1]['seek_seconds']+parts[-1]['requested_seconds']
    requested_duration=declared_end-declared_start
    if (abs(declared_start-(origin+base))>.002 or not 0<requested_duration<=60.01
            or lo<base-1e-9 or coordinates['core_end']>base+requested_duration+.002): reject('coordinates')
    measured_duration=parts[-1]['analyzer_frames'][1]/16000
    for unit in draft['units']:
        for key in ('start','end','ownership_anchor'):
            local=unit[key]-base
            if not -1e-9<=local<=min(requested_duration,measured_duration)+.15: reject('coordinates')
    start=draft['units'][0]['start']-lo
    end=max(unit['end']-lo for unit in draft['units'])
    # Match lexical_core and commit_window operation order, not an algebraic
    # simplification that can round differently on a nonzero session origin.
    if absolute: start,end=origin+offset+start,origin+offset+end
    if segment['start']!=start or segment['end']!=end: reject('coordinates')


def prepare(result, chunk, offset, end):
    """Validate optional input before SQL writes; reject the entire inventory."""
    try:
        if result.get('_lexical_rejection') is not None: reject('capture_unavailable')
        segments = result['segments']
        if type(segments) is not list or not segments: reject('missing')
        if len(segments) > MAX_WINDOW_UNITS: reject('capacity')
        parts = _sources(result.get('_lexical_sources'),chunk['session_id'])
        if not any(part['id']==chunk['id'] for part in parts): reject('source_unavailable')
        total, size, drafts = 0, 0, []
        for segment in segments:
            draft = _draft(segment.get('_lexical_evidence'),segment)
            metadata=result.get('metadata',{})
            effective=dict(segment,asr_backend=segment.get('asr_backend') or metadata.get('asr_backend'),
                timestamp_kind=segment.get('timestamp_kind') or metadata.get('timestamp_semantics'))
            _geometry(draft,effective,chunk,parts)
            if (abs(draft['coordinates']['core_start']-offset)>1e-9
                    or abs(draft['coordinates']['core_end']-end)>1e-9): reject()
            total += len(draft['units']); size += len(encoded(draft))
            if total > MAX_WINDOW_UNITS or size > MAX_WINDOW_BYTES: reject('capacity')
            drafts.append(draft)
        return (drafts,parts), None
    except (Rejected,KeyError,TypeError,UnicodeError,OverflowError) as exc:
        return None, str(exc) if isinstance(exc,Rejected) else 'invalid'


def primary_sha(sid,row):
    return contract.digest({'session_id':sid, **{key:row[key] for key in contract.PRIMARY_FIELDS}})


def persist(db, current, supplied_chunk, rows, prepared):
    """Only commit_window may call this inside its primary transaction."""
    if not db.in_transaction: raise ValueError('Lexical retention requires primary transaction')
    drafts, parts = prepared
    if (any(current[k] != supplied_chunk[k] for k in SOURCE_FIELDS)
            or not _sources_current(db,parts)): return 'source_changed'
    payloads, charge = [], 0
    for row,draft in zip(rows,drafts):
        try:_geometry(draft,row,current,parts,absolute=True)
        except Rejected:return 'invalid'
        value = {'schema':SCHEMA, 'primary_sha256':primary_sha(current['session_id'],row),
            'source_provenance':SOURCE_NOTE, 'source_parts':parts, 'timestamp_semantics':TIME_NOTE, **draft}
        raw = encoded(value).decode('ascii')
        if len(raw) > MAX_SEGMENT_BYTES: return 'capacity'
        charge += len(raw)
        if charge > MAX_WINDOW_BYTES: return 'capacity'
        payloads.append((raw,row['id']))
    if len(payloads) != len(drafts) or len(rows) != len(drafts): return 'invalid'
    used = db.execute('SELECT lexical_evidence_bytes FROM sessions WHERE id=?',(current['session_id'],)).fetchone()[0]
    if used + charge > MAX_SESSION_BYTES: return 'session_capacity'
    db.execute('UPDATE sessions SET lexical_evidence_bytes=lexical_evidence_bytes+? WHERE id=?',
        (charge,current['session_id']))
    db.executemany('UPDATE segments SET lexical_evidence=? WHERE id=?',payloads)
    return 'retained'


def current_inventory(db,sid,row):
    """Private dormant currentness validation. No public evidence GET is added."""
    raw = row['lexical_evidence']
    if raw is None: return None
    try:
        if type(raw) is not str or not 0 < len(raw) <= MAX_SEGMENT_BYTES: reject('capacity')
        value = json.loads(raw,object_pairs_hook=contract._unique,
            parse_constant=lambda _: reject())
        fields = {'schema','primary_sha256','source_provenance','source_parts','timestamp_semantics','coordinates','units'}
        if type(value) is not dict or len(value) != len(fields) or set(value) != fields or encoded(value).decode('ascii') != raw: reject()
        if value['schema'] != SCHEMA or value['source_provenance'] != SOURCE_NOTE or value['timestamp_semantics'] != TIME_NOTE: reject()
        if value['primary_sha256'] != primary_sha(sid,row): reject()
        parts = _sources(value['source_parts'],sid)
        draft=_draft({k:value[k] for k in ('coordinates','units')},row)
        own=next((part for part in parts if part['id']==row['chunk_id']),None)
        if own is None:reject('source_unavailable')
        _geometry(draft,row,own,parts,absolute=True)
    except (Rejected,ValueError,KeyError,TypeError,UnicodeError,RecursionError,OverflowError): return None
    if not _sources_current(db,parts): return None
    sha = hashlib.sha256(raw.encode('ascii')).hexdigest()
    return {'source_ref':f"l.{row['id']}.{sha}", 'inventory_sha256':sha, 'inventory':value}


_PRIMARY_FIELDS = ','.join(contract.PRIMARY_FIELDS)
_CHANGED_PRIMARY = ' OR '.join(f'OLD.{key} IS NOT NEW.{key}' for key in contract.PRIMARY_FIELDS)
TRIGGERS = {
    'lexical_inventory_v1_revision': '''CREATE TRIGGER lexical_inventory_v1_revision
        AFTER UPDATE OF lexical_evidence ON segments WHEN OLD.lexical_evidence IS NOT NEW.lexical_evidence BEGIN
        UPDATE sessions SET transcript_revision=CASE WHEN transcript_revision<9223372036854775807
            THEN transcript_revision+1 ELSE RAISE(ROLLBACK,'Transcript revision exhausted') END
            WHERE id IN (SELECT session_id FROM chunks WHERE id=OLD.chunk_id OR id=NEW.chunk_id); END''',
    'lexical_inventory_v1_revoke': f'''CREATE TRIGGER lexical_inventory_v1_revoke
        AFTER UPDATE OF {_PRIMARY_FIELDS} ON segments
        WHEN NEW.lexical_evidence IS NOT NULL AND ({_CHANGED_PRIMARY}) BEGIN
        UPDATE segments SET lexical_evidence=NULL WHERE id=NEW.id; END''',
}


def init_schema(db):
    """All-or-none additive shape; historical inventory remains NULL."""
    if not db.in_transaction: raise ValueError('Lexical migration requires transaction')
    segment = {r['name']:r for r in db.execute('PRAGMA table_info(segments)')}
    session = {r['name']:r for r in db.execute('PRAGMA table_info(sessions)')}
    present = ['lexical_evidence' in segment,'lexical_evidence_bytes' in session]
    existing = [db.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone() for name in TRIGGERS]
    shape = present + [r is not None for r in existing]
    if any(shape) and not all(shape): raise ValueError('Incomplete lexical inventory schema')
    if not any(shape):
        db.execute('ALTER TABLE segments ADD COLUMN '+COLUMN)
        db.execute('ALTER TABLE sessions ADD COLUMN '+BUDGET_COLUMN)
        for sql in TRIGGERS.values(): db.execute(sql)
        return
    col = segment['lexical_evidence']
    if col['type'] != 'TEXT' or col['notnull'] or col['dflt_value'] is not None: raise ValueError('Incompatible lexical inventory column')
    definition = db.execute("SELECT sql FROM sqlite_master WHERE name='sessions'").fetchone()[0]
    if ' '.join(BUDGET_COLUMN.split()) not in ' '.join(definition.split()): raise ValueError('Incompatible lexical budget column')
    for sql,row in zip(TRIGGERS.values(),existing):
        if ' '.join(sql.split()) != ' '.join(row[0].split()): raise ValueError('Incompatible lexical inventory trigger')
