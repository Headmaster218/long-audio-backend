# Experimental bounded fresh-window sampling

Experimental dev63 implementation with finite synthetic qualification only.
The selected 234 model-free tests passed; a separate installed metadata-only
smoke passed after two preserved fixture failures. No model qualification or
acoustic gain is established. The 234-node result binds the runtime02 source;
the installed package changes only version metadata in product code. The later
smoke fixture and documentation are not a rerun of all 234 nodes. See the [exact qualification and failure history](../reports/PRIMARY_LIVE_WINDOW_SAMPLING_DEV63_QUALIFICATION.json). The existing acoustic/comparison policy and thresholds
remain `primary-clean-history-v1` without changes.

## Explicit contract and compatibility

The separate setting/CLI flag `recording_primary_sampling_policy` /
`--recording-primary-sampling-policy` defaults to `skip-bucket-v1`. This retains
the documented old policy: an empty/busy minute is skipped; no historical replay.
`fresh-window-bounded-v2` is an explicit opt-in for newly created scopes only.
A scope pins its sampling policy permanently. Starting with v2 configured does
not convert an existing v1 recording. Diagnostics report the actual pinned
policy, not merely the current process configuration.

A fixed one-row-per-scope sampling table stores bounded metadata. The exact
existing immutable-scope-policy trigger is changed to fence old dev62 writers.
Migration accepts only the complete known old history shape with its recognized
bridge/reference guard, adds v1 rows for existing scopes, and updates the fence
inside the enclosing transaction. Partial/mixed shapes are refused, not repaired.
The unchanged scope metadata charge of 32 KiB includes this sub-2-KiB metadata.
Session erasure explicitly deletes sampling rows even without foreign-key
cascade enforcement. There is no per-window ledger or additional PCM storage.

## Admission policy

Bootstrap remains up to six admitted opportunities before 120 seconds, separated by
at least ten seconds. After 120 seconds:

1. Every newly committed examined source frame advances the existing high-water
   mark. Duplicate/older frames are no-ops and cannot reopen an opportunity.
2. Each 60-second bucket allows at most three eligible fresh-window examinations,
   counting an eventual admission. Non-due windows do not spend this budget.
3. A `busy` or `insufficient_clean_support` result may leave room for a DIFFERENT
   later live window in the same bucket. The old PCM is neither kept nor replayed.
4. A successful actual admission consumes the minute. All existing single-slot,
   immutable-attempt, dispatch, fairness and cleaned-operation guards remain.
5. Unavailable, permission, integrity/source, capacity or unclassified hard
   failures close the bucket; they do not authorize another window that minute.
6. Unresolved claim acknowledgments retain the existing slot and storage-only
   lifecycle. A positively resolved absent/rolled-back claim is no longer an
   unknown outcome: only after durable resolution and slot release may a different
   later live window use remaining budget. Its old attempt/PCM is never retried.
7. A positively failed preclaim validation must record its v2 hard closure before
   releasing the slot. If that metadata write is uncertain, maintenance retains
   only metadata, never audio or execution authority, until closure is confirmed.

## Diagnostics and limits

Read-only history/observation responses include `sampling`: pinned policy,
current examination bucket/count, last frame/reason, and fixed-key counters.
Counters saturate at 2,147,483,647; JSON is at most 2,048 bytes, rejects unexpected
keys/non-integer/out-of-range values before mutation, and never contains paths,
exception text, identities, PCM or vectors. `examined` is NOT a probe count;
`admitted` is separately recorded. `exhausted` counts unsuccessful third checks,
not successful third admissions. Default/v1 scopes explicitly report diagnostics
disabled with zero new counters rather than fabricate historical reasons.
Sampling changes do not invalidate unchanged acoustic comparison evidence.

More eligible live chances may increase optional work, but bounded cadence does
not promise more clean samples, per-person coverage or better speaker accuracy.
The exposed AMI A/B recordings are not rerun or tuned by this proposal.

## Finite synthetic fixtures

`tests/test_primary_live_window_sampling.py` defines finite metadata fixtures for
later-window admission, old/duplicate suppression, one admission per minute after 120 seconds,
three-check exhaustion, next-minute reset, hard denials, fixed diagnostics and
saturation/corruption, v1 pinning, schema migration/refusal, erasure, unresolved
unknown versus positive absence, and preclaim hard closure. Its cadence-claim
fixture explicitly mocks proof/admission boundaries, not the production cadence
transaction; it is not end-to-end model or provenance evidence. The selected genuine
producer/unknown-commit/fairness regression cases passed with the same 234-node
run. This is not the full repository test suite or an acoustic probe qualification.

## Diagnostic limits retained after qualification

The counters describe committed cadence outcomes, not every provisional copy,
encoder invocation or source-eligibility call. A rolled-back claim resolved as
absent after a newer busy watermark may omit its earlier outcome diagnostic.
An overtaken hard closure can update `last_frame` to its earlier event's source
frame; that field is the last recorded event source frame, NOT `high_water`.
`examined_count` is the normal live-check budget state, not a count of all late
closure events. For example, busy at source time 130 seconds followed by delayed hard closure at source time
120 seconds records
two total examined outcomes but retains one normal live-check budget entry;
the original minute is nevertheless irrevocably closed. These distinctions must
not be presented as exact coverage/attempt census. The installed smoke additionally checked genuine old-dev62 plain, reviewed-bridge
and reference-enabled databases, preserved existing v1 policy under v2 settings,
and demonstrated old-dev62 refusal of migrated copies without logical mutation.
Its successful Store reopen permits only the pre-existing +4 schema cookie from
two known lifecycle trigger replacements, with exact schema SQL, rows and other
PRAGMAs preserved. Failed old-writer and partial-schema opens retain strict
full-snapshot equality including the cookie. General/endurance regression and
complete provisional-attempt accounting remain outside this qualification.
