"""Fresh SQL compatibility and boundary-budget contracts, without models."""
from copy import deepcopy
import json

import pytest

from audio_backend import recording_primary_probe as primary
from audio_backend import recording_speaker_links as links
from audio_backend import recording_speaker_link_store as ledger
from audio_backend import moss_result_store as history
from audio_backend.native_owned_support_cache import ReadError
from test_recording_speaker_link_store import (
    environment, connection, prepared, finish, SID, RID,
)
from test_recording_speaker_links import ordinary, vector


def legacy_batch(store, batch):
    with connection(store.path) as db:
        result = json.loads(db.execute(f'SELECT result_json FROM {history.TABLE} WHERE job_id=?',
            (batch['job_id'],)).fetchone()[0])
    support, pcm, proof = ordinary(seed=1)
    old, _ = links.capture_candidates(support, pcm, proof, result,
        batch['job_id'], batch['result_sha256'], links.expected_space())
    return old


def test_new_scope_is_v2_before_first_ordinary_capture(environment):
    store, value, _ = environment
    batch = prepared(store)
    proof = history.encoded(batch['proof'])
    assert value.capture_policy(SID, proof) == links.POLICY_SHA256_V2
    legacy = legacy_batch(store, batch)
    with pytest.raises(ReadError, match='speaker_link_policy_conflict'):
        value.reserve(SID, RID, legacy['job_id'], legacy['result_sha256'],
            links.POLICY_SHA256, links.expected_space(), max_nodes=len(legacy['nodes']), batch=legacy)
    token, embedded = finish(value, batch)
    assert value.snapshot(token['scope_id'])['policy_sha256'] == links.POLICY_SHA256_V2
    assert value.capture_policy(SID, proof) == links.POLICY_SHA256_V2
    with pytest.raises(ReadError, match='speaker_link_policy_conflict'):
        value.reserve(SID, RID, legacy['job_id'], legacy['result_sha256'],
            links.POLICY_SHA256, links.expected_space(), max_nodes=len(legacy['nodes']), batch=legacy)


def test_existing_v1_scope_stays_readable_moss_only_and_cannot_mix(environment):
    store, value, _ = environment
    modern = prepared(store)
    legacy = legacy_batch(store, modern)
    scope = ledger.scope_identity(SID, RID)
    raw_space, space_id = ledger._space(links.expected_space())
    # Explicit historical fixture. Production admission never creates v1 now.
    with connection(store.path) as db:
        db.execute(f'''INSERT INTO {ledger.SCOPES}(scope_seq,scope_id,session_id,recording_id,
            policy_sha256,space_json,space_id,retained_bytes) VALUES(1,?,?,?,?,?,?,?)''',
            (scope,SID,RID,links.POLICY_SHA256,raw_space,space_id,ledger.SCOPE_BYTES))
        db.execute(f'UPDATE {ledger.SCOPES} SET retained_bytes=? WHERE scope_seq=0',
                   (ledger.SCOPE_BYTES,))
    assert value.capture_policy(SID,history.encoded(legacy['proof'])) == links.POLICY_SHA256
    token = value.reserve(SID,RID,legacy['job_id'],legacy['result_sha256'],
        links.POLICY_SHA256,links.expected_space(),max_nodes=len(legacy['nodes']),batch=legacy)
    embedded = deepcopy(legacy)
    for node in embedded['nodes']:node['vector'] = vector(1.,0.)
    value.commit(token,embedded)
    assert value.recompute_scope(scope,links.reconcile_batches)['saved']
    saved = value.read_saved(SID,RID)
    assert saved['policy_sha256'] == links.POLICY_SHA256
    assert saved['annotations'] and all('source_kind' not in a for a in saved['annotations'])
    assert value.snapshot(scope)['batches'] == [embedded]
    with pytest.raises(ReadError, match='speaker_link_policy_conflict'):
        value.reserve(SID,RID,modern['job_id'],modern['result_sha256'],
            links.POLICY_SHA256_V2,links.expected_space(),max_nodes=len(modern['nodes']),batch=modern)


def test_inventory_uses_one_fresh_authority_budget_across_all_batches(environment,monkeypatch):
    store, value, _ = environment
    for number in (1,2,3):
        token, _ = finish(value,prepared(store,number))
    budgets, boundaries = [], []
    original_budget, original_current = primary.Budget, primary.current_packets

    def budget():
        instance = original_budget()
        budgets.append(instance)
        return instance

    def current(*args,**kwargs):
        boundaries.append(kwargs['boundary'])
        return original_current(*args,**kwargs)

    monkeypatch.setattr(primary,'Budget',budget)
    monkeypatch.setattr(primary,'current_packets',current)
    snapshot = value.snapshot(token['scope_id'])
    assert len(budgets) == 1 and len(boundaries) == 3
    assert all(boundary is boundaries[0] for boundary in boundaries)
    decision = links.reconcile_batches(snapshot['evidence_batches'])
    assert value.save_decision(token['scope_id'],snapshot['revision'],decision,
        session_revision=snapshot['session_revision'],policy_sha256=snapshot['policy_sha256'])
    assert len(budgets) == 2 and len(boundaries) == 6
    assert all(boundary is boundaries[3] for boundary in boundaries[3:])
    assert boundaries[0] is not boundaries[3]
