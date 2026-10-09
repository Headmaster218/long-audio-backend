# Contributing and safe source imports

This repository is an audited public source mirror. Private development data and running evaluations live outside it. Never initialize, checkout, clean or overwrite a live evaluation directory while importing source.

## Local checks

Use Python 3.12+ and install the project test/inference dependencies in your own virtual environment. Tests use synthetic fixtures or fake analyzers; an optional local-asset test skips without its separately prepared models.

```sh
python -m pytest -q
python scripts/check_public_tree.py
python scripts/check_public_tree.py --history
```

No GitHub Actions, model download or paid evaluation is run automatically. Some fake-worker process tests require 5 GiB free disk on the project volume. No credentials are needed for these checks.

Optionally enable the shipped local pre-commit gate:

```sh
git config core.hooksPath .githooks
```

The hook checks the staged tree, not just the working copy. It is a guardrail, not a complete secret scanner or an authorization to publish.

## Import a later source snapshot

1. Make a focused branch from the reviewed integration branch. Do not operate Git in the live source tree.
2. Prepare an offline proposal in a new sibling directory:

```sh
python scripts/prepare_public_sync.py --source-dir ../private-source --output-dir ../public-sync-review
```

3. The command reads only the exact `sync_allowlist` in `PUBLIC_POLICY.json`, rejects symlinks and unsafe content, checks for concurrent changes, and writes a proposal. It never edits the live source, this repository, branches or remote state. Newly added source files are excluded until reviewed and explicitly allowlisted.
4. Inspect every proposed diff and hash. Review semantics, privacy, dependency closure and licensing. Copy only the approved changes into this repository. Public-specific docs, version numbers and test adaptations must not be overwritten by a blanket sync.
5. Update versions consistently when appropriate; add tests. Refresh the manifest, inspect the exact staged paths, and commit a coherent change:

```sh
python scripts/check_public_tree.py --write-manifest
git add -- path/to/reviewed-file PUBLIC_SOURCE_MANIFEST.json
python scripts/check_public_tree.py --staged
git diff --cached --stat
git diff --cached
```

6. Run the exported suite and record only factual aggregate results. Review all reachable Git history before external publication. A successful heuristic scan does not prove absence of secrets or personal data.

Never use a private recovery packager as a public Git import tool. Do not add recordings, full transcripts/gold annotations, model weights, voice vectors, individual clinical/voice-condition records, cache/state, databases, recovery manifests, resource dumps, operational machine paths or private service identifiers. Synthetic small vectors in source-code unit tests are invented fixtures, not voice embeddings from people.

Do not choose a project license, make a release/tag, enable external CI, or publish new sensitive data without the owner's decision. Keep third-party notices and pinned dependency metadata.
