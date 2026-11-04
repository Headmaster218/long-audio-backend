"""Linux foreground supervisor for the existing strict CPU backend.

No asset download, runtime qualification, session creation or automatic restart.
The check command reads metadata, an explicitly supplied policy and optional
embedding-runtime/model hashes without constructing a model or starting a child.
Serve uses the same preflight before one child launch.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

from .resources import InstanceLock


class Refused(ValueError):
    pass


class OwnershipUncertain(RuntimeError):
    pass


# Retention is deliberate: a failed cleanup must not let garbage collection
# close the supervisor's last owner. The CLI stays alive on this condition.
_PENDING_OWNER = None


def _notice(*parts):
    # Logging must never turn uncertain ownership into process exit.
    try:
        print(*parts, file=sys.stderr, flush=True)
    except BaseException:
        pass


def _canonical(path, *, directory=False, private=False):
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path or path.is_symlink():
        raise Refused('Paths must be existing absolute canonical paths without symlinks')
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise Refused('Expected an existing directory' if directory else 'Expected an existing regular file')
    if private and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise Refused('Data directory and instance.lock must be private and owned by this user')
    if not directory and (info.st_nlink != 1 or (not private and info.st_size == 0)):
        raise Refused('Files must have one hard link; model/runtime assets must be nonempty')
    return path


def _acquire(data):
    """Acquire once, read-only, then reuse the product inherited-fd validator."""
    import fcntl
    _canonical(data, directory=True, private=True)
    path = _canonical(data / 'instance.lock', private=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    # adopt consumes fd, including on failure. No LOCK_UN is ever issued.
    return InstanceLock(path).adopt(fd)


def _assets(args):
    _canonical(args.model_dir, directory=True)
    for path in (args.native_diarization_model, args.native_diarization_library,
                 args.recording_speaker_model,
                 args.model_dir / 'speaker.onnx',
                 args.model_dir / 'sensevoice/model.int8.onnx',
                 args.model_dir / 'sensevoice/tokens.txt',
                 args.model_dir / 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx',
                 args.model_dir / 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv'):
        _canonical(path)
    if args.reviewed_primary_persons:
        # Use the product's bounded exact-schema validator, just as Settings
        # does. This reads configuration only, never opens or activates a bank.
        from .verified_identity import policy_file
        policy_file(_canonical(args.verified_identity_policy))


def _voice_embedding_preflight(args):
    """Advisory current-process snapshot, never bank or acoustic qualification.

    Reuse the probe's authoritative pre-construction check. Native runtime
    libraries/configuration may load, but no extractor is constructed. The real
    child must still repeat its unchanged checks immediately before extraction.
    """
    report = {'status': 'inspection_failed', 'reason': 'embedding_inspection_failed',
              'scope': 'current_process_preconstruction_snapshot',
              'provider': 'cpu', 'num_threads': 2, 'extractor': 'not_constructed',
              'inference': 'not_run', 'quality': 'not_evaluated'}
    try:
        from .embedding_space import SherpaSpaceAttestor
        from .recording_speaker_probe_runner import BINDING_ID, MODEL_SHA256, expected_space
        from .moss_handy_profile import runtime_site_paths
        expected, space_id = expected_space()
        report.update(expected_binding_id=BINDING_ID, expected_space_id=space_id)
        import numpy as np
        import sherpa_onnx as sherpa
        config = sherpa.SpeakerEmbeddingExtractorConfig(
            model=str(args.recording_speaker_model), provider='cpu', num_threads=2)
        attestor = SherpaSpaceAttestor()
        snapshot = attestor.begin(sherpa, config, MODEL_SHA256)
        if snapshot['descriptor'] != expected:
            report.update(status='unsupported', reason='probe_embedding_space_mismatch')
            return report
        # A matching foreign PYTHONPATH package cannot establish support in the
        # selected interpreter's isolated child. This is an origin check only;
        # compatibility remains exclusively the existing attestor's decision.
        roots = runtime_site_paths()
        paths = [Path(sherpa.__file__).resolve(), Path(np.__file__).resolve(),
                 *attestor._runtime_paths.values()]
        if any(not any(path.is_relative_to(root) for root in roots) for path in paths):
            report['reason'] = 'foreign_runtime_origin'
            return report
        report.update(status='preconstruction_supported', reason='existing_probe_binding_match')
    except ImportError:
        report['reason'] = 'embedding_dependency_unavailable'
    except OSError:
        report['reason'] = 'embedding_artifact_read_failed'
    except ValueError as error:
        # Fixed codes only: native exceptions can contain private model paths.
        reason = {
            'Embedding runtime platform unverified': 'embedding_platform_unverified',
            'Unrecognized extractor implementation': 'extractor_implementation_unverified',
            'Unrecognized extractor configuration': 'extractor_configuration_unverified',
            'Embedding model changed since analyzer initialization': 'embedding_model_bytes_unverified',
            'Effective frontend defaults differ from audited runtime': 'embedding_frontend_unverified',
            'Embedding runtime/model proof unavailable': 'embedding_runtime_model_unverified',
            'Sherpa wrapper and native versions disagree': 'embedding_runtime_version_mismatch',
        }.get(str(error))
        if reason is not None:
            report.update(status='unsupported', reason=reason)
    except Exception:
        pass  # Diagnostic failure never legalizes evidence or blocks core ASR.
    return report


def _child_command(args, fd):
    # -I excludes PYTHONPATH/user-site injection. Insert only this exact checkout
    # or installed package root; no previous workspace or qualification harness.
    package_root = str(Path(__file__).resolve().parent.parent)
    bootstrap = ('import sys; sys.path.insert(0, ' + repr(package_root) + '); '
                 'from audio_backend.__main__ import main; main()')
    command = [sys.executable, '-I', '-B', '-c', bootstrap,
        '--instance-lock-fd', str(fd), '--data-dir', str(args.data_dir),
        '--model-dir', str(args.model_dir), '--host', args.host, '--port', str(args.port),
        '--device', 'cpu', '--asr-backend', 'sensevoice', '--diarization-backend', 'nemotron',
        '--overlap-backend', 'none', '--native-diarization-model', str(args.native_diarization_model),
        '--native-diarization-library', str(args.native_diarization_library),
        '--recording-speaker-model', str(args.recording_speaker_model),
        '--language', args.language, '--threads', str(args.threads),
        '--native-diarization-threads', str(args.native_diarization_threads),
        '--memory-limit-gib', str(args.memory_limit_gib),
        '--window-seconds', '5.25', '--context-seconds', '0',
        '--speaker-support-max-records', '65536',
        '--recording-capture-mib', str(args.recording_capture_mib),
        '--recording-total-mib', str(args.recording_total_mib),
        '--lexical-evidence-session-mib', str(args.lexical_evidence_session_mib),
        '--strict-primary', '--strict-primary-policy', args.profile,
        '--strict-payload-source-storage', args.strict_payload_source_storage,
        '--strict-primary-lifetime-records', str(args.strict_primary_lifetime_records),
        '--strict-primary-receipt-records', str(args.strict_primary_receipt_records),
        '--native-owned-support', '--native-owned-cells', '--native-owned-core-seams',
        '--native-owned-bounded-core-support', '--lexical-evidence',
        '--recording-speaker-links', '--speaker-support-review',
        '--recording-primary-observation-policy', 'primary-clean-history-v1',
        '--recording-primary-sampling-policy', 'skip-bucket-v1',
        '--recording-primary-observation-interval-seconds', '60']
    if args.reviewed_primary_persons:
        command.extend(['--reviewed-primary-persons', '--verified-identity-policy',
                        str(args.verified_identity_policy)])
    return command


def _subreaper():
    # Orphaned inference grandchildren become ours to reap, even if they create
    # another process group. This module is a standalone process, not a generic
    # supervisor library to embed in an application with unrelated children.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'Cannot establish child-subreaper ownership')
    value = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(value), 0, 0, 0) != 0 or value.value != 1:
        raise Refused('Child-subreaper ownership was not established')


class _Owner:
    def __init__(self, lock):
        self.lock = lock
        self.process = None
        self.complete = False
        self.uncertain = False


class _Signals:
    def __init__(self):
        self.requested = None
        self.previous = {}

    def __enter__(self):
        def request(signum, _frame):
            # Never raise between Popen and assignment or interrupt positive wait.
            if self.requested is None:
                self.requested = signum
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.previous[signum] = signal.signal(signum, request)
        return self

    def __exit__(self, *_):
        for signum, previous in self.previous.items():
            signal.signal(signum, previous)


def _children():
    import psutil
    return psutil.Process().children(recursive=True)


def _signal_children(signum, original):
    import psutil
    # PID reuse is checked by psutil's send_signal; the unreaped original also
    # pins its PID/process-group identity until the final Popen.wait().
    for child in reversed(_children()):
        try:
            if child.pid != original or signum == signal.SIGKILL:
                child.send_signal(signum)
        except psutil.NoSuchProcess:
            pass


def _reap_adopted(original):
    import psutil
    for child in psutil.Process().children():
        if child.pid == original:
            continue
        try:
            os.waitpid(child.pid, os.WNOHANG)
        except ChildProcessError:
            # A live descendant can still belong to its original parent while
            # process-tree inspection races reparenting. Never count it closed.
            pass


def _run_child(owner, command, signals, grace_seconds):
    """Internal process primitive. Production never accepts an arbitrary command."""
    if _children():
        raise Refused('Supervisor must be a dedicated process without existing children')
    _subreaper()
    owner.lock.verify_inherited()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('AUDIO_') or key == 'AUDIO_API_KEY'}
    package_root = str(Path(__file__).resolve().parent.parent)
    for key in ('PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE', 'PYTHONSAFEPATH'):
        env.pop(key, None)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', PYTHONPATH=package_root)
    try:
        owner.process = subprocess.Popen(command, pass_fds=(owner.lock.file.fileno(),),
            close_fds=True, stdin=subprocess.DEVNULL, start_new_session=True, env=env, cwd=package_root)
    except OSError:
        # Popen waits its failed exec child before propagating its ordinary
        # launch error. Unexpected exceptions are not treated as this guarantee.
        owner.uncertain = True
        if not _children():
            owner.uncertain = False
        raise
    except BaseException:
        owner.uncertain = True
        raise
    process = owner.process
    deadline = None
    forced = False
    unexpected = None
    try:
        while True:
            # WNOWAIT observes exit without reaping the leader, preventing PID
            # reuse until its session/group descendants have also been drained.
            exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if signals.requested is not None and deadline is None:
                deadline = time.monotonic() + grace_seconds
                if exited is None:
                    # Popen.send_signal polls/reaps internally, losing WNOWAIT's
                    # PID pin if exit races signal dispatch. Signal without wait.
                    try:
                        os.kill(process.pid, signals.requested)
                    except ProcessLookupError:
                        pass
            if exited is not None:
                _reap_adopted(process.pid)
                remaining = [child for child in _children() if child.pid != process.pid]
                if not remaining:
                    # The only positive original wait is on the original handle.
                    result = process.wait()
                    if _children():
                        raise OwnershipUncertain('Children remain after original positive wait')
                    owner.complete = True
                    if forced and result == 0:
                        result = 1  # A killed orphan is not a clean service success.
                    return (128 + signals.requested if signals.requested is not None else
                            128 - result if result < 0 else result)
                if deadline is None:
                    deadline = time.monotonic() + grace_seconds
                    _signal_children(signal.SIGTERM, process.pid)
            if deadline is not None and time.monotonic() >= deadline:
                # The unreaped original pins the group ID. Cover both its group
                # and any descendants that moved into another session/group.
                if not forced:
                    _notice('Graceful shutdown expired; forcibly terminating owned processes. '
                            'This is not a clean service completion.')
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                _signal_children(signal.SIGKILL, process.pid)
                forced = True
            if forced:
                _reap_adopted(process.pid)
            time.sleep(.05)
    except BaseException as error:
        unexpected = error
    # An observation/wait failure is not completion. Keep the lock strongly held;
    # kill known owned processes without claiming that cleanup was confirmed.
    owner.uncertain = True
    try:
        _signal_children(signal.SIGKILL, process.pid)
    except BaseException:
        pass
    raise OwnershipUncertain('Child ownership cleanup could not be confirmed') from unexpected


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'serve'))
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--native-diarization-model', type=Path, required=True)
    parser.add_argument('--native-diarization-library', type=Path, required=True)
    parser.add_argument('--recording-speaker-model', type=Path, required=True)
    parser.add_argument('--language', choices=('auto', 'zh', 'en', 'ja', 'ko', 'yue'), required=True)
    parser.add_argument('--host', choices=('127.0.0.1', '::1'), default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--threads', type=int, choices=range(1, 5), default=2)
    parser.add_argument('--native-diarization-threads', type=int, choices=(1, 2), default=2)
    parser.add_argument('--memory-limit-gib', type=float, default=6.)
    parser.add_argument('--recording-capture-mib', type=int, default=4096)
    parser.add_argument('--recording-total-mib', type=int, default=8192)
    parser.add_argument('--lexical-evidence-session-mib', type=int, default=256)
    parser.add_argument('--profile', choices=('strict-primary-payload-v2',), required=True)
    parser.add_argument('--strict-payload-source-storage', choices=('inline', 'shared-source-v1'), required=True)
    parser.add_argument('--strict-primary-lifetime-records', type=int, default=32768)
    parser.add_argument('--strict-primary-receipt-records', type=int, default=8192)
    parser.add_argument('--reviewed-primary-persons', action='store_true',
                        help='Opt in to existing reviewed person APIs; requires --verified-identity-policy')
    parser.add_argument('--verified-identity-policy', type=Path,
                        help='Existing absolute canonical experimental policy JSON; requires --reviewed-primary-persons')
    parser.add_argument('--shutdown-grace-seconds', type=float, default=90.)
    return parser


def _validate(args):
    if sys.platform != 'linux':
        raise Refused('Strict supervisor requires Linux, procfs and flock')
    if args.reviewed_primary_persons != (args.verified_identity_policy is not None):
        raise Refused('--reviewed-primary-persons and --verified-identity-policy must be supplied together')
    if not 1 <= args.port <= 65535:
        raise Refused('Port must be between 1 and 65535')
    for name, low, high in (('memory_limit_gib', .25, 10.), ('shutdown_grace_seconds', .1, 600.),
                            ('recording_capture_mib', 1, 65536), ('recording_total_mib', 1, 65536),
                            ('lexical_evidence_session_mib', 1, 1024)):
        if not math.isfinite(getattr(args, name)) or not low <= getattr(args, name) <= high:
            raise Refused(name + ' is outside its supported range')
    from .strict_lifetime import validate, validate_receipts
    validate(args.strict_primary_lifetime_records)
    validate_receipts(args.strict_primary_receipt_records)


def main(argv=None):
    global _PENDING_OWNER
    args = _parser().parse_args(argv)
    if _PENDING_OWNER is not None:
        raise OwnershipUncertain('Prior supervisor ownership is unresolved')
    owner = None
    with _Signals() as signals:
        try:
            _validate(args)
            owner = _Owner(_acquire(args.data_dir))
            _PENDING_OWNER = owner
            from .strict_runtime_preflight import inspect_runtime
            runtime = inspect_runtime(args.data_dir)
            if runtime['status'] != 'matched':
                print(json.dumps({'schema': 'strict-supervisor-check-v1', 'status': 'refused',
                    'reason': 'unqualified_runtime', 'runtime': runtime}, sort_keys=True))
                return 2
            _assets(args)
            owner.lock.verify_inherited()
            if signals.requested is not None:
                return 128 + signals.requested
            voice_embedding = _voice_embedding_preflight(args)
            owner.lock.verify_inherited()
            if signals.requested is not None:
                return 128 + signals.requested
            if args.action == 'check':
                print(json.dumps({'schema': 'strict-supervisor-check-v1', 'status': 'preflight_passed',
                    'runtime': runtime, 'assets': 'existing_paths_only_not_content_or_license_verified',
                    'policy': args.profile, 'storage': args.strict_payload_source_storage,
                    'reviewed_primary_persons_enabled': args.reviewed_primary_persons,
                    'verified_identity_policy': ('validated_configuration_only' if args.reviewed_primary_persons
                                                 else 'not_requested'),
                    'person_bank': 'not_opened_or_activated',
                    'voice_embedding': voice_embedding,
                    'models': 'not_loaded', 'service': 'not_started', 'session': 'not_created'}, sort_keys=True))
                return 0
            _notice('Voice embedding diagnostic (advisory, not service readiness):',
                    json.dumps(voice_embedding, sort_keys=True))
            if voice_embedding['status'] != 'preconstruction_supported':
                _notice('Optional voice embeddings are not preflight-supported; continuing the strict service. '
                        'Model-free reviewed-person configuration is separate from acoustic matching.')
            _notice('Preflight passed. Starting one strict CPU service on loopback; no automatic restart. '
                    'Existing queued work may run. Process start is not service readiness.')
            return _run_child(owner, _child_command(args, owner.lock.file.fileno()), signals,
                              args.shutdown_grace_seconds)
        except BaseException as error:
            _notice('Strict supervisor refused:', error)
            if owner is not None and (owner.uncertain or (owner.process is not None and not owner.complete)):
                _notice('Ownership is unresolved; retaining the original lock. Inspect this process and its '
                        'descendants before taking recovery action. SIGINT/SIGTERM do not release this hold.')
                while True:
                    time.sleep(3600)
            return 1
        finally:
            if owner is not None and not owner.uncertain and (owner.process is None or owner.complete):
                owner.lock.release()
                _PENDING_OWNER = None


if __name__ == '__main__':
    raise SystemExit(main())
