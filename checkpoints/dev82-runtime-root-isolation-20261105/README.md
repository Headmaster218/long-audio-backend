# Optional runtime dependency-root isolation

The verifier, candidate and leaf bootstraps retain the verified product package origin but remove its entire parent site-packages directory from dependency and metadata discovery. Selected optional-runtime roots remain mandatory; provider and hash checks are not relaxed.

Source tests and independent review passed. The actual whole-source verifier passed selected runtime/model hashes, but admission remained blocked by global fd enumeration before a positive terminal observation (PUBLIC_HASH_ONLY_OUTCOME02.json). Fresh installed verification is not claimed. No old task or immutable ledger is adopted, and no recognition-quality improvement is claimed.
