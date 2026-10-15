# Explicit configuration and reproduction boundary

These .py.in files are source templates, not ready-to-run programs. This document
specifies a future reviewed configuration; no configuration or harness execution
was performed for this checkpoint. The checkpoint MANIFEST.json is deliberately
not a runtime manifest. Never copy it into a runtime packet as if it were one.

## Source placeholder contract

Each marker occurs inside a quoted Python string literal. Replace the whole
quoted placeholder literal with a correctly escaped Python string literal for
the intended value, using an exact substitution. Do not use shell expansion or
interpret configuration values as Python statements. All relative paths must be
normalized, remain within the stated root, and avoid symlink escapes.

- @@WORKSPACE_ROOT@@: absolute root counted by the workspace disk guard.
- @@ACCEPTANCE_PACKET_REL@@: acceptance runtime packet directory, relative to
  the workspace; it contains acceptance.py, README.md, and its own MANIFEST.json.
- @@REAL30_PACKET_REL@@: separate real30 runtime packet directory, relative to
  the workspace; it contains real30.py, README.md, and its own MANIFEST.json.
- @@PRODUCT_SOURCE_REL@@: product source tree relative to the workspace. It must
  contain the reviewed audio_backend package and match its aggregate source pin.
- @@RUNTIME_PYTHON_REL@@: real Python 3.12 executable relative to the workspace.
  The harness expects its installed audio_backend package under the interpreter's
  parent-parent lib/python3.12/site-packages directory. Do not substitute a
  symlink interpreter or assume an arbitrary system Python is equivalent.
- @@ASSET_ROOT_REL@@: asset directory relative to the workspace. The ordinary
  product's model_dir remains its models subdirectory.
- @@DEMO_AUDIO_REL@@: demo WAV path relative to the asset root.
- @@AISHELL_AUDIO_REL@@ and @@AMI_AUDIO_REL@@: WAV paths relative to the workspace.
- @@NATIVE_MODEL_REL@@, @@NATIVE_LIBRARY_REL@@, and @@MOSS_MODEL_REL@@: file paths
  relative to the asset root. The model/library roles and profile must stay
  unchanged if comparing with the historical test.
- @@SUPERVISOR_SOURCE_SHA256@@: SHA-256 of the configured acceptance.py bytes,
  not the hash of acceptance.py.in and not automatically the historical hash.

Both templates must use the same workspace, acceptance packet, and product
source bindings. Their operational relative names such as data/tmp, run leaves,
Python package paths, and server-clean marker patterns are technical mechanics
and have been retained. The templates deliberately preserve dynamic process
identity tracking and original-parent wait semantics, not any captured process
identity from the recorded runs.

## Assembly order and pin dependencies

1. Review the intended product/runtime/assets and all resource limits. Use two
   new runtime packet directories. Do not overwrite existing run evidence.
2. Substitute the acceptance template and save it as acceptance.py in the
   acceptance packet. Write a short packet README describing that configuration
   and its unexecuted status. Hash the exact bytes of both files.
3. Create that packet's MANIFEST.json using the required field structure below.
   Resolve integer placeholders to JSON numbers. Verify every historical pin
   against the selected local environment; a changed value requires a new
   reviewed qualification, not silent relabeling of the old result.
4. Hash the resulting acceptance MANIFEST.json. Substitute real30.py.in,
   including the configured acceptance.py hash in @@SUPERVISOR_SOURCE_SHA256@@,
   and save it as real30.py in its separate packet. Write its README, then hash
   those exact bytes.
5. Create the real30 runtime MANIFEST.json below, including the configured
   acceptance manifest hash. All inputs_and_profile and models paths are relative
   to the workspace, and must agree with the source bindings. Retain all model
   rows, including the MOSS artifact required by the selected profile; this does
   not authorize or schedule a MOSS job.
6. Recheck exact runtime inventories, byte caps, all hashes, safe path bindings,
   fresh run leaves, local ports, resource reservation, and environment equality.
   Merely performing these configuration steps is not an execution result.

The aggregate product pin hashes, in sorted relative-path order, one UTF-8 JSON
record per Python file: [relative_name, byte_count, file_sha256], compact separators
(',', ':'), ensure_ascii=True, and a final newline per record. The acceptance
inventory is relative to the product source root and includes audio_backend/ in
names. The installed-child inventory is relative to the installed audio_backend
package itself. Counts and total bytes must also match; these two inventories
are intentionally different.

