# Narrow atomic initialization correction; stop qualification here

This is a second, unapplied isolated candidate patch. No working application,
accepted native candidate, existing store or deployment is changed.

## New gap versus pre-existing behavior

The first idempotency prototype initialized its feature tables in a second
transaction after strict schema creation committed. This introduced a new state:
complete strict schema without the required opt-in feature namespace. A process
kill in that gap caused the feature's fresh-store guard to refuse reopening.

The minimal correction moves `session_requests.initialize(db)` directly after
`strict_recording.init_schema` inside the existing `BEGIN IMMEDIATE` transaction.
It removes the extra connection and second BEGIN. Independent source review found
no implicit COMMIT/executescript in the intervening schema helpers. Real SQLite
trace verifies the same connection, active transaction, no intervening commit,
and one commit for strict plus feature namespaces. Injected failures before and
after feature creation roll both namespaces back.

This is deliberately NOT whole-Store recovery. The original Store already commits
base tables via executescript/stream/overlap setup and an explicit commit before
its strict-schema transaction. A crash before that final transaction commits
therefore leaves earlier base tables. Existing strict admission correctly refuses
these base-only stores as `strict_archive_fresh_only`; no automatic adoption,
repair, deletion, migration or gate weakening is introduced.

## Bounded process proof

The reduced probe stopped a real child immediately before feature initialization
and another immediately after feature DDL but before transaction commit. In each
case it asserted an active transaction, the supervisor observed SIGSTOP, killed
and reaped the process, preserved original database/sidecars, and reopened only a
copy. Both reopened copies refused strict adoption as expected. This is safe
refusal, not successful recovery. No models/native lifespan or runtime activation
ran; the allocation workload was not repeated because DDL is unchanged.

All 25 candidate tests passed, independently rerun: 22 prior idempotency tests
plus three transaction/fault cases. The two-checkpoint probe exited 0 with two
acknowledged killed children and two expected-refusal recovery exits. Result
SHA-256 `a9b15e6e612e55cec9779f6654401e8d7dace29b6b308961137b4267b420deea`.
No midcommit, power-loss, media workload or whole-store initialization guarantee
is claimed. Generated databases and raw evidence remain local.

## Reproduce without activation

Copy the source to a fresh directory, apply `isolated_session_requests_20261103.patch`
with `patch -p1`, then `session_request_atomic_init_20261103.patch` with `patch -p1`.
Verify resulting files against `SESSION_REQUEST_ATOMIC_INIT_BASIS_20261103.json`.
Run the explicit `test_session_requests_candidate.py` and
`test_session_request_atomic_init.py` under `qualification`. The normal source tree
intentionally does not contain the activated feature module or setting.

## Application value and next step

Atomic request-key creation helps a future automatic intake service avoid duplicate
sessions after a lost response. It does not authenticate a server or recorder,
choose the right session, make interrupted inference retry-safe, or turn transport
metadata into trusted capture provenance. Whole-Store bootstrap recovery would
expand into a broader initialization/migration design with limited immediate value.

Stop this qualification detour after the narrow proof. The practical next step is
an explicit command that imports a verified delivery into a user-selected existing
session using the already tested uploader. Preserve its local retry journal and
originals, show pending/failed/unresolved states clearly, and require deliberate
operator action where server state is uncertain. Do not advertise unattended
intake, create replacement sessions, hide partial results, or enable the new
idempotency prototype. This makes the existing successful FTPS/snapshot/upload/
publication chain usable without depending on a new bootstrap registry.

Independent saved-evidence audit accepted the narrow proof and confirmed both
namespaces absent after both kills. Earlier base tables were recovered from their
committed WAL frames, so the audit used WAL-aware read-only copies, not immutable
mode that could ignore sidecars. Original artifacts and source pins remained
unchanged. Audit report SHA-256:
`99ccfc38155e9d0f5074b1ca5fa932b975bb04cf426b70ba33b8131a8acc8804`.
Only `independent_atomic_init_audit_20261103.py` is backed up; its local audit
folder also contains database copies and must not be uploaded. As with earlier
historical audits, its paths and generated outputs are not a portable test to run
inside a manifest-verified source snapshot.
