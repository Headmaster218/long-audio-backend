# Bounded public input feasibility: AMI same-day cross-meeting mechanism study

2026-11-04 UTC. Metadata-only investigation; no PCM acquisition, listening, embedding, score inspection, threshold fitting, external communication or registration.

## Decision and remaining blocker

The physically actionable small route is **AMI ES2014a/d and ES2015a/d Mix-Headset**, with 36 fixed annotation-qualified two-second crops across eight official global person IDs. Four individually accessible original WAVs total **228,432,050 bytes (217.8498 MiB)**. All four HTTP range requests for their exact 44-byte WAV headers returned 206 and confirmed 16 kHz mono PCM16, data offset44. Only176 WAV-header bytes were transferred; no PCM bytes were read.

**Do not call these people historically untouched.** The recovered project inventory shows no selected series or selected IDs, but the legacy AMI20 calibration and fresh-holdout full rosters are missing. This is a real provenance limitation, not an acquisition blocker. Proceed only if the experiment owner explicitly accepts a mechanism experiment with current C/T disjointness and unknown overlap with old development. Otherwise independently unexposed input remains blocked.

## Concrete roster and design

- C: known FEE055 and MEE053; unknown MEE054 and MEE056. References ES2014a, queries ES2014d, both2005-03-02.
- T: known FEE057 and FEE058; unknown FEE059 and FEE060. References ES2015a, queries ES2015d, both2005-03-09.
- Each known has three references from distinct original segments in meeting a, with start separation≥30seconds. Each person has primary query slot1 plus two diagnostic queries from distinct segments in meeting d, also≥30seconds apart.
- 12 references+24 queries=36 distinct clips,72seconds total. The two arms must share identical queries and the single reference must be candidate reference slot1.
- First two IDs in each series' lexicographic global-ID order are known. This is a prospective metadata choice, not a score-based selection.
- Explicit scope: English same-day earlier-meeting→later-meeting mechanism study. These scenario submeetings belong to one day's elicitation, not independent days. It is not the strict three-reference-days D protocol, not a Chinese meeting benchmark and not evidence of long-term identity stability.

## Exact sources, sizes and rights

Official URL prefix: https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/

- ES2014a/audio/ES2014a.Mix-Headset.wav:36,768,472bytes.
- ES2014d/audio/ES2014d.Mix-Headset.wav:93,163,564bytes.
- ES2015a/audio/ES2015a.Mix-Headset.wav:36,689,964bytes.
- ES2015d/audio/ES2015d.Mix-Headset.wav:61,810,050bytes.

The complete URLs, Content-Range, ETag, Last-Modified and header hashes are in official_wav_range_headers.json and prospective_annotation_candidates.json. Direct original WAVs do not require authentication or registration. HEAD probes of additional ES2014b/c and ES2016a were also successful; they are not selected and no header/PCM was read from them.

The source license is CC BY4.0, publicly stated at https://groups.inf.ed.ac.uk/ami/corpus/license.shtml . Preserve attribution to AMI Meeting Corpus, license link and a description of cropped modifications. Signals/channel conventions: https://groups.inf.ed.ac.uk/ami/corpus/signals.shtml . Stable global participant IDs and meetings.xml mapping: https://groups.inf.ed.ac.uk/ami/corpus/participantids.shtml . a→d chronological scenario-meeting order: https://groups.inf.ed.ac.uk/ami/corpus/meetingids.shtml . Original annotation download/version: https://groups.inf.ed.ac.uk/ami/download/ .

## Annotation evidence, not acoustic proof

Official retained ami_public_manual_1.6.2.zip supplies corpusResources/meetings.xml, per-agent segments and words. meetings.xml SHA256:8ab6cdcf03ed863e839418f31c7380392397a2eed892d73b9bc1b438c7ec520a. Candidate manifest binds every used annotation file hash.

Selection uses the earliest2seconds within each original segment that also fit a contiguous union of positive-duration lexical word alignments. Original segment and word-chain boundaries are eroded100ms. Every other participant's full annotated segment is expanded100ms and subtracted. The same participant's positive-duration non-word events (including gap,vocalsound,disfmarker when timed) are also expanded100ms and excluded. First qualifying candidate per segment is used, then chronological≥30second spacing. No RMS, VAD, scores or listening influenced selection.

