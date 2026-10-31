"""Fresh-store finite retained-record quota; not a per-operation budget or GC.

The historical default has exactly the historical schema. Explicit larger (or
smaller test) quotas are bound by an immutable singleton and exact DDL admission.
Each affected retained table has its own count ceiling, not a pooled RAM claim.
"""
DEFAULT_RECORDS = 2056
MAX_RECORDS = 131072
DEFAULT_RECEIPTS = 4096
MAX_RECEIPTS = 20000


def validate(records):
    if type(records) is not int or not 1 <= records <= MAX_RECORDS:
        raise ValueError('strict_primary_lifetime_records must be an integer from 1 through 131072')
    return records


def validate_receipts(records):
    if type(records) is not int or not 1 <= records <= MAX_RECEIPTS:
        raise ValueError('strict_primary_receipt_records must be an integer from 1 through 20000')
    return records


def schema_statements(records=DEFAULT_RECORDS, receipt_records=DEFAULT_RECEIPTS):
    records = validate(records)
    receipt_records = validate_receipts(receipt_records)
    if (records,receipt_records) == (DEFAULT_RECORDS,DEFAULT_RECEIPTS):return []
    return [
        f'''CREATE TABLE IF NOT EXISTS strict_lifetime_policy (
          singleton INTEGER PRIMARY KEY CHECK(singleton=1),
          records INTEGER NOT NULL CHECK(typeof(records)='integer' AND records={records}),
          receipt_records INTEGER NOT NULL CHECK(typeof(receipt_records)='integer' AND receipt_records={receipt_records}))''',
        '''CREATE TRIGGER IF NOT EXISTS strict_lifetime_update BEFORE UPDATE ON strict_lifetime_policy
          BEGIN SELECT RAISE(ABORT,'strict_lifetime_immutable'); END''',
        '''CREATE TRIGGER IF NOT EXISTS strict_lifetime_delete BEFORE DELETE ON strict_lifetime_policy
          BEGIN SELECT RAISE(ABORT,'strict_lifetime_retained'); END''',
    ]


def initialize(db, records=DEFAULT_RECORDS, receipt_records=DEFAULT_RECEIPTS):
    records = validate(records)
    receipt_records = validate_receipts(receipt_records)
    if (records,receipt_records) != (DEFAULT_RECORDS,DEFAULT_RECEIPTS):
        db.execute('INSERT INTO strict_lifetime_policy VALUES(1,?,?)', (records,receipt_records))


def _read_limits(db):
    # This connection's Store admission validates exact schema first. Standalone
    # legacy connections retain their original default, never infer a larger cap.
    present = db.execute("SELECT 1 FROM sqlite_master WHERE name='strict_lifetime_policy' COLLATE BINARY AND type='table'").fetchone()
    if present is None:return DEFAULT_RECORDS,DEFAULT_RECEIPTS
    rows = db.execute('SELECT singleton,records,receipt_records FROM strict_lifetime_policy LIMIT 2').fetchall()
    if len(rows) != 1 or rows[0][0] != 1:
        raise ValueError('strict_lifetime_policy_missing')
    return validate(rows[0][1]), validate_receipts(rows[0][2])


def read_limit(db):
    return _read_limits(db)[0]


def read_receipt_limit(db):
    return _read_limits(db)[1]


def admit(db, records=DEFAULT_RECORDS, receipt_records=DEFAULT_RECEIPTS):
    if _read_limits(db) != (validate(records),validate_receipts(receipt_records)):
        raise ValueError('strict_lifetime_policy_mismatch')
