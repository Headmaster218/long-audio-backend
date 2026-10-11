"""Closed offline model identities; no imports of models or runtime I/O.

These constants identify allowed artifacts. A matching caller-supplied receipt
is not live attestation: the runner still hashes files and checks the loaded
interpreter, wrapper, native library, model variant and CPU device.
"""
from dataclasses import dataclass

from .fragment_runtime_contracts import digest

OPTIONAL_PROFILE = 'qwen3-asr-1.7b-q5-k-m-cpu-v1'
_CONFIG = (('backend', 'cpu'), ('n_threads', 2), ('kv_type', 'f16'), ('n_ctx', 2048),
           ('language', None), ('task', 'transcribe'), ('timestamps', 'none'),
           ('context_frames', 0), ('generation_cap', 256), ('no_history', True))


@dataclass(frozen=True)
class ModelProfile:
    name: str
    model_sha256: str
    model_bytes: int
    variant: str
    model_id: str | None = None
    model_revision: str | None = None
    license: str | None = None
    package_version: str = '0.2.3'
    native_commit: str = '63a44d9239d610b3908e8a66b384924cd4a77217'

    def config(self):
        return dict(_CONFIG)

    def payload(self):
        return {'schema': 'qwen-offline-model-profile-v1', 'name': self.name,
                'model_sha256': self.model_sha256, 'model_bytes': self.model_bytes,
                'variant': self.variant, 'arch': 'qwen3_asr', 'backend': 'cpu',
                'package_version': self.package_version, 'native_commit': self.native_commit,
                'model_id': self.model_id, 'model_revision': self.model_revision,
                'license': self.license, 'config': self.config()}

    @property
    def sha256(self):
        return digest(self.payload())


DEFAULT_PROFILE = ModelProfile('qwen3-asr-0.6b-q5-k-m-cpu-v1',
    '062a7bcb18675c2fe5ffd0e8b354eac6a1ceada9b2af9ef7168746ae16b54358',
    645356192, 'qwen3-asr-0.6b')
_QWEN17 = ModelProfile(OPTIONAL_PROFILE,
    '034c557fe92ff8fcd9a9c041cbdaad347be0a86a58d3a348f63cf3f0180879d0',
    1517290464, 'qwen3-asr-1.7b', 'handy-computer/Qwen3-ASR-1.7B-gguf',
    '3555bd238a8572bbace3ebf60d23b036dc0a5dbe', 'apache-2.0')


def optional_profile(name):
    """No aliases, implicit filename detection, or caller-defined profiles."""
    if type(name) is not str or name != OPTIONAL_PROFILE:
        raise ValueError('Unknown explicit offline Qwen model profile')
    return _QWEN17


def admit_optional_runtime(runtime, name):
    """Strict identity check, followed separately by the runner's full hashes."""
    profile = optional_profile(name)
    if type(runtime) is not dict or type(runtime.get('model')) is not dict:
        raise ValueError('Explicit profile requires an exact runtime receipt')
    expected = {'package_version': profile.package_version, 'native_commit': profile.native_commit,
                'model_id': profile.model_id, 'model_revision': profile.model_revision,
                'license': profile.license}
    if any(type(runtime.get(k)) is not str or runtime[k] != v for k, v in expected.items()):
        raise ValueError('Runtime provenance differs from selected model profile')
    model = runtime['model']
    if (type(model.get('sha256')) is not str or model['sha256'] != profile.model_sha256 or
        type(model.get('bytes')) is not int or model['bytes'] != profile.model_bytes):
        raise ValueError('Model identity differs from selected model profile')
    return profile
