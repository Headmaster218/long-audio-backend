# Unexecuted portable template; generate configuration placeholders before use.
"""Passive wrappers for actual installed symbols; never supplies provider output."""
from __future__ import annotations
import ctypes
import functools
import hashlib
import inspect
import json
from pathlib import Path
import sys
import time
from common import Admission, FRAMES, MIB, require, bounded_read, sha


class Instrumentation:
    def __init__(self, spec, deadlines, journal, role):
        self.spec, self.deadlines, self.journal, self.role = spec, deadlines, journal, role
        self.out = Path(spec['output_dir'])
        self.symbols = []
        self.streams = {}
        self.streams_created = 0
        self.stream_classes = set()
        self.clean_receipts = []
        self.native_frames = 0
        self.native_blocks = []
        self.native_hash = hashlib.sha256()
        self.lifecycle = Admission({'analyzer_constructor': 1}, deadlines.check, journal.emit)
        self.sdk = Admission({'event_compute': 4, 'embedding_compute': 8, 'sensevoice_decode': 32,
                              'event_constructor': 1, 'embedding_constructor': 1,
                              'sensevoice_constructor': 1}, self.check, journal.emit)
        self.abi = Admission({'native_model_create': 1, 'native_stream_open': 1,
                              'native_push': 12, 'native_finish': 1,
                              'native_segments': 4}, self.check, journal.emit,
                             ['native_model_create', 'native_stream_open'] + ['native_push']*12 +
                             ['native_finish'] + ['native_segments']*4)

    def check(self):
        self.deadlines.check()
        data = bounded_read(self.out / f'{self.role}-active.json', 4096, self.deadlines.check)
        value = json.loads(data)
        require(value['role'] == self.role and value['spec_sha256'] == self.spec['_sha256'],
                'child_dispatch_identity')
        end = value['deadline']
        maximum = 120 if self.role == 'native' else 300
        require(type(end) is float and 0 < end <= self.deadlines.work and
                end <= value['entered_at'] + maximum, 'child_dispatch_deadline')
        self.deadlines.check(end)

    def bind(self, owner, name, replacement):
        original = getattr(owner, name)
        module_name = getattr(original, '__module__', None) or getattr(owner, '__module__', None)
        module = sys.modules.get(module_name)
        origin = getattr(module, '__file__', None)
        require(origin is not None, 'provider_symbol_origin_missing')
        if origin is not None:
            path = Path(origin).resolve()
            require(Path(origin).is_absolute() and Path(origin) == path, 'provider_symbol_noncanonical_origin')
            source_modules = {
                'audio_backend.inference.native_diarization': 'audio_backend/inference/native_diarization.py',
                'audio_backend.inference.analyzer': 'audio_backend/inference/analyzer.py',
                'audio_backend.speaker_evidence': 'audio_backend/speaker_evidence.py',
            }
            if module_name in source_modules:
                expected = Path(self.spec['source_root']) / source_modules[module_name]
                require(path == expected and type(self.spec['source_files']) is list and
                        str(path) in self.spec['source_files'], 'provider_source_module_path')
                installed = self.spec['installed_files'].get(str(path))
                rows = self.spec['hash_inventory']
                require(type(rows) is list and len(rows) <= 32768, 'provider_source_inventory_cap')
                matches = [row for row in rows if row.get('path') == str(path)]
                require(type(installed) is dict and set(installed) == {'bytes','sha256'} and
                        len(matches) == 1 and type(installed['bytes']) is int and
                        0 <= installed['bytes'] <= MIB and
                        all(matches[0].get(key) == value for key,value in installed.items()),
                        'provider_source_pin_agreement')
                raw = bounded_read(path, MIB, self.deadlines.check)
                require(len(raw) == installed['bytes'] and sha(raw) == installed['sha256'],
                        'provider_source_current_hash')
            else:
                require(path.is_relative_to(Path(self.spec['site_packages'])), 'provider_symbol_not_installed')
            require(str(path) in self.spec['installed_files'], 'provider_symbol_unpinned')
        symbol = {'owner': getattr(owner, '__name__', type(owner).__name__), 'name': name,
                  'original_module': getattr(original, '__module__', None), 'origin': origin}
        # Any immutable/unsupported extension type fails before its first provider entry.
        setattr(owner, name, replacement)
        require(getattr(owner, name) is replacement or isinstance(inspect.getattr_static(owner, name),
                (classmethod, staticmethod)), 'symbol_binding_failed')
        self.symbols.append(symbol)
        self.journal.emit('bound_original_symbol', **symbol)
        return original

    def sample_receipt(self, audio):
        require(getattr(audio, 'ndim', None) == 1 and str(audio.dtype) == 'float32', 'sdk_sample_type')
        require(0 <= len(audio) <= FRAMES, 'sdk_sample_frame_cap')
        require(audio.dtype.byteorder in ('=', '<', '|') and sys.byteorder == 'little', 'float32_endian')
        raw = audio.tobytes(order='C')
        return {'frames': len(audio), 'float32le_sha256': sha(raw)}

    def attach_stream(self, stream, kind):
        require(self.streams_created < 44, 'stream_observation_cap')
        self.streams_created += 1
        require(id(stream) not in self.streams, 'reused_observed_stream')
        self.streams[id(stream)] = {'original': stream, 'kind': kind, 'receipt': None}
        cls = type(stream)
        if cls not in self.stream_classes:
            original = cls.accept_waveform
            @functools.wraps(original)
            def accept(obj, *args, **kwargs):
                item = self.streams.get(id(obj))
                require(item is not None and item['original'] is obj, 'unregistered_stream')
                require(item['receipt'] is None, 'second_waveform_submission')
                self.check()
                rate = kwargs.get('sample_rate', args[0] if args else None)
                audio = kwargs.get('waveform', args[1] if len(args) > 1 else None)
                require(rate == 16000 and audio is not None, 'sdk_waveform_arguments')
                receipt = self.sample_receipt(audio)
                if item['kind'] == 'event_compute':
                    require(receipt['frames'] == 80000, 'event_window_shape')
                elif item['kind'] == 'sensevoice_decode':
                    require(0 < receipt['frames'] < 28*16000, 'sensevoice_region_shape')
                else:
                    require(32000 <= receipt['frames'] <= FRAMES, 'embedding_clean_shape')
                    matches = [r for r in self.clean_receipts if r['samples'] == receipt]
                    require(len(matches) >= 1, 'embedding_missing_clean_source_receipt')
                self.journal.emit('sdk_waveform_entered', kind=item['kind'], **receipt)
                try:
                    result = original(obj, *args, **kwargs)
                except BaseException:
                    self.streams.pop(id(obj), None)
                    raise
                self.journal.emit('sdk_waveform_returned', kind=item['kind'], **receipt)
                self.check()
                item['receipt'] = receipt
                return result
            self.bind(cls, 'accept_waveform', accept)
            self.stream_classes.add(cls)
        return stream

    def install_sdk(self):
        import sherpa_onnx as sdk
        import audio_backend.speaker_evidence as evidence
        from audio_backend.inference.analyzer import AudioAnalyzer

        original_analyzer_init = AudioAnalyzer.__init__
        def analyzer_init(obj, *args, **kwargs):
            return self.lifecycle.invoke('analyzer_constructor', original_analyzer_init, obj, *args, **kwargs)
        self.bind(AudioAnalyzer, '__init__', analyzer_init)

        original_clean = evidence.clean_speaker_audio
        @functools.wraps(original_clean)
        def clean(*args, **kwargs):
            self.check()
            require(len(self.clean_receipts) < 8, 'clean_speaker_receipt_cap')
            result = original_clean(*args, **kwargs)
            audio, proof = result
            receipt = {'speaker_local_id': args[2] if len(args) > 2 else kwargs['local_id'],
                       'samples': self.sample_receipt(audio), 'source_evidence': proof}
            require(len(json.dumps(receipt).encode()) <= 65536, 'clean_evidence_byte_cap')
            self.clean_receipts.append(receipt)
            self.journal.emit('clean_source_samples', **receipt)
            self.check()
            return result
        self.bind(evidence, 'clean_speaker_audio', clean)

        def install_class(cls, operation, constructor):
            original_init = cls.__init__
            @functools.wraps(original_init)
            def init(obj, *args, **kwargs):
                return self.sdk.invoke(constructor, original_init, obj, *args, **kwargs)
            self.bind(cls, '__init__', init)
            original_stream = cls.create_stream
            @functools.wraps(original_stream)
            def create(obj, *args, **kwargs):
                self.check()
                require(self.streams_created < 44, 'stream_constructor_count_cap')
                stream = original_stream(obj, *args, **kwargs)
                self.check()
                return self.attach_stream(stream, operation)
            self.bind(cls, 'create_stream', create)
            original_compute = cls.compute
            @functools.wraps(original_compute)
            def compute(obj, stream, *args, **kwargs):
                def admission():
                    item = self.streams.get(id(stream))
                    require(item and item['original'] is stream and item['kind'] == operation and
                            item['receipt'] is not None, 'compute_without_waveform_receipt')
                    self.journal.emit('sdk_samples', operation=operation, **item['receipt'])
                try:
                    return self.sdk.invoke(operation, original_compute, obj, stream, *args,
                                           before_entry=admission, **kwargs)
                finally:
                    self.streams.pop(id(stream), None)
            self.bind(cls, 'compute', compute)

        install_class(sdk.AudioTagging, 'event_compute', 'event_constructor')
        install_class(sdk.SpeakerEmbeddingExtractor, 'embedding_compute', 'embedding_constructor')
        original_ready = sdk.SpeakerEmbeddingExtractor.is_ready
        def ready(obj, stream, *args, **kwargs):
            result = original_ready(obj, stream, *args, **kwargs)
            if not result:self.streams.pop(id(stream), None)
            return result
        self.bind(sdk.SpeakerEmbeddingExtractor, 'is_ready', ready)
        original_factory = sdk.OfflineRecognizer.from_sense_voice
        def factory(cls, *args, **kwargs):
            return self.sdk.invoke('sensevoice_constructor', original_factory, *args, **kwargs)
        self.bind(sdk.OfflineRecognizer, 'from_sense_voice', classmethod(factory))
        original_stream = sdk.OfflineRecognizer.create_stream
        def create(obj, *args, **kwargs):
            self.check()
            require(self.streams_created < 44, 'stream_constructor_count_cap')
            value = original_stream(obj, *args, **kwargs)
            self.check()
            return self.attach_stream(value, 'sensevoice_decode')
        self.bind(sdk.OfflineRecognizer, 'create_stream', create)
        original_decode = sdk.OfflineRecognizer.decode_stream
        def decode(obj, stream, *args, **kwargs):
            def admission():
                item = self.streams.get(id(stream))
                require(item and item['original'] is stream and item['kind'] == 'sensevoice_decode' and
                        item['receipt'] is not None, 'decode_without_waveform_receipt')
                self.journal.emit('sdk_samples', operation='sensevoice_decode', **item['receipt'])
            try:
                return self.sdk.invoke('sensevoice_decode', original_decode, obj, stream, *args,
                                       before_entry=admission, **kwargs)
            finally:
                self.streams.pop(id(stream), None)
        self.bind(sdk.OfflineRecognizer, 'decode_stream', decode)
        for name in ('_events', '_embed_turns', '_transcribe_sensevoice'):
            original = getattr(AudioAnalyzer, name)
            def wrap(original, name):
                @functools.wraps(original)
                def run(obj, *args, **kwargs):
                    self.check()
                    self.journal.emit('stage_entered', stage=name)
                    try:
                        result = original(obj, *args, **kwargs)
                    except BaseException as exc:
                        self.journal.emit('stage_exception', stage=name, exception=type(exc).__name__)
                        raise
                    self.journal.emit('stage_returned', stage=name)
                    self.check()
                    return result
                return run
            self.bind(AudioAnalyzer, name, wrap(original, name))
        # Candidate dispatch and alternate ASR are fail-closed even if future config drifts.
        def forbidden(*args, **kwargs):
            raise RuntimeError('Unapproved alternate inference dispatch')
        self.bind(AudioAnalyzer, '_recover_missing_speech', forbidden)
        self.bind(AudioAnalyzer, '_diarize', forbidden)

    def install_native(self):
        from audio_backend.inference.native_diarization import _CAPI
        original_init = _CAPI.__init__
        def init(obj, *args, **kwargs):
            self.check()
            require(len(args) == 1 and not kwargs and str(Path(args[0]).resolve()) ==
                    str(Path(self.spec['settings']['native_diarization_library']).resolve()), 'native_library_argument_pin')
            original_init(obj, *args, **kwargs)
            self.check()
            names = {'nemo_speech_diar_create': 'native_model_create',
                     'nemo_speech_diar_stream_open': 'native_stream_open',
                     'nemo_speech_diar_stream_push_f32': 'native_push',
                     'nemo_speech_diar_stream_finish': 'native_finish',
                     'nemo_speech_diar_segments': 'native_segments'}
            for symbol, operation in names.items():
                original = getattr(obj.lib, symbol)
                def wrap(original, operation, symbol):
                    def call(*a, **kw):
                        def admission():
                            if operation == 'native_push':
                                entered = self.abi.counts[operation]['entered']
                                expected = (32000,32000,32000,32000,32000,8000)[entered % 6]
                                require(len(a) == 4 and a[2] == expected and a[3] == 16000,
                                        'native_abi_block_shape')
                                raw = ctypes.string_at(a[1], expected * 4)
                                self.native_frames += expected
                                self.native_blocks.append(expected)
                                self.native_hash.update(raw)
                                self.journal.emit('native_abi_samples', frames=expected, float32le_sha256=sha(raw))
                            elif operation == 'native_finish':
                                require(self.native_frames == FRAMES and self.native_hash.hexdigest() == self.spec['input_pins']['source']['float32_sha256'],
                                        'native_submitted_whole_input_pin')
                        return self.abi.invoke(operation, original, *a, before_entry=admission, **kw)
                    return call
                setattr(obj.lib, symbol, wrap(original, operation, symbol))
                self.journal.emit('bound_native_abi_symbol', symbol=symbol,
                                  library=self.spec['settings']['native_diarization_library'])
        self.bind(_CAPI, '__init__', init)

    def summary(self):
        return {'sdk': self.sdk.counts, 'abi': self.abi.counts, 'lifecycle': self.lifecycle.counts,
                'native_frames': self.native_frames, 'native_push_frames':self.native_blocks,
                'native_float32_sha256': self.native_hash.hexdigest() if self.native_frames else None,
                'symbols': self.symbols, 'streams_created': self.streams_created,
                'unreleased_observed_streams': len(self.streams),
                'hidden_native_model_forward_counts': 'unknown_not_exposed'}
