# Private ASR input coverage evidence

The opt-in supervised CPU provider now attaches `asr_input_coverage` to its
existing, content-addressed raw evidence. There is no new table, public API,
inference pass, download, identity input, model, crop, or scheduler change.
`fragment_asr_coverage.py` is a new adapter layer; historical
`fragment_model_bridges.py` bytes and reviewed source pins remain unchanged.

## Meaning

Each core records its bounded SenseVoice attempt inventory. A record includes
the primary-mixture role, ticket, lifecycle status, planned selection, actual
read-relative integer 16 kHz frame selection when available, exact float32
compute-input hash/encoding, logical input support, intersection with the owned
core, and verifiable source mapping. The float32 input hash is not the PCM16 read
hash or the original source-file hash. Reserved-only selections never become
actual submitted input by implication.

- `submitted_union`: the union of core intersections having a durable
  invocation-boundary `execution_started` receipt. A process loss at that boundary
  can leave execution uncertain; this does not prove decoder return.
- `successfully_decoded_union`: the union having both a confirmed decoder return
  and a completed receipt. This names call completion, not transcription accuracy,
  intelligibility, or speech detection. Even a returned call is excluded until
  completion is acknowledged.
- `gaps`: the half-open integer-frame complement of the successful union within
  this core. It includes unsubmitted input and failed/unconfirmed calls. It is
  never a claim of silence. A fully committed session can legitimately have gaps.
- `has_text`: raw native token count greater than zero, before analyzer filtering.
  It can be true for punctuation alone. False does not establish silence; null
  means no token-presence receipt. A successful empty decode still contributes
  successful input support.

Context support is retained in each attempt, but only its intersection with the
current core contributes to these unions. Adjacent and overlapping decode inputs
are merged without double counting. No output endpoint, diarization interval,
punctuation, published segment, or previous core fills missing input support.
All intervals use integer half-open frames, including one-sample EOS tails.

Exact PCM16 source mappings retain clipped original owners across file seams.
For transformed canonical reads, only references to the existing validated
many-to-many native filter support are recorded; these are not relabeled as exact
original-sample ownership. No original audio is copied by the coverage layer.

## Persistence and recovery

Coverage is derived from the current core attempt's existing durable CPU meter
receipts and stored before the final evidence reference enters the existing
core/cursor/EOS transaction. A pre-commit crash leaves no advanced cursor; an
after-commit acknowledgement loss resumes from the committed reference. Retried
inference may create another attempt, but cannot create a duplicate core commit.
Attempt budgets are never refunded. Failed analyzer evidence is augmented when
the evidence writer remains authorized; cancellation can fence that write, while
the existing durable meter attempts remain retained. Erasure retains its existing
semantics and does not become bypassable through coverage recording.

The module bounds work to one 34-second read, at most 512 total call records,
32 ASR calls, 128 mapping parts, and a 512 KiB coverage envelope. It never scans
historical cores or retains recording-length audio. Deployments must rebuild the
explicit CPU manifest to pin the new module and the changed meter/provider bytes.
Old manifests are rejected rather than silently inheriting new behavior.

## Verification scope

`tests/test_fragment_asr_coverage.py` uses synthetic receipts/audio and fake
engines to cover failed/started/reserved/returned calls, empty and punctuation-only
tokens, overlapping inputs, source seams, current-core ownership, omitted tails,
one-frame EOS, cancellation, atomic transaction faults, and restart. CPU transport
tests exercise the new layer through the existing supervised fake-provider path.
These are model-free accounting tests. They do not establish real-model accuracy
or repair activity-gate omissions, and do not read private recordings.
