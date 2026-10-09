# Public-source validation scope

This is a newly curated source-only test suite, not the full private development suite. Historical recovery validation and model measurements do not automatically apply to this exported tree.

The public suite covers runtime/API behavior, storage, queues, boundaries, identity guards, model setup logic and fake-worker subprocesses using synthetic inputs. Real media/clinical corpora, full gold annotations, operational endurance records, private packaging tests, and benchmark replay tests that depend on omitted assets are excluded. One optional MOSS-TD local-asset test is expected to skip without separately prepared validation assets.

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


## Current 0.2.0rc2.dev1 public integration

- Both public development version fields now match the exact reviewed primary source version, `0.2.0rc2.dev1`; historical `main` stays at `0.2.0rc1`.
- Entire curated suite after the version update: 676 passed, 1 optional-asset skip, one dependency deprecation warning in 29.51 seconds.
- Python source fingerprint: `d68b3045f240c8a41403a73eb255d7b81eb0cdaf414045825a5f9c96e67e34a0` using the algorithm above.
- Source comparison verifies 129 byte-identical paths and 12 intentional public-edition adaptations.
- No inference study/model promotion, raw reports, source recordings, voice vectors or corpus-person identifiers were imported. No tiny real integration result is claimed by this source snapshot.
