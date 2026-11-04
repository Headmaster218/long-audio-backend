"""Real TLS loopback generation routing; synthetic media, no model/server deployment."""
from dataclasses import replace
import ftplib
import io
import shutil
import pytest
from test_recorder_ftps_receiver import configured, running, connect, upload
from test_recorder_receiver_completion_review import example, contents, adapter
from test_recorder_inbox import delivery
from audio_backend import recorder_ftps_receiver as receiver
from audio_backend import recorder_receiver_publication as publication
from audio_backend.recorder_receiver_routing import Routes, validate


def two(case,tmp_path):
    second=tmp_path/'second';second.mkdir(mode=0o700)
    raw,ready,state,_=example(second)
    generations=({'binding':case['config'].binding,'inbox_dir':str(case['ready'])},
                 {'binding':publication.bind(state),'inbox_dir':str(ready)})
    return replace(case['config'],generations=generations),raw,ready,state


def read(client,path):
    out=io.BytesIO();client.retrbinary('RETR '+path,out.write);return out.getvalue()


def test_real_ftps_restart_routes_old_readback_and_new_queued_attempt(configured,tmp_path):
    config,raw,ready,state=two(configured,tmp_path);source=configured['source']
    with running(config) as (server,owner):
        client=connect(server,config);client.mkd(source.name);upload(client,source);client.quit()
    before=contents(configured['ready'])
    config=replace(config,binding=publication.bind(state),inbox_dir=ready)
    new=delivery(tmp_path/'second/templates',1,close='ROTATION')
    with running(config) as (server,owner):
        client=connect(server,config)
        client.cwd(source.name)
        assert read(client,'audio.wav')==(source/'audio.wav').read_bytes()
        client.cwd('/')
        with pytest.raises(ftplib.error_perm):client.mkd(source.name)
        client.mkd(new.name);upload(client,new)
        client.cwd(new.name)
        assert read(client,'audio.wav')==(new/'audio.wav').read_bytes()
        with pytest.raises(ftplib.error_perm):read(client,'../../password')
        with pytest.raises(ftplib.error_perm):client.cwd('/not-a-recorder-attempt')
        client.quit()
    assert (ready/new.name).is_dir() and not (configured['ready']/new.name).exists()
    assert contents(configured['ready'])==before
    # Second restart preserves both virtual paths without adopting write rights.
    with running(config) as (server,owner):
        client=connect(server,config)
        for path in (source,new):
            client.cwd('/'+path.name);assert read(client,'audio.wav')==(path/'audio.wav').read_bytes()
            with pytest.raises(ftplib.error_perm):client.storbinary('STOR audio.wav',io.BytesIO(b'no'))
        client.quit()


def test_all_leases_and_duplicate_names_refused_before_socket(configured,tmp_path,monkeypatch):
    config,raw,ready,state=two(configured,tmp_path)
    with adapter(state,ready):
        with pytest.raises(publication.Refused,match='already_owned'):Routes(config)
    # The first lease was released after the second generation failed.
    with adapter(configured['state'],configured['ready']):pass
    for st,rd in ((configured['state'],configured['ready']),(state,ready)):
        with adapter(st,rd) as owner:owner.begin_attempt(configured['source'].name)
    with pytest.raises(publication.Refused,match='collision'):receiver.create_server(replace(config,port=0))
    for st,rd in ((configured['state'],configured['ready']),(state,ready)):
        with adapter(st,rd):pass


def test_unknown_route_and_replaced_attempt_refused(configured,tmp_path):
    config,*_=two(configured,tmp_path)
    with pytest.raises(publication.Refused):validate(config.generations*2,config.binding,config.inbox_dir)
    routes=Routes(config)
    try:
        with pytest.raises(publication.Refused):routes.resolve(configured['source'].name)
        owner,attempt=routes.begin(configured['source'].name)
        assert routes.resolve(attempt.path.name)==0
        attempt.path.rename(attempt.path.with_name(attempt.path.name+'-held'))
        attempt.path.mkdir(mode=0o700)
        with pytest.raises(publication.Refused):routes.resolve(attempt.path.name)
    finally:routes.close()


def test_real_old_queued_original_can_use_fresh_attempt_in_active_generation(configured,tmp_path):
    config,raw,ready,state=two(configured,tmp_path);source=configured['source']
    with running(config) as (server,owner):
        client=connect(server,config);client.mkd(source.name);upload(client,source);client.quit()
    original=contents(configured['ready'])
    fresh=source.with_name(source.name[:-32]+('f'*32 if not source.name.endswith('f'*32) else 'e'*32))
    shutil.copytree(source,fresh)
    marker=fresh/'delivery.v1.txt';marker.write_bytes(marker.read_bytes().replace(source.name.encode(),fresh.name.encode()))
    config=replace(config,binding=publication.bind(state),inbox_dir=ready)
    with running(config) as (server,owner):
        client=connect(server,config);client.mkd(fresh.name);upload(client,fresh)
        client.cwd(source.name);assert read(client,'audio.wav')==(source/'audio.wav').read_bytes()
        client.cwd('/'+fresh.name);assert read(client,'audio.wav')==(source/'audio.wav').read_bytes()
        client.quit()
    assert (ready/fresh.name/'audio.wav').read_bytes()==(source/'audio.wav').read_bytes()
    assert original==contents(configured['ready'])


