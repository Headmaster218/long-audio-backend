# Durable-cell jobs: evolving source-only checkpoint

UNQUALIFIED. This is a partial, non-standalone copy of corrected source, not a tested release.

The accepted base is rc3.dev6 commit 9e0a135bda2c37641fb0a641655835a0c6a92feb. The full source adds three bounded cell routes to the existing explicit MOSS jobs owner; it adds no scheduler or native inference path.

The first full 458-case model-free run on the earlier source failed: 429 passed, 6 failed and 23 errored. Its retained evidence also exceeded the selected 64 MiB run-output cap. Those results and artifacts remain preserved.

This copy contains a corrected validation response and focused fixture corrections. The corrected source has not been executed. Its later full model-free rerun, exact-runtime adapter check, independent review and publication closure remain pending. The maximum-material fixture observes only postclaim Python allocation transients; it does not measure acquisition allocations or hard RSS, and the cause of the earlier acquisition failure is unproven.

No model, acoustic, genuine larger-core, long-recording, actual in-flight native, power-loss, hard-RSS or broader-platform qualification is claimed.
