"""Prepared model-free terminal readback contracts; execution is separately gated.

Fresh fixture-owned SQLite files and <=2 synthetic four-second WAVs only.
Each comparison has one missing observation, <=1 witness and <=32 steps.
No model, subprocess, server, or real recording is used. Existing fixture
definitions are reused unchanged; full pages use the query-only transcript path.
"""
import json

import pytest

from audio_backend import moss_automatic as automatic
from audio_backend import moss_result_store as result_store
from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_links as links
from audio_backend import transcript_store as transcript
from audio_backend.recording_speaker_history_compare import ComparisonBuild
from test_recording_speaker_history import (
    FRAMES, assert_accounting, claim, claim_moss, connection, failure_bytes,
    history_env, moss_fixture, observation_rows, primary_fixture, saved_read,
    scope_row, snapshot,
)


def settled_missing_primary(env):
    item = primary_fixture(env)
    reservation = claim(env, item)
    env.value.finish_primary(reservation, 'probe_failed', failure_json=failure_bytes())
    pending = env.value.begin_comparison()
    assert pending is not None
    sources = env.value.active_source_page(pending)
    assert sources['complete'] and len(sources['rows']) == 1
    env.value.revalidate_active_sources(pending, sources)
    build = ComparisonBuild(pending['token'], admitted_bytes=history.COMPARE_BYTES, clock=lambda: 0.)
    observations = env.value.observation_page(pending)
    witnesses = env.value.witness_page(pending)
    assert observations['complete'] and len(observations['rows']) == 1
    assert witnesses['complete'] and len(witnesses['rows']) <= 1
    build.feed_observations(pending['token'], 0, observations['rows'], complete=True)
    build.feed_witnesses(pending['token'], 0, witnesses['rows'], complete=True)
    for _ in range(32):
        result = build.step()
        if result['state'] not in ('loading', 'pending'):break
    assert result['state'] == 'refused' and result['reason'] == 'unknown_rival'
    assert env.value.finish_comparison(pending, result['reason']) is True
    build.discard()
    scope = scope_row(env.store, item.rid)
    assert scope['state'] == 'unknown_rival'
    assert scope['reserved_bytes'] == scope['staging_bytes'] == 0
    assert scope['building_slot'] is scope['build_token'] is scope['current_slot'] is None
    assert_accounting(env)
    return item, reservation


def page_reader(env):
    # Extend only this fresh test database with the passive transcript surface.
    # No fixture/source insertion follows this additive page-only preparation.
    with connection(env.store.path) as db:
        db.executescript('''
            ALTER TABLE sessions ADD COLUMN timeline_end REAL NOT NULL DEFAULT 4.;
            ALTER TABLE segments ADD COLUMN confidence REAL NOT NULL DEFAULT 0.;
            CREATE INDEX chunk_timeline ON chunks(session_id,timeline_start);
            CREATE INDEX segments_chunk ON segments(chunk_id,start);
            CREATE TABLE result_changes(cursor INTEGER PRIMARY KEY,session_id TEXT);
            CREATE INDEX result_changes_session ON result_changes(session_id,cursor);
        ''')
        automatic.init_schema(db)
        result_store.init_schema(db)
    return transcript.TranscriptStore(env.store, clock=lambda: 0.)


def test_terminal_unknown_rival_is_abstained_in_readback_and_page_scope(history_env, monkeypatch):
    env = history_env
    item, _ = settled_missing_primary(env)
    reader = page_reader(env)
    before = snapshot(env.store)
    reader.speaker_links_enabled = False
    baseline = json.loads(reader.page(item.sid))
    reader.speaker_links_enabled = True
    page = json.loads(reader.page(item.sid))
    saved = saved_read(env, item)
    assert saved['history_status'] == 'abstained' and saved['reason'] == 'unknown_rival'
    assert saved['state'] == 'links_stale' and saved['annotations'] == []
    assert saved['decision_revision'] == -1
    assert len(page['recording_speaker_links']['scopes']) == 1
    scope = page['recording_speaker_links']['scopes'][0]
    assert scope['recording_id'] == item.rid and scope['scope_id'] == saved['scope_id']
    assert scope['history_status'] == 'abstained' and scope['reason'] == 'unknown_rival'
    assert scope['state'] == 'links_stale' and scope['decision_revision'] == -1
    assert page['recording_speaker_links']['automatic_gallery_enrollment'] is False
    for key in ('reading', 'baseline_reading', 'alternatives', 'selected_runs'):
        assert page[key] == baseline[key]
    assert len(page['baseline_reading']) == 1
    assert all('recording_speaker_link' not in row for row in page['baseline_reading'])
    assert 'recording-speaker-' not in json.dumps(page)

    original = history.read_saved_for_refs
    private_reason = 'private diagnostic must not be projected'
    def unexpected_reason(*args, **kwargs):
        result = original(*args, **kwargs)
        return dict(result, reason=private_reason)
    monkeypatch.setattr(history, 'read_saved_for_refs', unexpected_reason)
    bounded = json.loads(reader.page(item.sid))
    assert 'reason' not in bounded['recording_speaker_links']['scopes'][0]
    assert private_reason not in json.dumps(bounded)
    assert snapshot(env.store) == before
    assert env.owner.invocations == []


