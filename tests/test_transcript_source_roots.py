"""Ordered source roots only; synthetic SQLite and saved-proof numeric topologies.

All names, hashes, text and receipts here are synthetic. The six numeric cases
were checked against exact saved proof bytes in a separate private provenance
record. These tests do not load historical databases, audio or model runtimes.
"""
import base64
from copy import deepcopy
import hashlib
import json

import pytest

from test_transcript_http import Fixture, SID
from audio_backend import transcript_contract as c, transcript_store as ts
from audio_backend import moss_automatic as auto, moss_result_store as history
from audio_backend.native_owned_support_cache import ReadError

# (first core start, core join, last core end, actual support start).
SAVED_TOPOLOGIES = (
    (168000, 305920, 336000, 168000),
    (2184000, 2333440, 2352000, 2184000),
    (4200000, 4360960, 4368000, 4200960),
    (7728000, 7808000, 7896000, 7728000),
    (168000, 305920, 336000, 168000),
    (3192000, 3347200, 3360000, 3192000),
)


@pytest.fixture
def data(tmp_path):
    value = Fixture(tmp_path / 'synthetic-source-roots.sqlite3')
    with value.db() as db:
        db.execute('DELETE FROM segments')
        db.execute('DELETE FROM recording_work')
        db.execute('DELETE FROM chunks')
    return value


def same_upload(data, geometry=SAVED_TOPOLOGIES[0], kind='core_seam'):
    a, join, b, support_start = geometry
    data.chunk('source', a / c.RATE, b / c.RATE)
    job = data.job(1, support_start / c.RATE, b / c.RATE, cid='source',
                   kind=kind, logical_origin=0)
    binding = deepcopy(job['binding'])
    binding['core_frames'] = [[a, join], [join, b]]
    raw = c.encoded(binding)
    key = hashlib.sha256(raw).hexdigest()
    meta = json.loads(job['metadata'])
    meta['context']['automatic_candidate_key'] = key
    slots = [c.digest({'synthetic_commit_core': core}) for core in binding['core_frames']]
    assert slots[0] != slots[1]
    with data.db() as db:
        db.execute(f'''UPDATE {auto.TABLE} SET candidate_key=?,binding=?,cid2=cid1,
            slot1=?,slot2=?,proof=? WHERE job_id=?''',
            (key, raw, *slots, b'synthetic-proof-two-distinct-commits', job['jid']))
        db.execute(f'UPDATE {history.TABLE} SET metadata_json=? WHERE job_id=?',
                   (c.encoded(meta), job['jid']))
    job.update(key=key, binding=binding)
    return job


def cross_upload(data):
    data.chunk('left', 0, 10.5)
    data.chunk('right', 10.5, 21)
    job = data.job(1, .5, 20.5, kind='core_seam',
                   source_chunks=('left', 'right'), logical_origin=123456)
    with data.db() as db:
        db.execute(f'UPDATE {auto.TABLE} SET slot2=? WHERE job_id=?', ('7' * 64, job['jid']))
    return job


def binding_row(data):
    with data.db() as db:
        row = dict(db.execute(f'SELECT * FROM {auto.TABLE}').fetchone())
        return ts.TranscriptStore._binding(db, SID, row)


def rewrite_binding(data, mutate, *, rehash=True):
    with data.db() as db:
        row = db.execute(f'SELECT * FROM {auto.TABLE}').fetchone()
        binding = json.loads(row['binding'])
        mutate(binding)
        raw = c.encoded(binding)
        key = hashlib.sha256(raw).hexdigest() if rehash else row['candidate_key']
        db.execute(f'UPDATE {auto.TABLE} SET candidate_key=?,binding=? WHERE seq=?',
                   (key, raw, row['seq']))


