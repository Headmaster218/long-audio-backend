# Explicit existing reviewed-bank handoff

Logical session rollover can opt in to the same **already activated reviewed-person bank**. This keeps the supported review and crop-suggestion lookup in the same identity group. It does not copy speaker labels or vectors, create or activate a bank, enroll a voice, relabel a transcript, change matching thresholds, or establish acoustic recognition accuracy. Existing commands and v1/v2 boundary receipts without this opt-in retain their old behavior.

## Select and bind the existing bank

First inspect the existing source session and the supported read-only bank route:

- `GET /sessions/{source_session_id}`
- `GET /identity-groups/{identity_group}/reviewed-person-bank`

The source must be an existing `legacy` processing session in that group. The bank must report `identity_mode=verified_review_v1`, `primary_associations_enabled=true`, `explicitly_retained=true`, and `automatic_enrollment=false`.

Add all three arguments to `audio_backend.recorder_logical_boundary` or `audio_backend.recorder_generation_transition`, alongside their existing explicit acknowledgments and exact successor arguments:

```
--preserve-reviewed-identity-group REVIEWED_GROUP
--identity-generation EXACT_32_HEX_GENERATION
--identity-policy-sha256 EXACT_64_HEX_POLICY_HASH
```

The boundary command remains local-only and performs no HTTP. Its v3 receipt binds the old source session, group, generation, mode, exact policy hash, and full next-session POST request, together with the original prefix and exact successor delivery. Both same-inbox and explicitly routed cross-inbox successors are supported. Partial, changed, or malformed opt-in selection does not silently choose another bank. Omitting all identity arguments preserves the legacy creation request.

For a direct `recorder_inbox_runner initialize`, the same three arguments additionally require `--identity-source-session-id EXISTING_SOURCE_SESSION`. This is also an existing-source handoff, not a new-bank setup command.

Initializer v2 receipts and runner config v2 independently bind the selection. Removing initialization receipts cannot downgrade a configured opt-in runner to legacy behavior. Existing v1 configs and initializer receipts remain valid and unchanged.

## Checks, recovery, and bounds

The coordinator checks the selected bank before unfinished old-span finalization/export and again before each old-session write. The initializer checks source membership and bank state before its single POST, then checks the created session's membership and bank state before saving a completed binding. An opted-in runner checks before intake and before each recording write, using its existing deadline, request, write, and response-byte limits. The source session must remain readable; deleting it stops subsequent checked operations.

A normal opted-in initialization uses at most eleven requests and one session POST. The opt-in coordinator counts its checks and initialization within its total request/write/response-byte limits, reserving the full bootstrap allowance before creating next-state receipts. Insufficient remaining allowance returns a bounded pause that can be resumed with sufficient limits. Completed transition replay stays local-only with zero HTTP; those historical receipts are not a live claim that the bank has never changed. The next intake still rechecks it.

An observed reset, changed group, changed policy, disabled/unavailable bank, or inconsistent receipt refuses further dependent work. A creation that returned an ID retains its empty-created-session receipt even if a post-check fails. An uncertain POST is never automatically repeated. Originals, capture/frame origins, successor selection, and ingestion behavior are unchanged.

Legitimate review revision, availability, and first embedding-space binding changes are not treated as identity resets. The receipt binds the existing policy hash, not a rewritten or automatically upgraded policy, and does not freeze the bank's review contents.

## Deliberate concurrency limit

These are **observed continuity checks**, not server-enforced compare-and-swap or a permanent inference-generation pin. The existing API does not accept a generation fence with session creation or recording admission. A reset can race between the final GET and a write, or occur after upload before queued inference runs. The next observed mismatch is refused, but the client cannot undo an already accepted write or promise that delayed inference uses the earlier generation. Current server reset semantics remain authoritative.

`recognition_continuity_verified` remains false. These controls establish the selected existing bank and exact delivery boundary, not sameness of voices, listening quality, or correct automatic recognition.

## Verification scope

`tests/test_recorder_reviewed_identity_handoff.py` covers old-receipt compatibility through the unchanged suites, exact opt-in bodies and hashes, partial opt-in refusal, membership/reset checks, uncertain POST recovery, post-link intake refusal, bounded accounting, cross-inbox handoff, the missing-initialization downgrade control, and the explicitly documented GET/write race. Genuine `create_app`/SQLite tests activate only a synthetic empty bank, exercise supported creation and reset routes, and preserve the existing strict-contract refusal on a native-free legacy fixture. They do not start native models or assert acoustic enrollment/quality.