def test_genuine_new_admission_resets_terminal_abstention_to_pending(history_env):
    env = history_env
    item, reservation = settled_missing_primary(env)
    retained = observation_rows(env.store, reservation['token'])
    terminal = saved_read(env, item)
    assert terminal['history_status'] == 'abstained'
    prior = scope_row(env.store, item.rid)
    assert env.value.note_primary_skip(item.sid, item.rid, prior['high_water'] + FRAMES,
                                       'capacity', links.expected_space()) is True
    assert saved_read(env, item) == terminal
    assert env.value.begin_comparison() is None
    other, batch = moss_fixture(env)
    assert other.rid == item.rid
    fresh = claim_moss(env, other, batch)
    assert fresh['should_probe'] is True
    admitted = scope_row(env.store, item.rid)
    assert admitted['state'] == 'pending'
    assert admitted['history_revision'] == prior['history_revision'] + 1
    assert admitted['observation_count'] == 2 and admitted['vector_count'] == 0
    saved = saved_read(env, item)
    assert saved['history_status'] == 'pending' and saved['reason'] == 'pending'
    assert saved['state'] == 'links_stale' and saved['annotations'] == []
    assert observation_rows(env.store, reservation['token']) == retained
    assert env.owner.invocations == []
    assert_accounting(env)


@pytest.mark.parametrize('unsettled', [
    'reserved_bytes', 'staging_bytes', 'building_slot', 'current_slot', 'reserved_attempt',
])
def test_unknown_rival_with_unsettled_ownership_stays_pending(history_env, unsettled):
    env = history_env
    if unsettled == 'reserved_attempt':
        item = primary_fixture(env)
        claim(env, item)
        # Even inconsistent aggregate headers cannot hide a live attempt.
        assignment = "state='unknown_rival',reserved_bytes=0,staging_bytes=0"
    else:
        item, _ = settled_missing_primary(env)
        assignment = {
            'reserved_bytes': 'reserved_bytes=1',
            'staging_bytes': 'staging_bytes=1',
            'building_slot': "building_slot=0,build_token='" + 'f' * 32 + "'",
            'current_slot': 'current_slot=0',
        }[unsettled]
    # Deliberate inconsistent headers belong only to this fresh negative fixture.
    with connection(env.store.path) as db:
        db.execute(f'UPDATE {history.SCOPES} SET {assignment} WHERE recording_id=?', (item.rid,))
    before = snapshot(env.store)
    saved = saved_read(env, item)
    assert saved['history_status'] == 'pending' and saved['reason'] == 'unknown_rival'
    assert saved['state'] == 'links_stale' and saved['annotations'] == []
    assert snapshot(env.store) == before


@pytest.mark.parametrize('authority', ['unknown', 'invalid'])
def test_authority_fences_precede_terminal_abstention(history_env, monkeypatch, authority):
    env = history_env
    item, _ = settled_missing_primary(env)
    if authority == 'unknown':
        monkeypatch.setattr(history, '_AUTHORITY_UNKNOWN_OVERFLOW', True)
        expected = ('pending', 'source_authority_unknown')
    else:
        with connection(env.store.path) as db:
            db.execute("UPDATE segments SET text='changed' WHERE id=?", (item.number,))
        expected = ('invalid', 'source_changed')
    saved = saved_read(env, item)
    assert (saved['history_status'], saved['reason']) == expected
    assert saved['state'] == 'links_stale' and saved['annotations'] == []
