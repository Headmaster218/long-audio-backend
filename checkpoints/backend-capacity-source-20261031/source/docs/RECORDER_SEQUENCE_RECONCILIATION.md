# Read-only snapshot sequence and gap reconciliation

```sh
python -m audio_backend.recorder_sequence /private/snapshot-a /private/snapshot-b
```

Supply explicit previously created snapshot directories. The command never scans
an entire incoming FTPS folder, downloads data, creates copies, writes a database,
repairs gaps, resamples audio, starts models or publishes strict recording work.
It verifies each owned snapshot and the complete producer metadata before
returning one JSON report. Any invalid/incomplete/tampered input refuses the whole
request; there is no partial-success report authorizing a subset.

## Report meaning

Inputs are grouped by hashed capture identity and sorted by declared sequence.
Input indices refer to the supplied argument order. Raw capture/run names and
local filesystem paths are not included in output. Sequence is global within a
capture; `frame_start` is the producer's epoch-local **recorded PCM** coordinate,
not a backend-normalized 16 kHz coordinate or a hardware clock.

- Repeated capture/ordinal with identical inner metadata bytes and WAV hash is a
  `same_content_repeat` observation, even if the transfer attempt/revision differs.
  It does not schedule replay or make another unit of work.
- Different content for one capture/ordinal is `conflicting_ordinal_content`.
  Both input indices are retained and edges touching that ordinal stay unresolved.
- Missing ordinals, declared frame gaps/overlaps, regressing epochs and conflicting
  run/format/input/target declarations are reported explicitly. No silence or
  synthetic timeline is inserted.
- Every epoch transition remains `epoch_boundary_unmapped`. Restart/run mismatch,
  START boundary reuse, missing epoch declarations, cross-epoch seam claims and
  nonzero first frame after an immediately preceding ordinal get additional
  diagnostics. A valid restart declaration still does not settle physical time.
- Within one epoch, `declared_adjacent` requires exact consecutive ordinal and
  frame extent, matching run/format/input/boundary/target declarations, previous
  ROTATION close, current trusted-previous-seam declaration and no recovered tail
  or ordinal content conflict. This label describes agreement among declarations,
  **not proven continuous recording**.
- Same-epoch consecutive v2 neighbors must also agree on process clock domain,
  contiguous run-sample extent and read order. A read split over two segments may
  repeat its ordinal only with an identical full retained observation; distinct
  reads require exactly the next ordinal and matching sample endpoints. Mixed
  observed/unobserved mode or contradictory retained observations remain unresolved.
  This checks producer declarations and does not align hardware clocks.
- Missing anchor observations, declared anomaly flags/counts and unavailable
  queries remain warnings. They do not become a clock mapping or silently change
  timestamps. The reporter does not reconstruct hardware timing from observations.

The report always includes `physical_continuity_proven: false`,
`hardware_frame_mapping: UNESTABLISHED`, `strict_import_ready: false` and
`gap_repair_performed: false`. A supplied prefix is distinguished from an absent
predecessor, and STOP at the end does not establish whole-capture completeness.
It is possible to have a locally adjacent pair inside a capture that also contains
an unrelated conflicting ordinal; the conflict remains visible in the capture
summary. Neither that pair nor any aggregate becomes an import authorization.

## Bounds and concurrent change

At most 32 snapshot arguments and 512 MiB aggregate WAV bytes are accepted. Each
input uses a point-in-time regular-file size precheck, then full bounded snapshot
validation and metadata hash binding. These are observed input-size limits,
**not a hard kernel-I/O meter against a concurrent same-user writer**. Snapshot
validation rereads files. One cooperative 120-second deadline is shared across
inputs, but blocking filesystem calls are not forcibly interrupted. Memory holds
at most 32 bounded decoded metadata objects and a bounded report, never whole WAVs.
Snapshot files must be under the operator's control and remain unchanged during
inspection. Existing snapshot ownership/trust limitations still apply.

## Validation, 2026-11-03

44 new reconciliation tests cover shuffled order, duplicate identity/conflict,
missing ordinal/prefix, frame gap/overlap, restart/epoch transitions, run/format/
input/target mismatch, stop/interruption/recovered tails, warnings, bounds,
tampering, CLI and real generated snapshot integration. The final selected suite
contains 203 checks with the prior snapshot/metadata/transport and selected
capacity checks. This is not the full repository test suite.

Three Java-produced rotation snapshots yielded two declared-adjacent edges while
retaining all unproven-continuity flags. Combining all nine independent Java
fixture deliveries correctly produced capture/ordinal conflicts: several scenarios
intentionally reuse the same synthetic capture and ordinal zero. An initial ad-hoc
expectation that all nine should join was wrong; the expectation was corrected,
not the conflict detection. No actual recording was repaired or ingested.

This is the last read-only boundary before a separately reviewed ingestion design.
Durable backend session/idempotency mapping, owned-source lifetime, normalization
across formats, explicit gap handling and strict primary evidence remain separate
requirements. No ingestion, source-retirement or MOSS dispatch has been enabled.

Independent review caught an initial omission of cross-segment v2 observation
contradictions. Clock-domain, run-sample, read-ordinal/shared-read and observed-mode
checks were added before publication, with eight regression cases.
