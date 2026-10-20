"""Portable single-process launcher. CPU is the measured default; Vulkan is opt-in."""
from __future__ import annotations
import argparse
import os
from pathlib import Path


# Keep unresolved child owners referenced until process exit, not garbage
# collection. The supervisor separately keeps its hold until positive wait.
_INHERITED_APPS_PENDING_CLEANUP = []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--fragment-cpu-manifest', type=Path, help='Explicit pinned finalized-PCM16 CPU provider manifest')
    parser.add_argument('--fragment-cpu-sha256', help='SHA256 of the exact manifest file bytes')
    parser.add_argument('--fragment-cpu-check', action='store_true', help='Read-only admission check; no models, database, child or listener')
    parser.add_argument('--fragment-cpu-inspect-lease', help='Inspect one pending owner lease while holding the instance lock')
    parser.add_argument('--fragment-cpu-reconcile', help='Explicitly reconcile that lease using the exact prior inspection SHA256')
    parser.add_argument('--instance-lock-fd', type=int, help='Linux only: consume an inherited prelocked read-only instance.lock fd before initialization')
    parser.add_argument('--data-dir', type=Path, default=Path(os.getenv('AUDIO_DATA_DIR','audio-data')))
    parser.add_argument('--model-dir', type=Path, default=Path(os.getenv('AUDIO_MODEL_DIR','models')))
    parser.add_argument('--threads', type=int, choices=range(1,5), default=4)
    parser.add_argument('--memory-limit-gib', type=float, default=6.0)
    parser.add_argument('--recording-capture-mib', type=int, default=1024,
                        help='Explicit retained recording-copy budget per capture, 1..65536 MiB; free-space floor still applies')
    parser.add_argument('--recording-total-mib', type=int, default=4096,
                        help='Explicit global retained recording-copy budget, 1..65536 MiB; free-space floor still applies')
    parser.add_argument('--device', choices=('cpu','vulkan'), default='cpu')
    parser.add_argument('--asr-backend', choices=('whisper','sensevoice'), default='whisper', help='SenseVoice is opt-in with a custom model license; see README')
    parser.add_argument('--diarization-backend',choices=('pyannote','nemotron'),default='pyannote',help='Nemotron native is opt-in;8 local speakers per bounded epoch, separate model license')
    parser.add_argument('--native-diarization-model',type=Path)
    parser.add_argument('--native-diarization-library',type=Path)
    parser.add_argument('--native-diarization-threads',type=int,choices=(1,2),default=2)
    parser.add_argument('--overlap-backend',choices=('none','mossformer2_experimental','dicow_en_experimental','moss_td_experimental'),default='none',help='Opt-in unverified source candidates; never replaces the mixture transcript')
    parser.add_argument('--overlap-python',type=Path)
    parser.add_argument('--dicow-python',type=Path)
    parser.add_argument('--dicow-model-dir',type=Path)
    parser.add_argument('--moss-td-python',type=Path)
    parser.add_argument('--moss-td-model-dir',type=Path)
    parser.add_argument('--moss-td-timeout-seconds',type=float,default=300.)
    parser.add_argument('--moss-td-max-seconds',type=float,default=40.,help='Total candidate support audio per core,1–60s; each support is at most20s')
    parser.add_argument('--moss-td-trigger',choices=('native_overlap','speech_probe'),default='native_overlap',help='Optional speech probes attempt additional native-speech supports; they are not detected overlaps')
    parser.add_argument('--moss-td-runtime',choices=('transformers','cpp'),default='transformers')
    parser.add_argument('--moss-td-gguf',type=Path,help='Pinned GGUF model for the optional C++ MOSS runtime')
    parser.add_argument('--moss-td-cpp-device',choices=('cpu','vulkan'),default='cpu',help='Vulkan is opt-in and unmeasured here; RAM watchdog does not measure VRAM')
    parser.add_argument('--overlap-source-dir',type=Path)
    parser.add_argument('--overlap-window-mode',choices=('official_2s','direct_bounded'),default='official_2s')
    parser.add_argument('--asr-model', default='ggml-small-q5_1.bin', help='Whisper model filename; unused by SenseVoice')
    parser.add_argument('--whisper-cli', type=Path)
    parser.add_argument('--language', default='auto', help='auto, zh, en, or a supported Whisper language code')
    parser.add_argument('--window-seconds', type=float, default=30)
    parser.add_argument('--event-threshold', type=float, default=.25)
    parser.add_argument('--framemn', action='store_true', help='EXPERIMENTAL default-off temporal Knock candidate side channel; does not change events or ASR')
    parser.add_argument('--framemn-checkpoint', type=Path, help='User-supplied local FrameMN10 checkpoint; no automatic downloads')
    parser.add_argument('--framemn-profile', choices=('knock-001-median9-experimental-v1',), default='knock-001-median9-experimental-v1')
    parser.add_argument('--reviewed-primary-persons', action='store_true', help='Enable explicit model-free crop-only human person associations')
    parser.add_argument('--verified-identity-policy',help='Explicit experimental verified-review JSON policy; applies only to newly requested verified groups')
    parser.add_argument('--speaker-support-review',action='store_true',help='Opt-in exact acoustic review capture; bounded private PCM, not identity learning; see docs/EMBEDDING_SUPPORT_LINEAGE.md')
    parser.add_argument('--native-owned-cells',action='store_true',help='UNQUALIFIED default-OFF passive durable selected cells; requires separately qualified SQLite/VFS runtime')
    parser.add_argument('--native-owned-core-seams',action='store_true',help='Candidate default-OFF same-epoch and strictly validated reset-replay adjacent-core alternatives; requires --native-owned-cells and fresh optional store')
    parser.add_argument('--native-owned-bounded-core-support', action='store_true', help='Default-off bounded capture; requires automatic MOSS or exact clean-primary mode, native-owned support/cells/core-seams and lexical evidence')
    parser.add_argument('--lexical-evidence', action='store_true', help='Default-off exact lexical retention for bounded native provenance')
    parser.add_argument('--native-owned-compact-gc',action='store_true',help='Candidate default-OFF orphan compact reclamation for completed stream sources; keeps all source receipts/cells/results; requires --native-owned-cells and --native-owned-core-seams')
    parser.add_argument('--native-owned-receipt-gc',action='store_true',help='Candidate default-OFF orphan receipt reclamation for completed sealed recording pages; requires --native-owned-compact-gc; keeps all cells, audio and results')
    parser.add_argument('--native-owned-seam-cells',action='store_true',help='UNQUALIFIED default-OFF two-commit seam alternatives; requires --native-owned-cells; explicit overlapping diagnostics only')
    parser.add_argument('--native-owned-support',action='store_true',help='Experimental volatile native overlap support for isolated PCM uploads up to20s; read-only, lost on restart')
    for option, default in (('rows', 65536), ('rows-per-session', 16384),
                            ('bytes', 2 * 1024**3), ('bytes-per-session', 1024**3)):
        parser.add_argument('--moss-result-max-' + option, type=int, default=default,
                            help='Finite retained MOSS result admission limit; never evicts saved outputs')
    parser.add_argument('--moss-jobs',action='store_true',help='Explicit single MOSS job service with durable structured history; requires native-owned support, CPU and no overlap/fragment provider')
    parser.add_argument('--moss-jobs-model',type=Path,help='Explicit local fixed-profile MOSS model; does not enable jobs implicitly')
    parser.add_argument('--moss-automatic',action='store_true',help='Candidate default-OFF automatic timestamped recording MOSS dispatch; requires MOSS jobs, native-owned support/cells/core-seams and the ordinary Worker; excludes legacy seam-cells')
    parser.add_argument('--recording-speaker-links', action='store_true', help='EXPERIMENTAL default-off recording-scoped anonymous speaker links; 32-node cap; requires automatic MOSS or exact clean-primary mode')
    parser.add_argument('--recording-primary-observation-policy', choices=('off','primary-clean-history-v1'), default='off', help='EXPERIMENTAL clean-primary immutable history; no model qualification')
    parser.add_argument('--recording-primary-sampling-policy', choices=('skip-bucket-v1','fresh-window-bounded-v2'), default='skip-bucket-v1', help='EXPERIMENTAL fresh live-window admission only; existing scopes keep their policy')
    parser.add_argument('--recording-primary-observation-interval-seconds', type=int, default=60)
    parser.add_argument('--recording-speaker-history-read', action='store_true', help='Passive saved history projection; does not enable models')
    parser.add_argument('--recording-speaker-model', type=Path, help='Explicit existing local audited speaker.onnx; no downloads or gallery enrollment')
    parser.add_argument('--recording-primary-provenance-recording-bytes', type=int, default=128 * 1024**2, help='Logical immutable primary provenance quota per recording; no eviction')
    parser.add_argument('--recording-primary-provenance-global-bytes', type=int, default=512 * 1024**2, help='Global logical immutable primary provenance quota; physical DB/WAL is separate')
    parser.add_argument('--speaker-threshold', type=float, default=.75)
    parser.add_argument('--new-speaker-threshold', type=float, default=.45)
    parser.add_argument('--speaker-margin', type=float, default=.10)
    parser.add_argument('--speaker-profile-strategy', choices=['max','centroid'], default='max', help='Score only explicit human-merged examples; never adapt from automatic matches')
    parser.add_argument('--speaker-calibration', help='Opt-in model-bound policy: eres2net-ami20-v1 or a local profile JSON; controls speaker thresholds and scoring')
    args = parser.parse_args(argv)
    lock=None
    try:
        if args.instance_lock_fd is not None:
            from .resources import InstanceLock
            lock=InstanceLock(args.data_dir/'instance.lock').adopt(args.instance_lock_fd)
        return _main(args,parser,argv,lock)
    finally:
        if lock is not None: lock.release()


