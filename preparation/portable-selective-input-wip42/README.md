# Portable selective-input checkpoint WIP42

This is a source-only checkpoint of the frozen v3 preparation recipes. The ten
packet files are copied byte-for-byte, with no substitutions or new execution
authority. The additional README describes the checkpoint status. The packet
contains source, schema, lock metadata, and synthetic fixture definitions only.
It contains no installed runtime, model weights, audio, or raw run evidence.

## Exact packet

The ten-file packet totals 407,840 logical bytes. Its PACKET_MANIFEST.json SHA-256
is fc83222d8c809cf98883fa1bbc6a000f7037e52fd0cbfb2cf7bd41cff8d54190.
That manifest pins the other nine files; this README is outside that manifest.

The manifest and embedded source-map/lock status fields are preserved historical
source-preparation records. Statements there such as tests_executed=false or
focused_execution_pending describe their frozen preparation state. They are not
updated run receipts and must not override the explicitly scoped results below.

## Results known at checkpoint preparation

- The inherited 89-fixture suite was run on the older packet. It was not rerun as
  a complete suite on this frozen v3 packet.
- v3 received 16 focused synthetic checks. The initial checker observer error
  remains preserved in the private evidence; the corrected checker passed.
  This is focused synthetic coverage, not complete-suite or model qualification.
- The wheels-only audit of all 30 wheel inputs passed and its plan was sealed.
  Auditing compressed wheel inputs does not establish an installed runtime.
- The three-model archive audit reached its work deadline after two archive
  progress completions. There is no accepted combined three-model plan.
  Progress markers alone do not establish an accepted terminal result.
- WAV-only prefix selection/extraction and direct 21-second clip derivation
  passed in 62.47 seconds. The original owner was positively reaped. Independent
  acceptance is complete: the known full selected-WAV hash was verified, and an
  independent bounded channel-0 comparison matched the derived clip.
- The derived source clip contains exactly 336,000 frames at 16,000 Hz, mono
  PCM16 (672,044 WAV bytes). It uses channel 0 of source frames
  [23,400,320, 23,736,320), without resampling or padding. The full selected source
  WAV remains distinct from this derived mono clip.
- Both older combined-audit owners remain UNKNOWN. These newer results do not
  establish those owners' terminal or cleanup status.

## Deliberately limited claims

The selective route stops after the first exact known WAV member. It relies on
previously accepted full-archive provenance and current descriptor continuity;
it does not perform a fresh full compressed-archive hash. It establishes neither
gzip-suffix/trailer validation, tar EOF, nor global member-name uniqueness.

No TextGrid was extracted through this route, and no CER or acoustic-accuracy
result is claimed. There is no installed-runtime, ordinary-import, provider,
model-inference, genuine-upload, or CellStore qualification. Archive progress,
wheel plans, synthetic checks, and WAV byte acceptance do not confer those
separate qualifications.

## Reuse requires fresh local qualification

These public recipes remain templates. A new environment requires fresh explicit
configuration, reviewed local paths and host closure, independently accepted
input/owner evidence, finite allocation admission, and an explicit local phase
release before any execution. Historical pins, placeholder namespaces, synthetic
identities, and preserved status flags are not portable authorization or evidence
that another machine is qualified. Missing prerequisites must refuse closed.

This checkpoint preparation only inspected and copied source and created review
metadata. It did not execute or import the packet, install a runtime, read model
or audio assets, or publish a commit.
