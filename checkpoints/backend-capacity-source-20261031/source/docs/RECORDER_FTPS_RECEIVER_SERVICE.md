# Explicit FTPS receiver process

`python -m audio_backend.recorder_ftps_receiver serve --config /private/receiver/service.json`

This is an opt-in, single-process Linux receiver around `ReceiverCompletion`.
It does not install a service, create credentials/certificates, open a firewall,
contact a recorder, run inference, ingest an inbox, select retries, or delete
retained data. It uses only an already configured publication binding. Its
ordinary STOR writes always go through the owned completion adapter.

**Deployment boundary:** loopback is the default and the tested network scope.
A trusted, isolated private network can be explicitly configured, but public
internet and hostile/shared-NAT deployment are not qualified. Control login is
authenticated and both channels require TLS 1.2+, but pyftpdlib 2.1.0 binds a
passive data peer to the control peer by IP, not by TLS session or client
certificate. Another process sharing that source IP can race a passive
connection. This wrapper does not claim protection from that attacker or a
process controlling the same OS account. TLS and content hashes do not establish
physical-recorder provenance.

## Existing operator-provisioned inputs

Use a dedicated, unprivileged account and private directories outside any Git
checkout. The service refuses public/multi-link/symlink secret files, keys and
config. Config and password/key files must be owned by the service account,
mode 0600 (or stricter), with private 0700 parents and canonical absolute paths.
Certificate chain and private key must already exist and match. The client must
trust the certificate chain and verify the selected server hostname/IP; this
service never disables client verification or creates trust material.

Install the optional, pinned `requirements-receiver.txt` in a separate service
virtual environment using the operator's normal package-management process.
The wrapper refuses a different pyftpdlib or pyOpenSSL version because its
receive-order safety depends on those exact implementations. Do not silently
upgrade them without rerunning the wire and independent review tests.

Use the existing explicit publication setup on fresh, disjoint private trees:

```sh
python -m audio_backend.recorder_receiver_publication configure \
  --state-dir /private/receiver/state \
  --staging-dir /private/receiver/raw \
  --inbox-dir /private/receiver/inbox --exclusive-receiver
```

The raw and inbox directories, and their private parent, must already exist;
state must be absent and inbox empty. This setup is not performed at service
startup. Retain the returned state, and obtain its frozen binding with
`recorder_receiver_publication.bind(Path('/private/receiver/state'))` in local
Python. Preserve both fields in config rather than silently rebinding on
restart.

Prepare this JSON privately, replacing every placeholder with existing values.
The password file contains the existing account password as UTF-8, optionally
terminated by one LF. There are no built-in account credentials. Do not place a
password in this JSON, shell arguments, reports, logs or source control.

```json
{
  "schema": "recorder-ftps-receiver-config-v1",
  "binding": {
    "state_dir": "/private/receiver/state",
    "config_sha256": "EXACT_BINDING_SHA256"
  },
  "inbox_dir": "/private/receiver/inbox",
  "cert_file": "/private/tls/existing-server-chain.pem",
  "key_file": "/private/tls/existing-server.key",
  "username": "EXISTING_ACCOUNT_NAME",
  "password_file": "/private/receiver-secrets/existing-password",
  "bind_host": "127.0.0.1",
  "port": 2121,
  "passive_ports": [30000, 30007]
}
```

The FTPS account's remote base directory is `/`, mapped exactly to the bound raw
staging directory. Configure the recorder's profile with `/`; do not use the old
qualification fixture's `/fixture` path. Each new attempt must be MKD-created by
the same authenticated control session before STOR. Only the three canonical
filenames are writable, once each. Only binary, TLS-protected passive transfers
are accepted. Anonymous login, active data, plaintext data, ASCII mode, resume,
append, unique-store, rename, delete, chmod and directory listings are disabled.

For non-loopback bind, explicitly set `bind_host` to a numeric IP plus both
`"allow_non_loopback": true` and `"acknowledge_data_peer_ip_only": true`.
These flags acknowledge the deployment boundary above; they do not add peer
binding or establish network qualification. No NAT masquerade configuration is
provided. Configured passive ports fail closed when exhausted; no out-of-range
fallback listener is opened. Omit `passive_ports` to let the kernel allocate
bounded ephemeral passive listeners.

## Check, run, stop

```sh
python -m audio_backend.recorder_ftps_receiver check --config /private/receiver/service.json
python -m audio_backend.recorder_ftps_receiver serve --config /private/receiver/service.json
```

`check` verifies configuration, private files and frozen publication binding
without opening a socket. Its result explicitly says `tls_material_loaded:false`:
it does not promise that the certificate/key parse, match or are trusted by a
client. `serve` additionally checks dependency versions, loads/matches TLS
material, takes the adapter's exclusive staging lease, and then binds. No
service is started by importing the module. Do not run multiple processes or
threaded/multiprocess FTP server variants over this state.