@pytest.mark.parametrize('kind',['empty','too_many','missing_active','extra_key'])
def test_routing_config_refusals(configured,tmp_path,kind):
    config,raw,ready,state=two(configured,tmp_path)
    data=dict(configured['data']);items=list(config.generations)
    if kind=='empty':items=[]
    if kind=='too_many':items=items*5
    if kind=='missing_active':items=items[1:]
    if kind=='extra_key':items[0]=dict(items[0],unknown=True)
    data['generations']=items
    with pytest.raises((publication.Refused,ValueError)):receiver._validate_config(data)


def test_route_check_refuses_new_out_of_band_cross_generation_collision(configured,tmp_path):
    config,raw,ready,state=two(configured,tmp_path);routes=Routes(config)
    try:
        routes.begin(configured['source'].name)
        (raw/configured['source'].name).mkdir(mode=0o700)
        with pytest.raises(publication.Refused,match='members_changed'):routes.resolve(configured['source'].name)
    finally:routes.close()


def setup_files(case,tmp_path):
    import json
    config,raw,ready,state=two(case,tmp_path)
    second=dict(case['data'],binding=publication.bind(state),inbox_dir=str(ready),port=2233)
    path=tmp_path/'second-service.json';path.write_text(json.dumps(second));path.chmod(0o600)
    return path,second


def test_setup_only_retains_endpoint_settings_and_refuses_overwrite(configured,tmp_path,monkeypatch):
    from audio_backend import recorder_receiver_routing as routing
    second,data=setup_files(configured,tmp_path);output=tmp_path/'routed.json'
    monkeypatch.setattr(receiver,'create_server',lambda *a,**kw:pytest.fail('listener'))
    args=dict(endpoint_config=configured['path'],generation_configs=[configured['path'],second],active_generation=1,output=output)
    assert routing.configure(**args)['service_started'] is False
    config=receiver.load_config(output)
    assert config.port==configured['config'].port and config.binding==data['binding'] and len(config.generations)==2
    assert output.stat().st_mode&0o777==0o600
    before=output.read_bytes()
    with pytest.raises(publication.Refused,match='fresh_output'):routing.configure(**args)
    assert output.read_bytes()==before


@pytest.mark.parametrize('change',['username','password_file','cert_file','key_file','recursive'])
def test_setup_refuses_identity_or_recursive_generation(configured,tmp_path,change):
    import json
    from audio_backend import recorder_receiver_routing as routing
    second,data=setup_files(configured,tmp_path);output=tmp_path/'routed.json'
    if change=='recursive':data['generations']=[dict(binding=data['binding'],inbox_dir=data['inbox_dir'])]
    elif change=='username':data['username']='other-user'
    else:
        original=__import__('pathlib').Path(data[change]);copy=tmp_path/('other-'+original.name)
        copy.write_bytes(original.read_bytes());copy.chmod(0o600);data[change]=str(copy)
    second.write_text(json.dumps(data))
    with pytest.raises(publication.Refused):routing.configure(endpoint_config=configured['path'],
        generation_configs=[configured['path'],second],active_generation=1,output=output)
    assert not output.exists()


def test_setup_cli_is_secret_free_and_setup_only(configured,tmp_path,capsys):
    from audio_backend import recorder_receiver_routing as routing
    second,_=setup_files(configured,tmp_path);output=tmp_path/'cli-routed.json'
    args=['--endpoint-config',str(configured['path']),'--generation-config',str(configured['path']),
          '--generation-config',str(second),'--active-generation','1','--output',str(output)]
    assert routing.main(args)==0
    text=capsys.readouterr().out
    assert 'routing_configuration_created' in text
    assert configured['config'].password not in text and configured['config'].username not in text
    assert routing.main(args)==2
    error=capsys.readouterr().err
    assert configured['config'].password not in error and str(output) not in error


@pytest.mark.parametrize('destination',['outside','raw','ready','publisher'])
def test_setup_omitted_endpoint_refused_without_side_effect(configured,tmp_path,destination):
    from audio_backend import recorder_receiver_routing as routing
    second,_=setup_files(configured,tmp_path)
    parent=tmp_path if destination=='outside' else configured[{'raw':'raw','ready':'ready','publisher':'state'}[destination]]
    output=parent/'routing.json';before=contents(tmp_path)
    with pytest.raises(publication.Refused,match='endpoint_generation_omitted'):
        routing.configure(endpoint_config=configured['path'],generation_configs=[second],active_generation=0,output=output)
    assert not output.exists() and contents(tmp_path)==before


def test_setup_routed_endpoint_requires_all_retained_namespaces(configured,tmp_path):
    from audio_backend import recorder_receiver_routing as routing
    second,_=setup_files(configured,tmp_path);endpoint=tmp_path/'both.json'
    routing.configure(endpoint_config=configured['path'],generation_configs=[configured['path'],second],
                      active_generation=1,output=endpoint)
    output=configured['raw']/'bad-route.json';before=contents(tmp_path)
    with pytest.raises(publication.Refused,match='endpoint_generation_omitted'):
        routing.configure(endpoint_config=endpoint,generation_configs=[second],active_generation=0,output=output)
    assert not output.exists() and contents(tmp_path)==before
    # Replacing a routed endpoint while retaining all namespaces remains valid.
    routing.configure(endpoint_config=endpoint,generation_configs=[configured['path'],second],
                      active_generation=0,output=tmp_path/'retained.json')
