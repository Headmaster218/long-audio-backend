# Controlled real-route result

All three retained-evidence ASGI controls passed on 2026-11-03. Final run: 3 passed in 2.90 seconds; one existing Starlette/httpx deprecation warning. No listening socket or native inference executed.

- Default-disabled watch: actual production cursor GET returned HTTP 409 with transcript_revision_changed. Watch returned unresolved and retained partial files, without a successful export receipt.
- Opt-in two-attempt watch: actual first cursor GET returned the same typed 409. Fresh attempt two read a new revision and committed five nonempty units covering 144000 frames. Failed attempt-one file hashes, sizes, modification times and change times were unchanged from the first subsequent GET after normal failed-attempt file closure through successful recovery and receipt verification. The subsequent watch invocation verified its durable done receipt with zero HTTP requests.
- Persistent conflicts: both actual cursor requests returned typed revision conflicts. Watch returned unresolved, preserved both partial attempts, produced neither a third attempt nor a success receipt, and left the first failed attempt unchanged during the second.

Every request was GET. All original run and producer files retained exact hashes, sizes, modification times and change times. Production source manifest verification passed before and after each control. Response leases were zero after each case, and the cooperative original/source write guard recorded no denied write attempts.

This is a synthetic completed runner-stage fixture using retained same-job results and real production read routes against a disposable copy. It is not historical-run resumption, a live deployment test, native replay, or an accuracy/device/capture-completeness claim. The deliberate revision-counter mutation affects only the disposable test database. The original database was not opened by SQLite.

Test SHA256: dbc6bcfd415c3f2dc8248d7deec4abd60109fbebe36cf1ea340b4f4851dd725e

Runner SHA256: d6dbed92dee7128360b41c74d949c87a161f020af5cba97f59e9cb320245d82e

The fixture uses disk_floor=0 for these bounded model-free tests. This does not qualify or relax any runtime launch gate.
