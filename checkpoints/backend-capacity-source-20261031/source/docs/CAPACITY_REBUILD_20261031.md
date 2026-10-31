# Fresh capacity reconstruction (2026-10-31)

This is a new local reconstruction from the surviving October 29 candidate13,
not recovery of an October 30 artifact or its test qualification. Defaults remain
inline source storage, 32 MiB lexical evidence/session, 1 GiB/capture and 4 GiB
retained recording sources. No 12-hour or 48-hour endurance/accuracy claim is made.

## Explicit fresh-store options

The service accepts these options (explicit CLI values override environment defaults):

| Flag | Environment | Default |
|---|---|---|
| `--recording-capture-mib` | `AUDIO_RECORDING_CAPTURE_MIB` | 1024 |
| `--recording-total-mib` | `AUDIO_RECORDING_TOTAL_MIB` | 4096 |
| `--lexical-evidence-session-mib` | `AUDIO_LEXICAL_EVIDENCE_SESSION_MIB` | 32 |
| `--strict-payload-source-storage` | `AUDIO_STRICT_PAYLOAD_SOURCE_STORAGE` | inline |
| `--strict-primary-lifetime-records` | `AUDIO_STRICT_PRIMARY_LIFETIME_RECORDS` | 2056 |
| `--strict-primary-receipt-records` | `AUDIO_STRICT_PRIMARY_RECEIPT_RECORDS` | 4096 |

Capacity additions to an already valid supervised, qualified native strict-primary
profile can be specified as:

```
--strict-primary-policy strict-primary-payload-v2 \
--strict-payload-source-storage shared-source-v1 \
--lexical-evidence-session-mib 256 \
--recording-capture-mib 2048 --recording-total-mib 8192 \
--strict-primary-lifetime-records 32768 \
--strict-primary-receipt-records 12000
```

These are profile additions, **not a standalone launch command**: original model,
native runtime, strict evidence and supervisor-held instance-lock requirements
still apply. The service validates these capacity arguments before adopting its
inherited lock; the launcher may already hold its supervisor lock. Settings also
supports `speaker_support_max_records` (default 4096, finite maximum 262144),
frozen by Store; the planned 12-hour test uses 65536. This last setting is not a
new CLI flag or a persisted schema quota.

Lexical and strict lifetime/receipt limits are bound to exact fresh schema DDL;
reopening requires matching configuration. Existing lexical schemas are not
migrated. The pre-existing additive initialization of databases lacking lexical
columns remains unchanged. Shared storage is a new explicit format: an old
binary refuses its marker rather than modifying it. No conversion of existing
inline stores, garbage collection or erasure guarantee is provided.

## Storage semantics

Only original and canonical source WAV bodies are shared, keyed by full source
identity, not merely equal content. Repeated references to the same identity
reuse one immutable body; different identities with identical bytes remain
separate. Identity-key collision/content changes refuse. PCM and vector payloads
retain inline locator semantics. Units retain exact source references and hashes.

Reservation precedes file creation. File/directory synchronization and positive
close precede publication; publication and final source validation precede hot
retirement. Unknown reservations remain charged and are never automatically
adopted. Unregistered bucket files refuse. Published bodies are fully hashed on
reuse under the original cumulative operation budget. Native SQLite waiting is
limited to acquisition, intersects the original deadline and is reset before
business statements/COMMIT; no transaction body is replayed.

`strict_shared_source_usage.allocated` counts physical **body-file** blocks, not
directory or database overhead. Unit archive bytes exclude external shared
bodies. Capacity measurement must include both, plus all originals, canonical
files, databases and directories. Mono PCM16 at 16 kHz alone is 1,382,400,000 bytes
for 12 hours and 5,529,600,000 bytes for 48 hours; these are not whole-store bounds.

For explicit longer/shared profiles, candidate selection starts from a partial
hot-support index; CROSS JOIN fixes the bounded hot-set traversal order. Default
inline historical schema/query remains unchanged. No SQL VM budget was raised.

## Fresh validation and limits

Model-free tests cover configured lexical boundaries/reopen mismatch, CLI/env
binding, real SQLite acquisition refusal and snapshot stability, distinct
120/120/54-second WAV identity reuse, unknown/corrupt files, reserved failures,
and actual Store archive/cold reads/reopen. The realistic WAV component test is
not a Worker or full 294-second archival lifecycle test. Actual Store coverage
uses the existing small component fixture. Independent SQL checks compare
original/new candidate selection across 2056/4116/16458 rows and 27 shapes;
maximum observed VM counts were 3600/7300/28900 under the unchanged 150000 cap.
A fresh actual-service 588-second gate and subsequent long run remain separate
qualification work, not inherited passes.
