# Installed optional native export and legacy compatibility, dev78

The default-off `--native-turns` option now passes through generated bundles and finite analysis plans. Native mode writes three hashed output artifacts (`export.json`, `transcript.txt`, `native-turns.json`) plus the completion manifest. Existing default exports retain two artifacts. Candidate inference stays separate and never replaces the primary transcript.

## Actual installed verification

- A fresh three-generation plan processed one previously exposed nine-second public AMI recording crop. It completed two handoffs and all three native exports, preserving 144,000 exact PCM frames. No optional candidate model was called.
- The original test harness then failed on a stale recovery-directory assertion. That failure is preserved. Separate read-only validation used the real output configuration and installed receipt validator, then checked PCM, source evidence and selectors successfully.
- The previously unexecuted stopped repeat completed with zero HTTP, writes, mutations, services or models. No primary work was replayed.
- The actual old dev73 default plan also repeats successfully under installed dev78 with zero HTTP or mutation; its original bytes and identity attributes remain unchanged.

The earlier real compatibility failure came from reconstructing old recovery headers with a newer request-cap default. The fix accepts only the two known published caps, 41,050 and 73,978, and retains each original cap. Unknown caps, invalid types, expanded usage and tampered bindings remain refused. No old receipt was rewritten.

444 selected regression tests passed, including 41 new compatibility controls. Fresh same-host qualification separately passed 9 gates and 41 controls. These are scoped checks, not a full-repository or user-server approval.

## Limits and retained failures

Native export combined with automatic revision-recovery is explicitly refused before effects. Candidate processing is still explicit. The nine-second crop proves operational wiring, not detection or recognition accuracy, physical recorder capture, sustained concurrency, or long-duration use. Empty overlap selections are not proof that the audio contained no overlap.

The dev77 predispatch harness failure, old-receipt compatibility failure, and dev78 post-plan layout assertion remain documented. Separate validation success does not rewrite them into original harness passes. Earlier measured silence hallucination and boundary-unscorable candidate results also remain unchanged.

This checkpoint contains documentation, counts and hashes only, not audio, transcripts, models, databases or credentials.
