"""Finite model-free sampling diagnostics; execution is separately gated.

Nine direct nodes use fixed scalar objects only. Four durable nodes each reuse
one fresh history database, one 128,044-byte synthetic WAV, one scope/attempt/
observation/source, <=3 dependencies and <=1 small MOSS proof/copied receipt.
There is no model, subprocess, network, real audio, shared-clock patch, maximum
proof allocation, schema migration, or old-product import/execution. Diagnostics
are <=376 bytes except explicit rejection inputs (including exactly 513 bytes).
"""
from contextlib import contextmanager
import hashlib
import json
import threading

import pytest

from audio_backend import native_execution_permit as permit_mod
from audio_backend import recording_primary_probe as primary
from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_link_store as store
from audio_backend import recording_speaker_probe_runner as runner
from audio_backend.native_owned_support_cache import ReadError
from test_native_execution_permit import sample, settings
from test_recording_speaker_history import (
    assert_accounting, assert_missing_observation, attempt_row, claim, claim_moss,
    connection, database_write_fault, history_env, invocation_for, moss_fixture,
    observation_rows, primary_fixture, restart_history, scope_row, snapshot,
)

OLD_RAW = (b'{"child_dispatch":"possible","error_type":"RuntimeError",'
           b'"reason":"child_failed","schema":"recording-speaker-probe-failure-v1",'
           b'"stage":"child_wait"}')
OLD_KEYS = {'schema', 'stage', 'reason', 'child_dispatch', 'error_type'}
SUBTYPES = ('busy_flight', 'stale_generation', 'cooldown', 'recovery_not_due',
    'skipped', 'wall_budget', 'rows_budget', 'census_failure', 'resource_limit',
    'cancelled', 'deadline', 'ownership_changed', 'sampling_pending', 'sticky', 'other')
PHASES = ('caller_identity', 'process_enumeration', 'remembered_identity',
    'family_selection', 'selected_identity', 'selected_rss', 'selected_recheck',
    'host_memory', 'disk_free', 'bind_identity', 'sample', 'family_tracking',
    'resident_check', 'original_wait', 'descendant_check')
CAUSES = ('AdmissionError', 'NoSuchProcess', 'ZombieProcess', 'AccessDenied',
    'Error', 'PermissionError', 'FileNotFoundError', 'ProcessLookupError',
    'OSError', 'KeyError', 'TypeError', 'ValueError', 'RuntimeError',
    'TimeoutExpired', 'other')
SAMPLE = {'subtype': 'busy_flight', 'reservation_held': True, 'sample_age_ms': None,
    'wait_ms': 0, 'grace_used': False, 'phase': None, 'cause': None}
PRIVATE = 'SELECT secret FROM credentials; /private/source.wav private-token'


def marker(value):
    # Deliberately constructed private tuple for accessor boundary tests only.
    # Separate real-permit cases below establish production marker creation.
    return (permit_mod._OPTIONAL_SAMPLE, value['subtype'], value['reservation_held'],
        value['sample_age_ms'], value['wait_ms'], value['phase'], value['cause'],
        value['grace_used'])


def observed(value):
    error = RuntimeError(PRIVATE)
    error._optional_sample_failure = marker(value)
    return runner.failure_observation('child_wait', 'child_failed', 'possible', error)


def enriched(value=None):
    return dict(json.loads(OLD_RAW), sampling=dict(SAMPLE if value is None else value))


def maximum_failure():
    # Independent field maxima, not a Cartesian product or large allocation.
    value = {'schema': store.FAILURE_SCHEMA,
        'stage': max(store.FAILURE_STAGES, key=len),
        'reason': max(store.FAILURE_REASONS, key=len),
        'child_dispatch': max(store.FAILURE_DISPATCH, key=len),
        'error_type': max(store.FAILURE_ERROR_TYPES, key=len),
        'sampling': {'subtype': max(SUBTYPES, key=len), 'reservation_held': False,
            'sample_age_ms': 2**31 - 1, 'wait_ms': 250, 'grace_used': False,
            'phase': max(PHASES, key=len), 'cause': max(CAUSES, key=len)}}
    return history.encoded(value)