SIGINT/SIGTERM request an orderly stop. Open and queued uploads are closed as
failed; the adapter lease is released only after its streams close. A hard kill
or power loss preserves raw bytes but loses process-local completion handles.
Restart counts retained attempts/bytes and never adopts them as successful or
replays publication. After a configuration/runtime/publication error, CLI exits
nonzero with a fixed diagnostic code. Inspect state explicitly; do not configure
an automatic restart/retry loop to hide an uncertain publication.

Only fixed lifecycle/status codes are logged. Library command/path/account and
traceback logging is suppressed. A control connection has at most one pending
transfer. For uploads, data EOF must carry a genuine TLS `close_notify` while the control
session remains live. Full bytes followed by unclean EOF, ABOR, QUIT, control
disconnect, or receiver shutdown do not become successful completion.

For uploads, the exact adapter stream closes and its outcome settles **before**
the transfer's 226 response. The final successful file publishes only after all
three streams completed and strict delivery validation passes. A publication
failure returns 451, terminates normal service processing and is never retried.
An error may occur after publication rename, so 451 is not proof of absence;
operator inspection remains required. A lost 226 is similarly uncertain.

The Android uploader requires RETR readback. Current-session readback is allowed
only for its successfully closed streams, bounded to the original file size.
On reconnect, readback uses only an already published immutable inbox copy of
the **exact requested attempt and filename**, never a different retry or a raw
completion guess. Failed/incomplete/unpublished raw attempts cannot reconcile
by readback. Published readback does not trigger intake or republish anything.

## Finite budgets, no unattended retention

Defaults: 8 simultaneous library connections (including data/listeners), 4 per
IP, 256 retained attempts, 8 GiB raw-file reservation budget, 2 GiB readback
reservation per control session, 60-second idle/stall timers, 3600-second total
control-session lifetime, 10,000 commands per session, and a 30-second cooperative
publication deadline. A WAV is limited to 512 MiB by the underlying format.
Idle/STOR/readback/session quotas are deliberately finite, not a throughput or
long-duration qualification.

Optional config fields lower/tune these within hard bounds: `max_connections`
2–32, `max_connections_per_ip` 1–16 and no greater than total,
`max_attempts` 1–256, `max_raw_bytes` and `max_readback_bytes` 1–8 GiB,
`idle_seconds` 1–300, `session_seconds` 1–86400, `max_commands` 10–100000,
and `publication_seconds` 1–120. Configured control/passive ports must be
unprivileged; passive range is at most 128 ports. Unknown/duplicate keys and
invalid booleans/numeric bounds refuse rather than being ignored.

The adapter reserves each file's maximum before opening it and charges retained
bytes on restart. Readback charges the entire requested file before returning
bytes, including aborted reads. Existing publication storage/inbox/preparation
bounds also apply independently. Raw, immutable inbox and interrupted preparation
copies can all consume disk. The underlying publisher's disk-headroom checks remain in place. This service
adds no reserved-capacity guarantee, storage cleanup, retention scheduler,
alerting service, or automatic recovery. Disk-full errors fail closed. An operator must provision capacity and inspect retention before
resuming. With one attempt per 300-second segment, 256 attempts cover at most
21h20m before retries; the byte budget or publication bounds may stop earlier.
This is not an indefinitely running unattended receiver.

## Verification scope

Run the focused model-free gate with a separate environment containing test
requirements and `requirements-receiver.txt`:

```sh
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_recorder_ftps_receiver.py \
  tests/test_recorder_receiver_completion.py \
  tests/test_recorder_receiver_completion_review.py
```

`tests/test_recorder_ftps_receiver_android.py` is an explicit optional host test.
Set `ANDROID_RECEIVER_SOURCE` to the recorder source checkout to compile its
unchanged production `FtpsTransfer` and `FtpsNetwork` with Android API fixtures.
Only the host fixture's configured remote profile is adapted from `/fixture` to
`/`. It verifies actual TLS transfer/readback, genuine close notification,
publication and resource release without a phone or model inference. Without
that environment variable it is explicitly skipped, not counted as compatible.
This does not qualify Android device behavior, a LAN/WAN, a public server,
end-to-end acoustic quality, long-duration performance, or automatic intake.

## Observed source verification, 2026-11-04

The explicit service + Android host smoke + existing completion/independent
completion regression gate passed **82 tests** in 13.15 seconds, with no model
inference or non-loopback listener. Two expected Python deprecation warnings
came from the negative TLS 1.1 handshake test. The service and host-client
portion accounts for 46 of those tests.

The actual host test transferred one 16,428-byte synthetic WAV with its metadata
and marker using unchanged production Android transport sources, verified all
required readbacks, published the exact attempt, and closed all raw sockets.
Only the host fixture's profile path was adapted to `/`; Android API stand-ins
were used, not a phone. The independent reviewer separately passed the earlier
43-test service/host set against the same unchanged runtime module before the
three extra negative controls were added.

Runtime module SHA-256:
`bc9be5ba6b9cf5b4acee4b11ec15c874240c2069d62005b9d296a98be6a859b4`.
This is a focused source gate, not a full backend regression or deployment claim.
