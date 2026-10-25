# Root-only proposed commands

Author executed no code/tests/SQL. Require independent frozen-source review and root's existing execution guard. Never resume an existing output. A missed commit-window pair is not grounds for unbounded replay.

From /workspace/scratch/12ec3121e93f, focused controls first (13 cases; synthetic parser data is not physical qualification):

    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="$PWD/audio-recovery-test-tools-20261023b:$PWD/audio-runtime-physical-gate-preparation-rev02-20261025" /opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python3.12 -B -m pytest -q -p no:cacheprovider --basetemp="$PWD/audio-runtime-physical-controls-rev02-20261025" audio-runtime-physical-gate-preparation-rev02-20261025/test_gate.py audio-runtime-physical-gate-preparation-rev02-20261025/test_full_database_mutation.py

Use root's standard finite test wrapper, not a new unowned background process. If controls fail, stop and diagnose before any physical gate.

After independent approval of gate.py SHA256 f6fce6bfff8fd011740890f554e023f95dca5d83461e5ae16b00304b8aaf46c1 and controls:

    env -u PYTHONPATH PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python3.12 -B audio-runtime-physical-gate-preparation-rev02-20261025/gate.py --output "$PWD/audio-runtime-physical-gate-run02-20261025" --pins "$PWD/audio-runtime-physical-gate-preparation-rev02-20261025/pins.json" --pins-sha256 c18fa0049e696913c6ed378aa825510b78dcace4f74ec7ff83c48424916a48e2 --manifest "$PWD/audio-runtime-physical-gate-preparation-rev02-20261025/product.sha256" --manifest-sha256 168d5af6727b27ae3a85127893c994f8533b659ac0c02bdc87908a65c475af3f --harness-sha256 f6fce6bfff8fd011740890f554e023f95dca5d83461e5ae16b00304b8aaf46c1

The supervisor implements four120s phase alarms,2CPU affinity,1GiB RLIMIT_AS, sampled256MiB parent+child RSS,192MiB artifact bound and≤5s cleanup. Root must preserve the original executor/process handle and positively wait it. Do not put an external kill timer at480s that could kill the supervisor during owned-child cleanup. If infrastructure loses the executor, classify outcome/cleanup UNKNOWN pending actual original-wait reconciliation; do not create another gate merely because no terminal arrived.

Output acceptance requires original root exit0, original child waits, terminal.json plus terminal-accounting.json, captured STOP/KILL acknowledgment, genuine journal audit, exact recovery/oracle and post-run independent review. COMPLETE_REVIEW_REQUIRED does not activate or authorize a product runtime profile update. Preserve all failed/missed-attempt evidence.
