# Bounded native capacity result, 2026-11-04

## Result

PASS under the original two-page capacity gate, with one preserved result-store
refusal warning. This is a separate inference experiment from the synthetic
256-segment receiver study. Do not combine their durations or claim eight hours
of inference or uptime.

The run completed 56 chunks of 10.5 seconds: 588 seconds (9 min 48 s) of repeated
existing real English speech through the real application Worker, SenseVoice and
Nemotron path. Independent observer wall time was 577.649 seconds (9 min 37.649 s),
including launch and cleanup. No input mutation, inference replacement, gate-limit
change, failed-attempt retry, or automatic replay was introduced for this run.

## Exact tested basis

- Backend core: `bda0ad6e87779b884a872cd4aedbde739976d829`, all 962 manifest files
  restored and checked before launch.
- An isolated candidate applied exactly `qualification/accepted_candidate_20261103.patch`.
  Only the two previously accepted CellStore runtime/evidence literals changed;
  patched module SHA-256 was `323bb5528ad656baa0699131154990a6dc5184cbf14a6b0591ba81482cf4fd77`.
  Production source/profile defaults were not altered. Candidate equality was
  checked again after execution.
- The accepted interpreter/SQLite fingerprints and local filesystem device 27
  matched freshly. All 33 pinned assets and all 27 runtime dependency versions
  matched. Original wheel archives were not freshly reverified.
- 160 focused capacity/accepted-runtime tests passed on the qualified workspace
  filesystem. An initial invocation in `/tmp` correctly failed eight qualification
  setups because that filesystem was device 39; 152 other tests passed. The setup
  was moved to the actual qualified workspace, without changing tests or selectors.
- Command semantics remained `tools/capacity_run.py NEW_PRIVATE_OUTPUT --pages 2`,
  using the recovered qualified interpreter with `-I -B`. Existing asset-layout
  prerequisites still apply. The capacity controller retained its original 1,800 s
  gate, two CPU slots, 6 GiB sampled memory bound, 16.25 GiB output bound and 5.5 GiB
  free-disk floor. A separate observer retained samples and imposed an additional
  2,100 s outer stop boundary; it never needed to stop this run.

## Measured resources

574 independent samples recorded the owned process tree and retained output.
Peak observed process-tree RSS was 915.30 MiB, excluding the controller's 64 MiB
accounting headroom. The original gate separately reported peak charged RSS of
960.18 MiB including that headroom; different sampling instants explain why these
are not identical measurements.

Observed aggregate peaks by completed-chunk band were approximately 901.68 MiB
(1–14), 915.30 MiB (15–28), 874.69 MiB (29–42) and 874.00 MiB (43–56). The service
process itself remained within roughly 89–104 MiB after the initial warmup. These
samples show no runaway memory growth over this bounded workload, not proof of an
indefinite plateau or a multi-hour endurance guarantee.

Final allocated output was 95.67 MiB across 131 files. Retained audio, primary
records, archives and evidence intentionally accumulate; disk is not expected to
stay flat. All sampled resource bounds remained satisfied.

## Integrity and shutdown

- All 56 chunks completed with exact original geometry.
- Original stopped checks validated both SQLite databases, file hashes, 26 archives,
  29 shared bodies, 54 shared references and three reused bodies.
- Three cold PCM objects were read successfully through the original API after
  Store reopen. Reopen cleanup was confirmed.
- Service exited zero; worker stopped, store safe, instance lock closed, execution
  permit clear, no cleanup exceptions and no surviving owned descendants.
- Zero committed-claim late notices and zero skipped read observations.
- The final periodic progress snapshot still showed 54 chunks; the authoritative
  RESULT and stopped database checks established 56. A periodic snapshot is not a
  final completion receipt.

## Preserved warning and limits

One stderr event reported `moss_result_database_refusal`, operation write, phase
configure, deadline budget, `SQLITE_INTERRUPT`, 2,800 VM steps. Source inspection
shows that this refusal occurs under the unchanged 250 ms result-store write budget
before transaction/body entry. The bounded log does not establish the exact caller
or scheduling cause. It was not erased, retried by the harness, or reclassified as
an identity success. No evidence here justifies relaxing that guard.

The original gate still passed primary work, integrity and shutdown. Its optional
attempt summary recorded 13 successes, but explicitly did not claim successful
speaker identity resolution. Repeated short English content does not evaluate
acoustic accuracy, diverse long conversations, overlap, speaker stability, physical
Android capture, the new FTPS service wrapper, network loss, power loss, or local-server
portability. This exact runtime qualification is not a generic deployment approval.
