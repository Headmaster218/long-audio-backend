# Nov2 clean balanced Chinese selection — pre-inference protocol v1

Purpose: one new, reproducible paired diagnostic, not a replay of a favorable
historical score. Use only the pinned AISHELL-4 source prefix and annotation
revision aada72727856313b19d4a030383c426364931dbf. HF mirror/full officialarchive
byte identity remains unverified; upstreamCCBYSA4 applies. Professional human-
verified ASR-assisted labels are corpus references, not new model-generated gold.

Fixed input domain: verified complete decoded frames from public source
L_R003S01C02.flac, channel0, HTTPRange0..41943039 only. Headerfull duration isnot
availableprefix duration. Keep truncated-tail decoder error visible. Reconstruct
historical sourceframes1998020..2616506 solely asPCMhash anchor, NOT qualityrun.
No new case may overlap that prior38.655375s diagnostic. Existing priorbalanced
181.894875..221.84125case isnot automaticallyusable: it includesinvalidspeech.

Before modeloutputs, enumerate completeutterance boundaries from TextGrid.
Quantize startsoutwardfloor/endsoutwardceil to16kHz samplegrid; reject anywindow
that crosses ANY annotatednonemptyutterance, includingfractionalframeoverlap.
Keepduration>0and<=45s,3–4lexicalspeakers,atleast60normalizedcharacterstotal,
atleast10characters/personandeach>=10%total. Reject ANYintersectingutterance
containing an angle-bracket annotationtag or '&' overlapmarker: no tag masks,
reference-onlydeletion, or unknownspeechtreatedasempty. Spokenfillersstay.
Useoriginalv2normalization: NFKClowercase/removeUnicodeP/S thenwhitespace for
characterunits. Do notmapnumerals, simpl/traditional ordeletefillers. Unknown
annotationsoutsidecandidatearenotignoredforboundarychecks. Require suppliedRTTM
agreement. Selectshortesteligiblewindow, earliestsourceframe tie. Maximumonecase.
Ifnonequalifies, stop/reportnone; no lowerbalance/floor ormoredatawithoutapproval.

Subsequent proposedpairedrun onlyafterindependentreview/sourcebackup: freshnative
baseline, SenseVoiceauto turncrop/align, thenwholeHandyfixedprompt/2CPU/f16KV/
n_ctx2048. Sameinputforeacharm, nospeakeroracle/goldinput, no optionalnewweights.
Separateoriginalv2CERoracleassignment/S-D-I/per-personmatchedcharacters/unknowns;
noEnglishunitpool/globalidentityclaims. TwoCPU,baseline4GiBAS3GiBsampledRSS;
candidate6GiBAS4GiBsampledRSS,300scompute60cleanup365outer. Oneattempt, failclosed,
positivecleanupbeforedependentphase. No inferenceauthorizedbythisdocument.
