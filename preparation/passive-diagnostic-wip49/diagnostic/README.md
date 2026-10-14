# Passive diagnostic harness templates

This directory preserves the source structure of a bounded two-upload seam qualification harness with passive diagnostics. All public source is unexecuted after substitution. Generate current paths, process identities, byte counts, artifact digests, runtime manifests and prerequisites before use. The repository does not contain executable configuration, input audio, model payloads, runtime installations, databases or run records.

Diagnostics retain original return values and primary exception identity. They collect safe exception types and built-in SQLite codes/names, capped at 64 observations per process. They never call arbitrary exception getters, repr, str or exception messages. An existing journal receives a lossy best-effort flush; contention, capacity exhaustion and sink failures can drop observations. A flush advances its cursor before emission, so failed batches are not retried. No SQLite policy, retry, durability, deduplication or operation-count change is introduced.

The derivation contract substitutes exactly eleven harness rows while preserving 3,162 other inventory rows. Current descriptor observations are separate from historical identity continuity. The controller still requires complete pre/post source hashes, separate exact execution configuration, positively owned process cleanup and a fresh run namespace. Missing prerequisites fail closed before provider execution.

The companion actual-owner directory contains the process supervisor, bounded guard and fourteen focused diagnostic groups. See the top-level README for the qualified scope, known incomplete actual result, resource limits and portability requirements.
