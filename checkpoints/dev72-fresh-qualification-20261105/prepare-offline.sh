#!/bin/bash
set -euo pipefail
umask 077
B=/workspace/scratch/12ec3121e93f
V=$B/backend-dev72-fresh-runtime-20261105-0152
P=$B/backend-dev72-fresh-proof-20261105-0152
/usr/bin/python3.13 -I -B -m venv --without-pip "$V"
"$B/audio-recovery-runtime-20261023b/bin/python" -I -B -m pip --python "$V/bin/python" install --no-index --require-hashes --no-compile --only-binary=:all: --find-links "$B/backend-dynamic-proof-20261104/wheels" --find-links "$B/backend-operational-proof-20261104/receiver-wheels" -r "$P/Linux-py313-runtime-and-receiver.lock.txt" > "$P/dependency-install.log" 2>&1
"$B/audio-recovery-runtime-20261023b/bin/python" -I -B -m pip --python "$V/bin/python" check > "$P/pip-check.log" 2>&1
