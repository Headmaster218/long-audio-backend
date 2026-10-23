"""Operational idle only; persisted maintenance backlog remains UNKNOWN.

Source-reviewed against audio-dev71-recovered-rev02-20261023 app.health,
NativeOwnedCells.health/_retention_health, Capture.health and MossJobs.health.
Root must recheck these paths against the final pinned candidate.
"""
import time


EXPECTED = {
    'status': 'ok', 'worker_alive': True, 'active_chunk': None,
    'fragment_cleanup_pending': None,
    'native_owned_cells.enabled': True, 'native_owned_cells.status': 'ready',
    'native_owned_cells.ready': True, 'native_owned_cells.owner_alive': True,
    'native_owned_cells.cleanup_pending': False,
    'native_owned_cells.publication_revocation_pending': False,
    'native_owned_cells.pins': 0, 'native_owned_cells.handoffs_active_or_pending': 0,
    'native_owned_cells.retention.roots.producer_active': 0,
    'native_owned_cells.retention.roots.core_flight': 0,
    'native_owned_cells.retention.roots.in_flight_operations': 0,
    'moss_jobs.enabled': False, 'moss_jobs.cleanup_pending': False,
    'moss_jobs.primary_probe_state': None, 'moss_jobs.primary_probe_crop_bytes': 0,
    'moss_jobs.speaker_probe_staging_bytes': 0,
    'moss_jobs.primary_history_recovery': 'ready',
    'moss_jobs.speaker_probe_storage_pending': False,
    'moss_jobs.speaker_probe_storage_failure': None,
    'moss_jobs.speaker_probe_cleanup_pending': False, 'moss_jobs.storage_pending': False,
}
MISSING = object()


def lookup(value, dotted):
    for part in dotted.split('.'):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def health_blockers(value):
    return sorted(path for path, expected in EXPECTED.items()
                  if type(lookup(value, path)) is not type(expected)
                  or lookup(value, path) != expected)


def sanitized(value):
    # Only booleans/integers/null and the fixed expected enum are retained.
    # Arbitrary strings (active chunk IDs, error details, transcript) are omitted.
    result = {}
    for path, expected in EXPECTED.items():
        actual = lookup(value, path)
        if actual is MISSING:
            result[path] = 'UNKNOWN'
        elif actual is None or type(actual) is bool:
            result[path] = actual
        elif type(actual) is int and abs(actual) <= 2**63:
            result[path] = actual
        elif type(expected) is str and actual == expected:
            result[path] = expected
        else:
            result[path] = 'NONMATCHING_REDACTED'
    pending = lookup(value, 'native_owned_cells.core_seam_pending')
    result['core_seam_pending'] = pending if type(pending) is bool else 'UNKNOWN'
    result['maintenance_backlog'] = 'UNKNOWN'
    result['durable_pending_zero_proven'] = False
    return result


class HealthDiagnostics:
    def __init__(self, emit, *, clock=time.monotonic):
        self.emit, self.clock = emit, clock
        self.last_snapshot = None
        self.last_valid_time = None
        self.last_blockers = None
        self.last_late = None

    def observe(self, value, *, late=False):
        # Compute a whole new pair before replacing old state; malformed nested
        # objects cannot pair a new snapshot with a previous blocker set.
        blockers, snapshot = health_blockers(value), sanitized(value)
        now = self.clock()
        changed = blockers != self.last_blockers or late != self.last_late
        self.last_snapshot, self.last_blockers = snapshot, blockers
        self.last_valid_time, self.last_late = now, late
        if changed:
            self.emit(dict(event='health_blockers', blockers=blockers, late=late,
                           observed_monotonic=now, fields=snapshot))

    def failure(self):
        now = self.clock()
        self.emit(dict(event='health_exit', current_health='UNKNOWN',
                       last_valid_monotonic=self.last_valid_time,
                       last_valid_age=None if self.last_valid_time is None else now-self.last_valid_time,
                       last_response_late=self.last_late,
                       last_fields=self.last_snapshot, last_blockers=self.last_blockers))


def wait_operational_idle(get_health, *, overall_deadline, diagnostics,
                          clock=time.monotonic, sleep=time.sleep, monitor=lambda: None):
    until = min(overall_deadline, clock()+60.0)
    try:
        while True:
            if clock() >= until:
                raise TimeoutError('final operational health deadline')
            monitor()
            # get_health must pass until to request() and diagnostics.observe as
            # its decode observer, so even late responses are retained as late.
            value = get_health(until, diagnostics.observe)
            if clock() >= until:
                raise TimeoutError('late operational health')
            if not health_blockers(value):
                return dict(operational_idle=True, maintenance_backlog='UNKNOWN',
                            durable_pending_zero_proven=False)
            sleep(min(.1, max(0, until-clock())))
    except BaseException as original:
        try:
            diagnostics.failure()
        except BaseException as secondary:
            original.add_note('Health diagnostic write failure: '+type(secondary).__name__)
        raise
