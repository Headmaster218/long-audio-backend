# Fixed three second development representation measurement

19 October 2026. Status: measurement completed and independently accepted.
This report concerns one exploratory D3-versus-D2
representation contrast on two already exposed development meetings. It does not
establish a deployment choice, safe linking, or a general improvement.

## Design and execution

The frozen population contains 13 original observations: four in rank 3 and nine
in rank 4. Each D3 input is 48,000 contiguous frames at 16 kHz, centered in the same
previously selected eroded component. It extends the original centered
32,000-frame D2 support by exactly 8,000 frames on each side. Three seconds was the
only whole-second extension beyond D2 fitting every fixed component; it was an
engineering choice, not an established model optimum.

One owned invocation completed exactly 13 new D3 encoder calls, totaling 39 seconds
of waveform input. All 13 byte-exact accepted D2 controls were reused after fresh
runtime/input gates and exact original-baseline replay. There were no fresh D2 or
DF calls, extra observations, alternative windows, padding, pooling, or retries.
Weights, frontend, normalization, score arithmetic, thresholds, admission,
original source/mapping metadata, and original rivals remained fixed.

Both meetings' D3 predictions, mappings, vector hashes, and failures were sealed
before reference evaluation. Actual D3 source geometry, exact-source/PCM aliases,
and physical overlap were recomputed. Original witness, alias, dependency, and
cannot-link constraints were preserved. Reference annotations affected evaluation
only; annotation overlap did not become an algorithmic constraint.

## Full population and reference coverage

All 13 D3 vectors were present. Natural-pair denominators remain six and 36;
reference exclusions do not remove observations or rivals. The following counts
are identical for D2 and D3.

| Development meeting | Observations and vectors | Natural pairs | Reference-scorable pairs | Reference-unscorable pairs | Same / different scorable pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| Rank 3 | 4 / 4 | 6 | 3 | 3 | 3 / 0 |
| Rank 4 | 9 / 9 | 36 | 36 | 0 | 16 / 20 |

Rank 3 has three single-speaker observations and one annotation-overlap
observation. Its annotated overlap grows from 0.1305 seconds to 0.660 seconds,
but it remains the same unscorable observation, with no new missing coverage or
label-derived algorithm constraint. The three pairs involving it are excluded
from the paired reference comparison in both arms. Rank 4 has nine single-speaker observations.
Neither meeting has missing vectors or observations classified as missing
reference coverage, partial speech, silence, or unreadable reference.

All six and 36 pairs pass the retained source-independence checks: no different
embedding spaces, same-source aliases, or overlapping source ranges exclude a
pair. This does not make pairs sharing observations statistically independent.
Original witness accounting is unchanged: rank 3 retains 16 rows, eight partially
bound and eight unbound; rank 4 retains three partially bound rows. Historical
skips remain 32 and 25 respectively, with unknown causes; this experiment does not
reclassify them.

## Paired representation scores

These are normalized-float32 dot-score differences, D3 minus D2, on the common
reference-scorable, source-independent intersection. Values below are rounded to
seven decimals.

| Meeting and reference class | Pairs | Minimum delta | Median delta | Maximum delta | Positive / negative |
| --- | ---: | ---: | ---: | ---: | ---: |
| Rank 3 same speaker | 3 | +0.0446469 | +0.0626243 | +0.0899604 | 3 / 0 |
| Rank 4 same speaker | 16 | -0.0411102 | +0.0395272 | +0.1484620 | 13 / 3 |
| Rank 4 different speaker | 20 | -0.1071676 | +0.0174210 | +0.2139183 | 13 / 7 |

There are no rank-3 different-speaker comparison pairs. Rank-4 movements are
mixed: positive same-speaker medians do not establish general improvement, and
the different-speaker median also rises. Descriptively, rank 4's minimum
same-speaker score minus maximum different-speaker score changes from 0.0712031
to 0.1879757; its maximum different-speaker score falls from 0.4073771 to 0.3141591.
Descriptive AUROC is 1.0 in both arms on this small exposed sample. None of these
quantities validates a threshold or linking policy.

D3 eligibility, published links, false-merge counts, precision, link coverage, and
policy abstention outcomes are unavailable/null, not zero. No D3 linking policy was
evaluated. DER and CER were not run.

## Preserved control failure and terminal accounting

The initial four-method offline invocation exited 1: three methods passed and one
errored because its fixture expected adapter refusal while the genuine helper
raised `CheckError`. The correction changed only the expected exception to
`helpers.CheckError` with the anchored `crop_original_mapping_changed` reason and
added a final zero-encoder-call assertion. A separately authorized one-method
continuation passed. Thus four distinct controls passed across five method
executions in two invocations, not four fresh rerun passes. The initial failure
is retained; neither fixture invocation performed native inference.

The actual measurement's original owned handle returned exit 0 with one owner
timing record: 2.883 seconds wall and 5.017 qualified CPU seconds, including the
1.002-second owner allowance. Sampled/high-water charged RSS was 215,236,608 bytes.
The 60 result files contain 659,977 logical bytes; allocated storage including
directories is 847,872 bytes. These are qualified receipt measurements, not
continuous host-wide containment evidence.
The original positive terminal wait and source-reviewed childless process model
provide the recorded cleanup basis; no separate final process census is claimed.

D3 waveform input per observation is 50% longer than D2. No isolated paired D2
end-to-end timing was measured, so the standalone 2.883-second runtime does not
measure a pipeline slowdown.

## Limits and stopping point

D3 changes speech content as well as duration. The two exposed development
meetings, matched views, and dependent pairs provide no independent confirmation,
duration-only causal estimate, or person/session/room generalization. Existing DF
results are context only. Reserved and confirmation data remain untouched.
There is no threshold, calibration, model, or product change, no development-selected
winner, and no further duration search. Work stops at this fixed measurement and
its post-seal report.

## Provenance

SHA-256 commitments identify the reviewed design and retained evidence; those
payloads are not included here.

- Prospective design: `6daeab1323f687c23019d9ccce8e235d4910717fe341c7f7a158c6b8dc871410`
- Source/control-reuse review: `706bc8b34eb0c705fac1af1fac77543c3f72d3b93ad3f32bf22b61a7395404fb`
- Combined offline acceptance: `0fdc926370ff820885f628cc32873fcd358e8824c9529b002b68d64880f3f556`
- D3 representation seal: `6309e709cf704a80fb4c848056281380bb8c2a6a3509850ba2361627aca8c9fa`
- Saved score output: `4b2ab52e8dc51f0a7b07792987ea7b805b9df1be6332d3c0846cf48f0445b2c3`
- Owner terminal receipt: `508ce2592eae190322f79c4f9e353a29756ffb22b2e700597787ad890fc445b1`
- Independent terminal acceptance: `f008499926fd4a70f560d92c594d8b63a0354bb685fda99af25d3b0d44f01452`

This public source-only report contains no audio, vectors, transcripts, per-crop
labels, crop coordinates, observation identifiers, or host paths.
