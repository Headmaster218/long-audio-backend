# Strict primary lifecycle (fresh rebuild)

This is new source built on recovered Oct25 code. It is **not** the lost Oct28
candidate or an inherited test result. The proposed focused controls have not
been executed at source-authoring time.

`Settings(strict_primary_enabled=True)` / `--strict-primary` opts into a fresh
strict recording policy. It requires the existing exact clean-primary profile:
SenseVoice + native Nemotron on CPU; native support, cells, core seams, bounded
support, lexical evidence, and recording speaker links; no automatic MOSS,
legacy seam-cells, compact GC, or receipt GC. Existing model paths remain explicit
and no model is downloaded. Strict mode does not promise eventual completion:
incomplete evidence must pause rather than advance the accepted cursor. Manual
retry is separately authorized and cannot reset an unresolved resource owner.
The Store owns durable fresh admission, immutable policy, atomic proof/cursor
checks, retry status, source identity and old-writer advancement fences.

## Supported launcher boundary

Strict `create_app` requires `instance_lock_fd` before Store construction or
creation of its data directories. The CLI requires `--instance-lock-fd`; it does
not silently create a lock or fall back to legacy startup. Supply a genuine,
already exclusively locked, read-only Linux descriptor for the exact canonical
`data_dir/instance.lock` from the supervising launcher. Keep the supervisor's
original hold until its original child handle has positively waited. The
in-process CLI retains its adopted original hold while passing only a duplicate
to `create_app`, through the service/lifespan result.

The inherited `InstanceLock.adopt` implementation is unchanged, including its
suppressed consumed-raw-fd close error. This patch does **not** prove that close
was positive. The genuine external parent hold/original-wait contract is still
required. No multiple-worker/reload startup is supported (`workers=1`).

One fixed factory owner binds the exact Store immediately after its constructor
returns, before helpers can open connections. An initializer witness supplied by
Store covers constructor failure before assignment. A fixed launcher owner
retains the original lock. Unknown Store/lock cleanup prevents release/reuse;
close is never retried to claim cleanup. Original body exceptions dominate later
cleanup errors, which remain attached to the retained owner. This contract is
for one supported Store/factory/launcher lifecycle, not arbitrary replacement
Stores or cloned inherited descriptors created by trusted Python code.

Strict inherited inspection `OSError` is conservatively fail-stop. Both fixed
MOSS ownership-fence call sites also fail-stop on any error, since that inherited
inspector converts some OS/close errors to `RuntimeError`. This does not
assert positive closure of all inherited reader descriptors. The parent hold
remains required until process exit/original wait. Legacy inference and retry routing remain unchanged when strict mode is disabled.
Store's pre-DDL namespace-discovery gate now serializes or refuses overlapping
initializers before the database mode is known; a positively identified legacy
initializer releases that gate promptly. This is an initialization-concurrency
change, not a change to legacy inference or retry policy.

## Retention and availability

Automatic cleanup preserves nondeleted strict sources. Marker-based source
retirement refuses strict audio. Explicit session DELETE remains an explicit
erasure request, only after positive ownership cleanup and the existing idle
check; immutable strict policy tombstones remain in Store.

The typed Store mutex-admission busy exception maps to HTTP 503 without an
automatic-replay hint. It proves only that **this mutation admission** did not enter; an
HTTP request can have earlier committed effects. Do not blindly replay POSTs or
infer rollback from the response. Inspect durable state/idempotency first.

## Focused source controls and remaining qualification

`tests/test_strict_app_lifecycle.py` exercises exact extracted factory/CLI source
with fake Stores and locks: prelocked refusal, unchanged legacy dispatch,
constructor/post-constructor ownership, original exception preservation,
unknown-close retention, successful transfer, and launcher hold ordering.
These are not actual service, FD, HTTP, model or end-to-end tests. The fresh
candidate still requires the reviewed finite functional gate and actual tiny
HTTP/manual-retry lifecycle with real inherited ownership before such claims.
