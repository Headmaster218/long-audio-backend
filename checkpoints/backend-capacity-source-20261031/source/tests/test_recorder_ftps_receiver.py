"""Synthetic loopback FTPS tests; no devices, external listeners or inference."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import ftplib
import io
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import threading
import time

import pytest

from audio_backend import recorder_ftps_receiver as receiver
from audio_backend import recorder_receiver_publication as publication
from test_recorder_receiver_completion_review import example, contents

pytest.importorskip('pyftpdlib', reason='install requirements-receiver.txt for receiver wire tests')
pytest.importorskip('OpenSSL', reason='install requirements-receiver.txt for receiver wire tests')


@pytest.fixture
def configured(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    raw, ready, state, source = example(tmp_path)
    # Ephemeral loopback fixture identity only, never an installed account/key.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'receiver-loopback-test')])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1))
            .not_valid_after(datetime.now(timezone.utc)+timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(key, hashes.SHA256()))
    certfile, keyfile, password = tmp_path/'test.crt', tmp_path/'test.key', tmp_path/'password'
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption())); keyfile.chmod(0o600)
    password.write_text('synthetic-only-password\n'); password.chmod(0o600)
    data = dict(schema=receiver.SCHEMA, binding=publication.bind(state), inbox_dir=str(ready),
                cert_file=str(certfile), key_file=str(keyfile), username='synthetic-user',
                password_file=str(password))
    path = tmp_path/'receiver.json'; path.write_text(json.dumps(data)); path.chmod(0o600)
    return dict(raw=raw, ready=ready, state=state, source=source, path=path, data=data,
                config=receiver.load_config(path))


def rewrite(case, **changes):
    data = dict(case['data'], **changes)
    case['path'].write_text(json.dumps(data))
    return receiver.load_config(case['path'])


@contextmanager
def running(config):
    # port=0 is used only by this in-process test helper, not accepted in config.
    server, owner = receiver.create_server(replace(config, port=0))
    stop = threading.Event(); errors = []
    def serve():
        try:
            while not stop.is_set():
                server.serve_forever(timeout=.01, blocking=False, handle_exit=False)
        except BaseException as error:
            errors.append(error)
        finally:
            server.close_all()
    thread = threading.Thread(target=serve); thread.start()
    try:
        yield server, owner
    finally:
        stop.set(); thread.join(5)
        assert not thread.is_alive()
        owner.close()
        assert not server.ioloop.socket_map
        assert not errors


def connect(server, config):
    client = ftplib.FTP_TLS(context=ssl.create_default_context(cafile=str(config.cert_file)), timeout=3)
    client.connect(*server.socket.getsockname()[:2])
    client.login(config.username, config.password)
    client.prot_p()
    return client


def wait_for(predicate):
    end = time.monotonic()+3
    while time.monotonic() < end:
        if predicate(): return
        time.sleep(.01)
    assert predicate()


def upload(client, source, names=('audio.wav', 'segment.v1.bin', 'delivery.v1.txt')):
    for name in names:
        assert client.storbinary('STOR '+source.name+'/'+name, io.BytesIO((source/name).read_bytes())).startswith('226')


def test_config_has_no_default_account_or_external_listener(configured, capsys):
    config = configured['config']
    assert config.bind_host == '127.0.0.1' and config.port == 2121
    assert config.password not in repr(config)
    assert receiver.main(['check', '--config', str(configured['path'])]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['network_requests'] == 0 and not result['automatic_intake_enabled']
    assert not contents(configured['raw']) and not contents(configured['ready'])


@pytest.mark.parametrize('change', [dict(username='anonymous'), dict(username=''), dict(password_file='/not-real'),
    dict(bind_host='0.0.0.0'), dict(bind_host='0.0.0.0', allow_non_loopback=True), dict(bind_host='localhost'), dict(port=21), dict(port=True),
    dict(max_attempts=257), dict(max_raw_bytes=0), dict(passive_ports=[2000, 2200]),
    dict(passive_ports=[2121, 2122]), dict(max_connections_per_ip=32), dict(unknown='ignored')])
def test_config_refuses_unsafe_or_ambiguous_options(configured, change):
    with pytest.raises((ValueError, OSError)):
        rewrite(configured, **change)


def test_non_loopback_requires_explicit_config_without_binding(configured):
    assert rewrite(configured, bind_host='0.0.0.0', allow_non_loopback=True, acknowledge_data_peer_ip_only=True).bind_host == '0.0.0.0'


@pytest.mark.parametrize('target', ['password_file', 'key_file'])
def test_private_secret_material_required(configured, target):
    Path(configured['data'][target]).chmod(0o644)
    with pytest.raises(receiver.ConfigurationError): receiver.load_config(configured['path'])


def test_duplicate_keys_and_missing_password_rejected(configured):
    configured['path'].write_text('{"schema":"a","schema":"b"}')
    with pytest.raises(receiver.ConfigurationError, match='duplicate'): receiver.load_config(configured['path'])
    configured['path'].write_text(json.dumps(configured['data']))
    Path(configured['data']['password_file']).write_text('\n')
    with pytest.raises(receiver.ConfigurationError): receiver.load_config(configured['path'])


def test_tls_account_binary_and_command_allowlist(configured):
    config = configured['config']
    with running(config) as (server, owner):
        plain = ftplib.FTP(timeout=3); plain.connect(*server.socket.getsockname()[:2])
        with pytest.raises(ftplib.error_perm): plain.sendcmd('USER '+config.username)
        plain.close()
        wrong = ftplib.FTP_TLS(context=ssl.create_default_context(cafile=str(config.cert_file)), timeout=3)
        wrong.connect(*server.socket.getsockname()[:2])
        with pytest.raises(ftplib.error_perm): wrong.login(config.username, 'wrong')
        wrong.close()
        client = connect(server, config)
        for command in ('PROT C', 'TYPE A', 'PORT 127,0,0,1,1,1', 'EPRT |1|127.0.0.1|1234|',
                        'APPE x', 'REST 0', 'STOU x', 'DELE x', 'RNFR x',
                        'RMD x', 'SITE CHMOD 777 x', 'LIST', 'USER other'):
            with pytest.raises(ftplib.error_perm): client.sendcmd(command)
        client.quit()
        assert not contents(configured['raw']) and not contents(configured['ready'])


def test_three_closed_streams_publish_before_final_226(configured, monkeypatch):
    source, ready = configured['source'], configured['ready']
    published = []
    original = publication.publish_completed
    def publish(**kwargs):
        result = original(**kwargs); published.append(result); return result
    monkeypatch.setattr(publication, 'publish_completed', publish)
    with running(configured['config']) as (server, owner):
        client = connect(server, configured['config']); client.mkd(source.name)
        upload(client, source, ('audio.wav', 'segment.v1.bin'))
        assert not contents(ready) and not published
        upload(client, source, ('delivery.v1.txt',))
        assert len(published) == 1 and published[0]['status'] == 'published'
        assert contents(ready/source.name) == contents(source) == contents(configured['raw']/source.name)
        before = contents(configured['raw'])
        with pytest.raises(ftplib.error_perm): client.storbinary('STOR '+source.name+'/audio.wav', io.BytesIO(b'bad'))
        assert contents(configured['raw']) == before
        client.quit()


def test_publication_exception_never_sends_success(configured, monkeypatch):
    source = configured['source']
    monkeypatch.setattr(publication, 'publish_completed', lambda **kw: (_ for _ in ()).throw(OSError('secret-path')))
    with running(configured['config']) as (server, owner):
        client = connect(server, configured['config']); client.mkd(source.name)
        upload(client, source, ('audio.wav', 'segment.v1.bin'))
        with pytest.raises(ftplib.error_temp, match='451'):
            upload(client, source, ('delivery.v1.txt',))
        assert server.receiver_fault and not contents(configured['ready'])
        client.close()


def test_invalid_full_delivery_refused_without_226(configured):
    source = configured['source']
    with running(configured['config']) as (server, owner):
        client = connect(server, configured['config']); client.mkd(source.name)
        upload(client, source, ('audio.wav', 'segment.v1.bin'))
        with pytest.raises(ftplib.error_temp, match='451'):
            client.storbinary('STOR '+source.name+'/delivery.v1.txt', io.BytesIO(b'invalid'))
        assert not contents(configured['ready']) and server.receiver_fault
        client.close()


@pytest.mark.parametrize('end', ['unclean_data_eof', 'control_disconnect', 'abort', 'quit'])
def test_full_marker_bytes_do_not_publish_without_orderly_success(configured, end):
    source = configured['source']
    with running(configured['config']) as (server, owner):
        client = connect(server, configured['config']); client.mkd(source.name)
        upload(client, source, ('audio.wav', 'segment.v1.bin'))
        data = client.transfercmd('STOR '+source.name+'/delivery.v1.txt')
        data.sendall((source/'delivery.v1.txt').read_bytes())
        wait_for(lambda: (configured['raw']/source.name/'delivery.v1.txt').stat().st_size > 0)
        if end == 'unclean_data_eof':
            os.close(data.detach())
            with pytest.raises(ftplib.error_temp, match='451'): client.voidresp()
        elif end == 'control_disconnect': client.close()
        elif end == 'abort':
            with pytest.raises(ftplib.error_temp, match='426'): client.sendcmd('ABOR')
        else: client.putcmd('QUIT')
        if end != 'unclean_data_eof': data.close()
        wait_for(lambda: all(w.stream.closed for a in owner._attempts.values() for w in a.writers.values()))
        assert not contents(configured['ready'])
        assert owner.status(next(iter(owner._attempts.values())))['status'] == 'failed'
        client.close()


@pytest.mark.parametrize('end', ['disconnect', 'abort', 'shutdown'])
def test_pending_stor_without_data_connection_is_closed_failed(configured, end):
    source = configured['source']
    with running(configured['config']) as (server, owner):
        client = connect(server, configured['config']); client.mkd(source.name); client.voidcmd('TYPE I')
        assert client.sendcmd('STOR '+source.name+'/audio.wav').startswith('150')
        writer = next(iter(owner._attempts.values())).writers['audio.wav']
        with pytest.raises(ftplib.error_perm, match='503'):
            client.sendcmd('STOR '+source.name+'/segment.v1.bin')
        if end == 'abort':
            with pytest.raises(ftplib.error_temp, match='426'): client.sendcmd('ABOR')
        elif end == 'disconnect': client.close()
        # shutdown case is settled by the running context before owner.close.
        if end != 'shutdown': wait_for(lambda: writer.stream.closed)
        assert not contents(configured['ready'])
    assert writer.stream.closed and writer.successful is False
    client.close()


def test_attempts_belong_to_creating_session_and_retained_names_cannot_resume(configured):
    source, config = configured['source'], configured['config']
    with running(config) as (server, owner):
        first = connect(server, config); first.mkd(source.name)
        second = connect(server, config)
        with pytest.raises(ftplib.error_perm): second.storbinary('STOR '+source.name+'/audio.wav', io.BytesIO(b'x'))
        with pytest.raises(ftplib.error_perm): second.mkd(source.name)
        first.close(); second.close()
    with running(config) as (server, owner):
        client = connect(server, config)
        with pytest.raises(ftplib.error_perm): client.mkd(source.name)
        with pytest.raises(ftplib.error_perm): client.storbinary('STOR '+source.name+'/audio.wav', io.BytesIO(b'x'))
        client.quit()
    assert not contents(configured['ready'])


def test_attempt_quota_refuses_without_deleting_retained_bytes(configured):
    source, config = configured['source'], replace(configured['config'], max_attempts=1)
    with running(config) as (server, owner):
        client = connect(server, config); client.mkd(source.name)
        upload(client, source, ('audio.wav',))
        before = contents(configured['raw'])
        with pytest.raises(ftplib.error_perm): client.mkd(source.name[:-32]+'1'*32)
        assert contents(configured['raw']) == before
        client.quit()


def test_total_session_deadline_bounds_a_live_control_connection(configured):
    with running(replace(configured['config'], session_seconds=1)) as (server, owner):
        client = connect(server, configured['config'])
        time.sleep(1.2)
        with pytest.raises((EOFError, OSError)): client.sendcmd('NOOP')
        client.close()


def retrieve(client, path):
    result = io.BytesIO()
    assert client.retrbinary('RETR '+path, result.write).startswith('226')
    return result.getvalue()


def test_readback_matches_owned_files_and_exact_published_attempt_after_reconnect(configured):
    source, config = configured['source'], configured['config']
    with running(config) as (server, owner):
        client = connect(server, config); client.mkd(source.name)
        for name in ('audio.wav', 'segment.v1.bin', 'delivery.v1.txt'):
            upload(client, source, (name,))
            assert retrieve(client, source.name+'/'+name) == (source/name).read_bytes()
        client.quit()
    # Mutating raw after publication must not change reconnect readback.
    (configured['raw']/source.name/'audio.wav').write_bytes(b'changed')
    with running(config) as (server, owner):
        client = connect(server, config)
        for name in publication.FILES:
            assert retrieve(client, source.name+'/'+name) == (source/name).read_bytes()
        with pytest.raises(ftplib.error_perm): retrieve(client, source.name[:-32]+'9'*32+'/audio.wav')
        client.quit()


def test_reconnect_cannot_read_unpublished_raw_or_traverse(configured):
    source, config = configured['source'], configured['config']
    with running(config) as (server, owner):
        first = connect(server, config); first.mkd(source.name); upload(first, source, ('audio.wav',))
        second = connect(server, config)
        for path in (source.name+'/audio.wav', '../password', source.name+'/../../password', '/password'):
            with pytest.raises(ftplib.error_perm): retrieve(second, path)
        first.quit(); second.quit()


def test_readback_session_byte_reservation_refuses_before_second_read(configured):
    source = configured['source']; size = (source/'audio.wav').stat().st_size
    config = replace(configured['config'], max_readback_bytes=size)
    with running(config) as (server, owner):
        client = connect(server, config); client.mkd(source.name); upload(client, source, ('audio.wav',))
        assert retrieve(client, source.name+'/audio.wav') == (source/'audio.wav').read_bytes()
        with pytest.raises(ftplib.error_perm): retrieve(client, source.name+'/audio.wav')
        client.quit()


def test_published_symlink_readback_refused(configured):
    source, config = configured['source'], configured['config']
    with running(config) as (server, owner):
        client = connect(server, config); client.mkd(source.name); upload(client, source); client.quit()
        audio = configured['ready']/source.name/'audio.wav'
        audio.unlink(); audio.symlink_to(source/'audio.wav')
        other = connect(server, config)
        with pytest.raises(ftplib.error_perm): retrieve(other, source.name+'/audio.wav')
        other.quit()


def test_sigterm_cli_closes_pending_stor_and_preserves_retained_attempt(configured):
    import select
    import subprocess
    import sys
    # Select a loopback ephemeral port, then write an explicit legal CLI port.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    config = rewrite(configured, port=port)
    process = subprocess.Popen([sys.executable, '-m', 'audio_backend.recorder_ftps_receiver',
                               'serve', '--config', str(configured['path'])],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    client = None
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert json.loads(process.stdout.readline())['status'] == 'listening'
        client = ftplib.FTP_TLS(context=ssl.create_default_context(cafile=str(config.cert_file)), timeout=3)
        client.connect('127.0.0.1', port); client.login(config.username, config.password); client.prot_p()
        client.mkd(configured['source'].name); client.voidcmd('TYPE I')
        assert client.sendcmd('STOR '+configured['source'].name+'/audio.wav').startswith('150')
        process.terminate(); out, err = process.communicate(timeout=5)
        assert process.returncode == 0 and not err
        assert not contents(configured['ready'])
        assert (configured['raw']/configured['source'].name/'audio.wav').stat().st_size == 0
        with completion_owner(config) as owner:
            with pytest.raises(publication.Refused): owner.begin_attempt(configured['source'].name)
    finally:
        if client: client.close()
        if process.poll() is None: process.kill(); process.communicate(timeout=3)


def completion_owner(config):
    return receiver.completion.ReceiverCompletion(binding=config.binding, inbox_dir=config.inbox_dir,
                                                  exclusive_receiver=True)


def test_exhausted_configured_passive_ports_never_fall_back(configured):
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0)); occupied.listen(1)
        config = replace(configured['config'], passive_ports=(occupied.getsockname()[1],))
        with running(config) as (server, owner):
            client = connect(server, config)
            for command in ('EPSV', 'PASV'):
                with pytest.raises(ftplib.error_temp, match='425'): client.sendcmd(command)
                listeners = [c for c in server.ioloop.socket_map.values() if getattr(c, 'accepting', False)]
                assert listeners == [server]
            assert not server.receiver_fault
            client.quit()


@pytest.mark.parametrize('target', ['authorizer', 'loop', 'listener'])
def test_setup_failure_releases_any_receiver_lease(configured, monkeypatch, target):
    from pyftpdlib import authorizers, ioloop, servers
    def fail(*args, **kwargs):
        raise OSError('injected setup failure')
    with monkeypatch.context() as patch:
        if target == 'authorizer': patch.setattr(authorizers.DummyAuthorizer, 'add_user', fail)
        elif target == 'loop': patch.setattr(ioloop, 'IOLoop', fail)
        else: patch.setattr(servers, 'FTPServer', fail)
        with pytest.raises(OSError): receiver.create_server(configured['config'])
    with completion_owner(configured['config']): pass


def test_raw_byte_quota_refuses_stor_before_creating_file(configured):
    source = configured['source']
    with running(replace(configured['config'], max_raw_bytes=1)) as (server, owner):
        client = connect(server, configured['config']); client.mkd(source.name)
        with pytest.raises(ftplib.error_perm): upload(client, source, ('audio.wav',))
        assert not list((configured['raw']/source.name).iterdir())
        assert not contents(configured['ready'])
        client.quit()


def test_tls_below_12_is_rejected_before_account_authentication(configured):
    with running(configured['config']) as (server, owner):
        plain = ftplib.FTP(timeout=3); plain.connect(*server.socket.getsockname()[:2])
        assert plain.sendcmd('AUTH TLS').startswith('234')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=str(configured['config'].cert_file))
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_1
        context.set_ciphers('ALL:@SECLEVEL=0')
        with pytest.raises(ssl.SSLError):
            context.wrap_socket(plain.sock, server_hostname='127.0.0.1')
        plain.close()
        assert not contents(configured['raw'])


def test_error_after_publication_retains_copy_without_retry_and_reads_exact_copy(configured, monkeypatch):
    source, config = configured['source'], configured['config']
    original = publication.publish_completed
    calls = []
    def uncertain(**kwargs):
        calls.append(1); original(**kwargs); raise OSError('synthetic lost publication outcome')
    with monkeypatch.context() as patch:
        patch.setattr(publication, 'publish_completed', uncertain)
        with running(config) as (server, owner):
            client = connect(server, config); client.mkd(source.name)
            upload(client, source, ('audio.wav', 'segment.v1.bin'))
            with pytest.raises(ftplib.error_temp, match='451'): upload(client, source, ('delivery.v1.txt',))
            assert server.receiver_fault and calls == [1]
            assert owner.status(next(iter(owner._attempts.values())))['status'] == 'uncertain'
            assert contents(configured['ready']/source.name) == contents(source)
            client.close()
    with running(config) as (server, owner):
        client = connect(server, config)
        for name in publication.FILES: assert retrieve(client, source.name+'/'+name) == (source/name).read_bytes()
        with pytest.raises(ftplib.error_perm): client.mkd(source.name)
        assert calls == [1]
        client.quit()
