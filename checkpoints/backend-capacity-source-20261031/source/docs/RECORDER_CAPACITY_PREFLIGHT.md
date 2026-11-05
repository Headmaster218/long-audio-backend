# Controlled capture capacity preflight

Run the installed interpreter with `-I -B -m audio_backend.recorder_capacity_preflight`.
This prints JSON without HTTP or filesystem writes. `-B` also disables Python's
ordinary import cache writes. An arithmetic report is not permission to activate
services, change existing configurations, or resume uncertain uploads.

The default reviewed profile is **mono 16 kHz PCM16, 300-second segments, at most
144 receiver attempts per generation, 192 analysis inputs and 12 hours per span**.
It explicitly retains the receiver's 8 GiB raw ceiling. It assumes small canonical
WAV headers (at most 64 KiB ancillary/header overhead) and bounded sidecars.
The capture-profile simulator accounts for page source slots, not only seconds.
One-second segmentation can exhaust source slots after 14 successful originals.
The older 256-attempt receiver default cannot safely be paired with 192 analysis
inputs. Equality is also unsafe: analysis must have strict input-count headroom
for successor discovery. Existing defaults are not changed.

144 full rotations contain 12 hours but are **not EOS**. A terminal-aware producer
must mark its actual final original STOP, including when that original is a full
300 seconds. Otherwise an adjacent generation is required. This does not cover
legacy exact-boundary rotation followed by an extra nonempty STOP tail.

Every new raw attempt reserves 536,880,640 bytes before accepting files. Setting
raw capacity to merely 12 hours of PCM can therefore rotate early. The report
checks this full reservation plus retained raw/publication accounting. Partial,
failed, and empty attempts consume capacity and can rotate much earlier. They do
not create permission to retry, adopt, discard, or fabricate audio or STOP.

For existing prepared namespaces, stop the receiver using its documented normal
shutdown, then run:

```
python -I -B -m audio_backend.recorder_capacity_preflight \
  --receiver-config /private/routes/receiver.json \
  --runner-state /private/runner-initial
```

This opens existing lock files without creating or modifying them, locks every
retained raw namespace and publication tree, validates all bindings, counts all
attempt directories including unpublished ones, and refuses oversized inventories
or a busy receiver. It compares the runner's inherited max_hours/max_inputs with
the complete retained route. It does not inspect/repair ongoing analysis journals,
prove the live process uses these files, or qualify existing media format. A
stopped inventory is a point-in-time observation, not a live admission promise.
Unpublished counts cannot distinguish interrupted, failed, and never-started
uploads durably; their full reservation is reported separately and conservatively.

Finite-plan defaults here reserve 16 actions per phase, 192 discovery polls, 32768
aggregate actions and 4096 aggregate polls. For N generations, the conservative
whole-invocation envelope is `((N-1)*(phase_polls+1)+phase_polls)*phase_actions` actions and
`2*N*phase_polls` polls. This bounds allowed work, not runtime success. The original
64-poll default cannot drain a 144-original backlog. Initialization-only network
reserves exclude all intake/status/export traffic and reviewed identity handoff.
Network responses, inference, polling and the deadline remain independent limits;
no static calculation guarantees a complete invocation. A bounded pause needs
operator review and an explicit later invocation, never an uncertain write replay.

Use the separate fresh bundle workflow for a reviewable new deployment. Neither
command promises indefinite service, arbitrary-upload compatibility, uninterrupted
capture, zero-loss delivery, or speaker/recognition continuity.
