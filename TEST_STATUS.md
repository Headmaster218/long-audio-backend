# Public-source validation scope

This is a newly curated source-only test suite, not the full private development suite. Historical recovery validation and model measurements do not automatically apply to this exported tree.

The public suite covers runtime/API behavior, storage, queues, boundaries, identity guards, model setup logic and fake-worker subprocesses using synthetic inputs. Real media/clinical corpora, full gold annotations, operational endurance records, private packaging tests, and benchmark replay tests that depend on omitted assets are excluded. Private-input and optional local-asset cases skip explicitly; the current integration section lists their exact counts and scope.

The first exact source import exposed two portability dependencies: a test temporary directory under an excluded validation tree, and a source-pin assertion reading a private operational report. The next commit moves the temporary directory to the project filesystem and retains only reviewed public dependency-pin fields for the assertion.

## Exported main result

- Command: `python -m pytest -q -p no:cacheprovider`
- Linux, Python 3.12; reused existing development dependencies without downloads.
- 568 passed, 1 skipped in 24.88 seconds; one installed FastAPI/HTTPX deprecation warning.
- After size-limit/secret-pattern hardening of the publication tools, the 8 publication-workflow tests passed again.
- Optional skip: local MOSS-TD model validation assets absent. No model inference was run by this source-only gate.
- Python source fingerprint: `6d75179e5fdd0f4919927c501dc4e3580886f678c2bb09ff33a51ddc47ce0cf2`. Algorithm: SHA256 of compact JSON containing policy-ordered `[relative Python path, file SHA256]` pairs, encoded as UTF-8.
- Public gate: all 152 allowlisted files passed path, size, text/binary, secret-pattern, private-identifier, structured-vector, manifest and version checks. All local Markdown links resolve.

These are exact exported-source checks; no claim is made that this equals the full private-development test suite. No real-model quality, long-duration stability, Windows/Vulkan execution or GPU memory acceptance is claimed.


## 0.2.0rc2.dev0 support-lineage integration

- Entire exported suite: 590 passed, 1 optional-asset skip, one dependency deprecation warning in 24.76 seconds.
- Same command and source-only/model-free scope as above.
- Python source fingerprint: `da63051c27f7483a097e0aa659854a3d53913eedb1d28f3f933a940505042cb4` using the algorithm above.
- 22 additional synthetic tests cover exact pooled sample support, bounded incomplete metadata and coordinate projection validation.
- No automatic enrollment, template updates, runtime support persistence or identity-quality acceptance was added.


## Immutable-source synchronization check (historical dev0)

- Entire exported integration suite after adding committed-source sync: 591 passed, 1 optional-asset skip, one dependency deprecation warning in 24.22 seconds.
- Python source fingerprint: `298bdd1c6900f18e58172bdc8f762247bfed7afad1cf1f5b133e0ea2977fd09a` using the algorithm above.
- A new synthetic Git fixture proves the proposal reads the selected commit and ignores later working-file edits. Missing source files are reported, never deleted automatically.
- `SOURCE_CONTROL_PROVENANCE.json` records the reviewed local source commit, matching file hashes and intentional public adaptations. At that checkpoint, primary source remained at 0.2.0rc1 and the isolated public integration was 0.2.0rc2.dev0.


## Reviewed Phase A and event-provenance import

- Immutable source: `86c1b7983206a8e83877a3384a91ba0b04a6316e`.
- Entire exported feature suite: 676 passed, 1 optional MOSS-TD local-asset skip, one dependency deprecation warning in 32.32 seconds.
- Python source fingerprint: `60bfe2eb080611f750eaf3f1c655855f4caf12b4661fc5b283e6ad3ed51be5c0` using the algorithm above.
- Includes 85 additional synthetic event-provenance and acoustic-support cases; excludes the local array-study implementation/tests and all raw study evidence.
- Runtime relative-import closure and local document links pass. Public packaging guards and document adaptations remain intact.
- Capture remains default-off. No identity learning, purity certification, model replacement or long-duration/real-model acceptance is claimed.


## Historical 0.2.0rc2.dev1 public integration

- Both public development version fields now match the exact reviewed primary source version, `0.2.0rc2.dev1`; historical `main` stays at `0.2.0rc1`.
- Entire curated suite after the version update: 676 passed, 1 optional-asset skip, one dependency deprecation warning in 29.51 seconds.
- Python source fingerprint: `d68b3045f240c8a41403a73eb255d7b81eb0cdaf414045825a5f9c96e67e34a0` using the algorithm above.
- Source comparison verifies 129 byte-identical paths and 12 intentional public-edition adaptations.
- No inference study/model promotion, raw reports, source recordings, voice vectors or corpus-person identifiers were imported. No tiny real integration result is claimed by this source snapshot.


## Historical 0.2.0rc2.dev2 public integration

