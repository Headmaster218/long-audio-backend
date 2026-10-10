# Bounded recovery: no clean pass (2026-10-10)

The original continuous screen remains FAIL. A known-failure post-hoc diagnostic
improved its counterfactual score; a separate prospective recovery screen still
failed its quality gate. Do not merge these experiments or present either as
stable, general, or production-ready success. All inherited evidence is unchanged.

## A. Two known failures: post-hoc counterfactual

Split the two already-known truncated 20-second Qwen parents into four fresh
10-second calls. Both child pairs succeeded without truncation or repetition
flags. Only their parent outputs were replaced, keeping the full original
4,182-character denominator and every original record.

- Original Qwen: 1,637 errors, 39.1440% CER, immutable FAIL
- Counterfactual Qwen: 1,272 errors, 30.4161% CER
  (206 substitutions, 1,017 deletions, 49 insertions)
- SenseVoice unchanged: 1,360 errors, 32.5203% CER
- Counterfactual: 6/8 meetings nonregressing, no final repetition/truncation flags

This used known failed parents after seeing the original run. Passing descriptive
checks is not a clean first-pass gate pass and supplies no generalization proof.
The original result and its failure are preserved under ../continuous/.

## B. Prospective new supports, same meetings: FAIL

A new cohort and status-only recovery policy were frozen before its model
outputs: eight new excerpts in the same eight meetings, disjoint from all prior
eight continuous excerpts and 56 Chinese oracle clips. It contains 4,000 reference
characters and 696.15 seconds per baseline/base-Qwen arm.

- SenseVoice: 1,416 errors, 35.40% CER (S193 / D1204 / I19)
- Qwen first pass: 1,718 errors, 42.95% CER (S510 / D935 / I273)
- Qwen recovered: 1,370 errors, 34.25% CER (S207 / D1146 / I17)
- Recovered Qwen was nonregressing in only 4/8 meetings; at least 6/8 required

The overall recovered score improved, but the frozen quality gate failed.
Neither corpus familiarity nor speaker/meeting independence was established.

There were 40 SenseVoice base calls, 40 Qwen base calls and four Qwen child
calls: 84 actual calls. Of 32 full 20-second parents, two native-status-18 parents
triggered paired recovery. The other 60 potential children were not attempted,
not failures. Qwen submitted 736.15 seconds including the additional 40 seconds;
SenseVoice submitted 696.15 seconds. All 44 Qwen calls had independently checked
fresh-session evidence. Both recovery outcomes were verified by replay through
the actual qualified pure module. This did not evaluate a production adapter.

Observed execution-phase wall time was 846.8586 seconds, peak owned RSS
1,513,394,176 bytes and peak aggregate RSS 1,531,228,160 bytes (about 1.513 GB and
1.531 GB, respectively; decimal units). These are sampled phase observations,
not whole-project wall time, per-model latency or kernel-enforced memory limits.
Bound pre/postflight verification and original-handle cleanup passed; successful
execution does not turn the quality FAIL into a pass.

## Files, reproducibility and limits

- `PROTOCOL.md`, `policy.json`: the distinct experimental protocols and frozen rule
- `selection.json`: public source/sample coordinates, without text or private IDs
- `results.json`: count/status summaries, gates, actual calls and exact resource units
- `pins.json`: public qualified recovery source and inherited model/runtime pins
- `test_snapshot.py`: offline count, support and policy consistency checks
- `ATTRIBUTION.md`: corpus and model licensing scope

Run `python -B -m unittest discover -s recovery -p 'test_*.py'` from the snapshot
root. Existing ../continuous/score.py supplies model-free whole-excerpt CER;
no new recognizer, recovery executor or acquisition helper is bundled here.
Full source-to-result execution reproduction is not automated by this snapshot.
Private raw/audit evidence was reviewed, but is not shipped as a public execution
attestation. No audio, transcript/prediction text, credentials, internal artifact
hashes/IDs, machine paths, process identifiers or approval logs are included.

These are annotation-assisted outer-edge, overlap-order-sensitive text screens.
Internal fixed cuts are not gold-aligned. There is no end-to-end, diarization,
identity, timing, independent-meeting or unseen-training validation. No backend
default, runtime API, proof gate or version is changed by this evidence snapshot.
