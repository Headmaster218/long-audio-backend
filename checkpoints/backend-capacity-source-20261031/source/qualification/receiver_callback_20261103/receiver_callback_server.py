"""Loopback TLS fixture: server-owned handles call the product completion adapter."""
import argparse
import errno
import hashlib
import json
import logging
import os
from pathlib import Path
import time

from OpenSSL import SSL
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.filesystems import AbstractedFS
from pyftpdlib.handlers import TLS_DTPHandler, TLS_FTPHandler
from pyftpdlib.servers import FTPServer

from audio_backend import recorder_receiver_publication as receiver
from audio_backend.recorder_receiver_completion import ReceiverCompletion


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
    remote, inbox, state = work/'remote', work/'inbox', work/'receiver'
    staging = remote/'fixture'
    staging.mkdir(parents=True, mode=0o700)
    inbox.mkdir(mode=0o700)
    receiver.configure(state_dir=state, staging_dir=staging, inbox_dir=inbox, exclusive_receiver=True)
    adapter = ReceiverCompletion(binding=receiver.bind(state), inbox_dir=inbox,
        exclusive_receiver=True, deadline_seconds=30)
    metrics = dict(commands=[], sessions=0, control_tls=0, data_tls=0,
        data_bytes=0, errors=[], listeners=[], held_marker=False, fault=False,
        callbacks=[], opens=[], refusals=[], attempts=[])
    handlers, channels, writers, attempts = [], [], {}, {}
    settled_writers = set()
    logging.basicConfig(level=logging.WARNING)

    def live_writers():
        return sum(not writer.stream.closed for writer in writers.values())

    class OwnedFS(AbstractedFS):
        def mkdir(self, path):
            item = Path(path)
            if item.parent != staging:
                raise OSError(errno.EPERM, 'only receiver attempt creation allowed')
            try:
                attempts[str(item)] = adapter.begin_attempt(item.name)
                metrics['attempts'].append(str(item))
            except receiver.Refused as error:
                metrics['refusals'].append(dict(operation='mkdir', path=str(item), reason=str(error)))
                raise OSError(errno.EPERM, str(error)) from error

        def open(self, path, mode):
            item = Path(path)
            if mode == 'rb':
                return super().open(path, mode)
            if mode != 'wb' or str(item.parent) not in attempts:
                raise OSError(errno.EPERM, 'only registered ordinary STOR allowed')
            try:
                writer = adapter.open_writer(attempts[str(item.parent)], item.name)
            except receiver.Refused as error:
                metrics['refusals'].append(dict(operation='open', path=str(item), reason=str(error),
                    existed=item.exists(), size=item.stat().st_size if item.exists() else None))
                raise OSError(errno.EPERM, str(error)) from error
            assert str(writer.stream.name) == str(item)
            writers[str(item)] = writer
            self.cmd_channel.fixture_writers.append(writer)
            metrics['opens'].append(dict(path=str(item), session=self.cmd_channel.fixture_session,
                live_writers=live_writers(), monotonic=time.monotonic()))
            return writer.stream

    class Data(TLS_DTPHandler):
        ac_in_buffer_size = 32

        def __init__(self, sock, cmd_channel):
            channels.append(self)
            super().__init__(sock, cmd_channel)
            self._counted = self._held = False

        def handle_ssl_established(self):
            metrics['data_tls'] += 1
            return super().handle_ssl_established()

        def handle_read_event(self):
            if self._held and not (work/'release-marker').exists():
                return
            super().handle_read_event()
            stream = getattr(self, 'file_obj', None)
            if (self.receive and self.tot_bytes_received > 0 and stream is not None
                    and not stream.closed and Path(stream.name).name == 'delivery.v1.txt'):
                stream.flush()
                os.fsync(stream.fileno())
                if (work/'interrupt-marker').exists() and not metrics['fault']:
                    metrics['fault'] = True
                    save(work/'marker-interrupted.json', dict(path=str(stream.name),
                        size=Path(stream.name).stat().st_size, live_writers=live_writers()))
                    self.close()
                    self.cmd_channel.close()
                elif not metrics['held_marker']:
                    metrics['held_marker'] = self._held = True
                    save(work/'marker-held.json', dict(path=str(stream.name),
                        size=Path(stream.name).stat().st_size, handle_closed=stream.closed,
                        live_writers=live_writers(), callbacks=list(metrics['callbacks'])))

        def close(self):
            if not getattr(self, '_counted', True):
                self._counted = True
                metrics['data_bytes'] += self.get_transmitted_bytes()
            return super().close()

    class Handler(TLS_FTPHandler):
        certfile, keyfile = args.cert, args.key
        dtp_handler, abstracted_fs = Data, OwnedFS
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
            self.fixture_writers = []
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
            assert cmd not in ('DELE', 'RMD', 'RNFR', 'RNTO', 'APPE', 'STOU', 'REST')
            metrics['commands'].append(dict(session=self.fixture_session, cmd=cmd, arg=arg, tls=protected))
            assert len(metrics['commands']) <= 256
            return super().process_command(cmd, *args, **kwargs)

        def complete(self, path, successful):
            item = Path(path)
            writer = writers[str(item)]
            assert writer.stream.closed
            row = dict(path=str(item), successful=successful, handle_closed=True,
                live_writers=live_writers(), bytes=item.stat().st_size,
                sha256=hashlib.sha256(item.read_bytes()).hexdigest(), monotonic=time.monotonic())
            row['result'] = adapter.writer_closed(writer, successful=successful)
            metrics['callbacks'].append(row)
            settled_writers.add(id(writer))
            if item.name == 'delivery.v1.txt':
                # Same original writer capability; no guessed or regenerated ID.
                row['duplicate_result'] = adapter.writer_closed(writer, successful=successful)
                save(work/('completed-delivery.json' if successful else 'failed-delivery.json'), row)

        def on_file_received(self, path):
            self.complete(path, True)

        def on_incomplete_file_received(self, path):
            self.complete(path, False)

        def on_disconnect(self):
            # Pending STOR can lose its control connection before data starts.
            # It is never converted to a successful receive callback.
            for writer in self.fixture_writers:
                if id(writer) not in settled_writers:
                    writer.stream.close()
                    adapter.writer_closed(writer, successful=False)
                    settled_writers.add(id(writer))

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
    save(work/'ready.json', dict(host='127.0.0.1', port=port, receiver_state=str(state),
        inbox=str(inbox), staging=str(staging), trusted_scope='synthetic account -> fixed private /fixture'))
    started = time.monotonic()
    try:
        while not (work/'stop').exists():
            assert time.monotonic()-started <= 60, 'server deadline'
            assert metrics['data_bytes'] <= 2*1024**2, 'wire bytes'
            live = sum(bool(getattr(obj, 'accepting', False)) for obj in server.ioloop.socket_map.values())
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
        adapter.close()
        metrics['all_closed'] = not server.ioloop.socket_map
        metrics['live_writers_at_exit'] = live_writers()
        metrics['remote_files'] = {str(f.relative_to(remote)): dict(size=f.stat().st_size,
            sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in remote.rglob('*') if f.is_file()}
        save(work/'server-result.json', metrics)


if __name__ == '__main__':
    main()
