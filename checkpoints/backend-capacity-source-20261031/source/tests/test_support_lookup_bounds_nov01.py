"""Fresh Nov1 synthetic regression, not recovered Oct31 evidence."""
import hashlib
import json
import sqlite3
import pytest
from audio_backend import speaker_support

BODY = b'synthetic-pcm-01'
DIGEST = hashlib.sha256(BODY).hexdigest()


def database(count=8461, cold=False):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''
    CREATE TABLE sessions(id TEXT PRIMARY KEY, deleted INTEGER, identity_group TEXT);
    CREATE TABLE speaker_identity_generations(identity_group TEXT PRIMARY KEY, generation TEXT);
    CREATE TABLE speaker_support(id TEXT PRIMARY KEY, session_id TEXT, identity_group TEXT,
      generation TEXT, manifest TEXT, manifest_sha256 TEXT, status TEXT, pcm_bytes INTEGER,
      window_start REAL, local_speaker_id TEXT, pcm BLOB);
    CREATE INDEX support_session ON speaker_support(session_id,id);
    CREATE INDEX support_group ON speaker_support(identity_group);
    INSERT INTO sessions VALUES('session',0,'group');
    INSERT INTO speaker_identity_generations VALUES('group','generation');
    ''')
    manifest = json.dumps({'support': {'pooled_pcm_sha256': DIGEST}})
    db.executemany('INSERT INTO speaker_support VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        ((f'{i:032x}', 'session', 'group', 'generation', manifest, '0'*64,
          'available', len(BODY), 0, 'speaker', None if cold else BODY) for i in range(count)))
    return db


class Cold:
    def __init__(self): self.calls = []
    def available(self, *args): self.calls.append(('available', args)); return True
    def resolve(self, *args): self.calls.append(('resolve', args)); return BODY


def measured(db, fn):
    count = [0]
    queries = []
    def step(): count[0] += 1; return 0
    db.set_progress_handler(step, 1)
    db.set_trace_callback(queries.append)
    try: result = fn()
    finally:
        db.set_progress_handler(None, 0)
        db.set_trace_callback(None)
    return result, count[0], queries


@pytest.mark.parametrize('count', [674, 8461, 32916])
@pytest.mark.parametrize('pcm', [False, True])
def test_lookup_bounded_and_identical_to_unhinted_query(count, pcm):
    db = database(count)
    try:
        result, steps, queries = measured(db, lambda: speaker_support._row(db, 'session', f'{count-1:032x}', pcm))
        assert steps < 200, steps
        query = queries[0]
        assert 'INDEXED BY support_session' in query
        prior, prior_steps, _ = measured(db, lambda: db.execute(query.replace(' INDEXED BY support_session', '')).fetchone())
        assert dict(result) == dict(prior)
        plan = [tuple(r) for r in db.execute('EXPLAIN QUERY PLAN ' + query)]
        assert any('support_session' in str(r) for r in plan)
    finally: db.close()


@pytest.mark.parametrize('mutation', [
    "UPDATE sessions SET deleted=1", "UPDATE sessions SET identity_group='other'",
    "UPDATE speaker_identity_generations SET generation='new'",
    "DELETE FROM sessions", "DELETE FROM speaker_identity_generations",
])
def test_authority_predicates_unchanged(mutation):
    db = database(3)
    try:
        db.execute(mutation)
        with pytest.raises(KeyError): speaker_support._row(db, 'session', f'{0:032x}')
    finally: db.close()


@pytest.mark.parametrize('sid,identifier', [('foreign', f'{0:032x}'), ('session', 'missing')])
def test_exact_identity_refuses_other_or_absent(sid, identifier):
    db = database(3)
    try:
        with pytest.raises(KeyError): speaker_support._row(db, sid, identifier)
    finally: db.close()


@pytest.mark.parametrize('pcm', [False, True])
def test_cold_resolver_unchanged(pcm):
    db = database(3, cold=True)
    payload = Cold()
    try:
        row = speaker_support._row(db, 'session', f'{0:032x}', pcm, payload=payload)
        assert row['actual_pcm_bytes'] == len(BODY)
        assert [call[0] for call in payload.calls] == (['available', 'resolve'] if pcm else ['available'])
        assert all(call[1] == ('pcm', f'{0:032x}', len(BODY), DIGEST) for call in payload.calls)
        if pcm: assert row['pcm'] == BODY
    finally: db.close()
