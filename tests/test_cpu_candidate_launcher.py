"""Launcher logic only: fake tiny assets, no model inference or downloads."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/run_cpu_candidate.py"
spec = importlib.util.spec_from_file_location("cpu_candidate_launcher", SCRIPT)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "relocated checkout"
    root.mkdir()
    runtime = "linux-x86_64-cpu"
    monkeypatch.setattr(launcher, "host_runtime", lambda: runtime)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/" + name)

    def asset(name, data=b"test fixture, not a model"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    native_root = f"vendor/native-diarization/{runtime}"
    library = asset(f"{native_root}/runtime/lib/libnemo_speech_asr_c.so")
    notice = asset(f"{native_root}/runtime/LICENSE.txt", b"runtime notice")
    license_file = asset("licenses/native-diarization/OpenMDW-1.1.txt", b"model notice")
    model = asset("models/native-diarization/Nemotron-3-Diarization.q8_0.gguf")
    native_manifest = {"release": "0.2.0", "runtimes": {runtime: {}},
                       "model_license_sha256": license_file["sha256"],
                       "model": {**model, "destination": model["path"]}}
    (root / "native-runtime-manifest.json").write_text(json.dumps(native_manifest))
    model_manifest = {"runtime_files": [asset(name) for name in launcher.MODEL_FILES]}
    (root / "model-manifest.json").write_text(json.dumps(model_manifest))
    receipt = {"runtime": runtime, "version": "0.2.0", "files": [library, notice],
               "settings": {"native_diarization_model": "/old-host/model.gguf",
                            "native_diarization_library": "/old-host/library.so"}}
    receipt_path = root / native_root / "installation.json"
    receipt_path.write_text(json.dumps(receipt))
    args = argparse.Namespace(data_dir=root / "new data", model_dir=root / "models",
                              port=8765, memory_limit_gib=6.0)
    return SimpleNamespace(root=root, args=args, receipt=receipt, receipt_path=receipt_path,
                           library=library, model=model)


def test_plan_rebases_receipt_and_preserves_venv_python(installation):
    case = installation
    executable = str(case.root / ".venv/bin/python")
    plan = launcher.build_plan(case.args, root=case.root, executable=executable)
    command = plan["command"]
    assert command[:3] == [executable, "-m", "audio_backend"]
    flags = dict(zip(command[3::2], command[4::2]))
    assert flags["--native-diarization-library"] == str(case.root / case.library["path"])
    assert flags["--native-diarization-model"] == str(case.root / case.model["path"])
    assert flags["--asr-backend"] == "sensevoice"
    assert flags["--diarization-backend"] == "nemotron"
    assert flags["--speaker-calibration"] == "eres2net-ami20-v1"
    assert flags["--threads"] == flags["--native-diarization-threads"] == "2"
    assert flags["--device"] == "cpu" and flags["--overlap-backend"] == "none"
    assert flags["--host"] == "127.0.0.1" and flags["--memory-limit-gib"] == "6.0"
    assert not any("threshold" in flag for flag in flags if "speaker" in flag)
    assert not case.args.data_dir.exists()
    assert "/old-host/" not in json.dumps(plan)


def test_plan_accepted_by_existing_cli_without_starting_worker(installation, monkeypatch):
    from audio_backend.__main__ import main
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    plan = launcher.build_plan(installation.args, root=installation.root)
    main(plan["command"][3:])
    app, options = calls[0]
    settings = app.state.settings
    assert options == {"host": "127.0.0.1", "port": 8765, "workers": 1}
    assert settings.speaker_match_threshold == .55
    assert settings.speaker_centroid_threshold == .56
    assert settings.speaker_calibration_profile["profile_id"] == launcher.PROFILE
    assert settings.asr_backend == "sensevoice" and settings.diarization_backend == "nemotron"
    assert settings.overlap_backend == "none" and settings.inference_device == "cpu"


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/absolute", "..\\outside", "https://host/file"])
def test_relative_paths_reject_escape(tmp_path, name):
    with pytest.raises(ValueError, match="Unsafe"):
        launcher.local_path(tmp_path, name)


def test_relative_path_rejects_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside")
    try:
        (root / "link").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation not permitted on this platform")
    with pytest.raises(ValueError, match="escapes"):
        launcher.local_path(root, "link")


@pytest.mark.parametrize("field,value", [("runtime", "windows-x86_64-cpu"),
                                        ("runtime", "linux-x86_64-vulkan"),
                                        ("version", "0.1.0")])
def test_receipt_rejects_foreign_or_unexpected_runtime(installation, field, value):
    case = installation
    case.receipt[field] = value
    case.receipt_path.write_text(json.dumps(case.receipt))
    with pytest.raises(ValueError, match="does not match"):
        launcher.build_plan(case.args, root=case.root)


def test_receipt_rejects_entries_outside_runtime_tree(installation):
    case = installation
    case.receipt["files"][0]["path"] = "models/speaker.onnx"
    case.receipt_path.write_text(json.dumps(case.receipt))
    with pytest.raises(ValueError, match="outside the selected runtime"):
        launcher.build_plan(case.args, root=case.root)


def test_receipt_rejects_duplicate_entries(installation):
    case = installation
    case.receipt["files"].append(case.receipt["files"][0])
    case.receipt_path.write_text(json.dumps(case.receipt))
    with pytest.raises(ValueError, match="Duplicate"):
        launcher.build_plan(case.args, root=case.root)


@pytest.mark.parametrize("relative", ["models/speaker.onnx", "models/sensevoice/tokens.txt",
                                     "models/sensevoice/UPSTREAM_MODEL_LICENSE",
                                     "vendor/native-diarization/linux-x86_64-cpu/runtime/LICENSE.txt"])
def test_missing_required_asset_fails_before_launch(installation, relative):
    case = installation
    (case.root / relative).unlink()
    with pytest.raises(ValueError, match="Missing local asset"):
        launcher.build_plan(case.args, root=case.root)
    assert not case.args.data_dir.exists()


def test_corrupt_same_size_model_is_rejected(installation):
    case = installation
    path = case.root / "models/speaker.onnx"
    path.write_bytes(b"X" * path.stat().st_size)
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        launcher.build_plan(case.args, root=case.root)


def test_missing_receipt_has_explicit_setup_instruction(installation):
    case = installation
    case.receipt_path.unlink()
    with pytest.raises(ValueError, match="setup_native_diarization.py"):
        launcher.build_plan(case.args, root=case.root)


def test_windows_receipt_separators_are_portable(installation):
    case = installation
    for item in case.receipt["files"]:
        item["path"] = item["path"].replace("/", "\\")
    case.receipt_path.write_text(json.dumps(case.receipt))
    paths = launcher.installed_paths(case.root, case.args.model_dir)
    assert paths["library"] == str(case.root / case.library["path"].replace("\\", "/"))


def test_explicit_moved_model_tree_is_used(installation):
    case = installation
    moved = case.root / "copied models"
    case.args.model_dir.rename(moved)
    case.args.model_dir = moved
    plan = launcher.build_plan(case.args, root=case.root)
    command = plan["command"]
    assert command[command.index("--model-dir") + 1] == str(moved)
    assert command[command.index("--native-diarization-model") + 1].startswith(str(moved))


@pytest.mark.parametrize("budget", [.1, 11, float("nan"), float("inf")])
def test_invalid_memory_budget(installation, budget):
    case = installation
    case.args.memory_limit_gib = budget
    with pytest.raises(ValueError, match="memory-limit"):
        launcher.build_plan(case.args, root=case.root)


@pytest.mark.parametrize("port", [0, 65536])
def test_invalid_port(installation, port):
    installation.args.port = port
    with pytest.raises(ValueError, match="port"):
        launcher.build_plan(installation.args, root=installation.root)


def test_missing_ffprobe_is_reported(installation, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None if name == "ffprobe" else name)
    with pytest.raises(ValueError, match="ffprobe"):
        launcher.build_plan(installation.args, root=installation.root)


def test_dry_run_prints_json_without_subprocess_or_data(installation, monkeypatch, capsys):
    case = installation
    build = launcher.build_plan
    monkeypatch.setattr(launcher, "build_plan", lambda args: build(args, root=case.root))
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: pytest.fail("Dry run spawned a process"))
    assert launcher.main(["--dry-run", "--model-dir", str(case.args.model_dir),
                          "--data-dir", str(case.args.data_dir)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["command"][plan["command"].index("--overlap-backend") + 1] == "none"
    assert not case.args.data_dir.exists()


def test_launch_uses_argument_array_and_propagates_exit(monkeypatch, capsys):
    plan = {"command": ["venv python", "-m", "audio_backend"], "cwd": "/test checkout"}
    monkeypatch.setattr(launcher, "build_plan", lambda args: plan)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.main([]) == 7
    assert calls == [(plan["command"], {"cwd": plan["cwd"], "check": False})]
    assert json.loads(capsys.readouterr().out) == plan


@pytest.mark.parametrize("override", [["--device", "vulkan"], ["--host", "0.0.0.0"],
                                      ["--overlap-backend", "mossformer2_experimental"],
                                      ["--speaker-threshold", "0.1"]])
def test_candidate_rejects_implicit_profile_overrides(monkeypatch, override):
    monkeypatch.setattr(launcher, "build_plan", lambda args: pytest.fail("Invalid flags reached launch"))
    with pytest.raises(SystemExit) as exc:
        launcher.main(override)
    assert exc.value.code == 2


@pytest.mark.parametrize("system,machine,expected", [("Linux", "x86_64", "linux-x86_64-cpu"),
                                                   ("Windows", "AMD64", "windows-x86_64-cpu")])
def test_platform_selection(monkeypatch, system, machine, expected):
    monkeypatch.setattr(launcher.platform, "system", lambda: system)
    monkeypatch.setattr(launcher.platform, "machine", lambda: machine)
    assert launcher.host_runtime() == expected


@pytest.mark.parametrize("system,machine", [("Darwin", "x86_64"), ("Linux", "aarch64")])
def test_unsupported_host_requires_explicit_supported_setup(monkeypatch, system, machine):
    monkeypatch.setattr(launcher.platform, "system", lambda: system)
    monkeypatch.setattr(launcher.platform, "machine", lambda: machine)
    with pytest.raises(ValueError, match="supports"):
        launcher.host_runtime()


def test_moss_opt_in_requires_explicit_enable(installation):
    case=installation
    case.args.moss_td_python=case.root/'isolated-python'
    with pytest.raises(ValueError,match='enable-moss-td'):
        launcher.build_plan(case.args,root=case.root)


def test_moss_explicit_plan_verifies_assets_and_preserves_runtime_path(installation):
    case=installation
    case.args.enable_moss_td=True
    case.args.moss_td_python=case.root/'isolated-python'
    case.args.moss_td_python.write_bytes(b'placeholder for interpreter existence only')
    directory=case.root/'models/moss-td';directory.mkdir(parents=True)
    asset=directory/'model.safetensors';asset.write_bytes(b'fake tiny model')
    manifest={'runtime_files':[{'name':asset.name,'bytes':asset.stat().st_size,
                  'sha256':hashlib.sha256(asset.read_bytes()).hexdigest()}]}
    (case.root/'moss-td-model-manifest.json').write_text(json.dumps(manifest))
    plan=launcher.build_plan(case.args,root=case.root)
    command=plan['command']
    assert command[command.index('--overlap-backend')+1]=='moss_td_experimental'
    assert command[command.index('--moss-td-python')+1]==str(case.args.moss_td_python)
    assert command[command.index('--moss-td-model-dir')+1]==str(directory)
    assert 'unverified' in plan['warnings'][-1]
    asset.write_bytes(b'wrong content!!')
    with pytest.raises(ValueError,match='mismatch'):
        launcher.build_plan(case.args,root=case.root)


def test_probe_without_enable_does_not_silently_stay_disabled(installation):
    case=installation;case.args.moss_td_trigger='speech_probe'
    with pytest.raises(ValueError,match='enable-moss-td'):
        launcher.build_plan(case.args,root=case.root)

@pytest.fixture
def cpp_installation(installation):
    case=installation
    case.args.enable_moss_td=True
    case.args.moss_td_runtime='cpp'
    case.args.moss_td_python=case.root/'cpp-python'
    case.args.moss_td_python.write_bytes(b'interpreter fixture')
    directory=case.root/'models/moss-td-cpp';directory.mkdir()
    case.args.moss_td_gguf=case.root/'copied-model.gguf'
    case.args.moss_td_gguf.write_bytes(b'fake gguf')
    audit=directory/'LICENSE';audit.write_bytes(b'notice')
    def entry(p):return {'name':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    manifest=json.dumps({'models':[entry(case.args.moss_td_gguf)],'audit_files':[entry(audit)]})
    (case.root/'moss-td-cpp-model-manifest.json').write_text(manifest)
    (directory/'moss-td-cpp-model-manifest.json').write_text(manifest)
    return case


def test_cpp_plan_uses_separate_assets_and_cpu(cpp_installation):
    case=cpp_installation
    plan=launcher.build_plan(case.args,root=case.root)
    command=plan['command'];flags=dict(zip(command[3::2],command[4::2]))
    assert flags['--moss-td-runtime']=='cpp'
    assert flags['--moss-td-cpp-device']=='cpu'
    assert flags['--moss-td-gguf']==str(case.args.moss_td_gguf)
    assert flags['--moss-td-model-dir']==str(case.root/'models/moss-td-cpp')
    assert 'GGUF CPU' in plan['warnings'][-1]
    assert not case.args.data_dir.exists()


@pytest.mark.parametrize('target',['model','audit','receipt'])
def test_cpp_plan_rejects_changed_consumed_assets(cpp_installation,target):
    case=cpp_installation
    path={'model':case.args.moss_td_gguf,'audit':case.root/'models/moss-td-cpp/LICENSE',
          'receipt':case.root/'models/moss-td-cpp/moss-td-cpp-model-manifest.json'}[target]
    path.write_bytes(b'X'*path.stat().st_size)
    with pytest.raises(ValueError,match='mismatch'):
        launcher.build_plan(case.args,root=case.root)


@pytest.mark.parametrize('enabled,provider,gguf',[(False,'cpp',None),(False,'transformers',Path('x')),
                                              (True,'transformers',Path('x')),(True,'bad',None)])
def test_cpp_flags_cannot_be_silently_ignored(installation,enabled,provider,gguf):
    case=installation;case.args.enable_moss_td=enabled;case.args.moss_td_runtime=provider;case.args.moss_td_gguf=gguf
    with pytest.raises(ValueError):launcher.build_plan(case.args,root=case.root)
