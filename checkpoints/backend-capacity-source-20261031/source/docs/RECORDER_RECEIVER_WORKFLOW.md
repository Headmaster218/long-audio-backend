# Explicit receiver-to-intake workflow

Use this workflow only with an already configured, trusted local FTPS receiver directory and a genuinely qualified backend. It does not provision a public FTPS server, change firewall/TLS settings, create credentials, activate candidate runtime pins, create sessions automatically or start a watcher. The receiver's marker is uploaded last with ordinary STOR; it is not immutable publication or an authenticated recorder identity.

## Inspect received attempts without importing

```sh
python -m audio_backend.recorder_inbox /private/receiver/inbox
```

This Linux-only command reads immediate canonical `r-...` attempt directories. It lists every validated candidate grouped by capture hash and sequence, gives compact missing-ordinal ranges, and retains each candidate's close reason, source identity, frame placement and hashes. It never selects a retry, runs intake, sends HTTP, creates snapshots or finalizes. A STOP observation is not a terminal decision; `terminal_state` remains `not_determined`. Multiple attempts at the same ordinal always require selection, even if their content is identical. Hashes are declarations, not proof of the recorder's identity.

Default enumeration is 128 immediate entries, including ignored files; `--max-entries` may raise that to 256. Enumeration is bounded before sorting. Logical WAV-read accounting budget defaults to 8 GiB, with 512 MiB per candidate and one cooperative 120-second deadline. The inspector enforces each remaining byte allowance before WAV reads. Failed candidates conservatively consume their reserved allowance, so a corrupt file cannot evade the aggregate bound by failing its final hash. This is logical byte accounting, not a kernel-I/O meter or protection against concurrent same-account mutation. `--max-bytes` and `--deadline-seconds` may lower these bounds.

Incomplete, extra-file, invalid, symlink and replaced candidates are visible as unresolved. Any entry, aggregate-byte or global-time exhaustion refuses rather than silently returning a complete-looking prefix. Root replacement/change also refuses. Exit 0 means the recognized candidates were individually validated during that pass, not that the inbox is complete, unchanged now, safe to ingest or a coherent snapshot. Zero recognized candidates is possible; ignored entries are counted. The observation expires immediately. Child files can change after their individual observations; intake independently revalidates and owns a snapshot. Trusted local parent paths and Linux `/proc/self/fd` are required. This is not protection against an adversary controlling the same OS account.

## Select and preserve exact paths

1. Identify the intended recorder/capture through independently known context. Do not choose only by newest directory time or lexicographic attempt name.
2. Check sequence starts at zero, expected format is mono16k PCM16, and every desired ordinal has one explicitly selected complete attempt. Missing segments, alternate retries, changed epoch/configuration, interruption, recovered tails or uncertain seams require investigation. The inventory does not resolve those boundaries; the existing intake refuses unsafe adjacency.
3. Preserve the exact selected attempt paths and original bytes for the whole job. Later calls revalidate the originals. Do not rename, remove, edit, or substitute a second attempt after admission, even when it appears to contain the same audio. Do not delete a failed job or create a new capture to escape an uncertain result.
4. Have an existing empty strict session and exclusive control of that session/capture. The current server does not reserve ownership or offer activated idempotent session creation. Never automatically retry an uncertain session-creation request. Keep any API key in the existing environment/secret mechanism, never a command argument, URL, report or Git commit.

## Start, append, inspect, explicitly finalize

Use the [incremental intake command](INCREMENTAL_RECORDER_CAPTURE_INTAKE.md), one selected delivery per invocation in ordinal order. Create a private 0700 parent for the job. Set capacity options at the first call, using the [profile planner](RECORDER_SEGMENT_PROFILE_PREFLIGHT.md); existing journal limits cannot be changed later.

```sh
python -m audio_backend.recorder_capture_intake \
  --start --delivery /private/receiver/inbox/SELECTED_ORDINAL_ZERO_ATTEMPT \
  --job-dir /private/jobs/CAPTURE_JOB \
  --api https://your-backend.example --session-id EXISTING_SESSION_ID \
  --capture-id OPERATOR_CHOSEN_CAPTURE_ID --exclusive-writer \
  --max-hours 8 --max-inputs 128
```

For later deliveries omit `--start`, keep the same target/job/capture, and pass the next explicitly selected attempt path. On an interrupted append, repeat the exact latest delivery without `--start`. For a complete ordinal-zero admission interrupted before any uploader checkpoint, the incremental command documents narrowly bounded explicit `--recover-initial` reconciliation. An incomplete snapshot/header/receipt or any uncertain state outside those preconditions still requires investigation; no automatic repair or adoption is provided.

An append success can mean only local staging. Check `published_frames` separately from `staged_frames`; neither establishes transcription completion. The terminal-aware recorder may legitimately publish a full-sized STOP segment. Old ROTATION-only endings remain nonterminal. Even after STOP append succeeds, issue the separate explicit `--finalize` command only when intended. If that call is uncertain, repeat `--finalize` against the same intact job and target. Do not append or create a replacement capture.

EOS acceptance and downstream analysis completion are different. Inspect backend page/chunk status for failed, paused or pending work before calling processing complete. No physical continuity, microphone authenticity, accurate transcription, global speaker identity or long-duration resource qualification is implied by this workflow.

## Observed multi-segment network linkage, 2026-11-03

The actual terminal-aware producer's three four-second silence segments were sent using the existing production Android FTPS/TLS classes in three separate private loopback server sessions. The retained server-received WAV/envelope/marker directories, not a copied substitute fixture, were individually inventoried and then explicitly appended to one existing strict session in the accepted isolated native candidate. This does not deploy a production receiver or prove a physical phone/network path.

The three calls staged 64,000/128,000/192,000 frames without EOS. Separate explicit finalize published and released one EOS page with three sources; native chunks of 10.5 and 1.5 seconds completed. A lost published-seal response reconciled and completed finalize replay matched. Source/input integrity and clean shutdown passed. The reusable `qualification/audit_ftps_sequence_linkage_20261103.py` binds each actual received path and file hashes from the saved wire report to its immutable intake admission, then runs the existing native PCM/history/source audit.

Android fixture-runner source: [eb12c9d](https://github.com/Headmaster218/long-audio-recorder-android/commit/eb12c9d3ea44b62adba24954cb24d2012d45c043). Local wire report SHA-256: `5b51770d7c7d8165d62e5b1c9e5ef515bfb3560f1450fbf6de77efaf8f0fa943`. Local backend RESULT SHA-256: `3a3edfd2871dc17d9fafd173108b2055da9faa78a7c8ef0ab122bb3648c6e524`. Host Android API fixtures were used, not an Android device. This is a 12-second digital-silence linkage check, not speech accuracy or long-duration resource evidence. Generated media, reports, certificates, keys, model assets and databases remain outside source backups. Main and production runtime pins remain unchanged.