This lexical-word coverage filter is an additional prospective choice that must be frozen before acquisition/scoring. Forced alignments and human segment labels can be wrong or contain long assigned-word silences. Therefore label the result **annotation-qualified single-speaker**, not human-listened clean speech, guaranteed physical purity or unattended-clean sampling. Zero-duration punctuation/nonword markers are not treated as positive-duration events.

Exact source-frame coordinates, globalIDs, channel-agent mappings, annotation segment IDs, source byte ranges and source header evidence are in prospective_annotation_candidates.json. Source PCM hashes and crop hashes remain unavailable until actual audio acquisition; ETags or header hashes do not replace whole-source SHA256.

## Acquisition recommendation

For stronger provenance, obtain the four complete original WAVs under a separately frozen≤512MiB transfer+retention cap and≤20minute wall cap, as proposed by the experiment owner. Their verified total leaves≈294MiB headroom. Check exact expected Content-Length and source geometry, retain full-source SHA256 and per-crop SHA256, and reject any changed/missing/malformed source rather than substituting people or slots. This worker did not launch that acquisition.

A smaller alternative is exact PCM byte ranges:44+2×start_frame through44+2×end_frame−1 inclusive. The36 two-second ranges total2,304,000 PCMbytes; header requests are176bytes. A partial download cannot establish a full-file hash. Range support is verified only for headers in this investigation, not for the exact later PCM ranges. If partial acquisition is used, fail closed on200responses, unexpected ranges/sizes/ETags or any cap breach.

## Prior-use inventory and limits

historical_exposure_audit.json records a bounded text/filename scan of127,271 local metadata/source files. Known exposure groups EN2001,EN2002,ES2002,ES2004,ES2011 andTS3003 expand via official globalIDs to25 people and24 meetings. ES2014/15 and their eight globalIDs do not overlap those known exclusions and were not found in historical text outside this new feasibility folder.

The recovered profile explicitly records20 old calibration people and unnamed fresh-holdout evaluation. Their original roster is missing; a971c82cc368af62286ac820567a47732083109de557155bf4149b40ec94b8fa is only the referenced artifact digest. The source mentions benchmarks/private_speaker_embeddings/*.json, not retained here. This audit cannot reconstruct an unseen roster from counts. Model-training person/waveform overlap and cross-corpus real-person aliases also remain unresolved.

## Chinese route and statistics

SMIIP-TV remains unsuitable for this small immediately bounded rerun: official audio is one≈50GB gzip with no verified individually accessible files. All32 previously requested people must be excluded, not just retained/scored people. The first21 directories were reached only after4.8GB/30min; fresh eval people are after the first32 and gzip ranges do not provide random access. Its available metadata has no frame-level speaker/overlap support, so acquiring new mobile utterances would still not prove clean speech. No better officially accessible Chinese multiple-session small source was established in this bounded pass. SHALCAS22A has a3.9GB archive and no verified multiple-session evidence, so it is not a justified substitution.

Each split has only2 known and2 unknown people. Additional queries are correlated diagnostic observations and must not increase independent-person n. Even0/2 unknown accepts has a one-sided95% binomial upper bound≈77.6% under iid assumptions, which this small convenience selection does not establish. No low-error guarantee, automated identity merge permission or product deployment conclusion follows.

## Artifacts and validation

- prospective_annotation_candidates.json:36exact crops,8person roster,4source HEAD/range bindings.
- build_annotation_candidates.py:reproducible metadata-only selection.
- official_wav_heads_initial.json and official_wav_range_headers.json:actual HTTP observations.
- historical_exposure_audit.json:known old-source exclusions and unrecovered-roster blocker.
- metadata_candidate_validation.json:36slot,12ref/24query,32000frame,distinct-turn,30second spacing,C/Tperson/meeting disjoint and no unknown enrollment checks passed.

These checks establish a feasible metadata plan only. None claims acoustic validation or a completed experiment.
