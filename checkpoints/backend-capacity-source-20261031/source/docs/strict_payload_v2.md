# Strict payload v2: focused main-path slice

Status: implementation/test draft; no execution result is claimed here. This is
the fresh `strict-primary-payload-v2` policy, not a migration or reinterpretation
of v1 or the qualified offline `strict-primary-archive-v2` slice.

## Contract under test

The actual strict Worker commits primary progress, positively finishes its
support/cohort/intent cleanup, and automatically attempts at most one eligible
payload handoff. The trusted original frontier keeps the current 300-second
native epoch, 21.2-second preroll, current analyzer/capture, pins and other live
roots resident. An idle resident model is not evidence of an unknown owner and
need not be destroyed for each handoff.

Each handoff durably publishes a bounded immutable PCM/vector unit, then moves
the exact hot BLOBs to committed cold placements in a protected transaction.
Support/vector logical rows, original byte counts, IDs, manifests, hashes,
generation/space, source links and review FKs remain. Chunks, transcript rows,
outcomes/compacts, receipts, recording/history rows and source/work WAVs remain.
Physical placement must not emit logical deletion/changefeed events or change
transcript revisions. Missing or corrupt cold bodies are explicit failures,
not successful empty responses or silently omitted historical candidates.

Original support/audio/vector validators consume exact cold bytes through an
explicit trusted resolver in the same snapshot. Existing and new reviews,
full-history candidates and primary voice-reference gallery reads must
retain their meaning. Source erasure and generation invalidation take priority;
a cold archive is not authority to resurrect revoked evidence. History's
separate certificate-vector bodies are unchanged by this slice.

The 64 MiB hot PCM/vector allowance is not enlarged. Capacity failures must
refuse before acceptance (or roll back the same accepting transaction), rather
than accepting a valid result with a fabricated unavailable support. Known
busy is distinct from sticky COMMIT/close uncertainty. Unknown outcomes retain
the original owner, original error and both evidence copies; tests cannot use a
replacement Store to impersonate supervisor recovery.

## Bounded layout and reads

The live format uses a durable paged catalog and at most 16 units per directory
bucket. The rollover fixture must create a real seventeenth unit in another
bucket; changing the old global cap or faking catalog counters is not coverage.
Reservations include failed/staged/orphan copies. No global directory/catalog
scan, unbounded temporary WAV/cache generation, or per-object budget renewal is
allowed. Every caller shares the intersection of its remaining allowance with
the fixed 5-second / 128-MiB controlled explicit file plus SQL-projected reads /
4-MiB metadata / 32-MiB payload / 1-MiB buffer / 150k-SQL-step envelope. This is
not a claim to meter all kernel/page-cache/import-loader I/O.

## Finite proposed model-free acceptance selection

The draft selection is 18 cases; exact node IDs and the command require a later
root authorization. Do not rerun the prior 66- or 55-case qualification suites
as a substitute for this connected path.

| Group | Cases | Required observation |
| --- | ---: | --- |
| Fresh live admission and old-policy isolation | 3 | Exact new schema/profile; v1 and offline databases are not migrated or edited. |
| Connected Worker lifecycle | 1 | Original Worker commit and cleanup, automatic one-unit handoff, actual BLOB removal, preserved logical rows/FKs, exact normal cold readers, old/new review and old-cold gallery, then another normal Worker commit. After positive original shutdown: data reopen, cold readers/review details, and original Store manual-tail retry admission with a new nonce and unchanged prefix. No second inference cohort. |
| Actual bucket rollover | 1 | Seventeen tiny real units form buckets of 16 and 1; exact early/late historical reads remain bounded. |
| Required roots and one-unit bound | 1 | Original pin-root extraction plus exact component frontier/root exclusions and one-unit bound. The connected case separately crosses the actual native epoch/preroll frontier with the inherited 2-second context. |
| Capacity before acceptance | 3 | Hot capacity, archive disk floor and known-busy boundaries preserve the previously accepted prefix. |
| Cold integrity | 4 | Missing body, changed byte, locator/body disagreement and conflicting retained hot duplicate never succeed or silently omit evidence. |
| Revocation/currentness | 2 | Source revocation and generation invalidation do not resurrect cold support/reviews. |
| Original uncertain ownership | 2 | COMMIT and close uncertainty retain exact owners and first errors, and fence later destructive work. |
| Shared caller budget | 1 | Multiple objects consume one remaining budget; a later object cannot renew it. |

Fixtures use fresh generated canonical WAVs, real original Receipt/source/range
bindings, original lexical/provenance/support/vector codecs and SQLite Store
transactions. Numerical native observations and embedding vectors are explicitly
synthetic witnesses, not acoustic or installed-model qualification. The one
connected case is approved to start only the original `Worker.loop` and
`CellStore._run` threads, retaining their original owners before start and
requiring original stop/join confirmation. Other cases are threadless component
contracts with their synthetic scheduling authority explicitly labeled. No
server, child process, model or HTTP execution is part of this proposal. One
generated 325.5-second source/frontier case advances sample
coordinates without a real-time wait; short cases do not invent elapsed native
history. Fault owners are retained in fixed test witnesses
before fault injection, including if an assertion itself fails.

## Scope limits

Passing this slice would demonstrate payload residency, not all-history logical
row retirement, source-incarnation retirement, a real supervisor restart, or
12–48-hour readiness. The 2056 outcome/compact, 4096 support/receipt, 1024 page,
lexical/history and original source/work growth limits remain. Exact primary
CellStore `cells=()` does not remove independent CellStore/receipt metadata
constraints. The first exhausted resource has not been established by this
model-free selection.

Hot byte reduction, SQLite file size/freelist reuse and cold archive bytes are
separate measurements. Full evidence retention still grows disk usage; keeping
original source/work files means this slice cannot claim a 32-GB/48-hour fit.
A later real replay requires separate approval and must actually cross the old
64-MiB cumulative support-production boundary; wall-clock duration alone does
not prove capacity coverage.

The selected dynamic voice-reference coverage is the original `_gallery` chain
inside the original bridge snapshot and shared resolver, using real old cold
support reviews and all original projection/vector validators. Top-level
`process_one` / `_compute` propagation and unchanged separate history-certificate
vectors are source-reviewed, not dynamically qualified here. The component
root case does not impersonate a live actor/pin: it labels its synthetic pin
document and frontier authority. Full active-owner/lock fault combinations are
not claimed as independently executed cases.

The connected fixture's 31 contiguous 10.5-second original/work pairs contain
20,834,728 generated WAV bytes in total (325.5 seconds of source samples).
Source files remain individually bounded; nonzero context deliberately produces
adjacent-source support closure. Other cases use tiny independent fixtures.
The expected generated footprint is below 96 MiB / 1,000 entries, with the
existing 128-MiB / 2,048-entry gate as authority, not this source estimate.
