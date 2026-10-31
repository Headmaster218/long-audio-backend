"""Explicit fresh primary-only capacity profile, using existing local assets."""
from pathlib import Path


def configuration(workspace,data):
    root=Path(workspace).resolve()
    return dict(data_dir=str(Path(data).resolve()),model_dir=str(root/'audio-recovery-model-layout-20261023b'),
        strict_primary_enabled=True,strict_primary_policy='strict-primary-payload-v2',
        strict_payload_source_storage='shared-source-v1',strict_primary_lifetime_records=32768,
        strict_primary_receipt_records=8192,lexical_evidence_session_bytes=256*1024**2,
        recording_capture_bytes=4*1024**3,recording_total_bytes=8*1024**3,
        recording_disk_reserve_bytes=11*1024**3//2,
        asr_backend='sensevoice',diarization_backend='nemotron',language='en',
        native_diarization_model=str(root/'audio-model-recovery-20261023b/models/native-diarization/Nemotron-3-Diarization.q8_0.gguf'),
        native_diarization_library=str(root/'audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_asr_c.so'),
        recording_speaker_model_path=str(root/'audio-recovery-model-layout-20261023b/speaker.onnx'),
        speaker_support_enabled=True,speaker_support_max_records=65536,native_owned_support_enabled=True,native_owned_cells_enabled=True,
        native_owned_core_seams_enabled=True,native_owned_bounded_core_support_enabled=True,
        lexical_evidence_enabled=True,recording_speaker_links_enabled=True,
        recording_primary_observation_policy='primary-clean-history-v1',native_owned_compact_gc_enabled=False,
        native_owned_receipt_gc_enabled=False,start_worker=True,window_seconds=5.25,context_seconds=0.,
        cpu_threads=2,native_diarization_threads=2,process_memory_limit_bytes=6*1024**3)
