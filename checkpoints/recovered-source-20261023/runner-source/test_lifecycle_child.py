"""Executable TEST fixture only. No app, model, Store, uploader or SQL import."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--product-source', required=True)
    parser.add_argument('--lock', required=True)
    parser.add_argument('--lock-fd', type=int, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--fault', choices=('none', 'ignore_term', 'after_adopt'), default='none')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.product_source).resolve(strict=True)))
    from audio_backend.resources import InstanceLock
    owner = InstanceLock(Path(args.lock)).adopt(args.lock_fd)
    output = Path(args.output)
    stop = False
    signals = 0
    def handle(*_):
        nonlocal stop, signals
        signals += 1
        if args.fault != 'ignore_term': stop = True
    signal.signal(signal.SIGTERM, handle)
    try:
        owner.verify_inherited()
        if args.fault == 'after_adopt':
            raise RuntimeError('fixed injected failure')
        with (output/'ready.json').open('x') as stream:
            json.dump({'ready': True, 'pid': os.getpid()}, stream)
        until = time.monotonic()+20
        while not stop and time.monotonic() < until:
            time.sleep(.01)
    finally:
        owner.release()
        with (output/'terminal.json').open('x') as stream:
            json.dump({'lock_closed': owner.file is None, 'signals': signals}, stream)


if __name__ == '__main__': main()
