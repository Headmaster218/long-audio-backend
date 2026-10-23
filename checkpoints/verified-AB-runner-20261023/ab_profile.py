"""Explicit bounded two-session integration profile, collection only."""
KIND = 'AB-two-session'
GROUP = 'AB-verified-anonymous'
PROFILE = dict(kind=KIND, session_order=['A', 'B'], capture_ids=['A-fixed', 'B-fixed'],
               identity_group=GROUP, identity_mode='verified_review_v1', max_sessions=2,
               reviewed_primary_persons_enabled=False,
               verified_policy_sha256='5217b5fe9737ba2b0348d8ec1d2ba31ccbce6da264630a7506a6f5a71b09cbff')


def validate_arm(arm, result):
    from a_input import SHA256 as A_SHA
    from b_input import SHA256 as B_SHA
    expected = dict(completed_chunks=69, accepted_sources=8, pages=3, eos_pages=1,
                    frames=11520000, seconds=720, input_sha256={'A': A_SHA, 'B': B_SHA}[arm],
                    source_window_seconds=[840, 1560], capture_origin=100003,
                    capture_end=11620003, exact_offsets=True, exact_frame_conservation=True)
    if not isinstance(result, dict) or any(result.get(k) != v for k, v in expected.items()):
        raise ValueError('AB arm lacks fixed complete receipt')


def validate_result(result):
    import re
    if (result.get('integration_profile') != PROFILE or result.get('completed_chunks') != 138
            or result.get('frames') != 23040000 or result.get('seconds') != 1440
            or result.get('accepted_sources') != 16 or result.get('pages') != 6
            or result.get('eos_pages') != 2 or result.get('exact_offsets') is not True
            or result.get('exact_frame_conservation') is not True):
        raise ValueError('AB aggregate completion differs')
    sessions, arms = result.get('sessions'), result.get('arms')
    if (not isinstance(sessions, dict) or set(sessions) != {'A', 'B'}
            or not all(isinstance(sid, str) and re.fullmatch('[a-f0-9]{32}', sid) for sid in sessions.values())
            or len(set(sessions.values())) != 2 or not isinstance(arms, dict) or set(arms) != {'A', 'B'}):
        raise ValueError('AB distinct completed arm receipts missing')
    for arm in ('A', 'B'): validate_arm(arm, arms[arm])


def validate_inputs(value, pinned, deadline):
    from a_input import AInput, SHA256 as A_SHA, BYTES as A_BYTES
    from b_input import BInput, SHA256 as B_SHA, BYTES as B_BYTES
    from bindings import canonical
    if value.get('integration_profile') != PROFILE:
        raise ValueError('AB explicit integration profile differs')
    rows = value.get('input')
    if not isinstance(rows, dict) or set(rows) != {'A', 'B'}:
        raise ValueError('AB requires both input bindings')
    for arm, reader, sha, size in [('A', AInput, A_SHA, A_BYTES), ('B', BInput, B_SHA, B_BYTES)]:
        row = rows[arm]
        if (not isinstance(row, dict) or row.get('sha256') != sha or row.get('bytes') != size
                or row.get('path') not in pinned):
            raise ValueError('AB input lacks exact distinct arm pin')
        if (row.get('source_frame_start'), row.get('source_frame_end'),
                row.get('capture_origin_frame'), row.get('local_timeline_start')) != (13440000, 24960000, 100003, 0):
            raise ValueError('AB predeclared window differs')
    if rows['A']['path'] == rows['B']['path']:
        raise ValueError('AB originals must be separate')
    for arm, reader in [('A', AInput), ('B', BInput)]:
        with reader(canonical(rows[arm]['path']), deadline): pass
