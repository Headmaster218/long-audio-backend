# dev74 optional overlap preparation

Code at commit 39451ad adds explicit bounded post-primary MOSS-TD direct multi-voice candidate recognition, not waveform separation. The mixed baseline stays intact. Exact original-source/export binding is required; legacy unbound exports are refused.

Independent code review cleared six files and 29 focused tests. Broader source regression: 157 passed, 3 receiver-wire dependency skips, 53 subtests. Fresh target qualification passed 9 gates and 41 controls, followed by independent same-host receipt review and isolated materialization. Separate optional Python 3.12 runtime has complete base dependencies and exact model-runtime hash verification, but is not strict-qualified.

No model calls or acoustic results yet. The fixed panel has six eligible cases totaling 69.56 seconds, including only one eligible Chinese overlap. Prior source exposure is known; no held-out claim. Runtime launch and resource/lifecycle review remain pending. Hash checks and two output voice labels do not establish successful recognition of both speakers.

Summaries contain no audio, transcripts, private gold, models, database files or credentials.
