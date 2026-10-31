# Restore the portable event diagnostic source checkpoint

Frozen source: `234c73e37c27f67027ea11753140883c3ccbb7e1`.
Three source files are byte-identical to that Git snapshot. Run
`python verify_source.py`, then copy `source/` into an empty directory.
Linux/Python 3.11+ is required. Do not run with Python optimization (`-O`/`-OO`).
No audio, annotations, model, label CSV, results or private run specification is
included. This is not a standalone inference installation or accuracy report.

## Exact external backend dependency

The model-free contract tests require four existing backend modules listed with
SHA256 and Git blob hashes in SOURCE_MANIFEST.json. They are already public at
repository `Headmaster218/long-audio-backend`, fixed commit
`f5105d48bb588a8f25c1e0b2e5ac646edc4dee9e`, under
`checkpoints/backend-capacity-source-20261031/source/`.
Copy those four paths, preserving their relative package layout, into a separate
empty backend directory and verify every listed hash. Alternatively restore the
entire backend checkpoint using its existing verification instructions. This
checkpoint does not duplicate or update backend source.

From the restored harness directory, run:

```sh
python -I -B test_protocol.py --repo-root /path/to/restored/backend
python -I -B run_once.py --help
```

The two synthetic contract tests load no model and need no inference dependency
installation. They cover positive-only metric semantics and category/threshold
mapping, not runtime inference or resource supervision. Omitting `--execute`
with valid backend and empty external run directories must exit with code 2 and
leave that run directory empty. Never use the internal `--child` mode directly:
it is an implementation detail, not an independently guarded execution entry.

Actual inference requires a complete compatible backend/runtime installation
including soundfile, psutil and model inference dependencies, plus independently
licensed/prepared assets and a pinned external freeze.json as the source README
specifies. No downloads/installation or inference are part of restoration.
The portable revision itself has only model-free validation; no historical
inference result is represented as a run of this newer source revision.
Native process/RSS sampling and receipt wording are not independent proof of
allocator release, absolute memory bounds or absence of all descendants.
