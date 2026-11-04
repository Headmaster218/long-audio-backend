#!/usr/bin/env python3
"""Export only research code, aggregate metrics, and path-redacted integrity pins."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--diagnosis", type=Path, required=True)
    p.add_argument("--pilot", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    src, pilot, out = args.diagnosis.resolve(), args.pilot.resolve(), args.out.resolve()
    if out == src or out == pilot or pilot in out.parents:
        raise ValueError("Use a separate public export directory")
    out.mkdir(parents=True, exist_ok=True)
    result = json.loads((src / "DIAGNOSTIC_RESULTS.json").read_text())
    frozen = json.loads((src / "FROZEN_PILOT_BEFORE_AFTER.json").read_text())
    if not frozen["unchanged"]:
        raise ValueError("Pilot integrity failed")
    public_pins = []
    for raw_path, digest in sorted(frozen["files"].items()):
        path = Path(raw_path)
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != after:
            raise ValueError("Pilot changed since diagnostic check")
        public_pins.append({"pilot_relative_path": str(path.relative_to(pilot)),
                            "before_sha256": digest, "after_sha256": after})
    write(out / "FROZEN_PILOT_INTEGRITY.json", {
        "unchanged": True, "files_count": len(public_pins),
        "scope": "All frozen pilot files, including existing Python bytecode; no source/crop/model bytes included.",
        "pins": public_pins})
    write(out / "DIAGNOSTIC_AGGREGATES.json", {key: result[key] for key in (
        "status", "boundary", "versions", "method", "frozen_candidate_counts",
        "integrity", "group_summaries", "person_consistency")})
    for name in ("diagnose_frozen.py", "test_diagnose_frozen.py", "export_public.py", "VALIDATION.json"):
        shutil.copyfile(src / name, out / name)
    report = (src / "DIAGNOSTIC_REPORT.md").read_text()
    marker = "## 可复查产物\n"
    if report.count(marker) != 1:
        raise ValueError("Report artifact section changed; review public export wording")
    public_artifacts = """## 可复查产物

本公开目录的 `DIAGNOSTIC_AGGREGATES.json` 提供方法、原冻结统计、描述性分组摘要与同人几何摘要；`FROZEN_PILOT_INTEGRITY.json` 提供原 pilot 全部 89 个文件的前后哈希，使用相对路径，不含本地工作区绝对路径。`PUBLIC_EXPORT_MANIFEST.json` 记录本次公开导出的文件及 SHA256。

`diagnose_frozen.py` 可使用本地保留的原输入重建完整诊断；`test_diagnose_frozen.py` 用合成数据验证波形计数、归一化和 WAV 读取。`VALIDATION.json` 记录五项合成测试与五个本地输出重复生成逐字节一致性。完整 `DIAGNOSTIC_RESULTS.json`、`signal_metrics.csv`、`frozen_query_diagnostics.csv` 和 `within_person_cosines.csv` 仅保留在本地，未包含在公开备份中。未复制原音频、原向量、人口属性或模型权重。
"""
    (out / "DIAGNOSTIC_REPORT.md").write_text(report.split(marker)[0] + public_artifacts)
    (out / "README.md").write_text("""# Frozen pilot diagnosis: public research backup

This directory contains only research code, synthetic tests, a report, aggregate diagnostic metrics, and path-redacted integrity hashes. It contains no audio, embeddings, weights, transcripts, demographic attributes, per-input waveform metrics, or private user data. Public corpus speaker IDs occur in the report and aggregate geometry.

The original T decisions are unchanged. No new T predictions, threshold search, quality filtering, model calls, source edits, or acoustic-purity claims were made. Detailed per-input diagnostics and full local input paths remain outside this public directory.

Run from a workspace that already has the retained original pilot and original acquisition files (no download/installation is performed):

```sh
python -B test_diagnose_frozen.py
python -B diagnose_frozen.py --pilot /path/to/original/pilot --out /path/to/local/diagnosis
python -B export_public.py --diagnosis /path/to/local/diagnosis --pilot /path/to/original/pilot --out /path/to/new/public/export
```

For a new export, keep this report, the code, and the validation receipt beside the local generated diagnostics. Absolute original input paths come from the frozen manifests and receipt and must still resolve locally. The original corpus/model files are deliberately not part of this research backup. `VALIDATION.json` records five passed synthetic tests and byte-identical independent reruns of the five local diagnostic data files. `FROZEN_PILOT_INTEGRITY.json` contains 89 before/after hashes without absolute workspace paths. The diagnostic implementation does not import the original runner or encoder.
""")
    names = sorted(x.name for x in out.iterdir() if x.is_file() and x.name != "PUBLIC_EXPORT_MANIFEST.json")
    write(out / "PUBLIC_EXPORT_MANIFEST.json", {
        "scope": "research_code_tests_report_aggregate_metrics_redacted_hashes_only",
        "raw_audio_included": False, "embeddings_included": False,
        "demographic_attributes_included": False, "per_input_signal_metrics_included": False,
        "files": [{"name": n, "bytes": (out/n).stat().st_size,
                   "sha256": hashlib.sha256((out/n).read_bytes()).hexdigest()} for n in names]})
    print(json.dumps({"status": "public_export_ready", "files": len(names)+1,
                      "frozen_pilot_files_rechecked": len(public_pins), "out": str(out)}, indent=2))


if __name__ == "__main__":
    main()
