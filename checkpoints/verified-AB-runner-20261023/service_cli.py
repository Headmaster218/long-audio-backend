"""Actual service entry, accepting no fake factory or optional observer mode."""
import argparse
import json
import os
from pathlib import Path
import socket
import time

from launch_binding import prepare
from service_wrapper import run_service
from child_environment import install_product_path


def write_once(path, value):
    encoded = (json.dumps(value, sort_keys=True, allow_nan=False)+'\n').encode()
    if len(encoded) > 1024*1024:
        raise ValueError('terminal receipt too large')
    with Path(path).open('xb') as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--without-trial-observer', action='store_true', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--instance-lock-fd', type=int, required=True)
    parser.add_argument('--listener-fd', type=int, required=True)
    parser.add_argument('--deadline', type=float, required=True)
    args = parser.parse_args(argv)
    if args.instance_lock_fd < 3 or args.listener_fd < 3 or args.instance_lock_fd == args.listener_fd:
        raise ValueError('distinct inherited descriptors required')
    if not 1 <= len(os.sched_getaffinity(0)) <= 2:
        raise ValueError('parent CPU affinity restriction missing')
    value, settings = prepare(args.manifest, args.manifest_sha256,
                              deadline=args.deadline, output=args.output)
    # Full pins and initial clean import paths are verified above. Normal
    # NativeProcess grandchildren use -m, so parent sys.path alone is not enough.
    install_product_path(os.environ, value['product_source'])
    listener = socket.socket(fileno=args.listener_fd)
    try:
        if listener.getsockname() != ('127.0.0.1', value['port']):
            raise ValueError('listener identity differs from manifest')
        import uvicorn
        from audio_backend.app import create_app
        if time.monotonic() >= args.deadline:
            raise TimeoutError('startup work deadline')
        run_service(settings=settings, output=args.output, port=value['port'],
                    instance_lock_fd=args.instance_lock_fd, app_factory=create_app,
                    uvicorn_module=uvicorn, sockets=[listener],
                    write_terminal=lambda row: write_once(args.output/'server-terminal.json', row),
                    without_trial_observer=args.without_trial_observer)
    finally:
        listener.close()


if __name__ == '__main__':
    main()
