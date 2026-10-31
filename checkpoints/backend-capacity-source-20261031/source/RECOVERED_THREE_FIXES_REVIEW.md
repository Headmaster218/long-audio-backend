# Newly reconstructed three-fix candidate

This is new source, not a claim of original unpublished byte recovery. Baseline
is root-verified audio-dev71-recovered-rev02-20261023, complete manifest SHA256
9027da92a74db2def72ac4482df1345b082b7a4293c5a926e2a4a1c502496cdc.
The published clone and verified baseline are unchanged. No old global phase
instrumentation, profiling tools or commit diagnostics are required or added.
No tests, imports, SQL, models or runtime workloads were executed by the author.

## Product changes (three files only)

1. Capture._prepare_with_census wraps mapping and original_pcm in the existing
   default list_read_scope(self.budget). Scope closes before comparison,
   provenance freeze, optional copy or publication. Required capture remains
   hard; bounded_planning is NOT enabled here. Every existing authority read
   still starts a fresh snapshot and retains all identity/receipt/source checks.
2. float32_digest uses bounded stdlib array conversion only on little-endian
   hosts with signed-short itemsize2 and float itemsize4. Input block is still
   IO_BYTES//2 (32768 bytes). Scalar conversion remains the unsupported-layout
   fallback. Input type/evenness/640000-byte cap/error, IEEE float32LE hash and
   both independent construction/validation calls are unchanged. No cache,
   numpy, trusted digest, new budget or skipped checks.
3. CoreSeams.prepare's initial planning block opts into the existing authenticated
   read-refusal scope ONLY when a bounded handoff exists; nonbounded uses
   nullcontext. It ends before later bounded_prepare or native TURNS. Existing
   same-attempt callback/SQLITE_INTERRUPT/confirmed rollback+close conditions
   are untouched. No global catch, retry or deadline widening. Original
   Unavailable continuation uses the same cumulative primary budget; total
   exhaustion and uncertain cleanup remain hard.

## New test coverage and limits

- test_recovered_array_digest.py: 25 expanded cases. Exhaustive int16 raw bytes
  and hash, eleven boundaries, six invalid values, three fallback layouts,
  supported fast-path counts, current proof/ID/ownership equivalence for 2/3/4
  commits, independent two digest calls, and tamper rejection. These do not
  repair or update the pre-existing frozen historical AST oracle hashes.
- test_recovered_capture_read_reuse.py: four cases. Uses published real authority
  and tiny generated WAV/SQLite fixture. The mapping call itself is a stub that
  performs two real row reads; original_pcm and its source/receipt checks are
  real. Twelve snapshots and one connection are expected for THIS fixture only,
  not a claim to cover every real mapping branch. Mutation after mapping,
  rollback uncertainty, scope closure before copy and thread-local connections
  are covered. Existing authority/mapping regressions should run separately.
- test_recovered_initial_scope.py: fifteen expanded cases. Real Worker/SQLite
  with existing synthetic bounded fixture covers actual initial mapping and
  core_previous, own callback VM/deadline, external interrupt/stale marker,
  targeted rollback/close faults, same remaining budget, and total exhaustion.
  Deadline controls change only the test-local Budget clock dependency; VM
  controls keep real clocks. The inherited measurement-only commit wrapper
  assumes later true_eof exists; refusal tests bypass that wrapper ONLY and
  invoke unchanged Store.commit_window exactly once, without inventing true_eof.
  Includes scope-before-TURNS, nonoptional hard controls, and valid source or
  receipt timeline-binding mutation with constructor/roundtrip verification.
  This combined/automatic fixture proves the affected optional discovery branch,
  not A10 primary-only support workload fidelity or acoustic qualification.

Total authored cases: 44 expanded (root must verify collection independently).
No old test pass/failure counts transfer as new results. The lost receipt
frames-only fixture failed construction; this new suite deliberately uses a
valid timeline mutation instead, without rewriting that historical failure.
The historical nine AST oracle failures remain separate; no oracle pins changed.

## Root-owned next gates

Use the restored exact locked runtime, separate test-tool target and a fresh
workspace basetemp (not a low-headroom /tmp fixture). Check new manifest before
and after finite tests. First run the three new files; then selected published
list-authority/mapping and authenticated planning-read controls. Preserve every
outcome. Do not run until pass, change limits, weaken errors, or declare full
qualification from synthetic success. The separate A10-relevant primary-only
support fixture and necessary commit failure/stage diagnostics are subsequent
newly reviewed layers, not part of this candidate.

Historical A06–A10 failures remain historical observations; A11 lost final
receipt and cleanup status are UNKNOWN. No A11 result is reconstructed here.
