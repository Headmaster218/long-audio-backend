"""Fresh configuration only; no listeners, HTTP, sessions, or existing-data adoption."""
import json
from pathlib import Path
import pytest
from test_recorder_ftps_receiver import configured
from audio_backend import recorder_deployment_bundle as bundle
from audio_backend import recorder_ftps_receiver as receiver


def args(configured,tmp_path):
    return dict(bundle_dir=tmp_path/'fresh',endpoint_config=configured['path'],
        api='http://127.0.0.1:8000',acknowledge_fresh_only=True)


def test_fresh_bundle_routes_share_full_roster(configured,tmp_path,monkeypatch):
    from audio_backend import recording_uploader as u
    monkeypatch.setattr(u.HTTPTransport,'request',lambda *a,**k: pytest.fail('HTTP forbidden'))
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result=bundle.prepare(**args(configured,tmp_path))
    assert result['status']=='prepared_not_started' and result['network_requests']==0
    root=tmp_path/'fresh';routes=[json.loads((root/('route-%02d.json'%i)).read_text()) for i in range(3)]
    assert all(r['generations']==routes[0]['generations'] for r in routes)
    assert all(r['max_attempts']==144 and r['max_raw_bytes']==8*1024**3 for r in routes)
    assert [r['inbox_dir'] for r in routes]==[g['inbox_dir'] for g in routes[0]['generations']]
    assert all(Path(p).read_bytes()==raw for p,raw in before.items())
    assert all(not (root/('generation-%02d'%i)/name).exists() for i in range(3) for name in ('runner','job','export'))
    assert (root/'COMPLETE.json').exists()
    with pytest.raises(Exception,match='fresh_destination'):bundle.prepare(**args(configured,tmp_path))


@pytest.mark.parametrize('change',[{'segment_seconds':1},{'max_attempts':192},{'generations':9}, {'phase_max_polls':64}])
def test_invalid_profile_leaves_no_output(configured,tmp_path,change):
    with pytest.raises(Exception):bundle.prepare(**dict(args(configured,tmp_path),**change))
    assert not (tmp_path/'fresh').exists()


def test_changed_template_before_creation_refused(configured,tmp_path,monkeypatch):
    original=bundle._snapshot;calls=0
    def changed(path):
        nonlocal calls
        calls+=1
        if calls==2:path.write_bytes(path.read_bytes()+b'\n')
        return original(path)
    monkeypatch.setattr(bundle,'_snapshot',changed)
    with pytest.raises(Exception,match='template_or_credentials_changed'):bundle.prepare(**args(configured,tmp_path))
    assert not (tmp_path/'fresh').exists()


def test_partial_failure_preserved_never_adopted(configured,tmp_path,monkeypatch):
    def fail(**kwargs):raise RuntimeError('injected configuration failure')
    monkeypatch.setattr(receiver,'configure',fail)
    with pytest.raises(RuntimeError):bundle.prepare(**args(configured,tmp_path))
    assert (tmp_path/'fresh/generation-00/publication/config.json').exists()
    assert not (tmp_path/'fresh/COMPLETE.json').exists()
    with pytest.raises(Exception,match='fresh_destination'):bundle.prepare(**args(configured,tmp_path))


def test_invalid_api_and_custom_policy_leave_no_output(configured,tmp_path):
    with pytest.raises(Exception):bundle.prepare(**dict(args(configured,tmp_path),api='not-an-api'))
    assert not (tmp_path/'fresh').exists()
    data=json.loads(configured['path'].read_text());data['idle_seconds']=75
    configured['path'].write_text(json.dumps(data))
    with pytest.raises(Exception,match='custom_endpoint_policy'):bundle.prepare(**args(configured,tmp_path))
    assert not (tmp_path/'fresh').exists()


def test_generic_cli_error_redacted(monkeypatch,capsys):
    from audio_backend import recorder_deployment_bundle as bundle
    def fail(**kw):raise ValueError('private input must not appear')
    monkeypatch.setattr(bundle,'prepare',fail)
    assert bundle.main(['--bundle-dir','/private/new','--endpoint-config','/private/template',
        '--api','http://127.0.0.1:8000','--acknowledge-fresh-only'])==2
    assert 'private input' not in capsys.readouterr().out
