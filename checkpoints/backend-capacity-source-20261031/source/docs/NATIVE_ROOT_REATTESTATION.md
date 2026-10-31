# Explicit bounded-native root re-attestation (dev66)

SOURCE PROPOSAL ONLY. This document is not a passing qualification, a new runtime
qualification, an attestation for original data, or permission to start a server.
The supervising root must review and run finite synthetic controls, preserve the
failed historical evidence, review packaging, and separately gate any real use.

## Why this is admission, not repair

The native retention key is SHA256 of the canonical complete stored six-field
`store_meta` tuple. Changing even its first ancestor would change authority for
retention, native commits, recording batches and reclamation. Never rewrite this
tuple, rebuild/delete the native database, or regenerate receipts to recover a
root-only mismatch.

This default-off amendment admits one exact old tuple against one exact current
root-device tuple. It preserves the old tuple and its key byte for byte. It does
not change the schema or runtime-qualification registry. A no-flag startup keeps
the preexisting strict mismatch refusal and normal new-store behavior.

## Explicit interface

The launcher requires both:

- `--native-root-reattestation /absolute/canonical/reviewed.json`
- `--native-root-reattestation-sha256 <exact SHA256 of file bytes>`

It also requires the existing `--instance-lock-fd`, authenticated as an inherited
read-only exclusive flock before admission. The bounded-native cells/core-seams
configuration must already be valid. There is no environment override, discovery,
artifact generator, fallback, generic skip-root, or root wildcard.

`create_app` has corresponding `native_root_reattestation` and
`native_root_reattestation_sha256` arguments. It consumes the inherited fd under
the existing dev65 contract, completes read-only admission before `_create_app`
and before any directory creation or primary `Store` initialization, and passes
one immutable admission explicitly through native registration to the owner.
Native registry reuse requires the identical admission and lock objects, not an
unverified approximation. Failed construction closes only the child duplicate;
the supervisor retains its own hold through positive child wait/cleanup.

The supervisor's independent uploader ownership, outcome conservation and
positive stop/reap evidence remain runner responsibilities. This API does not
claim that checking an instance flock proves the uploader lock or process exits.

## Artifact contract

The artifact is canonical ASCII JSON, exactly the serialization used by
`native_owned_cells.canonical`, without a trailing newline, at most 32 KiB, with
exactly these keys:

- `schema`: `native-root-reattestation-v1`.
- `data_path`, `primary_path`, `native_path`, `lock_path`: canonical absolute
  original logical paths. The latter three must be, respectively, `jobs.sqlite3`,
  `native-owned-cells-bounded-v1/cells.sqlite3`, and `instance.lock` beneath the
  original data path.
- `lock_identity`: exact instance-lock `[device, inode]`.
- `old_binding`: the exact six strings in stored SELECT order: schema, policy,
  primary_identity, qualification, namespace_identity, database_identity.
- `old_binding_sha256`: SHA256 of the canonical whole `old_binding` array. This
  is also the existing durable retention-store key, not a newly minted key.
- `new_root`: one exact `[device, inode]`. Its device must differ from the stored
  first ancestor, while its inode must be identical.
- `native_schema_sha256`: exact SHA256 of canonical rows selected as
  `type,name,tbl_name,sql` from non-internal sqlite_master entries ordered by
  `type,name`. The admitted schema must also equal the schema independently
  created from the current shipped `_schema` implementation in memory.
- `baseline`: exactly `path` and `sha256` for the immutable stopped-baseline JSON
  receipt. This exact-byte hash is the provenance pin, not an unverified label.

The separately reviewed release/launch manifest must pin the artifact hash and
source/package provenance. Product code does not issue or silently refresh an
artifact, or treat an operator-selected file as evidence of independent review.

The baseline is bounded to 2 MiB, must record complete unchanged observations,
integer `exclusive_locks_acquired: 2`, and `both_existing_flocks_held`. Its exact
stored six-field mapping, instance identity, and all recorded original logical
ancestor/namespace/primary paths and identities must agree with the artifact.
The baseline collector's observation is not retroactively represented as the
failed historical server's startup observation.

