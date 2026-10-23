# Verified-mode A/B runner source checkpoint

This directory preserves 41 Python implementation and test files from runner
revision 09. It is a source checkpoint, not a self-contained runtime, deployment,
accuracy result, or identity-learning acceptance.

## Backend dependency

Start with Headmaster218/long-audio-backend commit
`30bfdb89b19e27f877772b3f0feeaafb27de6cb9`. Follow
`checkpoints/recovered-source-20261023/README.md` to reconstruct the public dev71
source overlays and apply the recovered backend delta. The repository root alone
is not that reconstructed product. This runner adds no backend changes and does
not replace the prior source checkpoints.

## Explicit collection profile

The separately named `AB-two-session` profile processes the fixed 720-second A
window, then the fixed 720-second B window, using two distinct sessions in one
service/Store. Both use `verified_review_v1` and the shared anonymous group
`AB-verified-anonymous`. The profile raises only session capacity to two relative
to the single-session profiles; existing work, uploader, HTTP, cleanup, resource
and product budgets remain unchanged. A and B share one uploader deadline and
request ledger. Completion requires both fixed arms, not merely accepted EOS.

The profile binds the existing experimental schema-1 policy at
`audio_backend/profiles/verified-review-experimental-v1.json`, SHA-256
`5217b5fe9737ba2b0348d8ec1d2ba31ccbce6da264630a7506a6f5a71b09cbff`.
Its single/centroid thresholds are 0.75, margin 0.10, and minimum query/enrollment
durations two seconds. The file is inherited from the verified product source;
it is not rewritten or tuned here. Both raw bytes and effective parsed policy
must match. Single-session modes reject the policy override. Reviewed-primary-
persons remains disabled.

Verified mode is a collection configuration, not proof of verified identity or
successful learning. A/B are not a cross-day holdout. No accuracy, long-recording
acceptance, identity-learning acceptance or resolution of earlier A10 failures
is claimed. The frozen runner passed 239 finite controls, two actual-input byte
controls and preflight. One subsequent operational run completed both fixed
windows: 138 chunks and 1,440 seconds, with original child waits and clean
shutdown observed. A stopped snapshot confirmed the two sessions' actual
verified group mode and unchanged schema-1 policy. Of 32 retained history
attempts, 31 produced vectors and one retained a candidate-missing failure;
this failure is not discarded or counted as a successful voice observation.
These are bounded collection results, not recognition or learning accuracy.

## Restoration prerequisites

- Reconstruct and verify the backend, including the exact inherited policy file.
  Review and rebind prepare_manifest.py's source basename/index to the restored
  complete tree. Do not bypass verification or reuse an unreproduced old index.
- Regenerate local settings and fresh runtime/model/input bindings for the actual
  restored paths and fresh output. No settings JSON or measured inventories are
  included. Keep the explicit two-session profile and unchanged budgets.
- Independently acquire permitted public A/B and short input audio. The source
  readers retain fixed public input pins, but no audio or transcript is bundled.
- Review fixed workspace, interpreter and product paths before relocation.
  Real-input tests need the external recordings. Process controls require the
  named reconstructed baseline sibling; policy tests require the named restored
  product sibling and exact policy bytes.
- Settings comparison tests require intentionally omitted local settings, and
  the policy comparison test also references rev08 settings. Historical sibling
  source comparison controls require their named reference trees. Restore those
  prerequisites explicitly before claiming the affected tests can run; label
  reconstructed references as such rather than claiming historical recovery.
- runner.py intentionally refuses direct launch. Actual orchestration uses
  lifecycle_cli.py with explicit mode and newly verified bindings. Source
  restoration does not itself authorize execution or publication.

Only Python source/tests and this README are included. Actual settings,
manifests, runtime/model inventories, audio, transcripts, databases, logs, model
artifacts, credentials and internal recovery notes are excluded.

## Known inherited monitoring limitation

A later, separate continuity trial exposed an inherited sampled-RSS monitoring
race: a descendant exiting between enumeration and memory inspection raises
`psutil.NoSuchProcess` and aborts the trial. This checkpoint deliberately preserves
the historical revision-09 source, not an untested correction. A fresh monitor
revision and independent controls are required before further stability claims.
