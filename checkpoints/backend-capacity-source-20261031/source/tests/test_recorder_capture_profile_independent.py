"""Independent recorder profile arithmetic and no-operation-I/O audit.

Run from the backend checkout:
  PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python tests/test_recorder_capture_profile_independent.py
  PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python tests/test_recorder_capture_profile_independent.py --full
  PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_recorder_capture_profile_independent.py

The default/pytest matrix is bounded. --full checks 259,200 profiles.
This tests arithmetic, not media ingestion, devices or sustained runtime.
The I/O check starts after normal imports: it does not claim Python imports
perform zero filesystem reads or never create bytecode caches.
"""

import argparse
import builtins
from contextlib import ExitStack
import json
import math
import os
import socket
import subprocess
from unittest.mock import patch

from audio_backend.recorder_capture_profile import plan


# Independent contract constants rather than the planner's imported constants.
RATE = 16000
PAGE = 294 * RATE
SOURCE = 120 * RATE
SLOTS = 16
HOURS = (1, 7, 12, 48)
COUNTS = (1, 2, 16, 64, 85, 128, 143, 144, 192)
REPRESENTATIVE_SECONDS = (
    1, 2, 4, 10, 18, 19, 20, 21, 30, 60, 119, 120, 121,
    146, 147, 148, 293, 294, 295, 299, 300, 301, 337, 338,
    599, 600, 601, 3599, 3600,
)


def piece_count(start, end, target):
    """Count intersections directly; do not simulate uploader piece allocation."""
    if end <= start:
        return 0
    return sum(
        math.ceil((min(end, (j + 1) * target) - max(start, j * target)) / SOURCE)
        for j in range(start // target, math.ceil(end / target))
    )


def oracle(seconds, hours, inputs, terminal_aware):
    target = seconds * RATE
    cap = hours * 3600 * RATE
    maxima = []
    for index in range(inputs):
        position = index * target
        if position >= cap:
            return max(maxima), index, 'duration', None
        page_start = (position // PAGE) * PAGE
        used = piece_count(page_start, position, target)
        reservation = math.ceil(min(PAGE - (position - page_start), cap - position) / SOURCE)
        if used + reservation > SLOTS:
            return max(maxima), index, 'page_source_slots', index
        maxima.append(min(position + target - (0 if terminal_aware else 1), cap))
        if position + target > cap:
            return max(maxima), index, 'duration', None
    return max(maxima), inputs, ('duration' if inputs * target == cap else 'input_count'), None


def check_matrix(seconds):
    checked = 0
    for segment in seconds:
        for hours in HOURS:
            for inputs in COUNTS:
                for mode in ('terminal-aware', 'legacy-rotation-only'):
                    result = plan(segment_seconds=segment, max_hours=hours, max_inputs=inputs,
                                  producer_mode=mode)
                    refusal = result['first_source_slot_refusal']
                    actual = (
                        result['maximum_frames_with_stop'],
                        result['full_rotations_before_limit'],
                        result['limiting_factor'],
                        None if refusal is None else refusal['input_ordinal'],
                    )
                    expected = oracle(segment, hours, inputs, mode == 'terminal-aware')
                    assert actual == expected, (segment, hours, inputs, mode, actual, expected)
                    assert result['maximum_frames_with_nonempty_stop_tail'] == oracle(
                        segment, hours, inputs, False)[0]
                    checked += 1
    return checked


def deny_io(*args, **kwargs):
    raise AssertionError('Unexpected media/state/backend or process I/O')


def check_no_operation_io():
    operations = (
        (builtins, ('open',)),
        (os, ('open', 'mkdir', 'makedirs', 'remove', 'unlink', 'rename', 'replace',
              'stat', 'lstat', 'scandir')),
        (socket, ('socket', 'create_connection')),
        (subprocess, ('Popen', 'run', 'call')),
    )
    with ExitStack() as stack:
        for owner, names in operations:
            for name in names:
                stack.enter_context(patch.object(owner, name, deny_io))
        for seconds in (1, 19, 20, 294, 300, 3600):
            plan(segment_seconds=seconds, max_inputs=128, expected_seconds=25200)
    return 6


def test_representative_independent_oracle():
    assert check_matrix(REPRESENTATIVE_SECONDS) == 2088


def test_plan_does_not_perform_media_state_backend_io():
    assert check_no_operation_io() == 6


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='Check all 259,200 profiles')
    args = parser.parse_args(argv)
    count = check_matrix(range(1, 3601) if args.full else REPRESENTATIVE_SECONDS)
    blocked = check_no_operation_io()
    print(json.dumps(dict(
        status='passed', profiles_checked=count, blocked_io_calls_checked=blocked,
        arithmetic_only=True, native_or_device_qualification=False,
    ), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
