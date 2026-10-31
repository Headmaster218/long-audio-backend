"""Finite pre-transaction admission retries; never replays primary SQL effects."""
import logging
import sqlite3
import threading
import time

_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_COUNT = 0
_LIMIT = 128
_DELAYS = (.01, .02, .04)


def diagnostic(stage, exc, *, attempt=0, in_transaction=None, outcome=None):
    """Bounded metadata only: no messages, SQL, identifiers, paths or results."""
    global _COUNT
    if not _LOCK.acquire(blocking=False): return
    try:
        if _COUNT >= _LIMIT: return
        _COUNT += 1
    finally: _LOCK.release()
    code = getattr(exc, 'sqlite_errorcode', None)
    if type(code) is not int or not 0 <= code < 2**31: code = None
    if stage not in ('begin_refused', 'begin_acquired_error', 'connect', 'body', 'commit'):
        stage = 'other'
    if outcome not in ('not_started', 'rolled_back', 'commit_attempted', 'committed', 'unknown', None):
        outcome = 'other'
    try:
        _LOG.warning('primary_sql_failure stage=%s sqlite_code=%s attempt=%d in_transaction=%s outcome=%s',
                     stage, code, attempt, in_transaction, outcome)
    except Exception: pass


def begin(db, budget):
    """Called inside the original cumulative notice_phase, never a fresh budget.

    Only bounded handoffs opt in. A transaction acquired before a driver error,
    unknown errors, or any failure after this function returns are not replayed.
    """
    for attempt in range(4):
        budget.check()
        try:
            db.execute('BEGIN IMMEDIATE')
            return
        except sqlite3.OperationalError as exc:
            try: acquired = db.in_transaction
            except Exception: raise exc
            try:
                diagnostic('begin_acquired_error' if acquired else 'begin_refused', exc,
                           attempt=attempt+1, in_transaction=acquired,
                           outcome=getattr(budget, 'sql_outcome', None))
            except Exception: pass
            code = getattr(exc, 'sqlite_errorcode', None)
            if acquired or type(code) is not int or not 0 <= code < 2**31 or (code & 0xff) != sqlite3.SQLITE_BUSY or attempt == 3:
                raise
            budget.check()
            # Sleeping is inside notice_phase and consumes its original elapsed
            # and absolute-deadline allowance; check again before every attempt.
            time.sleep(_DELAYS[attempt])
            budget.check()
