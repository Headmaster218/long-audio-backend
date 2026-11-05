"""Opt-in, finite FTPS receiver. No intake, credentials provisioning or retry.

Only the pinned pyftpdlib receive path is supported. A stream settles before
226, and only an orderly protected-data EOF can be successful. Service state is
local, single-process, private and exclusively owned by ReceiverCompletion.
"""
import argparse
from dataclasses import dataclass, field
import errno
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import signal
import stat
import sys

from . import recorder_receiver_completion as completion
from . import recorder_receiver_publication as publication

SCHEMA = 'recorder-ftps-receiver-config-v1'
DEPENDENCIES = {'pyftpdlib': '2.1.0', 'pyOpenSSL': '25.3.0'}
ALLOWED_COMMANDS = {'AUTH', 'USER', 'PASS', 'PBSZ', 'PROT', 'FEAT', 'OPTS', 'SYST',
                    'TYPE', 'PWD', 'CWD', 'CDUP', 'PASV', 'EPSV', 'MKD', 'STOR',
                    'NOOP', 'ABOR', 'QUIT', 'RETR'}


class ConfigurationError(ValueError):
    """Fixed diagnostic codes only, never input or exception text."""


def _require(condition, code):
    if not condition:
        raise ConfigurationError(code)


def _path(value):
    _require(type(value) is str, 'receiver_path_required')
    path = Path(value)
    _require(path.is_absolute() and path == path.resolve(), 'receiver_canonical_path_required')
    return path


def _private_file(path, maximum):
    path = _path(str(path))
    publication._private(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                 and not before.st_mode & 0o077 and before.st_nlink == 1
                 and 0 < before.st_size <= maximum, 'receiver_private_file_required')
        data = os.read(fd, maximum + 1)
        _require(len(data) == before.st_size
                 and publication.snapshot._stamp(os.fstat(fd)) == publication.snapshot._stamp(before)
                 and publication.snapshot._stamp(path.stat()) == publication.snapshot._stamp(before), 'receiver_private_file_changed')
        return data
    finally:
        os.close(fd)


def _integer(value, low, high):
    _require(type(value) is int and low <= value <= high, 'receiver_config_bound')
    return value


def _unique_pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, 'receiver_duplicate_config_key')
        result[key] = value
    return result


@dataclass(frozen=True)
class Config:
    binding: dict
    inbox_dir: Path
    cert_file: Path
    key_file: Path
    username: str = field(repr=False)
    password: str = field(repr=False)
    bind_host: str = '127.0.0.1'
    port: int = 2121
    passive_ports: tuple | None = None
    max_connections: int = 8
    max_connections_per_ip: int = 4
    max_attempts: int = 256
    max_raw_bytes: int = 8 * 1024**3
    max_readback_bytes: int = 2 * 1024**3
    idle_seconds: int = 60
    session_seconds: int = 3600
    max_commands: int = 10000
    publication_seconds: int = 30
    generations: tuple | None = None
    auto_advance_generations: bool = False


def load_config(path):
    """Read existing private config and secret files, without binding a socket."""
    data = json.loads(_private_file(path, 16384), object_pairs_hook=_unique_pairs)
    return _validate_config(data)


