# New Chinese cross-session corpus route

Research status: 2026-11-04 UTC. Metadata-only feasibility inspection completed. No speaker embedding, threshold search, production mutation, bulk acquisition, or chosen audio listening has been performed in the feasibility inspection.

## Recommendation

Use a bounded subset of **SMIIP-TV, OpenSLR 156**, if the operator approves the specific acquisition cap and roster. It has direct author evidence for separate recording days. Restrict the claim to **Mandarin mobile prompted/free-text speech**, not meetings, natural multi-person conversations, automatic diarization, or deployment qualification.

Official sources:

- Resource and published license: https://openslr.org/156/
- Author repository, pinned revision: https://github.com/qinxiaoyi/TimeVarying_ASV/tree/6c27fea5d2939b77e295559103375e0bbe70e4ff
- Paper: https://sites.duke.edu/dkusmiip/files/2025/01/Investigating_Long-Term_and_Short-Term_Time-Varying_Speaker_Verification.pdf
- Direct archive: https://openslr.trmal.net/resources/156/SMIIP-TV_v2.0.tar.gz
- Official alternative mirrors linked on OpenSLR: https://openslr.elda.org/resources/156/SMIIP-TV_v2.0.tar.gz and https://openslr.magicdatatech.com/resources/156/SMIIP-TV_v2.0.tar.gz (not probed).

## License and access

OpenSLR labels the dataset “Attribution-ShareAlike 3.0 Unported (CC BY-SA 3.0 US)”. This wording mixes Unported and US terminology; do not silently resolve the variant. Both imply attribution and share-alike obligations. No authentication, registration, payment, or acceptance form was needed for the successful official HTTPS HEAD and bounded Range read. This inspection has not redistributed source audio. Preserve the resource notice and citation, and resolve the exact license variant before redistributing a derived corpus externally if necessary. The archive prefix does not contain a separate LICENSE file.

SMIIP-lab's Hugging Face repository currently contains only .gitattributes and a 65-byte README, not independently downloadable audio files: https://huggingface.co/api/datasets/SMIIP-lab/SMIIP-TV/tree/main?recursive=true . No smaller official audio shard or ZIP was found. Do not use an unlicensed third-party mirror merely to reduce download size.

## Verified cross-session evidence

The paper and author README describe 373 speakers recording across 90 consecutive days in five prompted daily time slots, with three utterances per slot. The paper distinguishes short fixed wake phrases and free text; free text averages 4.45 seconds, while fixed wake phrases average under two seconds. Therefore only **ti_0** free-text utterances are appropriate candidates for the fixed two-second experiment, subject to actual length/purity validation before score access.

Filename dates are not being inferred solely from their appearance. The author's pinned `Untitled.ipynb` explicitly groups six-field utterance IDs using `2020_` plus the first four characters of the second underscore-delimited field, and parses those characters as month/day with Python datetime. It interprets the remaining four characters as the within-day time. The source was read as JSON and inspected, not executed; no external pickle was loaded. Relevant static code is saved in `author_datetime_code.txt`.

Use conservative session keys `speaker_id + 2020-MM-DD`: all time slots from one person's day remain together. Reference and query must come from different dates. Group every selected recording and derivative of the same speaker into one of C or T. The archive prefix root is `release_v1`, despite the downloaded filename containing `v2.0`; retain both identifiers in provenance.

## Actual small metadata acquisition

Successful HEAD: Content-Length **49,951,256,989** bytes, ETag `"ba153b19d-624932ee35f6a"`, Last-Modified 2024-10-16 07:40:25 GMT; server advertises byte ranges.

Successful Range request: `bytes=0-4194303`, HTTP 206, Content-Range `bytes 0-4194303/49951256989`, exactly **4,194,304 compressed bytes**. This yielded the complete first two metadata members and partial beginning of one speaker's audio directory. No audio was listened to or embedded. A partial tar was retained for metadata/header inspection, not a complete archive.

