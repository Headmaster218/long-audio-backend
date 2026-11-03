# Recorder segment profile preflight

The Android development source defaults to **16 kHz mono and 300-second segments**. Its editable duration field accepts **1–3600 seconds**. This is a source-code fact, not proof of the configuration installed on a physical phone.

Default five-minute segments are not close to the backend's 16-piece page limit: the actual 294-second page / 120-second piece arithmetic peaks at four pieces per page across that profile's alignment cycle. Very short configured segments are different: a one-second profile accepts fourteen full deliveries then refuses the fifteenth. A nineteen-second profile can progress to 874 seconds before its next delivery is refused. Whole-second profiles of at least twenty seconds avoid this particular fragmentation limit; other limits still apply.

## The practical default limit is delivery count

The incremental wrapper deliberately retains the existing default of 64 deliveries. At five minutes each, that is 5h20m of full rotations, **not twelve hours**, even though the independent software frame cap defaults to twelve hours. A STOP-bearing tail must end before the exact count boundary.

A roughly seven-hour capture with five-minute rotations needs about 85 delivery slots when reserving a nonempty STOP tail. The existing explicit `--max-inputs 128` option can accommodate it. That option must be selected when starting a NEW intake job; it cannot rewrite a frozen existing journal. At five minutes per segment, even 128 inputs reach only 10h40m of full rotations, so twelve hours needs a different planned profile or separately qualified history-capacity work.

No defaults, device settings, source-slot limits, coalescing behavior or EOS rules are changed by this checkpoint.

## Run the read-only planner before starting

```sh
python -m audio_backend.recorder_capture_profile \
  --segment-seconds 300 --expected-seconds 25200 \
  --max-hours 8 --max-inputs 128
```

This plans roughly seven hours with one hour of frame-cap headroom and enough existing input slots. Use the same `--max-hours 8 --max-inputs 128` options when creating the incremental job. The planner prints JSON and exits with code 2 when the expected duration plus a necessary STOP tail does not fit. Without an expected duration it reports the profile's finite prefix bounds and warnings.

Compare the unchanged defaults:

```sh
python -m audio_backend.recorder_capture_profile --expected-seconds 25200
```

That plan reports the input-count limitation and refuses the seven-hour expectation. A one-second or nineteen-second profile reports the first source-slot refusal. The planner performs **no media/job-state access, HTTP requests, recorder changes or ingestion**. Normal Python startup still reads code and may create bytecode caches. It only simulates the existing admission reservation and source-piece rules for unchanged mono16k rotation segments.

Results distinguish the independent duration cap, input count, source-slot exhaustion, peak pieces within the planned prefix, and maximum frames ending in a nonempty shorter STOP tail. They do not guarantee disk, network, native processing speed, long-duration resource stability, device behavior, accurate transcription or physical continuity. Path/metadata and other runtime refusal conditions still apply.

## Exact-boundary STOP is a separate unresolved limitation

The producer immediately seals a ROTATION and clears its open writer at the target frame count. If recording is stopped before another PCM frame arrives, there is no open tail to emit a STOP manifest. The backend therefore sees a last ROTATION and **must not infer EOS**.

For an expected duration exactly divisible by the segment target, the planner reserves one additional input and at least one additional PCM frame for a nonempty STOP tail. That is a minimum arithmetic reservation, not a guarantee that a physical device can stop at a chosen sample. If a recording has already ended with only ROTATION and no STOP declaration, the current explicit intake cannot finalize it. Do not fabricate metadata or bypass the STOP check. A separate trustworthy terminal-declaration workflow remains future work.

## Source and validation scope

Android defaults and accepted ranges are defined in the active `RecordingSettings.java` overlay; `MainActivity.java` exposes the duration field. `PcmSegmentWriter.append` immediately finishes ROTATION at its byte target, while `stop` only seals when a staging writer exists. The source assembly instructions select these overlays over the older baseline.

Backend page/piece limits and the admission reservation are taken from `recording_uploader.py` and `recorder_capture_intake.py`. The planner uses those constants without changing either path. Tests cover the default, explicit 128-input case, tiny profiles, count/frame boundaries, exact rotation termination and invalid settings. These are arithmetic checks, not another audio/native qualification run.

Independent review compared a separate interval-overlap oracle against 86,400 profiles (all 1–3600-second settings across several hour/count caps), with no mismatch. The backed-up independent test defaults to 696 representative profiles and six calls with media/state/file APIs, sockets and subprocess operations blocked after import; `--full` repeats the larger arithmetic matrix. These checks do not exercise audio or native inference.
