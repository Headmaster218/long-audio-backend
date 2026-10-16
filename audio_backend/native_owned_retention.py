"""Read-only retention accounting, never authority to retire stored evidence.

Live producers do not yet expose a proved no-future-use frontier or complete
root closure. Conditional fixture eligibility must not be used for live GC.
"""
from .native_owned_cells import Unavailable


def classify_row(kind, *, referenced=False, pending=False, in_flight=False,
                 roots_complete=False, behind_frontier=False):
    """Classify one already-inventoried row using explicit, proved root facts.

    Both sides of proofs, retained results, outboxes and producer/replay ranges
    must be included before a caller can assert roots_complete. A settled state,
    released pin or successful primary publication does not establish it.
    """
    if (kind not in ('cells', 'sources', 'compacts') or any(type(value) is not bool
            for value in (referenced, pending, in_flight, roots_complete, behind_frontier))):
        raise Unavailable('retention_classification_input')
    if kind == 'cells' or referenced or pending or in_flight:return 'referenced'
    if roots_complete and behind_frontier:return 'eligible_orphan'
    return 'unknown_protected'


def row_counts(kind, occupied, limit, referenced=None, pending=None):
    """Fixed-size, conservative live projection; no frontier is asserted."""
    if kind not in ('cells', 'sources', 'compacts'):raise Unavailable('retention_count_kind')
    if type(limit) is not int or limit < 1:raise Unavailable('retention_count_limit')
    if occupied is not None:
        if (type(occupied) is not int or not 0 <= occupied <= limit
                or type(referenced) is not int or not 0 <= referenced <= occupied
                or type(pending) is not int or not 0 <= pending <= occupied):
            raise Unavailable('retention_count_capacity')
        if kind == 'cells':referenced = occupied
    return {'occupied': occupied, 'limit': limit, 'referenced': referenced,
            'referenced_is_lower_bound': kind != 'cells', 'eligible_orphan': 0,
            'unknown_protected': None if occupied is None else occupied - referenced,
            'pending': pending}
