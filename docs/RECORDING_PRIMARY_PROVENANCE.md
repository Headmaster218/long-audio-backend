# Prospective native-primary provenance (dev46)

This slice retains immutable numerical evidence of how an audited native query
assigned the primary ASR units. It makes no speaker-identity decision. Existing
recording-local reconciliation, 32-node/four-crop limits, probe/link quotas,
thresholds, scheduling and source GC are unchanged. Stored pooled vectors are
never used. No actual embedding, native model or ASR-accuracy result is claimed.

## Activation, data and authority

There is no new enable switch. Capture requires the existing opted-in recording
speaker links plus automatic bounded native path and the audited SenseVoice CTC
producer. Disabled configurations retain their former behavior. Evidence is
prospective: old primary rows without a packet remain unavailable.

The worker takes a bounded external observation before result validation. It
compares the audited fields after validation before attaching original parent
and unit ordinals. Ownership cuts retain those ordinals and the exact final row
order. Capture freezes the observation while its native-response/PCM witness is
still live. Assignment replays the existing unit-start anchor and inclusive
turn.start - 0.03 <= anchor <= turn.end + 0.03 rule. One distinct raw label is
required; every matching same-label turn is retained. Unknown siblings remain
represented, but empty, unknown-only and wholly unbound windows are skipped.
Normalization, ambiguity or binding mismatch never produces a new assignment.

Each compact packet has query/run/epoch/runtime/source/owned-PCM and core
commitments, one shared turn/label dictionary, original parent/unit ordinals,
numeric anchors/endpoints, all matching witnessed raw-turn ordinals, and exact
references to the actual inserted SQLite primary rows. The p.ref computation
uses stored PRIMARY_FIELDS, including SQLite REAL affinity. Optional l.refs
come only from validated lexical inventory. Missing lexical retention, including
its separate 32 MiB cutoff, does not remove this numerical geometry. Packets do
not copy transcript/token text, PCM, vectors or complete primary/native rows.
Existing p./l. bytes, hashes and revisions are unchanged.

Historical audit and current eligibility are separate. Indexed audit reads can
return a retained packet after its source or compact core is gone. Current use
requires a single read snapshot binding the exact stored packet/header, live
session, recording membership, source placement/current authority, surviving
exact core and actual primary rows. A caller-supplied rehashed document is not
stored evidence. No public endpoint was added; the existing chunk provenance
contains fixed cumulative reason/count summaries, so later success does not hide
earlier refusal.

## Storage, quotas and failures

Two additive tables, recording_primary_packets_v1 and
recording_primary_usage_v1, hold immutable packets and transaction-owned logical
usage. Exact window/core uniqueness and a monotonic sequence support exact lookup
and indexed keyset reads. A read returns at most eight headers and 64 KiB of
bodies; it does not materialize a session. Admission uses indexed usage counters,
not a session-wide SUM.

Settings and matching CLI options are:

- recording_primary_provenance_recording_bytes /
  --recording-primary-provenance-recording-bytes: 128 MiB by default.
- recording_primary_provenance_global_bytes /
  --recording-primary-provenance-global-bytes: 512 MiB by default.

Both must be positive integers, at most 64 GiB, with recording <= global. These
are separate from the existing 4 MiB recording probe/link quota. Packet bodies
are at most 64 KiB. Charges are actual canonical body bytes plus bounded indexed
header bytes. There is no arbitrary packet-count ceiling. Lowering a quota does
not evict existing packets; additional admission refuses until usage permits it.
Physical DB pages, indexes, WAL and SHM are separate from the logical quota.

Persistence follows actual primary-row insertion and surviving core commitment
in the same transaction. It reserves optional work within the existing SQL/time
budget, without raising RAM, SQL, execution or identity limits. Only defined
optional preflight/capacity/quota refusal rolls back its own savepoint and keeps
the primary commit. SQLite FULL/IO errors, uncertain commit/rollback, unsafe file
identity and baseline free-space failure propagate. The provenance-local fixed
DB/WAL/SHM check keeps the existing 5.5 GiB floor and write/scratch/cell allowances,
accounts for current WAL and adds a bounded incremental packet allowance. It
does not inspect execution permits or mutate MOSS resource state. This check is
not a physical reservation against other writers or arbitrarily old readers.

Packets are kept until explicit session erasure. The only ownership cascade is
from chunks; no source/core FK or additional GC pin was added. SQL-only deletion
and accounting triggers work on old dev45 connections. Isolated whole-package
dev45 startup preserved packets and counters; intentional old-binary session
erasure removed them and decremented exactly once, including empty recording
usage cleanup. Unknown schema shapes fail without automatic conversion/rewrite.

## Fixed metadata-only physical measurements

The shapes were frozen before measurements:

- Sparse: 24 units, one label and two matching duplicate raw turns per 10.5 s.
- Dense: 512 units, four labels and eight raw turns per 10.5 s; ambiguity at
  label boundaries retains unknown sibling units.

Each opportunity uses an independent synthetic 10.5-second upload scope in the
same recording. This avoids crossing the native 300-second epoch and adds a
conservative extra chunk/source/index footprint. The real encoder and persist
transactions run on fresh SQLite databases with fixture-owned primary/core rows.
The fixture removes its core after persistence to retain historical audit without
claiming continuing source authority. No continuous producer, native epoch
scheduler, historical audio or actual model was exercised by these sizing runs.
The counts 4,115 and 16,458 correspond to nominal 12 h / 48 h opportunities; they
are not a universal retention guarantee.

