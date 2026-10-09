#!/usr/bin/env python3
"""Explicit, offline CPU candidate launcher; it never changes the base CLI defaults.

Reads and verifies already installed local assets. --dry-run prints JSON without
loading models, starting the API, creating a database, or downloading anything.
Run setup_models.py --sensevoice and setup_native_diarization.py first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PROFILE = "eres2net-ami20-v1"
MODEL_FILES = (
    "models/speaker.onnx",
    "models/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx",
    "models/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv",
    "models/sensevoice/model.int8.onnx",
    "models/sensevoice/tokens.txt",
    "models/sensevoice/UPSTREAM_MODEL_LICENSE",
)


def host_runtime() -> str:
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise ValueError("The pinned native CPU runtime supports x86_64 only")
    system = {"Linux": "linux", "Windows": "windows"}.get(platform.system())
    if system is None:
        raise ValueError("The pinned native CPU runtime supports Linux or Windows only")
    return f"{system}-x86_64-cpu"


def local_path(root: Path, name: str) -> Path:
    """Resolve a receipt/manifest relative path without following it outside root."""
    if not isinstance(name, str) or not name:
        raise ValueError("Expected a nonempty relative asset path")
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or any(":" in x for x in path.parts):
        raise ValueError(f"Unsafe asset path: {name}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Asset path escapes its installation directory: {name}")
    return resolved


def verify_file(path: Path, item: dict) -> None:
    if not path.is_file():
        raise ValueError(f"Missing local asset: {path}; run the relevant setup script first")
    if "bytes" in item and path.stat().st_size != item["bytes"]:
        raise ValueError(f"Asset size mismatch: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != item["sha256"]:
        raise ValueError(f"Asset SHA256 mismatch: {path}; verify/reinstall it explicitly")


def installed_paths(root: Path, model_dir: Path) -> dict:
    """Rebase portable receipt entries, never reuse a preparation host's paths."""
    runtime = host_runtime()
    native_manifest = json.loads((root / "native-runtime-manifest.json").read_text())
    if runtime not in native_manifest["runtimes"]:
        raise ValueError(f"Runtime not present in pinned manifest: {runtime}")
    runtime_dir = root / "vendor/native-diarization" / runtime
    receipt_path = runtime_dir / "installation.json"
    if not receipt_path.is_file():
        raise ValueError("Missing native CPU installation receipt; run scripts/setup_native_diarization.py")
    receipt = json.loads(receipt_path.read_text())
    if receipt["runtime"] != runtime or receipt["version"] != native_manifest["release"]:
        raise ValueError("Native receipt platform/runtime/version does not match this CPU host")
    # The receipt's settings may contain absolute paths from a different machine.
    # Only its relative file inventory is used, constrained to this runtime tree.
    libraries = []
    seen = set()
    for item in receipt["files"]:
        path = local_path(root, item["path"])
        if not path.is_relative_to(runtime_dir.resolve()):
            raise ValueError("Native receipt file lies outside the selected runtime directory")
        # Multiple SONAME symlinks may resolve to the same regular library.
        relative = PurePosixPath(item["path"].replace("\\", "/"))
        if relative in seen:
            raise ValueError("Duplicate native receipt entry")
        seen.add(relative)
        verify_file(path, item)
        expected = "nemo_speech_asr_c.dll" if runtime.startswith("windows") else "libnemo_speech_asr_c.so"
        if relative.name == expected:
            libraries.append(path)
    if len(libraries) != 1:
        raise ValueError("Native receipt must list exactly one expected C ABI library")

    verify_file(local_path(root, "licenses/native-diarization/OpenMDW-1.1.txt"),
                {"sha256": native_manifest["model_license_sha256"]})
    model = native_manifest["model"]
    model_name = PurePosixPath(model["destination"])
    if not model_name.parts or model_name.parts[0] != "models":
        raise ValueError("Native model destination must be inside models")
    native_model = local_path(model_dir, str(model_name.relative_to("models")))
    verify_file(native_model, model)

    manifest = json.loads((root / "model-manifest.json").read_text())
    entries = {item["path"]: item for item in manifest["runtime_files"]}
    for name in MODEL_FILES:
        verify_file(local_path(model_dir, str(PurePosixPath(name).relative_to("models"))), entries[name])
    return {"runtime": runtime, "receipt": str(receipt_path),
            "model": str(native_model), "library": str(libraries[0])}


