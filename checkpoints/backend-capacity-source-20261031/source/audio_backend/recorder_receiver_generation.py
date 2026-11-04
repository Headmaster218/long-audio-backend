"""Prepare an inactive receiver generation; never switch routes or remove originals."""
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path

from . import recorder_ftps_receiver as service
from . import recorder_receiver_publication as publication
from .recorder_receiver_status import _acquire

require = publication.require
SCHEMA = 'recorder-receiver-generation-v1'
MEMBERS = {'intent.json', 'raw', 'raw.json', 'inbox', 'inbox.json', 'publication',
           'service.json', 'complete.json'}


def _bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def _read(path):
    return service._private_file(path, 16384)


def _write(path, value):
    """Exclusive durable write. Uncertain/partial files are never overwritten."""
    raw = _bytes(value)
    require(len(raw) <= 16384, 'generation_control_bound')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    publication.u._fsync_dir(path.parent)
    require(_read(path) == raw, 'generation_control_changed')


def _exact(path, expected):
    require(_read(path) == _bytes(expected), 'generation_component_mismatch')


def _empty_directory(root, name):
    path, receipt = root/name, root/(name+'.json')
    if os.path.lexists(path):
        # A mkdir interrupted before its identity receipt is not adopted.
        require(os.path.lexists(receipt), 'generation_unconfirmed_directory')
        publication._private(path)
        _exact(receipt, {'identity': publication._identity(path)})
    else:
        require(not os.path.lexists(receipt), 'generation_missing_directory')
        path.mkdir(mode=0o700)
        publication.u._fsync_dir(root)
        _write(receipt, {'identity': publication._identity(path)})
    require(not publication._names(path, 1), 'generation_not_inactive')
    return path


