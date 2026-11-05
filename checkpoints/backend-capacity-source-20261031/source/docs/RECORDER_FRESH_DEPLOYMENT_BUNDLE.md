# Fresh finite deployment configuration

This workflow prepares local configuration only. It does not launch a listener, create a backend session, install software, alter an existing configuration, copy credentials, or adopt existing recordings. Use a qualified installed backend and a private directory outside the repository. Do not point it at a directory that already exists, including an incomplete earlier bundle.

## 1. Review the controlled producer profile

The supported profile is mono 16 kHz PCM16, 300-second segments, with terminal-aware physical STOP. Each generation admits at most 144 receiver attempts, with 192 analysis inputs and 12 hours of analysis capacity. Retain the explicit 8 GiB raw allowance: a full upload reservation is much larger than an ordinary five-minute segment. Failed, empty, and partial attempts consume receiver capacity and may advance the receiver early. This is not a promise that arbitrary uploads, interrupted producers, or all runtime budgets fit.

A full final segment marked ROTATION does not mean recording complete. The producer must declare STOP when genuinely stopped; otherwise a successor generation is necessary. This finite plan does not run indefinitely, delete old generations, provision new storage, repair partial uploads, or synthesize STOP.

## 2. Prepare a fresh bundle

First obtain a valid single-generation endpoint configuration using the existing `audio_backend.recorder_ftps_receiver configure` command and existing credential/certificate files. It is a read-only transport template; its inbox, raw uploads, and publication state are never adopted. Existing custom connection/time policies that the fresh-config API cannot preserve are refused rather than silently reset. TLS trust, host accessibility, and firewall configuration still need operator verification.

Using the qualified environment's Python, run:

    python -I -B -m audio_backend.recorder_deployment_bundle --bundle-dir /private/new-recorder-bundle --endpoint-config /private/existing-receiver.json --api http://127.0.0.1:8000 --generations 3 --segment-seconds 300 --max-attempts 144 --max-hours 12 --max-inputs 192 --acknowledge-fresh-only

The destination must not exist and its parent must be private. The command validates the complete profile before creating anything, then uses the supported publication, receiver, and routing configurators. It creates identical full generation rosters in all route files, with distinct active selections. Secrets remain at their original paths. **Launch gate:** this bundle is for a genuinely fresh destination/use with no outstanding historical attempts. It is NOT an in-place replacement or upgrade of a used endpoint. Reusing the same address, ports, and account with only fresh namespaces can strand old producer CWD/RETR reconciliation even though files were preserved. Retain the existing service and complete routing roster for historical deliveries. Do not stop a used service and substitute this bundle. Changing the producer destination may rescan originals and is not a duplicate-free workaround. Never run two listeners on the same ports.

Review `capacity.json`, all receiver and route files, `plan.json`, and `commands.json`. `COMPLETE.json` is the last-written manifest of a successfully prepared bundle, not a runtime qualification or proof of deployment. If creation fails, retain the directory for inspection. Do not rerun over it or infer success from partially generated files.

## 3. Verify and launch explicitly

`commands.json` contains argument arrays, not automatically executed shell scripts. Replace `python` with the exact qualified interpreter. Its `check` array validates the saved route and references without starting a listener. Its `serve` array explicitly starts the receiver on route 00, allowing admission to later predeclared generations while retaining old readback. Only run it after reviewing the intended address, ports, TLS trust, credential references, and directory permissions.

Start the qualified native analysis service separately according to its deployment instructions. Ensure the declared API matches that service. Neither this bundle nor its completion manifest proves that any service is running with these settings.

## 4. Initialize from a real published original

Wait for the first genuinely completed, published attempt in generation 00. Inspect its metadata and intended recorder identity. Append `--first-delivery /absolute/path/to/that/published/attempt` to the `initialize_prefix` array in `commands.json`. This is an explicit network operation that creates one analysis session. It must be done only against the intended API. Future runner/job/export paths are deliberately absent until the supported initializer and handoff create them.

Do not use raw or partially uploaded files, fabricate a first delivery, or manually write a session ID into the plan. If initialization reports an uncertain POST, stop and preserve its receipts; never retry creation blindly. Use the initializer's existing explicit recovery rules.

## 5. Run one bounded finite plan

Review then execute the `analyze` array in `commands.json`. It uses phase actions 16 and phase polls 192 rather than the default 64-poll backlog limit. Global action/poll/request/write/byte budgets are explicit; the default 120-second deadline is still bounded and may pause before a large backlog finishes. These arithmetic allowances do not predict inference duration or guarantee enough requests/response bytes for a real recording. Choose a supported explicit deadline only after considering the actual workload.

Inspect each result. A paused or unresolved result is not success and is not permission to loop automatically. Preserve all receipts and resolve the reported condition before an explicit later invocation. An exact completed repeat checks durable receipts and does not recreate sessions. Final ROTATION with no next entry is `plan_exhausted`, not recording completion. Early verified physical STOP exports the recording and reports unused entries.

Existing stored generations are not resized by this workflow. Use the read-only capacity preflight for stopped, locked inventories across every retained generation; a configuration-only calculation cannot establish the state of a live receiver or authorize edits to it.