- Immutable reviewed runtime/source projection: `839187b89c833fe7f0457daee38edcfdb0d0f0a7`; only the separately reviewed public HTTP summary is taken from document commit `697e02e9b6c82d289a34aeb05736982fbf8c1872`.
- Entire curated public suite: 793 passed, 1 optional local MOSS-TD asset skip, one installed dependency deprecation warning in 49.63 seconds. This is a new public-tree run, not the private integration suite.
- 117 additional synthetic cases cover embedding-space proof, original-vector bindings, explicit review bank admission/integrity, matching, lifecycle and safe projections. No real model inference or downloads were run by this suite.
- Existing Python 3.12 dependencies were reused; import resolution was verified to the public worktree. Numerical-library thread limits were set to one.
- Python source fingerprint: `9f7532afe829121610e5f0a27bfba4002adc12d784f5efc92e13b8807d9ffdb6` using the algorithm above.
- Public source/version/manifest guards, runtime relative imports, test fixture closure and local Markdown links pass. Public workflow hooks and source guards remain unchanged.
- The [bounded real HTTP gate](docs/VERIFIED_IDENTITY_HTTP_SMOKE.md) is a separate functional smoke result on its explicitly named feature commit. Its scripted review attestations are not human listening, accuracy, cross-date or long-recording acceptance.
- Verified identity remains opt-in, experimental and uncalibrated. Unknown runtime/model bindings abstain; unsupported verified merge/undo is explicit. No raw evidence, operational reports, private provenance files or unrelated residual research helpers are included.


## Historical 0.2.0rc2.dev3 offline fragment integration

- Immutable active source-only input: `6835ec41c7b2c4b5bcd2da53ed4b96428b91ebe2`. The 29 reviewed matcher/runtime imports, integration API documentation and test setup retain exact source bytes. Original private feature histories and reports are excluded.
- Entire curated public suite: **1,122 passed, 43 skipped, 2 strict xfailed**, with one existing Starlette/HTTPX deprecation warning. The first isolated public-tree run took 77.79 seconds. These are public-source results, distinct from the broader private suite.
- All 102 injected-provider runtime tests passed. The 43 skips are exactly 42 explicit-opt-in real PCM matrix arms and one optional local MOSS-TD asset case. A skip is not a real-data pass.
- The two strict expected failures are only rejected-q1 `development-quiet-tone-jitter` and `independent_stress-strong-tone-jitter`. Their assertions and frozen fixtures are unchanged; an unexpected q1 pass fails the suite. p1 has no expected-failure marker.
- Existing public dependency-pin and temporary-storage test adaptations are preserved. No private residual-protocol or operational-audit inputs are imported.
- Python 3.12 uses existing dependencies, one CPU and one numerical-library thread, with explicit isolated temporary storage. No download, real model inference, production worker/API integration or model-quality acceptance was performed.
- Source fingerprint: `6b6a95f2a16f6ef4d8f608a2b29ce9c1f88dfcc2d89e631cc1c29e6bded7b02b` using the policy-ordered algorithm above. Public versions, path allowlist, manifest, private exclusions, source caps and unchanged hook/scanner pass; local document links resolve.
- The source budget grows only from 200 to 224 files to accommodate reviewed paths. The 2,000,000-byte total and 250,000-byte per-file limits remain unchanged. The release remains unmade and the fragment modules remain import-only/default OFF.


## Historical 0.2.0rc2.dev4 offline extensions

- Immutable source input: `732f37698b58f00750d76ba99e4cf7599c1b97f7`. All 30 reviewed component files retain their exact source bytes and modes. Original private feature graphs, PCM/cache assets, models, vectors and raw reports are excluded.
- The complete exported suite exercised **all 71 public test modules sequentially: 1,222 passed, 51 explicitly skipped, 2 strict rejected-q1 expected failures, no failures/errors**. This was module-by-module coverage in fresh pytest processes, not a single-process invocation. The first public-tree run took 175.96 seconds total. The existing Starlette/HTTPX deprecation appeared 19 times across separate processes, with no new warning category.
- New component coverage: bridge 41 passed / one private-packager skip; standalone normalizer 20 passed; positive-event harness 15 passed / seven private-development skips; native boundary probe 15 passed. The public projection also ran nine provenance/privacy checks, including a separate admission/exclusion case against the real public gate.
- The 51 skips are exactly 42 opt-in real PCM matrix arms, one optional local MOSS-TD asset case, seven private development PCM/cache-dependent event cases, and one private source/evidence-packager case. No test module is broadly skipped; reviewed component assertions are unchanged.
- Seven named cases in `tests/test_positive_event_seams.py` require both explicit `FRAGMENT_POSITIVE_DEVELOPMENT_OPT_IN=1` and the excluded local development bindings:
  - `test_actual_development_selection_and_matcher_transport`
  - `test_full_event_only_bridge_model_free`
  - `test_model_load_failure_persisted_and_same_receipt_cannot_retry`
  - `test_compute_failure_keeps_durable_partial_calls`
  - `test_terminal_reporting_failure_preserves_primary_and_ceiling`
  - `test_reservation_persistence_failure_prevents_model_call`
  - `test_compute_primary_survives_bridge_evidence_write_failure`
