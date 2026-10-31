"""Best-effort, bounded in-memory shutdown decision evidence; no cleanup calls."""
ERROR_CLASSES=frozenset(('RuntimeError','OSError','ValueError','ReadError','Unavailable',
    'TimeoutError','OperationalError','PermissionError','MemoryError','AssertionError',
    'TypeError','AttributeError','KeyError','ProcessError','KeyboardInterrupt','SystemExit'))

def _error_class(value):
    if value is None:return None
    name=type(value).__name__
    return name if name in ERROR_CLASSES else 'other'

def record_shutdown_decision(state,*,worker_stopped,jobs_stopped,cells_stopped,
                             fragment_cleanup_error,initializer_safe):
    """Only caller's already available locals/exception objects; no health/stop.

    Failure to observe or store diagnostics must not alter lifecycle semantics.
    Non-bool return values are UNKNOWN, not coerced into approval.
    """
    try:
        state.shutdown_decision_diagnostics=None
        flags={'worker_stopped':worker_stopped,'jobs_stopped':jobs_stopped,
            'cells_stopped':cells_stopped,'fragment_cleanup_clear':fragment_cleanup_error is None,
            'initializer_safe':initializer_safe}
        if any(type(value) is not bool for value in flags.values()):return
        exceptions={name:_error_class(getattr(state,attribute,None)) for name,attribute in (
            ('worker','worker_shutdown_error'),('jobs','moss_jobs_shutdown_error'),
            ('cells','native_owned_cells_shutdown_error'))}
        exceptions['fragment']=_error_class(fragment_cleanup_error)
        state.shutdown_decision_diagnostics={'schema':1,'observation':'OBSERVED',
            'predicates':flags,'exception_classes':exceptions}
    except BaseException:
        pass  # No terminal status, lock release, retry or recovery side effect.
