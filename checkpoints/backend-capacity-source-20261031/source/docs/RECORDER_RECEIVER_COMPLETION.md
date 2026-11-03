# Trusted receiver completion lifecycle

`audio_backend.recorder_receiver_completion.ReceiverCompletion` is an opt-in,
server-neutral lifecycle adapter above the existing strict publication boundary.
It opens no socket, installs no service, creates no credentials, performs no
HTTP/intake/inference, and changes no Android wire format. It does not turn
filesystem observations into a transfer-completion claim.

## Integration contract

An actual receiver must authenticate the connection and authorize its account
for the configured raw namespace **before** invoking this API. The adapter's
explicit frozen receiver binding identifies local state, staging and inbox
paths. It is not principal authentication, device identity or provenance.
Lifecycle methods and correlation objects are trusted internal receiver calls,
never client-supplied JSON, marker fields or remote commands.

The integration must route **every** writable operation in this namespace
through the adapter. It replaces the attempt-directory creation and write-open
hooks, not merely the final transfer callback:

1. Construct one adapter with the frozen binding from the existing publisher
   configuration and the exact inbox. `exclusive_receiver=True` acknowledges
   exclusive ownership of those namespaces.
2. When the receiver authorizes a new attempt-directory creation, call
   `begin_attempt(original_attempt_name)` **instead of** creating that directory
   first. This atomically creates and fsyncs a fresh private directory and returns
   an in-process attempt handle. Existing directories are never adopted, even
   if empty or fully valid.
3. Before opening or truncating a file for STOR, call
   `open_writer(attempt, filename)`. Return `writer.stream` from the receiver's
   filesystem-open hook. Do not also perform the server's ordinary write-open.
   Only `audio.wav`, `segment.v1.bin`, and `delivery.v1.txt` are permitted. One
   writer per file is admitted for the entire attempt lifetime, using `O_EXCL`.
4. The receiver writes through this unbuffered, bounded stream, closes it, then
   calls `writer_closed(writer, successful=True)` only from its real successful
   transfer callback. The exact stream must already be closed. Incomplete or
   failed transfers use `successful=False`, after closing their stream. EOF,
   ABOR, disconnect and exception paths are not automatically successes.
5. The final of the three successful closures automatically invokes the strict
   publisher. The order of opens and closures does not matter. A marker callback
   alone, completed-looking bytes, a quiet interval, or a closed stream without
   its successful callback cannot trigger publication.

The stream provides `name` (canonical full path), `mode`, `write`, `flush`,
`fileno`, `tell`, `closed` and `close`, plus context-manager support. It rejects
seeking. Reject APPE, REST/resumed uploads, STOU, raw-file rename/delete, descriptor
duplication/transfer and other bypasses rather than mapping them to success.
`fileno` is for receiver inspection/fsync, not alternate write or seek access.
Read-only retrieval can use the receiver's ordinary confined filesystem path.
Convert adapter refusals into the receiver's controlled transfer-failure reply;
do not silently fall back to an ordinary file open.

```python
from audio_backend import recorder_receiver_publication as publication
from audio_backend.recorder_receiver_completion import ReceiverCompletion

with ReceiverCompletion(
    binding=publication.bind('/private/receiver'),
    inbox_dir='/private/inbox',
    exclusive_receiver=True,
) as receiver:
    # In the authorized MKD handler, instead of the normal mkdir:
    attempt = receiver.begin_attempt(original_attempt_name)
    # In the authorized write-open hook, before any file open/truncation:
    writer = receiver.open_writer(attempt, 'audio.wav')
    # Return writer.stream to the receiver's ordinary data transfer machinery.
    # After that machinery has closed it, from the actual success callback:
    result = receiver.writer_closed(writer, successful=True)
```

This is an integration sketch, not an FTPS server. The receiver maintains the
mapping from its actual file transfer to the returned writer object. It must
never reconstruct a writer from an untrusted filename or confuse callbacks
between simultaneous connections. The successful close is checked against the
exact file inode and final closed-file stamp. Every earlier successful file is
checked again before publication.

## States, concurrency and failure

- `receiving`: zero to three registered writers. `pending_writers` includes
  streams already closed but lacking a trusted completion callback.
- `failed`: a failed transfer, conflicting callback, changed closed file or open
  failure prevents publication. Existing live writers must still close and may
  report failure; new opens are refused. Preserved raw bytes are not repaired.
- `publishing`: all three exact streams are closed with successful callbacks.
  The admission lock is held through strict publication. Concurrent new opens
  cannot race this transition; already admitted unrelated raw streams can
  continue receiving. The publisher still detects observed source mutations.
