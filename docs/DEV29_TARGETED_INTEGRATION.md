# Dev29 targeted Chinese and AMI integration result

## Scope and completion

The targeted CPU run on 2026-10-16 completed with exit 0 in 391.911 seconds. It used the frozen dev29 source checkpoint, with installed-package parity verified separately. Its two complete recording scopes were Chinese speech padded to 120 seconds, followed by AMI speech padded to 100 seconds. The source clips contained 52.5 recorded seconds; the remaining 167.5 seconds were digital zeros. This is a bounded operational integration check, not an accuracy benchmark or a full-speech throughput test.

All eight pages were admitted and published: 3,520,000 samples at 16 kHz, or 220 seconds. The primary path committed all 220 seconds, retained audio verification passed, and every admitted automatic candidate drained. Final retained-pending and blocked-candidate lists were empty. Admission took 377.169 seconds under the existing pacing and backpressure rules; maximum observed accepted-minus-committed backlog was 79.76 seconds. This was slower than real-time delivery despite completing within the fixed work limit.

## Automatic results and quality limits

There were 11 committed automatic overlap results with exactly 11 validated native run attempts. Each receipt recorded one construction, one session and one run, with model/session closure, process cleanup and copied-source-pin release acknowledged. No manual MOSS POST was sent, and no attempt had unknown counts. The primary evidence also recorded 16 completed FrameMN calls and 11 observed SenseVoice batches.

The stored parsing outcomes were eight schema-clean results and three with invalid_model_timestamps. All 11 retain the timestamp-quality designation model_generated_unverified. Schema-clean does not establish correct transcription, speaker attribution, complete content or accurate word timing. The run adds no human-scored quality conclusion and does not make the three invalid-timestamp results trustworthy.

The retained validation category narrows those errors to reversed or equal endpoints and/or an end beyond the support duration. The exact rejected numeric pairs were discarded, so the saved records cannot recover them. This parser path does not add a frame-origin offset or clamp timestamps; the available evidence does not establish a frame-origin defect or a safe numeric correction.

## Historical persistence

After primary completion and orderly shutdown, the same owned data was reopened through normal application startup with workers and model features disabled. Both history lists together retained all 11 job identities. Each saved detail/result pair matched its pre-shutdown bytes exactly; the original saved committed snapshots also matched those pairs. No inference was repeated during this history check.

The passed persistence check concerns the retained structured results. It does not add raw decoder-output retention or establish global speaker identity. There was no scheduled primary-process restart during active processing; the models-disabled history restart is a separate stage.

## Timing, resources and cleanup

Inclusive elapsed time divided by committed audio duration was 1.7814. This wall-time ratio includes pacing, startup, primary and optional processing, backpressure, history verification and cleanup; it is not isolated model compute speed.

The fixed envelope used two CPU cores, an 8 GiB address-space limit, a 6 GiB sampled aggregate memory ceiling including 1.25 GiB of explicit reserves, and a 1 GiB complete-output ceiling. Work and nominal total limits were 900 and 1,020 seconds. The observed 64-job admission-stop threshold remained a stop on new admissions, not a hard native-call cap.

Peak sampled process-tree RSS was 1,886,470,144 bytes. Including reserves, the peak was 3,228,647,424 bytes. The sampler retained 1,558 observations with a maximum observed gap of 0.2825 seconds. Five descendants were observed concurrently at peak, and 231 cumulative process identities were recorded. Peak charged output was 23,789,568 bytes. These sampled values are not guarantees of instantaneous peaks. No resource violation or discarded server-log bytes was reported.

Fifteen HTTP 503 responses were retained. Temporary pending states settled before completion. Both owned servers received intentional SIGTERM, emitted shutdown-complete markers and were positively waited with return code minus fifteen. The original helper was positively waited with exit 0. No known surviving owned children remained.

## Reading the retained summary

The inherited fields primary=not_run, automatic=not_run, recording=not_verified and committed_candidates=0 were unfilled defaults. They do not negate the specific completion evidence: primary_verified=true, retained_audio_verified=true, 220 committed seconds, 11 saved committed results, 11 exact validated native attempts, history_byte_exact=true and empty pending lists. The original summary is preserved without rewriting those defaults.

The helper's overall label is passed_targeted_cn_ami_220_seconds. This is limited to this declared workload and its operational checks. It does not turn earlier failed runs into passes or establish 30-minute, 12–48-hour, Windows/Vulkan, continuous-speech or quality acceptance. Completion alone does not prove that every new contention-handling branch was exercised.

## Bounded longer-run proposal

A prospective follow-up can reuse the unchanged 1,800-second fixed four-scope workload with 3,600 seconds of work and 120 seconds of cleanup, preserving the same resource limits and observed admission-stop policy. Scaling the earlier incomplete run's inclusive ratio of about 1.50 and this targeted run's 1.78 gives roughly 2,696–3,207 seconds, leaving approximately 393–904 seconds within the proposed work limit. This is a planning range, not a completion guarantee: the runs differ in composition and size, and one reference run was incomplete.

That follow-up requires preflight of proportionate observer and evidence ceilings for the longer duration. Any cutoff, admission stop, unresolved candidate or unknown attempt must remain visible in the result. No longer run is qualified by this document.
