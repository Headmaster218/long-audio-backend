"""Prepared model-free contracts for exact primary recording annotations.

Run only after review and approval. All databases and source rows are synthetic;
no audio, model, native extraction, owner worker or filesystem proof is opened.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from audio_backend import recording_speaker_link_store as ledger
from audio_backend import recording_speaker_links as links
from audio_backend import transcript_contract as c
from audio_backend import transcript_store as ts
from audio_backend.native_owned_support_cache import ReadError
from test_transcript_http import SID, data
from test_transcript_lexical_projection import LexicalFixture, four_units


IDENTITY = 'recording-speaker-' + 'a' * 20


def parent_ref(f, ident=1):
    with f.db() as db:
        row = db.execute('SELECT * FROM segments WHERE id=?', (ident,)).fetchone()
    return c.primary_evidence(SID, row)['source_ref']


def saved_primary(ref):
    return {'scope_id':'1' * 64, 'state':'provisional_linked',
        'revision':4, 'decision_revision':4, 'policy_sha256':links.POLICY_SHA256_V2,
        'space_id':'synthetic-recording-space', 'jobs':[],
        'annotations':[{'source_kind':'native_primary', 'primary_source_ref':ref,
            'status':'provisional_linked', 'provisional_speaker_id':IDENTITY,
            'node_id':'3' * 64, 'proof_sha256':'4' * 64, 'source_key':'5' * 64,
            'primary_packet_sha256':'6' * 64, 'duplicate_evidence':False,
            'speaker_identity_valid':False, 'profile_update_allowed':False,
            'vector':[1., 0.], 'capture_packet':{'private':'never export'},
            'native_label':'S1', 'lexical_source_refs':['historical-l-ref']} ]}


def install_saved(f, monkeypatch, saved):
    calls = []
    def read(db, sid, rid):
        assert db.in_transaction
        assert db.execute('PRAGMA query_only').fetchone()[0] == 1
        calls.append((sid, rid))
        return deepcopy(saved)
    monkeypatch.setattr(ledger, 'read_saved_for_recording', read)
    monkeypatch.setattr(ledger, 'read_session_revision', lambda *_:'7' * 64)
    f.reader.speaker_links_enabled = True
    return calls


def projected_rows(value):
    stack = [value]
    while stack:
        item = stack.pop()
        if type(item) is dict:
            if item.get('source') in ('primary', 'primary_lexical', 'moss'):
                yield item
            stack.extend(v for k, v in item.items() if k != 'recording_speaker_link')
        elif type(item) is list:
            stack.extend(item)


def without_annotations(value):
    value = deepcopy(value)
    for item in projected_rows(value):
        item.pop('recording_speaker_link', None)
    return value


def test_primary_only_page_discovers_current_recording_and_preserves_exact_content(data, monkeypatch):
    before = data.page()
    ref = parent_ref(data)
    evidence = data.reader.evidence(SID, ref)
    calls = install_saved(data, monkeypatch, saved_primary(ref))
    page = data.page()
    assert calls == [(SID, 'batch')]
    assert page['recording_speaker_links']['scopes'][0]['recording_id'] == 'batch'
    assert page['selected_runs'] == []
    for key in ('reading', 'baseline_reading', 'alternatives'):
        assert without_annotations(page[key]) == before[key]
    for item in projected_rows(page):
        annotation = item.get('recording_speaker_link')
        if item['source_ref'] != ref:
            assert annotation is None  # Other rows have the same native S1 label.
            continue
        assert annotation['provisional_speaker_id'] == IDENTITY
        assert annotation['primary_source_ref'] == ref
        assert annotation['speaker_identity_valid'] is False
        assert annotation['profile_update_allowed'] is False
        assert annotation['reversible'] is True
        assert annotation['validation_semantics'] == 'validated_at_decision_revision'
        assert not {'vector', 'capture_packet', 'native_label', 'lexical_source_refs'} & annotation.keys()
    assert data.reader.evidence(SID, ref) == evidence
    assert 'never export' not in json.dumps(page)


@pytest.mark.parametrize('change', [
    "UPDATE segments SET text='changed primary' WHERE id=1",
    "UPDATE segments SET raw_text='changed raw' WHERE id=1",
    'DELETE FROM segments WHERE id=1',
])
def test_changed_or_deleted_parent_never_uses_old_primary_annotation(data, monkeypatch, change):
    ref = parent_ref(data)
    install_saved(data, monkeypatch, saved_primary(ref))
    assert IDENTITY in json.dumps(data.page())
    with data.db() as db:
        db.execute(change)
    # Even a stale-looking adapter returning the old decision cannot rebind it
    # to a changed exact p.ref or a different segment with the same S1 label.
    page = data.page()
    assert IDENTITY not in json.dumps(page)
    assert all('recording_speaker_link' not in row for row in projected_rows(page))


@pytest.mark.parametrize('change', [
    "DELETE FROM recording_work WHERE chunk_id='chunk'",
    "UPDATE recording_batches SET session_id='other' WHERE id='batch'",
    "UPDATE recording_work SET batch_id='missing-batch' WHERE chunk_id='chunk'",
])
def test_primary_scope_discovery_requires_current_session_membership(data, monkeypatch, change):
    calls = install_saved(data, monkeypatch, saved_primary(parent_ref(data)))
    with data.db() as db:
        db.execute(change)
    page = data.page()
    assert calls == []
    assert page['recording_speaker_links']['scopes'] == []
    assert len(page['reading']) == 3
    assert IDENTITY not in json.dumps(page)


def test_primary_link_does_not_label_unprobed_moss_phrases(data, monkeypatch):
    job = data.job(1, 0, 5, labels=('S1', 'S2'))
    saved = saved_primary(parent_ref(data))
    # Even overlapping job coordinates in a primary observation do not turn
    # the native-primary observation into MOSS phrase evidence.
    saved['annotations'][0].update(job_id=job['jid'], result_sha256=job['sha'], phrase_index=0)
    install_saved(data, monkeypatch, saved)
    page = data.page()
    assert IDENTITY in json.dumps(page['baseline_reading'])
    moss_rows = [row for row in page['reading'] if row['source'] == 'moss']
    assert len(moss_rows) == 2
    for row in moss_rows:
        link = row['recording_speaker_link']
        assert link['provisional_speaker_id'] is None
        assert link['status'] == 'insufficient_clean_support'
        assert link['speaker_identity_valid'] is False
        assert link['validation_semantics'] == 'validated_at_decision_revision'


@pytest.mark.parametrize('inventory_change', ['unchanged', 'changed', 'missing'])
def test_lexical_children_use_current_parent_not_packet_lexical_ref(tmp_path, monkeypatch, inventory_change):
    f = LexicalFixture(tmp_path / 'primary-lexical-annotations.sqlite3')
    inventory = f.install(1, four_units())
    f.job(1, 1, 3, labels=('S1',))
    ref = parent_ref(f)
    old_refs = [row['source_ref'] for row in projected_rows(f.lexical_page())
                if row['source'] == 'primary_lexical']
    saved = saved_primary(ref)
    saved['annotations'][0]['lexical_source_refs'] = old_refs
    if inventory_change == 'changed':
        inventory['coordinates']['core_end'] = 20.
        f.replace_inventory(1, inventory)
    elif inventory_change == 'missing':
        f.replace_inventory(1, None)
    # This getter represents a current saved decision; historical packet l.refs
    # may be absent or different while the current p.ref remains exact.
    assert parent_ref(f) == ref
    before = f.lexical_page()
    install_saved(f, monkeypatch, saved)
    after = f.lexical_page()
    for key in ('reading', 'baseline_reading', 'alternatives', 'selected_runs'):
        assert without_annotations(after[key]) == before[key]
    primary_rows = [row for row in projected_rows(after) if row['source'] in ('primary', 'primary_lexical')]
    assert primary_rows
    for row in primary_rows:
        assert row['recording_speaker_link']['provisional_speaker_id'] == IDENTITY
        assert row['recording_speaker_link']['primary_source_ref'] == ref
    lexical_rows = [row for row in primary_rows if row['source'] == 'primary_lexical']
    if inventory_change == 'changed':
        assert lexical_rows and all(row['source_ref'] not in old_refs for row in lexical_rows)
    elif inventory_change == 'missing':
        assert lexical_rows == []


def test_projection_pass_requires_exact_parent_and_never_parses_lexical_ref():
    ref = 'p.1.' + 'a' * 64
    other = 'p.2.' + 'a' * 64
    annotation = c.primary_speaker_annotation(saved_primary(ref), ref)
    rows = [
        {'source':'primary', 'source_ref':ref, 'text':'exact'},
        {'source':'primary_lexical', 'source_ref':'changed-l-ref', 'parent_source_ref':ref, 'text':'child'},
        {'source':'primary_lexical', 'source_ref':'historical-l-ref', 'parent_source_ref':other},
        {'source':'primary_lexical', 'source_ref':'historical-l-ref'},
        {'source':'moss', 'source_ref':ref, 'speaker_scope':['S1']},
    ]
    before = deepcopy(rows)
    c.annotate_primary_speaker_links(rows, {ref:annotation})
    assert all('recording_speaker_link' in row for row in rows[:2])
    assert all('recording_speaker_link' not in row for row in rows[2:])
    assert without_annotations(rows) == before


@pytest.mark.parametrize('second', [
    {'provisional_speaker_id':'recording-speaker-' + 'b' * 20},
    {'status':'probe_unavailable', 'provisional_speaker_id':None},
    {'status':'source_scoped', 'provisional_speaker_id':None},
])
def test_multiple_observations_must_agree_regardless_of_order(second):
    ref = 'p.1.' + 'a' * 64
    saved = saved_primary(ref)
    saved['annotations'].append(dict(saved['annotations'][0], **second))
    result = c.primary_speaker_annotation(saved, ref)
    saved['annotations'].reverse()
    assert c.primary_speaker_annotation(saved, ref) == result
    assert result['status'] == 'insufficient_clean_support'
    assert result['provisional_speaker_id'] is None
    assert result['reason'] == 'primary_observations_disagree'


def test_agreeing_observations_keep_identity_but_omit_ambiguous_proof():
    ref = 'p.1.' + 'a' * 64
    saved = saved_primary(ref)
    saved['annotations'].append(dict(saved['annotations'][0], node_id='9' * 64, proof_sha256='8' * 64))
    result = c.primary_speaker_annotation(saved, ref)
    assert result['provisional_speaker_id'] == IDENTITY
    assert 'node_id' not in result and 'proof_sha256' not in result
    assert result['source_key'] == '5' * 64


@pytest.mark.parametrize('change', [
    {'status':[]}, {'provisional_speaker_id':'private-person'},
    {'status':'source_scoped'}, {'provisional_speaker_id':None},
])
def test_malformed_matching_observation_never_exports_identity(change):
    ref = 'p.1.' + 'a' * 64
    saved = saved_primary(ref)
    saved['annotations'][0].update(change)
    result = c.primary_speaker_annotation(saved, ref)
    assert result['status'] == 'probe_unavailable'
    assert result['provisional_speaker_id'] is None
    assert result['speaker_identity_valid'] is False
    assert result['profile_update_allowed'] is False


@pytest.mark.parametrize('policy', [links.POLICY_SHA256, '2' * 64, None])
def test_native_annotation_requires_exact_v2_policy(policy):
    ref = 'p.1.' + 'a' * 64
    saved = saved_primary(ref)
    saved['policy_sha256'] = policy
    assert c.primary_speaker_annotation(saved, ref) is None


@pytest.mark.parametrize('semantics', [True, None, 0, 'false', 'missing'])
def test_native_annotation_requires_explicit_false_identity_semantics(semantics):
    ref = 'p.1.' + 'a' * 64
    saved = saved_primary(ref)
    unsafe = deepcopy(saved['annotations'][0])
    if semantics == 'missing':
        unsafe.pop('speaker_identity_valid')
    else:
        unsafe['speaker_identity_valid'] = semantics
    # A valid duplicate cannot mask an unsafe observation in either order.
    saved['annotations'].append(unsafe)
    assert c.primary_speaker_annotation(saved, ref) is None
    saved['annotations'].reverse()
    assert c.primary_speaker_annotation(saved, ref) is None


@pytest.mark.parametrize('change', [
    {'state':'links_stale'}, {'decision_revision':3}, {'revision':True},
])
def test_stale_or_invalid_decision_revision_cannot_project_identity(change):
    ref = 'p.1.' + 'a' * 64
    saved = dict(saved_primary(ref), **change)
    result = c.primary_speaker_annotation(saved, ref)
    assert result['status'] == 'links_stale'
    assert result['provisional_speaker_id'] is None


@pytest.mark.parametrize('broken', [None, [], {'annotations':None}, {'annotations':[None, []]},
    {'state':[], 'jobs':[None, {}], 'annotations':[{'status':[]}] }])
def test_malformed_optional_saved_decisions_do_not_hide_primary_or_moss_text(data, monkeypatch, broken):
    data.job(1, 0, 5)
    before = data.page()
    install_saved(data, monkeypatch, broken)
    after = data.page()
    for key in ('reading', 'baseline_reading', 'alternatives'):
        assert without_annotations(after[key]) == before[key]


def test_saved_read_failure_is_optional_on_primary_only_page(data, monkeypatch):
    before = data.page()
    install_saved(data, monkeypatch, saved_primary(parent_ref(data)))
    def unavailable(*_):
        raise ReadError(503, 'saved_links_unavailable')
    monkeypatch.setattr(ledger, 'read_saved_for_recording', unavailable)
    after = data.page()
    assert after['reading'] == before['reading']
    assert after['recording_speaker_links']['scopes'][0]['state'] == 'probe_unavailable'


def test_legacy_moss_annotation_remains_unchanged_and_identity_false():
    ref = 'p.1.' + 'a' * 64
    saved = saved_primary(ref)
    saved['policy_sha256'] = links.POLICY_SHA256
    saved['annotations'] = [{'job_id':'a' * 32, 'result_sha256':'b' * 64,
        'phrase_index':0, 'status':'provisional_linked',
        'provisional_speaker_id':'recording-speaker-legacy', 'node_id':'c' * 64}]
    result = c.speaker_annotation(saved, 'a' * 32, 'b' * 64, 0)
    assert result == {'status':'provisional_linked', 'provisional_speaker_id':'recording-speaker-legacy',
        'scope':'recording', 'speaker_identity_valid':False, 'profile_update_allowed':False,
        'policy_sha256':links.POLICY_SHA256, 'space_id':'synthetic-recording-space',
        'revision':4, 'decision_revision':4, 'node_id':'c' * 64}
    assert c.primary_speaker_annotation(saved, ref) is None


def test_v2_moss_annotation_discloses_revision_bound_validation():
    ref = 'p.1.' + 'a' * 64
    saved = saved_primary(ref)
    saved['annotations'] = [{'source_kind':'moss', 'job_id':'a' * 32,
        'result_sha256':'b' * 64, 'phrase_index':0, 'status':'provisional_linked',
        'provisional_speaker_id':IDENTITY, 'speaker_identity_valid':False}]
    result = c.speaker_annotation(saved, 'a' * 32, 'b' * 64, 0)
    assert result['provisional_speaker_id'] == IDENTITY
    assert result['validation_semantics'] == 'validated_at_decision_revision'
    assert result['speaker_identity_valid'] is False
    assert result['profile_update_allowed'] is False


@pytest.mark.parametrize('include_moss', [False, True])
@pytest.mark.parametrize('malformed', [False, True])
def test_many_recordings_admit_before_blob_read_and_share_one_allowance(data, monkeypatch,
                                                                     include_moss, malformed):
    count = ts.MAX_CHUNKS
    with data.db() as db:
        db.execute('BEGIN')
        ledger.init_schema(db)
        db.execute('DELETE FROM segments')
        db.execute('DELETE FROM recording_work')
        db.execute('DELETE FROM chunks')
        for index in range(count):
            cid, rid = f'chunk-{index:03d}', f'recording-{index:03d}'
            start = index / 4
            db.execute('''INSERT INTO chunks(id,session_id,digest,timeline_start,duration,status)
                VALUES(?,?,?,?,.25,'completed')''', (cid,SID,'d' * 64,start))
            db.execute('INSERT INTO recording_batches VALUES(?,?,0)', (rid,SID))
            db.execute('INSERT INTO recording_work VALUES(?,?,?,?,?)',
                (cid,rid,4000,'e' * 64,index * 4000))
            db.execute('''INSERT INTO segments(id,chunk_id,start,end,text,raw_text,asr_backend,
                timestamp_kind,confidence,speaker_local_id,speaker_window_start)
                VALUES(?,?,?,?,'p','p','synthetic','native',.8,'S1',0.)''',
                (index + 1,cid,start,start + .25))
        first = c.primary_evidence(SID, db.execute('SELECT * FROM segments WHERE id=1').fetchone())
        for index in range(count):
            # Large ignored padding makes every materialized decision exactly
            # 64 KiB while its returned public decision is tiny. Charging only
            # the getter's result would incorrectly admit all 128 recordings.
            document = {'annotations':saved_primary(first['source_ref'])['annotations'] if index == 0 else [],
                'jobs':[], 'diagnostics':{'policy_sha256':links.POLICY_SHA256_V2}, 'padding':''}
            document['padding'] = 'x' * (ledger.MAX_DECISION_BYTES - len(c.encoded(document)))
            raw = b' ' * ledger.MAX_DECISION_BYTES if malformed else c.encoded(document)
            assert len(raw) == ledger.MAX_DECISION_BYTES
            db.execute(f'''INSERT INTO {ledger.SCOPES}(scope_seq,scope_id,session_id,recording_id,
                policy_sha256,space_json,space_id,revision,decision_revision,decision_session_revision,
                decision_json,decision_sha256,state) VALUES(?,?,?,?,?,?,?,4,4,0,?,?,?)''',
                (index + 1,c.digest({'scope':index}),SID,f'recording-{index:03d}',
                 links.POLICY_SHA256_V2,b'{}','synthetic',raw,hashlib.sha256(raw).hexdigest(),
                 'provisional_linked' if index == 0 else 'source_scoped'))
    if include_moss:
        data.job(1, 0, .25, cid='chunk-000', labels=('S1',), text='p')
    with data.db() as db:
        revision = db.execute('SELECT transcript_revision FROM sessions WHERE id=?', (SID,)).fetchone()[0]
        db.execute(f'UPDATE {ledger.SCOPES} SET decision_session_revision=? WHERE session_id=?', (revision,SID))
    before = data.page()
    original_read, original_json = ledger.read_saved_for_recording, ledger._json
    calls, decoded_bytes = [], []
    maximum_reads = c.MAX_RESULTS_BYTES // ledger.MAX_DECISION_BYTES
    def read(db, sid, rid, **kwargs):
        # This guard is before the getter's SELECT decision_json, so even a
        # failed or unreturned BLOB materialization would fail the contract.
        assert len(calls) < maximum_reads
        calls.append((sid,rid))
        assert db.execute('PRAGMA query_only').fetchone()[0] == 1
        return original_read(db,sid,rid,**kwargs)
    def decode(raw, maximum):
        if maximum == ledger.MAX_DECISION_BYTES:
            decoded_bytes.append(len(raw))
            assert sum(decoded_bytes) <= c.MAX_RESULTS_BYTES
        return original_json(raw,maximum)
    monkeypatch.setattr(ledger, 'read_saved_for_recording', read)
    monkeypatch.setattr(ledger, '_json', decode)
    data.reader.speaker_links_enabled = True
    after = data.page()
    assert len(calls) == len(set(calls)) == maximum_reads
    assert len(decoded_bytes) == maximum_reads
    assert sum(decoded_bytes) == c.MAX_RESULTS_BYTES
    assert calls[0] == (SID,'recording-000')
    assert after['recording_speaker_links']['annotation_read'] == {
        'complete':False, 'scope':'page_recording_decisions',
        'reason':'saved_decision_bytes', 'byte_limit':c.MAX_RESULTS_BYTES}
    assert sum(item['state'] == 'capacity_exhausted'
        for item in after['recording_speaker_links']['scopes']) == count - maximum_reads
    for key in ('reading', 'baseline_reading', 'alternatives', 'selected_runs'):
        assert without_annotations(after[key]) == before[key]
    assert (IDENTITY in json.dumps(after['baseline_reading'])) is (not malformed)
    # Skipped decisions still contribute scalar headers to the cursor digest.
    with data.db() as db:
        db.execute(f'UPDATE {ledger.SCOPES} SET decision_sha256=? WHERE recording_id=?',
            ('9' * 64,f'recording-{count - 1:03d}'))
    with pytest.raises(ReadError) as exc:
        data.page(cursor=after['next_cursor'])
    assert exc.value.reason == 'transcript_revision_changed'
    assert len(calls) == maximum_reads


@pytest.mark.parametrize('projection,policy_hash', [
    ('whole-primary-v1',c.POLICY_HASH), ('lexical-estimated-v1',c.LEXICAL_POLICY_HASH),
])
@pytest.mark.parametrize('decision_policy', [links.POLICY_SHA256, links.POLICY_SHA256_V2])
def test_upgraded_annotation_contract_rejects_baseline_cursor_without_rehashing_sources(
        tmp_path, projection, policy_hash, decision_policy):
    f = LexicalFixture(tmp_path / 'annotation-cursor-upgrade.sqlite3')
    f.install(1, four_units())
    job = f.job(1, 1, 3, labels=('S1',))
    ref = parent_ref(f)
    lexical_refs = [group['source_ref'] for group in f.groups(1)]
    source_bytes = {source:f.reader.evidence(SID,source) for source in [ref,*lexical_refs]}
    moss_annotation = {'job_id':job['jid'], 'result_sha256':job['sha'], 'phrase_index':0,
        'status':'provisional_linked', 'provisional_speaker_id':IDENTITY,
        'speaker_identity_valid':False}
    annotations = [moss_annotation]
    if decision_policy == links.POLICY_SHA256_V2:
        moss_annotation['source_kind'] = 'moss'
        annotations.extend(saved_primary(ref)['annotations'])
    decision = {'annotations':annotations, 'jobs':[],
        'diagnostics':{'policy_sha256':decision_policy,
            'schema':links.SCHEMA if decision_policy == links.POLICY_SHA256 else links.SCHEMA_V2}}
    raw = c.encoded(decision)
    with f.db() as db:
        db.execute('BEGIN')
        ledger.init_schema(db)
        session_revision = db.execute('SELECT transcript_revision FROM sessions WHERE id=?', (SID,)).fetchone()[0]
        db.execute(f'''INSERT INTO {ledger.SCOPES}(scope_seq,scope_id,session_id,recording_id,
            policy_sha256,space_json,space_id,revision,decision_revision,decision_session_revision,
            decision_json,decision_sha256,state) VALUES(1,?,?, 'batch',?,?,?,4,4,?,?,?,'provisional_linked')''',
            (ledger.scope_identity(SID,'batch'),SID,decision_policy,b'{}','synthetic',
             session_revision,raw,hashlib.sha256(raw).hexdigest()))
    f.reader.speaker_links_enabled = True
    with f.reader.snapshot(SID) as (db,session,_):
        rows = db.execute(f'''SELECT scope_id,revision,decision_revision,decision_session_revision,
            decision_sha256,state,policy_sha256,space_id FROM {ledger.SCOPES}
            INDEXED BY recording_link_session_scope WHERE session_id=? ORDER BY scope_seq LIMIT 1025''',
            (SID,)).fetchall()
        headers = [tuple(row) for row in rows]
        # Exact pre-upgrade getter payload, including its singular policy key.
        # No baseline code is imported, and no DB/source mutation is involved.
        baseline_links_revision = c.digest({'session_id':SID,
            'session_revision':session['transcript_revision'],
            'current_policy_sha256':links.POLICY_SHA256, 'scopes':headers})
        current_links_revision = ledger.read_session_revision(db,SID)
        assert current_links_revision == c.digest({'session_id':SID,
            'session_revision':session['transcript_revision'],
            'supported_policy_sha256':links.SUPPORTED_POLICIES, 'scopes':headers})
        assert current_links_revision != baseline_links_revision
        change = db.execute('''SELECT cursor FROM result_changes INDEXED BY result_changes_session
            WHERE session_id=? ORDER BY cursor DESC LIMIT 1''', (SID,)).fetchone()
        baseline_revision = c.digest({'policy':policy_hash, 'session':SID,
            'primary_change_max':change[0] if change else 0,
            'transcript_revision':session['transcript_revision'], 'timeline_end':session['timeline_end'],
            'speaker_links_revision':baseline_links_revision})
        assert f.reader._revision(db,SID,session,policy_hash=policy_hash) != baseline_revision
        saved = ledger.read_saved_for_recording(db,SID,'batch')
        assert saved['state'] == 'provisional_linked'
        assert saved['annotations'] == annotations
        assert saved['policy_sha256'] == decision_policy
    old_cursor = c.cursor(SID,0,baseline_revision,policy_hash=policy_hash)
    assert c.parse_cursor(old_cursor,SID,policy_hash=policy_hash)['revision'] == baseline_revision
    with pytest.raises(ReadError) as exc:
        f.page(cursor=old_cursor,projection=projection)
    assert exc.value.status_code == 409 and exc.value.reason == 'transcript_revision_changed'
    page = f.page(projection=projection)
    assert page['recording_speaker_links']['annotation_read'] == {
        'complete':True, 'scope':'page_recording_decisions'}
    moss_rows = [row for row in projected_rows(page) if row['source'] == 'moss']
    assert moss_rows and all(row['recording_speaker_link']['provisional_speaker_id'] == IDENTITY
                             for row in moss_rows)
    if decision_policy == links.POLICY_SHA256:
        assert all('validation_semantics' not in row['recording_speaker_link'] for row in moss_rows)
        assert all('recording_speaker_link' not in row for row in projected_rows(page)
                   if row['source'] in ('primary','primary_lexical'))
    else:
        assert all(row['recording_speaker_link']['validation_semantics'] ==
                   'validated_at_decision_revision' for row in moss_rows)
        assert all(row['recording_speaker_link']['source_kind'] == 'native_primary'
                   for row in projected_rows(page) if row['source'] in ('primary','primary_lexical'))
    assert parent_ref(f) == ref
    assert [group['source_ref'] for group in f.groups(1)] == lexical_refs
    assert {source:f.reader.evidence(SID,source) for source in source_bytes} == source_bytes
