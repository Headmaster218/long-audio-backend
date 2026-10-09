"""CPU-call receipts; no inference imports until the admitted factory.

Budget reserve/update/complete must durably acknowledge before returning.
Failed or abandoned attempts are never refunded.
"""
import copy
import hashlib
import math
from pathlib import Path
import wave

from .fragment_runtime_contracts import RATE

POLICY = 'fragment-cpu-actual-call-meter-v1'
INPUT_ENCODING = 'mono IEEE754 float32 little-endian; exact compute input'


def plan_asr_inputs(turns, read_frames, *, max_read_frames=34 * RATE, max_calls=32):
    """Mirror AudioAnalyzer crops/28s CTC cores, including float/int flooring.

    Bound one read, never recording length. Reject overflow without truncation.
    """
    if (type(max_read_frames) is not int or not 0 < max_read_frames <= 60 * RATE
        or type(read_frames) is not int or not 0 < read_frames <= max_read_frames
        or type(max_calls) is not int or not 0 < max_calls <= 4096
        or not isinstance(turns, list) or len(turns) > 4096):
        raise ValueError('Invalid read or ASR inventory bound')
    duration = read_frames / RATE
    for turn in turns:
        if (not isinstance(turn, dict) or not isinstance(turn.get('speaker_local_id'), str)
            or not turn['speaker_local_id'] or len(turn['speaker_local_id']) > 512
            or any(type(turn.get(k)) not in (int, float) or not math.isfinite(turn[k]) for k in ('start', 'end'))
            or not 0 <= turn['start'] < turn['end'] <= duration):
            raise ValueError('Invalid native turn in analyzer read')
    supports = []
    if len(turns) == 1:
        supports = [(0, read_frames)]
    elif turns:
        regions = []
        for turn in sorted(turns, key=lambda t: (t['start'], t['end'])):
            if regions and turn['start'] < regions[-1]['end'] - .15:
                regions[-1]['end'] = max(regions[-1]['end'], turn['end'])
                if regions[-1]['speaker_local_id'] != turn['speaker_local_id']:
                    regions[-1]['speaker_local_id'] = None
            elif (regions and regions[-1]['speaker_local_id'] == turn['speaker_local_id']
                  and turn['start'] <= regions[-1]['end'] + .35):
                regions[-1]['end'] = max(regions[-1]['end'], turn['end'])
            else:
                regions.append(dict(turn))
        for i, region in enumerate(regions):
            left = (regions[i-1]['end'] + region['start']) / 2 if i else 0.
            right = (region['end'] + regions[i+1]['start']) / 2 if i+1 < len(regions) else duration
            start = max(0., min(region['start'], left), region['start'] - .35)
            end = min(duration, max(region['end'], right), region['end'] + .35)
            if i and regions[i-1]['end'] > region['start']:
                start = left
            if i+1 < len(regions) and region['end'] > regions[i+1]['start']:
                end = right
            if end > start:
                supports.append((int(start * RATE), int(end * RATE)))
    inputs, previous_end = [], 0
    for index, (a, b) in enumerate(supports):
        if not previous_end <= a < b <= read_frames:
            raise ValueError('Overlapping or empty ASR crop; no truncation')
        previous_end = b
        duration = (b-a) / RATE
        for core_frame in range(0, b-a, 28 * RATE):
            core = core_frame / RATE
            start, end = max(0., core-1.), min(duration, min(duration, core+28.)+1.)
            inputs.append({'region_index': index, 'read_frames': [a+int(start*RATE), a+int(end*RATE)]})
    if len(inputs) > max_calls:
        raise ValueError('ASR inventory exceeds call limit; no truncation')
    return {'regions': [{'read_frames': list(pair)} for pair in supports],
            'decode_inputs': inputs, 'calls': len(inputs),
            'frames': sum(item['read_frames'][1]-item['read_frames'][0] for item in inputs)}


def _receipt(samples):
    import numpy as np
    data = np.asarray(samples)
    if data.ndim != 1 or not 0 < data.size <= 60 * RATE or not np.isfinite(data).all():
        raise ValueError('Expected bounded finite mono compute input')
    data = np.ascontiguousarray(data, dtype='<f4')
    if not np.isfinite(data).all():
        raise ValueError('Nonfinite float32 compute input')
    return data, {'sample_rate': RATE, 'sample_count': int(data.size),
                  'pcm_sha256': hashlib.sha256(data.tobytes()).hexdigest(), 'encoding': INPUT_ENCODING}


