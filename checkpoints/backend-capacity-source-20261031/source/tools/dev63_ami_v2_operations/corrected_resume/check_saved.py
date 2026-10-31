# SOURCE ARCHIVE ONLY: this sanitized file is not the executable tested bundle.
# ${WORKSPACE_ROOT} is an explicit deployment placeholder, not a literal path.
# Restore local configuration and regenerate all pins/releases before execution.
#!/usr/bin/env python3
"""Stopped, model-free conservation and passive dev56 readback.

Preparation does not execute this file. No labels, audio, model, application,
recovery, checkpoint or collector are opened by these helpers. The caller owns
positive-stop proof, output reservations, immutable pins and the runtime seal.
All paths saved in SQLite stay byte-for-byte unchanged.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import importlib.abc
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import sys
import time
from types import SimpleNamespace
from urllib.parse import quote

MiB = 1024 * 1024
MAX_DATABASE_BYTES = 128 * MiB
MAX_BACKUP_SECONDS = 45.0
BACKUP_PAGES = 64
MAX_TABLES = 256
MAX_COLUMNS = 128
MAX_ROWS = 262144
MAX_ROW_BYTES = 2 * MiB
MAX_SUMMARY_BYTES = 8 * MiB
MAX_DIRECT_BYTES = 256 * 1024
MAX_DIRECT_ATTEMPTS = 261
MAX_OBSERVATIONS = 31
MAX_WITNESSES = 868
MAX_TRANSCRIPT_PAGES = 8  # DURATION_FRAMES / 960000
MAX_EVIDENCE_REFS = 31
MAX_AUDIT_PAGES = 2
DURATION_FRAMES = 7680000
CAPTURE_ORIGIN = 100003
PAGE_BOUNDARIES = (0,4704000,7680000)
WORK_FRAMES = 168000
RATE = 16000
HISTORY_POLICY = 'primary-clean-history-v1'
HISTORY_POLICY_SHA256 = 'f9594ca075b108636430db8d8f9a3827f3a76588e5c4cde95193b8880fe7f322'
TRANSCRIPT_POLICY_SHA256 = 'ddbe0f3cf406ea4ef91d27706b542b14220dc2cee96591c5ea6e5781fe29edda'
H = 'recording_history_'
SCOPES = H + 'scopes_v1'
ATTEMPTS = H + 'attempts_v1'
CERTIFICATES = H + 'certificates_v1'
OBJECTS = H + 'objects_v1'
REFS = H + 'refs_v1'
DEPENDENCIES = H + 'dependencies_v1'
SOURCES = H + 'sources_v1'
DECISIONS = H + 'decisions_v1'
ROWS = H + 'decision_rows_v1'
PACKETS = 'recording_primary_packets_v1'


class CheckError(RuntimeError):
    """A bounded read was refused; no truncated positive result is returned."""


def _encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('ascii')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _check(deadline):
    if time.monotonic() >= deadline:
        raise CheckError('deadline_exhausted')


def _deadline(value, maximum):
    now = time.monotonic()
    if value is not None and (type(value) not in (float,int) or not math.isfinite(value)):
        raise CheckError('invalid_deadline')
    result = min(now + maximum, value) if value is not None else now + maximum
    _check(result)
    return result


def _q(name):
    if type(name) is not str or len(name) > 256 or '\x00' in name:
        raise CheckError('invalid_sql_identifier')
    return '"' + name.replace('"','""') + '"'


def _file_identity(path):
    st = Path(path).stat()
    if not Path(path).is_file() or Path(path).is_symlink():
        raise CheckError('regular_database_required')
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _readonly_authorizer(action, arg1, arg2, database, trigger):
    # All PRAGMAs are configured before installing this guard. Allow only the
    # bounded introspection queries this helper needs; never checkpoint or edit.
    if action == sqlite3.SQLITE_PRAGMA:
        return sqlite3.SQLITE_OK if arg1.lower() in {
            'page_count','page_size','table_info','database_list','user_version',
            'application_id','encoding','schema_version'} else sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                  sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@contextmanager
def readonly_database(path, deadline):
    path = Path(path).absolute()
    before = _file_identity(path)
    if before[2] > MAX_DATABASE_BYTES:
        raise CheckError('database_file_capacity')
    db = sqlite3.connect('file:' + quote(str(path),safe='/') + '?mode=ro',
                         uri=True, timeout=0)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA busy_timeout=0')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_ROW_BYTES)
        db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, MAX_COLUMNS)
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.set_authorizer(_readonly_authorizer)
        db.execute('BEGIN')
        pages = db.execute('PRAGMA page_count').fetchone()[0]
        size = db.execute('PRAGMA page_size').fetchone()[0]
        if type(pages) is not int or type(size) is not int or pages * size > MAX_DATABASE_BYTES:
            raise CheckError('logical_database_capacity')
        _check(deadline)
        yield db
        _check(deadline)
        if _file_identity(path) != before:
            raise CheckError('database_changed_during_read')
    finally:
        db.close()


def _typed(value):
    # Explicit type/length framing preserves NULL, REAL bits, UTF-8 TEXT and
    # BLOB bytes. No JSON normalization of durable values participates in hashes.
    if value is None:
        return b'N', b''
    if type(value) is int:
        return b'I', str(value).encode('ascii')
    if type(value) is float:
        return b'R', struct.pack('>d',value)
    if type(value) is str:
        return b'T', value.encode('utf-8')
    if type(value) is bytes:
        return b'B', value
    raise CheckError('unknown_sqlite_value_type')


def _hash_rows(db, table, deadline):
    columns = list(db.execute('PRAGMA table_info(' + _q(table) + ')'))
    if not columns or len(columns) > MAX_COLUMNS:
        raise CheckError('table_columns_capacity')
    names = [r['name'] for r in columns]
    pk = [r['name'] for r in sorted(columns,key=lambda r:r['pk']) if r['pk']]
    order = ','.join(_q(x) for x in pk) if pk else 'rowid'
    # Scalar pre-admission occurs before a single payload row is copied.
    lengths = '+'.join('COALESCE(length(CAST(' + _q(x) + ' AS BLOB)),0)' for x in names)
    count, largest = db.execute('SELECT COUNT(*),COALESCE(MAX(' + lengths + '),0) FROM ' + _q(table)).fetchone()
    if count > MAX_ROWS or largest > MAX_ROW_BYTES:
        raise CheckError('durable_table_capacity:' + table)
    hasher = hashlib.sha256()
    hasher.update(_encoded({'columns':names,'order':pk or ['rowid'],'framing':'typed-u64be-v1'}))
    total = 0
    cursor = db.execute('SELECT ' + ','.join(_q(x) for x in names) + ' FROM ' + _q(table) + ' ORDER BY ' + order)
    actual = 0
    for row in cursor:
        _check(deadline)
        hasher.update(b'ROW' + struct.pack('>I',len(row)))
        for value in row:
            kind, raw = _typed(value)
            total += len(raw)
            if total > MAX_DATABASE_BYTES:
                raise CheckError('durable_payload_capacity:' + table)
            hasher.update(kind + struct.pack('>Q',len(raw)))
            for start in range(0,len(raw),65536):
                _check(deadline)
                hasher.update(raw[start:start+65536])
        actual += 1
    if actual != count:
        raise CheckError('durable_row_count_changed')
    return {'rows':count,'payload_bytes':total,'sha256':hasher.hexdigest(),
            'columns':names,'order':pk or ['rowid']}


def _conservation(db, deadline):
    schema = [tuple(r) for r in db.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name,tbl_name')]
    if len(schema) > 4096:
        raise CheckError('schema_capacity')
    tables = [r[1] for r in schema if r[0] == 'table']
    if len(tables) > MAX_TABLES:
        raise CheckError('table_count_capacity')
    values = {name:_hash_rows(db,name,deadline) for name in tables}
    payload = sum(x['payload_bytes'] for x in values.values())
    if payload > MAX_DATABASE_BYTES:
        raise CheckError('all_durable_payload_capacity')
    metadata = {key:db.execute('PRAGMA ' + key).fetchone()[0]
                for key in ('user_version','application_id','encoding')}
    result = {'schema_sha256':_sha(_encoded(schema)), 'tables':values,
              'metadata':metadata,'payload_bytes':payload,
              'coverage':'all_tables_all_columns_all_rows_including_sqlite_sequence'}
    result['sha256'] = _sha(_encoded(result))
    return result


def conservation_snapshot(path, *, deadline=None):
    """Read/hash all durable tables with exact payload bytes, no mutation."""
    deadline = _deadline(deadline, MAX_BACKUP_SECONDS)
    with readonly_database(path,deadline) as db:
        return _conservation(db,deadline)


def backup_database(source, dest, deadline, max_bytes=MAX_DATABASE_BYTES):
    """One read-only-source SQLite backup; bounded progress, full conservation.

    Requires caller's positive original-handle shutdown proof. A partial failed
    snapshot is preserved; this function does not retry, unlink, or checkpoint.
    The caller pre-reserves max_bytes plus terminal/output headroom.
    """
    deadline = _deadline(deadline, MAX_BACKUP_SECONDS)
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_DATABASE_BYTES:
        raise CheckError('invalid_snapshot_allowance')
    source, dest = Path(source).absolute(), Path(dest).absolute()
    if source == dest or dest.exists():
        raise CheckError('fresh_snapshot_destination_required')
    if not dest.parent.is_dir():
        raise CheckError('snapshot_parent_missing')
    before = _file_identity(source)
    progress_steps = 0
    started = time.monotonic()
    with readonly_database(source,deadline) as original:
        page_count = original.execute('PRAGMA page_count').fetchone()[0]
        page_size = original.execute('PRAGMA page_size').fetchone()[0]
        if page_count * page_size > max_bytes:
            raise CheckError('snapshot_reservation_exceeded')
        conserved_before = _conservation(original,deadline)
        # O_EXCL reserves exactly this destination; source is never reopened RW.
        descriptor = os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(descriptor)
        target = sqlite3.connect(str(dest),timeout=0)
        try:
            target.execute('PRAGMA busy_timeout=0')
            def progress(status, remaining, total):
                nonlocal progress_steps
                progress_steps += 1
                _check(deadline)
                if total * page_size > max_bytes or dest.stat().st_size > max_bytes:
                    raise CheckError('snapshot_capacity_exceeded')
                if status not in (sqlite3.SQLITE_OK, sqlite3.SQLITE_DONE):
                    raise CheckError('snapshot_busy_or_error:' + str(status))
            original.backup(target,pages=BACKUP_PAGES,progress=progress,sleep=0)
        finally:
            target.close()
        _check(deadline)
        # The source's read transaction remains open through the comparison.
        with readonly_database(dest,deadline) as copied:
            conserved_after = _conservation(copied,deadline)
        if conserved_before != conserved_after:
            raise CheckError('snapshot_durable_conservation_failed')
    if _file_identity(source) != before:
        raise CheckError('source_identity_changed_after_backup')
    return {'source':str(source),'snapshot':str(dest),'bytes':dest.stat().st_size,
            'pages_per_step':BACKUP_PAGES,'progress_steps':progress_steps,
            'elapsed_seconds':time.monotonic()-started,'read_only_source':True,
            'paths_preserved':True,'checkpoint_performed':False,
            'durable_conserved':True,'conservation':conserved_after}


def _json(raw, maximum, label):
    if type(raw) not in (str,bytes) or len(raw) > maximum:
        raise CheckError('payload_capacity:' + label)
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result:
                raise CheckError('duplicate_json_key:' + label)
            result[key] = value
        return result
    try:
        return json.loads(raw,object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(CheckError('nonfinite_json:' + label)))
    except (ValueError,UnicodeError,RecursionError) as exc:
        raise CheckError('invalid_json:' + label) from exc


def _jsonable(value):
    if type(value) is bytes:
        return {'encoding':'base64','bytes':len(value),'sha256':_sha(value),
                'data':base64.b64encode(value).decode('ascii')}
    if isinstance(value,sqlite3.Row):
        value = dict(value)
    if type(value) is dict:
        return {key:_jsonable(item) for key,item in value.items()}
    if type(value) in (tuple,list):
        return [_jsonable(item) for item in value]
    return value


def _rows(db, query, args, cap, deadline):
    """Bound rows before payload selection, then reject rather than truncate."""
    _check(deadline)
    result = []
    size = 0
    for row in db.execute(query,args):
        if len(result) >= cap:
            raise CheckError('summary_row_capacity')
        size += sum(len(_typed(v)[1]) for v in row)
        if size > MAX_SUMMARY_BYTES:
            raise CheckError('summary_payload_capacity')
        result.append(dict(row))
        _check(deadline)
    return result


def _scope_rows(db,table,scope_seq,cap,deadline):
    return _rows(db,'SELECT * FROM ' + _q(table) + ' WHERE scope_seq=?',
                 (scope_seq,),cap,deadline)


def _packet_witnesses(packet,digest):
    """Exact _namespace/_witnesses semantics; no product imports or comparison."""
    labels, turns = packet['labels'], packet['turns']
    if len(labels) > 8 or len(turns) > 64:
        raise CheckError('standalone_packet_shape')
    commit = packet['commit']
    namespace = {'schema':'recording-primary-native-capture-v2','scope':commit['scope'],
                 'runtime_sha256':commit['runtime_sha256'],'query':packet['query']}
    scope = _sha(_encoded(namespace))
    found = {}
    for i, one in enumerate(turns):
        for two in turns[i+1:]:
            pair = tuple(sorted((labels[one[1]],labels[two[1]])))
            a,b = max(one[2],two[2]), min(one[3],two[3])
            if pair[0] == pair[1] or a >= b:
                continue
            if pair not in found or (a,b) < found[pair]:
                found[pair] = (a,b)
    return [{'native_scope_id':scope,'labels':list(pair),'overlap_frames':list(found[pair]),
             'proof_sha256':digest} for pair in sorted(found)]


def _covered(a,b,pieces):
    cursor = a
    for lo,hi in sorted(pieces):
        if hi <= cursor:
            continue
        if lo > cursor:
            return False
        cursor = hi
        if cursor >= b:
            return True
    return False


def _mapped_crop(header, packet, work, ranges, sources):
    result, audit = [], []
    spans = header.get('spans')
    if type(spans) is not list or not 1 <= len(spans) <= 4:
        raise CheckError('crop_span_shape')
    if any(type(span) is not list or len(span)!=4 for span in spans):
        raise CheckError('crop_span_shape')
    source_key=_sha(_encoded({'schema':'exact-source-crop-v1','parts':[
        dict(zip(('source_sha256','source_frames','source_start','source_end'),span)) for span in spans]}))
    if source_key!=header.get('source_key'):
        raise CheckError('crop_source_key_changed')
    part = packet['commit']['part']
    for span in spans:
        if type(span) is not list or len(span) != 4:
            raise CheckError('crop_span_shape')
        digest,frames,a,b = span
        if not all(type(x) is int for x in (frames,a,b)) or not 0 <= a < b <= frames:
            raise CheckError('crop_span_geometry')
        cid = part['chunk_id']
        item = work.get(cid)
        if item is None or (item['digest'],item['frames']) != (digest,frames):
            raise CheckError('crop_work_binding')
        if (part['source_sha256'],part['source_frames']) != (digest,frames):
            raise CheckError('crop_packet_binding')
        absolute_a = item['capture_start_frame'] + a
        absolute_b = item['capture_start_frame'] + b
        supporting = []
        for row in ranges:
            if row['chunk_id'] != cid:
                continue
            src = sources.get(row['source_id'])
            if src is None or row['relation'] not in ('owner','exact_alias'):
                raise CheckError('crop_original_source_missing')
            if (src['source_start_frame'] + row['source_start_frame'] != row['capture_start_frame']
                or item['capture_start_frame'] + row['work_start_frame'] != row['capture_start_frame']
                or not 0 <= row['source_start_frame'] < row['source_start_frame'] + row['frames'] <= src['frames']
                or not 0 <= row['work_start_frame'] < row['work_start_frame'] + row['frames'] <= frames):
                raise CheckError('crop_original_mapping_changed')
            lo,hi = row['capture_start_frame'],row['capture_start_frame'] + row['frames']
            if max(lo,absolute_a) < min(hi,absolute_b):
                supporting.append((lo,hi))
                audit.append({'range':dict(row),'source':dict(src)})
        if not _covered(absolute_a,absolute_b,supporting):
            raise CheckError('crop_original_mapping_incomplete')
        rel = [absolute_a-CAPTURE_ORIGIN,absolute_b-CAPTURE_ORIGIN]
        if not 0 <= rel[0] < rel[1] <= DURATION_FRAMES:
            raise CheckError('crop_outside_fixed_audio')
        result.append(rel)
    if sum(b-a for a,b in result) != 32000:
        raise CheckError('natural_crop_not_32000_frames')
    return result,audit


def _adapter(observations,witnesses):
    """Map saved crop geometry and witnesses, without deriving live authority.

    This is a reference-opportunity adapter, never a second dependency validator
    or current-decision reader. Only captured production-current annotations may
    establish operational links or policy eligibility in a later bounded step.
    """
    rows = []
    parent = list(range(len(observations)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for j,one in enumerate(observations):
        for i,two in enumerate(observations[:j]):
            a,b = one['header'],two['header']
            if a['space_id'] == b['space_id'] and (a['source_key']==b['source_key'] or a['pcm_hash']==b['pcm_hash']):
                parent[root(j)] = root(i)
    groups = {}
    for i,one in enumerate(observations):
        groups.setdefault(root(i),[]).append(one)
    bad_aliases = set()
    for index,group in groups.items():
        exemplar = group[0]['header']
        vectors = [x['_vector'] for x in group if x['_vector'] is not None]
        if any((x['header']['pcm_hash'],x['header']['float_hash']) != (exemplar['pcm_hash'],exemplar['float_hash']) for x in group):
            bad_aliases.add(index)
        if vectors and any(struct.unpack('<512f',v) != struct.unpack('<512f',vectors[0]) for v in vectors[1:]):
            bad_aliases.add(index)
    keys = {(w['native_scope_id'],tuple(w['labels'])) for w in witnesses}
    # Preserve ALL native input rows, even duplicate rows and rows for which
    # neither native label has a retained candidate. Pair enumeration is not
    # an input-witness cap and must never replace this stream.
    mapped_witnesses=[]
    for witness in witnesses:
        sides=[]
        for label in witness['labels']:
            sides.append(sorted(x['observation_id'] for x in observations
                if [witness['native_scope_id'],label] in x['header']['attributions']))
        key=_sha(_encoded({k:witness[k] for k in
            ('native_scope_id','labels','overlap_frames','proof_sha256')}))
        mapped_witnesses.append({'left':sides[0],'right':sides[1],'key':key})
    for index,group in groups.items():
        attrs=[a for row in group for a in row['header']['attributions']]
        if any(a[0]==b[0] and (a[0],tuple(sorted((a[1],b[1])))) in keys
               for a in attrs for b in attrs):
            bad_aliases.add(index)
    for i,one in enumerate(observations):
        rows.append({'id':one['observation_id'],'alias':min(x['observation_id'] for x in groups[root(i)]),
            'space':one['header']['space_id'],'ranges':one['ranges'],
            'vector_present':one['vector_present'],
            'eligible':None,
            'dependencies':[],'cannot_links':[]})
    # Dependencies and cannot-links propagate over complete physical alias sets.
    for j,one in enumerate(observations):
        for i,two in enumerate(observations[:j]):
            if root(i) == root(j):
                continue
            ga,gb = groups[root(i)],groups[root(j)]
            overlap = any(a[0:2]==b[0:2] and max(a[2],b[2]) < min(a[3],b[3])
                for x in ga for y in gb for a in x['header']['spans'] for b in y['header']['spans'])
            cannot = any(a[0]==b[0] and (a[0],tuple(sorted((a[1],b[1])))) in keys
                for x in ga for y in gb for a in x['header']['attributions'] for b in y['header']['attributions'])
            if overlap:
                rows[i]['dependencies'].append(rows[j]['id']); rows[j]['dependencies'].append(rows[i]['id'])
            if cannot:
                rows[i]['cannot_links'].append(rows[j]['id']); rows[j]['cannot_links'].append(rows[i]['id'])
    return {'observations':rows,'links':None,'witnesses':mapped_witnesses,
            'input_witness_rows':len(witnesses),'published_links_available':False,
            'policy_eligibility_available':False,
            'authority_unavailable_reason':'no_bound_production_current_annotations',
            'alias_invalid_groups':len(bad_aliases),
            'mapping_unavailable':[x['observation_id'] for x in observations if not x['mapping_available']]}


def bind_public_links(summary,snapshots):
    """Bind complete public production annotations; never infer SQL authority."""
    label=None
    def unavailable(reason):
        return {'links':None,'available':False,'reason':reason,'snapshot_label':label,
                'policy_eligibility_available':False}
    if type(summary) is not dict: return unavailable('conserved_summary_invalid')
    if type(snapshots) is not list or not 1<=len(snapshots)<=4: return unavailable('no_bounded_final_snapshot')
    final=snapshots[-1]; label=final.get('label') if type(final) is dict else None
    if label not in ('final-quiescence','final-cutoff') or final.get('complete') is not True or final.get('complete_meeting') is not True:
        return unavailable('final_snapshot_incomplete')
    if final.get('annotation_disagreement') is True: return unavailable('public_annotations_disagree')
    history=summary.get('history',{})
    if type(history) is not dict: return unavailable('conserved_history_invalid')
    scope=history.get('scope'); observations=history.get('observations')
    if type(scope) is not dict or type(observations) is not list or not 1<=len(observations)<=31:
        return unavailable('no_bounded_natural_observations')
    revision=scope.get('history_revision')
    if scope.get('policy')!=HISTORY_POLICY or type(revision) is not int: return unavailable('conserved_scope_invalid')
    refs={}; ids=set()
    for row in observations:
        if type(row) is not dict or type(row.get('header')) is not dict: return unavailable('conserved_observation_invalid')
        ref=row.get('header',{}).get('ref_key'); ident=row.get('observation_id')
        if type(ref) is not str or type(ident) is not str or re.fullmatch(r'[a-f0-9]{64}',ident) is None or ident in ids:
            return unavailable('conserved_observation_invalid')
        ids.add(ident); refs.setdefault(ref,[]).append(ident)
    pages=final.get('pages'); annotations={}; transcript_revision=None
    if type(pages) is not list or len(pages)!=MAX_TRANSCRIPT_PAGES: return unavailable('final_pages_incomplete')
    for block,page in enumerate(pages):
        if type(page) is not dict: return unavailable('invalid_public_page')
        if block==0: transcript_revision=page.get('revision')
        if (page.get('block')!=block or page.get('session_id')!=summary.get('session_id')
            or page.get('policy_sha256')!=TRANSCRIPT_POLICY_SHA256 or page.get('revision')!=transcript_revision
            or type(transcript_revision) is not str or re.fullmatch(r'[a-f0-9]{64}',transcript_revision) is None):
            return unavailable('public_page_binding_changed')
        public=page.get('recording_speaker_links',{})
        if type(public) is not dict or type(public.get('annotation_read')) is not dict: return unavailable('public_scope_incomplete')
        scopes=public.get('scopes',[])
        if public.get('annotation_read',{}).get('complete') is not True or type(scopes) is not list or len(scopes)!=1:
            return unavailable('public_scope_incomplete')
        current=scopes[0]
        if (type(current) is not dict or current.get('history_status')!='current' or current.get('state')!='source_scoped'
            or current.get('recording_id')!=scope.get('recording_id') or current.get('scope_id')!=scope.get('scope_id')
            or current.get('policy_sha256')!=HISTORY_POLICY_SHA256 or current.get('space_id')!=scope.get('space_id')
            or current.get('authority_revision')!=scope.get('authority_revision')
            or any(type(current.get(k)) is not int or current[k]!=revision for k in ('revision','decision_revision'))):
            return unavailable('public_scope_not_current_or_not_conserved')
        observed=page.get('primary_annotations')
        if type(observed) is not dict or len(observed)>31: return unavailable('public_annotation_capacity')
        for ref,annotation in observed.items():
            if type(annotation) is not dict or len(_encoded(annotation))>4096: return unavailable('public_annotation_shape')
            if ref in annotations and annotations[ref]!=annotation: return unavailable('public_annotations_disagree')
            annotations[ref]=annotation
            if len(annotations)>31: return unavailable('public_annotation_capacity')
    if not set(refs)<=set(annotations): return unavailable('natural_reference_annotation_missing')
    groups={}
    for ref,members in refs.items():
        a=annotations[ref]; status=a.get('status'); ident=a.get('provisional_speaker_id')
        if (a.get('primary_source_ref')!=ref or a.get('source_kind')!='native_primary' or a.get('scope')!='recording'
            or a.get('history_status')!='current' or a.get('policy_sha256')!=HISTORY_POLICY_SHA256
            or a.get('space_id')!=scope.get('space_id') or a.get('speaker_identity_valid') is not False
            or a.get('profile_update_allowed') is not False or a.get('reversible') is not True
            or any(type(a.get(k)) is not int or a[k]!=revision for k in ('revision','decision_revision'))
            or status not in ('provisional_linked','source_scoped','probe_unavailable') or a.get('reason')=='primary_observations_disagree'
            or status=='provisional_linked' and (type(ident) is not str or re.fullmatch(r'recording-speaker-[a-f0-9]{20}',ident) is None)
            or status!='provisional_linked' and ident is not None): return unavailable('public_annotation_not_authoritative')
        if status=='provisional_linked': groups.setdefault(ident,[]).extend(members)
    links=[{'left':left,'right':right} for members in groups.values()
           for i,right in enumerate(sorted(members)) for left in sorted(members)[:i]]
    return {'links':links,'available':True,'reason':'bound_complete_current_production_annotations',
            'snapshot_label':label,'history_revision':revision,'reference_count':len(refs),
            'observation_count':len(ids),'policy_eligibility_available':False}


def _validate_witness_ranges(attempts,total):
    """Empty admissions own no stream rows; positive slices tile the stream."""
    if type(total) is not int or not 0<=total<=MAX_WITNESSES:
        raise CheckError('history_witness_ranges')
    positive=[]
    for attempt in attempts:
        start,count=attempt['witness_start'],attempt['witness_count']
        if (type(start) is not int or type(count) is not int
            or not 0<=start<=total or not 0<=count<=total-start):
            raise CheckError('history_witness_ranges')
        if count: positive.append(attempt)
    cursor=0
    for attempt in sorted(positive,key=lambda x:(x['witness_start'],x['token'])):
        if attempt['witness_start']!=cursor: raise CheckError('history_witness_ranges')
        cursor+=attempt['witness_count']
    if cursor!=total: raise CheckError('history_witness_ranges')


def summarize_database(path,sid,capture,*,deadline=None):
    """Bounded raw durable summary and reference-adapter inputs; no scoring."""
    deadline = _deadline(deadline,45.0)
    failures = []
    with readonly_database(path,deadline) as db:
        sessions = _rows(db,'SELECT * FROM sessions WHERE id=?',(sid,),2,deadline)
        if len(sessions)!=1 or sessions[0]['id']!=sid or sessions[0]['deleted']:
            raise CheckError('fresh_single_session_scope')
        batches = _rows(db,'SELECT * FROM recording_batches WHERE session_id=? AND capture_id=?',(sid,capture),2,deadline)
        if len(batches)!=1 or (batches[0]['session_id'],batches[0]['capture_id'])!=(sid,capture):
            raise CheckError('fresh_single_capture_scope')
        batch=batches[0]; bid=batch['id']
        pages=_rows(db,'SELECT * FROM recording_pages WHERE batch_id=? ORDER BY page_number',(bid,),7,deadline)
        sources=_rows(db,'SELECT * FROM recording_sources WHERE batch_id=? ORDER BY seq',(bid,),17,deadline)
        work=_rows(db,'''SELECT c.*,w.batch_id,w.position,w.frames,w.bytes,w.capture_start_frame,w.page_id,
            w.native_receipt_status,w.native_receipt,w.receipt_id,w.receipt_state,w.receipt_attempts,w.file_signature
            FROM recording_work w JOIN chunks c ON c.id=w.chunk_id WHERE w.batch_id=? ORDER BY w.position''',(bid,),151,deadline)
        ranges=_rows(db,'SELECT * FROM recording_ranges WHERE batch_id=? ORDER BY capture_start_frame,source_id',(bid,),5472,deadline)
        expected_work=[(i*WORK_FRAMES,min((i+1)*WORK_FRAMES,DURATION_FRAMES)) for i in range(46)]
        actual_work=[(x['capture_start_frame']-CAPTURE_ORIGIN,x['capture_start_frame']-CAPTURE_ORIGIN+x['frames']) for x in work]
        if actual_work!=expected_work: failures.append('work_full_range_geometry')
        if len(work)!=46: failures.append('work_count_not_46')
        if any(x['status']!='completed' or x['next_offset']!=x['duration'] or x['duration']!=x['frames']/RATE for x in work):
            failures.append('work_completion_or_next_offset')
        if [x['page_number'] for x in pages]!=list(range(1,3)): failures.append('two_pages_required')
        if [(x['start_frame']-CAPTURE_ORIGIN,x['end_frame']-CAPTURE_ORIGIN if x['end_frame'] is not None else None) for x in pages]!=list(zip(PAGE_BOUNDARIES,PAGE_BOUNDARIES[1:])):
            failures.append('page_full_range_geometry')
        if any(x['released']!=1 or x['state']!='published' or x['eos']!=(i==1) for i,x in enumerate(pages)):
            failures.append('page_publication_or_eos')
        if len(sources)!=5: failures.append('source_count_not_5')
        if (batch['capture_origin_frame'],batch['capture_end_frame'],batch['eos'])!=(CAPTURE_ORIGIN,CAPTURE_ORIGIN+DURATION_FRAMES,1):
            failures.append('capture_geometry_or_eos')
        # Independent accepted-source coverage, with exact uploader split lengths.
        expected_sources=[]
        for start,end in zip(PAGE_BOUNDARIES,PAGE_BOUNDARIES[1:]):
            cursor=start
            while cursor<end:
                frames=min(120*RATE,end-cursor); expected_sources.append((cursor,frames)); cursor+=frames
        if [(s['source_start_frame']-CAPTURE_ORIGIN,s['frames']) for s in sources]!=expected_sources:
            failures.append('accepted_source_full_range_geometry')
        if db.execute('SELECT COUNT(*) FROM chunks WHERE session_id=?',(sid,)).fetchone()[0]!=len(work): failures.append('extra_chunk_scope')
        moss_counts={table:db.execute('SELECT COUNT(*) FROM '+_q(table)).fetchone()[0]
            for table in ('moss_retained_results_v1','moss_automatic_intents_v1','recording_speaker_probe_batches_v1')}
        if any(moss_counts.values()): failures.append('unexpected_moss_durable_records')
        scopes=_rows(db,'SELECT * FROM '+SCOPES+' WHERE scope_seq>0 AND session_id=? AND recording_id=?',(sid,bid),2,deadline)
        if len(scopes)>1 or any((s['session_id'],s['recording_id'],s['policy'])!=(sid,bid,HISTORY_POLICY) for s in scopes):
            raise CheckError('history_single_scope_invariant')
        scope=scopes[0] if scopes else None
        attempts=[]; certificates=[]; objects=[]; refs=[]; dependencies=[]; history_sources=[]; decisions=[]; decision_rows=[]
        if scope:
            seq=scope['scope_seq']
            attempts=_scope_rows(db,ATTEMPTS,seq,31,deadline)
            certificates=_scope_rows(db,CERTIFICATES,seq,31,deadline)
            objects=_scope_rows(db,OBJECTS,seq,62,deadline)
            refs=_scope_rows(db,REFS,seq,62,deadline)
            dependencies=_scope_rows(db,DEPENDENCIES,seq,4030,deadline)
            history_sources=_scope_rows(db,SOURCES,seq,31,deadline)
            decisions=_scope_rows(db,DECISIONS,seq,2,deadline)
            decision_rows=_scope_rows(db,ROWS,seq,184,deadline)
            if scope['observation_count']!=len(certificates) or scope['vector_count']!=sum(c['evidence_kind']=='vector' for c in certificates):
                raise CheckError('history_count_conservation')
            if scope['observation_count']>31 or scope['vector_count']>31 or scope['witness_count']>868:
                raise CheckError('history_observation_or_witness_bound')
            if len(attempts)!=len(certificates) or any(
                sum(c['attempt']==a['token'] for c in certificates)!=1 for a in attempts):
                raise CheckError('one_observation_per_primary_attempt')
            if scope['source_count']!=len(history_sources):
                raise CheckError('history_source_count_conservation')
            if scope['active_sources']!=sum(s['state']=='active' for s in history_sources):
                raise CheckError('history_active_source_count_conservation')
        for item in attempts:
            if item['origin_kind']!='primary_commit': raise CheckError('nonprimary_history_attempt')
            item['decoded_basis']=_json(item['basis'],512,'attempt_basis') if item['basis'] else None
            item['decoded_terminal']=_json(item['terminal'],512,'attempt_terminal') if item['terminal'] else None
            if item['terminal'] is not None and _sha(item['terminal'])!=item['terminal_sha256']:
                raise CheckError('attempt_terminal_hash')
        # Reconstruct every input witness row per admission, including repeats.
        witnesses=[]; packets={}; packet_audit=[]
        _validate_witness_ranges(attempts,scope['witness_count'] if scope else 0)
        for attempt in sorted(attempts,key=lambda x:(x['witness_start'],x['token'])):
            packet_refs=[r for r in refs if r['attempt']==attempt['token'] and r['kind']=='packet']
            if len(packet_refs)!=1: raise CheckError('primary_packet_ref_count')
            ref=packet_refs[0]
            saved=db.execute('SELECT * FROM '+PACKETS+' WHERE sequence=?',(ref['object_key'],)).fetchone()
            if saved is None or saved['digest']!=ref['digest'] or _sha(saved['body'])!=saved['digest']:
                raise CheckError('history_packet_missing_or_changed')
            packet=_json(saved['body'],65536,'primary_packet')
            if (saved['sid'],saved['rid'])!=(sid,bid): raise CheckError('history_packet_scope')
            basis=attempt['decoded_basis']
            if not basis or basis.get('kind')!='primary_commit' or basis.get('packet_digests')!=[saved['digest']]:
                raise CheckError('history_basis_packet_binding')
            derived=_packet_witnesses(packet,saved['digest'])
            if len(derived)!=attempt['witness_count'] or basis.get('witness_count')!=len(derived):
                raise CheckError('history_witness_count_changed')
            if derived and attempt['witness_start']!=len(witnesses): raise CheckError('history_witness_ranges')
            witness_start=len(witnesses)
            witnesses.extend(dict(w,attempt=attempt['token'],input_ordinal=witness_start+i) for i,w in enumerate(derived))
            packets[saved['digest']]=packet
            packet_audit.append({'attempt':attempt['token'],'saved':_jsonable(saved),'decoded_packet':packet})
        if len(witnesses)>868 or scope and len(witnesses)!=scope['witness_count']:
            raise CheckError('input_witness_conservation')
        work_by_id={x['id']:x for x in work}; source_by_id={x['id']:x for x in sources}
        observations=[]
        attempts_by_token={a['token']:a for a in attempts}
        for cert in sorted(certificates,key=lambda x:x['sequence']):
            header=_json(cert['header'],2048,'certificate_header')
            if header.get('origin_kind')!='primary_commit' or header.get('ref_kind')!='primary':
                raise CheckError('nonprimary_certificate')
            if header.get('space_id')!=scope['space_id']:
                raise CheckError('certificate_embedding_space_changed')
            vector=cert['vector']; present=cert['evidence_kind']=='vector'
            attempt=attempts_by_token.get(cert['attempt'])
            if attempt is None or attempt['basis'] is None:
                raise CheckError('certificate_attempt_basis_missing')
            if cert['observation_id']!=_sha(_encoded([cert['attempt'],_sha(attempt['basis']),_sha(cert['header'])])):
                raise CheckError('observation_identity_changed')
            if cert['invocation_id']!=attempt['invocation_id']:
                raise CheckError('observation_invocation_changed')
            expected_kind={'reserved':'pending','success':'vector','failure':'candidate_missing'}.get(attempt['state'])
            if cert['evidence_kind']!=expected_kind:
                raise CheckError('certificate_terminal_state_disagreement')
            if present and (type(vector) is not bytes or len(vector)!=2048 or _sha(vector)!=cert['vector_sha256']):
                raise CheckError('certificate_vector_hash')
            if present and cert['certificate_id']!=_sha(_encoded([
                cert['observation_id'],cert['invocation_id'],cert['vector_sha256']])):
                raise CheckError('certificate_identity_changed')
            if not present and vector is not None: raise CheckError('candidate_missing_has_vector')
            if present and not all(math.isfinite(v) for v in struct.unpack('<512f',vector)):
                raise CheckError('certificate_nonfinite_vector')
            packet=packets.get(header.get('packet'))
            if packet is None: raise CheckError('certificate_packet_missing')
            row_index=header.get('row')
            if type(row_index) is not int or not 0<=row_index<len(packet['refs']) or packet['refs'][row_index][0]!=header['ref_key']:
                raise CheckError('certificate_primary_ref_binding')
            bound=[d for d in dependencies if d['attempt']==cert['attempt']]
            cid=packet['commit']['part']['chunk_id']
            if ([d['dependency_key'] for d in bound if d['kind']=='source']!=[cid]
                or [d['dependency_key'] for d in bound if d['kind']=='mapping']!=[cid]
                or {d['dependency_key'] for d in bound if d['kind']=='primary'}!={ref[0].split('.')[1] for ref in packet['refs']}
                or not any(s['chunk_id']==cid for s in history_sources)):
                raise CheckError('certificate_dependency_set_incomplete')
            mapping_error=None
            try: crop_ranges,crop_audit=_mapped_crop(header,packet,work_by_id,ranges,source_by_id)
            except CheckError as exc: crop_ranges=[];crop_audit=[];mapping_error=str(exc)
            observations.append({**_jsonable(cert),'header':header,'raw_header':_jsonable(cert['header']),
                'vector_present':present,'ranges':crop_ranges,'mapping_available':mapping_error is None,
                'mapping_error':mapping_error,'original_source_mapping':crop_audit,
                'policy_eligibility':None,'dependency_authority_checked':False,'_vector':vector})
        adapter=_adapter(observations,witnesses)
        for item in observations: item.pop('_vector')
        unknown_rival_ownership_clear=bool(scope and scope['state']=='unknown_rival' and scope['reserved_bytes']==0
            and scope['staging_bytes']==0 and scope['current_slot'] is None and scope['building_slot'] is None
            and scope['build_token'] is None and not any(a['state']=='reserved' for a in attempts))
        if scope and (scope['reserved_bytes'] or scope['staging_bytes'] or scope['building_slot'] is not None or scope['build_token'] is not None or any(a['state']=='reserved' for a in attempts)):
            failures.append('stopped_history_ownership_remains')
        result={'session_id':sid,'capture_id':capture,'database':str(Path(path).absolute()),
            'coverage_complete':not any(x for x in failures if x!='stopped_history_ownership_remains'),
            'invariant_failures':failures,'duration_frames':DURATION_FRAMES,'origin_frame':CAPTURE_ORIGIN,
            'session':sessions[0],'batch':batch,'pages':pages,'sources':sources,'work_chunks':_jsonable(work),
            'recording_ranges':ranges,'moss_durable_counts':moss_counts,
            'history':{'scope':_jsonable(scope),'attempts':_jsonable(attempts),'observations':observations,
                'objects':_jsonable(objects),'refs':refs,'dependencies':_jsonable(dependencies),
                'sources':_jsonable(history_sources),'decisions':_jsonable(decisions),
                'decision_rows':_jsonable(decision_rows),'packets':packet_audit,'input_witnesses':witnesses,
                'unknown_rival_ownership_clear':unknown_rival_ownership_clear,
                'history_status_interpretation':'production_passive_reader_only',
                'identity_exercised':bool(observations),
                'claim_count':len(attempts),'skipped_count':scope['skipped_count'] if scope else 0,
                'admission_count':len(observations),
                'observation_count':len(observations),'vector_count':sum(x['vector_present'] for x in observations),
                'candidate_missing_count':sum(x['evidence_kind']=='candidate_missing' for x in observations),
                'pending_count':sum(x['evidence_kind']=='pending' for x in observations),
                'input_witness_count':len(witnesses)},
            'identity_inputs':adapter,
            'identity_scoring_available':not adapter['mapping_unavailable'] and not adapter['alias_invalid_groups'],
            'retained_refs':sorted({x['header']['ref_key'] for x in observations}),
            'semantics':{'reference_labels_read':False,'audio_read':False,'comparison_executed':False,
                'missing_rivals_preserved':True,'saved_paths_preserved':True,
                'dependency_authority_checked':False,'published_links_interpreted':False,
                'physical_probe_dispatches':'not_inferred_from_sql_claims',
                'fresh_boot_historical_projection_is_not_restart_loss':True}}
        result['global_primary_segment_count']=db.execute('SELECT COUNT(*) FROM segments').fetchone()[0]
        result['global_primary_packet_count']=db.execute('SELECT COUNT(*) FROM '+PACKETS).fetchone()[0]
        if len(_encoded(result))>MAX_SUMMARY_BYTES: raise CheckError('summary_serialized_capacity')
        _check(deadline)
        return result


def minimal_read_holder(snapshot):
    # TranscriptStore has no start_worker constructor parameter. The minimal
    # holder explicitly disables workers; no real Settings or Store is created.
    return SimpleNamespace(path=Path(snapshot),start_worker=False,
        settings=SimpleNamespace(start_worker=False,recording_speaker_links_enabled=False,
                                 recording_speaker_history_read_enabled=True))


_FORBIDDEN_IMPORTS = ('audio_backend.storage','audio_backend.app','audio_backend.__main__',
    'audio_backend.worker','audio_backend.engine','audio_backend.models','audio_backend.backends',
    'audio_backend.speaker','audio_backend.diarization','audio_backend.asr',
    'audio_backend.moss_jobs','audio_backend.native_owned_cell_jobs',
    'audio_backend.recording_primary_coordinator','torch','onnxruntime','transformers',
    'sherpa_onnx','funasr','numpy','scipy','subprocess','multiprocessing')


def _forbidden_import(name):
    return any(name==prefix or name.startswith(prefix+'.') for prefix in _FORBIDDEN_IMPORTS)


class PassiveGuard(importlib.abc.MetaPathFinder):
    """Runtime negative guards, scoped to a fresh passive interpreter.

    Definitions of CellStore are unavoidable TranscriptStore dependencies;
    construction is forbidden. Audit hooks are inert outside this context.
    These are defense-in-depth guards, not a host sandbox or peak-memory claim.
    """
    def __init__(self,snapshot,output_dir,*,require_fresh=True):
        self.snapshot=Path(snapshot).absolute()
        self.output_dir=Path(output_dir).absolute()
        self.require_fresh=require_fresh
        self.active=False
        self.denied=[]
        self.previous_profile=None

    def deny(self,reason):
        self.denied.append(reason)
        raise CheckError('passive_guard:' + reason)

    def find_spec(self,fullname,path=None,target=None):
        if self.active and _forbidden_import(fullname):
            self.deny('import:' + fullname)
        return None

    def _profile(self,frame,event,arg):
        if not self.active:
            return
        if event=='c_call' and getattr(arg,'__name__','') in ('start_new_thread','fork','forkpty','posix_spawn','posix_spawnp','system','execv','execve','execvp','execvpe'):
            self.deny('child_or_thread_dispatch')
        if event!='call':
            return
        name=frame.f_code.co_name
        module=frame.f_globals.get('__name__','')
        if module=='threading' and name=='start':
            self.deny('thread_start')
        if module.startswith('audio_backend.'):
            obj=frame.f_locals.get('self')
            cls=type(obj).__name__ if obj is not None else ''
            if name=='__init__' and cls!='TranscriptStore' and (
                cls.endswith('Store') or any(x in cls for x in ('Worker','Manager','Coordinator','Model','Jobs','Authority'))):
                self.deny('live_constructor:' + module + '.' + cls)
            if name in ('init_schema','recover','startup','start_worker','collect','_collect','_collect_receipts','_collect_primary_cache','claim_primary','begin_comparison','append_primary','extract_pcm'):
                self.deny('live_operation:' + module + '.' + name)

    def _audit(self,event,args):
        if not self.active:
            return
        if event.startswith(('subprocess.','os.exec','os.spawn','os.posix_spawn','os.fork')) or event in (
            'os.system','socket.connect','socket.bind','socket.getaddrinfo','ctypes.dlopen'):
            self.deny('dispatch:' + event)
        if event=='sqlite3.connect':
            expected='file:' + quote(str(self.snapshot),safe='/') + '?mode=ro'
            if args[0]!=expected:
                self.deny('non_snapshot_or_non_readonly_sqlite_connect')
        if event=='open' and args and isinstance(args[0],(str,bytes,os.PathLike)):
            name=os.fsdecode(args[0]); lower=name.lower()
            if lower.endswith(('.wav','.audio','.pcm','.onnx','.gguf','.textgrid','.textgrid.partial')):
                self.deny('audio_model_or_reference_open')
            mode=args[1] if len(args)>1 else None
            flags=args[2] if len(args)>2 else 0
            writing=(isinstance(mode,str) and any(x in mode for x in 'wax+')) or (
                isinstance(flags,int) and bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC)))
            if writing:
                path=Path(name).absolute()
                if path!=self.output_dir and self.output_dir not in path.parents:
                    self.deny('write_outside_passive_evidence')

    def __enter__(self):
        existing=[name for name in sys.modules if name.startswith('audio_backend')]
        if self.require_fresh and existing:
            raise CheckError('fresh_interpreter_required')
        if any(_forbidden_import(name) for name in existing):
            raise CheckError('live_product_import_present')
        self.previous_profile=sys.getprofile()
        sys.addaudithook(self._audit)
        sys.meta_path.insert(0,self)
        self.active=True
        sys.setprofile(self._profile)
        return self

    def __exit__(self,kind,value,traceback):
        self.active=False
        sys.setprofile(self.previous_profile)
        if self in sys.meta_path:
            sys.meta_path.remove(self)


class DirectCalls:
    """Exact direct bytes and typed errors, never manufactured HTTP responses."""
    def __init__(self,reader,read_error,recorder,deadline):
        self.reader=reader; self.read_error=read_error; self.recorder=recorder
        self.deadline=deadline; self.attempts=0; self.records=[]

    def call(self,method,sid,**kwargs):
        target=method + ':' + sid + ':' + _encoded(kwargs).decode('ascii')
        for attempt in range(3):
            _check(self.deadline)
            if self.deadline-time.monotonic()<.75:
                raise CheckError('insufficient_remaining_reader_window')
            if self.attempts>=MAX_DIRECT_ATTEMPTS:
                raise CheckError('direct_attempt_capacity')
            self.recorder.admit(MAX_DIRECT_BYTES)
            self.attempts+=1
            started=time.monotonic()
            try:
                raw=getattr(self.reader,method)(sid,**kwargs)
            except self.read_error as exc:
                error={'transport':'direct_method','type':type(exc).__module__+'.'+type(exc).__qualname__,
                       'status_code':exc.status_code,'reason':exc.reason,
                       'started_monotonic':started,'ended_monotonic':time.monotonic()}
                ledger=self.recorder.record('direct_method',target,'raised',b'',error=error)
                self.records.append({'attempt':self.attempts,'method':method,'ledger':ledger,'typed_error':error})
                if exc.status_code==503 and attempt<2:
                    delay=attempt+1
                    if time.monotonic()+delay>=self.deadline:
                        return None,error
                    time.sleep(delay)
                    continue
                return None,error
            except Exception as exc:
                error={'transport':'direct_method','type':type(exc).__module__+'.'+type(exc).__qualname__,
                       'reason':str(exc),'started_monotonic':started,'ended_monotonic':time.monotonic()}
                ledger=self.recorder.record('direct_method',target,'raised',b'',error=error)
                self.records.append({'attempt':self.attempts,'method':method,'ledger':ledger,'untyped_error':error})
                raise
            if type(raw) is not bytes or len(raw)>MAX_DIRECT_BYTES:
                error={'type':'direct_return_contract','returned_type':type(raw).__name__,
                       'bytes':len(raw) if isinstance(raw,(bytes,str)) else None,
                       'sha256':_sha(raw) if type(raw) is bytes else None}
                self.recorder.record('direct_method',target,'contract_error',b'',error=error)
                raise CheckError('direct_method_return_capacity_or_type')
            ledger=self.recorder.record('direct_method',target,'returned',raw,error=None)
            self.records.append({'attempt':self.attempts,'method':method,'ledger':ledger,
                'bytes':len(raw),'sha256':_sha(raw),'started_monotonic':started,'ended_monotonic':time.monotonic()})
            _check(self.deadline)
            return _json(raw,MAX_DIRECT_BYTES,'direct_method_return'),None
        raise AssertionError('three_total_attempts_loop')


def _cursor(token,sid,policy,revision,block):
    if type(token) is not str or not 1<=len(token)<=512:
        raise CheckError('transcript_cursor_shape')
    try:
        raw=base64.b64decode(token+'='*(-len(token)%4),altchars=b'-_',validate=True)
        value=_json(raw,384,'cursor')
    except (ValueError,UnicodeError) as exc:
        raise CheckError('transcript_cursor_encoding') from exc
    if value!={'policy':policy,'session':sid,'block':block,'revision':revision}:
        raise CheckError('transcript_cursor_binding')


def passive_read(snapshot,sid,output_dir,deadline,*,recorder=None,decoded_start=0,retained_refs=None):
    """Fresh interpreter's bounded installed TranscriptStore method reads.

    One pass normally; only a typed 409 permits a second pass. Every returned
    page and every failed attempt is retained, including abandoned passes.
    """
    deadline=_deadline(deadline,105.0)
    snapshot=Path(snapshot).absolute(); output_dir=Path(output_dir).absolute()
    if not output_dir.is_dir():
        raise CheckError('passive_output_directory_required')
    if recorder is None:
        from fixed_trial import EvidenceRecorder
        # 16 MiB exact evidence plus 8 MiB summary and 4 MiB terminal report
        # leave 4 MiB for driver seals/logs/receipts in the parent's pre-reserved
        # 32 MiB passive pool. Exhaustion is terminal, never truncation.
        recorder=EvidenceRecorder(output_dir/'evidence',decoded_start=decoded_start,
                                  evidence_limit=16*MiB)
    if retained_refs is None:
        with readonly_database(snapshot,deadline) as db:
            certs=_rows(db,'SELECT c.header FROM '+CERTIFICATES+' c JOIN '+SCOPES+
                ' s ON s.scope_seq=c.scope_seq WHERE s.session_id=?',(sid,),31,deadline)
            retained_refs=[_json(c['header'],2048,'certificate_header')['ref_key'] for c in certs]
    unique_refs=sorted(set(retained_refs))
    if any(type(ref) is not str or len(ref)>192 for ref in unique_refs):
        raise CheckError('natural_probe_ref_shape')
    selected=unique_refs[:MAX_EVIDENCE_REFS]
    omitted=unique_refs[MAX_EVIDENCE_REFS:]
    sys.dont_write_bytecode=True
    before=_file_identity(snapshot)
    passes=[]; evidence=[]; audits=[]; observed_scopes={}; restart_used=False
    with PassiveGuard(snapshot,output_dir) as guard:
        from audio_backend.transcript_store import TranscriptStore, READ_SECONDS, MAX_VM_STEPS
        from audio_backend.native_owned_support_cache import ReadError
        from audio_backend.transcript_contract import POLICY_HASH, BLOCK_FRAMES, MAX_RESPONSE_BYTES
        installed='${WORKSPACE_ROOT}/dev63-package01-20261020/output/installed'
        installed=Path(installed)
        actual=Path(sys.modules[TranscriptStore.__module__].__file__).absolute()
        if actual!=installed/'audio_backend'/'transcript_store.py':
            raise CheckError('passive_reader_not_fixed_installed_source')
        if READ_SECONDS!=.75 or MAX_VM_STEPS!=250000 or MAX_RESPONSE_BYTES!=MAX_DIRECT_BYTES or BLOCK_FRAMES!=960000:
            raise CheckError('installed_passive_reader_limits_changed')
        reader=TranscriptStore(minimal_read_holder(snapshot))
        if not reader.speaker_history_read_enabled or reader.legacy_speaker_links_enabled:
            raise CheckError('passive_history_settings_changed')
        calls=DirectCalls(reader,ReadError,recorder,deadline)
        for pass_index in range(2):
            report={'pass':pass_index,'pages':[],'complete':False,'abandoned':False,'error':None}
            passes.append(report)
            cursor=None; revision=None; restart=False
            for block in range(MAX_TRANSCRIPT_PAGES):
                kwargs={'projection':'whole-primary-v1'}
                if cursor is not None: kwargs['cursor']=cursor
                value,error=calls.call('page',sid,**kwargs)
                if error is not None:
                    report['error']=error
                    if error.get('status_code')==409 and not restart_used:
                        restart_used=True;restart=True;report['abandoned']=True
                    break
                if type(value) is not dict or value.get('session_id')!=sid or value.get('policy_sha256')!=POLICY_HASH or value.get('block')!=block:
                    raise CheckError('passive_page_session_policy_or_order')
                if revision is None: revision=value.get('revision')
                if revision is None or value.get('revision')!=revision:
                    raise CheckError('passive_page_revision_disagreement')
                annotations=value.get('recording_speaker_links')
                annotation_complete=bool(type(annotations) is dict and type(annotations.get('annotation_read')) is dict
                    and annotations['annotation_read'].get('complete') is True)
                next_cursor=value.get('next_cursor')
                if 'next_cursor' not in value:
                    raise CheckError('passive_page_cursor_missing')
                if next_cursor is not None:
                    _cursor(next_cursor,sid,POLICY_HASH,revision,block+1)
                if type(annotations) is dict:
                    for scope in annotations.get('scopes',[]):
                        observed_scopes[_encoded(scope).decode('ascii')]=scope
                report['pages'].append({'block':block,'revision':revision,'annotation_complete':annotation_complete,
                    'next_cursor':next_cursor,'complete_content':value.get('complete_content'),'eos':value.get('eos')})
                cursor=next_cursor
                if cursor is None:
                    report['complete']=(block==MAX_TRANSCRIPT_PAGES-1 and all(p['annotation_complete'] for p in report['pages']))
                    if block!=MAX_TRANSCRIPT_PAGES-1: report['error']={'type':'incomplete_meeting_extent','last_block':block}
                    elif not report['complete']: report['error']={'type':'incomplete_annotation_read'}
                    break
                if block==MAX_TRANSCRIPT_PAGES-1: report['error']={'type':'nonterminating_transcript_cursor'}
            if not restart:
                break
        for ref in selected:
            value,error=calls.call('evidence',sid,ref=ref)
            evidence.append({'ref':ref,'returned':error is None,'error':error,
                'source_ref_matches':bool(value is not None and value.get('source_ref')==ref)})
        cursor=None; audit_revision=None; audit_after=-1
        for page in range(MAX_AUDIT_PAGES):
            kwargs={} if cursor is None else {'cursor':cursor}
            value,error=calls.call('binding_audit',sid,**kwargs)
            if error is not None:
                audits.append({'page':page,'error':error,'complete':False});break
            if value.get('session_id')!=sid or value.get('policy_sha256')!=POLICY_HASH:
                raise CheckError('binding_audit_session_policy')
            if audit_revision is None: audit_revision=value.get('revision')
            if audit_revision is None or value.get('revision')!=audit_revision:
                raise CheckError('binding_audit_revision_changed')
            if 'next_cursor' not in value or type(value.get('last_examined_seq')) is not int or value['last_examined_seq']<=audit_after:
                raise CheckError('binding_audit_cursor_progress')
            audit_after=value['last_examined_seq']
            cursor=value.get('next_cursor')
            audits.append({'page':page,'examined':value.get('examined'),'has_more':value.get('has_more'),
                'next_cursor':cursor,'unplaced_bindings':value.get('unplaced_bindings'),
                'complete':cursor is None and value.get('has_more') is False})
            if cursor is None: break
        negative_guards={'active':True,'denied':list(guard.denied),'store_constructed':False,
            'app_started':False,'model_loaded':False,'children_dispatched':False,
            'start_worker':False,'reader_seconds':READ_SECONDS,'reader_vm_steps':MAX_VM_STEPS}
    if _file_identity(snapshot)!=before:
        raise CheckError('snapshot_changed_by_passive_read')
    return {'session_id':sid,'snapshot':str(snapshot),'transport':'direct_installed_methods',
        'attempts':calls.attempts,'attempt_limit':MAX_DIRECT_ATTEMPTS,'records':calls.records,
        'passes':passes,'complete_traversal':bool(passes and passes[-1]['complete']),
        'restart_used':restart_used,'evidence_refs':evidence,'omitted_refs':omitted,
        'evidence_complete':not omitted and all(x['returned'] and x['source_ref_matches'] for x in evidence),
        'binding_audit':audits,'binding_audit_complete':bool(audits and audits[-1]['complete']),
        'observed_history_scopes':list(observed_scopes.values()),'guards':negative_guards,
        'decoded_total':recorder.decoded,'reference_labels_read':False,
        'fresh_boot_projection_note':'Active-source current decisions may project historical; durable conservation, not current labeling alone, establishes retention.'}


def _write_json(path,value,maximum):
    raw=_encoded(value)+b'\n'
    if len(raw)>maximum: raise CheckError('terminal_json_capacity')
    with open(path,'xb') as handle:
        handle.write(raw)


def _sealed_summary(path,digest,snapshot,sid,capture,deadline):
    """Consume the parent's sealed selection; never recompute it in readback."""
    path=Path(path).absolute()
    before=_file_identity(path)
    if before[2]>MAX_SUMMARY_BYTES:
        raise CheckError('sealed_summary_capacity')
    _check(deadline)
    blocks=[]; total=0
    with path.open('rb') as handle:
        while True:
            _check(deadline)
            block=handle.read(min(65536,MAX_SUMMARY_BYTES-total+1))
            if not block: break
            blocks.append(block); total+=len(block)
            if total>MAX_SUMMARY_BYTES: raise CheckError('sealed_summary_capacity')
    raw=b''.join(blocks)
    if len(raw)>MAX_SUMMARY_BYTES or _sha(raw)!=digest or _file_identity(path)!=before:
        raise CheckError('sealed_summary_bytes_changed')
    value=_json(raw,MAX_SUMMARY_BYTES,'sealed_summary')
    if (type(value) is not dict or value.get('session_id')!=sid
        or value.get('capture_id')!=capture
        or value.get('database')!=str(Path(snapshot).absolute())):
        raise CheckError('sealed_summary_scope_binding')
    refs=value.get('retained_refs')
    if (type(refs) is not list or len(refs)>MAX_EVIDENCE_REFS
        or any(type(ref) is not str or not 1<=len(ref)<=160 for ref in refs)
        or refs!=sorted(set(refs))):
        raise CheckError('sealed_natural_probe_selection')
    _check(deadline)
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',required=True)
    parser.add_argument('--session',required=True)
    parser.add_argument('--capture',required=True)
    parser.add_argument('--summary',required=True)
    parser.add_argument('--summary-sha256',required=True)
    parser.add_argument('--conservation-sha256',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--deadline-monotonic',required=True,type=float)
    parser.add_argument('--decoded-start',type=int,default=0)
    args=parser.parse_args(argv)
    if not 0<=args.decoded_start<=256*MiB: raise CheckError('decoded_start_capacity')
    output=Path(args.output).absolute()
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise CheckError('fresh_reserved_output_directory_required')
    else:
        if not output.parent.is_dir(): raise CheckError('reserved_output_parent_missing')
        output.mkdir(mode=0o700)
    deadline=_deadline(args.deadline_monotonic,105.0)
    summary=_sealed_summary(args.summary,args.summary_sha256,args.snapshot,
                            args.session,args.capture,deadline)
    before=conservation_snapshot(args.snapshot,deadline=deadline)
    if before['sha256']!=args.conservation_sha256:
        raise CheckError('sealed_snapshot_conservation_mismatch_before_read')
    # The parent must reserve these 32 MiB within the global 512 MiB budget.
    # EvidenceRecorder enforces per-body/gzip/decoded/output pre-admission.
    result=passive_read(args.snapshot,args.session,output,deadline,
        decoded_start=args.decoded_start,retained_refs=summary['retained_refs'])
    after=conservation_snapshot(args.snapshot,deadline=deadline)
    if after!=before or after['sha256']!=args.conservation_sha256:
        raise CheckError('snapshot_durable_conservation_changed_by_passive_read')
    result['durable_summary']=str(Path(args.summary).absolute())
    result['durable_summary_sha256']=args.summary_sha256
    result['conservation_before_sha256']=before['sha256']
    result['conservation_after_sha256']=after['sha256']
    result['durable_conserved_across_passive_read']=True
    result['coverage_complete']=summary['coverage_complete']
    result['invariant_failures']=summary['invariant_failures']
    _write_json(output/'passive-readback.json',result,4*MiB)
    print(json.dumps({'status':'complete','output':str(output),'decoded_total':result['decoded_total'],
        'coverage_complete':result['coverage_complete'],'complete_traversal':result['complete_traversal'],
        'attempts':result['attempts']},sort_keys=True))
    return 0 if (result['coverage_complete'] and result['complete_traversal']
        and result['evidence_complete'] and result['binding_audit_complete']
        and not result['invariant_failures']) else 2


if __name__=='__main__':
    raise SystemExit(main())
