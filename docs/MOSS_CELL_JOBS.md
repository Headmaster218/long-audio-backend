# Explicit MOSS jobs from durable native-owned cells

Status: rc3.dev7 source-only, UNQUALIFIED pending separate frozen-source and model-free acceptance. This implementation does not qualify genuine larger-core capture, long recordings, acoustic quality, actual in-flight native cancellation, power loss, broader platforms, or hard service RSS limits.

## Opt-in and routes

Existing native_owned_cells_enabled and moss_jobs_enabled flags must both be enabled, with all existing MOSS/support requirements and the original qualified, ready cell owner. There is one existing MOSS manager, execution slot, optional permit and service-thread handoff. No scheduler, replay worker, automatic inference, regeneration or additional source-retention pool is introduced.

- GET /sessions/{sid}/native-owned-cells
- GET /sessions/{sid}/native-owned-cells/{cell_id}
- POST /sessions/{sid}/native-owned-cells/{cell_id}/moss-jobs

The existing MOSS list/detail/result/raw/cancel routes also serve cell jobs. Bearer authentication, private/no-store/nosniff responses, strict canonical query parsing, one mutation parser and the original two-response/1.5 MiB aggregate reservation apply to success and error paths.

List is a live, bounded view: after is exclusive, limit is 1..20, and a continuation requires expected_jobs_epoch. A page can contain fewer rows when finite source-validation work is exhausted. next_cursor identifies the last examined row; no unexplored row is skipped. This is not a snapshot or a ledger of omitted cells.

Detail returns the exact canonical stored proof, at most 65,536 bytes, rather than a wrapping JSON envelope. Headers report cell ID, row sequence/revision, proof SHA256, retention state, whether PCM is retained and current jobs epoch. A metadata-only row can have a valid proof without available PCM. Missing rows do not prove historical quota rejection.

Submit has exactly six fields: jobs_epoch, expected_cell_sequence, expected_cell_revision, expected_proof_sha256, support_id and profile. Sequence/revision are positive signed-64-bit integers; IDs and hashes have strict lowercase hexadecimal lengths. The profile is handy-moss-q8_0-cpu-experimental-v1. Required Idempotency-Key is 1..64 ASCII letters, digits, underscore or hyphen. Body size remains at most 1,024 bytes. No request controls paths, models, runtimes, prompts, threads or frame intervals. Acceptance 202 means ownership was accepted, not that inference succeeded.

## Binding and current authority

A process-local token binds durable_cell origin, authenticated session, opaque cell ID, exact row sequence/revision/state/proof hash, unchanged absolute SourceSupport, primary database identity, exact owner identity and revocation generation, adapter epoch, qualification digest and fixed MOSS profile/model/runtime. Full admitted proof bytes remain charged while the job is live; bounded cell_binding metadata links to the live proof route. Hashes preserve lineage but do not replace current primary rows, receipts and descriptor-relative source continuity checks.

SourceSupport retains its absolute start/end and core bounds. source_frames is the witnessed core endpoint, not the final recording duration. The stored proof contains the originally observed native configuration/model/runtime lineage and original/clipped turn intervals. There is no invented historical full-configuration hash or reconstructed raw native response. The controller's existing three-field service_context is unchanged; legacy capture_epoch carries the cell adapter's process epoch for this origin.

Idempotency includes origin, session, route target and all request fields. An exact repeat in one jobs epoch returns the same accepted job without another pin or invocation. Cross-origin/target/binding changes conflict. Retired accepted keys remain 410; the 65th distinct acceptance remains 429. Restart changes the volatile jobs epoch and does not replay or adopt results/keys. Durable cells are availability, not durable exactly-once inference.

## Delivery and cleanup ownership

Dispatch uses the cell owner's existing single read/write handoff and one of its existing two 768 KiB copied-pin reservations. There is at most one adapter acquisition. Read timeout, closed response or revoked generation abandons delivery; one claimant or the original owner owns cleanup. A ready or abandoned undrained result retains that same slot. The owner checks deadline/revocation around unlocked work and retains every copied pin until exact release acknowledgement. No late read consumes a key or creates a job.

A claim has an explicit owner-slot-release acknowledgement before subsequent validation. Preacceptance failures retain unresolved acquisition/permit references. Native job cancellation uses a different event from pin revocation/release. Normal pin release therefore cannot cancel a successful result. PCM is not early-dropped into a replacement pool; the original reservation remains charged until cleanup.

The manager validates current source after permit reservation, at before_input, before_spawn and before_admission, and after native return before publication. Running gates inspect generation/configuration/cancellation/TTL. Final serialization is followed by another immediate publication check. Result/raw reads freshly validate the original row, proof and source; uncertainty fails closed.

Native cleanup requires the original handle's positive wait/reap receipt, no known live owned descendants and no cleanup failure. Then copied memory must be removed from the exact pin registry. release_confirmed is set only after that registry removal and PCM drop. Process cleanup and memory cleanup are separate public fields; unresolved memory retains the record, lease and permit. Cleanup-only retry never starts another inference and preserves positive process cleanup evidence. The existing service/cleanup thread waits for memory acknowledgement only within the remainder of its existing 60-second cleanup phase; preacceptance cleanup uses a bounded 500 ms phase in that same exclusive handoff. After a deadline, ownership remains blocked. A later acknowledgement can be resolved by source-independent health/stop or cancellation cleanup, including after session deletion; automatic primary resumption beyond the cleanup phase is not promised.

Lock direction is manager to adapter to short operation/owner locks. The owner never calls manager or permit callbacks; no owner/delivery lock spans pin release, native execution, joins or response sending. Permit cancellation callbacks run outside the permit's condition. Shutdown closes admission/publication, retains cell ownership while stopping the primary Worker and jobs, then closes/unregisters capture. Earlier optional errors do not skip primary/jobs cleanup. Unresolved handoff/pins retain the original owner and instance-lock reference; a bounded join is not cleanup proof.

## Preserved bounds and semantics

The qualified schema, write transactions, write/read SQL strings, runtime tuple, pragmas, allocation rules and producer/controller/runner/permit sources are unchanged. Added owner operations are read-only: jobs_list, jobs_detail, dispatch, validate_dispatch. There are no new connections, threads, queues, tables, columns or persistent files.

Existing limits remain: 64 MiB DB family; 64 MiB unmeasured working reservation; 1,024 cells/4,096 receipts; 640,000 PCM and 65,536 proof bytes per cell; two copied pins; 500 ms cooperative owner budget, 5 ms lock ceiling, 150,000 SQL VM steps, 256 KiB projected SQL, 40 MiB source reads and 64 KiB transfers. Slow OS calls are not hard-real-time bounded. MOSS retains at most 1 MiB; raw at most 128 KiB, result at most 256 KiB, binding at most 4 KiB; 900-second TTL is job lifetime, not invented durable-cell expiry. Existing candidate/wrapper/scratch/host/disk reservations remain unchanged.

Exact native.raw_text UTF-8 bytes are not trimmed, reconstructed or normalized. Empty, missing, oversize and invalid-Unicode cases remain distinct. Phrase timestamps remain support-relative and unverified; EOS/token count remain unknown and complete_content remains false. Speaker labels are local to this invocation/cell. There is no native/adjacent-cell identity mapping, seam stitching/deduplication, word alignment, lexical winner, primary transcript rewrite, source retirement or automatic replay.

An already-authorized immutable response may finish after later deletion under its response lease. New reads fail current authorization. This is not forensic erasure.
