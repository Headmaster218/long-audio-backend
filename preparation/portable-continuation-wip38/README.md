# Portable recovery continuation and runtime recipes

This is a source checkpoint, not a completed recovery or a runtime qualification. It builds on the portable recovery checkpoint and the published dev8 audio backend.

## Download continuation

The bootstrap packet contains the bounded original-process guard, downloader, generated configuration schema, exact public asset manifest, and model-free fixtures. It restores the exact Hugging Face CDN hostname `us.aws.cdn.hf.co`, documented by Hugging Face at https://huggingface.co/docs/hub/models-downloading. No wildcard hostname expansion is used.

The first fresh acquisition verified four assets totaling 313,636,022 bytes, then refused before receiving the first Nemotron payload. Its failed result is preserved. A later no-follow HEAD observed the documented CDN redirect; it does not replace the missing headers of the earlier request.

The corrected continuation requires explicit prior evidence and fresh identity/full-hash checks for those exact four assets. It may request only the remaining 34 pinned assets, totaling 4,837,120,469 bytes. Final verification still covers all 38 assets and 5,150,756,491 bytes. Each missing asset gets one attempt in the new explicitly authorized phase; the earlier failed request remains historical. There is no byte-range resume, automatic retry, or redownload of the successful four.

The receipt-writing caller enforces a closed output set, a 32 KiB per-receipt cap and a 1 MiB aggregate cap. This is a disclosed caller adaptation; it is not represented as unchanged legacy caller behavior.

Earlier source revisions passed 24 guard and 27 pacing fixtures after one stale private-helper fixture was repaired. This current snapshot contains 24 guard and 37 pacing cases, including ten continuation/hostname/receipt regressions. A 61-case attempt passed its case bodies, but the outer guard refused a generated absolute symlink during its final census. That failed attempt remains failed. The sole fixture correction uses a bounded relative alias to the same owned target, with an explicit record of the historical link replacement. The unchanged 24 guard cases remain accepted at their original source freeze; at this checkpoint, the corrected 37-case replay and remaining-34 acquisition are unexecuted. Their later results must bind their exact source/configuration; this snapshot alone grants no execution qualification.

## Runtime extension draft

The five files under runtime-extension are a separately reviewed source-only draft. They describe bounded offline wheel/build preparation, complete-tree checks, pinned host dependencies and ordinary-startup import checks. Both CLIs and the fixture entry deliberately refuse unadmitted execution. The 30 prepared fixture methods have not run.

A later explicit guard/configuration integration is still needed. The current installer draft also expects the original one-shot acquisition receipt; it must be adapted to the accepted reused-four/new-34 provenance before use with this continuation. Never relabel reused bytes as newly received network bytes.

The host standard library and required system libraries remain explicit pinned external dependencies. Identical interpreter bytes do not establish a qualified runtime. No installation, model loading, audio inference, or storage qualification is claimed by these files.

## Reproducibility and retained limits

Private generated configurations, execution receipts, identity maps, logs, models and audio are excluded. Use a fresh local configuration and the documented local checks. The earlier environment reset lost local models, runtime, audio, database evidence and unpushed continuation work; public source recovery did not restore those data. Earlier incomplete actual attempts remain incomplete.
