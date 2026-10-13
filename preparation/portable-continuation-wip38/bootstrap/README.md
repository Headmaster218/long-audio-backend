# Portable recovery bootstrap: CDN and fixed-four continuation checkpoint

This packet contains source and expected public asset metadata only. It does not contain a restored runtime, model, corpus, download receipt or qualification. Product source is immutable dev8 commit `1fc066910ca78b2e57d7a28f79b91cafc06f5dd5`; public preparation source is immutable commit `30cc5b0ee22a9c6daf203f12421c9114986f927c`. No historical runtime or test receipt is recreated.

## Scope and authorization gates

Writing/reviewing this source does not authorize a command below. Review each frozen command and its fresh configuration before execution. First acquisition is download-only: no extraction, wheel installation, product/provider import, SQLite, service, model construction or inference. There are exactly 38 assets. Each newly authorized acquisition phase permits one attempt per selected missing asset, zero automatic retries and no ranges/resume. Prior attempts remain recorded. Failed bytes and receipts remain charged and preserved; another phase requires a fresh reviewed reservation, source/configuration freeze and explicit execution approval.

All first-checkpoint files must remain byte-exact until every running guard reaches terminal rehash and its original owning execution has been reaped. Do not add the five later recipe files to this frozen directory while work is active. A later versioned checkpoint may contain the full 15-file recipe. Final CellStore allocation/crash recipes are owned by the separate proof packet; their source directory is explicitly charged by this packet and their future outputs must be under the configured work/proof directory.

## Initial source-only configuration

Supply your chosen absolute paths through environment variables. These values are private generated configuration; do not publish them. `HOST_PY` must name the reviewed Python 3.12.14 interpreter. `PRODUCT` is the verified dev8 snapshot inside `SOURCE_RECOVERY`. `WORK` must be a fresh directory. `RUNTIME` selects the future runtime leaf; its workspace-child container must still be absent. `PROOF_SOURCE` may be absent and is charged as source whenever present. It cannot overlap another charged root.

Proposed command, after source/configuration review:

    "$HOST_PY" -I -B -S "$PACKET/bootstrap.py" configure --product "$PRODUCT" --source-recovery "$SOURCE_RECOVERY" --work "$WORK" --runtime "$RUNTIME" --proof-source "$PROOF_SOURCE"

It verifies the packet, host interpreter bytes, all 351 product files and fresh admission floors, then creates only WORK/evidence/config.json (schema v2, explicit prior_acquisition=null) and the evidence/run directories. It does not create the runtime or acquire assets. Retain the emitted config SHA256 as CONFIG_SHA; CONFIG is WORK/evidence/config.json. Unknown config fields, booleans used as integer limits, path aliases, overlapping roots, mismatched boot/PID namespace, changed interpreter and changed product bytes fail closed.

## Model-free qualification, after exact command approval

    "$HOST_PY" -I -B -S "$PACKET/guard_driver.py" --config "$CONFIG" --config-sha256 "$CONFIG_SHA" --phase guard-fixtures
    "$HOST_PY" -I -B -S "$PACKET/guard_driver.py" --config "$CONFIG" --config-sha256 "$CONFIG_SHA" --phase pacing-fixtures

These proposed commands have not been executed by source preparation. They use stdlib unittest, synthetic bytes and fake HTTP responses. Guard fixtures additionally launch bounded real success/nonzero Python descendants to test original-handle wait/reap and pidfd cleanup. Linux libc/prctl and /proc are the guard's operating-system mechanisms; these are not ML/provider imports. There is no model, network, SQLite or package dependency required. Each outer fixture phase is 300 seconds including 60 seconds cleanup.

The pacing suite contains the existing 27 cases derived from WIP8 plus 10 CDN/privacy/reuse/receipt-budget cases, for 37 cases and 61 combined with the unchanged 24 guard cases. Cases 14, 19, 20, 21 and 27 replace historical receipt/path dependencies with, respectively, exact upstream function AST pins, the real bootstrap metadata-write boundary and safe failure reporting, unchanged guard ownership AST pins, the tiny exact old defective tick method as a negative fixture, and the fresh full acquisition reservation. The other 22 test bodies preserve their original behavior. Test 19 verifies an exact-length metadata write, refusal at a cap one byte smaller before destination creation, and message-free UnclassifiedException reporting for the actual bootstrap Refused exception and a sentinel-bearing bootstrap Refused exception; it does not claim the historical MetadataLimit integration. The receipt-routing and atomic-promotion-census regression tests are in the 24-case guard suite. A finite three-pass complete rescan handles only a disappearing assets/tmp/<64-hex>.part; it never discounts a missing promoted asset. Other missing paths fail except the original narrow volatile guard scratch case.

## Fixture repair history

The first 24-case guard run passed. The first pacing run reached all 27 cases with 26 successes and one error in the former test_19_metadata_limit_exact_type_is_coded: it still referenced an absent historical prepare.py helper. The raw traceback was not retained; the missing helper is a separate static diagnosis. The failed owner terminal and all run files remain unchanged. The three original packet files, original configuration and eight spent phase claim/spec files are retained through identity-preserving relocation with an explicit bounded origins map. The already published WIP37 checkpoint is unchanged.

