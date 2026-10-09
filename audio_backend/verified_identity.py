"""Experimental explicit-review identity bank. No automatic learning.

All writes share the result store transaction. This bank never reads legacy
embeddings. Assertions are recorded operator claims, not proven speaker purity.
"""
import json
import time
import uuid
from pathlib import Path
from . import speaker_support as support, support_vectors, identity_canonical

MODE = 'verified_review_v1'
ATTESTATION = 'all_pooled_support_single_identified_voice_v1'
MAX_GROUPS = 1000
MAX_PERSONS = 1024
MAX_POSITIVES = 4096
MAX_NEGATIVES = 8192
MAX_EVENTS = 32768
MAX_PER_PERSON = 8
POLICY_KEYS = {'schema_version','experimental','single_threshold','centroid_threshold','margin',
               'minimum_query_seconds','minimum_enrollment_seconds'}


def policy_file(path):
    path=Path(path)
    if path.stat().st_size>8192:
        raise ValueError('Verified policy exceeds 8 KiB')
    with path.open('rb') as source:
        raw=source.read(8193)
    if len(raw)>8192:
        raise ValueError('Verified policy exceeds 8 KiB')
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != POLICY_KEYS or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['experimental'] is not True):
        raise ValueError('An explicit experimental verified policy is required')
    for key in ('single_threshold','centroid_threshold','margin'):
        if not support.finite(value[key]) or not 0 <= value[key] <= 1:
            raise ValueError('Invalid verified matching policy')
    if (not support.finite(value['minimum_query_seconds']) or
            not support.finite(value['minimum_enrollment_seconds']) or
            not .5 <= value['minimum_query_seconds'] <= value['minimum_enrollment_seconds'] <= 20):
        raise ValueError('Invalid verified duration policy')
    return value


