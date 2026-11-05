# Optional runtime dependency-root isolation

The verifier, candidate and leaf bootstraps retain the verified product package origin but remove its entire parent site-packages directory from dependency and metadata discovery. Selected optional-runtime roots remain mandatory; provider and hash checks are not relaxed.

Source tests and independent review passed. Actual whole-source admission and fresh installed verification remain pending. No old task or immutable ledger is adopted, and no recognition-quality improvement is claimed.
