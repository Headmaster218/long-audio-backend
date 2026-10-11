"""Pure untimed support alternatives. No I/O, dispatch, ranking or identity inference."""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
import re

SAMPLE_RATE = 16_000
MAX_SUPPORT_FRAMES = 320_000
MAX_FRAME = (1 << 53) - 1
MAX_TEXT_BYTES = 65_536
MAX_RAW_BYTES = 131_072
MAX_ALTERNATIVE_BYTES = 196_608
MAX_SUPPORT_BYTES = 4_096
MAX_BUNDLE_BYTES = 262_144
MAX_ALTERNATIVES = 8
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class ContractError(ValueError):
    """Evidence is malformed or exceeds this contract's bounded representation."""


def _hash(value: object) -> None:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ContractError("expected lowercase SHA256")


def _integer(value: object, maximum: int = MAX_FRAME) -> None:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ContractError("expected bounded nonnegative integer")


def _text(value: object) -> None:
    if type(value) is not str or len(value) > MAX_TEXT_BYTES:
        raise ContractError("text character limit or type")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ContractError("text is not valid Unicode") from exc
    if size > MAX_TEXT_BYTES:
        raise ContractError("text UTF-8 byte limit")


def _canonical(value: object, maximum: int) -> bytes:
    # Called only after strict field types and per-field bounds have been checked.
    result = json.dumps(value, ensure_ascii=False, allow_nan=False,
                        sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(result) > maximum:
        raise ContractError("serialized byte limit")
    return result


def _exact(value: object, names: set[str]) -> dict:
    if type(value) is not dict or len(value) != len(names) or set(value) != names:
        raise ContractError("unexpected or missing fields")
    return value


@dataclass(frozen=True, slots=True)
class SourceSupport:
    """Caller-supplied immutable native/source evidence; hashes are not live attestation."""
    source_generation: str
    plan_sha256: str
    native_runtime_sha256: str
    core_sha256: str
    source_frames: int
    core_start_frame: int
    core_end_frame: int
    start_frame: int
    end_frame: int
    pcm16le_sha256: str
    float32le_sha256: str
    sample_rate: int = SAMPLE_RATE

    def __post_init__(self) -> None:
        for name in ("source_generation", "plan_sha256", "native_runtime_sha256",
                     "core_sha256", "pcm16le_sha256", "float32le_sha256"):
            _hash(getattr(self, name))
        for name in ("source_frames", "core_start_frame", "core_end_frame",
                     "start_frame", "end_frame", "sample_rate"):
            _integer(getattr(self, name))
        if self.sample_rate != SAMPLE_RATE:
            raise ContractError("sample rate must be 16000")
        if not (0 <= self.core_start_frame <= self.start_frame < self.end_frame
                <= self.core_end_frame <= self.source_frames):
            raise ContractError("support must be inside its native core and source")
        if self.end_frame - self.start_frame > MAX_SUPPORT_FRAMES:
            raise ContractError("support exceeds 20 seconds")
        self.canonical_bytes()

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_dict(), MAX_SUPPORT_BYTES)

    @property
    def support_id(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> SourceSupport:
        return cls(**_exact(value, {f.name for f in fields(cls)}))


@dataclass(frozen=True, slots=True)
class Alternative:
    """Untimed text plus original raw evidence, with no provider winner semantics."""
    support: SourceSupport
    provider: str
    model_sha256: str
    runtime_sha256: str
    config_sha256: str
    text: str
    outcome: str
    native_status: int | None
    eos: bool | None
    truncated: bool | None
    raw: bytes

    def __post_init__(self) -> None:
        if type(self.support) is not SourceSupport:
            raise ContractError("validated support required")
        if type(self.provider) is not str or self.provider not in ("qwen", "moss"):
            raise ContractError("unsupported provider")
        for name in ("model_sha256", "runtime_sha256", "config_sha256"):
            _hash(getattr(self, name))
        _text(self.text)
        if type(self.outcome) is not str or self.outcome not in ("returned", "failed", "unknown"):
            raise ContractError("unsupported outcome")
        if self.native_status is not None and (type(self.native_status) is not int
                or not -(1 << 31) <= self.native_status < (1 << 31)):
            raise ContractError("native status must be int32 or null")
        for value in (self.eos, self.truncated):
            if value is not None and type(value) is not bool:
                raise ContractError("EOS and truncation must be bool or null")
        if self.outcome == "unknown" and self.native_status is not None:
            raise ContractError("unknown outcome cannot assert a native status")
        if type(self.raw) is not bytes or len(self.raw) > MAX_RAW_BYTES:
            raise ContractError("raw bytes limit or type")
        self.canonical_bytes()

    @property
    def complete_content(self) -> bool:
        return (self.outcome == "returned" and type(self.native_status) is int
                and self.native_status == 0 and self.eos is True and self.truncated is False
                and bool(self.text.strip()) and bool(self.raw))

    @property
    def alternative_key(self) -> tuple[str, str, str, str]:
        return (self.provider, self.model_sha256, self.runtime_sha256, self.config_sha256)

    def to_dict(self) -> dict:
        return {
            "schema": "untimed-native-support-alternative-v1",
            "support_id": self.support.support_id,
            "pcm16le_sha256": self.support.pcm16le_sha256,
            "float32le_sha256": self.support.float32le_sha256,
            "provider": self.provider, "model_sha256": self.model_sha256,
            "runtime_sha256": self.runtime_sha256, "config_sha256": self.config_sha256,
            "text": self.text, "outcome": self.outcome, "native_status": self.native_status,
            "eos": self.eos, "truncated": self.truncated,
            "raw_bytes": len(self.raw), "raw_sha256": hashlib.sha256(self.raw).hexdigest(),
            "complete_content": self.complete_content,
            "timing_scope": "input-support-only", "word_times": None,
            "global_speaker_id": None, "speaker_identity_valid": False,
            "profile_update_allowed": False,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_dict(), MAX_ALTERNATIVE_BYTES)

    @classmethod
    def from_dict(cls, value: object, *, support: SourceSupport, raw: bytes) -> Alternative:
        """Validate an exact projection against expected support and retained raw bytes."""
        names = {"schema", "support_id", "pcm16le_sha256", "float32le_sha256", "provider",
                 "model_sha256", "runtime_sha256", "config_sha256", "text", "outcome",
                 "native_status", "eos", "truncated", "raw_bytes", "raw_sha256",
                 "complete_content", "timing_scope", "word_times", "global_speaker_id",
                 "speaker_identity_valid", "profile_update_allowed"}
        data = _exact(value, names)
        if type(support) is not SourceSupport:
            raise ContractError("validated support required")
        obj = cls(support, **{name: data[name] for name in (
            "provider", "model_sha256", "runtime_sha256", "config_sha256", "text",
            "outcome", "native_status", "eos", "truncated")}, raw=raw)
        # Canonical equality rejects bool/int substitutions as well as unknown timing/identity claims.
        # Avoid serializing arbitrary attacker-controlled values before bounding each remaining field.
        for name in ("schema", "support_id", "pcm16le_sha256", "float32le_sha256", "raw_sha256",
                     "timing_scope"):
            if type(data[name]) is not str or len(data[name]) > 128:
                raise ContractError("invalid projection scalar")
        _integer(data["raw_bytes"], MAX_RAW_BYTES)
        for name in ("complete_content", "speaker_identity_valid", "profile_update_allowed"):
            if type(data[name]) is not bool:
                raise ContractError("invalid projection boolean")
        if data["word_times"] is not None or data["global_speaker_id"] is not None:
            raise ContractError("untimed alternatives cannot assert timing or global identity")
        if _canonical(data, MAX_ALTERNATIVE_BYTES) != obj.canonical_bytes():
            raise ContractError("projection, material, status or raw evidence mismatch")
        return obj


def bundle(support: SourceSupport, alternatives: tuple[Alternative, ...] | list[Alternative]) -> bytes:
    """Bounded canonical single-support bundle; caller order is preserved, never ranked."""
    if type(support) is not SourceSupport or type(alternatives) not in (tuple, list):
        raise ContractError("validated support and finite alternatives required")
    if len(alternatives) > MAX_ALTERNATIVES:
        raise ContractError("alternative count limit")
    keys: set[tuple[str, str, str, str]] = set()
    encoded = []
    size = len(support.canonical_bytes()) + 256
    for alternative in alternatives:
        if type(alternative) is not Alternative or alternative.support != support:
            raise ContractError("mixed or invalid support")
        if alternative.alternative_key in keys:
            raise ContractError("duplicate provider/profile alternative")
        keys.add(alternative.alternative_key)
        item = alternative.canonical_bytes()
        size += len(item) + 1
        if size > MAX_BUNDLE_BYTES:
            raise ContractError("bundle byte limit")
        encoded.append(item)
    # Per-item sizes were checked before the final aggregate allocation.
    prefix = b'{"alternatives":['
    result = prefix + b','.join(encoded) + b'],"schema":"untimed-native-support-bundle-v1","support":' + support.canonical_bytes() + b'}'
    if len(result) > MAX_BUNDLE_BYTES:
        raise ContractError("bundle byte limit")
    return result
