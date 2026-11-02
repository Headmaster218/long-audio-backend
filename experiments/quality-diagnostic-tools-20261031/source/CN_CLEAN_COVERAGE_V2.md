# Clean balanced CN: one coverage extension, before inference

The frozen prefix-only v1 search found zero eligible windows. This v2 changes
only the search domain to the complete SAME recording at the SAME pinned
revision, and excludes the prior unrun balanced interval as well as the old
214-character diagnostic. No performance outputs are used in selection.

All v1 quality criteria, normalization and shortest-duration / earliest-start
tie ordering remain unchanged. Excluded source intervals are
[124.876250, 163.531625) and [181.894875, 221.841250).

Approved retrieval: append bytes 41943040–286036323 to the verified first
41943040 bytes; require HTTP 206 and exact Content-Range/length. Full FLAC
must match LFS SHA256
93125b2c8f9f73c042ccd63178d1fec6db2d3047af4078834da7c6c1aa118e72
and 286036324 bytes before decode. Stream decode channel 0, not a full 8-channel
in-memory buffer. Preserve receipts privately.

Source: https://huggingface.co/datasets/AISHELL/AISHELL-4/resolve/aada72727856313b19d4a030383c426364931dbf/test/wav/L_R003S01C02.flac
This proves mirror-object identity, NOT byte identity with the official OpenSLR
archive. Upstream CC BY-SA 4.0 remains the conservative corpus license.

No further recording/corpus coverage extensions are authorized. If this domain
still yields zero, stop. If a case qualifies, freeze external references/input
hashes, obtain independent review and source-only backup before any inference.