def init_schema(db):
    support_vectors.init_schema(db)
    db.execute('''CREATE TABLE IF NOT EXISTS identity_group_policy (
        identity_group TEXT PRIMARY KEY, mode TEXT NOT NULL, policy TEXT NOT NULL, policy_sha256 TEXT NOT NULL,
        space_id TEXT, revision INTEGER NOT NULL DEFAULT 0, availability INTEGER NOT NULL DEFAULT 0)''')
    if 'change_reason' not in {row['name'] for row in db.execute('PRAGMA table_info(identity_group_policy)')}:
        db.execute('ALTER TABLE identity_group_policy ADD COLUMN change_reason TEXT')
    db.execute('CREATE TABLE IF NOT EXISTS identity_feed_suppression(identity_group TEXT PRIMARY KEY)')
    db.execute('''CREATE TRIGGER IF NOT EXISTS identity_policy_immutable BEFORE UPDATE OF mode,policy,policy_sha256 ON identity_group_policy
        BEGIN SELECT RAISE(ABORT,'Group identity mode and policy are immutable'); END''')
    db.execute('''CREATE TABLE IF NOT EXISTS identity_review_events (
        id TEXT PRIMARY KEY, identity_group TEXT NOT NULL, generation TEXT NOT NULL,
        sequence INTEGER NOT NULL, action TEXT NOT NULL, person_id TEXT,
        support_id TEXT, vector_id TEXT, evidence_key TEXT, fingerprints TEXT,
        attestation TEXT, operator_context TEXT NOT NULL, prior_prediction TEXT,
        previous_event_id TEXT, idempotency_key TEXT, request_sha256 TEXT,
        response TEXT NOT NULL, created REAL NOT NULL,
        UNIQUE(identity_group,generation,idempotency_key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS identity_review_event_order (
        cursor INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE REFERENCES identity_review_events(id) ON DELETE CASCADE)''')
    db.execute('''INSERT OR IGNORE INTO identity_review_event_order(event_id) SELECT id FROM identity_review_events ORDER BY created,id''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS identity_review_order AFTER INSERT ON identity_review_events
        BEGIN INSERT INTO identity_review_event_order(event_id) VALUES(NEW.id); END''')
    if 'review_kind' not in {row['name'] for row in db.execute('PRAGMA table_info(identity_review_events)')}:
        db.execute('ALTER TABLE identity_review_events ADD COLUMN review_kind TEXT')
    db.execute('CREATE INDEX IF NOT EXISTS review_events_generation ON identity_review_events(identity_group,generation)')
    db.execute('CREATE INDEX IF NOT EXISTS review_events_previous ON identity_review_events(previous_event_id)')
    db.execute('CREATE INDEX IF NOT EXISTS review_events_group ON identity_review_events(identity_group,sequence)')
    db.execute('''CREATE TRIGGER IF NOT EXISTS review_events_immutable BEFORE UPDATE ON identity_review_events
        BEGIN SELECT RAISE(ABORT,'Review events are immutable'); END''')
    db.execute('''CREATE TABLE IF NOT EXISTS identity_active_reviews (
        event_id TEXT PRIMARY KEY REFERENCES identity_review_events(id) ON DELETE CASCADE,
        identity_group TEXT NOT NULL, generation TEXT NOT NULL, kind TEXT NOT NULL,
        person_id TEXT NOT NULL, support_id TEXT NOT NULL REFERENCES speaker_support(id),
        evidence_key TEXT NOT NULL, space_id TEXT NOT NULL,
        UNIQUE(identity_group,kind,person_id,evidence_key))''')
    db.execute('CREATE INDEX IF NOT EXISTS active_reviews_support ON identity_active_reviews(support_id)')
    db.execute('CREATE INDEX IF NOT EXISTS active_reviews_group ON identity_active_reviews(identity_group,kind,person_id)')
    # Immutable decision history is separate from its mutable validity projection.
    db.execute('''CREATE TABLE IF NOT EXISTS identity_decisions (
        id TEXT PRIMARY KEY, identity_group TEXT NOT NULL, generation TEXT NOT NULL,
        chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE, window_start REAL NOT NULL,
        local_id TEXT NOT NULL, snapshot TEXT NOT NULL, UNIQUE(chunk_id,window_start,local_id))''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS identity_decisions_immutable BEFORE UPDATE ON identity_decisions
        BEGIN SELECT RAISE(ABORT,'Identity decisions are immutable'); END''')
    db.execute('''CREATE TABLE IF NOT EXISTS identity_decision_state (
        decision_id TEXT PRIMARY KEY REFERENCES identity_decisions(id) ON DELETE CASCADE,
        stale INTEGER NOT NULL DEFAULT 0, reason TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS identity_decisions_group ON identity_decisions(identity_group)')
    db.execute('''CREATE TABLE IF NOT EXISTS identity_decision_sources (
        decision_id TEXT NOT NULL REFERENCES identity_decisions(id) ON DELETE CASCADE,
        chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE, PRIMARY KEY(decision_id,chunk_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS decision_sources_chunk ON identity_decision_sources(chunk_id)')
    identity_canonical.init_schema(db)


def install_lifecycle(db):
    # Revocation history is bounded by reserved slots. Public staleness is an
    # O(1) gallery revision invalidation, never millions of result-row updates.
    db.execute('DROP TRIGGER IF EXISTS identity_review_support_expiry')
    db.execute('''CREATE TRIGGER identity_review_support_expiry BEFORE DELETE ON speaker_support
        WHEN EXISTS(SELECT 1 FROM identity_active_reviews WHERE support_id=OLD.id) BEGIN
        INSERT INTO identity_review_events
            (id,identity_group,generation,sequence,action,person_id,support_id,vector_id,evidence_key,
             fingerprints,attestation,operator_context,prior_prediction,previous_event_id,idempotency_key,
             request_sha256,response,created)
        SELECT lower(hex(randomblob(16))),a.identity_group,a.generation,p.revision+1,'source_expired',
            a.person_id,a.support_id,NULL,a.evidence_key,NULL,NULL,'source_lifecycle',NULL,a.event_id,NULL,NULL,
            '{}',(julianday('now')-2440587.5)*86400
        FROM identity_active_reviews a JOIN identity_group_policy p ON p.identity_group=a.identity_group
        WHERE a.support_id=OLD.id;
        UPDATE identity_group_policy SET revision=revision+1,availability=availability+1,change_reason='source_expired'
            WHERE identity_group=OLD.identity_group;
        DELETE FROM identity_active_reviews WHERE support_id=OLD.id;
        END''')
    db.execute('DROP TRIGGER IF EXISTS identity_query_source_expiry')
    # Once one affected current decision invalidates its gallery, subsequent
    # deletes from the same recording cannot emit unbounded duplicate events.
    affected = """SELECT d.identity_group FROM identity_decisions d
        JOIN identity_decision_sources src ON src.decision_id=d.id
        JOIN identity_group_policy p ON p.identity_group=d.identity_group
        WHERE src.chunk_id=OLD.id AND json_extract(d.snapshot,'$.profile_revision')=p.revision"""
    db.execute(f'''CREATE TRIGGER identity_query_source_expiry BEFORE DELETE ON chunks BEGIN
        UPDATE identity_group_policy SET revision=revision+1,availability=availability+1,change_reason='query_source_expired'
            WHERE identity_group IN ({affected}); END''')


def group_policy(db, group):
    row = db.execute('SELECT * FROM identity_group_policy WHERE identity_group=?', (group,)).fetchone()
    return dict(row) if row else None


def is_verified(db, sid):
    row = db.execute('''SELECT p.mode FROM identity_group_policy p JOIN sessions s
        ON s.identity_group=p.identity_group WHERE s.id=? AND s.deleted=0''', (sid,)).fetchone()
    return bool(row and row['mode'] == MODE)


def create_group(store, db, group, requested_mode):
    existing = group_policy(db, group)
    if existing:
        if requested_mode is not None and requested_mode != existing['mode']:
            raise ValueError('identity_mode is immutable for an existing group')
        return
    # Old databases have no policy row: a live legacy group must never convert.
    legacy = db.execute('SELECT 1 FROM sessions WHERE identity_group=? AND deleted=0 UNION ALL SELECT 1 FROM speakers WHERE identity_group=? LIMIT 1', (group,group)).fetchone()
    mode = 'legacy' if legacy or requested_mode is None else requested_mode
    if requested_mode is not None and requested_mode != mode:
        raise ValueError('Existing legacy identity group cannot convert')
    if mode not in ('legacy', MODE):
        raise ValueError('Unsupported identity_mode')
    if db.execute('SELECT COUNT(*) FROM identity_group_policy').fetchone()[0] >= MAX_GROUPS:
        raise OverflowError('Identity group policy capacity reached')
    policy = {}
    if mode == MODE:
        policy = store.settings.verified_identity_policy
        if not store.settings.speaker_support_enabled or policy is None:
            raise ValueError('verified_review_v1 requires review capture and explicit experimental policy')
    encoded = support.canonical(policy)
    db.execute('INSERT INTO identity_group_policy(identity_group,mode,policy,policy_sha256) VALUES(?,?,?,?)',
        (group, mode, encoded.decode(), support.digest(encoded)))
    if mode==MODE:support.generation(db, group)


def _context(db, sid):
    session = db.execute('SELECT * FROM sessions WHERE id=? AND deleted=0', (sid,)).fetchone()
    if not session:
        raise KeyError('Session not found')
    group = session['identity_group']
    policy = group_policy(db, group)
    if not policy or policy['mode'] != MODE:
        raise ValueError('Explicit verified_review_v1 identity group required')
    current=db.execute('SELECT generation FROM speaker_identity_generations WHERE identity_group=?',(group,)).fetchone()
    if not current:raise ValueError('Identity generation unavailable')
    return group,current['generation'],policy


def _invalidate(db, group, reason):
    db.execute('''UPDATE identity_group_policy SET revision=revision+1,availability=availability+1,
        change_reason=? WHERE identity_group=?''', (reason,group))


def project_result(row, policy, canonical=None):
    """Bounded response projection; immutable history keeps original prediction."""
    original = row.get('speaker_id')
    if row.get('identity_decision_id') is not None and policy:
        stale=int(row['identity_profile_revision'])!=policy['revision']
        row['identity_profile_revision']=int(row['identity_profile_revision'])
        row['identity_state']='stale' if stale else 'current'
        if stale and not row.get('speaker_status','').startswith('human_'):
            row.update(speaker_id=None,speaker_match_score=None,speaker_status='identity_stale')
    if policy and policy['mode'] == MODE:
        row['original_speaker_id'] = original
        row['canonical_revision'] = policy['revision']
        row['canonical_state'] = 'current' if canonical else 'unavailable'
        if row.get('speaker_id') is not None:
            row['speaker_id'] = canonical['mapping'].get(row['speaker_id']) if canonical else None
            if row['speaker_id'] is None:
                row['canonical_state'] = 'unavailable'
    return row


def _request(db, group, generation, policy, request, action, previous):
    if request.get('expected_generation') != generation:
        raise ValueError('stale_generation')
    key = request.get('idempotency_key')
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise ValueError('Idempotency key required (1–128 characters)')
    hashed = support.digest(support.canonical({'action': action, 'previous': previous, 'request': request}))
    prior = db.execute('''SELECT request_sha256,response FROM identity_review_events
        WHERE identity_group=? AND generation=? AND idempotency_key=?''', (group,generation,key)).fetchone()
    if prior:
        if prior['request_sha256'] != hashed:
            raise ValueError('idempotency_conflict')
        return hashed, json.loads(prior['response'])
    if db.execute('''SELECT 1 FROM identity_relation_events
        WHERE identity_group=? AND generation=? AND idempotency_key=?''', (group,generation,key)).fetchone():
        raise ValueError('idempotency_conflict')
    if type(request.get('expected_revision')) is not int or request['expected_revision'] != policy['revision']:
        raise ValueError('stale_profile_revision')
    return hashed, None


def _evidence(db, sid, generation, policy, request):
    row = support._row(db, sid, request.get('support_id'), with_pcm=True)
    manifest=support.validate_pcm_evidence(row,row['pcm'])
    if row['generation']!=generation:
        raise ValueError('Exact retained acoustic support generation unavailable')
    raw, vector = support_vectors.read(db, row['id'])
    if raw['generation'] != generation or raw['vector_sha256'] != manifest['embedding_sha256']:
        raise ValueError('Original support/vector binding unavailable')
    expected = {'support_id': row['id'], 'vector_id': raw['id'], 'manifest_sha256': row['manifest_sha256'],
                'vector_sha256': raw['vector_sha256'], 'space_id': raw['space_id']}
    if any(request.get(k) != v for k, v in expected.items()):
        raise ValueError('Review evidence binding mismatch')
    if policy['space_id'] is not None and policy['space_id'] != raw['space_id']:
        raise ValueError('incompatible_embedding_space')
    return raw, manifest, expected


def _capacity(db, kind, person, replacing=None, canonical=None):
    history = db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0]
    active = db.execute('SELECT COUNT(*) FROM identity_active_reviews').fetchone()[0]
    # Admission uses one history slot and reserves one later revocation. A
    # replacement appends one event and carries its predecessor's reservation.
    if history + active + (1 if replacing else 2) > MAX_EVENTS:
        raise OverflowError('Review event capacity reached; retraction remains available')
    count = db.execute('SELECT COUNT(*) FROM identity_active_reviews WHERE kind=?', (kind,)).fetchone()[0]
    removed = bool(replacing and replacing['kind'] == kind)
    if count + 1 - int(removed) > (MAX_POSITIVES if kind == 'positive' else MAX_NEGATIVES):
        raise OverflowError('Active review capacity reached')
    if kind == 'positive':
        root = identity_canonical.review_root(canonical, person)
        count = sum(identity_canonical.review_root(canonical, row['person_id']) == root
                    for row in db.execute("SELECT person_id FROM identity_active_reviews WHERE kind='positive' AND identity_group=?",
                                          (canonical['identity_group'],)))
        removed = bool(replacing and replacing['kind'] == kind
                       and identity_canonical.review_root(canonical, replacing['person_id']) == root)
        if count + 1 - int(removed) > MAX_PER_PERSON:
            raise OverflowError('At most eight active positive references per person')


def mutate(store, sid, request, *, previous=None, retract=False, operator_context='local_api'):
    """Explicit positive/negative/retract/atomic replacement, never line-label inference."""
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        group, generation, policy = _context(db, sid)
        action = 'retract' if retract else ('replace' if previous else request.get('kind'))
        if action not in ('positive','negative','retract','replace'):
            raise ValueError('Review kind must be positive or negative')
        hashed, retry = _request(db, group, generation, policy, request, action, previous)
        if retry is not None:
            return retry
        old = None
        if previous:
            old = db.execute('''SELECT * FROM identity_active_reviews
                WHERE event_id=? AND identity_group=? AND generation=?''', (previous,group,generation)).fetchone()
            if not old:
                raise ValueError('Active review to retract/replace not found')
        if retract and old is None:
            raise ValueError('Retraction requires an active review')
        raw = manifest = fingerprints = None
        person = old['person_id'] if old else None
        if not retract:
            canonical = identity_canonical.load(db, group, generation, policy)
            if not projection_intact(db, group, generation):
                raise ValueError('unknown_reference_integrity')
            kind = request.get('kind')
            if kind not in ('positive','negative'):
                raise ValueError('Review kind must be positive or negative')
            raw, manifest, fingerprints = _evidence(db,sid,generation,policy,request)
            assertion = request.get('attestation')
            if not isinstance(assertion, dict) or set(assertion) != {'version','reviewed_all_pooled_support','single_identified_voice','no_other_overlapping_voice'}:
                raise ValueError('Explicit full-support operator assertion required')
            if assertion['version'] != ATTESTATION or assertion['reviewed_all_pooled_support'] is not True:
                raise ValueError('Review must cover all pooled support')
            if kind == 'positive' and (assertion['single_identified_voice'] is not True or assertion['no_other_overlapping_voice'] is not True):
                raise ValueError('Mixed or uncertain support cannot be a positive reference')
            seconds = manifest['support']['pooled_sample_count'] / support.RATE
            if kind == 'positive' and seconds < json.loads(policy['policy'])['minimum_enrollment_seconds']:
                raise ValueError('Insufficient full-support duration for positive enrollment')
            person, name = request.get('person_id'), request.get('new_person_name')
            if bool(person) == bool(name) or (kind == 'negative' and name):
                raise ValueError('Specify one existing person or a positive new person name')
            if person:
                if not db.execute('SELECT 1 FROM speakers WHERE id=? AND identity_group=? AND merged_into IS NULL', (person,group)).fetchone():
                    raise KeyError('Target person not found in this identity group')
            else:
                if not isinstance(name,str) or not 1 <= len(name) <= 200:
                    raise ValueError('Invalid new person name')
                if db.execute("SELECT COUNT(*) FROM speakers WHERE state='reviewed_reference'").fetchone()[0] >= MAX_PERSONS:
                    raise OverflowError('Verified person capacity reached')
                person = uuid.uuid4().hex
            for claim in db.execute('''SELECT * FROM identity_active_reviews WHERE identity_group=? AND evidence_key=?''', (group,raw['evidence_key'])):
                if old and claim['event_id'] == old['event_id']:
                    continue
                if kind == 'positive' and claim['kind'] == 'positive':
                    raise ValueError('duplicate_or_conflicting_positive_evidence')
                if identity_canonical.review_root(canonical, claim['person_id']) == identity_canonical.review_root(canonical, person):
                    raise ValueError('duplicate_or_contradictory_review')
            _capacity(db, kind, person, old, canonical)
            if name:
                db.execute('''INSERT INTO speakers(id,session_id,identity_group,name,embedding,state,created)
                    VALUES(?,?,?,?,NULL,'reviewed_reference',?)''', (person,sid,group,name,time.time()))
        event_id = uuid.uuid4().hex
        response = {'event_id': event_id, 'person_id': person, 'action': action, 'generation': generation,
                    'profile_revision': policy['revision'] + 1, 'operator_assertion_is_claim': True}
        prediction = None
        if raw:
            prediction = db.execute('''SELECT snapshot FROM identity_decisions WHERE id IN
                (SELECT identity_decision_id FROM segments WHERE speaker_support_id=? LIMIT 1)''', (raw['support_id'],)).fetchone()
        db.execute('''INSERT INTO identity_review_events
            (id,identity_group,generation,sequence,action,person_id,support_id,vector_id,evidence_key,
             fingerprints,attestation,operator_context,prior_prediction,previous_event_id,idempotency_key,
             request_sha256,response,created,review_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (event_id,group,generation,policy['revision']+1,action,person,raw['support_id'] if raw else old['support_id'],
             raw['id'] if raw else None,raw['evidence_key'] if raw else old['evidence_key'],
             support.canonical(fingerprints).decode() if fingerprints else None,
             support.canonical(request['attestation']).decode() if raw else None,operator_context,
             prediction[0] if prediction else None,previous,request['idempotency_key'],hashed,
             support.canonical(response).decode(),time.time(),request.get('kind') if raw else None))
        if old:
            db.execute('DELETE FROM identity_active_reviews WHERE event_id=?', (old['event_id'],))
        if raw:
            db.execute('INSERT INTO identity_active_reviews VALUES(?,?,?,?,?,?,?,?)',
                (event_id,group,generation,request['kind'],person,raw['support_id'],raw['evidence_key'],raw['space_id']))
            db.execute('UPDATE identity_group_policy SET space_id=COALESCE(space_id,?) WHERE identity_group=?', (raw['space_id'],group))
        _invalidate(db,group,'bank_revision_changed')
        return response


def reset(db, group):
    # Delete active state first so support erasure does not retain expiry audit.
    identity_canonical.reset(db, group)
    db.execute('DELETE FROM identity_active_reviews WHERE identity_group=?', (group,))
    db.execute('DELETE FROM identity_review_events WHERE identity_group=?', (group,))
    db.execute('DELETE FROM identity_decisions WHERE identity_group=?', (group,))
    db.execute("UPDATE identity_group_policy SET space_id=NULL,revision=revision+1,availability=availability+1,change_reason='identity_reset' WHERE identity_group=?", (group,))
    for table in ('segments','speaker_turns'):
        db.execute(f'''UPDATE {table} SET identity_decision_id=NULL,identity_profile_revision=NULL,identity_state=NULL,
            speaker_id=NULL,speaker_match_score=NULL,speaker_status='identity_reset' WHERE identity_decision_id IS NOT NULL AND chunk_id IN
            (SELECT c.id FROM chunks c JOIN sessions s ON s.id=c.session_id WHERE s.identity_group=?)''', (group,))


def bank(store, sid):
    with store.db() as db:
        db.execute('BEGIN')
        group, generation, policy = _context(db,sid)
        counts = {row['kind']: row['n'] for row in db.execute('''SELECT kind,COUNT(*) AS n FROM identity_active_reviews
            WHERE identity_group=? GROUP BY kind''', (group,))}
        return {'identity_mode': MODE, 'generation': generation, 'profile_revision': policy['revision'],
                'availability_sequence': policy['availability'], 'policy_sha256': policy['policy_sha256'],
                'policy': json.loads(policy['policy']), 'space_id': policy['space_id'], 'active_counts': counts,
                'limits': {'persons_global':MAX_PERSONS,'positives_global':MAX_POSITIVES,
                           'negatives_global':MAX_NEGATIVES,'events_global':MAX_EVENTS,'positives_per_person':MAX_PER_PERSON,
                           'relation_events_global':identity_canonical.MAX_EVENTS},
                'relation_event_count':policy['relation_count'],
                'experimental':True, 'warning':'Operator assertions are claims, not proof of correct identity or acoustic purity.'}


def projection_intact(db,group,generation):
    """Validate the immutable current-generation gallery before index filters.

    At most MAX_EVENTS rows exist globally; the indexed ledger pass reads only
    this group/generation. Revocation lookups use their previous-event index.
    The second indexed pass checks only this group's active projection for extras.
    No vectors or transcript/decision history are materialized by either pass.
    """
    expected="""SELECT e.* FROM identity_review_events e INDEXED BY review_events_generation
        WHERE e.identity_group=? AND e.generation=? AND e.action IN ('positive','negative','replace')
        AND NOT EXISTS(SELECT 1 FROM identity_review_events r WHERE r.previous_event_id=e.id
            AND r.identity_group=e.identity_group AND r.generation=e.generation
            AND r.action IN ('retract','replace','source_expired'))"""
    # Scope starts with immutable ownership, so moved/missing index rows cannot
    # hide a competitor. json_valid turns damaged metadata into an abstention.
    invalid=db.execute(f'''SELECT 1 FROM ({expected}) e
        LEFT JOIN identity_active_reviews a ON a.event_id=e.id
        LEFT JOIN speakers p ON p.id=e.person_id
        LEFT JOIN speaker_support_vectors v ON v.id=e.vector_id
        LEFT JOIN speaker_support s ON s.id=e.support_id
        LEFT JOIN sessions sess ON sess.id=s.session_id
        WHERE a.event_id IS NULL OR e.identity_group IS NOT a.identity_group
        OR e.generation IS NOT a.generation OR e.person_id IS NOT a.person_id OR e.support_id IS NOT a.support_id
        OR e.evidence_key IS NOT a.evidence_key
        OR COALESCE(e.review_kind,CASE WHEN e.action IN ('positive','negative') THEN e.action END) IS NOT a.kind
        OR p.id IS NULL OR p.identity_group IS NOT e.identity_group OR p.merged_into IS NOT NULL
        OR v.id IS NULL OR v.support_id IS NOT e.support_id OR v.identity_group IS NOT e.identity_group
        OR v.generation IS NOT e.generation OR v.space_id IS NOT a.space_id OR v.evidence_key IS NOT a.evidence_key
        OR s.id IS NULL OR s.identity_group IS NOT e.identity_group OR s.generation IS NOT e.generation
        OR s.status IS NOT 'available' OR s.pcm_bytes<=0 OR length(s.pcm) IS NOT s.pcm_bytes
        OR sess.deleted IS NOT 0 OR sess.identity_group IS NOT e.identity_group
        OR CASE WHEN json_valid(e.fingerprints) THEN json_extract(e.fingerprints,'$.space_id') END IS NOT a.space_id
        OR CASE WHEN json_valid(e.fingerprints) THEN json_extract(e.fingerprints,'$.vector_id') END IS NOT v.id
        OR CASE WHEN json_valid(e.fingerprints) THEN json_extract(e.fingerprints,'$.vector_sha256') END IS NOT v.vector_sha256
        OR CASE WHEN json_valid(e.fingerprints) THEN json_extract(e.fingerprints,'$.manifest_sha256') END IS NOT s.manifest_sha256
        LIMIT 1''',(group,generation)).fetchone()
    if invalid:return False
    # An injected active row must not introduce evidence absent from the ledger's
    # expected set. This index is group-leading, not a global projection scan.
    return not db.execute('''SELECT 1 FROM identity_active_reviews a INDEXED BY active_reviews_group
        LEFT JOIN identity_review_events e ON e.id=a.event_id WHERE a.identity_group=? AND
        (e.id IS NULL OR e.identity_group IS NOT ? OR e.generation IS NOT ?
        OR e.action NOT IN ('positive','negative','replace')
        OR EXISTS(SELECT 1 FROM identity_review_events r WHERE r.previous_event_id=e.id
            AND r.identity_group=e.identity_group AND r.generation=e.generation
            AND r.action IN ('retract','replace','source_expired'))) LIMIT 1''',
        (group,group,generation)).fetchone()


def reference_vector(db,active):
    raw,vector=support_vectors.read(db,active['support_id'])
    event=db.execute('SELECT fingerprints,vector_id FROM identity_review_events WHERE id=?',(active['event_id'],)).fetchone()
    if event is None:raise ValueError('Missing review event')
    fingerprints=json.loads(event['fingerprints'])
    owner=db.execute('SELECT manifest_sha256 FROM speaker_support WHERE id=?',(active['support_id'],)).fetchone()
    expected={'support_id':raw['support_id'],'vector_id':raw['id'],'manifest_sha256':owner['manifest_sha256'],
              'vector_sha256':raw['vector_sha256'],'space_id':raw['space_id']}
    if (fingerprints!=expected or event['vector_id']!=raw['id'] or raw['evidence_key']!=active['evidence_key']
            or raw['identity_group']!=active['identity_group'] or raw['generation']!=active['generation']
            or raw['space_id']!=active['space_id']):
        raise ValueError('Active review differs from immutable evidence')
    return raw,vector


def resolve(store, db, chunk, offset, group, local_id, metadata, prepared, retained):
    """Match a transient query; retention and learning are independent decisions."""
    import math
    policy = group_policy(db,group)
    generation = support.generation(db,group)
    params = json.loads(policy['policy'])
    proof = None
    reason = 'unknown_query_proof'
    try:
        if not isinstance(prepared,dict) or prepared.get('generation') != generation:
            raise ValueError('Stale query generation')
        proof = support_vectors.query_proof(prepared.get('raw_vector'),metadata,prepared)
        for part in prepared['manifest']['source_parts']:
            source=db.execute('SELECT session_id,digest FROM chunks WHERE id=?',(part['chunk_id'],)).fetchone()
            if not source or source['session_id']!=chunk['session_id'] or source['digest']!=part['source_sha256']:
                proof=None
                raise ValueError('Query source lifecycle mismatch')
    except (ValueError,TypeError,KeyError,AttributeError):
        pass
    scores=[]
    canonical = identity_canonical.snapshot(db, group, policy)
    if proof:
        if canonical is None or not projection_intact(db,group,generation):
            reason='unknown_reference_integrity'
        elif policy['space_id'] is not None and proof['space_id'] != policy['space_id']:
            reason='unknown_embedding_space'
        elif proof['seconds'] < params['minimum_query_seconds']:
            reason='unknown_insufficient_speech'
        else:
            person=None;vectors=[];references=[];invalid_gallery=False
            def score_person():
                nonlocal invalid_gallery
                if not vectors:return
                try:
                    centroid,_=support_vectors.normalized_vector([math.fsum(col)/len(vectors) for col in zip(*vectors)])
                except ValueError:
                    invalid_gallery=True;return
                score=max(-1.,min(1.,math.fsum(a*b for a,b in zip(centroid,proof['vector']))))
                scores.append((score,person,list(references)))
            # Sort bounded metadata only. Raw vectors still stream one canonical
            # person's at-most-eight reviewed references at a time.
            gallery = sorted(db.execute('''SELECT a.* FROM identity_active_reviews a
                    WHERE a.identity_group=? AND a.generation=? AND a.kind='positive' AND a.space_id=?
                    ORDER BY a.person_id,a.event_id''', (group,generation,proof['space_id'])),
                    key=lambda row:(canonical['mapping'][row['person_id']],row['event_id']))
            for active in gallery:
                root=canonical['mapping'][active['person_id']]
                if root!=person:
                    score_person();person=root;vectors=[];references=[]
                try:
                    if len(vectors)>=MAX_PER_PERSON:
                        raise ValueError('Canonical reference capacity exceeded')
                    raw,vector=reference_vector(db,active)
                    if raw['space_id']!=proof['space_id'] or raw['generation']!=generation or raw['identity_group']!=group:
                        raise ValueError('Anchor binding changed')
                    vectors.append(vector);references.append(active['event_id'])
                except (ValueError,TypeError,KeyError):
                    # Do not silently score a partially corrupted gallery.
                    scores=[];vectors=[];reason='unknown_reference_integrity';break
            else:
                score_person();reason='unknown_no_reviewed_references'
            if invalid_gallery:scores=[];reason='unknown_reference_integrity'
    scores.sort(key=lambda row:(-row[0],row[1]))
    person=None;best=None;margin=None;references=[];negative_event_id=None;candidate=None
    if scores:
        best, candidate, references=scores[0]
        margin=best-scores[1][0] if len(scores)>1 else 2.
        threshold=params['centroid_threshold'] if len(references)>1 else params['single_threshold']
        veto=next((row for row in db.execute("""SELECT * FROM identity_active_reviews
            WHERE identity_group=? AND generation=? AND kind='negative' AND evidence_key=? ORDER BY event_id""",
            (group,generation,proof['evidence_key']))
            if canonical['mapping'][row['person_id']]==candidate),None)
        if veto:
            try:
                reference_vector(db,veto)
                negative_event_id=veto['event_id']
                reason='unknown_exact_negative'
            except (ValueError,TypeError,KeyError):
                reason='unknown_reference_integrity'
        elif best<threshold:
            reason='unknown_below_threshold'
        elif margin<params['margin']:
            reason='unknown_ambiguous'
        else:
            person=candidate;reason='experimental_identity_match'
    support_id,support_status=retained
    vector_row=db.execute('SELECT id FROM speaker_support_vectors WHERE support_id=?',(support_id,)).fetchone() if support_id else None
    review_reason=('available' if vector_row else ('vector_proof_unavailable' if support_status=='available' else support_status))
    decision_id=uuid.uuid4().hex
    snapshot={'identity_mode':MODE,'basis':'reviewed_reference_bank','experimental':True,
        'generation':generation,'profile_revision':policy['revision'],'availability_sequence':policy['availability'],
        'policy_sha256':policy['policy_sha256'],'space_id':proof['space_id'] if proof else None,
        'query_evidence_key':proof['evidence_key'] if proof else None,
        'query_vector_sha256':proof['vector_sha256'] if proof else None,
        'original_person_id':person,'candidate_person_id':candidate,'score':best,'margin':margin,'status':reason,
        'negative_event_id':negative_event_id,
        'reference_event_ids':references,'support_id':support_id,'review_availability':review_reason,
        'automatic_query_is_human_verified':False}
    db.execute('INSERT INTO identity_decisions VALUES(?,?,?,?,?,?,?)',
        (decision_id,group,generation,chunk['id'],offset,str(local_id),support.canonical(snapshot).decode()))
    db.execute('INSERT INTO identity_decision_state(decision_id) VALUES(?)',(decision_id,))
    # Dependencies survive a full review quota. No raw query/PCM is retained here.
    parts=prepared.get('manifest',{}).get('source_parts',[]) if isinstance(prepared,dict) else []
    source_ids={chunk['id']}
    if isinstance(parts,list) and len(parts)<=support.MAX_PARTS:
        source_ids.update(p['chunk_id'] for p in parts if isinstance(p,dict) and isinstance(p.get('chunk_id'),str))
    for cid in source_ids:
        if db.execute('SELECT 1 FROM chunks WHERE id=? AND session_id=?',(cid,chunk['session_id'])).fetchone():
            db.execute('INSERT INTO identity_decision_sources VALUES(?,?)',(decision_id,cid))
    return person,best,reason,decision_id,policy['revision']


def decision(store,sid,decision_id):
    store.session(sid)
    with store.db() as db:
        row=db.execute('''SELECT d.snapshot,s.stale,s.reason,p.revision,p.change_reason FROM identity_decisions d JOIN identity_group_policy p ON p.identity_group=d.identity_group
            JOIN chunks c ON c.id=d.chunk_id JOIN identity_decision_state s ON s.decision_id=d.id
            WHERE d.id=? AND c.session_id=?''',(decision_id,sid)).fetchone()
        if not row:raise KeyError('Identity decision not found in session')
        snapshot=json.loads(row['snapshot'])
        stale=bool(row['stale'] or snapshot['profile_revision']!=row['revision'])
        return {'decision_id':decision_id,**snapshot,
                'state':'stale' if stale else 'current','stale_reason':(row['reason'] or row['change_reason']) if stale else None}


def events(store,sid,after=0,limit=100):
    with store.db() as db:
        db.execute('BEGIN')
        group,generation,_=_context(db,sid)
        rows=[dict(row) for row in db.execute('''SELECT o.cursor,e.id,e.sequence,e.action,e.review_kind,e.person_id,e.previous_event_id,
            e.attestation,e.operator_context,e.created,CASE WHEN a.event_id IS NULL THEN 0 ELSE 1 END AS active
            FROM identity_review_events e JOIN identity_review_event_order o ON o.event_id=e.id LEFT JOIN identity_active_reviews a ON a.event_id=e.id
            WHERE e.identity_group=? AND e.generation=? AND o.cursor>? ORDER BY o.cursor LIMIT ?''',
            (group,generation,after,limit))]
        # Expiry may emit multiple events at one revision: use an event ID cursor
        # for exact pagination rather than accepting a lossy revision boundary.
        for row in rows:
            row['attestation']=json.loads(row['attestation']) if row['attestation'] else None
            row['active']=bool(row['active']);row['operator_assertion_is_claim']=True
            row['detail_url']=f'/sessions/{sid}/identity-reviews/{row["id"]}'
        return {'events':rows,'generation':generation,'next_cursor':rows[-1]['cursor'] if rows else after}


def event_detail(store,sid,event_id):
    """Resolve one opaque decision reference back to its reviewable source."""
    with store.db() as db:
        db.execute('BEGIN')
        group,generation,_=_context(db,sid)
        row=db.execute('''SELECT e.id,e.sequence,e.action,e.review_kind,e.person_id,e.previous_event_id,
            e.support_id,e.attestation,e.operator_context,e.created,
            CASE WHEN a.event_id IS NULL THEN 0 ELSE 1 END AS active
            FROM identity_review_events e LEFT JOIN identity_active_reviews a ON a.event_id=e.id
            WHERE e.id=? AND e.identity_group=? AND e.generation=?''',(event_id,group,generation)).fetchone()
        if row is None:raise KeyError('Review event not found in this identity group and generation')
        result=dict(row)
        support_id=result.pop('support_id')
        result['attestation']=json.loads(result['attestation']) if result['attestation'] else None
        result['active']=bool(result['active'])
        result['operator_assertion_is_claim']=True
        source={'support_id':support_id,'source_session_id':None,'audio_available':False,
                'reason':'support_revoked_or_removed'}
        owner=db.execute('''SELECT s.session_id FROM speaker_support s JOIN sessions sess ON sess.id=s.session_id
            WHERE s.id=? AND s.identity_group=? AND s.generation=? AND sess.identity_group=? AND sess.deleted=0''',
            (support_id,group,generation,group)).fetchone()
        if owner:
            source['source_session_id']=owner['session_id']
            source['metadata_url']=f'/sessions/{owner["session_id"]}/speaker-support/{support_id}'
            try:
                retained=support._row(db,owner['session_id'],support_id,with_pcm=True)
                support.validate_pcm_evidence(retained,retained['pcm'])
                source.update(audio_available=True,reason='available',
                    audio_url=f'/sessions/{owner["session_id"]}/speaker-support/{support_id}/audio')
            except (ValueError,TypeError,KeyError,AttributeError):
                source['reason']='support_integrity_unavailable'
        result['source']=source
        return result