@pytest.mark.parametrize('mode', ['busy', 'resource', 'other_exception'])
def test_real_permit_marker_reaches_probe_failure(mode, monkeypatch):
    permit = permit_mod.NativeExecutionPermit(settings(), sampler=lambda _: sample())
    permit._sample_clock = lambda: 10.
    token, cancelled = object(), threading.Event()
    permit.reserve_optional(token, cancelled, lambda: None, owner_kind='automatic_primary_probe')
    permit._last_sample_at = 9.
    if mode == 'busy':
        permit._sample_flight = object()
    elif mode == 'resource':
        permit._sampler = lambda _: sample(rss=7 * 1024**3)
    else:
        # check_optional authenticates any caught exception, not only the exact
        # AdmissionError class. Preserve that existing accessor contract.
        original = RuntimeError(PRIVATE)
        def fail_resource(**kwargs):
            raise original
        monkeypatch.setattr(permit, '_resource_check', fail_resource)
    try:
        with pytest.raises(RuntimeError) as caught:
            permit.check_optional(token, 'before_input')
        if mode == 'other_exception':assert caught.value is original
        detail = permit_mod.optional_sample_failure(caught.value)
        assert detail is not None
        assert detail['subtype'] == {'busy': 'busy_flight', 'resource': 'resource_limit',
                                     'other_exception': 'other'}[mode]
        assert detail['reservation_held'] is True
        assert detail['grace_used'] is False and detail['wait_ms'] == 0
        assert detail['sample_age_ms'] == (0 if mode == 'resource' else 1000)
        raw = runner.failure_observation('permit', 'permit_unavailable', 'not_attempted', caught.value)
        value = store.validate_failure(raw)
        assert value['sampling'] == detail
        assert value['error_type'] == ('RuntimeError' if mode == 'other_exception' else 'AdmissionError')
        assert cancelled.is_set() and permit._optional is token
        for secret in ('SELECT', '/private/source.wav', 'private-token'):
            assert secret.encode() not in raw
        detail['subtype'] = PRIVATE
        assert store.validate_failure(raw)['sampling']['subtype'] != PRIVATE
    finally:
        permit._sample_flight = None
        permit.release_optional(token, cleaned=True)


def test_old_five_field_bytes_and_null_remain_exact():
    assert runner.failure_observation('child_wait', 'child_failed', 'possible', RuntimeError(PRIVATE)) == OLD_RAW
    assert store.validate_failure(OLD_RAW) == json.loads(OLD_RAW)
    assert set(store.validate_failure(OLD_RAW)) == OLD_KEYS
    assert store.validate_failure(None) is None
    assert 'sampling' not in json.loads(runner.failure_observation('unknown', 'other', 'unknown'))


def test_forged_or_malformed_markers_are_omitted():
    valid = marker(SAMPLE)
    malformed = [None, list(valid), valid[:-1], valid + (0,),
        (object(),) + valid[1:],
        marker(dict(SAMPLE, subtype=PRIVATE)),
        marker(dict(SAMPLE, subtype={})),
        marker(dict(SAMPLE, reservation_held=1)),
        marker(dict(SAMPLE, sample_age_ms=True)),
        marker(dict(SAMPLE, sample_age_ms=-1)),
        marker(dict(SAMPLE, sample_age_ms=2**31)),
        marker(dict(SAMPLE, wait_ms=True)),
        marker(dict(SAMPLE, wait_ms=-1)),
        marker(dict(SAMPLE, wait_ms=251)),
        marker(dict(SAMPLE, grace_used=1)),
        marker(dict(SAMPLE, sample_age_ms=float('nan')))]
    assert len(malformed) == 16
    for value in malformed:
        error = RuntimeError(PRIVATE)
        error._optional_sample_failure = value
        error.reason = error.sql = error.path = PRIVATE
        assert permit_mod.optional_sample_failure(error) is None
        assert runner.failure_observation('child_wait', 'child_failed', 'possible', error) == OLD_RAW
    redacted = runner.failure_observation(PRIVATE, PRIVATE, PRIVATE, RuntimeError(PRIVATE))
    assert json.loads(redacted) == dict(json.loads(OLD_RAW), stage='unknown', reason='other', child_dispatch='unknown')


def test_every_sampling_enum_matches_accessor_and_validator():
    assert store.FAILURE_SAMPLING_SUBTYPES == SUBTYPES
    assert store.FAILURE_SAMPLING_PHASES == PHASES
    assert store.FAILURE_SAMPLING_CAUSES == CAUSES
    assert permit_mod._OBSERVATION_PHASES == frozenset(PHASES)
    checked = 0
    for field, values in (('subtype', SUBTYPES), ('phase', PHASES), ('cause', CAUSES)):
        for value in values:
            expected = dict(SAMPLE, **{field: value})
            assert store.validate_failure(observed(expected))['sampling'] == expected
            checked += 1
    assert checked == 45
    # Unknown optional observation strings are deliberately normalized to NULL.
    assert store.validate_failure(observed(dict(SAMPLE, phase=PRIVATE, cause={})))['sampling'] == SAMPLE


