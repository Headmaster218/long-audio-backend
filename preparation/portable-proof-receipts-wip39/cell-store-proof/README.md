# Portable CellStore allocation and process-crash recipe

## Unexecuted source revision v3

This reconstructed recipe has not run or passed. Preparation used only source
inspection, AST/JSON parsing, hashes and privacy checks. No product import,
SQLite connection, tests, children, providers, models or services ran.

Independent source review and execution authorization remain required. AST
parsing, configs and historical hashes are not fresh proof. WIP4 prove_store.py
tests primary Store, not CellStore. Original WIP37 and v2 remain preserved.
Only driver/tests/README change; schema/template remain byte-identical.

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

Target product commit: `1fc066910ca78b2e57d7a28f79b91cafc06f5dd5`. Exact
five-file closure and schema/open/mutation/fence AST pins remain fixed. Product
sources are read as data, never imported or edited. Seven schema statements are
literal-extracted; the exact pragma tuple is checked against pinned source.

The driver never constructs RuntimeQualification, injects a simulated profile,
changes QUALIFIED_RUNTIMES, changes production flags, or offers a qualification
override. Both configure and every actual run compare the current observation
with the one shipped record. A mismatch must return
`REGISTRY_REVIEW_REQUIRED` before creating proof directories or opening disk
SQLite. A future candidate-runtime mode would need separate source and execution
review; it does not exist here.

Matching binds actual interpreter bytes/size/realpath, /proc/self/exe,
sqlite3 Python/bytecode closure, built-in _sqlite3 spec/loader/no-file form,
SQLite version/source ID/options digest, Linux/unix and filesystem device.
No separate libsqlite3 mapping is allowed; absence is not static-link proof.
Relocation is not admitted by equal bytes. Device 27 is no general quota guarantee.

The local config records full compile options and package inventory, not a new
registry entry. Fresh hashes never relabel historical evidence.

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

V2 retains root/family/records descriptors, compares their inode identities and
fresh no-follow ancestry at disk-open, copy, capture, seal and recovery boundaries,
and binds the worker to the controller's initial directory token before disk
open. Structured worker refusals preserve phase/status; a late exit re-drain
distinguishes preflight rejection from an unfinished physical proof. V3 covers
setup with initialized-descriptor cleanup and preserves primary setup errors,
recording secondary cleanup errors separately. Failed setup does not publish.

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

Configure checks source and memory-only SQLite identity, then exclusively creates
the config only on a shipped-record match. Review paths, hashes and fixed limits
against config.schema.json. The template is non-runnable: never guess its values.
Keep generated configs private because they contain local paths.

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
- records/: config, bounded events, result.json and provisional result.pending.json

V2 initially writes result.json as FINALIZATION_PENDING. Both that placeholder
and the staged candidate are closed, fsynced, hashed and charged before any
success publication. Original-child cleanup, event/fence descriptor cleanup and
source checks precede replacement. The final operation replaces only the owned
placeholder with the charged candidate; no gate follows it. A staged file is
never terminal success. Finalization failures preserve non-success at the terminal
name. The controller response includes the conservative pre-replacement charge
and published receipt digest; this does not establish directory/power-loss durability.

There is no fourth database copy. Each family is capped at 64 MiB; each must
also satisfy the tighter 24 MiB main DB + 32 MiB journal + 2 MiB allocated slack
= 58 MiB envelope. Three 64 MiB reservations plus 1 MiB for records/control give
193 MiB total. Charges are max(logical size, st_blocks times 512), including
directories. WAL and SHM are charged before rejection. No uncertain journal is
manually removed. Original/control directory allocations remain charged.

Exact settings: page size 4,096; max_page_count 6,144; DELETE; synchronous FULL;
cache_spill OFF; cache_size -4096; memory temporaries; busy_timeout/mmap_size 0;
foreign_keys ON; trusted_schema OFF; auto_vacuum 0. Cache/journal size settings
alone do not prove the envelope.

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

Helper tests use fakes: no product, SQLite or real child. They cover config/journal
limits, charges, handles/fd0, directory drift, worker refusals and receipt failures.
V3 injects each setup acquisition failure and secondary cleanup failures, checking
primary-error preservation. None ran. After source review and authorization:

```sh
"$PYTHON" -I -S -B "$RECIPE/test_qualify_cell_store.py"
```

Passing helper tests is not a physical SQLite proof. The physical run needs its
own exact execution review. Passing the physical run still does not execute the
product's tiny real-profile enabled-retention gates. Those remain separately
authorized selections in tests/test_native_owned_cell_store.py:

- AdmissionTests.test_off_absent_and_enabled_unqualified_do_not_create_sql_or_thread
- AdmissionTests.test_seam_synthetic_real_runtime_a_profile_restart_dispatch_and_off

These need the real profile, secure temporary roots, pinned test closure,
publish/read/restart/dispatch and OFF/rejection checks, and clean owner/pin/lock
closure. Simulated or zero-header fixtures cannot substitute. Their model-free
harness, limits and execution remain separate; this driver does not launch them.

## Review and reporting

Freeze all five files before configuration or tests. Check byte caps, parse
ASTs/JSON without imports, compare product pins, and review privacy and runtime
paths. This is unexecuted WIP pending independent review and execution gates.
Retain fresh local results privately. Publishing source does not authorize
execution or qualify the runtime.
