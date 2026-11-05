# Automatic admission validation, 2026-11-05

Development source version: 0.2.0rc3.dev70. This is cloud-only functional evidence, not phone/server deployment or long-duration qualification.

## Verified

- Original feature revision: 94 focused source tests, 94 installed-wheel tests, and six additional independent boundary tests passed. A clean Git-restored snapshot passed all 100 focused checks and its 1,054-file source manifest.
- Follow-on hardening checks publication ambiguity in every retained generation before new admission, including retired generations. Readback remains available. The final wheel passed all 101 focused tests, including a retired-A preparation with B active after restart.
- All 184 packaged product files exactly match the final source and installed bytes. Final wheel SHA256: 0e71061627f1918a7b2d1307cb4ca419d02bae51e53d7684614299e3ae06f28e. Earlier wheel hash 9c3313d42cb8c6f130b2d709f2ffa2f49f23342dd06ce5bb1998771f964fce48 identifies the pre-hardening candidate, not this final wheel.
- Real loopback TLS exercises A admitted before B, B completing first, A completing afterward, old readback, conservative restart and full-registry refusal. A deliberate publication failure after rename yields 451, preserves original routing and never republishes it; explicit restart retains immutable readback.
- Independent checks cover publication-only byte capacity, unfinished-attempt entry reservation, configured active ahead of used, no-effect exhaustion and changed generation ordering.

## Broad regression limits

The first whole recorder test selection completed: 1,605 passed, 10 skipped, four failed in 473.26 seconds. The four failures are all historical `test_recorder_span_native_audit.py` fixture expectations: its frozen 2026-11-03 audit rejects the later production profile hash before those tests reach their intended assertions. An isolated checkout of the pre-feature commit 4e885651d6407a31cbbcf18aec393d74d998f747 reproduced exactly four failures and five passes in that module. No qualification hash or native source was relaxed or changed.

The ten skips require explicitly supplied Java producer/Android transport source fixtures. They are not passes. Final-code recorder regression excluding that separately reproduced historical module is running at this checkpoint; do not describe it as a complete pass. Focused final-code installed checks above have completed.

## Remaining work

Automatic analysis-session handoff is separate. This receiver does not create successors, delete originals, repair uncertain uploads, reserve physical disk blocks, guarantee indefinite storage, or prove recognition continuity. Exhausting all prepared generations returns refusal; stopped/interrupted uploads may need review.
