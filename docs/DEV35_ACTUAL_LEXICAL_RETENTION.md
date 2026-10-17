# Dev35 actual lexical-inventory retention check

Date: 2026-10-17 UTC. Target: unchanged installed `0.2.0rc3.dev35`, with lexical retention explicitly enabled for this check. The shipped default remains off. This is a persistence/compatibility check; it does not enable granular transcript selection or lexical evidence GET resolution.

## Result

One ordinary upload produced two primary segments and retained all 19 post-adapter CTC units. The canonical inventories totalled 4,864 bytes, exactly matching the session's admitted-byte counter. A separate worker-off, models-disabled restart preserved the public evidence bytes and the private inventory/evidence bytes exactly.

The two invocations have distinct outcomes:

- Original actual invocation: exit 1 after 6.111 seconds. The single final core completed, primary evidence GETs succeeded, and the server shut down cleanly. The helper then failed on a post-shutdown bare-bind `EADDRINUSE` assertion, before its stopped-database checks or restart. This is consistent with TIME_WAIT, but that cause was not independently proven. The failed helper and receipts remain unchanged.
- Saved-result continuation: exit 0 after 1.460 seconds. It validated the stopped database, restarted the ordinary API with its worker and models disabled on a fresh ephemeral port, performed GETs, shut down, and validated the stopped database again. It made no upload or inference retry.

Both servers were positively waited after owned SIGTERM, returned -15 with complete clean lifespan markers, and left no observed owned live descendants. No forced cleanup was needed. Port rebinding was not used as the continuation's cleanup criterion.

## Fixed input and ordinary pipeline

The input was fixed before recognition: source interval [163.5, 170.0), exactly 104,000 mono 16 kHz PCM16 frames, written as a standard WAV without resampling, padding or normalization. The upload used timeline start 163.5 and one final 6.5-second core. Source and prepared WAV/PCM hashes were recorded before recognition.

The ordinary SenseVoice/Nemotron pipeline retained its standard AudioSet tagging and ERes2Net embedding stages. Separate MOSS, FrameMN, support/cell/seam/recording-link engines were disabled; no gallery requests were made. Product and installed package files were unchanged, with no runtime interception or monkeypatching. Observed pipeline metadata is not an exact internal native-model call-count attestation.

## Independently checked

- Complete raw/display contribution reconstruction and exact private spans for all 19 units, including canonical serialization and inventory hashes.
- Existing primary-field content hashes and `p.*` evidence bindings, unchanged by inventory retention and restart.
- Current declared source-row bindings, the one extraction part, frame range and coordinate mapping, plus exact existing commit arithmetic. The analyzer and core origins were zero; the upload timeline origin was nonzero.
- All inventories remained current and complete; their canonical bytes matched the monotonic session charge before and after restart. Legacy API results did not expose the storage-only inventory.
- Bounded read-only stopped-database snapshots completed in 0.00795 and 0.00639 seconds; their deadline and SQLite progress checks passed. Progress-callback quanta are not an exact instruction count.

Evidence byte equality does not mean whole-database byte equality: the database grew from 884,736 to 888,832 bytes during ordinary restart.

## Bounds and qualification

The actual invocation used a 600-second envelope (480 work / 120 cleanup); the continuation used 120 seconds (60 / 60). Both used CPU 0-1 and nice 19. The model thread setting and native wrapper CPU limit were 2, with enforced affinity. Existing native provenance declares a requested-thread value of 4, but the Python C-ABI constructor exposes no thread-count argument; the effective internal native thread count was not observed. Sampled owned-process-tree RSS peaked at 639,324,160 bytes for the actual invocation and 95,166,464 bytes for the continuation. With the reserved 768 MiB unknown and 512 MiB secondary allowances, both fit the 6 GiB aggregate. The maximum sampled complete fresh output was 1,658,880 bytes; maximum sampled evidence was 479,232 bytes, below the combined 64 MiB / 8 MiB limits. No resource guard violation occurred. These are sampled measurements, not unsampled peak guarantees or performance benchmarks.

CTC emission start anchors were retained after the existing adapter's context shifts, millisecond rounding and clamping. Ends remain estimates. This does not certify word ends, word boundaries or frame-exact emission times, and declared source snapshots do not become fresh PCM attestations.

The interval came from the same recording used in earlier work. No recorded ASR use of this interval was found in the checked manifests, and it was outside the checked embedded supports; the recording prefix had previously been decoded. It is not characterized as wholly unseen or training-held-out. This single fixed interval supports no ASR accuracy, nonzero analyzer-origin, seam, identity or granular-projection claim. There was no interval reselection or inference retry.