def _validate_config(data):
    required = {'schema', 'binding', 'inbox_dir', 'cert_file', 'key_file', 'username', 'password_file'}
    optional = {'bind_host', 'port', 'passive_ports', 'max_connections', 'max_connections_per_ip',
                'max_attempts', 'max_raw_bytes', 'idle_seconds', 'session_seconds', 'max_commands',
                'generations', 'auto_advance_generations', 'publication_seconds', 'allow_non_loopback', 'acknowledge_data_peer_ip_only', 'max_readback_bytes'}
    _require(type(data) is dict and required <= data.keys() and not data.keys() - required - optional
             and data['schema'] == SCHEMA, 'receiver_config_fields')
    username = data['username']
    _require(type(username) is str and re.fullmatch(r'[A-Za-z0-9_.@-]{1,64}', username)
             and username.lower() != 'anonymous', 'receiver_account_required')
    password = _private_file(_path(data['password_file']), 4096).decode('utf-8')
    # A single conventional text-file terminator is allowed; never strip spaces.
    if password.endswith('\n'):
        password = password[:-1]
    _require(1 <= len(password) <= 1024 and not any(c in password for c in '\r\n\x00'),
             'receiver_password_file_invalid')
    inbox = _path(data['inbox_dir'])
    publication.validate_binding(data['binding'], inbox)
    cert, key = _path(data['cert_file']), _path(data['key_file'])
    _private_file(key, 65536)
    _require(cert.is_file() and cert.stat().st_size <= 1024 * 1024, 'receiver_certificate_file_invalid')
    host = data.get('bind_host', '127.0.0.1')
    _require(type(host) is str, 'receiver_bind_address_invalid')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ConfigurationError('receiver_numeric_bind_address_required') from None
    allow = data.get('allow_non_loopback', False)
    acknowledge = data.get('acknowledge_data_peer_ip_only', False)
    _require(type(allow) is bool and type(acknowledge) is bool
             and (address.is_loopback or (allow and acknowledge)), 'receiver_non_loopback_opt_in_required')
    ports = data.get('passive_ports')
    if ports is not None:
        _require(type(ports) is list and len(ports) == 2, 'receiver_passive_range_invalid')
        start, end = [_integer(p, 1024, 65535) for p in ports]
        _require(0 <= end - start < 128, 'receiver_passive_range_invalid')
        ports = tuple(range(start, end + 1))
    defaults = Config.__dataclass_fields__
    ranges = {'port': (1024, 65535), 'max_connections': (2, 32),
              'max_connections_per_ip': (1, 16), 'max_attempts': (1, completion.MAX_ATTEMPTS),
              'max_raw_bytes': (1, completion.MAX_RAW_BYTES),
              'max_readback_bytes': (1, completion.MAX_RAW_BYTES), 'idle_seconds': (1, 300),
              'session_seconds': (1, 86400), 'max_commands': (10, 100000),
              'publication_seconds': (1, 120)}
    values = {name: _integer(data.get(name, defaults[name].default), *bounds)
              for name, bounds in ranges.items()}
    _require(values['max_connections_per_ip'] <= values['max_connections'], 'receiver_connection_bound')
    _require(ports is None or values['port'] not in ports, 'receiver_passive_control_overlap')
    generations = data.get('generations')
    automatic = data.get('auto_advance_generations', False)
    _require(type(automatic) is bool and (not automatic or generations is not None),
             'receiver_automatic_routing_requires_generations')
    if generations is not None:
        from .recorder_receiver_routing import validate
        validate(generations,data['binding'],inbox)
        generations = tuple(generations)
    return Config(auto_advance_generations=automatic,generations=generations,binding=data['binding'], inbox_dir=inbox, cert_file=cert, key_file=key,
                  username=username, password=password, bind_host=str(address), passive_ports=ports,
                  **values)


def configure(*, config_path, state_dir, inbox_dir, cert_file, key_file,
              username, password_file, bind_host='127.0.0.1', port=2121,
              passive_ports=None, allow_non_loopback=False,
              acknowledge_data_peer_ip_only=False):
    """Create one private config from existing inputs; never provision or listen.

    Failure after exclusive file creation retains the partial file for explicit
    operator review. It must not be treated as success or overwritten on retry.
    """
    path = _path(str(config_path))
    publication._private(path.parent)
    _require(not os.path.lexists(path), 'receiver_fresh_config_required')
    data = dict(schema=SCHEMA, binding=publication.bind(_path(str(state_dir))),
        inbox_dir=str(inbox_dir), cert_file=str(cert_file), key_file=str(key_file),
        username=username, password_file=str(password_file), bind_host=bind_host,
        port=port, allow_non_loopback=allow_non_loopback,
        acknowledge_data_peer_ip_only=acknowledge_data_peer_ip_only)
    if passive_ports is not None:
        data['passive_ports'] = list(passive_ports)
    _validate_config(data)  # All configured inputs are checked before any write.
    publication_config = publication._config(_path(str(state_dir)))
    _require(not any(path.is_relative_to(Path(publication_config[name])) for name in
                     ('state_dir', 'staging_dir', 'inbox_dir')),
             'receiver_config_must_be_outside_data_trees')
    raw = (json.dumps(data, sort_keys=True, indent=2) + '\n').encode()
    _require(len(raw) <= 16384, 'receiver_config_bound')
    before = path.parent.stat()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _require((os.fstat(directory).st_dev, os.fstat(directory).st_ino)
                 == (before.st_dev, before.st_ino), 'receiver_config_parent_changed')
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.fsync(directory)
        publication._private(path.parent)
        _require((path.parent.stat().st_dev, path.parent.stat().st_ino)
                 == (before.st_dev, before.st_ino), 'receiver_config_parent_changed')
        _require(_private_file(path, 16384) == raw, 'receiver_config_changed')
        load_config(path)  # Detect changed input files/binding before success.
    finally:
        os.close(directory)
    return dict(status='configuration_created', network_requests=0,
                tls_material_loaded=False, automatic_intake_enabled=False,
                credentials_created=False, service_started=False)


