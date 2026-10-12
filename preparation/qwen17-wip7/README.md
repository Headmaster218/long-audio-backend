# WIP7: Qwen 1.7B acquisition and candidate preparation source

Source-only, unfinished recovery checkpoint. This adds preparation material under
`preparation/qwen17-wip7/`; it does not change backend or runtime source.
Proposed branch: `preparation/rc3-dev3-qwen17-wip7`.
Sole backup parent: WIP6 `87a791d50ef36ae08d0938e4acae48f1d687e053`.
Backend source remains pinned to `2c841d84a9b3204e510628f4aef17fe90334e76c`.

## Historical status at this source snapshot

- The original acquisition qualification reported 16 passing model-free
  fixtures. The first real acquisition nevertheless failed after observing
  142,374,727 payload bytes; its exact `Refused` branch remained unknown.
  Its original ownership, terminal evidence and retained partial were preserved,
  and the process was independently confirmed reaped.
- A requested-byte pacing defect was separately established. The corrected
  downloader paces actual received bytes, retains consumed blocks before pacing,
  bounds the burst to one block, and reports bounded literal diagnostic codes.
  The correction is not a retrospective proof of the first failure's cause.
  Its 20 fixture methods were written but unexecuted at this source freeze;
  this checkpoint makes no corrected acquisition success claim.
- The optional historical-candidate projection helper is unexecuted source.
  Its private fixtures and lineage are omitted. There is no Qwen model load,
  inference, transcription, accuracy result, or runtime-readiness qualification
  represented here.
- The original native acoustic execution remains INCOMPLETE. A later reconciled
  zero-inference continuation does not repair or upgrade that original status.

## Public contents and exact transformations

- `prepare.py` and `test_acquisition.py`: exact original acquisition source.
- `pacing-v2/acquire.py` and `pacing-v2/test_pacing.py`: exact corrected source.
  The downloader includes retained generic predecessor acquisition helpers;
  their presence does not authorize acquiring other artifacts.
- `launch.py.template`: original launcher with exactly one string-literal
  substitution, replacing its private fixed Python executable path with
  `__LOCAL_PINNED_PYTHON_EXECUTABLE__`. All other source bytes and checks remain.
- `future-candidate/project_historical_candidate.py.template`: original pure
  helper with exactly its private lineage digest literal replaced by
  `__LOCAL_REVIEWED_LINEAGE_SHA256__`. The placeholder cannot match a SHA-256
  commitment, so projection remains fail-closed. Its EOS remains unobserved
  (`null`) and emitted alternatives cannot claim complete content.
- `ASSETS.public.json`: a public metadata projection of the original fixed
  three-asset design. Exactly `model_profile_source.path` and
  `product_manifest.path` were removed; every other parsed field is retained.
  The model is Handy's third-party Qwen 1.7B GGUF conversion, not an upstream
  Qwen GGUF distribution. The two runtime wheels are official PyPI 0.2.3
  artifacts. Exact public URLs, bytes, SHA-256 values, source revision and
  license metadata are retained, including provenance limitations: the model
  exact size/hash came from the reviewed product profile, not a new pinned
  metadata fetch. The model is recorded as Apache-2.0 and runtime as MIT.

The launcher and pure-helper copies are non-runnable, unqualified templates.
Both substitutions are exactly reversible from the private source mapping;
no checks were removed. No preparation sources were imported, and no tests, downloads or model calls
were run while creating this package. Static parse, hashes and privacy inspection only.

The public asset projection is not byte-identical to the original `ASSETS.json`
and must not replace it beneath the original integrity pins. Historical design
fields such as `no_asset_bodies_downloaded` describe that original design stage,
not the later failed transfer. Neither the projection nor the templates claims
execution-byte parity with its original input.

## Recovery limits and omissions

This is not a standalone test suite or runnable release. Original fixtures need
excluded local specs, metadata and pinned predecessor helpers. Corrected pacing
fixtures additionally need their excluded private launcher/driver and admitted
outer environment. The public layout deliberately does not supply those inputs.
References to local input filenames in code are not the inputs themselves.
Any recovery requires new local readiness, reviewed pins and separate execution
authorization; changing a placeholder does not establish qualification.

Only the eight explicitly inventoried public files belong to this checkpoint.
No audio, model or wheel binaries, transcripts, raw receipts, local manifests,
specs, approvals, settings, credentials, proxy/environment values, private
absolute paths, generated execution/session identifiers, private lineage,
private candidate tests or private design evidence are included. Local mapping
and privacy-review files are deliberately outside the public allowlist.
