# Speaker identity boundaries

Local diarizer labels are scoped to a window/epoch and are not permanent identities. The service uses independent model-bound speaker evidence to propose cross-recording associations, with unknown/provisional states for insufficient or ambiguous evidence.

The opt-in `eres2net-ami20-v1` profile binds the precise ERes2Net model digest and 512-dimensional output. Its single-reference threshold is 0.55, verified-centroid threshold 0.56, novelty threshold 0.45, ambiguity margin 0.10, and minimum clean evidence 2 seconds. These thresholds are not universal biometric guarantees. Scores are cosine similarity, not calibrated probabilities.

Ordinary automatic matching does not update stored references. Verified examples, corrections, merges and reversals have separate semantics. A name change is not proof that every sample used by an embedding was reviewed. Short, mixed, far-field, cross-date and altered-style recordings remain failure risks; common-cold robustness is unverified.

Audio, transcripts and stored voice features are sensitive application data. Keep the API local and protect its data directory. This is not an authentication or high-impact identity-decision system. See [API](API.md) and the model profile for the implemented contract.
