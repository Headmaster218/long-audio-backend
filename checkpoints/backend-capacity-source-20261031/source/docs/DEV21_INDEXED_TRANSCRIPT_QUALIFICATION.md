# dev21 indexed transcript: qualification and limits

Version `0.2.0rc3.dev21` follows published dev20 commit
`0ddd50d03c2a42b3ba96f07896f2c06ee8a1aee6`. The display selection algorithm and
recognition claims are unchanged. This release bounds time-page discovery by
indexed local chunks and both automatic aliases, adds a separately keyset-paged
binding-placement audit, and invalidates opaque cursors transactionally.

## Source checks

- Final model-free focused selection: **130 passed**.
- Final targeted lifecycle, native receipt-GC and automatic integration selection:
  **128 passed**. These are targeted reruns, not a fresh full-suite pass.
- The earlier complete 20-module regression retained **722 passed, 46 subtests
  passed and four failed assertions**. One assertion expected the old raw
  session schema. Three compared session bytes across an explicit automatic
  terminal transition that now correctly advances the internal revision.
  Only two test files changed afterward; production implementation bytes did
  not. The corrections assert the new integer field separately, exact +1 on
  terminal transition, equality of every previous semantic session field and
  protected table, and complete post-terminal preservation across receipt GC.
- An earlier focused run retained 120 passes and one untriggered synthetic VM
  negative expectation. Increasing only that fixture's prefix from 40,000 to
  100,000 rows exercises the unchanged 250,000-step refusal. Its failure and
  exact source snapshot remain retained.

The final 130/128 runs took 3.34/49.17 supervised seconds, with sampled RSS
peaks of 77.7/113.2 MB. Both original processes were positively waited; no
remaining observed owned descendants, resource violation, forbidden model
import or frozen-source drift was recorded. They used the existing runner on
CPU 3, nice 19, with 1 GiB sampled process-tree RSS and 2 GiB address-space caps.
These limits are safeguards, not production latency or memory guarantees.

The executed r3 source manifest is SHA-256
`13a12099dea264343f7ac5d2832bb86847a7ea3d6d5e0b7a44d88e9f36211acf`.
Release preparation changes only the two version declarations and documentation
relative to that source. The immutable run receipts retain all earlier failures.

## Indexed read and invalidation coverage

A far-seek fixture contains 1,000 unrelated bindings plus one target and proves
only the target binding is loaded. It stays within current admission limits.
Tests cover second-alias-only seams, nonzero origins, half-open boundaries,
alias deduplication, result and response caps, missing/moved sources, empty audit
pages with continuation after the last examined row, and stable cursor scope.
The final chunk's indexed extent preserves late support when session timeline
metadata lags, under the existing disjoint published-chunk invariant. Legacy
non-grid primary duration envelopes remain valid; long single-chunk primary
scans still fail explicitly under the unchanged VM budget.

Revision tests cover late commits with earlier reserved archive sequence,
committed-source cancellation, OLD/NEW and retained automatic session owners,
source/mapping deletion and moves, primary segment moves, rollback, exact schema
initialization and fail-transaction integer overflow. Existing public change-feed
trigger definitions and the public session response remain unchanged.

Time pages do not certify absence of unplaced history. Clients must separately
consume `/sessions/{sid}/transcript/binding-audit`; an empty audit page can still
have a continuation cursor. The audit examines at most 64 rows per page and
advances by the last examined sequence, not the last emitted notice.

## Capacity and deployment boundaries

Time pages retain 1,024 local binding candidates / 4 MiB binding bytes, 16
intersecting results, 256 KiB per result, 2 MiB aggregate result bytes, 128 primary
units, 256 KiB response bytes, 250,000 SQLite steps and a 0.75-second read budget.
The old entire-session binding scan is gone. A dense local page can still refuse
explicitly rather than silently truncate.

Automatic scheduling admission remains **1,024 rows per session and 4,096
rows globally**. Indexed reading does not qualify 48-hour automatic-overlap
throughput or remove that separate admission limit. No model execution,
recognition-quality improvement, historical database adoption, live deployment,
or crash/power-loss qualification is claimed. Startup adds one internal integer
session revision and versioned triggers; historical database migration behavior
has not been qualified. The read routes themselves remain read-only.

## Package status

Standard offline wheel construction and fresh pip-target installation passed.
All **124 package files** matched source, wheel and installed target byte-for-byte.
Dependency declarations and bundled third-party license/adaptation files stayed
unchanged. Disabled-application construction registered all three transcript
routes; CLI help and existing prerequisite guards passed without a server.
The installed package passed **seven synthetic cases** covering authenticated
real-app registration, far seek, second-alias seams, empty audit continuation,
audit HTTP/cursor behavior, earlier-reserved late commit and OLD/NEW primary move.
All 49 imported product module origins matched the installed source pins.

The wheel/install/verify stages took 1.30/0.58/3.10 supervised seconds. The largest
sampled process-tree RSS was 77.2 MB and aggregate working artifacts were 22.3 MB,
within the existing 180-second / 1 GiB RSS / 2 GiB address-space / 128 MiB artifact
limits. All original processes were positively waited, with unchanged source
pins, no observed remaining owned group, no resource violations and no forbidden
model imports. These checks reused qualified dependencies, not newly installed
dependencies in a clean environment. No download, model or historical database
was used.

The wheel is `long_audio_backend-0.2.0rc3.dev21-py3-none-any.whl` (621,428 bytes),
SHA-256 `27784c49001232082acb315a81b423fb2e45b8917b362387aa875dd510e2f0c9`.
After packaging, only this qualification document was updated with observed
results. Product code, tests, configuration and wheel remain the checked bytes;
the wheel was not rebuilt for this documentation-only update. Prior saved-output
recognition diagnostics remain tied to their original versions and do not
constitute new dev21 model qualification.