def build_plan(args, *, root: Path = ROOT, executable: str | None = None) -> dict:
    if not math.isfinite(args.memory_limit_gib) or not .25 <= args.memory_limit_gib <= 10:
        raise ValueError("memory-limit-gib must be finite and between 0.25 and 10")
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be in 1..65535")
    root = root.resolve()
    model_dir = args.model_dir.expanduser().resolve()
    data_dir = args.data_dir.expanduser().resolve()
    paths = installed_paths(root, model_dir)
    for program in ("ffmpeg", "ffprobe"):
        if shutil.which(program) is None:
            raise ValueError(f"{program} must be available on PATH")
    # Do not resolve sys.executable: a venv interpreter can itself be a symlink.
    command = [executable or sys.executable, "-m", "audio_backend",
               "--host", "127.0.0.1", "--port", str(args.port),
               "--data-dir", str(data_dir), "--model-dir", str(model_dir),
               "--threads", "2", "--memory-limit-gib", str(args.memory_limit_gib),
               "--device", "cpu", "--asr-backend", "sensevoice",
               "--diarization-backend", "nemotron",
               "--native-diarization-model", paths["model"],
               "--native-diarization-library", paths["library"],
               "--native-diarization-threads", "2",
               "--speaker-calibration", PROFILE, "--overlap-backend", "none",
               "--language", "auto", "--window-seconds", "30", "--event-threshold", "0.25"]
    enabled=bool(getattr(args,'enable_moss_td',False))
    overlap_warning="Overlap source candidates are disabled; raw simultaneous speaker turns remain independent evidence."
    runtime=getattr(args,'moss_td_python',None)
    trigger=getattr(args,'moss_td_trigger','native_overlap')
    if trigger not in ('native_overlap','speech_probe'):raise ValueError('Invalid MOSS-TD trigger')
    if trigger!='native_overlap' and not enabled:raise ValueError('Speech probes require --enable-moss-td')
    provider=getattr(args,'moss_td_runtime','transformers')
    gguf=getattr(args,'moss_td_gguf',None)
    if provider not in ('transformers','cpp'):raise ValueError('Invalid MOSS-TD runtime')
    if (provider!='transformers' or gguf) and not enabled:
        raise ValueError('CPP options require --enable-moss-td')
    if gguf and provider!='cpp':raise ValueError('--moss-td-gguf requires --moss-td-runtime cpp')
    if runtime and not enabled:
        raise ValueError('Use --enable-moss-td explicitly with --moss-td-python')
    if enabled:
        if runtime is None or not runtime.expanduser().is_file():
            raise ValueError('MOSS-TD needs an existing --moss-td-python isolated interpreter')
        directory=getattr(args,'moss_td_model_dir',None) or model_dir/('moss-td-cpp' if provider=='cpp' else 'moss-td')
        directory=directory.expanduser().resolve()
        manifest_path=root/('moss-td-cpp-model-manifest.json' if provider=='cpp' else 'moss-td-model-manifest.json')
        manifest=json.loads(manifest_path.read_text())
        if provider=='cpp':
            if gguf is None or not gguf.expanduser().is_file():
                raise ValueError('CPP MOSS-TD needs an existing --moss-td-gguf')
            gguf=gguf.expanduser().resolve()
            candidates=[item for item in manifest['models'] if item['bytes']==gguf.stat().st_size]
            if not candidates:raise ValueError('GGUF size not present in pinned CPP manifest')
            with gguf.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if not any(item['sha256']==digest for item in candidates):raise ValueError('GGUF SHA256 mismatch')
            verify_file(directory/manifest_path.name,{'sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest()})
            for item in manifest['audit_files']:verify_file(local_path(directory,item['name']),item)
        else:
            for item in manifest['runtime_files']:
                verify_file(local_path(directory,item['name']),item)
        command[command.index('--overlap-backend')+1]='moss_td_experimental'
        command.extend(['--moss-td-python',str(runtime.expanduser().absolute()),
                        '--moss-td-model-dir',str(directory),'--moss-td-trigger',trigger,
                        '--moss-td-runtime',provider])
        if provider=='cpp':command.extend(['--moss-td-gguf',str(gguf),'--moss-td-cpp-device','cpu'])
        overlap_warning=('MOSS-TD BF16 CPU lexical candidates enabled: raw model times/identities are unverified; '
                         'support links do not establish phrase placement, minority speech can be missing. '
                         'Review Apache-2.0 notices. Runtime/dependency support is checked at execution.')
        if provider=='cpp':overlap_warning=overlap_warning.replace('BF16 CPU','pinned GGUF CPU')
        if trigger=='speech_probe':overlap_warning+=' Speech probes cost more CPU and can hallucinate extra voices; probe rows are not native overlap detections.'
    return {"profile": "explicit_cpu_candidate_not_accepted", "cwd": str(root),
            "command": command, "native_runtime": paths["runtime"],
            "native_receipt": paths["receipt"],
            "verification": "Local model and license SHA256/size; native files against installer receipt. No inference or downloads.",
            "warnings": [
                "Review SenseVoice's custom FunASR Model License and Nemotron's OpenMDW-1.1 before use or redistribution.",
                "CPU2 is wrapper/compute configuration, not a cap of two total OS threads; native internal requests differ.",
                "RAM watchdog measures the process tree only, not VRAM; polling is not a hard instantaneous memory cap.",
                "Quality and long-duration acceptance remain unfinished. Windows/Vulkan execution is not validated.",
                overlap_warning]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "audio-data-cpu-candidate",
                        help="Candidate database/audio directory; separate from legacy audio-data")
    parser.add_argument("--model-dir", type=Path, default=ROOT / "models",
                        help="Already prepared model tree; no automatic download")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument('--enable-moss-td',action='store_true',help='Explicitly enable optional unverified overlap text candidates')
    parser.add_argument('--moss-td-python',type=Path,help='Interpreter of separately installed MOSS-TD environment')
    parser.add_argument('--moss-td-model-dir',type=Path,help='Pinned model/audit directory; defaults to MODEL_DIR/moss-td or moss-td-cpp')
    parser.add_argument('--moss-td-runtime',choices=('transformers','cpp'),default='transformers')
    parser.add_argument('--moss-td-gguf',type=Path,help='Pinned GGUF for CPP; CPU-only in this launcher')
    parser.add_argument('--moss-td-trigger',choices=('native_overlap','speech_probe'),default='native_overlap')
    parser.add_argument("--memory-limit-gib", type=float, default=6.0,
                        help="Sampled process-tree RAM budget, 0.25..10; default 6")
    parser.add_argument("--dry-run", action="store_true",
                        help="Verify local assets and print argv JSON without starting or writing data")
    args = parser.parse_args(argv)
    try:
        plan = build_plan(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return 0
    return subprocess.run(plan["command"], cwd=plan["cwd"], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