The fixture-only revision replaced and renamed test 19. Its fresh 24+27 replay subsequently passed; that owner terminal and its runs remain unchanged. This later CDN/continuation revision preserves those 27 pacing test bodies and the entire guard suite, adds exactly 10 pacing cases, and requires a new full 24+37 replay. These corrected 61 fixtures remain unexecuted by source preparation. Prior successes are not substituted for this replay.

## Download-only acquisition, after fixture results and fresh review

    "$HOST_PY" -I -B -S "$PACKET/guard_driver.py" --config "$CONFIG" --config-sha256 "$CONFIG_SHA" --phase download

The original launcher monotonic start, absolute endpoint, boot ID, PID namespace and (PID,start_ticks) identity are pinned across a same-PID exec into the guard. Admission and source verification consume the same 10,800-second total; the clock cannot restart. Work ends 60 seconds before that absolute endpoint. CPU affinity is exactly CPU 3, nice is 19, sampled owned RSS is 256 MiB, RLIMIT_AS is 1 GiB, individual network reads are at most 30 seconds, and transfer pacing is 4 MiB/s based on bytes actually received/written with at most a 256 KiB burst. Platform proxy/trust values are forwarded only from the approved allowlist and their values are never recorded. Every redirect must remain on an approved HTTPS host.

The transfer, actual-byte pacing, timeout, redirect, hash and write_json functions retain their exact WIP8 ASTs. The HOSTS constant adds only us.aws.cdn.hf.co. failure_summary deliberately adds three exact-literal URL refusal codes (url_length, url_policy, url_credentials_fragment); its AST is therefore excluded from the preserved-node claim. Exact exception-type checks remain; arbitrary messages, signed paths/queries/userinfo and chained exception text are never retained in diagnostics. Context owns only WORK; asset destinations have a fixed assets/ prefix; temporary payloads are WORK/assets/tmp; bounded receipts are inside the original guard evidence run. Files are exact-size/hash verified, re-read before atomic promotion, and re-read again before the download-only result. A successful child result is insufficient: require outer PASS, original_handle_waited=true, cleanup=CONFIRMED, no unresolved descendants, final_source_pin_verified=true, and the owner's original execution reap/exit evidence. Never fabricate the latter inside this packet.

## Explicit continuation and published caller amendments

The first 38-asset attempt failed before Nemotron payload after verifying four assets totaling 313,636,022 B. That failed attempt remains failed. A later, separately authorized no-follow HEAD observed HTTP 302 to us.aws.cdn.hf.co with zero body; it is not evidence of the original GET response headers. The official Hugging Face downloading documentation identifies this CDN. The only added hostname is also asserted by public qwen17-wip7/test_acquisition.py; WIP8 and MOSS WIP11 loaders call the corresponding historical host extension. The recovered WIP1-to-WIP2 changes already included bounded redirect closing, per-connect timeout and read timeout; WIP7 added safe failures/actual-byte accounting; WIP8 fixed clock pacing. The old remaining-native helper itself is absent from the recovered public snapshot, so no private MetadataLimit implementation is reconstructed.

A separate, explicit caller-layer restoration wraps unchanged write_json: only the selected asset receipt names and the run download-result.json are writable, each serialized JSON is at most 32 KiB, and total receipt serialization is at most 1 MiB. Budget/path/duplicate refusals precede writer invocation. This adapts the published bounded-metadata caller pattern, not the WIP8 transfer primitive or a claim of exact historical MetadataLimit integration.

Fresh configuration always sets prior_acquisition=null and requires an absent asset root. The sole continuation mode sets prior_acquisition to the exact {path, bytes, sha256} pin of WORK/evidence/CONTINUATION_INPUTS.json, at most 32 KiB. There is no skip-if-exists or arbitrary historical resolver. The closed input schema is portable-fixed-four-reuse-v1: assets_sha256; prior_owner, prior_guard, prior_result, prior_spec, prior_claim, head_diagnostic references; exactly five ordered prior_download_receipts; exactly two origins_maps; exactly four ordered reused records; preserved_part; remaining_ids, remaining_bytes, full_count and full_bytes. References are workspace-relative {path, bytes, sha256}, with id added only to download receipts. Reused records are {id, destination, dev, ino, mode, mtime_ns, bytes, sha256}; the empty part has original_path/preserved_path plus those six identity/hash fields. All four public destinations/hashes and the remaining 34 IDs are fixed by assets.lock.json. Existing metadata references are individually capped at 256 KiB; each origins map is capped at 32 KiB.

Before any request, continuation verifies the accepted failed owner, original reap/exit, fail-closed guard with confirmed cleanup and exact caps, raw/embedded result agreement, spent phase claim/spec, five download receipts, later HEAD scope, prior metadata pins, four retained asset identities and full hashes, and the identity-preserved empty part. Existing asset files must be exactly those four plus that empty part. Every missing destination and temporary part must be absent. Only then are four rows seeded and the remaining 34 selected. Their new request bytes are 4,837,120,469; the previous Nemotron request remains explicit, so the policy is one attempt per missing asset in this new phase, not one lifetime attempt.

