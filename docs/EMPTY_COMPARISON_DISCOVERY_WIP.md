# Empty-comparison discovery candidate: unqualified WIP

This is a source-only work-in-progress checkpoint based on commit `e140e01bf74cc74a77aac1abe84e0b017b9ef16f`. It does not change the package version or establish release qualification.

## Candidate changes

- A primary probe encountering an unbound comparison owner or an active comparison-start claim defers before census or admission. An exact, single-use refusal receipt remains valid across comparison-only transitions; native/global invalidations still reject it. The receipt grants scheduling-pending only. A later submit must pass fresh admission, source and census checks.
- Fresh history work first performs a bounded read-only availability query. No-work discovery acquires no comparison grant. A positive hint promotes the same coordinator record, thread, completion event and original deadline before the existing 192 MiB reservation and authoritative comparison path.
- Discovery and admitted comparison ownership remain tracked through cancellation, positive completion and thread join. Cached-prefix accounting, authoritative begin/currentness checks, native ownership checks and unknown-rival handling remain on the existing paths. Periodic discovery and concurrent work notifications are retained.

Only `audio_backend/native_execution_permit.py`, `audio_backend/moss_handy_jobs.py` and `audio_backend/recording_speaker_history.py` change production behavior. No schema, capacity, threshold, child-process or scheduler additions are included.

## Verification and limits

The exact production diff received independent static review. Static parsing and a generation-writer audit were performed. Two review findings were corrected before this checkpoint: refusal lookup now occurs inside the consume lock, and discovery hands finalization entirely to the existing comparison runner upon entry into that admitted runner.

No candidate tests have been collected or run. No candidate server, database operation, inference, model workload or actual-run comparison has been executed. Focused synthetic coordination tests and isolated SQLite helper coverage are being prepared separately. Existing baseline reproduction/control evidence is retained as historical evidence, not a candidate pass. Full `Automatic.step`/`submit_primary` integration remains a later required qualification.

This checkpoint makes no historical causal attribution, measured improvement, completed-fix, package-installation or release-readiness claim.
