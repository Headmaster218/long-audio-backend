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
