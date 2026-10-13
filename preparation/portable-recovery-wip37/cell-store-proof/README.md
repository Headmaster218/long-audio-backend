# Portable CellStore allocation and process-crash recipe

## Unexecuted source checkpoint

This is a newly reconstructed public recipe, not recovered historical driver
bytes and not a claim that this recipe has passed. Preparation used only source
inspection, AST/JSON parsing, byte/hash inventories, and privacy checks. No
product import, SQLite connection, driver/test execution, subprocess regression,
provider construction, model execution, or service run was performed.

The source packet needs independent review and explicit execution authorization.
Do not treat an AST parse, a generated config, a matching interpreter hash, or a
historical qualification digest as a new proof result. WIP4's prove_store.py
tests the primary Store; it is not this CellStore proof.

### Exact public inventory

| File | Maximum UTF-8 bytes |
| --- | ---: |
| README.md | 12,288 |
| qualify_cell_store.py | 65,536 |
| config.schema.json | 12,288 |
| config.template.json | 4,096 |
| test_qualify_cell_store.py | 32,768 |

Five files, at most 126,976 bytes (124 KiB). Generated local configs, databases,
journals, runtime/package binaries, raw historical receipts, source audio,
private workspace paths, and source-recovery maps do not belong in this packet.

## Immutable product and qualification boundary

The target is product commit
`1fc066910ca78b2e57d7a28f79b91cafc06f5dd5`. The driver embeds exact bytes/hash
pins for its five-file pure source closure and structural pins for the store's
schema, pragmas/open, transaction, mutation, and namespace-check functions.
It reads those sources as data, never imports the product, and never edits them.
The seven schema statements are extracted using ast.literal_eval from the
pinned source. The exact pinned pragma tuple is compared with the source.

The driver never constructs RuntimeQualification, injects a simulated profile,
changes QUALIFIED_RUNTIMES, changes production flags, or offers a qualification
override. Both configure and every actual run compare the current observation
with the one shipped record. A mismatch must return
`REGISTRY_REVIEW_REQUIRED` before creating proof directories or opening disk
SQLite. A future candidate-runtime mode would need separate source and execution
review; it does not exist here.

Matching requires the configured actual interpreter content, byte size and
resolved path hash, the running /proc/self/exe container, exact bounded sqlite3
Python/bytecode inventory and digest, built-in _sqlite3 spec/loader/no-file form,
SQLite version/source ID and sorted compile-options digest, Linux/unix, and the
current filesystem device. No separate libsqlite3 mapping may be observed.
Mapping absence is not proof of static linking. A byte-identical relocated
interpreter is insufficient; neither device 27 nor the historical accepted path
digest is a general filesystem guarantee. The current runtime must be measured.

The local config records the complete observed compile-options list and package
inventory. It is not a production registry entry. The shipped historical
evidence digest remains a reference; fresh outputs have separate source/config
hashes and never relabel historical evidence.

## Prerequisites and local configuration

This recipe is Linux-only and stdlib-only, with the exact built-in SQLite
binding accepted by the product. It downloads and installs nothing. Supply a
previously reviewed interpreter and the complete immutable product checkout.
The Python command must use -I -S -B: isolated, no site import, and no new pyc.
Existing sqlite3 pyc files still belong to the measured closure.

Choose an existing owned work parent on the accepted filesystem. Every ancestor
must be a real no-follow directory, root- or current-user-owned, with no group
or other write bit. The work parent must be current-user-owned. There is no
sticky-/tmp exception. The recipe does not repair permissions or relocate a
runtime. Its per-run children are private 0700 directories. Avoid untrusted
same-UID concurrent mutation; that race is outside the claim.

Set these variables yourself; none of these illustrative strings is runnable:

```sh
PYTHON=/CHOOSE_REVIEWED_ABSOLUTE_INTERPRETER
PRODUCT=/CHOOSE_IMMUTABLE_PRODUCT_ROOT
RECIPE=/CHOOSE_THIS_SOURCE_PACKET
WORK_PARENT=/CHOOSE_OWNED_SECURE_PARENT
CONFIG=/CHOOSE_OWNED_SECURE_PARENT/local-cellstore-config.json
RUN_NAME=cellstore-proof-unique-name
```

After configuration execution has been authorized:

```sh
"$PYTHON" -I -S -B "$RECIPE/qualify_cell_store.py" configure \
  --product-root "$PRODUCT" --work-parent "$WORK_PARENT" \
  --run-name "$RUN_NAME" --config-out "$CONFIG"
```

This performs source checks and a bounded memory-only SQLite identity query. It
creates the requested config only if the shipped record matches. It refuses to
overwrite a file. Review the entire config against config.schema.json, the
selected paths, the exact source packet hashes, and the fixed resource limits.
config.template.json is a non-runnable field guide; never replace its
placeholders with guessed historic values. Use configure to generate actual
observations. Keep the local config private; it contains chosen local paths.

After reviewing that config, the separately authorized no-disk-SQLite preflight
is:

```sh
"$PYTHON" -I -S -B "$RECIPE/qualify_cell_store.py" preflight --config "$CONFIG"
```

Configure/preflight success is not allocation, crash, or retention evidence.
Changes to a recipe file, the product closure, interpreter, package inventory,
runtime binding, or selected filesystem require a newly reviewed config.

## Finite physical run, separately authorized

Read-only fd 0 must already be Linux /dev/null. Shell redirection below opens
it read-only. The controller validates the inherited character device and
/proc/self/fd/0, then passes stdin=0 to its one retained Popen. It does not use
subprocess.DEVNULL and does not add an O_RDWR /dev/null open.

```sh
"$PYTHON" -I -S -B "$RECIPE/qualify_cell_store.py" run --config "$CONFIG" < /dev/null
```

