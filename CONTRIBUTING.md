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
python scripts/prepare_public_sync.py --source-dir ../private-source --source-ref develop --output-dir ../public-sync-review
```

3. Prefer a reviewed commit from the local primary source repository. `--source-ref` records its exact commit and reads immutable Git objects; later working-file edits cannot enter the proposal. Omitting the option uses a twice-checked live snapshot. Missing allowlisted source paths are reported for review and never deleted automatically.

The command reads only the exact `sync_allowlist` in `PUBLIC_POLICY.json`, rejects symlinks and unsafe content, checks for concurrent changes, and writes a proposal. It never edits the live source, this repository, branches or remote state. Newly added source files are excluded until reviewed and explicitly allowlisted.
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


## Recover local history

Create a bundle only after the worktree/staged/history gates pass:

```sh
git bundle create ../public-source-history.bundle --all
git bundle verify ../public-source-history.bundle
```

A bundle preserves source commits and refs, not hooks, local configuration or ignored data. Restore into a fresh directory, never over a running source tree; re-enable the local hook after reviewing it. Keep a separate metadata-only primary-source workflow and its restore policy with the private recovery bundle. Never treat a source bundle as an audio/database backup.
