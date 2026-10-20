"""Finite runtime05 metadata bounds; synthetic DBs only, no model or audio.

Direct preflight acceptance below is not canonical-ledger acceptance. These tests
exercise metadata bounds separately from the unchanged cryptographic loader.
"""
import sqlite3

import pytest

from audio_backend import identity_canonical
from audio_backend import reviewed_primary_persons as bridge
from reviewed_person_bridge_fixtures import GROUP, env


def _relation(env):
    env.activate()
    one = env.associate(env.observe())
    two = env.associate(env.observe())
    event = env.relation(one['person_id'], two['person_id'])
    return event, one['person_id']


@pytest.mark.parametrize('corruption', ['oversized-text', 'blob', 'nul-byte-bound'])
def test_canonical_stream_refuses_bounded_corruption_before_loader(env, monkeypatch, corruption):
    event, _ = _relation(env)
    entered = []
    def forbidden(*args, **kwargs):
        entered.append(True)
        raise AssertionError('Malformed metadata reached materialized loader')
    monkeypatch.setattr(identity_canonical, 'load', forbidden)
    with env.store.db() as db:
        _, generation, policy = bridge._context(db, GROUP)
        db.execute('DROP TRIGGER relation_events_immutable')
        field, value = {
            'oversized-text': ('response', 'x' * 32769),
            'blob': ('response', sqlite3.Binary(b'[]')),
            'nul-byte-bound': ('idempotency_key', 'k' * 512 + '\0suffix'),
        }[corruption]
        db.execute(f'UPDATE identity_relation_events SET {field}=? WHERE id=?', (value, event['event_id']))
        old_limit = db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
        with pytest.raises(bridge.BridgeError) as caught:
            bridge._canonical(db, GROUP, generation, policy)
        assert caught.value.code == 'canonical_integrity_unavailable'
        assert caught.value.status == 503
        assert not entered
        assert db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == old_limit
        # No mutation/normalization of the malformed evidence is permitted.
        assert db.execute(f'SELECT {field} FROM identity_relation_events WHERE id=?',
                          (event['event_id'],)).fetchone()[0] == value


def test_canonical_stream_legal_control_escape_bounds_preserve_raw_values(env):
    event, _ = _relation(env)
    with env.store.db() as db:
        db.execute('DROP TRIGGER relation_events_immutable')
        # Exact maxima of every text scalar under the former preflight. This
        # intentionally is NOT a valid canonical ledger or an identity claim.
        maxima = dict(response=2048, operator_context=512, idempotency_key=512,
            id=32, person_id=32, other_person_id=32, generation=32,
            identity_group=100, action=32, request_sha256=64, event_sha256=64,
            previous_sha256=64, previous_event_id=32)
        assert sum(maxima.values()) == 3556
        values = {name: '\x01' * size for name, size in maxima.items()}
        values['operator_context'] = '\0' * 512
        db.execute('UPDATE identity_relation_events SET ' + ','.join(name+'=?' for name in values) + ' WHERE id=?',
                   (*values.values(), event['event_id']))
        lookup = (values['id'],)
        before = tuple(db.execute('SELECT * FROM identity_relation_events WHERE id=?', lookup).fetchone())
        old_limit = db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
        bridge._canonical_preflight(db, values['identity_group'], values['generation'])
        assert db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == old_limit
        assert tuple(db.execute('SELECT * FROM identity_relation_events WHERE id=?', lookup).fetchone()) == before


def test_canonical_stream_nullable_person_fields_preserve_acceptance(env):
    event, person = _relation(env)
    with env.store.db() as db:
        _, generation, _ = bridge._context(db, GROUP)
        db.execute('UPDATE speakers SET name=NULL,merged_into=NULL WHERE id=?', (person,))
        bridge._canonical_preflight(db, GROUP, generation)
        assert tuple(db.execute('SELECT name,merged_into FROM speakers WHERE id=?',
                                (person,)).fetchone()) == (None, None)


@pytest.mark.parametrize('created', [0.12345678901234566, float('inf')], ids=['precise-real', 'real-infinity'])
def test_canonical_stream_numeric_validation_does_not_rewrite_original(env, created):
    event, _ = _relation(env)
    with env.store.db() as db:
        _, generation, _ = bridge._context(db, GROUP)
        db.execute('DROP TRIGGER relation_events_immutable')
        db.execute('UPDATE identity_relation_events SET created=? WHERE id=?', (created, event['event_id']))
        before = db.execute('SELECT created,typeof(created),sequence,typeof(sequence) FROM identity_relation_events WHERE id=?',
                            (event['event_id'],)).fetchone()
        bridge._canonical_preflight(db, GROUP, generation)
        after = db.execute('SELECT created,typeof(created),sequence,typeof(sequence) FROM identity_relation_events WHERE id=?',
                           (event['event_id'],)).fetchone()
        assert tuple(before) == tuple(after)
        assert after[0] == created and after[1] == 'real' and after[3] == 'integer'


def test_canonical_stream_restores_preexisting_lower_connection_limit(env):
    event, _ = _relation(env)
    with env.store.db() as db:
        _, generation, _ = bridge._context(db, GROUP)
        original = db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8192)
        try:
            bridge._canonical_preflight(db, GROUP, generation)
            assert db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == 8192
        finally:
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, original)


@pytest.mark.parametrize('failure', ['fetch-interrupted', 'close-error'])
def test_canonical_stream_restores_limit_on_fetch_or_close_failure(env, failure):
    _relation(env)
    with env.store.db() as db:
        old_limit = db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
        class Cursor:
            def __init__(self, actual):
                self.actual = actual
            def __iter__(self):
                yield next(iter(self.actual))
                if failure == 'fetch-interrupted':
                    raise sqlite3.OperationalError('interrupted')
            def close(self):
                self.actual.close()
                if failure == 'close-error':
                    raise RuntimeError('synthetic cursor close failure')
        class Connection:
            getlimit = db.getlimit
            setlimit = db.setlimit
            def execute(self, sql, args):
                return Cursor(db.execute(sql, args))
        expected = sqlite3.OperationalError if failure == 'fetch-interrupted' else RuntimeError
        with pytest.raises(expected):
            bridge._canonical_preflight_rows(Connection(), 'speakers', 'identity_group=?',
                (GROUP,), (('id','text',32,32,False),), 1024)
        assert db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == old_limit
