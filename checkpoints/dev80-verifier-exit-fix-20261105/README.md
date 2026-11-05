# Optional verifier exit-race fix

The resource reader now recognizes only its retained original process after positive exit evidence, matching identity and no remaining descendants. Live, unknown and reused identities remain blocked; resource caps and cleanup ownership are unchanged.

This source checkpoint is independently reviewed. Fresh installation and a separately authorized new short operational trial are pending. The old dev79 task and immutable ledger are not migrated or rewritten. Its primary recovery succeeded, but candidate admission did not; both retained failures are recorded in [the dev79 outcome](../dev79-candidate-postphase-20261105/PUBLIC_OPERATIONAL_OUTCOME01.json).
