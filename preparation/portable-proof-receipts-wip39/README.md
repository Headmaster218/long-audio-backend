# Portable proof and receipt corrections: source checkpoint

This is a reviewed source-only snapshot. It contains the corrected SQLite proof driver and a draft acquisition-receipt adapter. Neither package has been executed or integrated into an admitted runtime.

## SQLite proof source

The five files in `cell-store-proof/` preserve the existing schema, transaction geometry, runtime verifier and fixed resource limits. They add persistent family/ancestor identity checks, separate preflight refusals from unfinished proof attempts, and publish success only after accounted staging and cleanup. Setup now initializes every owned descriptor inside protected cleanup; secondary close or flush errors preserve the primary failure.

There are 48 prepared helper cases, including setup-acquisition failure injection. None has run. Genuine allocation and process-crash recovery evidence, exact current-runtime checks and independent terminal review are still required. The three 64 MiB families and 1 MiB record allowance remain the proposed proof envelope. No registry entry is changed, and there is no power-loss or universal-platform qualification.

## Runtime receipt adapter draft

The three files in `runtime-receipt/` distinguish four reused assets (313,636,022 bytes) from 34 new assets (4,837,120,469 bytes), requiring a final 38-asset, 5,150,756,491-byte closure. Preserved prior-failure provenance is checked separately from the obsolete four-file pre-download census. The authentic numeric work limit accepts finite integer or floating-point 10740 while rejecting booleans and nonfinite values.

The final owner-terminal binding is deliberately pending and refuses admission. This adapter is not wired into the earlier runtime installer, and does not authorize installation. Its future fixtures remain unexecuted. A later interrupted acquisition cannot be treated as a successful continuation merely because some per-file receipts exist.

## Current recovery limits

The earlier corrected portable downloader has accepted evidence for 24 guard cases and 37 pacing cases, with the two execution freezes explicitly distinguished. The prior fixture and download failures remain historical failures.

During the subsequent asset continuation, the execution tool lost the original process handle. The machine boot identity and files remained intact, but no final guard receipt or download result was available when this snapshot was prepared. Eight assets had verified per-file receipts, totaling 1,414,236,545 bytes. This is partial acquisition evidence; original completion and cleanup remain unresolved. No replacement download, extraction, installation, model loading or inference is qualified by this checkpoint.

All older published source snapshots remain unchanged. Only portable source and this status document are included here; local configurations, origin maps, operational receipts, audio and model payloads are excluded.
