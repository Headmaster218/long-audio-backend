"""Bounded, explicit person relations. A merge never certifies acoustic evidence.

Original people and review owners are never rewritten. The hash-linked ledger is
the canonical mapping, not an enrollment source or a mutable gallery filter.
"""
import json
from contextlib import nullcontext
import re
import time
import uuid

from . import speaker_support as support

MAX_EVENTS = 4096
ID = re.compile(r'^[a-f0-9]{32}$')
FIELDS = ('id', 'identity_group', 'generation', 'sequence', 'action', 'person_id',
          'other_person_id', 'previous_event_id', 'idempotency_key', 'request_sha256',
          'operator_context', 'response', 'created', 'previous_sha256')


def init_schema(db):
    columns = {r['name'] for r in db.execute('PRAGMA table_info(identity_group_policy)')}
    for name, definition in (('relation_count', 'INTEGER NOT NULL DEFAULT 0'),
                             ('relation_head', "TEXT NOT NULL DEFAULT ''")):
        if name not in columns:
            db.execute(f'ALTER TABLE identity_group_policy ADD COLUMN {name} {definition}')
    db.execute('CREATE INDEX IF NOT EXISTS speakers_identity_group ON speakers(identity_group)')
    db.execute('''CREATE TABLE IF NOT EXISTS identity_relation_events (
        cursor INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        identity_group TEXT NOT NULL, generation TEXT NOT NULL, sequence INTEGER NOT NULL,
        action TEXT NOT NULL, person_id TEXT NOT NULL, other_person_id TEXT NOT NULL,
        previous_event_id TEXT, idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
        operator_context TEXT NOT NULL, response TEXT NOT NULL, created REAL NOT NULL,
        previous_sha256 TEXT NOT NULL, event_sha256 TEXT NOT NULL,
        UNIQUE(identity_group,generation,idempotency_key),
        UNIQUE(identity_group,generation,sequence), UNIQUE(previous_event_id))''')
    db.execute('''CREATE INDEX IF NOT EXISTS relation_events_group
        ON identity_relation_events(identity_group,generation,cursor)''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS relation_events_immutable
        BEFORE UPDATE ON identity_relation_events
        BEGIN SELECT RAISE(ABORT,'Person relation events are immutable'); END''')


def _hash(event):
    return support.digest(support.canonical({key: event[key] for key in FIELDS}))


def _root(person, edges):
    visited = set()
    while person in edges:
        if person in visited:
            raise ValueError('canonical_relation_integrity')
        visited.add(person)
        person = edges[person]
    return person


def load(db, group, generation, policy):
    """Validate the complete bounded group ledger before deriving any mapping."""
    from .verified_identity import MAX_PERSONS
    people = {r['id']: dict(r) for r in db.execute('''SELECT id,state,merged_into
        FROM speakers WHERE identity_group=? LIMIT ?''', (group, MAX_PERSONS + 1))}
    if len(people) > MAX_PERSONS or any(p['merged_into'] is not None for p in people.values()):
        raise ValueError('canonical_person_integrity')
    rows = [dict(r) for r in db.execute('''SELECT * FROM identity_relation_events
        WHERE identity_group=? AND generation=? ORDER BY cursor LIMIT ?''',
        (group, generation, MAX_EVENTS + 1))]
    if len(rows) > MAX_EVENTS or len(rows) != policy['relation_count']:
        raise ValueError('canonical_relation_integrity')
    edges, active = {}, {}
    head, sequence = '', 0
    for event in rows:
        source, target = event['person_id'], event['other_person_id']
        if (event['previous_sha256'] != head or _hash(event) != event['event_sha256']
                or not sequence < event['sequence'] <= policy['revision']
                or source not in people or target not in people or source == target):
            raise ValueError('canonical_relation_integrity')
        action = event['action']
        if action in ('merge', 'different_people'):
            if event['previous_event_id'] is not None or _root(source, edges) == _root(target, edges):
                raise ValueError('canonical_relation_integrity')
            if action == 'merge':
                # Validate roots at this historical admission, not after an undo.
                if source in edges or target in edges:
                    raise ValueError('canonical_relation_integrity')
                edges[source] = target
            active[event['id']] = event
        elif action in ('undo_merge', 'retract_constraint'):
            old = active.pop(event['previous_event_id'], None)
            expected = 'merge' if action == 'undo_merge' else 'different_people'
            if (old is None or old['action'] != expected or old['person_id'] != source
                    or old['other_person_id'] != target):
                raise ValueError('canonical_relation_integrity')
            if action == 'undo_merge':
                if edges.pop(source, None) != target:
                    raise ValueError('canonical_relation_integrity')
        else:
            raise ValueError('canonical_relation_integrity')
        head, sequence = event['event_sha256'], event['sequence']
    if head != policy['relation_head']:
        raise ValueError('canonical_relation_integrity')
    mapping = {person: _root(person, edges) for person in people}
    for event in active.values():
        if event['action'] == 'different_people' and mapping[event['person_id']] == mapping[event['other_person_id']]:
            raise ValueError('canonical_conflicting_people')
    return {'mapping': mapping, 'people': people, 'active': active, 'events': rows,
            'edges': edges, 'profile_revision': policy['revision'], 'generation': generation,
            'identity_group': group}


