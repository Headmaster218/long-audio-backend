# Prepare an inactive receiver generation

This opt-in command prepares new empty receiver storage and a service configuration. It does **not** rotate a running receiver, switch an endpoint, start a listener, create a backend session, change an Android profile, select recordings, or remove any originals/results. It is not an unattended continuous-recording solution.

```sh
python -B -m audio_backend.recorder_receiver_generation \
  --old-config /private/receiver/old-service.json \
  --root /private/receiver/next-generation \
  --port 2222
```

The new root must not exist and must not be nested inside an earlier prepared generation root. Use sibling roots so the old generation retains its exact directory roster. Its parent must be a canonical private directory outside repositories. The old service configuration and publication binding must validate, and the old receiver and publisher must be stopped. Nonblocking ownership locks refuse busy state without waiting or disrupting transfers. Old raw files, inbox, publication state, service configuration, runner jobs and exports stay at their exact original paths. The command does not inspect or certify the contents of old deliveries.

The new control port must differ from the old one. The bind address, credential/TLS **file references**, username, opt-ins and validated limits are inherited without generating or printing credentials. Fixed passive ports must not overlap either old control/passive ports. If the old configuration has a fixed passive range, explicitly choose a separate range:

```sh
python -B -m audio_backend.recorder_receiver_generation \
  --old-config /private/receiver/old-service.json \
  --root /private/receiver/next-generation \
  --port 2222 --passive-ports 31000 31015
```

This validates configured port independence only. It makes no network requests and does not prove port availability, firewall routing, certificate suitability, disk capacity, server readiness or recording continuity. Ephemeral passive ports remain OS-selected. Inherited limits are unchanged, including default limits omitted from the old configuration. A new generation does not increase a single receiver's limits.

## Outputs and interruption

The root contains private `intent.json`, `raw/`, `raw.json`, `inbox/`, `inbox.json`, `publication/`, `service.json` and `complete.json`. The intent binds the exact old configuration bytes, old publication binding, new root and port arguments before component creation. Directory identity receipts and the final completion receipt are local private controls, not credentials. The service configuration contains existing credential file references and the username, just like its source configuration; do not publish it.

Use exactly the same arguments plus `--resume` after an interruption. Resume revalidates the old configuration and only accepts positively verified complete components, including an otherwise fully completed inactive generation. It does not overwrite or repair partial files, adopt an unreceipted directory, reconstruct missing earlier components, accept extra files, or resume a generation that has begun receiving data. A crash after directory creation but before its identity receipt, or partway through publication setup, intentionally requires operator review rather than automatic repair. All uncertain files remain untouched. Even a complete receipt is not permission to start a service after an unsuccessful command; use a successful exact-argument resume to verify the inactive state.

Exit 0 means preparation verified; exit 2 means refused; interruption returns 130. Standard output contains fixed redacted status fields, never filenames, audio, credentials or exception details. No deletion or deletion-safety claim is made.

## Separate destination, not transparent rollover

Keep the original endpoint route available for old queued attempts and reconnect/readback. Android retains queued attempts against their saved destination revision. Replacing the old endpoint's root would break their `CWD`/`RETR` reconciliation even if old files remained elsewhere. This tool never performs that replacement.

Changing Android host, port, username or directory creates a different destination revision. Enabling automatic delivery for that destination can scan retained historical sources again and queue them under the new revision, duplicating old uploads. Preparing a new port is **not** a duplicate-free next-batch transition. The operator must explicitly consider the new destination's historical rescan scope; this command does not edit profiles or queues or claim to solve that product limitation.

Existing runner jobs remain bound to their old inbox/publication state and exact originals. Do not move them. A new selected span requires its own explicit existing runner initialization workflow. Storage capacity and file rotation are not proof of a recording end, and this tool does not synthesize EOS or split an ongoing recording. Use the stopped capacity-status command separately against each explicit service configuration; no implicit newest-generation discovery or aggregation is performed.

Old queued items that have not started still require writes at the original
endpoint; making it read-only is not sufficient to drain them. Existing attempts
need their original authentication and namespace continuously available. Android
may permanently block a queued attempt on 530/550 protocol errors, and profile
count is bounded; repeatedly changing profiles is not an indefinite strategy.
