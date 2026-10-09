#!/usr/bin/env python3
"""Run a bounded subprocess and capture resource evidence; never claims GPU measurements."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import psutil


def tree_memory(pid):
    try:
        root = psutil.Process(pid)
        procs = [root] + root.children(recursive=True)
    except psutil.NoSuchProcess:
        return 0, 0, "unavailable", {}
    rss = accounted = 0
    basis = "pss" if sys.platform.startswith("linux") else "rss_conservative"
    roles = {"gpu_eligible_asr_cpu_bytes": 0, "cpu_only_pipeline_and_service_bytes": 0}
    for proc in procs:
        try:
            info = proc.memory_full_info()
            rss += info.rss
            used = getattr(info, "pss", info.rss)
            accounted += used
            role = "gpu_eligible_asr_cpu_bytes" if "whisper-cli" in proc.name().lower() else "cpu_only_pipeline_and_service_bytes"
            roles[role] += used
            if not hasattr(info, "pss"):
                basis = "rss_conservative"  # Shared pages may be counted repeatedly.
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return accounted, rss, basis, roles


def stop_tree(pid):
    try:
        root = psutil.Process(pid)
        procs = root.children(recursive=True) + [root]
    except psutil.NoSuchProcess:
        return
    for proc in procs:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=3)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", required=True)
    ap.add_argument("--memory-limit-gib", type=float, default=5.5)
    ap.add_argument("--min-available-gib", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=7200)
    ap.add_argument("--interval", type=float, default=.10)
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        ap.error("command required after --")
    if args.memory_limit_gib <= 0 or args.interval <= 0:
        ap.error("positive limits required")
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    proc = subprocess.Popen(command)
    peak = peak_rss = samples = 0
    minimum_available = psutil.virtual_memory().available
    role_peaks = {}
    history = []
    next_history = 0.0
    reason = None
    basis = "unavailable"
    try:
        while proc.poll() is None:
            memory, rss, basis, roles = tree_memory(proc.pid)
            for role, value in roles.items():
                role_peaks[role] = max(role_peaks.get(role, 0), value)
            elapsed = time.monotonic()-start
            if elapsed >= next_history:
                history.append({"elapsed_seconds": round(elapsed, 3), "accounted_ram_bytes": memory, "roles": roles})
                next_history = elapsed + 30
            peak, peak_rss = max(peak, memory), max(peak_rss, rss)
            available = psutil.virtual_memory().available
            minimum_available = min(minimum_available, available)
            samples += 1
            if memory > args.memory_limit_gib * 2**30:
                reason = "service_memory_budget_exceeded"
            elif available < args.min_available_gib * 2**30:
                reason = "system_memory_headroom_exhausted"
            elif time.monotonic() - start > args.timeout:
                reason = "timeout"
            if reason:
                stop_tree(proc.pid)
                break
            time.sleep(args.interval)
    except BaseException:
        stop_tree(proc.pid)
        raise
    code = proc.wait()
    report = {
        "command": command, "wall_seconds": round(time.monotonic()-start, 3),
        "returncode": code, "termination_reason": reason,
        "peak_accounted_ram_bytes": peak, "ram_accounting": basis,
        "peak_sum_rss_bytes": peak_rss, "sample_interval_seconds": args.interval,
        "sample_count": samples, "minimum_system_available_bytes": minimum_available,
        "dedicated_vram_bytes": None, "gpu_measurement_status": "not_measured",
        "memory_limit_gib": args.memory_limit_gib,
        "role_peak_cpu_bytes": role_peaks, "memory_history_30s": history,
        "role_accounting_note": "Roles are classified by subprocess executable. Whisper ASR is eligible for future Vulkan; other current components use CPU. Per-role peaks may occur at different times and are NOT a GPU VRAM estimate.",
        "notes": "Sampled high-water mark, not proof of a strict instantaneous ceiling. Linux PSS avoids double counting shared pages; RSS fallback is conservative. External driver allocations are not measured.",
    }
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 125 if reason else code

if __name__ == "__main__":
    raise SystemExit(main())
