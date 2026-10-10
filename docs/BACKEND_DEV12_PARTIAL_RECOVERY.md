# Backend dev12 partial phrase recovery candidate

Version `0.2.0rc2.dev12` introduces `partial_schema_valid_phrases_v1` as a bounded
candidate-source feature for eligible failed overlap supports. It does not change
the strict inference parser or normalize, clip, repair or reorder the original
model timestamp tokens. Established coordinate ownership still translates
support-relative values into the existing window/source representations. A partial
candidate is evidence for existing overlap ownership and persistence checks, not
a claim that the whole failed support succeeded.

Only complete canonical structured output from eligible timestamp-only failures
can contribute independently schema-valid phrases. Exact rational comparisons
against the decoded support frame bounds determine eligibility. Retained phrase
timestamps, original phrase indexes and full-support speaker slots preserve their
source meaning. Existing strict-success behavior is preserved; independent
malformed/schema-invalid evidence cannot become a successful strict parse.
The runtime validators and public additive provenance distinguish this recovery
path. All committed examples and tests are synthetic.

## Source and historical proof

The published baseline is commit `5b71122f17e4a72cac6ad4120d1c707d9b2133fb`, tree
`7e7547e1b49473dd24c69887e772b4580014c236`, with ordered parents
`366797b5c0bba70975507b71db8b4596b6d9344f` and
`5cca518467e5d03eaced884f73798917542f79fd`. It has exactly 283 source files and
3,317,327 bytes. The dev11 source validator retains its original exact 19-path
composition, pinned runtime bytes, modes, policy and mutation rejection tests;
it now reads this immutable tree rather than treating today's dev12 index as
dev11. The earlier complete-allowlist test likewise retains its original 283-file
assertion on that verified dev11 tree; the current dev12 gate separately requires
exactly 288 files. Its combined runtime regressions still exercise the current runtime.

The published dev11 commit has the exact tree and ordered parents of locally
qualified commit `f02e3e08dce8b52280741c67fba4d3eb5f81c1f8`. Its server-authored
commit ID differs; the source tree is identical. The dev12 historical proof names
only the verified published baseline, so a public-only clone never needs the
local unpublished commit object. The original historical mapping file stays
unchanged; this explicit successor reference belongs to the dev12 source proof.
A credential-free fresh clone of that published dev11 baseline passed 264
source/history/integration tests and strict Git object checks. All 283 file bytes
and modes matched, with original private commit objects absent and no alternates.
Those are baseline results; dev12 requires its own final-tree qualification.

The dev12 manifest pins its exact baseline and every added or changed path except
its own recursively defined hash. Its own schema, types, scope, order and canonical
serialization are checked exactly. The independent current-source test pins the
reviewed runtime and supporting changed artifacts, exact path delta, version,
worktree/index agreement and modes. It verifies unchanged inherited source,
including both strict inference parsers, historical manifests, public mapping,
source guard and privacy exclusions. Existing original private commit objects
must remain absent; alternates, replacement refs, grafts and shallow history are
rejected. The published commit and its exact tree must be available without private objects.

There are exactly five added paths and ten changed inherited paths, for 288
source files. The aggregate source ceiling remains 3,400,000 bytes, the per-file
ceiling remains 250,000 bytes, and the allowlist adds only those five paths.
Neither compression nor new eligibility exceptions are used to satisfy the cap.

## Qualification

Run the focused synthetic recovery tests, exact dev12 source/mutation tests,
immutable dev11 source tests, unchanged historical source proofs, then the full
default model-free suite on the final staged tree. Build and inspect the wheel
from an exact source export and use workers-disabled local HTTP checks. Report
exact source identity, actual outcomes, skips and expected failures. A listed
command or a synthetic result never establishes real-model quality acceptance.

No model download, private fixture publication, paid inference, default model or
threshold change, automatic timestamp normalization or release acceptance is
part of this source candidate. Later real-model validation requires separate
bounded authorization and fresh isolated state.
