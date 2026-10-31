# Hot-byte quota and historical placement checks

The payload quota accounts only currently resident PCM/vector BLOB bytes. It is
not a claim that every archived body was rehashed for every new audio window.

A strict payload Store now performs the original complete SQL placement census
while initializing under its existing exclusive initializer ownership. It checks
hot lengths, hot/locator conflicts, and cold locator/unit relationships, then
installs two exact partial indexes. This audit does not read archived file bodies.
An ephemeral qualification binds the Store, database device/inode/path, frozen
policy, and safety-relevant schema. It is not persisted and must be rebuilt on
reopen. Unqualified connections retain the original full census.

Qualified quota queries inspect every non-NULL hot row (regardless of status),
reject conflicting locators or lengths, and sum bytes through partial indexes.
Existing hot-only insert guards and immutable/authorized archive-transition
triggers maintain cold SQL placement. Relevant schema drift, changed database
identity, or changed policy refuses the fast path. Unrelated lazy schema creation
is accepted only after comparison with the original safety-schema snapshot.

Historical evidence reads still validate exact locator ownership, manifest/body
hashes, bounds, and file signatures. Full stopped-run registered-file hashing and
SQLite checks remain separate terminal validation. Arbitrary external SQL writes
that bypass the application's exclusive-ownership contract are not supported.

This changes neither the original primary VM/time/memory budgets nor model
inference, transcript rows, provenance acceptance, or explicit retry policy.
