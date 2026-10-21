# Fixed-window identity trial: inconclusive

The fixed eight-minute windows on each of two meeting dates were collected and exported successfully: 46 completed jobs per date, with 11 valid observed enrollment certificates and 10 valid observed query certificates. This is collection/provenance evidence, not identity-accuracy evidence.

The experiment stopped at the preregistered reference requirement. No references were selected, no zero/one/two-reference prediction stages were produced, and query-date annotation labels were not opened. Therefore accuracy, wrong-person assignment, withheld-person false assignment, abstention, precision and pair-link rates are not available. Zero predictions must not be described as zero errors or a successful all-abstention baseline.

## Why reference selection stopped

The test adapter rejected all 11 enrollment observations because their native packets contained overlap witnesses. The exporter copied that field correctly. However, the field counts overlapping label pairs anywhere in the whole packet, not overlap within the selected two-second crop. The adapter applies a conservative whole-packet veto; its rejection does not show that every crop itself overlaps or that the acoustic model failed. The installed producer separately selects a two-second crop after subtracting other-label turn ranges and boundary erosion, and the exported binding revalidates that metadata. Neither process is human listening.

The already-saved enrollment annotation audit also lacks the required person coverage independently of this veto: FEO070 has 0 wholly-singleton observed crops, MEE071 has 1, FEO072 has 5, the withheld MEE073 has 3, and 2 crops are not wholly singleton. The protocol requires two distinct eligible references for each of the three known people. These audit counts are diagnostic only; no alternate references were selected.

## Preserved stopping condition

The windows, thresholds, sampling policy and reference rule were not changed. No rerun or window extension was performed; query labels remain sealed off. The outcome is INSUFFICIENT_REFERENCE_EVIDENCE, not an accuracy pass or failure. The whole-packet veto is a limitation of this test adapter, while missing known-person coverage is a separate observed limitation of this fixed inventory.

Complete observed job/certificate accounting does not prove exhaustive due-slot sampling coverage. That coverage remains unknown, and global identity feasibility remains incomplete. Any future corrected study requires a separately preregistered and reviewed protocol, not a reinterpretation of this stopped result.

This small English same-group, fourteen-day temporal comparison cannot support population, long-term, Chinese-language, autonomous-learning or deployment claims. Production human review/enrollment was not performed. The previous first-six-minute insufficient-evidence result remains unchanged.

The annotation set had been exposed in earlier evaluation work. This is therefore a temporal holdout, not globally unseen annotations. The statement that query-date labels were not opened applies specifically to this stopped run, not to all earlier work.