class MeteredStream:
    def __init__(self, raw):
        self.raw, self.input_receipt, self.executed = raw, None, False

    def accept_waveform(self, sample_rate, waveform):
        if sample_rate != RATE or self.input_receipt is not None:
            raise ValueError('Expected one 16 kHz waveform per compute stream')
        data, receipt = _receipt(waveform)
        self.raw.accept_waveform(sample_rate, data)
        self.input_receipt = receipt

    def input_finished(self):
        return self.raw.input_finished()

    @property
    def result(self):
        return self.raw.result


class MeteredEngine:
    def __init__(self, raw, kind, reserve, budget):
        self.raw, self.kind, self.reserve, self.budget = raw, kind, reserve, budget

    def create_stream(self):
        return MeteredStream(self.raw.create_stream())

    def is_ready(self, stream):
        return self.raw.is_ready(stream.raw)

    def _invoke(self, method, stream):
        if stream.input_receipt is None or stream.executed:
            raise ValueError('Missing compute input or repeated stream execution')
        stream.executed = True
        ticket = self.reserve(self.kind, stream.input_receipt['sample_count'], stream.input_receipt)
        self.budget.update(ticket, execution_started=True)
        try:
            result = getattr(self.raw, method)(stream.raw)
            if self.kind == 'embedding':
                import numpy as np
                vector = np.ascontiguousarray(result, dtype='<f4')
                if vector.ndim != 1 or not 0 < vector.size <= 4096 or not np.isfinite(vector).all():
                    raise ValueError('Unbounded or invalid native ERes2Net vector')
                self.budget.update(ticket, output={
                    'encoding': 'native IEEE754 float32 little-endian output, before analyzer normalization',
                    'dimension': int(vector.size), 'sha256': hashlib.sha256(vector.tobytes()).hexdigest(),
                    'float32_values': vector.tolist()})
            self.budget.complete(ticket)
            return result
        except BaseException as exc:
            self.budget.update(ticket, status='failed', failure={'type': type(exc).__name__, 'message': str(exc)[:1000]})
            raise

    def decode_stream(self, stream):
        return self._invoke('decode_stream', stream)

    def compute(self, stream):
        return self._invoke('compute', stream)

    @property
    def dim(self):
        return self.raw.dim


