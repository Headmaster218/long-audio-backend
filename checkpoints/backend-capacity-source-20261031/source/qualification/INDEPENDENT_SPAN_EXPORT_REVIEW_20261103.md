# Independent selected-span transcript export review, 2026-11-03

## Scope

Independent code/design review and adversarial tests for
`audio_backend/recorder_incremental_span_export.py` and the narrow
`recorder_incremental_span_status.run(..., _completed_read=...)` extension.
This review is model-free, uses real local ASGI readers with explicitly synthetic
finalized publication/analysis rows, and performs no native-service launch,
external HTTP requests, deployment, or production mutation.

The export is explicitly **primary baseline only**, not the session's asynchronous
MOSS-preferred unified reading. Each full primary row must reconstruct its exact
canonical content hash, belong to an observed selected-span work chunk, preserve
original ASR segment timestamps, and fit the selected span. Source-local speaker
scope does not establish a person identity or cross-scope identity. Missing text
does not establish silence, complete acoustic coverage, or ASR execution.

## Reviewed boundaries

- Both existing job locks remain held during transcript retrieval and both
  transcript passes. The default status request sequence remains unchanged.
- Exact local controls, full journal chains, media hashes, capture/contract,
  page/work geometry, session chunk statuses and progress are revalidated.
- Cursor, session, policy, revision, terminal page count, numeric types, source
  IDs, hashes, full/reference rows, duplicate canonical segment IDs, and ordering
  are validated. Second-pass page hashes reject text changes with a reused revision.
- Network request/decoded-byte, transcript-page/unit, local-output-byte,
  cooperative deadline/cancellation and existing RSS controls remain finite.
  There is no retry or hidden repair, and all observed transport calls are GET.
- Output uses a fresh owner-only directory and exclusive owner-only files,
  anchored directory descriptors, no-follow checks, staged-file readback hashes,
  no-overwrite links, and a final manifest. Input trees and actual Git repositories
  are refused. Control characters, bidi formatting, and newlines are escaped in
  text rendering; exact JSON source text remains preserved.
- Unrelated optional projection text, named-person assignment, credentials,
  server error bodies and local original/job paths are not exported or logged.
- Non-atomic remote observation and exclusive-writer assumptions remain explicit.
  This does not provide a transaction spanning all server GETs or defend against
  arbitrary malicious same-UID actions, machine crashes, or loss of filesystem
  access after a partially executed write.

## Findings resolved during review

1. A two-status-call design would release locks and lose exact history binding.
   The implementation instead uses the narrow locked context-manager extension.
2. A mere ancestor `.git` existence check rejected the managed environment's empty
   `/tmp/.git` marker. Actual Git metadata detection restores private temporary
   output without removing or changing filesystem permissions.
3. Distinct valid content hashes for the same canonical segment ID could otherwise
   be emitted together. Canonical IDs now must be unique, and baseline order is
   checked against the server projection's ordering contract.
4. A staged file could be substituted between pre-link validation and the final
   link. Post-link inode/mode/link-count checks against the held file descriptor
   now refuse the injected symlink race and do not touch its unrelated target.
5. A final directory sync fault initially left `complete.json` after a failed
   invocation. The implementation now attempts to remove only its own unchanged
   completion-marker inode. A secondary unlink/sync failure remains explicitly
   uncertain, rather than being described as guaranteed rollback.

## Test history

- Initial 69-case run: 54 failed, 15 passed. The 54 failures occurred before
  transcript access because of the empty managed `/tmp/.git` marker. Preserved
  report: `/tmp/recorder-span-export-review01.xml`.
- Expanded 101-case run after the marker, duplicate-ID, ordering and link checks:
  **101 passed**, one existing dependency deprecation warning, 27.11 seconds.
  Reports: `/tmp/recorder-span-export-review02.xml` and matching `.log`.
- Two final filesystem-fault controls: output-path substitution passed; final
  directory-sync failure exposed finding 5. Preserved report:
  `/tmp/recorder-span-export-review-fault01.xml`.

These are separate runs, not a combined clean-pass count. The finite synthetic
controls do not qualify maximum-duration runtime, native inference, speech
accuracy, Android capture, physical continuity, or deployment.

## Final disposition

**Approved for the bounded source checkpoint.** No unresolved blocker remains
within this review's scope. Native public-speech execution and deployment remain
unqualified and are not authorized or claimed by this review.

Final independent run: **103 passed**, one existing dependency deprecation
warning, 26.87 seconds. This includes the targeted post-manifest sync-fault
regression and output-path substitution control. Reports:
`/tmp/recorder-span-export-review03.xml`, matching `.log`, and
`/tmp/recorder-span-export-review03-source.sha256`.

The following file hashes were recorded before the final run and checked again
after it, with all three unchanged:

- Export implementation: `5a72be66995cd5f175a1bff1231270492ccc1675739027cabe9591ebc5dfc0bf`
- Incremental status implementation: `fe3837557b04a1ec8553a1f2de2a5e715590eb357b8830944da7bf4bbeefe6b9`
- Independent test suite: `e59d9b5cee3ac02dd1e08f6fa6e863a8d7382ccfeec1556a940671f8c40204d1`

Interpreter: existing `audio-recovery-runtime-20261023b/bin/python`, with
`PYTHONDONTWRITEBYTECODE=1`, source plus existing recovery test tools on
`PYTHONPATH`, and pytest's cache provider disabled. No dependencies were installed.
`git diff --check` also passed. This approval is limited to the exact reviewed
source and finite checks, not a full-repository or native-runtime qualification.
