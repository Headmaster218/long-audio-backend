# Explicit primary-observation person associations (dev61)

Experimental, default off. This API records an operator claim about one exact
retained crop, not a verified identity, entire utterance or future voice. It adds
no acoustic exemplars and does not change automatic matching, transcript text,
word speaker labels, or the existing fragment-session identity restrictions.
Manual corrections do not yet teach future recordings; that remains separate,
uncompleted work. The V2 speaker-model confirmation did not justify promotion.

## Enable safely

Stop the old worker and back up the database before enabling the feature. Use an
isolated database copy for first evaluation. New Settings includes
`reviewed_primary_persons_enabled=True`; the CLI flag is
`--reviewed-primary-persons`. The flag alone does not disable an existing worker.
For a passive, model-free application use `Settings(data_dir=..., start_worker=False,
reviewed_primary_persons_enabled=True, verified_identity_policy_file=...)` with
`audio_backend.app.create_app(settings)`. No acoustic producer/capture options are
needed for these reads or claims. Existing observations must already be retained.

Creating a bank requires the explicit shipped experimental policy file
`audio_backend/profiles/verified-review-experimental-v1.json` and its exact
canonical policy digest. Existing policy content is immutable. Passive reads of
an existing bank do not require the startup policy file. The current local API's
bearer protection is optional; configure an API key for access control. These
routes are private/no-store, not separate tenant-level ACLs.

Opening Store with `reviewed_primary_persons_enabled=True` installs the guarded
schema recognized by dev61, before a bank is activated. Unmodified dev60 refuses
startup on that database. Turning the feature off does not remove the fence or
its deletion duties. Downgrade requires the backup taken before opening Store with the feature enabled or a separately
reviewed migration; never drop the guard trigger to force old code to open it.

## Short API sequence

1. Explicitly choose a bank group. `PUT /identity-groups/{group}/reviewed-person-bank`
   takes `expected_generation:null`, `expected_revision:0`, `idempotency_key`,
   `mode:"verified_review_v1"`, `policy_sha256` and `confirmed:true`. This creates
   only an absent bank. For an existing bank use its current generation/revision.
   Read the response and subsequent `GET` at the same URL for current fences.
2. `GET /sessions/{sid}/recordings/{rid}/primary-observations` lists retained
   primary observations. Read `.../primary-observations/{observation_id}` for
   `binding_sha256`, source authority/revision and review eligibility. This does
   not claim that audio is still available or that the operator listened.
3. `POST /identity-groups/{group}/primary-associations` takes the current bank
   generation/revision, a new idempotency key, `source_session_id`, `recording_id`,
   `observation_id`, returned `binding_sha256`,
   `expected_source_authority_revision`, `expected_source_revision`, and exactly
   one of `person_id` or `new_person_name`. Its attestation is
   `{"version":"human_exact_observation_person_claim_v1","extent":"observed_crop_only","confirmed":true}`.
   Use the server-minted original person UUID for another recording. Names need
   not be unique. A missing vector can still have an eligible manual claim.
4. `GET /identity-groups/{group}/speakers/{person_id}/occurrences` gives paged
   crop-only occurrences and `parent_evidence_url` navigation links. The crop may
   extend outside its lexical parent. Do not copy the person ID onto that whole
   parent or intersecting words. Existing unified transcript output is unchanged.

Revision-bound list endpoints use at most 32 items per page. Reuse the returned
cursor unchanged; a stale cursor or CAS fence requires refreshing and deciding
again, not silently rebasing the claim. Session recording observation lists use
`after`; bank association/person/occurrence lists use their returned `cursor`.
Names, UUIDs and recording links remain private personal data.

## Correct, retract, erase

`POST .../primary-associations/{event_id}/replace` accepts a complete revised
claim for the same observation. `POST .../{event_id}/retract` takes the current
bank generation/revision and idempotency key. Replacement is atomic; retraction
keeps audit history. Existing group-scoped identity relations can explicitly
merge, mark different people or undo an exact relation without rewriting owners.

`DELETE .../primary-associations/{event_id}` instead permanently removes all
association history for that exact observation in this bank generation, including
replacements and earlier owners. It needs current generation/revision, a UUIDv4
idempotency key, `scope:"all_claim_history_for_exact_observation"` and
`confirmed:true`. Only a bounded source-free retry receipt remains. It does not
erase the person catalog, independent acoustic reviews, source recording or other
observations. A repeated old DELETE returns its receipt without deleting a newly
confirmed claim; an erased old POST cannot silently resurrect the claim.

Deleting a source session erases its active and historical associations and retry
payloads, while retaining an explicitly activated bank and other recordings.
A normal source retirement can retain attribution while making audio unavailable;
revocation/cancellation/changed source binding suppresses effective attribution.
`DELETE /identity-groups/{group}/speakers` is the broader bank reset: it removes
identity state and rotates generation, including when the bank has no sessions.
Application deletion is not forensic wiping of disks, WAL or backups.

## Validation status

The focused synthetic gates distinguish earlier failures, carried-forward
assertions and the final continuation. Full-capacity metadata loading retained
1,024 people, 4,096 relation capacity, the 250,000 SQLite-step and 0.75-second
operation limits. It is not a whole-service 6 GiB or 12–48-hour acceptance claim.

The dev61 wheel was built and installed offline, with all 135 package payload
files byte-identical between source, wheel and installation. The installed
model-free ASGI smoke passed (1/1): two synthetic recordings sharing an explicit
person UUID, resolving evidence URLs, unchanged transcript bytes, reopening a
separate ASGI app/Store with models off, and deleting one source while preserving
the other recording's attribution. This is an application/Store reopen in one
test process, not an operating-system process restart or reboot.

The final source checkpoint updates documentation after that successful build;
the preserved tested wheel contains the pre-update documentation snapshot. Runtime
code and smoke tests are unchanged. See
[the verification report](../reports/REVIEWED_PRIMARY_PERSONS_DEV61_QUALIFICATION.md)
for gate history and limitations. No acoustic accuracy, operating-system crash
recovery, physical-phone behavior, real audio, live network server, automatic
identity learning or long-recording endurance was qualified.
