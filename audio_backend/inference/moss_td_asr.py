"""Audited, hash-pinned MOSS-TD CPU/BF16 runtime and fail-closed raw parser.

Only model-local labels are emitted. No identities, enrollments, target masks,
subtitle normalization, model downloads, or profile updates are used here.
"""
import ast
import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import types
import uuid

from ..moss_td_assets import verify_model_directory

MAX_NEW_TOKENS = 512
MAX_SUPPORT_SECONDS = 20.0
MAX_SPEAKERS = 8
_TIMESTAMP = r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)'
_CANONICAL = re.compile(r'\[(' + _TIMESTAMP + r')\]\s*\[(S[0-9]+)\](.*?)\[(' + _TIMESTAMP + r')\]', re.S)
_LABEL = re.compile(r'S[0-9]{1,15}\Z')


def unresolved(reason_code, *, raw_text='', raw_generated_ids=None, schema_errors=None, metadata=None):
    return {'status': 'unresolved', 'reason_code': reason_code, 'raw_text': raw_text,
            'raw_generated_ids': raw_generated_ids if raw_generated_ids is not None else [],
            'phrases': [], 'diagnostic_phrases': [], 'schema_errors': schema_errors or [reason_code],
            'metadata': metadata or {}}


def parse_generation(raw_text, raw_generated_ids, duration, parse_transcript):
    """Validate the official raw parser's exact output without repairing timings.

    The upstream parser tolerantly skips malformed text. An independent envelope
    check detects that loss, and comparison to its parsed rows catches ambiguous
    delimiters. Neither global chronological order nor disjointness is required.
    """
    if not isinstance(raw_text, str) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid generation input')
    errors = []
    if len(raw_generated_ids) >= MAX_NEW_TOKENS:
        errors.append('generation_token_limit_reached')
    matches = list(_CANONICAL.finditer(raw_text))
    if _CANONICAL.sub('', raw_text).strip():
        errors.append('noncanonical_or_unparsed_text')
    try:
        segments = list(parse_transcript(raw_text, strip_text=False, skip_empty=False))
    except Exception:
        segments = []
        errors.append('transcript_parser_error')
    if raw_text.strip() and not segments:
        errors.append('no_parseable_speaker_segments')
    canonical = [(float(m[1]), float(m[4]), m[2], m[3]) for m in matches]
    observed = [(s.start, s.end, s.speaker, s.text) for s in segments]
    if canonical != observed:
        errors.append('parser_envelope_mismatch')
    label_slots = {}
    diagnostics = []
    for segment in segments:
        speaker = segment.speaker
        if not isinstance(speaker, str) or not _LABEL.fullmatch(speaker):
            errors.append('invalid_speaker_label')
            continue
        if speaker not in label_slots:
            label_slots[speaker] = len(label_slots)
        start, end = segment.start, segment.end
        if (isinstance(start, bool) or isinstance(end, bool) or
                not isinstance(start, (float, int)) or not isinstance(end, (float, int)) or
                not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration):
            errors.append('out_of_range_or_invalid_timestamps')
            continue
        if not isinstance(segment.text, str) or not segment.text.strip():
            errors.append('empty_phrase_text')
            continue
        slot = label_slots[speaker]
        diagnostics.append({'speaker_local_id': speaker, 'source_slot': slot if slot < MAX_SPEAKERS else None,
                            'start': start, 'end': end, 'text': segment.text.strip(), 'raw_text': segment.text,
                            'ownership_anchor': start, 'unit_kind': 'native_phrase', 'confidence': None,
                            'timestamp_quality': 'model_generated_unverified'})
    if len(label_slots) > MAX_SPEAKERS:
        errors.append('speaker_capacity_exceeded')
    if not diagnostics:
        errors.append('no_text')
    errors = list(dict.fromkeys(errors))
    return {'status': 'unresolved' if errors else 'ok', 'reason_code': errors[0] if errors else None,
            'raw_text': raw_text, 'raw_generated_ids': list(raw_generated_ids),
            'phrases': [] if errors else diagnostics, 'diagnostic_phrases': diagnostics if errors else [],
            'schema_errors': errors,
            'metadata': {'generated_tokens': len(raw_generated_ids),
                         'token_limit_reached': len(raw_generated_ids) >= MAX_NEW_TOKENS,
                         'speaker_label_slots': {label: slot for label, slot in label_slots.items() if slot < MAX_SPEAKERS},
                         'model_local_speaker_count': len(label_slots),
                         'timestamp_quality': 'model_generated_unverified'}}