Use a new run name for every separately authorized attempt. Existing roots are
never reused, overwritten, cleaned, or retried automatically. Keep failed and
incomplete attempts; do not select only successful observations for a record.

### Three charged families and bounded records

Under the new run root:

- fill/: closed near-limit pretransaction database and its hash/logical oracle
- crash/: initially an exact baseline copy, then the sole original writer's
  interrupted database/journal; preserved only after positive kill and reap
- recovery/: copied after reap; the only interrupted copy opened for normal
  SQLite rollback recovery
- records/: local config, bounded event log, and final result

There is no fourth database copy. Each family is capped at 64 MiB; each must
also satisfy the tighter 24 MiB main DB + 32 MiB journal + 2 MiB allocated slack
= 58 MiB envelope. Three 64 MiB reservations plus 1 MiB for records/control give
193 MiB total. Charges are max(logical size, st_blocks times 512), including
directories. WAL and SHM are charged before rejection. No uncertain journal is
manually removed. Original/control directory allocations remain charged.

The exact settings are 4,096-byte pages; max_page_count 6,144; DELETE;
synchronous FULL; cache_spill OFF; cache_size -4096; memory temporaries;
busy_timeout 0; mmap_size 0; foreign_keys ON; trusted_schema OFF; auto_vacuum 0.
cache_size and journal_size_limit alone do not prove the journal envelope.

The controller owns 300 seconds overall, with a 240-second work cutoff and up
to 60 seconds for cleanup within that overall window. The original writer has
240-second CPU/alarm limits, 512 MiB address-space ceiling, 32 MiB per-file
limit, 64 open descriptors, no core dump, 5,000,000 SQL VM steps, bounded fill
attempts, reads, projections, poll count, event count, and output sizes. These
are cooperative wall-clock checks plus finite OS limits, not hard deadlines
for individual blocking syscalls and not a measured RSS guarantee.

### What must actually be observed

1. The sole child creates the exact schema and applies/readbacks exact pragmas.
   Deterministic SQL-schema-valid conservative payloads fill to 6,143–6,144
   actual pages. Actual SQLITE_FULL must occur, with rolled-back row state,
   integrity/FK checks, and retained page/allocation observations.
2. Close the fill DB; retain its exact complete file digest and complete typed,
   length-prefixed logical-row oracle, including metadata and sqlite_sequence.
3. Copy that closed DB to crash/. The same original child opens that copy and
   stages a bounded same-schema overwrite. These stress rows and writes do not
   claim to be authentic Cell/Receipt producer output or a production publish.
4. During ordinary COMMIT, observe a real finite nonzero rollback-journal header
   and changed database page 1. Kill the retained original Popen and positively
   reap SIGKILL before treating crash/ as preserved evidence or making recovery/.
   No artificial journal, VFS delay, replacement writer, or reconstructed PID
   is used. A missed window is INCOMPLETE, even if fill succeeded.
5. Audit exactly one header, finite nonzero nRec, page/sector geometry, exact
   extent, unique bounded page numbers, every SQLite rollback-record checksum,
   and each before-image against the preserved fill DB. Require a changed
   interrupted DB, so unchanged bytes are not called restored data.
6. Copy the interrupted pair after reap. Ordinary SQLite/unix recovery of only
   recovery/ must remove its journal itself, pass integrity/FK checks, and
   restore the exact baseline DB bytes and complete oracle. Rehash crash/ to
   prove the preserved original was not opened or altered by recovery.
7. Recheck runtime/source/config bindings, namespace permissions/identities and
   ancestry, allocation/accounting, and positive original-handle cleanup.

CORROBORATED_MATCH means only fresh corroboration of this one shipped profile
for this finite synthetic storage/process-crash case. It does not change the
registry. INCOMPLETE, FAILED, and UNREAPED are not qualification. A valid header
and configured synchronous FULL do not independently establish fsync completion
or power-loss durability. Observed allocation maxima are sampled high-water
observations, not universal filesystem quotas or hostile same-UID race safety.

## Source/helper tests and separate final gates

test_qualify_cell_store.py is a finite helper/regression test source. It contains
no product import, SQLite connection, actual subprocess launch, or native/model
call. It checks JSON/config boundaries, checksums/header cases, charge math,
source-level execution ordering, original-handle behavior with fakes, and the
inherited-fd0 regression. Preparation did not execute it. After independent
source review and explicit helper-test authorization:

```sh
"$PYTHON" -I -S -B "$RECIPE/test_qualify_cell_store.py"
```

Passing helper tests is not a physical SQLite proof. The physical run needs its
own exact execution review. Passing the physical run still does not execute the
product's tiny real-profile enabled-retention gates. Those remain separately
authorized selections in tests/test_native_owned_cell_store.py:

- AdmissionTests.test_off_absent_and_enabled_unqualified_do_not_create_sql_or_thread
- AdmissionTests.test_seam_synthetic_real_runtime_a_profile_restart_dispatch_and_off

They require the real shipped profile, secure temporary roots, the pinned test
source closure, tiny v1/v2 publish/read/restart/dispatch checks, explicit OFF and
rejection behavior, and clean owner/pin/instance-lock closure. Do not substitute
the broad simulated fixture suite or its zero-header accounting case. Do not
launch those existing tests through this driver; their exact harness, limits,
source closure and execution are a separate review. No model/provider is needed.

## Review and reporting

Freeze all five files before configuration or tests. Check byte caps, parse
ASTs/JSON without imports, compare product pins, and review privacy and runtime
paths. This is unexecuted WIP pending independent review and execution gates.
Retain fresh local results privately. Publishing source does not authorize
execution or qualify the runtime.