def retained_bytes(data):
    with data.db() as db:
        return tuple(tuple(tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY 1'))
                     for table in (auto.TABLE, history.TABLE))


@pytest.mark.parametrize('geometry', SAVED_TOPOLOGIES, ids=[f'saved-{n:02d}' for n in range(1, 7)])
def test_six_saved_topologies_place_merged_alias_and_preserve_evidence(data, geometry):
    job = same_upload(data, geometry)
    before = retained_bytes(data)
    binding, start, end, cancelled = binding_row(data)
    assert (start, end, cancelled) == (geometry[3], geometry[2], False)
    assert len(binding['parts']) == len(binding['source_aliases']) == 1
    assert binding['core_frames'] == [[geometry[0], geometry[1]], [geometry[1], geometry[2]]]
    assert binding['parts'][0]['source_frames'] == 168000
    assert binding['parts'][0]['source_start'] == geometry[3] - geometry[0]
    page = data.page(block=start // c.BLOCK_FRAMES)
    assert not any(n.get('reason') == 'automatic_binding_invalid' for n in page['notices'])
    assert len(page['selected_runs']) == 1
    assert (page['selected_runs'][0]['start_frame'], page['selected_runs'][0]['end_frame']) == (start, end)
    assert len(page['reading']) == 2
    assert all(item['speaker_scope'][0] == job['jid'] for item in page['reading'])
    assert data.audit()['unplaced_bindings'] == []
    raw = data.reader.evidence(SID, f"m.{job['jid']}.{job['sha']}.all")
    assert json.loads(raw)
    assert retained_bytes(data) == before
    with data.db() as db:
        row = db.execute(f'SELECT cid1,cid2,slot1,slot2 FROM {auto.TABLE}').fetchone()
    assert row['cid1'] == row['cid2'] == 'source'
    assert row['slot1'] != row['slot2'] and row['slot2'] is not None


def test_same_root_reset_seam_remains_two_cores(data):
    same_upload(data, kind='reset_seam')
    binding, start, end, _ = binding_row(data)
    assert binding['kind'] == 'reset_seam' and len(binding['core_frames']) == 2
    assert (start, end) == (168000, 336000)


def test_ordinary_single_root_is_unchanged(data):
    data.chunk('one', 0, 10.5)
    data.job(1, 0, 10.5, cid='one')
    binding, start, end, _ = binding_row(data)
    assert len(binding['core_frames']) == len(binding['source_aliases']) == 1
    assert (start, end) == (0, 168000)
    assert len(data.page()['selected_runs']) == 1


def test_distinct_upload_roots_remain_ordered(data):
    cross_upload(data)
    before = retained_bytes(data)
    binding, start, end, _ = binding_row(data)
    assert [v['chunk_id'] for v in binding['source_aliases']] == ['left', 'right']
    assert (start, end) == (8000, 328000)
    assert len(data.page()['selected_runs']) == 1
    assert retained_bytes(data) == before


@pytest.mark.parametrize('mutation', ['reverse_roots', 'reverse_aliases', 'missing_second',
    'duplicate_alias', 'wrong_root', 'cross_session', 'offset_mismatch'])
def test_distinct_upload_mismatches_are_not_deduplicated(data, mutation):
    cross_upload(data)
    if mutation == 'reverse_aliases':
        rewrite_binding(data, lambda b: b['source_aliases'].reverse())
    elif mutation == 'duplicate_alias':
        rewrite_binding(data, lambda b: b['source_aliases'].__setitem__(1, deepcopy(b['source_aliases'][0])))
    else:
        with data.db() as db:
            if mutation == 'reverse_roots':
                db.execute(f'UPDATE {auto.TABLE} SET cid1=cid2,cid2=cid1')
            elif mutation == 'missing_second':
                db.execute(f'UPDATE {auto.TABLE} SET cid2=NULL')
            elif mutation == 'wrong_root':
                db.execute(f"UPDATE {auto.TABLE} SET cid2='wrong'")
            elif mutation == 'cross_session':
                db.execute("UPDATE chunks SET session_id='other' WHERE id='right'")
            else:
                db.execute("UPDATE chunks SET timeline_start=timeline_start+1 WHERE id='right'")
                db.execute("UPDATE recording_work SET capture_start_frame=capture_start_frame+16000 WHERE chunk_id='right'")
    with pytest.raises(ReadError, match='automatic_(binding_invalid|source_unplaced)'):
        binding_row(data)


def mutate_shape(binding, mutation):
    part, alias = binding['parts'][0], binding['source_aliases'][0]
    if mutation == 'duplicate_alias':
        binding['source_aliases'].append(deepcopy(alias))
        binding['parts'].append(deepcopy(part))
    elif mutation == 'missing_alias': binding['source_aliases'].clear()
    elif mutation == 'alias_scalar': binding['source_aliases'][0] = 'source'
    elif mutation == 'alias_extra_key': alias['unexpected'] = 1
    elif mutation == 'wrong_alias': alias['chunk_id'] = 'wrong'
    elif mutation == 'bad_receipt': alias['receipt_sha256'] = 'malformed'
    elif mutation == 'wrong_receipt': alias['receipt_sha256'] = '0' * 64
    elif mutation == 'bad_digest': part['source_sha256'] = 'malformed'
    elif mutation == 'wrong_digest': part['source_sha256'] = '0' * 64
    elif mutation == 'source_length': part['source_frames'] += 1
    elif mutation == 'source_end': part['source_end'] -= 1
    elif mutation == 'logical_start': part['logical_start'] += 1
    elif mutation == 'support_end': binding['support_frames'][1] -= 1
    elif mutation == 'core_count': binding['core_frames'].pop()
    elif mutation == 'core_gap': binding['core_frames'][1][0] += 1
    elif mutation == 'core_reversed': binding['core_frames'].reverse()
    elif mutation == 'core_not_enclosing': binding['core_frames'][0][0] += 1
    elif mutation == 'wrong_session': binding['session_id'] = 'other'
    elif mutation == 'wrong_policy': binding['selection_policy'] = '0' * 64
    else: raise AssertionError(mutation)


@pytest.mark.parametrize('mutation', ['duplicate_alias', 'missing_alias', 'alias_scalar',
    'alias_extra_key', 'wrong_alias', 'bad_receipt', 'wrong_receipt', 'bad_digest',
    'wrong_digest', 'source_length', 'source_end', 'logical_start', 'support_end',
    'core_count', 'core_gap', 'core_reversed', 'core_not_enclosing', 'wrong_session', 'wrong_policy'])
def test_same_upload_binding_mutations_still_fail_closed(data, mutation):
    same_upload(data)
    rewrite_binding(data, lambda b: mutate_shape(b, mutation))
    with pytest.raises(ReadError, match='automatic_(binding_invalid|source_unplaced)'):
        binding_row(data)


@pytest.mark.parametrize('mutation', ['hash', 'receipt', 'digest', 'duration', 'capture',
    'batch_origin', 'batch_session', 'source_missing', 'wrong_indexed_root'])
def test_same_upload_source_and_hash_checks_remain_strict(data, mutation):
    same_upload(data)
    if mutation == 'hash':
        rewrite_binding(data, lambda b: b['parts'][0].update(source_sha256='0' * 64), rehash=False)
    else:
        queries = {
            'receipt': "UPDATE recording_work SET receipt_id='" + '0' * 64 + "'",
            'digest': "UPDATE chunks SET digest='" + '0' * 64 + "'",
            'duration': 'UPDATE chunks SET duration=duration+1',
            'capture': 'UPDATE recording_work SET capture_start_frame=capture_start_frame+1',
            'batch_origin': 'UPDATE recording_batches SET capture_origin_frame=capture_origin_frame+1',
            'batch_session': "UPDATE recording_batches SET session_id='other'",
            'source_missing': 'DELETE FROM recording_work',
            'wrong_indexed_root': f"UPDATE {auto.TABLE} SET cid2='wrong'",
        }
        with data.db() as db: db.execute(queries[mutation])
    with pytest.raises(ReadError, match='automatic_(binding_invalid|source_unplaced)'):
        binding_row(data)
    page = data.page()
    assert page['selected_runs'] == []
    assert data.audit()['unplaced_bindings'][0]['status'] == 'unsupported_unplaced'


def test_cancelled_same_upload_remains_unselected(data):
    same_upload(data)
    with data.db() as db: db.execute("UPDATE chunks SET status='cancelled'")
    assert binding_row(data)[3] is True
    assert data.page()['selected_runs'] == []


@pytest.mark.parametrize('audit', [False, True], ids=['time-page', 'binding-audit'])
def test_old_policy_cursors_refused_and_new_policy_round_trips(data, audit):
    same_upload(data)
    old_policy = dict(c.POLICY)
    assert old_policy.pop('source_roots') == 'ordered-unique-commit-roots-v1'
    old_hash = c.digest(old_policy)
    assert old_hash != c.POLICY_HASH
    assert c.POLICY['selection'] == 'exact-adjacent-path-whole-primary-v1'
    assert c.POLICY['rank'] == ['maximum-support-frames', 'fewer-jobs', 'seam-ordinary-start-archive-hash']
    make, parse = (c.audit_cursor, c.parse_audit_cursor) if audit else (c.cursor, c.parse_cursor)
    current = make(SID, 1, 'a' * 64)
    assert parse(current, SID)['policy'] == c.POLICY_HASH
    value = json.loads(base64.urlsafe_b64decode(current + '=' * (-len(current) % 4)))
    value['policy'] = old_hash
    old = base64.urlsafe_b64encode(c.encoded(value)).decode('ascii').rstrip('=')
    with pytest.raises(ReadError, match='invalid_transcript_cursor') as error:
        (data.audit if audit else data.page)(cursor=old)
    assert error.value.status_code == 409


@pytest.mark.parametrize('field,reason', [
    ('proof_sha256', 'transcript_result_invalid'),
    ('result_sha256', 'transcript_result_hash_invalid'),
])
def test_same_upload_retained_proof_and_result_hashes_remain_bound(data, field, reason):
    same_upload(data)
    table = auto.TABLE if field == 'proof_sha256' else history.TABLE
    with data.db() as db:
        db.execute(f'UPDATE {table} SET {field}=?', ('0' * 64,))
    with pytest.raises(ReadError, match=reason):
        data.page()
