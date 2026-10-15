# CN-Celeb open-set speaker verification pilot

Research date: 2026-10-15. This is a sanitized, independently checked bounded
research result, with reusable score-policy source and synthetic tests. It is a
custom 20-identity subset experiment, not an official CN-Celeb benchmark result.

## Main result

The unchanged fixed policy recovered no known-person queries on held-out
identities. A calibration-only alternative recovered 7 of 14, with seven known
queries still abstained. Neither policy made an observed false-person assignment
on this tiny evaluation sample. Zero errors here do not establish deployment
safety or a population false-accept rate.

Each role has a separate seven-person enrolled gallery and three unknown people,
with two queries per person: 14 known queries, 6 unknown queries, 20 total. The
calibration and evaluation human identities and galleries are disjoint. Both
roles use the same frozen feature extractor.

| Role / policy (cosine threshold, margin) | Known correct /14 | Known wrong /14 | Known abstain /14 | Unknown accepted /6 | Unknown rejected /6 | Accepted /20 |
|---|---:|---:|---:|---:|---:|---:|
| Calibration: fixed (0.75, 0.10) | 2 | 0 | 12 | 0 | 6 | 2 |
| Calibration: selected (0.55, 0.15) | 11 | 0 | 3 | 0 | 6 | 11 |
| Held-out: fixed (0.75, 0.10) | 0 | 0 | 14 | 0 | 6 | 0 |
| Held-out: selected (0.55, 0.15) | 7 | 0 | 7 | 0 | 6 | 7 |

False-person assignments are known-wrong plus unknown-accepted: 0/20 for every
row. Overall abstention is respectively 18/20, 9/20, 20/20 and 13/20; rejecting
unknowns is desirable, so overall abstention is not an error rate. Correct-person
precision among accepted queries is respectively 2/2, 11/11, undefined (0/0),
and 7/7. Held-out known recovery is 0/14 versus 7/14 (50%), while accepted coverage
is 0/20 versus 7/20 (35%). These denominators are not interchangeable.

At the identity level, the held-out selected policy recovered both queries for
one of seven known people, one query for five, and none for one. The fixed policy
recovered none for all seven. Calibration fixed recovery was one query for two
people and none for five; calibration selected recovery was both queries for
four people and one for three. With two queries per person, known recovery macro
rates equal the query rates. Queries from the same person remain correlated.

## Score distribution summaries

Scores are cosine similarities. Values below are aggregate summaries rounded to
six decimal places, not score matrices. Each role contributes 140 pair scores:
14 genuine known-person pairs, 84 wrong-enrollment pairs from known queries,
and 42 pairs from unknown queries. The latter two together are 126 nontarget
pairs. Top-two margin has one value per query, rather than one per pair.

| Role | Distribution | n | Minimum | Mean | Maximum |
|---|---|---:|---:|---:|---:|
| Calibration | Genuine | 14 | 0.461452 | 0.648678 | 0.875369 |
| Calibration | Known-query impostor | 84 | -0.119907 | 0.118792 | 0.448478 |
| Calibration | Unknown-query impostor | 42 | -0.041363 | 0.179747 | 0.525886 |
| Calibration | Top-two margin, all queries | 20 | 0.082953 | 0.322020 | 0.580300 |
| Held-out | Genuine | 14 | 0.346893 | 0.545667 | 0.737708 |
| Held-out | Known-query impostor | 84 | -0.102482 | 0.095052 | 0.425929 |
| Held-out | Unknown-query impostor | 42 | -0.084619 | 0.131875 | 0.354703 |
| Held-out | Top-two margin, all queries | 20 | 0.027296 | 0.227090 | 0.550198 |

All held-out genuine scores are below the fixed 0.75 threshold. The observed
lower held-out genuine mean than calibration is descriptive; it is not a
population shift estimate. Pair scores share queries and enrollment models and
cannot be treated as independent samples. No EER is reported.

## Frozen protocol

- Use opaque dataset labels, never real-name matching or voice-attribute inference.
- Metadata eligibility requires official enrollment, at least two speech queries
  whose dataset (genre, session_id) groups do not occur among the original
  enrollment components, and mono 16 kHz uncompressed PCM16. Original enrollment
  duration is 2–120 seconds; each query is 2–40 seconds, inclusive. No cropping,
  reconstruction, augmentation, VAD, resampling, or quality-based replacements.
- The duration constraints leave 20 of 22 session-group-eligible identities.
  Membership was fixed before inference. No score, embedding or audition was
  used to select people or queries.
- Deterministic ordering is ascending SHA-256 of UTF-8(seed + NUL + domain + NUL
  + value), then ascending exact value to break hash ties. The seed is
  `cnceleb-verification-pilot-v1-20261015`; identity ordering uses domain `identity`
  with opaque identity IDs, and query ordering uses domain `test` with exact
  archive member names. First ten identities are calibration, remaining ten
  evaluation; first seven within each role are known and final three unknown.
  Each identity contributes its first two eligible hash-ordered queries.
- The original concatenated enrollment is retained for each known person.
  Unknown people have no enrollment in their role's gallery. All selected
  official trial labels and full waveform lengths were checked in the private
  execution. No member was silently replaced and no denominator was reduced.
- The existing `eres2net-base-zh-cn-3dspeaker-16k-v1` extractor is frozen and runs
  serially on CPU with two extractor threads. Complete PCM16 is converted to
  float32 / 32768; finite nonzero 512-dimensional embeddings are L2-normalized
  and compared by float32 dot product. No threshold is transferred from a
  different embedding architecture or official xvector recipe.
