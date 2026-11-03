"""Loopback-only TLS STOR fixture. The only fault holds an ordinary marker read."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import time

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import TLS_DTPHandler, TLS_FTPHandler
from pyftpdlib.servers import FTPServer
from OpenSSL import SSL


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    for name in ('work', 'cert', 'key'):
        parser.add_argument(name)
    args = parser.parse_args()
    os.umask(0o077)
    work = Path(args.work).resolve()
    remote = work / 'remote'
    (remote / 'fixture').mkdir(parents=True, mode=0o700)
    metrics = dict(commands=[], sessions=0, control_tls=0, data_tls=0,
        data_bytes=0, errors=[], listeners=[], held_marker=False, received=[])
    handlers, channels = [], []
    logging.basicConfig(level=logging.WARNING)

    class Data(TLS_DTPHandler):
        ac_in_buffer_size = 32

        def __init__(self, sock, cmd_channel):
            channels.append(self)
            super().__init__(sock, cmd_channel)
            self._counted = False
            self._held = False

        def handle_ssl_established(self):
            metrics['data_tls'] += 1
            return super().handle_ssl_established()

        def handle_read_event(self):
            if self._held and not (work / 'release-marker').exists():
                return
            super().handle_read_event()
            stream = getattr(self, 'file_obj', None)
            if (not metrics['held_marker'] and self.receive and self.tot_bytes_received > 0
                    and stream is not None and not stream.closed
                    and Path(stream.name).name == 'delivery.v1.txt'):
                stream.flush()
                os.fsync(stream.fileno())
                metrics['held_marker'] = self._held = True
                item = Path(stream.name)
                save(work / 'marker-held.json', dict(path=str(item), size=item.stat().st_size,
                    transmitted=self.tot_bytes_received, handle_closed=stream.closed))

        def close(self):
            if not getattr(self, '_counted', True):
                self._counted = True
                metrics['data_bytes'] += self.get_transmitted_bytes()
            return super().close()

    class Handler(TLS_FTPHandler):
        certfile = args.cert
        keyfile = args.key
        dtp_handler = Data
        tls_control_required = tls_data_required = True
        timeout = 10
        max_login_attempts = 1
        permit_foreign_addresses = permit_privileged_ports = False
        passive_ports = masquerade_address = None
        use_sendfile = False

        def on_connect(self):
            assert self.remote_ip == self.socket.getsockname()[0] == '127.0.0.1'
            metrics['sessions'] += 1
            assert metrics['sessions'] <= 5
            self.fixture_session = metrics['sessions']
            handlers.append(self)

        def handle_ssl_established(self):
            metrics['control_tls'] += 1
            return super().handle_ssl_established()

        def process_command(self, cmd, *args, **kwargs):
            arg = str(args[0]) if args else ''
            if cmd == 'PASS':
                arg = '[redacted]'
            protected = bool(self._ssl_established)
            assert cmd not in ('USER', 'PASS') or protected
            metrics['commands'].append(dict(session=self.fixture_session,
                cmd=cmd, arg=arg, tls=protected))
            assert len(metrics['commands']) <= 256
            return super().process_command(cmd, *args, **kwargs)

        def on_file_received(self, path):
            item = Path(path)
            # pyftpdlib invokes this callback only after closing the file object.
            matches = [channel for channel in channels if channel.file_obj is not None
                and channel.file_obj.name == path]
            assert matches and all(channel.file_obj.closed for channel in matches)
            row = dict(path=str(item), bytes=item.stat().st_size,
                sha256=hashlib.sha256(item.read_bytes()).hexdigest(), handles_closed=True)
            metrics['received'].append(row)
            if item.name == 'delivery.v1.txt':
                save(work / 'completed-delivery.json', row)

        def handle_error(self):
            metrics['errors'].append('server handler exception')
            return super().handle_error()

    authorizer = DummyAuthorizer()
    authorizer.add_user('synthetic', 'synthetic', str(remote), perm='elrwm')
    Handler.authorizer = authorizer
    server = FTPServer(('127.0.0.1', 0), Handler)
    server.max_cons = server.max_cons_per_ip = 4
    server.handler.ssl_context.set_min_proto_version(SSL.TLS1_2_VERSION)
    port = server.socket.getsockname()[1]
    metrics['listeners'].append(port)
    save(work / 'ready.json', dict(host='127.0.0.1', port=port))
    started = time.monotonic()
    try:
        while not (work / 'stop').exists():
            assert time.monotonic() - started <= 60, 'server deadline'
            assert metrics['data_bytes'] <= 2 * 1024**2, 'wire bytes'
            live = sum(bool(getattr(obj, 'accepting', False))
                for obj in server.ioloop.socket_map.values())
            assert live <= 2, 'listener bound'
            for obj in list(server.ioloop.socket_map.values()):
                sock = getattr(obj, 'socket', None)
                if sock is None:
                    continue
                try:
                    ip, local_port = sock.getsockname()[:2]
                    assert ip == '127.0.0.1'
                    if getattr(obj, 'accepting', False) and local_port not in metrics['listeners']:
                        metrics['listeners'].append(local_port)
                except OSError:
                    pass
            server.serve_forever(timeout=.02, blocking=False, handle_exit=False)
    finally:
        for handler in handlers:
            handler.close()
        for channel in channels:
            channel.close()
        server.close_all()
        metrics['all_closed'] = not server.ioloop.socket_map
        metrics['remote_files'] = {str(f.relative_to(remote)): dict(size=f.stat().st_size,
            sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in remote.rglob('*') if f.is_file()}
        save(work / 'server-result.json', metrics)


if __name__ == '__main__':
    main()
