"""One model-free installed-package ASGI lifecycle smoke; no live listener.

The two sources are synthetic retained metadata, not acoustic results. Restart
means two independent app lifespans/Store instances, not an OS crash/reboot.
"""
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import audio_backend
from audio_backend.app import create_app
from audio_backend.worker import Worker
from reviewed_person_bridge_fixtures import GROUP, activation_request, env


def test_installed_two_recording_review_navigation_restart_and_source_deletion(env, monkeypatch):
    install_root = Path(os.environ['BRIDGE_INSTALL_ROOT']).resolve()
    assert Path(audio_backend.__file__).resolve().is_relative_to(install_root)
    assert audio_backend.__version__ == '0.2.0rc3.dev62'
    assert env.settings.start_worker is False
    first, second = env.observe(), env.observe()
    prefix = f'/identity-groups/{GROUP}'
    env.settings.api_key = 'synthetic-installed-smoke-key'
    headers = {'Authorization': 'Bearer synthetic-installed-smoke-key'}
    def forbidden(*args, **kwargs):
        raise AssertionError('Passive smoke attempted to start a model worker')
    monkeypatch.setattr(Worker, 'start', forbidden)

    def read(client, path):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.status_code, response.text)
        return response

    def claim(client, item, *, person=None):
        bank = read(client, prefix + '/reviewed-person-bank').json()
        observation = read(client, f'/sessions/{item.sid}/recordings/{item.rid}/primary-observations/{item.observation_id}').json()
        assert observation['review_eligible'] is True
        body = dict(expected_generation=bank['generation'], expected_revision=bank['profile_revision'],
            idempotency_key='installed-claim-' + item.observation_id,
            source_session_id=item.sid, recording_id=item.rid, observation_id=item.observation_id,
            binding_sha256=observation['binding_sha256'],
            expected_source_authority_revision=observation['source_authority_revision'],
            expected_source_revision=observation['source_revision'],
            attestation={'version':'human_exact_observation_person_claim_v1',
                         'extent':'observed_crop_only','confirmed':True})
        body['person_id' if person else 'new_person_name'] = person or 'Synthetic person'
        response = client.post(prefix + '/primary-associations', json=body, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    with TestClient(create_app(env.settings)) as client:
        before = {item.sid: read(client, f'/sessions/{item.sid}/transcript').content
                  for item in (first, second)}
        initialized = client.put(prefix + '/reviewed-person-bank', json=activation_request(), headers=headers)
        assert initialized.status_code == 200, initialized.text
        generation = initialized.json()['generation']
        one = claim(client, first)
        two = claim(client, second, person=one['person_id'])
        person = one['person_id']
        assert two['person_id'] == person
        page = read(client, prefix + f'/speakers/{person}/occurrences').json()
        assert len(page['items']) == 2
        assert {row['observation_id'] for row in page['items']} == {first.observation_id, second.observation_id}
        for row in page['items']:
            assert row['extent'] == 'observed_crop_only'
            evidence = read(client, row['parent_evidence_url'])
            assert 'no-store' in evidence.headers['cache-control']
        for item in (first, second):
            assert read(client, f'/sessions/{item.sid}/transcript').content == before[item.sid]

    # Reopen without the startup policy; no processing worker is started and no model is run.
    env.settings.verified_identity_policy = None
    env.settings.verified_identity_policy_file = None
    with TestClient(create_app(env.settings)) as restarted:
        bank = read(restarted, prefix + '/reviewed-person-bank').json()
        assert bank['generation'] == generation
        page = read(restarted, prefix + f'/speakers/{person}/occurrences').json()
        assert len(page['items']) == 2
        assert all(row['person_id'] == person for row in page['items'])
        deleted = restarted.delete(f'/sessions/{first.sid}', headers=headers)
        assert deleted.status_code == 200, deleted.text
        remaining = read(restarted, prefix + f'/speakers/{person}/occurrences').json()
        assert [row['observation_id'] for row in remaining['items']] == [second.observation_id]
        assert first.sid not in read(restarted, prefix + '/primary-associations').text
        assert restarted.get(prefix + '/primary-associations/' + one['event_id'], headers=headers).status_code == 404
        assert read(restarted, prefix + '/reviewed-person-bank').json()['generation'] == generation
        assert read(restarted, f'/sessions/{second.sid}/transcript').content == before[second.sid]
        read(restarted, remaining['items'][0]['parent_evidence_url'])

    origins = {name: Path(module.__file__).resolve() for name, module in sys.modules.items()
               if name == 'audio_backend' or name.startswith('audio_backend.')
               if getattr(module, '__file__', None)}
    assert origins and all(path.is_relative_to(install_root) for path in origins.values()), origins
