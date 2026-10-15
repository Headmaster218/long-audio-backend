# Overlap API and passive-capture diagnostic source checkpoint

This eight-file source packet contains two smoke-helper templates, two exact
product modules, a focused-test template, configuration-field documentation and
a public integrity manifest. It records one completed historical functional
milestone. The public templates have not been executed. No audio, transcript,
raw API receipts, runtime configuration, database or execution log is included.

## Observed functional milestone

The original diagnostic helper exited 0 after two adjacent 10.5-second uploads
from an official AMI recording. Both uploads completed, producing 16 primary
segments and genuine native overlapping-speech evidence. A current retained
20-second seam composite, support frames [8000, 328000) at 16 kHz, was dispatched
to exactly one MOSS job through the normal API. The job reached
`returned_diagnostic` with `process_cleanup_verified=true`; its structured and
441-byte raw outputs were retrieved through the API. The structured result had
nine diagnostic phrases and two local speaker tags. These tags do not identify
real people.

Total helper time was 37.484 seconds. Peak sampled process-tree RSS was
1,855,344,640 bytes. Cleanup waited and found no known live child processes.
This is a sampled observation, not a hard memory guarantee. The server's -15
return code reflects the helper's shutdown and is distinct from the helper's
successful exit.

A retained owner warning remained: `optional_sql_or_io_failure`, with one dropped
operation. Earlier capture failures remain unresolved. This successful path does
not establish that the warning's cause was fixed or that all optional captures
succeed. EOS, generated-token count, word timing, speaker identity, transcript
accuracy, content completeness, durability and full production robustness are
not established by this run.

## Source changes and test status

- `smoke-idle.py.in` adds a bounded, fail-closed idle check before reading cells.
  It preserves the distinction between missing eligible seam cells and missing
  overlapping speech. Its candidate pins target the earlier product baseline.
- `smoke-diagnostic.py.in` uses the diagnostic store/capture pins and otherwise
  retains the idle helper's behavior. Both helpers require fresh output locations
  and keep the existing natural-overlap proof checks and one-job dispatch gate.
- `native_owned_cell_store.py` and `native_owned_cell_capture.py` are byte-exact
  diagnostic source copies. They add bounded fixed-field diagnostics around
  existing optional failure handling. They do not change the limits, SQL,
  retries, primary outcomes or fail-closed behavior.
- `test_diagnostic.py.in` preserves the focused comparison tests. Nine model-free
  tests passed on the original source before installation, using fresh in-memory
  SQLite only. That historical result is not an execution result for this public
  template. The test intentionally uses fabricated private-looking strings to
  check that exception text and paths never enter diagnostics.

The diagnostic record cap is 32 per process, with at most 1,024 characters per
record. Logging is best-effort and does not replace the original refusal or
capture outcome. A passing diagnostic test demonstrates its masking behavior;
it does not diagnose the earlier retained failure cause.

The five public source files were parsed and compiled in memory without running
them. Placeholder substitution is reversible byte-for-byte to the frozen source
inputs; product files require no substitutions. No public helper, model, service
or comparison test was executed while preparing this packet.

## Local configuration

Replace the placeholders in the three `.py.in` files using
`CONFIGURATION_FIELDS.json`. Keep every integrity pin unchanged. The packet is
not a standalone runtime: actual mode needs the pinned Python 3.12 interpreter,
product dependencies, models, FFmpeg, psutil, source lock and native installation
settings. The source lock and local settings are deliberately not bundled.

For the comparison test, supply a separate pre-diagnostic `audio_backend`
package that matches both `BASELINE_PINS` and includes its supporting modules.
Keep the two included diagnostic `.py` modules beside the test template. A
package already overwritten with the diagnostic modules is not the baseline.

Helper model-free mode is `--self-test`; actual mode is separately selected by
`--run`, with optional `--port` (default 8765). Resolve the template before use.
Neither mode was run on these public templates. The idle helper and diagnostic
helper target different product revisions; do not change pins to suppress a
mismatch or infer that both can run against one unchanged installation.

Existing bounds include a 60-second optional-idle wait, 1,680-second work limit,
1,800-second total limit, CPU affinity 0-1, nice 19, a 6-GiB sampled aggregate RSS
ceiling, 128-MiB data and 144-MiB total output caps, 4,096 output entries,
256-KiB retained server log and 5.5-GiB free-space floor. HTTP responses and
pagination are bounded. These are sampled/cooperative limits, not kernel quotas.
There is no synthetic-overlap fallback or automatic inference rerun.

## AMI source and attribution

Credit: AMI Meeting Corpus, University of Edinburgh and partners. The historical
fixture used [official ES2002a Array1-01 audio](https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/ES2002a/audio/ES2002a.Array1-01.wav),
cropped to source seconds [510.5, 531.5), rebased to zero and split at 10.5 seconds.
No audio is included in this packet. The [official AMI download page](https://groups.inf.ed.ac.uk/ami/download/)
identifies the signals as [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
This attribution describes the external corpus; it does not relicense the code.

Reference intervals came from BUTSpeechFIT's [AMI-diarization-setup](https://github.com/BUTSpeechFIT/AMI-diarization-setup/blob/2509d8933721023fab4def2618aabd5c28eb82e9/README.md),
commit `2509d8933721023fab4def2618aabd5c28eb82e9`,
[`only_words/rttms/train/ES2002a.rttm`](https://github.com/BUTSpeechFIT/AMI-diarization-setup/blob/2509d8933721023fab4def2618aabd5c28eb82e9/only_words/rttms/train/ES2002a.rttm).
Credit for that setup: F. Landini, J. Profant, M. Diez and L. Burget,
“Bayesian HMM clustering of x-vector sequences (VBx) in speaker diarization:
theory, implementation and analysis on standard tasks.” Reference timing is not
sample-accurate ground truth; only-word references omit non-word vocalizations.
No full-source audio hash or independent listening-based accuracy is claimed.
