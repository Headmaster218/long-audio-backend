# WIP6: corrected continuation recovery source

Source-only preparation checkpoint. Backend source remains pinned to
`2c841d84a9b3204e510628f4aef17fe90334e76c`.
Proposed additive branch: `preparation/rc3-dev3-native-capture-wip6`.
Backup parent: WIP5 `04c91dff87dad6570da77e9cad2d2c1b6ff9bec6`.

## Status and limits

- The original real acoustic run used 19.19 seconds / 307,040 frames. Actual
  native capture, read, and delete succeeded, but the run remains INCOMPLETE:
  the original server mapping classification was missing. Nothing here repairs
  that historical observation or retroactively qualifies the original run.
- The first zero-inference continuation stopped during initial bookkeeping,
  before database cloning or server launch.
- The corrected continuation completed enabled/disabled restarts and four GETs,
  with zero new inference. Its full posthash covered 3,953 files / 1,007,865,815
  bytes, and original-process cleanup was confirmed. Independent final review
  accepted the reconciled zero-inference continuation only. The original
  acoustic run remains INCOMPLETE; broader qualification is not established.
- New server terminal mappings were observed, not separately rehashed as a
  mapping set. Neither restart retained SQLite SHM at exit. The ownership-aware
  SHM classifier was exercised by a real SQLite fixture, not by retained SHM
  in those two restart terminals.
- Historical qualification reported 19 SHM/ownership fixtures and 14 accounting
  fixtures passing. No tests, model work, or execution were performed to build
  this source-only package.
- The product remains a default-OFF, volatile bridge for inputs at most 20
  seconds long. This does not establish lexical accuracy, durable caching,
  or long-input qualification.

## Recovery contents

Seven corrected controller modules and two archived qualification test sources
are retained. The two test files preserve their original relative provenance;
they are not a standalone test suite. The older 19-case suite expects its older
128-addition inventory cap and predecessor sources; the corrected harness uses
168. Both suites also require excluded local qualification inputs.

`continuation-retry-readiness/controller/outer_ledger.py` is a transformed
preparation/recovery copy, not the exact executed artifact: only two historical
run-directory names were replaced with explicit generic placeholders, plus
explanatory comments. Its admission, ownership, accounting, and safety checks
remain intact. Other Python files are exact source bytes. Local-only mapping
and review metadata are deliberately outside the public allowlist.

No receipts, approval/spec/settings payloads, credentials, generated execution
or session identifiers, private absolute paths, audio, transcripts, or model
bytes are included. Source references to those inputs are not the inputs.
Recovery requires a separately approved local setup with matching contracts,
pins, and predecessor artifacts. This package is neither a runnable release nor
permission to launch another run.
