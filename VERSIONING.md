# Versions and branches

- `main`: reviewed source-only baseline, initially `0.2.0rc1`. This inherited version string does not imply release readiness or quality acceptance.
- `develop`: current reviewed integration, `0.2.0rc2.dev5`.
- `feature/<subject>`: reviewed, default-off additions; the dev5 work is isolated on `feature/normalized-runtime-codec-dev5`.
- `fix/<subject>`: focused corrections; merge only after the scoped tests and public-tree gate pass.
- `research/<subject>`: isolated diagnostic work. No automatic promotion into live identity enrollment or template updates.

Use PEP 440 versions in both `pyproject.toml` and `audio_backend/__init__.py`; the API reads the latter. Increment `.devN` for distinct unreleased integration milestones, or advance the prerelease series when its scope changes. Update the change log with the reason, tests and limitations.

Commits use accurate present-day authorship and describe one coherent change. The first commit imports a known historical source snapshot now; it is not reconstructed historical development. `BASELINE_IMPORT.json` records the exact source allowlist and hashes. Later publication-specific adaptations are separate commits.

No tags or releases are created yet. A release requires an explicit decision, documented acceptance results for that exact commit, privacy and dependency-license review, and reproducible packaging. Unit-test success alone is insufficient.

Keep normal fast-forward ancestry. If an empty remote requires an initial bootstrap commit, replay these reviewed changes on its verified parent before publication; never force-push over an unrelated remote history.
