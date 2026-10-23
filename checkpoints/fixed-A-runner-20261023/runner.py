"""New recovery runner interface. Service wiring is deliberately blocked for now."""
import argparse
from pathlib import Path


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--without-trial-observer', action='store_true', required=True)
    parser.add_argument('--kind', choices=('lifecycle', 'short', 'A'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('nonprivileged loopback port required')
    return args


def mode_receipt():
    return dict(trial_observer_mode='NOT_ENABLED', census_status='NO_CENSUS',
                trial_observer_enabled=False, healthy=None,
                complete_sampling_denominator=False)


def work_limits(kind):
    return {'lifecycle': (60, None), 'short': (300, 290), 'A': (1800, 1700)}[kind]


def require_no_census(output):
    for name in ('census', 'observer-config.json'):
        path = Path(output)/name
        if path.exists() or path.is_symlink():
            raise RuntimeError('unexpected census artifact in observer-off mode')


def main(argv=None):
    parse_arguments(argv)
    # Do not create output, locks, socket, process or model before bindings are
    # recovered and a real service wrapper has passed independent source review.
    raise RuntimeError('NOT READY: product/runtime pins, real locks and service wiring pending')


if __name__ == '__main__':
    main()
