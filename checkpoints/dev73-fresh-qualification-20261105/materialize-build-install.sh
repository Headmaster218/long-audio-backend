#!/bin/bash
set -euo pipefail
umask 077
B=/workspace/scratch/12ec3121e93f
S=$B/backend-dev73-fresh-source-20261105-0214
P=$B/backend-dev73-fresh-proof-20261105-0214
M=$B/backend-dev73-fresh-materialized-20261105-0214
V=$B/backend-dev73-fresh-runtime-20261105-0214
D=$B/audio-recovery-runtime-20261023b/bin/python
"$V/bin/python" -I -B "$S/qualification/materialize_target_candidate.py" --source "$S" --manifest "$P/SOURCE_MANIFEST.json" --basis "$P/REVIEW_CANDIDATE_BASIS.json" --basis-sha256 "$(sha256sum "$P/REVIEW_CANDIDATE_BASIS.json" | cut -d' ' -f1)" --target "$P/target.json" --audit "$P/cellstore-audit.json" --physical "$P/cellstore/RESULT.json" --candidate-tests "$P/candidate-tests-receipt.json" --output "$M" > "$P/materialization.stdout" 2> "$P/materialization.stderr"
mkdir "$P/wheel-input" "$P/product-wheel"
cp "$M/source/pyproject.toml" "$P/wheel-input/"
cp -a "$M/source/audio_backend" "$P/wheel-input/"
"$D" -I -B -m pip --python "$B/backend-dynamic-runtime-20261104/bin/python" wheel --no-index --no-deps --no-build-isolation --wheel-dir "$P/product-wheel" "$P/wheel-input" > "$P/build.log" 2>&1
"$V/bin/python" -I -B -c 'import hashlib,pathlib; p=pathlib.Path("/workspace/scratch/12ec3121e93f/backend-dev73-fresh-proof-20261105-0214"); w=list((p/"product-wheel").glob("*.whl")); assert len(w)==1 and w[0].name=="long_audio_backend-0.2.0rc3.dev73-py3-none-any.whl"; (p/"product-wheel.lock.txt").write_text(str(w[0])+" --hash=sha256:"+hashlib.sha256(w[0].read_bytes()).hexdigest()+"\n")'
"$D" -I -B -m pip --python "$V/bin/python" install --no-index --no-deps --require-hashes --no-compile -r "$P/product-wheel.lock.txt" > "$P/install.log" 2>&1
RECEIPT=$(sha256sum "$M/RECEIPT.json" | cut -d' ' -f1)
"$V/bin/python" -I -B "$S/qualification/verify_installed_target.py" --receipt "$M/RECEIPT.json" --receipt-sha256 "$RECEIPT" > "$P/installed-before.json"
"$D" -I -B -m pip --python "$V/bin/python" check > "$P/post-product-pip-check.log" 2>&1