## Acceptance runtime manifest fields

This is a schema example with historical binary/product pins. It is not a
configured manifest. The 'files' list is exactly acceptance.py and README.md;
it does not hash itself. Use SHA-256 of exact bytes, not normalized text.

```json
{
  "ffmpeg": {
    "path": "@@FFMPEG_ABSOLUTE_PATH@@",
    "sha256": "e8a8d46f5225f3062cec7c07fb145d58ae73c603cb740dcd5bad34bfb54e455a"
  },
  "ffprobe": {
    "path": "@@FFPROBE_ABSOLUTE_PATH@@",
    "sha256": "cefc5f81c0b18429cdbf8fa5a89e1e9c759235e10f93f9b329ff1b2fdff26f46"
  },
  "files": [
    {
      "bytes": "@@CONFIGURED_ACCEPTANCE_BYTES_INTEGER@@",
      "path": "acceptance.py",
      "sha256": "@@CONFIGURED_ACCEPTANCE_SHA256@@"
    },
    {
      "bytes": "@@ACCEPTANCE_README_BYTES_INTEGER@@",
      "path": "README.md",
      "sha256": "@@ACCEPTANCE_README_SHA256@@"
    }
  ],
  "product_python": {
    "bytes": 1671402,
    "files": 99,
    "sha256": "06a6b81a8f2e8c31e98a04b52601aa03cce1ec69d40f20d0eb189f3bb140bee7"
  },
  "python_sha256": "fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7"
}
```

Caps enforced by acceptance.py: acceptance.py <=49,152 bytes; README.md and
MANIFEST.json <=8,192 bytes each; aggregate <=65,536 bytes. The manifest verifies
installed guard.py and __init__.py against the chosen product source and pins
the interpreter and resolved FFmpeg/ffprobe binaries. Different binaries or
source inventories require explicit review and separate results.

## real30 runtime manifest fields

This example retains the reviewed historical file sizes and digests as
reference fingerprints. It supplies no recordings, models, native library, or
profile-command file. Supply the exact local artifacts or establish a separately
reviewed test variant. The source's input geometry assertions are fixed: mono
PCM16, 16 kHz, and 1,127,530 / 721,600 / 336,000 frames for the three recordings.

```json
{
  "files": [
    {
      "bytes": "@@CONFIGURED_REAL30_BYTES_INTEGER@@",
      "path": "real30.py",
      "sha256": "@@CONFIGURED_REAL30_SHA256@@"
    },
    {
      "bytes": "@@REAL30_README_BYTES_INTEGER@@",
      "path": "README.md",
      "sha256": "@@REAL30_README_SHA256@@"
    }
  ],
  "inputs_and_profile": [
    {
      "bytes": 2261722,
      "path": "@@ASSET_ROOT_REL@@/@@DEMO_AUDIO_REL@@",
      "sha256": "a7431f0169ef76ef630c945a1d2c3675d8c8c2df2ae4a6b16f8a88ba1bccfbbb"
    },
    {
      "bytes": 1443278,
      "path": "@@AISHELL_AUDIO_REL@@",
      "sha256": "9024dee578b4f825a4465e456d05544827cf0c6afdabf2712249cd484f7f1cc7"
    },
    {
      "bytes": 672044,
      "path": "@@AMI_AUDIO_REL@@",
      "sha256": "0263b786f1375c96c121e52e48d79f4c6adcddb10b609b8bb67d5b35bd27d606"
    },
    {
      "bytes": 1264,
      "path": "@@PROFILE_COMMAND_REL@@",
      "sha256": "951f35aacd826ace8284bab946514f08abbbd7dc03e93244e222c23367a0e43c"
    },
    {
      "bytes": 2060,
      "path": "@@CALIBRATION_PROFILE_REL@@",
      "sha256": "670388018d7af578bc54405a27287422d54d0bd98a181f8c366c13f6d80da590"
    }
  ],
  "installed_python": {
    "bytes": 1608951,
    "files": 98,
    "sha256": "51ceddd67a339543a47ddb0f1bc87890f14afd638b76f85e6af992f114cf4bd6"
  },
  "models": [
    {
      "bytes": 986899616,
      "path": "@@ASSET_ROOT_REL@@/@@MOSS_MODEL_REL@@",
      "sha256": "64ec654dc6ffcfdfe180422dffce1d33422b0c30959b7edfd131bad77ee35039"
    },
    {
      "bytes": 107012128,
      "path": "@@ASSET_ROOT_REL@@/@@NATIVE_MODEL_REL@@",
      "sha256": "08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1"
    },
    {
      "bytes": 239233841,
      "path": "@@ASSET_ROOT_REL@@/models/sensevoice/model.int8.onnx",
      "sha256": "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"
    },
    {
      "bytes": 315894,
      "path": "@@ASSET_ROOT_REL@@/models/sensevoice/tokens.txt",
      "sha256": "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"
    },
    {
      "bytes": 39593761,
      "path": "@@ASSET_ROOT_REL@@/models/speaker.onnx",
      "sha256": "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b"
    },
    {
      "bytes": 91932865,
      "path": "@@ASSET_ROOT_REL@@/models/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx",
      "sha256": "e61bdfaab92b48bc51209c0da91621fb8d89d1564bb5bb38be6e4a4380c1ed34"
    },
    {
      "bytes": 14675,
      "path": "@@ASSET_ROOT_REL@@/models/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv",
      "sha256": "cdd1049833c4b86127c2773ac0d14a2754b6a6d0d1798002ed5c66e699708429"
    },
    {
      "bytes": 68208,
      "path": "@@ASSET_ROOT_REL@@/@@NATIVE_LIBRARY_REL@@",
      "sha256": "b16719ac6c8614e43a9bed224b21dd17b9bfb59b5215cc7fe29482712090dc52"
    }
  ],
  "supervisor_manifest_sha256": "@@CONFIGURED_ACCEPTANCE_MANIFEST_SHA256@@"
}
```

