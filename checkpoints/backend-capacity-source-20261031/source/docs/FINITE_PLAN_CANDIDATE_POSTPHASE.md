# Explicit finite-plan candidate postphase

This optional, CPU-only path is off by default. Existing plan intent shapes,
primary statuses and legacy two-artifact export receipts are unchanged when it
is off. It does not start, stop, restart or repair the primary service, install a
runtime, retry inference, or create a general scheduler.

Use the existing installed strict-runtime finite-plan command with both
`--native-turns --candidate-postphase --candidate-config /private/config.json`.
The private config has exactly these fields:

```json
{
  "schema": "recorder-finite-candidate-config-v1",
  "python": "/absolute/optional-runtime/bin/python",
  "model": "/absolute/verified-handy-q8_0.gguf",
  "ledger_dir": "/private/fresh-candidate-ledger",
  "max_calls": 8,
  "max_wall_seconds": 4000,
  "max_output_bytes": 268435456
}
```

The ledger must be genuinely fresh under a private parent, separate from all
plan, receiver, job and export trees. Before primary work, its immutable owner
binding and initial reservation anchor are persisted before the plan intent.
This is configuration bookkeeping only. A crash between the two publications
requires review: an orphan ledger is not adopted and a missing pinned ledger is
not recreated. An existing plan cannot gain, lose or change this opt-in.

Only `plan_complete` with a verified physical terminal stop triggers the drain.
An early physical STOP includes only the exported generations actually visited.
All paused, refused and exhausted plans report `enhancement_deferred` without
candidate preparation, reservation, optional process or result artifacts.

Each completed export is revalidated against finalized original audio and its
native-turn artifact. The existing native-overlap selector determines the exact
supports. The optional child must match the reserved plan digest after its own
locked preparation and before creating output or invoking any controller. Exact-zero PCM and absent native detections are explicit `no_call`
outcomes, not recognition success. Serial attempts reserve the exact nonzero
call count, a whole-generation process deadline (180 + 360 seconds per call),
plus a fixed 120-second runtime-verifier allowance, and conservative retained bytes (16 MiB + 6 MiB per selected support, at most
64 MiB; no-call generations reserve 16 MiB for their ledger evidence). The persisted allowance is never refunded using sampled usage. `max_wall_seconds`
is the conservative reserved candidate-attempt allowance, not exact cumulative
CLI elapsed time. Local source preparation is separately bounded to 120 seconds
per pass; read-only resource admission and blocked verifications do not claim to
measure or cap total time across CLI reruns.
The monotonic reservation anchor is committed before the attempt intent. Missing
attempt tails, missing terminal manifests and incomplete receipts remain unknown
and stop the drain; another directory is never used to escape this result.
This protects normal interruption/deletion, not malicious rollback of all
persisted state to a mutually consistent earlier snapshot.

A new dispatch requires positive local listener/process identity, a verified
shared finite cgroup-v2 memory/swap boundary or a verified whole-guest memory/no-
swap boundary, CPU-selected pinned execution, observed combined memory and host
headroom. A remote or unidentifiable primary reports `candidate_resource_blocked`
with its prerequisite. The hash-only runtime verifier uses the standard temporary
directory. Set `TMPDIR` to a precreated, private absolute scratch directory on a
filesystem with enough free space to preserve the unchanged 5.5 GiB disk floor;
a small `/tmp` tmpfs is not sufficient. This changes scratch placement, not the
resource limits. An acknowledgment flag is not resource evidence. Primary
processes are observation-only and never cleanup targets. The optional controller
keeps its existing 3.5 GiB/300-second limits and 5.5 GiB disk reserve.

The strict installed package is the source of the trusted candidate code. Its
full Python/profile closure is pinned in the plan policy. The separately selected
optional interpreter runs only a fixed `-I -B -S` bootstrap, ignores ambient
bytecode/site startup and inserts its explicit verified runtime site roots.
Existing profile, model and wheel-member verification is reused. No source
checkout is needed for an installed invocation. A source layout and an installed
layout are distinct pinned policies; neither can replace the other on a used
ledger. The packaged runtime-manifest fallback works without a repository-root
manifest.

A successful own-ledger read checks every selected source/model/profile/plan
binding, exact files, native return, raw bytes, parsed diagnostic, positive child
cleanup and a final outer hash manifest. Validated complete entries need no live
primary resource admission and launch no optional processes on a repeat.
Missing raw/status/native evidence stays unknown. Native failures, aborted or
truncated output, missing text and parsing/schema errors are terminal failures
with diagnostics retained. None establishes recognition accuracy or speaker
identity. A failed or unknown generation prevents remaining work.

The output preserves `status` and adds `primary_status` plus a separate
`enhancement`. Exit 0 means primary complete and the opted-in drain complete;
exit 3 also covers primary-complete with enhancement blocked/failed/unknown.
If an actually spawned owned child's cleanup is uncertain, the CLI prints
`cleanup_incomplete`, marks intervention required, and keeps the original owner
alive holding the original plan and ledger locks and doing **only owned-child cleanup**.
The intervention notice is emitted immediately on stderr before this wait;
concurrent invocations cannot acquire ownership while cleanup is unresolved. This exceptional wait can outlast the
normal finite-work deadline. SIGINT/TERM do not discard unresolved ownership.
No new candidate, inference retry or primary action occurs. After positive
cleanup it emits a cleanup-complete notice and returns the original partial exit.
