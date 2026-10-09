from __future__ import annotations
import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

import numpy as np
import soundfile as sf
from ..boundaries import overlap_matches
from ..events.provenance import EventArtifactHashes, effective_event_config

_LOCK = threading.Lock()
ROOT = Path(__file__).resolve().parents[2]

def candidate_triggered(config,turns,overlap_regions):
    backend=config.get('overlap_backend','none')
    if backend=='none':return False
    return bool(overlap_regions) or (backend=='moss_td_experimental' and
        config.get('moss_td_trigger','native_overlap')=='speech_probe' and bool(turns))

class ModelMissingError(RuntimeError):
    pass

class AudioAnalyzer:
    """Whisper.cpp + actual neural segmentation, speaker embeddings and AudioSet tagging.

    One call at a time per process. Service must additionally serialize workers.
    Input is 16 kHz mono PCM WAV, at most 60 s by default. Timestamps are relative.
    Embedding cosine scores and event posteriors require deployment calibration.
    """
    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        if self.config.get('overlap_backend','none') not in {'none','mossformer2_experimental','dicow_en_experimental','moss_td_experimental'}:
            raise ValueError('Unsupported experimental overlap backend')
        self.backend = self.config.get('asr_backend','whisper')
        if self.backend not in {'whisper','sensevoice'}: raise ValueError('asr_backend must be whisper or sensevoice')
        if self.backend=='sensevoice' and self.config.get('device','cpu')!='cpu':
            raise ValueError('The optional SenseVoice adapter is CPU-only; use device=cpu')
        self._sensevoice = None
        self.root = Path(self.config.get('model_dir', ROOT / 'models')).resolve()
        self.threads = min(4, max(1, int(self.config.get('cpu_threads', 4))))
        default_cli = ROOT / 'vendor/whisper.cpp/build/bin/whisper-cli'
        if os.name == 'nt':
            default_cli = ROOT / 'vendor/whisper.cpp/build/bin/Release/whisper-cli.exe'
        self.cli = Path(self.config.get('whisper_cli') or default_cli).resolve()
        self.closed = False
        self.paths = {
            'asr': self.root / self.config.get('asr_model', 'ggml-small-q5_1.bin'),
            'segmentation': self.root / 'sherpa-onnx-pyannote-segmentation-3-0/model.onnx',
            'speaker': self.root / 'speaker.onnx',
            'events': self.root / 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx',
            'labels': self.root / 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv',
        }
        if self.backend=='sensevoice':
            self.paths['asr']=self.root/'sensevoice/model.int8.onnx'
            self.paths['tokens']=self.root/'sensevoice/tokens.txt'
        required = [v for k,v in self.paths.items() if k!='segmentation' or self.config.get('diarization_backend','pyannote')=='pyannote'] + ([self.cli] if self.backend=='whisper' else [])
        missing = [str(x) for x in required if not x.is_file()]
        if missing:
            raise ModelMissingError('Missing offline model/runtime files: ' + ', '.join(missing) + '. Run scripts/setup_models.py (add --sensevoice for the optional licensed backend) and build whisper.cpp for Whisper.')
        digest=hashlib.sha256()
        with self.paths['speaker'].open('rb') as model:
            for block in iter(lambda:model.read(1024*1024),b''):digest.update(block)
        self._speaker_sha256=digest.hexdigest()
        self._event_artifact_hashes = EventArtifactHashes()
        self._support_diarization={}
        if self.config.get('speaker_support_enabled',False) and self.config.get('diarization_backend','pyannote')=='pyannote':
            try:
                import importlib.metadata
                with self.paths['segmentation'].open('rb') as model:
                    model_hash=hashlib.file_digest(model,'sha256').hexdigest()
                self._support_diarization={'diarization_model_sha256':model_hash,
                    'diarization_runtime':'sherpa-onnx',
                    'diarization_runtime_version':importlib.metadata.version('sherpa-onnx')}
            except (OSError,ValueError,ImportError):
                pass  # Missing optional review lineage cannot change ordinary inference.

    def _metadata_provenance(self, *, event_artifacts_required=True):
        """Configured policy and local artifact hashes, not evidence of inference."""
        sensevoice = self.backend == 'sensevoice'
        semantics = ('Native CTC token starts; interval ends are estimated, not forced alignment'
                     if sensevoice else 'Approximate Whisper token timestamps')
        warning = ('SenseVoice provides CTC token start anchors only; interval ends are estimated, not forced alignment. '
                   'Mixed-source word attribution is uncertain; overlapping activity is retained.' if sensevoice else
                   'Whisper token timestamps are approximate. Mixed-source word attribution is uncertain; overlapping activity is retained.')
        strategy = 'ctc_speaker_turns' if sensevoice else self.config.get('asr_strategy', 'speaker_turns')
        script = self.config.get('chinese_script', 'simplified')
        artifacts = ({**self._event_artifact_hashes.snapshot(self.paths),
                      'event_artifact_identity_status': 'hashed_local_files'}
                     if event_artifacts_required else
                     {'event_artifact_identity_status': 'not_checked_silence'})
        return {
            **artifacts,
            'event_policy_revision': 'audioset-window-v1',
            'event_window_seconds': 5.0, 'event_hop_seconds': 2.5, 'event_max_seconds': 5.0,
            'inference_revision': ('0.5.0-opt-in-stateful-diarization' if self.config.get('diarization_backend')=='nemotron' else '0.4.1-overlap-union-lexical-assembly'),
            'diarization_backend': self.config.get('diarization_backend','pyannote'),
            'asr_backend': self.backend,
            'overlap_backend':self.config.get('overlap_backend','none'),
            'asr_model_id': ('sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
                             if sensevoice else self.paths['asr'].name),
            'asr_model_license': ('FunASR Model Open Source License Agreement v1.1 (custom)'
                                  if sensevoice else 'MIT'),
            'asr_model_license_url': ('https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE'
                                      if sensevoice else 'https://github.com/openai/whisper/blob/main/LICENSE'),
            'timestamp_semantics': semantics,
            'requested_device': self.config.get('device', 'cpu'),
            'models': {'asr': self.paths['asr'].name, 'segmentation': ('Nemotron-3 native v0.2.0' if self.config.get('diarization_backend')=='nemotron' else 'pyannote/segmentation-3.0'),
                       'speaker': '3D-Speaker ERes2Net base zh-cn', 'events': 'Zipformer-small AudioSet 2024-04-15'},
            'asr_strategy': strategy,
            'chinese_script': script,
            'effective_config': {'asr_backend': self.backend, 'cpu_threads': self.threads,
                                 'diarization_backend':self.config.get('diarization_backend','pyannote'),
                                 'overlap_backend':self.config.get('overlap_backend','none'),
                                 'asr_strategy': strategy, 'chinese_script': script,
                                 'diarization_threshold': float(self.config.get('diarization_threshold', .7)),
                                 **effective_event_config(self.config),
                                 'max_segment_seconds': float(self.config.get('max_segment_seconds', 5))},
            'warnings': ['Speaker identities and event thresholds require calibration; scores are not calibrated probabilities.',
                         warning],
        }

    def close(self):
        self.closed = True
        self._sensevoice = None
        gc.collect()

    def analyze(self, wav_path: str, diarization: dict | None = None, analysis_options: dict | None = None) -> dict:
        if self.closed:
            raise RuntimeError('AudioAnalyzer is closed')
        with _LOCK:
            t = time.monotonic()
            info=sf.info(wav_path)
            max_seconds=min(60.,float(self.config.get('max_chunk_seconds',60)))
            if info.samplerate!=16000 or info.channels!=1 or info.frames<=0 or info.frames/16000>max_seconds+.01:
                raise ValueError('Expected nonempty bounded 16 kHz mono WAV (maximum 60 seconds)')
            samples, sr = sf.read(wav_path, dtype='float32', always_2d=False)
            if sr != 16000 or samples.ndim != 1:
                raise ValueError('Expected 16 kHz mono WAV')
            duration = len(samples) / sr
            if not 0 < duration <= float(self.config.get('max_chunk_seconds', 60)) + .01:
                raise ValueError('Audio chunk is empty or exceeds max_chunk_seconds')
            if not np.isfinite(samples).all():
                raise ValueError('Non-finite audio samples')
            silence_fast_path = float(np.max(np.abs(samples))) < float(self.config.get('event_silence_peak',1e-5)) and not (diarization and diarization.get('turns'))
            provenance = self._metadata_provenance(event_artifacts_required=not silence_fast_path)
            provenance.update(getattr(self,'_support_diarization',{}))
            if silence_fast_path:
                return {'segments': [], 'speaker_turns': [], 'events': [{'start': 0., 'end': duration, 'label': 'silence', 'score': 1., 'method': 'peak-amplitude fast path'}],
                        'metadata': {**provenance, 'duration_seconds': duration,
                                     'processing_seconds': round(time.monotonic()-t, 3),
                                     'stage_seconds': {'events': 0., 'diarization': 0., 'asr': 0.},
                                     'silence_fast_path': True, 'event_model_inference_executed': False, 'models_executed': [], 'model_execution': 'not_run_silence',
                                     'model_execution_note': 'Configured model provenance only; silence bypassed every model.',
                                     'asr_runtime': {}, 'language': None, 'languages': [],
                                     'asr_turn_batches': 0, 'asr_recovered_regions': 0,
                                     'diarization_turns': [], 'event_candidates': []}}
            stage = time.monotonic()
            events, event_details = self._events(samples)
            self._event_artifact_hashes.verify_unchanged(self.paths)
            timings = {'events': round(time.monotonic()-stage, 3)}
            # A narrow, configurable veto against confident non-speech hallucinations.
            nonspeech = bool(self.config.get('non_speech_gate',True) and event_details
                and max(x.get('speech_score',0.) for x in event_details)<.005
                and any(x['label']!='silence' and x['score']>.9 for x in events))
            gc.collect(); stage = time.monotonic()
            if diarization is not None:
                if self.config.get('diarization_backend')!='nemotron':raise ValueError('External turns require explicit native backend')
                turns=diarization.get('turns',[])
                if not isinstance(turns,list) or len(turns)>4096:raise ValueError('Invalid external speaker turns')
                for turn in turns:
                    if not isinstance(turn.get('speaker_local_id'),str) or not 0<=turn['start']<turn['end']<=duration+.01:
                        raise ValueError('Invalid external speaker interval')
                embeddings,dim=self._embed_turns(samples,turns)
                provenance.update(diarization.get('provenance',{}))
            elif self.config.get('diarization_backend')=='nemotron':
                raise ValueError('Native backend requires worker-supplied stateful diarization')
            else:turns, embeddings, dim = ([],{},512) if nonspeech else self._diarize(samples)
            timings['diarization'] = round(time.monotonic()-stage, 3)
            from ..overlap import overlapping_regions
            overlap_regions=overlapping_regions(turns,duration,capacity=2)
            for region in overlap_regions:
                if region['status']=='pending_separation':region['status']='unresolved_mixture_asr'
            overlap_summary={'regions':overlap_regions,'status':'unresolved_mixture_asr' if overlap_regions else 'none_detected',
                'separation_enabled':False,'original_mixture_retained':True,'time_axis':'analyzer_window',
                'note':'Detected simultaneous activity is retained. Current ASR decodes the mixture; separate words for every voice are not guaranteed.'}
            gc.collect()
            stage = time.monotonic()
            strategy='ctc_speaker_turns' if self.backend=='sensevoice' else self.config.get('asr_strategy','speaker_turns')
            if turns and strategy in {'speaker_turns','ctc_speaker_turns'} and len(turns)>1:
                transcript=self._transcribe_turns(samples,turns)
            else:
                transcript = self._transcribe(wav_path) if turns else {'transcription': [], 'result': {'language': None}}
            recovered = self._recover_missing_speech(samples, turns, transcript) if turns and self.backend=='whisper' else 0
            timings['asr'] = round(time.monotonic()-stage, 3)
            segments = align_tokens(transcript.get('transcription', []), turns, embeddings, duration, float(self.config.get('max_segment_seconds', 5)))
            for segment in segments:
                segment['asr_backend'] = self.backend
                segment['timestamp_kind'] = provenance['timestamp_semantics']
                segment['raw_text'] = segment['text']
                if self.backend == 'sensevoice':
                    segment['confidence'] = None
                    for unit in segment.get('_units', []): unit['confidence'] = None
            script=self.config.get('chinese_script','simplified')
            if script!='as_is':
                from opencc import OpenCC
                converter=OpenCC('t2s' if script=='simplified' else 's2t')
                for segment in segments:
                    segment['raw_text']=segment['text']; segment['text']=converter.convert(segment['text'])
                    for unit in segment.get('_units', []):
                        unit['raw_text']=unit['text']; unit['text']=converter.convert(unit['text'])
            overlap_candidates=None
            overlap_backend=self.config.get('overlap_backend','none')
            if candidate_triggered(self.config,turns,overlap_regions):
                stage=time.monotonic()
                from ..overlap_ownership import validate_candidates,failed_candidates
                try:
                    from ..overlap_candidates import analyze_overlap_candidates
                    detected_languages=sorted({language for language in [transcript.get('result',{}).get('language'),*transcript.get('recovered_languages',[])] if language})
                    candidate_config=dict(self.config,overlap_detected_languages=detected_languages)
                    options=analysis_options or {}
                    from ..boundaries import candidate_core_bounds
                    core_start,core_end=candidate_core_bounds(options,turns,duration)
                    candidate_config.update(candidate_core_start=core_start,candidate_core_end=core_end)
                    overlap_candidates=analyze_overlap_candidates(wav_path,[dict(turn) for turn in turns],self._transcribe,candidate_config)
                    validate_candidates(overlap_candidates,duration)
                    if script!='as_is' and overlap_backend!='moss_td_experimental':
                        for track in overlap_candidates.get('tracks',[]):
                            track['raw_text']=track.get('raw_text',track.get('text',''));track['text']=converter.convert(track.get('text',''))
                            for unit in track.get('_units',[]):
                                unit['raw_text']=unit.get('raw_text',unit['text']);unit['text']=converter.convert(unit['text'])
                except (InterruptedError,KeyboardInterrupt,SystemExit):raise
                except Exception as exc:
                    overlap_candidates=failed_candidates(overlap_regions,overlap_backend,f'{type(exc).__name__}: {exc}')
                timings['overlap_candidates']=round(time.monotonic()-stage,3)
                overlap_summary.update(candidate_generation_enabled=True,candidate_backend=overlap_backend,
                    inference_trigger=self.config.get('moss_td_trigger','native_overlap') if overlap_backend=='moss_td_experimental' else 'native_overlap',
                    native_overlap_detected=bool(overlap_regions),
                    candidate_tracks=len(overlap_candidates.get('tracks',[])),separation_enabled=True,
                    note='Experimental candidate sources are separate unverified evidence; the ordinary transcript remains mixture ASR.')
            gc.collect()
            return {'segments': segments, 'events': events, 'overlap_candidates':overlap_candidates,

                    'speaker_turns':[dict(turn,speaker_embedding=embeddings.get(turn['speaker_local_id'])) for turn in turns],
                    'metadata': {
                **provenance, 'asr_suppressed_as_nonspeech': bool(nonspeech and not turns),
                'non_speech_gate_triggered':nonspeech,
                'non_speech_gate_overridden_by_native_activity':bool(nonspeech and diarization is not None and turns),
                'overlapping_speech':overlap_summary,
                'asr_runtime': transcript.get('_runtime',{}),
                'speaker_embedding_dimension': dim,
                'speaker_support_producer':getattr(self,'_speaker_support_producer',None),
                'speaker_embedding_evidence': getattr(self,'_embedding_evidence',{}), 'speaker_embedding_metric': 'cosine (L2 normalized)',
                'speaker_embedding_version': 'eres2net-base-zh-cn-3dspeaker-16k-v1',
                'speaker_embedding_sha256':getattr(self,'_speaker_sha256',None),
                'asr_turn_batches': transcript.get('asr_turn_batches',1), 'asr_strategy': strategy, 'chinese_script': script, 'language': transcript.get('result', {}).get('language'), 'languages': sorted(set([x for x in [transcript.get('result',{}).get('language'),*transcript.get('recovered_languages',[])] if x])), 'asr_recovered_regions': recovered, 'duration_seconds': duration,
                'processing_seconds': round(time.monotonic()-t, 3), 'stage_seconds': timings, 'diarization_turns': turns,
                'event_candidates': event_details}}

    def _diarize(self, a):
        import sherpa_onnx as s
        ec = s.SpeakerEmbeddingExtractorConfig(model=str(self.paths['speaker']), num_threads=self.threads, provider='cpu')
        config = s.OfflineSpeakerDiarizationConfig(
            segmentation=s.OfflineSpeakerSegmentationModelConfig(
                pyannote=s.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(self.paths['segmentation'])),
                num_threads=self.threads, provider='cpu'),
            embedding=ec, clustering=s.FastClusteringConfig(num_clusters=-1,
                threshold=float(self.config.get('diarization_threshold', .7))),
            min_duration_on=.3, min_duration_off=.3)
        diarizer = s.OfflineSpeakerDiarization(config)
        turns = [{'start': max(0.,float(x.start)), 'end': min(len(a)/16000,float(x.end)),
                  'speaker_local_id': f'speaker_{x.speaker}'} for x in diarizer.process(a).sort_by_start_time()]
        del diarizer
        embeddings,dim=self._embed_turns(a,turns)
        return turns,embeddings,dim

    def _embed_turns(self,a,turns):
        """Pool clean evidence independently of the source of speaker activity."""
        import sherpa_onnx as s
        ec=s.SpeakerEmbeddingExtractorConfig(model=str(self.paths['speaker']),num_threads=self.threads,provider='cpu')
        extractor = s.SpeakerEmbeddingExtractor(ec)
        embeddings = {}
        self._embedding_evidence = {}
        self._speaker_support_producer = None
        try:
            if self.config.get('speaker_support_enabled',False):
                from audio_backend.speaker_support import producer_provenance
                self._speaker_support_producer = producer_provenance(a,turns,self._speaker_sha256,self.threads)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
            pass  # Optional review evidence must not alter embedding/matching behavior.
        from audio_backend.speaker_evidence import clean_speaker_audio
        for sid in sorted({x['speaker_local_id'] for x in turns}):
            clean,evidence=clean_speaker_audio(a,turns,sid)
            minimum=float(self.config.get('speaker_min_query_seconds',2.0))
            evidence['minimum_query_seconds']=minimum
            self._embedding_evidence[sid]=evidence
            if len(clean)<int(16000*minimum):continue
            stream = extractor.create_stream()
            stream.accept_waveform(sample_rate=16000, waveform=clean)
            stream.input_finished()
            if not extractor.is_ready(stream): continue
            emb = np.asarray(extractor.compute(stream), dtype=np.float32)
            norm = np.linalg.norm(emb)
            if norm > 1e-8 and np.isfinite(emb).all():
                embeddings[sid] = (emb/norm).tolist()
        dim = extractor.dim
        del extractor
        return embeddings, dim

    def _transcribe(self, wav):
        if self.backend=='sensevoice': return self._transcribe_sensevoice(wav)
        with tempfile.TemporaryDirectory(prefix='offline-asr-') as td:
            out = Path(td)/'transcript'
            cmd = [str(self.cli), '-m', str(self.paths['asr']), '-f', str(Path(wav).resolve()),
                   '-t', str(self.threads), '-l', str(self.config.get('language', 'auto')),
                   '-ojf', '-of', str(out), '-np', '-sns']
            if self.config.get('device', 'cpu') == 'cpu': cmd += ['-ng']
            env = dict(os.environ, OMP_NUM_THREADS=str(self.threads), OPENBLAS_NUM_THREADS=str(self.threads))
            p = subprocess.run(cmd, capture_output=True, timeout=float(self.config.get('asr_timeout_seconds', 900)), env=env)
            if p.returncode:
                raise RuntimeError('whisper.cpp failed: '+p.stderr.decode('utf8','replace')[-2500:])
            # Individual BPE tokens can split a UTF-8 codepoint; preserve their raw bytes until grouping.
            result=json.loads(out.with_suffix('.json').read_bytes().decode('utf8','surrogateescape'))
            result['_runtime']={'systeminfo':result.get('systeminfo'), 'backend_log':[line for line in p.stderr.decode('utf8','replace').splitlines() if any(term in line.lower() for term in ('vulkan','gpu','backend'))][-15:]}
            return result

    def _transcribe_sensevoice(self, wav):
        """Optional licensed CPU backend; native CTC start anchors, not token ends."""
        import sherpa_onnx as s
        if self._sensevoice is None:
            language=self.config.get('language','auto')
            if language not in {'auto','zh','en','ja','ko','yue'}:
                raise ValueError('SenseVoice language must be auto, zh, en, ja, ko or yue')
            self._sensevoice=s.OfflineRecognizer.from_sense_voice(
                model=str(self.paths['asr']),tokens=str(self.paths['tokens']),
                num_threads=self.threads,language=language,use_itn=True,provider='cpu')
        audio,sr=sf.read(wav,dtype='float32');duration=len(audio)/sr
        result={'transcription':[],'result':{'language':None},'recovered_languages':[],
                '_runtime':{'provider':'cpu','model':'SenseVoiceSmall int8','timestamp_note':'CTC start anchors only; no native durations'}}
        # Bound every CTC decode to <=30 seconds; align complete lexical units
        # in the overlap before committing the next core.
        previous=[]
        for core_start in np.arange(0.,duration,28.):
            core_end=min(duration,core_start+28.)
            start=max(0.,core_start-1.);end=min(duration,core_end+1.)
            stream=self._sensevoice.create_stream()
            stream.accept_waveform(sr,audio[int(start*sr):int(end*sr)])
            self._sensevoice.decode_stream(stream)
            native=stream.result
            language=str(native.lang).replace('<|','').replace('|>','')
            if result['result']['language'] is None:result['result']['language']=language
            result['recovered_languages'].append(language)
            if len(native.tokens)!=len(native.timestamps):
                raise RuntimeError('SenseVoice returned unmatched token/start-anchor counts')
            anchors=[float(t)+start for t in native.timestamps]
            tokens=[]
            for index,(text,anchor) in enumerate(zip(native.tokens,anchors)):
                next_anchor=anchors[index+1] if index+1<len(anchors) else end
                stop=min(end,max(anchor+.01,next_anchor))
                tokens.append({'id':index,'text':str(text),'offsets':{'from':round(anchor*1000),'to':round(stop*1000)},
                               '_ctc_anchor':True,'p':0.})
            tokens=join_ctc_words(tokens)
            current=[{'text':t['text'],'ownership_anchor':t['offsets']['from']/1000} for t in tokens]
            matches=overlap_matches(previous,current)
            seam=max((j for i,j in matches if previous[i].get('emitted')),default=None)
            selected=[]
            for j,(token,unit) in enumerate(zip(tokens,current)):
                keep=core_start<=unit['ownership_anchor']<core_end
                if seam is not None and unit['ownership_anchor']<core_end:keep=j>seam
                unit['emitted']=keep
                if keep:selected.append(token)
            previous=[u for u in current if u['ownership_anchor']>=core_end-2.]
            tokens=selected
            if tokens:
                result['transcription'].append({'text':''.join(x['text']for x in tokens),
                    'offsets':{'from':tokens[0]['offsets']['from'],'to':tokens[-1]['offsets']['to']},'tokens':tokens})
        return result

    def _transcribe_turns(self, samples, turns):
        """Fresh automatic language detection at independent speaker turns.

        Merge adjacent fragments of the same speaker and decode true overlapping
        acoustic unions once, without cropping the main voice under nested turns.
        Original independent speaker intervals still determine attribution.
        """
        regions=[]
        for turn in sorted(turns,key=lambda t:(t['start'],t['end'])):
            if regions and turn['start']<regions[-1]['end']-.15:
                # Nested/overlapping turns are a single acoustic mixture. Cutting
                # at their midpoint deletes the main voice under an interruption.
                # Decode the union once; original turns still own attribution.
                regions[-1]['end']=max(regions[-1]['end'],turn['end'])
                if regions[-1]['speaker_local_id']!=turn['speaker_local_id']:
                    regions[-1]['speaker_local_id']=None
            elif regions and regions[-1]['speaker_local_id']==turn['speaker_local_id'] and turn['start']<=regions[-1]['end']+.35:
                regions[-1]['end']=max(regions[-1]['end'],turn['end'])
            else:regions.append(dict(turn))
        # Do not merge brief turns from different voices: doing so can suppress a
        # short language switch. Calls stay sequential and bounded by the audio
        # duration; time/resource watchdogs remain enforced by the worker.
        duration=len(samples)/16000
        transcript={'transcription':[],'result':{'language':None},'recovered_languages':[], 'asr_turn_batches':len(regions)}
        with tempfile.TemporaryDirectory(prefix='asr-turns-') as td:
            for index,region in enumerate(regions):
                left=(regions[index-1]['end']+region['start'])/2 if index else 0.
                right=(region['end']+regions[index+1]['start'])/2 if index+1<len(regions) else duration
                start=max(0.,min(region['start'],left),region['start']-.35)
                end=min(duration,max(region['end'],right),region['end']+.35)
                if index and regions[index-1]['end']>region['start']:start=left
                if index+1<len(regions) and region['end']>regions[index+1]['start']:end=right
                if end<=start:continue
                path=Path(td)/f'{index}.wav';sf.write(path,samples[int(start*16000):int(end*16000)],16000,subtype='PCM_16')
                result=self._transcribe(str(path))
                transcript['_runtime']=result.get('_runtime',{})
                language=result.get('result',{}).get('language')
                if transcript['result']['language'] is None:transcript['result']['language']=language
                transcript['recovered_languages'].append(language)
                for segment in result.get('transcription',[]):
                    for item in [segment,*segment.get('tokens',[])]:
                        if 'offsets' in item:item['offsets']={k:round(v+start*1000) for k,v in item['offsets'].items()}
                    transcript['transcription'].append(segment)
        return transcript

    def _recover_missing_speech(self, samples, turns, transcript):
        """Retry substantial neural-speech intervals omitted by global language decoding.

        Language is detected afresh for each missing interval. This improves language
        switches; it does not assert that all code-switching speech is recognized.
        """
        if not self.config.get('recover_missing_speech', True): return 0
        coverage = [(x.get('offsets',{}).get('from',0)/1000, x.get('offsets',{}).get('to',0)/1000)
                    for x in transcript.get('transcription',[]) if x.get('text','').strip()]
        gaps=[]
        for turn in turns:
            intervals=[(turn['start'],turn['end'])]
            for c,d in coverage:
                remaining=[]
                for a,b in intervals:
                    if d<=a or c>=b: remaining.append((a,b)); continue
                    if c>a: remaining.append((a,c))
                    if d<b: remaining.append((d,b))
                intervals=remaining
            gaps.extend((a,b) for a,b in intervals if b-a>=2.)
        # At most four sequential decodes per bounded chunk; caller watchdog still applies.
        recovered=0
        for a,b in sorted(gaps,key=lambda x:x[1]-x[0],reverse=True)[:4]:
            if any(c<=a+.1 and d>=b-.1 for c,d in coverage):continue
            start=max(0.,a-.15);end=min(len(samples)/16000,b+.15)
            with tempfile.TemporaryDirectory(prefix='asr-recovery-') as td:
                path=Path(td)/'speech.wav';sf.write(path,samples[int(start*16000):int(end*16000)],16000,subtype='PCM_16')
                retry=self._transcribe(str(path))
                transcript.setdefault('recovered_languages',[]).append(retry.get('result',{}).get('language'))
            for seg in retry.get('transcription',[]):
                for item in [seg,*seg.get('tokens',[])]:
                    if 'offsets' in item:
                        item['offsets']={key:round(value+start*1000) for key,value in item['offsets'].items()}
                mid=sum(seg.get('offsets',{}).values())/2000
                if a-.15<=mid<=b+.15:
                    transcript.setdefault('transcription',[]).append(seg)
            recovered+=1;coverage.append((a,b))
        transcript['transcription'].sort(key=lambda x:x.get('offsets',{}).get('from',0))
        return recovered

    def _events(self, a):
        from ..events import detect_events
        return detect_events(a,self.paths['events'],self.paths['labels'],self.threads,self.config)