@@PROFILE_COMMAND_REL@@ is the reviewed profile-description JSON path;
@@CALIBRATION_PROFILE_REL@@ is the calibration JSON path. They are checked as
files, not executed commands. The source profile uses CPU SenseVoice, native
Nemotron q8_0, two threads, automatic language, 30-second windows, two-second
configured context, event threshold 0.25, and calibration eres2net-ami20-v1.
The existing literal decode boundary owns 28-second cores with up to one second
context on each side, at most 30 seconds per decode. Overlap backend remains
none. Native support/cells/seam cells and MOSS job ownership are enabled for
profile parity; accepted MOSS jobs must remain zero.

Caps enforced by real30.py: real30.py <=36,864 bytes; README.md <=11,264 bytes;
MANIFEST.json <=8,192 bytes; all three together <=49,152 bytes. The examples above
are field requirements, not permission to exceed aggregate caps.

## Reviewed execution shape, not an instruction to run now

Only after separately reviewing a completed configuration, the launch shape is:

    /usr/bin/prlimit --as=8589934592 -- <PYTHON> -I -B <ACCEPTANCE_PACKET>/acceptance.py --mode focused --port <LOCAL_PORT>

The same helper accepts --mode prefix and --mode logical. Prefix/focused have
300 seconds of work plus 120 seconds cleanup. Logical has 1,800 plus 120 seconds.
Review prefix outcome and resource reservation before a full logical run.

The real30 forms are:

    /usr/bin/prlimit --as=8589934592 -- <PYTHON> -I -B <REAL30_PACKET>/real30.py --check
    /usr/bin/prlimit --as=8589934592 -- <PYTHON> -I -B <REAL30_PACKET>/real30.py --run --port <LOCAL_PORT>

These angle-bracket fields are explanatory, not shell-ready values. --check has
its own 30-second alarm and is model-free; it still verifies source/runtime
packets and file geometry. --run uses actual monotonic pacing and separately
bounded drain/cleanup. No mode permits a shortened clock to be called a paced
success. The helpers require Linux, CPUs 0-1, nice 19, the inherited address-space
limit, and the documented memory/disk/ownership checks. No product modification,
installation, download, existing evidence deletion, or arbitrary external command
is part of this checkpoint. No new execution is authorized by these examples.

## Additional unconfigured 12-hour packet

