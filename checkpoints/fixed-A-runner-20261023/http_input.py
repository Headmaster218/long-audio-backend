"""Bounded read transport; journal never includes URL, identifiers or body."""
import http.client
import json
import time


class ReadDeadline(RuntimeError):
    pass


class RequestLedger:
    ROUTES = frozenset(('health', 'observer_health', 'create_recording',
                        'create_source', 'source_bytes', 'seal_page', 'page_status'))

    def __init__(self, stream):
        self.stream, self.count, self.bytes = stream, 0, 0

    def reserve_attempt(self):
        # Refuse before sending (especially before a mutation) if no bounded
        # timing row can be retained. Fixed categories and numeric fields only.
        if self.count >= 4096 or self.bytes + 1024 > 8 * 1024**2:
            raise RuntimeError('request ledger envelope exhausted before request')

    def emit(self, *, route, method, attempt, started, ended, outcome, status=None):
        if route not in self.ROUTES or method not in ('GET', 'POST', 'PUT'):
            raise ValueError('unrecognized fixed request category')
        # Deliberately do not serialize exception text or request/response bodies.
        row = dict(route=route, method=method, attempt=attempt, started=started,
                   ended=ended, elapsed=ended-started, outcome=outcome, status=status)
        data = json.dumps(row, sort_keys=True, allow_nan=False)+'\n'
        size = len(data.encode('utf-8'))
        if self.count >= 4096 or self.bytes + size > 8 * 1024**2:
            raise RuntimeError('request ledger envelope exceeded')
        self.stream.write(data)
        self.stream.flush()
        self.count += 1
        self.bytes += size


def request(method, route, operation, *, deadline, ledger, clock=time.monotonic,
            sleep=time.sleep, observe=None):
    """operation(timeout) owns socket cleanup/body bound and returns (status,value).

    No mutation retry. HTTP status errors and JSON failures are not transport
    retries. Socket timeouts are per blocking operation, not total-wall limits.
    observe sees a decoded response before the post-call deadline check, and
    receives late=True when it cannot be accepted as timely evidence.
    """
    if method not in ('GET', 'POST', 'PUT') or route not in RequestLedger.ROUTES:
        raise ValueError('unsupported request')
    attempts = 3 if method == 'GET' else 1
    for attempt in range(1, attempts+1):
        started = clock()
        if started >= deadline:
            raise ReadDeadline('request deadline before attempt')
        ledger.reserve_attempt()
        try:
            socket_cap = 2.0 if route in ('health', 'observer_health') else 10.0
            status, value = operation(min(socket_cap, deadline-started))
        except (TimeoutError, ConnectionError, http.client.RemoteDisconnected,
                http.client.IncompleteRead) as exc:
            ended = clock()
            ledger.emit(route=route, method=method, attempt=attempt, started=started,
                        ended=ended, outcome=('transport_failure' if method == 'GET'
                                              else 'mutation_outcome_uncertain'))
            if method != 'GET' or attempt == attempts:
                raise
            delay = float(attempt)
            if ended + delay >= deadline:
                raise ReadDeadline('retry backoff exceeds deadline') from exc
            sleep(delay)
            continue
        except Exception:
            ledger.emit(route=route, method=method, attempt=attempt, started=started,
                        ended=clock(), outcome='nonretryable_failure')
            raise
        ended = clock()
        ledger.emit(route=route, method=method, attempt=attempt, started=started,
                    ended=ended, outcome='response', status=status)
        if observe is not None:
            observe(value, late=ended >= deadline)
        if clock() >= deadline:
            raise ReadDeadline('response arrived after deadline')
        if not 200 <= status < 300:
            raise RuntimeError('non-success HTTP status')
        return value
    raise AssertionError('unreachable')


def require_observer_off(value):
    expected = dict(enabled=False, healthy=None, trial_observer_mode='NOT_ENABLED',
                    census_status='NO_CENSUS', complete_sampling_denominator=False)
    if not isinstance(value, dict):
        raise RuntimeError('observer absence UNKNOWN')
    if any(key not in value or type(value[key]) is not type(wanted) or value[key] != wanted
           for key, wanted in expected.items()):
        raise RuntimeError('observer absence not established')
    return value