## Read-only admission and exact exception

Every ancestor is opened dirfd-relative with O_NOFOLLOW/O_DIRECTORY, including
`/`, checked for directory type, root/current UID and safe mode, and compared to
the held descriptor. The data directory and native namespace must be owned by
the current UID; the native namespace must be private. Artifact, baseline,
instance lock, primary database and native database must be owned single-link
regular files with no group/other write. File reads are bounded and reject
observed content/metadata/path replacement. Exact artifact and baseline file
identities are rechecked by the native owner.

Before any native disk connection, admission rejects a missing store, any
sidecar or unknown native entry, excessive logical or allocated bytes, unsafe
namespace/ancestors, or unqualified runtime. Runtime verification remains the
existing in-memory profile check. The existing native database is read with
`mode=ro&immutable=1`, query_only and trusted_schema OFF, with finite row/string,
progress and file limits. No rollback recovery, sidecar creation, schema change
or native write occurs in this preflight. Immutable reads are permitted only
under authenticated ownership with no journal/WAL/SHM evidence.

The owner recomputes its current six-field binding normally. The admitted stored
tuple must match every old string exactly. All five nonnamespace fields must
match current values exactly. Namespace inode/device and every suffix ancestor
must match exactly. The current first ancestor must equal the one attested new
root; the sole changed coordinate is the root device. Actual SQL schema,
supported bounded policy, loaded runtime qualification and logical DB identities
remain mandatory.

The exception is applied only at the existing stored-binding comparison.
`_ancestor_identity` and `_namespace_identity` retain the complete actual current
observations. Every normal within-run fence, receipt/source authority check,
capacity limit and retention/GC condition remains active. The final SELECT is
hashed unchanged, and a second check ensures its hash remains the attested old
binding hash before exposing the retention key.

Diagnostics contain only bounded artifact/baseline hashes, old/current whole
binding hashes and `root_device_only` classification. No raw path, source data,
SQL body or unobserved historical tuple is logged.

## Repeated startup and limitations

The same exact attestation may be reused across finite generations with the same
root, logical paths, shared suffix/namespace/DB/lock identities, stored tuple,
supported schema/policy and runtime. Legitimate source/cell rows may evolve;
permanent DB-content hashes are deliberately not required. Baseline bytes and
artifact bytes do not evolve. Changed root or anchors require new separately
reviewed evidence, never automatic widening. Attested startup does not authorize
an unresolved native rollback journal; existing ordinary recovery policy is not
broadened.

These are finite cooperative ownership and observed pathname fences. They do
not claim an atomic SQLite dirfd open, protection against a hostile same-UID
actor racing every check, universal filesystem guarantees, new durability or
model/acoustic qualification. The ten-second read deadline and existing owner
budgets are cooperative, not hard upper bounds on an individual OS call.

## Finite synthetic gate

New source controls exercise two direct owner-open cycles with populated receipt,
cell, dependency and proof rows, DB bytes, store_meta and retention keys unchanged;
a four-root bounded proof and positive/negative GC key fences are also preserved.
A separate finite control executes actual CLI-to-factory-to-native-owner-thread
plumbing across two application lifespans with only inference/listener startup
disabled. These are not two OS-process generations. Controls also exercise no-flag refusal; ordinary
new-store behavior; all five nonnamespace fields; root inode/suffix/namespace
changes; wrong hash/path/policy/qualification/schema/baseline provenance;
symlinks, hardlinks, unsafe modes/UID/types; artifact replacement/read races;
sidecar/capacity/parser/deadline bounds; real inherited-lock requirements;
pre-Store ordering and close-only failure cleanup; registry admission isolation;
within-run root/suffix drift; and an actual receipt-authority rejection after
admission. Test fixtures are synthetic and use a simulated runtime qualification.
They create no listener, run no model and touch no original data. Existing
inherited-lock, launch-order, CLI and supervisor ownership controls accompany the
new tests in the separate root-only proposal. No test has been run by this worker.
