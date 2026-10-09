# Supplemental positive-event seam protocol

Implementation-only, private research. No inference has been admitted or run.
This isolates the gap left by the 95725570 eight-arm actual-model comparison:
its 108 Zipformer calls were equal across transport arms but every event list
was empty. Positive event retention was therefore not established.

## Selected exposed development sources

Choose two from the existing twelve already-scored development clips. Eligibility
requires at least 20 seconds for a fixed excerpt. Greedily maximize additional
cached emitted non-silence public labels, break ties by numeric source ID. This
selects 180174 (driving/music/subway), then 421591 (adds door). This is deliberate
selection on previously exposed model-positive development predictions, not a
blind evaluation, strong acoustic truth, or onset gold. Cached Vehicle support
must not be called proven road driving. Rain remains uncovered.

No original clip reaches 30 seconds. Instead define A=180174[0,20s) and
B=421591[0,20s), using existing mono16kPCM16 conversion bytes unchanged. AB and
BA are separate 40-second synthetic canonical arrangements. The artificial
join at 20 seconds is disclosed; no natural acoustic continuity is claimed.
There is no crossfade, gain, enhancement, re-resampling or event tuning.
Licenses, original/converted hashes and cached evidence hashes are frozen.
No confirmation clip, download, or listening is involved.

## Transport and ownership

Each arrangement has whole and split-plus-duplicate arms. Split at 7.125,
29.25 and 35.125 seconds; the second physical fragment additionally begins with
the preceding 1.25 seconds copied exactly. A real p1 matcher produces the
ownership plan, without being supplied trim gold. The expected trim is an
independent generated-fixture check after matching. The continuity/replay
exclusion declaration concerns slices of the generated canonical transport,
not a claim that the original A and B captures are continuous. The synthetic
canonical arrangement contains no intentional repeated excerpt. No robustness
claim for unknown real-world replay is made.

Every arm must reconstruct the canonical owned bytes exactly. The unchanged
LogicalRunner makes cores [0,30), [30,40) with reads [0,32), [28,40). Detector
scheduling remains 5-second windows, 2.5-second hop, top_k527, threshold0.25,
release_ratio1 and silence-peak1e-5, with detector-internal final padding only.
There are 12+4 model-call opportunities per arm, 64 maximum across four arms,
and 320 padded inference seconds. No model result is reused between arms.

## Actual event-only path and scope

The event-only adapter calls the unchanged detector and uses the unchanged
AnalyzerBridge, private evidence store, LogicalRunner and durable RuntimeStore.
The native scheduler provider is an explicitly synthetic immediate frontier
with empty turns. Lexical segments are empty. No ASR, speaker embedding,
native diarization or MOSS model is constructed. This is not a full pipeline,
full Analyzer implementation, or diarization test.

Exact comparisons include logical-read hashes, actual padded float32 inference
input hashes, schedule/provenance, raw events and details, raw confidence and
source labels, window IDs, core-clipped intervals, and event instance IDs.
An independent projection oracle checks retention/order/count and clipping of
every raw event. Original confidence is retained in linked evidence; the
published logical event schema intentionally has no confidence field.

The detector already merges touching evidence and divides it into <=5-second
segments. Core ownership clips those records. The test adds no cross-core
merge; distinct per-read evidence remains distinct. Counts are evidence segment
counts, not occurrences. Raw records permit review of confidence changes and
repeated supports across the logical boundary without concealing them.

Success additionally requires nonempty non-silence published output for both
fixtures, positive support crossing at least one physical cut, and a common
label whose owned support reaches and begins at exactly 30 seconds. Absence
means incomplete positive coverage even if exact transport equality passes.
No retuning, retry, alternate source selection, or claim of accuracy follows.
A separate fake-tagger/model-free test checks digital silence's zero-call path;
it does not add real silence accuracy evidence. Fake test scores are labeled
and never represented as actual cached or new model outputs.

## Admission, resources and commands

Default `proposal` and `freeze` load no model. The frozen manifest binds committed
source files, selected source/converted/cache bytes, model/labelmap, interpreter,
installed numpy/Sherpa/core/psutil payloads and licenses. It is a local integrity
check, not an adversarial filesystem security boundary. Model-free preparation
uses CPU7, <=256 MiB sampled own RSS, <=64 MiB total checkout+artifacts, host
available RAM >=2 GiB, and free disk after writes >=5.5 GiB. Hashing streams data.

A future run requires an independent root admission receipt containing the
exact freeze digest, four-arm list, call/frame budgets, maximum one model load,
exact absolute output directory and stage, explicit authorization reference, CPU7 and 512 MiB own RSS, and a fresh concurrent-project roster or
explicitly approved external-feed observation contract. Aggregate project cap
is 6 GiB. This proposed model budget is not approved by this document. The
existing 50ms watchdog provides sampled enforcement, outer300s and per-core45s
limits, not an OS-hard bound. No process outside this owned experiment may be
signaled. An exclusive attempt marker in the approved output directory is file-
and directory-fsynced before model imports or construction. Reusing that receipt
in another output directory is refused; reusing its original output is fenced
before models. A separately approved future rerun can use a new receipt/output;
there is no global registry or implicit retry authorization.

Every analyzer/model attempt is durably reserved before provider execution and
never refunded. The root attempt-state preserves model-load status, known partial
budgets/call records and ordinary construction/compute failure details, including
failures occurring before bridge evidence exists. If reporting fails, the primary
exception remains primary and owned cleanup is still attempted. Both watchdog
closure and owned-tree termination are attempted even if either cleanup fails.
The initial marker records conservative interrupted accounting: absent a durable
terminal state, charge the full approved ceiling plus one model load, including
unknown hard kills. No new model is attempted when reservation persistence fails.
The unchanged bridge's raw detector failure evidence supplements this root journal.
The event adapter retains the original provider exception so a secondary failure
while the unchanged bridge writes failure evidence cannot replace that exception.
No automatic retry is allowed.

Use the existing audio-backend/.venv/bin/python, taskset -c7 and environment
OMP_NUM_THREADS=OMP_THREAD_LIMIT=OPENBLAS_NUM_THREADS=MKL_NUM_THREADS=
NUMBA_NUM_THREADS=1, PYTHONDONTWRITEBYTECODE=1. All output lives under this
isolated checkout's reports/private_* tree; no model files are copied.

- Model-free test: `python -m pytest -q -p no:cacheprovider tests/test_positive_event_seams.py`
- Freeze after committing source: `python scripts/benchmarks/positive_event_seams.py freeze --output reports/private_positive_event_preparation`
- After separate review/admission only: `python scripts/benchmarks/positive_event_seams.py run --freeze reports/private_positive_event_preparation/freeze.json --approval <root-receipt.json> --output reports/private_positive_event_run`

The receipt is an operational record, never independent authority. Output
comparisons can establish event-only transport invariance on these selected
synthetic fixtures, not correctness of the model's acoustic interpretation.
