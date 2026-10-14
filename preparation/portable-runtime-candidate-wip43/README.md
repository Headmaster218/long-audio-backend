# Portable runtime candidate checkpoint WIP43

This is a source-only checkpoint of the frozen v5 offline runtime recipes.
The ten packet files are copied byte-for-byte. This README is the only added
public file. The checkpoint contains source, schema, lock metadata, and synthetic
fixture definitions. It contains no installed runtime, model weights, audio,
private configuration, operational receipts, or execution authority.

## Exact packet

The ten-file packet totals 458,439 logical bytes. Its PACKET_MANIFEST.json SHA-256
is 9c074b9712153efef618bd152cc8d1e99fa3f063f705170134b7f23b0bf971e7.
That manifest pins the other nine files; this README is outside that manifest.

The manifest and embedded source-map/lock status fields are preserved historical
source-preparation records. Fields such as tests_executed=false describe their
frozen preparation state. They are not current run receipts and must not override
the explicitly scoped private results summarized below.

## Candidate-byte and actual-mapping mode

The candidate host mode is CANDIDATE_BYTES_AND_OBSERVED_MAPPINGS. It checks exact
sealed candidate bytes and records actual observed mappings under finite read
budgets. Static native dependency closure remains incomplete. First-observed
mapping hashes and stable descriptors alone do not establish qualification;
ordinary imports require separate independent review of the observed mappings.

The source restricts this candidate mode to runtime-build-install and
runtime-import. Inclusion of either phase in the recipe does not authorize it.
A candidate byte match, a wheel audit, or a produced wheel does not qualify an
installed runtime, native dependency closure, or model execution.

## Results known at checkpoint preparation

- Twelve focused checks passed privately against this exact frozen v5 packet.
  This establishes only that focused coverage, not a complete-suite result.
  The generic public recipe has not been executed or qualified for a new host.
- A separately controlled private candidate build reached its 540-second work
  deadline. A backend wheel was produced, and a partial runtime was retained.
  The original supported-tool wait was positively completed and cleanup was
  confirmed. The guard's final_source_pin_verified field was false.
- That attempt did not complete or qualify the runtime. Approximately 26 MiB of
  partial library output remains retained and charged. No successful final-source
  verification, accepted final runtime inventory, or runtime-import result is
  claimed for the attempt.
- Follow-on continuation preparation is separate from this checkpoint. Neither
  this source copy nor these status statements authorize reuse, deletion,
  continuation, another build, or an import.

## Deliberately limited claims

There is no completed installed-runtime, ordinary-import, provider, model,
inference, genuine-upload API, or CellStore qualification. There is no new audio,
TextGrid, CER, or acoustic-accuracy result. Synthetic checks, candidate-byte pins,
observed mappings, wheel production, positive waiting, and cleanup are distinct
facts and do not confer any of those qualifications.

## Reuse requires fresh local qualification

These public sources remain a generic recipe. A new environment requires fresh
explicit configuration, reviewed local paths and host evidence, independently
accepted input and owner evidence, finite allocation admission, and an explicit
local phase release before execution. Historical pins, synthetic identities,
placeholder namespaces, and preserved status flags are not portable permission
or proof that another machine is qualified. Missing prerequisites must refuse
closed. Static closure remains incomplete unless separately established.

This checkpoint preparation inspected source as data, copied the frozen packet,
and created review metadata. It did not execute or import the packet, run its
tests, read runtime or model/audio assets, mutate Git, access the network, or
publish a commit. Private evidence remains outside this public payload.
