# Private-input quality diagnostics: source-only checkpoint

Utilities take external/private inputs. This Git tree contains no corpus audio,
reference text, model assets, transcripts or raw runtime logs. Do not add them.
No network/model inference is performed by the included utilities.

- `validate_pcm.py LOCAL_WAV OFFICIAL_PREFIX --frames N`: exact prefix comparison.
- `paired_metric.py`: explicitly boundary-sensitive per-job oracle edit diagnostic,
  never persistent identity or complete-excerpt cpWER. Normalization is frozen;
  punctuation deletion can merge adjacent words. Do not repair this post-output.
- `context_budget.py`: isolated model-free arithmetic for the pinned Handy model.
- `python -m unittest -v`: synthetic controls only.

AMI data and manual annotations are CC BY4; official audio source:
https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/
Annotation source:
https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip
AMI samples considered here are EXPOSED diagnostics, not heldout benchmarks.
Word timestamps are forced-aligned. Gold must never be model input.

Pinned model: handy-computer/moss-transcribe-diarize-gguf revision
bfa3d24438711391d8713c6ab0efd6264527757c, Apache-2.0,
Q8_0 986899616bytes, SHA256
64ec654dc6ffcfdfe180422dffce1d33422b0c30959b7edfd131bad77ee35039.
Pinned native source:
https://github.com/handy-computer/transcribe.cpp/tree/63a44d9239d610b3908e8a66b384924cd4a77217
Existing runtime0.2.3; no install included or authorized by this checkpoint.

## Prospective whole-excerpt control — NOT AUTHORIZED TO EXECUTE
One direct-native call on externally verified monoPCM16/16kHz731648frames,
not the production20s adapter. No monkeypatch/guard changes. Same Q8 model,
CPU2threads, f16KV, auto language, diarize on, segment timestamps. n_ctx2048:
572audio+17marker+86fixedprompt=675prompt; native generation1272;
total1947<=2048. New bound/manifest must be explicit. Read-only metadata is
not a model run. Native public API does not expose generated count or EOS;
preserve nulls even if successful status and absent truncation support an
implementation-level EOS inference. Never claim content completeness.

Prospective supervisor:2CPU,6GiB AS,4GiB sampled process-tree RSS,300scompute,
60scleanup,365souter; fresh child and output directory; one attempt, no retry.
Expect ~1.5–2GiB RSS based on preceding short calls, not guaranteed. Fail closed
on resource, timeout, parser, truncation or cleanup failure. Keep exact raw result
private before interpretation. Verify model/runtime/source/input hashes first.

Use original whole-excerpt v2 scorer from an externally supplied pinned path
with exact hashes; unknown is retained as the same explicit hypothesis bucket.
Reference is the entire125words; baseline80edits/125. Do not compare against
three-job scores. No claim about stable cross-job identity or Chinese benefit.
No positive result alone promotes production; no meaningful improvement or
invalid/truncated result stops this Handy promotion investigation. Any later
harness must be source-checkpointed and reviewed before inference.