class _Readback:
    """A fixed-size read snapshot, charged in full before any outbound data."""
    def __init__(self, stream, name, size):
        self.stream, self.name, self.remaining = stream, name, size

    @property
    def closed(self):
        return self.stream.closed

    def read(self, size):
        data = self.stream.read(min(size, self.remaining))
        self.remaining -= len(data)
        return data

    def close(self):
        self.stream.close()


class _Upload:
    """No alternate descriptors or paths: every write uses the adapter stream."""
    def __init__(self, writer, handler, owner):
        self.writer, self.handler, self.owner = writer, handler, owner
        self.name, self.mode = writer.stream.name, 'wb'
        self.settled = False
        self.accepted = False

    @property
    def closed(self):
        return self.writer.stream.closed

    def write(self, data):
        return self.writer.stream.write(data)

    def close(self):
        if self.settled:
            return
        channel = getattr(self.handler, 'data_channel', None)
        successful = (channel is not None and channel.file_obj is self
                      and getattr(channel, '_receiver_outcome', False)
                      and not self.handler._receiver_ending
                      and not getattr(self.handler.server, 'receiver_stopping', False))
        try:
            self.writer.stream.close()
            result = self.owner.writer_closed(self.writer, successful=successful)
            self.accepted = successful and result['status'] in ('receiving', 'published')
        except Exception:
            # Publication may have committed before an error. Never replay it.
            self.handler.server.receiver_fault = True
            self.accepted = False
        finally:
            self.settled = True
            if not self.accepted and channel is not None and channel.file_obj is self:
                channel.transfer_finished = False
                channel._resp = ('451 Receive not committed; operator review required.', lambda *a: None)


