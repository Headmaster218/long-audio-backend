# Early MOSS owned-controller source checkpoint 14

This is a small, evolving UNQUALIFIED source snapshot. Fixture tests, ownership qualification, full implementation review and final source freeze are pending. It may be superseded by later corrections. It is not a release or an execution-ready package.

The default app, API and scheduler remain unchanged. The proposed controller is an explicit local developer entry; it is not wired into ordinary application routes or scheduled work. No model or native call was made to prepare this checkpoint.

## Included source

- audio_backend/profiles/moss_handy_q8_0.json: explicit experimental Handy Q8 CPU profile and product identity pins.
- audio_backend/moss_handy_profile.py: closed profile and model/runtime identity checks.
- audio_backend/moss_handy_adapter.py: bounded raw-first diagnostic projection with unknown EOS, anonymous model-local labels and unverified model-generated timestamps.
- audio_backend/moss_handy_runner.py: separate one-call child, parent admission, fixed scratch bounds and explicit raw-output scope.
- audio_backend/moss_handy_controller.py: proposed volatile one-job owner, cancellation, original-handle cleanup and guarded result publication.
- README.md: this snapshot notice.

All five product/profile files are byte-exact copies from one stable copy window. The current narrow corrections address parent identity around admission, explicit raw scope, loader-context refusal, scratch-directory identity binding and an early PCM bound. Their presence is a source observation; their behavior has not yet been qualified by the pending tests and review.

This checkpoint contains only source. Dependencies and any model/runtime assets must be supplied and independently verified before authorized execution. No release manifest, gate result, test receipt, execution authority, model, audio, transcript, raw result, private local path, credential or captured environment value is included. Public model/revision pins and the ordinary product runtime-manifest integrity pin remain source constants. Private copy records and review files are excluded.
