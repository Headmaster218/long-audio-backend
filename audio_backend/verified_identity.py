"""Experimental explicit-review identity bank. No automatic learning.

All writes share the result store transaction. This bank never reads legacy
embeddings. Assertions are recorded operator claims, not proven speaker purity.
"""
import json
import time
import uuid
from pathlib import Path
from . import speaker_support as support, support_vectors

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
    raw = Path(path).read_bytes()
    if len(raw) > 8192:
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


def install_lifecycle(db):
    # This defensive trigger covers direct SQL source erasure as well as normal
    # session deletion. One revocation slot was reserved for every active review.
    db.execute('''CREATE TRIGGER IF NOT EXISTS identity_review_support_expiry BEFORE DELETE ON speaker_support
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
        UPDATE identity_group_policy SET revision=revision+1,availability=availability+1
            WHERE identity_group=OLD.identity_group;
        UPDATE identity_decision_state SET stale=1,reason='source_expired' WHERE stale=0 AND decision_id IN
            (SELECT id FROM identity_decisions WHERE identity_group=OLD.identity_group);
        UPDATE segments SET speaker_id=NULL,speaker_match_score=NULL,speaker_status='identity_stale'
            WHERE identity_decision_id IN (SELECT id FROM identity_decisions WHERE identity_group=OLD.identity_group)
            AND speaker_status NOT LIKE 'human_%';
        UPDATE speaker_turns SET speaker_id=NULL,speaker_match_score=NULL,speaker_status='identity_stale'
            WHERE identity_decision_id IN (SELECT id FROM identity_decisions WHERE identity_group=OLD.identity_group)
            AND speaker_status NOT LIKE 'human_%';
        DELETE FROM identity_active_reviews WHERE support_id=OLD.id;
        END''')


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
    legacy = db.execute('SELECT 1 FROM sessions WHERE identity_group=? AND deleted=0', (group,)).fetchone()
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
    support.generation(db, group)


def _context(db, sid):
    session = db.execute('SELECT * FROM sessions WHERE id=? AND deleted=0', (sid,)).fetchone()
    if not session:
        raise KeyError('Session not found')
    group = session['identity_group']
    policy = group_policy(db, group)
    if not policy or policy['mode'] != MODE:
        raise ValueError('Explicit verified_review_v1 identity group required')
    return group, support.generation(db, group), policy


def _invalidate(db, group, reason):
    db.execute('UPDATE identity_group_policy SET revision=revision+1,availability=availability+1 WHERE identity_group=?', (group,))
    db.execute('''UPDATE identity_decision_state SET stale=1,reason=? WHERE stale=0 AND decision_id IN
        (SELECT id FROM identity_decisions WHERE identity_group=?)''', (reason, group))
    for table in ('segments', 'speaker_turns'):
        db.execute(f'''UPDATE {table} SET speaker_id=NULL,speaker_match_score=NULL,speaker_status='identity_stale'
            WHERE identity_decision_id IN (SELECT id FROM identity_decisions WHERE identity_group=?)
            AND speaker_status NOT LIKE 'human_%' AND speaker_status!='identity_stale' ''', (group,))


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
    if type(request.get('expected_revision')) is not int or request['expected_revision'] != policy['revision']:
        raise ValueError('stale_profile_revision')
    return hashed, None


def _evidence(db, sid, generation, policy, request):
    row = support._row(db, sid, request.get('support_id'), with_pcm=True)
    manifest = json.loads(row['manifest'])
    support.validate_manifest(manifest)
    pcm = row['pcm']
    if (row['generation'] != generation or row['status'] != 'available' or not support.finite_pcm(pcm)
            or not 0 < len(pcm) <= support.MAX_PCM_BYTES or len(pcm) != row['pcm_bytes']
            or support.digest(pcm) != manifest['support']['pooled_pcm_sha256']
            or support.digest(support.canonical(manifest)) != row['manifest_sha256']):
        raise ValueError('Exact retained acoustic support integrity unavailable')
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


def _capacity(db, kind, person, replacing=None):
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
        count = db.execute("SELECT COUNT(*) FROM identity_active_reviews WHERE kind='positive' AND person_id=?", (person,)).fetchone()[0]
        removed = bool(replacing and replacing['kind'] == kind and replacing['person_id'] == person)
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
                if claim['person_id'] == person:
                    raise ValueError('duplicate_or_contradictory_review')
            _capacity(db, kind, person, old)
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
        db.execute('INSERT INTO identity_review_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (event_id,group,generation,policy['revision']+1,action,person,raw['support_id'] if raw else old['support_id'],
             raw['id'] if raw else None,raw['evidence_key'] if raw else old['evidence_key'],
             support.canonical(fingerprints).decode() if fingerprints else None,
             support.canonical(request['attestation']).decode() if raw else None,operator_context,
             prediction[0] if prediction else None,previous,request['idempotency_key'],hashed,
             support.canonical(response).decode(),time.time()))
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
    db.execute('DELETE FROM identity_active_reviews WHERE identity_group=?', (group,))
    db.execute('DELETE FROM identity_review_events WHERE identity_group=?', (group,))
    db.execute('DELETE FROM identity_decisions WHERE identity_group=?', (group,))
    db.execute('UPDATE identity_group_policy SET space_id=NULL,revision=revision+1,availability=availability+1 WHERE identity_group=?', (group,))
    for table in ('segments','speaker_turns'):
        db.execute(f'''UPDATE {table} SET identity_decision_id=NULL WHERE identity_decision_id IS NOT NULL AND chunk_id IN
            (SELECT c.id FROM chunks c JOIN sessions s ON s.id=c.session_id WHERE s.identity_group=?)''', (group,))


def bank(store, sid):
    with store.db() as db:
        group, generation, policy = _context(db,sid)
        counts = {row['kind']: row['n'] for row in db.execute('''SELECT kind,COUNT(*) AS n FROM identity_active_reviews
            WHERE identity_group=? GROUP BY kind''', (group,))}
        return {'identity_mode': MODE, 'generation': generation, 'profile_revision': policy['revision'],
                'availability_sequence': policy['availability'], 'policy_sha256': policy['policy_sha256'],
                'policy': json.loads(policy['policy']), 'space_id': policy['space_id'], 'active_counts': counts,
                'limits': {'persons_global':MAX_PERSONS,'positives_global':MAX_POSITIVES,
                           'negatives_global':MAX_NEGATIVES,'events_global':MAX_EVENTS,'positives_per_person':MAX_PER_PERSON},
                'experimental':True, 'warning':'Operator assertions are claims, not proof of correct identity or acoustic purity.'}