def make_cpu_metered_analyzer(config, budget, guard, *, raw_failure_sink):
    """Admitted child entry point; the parent owns process and durable limits."""
    if (config.get('asr_backend') != 'sensevoice' or config.get('diarization_backend') != 'nemotron'
        or config.get('device') != 'cpu' or type(config.get('cpu_threads')) is not int
        or config['cpu_threads'] != 2 or config.get('language') != 'auto'
        or config.get('overlap_backend') != 'none'
        or config.get('chinese_script') not in ('as_is', 'simplified', 'traditional')
        or config.get('speaker_support_enabled', False)):
        raise ValueError('Expected SenseVoice/native CPU2, overlap none, no identity bank')

    def load(kind, create, records=None):
        guard.check()
        ticket = budget.reserve(kind)
        if records is not None:
            records.append(ticket)
        budget.update(ticket, execution_started=True)
        try:
            result = create()
            budget.complete(ticket)
            return result
        except BaseException as exc:
            budget.update(ticket, status='failed', failure={'type': type(exc).__name__, 'message': str(exc)[:1000]})
            raise

    from .inference.analyzer import AudioAnalyzer

    class MeteredAnalyzer(AudioAnalyzer):
        def analyze(self, wav_path, diarization=None, analysis_options=None):
            import numpy as np
            from .events.detector import window_starts
            guard.check()
            if getattr(self, 'closed', False):
                raise RuntimeError('AudioAnalyzer is closed')
            if not isinstance(diarization, dict) or not isinstance(diarization.get('turns'), list):
                raise ValueError('Explicit native diarization required')
            with wave.open(str(wav_path), 'rb') as source:
                self._read_frames = source.getnframes()
                if (source.getframerate() != RATE or source.getnchannels() != 1 or source.getsampwidth() != 2
                    or source.getcomptype() != 'NONE' or not 0 < self._read_frames <= 34 * RATE):
                    raise ValueError('Expected bounded 34s 16 kHz mono PCM16 read')
                pcm = source.readframes(self._read_frames)
            if len(pcm) != 2 * self._read_frames:
                raise ValueError('Truncated analyzer PCM16 read')
            samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.
            turns = diarization['turns']
            self._asr_plan = plan_asr_inputs(turns, self._read_frames)
            if len({turn['speaker_local_id'] for turn in turns}) > 8:
                raise ValueError('Native local speaker capacity exceeded')
            self._request_counts, self._request_records = {}, []
            self._asr_expected, self._asr_tickets, self._event_expected = [], [], []
            for item in self._asr_plan['decode_inputs']:
                a, b = item['read_frames']
                self._asr_expected.append({'read_frames': [a, b], 'input': _receipt(samples[a:b])[1]})
            starts = window_starts(self._read_frames)
            for a in starts:
                b = min(self._read_frames, a+5*RATE)
                data = samples[a:b]
                if float(np.max(np.abs(data))) <= float(self.config.get('event_silence_peak', 1e-5)):
                    continue
                padding = 5*RATE-len(data)
                self._event_expected.append({'input': _receipt(np.pad(data, (0, padding)))[1],
                    'real_read_support_frames': [a, b], 'inference_padding_frames': padding,
                    'padding_is_source_owned_audio': False})
            # All ASR reservations precede any engine load; no partial refund.
            for expected in self._asr_expected:
                guard.check()
                ticket = budget.reserve('sensevoice', expected['input']['sample_count'],
                    context={'scope': 'complete analyzer ASR inventory', **copy.deepcopy(expected)})
                self._asr_tickets.append(ticket)
                self._request_records.append(ticket)
            result = super().analyze(wav_path, diarization=diarization, analysis_options=analysis_options)
            count = self._request_counts.get('sensevoice', (0, 0))[0]
            event_count = self._request_counts.get('zipformer', (0, 0))[0]
            result['metadata']['cpu_meter_receipt'] = {
                'policy': POLICY, 'asr_input_plan': copy.deepcopy(self._asr_plan),
                'asr_executed_count': count, 'asr_plan_complete': count == len(self._asr_expected),
                'event_executed_count': event_count, 'event_plan_complete': event_count == len(self._event_expected),
                'zipformer_window_ceiling': len(starts), 'embedding_audio_ceiling_frames': self._read_frames}
            if count != len(self._asr_expected):
                raw_failure_sink({'kind': 'asr_inventory_mismatch', 'raw_analyzer_result': result,
                                  'model_call_records': copy.deepcopy(self._request_records)})
                raise ValueError('Completed SenseVoice inventory differs from planned inputs')
            if event_count != len(self._event_expected):
                raw_failure_sink({'kind': 'event_inventory_mismatch', 'raw_analyzer_result': result,
                                  'model_call_records': copy.deepcopy(self._request_records)})
                raise ValueError('Completed event inventory differs from planned inputs')
            return result

        def _reserve(self, kind, frames, input_receipt):
            guard.check()
            calls, consumed = self._request_counts.get(kind, (0, 0))
            fields = {'input': copy.deepcopy(input_receipt)}
            if kind == 'sensevoice':
                if calls >= len(self._asr_expected) or input_receipt != self._asr_expected[calls]['input']:
                    raise ValueError('SenseVoice input differs from exact plan')
                expected = self._asr_expected[calls]
                fields['selection'] = {'read_frames': expected['read_frames'],
                    'coordinate_contract': 'integer frames in logical analyzer read; not CTC timing'}
                ticket = self._asr_tickets[calls]
            elif kind == 'embedding':
                selection = self._embedding_selection
                support = selection['support']
                if (calls >= 8 or frames > 20*RATE or consumed+frames > self._read_frames
                    or support['pooled_pcm_sha256'] != input_receipt['pcm_sha256']
                    or support['pooled_sample_count'] != frames):
                    raise ValueError('ERes2Net input differs from bounded clean selection')
                fields['selection'] = copy.deepcopy(selection)
                ticket = budget.reserve(kind, frames)
                self._request_records.append(ticket)
            elif kind == 'zipformer':
                if calls >= len(self._event_expected) or input_receipt != self._event_expected[calls]['input']:
                    raise ValueError('Event input differs from real/padded window plan')
                fields.update(copy.deepcopy(self._event_expected[calls]))
                ticket = budget.reserve(kind, frames)
                self._request_records.append(ticket)
            else:
                raise ValueError('Unapproved CPU model kind')
            budget.update(ticket, **fields)
            self._request_counts[kind] = calls+1, consumed+frames
            return ticket

        def _transcribe_sensevoice(self, wav):
            if self._sensevoice is None:
                import sherpa_onnx as s
                original = load('sensevoice_load', lambda: s.OfflineRecognizer.from_sense_voice(
                    model=str(self.paths['asr']), tokens=str(self.paths['tokens']), num_threads=self.threads,
                    language=self.config['language'], use_itn=True, provider='cpu'), self._request_records)
                self._sensevoice = MeteredEngine(original, 'sensevoice', self._reserve, budget)
            return super()._transcribe_sensevoice(wav)

        def _transcribe_turns(self, samples, turns):
            guard.reserve_component('transports', 2*len(samples) + 44*len(self._asr_plan['regions']))
            return super()._transcribe_turns(samples, turns)

        def _embed_turns(self, samples, turns):
            import sherpa_onnx as s
            from . import speaker_evidence
            factory, select = s.SpeakerEmbeddingExtractor, speaker_evidence.clean_speaker_audio
            with Path(speaker_evidence.__file__).open('rb') as source:
                implementation_hash = hashlib.file_digest(source, 'sha256').hexdigest()

            def capture_selection(audio, native_turns, local_id, *args, **kwargs):
                clean, evidence = select(audio, native_turns, local_id, *args, **kwargs)
                support = evidence.get('pooled_support')
                if (not support or not support.get('complete') or support.get('sample_rate') != RATE
                    or support.get('analyzer_frame_count') != self._read_frames):
                    raise ValueError('Incomplete or mismatched speaker support')
                self._embedding_selection = {'speaker_local_id': local_id, 'support': copy.deepcopy(support),
                    'selection_implementation_sha256': implementation_hash,
                    'coordinate_contract': 'logical read PCM16 to float32; not upload coordinates'}
                return clean, evidence

            speaker_evidence.clean_speaker_audio = capture_selection
            s.SpeakerEmbeddingExtractor = lambda *a, **kw: MeteredEngine(
                load('embedding_load', lambda: factory(*a, **kw), self._request_records), 'embedding', self._reserve, budget)
            try:
                return super()._embed_turns(samples, turns)
            finally:
                s.SpeakerEmbeddingExtractor = factory
                speaker_evidence.clean_speaker_audio = select

        def _events(self, samples):
            import sherpa_onnx as s
            from .events.detector import detect_events
            engine = load('zipformer_load', lambda: s.AudioTagging(s.AudioTaggingConfig(
                model=s.AudioTaggingModelConfig(zipformer=s.OfflineZipformerAudioTaggingModelConfig(model=str(self.paths['events'])),
                    num_threads=max(1, min(2, self.threads)), provider='cpu'), labels=str(self.paths['labels']), top_k=527)),
                self._request_records)
            result = detect_events(samples, self.paths['events'], self.paths['labels'], self.threads,
                                 self.config, tagger=MeteredEngine(engine, 'zipformer', self._reserve, budget))
            windows = [item for item in result[1] if item.get('model_inference_executed')]
            if (len(windows) != self._request_counts.get('zipformer', (0, 0))[0]
                or len(windows) != len(self._event_expected)
                or any([round(w['start']*RATE), round(w['end']*RATE)] != e['real_read_support_frames']
                       for w, e in zip(windows, self._event_expected))):
                raw_failure_sink({'kind': 'event_inventory_mismatch', 'raw_event_result': result,
                                  'model_call_records': copy.deepcopy(self._request_records)})
                raise ValueError('Event compute inventory differs from raw window evidence')
            return result

    return load('analyzer_init', lambda: MeteredAnalyzer(config))
