"""Fail-closed identities for the actual speaker embedding implementation.

The one audited build binds installed bytes to their *reported* vendor revision
and inspected source/model metadata. This is practical local provenance, not a
reproducible-build or adversarial-process attestation. Unknown platforms, builds,
models and effective settings are unsupported. Decoder/PCM lineage belongs to
speaker_support and is deliberately absent from this comparability identity.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform

SCHEMA_VERSION = 1
MAX_VECTORS = 64
MAX_DIMENSION = 2048
MAX_DESCRIPTOR_BYTES = 16384
OUTPUT_NORMALIZATION = 'numpy-float32-asarray-linalg-norm-divide-v1'
MODEL_VERSION = 'eres2net-base-zh-cn-3dspeaker-16k-v1'


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


# Exact-source audit 2026-10-09. Source URLs and Apache-2.0 notices are preserved
# in docs/EMBEDDING_SPACE_PROVENANCE.md. These are
# resolved values, not guesses from an arbitrary installed version's defaults.
_AUDITED_BINDINGS = {}

_AUDITED_BINDINGS['02763d940b59563aeeb7e70cc1b0b12542b6d09fa1f0c7be76f33faedb293be0'] = {'proof': {'kind': 'audited-binary-and-self-reported-source-v1',
           'source_commit': '11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf',
           'source_base_url': 'https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/',
           'source_files': {'sherpa-onnx/csrc/features.h': '98d7cced1049d2dcaca085d8b309e7296d8b6f811db8c8f0762907d4fd31d66e',
                            'sherpa-onnx/csrc/features.cc': '1d2f19982d5e9c410a7101027a0251ef63ca7d50413d91edab029d546c9cf2e8',
                            'sherpa-onnx/csrc/speaker-embedding-extractor-general-impl.h': '585a32319d1bc29763000c3c18eb3e97413dbb5d7965a9a485caed7a0dd9ee22',
                            'sherpa-onnx/csrc/speaker-embedding-extractor-model.cc': 'eae4f613882731c8e027543df4d40cf58c719a0bd0ef963a9ada4dc1fa7cc120',
                            'sherpa-onnx/csrc/speaker-embedding-extractor-impl.cc': '0a98272899c8310743dea4281e1011a0dcf360d26aebe43dd146f4c594cb27bc',
                            'sherpa-onnx/csrc/online-stream.cc': 'c2b7d33398d1a1125a97fd2d62ea2eaf137dfa7b492ff0b3d9ac38f6322f0dee',
                            'sherpa-onnx/csrc/speaker-embedding-extractor-model-meta-data.h': '1309c1476492a7c6f5e3aad45ebee65978087b0bc567f4861b814c6e49fd2aa1',
                            'cmake/kaldi-native-fbank.cmake': '619f0046f568e85790f4180899e666053f246f5c0fc36c951421441f3e992e38',
                            'LICENSE': 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
                            'sherpa-onnx/csrc/session.cc': '2ace7fcbc910ab41804ab93869c0eeceac8b0d416d6dbe76a4417c2b007a9e52'},
           'fbank_archive_url': 'https://github.com/csukuangfj/kaldi-native-fbank/archive/refs/tags/v1.22.3.tar.gz',
           'fbank_archive_sha256': '9176cc66fc7ce1edf85cf355b06e320c57db6297df74277f575183468893cf61',
           'fbank_source_files': {'kaldi-native-fbank/csrc/feature-fbank.h': '4be90549f3df404c71cab527fa9ef7fcc3ec14883c33235dfddde234be36a8ce',
                                  'kaldi-native-fbank/csrc/feature-fbank.cc': '6c9edabf3b2479f31392aec9490688f59262e6f86c85cc648cfa8987387814c1',
                                  'kaldi-native-fbank/csrc/feature-window.h': '8a181e9111ed9c31b3d2c3fcc14cb52ef106294e0edb78ed69788300c3992a59',
                                  'kaldi-native-fbank/csrc/feature-window.cc': 'cac43e0def16acb3032491ea65d4cdfc9e2523ef18d8202524a58b43b7fe67d2',
                                  'kaldi-native-fbank/csrc/mel-computations.h': '3a85adb8f7e51598f6f46a0d2b6bdef4c0836efbc925dd0a5e1983598f3f4341',
                                  'LICENSE': 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
                                  'kaldi-native-fbank/csrc/online-feature.h': '9cc145593df3fa9725cdd8a2860236ce95cbd43624c275195bb15912d642792b',
                                  'kaldi-native-fbank/csrc/online-feature.cc': 'd74de946a0ccdeec7b5d44bf31d7038d72d5c36b7aa328f842ac89ebfa86ccd1'}},
 'runtime': {'name': 'sherpa-onnx',
             'version': '1.13.8',
             'git_sha1': '11afbd00',
             'onnxruntime_version': '1.28.2',
             'platform': 'Linux',
             'machine': 'x86_64',
             'artifacts': {'sherpa_extension': 'eb9c3545b310ecd97ba0a2ff53fe39f8d8fb9efaea2dea0ca333ae9050bf2cd2',
                           'onnxruntime': '4b3607aebd1784b26b6f9b20e4bd974c7ab8287043e4d095cb7d2cb40b5e566e'}},
 'model': {'sha256': '1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b',
           'version': 'eres2net-base-zh-cn-3dspeaker-16k-v1',
           'dimension': 512,
           'metadata': {'framework': '3d-speaker',
                        'language': 'Chinese',
                        'url': 'https://www.modelscope.cn/models/iic/speech_eres2net_base_sv_zh-cn_3dspeaker_16k/summary',
                        'comment': 'This model is from '
                                   'iic/speech_eres2net_base_sv_zh-cn_3dspeaker_16k',
                        'sample_rate': '16000',
                        'output_dim': '512',
                        'normalize_samples': '1',
                        'feature_normalize_type': 'global-mean'}},
 'frontend': {'implementation': 'sherpa-general-speaker-kaldi-native-fbank',
              'sampling_rate': 16000,
              'feature_dim': 80,
              'low_freq': 20.0,
              'high_freq': -400.0,
              'high_freq_hz': 7600.0,
              'dither': 0.0,
              'normalize_samples': True,
              'waveform_scale': 1.0,
              'snip_edges': False,
              'frame_length_ms': 25.0,
              'frame_shift_ms': 10.0,
              'window_type': 'povey',
              'preemph_coeff': 0.9700000286102295,
              'remove_dc_offset': True,
              'round_to_power_of_two': True,
              'fft_size': 512,
              'is_librosa': False,
              'use_energy': False,
              'raw_energy': True,
              'energy_floor': 0.0,
              'htk_compat': False,
              'htk_mode': False,
              'use_log_fbank': True,
              'use_power': True,
              'log_floor': 1.1920928955078125e-07,
              'vtln_warp': 1.0,
              'feature_normalize_type': 'global-mean',
              'mean_axis': 'per-bin-over-all-frames',
              'input_finished': True,
              'resampling': 'none-input-and-model-16000'},
 'output': {'dtype': 'float32',
            'normalization': 'numpy-float32-asarray-linalg-norm-divide-v1',
            'numpy': {'version': '2.5.3',
                      'artifacts': {'numpy._core._multiarray_umath': 'ceec5ba13ab88318632307fdd2df74f317fa293ce77be559c4298ddd1f4306b0',
                                    'numpy.linalg._umath_linalg': '19d024507f73b42889baeb00f9da0004e33a42ed7a78fd77662fd6236e930d78',
                                    'numpy.linalg._linalg': 'fb51e084cbb05b72a571a088299222055f3f176792ea9ea66186327a31ec787f',
                                    'numpy.libs/libgfortran-83c28eba-468e71e5.so.5.0.0': 'a6a368328d02a5d8a149746f13f6268f8cc9192df5c18777b25c3c9b5ef52160',
                                    'numpy.libs/libquadmath-2284e583-a9307bba.so.0.0.0': '6bc3069003caec1f075be3c51ae8355332f081fa9465910c4b5724e7fe7d8ad4',
                                    'numpy.libs/libscipy_openblas64_-f48b354e.so': '432e1c1739731468ba305771af96773939e43016643e2d630f58cb45b6670e96'}}}}


def validate_space(descriptor):
    """Return its canonical ID only for an exact audited effective descriptor.

    Callers must additionally bind this server-produced record to the raw vector
    and support manifest. A client-supplied descriptor never authorizes evidence.
    """
    try:
        if (not isinstance(descriptor, dict) or set(descriptor) != {
                'schema_version', 'status', 'binding_id', 'proof', 'runtime',
                'model', 'frontend', 'extractor', 'output'} or
                type(descriptor['schema_version']) is not int or
                descriptor['schema_version'] != SCHEMA_VERSION or descriptor['status'] != 'proven'):
            raise ValueError('Embedding space proof unavailable')
        binding = _AUDITED_BINDINGS.get(descriptor['binding_id'])
        if binding is None or _digest(binding) != descriptor['binding_id']:
            raise ValueError('Unknown embedding runtime/model proof')
        # Canonical bytes avoid Python's True == 1 and 1 == 1.0 ambiguities.
        for key in ('proof', 'runtime', 'model', 'frontend', 'output'):
            if _canonical(descriptor[key]) != _canonical(binding[key]):
                raise ValueError('Embedding space differs from audited proof: ' + key)
        extractor = descriptor['extractor']
        if (not isinstance(extractor, dict) or set(extractor) != {'provider', 'num_threads'} or
                extractor['provider'] != 'cpu' or not _integer(extractor['num_threads'], 1, 4)):
            raise ValueError('Unsupported extractor configuration')
        if not _integer(descriptor['model']['dimension'], 8, MAX_DIMENSION):
            raise ValueError('Invalid embedding dimension')
        encoded = _canonical(descriptor)
        if len(encoded) > MAX_DESCRIPTOR_BYTES:
            raise ValueError('Oversized embedding space')
        return hashlib.sha256(encoded).hexdigest()
    except (KeyError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError('Malformed embedding space') from exc


canonical_embedding_space_id = validate_space


def unavailable(reason):
    return {'schema_version': SCHEMA_VERSION, 'status': 'unavailable', 'reason': reason}


def _descriptor(binding_id, config):
    result = {'schema_version': SCHEMA_VERSION, 'status': 'proven', 'binding_id': binding_id,
              **copy.deepcopy(_AUDITED_BINDINGS[binding_id]),
              'extractor': {'provider': config.provider, 'num_threads': config.num_threads}}
    validate_space(result)
    return result


def bounded_raw_vectors(embeddings, dimension):
    """Copy original analyzer lists exactly; never normalize/round replacements."""
    if not isinstance(embeddings, dict) or not _integer(dimension, 8, MAX_DIMENSION):
        return {}
    result = {}
    for key in sorted(k for k in embeddings if isinstance(k, str))[:MAX_VECTORS]:
        vector = embeddings[key]
        if (not isinstance(key, str) or not 0 < len(key) <= 200 or
                not isinstance(vector, list) or len(vector) != dimension or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
            continue
        if not any(v != 0 for v in vector):
            continue
        result[key] = list(vector)
    return result


def _signature(path):
    stat = Path(path).stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class _ArtifactCache:
    """Bounded stat-keyed file digests; never retain model/library byte payloads."""
    def __init__(self):
        self.entries = OrderedDict()

    def digest(self, path):
        path = Path(path).resolve()
        signature = _signature(path)
        if not 0 < signature[2] <= 512 * 1024 * 1024:
            raise ValueError('Embedding artifact size unsupported')
        cached = self.entries.get(path)
        if cached is None or cached[0] != signature:
            hashed = hashlib.sha256()
            with path.open('rb') as source:
                stat = os.fstat(source.fileno())
                if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != signature:
                    raise ValueError('Embedding artifact changed before hashing')
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    hashed.update(block)
            if _signature(path) != signature:
                raise ValueError('Embedding artifact changed during hashing')
            cached = (signature, hashed.hexdigest())
            self.entries[path] = cached
            while len(self.entries) > 32:
                self.entries.popitem(last=False)
        self.entries.move_to_end(path)
        return cached[1], (path, signature)


def _loaded_ort_path(extension):
    """Linux audit only: bind the loaded ORT object, not an adjacent unused file."""
    expected = extension.parent / 'libonnxruntime.so'
    found = set()
    with Path('/proc/self/maps').open() as maps:
        for line in maps:
            parts = line.rstrip().split(maxsplit=5)
            if len(parts) != 6 or 'libonnxruntime.so' not in parts[5]:
                continue
            path = Path(parts[5])
            if path.name != 'libonnxruntime.so' or not path.is_file():
                raise ValueError('Unrecognized loaded ONNX runtime')
            stat = path.stat()
            if stat.st_ino != int(parts[4]):
                raise ValueError('Loaded ONNX runtime was replaced')
            found.add(path.resolve())
    if found != {expected.resolve()}:
        raise ValueError('Loaded ONNX runtime identity ambiguous')
    return expected


class SherpaSpaceAttestor:
    """Snapshot before extractor creation; verify the same files after compute.

    No model is loaded here. Only bounded streaming file hashes and already
    loaded runtime/configuration inspection are used. Hashes are cached for the
    analyzer's lifetime; subsequent windows only stat unchanged artifacts.
    """
    def __init__(self):
        self._hashes = _ArtifactCache()
        self._runtime_paths = None

    def _runtime_snapshot(self, sherpa):
        import numpy as np
        native = importlib.import_module('sherpa_onnx.lib._sherpa_onnx')
        if (sherpa.SpeakerEmbeddingExtractor is not native.SpeakerEmbeddingExtractor or
                sherpa.SpeakerEmbeddingExtractorConfig is not native.SpeakerEmbeddingExtractorConfig or
                sherpa.FeatureExtractorConfig is not native.FeatureExtractorConfig):
            raise ValueError('Unrecognized extractor implementation')
        if self._runtime_paths is None:
            extension = Path(native.__file__).resolve()
            paths = {'sherpa_extension': extension, 'onnxruntime': _loaded_ort_path(extension)}
            for name in ('numpy._core._multiarray_umath', 'numpy.linalg._umath_linalg', 'numpy.linalg._linalg'):
                paths[name] = Path(importlib.import_module(name).__file__).resolve()
            numpy_libs = Path(np.__file__).resolve().parent.parent / 'numpy.libs'
            for path in sorted(numpy_libs.iterdir()):
                if path.is_file():
                    paths['numpy.libs/' + path.name] = path
            if len(paths) > 16:
                raise ValueError('Unrecognized runtime artifact inventory')
            self._runtime_paths = paths
        signatures, hashes = [], {}
        for key, path in self._runtime_paths.items():
            hashed, signature = self._hashes.digest(path)
            signatures.append(signature)
            hashes[key] = hashed
        runtime = {'name': 'sherpa-onnx', 'version': native.version, 'git_sha1': native.git_sha1,
                   'onnxruntime_version': native.onnxruntime_version,
                   'platform': platform.system(), 'machine': platform.machine(),
                   'artifacts': {k: hashes[k] for k in ('sherpa_extension', 'onnxruntime')}}
        if sherpa.__version__ != native.version:
            raise ValueError('Sherpa wrapper and native versions disagree')
        numpy_runtime = {'version': np.__version__,
                         'artifacts': {k: v for k, v in hashes.items() if k.startswith('numpy')}}
        defaults = sherpa.FeatureExtractorConfig()
        frontend = {key: getattr(defaults, key) for key in (
            'sampling_rate', 'feature_dim', 'low_freq', 'high_freq', 'dither', 'normalize_samples', 'snip_edges')}
        return runtime, numpy_runtime, frontend, signatures

    def begin(self, sherpa, config, expected_model_sha256):
        """Return a private snapshot, or raise; caller keeps legacy inference alive."""
        if platform.system() != 'Linux' or platform.machine() != 'x86_64':
            raise ValueError('Embedding runtime platform unverified')
        if type(config) is not sherpa.SpeakerEmbeddingExtractorConfig:
            raise ValueError('Unrecognized extractor configuration')
        runtime, numpy_runtime, defaults, signatures = self._runtime_snapshot(sherpa)
        model_hash, signature = self._hashes.digest(config.model)
        signatures.append(signature)
        if model_hash != expected_model_sha256:
            raise ValueError('Embedding model changed since analyzer initialization')
        for binding_id, binding in _AUDITED_BINDINGS.items():
            if (runtime != binding['runtime'] or numpy_runtime != binding['output']['numpy'] or
                    model_hash != binding['model']['sha256']):
                continue
            # Source audit establishes that the general implementation inherits
            # these defaults; model metadata then sets sample rate and scaling.
            if any(_canonical(value) != _canonical(binding['frontend'][key])
                   for key, value in defaults.items()):
                raise ValueError('Effective frontend defaults differ from audited runtime')
            descriptor = _descriptor(binding_id, config)
            return {'descriptor': descriptor, 'signatures': signatures,
                    'extractor_type': sherpa.SpeakerEmbeddingExtractor}
        raise ValueError('Embedding runtime/model proof unavailable')

    def finish(self, snapshot, extractor):
        if type(extractor) is not snapshot['extractor_type']:
            raise ValueError('Unrecognized instantiated extractor')
        if extractor.dim != snapshot['descriptor']['model']['dimension']:
            raise ValueError('Extractor dimension differs from audited model metadata')
        for path, signature in snapshot['signatures']:
            if _signature(path) != signature:
                raise ValueError('Embedding artifact changed during extraction')
        descriptor = copy.deepcopy(snapshot['descriptor'])
        return descriptor, validate_space(descriptor)
