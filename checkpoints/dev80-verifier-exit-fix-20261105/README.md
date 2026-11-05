# Optional verifier exit-race fix

The resource reader now recognizes only its retained original process after positive exit evidence, matching identity and no remaining descendants. Live, unknown and reused identities remain blocked; resource caps and cleanup ownership are unchanged.

This source checkpoint is independently reviewed. Fresh same-host strict qualification and both installations now pass using one shared wheel (INSTALL_SUMMARY.json). The independent short trial completed primary/export, but candidate admission was blocked by an owned-verifier fd permission observation before dispatch. Candidate calls remained zero; see PUBLIC_OPERATIONAL_OUTCOME01.json. The failed trial is retained. The old dev79 task and immutable ledger are not migrated or rewritten. Its primary recovery succeeded, but candidate admission did not; both retained failures are recorded in [the dev79 outcome](../dev79-candidate-postphase-20261105/PUBLIC_OPERATIONAL_OUTCOME01.json).