def _read_prompt(path):
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == 'DEFAULT_PROMPT' for target in node.targets)]
    if len(values) != 1 or not isinstance(values[0], str) or not values[0]:
        raise ValueError('Pinned helper has no single literal DEFAULT_PROMPT')
    return values[0]


class _VerifiedSourceLoader(importlib.machinery.SourceFileLoader):
    """Compile checked source bytes, never unchecked on-disk .pyc caches."""
    def __init__(self, name, path, expected_sha256):
        super().__init__(name, str(path))
        self.expected_sha256 = expected_sha256

    def get_code(self, fullname):
        source = Path(self.path).read_bytes()
        if hashlib.sha256(source).hexdigest() != self.expected_sha256:
            raise ValueError(f'MOSS-TD SHA256 mismatch before import: {Path(self.path).name}')
        return self.source_to_code(source, self.path)


def _import_verified(name, path, sha256):
    loader = _VerifiedSourceLoader(name, path, sha256)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


class MossTDRecognizer:
    def __init__(self, config):
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                          TOKENIZERS_PARALLELISM='false')
        directory = Path(config['moss_td_model_dir']).expanduser().resolve()
        # Every required code, tokenizer, configuration and weight file is hashed
        # before any custom code import. Readiness intentionally checks only size.
        self.manifest = verify_model_directory(directory)
        import torch
        import torchaudio
        import transformers
        import safetensors
        expected = {'torch': '2.8.0+cpu', 'torchaudio': '2.8.0+cpu',
                    'transformers': '5.6.0', 'safetensors': '0.6.2'}
        for name, module in [('torch', torch), ('torchaudio', torchaudio),
                             ('transformers', transformers), ('safetensors', safetensors)]:
            if module.__version__ != expected[name]:
                raise ImportError(f'Use the isolated pinned MOSS-TD runtime: {name}=={expected[name]}')
        self.torch = torch
        torch.set_num_threads(2)
        torch.set_num_interop_threads(2)
        torch.manual_seed(0)
        self.max_support_seconds = min(float(config.get('moss_td_max_support_seconds', 20)), MAX_SUPPORT_SECONDS)
        if not math.isfinite(self.max_support_seconds) or self.max_support_seconds <= 0:
            raise ValueError('Invalid MOSS-TD support limit')
        hashes = {row['name']: row['sha256'] for row in self.manifest['runtime_files']}
        package = '_audio_backend_pinned_moss_td_' + uuid.uuid4().hex
        namespace = types.ModuleType(package)
        namespace.__path__ = [str(directory)]
        sys.modules[package] = namespace
        def load(filename):
            return _import_verified(package + '.' + filename[:-3], directory / filename, hashes[filename])
        Config = load('configuration_moss_transcribe_diarize.py').MossTranscribeDiarizeConfig
        Model = load('modeling_moss_transcribe_diarize.py').MossTranscribeDiarizeForConditionalGeneration
        Processor = load('processing_moss_transcribe_diarize.py').MossTranscribeDiarizeProcessor
        self.parse_transcript = load('transcript_parser.py').parse_transcript
        # The helper's URL/PyAV/subtitle code is deliberately never imported.
        self.prompt = _read_prompt(directory / 'inference_utils.py')
        transformers.utils.logging.set_verbosity_error()
        began = time.monotonic()
        cfg = Config.from_pretrained(directory, local_files_only=True)
        self.model, loading = Model.from_pretrained(
            directory, config=cfg, local_files_only=True, use_safetensors=True,
            dtype=torch.bfloat16, attn_implementation='sdpa', low_cpu_mem_usage=True, output_loading_info=True)
        self.model.eval()
        if any(p.dtype != torch.bfloat16 or p.device.type != 'cpu' for p in self.model.parameters()):
            raise RuntimeError('MOSS-TD requires CPU BF16 parameters; no fallback is permitted')
        if any(value != 'sdpa' for value in (self.model.config._attn_implementation,
                self.model.config.text_config._attn_implementation, self.model.config.audio_config._attn_implementation)):
            raise RuntimeError('MOSS-TD requires SDPA in every component; no fallback is permitted')
        tied = self.model.get_input_embeddings().weight.data_ptr() == self.model.get_output_embeddings().weight.data_ptr()
        if not tied:
            raise RuntimeError('Official MOSS-TD tied embedding/head invariant failed')
        if (loading.get('unexpected_keys') or loading.get('mismatched_keys') or loading.get('error_msgs') or
                any(key != 'lm_head.weight' for key in loading.get('missing_keys', []))):
            raise RuntimeError('MOSS-TD checkpoint loading mismatch')
        self.activation_dtypes = {}
        def hook(name):
            def check(module, args, output):
                value = output.last_hidden_state if hasattr(output, 'last_hidden_state') else output
                if isinstance(value, tuple):
                    value = value[0]
                self.activation_dtypes[name] = str(value.dtype)
                if value.dtype != torch.bfloat16 or value.device.type != 'cpu':
                    raise RuntimeError(f'MOSS-TD CPU BF16 activation invariant failed at {name}')
            return check
        for name, module in [('whisper_encoder', self.model.model.whisper_encoder),
                             ('projection', self.model.model.vq_adaptor),
                             ('language_model', self.model.model.language_model), ('lm_head', self.model.lm_head)]:
            module.register_forward_hook(hook(name))
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        extractor = transformers.WhisperFeatureExtractor.from_pretrained(directory, local_files_only=True)
        pcfg = json.loads((directory / 'processor_config.json').read_text())
        self.processor = Processor(feature_extractor=extractor, tokenizer=self.tokenizer,
                                   chat_template=(directory / 'chat_template.jinja').read_text(),
                                   **{key: pcfg[key] for key in ('audio_tokens_per_second', 'audio_merge_size',
                                                               'time_marker_every_seconds', 'enable_time_marker')})
        self.metadata = {'model_revision': self.manifest['revision'], 'source_revision': self.manifest['source_revision'],
                         'model_sha256': hashes['model-00000-of-00001.safetensors'], 'device': 'cpu', 'dtype': 'bfloat16',
                         'cpu_threads': 2, 'attention_implementation': 'sdpa', 'weights_tied': tied,
                         'load_seconds': time.monotonic() - began, 'dependency_versions': expected,
                         'prompt_sha256': hashlib.sha256(self.prompt.encode()).hexdigest(),
                         'source_sha256': hashes, 'profile_update_eligible': False, 'identity_status': 'unresolved'}

    def _read(self, path):
        import numpy as np
        import soundfile as sf
        with sf.SoundFile(path) as stream:
            if (stream.samplerate != 16000 or stream.channels != 1 or
                    not 0 < len(stream) <= self.max_support_seconds * 16000):
                raise ValueError('Expected a bounded nonempty 16 kHz mono support')
            samples = stream.read(dtype='float32')
        if not np.isfinite(samples).all():
            raise ValueError('Nonfinite MOSS-TD input')
        return samples

    def transcribe(self, support):
        import numpy as np
        began = time.monotonic()
        wave = self._read(support['path'])
        with Path(support['path']).open('rb') as stream:
            audio_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
        duration = len(wave) / 16000
        provenance = {**self.metadata, 'audio_sha256': audio_sha256, 'audio_hash_scope': 'encoded_input_file', 'duration_seconds': duration}
        if not np.any(wave != 0):
            return unresolved('silent_input', metadata={**provenance, 'inference_run': False})
        messages = [{'role': 'user', 'content': [{'type': 'audio', 'audio': str(support['path'])},
                                                {'type': 'text', 'text': self.prompt}]}]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.processor(text=text, audio=[wave], max_length=131072, return_tensors='pt')
        prompt_length = int(inputs['attention_mask'][0].sum().item())
        generation = copy.deepcopy(self.model.generation_config)
        generation.max_new_tokens = MAX_NEW_TOKENS
        generation.do_sample = False
        generation.num_beams = 1
        generation.num_return_sequences = 1
        self.activation_dtypes.clear()
        with self.torch.inference_mode():
            output = self.model.generate(**inputs, generation_config=generation)
        generated = output[0][prompt_length:].tolist()
        raw = self.tokenizer.decode(generated, skip_special_tokens=True)
        result = parse_generation(raw, generated, duration, self.parse_transcript)
        result['metadata'].update(provenance)
        result['metadata'].update(duration_seconds=duration, inference_run=True,
                                  seconds=time.monotonic() - began, prompt_token_count=prompt_length,
                                  major_activation_dtypes=dict(self.activation_dtypes),
                                  finish_reason='length' if len(generated) >= MAX_NEW_TOKENS else 'eos')
        return result