- `release_v1/meta_info/spk2info`: 10,296 bytes, 373 speaker rows, gender/region/age only.
- `release_v1/meta_info/utt2info.zh`: 21,859,582 bytes, 325,161 utterance rows, environment/state/noise fields.
- 366 of 373 speakers have more than one recording date in this metadata.
- The author's `trials/1das/enroll_model` gives 58 official evaluation speaker IDs. Those IDs account for 78,351 metadata utterances and each has at least 91 distinct month/day values.
- The first WAV header inspected is 16 kHz, 16-bit, **two channels**, not mono. Freeze the existing pipeline's actual channel handling. Do not treat the two channels as independent sessions or examples.

Metadata SHA-256:

- spk2info: c43e5e347c6238906cb96096946827215e13efc1c61997f2726be04f142bfff9
- utt2info.zh: a856476121b6bb3d69c304747f2c2ebe5251a145df7e54a3a0742c04ce01258e
- official_eval_enroll_model.txt: 836ab38aebdbbada428db2aa16c5d5d6b4a35e16badba548978b27eec51f408f

## Proposed bounded roster and acquisition

Propose C: K=6 known + 10 unknown; T: K=6 known + 10 unknown. All 32 people are distinct. A one-primary plus two-diagnostic-query design gives 96 queries plus 12 references = 108 encodes. Final roles and utterances must be frozen by the experiment owner before any score access.

`candidate32_metadata.csv` records the first 32 adult speakers in the official evaluation index's existing order. They are:

0073, 0005, 0311, 0035, 0172, 0079, 0318, 0347, 0126, 0222, 0085, 0026, 0020, 0198, 0320, 0205, 0004, 0053, 0107, 0291, 0217, 0249, 0335, 0232, 0203, 0014, 0042, 0050, 0002, 0043, 0277, 0253.

All 32 are adults in the supplied metadata, and all have recordings on May 1/8/15/22 and June 1/8/15/22. A fixed seed can assign the entire speakers to C/T and known/unknown roles. Optional C-May/T-June date windows avoid calendar-date reuse too, but cannot establish different acquisition batches or physical recording environments. The remaining index contains one under-18 speaker, so extending the roster requires reapplying the adult filter.

The first 32 index IDs account for 43,515 utterances. Multiplying their fraction of metadata utterances by full archive size gives a **rough transfer estimate of 6,684,777,534 bytes = 6.23 GiB**; 58 IDs would be about 11.21 GiB. This is a workload estimate, not a physical-offset or compression bound. Only the first archive speaker (`0073`) has been observed to match index order. The order of later directories is not yet verified. Extra members, versions, duration, and compression vary.

Suggested hard budget: **8 GiB total compressed archive bytes** (including the initial 4 MiB probe if counting cumulative transfer). After approval, stream the official gzip sequentially, inspect tar headers safely, discard unselected payloads, and retain only the fixed WAV list plus bounded provenance logs. Never extract arbitrary paths or execute archive content. A gzip byte range cannot generally jump to an arbitrary later WAV without decompression history, so Range support alone does not make the gzip randomly accessible.

Pre-register an acquisition failure rule: if the chosen files are not all obtained within the cap, report BLOCKED/incomplete acquisition. Do not silently substitute speakers, lower N after seeing scores, or exceed the cap. Request a new metadata-only plan or explicit budget change before continuing. Keep retained audio capped separately, e.g. 128 MiB, and do not infer completeness merely from having encountered 32 directory names.

Partial streaming cannot verify the full archive's gzip trailer/CRC or a full-download checksum. Record the official TLS URL, exact response ranges, ETag/Last-Modified, tar member path/size, each selected WAV SHA-256, and resulting canonical PCM/crop SHA-256. This is bounded source-and-member provenance, not a verified full-release checksum. Archive header checksums are not a substitute for an authoritative per-file digest.

## Independence boundaries that remain unresolved