def test_sampling_scalar_bounds_shapes_and_canonical_rejections():
    accepted = [('reservation_held', False), ('grace_used', True),
        ('sample_age_ms', None), ('sample_age_ms', 0), ('sample_age_ms', 2**31 - 1),
        ('wait_ms', 0), ('wait_ms', 250), ('phase', None), ('cause', None)]
    for field, value in accepted:
        expected = dict(SAMPLE, **{field: value})
        assert store.validate_failure(observed(expected))['sampling'] == expected
    rejected = [('subtype', None), ('subtype', {}), ('subtype', 'unknown'),
        ('reservation_held', 1), ('reservation_held', None),
        ('grace_used', 0), ('grace_used', None),
        ('sample_age_ms', True), ('sample_age_ms', -1), ('sample_age_ms', 2**31), ('sample_age_ms', 0.),
        ('wait_ms', True), ('wait_ms', -1), ('wait_ms', 251), ('wait_ms', None), ('wait_ms', 0.),
        ('phase', 1), ('phase', {}), ('phase', 'unknown'),
        ('cause', 1), ('cause', {}), ('cause', 'unknown')]
    invalid = [history.encoded(enriched(dict(SAMPLE, **{field: value}))) for field, value in rejected]
    for field in SAMPLE:
        missing = dict(SAMPLE); del missing[field]
        invalid.append(history.encoded(enriched(missing)))
    invalid.append(history.encoded(enriched(dict(SAMPLE, extra='unknown'))))
    for value in (None, [], 'unknown'):
        invalid.append(history.encoded(dict(json.loads(OLD_RAW), sampling=value)))
    invalid.append(history.encoded(dict(enriched(), extra=True)))
    missing = enriched(); del missing['reason']
    invalid.extend([history.encoded(missing), history.encoded([enriched()]),
        OLD_RAW.decode(), bytearray(OLD_RAW), history.encoded(enriched()) + b' ', b' ' * 513])
    assert len(accepted) == 9 and len(rejected) == 22 and len(invalid) == 40
    for raw in invalid:
        with pytest.raises(ReadError):store.validate_failure(raw)


def test_maximum_encoding_fits_existing_failure_accounting():
    raw = maximum_failure()
    assert len(raw) == 376
    assert history.encoded(store.validate_failure(raw)) == raw
    assert store.MAX_FAILURE_BYTES == 512
    assert len(raw) <= store.MAX_FAILURE_BYTES < history.ATTEMPT_CHARGE == 1024
    assert history.BASIS_CHARGE == 1024
    assert set(store.validate_failure(raw)['sampling']) == set(SAMPLE)


def test_enriched_shape_exceeds_unchanged_dev51_five_key_contract():
    # The inspected dev51 validate_failure predicate requires exactly OLD_KEYS.
    # This is a shape/conservation assertion, not an old-binary execution test.
    # Dev51 source SHA-256: d0335b881f68da36efc36ac9ef0bf26d53713bcd5314d424612ff26cbd317253.
    old, enriched_value = store.validate_failure(OLD_RAW), store.validate_failure(maximum_failure())
    assert set(old) == OLD_KEYS
    assert set(enriched_value) == OLD_KEYS | {'sampling'}
    assert set(enriched_value) != OLD_KEYS
    assert old['schema'] == enriched_value['schema'] == 'recording-speaker-probe-failure-v1'
    assert history.encoded(old) == OLD_RAW


