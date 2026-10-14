# Offline recovery recipe

Source-only snapshot; full recovery is incomplete. Review sources, pins, commands
and resources before execution. The immutable product stays untouched; optional
replacements affect a separate build copy. No assets, installed runtime or logs
are included.

## Inputs

Product: Headmaster218/long-audio-backend commit
`1fc066910ca78b2e57d7a28f79b91cafc06f5dd5`. source.lock.json pins 106 files /
1,640,149 bytes, including all 101 package files and three profiles.

assets.lock.json closes 38 assets / 1,479,299,858 bytes: 7 primary/MOSS/native/license
resources, 30 wheels (27 runtime, 3 build), and the 2,261,722-byte official FunASR
demo. Every URL, destination, length and SHA-256 is explicit. AliMeeting is absent.
No extra model acquisition is included.

Pins derive from WIP38/45/46 at preparation commit
`4967dd8e7239a563fa8f6858a1af3483f1e3c3e7`; the demo revision is in assets.lock.json.

Host CPython 3.12.14: 30,894,944 bytes, SHA-256
`fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.
Host venv and bundled ensurepip pip 25.0.1 were observed, not downloaded. Host
stdlib/system libraries remain external. Linux x86-64/manylinux_2_28 is required.

## Separate stage commands after review

Run with the known host Python and -I -B from this packet directory:

1. `HOST -I -B recover.py download`
2. `HOST -I -B recover.py install --candidate CANDIDATE_ROOT --candidate-store-sha REVIEWED_SHA --candidate-jobs-sha REVIEWED_SHA`
3. `HOST -I -B recover.py extract`
4. `HOST -I -B recover.py check`

Omit all candidate arguments for unmodified base installation. Candidate mode
requires both exact reviewed digests and copies only
`audio_backend/native_owned_cell_store.py` and
`audio_backend/native_owned_cell_jobs.py` over the verified build-copy counterparts.

Default sibling directories:
- source-recovery-20261014/product-dev8: immutable source
- minimal-recovery-work-20261014: assets, build, temporary files and bounded logs
- reconstruction-rc3-dev5-api-prep/runtime: canonical final runtime

Subsequent product verification should invoke the canonical runtime's
bin/python3.12. Do not change the qualification registry. Identical interpreter
bytes alone do not qualify its path, SQLite binding or filesystem tuple.

Download runs one curl at a time, ignores curlrc, and permits HTTPS-only redirects
(maximum five), with no retry/resume. Only downloads inherit the fixed proxy/certificate whitelist in recover.py.
Values are never logged; TLS verification stays enabled.
Connection timeout is 30s; speed-time 30 / speed-limit 1 is a low-speed stall bound,
not a per-read timeout. Rate is capped at 4 MiB/s. Curl max-time and supervision
bound commands within 10,800s. RLIMIT_FSIZE/max-filesize bound output even without
Content-Length. Exact size/SHA verification precedes promotion. Failed partials
remain; existing finals/partials refuse repeats. Curl stderr is suppressed to avoid
signed URLs. Logs retain hashes/exit status. A reviewed retry needs a fresh --work
root; earlier failed work remains untouched.

Installation uses venv --copies and offline pip, a separate build venv, hash-locked
--no-index/--no-deps/--no-cache-dir/--no-compile/--only-binary installs, and a backend
wheel built from the fresh source copy using --no-build-isolation. No editable install, ambient packages or extra .pth is added. The exact
MOSS C++ root manifest is copied beside the installed audio_backend directory,
matching the product's package-relative lookup.

Extraction uses stdlib tarfile streaming. AudioSet retains model.onnx and labels;
SenseVoice retains model.int8.onnx and tokens.txt, all with fixed size/SHA checks.
Native retains 75 regular files and 31 directories, resolving 11 in-root aliases
into ordinary copies: 17,872,648 regular bytes become 31,003,744 bytes in 86 outputs.
The project installation.json contract uses asset-relative file rows and fresh
absolute model/library settings. Model/native manifests and vendor notice are
copied to the asset root. Unsafe paths, alias cycles, sparse/unsupported entries
and wrong inventory fail. Inputs remain retained. No native/model calls occur.

## Limits and completion

Setup uses three available CPU cores, nice 19, 6 GiB per-process address space,
and a sampled 6 GiB RSS threshold for driver plus current child process group.
Sampling is not a hard aggregate quota. Child groups are terminated and original
handles reaped on ordinary failure, interrupt and SIGTERM. Executor loss/SIGKILL
still requires external cleanup review.

Stage work clocks: download 10,800s; install/extract 1,800s; check 300s. A stage-wide
alarm also covers hashing and other driver work. Individual setup commands are
bounded to 180/600s; extraction to 1,740s with a tighter internal streaming limit.
Cleanup permits at most 10 further seconds. No retries.

Disk checks retain 12 GiB workspace maximum / 5.5 GiB free floor, 64 MiB source,
7 GiB assets, and combined 512 MiB runtime/build/temp caps. Finals, partials and
failures all count using max(logical, allocated). Checks are sampled, not disk
quotas. Offline command output is drained into at most 240 KiB per stage; excess
is discarded, and all log allocation is capped at 1 MiB. Curl output is never
captured. Positive command exit codes and completed asset hashes are retained.

Selected extraction projects 363,085,824 allocated bytes on 4 KiB allocation.
Compressed inputs plus extraction and full runtime/source/evidence allowances
are about 2.514 GB before rounding, under a 3 GiB planning envelope. Standard
venv/pip peak is unmeasured; fresh allocation must remain within the caps.

Successful check means all 38 asset hashes match. It does not establish imports,
decoder readiness, native execution, API/CellStore acceptance or model quality.
Fresh ordinary verification must use the actual current profile. Official Handy
Q8 SHA-256 is 64ec654dc6ffcfdfe180422dffce1d33422b0c30959b7edfd131bad77ee35039;
the older general MOSS manifest's locally quantized model is different.

This public snapshot is unexecuted. Syntax checks alone do not qualify the runtime.