- `published`: the existing publisher returned success. The result contains its
  full report under `publication`. All later opens for that attempt are refused.
- `uncertain`: publication raised, including an exception after its atomic
  rename. No retry is scheduled. A published path may exist even though this
  callback did not report success.

Identical duplicate callbacks return the current cached status without
republishing, including an explicit `uncertain` result. Contradictory callbacks
are refused and poison a still-receiving attempt. Unknown or cross-instance
handles cannot contribute successful closures. A callback before stream closure
is refused without treating the transfer as complete. The receiver must inspect
`status`; a callback returning successfully does not imply publication.

A process-lifetime exclusive `flock` on the configured staging directory inode
prevents two adapters from using independent writer registries for the same
namespace, even with different receiver state configurations. The original
publisher configuration remains unchanged. Threads share one bounded admission
mutex. Adapter and stream operations reject use after fork; closing in a child
cannot unlock the parent's lease. Use the context manager or `close()` for
normal shutdown. Shutdown closes outstanding streams as unsuccessful, performs
no publication, then releases the owner descriptor. A close error does not skip the other
streams: cleanup attempts every stream and reports the error. If any stream
remains open, ownership stays held and a later `close()` can retry cleanup. If
all streams closed despite an error, the lease is released but the error is still
reported. Do not fork or transfer file
descriptors as part of this integration.

The lock is advisory. Nonparticipating writers, a malicious privileged process,
or a trusted integration that bypasses the owned stream are outside the
exclusive-ownership contract. They cannot be authenticated or prevented by this
module. Observed raw mutation during the existing strict snapshot/publication
still fails. A successful publication is an independent copied inode tree, so
subsequent raw STORs cannot mutate it. Never invoke the legacy explicit publisher
as an alternate completion path while this receiver is active.

## Restart, unknown outcomes and resource limits

No lifecycle success journal is replayed. A fresh process inventories retained
raw files **only to account for resource use**; it does not infer their transfer
state. Existing attempt paths cannot be registered again, including interrupted,
empty, failed, successful and uncertain attempts. Old callback objects are
invalid. A sender must use a genuinely fresh attempt nonce under its existing
retry protocol, or an operator must investigate the preserved evidence. The
adapter has no cleanup, repair, retry, rebind or adoption API.

If an operation fails after publication's rename, do not delete or overwrite the
visible destination. The older low-level publication API's explicit operator
reconciliation remains separate, with its documented exact-source validation
and fsync checks. The lifecycle adapter never invokes it automatically after an
uncertain result or restart.

Bounds apply across process restarts because all retained raw directories count:

- At most 256 retained raw attempts, configurable downward with `max_attempts`.
  Attempts are never evicted from the lifetime registry to reuse a name.
- At most 8 GiB raw file bytes, configurable downward with `max_raw_bytes`.
  The constructor performs a bounded stat-only scan of retained files, rejecting
  unexpected names, links, unsafe permissions and oversized files.
- Each write-open reserves that file's entire existing format limit before any
  file creation. Streams reject oversized writes before disk mutation. A
  validated successful closure releases the unused reservation. Failed or
  uncertain streams may retain a conservative reservation until shutdown.
- Marker and metadata use the existing format limits; WAV is at most 512 MiB.
  There are at most three stream objects per admitted attempt. Callbacks and
  publication share the configured finite event deadline (at most 120 seconds).
  Kernel file operations remain subject to the underlying filesystem's behavior.

The existing publisher's separate 8 GiB aggregate budget across ready and
preparation trees, 256 preparation/inbox limits, disk headroom, strict hashes,
producer metadata validation, inode checks, no-replace atomic rename, fsyncs and
runner inventory coordination remain unchanged. Raw and publication budgets are
separate; their combined file-byte ceiling is 16 GiB, plus filesystem overhead.
A receiver operator must arrange authorized retention before these limits are
reached. This adapter never removes evidence to make space.

Local unit/process tests prove only the server-neutral boundary. Real callback
wiring needs separate qualification of the chosen receiver's open, success,
incomplete and disconnect hooks. Neither those tests nor a synthetic FTPS
fixture establish a deployed authenticated receiver, phone lifecycle correctness
or native ASR quality.

## Actual callback qualification

The [independently audited real FTPS callback run](../qualification/receiver_callback_20261103/RESULT.md) passed on its first live launch. Actual success/incomplete callbacks drove automatic publication and failure refusal while the same inbox watch remained active. It also verified sealed-write rejection, duplicate callbacks and crash/restart fences. This proves the reviewed fixture integration, not a production receiver installation, authentication architecture, device behavior or native ASR.
