# Additional within-recording validation panel — selection only

Reuse cn-clean-balanced-coverage-v2 eligibility unchanged: at most45 seconds,
3–4 reference speakers, at least60 normalized characters total, each speaker
at least10 characters and10 percent, complete nonempty utterances, no angle-
bracket or ampersand annotation tags. Original NFKC/lowercase/P-S deletion/
whitespace character normalization unchanged. Same pinned full recording only.
No model outputs may enter selection, and this exposed recording is NOT heldout.

Exclude all earlier selected intervals: source [124.876250,163.531625),
[181.894875,221.841250), and current [955.180000,966.750750).
Use exact half-open intervals/sample coordinates. Enumerate eligible windows,
sort shortest duration then earliest start/end; greedily accept at most three
additional pairwise non-overlapping windows. No balancing relaxation, later
performance-based replacement, or extra recording if fewer qualify.

Freeze each input PCM/reference/annotation hash and selection before inference.
Independent review required. Future comparison, if separately authorized, uses
only original baseline versus whole-context Handy with existing fixed settings.
No locality/separator runs or threshold search. Raw references/audio/output stay
private and outside public source Git. This protocol authorizes selection only.