- The one excluded-packager case is `tests/test_fragment_model_bridges.py::test_private_real_output_admission_and_packaging_exclusion`. Public output admission and private-path exclusion are independently covered by `test_private_output_admission_and_public_gate_exclusion`; no private packager or substitute corpus is shipped.
- The two strict xfails remain only the historical rejected-q1 quiet-tone-jitter and strong-tone-jitter controls listed above. Unexpected passes fail. p1 assertions remain ordinary tests.
- Existing dependencies and the pinned local FFmpeg build were used with synthetic PCM, CPU 6, one numerical-library thread and isolated temporary roots. No neural inference, model download or external API call was performed. Source fingerprint: `4fb09f7f61b21173838a9f0b82bfc23fec51e167e9f238203027aeaf5d8050c8`, using the policy-ordered algorithm above.
- This result is distinct from the wider staging run of 1,416 passed / 56 skipped / two strict xfails at `2011dc082e6cf4f6daa9bf81018ff91c59f9c89b`, and from the 71 version/docs-affected checks later run at dev4. Neither is substituted for the public result.
- The [extension scope](docs/FRAGMENT_EXTENSIONS_INTERNAL.md) preserves the empty-event base result, selected synthetic-development positive-event limits, original native P2 report-size failure and separately admitted remainder. Normalization remains standalone/default OFF. No HTTP or end-to-end product acceptance is claimed.


## Current 0.2.0rc2.dev5 normalized runtime and standalone codec source

- Immutable source: `867a215a0578bb23885d175623a36ee3ed04d238`. All 12 reviewed updated paths retain exact bytes/modes; the layered map resolves 39 effective component files. The old flat map is unchanged and its hash is pinned. Missing/duplicate/stale overrides, wrong versions, missing files and unreviewed bytes are rejected by executable lineage checks.
- Complete public coverage across all 74 modules in separate sequential pytest processes: **1,285 passed, 75 explicitly skipped, two strict historical rejected-q1 xfails, zero failures/errors**. The first complete public-tree run took 243.02 seconds. This is module-by-module coverage, not a single-process run. The existing Starlette/HTTPX deprecation repeated 19 times across processes; no new warning category was observed.
- The new normalized-runtime module passed 32 tests. Decoded-source structural/control tests passed 21 cases while eight frozen-codec cases skipped. All 16 rolling-reader cases require omitted frozen codec fixtures and explicitly skipped through their original fixture; this public run makes no rolling-decode integration-pass claim. Public layered provenance/privacy coverage passed 19 cases.
- The 75 skips comprise the prior 51 precisely scoped cases documented above, plus eight decoded-source and 16 rolling-reader frozen-fixture cases. The codec tests already require `AUDIO_COMPRESSED_FIXTURES`; that variable was unset for public validation. No private fixture, substitute corpus, module-level skip or weakened assertion was added.
- Existing public conftest, strict q1 controls and named private-input gates are unchanged. Unexpected q1 passes still fail; p1 remains ordinary assertions. The private staging 1,479-pass / 80-skip / two-xfail run and its separate 45-pass frozen-codec opt-in run are different evidence and are not substituted for this result.
- Test supervision and ordinary descendants used CPU 8. The two decoded-source pipe/cancellation tests explicitly construct the reviewed default `Limits(cpu=7)`; their tiny Python children ran during a separately reserved CPU 7 window (the complete decoded-source module took 0.73 seconds on the first run). Component and test bytes were not changed or monkeypatched to redirect affinity. No codec fixtures, neural inference, downloads or external API calls were used.
- Existing Python 3.12 dependencies and pinned installed FFmpeg were used for synthetic PCM. The owned process-tree RSS cap was 256 MiB and new temporary-disk cap 128 MiB, with host reserves of 2 GiB available RAM and 5.5 GiB free disk. Successful module temporary files were removed. Source fingerprint: `136b8f1351c67f4f0118fdc673801e92209c23b75afd237fb234f0b9163f8df8`, using the policy-ordered algorithm above.
- Source/privacy budgets remain unchanged at 250 files / 2,500,000 bytes and 250,000 bytes per file. Guard implementation, hook, exclusions and exact-path review are unchanged.
- The [current extension scope](docs/FRAGMENT_EXTENSIONS_INTERNAL.md) makes the distinction explicit: finalized 44.1/48 kHz PCM16 now has an opt-in internal normalized adapter; compressed receipts/rolling reads remain standalone, provisional until verified within the same session, and outside SourcePlan/Alias/runtime admission. AAC endpoints remain unresolved. No HTTP, production, arbitrary seeking, 48-hour or throughput acceptance is claimed.
