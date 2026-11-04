# Strict backend foreground supervisor

`python -B -m audio_backend.strict_supervisor check|serve ...` is the product
launcher for one Linux strict CPU backend. It runs from this checkout or an
installation of this package. It does not depend on an old private workspace,
a qualification harness, a pre-created session, or an externally supplied fd.

This addition is **model-free process/control-plane tested**, not a new model,
acoustic, throughput, target-server, or end-to-end deployment qualification.
The checked-in runtime admission pins are unchanged. An ordinary Python install
on a new server will normally be refused until that exact interpreter, SQLite
closure and filesystem have independently qualified product evidence. Copying
an observed fingerprint into an allowlist is not qualification.

## Scope and prerequisites

- Linux with working procfs, `flock`, `waitid(WNOWAIT)` and child-subreaper support.
- Use the intended Python runtime and install the package and its runtime
  dependencies there. From a fresh clone, the package root is this `source`
  directory. The launcher imports the same exact package root in the child and
  inference descendants; no prior workspace paths are embedded in the product.
- An operator-provisioned **existing canonical absolute private data directory**,
  owned by the service user (normally mode `0700`), and an existing single-link
  regular `instance.lock`, owned by that user (normally mode `0600`). Neither
  command creates, truncates, chmods, replaces or deletes the lock or directory.
- Local provisioned SenseVoice, speaker, AudioSet and Nemotron model/runtime
  assets. The launcher checks canonical file metadata, not their contents,
  model compatibility, licenses, inference output or executable trust. Supply
  the actual resolved library file rather than a SONAME symlink. Existing
  product runtime/model gates still apply during normal operation. Review the
  model licenses and approved asset provisioning process before serving.
- `--language` and `--profile strict-primary-payload-v2` are mandatory. Choose
  `--strict-payload-source-storage shared-source-v1` explicitly for the accepted
  capacity configuration. `inline` is the existing product alternative; selecting
  it does not transfer the shared-source capacity evidence to that configuration.

For a **new empty** deployment directory only, an operator can provision it
without truncating an existing lock:

```sh
DATA=/srv/audio-backend/private-data
install -d -m 0700 "$DATA"
(umask 077; set -C; : > "$DATA/instance.lock")
```

Do not repeat initialization as a repair strategy. Existing data must already
have the matching strict policy, capacities, source-storage choice and compatible
models. Changing directories or replacing a lock to bypass contention is unsafe.

## Read-only check, then an explicit serve

Use real absolute canonical paths appropriate to this server:

```sh
PYTHON=/path/to/qualified/python
DATA=/srv/audio-backend/private-data
MODELS=/srv/audio-backend/models
NATIVE_MODEL=/srv/audio-backend/native/Nemotron-3-Diarization.q8_0.gguf
NATIVE_LIBRARY=/srv/audio-backend/native/libnemo_speech_asr_c.so

"$PYTHON" -B -m audio_backend.strict_supervisor check \
  --data-dir "$DATA" --model-dir "$MODELS" \
  --native-diarization-model "$NATIVE_MODEL" \
  --native-diarization-library "$NATIVE_LIBRARY" \
  --recording-speaker-model "$MODELS/speaker.onnx" \
  --language en --profile strict-primary-payload-v2 \
  --strict-payload-source-storage shared-source-v1
```

A check acquires the existing lock read-only and uses
[`strict_runtime_preflight`](STRICT_RUNTIME_SETUP_ZH.md). Runtime refusal occurs
before asset inspection, database construction, worker/model initialization,
child creation or listener startup. Successful checks additionally stat the
required model files, but never load them. No target database is opened or
created. Lock bytes are unchanged; normal read/metadata atime effects may occur.

Only `status: preflight_passed` with exit status `0` passes this preflight. Exit
`2` means the existing runtime qualification did not match. Exit `1` means a
path, ownership, asset metadata or other prerequisite was refused. This is not a
readiness check: no models or service have started, disk capacity is not proved,
and existing database compatibility has not been tested. A passing report is
not reusable authorization; `serve` repeats the check under a fresh held lock.

To start the real service, replace only `check` with `serve` in the same command.
This is a consequential operation: the existing backend may initialize or
reconcile databases and process queued work. The supervisor creates no session,
upload, recording, retry, inference request or external device connection itself.
Do not serve an old directory merely to inspect it. Use a dedicated terminal or
service manager and keep the supervisor in the foreground.

The default endpoint is `127.0.0.1:8765`; `--host ::1` is the only alternative
host. Nonloopback binds and arbitrary child arguments are not supported. Set
`AUDIO_API_KEY` in the service environment if desired; it is passed to the
backend, never printed by the supervisor. Other `AUDIO_*` configuration is
ignored by the child so the explicit profile cannot silently inherit it.
Python import paths are controlled for the child and its normal inference
launch paths. The launch message means **a launch attempt**, not that the backend,
native cell store or worker is ready. Check the backend's normal health/status
surfaces before submitting work; this launcher makes no HTTP requests.

