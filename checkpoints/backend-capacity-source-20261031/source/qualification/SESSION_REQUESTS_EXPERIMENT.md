# Isolated server-side session request experiment

Status: design and opt-in isolated SQLite/API candidate only. NOT activated in
working source, the accepted native runtime candidate, any existing database,
or an automatic intake loop. The backed-up patch is deliberately unapplied.

## Problem and existing mechanisms

`POST /sessions` creates a fresh random session every time. Its `Idempotency-Key`
header currently has no meaning. An intake client cannot safely retry unknown
session creation, invent a replacement key, or infer its session from a matching
name. Existing source/page idempotency and the uploader journal apply only after
a known session/capture exists. Local proposal files do not provide server-side
uniqueness, and a transport marker is not authenticated recorder identity.

The existing Store already creates session/group/strict policy in one
`BEGIN IMMEDIATE` transaction and keeps scrubbed session tombstones on deletion.
The candidate reuses these mechanisms instead of inventing a separate scheduler.

## Narrow candidate contract

- Explicit `session_requests_experimental=False` by default. Enabled mode requires
  strict primary and a configured API key. Existing authentication middleware
  protects both new endpoints; one store and one authorization domain only.
  Multiple API keys/rotations must represent the same domain, not multiple tenants.
- New `PUT /session-requests/{key}` and `GET /session-requests/{key}`. Existing
  `POST /sessions` and its non-idempotent contract remain unchanged.
- Key is exactly 32 lowercase hex characters. The caller must generate and durably
  retain a random opaque key before first use. Format validation cannot prove
  entropy; raw recorder names/transport content hashes are not sufficient keys.
- Only the default identity group, unspecified identity mode and primary legacy
  processing mode are admitted. No fragment callbacks or arbitrary identity
  enrollment runs through this endpoint. Name retains its existing 200-character
  bound. Canonical UTF-8 JSON hashes the four explicit API fields including
  defaults, sorted keys, no whitespace, no Unicode normalization; unknown fields
  and unsupported bodies refuse before state changes. Canonical bytes <= 1024.
- Extracted transaction-local session helper preserves the existing POST body.
  PUT uses the same connection for session, strict policy and receipt mapping.
  No nested session creation, external callback, or post-commit mutable session
  lookup is used. A minimal immutable receipt is returned only after the Store
  context confirms its commit. Successful responses are private/no-store.
- Exact replay is checked before active or lifetime capacity, returning the same
  session ID/digest. Changed body is 409; GET unknown key is 404. Deleted session
  is 410, never recreation. Deletion scrubs the request digest (which could encode
  a name) but retains opaque key/session-ID tombstones. Tombstones are not evicted.
- Fixed lifetime cap 4096 receipts, independently enforced by a SQL trigger.
  Capacity stops new creation; existing exact active requests still reconcile.
  No client may evade a conflict/deletion/unknown result with a replacement key.
- A fresh-store namespace and exact policy/schema marker are required. Opting an
  existing unmarked database in, opening a marked database with opt-in disabled,
  or schema drift refuses on the existing tracked, admitted Store discovery
  connection before base schema writes. There is no extra raw SQLite connection. This is not migration or silent adoption. A crash during
  initial schema setup can leave an unmarked database that requires manual review.

## What is and is not tested

The candidate uses real strict ASGI handlers and SQLite with factory instance
ownership, without native lifespan, models, microphone, FTPS, or service activation.
Tests cover exact replay, changed body, ignored/discarded committed response plus
GET and Store reopen, same/different-body concurrent requests, existing Store busy
backpressure, authentication, bounds, deleted tombstones/digest scrubbing,
immutable mappings, rollback injected after session/policy/mapping insertion,
active capacity, a directly constructed full 4096 tombstone state, and schema/
opt-in refusal. Existing session-contract and uploader-admission tests also pass.

The full-capacity fixture constructs historical tombstones directly; it is not
4096 end-to-end session lifecycles. Discarding a completed response tests server
reconciliation semantics, not an actual network disconnect. Pre-commit injected
failures are not ambiguous COMMIT or power-loss qualification. A Store whose
commit/cleanup is uncertain must retain its existing ownership fences; this
candidate does not clear them or authorize automatic retries of processing jobs.

## Acceptance still required

This adds jobsDB schema, including a session-deletion trigger. The earlier owned
CellStore physical qualification does NOT qualify this primary-store change.
Before any runtime activation: independently review primary-store schema/budget
and deletion interactions; test bounded allocation and actual crash/commit-loss
reconciliation; verify no duplicate session across restart; and establish an
explicit server-instance/authentication binding for the intake client. Strict
interrupted jobs still require manual retry. This feature alone is not a durable
recorder-to-session registry, authenticated capture authority, cross-server
idempotency, safe multi-tenant service, automatic intake, or exactly-once inference.

## Reproduction

Make a fresh source copy and apply `isolated_session_requests_20261103.patch` with
`patch -p1`; do not apply it to production or an existing store. Explicitly run
`qualification/test_session_requests_candidate.py` plus existing
`tests/test_recording_session_contract.py` and `tests/test_strict_uploader_admission.py`.
The main source tree intentionally lacks the experimental module and setting.
These opt-in candidate tests are outside the normal `tests` collection.

## Observed review and tests

The final isolated candidate passed 33 combined focused tests (22 new candidate
cases plus 11 existing session/uploader admission cases); the independent reviewer
reran all 22 candidate cases successfully. One existing Starlette/httpx
 deprecation warning remains. The first review found a raw discovery connection
outside tracked admission and an SQLite NULL-key mutation edge. Both were fixed:
discovery is inside tracked admission, key/digest constraints are typed, and
immutable-key comparisons are NULL-safe. The direct SQL regression is included.

`SESSION_REQUESTS_CANDIDATE_BASIS_20261103.json` pins the tested candidate files,
patch and local result hash. It explicitly denies activation and crash/allocation
qualification. No raw database, request name, audio or transcript is backed up.