All numbers below are actual bytes. DB file size can lag logical database pages
while changed pages reside in WAL. Page size was 4,096 bytes and freelist zero.

| Shape / opportunities | Retained / refused | Logical charge | DB file at cutpoint | Logical DB pages (bytes) | WAL / SHM at cutpoint |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sparse / 4,115 | 4,115 / 0 | 11,273,883 | 24,793,088 | 24,801,280 | 4,260,112 / 32,768 |
| Sparse / 16,458 | 16,458 / 0 | 45,100,162 | 96,731,136 | 96,755,712 | 4,293,072 / 65,536 |
| Dense / 4,115 | 4,115 / 0 | 95,201,654 | 119,279,616 | 119,877,632 | 4,301,312 / 32,768 |
| Dense / 16,458 | 5,801 / 10,657 | 134,212,322 | 212,447,232 | 212,484,096 | 4,293,072 / 98,304 |

Dense exhaustion occurs after 5,801 retained opportunities, about 16.9 nominal
hours for this fixed shape. All 10,657 refusals were explicit recording_quota
statuses; every refused window's exact primary fields and stored status summary
were checked after commit. The complete dense primary output has 115,206 rows.
Both global and recording usage counters equal the actual retained packet count
and summed charges at both cutpoints. Canonical body totals at the final cutpoint
were 37,825,726 sparse and 131,648,280 dense bytes.

A reader was held across 256 writes after opportunity 4,115. Peak WAL sizes were
33,400,872 sparse and 40,779,792 dense bytes (allocated 33,406,976 and 40,787,968).
Reader-release and checkpoint samples are retained. Final TRUNCATE checkpoints
left zero WAL bytes and DB files of 96,755,712 and 212,484,096 bytes (allocated
96,759,808 and 212,488,192). SQLite dbstat was available: final provenance tables
and all their indexes occupied 71,598,080 sparse and 144,048,128 dense bytes.
The rest of each database includes existing schema and synthetic primary/source
rows. A longer retained reader can accumulate much more WAL; these measurements
are not an upper bound for arbitrary readers or pathological 64 KiB packets.

Early/late recording-keyset reads used the (sid, rid, sequence) index. Sparse
returned eight packets / 18,340–18,392 body bytes in nine SQL statements and
285/279 VM steps. Dense returned two packets / 45,327–45,392 bytes in three
statements and 183/177 steps. Mismatched session/recording reads were empty in
one statement / 16 steps. Maximum measured window SQL work was 2,700/4,000 VM
steps and 8,622/11,764 projected bytes, within existing budgets.

## Qualification scope and reproducibility

Fresh replacement-runtime model-free stages passed: one real Worker -> ownership
-> Capture.prepare -> primary SQL -> restart test; 40 contract cases; two fixed
single-packet measurements; two pure geometry cases; two 128-transaction
calibrations; two long sizing cases; and 28 selected affected regressions. This is
77 scoped cases, not a full repository suite. The finite affected selection
covers lexical references/ownership, disabled capture, conservative compact GC
and recording-link ledger compatibility. Cap-filling/threaded broad suites and
actual inference remain outside this qualification.

Two failed calibration attempts are preserved: a missing required fixture
speaker_status column, then an epoch-crossing synthetic scope. The corrected
pure test reproduces the latter refusal before validating the independent-source
geometry. Production validation was not relaxed. Earlier pre-recovery scratch
results whose raw artifacts were lost are not counted as recovered qualification.

The runtime was Python 3.12.14 / pytest 8.4.2, with pinned restored dependencies.
Stages used CPU 3/nice 19, 256 MiB combined RSS and 1 GiB AS; dense sizing had an
explicit 240-second envelope, others 120 seconds. Actual long-stage durations
were 26.660 / 132.933 seconds with about 64.5 / 64.3 MB peak combined RSS. These
synthetic process measurements do not establish actual-model RAM/VRAM usage;
the existing 6 GiB target is unchanged. All terminal handles were waited, owned
process groups were empty, and source/control/runtime pins were rechecked.

For the old-binary test, set LONG_AUDIO_DEV45_BASELINE to the absolute source
root of exact dev45 commit 2be2be425e51091d890fab03564661524752afa9. Only an unset
variable skips that optional compatibility test. A supplied invalid path fails;
there is no fallback to the new package. Release qualification explicitly sets
the pinned baseline and requires that case to pass without skips. Offline
source/wheel/installed-byte and installed-path results are recorded separately;
package parity does not establish external model/runtime qualification.

Retained numerical reports are bound by SHA-256:

- Sparse: 52e1785fd6383492a34b332ce22d92c6349cccda106d8af2bb68f8c1f9f2abd4.
- Dense: 3a72f03adf8fca7a047b91470ceb610c3281b81c464e3363c74077bed42daf05.

Independent terminal receipts bind the raw reports and retained databases:
sparse e7ff0d71997f73ba6d3841cde9a7bc1d0013ec10198002c30a039cfc2b559552;
dense b42b96a9a4b0ca383e8d0f174c20d7dbd2403d6332c0f48960886491e04c6a44.
