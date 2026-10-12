# dev5 permit integration: early source backup

Status: UNQUALIFIED, incomplete source-only snapshot. These three files preserve the first dev5 shared execution-permit and primary Worker/subprocess wiring draft. They are not a runnable standalone package or an accepted service release.

The implementation starts from published dev4 commit 762509bddf4c3f1604c446d485034be8882de6f7. That earlier checkpoint passed 114 focused model-free tests. Those results do not qualify the new dev5 code here. This snapshot has only preparatory static AST checks; no dev5 tests, native imports or model calls have run. Full independent source review is pending, and later corrections must be preserved in subsequent checkpoints.

Planned scope is explicit optional MOSS jobs, one job and no backlog, primary-work precedence, bounded source/result/response lifetimes, and fail-closed process ownership. This snapshot contains only the initial permit, Worker and resources pieces. Cache leases, job manager, HTTP routes, startup fencing, tests and source-release metadata are still being integrated. Do not activate the draft from these files alone.

The intended optional input remains a selected native-owned support of at most 20 seconds. Raw output is scoped to exact native.raw_text UTF-8; EOS remains unknown and complete_content remains false. No primary transcript replacement, speaker identity claim, or long-input optional completion is established.

This additive backup does not change the runtime branch or earlier snapshots. It includes source code only: no model assets, audio, transcripts, execution receipts, credentials or machine-specific configuration.
