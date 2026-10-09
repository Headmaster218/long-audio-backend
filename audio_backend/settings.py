from dataclasses import dataclass, field
from pathlib import Path
import os
import math

@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('AUDIO_DATA_DIR', './audio-data')).resolve())
    model_dir: Path = field(default_factory=lambda: Path(os.getenv('AUDIO_MODEL_DIR', './models')).resolve())
    api_key: str | None = field(default_factory=lambda: os.getenv('AUDIO_API_KEY'))
    window_seconds: float = 30.0
    context_seconds: float = 2.0
    max_upload_bytes: int = 8 * 1024**3
    disk_reserve_bytes: int = 2 * 1024**3
    max_pending_chunks: int = 64
    max_sessions: int = 1000
    max_active_uploads: int = 1
    max_audio_seconds: float = 7 * 24 * 3600
    process_memory_limit_bytes: int = 6 * 1024**3
    cpu_threads: int = 4
    inference_device: str = 'cpu'
    asr_backend: str = 'whisper'
    diarization_backend: str = 'pyannote'
    native_diarization_model: str | None = None
    native_diarization_library: str | None = None
    native_diarization_threads: int = 2
    overlap_backend: str = 'none'
    overlap_python: str | None = None
    overlap_source_dir: str | None = None
    overlap_window_mode: str = 'official_2s'
    overlap_max_seconds: float = 12.0
    overlap_context_seconds: float = 1.0
    overlap_threads: int = 2
    overlap_timeout_seconds: float = 300.0
    dicow_python: str | None = None
    dicow_model_dir: str | None = None
    dicow_min_enrollment_seconds: float = 2.0
    dicow_timeout_seconds: float = 300.0
    dicow_memory_limit_bytes: int = int(3.5*1024**3)
    moss_td_python: str | None = None
    moss_td_model_dir: str | None = None
    moss_td_timeout_seconds: float = 300.0
    moss_td_memory_limit_bytes: int = int(3.5*1024**3)
    moss_td_max_support_seconds: float = 20.0
    moss_td_max_seconds: float = 40.0
    moss_td_trigger: str = 'native_overlap'
    moss_td_runtime: str = 'transformers'
    moss_td_gguf_path: str | None = None
    moss_td_cpp_device: str = 'cpu'
    asr_model: str = 'ggml-small-q5_1.bin'
    whisper_cli: str | None = None
    language: str = 'auto'
    event_threshold: float = .25
    speaker_calibration_file: str | Path | None = None
    speaker_calibration_profile: dict | None = field(default=None,init=False,repr=False)
    speaker_centroid_threshold: float | None = field(default=None,init=False)
    speaker_profile_strategy: str = 'max'
    speaker_min_clean_seconds: float = 2.0
    speaker_min_query_seconds: float = 2.0
    speaker_match_threshold: float = .75
    speaker_new_threshold: float = .45
    speaker_match_margin: float = .10
    max_speakers_per_session: int = 256
    max_speaker_observations_per_group: int = 4096
    max_matched_speaker_observations_per_group: int = 512
    max_speaker_examples_per_person: int = 8
    max_speaker_templates_total: int = 4096
    max_speaker_observations_total: int = 8192
    offline: bool = True
    ffmpeg: str = 'ffmpeg'
    ffprobe: str = 'ffprobe'
    start_worker: bool = True

    def __post_init__(self):
        self.data_dir = Path(self.data_dir).resolve()
        self.model_dir = Path(self.model_dir).resolve()
        if self.speaker_calibration_file is not None:
            from .speaker_calibration import load_profile
            self.speaker_calibration_profile=load_profile(self.speaker_calibration_file)
            policy=self.speaker_calibration_profile['policy']
            self.speaker_match_threshold=policy['single_threshold']
            self.speaker_centroid_threshold=policy['verified_centroid_threshold']
            self.speaker_new_threshold=policy['novelty_threshold']
            self.speaker_match_margin=policy['ambiguity_margin']
            self.speaker_profile_strategy='centroid'
            self.speaker_min_clean_seconds=policy['minimum_new_enrollment_seconds']
            self.speaker_min_query_seconds=policy['minimum_matching_query_seconds']
        if not isinstance(self.process_memory_limit_bytes,int) or not 256*1024**2 <= self.process_memory_limit_bytes <= 10*1024**3:
            raise ValueError('Memory budget must be 256 MiB to 10 GiB; use 6 GiB on this host')
        for name in ('max_upload_bytes','max_pending_chunks','max_sessions','max_speakers_per_session','max_speaker_observations_per_group','max_matched_speaker_observations_per_group','max_speaker_examples_per_person','max_speaker_templates_total','max_speaker_observations_total'):
            if not isinstance(getattr(self,name),int) or getattr(self,name)<=0: raise ValueError(f'{name} must be a positive integer')
        if not isinstance(self.disk_reserve_bytes,int) or self.disk_reserve_bytes<0: raise ValueError('Disk reserve must be nonnegative')
        if not isinstance(self.cpu_threads,int) or not 1<=self.cpu_threads<=4: raise ValueError('cpu_threads must be 1 to 4')
        if self.diarization_backend not in ('pyannote','nemotron'):raise ValueError('diarization_backend must be pyannote or nemotron')
        if not isinstance(self.native_diarization_threads,int) or not 1<=self.native_diarization_threads<=2:raise ValueError('Native diarization CPU limit is1–2')
        if self.diarization_backend=='nemotron' and (not self.native_diarization_model or not self.native_diarization_library):raise ValueError('Native diarization requires explicit local model and library paths')
        if self.overlap_backend not in ('none','mossformer2_experimental','dicow_en_experimental','moss_td_experimental'):raise ValueError('Invalid overlap backend')
        if self.overlap_backend=='mossformer2_experimental' and not self.overlap_python:raise ValueError('Experimental overlap backend requires explicit overlap_python')
        if self.overlap_window_mode not in ('official_2s','direct_bounded'):raise ValueError('Invalid overlap window mode')
        if not isinstance(self.overlap_threads,int) or not 1<=self.overlap_threads<=2:raise ValueError('Overlap CPU limit is1–2')
        if not math.isfinite(self.overlap_max_seconds) or not 0<self.overlap_max_seconds<=12:raise ValueError('Overlap total per-window audio budget must be positive and<=12seconds')
        if not math.isfinite(self.overlap_context_seconds) or not 0<=self.overlap_context_seconds<=2:raise ValueError('Overlap context must be0–2seconds')
        if not math.isfinite(self.overlap_timeout_seconds) or not 1<=self.overlap_timeout_seconds<=600:raise ValueError('Overlap timeout must be1–600seconds')
        if not math.isfinite(self.dicow_min_enrollment_seconds) or not 2<=self.dicow_min_enrollment_seconds<=30:raise ValueError('DiCoW clean evidence minimum must be2–30seconds')
        if not math.isfinite(self.dicow_timeout_seconds) or not 30<=self.dicow_timeout_seconds<=600:raise ValueError('DiCoW timeout must be30–600seconds')
        if not isinstance(self.dicow_memory_limit_bytes,int) or not 2*1024**3<=self.dicow_memory_limit_bytes<=int(4.5*1024**3):raise ValueError('DiCoW memory budget must be2–4.5GiB')
        if not math.isfinite(self.moss_td_timeout_seconds) or not 30<=self.moss_td_timeout_seconds<=600:raise ValueError('MOSS-TD timeout must be30–600seconds')
        if not isinstance(self.moss_td_memory_limit_bytes,int) or not 2*1024**3<=self.moss_td_memory_limit_bytes<=int(4.5*1024**3):raise ValueError('MOSS-TD child memory budget must be2–4.5GiB; aggregate guard remains independent')
        if not math.isfinite(self.moss_td_max_support_seconds) or not 1<=self.moss_td_max_support_seconds<=20:raise ValueError('MOSS-TD support must be1–20seconds')
        if not math.isfinite(self.moss_td_max_seconds) or not 1<=self.moss_td_max_seconds<=60:raise ValueError('MOSS-TD per-core support budget must be1–60seconds')
        if self.moss_td_trigger not in ('native_overlap','speech_probe'):raise ValueError('MOSS-TD trigger must be native_overlap or speech_probe')
        if self.moss_td_runtime not in ('transformers','cpp'):raise ValueError('MOSS-TD runtime must be transformers or cpp')
        if self.moss_td_cpp_device not in ('cpu','vulkan'):raise ValueError('MOSS-TD C++ device must be cpu or vulkan')
        if self.moss_td_cpp_device!='cpu' and self.moss_td_runtime!='cpp':raise ValueError('MOSS-TD Vulkan requires the C++ runtime')
        if self.overlap_backend!='none':
            from .overlap_candidates import overlap_readiness
            readiness=overlap_readiness(self.inference_config())
            if not readiness['ready']:raise ValueError('Experimental overlap backend configuration error: '+', '.join(readiness['errors']))
        if self.asr_backend not in ('whisper','sensevoice'): raise ValueError('ASR backend must be whisper or sensevoice')
        if self.asr_backend == 'sensevoice' and self.inference_device != 'cpu': raise ValueError('SenseVoice adapter supports CPU only; use Whisper for Vulkan')
        if self.inference_device not in ('cpu','vulkan'): raise ValueError('Device must be cpu or vulkan')
        if not self.asr_model or '/' in self.asr_model or '\\' in self.asr_model or not self.asr_model.endswith('.bin'): raise ValueError('asr_model must be a local model filename ending in .bin')
        if not self.language or len(self.language)>16 or not self.language.replace('-','').isalpha(): raise ValueError('Invalid language code')
        if not math.isfinite(self.max_audio_seconds) or self.max_audio_seconds<=0: raise ValueError('max_audio_seconds must be finite and positive')
        if not 1 <= self.window_seconds <= 60:
            raise ValueError('window_seconds must be between 1 and 60')
        if not 0 <= self.context_seconds <= 5 or self.window_seconds + 2*self.context_seconds > 60:
            raise ValueError('Window plus context must not exceed 60 seconds')
        if self.max_active_uploads != 1:
            raise ValueError('This single-process implementation supports one active upload')
        if not 0 <= self.event_threshold <= 1: raise ValueError('Event threshold must be between zero and one')
        if self.max_speaker_examples_per_person>8:raise ValueError('At most 8 explicit speaker examples per person')
        if self.speaker_profile_strategy not in ('max','centroid'):
            raise ValueError('speaker_profile_strategy must be max or centroid')
        if not math.isfinite(self.speaker_min_query_seconds) or not .5<=self.speaker_min_query_seconds<=self.speaker_min_clean_seconds:
            raise ValueError('speaker_min_query_seconds must be .5 to minimum enrollment duration')
        if not math.isfinite(self.speaker_min_clean_seconds) or not 2 <= self.speaker_min_clean_seconds <= 20:
            raise ValueError('speaker_min_clean_seconds must be between 2 and 20')
        if not 0 <= self.speaker_new_threshold <= self.speaker_match_threshold <= 1 or not 0 <= self.speaker_match_margin <= 1:
            raise ValueError('Invalid speaker matching threshold')

    def inference_config(self):
        config={'model_dir': str(self.model_dir), 'offline': self.offline,
                'data_dir':str(self.data_dir),'disk_reserve_bytes':self.disk_reserve_bytes,
                'process_memory_limit_bytes':self.process_memory_limit_bytes,
                'overlap_backend':self.overlap_backend,'overlap_python':self.overlap_python,
                'overlap_source_dir':self.overlap_source_dir,'overlap_window_mode':self.overlap_window_mode,
                'overlap_max_seconds':self.overlap_max_seconds,'overlap_context_seconds':self.overlap_context_seconds,
                'overlap_threads':self.overlap_threads,'overlap_timeout_seconds':self.overlap_timeout_seconds,
                'dicow_python':self.dicow_python,'dicow_model_dir':self.dicow_model_dir,
                'dicow_min_enrollment_seconds':self.dicow_min_enrollment_seconds,
                'dicow_timeout_seconds':self.dicow_timeout_seconds,'dicow_memory_limit_bytes':self.dicow_memory_limit_bytes,
                'moss_td_python':self.moss_td_python,'moss_td_model_dir':self.moss_td_model_dir,
                'moss_td_timeout_seconds':self.moss_td_timeout_seconds,
                'moss_td_memory_limit_bytes':self.moss_td_memory_limit_bytes,
                'moss_td_max_support_seconds':self.moss_td_max_support_seconds,'moss_td_max_seconds':self.moss_td_max_seconds,
                'moss_td_trigger':self.moss_td_trigger,
                'moss_td_runtime':self.moss_td_runtime,'moss_td_gguf_path':self.moss_td_gguf_path,
                'moss_td_cpp_device':self.moss_td_cpp_device,
                'diarization_backend':self.diarization_backend,
                'native_diarization_model':self.native_diarization_model,
                'native_diarization_library':self.native_diarization_library,
                'native_diarization_threads':self.native_diarization_threads,
                'device': self.inference_device, 'asr_backend': self.asr_backend, 'asr_model': self.asr_model, 'language':self.language,
                'cpu_threads': self.cpu_threads, 'event_threshold': self.event_threshold,
                'speaker_min_query_seconds':self.speaker_min_query_seconds,
                'max_segment_seconds': 5.0, 'max_chunk_seconds': 60}

        if self.whisper_cli: config['whisper_cli']=str(self.whisper_cli)
        return config
