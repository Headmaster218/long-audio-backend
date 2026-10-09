# Change log

## 0.2.0rc1 public source baseline

- Import reviewed project-owned source from the verified historical snapshot with accurate present-day Git authorship.
- Keep runtime behavior and model/dependency pins; exclude private evidence, media, weights, operational state and real-data fixtures.
- Add public setup/API documentation, third-party notices, source-only test scope and manual version/branch guidance.
- Make fake-worker test temporary directories independent of omitted validation assets; replace a private operational-audit dependency with public upstream pin metadata.
- Add exact allowlist/hash checks and an offline, proposal-only sync tool. No releases, tags, GitHub Actions or automatic model downloads.