def create_server(config):
    """Validate dependencies/TLS, acquire the staging lease, then explicitly bind.

    Caller owns close_all() and owner.close(). This is deliberately not threaded
    or multiprocess; pyftpdlib internals used here are pinned and wire-tested.
    """
    from importlib.metadata import version
    for name, wanted in DEPENDENCIES.items():
        _require(version(name) == wanted, 'receiver_dependency_version_mismatch')
    from OpenSSL import SSL
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.filesystems import AbstractedFS
    from pyftpdlib.handlers import PassiveDTP, TLS_DTPHandler, TLS_FTPHandler
    from pyftpdlib.ioloop import IOLoop
    from pyftpdlib.servers import FTPServer

    # pyftpdlib debug/error paths may contain commands, usernames or file paths.
    # This standalone receiver intentionally emits only fixed lifecycle codes.
    logger = logging.getLogger('pyftpdlib')
    logger.handlers[:] = [logging.NullHandler()]
    logger.propagate = False
    logger.disabled = True
    context = SSL.Context(SSL.TLS_SERVER_METHOD)
    context.set_min_proto_version(SSL.TLS1_2_VERSION)
    context.set_options(SSL.OP_NO_COMPRESSION)
    context.use_certificate_chain_file(str(config.cert_file))
    context.use_privatekey_file(str(config.key_file))
    context.check_privatekey()
    receiver_config = publication.validate_binding(config.binding, config.inbox_dir)
    staging = Path(receiver_config['staging_dir'])

    class OwnedFS(AbstractedFS):
        def route(self, name):
            try:
                index = owner.resolve(name)
                pins = self.cmd_channel._receiver_routes
                if name in pins and pins[name] != index:
                    raise publication.Refused('routing_connection_changed')
                pins[name] = index
                return index
            except (publication.Refused, OSError):
                raise OSError(errno.EPERM, 'Receive route refused') from None

        def chdir(self, path):
            if config.generations is None:
                return super().chdir(path)
            item = Path(path)
            if item != staging:
                if item.parent != staging:
                    raise OSError(errno.EPERM, 'Receive directory refused')
                self.route(item.name)
            self.cwd = self.fs2ftp(path)

        def mkdir(self, path):
            item = Path(path)
            if item.parent != staging:
                raise OSError(errno.EPERM, 'Receive directory refused')
            try:
                if config.generations is None:
                    self.cmd_channel._receiver_attempts[str(item)] = owner.begin_attempt(item.name)
                else:
                    selected, attempt = owner.begin(item.name)
                    self.cmd_channel._receiver_attempts[str(item)] = (selected,attempt)
                    self.route(item.name)
            except (publication.Refused, OSError):
                raise OSError(errno.EPERM, 'Receive directory refused') from None

        def open(self, path, mode):
            item = Path(path)
            handler = self.cmd_channel
            if mode == 'rb':
                return self.readback(item)
            if mode != 'wb' or str(item.parent) not in handler._receiver_attempts:
                raise OSError(errno.EPERM, 'Receive file refused')
            try:
                selected = owner
                attempt = handler._receiver_attempts[str(item.parent)]
                if config.generations is not None:
                    self.route(item.parent.name)
                    selected, attempt = attempt
                writer = selected.open_writer(attempt, item.name)
            except (publication.Refused, OSError):
                raise OSError(errno.EPERM, 'Receive file refused') from None
            stream = _Upload(writer, handler, selected)
            handler._receiver_uploads.append(stream)
            return stream

        def readback(self, item):
            handler = self.cmd_channel
            if (item.parent.parent != staging or item.name not in publication.FILES
                    or publication.inbox.NAME.fullmatch(item.parent.name) is None):
                raise OSError(errno.EPERM, 'Readback refused')
            binding, inbox_dir, raw_dir = config.binding, config.inbox_dir, staging
            if config.generations is not None:
                binding, inbox_dir, raw_dir = owner.generations[self.route(item.parent.name)]
            actual = raw_dir/item.parent.name/item.name
            known = next((u for u in handler._receiver_uploads if u.name == str(actual) and u.accepted), None)
            # Reconnect readback is served only from an already published,
            # immutable copy of this exact attempt. Never infer raw completion.
            directory = actual.parent if known else inbox_dir/item.parent.name
            fd = raw = None
            try:
                publication.validate_binding(binding, inbox_dir)
                publication._private(directory)
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                publication.snapshot._same_directory(directory, fd)
                raw = os.open(item.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd)
                info = os.fstat(raw)
                if not (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                        and not info.st_mode & 0o077 and 0 < info.st_size <= completion.LIMITS[item.name]
                        and handler._receiver_read_bytes + info.st_size <= config.max_readback_bytes):
                    raise OSError(errno.EPERM, 'Readback refused')
                if known and publication.snapshot._stamp(info) != known.writer.stream.final_stamp:
                    raise OSError(errno.EPERM, 'Readback changed')
                handler._receiver_read_bytes += info.st_size
                stream = os.fdopen(raw, 'rb'); raw = None
                return _Readback(stream, str(item), info.st_size)
            except (publication.Refused, OSError):
                raise OSError(errno.EPERM, 'Readback refused') from None
            finally:
                if raw is not None: os.close(raw)
                if fd is not None: os.close(fd)

    class Passive(PassiveDTP):
        timeout = config.idle_seconds

        def __init__(self, *args, **kwargs):
            self._receiver_probe = False
            try:
                super().__init__(*args, **kwargs)
            except BaseException:
                self.close()
                raise

        def bind_af_unspecified(self, address):
            # Upstream probes the IPv6 family with a temporary, non-listening
            # ephemeral socket. Only the actual configured listener is bounded.
            self._receiver_probe = True
            try:
                return super().bind_af_unspecified(address)
            finally:
                self._receiver_probe = False

        def bind(self, address):
            if config.passive_ports is not None and address[1] == 0 and not self._receiver_probe:
                raise OSError(errno.EADDRINUSE, 'Configured passive ports unavailable')
            return super().bind(address)

    class Data(TLS_DTPHandler):
        timeout = config.idle_seconds
        ac_in_buffer_size = 65536

        def close(self):
            if not hasattr(self, '_receiver_outcome'):
                # Read before pyftpdlib's TLS shutdown helper synthesizes a
                # RECEIVED_SHUTDOWN bit. Unclean TLS EOF is never completion.
                sock = getattr(self, 'socket', None)
                clean = isinstance(sock, SSL.Connection) and bool(sock.get_shutdown() & SSL.RECEIVED_SHUTDOWN)
                self._receiver_outcome = bool(clean and self.receive and self.transfer_finished
                    and not self._error and self.cmd_channel.connected
                    and not self.cmd_channel._receiver_ending
                    and not getattr(self.cmd_channel.server, 'receiver_stopping', False))
            return super().close()

        def handle_error(self):
            self._receiver_outcome = False
            self._error = True
            self._resp = ('451 Receive failed; operator review required.', lambda *a: None)
            self.close()

    class Handler(TLS_FTPHandler):
        abstracted_fs, dtp_handler, passive_dtp = OwnedFS, Data, Passive
        ssl_context = context
        certfile, keyfile = str(config.cert_file), str(config.key_file)
        tls_control_required = tls_data_required = True
        timeout = config.idle_seconds
        max_login_attempts = 3
        auth_failed_timeout = 1
        passive_ports = config.passive_ports
        permit_foreign_addresses = permit_privileged_ports = False
        use_sendfile = False
        banner = 'Recorder receive service ready.'
        # No list/rename/delete/resume/active-data alternatives. Account
        # switching mid-session is separately refused below.
        allowed = ALLOWED_COMMANDS
        proto_cmds = {key: value for key, value in TLS_FTPHandler.proto_cmds.items() if key in ALLOWED_COMMANDS}

        def __init__(self, *args, **kwargs):
            self._receiver_attempts = {}
            self._receiver_routes = {}
            self._receiver_uploads = []
            self._receiver_ending = False
            self._receiver_commands = 0
            self._receiver_read_bytes = 0
            self._receiver_timer = None
            super().__init__(*args, **kwargs)

        def on_connect(self):
            self._receiver_timer = self.ioloop.call_later(config.session_seconds, self.close)

        def pre_process_command(self, line, cmd, arg):
            self._receiver_commands += 1
            if self._receiver_commands > config.max_commands:
                self.respond('421 Session command limit reached.')
                self.close_when_done()
                return
            if cmd not in self.allowed:
                self.respond('502 Command not supported by receiver.')
                return
            if self.authenticated and cmd in {'USER', 'PASS', 'AUTH'}:
                self.respond('503 Reconnect to authenticate again.')
                return
            return super().pre_process_command(line, cmd, arg)

        def _make_epasv(self, extmode=False):
            try:
                return super()._make_epasv(extmode)
            except OSError:
                self.respond('425 Configured passive listener unavailable.')

        def ftp_TYPE(self, line):
            if line.upper() not in {'I', 'L8', 'L 8'}:
                self.respond('504 Binary transfers required.')
                return
            return super().ftp_TYPE(line)

        def ftp_PROT(self, line):
            if line.upper() != 'P':
                self.respond('536 Protected data channel required.')
                return
            return super().ftp_PROT(line)

        def _pending_transfer(self):
            channel = getattr(self, 'data_channel', None)
            return (any(not upload.settled for upload in self._receiver_uploads)
                    or self._in_dtp_queue is not None or self._out_dtp_queue is not None
                    or (channel is not None and channel.file_obj is not None))

        def ftp_RETR(self, file):
            if self._current_type != 'i' or not self._prot or self._receiver_ending or self._pending_transfer():
                self.respond('503 Protected binary transfer with no pending transfer required.')
                return
            return super().ftp_RETR(file)

        def ftp_STOR(self, file, mode='w'):
            if (self._current_type != 'i' or not self._prot or self._receiver_ending
                    or self._pending_transfer()):
                self.respond('503 Protected binary transfer with no pending STOR required.')
                return
            return super().ftp_STOR(file, mode)

        def ftp_ABOR(self, line):
            self.respond('426 Transfer aborted; reconnect with a fresh attempt.')
            self._abort_uploads()
            self.close_when_done()

        def ftp_QUIT(self, line):
            # Unlike generic FTP, quitting does not allow a pending upload to
            # become successful while the control session is going away.
            self._abort_uploads()
            return super().ftp_QUIT(line)

        def _abort_uploads(self):
            self._receiver_ending = True
            for upload in self._receiver_uploads:
                upload.close()
            channel = getattr(self, 'data_channel', None)
            if channel is not None:
                channel._receiver_outcome = False
                channel.close()

        def close(self):
            self._abort_uploads()
            timer = self._receiver_timer
            if timer is not None and not timer.cancelled:
                timer.cancel()
            return super().close()

        def on_disconnect(self):
            self._abort_uploads()

        def handle_error(self):
            self.server.receiver_fault = True
            self.close()

        # No raw commands, paths, account names, audio, tracebacks or credentials.
        def log(self, *args, **kwargs): pass
        def logline(self, *args, **kwargs): pass
        def log_exception(self, *args, **kwargs): pass
        def log_cmd(self, *args, **kwargs): pass
        def log_transfer(self, *args, **kwargs): pass

    loop = owner = None
    try:
        authorizer = DummyAuthorizer()
        authorizer.add_user(config.username, config.password, str(staging), perm='erwm')
        Handler.authorizer = authorizer
        loop = IOLoop()
        if config.generations is None:
            owner = completion.ReceiverCompletion(binding=config.binding, inbox_dir=config.inbox_dir,
                exclusive_receiver=True, max_attempts=config.max_attempts, max_raw_bytes=config.max_raw_bytes,
                deadline_seconds=config.publication_seconds)
        else:
            from .recorder_receiver_routing import Routes
            owner = Routes(config)
        server = FTPServer((config.bind_host, config.port), Handler, ioloop=loop)
        server.max_cons = config.max_connections
        server.max_cons_per_ip = config.max_connections_per_ip
        server.receiver_fault = False
        server.receiver_stopping = False
        return server, owner
    except BaseException:
        try:
            if loop is not None: loop.close()
        finally:
            if owner is not None: owner.close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('configure', 'check', 'serve'))
    parser.add_argument('--config', required=True, help='Existing private JSON config (no secrets on command line)')
    for name in ('state-dir', 'inbox-dir', 'cert-file', 'key-file', 'username', 'password-file'):
        parser.add_argument('--' + name)
    parser.add_argument('--bind-host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=2121)
    parser.add_argument('--passive-ports', nargs=2, type=int, metavar=('START', 'END'))
    parser.add_argument('--allow-non-loopback', action='store_true')
    parser.add_argument('--acknowledge-data-peer-ip-only', action='store_true')
    args = parser.parse_args(argv)
    server = owner = None
    stopped = False
    old_signals = {}
    cleanup_failed = False
    def stop(signum, frame):
        nonlocal stopped
        stopped = True
        if server is not None:
            server.receiver_stopping = True
    try:
        if args.operation == 'configure':
            _require(all(getattr(args, name) is not None for name in
                ('state_dir', 'inbox_dir', 'cert_file', 'key_file', 'username', 'password_file')),
                'receiver_configuration_inputs_required')
            values = vars(args).copy(); values.pop('operation')
            values['config_path'] = values.pop('config')
            print(json.dumps(configure(**values), sort_keys=True))
            return 0
        # Setup-only arguments never silently override a saved configuration.
        _require(not any(getattr(args, name) is not None for name in
            ('state_dir', 'inbox_dir', 'cert_file', 'key_file', 'username', 'password_file', 'passive_ports'))
            and args.bind_host == '127.0.0.1' and args.port == 2121
            and not args.allow_non_loopback and not args.acknowledge_data_peer_ip_only,
            'receiver_setup_arguments_not_allowed')
        config = load_config(args.config)
        if args.operation == 'check':
            print(json.dumps({'status': 'configuration_valid', 'network_requests': 0,
                              'tls_material_loaded': False, 'automatic_intake_enabled': False}))
            return 0
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_signals[sig] = signal.signal(sig, stop)
        server, owner = create_server(config)
        print(json.dumps({'status': 'listening', 'automatic_intake_enabled': False}), flush=True)
        while not stopped and not server.receiver_fault:
            server.serve_forever(timeout=.2, blocking=False, handle_exit=False)
        if server.receiver_fault:
            print(json.dumps({'status': 'stopped', 'reason': 'receiver_fault_operator_review_required'}), file=sys.stderr)
            return 1
        return 0
    except KeyboardInterrupt:
        if args.operation == 'configure':
            print(json.dumps({'status': 'cancelled', 'reason': 'receiver_configuration_interrupted',
                              'preserve_existing_config': True}), file=sys.stderr)
            return 130
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ConfigurationError) else 'receiver_configuration_or_runtime_failure'
        print(json.dumps({'status': 'refused', 'reason': reason}), file=sys.stderr)
        return 1
    finally:
        try:
            if server is not None:
                server.receiver_stopping = True
                server.close_all()
        except Exception:
            cleanup_failed = True
        finally:
            try:
                if owner is not None:
                    owner.close()
            except Exception:
                cleanup_failed = True
            for sig, previous in old_signals.items():
                signal.signal(sig, previous)
        if cleanup_failed:
            print(json.dumps({'status': 'stopped', 'reason': 'receiver_close_unresolved'}), file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
