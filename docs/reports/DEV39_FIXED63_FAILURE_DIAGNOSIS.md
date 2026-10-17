# dev39 fixed 63-second trial: verified partial replacement, full objective unmet

Date: 2026-10-17. Published version: `0.2.0rc3.dev39`, commit `1d0eb2164f665d9e8b7be1d957fd8a1f3174daf3`.

## Scope and overall result

The unchanged exposed input comprised 31.5 seconds of previously used AISHELL-4 speech followed by 31.5 seconds of declared digital zeros, submitted as two uploads. The same experimental local model/profile and fixed resource limits were used. This was a same-epoch functional trial; a speech-to-zero crossing would establish provenance, not conversational cross-file accuracy.

The actual run exited **1** after **80.303 seconds**. Primary processing completed all six 10.5-second chunks, and admitted automatic work drained. Two v2 intents remained in terminal history: one `committed` with `result_committed`, and one `no_result` with `unresolved_commit_horizon`.

The complete intended objective was not demonstrated: there were **zero positive-third-root witnesses and zero cross-upload witnesses**. Separately, actual partial-parent replacement, readable authenticated v3 proof, exact retained-text partitions and models-disabled restart were verified. No automatic rerun occurred.

## Authenticated proof and completed automatic result

The committed proof request returned HTTP 200. Its 28,443-byte proof retained `native-owned-bounded-inventory-v3` with scope policy `selected-support-whole-parent-v1`. The inventory measured 4,797 bytes and contained two whole primary parents, two source descriptors and seven lexical units. Proof plus the separately retained original target measured 38,995 bytes.

At 16 kHz, selected support and the inventory scan were exactly frames `[121136,172976)`. The actual query covered `[121136,336000)`, and the original PCM/query lineage checks passed. All three declared native commitments remained represented, but their positive contributions were 46,864, 4,976 and **0** frames. The entire selected support stayed within the first upload. Three declared commitments therefore did not satisfy the required positive third-root contribution or cross-upload criterion.

One ordinary automatic MOSS invocation produced a validated 4,166-byte structured result containing three diagnostic phrases across two job-local labels. Retained metadata recorded exactly one construction, one session and one run attempt, matching the qualified local model/runtime/profile pins, with model/session closure and process cleanup acknowledged.

The raw-content endpoint returned HTTP 409, `automatic_history_only`. Its 96-byte native-raw count and hash were retained as metadata; the raw content itself was unavailable for independent reading. Structured result validation must not be described as independent raw-stream validation.

The result had zero schema errors and zero timestamp-adjusted phrases. Its timestamp policy was `support-end-intersection-10ms-v1`, while timestamp quality remained `model_generated_unverified`. `aborted` and `truncated` were false; `eos` and `generated_token_count` were null. `complete_content`, `speaker_identity_valid` and `profile_update_allowed` were false. These flags do not establish completion, acoustic accuracy, accurate word timing or global speaker identity.

## Actual partial-parent replacement and persistence

One four-unit primary parent was partially replaced: three units were retained and one was replaced. The kept/replaced pieces exactly conserved the original UTF-8 raw and display text. One further whole primary parent was replaced, leaving 12 whole-primary reading items. All three MOSS phrases were retained unchanged, and default transcript output agreed with the explicit legacy view.

The durable primary census contained 14 rows and matching content hashes, 107 retained units and no missing inventories. Retained inventory payloads totaled 37,254 bytes.

A fresh-port, models-disabled restart reproduced all six transcript pages, 20 evidence objects, the structured result body, history metadata, projection checks and durable primary/source rows exactly. Both servers completed clean, positively waited shutdowns with no surviving owned process or forced kill.

## Remaining certificate-capacity limitation

Saved notices for chunks ending at 31.5 and 42 seconds reported `bounded_certificate_capacity` with owned targets preserved. This differs from the earlier trial's `serialization_capacity` notices. The later terminal `unresolved_commit_horizon` reason is a separate outcome; it does not establish that no overlap existed.

Neither notice exposed a capacity subphase or encoded-size/limit fields. Multiple guard sites share this reason. The exact historical throwing guard, combined object membership and failing size remain unknown. The measured successful proof and target do not identify the separate failed certificate. No additional database diagnostic or inference replay was used for this report.

## Bounds and qualification

The fixed envelope remained 900 seconds of work plus 120 seconds of cleanup, CPU 0-1 at nice 19, 6 GiB aggregate memory including 768 MiB unknown and 512 MiB secondary reserves, 64 MiB total output and 8 MiB evidence. Peak sampled process-tree RSS was 1,859,166,208 bytes, or 3,201,343,488 bytes including reserves. Peak trial output was 12,038,144 bytes and evidence 1,298,432 bytes. No resource failure occurred.

This trial establishes the partial-replacement, proof-read and persistence gains above while preserving the failed full-path gate. It does not qualify a positive third-root/cross-upload path, long-run retention, global identity or recognition accuracy. Local artifact/profile pins establish repeatability; upstream conversion provenance remains unverified. No later fix or further actual-run success is claimed.
