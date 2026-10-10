"""Private, bounded, quarantined model evidence. Never lexical/identity input.

Budgets include ASCII JSON serialization (and therefore UTF-8 bytes), not just
Python character counts. Model intervals inside captures are opaque evidence;
only trusted support bounds are rebased. No runtime logs/config/environment are
retained. A digest is of full observed source bytes, never an unread suffix.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

VERSION = 1
MAX_RECORD_BYTES = 128 * 1024
MAX_WINDOW_BYTES = 640 * 1024
MAX_RECORDS = 5  # four planned MOSS supports and one batch failure
MAX_PAGE_BYTES = 768 * 1024
RETENTIONS = {'complete', 'partial', 'unavailable'}
_MISSING = object()
# Defense in depth; this is not a general-purpose secret detector. Never capture
# ambient environment, config, logs, exception reprs or arbitrary metadata.
_SECRET = re.compile(r'(?i)(?:bearer\s+[^\s"\']+|(?:api[_-]?key|access[_-]?token|token|password|secret|authorization)["\']?\s*[:=]\s*["\']?[^\s,"\'}]+|\b(?:sk|hf)_[A-Za-z0-9_-]{12,}|\bsk-[A-Za-z0-9_-]{12,})')
_PATH = re.compile(r'(?:(?:[A-Za-z]:[\\/]|/)[^\s"\'<>]+)')


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':'), sort_keys=True).encode('utf-8')


def capture(value=_MISSING, *, budget=48 * 1024, json_value=False, observed=True,
            source_bytes=None, source_size=None, reason='not_emitted', scrub_paths=False):
    """Capture an exact string/JSON value up to a serialized-byte budget.

    JSON-valued evidence stays opaque text, including malformed numeric values.
    `source_bytes` is only for bounded artifact prefixes; `observed` must be false
    unless all bytes were read. Known file size is not proof of observation.
    """
    if source_bytes is None and value is _MISSING:
        return {'retention': 'unavailable', 'reason': reason, 'full_content_observed': False,
                'source_bytes': None, 'retained_bytes': 0, 'sha256': None, 'text': None,
                'redacted': False, 'truncated': False, 'format': 'json' if json_value else 'text'}
    issue = None
    if source_bytes is None:
        if not json_value and not isinstance(value, str):
            # Invalid raw-text shapes still contain observed evidence. Keep the
            # parsed value as opaque JSON instead of discarding it as absent.
            json_value = True
            issue = 'invalid_field_type'
        text = json.dumps(value, ensure_ascii=True, allow_nan=True, separators=(',', ':')) if json_value else value
        try:
            raw = text.encode('utf-8')
        except UnicodeEncodeError:
            # An unpaired surrogate is not valid UTF-8. Preserve its exact parsed
            # value as escaped JSON, hash that declared representation, and never
            # label it complete raw UTF-8 text.
            text = json.dumps(value, ensure_ascii=True, allow_nan=True, separators=(',', ':'))
            raw = text.encode('utf-8')
            json_value = True
            issue = 'invalid_unicode'
    else:
        raw = source_bytes
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            # Do not hide lossy replacement behind an "exact raw text" claim.
            text = raw.decode('utf-8', errors='replace')
    source_size = len(raw) if source_size is None else source_size
    clean = _SECRET.sub('[redacted credential]', text)
    if scrub_paths:
        clean = _PATH.sub('[redacted path]', clean)
    redacted = clean != text
    lossy = clean.encode('utf-8') != raw and not redacted
    item = {'retention': 'complete', 'reason': None, 'full_content_observed': bool(observed),
            'source_bytes': source_size, 'retained_bytes': 0,
            'sha256': hashlib.sha256(raw).hexdigest() if observed else None,
            'text': clean, 'redacted': redacted, 'truncated': not observed,
            'format': 'json' if json_value else 'text'}
    # Binary search over Unicode codepoints so an intact UTF-8 character never
    # becomes an accidental decoding error. All envelope overhead is counted.
    def update(length):
        item['text'] = clean[:length]
        item['retained_bytes'] = len(item['text'].encode('utf-8'))
        item['truncated'] = not observed or length < len(clean)
        partial = item['truncated'] or redacted or lossy or issue is not None
        item['retention'] = 'partial' if partial else 'complete'
        item['reason'] = ('privacy_redaction' if redacted else 'invalid_utf8' if lossy else
                          issue if issue is not None else 'byte_budget' if item['truncated'] else None)
    update(len(clean))
    if len(encoded(item)) > budget:
        low, high = 0, len(clean)
        while low < high:
            middle = (low + high + 1) // 2
            update(middle)
            if len(encoded(item)) <= budget: low = middle
            else: high = middle - 1
        update(low)
    if len(encoded(item)) > budget:
        raise ValueError('Diagnostic capture budget too small')
    return item


def artifact(path: Path, *, budget=48 * 1024):
    """A bounded read, without following symlinks or retaining ambient logs."""
    try:
        if path.is_symlink() or not path.is_file():
            return capture(reason='artifact_missing_or_unsafe')
        with path.open('rb') as stream:
            raw = stream.read(budget + 1)
            # Read at most budget+1; size alone never earns a full-content hash.
            size = path.stat().st_size
            observed = len(raw) <= budget and len(raw) == size
        return capture(source_bytes=raw[:budget], source_size=size, observed=observed,
                       budget=budget, scrub_paths=True)
    except OSError:
        return capture(reason='artifact_unreadable')


def _finish(item):
    states = [part['retention'] for part in item['captures'].values()]
    item['retention'] = ('unavailable' if all(x == 'unavailable' for x in states) else
                         'complete' if all(x == 'complete' for x in states) else 'partial')
    if len(encoded(item)) > MAX_RECORD_BYTES:
        raise ValueError('Diagnostic record exceeds byte budget')
    return item


def support(plan, group):
    """Keep failed/native-budget output quarantined before parse/projection."""
    metadata = group.get('metadata')
    failure = metadata.get('error',_MISSING) if isinstance(metadata, dict) else _MISSING
    return _finish({'version': VERSION, 'key': plan['shared_support_id'], 'kind': 'support',
        'shared_support_id': plan['shared_support_id'], 'support_start': plan['start'],
        'support_end': plan['end'], 'time_axis': 'analyzer_window',
        'evidence_time_axis': 'support_relative_unverified', 'candidate_only': True,
        'status': safe_code(group.get('status'), 'invalid_status'),
        'reason_code': safe_code(group.get('reason_code'), 'not_reported'),
        'captures': {
            'raw_text': capture(group.get('raw_text',_MISSING)),
            'schema_errors': capture(group.get('schema_errors',_MISSING), budget=12*1024, json_value=True),
            'diagnostic_phrases': capture(group.get('diagnostic_phrases',_MISSING), budget=24*1024, json_value=True),
            'emitted_phrases': capture(group.get('phrases',_MISSING), budget=24*1024, json_value=True),
            'failure': capture(failure, budget=4*1024, scrub_paths=True)}})


def safe_code(value, default):
    return value if isinstance(value, str) and 0 < len(value) <= 100 and re.fullmatch(r'[A-Za-z0-9_]+', value) else default


def batch_failure(plans, output, code, exception, *, associated=False, observed_result=None):
    # Exception messages can contain local paths, commands, credentials or full
    # untrusted reprs. Retain the safe class/code, not their arbitrary message.
    return _finish({'version': VERSION, 'key': 'batch-failure', 'kind': 'batch',
        'shared_support_id': None, 'support_start': min(p['start'] for p in plans),
        'support_end': max(p['end'] for p in plans), 'time_axis': 'analyzer_window',
        'evidence_time_axis': 'unassociated_raw_artifact', 'candidate_only': True,
        'status': 'unresolved', 'reason_code': safe_code(code, 'candidate_failure'),
        'captures': {'result_artifact': (capture(reason='already_retained_by_support') if associated else
                capture(source_bytes=observed_result,scrub_paths=True) if observed_result is not None else artifact(output)),
            'error_artifact': artifact(output.with_suffix('.error.json'), budget=4*1024),
            'failure': capture(type(exception).__name__, budget=4*1024)}})


def validate(rows, regions, *, owned=False):
    if not isinstance(rows, list) or len(rows) > MAX_RECORDS or len(encoded(rows)) > MAX_WINDOW_BYTES:
        raise ValueError('Diagnostic window exceeds byte/count budget')
    keys = set()
    supports = {}
    for region in regions:
        if region.get('shared_support_id'):
            supports.setdefault(region['shared_support_id'],[]).append(region)
    required = {'version','key','kind','shared_support_id','support_start','support_end','time_axis',
                'evidence_time_axis','candidate_only','status','reason_code','captures','retention'}
    capture_keys = {'retention','reason','full_content_observed','source_bytes','retained_bytes','sha256',
                    'text','redacted','truncated','format'}
    for row in rows:
        if not isinstance(row, dict) or set(row) != required or row['version'] != VERSION or len(encoded(row)) > MAX_RECORD_BYTES:
            raise ValueError('Invalid diagnostic envelope')
        key = row['key']
        if not isinstance(key, str) or not key or len(key) > 200 or key in keys:
            raise ValueError('Invalid duplicate diagnostic key')
        keys.add(key)
        if row['kind'] not in ('support','batch') or row['candidate_only'] is not True:
            raise ValueError('Diagnostic evidence must remain quarantined')
        if row['time_axis'] != ('core_relative' if owned else 'analyzer_window'):
            raise ValueError('Invalid diagnostic support axis')
        if row['evidence_time_axis'] != ('support_relative_unverified' if row['kind']=='support' else 'unassociated_raw_artifact'):
            raise ValueError('Invalid diagnostic evidence axis')
        if not all(type(row[k]) in (int,float) and math.isfinite(row[k]) for k in ('support_start','support_end')) or not 0 < row['support_end']-row['support_start'] <= 60.01:
            raise ValueError('Invalid diagnostic support bounds')
        if row['kind'] == 'support':
            linked = supports.get(row['shared_support_id'])
            if row['key'] != row['shared_support_id'] or not linked or any(abs(row[k]-region[k]) > .001 for region in linked for k in ('support_start','support_end')):
                raise ValueError('Orphan or mismatched diagnostic support')
        elif row['key'] != 'batch-failure' or row['shared_support_id'] is not None:
            raise ValueError('Invalid batch diagnostic association')
        if any(safe_code(row[k], None) is None for k in ('status','reason_code')):
            raise ValueError('Invalid diagnostic status')
        captures = row['captures']
        expected = {'raw_text','schema_errors','diagnostic_phrases','emitted_phrases','failure'} if row['kind']=='support' else {'result_artifact','error_artifact','failure'}
        if not isinstance(captures, dict) or set(captures) != expected:
            raise ValueError('Invalid diagnostic capture fields')
        for part in captures.values():
            if not isinstance(part, dict) or set(part) != capture_keys or part['retention'] not in RETENTIONS:
                raise ValueError('Invalid diagnostic capture')
            if any(type(part[k]) is not bool for k in ('full_content_observed','redacted','truncated')):
                raise ValueError('Invalid diagnostic capture flags')
            text = part['text']; size = part['source_bytes']; digest = part['sha256']
            if part['format'] not in ('text','json') or (part['reason'] is not None and safe_code(part['reason'], None) is None):
                raise ValueError('Invalid diagnostic capture format/reason')
            if type(part['retained_bytes']) is not int or part['retained_bytes'] != (len(text.encode('utf-8')) if isinstance(text,str) else 0):
                raise ValueError('Invalid diagnostic retained byte count')
            if part['retention']=='unavailable':
                if text is not None or size is not None or digest is not None or part['full_content_observed'] or not part['reason']:
                    raise ValueError('Unavailable diagnostic cannot claim observed content')
            else:
                if not isinstance(text,str) or type(size) is not int or size<0:
                    raise ValueError('Invalid diagnostic source size')
                if part['full_content_observed']:
                    if not isinstance(digest,str) or re.fullmatch('[0-9a-f]{64}',digest) is None:
                        raise ValueError('Missing full observed content hash')
                elif digest is not None or part['retention']=='complete':
                    raise ValueError('Unread output cannot have a full-content hash')
                if part['retention']=='complete':
                    if part['redacted'] or part['truncated'] or part['reason'] is not None or part['retained_bytes']!=size or hashlib.sha256(text.encode('utf-8')).hexdigest()!=digest:
                        raise ValueError('False complete diagnostic retention')
                elif not part['reason']:
                    raise ValueError('Partial diagnostic must explain missing content')
        states = [p['retention'] for p in captures.values()]
        expected_retention = 'unavailable' if all(x=='unavailable' for x in states) else 'complete' if all(x=='complete' for x in states) else 'partial'
        if row['retention'] != expected_retention:
            raise ValueError('False envelope retention status')
    return rows


def own(rows, start, end, regions):
    supports = {r.get('shared_support_id') for r in regions}
    result = []
    for row in rows:
        if row['kind']=='support' and row['shared_support_id'] not in supports:
            continue
        if min(end,row['support_end']) <= max(start,row['support_start']):
            continue
        result.append(dict(row, support_start=row['support_start']-start,
                           support_end=row['support_end']-start, time_axis='core_relative'))
    return result