## Explicit configuration

The supervisor passes the existing strict clean-primary SenseVoice/Nemotron CPU
profile, speaker support, bounded native core support, lexical retention and
speaker links. It keeps MOSS, source garbage collection and overlap backends
disabled. Default capacity settings match the accepted capacity configuration:

| Setting | Supervisor value |
| --- | --- |
| Window / context | 5.25 / 0 seconds |
| Primary sampling | `skip-bucket-v1` |
| CPU / native threads | 2 / 2 |
| Process-tree memory setting | 6 GiB |
| Retained recording capture / total | 4096 / 8192 MiB |
| Recording free-space floor | Existing 5.5 GiB |
| Lexical evidence per session | 256 MiB |
| Lifetime / receipt records | 32768 / 8192 |
| Speaker support record cap | 65536 |

`--help` lists bounded thread, memory, retained recording, lexical, lifetime,
receipt, port and shutdown-grace options. The selected values must agree on
reopen. Changing them is not a new capacity qualification. These are existing
product budgets and cooperative guards, not an OS-level RAM/CPU limit. The
ordinary `audio_backend` CLI retains its prior defaults; only two pass-through
options, `--context-seconds` and `--speaker-support-max-records`, were added to
express this existing profile.

### Optional reviewed primary person bank

The supervisor keeps reviewed person APIs disabled unless **both**
`--reviewed-primary-persons` and `--verified-identity-policy /absolute/canonical/policy.json`
are explicitly supplied to `check` or `serve`. Supplying only one is refused.
Use an existing, nonempty, single-link regular policy file without symlinks.
After runtime admission, preflight uses the same bounded 8 KiB, exact-schema
experimental policy validator as the product Settings. It accepts the existing
supported policy schemas without changing their thresholds or choosing one for
the operator. The shipped example is
`audio_backend/profiles/verified-review-experimental-v1.json`; pass its real
absolute canonical path if that is the intended policy.

Successful check output reports `reviewed_primary_persons_enabled` and
`verified_identity_policy: validated_configuration_only` (or `not_requested`
when disabled). `person_bank: not_opened_or_activated` is literal: no database,
bank, person, association or reference is created or inspected. These fields
report configuration, not bank availability, identity accuracy or model quality.
The child receives the same two flags on explicit `serve`; all strict runtime,
lock, CPU, profile, source-storage and capacity fences remain unchanged.

Before enabling, read the [reviewed person schema and downgrade precautions](REVIEWED_PRIMARY_PERSONS.md).
Serving with this opt-in can install the existing guarded schema; it does not
activate a bank, enroll voices or associate a person automatically. Existing
explicit bank activation/review APIs and immutable policy rules still apply.
This launcher change adds no recognition, enrollment or quality qualification.

## Ownership and shutdown

1. Open the existing lock read-only and take exclusive nonblocking `flock`.
   Adopt it with the existing product `InstanceLock` validator. No `LOCK_UN`
   operation occurs. Busy ownership fails immediately, with no retry.
2. Keep that original open description continuously held through preflight,
   launch, service operation and cleanup. Pass only its fd to one child. The
   child consumes its inherited entry; the supervisor retains its original.
3. Start the child in a new session, with Linux subreaper ownership. The
   supervisor must be a dedicated process without unrelated existing children.
4. SIGINT/SIGTERM request graceful shutdown of the original child. Repeated
   signals do not interrupt cleanup or release the lock. After the grace period
   (default 90 seconds), kill the owned child group and remaining descendants,
   including descendants that created another session. There is no restart.
5. Observe the original child's exit with `waitid(..., WNOWAIT)`, retaining its
   unreaped PID while adopted descendants are killed/reaped. Release the lock
   only after the original `Popen.wait()` has positively returned and there are
   no remaining owned descendants. A log line, PID disappearance or free lock
   is never used instead of that wait.

Ordinary child exit codes propagate. A zero-exit leader whose remaining
descendants require forced termination returns `1`, not a success status. A child terminated by a signal returns
`128 + signal`; an operator SIGINT/SIGTERM request returns `130`/`143` after
confirmed cleanup. Startup failure causes no automatic restart. No graceful
completion or durable success is inferred from a forced kill.

If child observation or positive wait fails, the supervisor reports unresolved
ownership and **stays alive retaining the original lock**, even after additional
SIGINT/SIGTERM. An operator must inspect the process tree and retained state
before recovery. Do not remove the lock file. SIGKILL, an OS crash, PID-namespace
teardown or a malicious actor can terminate the supervisor; this userspace tool
cannot promise ownership after its own forced death. Advisory locks and the
cooperative process model are not a hostile-process security boundary.

See [inherited-lock contract](INHERITED_LOCK_ADOPTION.md) for fd validation and
[read-only runtime preflight](STRICT_RUNTIME_SETUP_ZH.md) for qualification
boundaries. No runtime pin, re-attestation override or self-qualification flag
is exposed here.
