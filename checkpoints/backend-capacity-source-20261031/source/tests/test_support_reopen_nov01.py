"""New finite Store-reopen component coverage, not Worker/process restart."""
from types import SimpleNamespace
import pytest
from audio_backend import speaker_support as support
from audio_backend import strict_payload_v2 as payload
from audio_backend.storage import Store
from test_strict_payload_v2 import component_store, completed_component, make_cold, exact_reads
from test_strict_archive_v2 import positive_close


@pytest.mark.parametrize('with_pcm', [False, True])
def test_exact_support_lookup_survives_cold_store_reopen(tmp_path, monkeypatch, with_pcm):
    value = make_cold(completed_component(component_store(tmp_path)), monkeypatch)
    original_store = value.store
    cfg, path = original_store.settings, original_store.path
    exact_reads(value)
    original_unit = value.unit_path.read_bytes()
    positive_close(original_store)
    reopened = Store(path, cfg)
    reopened.moss_automatic = SimpleNamespace(primary_only=True)
    try:
        value.store = reopened
        exact_reads(value)
        budget = payload.PayloadBudget()
        deadline = budget.deadline
        with reopened.db() as db:
            db.execute('BEGIN')
            assert db.execute('SELECT pcm FROM speaker_support WHERE id=?', (value.support['id'],)).fetchone()[0] is None
            queries = []
            db.set_trace_callback(queries.append)
            try:
                with reopened.payload_resolver(db, budget=budget) as resolver:
                    row = support._row(db, value.sid, value.support['id'], with_pcm, payload=resolver)
                    assert row['actual_pcm_bytes'] == value.support['pcm_bytes']
                    if with_pcm: assert row['pcm'] == value.support['pcm']
            finally: db.set_trace_callback(None)
        assert any('speaker_support s INDEXED BY support_session' in q for q in queries)
        assert budget.deadline == deadline
        assert budget.sql_steps > 0 and budget.read_bytes > 0
        assert value.unit_path.read_bytes() == original_unit
        exhausted = payload.PayloadBudget()
        exhausted.sql_steps = payload.base.MAX_STEPS
        with pytest.raises(payload.base.ArchiveLimit):
            with reopened.db() as db:
                db.execute('BEGIN')
                with reopened.payload_resolver(db, budget=exhausted) as resolver:
                    support._row(db, value.sid, value.support['id'], True, payload=resolver)
        assert exhausted.sql_steps >= payload.base.MAX_STEPS
    finally:
        positive_close(reopened)
