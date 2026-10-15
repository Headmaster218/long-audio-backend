# Recording receipt-outbox API helper

This is the actual helper source used for the historical primary/deduplication
and receipt-outbox case, converted to a portable template. Only local paths, invocation identifiers,
two relocation-dependent integrity pins and the entry description changed.
The public templates have not run. The aggregate outcome is in ../README.md.

## Inputs and case

Provision a complete product/runtime and assets separately. The root README
identifies the base revision and exact overlays. This packet alone is not an
installable product. Resolve every placeholder in CONFIGURATION_FIELDS.json.
Keep deployment values and generated evidence private. The helper requires the
same fixed mono 16 kHz PCM16 WAV: 336,000 frames, 672,044 bytes, SHA-256
0263b786f1375c96c121e52e48d79f4c6adcddb10b609b8bb67d5b35bd27d606.
Audio is not included.

The helper creates these source WAVs in memory and uploads in order:

1. B: canonical frames [136000,336000), declared at 136000: 12.5 seconds.
2. A: [0,200000), declared at 0: 12.5 seconds.
3. C: [152000,184000), declared at 152000: 2 seconds.
4. Exact A retry with unchanged key, bytes and coordinate.

The capture is a fresh legacy session with one explicit shared sample-coordinate
domain. POST finish uses expected_revision=3 and a fixed idempotency key, then
repeats the same finish. Three unique original sources total 432,000 frames
(27 seconds); the byte-equal union is 336,000 frames (21 seconds), with 96,000
alias frames (6 seconds). Canonical work is two adjacent 168,000-frame chunks.
Exactly seven source/work range maps are checked. The source-level receipt
outbox may first report pending_receipts, while both work chunks remain staging.
The helper polls at most 61 GET samples within 60 seconds and the global work
deadline. Receipt state, status and attempts may advance; source IDs, receipt IDs,
work identities, geometry and maps remain fixed. A queued receipt gap is recorded
explicitly and fails intended integration. Primary evidence is attempted before
later assertions, including when receipt waiting fails. Original encoded files remain
retained byte-for-byte. No inference retry or alternative model route is added.

## Evidence ordering and scope

HTTP finish can dispatch work before its response arrives. The helper's HTTP
checks therefore do not establish prequeue validation timing; the candidate
performs prequeue byte checks and its separate model-free tests cover them.
Primary terminal rows and transcript are saved first. Native metadata must show
one epoch and input_until=processed_until=21 seconds, no replay and two disjoint
10.5-second primary ownership ranges. Analyzer context durations are reported
separately; bounded context rereads are not duplicate primary ownership.

Only after positive server/known-descendant shutdown does the helper open its
own fresh jobs database with mode=ro and query_only. Four fixed bounded queries
resolve this capture's three originals, two derived work files and seven maps.
It reads 1,536,220 retained WAV bytes, below 2 MiB, verifies all five whole-file
digests and exact PCM, and checks derived concatenation against the input PCM.
It does not inspect historical or concurrently written databases.

Optional MOSS requires a naturally retained eligible seam [8000,328000), bound
to the current session, stream, both derived receipts/digests and exact support
PCM. Its fixed route allows at most one 320,000-frame request, context 855 and
generation reserve 512. Those are configured limits, not observed token counts.
No eligible seam means no MOSS call. In this historical v2 case, both derived
source receipts became durable in one attempt each, with optional dropped=0.
Only one cell was retained. Capture logged OperationalError with extended code
261 / SQLITE_BUSY_RECOVERY; its exact failing SQL query was not recorded. No
eligible seam was available and MOSS dispatch/acceptance counts were both zero.
The helper exited 1 and retained failed_intended_full_integration despite
successful primary, byte-integrity, receipt and native-ownership checks.

The v2 acceptance predicate requires all primary/integrity/receipt/cleanup
criteria plus exactly one naturally gated returned_diagnostic MOSS job, result
and raw HTTP 200, and verified model-process cleanup. A receipt-ready state or
zero owner drops cannot substitute for these conditions. No later capture retry
fix or successful complete integration is included here.

## Template binding and execution boundary

Use Python-safe literal substitution: do not inject unescaped quotes, backslashes
or newlines. Resolve server_entry.py first and calculate SERVER_ENTRY_SHA256 from
its exact bytes. CANDIDATE_ROOT_SHA256 is relocation/layout-specific: hash compact
canonical JSON (UTF-8, sorted keys, no extra spaces, finite values) containing
sorted direct-child [name,"directory"|"file"] pairs. The unchanged full package
inventory pin must still pass. Do not regenerate a package pin to hide a mismatch.
The full package pin is SHA-256
0aea123b5c046adba8ee766914cf12b3c347deddb4e9c8af3e07a4fa18ba03fe.
The separate package inventory encoding in PUBLIC_MANIFEST.json is explicit.

Save the resolved files as smoke.py and server_entry.py under the fresh helper
directory. The Python executable must match the fixed digest and qualified
runtime/filesystem configuration. The installed baseline is checked separately;
the entry selects the assembled v2 package without overwriting that installation.
Native installation paths must point inside
the prepared asset root. Run the resolved helper with that Python and -I -B;
--self-test selects in-memory geometry/JSON assertions, while --run selects the
real server/API/model workflow. Neither mode was invoked for this public packet.
Do not import the helper as a substitute for source inspection.

## Resource and process contracts

- Work deadline 1,680 seconds; total 1,800 seconds including 120-second cleanup.
- CPU 0-1 and nice 19; sampled helper/server/descendant RSS below 6 GiB.
- Inherited address-space limit at most 8 GiB; sampling is not a hard RSS quota.
- Fresh data at most 128 MiB, full output at most 144 MiB.
- HTTP has no redirects or proxies; bounded logs, pages and process identities.
- Optional idle wait at most 60 seconds/64 transitions; MOSS requires 480 seconds left.
- Full output-root allocated-byte accounting and locked RSS/identity/peak updates.
- Bounded identity-checked terminate/kill with positive wait/reap and known-child checks.

No runtime, database, audio, transcript or raw API response is distributed here.
