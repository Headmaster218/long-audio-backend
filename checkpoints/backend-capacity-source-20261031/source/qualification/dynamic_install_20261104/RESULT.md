# Alternate dynamic-runtime installed result, 2026-11-04

## Root cause and bounded support

The former rejection was an explicit built-in-only `_sqlite3` identity policy,
not evidence that dynamically linked SQLite corrupts data. The new verifier
supports a narrow Linux ELF64 little-endian x86-64 CPython extension form. It
checks actual SQLite relocation targets, exact symbol addresses, stable mapped
file identities and hashes, with balanced NOLOAD handles and unchanged budgets.
Detected unsupported, ambiguous, interposed or changing forms fail closed within
the documented threat model. See
`../../docs/DYNAMIC_SQLITE_RUNTIME_IDENTITY.md` for limitations. The default
production qualification literals are unchanged; observation does not activate.

The target is the existing system Python 3.13.5 and SQLite 3.46.1, in a fresh venv
at another path on the same cloud host/filesystem. This is a genuinely different
interpreter and SQLite build from the earlier relocated Python 3.12 builtin target,
but NOT an independent server, distribution or filesystem qualification.

## Exact source and physical evidence

Frozen source commit: `89c23bd7731bbcc997b55b06bc87fa07545c3672`.
Its 1,005-file manifest SHA-256 is
`a6e60bb77dd7cc383e87b114014d30d2ef49d33d127792b07318141803dce411`.
The target observation SHA-256 is
`86cdd5429b6362e44bf42ceb5c452b0ebeb94bd39b72edb83f86f88c323fab26`.

The finite production-DDL full-capacity crash probe passed: 6,144 pages, 55 cells,
2,048 sources, 6,044 journal records, 134 changed pages and 512-byte sectors.
Independent saved-byte reconstruction and recovered integrity/FK/row checks passed.
All changed pages reconstructed exactly to baseline, and the evidence inventory
remained unchanged. The audit shares the reviewed dynamic-binding verifier;
it does not claim a second independent ELF implementation. Lifecycle receipts
are historical, allocation is sampled, and process crash is not power-loss proof.

Nine explicit-evidence real-verifier tests passed. Review accepted the exact
basis and source-only materialization for a fresh bounded smoke. Only qualified
identity/evidence literals changed in the candidate. The saved patch and basis
are historical target-specific artifacts, never generic activation credentials.

## Fresh dependency preparation and installed source

Downloaded 27 exact-version platform wheels from the package registry, generated
and checked the platform-specific hash lock, then installed offline with
`--require-hashes --no-compile`. The new
`requirements-linux-py313-runtime.lock.txt` is specific to this Linux/Python3.13
configuration, not a universal ABI lock. Build requirements were also hash-locked.
Unlike the earlier copied-runtime test, these runtime dependencies were installed
freshly rather than inherited from another venv. System Python, SQLite and native
model-library identity remain separately checked, not covered by the wheel lock.

Built from a separate copy of the reviewed candidate and installed with
`--no-deps --no-compile`. The installed verifier checked all 176 product files /
4,186,705 bytes before and after native execution, with no product caches.

- Reviewed basis: `10b4bd6b35afbf46a281b39d7d9d0e14240b1e522a2eb3428f0692dfdaea4ba7`
- Materialization receipt: `88a9f474f4ca4821dde20a2ccc26e5eeb5b0c44c34835681db35d0cccefa3aca`
- Candidate CellStore: `6e37b5063a3242372fe2b7f6f7a0b124ff73e76d115c7b2517295ee35a582dd3`
- Exact patch: `940287bb7f0d2d0761e9f23141ffa9f4905928ebbc2778ce48a4b6bbf5fed8f4`
- Wheel: `6e608a58e35130eb83dc2df9c7ce5a1cc69de42dd2cb8ab7b9b6d2e806bb89b6`

## Actual native speech and stopped audit

The installed strict supervisor/uploader ran with `-I -B`, no source-path
injection, fresh private data and loopback only. Official public en.wav provided
7.152 seconds / 114,432 frames. One chunk completed, with two nonempty SenseVoice
segments and 14 primary lexical units. Independent stopped audit verified DB
integrity/FKs, exact uploaded PCM, source receipt/stat identity, two retained
packet hashes and matching native commits/strict outcomes. Packets cover
[0,84000) and [84000,114432). Model-layout copies have matching contents and
independent regular-file inodes. No transcript or recording is committed.

Historical process receipts/logs show exclusive lock while live, release after
intentional SIGTERM143, complete application shutdown, no forced cleanup and no
recorded surviving descendants. Sampled peak aggregate RSS was 755,499,008 bytes,
not a hard instantaneous bound.

## Tests and remaining limits

Separately rerun on frozen source: 106 selected dynamic/observation/materializer/
installed-source controls passed; six audit refusal controls passed; the nine
real-verifier target tests passed separately. Another selected historical suite
had 109 passes and three failures. The exact same three failures reproduce on
untouched remote b09b760: one stale empty-default-registry expectation and two
historical exact-host admission expectations. They were not changed or hidden.
This is not a full-suite green claim. A fresh restored delivery source also
passed the combined 112 selected controls (the 106 plus six audit controls).

No claim is made for independent-server deployment, Windows/other architectures,
physical Android recording, recorder provenance, acoustic accuracy, speaker
identity, overlap quality, long-running endurance, existing-store migration or
production readiness. Raw evidence, logs, transcripts, databases, installed wheels
and model weights remain outside Git. A different target must collect and review
its own evidence before materializing an isolated candidate.

## Same-target restart readback

One additional bounded service restart used this exact installation and existing
isolated test store, with GET-only readback and no new sessions/uploads or requested
inference. Independent review verified one completed chunk and two persisted ASR
segments, matching before/after durable ASR and chunk rows, clean integrity/FKs,
no nonempty WAL/journal, normal 143 shutdown and no recorded remaining processes.
All 176 installed source files still matched. Sampled peak RSS was 123,088,896 bytes.
Private restart result SHA-256:
`674de1e47fdea12217857d9290be41ecbaf8b8db49030344ff97db2907d228e9`.

An initial harness precheck refused an empty WAL before launching any service.
That distinct failure and empty-sidecar snapshots were retained; the subsequent
single actual restart accepted an empty WAL under an exclusive instance lock.
No product guard was weakened. Full stored segment rows happened to remain equal
in this run, which does not establish optional cross-boot annotation validity or
complete API-output equality. This restart is not an additional crash-recovery,
endurance, migration or independent-server result.

The final remote-restored source passed the 112 selected controls again.