def _run_app(settings,args,lock,**kwargs):
    from .app import create_app
    import uvicorn
    if lock is None:
        app=create_app(settings,**kwargs)
    else:
        # create_app consumes only this duplicate, never the launcher's hold.
        lock.verify_inherited(settings.data_dir/'instance.lock')
        app=create_app(settings,instance_lock_fd=os.dup(lock.file.fileno()),**kwargs)
    try:
        uvicorn.run(app,host=args.host,port=args.port,workers=1)
    finally:
        if lock is not None:
            if (not app.state.instance_lock_lifespan_started
                    or app.state.instance_lock_cleanup_complete):
                app.state.instance_lock.release()
            else:
                _INHERITED_APPS_PENDING_CLEANUP.append(app)


def _main(args,parser,argv,lock):
    for name in ('recording_capture_mib','recording_total_mib'):
        if not 1 <= getattr(args,name) <= 65536:
            parser.error('--'+name.replace('_','-')+' must be an integer in 1..65536 MiB')
    if args.framemn_checkpoint and not args.framemn:
        parser.error('--framemn-checkpoint requires --framemn')
    if args.framemn and not args.framemn_checkpoint:
        parser.error('--framemn requires an explicit --framemn-checkpoint')
    if args.native_owned_core_seams and not args.native_owned_cells:
        parser.error('--native-owned-core-seams requires --native-owned-cells')
    if args.native_owned_receipt_gc and not args.native_owned_compact_gc:
        parser.error('--native-owned-receipt-gc requires --native-owned-compact-gc')
    if args.native_owned_compact_gc and not (args.native_owned_cells and args.native_owned_core_seams):
        parser.error('--native-owned-compact-gc requires --native-owned-cells and --native-owned-core-seams')
    if args.native_owned_seam_cells and not args.native_owned_cells:
        parser.error('--native-owned-seam-cells requires --native-owned-cells')
    if args.recording_speaker_links and not args.recording_speaker_model:
        parser.error('--recording-speaker-links requires --recording-speaker-model; mode prerequisites are validated by Settings')
    if args.recording_speaker_model and not args.recording_speaker_links:
        parser.error('--recording-speaker-model requires --recording-speaker-links')
    if args.moss_jobs_model and not args.moss_jobs:
        parser.error('MOSS jobs model requires --moss-jobs')
    if args.moss_automatic and not (args.moss_jobs and args.native_owned_support
            and args.native_owned_cells and args.native_owned_core_seams):
        parser.error('--moss-automatic requires --moss-jobs, --native-owned-support, --native-owned-cells and --native-owned-core-seams')
    if args.moss_automatic and args.native_owned_seam_cells:
        parser.error('--moss-automatic excludes --native-owned-seam-cells')
    if args.moss_jobs and (not args.moss_jobs_model or not args.native_owned_support
            or args.diarization_backend != 'nemotron' or args.device != 'cpu' or args.overlap_backend != 'none'
            or any((args.fragment_cpu_manifest, args.fragment_cpu_sha256, args.fragment_cpu_check,
                    args.fragment_cpu_inspect_lease, args.fragment_cpu_reconcile))):
        parser.error('MOSS jobs requires explicit model, native-owned support, nemotron, CPU, overlap none and legacy worker')
    if (args.native_owned_support or args.native_owned_cells) and any((args.fragment_cpu_manifest,args.fragment_cpu_sha256,args.fragment_cpu_check,
                                          args.fragment_cpu_inspect_lease,args.fragment_cpu_reconcile)):
        parser.error('Native-owned support/cells require the ordinary legacy worker')
    if args.moss_td_trigger=='speech_probe' and args.overlap_backend!='moss_td_experimental':
        parser.error('--moss-td-trigger speech_probe requires --overlap-backend moss_td_experimental')
    if (args.moss_td_runtime!='transformers' or args.moss_td_gguf) and args.overlap_backend!='moss_td_experimental':
        parser.error('MOSS-TD C++ options require --overlap-backend moss_td_experimental')
    if args.moss_td_gguf and args.moss_td_runtime!='cpp':
        parser.error('--moss-td-gguf requires --moss-td-runtime cpp')
    if args.moss_td_cpp_device!='cpu' and (args.moss_td_runtime!='cpp' or args.overlap_backend!='moss_td_experimental'):
        parser.error('MOSS-TD Vulkan requires the enabled MOSS C++ runtime')
    if args.speaker_calibration:
        import sys
        raw_args=sys.argv[1:] if argv is None else argv
        manual={'--speaker-threshold','--new-speaker-threshold','--speaker-margin','--speaker-profile-strategy'}
        if any(arg.split('=',1)[0] in manual for arg in raw_args):
            parser.error('Choose --speaker-calibration or manual speaker policy flags, not both')
    if not .25 <= args.memory_limit_gib <= 10:
        parser.error('memory-limit-gib must be >=0.25 and <=10; leave RAM for the OS')
    if not 1 <= args.port <= 65535:
        parser.error('port must be in 1..65535')
    if args.host not in {'127.0.0.1','localhost','::1'} and not os.getenv('AUDIO_API_KEY'):
        parser.error('Set AUDIO_API_KEY before network exposure; TLS reverse proxy is your responsibility')
    if any((args.fragment_cpu_manifest,args.fragment_cpu_sha256,args.fragment_cpu_check,
            args.fragment_cpu_inspect_lease,args.fragment_cpu_reconcile)):
        import sys
        import json
        from .fragment_cpu_provider import Manifest, CPUFactory, namespace, inspect_lease, reconcile_lease
        allowed={'--fragment-cpu-manifest','--fragment-cpu-sha256','--fragment-cpu-check',
                 '--fragment-cpu-inspect-lease','--fragment-cpu-reconcile','--data-dir','--host','--port','--instance-lock-fd'}
        raw=sys.argv[1:] if argv is None else argv
        if any(a.split('=',1)[0] not in allowed for a in raw if a.startswith('--')):
            parser.error('The CPU manifest owns inference/resource settings; do not mix legacy policy flags')
        if not args.fragment_cpu_manifest or not args.fragment_cpu_sha256:
            parser.error('CPU provider requires the explicit manifest and its SHA256')
        if args.fragment_cpu_reconcile and not args.fragment_cpu_inspect_lease:
            parser.error('Reconciliation requires the exact pending lease')
        if args.fragment_cpu_check and args.fragment_cpu_inspect_lease:
            parser.error('Choose check-only or ownership inspection')
        manifest=Manifest.load(args.fragment_cpu_manifest,args.fragment_cpu_sha256)
        manifest.verify(full=True)
        ns=namespace()
        if args.fragment_cpu_check:
            print(json.dumps({'schema':manifest.value['schema'],'manifest_sha256':manifest.hash,
                  'config':manifest.config,'limits':manifest.limits,'namespace':ns,
                  'status':'model_free_check_only','model_execution':'not_run'},sort_keys=True))
            return
        if args.fragment_cpu_inspect_lease:
            from .resources import InstanceLock
            data=args.data_dir.resolve()
            path=data/'jobs.sqlite3'
            if not path.is_file():raise ValueError('Ownership inspection requires the existing service database')
            inspection_lock=lock or InstanceLock(data/'instance.lock');inspection_lock.acquire()
            try:
                result=(reconcile_lease(path,args.fragment_cpu_inspect_lease,manifest,args.fragment_cpu_reconcile)
                        if args.fragment_cpu_reconcile else inspect_lease(path,args.fragment_cpu_inspect_lease,manifest))
                print(json.dumps(result,sort_keys=True))
            finally:
                if lock is None: inspection_lock.release()
            return
        from .settings import Settings
        from .app import create_app
        import uvicorn
        config=manifest.config
        settings=Settings(data_dir=args.data_dir,model_dir=Path(config['model_dir']),cpu_threads=2,
            process_memory_limit_bytes=manifest.limits['tree_rss_bytes'],
            disk_reserve_bytes=manifest.limits['disk_reserve_bytes'],
            max_audio_seconds=manifest.limits['max_source_frames']/16000,
            asr_backend='sensevoice',diarization_backend='nemotron',overlap_backend='none',
            native_diarization_model=config['native_diarization_model'],
            native_diarization_library=config['native_diarization_library'],native_diarization_threads=2,
            inference_device='cpu',language='auto',window_seconds=30,context_seconds=2,offline=True)
        print('EXPERIMENTAL finalized PCM16 CPU providers: native overlap retained; mixture words remain uncertain. '
              'Existing SenseVoice custom and Nemotron OpenMDW model licenses still apply.',file=sys.stderr)
        _run_app(settings,args,lock,fragment_provider_factory=CPUFactory(manifest))
        return
    if args.framemn:
        import sys
        print('EXPERIMENTAL FrameMN temporal candidates use CPU and a user-supplied local checkpoint. Only Knock > 0.01 with median9 is enabled; no event promotion. Code license does not establish checkpoint redistribution rights. See docs/FRAMEMN_EXPERIMENTAL_CANDIDATES.md.', file=sys.stderr)
    if args.device == 'vulkan':
        import sys
        print('WARNING: Vulkan path is untested on this CPU-only validation host. RAM watchdog does not measure VRAM; validate aggregate RAM+VRAM on target hardware.', file=sys.stderr)
    if args.asr_backend == 'sensevoice':
        import sys
        print('SenseVoice uses the custom FunASR Model License: https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE . Review its attribution, name, conduct and revision terms before use or redistribution.', file=sys.stderr)
    if args.diarization_backend=='nemotron':
        if not args.native_diarization_model or not args.native_diarization_library:
            parser.error('Nemotron requires --native-diarization-model and --native-diarization-library')
        import sys
        print('Nemotron-3 native is opt-in:8 local speaker slots per epoch;10ms output cadence is not accuracy. Model license: https://openmdw.ai/license/1-1/ .',file=sys.stderr)
    if args.overlap_backend!='none':
        if args.overlap_backend=='mossformer2_experimental' and not args.overlap_python:parser.error('MossFormer candidates require --overlap-python')
        if args.overlap_backend=='dicow_en_experimental' and (not args.dicow_python or not args.dicow_model_dir):parser.error('DiCoW candidates require --dicow-python and --dicow-model-dir')
        if args.overlap_backend=='moss_td_experimental' and (not args.moss_td_python or not args.moss_td_model_dir):parser.error('MOSS-TD candidates require --moss-td-python and --moss-td-model-dir')
        if args.overlap_backend=='moss_td_experimental' and args.moss_td_runtime=='cpp' and not args.moss_td_gguf:parser.error('MOSS-TD C++ runtime requires --moss-td-gguf')
        if args.overlap_backend=='moss_td_experimental' and args.moss_td_cpp_device=='vulkan':
            import sys
            print('WARNING: MOSS-TD Vulkan has not been executed on this CPU-only host. GPU output parity and aggregate RAM+dedicated VRAM must be measured on target hardware; the current watchdog measures RAM only.',file=sys.stderr)
        import sys
        print('EXPERIMENTAL overlap source candidates: quality unverified, minority speech can be missing, identities unresolved. Original mixture retained; no voice enrollment.',file=sys.stderr)
    from .settings import Settings
    from .app import create_app
    import uvicorn
    settings = Settings(data_dir=args.data_dir, model_dir=args.model_dir,
                        cpu_threads=args.threads, process_memory_limit_bytes=int(args.memory_limit_gib*2**30),
                        recording_capture_bytes=args.recording_capture_mib*1024**2,
                        recording_total_bytes=args.recording_total_mib*1024**2,
                        overlap_backend=args.overlap_backend,
                        dicow_python=str(args.dicow_python) if args.dicow_python else None,
                        dicow_model_dir=str(args.dicow_model_dir) if args.dicow_model_dir else None,
                        moss_td_python=str(args.moss_td_python) if args.moss_td_python else None,
                        moss_td_model_dir=str(args.moss_td_model_dir) if args.moss_td_model_dir else None,
                        moss_td_timeout_seconds=args.moss_td_timeout_seconds,
                        moss_td_max_seconds=args.moss_td_max_seconds,
                        moss_td_trigger=args.moss_td_trigger,
                        moss_td_runtime=args.moss_td_runtime,
                        moss_td_gguf_path=str(args.moss_td_gguf) if args.moss_td_gguf else None,
                        moss_td_cpp_device=args.moss_td_cpp_device,
                        overlap_python=str(args.overlap_python) if args.overlap_python else None,
                        overlap_source_dir=str(args.overlap_source_dir) if args.overlap_source_dir else None,
                        overlap_window_mode=args.overlap_window_mode,
                        diarization_backend=args.diarization_backend,
                        native_diarization_model=str(args.native_diarization_model) if args.native_diarization_model else None,
                        native_diarization_library=str(args.native_diarization_library) if args.native_diarization_library else None,
                        native_diarization_threads=args.native_diarization_threads,
                        inference_device=args.device, asr_backend=args.asr_backend, asr_model=args.asr_model,
                        whisper_cli=str(args.whisper_cli) if args.whisper_cli else None,
                        language=args.language, window_seconds=args.window_seconds,
                        speaker_match_threshold=args.speaker_threshold, speaker_match_margin=args.speaker_margin,
                        speaker_new_threshold=args.new_speaker_threshold, speaker_profile_strategy=args.speaker_profile_strategy,
                        speaker_calibration_file=args.speaker_calibration,
                        speaker_support_enabled=args.speaker_support_review,
                        native_owned_support_enabled=args.native_owned_support,
                        native_owned_cells_enabled=args.native_owned_cells,
                        native_owned_seam_cells_enabled=args.native_owned_seam_cells,
                        native_owned_core_seams_enabled=args.native_owned_core_seams,
                        native_owned_bounded_core_support_enabled=args.native_owned_bounded_core_support,
                        lexical_evidence_enabled=args.lexical_evidence,
                        native_owned_compact_gc_enabled=args.native_owned_compact_gc,
                        native_owned_receipt_gc_enabled=args.native_owned_receipt_gc,
                        moss_result_max_rows=args.moss_result_max_rows,
                        moss_result_max_rows_per_session=args.moss_result_max_rows_per_session,
                        moss_result_max_bytes=args.moss_result_max_bytes,
                        moss_result_max_bytes_per_session=args.moss_result_max_bytes_per_session,
                        recording_speaker_links_enabled=args.recording_speaker_links,
                        recording_primary_observation_policy=args.recording_primary_observation_policy,
                        recording_primary_sampling_policy=args.recording_primary_sampling_policy,
                        recording_primary_observation_interval_seconds=args.recording_primary_observation_interval_seconds,
                        recording_speaker_history_read_enabled=args.recording_speaker_history_read,
                        recording_primary_provenance_recording_bytes=args.recording_primary_provenance_recording_bytes,
                        recording_primary_provenance_global_bytes=args.recording_primary_provenance_global_bytes,
                        recording_speaker_model_path=str(args.recording_speaker_model.resolve()) if args.recording_speaker_model else None,
                        moss_jobs_enabled=args.moss_jobs,
                        moss_jobs_model_path=str(args.moss_jobs_model.resolve()) if args.moss_jobs_model else None,
                        moss_automatic_enabled=args.moss_automatic,
                        verified_identity_policy_file=args.verified_identity_policy,
                        reviewed_primary_persons_enabled=args.reviewed_primary_persons,
                        event_threshold=args.event_threshold,
                        framemn_enabled=args.framemn,
                        framemn_checkpoint_path=str(args.framemn_checkpoint.resolve()) if args.framemn_checkpoint else None,
                        framemn_profile=args.framemn_profile)
    _run_app(settings,args,lock)

if __name__ == '__main__':
    main()
