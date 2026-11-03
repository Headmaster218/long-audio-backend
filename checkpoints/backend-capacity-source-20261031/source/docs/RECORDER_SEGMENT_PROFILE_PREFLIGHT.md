# Recorder segment profile preflight

The Android development source defaults to **16 kHz mono and 300-second segments**. Its editable duration field accepts **1–3600 seconds**. This is a source-code fact, not proof of the configuration installed on a physical phone.

Default five-minute segments are not close to the backend's 16-piece page limit: the actual 294-second page / 120-second piece arithmetic peaks at four pieces per page across that profile's alignment cycle. Very short configured segments are different: a one-second profile accepts fourteen full deliveries then refuses the fifteenth. A nineteen-second profile can progress to 874 seconds before its next delivery is refused. Whole-second profiles of at least twenty seconds avoid this particular fragmentation limit; other limits still apply.

## Twelve hours requires 144 five-minute deliveries

New incremental jobs default to **192 inputs**, also the maximum selectable input cap. The independent frame cap still defaults to twelve hours. At 300 seconds per delivery, exactly twelve hours is **144 deliveries**. With a terminal-aware producer, the last full delivery can explicitly declare STOP, so 144 input slots are sufficient. A cap of 143 is insufficient; 144, 145 and 192 all accommodate this arithmetic profile. Extra input slots do not extend the twelve-hour frame cap.

Previously frozen jobs keep their stored limits. In particular, existing 64- or 128-input jobs are not upgraded by a changed CLI default: at five minutes per input their full-delivery capacities remain 5h20m and 10h40m. Select the intended limits when starting a NEW job. Do not rewrite a journal to increase its frozen caps.

The planner takes its input default and maximum from the uploader's shared append constants. No device setting, source-slot limit, coalescing behavior or EOS rule changes here. These are capacity calculations, not sustained twelve-hour runtime qualification.

## Run the read-only planner before starting

```sh
python -m audio_backend.recorder_capture_profile \
  --segment-seconds 300 --expected-seconds 43200
```

The default producer mode is `terminal-aware`, matching the current terminal-aware Android producer path. The default plan fits exactly twelve hours with 144 deliveries under the 192-input default. An explicitly selected `--max-inputs 144` also fits. Use the corresponding caps when creating the incremental job; preflight does not change an existing job or verify the installed producer.

For a legacy eager-rotation producer, explicitly select compatibility mode:

```sh
python -m audio_backend.recorder_capture_profile \
  --segment-seconds 300 --expected-seconds 43200 \
  --producer-mode legacy-rotation-only --max-hours 13 --max-inputs 145
```

That plan reserves a 145th delivery with at least one additional PCM frame after twelve hours. It needs both count headroom and frame headroom: `--max-hours 12` fails even with 192 slots, while `--max-inputs 144` fails even with thirteen hours of frame capacity. At a nonexact boundary (for example 43,199 seconds), both modes need 144 deliveries, with a shorter last delivery explicitly marked STOP.

The planner prints JSON and exits with code 2 when `expected_capture_with_stop_fits` is false. Without an expected duration it reports the profile's finite prefix bounds and warnings. A one-second or nineteen-second profile reports the first source-slot refusal. The planner performs **no media/job-state access, HTTP requests, recorder changes or ingestion**. Normal Python startup still reads code and may create bytecode caches. It only simulates the existing admission reservation and source-piece rules for unchanged mono16k segments.

## Explicit STOP, never inferred EOS

The legacy eager producer immediately seals a ROTATION and clears its open writer at the target frame count. If recording is stopped before another PCM frame arrives, there is no open tail to emit a STOP manifest. The backend therefore sees a last ROTATION and **must not infer EOS**.

The terminal-aware Android writer keeps one full segment staged until another nonempty append or explicit close. A normal stop can publish that full segment directly as STOP. Backend admission accepts this metadata shape without a schema or gate change. Historical ROTATION-only files remain nonterminal in either planner mode. Selecting `terminal-aware` does not turn historical ROTATION metadata into STOP, authorize fabricated metadata, guarantee a physical stop at an exact sample, or bypass the terminal declaration check. See [exact STOP validation](EXACT_BOUNDARY_STOP_INTEGRATION.md).

## JSON fields and compatibility

The planner emits `recorder-capture-profile-plan-v2` with an explicit `producer_mode` and `explicit_stop_declaration_required=true`.

- `maximum_frames_with_stop` / `maximum_seconds_with_stop` describe the chosen producer mode's capacity for an explicitly declared STOP.
- `required_inputs_with_stop`, `minimum_additional_tail_frames`, and `expected_capture_with_stop_fits` describe the expectation under that chosen mode. Exact-full terminal-aware STOP needs no additional tail; legacy exact-full ROTATION needs one additional delivery and at least one frame.
- Retained `maximum_frames_with_nonempty_stop_tail`, `maximum_seconds_with_nonempty_stop_tail`, `required_inputs_with_stop_tail`, and `expected_capture_with_tail_fits` always describe a **strictly shorter** STOP tail, independently of mode. They must not be substituted for selected-mode fit. `minimum_additional_frames_for_shorter_stop_tail` makes this separate assumption explicit.
- `full_rotations_before_limit` and `full_rotation_frames_before_limit` retain their historical names: they measure the full-sized prefix capacity, not the count of actual ROTATION declarations in a terminal-aware recording. For a completed exact-full recording, its final full-sized segment is STOP instead.
- `no_default_changes=true` means invoking the read-only planner changes no settings; it does not claim this software version's defaults are unchanged from earlier versions. `no_automatic_eos=true` applies in both modes.

Results distinguish duration, input count, source-slot exhaustion and peak pieces. They do not guarantee disk, network, native processing speed, long-duration resource stability, device behavior, accurate transcription or physical continuity. Path/metadata and other runtime refusal conditions still apply.

## Source and validation scope

Android defaults and accepted ranges are defined in the active `RecordingSettings.java` overlay; `MainActivity.java` exposes the duration field. The legacy `PcmSegmentWriter` constructor immediately finishes ROTATION at its byte target, while the terminal-aware factory defers publication until the next append or close. Source assembly instructions select these overlays over the older baseline.

Backend page/piece limits and admission reservation come from `recording_uploader.py` and `recorder_capture_intake.py`. Tests cover terminal-aware and legacy modes, exact and nonexact boundaries, old 64/128 caps, the 143/144/145 transition, 192-input maximum, tiny fragmented profiles, and invalid settings. An independent interval-overlap oracle checks both modes and separately verifies shorter-tail capacity. The representative matrix contains 2,088 profiles; `--full` checks 259,200 profiles across all 1–3600-second settings. Six planner calls also run with media/state/file APIs, sockets and subprocess operations blocked after import. These are arithmetic and no-operation-I/O checks, not audio/native or device qualification.
