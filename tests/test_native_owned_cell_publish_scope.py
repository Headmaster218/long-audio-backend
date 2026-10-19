"""Model-free publication connection reuse; all authority snapshots stay fresh."""
import json
import sqlite3
import threading
from unittest.mock import patch

import pytest

from audio_backend import native_owned_cell_capture as cap
from audio_backend import native_owned_cell_store as store
from audio_backend.native_owned_cells import Unavailable
import test_native_owned_cell_capture as capture_fixture
import test_native_owned_cell_list_authority as list_fixture


@pytest.fixture
def authority():
    case = list_fixture.ListAuthorityTests('runTest')
    try:
        case.setUp()
        yield case
    finally:
        case.doCleanups()


def document(case):
    return json.loads(case.cell.proof)


@pytest.mark.parametrize('change', ('commit', 'source', 'session', 'epoch'))
def test_publication_later_checks_observe_mutation(authority, change):
    original = authority.authority.seam_commit_current
    observed = []

    def current(commit, scope, part, budget):
        result = original(commit, scope, part, budget)
        observed.append(result)
        if len(observed) == 1:
            if change == 'commit':
                authority.commit("UPDATE chunks SET metadata=json_set(metadata,'$.core_cut.nominal',999) WHERE id='left'")
            elif change == 'source':
                authority.commit("DELETE FROM chunks WHERE id='left'")
            elif change == 'session':
                authority.commit('UPDATE sessions SET deleted=1')
            else:
                authority.owner.revoked()
        return result

    with patch.object(authority.authority, 'seam_commit_current', side_effect=current):
        assert not authority.authority.cell_valid(document(authority), store.Budget(), publishing=True)
    assert observed[0] is True
    assert len(authority.connections) == 1
    assert authority.connections[0].record['closed']
    assert not authority.connections[0].record['close_with_transaction']
    assert getattr(authority.authority._list_reads, 'current', None) is None


class StopPublication(BaseException):
    pass


@pytest.mark.parametrize('failure', (RuntimeError, StopPublication))
def test_publication_read_exception_closes_scope(authority, failure):
    with patch.object(cap, 'seam_commit_projection', side_effect=failure('injected projection failure')):
        with pytest.raises(failure, match='^injected projection failure$'):
            authority.authority.cell_valid(document(authority), store.Budget(), publishing=True)
    assert len(authority.connections) == 1
    connection = authority.connections[0]
    assert connection.record['closed']
    # The failing snapshot closes rather than escaping as a retained snapshot.
    assert connection.record['begin'] == connection.record['rollback'] + 1
    assert getattr(authority.authority._list_reads, 'current', None) is None


def test_publication_uses_existing_scope_without_nesting(authority):
    budget = store.Budget()
    with authority.authority.list_read_scope(budget):
        scope = authority.authority._list_reads.current
        assert authority.authority.cell_valid(document(authority), budget, publishing=True)
        assert authority.authority._list_reads.current is scope
        assert len(authority.connections) == 1
        assert not scope['db'].in_transaction
        with pytest.raises(Unavailable, match='^nested_primary_list_read$'):
            with authority.authority.list_read_scope(budget):
                pass
        assert authority.authority._list_reads.current is scope
        assert authority.authority.session_valid(authority.sid, budget)
    assert authority.connections[0].record['closed']
    assert getattr(authority.authority._list_reads, 'current', None) is None


def test_publication_existing_wrong_budget_fails_closed(authority):
    budget = store.Budget()
    with authority.authority.list_read_scope(budget):
        assert authority.authority.session_valid(authority.sid, budget)
        with pytest.raises(Unavailable, match='^primary_list_read_scope_mismatch$'):
            authority.authority.cell_valid(document(authority), store.Budget(), publishing=True)
        assert authority.authority._list_reads.current['db'] is None
    assert len(authority.connections) == 1
    assert authority.connections[0].record['closed']
    assert getattr(authority.authority._list_reads, 'current', None) is None


def test_nonpublication_still_uses_fresh_connections(authority):
    assert authority.authority.cell_valid(document(authority), store.Budget(), publishing=False)
    assert len(authority.connections) == 30
    assert all(connection.record['begin'] == 1 and connection.record['closed']
               for connection in authority.connections)
    assert getattr(authority.authority._list_reads, 'current', None) is None


def test_three_publications_measure_unchanged_work_budget(record_property):
    case = capture_fixture.CaptureFixture('runTest')
    case.setUp()
    try:
        _, doc = case._seam_authority_fixture()
        for commit in doc['commits']:
            with case.primary.db() as db:
                cid = commit['chunk_id']
                metadata = json.loads(db.execute('SELECT metadata FROM chunks WHERE id=?', (cid,)).fetchone()[0])
                metadata['irrelevant_analyzer_output'] = 'x' * 30000
                db.execute('UPDATE chunks SET metadata=? WHERE id=?', (json.dumps(metadata), cid))
        connections = []
        raw_connect = sqlite3.connect
        caller = threading.get_ident()

        class Connection(sqlite3.Connection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.begins = self.rollbacks = 0
                self.closed = False
                connections.append(self)

            def execute(self, sql, parameters=()):
                self.begins += sql.strip().upper() == 'BEGIN'
                self.rollbacks += sql.strip().upper() == 'ROLLBACK'
                return super().execute(sql, parameters)

            def close(self):
                if not self.closed:
                    self.close_with_transaction = self.in_transaction
                super().close()
                self.closed = True

        def connect(database, *args, **kwargs):
            if threading.get_ident() == caller and str(database) == case.authority.path.as_uri() + '?mode=ro':
                return raw_connect(database, *args, factory=Connection, **kwargs)
            return raw_connect(database, *args, **kwargs)

        budget = store.Budget()
        with patch.object(sqlite3, 'connect', side_effect=connect), budget.phase():
            for _ in range(3):
                assert case.authority.cell_valid(doc, budget, publishing=True)
        assert len(connections) == 3
        assert all(connection.begins == connection.rollbacks == 30 and connection.closed
                   and not connection.close_with_transaction for connection in connections)
        assert 0 < budget.steps < store.SQL_STEPS == 150000
        assert 0 < budget.sql_bytes < store.SQL_PROJECTION_BYTES == 262144
        assert budget.read_bytes == 0
        assert store.OP_SECONDS == .5
        for name, value in {'steps': budget.steps, 'sql_bytes': budget.sql_bytes,
                            'read_bytes': budget.read_bytes, 'connections': len(connections),
                            'fresh_snapshots': sum(c.begins for c in connections),
                            'ended_snapshots': sum(c.rollbacks for c in connections)}.items():
            record_property(name, value)
    finally:
        case.tearDown()
        case.doCleanups()
