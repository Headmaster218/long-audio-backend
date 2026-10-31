"""Bounded fresh-connection WAL admission, before any business transaction.

One native SQLite read/wait, never a Python transaction or mutation replay.
The caller still owns identity checks, its original budget, and positive close.
"""
import math
import sqlite3
import time

MAX_INITIALIZATION_SECONDS = 1.


def budget_remaining_seconds(budget):
    method = getattr(budget,'remaining_seconds',None)
    if callable(method):return method()
    deadline = getattr(budget,'deadline',None)
    clock = getattr(budget,'clock',time.monotonic)
    if type(deadline) not in (int,float) or not math.isfinite(deadline) or not callable(clock):return 0.
    budget.check()
    return max(0.,deadline-clock())


def prime_connection(db, *, check, remaining_seconds, charge=None, restore_ms=0):
    """Finish one schema-page read with a clipped native busy handler.

    No open read transaction survives this function, so the original caller's
    BEGIN/first business read remains its visibility/linearization point.
    """
    if type(restore_ms) is not int or not 0<=restore_ms<=15000:raise ValueError('sqlite_restore_timeout')
    if db.in_transaction:raise sqlite3.OperationalError('sqlite_admission_inside_transaction')
    check()
    cursor=None;original=None
    try:
        if charge is not None and charge():
            check()
            raise sqlite3.OperationalError('sqlite_admission_budget_refused')
        check()
        remaining=remaining_seconds()
        if type(remaining) not in (int,float) or not math.isfinite(remaining) or remaining<0:
            raise ValueError('sqlite_admission_remaining')
        milliseconds=max(0,int(min(MAX_INITIALIZATION_SECONDS,remaining)*1000))
        db.execute('PRAGMA busy_timeout='+str(milliseconds))
        cursor=db.execute('SELECT rootpage FROM sqlite_schema LIMIT 1')
        cursor.fetchall()  # Fully exhaust and close; never retain a business snapshot.
        closing=cursor;cursor=None;closing.close()
        if db.in_transaction:raise sqlite3.OperationalError('sqlite_admission_unexpected_transaction')
        check()
    except BaseException as error:
        original=error;raise
    finally:
        try:
            try:
                if cursor is not None:
                    closing=cursor;cursor=None;closing.close()
            finally:db.execute('PRAGMA busy_timeout='+str(restore_ms))
        except BaseException as cleanup:
            if original is None:raise
            original.sqlite_admission_cleanup_error=cleanup
            original.add_note('SQLite admission cleanup also failed; caller must positively close the connection')
    check()
