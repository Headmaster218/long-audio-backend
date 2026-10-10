"""Immutable published dev11 composition and current synthetic transaction checks."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

import pytest

from scripts import source_staging_guard as guard
from scripts.public_history import PublicHistory

ROOT = Path(__file__).resolve().parents[1]
DEV11 = '5b71122f17e4a72cac6ad4120d1c707d9b2133fb'
DEV11_TREE = '7e7547e1b49473dd24c69887e772b4580014c236'
PARENTS = ['366797b5c0bba70975507b71db8b4596b6d9344f',
           '5cca518467e5d03eaced884f73798917542f79fd']
BASE = '4bde1e8d2fd050a10a1168e4cb46f80a5db6d28c'
PINS = {
    PARENTS[0]: {'tree': 'dc6c9bd493238b4b6c41b087eb668a60f1726895',
                 'parents': ['0a27197979c8e96fa560432f7a15f49c9e1965fb']},
    PARENTS[1]: {'tree': 'e35afda019d60bb0590b7b65480f55dbc38f2ef6', 'parents': [BASE]},
    BASE: {'tree': 'd6729a14d0981a58d191f21b788af62772155085',
           'parents': ['ba1ebe8dacaca8dda651bb7671b9c0036b80e85b',
                       '702c5055551767b3db41b6b296223aa60a6f78df']},
}
MANIFEST = 'docs/BACKEND_DEV11_INTEGRATION.json'
POLICY = 'docs/FRAGMENT_STAGING_POLICY.json'
LAYER_PATH = 'docs/FRAGMENT_STAGING_LAYERS.json'
HISTORICAL_DEV8 = '5ec6c9d5bd3febf2c30695120b969e6b70a1c0d2'
ADDED = {MANIFEST, 'docs/BACKEND_DEV11_INTEGRATION.md',
         'tests/test_backend_dev11_integration.py',
         'docs/EVENT_CANDIDATE_NUMERIC_OWNERSHIP.md',
         'docs/EVENT_CANDIDATE_NUMERIC_SOURCE.json',
         'tests/test_event_candidate_numeric_projection.py'}
CHANGED = {'README.md', 'audio_backend/__init__.py', 'pyproject.toml', POLICY,
           'audio_backend/event_candidate_store.py', 'audio_backend/storage.py',
           'audio_backend/worker.py', 'tests/test_fragment_staging_sources.py',
           'tests/test_public_history.py', 'tests/test_boundaries.py', 'tests/conftest.py',
           'tests/test_positive_event_seams.py', 'tests/test_fragment_decoded_source.py'}
NUMERIC_INHERITED = {'audio_backend/event_candidate_store.py',
                     'docs/EVENT_CANDIDATE_NUMERIC_OWNERSHIP.md',
                     'docs/EVENT_CANDIDATE_NUMERIC_SOURCE.json',
                     'tests/test_event_candidate_numeric_projection.py'}
COMPOSED = {
    'audio_backend/storage.py': {'bytes': 53164,
        'source_blob': '8a92aad2c32b603f4ed6dcfe1884152d99bc3aad',
        'sha256': '52112499c2e59046a466c64deb31e84d0208cb5a67c7bcfb2f273fbdb76f58f5'},
    'audio_backend/worker.py': {'bytes': 19455,
        'source_blob': '5b292d429fafce4446f046be306476ccf0db949e',
        'sha256': '9e59a3ceeccc33ebed18d3eb0aa6006b876276f1c3e71b22f6cd8b5565e52ea2'},
}
PURPOSE = ('Isolated dev11 duration/EOS, overlap-retention and bounded event-numeric '
           'integration; immutable historical pins and privacy exclusions unchanged')
SCOPE = {
    'runtime': 'Reviewed dev9 duration/EOS, dev10 private overlap retention and bounded v2 event numeric composition',
    'history': 'Original-to-public map and historical manifests unchanged; historical gates use immutable public heads',
    'acceptance': 'Model-free source integration only; no release, real-model quality or long-run acceptance',
    'numeric_limits': 'Bounded endpoint roundoff convention; arbitrary cancellation/rebasing and global timestamp precision remain unproved',
}


def canonical(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'source_blob': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}


def git(*args):
    return PublicHistory(ROOT)._git(*args)


def verified_revision(ref):
    assert ref in PINS, 'Unreviewed integration input'
    headers = git('cat-file', 'commit', ref).split(b'\n\n', 1)[0].splitlines()
    assert [line[5:].decode() for line in headers if line.startswith(b'tree ')] == [PINS[ref]['tree']]
    assert [line[7:].decode() for line in headers if line.startswith(b'parent ')] == PINS[ref]['parents']
    assert git('cat-file', '-t', PINS[ref]['tree']).strip() == b'tree'
    for parent in PINS[ref]['parents']:
        assert git('cat-file', '-t', parent).strip() == b'commit'
    return ref


@lru_cache(maxsize=None)
def source_tree(ref):
    return guard.read_git_tree(verified_revision(ref))


def dev8_tree():
    assert PublicHistory(ROOT).resolve(HISTORICAL_DEV8) == BASE
    return source_tree(BASE)


def numeric_tree():
    return source_tree(PARENTS[1])


def tree_modes(ref):
    result = {}
    for entry in git('ls-tree', '-rz', ref).split(b'\0'):
        if entry:
            header, name = entry.decode().split('\t')
            mode, kind, _ = header.split()
            assert kind == 'blob' and mode in {'100644', '100755'}
            result[name] = mode
    return result


def expected_manifest(files):
    left, right = (source_tree(ref) for ref in PARENTS)
    rows = []
    for path in sorted((ADDED | CHANGED) - {MANIFEST}):
        row = {'path': path, **pin(files[path])}
        row.update({'supersedes': pin(left[path])} if path in left else {'base_absent': True})
        if path in NUMERIC_INHERITED:
            row['source_commit'] = PARENTS[1]
        if path in COMPOSED:
            row['inputs'] = {ref: pin(source_tree(ref)[path]) for ref in [BASE, *PARENTS]}
        rows.append(row)
    return {'schema': 'reviewed-backend-dev11-integration-v1', 'version': '0.2.0rc2.dev11',
            'parents': PARENTS, 'input_pins': PINS, 'merge_base': BASE, 'scope': SCOPE,
            'source_budget': {'maximum_files': 283, 'maximum_total_bytes': 3400000,
                              'maximum_file_bytes': 250000, 'prior_total_byte_cap': 3200000},
            'manifest_self_hash': 'excluded-to-avoid-recursion; exact schema and canonical serialization validated',
            'files': rows}


def verify_integration(files, index, modes, index_modes):
    left, right = (source_tree(ref) for ref in PARENTS)
    assert files == index, 'Dev11 worktree/index mismatch'
    assert set(files) == set(left) | ADDED and len(files) == 283
    assert {path for path in files if files[path] != left.get(path)} == ADDED | CHANGED
    assert files[MANIFEST] == canonical(expected_manifest(files)), 'Integration pins, schema, order or types differ'
    for path in set(left) - CHANGED:
        assert files[path] == left[path], path
    for path in NUMERIC_INHERITED:
        assert files[path] == right[path], 'Numeric historical source changed'
    for path, expected in COMPOSED.items():
        assert pin(files[path]) == expected, 'Reviewed runtime composition changed'
    for path in ['audio_backend/__init__.py', 'pyproject.toml']:
        assert files[path] == left[path].replace(b'0.2.0rc2.dev10', b'0.2.0rc2.dev11')
    assert tomllib.loads(files['pyproject.toml'].decode())['project']['version'] == '0.2.0rc2.dev11'
    original = json.loads(left[POLICY])
    expected_policy = dict(original, purpose=PURPOSE, maximum_files=283, maximum_total_bytes=3400000,
                           allowed_files=sorted(set(original['allowed_files']) | ADDED))
    assert files[POLICY] == canonical(expected_policy), 'Policy exceeds reviewed integration delta'
    assert set(expected_policy['allowed_files']) == set(files)
    assert guard.MAX_BYTES == 250000
    assert guard.validate(files, expected_policy, require_manifest=False)['findings'] == 0
    expected_modes = {**tree_modes(PARENTS[0]), **{path: '100644' for path in ADDED}}
    assert modes == index_modes == expected_modes, 'Dev11 source modes differ'
    for target in re.findall(r'\]\(([^)]+)\)', files['README.md'].decode()):
        if '://' not in target and not target.startswith('#'):
            assert target.split('#', 1)[0] in files, target


def dev11_revision():
    headers = git('cat-file', 'commit', DEV11).split(b'\n\n', 1)[0].splitlines()
    assert [line[5:].decode() for line in headers if line.startswith(b'tree ')] == [DEV11_TREE]
    assert [line[7:].decode() for line in headers if line.startswith(b'parent ')] == PARENTS
    assert git('cat-file', '-t', DEV11_TREE).strip() == b'tree'
    return DEV11


@pytest.fixture(scope='module')
def integrated_sources():
    # Historical proof retains its exact 19-path composition and mutations.
    files = guard.read_git_tree(dev11_revision())
    modes = tree_modes(DEV11)
    assert len(files) == 283 and sum(map(len, files.values())) == 3317327
    return files, files, modes, modes


def test_dev11_exact_historical_integration(integrated_sources):
    verify_integration(*integrated_sources)


def test_dev11_actual_two_parent_merge():
    dev11_revision()
    for ref in [BASE, *PARENTS]:
        verified_revision(ref)
    assert git('merge-base', '--all', *PARENTS).decode().splitlines() == [BASE]


@pytest.mark.parametrize('mutation', [
    'missing', 'extra', 'inherited', 'numeric', 'storage', 'worker', 'version',
    'historical_layers', 'history_map', 'history_helper', 'recovery_manifest',
    'numeric_manifest', 'policy_cap', 'policy_paths', 'policy_privacy', 'policy_count',
    'manifest_parent_order', 'manifest_base', 'manifest_scope', 'manifest_schema',
    'manifest_types', 'manifest_duplicate', 'manifest_self', 'manifest_format',
    'worktree', 'index', 'mode', 'index_mode',
])
def test_dev11_rejects_unreviewed_composition(mutation, integrated_sources):
    verify_integration(*integrated_sources)
    files, index, modes, index_modes = map(dict, integrated_sources)
    if mutation == 'missing': files.pop('docs/API.md')
    elif mutation == 'extra': files['docs/unreviewed.md'] = b'synthetic'
    elif mutation in {'inherited', 'numeric', 'storage', 'worker', 'version', 'historical_layers',
                      'history_map', 'history_helper', 'recovery_manifest', 'numeric_manifest',
                      'worktree', 'index'}:
        path = {'inherited': 'audio_backend/settings.py', 'numeric': 'audio_backend/event_candidate_store.py',
                'storage': 'audio_backend/storage.py', 'worker': 'audio_backend/worker.py',
                'version': 'pyproject.toml', 'historical_layers': LAYER_PATH,
                'history_map': 'docs/PUBLIC_HISTORY_MAP.json', 'history_helper': 'scripts/public_history.py',
                'recovery_manifest': 'docs/PUBLIC_RECOVERY_FOLLOWUP.json',
                'numeric_manifest': 'docs/EVENT_CANDIDATE_NUMERIC_SOURCE.json'}.get(mutation, 'README.md')
        (index if mutation == 'index' else files)[path] += b'\n'
    elif mutation.startswith('policy_'):
        policy = json.loads(files[POLICY])
        if mutation == 'policy_cap': policy['maximum_total_bytes'] += 1
        elif mutation == 'policy_paths': policy['allowed_files'].append('docs/unreviewed.md')
        elif mutation == 'policy_count': policy['maximum_files'] += 1
        else: policy['excluded_categories'].pop()
        files[POLICY] = canonical(policy)
    elif mutation in {'mode', 'index_mode'}:
        (index_modes if mutation == 'index_mode' else modes)['audio_backend/storage.py'] = '100755'
    else:
        value = json.loads(files[MANIFEST])
        if mutation == 'manifest_parent_order': value['parents'].reverse()
        elif mutation == 'manifest_base': value['merge_base'] = '0' * 40
        elif mutation == 'manifest_scope': value['scope']['acceptance'] = 'model quality accepted'
        elif mutation == 'manifest_schema': value['schema'] = 'unreviewed'
        elif mutation == 'manifest_types': value['source_budget']['maximum_files'] = 283.0
        elif mutation == 'manifest_duplicate': value['files'].append(value['files'][0])
        elif mutation == 'manifest_self': value['files'].append({'path': MANIFEST, **pin(files[MANIFEST])})
        files[MANIFEST] = canonical(value) + (b'\n' if mutation == 'manifest_format' else b'')
    # Refresh pins for source mutations so independent inheritance/composition/
    # policy checks, not just a stale manifest, must reject the altered source.
    if mutation not in {'missing', 'worktree', 'index'} and not mutation.startswith('manifest_'):
        files[MANIFEST] = canonical(expected_manifest(files))
    if mutation not in {'worktree', 'index'}: index = dict(files)
    with pytest.raises((AssertionError, ValueError)):
        verify_integration(files, index, modes, index_modes)


# Historical standalone numeric proof remains exact, separate from dev11.
NUMERIC_MANIFEST = 'docs/EVENT_CANDIDATE_NUMERIC_SOURCE.json'
NUMERIC_ADDITIONS = {NUMERIC_MANIFEST, 'docs/EVENT_CANDIDATE_NUMERIC_OWNERSHIP.md',
                     'tests/test_event_candidate_numeric_projection.py'}
NUMERIC_PRODUCTION = {
    'audio_backend/event_candidate_store.py': '0e8224ae8ecc7a8f56b4bd461e11e65816f04ee19370c3873dbbc2f20d7cf968',
    'audio_backend/storage.py': 'c684d14b65e12d39ce6919323467bcc5ca8bd8c568c750520c5dd8fa24bfc401',
    'audio_backend/worker.py': '51e56dbe04cb9bf85e1ee265744856a0257689a7f5b734299da74dbb132050af',
}
NUMERIC_CHANGED = set(NUMERIC_PRODUCTION) | {
    'docs/FRAGMENT_STAGING_POLICY.json', 'tests/test_fragment_staging_sources.py'}
NUMERIC_SCOPE = {
    'status': 'standalone-dev8-derived-source-fix-not-a-release',
    'production': 'event candidate numeric ownership and bounded diagnostic persistence only',
    'unchanged': 'ASR, detector, speaker models, thresholds, schema and historical provenance',
    'validation': 'synthetic model-free numeric and transactional checks; no quality claim',
}


def verify_numeric_source_followup(files, baseline):
    """Pin only the reviewed delta while retaining every historical byte outside it."""
    assert set(baseline) == set(json.loads(baseline['docs/FRAGMENT_STAGING_POLICY.json'])['allowed_files'])
    assert len(baseline) == 267
    assert set(files) == set(baseline) | NUMERIC_ADDITIONS
    changed = {path for path in baseline if files[path] != baseline[path]}
    assert changed == NUMERIC_CHANGED, 'Unreviewed or missing inherited-source delta'
    manifest = json.loads(files[NUMERIC_MANIFEST])
    assert set(manifest) == {'schema', 'base_commit', 'public_base_commit', 'base_tree',
                            'version', 'scope', 'manifest_self_hash', 'files'}
    assert manifest['schema'] == 'reviewed-event-numeric-source-v1'
    assert manifest['base_commit'] == HISTORICAL_DEV8
    assert manifest['public_base_commit'] == '4bde1e8d2fd050a10a1168e4cb46f80a5db6d28c'
    assert manifest['base_tree'] == 'd6729a14d0981a58d191f21b788af62772155085'
    assert manifest['version'] == '0.2.0rc2.dev8'
    assert manifest['scope'] == NUMERIC_SCOPE
    assert manifest['manifest_self_hash'] == 'excluded-to-avoid-recursion; structure-validated'
    rows = manifest['files']
    expected = NUMERIC_CHANGED | (NUMERIC_ADDITIONS - {NUMERIC_MANIFEST})
    assert len(rows) == len(expected) and {row['path'] for row in rows} == expected
    for row in rows:
        path = row['path']; data = files[path]
        fields = {'path', 'bytes', 'sha256', 'source_blob'}
        if path in baseline:
            assert set(row) == fields | {'supersedes'}
            old = baseline[path]
            assert row['supersedes'] == {
                'sha256': hashlib.sha256(old).hexdigest(),
                'source_blob': hashlib.sha1(b'blob ' + str(len(old)).encode() + b'\0' + old).hexdigest()}
        else:
            assert set(row) == fields | {'base_absent'} and row['base_absent'] is True
        assert row['bytes'] == len(data) and row['sha256'] == hashlib.sha256(data).hexdigest()
        assert row['source_blob'] == hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    for path, digest in NUMERIC_PRODUCTION.items():
        assert hashlib.sha256(files[path]).hexdigest() == digest, 'Reviewed production bytes changed'
    previous_policy = json.loads(baseline['docs/FRAGMENT_STAGING_POLICY.json'])
    policy = json.loads(files['docs/FRAGMENT_STAGING_POLICY.json'])
    expected_policy = dict(previous_policy, maximum_files=270,
                           allowed_files=sorted(set(previous_policy['allowed_files']) | NUMERIC_ADDITIONS))
    assert policy == expected_policy, 'Policy broadened beyond the three reviewed additions'
    assert guard.MAX_BYTES == 250_000
    assert guard.validate(files, policy, require_manifest=False)['findings'] == 0


def test_historical_numeric_source_exact_public_tree():
    baseline = dev8_tree()
    assert subprocess.check_output(['git', 'rev-parse', PublicHistory(ROOT).resolve(HISTORICAL_DEV8) + '^{tree}'], cwd=ROOT).decode().strip() == 'd6729a14d0981a58d191f21b788af62772155085'
    staged = numeric_tree()
    worktree = staged  # Both views are the immutable reviewed numeric source tree.
    verify_numeric_source_followup(staged, baseline)
    verify_numeric_source_followup(worktree, baseline)


@pytest.mark.parametrize('mutation', [
    'missing_file', 'extra_file', 'production', 'inherited', 'manifest_key', 'manifest_self',
    'base', 'public_base', 'base_tree', 'version', 'scope', 'missing_row', 'duplicate_row',
    'extra_row', 'bytes', 'hash', 'blob', 'supersedes', 'absence', 'policy_budget',
    'policy_paths', 'policy_exclusions', 'document', 'test', 'layer_history',
])
def test_numeric_source_rejects_unreviewed_delta(mutation):
    baseline = dev8_tree()
    files = dict(numeric_tree())
    # A passing baseline prevents a pre-existing failure from masking a weak case.
    verify_numeric_source_followup(files, baseline)
    manifest = json.loads(files[NUMERIC_MANIFEST])
    policy_path = 'docs/FRAGMENT_STAGING_POLICY.json'
    if mutation == 'missing_file': files.pop('tests/test_event_candidate_numeric_projection.py')
    elif mutation == 'extra_file': files['docs/unreviewed.md'] = b'synthetic'
    elif mutation == 'production': files['audio_backend/worker.py'] += b'\n'
    elif mutation == 'inherited': files['audio_backend/settings.py'] += b'\n'
    elif mutation == 'manifest_key': manifest['unreviewed'] = True
    elif mutation == 'manifest_self': manifest['manifest_self_hash'] = 'not checked'
    elif mutation == 'base': manifest['base_commit'] = '0' * 40
    elif mutation == 'public_base': manifest['public_base_commit'] = '0' * 40
    elif mutation == 'base_tree': manifest['base_tree'] = '0' * 40
    elif mutation == 'version': manifest['version'] = 'release'
    elif mutation == 'scope': manifest['scope']['status'] = 'production-ready'
    elif mutation == 'missing_row': manifest['files'].pop()
    elif mutation == 'duplicate_row': manifest['files'].append(manifest['files'][0])
    elif mutation == 'extra_row': manifest['files'][0]['path'] = NUMERIC_MANIFEST
    elif mutation == 'bytes': manifest['files'][0]['bytes'] += 1
    elif mutation == 'hash': manifest['files'][0]['sha256'] = '0' * 64
    elif mutation == 'blob': manifest['files'][0]['source_blob'] = '0' * 40
    elif mutation == 'supersedes': next(r for r in manifest['files'] if 'supersedes' in r)['supersedes']['sha256'] = '0' * 64
    elif mutation == 'absence': next(r for r in manifest['files'] if 'base_absent' in r)['base_absent'] = False
    elif mutation.startswith('policy_'):
        policy = json.loads(files[policy_path])
        if mutation == 'policy_budget': policy['maximum_total_bytes'] += 1
        elif mutation == 'policy_paths': policy['allowed_files'].append('docs/unreviewed.md')
        else: policy['excluded_categories'].pop()
        files[policy_path] = json.dumps(policy).encode()
        # Refresh this row so the policy-specific restriction must reject it.
        row = next(r for r in manifest['files'] if r['path'] == policy_path)
        data = files[policy_path]
        row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                   source_blob=hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest())
    elif mutation == 'document': files['docs/EVENT_CANDIDATE_NUMERIC_OWNERSHIP.md'] += b'\n'
    elif mutation == 'test': files['tests/test_event_candidate_numeric_projection.py'] += b'\n'
    elif mutation == 'layer_history': files[LAYER_PATH] += b'\n'
    files[NUMERIC_MANIFEST] = json.dumps(manifest).encode()
    with pytest.raises((AssertionError, ValueError)):
        verify_numeric_source_followup(files, baseline)


# Combined runtime regressions use synthetic evidence only, never model output.
import copy
import math
import sqlite3
import wave

from fastapi.testclient import TestClient

from audio_backend import overlap_diagnostics as diagnostics
from audio_backend import resources
from audio_backend.app import create_app
from audio_backend.moss_td_candidates import plan_supports
from audio_backend.overlap_candidates import validate_candidates
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.worker import own_core

RATE = 16000
FRAMES = 32003
END = FRAMES / RATE
ORIGIN = float(2**20)
FAILED_TEXT = 'synthetic failed overlap 少数发言\nsecond line'
INVALID_PHRASE = 'synthetic untrusted minority phrase'
PRIVATE_LABEL = 'Female speech, woman speaking'
UNKNOWN_VALUE = 'synthetic unknown numeric evidence field'


def event(start, end, private=False):
    value = {'start': start, 'end': end,
             'top_labels': [{'label': 'Door', 'score': .25}],
             'category_scores': {'door': {'source_label': 'Door', 'score': .25}},
             'model_inference_executed': True}
    if private:
        value['top_labels'].append({'label': PRIVATE_LABEL, 'score': .75})
        value['category_scores']['music'] = {'source_label': PRIVATE_LABEL, 'score': .5}
        value['unknown_field'] = UNKNOWN_VALUE
    return value


def combined_result():
    turns = [{'start': 0., 'end': END, 'speaker_local_id': 'a'},
             {'start': .25, 'end': .75, 'speaker_local_id': 'b'},
             {'start': 1.25, 'end': 1.75, 'speaker_local_id': 'b'}]
    regions, plans = plan_supports(turns, END, {})
    assert len(regions) == 2 and len(plans) == 1
    failed = {'support_id': plans[0]['shared_support_id'], 'status': 'unresolved',
              'reason_code': 'unparsed_text', 'raw_text': FAILED_TEXT,
              'schema_errors': ['synthetic invalid native interval'],
              'diagnostic_phrases': [{'start': -5, 'end': -8, 'text': INVALID_PHRASE}],
              'phrases': [], 'metadata': {}}
    for region in regions:
        region.update(status='unresolved', reason_code='unparsed_text', schema_error_count=1,
                      raw_output_sha256=hashlib.sha256(FAILED_TEXT.encode()).hexdigest())
    overlap = {'regions': regions, 'tracks': [],
               'diagnostics': [diagnostics.support(plans[0], failed)]}
    validate_candidates(overlap, END)
    result = own_core({
        'segments': [
            {'start': .125, 'end': .25, 'text': 'synthetic original mixture',
             'raw_text': 'synthetic original raw text', 'speaker_local_id': None},
            {'start': (FRAMES - 1) / RATE, 'end': END, 'text': 'synthetic final sample',
             'raw_text': 'synthetic final sample raw text', 'speaker_local_id': None}],
        'events': [], 'speaker_turns': [],
        'metadata': {'event_candidates': [event(.125, .25),
                     event(math.nextafter(END, -math.inf), END + 1., private=True)]},
        'overlap_candidates': overlap}, 0., END)
    assert len(result['event_candidates']) == 1
    assert len(result['metadata']['event_candidate_projection_diagnostics']) == 1
    assert len(result['overlap_candidates']['diagnostics']) == 1
    return result


@pytest.fixture
def combined_env(tmp_path):
    settings = Settings(data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        sid = store.create_session('synthetic combined regression')['id']
        source = tmp_path / 'synthetic.wav'
        with wave.open(str(source), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(RATE)
            output.writeframes(b'\x01\x00' * FRAMES)
        cid, _ = store.add_chunk(sid, 'synthetic-key', 'synthetic-digest', source, END, offset=ORIGIN)
        chunk = store.claim()
        assert chunk['id'] == cid
        yield client, store, sid, chunk


def snapshot(client, store, sid, cid):
    response = client.get(f'/sessions/{sid}/changes')
    assert response.status_code == 200
    return {'chunk': store.chunk(cid), 'segments': store.results(sid),
            'events': store.events(sid), 'event_candidates': store.event_candidates(sid),
            'overlap': store.overlap_candidates(sid), 'diagnostics': store.overlap_diagnostics(sid),
            'changes': response.json()}


def assert_combined_counts(store):
    expected = {'segments': 2, 'events': 0, 'event_candidates': 1, 'overlap_regions': 2,
                'overlap_tracks': 0, 'overlap_support_diagnostics': 1, 'speakers': 0,
                'speaker_turns': 0, 'speaker_examples': 0, 'speaker_observations': 0}
    with store.db() as db:
        for table, count in expected.items():
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == count, table
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_combined_final_core_preserves_text_and_both_audits(combined_env):
    _, store, sid, chunk = combined_env
    result = combined_result()
    before = copy.deepcopy(result)
    assert store.commit_window(chunk, 0., END, result)
    assert result == before
    saved = store.chunk(chunk['id'])
    assert saved['status'] == 'completed'
    assert saved['duration'] == saved['next_offset'] == END
    assert saved['timeline_start'] == ORIGIN
    segments = store.results(sid)['segments']
    for row, original in zip(segments, before['segments'], strict=True):
        assert row['text'] == original['text'] and row['raw_text'] == original['raw_text']
        assert row['start'] == ORIGIN + original['start']
        assert row['end'] == ORIGIN + original['end']
        assert row['speaker_id'] is None and row['speaker_local_id'] is None
    assert segments[-1]['end'] == ORIGIN + END
    audit = saved['provenance']['event_candidate_projection_diagnostics']
    assert audit['count'] == 1 and not audit['truncated']
    note = audit['records'][0]
    assert note['reason'] == 'numeric_degenerate_sample_grid_intersection'
    assert note['intersection_frames'] == [FRAMES, FRAMES]
    assert note['analysis_offset'] == 0. and note['raw_time_axis'] == 'analyzer_input'
    private = store.overlap_diagnostics(sid)['diagnostics']
    assert len(private) == 1
    envelope = private[0]['envelope']
    assert envelope['time_axis'] == 'session_absolute'
    assert envelope['support_start'] == ORIGIN and envelope['support_end'] == ORIGIN + END
    assert envelope['captures'] == before['overlap_candidates']['diagnostics'][0]['captures']
    assert envelope['captures']['raw_text']['text'] == FAILED_TEXT
    assert not store.overlap_candidates(sid)['tracks']
    assert_combined_counts(store)


def test_late_failure_rolls_back_retry_and_replay_without_duplicate_audits(combined_env):
    client, store, sid, chunk = combined_env
    result = combined_result()
    # Exercise storage-phase absolute collapse alongside ownership-phase audit.
    legacy = event(.5, math.nextafter(.5, math.inf), private=True)
    legacy.update(support_start=0., support_end=END, time_axis='core_relative',
                  ownership='core_intersection', _source_candidate_index=99)
    assert ORIGIN + legacy['start'] == ORIGIN + legacy['end']
    result['event_candidates'].append(legacy)
    before = copy.deepcopy(result)
    baseline = snapshot(client, store, sid, chunk['id'])
    # Abort after both candidate ledgers are inserted and provenance is computed.
    with store.db() as db:
        db.execute("""CREATE TRIGGER dev11_abort_commit
            BEFORE UPDATE OF next_offset ON chunks WHEN NEW.next_offset > OLD.next_offset
            BEGIN SELECT RAISE(ABORT, 'synthetic dev11 late commit failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='synthetic dev11 late'):
        store.commit_window(chunk, 0., END, result)
    assert result == before
    assert snapshot(client, store, sid, chunk['id']) == baseline
    with store.db() as db:
        for table in ('segments', 'events', 'event_candidates', 'overlap_regions', 'overlap_tracks',
                      'overlap_support_diagnostics', 'speakers', 'speaker_turns',
                      'speaker_examples', 'speaker_observations'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0, table
        db.execute('DROP TRIGGER dev11_abort_commit')
    assert store.commit_window(chunk, 0., END, result)
    assert result == before
    audit = store.chunk(chunk['id'])['provenance']['event_candidate_projection_diagnostics']
    assert audit['count'] == len(audit['records']) == 2
    assert {note['reason'] for note in audit['records']} == {
        'numeric_degenerate_sample_grid_intersection', 'numeric_degenerate_absolute_projection'}
    assert_combined_counts(store)
    committed = snapshot(client, store, sid, chunk['id'])
    reopened = Store(store.path, store.settings)
    assert not reopened.commit_window(reopened.chunk(chunk['id']), 0., END, result)
    assert snapshot(client, reopened, sid, chunk['id']) == committed
    assert result == before


def test_disk_reserve_failure_keeps_both_ledgers_and_cursor_unchanged(combined_env, monkeypatch):
    client, store, sid, chunk = combined_env
    result = combined_result()
    before = copy.deepcopy(result)
    baseline = snapshot(client, store, sid, chunk['id'])
    requested = []
    def reject(settings, additional_disk=0):
        assert settings is store.settings
        requested.append(additional_disk)
        raise RuntimeError('synthetic combined disk reserve')
    with monkeypatch.context() as patch:
        patch.setattr(resources, 'check_resources', reject)
        with pytest.raises(RuntimeError, match='synthetic combined disk reserve'):
            store.commit_window(chunk, 0., END, result)
    assert requested == [4 * len(diagnostics.encoded(result['overlap_candidates']['diagnostics'])) + 65536]
    assert result == before
    assert snapshot(client, store, sid, chunk['id']) == baseline
    assert store.chunk(chunk['id'])['next_offset'] == 0.
    assert store.commit_window(chunk, 0., END, result)
    assert store.chunk(chunk['id'])['provenance']['event_candidate_projection_diagnostics']['count'] == 1
    assert_combined_counts(store)


def test_http_hides_failed_overlap_and_sanitizes_numeric_provenance(combined_env):
    client, store, sid, chunk = combined_env
    result = combined_result()
    original_candidates = copy.deepcopy(result['metadata']['event_candidates'])
    assert store.commit_window(chunk, 0., END, result)
    public = {}
    for endpoint in ('segments', 'events', 'event-candidates', 'overlap-candidates', 'speaker-turns', 'changes'):
        response = client.get(f'/sessions/{sid}/{endpoint}')
        assert response.status_code == 200
        public[endpoint] = response.json()
    encoded = json.dumps(public, ensure_ascii=False)
    for marker in (FAILED_TEXT, INVALID_PHRASE, PRIVATE_LABEL, UNKNOWN_VALUE):
        assert marker not in encoded
    overlap = public['overlap-candidates']
    assert 'diagnostics' not in overlap and len(overlap['regions']) == 2 and overlap['tracks'] == []
    assert all(row['status'] == 'unresolved' and row['reason_code'] == 'unparsed_text' for row in overlap['regions'])
    assert public['events']['events'] == []
    assert len(public['event-candidates']['event_candidates']) == 1
    changes = public['changes']['changes']
    assert not any(row['entity'] == 'overlap_support_diagnostics' for row in changes)
    assert sum(row['entity'] == 'event_candidates' for row in changes) == 1
    response = client.get(f"/chunks/{chunk['id']}")
    assert response.status_code == 200
    assert FAILED_TEXT not in response.text and INVALID_PHRASE not in response.text
    saved = response.json()
    # The original last-window metadata remains intact; durable audit is sanitized.
    assert saved['metadata']['event_candidates'] == original_candidates
    audit = saved['provenance']['event_candidate_projection_diagnostics']
    assert audit['count'] == len(audit['records']) == 1
    encoded = json.dumps(audit, ensure_ascii=False)
    assert PRIVATE_LABEL not in encoded and UNKNOWN_VALUE not in encoded and 'unknown_field' not in encoded
    note = audit['records'][0]
    assert note['candidate_evidence_sanitized'] is True
    assert note['candidate_evidence']['top_labels'] == [{'label': 'Door', 'score': .25}]
    raw = json.dumps(original_candidates[1], sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    assert note['raw_candidate_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    private = store.overlap_diagnostics(sid)
    assert len(private['diagnostics']) == 1
    diagnostic = private['diagnostics'][0]
    assert diagnostic['envelope']['captures']['raw_text']['text'] == FAILED_TEXT
    refs = store.overlap_diagnostic_status(sid)['regions']
    assert len(refs) == 2 and {row['diagnostic_id'] for row in refs} == {diagnostic['id']}
    assert all(row['full_raw_output_available'] for row in refs)
    assert store.overlap_diagnostics(sid, after=private['next_cursor'])['diagnostics'] == []


@pytest.mark.parametrize('target', ['audio_backend.overlap_store.commit_candidates',
                                     'audio_backend.storage.update_provenance'])
def test_each_combined_persistence_boundary_rolls_back(combined_env, monkeypatch, target):
    client, store, sid, chunk = combined_env
    result = combined_result()
    before = copy.deepcopy(result)
    baseline = snapshot(client, store, sid, chunk['id'])
    def reject(*args, **kwargs):
        raise RuntimeError('synthetic persistence boundary failure')
    with monkeypatch.context() as patch:
        patch.setattr(target, reject)
        with pytest.raises(RuntimeError, match='synthetic persistence boundary failure'):
            store.commit_window(chunk, 0., END, result)
    assert result == before
    assert snapshot(client, store, sid, chunk['id']) == baseline
    assert store.commit_window(chunk, 0., END, result)
    assert_combined_counts(store)
    assert store.chunk(chunk['id'])['provenance']['event_candidate_projection_diagnostics']['count'] == 1


@pytest.mark.parametrize('silence', [False, True])
def test_later_empty_or_silence_core_preserves_both_diagnostic_histories(combined_env, silence):
    _, store, sid, chunk = combined_env
    with store.db() as db:
        db.execute('UPDATE chunks SET duration=? WHERE id=?', (END + 2., chunk['id']))
    chunk = store.chunk(chunk['id'])
    assert store.commit_window(chunk, 0., END, combined_result())
    audit = store.chunk(chunk['id'])['provenance']['event_candidate_projection_diagnostics']
    overlap = store.overlap_diagnostics(sid)
    metadata = {'event_candidates': []}
    if silence: metadata.update(silence_fast_path=True, models={})
    empty = own_core({'segments': [], 'events': [], 'metadata': metadata}, 0., .5)
    assert store.commit_window(store.chunk(chunk['id']), END, END + .5, empty)
    saved = store.chunk(chunk['id'])
    assert saved['next_offset'] == END + .5 and saved['status'] == 'running'
    assert saved['provenance']['event_candidate_projection_diagnostics'] == audit
    assert store.overlap_diagnostics(sid) == overlap
    assert_combined_counts(store)


def test_numeric_audit_overflow_does_not_consume_overlap_budget(combined_env):
    from audio_backend import event_candidate_store as ledger
    _, store, sid, chunk = combined_env
    with store.db() as db:
        db.execute('UPDATE chunks SET duration=? WHERE id=?', (2. * END, chunk['id']))
    chunk = store.chunk(chunk['id'])
    result = combined_result()
    original = result['metadata']['event_candidates'][1]
    # Stay within the separate per-window input cap; overflow the durable audit
    # across two cores while preserving the first core's overlap evidence.
    metadata = {'event_candidates': [copy.deepcopy(original)
                                    for _ in range(ledger.MAX_PROJECTION_RECORDS)]}
    result['event_candidates'] = ledger.own_candidates(metadata, 0., END)
    result['metadata'] = metadata
    assert store.commit_window(chunk, 0., END, result)
    overlap = store.overlap_diagnostics(sid)
    later = own_core({'segments': [], 'events': [],
                      'metadata': {'event_candidates': [copy.deepcopy(original) for _ in range(6)]}},
                     0., END)
    assert store.commit_window(store.chunk(chunk['id']), END, 2. * END, later)
    audit = store.chunk(chunk['id'])['provenance']['event_candidate_projection_diagnostics']
    assert audit['count'] == ledger.MAX_PROJECTION_RECORDS + 6
    assert len(audit['records']) == ledger.MAX_PROJECTION_RECORDS and audit['truncated']
    assert len(json.dumps(audit).encode()) <= ledger.MAX_PROJECTION_BYTES
    assert len(overlap['diagnostics']) == 1 and store.overlap_diagnostics(sid) == overlap
    assert len(store.results(sid)['segments']) == 2
    assert store.event_candidates(sid)['event_candidates'] == []


def test_overlap_budget_rejection_cannot_commit_numeric_evidence(combined_env):
    client, store, sid, chunk = combined_env
    result = combined_result()
    # This malformed oversized envelope must be rejected before either ledger commits.
    result['overlap_candidates']['diagnostics'][0]['synthetic_oversize'] = 'x' * (diagnostics.MAX_WINDOW_BYTES + 1)
    before = copy.deepcopy(result)
    baseline = snapshot(client, store, sid, chunk['id'])
    with pytest.raises(ValueError, match='Diagnostic window exceeds byte budget'):
        store.commit_window(chunk, 0., END, result)
    assert result == before
    assert snapshot(client, store, sid, chunk['id']) == baseline


@pytest.mark.parametrize('fault', ['call', 'setup', 'teardown', 'skip'])
def test_cpu_isolation_relays_original_outcomes(tmp_path, request, fault):
    import conftest as isolation
    env = dict(os.environ)
    report_path, log_path = tmp_path / 'reports.jsonl', tmp_path / 'child.log'
    env[isolation._CPU_REPORT_ENV] = str(report_path)
    (tmp_path / 'conftest.py').write_text(
        'import importlib.util\n'
        'spec = importlib.util.spec_from_file_location("isolated_hooks", ' + repr(str(ROOT / 'tests/conftest.py')) + ')\n'
        'hooks = importlib.util.module_from_spec(spec); spec.loader.exec_module(hooks)\n'
        'pytest_runtest_makereport = hooks.pytest_runtest_makereport\n')
    body = {
        'call': 'def test_case():\n    assert False, "synthetic call failure"\n',
        'setup': '@pytest.fixture(autouse=True)\ndef fixture():\n    raise RuntimeError("synthetic setup failure")\ndef test_case(): pass\n',
        'teardown': '@pytest.fixture(autouse=True)\ndef fixture():\n    yield\n    raise RuntimeError("synthetic teardown failure")\ndef test_case(): pass\n',
        'skip': 'def test_case():\n    pytest.skip("synthetic runtime skip")\n',
    }[fault]
    (tmp_path / 'test_fault.py').write_text('import pytest\n' + body)
    node = 'test_fault.py::test_case'
    exit_code, data = isolation._run_cpu_report_process(
        [__import__('sys').executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', node],
        tmp_path, env, report_path, log_path, timeout=30)
    rows = isolation._validated_cpu_reports(request.config, data, [node], exit_code)[node]
    assert [row.when for row in rows] == (['setup', 'teardown'] if fault == 'setup' else ['setup', 'call', 'teardown'])
    if fault == 'skip':
        assert exit_code == 0 and any(row.skipped for row in rows)
        assert all(isinstance(row.longrepr, tuple) for row in rows if row.skipped)
    else:
        assert exit_code == 1 and any(row.failed and row.when == fault for row in rows)


def test_cpu_isolation_parent_terminal_and_junit_keep_original_outcomes(tmp_path):
    """Exercise both module caches and parent consumers, including skip tuples."""
    import conftest as isolation
    from xml.etree import ElementTree
    tests = tmp_path / 'tests'
    tests.mkdir()
    report_path, log_path = tmp_path / 'parent.jsonl', tmp_path / 'parent.log'
    # Record the actual relayed parent events without activating the child marker.
    hook = (ROOT / 'tests/conftest.py').read_text()
    hook += ('\n\ndef pytest_runtest_logreport(report):\n'
             '    if not os.environ.get(_CPU_REPORT_ENV):\n'
             '        with open(' + repr(str(report_path)) + ', "a") as stream:\n'
             '            stream.write(json.dumps({"nodeid": report.nodeid, '
             '"when": report.when, "outcome": report.outcome}) + "\\n")\n')
    (tests / 'conftest.py').write_text(hook)
    (tests / 'test_fragment_cpu_provider.py').write_text('''import os
import pytest

def test_pass():
    assert os.environ.get('LONG_AUDIO_TEST_CPU_REPORT'), 'must execute in child'

def test_skip():
    pytest.skip('synthetic runtime skip')

def test_call_failure():
    assert False, 'synthetic call failure'

@pytest.fixture
def setup_failure():
    raise RuntimeError('synthetic setup failure')

def test_setup_failure(setup_failure):
    pass

@pytest.fixture
def teardown_failure():
    yield
    raise RuntimeError('synthetic teardown failure')

def test_teardown_failure(teardown_failure):
    pass
''')
    (tests / 'test_qwen_candidate_store.py').write_text(
        'import os\ndef test_second_module():\n'
        '    assert os.environ.get("LONG_AUDIO_TEST_CPU_REPORT"), "must execute in child"\n')
    env = dict(os.environ)
    env.pop(isolation._CPU_REPORT_ENV, None)
    exit_code, rows = isolation._run_cpu_report_process(
        [__import__('sys').executable, '-m', 'pytest', '-q', '-rs',
         '-p', 'no:cacheprovider', '--junitxml=parent.xml', 'tests'],
        tmp_path, env, report_path, log_path, timeout=60)
    assert exit_code == 1
    output = log_path.read_text()
    assert 'INTERNALERROR' not in output
    for expected in ('1 failed', '3 passed', '1 skipped', '2 errors', 'synthetic runtime skip'):
        assert expected in output
    names = ['test_pass', 'test_skip', 'test_call_failure', 'test_setup_failure', 'test_teardown_failure']
    assert list(dict.fromkeys(row['nodeid'] for row in rows)) == [
        isolation._CPU_MODULE + '::' + name for name in names] + [
            'tests/test_qwen_candidate_store.py::test_second_module']
    cases = ElementTree.parse(tmp_path / 'parent.xml').findall('.//testcase')
    assert {case.attrib['name'] for case in cases} == set(names) | {'test_second_module'}
    assert sum(case.find('failure') is not None for case in cases) == 1
    assert sum(case.find('error') is not None for case in cases) == 2
    assert sum(case.find('skipped') is not None for case in cases) == 1


@pytest.mark.parametrize('exit_code', [-9, 2, 3, 5])
def test_cpu_isolation_abnormal_exit_cannot_pass(request, exit_code):
    import conftest as isolation
    with pytest.raises(isolation.IsolationError, match='exited unexpectedly'):
        isolation._validated_cpu_reports(request.config, [], ['synthetic::test'], exit_code)


def test_cpu_isolation_missing_or_duplicate_reports_cannot_pass(request):
    import conftest as isolation
    with pytest.raises(isolation.IsolationError, match='Missing'):
        isolation._validated_cpu_reports(request.config, [], ['synthetic::test'], 0)
    report = isolation.TestReport(nodeid='synthetic::test', location=('synthetic', 0, 'test'),
                                  keywords={}, outcome='passed', longrepr=None,
                                  when='setup', sections=[], duration=0)
    row = request.config.hook.pytest_report_to_serializable(config=request.config, report=report)
    with pytest.raises(isolation.IsolationError, match='duplicate'):
        isolation._validated_cpu_reports(request.config, [row, row], ['synthetic::test'], 0)


def test_cpu_isolation_timeout_reaps_child(tmp_path):
    import conftest as isolation
    pid_path = tmp_path / 'pid'
    command = [__import__('sys').executable, '-c',
               'import os,time;open(' + repr(str(pid_path)) + ',"w").write(str(os.getpid()));time.sleep(10)']
    with pytest.raises(isolation.IsolationError, match='timed out'):
        isolation._run_cpu_report_process(command, tmp_path, dict(os.environ),
                                         tmp_path / 'reports', tmp_path / 'log', timeout=.3)
    assert pid_path.exists()
    assert not __import__('psutil').pid_exists(int(pid_path.read_text()))


def test_cpu_isolation_malformed_payload_cannot_pass(request):
    import conftest as isolation
    with pytest.raises(isolation.IsolationError):
        isolation._validated_cpu_reports(request.config, [{}], ['synthetic::test'], 0)


def test_cpu_isolation_reaps_descendant_after_immediate_leader_exit(tmp_path):
    import conftest as isolation
    import psutil
    pid_path = tmp_path / 'descendant'
    code = ('import subprocess,sys;child=subprocess.Popen([sys.executable,"-c",'
            '"import time;time.sleep(30)"]);open(' + repr(str(pid_path)) + ',"w").write(str(child.pid))')
    with pytest.raises(isolation.IsolationError, match='live descendants'):
        isolation._run_cpu_report_process([__import__('sys').executable, '-c', code],
                                         tmp_path, dict(os.environ), tmp_path / 'reports',
                                         tmp_path / 'log', timeout=5)
    assert pid_path.exists()
    try:
        child = psutil.Process(int(pid_path.read_text()))
        assert not isolation._live_cpu_child(child)
    except psutil.NoSuchProcess:
        pass