def prepare(*, old_config, root, port, passive_ports=None, resume=False):
    """Bounded stopped-only setup. Exact resume never adopts unknown partial state.

    Old data and routing are untouched. Completion verifies an empty, inactive
    generation, not endpoint availability, TLS suitability or capture continuity.
    """
    old_config, root = map(publication._path, (old_config, root))
    publication._private(root.parent)
    old_bytes = _read(old_config)
    old_data = json.loads(old_bytes, object_pairs_hook=service._unique_pairs)
    old = service._validate_config(old_data)
    service._integer(port, 1024, 65535)
    require(port != old.port, 'generation_different_control_port_required')
    require(type(resume) is bool, 'generation_resume_flag')
    new_data = dict(old_data, port=port)
    # An explicitly configured passive range must also remain independent from
    # the old listener. Ephemeral passive allocation remains OS-selected.
    if passive_ports is not None:
        require(type(passive_ports) in (list, tuple) and len(passive_ports) == 2,
                'generation_passive_range_invalid')
        new_data['passive_ports'] = list(passive_ports)
    proposed = service._validate_config(new_data)
    old_ports = set(old.passive_ports or ()) | {old.port}
    new_ports = set(proposed.passive_ports or ()) | {proposed.port}
    require(not old_ports & new_ports, 'generation_endpoint_ranges_overlap')
    binding = publication.validate_binding(old.binding, old.inbox_dir)
    old_roots = [Path(binding[k]) for k in ('state_dir', 'staging_dir', 'inbox_dir')]
    require(not any(root.is_relative_to(p) or p.is_relative_to(root) for p in old_roots),
            'generation_data_trees_overlap')
    require(not old_config.is_relative_to(root), 'generation_old_config_inside_root')
    # A prepared generation owns an exact root roster. Even a sibling of its
    # data trees would invalidate that generation's completion/resume controls.
    if any(os.path.lexists(old_config.parent/name) for name in ('intent.json', 'complete.json')):
        require(not root.is_relative_to(old_config.parent), 'generation_nested_generation_root')
    intent = dict(schema=SCHEMA, old_config=str(old_config),
        old_config_sha256=hashlib.sha256(old_bytes).hexdigest(), old_binding=old.binding,
        root=str(root), port=port,
        passive_ports=new_data.get('passive_ports'))
    with ExitStack() as stack:
        held = []
        for path, directory in ((Path(binding['staging_dir']), True),
                (Path(binding['state_dir'])/'preparation.lock', False),
                (Path(binding['state_dir'])/'publication.lock', False)):
            fd, stamp = _acquire(stack, path, directory=directory)
            held.append((path, fd, stamp))
        require(publication.validate_binding(old.binding, old.inbox_dir) == binding,
                'generation_old_binding_changed')
        require(_read(old_config) == old_bytes, 'generation_old_config_changed')
        if resume:
            publication._private(root)
        else:
            require(not os.path.lexists(root), 'generation_fresh_root_required')
            root.mkdir(mode=0o700)
            publication.u._fsync_dir(root.parent)
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, root_fd)
        try:
            fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise publication.Refused('generation_setup_busy') from None
        def stable():
            publication.snapshot._same_directory(root, root_fd)
            require(publication._names(root_fd, len(MEMBERS)) <= MEMBERS,
                    'generation_unknown_components')
            require(_read(old_config) == old_bytes, 'generation_old_config_changed')
            require(publication.validate_binding(old.binding, old.inbox_dir) == binding,
                    'generation_old_binding_changed')
            for path, fd, stamp in held:
                require(publication.snapshot._stamp(os.fstat(fd)) == stamp ==
                        publication.snapshot._stamp(path.lstat()), 'generation_old_path_changed')
        stable()
        if resume:
            _exact(root/'intent.json', intent)
        else:
            _write(root/'intent.json', intent)
        names = publication._names(root_fd, len(MEMBERS))
        # Later durable state cannot authorize reconstruction of missing earlier
        # components. Reject corruption before making any additional writes.
        for later, prerequisites in (
                ('inbox', {'raw', 'raw.json'}),
                ('publication', {'raw', 'raw.json', 'inbox', 'inbox.json'}),
                ('service.json', {'raw', 'raw.json', 'inbox', 'inbox.json', 'publication'}),
                ('complete.json', MEMBERS - {'complete.json'})):
            require(later not in names or prerequisites <= names,
                    'generation_inconsistent_components')
        raw = _empty_directory(root, 'raw')
        # Prevent a cooperating server from starting against resumed state.
        _acquire(stack, raw, directory=True)
        ready = _empty_directory(root, 'inbox')
        state = root/'publication'
        if not os.path.lexists(state):
            require(not os.path.lexists(root/'service.json') and
                    not os.path.lexists(root/'complete.json'), 'generation_missing_publication')
            publication.configure(state_dir=state, staging_dir=raw, inbox_dir=ready,
                                  exclusive_receiver=True)
        # Only a fully valid existing publication component may be resumed.
        config = publication._config(state)
        require(config['staging_dir'] == str(raw) and config['inbox_dir'] == str(ready),
                'generation_publication_mismatch')
        for name in ('preparation.lock', 'publication.lock'):
            _acquire(stack, state/name)
        require(not publication._names(state/'prepare', 1), 'generation_not_inactive')
        new_data.update(binding=publication.bind(state), inbox_dir=str(ready))
        service._validate_config(new_data)
        if os.path.lexists(root/'service.json'):
            _exact(root/'service.json', new_data)
        else:
            require(not os.path.lexists(root/'complete.json'), 'generation_missing_service')
            _write(root/'service.json', new_data)
        service.load_config(root/'service.json')
        stable()
        _empty_directory(root, 'raw')
        _empty_directory(root, 'inbox')
        receipt = dict(schema=SCHEMA, status='prepared',
            intent_sha256=hashlib.sha256(_bytes(intent)).hexdigest(),
            service_sha256=hashlib.sha256(_bytes(new_data)).hexdigest(),
            binding=new_data['binding'], raw_identity=publication._identity(raw),
            inbox_identity=publication._identity(ready))
        if os.path.lexists(root/'complete.json'):
            _exact(root/'complete.json', receipt)
        else:
            _write(root/'complete.json', receipt)
        stable()
    return dict(schema=SCHEMA, status='prepared', network_requests=0,
        service_started=False, routing_changed=False, old_data_removed=False,
        endpoint_available_verified=False, recording_continuity_verified=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-config', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--passive-ports', nargs=2, type=int)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = prepare(**vars(args))
    except KeyboardInterrupt:
        print(json.dumps(dict(status='interrupted', reason='generation_setup_interrupted',
                              preserve_existing_files=True, network_requests=0)))
        return 130
    except Exception:
        # Configuration may include credentials and private paths: fixed output.
        print(json.dumps(dict(status='refused', reason='generation_setup_refused',
                              preserve_existing_files=True, network_requests=0)))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
