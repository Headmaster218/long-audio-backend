# Reviewed primary-observation person bridge: unqualified source snapshot

Status: **UNQUALIFIED_NOT_RUN**. This is an early source backup, not an installed
release or a functional, acoustic, privacy-erasure, or resource qualification.
The existing experimental dev60 product remains unchanged on its product branch.

The snapshot extends the source on commit
`d124fc3c0948d5a2a0a5b99870ca4648527d1edc`. All earlier reports are retained,
including the negative ERes2NetV2 confirmation and the terminal-release ordering
result, which found no actionable defect in the tested orderings.

## Intended feature boundary

The opt-in, default-disabled API associates an exact retained primary observation
with an immutable person UUID through an explicit human claim. It adds group-level
person and occurrence reads, revision-fenced correction and undo, and scoped
association-history erasure. Claims apply only to the observed crop; they do not
certify whole-parent speech, automatically enroll acoustic exemplars, or change
transcripts, embeddings, matching thresholds, or model dimensions.

The implementation spans thirteen runtime modules, including two new bridge
modules and one current-association index table. Existing review/relation ledgers,
generations, revisions and idempotency rules remain the authority. Normal source
retirement, source revocation, association erasure, and bank-wide identity reset
have distinct contracts. Synthetic tests must qualify those contracts before use.
No existing user dataset or personal association has been deleted.

## Bounded metadata working state

The new transient bridge-operation lease is planned at 32 MiB with one admitted
operation per budget owner and immediate refusal on contention. It is separate
from the existing response-buffer pool, which remains bounded at 1.5 MiB. The
operation lease must remain held through actual worker completion and response
serialization; the response lease lasts through ASGI completion.

This does not subtract from the producer's 8 MiB staging quota or reduce the
existing 1,024-person and 4,096-relation capacities. It uses the original
materialized canonical loader and the existing 0.75-second/250,000-VM-step metadata
read envelope. Oversized corrupt fields are intended to be rejected before
materialization. The 32 MiB charge is a conservative planning allowance, not a
measured RSS ceiling. Future whole-service checks must include up to 33.5 MiB of
transient overlap; an atomic whole-service 6 GiB guarantee is not established.

## Evidence and next gate

The thirteen Python modules were parsed statically. Product imports, test
collection, feature tests, package installation and model execution have not been
performed for this snapshot. No audio, reference annotations, vectors or original
recording databases are included in this checkpoint.

Static review found pending allocation-guard gaps in this exact snapshot:
canonical relation scalar fields, policy/generation rows, prior retry responses
and remaining event metadata need complete SQLite type/byte preflight before
materialization. Resource and
functional qualification remain blocked until those guards are completed and
tested. These known gaps are retained in this unqualified backup.

Pending checks include real synthetic SQLite/API transactions, exact retries and
revision conflicts, replacement/retraction/erasure without resurrection, source
delete/revocation, old-writer refusal, occurrence pagination and concurrent
cancellation. Full legal canonical capacities and actual lease cleanup require
measurement. Publication of this backup supplies no pass credit for those tests.