- Rank gallery scores descending, breaking ranking ties by opaque ID ascending.
  Accept the highest-scoring identity only if its score is at least tau and its
  gap above second place is at least margin. An exact top-score tie always
  abstains, even if margin is zero.
- Fixed policy: tau=0.75, margin=0.10. Study grid: tau in
  {0.55, 0.65, 0.75, 0.85, 0.95}, margin in {0.05, 0.10, 0.15}, plus always-abstain.
  Select using calibration only: minimize false-person assignments, then maximize
  known correct recovery, then prefer larger tau, then larger margin. The
  always-abstain candidate wins when no zero-error candidate recovers any known
  query; that outcome would not be a useful operating point.
- Persist the selected policy before computing or inspecting any evaluation
  embedding or score. Apply both frozen policies once to the separate held-out
  gallery. No evaluation retuning, template auto-update, production threshold,
  model-weight or production-code change occurred.

The successful run's policy freeze occurred after 27 calibration embeddings;
the first evaluation call started afterward. Separate gallery hashes were
unchanged before and after scoring. Independent checking recomputed decisions, rates and distribution summaries
from recorded scores and reconciled all 54 calls and chronology.
The public package intentionally omits exact private sample mappings, so it
reproduces the scoring policy and its tests, not the original inference run.

## Execution and resource history

The first attempt exceeded its 1 GiB sampled RSS target. It retained 27 completed
calibration embeddings and the policy freeze, then stopped with no held-out
result. A held-out native call had already begun; zero completed held-out
embeddings were recorded. This was not an untouched held-out execution boundary.

A prospective resource-only retry preserved the frozen protocol, membership,
model and preprocessing. Its sampled RSS target was 2 GiB, address-space cap
4 GiB and aggregate memory envelope 6 GiB. The 6 GiB aggregate envelope is an
externally reviewed budget target, not a machine-wide enforced quota.
Work/outer deadlines were 540/600
seconds; selected audio and logs were bounded by 40/4 MiB. It completed all
54/54 embedding calls and 280/280 pair scores with no missing planned query,
in 40.4535 seconds. Sampled peak RSS was 1,153,732,608 bytes across 396 samples;
maximum sample gap was 0.14508 seconds. Sampling can miss brief peaks and stalls;
these figures are observations, not a hard peak-memory guarantee. The sampler
joined, no child process was recorded, and the original process was positively
waited to exit code 0.

The original plan's planning-time statement that no execution had occurred is
historical. It is not the status of the completed experiment described here.

## Interpretation, rights and privacy

This small, duration-filtered, speech-only, seven-model-gallery experiment does
not validate broad speaker recognition, production diarization, ASR, overlap
handling, long recordings or real-world unknown-speaker rejection. Six unknown
queries from three people per role cannot establish low-error tails or a useful
population FAR bound. Calibration recovery is an in-sample optimization result;
the held-out recovery is the relevant limited check of that selection.

Dataset session-group separation is verified, but recording dates, source videos,
devices, true acoustic independence and cross-day robustness are unverified.
Two selected queries from one person may share a test session group. Whether the
exact pretrained extractor saw these identities or other CN-Celeb material is
unverified; this report makes no pretrained-identity-disjoint claim.

The [official CN-Celeb terms](https://www.cnceleb.org/dataset) prohibit commercial
use and retain original-owner copyright. This pilot uses the archive for
research/noncommercial purposes. This package redistributes no archive, audio,
transcripts, real names, embeddings, raw per-person score matrices, private
runtime paths or operational identifiers. The official acquisition recipe is
linked in the frozen private plan; it is not an authorization to redistribute
the dataset. Only aggregate observations and independently authored protocol
source are included here.

## Reusable protocol-only checks

`score_policy.py` is a standard-library-only reference accepting existing scores
under opaque IDs. It validates seven-person galleries and 7-known/3-unknown,
two-query role geometry, implements the decision and calibration objective, and
requires a freeze receipt before invoking a lazy evaluation loader. It rejects
cross-role identity/query overlap. Its freeze callback is a contract, not proof
of durable storage or proof the caller never previously inspected held-out data.

`test_score_policy.py` uses invented scores and identities. It tests inclusive
gates, exact ties, nonfinite scores, separate error counts, null precision,
conservative grid selection, false-person priority, always-abstain fallback,
role leakage, geometry, failed freezes, callback ordering, immutability and
invariance of selection to held-out scores. It has no network, audio, model or
private-runtime dependency. Run from this directory with:

```sh
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest -v test_score_policy.py
```

Validation passed all 16 tests in 0.019 seconds on the exact source
hashes recorded in the manifest. The isolated standard-library run used a
256 MiB address-space cap and a 30-second timeout with a 5-second kill grace;
its original process returned exit code 0. Unit checks cannot prove model
accuracy or reproduce the private trial. No new inference or acquisition is
part of this package.

## Provenance

SHA-256 of frozen inputs (the inputs themselves are not distributed):

- Plan: `6e115edc8955f478b320c836b279030a7715e85c35a52ec751753eb3b9941c2e`
- Completed result: `da1b7e4be1d3109932c285ca70730faf44feea890e198180ec313b6dfea0d8ee`
- Resource-only retry source: `e73cf1bbda6b4f88272d4cef17518deddb28c2a167fc58f0a93a522f335a2d23`
- Successful policy freeze: `07fc876de8dd0b64fa3dc1af8d9972147a2abf8abad1649aeeba512ed62edfb3`

`MANIFEST.json` records package-file sizes and hashes, excluding its own hash to
avoid self-reference.