def join_ctc_words(tokens):
    """Keep contiguous Latin subword pieces together before speaker attribution."""
    joined=[]
    for token in tokens:
        text=token['text']
        previous=joined[-1]['text'] if joined else ''
        latin_piece=bool(text and not text[0].isspace() and all(ord(c)<128 for c in text)
                         and (text[0].isalnum() or text[0] in "'.,!?;:-"))
        latin_previous=bool(previous and ord(previous[-1])<128 and
                            (previous[-1].isalnum() or previous[-1] in "'.-"))
        if latin_piece and latin_previous:
            joined[-1]['text']+=text
            joined[-1]['offsets']['to']=token['offsets']['to']
        else:joined.append(dict(token,offsets=dict(token['offsets'])))
    return joined


def event_category(label):
    l=label.lower()
    if l in {'rain','raindrop','rain on surface','thunderstorm'}: return 'rain'
    if l in {'subway, metro, underground'}: return 'subway'
    if l in {'vehicle','car','engine','idling','accelerating, revving, vroom','traffic noise, roadway noise'}: return 'driving'
    if 'door' in l or l in {'knock','slam'}: return 'door'
    if l == 'music' or l.endswith(' music'): return 'music'
    return None


def align_tokens(asr, turns, embeddings, duration, max_segment_seconds=5.):
    """Split ASR text at independently detected speaker turns using token timestamps."""
    output=[]
    for segment in asr:
        tokens=[x for x in segment.get('tokens',[]) if int(x.get('id',999999))<50257 and x.get('text')]
        if not tokens:
            tokens=[{'text':segment.get('text',''), 'offsets':segment.get('offsets',{}), 'p':0.}]
        # Join byte-level BPE fragments before speaker assignment; never split a Unicode character.
        joined=[]; pending=None
        for token in tokens:
            if pending is None: pending=dict(token)
            else:
                pending['text']+=token['text']
                pending['offsets']={'from':pending.get('offsets',{}).get('from',0), 'to':token.get('offsets',{}).get('to',0)}
                pending['p']=min(float(pending.get('p',0)),float(token.get('p',0)))
            try:
                pending['text'].encode('utf8','surrogateescape').decode('utf8')
            except UnicodeDecodeError: continue
            joined.append(pending); pending=None
        if pending: joined.append(pending)
        # Latin BPE pieces belong to one lexical word before ownership and
        # speaker attribution; CJK characters remain independent lexical units.
        joined=join_ctc_words(joined)
        for token in joined:
            offsets=token.get('offsets',segment.get('offsets',{}))
            start=max(0.,min(duration,float(offsets.get('from',0))/1000))
            end=max(start,min(duration,float(offsets.get('to',start*1000))/1000))
            if end<=start:
                # Whisper occasionally emits zero-duration final tokens; preserve text with explicitly approximate timing.
                start=max(0.,min(start,duration-.01)); end=min(duration,start+.01)
            mid=start if token.get('_ctc_anchor') else (start+end)/2
            active={x['speaker_local_id'] for x in turns if x['start']-.03<=mid<=x['end']+.03}
            sid=next(iter(active)) if len(active)==1 else None
            if token.get('_ctc_anchor') and sid is not None:
                # Do not portray a final CTC token as lasting through trailing silence.
                bounds=[x['end'] for x in turns if x['speaker_local_id']==sid and x['start']-.03<=start<=x['end']+.03]
                if bounds:end=max(start+.001,min(end,max(bounds)))
            p=max(0.,min(1.,float(token.get('p',0.))))
            unit={'start':start,'end':end,'text':token['text'],
                  'raw_text':token['text'], 'confidence':p,
                  'ownership_anchor':start if token.get('_ctc_anchor') else (start+end)/2,
                  'anchor_kind':'ctc_emission' if token.get('_ctc_anchor') else 'approximate_token_interval'}
            if output and output[-1]['speaker_local_id']==sid and start<=output[-1]['end']+.7 and end-output[-1]['start']<=max_segment_seconds:
                output[-1]['text']+=token['text'];output[-1]['end']=max(output[-1]['end'],end)
                output[-1]['_ps'].append(p);output[-1]['_units'].append(unit)
            else:
                output.append({'start':start,'end':end,'text':token['text'],'speaker_local_id':sid,
                               'speaker_embedding':embeddings.get(sid),'_ps':[p], '_units':[unit]})
    for x in output:
        for unit in x['_units']:
            unit['text']=unit['text'].encode('utf8','surrogateescape').decode('utf8','replace')
            unit['raw_text']=unit['text']
        x['text']=x['text'].encode('utf8','surrogateescape').decode('utf8','replace').strip()
        x['confidence']=sum(x['_ps'])/len(x.pop('_ps'))
        x['start']=round(x['start'],3);x['end']=round(x['end'],3)
    return [x for x in output if x['text'] and x['end']>x['start']]