def snapshot(db, group, policy):
    """Public projections fail closed, while preserving the stored original ID."""
    if not policy or policy['mode'] != 'verified_review_v1':
        return None
    current = db.execute('SELECT generation FROM speaker_identity_generations WHERE identity_group=?', (group,)).fetchone()
    try:
        return load(db, group, current['generation'], policy) if current else None
    except (ValueError, TypeError, KeyError):
        return None


def project_person(row, policy, state):
    if policy and policy['mode'] == 'verified_review_v1':
        row['canonical_person_id'] = state['mapping'].get(row['id']) if state else None
        row['canonical_revision'] = policy['revision']
        row['canonical_state'] = 'current' if state and row['canonical_person_id'] else 'unavailable'
    return row


def review_root(state, person):
    # A newly reviewed person is not in the pre-admission snapshot yet.
    return state['mapping'].get(person, person)


def _check_component(db, group, mapping, target):
    """Only immutable, explicitly reviewed references may contribute to a union."""
    from .verified_identity import MAX_PER_PERSON
    positives, negatives = set(), set()
    for row in db.execute('''SELECT person_id,kind,evidence_key FROM identity_active_reviews
            WHERE identity_group=?''', (group,)):
        if mapping.get(row['person_id']) != target:
            continue
        if row['kind'] == 'positive':
            if row['evidence_key'] in positives:
                raise ValueError('duplicate_or_conflicting_positive_evidence')
            positives.add(row['evidence_key'])
        else:
            negatives.add(row['evidence_key'])
    if positives & negatives:
        raise ValueError('canonical_contradictory_review')
    if len(positives) > MAX_PER_PERSON:
        raise OverflowError('Merged person exceeds eight active positives; explicitly retract selected reviews first')


def _request(db, group, generation, policy, request, previous):
    fields = {'expected_generation', 'expected_revision', 'idempotency_key'}
    if not previous:
        fields |= {'kind', 'person_id', 'other_person_id', 'confirmed'}
    if set(request) != fields:
        raise ValueError('Unexpected or missing person relation fields')
    if request['expected_generation'] != generation:
        raise ValueError('stale_generation')
    key = request['idempotency_key']
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise ValueError('Idempotency key required (1–128 characters)')
    hashed = support.digest(support.canonical({'request': request, 'previous': previous}))
    prior = db.execute('''SELECT request_sha256,response FROM identity_relation_events
        WHERE identity_group=? AND generation=? AND idempotency_key=?''', (group, generation, key)).fetchone()
    if prior:
        if prior['request_sha256'] != hashed:
            raise ValueError('idempotency_conflict')
        return hashed, json.loads(prior['response'])
    if db.execute('''SELECT 1 FROM identity_review_events
            WHERE identity_group=? AND generation=? AND idempotency_key=?''', (group, generation, key)).fetchone():
        raise ValueError('idempotency_conflict')
    if type(request['expected_revision']) is not int or request['expected_revision'] != policy['revision']:
        raise ValueError('stale_profile_revision')
    return hashed, None


