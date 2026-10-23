"""Exact existing schema1 policy, fixed before collection; no tuning or copying."""
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from bindings import canonical

POLICY_RELATIVE = 'audio_backend/profiles/verified-review-experimental-v1.json'
POLICY_SHA = '5217b5fe9737ba2b0348d8ec1d2ba31ccbce6da264630a7506a6f5a71b09cbff'
POLICY = dict(schema_version=1, experimental=True, single_threshold=.75,
              centroid_threshold=.75, margin=.10, minimum_query_seconds=2.,
              minimum_enrollment_seconds=2.)


def verify_policy(value, deadline):
    raw = value['settings'].get('verified_identity_policy_file')
    if value['qualification'] != 'AB-two-session':
        if raw is not None: raise ValueError('single-session policy override forbidden')
        return
    expected_path = str(Path(value['product_source'])/POLICY_RELATIVE)
    if raw != expected_path:
        raise ValueError('AB requires exact frozen source policy path')
    rows = [row for row in value['files'] if row['path'] == raw]
    if len(rows) != 1 or rows[0].get('sha256') != POLICY_SHA:
        raise ValueError('AB policy lacks exact unique content pin')
    if time.monotonic() >= deadline: raise TimeoutError('policy verification deadline')
    path = canonical(raw)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode): raise ValueError('policy must be regular')
        data = stream.read(8193)
        after = os.fstat(stream.fileno())
    if time.monotonic() >= deadline: raise TimeoutError('policy verification deadline')
    if len(data) > 8192 or len(data) != rows[0].get('bytes'):
        raise ValueError('policy size differs')
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('policy changed during bounded read')
    if hashlib.sha256(data).hexdigest() != POLICY_SHA or json.loads(data) != POLICY:
        raise ValueError('policy differs from frozen schema1 experiment')


def validate_effective_policy(settings, value):
    if value['qualification'] == 'AB-two-session':
        if (str(settings.verified_identity_policy_file) != str(Path(value['product_source'])/POLICY_RELATIVE)
                or settings.verified_identity_policy != POLICY):
            raise ValueError('effective verified policy differs')
    elif settings.verified_identity_policy is not None:
        raise ValueError('unexpected effective verified policy')