- Corpus is new to the project based on the documented exposure inventory, but the public speaker IDs are anonymous. The supplied metadata has no real-name/alias mapping to prior AISHELL/AliMeeting speakers. **Cross-corpus real-person disjointness is not proven.** Do not call it proven merely because ID namespaces differ. AMI/Oyez/LibriSpeech are also excluded as requested, rather than being backfilled into C/T.
- No full training manifest exists for the frozen model. **Model-training identity/waveform overlap remains unknown.** These recordings are from 2020, so their public release does not establish post-model recording.
- Supplied metadata does not give household IDs, physical device IDs, exact rooms/sites, or transcripts for each free-text item. The author describes participants including students and families. Speaker-disjoint is not proof of household/device/domain-independent or script-independent data.
- `speaker + day` is a defensible conservative recording-event group for the repeated mobile task. It is not a real meeting ID, and there is no basis for pretending these are multi-person meeting sessions.
- Keep raw demographic/state metadata private and only retain necessary aggregate/provenance fields in the experiment report.

## Other routes checked

- CN-Celeb is closer to interview/conversation style, but OpenSLR's CC BY-SA 4.0 label conflicts with the author's noncommercial-only notice. Source-video/session mapping was not verified. Do not choose it merely from filename differences. Sources: https://openslr.org/82/ and https://www.cnceleb.org/dataset .
- 3D-Speaker describes simultaneous multi-device capture; a device change is not by itself a separate recording session. It was not treated as an automatic solution.
- FFSVC currently asks researchers to request data by email, which would require external communication and is not this immediate public-download route: https://ffsvc.github.io/dataset/ .

**Current decision:** SMIIP-TV is the best evidenced, practically bounded route found. Execute only after the operator approves the exact roster/protocol and transfer cap, and keep unresolved identity/domain/training boundaries explicit.

## Frozen proposal update before acquisition

The latest operator-approved selection concept uses up to three metadata-ordered candidates per slot, rather than the single initial candidate described above. The concrete proposal contains 108 slots and 319 candidate files. Earlier unexecuted proposals are retained. `selection_protocol_candidates_v2.json` currently has internal revision 3, adding execution budgets; its filename is retained for the reviewed command.

- Slots SHA-256: `12d3a3a68e50d89e60fc7067dca80fb64d9697c6e2ce75857cb3ce124b622093`
- Candidates SHA-256: `5973e14ff61fe37e3cb54746f09b5de43492d03f9a6869e2a62f13c62b87c56f`
- Protocol SHA-256: `b3f45fc7cb609a00cb9d0bca0d7b383e10c2a17daf3fe0e6a5e6f316ca966bd7`
- Acquisition code SHA-256: `585d1bda4f1e07609543b79aa29ebeae7686c3ce357307a7fdb5964ea2fc4d21`

Each candidate rank is fixed from metadata. A later candidate can qualify for a slot only after every earlier candidate has been read and explicitly rejected by the registered size/format/length rules. A missing earlier candidate cannot be bypassed just because the transfer cap is reached. No listening, scoring, or learned quality filtering is used. The final crop manifest and every candidate rejection must be sealed before any model invocation.

The reviewed acquisition script requires `--execute`; without it, it makes no network request. Resource safeguards include an 8 GiB cumulative archive-transfer cap (4 MiB already spent), 128 MiB retained-source cap, 1 MiB maximum/44-byte minimum per source member, safe literal member paths/basenames, a 5 GiB minimum free-disk check at start and before each retained source/crop write, a 1790-second internal wall signal, a 1190-second CPU signal with 1200-second CPU hard limit, and per-read budget checks. Run under an outer 1800-second timeout as well.

Prepared command, pending explicit operator approval of this exact proposal:

`timeout --signal=TERM --kill-after=5s 1800s python -u chinese-corpus-feasibility-20261104/acquire_capped_proposal.py --execute`

Synthetic-only no-network QA passed for all 108 outputs, short-candidate rejection, an out-of-order rank-2 arrival, preserved candidate audit logs, and 32000-sample channel-0 center crops. A separate low-free-space test stopped before any network bytes, as intended. No real audio bulk acquisition has begun at this update.
