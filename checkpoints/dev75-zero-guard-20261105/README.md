# Prospective exact-zero guard

The installed dev75 CLI skipped model dispatch for the existing six-second exact-zero PCM input with a deliberately nonexistent model path. It emitted a no-call receipt and preserved the original failed candidate and primary export. This is a byte-exact input guard, not a VAD or a solution to hallucination on nonzero environmental noise. It does not overwrite primary text or change the failed dev74 evaluation.

Independent focused tests34passed; related regression96passed; fresh qualification9gates+41controls; installed synthetic checks5passed. No new inference or service run.
