# Frozen pilot diagnosis: public research backup

This directory contains only research code, synthetic tests, a report, aggregate diagnostic metrics, and path-redacted integrity hashes. It contains no audio, embeddings, weights, transcripts, demographic attributes, per-input waveform metrics, or private user data. Public corpus speaker IDs occur in the report and aggregate geometry.

The original T decisions are unchanged. No new T predictions, threshold search, quality filtering, model calls, source edits, or acoustic-purity claims were made. Detailed per-input diagnostics and full local input paths remain outside this public directory.

Run from a workspace that already has the retained original pilot and original acquisition files (no download/installation is performed):

```sh
python -B test_diagnose_frozen.py
python -B diagnose_frozen.py --pilot /path/to/original/pilot --out /path/to/local/diagnosis
python -B export_public.py --diagnosis /path/to/local/diagnosis --pilot /path/to/original/pilot --out /path/to/new/public/export
```

For a new export, keep this report, the code, and the validation receipt beside the local generated diagnostics. Absolute original input paths come from the frozen manifests and receipt and must still resolve locally. The original corpus/model files are deliberately not part of this research backup. `VALIDATION.json` records five passed synthetic tests and byte-identical independent reruns of the five local diagnostic data files. `FROZEN_PILOT_INTEGRITY.json` contains 89 before/after hashes without absolute workspace paths. The diagnostic implementation does not import the original runner or encoder.
