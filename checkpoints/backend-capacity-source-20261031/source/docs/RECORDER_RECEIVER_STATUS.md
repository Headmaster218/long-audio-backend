# Stopped receiver capacity status

Before resuming a finite recording collection, inspect retained capacity with the
existing private receiver service configuration:

```sh
python -B -m audio_backend.recorder_receiver_status \
  --config /private/receiver/service.json --deadline-seconds 10
```

This separate product command does not start the receiver, connect to anything,
open audio content, adopt transfers, repair state or remove files. It validates
configuration using the service's existing loader, including reading existing
private secret files, but never prints credentials, paths or attempt filenames.
It uses the actual configured raw attempt/byte limits, not a guessed default.

The supported observation is **stopped receiver only**. The command immediately
tries shared nonblocking ownership and publication locks. If a receiver or
publisher owns the namespace, it returns `busy` and exit 2 without waiting,
scanning transfers or interrupting the process. Stop normally before retrying;
this command never stops it for you. During a successful scan, cooperating new
receiver/publisher processes cannot start writes. A concurrent receiver start can
therefore receive its normal ownership refusal; status does not promise live
monitoring or transparent concurrent startup.

A successful `observed` result (exit 0) reports:

- Raw retained attempt count and logical file bytes, configured limits and
  remaining attempt/byte budgets. Partial and failed raw attempts count too.
- Published inbox entries, retained preparation entries, their combined logical
  file bytes and remaining publication/preparation budgets.
- Exhausted limits and whether the raw budget cannot reserve the largest allowed
  file. The receive adapter reserves each requested file's maximum before open,
  so some apparently free bytes may not permit a WAV transfer.

Publication counts use the existing 256-entry inbox/preparation bounds and shared
8-GiB retained-file budget. Raw accounting uses the completion adapter's exact
retained-file checks. Two bounded metadata passes and identity rechecks refuse
detected changes, symlinks, unexpected members, nonprivate/multilink files or
oversized/over-capacity trees. `changed` indicates a detected observation change;
`refused` indicates validation or budget failure. Both exit 2 and omit counts.
A 1–120-second cooperative deadline bounds ordinary work, not a stalled kernel
filesystem call. File reads may change access times; existing locks are acquired
and released without modifying their bytes. No lock or state file is created.

This is capacity accounting, not delivery validation or a next-upload guarantee.
It does not hash WAVs, inspect completion markers, measure filesystem allocation
or disk free space, include backend/runner storage, or prove that content is
safe to delete. Empty preparation entries still consume entry capacity. Available
budgets do not prove TLS readiness, client connectivity, primary completion,
retention safety or sustained throughput. Respect selected originals and readback
requirements; do not use these counts as permission to delete or rotate data.

Run the dedicated model-free controls with `tests/test_recorder_receiver_status.py`.