Six packet originals, the original configuration and ten spent phase claim/spec files are identity-preserved in finite history locations. The zero-byte failed part is retained under assets/history-cdn-continuation and charged as acquisition. The new old-only origins map contains exactly these 18 relocations and immutable earlier evidence, never new config/manifest/input hashes. The earlier 12-original fixture-repair map remains unchanged. New config binds inputs, inputs bind maps, and maps bind only older bytes, avoiding hash cycles. All previous owner terminals/runs remain intact. A continuation config changes only the schema version, packet hash and explicit prior binding; roots, limits, assets and host remain identical.

The final child reconciliation rehashes all 38 assets and explicitly distinguishes reused 4 / 313,636,022 B, newly received 34 / 4,837,120,469 B, and final verified 38 / 5,150,756,491 B. Prior pinned identities are checked again before the result. Partial failures report observed new/reused counters without claiming a full-38 reconciliation. New source/config/spec freeze, all 61 model-free fixtures, fresh floors and the explicit continuation dispatch review are required before requests.

## Prospective disk and memory accounting

Exact network bytes: 5,150,756,491 across 38 assets:
- 25 native runtime wheels: 37,460,749 B
- 3 build-only wheels: 981,492 B
- transcribe-cpp 0.2.3 wrapper/native wheels: 24,364,652 B
- SenseVoice/Nemotron/ERes2Net/AudioSet/native release/license: 427,331,627 B
- MOSS Q8: 986,899,616 B
- Official Eval_Ali corpus archive: 3,673,718,355 B

No Qwen weight is selected. The 2,619 B OpenMDW license is a local immutable-product copy, not a network asset; it is not copied by first dispatch.

Network bytes plus future reserved expansion/copies are 6,504,423,186 B. This includes 300 MiB AudioSet, 350 MiB SenseVoice, 256 MiB native archive extraction, 402,982,176 B selected corpus members, the future 672,044 B canonical WAV and 2,619 B local license. These are declared ceilings plus known byte counts, not measured extraction. This fits the acquisition cap with 1,011,769,582 B headroom for filesystem allocation/metadata. Archives are retained and charged; wheel expansions are charged separately in future runtime/proof categories.

Category ceilings are acquisition 7 GiB, runtime 512 MiB, combined model-free/proof 512 MiB, actual 544 MiB, source/recipes 64 MiB, evidence 64 MiB. Their sum is 9,294,577,664 B, leaving 3,590,324,224 B unassigned inside the 12 GiB global cap. Charges use max(logical bytes, st_blocks*512), include all selected source trees, packet, optional proof-source tree, work outputs, and the entire runtime container including ancestor directories. Required free space is 5.5 GiB plus the unconsumed part of the 12 GiB global reservation. MemAvailable must remain at least 2.75 GiB. This is a sampled watchdog, not a hard aggregate disk/RSS sandbox; sampling gaps are reported honestly.

The new input is channel 0, frames [23400320,23736320), 336,000 frames at 16 kHz. Its exact WAV hash must be freshly derived after source WAV verification; the old 19.19-second clip hash must not be reused. Corpus URL/size are official metadata; the archive's expected SHA256 is an immutable public pin, not a claim of a fresh full download verification.

## Remaining five-file runtime recipe plan

The full accepted packet additionally reserves install.py (40 KiB), probe_runtime.py (32 KiB), test_install.py (64 KiB), runtime.lock.json (128 KiB) and source-map.json (32 KiB). They are not present in this first checkpoint and there is no runnable install/import command yet.

The later lock is CPython 3.12.14/Linux x86_64, using the 25 WIP1 runtime wheel pins plus transcribe-cpp==0.2.3 and transcribe-cpp-native==0.2.3. The build-only closure is packaging==26.3, setuptools==84.0.0 and wheel==0.48.0. No host pip, unpinned dependency resolver or arbitrary build requirement is permitted. The later installer will audit bounded wheel ZIP layout and RECORD/member hashes offline, reject path traversal, unreviewed .data schemes, duplicate members, symlinks, unexpected startup hooks and unexpected distributions, stage only pinned wheels, and build the dev8 backend from an exact fresh source copy using those build-only tools. Final runtime source files must equal immutable dev8 package bytes; build tools will remain outside the final runtime.

Fresh seals will bind the host executable, required stdlib and dynamic-library observations, installed distribution/entrypoint closure and normal-startup/import origins. Native import-only qualification is a separate approved gate; it must deny models/audio/constructors/network/service actions and report any native C ABI calls accurately. Imports cannot prove inference readiness or complete lazy-dlopen closure. SQLite/CellStore qualification is separately owned. No claim is made that the minimal runtime includes pytest/httpx or supports the entire product development test suite; model-free packet fixtures are stdlib-only.
