# Automatic overlap entry, dev76

Opt-in native-turn export binds accepted native core evidence to original WAV bytes. Automatic candidate selection uses existing within-scope intersections and fixed one-second context, without gold labels or generated ASR timestamps. Primary text remains unchanged; local labels are not persistent identities.

On the same exposed three overlap clips, raw detection covers 48.1%, 97.1%, and 58.2% of annotation overlap. Padded support covers 100% in each, selecting five ranges totaling 13.901 seconds; controls select none. All five ranges cut a reference word/utterance boundary, so no complete-window WER/CER claim is valid. This is a small familiar-panel geometry check, not general detection accuracy.

The first actual installed API run failed on a greater-than-100 ms watchdog observation gap during the second export. Eleven native GETs and the first export completed; zero candidate calls occurred. Cleanup and original logical state preservation were independently verified. The rejected sample was not logged, so the maximum retained sample gap is not the actual maximum. The failed attempt is retained. A separately reviewed continuation may use an honestly recorded 500 ms gap threshold while retaining memory/reserve limits; this checkpoint does not claim that continuation succeeded.

Source review: 70 focused tests and 1,000 geometry controls passed. Fresh same-host qualification: 9 gates and 41 controls. Broader suite remains 16 passed / 2 base-precondition failures. Public documentation contains counts/hashes/anonymous geometry only, not audio, transcript tokens, models, databases or secrets.
