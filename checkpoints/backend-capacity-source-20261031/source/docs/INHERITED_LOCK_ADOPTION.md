# dev65: explicit inherited lock adoption (source candidate)

Status as of 2026-10-20: 79 selected model-free synthetic controls passed;
a separate offline wheel/install/migration smoke passed 1/1. The 137 product
files matched across qualified source, wheel and installation. These results
have different scopes and are not a full-suite or endurance qualification.
No real recording, model run, backend listener, uploader HTTP, restart, or
continuation is authorized or claimed by this publication. See the
[qualification record](../reports/INHERITED_LOCK_ADOPTION_DEV65_QUALIFICATION.md).
dev64 behavior and historical result artifacts retain their original scope.

## Opt-in interface

- Server CLI: `python -m audio_backend --data-dir CANONICAL_DATA --instance-lock-fd FD ...`
- Server factory: `create_app(settings, ..., instance_lock_fd=FD)`.
- Uploader CLI: `python -m audio_backend.recording_uploader --state-dir CANONICAL_STATE --uploader-lock-fd FD ...`.
- Uploader factory: `create_uploader(uploader_lock_fd=FD, final=False, **uploader_options)`.
  `final=True` selects the existing final-file uploader. Otherwise normal
  append/finalize selection and the exact existing v2/v3 policy dispatch apply.

These options have no environment-variable fallback and are default-off. They
are Linux/procfs-only. Omitting them retains the former lock acquisition timing,
including server construction before lifespan locking and uploader journal
construction before `run()` locking. **Only explicit inherited mode provides
pre-constructor/pre-mutation ownership.** No inference, worker, journal, schema,
resource, recovery, model, or recording policy is changed.

The server uses exactly `data_dir/instance.lock`; the uploader uses exactly
`state_dir/uploader.lock`. They must already exist. Adoption neither creates
these files/directories nor changes their bytes. CLI paths are checked before
Settings or uploader path normalization can conceal a symlink alias. A direct
server factory checks the `settings.data_dir` it receives; Settings may already
have normalized the caller's earlier spelling.

## Descriptor contract

`InstanceLock(path).adopt(fd)` accepts an integer descriptor at least 3 on a fresh
lock object. After taking ownership, it consumes that incoming descriptor on
success or failure. Invalid types, standard-stream numbers, or repeated adoption
are rejected without taking ownership. A caller must not concurrently close,
reuse, or unlock an adopted descriptor/open description.

The child duplicates with `F_DUPFD_CLOEXEC`, obtaining a non-inheritable owned
fd for the **same open file description**, then closes its incoming fd. It does
not substitute a reopened lock owner. Verification requires all of:

1. Existing canonical pathname, without leaf or ancestor symlink aliases.
2. Descriptor and path agree on device/inode/mode/link count; regular file with
   exactly one hard link; exact `/proc/self/fd` pathname, neither deleted nor
   renamed; read-only access, not `O_PATH`.
3. The descriptor's bounded Linux `/proc/self/fdinfo` already proves one
   whole-file `FLOCK ADVISORY WRITE` lock for that exact device/inode. PID is not
   used as authority because namespace views can differ.
4. A separately opened, same-inode read-only probe must fail nonblocking
   exclusive flock with EACCES/EAGAIN. Contention alone is insufficient: the
   supplied descriptor must itself own the already-held exclusive flock.
5. Nonblocking same-description verification, with repeated identity/fdinfo
   checks. The supplied incoming descriptor is also checked before closure.

Unavailable/malformed procfs, shared or unlocked descriptors, a different open
description contending with somebody else's lock, writable/O_PATH fds, wrong
inode/path, links, replacement, and unexpected probe errors fail closed. No
path uses `LOCK_UN`, even on errors. Only the caller's own descriptors close.
Lock-file bytes remain unchanged; reads may have filesystem atime effects.

## Parent versus child descriptor tables

A supervisor may open an existing canonical lock read-only, take `LOCK_EX |
LOCK_NB`, and transfer that fd into `adopt`. Its retained owner is then
`lock.file.fileno()`. It passes that descriptor only to the matching child using
`Popen(pass_fds=(fd,))` and the corresponding explicit flag. Popen leaves the
supervisor's fd open. The child's consumption closes only the child's entry;
the supervisor's entry and the child's duplicate still refer to the same open
description. No extra parent-side duplicate is required for this Popen flow.

If code calls `adopt` in the **same process** and wants to preserve its existing
fd too, it must instead pass `os.dup(existing_fd)`. Passing the original in that
case intentionally transfers and consumes it.

The supervisor must retain both role owners continuously, never `LOCK_UN`,
through startup, readiness, operation, shutdown, descendant cleanup and positive
`wait()` of each original child handle. Parent-only locking followed by release
and child reacquisition is not an atomic handoff. Lock availability, pidfiles,
PID absence, a log line, or session loss is not positive child completion.

## Initialization, shutdown and descriptor containment

Server CLI adoption precedes Settings/manifest construction; factory adoption
precedes directory creation, Store/schema initialization and optional stores.
The CLI guard and app owner remain held through uvicorn. Constructor failures
close only their owned duplicates. Existing lifespan recovery checks remain.

Uploader factory adoption precedes policy dispatch and all constructors. Both
journal implementations verify the exact inherited owner before even
`mkdir(exist_ok=True)`. v1, legacy v2 and v3 `run()` revalidate rather than
reopen/reacquire. Decode/source cleanup finishes before the child duplicate is
closed. Constructor/limit failures close only the factory-owned duplicate.

Unresolved inherited cleanup keeps a process-lifetime strong reference to the
app/uploader, including BaseException interruptions and discarded temporary
callers. Server anchoring begins at successful lifespan acquisition and ends
only after completed cleanup. The CLI separately retains an incomplete app if
uvicorn returns or raises. This is fail-closed retention, not a cleanup success
or retry authority. A direct caller that constructs an app/uploader but never
starts it is responsible for closing its lock without `LOCK_UN`.

Only role-specific incoming fds are passed into server/uploader processes. Their
incoming descriptors are consumed, retained duplicates are CLOEXEC, and existing
inference/decoder launch paths receive no lock `pass_fds`. Tiny exec controls
explicitly test a descendant with `close_fds=False` to verify CLOEXEC itself.
A raw fork without exec still duplicates fd tables; this interface does not
claim general fork isolation or permission to unlock a shared description.

## Scope and limitations

These are cooperative advisory locks, not a security boundary against an actor
who renames control paths, ignores flock, or explicitly unlocks the shared open
description. Identity checks detect observed changes but cannot prevent hostile
filesystem races. Support depends on Linux procfs and the actual filesystem's
flock semantics. Existing non-inherited startup is unchanged and does not gain
this early ownership guarantee.

The selected executed tests were model-free, temp-only constructor/lifespan
and tiny OS-child controls. They do not qualify a real continuation, production lifecycle,
throughput, inference descendants under all launch paths, or acoustic behavior.
Any actual supervisor run still needs its own reviewed, qualified launch and
ownership evidence. This publication includes source and a bounded result
summary, not private run artifacts, retained data, or permission to continue
an actual recording.
