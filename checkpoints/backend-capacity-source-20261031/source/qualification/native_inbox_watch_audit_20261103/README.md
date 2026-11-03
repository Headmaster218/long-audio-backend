# Independent finite native inbox watch audit

This verifier reads the closed saved evidence from the one confirmed native
finite-watch run. It never launches a service, sends HTTP, or opens original
service databases through SQLite. It copies closed databases and sidecars into a
fresh private audit directory and opens only the copies read-only and immutable.
Nonempty recovery WAL/journal evidence is refused instead of recovered in place.
Raw results, copied databases, audio, transcripts and logs remain outside Git.

The executed source checkpoint was
`1b2537d60e2a1f7afe69bcc66eef35a043d340b7`, extracted into a fresh candidate
with only the accepted runtime qualification patch. Candidate bytes are checked
against that immutable Git tree, not a subsequently edited source directory.
All 907 candidate files and 2,157 runtime/model assets are reverified.

The producer verifier checks the clean Android source pin, all 256 assembled
Git blobs, compiled-class pins, candidate-bound generator and Java harness,
original public WAV and selected PCM, source coordinates, and nine original
spool/envelope metadata pairs. The last delivery must be genuine STOP from the
real terminal-aware writer API. Default INTERRUPTION behavior remains available
and historical fixtures are not rewritten or relabeled.

The event verifier checks a single bounded foreground watch, full ordered
request start/return/error coverage, eight post-start arrivals, two actual
marker-absent observations with no intervening HTTP, full identical before/after
runner/intake inventories, explicit finalization after all nine inputs including
STOP, and GET-only export boundaries. All 18 writes must be inside finalization.
Independent status and repeated export are separate GET-only child processes.
Historical original-Popen wait receipts are checked, not re-executed.

The closed-copy audit binds original source PCM, snapshots, uploader hash chains,
native source/core receipts, canonical primary ASR rows, readable text, private
permissions and completion hashes. Both exports must match byte for byte. It
checks service terminal predicates and that originals retain their exact hashes,
sizes, mtimes and ctimes after inspection.

## Run on the closed envelope

Use the qualified interpreter and a fresh private output outside all evidence,
source, candidate and producer trees:

```sh
python -I -B audit_inbox_watch.py \
  --envelope <closed-native-watch-envelope> \
  --preflight <closed-native-watch-envelope>/PRELAUNCH_REVIEW.json \
  --output <fresh-private-audit-directory>
```

The verified saved-evidence run has **30 passing controls**: 19 model-free
ordering/bounds/receipt/tamper controls and 11 native/export closed-copy controls.
The latter reject permission drift, artifact hash drift, a mutating export call,
rehashed foreign ASR text or work identity, altered timestamps, unsupported
accuracy claims, readable-text drift, ASR-skipped receipts and native receipt
source drift. Every mutation targets a disposable copy. The original envelope's
full inventory is compared before and after the controls.

```sh
NATIVE_WATCH_AUDIT_EVIDENCE=<closed-native-watch-envelope> \
  python -B -m pytest -q test_audit_inbox_watch.py
```

Without the environment variable, the 11 closed-copy controls are skipped. This
count is separate from the 38 focused harness/fixture controls and is not a full
repository test result.

The audit does not establish ordinary FTPS STOR behavior, deployment, a daemon,
Android/microphone execution, hardware continuity, recognition accuracy, word
alignment, speaker identity, full-capture completeness, multiple pages, long
endurance, abrupt crash recovery or native-service restart. Arrivals are
controlled atomic local publications. STOP is a producer declaration and EOS
closes only the selected span. Limits remain sampled/cooperative.
