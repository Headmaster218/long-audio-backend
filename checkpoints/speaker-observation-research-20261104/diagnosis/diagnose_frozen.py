#!/usr/bin/env python3
"""Read-only, post-hoc diagnostics for the frozen SMIIP-TV pilot.

No encoder, model, VAD, resampling, threshold search, identity aggregation,
listening, downloads, source writes, or changed predictions. Only NumPy and
the Python standard library are used. Outputs are newly generated diagnostics.
All sample/group membership comes from the frozen inputs, not quality filters.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import platform
import wave

import numpy as np


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Inputs:
    def __init__(self):
        self.pins = {}

    def read(self, path, expected=None):
        path = Path(path).resolve()
        data = path.read_bytes()
        pin = {"path": str(path), "bytes": len(data), "sha256": sha(data)}
        if expected:
            require(pin["sha256"] == expected["sha256"], f"hash mismatch: {path}")
            if "bytes" in expected:
                require(pin["bytes"] == expected["bytes"], f"length mismatch: {path}")
        if str(path) in self.pins:
            require(self.pins[str(path)] == pin, f"input changed during read: {path}")
        self.pins[str(path)] = pin
        return data

    def json(self, path, expected=None):
        return json.loads(self.read(path, expected))

    def verify_unchanged(self):
        for pin in list(self.pins.values()):
            self.read(pin["path"], pin)


def pcm(data):
    with wave.open(io.BytesIO(data), "rb") as w:
        require(w.getsampwidth() == 2 and w.getframerate() == 16000,
                "Expected original PCM16 16 kHz WAV")
        require(w.getcomptype() == "NONE", "Compressed WAV unexpected")
        frames, channels = w.getnframes(), w.getnchannels()
        samples = np.frombuffer(w.readframes(frames), dtype="<i2")
        require(len(samples) == frames * channels, "Truncated WAV")
        return samples.reshape(frames, channels).copy()


def db(value):
    # None, not an arbitrary floor, denotes exact digital silence.
    return 20.0 * math.log10(value) if value > 0 else None


def signal_metrics(pcm16, rate=16000):
    """Metrics are descriptive proxies, never speech/quality/identity labels."""
    x = pcm16.astype(np.float64) / 32768.0
    frame_n = rate // 50  # fixed non-overlapping 20 ms frames
    complete = x[:len(x) // frame_n * frame_n].reshape(-1, frame_n)
    frame_rms = np.sqrt(np.mean(complete * complete, axis=1))
    rms = float(np.sqrt(np.mean(x * x)))
    peak = float(np.max(np.abs(x)))
    p95 = float(np.quantile(frame_rms, 0.95))
    relative_cut = p95 * 10.0 ** (-30.0 / 20.0)
    return {
        "samples": len(x), "duration_seconds": len(x) / rate,
        "rms_dbfs": db(rms), "peak_dbfs": db(peak),
        "dc_offset_fs": float(np.mean(x)),
        "crest_db": db(peak / rms) if rms > 0 else None,
        "digital_zero_fraction": float(np.mean(pcm16 == 0)),
        "endpoint_sample_count": int(np.sum((pcm16 == -32768) | (pcm16 == 32767))),
        "near_full_scale_fraction": float(np.mean(np.abs(x) >= 0.99)),
        "complete_20ms_frames": len(frame_rms),
        "excluded_tail_samples_for_frames": len(x) % frame_n,
        "low_energy_fraction_lt_minus50dbfs": float(np.mean(frame_rms < 10 ** (-50 / 20))),
        "low_energy_fraction_lt_p95_minus30db": float(np.mean(frame_rms < relative_cut)),
        "frame_rms_p05_dbfs": db(float(np.quantile(frame_rms, 0.05))),
        "frame_rms_p50_dbfs": db(float(np.quantile(frame_rms, 0.50))),
        "frame_rms_p95_dbfs": db(p95),
    }


def norm64(x):
    # Matches the existing saved-vector scorer's float64 normalization exactly.
    x = [float(y) for y in x]
    require(len(x) == 512 and all(math.isfinite(v) for v in x), "invalid vector")
    scale = max(abs(v) for v in x)
    require(scale > 1e-30, "zero vector")
    n = math.sqrt(math.fsum((v / scale) ** 2 for v in x))
    return [(v / scale) / n for v in x]


def cosine(a, b):
    # Existing one-reference matcher renormalizes its one-item centroid.
    a, b = norm64(norm64(a)), norm64(b)
    return max(-1.0, min(1.0, math.fsum(x * y for x, y in zip(a, b))))


def summary(values):
    require(values and all(v is not None for v in values), "empty/non-finite summary")
    return {"n": len(values), "min": min(values), "median": float(np.median(values)),
            "mean": float(np.mean(values)), "max": max(values)}


def write_json(path, data):
    path.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n")


def write_csv(path, rows):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    pilot, out = args.pilot.resolve(), args.out.resolve()
    require(pilot != out and pilot not in out.parents,
            "Diagnostics must be outside the frozen pilot")
    out.mkdir(parents=True, exist_ok=True)
    inputs = Inputs()
    # Pin all frozen pilot files before work, even those not interpreted here.
    frozen_tree = {str(p): sha(p.read_bytes()) for p in sorted(pilot.rglob("*")) if p.is_file()}
    protocol = inputs.json(pilot / "inputs-resource-reduced-v1/protocol.json")
    acquisition = inputs.json(protocol["acquisition_receipt"]["path"], protocol["acquisition_receipt"])
    sources = {s["slot_id"]: s for s in acquisition["selected"]}
    roles = inputs.json(pilot / "inputs-resource-reduced-v1/source_roles.json")
    require("records" in roles, "source roles schema changed")
    role_map = {r["id"]: r for r in roles["records"]}
    seal = inputs.json(pilot / "T_PREDICTION_SEAL.json")
    inputs.read(pilot / "RESULT_REPORT.md")
    lock = inputs.json(pilot / "CANDIDATE_LOCK.json")
    require(lock["threshold"] == 0.46 and lock["margin"] == 0.10, "unexpected frozen rule")
    sample_rows, query_rows, pair_rows, person_rows = [], [], [], []
    counts, saved_score_errors = {}, []
    for split in ("C", "T"):
        manifest_path = pilot / f"inputs-resource-reduced-v1/{split}_MANIFEST.json"
        manifest = inputs.json(manifest_path, seal["T_manifest"] if split == "T" else None)
        predictor = inputs.json(pilot / f"inputs-resource-reduced-v1/{split}_all_predictor.json")
        truth = inputs.json(pilot / f"inputs-resource-reduced-v1/{split}_all_truth.json")
        result = inputs.json(pilot / f"{split}-run-01/native/result.json",
                             seal["T_artifacts"]["result.json"] if split == "T" else None)
        vector_data = inputs.read(result["vectors"]["path"], result["vectors"])
        with np.load(io.BytesIO(vector_data), allow_pickle=False) as v:
            require(bool(np.all(v["valid"])), "saved vector unavailable")
            vectors = {str(k): np.array(x) for k, x in zip(v["ids"], v["normalized"])}
            require(len(vectors) == len(manifest["samples"]) == 31, "unexpected clip count")
            clips = {c["id"]: c for c in result["clips"]}
            for i, x in vectors.items():
                require(sha(x.tobytes()) == clips[i]["normalized_sha256"], "normalized vector hash mismatch")
        report = inputs.json(pilot / ("C_SELECTION.json" if split == "C" else "T_REPORT.json"))
        saved = report["descriptive_reports"]["all_three_queries"]["candidate"]
        outcomes = {r["query_id"]: r for r in saved["outcomes"]}
        counts[split] = saved["counts"]
        sealed_scores = {r["query_id"]: r for r in seal["all_three_queries"]["rows"]} if split == "T" else {}
        for sample in manifest["samples"]:
            sid, source = sample["id"], sources[sample["id"]]
            crop_data = inputs.read(sample["wav"]["path"], sample["wav"])
            source_data = inputs.read(source["source_local_path"], {"sha256": source["source_wav_sha256"]})
            crop, original = pcm(crop_data), pcm(source_data)
            require(crop.shape == (32000, 1), "crop format mismatch")
            require(original.shape[1] == 2 and original.shape[0] == source["source_frames"], "source format mismatch")
            start = (len(original) - 32000) // 2
            require(start == source["crop_start_sample"], "crop offset mismatch")
            require(np.array_equal(original[start:start+32000, 0], crop[:, 0]), "central channel-0 crop mismatch")
            require(sha(crop[:, 0].tobytes()) == sample["pcm_sha256"], "crop PCM mismatch")
            crop, channel0 = crop[:, 0], original[:, 0]
            crop_metrics, source_metrics = signal_metrics(crop), signal_metrics(channel0)
            outside = np.concatenate([channel0[:start], channel0[start+32000:]])
            outside_rms = float(np.sqrt(np.mean((outside.astype(float) / 32768) ** 2))) if len(outside) else 0.0
            energy_crop, energy_source = float(np.sum(crop.astype(float)**2)), float(np.sum(channel0.astype(float)**2))
            role = role_map[sid]
            sample_rows.append({
                "id": sid, "split": split, "speaker_id": role["speaker_id"],
                "role": role["role"], "roster_role": role["roster_role"],
                "recording_date": role["recording_date"],
                "crop_exact_central_channel0": True,
                "source_channels": 2, "source_duration_seconds": len(channel0)/16000,
                "crop_start_seconds": start/16000,
                "crop_fraction_of_source_duration": len(crop)/len(channel0),
                "crop_fraction_of_source_energy": energy_crop/energy_source if energy_source else None,
                "outside_crop_rms_dbfs": db(outside_rms),
                **{"crop_"+k: v for k,v in crop_metrics.items()},
                **{"source_"+k: v for k,v in source_metrics.items()},
            })
        local_samples = {r["id"]: r for r in sample_rows if r["split"] == split}
        for qid in predictor["queries"]:
            qtruth, outcome = truth[qid], outcomes[qid]
            scores = {g["person_id"]: cosine(vectors[g["sample_id"]], vectors[qid]) for g in predictor["gallery"]}
            if split == "T":
                for ss in sealed_scores[qid]["scores"]:
                    delta = abs(ss["score"] - scores[ss["person_id"]])
                    require(delta < 1e-14, "saved-score mismatch")
                    saved_score_errors.append(delta)
            ordered = sorted(scores, key=lambda k: (-scores[k], k))
            require(ordered[0] == outcome["winner"] and abs(scores[ordered[0]] - outcome["score"]) < 1e-14,
                    "saved outcome/score mismatch")
            metrics = local_samples[qid]
            query_rows.append({"query_id": qid, "split": split, "kind": qtruth["kind"],
                "speaker_id": qtruth["speaker_id"], "person_id": qtruth["person_id"],
                "role": role_map[qid]["role"], "saved_outcome": outcome["outcome"],
                "saved_reason": outcome["reason"], "saved_winner": outcome["winner"],
                "saved_top_score": outcome["score"], "saved_margin": outcome["margin"],
                "true_reference_cosine": scores.get(qtruth["person_id"]),
                "true_reference_rank": ordered.index(qtruth["person_id"])+1 if qtruth["person_id"] else None,
                "crop_rms_dbfs": metrics["crop_rms_dbfs"],
                "crop_low_energy_fraction_lt_minus50dbfs": metrics["crop_low_energy_fraction_lt_minus50dbfs"],
                "crop_low_energy_fraction_lt_p95_minus30db": metrics["crop_low_energy_fraction_lt_p95_minus30db"],
                "crop_endpoint_sample_count": metrics["crop_endpoint_sample_count"],
                "source_duration_seconds": metrics["source_duration_seconds"],
                "crop_fraction_of_source_energy": metrics["crop_fraction_of_source_energy"],
                "crop_fraction_of_source_duration": metrics["crop_fraction_of_source_duration"],
            })
        speaker_ids = sorted({local_samples[i]["speaker_id"] for i in vectors})
        for speaker in speaker_ids:
            ids = sorted(i for i in vectors if local_samples[i]["speaker_id"] == speaker)
            qq, rq = [], []
            for a,b in itertools.combinations(ids, 2):
                pair_kind = "reference_query" if any(role_map[x]["role"] == "reference" for x in (a,b)) else "query_query"
                score = cosine(vectors[a], vectors[b])
                (rq if pair_kind == "reference_query" else qq).append(score)
                pair_rows.append({"split": split, "speaker_id": speaker, "sample_a": a,
                                  "sample_b": b, "pair_kind": pair_kind, "cosine": score})
            person_rows.append({"split": split, "speaker_id": speaker,
                "roster_role": local_samples[ids[0]]["roster_role"], "sample_count": len(ids),
                "reference_query": summary(rq) if rq else None,
                "query_query": summary(qq)})
    group_summaries = {}
    for split in ("C", "T"):
        for outcome in ("known_correct", "known_refused", "unknown_refused"):
            group = [r for r in query_rows if r["split"] == split and r["saved_outcome"] == outcome]
            group_summaries[f"{split}_{outcome}"] = {k: summary([r[k] for r in group]) for k in
                ("crop_rms_dbfs", "crop_low_energy_fraction_lt_minus50dbfs",
                 "crop_low_energy_fraction_lt_p95_minus30db", "source_duration_seconds",
                 "crop_fraction_of_source_energy", "crop_fraction_of_source_duration")}
    inputs.verify_unchanged()
    require({str(p): sha(p.read_bytes()) for p in sorted(pilot.rglob("*")) if p.is_file()} == frozen_tree,
            "Frozen pilot tree changed")
    payload = {
        "status": "complete_read_only_posthoc_diagnostics",
        "boundary": "No model runs, new identity predictions, tuning, filtering, source edits, or purity claims.",
        "versions": {"python": platform.python_version(), "numpy": np.__version__},
        "method": {"signal": "PCM16/32768; channel 0; 20ms nonoverlapping complete frames; source tail excluded only for frame metrics",
            "low_energy_absolute": "frame RMS < -50 dBFS, a descriptive amplitude proxy, not VAD",
            "low_energy_relative": "frame RMS < within-clip 95th percentile RMS minus 30 dB, not VAD",
            "clipping": "counts PCM endpoints -32768/+32767; also |PCM/32768| >= .99 fraction; neither proves absence of prior clipping",
            "cosine": "float64 normalization of saved float32 normalized vectors; same existing one-reference math; no aggregation",
            "statistics": "post-hoc descriptive only; observations within a speaker are dependent; no tests, CI, quality gate, or candidate selection"},
        "frozen_candidate_counts": counts,
        "integrity": {"frozen_pilot_files_unchanged": len(frozen_tree),
            "input_files_pinned_and_rechecked": len(inputs.pins),
            "central_crops_verified": len(sample_rows),
            "sealed_T_reference_query_scores_verified": len(saved_score_errors),
            "maximum_T_saved_score_absolute_error": max(saved_score_errors)},
        "group_summaries": group_summaries, "person_consistency": person_rows,
        "inputs": sorted(inputs.pins.values(), key=lambda r:r["path"]),
    }
    write_json(out / "DIAGNOSTIC_RESULTS.json", payload)
    write_csv(out / "signal_metrics.csv", sample_rows)
    write_csv(out / "frozen_query_diagnostics.csv", query_rows)
    write_csv(out / "within_person_cosines.csv", pair_rows)
    write_json(out / "FROZEN_PILOT_BEFORE_AFTER.json", {"unchanged": True, "files": frozen_tree})
    print(json.dumps({"status": payload["status"], "integrity": payload["integrity"], "out": str(out)}, indent=2))


if __name__ == "__main__":
    main()
