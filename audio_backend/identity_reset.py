"""Forget a group's identity data without erasing its acoustic/text evidence."""
import json


GROUP_SESSIONS = 'SELECT id FROM sessions WHERE identity_group=? AND deleted=0'


def anonymous_conditioning(value):
    """Keep local source scope and raw-turn links, remove global person hints."""
    if isinstance(value, list):
        return [anonymous_conditioning(item) for item in value]
    if isinstance(value, dict):
        result = {key: anonymous_conditioning(item) for key, item in value.items()}
        if result.get('conditioned_on_speaker_id') is not None:
            result['conditioned_on_speaker_id'] = None
            result['raw_diarization_speaker_status'] = 'identity_reset'
        return result
    return value


def _page_rows(db, query, group):
    """A reset may cover years of history; read a bounded page at a time."""
    after = 0
    while True:
        rows = db.execute(query, (group, after)).fetchall()
        if not rows:
            return
        for row in rows:
            after = row['cursor']
            yield row


def reset_group(store, group):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM chunks WHERE status='running' LIMIT 1").fetchone():
            raise ValueError('Wait for worker to become idle')
        from .verified_identity import group_policy
        policy=group_policy(db,group)
        if policy and policy['mode']=='verified_review_v1':
            db.execute('INSERT OR IGNORE INTO identity_feed_suppression VALUES(?)',(group,))
        # An activated bank is real even with zero ordinary sessions. Preserve
        # its explicit retention/policy, but make every old request generation
        # unusable. Arbitrary reset URLs must not grow the generation table.
        retained = bool(policy and policy.get('primary_associations_enabled') == 1)
        if retained or db.execute('SELECT 1 FROM sessions WHERE identity_group=? AND deleted=0 LIMIT 1',(group,)).fetchone():
            import uuid
            db.execute('INSERT OR REPLACE INTO speaker_identity_generations VALUES(?,?)',(group,uuid.uuid4().hex))
        from .reviewed_primary_persons import schema_present
        if schema_present(db):
            # Erase the current projection before its immutable event owners,
            # across every generation. The shared reset below removes all event
            # actions, including source-free erasure receipts and activation.
            db.execute('DELETE FROM identity_primary_associations_v1 WHERE identity_group=?',(group,))
        from .verified_identity import reset
        reset(db,group)
        db.execute('DELETE FROM speaker_support WHERE identity_group=?',(group,))
        for table in ('segments', 'speaker_turns'):
            db.execute(f'''UPDATE {table} SET speaker_support_id=NULL,speaker_support_status='identity_reset'
                WHERE (speaker_support_id IS NOT NULL OR speaker_support_status IS NOT 'identity_reset')
                AND chunk_id IN (SELECT id FROM chunks WHERE session_id IN ({GROUP_SESSIONS}))''',(group,))
            db.execute(f'''UPDATE {table} SET speaker_id=NULL,
                speaker_status='identity_reset',speaker_match_score=NULL
                WHERE speaker_id IS NOT NULL AND chunk_id IN (
                    SELECT id FROM chunks WHERE session_id IN ({GROUP_SESSIONS}))''', (group,))
        db.execute('''UPDATE segments SET candidate_observation_id=NULL
            WHERE candidate_observation_id IN (
                SELECT id FROM speaker_observations WHERE identity_group=?)''', (group,))
        db.execute('DELETE FROM speaker_observations WHERE identity_group=?', (group,))
        # Explicit profile examples cascade with their speakers.
        db.execute('DELETE FROM speakers WHERE identity_group=?', (group,))

        for table in ('overlap_regions', 'overlap_tracks'):
            query = f'''SELECT id AS cursor,conditioning_evidence_json FROM {table}
                WHERE chunk_id IN (SELECT id FROM chunks WHERE session_id IN ({GROUP_SESSIONS}))
                AND id>? ORDER BY id LIMIT 64'''
            for row in _page_rows(db, query, group):
                original = json.loads(row['conditioning_evidence_json'])
                safe = anonymous_conditioning(original)
                if safe != original:
                    # Existing triggers append safe current snapshots for caught-up clients.
                    db.execute(f'UPDATE {table} SET conditioning_evidence_json=? WHERE id=?',
                               (json.dumps(safe), row['cursor']))

        # A saved cursor remains valid. Old speaker snapshots become anonymous
        # same-ID tombstones, so a fresh replay cannot recover forgotten labels.
        # Clients already holding labels receive the normal new DELETE records.
        db.execute(f'''UPDATE result_changes SET operation='delete',data=NULL
            WHERE session_id IN ({GROUP_SESSIONS}) AND entity='speakers'
            AND operation!='delete' ''', (group,))
        # Also cover historical snapshots by their recorded group, independent
        # of today's session membership (including a zero-session retained bank).
        db.execute('''UPDATE result_changes SET operation='delete',data=NULL
            WHERE entity='speakers' AND data IS NOT NULL
            AND json_extract(data,'$.identity_group')=?''', (group,))
        db.execute(f'''UPDATE result_changes SET data=json_set(data,
                '$.speaker_id',NULL,'$.speaker_match_score',NULL,'$.speaker_status','identity_reset')
            WHERE session_id IN ({GROUP_SESSIONS}) AND entity IN ('segments','speaker_turns')
            AND data IS NOT NULL AND json_extract(data,'$.speaker_id') IS NOT NULL''', (group,))
        query = f'''SELECT cursor,data FROM result_changes
            WHERE session_id IN ({GROUP_SESSIONS}) AND entity IN ('overlap_regions','overlap_tracks')
            AND data IS NOT NULL AND cursor>? ORDER BY cursor LIMIT 64'''
        for row in _page_rows(db, query, group):
            original = json.loads(row['data'])
            safe = dict(original)
            key = 'conditioning_evidence_json'
            if key in safe:
                evidence = json.loads(safe[key])
                anonymous = anonymous_conditioning(evidence)
                if anonymous != evidence:
                    safe[key] = json.dumps(anonymous)
            if safe != original:
                db.execute('UPDATE result_changes SET data=? WHERE cursor=?',
                           (json.dumps(safe), row['cursor']))

        db.execute(f"""UPDATE result_changes SET data=json_remove(data,'$.identity_decision_id','$.identity_profile_revision','$.identity_state')
            WHERE session_id IN ({GROUP_SESSIONS}) AND entity IN ('segments','speaker_turns') AND data IS NOT NULL""",(group,))
        # Bank history carries evidence-space/generation links; only the latest
        # reset snapshot remains visible, preserving monotonic feed cursors.
        db.execute(f"""UPDATE result_changes SET operation='delete',data=NULL WHERE session_id IN ({GROUP_SESSIONS})
            AND entity='identity_bank' AND data IS NOT NULL
            AND json_extract(data,'$.generation') != (SELECT generation FROM speaker_identity_generations WHERE identity_group=?)""",(group,group))
        # The private correction audit also contains labels and person links.
        # Retain action/segment/example provenance, not the forgotten identity.
        query = f'''SELECT id AS cursor,detail FROM corrections
            WHERE session_id IN ({GROUP_SESSIONS}) AND id>? ORDER BY id LIMIT 64'''
        for row in _page_rows(db, query, group):
            original = json.loads(row['detail'])
            safe = dict(original)
            for key in ('speaker_id', 'merge_into', 'name'):
                if key in safe:
                    safe[key] = None
            if safe != original:
                safe['identity_reset'] = True
                db.execute('UPDATE corrections SET detail=? WHERE id=?',
                           (json.dumps(safe), row['cursor']))
        db.execute('DELETE FROM identity_feed_suppression WHERE identity_group=?',(group,))
    return {'identity_group': group, 'reset': True}
