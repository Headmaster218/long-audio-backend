#!/usr/bin/env python3
"""Read-only, no-model MOSS support coverage audit of a frozen endurance run.

The default uses SQLite's online backup API. --consistent-snapshot means the
caller supplies a closed, consistent database without a live WAL. No source DB,
run metadata, service code, audio or model is written or loaded. Only an explicit
--output writes a report. Phrase-region links express SUPPORT association only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import contextlib
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile

TABLES = ('overlap_regions', 'overlap_tracks', 'overlap_track_regions')
SUCCESS_REASONS = {'support_not_model_timing', 'multi_speaker_quality_unverified',
                   'speech_support_probe_unverified'}
NOT_PLANNED = {'analysis_budget_exhausted', 'native_support_capacity_exceeded',
               'not_yet_attempted'}
MAX_JSON_BYTES = 32 * 1024 * 1024
TOLERANCE = 1 / 16000 + 1e-6


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    path = Path(path)
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError(f'JSON exceeds {MAX_JSON_BYTES} bytes: {path}')
    return json.loads(path.read_text(encoding='utf-8'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def rows(db, sql, values=(), batch_size=256):
    """Never fetch a whole candidate table into Python memory."""
    cursor = db.execute(sql, values)
    while batch := cursor.fetchmany(batch_size):
        yield from batch


def union(intervals):
    """Union finite half-open intervals; shared supports are counted once."""
    result = []
    for start, end in sorted(intervals):
        if not (math.isfinite(start) and math.isfinite(end)):
            raise ValueError('Nonfinite interval')
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def intersect(left, right):
    left, right = union(left), union(right)
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        start, end = max(left[i][0], right[j][0]), min(left[i][1], right[j][1])
        if end > start:
            result.append((start, end))
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return result


def seconds(intervals):
    return sum(end - start for start, end in intervals)


def reference_overlap(segments, duration):
    """Exact event sweep of >=2 distinct known annotation speakers.

    Duplicate/overlapping segments for one speaker do not add another speaker.
    No global-identity permutation or hypothesis-to-gold matching is performed.
    """
    events = defaultdict(Counter)
    skipped = Counter()
    for segment in segments:
        speaker = segment.get('speaker')
        if not isinstance(speaker, str) or not speaker.strip() or speaker.casefold() in {'unknown', 'null', 'none', 'unresolved'}:
            skipped['unknown_speaker_segments'] += 1
            continue
        try:
            start, end = float(segment['start']), float(segment['end'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('Invalid frozen-gold segment interval') from None
        if not (math.isfinite(start) and math.isfinite(end)):
            raise ValueError('Nonfinite frozen-gold interval')
        start, end = max(0., start), min(duration, end)
        if end <= start:
            skipped['nonpositive_or_outside_segments'] += 1
            continue
        events[start][speaker] += 1
        events[end][speaker] -= 1
    active = Counter()
    previous = None
    overlaps = []
    peak = 0
    for time, changes in sorted(events.items()):
        if previous is not None and time > previous and len(active) >= 2:
            overlaps.append((previous, time))
        active.update(changes)
        active = +active
        peak = max(peak, len(active))
        previous = time
    return union(overlaps), {'peak_distinct_annotation_speakers': peak, **dict(skipped)}


def classify_reason(reason):
    """Planning/output categories, never an inference-call counter."""
    if reason in SUCCESS_REASONS:
        return 'successful_format_status'
    if reason == 'analysis_budget_exhausted':
        return 'not_planned_analysis_budget'
    if reason == 'native_support_capacity_exceeded':
        return 'not_planned_native_capacity'
    if reason == 'not_yet_attempted':
        return 'not_yet_attempted'
    if reason in {'native_generation_budget_exhausted', 'candidate_native_generation_budget_exhausted'}:
        return 'unresolved_generation_budget'
    if 'timeout' in reason:
        return 'unresolved_timeout'
    if 'dependency' in reason or 'import' in reason:
        return 'unresolved_dependency'
    if 'silent' in reason:
        return 'unresolved_silent_input'
    return 'unresolved_other'


def readonly(path, immutable=False):
    suffix = '?mode=ro' + ('&immutable=1' if immutable else '')
    db = sqlite3.connect(Path(path).resolve().as_uri() + suffix, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA temp_store=FILE')
    db.execute('PRAGMA cache_size=-2048')
    return db


@contextlib.contextmanager
def consistent_database(path, directory, supplied=False, expected_sha256=None):
    path = Path(path).resolve(strict=True)
    if expected_sha256 and not supplied:
        raise ValueError('An expected database hash requires --consistent-snapshot')
    if supplied:
        for suffix in ('-wal', '-journal'):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists() and sidecar.stat().st_size:
                raise ValueError('Supplied snapshot has a WAL/journal; use online backup mode')
        snapshot = path
        mode = 'caller_asserted_consistent_closed_snapshot'
    else:
        snapshot = Path(directory) / 'consistent.sqlite3'
        with contextlib.closing(readonly(path)) as source, contextlib.closing(sqlite3.connect(snapshot)) as target:
            source.backup(target, pages=256, sleep=.02)
        mode = 'sqlite_online_backup_from_read_only_connection'
    before = sha256(snapshot)
    if expected_sha256 and before != expected_sha256.lower():
        raise ValueError('Supplied database SHA-256 mismatch')
    with contextlib.closing(readonly(snapshot, immutable=True)) as db:
        integrity = [row[0] for row in db.execute('PRAGMA quick_check')]
        if integrity != ['ok']:
            raise ValueError('Consistent database quick_check failed: ' + str(integrity[:5]))
        yield db, {'input_path': str(path), 'consistency_method': mode,
                   'snapshot_sha256': before, 'snapshot_bytes': snapshot.stat().st_size,
                   'expected_sha256': expected_sha256, 'expected_hash_verified': bool(expected_sha256),
                   'quick_check': 'ok',
                   'hash_scope': 'Exact consistent database bytes audited; not a hash of a changing live main file.'}
    if sha256(snapshot) != before:
        raise ValueError('Consistent database changed during audit')


def source_map(config):
    concat = config.get('concatenation')
    if not isinstance(concat, dict) or not concat.get('sources'):
        raise ValueError('Audit requires frozen concatenation.sources')
    lookup = {}
    for source in config['source_records']:
        key = (source['dataset'], source['recording_id'])
        if key in lookup:
            raise ValueError('Duplicate source_records identity')
        lookup[key] = source
    result = []
    previous = 0.
    for item in concat['sources']:
        key = (item['dataset'], item['recording_id'])
        if key not in lookup:
            raise ValueError('Concatenation source absent from source_records')
        record = lookup.pop(key)
        start, end = float(item['concatenated_start_seconds']), float(item['concatenated_end_seconds'])
        duration = float(record['duration_seconds'])
        if not all(math.isfinite(x) for x in (start, end, duration)) or end <= start or abs(start - previous) > TOLERANCE:
            raise ValueError('Invalid/noncontiguous concatenation source mapping')
        if abs(end - start - duration) > TOLERANCE or item.get('source_start_sample', 0) != 0:
            raise ValueError('Source mapping does not contain a complete original recording')
        if item.get('audio_sha256') != record.get('audio_sha256'):
            raise ValueError('Original audio digest differs between source maps')
        result.append({**record, 'key': canonical(key), 'concatenated_start_seconds': start,
                       'concatenated_end_seconds': end})
        previous = end
    if lookup or abs(previous - float(concat['duration_seconds'])) > TOLERANCE:
        raise ValueError('Incomplete concatenation source mapping')
    return result


def object_json(raw):
    try:
        value = json.loads(raw or '{}')
    except (TypeError, ValueError):
        return {}, False
    return (value, True) if isinstance(value, dict) else ({}, False)


def finite_interval(start, end):
    return (isinstance(start, (int, float)) and not isinstance(start, bool)
            and isinstance(end, (int, float)) and not isinstance(end, bool)
            and math.isfinite(start) and math.isfinite(end) and 0 <= start < end)


def initialize_work(db):
    db.executescript('''
      PRAGMA temp_store=FILE; PRAGMA cache_size=-2048;
      CREATE TABLE r(id INTEGER PRIMARY KEY,skey TEXT,sid TEXT,a REAL,b REAL,start REAL,end REAL,
                     kind TEXT,status TEXT,reason TEXT,category TEXT,native INTEGER,peak INTEGER,model INTEGER);
      CREATE TABLE t(id INTEGER PRIMARY KEY,skey TEXT,source_id TEXT,scope TEXT,label TEXT,
                     valid INTEGER,identity TEXT,source_identity TEXT,kind TEXT,quality TEXT,reason TEXT);
      CREATE TABLE l(track_id INTEGER,region_id INTEGER);
      CREATE TABLE phrase(skey TEXT,label TEXT,a REAL,b REAL,text TEXT,track_id INTEGER);
      CREATE TABLE issue(kind TEXT,example TEXT);
      CREATE TABLE s(skey TEXT PRIMARY KEY,sid TEXT,a REAL,b REAL,planned INTEGER,valid INTEGER,
                     unresolved INTEGER,boundary INTEGER,near_boundary INTEGER,owner TEXT,language TEXT,
                     labels INTEGER,native INTEGER,peak INTEGER,region_model INTEGER);
      CREATE INDEX r_support ON r(skey); CREATE INDEX t_support ON t(skey);
      CREATE INDEX l_region ON l(region_id); CREATE INDEX l_track ON l(track_id);
      CREATE INDEX phrase_support ON phrase(skey);
    ''')


def issue(db, kind, value):
    db.execute('INSERT INTO issue VALUES(?,?)', (kind, canonical(value)))


def load_candidates(source, work, chunk_id, batch_size):
    for row in rows(source, 'SELECT * FROM overlap_regions WHERE chunk_id=? ORDER BY id', (chunk_id,), batch_size):
        r = dict(row)
        sid = r.get('shared_support_id')
        if not isinstance(sid, str) or not sid:
            issue(work, 'missing_shared_support_id', r['id'])
            sid = None
        skey = canonical([chunk_id, r['window_start'], sid if sid else ['missing_region', r['id']]])
        activity, good = object_json(r.get('support_activity_json'))
        if not good:
            issue(work, 'malformed_support_activity', r['id'])
        if not finite_interval(r['support_start'], r['support_end']):
            raise ValueError(f'Invalid support interval in region {r["id"]}')
        if not finite_interval(r['start'], r['end']):
            raise ValueError(f'Invalid interval in region {r["id"]}')
        if r['start'] < r['support_start'] - .001 or r['end'] > r['support_end'] + .001:
            issue(work, 'region_outside_its_support', r['id'])
        reason = r.get('reason_code') or 'missing_reason'
        work.execute('INSERT INTO r VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (r['id'], skey, sid, r['support_start'], r['support_end'], r['start'], r['end'],
                      r.get('region_kind', 'missing_kind'), r['status'], reason, classify_reason(reason),
                      activity.get('distinct_local_speakers'), activity.get('max_simultaneous_speakers'),
                      r.get('model_label_count')))
    for row in rows(source, 'SELECT * FROM overlap_tracks WHERE chunk_id=? ORDER BY id', (chunk_id,), batch_size):
        t = dict(row)
        p, good = object_json(t.get('provenance_json'))
        region = work.execute('SELECT * FROM r WHERE id=?', (t['region_id'],)).fetchone()
        if region is None:
            issue(work, 'track_missing_primary_region', t['id'])
            skey = canonical([chunk_id, t['window_start'], ['orphan_track', t['id']]])
        else:
            skey = region['skey']
        valid = good and region is not None
        if not good:
            issue(work, 'malformed_track_provenance', t['id'])
        if region and (p.get('shared_support_id') != region['sid'] or t['support_start'] != region['a'] or t['support_end'] != region['b']):
            issue(work, 'track_support_identity_or_interval_mismatch', t['id'])
            valid = False
        if (not finite_interval(t.get('start'), t.get('end'))
                or not isinstance(t.get('text'), str) or not t['text'].strip()):
            issue(work, 'invalid_track_phrase_fields', t['id'])
            valid = False
        label = p.get('model_local_speaker_label')
        if not isinstance(label, str) or not label:
            issue(work, 'missing_model_local_label', t['id'])
            label = None
            valid = False
        native = p.get('native_phrase_intervals')
        if (p.get('association_basis') != 'support_not_model_timing'
                or p.get('native_time_axis') != 'session_absolute'
                or not isinstance(native, list) or not native or len(native) > 128):
            issue(work, 'missing_or_invalid_native_phrase_evidence', t['id'])
            valid = False
            native = []
        for unit in native:
            if (not isinstance(unit, dict) or not finite_interval(unit.get('start'), unit.get('end'))
                    or not isinstance(unit.get('text'), str) or not unit['text'].strip()
                    or unit.get('unit_kind') != 'native_phrase'):
                issue(work, 'invalid_native_phrase_unit', t['id'])
                valid = False
                continue
            if region and (unit['start'] < region['a'] - .001 or unit['end'] > region['b'] + .001):
                issue(work, 'native_phrase_outside_support', t['id'])
                valid = False
            # Exact same support/label/times/text is a duplicate key diagnostic.
            # Text recurring at different times is reported separately below.
            work.execute('INSERT INTO phrase VALUES(?,?,?,?,?,?)',
                         (skey, label, unit['start'], unit['end'], unit['text'], t['id']))
        source_identity = t.get('speaker_id')
        if source_identity is not None:
            issue(work, 'nonnull_candidate_source_identity', t['id'])
        if t.get('identity_status') != 'unresolved':
            issue(work, 'unexpected_candidate_identity_status', t['id'])
        work.execute('INSERT INTO t VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (t['id'], skey, t.get('source_track_id'), t.get('scope_id'), label, int(valid),
                      t.get('identity_status'), source_identity, p.get('native_unit_kind', 'missing_kind'),
                      t.get('quality_status'), t.get('reason_code')))
    for row in rows(source, '''SELECT l.track_id,l.region_id FROM overlap_track_regions l
                              LEFT JOIN overlap_tracks t ON t.id=l.track_id
                              LEFT JOIN overlap_regions r ON r.id=l.region_id
                              WHERE t.chunk_id=? OR r.chunk_id=?''', (chunk_id, chunk_id), batch_size):
        work.execute('INSERT INTO l VALUES(?,?)', tuple(row))
    for row in work.execute('''SELECT l.* FROM l LEFT JOIN t ON t.id=l.track_id LEFT JOIN r ON r.id=l.region_id
                               WHERE t.id IS NULL OR r.id IS NULL OR t.skey!=r.skey'''):
        issue(work, 'dangling_or_cross_support_link', dict(row))
    for row in work.execute('SELECT t.id FROM t LEFT JOIN l ON l.track_id=t.id WHERE l.track_id IS NULL'):
        issue(work, 'track_without_any_region_link', row['id'])
    work.commit()


def build_supports(db, sources, batch_size):
    boundaries = [s['concatenated_start_seconds'] for s in sources[1:]]
    for r in rows(db, '''SELECT skey,MIN(sid) sid,MIN(a) a,MAX(b) b,COUNT(DISTINCT a) na,
        COUNT(DISTINCT b) nb,MAX(native) native,MAX(peak) peak,MAX(model) model FROM r GROUP BY skey''', batch_size=batch_size):
        reasons = {row[0] for row in db.execute('SELECT DISTINCT reason FROM r WHERE skey=?', (r['skey'],))}
        track_counts = db.execute('SELECT COUNT(*),COALESCE(SUM(valid),0),COUNT(DISTINCT label) FROM t WHERE skey=?', (r['skey'],)).fetchone()
        format_ok = (track_counts[0] > 0 and track_counts[0] == track_counts[1]
                     and reasons <= SUCCESS_REASONS and r['na'] == r['nb'] == 1 and r['sid'] is not None)
        if r['na'] != 1 or r['nb'] != 1:
            issue(db, 'shared_support_has_conflicting_intervals', r['sid'])
        counts = db.execute('SELECT COUNT(DISTINCT native),COUNT(DISTINCT peak),COUNT(DISTINCT model) FROM r WHERE skey=?', (r['skey'],)).fetchone()
        if any(count > 1 for count in counts):
            issue(db, 'shared_support_has_conflicting_label_counts', r['sid'])
        if format_ok and r['model'] is not None and r['model'] != track_counts[2]:
            issue(db, 'reported_model_count_differs_from_distinct_labels', r['sid'])
        if reasons & SUCCESS_REASONS and not format_ok:
            issue(db, 'success_status_without_consistent_valid_phrases', r['sid'])
        if track_counts[0] and not reasons <= SUCCESS_REASONS:
            issue(db, 'phrases_on_unresolved_support', r['sid'])
        planned = any(reason not in NOT_PLANNED for reason in reasons)
        boundary = any(r['a'] < point < r['b'] for point in boundaries)
        near = any(r['a'] < point + 32 and r['b'] > point - 32 for point in boundaries)
        owners = [s for s in sources if r['a'] >= s['concatenated_start_seconds'] and r['b'] <= s['concatenated_end_seconds']]
        owner = owners[0] if len(owners) == 1 else None
        if not boundary and owner is None:
            issue(db, 'support_outside_source_mapping', r['sid'])
        db.execute('INSERT INTO s VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (r['skey'], r['sid'], r['a'], r['b'], int(planned), int(format_ok), int(not format_ok),
                    int(boundary), int(near), owner['key'] if owner else None,
                    owner['language'] if owner else None, track_counts[2], r['native'], r['peak'], r['model']))
    db.commit()


def distribution(db, sql, values=()):
    return [{**dict(row)} for row in db.execute(sql, values)]


def interval_query(db, where='1', values=()):
    """SQL sorts on disk; Python keeps only the merged interval union."""
    result = []
    for row in rows(db, 'SELECT a,b FROM s WHERE ' + where + ' ORDER BY a,b', values):
        a, b = row
        if result and a <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], b))
        else:
            result.append((a, b))
    return result


def bucket(db, where='1', values=()):
    support_count = db.execute('SELECT COUNT(*),COUNT(DISTINCT sid) FROM s WHERE ' + where, values).fetchone()
    selector = 'SELECT skey FROM s WHERE ' + where
    result = {'supports': support_count[0], 'distinct_stored_support_ids': support_count[1],
              'counts': {}, 'input_union_seconds': {}, 'input_union_semantics': 'Deduplicated support input extents, not candidate phrase timing.'}
    for table, alias in (('r', 'overlap_regions'), ('t', 'overlap_tracks')):
        result['counts'][alias] = db.execute(f'SELECT COUNT(*) FROM {table} WHERE skey IN ({selector})', values).fetchone()[0]
    result['counts']['overlap_track_regions'] = db.execute('SELECT COUNT(*) FROM l JOIN r ON r.id=l.region_id WHERE r.skey IN (' + selector + ')', values).fetchone()[0]
    for name, extra in (('all_observed', '1'), ('planned', 'planned=1'), ('valid_format', 'valid=1'),
                        ('unresolved', 'unresolved=1'), ('planned_unresolved', 'planned=1 AND unresolved=1'),
                        ('not_planned', 'planned=0')):
        cond = '(' + where + ') AND (' + extra + ')'
        result['input_union_seconds'][name] = seconds(interval_query(db, cond, values))
        result[name + '_support_count'] = db.execute('SELECT COUNT(*) FROM s WHERE ' + cond, values).fetchone()[0]
    result['region_reasons'] = distribution(db, 'SELECT kind,status,reason,category,COUNT(*) rows,COUNT(DISTINCT skey) supports FROM r WHERE skey IN (' + selector + ') GROUP BY kind,status,reason,category ORDER BY kind,status,reason', values)
    result['track_source_kinds'] = distribution(db, 'SELECT kind,quality,reason,COUNT(*) rows FROM t WHERE skey IN (' + selector + ') GROUP BY kind,quality,reason ORDER BY kind,quality,reason', values)
    result['identity_status_counts'] = distribution(db, 'SELECT identity,COUNT(*) rows FROM t WHERE skey IN (' + selector + ') GROUP BY identity ORDER BY identity', values)
    result['null_source_identity_rows'] = db.execute('SELECT COUNT(*) FROM t WHERE source_identity IS NULL AND skey IN (' + selector + ')', values).fetchone()[0]
    result['model_vs_native_label_counts'] = distribution(db, 'SELECT CASE WHEN valid=1 THEN labels END emitted_distinct_labels,labels stored_distinct_labels,valid valid_format,native native_distinct_labels,peak native_peak_simultaneous,region_model reported_model_labels,COUNT(*) supports FROM s WHERE ' + where + ' GROUP BY valid,labels,native,peak,region_model ORDER BY valid,labels,native,peak,region_model', values)
    digest = hashlib.sha256()
    for row in rows(db, 'SELECT skey FROM s WHERE ' + where + ' ORDER BY skey', values):
        digest.update((row[0] + '\n').encode())
    result['scoped_support_id_set_sha256'] = digest.hexdigest()
    return result


def duplicate_diagnostics(db):
    queries = {
        'duplicate_source_track_ids': 'SELECT source_id key,COUNT(*) rows FROM t WHERE source_id IS NOT NULL GROUP BY source_id HAVING COUNT(*)>1',
        'duplicate_exact_same_scope_native_phrase_keys': 'SELECT skey,label,a,b,text,COUNT(*) rows FROM phrase GROUP BY skey,label,a,b,text HAVING COUNT(*)>1',
        'repeated_same_scope_label_text_at_distinct_intervals': 'SELECT skey,label,text,SUM(n) rows,COUNT(*) intervals FROM (SELECT skey,label,text,a,b,COUNT(*) n FROM phrase GROUP BY skey,label,text,a,b) GROUP BY skey,label,text HAVING COUNT(*)>1',
        'duplicate_link_keys': 'SELECT track_id,region_id,COUNT(*) rows FROM l GROUP BY track_id,region_id HAVING COUNT(*)>1',
        'stored_support_id_reused_across_scopes': 'SELECT sid,COUNT(*) rows FROM s WHERE sid IS NOT NULL GROUP BY sid HAVING COUNT(*)>1',
    }
    result = {}
    for name, sql in queries.items():
        count = extras = 0
        examples = []
        for row in rows(db, sql):
            item = dict(row)
            count += 1
            extras += item['rows'] - 1
            # No transcript excerpt is required to diagnose a repeated key.
            if 'text' in item:
                item['text_sha256'] = hashlib.sha256(item.pop('text').encode()).hexdigest()
            if len(examples) < 20:
                examples.append(item)
        result[name] = {'groups': count, 'extra_rows_beyond_first': extras, 'examples_first_20': examples}
    result['interpretation'] = 'Exact-key collisions, repeated model text, and support links are separate diagnostics. No rows are rewritten; links are not duplicated phrases.'
    return result


def gold_exposure(run, config, source, db, processed):
    rel = Path(source['gold'])
    snapshot_root = (run / 'snapshot').resolve(strict=True)
    path = (snapshot_root / rel).resolve(strict=True)
    if rel.is_absolute() or not path.is_relative_to(snapshot_root):
        raise ValueError('Gold must be inside the frozen snapshot')
    expected = source.get('gold_sha256')
    inventory = config.get('source_sha256', {}).get(rel.as_posix())
    actual = sha256(path)
    if not expected or actual != expected or (inventory is not None and inventory != actual):
        raise ValueError(f'Frozen gold SHA-256 mismatch or missing expected hash: {rel}')
    gold = read_json(path)
    if gold.get('recording_id') != source['recording_id'] or gold.get('dataset') != source['dataset']:
        raise ValueError('Frozen gold source identity mismatch')
    duration = float(source['duration_seconds'])
    if abs(float(gold['duration_seconds']) - duration) > TOLERANCE:
        raise ValueError('Frozen gold duration mismatch')
    overlap, diagnostic = reference_overlap(gold['segments'], duration)
    if sha256(path) != actual:
        raise ValueError('Frozen gold changed while auditing')
    a, b = source['concatenated_start_seconds'], source['concatenated_end_seconds']
    complete = processed >= b - TOLERANCE
    absolute = [(lo + a, hi + a) for lo, hi in overlap]
    exposure = {}
    for name, condition in (('planned', 'planned=1'), ('valid_format', 'valid=1'), ('unresolved', 'unresolved=1')):
        for suffix, extra in (('including_boundary_crossing', '1'), ('source_contained', 'boundary=0'), ('boundary_crossing_only', 'boundary=1')):
            inputs = interval_query(db, f'({condition}) AND ({extra}) AND a<? AND b>?', (b, a))
            exposure[name + '_' + suffix + '_seconds'] = seconds(intersect(absolute, inputs))
    reference_seconds = seconds(overlap)
    return {'complete_original_source': complete,
            'assertion_status': 'final_for_complete_original' if complete else 'partial_observation_no_final_coverage_assertion',
            'observed_original_seconds': max(0., min(b, processed) - a),
            'gold': {'path': str(path), 'sha256': actual, 'expected_sha256': expected,
                     'verified': True, 'snapshot_inventory_hash_verified': inventory == actual,
                     'annotation_granularity': 'first_to_last_lexical_word_span_per_official_segment' if source['dataset'] == 'AMI' else 'annotated_turn_span',
                     'identity_scope': source.get('identity_group'), **diagnostic},
            'reference_overlap_whole_original_seconds': reference_seconds,
            'reference_overlap_in_observed_support_input': exposure,
            'final_reference_overlap_input_fraction': {
                name: (exposure[name + '_including_boundary_crossing_seconds'] / reference_seconds if reference_seconds else None)
                for name in ('planned', 'valid_format')} if complete else None,
            'meaning': 'Reference overlap included in support input, not recovered words, DER, verified phrase timing, acoustic quality, or speaker identity.'}


def aggregate_exposure(originals):
    """Aggregate only disjoint original timelines, preserving partial status."""
    result = {'original_recordings': len(originals), 'complete_original_recordings': 0,
              'reference_overlap_whole_original_seconds': 0.,
              'reference_overlap_in_observed_support_input': {},
              'complete_originals_only': {'reference_overlap_seconds': 0., 'exposure_seconds': {}}}
    for item in originals:
        evidence = item['reference_overlap_exposure']
        result['reference_overlap_whole_original_seconds'] += evidence['reference_overlap_whole_original_seconds']
        for name, value in evidence['reference_overlap_in_observed_support_input'].items():
            target = result['reference_overlap_in_observed_support_input']
            target[name] = target.get(name, 0.) + value
        if evidence['complete_original_source']:
            result['complete_original_recordings'] += 1
            complete = result['complete_originals_only']
            complete['reference_overlap_seconds'] += evidence['reference_overlap_whole_original_seconds']
            for name, value in evidence['reference_overlap_in_observed_support_input'].items():
                target = complete['exposure_seconds']
                target[name] = target.get(name, 0.) + value
    all_complete = result['complete_original_recordings'] == len(originals)
    denominator = result['reference_overlap_whole_original_seconds']
    result['final_reference_overlap_input_fraction'] = {
        kind: result['reference_overlap_in_observed_support_input'].get(kind + '_including_boundary_crossing_seconds', 0.) / denominator if denominator else None
        for kind in ('planned', 'valid_format')} if all_complete else None
    result['assertion_status'] = 'final_for_complete_originals' if all_complete else 'partial_observation_no_final_aggregate_fraction'
    return result


def audit(run_dir, database_path, *, consistent_snapshot=False, expected_database_sha256=None, batch_size=256):
    """Return a JSON-serializable report. Does not write into run_dir.

    Final assertions apply only to complete original source recordings according
    to the consistent database's committed chunks.next_offset. A partial run is
    explicitly labeled; no partial gold transcript is invented.
    """
    if not 1 <= batch_size <= 4096:
        raise ValueError('batch_size must be between 1 and 4096')
    run = Path(run_dir).resolve(strict=True)
    run_hash = sha256(run / 'run.json')
    config = read_json(run / 'run.json')
    sources = source_map(config)
    with tempfile.TemporaryDirectory(prefix='moss-candidate-audit-') as temporary:
        with consistent_database(database_path, temporary, consistent_snapshot, expected_database_sha256) as (source, evidence), contextlib.closing(sqlite3.connect(Path(temporary) / 'audit-work.sqlite3')) as work:
            work.row_factory = sqlite3.Row
            initialize_work(work)
            matches = source.execute('SELECT * FROM chunks WHERE digest=?', (config['concatenation']['audio_sha256'],)).fetchmany(2)
            if len(matches) != 1:
                raise ValueError('Expected exactly one chunk matching frozen concatenated audio SHA-256')
            chunk = dict(matches[0])
            if abs(float(chunk['timeline_start'])) > TOLERANCE or abs(float(chunk['duration']) - config['concatenation']['duration_seconds']) > TOLERANCE:
                raise ValueError('Database chunk timeline/duration does not match frozen concatenation')
            processed = float(chunk['next_offset'])
            if not math.isfinite(processed) or not 0 <= processed <= float(chunk['duration']) + TOLERANCE:
                raise ValueError('Invalid committed chunk offset')
            load_candidates(source, work, chunk['id'], batch_size)
            build_supports(work, sources, batch_size)
            for row in work.execute('SELECT sid,b FROM s WHERE b>?', (processed + TOLERANCE,)):
                issue(work, 'candidate_support_beyond_committed_offset', dict(row))
            full_counts = {table: source.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in TABLES}
            selected_counts = {
                'overlap_regions': work.execute('SELECT COUNT(*) FROM r').fetchone()[0],
                'overlap_tracks': work.execute('SELECT COUNT(*) FROM t').fetchone()[0],
                'overlap_track_regions': work.execute('SELECT COUNT(*) FROM l').fetchone()[0],
            }
            report = {'schema_version': 1, 'audit_kind': 'candidate_support_input_coverage_not_quality',
                      'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                      'run_id': config.get('run_id'), 'run_json_sha256': run_hash,
                      'evaluator': {'path': str(Path(__file__).resolve()), 'sha256': sha256(__file__)},
                      'database': evidence, 'database_all_chunk_counts': full_counts,
                      'selected_chunk_raw_counts': selected_counts,
                      'selected_chunk': {k: chunk.get(k) for k in ('id', 'digest', 'status', 'next_offset', 'duration', 'error')},
                      'overall': bucket(work),
                      'artificial_source_boundary_crossing': bucket(work, 'boundary=1'),
                      'within_32_seconds_of_artificial_boundary': bucket(work, 'near_boundary=1'),
                      'per_language_source_contained': {language: bucket(work, 'language=?', (language,)) for language in sorted({s['language'] for s in sources})},
                      'per_original_source': [], 'duplicates': duplicate_diagnostics(work)}
            for original in sources:
                report['per_original_source'].append({
                    'recording_id': original['recording_id'], 'dataset': original['dataset'], 'language': original['language'],
                    'concatenated_start_seconds': original['concatenated_start_seconds'],
                    'concatenated_end_seconds': original['concatenated_end_seconds'],
                    'source_contained_candidates': bucket(work, 'owner=?', (original['key'],)),
                    'reference_overlap_exposure': gold_exposure(run, config, original, work, processed)})
            report['reference_overlap_exposure'] = aggregate_exposure(report['per_original_source'])
            report['per_language_reference_overlap_exposure'] = {
                language: aggregate_exposure([item for item in report['per_original_source'] if item['language'] == language])
                for language in sorted({s['language'] for s in sources})}
            report['source_identity_storage'] = {
                'speaker_id_column_present': any(row['name'] == 'speaker_id' for row in source.execute('PRAGMA table_info(overlap_tracks)')),
                'meaning': 'Absent source identity columns are counted as null, as in the candidate public contract. Conditioning and anonymous model labels are not source identity assignments.'}
            for table, count in selected_counts.items():
                if count != report['overall']['counts'][table]:
                    issue(work, 'raw_rows_not_accounted_by_valid_support_mapping', {'table': table, 'raw': count, 'support_mapped': report['overall']['counts'][table]})
            completed = sum(item['reference_overlap_exposure']['complete_original_source'] for item in report['per_original_source'])
            report['completion'] = {'complete_original_recordings': completed, 'total_original_recordings': len(sources),
                                    'all_original_recordings_complete': completed == len(sources),
                                    'terminal_completed_chunk': chunk['status'] == 'completed',
                                    'status': 'final' if completed == len(sources) and chunk['status'] == 'completed' else 'partial'}
            report['diagnostics'] = []
            for row in work.execute('SELECT kind,COUNT(*) count FROM issue GROUP BY kind ORDER BY kind'):
                report['diagnostics'].append({**dict(row), 'examples_first_20': [json.loads(x[0]) for x in work.execute('SELECT example FROM issue WHERE kind=? LIMIT 20', (row['kind'],))]})
            report['semantics'] = [
                'Phrase-to-region links associate a phrase with the common support; they do not establish temporal phrase placement. One track may link to several regions.',
                'Shared supports are scoped by chunk, window and stored ID and counted once. Per-source/per-language row counts use wholly contained supports; cross-source supports are separate.',
                'Planned means a persisted reason is not a pre-inference planning rejection. It does not prove the model ran: timeouts, dependencies, silent inputs and worker failures may stop it earlier.',
                'Valid-format means consistent stored native phrase evidence and success reasons. It is not a correctness or speech-recovery claim. All raw failure reasons remain in the report.',
                'Unresolved supports have unknown emitted model label counts; stored-label counts are a separate ledger diagnostic. Model/native label-count differences do not measure recovered speakers.',
                'Null source identity is never mapped to gold. Model labels are support-local; persistent UUIDs and AliMeeting identities are never treated as verified global identities.',
                'AMI segment spans use first-to-last lexical word timing; AliMeeting uses turn timing. Reference overlap exposure is not directly comparable annotation resolution.',
                'Artificial source joins and the separate plus/minus 32-second diagnostic do not erase persistent diarization/identity/model state effects.',
                'Only complete original recordings receive final input-coverage fractions. Whole original frozen gold is retained; no truncated partial gold text is constructed.',
                'No audio/models loaded and no inference performed. Gold, run metadata and consistent database hashes are verified; live database bytes are never hashed as a consistent state.',
            ]
        report['database']['unchanged_during_audit'] = True
    if sha256(run / 'run.json') != run_hash:
        raise ValueError('run.json changed during audit')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('database_path', type=Path)
    parser.add_argument('--consistent-snapshot', action='store_true')
    parser.add_argument('--expected-database-sha256')
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--output', type=Path, help='Explicit new report path; refuses to overwrite any existing file')
    args = parser.parse_args(argv)
    report = audit(args.run_dir, args.database_path, consistent_snapshot=args.consistent_snapshot,
                   expected_database_sha256=args.expected_database_sha256, batch_size=args.batch_size)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(encoded)
    else:
        print(encoded, end='')


if __name__ == '__main__':
    main()