def mutate(store, sid, request, *, previous=None, operator_context='local_api', group=None, db=None):
    """An explicit relation claim, or the exact undo/retraction of that claim."""
    from . import verified_identity as bank
    with (store.db() if db is None else nullcontext(db)) as db:
        if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
        group, generation, policy = (bank._context(db, sid) if group is None else bank.group_context(db, group, require_primary=True))
        hashed, retry = _request(db, group, generation, policy, request, previous)
        if retry is not None:
            return retry
        state = load(db, group, generation, policy)
        if previous:
            old = state['active'].get(previous)
            if old is None:
                raise ValueError('Active person relation to undo/retract not found')
            source, target = old['person_id'], old['other_person_id']
            action = 'undo_merge' if old['action'] == 'merge' else 'retract_constraint'
        else:
            source, target = request['person_id'], request['other_person_id']
            if (request['confirmed'] is not True or request['kind'] not in ('same_person', 'different_people')
                    or not isinstance(source, str) or not ID.fullmatch(source)
                    or not isinstance(target, str) or not ID.fullmatch(target)):
                raise ValueError('Explicit confirmed person relation required')
            if source not in state['people'] or target not in state['people']:
                raise KeyError('Person not found in this identity group')
            if any(state['people'][p]['state'] not in ('reviewed_reference', 'reviewed_person') for p in (source, target)):
                raise ValueError('Person relation requires existing verified-group people')
            mapping = state['mapping']
            if mapping[source] == mapping[target]:
                raise ValueError('Person relation requires distinct canonical people')
            action = 'merge' if request['kind'] == 'same_person' else 'different_people'
            if action == 'merge':
                if mapping[source] != source or mapping[target] != target:
                    raise ValueError('Merge requires current canonical roots; resolve aliases first')
                proposed = {person: target if root == source else root for person, root in mapping.items()}
                for event in state['active'].values():
                    if event['action'] == 'different_people' and proposed[event['person_id']] == proposed[event['other_person_id']]:
                        raise ValueError('canonical_conflicting_people')
                if not bank.projection_intact(db, group, generation):
                    raise ValueError('unknown_reference_integrity')
                _check_component(db, group, proposed, target)
            elif any(event['action'] == 'different_people'
                     and {event['person_id'], event['other_person_id']} == {source, target}
                     for event in state['active'].values()):
                raise ValueError('duplicate_different_people_constraint')
            # Every active relation reserves one future undo/retraction event.
            history = db.execute('SELECT COUNT(*) FROM identity_relation_events').fetchone()[0]
            active = db.execute('''SELECT COUNT(*) FROM identity_relation_events e
                WHERE e.action IN ('merge','different_people') AND NOT EXISTS (
                    SELECT 1 FROM identity_relation_events r WHERE r.previous_event_id=e.id)''').fetchone()[0]
            if history + active + 2 > MAX_EVENTS:
                raise OverflowError('Person relation capacity reached; undo/retraction remains available')
        if not isinstance(operator_context, str) or not 1 <= len(operator_context) <= 128:
            raise ValueError('Invalid operator context')
        event_id = uuid.uuid4().hex
        response = {'event_id': event_id, 'action': action, 'person_id': source,
                    'other_person_id': target, 'previous_event_id': previous,
                    'generation': generation, 'profile_revision': policy['revision'] + 1,
                    'operator_assertion_is_claim': True, 'acoustic_support_certified': False}
        event = dict(id=event_id, identity_group=group, generation=generation,
                     sequence=policy['revision'] + 1, action=action, person_id=source,
                     other_person_id=target, previous_event_id=previous,
                     idempotency_key=request['idempotency_key'], request_sha256=hashed,
                     operator_context=operator_context, response=support.canonical(response).decode(),
                     created=time.time(), previous_sha256=policy['relation_head'])
        digest = _hash(event)
        db.execute(f'''INSERT INTO identity_relation_events ({','.join(FIELDS)},event_sha256)
            VALUES ({','.join('?' for _ in range(len(FIELDS)+1))})''',
            (*[event[key] for key in FIELDS], digest))
        db.execute('''UPDATE identity_group_policy SET relation_count=relation_count+1,relation_head=?
            WHERE identity_group=?''', (digest, group))
        bank._invalidate(db, group, 'canonical_persons_changed')
        return response


def events(store, sid, after=0, limit=100, *, group=None):
    from .verified_identity import _context, group_context
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('Invalid person relation page')
    with store.db() as db:
        db.execute('BEGIN')
        group, generation, policy = (_context(db, sid) if group is None else group_context(db, group, require_primary=True))
        state = load(db, group, generation, policy)
        page = [e for e in state['events'] if e['cursor'] > after][:limit]
        public = []
        for event in page:
            row = {key: event[key] for key in ('cursor', 'id', 'action', 'person_id',
                   'other_person_id', 'previous_event_id', 'sequence', 'operator_context', 'created')}
            row.update(active=event['id'] in state['active'], operator_assertion_is_claim=True,
                       acoustic_support_certified=False)
            public.append(row)
        return {'events': public, 'generation': generation, 'profile_revision': policy['revision'],
                'next_cursor': page[-1]['cursor'] if page else after}


def reset(db, group):
    db.execute('DELETE FROM identity_relation_events WHERE identity_group=?', (group,))
    db.execute("UPDATE identity_group_policy SET relation_count=0,relation_head='' WHERE identity_group=?", (group,))