real12h.py.in uses the same existing source/path markers and one new binding:
@@REAL12H_PACKET_REL@@, a fresh runtime packet directory relative to the
workspace. It contains real12h.py, README.md, and a dedicated MANIFEST.json.
Its run leaf is real12h-run. All earlier templates and the accepted local-original
results remain unchanged. The local original passed its model-free check without model inference or actual
pacing; its 12-hour run result is pending. This is configuration information only.

Create this packet only after the acceptance and real30 runtime packets have
been configured and reviewed. The acceptance.py hash goes into
@@SUPERVISOR_SOURCE_SHA256@@, exactly as for real30. Build the new manifest from
the real30 required fields described above, carrying forward installed_python,
inputs_and_profile, models, and supervisor_manifest_sha256 with independently
verified values. Replace its files list and add accepted_real30_source with
these field shapes (integer placeholders must become JSON numbers):

    {
      "accepted_real30_source": [
        {
          "bytes": "@@CONFIGURED_REAL30_BYTES_INTEGER@@",
          "path": "@@REAL30_PACKET_REL@@/real30.py",
          "sha256": "@@CONFIGURED_REAL30_SHA256@@"
        },
        {
          "bytes": "@@REAL30_README_BYTES_INTEGER@@",
          "path": "@@REAL30_PACKET_REL@@/README.md",
          "sha256": "@@REAL30_README_SHA256@@"
        },
        {
          "bytes": "@@CONFIGURED_REAL30_MANIFEST_BYTES_INTEGER@@",
          "path": "@@REAL30_PACKET_REL@@/MANIFEST.json",
          "sha256": "@@CONFIGURED_REAL30_MANIFEST_SHA256@@"
        }
      ],
      "files": [
        {
          "bytes": "@@CONFIGURED_REAL12H_BYTES_INTEGER@@",
          "path": "real12h.py",
          "sha256": "@@CONFIGURED_REAL12H_SHA256@@"
        },
        {
          "bytes": "@@REAL12H_README_BYTES_INTEGER@@",
          "path": "README.md",
          "sha256": "@@REAL12H_README_SHA256@@"
        }
      ]
    }

This is a partial manifest field example, not a runnable manifest. The required
shared fields listed above must also be present. The ancestor entries pin exact
configured files, not the public .in files. A new configuration's ancestry pin
does not inherit the local original's accepted outcome. Runtime real12h caps
are code <=49,152 bytes, README and MANIFEST <=8,192 bytes each, aggregate
<=65,536 bytes. The original source/README/manifest fingerprints are recorded in
the checkpoint manifest. Do not substitute the public checkpoint manifest for
any runtime manifest.

The fixed schedule has 691,200,000 frames, 732 receipts, and four final scopes.
The real recordings retain 7/5/2 receipts. Silence has 717 receipts of 960,000
frames plus one of 694,870 frames, totaling 718 silence receipts. PCM16 payload
is 1,382,400,000 bytes, plus 32,208 WAV-header bytes. One transient client WAV and
at most four unfinished receipts remain the input bounds.

The restart is scheduled at 21,620 seconds between admissions due at
21,616.570625 and 21,676.570625 seconds. It validates the latest observed
committed silence checkpoint/native frontier, not an assumed epoch. Its deadline
is next due +1 second, after which the original pacing/drain deadline is restored.
It remains an idle graceful open-stream restart, not an in-flight native crash.

Dense RSS/disk guards and all operational resource thresholds remain unchanged.
Only persisted resource cadence changes to 120 seconds, with a 2 MiB ledger.
Numeric evidence is bounded to 16 MiB and 65,536 rows, each <=1,024 bytes, plus
an eight-byte shared row count and at most eight live call pairs. Optional
diagnostic additions are capped at 4,096 per server; only fixed stages, exact
Unavailable instances, a sole ordinary string matching [a-z_]{1,80}, and sanitized
SQL category fields can enter them. Other reasons become unclassified. No raw
exception messages or captured identity data are added. Original diagnostics
are invoked exactly once, and their returns/exceptions are preserved.

The new helper's --check and --run forms mirror real30, using the configured
real12h.py and packet. This source preparation neither invokes them nor supplies
permission to execute them. The source check has its own 30-second bound; planned
phase bounds are startup 90, pacing 43,200, drain 300, cleanup 120 seconds. No
completed 12-hour claim should appear until terminal evidence and cleanup exist.
