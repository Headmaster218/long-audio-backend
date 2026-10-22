# Opt-in native crop support for primary identity suggestions

This candidate is separate from the closed dev68 trial. It does not repair,
reinterpret, resample or establish results for that trial. No acoustic accuracy
or cross-day identity acceptance is claimed by these synthetic contracts.

## Explicit new-bank configuration

The default verified policy remains schema 1, with the existing whole-packet
overlap veto. Schema 2 must contain the same seven required fields and exactly
one additional field:

```json
{
  "schema_version": 2,
  "experimental": true,
  "single_threshold": 0.75,
  "centroid_threshold": 0.75,
  "margin": 0.1,
  "minimum_query_seconds": 2.0,
  "minimum_enrollment_seconds": 2.0,
  "primary_query_overlap_policy": "native_crop_support_v1"
}
```

This requires a **new identity bank** with explicit activation. An existing
bank's mode, policy and policy hash are immutable. Changing the configuration
does not switch an existing bank, migrate claims, copy references or reuse its
pending/completed queries. Same-bank policy migration is not implemented.

The durable query identity includes identity group and generation, which bind
the immutable policy. Schema-2 outcomes additionally retain the exact policy
hash and version; reads refuse mismatched or missing schema-2 outcome identity.
No suggestion table migration is necessary. A new bank starts without reviewed
references; this feature does not provide authority to enroll any reference.

## What changed

For schema 2, HistoryStore derives a local support result during the same
snapshot that verifies the immutable packet, source mapping, parent, revision,
certificate/header, original crop and complete packet-wide witnesses. It
retains all packet witness checks. The added result is internal and outside the
human binding document, so the exact human binding digest is unchanged.

The selected two-second crop plus the existing 1,600-frame margins must have
continuous target-label coverage, no other-label intersection, and exact
logical/physical mapping. Missing, malformed, ambiguous, stale or revoked
evidence cannot qualify. Half-open intervals allow another label to touch the
outside edge of the margin, but a one-frame intrusion does not qualify.

This means **native metadata supports the crop**, not that a model's label is
ground-truth correct or the audio is acoustically pure. Packet-external overlap
can now proceed to existing scoring; a match is not promised. Minimum duration,
thresholds, margin, negative evidence, contradictory manual claims, gallery
capacity, certificate validation and explicit human enrollment are unchanged.
No transcript rewrite or automatic enrollment is introduced.

## Qualification scope

The new test module contains finite geometry tests and real-Store synthetic
integration cases; no models, downloaded audio, service, subprocess or ambient
database is used. Fixture lifecycle now acquires a real InstanceLock and binds
and reconciles startup recovery before synthetic primary admission, with
explicit revoke/release cleanup. The existing schema-1 overlap test remains
unchanged as the conservative-policy control.

Run focused new tests and the existing overlap control first. Then run existing
primary-voice and reviewed-person contracts, plus the restored startup recovery
module. This is not the missing historical crash-helper qualification, package
qualification, runtime qualification or a real-audio evaluation. Test results
must be recorded by the executing parent; this document claims no tests ran.

A subsequent acoustic evaluation needs a separately frozen prospective sampling
plan, an opportunity-level sampling census, adequate reference coverage, sealed
predictions before query labels, and an explicit insufficient-evidence outcome.
The former dev68 labels/windows are not an automatic retry dataset.