@pytest.mark.parametrize('origin,after_commit', [
    ('primary', False), ('primary', True), ('moss', False), ('moss', True),
], ids=['primary_write_failure', 'primary_lost_ack', 'moss_write_failure', 'moss_lost_ack'])
def test_sampling_failure_is_durable_frozen_charged_and_passive(history_env, monkeypatch, origin, after_commit):
    env = history_env
    if origin == 'primary':
        item = primary_fixture(env); reservation = claim(env, item)
    else:
        item, batch = moss_fixture(env); reservation = claim_moss(env, item, batch)
    with connection(env.store.path) as db:
        schema = list(db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))
    admitted = attempt_row(env.store, reservation['token'])
    pending = observation_rows(env.store, reservation['token'])[0]
    evidence = snapshot(env.store)
    diagnostic = maximum_failure()
    changed = json.loads(diagnostic); changed['sampling']['wait_ms'] = 249
    conflicting = history.encoded(changed)
    finish = getattr(env.value, 'finish_' + origin)
    for invalid in (history.encoded(enriched(dict(SAMPLE, sample_age_ms=True))), b' ' * 513):
        with pytest.raises(ReadError):finish(reservation, 'probe_failed', failure_json=invalid)
        assert snapshot(env.store) == evidence
    with database_write_fault(monkeypatch, env.value, after_commit=after_commit,
                              reason='synthetic_sampling_write_unknown'):
        with pytest.raises(ReadError, match='synthetic_sampling_write_unknown'):
            finish(reservation, 'probe_failed', failure_json=diagnostic)
    frozen = env.value._frozen[reservation['token']]
    assert frozen['failure_terminal'] == diagnostic
    uncertain = snapshot(env.store)
    with pytest.raises(ReadError, match='history_terminal_conflict'):
        finish(reservation, 'probe_failed', failure_json=conflicting)
    assert env.value._frozen[reservation['token']] is frozen
    assert frozen['failure_terminal'] == diagnostic and snapshot(env.store) == uncertain
    if not after_commit:assert uncertain == evidence
    restart_history(env)
    resolve = getattr(env.value, 'resolve_' + origin)
    result = resolve(reservation['token'], reservation['identity'], failure_json=diagnostic)
    assert result['found'] and result['resolved'] and not result['should_probe']
    terminal = attempt_row(env.store, reservation['token'])
    assert terminal['state'] == 'failure' and terminal['terminal'] == diagnostic
    assert terminal['terminal_sha256'] == hashlib.sha256(diagnostic).hexdigest()
    assert terminal['basis'] == admitted['basis']
    assert terminal['charge'] == admitted['charge'] == history.ATTEMPT_CHARGE + history.BASIS_CHARGE
    assert terminal['reserved_bytes'] == terminal['staging_bytes'] == 0
    missing = observation_rows(env.store, reservation['token'])[0]
    assert_missing_observation(missing, 'candidate_missing')
    assert {key: value for key, value in missing.items() if key != 'evidence_kind'} == {
        key: value for key, value in pending.items() if key != 'evidence_kind'}
    closed = snapshot(env.store)
    assert resolve(reservation['token'], reservation['identity'], failure_json=diagnostic)['state'] == 'failure'
    with pytest.raises(ReadError, match='history_terminal_conflict'):
        resolve(reservation['token'], reservation['identity'], failure_json=conflicting)
    assert snapshot(env.store) == closed
    for table in (history.OBJECTS, history.REFS, history.DEPENDENCIES, history.SOURCES):
        assert closed[table] == evidence[table]
    scope = scope_row(env.store, item.rid)
    assert (scope['history_revision'], scope['observation_count'], scope['vector_count']) == (2, 1, 0)
    assert scope['reserved_bytes'] == scope['staging_bytes'] == 0
    assert_accounting(env)

    restart_history(env)
    env.store.settings.recording_primary_observation_policy = 'off'
    env.store.settings.recording_speaker_links_enabled = False
    assert not env.value.enabled and env.value._frozen == {}
    original = env.value._database
    @contextmanager
    def read_only_database(*, write=False):
        assert write is False
        with original(write=False) as db:
            db.execute('PRAGMA query_only=ON')
            yield db
    def forbidden(*args, **kwargs):
        pytest.fail('Passive sampling audit attempted active work')
    monkeypatch.setattr(env.value, '_database', read_only_database)
    for name in ('begin_comparison', 'revalidate_active_sources', 'append_primary', 'append_moss',
                 'finish_primary', 'finish_moss', 'resolve_primary', 'resolve_moss'):
        monkeypatch.setattr(env.value, name, forbidden)
    monkeypatch.setattr(env.owner.cell_source.owner.authority, 'receipt_valid', forbidden)
    monkeypatch.setattr(primary, 'standalone_current', forbidden)
    audited = env.value.read_attempt(item.sid, item.rid, reservation['token'])
    assert audited['state'] == 'failure' and audited['audit_only'] is True
    assert audited['execution_authorized'] is False
    assert audited['invocation_id'] == terminal['invocation_id'] == invocation_for(reservation['token'])
    assert audited['basis_bytes'] == terminal['basis']
    assert audited['terminal_bytes'] == diagnostic
    assert audited['terminal_sha256'] == terminal['terminal_sha256']
    assert audited['failure'] == audited['terminal'] == json.loads(diagnostic)
    assert env.value.read_attempt(item.sid, item.rid, reservation['token']) == audited
    assert snapshot(env.store) == closed and env.owner.invocations == []
    assert env.value._frozen == {}
    with connection(env.store.path) as db:
        assert list(db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')) == schema
    assert_accounting(env)
