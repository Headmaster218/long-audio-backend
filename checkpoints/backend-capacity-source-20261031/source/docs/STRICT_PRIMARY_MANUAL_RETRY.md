# Strict primary evidence and manual retry

**Status: new source implementation, not yet execution-qualified.** This slice was rebuilt after the workspace returned an older snapshot. Its base is the newly assembled 954-file Oct25 source tree; it is not a restoration of the lost strict candidate or its test evidence. No historical runtime, model, HTTP or endurance PASS is inherited.

## User-visible contract

Strict mode is opt-in (`Settings.strict_primary_enabled=True`, CLI `--strict-primary`). The default legacy behavior remains unchanged. A newly admitted strict recording has an immutable durable policy. Existing optional-evidence recordings are not silently upgraded, and reopening a strict database with strict mode disabled is refused.

There is one narrow legacy startup operational change: pre-DDL schema discovery is serialized with nonblocking refusal of overlapping initializers. A positively identified legacy database releases that discovery guard immediately; no legacy inference or retry policy is changed.

An accepted primary interval must have its matching compact and a validated provenance outcome in the **same transaction** as its cursor advancement. The Worker also refuses incomplete current-window evidence before commit. A failed current interval is not bridged, invented or reported complete. The guarantee is a gap-free **accepted prefix**, not guaranteed eventual completion.

Strict refusal is exposed through the existing chunk `failed` status and a manual-retry reason. It retains the source audio and leaves the unaccepted cursor unchanged. Normal waiting for additional stream input remains distinct from evidence failure. A healthy accepted partial interval can yield fairly to unrelated work; a refusal or interrupted attempt cannot automatically replenish its authorization.

Validated empty or unknown-only output is legitimate, but an empty status string is not proof. It must come from the successful Capture opportunity for the exact source, attempt and interval. Retained/existing provenance requires the corresponding single packet; empty/unknown-only outcomes do not fabricate one.

## Manual operation

1. Inspect `GET /chunks/{cid}` and its error. Do not assume a client timeout means a request was rolled back.
2. When the cause has been addressed and cleanup is positively known, use the existing `POST /chunks/{cid}/retry`. Strict work rejects generic `resume`.
3. The server reconciles the durable accepted tip and creates a fresh attempt nonce when it next claims the unchanged source. A completed chunk cannot be retried as new work.

There is no automatic retry timer or retry scheduler. Restart recovery of an interrupted strict attempt requires manual retry. Unknown COMMIT acknowledgement or resource closure is not converted into permission to replay. Retained uncertain owners block new processing/manual eligibility in the supported service lifecycle; they are not closed a second time to manufacture a successful cleanup result.

Ordinary short Store contention uses a bounded admission wait. A pre-admission `StrictMutationBusy` response is HTTP 503; it does **not** prove that a multi-step request had no earlier durable effects. The idle claim path defers only before transaction entry. No SQL-body retry or whole-request automatic replay is added.

## Supported startup and ownership

Strict mode requires the exact clean-primary native/lexical configuration and a genuine supervisor-provided, prelocked **read-only** `--instance-lock-fd`. Missing ownership is rejected before Store/filesystem initialization. The supervisor must retain its original hold until the original service child has been waited for. The current inherited-lock primitive's suppressed consumed-raw-descriptor close is not newly qualified by this feature.

The supported boundary is one Store and one launcher lifecycle with its HTTP controls—not arbitrary trusted Python code constructing parallel replacement Stores. Fixed initializer, factory, Worker, database and publication owners retain uncertainty. Some inherited read-only inspections conservatively fail-stop on OS errors; this is not a proof of every request descriptor's closure.

Strict mode disables automatic source retirement/GC. Explicit authorized user deletion remains separate and is allowed only with positive cleanup. Immutable policy tombstones remain and count toward the finite admission limit. Retention is not unlimited storage or automatic capacity replenishment.

Old binaries are unsupported for strict databases. Fixed database invariants reject strict cursor/completion advancement without the matching active authorization and accepted interval record; this is fail-closed protection, not an old-binary compatibility promise.

## Qualification still required

The intended next checks are a bounded model-free complete application import diagnostic, focused Store/Worker/ownership tests, and a tiny real HTTP/manual-retry lifecycle. Real inference follows separately only after the current runtime and dependencies are admitted. A successful import census is diagnostic evidence, not runtime or strict-feature qualification. No model download, long recording run or public publication is part of this source checkpoint.
