# Historical rc3.dev4 Handy MOSS one-job source checkpoint

Version 0.2.0rc3.dev4 introduced an explicit experimental local developer profile
and one-job controller, without app/Worker dispatch or public routes. That
published checkpoint preserved default settings, the old MOSS model manifest,
SQL, scheduling and the unrestricted primary long-recording path. Its source
and qualification remain historical, without claiming lost commits or earlier
qualification were restored.

The separate [rc3.dev5 explicit jobs integration](MOSS_HANDY_JOBS.md) adds a
default-OFF service entry with a shared primary/optional execution permit. The
direct controller contracts below still apply; this page does not qualify
those service routes or their concurrency.

## Closed profile and separate entry

The only accepted name is `handy-moss-q8_0-cpu-experimental-v1`. Its packaged
profile identifies Handy's `handy-computer/moss-transcribe-diarize-gguf` repository
at revision `bfa3d24438711391d8713c6ab0efd6264527757c`, with 986899616 model bytes and
SHA256 `64ec654dc6ffcfdfe180422dffce1d33422b0c30959b7edfd131bad77ee35039`.
The runtime is transcribe.cpp 0.2.3 at
`63a44d9239d610b3908e8a66b384924cd4a77217`. Existing reviewed wheel-member hashes
are reused. The existing `551c…` product model identity is not edited or used to
admit this profile. There is no filename detection, default selection, download,
automatic install, Vulkan fallback, or model import in profile verification.

This slice is Linux x86-64 CPU only. Its actual owner requires psutil 7.2.2;
source and parser fixtures otherwise use the standard library. Model loading
occurs only in the separate child after explicit developer invocation, profile,
installed-runtime and full-model verification. The child checks loaded model
family, backend, native provider, library and version/commit. It uses two CPU
threads, f16 KV storage and the existing adaptive context policy, one support,
one session and at most one run attempt. It never uses the old strict_result
EOS inference or enters MOSS legacy recovery.

## Raw and diagnostic contracts

The exact-raw scope is explicitly native.raw_text encoded as UTF-8. It is not a
complete SDK envelope: SDK tokens, word objects and auxiliary segment fields
are neither retained under that name nor reconstructed. Up to 128 KiB of exact
raw text is written before parsing or closing the session. Invalid Unicode,
missing text and oversize output yield explicit unavailability. No truncated
prefix is described as a complete exact result. Error/abort paths retain any
available exact raw but never retry a possible call.

EOS and generated token count remain unknown, including a normal SDK return.
Native status is not invented. At most one untimed Alternative represents this
profile, with at most 64 KiB of text and the existing 192 KiB projection cap.
Its complete_content is always false. Canonical phrase text is separate from
raw preservation; malformed envelopes, text limits and unparsed content remain
explicit diagnostics. There is no ranking, identity update or downstream
candidate publication to the application.

Useful model-local anonymous speaker labels and support-relative phrase times
are kept separately, at most 256 phrases, eight labels and 64 KiB. Times remain
model-generated and unverified. Out-of-order or overlapping phrases are not
silently repaired. Invalid times are excluded from the timed diagnostic while
untimed text can remain. Global speaker identity and word alignment are absent.

## Ownership, budgets and failure semantics

The explicit MossController entry accepts an already validated immutable
SourceSupport plus matching immutable PCM16 bytes. It validates frame geometry
and both PCM16 and normalized float32 hashes. It does not rewrite caller-owned
input files. One support is limited to 320000 frames at 16 kHz, or 20 seconds.
This experimental bridge cannot complete a long input and does not constrain
the default long-recording pipeline.

There is one occupied slot and zero backlog. A terminal record continues to
occupy that slot until explicit discard. A possible call is recorded before
child admission and is never replayed. In-memory terminal projection is capped
at 256 KiB, with separately retained exact raw capped at 128 KiB. PCM input is
at most 640000 bytes and is released when the child request is written or the
job terminates. The immutable source/profile identities bind the job.

The caller supplies a private, mode-0700, no-symlink empty scratch directory.
An exclusive no-follow owner marker binds owner nonce, job, source generation,
support, profile and parent PID/start-time identity before spawn. Directory
device, inode and owner are revalidated before filesystem operations.
Only five regular unlinked files
are allowed: owner.json <=1024 bytes; request.json <=16 KiB; support.wav
<=640044 bytes; raw.bin <=128 KiB; result.json <=8 KiB. Combined logical and
allocated scratch are limited to 2 MiB. There are no growing stdout/stderr logs,
pending output copies or arbitrary extra filenames. File-size limits and
periodic accounting supplement fixed bounded writers; they are not a kernel
directory quota or hard RSS containment mechanism.

The owner starts one child and binds the original Popen PID/create-time before
a positive RSS sample and one-byte stdin admission. Parent identity is checked
before and after admission; EOF fails closed. The child starts with -I -B -S,
without sitecustomize or .pth startup execution.
For Python 3.12 -S, runtime sites are derived from the selected interpreter's
lexical venv path and regular pyvenv.cfg, with explicit system-site exclusion.
Missing, malformed or aliased expected sites/configuration fail closed. There
is no fallback from a detected venv to base packages. Distribution discovery
uses only those roots, rejects duplicate packages/providers and verifies that
ordinary lookup resolves the same exact metadata directory before reusing the
unchanged installed-wheel verifier. Native-loader environment
overrides and an existing system preload file are refused before spawn. The
child has a parent-loss/deadline watchdog and forbids Python process
or network creation. The owner checks a 300-second compute deadline, 3.5 GiB
caller-plus-owned-tree RSS cap, 2 GiB host reserve and 5.5 GiB disk reserve. A
separate finite 60-second cleanup phase preserves those resource floors.

Cleanup directly reuses the unchanged CandidateGuard/process_cleanup mechanics
from qwen_candidate_runner, with separate MOSS-only owner state. This does not
dispatch Qwen work. Original-handle wait/reap and no known live owned descendants
are required. Observed non-child zombies are called terminated, not reaped.
The receipt explicitly says discovery_complete=false and observed-owned-only:
PID/create-time snapshots are not proof of exhaustive descendant discovery,
nor protection against an unseen native fork. Unknown identity, denied scans,
PID reuse or an unconfirmed original wait fail closed. No permissive kill_tree
return is treated as cleanup proof.

Cancellation invalidates publication before termination. Late results cannot
become a candidate. Results are not exposed while cleanup is pending. Cleanup
failure keeps the owner marker and occupied slot; only bounded cleanup retry is
permitted, never another invocation. Discard cannot remove live or uncertain
ownership. After a crash, a fresh controller refuses any existing scratch or
marker rather than assuming the earlier process died. There is no automatic
adoption, restart resume, recovery replay or claim deletion. Human investigation
of abandoned ownership is a separate action.

## Qualification boundary

The rc3.dev4 tests cover profile and source closure, raw/UTF-8/projection bounds,
unknown EOS, one-call exception/close behavior, queue occupancy, cancellation,
restart refusal, marker tampering and identity cleanup negatives. Most are
injected standard-library fixtures. Two small admitted stdlib process fixtures
cover actual Popen binding/RSS/stdin/raw handoff/reap and EOF-before-admission.
They do not read model or audio files, import native code or perform inference.
Test artifacts and per-case failures are retained by the external guard.

Source-only preparation is not evidence that these tests ran. Execution results
must be reported separately against a frozen source/index/seal. There is no new
acoustic, long-recording, Windows, Vulkan, full-suite or production-concurrency
qualification here. Public routes and a shared execution permit are documented
separately for rc3.dev5; this historical checkpoint provides no evidence that
those additions were run, qualified or published.
