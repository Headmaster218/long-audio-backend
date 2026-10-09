"""Harness-only actual neural-call meters. No model import until explicit factory.

The original analyzer scheduling/turn preparation is unchanged. Attempt budgets
are checked immediately before compute/decode; failures are never refunded.
"""
from contextlib import contextmanager
import copy
import hashlib
from .fragment_runtime_contracts import RATE


class Stream:
    def __init__(self, raw):
        self.raw, self.frames = raw, 0

    def accept_waveform(self, *args, **kwargs):
        waveform = kwargs.get('waveform', args[1] if len(args) > 1 else None)
        self.frames = len(waveform)
        import numpy as np
        self.input_receipt = {'sample_rate': kwargs.get('sample_rate', args[0] if args else None),
            'sample_count': self.frames, 'pcm_sha256': hashlib.sha256(np.ascontiguousarray(waveform, dtype='<f4').tobytes()).hexdigest(),
            'encoding': 'mono IEEE754 float32 little-endian; exact compute input'}
        return self.raw.accept_waveform(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.raw, name)


class MeteredEngine:
    def __init__(self, raw, kind, meter):
        self.raw, self.kind, self.meter = raw, kind, meter

    def create_stream(self):
        return Stream(self.raw.create_stream())

    def is_ready(self, stream):
        return self.raw.is_ready(stream.raw)

    def _invoke(self, method, stream):
        ticket = self.meter(self.kind, stream.frames, stream.input_receipt)
        ticket['execution_started'] = True
        try:
            result = getattr(self.raw, method)(stream.raw)
        except BaseException as exc:
            ticket.update(status='failed', failure={'type': type(exc).__name__, 'message': str(exc)[:1000]})
            raise
        ticket['status'] = 'completed'
        return result

    def decode_stream(self, stream):
        return self._invoke('decode_stream', stream)

    def compute(self, stream):
        return self._invoke('compute', stream)

    def __getattr__(self, name):
        return getattr(self.raw, name)


def make_metered_analyzer(config, budget, guard):
    # This factory is an inference entry point and MUST only be called by an
    # explicitly approved execution mode, never freeze/test protocol commands.
    from .inference.analyzer import AudioAnalyzer
    if (config.get('asr_backend') != 'sensevoice' or config.get('diarization_backend') != 'nemotron'
        or config.get('device') != 'cpu' or config.get('cpu_threads') != 2
        or config.get('language') != 'auto' or config.get('overlap_backend') != 'none'
        or config.get('chinese_script') not in ('as_is', 'simplified', 'traditional')
        or config.get('speaker_support_enabled', False)):
        raise ValueError('Harness requires frozen SenseVoice/native CPU2 mixture policy, no identity bank')

    class MeteredAnalyzer(AudioAnalyzer):
        def analyze(self, *args, **kwargs):
            self._request_counts = {}
            self._request_records = []
            result = super().analyze(*args, **kwargs)
            # Preserve raw result unchanged. Counters go in the parent evidence.
            return result

        def _reserve(self, kind, frames, input_receipt):
            limits = {'sensevoice': (32, 60*RATE), 'embedding': (8, 160*RATE),
                      'zipformer': (12, 60*RATE)}
            calls, audio = self._request_counts.get(kind, (0, 0))
            maximum, bound = limits[kind]
            if calls+1 > maximum or audio+frames > bound:
                raise ValueError(f'Per-analyzer {kind} call/audio budget exhausted')
            if kind == 'embedding' and frames > 20*RATE:
                raise ValueError('ERes2Net clean support exceeds frozen 20s bound')
            self.guard.check()
            if kind == 'embedding':
                selection = self._embedding_selection
                support = selection['support']
                if support['pooled_pcm_sha256'] != input_receipt['pcm_sha256'] or support['pooled_sample_count'] != frames:
                    raise ValueError('Actual ERes2Net compute input differs from captured clean-speaker selection')
            ticket = self.budget.reserve(kind, frames)
            ticket['input'] = copy.deepcopy(input_receipt)
            if kind == 'embedding':
                ticket['selection'] = copy.deepcopy(selection)
            self._request_counts[kind] = calls+1, audio+frames
            self._request_records.append(ticket)
            return ticket

        def _transcribe_sensevoice(self, wav):
            if self._sensevoice is None:
                import sherpa_onnx as s
                self.guard.check()
                original = s.OfflineRecognizer.from_sense_voice(
                    model=str(self.paths['asr']), tokens=str(self.paths['tokens']), num_threads=self.threads,
                    language=self.config['language'], use_itn=True, provider='cpu')
                self._sensevoice = MeteredEngine(original, 'sensevoice', self._reserve)
            return super()._transcribe_sensevoice(wav)

        def _embed_turns(self, samples, turns):
            if len({turn['speaker_local_id'] for turn in turns}) > 8:
                raise ValueError('Native local speaker capacity exceeded')
            import sherpa_onnx as s
            # This offline process is single-call. Restore the factory even on
            # exceptions; no installed source or external environment changes.
            from . import speaker_evidence
            from .fragment_model_protocol import sha256
            factory = s.SpeakerEmbeddingExtractor
            select = speaker_evidence.clean_speaker_audio
            def capture_selection(samples, turns, local_id, *args, **kwargs):
                clean, evidence = select(samples, turns, local_id, *args, **kwargs)
                support = evidence.get('pooled_support')
                if support is None:
                    raise ValueError('Selected support helper omitted its input audit; no invented intervals')
                self._embedding_selection = {'speaker_local_id': local_id, 'support': copy.deepcopy(support),
                    'selection_implementation_sha256': sha256(speaker_evidence.__file__),
                    'coordinate_contract': 'exact logical read PCM16 decoded to float32; no physical upload decode contract'}
                return clean, evidence
            speaker_evidence.clean_speaker_audio = capture_selection
            s.SpeakerEmbeddingExtractor = lambda *args, **kwargs: MeteredEngine(
                factory(*args, **kwargs), 'embedding', self._reserve)
            try:
                return super()._embed_turns(samples, turns)
            finally:
                s.SpeakerEmbeddingExtractor = factory
                speaker_evidence.clean_speaker_audio = select

        def _events(self, samples):
            import sherpa_onnx as s
            from .events.detector import detect_events
            self.guard.check()
            engine = s.AudioTagging(s.AudioTaggingConfig(model=s.AudioTaggingModelConfig(
                zipformer=s.OfflineZipformerAudioTaggingModelConfig(model=str(self.paths['events'])),
                num_threads=max(1, min(2, self.threads)), provider='cpu'),
                labels=str(self.paths['labels']), top_k=527))
            start_record = len(self._request_records)
            result = detect_events(samples, self.paths['events'], self.paths['labels'], self.threads,
                                 self.config, tagger=MeteredEngine(engine, 'zipformer', self._reserve))
            calls = [r for r in self._request_records[start_record:] if r['kind'] == 'zipformer']
            windows = [w for w in result[1] if w.get('model_inference_executed')]
            if len(calls) != len(windows):
                raise ValueError('Event compute count differs from raw candidate-window evidence')
            for call, window in zip(calls, windows):
                a, b = round(window['start']*RATE), round(window['end']*RATE)
                call['real_read_support_frames'] = [a, b]
                call['inference_padding_frames'] = call['frames']-(b-a)
                call['padding_is_source_owned_audio'] = False
            return result

    analyzer = MeteredAnalyzer(config)
    analyzer.budget, analyzer.guard = budget, guard
    return analyzer
