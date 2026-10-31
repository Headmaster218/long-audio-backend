# Chinese matched whole-excerpt diagnostic — PREP ONLY

Separate214-character diagnostic; NEVER pool with English word counts.
One fixed38.655375s/618486frame mono16k PCM16case,3reference speakers,
6complete utterances,1.84392167soverlap. Annotation-only selection alreadylocked
before modeloutputs. Source frames1998020..2616506, channel0. No reselection.
Reference dominated by one person204/214chars; minority speakers8and2chars.
Report per-speaker oracle errors/counts as well as total; tiny responses matter.

Reference provenance: human-verified ASR-assisted AISHELL-4 transcript/RTTM,
HF AISHELL/aishelltech mirror commit aada72727856313b19d4a030383c426364931dbf.
Mirror identity/fullarchive byteidentity NOT independently verified against
OpenSLR official5.2GB archive. Preserve upstreamCCBYSA4 restrictions despite
mirrorApache metadata. Exposed diagnostic, not independently heldout claim.

Use original v2 scorer language=zh, exact NFKC lowercase normalization deleting
Unicode P/S, then whitespace removal to character units. This agrees with all
214 selected raw-reference characters. Do NOT perform reference-preparation-only
<...> or & token cleaning on hypotheses; no filler/numeral conversion/removal,
no simplified/traditional conversion, no post-output normalization updates.
Native ITN remains actual implementation behavior, not reference rewrite.

Fresh native→SenseVoice/raw/turncrop/align baseline→wholeHandy, samefrozen
geometry and weights. Baseline explicitly language=auto BEFORE outputs; earlier
English config wasen, notauto. Existing analyzer passes configlanguage to
sherpa SenseVoice use_itn=True/providerCPU; Chinese scriptas_is. Handy language
argument remainsunset, fixed baked modelprompt (not forcedzh or English).
No gold/hotwords/modelparameter hints. Existing2CPU,baseline4AS/3RSS,candidate
6AS/4RSS,300scompute60cleanup365outer, fresh perphaseprocess/outputdirs.
Outputpins gate dependent ASR/scoring. No retry/tuning. Operational resource or
unknowncleanup failure aborts; any other invalidcase requires explicit review.

Report both raw baseline/candidate diagnostic validity, CER oracle assignment
and unknownbucket, no global identity claim. EOS/token count remain unavailable,
no semanticcompleteness claim. Future model execution requires separate authorization and
independent pre-run review of external private manifests. No new weights/install.
