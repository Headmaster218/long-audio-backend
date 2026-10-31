# Fresh strict-store retained metadata quotas

`strict_primary_lifetime_records` defaults to 2056 and accepts an integer from
1 through 131072. It is a **per-table retained row ceiling**, not a per-core
work budget, aggregate RAM allowance, or permission to discard evidence.
`strict_primary_receipt_records` separately defaults to 4096 and accepts an
integer from 1 through 20000. Receipt counts describe sources/chunks, not core
outcomes; a chunk may produce several core outcomes.

The exact pair is persisted for a fresh nondefault strict store in an immutable
`strict_lifetime_policy` row and schema CHECK constraints. Store initialization
freezes both settings before acquiring initializer ownership. Reopening with a
different pair refuses; there is no schema migration. Default stores retain the
old schema. An old reader refuses a new nondefault namespace rather than silently
accepting the larger capacity. Missing policy contents never default-adopt.

Planning examples, **not duration qualifications**:

| Audio plan | Core/retained-table ceiling | Source receipt ceiling |
| --- | ---: | ---: |
| 12 hours, about 4116 chunks | 32768 | 8192 |
| 48 hours, about 16458 chunks | 131072 | 20000 |

The strict primary-only path stores source receipts but does not create acoustic
cells. Its existing cells database byte budget remains independently enforced.
These row ceilings do not guarantee that every metadata shape fits that byte
budget, nor do they qualify another acoustic-cell profile. Aggregate process RAM
remains separately capped at 6 GiB by the selected profile. Local per-chunk
outcome, segment, turn, source and archive-operation budgets are unchanged.

Original control/outcome identity guards, retained tombstones and immutable
proof rows remain. Shared source storage, lexical bytes, capture bytes, disk
reserve and full-session pagination are separate constraints; raising this pair
alone does not make a 12–48-hour recording safe or successful.

## Support metadata is a separate operational ceiling

`speaker_support_max_records` counts retained rows, including archived rows whose
PCM is cold; archival does not free a row. Store freezes this existing setting
at construction. Unlike the exact persisted core/receipt schema quotas, it is an
operational admission limit: an explicit changed value on reopen is permitted,
not a migration or evidence rewrite. Default remains 4096.

A 12h18s profile with 4116 chunks can use 65536 records; a 48h shape with 16458
chunks can use 262144. The existing archive admits at most eight supports per
chunk (32928 / 131664 records respectively), not eight per core. This does not
promise arbitrary input will meet that local shape. PCM and per-operation limits
are unchanged. Each manifest remains bounded at 128KiB; the row ceiling alone is
not a physical-disk or aggregate-memory guarantee. Measure actual retained
metadata and process memory with the chosen inputs before claiming duration.
