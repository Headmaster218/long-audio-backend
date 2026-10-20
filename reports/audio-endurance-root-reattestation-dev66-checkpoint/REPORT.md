# Exact-root native re-attestation: dev66 source and finite qualification

Public checkpoint dated 2026-10-20. Product version 0.2.0rc3.dev66, frozen source
revision 02. This is an additive source/evidence report, not a project README,
deployment artifact, real-data attestation, or authorization to start a service.

## Outcome and scope

- The final product source passed 146 finite synthetic controls. Its installed
  payload passed 145 controls with one intentional source-specific deselection.
- A completed byte audit established equality across 138 product payload files
  in source, wheel and installation, with 145 installed files and 69 verified
  installed module origins. Six product modules changed from the dev65 base.
- The runner's initial 327-control selection retained 326 passes and one fixture
  error. After the fixture correction, a fresh selection passed all 327 controls
  and a separate two-generation synthetic lifecycle. The unchanged runner later
  repeated both finite successes under a newly reviewed launch attestation.
- No endurance verdict is established here. The execution owner reported a new
  actual attempt was launched. This packaging work did not read live output,
  inspect original data, or establish current progress or a terminal result.

Qualification is from existing completed evidence, not tests rerun by the
publication packager. The machine-readable summary and raw-evidence hash pins
are in `qualification.json`. Failed earlier attempts remain failed evidence.

## Product change: explicit admission, never binding repair

The durable native retention-store key is derived from the entire stored
six-field binding. Rewriting that binding to fit a new host root would alter
authority for native commits, retained cells, recording batches and collection.
This change does not rewrite the binding, mint a replacement retention key,
rebuild the database, delete records, or regenerate receipts.

Instead, a default-off option admits one exact old binding against one exact
current root identity. The only accepted difference is the root device; the root
inode, suffix ancestry, namespace identity and all five nonnamespace binding
fields remain exact. The stored binding and retention key remain immutable.
Actual current ancestry remains visible to all normal within-run fences.

The command line requires both `--native-root-reattestation` and
`--native-root-reattestation-sha256`, plus an authenticated inherited instance
lock. No environment override, implicit artifact discovery, generic skip-root,
wildcard, automatic refresh, or product-side artifact issuer is added. With no
opt-in, the existing strict mismatch refusal and normal new-store behavior stay
in force. Existing schema, runtime, receipt authority, retention and GC checks
are not weakened.

Read-only admission precedes directory creation and primary store initialization.
It requires canonical, bounded, hash-pinned evidence; exact baseline provenance;
safe current ownership and modes; held ancestor/file identity fences; and a
sidecar-free native database. The bounded immutable read does not run recovery or
change native schema/data. Registry reuse requires the identical admission and
lock objects. A failed child construction closes only its duplicate, leaving the
supervisor's ownership hold intact through positive stop and cleanup.

The supervisor still independently owns uploader-lock verification, conservation,
process exits and launch review. Instance-lock admission is not proof of these
separate responsibilities. A new exact root needs new independently reviewed
evidence; a previous artifact is not broadened or silently rewritten.

## Preserved failures and narrow revision

The first product candidate completed with 143 passes and three failures. It was
not relabeled a pass. Revision 02 changes only the new re-attestation module and
its test file relative to that candidate:

1. The parser preserves its own precise refusal reason before the broader
   ValueError handler. Both candidates refuse the noncanonical input; this is not
   a relaxation of acceptance rules or a change to the artifact schema.
2. The replacement-race control accepts only the two exact safe refusal paths:
   replacement can first reveal an unlinked opened file before the later
   replacement-equality fence. Actual replacement and unchanged rows are still
   required.
3. The independent in-memory GC backup explicitly enables and verifies foreign
   keys, proves dependent target rows cascade away, and only then asserts the
   positive key-eligibility case. Production GC behavior is unchanged.

The runner's first selection retained one synthetic observer-fixture error. The
corrected-fixture pass and later unchanged-source repeat are distinct completed
qualifications, not a merged pass count that hides the failure.

The execution owner also reported that an obsolete exact-root artifact was
refused before launch when the current root no longer matched. A fresh stopped
baseline, source conservation and a newly issued exact-root artifact were
reviewed separately before the unchanged runner was requalified. This account
is explicitly owner-reported. No actual artifact, binding or stopped-database
snapshot is included, and this packager did not reproduce that real-data work.

## What the finite controls cover

The product controls exercise populated native rows, receipts, proof and PCM
preservation across two direct owner opens; unchanged store metadata and retention
keys; positive and negative GC authority; no-flag refusal; all exact binding
fields; unsafe files, symlinks and hardlinks; content/replacement races; parser,
capacity, sidecar and deadline bounds; inherited-lock requirements; pre-store
ordering; close-only failure cleanup; registry isolation; ancestry drift; and
receipt-authority refusal after admission.

The CLI-to-factory-to-native-owner control spans two application lifespans with
inference and listener startup disabled. These are not two OS-process
generations. Existing inherited-lock, launch-order, CLI and supervisor ownership
controls accompany the new product tests. Runtime qualification is simulated.

Separately, each successful runner lifecycle reports two synthetic generations,
completed lifespans, positive stops, no survivors or reported errors, retained
ownership on an injected helper failure, and ownership checks through final
receipt persistence. These finite fixtures do not establish completion or
resource behavior of the actual endurance attempt.

The installed selection intentionally excludes one child-import test hardcoded
to source. An installed no-default-database/version/CLI-help probe runs before
pytest. The exclusion is not a skipped product failure. Existing external
runtime pins are unchanged, but this checkpoint does not newly qualify those
dependencies or replace the requirement for a fresh launch rehash.

## Exact publication source and patch

`source/` contains precisely the nine changed product files, byte for byte from
the qualified frozen revision. No redaction, path substitution, import guard,
or other transformation was necessary in these files. Generic API routes,
standard Linux self-inspection paths, synthetic fixture labels and a documented
placeholder path are source interfaces, not private deployment coordinates.
`provenance.json` records original, base and published byte counts and hashes;
the original and published hashes match for every file.

`product-dev65-to-dev66.patch` is the exact textual delta against the frozen
dev65 revision 02 base. Its headers use repository-relative paths. `source-delta.json`
records the nine-file before/after hashes. The baseline manifest identity and
qualified source manifest identity are pinned, without their private locators.

This is a partial source checkpoint, not a standalone wheel or full checkout.
The source subset does not include unchanged dependencies. Review the patch
against the exact base before any integration, keep the host project's existing
README, and do not install the isolated source directory as a complete product.
Source comments calling the files a proposal are intentionally unchanged:
they describe the preparation stage, while this report supplies the later,
separately completed qualification chronology.

## Privacy and evidence limitations

No private absolute filesystem locator, actual run/session/chunk identifier,
process identifier, actual binding or artifact, database/audio/model payload,
runtime location, launch credential, raw command line, live count or live log is
included. Raw manifests and receipts stay private; public evidence pins contain
content hashes and semantic roles only. Hashes are provenance commitments, not
independent public reproduction of omitted private evidence.

Observed filesystem fences and cooperative deadlines do not promise atomic
SQLite directory-relative opens, universal filesystem guarantees, hard OS-call
timeouts, or protection against every hostile same-UID race. Passing finite
controls does not establish acoustic accuracy, identity accuracy, endurance
completion or deployment readiness.

Packaging used only source/text parsing, existing completed-evidence reads,
hashing, patch generation and deterministic archive construction. It imported
no target product, executed no product tests, started no model/server/listener,
called no application API, opened no SQLite database and read no live output.
Publication and real execution remain separate owner-controlled actions.
